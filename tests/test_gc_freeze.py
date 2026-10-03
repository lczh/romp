#!/usr/bin/env python3
"""Lifecycle validation for the Road B freeze (issue #1735, kernel/gc_freeze.py).

The freeze keeps the loaded decoded heap out of the collector's walk. WHEN to reclaim is MEASURED, not
guessed: every session-end pop registers a weakref to the session with the controller, and the idle
tick judges each by observation (dead ref died by refcount, no reclaim; a live ref whose worker thread
still runs is kept; a live ref whose thread finished is a surviving cycle to reclaim, or a ref a live root keeps, which reads the same, costs one reclaim and is counted a survivor). These tests pin
that judgement's truth table on synthetic refs and a real SdkSession, that every session-end pop
registers the ended session, plus the load fold-in, the backstop, the safe knob parse, the env
switch, a weakref oracle over the freeze, and the pusher glue, busy cycles included (a busy cycle folds a
load in and bounds the backstop, and judges an ended session only before the first freeze). Every fixture is synthetic. Real-collector
tests unfreeze in tearDown so the freeze never leaks to another test.
"""
import gc
import json
import os
import sys
import sysconfig
import tempfile
import threading
import time
import unittest
import weakref
from datetime import datetime, timezone
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
GC_FREEZE = os.path.join(ROOT, "kernel", "gc_freeze.py")

sys.path.insert(0, os.path.join(ROOT, "tests"))
from romp_load import load_source   # brought in before the state preamble (the mkdtemp hook and TMPDIR redirect ride it)

# hermetic before any romp module load (never load romp modules against live state)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off")
os.environ["ROMP_POSTAL_CLIENT_ONLY"] = "1"

gf = load_source("romp_gc_freeze", GC_FREEZE)


class FakeGc:
    """Records the collector calls a reconcile makes, in order, so the tick logic is pinned without the collector."""
    def __init__(self):
        self.calls = []
    def collect(self, *a):
        self.calls.append("collect")
    def freeze(self):
        self.calls.append("freeze")
    def unfreeze(self):
        self.calls.append("unfreeze")


class TakingGc(FakeGc):
    """A FakeGc whose next collect empties the list set on `take`, the way the real collector takes an unreachable cycle:
    an owner held only there then dies by reference counting, so a release's cheap stage sees it gone."""
    def __init__(self):
        super().__init__()
        self.take = None
    def collect(self, *a):
        super().collect(*a)
        if self.take is not None:
            self.take.clear()
            self.take = None


class ThresholdGc(FakeGc):
    """A FakeGc that carries the collector's thresholds and records every read and write, so the full-collection trigger's
    gates are pinned without touching the interpreter's own collector."""
    def __init__(self, thresholds=(2000, 10, 10)):
        super().__init__()
        self.thresholds = tuple(thresholds)
    def get_threshold(self):
        self.calls.append("get_threshold")
        return self.thresholds
    def set_threshold(self, *t):
        self.calls.append(("set_threshold",) + t)
        self.thresholds = tuple(t)


class ZeroThirdGc(ThresholdGc):
    """A collector that does not keep a third threshold it is given: it stores every set (`thresholds`), yet reads the third
    back as 0 after any of them, as some interpreters were seen to."""
    def get_threshold(self):
        self.calls.append("get_threshold")
        return self.thresholds[:2] + (0,)


class ClampThirdGc(ThresholdGc):
    """A collector that stores a third threshold other than the one set: it clamps every value above 50 to 50, so a set of
    100 reads back 50 while a set of 20 is kept. Where the zero-reading fake starts from the 0 it reads, this one tells a
    put-back of the replaced value from a write of 0 or of the value read back."""
    CAP = 50
    def set_threshold(self, *t):
        self.calls.append(("set_threshold",) + t)
        self.thresholds = tuple(t[:2]) + (min(t[2], self.CAP),)


_FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


def _collector_keeps_threshold2():
    """Whether the raised trigger applies on this interpreter, asked of its collector the way apply_gen2_threshold asks: not
    a free-threaded build, and a third threshold set reads back as set. The thresholds are put back as they were."""
    if _FREE_THREADED:
        return False
    saved = gc.get_threshold()
    try:
        gc.set_threshold(saved[0], saved[1], saved[2] + 1)
        return gc.get_threshold()[2] == saved[2] + 1
    finally:
        gc.set_threshold(*saved)


_KEEPS_THRESHOLD2 = _collector_keeps_threshold2()


class Gen2Trigger(unittest.TestCase):
    """2026-10-03: with the freeze on, CPython's full-collection trigger (threshold2, stock 10) is raised, so the controller's
    fold-ins, not a full collection every eleventh young-generation collection, set when full collections run. Pinned on a
    recording fake: applied only with the controller enabled on a build that is not free-threaded, the first two
    thresholds kept, the third read back and the trigger recorded only once it reads the new value (else the replaced value
    is put back), floored at the stock 10, put back when the controller is turned off, and the knob's safe parse with its
    ceiling. ROMP_GC_FREEZE=off makes no collector call at all."""

    def _c(self, enabled=True, thresholds=(2000, 10, 10)):
        fake = ThresholdGc(thresholds)
        return gf.GcFreeze(enabled=enabled, gc=fake, clock=lambda: 0.0), fake

    def test_the_trigger_is_raised_with_the_freeze_on_and_read_back(self):
        c, fake = self._c()
        self.assertEqual(gf.apply_gen2_threshold(c, value=100, free_threaded=False), 100)
        self.assertEqual(fake.calls, ["get_threshold", ("set_threshold", 2000, 10, 100), "get_threshold"],
                         "the third threshold raised, the first two kept, then read back: %r" % (fake.calls,))
        self.assertEqual(c.gen2_threshold, 100)
        self.assertEqual(c.perf()["gen2Threshold"], 100, "/perf names the trigger the controller set")
        c, fake = self._c(enabled=False)
        self.assertIsNone(gf.apply_gen2_threshold(c, value=100, free_threaded=False))
        self.assertEqual(fake.calls, [], "the freeze off: no collector call at all, so the stock thresholds stand")
        self.assertIsNone(c.perf()["gen2Threshold"], "and /perf says the stock trigger stands")
        c, fake = self._c()
        self.assertIsNone(gf.apply_gen2_threshold(c, value=100, free_threaded=True))
        self.assertEqual(fake.calls, [], "not applied on a free-threaded build, a different collector: %r" % (fake.calls,))
        self.assertIsNone(c.perf()["gen2Threshold"])

    def test_a_collector_that_does_not_keep_the_value_gets_the_replaced_one_back(self):
        """Review 2026-10-03: the gate asks the collector, not the version. Where the third threshold does not read back as
        set, the replaced value is put back and nothing is recorded, so /perf's gen2Threshold stays null (a version gate
        had declined interpreters whose collector honors the value, and a looser one would record 100 where it reads 0)."""
        fake = ZeroThirdGc((2000, 10, 0))
        c = gf.GcFreeze(enabled=True, gc=fake, clock=lambda: 0.0)
        self.assertIsNone(gf.apply_gen2_threshold(c, value=100, free_threaded=False), "declined: the value was not kept")
        self.assertEqual(fake.calls, ["get_threshold", ("set_threshold", 2000, 10, 100), "get_threshold",
                                      ("set_threshold", 2000, 10, 0)],
                         "tried, read back, and the replaced value put back: %r" % (fake.calls,))
        self.assertEqual(fake.thresholds, (2000, 10, 0), "the collector holds what it held before the try")
        self.assertIsNone(c.gen2_threshold, "no trigger recorded")
        self.assertIsNone(c.gen2_replaced, "and no replaced value to put back later")
        self.assertIsNone(c.perf()["gen2Threshold"], "/perf says the stock trigger stands")
        c.enabled = False
        fake.calls.clear()
        self.assertIsNone(gf.apply_gen2_threshold(c, free_threaded=False))
        self.assertEqual(fake.calls, [], "turned off afterwards, there is nothing to put back")

    def test_a_decline_puts_back_the_replaced_value_not_what_the_collector_read(self):
        """Review 2026-10-03, round two: on a collector that stores a value other than the one set (this one clamps the
        third threshold to 50), the read-back sees 50, not 100, and declines; what goes back is the 20 it replaced, not a 0
        and not the 50 it read. The zero-reading case above starts at 0, so it passes under either wrong put-back."""
        fake = ClampThirdGc((2000, 10, 20))
        c = gf.GcFreeze(enabled=True, gc=fake, clock=lambda: 0.0)
        self.assertIsNone(gf.apply_gen2_threshold(c, value=100, free_threaded=False), "declined: 50 read back, not 100")
        self.assertEqual(fake.thresholds, (2000, 10, 20), "the replaced 20 is back: %r" % (fake.calls,))
        self.assertIsNone(c.gen2_threshold, "no trigger recorded")
        self.assertIsNone(c.gen2_replaced, "and no replaced value to put back later")
        self.assertIsNone(c.perf()["gen2Threshold"], "/perf says the stock trigger stands")

    def test_the_default_is_100_and_the_value_is_floored_at_the_stock_10(self):
        c, fake = self._c(thresholds=(700, 10, 10))
        self.assertEqual(gf.DEFAULT_GEN2_THRESHOLD, 100)
        self.assertEqual(gf.apply_gen2_threshold(c, free_threaded=False), 100, "the default value")
        self.assertEqual(fake.thresholds, (700, 10, 100))
        c, fake = self._c(thresholds=(700, 10, 10))
        self.assertEqual(gf.apply_gen2_threshold(c, value=5, free_threaded=False), 10,
                         "floored at the stock 10: the trigger can only be raised, never made more frequent")
        self.assertEqual(fake.thresholds, (700, 10, 10))

    def test_turning_the_controller_off_puts_back_the_trigger_it_replaced(self):
        c, fake = self._c(thresholds=(700, 10, 10))
        gf.apply_gen2_threshold(c, value=100, free_threaded=False)
        gf.apply_gen2_threshold(c, value=150, free_threaded=False)   # a second raise keeps the STOCK value to put back
        self.assertEqual(fake.thresholds, (700, 10, 150))
        c.enabled = False
        fake.calls.clear()
        self.assertIsNone(gf.apply_gen2_threshold(c, value=150, free_threaded=False))
        self.assertEqual(fake.thresholds, (700, 10, 10), "turned off, the stock trigger is back, not the first raise: %r" % (fake.calls,))
        self.assertIsNone(c.gen2_threshold)
        self.assertIsNone(c.perf()["gen2Threshold"], "and /perf says the stock trigger stands")
        fake.calls.clear()
        self.assertIsNone(gf.apply_gen2_threshold(c, free_threaded=False))
        self.assertEqual(fake.calls, [], "a second call while off has nothing to put back and touches nothing")
        # what is put back is the value replaced, not the floor: a collector that started at 20 gets its 20 back
        c, fake = self._c(thresholds=(700, 10, 20))
        self.assertEqual(gf.apply_gen2_threshold(c, value=100, free_threaded=False), 100)
        self.assertEqual(fake.thresholds, (700, 10, 100))
        c.enabled = False
        self.assertIsNone(gf.apply_gen2_threshold(c, free_threaded=False))
        self.assertEqual(fake.thresholds, (700, 10, 20), "the replaced 20 is back, not the stock 10: %r" % (fake.calls,))
        # a trigger someone else set after the raise is theirs: turning the controller off leaves it
        c, fake = self._c(thresholds=(700, 10, 10))
        gf.apply_gen2_threshold(c, value=100, free_threaded=False)
        fake.thresholds = (700, 10, 40)
        c.enabled = False
        self.assertIsNone(gf.apply_gen2_threshold(c, free_threaded=False))
        self.assertEqual(fake.thresholds, (700, 10, 40), "a trigger set since the raise stays: %r" % (fake.calls,))
        self.assertIsNone(c.gen2_threshold)

    def test_the_gen2_knob_parses_safely_and_floors_at_the_stock_value(self):
        k = "ROMP_GC_GEN2_THRESHOLD"
        self.assertEqual(gf.gen2_threshold_from_env({}), (100, None), "absent: the default, no complaint")
        self.assertEqual(gf.gen2_threshold_from_env({k: ""}), (100, None), "empty: the default")
        self.assertEqual(gf.gen2_threshold_from_env({k: "  "}), (100, None), "whitespace: the default")
        self.assertEqual(gf.gen2_threshold_from_env({k: "abc"}), (100, "abc"), "a bad value falls back and is named")
        self.assertEqual(gf.gen2_threshold_from_env({k: "250"}), (250, None))
        self.assertEqual(gf.gen2_threshold_from_env({k: " 16 "}), (16, None))
        self.assertEqual(gf.gen2_threshold_from_env({k: "5"}), (10, None), "floored at the stock 10, a valid integer: no complaint")
        self.assertEqual(gf.gen2_threshold_from_env({k: "0"}), (10, None))
        self.assertEqual(gf.gen2_threshold_from_env({k: "-3"}), (10, None))

    def test_a_knob_past_the_collectors_ceiling_falls_back_like_a_non_integer(self):
        """Review 2026-10-03: gc.set_threshold takes a C int and raises past 2147483647, so a knob above it would have left
        the stock 10 standing (a failed apply), under the 100 a non-integer gets. It falls back to the default instead, and
        is named for the caller to say and count; the largest C int itself is taken. The ceiling is the real collector's."""
        k = "ROMP_GC_GEN2_THRESHOLD"
        self.assertEqual(gf.gen2_threshold_from_env({k: "2147483648"}), (100, "2147483648"), "one past the ceiling")
        self.assertEqual(gf.gen2_threshold_from_env({k: "99999999999999999999"}), (100, "99999999999999999999"))
        self.assertEqual(gf.gen2_threshold_from_env({k: "2147483647"}), (2147483647, None), "the ceiling itself is taken")
        self.assertEqual(gf.MAX_GEN2_THRESHOLD, 2147483647)
        saved = gc.get_threshold()
        self.addCleanup(gc.set_threshold, *saved)
        gc.set_threshold(saved[0], saved[1], gf.MAX_GEN2_THRESHOLD)          # the real collector takes the ceiling
        with self.assertRaises(OverflowError, msg="and raises one past it, the value the knob now refuses"):
            gc.set_threshold(saved[0], saved[1], gf.MAX_GEN2_THRESHOLD + 1)


class TriggerLogic(unittest.TestCase):
    def test_the_load_fold_in_and_the_backstop(self):
        fake = FakeGc()
        c = gf.GcFreeze(enabled=True, load_trees=8, backstop_foldins=2, gc=fake, clock=lambda: 0.0)
        self.assertIsNone(c.tick(inserts=0), "nothing loaded yet: no freeze")
        self.assertEqual(c.tick(inserts=3), "initial", "the first load makes the initial freeze")
        self.assertEqual(fake.calls, ["collect", "freeze"], "the initial freeze collects then freezes, no unfreeze")
        self.assertIsNone(c.tick(inserts=3 + 7), "a load under the threshold does not fold in")
        fake.calls.clear()
        self.assertEqual(c.tick(inserts=3 + 8), "load", "a load past the threshold folds in cheaply")
        self.assertEqual(fake.calls, ["collect", "freeze"], "a load fold-in walks only the unfrozen: no unfreeze")
        self.assertIsNone(c.tick(inserts=3 + 8), "no new load and nothing ended: nothing due (a record-cache pop is not a trigger)")
        # the backstop: after backstop_foldins load fold-ins since the last reclaim, a reclaim runs anyway
        self.assertEqual(c.tick(inserts=3 + 16), "load")   # fold-in 2 -> _foldins at the backstop
        fake.calls.clear()
        self.assertEqual(c.tick(inserts=3 + 16), "backstop", "the backstop reclaims: unfreeze, collect, re-freeze")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"])
        self.assertIsNone(c.tick(inserts=3 + 16), "the backstop reset _foldins: not due again until the next load or ended cycle")
        # PR 1999 review: a RELEASE resets the fold-in counter too (not only a backstop). After a release, the next backstop
        # needs a FULL backstop_foldins loads; guarding the reset on kind=="backstop" alone would fire the backstop a load early.
        ins = 3 + 24
        self.assertEqual(c.tick(inserts=ins), "load", "one load fold-in since the backstop (_foldins -> 1)")
        owner = _Owner()                                          # held in a local: alive with a finished worker -> owed (the FakeGc collect is a no-op)
        fin = _FakeThread(alive=False)   # a local, so the weakref resolves and is_alive() False is what decides (review PR 2042 low)
        c.note_ended(owner, thread=fin)
        self.assertEqual(c.tick(inserts=ins), "release", "the owed cyclic ref reclaims and resets _foldins")
        ins += 8; self.assertEqual(c.tick(inserts=ins), "load", "fold-in 1 since the release")
        ins += 8; self.assertEqual(c.tick(inserts=ins), "load", "fold-in 2 since the release: still a load, not an early backstop")
        self.assertEqual(c.tick(inserts=ins), "backstop", "now the backstop fires: a full backstop_foldins loads since the release")

    def test_a_pre_freeze_ended_ref_is_taken_by_the_initial_collect_not_a_wasted_reclaim(self):
        # a session ended BEFORE the first freeze: the initial collect takes any garbage; no separate reclaim. Both a dead
        # ref and a LIVE ref (with a finished worker, both kept in locals) are registered before the first tick, so the pin
        # fails if the release branch is moved ahead of the initial-freeze check (the live ref would then drive an unfreeze).
        fake = FakeGc()
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=fake, clock=lambda: 0.0)
        dead = _Owner()
        c.note_ended(dead, thread=None)
        del dead                                     # the ref is now dead: acyclic, gone
        live = _Owner(); fin = _FakeThread(alive=False)   # a LIVE pre-freeze ref with a finished worker, both in locals
        c.note_ended(live, thread=fin)
        self.assertEqual(c.tick(inserts=3), "initial", "the first tick freezes; the pre-freeze ended refs are judged then, not a separate reclaim")
        self.assertEqual(fake.calls, ["collect", "freeze"], "no unfreeze: the initial collect already took any pre-freeze garbage")
        self.assertEqual(c._ended, [], "the pre-freeze refs are consumed by the initial judgement, never carried")
        self.assertIsNone(c.tick(inserts=3), "a second tick with nothing new is None: the pre-freeze refs did not linger into a reclaim")

    def test_a_pre_freeze_ended_ref_is_taken_by_the_busy_initial_collect_too(self):
        """The busy twin of the test above (review 2026-09-28). On a pusher that never idles the first freeze lands on a busy
        cycle. Left unjudged there, a pre-freeze ref a live root keeps, its worker finished, is frozen with the heap and costs
        a full release (an unfreeze and a walk of the whole frozen heap) at the first idle cycle, possibly much later, where
        the idle initial freeze drops it at no cost. The busy initial freeze judges the pre-freeze refs the same way."""
        fake = FakeGc()
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=fake, clock=lambda: 0.0)
        dead = _Owner()
        c.note_ended(dead, thread=None)
        del dead                                     # the ref is now dead: acyclic, gone
        live = _Owner(); fin = _FakeThread(alive=False)   # a LIVE pre-freeze ref with a finished worker, both in locals
        c.note_ended(live, thread=fin)
        self.assertEqual(c.tick_busy(inserts=3), "initial", "the first busy tick with anything loaded freezes")
        self.assertEqual(fake.calls, ["collect", "freeze"], "no unfreeze: the initial collect already took any pre-freeze garbage")
        self.assertEqual(c._ended, [], "the busy initial freeze consumed the pre-freeze refs as the idle one does, never carried")
        self.assertIsNone(c.tick(inserts=3), "the first idle cycle after it owes no release: the pre-freeze ref was dropped")
        self.assertEqual(fake.calls, ["collect", "freeze"], "and nothing walked the frozen heap for it")

    def test_the_env_switch_turns_it_off(self):
        self.assertFalse(gf.enabled_from_env({"ROMP_GC_FREEZE": "off"}))
        self.assertFalse(gf.enabled_from_env({"ROMP_GC_FREEZE": "0"}))
        self.assertTrue(gf.enabled_from_env({}), "on by default")
        self.assertTrue(gf.enabled_from_env({"ROMP_GC_FREEZE": "on"}))
        fake = FakeGc()
        c = gf.GcFreeze(enabled=False, gc=fake)
        self.assertIsNone(c.tick(inserts=100))
        self.assertEqual(fake.calls, [], "a disabled controller never touches the collector")
        fin = _FakeThread(alive=False)   # a local for uniformity; here the DISABLED early return decides: note_ended returns before storing the pair, so the thread is never read (review PR 2049)
        c.note_ended(_Owner(), thread=fin)   # PR 1999 review: note_ended early-returns when disabled
        self.assertEqual(c._ended, [], "a disabled controller registers no ended session (else it would leak a pair per end for the process life)")

    def test_the_load_threshold_parses_safely_and_floors_at_one(self):
        self.assertEqual(gf.load_trees_from_env({}), (gf.DEFAULT_LOAD_TREES, None), "absent: the default, no complaint")
        self.assertEqual(gf.load_trees_from_env({"ROMP_GC_FREEZE_LOAD_TREES": "  "}), (gf.DEFAULT_LOAD_TREES, None), "whitespace: the default")
        self.assertEqual(gf.load_trees_from_env({"ROMP_GC_FREEZE_LOAD_TREES": "abc"}), (gf.DEFAULT_LOAD_TREES, "abc"), "a bad value falls back and is named")
        self.assertEqual(gf.load_trees_from_env({"ROMP_GC_FREEZE_LOAD_TREES": "20"}), (20, None))
        self.assertEqual(gf.load_trees_from_env({"ROMP_GC_FREEZE_LOAD_TREES": "0"})[0], 1, "floored at 1")
        self.assertEqual(gf.load_trees_from_env({"ROMP_GC_FREEZE_LOAD_TREES": "-5"})[0], 1, "a negative is floored at 1 too")
        self.assertEqual(gf.GcFreeze(load_trees=0).load_trees, 1, "the controller floors its threshold too")


class _FakeThread:
    def __init__(self, alive):
        self._alive = alive
    def is_alive(self):
        return self._alive


class _Owner:
    """A weakref-able stand-in for an ended session (SimpleNamespace is not weakref-able)."""


class EndedTruthTable(unittest.TestCase):
    """The measured judgement, the verifier's cases a to e (its probe drives real sessions; this pins the decision
    on synthetic weakref-able owners with a controllable worker thread, deterministically). resolve_ended returns
    whether a reclaim is owed, and a reclaim is NEVER owed for a dead ref."""
    def _controller(self):
        return gf.GcFreeze(enabled=True, gc=FakeGc(), clock=lambda: 0.0)

    def test_a_dead_ref_is_acyclic_and_owes_no_reclaim(self):     # (a) run to exit: died by refcount
        c = self._controller()
        s = _Owner()
        fin = _FakeThread(alive=False)   # a local for uniformity; here the DEAD REF decides: `s` is deleted below, so the dead-ref skip takes it before the thread is read (review PR 2049)
        c.note_ended(s, thread=fin)
        del s
        self.assertFalse(c.resolve_ended(), "a ref that died by refcount owes no reclaim (acyclic)")
        self.assertEqual(c._ended, [], "and it is dropped")

    def test_a_live_ref_with_a_finished_thread_is_cyclic_and_owes_a_reclaim(self):  # (b) client survives, (e) teardown raise
        c = self._controller()
        s = _Owner()
        fin = _FakeThread(alive=False)                # a local, so tref() resolves and is_alive() False is what decides (not a dead tref)
        c.note_ended(s, thread=fin)
        self.assertTrue(c.resolve_ended(), "a ref still alive with its worker thread finished is a surviving cycle: reclaim")
        self.assertEqual(c._ended, [], "and it is dropped after the reclaim it owes")

    def test_a_sid_less_owed_root_yields_one_empty_string_by_equality(self):
        """PR 2042 review (attribution): a sid-less owed root gathers one empty string into last_release_sids, so assert by
        EQUALITY, never truthiness (a truthiness check would pass a bug that gathered nothing; [''] is truthy but wrong)."""
        c = self._controller()
        s = _Owner()                                  # no `.sid`
        fin = _FakeThread(alive=False)
        c.note_ended(s, thread=fin)
        self.assertTrue(c.resolve_ended())
        self.assertEqual(c.last_release_sids, [""], "one owed root with no sid: exactly one empty string, by equality: %r" % c.last_release_sids)

    def test_a_live_ref_with_a_running_thread_is_kept_then_reclaimed_when_it_finishes(self):  # (c) kill mid-turn
        c = self._controller()
        s = _Owner()
        th = _FakeThread(alive=True)
        c.note_ended(s, thread=th)
        self.assertFalse(c.resolve_ended(), "a live ref whose thread still runs is not garbage yet: no reclaim")
        self.assertEqual(len(c._ended), 1, "it is kept for the next tick")
        th._alive = False                             # the thread finishes
        self.assertTrue(c.resolve_ended(), "now the thread is finished and the ref is alive: a surviving cycle, reclaim")
        self.assertEqual(c._ended, [], "and dropped")

    def test_the_tick_reclaims_a_real_cyclic_ended_session(self):
        # an executed oracle on a real SdkSession: a live client that refers back is a cycle; ended with a finished
        # thread, the STAGED release runs (collect with the freeze in place, then unfreeze + collect, then re-freeze).
        # The session's own never-started worker Thread would keep it alive by its `_target`, so the CLIENT cycle would
        # be redundant; rebind s.thread to a finished stand-in before the registration so the client cycle is the sole
        # keeper (review PR 1999 tests). Under gc.disable() so a young-generation collect cannot take it before the read.
        sbmod = load_source("romp_sdk_backend_ended", os.path.join(ROOT, "kernel", "sdk_backend.py"))
        class StubBackend:
            state_dir = tempfile.mkdtemp()
            def _update_reg(self, *a, **k): pass
            def _log(self, *a, **k): pass
        gc.disable()
        self.addCleanup(gc.enable)
        s = sbmod.SdkSession(StubBackend(), {"sid": "11111111-2222-3333-4444-555555555555", "name": "web", "cwd": "/tmp"})
        s.thread = _FakeThread(alive=False)           # a finished stand-in, holding nothing: the client cycle alone keeps s alive
        class Client: pass
        client = Client(); client.session = s; s.client = client   # the surviving-client cycle
        w = weakref.ref(s)
        fake = FakeGc()
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=fake, clock=lambda: 0.0)
        c.tick(inserts=1)                             # an initial freeze so a reclaim is meaningful
        c.note_ended(s, thread=s.thread)              # the session ended, its worker thread finished
        del s, client
        fake.calls.clear()
        self.assertEqual(c.tick(inserts=1), "release", "the tick observes the cyclic ended session and reclaims")
        self.assertEqual(fake.calls, ["collect", "unfreeze", "collect", "freeze"],
                         "the staged release: a cheap collect with the freeze in place, then unfreeze + collect (a survivor under the no-op fake), then re-freeze")
        self.assertIsNotNone(w(), "the cyclic session stays alive under the FakeGc (its collect is a no-op): this pins the reconcile KIND, not the collection")


class EndedLockConcurrency(unittest.TestCase):
    """LOW 1, the concurrency fix, pinned DETERMINISTICALLY (no wall-clock, no statistical race). resolve_ended sets
    self._ended = keep and judges the OLD list under _ended_lock, so a note_ended landing on another thread mid-judgement
    blocks and lands in the NEW list, never in the old one the judgement is consuming. A stub worker thread whose is_alive()
    pauses the judgement mid-flight while the lock is held; a second registration then lands only AFTER the judgement
    returns, unjudged in the live list, and is judged at the NEXT tick. At affd9abf there is no lock: resolve_ended iterates
    self._ended live and rebinds after the loop, so a registration landing mid-judgement is consumed by the index-based loop
    and the rebind drops it (this test reds there by copy-aside; the red is quoted in the PR body)."""

    class _Gate:
        """A stand-in worker thread: is_alive() signals it has paused the judgement, then blocks until released, then reads
        as still running (so the paused owner is KEPT, never itself a reclaim)."""
        def __init__(self, entered, release):
            self._entered, self._release = entered, release
            self.released = None                          # set True when the gate was released, False on a pathological timeout (PR 1999 round-four low)
        def is_alive(self):
            self._entered.set()
            self.released = self._release.wait(30)        # a generous bound; the test asserts it was RELEASED, not a vacuous timeout
            return True

    def test_resolve_ended_holds_the_lock_so_a_concurrent_registration_is_never_dropped(self):
        c = gf.GcFreeze(enabled=True, gc=FakeGc(), clock=lambda: 0.0)
        entered, release, at_lock = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)                              # PR 1999 round-four low: a failed pre-release assertion must still free R, not park it to the bound
        o1, o2 = _Owner(), _Owner()
        gate = self._Gate(entered, release)                       # a strong ref: the controller keeps only a weakref to the thread
        fin = _FakeThread(alive=False)                            # P2's finished worker thread (a strong ref, likewise)
        c.note_ended(o1, thread=gate)                             # P1: its is_alive pauses the judgement mid-flight
        box = {}
        R = threading.Thread(target=lambda: box.__setitem__("reclaim1", c.resolve_ended()), daemon=True)
        R.start()
        self.assertTrue(entered.wait(10), "the judgement reached P1 and paused mid-flight")
        # a second session ends WHILE the judgement is mid-flight; its worker thread has finished, so were it judged in this
        # pass it would owe a reclaim and leave the live list. It must not be: the lock holds the old list steady.
        lock = getattr(c, "_ended_lock", None)
        if lock is not None:                                       # the head: note_ended blocks on the lock; spy it to know it arrived
            real = lock                                            # R already holds the real lock (acquired before this swap); the spy sees only B's acquire
            class _SpyLock:
                def acquire(self, *a, **k):
                    at_lock.set()                                  # the concurrent note_ended has reached the lock (and will block: R holds it)
                    return real.acquire(*a, **k)
                def release(self): return real.release()
                def __enter__(self): self.acquire(); return self
                def __exit__(self, *a): self.release()
            c._ended_lock = _SpyLock()
            B = threading.Thread(target=lambda: c.note_ended(o2, thread=fin), daemon=True)
            B.start()
            self.assertTrue(at_lock.wait(10), "the concurrent note_ended reached the lock while the judgement held it")
            release.set()
            R.join(10); B.join(10)
        else:                                                      # affd9abf: no lock; the append lands unguarded, mid-loop, on this thread
            c.note_ended(o2, thread=fin)
            release.set()
            R.join(10)
        self.assertFalse(box["reclaim1"], "the judgement returned before the append landed: it judged only P1 (kept), never the "
                                          "mid-flight registration (at affd9abf the live loop consumed it and returned a reclaim)")
        self.assertTrue(any(sref() is o2 for sref, _ in c._ended), "the mid-judgement registration sits in the live list, never dropped")
        # judged at the NEXT tick: P2 is alive with a finished thread, so it owes a reclaim then and is dropped
        self.assertTrue(c.resolve_ended(), "the next tick judges the kept registration: a surviving cycle owes its reclaim")
        self.assertFalse(any(sref() is o2 for sref, _ in c._ended), "and P2 is dropped after the reclaim it owes")
        self.assertTrue(gate.released, "the gate was RELEASED mid-flight, not a vacuous 30 s timeout (PR 1999 round-four low)")


class DoubleController:
    """Records the tick calls, so the pusher tick's guard and error handling are pinned without the collector. `ticks` are the
    idle-cycle calls (the full tick), `busy_ticks` the busy-cycle calls (the fold-in-only tick)."""
    def __init__(self, enabled=True, raise_tick=False):
        self.enabled = enabled
        self._raise = raise_tick
        self.ticks = []
        self.busy_ticks = []
    def tick(self, inserts):
        self.ticks.append(inserts)
        if self._raise:
            raise RuntimeError("tick blew up")
        return "load"
    def tick_busy(self, inserts):
        self.busy_ticks.append(inserts)
        if self._raise:
            raise RuntimeError("busy tick blew up")
        return "load"


class PusherTick(unittest.TestCase):
    """The pusher's end-of-cycle seam, pinned in-process (the kernel's call site alone had no teeth)."""
    def _stats(self, inserts=10):
        return lambda: {"inserts": inserts}

    def test_an_idle_cycle_ticks_a_busy_cycle_ticks_busy_and_the_first_cycle_neither(self):
        # 2026-09-28: a busy cycle used to skip the reconcile altogether, so a pusher that never idled never folded a load
        # in; now a busy, non-first cycle calls the fold-in-only tick_busy and an idle one the full tick
        errs = []
        c = DoubleController()
        gf.pusher_tick(c, idle=True, first=False, stats_fn=self._stats(), on_error=errs.append)
        self.assertEqual((c.ticks, c.busy_ticks), ([10], []), "an idle, non-first cycle runs the full tick off the insert counter")
        c = DoubleController()
        gf.pusher_tick(c, idle=False, first=False, stats_fn=self._stats(), on_error=errs.append)
        self.assertEqual((c.ticks, c.busy_ticks), ([], [10]), "a busy, non-first cycle runs tick_busy off the insert counter, never the full tick")
        for idle in (True, False):
            c = DoubleController()
            gf.pusher_tick(c, idle=idle, first=True, stats_fn=self._stats(), on_error=errs.append)
            self.assertEqual((c.ticks, c.busy_ticks), ([], []), "the boot's first cycle never ticks, idle or busy: idle=%r" % idle)
        for idle in (True, False):
            c = DoubleController(enabled=False)
            gf.pusher_tick(c, idle=idle, first=False, stats_fn=self._stats(), on_error=errs.append)
            self.assertEqual((c.ticks, c.busy_ticks), ([], []), "a disabled controller never ticks: idle=%r" % idle)
        self.assertIsNone(gf.GcFreeze(enabled=False, gc=FakeGc()).tick_busy(10), "a disabled controller's busy tick is a no-op")
        self.assertEqual(errs, [], "no errors on the happy paths")

    def test_a_raising_stats_read_or_tick_is_handed_to_on_error_and_never_propagates(self):
        errs = []
        def boom():
            raise RuntimeError("cache stats read failed")
        gf.pusher_tick(DoubleController(), idle=True, first=False, stats_fn=boom, on_error=errs.append)   # must not raise
        self.assertEqual(len(errs), 1, "the failing read is counted once")
        errs2 = []
        c = DoubleController(raise_tick=True)
        gf.pusher_tick(c, idle=True, first=False, stats_fn=self._stats(), on_error=errs2.append)   # must not raise
        self.assertEqual(len(errs2), 1, "a raising tick is caught and counted once")
        self.assertEqual(len(c.ticks), 1, "the tick was attempted")
        errs3 = []
        c = DoubleController(raise_tick=True)
        gf.pusher_tick(c, idle=False, first=False, stats_fn=self._stats(), on_error=errs3.append)   # must not raise
        self.assertEqual(len(errs3), 1, "a raising busy tick is caught and counted once too")
        self.assertEqual(len(c.busy_ticks), 1, "the busy tick was attempted")

    def _seam(self, c, errs):
        """One end-of-cycle call through the real seam, with the record cache's insert counter at `ins`."""
        return lambda idle, first, ins: gf.pusher_tick(c, idle=idle, first=first, stats_fn=lambda: {"inserts": ins},
                                                       on_error=errs.append)

    def test_a_busy_cycle_folds_in_a_material_load(self):
        """2026-09-28: a pusher whose cycles are almost never idle never folded a load in, so everything loaded after the
        first freeze stayed in the collector's walk and every automatic full collection walked it again. A busy cycle now
        runs the LOAD fold-in once the insert counter has grown by load_trees since the last freeze: a walk of the unfrozen
        objects only, which the automatic collections were walking anyway. Before this change every busy tick returned None."""
        fake, errs = FakeGc(), []
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=fake, clock=lambda: 0.0)
        t = self._seam(c, errs)
        self.assertIsNone(t(True, True, 40), "the boot's first cycle never reconciles")
        self.assertEqual(t(True, False, 40), "initial", "the first idle cycle makes the initial freeze")
        kinds = {ins: t(False, False, ins) for ins in range(41, 57)}
        self.assertEqual({i: k for i, k in kinds.items() if k is not None}, {48: "load", 56: "load"},
                         "busy cycles fold in exactly when the insert counter has grown by load_trees: %r" % kinds)
        self.assertEqual(fake.calls, ["collect", "freeze"] * 3, "the initial freeze and two busy fold-ins, each walking only the unfrozen: no unfreeze")
        self.assertEqual((c.freezes, c.reclaims, c.last_kind), (3, 0, "load"), "/perf reads the busy fold-ins: %r" % c.perf())
        self.assertEqual(c.perf()["busyRuns"], 2, "the two busy fold-ins are counted apart on /perf; the idle initial freeze is not one: %r" % c.perf())
        self.assertEqual(errs, [])

    def test_a_pusher_that_never_idles_still_freezes(self):
        """2026-09-28: the initial freeze used to wait for an idle cycle, so a kernel with no idle cycle after its first never
        froze at all. A busy cycle makes the initial freeze once anything is loaded, and keeps folding in after it. The first
        busy freeze lands under the load bound (3 inserts of 8), since a freeze of an unfrozen controller at a load past the
        bound reads `initial` through the load branch too, so a pin past the bound could not tell the initial branch was gone
        (review 2026-09-28: a busy pusher with 1 to 7 inserts then made no freeze until the 8th)."""
        fake, errs = FakeGc(), []
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=fake, clock=lambda: 0.0)
        t = self._seam(c, errs)
        self.assertIsNone(t(False, True, 3), "the boot's first cycle never reconciles, busy or not")
        self.assertIsNone(gf.pusher_tick(c, idle=False, first=False, stats_fn=lambda: {"inserts": 0}, on_error=errs.append),
                          "nothing loaded yet: a busy cycle makes no freeze")
        self.assertEqual(t(False, False, 3), "initial", "the first busy cycle with anything loaded makes the initial freeze, under the load bound")
        self.assertEqual(t(False, False, 11), "load", "and the busy cycles fold in after it")
        self.assertEqual(fake.calls, ["collect", "freeze"] * 2)
        self.assertEqual(c.perf()["busyRuns"], 2, "a busy initial freeze counts as a busy run: %r" % c.perf())
        self.assertEqual(errs, [])

    def test_the_backstop_is_bounded_on_a_pusher_that_never_idles(self):
        """2026-09-28: busy fold-ins freeze live cyclic objects that may die later, and the backstop reclaim that bounds them
        runs on an idle cycle. A busy cycle leaves it to an idle one for one full backstop window (the frozen-heap walk is the
        pause the idle boundary exists for), then takes it itself once the fold-in count reaches twice backstop_foldins: the
        bound keys on the fold-in count, never a timer."""
        fake, errs = FakeGc(), []
        c = gf.GcFreeze(enabled=True, load_trees=8, backstop_foldins=2, gc=fake, clock=lambda: 0.0)
        t = self._seam(c, errs)
        t(False, True, 40)
        self.assertEqual(t(False, False, 41), "initial")
        self.assertEqual([t(False, False, 49), t(False, False, 57)], ["load", "load"], "fold-ins 1 and 2: the idle backstop would be due now")
        self.assertIsNone(t(False, False, 57), "a busy cycle leaves the backstop to an idle cycle for a full window")
        self.assertEqual([t(False, False, 65), t(False, False, 73)], ["load", "load"], "fold-ins 3 and 4: still loads on busy cycles")
        fake.calls.clear()
        self.assertEqual(t(False, False, 81), "backstop", "a full window passed with no idle cycle: the busy cycle runs the backstop, even with a load also due")
        self.assertEqual(fake.calls, ["unfreeze", "collect", "freeze"], "the backstop walks the frozen heap")
        self.assertEqual((c.reclaims, c._foldins), (1, 0), "a reclaim, and the fold-in count restarts")
        self.assertIsNone(t(False, False, 81), "nothing due right after the backstop: its re-freeze folded the load in")
        self.assertEqual(errs, [])

    def test_a_busy_cycle_after_the_first_freeze_never_judges_the_ended_sessions(self):
        """2026-09-28: the release (and the unfreeze its survivor drives) stays on the idle boundary. Once a freeze is held, a
        busy cycle folds a load in with an owed ended session registered, leaves it registered, never unfreezes; the next idle
        cycle releases it (before the first freeze a busy cycle judges them, as the idle one does, pinned in TriggerLogic)."""
        fake, errs = FakeGc(), []
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=fake, clock=lambda: 0.0)
        t = self._seam(c, errs)
        t(True, True, 40)
        self.assertEqual(t(True, False, 40), "initial")
        owner = _Owner(); fin = _FakeThread(alive=False)   # locals: alive with a finished worker, so owed at the next judgement
        c.note_ended(owner, thread=fin)
        fake.calls.clear()
        self.assertEqual(t(False, False, 48), "load", "a busy cycle past the threshold folds in, it does not release")
        self.assertEqual(len(c._ended), 1, "and leaves the owed ended session registered, unjudged")
        self.assertNotIn("unfreeze", fake.calls, "a busy cycle never walks the frozen heap for a release")
        self.assertEqual(t(True, False, 48), "release", "the next idle cycle judges it and releases")
        self.assertEqual(c._ended, [])
        self.assertEqual(errs, [])

    def test_a_busy_run_clears_the_last_release_sids(self):
        """Review 2026-09-28: after the first freeze a busy cycle judges nothing, so a busy load right after a live-root
        release left `lastReleaseSids` naming the released session beside the load's kind and the survivor count every run
        rebinds (0). On /perf that is the shape of a cheap-collect release of that session, where a full-heap pause had
        freed nothing. A busy run judged nothing and owed nothing, so it clears the sids: the three fields then describe one
        run, a busy load reading no sids, 0 survivors and `load`, a busy backstop the same with `backstop`. A busy cycle with
        nothing due runs nothing and keeps the last run's row."""
        fake, errs = TakingGc(), []
        c = gf.GcFreeze(enabled=True, load_trees=8, backstop_foldins=1, gc=fake, clock=lambda: 0.0)
        t = self._seam(c, errs)
        def fields():
            pf = c.perf()
            return (pf["lastReleaseSids"], pf["lastReleaseSurvivors"], pf["lastReconcileKind"])
        t(True, True, 40)
        self.assertEqual(t(True, False, 40), "initial")
        root = _Owner(); root.sid = "abcdef12-3456-7890-abcd-ef1234567890"   # held in a local: a live root keeps it through the reclaim
        fin = _FakeThread(alive=False)
        c.note_ended(root, thread=fin)
        self.assertEqual(t(True, False, 40), "release", "an idle cycle releases the owed ref")
        self.assertEqual(fields(), (["abcdef12"], 1, "release"), "a live-root release: its sid, one survivor, the kind: %r" % c.perf())
        # review 2026-09-29: a busy cycle with nothing due ran nothing, so it keeps the release's row, and /perf still names
        # the session a live root kept through that wasted pause
        self.assertIsNone(t(False, False, 41), "a busy cycle with nothing due runs nothing")
        self.assertEqual(fields(), (["abcdef12"], 1, "release"), "and keeps the release's row: its sid, the survivor, the kind: %r" % c.perf())
        self.assertEqual(t(False, False, 48), "load", "a busy cycle folds the next load in")
        self.assertEqual(fields(), ([], 0, "load"), "the busy load owed nothing: no sids, no survivor, a load: %r" % c.perf())
        # a cheap-collect release (judged `load`) names its owed sid and brings the fold-ins to twice the bound, so the next
        # busy cycle runs the backstop, which judged nothing either and clears the sids too
        held = [_Owner()]; held[0].sid = "beadfeed-1234-5678-9abc-def012345678"   # held only in the list the next collect empties
        fin2 = _FakeThread(alive=False)
        c.note_ended(held[0], thread=fin2)
        fake.take = held
        self.assertEqual(t(True, False, 48), "load", "the cheap collect took the released owner whole: a load pass")
        self.assertEqual(fields(), (["beadfeed"], 0, "load"), "a cheap-collect release names its owed sid: %r" % c.perf())
        self.assertEqual(t(False, False, 48), "backstop", "two fold-ins since the reclaim, twice the bound: the busy backstop")
        self.assertEqual(fields(), ([], 0, "backstop"), "the busy backstop owed nothing: no sids, no survivor, the kind: %r" % c.perf())
        self.assertEqual(errs, [])

    def test_the_kernels_tick_wrapper_ticks_and_counts_a_raising_tick(self):
        km = load_source("romp_kernel_gcf_tick", os.path.join(BIN, "romp-kernel"))
        saved_gf, saved_stats = km._GC_FREEZE, km.em.record_cache_stats
        saved_errs, saved_said = km._GC_FREEZE_ERRORS[0], km._GC_FREEZE_SAID[0]
        try:
            km.em.record_cache_stats = lambda: {"inserts": 3}
            d = DoubleController()
            km._GC_FREEZE = d
            km._gc_freeze_tick(True, False)
            self.assertEqual(d.ticks, [3], "the kernel tick reads inserts and ticks on an idle non-first cycle")
            d.ticks.clear()
            km._gc_freeze_tick(True, True)
            self.assertEqual(d.ticks, [], "the boot's first cycle does not tick")
            km._gc_freeze_tick(False, False)
            self.assertEqual((d.ticks, d.busy_ticks), ([], [3]), "a busy non-first cycle reaches the busy tick through the kernel wrapper")
            km._GC_FREEZE_ERRORS[0] = 0; km._GC_FREEZE_SAID[0] = False
            km._GC_FREEZE = DoubleController(raise_tick=True)
            km._gc_freeze_tick(True, False)   # must not raise
            self.assertEqual(km._GC_FREEZE_ERRORS[0], 1, "a raising tick is counted through the kernel wrapper")
            self.assertTrue(km._GC_FREEZE_SAID[0], "and said once")
        finally:
            km._GC_FREEZE = saved_gf
            km.em.record_cache_stats = saved_stats
            km._GC_FREEZE_ERRORS[0] = saved_errs; km._GC_FREEZE_SAID[0] = saved_said

    def test_the_kernel_wrapper_writes_one_line_per_release_naming_the_session_none_per_load(self):
        """PR 1999 review (attribution 6): a release means a session ended with a surviving cycle and a full-heap pause;
        the kernel wrapper writes one stderr line per release naming the resolved sid(s) off last_release_sids, and none
        on a load tick."""
        import contextlib, io
        km = load_source("romp_kernel_gcf_rel", os.path.join(BIN, "romp-kernel"))
        saved_gf, saved_stats = km._GC_FREEZE, km.em.record_cache_stats

        class _RelController:
            def __init__(self, kind, survivors=0, kept=None):
                self.enabled = True; self._kind = kind
                self.last_release_sids = ["deadbeef"]; self.last_ms = 1.2
                self.last_release_survivors = survivors; self.last_kept_sids = kept or []
            def tick(self, inserts):
                return self._kind
        def _line(controller):
            km._GC_FREEZE = controller
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km._gc_freeze_tick(True, False)
            return err.getvalue()
        try:
            km.em.record_cache_stats = lambda: {"inserts": 3}
            # a real reclaim (nothing survived): the line reads "reclaimed" and names the owed sid
            out = _line(_RelController("release", survivors=0))
            self.assertEqual(out.count("\n"), 1, "exactly one line per release: %r" % out)
            self.assertIn("deadbeef", out, "the release line names the ended session: %r" % out)
            self.assertIn("reclaimed", out)
            # PR 2042 review: the LIVE-ROOT case (a survivor) freed nothing: the line names the KEPT sids as kept by a live
            # root, never "reclaimed" (the base wrote "reclaimed a surviving cycle" for this case too)
            out2 = _line(_RelController("release", survivors=1, kept=["cafef00d"]))
            self.assertEqual(out2.count("\n"), 1, "exactly one line for the kept case: %r" % out2)
            self.assertIn("cafef00d", out2, "the kept case names the kept sid: %r" % out2)
            self.assertIn("live root", out2, "the kept case says kept by a live root: %r" % out2)
            self.assertNotIn("reclaimed", out2, "the kept case freed nothing: never 'reclaimed': %r" % out2)
            # a load tick writes no release line
            self.assertEqual(_line(_RelController("load")), "", "a load tick writes no release line")
        finally:
            km._GC_FREEZE = saved_gf
            km.em.record_cache_stats = saved_stats


class Cyclic:
    """A weakref-able node so a reference cycle can be watched by a weakref oracle."""
    __slots__ = ("other", "__weakref__")


class RealCollector(unittest.TestCase):
    def tearDown(self):
        gc.unfreeze()                                # never leak a freeze to another test
        gc.collect()

    def test_acyclic_dies_by_refcount_a_cycle_reclaims_only_when_the_ended_tick_runs(self):
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        acyclic = Cyclic(); acyclic.other = None
        wa = weakref.ref(acyclic)
        c.tick(inserts=1)                            # initial freeze folds `acyclic` in
        del acyclic
        self.assertIsNone(wa(), "an acyclic frozen object dies by refcount when its owner drops it, freeze or no freeze")
        a = Cyclic(); b = Cyclic(); a.other = b; b.other = a
        wcyc = weakref.ref(a)
        c.tick(inserts=2)                            # a load fold-in that freezes the cycle
        fin = _FakeThread(alive=False)   # a local, so the weakref resolves and is_alive() False is what decides (review PR 2042 low)
        c.note_ended(a, thread=fin)   # the cycle's owner ended, its thread finished
        del a, b
        c.tick(inserts=2)                            # the tick observes the cyclic ended ref and reclaims
        self.assertIsNone(wcyc(), "the frozen cycle is reclaimed once the ended tick unfreezes and collects")

    def test_a_live_root_ref_is_a_survivor_counted_once_then_dropped(self):
        """PR 1999 review (behaviour 4): a ref judged a surviving cycle (alive, worker finished) that a live ROOT, not a
        cycle, keeps is not freed by the reclaim; it is counted a survivor on /perf (a wasted pause) and dropped, never
        re-registered, so a strong root costs exactly ONE reclaim. A genuine frozen cycle is freed: no survivor."""
        gc.disable(); self.addCleanup(gc.enable)
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1)                            # initial freeze
        root = _Owner(); root.sid = "abcdef12-3456-7890-abcd-ef1234567890"   # a synthetic sid so the release names it (review PR 2042 low)
        fin = _FakeThread(alive=False)   # a local, so the weakref resolves and is_alive() False is what decides (review PR 2042 low)
        c.note_ended(root, thread=fin)
        self.assertEqual(c.tick(inserts=1), "release", "a live ref with a finished worker is judged a surviving cycle: a release runs")
        # the REAL resolve_ended gathered the owed sid onto last_release_sids (its first 8 chars): deleting the gather reds this
        self.assertEqual(c.last_release_sids, ["abcdef12"], "the release names the owed session by its sid's first 8 chars: %r" % c.last_release_sids)
        # PR 2042 review (attribution): the /perf VALUE carries it, and the live-root case reads lastReleaseSurvivors 1
        self.assertEqual(c.perf()["lastReleaseSids"], ["abcdef12"], "/perf carries the owed sid value: %r" % c.perf()["lastReleaseSids"])
        self.assertEqual(c.perf()["lastReleaseSurvivors"], 1, "the live-root release freed nothing: one survivor on /perf")
        self.assertEqual(c.survivors, 1, "the live root kept it through the reclaim: one survivor counted")
        self.assertIsNone(c.tick(inserts=1), "the survivor was dropped, never re-registered: no second reclaim owed")
        self.assertEqual(c.perf()["lastReleaseSids"], [], "the next tick owed nothing, so lastReleaseSids is cleared: %r" % c.perf()["lastReleaseSids"])
        self.assertEqual(c.survivors, 1, "and not double-counted")
        before = c.survivors
        a = Cyclic(); b = Cyclic(); a.other = b; b.other = a
        wcyc = weakref.ref(a)
        c.tick(inserts=2)                            # fold the cycle in (freeze it)
        fin = _FakeThread(alive=False)   # a local, so the weakref resolves and is_alive() False is what decides (review PR 2042 low)
        c.note_ended(a, thread=fin); del a, b
        c.tick(inserts=2)                            # a real reclaim
        self.assertIsNone(wcyc(), "the genuine cycle is freed")
        self.assertEqual(c.survivors, before, "a freed cycle adds no survivor")

    def test_a_release_the_cheap_collect_takes_whole_counts_as_a_load(self):
        """PR 1999 review (behaviour 5): the release is staged. A cyclic ended session on a cycle allocated SINCE the last
        freeze (no load pass between spawn and end) is UNFROZEN, so the cheap collect with the freeze in place takes it
        whole; it counts as a LOAD pass, not a reclaim, so the backstop bound does not stretch and no full-heap pause runs."""
        gc.disable(); self.addCleanup(gc.enable)
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1)                            # initial freeze
        reclaims0, foldins0 = c.reclaims, c._foldins
        a = _Owner(); b = _Owner(); a.other = b; b.other = a   # allocated AFTER the freeze: unfrozen (an _Owner so it can carry a sid)
        a.sid = "beadfeed-1234-5678-9abc-def012345678"
        wcyc = weakref.ref(a)
        fin = _FakeThread(alive=False)   # a local; here is_alive() False decides (the ref is live and the thread finished)
        c.note_ended(a, thread=fin); del a, b
        self.assertEqual(c.tick(inserts=1), "load", "the cheap collect took the released cycle whole: counted as a load pass")
        self.assertEqual(c.reclaims, reclaims0, "no full-heap reclaim ran: the backstop bound is not stretched")
        self.assertGreater(c._foldins, foldins0, "the cheap release folded in like a load")
        self.assertIsNone(wcyc(), "the cheap collect freed the unfrozen cycle")
        self.assertEqual(c.survivors, 0, "nothing survived: no wasted pause")
        # review PR 2049: a cheap release judged `load` STILL shows the owed sid (resolve_ended gathers it before _run decides
        # the kind), so the documented clause has a pin here; a gather conditioned on a real reclaim would red this
        self.assertEqual(c.last_release_sids, ["beadfeed"], "the load-judged cheap release still named the owed sid: %r" % c.last_release_sids)
        self.assertEqual(c.perf()["lastReleaseSids"], ["beadfeed"], "and the /perf value carries it: %r" % c.perf()["lastReleaseSids"])

    def test_a_frozen_member_of_the_released_cycle_forces_the_full_walk(self):
        """PR 2042 review (item 1), the condition pair: the cheap stage takes the cycle only when EVERY member postdates the
        last freeze. A released cycle with a FROZEN member (one folded in by an earlier load pass) is not taken by the cheap
        collect: the release must unfreeze and walk the frozen heap (a reclaim). A young cycle that merely KEEPS an unrelated
        object alive is still all-young, so it is the load case above."""
        gc.disable(); self.addCleanup(gc.enable)
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1)                            # initial freeze
        old = Cyclic(); old.other = None             # allocated after the initial freeze; fold it in so it is FROZEN:
        c.tick(inserts=2)                            # a load fold-in freezes `old`
        self.assertGreater(gc.get_freeze_count(), 0, "old is frozen")
        reclaims0 = c.reclaims
        young = Cyclic(); young.other = old; old.other = young   # the released cycle young<->old: `old` is a FROZEN member
        wy = weakref.ref(young)
        fin = _FakeThread(alive=False)
        c.note_ended(young, thread=fin); del young, old
        self.assertEqual(c.tick(inserts=2), "release", "a frozen member holds the cycle out of the cheap walk: the release unfreezes and reclaims")
        self.assertEqual(c.reclaims, reclaims0 + 1, "a full-heap reclaim ran")
        self.assertIsNone(wy(), "the unfreeze-and-collect freed the cycle with the frozen member")
        self.assertEqual(c.survivors, 0, "the cycle was garbage: no live-root survivor")

    def test_a_young_cycle_merely_keeping_a_frozen_object_is_the_load_case(self):
        """PR 2042 review (item 1 pair, the other half): the cheap stage's condition is on the cycle's MEMBERS, not on what
        the cycle can reach. A young end cycle whose member merely holds a strong ref to an object FROZEN by an earlier load
        fold-in is still all-young, so the cheap collect takes it: kind load, no unfreeze, and the frozen object untouched. A
        condition widened to anything the cycle reaches would force an unfreeze here."""
        gc.disable(); self.addCleanup(gc.enable)
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1)                            # initial freeze
        held = _Owner(); held.other = None           # created after the initial freeze; fold it in so it is FROZEN, and keep it in a local
        c.tick(inserts=2)
        wfrozen = weakref.ref(held)
        reclaims0 = c.reclaims
        a = _Owner(); b = _Owner(); a.other = b; b.other = a   # a YOUNG cycle
        a.kept = held                                # a merely KEEPS a ref to the frozen object (held is NOT a member of the cycle)
        wy = weakref.ref(a)
        fin = _FakeThread(alive=False)
        c.note_ended(a, thread=fin); del a, b
        self.assertEqual(c.tick(inserts=2), "load", "the cycle is all-young; the cheap collect takes it whole: a load, not a release")
        self.assertEqual(c.reclaims, reclaims0, "no full-heap reclaim ran: a merely-kept frozen object does not force the unfreeze")
        self.assertIsNone(wy(), "the young cycle was collected cheaply")
        self.assertIsNotNone(wfrozen(), "the frozen object it merely kept is untouched (still held, still frozen)")

    def test_collections_counts_each_run_steps_collect_calls_by_kind(self):
        """PR 2042 review (item 1): the /perf `collections` count bumps by exactly the collect calls each run step issues, on
        the REAL collector: initial one, a load one, a cheap release one, a full release TWO, a backstop one. A mutant
        dropping the second collect's count in the unfreeze branch reds the full-release case."""
        gc.disable(); self.addCleanup(gc.enable)
        c = gf.GcFreeze(enabled=True, load_trees=1, backstop_foldins=1, gc=gc)
        b0 = c.collections; self.assertEqual(c.tick(inserts=1), "initial"); self.assertEqual(c.collections - b0, 1, "initial: one collect")
        b0 = c.collections; self.assertEqual(c.tick(inserts=2), "load"); self.assertEqual(c.collections - b0, 1, "a load fold-in: one collect")
        # a cheap release (an unfrozen cycle): one collect
        x = Cyclic(); y = Cyclic(); x.other = y; y.other = x; fx = _FakeThread(alive=False)
        c.note_ended(x, thread=fx); del x, y
        b0 = c.collections; self.assertEqual(c.tick(inserts=2), "load", "cheap release counts as a load"); self.assertEqual(c.collections - b0, 1, "a cheap release: one collect")
        # a full release (a frozen member): two collects
        old = _Owner(); old.other = None; c.tick(inserts=3)   # freeze old
        yg = _Owner(); yg.other = old; old.other = yg; fy = _FakeThread(alive=False)
        c.note_ended(yg, thread=fy); del yg, old
        b0 = c.collections; self.assertEqual(c.tick(inserts=3), "release"); self.assertEqual(c.collections - b0, 2, "a full release: two collects (collect, then unfreeze-collect)")
        # a backstop (backstop_foldins=1): one collect
        b0 = c.collections; self.assertEqual(c.tick(inserts=4), "load")   # one fold-in to reach the backstop
        b0 = c.collections; self.assertEqual(c.tick(inserts=4), "backstop"); self.assertEqual(c.collections - b0, 1, "a backstop: one collect")

    def test_the_organic_arithmetic_is_exact_via_the_collections_count(self):
        """PR 2042 review (item 1): the exact equality the reference states. A gc.callbacks hook counts generation-2
        collections; every collect the controller ran is one, so (gen-2 collections) minus (the controller's `collections`)
        equals the organic full collections the test itself ran, exactly."""
        gc.disable(); self.addCleanup(gc.enable)
        gen2 = [0]
        def hook(phase, info):
            if phase == "stop" and info.get("generation") == 2:
                gen2[0] += 1
        gc.callbacks.append(hook); self.addCleanup(lambda: gc.callbacks.remove(hook))
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1); c.tick(inserts=2)          # two controller collects (initial + load)
        gc.collect(); gc.collect()                    # two ORGANIC full collections the test causes
        c.tick(inserts=3)                             # one more controller collect (a load)
        self.assertEqual(gen2[0] - c.collections, 2, "gen-2 collections less the controller's collections is the organic count the test ran: %d - %d" % (gen2[0], c.collections))

    def test_the_kernels_gc_hook_still_counts_the_collections_a_reconcile_runs(self):
        km = load_source("romp_kernel_gcf_hook", os.path.join(BIN, "romp-kernel"))
        st = km._PerfStats()
        st.install_gc_hook()
        try:
            before = st.gc[2][0]
            c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
            c.tick(inserts=1)                        # a freeze runs a collection
            self.assertGreater(st.gc[2][0], before, "the kernel's gc hook counted the full collection the freeze ran")
        finally:
            st.remove_gc_hook()

    @staticmethod
    def _make_cycles(n):
        out = []
        for _ in range(n):
            a = Cyclic(); b = Cyclic(); a.other = b; b.other = a
            out.append(a)
        return out                                   # a function scope so no loop variable leaks a reference

    def test_a_busy_cycle_fold_in_freezes_the_new_load_on_the_real_collector(self):
        """2026-09-28, on the real collector: a busy cycle's load fold-in moves what was loaded since the last freeze out of
        the collector's walk (the frozen count rises by at least the new load), with no unfreeze of the frozen heap."""
        errs = []
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        t = lambda ins: gf.pusher_tick(c, idle=False, first=False, stats_fn=lambda: {"inserts": ins}, on_error=errs.append)
        self.assertEqual(t(1), "initial", "a busy cycle makes the initial freeze")
        loaded = self._make_cycles(5000)             # a new load after the freeze, held (10,000 tracked objects)
        before = gc.get_freeze_count()
        self.assertEqual(t(2), "load", "a busy cycle folds the new load in")
        self.assertGreaterEqual(gc.get_freeze_count() - before, 10000, "the new load left the collector's walk")
        self.assertEqual((c.reclaims, errs), (0, []), "no frozen-heap walk and no error")
        del loaded

    def test_a_warm_collection_after_the_freeze_leaves_the_frozen_cold_cycles_alone(self):
        heap = self._make_cycles(20000)
        watched = [weakref.ref(x) for x in heap[:50]]   # a comprehension: its `x` does not leak into this scope
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(inserts=1)                            # freeze the cold cycles out of the walk
        del heap
        self._make_cycles(200)                       # a little new (unheld) garbage for the warm collection to walk
        gc.collect()
        self.assertTrue(all(w() is not None for w in watched),
                        "a warm full collection after the freeze does not walk the frozen cold cycles: they survive though nothing holds them")
        self.assertGreater(gc.get_freeze_count(), 20000, "the frozen count shows the cold cycles left the collector's walk")


@unittest.skipUnless(_KEEPS_THRESHOLD2, "the raised trigger applies only where the collector keeps a third threshold it "
                     "is given, on a build that is not free-threaded (asked of this collector by a set and a read-back)")
class RealTrigger(unittest.TestCase):
    """The premise on the REAL collector, through apply_gen2_threshold itself (2026-10-03). The setup freezes the heap and
    then runs one more full collection (one the controller never runs), so what survived the last full collection is
    small and CPython's quarter test is held open: this pins that the collector honors threshold2, the state after an
    automatic full collection over a frozen heap, not how often the quarter test holds a collection back after a
    fold-in. Builds of short-lived containers (alive while the build runs, then dropped, as a pusher build's temporaries
    are) then drive a full collection every eleventh generation-one collection at the stock trigger, and the raised
    trigger runs at least four times fewer. A scaled-down copy of the design's probe, on every interpreter whose collector
    keeps the value (the gate asks the collector, as apply_gen2_threshold does). The thresholds are restored and the heap
    unfrozen whatever happens."""

    def setUp(self):
        self.assertTrue(gc.isenabled(), "the automatic collector is what this measures")
        saved = gc.get_threshold()
        self.addCleanup(gc.set_threshold, *saved)

    def tearDown(self):
        gc.unfreeze()                                # never leak a freeze to another test
        gc.collect()

    @staticmethod
    def _full_collections(builds=20, size=50000):
        n = [0]
        def hook(phase, info):
            if phase == "stop" and info.get("generation") == 2:
                n[0] += 1
        gc.collect(); gc.freeze(); gc.collect()     # the heap frozen, and what survived the last full collection small
        gc.callbacks.append(hook)
        try:
            for _ in range(builds):
                build = [[i] for i in range(size)]   # a build's temporaries: tracked containers, alive while it runs
                del build
        finally:
            gc.callbacks.remove(hook)
            gc.unfreeze()
        return n[0]

    def test_a_raised_trigger_cuts_automatic_full_collections(self):
        gc.set_threshold(700, 10, 10)                # 3.12's stock thresholds, set the same on every interpreter of the matrix
        stock = self._full_collections()
        c = gf.GcFreeze(enabled=True, gc=gc)
        self.assertEqual(gf.apply_gen2_threshold(c, value=100), 100, "applied on this interpreter, whose collector keeps it")
        self.assertEqual(gc.get_threshold(), (700, 10, 100), "the real collector's third threshold raised, the first two kept")
        raised = self._full_collections()
        self.assertGreaterEqual(stock, 6, "the stock trigger ran full collections over the builds (the premise): %d" % stock)
        self.assertLessEqual(raised * 4, stock, "the raised trigger ran at least four times fewer: stock %d, raised %d" % (stock, raised))
        c.enabled = False
        self.assertIsNone(gf.apply_gen2_threshold(c))
        self.assertEqual(gc.get_threshold(), (700, 10, 10), "turned off, the stock trigger is back on the real collector")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _uline(sid, t, text, uid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uid, "parentUuid": parent, "sessionId": sid,
            "cwd": "/home/TESTHOST/proj", "message": {"role": "user", "content": text}}


def _aline(sid, t, text, uid, parent):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uid, "parentUuid": parent, "sessionId": sid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class RecordCacheIsAStat(unittest.TestCase):
    """The record cache's `released` counter is a /perf STATISTIC, not a reclaim trigger: its pops release acyclic
    json, freed by refcount. `released` moves on a re-read replacement and an OSError pop, but re-reads (which grow
    inserts and released) drive only cheap load fold-ins, never a reclaim; and a freeze forces no reread."""
    def tearDown(self):
        gc.unfreeze()
        gc.collect()

    def _write(self, d, sid, rows):
        p = os.path.join(d, sid + ".jsonl")
        Path(p).write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    def test_released_moves_on_a_pop_and_no_reread_across_a_freeze(self):
        em = load_source("romp_event_model_rel", os.path.join(BIN, "romp-event-model"))
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-cccccccc0001"
        rows = [_uline(sid, 1_700_000_000, "ask", "u1"), _aline(sid, 1_700_000_030, "answer", "a1", "u1"),
                _uline(sid, 1_700_000_600, "again", "u2", "a1"), _aline(sid, 1_700_000_630, "ok", "a2", "u2")]
        p = self._write(d, sid, rows)
        em.parse_session(p, rompuuid=sid)
        s1 = em.record_cache_stats()
        rel0 = s1["released"]
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(int(s1["inserts"]))                   # an initial freeze
        kinds = []
        for k in range(6):                           # six re-reads: append and re-parse, each REPLACES the cache entry
            rows.append(_uline(sid, 1_700_001_000 + k * 100, "more %d" % k, "u%d" % (10 + k), "a2"))
            Path(p).write_text("".join(json.dumps(r) + "\n" for r in rows))
            em.parse_session(p, rompuuid=sid)
            kinds.append(gf.pusher_tick(c, idle=True, first=False, stats_fn=em.record_cache_stats, on_error=lambda e: None))
        s2 = em.record_cache_stats()
        self.assertGreater(s2["released"], rel0, "each re-read replacement popped the old entry: `released` moved (a /perf stat)")
        self.assertNotIn("release", kinds, "a moving `released` under re-reads drives NO reclaim: %r" % kinds)
        self.assertNotIn("backstop", kinds, "and no backstop fired over six re-reads: %r" % kinds)
        self.assertEqual(c.reclaims, 0, "zero reclaims across the run of re-reads (the design's near-zero): %r" % c.reclaims)

    def test_freezing_forces_no_whole_transcript_reread(self):
        # a freeze must not make the reader re-read a file it already cached: re-parsing the UNCHANGED file after a
        # freeze moves neither `inserts` nor `wholeReads` (restored from the shipped NoRereadChurn, ported to tick)
        em = load_source("romp_event_model_reread", os.path.join(BIN, "romp-event-model"))
        d = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-cccccccc0002"
        rows = [_uline(sid, 1_700_000_000, "ask", "u1"), _aline(sid, 1_700_000_030, "answer", "a1", "u1"),
                _uline(sid, 1_700_000_600, "again", "u2", "a1"), _aline(sid, 1_700_000_630, "ok", "a2", "u2")]
        p = self._write(d, sid, rows)
        em.parse_session(p, rompuuid=sid)
        s1 = em.record_cache_stats()
        c = gf.GcFreeze(enabled=True, load_trees=1, gc=gc)
        c.tick(int(s1["inserts"]))                   # freeze the loaded records
        em.parse_session(p, rompuuid=sid)            # re-parse the unchanged file
        s2 = em.record_cache_stats()
        self.assertEqual(s2["inserts"], s1["inserts"], "no new insert: the freeze did not evict the cached records")
        self.assertEqual(s2.get("wholeReads"), s1.get("wholeReads"), "no new whole read: the freeze forced no reread")

    def test_the_load_fold_in_fires_off_the_real_insert_counter(self):
        # ten real parses grow the record cache's insert counter past the threshold; the tick folds them in and the
        # frozen count rises (restored from the shipped NoRereadChurn, ported to tick)
        em = load_source("romp_event_model_loadctr", os.path.join(BIN, "romp-event-model"))
        d = tempfile.mkdtemp()
        c = gf.GcFreeze(enabled=True, load_trees=8, gc=gc)

        def parse(i):
            sid = "11111111-2222-3333-4444-bbbbbbbb%04d" % i
            rows = [_uline(sid, 1_700_000_000, "ask", "u1"), _aline(sid, 1_700_000_030, "answer", "a1", "u1"),
                    _uline(sid, 1_700_000_600, "again", "u2", "a1"), _aline(sid, 1_700_000_630, "ok", "a2", "u2")]
            em.parse_session(self._write(d, sid, rows), rompuuid=sid)
            return int(em.record_cache_stats()["inserts"])
        parse(0)
        self.assertEqual(c.tick(int(em.record_cache_stats()["inserts"])), "initial", "the first parse makes the initial freeze")
        for i in range(1, 11):                       # ten more real parses grow the real insert counter past the threshold
            inserts = parse(i)
        frozen_before = gc.get_freeze_count()
        self.assertEqual(c.tick(inserts), "load", "the tick reads the REAL insert counter grown past the threshold and folds the load in")
        self.assertGreater(gc.get_freeze_count(), frozen_before, "the fold-in froze the newly loaded objects out of the collector's walk")


class KernelKnob(unittest.TestCase):
    """The kernel-side knob fallbacks, executed: a bad ROMP_GC_FREEZE_LOAD_TREES falls back to the default at import,
    counts an error and says one line; a 0 is floored to 1 with no error. A bad ROMP_GC_GEN2_THRESHOLD falls back at
    import too, and is said and counted by main()'s wrapper, only where it applies that default."""
    def _load_with(self, value, name, var="ROMP_GC_FREEZE_LOAD_TREES", extra=None):
        """Load the kernel with `var` set to `value` and `extra` beside it. The two knobs this class tests are cleared
        unless passed (review 2026-10-03): a value exported in the shell for one must never reach the other's case."""
        import contextlib, io
        env = {"ROMP_GC_FREEZE_LOAD_TREES": None, "ROMP_GC_GEN2_THRESHOLD": None}
        env.update(extra or {})
        env[var] = value
        saved = {k: os.environ.get(k) for k in env}
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        err = io.StringIO()
        try:
            with contextlib.redirect_stderr(err):
                km = load_source(name, os.path.join(BIN, "romp-kernel"))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return km, err.getvalue()

    def _apply(self, km, collector, enabled=True, free_threaded=False):
        """Run main()'s wrapper, km._gc_gen2_apply(), on a controller over `collector`, with the free-threaded gate read as
        `free_threaded` (passed through apply_gen2_threshold's own injection seam, so the build running the test never
        decides). Returns what the wrapper returned and what it said on stderr."""
        import contextlib, functools, io
        from unittest import mock
        km._GC_FREEZE = km.gcf.GcFreeze(enabled=enabled, gc=collector, clock=lambda: 0.0)
        gate = functools.partial(km.gcf.apply_gen2_threshold, free_threaded=free_threaded)
        err = io.StringIO()
        with mock.patch.object(km.gcf, "apply_gen2_threshold", gate), contextlib.redirect_stderr(err):
            got = km._gc_gen2_apply()
        return got, err.getvalue()

    def test_a_bad_gen2_knob_is_said_and_counted_only_once_its_fallback_is_applied(self):
        """2026-10-03: a bad ROMP_GC_GEN2_THRESHOLD (abc, or one past the largest C int) falls back to the default, never
        fatal. The import never touches the collector, even with the freeze ON in its environment: the trigger is applied
        by main() alone, so a test that loads the kernel keeps its interpreter's thresholds. Review round two: the import
        says and counts nothing either; on a collector that keeps the value, main()'s wrapper applies the default and only
        then says it once and counts it once."""
        before = gc.get_threshold()
        self.addCleanup(gc.set_threshold, *before)   # a regression that applied at import must not leak into later tests
        for raw, name in (("abc", "romp_kernel_knob_gen2_abc"), ("2147483648", "romp_kernel_knob_gen2_ceiling")):
            with self.subTest(knob=raw):
                km, err = self._load_with(raw, name, var="ROMP_GC_GEN2_THRESHOLD", extra={"ROMP_GC_FREEZE": "on"})
                self.assertEqual(gc.get_threshold(), before, "loading the kernel with the freeze on changed no threshold")
                self.assertTrue(km._GC_FREEZE.enabled, "the freeze was on for this load (else the line above proves nothing)")
                self.assertIsNone(km._GC_FREEZE.perf()["gen2Threshold"], "and its controller applied nothing at import")
                self.assertEqual(km._GC_GEN2_THRESHOLD, km.gcf.DEFAULT_GEN2_THRESHOLD, "%s falls back to the default" % raw)
                self.assertEqual(km._GC_FREEZE_ERRORS[0], 0, "the import counts nothing: the report waits for the apply")
                self.assertNotIn("ROMP_GC_GEN2_THRESHOLD", err, "and the import says nothing: %r" % err)
                fake = ThresholdGc((2000, 10, 10))
                got, said = self._apply(km, fake)
                self.assertEqual(got, 100, "the default applied, on a collector that keeps it")
                self.assertEqual(fake.thresholds, (2000, 10, 100), fake.calls)
                self.assertEqual(km._GC_FREEZE_ERRORS[0], 1, "the fallback in force is counted once under errors")
                lines = said.splitlines()
                self.assertEqual(len(lines), 1, "said once: %r" % said)
                self.assertIn("ROMP_GC_GEN2_THRESHOLD=%r" % raw, lines[0])
                self.assertIn("using the default 100", lines[0])

    def test_a_bad_gen2_knob_where_the_trigger_is_declined_is_neither_said_nor_counted(self):
        """Review 2026-10-03, round two: with the freeze on, a bad knob where apply_gen2_threshold applies no trigger (a
        collector that reads the third threshold back as 0, or a free-threaded build) names a default nothing uses, and /perf
        would show the stock thresholds and a null gen2Threshold beside an error. Nothing is counted and nothing is said."""
        for raw in ("abc", "2147483648"):
            for label, collector, free_threaded in (("reads 0", ZeroThirdGc((2000, 10, 0)), False),
                                                    ("free-threaded", ThresholdGc((2000, 10, 10)), True)):
                with self.subTest(knob=raw, declined=label):
                    km, err = self._load_with(raw, "romp_kernel_knob_gen2_declined", var="ROMP_GC_GEN2_THRESHOLD",
                                              extra={"ROMP_GC_FREEZE": "on"})
                    self.assertTrue(km._GC_FREEZE.enabled, "the freeze was on for this load")
                    got, said = self._apply(km, collector, free_threaded=free_threaded)
                    self.assertIsNone(got, "declined: no trigger applied")
                    self.assertIsNone(km._GC_FREEZE.perf()["gen2Threshold"], "and none recorded")
                    self.assertEqual(km._GC_FREEZE_ERRORS[0], 0, "no error counted for a default nothing uses")
                    self.assertNotIn("ROMP_GC_GEN2_THRESHOLD", err + said, "and nothing said: %r" % (err + said))

    def test_a_gen2_knob_of_250_is_parsed_not_the_default(self):
        """Review 2026-10-03: the import keeps the PARSED knob (a substitution of the default for the parse left every other
        case green); main() applies it, which tests/test_perf_gc_block.py pins through the kernel's wrapper. Applied, a
        valid knob is neither said nor counted."""
        km, err = self._load_with("250", "romp_kernel_knob_gen2_250", var="ROMP_GC_GEN2_THRESHOLD",
                                  extra={"ROMP_GC_FREEZE": "on"})
        self.assertEqual(km._GC_GEN2_THRESHOLD, 250, "the knob's value, parsed at import")
        got, said = self._apply(km, ThresholdGc((2000, 10, 10)))
        self.assertEqual(got, 250, "the parsed value applied")
        self.assertEqual(km._GC_FREEZE_ERRORS[0], 0, "a valid knob is no error")
        self.assertNotIn("ROMP_GC_GEN2_THRESHOLD", err + said, "and nothing is said for it: %r" % (err + said))

    def test_a_bad_gen2_knob_with_the_freeze_off_is_neither_said_nor_counted(self):
        """Review 2026-10-03: with the freeze off main() applies no trigger, so a bad knob names a value nothing uses. It is
        neither said on stderr nor counted under gc.freeze.errors there, at the import or at main()'s wrapper."""
        km, err = self._load_with("abc", "romp_kernel_knob_gen2_off", var="ROMP_GC_GEN2_THRESHOLD",
                                  extra={"ROMP_GC_FREEZE": "off"})
        self.assertFalse(km._GC_FREEZE.enabled, "the freeze was off for this load (else the lines below prove nothing)")
        fake = ThresholdGc((2000, 10, 10))
        got, said = self._apply(km, fake, enabled=km._GC_FREEZE.enabled)
        self.assertIsNone(got, "the freeze off: no trigger applied")
        self.assertEqual(fake.calls, [], "and no collector call")
        self.assertEqual(km._GC_FREEZE_ERRORS[0], 0, "not counted with the freeze off")
        self.assertNotIn("ROMP_GC_GEN2_THRESHOLD", err + said, "and not said: %r" % (err + said))

    def test_a_bad_knob_falls_back_and_is_said_a_zero_is_floored(self):
        km, err = self._load_with("abc", "romp_kernel_knob_abc")
        self.assertEqual(km._GC_FREEZE_LOAD_TREES, km.gcf.DEFAULT_LOAD_TREES, "abc falls back to the default")
        self.assertGreaterEqual(km._GC_FREEZE_ERRORS[0], 1, "the bad knob is counted under errors")
        self.assertIn("ROMP_GC_FREEZE_LOAD_TREES", err, "and said once on stderr: %r" % err)
        km0, err0 = self._load_with("0", "romp_kernel_knob_zero")
        self.assertEqual(km0._GC_FREEZE_LOAD_TREES, 1, "0 is floored to 1")
        self.assertEqual(km0._GC_FREEZE_ERRORS[0], 0, "a floored 0 is a valid int, no error")
        self.assertEqual(err0, "", "and nothing said for a valid, floored value")


def _install_fake_sdk():
    """The verifier's fake claude_agent_sdk: a client that stalls after the init frame, so a real SdkBackend
    connects and runs a session thread without the real CLI. Installed in sys.modules before sdk_backend loads."""
    import asyncio
    import importlib.machinery as _mach
    import types as _types

    class _StandIn(_types.ModuleType):
        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            cls = type(name, (), {"__init__": lambda self, *a, **k: self.__dict__.update(k)})
            setattr(self, name, cls)
            return cls

    class SystemMessage:
        def __init__(self, subtype, data=None):
            self.subtype = subtype
            self.data = data or {}

    class FakeClient:
        def __init__(self, options=None, transport=None):
            self.options = options
            self._q = asyncio.Queue()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def query(self, prompt, session_id="default"):
            async for turn in prompt:
                await self._q.put(turn)
        async def interrupt(self):
            pass
        async def get_context_usage(self):
            return {"percentage": 2, "model": "claude-x"}
        async def get_server_info(self):
            return {}
        async def receive_messages(self):
            await self._q.get()
            yield SystemMessage("init", {"session_id": getattr(self.options, "session_id", None) or "fsid",
                                         "model": "claude-x", "permissionMode": "acceptEdits"})
            while True:
                await asyncio.sleep(3600)

    m = _StandIn("claude_agent_sdk")
    m.__spec__ = _mach.ModuleSpec("claude_agent_sdk", None)
    m.__file__ = "<test stand-in>"
    m.ClaudeAgentOptions = type("ClaudeAgentOptions", (), {"__init__": lambda self, **kw: self.__dict__.update(kw)})
    m.ClaudeSDKClient = FakeClient
    m.SystemMessage = SystemMessage
    m.AssistantMessage = type("AssistantMessage", (), {"__init__": lambda self, content=None, model=None: self.__dict__.update(content=content or [], model=model)})
    m.ResultMessage = type("ResultMessage", (), {"__init__": lambda self, **k: self.__dict__.update(k)})
    m.TextBlock = type("TextBlock", (), {"__init__": lambda self, text="": self.__dict__.update(text=text)})
    had = "claude_agent_sdk" in sys.modules
    prior = sys.modules.get("claude_agent_sdk")
    sys.modules["claude_agent_sdk"] = m
    def restore():                                   # never leave the stand-in in sys.modules: on a box where the real package
        if had:                                      # imports (the kernel's sdkvenv on sys.path), it would shadow it for every later module
            sys.modules["claude_agent_sdk"] = prior
        else:
            sys.modules.pop("claude_agent_sdk", None)
    return restore


class FakeSdkRestore(unittest.TestCase):
    """PR 1999 round-four low: _install_fake_sdk's restore must put back whatever `claude_agent_sdk` was in sys.modules (a
    real package on a box with the sdkvenv on the path), or leave it absent when there was none. Seed a marker and check it
    comes back; then check the absent case pops the stand-in."""
    def test_the_restore_puts_back_the_prior_module_or_leaves_it_absent(self):
        import types as _types
        saved = sys.modules.get("claude_agent_sdk")
        self.addCleanup(lambda: sys.modules.__setitem__("claude_agent_sdk", saved) if saved is not None else sys.modules.pop("claude_agent_sdk", None))
        marker = _types.ModuleType("claude_agent_sdk"); marker.MARKER = object()
        sys.modules["claude_agent_sdk"] = marker
        restore = _install_fake_sdk()
        self.assertIsNot(sys.modules.get("claude_agent_sdk"), marker, "the stand-in replaced the prior module while installed")
        restore()
        self.assertIs(sys.modules.get("claude_agent_sdk"), marker, "the restore put the prior module back")
        # the absent case: no prior module, so the restore pops the stand-in
        sys.modules.pop("claude_agent_sdk", None)
        restore2 = _install_fake_sdk()
        self.assertIn("claude_agent_sdk", sys.modules, "the stand-in is installed")
        restore2()
        self.assertNotIn("claude_agent_sdk", sys.modules, "the restore pops the stand-in when there was no prior module")


class SessionEndPopsRegister(unittest.TestCase):
    """Executed: a real SdkBackend driven to each of its three session-end pops (run-to-exit, kill, conserve-close)
    through the fake SDK registers the ended session with the controller. A spy sees one registration per pop, with
    the session and its worker thread; the run-to-exit registers though its `client` is already None, so re-adding
    a `client is not None` guard at a pop (the round-one defect) would drop it and redden here."""
    @classmethod
    def setUpClass(cls):
        cls.addClassCleanup(_install_fake_sdk())     # install the stand-in and register its restore (pop it, or put back any real package)
        cls.addClassCleanup(lambda: sys.modules.pop("romp_sdk_backend_pops", None))
        cls.sb = load_source("romp_sdk_backend_pops", os.path.join(ROOT, "kernel", "sdk_backend.py"))

    def _backend(self, tag):
        d = tempfile.mkdtemp(prefix="gcf-pops-" + tag + "-")
        open(os.path.join(d, "session-hosts"), "w").write("off")
        return self.sb.SdkBackend(d, "/bin/true", lambda *a, **k: None), d

    def _wait(self, pred, timeout=15.0):
        end = time.time() + timeout
        while time.time() < end:
            if pred():
                return True
            time.sleep(0.02)
        return False

    def _connected(self, tag, name):
        be, d = self._backend(tag)
        sid = be.spawn(name, d)
        self.assertTrue(be.connect(sid), "connect refused")
        self.assertTrue(self._wait(lambda: be.sessions.get(sid) and be.sessions[sid].client is not None), "never connected")
        return be, d, sid

    def test_each_of_the_three_session_end_pops_registers_the_ended_session(self):
        """Executed teeth (review PR 1999): a REAL GcFreeze over the fake collector is the ended note (never a strong list,
        which would leak every ended session and pass a note that leaks). Each of the three pops registers EXACTLY once with
        the session and its worker thread (read off c._ended's weakrefs), ahead of the drop; and the run-to-exit end, the
        common case, DIES BY REFERENCE COUNTING once its strong refs are dropped and its worker joined, so the judgement owes
        no reclaim. Under gc.disable() so a young-generation collect cannot take the ref before the read."""
        c = gf.GcFreeze(enabled=True, gc=FakeGc(), clock=lambda: 0.0)
        self.sb.set_ended_note(c.note_ended)
        self.addCleanup(lambda: self.sb.set_ended_note(None))
        gc.disable(); self.addCleanup(gc.enable)
        # run to exit through _on_session_gone: shutdown ends the loop, its own thread pops with client cleared
        be, d, sid = self._connected("exit", "web")
        s = be.sessions[sid]; th = s.thread
        n = len(c._ended)
        s.shutdown()
        self.assertTrue(self._wait(lambda: not th.is_alive()), "the session thread never finished")
        self.assertTrue(self._wait(lambda: len(c._ended) == n + 1), "_on_session_gone registered EXACTLY one ended session: %d" % (len(c._ended) - n))
        sref, tref = c._ended[-1]
        self.assertIs(sref(), s, "the session is registered (read off the weakref while still held)")
        self.assertIs(tref(), th, "with its worker thread")
        self.assertIsNone(s.client, "run-to-exit clears client, yet the pop still registered (a client guard here would drop it)")
        # the common end dies by REFERENCE COUNTING: join the worker (clears its _target), drop every strong local, then the
        # weakref is dead and the judgement owes no reclaim (never a wasted full pause for the ordinary exit)
        th.join(10)
        del s, th
        owed = c.resolve_ended()
        self.assertIsNone(sref(), "the run-to-exit session died by reference counting once its refs dropped: acyclic")
        self.assertEqual(owed, [], "the common end owes NO reclaim (it was not a surviving cycle): %r" % owed)
        # kill (mid-turn). PR 2042 review item 7: WAIT for the worker (the gone hook registers from its finally), THEN
        # assert the count directly, so a broken `if popped:` guard that lets the gone hook register a SECOND time (count
        # n+2) reds; the earlier poll-until-n+1 passed as soon as it hit n+1, before the worker's gone hook could add one.
        be2, d2, sid2 = self._connected("kill", "api")
        s2 = be2.sessions[sid2]; th2 = s2.thread
        n = len(c._ended)
        be2.kill(sid2)
        self.assertTrue(self._wait(lambda: not th2.is_alive()), "the kill worker never finished")
        self.assertTrue(self._wait(lambda: len(c._ended) > n), "kill registered the ended session")
        self.assertEqual(len(c._ended), n + 1, "kill registered EXACTLY once, even after the worker's gone hook ran: %d" % (len(c._ended) - n))
        sref2, tref2 = c._ended[-1]
        self.assertIs(sref2(), s2, "kill registered the session"); self.assertIs(tref2(), th2, "with its worker thread")
        # item 8 mirror: the killed session dies by refcount and owes no reclaim (a pop that stashed it would leak it, unseen)
        del s2, th2
        owed2 = c.resolve_ended()
        self.assertIsNone(sref2(), "the killed session died by reference counting: acyclic, not stashed")
        self.assertEqual(owed2, [], "the killed session owes no reclaim: %r" % owed2)
        # conserve-close (the stop pop): same wait-then-assert and dead-ref mirror
        be3, d3, sid3 = self._connected("close", "tests")
        self.assertTrue(self._wait(lambda: be3.conserve_idle(sid3), 8.0), "never conserve-idle")
        s3 = be3.sessions[sid3]; th3 = s3.thread
        n = len(c._ended)
        be3.conserve_close(sid3)
        self.assertTrue(self._wait(lambda: not th3.is_alive()), "the conserve-close worker never finished")
        self.assertTrue(self._wait(lambda: len(c._ended) > n), "conserve_close registered the ended session")
        self.assertEqual(len(c._ended), n + 1, "conserve_close registered EXACTLY once, even after the worker's gone hook ran: %d" % (len(c._ended) - n))
        sref3, tref3 = c._ended[-1]
        self.assertIs(sref3(), s3, "conserve_close registered the session"); self.assertIs(tref3(), th3, "with its worker thread")
        del s3, th3
        owed3 = c.resolve_ended()
        self.assertIsNone(sref3(), "the conserve-closed session died by reference counting: acyclic, not stashed")
        self.assertEqual(owed3, [], "the conserve-closed session owes no reclaim: %r" % owed3)


class KernelGlue(unittest.TestCase):
    """The kernel wires the ended note at backend load and calls the tick at the end of every pusher cycle."""
    def test_the_pusher_cycle_calls_the_tick_and_the_kernel_wires_the_ended_note(self):
        import inspect
        km = load_source("romp_kernel_gcf_glue", os.path.join(BIN, "romp-kernel"))
        self.assertRegex(inspect.getsource(km._pusher_cycle), r"_gc_freeze_tick\(_cycle_idle, first\)",
                         "the pusher cycle calls the freeze tick with the cycle's idle flag and first-cycle flag")
        # the regex reds a guard-name typo; the SDK-load function itself is executed in
        # test_the_kernel_sdk_load_wires_the_ended_note_executed, which reds the three unreachable-call mutants the text misses.
        self.assertRegex(inspect.getsource(km), r'hasattr\(sbmod, "set_ended_note"\)[\s\S]{0,240}?sbmod\.set_ended_note\(_GC_FREEZE\.note_ended\)',
                         "the kernel guards and wires the ended note on the same literal name (executed by the glue test below)")
        sbmod = load_source("romp_sdk_backend_wire", os.path.join(ROOT, "kernel", "sdk_backend.py"))
        self.assertTrue(hasattr(sbmod, "set_ended_note"), "the real backend exposes the exact name the kernel's hasattr guards on")
        self.addCleanup(lambda: sbmod.set_ended_note(None))
        sbmod.set_ended_note(km._GC_FREEZE.note_ended)
        self.assertEqual(sbmod._ENDED_NOTE[0], km._GC_FREEZE.note_ended,
                         "the backend's own setter wires the note (==, a bound method mints anew per read); the kernel's call of it is executed below")

    def test_the_kernel_sdk_load_wires_the_ended_note_executed(self):
        """PR 2042 review (item 2): the ended-note wiring is pinned by EXECUTING the kernel's SDK-load function, not only the
        regex plus a call of the backend's own setter. Stub the kernel copy's load_source to a namespace whose backend is a
        plain recorder (not a mock, which answers hasattr True for a misspelt guard name), whose startup_auth_env returns {}
        (kernel.py reads it unguarded, so assert no problems) and whose set_ended_note records; save and restore the kernel
        names and jd wires as _sdk_locked_for_real does; run _sdk_locked; the recorded note equals the controller's
        note_ended. Three mutants that keep the source text but make the call unreachable red this."""
        import io, types
        from contextlib import redirect_stderr
        km = load_source("romp_kernel_gcf_glue_exec", os.path.join(BIN, "romp-kernel"))
        noted = []
        class _Recorder:
            def __init__(self, *a, **k):
                self.args = (a, k)
        fake = types.SimpleNamespace(SdkBackend=_Recorder, startup_auth_env=lambda *a, **k: {},
                                     set_ended_note=lambda fn: noted.append(fn))
        names = ("_sdk_backend", "load_source", "_sdk_import_notice", "_ensure_sdk_on_path", "_model_catalog_boot",
                 "_claude_bin", "_mark_boot", "_sdk_problem")
        saved = {n: getattr(km, n) for n in names}
        wires = ("_LOGIN_AUTH_ENV_FN", "_USAGE_REFRESH_FN", "_DEFAULT_AUTH_FN", "_DEFAULT_LOGIN_FN", "_API_HEALTH_NOTE_FN")
        saved_jd = {w: getattr(km.jd, w) for w in wires}
        problems = []
        try:
            km._sdk_backend = None
            km.load_source = lambda name, path: fake
            km._sdk_import_notice = lambda: True
            km._ensure_sdk_on_path = lambda: True
            km._model_catalog_boot = lambda _async=True: False
            km._claude_bin = lambda: "/bin/true"
            km._mark_boot = lambda *a, **k: None
            km._sdk_problem = problems.append
            err = io.StringIO()
            with redirect_stderr(err):
                km._sdk_locked()
        finally:
            for n in names:
                setattr(km, n, saved[n])
            for w in wires:
                setattr(km.jd, w, saved_jd[w])
        self.assertEqual(noted, [km._GC_FREEZE.note_ended], "the executed SDK-load wired the controller's note_ended (by equality): %r" % noted)
        self.assertEqual(problems, [], "the auth-env hook returned an empty problems list")

    def test_every_session_end_pop_is_paired_with_an_ended_note(self):
        """A source census beside the driven test: every `self.sessions.pop(` site in the SDK backend (a session end, in
        `kill`, `conserve_close` and the `_on_session_gone` hook) is followed within a few lines by a `_note_ended(` CALL,
        so a FUTURE fourth pop that forgets the note reds here. The driven test (SessionEndPopsRegister) proves the three
        that exist fire; this guards the ones not yet written. Comments are stripped and the `def _note_ended` line skipped,
        so a comment naming the call or the definition itself never pairs a pop (review PR 1999 tests). LIMIT (PR 1999
        round-four low): the census matches the single-line literal `self.sessions.pop(`; a pop split across physical lines
        (an argument on the next line) would not be found. The three pops today are each one line, and a new pop kept to one
        line is caught; a future multi-line pop would need this literal widened."""
        raw = Path(ROOT, "kernel", "sdk_backend.py").read_text().splitlines()
        code = [ln.split("#", 1)[0] for ln in raw]      # strip line comments so a comment naming the call does not pair a pop
        code = ["" if ln.lstrip().startswith("def _note_ended") else ln for ln in code]   # the def line is not a call
        pops = [i for i, ln in enumerate(code) if "self.sessions.pop(" in ln]
        self.assertEqual(len(pops), 3, "the SDK backend has exactly the three session-end pops (kill, conserve_close, _on_session_gone): %d" % len(pops))
        for i in pops:
            window = "\n".join(code[i:i + 5])           # the pop's line and the next four, comments stripped
            self.assertIn("_note_ended(", window,
                          "the session-end pop at line %d (a kill, conserve_close or _on_session_gone) is not paired with a "
                          "_note_ended call within four lines: %r" % (i + 1, "\n".join(raw[i:i + 5])))


if __name__ == "__main__":
    unittest.main()
