#!/usr/bin/env python3
"""The timeline's two floors that grew with history (2026-10-06, the build-cost design's D3).

Every derivation of a timeline lane sorted every caption row of its session and made a mark per row
(_derive_judging_marks), every build compared each of those marks against the horizon (_judging_assemble), memo hit
or not, and every build sorted each lane's states log twice and walked every transition (_state_intervals). The
captioner's marks are now held per session on the captions mapping's reader version (#118) and carried across an
append by placing only the appended rows; the assembly bisects the horizon; and the sorted transitions are held on the
states log's fold cursor, with each state set's intervals.

What must not change is the answer. The code as it stood at e1bdd273a is copied below as the REFERENCE, and the
equality classes compare against it:
  MarksProperty  every row of seeded synthetic captions files (drifted ids, rewrites of a row that keep, move, add or
                 drop its time, other grains, '#p' rows, ties, rewrites of the whole file), appended one row or a batch
                 at a time: the served marks equal the reference marks, the assembly at sampled horizons (marks' own
                 times and their neighbors) equals the reference assembly, and a list once served never changes; a
                 second file mixes in times the bisection cannot order (NaN, booleans, strings), where a raise is part
                 of the answer;
  FrameWalk      a seeded walk over two live lanes of caption and states appends and rewrites, transcript appends,
                 clock moves (some putting the horizon exactly on a caption's or a transition's time), and a lane that
                 departs and returns: after every step every lane's frame (its lane row, its bars, its judging marks)
                 is byte-identical to a build through the reference code with every lane memo cold.
Both are green on the code before the change by construction (the reference is that code): they are guards, and the
change's mutants turn them red. The classes below pin the new rules and are red before the change:
  MarksMemo      a version that stands serves the same list; an append extends it and leaves the served list as it
                 was; a rewritten row whose time stands is replaced in place, and one whose time moved or appeared
                 builds whole; a rewrite of the file builds whole; a mapping that is not the held one is derived and not
                 held; the held list refuses every write; the assembly bisects without reading a mark; times the
                 bisection cannot order fall back to the filter; the lane's numeric check reads a bit;
  StatesMemo     two state sets over two builds sort once; an append, a rewrite and a cleared cursor sort again; a last
                 row without its newline is not held; the horizon cut equals the walk at every edge; a log the bisection
                 cannot order walks as before;
  Bounds         both tables are byte-bounded LRU tables, an entry over the bound alone is not held, sheds nothing and
                 counts under overBound (since the follow-up to the review: before it, under evict, and in the states
                 table under sort too), a full build drops the lanes it no longer draws while a live-only build drops
                 none, and a lane the dead-lane memo takes over leaves the marks table and keeps its states entry
                 (since the same follow-up);
  PerfWiring     memos.judgingMarks and memos.stateIntervals carry their keys, and docs/reference.md names every one.
The follow-up to the review posted after the change merged adds cases for code the classes above left untested, each
green at the code it covers and red under its own mutant: in MarksMemo a NaN-timed append and a held row rewritten to
NaN build whole, and a cutoff that is not a plain number keeps the filter; in MarksMemo and StatesMemo a sort that
raises counts nowhere, as the reference says; in Bounds a hit makes an entry the newest in either table;
BoundEnvironment pins both bounds' environment read; and FrameWalk's clock never moves backward.
The same-second fix (2026-10-28) changed one rule the reference states: the states log's transitions sort on their
time alone, so rows that share a second keep the log's order (the reference's sort carries the same key), and
SameSecondOrder pins it on both of the sort's roads and on the lanes a build sends; it is red before the fix.
Synthetic data only: placeholder uuids, the notes-api demo names, invented captions."""
import contextlib
import copy
import json
import os
import pickle
import random
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time. This root is the module's own,
# outside the runner's belt, so it pins the per-session hosts off itself (CLAUDE.md, 2026-09-11); so does every
# per-test root below.
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
with open(os.path.join(_XDG, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off\n")
km = load_source("romp_kernel_timeline_floor_memos", os.path.join(BIN, "romp-kernel"))
em = km.em

SID = "55555555-6666-7777-8888-00000000d301"     # this module's own synthetic sids
SID2 = "55555555-6666-7777-8888-00000000d302"
SID3 = "55555555-6666-7777-8888-00000000d303"
WEB = "55555555-6666-7777-8888-00000000d311"     # the walk's two lanes
API = "55555555-6666-7777-8888-00000000d312"
T = 1_781_000_000


# ── the REFERENCE: e1bdd273a's code, verbatim but for the module prefix ──────────────────────────────────────────
def ref_cap_marks(sid, caps):
    """The captioner's marks as _derive_judging_marks made them inline."""
    caps_in = sorted((c for c in caps.values() if c.get("t")), key=lambda c: c["t"])
    return [{"judge": "captioner", "sid": sid, "t": c["t"],
             "kind": c.get("grain", "segment"), "text": c.get("caption", "")} for c in caps_in]


def ref_judging_assemble(cap_marks, other_marks, t0, out, stamp=False):
    kept = [m for m in cap_marks if m["t"] >= t0]
    if stamp:
        out.extend(dict(m, _h=m["t"]) for m in kept[-km.JUDGE_CAP_LIMIT:])
        out.extend(dict(m, _h=ft) for ft, m in other_marks if ft >= t0)
        return
    out.extend(kept[-km.JUDGE_CAP_LIMIT:])
    out.extend(m for ft, m in other_marks if ft >= t0)


def ref_state_intervals(sid, want, now):
    want = {want} if isinstance(want, str) else set(want)
    ev = sorted(km._fold_records(km._state_ev_cache, km.jd.STATE / "states" / (sid + ".jsonl"), list, km._state_ev_step,
                                 ckpt="stateIntervals"), key=lambda e: e[0])   # time only: a tie keeps the log's order
    cutoff, out = now - km.TL_HORIZON, []
    for i, (t, st) in enumerate(ev):
        if st not in want:
            continue
        open_now = i + 1 >= len(ev)
        end = now if open_now else ev[i + 1][0]
        if end < cutoff:
            continue
        out.append([max(t, cutoff), end, True] if open_now else [max(t, cutoff), end])
    return out


def ref_numeric(cap_marks):
    """The lane's numeric check on the captioner's marks, as _lane_segments ran it inline."""
    return not any(not isinstance(m["t"], (int, float)) for m in cap_marks)


def outcome(fn, *a):
    """What a call answered, as bytes: ("ok", its JSON), or ("raise", the exception's type name)."""
    try:
        return ("ok", json.dumps(fn(*a)))
    except Exception as e:                       # noqa: BLE001  the reference's raise is part of the answer
        return ("raise", type(e).__name__)


def assembled(fn, marks, t0):
    out = []
    fn(marks, [], t0, out)
    return out


_MISSING = object()


def _clear_floors():
    for nm in ("_JUDGING_MARKS_MEMO", "_STATE_IV_MEMO"):
        t = getattr(km, nm, None)
        if t is not None:
            t.clear()
    for nm in ("_JUDGING_MARKS_STATS", "_STATE_IV_STATS"):
        st = getattr(km, nm, None)
        if st is not None:
            for k in st:
                st[k] = 0


class _Root(unittest.TestCase):
    """A fresh state root per test (jd._rebind_state), its session hosts pinned off, both floor tables emptied, every
    write stamped with an mtime of its own so the readers see each version even when two have one size."""

    def setUp(self):
        self._saved_state = km.jd.STATE
        self._td = tempfile.mkdtemp()
        root = Path(self._td)
        km.jd._rebind_state(root)
        (root / "session-hosts").write_text("off\n")
        (root / "captions").mkdir(parents=True, exist_ok=True)
        (root / "states").mkdir(parents=True, exist_ok=True)
        self._saved_consts = {nm: getattr(km, nm) for nm in ("TL_HORIZON", "JUDGE_CAP_LIMIT", "JUDGING_MARKS_MEMO_BYTES",
                                                             "STATE_IV_MEMO_BYTES") if hasattr(km, nm)}
        self.mtime = int(time.time()) - 500_000
        _clear_floors()

    def tearDown(self):
        for nm, v in self._saved_consts.items():
            setattr(km, nm, v)
        for p in list((km.jd.STATE / "captions").glob("*.jsonl")) + list((km.jd.STATE / "states").glob("*.jsonl")):
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(str(p))
            km._CAPTION_ROWS_MEMO.pop(str(p), None)
            km._state_ev_cache.pop(str(p), None)
        _clear_floors()
        km.jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    # files
    def cap_path(self, sid=SID):
        return km.jd.CAPDIR / (sid + ".jsonl")

    def states_path(self, sid=SID):
        return km.jd.STATE / "states" / (sid + ".jsonl")

    def _stamp(self, p):
        self.mtime += 7
        os.utime(p, (self.mtime, self.mtime))

    def write(self, p, rows, tail=""):
        with open(p, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows) + tail)
        self._stamp(p)

    def append(self, p, rows, tail=""):
        with open(p, "a") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows) + tail)
        self._stamp(p)

    def derive(self, sid=SID):
        """The captions mapping and the captioner's marks a lane derivation reads (both exist before the change)."""
        caps = km._captions(sid)
        return caps, km._derive_judging_marks(sid, caps, {}, None)[0]

    def marks_equal(self, label, sid, caps, marks):
        self.assertEqual(json.dumps(marks), json.dumps(ref_cap_marks(sid, dict(caps))), "%s: the marks" % label)

    def stats(self, nm):
        return dict(getattr(km, nm))


def crow(t0h, grain, t, caption, p=False, sid=SID):
    """A caption row for the segment id <sid>:<t0>:<hash> (its message caption with p), time t (None: no time)."""
    t0, h = t0h
    r = {"id": "%s:%d:%s%s" % (sid, t0, h, "#p" if p else ""), "grain": grain, "caption": caption}
    if t is not None:
        r["t"] = t
    return r


# ── MarksProperty ────────────────────────────────────────────────────────────────────────────────────────────────
class _CaptionsWalk:
    """A seeded stream of captions-file operations: (kind, rows), kind "append" or "rewrite" (the whole file anew)."""

    def __init__(self, rng, sid, odd=False):
        self.rng, self.sid, self.odd = rng, sid, odd
        self.rows = []                            # every row the current file holds, in file order
        self.keys = []                            # (t0, hash) of the segments written so far
        self.n = 0

    def time(self):
        r = self.rng.random()
        if r < 0.07:
            return None                           # no time: no mark
        if r < 0.10:
            return 0                              # time zero: no mark either
        t = T + self.rng.randrange(0, 300) * 5    # few distinct values: ties
        if r < 0.18:
            return t + 0.5                        # a float
        if self.odd:
            if r < 0.23:
                return float("nan")
            if r < 0.26:
                return True
            if r < 0.28:
                return str(t)                     # a time the sort cannot compare with a number
        return t

    def fresh_row(self):
        rng = self.rng
        self.n += 1
        if self.keys and rng.random() < 0.3:      # a drifted id: a key already written, at another start
            t0, h = rng.choice(self.keys)
            key = (t0 + rng.choice((1, -1, 7, -48, 150)), h)
        else:
            key = (T + rng.randrange(0, 300) * 5, "%08x" % rng.randrange(1 << 32))
        self.keys.append(key)
        grain = rng.choice(("segment", "segment", "turn", "prompt", None))
        r = crow(key, grain or "segment", self.time(), "caption %d" % self.n, p=rng.random() < 0.3, sid=self.sid)
        if grain is None:
            del r["grain"]                        # no grain: the mark's kind defaults to segment
        return r

    def rewritten_row(self):
        rng = self.rng
        written = [r for r in self.rows if isinstance(r, dict) and r.get("id")]
        if not written:
            return self.fresh_row()
        self.n += 1
        old = dict(rng.choice(written))
        how = rng.random()
        if how < 0.4:
            pass                                  # the same time: a live caption rewritten as its segment grows
        elif how < 0.6:
            old["t"] = self.time()                # the time moves (or goes)
            if old["t"] is None:
                del old["t"]
        elif how < 0.75:
            old.pop("t", None)                    # the time goes
        else:
            old["grain"] = rng.choice(("segment", "turn", "prompt"))
        old["caption"] = "caption %d (rewritten)" % self.n
        return old

    def one(self):
        rng = self.rng
        r = rng.random()
        if r < 0.02:
            return [rng.choice([[1, 2], {"caption": "no id"}, {"id": "", "caption": "an empty id"}])]   # skipped by the fold
        if self.rows and r < 0.3:
            return [self.rewritten_row()]
        return [self.fresh_row()]

    def next(self):
        rng = self.rng
        r = rng.random()
        if self.rows and r < 0.04:
            keep = [x for x in self.rows if rng.random() < 0.8]
            rng.shuffle(keep)
            self.rows = keep + self.one()
            return "rewrite", list(self.rows)
        rows = []
        for _ in range(1 if r < 0.75 else rng.randrange(2, 6)):
            rows.extend(self.one())
            if rows and rng.random() < 0.15 and isinstance(rows[-1], dict) and rows[-1].get("id"):
                again = dict(rows[-1], caption="caption %d (twice in one append)" % self.n)
                rows.append(again)                # an id written twice inside one append
        self.rows.extend(rows)
        return "append", rows


class MarksProperty(_Root):
    def walk(self, seed, steps, odd):
        rng = random.Random(seed)
        w = _CaptionsWalk(rng, SID, odd=odd)
        served = []
        path = self.cap_path()
        self.write(path, [])
        for i in range(steps):
            kind, rows = w.next()
            (self.write if kind == "rewrite" else self.append)(path, rows)
            caps = km._captions(SID)
            label = "seed %d step %d (%s of %d rows)" % (seed, i, kind, len(rows))
            got = outcome(lambda: km._derive_judging_marks(SID, caps, {}, None)[0])
            want = outcome(ref_cap_marks, SID, dict(caps))
            self.assertEqual(got, want, label)
            if got[0] != "ok":
                continue
            marks = km._derive_judging_marks(SID, caps, {}, None)[0]
            ref = ref_cap_marks(SID, dict(caps))
            times = [m["t"] for m in ref if isinstance(m["t"], (int, float)) and m["t"] == m["t"]]
            probes = {0, T - 1, T + 10_000}
            for t in rng.sample(times, min(6, len(times))) + times[-2:]:
                probes.update((t, t - 1, t + 1, t - 0.25, t + 0.25))
            for limit in (km.JUDGE_CAP_LIMIT, 3):
                km.JUDGE_CAP_LIMIT = limit
                for t0 in sorted(probes):
                    self.assertEqual(outcome(assembled, km._judging_assemble, marks, t0),
                                     outcome(assembled, ref_judging_assemble, ref, t0),
                                     "%s: the assembly at horizon %r, cap %d" % (label, t0, limit))
            km.JUDGE_CAP_LIMIT = self._saved_consts["JUDGE_CAP_LIMIT"]
            served.append((marks, json.dumps(marks)))
            for m, snap in served[-25:]:
                self.assertEqual(json.dumps(m), snap, "%s: a list served earlier is as it was served" % label)

    def test_every_row_of_a_plain_file_gives_the_reference_marks_and_assembly(self):
        for seed in (1, 2, 3):
            self.walk(seed, 160, odd=False)
            _clear_floors()

    def test_times_the_bisection_cannot_order_give_the_reference_answer_raise_included(self):
        for seed in (11, 12):
            self.walk(seed, 90, odd=True)
            _clear_floors()


# ── MarksMemo ────────────────────────────────────────────────────────────────────────────────────────────────────
A, B, C, D = (T, "aaaa1111"), (T + 100, "bbbb2222"), (T + 200, "cccc3333"), (T + 300, "dddd4444")


def first_rows():
    return [crow(A, "segment", T, "first"),
            crow(B, "segment", T + 100, "second"),
            crow(B, "prompt", T + 100, "the second's message", p=True),     # a tie with the row before
            crow(C, "turn", T + 200, "a turn rollup"),
            crow(D, "segment", None, "no time"),
            crow((T + 50, A[1]), "segment", T + 50, "a drifted id of A's key")]


class MarksMemo(_Root):
    def setUp(self):
        super().setUp()
        self.write(self.cap_path(), first_rows())

    def test_a_version_that_stands_serves_the_same_list(self):
        caps1, m1 = self.derive()
        caps2, m2 = self.derive()
        self.assertIs(caps2, caps1, "(the premise) the captions mapping stands")
        self.assertIs(m2, m1, "the same marks list, not a sort of every row again")
        self.marks_equal("held", SID, caps2, m2)
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["build"], st["hit"], st["extend"]), (1, 1, 0))

    def test_an_append_extends_the_held_list_and_leaves_the_served_one_as_it_was(self):
        caps1, m1 = self.derive()
        before = json.dumps(m1)
        self.append(self.cap_path(), [crow((T + 400, "eeee5555"), "segment", T + 100, "a new row tied with B"),
                                      crow((T + 500, "ffff6666"), "segment", T - 50, "a new row before every other"),
                                      crow((T + 600, "abab7777"), "prompt", None, "a new row with no time")])
        caps2, m2 = self.derive()
        self.assertIsNot(caps2, caps1)
        self.marks_equal("after the append", SID, caps2, m2)
        self.assertEqual(json.dumps(m1), before, "the list served before the append is as it was served")
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["build"], st["extend"]), (1, 1), "the appended rows were placed, nothing was sorted whole")
        tied = [m["text"] for m in m2 if m["t"] == T + 100]
        self.assertEqual(tied[-1], "a new row tied with B", "a new row follows every mark of its time")

    def test_a_rewritten_row_whose_time_stands_is_replaced_in_place_and_one_whose_time_moved_builds_whole(self):
        self.derive()
        self.append(self.cap_path(), [crow(B, "segment", T + 100, "second, rewritten as it grew")])
        caps, m = self.derive()
        self.marks_equal("a rewrite at the same time", SID, caps, m)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["extend"], 1)
        self.append(self.cap_path(), [crow(A, "segment", None, "first, its time gone")])
        caps, m = self.derive()
        self.marks_equal("a rewrite that drops the time", SID, caps, m)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["extend"], 2, "a mark that goes is dropped where it stands")
        self.append(self.cap_path(), [crow(C, "turn", T + 5, "the rollup, moved")])
        caps, m = self.derive()
        self.marks_equal("a rewrite that moves the time", SID, caps, m)
        self.append(self.cap_path(), [crow(D, "segment", T + 100, "D, timed now and tied with B")])
        caps, m = self.derive()
        self.marks_equal("a rewrite that gives a time", SID, caps, m)
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["build"], st["extend"]), (3, 2), "a moved or a new time takes its rank among ties: whole")

    def test_a_rewrite_of_the_file_builds_whole(self):
        self.derive()
        self.write(self.cap_path(), list(reversed(first_rows())))
        caps, m = self.derive()
        self.marks_equal("the file rewritten", SID, caps, m)
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["build"], st["extend"], st["hit"]), (2, 0, 0))

    def test_a_mapping_that_is_not_the_held_one_is_derived_and_not_held(self):
        plain = {r["id"]: r for r in first_rows()}
        m = km._derive_judging_marks(SID, plain, {}, None)[0]
        self.assertEqual(json.dumps(m), json.dumps(ref_cap_marks(SID, plain)))
        self.assertEqual(len(km._JUDGING_MARKS_MEMO), 0, "a plain dict has no version: not held")
        caps1, _m1 = self.derive()
        self.append(self.cap_path(), [crow((T + 700, "cdcd8888"), "segment", T + 700, "later")])
        caps2, _m2 = self.derive()
        old = km._derive_judging_marks(SID, caps1, {}, None)[0]     # the mapping before the append: no longer held
        self.assertEqual(json.dumps(old), json.dumps(ref_cap_marks(SID, dict(caps1))))
        other = km._derive_judging_marks(SID2, caps2, {}, None)[0]  # another lane's sid over this file's mapping
        self.assertEqual(json.dumps(other), json.dumps(ref_cap_marks(SID2, dict(caps2))))
        self.assertNotIn(SID2, km._JUDGING_MARKS_MEMO)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["uncached"], 3)

    def test_the_held_list_refuses_every_write(self):
        _caps, m = self.derive()
        snap = json.dumps(m)
        writes = [lambda: m.append({}), lambda: m.extend([{}]), lambda: m.insert(0, {}), lambda: m.pop(),
                  lambda: m.remove(m[0]), lambda: m.clear(), lambda: m.sort(key=lambda x: x["t"]), lambda: m.reverse()]

        def setitem():
            m[0] = {}

        def setslice():
            m[0:1] = []

        def delitem():
            del m[0]

        def iadd():
            mm = m
            mm += [{}]

        def imul():
            mm = m
            mm *= 2
        for w in writes + [setitem, setslice, delitem, iadd, imul]:
            with self.assertRaises(TypeError):
                w()
        self.assertEqual(json.dumps(m), snap, "nothing reached the list")
        for c in (copy.copy(m), copy.deepcopy(m), pickle.loads(pickle.dumps(m)), list(m), m[:]):
            self.assertIs(type(c), list, "a copy is a plain list to edit")
            self.assertEqual(json.dumps(c), snap)
        self.assertIs(km._derive_judging_marks(SID, km._captions(SID), {}, None)[0], m, "and the held list still serves")

    def test_the_assembly_bisects_the_horizon_without_reading_a_mark(self):
        class Unreadable(dict):
            def __getitem__(self, k):
                raise AssertionError("the assembly read a mark")
        ts = (T, T + 10, T + 10, T + 20, T + 30)
        marks = km._CapMarks([Unreadable() for _ in ts], ts, True)
        km.JUDGE_CAP_LIMIT = 80
        for t0, first in ((T - 1, 0), (T, 0), (T + 1, 1), (T + 10, 1), (T + 11, 3), (T + 30, 4), (T + 31, 5)):
            out = []
            km._judging_assemble(marks, [], t0, out)
            self.assertEqual(len(out), len(ts) - first, "horizon %d" % (t0 - T))
            self.assertTrue(all(a is b for a, b in zip(out, list(marks)[first:])))
        _caps, m = self.derive()
        self.assertEqual(m._ts, tuple(x["t"] for x in m), "a held list carries its marks' times")

    def test_times_the_bisection_cannot_order_fall_back_to_the_filter(self):
        self.write(self.cap_path(), [crow(A, "segment", T + 10, "ten"), crow(B, "segment", float("nan"), "not a number"),
                                     crow(C, "segment", T + 5, "five")])
        caps, m = self.derive()
        self.marks_equal("NaN", SID, caps, m)
        self.assertIsNone(m._ts, "a NaN orders nothing: no bisection")
        for t0 in (0, T, T + 6, T + 10, T + 11):
            self.assertEqual(outcome(assembled, km._judging_assemble, m, t0),
                             outcome(assembled, ref_judging_assemble, ref_cap_marks(SID, dict(caps)), t0), "horizon %r" % t0)
        self.write(self.cap_path(), [crow(A, "segment", "T+10", "a string time"), crow(B, "segment", "T+5", "another")])
        caps, m = self.derive()
        self.marks_equal("string times", SID, caps, m)
        self.assertEqual((m._ts, m._num), (None, False))
        self.assertEqual(outcome(assembled, km._judging_assemble, m, 0), outcome(assembled, ref_judging_assemble,
                                                                                ref_cap_marks(SID, dict(caps)), 0))

    # the extend road's two NaN refusals: a NaN neither places after its ties nor stands in place, since the bisection
    # cannot order it; each case reaches one refusal only, and a comparison of the marks alone would pass without either
    # (NaN sorts last in both forms), so the counts, the held times and the assembly are what each case holds
    HORIZONS = (0, T - 1, T, T + 1, T + 50, T + 99, T + 100, T + 101, T + 200, T + 201, T + 10_000)

    def built_whole_with_no_times(self, label):
        caps, m = self.derive()
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["build"], st["extend"]), (2, 0), "%s: built whole, not carried" % label)
        self.assertIs(km._JUDGING_MARKS_MEMO[SID][2], m, "(the premise) the list served is the one held")
        self.assertIsNone(m._ts, "%s: the held list carries no times to bisect" % label)
        self.marks_equal(label, SID, caps, m)
        ref = ref_cap_marks(SID, dict(caps))
        for t0 in self.HORIZONS:
            self.assertEqual(outcome(assembled, km._judging_assemble, m, t0), outcome(assembled, ref_judging_assemble, ref, t0),
                             "%s: the assembly at horizon %r" % (label, t0))

    def test_an_appended_row_timed_nan_builds_whole(self):
        self.derive()
        self.append(self.cap_path(), [crow((T + 400, "eeee5555"), "segment", float("nan"), "a new row timed NaN")])
        self.built_whole_with_no_times("a NaN-timed append")

    def test_a_held_row_whose_time_is_rewritten_to_nan_builds_whole(self):
        self.derive()
        self.append(self.cap_path(), [crow(B, "segment", float("nan"), "second, its time rewritten to NaN")])
        self.built_whole_with_no_times("a held row rewritten to NaN")

    def test_a_cutoff_that_is_not_a_plain_number_keeps_the_filter(self):
        caps, m = self.derive()
        self.assertIsNotNone(m._ts, "(the premise) the held times order")
        ref = ref_cap_marks(SID, dict(caps))
        for t0 in (float("nan"), "a string"):
            self.assertEqual(outcome(assembled, km._judging_assemble, m, t0), outcome(assembled, ref_judging_assemble, ref, t0),
                             "horizon %r" % (t0,))
        self.assertEqual(assembled(km._judging_assemble, m, float("nan")), [], "a NaN cutoff keeps no mark, as the filter kept none")

    # the counters' rule as the reference states it: a derivation that returns counts once, and a sort that raises on a
    # held version counts nowhere, on a first build and on the build after an append the extend road refuses
    def test_a_sort_that_raises_on_a_held_version_counts_nowhere(self):
        self.derive()
        before = self.stats("_JUDGING_MARKS_STATS")
        self.append(self.cap_path(), [crow((T + 400, "eeee5555"), "segment", "a string", "a string time")])
        self.write(self.cap_path(SID2), [crow((T, "ffff6666"), "segment", T, "a number", sid=SID2),
                                         crow((T + 100, "abab7777"), "segment", "a string", "a string time", sid=SID2)])
        for label, sid in (("an append the extend road refuses", SID), ("a first build", SID2)):
            caps = km._captions(sid)
            self.assertIs(type(km._caption_rows_version(sid, caps)), tuple, "(the premise) %s: a held version" % label)
            with self.assertRaises(TypeError, msg=label):
                km._derive_judging_marks(sid, caps, {}, None)
            self.assertEqual(self.stats("_JUDGING_MARKS_STATS"), before, "%s: counted nowhere" % label)
        self.assertNotIn(SID2, km._JUDGING_MARKS_MEMO, "a sort that raises stores nothing")

    def test_the_lane_numeric_check_reads_the_held_bit(self):
        _caps, m = self.derive()
        self.assertIs(km._cap_marks_numeric(m), True)
        self.assertIs(km._cap_marks_numeric(km._CapMarks([{"t": 1}], (1,), False)), False, "the bit, not the marks")
        self.assertIs(km._cap_marks_numeric([{"t": 1}, {"t": "x"}]), False, "a plain list is checked mark by mark")
        self.assertIs(km._cap_marks_numeric([]), True)


# ── StatesMemo ───────────────────────────────────────────────────────────────────────────────────────────────────
NEEDS, COMPACT = None, "compacting"   # NEEDS is km._NEEDS_INPUT_STATES, read at run time


def srow(t, state):
    return {"t": t, "state": state}


class StatesMemo(_Root):
    NOW = T + 1000

    def setUp(self):
        super().setUp()
        self.rows = [srow(T + 100, "permission"), srow(T + 200, "working"), srow(T + 300, "compacting"),
                     srow(T + 300, "idle"), srow(T + 450, "picker"), srow(T + 500, "working"), srow(T + 700, "permission")]
        self.write(self.states_path(), self.rows)

    def intervals_equal(self, label, now=None):
        now = self.NOW if now is None else now
        for want in (km._NEEDS_INPUT_STATES, COMPACT, "working", ("idle", "picker")):
            self.assertEqual(outcome(km._state_intervals, SID, want, now), outcome(ref_state_intervals, SID, want, now),
                             "%s: %r at now %+d" % (label, want, now - T))

    def test_two_state_sets_over_two_builds_sort_once(self):
        for _ in range(2):
            for want in (km._NEEDS_INPUT_STATES, COMPACT):
                self.assertEqual(km._state_intervals(SID, want, self.NOW), ref_state_intervals(SID, want, self.NOW))
        st = self.stats("_STATE_IV_STATS")
        self.assertEqual((st["sort"], st["hit"], st["uncached"]), (1, 3, 0))
        ent = km._STATE_IV_MEMO[SID]
        self.assertEqual(set(ent[4]), {frozenset(km._NEEDS_INPUT_STATES), frozenset([COMPACT])}, "each state set's intervals, once")

    def test_an_append_a_rewrite_and_a_cleared_cursor_sort_again(self):
        self.intervals_equal("first")
        self.append(self.states_path(), [srow(T + 800, "compacting"), srow(T + 650, "permission")])   # one out of order
        self.intervals_equal("an append")
        self.write(self.states_path(), self.rows[2:])
        self.intervals_equal("a rewrite")
        km._state_ev_cache.pop(str(self.states_path()), None)      # a test's clear, a swept cursor: a refold, a new state
        self.intervals_equal("a cleared cursor")
        self.assertEqual(self.stats("_STATE_IV_STATS")["sort"], 4)

    def test_a_last_row_without_its_newline_is_not_held(self):
        self.intervals_equal("first")
        self.append(self.states_path(), [], tail=json.dumps(srow(T + 900, "compacting")))
        self.intervals_equal("a newline-less last row, folded provisionally")
        self.assertEqual(self.stats("_STATE_IV_STATS")["uncached"], 4)
        with open(self.states_path(), "a") as fh:
            fh.write("\n")
        self._stamp(self.states_path())
        self.intervals_equal("the row completed")
        st = self.stats("_STATE_IV_STATS")
        self.assertEqual((st["sort"], st["uncached"]), (2, 4))

    def test_the_horizon_cut_equals_the_walk_at_every_edge(self):
        km.TL_HORIZON = 600
        times = sorted({r["t"] for r in self.rows})
        for t in times:
            for d in (-1, 0, 1):
                self.intervals_equal("cutoff at %+d" % (t + d - T), now=t + d + 600)   # the cutoff on a transition, either side
        km.TL_HORIZON = -50                                                        # a cutoff past the clock: the open one goes too
        self.intervals_equal("a cutoff after now")
        km.TL_HORIZON = 600
        self.intervals_equal("a float clock", now=T + 900.5)
        self.assertEqual(km._state_intervals(SID, "working", T + 900), [[T + 300, T + 300], [T + 500, T + 700]],
                         "an interval that ends on the cutoff stays, cut to a point")

    def test_a_log_the_bisection_cannot_order_walks_as_before(self):
        km.TL_HORIZON = 600
        for rows in ([srow(T + 100, "permission"), srow(float("nan"), "working"), srow(T + 300, "permission"),
                      srow(T + 400, "working"), srow(T + 500, "permission"), srow(T + 600, "idle")],   # a NaN end mid-list
                     [srow(T + 100, "permission"), srow(T + 200, 7), srow(T + 300, "permission")],
                     [srow(T + 100, "permission"), srow(T + 200, ["a", "list"]), srow(T + 300, "permission")],
                     [srow("later", "permission"), srow("sooner", "working")],
                     [srow(T + 100, "permission"), srow("a string", "working")]):
            self.write(self.states_path(), rows)
            for d in (0, 150, 350, 450, 550, 650, 2000):           # cutoffs before, inside and after the transitions
                self.intervals_equal("rows %r" % (rows,), now=T + d + 600)

    # the counters' rule as the reference states it: a read that returns counts once, and a sort that raises counts
    # nowhere, on the cursor's own state and on a fold answer that is not it
    def test_a_sort_that_raises_counts_nowhere(self):
        self.intervals_equal("first")
        before = self.stats("_STATE_IV_STATS")
        for label, rows, tail in (("the cursor's own state", [srow("a string", "working")], ""),
                                  ("a last row without its newline", [], json.dumps(srow(T + 900, "compacting")))):
            self.append(self.states_path(), rows, tail=tail)
            with self.assertRaises(TypeError, msg=label):
                km._state_intervals(SID, "working", self.NOW)
            self.assertEqual(self.stats("_STATE_IV_STATS"), before, "%s: counted nowhere" % label)


# ── the lanes: a world for build_timeline ────────────────────────────────────────────────────────────────────────
def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rec(kind, t, uuid, parent, text):
    if kind == "user":
        return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
                "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def _live_row(since):
    return {"state": "waiting", "since": since, "model": "", "effort": "", "context": None,
            "compactPct": None, "color": "#336699", "mode": ""}


_REFERENCE_FNS = {"_judging_cap_marks": ref_cap_marks, "_judging_assemble": ref_judging_assemble,
                  "_state_intervals": ref_state_intervals, "_cap_marks_numeric": ref_numeric}


class _Lanes(_Root):
    """Live lanes for build_timeline: a transcript, a captions file and a states log each, the SDK backend off and the
    band carrying this build's own artifact marks (_run_judging handing them through), as tests/test_timeline_lane_memo.py
    builds them."""
    LANES = (("web", WEB), ("api", API))

    def setUp(self):
        super().setUp()
        self._saved_attrs = {nm: getattr(km, nm) for nm in ("_sdk", "_run_judging", "NAMES")}
        self._saved_proj = km.jd.PROJECTS
        km.jd.PROJECTS = Path(self._td) / "projects"
        km.NAMES = km.jd.NAMES
        km.jd._discover_cache.clear(); km.jd._CHAIN_MEMO.clear()
        km._parse_cache.clear(); km._BARS_COMPLAINED.clear()
        km._dead_lane_memo.clear(); km._delta_entry_memo.clear()
        km._lanes_memo.clear(); km._lane_prefix_memo.clear()
        km._downtime[:] = []
        km._sdk = lambda: None
        km._run_judging = lambda t0, alive, semantic: [dict(m) for m in semantic]
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = km.jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        self.present = {}
        self.recs, self.caps, self.states, self.turns = {}, {}, {}, {}
        for name, sid in self.LANES:
            self.recs[sid], self.caps[sid], self.states[sid], self.turns[sid] = [], [], [], 0
            for k in range(4):
                self.add_turn(sid, self.now - 3000 + k * 300, write=False)
            self.tpath(sid).write_text("".join(json.dumps(r) + "\n" for r in self.recs[sid]))
            self.arrive(name, sid)
            self.write(self.cap_path(sid), [])
            self.write(self.states_path(sid), [srow(self.now - 3000, "working"), srow(self.now - 2900, "idle")])
            self.states[sid] = [srow(self.now - 3000, "working"), srow(self.now - 2900, "idle")]

    def tearDown(self):
        for nm, v in self._saved_attrs.items():
            setattr(km, nm, v)
        km.jd.PROJECTS = self._saved_proj
        km._lanes_memo.clear(); km._lane_prefix_memo.clear(); km._dead_lane_memo.clear()
        km._parse_cache.clear(); km._downtime[:] = []
        super().tearDown()

    def tpath(self, sid):
        return self.proj / (sid + ".jsonl")

    def arrive(self, name, sid):
        (km.jd.NAMES / sid).write_text("%s\t%s\n" % (name, self.cdir))
        km.jd._discover_cache.clear()
        self.present[sid] = name

    def depart(self, sid):
        (km.jd.NAMES / sid).unlink()
        km.jd._discover_cache.clear()
        self.present.pop(sid, None)

    def add_turn(self, sid, t, write=True):
        k = self.turns[sid] = self.turns[sid] + 1
        tag = "w" if sid == WEB else "a"
        parent = self.recs[sid][-1]["uuid"] if self.recs[sid] else None
        new = [_rec("user", t, "%s-u%03d" % (tag, k), parent, "make the notes endpoint page %d" % k),
               _rec("assistant", t + 20, "%s-a%03d" % (tag, k), "%s-u%03d" % (tag, k), "Paged it, step %d." % k)]
        self.recs[sid].extend(new)
        if write:
            with self.tpath(sid).open("a") as fh:
                fh.write("".join(json.dumps(r) + "\n" for r in new))

    def live_map(self):
        return {sid: _live_row(self.now - 100) for sid in self.present}

    def build(self, live_only=False):
        return km.build_timeline(self.now, self.live_map(), with_bars=True, live_only=live_only)

    @contextlib.contextmanager
    def cold(self):
        """A build through the REFERENCE code with every lane memo empty: the tables are emptied for its length and put
        back after, with their counters, so the warm builds go on exactly as they would have."""
        tables = [t for t in (km._lanes_memo, km._lane_prefix_memo, km._dead_lane_memo,
                              getattr(km, "_JUDGING_MARKS_MEMO", None), getattr(km, "_STATE_IV_MEMO", None)) if t is not None]
        saved_tables = [(t, list(t.items())) for t in tables]
        stats = [s for s in (km._lanes_stats, getattr(km, "_JUDGING_MARKS_STATS", None),
                             getattr(km, "_STATE_IV_STATS", None)) if s is not None]
        saved_stats = [(s, dict(s)) for s in stats]
        saved_fns = {nm: getattr(km, nm, _MISSING) for nm in _REFERENCE_FNS}
        for t in tables:
            t.clear()
        for nm, fn in _REFERENCE_FNS.items():
            setattr(km, nm, fn)
        try:
            yield
        finally:
            for nm, v in saved_fns.items():
                if v is _MISSING:
                    delattr(km, nm)
                else:
                    setattr(km, nm, v)
            for t, items in saved_tables:
                t.clear()
                t.update(items)
            for s, d in saved_stats:
                s.clear()
                s.update(d)

    def frame(self, tl, sid):
        lane = next(l for l in tl["sessions"] if l["id"] == sid)
        return json.dumps([lane, tl["turns"].get(sid), tl["judging"].get(sid)])


# ── FrameWalk ────────────────────────────────────────────────────────────────────────────────────────────────────
class FrameWalk(_Lanes):
    def live_map(self):                           # the walk's own liveness: a lane can die, and its dead lane is compared too
        live = getattr(self, "live", None)
        return {sid: _live_row(self.now - 100) for sid in self.present if live is None or sid in live}

    def check(self, i, label):
        tl = self.build()
        with self.cold():
            ref = self.build()
        self.assertEqual([l["id"] for l in tl["sessions"]], [l["id"] for l in ref["sessions"]], "step %d (%s)" % (i, label))
        for sid in self.present:
            self.assertEqual(self.frame(tl, sid), self.frame(ref, sid), "step %d (%s): the %s lane's frame" % (i, label,
                                                                                                         self.present[sid]))
        return tl

    def cap_rows(self, rng, sid, bars):
        rows = []
        for _ in range(rng.randrange(1, 4)):
            r = rng.random()
            t = self.now - rng.randrange(0, 360) * 10
            if r < 0.3 and bars:                  # a segment's own work or message caption
                bid = rng.choice(bars)["id"]
                rows.append({"id": bid + ("#p" if rng.random() < 0.4 else ""), "grain": rng.choice(("segment", "prompt")),
                             "t": t, "caption": "caption %d" % rng.randrange(10 ** 6)})
            elif r < 0.45 and bars:               # a drifted id of a segment's
                parts = rng.choice(bars)["id"].split(":")
                if len(parts) >= 3 and parts[-2].lstrip("-").isdigit():
                    parts[-2] = str(int(parts[-2]) + rng.choice((1, -2, 30)))
                rows.append({"id": ":".join(parts), "grain": "segment", "t": t, "caption": "drifted %d" % rng.randrange(10 ** 6)})
            elif r < 0.7 and self.caps[sid]:      # a rewrite of a row: the same time, another, none
                old = dict(rng.choice(self.caps[sid]))
                h = rng.random()
                if h < 0.3:
                    old["t"] = t
                elif h < 0.4:
                    old.pop("t", None)
                old["caption"] = "rewritten %d" % rng.randrange(10 ** 6)
                rows.append(old)
            else:                                 # another grain's row, or one without a time
                row = {"id": "%s:%d:%08x" % (sid, t, rng.randrange(1 << 32)), "grain": rng.choice(("turn", "segment", "prompt")),
                       "caption": "other %d" % rng.randrange(10 ** 6)}
                if rng.random() > 0.1:
                    row["t"] = t + (0.5 if rng.random() < 0.1 else 0)
                rows.append(row)
        return rows

    def test_every_lanes_frame_equals_the_reference_build_after_every_step(self):
        ln0 = dict(km._lanes_stats)
        self.walk(20261006, 70)
        if hasattr(km, "_JUDGING_MARKS_STATS"):           # the walk took every road of both tables
            st, sv = self.stats("_JUDGING_MARKS_STATS"), self.stats("_STATE_IV_STATS")
            for k in ("hit", "extend", "build", "forget"):
                self.assertGreater(st[k], 0, "judgingMarks.%s" % k)
            for k in ("hit", "sort", "forget"):
                self.assertGreater(sv[k], 0, "stateIntervals.%s" % k)
        for k in ("dead_miss", "dead_serve"):                # and the dead-lane road, derived and served
            self.assertGreater(km._lanes_stats[k] - ln0.get(k, 0), 0, "lanes.%s" % k)

    def walk(self, seed, steps):
        """`steps` seeded steps, every lane's frame compared after each. The clock only moves forward: a horizon-edge
        step picks a time whose horizon is at or after the clock. A step back made a dead lane's served frame differ from
        a cold build at some seeds, at the base before either table too, so it tested the dead-lane memo under a clock
        that steps back, which is not what this walk is for."""
        rng = random.Random(seed)
        self.live = set(self.present)
        km.TL_HORIZON, km.JUDGE_CAP_LIMIT = 1800, 5
        for sid in (WEB, API):
            rows = [{"id": "%s:%d:%08x" % (sid, self.now - 900 - 60 * j, j), "grain": "segment", "t": self.now - 900 - 60 * j,
                     "caption": "an early caption %d" % j} for j in range(3)]
            self.caps[sid].extend(rows)
            self.append(self.cap_path(sid), rows)
        tl = self.check(-1, "start")
        ops = ("cap", "caprw", "st", "strw", "turn", "clock", "edge", "ivedge", "die", "wake", "depart", "noop")
        weights = (30, 4, 16, 3, 6, 10, 10, 10, 5, 4, 4, 6)
        wanted = set(km._NEEDS_INPUT_STATES) | {"compacting"}
        for i in range(steps):
            op = rng.choices(ops, weights)[0]
            if i == 20:
                op = "depart" if API in self.present else op   # the api lane leaves at least once, and comes back
            elif i == 40:
                op = "depart" if API not in self.present else op
            sid = rng.choice(sorted(self.present))
            clock = self.now
            if op == "cap":
                rows = self.cap_rows(rng, sid, tl["turns"].get(sid) or [])
                self.caps[sid].extend(rows)
                self.append(self.cap_path(sid), rows)
            elif op == "caprw":
                keep = [r for r in self.caps[sid] if rng.random() < 0.7]
                rng.shuffle(keep)
                self.caps[sid] = keep
                self.write(self.cap_path(sid), keep)
            elif op == "st":
                rows = [srow(self.now - rng.randrange(0, 250) * 10, rng.choice(("working", "idle", "permission", "picker",
                                                                                  "compacting"))) for _ in range(rng.randrange(1, 3))]
                self.states[sid].extend(rows)
                self.append(self.states_path(sid), rows)
            elif op == "strw":
                keep = [r for r in self.states[sid] if rng.random() < 0.7]
                self.states[sid] = keep
                self.write(self.states_path(sid), keep)
            elif op == "turn":
                self.add_turn(sid, self.now - 30)
            elif op == "clock":
                self.now += rng.choice((5, 60, 300))
            elif op == "edge":                    # the horizon exactly on a caption's or a transition's time
                ts = [r["t"] for s in self.present for r in self.caps[s] + self.states[s]
                      if isinstance(r.get("t"), int) and r["t"] + km.TL_HORIZON >= self.now]
                if ts:
                    self.now = rng.choice(ts) + km.TL_HORIZON
            elif op == "ivedge":                  # the horizon exactly on the end of a wanted state's interval
                ends = []
                for s in self.present:
                    ev = sorted(((r["t"], r["state"]) for r in self.states[s] if r.get("t") and r.get("state")),
                                key=lambda e: e[0])
                    ends.extend(ev[j + 1][0] for j in range(len(ev) - 1) if ev[j][1] in wanted)
                ends = [e for e in ends if e + km.TL_HORIZON >= self.now]
                if ends:
                    self.now = rng.choice(ends) + km.TL_HORIZON
            elif op == "die":                     # a lane goes dead: its frame comes off the dead-lane road
                if self.live:
                    self.live.discard(rng.choice(sorted(self.live)))
            elif op == "wake":
                dead = sorted(set(self.present) - self.live)
                if dead:
                    self.live.add(rng.choice(dead))
            elif op == "depart":
                if API in self.present:
                    self.depart(API)
                    self.live.discard(API)
                else:
                    self.arrive("api", API)
                    self.live.add(API)
            self.assertGreaterEqual(self.now, clock, "step %d (%s): the clock moved backward" % (i, op))
            tl = self.check(i, op)


# ── SameSecondOrder ──────────────────────────────────────────────────────────────────────────────────────────────
class SameSecondOrder(_Lanes):
    """The backend stamps the states log in whole seconds, so the 'working' row written when one prompt is answered and
    the next prompt's row (or a compaction's) often share a second. Those rows keep the log's order. A sort on the whole
    (time, state) row ordered them by state name and put 'working' last, so a lane stopped on that prompt right now
    carried a zero-width closed band and no open one, while the session's own state read the prompt."""

    def log(self, sid, pairs):
        rows = [srow(t, st) for t, st in pairs]
        self.states[sid].extend(rows)
        self.append(self.states_path(sid), rows)

    def lane(self, sid):
        return next(l for l in self.build()["sessions"] if l["id"] == sid)

    def test_a_prompt_in_the_same_second_as_working_stays_open_on_the_lane(self):
        t = self.now - 50
        self.log(WEB, [(t, "working"), (t, "permission")])
        self.log(API, [(t, "working"), (t, "picker")])
        for sid, st in ((WEB, "permission"), (API, "picker")):
            self.assertEqual(km._last_state(sid), (st, t), "(the premise) the log's newest row is the prompt")
            self.assertEqual(self.lane(sid)["awaiting"], [[t, self.now, True]], st)

    def test_a_compaction_in_the_same_second_as_working_stays_open_on_the_lane(self):
        t = self.now - 50
        self.log(WEB, [(t, "working"), (t, "compacting")])
        self.assertEqual(self.lane(WEB)["compacting"], [[t, self.now, True]])

    def test_a_second_prompt_in_history_keeps_its_width(self):
        t = self.now - 50
        self.log(WEB, [(t - 40, "permission"), (t - 30, "working"), (t - 30, "permission"), (t, "working")])
        self.assertEqual(self.lane(WEB)["awaiting"], [[t - 40, t - 30], [t - 30, t]])

    def test_the_unheld_sort_keeps_the_order_too(self):
        # a last row without its newline is folded provisionally, and that answer is sorted without being held
        t = self.now - 50
        self.append(self.states_path(WEB), [srow(t, "working")], tail=json.dumps(srow(t, "permission")))
        self.assertEqual(km._state_intervals(WEB, km._NEEDS_INPUT_STATES, self.now), [[t, self.now, True]])
        self.assertEqual(self.stats("_STATE_IV_STATS")["uncached"], 1, "(the premise) the unheld road")

    def test_a_log_the_bisection_cannot_order_walks_in_the_same_order(self):
        t = self.now - 50
        self.log(WEB, [(t - 10, 7), (t, "working"), (t, "permission")])   # a state that is not a string: the walk
        self.assertIs(km._state_ev_sorted(WEB, self.states_path(WEB))[3], False, "(the premise) the walk's road")
        self.assertEqual(km._state_intervals(WEB, km._NEEDS_INPUT_STATES, self.now), [[t, self.now, True]])


# ── Bounds ───────────────────────────────────────────────────────────────────────────────────────────────────────
class Bounds(_Lanes):
    def caps_rows(self, sid, n):
        return [crow((T + i, "%08x" % i), "segment", T + i, "row %d" % i, sid=sid) for i in range(n)]

    def test_the_marks_table_is_lru_and_an_entry_over_the_bound_alone_is_not_held(self):
        for sid, n in ((SID, 20), (SID2, 20), (SID3, 200)):
            self.write(self.cap_path(sid), self.caps_rows(sid, n))
        self.derive(SID)
        size = km._JUDGING_MARKS_MEMO[SID][4]
        km.JUDGING_MARKS_MEMO_BYTES = size + size // 2   # one 20-row entry fits, two do not
        self.derive(SID2)
        self.assertEqual(list(km._JUDGING_MARKS_MEMO), [SID2], "the least recently used went")
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["evict"], 1)
        self.derive(SID3)
        self.assertEqual(list(km._JUDGING_MARKS_MEMO), [SID2], "an entry over the bound alone is not held and sheds nothing")
        st = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual((st["evict"], st["overBound"], st["build"]), (1, 1, 2),
                         "counted over the bound: not an eviction (no lane was shed) and not a held build")
        caps, m = self.derive(SID3)
        self.marks_equal("served, not held", SID3, caps, m)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["overBound"], 2, "and again on every derivation of it")
        rep = km._judging_marks_report()
        self.assertEqual((rep["entries"], rep["marks"], rep["bytes"], rep["bound"]), (1, 20, km._JUDGING_MARKS_MEMO[SID2][4],
                                                                                   km.JUDGING_MARKS_MEMO_BYTES))

    def test_the_states_table_is_lru_and_an_entry_over_the_bound_alone_is_not_held(self):
        for sid, n in ((SID, 10), (SID2, 10), (SID3, 100)):
            self.write(self.states_path(sid), [srow(T + 10 * i, ("working", "idle")[i % 2]) for i in range(n)])
        km._state_intervals(SID, COMPACT, T + 5000)
        size = km._STATE_IV_MEMO[SID][5]
        km.STATE_IV_MEMO_BYTES = size + size // 2
        km._state_intervals(SID2, COMPACT, T + 5000)
        self.assertEqual(list(km._STATE_IV_MEMO), [SID2])
        km._state_intervals(SID3, COMPACT, T + 5000)
        self.assertEqual(list(km._STATE_IV_MEMO), [SID2])
        st = self.stats("_STATE_IV_STATS")
        self.assertEqual((st["evict"], st["overBound"], st["sort"]), (1, 1, 2),
                         "counted over the bound: not an eviction (no lane was shed) and not a version sorted and held")
        self.assertEqual(km._state_intervals(SID3, "working", T + 5000), ref_state_intervals(SID3, "working", T + 5000))
        self.assertEqual(self.stats("_STATE_IV_STATS")["overBound"], 2, "and again on every read of it")

    # recency: under a bound that fits two entries, lanes A and B are derived, A again as a hit, then C; the bound sheds B,
    # the least recently USED, and keeps A, the least recently derived (a table that never moved a hit sheds A)
    def test_a_hit_makes_the_marks_entry_the_newest(self):
        for sid in (SID, SID2, SID3):
            self.write(self.cap_path(sid), self.caps_rows(sid, 20))
        self.derive(SID)
        size = km._JUDGING_MARKS_MEMO[SID][4]
        km.JUDGING_MARKS_MEMO_BYTES = 2 * size + size // 2   # two 20-row entries fit, three do not
        self.derive(SID2)
        _caps, held = self.derive(SID)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["hit"], 1, "(the premise) A's second derivation was a hit")
        self.derive(SID3)
        self.assertEqual(list(km._JUDGING_MARKS_MEMO), [SID, SID3], "B went; A, used since, stayed")
        self.assertIs(km._JUDGING_MARKS_MEMO[SID][2], held)
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["evict"], 1)

    def test_a_hit_makes_the_states_entry_the_newest(self):
        for sid in (SID, SID2, SID3):
            self.write(self.states_path(sid), [srow(T + 10 * i, ("working", "idle")[i % 2]) for i in range(10)])
        km._state_intervals(SID, COMPACT, T + 5000)
        size = km._STATE_IV_MEMO[SID][5]
        km.STATE_IV_MEMO_BYTES = 2 * size + size // 2
        km._state_intervals(SID2, COMPACT, T + 5000)
        km._state_intervals(SID, "working", T + 5000)
        self.assertEqual(self.stats("_STATE_IV_STATS")["hit"], 1, "(the premise) A's second read was a hit")
        km._state_intervals(SID3, COMPACT, T + 5000)
        self.assertEqual(list(km._STATE_IV_MEMO), [SID, SID3], "B went; A, used since, stayed")
        self.assertEqual(self.stats("_STATE_IV_STATS")["evict"], 1)

    def test_a_full_build_drops_the_lanes_it_no_longer_draws_and_a_live_only_build_drops_none(self):
        for sid in (WEB, API):
            self.append(self.cap_path(sid), [crow((self.now - 600, "%08x" % 7), "segment", self.now - 600, "a caption", sid=sid)])
        self.build()
        self.assertEqual(set(km._JUDGING_MARKS_MEMO), {WEB, API})
        self.assertEqual(set(km._STATE_IV_MEMO), {WEB, API})
        self.depart(API)
        self.build(live_only=True)
        self.assertEqual(set(km._JUDGING_MARKS_MEMO), {WEB, API}, "a live-only build reads a subset and drops nothing")
        self.build()
        self.assertEqual(set(km._JUDGING_MARKS_MEMO), {WEB})
        self.assertEqual(set(km._STATE_IV_MEMO), {WEB})
        self.assertEqual((self.stats("_JUDGING_MARKS_STATS")["forget"], self.stats("_STATE_IV_STATS")["forget"]), (1, 1))
        self.arrive("api", API)
        tl = self.build()
        self.assertIn(API, km._JUDGING_MARKS_MEMO, "a lane that returns is derived and held again")
        self.assertEqual([m["x"] for m in tl["judging"].get(API, [])], ["a caption"])

    def test_a_lane_the_dead_lane_memo_takes_over_leaves_the_marks_table_and_keeps_its_states(self):
        for sid in (WEB, API):
            self.append(self.cap_path(sid), [crow((self.now - 600, "%08x" % 7), "segment", self.now - 600, "a caption", sid=sid)])
        self.build()
        self.assertEqual(set(km._JUDGING_MARKS_MEMO), {WEB, API}, "(the premise) both live lanes' marks are held")
        live = {WEB: _live_row(self.now - 100)}             # the api session ends: its lane is dead and still drawn
        forget, sorts = self.stats("_JUDGING_MARKS_STATS")["forget"], self.stats("_STATE_IV_STATS")["sort"]
        tl = km.build_timeline(self.now, live, with_bars=True)
        self.assertIn(API, km._dead_lane_memo, "(the premise) this build cached the dead lane")
        self.assertNotIn(API, km._JUDGING_MARKS_MEMO, "its marks entry went with its parse: the dead-lane memo serves them")
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["forget"] - forget, 1, "counted under forget")
        self.assertIn(WEB, km._JUDGING_MARKS_MEMO, "a live lane's entry stays")
        self.assertEqual(set(km._STATE_IV_MEMO), {WEB, API}, "the dead lane's states entry stays: every build reads it")
        self.assertEqual(self.stats("_STATE_IV_STATS")["sort"], sorts, "read again from the held entry, not sorted again")
        self.assertEqual([m["x"] for m in tl["judging"].get(API, [])], ["a caption"])
        before = self.stats("_JUDGING_MARKS_STATS")
        tl = km.build_timeline(self.now, live, with_bars=True)   # a dead-lane hit: served whole from that memo
        after = self.stats("_JUDGING_MARKS_STATS")
        self.assertEqual([after[k] - before[k] for k in ("build", "extend", "uncached", "forget")], [0, 0, 0, 0],
                         "nothing derived for the dead lane")
        self.assertNotIn(API, km._JUDGING_MARKS_MEMO)
        self.assertEqual([m["x"] for m in tl["judging"].get(API, [])], ["a caption"])
        self.append(self.cap_path(API), [crow((self.now - 300, "%08x" % 8), "segment", self.now - 300, "a later caption", sid=API)])
        build = self.stats("_JUDGING_MARKS_STATS")["build"]
        tl = km.build_timeline(self.now, live, with_bars=True)   # a dead miss: its marks built whole once, then taken over again
        self.assertEqual(self.stats("_JUDGING_MARKS_STATS")["build"] - build, 1)
        self.assertNotIn(API, km._JUDGING_MARKS_MEMO)
        self.assertEqual([m["x"] for m in tl["judging"].get(API, [])], ["a caption", "a later caption"])


class BoundEnvironment(unittest.TestCase):
    """The two bounds' environment read (_floor_memo_bound): a positive integer override is the bound, anything else is
    the machine's memory over the table's share, and each module constant is that share, read at import."""

    def test_an_override_is_honored_and_anything_else_is_memory_over_the_share(self):
        from unittest import mock
        mem = km._mem_total_bytes()
        for env, share, const in (("ROMP_JUDGING_MARKS_MEMO_BYTES", 256, "JUDGING_MARKS_MEMO_BYTES"),
                                  ("ROMP_STATE_INTERVALS_MEMO_BYTES", 512, "STATE_IV_MEMO_BYTES")):
            with mock.patch.dict(os.environ, {env: "8192"}):
                self.assertEqual(km._floor_memo_bound(env, share), 8192, "%s: the override" % env)
            for raw in ("lots", "0", "-4096", ""):
                with mock.patch.dict(os.environ, {env: raw}):
                    self.assertEqual(km._floor_memo_bound(env, share), mem // share,
                                     "%s=%r: memory over %d" % (env, raw, share))
            self.assertNotIn(env, os.environ, "(the premise) the run sets no override")
            self.assertEqual(getattr(km, const), mem // share, "%s: memory over %d" % (const, share))


# ── PerfWiring ───────────────────────────────────────────────────────────────────────────────────────────────────
class PerfWiring(_Lanes):
    def test_both_tables_report_under_perf_and_the_reference_names_every_key(self):
        self.append(self.cap_path(WEB), [crow((self.now - 600, "0000000a"), "segment", self.now - 600, "a caption", sid=WEB)])
        self.build(); self.build()
        memos = km._PERF_STATS.snapshot()["memos"]
        marks, states = memos["judgingMarks"], memos["stateIntervals"]
        self.assertEqual(set(marks), {"hit", "extend", "build", "uncached", "overBound", "evict", "forget", "entries", "marks",
                                      "bytes", "bound"})
        self.assertEqual(set(states), {"hit", "sort", "uncached", "overBound", "evict", "forget", "entries", "transitions",
                                       "bytes", "bound"})
        self.assertTrue(all(type(v) is int for v in list(marks.values()) + list(states.values())))
        self.assertEqual((marks["build"], marks["entries"], marks["marks"], marks["bound"]), (1, 1, 1, km.JUDGING_MARKS_MEMO_BYTES))
        self.assertEqual((states["sort"], states["entries"], states["transitions"], states["bound"]), (2, 2, 4, km.STATE_IV_MEMO_BYTES))
        self.assertEqual(states["hit"], 6, "four reads per build, two lanes: one sort per lane, every later read held")
        doc = (Path(HERE).parent / "docs" / "reference.md").read_text(encoding="utf-8")
        at = doc.index("`judgingMarks` is the captioner's marks")
        mid = doc.index("`stateIntervals` is the states logs'", at)
        end = doc.index("Four memos cover", mid)
        for passage, keys in ((doc[at:mid], km._judging_marks_report()), (doc[mid:end], km._state_intervals_report())):
            for k in keys:
                self.assertIn("`%s`" % k, passage, k)
        self.assertIn("ROMP_JUDGING_MARKS_MEMO_BYTES", doc[at:mid])
        self.assertIn("ROMP_STATE_INTERVALS_MEMO_BYTES", doc[mid:end])


if __name__ == "__main__":
    unittest.main()
