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
import inspect
import json
import os
import tempfile
import threading
import time
import unittest
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


class FeedAndTimelineSingleFlight(unittest.TestCase):
    def setUp(self):
        self.saved = (km.build_feed, km.build_timeline, list(km._built_feed), list(km._built_timeline), km._views_dirty[0],
                      dict(km._VIEW_STATS))
        km._built_feed[:] = [None, None, 0.0, 0.0]
        km._built_timeline[:] = [None, None, 0.0, 0.0]
        km._views_dirty[0] = 0.0
        self.builds = []
        def feed(now, live_map):
            self.builds.append("feed"); time.sleep(0.3); return json.loads(json.dumps(FEED))
        def timeline(now, live_map, with_bars=True, live_only=False):
            self.builds.append("tl:%s" % ("live" if live_only else "full")); time.sleep(0.3)
            return {"lanes": [], "turns": {}, "judging": {}, "messages": [], "now": now}
        km.build_feed, km.build_timeline = feed, timeline
        km._PERF_STATS.reset()

    def tearDown(self):
        (km.build_feed, km.build_timeline, bf, bt, km._views_dirty[0], vs) = self.saved
        km._built_feed[:] = bf; km._built_timeline[:] = bt
        km._VIEW_STATS.clear(); km._VIEW_STATS.update(vs)
        km._PERF_STATS.reset()

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

    def test_two_cold_timeline_callers_build_once(self):
        now = int(time.time())
        got = _race(lambda: km._cached_timeline(now, {}, ("sig",), False))
        self.assertEqual(self.builds, ["tl:full"], self.builds)
        self.assertTrue(all(g and "lanes" in g for g in got))
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
        self.assertIn("with _TL_BUILD_LOCK:", inspect.getsource(km._cached_timeline))
        push = inspect.getsource(km._push)
        self.assertNotIn("with _TL_BUILD_LOCK:", push, "the connect path's live-only timeline runs outside any lock (round two, medium 2)")
        self.assertIn("_chat_inflight_release_all()", inspect.getsource(km._chat_push_scopes_close),
                      "the thread's scope close releases the claims a raise left (round two, medium 1)")

    def test_two_cold_connect_timeline_pushes_run_side_by_side(self):
        """Round two, medium 2: the first cut held the full build's lock across the connect path's live-only builds, so the second
        pane's first frame waited for the first pane's, and a connect that won the lock blocked the pusher's timeline stage."""
        now = int(time.time())
        frames = {0: [], 1: []}
        def client(i):
            return {"app": "timeline", "alive": True, "sent": {}, "send": lambda s, i=i: frames[i].append(time.monotonic())}
        saved = (km._live_map, km._cached_feed, km._chat_tab_sessions, list(km._clients))
        km._live_map = lambda: {}; km._cached_feed = lambda *a, **k: None; km._chat_tab_sessions = lambda now, live_map: []
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
        self.assertEqual(self.builds.count("tl:live"), 4, "the two partial builds per connect, as before the change: %r" % self.builds)


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
        post-mark build from the regular section after the chat stage, that rebuild and, with a timeline pane, the timeline
        stage (FeedFirstWholePush reads that order). The feed-send change, PR 101, sends the feed frame at the end of
        the feed section, before the timeline section, so that part of the delay goes with it; whichever of the two
        lands second drops the timeline clause here. This test pins the early passes only. A fix that only handed
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
        pusher's feed pane gets the pre-mark build and then the post-mark build after the chat stage and that rebuild
        (and, with a timeline pane, which these pushes have not, after the timeline stage too, until the feed-send
        change, PR 101, sends the feed frame at the end of the feed section, before the timeline section; whichever of
        the two lands second drops this clause), and the connect pane's first frame is the pre-mark build (_pinned). On
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
