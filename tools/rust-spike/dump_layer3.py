#!/usr/bin/env python3
"""Python's own answers for layer 3 of the Rust spike's port (rust/notes/s2-design.md section 2, "Tree facts and the
anchor walk"; rust/SPIKE.md step S4.3), for the Rust side to match with zero differences.

It loads the real kernel the way dump_layer1.py does (an empty state root, the bench guards) and, per alive session of
a dump, calls the real functions as the per-card build calls them: kernel._session_working over the tree's turns (inside
the key's try, False without a tree) and its last _suspended_after read, kernel._open_turn_progress, the anchor walk
kernel._feed_seg_walk with nothing held (_FEED_SEG_HELD cleared, head None) and kernel._feed_seg_plain_t over it, then
`k in seg_turn` and `seg_turn.get(k)` for every key the walk recorded and one absent key, counting the faults those gets
add (_SUMMARY_ANCHOR_STATS["fault"]). Per turn it also records, per segment of kernel._segs_seam(turn, store), the
answers of _seg_anchors, _seg_jump, jd._prompt_anchor_uuid and _seg_last_text (or the exception segmentation raised).
The board's downtime list (snapshot.board.downtime) is installed as kernel._downtime first. `romp-spike layer3 DUMP`
computes the same over the typed structs and compares.

    python3 tools/rust-spike/dump_layer3.py DUMP_DIR [DUMP_DIR ...]   # writes DUMP_DIR/layer3.json
    python3 tools/rust-spike/dump_layer3.py --edge-cases OUT.json     # the synthetic cases (romp-spike layer3 OUT.json)
    python3 tools/rust-spike/dump_layer3.py --fuzz N [--seed S] [--plant NAME] [--bin ROMP_SPIKE] [--out FILE]

A dump also gets a self-check of the held aggregate the port leaves out: each session is walked again with a leaf head,
twice (the second walk served from the hold the first stored), and both must give the same maps and plain-prompt time
as the walk with nothing held (`heldCheck` in layer3.json).

The worlds miss most branches (no session is suspended, no key repeats, no walk raises, no settle, attachment or
API error, _FeedSegTurns.get runs on positions that always match), so the edge cases build them: empty and ended turns,
trailing command atoms, idle tails, downtime around the last activity, echo turns and romp-goal-id prompts, every
trigger shape (with no trigger uuid, the first atom without a uuid is the anchor), zero and tied times of both tags,
settle and synthetic replies, empty uuids, bare ends, attachments, the substantive floor at 79/80 code points, repeated
keys, raises in the first, a middle and the open turn, stale positions handed to _FeedSegTurns, and direct
_suspended_after reads. A case named "REFUSE ..." is one the Rust loader must refuse.

`--fuzz N` is the randomized differential check: N random sessions (layer 2's random turns, decorated with command
atoms, idle tails, echoes, settles, attachments, repeated turns that share keys, downtime and seams aimed at the turns'
segments; one case in twelve hands _FeedSegTurns perturbed positions), answered by the real functions, written in the
edge-set format and compared by `romp-spike layer3`; any difference or refusal fails it. `--seed S` replays. With
`--plant NAME` it passes only when romp-spike's planted mistake is caught. Stdlib only.
"""
import argparse
import copy
import importlib.util
import json
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
SCHEMA = "romp-spike-layer3/1"
EDGE_SCHEMA = "romp-spike-layer3-edge/1"
OUT_NAME = "layer3.json"
ABSENT_KEY = "absent-key"          # no fold writes it: every seg key holds a colon (layer3.rs ABSENT_KEY)
HEAD = ("dump-head", 0)            # a leaf head for the held-aggregate self-check
PLANTS = ("max-last", "overwrite-moves", "bytes", "raise-skips")


def load_l2():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer2", os.path.join(HERE, "dump_layer2.py"))
    l2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l2)
    return l2


def raised(e):
    return {"raises": type(e).__name__, "arg": str(e)}


def seg_turn_out(turns, got):
    """A _FeedSegTurns.get answer: null, or [turn index, [each atom's index in that turn]] (by identity)."""
    if got is None:
        return None
    turn, atoms = got
    ti = next(i for i, t in enumerate(turns) if t is turn)
    index_of = {id(a): i for i, a in enumerate(turn.get("atoms") or [])}
    return [ti, [index_of[id(a)] for a in atoms]]


def turn_rows(km, turn, store):
    """Per segment of kernel._segs_seam(turn, store): [key, work, reply, jump, trig, lastUuid, lastSubstantive, t]."""
    try:
        segs = km._segs_seam(turn, store)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        return raised(e)
    rows = []
    for seg in segs:
        atoms = seg["atoms"]
        work, reply = km._seg_anchors(atoms)
        lu, lsub = km._seg_last_text(atoms)
        rows.append([km._seg_key(seg["id"]), work, reply, km._seg_jump(atoms), km.jd._prompt_anchor_uuid(seg), lu,
                     lsub, seg.get("t", 0)])
    return rows


def walk_maps(w):
    return {"walkOk": w.ok,
            "segUuid": [[k, v] for k, v in w.seg_uuid.items()],
            "segTrig": [[k, v] for k, v in w.seg_trig.items()],
            "segBest": [[k, list(v)] for k, v in w.seg_best.items()],
            "pos": [[k, list(v)] for k, v in w.seg_turn._pos.items()],
            "cite": sorted(w.cite_uuids)}


def session_answer(km, tree, store, downtime, fsid="fsid"):
    """One session's answer, keys in the order romp-spike writes them (layer3.rs session_json)."""
    km._downtime[:] = [tuple(x) for x in downtime]
    try:
        ps = tree
        turns = ps["turns"] if ps else None
        ans = {}
        try:
            ans["working"] = km._session_working(turns) if ps else False    # the key's try (_feed_session_key)
        except Exception:                                                    # noqa: BLE001
            ans["working"] = False
        if turns:
            lt = turns[-1]
            ans["suspended"] = km._suspended_after(lt.get("end", lt.get("t")))
        else:
            ans["suspended"] = None
        ans["progress"] = km._open_turn_progress(turns) if ps else None
        km._FEED_SEG_HELD.clear()
        w = km._feed_seg_walk(fsid, None, turns, store)
        ans.update(walk_maps(w))
        ans["plainT"] = km._feed_seg_plain_t(w) if ps else 0
        st = w.seg_turn
        f0 = km._SUMMARY_ANCHOR_STATS["fault"]
        queries = []
        for k in list(st._pos) + [ABSENT_KEY]:
            has = k in st
            queries.append([k, has, seg_turn_out(turns, st.get(k))])
        ans["segTurn"] = queries
        ans["faults"] = km._SUMMARY_ANCHOR_STATS["fault"] - f0
        ans["turns"] = [turn_rows(km, t, store) for t in (turns or [])]
        return ans, w
    finally:
        km._downtime[:] = []


def held_check(km, fsid, turns, store, downtime, ans):
    """Walk with a leaf head twice (the second served from the hold the first stored): (agrees, turns served)."""
    km._downtime[:] = [tuple(x) for x in downtime]
    try:
        km._FEED_SEG_HELD.clear()
        want = json.dumps([{k: ans[k] for k in ("walkOk", "segUuid", "segTrig", "segBest", "pos", "cite")},
                           ans["plainT"]])
        agree, served = True, 0
        for _ in range(2):
            w = km._feed_seg_walk(fsid, HEAD, turns, store)
            p = km._feed_seg_plain_t(w)
            served = max(served, w.start)
            agree = agree and json.dumps([walk_maps(w), p]) == want
        km._FEED_SEG_HELD.clear()
        return agree, served
    finally:
        km._downtime[:] = []


def new_cov():
    return {"sessions": 0, "turns": 0, "segments": 0, "keys": 0, "overwrites": 0, "working": 0, "suspended": 0,
            "progress": 0, "walkNotOk": 0, "raisingTurns": 0, "jumpFallback": 0, "substantive": 0, "cite": 0,
            "plainFloat": 0, "faults": 0, "segTurnHits": 0, "segTurnsCases": 0}


def count(cov, ans):
    """What one answer reached (the header's and the fuzz's `coverage`)."""
    cov["sessions"] += 1
    cov["turns"] += len(ans["turns"])
    cov["working"] += ans["working"] is True
    cov["suspended"] += ans["suspended"] is True
    cov["progress"] += ans["progress"] is not None
    cov["walkNotOk"] += not ans["walkOk"]
    cov["keys"] += len(ans["pos"])
    cov["cite"] += len(ans["cite"])
    cov["plainFloat"] += isinstance(ans["plainT"], float)
    cov["faults"] += ans["faults"]
    cov["segTurnHits"] += sum(1 for q in ans["segTurn"] if q[2] is not None)
    folded = 0
    for rows in ans["turns"]:
        if isinstance(rows, dict):
            cov["raisingTurns"] += 1
            continue
        cov["segments"] += len(rows)
        for _k, _w, reply, jump, _tr, _lu, lsub, _t in rows:
            cov["jumpFallback"] += (not reply) and jump is not None
            cov["substantive"] += lsub
    if ans["walkOk"]:
        folded = sum(len(r) for r in ans["turns"] if not isinstance(r, dict))
        cov["overwrites"] += folded - len(ans["pos"])


def dump_one(dw, km, out):
    snap, trees = dw.load_dump(out)
    downtime = snap["board"].get("downtime") or []
    sessions, cov = [], new_cov()
    held = {"sessions": 0, "agree": 0, "turnsServed": 0}
    for r in snap["alive"]:
        sid = r["sid"]
        tree = trees.get(sid)
        store = (snap["sessions"].get(sid) or {}).get("store")
        ans, _w = session_answer(km, tree, store, downtime, fsid=sid)
        count(cov, ans)
        if tree:
            agree, served = held_check(km, sid, tree["turns"], store, downtime, ans)
            held["sessions"] += 1
            held["agree"] += agree
            held["turnsServed"] += served
        sessions.append({"sid": sid, "answer": ans})
    doc = {"schema": SCHEMA, "world": snap["world"]["name"], "python": platform.python_version(),
           "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "heldCheck": held,
           "sessions": sessions}
    path = os.path.join(out, OUT_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return cov, held, os.path.getsize(path)


# ───────────────────────────── the edge cases ─────────────────────────────
SETTLE = "No response requested."


def edge_cases(km, l2):
    """(name, kind, payload). kind "session": {tree, store, downtime}; "segturns": {tree, store, pos, keys} (a direct
    _FeedSegTurns over hand-made positions, as a held aggregate would hand them); "suspended": {downtime, t}."""
    em, jd = km.em, km.jd
    SID, u, asst, tool, result, idle, turn, pad = l2.SID, l2.u, l2.asst, l2.tool, l2.result, l2.idle, l2.turn, l2.pad
    TOP = l2.TOP

    def tree(*turns):
        return {"turns": list(turns)}

    def att(uuid, t, **kw):
        a = {"type": "attachment", "uuid": uuid, "t": t, "session_id": SID}
        a.update(kw)
        return a

    def cmd(uuid, t, command="/model", **kw):
        a = {"type": "system", "uuid": uuid, "t": t, "session_id": SID, "command": command}
        a.update(kw)
        return a

    def settle(uuid, t, **kw):
        return asst(uuid, t, SETTLE, **kw)

    def synthetic(uuid, t, text="Interrupted.", **kw):
        a = asst(uuid, t, text, **kw)
        a["message"]["model"] = "<synthetic>"
        return a

    def bare_end(uuid, t):
        return asst(uuid, t, None)

    def thinking(uuid, t):
        a = asst(uuid, t, None)
        a["message"]["content"] = [{"type": "thinking", "thinking": "hmm"}]
        a["message"]["stop_reason"] = None
        return a

    def open_turn(atoms, **kw):
        return turn(atoms, ended=False, **kw)

    def seg_key(tr, i=0):
        return jd._seg_key(em.segments(tr)[i]["id"])

    def store(*seams):
        return {"rompUuid": SID, "seams": list(seams)}

    def seam(t, keys, text="the settled goal"):
        return {"t": t, "top": TOP, "text": text, "segs": keys}

    c = []

    def add(name, tr, st=None, downtime=()):
        c.append((name, "session", {"tree": tr, "store": st, "downtime": [list(x) for x in downtime]}))

    def gets(name, tr, pos, keys, st=None):
        c.append((name, "segturns", {"tree": tr, "store": st, "pos": [[k, list(p)] for k, p in pos], "keys": keys}))

    def susp(name, downtime, t):
        c.append((name, "suspended", {"downtime": [list(x) for x in downtime], "t": t}))

    working = open_turn([u("u1", 100), tool("a1", 105), tool("a2", 106)], end=110)

    # _session_working and _suspended_after
    add("no tree", None)
    add("no turns", tree())
    add("open turn with tool uses: working", tree(working))
    add("ended last turn: not working", tree(turn([u("u1", 100), asst("a1", 101, "done")], end=110)))
    add("open turn ending in an idle span: not working", tree(open_turn([u("u1", 100), idle(101, 110)], end=110)))
    add("idle span in the middle, resumed: working",
        tree(open_turn([u("u1", 100), idle(101, 105), u("u2", 106, "again"), tool("a2", 107)], end=108)))
    add("trailing command after the idle tail is skipped: not working",
        tree(open_turn([u("u1", 100), idle(101, 110), cmd("c1", 111)], end=111)))
    add("two trailing commands after a tool use: working",
        tree(open_turn([u("u1", 100), tool("a1", 101), cmd("c1", 102), cmd("c2", 103, command={"name": "/x"})],
                       end=103)))
    add("every atom a command: working", tree(open_turn([cmd("c1", 100), cmd("c2", 101)], end=101,
                                                          trigger={"uuid": "c1"})))
    add("a falsy command is not skipped: the idle before it does not end the turn",
        tree(open_turn([u("u1", 100), idle(101, 110), cmd("c1", 111, command=""), cmd("c2", 112, command=0),
                        cmd("c3", 113, command=None)], end=113)))
    add("open turn with no atoms: working, no tool uses", tree(open_turn([], t=100, end=100, trigger=None)))
    add("ended absent reads open", tree({k: v for k, v in working.items() if k != "ended"}))
    add("downtime after the last activity: suspended, not working", tree(working), downtime=[(110.5, 200.0)])
    add("downtime at the last activity is not after it: working", tree(working), downtime=[(110, 200)])
    add("downtime equal as a float: working", tree(working), downtime=[(110.0, 200.0)])
    add("downtime before the turn: working", tree(working), downtime=[(50.0, 60.0), (90.25, 95.5)])
    add("downtime: one of several after", tree(working), downtime=[(50.0, 60.0), (300.0, 400.0)])
    add("downtime after an ended turn: still not working", tree(turn([u("u1", 100)], end=110)), downtime=[(200, 300)])
    add("float end against an int start", tree(open_turn([u("u1", 100), tool("a1", 101)], end=110.5)),
        downtime=[(110, 120)])
    add("zero end: t or 0", tree(open_turn([u("u1", -10), tool("a1", -5)], end=0)), downtime=[(-1, 1)])
    add("negative end, earlier downtime start after it", tree(open_turn([u("u1", -10), tool("a1", -5)], end=-5)),
        downtime=[(-1, 1)])

    # _open_turn_progress
    p_atoms = [u("u1", 100), tool("a1", 101),
               asst("a2", 102, None, message={"role": "assistant", "content": "tool_use"}),
               asst("a3", 103, None, message={"role": "assistant", "content": [
                   "tool_use", {"type": "tool_use"}, {"type": "tool_use", "id": "x"}, {"type": "text", "text": "t"},
                   {"name": "tool_use"}, 5, None]}),
               asst("a4", 104, None, message={}), asst("a5", 105, None, message=None),
               {k: v for k, v in asst("a6", 106, None).items() if k != "message"},
               dict(result("r1", 107), message={"role": "user", "content": [{"type": "tool_use"}]}),
               dict(tool("a7", 108), isApiError=True)]
    add("progress counts dict tool_use blocks of assistant atoms only", tree(open_turn(p_atoms, end=110)))
    add("progress since a float turn time", tree(open_turn([u("u1", 100.75), tool("a1", 101)], end=102)))
    add("progress since the turn's t, not its first atom's", tree(open_turn([asst("a0", 95, "lead"), u("u1", 100),
                                                                            tool("a1", 101)], t=90, end=102)))
    add("progress on an ended turn after an open one", tree(working, turn([u("u9", 200)], end=210)))

    # _last_plain_user_turn_t
    def closed(atoms, **kw):
        return turn(atoms, **kw)
    add("plain prompts: the latest wins", tree(closed([u("u1", 100)]), closed([u("u2", 300)]), closed([u("u3", 200)])))
    add("echo turn skipped", tree(closed([u("u1", 100)]), closed([u("u2", 200)], echoTurn=True)))
    add("falsy echoTurn values do not skip", tree(closed([u("u1", 100)], echoTurn=0), closed([u("u2", 200)], echoTurn=""),
                                                  closed([u("u3", 300)], echoTurn=False)))
    add("an anchor with _echo_text is skipped", tree(closed([u("u1", 100)]), closed([u("u2", 200, _echo_text="hi")])))
    add("an empty _echo_text does not skip", tree(closed([u("u1", 100)]), closed([u("u2", 200, _echo_text="")])))
    add("romp-goal-id prompts are targeted replies", tree(closed([u("u1", 100)]), closed([u(
        "u2", 200, "on it <!-- romp-goal-id: s1:g1 -->")])))
    add("romp-goal-id in a bare-string content", tree(closed([u("u1", 100)]), closed([dict(u("u2", 200), message={
        "role": "user", "content": "  reply romp-goal-id=g2 "})])))
    add("romp-goal-id in a non-text block does not count", tree(closed([dict(u("u2", 200), message={
        "role": "user", "content": [{"type": "text", "text": "ok"}, {"type": "tool_result", "content": "romp-goal-id"}]})])))
    add("authors other than human are not plain",
        tree(closed([u("u1", 100, author="sdk")]), closed([u("u2", 200, author="romp")]),
             closed([u("u3", 300, author={"peer": "s002", "host": "TESTHOST"})]),
             closed([u("u4", 400, author="system")]), closed([u("u5", 450, author="Human")])))
    add("the trigger names a later atom", tree(closed([u("u1", 100, author="sdk"), u("u2", 150)], trigger={"uuid": "u2"})))
    add("the trigger names an sdk atom after a human one",
        tree(closed([u("u1", 100), u("u2", 150, author="sdk")], trigger={"uuid": "u2"})))
    add("trigger not among the atoms: the first atom", tree(closed([u("u1", 100), u("u2", 150, author="sdk")],
                                                                    trigger={"uuid": "zz"})))
    add("no trigger: the first atom without a uuid (an idle span) is the anchor",
        tree(closed([u("u1", 100), idle(101, 105)], trigger=None)))
    add("no trigger and every atom has a uuid: the first atom", tree(closed([u("u1", 100), asst("a1", 101, "x")],
                                                                             trigger=None)))
    add("trigger without uuid: an atom whose uuid is absent",
        tree(closed([u("u1", 100), {k: v for k, v in u("u2", 150).items() if k != "uuid"}], trigger={"source": "x"})))
    add("trigger an empty dict", tree(closed([u("u1", 100), idle(101, 102)], trigger={})))
    add("trigger uuid empty matches an atom whose uuid is empty",
        tree(closed([u("u1", 100, author="sdk"), u("", 150)], trigger={"uuid": ""})))
    add("trigger absent", tree({k: v for k, v in closed([u("u1", 100)]).items() if k != "trigger"}))
    add("zero atom time falls to the turn's t", tree(closed([u("u1", 0)], t=250.5)))
    add("zero atom and turn times give 0", tree(closed([u("u1", 0)], t=0), closed([u("u2", 0.0)], t=-0.0)))
    add("negative times keep 0", tree(closed([u("u1", -5)], t=-5)))
    add("equal times: the int first is kept", tree(closed([u("u1", 5)]), closed([u("u2", 5.0)]), closed([u("u3", 1)])))
    add("equal times: the float first is kept", tree(closed([u("u1", 5.0)]), closed([u("u2", 5)]), closed([u("u3", 1)])))
    add("a tie across the closed and open turns keeps the closed time",
        tree(closed([u("u1", 7.0)]), open_turn([u("u2", 7), tool("a2", 8)], end=9)))
    add("a tie across the closed and open turns, int closed",
        tree(closed([u("u1", 7)]), open_turn([u("u2", 7.0), tool("a2", 8)], end=9)))
    add("an int past 2**53 beats the float it rounds to", tree(closed([u("u1", 9007199254740992.0)]),
                                                                closed([u("u2", 9007199254740993)])))
    add("the float 2**53 does not beat the int 2**53", tree(closed([u("u1", 9007199254740992)]),
                                                             closed([u("u2", 9007199254740992.0)])))
    add("one turn: only the open read", tree(open_turn([u("u1", 42.5), tool("a1", 43)], end=44)))
    add("no atoms: no anchor", tree(closed([], t=100, trigger=None), closed([u("u2", 50)])))

    # _seg_anchors, _seg_jump, jd._prompt_anchor_uuid, _seg_last_text
    add("settle only: the settle is the reply", tree(closed([u("u1", 100), settle("s1", 101)])))
    add("synthetic model reply is a settle", tree(closed([u("u1", 100), synthetic("s1", 101), tool("a2", 102)])))
    add("settle then reply: the reply", tree(closed([u("u1", 100), settle("s1", 101), asst("r1", 102, "done")])))
    add("reply then settle: still the reply", tree(closed([u("u1", 100), asst("r1", 101, "done"), settle("s1", 102)])))
    add("API error skipped for both anchors",
        tree(closed([u("u1", 100), asst("r1", 101, "done"), asst("e1", 102, "API Error: 500", isApiError=True)])))
    add("API error first: work is the next atom",
        tree(closed([u("u1", 100), asst("e1", 101, "API Error", isApiError=True), tool("a2", 102)])))
    add("isApiError false is not skipped", tree(closed([u("u1", 100), asst("r1", 101, "fine", isApiError=False)])))
    add("bare end skipped for work, not a reply", tree(closed([u("u1", 100), bare_end("b1", 101), tool("a2", 102)])))
    add("bare end alone: no work, no reply, no jump", tree(closed([u("u1", 100), bare_end("b1", 101)])))
    add("work without a uuid is set again by a later atom",
        tree(closed([u("u1", 100), dict(tool("a1", 101), uuid=None), tool("a2", 102)])))
    add("work uuid empty stays", tree(closed([u("u1", 100), dict(tool("a1", 101), uuid=""), tool("a2", 102)])))
    add("a later uuid-less reply resets the reply", tree(closed([u("u1", 100), asst("r1", 101, "one"),
                                                                 dict(asst("r2", 102, "two"), uuid=None)])))
    add("an empty reply uuid falls to the settle", tree(closed([u("u1", 100), settle("s1", 101),
                                                                asst("", 102, "late reply")])))
    add("an empty reply uuid and no settle: the jump", tree(closed([u("u1", 100), tool("a1", 101),
                                                                    asst("", 102, "late reply")])))
    add("no reply: the jump is the first tool use", tree(closed([u("u1", 100), thinking("k1", 101), tool("a2", 102),
                                                                  tool("a3", 103)])))
    add("the jump skips uuid-less, empty-uuid and API-error atoms",
        tree(closed([u("u1", 100), dict(tool("a1", 101), uuid=None), dict(tool("a2", 102), uuid=""),
                     dict(tool("a3", 103), isApiError=True), tool("a4", 104)])))
    add("thinking only: no jump", tree(closed([u("u1", 100), thinking("k1", 101)])))
    add("an empty settle uuid: the reply is \"\", so the jump", tree(closed([u("u1", 100), tool("a1", 101),
                                                                         settle("", 102)])))
    add("a tool_use block without id or name still lands",
        tree(closed([u("u1", 100), asst("a1", 101, None, message={"role": "assistant", "content": [
            {"type": "tool_use"}]})])))
    add("a settle beside a tool use: the settle is the reply, so no jump",
        tree(closed([u("u1", 100), tool("a1", 101), settle("s1", 102)])))
    no_in = {"uuid": "at1"}
    add("attachment trigger: the first user atom with a uuid",
        tree(closed([att("at1", 100), dict(result("p0", 101), uuid=""), result("p1", 102), asst("a1", 103, "x")],
                    trigger=no_in)))
    add("attachment trigger, no user atom: the trigger",
        tree(closed([att("at1", 100), asst("a1", 103, "x")], trigger=no_in)))
    add("attachment trigger, the only user atom has no uuid: the trigger",
        tree(closed([att("at1", 100), dict(result("p0", 101), uuid=None)], trigger=no_in)))
    add("trigger names an attachment not among the atoms: the trigger",
        tree(closed([asst("a1", 103, "x")], trigger={"uuid": "at9"})))
    add("trigger names a non-attachment: the trigger", tree(closed([result("p1", 100), asst("a1", 103, "x")],
                                                                    trigger={"uuid": "a1"})))
    add("the trigger uuid twice: the first match (the attachment) decides",
        tree(closed([att("at1", 100), result("p1", 101), att("at1", 102, type="user")], trigger=no_in)))
    add("the trigger uuid twice: the first match (a user atom) decides",
        tree(closed([att("at1", 100, type="user"), result("p1", 101), att("at1", 102)], trigger=no_in)))
    add("trigger null or empty: no prompt anchor", tree(closed([asst("a1", 100, "x")], trigger=None),
                                                         closed([asst("a2", 200, "y")], trigger={"uuid": ""})))
    sub80, sub79 = pad(80), pad(79)
    add("last text: 80 code points is substantive, 79 is not (88 bytes)",
        tree(closed([u("u1", 100), asst("a1", 101, sub80), asst("a2", 102, sub79)]),
             closed([u("u2", 200), asst("a3", 201, sub79)])))
    add("last text: 79 code points after a strip is still counted unstripped",
        tree(closed([u("u1", 100), asst("a1", 101, " " + sub79)])))
    add("last text: prose across two blocks sums", tree(closed([u("u1", 100), asst("a1", 101, None, message={
        "role": "assistant", "content": [{"type": "text", "text": pad(40)}, {"type": "text", "text": pad(40)}]})])))
    add("last text: a bare-string content counts nothing",
        tree(closed([u("u1", 100), asst("a1", 101, None, message={"role": "assistant", "content": sub80})])))
    add("last text: uuid-less and empty-uuid prose atoms are skipped",
        tree(closed([u("u1", 100), asst("a1", 101, "short"), dict(asst("a2", 102, sub80), uuid=None),
                     dict(asst("a3", 103, sub80), uuid="")])))
    add("last text: API-error and user prose count 0",
        tree(closed([u("u1", 100, sub80), asst("a1", 101, sub80, isApiError=True), asst("a2", 102, "tiny")])))
    add("last text: a shorter later atom is the any, the earlier the substantive",
        tree(closed([u("u1", 100), asst("a1", 101, sub80), asst("a2", 102, "brief")])))
    add("cite: substantive atoms across turns, duplicates once",
        tree(closed([u("u1", 100), asst("a1", 101, sub80)]), closed([u("u2", 200), asst("a1", 201, pad(90)),
                                                                      asst("a3", 202, pad(85))])))
    # the S4.3 review's gap: two distinct substantive atoms in ONE segment, then a short one. Python cites both a1 and
    # a2; a port that cited only the segment's last substantive atom passed every other case (only the fuzz caught it)
    add("cite: two substantive atoms in one segment, then a short one",
        tree(closed([u("u1", 100), asst("a1", 101, pad(85)), asst("a2", 102, pad(90)), asst("a3", 103, "short")])))

    # _feed_seg_fold: repeated keys, raises, seams, echoes
    add("the same prompt in two turns: the later overwrites in the first place",
        tree(closed([u("u1", 100, "continue"), asst("a1", 101, "first")]),
             closed([u("u2", 200, "other"), asst("a2", 201, "second")]),
             closed([u("u3", 300, "continue"), tool("a3", 301)])))
    add("one key three times, two keys between",
        tree(closed([u("u1", 100, "go"), asst("a1", 101, "1")]), closed([u("u2", 200, "x"), asst("a2", 201, "2")]),
             closed([u("u3", 300, "go"), asst("a3", 301, "3")]), closed([u("u4", 400, "y")]),
             open_turn([u("u5", 500, "go"), settle("a5", 501)], end=502)))
    add("the same prompt twice in one turn", tree(closed([u("u1", 100, "again"), asst("a1", 101, "x"),
                                                          u("u2", 110, "again"), tool("a2", 111)])))
    add("a text-less prompt keyed by its uuid repeats with the uuid",
        tree(closed([u("u1", 100, None), asst("a1", 101, "x")]), closed([u("u1", 200, None), asst("a2", 201, "y")])))
    noid = {k: v for k, v in u("u9", 250).items() if k != "session_id"}
    add("a raise in the middle turn: the maps as far as the fold got",
        tree(closed([u("u1", 100), asst("a1", 101, sub80)]), closed([noid, asst("a2", 251, "y")]),
             closed([u("u3", 300, "later"), asst("a3", 301, pad(81))])))
    add("a raise in the first turn: empty maps, the plain time still read",
        tree(closed([noid]), closed([u("u3", 300)])))
    add("a raise in the open turn: the closed maps complete",
        tree(closed([u("u1", 100), asst("a1", 101, "x")]), open_turn([noid, tool("a2", 260)], end=270)))
    add("a raise after a repeated key", tree(closed([u("u1", 100, "same")]), closed([u("u2", 200, "same")]),
                                             closed([{k: v for k, v in asst("a3", 300, "x").items()
                                                      if k != "session_id"}], trigger=None)))
    add("a trigger-less turn without inputs raises KeyError 'trigger'",
        tree(closed([u("u1", 100)]), {k: v for k, v in closed([asst("a2", 200, "x")]).items() if k != "trigger"}))
    w = l2.work_turn()
    add("a seam splits a tail off: the tail has no prompt anchor", tree(w), store(seam(105, [seg_key(w)])))
    w2 = l2.work_turn([asst("a2", 110, sub80), tool("a3", 115)])
    add("a seam tail with substantive prose", tree(closed([u("u0", 50)]), w2), store(seam(105, [seg_key(w2)])))
    w3 = l2.work_turn([tool("a2", 110.5), asst("a3", 120.25, "done")], end=130.5)
    add("a seam tail at a float time", tree(w3), store(seam(105.5, [seg_key(w3)])))
    neg = turn([u("u1", -10), tool("a2", 5)], end=20)
    add("a seam without t that hits raises in the open turn", tree(closed([u("u0", -50, "earlier")]), neg),
        store({"top": TOP, "segs": [seg_key(neg)]}))
    echo = turn([u("u1", 100), u("e1", 104, "echo"), tool("a2", 110)], placedEchoes=["e1"])
    add("placed echoes are dropped before the fold", tree(echo))
    add("float segment times in seg_best", tree(closed([u("u1", 1781100000.5), asst("a1", 1781100001.25, "x")]),
                                                closed([u("u2", -0.0)])))

    # _FeedSegTurns.get over hand-made positions
    gt = closed([u("u1", 100, "a"), u("u2", 110, "b"), u("u3", 120, "a"), asst("a3", 121, "x")])
    ka, kb = seg_key(gt, 0), seg_key(gt, 1)
    raiser = closed([noid])
    gets("get: the segment at the ordinal", tree(gt), [(ka, (0, 2)), (kb, (0, 1))], [ka, kb])
    gets("get: the ordinal holds another key: the turn's last with the key", tree(gt), [(ka, (0, 1))], [ka])
    gets("get: the ordinal holds another key, the key is earlier only", tree(gt), [(kb, (0, 2))], [kb])
    gets("get: an ordinal past the end", tree(gt), [(ka, (0, 7))], [ka])
    gets("get: no segment with the key: the default", tree(gt), [(SID + ":nope", (0, 0))], [SID + ":nope"])
    gets("get: an absent key: the default, no fault", tree(gt), [(ka, (0, 0))], [kb, ABSENT_KEY])
    gets("get: a raising turn faults on every get", tree(gt, raiser), [(ka, (1, 0)), (kb, (1, 0))], [ka, kb, ka])
    gets("get: a turn index past the end faults", tree(gt), [(ka, (3, 0))], [ka])
    gets("get: a cached turn answers twice", tree(gt), [(ka, (0, 0)), (kb, (0, 1))], [ka, kb, ka])
    wst = store(seam(105, [seg_key(w)]))
    tail_k = jd._seg_key(km._segs_seam(w, wst)[1]["id"])
    gets("get: a seam tail's key", tree(w), [(tail_k, (0, 1))], [tail_k], st=wst)
    gets("get: no turns", tree(), [(ka, (0, 0))], [ka])

    # _suspended_after directly
    for name, dt, t in (("t null reads 0", [(0.5, 1)], None), ("t 0", [(0.5, 1)], 0), ("t -0.0", [(0.5, 1)], -0.0),
                        ("t 0 and a negative start", [(-1, 1)], 0), ("t negative, start between", [(-1, 1)], -5),
                        ("start equal int and float", [(110, 1)], 110.0), ("start greater by a hair", [(110.0001, 1)], 110),
                        ("start just below", [(109.9999, 1)], 110), ("no downtime", [], 5),
                        ("the last of several", [(1, 2), (3, 4), (500, 600)], 400),
                        ("an int past 2**53 against its float", [(9007199254740993, 0)], 9007199254740992.0),
                        ("huge float start", [(1e20, 1e21)], 1781100000)):
        susp("suspended: " + name, dt, t)

    # shapes the Rust loader refuses
    add("REFUSE an open turn's message that is a string (_open_turn_progress swallows it, em._stop_reason raises)",
        tree(open_turn([u("u1", 100), dict(tool("a1", 101), message="tool_use")], end=102)))
    add("REFUSE a content that is a dict (Python iterates its keys)",
        tree(open_turn([u("u1", 100), asst("a1", 101, None, message={"role": "assistant", "content": {
            "tool_use": 1}})], end=102)))
    add("REFUSE a restored pre-cut turn with stored prose counts",
        tree(dict(closed([u("u1", 100), asst("a1", 101, sub80)]), pre=True, pcs={"a1": 80},
                  segs=[[SID + ":100:h", "u1", 100, 200, 0, 2]])))
    add("REFUSE a trigger that is a string", tree(closed([u("u1", 100)], trigger="u1")))
    add("REFUSE a downtime entry of three values", tree(working), downtime=[(200, 300, 400)])
    add("REFUSE a downtime start that is null", tree(working), downtime=[(None, 300)])
    add("REFUSE a downtime start that is a bool", tree(working), downtime=[(True, 300)])
    return c


def plain(v):
    return json.loads(json.dumps(v))


def answer_case(km, name, kind, payload):
    case = {"name": name, "kind": kind}
    case.update(plain(payload))
    try:
        if kind == "session":
            ans, _w = session_answer(km, case["tree"], case["store"], case["downtime"])
            case["expect"] = ans
        elif kind == "segturns":
            turns = (case["tree"] or {}).get("turns") or []
            st = km._FeedSegTurns({k: tuple(p) for k, p in case["pos"]}, turns, case["store"])
            f0 = km._SUMMARY_ANCHOR_STATS["fault"]
            out = [[k, k in st, seg_turn_out(turns, st.get(k))] for k in case["keys"]]
            case["expect"] = {"gets": out, "faults": km._SUMMARY_ANCHOR_STATS["fault"] - f0}
        elif kind == "suspended":
            km._downtime[:] = [tuple(x) for x in case["downtime"]]
            try:
                case["expect"] = km._suspended_after(case["t"])
            finally:
                km._downtime[:] = []
        else:
            raise ValueError(kind)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        case["expect"] = raised(e)
    return case


def answer_cases(km, specs):
    cases, cov = [], new_cov()
    for name, kind, payload in specs:
        case = answer_case(km, name, kind, payload)
        if name.startswith("REFUSE ") or (isinstance(case["expect"], dict) and "raises" in case["expect"]):
            pass
        elif kind == "session":
            count(cov, case["expect"])
        elif kind == "segturns":
            cov["segTurnsCases"] += 1
            cov["faults"] += case["expect"]["faults"]
            cov["segTurnHits"] += sum(1 for q in case["expect"]["gets"] if q[2] is not None)
        cases.append(case)
    return cases, cov


def edge_main(out):
    l2 = load_l2()
    l1 = l2.load_l1()
    dw = l1.load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer3-")
    try:
        _root, km, _rec = l1.load_kernel(dw, tmp)
        cases, cov = answer_cases(km, edge_cases(km, l2))
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer3.py --edge-cases",
               "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, indent=1, ensure_ascii=True, allow_nan=False) + "\n")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    n_raise = sum(1 for c in cases if isinstance(c["expect"], dict) and "raises" in c["expect"])
    n_refuse = sum(1 for c in cases if c["name"].startswith("REFUSE "))
    print("edge cases: %d (%s), %d where Python raises, %d the loader must refuse; coverage %s -> %s" % (
        len(cases), ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())), n_raise, n_refuse,
        json.dumps(cov, sort_keys=True), out))
    return 0


# ───────────────────────────── the randomized check (--fuzz) ─────────────────────────────
# Random sessions: layer 2's random turns (dump_layer2.fuzz_turn: atoms of every kind, uuids that repeat or are ""
# or null or absent, non-ASCII and Python-only whitespace, int and float times with ties, every trigger shape, placed
# echoes), decorated with what layer 3 reads: ended flags, trailing idle spans and command atoms (truthy and falsy),
# echo turns and _echo_text, romp-goal-id prompts, human anchors, settle and synthetic replies, bare ends, API errors,
# attachment triggers, prose at the 80-code-point floor, earlier turns repeated (their keys collide), downtime around
# the last activity, and seams aimed at the turns' segments (dump_layer2.fuzz_store). One case in twelve hands
# _FeedSegTurns the walk's positions perturbed (another ordinal, another turn, past the end). Seeded: a failure replays.
GOAL_MARKS = ("<!-- romp-goal-id: s1:g1 -->", "romp-goal-id", "romp-goal-i", "ROMP-GOAL-ID")


def fuzz_decorate_atom(rng, a, l2):
    r = rng.random()
    if r < 0.05:
        a["command"] = rng.choice(("/model", "/clear", {"name": "/x"}, ["/y"], "", 0, None, [], False, True))
    if a.get("type") == "user" and rng.random() < 0.5:
        a["author"] = "human"
    if rng.random() < 0.04:
        a["_echo_text"] = rng.choice(("echo", "", None, 0, 1))
    m = a.get("message")
    if a.get("type") == "user" and isinstance(m, dict) and isinstance(m.get("content"), list) and rng.random() < 0.1:
        m["content"].append({"type": "text", "text": rng.choice(GOAL_MARKS)})
    elif a.get("type") == "user" and isinstance(m, dict) and rng.random() < 0.05:   # a bare string: jd._atom_text
        m["content"] = rng.choice(("", " ")) + "reply " + rng.choice(GOAL_MARKS) + " ok"   # reads "" from it
    if a.get("type") == "user" and rng.random() < 0.05:     # a zero time: the plain time falls to the turn's
        a["t"] = rng.choice((0, 0.0, -0.0))
    if a.get("type") == "assistant" and isinstance(m, dict):
        r = rng.random()
        if r < 0.08:
            m["content"] = [{"type": "text", "text": rng.choice((SETTLE, " " + SETTLE + "\n", SETTLE + "."))}]
        elif r < 0.12:
            m["model"] = rng.choice(("<synthetic>", "claude", None))
        elif r < 0.17:
            m["content"] = []
            m["stop_reason"] = rng.choice(("end_turn", "stop_sequence", "tool_use", None))
        elif r < 0.27:
            m["content"] = [{"type": "text", "text": l2.pad(rng.choice((78, 79, 80, 81, 85)))}]
        elif r < 0.30:
            m["content"] = [{"type": "thinking", "thinking": "hmm"}]
        if rng.random() < 0.06:
            a["isApiError"] = rng.choice((True, False, None))
    return a


def fuzz_session(rng, km, l2, serial):
    if rng.random() < 0.02:
        return None
    n = rng.choice((0, 1, 1, 2, 2, 3, 3, 4, 5, 6))
    turns = []
    for i in range(n):
        if turns and rng.random() < 0.15:                    # an earlier turn again: its segments' keys repeat
            tr = copy.deepcopy(rng.choice(turns))
            twins = rng.random() < 0.4                       # ...at equal times of the other tag (max keeps the first)
            if twins:
                tr["t"] = l2.twin(tr["t"])
            for a in tr["atoms"]:
                if twins:
                    a["t"] = l2.twin(a["t"])
                elif rng.random() < 0.3 and isinstance(a.get("t"), (int, float)) and abs(a["t"]) < 1e300:
                    a["t"] = a["t"] + rng.choice((1, 100, 0.5))
        else:
            tr = l2.fuzz_turn(rng, serial * 10 + i)
            for a in tr["atoms"]:
                fuzz_decorate_atom(rng, a, l2)
        inputs = [a for a in tr["atoms"] if a.get("type") == "user" and a.get("author") is not None]
        if inputs and rng.random() < 0.12:                  # an input again in the same turn: two segments, one key
            again = copy.deepcopy(rng.choice(inputs))
            tr["atoms"].insert(rng.randint(tr["atoms"].index(next(x for x in tr["atoms"] if x is inputs[0])) + 1,
                                           len(tr["atoms"])), again)
        r = rng.random()
        if r < 0.1:
            tr["atoms"].append({"type": "idle", "uuid": None, "t": tr["end"], "end": tr["end"],
                                "session_id": l2.SID})
        elif r < 0.18:
            tr["atoms"].append({"type": "system", "uuid": "cmd-%d-%d" % (serial, i), "t": tr["end"],
                                "session_id": l2.SID, "command": rng.choice(("/model", "", None, 0, "/clear"))})
        if rng.random() < 0.06 and tr["atoms"]:
            k = rng.randrange(len(tr["atoms"]))
            tr["atoms"][k]["type"] = "attachment"
            if isinstance(tr["atoms"][k].get("uuid"), str):
                tr["trigger"] = {"uuid": tr["atoms"][k]["uuid"]}
        r = rng.random()
        if r < 0.06:
            tr["echoTurn"] = rng.choice((True, 1, "x"))
        elif r < 0.09:
            tr["echoTurn"] = rng.choice((False, 0, "", None))
        r = rng.random()
        if r < 0.15:
            tr.pop("ended", None)
        elif r < 0.3:
            tr["ended"] = False
        turns.append(tr)
    if turns and rng.random() < 0.5:
        turns[-1]["ended"] = rng.random() < 0.3
    return {"turns": turns}


def fuzz_downtime(rng, tree):
    if not tree or rng.random() < 0.6:
        return []
    turns = tree["turns"]
    end = turns[-1].get("end", turns[-1].get("t")) if turns else rng.choice((0, 100))
    out = []
    for _ in range(rng.choice((1, 1, 2, 3))):
        base = end if (isinstance(end, (int, float)) and abs(end) < 1e300 and rng.random() < 0.8) else \
            rng.choice((0, -1, 100, 1781100000.5))
        s = rng.choice((base, base + 0.5, base - 0.5, base + 1, base - 1, float(base) if isinstance(base, int) else
                        (int(base) if float(base).is_integer() and abs(base) < 2 ** 63 else base), 0, -0.0))
        out.append([s, s + rng.choice((60, 0.25, 3600.0))])
    return out


def fuzz_store(rng, km, l2, tree):
    turns = (tree or {}).get("turns") or []
    if not turns:
        return rng.choice((None, {}, {"rompUuid": l2.SID}))
    st = l2.fuzz_store(rng, km, rng.choice(turns))
    if st and isinstance(st.get("seams"), list) and len(turns) > 1 and rng.random() < 0.3:
        more = l2.fuzz_store(rng, km, rng.choice(turns))
        if more and isinstance(more.get("seams"), list):
            st["seams"] += more["seams"]
    return st


def fuzz_pos(rng, km, tree, store):
    """The walk's positions over `tree`, perturbed as a held aggregate's might be, and keys to ask for."""
    turns = (tree or {}).get("turns") or []
    km._FEED_SEG_HELD.clear()
    w = km._feed_seg_walk("fsid", None, turns, store)
    pos = [[k, list(p)] for k, p in w.seg_turn._pos.items()]
    keys = [k for k, _p in pos]
    for kp in pos:
        r = rng.random()
        if r < 0.25:
            kp[1][1] = max(0, kp[1][1] + rng.choice((-1, 1, 2, 5)))
        elif r < 0.4 and turns:
            kp[1][0] = rng.randrange(len(turns) + 2)
        elif r < 0.5:
            kp[1] = [rng.randrange(4), rng.randrange(4)]
    if keys and rng.random() < 0.3:
        pos.append([rng.choice(keys) + "x", [0, 0]])
    keys = [k for k, _p in pos] + rng.sample(keys, min(len(keys), 2)) + [ABSENT_KEY]
    return pos, keys


def fuzz_specs(rng, km, l2, n, seed):
    specs = []
    for i in range(n):
        tree = fuzz_session(rng, km, l2, i)
        store = fuzz_store(rng, km, l2, tree)
        name = "fuzz seed %d case %d" % (seed, i)
        if tree and tree["turns"] and rng.random() < 1 / 12:
            pos, keys = fuzz_pos(rng, km, plain(tree), plain(store))
            specs.append((name, "segturns", {"tree": tree, "store": store, "pos": pos, "keys": keys}))
        else:
            specs.append((name, "session", {"tree": tree, "store": store, "downtime": fuzz_downtime(rng, tree)}))
    return specs


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


# romp-spike's edge-set summary line (main.rs, layer3_edge)
RUST_SUMMARY = re.compile(r"^layer3 edge set(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"values\), (\d+) refused at load \(every REFUSE case\), (\d+) differences in (\d+) cases ")


def fuzz_main(n, seed, plant, binary, out):
    """Make `n` cases from `seed`, answer them in Python, and compare with `romp-spike layer3` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    l2 = load_l2()
    l1 = l2.load_l1()
    dw = l1.load_dw()
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer3-fuzz-")
    try:
        t0 = time.monotonic()
        _root, km, _rec = l1.load_kernel(dw, tmp)
        specs = fuzz_specs(random.Random(seed), km, l2, n, seed)
        cases, cov = answer_cases(km, specs)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer3.py --fuzz %d --seed %d" % (n, seed),
               "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "cases": cases}
        path = out or os.path.join(tmp, "layer3_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        nget = sum(c["kind"] == "segturns" for c in cases)
        gets = [c["expect"] for c in cases if c["kind"] == "segturns"]
        reach = {"segturnsFaults": sum(g["faults"] for g in gets),
                 "segturnsHits": sum(1 for g in gets for q in g["gets"] if q[2] is not None),
                 "segturnsDefaults": sum(1 for g in gets for q in g["gets"] if q[2] is None)}
        print("fuzz seed %d: %d cases (%d sessions, %d segturns) made and answered by Python in %.1f s, %.1f MB" % (
            seed, n, n - nget, nget, py_s, os.path.getsize(path) / 1e6))
        print("  python: coverage %s" % json.dumps(cov, sort_keys=True))
        print("  python: reach %s" % json.dumps(reach, sort_keys=True))
        p = subprocess.run([binary, "layer3", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        lines = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, lines) if m]
        for line in lines:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer3: romp-spike layer3 exited %d without its summary line: %s" % (
                p.returncode, p.stderr.strip()[-2000:]), file=sys.stderr)
            return 2
        ncases, loaded, values, refused, diffs, differing = map(int, summary[0].groups())
        if ncases != n or (plant is None and (loaded != n or refused != 0 or diffs != 0 or p.returncode != 0)):
            print("fuzz seed %d: FAILED, %d differences over %d cases (%d loaded, %d refused at load)" % (
                seed, diffs, ncases, loaded, refused))
            return 1
        if plant is None:
            print("fuzz seed %d: OK, %d cases, %d values compared, 0 differences" % (seed, n, values))
            return 0
        print("fuzz seed %d, planted %s: %s, %d differences in %d of %d cases" % (
            seed, plant, "caught" if diffs else "NOT CAUGHT", diffs, differing, n))
        return 0 if diffs else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random cases with romp-spike layer3")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=PLANTS, help="--fuzz against romp-spike layer3 --plant NAME: the check passes "
                    "when the plant is caught")
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
    l2 = load_l2()
    l1 = l2.load_l1()
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
                print("dump_layer3: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            cov, held, size = dump_one(dw, km, d)
            print("%s: %s, held check %s -> %s (%.1f MB)" % (os.path.basename(d), json.dumps(cov, sort_keys=True),
                                                             json.dumps(held, sort_keys=True),
                                                             os.path.join(d, OUT_NAME), size / 1e6))
            if held["agree"] != held["sessions"]:
                print("dump_layer3: the held aggregate changed a walk's answer in %s" % d, file=sys.stderr)
                return 1
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer3: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
