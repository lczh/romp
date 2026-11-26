#!/usr/bin/env python3
"""Python's own answers for layer 2 of the Rust spike's port (rust/notes/s2-design.md section 2, "Segmentation";
rust/SPIKE.md step S4.2), for the Rust side to match with zero differences.

It loads the real kernel the way dump_layer1.py does (an empty state root, the bench guards) and calls the real
kernel._segs_seam(turn, store) on every turn of every tree in a dump, with that session's store from snapshot.json.
That is jd.apply_seams(em.segments(kernel._turn_sans_placed_echoes(turn)), store or {}), the segmentation the per-card
code reads. Every segment is written as its dict, keys in insertion order, with `atoms` replaced by the atoms' uuids and
`atomIdx` (each atom's index in the turn's `atoms`) appended. `romp-spike layer2 DUMP` computes the same over the
typed structs and compares.

    python3 tools/rust-spike/dump_layer2.py DUMP_DIR [DUMP_DIR ...]   # writes DUMP_DIR/layer2.json
    python3 tools/rust-spike/dump_layer2.py --edge-cases OUT.json     # the synthetic cases (romp-spike layer2 OUT.json)
    python3 tools/rust-spike/dump_layer2.py --fuzz N [--seed S] [--plant end|bytes] [--bin ROMP_SPIKE] [--out FILE]

Neither world splits a segment (each holds one seam, and no piece's span holds its time), so the edge cases build
the branches the worlds never reach: straddling seams, recursive splits, equal and int-vs-float times, absent and null
seam text and top, the prose floor at 79/80/81 code points of non-ASCII text, placed echoes, triggers of every shape,
float times printed with "%d", and the inputs on which Python raises. A case that raises records
{"raises": <exception name>, "arg": str(e)}, and every case records what apply_seams did (`counts`: pieces examined,
em.split_segment calls, splits). A case named "REFUSE ..." is one the Rust loader must refuse.

`--fuzz N` is the randomized differential check: N random turns and stores (atom kinds, texts with non-ASCII and
Python-only whitespace, int and float times with ties, seams whose keys and times come from the turn's own segments
and their tails, absent and null seam text and top, placed echoes, every trigger shape), answered by the real
kernel._segs_seam, written in the edge-set format and compared by `romp-spike layer2`; any difference fails it. Seeded:
`--seed S` replays. With `--plant NAME` it passes only when romp-spike's planted mistake is caught. Stdlib only.
"""
import argparse
import contextlib
import importlib.util
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
SCHEMA = "romp-spike-layer2/1"
EDGE_SCHEMA = "romp-spike-layer2-edge/1"
OUT_NAME = "layer2.json"


def load_l1():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer1", os.path.join(HERE, "dump_layer1.py"))
    l1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l1)
    return l1


def seg_out(seg, index_of):
    """A segment dict as written: its keys in order, `atoms` as uuids, then `atomIdx`."""
    out = {k: ([a.get("uuid") for a in v] if k == "atoms" else v) for k, v in seg.items()}
    out["atomIdx"] = [index_of[id(a)] for a in seg["atoms"]]
    return out


def raised(e):
    return {"raises": type(e).__name__, "arg": str(e)}


@contextlib.contextmanager
def counting(km):
    """Count what apply_seams does, through the module attributes it calls: jd._seg_key once per piece examined,
    em.split_segment once per piece with a hit seam (the result says whether it split)."""
    em, jd = km.em, km.jd
    if jd.em is not em:
        raise RuntimeError("judge's event_model is not the kernel's: the counters would miss the calls")
    stats = {"pieces": 0, "splitCalls": 0, "splits": 0}
    seg_key, split = jd._seg_key, em.split_segment

    def seg_key_counted(seg_id):
        stats["pieces"] += 1
        return seg_key(seg_id)

    def split_counted(seg, t):
        stats["splitCalls"] += 1
        r = split(seg, t)
        stats["splits"] += r is not None
        return r
    jd._seg_key, em.split_segment = seg_key_counted, split_counted
    try:
        yield stats, seg_key
    finally:
        jd._seg_key, em.split_segment = seg_key, split


def key_listed(seg_key, seg, store):
    """Whether a seam the store holds lists this segment's key (the coverage count keyHits)."""
    seams = [s for s in ((store or {}).get("seams") or []) if isinstance(s, dict) and s.get("segs")]
    return any(seg_key(seg["id"]) in s["segs"] for s in seams)


def segs_answer(km, turn, store, cov, seg_key):
    index_of = {id(a): i for i, a in enumerate(turn.get("atoms") or [])}
    try:
        segs = km._segs_seam(turn, store)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        cov["raises"] += 1
        return raised(e)
    cov["segments"] += len(segs)
    cov["seamTails"] += sum(1 for s in segs if s.get("seam"))
    cov["keyHits"] += sum(1 for s in segs if key_listed(seg_key, s, store))
    return [seg_out(s, index_of) for s in segs]


def new_cov():
    return {"turns": 0, "segments": 0, "seamTails": 0, "keyHits": 0, "raises": 0}


def dump_one(dw, km, out):
    snap, trees = dw.load_dump(out)
    sessions, cov = [], new_cov()
    seams = 0
    with counting(km) as (stats, seg_key):
        for r in snap["alive"]:
            store = (snap["sessions"].get(r["sid"]) or {}).get("store")
            seams += sum(1 for s in ((store or {}).get("seams") or []) if isinstance(s, dict) and s.get("segs"))
            turns = []
            for turn in (trees.get(r["sid"]) or {}).get("turns") or []:
                cov["turns"] += 1
                turns.append(segs_answer(km, turn, store, cov, seg_key))
            sessions.append({"sid": r["sid"], "turns": turns})
    cov.update(stats)
    cov["seams"] = seams
    doc = {"schema": SCHEMA, "world": snap["world"]["name"], "python": platform.python_version(),
           "constants": {"SEAM_PROSE_FLOOR": km.em.SEAM_PROSE_FLOOR}, "coverage": cov, "sessions": sessions}
    path = os.path.join(out, OUT_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return cov, os.path.getsize(path)


# ───────────────────────────── the edge cases ─────────────────────────────
SID = "eeeeeeee-0000-0000-0000-000000000002"
INJECT = "<!-- romp-injected -->"
PREAMBLE = "[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT]"
TOP = SID + ":g1"


def pad(n):
    """Prose of exactly n code points that strip() keeps whole, non-ASCII (ï is 2 UTF-8 bytes, → 3, 😀 4), so a byte
    count reads it longer than it is."""
    base = "Wrapped up: the naïve → index is fixed 😀 "
    s = (base * (n // len(base) + 1))[:n]
    if s[-1].isspace():
        s = s[:-1] + "."
    assert len(s) == n and len(s.strip()) == n and len(s.encode()) > n
    return s


def u(uuid, t, text="please fix it", author="human", **kw):
    a = {"type": "user", "uuid": uuid, "t": t, "session_id": SID, "author": author,
         "message": {"role": "user", "content": [{"type": "text", "text": text}] if text is not None else []}}
    a.update(kw)
    return a


def asst(uuid, t, text=None, tools=0, **kw):
    content = ([{"type": "text", "text": text}] if text is not None else []) + [
        {"type": "tool_use", "id": "tu-%s-%d" % (uuid, i), "name": "Bash", "input": {}} for i in range(tools)]
    a = {"type": "assistant", "uuid": uuid, "t": t, "session_id": SID,
         "message": {"role": "assistant", "content": content, "stop_reason": "tool_use" if tools else "end_turn"}}
    a.update(kw)
    return a


def tool(uuid, t, **kw):
    return asst(uuid, t, tools=1, **kw)


def result(uuid, t, **kw):
    """A tool_result user atom: no author, so not an input."""
    a = {"type": "user", "uuid": uuid, "t": t, "session_id": SID,
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]}}
    a.update(kw)
    return a


def idle(t, end):
    return {"type": "idle", "uuid": None, "t": t, "end": end, "session_id": SID}


def turn(atoms, t=None, end=200, trigger="first", **kw):
    if trigger == "first":
        trigger = {"uuid": atoms[0]["uuid"]} if atoms else None
    tr = {"atoms": atoms, "t": atoms[0]["t"] if t is None and atoms else (t if t is not None else 100),
          "end": end, "ended": True, "id": "edge-turn", "trigger": trigger}
    tr.update(kw)
    return tr


# the standard straddle: a prompt at 100, a reply at 101, real work from 110 on
def work_turn(tail=None, end=200, **kw):
    tail = tail if tail is not None else [tool("a2", 110), asst("a3", 120, "done")]
    return turn([u("u1", 100), asst("a1", 101, "on it")] + tail, end=end, **kw)


def edge_cases(km):
    """(name, kind, turn, store or t, note). Seam keys are taken from the real em.segments, so a seam names the
    segment it is meant to hit."""
    em, jd = km.em, km.jd

    def key(tr, i=0):
        return jd._seg_key(em.segments(tr)[i]["id"])

    def tail_key(tr, t, i=0):
        sp = em.split_segment(em.segments(tr)[i], t)
        return jd._seg_key(sp[1]["id"])

    def seam(t, keys, top=TOP, text="the settled goal", **kw):
        s = {"t": t, "top": top, "text": text, "segs": keys}
        for k in ("t", "top", "text"):
            if kw.pop("no_" + k, False):
                s.pop(k)
        s.update(kw)
        return s

    def st(*seams):
        return {"rompUuid": SID, "seams": list(seams)}

    c = []

    def add(name, tr, store=None, kind="segs", t=None):
        c.append((name, kind, tr, store if kind == "segs" else t))

    # em.segments
    add("no atoms, trigger uuid", turn([], trigger={"uuid": "u-gone"}))
    add("no atoms, trigger null", turn([], trigger=None))
    add("no inputs, trigger among the atoms", turn([tool("a0", 99), asst("a1", 100, "x")], trigger={"uuid": "a1"}))
    add("no inputs, trigger text-less, first atom has text",
        turn([asst("a1", 100, "x"), tool("a2", 101)], trigger={"uuid": "a2"}))
    add("no inputs, trigger null", turn([asst("a1", 100, "x")], trigger=None))
    add("no inputs, trigger an empty dict", turn([asst("a1", 100, "x")], trigger={}))
    add("no inputs, trigger absent", {k: v for k, v in turn([asst("a1", 100, "x")]).items() if k != "trigger"})
    add("no inputs, trigger without uuid", turn([asst("a1", 100, "x")], trigger={"other": 1}))
    add("no inputs, trigger uuid null", turn([asst("a1", 100, "x")], trigger={"uuid": None}))
    add("no inputs, trigger uuid empty", turn([asst("a1", 100, "x")], trigger={"uuid": ""}))
    # `if trigger_uuid:` skips the anchor search for "": the id hashes the first atom's text ("x"), not the text of
    # the atom whose uuid is "" ("y"). A search that ran for "" would find that atom (layer2.rs: the filter on "").
    add("no inputs, trigger uuid empty, an atom with an empty uuid",
        turn([asst("a1", 100, "x"), asst("", 101, "y")], trigger={"uuid": ""}))
    add("no inputs, trigger uuid not among the atoms", turn([asst("a1", 100, "x")], trigger={"uuid": "zz"}))
    add("no inputs, no text, no uuids", turn([dict(tool("a1", 100), uuid=None), idle(101, 105)], trigger=None))
    add("no inputs, text-less first atom without uuid", turn([idle(99, 100), tool("a2", 101)], trigger=None))
    add("leading atoms attach at the turn's time", turn([result("r0", 95), asst("a0", 96, "lead"), u("u1", 100),
                                                         asst("a1", 101, "x")], t=90))
    add("leading atoms, float turn time", turn([asst("a0", 96, "lead"), u("u1", 100)], t=1781100000.9))
    add("inputs of every author", turn([u("u1", 100), u("u2", 102, author="sdk"), u("u3", 104, author="romp"),
                                        u("u4", 106, author={"peer": "s002", "host": "TESTHOST"}),
                                        u("u5", 108, "<task-notification> done", author="system"),
                                        u("u6", 110, author="peer"), result("r7", 112)]))
    add("session_id absent on the first atom", turn([{k: v for k, v in u("u1", 100).items() if k != "session_id"}]))
    add("session_id null on the first atom", turn([u("u1", 100, session_id=None), asst("a1", 101, "x")]))
    add("session_id absent on a later atom", turn([u("u1", 100), {k: v for k, v in u("u2", 105).items()
                                                                   if k != "session_id"}]))
    add("session_id from another session on a later input", turn([u("u1", 100), u("u2", 105, session_id="other")]))
    add("text-less trigger keyed by its uuid", turn([u("u1", 100, None), asst("a1", 101, "x")]))
    add("text-less trigger without uuid, a later uuid", turn([u(None, 100, None), asst("a1", 101, "x")]))
    add("text-less trigger, empty uuid, a later uuid", turn([u("", 100, None), asst("a1", 101, "x")]))
    add("romp-injected trigger keyed by its uuid", turn([u("u1", 100, "go on " + INJECT, author="romp")]))
    add("scheduled preamble trigger keyed by its uuid", turn([u("u1", 100, "  " + PREAMBLE + " report", author="sdk")]))
    add("scheduled origin trigger keyed by its uuid",
        turn([u("u1", 100, "report", author="sdk", origin={"kind": "task-notification",
                                                            "subkind": "scheduled-trigger"})]))
    add("interrupt record trigger keyed by its uuid", turn([u("u1", 100, "[Request interrupted by user]")]))
    add("interrupt record without a uuid falls to the next uuid",
        turn([u(None, 100, "[Request interrupted by user for tool use]"), asst("a1", 101, "x")]))
    add("two identical prompts share a hash, not an id", turn([u("u1", 100, "continue"), u("u2", 150, "continue")]))
    add("bare string content trigger", turn([dict(u("u1", 100), message={"role": "user", "content": "  hi  "})]))
    add("float input times truncate in the id", turn([u("u1", 1781100000.7), u("u2", -5.5), u("u3", -0.5),
                                                      u("u4", -0.0)], end=1781100009.25))
    add("huge float times print exactly", turn([u("u1", 1e20), u("u2", 9.3e18), u("u3", -1e19),
                                                u("u4", 1.7976931348623157e308)], end=1.7976931348623157e308))
    add("placed echo dropped", turn([u("u1", 100), asst("a1", 101, "x"), u("e1", 105, "echo"), asst("a2", 110, "y")],
                                    placedEchoes=["e1"]))
    add("placed echo first: the rompuuid comes from the next atom",
        turn([u("e1", 99, "echo", session_id="echo-sid"), u("u1", 100), asst("a1", 101, "x")], placedEchoes=["e1"]))
    add("placed echoes empty", turn([u("u1", 100), u("e1", 105, "echo")], placedEchoes=[]))
    add("placed echoes null", turn([u("u1", 100), u("e1", 105, "echo")], placedEchoes=None))
    add("placed echoes with null drops uuid-less atoms",
        turn([u("u1", 100), idle(101, 104), asst("a1", 105, "x")], placedEchoes=[None, "nope"]))
    add("placed echoes drop every atom", turn([u("u1", 100)], placedEchoes=["u1"]))
    add("pre false is a plain turn", turn([u("u1", 100)], pre=False))

    # jd.apply_seams and em.split_segment
    w = work_turn()
    k = key(w)
    add("seam straddles: split", w, st(seam(105, [k])))
    add("seam text absent", w, st(seam(105, [k], no_text=True)))
    add("seam text null", w, st(seam(105, [k], text=None)))
    add("seam top absent", w, st(seam(105, [k], no_top=True)))
    add("seam top null, text empty", w, st(seam(105, [k], top=None, text="")))
    add("seam text non-ASCII", w, st(seam(105, [k], text="café → \U0001F600")))
    add("seam at the segment's t: no hit", w, st(seam(100, [k])))
    add("seam at the segment's end: no hit", w, st(seam(200, [k])))
    add("seam past the end: no hit", w, st(seam(250, [k])))
    add("seam lists the full id: no hit", w, st(seam(105, [em.segments(w)[0]["id"]])))
    add("seam lists another key: no hit", w, st(seam(105, [SID + ":deadbeef"])))
    w2 = work_turn([tool("a2", 110), tool("a3", 115), tool("a4", 130)])
    add("atom at the seam time stays in the head", w2, st(seam(110, [key(w2)])))
    add("int atom time, equal float seam time", w2, st(seam(110.0, [key(w2)])))
    add("float atom time, int seam time", work_turn([tool("a2", 110.5), asst("a3", 120, "done")]),
        st(seam(110, [key(work_turn([tool("a2", 110.5), asst("a3", 120, "done")]))])))
    ft = work_turn([tool("a2", 110.7), asst("a3", 120.25, "done")], end=230.5)
    add("float tail time: the tail id truncates, the head ends at it", ft, st(seam(105.5, [key(ft)])))
    big = turn([u("u1", 9007199254740990), tool("a2", 9007199254740993)], end=9007199254741000)
    add("int beyond 2**53 against an equal-looking float", big, st(seam(9007199254740992.0, [key(big)])))
    add("equal times, int first wins", w, st(seam(105, [k], top=SID + ":gA", text="A"),
                                             seam(105.0, [k], top=SID + ":gB", text="B")))
    add("equal times, float first wins", w, st(seam(105.0, [k], top=SID + ":gB", text="B"),
                                               seam(105, [k], top=SID + ":gA", text="A")))
    add("the earlier seam wins over the first listed", w, st(seam(115, [k], top=SID + ":gL", text="late"),
                                                            seam(105, [k], top=SID + ":gE", text="early")))
    add("seam t absent, no hit", w, st(seam(105, [k], no_t=True)))
    neg = turn([u("u1", -10), tool("a2", 5)], end=20)
    add("seam t absent, hit: min raises", neg, st(seam(None, [key(neg)], no_t=True)))
    add("seam t absent hit, behind a good seam: min still raises", neg,
        st(seam(1, [key(neg)]), seam(None, [key(neg)], no_t=True)))
    add("seam segs empty: skipped", w, st(seam(105, [])))
    add("seam segs null: skipped", w, st(seam(105, None)))
    add("seam segs absent: skipped", w, st({"t": 105, "top": TOP, "text": "x"}))
    add("seams null", w, {"rompUuid": SID, "seams": None})
    add("seams absent", w, {"rompUuid": SID})
    add("store null", w, None)
    add("store empty", w, {})
    add("tail of user atoms only: no real work", work_turn([result("r2", 110), u("u9", 120, author="system")]),
        st(seam(105, [key(work_turn([result("r2", 110), u("u9", 120, author="system")]))])))
    for n in (79, 80, 81):
        tw = work_turn([asst("a2", 110, pad(n))])
        add("tail prose %d code points, %d bytes" % (n, len(pad(n).encode())), tw, st(seam(105, [key(tw)])))
    for n in (79, 80):
        tw = work_turn([asst("a2", 110, "x" * n)])
        add("tail prose %d ASCII" % n, tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, "  " + pad(79) + "\x1c ")])
    add("tail prose 79 after strip (83 before)", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, None, message={"role": "assistant", "content": [
        {"type": "text", "text": pad(40)}, {"type": "text", "text": pad(39)}]})])
    add("tail prose 40 + 39 joined by a space is 80", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, None, message={"role": "assistant", "content": " " + pad(80) + " "})])
    add("tail prose 80 in a bare string content", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, pad(80), isApiError=True)])
    add("tail API-error prose 80 still counts", tw, st(seam(105, [key(tw)])))
    tw = work_turn([u("u9", 110, pad(90), author="system")])
    add("tail user prose 90 does not count", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, pad(50)), asst("a3", 111, pad(50))])
    add("tail prose split over two atoms, 50 each", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, None, message={"role": "assistant", "content": [
        "tool_use", {"type": "tool_use"}]})])
    add("tail tool_use block without id or name", tw, st(seam(105, [key(tw)])))
    tw = work_turn([asst("a2", 110, pad(85))])
    add("tail with text: keyed by its first atom's text", tw, st(seam(105, [key(tw)])))
    tw = work_turn([u("m2", 110, "resumed " + INJECT, author="system"), tool("a3", 115)])
    add("tail opened by a machine-written atom: keyed by its uuid", tw, st(seam(105, [key(tw)])))
    tw = work_turn([dict(tool("a2", 110), uuid=None), tool("a3", 115)])
    add("tail first atom without uuid: the next uuid", tw, st(seam(105, [key(tw)])))
    lead = turn([asst("a0", 120, "lead", tools=1), u("u1", 125), tool("a1", 130)], t=90)
    add("head empty: every atom after the seam", lead, st(seam(100, [key(lead)])))
    early = turn([u("u1", 100), asst("a1", 101, "x")], end=200)
    add("tail empty: the seam after every atom", early, st(seam(150, [key(early)])))
    add("a second seam inside the head never re-splits it", w2,
        st(seam(105, [key(w2)], top=SID + ":g1"), seam(107, [key(w2)], top=SID + ":g2")))
    tk = tail_key(w2, 105)
    add("recursive: the tail re-splits at a seam listing its key", w2,
        st(seam(105, [key(w2)], top=SID + ":g1", text="first"), seam(120, [tk], top=SID + ":g2", text="second")))
    w3 = work_turn([tool("a2", 110), tool("a3", 115), tool("a4", 130), tool("a5", 140)])
    tk = tail_key(w3, 105)
    tk2 = jd._seg_key(em.split_segment(em.split_segment(em.segments(w3)[0], 105)[1], 120)[1]["id"])
    add("recursive: three levels", w3, st(seam(105, [key(w3)], text="first"), seam(120, [tk], text="second"),
                                         seam(135, [tk2], text="third")))
    two = turn([u("u1", 100), tool("a1", 105), tool("a2", 110), u("u2", 150), tool("a3", 155), tool("a4", 160)])
    add("one seam lists two segments of the turn", two, st(seam(107, [key(two, 0)]), seam(157, [key(two, 1)])))
    add("one seam listing both keys hits the one whose span holds it", two, st(seam(157, [key(two, 0), key(two, 1)])))
    later = turn([u("u1", 100), u("u2", 150, session_id=None), tool("a3", 155), tool("a4", 160)])
    add("tail of a later segment whose first atom has a null session_id", later, st(seam(152, [key(later, 1)])))
    later2 = turn([u("u1", 100), {k: v for k, v in u("u2", 150).items() if k != "session_id"}, tool("a3", 155)])
    add("tail of a later segment whose first atom has no session_id", later2, st(seam(152, [key(later2, 1)])))
    echo = turn([u("u1", 100), u("e1", 104, "echo"), tool("a2", 110)], placedEchoes=["e1"])
    add("placed echo dropped before the seam split", echo, st(seam(105, [key(dict(echo, atoms=[
        a for a in echo["atoms"] if a["uuid"] != "e1"]))])))

    # em.split_segment directly, on the first segment of em.segments(turn)
    add("split: inside, with real work", w, kind="split", t=105)
    add("split: before every atom", w, kind="split", t=50)
    add("split: after every atom", w, kind="split", t=500)
    add("split: float t equal to an int atom time", w2, kind="split", t=110.0)
    add("split: inside, no real work", work_turn([result("r2", 110)]), kind="split", t=105)

    # shapes the Rust loader refuses
    add("REFUSE pre-cut turn with stored spans", turn([u("u1", 100)], pre=True,
                                                     segs=[["x:1:h", None, 100, 200, 0, 1]]))
    add("REFUSE pre true without stored spans", turn([u("u1", 100)], pre=True))
    add("REFUSE stored spans on a plain turn", turn([u("u1", 100)], segs=[]))
    add("REFUSE trigger a string", turn([asst("a1", 100, "x")], trigger="u1"))
    add("REFUSE placed echoes a string", turn([u("u1", 100)], placedEchoes="u1"))
    add("REFUSE seam not a dict", w, st("a seam", seam(105, [k])))
    add("REFUSE seam t null", w, st(seam(None, [k])))
    add("REFUSE seam text a number", w, st(seam(105, [k], text=5)))
    add("REFUSE seam segs holding a number", w, st(seam(105, [k, 5])))
    add("REFUSE atom t null", turn([u("u1", 100), dict(tool("a2", 110), t=None)]), st(seam(105, [k])))
    return c


def plain(v):
    return json.loads(json.dumps(v))


def answer_cases(km, specs):
    """Python's answer to each (name, kind, turn, store or t), as the edge-set cases (the turn and store as JSON reads
    them back), and the coverage over all of them."""
    em = km.em
    cases, cov = [], new_cov()
    with counting(km) as (stats, seg_key):
        for name, kind, tr, arg in specs:
            tr = plain(tr)
            index_of = {id(a): i for i, a in enumerate(tr.get("atoms") or [])}
            case = {"name": name, "kind": kind, "turn": tr}
            before = dict(stats)
            if kind == "segs":
                case["store"] = plain(arg)
                cov["turns"] += 1
                case["expect"] = segs_answer(km, tr, case["store"], cov, seg_key)
            else:
                case["t"] = arg
                try:
                    sp = em.split_segment(em.segments(tr)[0], arg)
                    case["expect"] = None if sp is None else [seg_out(s, index_of) for s in sp]
                except Exception as e:           # noqa: BLE001
                    case["expect"] = raised(e)
            case["counts"] = {k: stats[k] - before[k] for k in stats}
            cases.append(case)
    cov.update(stats)
    return cases, cov


def edge_main(out):
    l1 = load_l1()
    dw = l1.load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer2-")
    try:
        _root, km, _rec = l1.load_kernel(dw, tmp)
        em = km.em
        cases, cov = answer_cases(km, edge_cases(km))
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer2.py --edge-cases",
               "constants": {"SEAM_PROSE_FLOOR": em.SEAM_PROSE_FLOOR}, "coverage": cov, "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, indent=1, ensure_ascii=True, allow_nan=False) + "\n")
    n_raise = sum(1 for c in cases if isinstance(c["expect"], dict))
    n_refuse = sum(1 for c in cases if c["name"].startswith("REFUSE "))
    print("edge cases: %d (%d segs, %d split), %d where Python raises, %d the loader must refuse; coverage %s -> %s"
          % (len(cases), sum(c["kind"] == "segs" for c in cases), sum(c["kind"] == "split" for c in cases), n_raise,
             n_refuse, json.dumps(cov, sort_keys=True), out))
    return 0


# ───────────────────────────── the randomized check (--fuzz) ─────────────────────────────
# Random turns and stores, Python's answers from the real kernel._segs_seam (one case in twelve: em.split_segment on
# the first segment of em.segments), written in the edge-set format and compared by `romp-spike layer2`. Seeded, so a
# failure replays: `--fuzz N --seed S` makes the same cases. Every case is a shape the loader accepts, so a refusal
# counts as a difference. Seam keys and times come from the real em.segments and em.split_segment of the case's own
# turn, so seams hit, straddle a span, sit on its strict bounds, re-split their tails, and tie.
FUZZ_SIDS = (SID, SID, SID, SID, "eeeeeeee-0000-0000-0000-000000000003", "", "a:b:c", "s\u00e9ance \u2192 \U0001F600")
FUZZ_UUIDS = ("u1", "u2", "a1", "a2", "", "\u00fc-1", "x:y")
# Python's str.strip() and \s whitespace, including the 4 Rust's trim misses (U+001C..U+001F)
FUZZ_WS = " \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0\u1680\u2000\u200a\u2028\u2029\u202f\u205f\u3000"
# ASCII, 2-, 3- and 4-byte UTF-8, a zero-width space and a soft hyphen (not whitespace), a combining accent
FUZZ_CHARS = ("abcdefghijklmnopqrstuvwxyz      ABCXYZ0123456789.,:;-_/()[]<>!?'\""
              "\u00e9\u00ef\u00f1\u00fc\u00df\u00e7\u00f8\u2192\u2014\u2026\u00ab\u00bb\u03a9\u03bb\u0436\u0416"
              "\u65e5\u672c\u8a9e\u4e2d\u6587\U0001F600\U0001F389\u200b\u00ad\u0301")
FUZZ_MARKERS = (INJECT, "<!--romp-injected-->", "<!--\t romp-injected\n-->", "<!- romp-injected -->",
                "<!-- romp-injected ->", PREAMBLE, PREAMBLE.lower(), "[Request interrupted by user]",
                "[Request interrupted by user for tool use]", "[Request interrupted", "<task-notification> done")
FUZZ_AUTHORS = ("human", "human", "human", "sdk", "romp", {"peer": "s002", "host": "TESTHOST"}, {}, "system", "peer",
                "Human", None, "absent")
# time bases: small, zero (and -0.0), negative, a wall clock (int and fractional), just under 2**53 (where a float
# conversion merges neighbors), past 2**62, and floats past 2**63 ("%d" prints every digit), up to f64::MAX
FUZZ_BASES = (100, 100, 0, -50, 1781100000, 1781100000.25, 9007199254740990, 4611686018427387900, 1e20, -1e19,
              1.7976931348623157e308)
FUZZ_STEPS = (0, 0, 1, 1, 2, 5, 10, 0.5, 0.25, 0.7, 1.0)


def fuzz_num(rng, x):
    """`x`, its tag flipped now and then: an integral float inside i64 may become an int, an int a float (rounding it
    past 2**53), a zero -0.0."""
    r = rng.random()
    if isinstance(x, float) and x.is_integer() and abs(x) < 2 ** 63 and r < 0.3:
        return int(x)
    if isinstance(x, int) and r < 0.2:
        return float(x)
    if x == 0 and r < 0.5:
        return -0.0
    return x


def twin(x):
    """An equal time with the other tag, where one exists."""
    if isinstance(x, int):
        return float(x)
    return int(x) if x.is_integer() and abs(x) < 2 ** 63 else x


def fuzz_text(rng):
    r = rng.random()
    if r < 0.07:
        return ""
    if r < 0.13:
        return "".join(rng.choice(FUZZ_WS) for _ in range(rng.randint(1, 4)))
    if r < 0.30:
        tail = "".join(rng.choice(FUZZ_CHARS) for _ in range(rng.randint(0, 12)))
        return rng.choice(("", " ", "\n ", "\x1c", "go on ")) + rng.choice(FUZZ_MARKERS) + rng.choice(("", " ")) + tail
    # prose, its length often near the seam floor (80 code points; more in UTF-8 bytes)
    n = rng.choice((rng.randint(1, 12), rng.randint(70, 90), rng.randint(76, 84), rng.randint(0, 160)))
    s = "".join(rng.choice(FUZZ_CHARS) for _ in range(n))
    if rng.random() < 0.25:
        s = rng.choice(FUZZ_WS) * rng.randint(1, 3) + s + rng.choice(FUZZ_WS) * rng.randint(0, 3)
    return s


def fuzz_block(rng):
    r = rng.random()
    if r < 0.45:
        return {"type": "text", "text": fuzz_text(rng)}
    if r < 0.62:
        b = {"type": "tool_use"}
        for k, vals in (("id", ("tu-1", "tu-2", None)), ("name", ("Bash", "Edit", None))):
            if rng.random() < 0.8:
                b[k] = rng.choice(vals)
        if rng.random() < 0.5:
            b["input"] = {}
        return b
    if r < 0.71:
        return {"type": "tool_result", "tool_use_id": rng.choice(("tu-1", None)), "content": "ok"}
    if r < 0.76:
        return {"type": "thinking", "thinking": fuzz_text(rng)}
    if r < 0.82:
        return {"text": fuzz_text(rng)}
    if r < 0.86:
        return {"type": None, "text": fuzz_text(rng)}
    if r < 0.90:
        return {"type": "text"}
    return rng.choice(("tool_use", "text", 5, 2.5, None, True, [1], [], {}))


def fuzz_message(rng, role):
    """A message dict, or None (null), or "absent"."""
    r = rng.random()
    if r < 0.04:
        return "absent"
    if r < 0.07:
        return None
    if r < 0.09:
        return {}
    m = {"role": role}
    r = rng.random()
    if r < 0.12:
        m["content"] = fuzz_text(rng)
    elif r >= 0.15:
        m["content"] = [fuzz_block(rng) for _ in range(rng.choice((0, 1, 1, 1, 2, 2, 3, 4)))]
    if rng.random() < 0.5:
        m["stop_reason"] = rng.choice(("end_turn", "tool_use", "stop_sequence", None))
    return m


def fuzz_atom(rng, t, serial, i):
    kind = rng.choices(("user", "assistant", "idle", "system", "attachment", ""), (36, 40, 8, 8, 6, 2))[0]
    a = {"type": kind}
    r = rng.random()
    if r < 0.07:
        a["uuid"] = None
    elif r < 0.40:
        a["uuid"] = rng.choice(FUZZ_UUIDS)
    elif r < 0.95:
        a["uuid"] = "f%d-%d" % (serial, i)
    a["t"] = t
    r = rng.random()
    if r < 0.04:
        a["session_id"] = None
    elif r < 0.98:
        a["session_id"] = rng.choice(FUZZ_SIDS)
    if kind == "user" or rng.random() < 0.05:
        au = rng.choice(FUZZ_AUTHORS)
        if au != "absent":
            a["author"] = dict(au) if isinstance(au, dict) else au
    if kind == "idle":
        a["end"] = fuzz_num(rng, t + rng.choice(FUZZ_STEPS))
        if rng.random() < 0.8:
            return a
    if kind == "assistant" and rng.random() < 0.35:
        m = {"role": "assistant", "content": [{"type": "tool_use", "id": "tu-%d-%d" % (serial, i), "name": "Bash",
                                               "input": {}}], "stop_reason": "tool_use"}
    else:
        m = fuzz_message(rng, "assistant" if kind == "assistant" else "user")
    if m != "absent":
        a["message"] = m
    if kind == "assistant" and rng.random() < 0.06:
        a["isApiError"] = rng.choice((True, False, None))
    if rng.random() < 0.04:
        a["origin"] = rng.choice(({"kind": "task-notification", "subkind": "scheduled-trigger"},
                                  {"kind": "task-notification"}, "scheduled-trigger", None))
    if rng.random() < 0.03:
        a["lazy"] = None
    return a


def fuzz_turn(rng, serial):
    n = rng.choice((0, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 7, 8, 10))
    t, times = rng.choice(FUZZ_BASES), []
    for _ in range(n):
        times.append(fuzz_num(rng, t))
        t = t + rng.choice(FUZZ_STEPS)
    if n >= 2 and rng.random() < 0.08:                      # out of order
        i, j = rng.sample(range(n), 2)
        times[i], times[j] = times[j], times[i]
    atoms = [fuzz_atom(rng, x, serial, i) for i, x in enumerate(times)]
    tr = {"atoms": atoms}
    if atoms and rng.random() < 0.7:
        tr["t"] = atoms[0]["t"]
    else:
        tr["t"] = fuzz_num(rng, (times[0] if times else rng.choice(FUZZ_BASES)) - rng.choice(FUZZ_STEPS))
    r = rng.random()
    tr["end"] = fuzz_num(rng, t if r < 0.7 else rng.choice(times + [tr["t"]]) if r < 0.9 else rng.choice(FUZZ_BASES))
    tr["ended"] = rng.random() < 0.5
    tr["id"] = "fuzz-turn-%d" % serial
    shape = rng.choices(range(8), (6, 10, 8, 6, 10, 12, 10, 38))[0]   # every trigger shape (0: absent)
    uuids = [a["uuid"] for a in atoms if isinstance(a.get("uuid"), str)]
    if shape == 1:
        tr["trigger"] = None
    elif shape == 2:
        tr["trigger"] = {}
    elif shape == 3:
        tr["trigger"] = {"source": "prompt"}
    elif shape == 4:
        tr["trigger"] = {"uuid": None}
    elif shape == 5:
        tr["trigger"] = {"uuid": ""}
    elif shape == 6:
        tr["trigger"] = {"uuid": "zz-not-there"}
    elif shape == 7:
        tr["trigger"] = {"uuid": rng.choice(uuids) if uuids else "u1"}
    if isinstance(tr.get("trigger"), dict) and rng.random() < 0.1:
        tr["trigger"]["source"] = "prompt"
    r = rng.random()
    if r < 0.08:
        tr["placedEchoes"] = None
    elif r < 0.13:
        tr["placedEchoes"] = []
    elif r < 0.30:
        pool = [a.get("uuid") for a in atoms] + ["nope", None]
        tr["placedEchoes"] = [rng.choice(pool) for _ in range(rng.randint(1, 3))]
    if rng.random() < 0.03:
        tr["pre"] = rng.choice((False, 0, "", None, [], {}))
    return tr


def fuzz_aims(rng, km, segs):
    """(key, time) pairs aimed at the segments, and a pool of times. For each segment a time from its atoms' times,
    the midpoints between distinct neighbors and its span's ends; when em.split_segment cuts it there, the tail is
    aimed at in turn (up to three levels), so a seam listing a tail's key re-splits it."""
    em, jd = km.em, km.jd
    aims, times = [], []
    for s in segs:
        times += [s["t"], s["end"]]
        piece = s
        for _ in range(rng.choice((1, 2, 3))):
            ts = [a.get("t", 0) for a in piece["atoms"]]
            times += ts
            mids = [m for m in (x + (y - x) / 2 for x, y in zip(ts, ts[1:]) if x != y) if math.isfinite(m)]
            cand = mids if mids and rng.random() < 0.6 else [c for c in ts + mids + [piece["t"], piece["end"]]
                                                              if math.isfinite(c)]
            t = fuzz_num(rng, rng.choice(cand))
            aims.append((jd._seg_key(piece["id"]), t))
            try:
                sp = em.split_segment(piece, t)
            except Exception:                       # noqa: BLE001 (no aim then; the answer records any raise)
                sp = None
            if not sp:
                break
            piece = sp[1]
            times.append(t)
    return aims, [x for x in times if math.isfinite(x)]


def fuzz_seam(rng, aims, times, ids, aim=None):
    if aim is not None:
        key, t = aim
    elif aims and rng.random() < 0.65:
        key, t = rng.choice(aims)
    else:
        key = rng.choice(aims)[0] if aims else SID + ":deadbeef"
        t = rng.choice(times) if times else rng.choice(FUZZ_BASES)
    t = fuzz_num(rng, t)
    if rng.random() < 0.1 and abs(t) < 1e300:
        t = t + rng.choice((-0.5, 0.5, -1, 1))
    keys = [key]
    for _ in range(rng.choice((0, 0, 0, 1, 2))):
        keys.append(rng.choice((SID + ":deadbeef", "", rng.choice(ids) if ids else "x:1:y",
                                rng.choice(aims)[0] if aims else "x:y")))
    rng.shuffle(keys)
    s = {}
    if rng.random() >= 0.04:
        s["t"] = t
    r = rng.random()
    if r < 0.08:
        s["top"] = None
    elif r < 0.90:
        s["top"] = rng.choice((TOP, SID + ":g2", "", "\u00fc:g3"))
    r = rng.random()
    if r < 0.08:
        s["text"] = None
    elif r < 0.88:
        s["text"] = fuzz_text(rng)
    r = rng.random()
    if r < 0.04:
        s["segs"] = None
    elif r < 0.08:
        s["segs"] = []
    elif r < 0.94:
        s["segs"] = keys
    if rng.random() < 0.05:
        s["stamp"] = 1
    return s


def fuzz_store(rng, km, tr):
    r = rng.random()
    if r < 0.05:
        return None
    if r < 0.08:
        return {}
    if r < 0.10:
        return {"rompUuid": SID}
    if r < 0.12:
        return {"rompUuid": SID, "seams": None}
    if r < 0.14:
        return {"rompUuid": SID, "seams": []}
    try:
        segs = km.em.segments(km._turn_sans_placed_echoes(tr))
    except Exception:                               # noqa: BLE001 (Python raises before apply_seams; any store will do)
        segs = []
    aims, times = fuzz_aims(rng, km, segs)
    ids = [s["id"] for s in segs]
    seams = []
    if aims and rng.random() < 0.35:                # a chain: a seam aimed at each segment and each tail cut from it
        seams = [fuzz_seam(rng, aims, times, ids, aim) for aim in aims]
        rng.shuffle(seams)
    more = rng.choice((0, 1, 1, 2, 3) if seams else (1, 1, 2, 2, 3, 4))
    seams += [fuzz_seam(rng, aims, times, ids) for _ in range(more)]
    if rng.random() < 0.15:                         # a tie: the same keys at an equal time, the other tag first or last
        s0 = rng.choice(seams)
        if "t" in s0:
            seams.insert(rng.randrange(len(seams) + 1), dict(s0, t=twin(s0["t"]), top=SID + ":gT", text="the twin"))
    return {"rompUuid": SID, "seams": seams}


def fuzz_specs(rng, km, n, seed):
    """`n` cases (name, kind, turn, store or t)."""
    specs = []
    for i in range(n):
        tr = fuzz_turn(rng, i)
        name = "fuzz seed %d case %d" % (seed, i)
        if rng.random() < 1 / 12:
            try:
                aims, times = fuzz_aims(rng, km, km.em.segments(tr)[:1])
                t = rng.choice([x for _, x in aims] + times)
            except Exception:                       # noqa: BLE001 (em.segments raises: any t)
                t = rng.choice(FUZZ_BASES)
            specs.append((name, "split", tr, t))
        else:
            specs.append((name, "segs", tr, fuzz_store(rng, km, tr)))
    return specs


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


# romp-spike's edge-set summary line (main.rs, layer2_edge)
RUST_SUMMARY = re.compile(r"^layer2 edge set(?: \[planted: [a-z]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"values, (\d+) counts\), (\d+) refused at load \(every REFUSE case\), (\d+) differences ")


def fuzz_reach(cases):
    """What the cases make Python do, beyond the coverage counts: raises by exception, cases that split once and more
    than once, segments with a float t, seamOf texts null and "", and the segments a search for an empty trigger uuid
    could misread (trigger "", and an atom after the first whose uuid is "": the search would anchor on it)."""
    reach = {"raises": {}, "casesSplit": 0, "casesSplitTwice": 0, "floatT": 0, "seamOfTextNull": 0,
             "seamOfTextEmpty": 0, "emptyTriggerLaterEmptyUuid": 0}
    for c in cases:
        e = c["expect"]
        if isinstance(e, dict):
            k = "%s %s" % (e["raises"], e["arg"])
            reach["raises"][k] = reach["raises"].get(k, 0) + 1
            continue
        reach["casesSplit"] += c["counts"]["splits"] >= 1
        reach["casesSplitTwice"] += c["counts"]["splits"] >= 2
        for s in e or []:
            reach["floatT"] += isinstance(s["t"], float)
            reach["emptyTriggerLaterEmptyUuid"] += s["trigger"] == "" and "" in s["atoms"][1:]
            so = s.get("seamOf")
            if so is not None:
                reach["seamOfTextNull"] += so["text"] is None
                reach["seamOfTextEmpty"] += so["text"] == ""
    return reach


def fuzz_main(n, seed, plant, binary, out):
    """Make `n` cases from `seed`, answer them in Python, and compare with `romp-spike layer2` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    l1 = load_l1()
    dw = l1.load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer2-fuzz-")
    try:
        t0 = time.monotonic()
        _root, km, _rec = l1.load_kernel(dw, tmp)
        specs = fuzz_specs(random.Random(seed), km, n, seed)
        cases, cov = answer_cases(km, specs)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer2.py --fuzz %d --seed %d" % (n, seed),
               "constants": {"SEAM_PROSE_FLOOR": km.em.SEAM_PROSE_FLOOR}, "coverage": cov, "cases": cases}
        path = out or os.path.join(tmp, "layer2_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        reach = fuzz_reach(cases)
        nsplit = sum(c["kind"] == "split" for c in cases)
        print("fuzz seed %d: %d cases (%d segs, %d split) made and answered by Python in %.1f s, %.1f MB" % (
            seed, n, n - nsplit, nsplit, py_s, os.path.getsize(path) / 1e6))
        print("  python: coverage %s" % json.dumps(cov, sort_keys=True))
        print("  python: reach %s" % json.dumps(reach, sort_keys=True, ensure_ascii=False))
        p = subprocess.run([binary, "layer2", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        lines = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, lines) if m]
        for line in lines:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer2: romp-spike layer2 exited %d without its summary line: %s" % (
                p.returncode, p.stderr.strip()[-2000:]), file=sys.stderr)
            return 2
        ncases, loaded, values, counts, refused, diffs = map(int, summary[0].groups())
        if ncases != n or (plant is None and (loaded != n or refused != 0 or diffs != 0 or p.returncode != 0)):
            print("fuzz seed %d: FAILED, %d differences over %d cases (%d loaded, %d refused at load)" % (
                seed, diffs, ncases, loaded, refused))
            return 1
        if plant is None:
            print("fuzz seed %d: OK, %d cases, %d values and %d counts compared, 0 differences" % (
                seed, n, values, counts))
            return 0
        print("fuzz seed %d, planted %s: %s, %d differences over %d cases" % (
            seed, plant, "caught" if diffs else "NOT CAUGHT", diffs, n))
        return 0 if diffs else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random cases with romp-spike layer2")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=("end", "bytes"), help="--fuzz against romp-spike layer2 --plant NAME: the "
                    "check passes when the plant is caught")
    ap.add_argument("--bin", help="a built romp-spike for --fuzz (default: cargo build --release)")
    ap.add_argument("--out", help="keep the --fuzz cases in this file (default: a temporary file, removed)")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if a.fuzz is not None:
        if a.fuzz < 1:
            ap.error("--fuzz N needs N >= 1")
        return fuzz_main(a.fuzz, a.seed, a.plant, rust_binary(a.bin), a.out)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT, or --fuzz N")
    l1 = load_l1()
    dw = l1.load_dw()
    first = os.path.realpath(a.dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        root, km, rec = l1.load_kernel(dw, work)
        for d in a.dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer2: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            cov, size = dump_one(dw, km, d)
            print("%s: %s -> %s (%.1f MB)" % (os.path.basename(d), json.dumps(cov, sort_keys=True),
                                              os.path.join(d, OUT_NAME), size / 1e6))
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer2: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
