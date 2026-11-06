#!/usr/bin/env python3
"""Progress notes: which thinking blocks are the model's notes to the person (2026-11-06).

One store, shared in-process by the writer (the SDK backend, as each raw assistant frame arrives) and the
readers (the kernel's chat build, its signature, the history pages and the subagent viewer's key). Loaded by
both under the one module name `romp_narration`, through sys.modules first, so the in-memory memo is one object
per process.
"""
from __future__ import annotations
import collections
import json
import os
import re
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
#
# Bounds. The journal rotates like the backend's ledgers: past NARRATION_ROTATE_BYTES it moves to <sid>.jsonl.1 (the older
# predecessor dropped) and the reader reads both, so a session keeps about 60,000 notes' marks (older ones read as Thought
# rows again). romp deletes no session's state, so the store lives as long as the session's other per-session files. In
# memory a session's marks are read only when its chat is built (narration_marks), held in an LRU of _NARR_MEMO_MAX
# sessions, and each set of ordinals is interned (almost every one is {0}, one block per record). The signatures read
# narration_rev, which is a counter in memory and never reads the file.
NARRATION_DIR = "narration"
NARRATION_ROTATE_BYTES = 4 * 1024 * 1024
# The first Claude Code that lists notes on its frames: bisected over the published linux-x64 builds (2026-11-06), 2.1.259
# has no narration_block_indexes and 2.1.260 does. The frame-level thinking_display came later (about 2.1.300), so it is
# not the test of an older CLI: the init frame's claude_code_version is.
NARRATION_MIN_CLI = (2, 1, 260)
_NARR_LOCK = threading.Lock()
_NARR_MEMO = collections.OrderedDict()   # (state root, sid) -> {"marks": {uuid: frozenset}, "log": [(gen, uuid, sub)], "loaded": gen}
_NARR_MEMO_MAX = 4096                    # sessions held; past it the least recently read goes, one at a time
_NARR_REVS = {}                          # (state root, sid) -> [main gen, subagent gen]: never dropped, so never reused
_NARR_GEN = [0]                          # the process's one monotonic counter: every mark filed takes the next value
_NARR_ZERO = frozenset((0,))
_NARR_INTERN = {}                        # the other ordinal sets, one instance each
_NARR_SINK = [None]             # the backend's _log (SdkBackend.__init__): an unreadable store is reported, never a silent miss
NARRATION_PROBLEM_KEY = "narration-marks"
_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def narration_path(state_dir, sid) -> Path:
    return Path(state_dir) / NARRATION_DIR / (str(sid) + ".jsonl")


def cli_lists_notes(version):
    """Whether the Claude Code that reported `version` at init lists notes on its frames: True from NARRATION_MIN_CLI on,
    False before it, None when there is no version to read (no init frame seen, an unparseable string)."""
    m = _VERSION_RE.match(str(version or ""))
    if not m:
        return None
    return tuple(int(x) for x in m.groups()) >= NARRATION_MIN_CLI


def narration_from_frame(data, lists=None):
    """What one raw assistant frame says about progress notes: {"uuid", "idx", "ords", "sub", "why"}, or None when it says
    nothing (no thinking block, nothing listed, nothing amiss). `idx` is the CLI's narration_block_indexes kept to those
    that name a thinking block, `ords` the same blocks as ordinals among the frame's thinking blocks, `sub` whether the
    frame is a subagent's. `lists` is the caller's knowledge of the CLI: True when it lists notes (cli_lists_notes of the
    init's version, or the field already seen in the session), False when its version is older than the list, None when
    unknown. `why` names a frame the kernel cannot read, so its thinking stays as Thought rows and the caller reports it:
    - a listed index that names no thinking block, or a list that is not one of indexes (the format moved);
    - a main-conversation frame of one thinking block WITH words that lists no index while it says
      `thinking_display: "updates"`: under that display every such block is a progress note by the API's definition,
      so the CLI should have listed it;
    - the same frame from a Claude Code older than NARRATION_MIN_CLI (`lists` False), where a note and a reasoning
      summary cannot be told apart.
    Otherwise an unlisted block with words is a reasoning summary, the ordinary case, and says nothing: with `lists`
    unknown the kernel makes no judgement rather than a false one."""
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
    sub = bool(data.get("parent_tool_use_id"))
    why = None
    idx = []
    if listed is not None:
        good = isinstance(listed, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in listed)
        idx = sorted({i for i in listed if i in ordinal}) if good else []
        if not good or len(idx) != len(set(listed)):
            why = "a frame's narration_block_indexes names something other than its thinking blocks"
        elif idx and not uuid_:
            why = "a frame lists progress notes but carries no uuid to file them under"
    if why is None and not sub and len(content) == 1 and ordinal:
        worded = [j for j in ordinal if j not in idx and str(content[j].get("thinking") or "").strip()]
        if worded:
            if data.get("thinking_display") == "updates":
                why = "a thinking block with words under the updates display is not listed as a progress note"
            elif lists is False:
                why = ("this Claude Code is older than %s, the first to list progress notes; update it"
                       % ".".join(str(x) for x in NARRATION_MIN_CLI))
    if not idx and why is None:
        return None
    return {"uuid": uuid_, "idx": idx, "ords": [ordinal[i] for i in idx], "sub": sub, "why": why}


def _intern(ks):
    if ks == _NARR_ZERO:
        return _NARR_ZERO
    return _NARR_INTERN.setdefault(ks, ks)


def _say(text, key):
    sink = _NARR_SINK[0]
    if sink is not None:
        try:
            sink(text, problem=True, key=(NARRATION_PROBLEM_KEY, key))
            return
        except Exception:
            pass
    sys.stderr.write(text + "\n")


def _narr_entry(state_dir, sid):
    """The memo entry for one session's store, read from disk on its first touch and LRU-touched on every one (call under
    _NARR_LOCK). The rotated predecessor is read first. A torn or foreign line costs that line; an unreadable file is
    reported through the sink and reads as no marks."""
    key = (str(Path(state_dir)), str(sid))
    ent = _NARR_MEMO.get(key)
    if ent is not None:
        _NARR_MEMO.move_to_end(key)
        return ent
    marks = {}
    p = narration_path(state_dir, sid)
    for f in (p.with_name(p.name + ".1"), p):
        try:
            with open(f, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                        u = str(row["u"])
                        ks = frozenset(int(k) for k in row["k"])
                    except (ValueError, KeyError, TypeError):
                        continue
                    old = marks.get(u)
                    marks[u] = _intern(ks | old if old is not None else ks)
        except FileNotFoundError:
            pass
        except OSError as e:
            _say("progress notes: the store for %s cannot be read (%s); its notes show as Thought rows" % (sid, e), "store-read")
    while len(_NARR_MEMO) >= _NARR_MEMO_MAX:
        _NARR_MEMO.popitem(last=False)                # the least recently read session; it reloads on its next read
    ent = _NARR_MEMO[key] = {"marks": marks, "log": [], "loaded": _NARR_GEN[0]}
    return ent


def narration_rev(state_dir, sid, sub=False):
    """The session's mark revision: the counter value of the last main-conversation mark (`sub` False) or subagent mark
    (`sub` True) filed in this kernel's life, 0 when none was. Memory only, never the file; a value is never reused (the
    counter is the process's), so a key that holds a revision changes exactly when a mark of that kind is filed."""
    r = _NARR_REVS.get((str(Path(state_dir)), str(sid)))
    return (r[1] if sub else r[0]) if r else 0


def narration_marks(state_dir, sid):
    """The session's marks: a transcript record's uuid -> the ordinals of its thinking blocks the CLI listed as progress
    notes. Read from the store on first use (the chat build of this session, never a signature). The mapping is the
    store's live dict: look up, never iterate."""
    with _NARR_LOCK:
        return _narr_entry(state_dir, sid)["marks"]


def narration_marked_since(state_dir, sid, rev):
    """The uuids of the main-conversation marks filed after revision `rev` (a value narration_rev returned). When the
    session's marks were read from the file after `rev` (a cold memo), which of them came later is not known, and every
    marked uuid is returned: a caller intersects them with what it rendered as thinking, so the answer stays exact."""
    with _NARR_LOCK:
        ent = _narr_entry(state_dir, sid)
        r = rev if isinstance(rev, int) and rev >= 0 else 0
        if r < ent["loaded"]:
            return list(ent["marks"])
        return [u for g, u, s in ent["log"] if g > r and not s]


def record_narration(state_dir, sid, uuid_, idx, ords, sub=False) -> bool:
    """File one frame's marks. True when they were new: a replayed frame (a host's journal handed over again after a
    kernel restart) changes nothing. The row is written before it is served, under the lock, the journal rotated first
    when it is past NARRATION_ROTATE_BYTES; a failed write keeps the mark for this kernel's life and is reported."""
    if not uuid_ or not ords:
        return False
    with _NARR_LOCK:
        ent = _narr_entry(state_dir, sid)
        old = ent["marks"].get(uuid_)
        new = _intern(frozenset(ords) | old if old is not None else frozenset(ords))
        if new == old:
            return False
        try:
            p = narration_path(state_dir, sid)
            p.parent.mkdir(parents=True, exist_ok=True)
            try:
                if p.stat().st_size >= NARRATION_ROTATE_BYTES:
                    os.replace(p, p.with_name(p.name + ".1"))   # the older predecessor, if any, is dropped
            except FileNotFoundError:
                pass
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps({"u": uuid_, "i": list(idx), "k": sorted(ords)}) + "\n")
        except OSError as e:
            _say("progress notes: a mark for %s was not written (%s); it holds until the kernel restarts" % (sid, e), "store-write")
        _NARR_GEN[0] += 1
        g = _NARR_GEN[0]
        ent["marks"][uuid_] = new
        ent["log"].append((g, uuid_, bool(sub)))
        revs = _NARR_REVS.setdefault((str(Path(state_dir)), str(sid)), [0, 0])
        revs[1 if sub else 0] = g
        return True
