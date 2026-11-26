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

Neither world splits a segment (each holds one seam, and no piece's span holds its time), so the edge cases build
the branches the worlds never reach: straddling seams, recursive splits, equal and int-vs-float times, absent and null
seam text and top, the prose floor at 79/80/81 code points of non-ASCII text, placed echoes, triggers of every shape,
float times printed with "%d", and the inputs on which Python raises. A case that raises records
{"raises": <exception name>, "arg": str(e)}, and every case records what apply_seams did (`counts`: pieces examined,
em.split_segment calls, splits). A case named "REFUSE ..." is one the Rust loader must refuse. Stdlib only.
"""
import argparse
import contextlib
import importlib.util
import json
import os
import platform
import shutil
import sys
import tempfile

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


def edge_main(out):
    l1 = load_l1()
    dw = l1.load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer2-")
    try:
        _root, km, _rec = l1.load_kernel(dw, tmp)
        em = km.em
        cases, cov = [], new_cov()
        with counting(km) as (stats, seg_key):
            for name, kind, tr, arg in edge_cases(km):
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT")
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
