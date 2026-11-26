#!/usr/bin/env python3
"""Python's own memo decisions for step S6.1 of the Rust spike (rust/SPIKE.md, "S6.1 memo port and tuning"), for the
port's memo (rust/crates/romp-views/src/memo.rs) to match decision for decision and byte for byte.

The problem: build_feed serves a session's previous cards while every input its derivation reads stands
(kernel._feed_session_key, a 31-component key) and derives it again when one moved. A ported memo must serve exactly
when Python serves (serving more often shows stale cards Python would not; less often loses the memo's gain) and the
served build must equal what Python ships. The S2 replay cannot show either: it replaces the key with a constant.

The oracle: a world (a snapshot in the S2 dump's schema and its trees) and a sequence of steps, each a list of edits to
the world (`ops`). Python runs the steps as consecutive build_feed calls in ONE process, with the REAL
_feed_session_key and _feed_key_with_deps, the real memo, fold, held anchor aggregates and anchor table carried from
build to build. Only the key's leaves are answered from the world, the way the S2 replay answers the recorded helpers:
the files it stats from `keyFacts.files` (by kind: `transcript:<sid>`, `states:<name>`, `captions:<sid>`,
`store:<sid>` as [store, journal, archive], `goal:<sid>`, `gone:<sid>`, `usage`, `errors`), the in-memory revisions from
each session's `keyFacts` (`cut`, `live`, `watch`, `postal`, `reg`, `anchors`, `userWrite`, `snapDone`, `hold`,
`replayWrote`), the key on hand and the capped usage window from `keyFacts.auth` and `capOpen`, the nudge ledger from the
board's nudge records and times. Absent facts read as an untouched kernel's: null identities, except the transcript's,
`(0, 0, 0, 0)` when the session's `leafOk` (the inode 0 the S2 replay hands as the leaf head). The landing memo
(_SUMMARY_ANCHOR_MEMO), which the port does not have, is cleared before every build: its one observable effect is S4.5's
stale landing after a seam. The recorded `_bg_live_norm` answer feeds the key's `bg` component, read as an empty list by
the key when it was never recorded (the body raises there, as before).

Per step it records: every alive session's decision ({"hit": true} or {"miss": [the components that moved]}, with
"raise" and "served" when its key or derivation raised), the absent sessions, the SHA-1 of the feed's canonical and wire
strings, the held walk's counters moved, the memo's entries and evictions, and the same world built FRESH (every memo
cleared) with its SHA-1s, so the port can show that a memo-served build equals a fresh one wherever Python's do, and
that it serves Python's stale bytes where Python's do not. The frame keys build_feed passes through are taken from
Python's own build, as dump_layer7.py takes them, and written into the step's ops (the fresh build's when the memo build
raised, since a raising build never frames). Where Python's fresh build passes a different frame through (the session
order after the alive set changes, say), the port cannot build it from the snapshot: the step marks the fresh comparison
skipped and records the frame keys that differ (`keys`); a skip no key explains stops the run.

    python3 tools/rust-spike/dump_memo.py --edge DUMP OUT.json            # the small world's scripted sequence
    python3 tools/rust-spike/dump_memo.py --dump DUMP [--bin B] [--out F]   # a dump's default sequence, then romp-spike memo
    python3 tools/rust-spike/dump_memo.py --fuzz N [--seed S] [--steps K] [--plant NAME] [--bin B] [--out FILE]

`--edge` writes the scripted sequence over the small world as rust/crates/romp-views/tests/fixtures/memo_edge.json
(tests/memo_edge.rs checks it); `--dump` refers to the dump directory instead of inlining it (the large worlds, run on
the cluster); `--fuzz` draws N worlds from dump_layer7.py's generator (the postal log and notice files left out: they are
recomposed after the loop and never memoized) and K random steps each. Any difference fails the run; under `--plant`
the run passes only when the plant is caught. Stdlib only.
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
SCHEMA = "romp-spike-memo/1"
EMBED = False                  # --embed: each step carries Python's wire string, for the port's first-difference report
PLANTS = ("peers-settled", "tag-strict", "row-whole", "raise-absent", "no-forget", "stalls-all")
SEG_KEYS = ("segWalks", "segHeld", "segWalkTurns", "segPartial", "segReset", "segRaised", "segEvict", "segOverBound")
SESSION_FACTS = ("cut", "live", "watch", "postal", "reg", "anchors", "userWrite", "snapDone", "hold", "replayWrote")
MISSING = object()


def load_l7():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer7", os.path.join(HERE, "dump_layer7.py"))
    l7 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l7)
    return l7


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


# ───────────────────────────── the edits ─────────────────────────────
def apply_ops(snap, trees, ops):
    """Apply a step's edits in order. An op: {"op": "set"|"init"|"del", "at": "snapshot"|"tree", "sid" (for a tree),
    "path": [keys and indexes], "value"}. `set` at an index equal to a list's length appends; `init` sets only where the
    key is absent or null (so chained helpers can each make sure a container exists); `set` with an empty path at a tree
    replaces the tree (null: no tree). romp-spike's runner applies the same ops the same way (memo_cmd.rs)."""
    for op in ops:
        path = op["path"]
        if op["at"] == "tree":
            if not path:
                if op["op"] == "set":
                    trees[op["sid"]] = copy.deepcopy(op["value"])
                else:
                    trees.pop(op["sid"], None)
                continue
            box = trees[op["sid"]]
        else:
            box = snap
        for k in path[:-1]:
            box = box[k]
        last = path[-1]
        if op["op"] == "init":
            if isinstance(box, dict) and box.get(last) is None:
                box[last] = copy.deepcopy(op["value"])
        elif op["op"] == "set":
            if isinstance(box, list) and last == len(box):
                box.append(copy.deepcopy(op["value"]))
            else:
                box[last] = copy.deepcopy(op["value"])
        else:
            del box[last]


def S_(path, value):
    return {"op": "set", "at": "snapshot", "path": list(path), "value": value}


def D_(path):
    return {"op": "del", "at": "snapshot", "path": list(path)}


def T_(sid, path, value):
    return {"op": "set", "at": "tree", "sid": sid, "path": list(path), "value": value}


def I_(path, value):
    return {"op": "init", "at": "snapshot", "path": list(path), "value": value}


def files_op(snap, name, value):
    """The ops that record file identity `name` (keyFacts and its files map made first where absent)."""
    return [I_(["keyFacts"], {}), I_(["keyFacts", "files"], {}), S_(["keyFacts", "files", name], value)]


def fact_op(snap, sid, k, value):
    return [I_(["sessions", sid, "keyFacts"], {}), S_(["sessions", sid, "keyFacts", k], value)]


def board_fact_op(snap, k, value):
    return [I_(["keyFacts"], {}), S_(["keyFacts", k], value)]


# ───────────────────────────── the key's leaves ─────────────────────────────
class KeyHarness:
    """Installs the REAL _feed_session_key and _feed_key_with_deps over a world, with the leaves the S2 replay does not
    answer read from its keyFacts (the module docstring lists them), and records every session's decision."""

    def __init__(self, dw, l6, km, rp, snap):
        self.dw, self.l6, self.km, self.jd, self.rp, self.snap = dw, l6, km, km.jd, rp, snap
        self.saved = []
        self.cur = None
        self.in_key = False
        self.decisions = {}
        self.unknown = []

    def patch(self, obj, name, fn):
        self.saved.append((obj, name, obj.__dict__[name] if isinstance(obj, type) else getattr(obj, name)))
        setattr(obj, name, fn)

    def files(self):
        kf = self.snap.get("keyFacts")
        return (kf.get("files") if isinstance(kf, dict) else None) or {}

    def board_fact(self, k, default=None):
        kf = self.snap.get("keyFacts")
        return copy.deepcopy(kf.get(k, default)) if isinstance(kf, dict) else default

    def sfact(self, sid, k, default=None):
        kf = (self.snap["sessions"].get(str(sid)) or {}).get("keyFacts")
        return copy.deepcopy(kf.get(k, default)) if isinstance(kf, dict) else default

    def chat_ident(self, path):
        jd, rp, p = self.jd, self.rp, str(path)
        files = self.files()
        sid = rp.path_sid.get(p)
        if sid is not None:
            name = "transcript:" + sid
            default = (0, 0, 0, 0) if self.snap["sessions"][sid].get("leafOk") else None
            return copy.deepcopy(files[name]) if name in files else default
        d, base = os.path.split(p)
        kinds = ((str(jd.STATESDIR), ".jsonl", "states:"), (str(jd.CAPDIR), ".jsonl", "captions:"),
                 (str(jd.GONEDIR), ".json", "gone:"), (str(jd.GOALDIR), ".json", "goal:"))
        name = None
        for dirname, ext, kind in kinds:
            if d == dirname and base.endswith(ext):
                name = kind + base[:-len(ext)]
        if p == str(jd.STATE / "usage.json"):
            name = "usage"
        elif p == str(jd.ERRORS):
            name = "errors"
        if name is None:
            self.unknown.append(p)
            return None
        return copy.deepcopy(files.get(name))

    def store_identity(self, fsid):
        v = self.files().get("store:" + str(fsid))
        ident = tuple(copy.deepcopy(v)) if isinstance(v, list) else (None, None, None)
        return (str(self.jd.GOALDIR / (str(fsid) + ".json")),) + ident

    def install(self, real_key, real_deps):
        km, jd, snap, h = self.km, self.jd, self.snap, self

        def key(s, tm, ctx, prev):
            h.cur = s["sid"]
            h.in_key = True
            try:
                return real_key(s, tm, ctx, prev)
            finally:
                h.in_key = False
        self.patch(km, "_feed_session_key", key)
        self.patch(km, "_feed_key_with_deps", real_deps)
        self.patch(km, "_chat_ident", self.chat_ident)
        self.patch(jd, "_store_identity", self.store_identity)
        self.patch(jd, "shared_replay_wrote", lambda st: h.sfact(h.cur, "replayWrote"))
        self.patch(km, "_rewind_hold_get", lambda sid: h.sfact(sid, "hold"))
        self.patch(km, "_feed_reg_sig", lambda sid: h.sfact(sid, "reg"))
        self.patch(km, "_postal_session_slice", lambda sid, maps=None: h.sfact(sid, "postal", ((), ())))
        self.patch(km, "_postal_maps_indexed", lambda: ({}, {}, {}, {}, {}, {}))
        self.patch(km, "_auto_nudge_data", lambda: {"nudged": copy.deepcopy(snap["board"].get("nudgeRecords") or {})})
        self.patch(km, "_nudge_times", lambda: {g: list(v) for g, v in (snap["board"].get("nudgeTimes") or {}).items()})
        self.patch(km, "_usage_cap_open", lambda now=None: h.board_fact("capOpen"))
        self.patch(km, "_auth_key_present", lambda: bool(h.board_fact("auth")))
        self.patch(km, "_watch_awaiting", lambda sid: h.sfact(sid, "watch"))
        be = km._sdk_backend
        self.patch(be, "pending_cut", lambda sid: h.sfact(sid, "cut", ""))
        rp_bg = km._bg_live_norm

        def bg(fsid, *a, **k):
            rec = (snap["sessions"].get(str(fsid)) or {}).get("recorded") or {}
            if h.in_key and "bgLive" not in rec:
                return []                              # the key's read of a helper the dump never recorded
            return rp_bg(fsid, *a, **k)
        self.patch(km, "_bg_live_norm", bg)

        class Stub(self.l6._AskStub):
            def live_rev(self, sid):
                return h.sfact(sid, "live", 0)

            def live_atoms(self, sid):
                return []
        stub = Stub(self.rp, snap)
        self.patch(km.Sessions, "backend_for", staticmethod(lambda sid: stub))
        # the module dicts the key reads by sid, filled from the facts for this build
        for name, fact in (("_node_anchor_rev", "anchors"), ("_user_goal_write", "userWrite"),
                           ("_goals_snap_done", "snapDone")):
            d = getattr(km, name)
            self.saved.append((None, name, dict(d)))
            d.clear()
            for sid, S in snap["sessions"].items():
                kf = S.get("keyFacts")
                if isinstance(kf, dict) and fact in kf:
                    d[sid] = copy.deepcopy(kf[fact])
        real_count, real_miss = km._feed_memo_count, km._feed_memo_miss

        def count(k, n=1):
            if k == "hit":
                h.decisions.setdefault(h.cur, {})["hit"] = True
            return real_count(k, n)

        def miss(old, new):
            labels = real_miss(old, new)
            h.decisions.setdefault(h.cur, {})["miss"] = list(labels)
            return labels

        def complain(sid, name, e, served_prev):
            d = h.decisions.setdefault(sid, {})
            d["raise"] = "NotRecorded" if isinstance(e, h.dw.DumpError) else type(e).__name__
            d["served"] = bool(served_prev)
        self.patch(km, "_feed_memo_count", count)
        self.patch(km, "_feed_memo_miss", miss)
        self.patch(km, "_feed_derive_complain", complain)
        self.patch(km, "_held_mail_backfill", lambda: 0)

    def uninstall(self):
        for obj, name, v in reversed(self.saved):
            if obj is None:
                d = getattr(self.km, name)
                d.clear()
                d.update(v)
            else:
                setattr(obj, name, v)
        self.saved = []


# ───────────────────────────── one process, many builds ─────────────────────────────
class Runner:
    """One kernel, one world, its steps built in order with the memos carried across builds."""

    def __init__(self, l7, l6, dw, km, snap, trees, memo_bound=None, seg_bound=None):
        self.l7, self.l6, self.dw, self.km = l7, l6, dw, km
        self.snap, self.trees = snap, trees
        dw.clear_build_memos(km)
        km._node_anchor_last.clear()
        km._NOTICE_MEMO.clear()
        # the bounds an 8 GB box gets (a 64th and a 128th of its memory), not this machine's: the case file then reads
        # the same wherever it is made
        self.memo_bound = memo_bound if memo_bound is not None else (8 << 30) // 64
        self.seg_bound = seg_bound if seg_bound is not None else (8 << 30) // 128
        self.unknown = []
        km._HEAL_LOG.update(n=10 ** 9, h=10 ** 9)       # build_feed's once-per-rise heal note: stderr noise here
        # the adoption build (session-order.json, as dump_world's recipe), then every memo cleared, so the first step
        # is a cold build whose frame the later ones share; the bytes its entries took, for a bound that just holds them
        self.build()
        self.adopted_bytes = sum(ent[2] for ent in km._feed_memo.values())
        dw.clear_build_memos(km)
        km._node_anchor_last.clear()

    def build(self):
        """One build over the current world: (decisions by sid, feed or None, raise or None, seg deltas)."""
        km, dw = self.km, self.dw
        rp = dw.Replay(km, self.snap, self.trees)
        real_key, real_deps = km._feed_session_key, km._feed_key_with_deps
        rp.install()
        h = KeyHarness(dw, self.l6, km, rp, self.snap)
        h.install(real_key, real_deps)
        km._SUMMARY_ANCHOR_MEMO.clear()               # the port has no landing memo (the module docstring)
        seg0 = dict(km._FEED_SEG_STATS)
        evict0 = km._FEED_MEMO_STATS["evict"]
        now = self.snap["world"]["now"]
        feed = raised = None
        saved = (km.FEED_MEMO_BYTES, km.FEED_SEG_HELD_BYTES)
        km.FEED_MEMO_BYTES, km.FEED_SEG_HELD_BYTES = self.memo_bound, self.seg_bound
        try:
            with mock.patch("time.time", return_value=float(now)):
                km._STARTED = float(now)
                try:
                    feed = km.build_feed(now, km._live_map())
                except Exception as e:                 # noqa: BLE001 (the answer: build_feed itself raised)
                    raised = {"raises": type(e).__name__, "arg": str(e)}
        finally:
            km.FEED_MEMO_BYTES, km.FEED_SEG_HELD_BYTES = saved
            h.uninstall()
            rp.uninstall()
        self.unknown += h.unknown
        seg = {k: km._FEED_SEG_STATS.get(k, 0) - seg0.get(k, 0) for k in SEG_KEYS}
        memo = {"entries": len(km._feed_memo), "evict": km._FEED_MEMO_STATS["evict"] - evict0}
        return h.decisions, feed, raised, seg, memo

    def fresh(self):
        """The same world built with every memo cleared, the carried state put back after."""
        km = self.km
        names = ("_feed_memo", "_FEED_FOLD", "_FEED_SEG_HELD", "_SUMMARY_ANCHOR_MEMO", "_node_anchor_last",
                 "_FEED_DERIVE_FAILED")
        keep = {n: copy.copy(getattr(km, n)) for n in names}
        stats = {n: copy.deepcopy(getattr(km, n)) for n in ("_FEED_SEG_STATS", "_FEED_MEMO_STATS", "_FEED_FOLD_STATS")}
        build_no = km._FEED_SEG_BUILD[0]
        try:
            self.dw.clear_build_memos(km)
            km._node_anchor_last.clear()
            _d, feed, raised, _s, _m = self.build()
        finally:
            for n, v in keep.items():
                d = getattr(km, n)
                d.clear()
                d.update(v)
            for n, v in stats.items():
                d = getattr(km, n)
                d.clear()
                d.update(v)
            km._FEED_SEG_BUILD[0] = build_no
        return feed, raised

    def frame_ops(self, feed):
        """The ops that set the snapshot's frame to Python's own build's (its pass-through keys)."""
        dw, l7 = self.dw, self.l7
        keys = [k for k in feed if k not in l7.CONDITIONAL]
        frame = {k: dw.plain(v) for k, v in feed.items() if k not in dw.FRAME_SKIP}
        ops = []
        if self.snap.get("frameKeys") != keys:
            ops.append(S_(["frameKeys"], keys))
        if self.snap.get("frame") != frame:
            ops.append(S_(["frame"], frame))
        return ops

    def frame_diff(self, feed):
        """Why a fresh comparison is skipped: the pass-through frame keys whose value in `feed` (the fresh build) differs
        from the snapshot's frame (the memo build's, just set), and "(order)" when the frame's keys come in another
        order. The port takes those keys from the snapshot, so it cannot build Python's fresh frame there."""
        dw, l7 = self.dw, self.l7
        frame = {k: dw.plain(v) for k, v in feed.items() if k not in dw.FRAME_SKIP}
        have = self.snap.get("frame") or {}
        out = sorted(k for k in set(frame) | set(have) if frame.get(k, MISSING) != have.get(k, MISSING))
        if [k for k in feed if k not in l7.CONDITIONAL] != self.snap.get("frameKeys"):
            out.append("(order)")
        if not out:
            # a skip no frame key explains would hide the fresh comparison for an unknown reason: refuse the case file
            raise SystemExit("dump_memo: the fresh build's frame does not assemble from the snapshot, yet no pass-through "
                             "key differs and the key order is the same: a new kind of skip, not written")
        return out

    def step(self, ops, note):
        """Apply `ops`, build, and record the step (the frame ops appended to its ops)."""
        dw, l7 = self.dw, self.l7
        apply_ops(self.snap, self.trees, ops)
        decisions, feed, raised, seg, memo = self.build()
        ffeed, fraised = self.fresh()
        ops = list(ops)
        expect = {"decisions": [], "seg": seg, "memo": memo}
        for r in self.snap["alive"]:
            expect["decisions"].append([r["sid"], decisions.get(r["sid"], {})])
        if feed is not None:
            fops = self.frame_ops(feed)
            apply_ops(self.snap, self.trees, fops)
            ops += fops
            if dw.wire(l7.assemble(dw, self.snap, feed)) != dw.wire(feed):
                raise SystemExit("dump_memo: %s: the frame assembly does not give Python's wire bytes back" % note)
            expect.update(canonSha1=sha1(dw.canon(feed)), wireSha1=sha1(dw.wire(feed)),
                          absent=list(feed.get("absentSids") or []))
            if EMBED:
                expect["wire"] = dw.wire(feed)
        else:
            expect["raises"] = raised
            if ffeed is not None:
                # the memo build raised before it framed (the port's raises in the loop or the fold, before the frame
                # is assembled), so the snapshot's frame cannot reach it: give the snapshot the fresh build's frame and
                # compare the fresh build instead of skipping it (S6.1's second review)
                fops = self.frame_ops(ffeed)
                apply_ops(self.snap, self.trees, fops)
                ops += fops
            elif not self.snap.get("frameKeys"):
                # a build that raises makes no frame: the minimal frame keys the loader requires, never assembled (the
                # port must raise the same before any frame exists), as dump_layer7.py's raising cases carry
                fops = [S_(["frameKeys"], ["type", "asks", "now", "working", "awaiting", "bgServices"]),
                        S_(["frame"], {"type": "feed"})]
                apply_ops(self.snap, self.trees, fops)
                ops += fops
        if ffeed is not None:
            same_frame = dw.wire(l7.assemble(dw, self.snap, ffeed)) == dw.wire(ffeed)
            expect["fresh"] = ({"canonSha1": sha1(dw.canon(ffeed)), "wireSha1": sha1(dw.wire(ffeed))} if same_frame
                               else {"frameDiffers": True, "keys": self.frame_diff(ffeed)})
        else:
            expect["fresh"] = {"raises": fraised}
        expect["freshEqualsMemo"] = (feed is not None and ffeed is not None
                                     and dw.wire(feed) == dw.wire(ffeed) and dw.canon(feed) == dw.canon(ffeed))
        return {"note": note, "ops": ops, "expect": expect}


def strip_files(snap):
    """A memo world carries no postal log or notice files: those sections are recomposed after the loop and never
    memoized, and their files are the layer 7 harness's to write."""
    b = snap["board"]
    for k in ("postalLog", "mailNew", "notices"):
        b.pop(k, None)
    b["cleared"] = [c for c in b.get("cleared") or [] if not c.startswith(("parked:", "notice:"))]
    return snap


# ───────────────────────────── the scripted sequence (the small world) ─────────────────────────────
def sids_of(snap):
    return [r["sid"] for r in snap["alive"]]


def text_atom(tree, human):
    """(turn index, atom index, block index) of the last atom with a text block, a human prompt's or a reply's."""
    found = None
    for ti, t in enumerate(tree.get("turns") or []):
        for ai, a in enumerate(t.get("atoms") or []):
            c = ((a.get("message") or {}) if isinstance(a.get("message"), dict) else {}).get("content")
            if not isinstance(c, list):
                continue
            for bi, b in enumerate(c):
                if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str):
                    if (a.get("type") == "user") == human:
                        found = (ti, ai, bi)
    return found


def edge_steps(runner):
    """The scripted sequence over the small world: (note, ops) pairs, each built in turn (each plant of romp-spike memo
    is caught by a step built for it; rust/SPIKE.md lists them)."""
    snap, trees = runner.snap, runner.trees
    s1, s2, s3, s4 = sids_of(snap)[:4]
    now = snap["world"]["now"]
    steps = [("the cold build: every session derives", []),
             ("again: the two sessions that name peers derive once more (their peers were unsettled)", []),
             ("again: every session is served", []),
             ("the clock moves 300 s: served, the tints restamped", [S_(["world", "now"], now + 300)]),
             ("s003's transcript identity moves under the same inode: s003 derives, its closed turns held",
              files_op(snap, "transcript:" + s3, [0, 1781100100000000000, 1000, 0])),
             ("s003's transcript is rewritten under a new inode: s003 derives, its aggregate reset",
              files_op(snap, "transcript:" + s3, [7, 1781100200000000000, 1000, 0]))]
    for name, ops_fn in [("an atom's text in s004's tree changes, its identity stands: served stale",
                          lambda: _edit_prompt(trees, s4)),
                         ("s004's transcript identity moves: s004 derives the edit",
                          lambda: files_op(snap, "transcript:" + s4, [0, 1781100300000000000, 2000, 0]))]:
        steps.append((name, ops_fn))
    steps += [
        ("s001's live row: a field no card reads (model) changes: served",
         [S_(["liveMap", s1, "model"], "another-model")]),
        ("s001's live state moves: s001 derives (row)", [S_(["liveMap", s1, "state"], "working")]),
        ("s001's live since becomes the float of the same value: served (a tag change alone moves nothing)",
         [S_(["liveMap", s1, "since"], float(snap["liveMap"][s1]["since"]))]),
        ("s002 is no longer live: s002 derives (row)", [D_(["liveMap", s2])]),
        ("s002 is live again on a permission prompt with a recorded ask: s002 derives (row, ask)",
         [S_(["liveMap", s2], {"state": "permission", "since": now - 50}),
          S_(["sessions", s2, "recorded", "currentAsk"], {"question": "Run the migration?"})]),
        ("s002's current ask changes: s002 derives (ask)",
         [S_(["sessions", s2, "recorded", "currentAsk"], {"question": "Run the migration now?", "header": "Plan"})]),
        ("s001's names entry changes its color: s001 derives (names), s002 too (peers: s001 is its peer)",
         [S_(["sessions", s1, "names", 2], "#123456")]),
        ("s003's display name changes: s003 derives (names)", [S_(["alive", 2, "name"], "s003-renamed")]),
        ("a cleared id under s003: s003 derives (cleared), s001 too (peers: s003's cleared slice)",
         [S_(["board", "cleared"], sorted(snap["board"]["cleared"] + [s3 + ":g9"]))]),
        ("a cleared id with no colon belongs to no session: served", None),
        ("s003's cleared id listed a second time: served (Python's key reads the cleared set, where the duplicate "
         "collapses; s001's peer facts carry s003's slice, so s001 too)", None),
        ("a second cleared id under s003 listed after its first though it sorts before it (the list unsorted, as "
         "production's cleared log is in write order): s003 derives (cleared), s001 too (peers)", None),
        ("the same cleared ids in the reverse order: served (kernel._cleared_by_sid sorts each session's slice)", None),
        ("s003's first cleared id listed again at the head, apart from its other copies (a duplicate that is not "
         "adjacent, in a slice that is not sorted): served (the set collapses it)", None),
        ("s003's store: a node's text changes, its identity stands: served stale", lambda: _edit_store(snap, s3)),
        ("s003's store identity moves: s003 derives (store)",
         files_op(snap, "store:" + s3, [[11, 1781100400000000000, 300], None, None])),
        ("s004's store read faults: s004 derives (store)", [S_(["sessions", s4, "store"], None)]),
        ("s004's store reads again: s004 derives (store)", None),
        ("s003 waits on a peer: s003 derives (wait)", [S_(["board", "wmap", s3], {"peer": "peer9", "since": now - 30})]),
        ("the judges' auth is down for s003: s003 derives (jauth); the floor reaches a helper the dump never recorded, so "
         "the derivation raises and the previous cards are served",
         [S_(["board", "jauth", s3], {"mode": "key", "t": now - 20, "note": "bad key"})]),
        ("the judges' auth is back: s003's key equals the one its served entry was memoized under, a hit",
         [D_(["board", "jauth", s3])]),
        ("a judge call for s004 is in flight: s004 derives (jactive)",
         [S_(["board", "jactive"], sorted(set(snap["board"]["jactive"]) | {s4}))]),
        ("a host suspension: every session derives (downtime)", [S_(["board", "downtime"], [[now - 4000, now - 3900]])]),
        ("debug mode on: every session derives (debug)", [S_(["board", "debug"], True), S_(["board", "debugRows"], [])]),
        ("debug mode off: every session derives (debug)", [S_(["board", "debug"], False)]),
        ("s003's recorded interrupting answer flips: s003 derives (interrupting)",
         [S_(["sessions", s3, "recorded", "interrupting"], True)]),
        ("s003's interrupting answer is no longer recorded: its key raises, its previous cards are served",
         [D_(["sessions", s3, "recorded", "interrupting"])]),
        ("again: the key raises again, served again", []),
        ("restored: s003 derives (interrupting)", [S_(["sessions", s3, "recorded", "interrupting"], False)]),
        ("a deferral record on a node s001 read: s001 derives (stalls)",
         [S_(["board", "stalls", s1 + ":g1"], {"why": "the closer has not settled it", "since": now - 60})]),
        ("a deferral record on a node nobody read: served", [S_(["board", "stalls", s1 + ":g99"], {"why": "x", "since": 1})]),
        ("s003's nudge record counts again: s003 derives (nudge)",
         [S_(["board", "nudgeRecords", s3 + ":g1", "count"], 3)]),
        ("a zero-count nudge record's history changes: served (history shows only with a count)",
         [S_(["board", "nudgeRecords", s1 + ":g3"], {"count": 0, "failed": False}),
          S_(["board", "nudgeTimes", s1 + ":g3"], [now - 500])]),
        ("again with other history: served", [S_(["board", "nudgeTimes", s1 + ":g3"], [now - 400, now - 300])]),
        ("s004 is hidden: s004 derives (hide), its entry null", [S_(["sessions", s4, "flags"], {"hideFromFeed": True})]),
        ("again: the hidden session is served", []),
        ("s004 shows again: s004 derives (hide)", [S_(["sessions", s4, "flags"], None)]),
        ("s002 leaves the alive set: its entry is forgotten", None),
        ("s002 returns: it derives cold", None),
        ("again: s002 derives once more (its peer unsettled), the rest served", []),
        ("s003's cut, live revision and watch move: s003 derives (cut, live, watch)",
         lambda: fact_op(snap, s3, "cut", "rewind-1") + fact_op(snap, s3, "live", 4)
         + fact_op(snap, s3, "watch", {"kind": "watches", "why": "a build", "since": now - 10, "tasks": ["b"]})),
        ("s003's postal slice, registry and anchors revision move: s003 derives (postal, reg, anchors)",
         lambda: fact_op(snap, s3, "postal", [[[[s3, s1], now - 70, None, None, []]], [[s1, "s001"]]])
         + fact_op(snap, s3, "reg", ["readable", {"bgLedger": None, "spawnedAt": 5}]) + fact_op(snap, s3, "anchors", 2)),
        ("s003's user-write mark, punch record and rewind hold: s003 derives (store)",
         lambda: fact_op(snap, s3, "userWrite", now - 5.5) + fact_op(snap, s3, "snapDone", now - 5.5)
         + fact_op(snap, s3, "hold", {"cutT": now - 99, "leaf": "x", "at": now - 98})),
        ("s003's rewind hold becomes empty: s003 derives (store)", lambda: fact_op(snap, s3, "hold", {})),
        ("s003's store gains a journal whose replay wrote nothing: s003 derives (store)",
         lambda: files_op(snap, "store:" + s3, [[12, 1, 2], [13, 1, 2], None]) + fact_op(snap, s3, "replayWrote", False)),
        ("the journal's replay wrote: s003 derives (store)", lambda: fact_op(snap, s3, "replayWrote", True)),
        ("s003's states, captions and death-marker identities move: s003 derives (states, captions, reg)",
         lambda: files_op(snap, "states:" + s3, [21, 1, 1, 1]) + files_op(snap, "captions:" + s3, [22, 1, 1, 1])
         + files_op(snap, "gone:" + s3, [23, 1, 1, 1])),
        ("usage.json's identity and the capped window move: served (no entry read usage)",
         lambda: files_op(snap, "usage", [31, 1, 1, 1])
         + board_fact_op(snap, "capOpen", {"window": "five_hour", "resetsAt": now + 3600})),
        ("the key on hand appears: every session derives (auth)", lambda: board_fact_op(snap, "auth", True)),
        ("the judge-errors identity moves with debug off: served", lambda: files_op(snap, "errors", [41, 1, 1, 1])),
        ("s001's recorded api error and cap offer change, its transcript identity stands: served stale",
         [S_(["sessions", s1, "recorded", "apiError"], {"status": 529, "message": "Overloaded"}),
          S_(["sessions", s1, "recorded", "capSwitchOffer"], None)]),
        ("s001's transcript identity moves: s001 derives the error, its entry now reading usage.json (transcript)",
         lambda: files_op(snap, "transcript:" + s1, [0, 1781100500000000000, 3000, 0])),
        ("again: served (s001's stored key took `usage` over the entry it just derived, which reads usage.json, not "
         "over the previous entry, which did not: the key computed now, over that entry, equals it)", []),
        ("usage.json's identity moves: s001 derives (usage)", lambda: files_op(snap, "usage", [32, 1, 1, 1])),
        ("the capped window moves on: s001 derives (offer)",
         lambda: board_fact_op(snap, "capOpen", {"window": "seven_day", "resetsAt": now + 86400})),
        ("s003's peer store identity moves: s003 derives (store), s001 too (peers)",
         lambda: files_op(snap, "store:" + s3, [[12, 2, 2], [13, 1, 2], None])),
        ("s004 loses a recorded helper and its transcript moves: its derivation raises, its previous cards are served",
         lambda: files_op(snap, "transcript:" + s4, [0, 1781100600000000000, 4000, 0])
         + [D_(["sessions", s4, "recorded", "healSessionTops"])]),
        ("s004 is hidden while its derivation would raise: s004 derives (hide), null", None),
        ("s004 shows again, the helper still missing: the derivation raises, nothing to serve, absent", None),
        ("again: still absent (the key moved nothing, but there is no entry to hit)", []),
        ("the helper is recorded again: s004 derives", None),
        ("the clock moves to a float: served, restamped", [S_(["world", "now"], float(now) + 900.5)]),
        ("s001's live row gains a subagent (its agentId, type and since are what the awaiting rows read): s001 derives "
         "(row)",
         [S_(["liveMap", s1, "subagents"], [{"agentId": "a1", "type": "general-purpose", "since": now - 40}])]),
    ]
    return steps


def _edit_prompt(trees, sid):
    tr = trees[sid]
    at = text_atom(tr, True)
    if at is None:
        raise SystemExit("dump_memo: no human prompt text in %s's tree" % sid)
    ti, ai, bi = at
    old = tr["turns"][ti]["atoms"][ai]["message"]["content"][bi]["text"]
    return [T_(sid, ["turns", ti, "atoms", ai, "message", "content", bi, "text"], old + " (and the docs)")]


def _edit_store(snap, sid):
    st = snap["sessions"][sid]["store"]
    for nid, nd in st["nodes"].items():
        if isinstance(nd.get("text"), str):
            return [S_(["sessions", sid, "store", "nodes", nid, "text"], nd["text"] + " (revised)")]
    raise SystemExit("dump_memo: no node text in %s's store" % sid)


def run_edge(runner):
    """Build the scripted steps in order; the steps given as None are made from the world at that point."""
    snap, trees = runner.snap, runner.trees
    s1, s2, s3, s4 = sids_of(snap)[:4]
    out = []
    saved = {}
    for note, ops in edge_steps(runner):
        if callable(ops):
            ops = ops()
        elif ops is None:
            ops = _late_ops(note, snap, trees, saved, s1, s2, s3, s4)
        out.append(runner.step(ops, note))
    return out


def _late_ops(note, snap, trees, saved, s1, s2, s3, s4):
    if note.startswith("a cleared id with no colon"):
        return [S_(["board", "cleared"], sorted(snap["board"]["cleared"] + ["nocolon"]))]
    if note.startswith("s003's cleared id listed a second time"):
        if s3 + ":g9" not in snap["board"]["cleared"]:
            raise AssertionError("the duplicate step follows the step that clears %s:g9" % s3)
        return [S_(["board", "cleared"], sorted(snap["board"]["cleared"] + [s3 + ":g9"]))]
    if note.startswith("a second cleared id under s003"):
        if snap["board"]["cleared"].count(s3 + ":g9") != 2:
            raise AssertionError("the unsorted step follows the duplicate step")
        return [S_(["board", "cleared"], snap["board"]["cleared"] + [s3 + ":g10"])]   # ":g10" sorts before ":g9"
    if note.startswith("the same cleared ids in the reverse order"):
        return [S_(["board", "cleared"], list(reversed(snap["board"]["cleared"])))]
    if note.startswith("s003's first cleared id listed again at the head"):
        cur = snap["board"]["cleared"]
        if cur[:1] == [s3 + ":g9"] or cur.index(s3 + ":g10") > cur.index(s3 + ":g9"):
            raise AssertionError("the reversed list puts s003:g10 before s003:g9")
        return [S_(["board", "cleared"], [s3 + ":g9", "nocolon"] + cur)]
    if note.startswith("s004's store read faults"):
        raise AssertionError("listed with its ops")
    if note.startswith("s004's store reads again"):
        return [S_(["sessions", s4, "store"], saved.pop("store", None) or _store_back(snap, s4))]
    if note.startswith("s002 leaves"):
        i = sids_of(snap).index(s2)
        saved["s2row"] = (i, copy.deepcopy(snap["alive"][i]))
        return [S_(["alive"], [r for r in snap["alive"] if r["sid"] != s2])]
    if note.startswith("s002 returns"):
        i, row = saved.pop("s2row")
        rows = copy.deepcopy(snap["alive"])
        rows.insert(i, row)
        return [S_(["alive"], rows)]
    if note.startswith("s004 is hidden while"):
        return [S_(["sessions", s4, "flags"], {"hideFromFeed": True})]
    if note.startswith("s004 shows again, the helper"):
        return [S_(["sessions", s4, "flags"], None)]
    if note.startswith("the helper is recorded again"):
        return [S_(["sessions", s4, "recorded", "healSessionTops"], {})]
    raise AssertionError(note)


STORE_KEEP = {}


def _store_back(snap, sid):
    return STORE_KEEP[sid]


# ───────────────────────────── random sequences ─────────────────────────────
def fz_ops(rng, snap, trees, step, kept, force=None):
    """One random step's edits over a world (each kind of input the key reads, moved with and without its identity),
    with the helper a `rerecord-stash` step took the step before put back, whatever this step draws. `force` names the
    kind instead of drawing one (a just-full world's `grow` step)."""
    back = kept.pop("back", None)
    kind, ops = _fz_kind_ops(rng, snap, trees, step, kept, force)
    if back is not None and back in kept.get("rec", {}):
        ops = list(ops) + [S_(["sessions", back[0], "recorded", back[1]], kept["rec"].pop(back))]
        kept["backs"] = kept.get("backs", 0) + 1
    return kind, ops


def _fz_kind_ops(rng, snap, trees, step, kept, force=None):
    """fz_ops's step: one kind drawn (or `force`) and its edits."""
    sids = sids_of(snap)
    sid = rng.choice(sids) if sids else None
    now = snap["world"]["now"]
    kinds = ["none", "clock", "tident", "tinode", "tree", "tree-stale", "store", "store-stale", "storeid", "fault",
             "live-unread", "live-read", "live-tag", "live-off", "names", "peername", "cleared", "cleared-other", "wait",
             "jauth", "jactive", "hide", "downtime", "debug", "interrupting", "closer", "stall", "nudge", "facts",
             "files", "board-facts", "recorded-stale", "leave", "unrecord", "rerecord",
             # S6.1's mutant sweep: no draw moved a live row's subagents often enough (mutant 3), and none flipped an
             # entry's read of usage.json and then left the session alone for a build (mutant 22)
             "row-agents", "api-error"]
    kind = force or rng.choice(kinds)
    ops = []
    S = snap["sessions"].get(sid) if sid else None
    ver = 1781100000000000000 + step * 1000 + rng.randint(0, 999)

    def tident(inode=0):
        return files_op(snap, "transcript:" + sid, [inode, ver, rng.randint(1, 10 ** 6), 0])
    if sid is None or kind == "none":
        return kind, ops
    if kind == "clock":
        ops = [S_(["world", "now"], rng.choice((now + rng.choice((1, 60, 3600)), float(now) + 0.5, now)))]
    elif kind == "tident":
        ops = tident()
    elif kind == "tinode":
        ops = tident(rng.randint(1, 50))
    elif kind in ("tree", "tree-stale"):
        tr = trees.get(sid)
        if tr is not None and len(tr.get("turns") or []) > 1 and rng.random() < 0.4:
            ops = [T_(sid, ["turns"], tr["turns"][:-1])]
        elif tr is not None and text_atom(tr, rng.random() < 0.5) is not None:
            ti, ai, bi = text_atom(tr, rng.random() < 0.5) or text_atom(tr, True) or text_atom(tr, False)
            old = tr["turns"][ti]["atoms"][ai]["message"]["content"][bi]["text"]
            ops = [T_(sid, ["turns", ti, "atoms", ai, "message", "content", bi, "text"], old + " more")]
        elif tr is not None:
            kept.setdefault("trees", {})[sid] = tr
            ops = [T_(sid, [], None)]
        elif sid in kept.get("trees", {}):
            ops = [T_(sid, [], kept["trees"].pop(sid))]
        if kind == "tree":
            ops += tident()
    elif kind in ("store", "store-stale", "storeid"):
        st = S.get("store")
        if isinstance(st, dict) and kind != "storeid":
            nodes = [(k, n) for k, n in (st.get("nodes") or {}).items() if isinstance(n.get("text"), str)]
            if nodes:
                k, n = rng.choice(nodes)
                ops = [S_(["sessions", sid, "store", "nodes", k, "text"], n["text"] + " (revised)")]
            elif st.get("status"):
                k = rng.choice(sorted(st["status"]))
                ops = [S_(["sessions", sid, "store", "status", k], rng.choice(("working", "blocked", "completed")))]
        if kind != "store-stale":
            ops += files_op(snap, "store:" + sid, [[rng.randint(1, 9), ver, 1], rng.choice((None, [1, ver, 2])), None])
    elif kind == "fault":
        if S.get("store") is not None:
            kept.setdefault("stores", {})[sid] = S["store"]
            ops = [S_(["sessions", sid, "store"], None)]
        elif sid in kept.get("stores", {}):
            ops = [S_(["sessions", sid, "store"], kept["stores"].pop(sid))]
    elif kind.startswith("live"):
        row = snap["liveMap"].get(sid)
        if kind == "live-off":
            if row is not None:
                kept.setdefault("live", {})[sid] = row
                ops = [D_(["liveMap", sid])]
            else:
                ops = [S_(["liveMap", sid], kept.get("live", {}).pop(sid, {"state": "idle", "since": now - 100}))]
        elif row is not None:
            if kind == "live-unread":
                k = rng.choice(("model", "effort", "context", "color", "tokens", "ctxPct"))
                v = {"model": "m%d" % step, "effort": rng.choice(("high", "low")), "context": rng.choice((None, 5000 + step)),
                     "color": rng.choice(("#abcdef", None)), "tokens": step * 100, "ctxPct": rng.choice((12, 12.5))}[k]
                ops = [S_(["liveMap", sid, k], v)]
            elif kind == "live-read":
                k = rng.choice(("state", "since", "authLive", "auth", "authPending", "retryCount", "retryInfo",
                                "subagents", "bgTasks"))
                v = {"state": rng.choice(("idle", "working", "permission", "picker", None)),
                     "since": rng.choice((now - 7, None)), "authLive": rng.choice((True, None)),
                     "auth": rng.choice(("login", "key")), "authPending": rng.choice((True, False)),
                     "retryCount": rng.choice((0, 1, 2)), "retryInfo": rng.choice(({"max": 3, "status": 529}, None, 5)),
                     "subagents": rng.choice(([{"agentId": "a1", "type": "t", "since": 1, "x": 2}], [], None)),
                     "bgTasks": rng.choice(([{"toolUseId": "u1", "type": "bash", "desc": "build", "since": 2}], [],
                                            None))}[k]
                ops = [S_(["liveMap", sid, k], v)]
            elif kind == "live-tag" and isinstance(row.get("since"), int):
                ops = [S_(["liveMap", sid, "since"], float(row["since"]))]
    elif kind == "names":
        if S.get("names") is not None:
            ops = [S_(["sessions", sid, "names"], [str(step), "/x", "#%06x" % rng.randint(0, 0xffffff)])]
        else:
            ops = [S_(["alive", sids.index(sid), "name"], "n%d" % step)]
    elif kind == "peername":
        peers = list(snap.get("peers") or {})
        if peers:
            p = rng.choice(peers)
            ops = [S_(["peers", p, "names"], ["peer-%d" % step, "/x", "#00aa00"])]
    elif kind in ("cleared", "cleared-other"):
        # the list is written in no particular order (production's cleared log is in write order; Python's key sorts
        # each session's slice), each new or repeated id at a random place, so a copy need not sit beside its first
        cur = list(snap["board"].get("cleared") or [])
        mine = [c for c in cur if ":" in c and c.rpartition(":")[0] in snap["sessions"]]
        pool = mine if kind == "cleared" else [c for c in cur if c not in mine]
        new = list(cur)
        draw = rng.random()
        if pool and draw < 0.35:
            # an id already cleared listed a second time (an alive session's, or a peer's or a colonless one): Python's
            # key reads the cleared set, where the duplicate collapses, so nothing moves
            new.insert(rng.randint(0, len(new)), rng.choice(pool))
            kind += "-dup"
        elif len(set(cur)) > 1 and draw < 0.6:
            # the same ids in another order: nothing moves (two distinct ids at least, or no shuffle can differ)
            while new == cur:
                rng.shuffle(new)
            kind += "-order"
        else:
            target = sid if kind == "cleared" else rng.choice(list(snap.get("peers") or {}) + ["nocolon"])
            ident = target + (":g%d" % step if target != "nocolon" else "")
            new.insert(rng.randint(0, len(new)), ident)
        ops = [S_(["board", "cleared"], new)]
    elif kind == "wait":
        ops = [S_(["board", "wmap", sid], {"peer": "peer9", "since": now - step})] if rng.random() < 0.7 else (
            [D_(["board", "wmap", sid])] if sid in (snap["board"].get("wmap") or {}) else [])
    elif kind == "jauth":
        ops = [S_(["board", "jauth", sid], {"mode": "key", "t": step, "note": "n"})]
    elif kind == "jactive":
        cur = set(snap["board"].get("jactive") or [])
        ops = [S_(["board", "jactive"], sorted(cur ^ {sid}))]
    elif kind == "hide":
        f = S.get("flags")
        if f is None or isinstance(f, dict):
            f = dict(f or {})
            f["hideFromFeed"] = not f.get("hideFromFeed")
            ops = [S_(["sessions", sid, "flags"], f)]
    elif kind == "downtime":
        ops = [S_(["board", "downtime"], (snap["board"].get("downtime") or []) + [[now - 9000 + step, now - 8900]])]
    elif kind == "debug":
        ops = [S_(["board", "debug"], not snap["board"].get("debug")), S_(["board", "debugRows"], [])]
    elif kind in ("interrupting", "closer"):
        k = "interrupting" if kind == "interrupting" else "closerPending"
        rec = S["recorded"]
        if k in rec:
            ops = [S_(["sessions", sid, "recorded", k], not rec[k])]
    elif kind == "stall":
        nodes = [n for s in snap["sessions"].values() for n in ((s.get("store") or {}).get("nodes") or {})]
        if nodes:
            g = rng.choice(nodes + ["nobody:g1"])
            ops = [S_(["board", "stalls", g], {"why": rng.choice(("w", "a judge pass is mid-flight")), "since": step})]
    elif kind == "nudge":
        nodes = [n for s in snap["sessions"].values() for n in ((s.get("store") or {}).get("nodes") or {})]
        if nodes:
            g = rng.choice(nodes)
            ops = [S_(["board", "nudgeRecords", g], {"count": rng.choice((0, 1, 2)), "failed": rng.random() < 0.5}),
                   S_(["board", "nudgeTimes", g], [now - step, now - step + 1])]
    elif kind == "facts":
        k = rng.choice(SESSION_FACTS)
        v = {"cut": "c%d" % step, "live": step, "watch": rng.choice((None, {"kind": "watches", "why": "w"})),
             "postal": [[], [[sid, "n%d" % step]]], "reg": ["readable", {"spawnedAt": step}], "anchors": step,
             "userWrite": float(step), "snapDone": step, "hold": rng.choice((None, {}, {"cutT": step, "leaf": "l"})),
             "replayWrote": rng.choice((True, False, None))}[k]
        ops = fact_op(snap, sid, k, v)
    elif kind == "files":
        name = rng.choice(("states:" + sid, "captions:" + sid, "gone:" + sid, "goal:" + rng.choice(
            list(snap.get("peers") or {}) + sids), "usage", "errors"))
        ops = files_op(snap, name, [step, ver, 1, 1])
    elif kind == "board-facts":
        ops = board_fact_op(snap, rng.choice(("auth", "capOpen")), rng.choice(
            (True, False, None, {"window": "five_hour", "resetsAt": now + step})))
        if ops[-1]["path"][-1] == "auth" and isinstance(ops[-1]["value"], dict):
            ops[-1]["value"] = True
        if ops[-1]["path"][-1] == "capOpen" and isinstance(ops[-1]["value"], bool):
            ops[-1]["value"] = None
    elif kind == "recorded-stale":
        rec = S["recorded"]
        if "apiError" in rec:
            ops = [S_(["sessions", sid, "recorded", "apiError"], rng.choice(
                ({"status": 529, "message": "Overloaded"}, None, {"status": 401, "authErr": True})))]
    elif kind == "api-error":
        # the recorded api error flips (one where there was none, none where there was one) and the transcript moves,
        # so the session derives now and its entry starts or stops reading usage.json (the cap offer); the next build,
        # whatever it draws, then shows whether the stored key took `usage` over this entry or the one before it
        rec = S["recorded"]
        if "apiError" in rec:
            ops = [S_(["sessions", sid, "recorded", "apiError"], None if rec["apiError"] else rng.choice(
                ({"status": 529, "message": "Overloaded"}, {"status": 401, "authErr": True})))] + tident()
    elif kind == "row-agents":
        # the live row's subagents, whose (agentId, type, since) the `row` component keys: one added or one's since
        # moved (s derives), or a field no card reads set on one (served)
        row = snap["liveMap"].get(sid)
        if row is not None:
            agents = [dict(a) for a in row.get("subagents") or [] if isinstance(a, dict)] \
                if isinstance(row.get("subagents"), list) else []
            draw = rng.random()
            if not agents or draw < 0.5:
                agents.insert(rng.randint(0, len(agents)), {"agentId": "a%d" % step, "type": rng.choice(
                    ("general-purpose", "explore")), "since": now - step})
            elif draw < 0.75:
                rng.choice(agents)["since"] = now - 1000 - step
                kind += "-since"
            else:
                rng.choice(agents)["lastTool"] = "t%d" % step
                kind += "-unread"
            ops = [S_(["liveMap", sid, "subagents"], agents)]
    elif kind == "grow":
        # never drawn: one step of each just-full world (fuzz_main). The middle session's display name grows by 20 to
        # 200 characters, so it derives (names) and its entry grows wherever the name is shown, and its put passes the
        # bound. Python sheds the next session in alive order (the least recently served); a cache ordered by its puts
        # alone sheds the first session, put before the last one by the cold build, unless a later put reordered them
        i = len(sids) // 2
        name = snap["alive"][i].get("name")
        ops = [S_(["alive", i, "name"], (name if isinstance(name, str) else "") + "-" + "x" * rng.randint(20, 200))]
    elif kind == "leave":
        if len(sids) > 1:
            i = sids.index(sid)
            kept.setdefault("rows", []).append((i, snap["alive"][i]))
            ops = [S_(["alive"], [r for r in snap["alive"] if r["sid"] != sid])]
        elif kept.get("rows"):
            i, row = kept["rows"].pop()
            rows = list(snap["alive"])
            rows.insert(min(i, len(rows)), row)
            ops = [S_(["alive"], rows)]
    elif kind == "unrecord":
        rec = S["recorded"]
        ks = [k for k in ("interrupting", "healSessionTops", "sessionAwaiting", "bgLive") if k in rec]
        if ks:
            k = rng.choice(ks)
            kept.setdefault("rec", {})[(sid, k)] = rec[k]
            ops = [D_(["sessions", sid, "recorded", k])] + (tident() if rng.random() < 0.6 else [])
    elif kind == "rerecord":
        for (s, k), v in list(kept.get("rec", {}).items()):
            if s in snap["sessions"]:
                ops.append(S_(["sessions", s, "recorded", k], v))
                del kept["rec"][(s, k)]
                break
        else:
            # nothing stashed (an earlier `unrecord` in only a few worlds): stash one now with the transcript moved, so
            # the session misses and its derivation raises (its previous cards served when it has an entry), and bring
            # it back at the next step whatever that step draws (S6.1's second review: rerecord was almost always a no-op)
            rec = S["recorded"]
            ks = [k for k in ("interrupting", "healSessionTops", "sessionAwaiting", "bgLive") if k in rec]
            if ks:
                k = rng.choice(ks)
                kept.setdefault("rec", {})[(sid, k)] = rec[k]
                ops = [D_(["sessions", sid, "recorded", k])] + tident()
                kind = "rerecord-stash"
                kept["back"] = (sid, k)
    if kind not in ("leave",) and kept.get("rows") and rng.random() < 0.15:
        i, row = kept["rows"].pop()
        rows = list(snap["alive"])
        rows.insert(min(i, len(rows)), row)
        ops.append(S_(["alive"], rows))
    return kind, ops


def seed_cleared(rng, snap):
    """Cleared ids under the world's sessions and peers from the start, in half the worlds (the generator clears only
    parked and notice ids, which strip_files drops), in no particular order, so a later `cleared` step can list one a
    second time or reorder them: only a second copy of an id the stored key already holds moves a slice that is not
    deduplicated (a duplicate present from the id's first appearance moves nothing a set would not), and only a slice
    of two or more ids can come in another order, so one alive session gets two or three of its own."""
    if rng.random() >= 0.5:
        return
    owners = sids_of(snap) + sorted(snap.get("peers") or {})
    ids = set(snap["board"].get("cleared") or [])

    def one(o):
        facts = snap["sessions"].get(o) or (snap.get("peers") or {}).get(o) or {}
        nodes = [n for n in ((facts.get("store") or {}).get("nodes") or {}) if n.startswith(o + ":")]
        ids.add(rng.choice(nodes) if nodes and rng.random() < 0.5 else "%s:g9%d" % (o, rng.randint(0, 9)))
    if sids_of(snap):
        o = rng.choice(sids_of(snap))
        for _ in range(rng.randint(2, 3)):
            one(o)
    for _ in range(rng.randint(0, 2)):
        one(rng.choice(owners))
    ids = sorted(ids)                                   # (a set's order is the hash seed's: sorted, then shuffled)
    rng.shuffle(ids)
    snap["board"]["cleared"] = ids


def seed_usage(rng, snap):
    """usage.json's identity recorded from the start in half the worlds. The generator records no file identities, so
    the key's `usage` component was None whether or not an entry read usage.json, and a stored key that took `usage`
    over the wrong entry (S6.1's mutant 22) moved nothing; only a `files` draw naming usage.json made it matter."""
    if rng.random() >= 0.5:
        return
    if not isinstance(snap.get("keyFacts"), dict):
        snap["keyFacts"] = {}
    if not isinstance(snap["keyFacts"].get("files"), dict):
        snap["keyFacts"]["files"] = {}
    snap["keyFacts"]["files"]["usage"] = [rng.randint(1, 99), 1781000000000000000, 1, 1]


def memo_valid(snap):
    """Mirror memo::validate's refusals that a generated world could meet (the generator then redraws)."""
    for sid, S in snap["sessions"].items():
        own = bool(((S.get("flags") or {}) if isinstance(S.get("flags"), dict) else {}).get("hideFromFeed"))
        f = S.get("flags")
        if f is None:
            f = ((snap.get("peers") or {}).get(sid) or {}).get("flags")
        merged = bool((f if isinstance(f, dict) else {}).get("hideFromFeed"))
        if own != merged:
            return False
    return True


# ───────────────────────────── the Rust side ─────────────────────────────
RUST_SUMMARY = re.compile(r"^memo (?:edge set|cases)(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) steps, (\d+) builds "
                          r"compared \((\d+) session decisions: (\d+) hits, (\d+) misses, (\d+) raises\), (\d+) memo-served "
                          r"builds equal to their fresh build, (\d+) stale where Python's are, (\d+) differences ")


def rust_binary(given):
    if given:
        return given
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    path = os.path.join(rust, "target", "release", "romp-spike")
    if not os.path.exists(path):
        raise SystemExit("dump_memo: no %s; build it first (cargo build --release -j 4 -p romp-spike) or pass --bin" % path)
    return path


def run_rust(binary, path, plant):
    p = subprocess.run([binary, "memo", path] + (["--plant", plant] if plant else []), capture_output=True, text=True)
    out = p.stdout.splitlines()
    for line in out:
        print("  rust: " + line)
    summary = [m for m in map(RUST_SUMMARY.match, out) if m]
    if p.returncode not in (0, 1) or len(summary) != 1:
        print("dump_memo: romp-spike memo exited %d without its summary line: %s" % (p.returncode, p.stderr.strip()[-2000:]),
              file=sys.stderr)
        return None, p.returncode
    return list(map(int, summary[0].groups())), p.returncode


def write_doc(path, cases, generated_by):
    doc = {"schema": SCHEMA, "python": platform.python_version(), "generatedBy": generated_by, "cases": cases}
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")


def tally(cases):
    n = {"steps": 0, "hits": 0, "misses": 0, "raises": 0, "stale": 0, "freshEqual": 0, "skipped": 0, "skipKeys": {},
         "skipAlive": 0, "skipAt": []}
    for c in cases:
        for i, st in enumerate(c["steps"]):
            n["steps"] += 1
            e = st["expect"]
            fr = e.get("fresh", {})
            if fr.get("frameDiffers"):
                n["skipped"] += 1
                for k in fr.get("keys") or ["(unrecorded)"]:
                    n["skipKeys"][k] = n["skipKeys"].get(k, 0) + 1
                n["skipAlive"] += any(op["at"] == "snapshot" and op["path"] == ["alive"] for op in st["ops"])
                n["skipAt"].append("%s step %d" % (c["name"], i))
            for _sid, d in e["decisions"]:
                n["hits"] += 1 if d.get("hit") else 0
                n["misses"] += 1 if "miss" in d else 0
                n["raises"] += 1 if "raise" in d else 0
            if e.get("freshEqualsMemo"):
                n["freshEqual"] += 1
            elif "canonSha1" in e and "canonSha1" in fr:
                n["stale"] += 1                         # the memo build built, and its bytes are not the fresh build's
    return n


def print_skips(n, at=False):
    """The fresh comparisons skipped because Python's fresh build passes another frame through, and why."""
    print("  fresh comparisons skipped: %d (frame keys that differ: %s; %d in steps that change the alive set)%s" % (
        n["skipped"], json.dumps(dict(sorted(n["skipKeys"].items()))), n["skipAlive"],
        (": " + ", ".join(n["skipAt"])) if at and n["skipAt"] else ""))


def setup(tmp):
    l7 = load_l7()
    l6, l5, dw, km, rec, root = l7.load_kernel(tmp)
    return l7, l6, l5, dw, km, rec


LRU_BOUND = 18000


def lru_steps(r):
    """The memo just full: which entry the byte bound sheds depends on the gets (S6.1's mutant sweep: mutant 43, a get
    that does not move the entry to the LRU tail, survived both checks, since no case shed an entry that had been served
    more recently than another was put). The bound, 18,000 bytes, holds the small world's four settled entries (17,470:
    s001 9,631, s002 4,257, s003 2,891, s004 691). After the peers settle, s001 and s002 hold the latest puts and s003
    and s004 the oldest (the cold build's), while every build serves all four in alive order. Then s001's entry grows
    past the bound at its put: Python sheds the least recently served, s002 (served in the last build after s001, which
    this build has just served), where a cache ordered by puts alone would shed s003. The steps check that Python
    did."""
    snap = r.snap
    s1, s2 = sids_of(snap)[:2]
    name = snap["alive"][0]["name"]
    steps = [r.step([], "the cold build under an 18,000-byte bound: the four entries (17,470 bytes) fit"),
             r.step([], "again: s001 and s002 derive once more (their peers settle) and are put again, after s003 and "
                        "s004; every session is served in alive order, s001 first")]
    e = steps[-1]["expect"]
    if e["memo"] != {"entries": 4, "evict": 0} or [d for _s, d in e["decisions"]] != [
            {"miss": ["peers"]}, {"miss": ["peers"]}, {"hit": True}, {"hit": True}]:
        raise SystemExit("dump_memo: the just-full case's second build is not s001 and s002 re-put, all four held: %r"
                         % e)
    steps.append(r.step([S_(["alive", 0, "name"], name + "-" + "x" * 200)],
                        "s001's display name grows by 201 characters (1,608 bytes of entry): s001 derives (names) and "
                        "its put passes the bound, which sheds the least recently served entry: s002 (served after "
                        "s001 in the last build, and s001 has just been served again), not s003, the least recently "
                        "put; each later session then finds its entry shed in turn (the LRU's worst case, a cyclic "
                        "sweep over more than fits)"))
    e = steps[-1]["expect"]
    if dict(e["decisions"]).get(s2) != {"miss": ["cold"]}:
        raise SystemExit("dump_memo: the just-full case's growth did not shed s002 first: %r" % e["decisions"])
    steps.append(r.step([S_(["alive", 0, "name"], name)],
                        "s001's name back: s001 derives cold (the shedding reached it), s002 once more (its cold "
                        "derivation in the last build left its peer unsettled), s003 and s004 are served, and the four "
                        "fit again"))
    e = steps[-1]["expect"]
    if e["memo"] != {"entries": 4, "evict": 0} or dict(e["decisions"]).get(s1) != {"miss": ["cold"]}:
        raise SystemExit("dump_memo: the just-full case does not settle back to four entries: %r" % e)
    return steps


def edge_main(dump, out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-memo-edge-")
    try:
        l7, l6, _l5, dw, km, rec = setup(tmp)
        snap, trees = dw.load_dump(os.path.realpath(dump))
        if snap["world"]["name"] != "small":
            raise SystemExit("dump_memo: --edge takes the small world's dump, not %r" % snap["world"]["name"])
        base = (copy.deepcopy(snap), copy.deepcopy(trees))
        cases = []
        STORE_KEEP.clear()
        s4 = sids_of(snap)[3]
        STORE_KEEP[s4] = copy.deepcopy(snap["sessions"][s4]["store"])
        r = Runner(l7, l6, dw, km, snap, trees)
        steps = run_edge(r)
        cases.append({"name": "the small world, scripted", "snapshot": base[0], "trees": base[1],
                      "memoBound": r.memo_bound, "segBound": r.seg_bound, "steps": steps})
        # the byte bound: entries shed least recently served first
        snap2, trees2 = copy.deepcopy(base[0]), copy.deepcopy(base[1])
        r2 = Runner(l7, l6, dw, km, snap2, trees2, memo_bound=15000)
        s1, s2, s3, s4 = sids_of(snap2)[:4]
        steps2 = [r2.step([], "the cold build under a 15,000-byte bound (the four entries are 17,470): entries shed as "
                              "they are put, the least recently served first"),
                  r2.step([], "again: the shed sessions derive cold"),
                  r2.step(files_op(snap2, "transcript:" + s3, [0, 5, 5, 0]), "s003 moves: it derives and is put"),
                  r2.step([], "again")]
        cases.append({"name": "the small world under a small byte bound", "snapshot": base[0], "trees": base[1],
                      "memoBound": 15000, "segBound": r2.seg_bound, "steps": steps2})
        # the LRU's order: a get is a use (S6.1's mutant sweep, mutant 43)
        snap3, trees3 = copy.deepcopy(base[0]), copy.deepcopy(base[1])
        r3 = Runner(l7, l6, dw, km, snap3, trees3, memo_bound=LRU_BOUND)
        cases.append({"name": "the small world with the memo just full", "snapshot": base[0], "trees": base[1],
                      "memoBound": LRU_BOUND, "segBound": r3.seg_bound, "steps": lru_steps(r3)})
        unknown = r.unknown + r2.unknown + r3.unknown
        if unknown:
            raise SystemExit("dump_memo: the key stat files the harness does not answer: %r" % sorted(set(unknown))[:5])
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            raise SystemExit("dump_memo: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]))
        write_doc(out, cases, "tools/rust-spike/dump_memo.py --edge DUMP(small)")
        n = tally(cases)
        print("memo edge set: %d cases, %d steps, %d hits, %d misses, %d raises; %d memo-served builds equal their fresh "
              "build, %d stale; %s (%.2f MB)" % (len(cases), n["steps"], n["hits"], n["misses"], n["raises"],
                                                 n["freshEqual"], n["stale"], out, os.path.getsize(out) / 1e6))
        print_skips(n, at=True)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def dump_main(dump, binary, out, plant):
    """A dump's default sequence (a cold build, the peers' second build, served builds, one input of each kind moved),
    the case referring to the dump directory, then romp-spike memo over it."""
    tmp = tempfile.mkdtemp(prefix="romp-spike-memo-dump-")
    try:
        l7, l6, _l5, dw, km, rec = setup(tmp)
        d = os.path.realpath(dump)
        snap, trees = dw.load_dump(d)
        sids = sids_of(snap)
        big = max(sids, key=lambda s: len((trees.get(s) or {}).get("turns") or []))
        r = Runner(l7, l6, dw, km, snap, trees)
        now = snap["world"]["now"]
        plan = [([], "the cold build"), ([], "again (peers settle)"), ([], "served"),
                ([S_(["world", "now"], now + 60)], "the clock moves"),
                (files_op(snap, "transcript:" + big, [0, 1, 1, 0]), "the largest session's transcript moves"),
                (files_op(snap, "transcript:" + big, [0, 2, 1, 0]), "and again"),
                ([S_(["board", "downtime"], [[now - 9000, now - 8000]])], "a host suspension (every session)"),
                ([], "served")]
        steps = []
        for ops, note in plan:
            steps.append(r.step(ops, note))
        case = {"name": "%s, default sequence" % snap["world"]["name"], "dump": d, "memoBound": r.memo_bound,
                "segBound": r.seg_bound, "steps": steps}
        path = out or os.path.join(tmp, "memo_dump.json")
        write_doc(path, [case], "tools/rust-spike/dump_memo.py --dump %s" % snap["world"]["name"])
        if r.unknown:
            raise SystemExit("dump_memo: unanswered stats: %r" % sorted(set(r.unknown))[:5])
        n = tally([case])
        print("%s: %d steps, %d hits, %d misses, %d raises, %d memo-served builds equal their fresh build" % (
            snap["world"]["name"], n["steps"], n["hits"], n["misses"], n["raises"], n["freshEqual"]))
        g, rc = run_rust(rust_binary(binary), path, plant)
        if g is None:
            return 2
        diffs = g[-1]
        if plant:
            print("planted %s: %s, %d differences" % (plant, "caught" if diffs else "NOT CAUGHT", diffs))
            return 0 if diffs else 1
        return 0 if diffs == 0 and rc == 0 else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def fuzz_main(n, seed, nsteps, plant, binary, out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-memo-fuzz-")
    try:
        t0 = time.monotonic()
        l7, l6, l5, dw, km, rec = setup(tmp)
        l3 = l5.load_l4().load_l3()
        l2 = l3.load_l2()
        rng = random.Random(seed)
        cases, kinds, bounds = [], {}, {}
        serial = 0
        while len(cases) < n:
            snap, trees = l7.fz_world7(rng, km, l6, l5, l3, l2, serial)
            serial += 1
            strip_files(snap)
            if not memo_valid(snap):
                continue
            seed_cleared(rng, snap)
            seed_usage(rng, snap)
            base = (copy.deepcopy(snap), copy.deepcopy(trees))
            bound = rng.choice((None, None, None, 4000, 12000))
            # the memo just full, in half the worlds of three sessions: the bound is the bytes the adoption build's
            # entries took, and the first random step (`grow`) makes the middle session's entry grow past it. Which
            # entry its put sheds is the LRU order's (a get is a use; S6.1's mutant 43). Only three entries can tell
            # that order from the order of the puts (of two, the one not being put is the only one to shed), and only
            # an entry growing past the bound shows it: every build serves the sessions in one order, so a memo either
            # holds them all or, past the bound, sheds each in turn (the LRU's cyclic worst case), whatever the order
            full = len(sids_of(snap)) >= 3 and rng.random() < 0.5
            r = Runner(l7, l6, dw, km, snap, trees, memo_bound=None if full else bound)
            if full:
                r.memo_bound = r.adopted_bytes
            label = "full" if full else str(bound)
            bounds[label] = bounds.get(label, 0) + 1
            grow_at = 2 if full else None          # the first random step, right after the peers settle
            steps, kept = [], {}
            for k in range(nsteps):
                kind, ops = ("none", []) if k < 2 else fz_ops(rng, snap, trees, k, kept,
                                                               force="grow" if k == grow_at else None)
                kinds[kind] = kinds.get(kind, 0) + 1
                steps.append(r.step(ops, "step %d: %s" % (k, kind)))
            if kept.get("backs"):
                kinds["rerecord-back"] = kinds.get("rerecord-back", 0) + kept["backs"]
            if r.unknown:
                raise SystemExit("dump_memo: unanswered stats: %r" % sorted(set(r.unknown))[:5])
            cases.append({"name": "fuzz seed %d world %d" % (seed, len(cases)), "snapshot": base[0], "trees": base[1],
                          "memoBound": r.memo_bound, "segBound": r.seg_bound, "steps": steps})
        path = out or os.path.join(tmp, "memo_fuzz.json")
        write_doc(path, cases, "tools/rust-spike/dump_memo.py --fuzz %d --seed %d --steps %d" % (n, seed, nsteps))
        t = tally(cases)
        print("memo fuzz seed %d: %d worlds x %d steps, %d hits, %d misses, %d raises, %d memo-served builds equal their "
              "fresh build, %d stale; made and answered by Python in %.1f s, %.1f MB" % (
                  seed, n, nsteps, t["hits"], t["misses"], t["raises"], t["freshEqual"], t["stale"],
                  time.monotonic() - t0, os.path.getsize(path) / 1e6))
        print("  step kinds: %s" % json.dumps(dict(sorted(kinds.items()))))
        print("  memo bounds (worlds): %s" % json.dumps(dict(sorted(bounds.items()))))
        print_skips(t)
        g, rc = run_rust(rust_binary(binary), path, plant)
        if g is None:
            return 2
        diffs = g[-1]
        if plant:
            print("memo fuzz seed %d, planted %s: %s, %d differences" % (seed, plant, "caught" if diffs else "NOT CAUGHT",
                                                                      diffs))
            return 0 if diffs else 1
        ok = diffs == 0 and rc == 0
        print("memo fuzz seed %d: %s, %d differences" % (seed, "OK" if ok else "FAILED", diffs))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--edge", nargs=2, metavar=("DUMP", "OUT"))
    ap.add_argument("--dump")
    ap.add_argument("--fuzz", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--plant", choices=PLANTS)
    ap.add_argument("--bin")
    ap.add_argument("--out")
    ap.add_argument("--embed", action="store_true", help="carry Python's wire strings (for debugging a difference)")
    a = ap.parse_args(argv)
    global EMBED
    EMBED = a.embed
    if a.edge:
        return edge_main(*a.edge)
    if a.dump:
        return dump_main(a.dump, a.bin, a.out, a.plant)
    if a.fuzz:
        return fuzz_main(a.fuzz, a.seed, a.steps, a.plant, a.bin, a.out)
    ap.print_usage()
    return 2


if __name__ == "__main__":
    sys.exit(main())
