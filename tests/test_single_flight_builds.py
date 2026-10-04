#!/usr/bin/env python3
"""Single-flight builds (2026-09-14). At the first browser-attached boot on the cards-first code (12:06 PM PT) every pane's
socket redialed after the outage and each connect push built its own copy of the same cold work on its handler thread while
the pusher built it too: five to seven cold builders of one feed, one timeline and the same chat tabs on one interpreter,
each five to seven times slower for it (push.feedFirst 106 s, connect pushes 109 to 130 s, cards at 131 s). Now a feed or
timeline build in flight on one thread is the build every later caller serves (one lock each), the connect path's live-only
timeline runs outside any lock (a pane's first frame is never behind another pane's build), and a chat tab in flight on another
thread is waited for and re-read from the cache, the claim released by the thread's scope close whatever raised. Hermetic: the
builders are stubbed to sleep and count; two threads race each call."""
import contextlib
import gc
import inspect
import io
import json
import os
import tempfile
import threading
import time
import unittest
import weakref
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_single_flight", os.path.join(BIN, "romp-kernel"))

S1 = "11111111-2222-3333-4444-777777777771"
S2 = "11111111-2222-3333-4444-777777777772"
NAMES = {S1: "web", S2: "api"}
FEED = {"type": "feed", "asks": [], "working": [], "awaiting": []}


def _cold_timeline_state_clear():
    """The cold timeline's state (2026-10-03) back to a cold kernel's: no partial, no cold lanes, nothing owed, no pusher wake.
    Read through getattr, so the module still loads and runs on a tree without the builder (the red runs)."""
    for name in ("_tl_partial", "_tl_cold_lanes"):
        slot = getattr(km, name, None)
        if slot is not None:
            slot[0] = None
    owed = getattr(km, "_TL_OWED", None)
    if owed is not None:
        owed.clear()
    km._pusher_wake.clear()


def _race(fn, n=2):
    """Run `fn` on n threads at once; return their results in start order."""
    out = [None] * n
    go = threading.Event()
    def run(i):
        go.wait(); out[i] = fn()
    ths = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in ths: t.start()
    go.set()
    for t in ths: t.join(20)
    return out


class _Gate:
    """A pane's slot lock (its `dlock`) that says when a second thread waits on it, once armed."""

    def __init__(self):
        self.lk, self.armed, self.waiting = threading.RLock(), False, threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self.lk.acquire(False):
            return True
        if not blocking:
            return False
        if self.armed:
            self.waiting.set()
        return self.lk.acquire(True, timeout)

    def release(self):
        self.lk.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()
        return False


class _HoldAtLock:
    """A pane's slot lock (its `dlock`) that, once `name` is set, holds the thread of that name at its next take of the lock
    until `go` is set: the moment a push has built or been served its cold lanes and has not sent them yet."""

    def __init__(self):
        self.lk, self.name, self.held, self.go = threading.RLock(), None, threading.Event(), threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if self.name is not None and threading.current_thread().name == self.name:
            self.name = None
            self.held.set()
            self.go.wait(10)
        return self.lk.acquire(blocking, timeout)

    def release(self):
        self.lk.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *a):
        self.release()
        return False


class _HookAtRead(list):
    """A one-slot module list (_tl_cold_lanes) whose next read of slot 0 on the thread named `thread` runs `hook` first and
    then returns what the slot holds after it (`before=False`), or what it held before it (`before=True`): a moment between
    a read and what the reader does next, on one thread, with other threads left alone."""

    def __init__(self):
        super().__init__([None])
        self.thread, self.hook, self.before = None, None, False

    def __getitem__(self, i):
        if i == 0 and self.thread is not None and threading.current_thread().ident == self.thread:
            self.thread = None
            held = list.__getitem__(self, 0)
            self.hook()
            return held if self.before else list.__getitem__(self, 0)
        return list.__getitem__(self, i)


class FeedAndTimelineSingleFlight(unittest.TestCase):
    def setUp(self):
        self.saved = (km.build_feed, km.build_timeline, list(km._built_feed), list(km._built_timeline), km._views_dirty[0],
                      dict(km._VIEW_STATS))
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        _cold_timeline_state_clear()
        self.builds, self.spans = [], []
        def feed(now, live_map):
            self.builds.append("feed"); time.sleep(0.3); return json.loads(json.dumps(FEED))
        def timeline(now, live_map, with_bars=True, live_only=False):
            self.builds.append("tl:%s" % ("live" if live_only else "full")); t0 = time.monotonic(); time.sleep(0.3)
            self.spans.append((t0, time.monotonic()))
            return {"lanes": [], "turns": {}, "judging": {}, "messages": [], "now": now}
        km.build_feed, km.build_timeline = feed, timeline
        km._PERF_STATS.reset()

    def tearDown(self):
        (km.build_feed, km.build_timeline, bf, bt, km._views_dirty[0], vs) = self.saved
        km._built_feed[:] = bf; km._built_timeline[:] = bt
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._PERF_STATS.reset()
        _cold_timeline_state_clear()

    def test_two_cold_feed_callers_build_once_and_both_get_the_frame(self):
        now = int(time.time())
        with mock.patch.object(km, "_task_tracking_on", lambda: True):
            got = _race(lambda: km._cached_feed(now, {}, ("sig",), False))
        self.assertEqual(self.builds, ["feed"], "one build for two racing callers: %r" % self.builds)
        self.assertTrue(all(g and g.get("type") == "feed" for g in got), got)
        self.assertEqual(km._VIEW_STATS["feedWaited"], 1, "the second caller waited for the first's build and served it")
        b = km._PERF_STATS.snapshot()["builds"]["feed"]
        self.assertEqual((b["cached"], b["built"]), (1, 1))

    def test_a_connect_caller_racing_the_pushers_cold_feed_build_serves_it(self):
        now = int(time.time())
        with mock.patch.object(km, "_task_tracking_on", lambda: True):
            got = _race(lambda: km._cached_feed(now, {}, ("sig",), False), n=1) + \
                  _race(lambda: km._cached_feed(now, {}, ("other",), True), n=1)   # sequential here; the race is below
        self.assertEqual(self.builds, ["feed"], "a connect never rebuilds a warmed feed: %r" % self.builds)
        del self.builds[:]
        km._built_feed[:] = [None, None, 0.0, 0.0]
        with mock.patch.object(km, "_task_tracking_on", lambda: True):
            fns = [lambda: km._cached_feed(now, {}, ("sig",), False), lambda: km._cached_feed(now, {}, ("other",), True)]
            out = [None, None]; go = threading.Event()
            def run(i):
                go.wait(); out[i] = fns[i]()
            ths = [threading.Thread(target=run, args=(i,)) for i in range(2)]
            for t in ths: t.start()
            go.set()
            for t in ths: t.join(20)
        self.assertEqual(self.builds, ["feed"], "the cold connect waited for the pusher's build (or the pusher for the connect's): %r" % self.builds)
        self.assertTrue(all(o and o.get("type") == "feed" for o in out))

    def test_two_callers_on_a_stale_timeline_slot_build_once(self):
        # The lock's single-flight guards the warm road since 2026-10-03: a COLD slot is filled by the timeline builder
        # alone (ColdTimelineBuilder below), so two pushers' callers meet on the lock only over a filled slot gone stale.
        now = int(time.time())
        km._built_timeline[:] = [("older",), {"lanes": [], "turns": {}, "judging": {}, "messages": [], "now": 1}, 1.0, 1.0]
        got = _race(lambda: km._cached_timeline(now, {}, ("sig",), False))
        self.assertEqual(self.builds, ["tl:full"], self.builds)
        self.assertTrue(all(g and "lanes" in g and g["now"] == now for g in got), "both serve the one rebuild: %r" % got)
        self.assertEqual(km._VIEW_STATS["tlWaited"], 1)

    def test_a_dirty_mark_after_the_build_still_rebuilds_for_the_pusher(self):
        now = int(time.time())
        with mock.patch.object(km, "_task_tracking_on", lambda: True):
            km._cached_feed(now, {}, ("sig",), False)
            km._views_dirty[0] = time.time() + 1          # a kernel-side mutation after the build's start
            km._cached_feed(now, {}, ("sig",), False)
        self.assertEqual(self.builds, ["feed", "feed"], "the lock is single-flight, not a cache: a dirty pusher call rebuilds")

    def test_the_locks_are_reentrant_and_named(self):
        self.assertTrue(km._FEED_BUILD_LOCK.acquire(blocking=False)); self.assertTrue(km._FEED_BUILD_LOCK.acquire(blocking=False))
        km._FEED_BUILD_LOCK.release(); km._FEED_BUILD_LOCK.release()
        src = inspect.getsource(km._cached_feed)
        self.assertIn("with _FEED_BUILD_LOCK:", src)
        self.assertIn("with _TL_BUILD_LOCK:", inspect.getsource(km._cached_timeline), "the pusher's stale rebuild")
        self.assertIn("with _TL_BUILD_LOCK:", inspect.getsource(km._timeline_build_cold), "the builder's full build into the cold slot")
        push = inspect.getsource(km._push)
        self.assertNotIn("with _TL_BUILD_LOCK:", push, "the connect path's lanes skeleton runs outside any lock (round two, medium 2)")
        self.assertIn("_chat_inflight_release_all()", inspect.getsource(km._chat_push_scopes_close),
                      "the thread's scope close releases the claims a raise left (round two, medium 1)")

    def test_two_cold_connect_timeline_pushes_run_side_by_side(self):
        """Round two, medium 2: the first cut held the full build's lock across the connect path's live-only builds, so the second
        pane's first frame waited for the first pane's, and a connect that won the lock blocked the pusher's timeline stage. Since
        2026-10-03 a cold connect sends the cold lanes alone and owes the bars to the timeline builder, so each connect makes
        one live-only build (the skeleton) where it made two (the skeleton and live bars of its own). With one build per connect,
        two connects run one after the other put the second first frame inside the old timing bound, so the check is that the
        two skeleton builds overlapped: neither connect waited on the other or on a lock (review find, 2026-10-03)."""
        now = int(time.time())
        frames = {0: [], 1: []}
        def client(i):
            return {"app": "timeline", "alive": True, "sent": {}, "send": lambda s, i=i: frames[i].append(time.monotonic())}
        saved = (km._live_map, km._cached_feed, km._chat_tab_sessions, list(km._clients))
        km._live_map = lambda: {}; km._cached_feed = lambda *a, **k: None; km._chat_tab_sessions = lambda now, live_map: []
        owed0 = km._VIEW_STATS.get("tlOwed", 0)
        try:
            cs = [client(0), client(1)]
            km._clients[:] = cs
            t0 = time.monotonic()
            _race(lambda: None, n=1)
            out = [None, None]; go = threading.Event()
            def run(i):
                go.wait(); km._push([cs[i]], connect=True)
            ths = [threading.Thread(target=run, args=(i,)) for i in range(2)]
            for th in ths: th.start()
            go.set()
            for th in ths: th.join(20)
        finally:
            km._live_map, km._cached_feed, km._chat_tab_sessions, clients = saved
            del km._clients[:]; km._clients.extend(clients)
        firsts = [min(frames[i]) - t0 for i in range(2)]
        self.assertLess(max(firsts), 0.3 + 0.35, "both first frames within one live-only build (0.3 s), not one behind the other: %r" % firsts)
        self.assertEqual(self.builds, ["tl:live", "tl:live"], "one skeleton per connect and no bars build on either: %r" % self.builds)
        self.assertEqual(len(self.spans), 2, "both connects built the cold lanes, neither waited for the other's: %r" % self.spans)
        self.assertLess(max(s for s, _ in self.spans), min(e for _, e in self.spans),
                        "the two connects' skeleton builds overlapped: %r" % self.spans)
        self.assertTrue(km._TL_OWED.is_set(), "the cold bars are owed to the timeline builder")
        self.assertEqual(km._VIEW_STATS["tlOwed"] - owed0, 2, "each connect owed it once")


class ColdTimelineBuilder(unittest.TestCase):
    """The cold timeline build, off the pusher (2026-10-03). After a restart the pusher built the whole cold timeline inside its
    cycle, so no later cycle could begin until that build finished, while a reloaded Sessions pane's connect push built live
    bars of its own on the handler thread and stored them nowhere. A cold slot is now filled by one builder thread
    (_timeline_builder): phase one the live-only partial (its bars served warming), phase two the full build; the pusher and
    the connect pushes send the cold lanes (one memo, never the partial's lanes) and owe the build. Hermetic: build_timeline
    is stubbed to record "skel", "live" or "full" and to sleep 0.5 s for a bars build; the builder's pass runs synchronously
    on the test thread, and the three tests that run the real loop stop it through the T282 seam, so no thread outlives a
    test. The partials the tests plant are phase one's older world (web and api both live); the stub's skeleton is the
    world now (web alone live), so lanes projected from a partial are told apart from the cold lanes. Round two: the
    builder sends the partial's bars itself to the panes in km._clients (empty at setUp, set per test, restored in
    tearDown), and a push that meets the full build mid-way sends no cold frame after it; `on_skel` runs inside a skeleton
    build, the moment a push has read the slot and the partial and has not sent yet. Round three: with a board pane (app
    "feed") in km._clients and no feed frame sent to one yet, phase two waits for that frame; with none it runs at once, as
    every earlier test has it. The builder sends no frame of the full build: the pusher's next push sends it (2026-10-04).
    The PR's first review round (2026-10-04) adds two cold-lanes sends that overlap, a connect's clock, a raising send inside
    the builder's pass, the backoff after a failed pass, and the two guards only a direct call reaches."""

    LANES = [{"id": S1, "name": NAMES[S1]}, {"id": S2, "name": NAMES[S2]}]   # S1 is the live session, S2 dead within the window

    def setUp(self):
        self.saved = {nm: getattr(km, nm) for nm in ("build_timeline", "_live_map", "_cached_feed", "_chat_tab_sessions",
                                                      "_fleet_view_sig", "_skel_wire", "_bars_wire", "_feed_wire")}
        self.saved_state = (list(km._built_timeline), km._views_dirty[0], dict(km._VIEW_STATS), list(km._clients))
        km._clients[:] = []                               # no board pane: phase two never waits unless a test connects one
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._skel_wire = km._bars_wire = None
        _cold_timeline_state_clear()
        said = getattr(km, "_TL_BUILD_FAILED_SAID", None)
        if said is not None:
            said.clear()
        self.builds, self.on_bars, self.on_skel, self.sig, self.stamp = [], None, None, [("sig",)], False
        km.build_timeline = self._build
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None
        km._chat_tab_sessions = lambda now, live_map: []
        km._fleet_view_sig = lambda now, live_map: self.sig[0]
        km._PERF_STATS.reset()

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        bt, vd, vs, clients = self.saved_state
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._clients[:] = clients
        _cold_timeline_state_clear()
        km._PERF_STATS.reset()

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        kind = "skel" if not with_bars else ("live" if live_only else "full")
        self.builds.append(kind)
        if not with_bars and self.on_skel is not None:
            self.on_skel()
        if with_bars:
            if self.on_bars is not None:
                self.on_bars(kind, live_map)
            time.sleep(0.5)
        lanes = self.LANES[:1] if live_only else self.LANES
        turns = {l["id"]: [{"id": "%s-%s" % (kind, l["id"][-1]), "t": now - 60, "t1": now - 30}] for l in lanes} if with_bars else {}
        sessions = [dict(l) for l in lanes]
        if self.stamp and not with_bars:        # an open interval ends at its build's clock: the build's ordinal stands in
            for s in sessions:
                s["states"] = [[0, len(self.builds), 1]]
        return {"type": "timeline", "sessions": sessions, "turns": turns, "judging": [], "messages": [], "now": now}

    def _partial(self):
        """A partial as phase one would have built it a while ago: web and api both live then, each with a bar."""
        return getattr(km, "_TimelinePartial", dict)(
            {"type": "timeline", "sessions": [dict(l, state="working") for l in self.LANES],
             "turns": {l["id"]: [{"id": "b-" + l["id"][-1], "t": 1, "t1": 2}] for l in self.LANES},
             "judging": [], "messages": [], "now": int(time.time()) - 30})

    @staticmethod
    def _plant(partial):
        if hasattr(km, "_tl_partial"):
            km._tl_partial[0] = partial

    @staticmethod
    def _pane():
        frames = []
        return {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}, frames

    @staticmethod
    def _owed():
        return getattr(km, "_TL_OWED", threading.Event()).is_set()

    @staticmethod
    def _wait(cond, what, timeout=10.0):
        deadline = time.monotonic() + timeout
        while not cond():
            if time.monotonic() > deadline:
                raise AssertionError("timed out waiting for " + what)
            time.sleep(0.01)

    def _stop(self, th):
        """The T282 seam: the stop set, the owe and the board wake set to wake the waits, the join; the stop cleared only once
        the loop ended."""
        km._LOOPS_STOP.set()
        km._TL_OWED.set()
        board_wake = getattr(km, "_TL_BOARD_WAKE", None)
        if board_wake is not None:
            board_wake.set()
        th.join(5)
        if not th.is_alive():
            km._LOOPS_STOP.clear()
        return th.is_alive()

    def test_a_cold_pusher_push_sends_the_lanes_and_owes_the_full_build(self):
        pane, frames = self._pane()
        owed0 = km._VIEW_STATS.get("tlOwed", 0)
        t0 = time.monotonic()
        km._push([pane])
        dt = time.monotonic() - t0
        self.assertLess(dt, 0.4, "the push waited on no bars build (each takes 0.5 s here): %.2f s" % dt)
        self.assertEqual(self.builds, ["skel"], "the lanes skeleton alone on the pusher's thread: %r" % self.builds)
        self.assertEqual([f["type"] for f in frames], ["data"], "the lanes went, and no bars")
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1], "the live lanes alone (LIVE-FIRST)")
        self.assertTrue(self._owed(), "the full build is owed to the builder")
        self.assertEqual(km._VIEW_STATS.get("tlOwed", 0) - owed0, 1, "the owe is counted")

    def test_a_cold_connect_owes_the_build_and_builds_no_bars_of_its_own(self):
        pane, frames = self._pane()
        km._push([pane], connect=True)
        self.assertEqual(self.builds, ["skel"], "the connect built its lanes and no live bars of its own: %r" % self.builds)
        self.assertEqual([f["type"] for f in frames], ["data"], "the lanes went, and no bars until the builder lands some")
        self.assertTrue(self._owed(), "the cold build is owed to the builder")

    def test_the_builder_lands_live_bars_first_then_the_full_build_and_wakes_the_pusher(self):
        seen = {}

        def on_bars(kind, live_map):
            if kind == "full":
                seen["partial"] = km._tl_partial[0]
                seen["woken"] = km._pusher_wake.is_set()
                km._pusher_wake.clear()                   # so the wake after the pass is the full build's own
        self.on_bars = on_bars
        km._timeline_builder_pass()
        self.assertEqual(self.builds, ["live", "full"], "the live bars first, then the full build")
        self.assertIsInstance(seen["partial"], km._TimelinePartial, "the partial stood while the full build ran")
        self.assertEqual(list(seen["partial"]["turns"]), [S1], "...and it was the live-only build")
        self.assertTrue(seen["woken"], "the pusher was woken to send the partial before the full build began")
        full = km._built_timeline[1]
        self.assertIsNotNone(full, "the slot is filled")
        self.assertNotIsInstance(full, km._TimelinePartial)
        self.assertEqual(sorted(full["turns"]), sorted([S1, S2]), "...with the full build, the dead lane included")
        self.assertIsNone(km._tl_partial[0], "the partial is dropped once the full build lands")
        self.assertTrue(km._pusher_wake.is_set(), "and the pusher woken to send the full build")
        self.assertEqual(km._PERF_STATS.snapshot()["builds"]["timeline"]["built"], 1, "one full build counted")

    def test_a_push_between_the_partial_and_the_full_serves_the_partial_warming(self):
        # the bars are the partial's, warming; the lanes are the cold lanes and never the partial's, which are as old as phase
        # one's start (review find 2026-10-03: projected over the lanes a pane held, they moved the lanes back)
        partial = self._partial()
        self._plant(partial)
        pane, frames = self._pane()
        km._push([pane])
        self.assertEqual(self.builds, ["skel"], "the cold lanes on the pusher's thread, and no bars build: %r" % self.builds)
        self.assertEqual([f["type"] for f in frames], ["data", "bars"])
        self.assertEqual(frames[0]["data"]["sessions"], [dict(self.LANES[0])], "the cold lanes (the world now), not the partial's")
        self.assertEqual(frames[0]["data"]["turns"], {}, "...as the lanes frame, with no bars in it")
        self.assertIs(frames[1]["warming"], True, "the partial's bars go warming: the pane keeps its loader if they are empty")
        self.assertEqual(frames[1]["turns"], partial["turns"], "...and they are the partial's bars")

    def test_lanes_a_pane_holds_stand_when_an_older_partial_lands(self):
        # the cold lanes of the world now go first; then phase one's partial, built from an older world, lands
        pane, frames = self._pane()
        km._push([pane])
        partial = self._partial()
        self._plant(partial)
        km._push([pane])
        self.assertEqual([f["type"] for f in frames], ["data", "bars"],
                         "no second lanes frame: the partial's older lanes never go over the ones the pane holds")
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1])
        self.assertIs(frames[1]["warming"], True)
        self.assertEqual(frames[1]["turns"], partial["turns"], "only the partial's bars went")
        self.assertEqual(self.builds, ["skel"], "the one cold lanes build, served again from its memo")

    def test_a_reload_during_phase_two_then_the_pushers_push_sends_no_second_lanes_frame(self):
        partial = self._partial()
        self._plant(partial)
        pane, frames = self._pane()
        km._push([pane], connect=True)                    # a Sessions pane reloaded while the full build runs
        km._push([pane])                                  # the pusher's next push
        self.assertEqual([f["type"] for f in frames], ["data", "bars"],
                         "the pusher's push after the connect sends nothing new: no lanes frame from the partial")
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1], "the connect sent the cold lanes")

    def test_a_cold_connect_during_phase_two_serves_the_partial_warming(self):
        partial = self._partial()
        self._plant(partial)
        pane, frames = self._pane()
        km._push([pane], connect=True)
        self.assertEqual(self.builds, ["skel"], "the connect's cold lanes and no bars build of its own: %r" % self.builds)
        self.assertEqual([f["type"] for f in frames], ["data", "bars"])
        self.assertEqual(frames[0]["data"]["sessions"], [dict(self.LANES[0])], "the cold lanes, not the partial's")
        self.assertIs(frames[1]["warming"], True, "a reloaded pane gets the partial's bars as warming, so an empty pane keeps its loader")
        self.assertEqual(frames[1]["turns"], partial["turns"], "...and they are the partial's bars")
        self.assertTrue(self._owed(), "the full build is still owed")

    def test_a_cold_connect_takes_the_lanes_the_pusher_sends(self):
        # an open interval ends at the build's clock, so two skeleton builds differ; a connect that built its own fresher
        # lanes was followed by the pusher's older memo frame on its next push, sent again with no new build behind it
        self.stamp = True
        a, fa = self._pane()
        km._push([a])                                     # the pusher's cold lanes
        b, fb = self._pane()
        km._push([b], connect=True)                       # a reload: the same lanes, under the cycle's clock
        km._push([a, b])                                  # the pusher's next push
        self.assertEqual(self.builds, ["skel"], "one lanes build for the pusher and the connect: %r" % self.builds)
        self.assertEqual([f["type"] for f in fa], ["data"])
        self.assertEqual([f["type"] for f in fb], ["data"], "no older lanes frame went after the connect's")
        self.assertEqual(fb[0]["data"]["sessions"], fa[0]["data"]["sessions"], "the connect's lanes were the pusher's")

    def test_a_full_build_landing_under_the_ask_is_served_not_answered_with_nothing(self):
        # The builder fills the slot and THEN drops the partial; an ask whose first read saw the slot empty and whose read of
        # the partial came after the drop would answer None, and the push would send no bars for a cycle and build the cold
        # lanes again (the full build dropped them). The ask reads the slot again and serves the build.
        full = {"type": "timeline", "sessions": [dict(l) for l in self.LANES], "turns": {}, "judging": [], "messages": [], "now": 1}

        class LandsUnderTheRead(list):
            """_built_timeline whose payload slot reads empty once (the ask's first read), then filled: the builder's
            `_built_timeline[:] = [...]` and its partial drop landing between the ask's reads."""
            def __init__(self, entry):
                super().__init__(entry)
                self.reads = 0

            def __getitem__(self, i):
                if i == 1:
                    self.reads += 1
                    if self.reads == 1:
                        return None
                return list.__getitem__(self, i)
        real = km._built_timeline
        owed0 = km._VIEW_STATS.get("tlOwed", 0)
        km._built_timeline = LandsUnderTheRead([("sig",), full, time.time(), time.time()])
        try:
            got = km._cached_timeline(int(time.time()), {}, ("sig",), False)
        finally:
            km._built_timeline = real                     # before tearDown, which restores the real slot's entry
        self.assertIs(got, full, "the full build that landed under the ask is served, not None")
        self.assertEqual(self.builds, [], "and nothing is built for it")
        self.assertFalse(self._owed(), "a slot found filled owes nothing")
        self.assertEqual(km._VIEW_STATS.get("tlOwed", 0), owed0)

    def test_the_builder_never_rebuilds_a_filled_slot(self):
        held = {"type": "timeline", "sessions": [], "turns": {}, "judging": [], "messages": [], "now": 1}
        for entry in ([("sig",), held, time.time(), time.time()],      # fresh
                      [("older",), held, 1.0, 1.0]):                   # stale: the pusher's to rebuild
            km._built_timeline[:] = entry
            km._timeline_builder_pass()
            self.assertEqual(self.builds, [], "a filled slot, fresh or stale, is the pusher's: %r" % (entry[0],))
            self.assertIs(km._built_timeline[1], held)

    def test_a_stale_slot_is_still_rebuilt_on_the_pushers_thread(self):
        # green before the change too: it guards the warm road, which stays on the pusher (with the test above, a stale arm
        # that owed instead would serve a stale build for good)
        held = {"type": "timeline", "sessions": [], "turns": {}, "judging": [], "messages": [], "now": 1}
        km._built_timeline[:] = [("older",), held, 1.0, 1.0]
        owed0 = km._VIEW_STATS.get("tlOwed", 0)
        tl = km._cached_timeline(int(time.time()), {}, ("sig",), False)
        self.assertEqual(self.builds, ["full"], "the stale slot is rebuilt on the calling thread, as before")
        self.assertIs(km._built_timeline[1], tl)
        self.assertFalse(self._owed(), "a filled slot owes nothing")
        self.assertEqual(km._VIEW_STATS.get("tlOwed", 0), owed0)

    def test_the_builder_opens_the_cycle_scopes_around_its_builds(self):
        live, reads, seen = {S1: {"state": "working"}}, [], []
        km._live_map = lambda: reads.append(1) or live

        def on_bars(kind, live_map):
            sc = km._live_scope
            seen.append((kind, live_map is live, getattr(sc, "snapshot", None) is live, getattr(sc, "names", None) is not None,
                         isinstance(getattr(sc, "paths", None), dict), isinstance(getattr(sc, "subagent_trees", None), dict)))
        self.on_bars = on_bars
        km._timeline_builder_pass()
        self.assertEqual(seen, [("live",) + (True,) * 5, ("full",) + (True,) * 5],
                         "each phase builds over the snapshot it took, with the names and the cycle memos open: %r" % seen)
        self.assertEqual(len(reads), 2, "one liveness read per phase")
        self.assertIsNone(getattr(km._live_scope, "snapshot", None), "the scopes are closed after the pass")
        self.assertIsNone(getattr(km._live_scope, "names", None))

    def test_the_pushers_cold_lanes_are_rebuilt_under_the_cached_builds_rule(self):
        # the rule _timeline_cache_fresh serves a cached full build under: a moved signature rebuilds only once REBUILD_MIN_S
        # has passed since the build (the signature carries every live transcript's mtime, so it moves most cycles at boot),
        # a dirty mark at once
        pane, frames = self._pane()
        km._push([pane])
        km._push([pane])
        self.assertEqual(self.builds, ["skel"], "two cold pushes under one signature build the lanes once: %r" % self.builds)
        self.sig[0] = ("sig", "next bucket")
        km._push([pane])
        self.assertEqual(self.builds, ["skel"], "a moved signature within REBUILD_MIN_S of the build serves the memo: %r" % self.builds)
        with mock.patch.object(km, "REBUILD_MIN_S", 0.0):
            km._push([pane])
        self.assertEqual(self.builds, ["skel", "skel"], "a moved signature past the floor builds them again")
        km._mark_views_dirty()
        km._push([pane])
        self.assertEqual(self.builds, ["skel", "skel", "skel"], "and so does a dirty mark")
        self.assertEqual([f["type"] for f in frames], ["data"], "identical lanes dedup on content: one frame in all")

    def test_owes_during_a_build_coalesce(self):
        started, release, passes = threading.Event(), threading.Event(), []

        def on_bars(kind, live_map):
            if kind == "full":
                started.set()
                release.wait(10)
        self.on_bars = on_bars
        real_pass = km._timeline_builder_pass
        km._timeline_builder_pass = lambda: passes.append(1) or real_pass()
        th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
        alive = None
        try:
            th.start()
            now = int(time.time())
            self.assertIsNone(km._cached_timeline(now, {}, ("sig",), False), "cold, nothing landed: owed")
            self.assertTrue(started.wait(5), "the builder took the owe and reached its full build")
            for _ in range(5):
                km._cached_timeline(now, {}, ("sig",), False)   # five more owes while the build runs
            release.set()
            self._wait(lambda: km._built_timeline[1] is not None and not km._TL_OWED.is_set() and len(passes) >= 2,
                       "the slot filled and the coalesced pass taken")
            time.sleep(0.2)                                       # a pass per owe would show here
            self.assertEqual(self.builds, ["live", "full"], "one cold build for six owes")
            self.assertEqual(len(passes), 2, "the owes during the build coalesced into one more pass: %d" % len(passes))
            self.assertTrue(th.is_alive(), "and the loop waits for the next owe")
        finally:
            km._timeline_builder_pass = real_pass
            release.set()
            alive = self._stop(th)
        self.assertFalse(alive, "the loop ended on the stop seam")

    def test_a_failing_full_build_is_said_once_counted_and_retried_on_the_next_owe(self):
        # a failed pass drops its partial (review find 2026-10-03: a partial kept across a failure froze every pane's bars at
        # phase one's start for as long as the fault lasted), so each retry runs both phases; and only an owe retries
        fails = [2]

        def on_bars(kind, live_map):
            if kind == "full" and fails[0] > 0:
                fails[0] -= 1
                raise RuntimeError("synthetic build failure")
        self.on_bars = on_bars
        failed0 = km._VIEW_STATS.get("tlBuildFailed", 0)
        err = io.StringIO()
        th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
        now = int(time.time())
        alive = None
        try:
            with contextlib.redirect_stderr(err):
                th.start()
                km._cached_timeline(now, {}, ("sig",), False)              # the owe
                self._wait(lambda: km._VIEW_STATS.get("tlBuildFailed", 0) - failed0 >= 1, "the first failure")
                self.assertIsNone(km._built_timeline[1], "the slot stays cold")
                self._wait(lambda: km._tl_partial[0] is None, "the failed pass's partial dropped", timeout=2.0)
                time.sleep(km.PUSHER_FAIL_BACKOFF_S[0] + 0.5)
                self.assertEqual(self.builds, ["live", "full"], "no retry without an owe: %r" % self.builds)
                self.assertIsNone(km._cached_timeline(now, {}, ("sig",), False),
                                  "a cold ask after a failure serves no partial until the retry's phase one lands; it owes again")
                self._wait(lambda: km._VIEW_STATS.get("tlBuildFailed", 0) - failed0 >= 2, "the retry's failure")
                km._cached_timeline(now, {}, ("sig",), False)
                self._wait(lambda: km._built_timeline[1] is not None, "the third pass's full build")
        finally:
            alive = self._stop(th)
        self.assertFalse(alive, "the loop ended on the stop seam")
        self.assertEqual(self.builds, ["live", "full", "live", "full", "live", "full"], "each retry runs both phases")
        self.assertEqual(km._VIEW_STATS["tlBuildFailed"] - failed0, 2, "both failures counted")
        lines = [l for l in err.getvalue().splitlines() if l.startswith("timeline-build:")]
        self.assertEqual(len(lines), 1, "said once per exception kind: %r" % lines)
        self.assertIn("RuntimeError", lines[0])
        self.assertIn(", in on_bars", err.getvalue(), "the line carries the traceback, the function the build raised in "
                      "included (review find, 2026-10-04: the kind and text alone named neither function nor file)")

    def test_a_failed_pass_keeps_nothing_of_the_failed_build_alive(self):
        # the loop kept the exception between passes, and through its traceback every frame of the failed build and what
        # they held, for as long as no owe came (a closed Sessions pane: indefinitely; review find, 2026-10-03)
        refs = []

        class Held:
            pass

        def on_bars(kind, live_map):
            if kind == "full":
                held = Held()                              # an object local to the failed build
                refs.append(weakref.ref(held))
                raise RuntimeError("synthetic build failure")
        self.on_bars = on_bars
        failed0 = km._VIEW_STATS.get("tlBuildFailed", 0)
        th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
        alive = None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                th.start()
                km._cached_timeline(int(time.time()), {}, ("sig",), False)   # the owe
                self._wait(lambda: km._VIEW_STATS.get("tlBuildFailed", 0) - failed0 >= 1, "the failure")
                time.sleep(km.PUSHER_FAIL_BACKOFF_S[0] + 0.3)               # past the backoff: the loop waits for an owe
                self.assertTrue(th.is_alive(), "the loop waits for the next owe")
                gc.collect()
                self.assertEqual(len(refs), 1)
                self.assertIsNone(refs[0](), "the failed build's objects were freed while the loop waits")
        finally:
            alive = self._stop(th)
        self.assertFalse(alive, "the loop ended on the stop seam")

    # ---- round two (2026-10-03): the builder sends the partial itself, and no cold frame follows the full build ----

    def _full(self):
        """A full build as phase two would land it: both lanes, each with a bar of its own."""
        return {"type": "timeline", "sessions": [dict(l) for l in self.LANES],
                "turns": {l["id"]: [{"id": "full-" + l["id"][-1], "t": 1, "t1": 2}] for l in self.LANES},
                "judging": [], "messages": [], "now": int(time.time())}

    @staticmethod
    def _on_thread(fn, *a, **k):
        """Another thread's push (the pusher's, or a reload's connect push), run to its end."""
        th = threading.Thread(target=fn, args=a, kwargs=k, daemon=True)
        th.start()
        th.join(10)

    @staticmethod
    def _kinds(frames):
        return [(f["type"], f.get("warming")) for f in frames]

    def test_the_partial_reaches_a_pane_before_the_pushers_next_push(self):
        # the builder sends phase one's bars itself; until then they waited for the pusher's next push, which at boot came 10
        # to 22 s after the base's connect push had sent live-only bars of its own (lab, synthetic)
        pane, frames = self._pane()
        km._clients[:] = [pane]
        km._push([pane])                                  # the cold lanes: the pane holds its lanes frame
        seen = {}

        def on_bars(kind, live_map):
            if kind == "full":                            # phase two begins, and no push has run since the cold lanes
                seen["frames"] = self._kinds(frames)
                seen["turns"] = frames[-1].get("turns")
        self.on_bars = on_bars
        km._timeline_builder_pass()
        self.assertEqual(seen["frames"], [("data", None), ("bars", True)],
                         "the partial's bars reached the pane, warming, before phase two began: %r" % (seen["frames"],))
        self.assertEqual(list(seen["turns"]), [S1], "...and they were the live-only build's")
        self.assertEqual(seen["turns"][S1][0]["id"], "live-1")

    def test_the_pushers_push_after_the_builders_send_sends_nothing_again(self):
        # the builder fills the pusher's wire tuple for the partial, so the pusher's next push of the same partial serves
        # the same objects and both kinds of pane dedup: a whole-frame pane on the signature, a delta pane on the split
        whole, fw = self._pane()
        delta, fd = self._pane()
        delta["delta"] = True
        km._clients[:] = [whole, delta]
        km._push([whole, delta])
        seen = {}

        def on_bars(kind, live_map):
            if kind == "full":
                seen["sent"] = ([f["type"] for f in fw], [f["type"] for f in fd])
                w = km._bars_wire
                seen["wire"] = (w is not None and w[0] is km._tl_partial[0], w is not None and w[1] is True)
                self._on_thread(km._push, [whole, delta])  # the pusher's next push, while phase two runs
                seen["after"] = ([f["type"] for f in fw], [f["type"] for f in fd])
                seen["same"] = km._bars_wire is w
        self.on_bars = on_bars
        km._timeline_builder_pass()
        self.assertEqual(seen["sent"], (["data", "bars"], ["data", "bars"]), "the builder sent each pane the partial's bars")
        self.assertEqual(seen["wire"], (True, True), "...through the pusher's wire tuple for the partial, warming")
        self.assertEqual(seen["after"], seen["sent"], "the pusher's push sent neither pane anything: %r" % (seen["after"],))
        self.assertTrue(seen["same"], "...and served the builder's wire tuple, no second fill")

    def test_a_push_that_read_the_partial_sends_no_cold_frame_after_the_full_build(self):
        # the pusher's push reads the partial; the full build lands and a reload's connect push sends it to the pane
        # before this push's sends. Its cold lanes and the partial's bars, sent after the full build's, put the pane back
        # on the live lanes alone and on phase one's bars, with nothing new behind them, until the next push
        partial, full = self._partial(), self._full()
        self._plant(partial)
        pane, frames = self._pane()

        def lands():
            self.on_skel = None
            km._built_timeline[:] = [self.sig[0], full, time.time(), time.time()]
            km._tl_partial[0] = None                      # as the builder does: the slot filled, then the partial dropped
            self._on_thread(km._push, [pane], connect=True)
        self.on_skel = lands                              # inside the push's cold lanes build, after its read of the partial
        km._push([pane])
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)],
                         "the full build's lanes and bars, and no cold frame after them: %r" % (self._kinds(frames),))
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1, S2], "the full build's lanes, dead one too")
        self.assertEqual(frames[1]["turns"], full["turns"], "...and its bars")

    def test_a_cold_connect_sends_no_cold_lanes_after_the_full_build(self):
        # a reload's connect push reads the slot cold; the full build lands and the pusher sends it to the pane before the
        # connect's lanes go: the connect's cold lanes must not follow the full build's
        full = self._full()
        pane, frames = self._pane()

        def lands():
            self.on_skel = None
            km._built_timeline[:] = [self.sig[0], full, time.time(), time.time()]
            self._on_thread(km._push, [pane])             # the pusher's push, woken by the full build
        self.on_skel = lands                              # inside the connect's cold lanes build, after its cold read
        km._push([pane], connect=True)
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)],
                         "the full build's lanes and bars, and no cold lanes after them: %r" % (self._kinds(frames),))
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1, S2])

    def test_a_partial_landing_after_the_full_build_goes_to_no_pane(self):
        pane, frames = self._pane()
        km._clients[:] = [pane]
        km._built_timeline[:] = [self.sig[0], self._full(), time.time(), time.time()]
        km._push([pane])                                  # the pane holds the full build
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)])
        km._timeline_build_partial(int(time.time()), {})  # a phase one that lands after it
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)], "no partial's bars after the full build's")
        self.assertIsNone(km._tl_partial[0], "and the filled slot keeps the partial out")

    def test_the_builders_send_skips_a_pane_with_no_lanes_yet(self):
        # a pane connected and not yet sent its lanes (its connect push not run): the builder sends it nothing, and its
        # connect push sends the lanes and then the partial's bars, in the wire's order
        pane, frames = self._pane()
        km._clients[:] = [pane]
        seen = {}

        def on_bars(kind, live_map):
            if kind == "full":
                seen["before"] = self._kinds(frames)
                self._on_thread(km._push, [pane], connect=True)
                seen["after"] = self._kinds(frames)
        self.on_bars = on_bars
        km._timeline_builder_pass()
        self.assertEqual(seen["before"], [], "no bars to a pane that holds no lanes yet: %r" % (seen["before"],))
        self.assertEqual(seen["after"], [("data", None), ("bars", True)], "its connect push: the lanes, then the bars")

    # ---- the round-two gate's finds (2026-10-03) ----

    def test_a_cold_connect_the_full_build_overtook_still_sends_lanes_before_bars(self):
        # a reload's connect push reads the slot cold and the full build lands inside its cold lanes build, with no other push
        # in between: the guard holds the cold lanes back, so the connect sends the full build's lanes itself, then the bars
        full = self._full()
        pane, frames = self._pane()

        def lands():
            self.on_skel = None
            km._built_timeline[:] = [self.sig[0], full, time.time(), time.time()]
            km._tl_partial[0] = None
        self.on_skel = lands
        km._push([pane], connect=True)
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)], "lanes before bars: %r" % (self._kinds(frames),))
        self.assertEqual([l["id"] for l in frames[0]["data"]["sessions"]], [S1, S2], "the full build's lanes, dead one too")

    def test_an_older_cold_lanes_build_finishing_last_is_not_the_memo(self):
        # a reload's connect push and the pusher rebuild the cold lanes side by side; the connect's build began first, in an
        # older world, and ends last. Stored as the memo every push sends, it put pane a back on the older lanes with no new
        # information, for as long as the signature held
        world = ["working"]
        began, release = threading.Event(), threading.Event()

        def build(now, live_map=None, with_bars=True, live_only=False):
            state = world[0]
            if not with_bars and threading.current_thread().name == "slow-connect":
                began.set()
                release.wait(10)
            return {"type": "timeline", "sessions": [{"id": S1, "name": "web", "state": state}], "turns": {},
                    "judging": [], "messages": [], "now": now}
        km.build_timeline = build
        a, fa = self._pane()
        b, fb = self._pane()
        with mock.patch.object(km, "REBUILD_MIN_S", 0.2):
            km._push([a])                                 # the cold lanes: web working
            self.sig[0] = ("sig", 2)
            time.sleep(0.3)                               # the memo is past the floor under a moved signature
            th = threading.Thread(target=km._push, args=([b],), kwargs={"connect": True}, name="slow-connect", daemon=True)
            th.start()
            try:
                self.assertTrue(began.wait(10), "the connect's lanes build began")
                world[0] = "waiting"                      # web stops working
                km._push([a])                             # the pusher's cold lanes, rebuilt: web waiting
            finally:
                release.set()
                th.join(10)                               # the connect's older build ends last
            km._push([a])                                 # the pusher's next push, nothing moved since
        web = [f["data"]["sessions"][0]["state"] for f in fa if f["type"] == "data"]
        webb = [f["data"]["sessions"][0]["state"] for f in fb if f["type"] == "data"]
        self.assertEqual(web, ["working", "waiting"], "pane a's lane stepped back with no new information: %r" % (web,))
        self.assertEqual(webb, ["waiting"], "the connect sent the newer memo, not its own older build: %r" % (webb,))

    def test_a_cold_push_waiting_on_the_panes_lock_sends_no_cold_frame_after_the_full_build(self):
        # the guard decides under the pane's slot lock: a cold push past its read waits on the lock while another thread lands
        # the full build and sends it; once it gets the lock, it sends no cold frame after the full build's
        self._plant(self._partial())
        full = self._full()
        pane, frames = self._pane()
        gate = _Gate()
        pane["dlock"] = gate
        held, go = threading.Event(), threading.Event()

        def holder():
            with gate:
                held.set()
                go.wait(10)
                km._built_timeline[:] = [self.sig[0], full, time.time(), time.time()]
                km._tl_partial[0] = None
                km._push([pane])
        hth = threading.Thread(target=holder, daemon=True)
        hth.start()
        self.assertTrue(held.wait(10))

        def arm():
            self.on_skel = None
            gate.armed = True
        self.on_skel = arm                                # past the push's cold read, before its sends
        pth = threading.Thread(target=km._push, args=([pane],), daemon=True)
        pth.start()
        try:
            self.assertTrue(gate.waiting.wait(10), "the cold push waited on the pane's lock after its cold read")
        finally:
            go.set()
            hth.join(10)
            pth.join(10)
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", False)],
                         "a cold frame followed the full build's: %r" % (self._kinds(frames),))

    # ---- round three (2026-10-04): the full build waits for the board's first frame, and the pusher sends it ----

    FEED = {"type": "feed", "asks": [], "working": [], "awaiting": []}

    @staticmethod
    def _board():
        """A board pane (app "feed") that records each frame and when it arrived."""
        frames, at = [], []

        def send(s):
            at.append(time.monotonic())
            frames.append(json.loads(s))
        return {"app": "feed", "alive": True, "sent": {}, "send": send}, frames, at

    @staticmethod
    def _board_unfed():
        """A kernel whose board has had no feed frame yet (the event is once per kernel life)."""
        ev = getattr(km, "_BOARD_FIRST", None)
        if ev is not None:
            ev.clear()

    @staticmethod
    def _release_board():
        """Cleanup: whatever a test left waiting for the board's first frame is let go."""
        for nm in ("_BOARD_FIRST", "_TL_BOARD_WAKE"):
            ev = getattr(km, nm, None)
            if ev is not None:
                ev.set()

    def _phase_two_waits_for(self, release, label):
        """The builder's pass on its own thread with a board pane connected and unfed: once phase one has landed, phase two
        is seen blocked on the board-wake event with no timeout (a fixed timer or a timed wait of any length fails here), does not begin within half a
        second, nor before `release()` runs, and begins within a second after it. Returns (when the release began, when
        phase two began)."""
        started = {}

        def on_bars(kind, live_map):
            if kind == "full":
                started["at"] = time.monotonic()
        self.on_bars = on_bars
        entered = threading.Event()
        timeouts = []

        class Recording(threading.Event):
            def wait(self, timeout=None):
                timeouts.append(timeout)
                entered.set()
                return super().wait(timeout)
        saved_wake = km._TL_BOARD_WAKE
        km._TL_BOARD_WAKE = Recording()
        th = threading.Thread(target=km._timeline_builder_pass, daemon=True)
        try:
            th.start()
            self._wait(lambda: km._tl_partial[0] is not None, "phase one landed")
            self.assertTrue(entered.wait(5), "%s: phase two did not wait on the board-wake event" % label)
            time.sleep(0.5)
            self.assertNotIn("full", self.builds, "%s: phase two began before the board's first feed frame: %r"
                             % (label, self.builds))
            t0 = time.monotonic()
            release()
            self._wait(lambda: "at" in started, "%s: phase two began after the release" % label, timeout=5.0)
        finally:
            self._release_board()
            th.join(10)
            km._TL_BOARD_WAKE = saved_wake
        self.assertFalse(th.is_alive(), "the pass ended")
        self.assertEqual([t for t in timeouts if t is not None], [], "%s: the wait for the board is timed: %r" % (label, timeouts))
        self.assertLess(started["at"] - t0, 1.0, "%s: phase two began right after the release, keyed on it" % label)
        self.assertEqual(self.builds[-1], "full")
        return t0, started["at"]

    def test_with_a_board_pane_connected_phase_two_waits_for_its_first_feed_frame(self):
        # the board's connect push built its cold feed beside both builder phases, and its first frame came 2.6 and 10.0 s
        # later than at the base (lab, synthetic): phase two now starts once that frame has gone out, here the push's own
        board, bframes, bat = self._board()
        km._clients[:] = [board]
        km._cached_feed = lambda *a, **k: self.FEED
        self._board_unfed()
        with mock.patch.dict(km._BOOT_MARKS, {}, clear=True):   # no early frame on this push: the feed section's send
            t0, began = self._phase_two_waits_for(lambda: km._push([board]), "the push's feed send")
        self.assertEqual([f["type"] for f in bframes], ["feed"], "the board got its first frame from the push")
        self.assertGreaterEqual(began, bat[0], "phase two began after the board's frame went out")

    def test_the_early_feed_frames_send_releases_phase_two(self):
        # the cold kernel's early feed frame (_feed_first), the measured connect push's road
        board, bframes, bat = self._board()
        km._clients[:] = [board]
        km._cached_feed = lambda *a, **k: self.FEED
        self._board_unfed()
        t0, began = self._phase_two_waits_for(lambda: km._feed_first(int(time.time()), {}, [board], True), "the early frame")
        self.assertEqual([f["type"] for f in bframes], ["feed"], "the early frame went to the board")
        self.assertGreaterEqual(began, bat[0], "phase two began after it went out")

    def test_an_early_feed_frame_that_raises_releases_phase_two(self):
        # a feed that cannot build never holds the cold timeline build: the push's early frame raised, and nothing went
        board, bframes, _bat = self._board()
        km._clients[:] = [board]
        self._board_unfed()
        saved_feed = list(km._built_feed)

        def boom(*a, **k):
            raise RuntimeError("synthetic feed failure")

        def release():
            km._cached_feed = boom
            km._built_feed[:] = [None, None, 0.0, 0.0]
            with mock.patch.dict(km._BOOT_MARKS, {"firstServe": time.time()}), contextlib.redirect_stderr(io.StringIO()):
                km._push([board])
        try:
            self._phase_two_waits_for(release, "a raising early frame")
        finally:
            km._built_feed[:] = saved_feed
        self.assertEqual(bframes, [], "the board got nothing: the release is the raise")

    def test_a_board_pane_leaving_before_its_first_frame_releases_phase_two(self):
        board, bframes, _bat = self._board()
        km._clients[:] = [board]
        self._board_unfed()

        def leaves():                                     # the ws handler's end, as it runs it
            with km._clients_lock:
                km._clients.remove(board)
            km._board_pane_gone(board)
        self._phase_two_waits_for(leaves, "the board pane leaving")
        src = inspect.getsource(km.Handler._ws)
        self.assertIn("_board_pane_gone(client)", src, "the ws handler's end wakes the wait")
        self.assertLess(src.index("_clients.remove(client)"), src.index("_board_pane_gone(client)"),
                        "...after the pane has left the client list")

    def test_phase_two_waits_on_nothing_with_no_board_pane_or_a_fed_board(self):
        board, _bframes, _bat = self._board()
        pane, _frames = self._pane()
        for label, clients, fed in (("no board pane", [pane], False), ("a board already fed", [board, pane], True)):
            with self.subTest(label):
                del self.builds[:]
                km._built_timeline[:] = [None, None, 0.0, 0.0]
                _cold_timeline_state_clear()
                km._clients[:] = clients
                self._board_unfed()
                if fed:                                   # the board's first frame went out before the pass
                    self._release_board()
                th = threading.Thread(target=km._timeline_builder_pass, daemon=True)
                try:
                    th.start()
                    th.join(5)
                    alive = th.is_alive()
                finally:
                    self._release_board()
                    th.join(10)
                self.assertFalse(alive, "%s: the pass ran on to its end without waiting" % label)
                self.assertEqual(self.builds, ["live", "full"], "%s: %r" % (label, self.builds))

    def test_an_unmoved_landing_goes_on_the_pushers_next_push_not_from_the_builder(self):
        # the builder sends no frame of its full build (2026-10-04): its own send, keyed on a freshness rule whose 5 s clock
        # bucket moves during any longer phase two, never fired in the lab (synthetic), and the round-three review and gate
        # each found a race in which it moved a lane back. With nothing moved during phase two, the pusher's next push
        # serves the build as it stands
        pane, frames = self._pane()
        km._clients[:] = [pane]
        km._push([pane])                                  # the cold lanes
        km._timeline_builder_pass()                       # no push runs during it
        self.assertEqual(self._kinds(frames), [("data", None), ("bars", True)],
                         "the cold lanes and the partial's bars, and no frame of the full build from the builder: %r"
                         % (self._kinds(frames),))
        km._push([pane])                                  # the pusher's next push
        self.assertEqual(self._kinds(frames)[2:], [("data", None), ("bars", False)],
                         "the full build's lanes, then its bars, settled: %r" % (self._kinds(frames),))
        self.assertEqual([l["id"] for l in frames[2]["data"]["sessions"]], [S1, S2], "the full build's lanes, dead one too")
        self.assertEqual(frames[3]["turns"], km._built_timeline[1]["turns"], "...and its bars")
        self.assertEqual(self.builds, ["skel", "live", "full"], "served as it stands: no rebuild")

    def test_a_landing_after_anything_moved_sends_nothing_from_the_builder(self):
        # the view signature moved, or a dirty mark landed, while phase two ran: a pane may hold cold lanes newer than the
        # build, so the pusher's next push rebuilds it first and sends that (no lane moves back), and the builder sends nothing
        def moved_signature():
            self.sig[0] = ("sig", "moved")
        for label, move in (("a moved signature", moved_signature), ("a dirty mark", km._mark_views_dirty)):
            with self.subTest(label):
                del self.builds[:]
                km._built_timeline[:] = [None, None, 0.0, 0.0]
                km._skel_wire = km._bars_wire = None
                km._views_dirty[0] = 0.0
                _cold_timeline_state_clear()
                self.sig[0] = ("sig",)
                pane, frames = self._pane()
                km._clients[:] = [pane]
                km._push([pane])                          # the cold lanes

                def on_bars(kind, live_map, move=move):
                    if kind == "full":
                        move()                            # phase two has read its world
                self.on_bars = on_bars
                km._timeline_builder_pass()
                self.on_bars = None
                self.assertEqual(self._kinds(frames), [("data", None), ("bars", True)],
                                 "%s: nothing from the builder after the landing: %r" % (label, self._kinds(frames)))
                km._push([pane])                          # the pusher's next push
                self.assertEqual(self.builds, ["skel", "live", "full", "full"], "%s: the pusher rebuilt it first" % label)
                self.assertEqual(self._kinds(frames)[2:], [("data", None), ("bars", False)],
                                 "%s: the rebuild's lanes, then its bars" % label)

    # ---- the PR's first review round (2026-10-04) ----

    def test_a_cold_lanes_send_landing_after_a_newer_builds_never_steps_the_pane_back(self):
        # review find (2026-10-04): of two overlapping cold-lanes builds, the older one could also end first, be stored and be
        # sent after the newer one's, since the send checked only that the slot was still cold, and the pane stepped back
        # until the next push. Each send now takes the newest memo under the pane's slot lock, and none once the full build
        # has landed: the third case lands it between the guard's read of the slot and the send's read of the memo
        world, arm, gate = ["working"], [False], [None]

        def build(now, live_map=None, with_bars=True, live_only=False):
            sessions = [{"id": S1, "name": NAMES[S1], "state": world[0]}]
            if not live_only:
                sessions.append({"id": S2, "name": NAMES[S2], "state": "ended"})
            if not with_bars and arm[0] and threading.current_thread().name == "held":
                arm[0] = False
                gate[0].name = "held"                     # its next take of the pane's lock is its cold-lanes send
            turns = {s["id"]: [{"id": "t-" + s["id"][-1], "t": 1, "t1": 2}] for s in sessions} if with_bars else {}
            return {"type": "timeline", "sessions": sessions, "turns": turns, "judging": [], "messages": [], "now": now}
        km.build_timeline = build
        cases = (("a connect's send held", True, False), ("the pusher's send held", False, False),
                 ("the full build landing at the held send", True, True))
        for label, held_connect, lands in cases:
            with self.subTest(label):
                km._built_timeline[:] = [None, None, 0.0, 0.0]
                km._views_dirty[0] = 0.0
                km._skel_wire = km._bars_wire = None
                _cold_timeline_state_clear()
                world[0], arm[0], gate[0] = "working", False, _HoldAtLock()
                frames = []
                pane = {"app": "timeline", "alive": True, "sent": {}, "dlock": gate[0],
                        "send": lambda s, frames=frames: frames.append(json.loads(s))}
                memo = _HookAtRead()
                full = {"type": "timeline", "sessions": [{"id": S1, "name": NAMES[S1], "state": "waiting"},
                                                         {"id": S2, "name": NAMES[S2], "state": "ended"}],
                        "turns": {}, "judging": [], "messages": [], "now": int(time.time())}

                def land(memo=memo, full=full):           # the builder's landing: the slot filled, then the memo dropped
                    km._built_timeline[:] = [self.sig[0], full, 0.0, time.time()]
                    list.__setitem__(memo, 0, None)
                memo.hook = land
                with mock.patch.object(km, "REBUILD_MIN_S", 0.2), mock.patch.object(km, "_tl_cold_lanes", memo):
                    km._push([pane])                      # the cold lanes: web working
                    self.sig[0] = ("sig", label)
                    time.sleep(0.3)                       # the memo is past the floor under a moved signature
                    arm[0] = True
                    th = threading.Thread(target=km._push, args=([pane],), kwargs={"connect": held_connect}, name="held",
                                          daemon=True)
                    th.start()
                    try:
                        self.assertTrue(gate[0].held.wait(10), "%s: the older build was stored and its send waits" % label)
                        world[0] = "waiting"              # web stops working
                        km._mark_views_dirty()
                        km._push([pane], connect=not held_connect)   # the newer build: stored, and sent to the pane
                        if lands:
                            memo.thread = th.ident        # the held send's read of the memo, under the pane's lock
                    finally:
                        gate[0].go.set()
                        th.join(10)
                    km._push([pane])                      # the next push
                web = [[s.get("state") for s in f["data"]["sessions"] if s["id"] == S1][0]
                       for f in frames if f["type"] == "data"]
                self.assertEqual(web[:2], ["working", "waiting"], "%s: the cold lanes, then the newer build's: %r" % (label, web))
                self.assertNotIn("working", web[1:], "%s: the pane stepped back to the older lanes: %r" % (label, web))
                if lands:
                    last = [f for f in frames if f["type"] == "data"][-1]
                    self.assertEqual(len(last["data"]["sessions"]), 2, "the full build's lanes went next: %r" % (web,))

    def test_a_cold_connect_sends_the_cold_lanes_under_its_own_clock(self):
        # a connect stamps the memo with its own cycle's clock, as a warm connect re-stamps the cached build: the pane anchors
        # its live edge on its first sample, and the memo's own clock can be a floor and a bucket old (review find,
        # 2026-10-04: test 8 compares only the sessions, so sending the memo's clock left the suite green)
        a, _fa = self._pane()
        km._push([a])                                     # the pusher's cold lanes: the memo
        m = km._tl_cold_lanes[0]
        aged = {"type": "data", "data": dict(m[2]["data"], now=m[2]["data"]["now"] - 30)}
        km._tl_cold_lanes[0] = m[:2] + (aged, json.dumps(aged)) + m[4:]   # the memo as a push 30 s ago built it
        b, fb = self._pane()
        t0 = int(time.time())
        km._push([b], connect=True)
        self.assertEqual(self.builds, ["skel"], "the connect served the memo: %r" % self.builds)
        self.assertEqual(fb[0]["data"]["sessions"], aged["data"]["sessions"])
        self.assertGreaterEqual(fb[0]["data"]["now"], t0, "the connect's clock, not the memo's: %r" % (fb[0]["data"]["now"],))

    def test_a_raising_bars_fill_or_pane_send_never_fails_the_builders_pass(self):
        # review find (2026-10-04): nothing raised inside _timeline_send_partial, so narrowing either of its excepts left the
        # suite green, while a raise there would fail the whole pass: the full build lost and a failed pass counted
        def boom(*a, **k):
            raise RuntimeError("synthetic send failure")
        for label, name, said in (("the fill raises", "_bars_fill", "timeline-build send bars (fill):"),
                                  ("a pane's send raises", "_send_slot", "timeline-build send bars:")):
            with self.subTest(label):
                del self.builds[:]
                km._built_timeline[:] = [None, None, 0.0, 0.0]
                _cold_timeline_state_clear()
                pane, frames = self._pane()
                pane["sent"][("timeline",)] = ("lanes", time.time())   # it holds a lanes frame: the builder sends it bars
                km._clients[:] = [pane]
                failed0 = km._VIEW_STATS.get("tlBuildFailed", 0)
                err = io.StringIO()
                th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
                alive = None
                try:
                    with mock.patch.object(km, name, boom), contextlib.redirect_stderr(err):
                        th.start()
                        km._cached_timeline(int(time.time()), {}, ("sig",), False)   # the owe
                        self._wait(lambda: km._built_timeline[1] is not None
                                   or km._VIEW_STATS.get("tlBuildFailed", 0) != failed0, "%s: the pass ended" % label)
                finally:
                    alive = self._stop(th)
                self.assertFalse(alive, "the loop ended on the stop seam")
                self.assertIsNotNone(km._built_timeline[1], "%s: the full build landed" % label)
                self.assertEqual(km._VIEW_STATS.get("tlBuildFailed", 0), failed0, "%s: no failed pass counted" % label)
                self.assertIn(said, err.getvalue(), "%s: said on stderr" % label)
                self.assertEqual(self.builds, ["live", "full"], "%s: one pass, both phases: %r" % (label, self.builds))

    def test_a_failed_pass_waits_out_the_backoff_before_an_owe_retries_it(self):
        # review find (2026-10-04): test 15 sleeps past the backoff before its retry, so a loop with no backoff wait passed it
        fails, marks = [1], []

        def on_bars(kind, live_map):
            marks.append((kind, time.monotonic()))
            if kind == "full" and fails[0] > 0:
                fails[0] -= 1
                raise RuntimeError("synthetic build failure")
        self.on_bars = on_bars
        failed0 = km._VIEW_STATS.get("tlBuildFailed", 0)
        th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
        alive = None
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                th.start()
                km._cached_timeline(int(time.time()), {}, ("sig",), False)   # the owe
                self._wait(lambda: km._VIEW_STATS.get("tlBuildFailed", 0) - failed0 >= 1, "the failure")
                km._cached_timeline(int(time.time()), {}, ("sig",), False)   # an owe at once
                self._wait(lambda: len([k for k, _t in marks if k == "live"]) >= 2, "the retry's phase one")
        finally:
            alive = self._stop(th)
        self.assertFalse(alive, "the loop ended on the stop seam")
        failed_at = [t for k, t in marks if k == "full"][0]
        retry_at = [t for k, t in marks if k == "live"][1]
        self.assertGreaterEqual(retry_at - failed_at, km.PUSHER_FAIL_BACKOFF_S[0] - 0.05,
                                "the retry waited out the backoff after the failure: %.3f s" % (retry_at - failed_at))

    def test_the_builders_full_build_returns_a_filled_slot_as_it_stands(self):
        # _timeline_build_cold over a filled slot, fresh or stale: the slot's build comes back and nothing is built. The pass
        # returns before it today, so only a direct call reaches the guard its docstring promises (review find, 2026-10-04)
        held = {"type": "timeline", "sessions": [], "turns": {}, "judging": [], "messages": [], "now": 1}
        for entry in ([("sig",), held, time.time(), time.time()], [("older",), held, 1.0, 1.0]):
            km._built_timeline[:] = entry
            before = list(km._built_timeline)
            self.assertIs(km._timeline_build_cold(int(time.time()), {}, ("sig",)), held)
            self.assertEqual(self.builds, [], "nothing built over a filled slot: %r" % (entry[0],))
            self.assertEqual(km._built_timeline, before, "the slot's entry stands: %r" % (entry[0],))

    def test_the_builders_send_stops_at_a_pane_whose_lock_it_takes_after_the_full_build_landed(self):
        # _timeline_send_partial decides each pane under its slot lock: the full build landing as it takes the second pane's
        # lock stops the send there. The builder is the slot's one writer, so only a direct call reaches it today (review find,
        # 2026-10-04)
        a, fa = self._pane()
        b, fb = self._pane()
        for p in (a, b):
            p["sent"][("timeline",)] = ("lanes", time.time())   # each holds a lanes frame
        full, landed = self._full(), []

        class LandsOnTake:
            """b's slot lock: the full build lands as the send takes it."""

            def __init__(self):
                self.lk = threading.RLock()

            def acquire(self, blocking=True, timeout=-1):
                if not landed:
                    landed.append(1)
                    km._built_timeline[:] = [("sig",), full, 0.0, time.time()]
                return self.lk.acquire(blocking, timeout)

            def release(self):
                self.lk.release()

            def __enter__(self):
                self.acquire()
                return self

            def __exit__(self, *x):
                self.release()
                return False
        b["dlock"] = LandsOnTake()
        km._clients[:] = [a, b]
        km._timeline_send_partial(self._partial())
        self.assertEqual(landed, [1], "the full build landed at the second pane's take")
        self.assertEqual(self._kinds(fa), [("bars", True)], "the first pane got the partial's bars, warming")
        self.assertEqual(fb, [], "the second pane got no cold bars after the full build landed")


class FullBuildLanding(unittest.TestCase):
    """The builder's full build landing over the cold lanes a Sessions pane holds (the gate's probe, 2026-10-03). Phase two
    reads its world at its start and runs for as long as the full build takes; meanwhile the pusher's cold lanes are rebuilt
    under a moved signature, so the pane holds lanes newer than phase two's. Stamped as built at its FINISH, the full build
    was fresh for REBUILD_MIN_S after it landed, and the push on the landing wake projected its lanes, as old as phase two's
    start, over the newer ones: web went back to working with no new information. Stamped at its START, a phase two shorter
    than the floor was still inside it at landing. Stored with no built-at, a signature that moved during the build
    rebuilds it on the pusher first, however long phase two ran. REBUILD_MIN_S is 0.2 s here and phase two outlasts it,
    except in the second test, which sets a 1 s floor. The pane is not in km._clients (empty here, restored after), so the
    builder's send of the partial's bars reaches no pane in this class."""

    def setUp(self):
        self.saved = {nm: getattr(km, nm) for nm in ("build_timeline", "_live_map", "_cached_feed", "_chat_tab_sessions",
                                                      "_fleet_view_sig", "_skel_wire", "_bars_wire", "REBUILD_MIN_S")}
        self.saved_state = (list(km._built_timeline), km._views_dirty[0], dict(km._VIEW_STATS), list(km._clients))
        km._clients[:] = []
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._skel_wire = km._bars_wire = None
        _cold_timeline_state_clear()
        km.REBUILD_MIN_S = 0.2                            # a 0.2 s floor for the 2 s one; phase two below outlasts it
        self.world, self.sig, self.hook = ["working"], [("w", 1)], None
        km.build_timeline = self._build
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None
        km._chat_tab_sessions = lambda now, live_map: []
        km._fleet_view_sig = lambda now, live_map: self.sig[0]

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        bt, vd, vs, clients = self.saved_state
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._clients[:] = clients
        _cold_timeline_state_clear()

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        state = self.world[0]                             # the world as this build reads it, at its start
        sessions = [{"id": S1, "name": "web", "state": state}]
        if not live_only:
            sessions.append({"id": S2, "name": "api", "state": "ended"})
        turns = {s["id"]: [{"id": "t-" + s["id"][-1], "t": 1, "t1": 2}] for s in sessions} if with_bars else {}
        if with_bars and not live_only and self.hook is not None:
            h, self.hook = self.hook, None
            h()                                           # phase two runs on after its read
        return {"type": "timeline", "sessions": sessions, "turns": turns, "judging": [], "messages": [], "now": now}

    def test_a_lane_never_moves_back_when_the_full_build_lands(self):
        frames = []
        pane = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}
        km._push([pane])                                  # the cold lanes of the world then: web working
        started, release = threading.Event(), threading.Event()

        def hook():
            started.set()
            release.wait(10)
        self.hook = hook
        th = threading.Thread(target=km._timeline_builder_pass, daemon=True)
        th.start()
        self.assertTrue(started.wait(10), "phase two began")
        self.world[0] = "waiting"                         # web stops working while phase two runs
        self.sig[0] = ("w", 2)
        time.sleep(0.3)                                   # past the floor since the cold lanes were built
        km._push([pane])                                  # the pusher's cold lanes, rebuilt: web waiting
        time.sleep(0.3)                                   # phase two runs on, past the floor
        release.set()
        th.join(10)
        self.assertIsNotNone(km._built_timeline[1], "the full build landed")
        km._push([pane])                                  # the pusher's push on the landing wake
        time.sleep(0.3)
        km._push([pane])                                  # and the one after
        web = [[s["state"] for s in f["data"]["sessions"] if s["id"] == S1][0] for f in frames if f["type"] == "data"]
        after = web[web.index("waiting"):] if "waiting" in web else []
        self.assertNotIn("working", after, "a lane moved back to an older state with no new information: %r" % web)
        self.assertEqual(web, ["working", "waiting", "waiting"], "the cold lanes, their rebuild, the full build rebuilt")

    def test_a_phase_two_shorter_than_the_floor_never_moves_a_lane_back(self):
        # the landing case with a phase two well inside REBUILD_MIN_S: the cold lanes are rebuilt inside phase two from a newer
        # world, and the full build lands within the floor of its own start
        km.REBUILD_MIN_S = 1.0
        frames = []
        pane = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}
        km._push([pane])                                  # the cold lanes: web working
        time.sleep(1.1)                                   # the memo is past the floor

        def hook():                                       # phase two has read its world (web working)
            self.world[0] = "waiting"
            self.sig[0] = ("w", 2)
            th = threading.Thread(target=km._push, args=([pane],), daemon=True)
            th.start()
            th.join(10)                                   # the pusher's cold lanes, rebuilt: web waiting
        self.hook = hook
        km._timeline_builder_pass()
        km._push([pane])                                  # the push on the landing wake
        web = [[s["state"] for s in f["data"]["sessions"] if s["id"] == S1][0] for f in frames if f["type"] == "data"]
        self.assertIn("waiting", web, "the rebuilt cold lanes reached the pane: %r" % web)
        self.assertNotIn("working", web[web.index("waiting"):], "a lane moved back with no new information: %r" % web)


class _InFlightPush(unittest.TestCase):
    """A pusher push already in flight when the builder's full build lands (the round-three review's probe, 2026-10-04). Its
    cycle took its liveness snapshot and its clock before phase two began; held in its chat stage, it reaches its timeline
    section after the landing, under a signature that is not the build's: a live row moved between its snapshot and phase
    two's (InFlightPushRowMoved: web working, then waiting), or only the clock did (InFlightPushClockMoved: the signature
    carries the clock, a 1 s stand-in for the 5 s bucket, and web's open interval ends at its build's clock). Rebuilt from
    its older snapshot, the build reached the pane after the builder's send of the partial's bars, built from the newer
    world, and moved the bars and their clock back with no new information; the push now serves the build as it stands.
    The push's thread carries the cycle's snapshot stamp, as _pusher_cycle sets it. REBUILD_MIN_S is 0.2 s here."""

    BUCKET = False

    def setUp(self):
        self.saved = {nm: getattr(km, nm) for nm in ("build_timeline", "_live_map", "_cached_feed", "_chat_tab_sessions",
                                                      "_fleet_view_sig", "_skel_wire", "_bars_wire", "REBUILD_MIN_S")}
        self.saved_state = (list(km._built_timeline), km._views_dirty[0], dict(km._VIEW_STATS), list(km._clients))
        km._clients[:] = []
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._skel_wire = km._bars_wire = None
        _cold_timeline_state_clear()
        km.REBUILD_MIN_S = 0.2
        self.live, self.block = [None], None
        km.build_timeline = self._build
        km._live_map = lambda: self.live[0]
        km._cached_feed = lambda *a, **k: None
        km._chat_tab_sessions = self._tabs
        km._fleet_view_sig = self._sig

    def tearDown(self):
        if self.block is not None:
            self.block[1].set()
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        bt, vd, vs, clients = self.saved_state
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._clients[:] = clients
        _cold_timeline_state_clear()

    def _sig(self, now, live_map):
        rows = tuple(sorted((sid, (row or {}).get("state")) for sid, row in (live_map or {}).items()))
        return ("v", now if self.BUCKET else 0, rows)

    def _tabs(self, now, live_map):
        b = self.block
        if b is not None and threading.current_thread().name == "in-flight":
            b[0].set()
            b[1].wait(10)                                 # the in-flight push, held in its chat stage
        return []

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        lm = live_map or {}
        web = {"id": S1, "name": NAMES[S1], "state": (lm.get(S1) or {}).get("state", "ended")}
        if self.BUCKET:
            web["states"] = [[0, now]]                    # an open interval ends at the build's clock
        sessions = [web] + ([] if live_only else [{"id": S2, "name": NAMES[S2], "state": "ended"}])
        turns = ({s["id"]: [{"id": "t-%s-%s" % (s["id"][-1], s["state"]), "t": 1, "t1": now}] for s in sessions}
                 if with_bars else {})
        return {"type": "timeline", "sessions": sessions, "turns": turns, "judging": [], "messages": [], "now": now}

    def _run(self):
        """The cold lanes from the first snapshot; the in-flight push, its snapshot stamped, held in its chat stage while the
        world moves (or only the clock) and the builder's pass runs (the partial's bars sent, the full build landed); then
        the held push runs on, and the next push reads the world now. Returns web's lanes frames as (state, clock, open
        interval's end) and the bars frames as (clock, bar ids)."""
        old = {S1: {"state": "working"}}
        new = dict(old) if self.BUCKET else {S1: {"state": "waiting"}}
        self.live[0] = old
        frames = []
        pane = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}
        km._clients[:] = [pane]
        km._push([pane], live_map=old)                   # the cold lanes, from the first snapshot
        started, release = threading.Event(), threading.Event()
        self.block = (started, release)

        def in_flight():                                  # the pusher cycle's push: its snapshot `old`, its clock now
            km._live_scope.snapshot_t = time.time()      # the cycle's snapshot stamp, as _pusher_cycle sets it
            try:
                km._push([pane], live_map=old)
            finally:
                km._live_scope.snapshot_t = None
        th = threading.Thread(target=in_flight, name="in-flight", daemon=True)
        th.start()
        self.assertTrue(started.wait(10), "the in-flight push reached its chat stage")
        self.live[0] = new                               # the world moves after its snapshot (or only the clock does)
        time.sleep(1.1)
        while time.time() % 1.0 > 0.3:                   # the builder's pass inside one clock second
            time.sleep(0.01)
        km._timeline_builder_pass()                       # the partial and its bars, then the full build lands
        time.sleep(0.3)                                   # past REBUILD_MIN_S since phase two began
        release.set()
        th.join(10)
        self.assertFalse(th.is_alive(), "the in-flight push ended")
        km._push([pane], live_map=self.live[0])          # the next push, from the world now
        lanes = []
        for f in frames:
            if f["type"] == "data":
                web = [s for s in f["data"]["sessions"] if s["id"] == S1][0]
                lanes.append((web["state"], f["data"]["now"], (web.get("states") or [[None, None]])[0][1]))
        bars = [(f["now"], sorted(e["id"] for v in f["turns"].values() for e in v)) for f in frames if f["type"] == "bars"]
        self.assertTrue(lanes and bars, "the pane got lanes and bars: %r" % (frames,))
        return lanes, bars


class InFlightPushRowMoved(_InFlightPush):
    BUCKET = False

    def test_a_push_in_flight_at_the_landing_never_steps_a_lane_or_its_bars_back(self):
        lanes, bars = self._run()
        states = [st for st, _now, _end in lanes]
        if "waiting" in states:
            self.assertNotIn("working", states[states.index("waiting"):], "web's lane stepped back to working: %r" % (lanes,))
        seen_wait = False
        for _now, ids in bars:
            if any(i.endswith("-waiting") for i in ids):
                seen_wait = True
            elif seen_wait and any(i.endswith("-working") for i in ids):
                self.fail("web's bars stepped back to working with no new information: %r" % (bars,))
        self.assertTrue(seen_wait, "the bars reached the world after the move: %r" % (bars,))


class InFlightPushClockMoved(_InFlightPush):
    BUCKET = True

    def test_a_push_in_flight_at_the_landing_never_steps_the_clock_back(self):
        lanes, bars = self._run()
        nows = [now for _st, now, _end in lanes]
        ends = [end for _st, _now, end in lanes]
        self.assertEqual(nows, sorted(nows), "the lanes frame's clock went back: %r" % (lanes,))
        self.assertEqual(ends, sorted(ends), "web's open interval shrank with no new information: %r" % (lanes,))
        bnows = [now for now, _ids in bars]
        self.assertEqual(bnows, sorted(bnows), "the bars frame's clock went back: %r" % (bars,))


class ColdLanesWorldOrder(_InFlightPush):
    """The cold lanes dated by the world they read (the PR's first review round, 2026-10-04). The memo was dated by when its
    build began, but a pusher push builds from its cycle's snapshot after the cycle's jobs and its own chat stage: one that
    began its build after a reload's connect push had read a newer world replaced that connect's memo with older lanes and
    stepped the pane's lane back. The memo now carries the stamp of its liveness snapshot (the cycle's, or the connect's own
    read): a memo from a newer world is served to an older push as it stands, and of two overlapping builds the newer
    world's is the memo, whichever began first. The in-flight push's thread carries its cycle's stamp, as _pusher_cycle
    sets it; web works in the first snapshot and waits in the second. REBUILD_MIN_S is 0.2 s here."""

    BUCKET = False

    def setUp(self):
        super().setUp()
        self.skels, self.hold = [], None

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        out = super()._build(now, live_map, with_bars, live_only)   # web's state from the snapshot it is handed
        if not with_bars:
            name = threading.current_thread().name
            self.skels.append(name)
            h = self.hold
            if h is not None and name == h[0]:
                self.hold = None
                h[1].set()
                h[2].wait(10)                             # the build has read its world and is held before its store
        return out

    def _in_flight(self, pane, old):
        """The pusher cycle's push, its snapshot `old` and its stamp taken now, held in its chat stage."""
        started, release = threading.Event(), threading.Event()
        self.block = (started, release)

        def run():
            km._live_scope.snapshot_t = time.time()
            try:
                km._push([pane], live_map=old)
            finally:
                km._live_scope.snapshot_t = None
        th = threading.Thread(target=run, name="in-flight", daemon=True)
        th.start()
        self.assertTrue(started.wait(10), "the in-flight push reached its chat stage")
        return th, release

    @staticmethod
    def _web(frames):
        return [[s["state"] for s in f["data"]["sessions"] if s["id"] == S1][0] for f in frames if f["type"] == "data"]

    def _pane_frames(self):
        frames = []
        return {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}, frames

    def test_a_push_from_an_older_snapshot_serves_a_newer_memo_as_it_stands(self):
        # the cycle stamps its snapshot and runs its jobs before its push; a reload's connect push reads a newer world in
        # between, so the cycle's push, which begins after the connect, still builds from the older snapshot
        old, new = {S1: {"state": "working"}}, {S1: {"state": "waiting"}}
        self.live[0] = old
        pane, frames = self._pane_frames()
        km._push([pane], live_map=old)                   # the cold lanes: web working
        time.sleep(0.3)                                   # past the floor
        stamped, release = threading.Event(), threading.Event()

        def cycle():
            km._live_scope.snapshot_t = time.time()      # the cycle's stamp, before its snapshot `old` (_pusher_cycle)
            stamped.set()
            release.wait(10)                              # the cycle's jobs before its push
            try:
                km._push([pane], live_map=old)
            finally:
                km._live_scope.snapshot_t = None
        th = threading.Thread(target=cycle, name="in-flight", daemon=True)
        th.start()
        self.assertTrue(stamped.wait(10), "the cycle stamped its snapshot")
        self.live[0] = new
        km._push([pane], connect=True)                    # a reload reads the world now: web waiting, and that is the memo
        time.sleep(0.3)                                   # past the floor, under a signature the cycle's push does not share
        del self.skels[:]
        release.set()
        th.join(10)
        self.assertFalse(th.is_alive(), "the cycle's push ended")
        self.assertEqual(self.skels, [], "the push from the older snapshot built no lanes over the newer memo: %r" % self.skels)
        self.assertEqual(self._web(frames), ["working", "waiting"], "and no lane stepped back: %r" % (self._web(frames),))

    def test_of_two_overlapping_builds_the_newer_worlds_is_the_memo_whichever_began_first(self):
        old, new = {S1: {"state": "working"}}, {S1: {"state": "waiting"}}
        self.live[0] = old
        pane, frames = self._pane_frames()
        km._push([pane], live_map=old)                   # the cold lanes: web working
        time.sleep(0.3)
        th, release = self._in_flight(pane, old)
        self.live[0] = new
        km._mark_views_dirty()                            # both builds below build the lanes again
        del self.skels[:]
        began, go = threading.Event(), threading.Event()
        self.hold = ("connect", began, go)
        cth = threading.Thread(target=km._push, args=([pane],), kwargs={"connect": True}, name="connect", daemon=True)
        cth.start()
        try:
            self.assertTrue(began.wait(10), "the reload's lanes build read the world now and is held before its store")
            release.set()
            th.join(10)                                   # the in-flight push builds from its older snapshot and stores first
            self.assertFalse(th.is_alive(), "the in-flight push ended")
        finally:
            go.set()
            cth.join(10)
        self.assertEqual(self.skels, ["connect", "in-flight"], "the reload's build began first: %r" % self.skels)
        self.assertEqual(self._web(frames), ["working", "waiting"],
                         "the reload sent the newer world's lanes, and nothing older went after them: %r" % (self._web(frames),))
        self.assertEqual(km._tl_cold_lanes[0][2]["data"]["sessions"][0]["state"], "waiting", "the memo is the newer world's")


class SameBucketLanding(unittest.TestCase):
    """The full build landing inside the view signature's clock bucket after the cold lanes moved on (the round-three gate's
    probe, 2026-10-04). The bucket turns just before phase two opens and nothing else moves; a push inside phase two rebuilds
    the cold lanes under the new bucket on a later clock; the full build then lands under the signature it read, unmoved.
    Served as it stands on the pusher's next push, its lanes went with web's open awaiting interval ending at phase two's
    clock, earlier than the end the pane already held. It is now stored under no signature, so that push rebuilds it first;
    the builder sends no frame of it. Synthetic: web live and awaiting, api dead within the window. REBUILD_MIN_S is 0.2 s."""

    def setUp(self):
        self.saved = {nm: getattr(km, nm) for nm in ("build_timeline", "_live_map", "_cached_feed", "_chat_tab_sessions",
                                                      "_fleet_view_sig", "_skel_wire", "_bars_wire", "REBUILD_MIN_S")}
        self.saved_state = (list(km._built_timeline), km._views_dirty[0], dict(km._VIEW_STATS), list(km._clients))
        km._clients[:] = []
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._skel_wire = km._bars_wire = None
        _cold_timeline_state_clear()
        km.REBUILD_MIN_S = 0.2
        self.sigv, self.hook = [("v", 1)], None
        km.build_timeline = self._build
        km._live_map = lambda: {S1: {"state": "awaiting"}}
        km._cached_feed = lambda *a, **k: None
        km._chat_tab_sessions = lambda now, live_map: []
        km._fleet_view_sig = lambda now, live_map: self.sigv[0]

    def tearDown(self):
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        bt, vd, vs, clients = self.saved_state
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._clients[:] = clients
        _cold_timeline_state_clear()

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        web = {"id": S1, "name": NAMES[S1], "state": "awaiting", "awaiting": [[0, now]]}   # an open interval ends at the clock
        sessions = [web] + ([] if live_only else [{"id": S2, "name": NAMES[S2], "state": "ended", "awaiting": []}])
        turns = {s["id"]: [{"id": "t-" + s["id"][-1], "t": 1, "t1": 2}] for s in sessions} if with_bars else {}
        if with_bars and not live_only and self.hook is not None:
            h, self.hook = self.hook, None
            h()                                           # phase two runs on after its read
        return {"type": "timeline", "sessions": sessions, "turns": turns, "judging": [], "messages": [], "now": now}

    def test_a_landing_inside_the_bucket_after_the_cold_lanes_moved_on_never_shrinks_an_open_interval(self):
        frames = []
        pane = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}
        km._clients[:] = [pane]
        km._push([pane])                                  # the cold lanes, under bucket one
        time.sleep(0.3)                                   # the memo is past the floor
        self.sigv[0] = ("v", 2)                           # the clock bucket turns before phase two opens; nothing else

        def hook():                                       # phase two has read its world and its clock
            time.sleep(1.1)                               # a second on, inside the same bucket
            th = threading.Thread(target=km._push, args=([pane],), daemon=True)
            th.start()
            th.join(10)                                   # the pusher's cold lanes, rebuilt under the new bucket, later clock
        self.hook = hook
        km._timeline_builder_pass()                       # phase one, phase two, the landing
        during = [len(f["data"]["sessions"]) for f in frames if f["type"] == "data"]
        km._push([pane])                                  # the pusher's next pushes, the same bucket
        km._push([pane])
        seq = [(len(f["data"]["sessions"]), f["data"]["now"], [s for s in f["data"]["sessions"] if s["id"] == S1][0]["awaiting"][0][1])
               for f in frames if f["type"] == "data"]
        self.assertEqual(set(during), {1}, "the builder sent no frame of its full build: %r" % (seq,))
        ends = [end for _k, _now, end in seq]
        self.assertEqual(ends, sorted(ends), "web's open interval shrank with no new information: %r" % (seq,))
        self.assertEqual(seq[-1][0], 2, "the full build's lanes reached the pane: %r" % (seq,))

    def test_a_cold_lanes_store_between_the_full_builds_read_and_its_store_waits_for_the_landing(self):
        # The full build reads the cold lanes' clock and stores itself into the slot under the cold lanes' lock (the PR's first
        # review round, 2026-10-04: no test reached that lock). Without it, a push could store newer cold lanes between that
        # read and the store, and send them; the full build, stored under the signature it read, then went on the next push
        # with web's open interval ending earlier than the end the pane held. The push here runs at that moment: it waits for
        # the lock, finds the slot filled, and sends nothing
        frames = []
        pane = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s))}
        km._clients[:] = [pane]
        gate, memo, pushed = _Gate(), _HookAtRead(), []
        memo.before = True                                # the full build reads the memo as it stood before the push

        def push_between():                               # inside the full build's read of the memo, under the lock
            gate.armed = True
            th = threading.Thread(target=km._push, args=([pane],), daemon=True)
            th.start()
            deadline = time.monotonic() + 5
            while th.is_alive() and not gate.waiting.is_set() and time.monotonic() < deadline:
                time.sleep(0.01)                          # until the push waits on the lock, or has run to its end
            pushed.append(th)
        memo.hook = push_between

        def hook():                                       # phase two has read its world and its clock
            time.sleep(1.1)                               # a second on, inside the same bucket: the push's clock is later
            memo.thread = threading.get_ident()           # the full build's next read of the memo is the one in its store
        self.hook = hook
        with mock.patch.object(km, "_tl_cold_lanes", memo), mock.patch.object(km, "_TL_COLD_LANES_LOCK", gate):
            km._push([pane])                              # the cold lanes, under bucket one
            time.sleep(0.3)                               # the memo is past the floor
            self.sigv[0] = ("v", 2)                       # the clock bucket turns before phase two opens; nothing else
            km._timeline_builder_pass()                   # phase one, phase two, the push between, the landing
            for th in pushed:
                th.join(10)
            km._push([pane])                              # the pusher's next pushes, the same bucket
            km._push([pane])
        self.assertEqual(len(pushed), 1, "the push ran inside the full build's store")
        seq = [(len(f["data"]["sessions"]), f["data"]["now"], [s for s in f["data"]["sessions"] if s["id"] == S1][0]["awaiting"][0][1])
               for f in frames if f["type"] == "data"]
        ends = [end for _k, _now, end in seq]
        self.assertEqual(ends, sorted(ends), "web's open interval shrank with no new information: %r" % (seq,))
        self.assertEqual(seq[-1][0], 2, "the full build's lanes reached the pane: %r" % (seq,))


class TimelineBuildStage(unittest.TestCase):
    """The builder loop's `timelineBuild` stage leaves out the wait for the board's first frame (the round-three gate's probe,
    2026-10-04), for a pass that completes and for one whose phase two raises: the wait is kept in a module slot as soon as
    it ends, so a raise in phase two no longer puts it in the stage. The real loop, stopped through the T282 seam; a board
    pane connected and unfed, released by the first frame's event after 0.6 s. Phase two takes 0.15 s, so the stage also
    holds at least 100 ms (the PR's first review round, 2026-10-04: with the upper bound alone, a deleted stage line or the
    wait subtracted twice passed)."""

    def setUp(self):
        # the kernel names this class needs, read before anything is changed: on a kernel without them (a red run over an
        # older tree) setUp raises with nothing replaced yet, so no stub outlives it into the classes after this one
        board_first, board_wake, said = km._BOARD_FIRST, km._TL_BOARD_WAKE, km._TL_BUILD_FAILED_SAID
        self.saved = {nm: getattr(km, nm) for nm in ("build_timeline", "_live_map", "_cached_feed", "_chat_tab_sessions",
                                                      "_fleet_view_sig")}
        self.saved_state = (list(km._built_timeline), km._views_dirty[0], dict(km._VIEW_STATS), list(km._clients))
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        _cold_timeline_state_clear()
        said.clear()
        self.fail_full = False
        km.build_timeline = self._build
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None
        km._chat_tab_sessions = lambda now, live_map: []
        km._fleet_view_sig = lambda now, live_map: ("sig",)
        km._clients[:] = [{"app": "feed", "alive": True, "sent": {}, "send": lambda s: None}]   # a board pane, unfed
        board_first.clear()
        board_wake.clear()

    def tearDown(self):
        km._BOARD_FIRST.set()
        km._TL_BOARD_WAKE.set()
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        bt, vd, vs, clients = self.saved_state
        km._built_timeline[:] = bt
        km._views_dirty[0] = vd
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._clients[:] = clients
        _cold_timeline_state_clear()

    PHASE_TWO_S = 0.15                                    # phase two's known length: the stage holds at least it

    def _build(self, now, live_map=None, with_bars=True, live_only=False):
        if with_bars and not live_only:
            time.sleep(self.PHASE_TWO_S)
            if self.fail_full:
                raise RuntimeError("synthetic phase-two failure")
        sessions = [{"id": S1, "name": NAMES[S1]}] + ([] if live_only else [{"id": S2, "name": NAMES[S2]}])
        return {"type": "timeline", "sessions": sessions, "turns": {}, "judging": [], "messages": [], "now": now}

    def _one_pass_ms(self):
        before = km._PERF_STATS.stages.get("timelineBuild", 0.0)
        failed0 = km._VIEW_STATS.get("tlBuildFailed", 0)
        km._LOOPS_STOP.clear()
        th = threading.Thread(target=km._timeline_builder, name="timeline-build", daemon=True)
        th.start()
        try:
            km._TL_OWED.set()
            deadline = time.time() + 5
            while km._tl_partial[0] is None and time.time() < deadline:
                time.sleep(0.01)
            self.assertIsNotNone(km._tl_partial[0], "phase one landed")
            time.sleep(0.6)                               # the pass waits for the board's first frame
            km._board_first_sent()                        # ...which goes out now
            deadline = time.time() + 5
            while (km._built_timeline[1] is None and km._VIEW_STATS.get("tlBuildFailed", 0) == failed0
                   and time.time() < deadline):
                time.sleep(0.01)
            time.sleep(0.2)                               # the stage is recorded right after the pass
        finally:
            km._LOOPS_STOP.set()
            km._TL_OWED.set()
            km._TL_BOARD_WAKE.set()
            th.join(5)
            if not th.is_alive():
                km._LOOPS_STOP.clear()
        self.assertFalse(th.is_alive(), "the loop ended on the stop seam")
        return km._PERF_STATS.stages.get("timelineBuild", 0.0) - before

    def test_a_completed_pass_leaves_the_wait_for_the_board_out_of_the_stage(self):
        ms = self._one_pass_ms()
        self.assertIsNotNone(km._built_timeline[1], "the pass completed")
        self.assertLess(ms, 400.0, "the stage counted the wait for the board: %.1f ms" % ms)
        self.assertGreaterEqual(ms, 100.0, "the stage holds phase two's own time (review find, 2026-10-04): %.1f ms" % ms)

    def test_a_pass_whose_phase_two_raises_leaves_the_wait_for_the_board_out_of_the_stage(self):
        self.fail_full = True
        with contextlib.redirect_stderr(io.StringIO()):
            ms = self._one_pass_ms()
        self.assertIsNone(km._built_timeline[1], "phase two raised and the slot stayed cold")
        self.assertLess(ms, 400.0, "the stage counted the wait for the board: %.1f ms" % ms)
        self.assertGreaterEqual(ms, 100.0, "the stage holds phase two's own time, up to its raise: %.1f ms" % ms)


class FeedFirstWaiter(unittest.TestCase):
    """The cold kernel's early feed frame (_feed_first) and a second caller waiting on its build (2026-10-03). After a restart
    the pusher's push and a pane's connect push both reach _feed_first while no feed is built; one builds, the other waits on
    the single-flight lock. The waiter used to re-check the stored build under the pusher's dirty rule, so a views mark the
    housekeeping made DURING the build made it build the same cold feed again. The waiter now serves the build it waited
    for, and the second build moves to the pusher's regular feed section (after its chat stage), which carries the mark. The
    built 1 / cached 1 read here counts the two early passes only. Over the whole push the count falls only where that
    section would have rebuilt over the waiter's rebuild anyway: a later mark, any time from the start of that rebuild to
    the regular ask, or a moved view signature with the regular ask more than REBUILD_MIN_S after that rebuild finished (a
    chat stage longer than the floor when the pusher waited, longer than the floor plus the rebuild when a connect push
    did). FeedFirstWholePush below reads the whole push. Hermetic: build_feed stubbed to count, mark and hold; fake feed
    panes."""

    def setUp(self):
        self.saved = (km.build_feed, list(km._built_feed), km._views_dirty[0], dict(km._VIEW_STATS), km._feed_wire,
                      dict(km._wire_stats))
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._feed_wire = None
        km._VIEW_STATS["feedWaited"] = 0
        km._wire_stats["feed_first"] = 0
        self.builds = []
        self.inflight, self.second_in = threading.Event(), threading.Event()
        def feed(now, live_map):
            self.builds.append("feed")
            self.inflight.set()
            self.second_in.wait(5)                    # the second caller has taken its view signature, next is the lock
            time.sleep(0.2)                           # ...where it now waits
            km._views_dirty[0] = time.time()          # the housekeeping marks the views mid-build: newer than this build's start
            return json.loads(json.dumps(FEED))
        km.build_feed = feed
        self.sigs = [0]
        def sig(now, live_map):                       # a different view signature per caller, so only the rule decides
            self.sigs[0] += 1
            if self.sigs[0] == 2:
                self.second_in.set()
            return ("view", self.sigs[0])
        self.sig = sig
        km._PERF_STATS.reset()

    def tearDown(self):
        (km.build_feed, bf, km._views_dirty[0], vs, km._feed_wire, ws) = self.saved
        km._built_feed[:] = bf
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._wire_stats.update(ws)
        km._PERF_STATS.reset()

    def _pane(self):
        frames = []
        return {"app": "feed", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames}

    def _race(self, first_connect, second_connect):
        """`first` reaches _feed_first on a cold cache and builds; `second` arrives while that build runs. Returns the panes."""
        now = int(time.time())
        a, b = self._pane(), self._pane()
        out = {}
        with mock.patch.object(km, "_task_tracking_on", lambda: True), mock.patch.object(km, "_fleet_view_sig", self.sig):
            ta = threading.Thread(target=lambda: out.__setitem__("a", km._feed_first(now, {}, [a], first_connect)))
            ta.start()
            self.assertTrue(self.inflight.wait(5), "the first caller's build started")
            tb = threading.Thread(target=lambda: out.__setitem__("b", km._feed_first(now, {}, [b], second_connect)))
            tb.start()
            ta.join(20); tb.join(20)
        self.assertEqual(out, {"a": True, "b": True}, "both callers made the early frame")
        return a, b

    def _assert_one_build_both_served(self, a, b):
        self.assertEqual(self.builds, ["feed"], "the waiter served the build it waited for, past the mid-build mark, instead of "
                                                "building the cold feed again: %r" % self.builds)
        for name, pane in (("first", a), ("second", b)):
            self.assertIn("feed", [f.get("type") for f in pane["_frames"]], "the %s caller's pane got the early frame" % name)
        self.assertEqual(km._VIEW_STATS["feedWaited"], 1, "counted as a wait served")
        self.assertEqual(km._wire_stats["feed_first"], 2, "two early frames, one build")
        bs = km._PERF_STATS.snapshot()["builds"]["feed"]
        self.assertEqual((bs["built"], bs["cached"]), (1, 1))

    def test_a_connect_waiting_on_the_pushers_cold_build_serves_it_past_a_dirty_mark(self):
        """The measured order: the pusher builds the cold feed, a pane's connect push arrives during it and waits."""
        self._assert_one_build_both_served(*self._race(False, True))

    def test_the_pusher_waiting_on_a_connects_cold_build_serves_it_too(self):
        """The mirrored order: a pane's connect push builds the cold feed and the pusher's early pass waits for it. The pusher
        serves its feed panes the pre-mark build, so its chat stage starts one cold build sooner, and those panes get the
        post-mark build from the regular section after the chat stage and that rebuild (FeedFirstWholePush reads that
        order). This test pins the early passes only. A fix that only handed
        `connect` through to the cache would still rebuild here: the waiter is the pusher, whose own arm asks under the
        dirty rule."""
        self._assert_one_build_both_served(*self._race(True, False))

    def test_the_pushers_arm_still_rebuilds_for_a_mark_that_landed_during_the_build(self):
        """Freshness is the regular section's, which asks on the pusher's arm (connect False; that it does is pinned in
        tests/test_feed_first_cold_push.py): after the early frame, that arm finds the mark that landed during the build newer
        than its start, and builds. This pins the arm's dirty rule alone, not which arm the regular section asks, so it counts
        the builds of that one ask after the race; the whole push's count is FeedFirstWholePush's."""
        self._race(False, True)
        n = len(self.builds)
        km.build_feed = lambda now, live_map: (self.builds.append("feed"), json.loads(json.dumps(FEED)))[1]
        with mock.patch.object(km, "_task_tracking_on", lambda: True):
            km._cached_feed(int(time.time()), {}, ("view", 1), False)
        self.assertEqual(self.builds[n:], ["feed"], "the pusher's arm rebuilt for the mid-build mark: %r" % self.builds)

    def test_a_cold_early_frame_builds_for_either_caller(self):
        """Alone on a cold cache, either role builds once and sends: this guards a connect arm made serve-only. (The
        2026-09-13 boot's missing early frame came from a cycle-zero guard in _push, which test_feed_first_cold_push's
        later-cycle tests pin.)"""
        self.second_in.set()
        now = int(time.time())
        for connect in (False, True):
            del self.builds[:]
            km._built_feed[:] = [None, None, 0.0, 0.0]
            pane = self._pane()
            with mock.patch.object(km, "_task_tracking_on", lambda: True), mock.patch.object(km, "_fleet_view_sig", self.sig):
                self.assertTrue(km._feed_first(now, {}, [pane], connect), "connect=%s" % connect)
            self.assertEqual(self.builds, ["feed"], "connect=%s built: %r" % (connect, self.builds))
            self.assertIn("feed", [f.get("type") for f in pane["_frames"]], "connect=%s sent" % connect)


TICK = 0.2                                            # each stubbed feed build's hold, once the other caller is in
CHAT_S = 0.15                                         # each stubbed chat tab's build: two tabs make the chat stage
NEEDS_CARD = {"itemId": "g1", "sid": S1, "text": "a card", "category": "needs_input"}   # in the feed board's needs-you category


class FeedFirstWholePush(unittest.TestCase):
    """The whole cold push around the early frame (2026-10-03, the first review of FeedFirstWaiter). Two real _push calls,
    the pusher's (a feed pane and a chat pane) and a pane's connect push (one feed pane), race over the real feed cache and
    its lock: synthetic sessions, a views mark during the first build once the other caller waits, and a sleep as the chat
    stage. Each feed pane's frames are read by build (1 is the build the mark landed in, 2 the next), and builds and cached
    serves are counted over both pushes. The waiter serves the build it waited for, so the second build moves to the
    pusher's regular feed section, after its chat stage, and the count falls only where that section would have rebuilt
    over the waiter's rebuild anyway. Hermetic: build_feed and build_session stubbed; fake panes; a temp state root."""

    ORDERS = ("pusher", "connect")                    # who builds first: the measured order, then the mirrored one

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid in (S1, S2):
            p = os.path.join(self.tmp, sid + ".jsonl"); open(p, "w").write("x" * 100); self.paths[sid] = p
        self._saved = (km._chat_tab_sessions, km._live_map, km.build_session, km.build_feed, km._comments_frame,
                       km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients), list(km._built_feed), km._views_dirty[0],
                       dict(km._VIEW_STATS), km._feed_wire, dict(km._wire_stats), dict(km._BOOT_MARKS),
                       km._feed_needs_input[0], km._feed_needs_input_count[0], km._feed_needs_rows[0],
                       km._NOTIFY_PREV[0], km._NOTIFY_PREV_DISK[0], km._BADGE_LAST[0], km._pusher_wake.is_set())
        km._chat_tab_sessions = lambda now, live_map: [{"sid": s, "name": NAMES[s], "path": self.paths[s], "anchor": s}
                                                       for s in (S1, S2)]
        km._live_map = lambda: {}
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"; km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._BOOT_MARKS["firstServe"] = 1.0                # a kernel that is serving
        km.build_feed, km.build_session = self._feed, self._chat
        self.mark_every, self.moved_sig, self.needs_from = False, False, None

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km.build_session, km.build_feed, km._comments_frame, km._push_subagents,
         km.NAMES, km.jd.STATE, clients, bf, km._views_dirty[0], vs, km._feed_wire, ws, marks,
         km._feed_needs_input[0], km._feed_needs_input_count[0], km._feed_needs_rows[0],
         km._NOTIFY_PREV[0], km._NOTIFY_PREV_DISK[0], km._BADGE_LAST[0], woke) = self._saved
        del km._clients[:]; km._clients.extend(clients)
        km._built_feed[:] = bf
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._wire_stats.update(ws)
        km._BOOT_MARKS.clear(); km._BOOT_MARKS.update(marks)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._CHAT_INFLIGHT.clear()
        if woke:
            km._pusher_wake.set()
        else:
            km._pusher_wake.clear()
        km._PERF_STATS.reset()

    def _feed(self, now, live_map):
        """build_feed: counts, holds until the other caller is in, and marks the views mid-build (the first build, or every
        build with mark_every). Each build's payload differs (`stamp`), so no frame of one build dedups against another's."""
        n = len(self.bids) + 1                            # under the single-flight lock: one build at a time
        self.bids.append(km._feed_build_id[0])            # the id _build_feed_locked claimed for this build just before the call
        self.events.append("feed")
        self.inflight.set()
        self.second_in.wait(5)                            # the other caller has taken its view signature, next is the lock
        time.sleep(TICK)                                  # ...where it now waits
        if n == 1 or self.mark_every:
            km._views_dirty[0] = time.time()              # the housekeeping marks the views mid-build: newer than this build's start
        out = json.loads(json.dumps(FEED))
        out["stamp"] = n
        if self.needs_from is not None and n >= self.needs_from:
            out["asks"] = [dict(NEEDS_CARD)]              # a card that needs the user, in S1's session
        return out

    def _chat(self, sid, now, live_map=None, **kw):
        """build_session, the chat stage: records the three needs-you reads build_session makes (the ring, its count and
        the Needs you box's rows), then sleeps."""
        self.events.append("chat")
        self.reads.append((sid, km._feed_needs_input_of(sid), km._feed_needs_input_count_of(sid),
                           [r.get("itemId") for r in (km._chat_notices(sid) or [])]))
        time.sleep(CHAT_S)
        km._pusher_wake.clear()                           # the early pass's wake: only one after the chat stage counts below
        return {"type": "session", "id": sid, "name": NAMES[sid], "events": [{"kind": "assistant", "uuid": "u1", "md": "m1"}],
                "status": {"state": "working", "sinceEpoch": None}, "ledger": None}

    def _sig(self, now, live_map):
        """The view signature: constant, or a new value per ask with moved_sig. The second ask is the second caller's
        early pass, about to wait on the lock."""
        self.nsig += 1
        if self.nsig == 2:
            self.second_in.set()
        return ("view", self.nsig if self.moved_sig else 0)

    def _patches(self):
        st = contextlib.ExitStack()
        st.enter_context(mock.patch.object(km, "_task_tracking_on", lambda: True))
        st.enter_context(mock.patch.object(km, "_fleet_view_sig", self._sig))
        return st

    def _pane(self, app):
        frames = []
        return {"app": app, "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames}

    def _by_build(self, pane):
        return [self.bids.index(f.get("buildId")) + 1 if f.get("buildId") in self.bids else f.get("buildId")
                for f in pane["_frames"] if f.get("type") == "feed"]

    @staticmethod
    def _pinned(frames):
        """The frames the tests pin, by build: all of the pusher's feed pane's, and the connect pane's first when its
        list is [1] or [1, 2], else its whole list, so the pin fails on anything else. Whether the connect pane also
        gets the post-mark build within these pushes is timing, not this change: its push's regular ask, on the connect
        arm, serves whichever build is stored, and the pusher's rebuild stores about one chat stage and one build after
        the first build, so a connect push slower than that to reach its ask (a loaded runner) gets build 2 too. What
        the change decides is that pane's first frame: the build it waited for, where a waiter that rebuilt sent it the
        post-mark build first in the measured order. A duplicate ([1, 1]) fails: the early frame fills the pane's dedup
        slot, so its push's regular section, serving the same build, sends nothing."""
        connect = frames["connect"]
        return {"pusher": frames["pusher"], "connect": connect[:1] if connect in ([1], [1, 2]) else connect}

    def _run(self, order):
        """One cold boot: the pusher's push (feed pane a, chat pane c) and a pane's connect push (feed pane b) on an empty
        cache, `order` naming the push that builds first. Returns each feed pane's feed frames by build, the whole push's
        (built, cached), and the order of the feed and chat builds."""
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        km._feed_wire = None
        km._VIEW_STATS["feedWaited"] = 0
        km._wire_stats["feed_first"] = 0
        km._feed_needs_input[0] = km._feed_needs_input_count[0] = km._feed_needs_rows[0] = None
        km._NOTIFY_PREV[0] = None
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._CHAT_INFLIGHT.clear()
        km._push_first.clear()                            # no session named ahead of the chat stage's order
        km._PERF_STATS.reset()
        self.events, self.bids, self.reads, self.nsig = [], [], [], 0
        self.inflight, self.second_in = threading.Event(), threading.Event()
        a, b, c = self._pane("feed"), self._pane("feed"), self._pane("chat")
        km._clients[:] = [a, c, b]
        self.panes = (a, b, c)
        pushes = {"pusher": lambda: km._push([a, c]), "connect": lambda: km._push([b], connect=True)}
        first, second = pushes[order], pushes["connect" if order == "pusher" else "pusher"]
        with self._patches():
            t1 = threading.Thread(target=first); t1.start()
            self.assertTrue(self.inflight.wait(5), "the first push's cold build started")
            t2 = threading.Thread(target=second); t2.start()
            t1.join(20); t2.join(20)
        self.assertFalse(t1.is_alive() or t2.is_alive(), "both pushes ended")
        bs = km._PERF_STATS.snapshot()["builds"]["feed"]
        return {"pusher": self._by_build(a), "connect": self._by_build(b)}, (bs["built"], bs["cached"]), list(self.events)

    def _both(self):
        """_run in each order, the measured one first; every test asserts once over both, so a failure shows both."""
        return {order: self._run(order) for order in self.ORDERS}

    def test_one_mid_build_mark_and_a_stable_signature_build_twice_in_either_order(self):
        """The regression guard, green before this change too. With one mark during the first build and an unchanged view
        signature, a waiter that re-checked under the pusher's arm rebuilt and the regular section served that rebuild;
        now the waiter serves and the regular section rebuilds. The second build moves; the count stays at two."""
        runs = self._both()
        self.assertEqual({o: r[1] for o, r in runs.items()}, {o: (2, 2) for o in self.ORDERS},
                         "built, cached over the whole push, by who built first (frames %r)" % {o: r[0] for o, r in runs.items()})

    def test_the_second_build_follows_the_chat_stage_and_each_feed_pane_gets_the_pre_mark_build_first(self):
        """Where the second build goes and the frame order it costs, in both orders. The chat stage starts right after the
        first cold build, the one the mark landed in, and the post-mark build is the regular section's, after it. So the
        pusher's feed pane gets the pre-mark build and then the post-mark build after the chat stage and that rebuild,
        and the connect pane's first frame is the pre-mark build (_pinned). On
        a boot the post-mark build reaches the connect pane in the measured order from
        the next pusher cycle, after the whole first push, its cold chat stage included, so a card move predicted there
        during the first build waits that long for the frame that confirms it, with the pane's 15 s backstop behind it
        (ui/webview/feed.ts; read, not run here). A waiter that rebuilt sent the connect pane the post-mark build first in
        the measured order, and in the mirrored order the pusher's pane, with that rebuild ahead of the chat stage; handing
        the caller's role through to the cache would bring the mirrored one back."""
        runs = self._both()
        self.assertEqual({o: (r[2], self._pinned(r[0])) for o, r in runs.items()},
                         {o: (["feed", "chat", "chat", "feed"], {"pusher": [1, 2], "connect": [1]}) for o in self.ORDERS},
                         "by who built first: the order of the feed and chat builds, and each feed pane's frames by build "
                         "(the connect pane's first, of [1] or [1, 2]; all of them: %r)" % {o: r[0] for o, r in runs.items()})

    def test_a_mark_in_every_build_costs_two_builds_where_a_rebuilding_waiter_made_three(self):
        """The first case where the count falls: a mark lands in every build, so the regular section rebuilds over whatever
        the early passes stored. With the waiter rebuilding too that was built 3, cached 1; now built 2, cached 2 in both
        orders, with the frames of the single mark."""
        self.mark_every = True
        runs = self._both()
        self.assertEqual({o: (r[1], self._pinned(r[0])) for o, r in runs.items()},
                         {o: ((2, 2), {"pusher": [1, 2], "connect": [1]}) for o in self.ORDERS},
                         "by who built first: built, cached over the whole push, and each feed pane's frames by build "
                         "(the connect pane's first, of [1] or [1, 2]; all of them: %r)" % {o: r[0] for o, r in runs.items()})

    def test_a_signature_moved_past_the_floor_costs_two_builds_where_a_rebuilding_waiter_made_three(self):
        """The second case: one mark, a view signature that moves between asks, and a regular ask more than
        REBUILD_MIN_S after the waiter's rebuild finished: a chat stage longer than the floor in the mirrored order, and
        longer than the floor plus that rebuild in the measured order, where the waiter rebuilt during the pusher's chat
        stage (the floor patched to 0 here stands for both). In the mirrored order both compared signatures are the
        pusher's, so only inputs read at the ask move it, such as a transcript append, a state or names write or a judge
        pass. In the measured order the waiter's rebuild carried the connect push's signature, so the two pushes' clocks
        (the 5 s bucket) and liveness reads count too, and a connect push 5 s or more into the first build is always in
        another bucket than the pusher's: there only the timing condition applies. So the regular section rebuilds
        whatever the early passes stored. With the waiter rebuilding too that was built 3, cached 1; now built 2, cached
        2 in both orders."""
        self.moved_sig = True
        with mock.patch.object(km, "REBUILD_MIN_S", 0.0):
            runs = self._both()
        self.assertEqual({o: (r[1], self._pinned(r[0])) for o, r in runs.items()},
                         {o: ((2, 2), {"pusher": [1, 2], "connect": [1]}) for o in self.ORDERS},
                         "by who built first: built, cached over the whole push, and each feed pane's frames by build "
                         "(the connect pane's first, of [1] or [1, 2]; all of them: %r)" % {o: r[0] for o, r in runs.items()})

    def test_the_first_pushs_needs_you_reads_trail_one_build_and_the_rebuild_wakes_the_push_that_catches_up(self):
        """The chat stage's needs-you reads for the first push after a restart, in the mirrored order (where a waiter that
        rebuilt did so before the chat stage). The post-mark build files a card that needs the user in S1's session; the
        first build has none. The chat stage runs before the post-mark build, so the ring, its count and the Needs you
        box's rows read the pre-mark build. The regular section's rebuild then moves the needs-you set, which wakes the
        pusher (_build_feed_locked), and the push that wake brings reads the post-mark set."""
        self.needs_from = 2
        self._run("connect")
        self.assertEqual(sorted(self.reads), [(S1, False, 0, []), (S2, False, 0, [])], "the first push's chat stage read the pre-mark build")
        self.assertTrue(km._pusher_wake.is_set(), "the regular section's rebuild moved the needs-you set and woke the pusher")
        a, _b, c = self.panes
        del self.reads[:]
        with self._patches():
            km._push([a, c])                              # the cycle that wake brings
        self.assertIn((S1, True, 1, ["g1"]), self.reads, "its chat stage rebuilt S1's tab on the post-mark set: %r" % self.reads)

    def test_a_rebuild_that_leaves_the_needs_you_set_alone_wakes_nothing_after_the_chat_stage(self):
        """The control for the wake above: both builds carry the card, so the regular section's rebuild after the chat
        stage moves no needs-you read and nothing after the chat stage wakes the pusher. The wake is the set's change,
        not the push's. The rebuild's place is asserted (feed, chat, chat, feed): a second build before the chat stage
        (a waiter that rebuilt) would leave the wake check below nothing to catch, since the chat stub clears the wake
        at each chat build."""
        self.needs_from = 1
        self._run("connect")
        self.assertEqual(sorted(self.reads), [(S1, True, 1, ["g1"]), (S2, False, 0, [])], "the chat stage read the card")
        self.assertEqual(self.events, ["feed", "chat", "chat", "feed"],
                         "the regular section rebuilt after the chat stage: %r" % self.events)
        self.assertFalse(km._pusher_wake.is_set(), "no needs-you change after the chat stage, no wake")


class ChatClaimRelease(unittest.TestCase):
    """Round two, medium 1: a raise between the claim and the store must not leave the tab claimed for the kernel's life."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid in (S1, S2):
            p = os.path.join(self.tmp, sid + ".jsonl"); open(p, "w").write("x" * 100); self.paths[sid] = p
        self._saved = (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame,
                       km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients), km._send_chat_or_status)
        km._chat_tab_sessions = lambda now, live_map: [{"sid": s, "name": NAMES[s], "path": self.paths[s], "anchor": s} for s in (S1, S2)]
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None
        self.builds = []
        def build(sid, now, live_map=None, **kw):
            self.builds.append(sid)
            return {"type": "session", "id": sid, "name": NAMES[sid],
                    "events": [{"kind": "assistant", "uuid": "u1", "md": "m1"}], "status": {"state": "working", "sinceEpoch": None}, "ledger": None}
        km.build_session = build
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"; km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        del km._clients[:]
        km._CHAT_INFLIGHT.clear()

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame, km._push_subagents,
         km.NAMES, km.jd.STATE, clients, km._send_chat_or_status) = self._saved
        del km._clients[:]; km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._CHAT_INFLIGHT.clear()

    def _client(self):
        return {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None, "ready": True, "proto": 2, "active": S1}

    def test_a_raise_after_the_build_releases_the_claim_and_the_next_push_builds_at_once(self):
        c = self._client(); km._clients[:] = [c]
        n = [0]
        real = km._send_chat_or_status
        def boom(cl, m, ms, change_from, led_changed):
            n[0] += 1
            if n[0] == 1:
                raise RuntimeError("wire gone")            # a post-build step in the claimed span raises
            return real(cl, m, ms, change_from, led_changed)
        km._send_chat_or_status = boom
        with mock.patch.object(km.sys, "stderr", mock.Mock()):
            km._push([c])                                    # the cycle's catch swallows it; the scope close releases the claim
        self.assertEqual(km._CHAT_INFLIGHT, {}, "no claim survives the raise")
        del self.builds[:]
        km._built_chat.clear()
        with mock.patch.object(km, "CHAT_INFLIGHT_WAIT_S", 2.0):
            t0 = time.monotonic(); km._push([c]); dt = time.monotonic() - t0
        self.assertEqual(sorted(self.builds), [S1, S2], "the next push builds")
        self.assertLess(dt, 1.0, "at once, not after the bound: %.2f s" % dt)

    def test_a_stale_claim_is_replaced_and_its_waiters_released(self):
        c = self._client(); km._clients[:] = [c]
        stale = threading.Event()
        with mock.patch.object(km, "CHAT_INFLIGHT_WAIT_S", 0.5):
            km._CHAT_INFLIGHT[S1] = (stale, time.monotonic() - 5.0)   # an entry older than the bound: its builder is gone
            t0 = time.monotonic(); km._push([c]); dt = time.monotonic() - t0
        self.assertIn(S1, self.builds, "built")
        self.assertLess(dt, 0.4, "without waiting the bound: %.2f s" % dt)
        self.assertTrue(stale.is_set(), "whoever waited on the stale entry was released")
        self.assertEqual(km._CHAT_INFLIGHT, {})

    def test_the_bound_is_near_the_per_tab_worst_case(self):
        self.assertEqual(km.CHAT_INFLIGHT_WAIT_S, 30.0)


class ChatTabSingleFlight(unittest.TestCase):
    """The pusher's push and a connect push building the same cold tab at once: one build, the second serves the cache."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for sid in (S1, S2):
            p = os.path.join(self.tmp, sid + ".jsonl"); open(p, "w").write("x" * 100); self.paths[sid] = p
        self._saved = (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame,
                       km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients), dict(km._VIEW_STATS))
        km._chat_tab_sessions = lambda now, live_map: [{"sid": s, "name": NAMES[s], "path": self.paths[s], "anchor": s} for s in (S1, S2)]
        km._live_map = lambda: {}
        km._cached_feed = lambda *a, **k: None
        self.builds = []
        self.lock = threading.Lock()
        def build(sid, now, live_map=None, **kw):
            with self.lock:
                self.builds.append(sid)
            time.sleep(0.4)
            return {"type": "session", "id": sid, "name": NAMES[sid],
                    "events": [{"kind": "assistant", "uuid": "u1", "md": "m1"}], "status": {"state": "working", "sinceEpoch": None}, "ledger": None}
        km.build_session = build
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"; km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        del km._clients[:]
        km._CHAT_INFLIGHT.clear()

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame, km._push_subagents,
         km.NAMES, km.jd.STATE, clients, vs) = self._saved
        del km._clients[:]; km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._CHAT_INFLIGHT.clear()

    def _client(self, **kw):
        frames = []
        c = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames,
             "ready": True, "proto": 2}
        c.update(kw)
        return c

    def test_a_connect_push_racing_the_cycle_builds_each_tab_once(self):
        a, b = self._client(active=S1), self._client(active=S1)
        km._clients[:] = [a, b]
        w0 = km._VIEW_STATS["chatWaited"]
        _race_fns = [lambda: km._push([a, b]), lambda: km._push([b], connect=True)]
        go = threading.Event()
        def run(i):
            go.wait(); _race_fns[i]()
        ths = [threading.Thread(target=run, args=(i,)) for i in range(2)]
        for t in ths: t.start()
        go.set()
        for t in ths: t.join(30)
        self.assertEqual(sorted(self.builds), [S1, S2], "each tab built once across the two pushes: %r" % self.builds)
        self.assertGreaterEqual(km._VIEW_STATS["chatWaited"] - w0, 1, "the later push waited and served the cache")
        self.assertEqual({f["id"] for f in b["_frames"] if f["type"] == "session"}, {S1, S2}, "the connect client still got both tabs")
        self.assertEqual(km._CHAT_INFLIGHT, {}, "no claim left behind")

    def test_a_failing_build_releases_its_waiters(self):
        a = self._client(active=S1)
        km._clients[:] = [a]
        def boom(sid, now, live_map=None, **kw):
            with self.lock:
                self.builds.append(sid)
            time.sleep(0.2); raise RuntimeError("read failed")
        km.build_session = boom
        with mock.patch.object(km, "CHAT_INFLIGHT_WAIT_S", 5.0), mock.patch.object(km.sys, "stderr", mock.Mock()):
            t0 = time.monotonic()
            go = threading.Event()
            def run():
                go.wait(); km._push([a])
            ths = [threading.Thread(target=run) for _ in range(2)]
            for t in ths: t.start()
            go.set()
            for t in ths: t.join(30)
            dt = time.monotonic() - t0
        self.assertLess(dt, 4.0, "the waiter was released by the failing builder, not by the bound: %.1f s" % dt)
        self.assertEqual(km._CHAT_INFLIGHT, {}, "no claim left behind")


if __name__ == "__main__":
    unittest.main()
