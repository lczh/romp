#!/usr/bin/env python3
"""One captions version for both memos that hold what they derived from the captions (2026-10-06).

Two memos hold results derived from the captions mapping _captions serves on its reader version (#118): the lane
prefix's held bars, re-captioned when the captions move (_lane_segments), and the captioner's marks
(_judging_cap_marks). Each asks the same two questions: which version of the file a mapping was built from, and which
records were appended between two versions. Each had its own pair of helpers for them, _caption_rows_version and
_caption_rows_since for the lane prefix, _judging_caps_version and _judging_caps_tail for the marks, so a later change to
one pair (to what a version is, or to when an append's records can be trusted) would have reached one memo and left the
other on the old rule. The marks now ask the lane prefix's pair.

The two pairs answered three questions differently, and each caller keeps its own answer:
  an empty mapping    the lane's version is "empty", which the prefix holds like any version; the marks' was None, so
                      an empty mapping was derived there, not held and not counted. The folded version answers "empty",
                      and the marks' call site reads anything but a tuple as no version.
  not a tuple         the lane's since answers None when either version is "empty" or None. The marks' tail was never
                      handed one (that memo holds tuples only) and would have raised on None. The folded since is the
                      lane's.
  one version twice   the lane's since answers [] without reading the reader's entry; the marks' tail read the entry and
                      answered None once it had moved. Neither caller asks (each serves its held entry first). The
                      folded since is the lane's, and the road below checks the marks never ask it.
One more line differed with no answer moving: the marks' version refused a plain dict by its type, the lane's by
identity (the mapping _captions holds is always a _CaptionRows, so a plain dict is never it). The folded version keeps
the type check as its early exit.

The old pairs and the marks' old caller are copied below as the REFERENCE (1a5906d35, verbatim but for the module
prefix and the comments), and both classes compare against it:
  Road        one road of captions states: no file, rows the fold skips, appended rows (new ids, a drifted id, a '#p'
              row, an overwrite), the file rewritten, the reader's entry evicted, the memo swept, the file read back, a
              version replaced on another thread, the file rewritten on another thread, the file removed and back. At
              every step a pool of mappings is asked (the one this build read, every one served before, a plain copy,
              an empty dict, the shared empty mapping, another lane's sid over this file's mapping): the lane's version
              and since equal its old pair's for every mapping and every pair of versions seen, and its call site
              answers alike under every held version; the marks' caller (the kernel's _judging_cap_marks against the
              old one, each run from the same memo) leaves the same counters and held entries, serves the same marks,
              and asks since the same questions with the same answers, never one version twice.
  Exceptions  two of the three answers the pairs gave differently, each on its own (Road asks the third, a version that is
              not a tuple): an empty mapping, and one version twice.
  OnePair     both callers name the one pair, _caption_rows_version and _caption_rows_since.
Every answer compared here is equal on the kernel before the fold (the reference is that code), yet Road is red there:
its marks asked their own tail, which the spy on the shared since cannot see, so the since questions differ. That is
the one thing the fold changes. Road compares answers, so of the private copies that answer alike it catches only one: a
private since for the marks. OnePair holds the rest by reading _lane_segments' and _judging_cap_marks' source for the two
calls.
Synthetic data only: placeholder uuids and invented captions."""
import collections
import inspect
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time. This root is the module's own,
# outside the runner's belt, so it pins the per-session hosts off itself (CLAUDE.md, 2026-09-11).
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
with open(os.path.join(_XDG, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off\n")
km = load_source("romp_kernel_caption_rows_version", os.path.join(BIN, "romp-kernel"))
em = km.em

SID = "11111111-2222-3333-4444-0000000cb1d1"     # this module's own synthetic sids
SID2 = "11111111-2222-3333-4444-0000000cb1d2"
T = 1_781_000_000


# ── the REFERENCE: 1a5906d35's two pairs and the marks' caller, verbatim but for the module prefix ──────────────────
def ref_lane_version(fsid, caps):
    """_caption_rows_version as the lane prefix had it."""
    if not caps:
        return "empty"
    path = str(km.jd.CAPDIR / (fsid + ".jsonl"))
    with km._CAPTION_ROWS_LOCK:
        held = km._CAPTION_ROWS_MEMO.get(path)
    if held is not None and held[3] is caps:
        return (path, held[0], held[1], held[2])
    return None


def ref_lane_since(old, new):
    """_caption_rows_since as the lane prefix had it."""
    if not (isinstance(old, tuple) and isinstance(new, tuple)) or old[:3] != new[:3] or old[3] > new[3]:
        return None
    if old[3] == new[3]:
        return []
    with em._JSONL_CACHE_LOCK:
        ent = em._JSONL_CACHE.get(new[0])
    if ent is None or ent[6] != new[1] or ent[5] != new[2] or len(ent[4]) < new[3]:
        return None
    return ent[4][old[3]:new[3]]


def ref_marks_version(sid, caps):
    """_judging_caps_version, the marks' own."""
    if type(caps) is not km._CaptionRows or not caps:
        return None
    path = str(km.jd.CAPDIR / (sid + ".jsonl"))
    with km._CAPTION_ROWS_LOCK:
        held = km._CAPTION_ROWS_MEMO.get(path)
    if held is None or held[3] is not caps:
        return None
    return (path, held[0], held[1], held[2])


def ref_marks_tail(old, new):
    """_judging_caps_tail, the marks' own."""
    if old[:3] != new[:3] or old[3] > new[3]:
        return None
    with em._JSONL_CACHE_LOCK:
        ent = em._JSONL_CACHE.get(new[0])
    if ent is None or ent[6] != new[1] or ent[5] != new[2] or len(ent[4]) < new[3]:
        return None
    return ent[4][old[3]:new[3]]


def ref_judging_cap_marks(sid, caps):
    """_judging_cap_marks as it asked its own pair."""
    ver = ref_marks_version(sid, caps)
    if ver is None:
        if caps:
            with km._JUDGING_MARKS_LOCK:
                km._JUDGING_MARKS_STATS["uncached"] += 1
        return km._cap_marks_build(sid, caps)[0]
    with km._JUDGING_MARKS_LOCK:
        held = km._JUDGING_MARKS_MEMO.get(sid)
        if held is not None:
            km._JUDGING_MARKS_MEMO.move_to_end(sid)
            if held[0] == ver:
                km._JUDGING_MARKS_STATS["hit"] += 1
                return held[2]
    got, kind = None, "build"
    if held is not None:
        tail = ref_marks_tail(held[0], ver)
        if tail is not None:
            got = km._cap_marks_extend(sid, held[2], held[3], held[1], caps, tail)
            if got is not None:
                kind = "extend"
    if got is None:
        got = km._cap_marks_build(sid, caps)
    marks, ids = got
    size = km._judging_marks_bytes(marks, ids)
    with km._JUDGING_MARKS_LOCK:
        st = km._JUDGING_MARKS_STATS
        st[kind] += 1
        km._JUDGING_MARKS_MEMO.pop(sid, None)
        if size > km.JUDGING_MARKS_MEMO_BYTES:
            st["evict"] += 1
        else:
            km._JUDGING_MARKS_MEMO[sid] = (ver, len(caps), marks, ids, size)
            total = sum(e[4] for e in km._JUDGING_MARKS_MEMO.values())
            while total > km.JUDGING_MARKS_MEMO_BYTES:
                _k, old = km._JUDGING_MARKS_MEMO.popitem(last=False)
                total -= old[4]
                st["evict"] += 1
    return marks


# ── the lane prefix's call site, which the fold leaves as it was ────────────────────────────────────────────────────
def outcome(fn, *a):
    """What a call answered: ("ok", value), or ("raise", the exception's type name)."""
    try:
        return ("ok", fn(*a))
    except Exception as e:                       # noqa: BLE001  a raise is part of the answer
        return ("raise", type(e).__name__)


def lane_asks(version, since, sid, caps, held_ver):
    """The lane prefix's questions as _lane_segments asks them: the version this derivation's bars are captioned under,
    and, when that is not the held version, the appended records (None: every held bar is looked up again)."""
    cap_ver = version(sid, caps)
    if cap_ver is None or held_ver != cap_ver:
        return cap_ver, "re-caption", outcome(since, held_ver, cap_ver)
    return cap_ver, "stands", None


def crow(t0h, grain, t, caption, p=False, sid=SID):
    """A caption row for the segment id <sid>:<t0>:<hash> (its message caption with p)."""
    t0, h = t0h
    return {"id": "%s:%d:%s%s" % (sid, t0, h, "#p" if p else ""), "grain": grain, "t": t, "caption": caption}


A, B, C, D = (T, "aaaa1111"), (T + 100, "bbbb2222"), (T + 200, "cccc3333"), (T + 300, "dddd4444")
NO_ID = {"grain": "segment", "caption": "a row with no id: the fold skips it"}


def first_rows():
    return [crow(A, "segment", T, "first"), crow(B, "segment", T + 100, "second"), crow(C, "turn", T + 5, "the rollup")]


class _Captions(unittest.TestCase):
    """Each test's own captions directory (jd.CAPDIR rebound), every write stamped with a distinct mtime so the reader
    sees each version, and the marks memo emptied before and after."""

    def setUp(self):
        self._capdir = km.jd.CAPDIR
        self._td = tempfile.TemporaryDirectory()
        km.jd.CAPDIR = Path(self._td.name) / "captions"
        km.jd.CAPDIR.mkdir()
        self.path = km.jd.CAPDIR / (SID + ".jsonl")
        self.path2 = km.jd.CAPDIR / (SID2 + ".jsonl")
        self.mtime = 1_700_000_000
        self._clear_marks()

    def tearDown(self):
        for p in (self.path, self.path2):
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(str(p))
            km._CAPTION_ROWS_MEMO.pop(str(p), None)
        km.jd.CAPDIR = self._capdir
        self._clear_marks()
        self._td.cleanup()

    @staticmethod
    def _clear_marks():
        km._JUDGING_MARKS_MEMO.clear()
        for k in km._JUDGING_MARKS_STATS:
            km._JUDGING_MARKS_STATS[k] = 0

    def stamp(self, p):
        self.mtime += 7
        os.utime(p, (self.mtime, self.mtime))

    def write(self, rows, path=None):
        p = path or self.path
        with open(p, "w") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        self.stamp(p)

    def append(self, rows):
        with open(self.path, "a") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in rows))
        self.stamp(self.path)

    def rows(self):
        return [json.loads(x) for x in self.path.read_text().splitlines() if x.strip()]

    def held(self):
        return km._CAPTION_ROWS_MEMO.get(str(self.path))

    @staticmethod
    def on_another_thread(fn):
        err = []

        def run():
            try:
                fn()
            except Exception as e:               # noqa: BLE001  reported on the test's own thread
                err.append(e)
        t = threading.Thread(target=run)
        t.start()
        t.join(30)
        if err:
            raise err[0]


class Road(_Captions):
    def setUp(self):
        super().setUp()
        self.served = []                         # every mapping a build read, in order
        self.versions = [None, "empty"]          # every version seen, the two that are not tuples first
        self.marks_kinds = collections.Counter()
        self.lane_kinds = collections.Counter()

    def marks_run(self, fn, sid, caps, before):
        """One call of a marks caller from the memo as `before` holds it: what it served, the memo and counters it left,
        and the since questions it asked with their answers."""
        calls = []

        def spy(real):
            def call(old, new):
                got = real(old, new)
                calls.append((old, new, got))
                return got
            return call
        # the name each caller looks its since up by, in its own module's globals: the reference's here, the kernel's there
        ns, name = (globals(), "ref_marks_tail") if fn is ref_judging_cap_marks else (km.__dict__, "_caption_rows_since")
        real = ns[name]
        held_before = [e[2] for _k, e in before[0]]
        ns[name] = spy(real)
        try:
            got = fn(sid, caps)
            served = ("ok", json.dumps(got), any(got is m for m in held_before),
                      any(got is e[2] for e in km._JUDGING_MARKS_MEMO.values()))
        except Exception as e:                   # noqa: BLE001  a raise is part of the answer
            served = ("raise", type(e).__name__)
        finally:
            ns[name] = real
        memo = [(k, e[0], e[1], json.dumps(e[2]), e[3], e[4], any(e[2] is m for m in held_before))
                for k, e in km._JUDGING_MARKS_MEMO.items()]
        stats = {k: v - before[1].get(k, 0) for k, v in km._JUDGING_MARKS_STATS.items()}
        return served, memo, stats, calls

    def marks_both(self, label, sid, caps):
        before = (list(km._JUDGING_MARKS_MEMO.items()), dict(km._JUDGING_MARKS_STATS))
        ref = self.marks_run(ref_judging_cap_marks, sid, caps, before)
        km._JUDGING_MARKS_MEMO.clear()
        km._JUDGING_MARKS_MEMO.update(before[0])
        km._JUDGING_MARKS_STATS.clear()
        km._JUDGING_MARKS_STATS.update(before[1])
        got = self.marks_run(km._judging_cap_marks, sid, caps, before)
        self.assertEqual(got[0], ref[0], "%s: the marks served" % label)
        self.assertEqual(got[1], ref[1], "%s: the marks memo left" % label)
        self.assertEqual(got[2], ref[2], "%s: the counters moved" % label)
        self.assertEqual(got[3], ref[3], "%s: the since questions and their answers" % label)
        for old, new, _ans in got[3]:
            self.assertNotEqual(old, new, "%s: the marks never ask since of one version twice" % label)
        for k, v in got[2].items():
            if v:
                self.marks_kinds[k] += v

    def step(self, label, cur):
        """Ask every caller's questions at this step, through the old pairs and the folded one, of every mapping in the
        pool; `cur` is the mapping this build read, asked last so the marks memo moves as a build's would."""
        if not any(cur is m for m in self.served):
            self.served.append(cur)
        pool = [("served %d" % i, SID, m) for i, m in enumerate(self.served) if m is not cur]
        pool += [("a plain copy", SID, dict(cur)), ("an empty dict", SID, {}),
                 ("the shared empty mapping", SID, km._CAPTION_ROWS_EMPTY), ("another lane's sid", SID2, cur),
                 ("this build's", SID, cur)]
        for what, sid, m in pool:
            want = ref_lane_version(sid, m)
            self.assertEqual(km._caption_rows_version(sid, m), want, "%s, %s: the lane's version" % (label, what))
            if want not in self.versions:
                self.versions.append(want)
        for what, sid, m in pool:
            for hv in self.versions:
                want = lane_asks(ref_lane_version, ref_lane_since, sid, m, hv)
                self.assertEqual(lane_asks(km._caption_rows_version, km._caption_rows_since, sid, m, hv), want,
                                 "%s, %s, held %r: the lane's call site" % (label, what, hv))
                self.lane_kinds[want[1] if want[2] is None else (want[1], type(want[2][1]).__name__)] += 1
        for old in self.versions:
            for new in self.versions:
                got = outcome(km._caption_rows_since, old, new)
                self.assertEqual(got, outcome(ref_lane_since, old, new), "%s: the lane's since(%r, %r)" % (label, old, new))
                if isinstance(old, tuple) and isinstance(new, tuple) and old != new:
                    self.assertEqual(got, outcome(ref_marks_tail, old, new),
                                     "%s: the marks' tail(%r, %r)" % (label, old, new))
        for what, sid, m in pool:
            self.marks_both("%s, %s" % (label, what), sid, m)

    def test_every_caller_answers_as_its_old_pair_did_at_every_step(self):
        path = str(self.path)
        cur = km._captions(SID)
        self.assertIs(cur, km._CAPTION_ROWS_EMPTY, "(the premise) no file: the shared empty mapping")
        self.step("no file", cur)

        self.write([NO_ID])
        cur = km._captions(SID)
        self.assertEqual((len(cur), self.held()[3] is cur), (0, True), "(the premise) an empty mapping the memo holds")
        self.step("rows the fold skips", cur)

        for label, rows in (("the first rows", first_rows()),
                            ("a new row", [crow(D, "segment", T + 300, "fourth")]),
                            ("a drifted id", [crow((A[0] + 3, A[1]), "segment", T + 3, "first, drifted")]),
                            ("a '#p' row", [crow(A, "prompt", T, "gist of the first", p=True)]),
                            ("an overwrite", [crow(B, "segment", T + 100, "second, rewritten")])):
            was = self.held()
            self.append(rows)
            cur = km._captions(SID)
            now = self.held()
            self.assertEqual((now[3] is cur, now[:2], now[2]), (True, was[:2], was[2] + len(rows)),
                             "(the premise) %s: an append, the generation and base kept" % label)
            self.step(label, cur)

        g = self.held()[0]
        self.write(list(reversed(self.rows()))[1:])
        cur = km._captions(SID)
        self.assertNotEqual(self.held()[0], g, "(the premise) a rewrite is a new generation")
        self.step("the file rewritten", cur)

        with em._JSONL_CACHE_LOCK:
            em._cache_pop_locked(path)
        self.assertIs(self.held()[3], cur, "(the premise) the memo still holds the mapping its reader entry let go")
        self.step("the reader's entry evicted", cur)

        self.write([crow(A, "segment", T, "another lane's row", sid=SID2)], path=self.path2)
        km._captions(SID2)                       # a new version held: the sweep drops what the reader let go
        self.assertIsNone(self.held(), "(the premise) swept")
        self.step("the memo swept", cur)

        prev = cur
        cur = km._captions(SID)
        self.assertTrue(cur is not prev and dict(cur) == dict(prev), "(the premise) the same rows, a new mapping")
        self.step("the file read back", cur)

        self.append([crow((T + 400, "abab7777"), "segment", T + 400, "fifth")])
        cur = km._captions(SID)
        self.step("an append after the read back", cur)

        self.append([crow((T + 500, "cdcd8888"), "segment", T + 500, "sixth")])
        self.on_another_thread(lambda: km._captions(SID))
        self.assertIsNot(self.held()[3], cur, "(the premise) the memo holds the other thread's newer read")
        self.step("a version replaced on another thread", cur)
        cur = km._captions(SID)
        self.step("the other thread's version, served", cur)

        ver = km._caption_rows_version(SID, cur)
        self.write(list(reversed(self.rows())) + [crow((T + 600, "efef9999"), "segment", T + 600, "seventh"),
                                                  crow((T + 700, "f0f0aaaa"), "segment", T + 700, "eighth")])
        self.on_another_thread(lambda: km._captions(SID))
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE[path]
        self.assertTrue(ent[6] != ver[1] and len(ent[4]) > ver[3],
                        "(the premise) a new generation holding more records than the version this build read")
        self.step("the file rewritten on another thread", cur)

        os.unlink(self.path)
        cur = km._captions(SID)
        self.assertIs(cur, km._CAPTION_ROWS_EMPTY, "(the premise) removed")
        self.step("the file removed", cur)
        self.write(first_rows())
        cur = km._captions(SID)
        self.step("the file back", cur)

        # the road took every road of both callers, so the equalities above compared something
        for k in ("hit", "extend", "build", "uncached"):
            self.assertGreater(self.marks_kinds[k], 0, "the marks took the %s road" % k)
        for k in ("stands", ("re-caption", "list"), ("re-caption", "NoneType")):
            self.assertGreater(self.lane_kinds[k], 0, "the lane took the %r road" % (k,))
        self.assertGreaterEqual(sum(1 for v in self.versions if isinstance(v, tuple)), 10,
                                "a version per step that read rows the memo held")


class Exceptions(_Captions):
    def test_an_empty_mapping_is_a_version_to_the_lane_and_none_to_the_marks(self):
        """The lane prefix holds "empty" like any version (a later empty mapping stands; a mapping with rows
        re-captions); the marks derived an empty mapping (no marks) and neither held nor counted it. Both stand."""
        self.write([NO_ID])
        held_empty = km._captions(SID)
        self.assertIs(self.held()[3], held_empty, "(the premise) the memo holds an empty mapping of its own")
        for label, caps in (("the shared empty mapping", km._CAPTION_ROWS_EMPTY), ("an empty dict", {}),
                            ("an empty mapping the memo holds", held_empty)):
            self.assertEqual((ref_lane_version(SID, caps), ref_marks_version(SID, caps)), ("empty", None),
                             "%s: (the premise) where the old pairs parted" % label)
            self.assertEqual(km._caption_rows_version(SID, caps), "empty", "%s: the lane's answer" % label)
            marks = km._judging_cap_marks(SID, caps)
            self.assertEqual(list(marks), [], label)
            self.assertEqual(len(km._JUDGING_MARKS_MEMO), 0, "%s: not held by the marks" % label)
            self.assertEqual(sum(km._JUDGING_MARKS_STATS.values()), 0, "%s: and not counted" % label)

    def test_one_version_twice_is_the_answer_the_pairs_gave_apart_and_no_caller_asks_it(self):
        """While the reader's entry stands both pairs answered [] for one version twice; once it went, the marks' tail
        answered None and the lane's since still []. The folded since is the lane's. The marks serve a held version
        that stands before they ask since, so they never ask it."""
        self.write(first_rows())
        caps = km._captions(SID)
        v = km._caption_rows_version(SID, caps)
        self.assertIsInstance(v, tuple)
        self.assertEqual((ref_lane_since(v, v), ref_marks_tail(v, v), km._caption_rows_since(v, v)), ([], [], []))
        with em._JSONL_CACHE_LOCK:
            em._cache_pop_locked(str(self.path))
        self.assertEqual((ref_lane_since(v, v), ref_marks_tail(v, v)), ([], None),
                         "(the premise) the old pairs part once the reader's entry is gone")
        self.assertEqual(km._caption_rows_since(v, v), [], "the folded since is the lane's")
        m1 = km._judging_cap_marks(SID, caps)
        calls, real = [], km._caption_rows_since
        with mock.patch.object(km, "_caption_rows_since", lambda o, n: (calls.append((o, n)), real(o, n))[1]):
            m2 = km._judging_cap_marks(SID, caps)
        self.assertIs(m2, m1, "the held marks served")
        self.assertEqual(calls, [], "one version twice is a hit, never a since question")
        self.assertEqual((km._JUDGING_MARKS_STATS["build"], km._JUDGING_MARKS_STATS["hit"]), (1, 1))


class OnePair(unittest.TestCase):
    """Both memos ask the one pair by name. Road compares answers, which a private copy that answers alike keeps equal,
    and its spy sees only the marks' since; this holds the rest."""

    def test_the_lane_prefix_and_the_marks_ask_the_one_pair(self):
        for fn, since in ((km._lane_segments, "_caption_rows_since(held[9], cap_ver)"),
                          (km._judging_cap_marks, "_caption_rows_since(held[0], ver)")):
            src = inspect.getsource(fn)
            self.assertIn("_caption_rows_version(sid, caps)", src, fn.__name__)
            self.assertIn(since, src, fn.__name__)


if __name__ == "__main__":
    unittest.main()
