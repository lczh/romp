#!/usr/bin/env python3
"""Progress notes: which thinking blocks are the model's notes to the person (2026-11-06).

One store, shared in-process by the writer (the SDK backend, as each raw assistant frame arrives) and the
readers (the kernel's chat build, its signature and the subagent viewer's key). Loaded by both under the one
module name `romp_narration`, through sys.modules first, so the in-memory memo is one object per process.
"""
from __future__ import annotations
import json
import sys
import threading
from pathlib import Path

# On Claude Opus 5.5 a note the model writes between tool calls (what it found, what it does next, an answer to a message
# sent mid-turn) comes back as a `thinking` block, not a `text` block, once it is longer than a sentence or two. The chat
# drew every thinking block as a collapsed Thought row, so those notes never reached the person. The CLI decodes each
# block's kind from its signature and lists the note blocks on the assistant frame it streams: `narration_block_indexes`,
# ascending zero-based indexes into the frame's message.content, whatever the thinking display, omitted when the frame
# holds none (CLI schema: "server summaries of the prose between tool calls, not the model's own reasoning"). The SDK's
# parser drops frame-level keys and the transcript does not carry the list, so the kernel records it here as each raw
# frame arrives (SdkSession._narration_frame), in a store of its own: one JSON row per frame under
# STATE/narration/<sid>.jsonl, {"u": the frame's uuid (the transcript record's own, verified), "i": the CLI's indexes,
# "k": the same blocks as ordinals among the frame's thinking blocks}. The chat build reads `k`: the live atom drops the
# block types the SDK does not parse, so a raw index could shift there, while every thinking block survives both roads.
# Marks are applied when the chat is built (kernel build_session), never written into the parse or its documents.
NARRATION_DIR = "narration"
_NARR_LOCK = threading.Lock()
_NARR_MEMO = {}                 # (state root, sid) -> {"marks": {uuid: frozenset(ordinals)}, "order": [uuid, ...]}
_NARR_MEMO_MAX = 4096           # sids held; past it the memo is dropped whole and each store reloads on its next read
_NARR_SINK = [None]             # the backend's _log (SdkBackend.__init__): an unreadable store is reported, never a silent miss
NARRATION_PROBLEM_KEY = "narration-marks"


def narration_path(state_dir, sid) -> Path:
    return Path(state_dir) / NARRATION_DIR / (str(sid) + ".jsonl")


def narration_from_frame(data):
    """What one raw assistant frame says about progress notes: {"uuid", "idx", "ords", "why"}, or None when it says
    nothing (no thinking block, nothing listed, nothing amiss). `idx` is the CLI's narration_block_indexes kept to those
    that name a thinking block, `ords` the same blocks as ordinals among the frame's thinking blocks. `why` names a frame
    the kernel cannot read, so its thinking stays as Thought rows and the caller reports it (never a guess):
    - a listed index that names no thinking block, or a list that is not one of indexes (the format moved);
    - a main-conversation frame of one thinking block WITH words that lists no index while it says
      `thinking_display: "updates"`: under that display every such block is a progress note by the API's definition,
      so the CLI should have listed it;
    - the same frame carrying neither field: a Claude Code from before the list, or a reshaped frame, where a note and
      a reasoning summary cannot be told apart.
    A frame under `"summarized"` with an unlisted block is a reasoning summary, the ordinary case, and says nothing. A
    subagent's frame carries no `thinking_display` (the CLI adds it to the main conversation's frames only), so only its
    list is read."""
    if not isinstance(data, dict) or data.get("type") != "assistant":
        return None
    m = data.get("message")
    content = m.get("content") if isinstance(m, dict) else None
    if not isinstance(content, list):
        return None
    ordinal = {}                                     # raw index -> ordinal among the frame's thinking blocks
    for j, b in enumerate(content):
        if isinstance(b, dict) and b.get("type") == "thinking":
            ordinal[j] = len(ordinal)
    listed = data.get("narration_block_indexes")
    uuid_ = data.get("uuid") if isinstance(data.get("uuid"), str) and data.get("uuid") else None
    why = None
    idx = []
    if listed is not None:
        good = isinstance(listed, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in listed)
        idx = sorted({i for i in listed if i in ordinal}) if good else []
        if not good or len(idx) != len(set(listed)):
            why = "a frame's narration_block_indexes names something other than its thinking blocks"
        elif idx and not uuid_:
            why = "a frame lists progress notes but carries no uuid to file them under"
    if why is None and not data.get("parent_tool_use_id") and len(content) == 1 and ordinal:
        worded = [j for j in ordinal if j not in idx and str(content[j].get("thinking") or "").strip()]
        if worded:
            disp = data.get("thinking_display")
            if disp == "updates":
                why = "a thinking block with words under the updates display is not listed as a progress note"
            elif "thinking_display" not in data:
                why = ("a thinking frame carries neither narration_block_indexes nor thinking_display (a Claude Code "
                       "older than the list, or a changed frame)")
    if not idx and why is None:
        return None
    return {"uuid": uuid_, "idx": idx, "ords": [ordinal[i] for i in idx], "why": why}


def _narr_entry(state_dir, sid):
    """The memo entry for one session's store, read from disk on its first touch (call under _NARR_LOCK). A torn or
    foreign line costs that line; an unreadable file is reported once through the sink and reads as no marks."""
    key = (str(Path(state_dir)), str(sid))
    ent = _NARR_MEMO.get(key)
    if ent is not None:
        return ent
    marks, order = {}, []
    try:
        with open(narration_path(state_dir, sid), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                    u = str(row["u"])
                    ks = frozenset(int(k) for k in row["k"])
                except (ValueError, KeyError, TypeError):
                    continue
                old = marks.get(u)
                new = ks | old if old is not None else ks
                if new != old:
                    marks[u] = new
                    order.append(u)
    except FileNotFoundError:
        pass
    except OSError as e:
        sink = _NARR_SINK[0]
        text = "progress notes: the store for %s cannot be read (%s); its notes show as Thought rows" % (sid, e)
        if sink is not None:
            try:
                sink(text, problem=True, key=(NARRATION_PROBLEM_KEY, "store-read"))
            except Exception:
                pass
        else:
            sys.stderr.write(text + "\n")
    if len(_NARR_MEMO) >= _NARR_MEMO_MAX:
        _NARR_MEMO.clear()
    ent = _NARR_MEMO[key] = {"marks": marks, "order": order}
    return ent


def narration_marks(state_dir, sid):
    """(revision, marks) for one session: marks maps a transcript record's uuid to the ordinals of its thinking blocks the
    CLI listed as progress notes; the revision counts the changes the store has taken, so a caller can ask what moved
    since (narration_marked_since). The mapping is the store's live dict: look up, never iterate."""
    with _NARR_LOCK:
        ent = _narr_entry(state_dir, sid)
        return len(ent["order"]), ent["marks"]


def narration_marked_since(state_dir, sid, rev):
    """The uuids marked after revision `rev` (every marked uuid when `rev` is past the store's, which a memo reload after
    a failed write can cause)."""
    with _NARR_LOCK:
        order = _narr_entry(state_dir, sid)["order"]
        r = rev if isinstance(rev, int) and 0 <= rev <= len(order) else 0
        return list(order[r:])


def record_narration(state_dir, sid, uuid_, idx, ords) -> bool:
    """File one frame's marks. True when they were new: a replayed frame (a host's journal handed over again after a
    kernel restart) changes nothing. The row is written before it is served, under the lock; a failed write keeps the
    mark for this kernel's life and is reported."""
    if not uuid_ or not ords:
        return False
    with _NARR_LOCK:
        ent = _narr_entry(state_dir, sid)
        old = ent["marks"].get(uuid_)
        new = frozenset(ords) | old if old is not None else frozenset(ords)
        if new == old:
            return False
        try:
            p = narration_path(state_dir, sid)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"u": uuid_, "i": list(idx), "k": sorted(ords)}) + "\n")
        except OSError as e:
            sink = _NARR_SINK[0]
            text = "progress notes: a mark for %s was not written (%s); it holds until the kernel restarts" % (sid, e)
            if sink is not None:
                try:
                    sink(text, problem=True, key=(NARRATION_PROBLEM_KEY, "store-write"))
                except Exception:
                    pass
            else:
                sys.stderr.write(text + "\n")
        ent["marks"][uuid_] = new
        ent["order"].append(uuid_)
        return True
