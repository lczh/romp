#!/usr/bin/env python3
"""A tab nobody is watching is not rebuilt (2026-11-15): the warm half of the cold-tab gate (tests/test_cold_tab_gate.py).

Measured on a machine with 85 live sessions and one page connected: 386 chat builds in thirteen minutes, 364 of them for
tabs no page was showing, 702 of the pusher's 802 seconds, cycles of one to eight minutes, with the tab on screen waiting
for the cycle to end. The cold gate skips a tab no kernel life has built; once a page had loaded a tab (its click, its
idle prefetch) the tab was rebuilt on every cycle its transcript moved, watched or not. Now a tab that no connected chat
client names active and none has asked for whole, whose build is owed, is handed back to every client that holds it as a
skeleton (a status frame carrying `rest`), the cached build and the shared delta baseline are forgotten, and from then on
it is the cold gate's tab: a light status per cycle, the Outline's provisional row, and a build at once when a page asks.

Drives the REAL _push, _push_session_now and Handler._dispatch_ws over fake clients with build_session stubbed and counted,
as the gate's own test does; real temp transcript files (their stat is the build signature's transcript component, so an
append is the move). Synthetic only: the notes-api demo world, invented session ids of this module's own."""
import ast
import contextlib
import inspect
import io
import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
SRC = open(os.path.join(BIN, "romp-kernel")).read()
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")                       # a minted state root pins the hosts off (the 2026-09-11 rule)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_unwatched_tabs", os.path.join(BIN, "romp-kernel"))

S1 = "11111111-2222-3333-4444-121212121211"   # web: the tab the page is looking at
S2 = "11111111-2222-3333-4444-121212121212"   # api: the tab nobody is looking at, whose transcript moves
S3 = "11111111-2222-3333-4444-121212121213"   # tests: a second unwatched tab, which stays still
NAMES = {S1: "web", S2: "api", S3: "tests"}
TAB_ORDER = [S1, S2, S3]
FEED = {"type": "feed", "items": [], "asks": [], "working": [], "awaiting": [], "stateUnknown": [], "order": [], "sessions": [],
        "hosts": [], "pendingHosts": [], "pendingDead": []}


def _sess(sid, n, state):
    """A synthetic build_session payload of n events (well under the wire's tail, so a full send is the whole list)."""
    return {"type": "session", "id": sid, "name": NAMES[sid],
            "events": [{"kind": "assistant", "uuid": "%s-u%d" % (sid[-1], i), "md": "m%d" % i} for i in range(n)],
            "status": {"state": state, "sinceEpoch": None},
            "ledger": {"summary": "", "tree": [], "current": None, "recent": [], "workingNote": "", "needsInput": None}}


class _Self:
    """The handler's `self` for _dispatch_ws: a needFull reaches _push_one, whose real body is this push."""
    def _push_one(self, client, only=None):
        km._push([client], connect=True, only=only)


def _fake_self(path):
    """A connect handler with a peer that closes at once (tests/test_chat_skeleton_reconnect.py's shape)."""
    class FakeSelf:
        headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
        rfile = io.BytesIO(); wfile = io.BytesIO()
        connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
        close_connection = False
        def send_response(self, *a): pass
        def send_header(self, *a): pass
        def end_headers(self): pass
    FakeSelf.path = path
    return FakeSelf()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths, self.n = {}, {S1: 5, S2: 7, S3: 3}
        for sid in TAB_ORDER:
            p = os.path.join(self.tmp, sid + ".jsonl")
            with open(p, "w") as f:
                f.write("x" * 100)
            self.paths[sid] = p
        self._saved = (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame,
                       km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients))
        km._chat_tab_sessions = lambda now, live_map: [
            {"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in TAB_ORDER]
        # a live row per session: a tab at rest states its status from the row, and without one the gate builds
        self.live = {sid: {"state": "working", "since": 1781100000, "model": "", "effort": "", "mode": "", "backend": "sdk"}
                     for sid in TAB_ORDER}
        km._live_map = lambda: dict(self.live)
        km._cached_feed = lambda *a, **k: json.loads(json.dumps(FEED))
        self.built = []

        def build(sid, now, live_map=None, **kw):
            self.built.append(sid)
            return _sess(sid, self.n[sid], (self.live.get(sid) or {}).get("state", "waiting"))
        km.build_session = build
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        state = Path(self.tmp) / "state"; state.mkdir(parents=True, exist_ok=True)
        km.jd._rebind_state(state)                     # the goal directory follows (the provisional row reads the store)
        (state / "session-hosts").write_text("off\n")
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear(); km._chat_baseline_raced.clear()
        getattr(km, "_chat_rest_retried", set()).clear()
        if hasattr(km, "_prov_ledger_memo"):
            km._prov_ledger_memo.clear()
        del km._clients[:]
        km._pusher_wake.clear()
        km._PERF_STATS.reset()

    def tearDown(self):
        (km._chat_tab_sessions, km._live_map, km._cached_feed, km.build_session, km._comments_frame,
         km._push_subagents, km.NAMES, state, clients) = self._saved
        km.jd._rebind_state(state)
        del km._clients[:]; km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear(); km._chat_baseline_raced.clear()
        km._PERF_STATS.reset()

    def _client(self, app="chat", **kw):
        """A page past its ready on the uuid wire that said it can take a loaded tab back (`rest=1` at its dial), unless
        the test says otherwise. No diet: its first push hands it every tab whole, the state a prefetch leaves."""
        frames = []
        c = {"app": app, "alive": True, "sent": {}, "send": lambda s: frames.append(json.loads(s)), "_frames": frames}
        if app == "chat":
            c.update(ready=True, proto=2, restTabs=True)
        c.update(kw)
        return c

    @staticmethod
    def _frames(c, typ, sid=None):
        return [f for f in c["_frames"] if f["type"] == typ and (sid is None or f.get("id") == sid)]

    def _move(self, sid, by=1):
        """The session wrote to its transcript: the file grows (the signature's transcript component) and the next build
        has more events."""
        with open(self.paths[sid], "a") as f:
            f.write("y" * 50)
        self.n[sid] += by

    def _chat(self):
        return km._PERF_STATS.snapshot()["builds"]["chat"]

    def _warm(self, *clients):
        """The first push: every tab built and handed whole to every client (a page that dialed no diet holds no set and
        is served whole, as before). The counters are reset behind it, so each test reads its own."""
        km._clients[:] = list(clients)
        km._push(list(clients))
        self.assertEqual(sorted(self.built), sorted(TAB_ORDER))
        for c in clients:
            if c["app"] == "chat":
                self.assertEqual(sorted(f["id"] for f in self._frames(c, "session")), sorted(TAB_ORDER))
                c["_frames"].clear()
        del self.built[:]
        km._PERF_STATS.reset()


    def _warm_second(self, joining, *others):
        """A page that joins a warm kernel: its own connect push hands it every tab whole."""
        km._clients[:] = list(others) + [joining]
        km._push([joining], connect=True)
        joining["_frames"].clear()
        for c in others:
            c["_frames"].clear()
        del self.built[:]
        km._PERF_STATS.reset()


class UnwatchedTabs(_Base):
    def test_01_an_unwatched_tab_whose_transcript_moves_is_not_built_and_its_status_still_updates(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [], "nobody is watching api: no chat build for it (web did not move: served)")
        rest = self._frames(c, "status", S2)
        self.assertEqual(len(rest), 1, "one status frame for the tab: %r" % c["_frames"])
        self.assertIs(rest[0].get("rest"), True, "the frame says the page's copy went behind")
        self.assertEqual(rest[0]["status"]["state"], "working", "the live row's word")
        self.assertTrue(rest[0]["status"].get("provisional"))
        self.assertEqual(self._frames(c, "session", S2) + self._frames(c, "chatTail", S2), [], "no chat frame for it")
        self.assertNotIn(S2, c.get("echat") or {}, "the kernel no longer believes the page holds a base for it")
        self.assertIn(S2, c["skeleton"])
        self.assertNotIn(S2, km._built_chat, "the cached build is forgotten")
        self.assertNotIn(S2, km._prev_chat_events, "and the shared delta baseline with it")
        self.assertEqual((self._chat()["rested"], self._chat()["bg_built"]), (1, 0))
        # the next cycles: the strip lists it as a skeleton, nothing is built however often the transcript moves, and
        # the light status follows the session (working, then idle)
        c["_frames"].clear()
        self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [])
        self.assertEqual(self._frames(c, "tabOrder")[-1]["skeleton"], [S2])
        self.assertEqual(self._frames(c, "status", S2), [], "an unchanged status is deduped against the frame that rested the tab")
        self.live[S2]["state"] = "waiting"
        self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [])
        st = self._frames(c, "status", S2)
        self.assertEqual([f["status"]["state"] for f in st], ["ready"], "the chip follows the row without a build")
        self.assertNotIn("rest", st[0], "only the frame that takes the tab back carries the mark")
        self.assertEqual(self._chat()["rested"], 1, "rested once; the later pushes are the cold gate's skips")
        self.assertEqual(self._chat()["coldSkipped"], 2)

    def test_02_an_unwatched_tab_that_did_not_move_is_served_from_its_cached_build_and_stays_loaded(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S1)                                       # only the watched tab moves
        km._push([c])
        self.assertEqual(self.built, [S1], "the watched tab is rebuilt")
        self.assertEqual(self._frames(c, "status"), [], "no tab went to rest: no new information about the others")
        self.assertIn(S2, c["echat"]); self.assertIn(S3, c["echat"])
        self.assertEqual(self._chat()["rested"], 0)
        self.assertTrue(self._frames(c, "chatTail", S1), "and the watched tab's change goes as a delta")

    def test_03_activating_a_resting_tab_builds_it_once_at_once_and_sends_a_full_frame(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])                                        # api goes to rest
        c["_frames"].clear()
        self._move(S2)                                       # …and keeps moving while nobody looks
        # the page's click: activeTab, then the needFull its loader sends (render.ts showActive's skeleton branch)
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "skeleton-click"}, c)
        self.assertEqual(self.built, [S2], "built once, by the ask's own push, not by a cycle")
        full = self._frames(c, "session", S2)
        self.assertEqual(len(full), 1, "a FULL frame: %r" % [f["type"] for f in c["_frames"]])
        self.assertEqual(self._frames(c, "chatTail", S2), [], "never a delta against the base from before the rest")
        self.assertEqual(len(full[0]["events"]), self.n[S2], "the build of the transcript as it stands now")
        self.assertNotIn(S2, c["skeleton"]); self.assertIn(S2, c["echat"])
        self.assertEqual(km._prev_chat_events.get(S2), full[0]["events"], "the full establishes the baseline again")
        # the cycle the click woke serves the same build from the cache: no second build, no second frame
        del self.built[:]; c["_frames"].clear()
        km._push([c])
        self.assertEqual(self.built, [])
        self.assertEqual(self._frames(c, "session", S2) + self._frames(c, "chatTail", S2), [])

    def test_04_the_delta_stream_resumes_from_the_fresh_base_after_a_return(self):
        c = self._client(active=S1)
        self._warm(c)
        before = self.n[S2]
        self._move(S2, by=4)
        km._push([c])                                        # at rest, four events behind
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "skeleton-click"}, c)
        km._push([c])
        c["_frames"].clear(); del self.built[:]
        self._move(S2, by=2)                                 # watched now: the next change is a delta
        km._push([c])
        self.assertEqual(self.built, [S2])
        tails = self._frames(c, "chatTail", S2)
        self.assertEqual(len(tails), 1, [f["type"] for f in c["_frames"]])
        self.assertEqual(self._frames(c, "session", S2), [])
        sent = [e["uuid"] for e in tails[0]["events"]]
        self.assertEqual(sent, ["%s-u%d" % (S2[-1], i) for i in range(before + 4, before + 6)],
                         "the suffix past what the full handed over: nothing from before the rest is re-sent or skipped")

    def test_05_two_pages_watching_different_tabs_get_both_built(self):
        a, b = self._client(active=S1), self._client(active=S2)   # a laptop and a phone, each on its own tab
        self._warm(a, b)
        self._move(S1); self._move(S2); self._move(S3)
        km._push([a, b])
        self.assertEqual(sorted(self.built), [S1, S2], "both watched tabs are built; the one neither watches is not")
        for c in (a, b):
            self.assertTrue(self._frames(c, "chatTail", S1), "a base holder of a watched tab still gets its delta")
            self.assertTrue(self._frames(c, "chatTail", S2))
            self.assertEqual([f.get("rest") for f in self._frames(c, "status", S3)], [True])
        self.assertEqual(self._chat()["rested"], 1)

    def test_06_a_page_disconnecting_makes_its_tab_unwatched(self):
        a, b = self._client(active=S1), self._client(active=S2)
        self._warm(a, b)
        self._move(S2)
        km._push([a, b])
        self.assertEqual(self.built, [S2], "b is watching api: built")
        del self.built[:]; a["_frames"].clear()
        km._clients[:] = [a]                                 # b's socket ended
        self._move(S2)
        km._push([a])
        self.assertEqual(self.built, [], "nobody connected watches api any more")
        self.assertEqual([f.get("rest") for f in self._frames(a, "status", S2)], [True], "a held it whole: it is handed back")

    def test_07_a_client_that_cannot_take_a_tab_back_keeps_it_built_and_perf_says_why(self):
        old = self._client(active=S1, restTabs=False)        # a bundle from before the capability, holding every tab whole
        self._warm(old)
        self._move(S2)
        km._push([old])
        self.assertEqual(self.built, [S2], "built as before")
        self.assertTrue(self._frames(old, "chatTail", S2), "and its delta goes as before")
        self.assertEqual(self._frames(old, "status"), [])
        self.assertEqual((self._chat()["rested"], self._chat()["restHeld"]), (0, {"noCap": 1}))
        # one such page among capable ones holds the tab for all of them: nobody is told a tab rests while it is built
        new = self._client(active=S1)
        self._warm_second(new, old)
        self._move(S2)
        km._push([old, new])
        self.assertEqual(self.built, [S2])
        self.assertEqual(self._frames(new, "status"), [])
        self.assertIn(S2, new["echat"])

    def test_08_a_prefetch_ask_builds_the_tab_once_and_the_kernel_never_builds_it_again_unasked(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])                                        # at rest
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "prefetch"}, c)   # the idle chain's one ask
        self.assertEqual(self.built, [S2])
        self.assertEqual(len(self._frames(c, "session", S2)), 1)
        self.assertEqual(c.get("active"), S1, "a prefetch does not make the tab watched")
        del self.built[:]; c["_frames"].clear()
        for _ in range(4):                                   # the session keeps working; no page asks again
            self._move(S2)
            km._push([c])
        self.assertEqual(self.built, [], "four moves, four cycles, no build: only an ask builds a tab nobody watches")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True], "handed back once, at its first move")
        self.assertEqual(self._chat()["restHeld"], {"asked": 1}, "the ask's own push is the one build /perf attributes")

    def test_09_the_outline_gets_a_provisional_row_for_a_resting_tab_and_an_older_outline_keeps_the_builds(self):
        c = self._client(active=S1)
        outline = self._client(app="fleet", provRows=True)
        self._warm(c, outline)
        outline["_frames"].clear()
        self._move(S2)
        km._push([c, outline])
        self.assertEqual(self.built, [])
        rows = {r["sid"]: r for r in self._frames(outline, "feed")[-1]["ledgers"]}
        self.assertEqual(sorted(rows), sorted(TAB_ORDER), "the row stays in the pane")
        self.assertIs(rows[S2].get("provisional"), True)
        self.assertEqual(rows[S2]["status"]["state"], "working")
        self.assertNotIn("provisional", rows[S1]); self.assertNotIn("provisional", rows[S3])
        # an Outline that never said it draws a provisional row needs the built one: nothing rests while it is connected
        plain = self._client(app="fleet")
        km._clients[:] = [c, outline, plain]
        self._move(S3)
        km._push([c, outline, plain])
        self.assertEqual(self.built, [S2, S3], "both unwatched tabs are built for its rows: %r" % self.built)
        self.assertEqual(self._chat()["restHeld"], {"plainOutline": 2}, "the resting tab and the one that just moved")
        self.assertNotIn("provisional", {r["sid"]: r for r in self._frames(plain, "feed")[-1]["ledgers"]}[S2])

    def test_10_each_page_that_held_a_resting_tab_gets_its_own_full_on_its_own_return(self):
        a, b = self._client(active=S1), self._client(active=S3)
        self._warm(a, b)
        self._move(S2)
        km._push([a, b])
        self.assertEqual([[f.get("rest") for f in self._frames(c, "status", S2)] for c in (a, b)], [[True], [True]])
        a["_frames"].clear(); b["_frames"].clear()
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, a)
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "skeleton-click"}, a)
        self.assertEqual(len(self._frames(a, "session", S2)), 1)
        self._move(S2)
        km._push([a, b])                                     # watched by a: built; b still holds it as a skeleton
        self.assertEqual(self._frames(b, "session", S2) + self._frames(b, "chatTail", S2), [], "b did not ask: no chat frame")
        self.assertTrue(self._frames(b, "status", S2), "its chip follows the built status")
        self.assertNotIn(S2, b.get("echat") or {})
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "prefetch"}, b)
        full = self._frames(b, "session", S2)
        self.assertEqual(len(full), 1, "b's own ask is answered with a full, whatever a holds")
        self.assertEqual(len(full[0]["events"]), self.n[S2])

    def test_11_a_click_or_an_ask_that_lands_first_keeps_the_tab_from_resting(self):
        c = self._client(active=S1)
        self._warm(c)
        light = km._light_status(S2, self.paths[S2], self.live[S2], int(time.time()))
        c["active"] = S2                                     # the click's activeTab landed after the loop read every active
        self.assertEqual(km._rest_unwatched(S2, [c], light), "watched")
        c["active"] = S1
        c.setdefault("askedFull", set()).add(S2)             # a needFull whose answer is on its way
        self.assertEqual(km._rest_unwatched(S2, [c], light), "asked")
        self.assertIn(S2, c["echat"], "nothing was taken from the client either time")
        self.assertEqual(self._frames(c, "status"), [])
        c["askedFull"].discard(S2)
        self.assertEqual(km._rest_unwatched(S2, [], light), "noClient", "no chat page connected: the cold gate's own rule")
        self.assertIsNone(km._rest_unwatched(S2, [c], light))
        self.assertIsNone(km._rest_unwatched(S2, [c], light), "a tab already at rest stays, and no second frame goes")
        self.assertEqual(len(self._frames(c, "status", S2)), 1)

    def test_16_a_page_that_names_no_tab_keeps_what_it_holds(self):
        # a first load with no saved tab: the dial carries no active hint, the page's first report is an activeTab with no
        # id (its first paint runs before a session has landed), and the report naming the tab follows. The kernel cannot
        # know which tab is on screen until then, so none is taken back: taken back in that moment, the tab the reader was
        # on went to the loader and was fetched a second time
        c = self._client()
        self.assertNotIn("active", c)
        self._warm(c)
        for report in (None, {"type": "activeTab", "id": None}, {"type": "activeTab", "id": ""}):
            if report is not None:
                km.Handler._dispatch_ws(_Self(), report, c)
            del self.built[:]; km._PERF_STATS.reset()
            self._move(S1); self._move(S2)
            km._push([c])
            self.assertEqual(sorted(self.built), [S1, S2], "both built, as before: %r" % (report,))
            self.assertEqual(self._frames(c, "status"), [])
            self.assertEqual(self._chat()["restHeld"], {"unreported": 2})
        del self.built[:]; c["_frames"].clear()
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S1}, c)   # the page names its tab
        self._move(S1); self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [S1], "it watches web: api rests")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True])
        # a linked machine's relay names an active only while the page's tab lives here: for it none named is the report
        relay = self._client(kind="relay")
        self._warm_second(relay, c)
        del self.built[:]
        self._move(S3)
        km._push([c, relay])
        self.assertEqual(self.built, [], "neither the page nor the relay watches tests")
        self.assertEqual([f.get("rest") for f in self._frames(relay, "status", S3)], [True])

    def test_17_a_tab_with_no_cached_build_gets_one_build_and_rests_if_that_one_cannot_be_cached_either(self):
        # the page holds api whole from a build nothing recorded (an ask's own push whose inputs moved under it, a
        # per-session push): there is no signature for it to have moved past, so one build says where it stands. A
        # session's very first build is the common case: it mints the goal store and so moves its own signature, and
        # without this build every tab the idle prefetch had just filled went straight back to rest
        c = self._client(active=S1)
        self._warm(c)
        km._built_chat.pop(S2)                               # the build the page's copy came from was never cached
        km._push([c])
        self.assertEqual(self.built, [S2], "built once, though nobody watches it and nothing moved")
        self.assertEqual(self._frames(c, "status"), [], "and it stays a loaded tab")
        self.assertIn(S2, c["echat"]); self.assertIn(S2, km._built_chat)
        self.assertEqual(self._chat()["restHeld"], {"uncached": 1})
        del self.built[:]
        km._push([c])
        self.assertEqual(self.built, [], "cached now: served, as any unwatched tab whose signature holds")
        # a session that keeps moving under its builds: the one build cannot be cached either, and the tab rests
        km._built_chat.pop(S2)
        real = km.build_session

        def moving(sid, now, live_map=None, **kw):
            m = real(sid, now, live_map, **kw)
            if sid == S2:
                self._move(S2)                               # the transcript grew while the build ran
            return m
        km.build_session = moving
        km._push([c])
        self.assertEqual(self.built, [S2], "its one build")
        self.assertNotIn(S2, km._built_chat, "which could not be cached: an input moved under it")
        self.assertEqual(self._frames(c, "status"), [])
        del self.built[:]
        km._push([c])
        self.assertEqual(self.built, [], "no second build for a tab nobody watches")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True], "it is taken back instead")
        self.assertEqual(self._chat()["restHeld"], {"uncached": 2})
        self.assertNotIn(S2, km._chat_rest_retried, "the mark goes with the rest")

    def test_12_the_per_session_push_states_a_resting_tabs_status_and_builds_nothing(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])
        c["_frames"].clear()
        self.live[S2]["state"] = "waiting"
        km._push_session_now(S2)                             # a per-session event for a tab at rest (an attach handshake)
        self.assertEqual(self.built, [])
        self.assertEqual([f["status"]["state"] for f in self._frames(c, "status", S2)], ["ready"])

    def test_13_a_tab_with_no_live_row_or_no_transcript_is_built_as_before(self):
        c = self._client(active=S1)
        self._warm(c)
        self.live.pop(S2)                                    # no row: the kernel cannot state its status without a build
        self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [S2])
        self.assertEqual(self._chat()["restHeld"], {"noLiveRow": 1})

    def test_15_a_warm_tab_every_page_holds_as_a_skeleton_is_not_rebuilt_for_its_status(self):
        # a redial onto a warm kernel: the page holds every tab but its own as a skeleton, and a tab that moved was
        # rebuilt on every cycle only to state its status to a page that draws nothing else of it
        first = self._client(active=S1)
        self._warm(first)
        c = self._client(reconnect=True, active=S1, restTabs=False)   # the diet alone says enough: nothing whole is held
        km._clients[:] = [c]
        km._push([c])
        self.assertEqual(self.built, [], "served from the cache, as before")
        c["_frames"].clear()
        self._move(S2)
        km._push([c])
        self.assertEqual(self.built, [], "a skeleton to every page, its build owed: not built")
        st = self._frames(c, "status", S2)
        self.assertEqual([f["status"]["state"] for f in st], ["working"], "the status goes on, from the live row")
        self.assertNotIn("rest", st[0], "nothing was taken back: the page never held it whole on this socket")
        self.assertEqual(self._chat()["rested"], 1)

    def test_14_the_handshake_reads_the_flag_for_the_chat_app_only_and_the_dials_carry_it(self):
        for path, expect in (("/ws?app=chat&delta=1&iid=page-7&rest=1", True), ("/ws?app=chat&delta=1&iid=page-7", False),
                             ("/ws?app=feed&delta=1&iid=page-7&rest=1", False)):
            got = []
            real_reg, real_recv = km._register_ws_client, km._ws_recv
            km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
            km._ws_recv = lambda rfile: (0x8, b"", True)
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    km.Handler._ws(_fake_self(path))
            finally:
                km._register_ws_client, km._ws_recv = real_reg, real_recv
                for c in got:
                    if c in km._clients:
                        km._clients.remove(c)
            self.assertEqual(len(got), 1, path)
            self.assertEqual(bool(got[0].get("restTabs")), expect, path)
        self.assertIn('(APP==="chat"?"&rest=1":"")', SRC, "the pane shim's dial carries the term for the chat app only")
        # …and the phone's chat page is the desktop's: one page, served with the phone's layout inside it, on the one shim, so
        # both say it; no other document of the kernel's dials the chat socket (the shell's own socket is app=shell)
        page = inspect.getsource(km._chat_page)
        self.assertIn('_shim("chat", v)', page); self.assertIn("_CHAT_MOBILE_JS", page)
        self.assertIn("&rest=1", km._shim("chat"))
        self.assertEqual(SRC.count('new WebSocket(proto+location.host+"/ws?app=%s'), 1, "the shim is the one page-side dialer of a pane's socket")
        self.assertIn('rest:(APP==="chat"?1:0)', SRC, "…and the dial terms a linked machine's relay is dialed with")
        fed = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "federation.ts")).read()
        self.assertIn('if (t.rest) url += "&rest=1";', fed, "the relay's dial states it to the remote kernel")


class TheLastFewTabsAPageShowed(_Base):
    """What a page watches is the last few tabs it showed, not its active tab alone (the same day's second cut). With one
    watched tab per page, a flip between two sessions took the one left to rest at its next move, and the flip back met the
    loader and a build on demand, seconds on a large transcript. Each chat client keeps the last ROMP_WATCHED_TABS (four by
    default) distinct tabs its page activated; all of them go on being built, and a tab leaves the set only when a newer
    activation displaces it or the socket ends."""

    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": "2"})   # two, so three sessions can show a displacement
        self._env.start()

    def tearDown(self):
        self._env.stop()
        super().tearDown()

    def _show(self, c, sid):
        """The page activates a tab: the message its click sends."""
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": sid}, c)

    def _cycle(self, clients, *moved):
        """Every named session writes, then one pusher cycle. Returns what was built, in order."""
        del self.built[:]
        for sid in moved:
            self._move(sid)
        km._push(list(clients))
        return list(self.built)

    def test_30_flipping_between_two_tabs_rests_neither_and_sends_no_skeleton(self):
        c = self._client()
        self._warm(c)
        self._show(c, S1)                                    # A
        self._show(c, S2)                                    # A -> B
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S2], "both are among the last two the page showed: both built; "
                         "tests, never shown, is not")
        self._show(c, S1)                                    # B -> A
        self.assertEqual(sorted(self._cycle([c], S1, S2)), [S1, S2])
        self._show(c, S2)                                    # and again
        self.assertEqual(sorted(self._cycle([c], S1, S2)), [S1, S2])
        self.assertEqual(self._frames(c, "status", S1) + self._frames(c, "status", S2), [], "no status frame: neither went to rest")
        self.assertEqual([f for f in self._frames(c, "tabOrder") if S1 in (f.get("skeleton") or []) or S2 in (f.get("skeleton") or [])], [],
                         "no strip ever lists either as a skeleton")
        self.assertEqual(len(self._frames(c, "chatTail", S1)), 3, "the tab the page left keeps taking its changes as deltas")
        self.assertEqual(len(self._frames(c, "chatTail", S2)), 3)
        self.assertEqual(self._frames(c, "session"), [], "and no full frame: a flip paints from the copy the page holds")
        self.assertIn(S1, c["echat"]); self.assertIn(S2, c["echat"])
        self.assertEqual(c["recent"], (S1, S2), "the last two it showed, newest last")
        self.assertEqual(self._chat()["rested"], 1, "tests alone went to rest")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S3)], [True])

    def test_31_the_activation_past_the_count_rests_the_oldest(self):
        c = self._client()
        self._warm(c)
        self._show(c, S1); self._show(c, S2)
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S2], "tests was never shown: it rests")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S3)], [True])
        c["_frames"].clear()
        self._show(c, S3)                                    # the third distinct tab: web, the oldest, is displaced
        self.assertEqual(c["recent"], (S2, S3))
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S3, "why": "skeleton-click"}, c)   # the page's ask for the tab it opened
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S2, S3], "web is not built: nobody is showing it, nor showed it among the last two")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S1)], [True], "it is taken back at its next move")
        self.assertEqual(self._frames(c, "status", S2), [], "api, the one before, stays")
        # showing a tab already in the set moves it to the newest place and displaces nothing
        self._show(c, S2)
        self.assertEqual(c["recent"], (S3, S2))
        self.assertEqual(sorted(self._cycle([c], S2, S3)), [S2, S3])

    def test_32_two_pages_keep_separate_sets(self):
        a, b = self._client(), self._client()                # a laptop and a phone
        self._warm(a, b)
        self._show(a, S1); self._show(a, S2)                 # the laptop works web and api
        self._show(b, S3)                                    # the phone is on tests
        self.assertEqual((a["recent"], b["recent"]), ((S1, S2), (S3,)))
        self.assertEqual(sorted(self._cycle([a, b], S1, S2, S3)), [S1, S2, S3], "each page's set keeps its tabs built for both")
        self.assertEqual(self._frames(a, "status") + self._frames(b, "status"), [])
        self._show(b, S1); self._show(b, S2)                 # the phone moves on: ITS third and fourth tabs displace tests
        self.assertEqual((a["recent"], b["recent"]), ((S1, S2), (S1, S2)), "the laptop's set is its own activations alone")
        self.assertEqual(sorted(self._cycle([a, b], S1, S2, S3)), [S1, S2], "no page showed tests among its last two")
        for c in (a, b):
            self.assertEqual([f.get("rest") for f in self._frames(c, "status", S3)], [True])

    def test_33_a_page_disconnecting_takes_its_set_with_it(self):
        a, b = self._client(), self._client()
        self._warm(a, b)
        self._show(a, S1)
        self._show(b, S2); self._show(b, S3)
        self.assertEqual(sorted(self._cycle([a, b], S1, S2, S3)), [S1, S2, S3])
        km._clients[:] = [a]                                 # the phone's socket ended: its record, and its set, are gone
        a["_frames"].clear()
        self.assertEqual(self._cycle([a], S1, S2, S3), [S1], "only what the page still connected watches")
        self.assertEqual(sorted(f["id"] for f in self._frames(a, "status") if f.get("rest")), [S2, S3])
        # a socket that dials again starts its set from the tab its dial names
        got = []
        real_reg, real_recv = km._register_ws_client, km._ws_recv
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: (0x8, b"", True)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(_fake_self("/ws?app=chat&delta=1&iid=page-9&rest=1&active=%s" % S3))
        finally:
            km._register_ws_client, km._ws_recv = real_reg, real_recv
            for c in got:
                if c in km._clients:
                    km._clients.remove(c)
        self.assertEqual((got[0].get("active"), got[0].get("recent")), (S3, (S3,)))

    def test_34_an_activation_that_names_no_tab_displaces_nothing(self):
        c = self._client()
        self._warm(c)
        self._show(c, S1); self._show(c, S2)
        for none in (None, ""):                              # a page showing no tab; a relay whose page moved to another machine's tab
            self._show(c, none)
            self.assertEqual(c["recent"], (S1, S2))
        self.assertFalse(c.get("active"))
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S2], "the tabs it last showed stay watched; the rest may rest")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S3)], [True], "it has named tabs: it is not taken to watch all it holds")

    def test_35_the_count_is_a_constant_with_an_environment_override_and_perf_reports_it(self):
        self.assertEqual(km.WATCHED_TABS, 4)
        self.assertEqual(km._watched_tabs(), 2, "this class's override")
        self.assertEqual(self._chat()["watchedTabs"], 2, "/perf says the count in force")
        for raw, want in (("8", 8), ("1", 1), ("", 4), ("0", 4), ("-3", 4), ("four", 4)):
            with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": raw}):
                self.assertEqual(km._watched_tabs(), want, raw)
        with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": "1"}):   # one: the page's active tab alone, the first cut's rule
            c = self._client()
            self._warm(c)
            self._show(c, S1); self._show(c, S2)
            self.assertEqual(c["recent"], (S2,))
            self.assertEqual(self._cycle([c], S1, S2), [S2])
            self.assertEqual([f.get("rest") for f in self._frames(c, "status", S1)], [True])
        doc = open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8").read()
        self.assertIn("`ROMP_WATCHED_TABS`", doc); self.assertIn("`watchedTabs`", doc)


class AfterTheReview(_Base):
    """The findings of the independent review of the first cut, and the cases it did not exercise."""

    def test_40_a_socket_that_never_said_ready_and_holds_nothing_keeps_no_tab_built(self):
        # a bare chat socket (a monitor, a lab's stand-in) names no tab and holds nothing. Read as a page that has not said
        # what it shows, it kept every tab built for as long as it stayed open
        c = self._client(active=S1)
        self._warm(c)
        bare = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None, "handshake": False}   # as _new_ws_client makes it
        km._clients[:] = [c, bare]
        self._move(S2)
        km._push([c, bare])
        self.assertEqual(self.built, [], "nobody watches api, and the bare socket is owed nothing until it says ready")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True])
        self.assertNotIn(S2, bare.get("skeleton") or (), "nothing is taken from it or given to it")
        # the same socket with the dial's hint watches that tab, as any page's does
        bare["active"] = S3
        self._move(S3)
        km._push([c, bare])
        self.assertEqual(self.built, [S3])
        # …and a page past its ready that names no tab but holds none of this tab is handed it (unserved), not read as watching
        self.assertEqual(km._rest_blocker_locked({"app": "chat", "sent": {}}, S2), "unserved")
        self.assertEqual(km._rest_blocker_locked({"app": "chat", "sent": {}, "echat": {S2: ("u", 1)}}, S2), "unreported", "one that HOLDS it is")

    def test_41_a_client_that_cannot_take_a_tab_back_is_said_once_in_the_problem_center_by_its_kind(self):
        said = []
        real = km._sdk_problem
        km._sdk_problem = said.append
        try:
            old = self._client(active=S1, restTabs=False)
            self._warm(old)
            for _ in range(3):
                self._move(S2); self._move(S3)
                km._push([old])
            self.assertEqual(len(said), 1, "one line for the socket, however many tabs and cycles: %r" % said)
            self.assertIn("a chat page loaded before this build", said[0])
            self.assertIn("rebuilds every tab it holds on each refresh until that page is reloaded or closed", said[0])
            ext = self._client(active=S1, restTabs=False, ext=True)          # the VS Code extension host's dial (client=ext)
            relay = self._client(active=S1, restTabs=False, kind="relay")    # a linked machine's page from before this build
            for c in (ext, relay):
                self._warm_second(c)
                km._clients[:] = [c]
                self._move(S2)
                km._push([c])
            self.assertEqual(len(said), 3)
            self.assertIn("the VS Code chat panel", said[1]); self.assertIn("until that panel is closed", said[1])
            self.assertIn("a chat page on a linked machine loaded before this build", said[2])
        finally:
            km._sdk_problem = real
        got = []
        real_reg, real_recv = km._register_ws_client, km._ws_recv
        km._register_ws_client = lambda c: (got.append(c), km._clients.append(c))
        km._ws_recv = lambda rfile: (0x8, b"", True)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                km.Handler._ws(_fake_self("/ws?app=chat&wid=w1&client=ext&delta=1"))
        finally:
            km._register_ws_client, km._ws_recv = real_reg, real_recv
            for c in got:
                if c in km._clients:
                    km._clients.remove(c)
        self.assertIs(got[0].get("ext"), True, "the handshake remembers the extension host's dial")

    def test_42_the_machines_billing_reads_are_taken_once_per_push_for_every_resting_tab(self):
        calls = {"both": 0, "acct": 0, "login": 0}
        saved = (km._auth_both, km._claude_account_label, km._claude_login_display)
        km._auth_both = lambda: calls.__setitem__("both", calls["both"] + 1) or False
        km._claude_account_label = lambda: calls.__setitem__("acct", calls["acct"] + 1) or "dev@example.com"
        km._claude_login_display = lambda: calls.__setitem__("login", calls["login"] + 1) or "dev@example.com · Example Org"
        try:
            km._live_scope.auth = {}                         # a push's scope, as the pusher's cycle and a connect push open it
            rows = [{"authLabel": ""}, {"authLabel": "Work"}] * 40   # eighty resting tabs' rows
            km._light_billing(rows[0])
            first = dict(calls)                              # the push's one take (the availability read takes the account's label once more, itself once a push)
            got = [km._light_billing(r) for r in rows]
            self.assertEqual(calls, first, "eighty more tabs, not one more read")
            self.assertEqual(calls["both"], 1)
            self.assertEqual((got[0]["authLabel"], got[1]["authLabel"]), ("dev@example.com · Example Org", "Work"),
                             "a row with a label of its own keeps it; the rest show the machine's login")
            self.assertEqual({g["authAcct"] for g in got}, {"dev@example.com"})
            km._live_scope.auth = None                       # outside a push (the per-session push): read fresh
            km._light_billing(rows[0]); km._light_billing(rows[0])
            self.assertEqual(calls["both"], 3, "two more calls, two more reads")
        finally:
            km._live_scope.auth = None
            km._auth_both, km._claude_account_label, km._claude_login_display = saved
        self.assertIn("**_light_billing(tm)}", inspect.getsource(km._light_status), "the short status takes its machine half from there")

    def test_43_a_click_on_a_second_tab_does_not_wait_for_the_build_the_first_click_asked_for(self):
        # a needFull's build ran on the socket's lane, so the lane stood still for its length: seconds for a large tab at
        # rest, and a click on another tab meanwhile waited behind it
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2); self._move(S3)
        km._push([c])                                        # api and tests at rest
        c["_frames"].clear()
        import threading
        gate, started = threading.Event(), threading.Event()
        real = km.build_session

        def slow(sid, now, live_map=None, **kw):
            if sid == S2:                                    # the first tab is large: its build takes as long as the test lets it
                started.set()
                gate.wait(20)
            return real(sid, now, live_map, **kw)
        km.build_session = slow
        lane = km._ws_arm_lane(c)
        try:
            put = lambda msg: km._lane_put(lane, msg["type"], lambda: km.Handler._dispatch_ws(_Self(), msg, c))
            put({"type": "activeTab", "id": S2}); put({"type": "needFull", "id": S2, "why": "rest-open"})
            self.assertTrue(started.wait(10), "the first click's build is under way")
            put({"type": "activeTab", "id": S3}); put({"type": "needFull", "id": S3, "why": "rest-open"})   # the reader moved on
            deadline = time.time() + 10
            while time.time() < deadline and not self._frames(c, "session", S3):
                time.sleep(0.02)
            self.assertEqual(len(self._frames(c, "session", S3)), 1, "the second tab's full is out while the first is still building")
            self.assertEqual(self._frames(c, "session", S2), [], "…which it still is")
            self.assertEqual(c.get("active"), S3, "the lane took the second click's activeTab: no build holds it")
            gate.set()
            deadline = time.time() + 10
            while time.time() < deadline and not self._frames(c, "session", S2):
                time.sleep(0.02)
            self.assertEqual(len(self._frames(c, "session", S2)), 1, "the first ask is still answered: the page keeps it latched until then")
        finally:
            gate.set()
            km._lane_close(c)
            c.pop("lane", None); c.pop("closing", None)
            deadline = time.time() + 5
            while time.time() < deadline and (c.get("repairs") or {}).get("running"):
                time.sleep(0.02)

    def test_44_an_ask_for_a_tab_the_page_moved_away_from_waits_behind_the_one_it_shows_now(self):
        c = self._client(active=S1)
        self._warm(c)
        order = []
        handler = type("H", (), {"_push_one": lambda self, client, only=None: order.append(sorted(only)[0])})()
        c["lane"] = {}                                       # a socket with a lane: its asks go to its repairs
        c["repairs"] = {"pending": [], "running": 1, "building": {S1}}   # one thread is building web already
        try:
            c["active"] = S3
            km._repair_ask(handler, c, S2)                   # an ask for a tab the page is not showing: it waits
            self.assertEqual((c["repairs"]["pending"], c["repairs"]["running"]), ([S2], 1), "no second thread for it")
            km._repair_ask(handler, c, S2)
            self.assertEqual(c["repairs"]["pending"], [S2], "asked twice, built once")
            km._repair_ask(handler, c, S3)                   # the tab on screen: a second thread, and it goes first
            deadline = time.time() + 5
            while time.time() < deadline and len(order) < 2:
                time.sleep(0.02)
            self.assertEqual(order, [S3, S2], "the tab the page shows now, then the one it left: none is dropped")
        finally:
            c.pop("lane", None)

    def test_45_a_session_that_dies_while_its_tab_rests_is_built_once_for_its_last_word(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])                                        # at rest, its status from the live row
        c["_frames"].clear()
        self.live.pop(S2)                                    # the session ended in place: no live row to state a status from
        for _ in range(3):
            km._push([c])
        self.assertEqual(self.built, [S2], "the kernel cannot state its status without a build: built, once")
        st = self._frames(c, "status", S2)
        self.assertEqual(len(st), 1, "the page's chip gets the built status: %r" % st)
        self.assertNotIn("provisional", st[0]["status"])
        self.assertEqual(self._frames(c, "session", S2) + self._frames(c, "chatTail", S2), [], "and still no chat frame it did not ask for")
        # a crash that takes the tab off the strip forgets it whole: the cached build, the baseline, the settling mark
        km._chat_rest_retried.add(S2)
        real = km._chat_tab_sessions
        km._chat_tab_sessions = lambda now, live_map: [r for r in real(now, live_map) if r["sid"] != S2]
        try:
            km._push([c])
        finally:
            km._chat_tab_sessions = real
        self.assertNotIn(S2, km._built_chat); self.assertNotIn(S2, km._chat_rest_retried)
        self.assertNotIn(S2, c.get("skeleton") or ())

    def test_46_the_needs_you_mark_and_count_and_held_mail_reach_a_resting_tab_without_a_build(self):
        # the ring a tab wears when a card of its session needs you is the feed's verdict, a membership read: a question the
        # agent stopped on, a held message from a peer waiting for approval. It rides the short status every cycle
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])
        verdict = {"needs": False, "count": 0}
        saved = (km._feed_needs_input_of, km._feed_needs_input_count_of)
        km._feed_needs_input_of = lambda sid: verdict["needs"] if sid == S2 else False
        km._feed_needs_input_count_of = lambda sid: verdict["count"] if sid == S2 else 0
        try:
            km._push([c])
            c["_frames"].clear()
            for needs, count, want in ((True, 1, (True, 1)),     # a peer's message is held for approval: one thing needs you
                                       (True, 3, (True, 3)),     # two more questions since
                                       (False, 0, (False, 0))):  # all answered
                verdict.update(needs=needs, count=count)
                self._move(S2)
                km._push([c])
                st = self._frames(c, "status", S2)[-1]["status"]
                self.assertEqual((st["needsYou"], st["needsYouCount"]), want)
            self.assertEqual(self.built, [], "three changes of the mark, no build")
            self.assertEqual(len(self._frames(c, "status", S2)), 3, "one status frame per change")
        finally:
            km._feed_needs_input_of, km._feed_needs_input_count_of = saved


class WhatNeverReadABackgroundBuild(_Base):
    """The consumers the survey found beside the page's own tab frames, each shown to work with no chat build behind it."""

    def test_20_the_tracking_off_dots_take_the_live_rows_word_when_no_parse_is_cached(self):
        # with Task tracking off the chat's working and awaiting dots are derived outside the feed build, from the cached
        # parse; every tab's chat build used to store that parse earlier in the same push
        saved = {k: getattr(km, k) for k in ("_alive_sessions", "_parse_cached", "_merge_live_atoms", "_session_working", "_session_awaiting")}
        try:
            km._alive_sessions = lambda now, live_map: [{"sid": "s1", "name": "web", "path": "/p1"}, {"sid": "s2", "name": "api", "path": "/p2"},
                                                         {"sid": "s3", "name": "tests", "path": "/p3"}, {"sid": "s4", "name": "docs", "path": "/p4"}]
            km._parse_cached = lambda path: {"turns": [path]} if path == "/p1" else None
            km._merge_live_atoms = lambda ps, sid, shown_texts=(): ps
            km._session_working = lambda turns: False        # web's cached parse: its last turn is closed
            km._session_awaiting = lambda sid, path, idle, stamp=False: {"why": "a delegate"} if sid == "s3" else None
            live = {"s1": {"state": "working"}, "s2": {"state": "working"}, "s3": {"state": "waiting"}}
            self.assertEqual(km._chat_dots_off(0, live), (["api"], ["tests"]),
                             "a cached parse is read as before (web: closed); with none, the row's word (api works, tests "
                             "awaits); a session with neither a parse nor a row shows no dot (docs)")
        finally:
            for k, v in saved.items():
                setattr(km, k, v)

    def test_21_the_retry_suppression_lifts_for_a_session_nobody_is_watching(self):
        # the job read the cached parse alone, which a chat build of every tab kept fresh; a suppressed session the user
        # re-engaged without opening its tab stayed suppressed until something parsed it
        saved = {k: getattr(km, k) for k in ("_alive_sessions", "_parse_cached", "_parse", "_session_chip", "_mark_views_dirty")}
        try:
            km._retry_suppress_cache.clear()
            km._mark_views_dirty = lambda *a, **k: None
            km._suppress_session_retry(S2)
            floor = json.loads((km.jd.STATE / "retry-suppressed.json").read_text())[S2]
            parsed = []
            km._alive_sessions = lambda now, live_map: [{"sid": S2, "path": self.paths[S2]}, {"sid": S3, "path": os.path.join(self.tmp, "gone.jsonl")}]
            km._parse_cached = lambda p: None                # nothing built the tab: no parse at this version
            km._parse = lambda p, sid, now: parsed.append(sid) or {"turns": [{"atoms": [{"type": "user", "author": "human", "t": floor + 5, "text": "carry on"}]}]}
            km._session_chip = lambda *a, **k: "ready"
            km._auto_resume_session_retry(int(time.time()), {})
            km._retry_suppress_cache.clear()
            self.assertEqual(parsed, [S2], "read for the session under a floor, once; never for a transcript it cannot stat")
            self.assertFalse(km._session_retry_suppressed(S2), "the user spoke after the stop and the turn settled: re-armed")
        finally:
            for k, v in saved.items():
                setattr(km, k, v)
            km._retry_suppress_cache.clear()

    def test_22_the_roads_that_notify_and_the_thread_popovers_read_no_chat_build(self):
        # Turn-end notifications and phone pushes (the jobs pass's turnNotify), the cards' bells (the feed build's notify
        # pass), the thread popovers' frame and its tail parse, and each lane's live status frame have their own sources.
        # Pinned at the source: none of these bodies names the chat build or its cache, so a tab at rest costs them nothing.
        for name in ("_turn_notify_tick", "_turn_end_key", "_feed_notifications", "_comments_frame", "_thread_tail_parse",
                     "_live_status_frame", "_live_status_frames", "_light_status", "_provisional_row", "_provisional_ledger"):
            fn = getattr(km, name)
            fn = getattr(fn, "__wrapped__", fn)
            names = {n.id for n in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))) if isinstance(n, ast.Name)}
            self.assertFalse(names & {"build_session", "_built_chat", "_prev_chat_events"}, name)

    def test_23_a_kernel_with_no_page_connected_builds_no_chat_at_all(self):
        # the standing proof that nothing outside a page depends on background chat builds: with no client the push
        # returns before any build, and it always has; the judges, the nudges and the notifications run all the same
        km._push([])
        self.assertEqual(self.built, [])
        feed_only = self._client(app="feed")
        km._clients[:] = [feed_only]
        km._push([feed_only])
        self.assertEqual(self.built, [], "a feed pane alone needs no chat build either")


if __name__ == "__main__":
    unittest.main()
