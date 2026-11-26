#!/usr/bin/env python3
"""Python's own answers for layer 6 of the Rust spike's port (rust/notes/s2-design.md section 2, "The body and its
closures"; rust/SPIKE.md step S4.6), for the Rust side to match byte for byte.

Layer 6's answer is the per-session entry itself: the dict kernel._feed_session_entry returns and build_feed memoizes
as a JSON string. The S2 dump already holds Python's real entries for both worlds (`oracle/entries/<sid>.json`), and
`romp-spike layer6 DUMP` compares with those. This tool makes the inputs the worlds do not reach and answers them with
the real kernel.

A case is a small world: a snapshot in the S2 dump's schema (dump_world.py) and its trees. Python answers it with one
replay build_feed over it (dump_world's Replay harness: the recorded helpers answered by sid, _feed_session_key replaced
by the ctx facts it hands the body), with the fold stubbed (layer 7's), every entry captured from the memo put (the
exact string build_feed memoizes, kept with its SHA-1) or, for a session whose derivation raised, the raise: the
replay's DumpError for an answer it never recorded as {"raises": "NotRecorded", "arg": "<helper>(<sid>)"}, anything
else as {"raises": type, "arg": str}. _node_anchor_last and the build memos are cleared first, so a case starts as a
fresh process does, and the anchor table carries across a case's sessions in alive order, as it does across a build.
The harness also answers the two helpers dump_world.py now records (_bg_owner_tops, _bg_service_descs) and the live
backend's `current_ask` (recorded here as `currentAsk`, read by _blocked_placeholder).

    python3 tools/rust-spike/dump_layer6.py DUMP_DIR [DUMP_DIR ...]   # the harness against the dump's own entries
    python3 tools/rust-spike/dump_layer6.py --edge-cases OUT.json     # the synthetic cases (romp-spike layer6 OUT.json)
    python3 tools/rust-spike/dump_layer6.py --fuzz N [--seed S] [--plant NAME] [--bin ROMP_SPIKE] [--out FILE] [--lines]

The dump mode runs this harness over a dump's snapshot and trees and requires every entry to equal the dump's
oracle/entries file byte for byte (the harness's own external check), and reports the body's lines the world runs. A
case named "REFUSE ..." is one the Rust loader must refuse. The edge set reports the lines of the body and its builders
it ran (`lineCoverage`), and so does `--fuzz` with `--lines`. Stdlib only.
"""
import argparse
import copy
import hashlib
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
from unittest import mock

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
EDGE_SCHEMA = "romp-spike-layer6-edge/1"
OUT_NAME = "layer6.json"
NOW = 1781100000
PLANTS = ("anchor-store-order", "fsubmax-last", "mt-or", "land-first-equal", "peers-unsorted", "born-empty-kept")
# the functions layer 6 ports whose lines are reported: the body (its closures count toward it) and its three builders
FUNCS = ("_feed_session_entry", "_card_warn_rows", "_blocked_placeholder", "_awaiting_card")


def load_l5():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer5", os.path.join(HERE, "dump_layer5.py"))
    l5 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l5)
    return l5


def load_kernel(tmp):
    l5 = load_l5()
    l4, dw, km, rec, root, _bc = l5.load_kernel(tmp)
    return l5, l4, dw, km, rec, root


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Lines:
    """sys.settrace line events inside FUNCS (their nested closures and comprehensions count toward them)."""

    def __init__(self, km):
        self.codes, self.labels = {}, []
        for n in FUNCS:
            label = "kernel.%s" % n
            self.labels.append(label)
            stack = [getattr(km, n).__code__]
            while stack:
                c = stack.pop()
                self.codes[id(c)] = (label, c)
                stack.extend(k for k in c.co_consts if hasattr(k, "co_lines"))
        self.hit = {label: set() for label in self.labels}

    def _global(self, frame, event, arg):
        got = self.codes.get(id(frame.f_code))
        if got is None:
            return None
        hit = self.hit[got[0]]

        def local(fr, ev, a):
            if ev == "line":
                hit.add(fr.f_lineno)
            return local
        return local

    def run(self, fn):
        sys.settrace(self._global)
        try:
            return fn()
        finally:
            sys.settrace(None)

    def report(self):
        exe = {label: set() for label in self.labels}
        for label, c in self.codes.values():
            own = {ln for _s, _e, ln in c.co_lines() if ln is not None}
            own.discard(c.co_firstlineno)
            exe[label] |= own
        out, total, ran = {}, 0, 0
        for label in self.labels:
            miss = sorted(exe[label] - self.hit[label])
            out[label] = miss
            total += len(exe[label])
            ran += len(exe[label]) - len(miss)
        return {"lines": total, "ran": ran, "missed": {k: v for k, v in out.items() if v}}


# ───────────────────────────── the harness ─────────────────────────────
class _AskStub:
    """The live backend Sessions.backend_for answers, whose current_ask reads the recorded `currentAsk`."""

    def __init__(self, rp, snap):
        self.rp, self.snap = rp, snap

    def current_ask(self, fsid):
        rec = (self.snap["sessions"].get(str(fsid)) or {}).get("recorded") or {}
        if "currentAsk" not in rec:
            self.rp.missing.append("current_ask(%s)" % fsid)
            raise RuntimeError("no recorded current_ask for %s" % fsid)
        return copy.deepcopy(rec["currentAsk"])


def answer_world(km, dw, snap, trees, lines=None):
    """Python's answer for each alive session of one world, in alive order: [{"sid", "answer", "sha1"}], the answer
    being the entry (decoded from the string build_feed memoized; `sha1` is that string's) or the raise. Also returns
    a note when build_feed itself raised after the derivations (the fold is stubbed, so nothing should)."""
    rp = dw.Replay(km, snap, trees)
    dw.clear_build_memos(km)
    km._node_anchor_last.clear()
    got, raised, saved = {}, {}, []

    def patch(obj, name, fn):
        # a class attribute is saved from the class dict, so a staticmethod goes back as the staticmethod it was
        saved.append((obj, name, obj.__dict__[name] if isinstance(obj, type) else getattr(obj, name)))
        setattr(obj, name, fn)

    def exc_answer(e):
        if isinstance(e, dw.DumpError):
            return {"raises": "NotRecorded", "arg": rp.missing[-1]}
        return {"raises": type(e).__name__, "arg": str(e)}

    rp.install()
    real_key, real_entry = km._feed_session_key, km._feed_session_entry

    def key(s, tm, ctx, prev):
        try:
            return real_key(s, tm, ctx, prev)
        except Exception as e:
            raised[s["sid"]] = exc_answer(e)
            raise

    def entry(s, ctx):
        try:
            return real_entry(s, ctx)
        except Exception as e:
            raised[s["sid"]] = exc_answer(e)
            raise

    patch(km, "_feed_session_key", key)
    patch(km, "_feed_session_entry", entry)
    patch(km, "_feed_memo_put", lambda sid, k, js: got.__setitem__(sid, js))
    patch(km, "_feed_fold_session", lambda *a, **k: (0, 0, False))
    patch(km, "_feed_derive_complain", lambda *a, **k: None)
    stub = _AskStub(rp, snap)
    patch(km.Sessions, "backend_for", staticmethod(lambda sid: stub))
    note = None
    try:
        with mock.patch("time.time", return_value=float(NOW)):
            km._STARTED = float(NOW)
            fn = lambda: km.build_feed(NOW, km._live_map())
            try:
                lines.run(fn) if lines is not None else fn()
            except Exception as e:                     # noqa: BLE001 (after the loop: the entries are already captured)
                note = "build_feed raised %s: %s" % (type(e).__name__, e)
    finally:
        for obj, name, v in reversed(saved):
            setattr(obj, name, v)
        rp.uninstall()
    out = []
    for r in snap["alive"]:
        sid = r["sid"]
        if sid in raised:
            out.append({"sid": sid, "answer": raised[sid]})
        elif sid in got:
            out.append({"sid": sid, "answer": json.loads(got[sid]), "sha1": sha1(got[sid])})
        else:
            raise SystemExit("dump_layer6: %s neither derived nor raised (the harness missed a road)" % sid)
    return out, got, note


def dump_one(dw, km, d):
    """The harness over a dump's own inputs: every entry must equal oracle/entries/<sid>.json byte for byte."""
    snap, trees = dw.load_dump(d)
    lines = Lines(km)
    _out, got, note = answer_world(km, dw, snap, trees, lines)
    same, differ = 0, []
    for r in snap["alive"]:
        with open(os.path.join(d, "oracle", "entries", r["sid"] + ".json"), encoding="utf-8") as f:
            want = f.read()
        if got.get(r["sid"]) == want:
            same += 1
        else:
            differ.append(r["sid"])
    lc = lines.report()
    doc = {"schema": "romp-spike-layer6/1", "world": snap["world"]["name"], "python": platform.python_version(),
           "harnessAgrees": not differ and note is None, "entriesSame": same, "entriesDiffer": differ,
           "buildNote": note, "lineCoverage": lc}
    with open(os.path.join(d, OUT_NAME), "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return doc


def main_dumps(dumps):
    first = os.path.realpath(dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        _l5, _l4, dw, km, rec, root = load_kernel(work)
        rc = 0
        for d in dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer6: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            doc = dump_one(dw, km, d)
            lc = doc["lineCoverage"]
            print("%s: the harness's entries equal oracle/entries on %d of %d sessions%s; body lines run %d of %d -> %s"
                  % (os.path.basename(d), doc["entriesSame"], doc["entriesSame"] + len(doc["entriesDiffer"]),
                     "" if doc["harnessAgrees"] else " (DIFFER: %s %s)" % (doc["entriesDiffer"], doc["buildNote"]),
                     lc["ran"], lc["lines"], os.path.join(d, OUT_NAME)))
            rc = rc or (0 if doc["harnessAgrees"] else 1)
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer6: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
        return rc
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)


# ───────────────────────────── the worlds: builders ─────────────────────────────
SIDS = ["eeeeeeee-0000-0000-0000-%012d" % (i + 1) for i in range(4)]
PEER = "ffffffff-0000-0000-0000-000000000009"    # a session the registry knows that is not alive (a peer)
PEER2 = "ffffffff-0000-0000-0000-000000000008"   # one it does not know
PEER3 = "ffffffff-0000-0000-0000-000000000007"   # another the registry names, under the same name as PEER
COLOR = "#4a9eff"
ABSENT = object()
SUB1 = "The cache invalidation bug is fixed: the loader now rebuilds the index on every write path we have."
SUB2 = "Recap: the search suite passes again, and the naïve cache is gone from every code path in the loader."
SUB3 = "Status: the migration is halfway done; the remaining tables need the new key format before we cut over."
REC0 = {"closerPending": False, "interrupting": False, "sessionRetrying": None,
        "sessionAwaiting": {"idle=true": None, "idle=false": None}, "bgLive": [], "apiError": None,
        "interruptedBy": False, "peerAnswered": [0, {}], "healSessionTops": {}, "awaitingTaskDescs": [],
        "warmWanted": False, "capSwitchOffer": None}


def u(uuid, t, text="please fix the cache", author="human", s=0, **kw):
    a = {"type": "user", "uuid": uuid, "t": t, "session_id": SIDS[s], "author": author,
         "message": {"role": "user", "content": [{"type": "text", "text": text}] if text is not None else []}}
    a.update(kw)
    return a


def asst(uuid, t, text=None, tools=0, s=0, **kw):
    content = ([{"type": "text", "text": text}] if text is not None else []) + [
        {"type": "tool_use", "id": "tu-%s-%d" % (uuid, i), "name": "Bash", "input": {}} for i in range(tools)]
    a = {"type": "assistant", "uuid": uuid, "t": t, "session_id": SIDS[s],
         "message": {"role": "assistant", "content": content, "stop_reason": "tool_use" if tools else "end_turn"}}
    a.update(kw)
    return a


def tool(uuid, t, s=0, **kw):
    return asst(uuid, t, tools=1, s=s, **kw)


def idle(t, end, s=0):
    return {"type": "idle", "uuid": None, "t": t, "end": end, "session_id": SIDS[s]}


def turn(atoms, ended=True, trigger="first", t=None, end=None, **kw):
    if trigger == "first":
        trigger = {"uuid": atoms[0]["uuid"]} if atoms else None
    tr = {"atoms": atoms, "t": atoms[0]["t"] if t is None and atoms else (t if t is not None else 100),
          "end": end if end is not None else ((atoms[-1]["t"] + 5) if atoms else 105), "ended": ended,
          "id": "edge-turn", "trigger": trigger}
    tr.update(kw)
    return tr


def tree(*turns):
    return {"turns": list(turns)}


def N(nid, t=100, text="", **kw):
    """A node: id, text (default "goal <nid>"; ABSENT drops the key) and t (ABSENT drops it)."""
    d = {"id": nid, "__key__": nid}
    if text is not ABSENT:
        d["text"] = text or ("goal " + nid)
    if t is not ABSENT:
        d["t"] = t
    for k, v in kw.items():
        if v is not ABSENT:
            d[k] = v
    return d


def ST(*nodes, status=None, **kw):
    st = {"rompUuid": "r", "nodes": {n.get("__key__", n["id"]): {k: v for k, v in n.items() if k != "__key__"}
                                     for n in nodes}, "status": status or {}}
    st.update(kw)
    return st


def REC(drop=(), **kw):
    r = copy.deepcopy(REC0)
    r.update(kw)
    for k in drop:
        r.pop(k, None)
    return r


def S(i=0, tr=None, st=None, rec=None, flags=None, names="", captions=None, leaf_ok=True, live="", name="",
      peer_store=ABSENT):
    """One alive session's facts. live: a live-map row (default idle), or None for a session not live."""
    nm = name or "s%03d" % (i + 1)
    return {"i": i, "sid": SIDS[i], "name": nm, "tree": tr, "store": ST() if st is None else st,
            "rec": REC() if rec is None else rec, "flags": flags,
            "names": [nm, "/TESTDIR/work/x", COLOR] if names == "" else names,
            "captions": {} if captions is None else captions, "leafOk": leaf_ok,
            "live": {"state": "idle", "since": NOW - 100} if live == "" else live}


def W(*sessions, board=None, peers=None):
    """A world (snapshot, trees) from session facts in alive order."""
    b = {"cleared": [], "jactive": [], "jauth": {}, "wmap": {}, "stalls": {}, "nudgeRecords": {}, "nudgeTimes": {},
         "debug": False, "debugRows": None, "notifyCards": {}, "downtime": []}
    b.update(board or {})
    snap = {"schema": "romp-spike-snapshot/1",
            "world": {"name": "edge", "now": NOW, "mintClock": NOW - 1800, "colormap": None, "hostName": "TESTHOST"},
            "alive": [], "liveMap": {}, "board": b, "sessions": {},
            "peers": peers if peers is not None else {PEER: {"names": ["peer9", "/TESTDIR/work/p", "#ff8800"],
                                                              "flags": None, "store": None}},
            "frameKeys": [], "frame": {}}
    trees = {}
    for k, s in enumerate(sessions):
        sid = s["sid"]
        snap["alive"].append({"sid": sid, "name": s["name"], "anchor": None, "mtime": float(NOW - 60 - k),
                              "tree": ("trees/%s.json" % sid) if s["tree"] is not None else None,
                              "transcript": "transcripts/%s.jsonl" % sid})
        if s["live"] is not None:
            snap["liveMap"][sid] = s["live"]
        snap["sessions"][sid] = {"names": s["names"], "flags": s["flags"], "leafOk": s["leafOk"],
                                 "store": s["store"], "captions": s["captions"], "recorded": s["rec"]}
        trees[sid] = s["tree"]
    return snap, trees


def segids(km, tr, st=None):
    return [g["id"] for t in tr["turns"] for g in km._segs_seam(t, st)]


def edge_cases(km):
    """(name, snapshot, trees)."""
    c = []

    def add(name, w):
        c.append((name, w[0], w[1]))

    # two turns: a closed one with a long reply, an open one with tools (s001 works while live)
    T_open = tree(turn([u("u1", 100, "fix the cache"), asst("a1", 101, SUB1), tool("a2", 102),
                        asst("a3", 103, "Short note.")]),
                  turn([u("u2", 200, "now the index"), tool("b1", 201), asst("b2", 202, SUB2)], ended=False))
    T_idle = tree(turn([u("u1", 100, "fix the cache"), asst("a1", 101, SUB1), tool("a2", 102),
                        asst("a3", 103, "Short note.")]),
                  turn([u("u2", 200, "now the index"), tool("b1", 201), asst("b2", 202, SUB2)]))
    k1, k2 = segids(km, T_idle)
    def g(nid, **kw):
        kw.setdefault("trail", [k1])
        return N(nid, **kw)

    # ── the session-level reads
    add("plain: a working top on an open turn, live", W(S(tr=T_open, st=ST(g("g1")))))
    add("plain: the same session idle", W(S(tr=T_idle, st=ST(g("g1")))))
    add("hide: hideFromFeed is a null entry", W(S(tr=T_idle, st=ST(g("g1")), flags={"hideFromFeed": True})))
    add("hide: a falsy hideFromFeed shows", W(S(tr=T_idle, st=ST(g("g1")), flags={"hideFromFeed": 0, "x": 1})))
    w = W(S(tr=T_idle, st=ST(g("g1")), flags=None))
    w[0]["peers"][SIDS[0]] = {"names": None, "flags": {"hideFromFeed": True}, "store": None}
    add("hide: the session's flags are null and its peer row hides it (the key sees no hide, the body does)", w)
    add("cold: no tree, leaf ok, warm wanted", W(S(tr=None, st=ST(N("g1")), rec=REC(warmWanted=True))))
    add("cold: no tree, warm not wanted", W(S(tr=None, st=ST(N("g1")), rec=REC(warmWanted=0))))
    add("cold: no tree, the leaf did not stat: warm never asked",
        W(S(tr=None, st=ST(N("g1")), leaf_ok=False, rec=REC(drop=("warmWanted",)))))
    add("cold: unrecorded warm answer raises", W(S(tr=None, st=ST(N("g1")), rec=REC(drop=("warmWanted",)))))
    add("working: not live still works by the tree", W(S(tr=T_open, st=ST(g("g1")), live=None)))
    add("downtime: a suspension after the last activity stops the working dot",
        W(S(tr=T_open, st=ST(g("g1"))), board={"downtime": [[203, 260]]}))
    add("downtime: a suspension before the last activity does not",
        W(S(tr=T_open, st=ST(g("g1"))), board={"downtime": [[150, 160.5]]}))
    for v in (True, "spend", 0, None):
        add("interrupted: the recorded answer %r on an idle working card" % (v,),
            W(S(tr=T_idle, st=ST(g("g1")), rec=REC(interruptedBy=v))))
    add("interrupted: unrecorded reads False (inside a try)",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("interruptedBy",)))))
    add("interrupting: in flight beats interrupted",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(interrupting=True, interruptedBy=True))))
    add("interrupted: a blocked card whose last block is the interrupt",
        W(S(tr=T_idle, st=ST(g("g1", blocked=True, log=[{"kind": "block", "src": "interrupt", "ev_t": 150}]),
                            status={"g1": "blocked"}), rec=REC(interrupting=True, interruptedBy=True))))
    add("interrupted: a blocked card whose last block is the planner's",
        W(S(tr=T_idle, st=ST(g("g1", blocked=True, log=[{"kind": "block", "src": "interrupt", "ev_t": 150},
                                                         {"kind": "block", "src": "planner", "ev_t": 160}]),
                            status={"g1": "blocked"}), rec=REC(interruptedBy="spend"))))
    add("interrupting: unrecorded raises in the key", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("interrupting",)))))
    add("interrupting: unrecorded but hidden: the key never asks",
        W(S(tr=T_idle, st=ST(g("g1")), flags={"hideFromFeed": True}, rec=REC(drop=("interrupting",)))))
    add("retrying: the chip on a working card",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=90), status={"g2": "completed"}),
            rec=REC(sessionRetrying={"since": 150, "count": 3}))))
    add("retrying: unrecorded raises", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("sessionRetrying",)))))
    add("judging: a judge in flight", W(S(tr=T_idle, st=ST(g("g1"))), board={"jactive": [SIDS[0]]}))
    add("judging: the closer pending on a live idle session", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(closerPending=True))))
    add("judging: closer unrecorded raises in the key",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("closerPending",)))))
    add("judging: closer unrecorded, not asked when a judge is in flight",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("closerPending",))), board={"jactive": [SIDS[0]]}))
    add("judging: closer unrecorded, not asked when not live",
        W(S(tr=T_idle, st=ST(g("g1")), live=None, rec=REC(drop=("closerPending",)))))
    add("store: a store that could not be read renders nothing",
        W(S(tr=T_open, st=None, rec=REC()) | {"store": None}))
    add("heal: unrecorded raises", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("healSessionTops",)))))
    add("awaiting: unrecorded answer raises", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(sessionAwaiting={}))))
    add("awaiting: the open turn asks idle=false", W(S(tr=T_open, st=ST(g("g1")),
                                                     rec=REC(sessionAwaiting={"idle=true": None}))))
    add("awaiting: an answer without why raises KeyError",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(sessionAwaiting={"idle=true": {"kind": "agents"}}))))
    add("awaiting: an answer without kind raises KeyError",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(sessionAwaiting={"idle=true": {"why": "x"}}))))
    add("awaiting: an empty answer is no answer", W(S(tr=T_idle, st=ST(g("g1")),
                                                    rec=REC(sessionAwaiting={"idle=true": {}}))))
    aw = {"why": "waiting on 2 agents", "kind": "agents", "since": 150.5, "count": 2,
          "peers": [{"name": "x", "sid": PEER2}, PEER, {"name": "y"}, "", {"sid": ""}],
          "items": [{"kind": "agents", "id": "a1", "label": "one", "since": 150}]}
    add("awaiting: the session's signal floors a working top", W(S(tr=T_idle, st=ST(g("g1")),
                                                                    rec=REC(sessionAwaiting={"idle=true": aw}))))
    for cnt in (True, False, 0, -1, 2.5, "2", None):
        add("awaiting: a count of %r" % (cnt,), W(S(tr=T_idle, st=ST(g("g1")),
                                                     rec=REC(sessionAwaiting={"idle=true": dict(aw, count=cnt)}))))
    add("awaiting: a falsy why does not floor, the kind is still read",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(sessionAwaiting={"idle=true": dict(aw, why="")}))))
    add("awaiting: items absent, peers absent", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(
        sessionAwaiting={"idle=true": {"why": "w", "kind": None}}))))
    add("awaiting: the tasks unrecorded raise before the tree is flattened (the table keeps nothing of it)",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup")),
            rec=REC(sessionAwaiting={"idle=true": aw}, drop=("awaitingTaskDescs",))),
          S(1, tr=None, st=ST(N("g1", trail=[k1], id="dup")))))
    add("awaiting: the tasks recorded, the same pair writes the table for the next session",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup")),
            rec=REC(sessionAwaiting={"idle=true": aw}, awaitingTaskDescs=["npm test"])),
          S(1, tr=None, st=ST(N("g1", trail=[k1], id="dup")))))

    # ── heal and hidden
    heal = {"g2": ["g1", {"why": "the planner started it", "parentText": "the host ask"}]}
    add("heal: a machine-rooted top nests under its host", W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110)),
                                                              rec=REC(healSessionTops=heal))))
    add("heal: entries for nodes the store lacks still count",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(healSessionTops={"zz": ["g1", {}], "yy": [None, {"hidden": 1}],
                                                                "xx": ["", {"why": "w"}]}))))
    add("hidden: a skill-rooted top is hidden", W(S(tr=T_idle, st=ST(g("g1"), g("g2")),
                                                   rec=REC(healSessionTops={"g2": [None, {"hidden": True}]}))))
    live_perm = {"state": "permission", "since": NOW - 30}
    add("un-nest: a permission floor on a healed top brings it back as a card",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110), g("g3", t=120, parentId="g2"), lastNode="g3"),
            rec=REC(healSessionTops=heal), live=live_perm)))
    add("un-nest: the api floor on a hidden top brings it back, its born record without hidden",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110), lastNode="g2"),
            rec=REC(healSessionTops={"g2": [None, {"hidden": True, "why": "skill"}]},
                    apiError={"status": 500, "text": "overloaded"}))))
    add("un-nest: a hidden top whose record is only hidden comes back with no face",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110), lastNode="g2"),
            rec=REC(healSessionTops={"g2": [None, {"hidden": True}]}, apiError={"status": 500}))))
    add("un-nest: the same healed top under the permission and the api floors",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110), lastNode="g2"),
            rec=REC(healSessionTops=heal, apiError={"tooLong": True}), live=live_perm)))
    add("un-nest: a floor on a top that is not healed changes nothing",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=110), lastNode="g1"), rec=REC(healSessionTops=heal), live=live_perm)))
    add("born: a step whose parent is gone is a root and says so",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", parentId="gone", born={"why": "started it", "parentText": "old ask"})))))
    add("born: an empty born dict on a node is null, its face is null",
        W(S(tr=T_idle, st=ST(g("g1", born={}), g("g2", parentId="g1", born={})))))
    add("born: a node's own born dict beats the heal's record (in the row and the face)",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", parentId="gone", born={"why": "its own", "parentText": "own ask"})),
            rec=REC(healSessionTops={"g2": [None, {"why": "the heal's", "parentText": "healed ask"}]}))))
    add("born: a null born falls to the heal's record",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", born=None)), rec=REC(healSessionTops={"g2": ["g1", {"why": "w"}]}))))
    add("born: an empty heal record is null in the row",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2")), rec=REC(healSessionTops={"g2": ["g1", {}]}))))

    # ── the floors
    for st_ in ("permission", "picker", "working", None):
        add("perm: the live state %r with a focus" % st_,
            W(S(tr=T_idle, st=ST(g("g1"), g("g2", parentId="g1"), lastNode="g2"),
                live={"state": st_, "since": NOW - 5})))
    add("perm: an empty live row is falsy (no state read)", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), live={})))
    add("perm: the focus top is completed: no floor, the blocked placeholder",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True), lastNode="g1", status={"g1": "completed"}), live=live_perm)))
    add("perm: no goals at all: the blocked placeholder from the live prompt",
        W(S(tr=tree(turn([u("u1", 100, "may I run rm?")], ended=False)), st=ST(), live=live_perm)))
    aflags = [{"tooLong": True}, {"spendLimit": True}, {"modelLimit": 1}, {"authErr": True}, {"refusal": True},
              {"noRetry": True, "text": "the turn was cut by a restart"}, {"noRetry": True}, {"noRetry": True, "text": ""},
              {}, {"status": 529, "text": "overloaded", "tooLong": 0}, {"authErr": True, "spendLimit": True}]
    for af in aflags:
        add("api: the error %s" % json.dumps(af, sort_keys=True),
            W(S(tr=T_idle, st=ST(g("g1"), g("g2", parentId="g1"), lastNode="g2"),
                rec=REC(apiError=dict({"status": 400, "text": "bad"}, **af)))))
    add("api: a refused stored login names its label",
        W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(apiError={"authErr": True, "text": "401"}),
            live={"state": "idle", "authLogin": "L1", "authLoginLive": "L1", "authLabel": "work login"})))
    add("api: a stored login the CLI did not use is not named",
        W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(apiError={"authErr": True}),
            live={"state": "idle", "authLogin": "L1", "authLoginLive": "L2", "authLabel": "work"})))
    add("api: the capacity offer rides the badge",
        W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(apiError={"modelLimit": True},
                                                            capSwitchOffer={"to": "key", "why": "capped"}))))
    add("api: a falsy offer is left out", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"),
                                             rec=REC(apiError={"status": 1}, capSwitchOffer={}))))
    add("api: an unrecorded offer raises", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"),
                                              rec=REC(apiError={"status": 1}, drop=("capSwitchOffer",)))))
    add("api: a truthy non-dict error raises AttributeError",
        W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(apiError="boom"))))
    add("api: a falsy error is no error", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(apiError=0))))
    add("api: unrecorded raises", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("apiError",)))))
    add("api: the awaiting signal means the error is never read",
        W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), rec=REC(drop=("apiError",),
                                                            sessionAwaiting={"idle=true": aw}))))
    add("api: no focus: the error floors nothing but the usage read stands",
        W(S(tr=T_idle, st=ST(g("g1")), rec=REC(apiError={"tooLong": True}))))
    jl = {"mode": "login", "t": 150, "note": "401 from the judge", "loginLabel": "judge login"}
    for name_, je in (("key mode", {"mode": "key", "t": 150, "note": "bad key"}), ("a stored login", jl),
                      ("neither", {"mode": "login", "t": 150}), ("a falsy label", dict(jl, loginLabel="")),
                      ("a falsy entry", {}), ("a null note", dict(jl, note=None))):
        add("jauth: %s" % name_, W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1")), board={"jauth": {SIDS[0]: je}}))
    add("jauth: yields to the permission floor", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1"), live=live_perm),
                                                  board={"jauth": {SIDS[0]: jl}}))
    add("jauth: another session's latch", W(S(tr=T_idle, st=ST(g("g1"), lastNode="g1")),
                                            board={"jauth": {SIDS[1]: jl}}))
    rows = [{"fsid": SIDS[0], "goal": "g1", "kind": "planner", "n": i} for i in range(23)] + [
        {"fsid": SIDS[0], "goal": ["zz", "g2"], "kind": "closer"}, {"fsid": SIDS[1], "goal": "g1"},
        {"fsid": SIDS[0], "seg": "kk", "kind": "grouper"}, {"fsid": SIDS[0], "seg": "kd"}, {"fsid": SIDS[0], "goal": 5},
        {"fsid": SIDS[0], "seg": "kn"}, {"fsid": SIDS[0], "seg": ""}]
    add("debug: rows join by goal, by a goal list, and by a placement under each phase, the last 20",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", parentId="g1"), g("g3", t=90),
                             placements={"kk#live": "g2", "kd#d": "g1", "kn": None, "kn#p": "zz"})),
          board={"debug": True, "debugRows": rows}))
    add("debug: a few rows, one card without any", W(S(tr=T_idle, st=ST(g("g1"), g("g3", t=90),
                                                                       placements={"kk": "g3"})),
                                                     board={"debug": True, "debugRows": rows[-6:]}))
    add("debug: off, the rows are never read", W(S(tr=T_idle, st=ST(g("g1"))),
                                                board={"debug": False, "debugRows": rows}))
    add("debug: on with no rows", W(S(tr=T_idle, st=ST(g("g1"))), board={"debug": True, "debugRows": []}))
    # ── the card loop
    add("cleared: a top whose status is cleared, and one in the build's cleared set, are skipped",
        W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=90), g("g3", t=80), status={"g1": "cleared"})),
          board={"cleared": ["g2"]}))
    add("cleared: a cleared top never writes the anchor table (the next session's cold lookup finds nothing)",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup"), status={"g1": "cleared"})),
          S(1, tr=None, st=ST(N("g1", trail=[k1], id="dup", summaryAnchor="sa-fallback")))))
    def hand(nid, peer=PEER, **kw):
        kw.setdefault("handoff", {"peer": peer, "msgId": "m1"})
        return N(nid, **kw)
    add("delegation: a top whose every leaf is a handoff is coordination, not a card",
        W(S(tr=T_idle, st=ST(g("g1"), hand("h1", parentId="g1", text="↪ delegated to peer9: the index")))))
    add("delegation: a pure-delegation top that a floor keys on keeps its card",
        W(S(tr=T_idle, st=ST(g("g1"), hand("h1", parentId="g1"), lastNode="h1"), live=live_perm)))
    add("delegation: a pure-delegation top never writes the anchor table",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup"), hand("h1", parentId="g1"))),
          S(1, tr=None, st=ST(N("g1", trail=[k1], id="dup")))))
    add("delegation: the dictated ask keeps its card (a human prompt uuid)",
        W(S(tr=T_idle, st=ST(g("g1", promptUuid="u1"), hand("h1", parentId="g1")))))
    add("delegation: every open leaf a handoff, the top open: awaiting delegated to the peers, names sorted",
        W(S(tr=T_idle, st=ST(g("g1", promptUuid="u1"), hand("h1", parentId="g1", t=130), hand("h2", PEER2, parentId="g1", t=120.5),
                             hand("h3", parentId="g1", t=130.0), g("g4", parentId="g1", nodeComplete=True)))))
    add("delegation: two peers the registry names alike are one name",
        W(S(tr=T_idle, st=ST(g("g1", promptUuid="u1"), hand("h1", parentId="g1", t=130), hand("h2", PEER3, parentId="g1", t=120))),
          peers={PEER: {"names": ["peer9", "/TESTDIR/work/p", "#ff8800"], "flags": None, "store": None},
                 PEER3: {"names": ["peer9", "/TESTDIR/work/q", "#00aa00"], "flags": None, "store": None}}))
    add("delegation: a handoff with no peer names nobody: 'a peer'",
        W(S(tr=T_idle, st=ST(g("g1", promptUuid="u1"), hand("h1", "", parentId="g1", t=0)))))
    add("delegation: handoffs without times: no since", W(S(tr=T_idle, st=ST(
        g("g1", promptUuid="u1"), hand("h1", parentId="g1", t=0), hand("h2", parentId="g1", t=0.0)))))
    wq = {"peer": "peer9", "host": "", "since": 150, "kind": "question"}
    add("wait: a working card that predates the question wears waitingOn",
        W(S(tr=T_idle, st=ST(g("g1", t=150), g("g2", t=151))), board={"wmap": {SIDS[0]: wq}}))
    add("wait: another session's wait", W(S(tr=T_idle, st=ST(g("g1", t=150))), board={"wmap": {SIDS[1]: wq}}))
    add("wait: a node without t under the wait graph raises KeyError",
        W(S(tr=T_idle, st=ST(g("g1", t=ABSENT))), board={"wmap": {SIDS[0]: wq}}))
    add("wait: an int t against a float since, equal", W(S(tr=T_idle, st=ST(g("g1", t=150))),
                                                        board={"wmap": {SIDS[0]: dict(wq, since=150.0)}}))
    blk = lambda nid, ev, **kw: g(nid, blocked=True, log=[{"kind": "block", "src": "planner", "ev_t": ev}], **kw)
    add("wait: a blocked card yields when the question is at or after its block",
        W(S(tr=T_idle, st=ST(blk("g1", 140, t=100), status={"g1": "blocked"})), board={"wmap": {SIDS[0]: wq}}))
    add("wait: a blocked card whose block is newer than the question stays blocked (no waitingOn off working)",
        W(S(tr=T_idle, st=ST(blk("g1", 160, t=100, blockCheckT=250), status={"g1": "blocked"})),
          board={"wmap": {SIDS[0]: wq}}))
    add("wait: a completed card that predates the question wears no waitingOn",
        W(S(tr=T_idle, st=ST(g("g1", t=100, nodeComplete=True), status={"g1": "completed"})),
          board={"wmap": {SIDS[0]: wq}}))
    add("wait: the raise on a node without t comes before its flatten (the next session's cold lookup finds nothing)",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup", t=ABSENT))), S(1, tr=None, st=ST(N("g1", trail=["x"], id="dup"))),
          board={"wmap": {SIDS[0]: wq}}))
    add("wait: a blocked card with a node lacking a block row (no time) does not yield",
        W(S(tr=T_idle, st=ST(g("g1", blocked=True, t=100), status={"g1": "blocked"})), board={"wmap": {SIDS[0]: wq}}))
    live_tasks = [{"tid": "tu-a2-0", "desc": "npm test", "t": 170}]
    for name_, owned in (("newer than the block", {"g1": {"since": 170, "descs": ["npm test", "x"]}}),
                         ("at the block's time", {"g1": {"since": 140, "descs": []}}),
                         ("older than the block", {"g1": {"since": 130, "descs": ["old"]}}),
                         ("owned by another top", {"g9": {"since": 170, "descs": ["npm test"]}})):
        add("owned: a blocked card and a live task %s" % name_,
            W(S(tr=T_idle, st=ST(blk("g1", 140), status={"g1": "blocked"}),
                rec=REC(bgLive=live_tasks, bgOwnerTops=owned, bgServiceDescs=["dev server"]))))
    add("owned: a blocked card whose block time is unknown does not yield",
        W(S(tr=T_idle, st=ST(g("g1", blocked=True), status={"g1": "blocked"}),
            rec=REC(bgLive=live_tasks, bgOwnerTops={"g1": {"since": 170, "descs": []}}, bgServiceDescs=[]))))
    add("bg: the service chip on a live session", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(
        bgLive=live_tasks, bgOwnerTops={}, bgServiceDescs=["dev server", "watcher"]))))
    add("bg: not live: no chip and no service read", W(S(tr=T_idle, st=ST(g("g1")), live=None, rec=REC(
        bgLive=live_tasks, bgOwnerTops={}))))
    add("bg: unrecorded owner tops raise", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(bgLive=live_tasks))))
    add("bg: unrecorded live tasks raise", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("bgLive",)))))
    add("bg: working: live tasks never read", W(S(tr=T_open, st=ST(g("g1")), rec=REC(drop=("bgLive",)))))
    stamp = dict(awaitingWhy="waiting on the build", awaitingAt=160, awaitingKind="commands")
    add("stamp: the judge's awaiting stamp floors a working top", W(S(tr=T_idle, st=ST(g("g1", **stamp)))))
    add("stamp: a peer stamp names its peers, sorted, counted",
        W(S(tr=T_idle, st=ST(g("g1", awaitingWhy="waiting on peers", awaitingAt=160, awaitingKind="peer",
                                awaitingPeers=[PEER2, PEER, "peer:" + PEER])))))
    add("stamp: superseded by a peer's answer",
        W(S(tr=T_idle, st=ST(g("g1", awaitingWhy="waiting on peer", awaitingAt=160, awaitingKind="peer",
                                awaitingPeers=[PEER])), rec=REC(peerAnswered=[170, {PEER: 170}]))))
    add("stamp: a falsy anchor: no since", W(S(tr=T_idle, st=ST(g("g1", awaitingWhy="w", awaitingAt=0)))))
    add("stamp: unrecorded answers raise on a working top", W(S(tr=T_idle, st=ST(g("g1")), rec=REC(drop=("peerAnswered",)))))
    add("stamp: unrecorded answers are never read for a blocked top",
        W(S(tr=T_idle, st=ST(blk("g1", 140), status={"g1": "blocked"}), rec=REC(drop=("peerAnswered",)))))
    add("tracked: the sender's card names the tracked recipients",
        W(S(tr=T_idle, st=ST(g("g1"), hand("h1", parentId="g1", handoff={"peer": PEER, "tracked": True}),
                             g("g2", parentId="g1")))))
    add("tracked: a completed or cleared tracker is not tracked",
        W(S(tr=T_idle, st=ST(g("g1"), N("h1", parentId="g1", handoff={"peer": PEER, "tracked": 1}, nodeComplete=True),
                             N("h2", parentId="g1", handoff={"peer": PEER2, "tracked": 1}, cleared=True),
                             g("g2", parentId="g1")))))
    pst = ST(N(PEER + ":g1", t=50), N(PEER + ":g2", t=50, nodeComplete=True))
    peers_w = {PEER: {"names": ["peer9", "/TESTDIR/work/p", "#ff8800"], "flags": None, "store": pst},
               PEER2: {"names": None, "flags": None, "store": None}}
    for name_, o in (("a registry sender, its goal open, tracked", {"peer": PEER, "goalId": PEER + ":g1", "tracked": True,
                                                                     "peerName": "old", "peerHost": "far"}),
                     ("its goal complete", {"peer": PEER, "goalId": PEER + ":g2", "tracked": True}),
                     ("its goal cleared in this build", {"peer": PEER, "goalId": PEER + ":g1", "tracked": True}),
                     ("a sender the registry misses, named at plant time", {"peer": PEER2, "goalId": "x",
                                                                           "peerName": "remote", "peerHost": "far"}),
                     ("a sender with no name at all", {"peer": PEER2, "peerHost": ""}),
                     ("a sender whose store is not in the dump", {"peer": "eeeeeeee-9999", "goalId": "z"}),
                     ("no peer", {"goalId": "z"})):
        add("origin: %s" % name_, W(S(tr=T_idle, st=ST(g("g1", origin=o))), peers=peers_w,
                                    board={"cleared": [PEER + ":g1"]} if "cleared" in name_ else None))
    add("origin: a tracked satellite on a needs-you card is not hidden",
        W(S(tr=T_idle, st=ST(blk("g1", 140, blockCheckT=250, origin={"peer": PEER, "goalId": PEER + ":g1", "tracked": True}),
                             status={"g1": "blocked"})), peers=peers_w))
    add("handoffTo: a top-level handoff card titles the work",
        W(S(tr=T_idle, st=ST(hand("h1", text="↪ delegated to peer9: rebuild the index", promptUuid="u1")))))
    add("handoffTo: a registry miss takes the label's host and name",
        W(S(tr=T_idle, st=ST(hand("h1", PEER2, text="↪ delegated to far:remote: the work", promptUuid="u1")))))
    # card times
    done = lambda nid, ev, **kw: g(nid, nodeComplete=True, log=[{"kind": "done", "src": "closer", "ev_t": ev}], **kw)
    add("time: a completed card dates from its settle or its newest done descendant, rolled-down ones skipped",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True, settledAt=150, mt=300), done("g2", 170, parentId="g1"),
                             done("g3", 180, parentId="g1", rolledUp=True), done("g4", 170.0, parentId="g1"),
                             status={"g1": "completed"}))))
    add("time: a completed card with no settle and no done rows keeps its subtree time",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True, mt=120), status={"g1": "completed"}))))
    add("time: a completed card: the first of equal times keeps its tag",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True, settledAt=170), done("g2", 170.0, parentId="g1"),
                             status={"g1": "completed"}))))
    add("time: a blocked card dates from its newest block, a done blocked node skipped",
        W(S(tr=T_idle, st=ST(blk("g1", 140, mt=300), blk("g2", 145.5, parentId="g1"),
                             blk("g3", 190, parentId="g1", nodeComplete=True), status={"g1": "blocked"}))))
    add("time: a blocked card: of equal block times the first keeps its tag",
        W(S(tr=T_idle, st=ST(blk("g1", 145), blk("g2", 145.0, parentId="g1"), status={"g1": "blocked"}))))
    add("time: a working card floors at its follow-up", W(S(tr=T_idle, st=ST(g("g1", followupAt=500, mt=120)))))
    add("time: a working card whose follow-up equals its subtree time keeps the subtree's tag",
        W(S(tr=T_idle, st=ST(g("g1", followupAt=120.0, mt=120)))))
    add("time: a follow-up stamp of 0 is null on the card", W(S(tr=T_idle, st=ST(g("g1", followupAt=0, mt=120)))))
    add("time: a working card whose follow-up is older keeps its subtree time",
        W(S(tr=T_idle, st=ST(g("g1", followupAt=110.5, mt=120), g("g2", parentId="g1", mt=125.0)))))
    add("time: _fsubmax: mt, else t, and the first of equal values (5 before 5.0)",
        W(S(tr=T_idle, st=ST(g("g1", t=5, mt=ABSENT), g("g2", parentId="g1", t=1, mt=5.0),
                             g("g3", parentId="g1", t=5.0), g("g4", parentId="g2", t=2, mt=0)))))
    add("time: _fsubmax: a child's later equal value does not replace the node's",
        W(S(tr=T_idle, st=ST(g("g1", t=7.0), g("g2", parentId="g1", t=7)))))
    # re-check and re-judging
    add("recheck: a targeted follow-up on a soft-blocked top drops it to working",
        W(S(tr=T_idle, st=ST(blk("g1", 140, followupPending={"t": 150}), status={"g1": "blocked"}))))
    add("rejudging: a plain reply after the block, before the unblocker looked",
        W(S(tr=T_idle, st=ST(blk("g1", 140, blockCheckT=150), status={"g1": "blocked"}))))
    add("rejudging: the unblocker's watermark covers the reply",
        W(S(tr=T_idle, st=ST(blk("g1", 140, blockCheckT=250), status={"g1": "blocked"}))))
    add("rejudging: the watermark is the oldest among blocked children, truncated",
        W(S(tr=T_idle, st=ST(g("g1", blocked=False, blockCheckT=500), blk("g2", 140, parentId="g1", blockCheckT=250.9),
                             blk("g3", 141, parentId="g1", blockCheckT=199.9), status={"g1": "blocked"}))))
    add("rejudging: a blocked child's missing watermark is 0",
        W(S(tr=T_idle, st=ST(g("g1", blockCheckT=500), blk("g2", 140, parentId="g1"), blk("g3", 141, parentId="g1",
                                                                                         blockCheckT=900),
                             status={"g1": "blocked"}))))
    Tf = tree(turn([u("u1", 100, "fix the cache"), asst("a1", 101, SUB1)]),
              turn([u("u2", 200.3, "now the index"), asst("b1", 201, SUB2)]))
    add("rejudging: the child's watermark is truncated before the compare (200.5 reads 200, under a reply at 200.3)",
        W(S(tr=Tf, st=ST(g("g1"), blk("g2", 140, parentId="g1", blockCheckT=200.5), status={"g1": "blocked"}))))
    add("rejudging: the top's own watermark is not truncated (200.5 stays above a reply at 200.3)",
        W(S(tr=Tf, st=ST(g("g1", blockCheckT=200.5), status={"g1": "blocked"}))))
    add("rejudging: no open block below: the top's own watermark, not truncated",
        W(S(tr=T_idle, st=ST(g("g1", blockCheckT=199.5), status={"g1": "blocked"}))))
    add("rejudging: no plain reply (the last prompt carries a goal id)",
        W(S(tr=tree(turn([u("u1", 100, "fix it")]), turn([u("u2", 200, "go <!-- romp-goal-id: g1 -->"),
                                                            asst("b1", 201, SUB2)])),
            st=ST(blk("g1", 140, trail=[k1]), status={"g1": "blocked"}))))
    add("rejudging: no parse: the plain reply is 0", W(S(tr=None, st=ST(blk("g1", 140, blockCheckT=0),
                                                                        status={"g1": "blocked"}))))
    # nudges
    nr = lambda **kw: {"count": 1, "failed": False, **kw}
    add("nudge: the history rides the card, a float count truncated",
        W(S(tr=T_idle, st=ST(g("g1"))), board={"nudgeRecords": {"g1": nr(count=2.7)}, "nudgeTimes": {"g1": [150, 160.5]}}))
    add("nudge: a zero count is no history", W(S(tr=T_idle, st=ST(g("g1"))), board={"nudgeRecords": {"g1": nr(count=0)}}))
    add("nudge: failed on a working card", W(S(tr=T_idle, st=ST(g("g1", log=[{"kind": "block", "src": "nudge", "at": 150}]))),
                                             board={"nudgeRecords": {"g1": nr(failed=True)}}))
    add("nudge: failed, then a planner row after the nudge's block retires it",
        W(S(tr=T_idle, st=ST(g("g1", log=[{"kind": "block", "src": "nudge", "at": 150},
                                          {"kind": "note", "src": "planner", "ev_t": 160}]))),
          board={"nudgeRecords": {"g1": nr(failed=True)}}))
    add("nudge: failed, a row at the same time does not retire it",
        W(S(tr=T_idle, st=ST(g("g1", log=[{"kind": "block", "src": "nudge", "at": 150},
                                          {"kind": "note", "src": "closer", "at": 150.0}]))),
          board={"nudgeRecords": {"g1": nr(failed=True)}}))
    add("nudge: failed, no nudge block row: the failure stamp is the floor",
        W(S(tr=T_idle, st=ST(g("g1", log=[{"kind": "x", "src": "user", "ev_t": 140}]))),
          board={"nudgeRecords": {"g1": nr(failed=True, failedAt=145)}}))
    add("nudge: failed, neither: any user event retires it",
        W(S(tr=T_idle, st=ST(g("g1", log=[{"kind": "x", "src": "user"}]))), board={"nudgeRecords": {"g1": nr(failed=1 == 1)}}))
    add("nudge: failed, blocked by the nudge itself", W(S(tr=T_idle, st=ST(
        g("g1", blocked=True, log=[{"kind": "block", "src": "nudge", "ev_t": 150, "at": 0}]), status={"g1": "blocked"})),
        board={"nudgeRecords": {"g1": nr(failed=True)}}))
    add("nudge: failed, blocked by the planner: no chip", W(S(tr=T_idle, st=ST(
        blk("g1", 150), status={"g1": "blocked"})), board={"nudgeRecords": {"g1": nr(failed=True)}}))
    # stalls
    st_rec = {"why": "the closer has not settled it", "since": 140}
    add("stall: the chip on an idle session's working card files it under needs you",
        W(S(tr=T_idle, st=ST(g("g1", stallSummary="the closer is stuck"))), board={"stalls": {"g1": st_rec}}))
    add("stall: a working session's stalled card stays working", W(S(tr=T_open, st=ST(g("g1"))),
                                                                   board={"stalls": {"g1": st_rec}}))
    add("stall: a completed card keeps its column", W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True), status={"g1": "completed"})),
                                                      board={"stalls": {"g1": st_rec}}))
    add("stall: a done-confirming card stays working", W(S(tr=T_idle, st=ST(g("g1"), confirming=["g1"])),
                                                         board={"stalls": {"g1": st_rec}}))
    add("stall: an in-flight hold is the Analyzing swirl", W(S(tr=T_idle, st=ST(g("g1"))),
                                                             board={"stalls": {"g1": {"why": "a judge pass is mid-flight",
                                                                                      "since": 1}}}))
    add("stall: a record without since raises KeyError", W(S(tr=T_open, st=ST(g("g1"))),
                                                           board={"stalls": {"g1": {"why": "x"}}}))
    add("stall: a falsy record is none", W(S(tr=T_idle, st=ST(g("g1"))), board={"stalls": {"g1": {}}}))
    add("stall: a failed nudge hides the stall chip", W(S(tr=T_idle, st=ST(g("g1"))),
                                                        board={"stalls": {"g1": st_rec},
                                                               "nudgeRecords": {"g1": nr(failed=True)}}))
    # the pass-through fields
    add("fields: done-confirming, the stale summary, and the pass-through keys",
        W(S(tr=T_idle, st=ST(g("g1", followupAt=300, distilledMt=200, summary=" a takeaway ", relayCarried="far asks",
                                briefParts=[{"id": "x", "since": 1}], summaryParts=[], warns=[{"what": "w"}],
                                failLog=None, background="the backstory", followupPending=0, summaryQuote=""),
                             confirming=["g1", "zz"]))))
    add("fields: falsy pass-through keys are null where Python writes `or None`, raw where it writes `.get`",
        W(S(tr=T_idle, st=ST(g("g1", relayCarried="", briefParts=[], summaryParts={}, warns=0, failLog="",
                                stallSummary="", background=0, followupPending="", summaryQuote=False)))))
    add("fields: a completed card in the confirming set is not done-confirming",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True), status={"g1": "completed"}, confirming=["g1"]))))
    add("fields: a summary of whitespace is not stale",
        W(S(tr=T_idle, st=ST(g("g1", followupAt=300, distilledMt=200, summary="\x1c "))))) 
    # the serving fold
    srv = {"goalId": PEER + ":g1", "msgId": "m1"}
    add("serving: a working mirror folds into its sender's card", W(S(tr=T_idle, st=ST(g("g1", serving=srv)))))
    add("serving: a needs-you mirror stands on the board", W(S(tr=T_idle, st=ST(blk("g1", 140, serving=srv, blockCheckT=250),
                                                                               status={"g1": "blocked"}))))
    add("serving: a falsy goal id does not fold", W(S(tr=T_idle, st=ST(g("g1", serving={"goalId": ""}),
                                                                       g("g2", serving="x")))))

    # ── flatten and the rows
    add("flatten: children by subtree recency, newest first, ties in store order",
        W(S(tr=T_idle, st=ST(g("g1"), g("c1", parentId="g1", t=120), g("c2", parentId="g1", t=130),
                             g("c3", parentId="g1", t=120.0), g("c4", parentId="g1", t=110), g("d1", parentId="c4", t=140)))))
    trs = lambda *ks: [k for k in ks]
    add("anchors: two flattened nodes sharing an id: the warm one's pair serves the cold one after it",
        W(S(tr=T_idle, st=ST(g("g1"), N("c1", parentId="g1", t=130, id="dup", trail=["nokey:1:x"]),
                             N("c2", parentId="g1", t=120, id="dup", trail=[k2])))))
    add("anchors: the same, store order reversed from flatten order",
        W(S(tr=T_idle, st=ST(g("g1"), N("c2", parentId="g1", t=120, id="dup", trail=[k2]),
                             N("c1", parentId="g1", t=130, id="dup", trail=["nokey:1:x"], summaryAnchor="sa")))))
    add("anchors: across sessions in alive order",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup"))), S(1, tr=None, st=ST(N("g1", trail=["x"], id="dup"))),
          S(2, tr=None, st=ST(N("g7", trail=["x"], id="dup", promptUuid="p7")))))
    add("anchors: a hidden node never writes the table",
        W(S(0, tr=T_idle, st=ST(g("g0"), N("g1", trail=[k1], id="dup")),
            rec=REC(healSessionTops={"g1": [None, {"hidden": True}]})),
          S(1, tr=None, st=ST(N("g1", trail=[k1], id="dup")))))
    add("anchors: an orphan and a cycle are never flattened",
        W(S(0, tr=T_idle, st=ST(g("g0"), N("o1", parentId="gone", trail=[k1], id="dup1"),
                                N("y1", parentId="y2", trail=[k1], id="dup2"), N("y2", parentId="y1", trail=[k2]))),
          S(1, tr=None, st=ST(N("a", id="dup1", trail=["x"]), N("b", id="dup2", trail=["x"])))))
    add("anchors: a junk quote drops the prompt anchor", W(S(tr=T_idle, st=ST(g("g1", quote="continue."),
                                                                              g("g2", quote="fix it", t=90)))))
    add("row: a present mt of 0 is kept, not t", W(S(tr=T_idle, st=ST(g("g1"), g("c1", parentId="g1", t=120, mt=0),
                                                                     g("c2", parentId="g1", t=110, mt=0.0)))))
    add("row: a null text", W(S(tr=T_idle, st=ST(g("g1", text=None)))))
    add("row: no text raises KeyError after the row's anchors are written (the next session reads them)",
        W(S(0, tr=T_idle, st=ST(N("g1", trail=[k1], id="dup", text=ABSENT))), S(1, tr=None, st=ST(N("g1", trail=["x"], id="dup")))))
    add("row: no t raises KeyError", W(S(tr=T_idle, st=ST(g("g1"), g("c1", parentId="g1", t=ABSENT, mt=130)))))
    add("row: reviewed earlier: a done sub before the top's review boundary",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True, deltaSince=160), done("c1", 150, parentId="g1"),
                             done("c2", 170, parentId="g1"), done("c3", 160.0, parentId="g1"),
                             status={"g1": "completed"}))))
    add("row: parked rows, open only", W(S(tr=T_idle, st=ST(
        g("g1"), g("c1", parentId="g1", t=110), hand("h1", parentId="g1", t=120), hand("h2", parentId="g1", t=130),
        g("c2", parentId="g1", t=105, nodeComplete=True)))))
    add("row: a parked row under a done top reads done, so no parked cue",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True), N("c1", parentId="g1", t=110),
                             hand("h1", parentId="g1", t=120), status={"g1": "completed"}))))
    add("row: a parked row with an unlogged blocked child reads as a question, so no parked cue",
        W(S(tr=T_idle, st=ST(g("g1"), N("c1", parentId="g1", t=110), N("d1", parentId="c1", t=111, blocked=True),
                             hand("h1", parentId="g1", t=120)))))
    add("row: the agent's own to-do item: auth, and never rolled down",
        W(S(tr=T_idle, st=ST(g("g1", nodeComplete=True), g("c1", parentId="g1", agentTask={"status": "open"}),
                             g("c2", parentId="g1", agentTask={"status": "done", "x": 1}), g("c3", parentId="g1", agentTask={}),
                             g("c4", parentId="g1", agentTask=None), status={"g1": "completed"}))))
    add("row: log rows on rows not done", W(S(tr=T_idle, st=ST(blk("g1", 140, seg=k1), g("c1", parentId="g1", nodeComplete=True,
                                                                                       log=[{"kind": "done", "seg": k1}]),
                                              status={"g1": "blocked"}))))
    add("row: a rolled-up question", W(S(tr=T_idle, st=ST(g("g1"), blk("c1", 140, parentId="g1"),
                                                          blk("c2", 150, parentId="g1", cleared=True),
                                         status={"g1": "blocked"}))))
    add("row: an umbrella is done when every kid not cleared is done",
        W(S(tr=T_idle, st=ST(g("g1", umbrella=True), done("c1", 150, parentId="g1"), g("c2", parentId="g1", cleared=True),
                             g("u2", umbrella=1, t=90), g("u3", umbrella=True, t=80), g("c3", parentId="u3", t=81)))))
    add("row: a handoff row names its recipient, no landing", W(S(tr=T_idle, st=ST(
        g("g1", summary=SUB1[:40]), hand("h1", parentId="g1", summary=SUB1[:40]), hand("h2", "ünï-codé-sid-ëëë", parentId="g1"),
        g("c1", parentId="g1")))))
    # ── the landing
    T3 = tree(turn([u("u1", 100, "fix the cache"), asst("a1", 101, SUB1), asst("a2", 102, "Short.")]),
              turn([u("u2", 200, "and the suite"), asst("b1", 201, SUB2), asst("b2", 202, "tiny")]),
              turn([u("u3", 300, "and the migration"), asst("c1", 301, SUB3), tool("c2", 302)]))
    q1, q2, q3 = segids(km, T3)
    add("landing: a completed card pins the newest substantive tail across the subtree",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], nodeComplete=True, summary="It is done."), N("c1", parentId="g1", trail=[q2]),
                         N("c2", parentId="g1", trail=[q3, q1]), status={"g1": "completed"}))))
    bl = dict(blocked=True, log=[{"kind": "block", "ev_t": 150}])
    add("landing: the cited anchor holds when not outrun, with its quote and paragraphs",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], blockSummary="Some brief.", summaryAnchor="b1", summaryQuote="the suite",
                           distilledMt=250, summaryAnchors=[{"a": "b1", "q": "x"}, None, {"a": "c1", "q": ""}, 0], **bl),
                         status={"g1": "blocked"}))))
    add("landing: the cited anchor's row: a raw falsy quote on the card, none on the row",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], blockSummary="Some brief.", summaryAnchor="b1", summaryQuote=0,
                           distilledMt=250, **bl), status={"g1": "blocked"}))))
    add("landing: the cited anchor outrun by a newer trail segment",
        W(S(tr=T3, st=ST(N("g1", trail=[q1, q3], blockSummary="Some brief.", summaryAnchor="b1", summaryQuote="q",
                           distilledMt=250, summaryAnchors=[{"a": "b1"}], **bl), status={"g1": "blocked"}))))
    add("landing: the cited anchor outrun by the follow-up stamp",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], blockSummary="Some brief.", summaryAnchor="b1", distilledMt=250,
                           followupAt=260, **bl), status={"g1": "blocked"}))))
    add("landing: a cited anchor that is not substantive is not cited",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], blockSummary="x", summaryAnchor="b2", summaryAnchors=[{"a": "b2"}], **bl),
                         status={"g1": "blocked"}))))
    add("landing: a completed card's pinned tail beats its citation",
        W(S(tr=T3, st=ST(N("g1", trail=[q1], summary="Some takeaway.", summaryAnchor="b1", summaryQuote="sq",
                           distilledMt=250, summaryAnchors=[{"a": "b1"}], nodeComplete=True),
                         status={"g1": "completed"}))))
    add("landing: the line's opening sentence in the newest segment's turn",
        W(S(tr=T3, st=ST(N("g1", trail=[q2, q1], blockSummary="The cache invalidation bug is fixed: more.", blocked=True,
                           log=[{"kind": "block", "ev_t": 150}]), status={"g1": "blocked"}))))
    T4 = tree(turn([u("u1", 100, "alpha"), asst("a1", 101, SUB1)]), turn([u("u2", 100, "beta"), asst("b1", 101, SUB2)]))
    r1, r2 = segids(km, T4)
    add("landing: of two trail segments with equal times, the last one counted wins",
        W(S(tr=T4, st=ST(N("g1", trail=[r1, r2], summary="Recap: the search suite passes again.", nodeComplete=False,
                           blocked=True, blockSummary="Recap: the search suite passes again.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    add("landing: the same, the order of the trail reversed",
        W(S(tr=T4, st=ST(N("g1", trail=[r2, r1], blocked=True, blockSummary="Recap: the search suite passes again.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    T5 = tree(turn([u("u1", -50, "alpha"), asst("a1", -49, SUB1)]))
    (n1,) = segids(km, T5)
    add("landing: a segment whose time is below -1 never qualifies for the text tier",
        W(S(tr=T5, st=ST(N("g1", t=-50, trail=[n1], blocked=True, blockSummary="The cache invalidation bug is fixed: x.",
                           log=[{"kind": "block", "ev_t": -40}]), status={"g1": "blocked"}))))
    add("landing: the latest-prose walk prefers substantive, then newest",
        W(S(tr=T3, st=ST(N("g1", trail=[q3, q2, q1], blocked=True, blockSummary="Nothing matches this line at all.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    T6 = tree(turn([u("u1", 100, "x"), asst("a1", 101, "tiny one"), asst("a2", 102, "tiny two")]))
    (s1,) = segids(km, T6)
    add("landing: the stub tier when nothing is substantive",
        W(S(tr=T6, st=ST(N("g1", trail=[s1], blocked=True, blockSummary="Nothing here matches.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    T8 = tree(turn([u("u1", 100, "x"), tool("w1", 101), u("u2", 110, "and y"), asst("w2", 111, "tiny"),
                    tool("w3", 112)]), turn([u("u3", 200, "z"), tool("w4", 201)]))
    t1, t2, t3 = segids(km, T8)
    add("landing: the stub tier takes the rest of the turn when the segment has no text",
        W(S(tr=T8, st=ST(N("g1", trail=[t1], blocked=True, blockSummary="Nothing here matches.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    add("landing: the last resort in subtree order (the node, then its children last to first)",
        W(S(tr=T8, st=ST(N("g1", trail=["nokey:1:a"]), N("c1", parentId="g1", trail=[t3, "nokey:3:c"]),
                         N("c2", parentId="g1", trail=[t1, "nokey:2:b"]), N("c3", parentId="c2", trail=[t3])))))
    add("landing: the last resort reads each trail from its end",
        W(S(tr=T8, st=ST(N("g1", trail=[t3, t1])))))
    T7 = tree(turn([u("u1", 100, "x"), asst("", 101, SUB1), asst("a2", 102, "tiny")]))
    (e1,) = segids(km, T7)
    add("landing: a substantive reply whose uuid is '' is no text atom (the walk lands on the stub)",
        W(S(tr=T7, st=ST(N("g1", trail=[e1], blocked=True, blockSummary="Nothing matches.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    add("landing: with a parse, a landing that finds nothing stays null (no raw citation)",
        W(S(tr=T3, st=ST(N("g1", trail=["nokey:1:x"], summaryAnchor="ghost", summary="s"), status={"g1": "completed"}))))
    T9 = tree(turn([u("u1", 100, "fix the cache"), asst("a1", 101, SUB1)]), turn([u("u3", 300, "z"), asst("c1", 301, "tiny")]))
    v1, v2 = segids(km, T9)
    add("landing: the latest-prose walk takes an older substantive segment over a newer stub",
        W(S(tr=T9, st=ST(N("g1", trail=[v1, v2], blocked=True, blockCheckT=900, blockSummary="Nothing matches this line.",
                           log=[{"kind": "block", "ev_t": 1}]), status={"g1": "blocked"}))))
    add("landing: a completed card's tail: of equal segment times the first in the subtree stays",
        W(S(tr=T4, st=ST(N("g1", trail=["nokey:1:x"], nodeComplete=True, summary="Done."), N("c1", parentId="g1", trail=[r1]),
                         N("c2", parentId="g1", trail=[r2]), status={"g1": "completed"}))))
    add("landing: no parse: the citation is served raw",
        W(S(tr=None, st=ST(N("g1", summaryAnchor="cited-raw", summary="s", summaryQuote="sq", summaryAnchors=[{"a": "x"}]),
                         status={"g1": "completed"}))))
    add("landing: no parse and no citation", W(S(tr=None, st=ST(N("g1", summary="s"), status={"g1": "completed"}))))

    # ── the placeholders
    add("provisional: a live session's unplaced prompt and no working card",
        W(S(tr=tree(turn([u("u1", 100, "write the docs")], ended=False)), st=ST(N("g1", nodeComplete=True),
                                                                              status={"g1": "completed"}))))
    add("provisional: a working card covers it", W(S(tr=tree(turn([u("u1", 100, "write the docs")], ended=False)),
                                                   st=ST(N("g1")))))
    add("provisional: captions unrecorded raise", W(S(tr=tree(turn([u("u1", 100, "write the docs")], ended=False)),
                                                    st=ST(), captions=None) | {"captions": None}))
    add("provisional: a store fault means no placeholder", W(S(tr=tree(turn([u("u1", 100, "write")], ended=False)))
                                                             | {"store": None}))
    pt = tree(turn([u("u1", 100, "may I run the migration " + "x" * 150)], ended=False))
    (p1,) = segids(km, pt)
    add("blocked placeholder: the live prompt, cut at 140", W(S(tr=pt, st=ST(), live=live_perm)))
    add("blocked placeholder: the caption wins", W(S(tr=pt, st=ST(placements={p1 + "#p": None}), live=live_perm,
                                                    captions={p1 + "#p": {"caption": "the gist"}})))
    add("blocked placeholder: a numeric caption is the text",
        W(S(tr=pt, st=ST(placements={p1 + "#p": None}), live=live_perm, captions={p1 + "#p": {"caption": 7}})))
    add("blocked placeholder: captions unrecorded raise", W(S(tr=pt, st=ST(placements={p1: None}), live=live_perm)
                                                            | {"captions": None}))
    nt = tree(turn([u("u1", 100, "go", author="sdk")], ended=False))
    for name_, ask in (("a long question", {"question": "  Which database should the migration target? " * 4}),
                       ("a short question", {"question": "Proceed?", "header": "Plan"}),
                       ("a header", {"question": "", "header": " Choose a model "}),
                       ("a question whose cut ends in whitespace (U+001C is Python whitespace)",
                        {"question": "x" * 136 + " \x1c " + "y" * 20}),
                       ("a question of exactly 140 code points", {"question": "é" * 140}),
                       ("a Permission header", {"header": "PERMISSION"}), ("an empty ask", {}), ("no ask", None)):
        add("blocked placeholder: no human prompt, %s" % name_, W(S(tr=nt, st=ST(), live=live_perm,
                                                                    rec=REC(currentAsk=ask))))
    add("blocked placeholder: an unrecorded ask is no ask (inside a try)",
        W(S(tr=nt, st=ST(), live={"state": "picker", "since": 5})))
    add("blocked placeholder: no turns", W(S(tr=tree(), st=ST(), live=live_perm)))
    awt = tree(turn([u("u1", 100, "go"), asst("a1", 101, "done")]))
    (w1,) = segids(km, awt)
    pl = {w1 + "#p": "g1"}
    aw2 = {"why": "ßlow task: npm test", "kind": "commands", "since": 99, "count": True, "items": [{"k": 1}]}
    add("awaiting placeholder: a live idle session with no goal to floor, its why capitalized (str.upper)",
        W(S(tr=awt, st=ST(N("g1", nodeComplete=True), status={"g1": "completed"}, placements=pl),
            rec=REC(sessionAwaiting={"idle=true": aw2}, awaitingTaskDescs=["npm test"]))))
    add("awaiting placeholder: the provisional card comes first", W(S(tr=awt, st=ST(), rec=REC(
        sessionAwaiting={"idle=true": aw2}))))
    add("awaiting placeholder: not live: a null in asks (the provisional card is not live either)",
        W(S(tr=awt, st=ST(placements=pl), live=None, rec=REC(sessionAwaiting={"idle=true": aw2}))))
    awt1 = tree(turn([u("u1", 100, "go", s=1), asst("a1", 101, "done", s=1)]))
    (w2,) = segids(km, awt1)
    add("awaiting placeholder: a count of 0 kept, a float count is null, the tasks unrecorded raise",
        W(S(0, tr=awt, st=ST(placements=pl), rec=REC(sessionAwaiting={"idle=true": dict(aw2, count=0, why="w")})),
          S(1, tr=awt1, st=ST(placements={w2: None}),
            rec=REC(sessionAwaiting={"idle=true": dict(aw2, count=2.0, items=None)})),
          S(2, tr=awt, st=ST(placements=pl), rec=REC(sessionAwaiting={"idle=true": aw2}, drop=("awaitingTaskDescs",)))))
    add("awaiting placeholder: a one-letter why and a non-ASCII first letter",
        W(S(0, tr=awt, st=ST(placements=pl), rec=REC(sessionAwaiting={"idle=true": dict(aw2, why="w")})),
          S(1, tr=awt1, st=ST(placements={w2: None}), rec=REC(sessionAwaiting={"idle=true": dict(aw2, why="ǆob")}))))
    add("awaiting dot: an awaiting card lights the session's awaiting dot",
        W(S(tr=T_idle, st=ST(g("g1", **stamp)))))
    # ── peers, reads, several sessions
    add("peers: sorted, across the awaiting signal, stamps, handoff rows and origins",
        W(S(0, tr=T_idle, st=ST(g("g1", origin={"peer": "zz-sender"}), hand("h1", PEER2, parentId="g1"),
                                g("g2", t=90, awaitingWhy="w", awaitingKind="peer", awaitingAt=5, awaitingPeers=["aa-peer"])),
            rec=REC(sessionAwaiting={"idle=true": None}, apiError={"status": 1})),
          S(1, tr=T_idle, st=ST(g("g1")), rec=REC(sessionAwaiting={"idle=true": dict(aw, peers=["mm", {"sid": "bb"}])}))))
    add("reads: the usage read before the nudges", W(S(tr=T_idle, st=ST(g("g1"), g("g2", t=90)),
                                                      rec=REC(apiError={"status": 500}))))
    add("reads: no top reached, no nudges", W(S(tr=T_idle, st=ST(g("g1"), status={"g1": "cleared"}))))
    return c


def refuse_cases(km):
    """Worlds the Rust loader refuses (layer6.rs validate): Python answers or raises on them."""
    c = []
    T = tree(turn([u("u1", 100, "x"), asst("a1", 101, SUB1)]))

    def add(name, w):
        c.append(("REFUSE " + name, w[0], w[1]))
    add("a wait without since", W(S(tr=T, st=ST(N("g1"))), board={"wmap": {SIDS[0]: {"peer": "x"}}}))
    add("a wait whose since is a string", W(S(tr=T, st=ST(N("g1"))), board={"wmap": {SIDS[0]: {"since": "5"}}}))
    add("a stall record that is a string", W(S(tr=T, st=ST(N("g1"))), board={"stalls": {"g1": "stuck"}}))
    add("a judge-auth entry with a login (its label resolved through the SDK)",
        W(S(tr=T, st=ST(N("g1"), lastNode="g1")), board={"jauth": {SIDS[0]: {"mode": "login", "login": "L1"}}}))
    add("a judge-auth label that is a number", W(S(tr=T, st=ST(N("g1"), lastNode="g1")),
                                                 board={"jauth": {SIDS[0]: {"loginLabel": 5}}}))
    add("a judge-auth entry that is a string", W(S(tr=T, st=ST(N("g1"))), board={"jauth": {SIDS[0]: "down"}}))
    add("debug rows that are not a list", W(S(tr=T, st=ST(N("g1"))), board={"debug": True, "debugRows": {"a": 1}}))
    add("a debug row that is a string", W(S(tr=T, st=ST(N("g1"))), board={"debug": True, "debugRows": ["x"]}))
    add("a debug row whose seg is a number", W(S(tr=T, st=ST(N("g1"))),
                                               board={"debug": True, "debugRows": [{"fsid": SIDS[0], "seg": 5}]}))
    add("a debug row whose goal list holds a dict", W(S(tr=T, st=ST(N("g1"))), board={
        "debug": True, "debugRows": [{"fsid": SIDS[0], "goal": [{"a": 1}]}]}))
    add("a confirming item that is a number", W(S(tr=T, st=ST(N("g1"), confirming=[5]))))
    add("summaryAnchors that is a string", W(S(tr=T, st=ST(N("g1", summaryAnchors="b1")))))
    add("a summaryAnchors item without a", W(S(tr=T, st=ST(N("g1", summaryAnchors=[{"q": "x"}])))))
    add("a blockCheckT that is a string", W(S(tr=T, st=ST(N("g1", blockCheckT="5")))))
    add("a session-awaiting why that is a number", W(S(tr=T, st=ST(N("g1")), rec=REC(
        sessionAwaiting={"idle=true": {"why": 5, "kind": "x"}}))))
    add("a session-awaiting peer sid that is a number", W(S(tr=T, st=ST(N("g1")), rec=REC(
        sessionAwaiting={"idle=true": {"why": "w", "kind": "x", "peers": [{"sid": 5}]}}))))
    add("session-awaiting items that are a string", W(S(tr=T, st=ST(N("g1")), rec=REC(
        sessionAwaiting={"idle=true": {"why": "w", "kind": "x", "items": "ab"}}))))
    add("owner tops without descs", W(S(tr=T, st=ST(N("g1")), rec=REC(bgLive=[{"tid": "t"}],
                                                                       bgOwnerTops={"g1": {"since": 5}}))))
    add("a current ask that is a string", W(S(tr=T, st=ST(), rec=REC(currentAsk="ask?"))))
    add("flags that are a list", W(S(tr=T, st=ST(N("g1")), flags=["hideFromFeed"])))
    w = W(S(tr=T, st=ST(N("g1"))))
    del w[0]["sessions"][SIDS[0]]["store"]
    add("a session without a store key", w)
    return c


def answer_cases(km, dw, specs, lines=None):
    cases = []
    for name, snap, trees in specs:
        try:
            expect, _got, note = answer_world(km, dw, snap, trees, lines)
        except Exception as e:                         # noqa: BLE001 (only a refused world may break the harness)
            if not name.startswith("REFUSE "):
                raise
            expect, note = [], "the replay harness raised %s: %s" % (type(e).__name__, e)
        case = {"name": name, "snapshot": snap, "trees": {k: v for k, v in trees.items()}, "expect": expect}
        if note is not None:
            # build_feed's frame code after the session loop (layer 7's), not the body: the entries are already taken.
            # The one known: the replay's _cleared_ids_display answers a set, which _cleared_foreign reads as a dict.
            case["buildNote"] = note
        cases.append(case)
    return cases


def summarize(cases):
    n_sess = sum(len(cs["expect"]) for cs in cases if not cs["name"].startswith("REFUSE "))
    n_raise = sum(1 for cs in cases if not cs["name"].startswith("REFUSE ") for e in cs["expect"]
                  if isinstance(e["answer"], dict) and "raises" in e["answer"])
    n_null = sum(1 for cs in cases for e in cs["expect"] if e["answer"] is None)
    n_cards = sum(len((e["answer"] or {}).get("asks") or []) + len((e["answer"] or {}).get("servingFolds") or [])
                  for cs in cases if not cs["name"].startswith("REFUSE ") for e in cs["expect"]
                  if not (isinstance(e["answer"], dict) and "raises" in e["answer"]))
    return n_sess, n_raise, n_null, n_cards


def edge_main(out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer6-")
    try:
        _l5, _l4, dw, km, _rec, _root = load_kernel(tmp)
        lines = Lines(km)
        cases = answer_cases(km, dw, edge_cases(km), lines)
        cases += answer_cases(km, dw, refuse_cases(km))
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer6.py --edge-cases", "lineCoverage": lines.report(),
               "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
    n_sess, n_raise, n_null, n_cards = summarize(cases)
    n_refuse = sum(1 for cs in cases if cs["name"].startswith("REFUSE "))
    notes = sorted({cs["buildNote"] for cs in cases if "buildNote" in cs})
    lc = doc["lineCoverage"]
    print("edge cases: %d worlds (%d the loader must refuse), %d sessions, %d entries raising, %d null, %d cards; "
          "lines %d of %d run, missed %s -> %s (%.2f MB)" % (
              len(cases), n_refuse, n_sess, n_raise, n_null, n_cards, lc["ran"], lc["lines"], json.dumps(lc["missed"]),
              out, os.path.getsize(out) / 1e6))
    print("  frame-code notes after the session loop (not the body's): %d cases %s" % (
        sum(1 for cs in cases if "buildNote" in cs), json.dumps(notes)))
    return 0

# ───────────────────────────── --fuzz ─────────────────────────────
# Random worlds of one to three sessions. Each session's tree and store come from layer 5's generator
# (dump_layer5.fz_session: layer 3's random trees with prompts, markers, prose and seams; stores whose trails name the
# tree's segments, with summaries taken from its prose, handoffs, origins, latches, placements, captions, heals and
# cleared sets), its sid renamed. Node ids repeat across sessions (g0, g1, ...), so the anchor table is shared. Then
# every node gets what layer 6 reads (text, t and mt of both tags with ties and zeros, completion and block flags, log
# rows of every kind and source, settle and follow-up stamps, watermarks, citations and paragraphs, the pass-through
# keys, serving, born records, awaiting stamps, tracked handoffs and origins), the store a status map, a focus and a
# confirming set, the session random recorded answers (session awaiting, api errors, live tasks and their owners,
# services, interrupts, retry storms, the closer, warm asks, current asks; one answer in 150 dropped, so a NotRecorded
# raise is reached), flags and a live row, and the board a cleared set, a wait graph, stalls, judge-auth latches,
# judges in flight, nudge records, debug rows and downtime. Seeded: a failure replays.
FZ_PEERS = (PEER, PEER2, "s9", "s8", "zz-sender", "")
FZ_T = (100, 100.0, 101, 150, 150.0, 200, 0, 0.0, -5, 99.5, 1781000000)


def fz_num(rng, pool=FZ_T):
    return rng.choice(pool)


def fz_log(rng, trail):
    rows = []
    for _ in range(rng.choice((0, 0, 1, 1, 2, 3, 4))):
        r = {"kind": rng.choice(("block", "block", "done", "done", "reopen", "awaiting", "note", "unblock"))}
        if rng.random() < 0.85:
            r["src"] = rng.choice(("planner", "closer", "nudge", "user", "interrupt", "unblocker", "agent", "courier",
                                   "grouper", None))
        for k in ("ev_t", "at"):
            if rng.random() < 0.6:
                r[k] = fz_num(rng)
        if trail and rng.random() < 0.4:
            r["seg"] = rng.choice(trail)
        if rng.random() < 0.05:
            r["synth"] = True
        if rng.random() < 0.05:
            r["lift"] = True
        rows.append(r)
    return rows


def fz_node(rng, nd, uuids, ids):
    trail = nd.get("trail") or []
    r = rng.random()
    if r < 0.007:
        nd.pop("text", None)
    elif r < 0.05:
        nd["text"] = None
    elif r < 0.12:
        nd["text"] = "↪ delegated to %s: %s" % (rng.choice(("peer9", "far:remote", "")), rng.choice(("the work", "")))
    else:
        nd["text"] = rng.choice(("goal ", "Ünïcödé goal ", "fix ")) + nd["id"]
    if rng.random() < 0.993:
        nd["t"] = fz_num(rng)
    if rng.random() < 0.5:
        nd["mt"] = fz_num(rng)
    for k, p in (("nodeComplete", 0.25), ("blocked", 0.2), ("rolledUp", 0.05), ("umbrella", 0.06)):
        if rng.random() < p:
            nd[k] = True if k in ("nodeComplete", "blocked") else rng.choice((True, 1, "x", 0))
    nd["log"] = fz_log(rng, trail)
    if rng.random() < 0.2:
        nd["settledAt"] = rng.choice((0, None, 150, 150.0, 200))
    if rng.random() < 0.12:
        nd["agentTask"] = rng.choice(({"status": "open"}, {"status": "done"}, {}, None, 0))
    for k, vals in (("followupPending", ({"t": 1}, True, 0, None, "x")), ("blockCheckT", (0, None, 150, 150.9, 199.5,
                                                                                         250)),
                    ("summaryQuote", ("a quote", "", None, 0)), ("relayCarried", ("far asks", "", None)),
                    ("briefParts", ([{"id": "x", "since": 1}], [], None)), ("summaryParts", ([{"id": "y"}], None)),
                    ("stallSummary", ("stuck note", "", None)), ("warns", ([{"what": "w"}], [], None)),
                    ("failLog", ([{"model": "m", "error": "e"}], None)), ("background", ("backstory", None, "")),
                    ("deltaSince", (0, 100, 150, 150.0, 200, None))):
        if rng.random() < 0.15:
            nd[k] = rng.choice(vals)
    if rng.random() < 0.25:
        nd["summaryAnchor"] = rng.choice((uuids or ["nope"]) + ["", "nope"])
    if rng.random() < 0.12:
        nd["summaryAnchors"] = [rng.choice(({"a": rng.choice((uuids or ["n"]))}, {"a": "x", "q": "q"},
                                            {"a": "x", "q": ""}, None, 0, {})) for _ in range(rng.randint(0, 3))]
    if rng.random() < 0.15:
        nd["serving"] = rng.choice(({"goalId": PEER + ":g1"}, {"goalId": PEER + ":g1"}, {"goalId": ""}, "x", None))
    if rng.random() < 0.1:
        nd["born"] = rng.choice(({"why": "started it", "parentText": "ask"}, {}, None, {"why": "w"}))
        if rng.random() < 0.5:
            nd["parentId"] = "gone"
    if rng.random() < 0.15:
        nd["awaitingWhy"] = rng.choice(("waiting on the build", "waiting on peers", ""))
        nd["awaitingAt"] = rng.choice((0, 150, 150.0, 160, None))
        nd["awaitingKind"] = rng.choice(("peer", "commands", None, ""))
        if rng.random() < 0.6:
            nd["awaitingPeers"] = rng.sample([PEER, PEER2, "peer:" + PEER, "s9"], rng.randint(0, 3))
    if isinstance(nd.get("handoff"), dict):
        nd["handoff"]["peer"] = rng.choice(FZ_PEERS)
        if rng.random() < 0.3:
            nd["handoff"]["tracked"] = rng.choice((True, 0))
    if isinstance(nd.get("origin"), dict) or rng.random() < 0.05:
        o = {"peer": rng.choice(FZ_PEERS + (("eeeeeeee-9999",) if rng.random() < 0.1 else ()))}
        if rng.random() < 0.6:
            o["goalId"] = rng.choice(("s9:g1", "s9:g2", PEER + ":g1", "x"))
        for k in ("peerName", "peerHost"):
            if rng.random() < 0.4:
                o[k] = rng.choice(("remote", "far", ""))
        if rng.random() < 0.4:
            o["tracked"] = rng.choice((True, 0))
        nd["origin"] = o
    if rng.random() < 0.06 and ids:
        nd["id"] = rng.choice(ids)                   # an id another node's key carries: the anchor table's key


def fz_recorded(rng, ids, uuids):
    def aw():
        if rng.random() < 0.6:
            return rng.choice((None, None, {}))
        d = {"why": rng.choice(("waiting on 2 agents", "ßlow task", "", "waiting on a background command: x")),
             "kind": rng.choice(("agents", "commands", "peer", None))}
        for k, vals in (("since", (150, 150.5, None)), ("count", (2, 1, True, False, 0, -1, 2.5, None)),
                        ("peers", ([{"name": "x", "sid": PEER2}, PEER, {"name": "y"}, ""], [PEER], None, [])),
                        ("items", ([{"kind": "agents", "id": "a1"}], [], None))):
            if rng.random() < 0.7:
                d[k] = rng.choice(vals)
        return d
    rec = {"closerPending": rng.random() < 0.2, "interrupting": rng.random() < 0.1,
           "sessionRetrying": rng.choice((None, None, None, {"since": 150, "count": 3})),
           "sessionAwaiting": {"idle=true": aw(), "idle=false": aw()},
           "bgLive": rng.choice(([], [], [], [{"tid": "t1", "desc": "npm test", "t": 170}])),
           "apiError": rng.choice((None, None, None, None, {"status": 500, "text": "overloaded"}, {"tooLong": True},
                                   {"authErr": True, "text": "401"}, {"noRetry": True}, {"refusal": True},
                                   {"spendLimit": True}, {"modelLimit": True, "status": 429}, 0)),
           "interruptedBy": rng.choice((False, False, True, "spend", None)),
           "peerAnswered": [rng.choice((0, 155, 170)), rng.choice(({}, {PEER: 170}, {PEER2: 155.5}))],
           "healSessionTops": {}, "awaitingTaskDescs": rng.choice(([], ["npm test"])),
           "warmWanted": rng.random() < 0.5, "capSwitchOffer": rng.choice((None, {"to": "key"}, {})),
           "bgOwnerTops": {x: {"since": rng.choice((130, 170, 140)), "descs": rng.choice(([], ["npm test"]))}
                           for x in rng.sample(ids, min(len(ids), rng.randint(0, 2)))},
           "bgServiceDescs": rng.choice(([], ["dev server"])),
           "currentAsk": rng.choice((None, {"question": "Proceed with the migration?"}, {"header": "Permission"},
                                     {"header": "Plan", "question": ""}, {"question": "x" * 150}))}
    if ids and rng.random() < 0.2:
        x = rng.choice(ids)
        rec["healSessionTops"][x] = [rng.choice(ids + [None, ""]), rng.choice(({"why": "healed", "parentText": "p"},
                                                                               {"hidden": True}, {},
                                                                               {"hidden": True, "why": "skill"}))]
        rec["healFocus"] = x                          # a hint for the store's focus (removed before the world is built)
    for k in list(rec):
        if rng.random() < 1 / 150:
            del rec[k]
    return rec


def fz_world(rng, km, l5, l3, l2, serial):
    n = rng.choice((1, 1, 1, 2, 2, 3))
    sessions, all_ids, cleared = [], [], []
    for i in range(n):
        s5 = l5.fz_session(rng, km, l3, l2, serial * 4 + i)
        s5 = json.loads(json.dumps(s5).replace(l2.SID, SIDS[i]).replace(l5.SID, SIDS[i]))
        tr, st = s5["tree"], s5["store"]
        uuids = [a.get("uuid") for t in (tr or {}).get("turns") or [] for a in t.get("atoms") or []
                 if isinstance(a.get("uuid"), str)]
        ids = list((st or {}).get("nodes") or {})
        all_ids += ids
        cleared += s5["cleared"]
        if st is not None:
            for nd in st["nodes"].values():
                fz_node(rng, nd, uuids, ids)
            st["status"] = {x: rng.choice(("working", "blocked", "completed", "cleared", "awaiting"))
                            for x in ids if rng.random() < 0.6}
            if rng.random() < 0.6:
                st["lastNode"] = rng.choice(ids + [None, "gone"]) if ids else None
            if rng.random() < 0.2:
                st["confirming"] = rng.sample(ids, min(len(ids), rng.randint(0, 2)))
        rec = fz_recorded(rng, ids, uuids)
        hf = rec.pop("healFocus", None)
        if st is not None and hf is not None and rng.random() < 0.6:
            st["lastNode"] = hf
            st["nodes"][hf]["parentId"] = None
        if st is not None and rec.get("bgLive") and rec.get("bgOwnerTops") and rng.random() < 0.7:
            for x in rec["bgOwnerTops"]:             # an owned task on a blocked top, newer or older than its block
                st["status"][x] = "blocked"
                st["nodes"][x]["parentId"] = None
                st["nodes"][x]["blocked"] = True
                st["nodes"][x]["log"].append({"kind": "block", "src": "planner",
                                              "ev_t": rng.choice((130, 140, 170, 170.0, 200))})
        if s5["healed"] and "healSessionTops" in rec:
            rec["healSessionTops"].update(s5["healed"])
        live = None
        if s5["live"]:
            live = {"state": rng.choice(("idle", "idle", "idle", "permission", "picker", "working", None)),
                    "since": rng.choice((NOW - 100, None))}
            if rng.random() < 0.1:
                lid = rng.choice(("L1", ""))
                live.update(authLogin=lid, authLoginLive=rng.choice((lid, "L2")), authLabel=rng.choice(("work", "")))
            if rng.random() < 0.03:
                live = {}
        sp = S(i, tr=tr, st=st, rec=rec, flags=rng.choice((None,) * 6 + ({"hideFromFeed": True}, {"x": 1}, {},
                                                                            {"hideFromFeed": 0})),
               names=rng.choice(("", "", "", None, ["", "", ""], ["s-%d" % i, "/x", "nocolor"])),
               captions=s5["captions"], leaf_ok=rng.random() < 0.9, live=live)
        if st is None:
            sp["store"] = None
        if s5["captions"] is None:
            sp["captions"] = None
        sessions.append(sp)
    sids = [s["sid"] for s in sessions]
    board = {"cleared": sorted(set(cleared + rng.sample(all_ids, min(len(all_ids), rng.choice((0, 0, 1)))))),
             "jactive": rng.sample(sids, rng.choice((0, 0, 0, 1))),
             "wmap": {x: {"peer": "peer9", "since": rng.choice((99, 100, 100.0, 150, 200, 1e10))}
                      for x in sids if rng.random() < 0.2},
             "stalls": {x: rng.choice(({"why": "the closer has not settled it", "since": 140},
                                       {"why": "the closer has not settled it", "since": 140.5},
                                       {"why": "a judge pass is mid-flight", "since": 1}, {},
                                       {"why": "w"} if rng.random() < 0.2 else {"why": "x", "since": 0}))
                        for x in rng.sample(all_ids, min(len(all_ids), rng.choice((0, 0, 1, 2))))},
             "jauth": {x: rng.choice(({"mode": "key", "t": 150, "note": "bad key"}, {"mode": "login", "t": 1},
                                      {"mode": "login", "loginLabel": "judge login", "note": ""}, {}))
                       for x in sids if rng.random() < 0.1},
             "nudgeRecords": {x: {"count": rng.choice((0, 1, 2, 2.7)), "failed": rng.random() < 0.5,
                                  **({"failedAt": rng.choice((145, None))} if rng.random() < 0.5 else {})}
                              for x in rng.sample(all_ids, min(len(all_ids), rng.choice((0, 0, 1, 2))))},
             "downtime": rng.choice(([], [], [], [[150, 9e18]], [[0, 1]]))}
    board["nudgeTimes"] = {x: [150, 160.5] for x in board["nudgeRecords"] if rng.random() < 0.5}
    if rng.random() < 0.3:
        board["debug"] = True
        board["debugRows"] = [{"fsid": rng.choice(sids + ["other"]), "kind": "planner",
                               **rng.choice(({"goal": rng.choice(all_ids + ["zz"])} if all_ids else {"goal": "zz"},
                                             {"goal": rng.sample(all_ids, min(len(all_ids), 2))},
                                             {"seg": rng.choice(("k", "", "x:1:y") + tuple(
                                                 re.sub(r"#(p|d|live)$", "", k) for st_ in
                                                 [s["store"] for s in sessions if s["store"]]
                                                 for k in (st_.get("placements") or {})))},
                                             {"goal": 5}))}
                              for _ in range(rng.choice((0, 1, 3, 25)))]
    pst = {"rompUuid": "s9", "nodes": {"s9:g1": {"id": "s9:g1", "t": 1, "nodeComplete": rng.random() < 0.3,
                                                 "cleared": rng.random() < 0.1}}, "status": {}}
    peers = {PEER: {"names": ["peer9", "/TESTDIR/work/p", "#ff8800"], "flags": None, "store": pst},
             "s9": {"names": ["peer-s9", "/x", "#00aa00"], "flags": None, "store": pst},
             "s8": {"names": None, "flags": None, "store": None}}
    return W(*sessions, board=board, peers=peers)


def fuzz_specs(rng, km, l5, n, seed):
    l3 = l5.load_l4().load_l3()
    l2 = l3.load_l2()
    return [("fuzz seed %d world %d" % (seed, i),) + fz_world(rng, km, l5, l3, l2, i) for i in range(n)]


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


RUST_SUMMARY = re.compile(r"^layer6 edge set(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"sessions, (\d+) cards, (\d+) tree rows, (\d+) values\), (\d+) refused at load \(every REFUSE "
                          r"case\), (\d+) differences in (\d+) cases ")


def fuzz_main(n, seed, plant, binary, out, with_lines):
    """Make `n` worlds from `seed`, answer them in Python, and compare with `romp-spike layer6` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer6-fuzz-")
    try:
        t0 = time.monotonic()
        l5, _l4, dw, km, _rec, _root = load_kernel(tmp)
        specs = fuzz_specs(random.Random(seed), km, l5, n, seed)
        lines = Lines(km) if with_lines else None
        cases = answer_cases(km, dw, specs, lines)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer6.py --fuzz %d --seed %d" % (n, seed), "cases": cases}
        path = out or os.path.join(tmp, "layer6_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        n_sess, n_raise, n_null, n_cards = summarize(cases)
        raises = {}
        for cs in cases:
            for e in cs["expect"]:
                a = e["answer"]
                if isinstance(a, dict) and "raises" in a:
                    k = a["raises"] + (" " + a["arg"].split("(")[0] if a["raises"] == "NotRecorded" else " " + a["arg"])
                    raises[k] = raises.get(k, 0) + 1
        print("fuzz seed %d: %d worlds, %d sessions, %d cards made and answered by Python in %.1f s, %.1f MB; %d entries "
              "raise %s, %d null" % (seed, n, n_sess, n_cards, py_s, os.path.getsize(path) / 1e6, n_raise,
                                     json.dumps(raises, sort_keys=True), n_null))
        if lines is not None:
            lc = lines.report()
            print("  python: lines %d of %d run, missed %s" % (lc["ran"], lc["lines"], json.dumps(lc["missed"])))
        p = subprocess.run([binary, "layer6", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        out_lines = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, out_lines) if m]
        for line in out_lines:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer6: romp-spike layer6 exited %d without its summary line: %s" % (
                p.returncode, p.stderr.strip()[-2000:]), file=sys.stderr)
            return 2
        ncases, loaded, _s, _c, _r, values, refused, diffs, differing = map(int, summary[0].groups())
        if ncases != n or (plant is None and (loaded != n or refused != 0 or diffs != 0 or p.returncode != 0)):
            print("fuzz seed %d: FAILED, %d differences over %d worlds (%d loaded, %d refused at load)" % (
                seed, diffs, ncases, loaded, refused))
            return 1
        if plant is None:
            print("fuzz seed %d: OK, %d worlds, %d values compared, 0 differences" % (seed, n, values))
            return 0
        print("fuzz seed %d, planted %s: %s, %d differences in %d of %d worlds" % (
            seed, plant, "caught" if diffs else "NOT CAUGHT", diffs, differing, n))
        return 0 if diffs else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random worlds with romp-spike layer6")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=PLANTS, help="--fuzz against romp-spike layer6 --plant NAME: the check passes "
                    "when the plant is caught")
    ap.add_argument("--bin", help="a built romp-spike for --fuzz (default: cargo build --release)")
    ap.add_argument("--out", help="keep the --fuzz cases in this file (default: a temporary file, removed)")
    ap.add_argument("--lines", action="store_true", help="--fuzz: also report the body's lines run")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if a.fuzz is not None:
        if a.fuzz < 1:
            ap.error("--fuzz N needs N >= 1")
        return fuzz_main(a.fuzz, a.seed, a.plant, rust_binary(a.bin), a.out, a.lines)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT, or --fuzz N")
    return main_dumps(a.dumps)


if __name__ == "__main__":
    sys.exit(main())
