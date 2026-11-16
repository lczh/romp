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
        self.assertEqual(km._watched_by(c), (S2, S3))
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S3, "why": "skeleton-click"}, c)   # the page's ask for the tab it opened
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S2, S3], "web is not built: nobody is showing it, nor showed it among the last two")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S1)], [True], "it is taken back at its next move")
        self.assertEqual(self._frames(c, "status", S2), [], "api, the one before, stays")
        self.assertEqual(c["recent"], (S2, S3), "a tab taken back leaves the page's record of what it showed")
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
        self.assertEqual((km._watched_by(a), km._watched_by(b)), ((S1, S2), (S1, S2)), "the laptop's set is its own activations alone")
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
        for raw, want in (("8", 8), ("1", 1), ("", 4), ("0", 4), ("-3", 4), ("four", 4), ("40", 16)):
            with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": raw}):
                self.assertEqual(km._watched_tabs(), want, raw)
        with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": "1"}):   # one: the page's active tab alone, the first cut's rule
            c = self._client()
            self._warm(c)
            self._show(c, S1); self._show(c, S2)
            self.assertEqual(km._watched_by(c), (S2,))
            self.assertEqual(self._cycle([c], S1, S2), [S2])
            self.assertEqual([f.get("rest") for f in self._frames(c, "status", S1)], [True])
        doc = open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8").read()
        self.assertIn("`ROMP_WATCHED_TABS`", doc); self.assertIn("`watchedTabs`", doc)


class TheCountFromAStateFile(_Base):
    """The count can be changed on a running kernel (2026-11-16): one of the machines this runs on cannot be handed a new
    environment variable without restarting the manager that keeps its kernel up, and the lab says the count matters (a
    cycle of 5.3 s with one watched tab against 9.9 s with four, two large sessions among them). So it is read from
    STATE/watched-tabs, a bare integer, at each cycle: the file, else ROMP_WATCHED_TABS, else four, held to 1..16. A file
    that cannot be used falls back and says so once in the problem center."""

    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": "2"})
        self._env.start()
        self.said = []
        self._real_problem = km._sdk_problem
        km._sdk_problem = self.said.append

    def tearDown(self):
        km._sdk_problem = self._real_problem
        self._env.stop()
        super().tearDown()

    def _file(self, text):
        """Publish STATE/watched-tabs as a writer publishes (a temp file renamed into place, so the stat the reader keys on
        moves however fast the test writes), or take it away (None)."""
        p = km.jd.STATE / "watched-tabs"
        if text is None:
            if p.is_dir():
                p.rmdir()
            elif p.exists():
                p.unlink()
            return
        tmp = p.with_name("watched-tabs.tmp")
        tmp.write_text(text)
        os.replace(tmp, p)

    def test_50_the_file_overrides_the_environment_and_perf_says_where_the_count_came_from(self):
        self.assertEqual(km._watched_tabs_path(), km.jd.STATE / "watched-tabs", "beside session-hosts and the other switches")
        self.assertEqual(km._watched_tabs_setting(), (2, "env", None), "no file: the environment's")
        self._file("3\n")
        self.assertEqual(km._watched_tabs_setting(), (3, "file", None), "the file wins")
        self.assertEqual(km._watched_tabs(), 3)
        chat = self._chat()
        self.assertEqual((chat["watchedTabs"], chat["watchedTabsFrom"], chat["watchedTabsFault"]), (3, "file", None), "/perf: the count in force and its source")
        for raw, want in (("1", 1), ("16", 16), ("40", 16), ("0", 1), ("-3", 1), (" 5 \n", 5), ("99999999999999999999", 16)):   # held to 1..16
            self._file(raw)
            self.assertEqual(km._watched_tabs_setting(), (want, "file", None), raw)
        self._file(None)
        self.assertEqual(km._watched_tabs_setting(), (2, "env", None), "the file gone: the environment's again")
        self.assertEqual(self._chat()["watchedTabsFrom"], "env")
        with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": ""}):
            self.assertEqual(km._watched_tabs_setting(), (4, "default", None))
            self.assertEqual(self._chat()["watchedTabsFrom"], "default")
        self.assertEqual(self.said, [], "nothing here is a fault")
        doc = open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8").read()
        self.assertIn("`watched-tabs`", doc); self.assertIn("`watchedTabsFrom`", doc)

    def test_51_a_change_takes_effect_at_the_next_cycle_shrinking_and_growing_the_set(self):
        c = self._client()
        self._warm(c)
        self._file("3")
        self._show(c, S1); self._show(c, S2); self._show(c, S3)
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S2, S3], "three watched tabs, by the file, on a kernel started with two")
        self.assertEqual(self._frames(c, "status"), [], "none taken back")
        # SHRINK to one, no restart: at the next cycle web and api are no longer watched. web, which goes on writing, is
        # taken back at once; api, which wrote nothing, keeps its copy and costs nothing (an unwatched tab that does not
        # move is served from its cached build, as ever)
        self._file("1")
        self.assertEqual(self._cycle([c], S1, S3), [S3], "the tab on screen alone is built")
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S1)], [True], "web, the oldest, is taken back at this cycle")
        self.assertEqual(self._frames(c, "status", S2), [])
        self.assertEqual(km._watched_by(c), (S3,))
        self.assertEqual(self._chat()["watchedTabs"], 1)
        # GROW to two: api, which the page still holds current, is among the last two it showed again and takes its
        # changes as deltas; web was taken back and stays at rest until the page opens it (a larger count builds nothing
        # for a copy the page was told is behind)
        self._file("2")
        c["_frames"].clear()
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S2, S3], "api is built again; web is not")
        self.assertEqual(len(self._frames(c, "chatTail", S2)), 1, "…and its change reaches the page as a delta on the base it kept")
        self.assertEqual(self._frames(c, "session"), [], "no full frame: nothing was reloaded")
        self.assertEqual([f for f in self._frames(c, "status") if f.get("rest")], [], "and nothing is taken back")
        self.assertEqual(km._watched_by(c), (S2, S3))
        # the page opens web: the newest of the three it showed; with two, api is the one displaced
        self._show(c, S1)
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S1, "why": "rest-open"}, c)
        self.assertEqual(km._watched_by(c), (S3, S1))
        c["_frames"].clear()
        self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S3])
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True])
        self.assertEqual(self.said, [])

    def test_52_a_file_that_cannot_be_used_falls_back_and_says_so_once(self):
        c = self._client()
        self._warm(c)
        self._show(c, S1); self._show(c, S2)
        self._file("four\n")
        for _ in range(3):                                   # three cycles over the same bad file
            self.assertEqual(sorted(self._cycle([c], S1, S2, S3)), [S1, S2], "the environment's two, as with no file")
        n, src, fault = km._watched_tabs_setting()
        self.assertEqual((n, src), (2, "env"))
        self.assertIn('"four"', fault); self.assertIn("not a whole number", fault)
        self.assertEqual(len(self.said), 1, "one line in the problem center, not one per cycle: %r" % self.said)
        line = self.said[0]
        self.assertIn(str(km.jd.STATE / "watched-tabs"), line, "it names the file")
        self.assertIn('"four"', line); self.assertIn("2", line); self.assertIn("ROMP_WATCHED_TABS", line)
        self.assertIn("1 to 16", line, "and what the file may hold")
        chat = self._chat()
        self.assertEqual((chat["watchedTabs"], chat["watchedTabsFrom"]), (2, "env"))
        self.assertEqual(chat["watchedTabsFault"], fault, "/perf carries the reason too, and reading /perf says nothing a second time")
        self.assertEqual(len(self.said), 1)
        # another edit that cannot be used is new information: said again, once
        self._file("")
        for _ in range(2):
            km._push([c])
        self.assertEqual(len(self.said), 2)
        self.assertIn("empty", self.said[1])
        # a file that cannot be read (a directory in its place), with nothing in the environment: the default, said so
        self._file(None)
        (km.jd.STATE / "watched-tabs").mkdir()
        with mock.patch.dict(os.environ, {"ROMP_WATCHED_TABS": ""}):
            for _ in range(2):
                km._push([c])
            n, src, fault = km._watched_tabs_setting()
            self.assertEqual((n, src), (4, "default"))
            self.assertIn("cannot be read", fault)
        self.assertEqual(len(self.said), 3)
        self.assertIn("default", self.said[2])
        # repaired: the file's count, at the next cycle, and nothing more is said
        self._file(None)
        self._file("3")
        km._push([c])
        self.assertEqual(km._watched_tabs_setting(), (3, "file", None))
        self.assertEqual(len(self.said), 3)


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


S4 = "11111111-2222-3333-4444-121212121214"   # docs: a fourth tab, for the third quick click


class AfterTheFinalReview(_Base):
    """The final review's findings (2026-11-16): a failed read at the open of a tab at rest; the parse a full frame's tail
    start needs, taken inside the client's slot lock; a third quick click queued behind two builds for tabs the reader
    had left."""

    def setUp(self):
        super().setUp()
        self.notices = []
        self._real_notice = km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok, kind))

    def tearDown(self):
        km._sync_notice = self._real_notice
        getattr(km, "_chat_rested", {}).clear()
        getattr(km, "_TAIL_TIX", {}).clear()
        km._EMPTY_BUILD_NOTED.clear()
        getattr(km, "_EMPTY_BUILD_BELLED", set()).clear()
        km._chat_build_faults.clear()
        super().tearDown()

    def _click(self, c, sid):
        """What a click on a tab at rest sends: the tab it shows now, then its ask for the tab whole."""
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": sid}, c)
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": sid, "why": "rest-open"}, c)

    def _rested(self):
        """A page on web with api taken back: the state a click on api starts from."""
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True])
        c["_frames"].clear()
        del self.built[:]
        return c

    # ── 1: a failed read at the open ──
    def test_60_a_transcript_that_cannot_be_read_at_the_open_sends_no_empty_frame_and_is_said_once(self):
        c = self._rested()
        held = self.n[S2]
        self.n[S2] = 0                                       # the read fails: the build comes back with no events
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self._click(c, S2)
            for _ in range(3):                               # …and goes on failing for three more cycles
                km._push([c])
        self.assertEqual(self._frames(c, "session", S2), [], "no full with no events: it took the page's line down over a copy still behind")
        self.assertEqual(self._frames(c, "chatTail", S2), [])
        self.assertIn(S2, c.get("askedFull") or (), "the ask stands, so the tab is not taken back and the page's line stays up")
        mine = [n for n in self.notices if "api" in n[0]]
        self.assertEqual(len(mine), 1, "said once on the bell, not once per cycle: %r" % self.notices)
        text, ok, kind = mine[0]
        self.assertEqual((ok, kind), (False, "refused"))
        self.assertIn("cannot be read", text); self.assertIn("catching up", text)
        self.assertEqual(err.getvalue().count("came back EMPTY"), 1, "…and once on stderr, as for a watched tab")
        self.assertGreaterEqual(km._chat_rested.get(S2) or 0, 1, "what the pages held when it went to rest is what the guard reads")
        # the read succeeds: the full that answers the ask, with the transcript
        self.n[S2] = held + 2
        self._move(S2, by=0)
        km._push([c])
        full = self._frames(c, "session", S2)
        self.assertEqual(len(full), 1)
        self.assertEqual(len(full[0]["events"]), held + 2)
        self.assertNotIn(S2, c.get("askedFull") or ())
        self.assertEqual(len([n for n in self.notices if "api" in n[0]]), 1)
        self.assertNotIn(S2, km._EMPTY_BUILD_BELLED, "the read that succeeded ended the episode: a later failure is said anew")

    def test_66_an_episode_noted_before_any_page_asked_is_still_said_to_the_page_that_asks(self):
        # the per-session push runs the same guard and names no page: its stderr line must not use up the bell's one line
        c = self._rested()
        self.n[S2] = 0
        with contextlib.redirect_stderr(io.StringIO()):
            km._note_empty_build(S2, self.paths[S2], 7, marked=True)       # the episode opens with nobody waiting
            self.assertEqual(self.notices, [])
            self._click(c, S2)
            km._push([c])
        self.assertEqual(len([n for n in self.notices if "api" in n[0]]), 1, "the page that asks is told, once: %r" % self.notices)

    def test_61_a_tab_that_never_rested_or_never_had_events_is_untouched(self):
        # a session that genuinely has nothing (a fresh one) is still sent its empty full; the record is of tabs that rested
        c = self._client(active=S1)
        self.n[S3] = 0
        km._clients[:] = [c]
        with contextlib.redirect_stderr(io.StringIO()):
            km._push([c])
        self.assertEqual([len(f["events"]) for f in self._frames(c, "session", S3)], [0], "a fresh session's empty full goes, as before")
        self.assertNotIn(S3, km._chat_rested)
        self.assertEqual(self.notices, [])

    def test_62_a_build_that_raises_at_the_open_leaves_the_ask_standing_and_is_said_once(self):
        c = self._rested()
        real = km.build_session

        def broken(sid, now, live_map=None, **kw):
            if sid == S2:
                raise ValueError("a record that cannot be read")
            return real(sid, now, live_map, **kw)
        km.build_session = broken
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self._click(c, S2)
                for _ in range(3):
                    self._move(S2)
                    km._push([c])
        finally:
            km.build_session = real
        self.assertEqual(self._frames(c, "session", S2) + self._frames(c, "chatTail", S2), [], "nothing is sent for it")
        self.assertIn(S2, c.get("askedFull") or (), "the ask stands: the page goes on saying it is catching up")
        mine = [n for n in self.notices if "api" in n[0]]
        self.assertEqual(len(mine), 1, "one line on the bell for the episode: %r" % self.notices)
        self.assertIn("cannot be built", mine[0][0])
        self._move(S2)
        km._push([c])                                        # the build succeeds
        self.assertEqual(len(self._frames(c, "session", S2)), 1)
        self.assertNotIn(S2, c.get("askedFull") or ())

    # ── 2: the parse a full needs, outside the client's lock ──
    def _index_probe(self, c):
        """Stand in for the session listing and the parse (one turn per event), and record at every index build whether
        the calling thread holds the client's slot lock."""
        owned = []
        saved = (km._sessions, km._parse, km._turn_index_of_events, km.WIRE_TAIL)
        km._sessions = lambda now: [{"sid": sid, "path": self.paths[sid]} for sid in TAB_ORDER]
        km._parse = lambda path, sid, now: {"turns": [{"atoms": [{"uuid": "%s-u%d" % (sid[-1], i)}]} for i in range(self.n[sid])]}
        real_index = km._turn_index_of_events

        def index(evs, turns):
            owned.append(km._client_lock(c)._is_owned())
            return real_index(evs, turns)
        km._turn_index_of_events = index
        km.WIRE_TAIL = 3                                     # so a full is cut inside the list and its tail start is the index's
        return owned, saved

    def test_63_the_index_a_full_needs_is_built_with_the_clients_lock_released_on_the_ask_and_on_the_cycle(self):
        c = self._rested()
        owned, saved = self._index_probe(c)
        try:
            self._click(c, S2)                               # THE ASK'S ROAD: a full for the tab at rest
            full = self._frames(c, "session", S2)
            self.assertEqual(len(full), 1)
            self.assertEqual(owned, [False], "the index was built once, with the lock released: %r" % owned)
            n = self.n[S2]
            self.assertEqual((len(full[0]["events"]), full[0]["tailLo"], full[0]["firstUuid"]), (3, n - 3, "2-u%d" % (n - 3)),
                             "and the frame's tail start is the index's: the last three events, from their turn")
            # THE CYCLE'S ROAD: the page's base for the tab on screen is gone (its own ask), and the cycle's send is the full
            del owned[:]
            km._client_reset_chat_sid(c, S1)
            c["_frames"].clear()
            self._move(S1)
            km._push([c])
            self.assertEqual(len(self._frames(c, "session", S1)), 1)
            self.assertEqual(owned, [False], "the cycle's full too: %r" % owned)
            # a delta needs no index at all: the tab on screen, on the base the ask's full gave it
            del owned[:]
            self._move(S2)
            km._push([c])
            self.assertEqual(len(self._frames(c, "chatTail", S2)), 1)
            self.assertEqual(owned, [], "a delta on a held base builds no index, in the lock or out of it")
        finally:
            km._sessions, km._parse, km._turn_index_of_events, km.WIRE_TAIL = saved

    def test_64_two_senders_and_one_page_no_tail_before_its_base_and_no_older_frame_after_a_newer_one(self):
        # the wire, as a page reads it: two builders (the cycle and a targeted push) hand one client their lists in either
        # order while the session writes and is sometimes rewound (a full for every base holder), each taking the index
        # before the lock. The page's own rules are the check: a tail applies only after the key it names, and no frame
        # is older than one already applied
        import threading
        sid, path = S1, self.paths[S1]
        frames = []
        c = {"app": "chat", "alive": True, "sent": {}, "ready": True, "proto": 2, "restTabs": True,
             "send": lambda s: frames.append(json.loads(s)), "_frames": frames}
        saved = (km._sessions, km._parse, km._turn_index_of_events, km.WIRE_TAIL)
        km._sessions = lambda now: [{"sid": sid, "path": path}]
        km._parse = lambda p, s_, now: {"turns": []}
        real_index = km._turn_index_of_events

        def slow_index(evs, turns):
            time.sleep(0.004)                                # the parse a large session costs: both senders are inside it at once
            return real_index(evs, turns)
        km._turn_index_of_events = slow_index
        km.WIRE_TAIL = 250
        versions, keys = [], ["k%d" % i for i in range(4)]
        for v in range(1, 61):
            if v % 7 == 0:                                   # a rewind: the last two events leave and new ones come
                keys = keys[:-2] + ["r%d-%d" % (v, i) for i in range(3)]
            else:
                keys = keys + ["k%d-%d" % (v, i) for i in range(2)]
            evs = [{"kind": "assistant", "uuid": k, "md": k} for k in keys]
            versions.append({"type": "session", "id": sid, "name": "web", "events": evs, "status": {"state": "working", "sinceEpoch": None},
                             "ledger": {"summary": "", "tree": [], "current": None, "recent": [], "workingNote": "", "needsInput": None},
                             "wm": {"leaf": "L", "tx": [[v, v]], "live": None}})

        def sender(offset):
            prev = None
            for i, m in enumerate(versions):
                if (i + offset) % 3 == 0:
                    continue                                 # each sender skips some builds, so the two interleave out of step
                evs = m["events"]
                change = 0
                if prev is not None:
                    change = next((j for j, (a, b) in enumerate(zip(prev, evs)) if a["uuid"] != b["uuid"]), min(len(prev), len(evs)))
                km._send_chat_or_status(c, m, None, change, False)
                prev = evs
        try:
            ts = [threading.Thread(target=sender, args=(k,)) for k in (0, 1)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(60)
            self.assertFalse(any(t.is_alive() for t in ts))
        finally:
            km._sessions, km._parse, km._turn_index_of_events, km.WIRE_TAIL = saved
        held, last_v, fulls, tails = None, 0, 0, 0
        for f in frames:
            v = f["wm"]["tx"][0][0]
            self.assertGreaterEqual(v, last_v, "a frame older than one the page already applied: %d after %d" % (v, last_v))
            last_v = v
            if f["type"] == "session":
                fulls += 1
                held = [e["uuid"] for e in f["events"]]
            else:
                tails += 1
                self.assertIsNotNone(held, "a tail before any base")
                self.assertIn(f["afterUuid"], held, "a tail that names a key the page does not hold: its base never arrived")
                held = held[:held.index(f["afterUuid"]) + 1] + [e["uuid"] for e in f["events"]]
        self.assertGreater(fulls, 3, "the rewinds sent fulls: %d fulls, %d tails" % (fulls, tails))
        self.assertGreater(tails, 10)
        self.assertEqual(held, [e["uuid"] for e in versions[-1]["events"]], "and the page ends on the newest list, whole")

    def test_67_an_ask_does_not_build_or_wait_for_a_tab_that_has_an_ask_of_its_own(self):
        # a repair push serves, beside its tab, every tab the strip lists that the page holds in no form (a session created
        # since its last push). A tab the page has ASKED for is held in no form too, so the second click's push took the
        # first click's tab along: it waited out that build, then built the transcript a second time
        import threading
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2); self._move(S3)
        km._push([c])                                        # api and tests at rest
        c["_frames"].clear()
        del self.built[:]
        gate, started = threading.Event(), threading.Event()
        real = km.build_session

        def slow(sid, now, live_map=None, **kw):
            if sid == S2:
                started.set()
                gate.wait(20)
            return real(sid, now, live_map, **kw)
        km.build_session = slow
        lane = km._ws_arm_lane(c)
        try:
            put = lambda msg: km._lane_put(lane, msg["type"], lambda: km.Handler._dispatch_ws(_Self(), msg, c))
            put({"type": "activeTab", "id": S2}); put({"type": "needFull", "id": S2, "why": "rest-open"})
            self.assertTrue(started.wait(10))
            put({"type": "activeTab", "id": S3}); put({"type": "needFull", "id": S3, "why": "rest-open"})
            deadline = time.time() + 10
            while time.time() < deadline and not self._frames(c, "session", S3):
                time.sleep(0.02)
            self.assertEqual(len(self._frames(c, "session", S3)), 1)
            deadline = time.time() + 3
            while time.time() < deadline and c["repairs"]["running"] != 1:
                time.sleep(0.02)
            self.assertEqual(c["repairs"]["running"], 1, "the second click's thread is done: it did not stay behind the first tab's build")
            gate.set()
            deadline = time.time() + 10
            while time.time() < deadline and not self._frames(c, "session", S2):
                time.sleep(0.02)
            self.assertEqual(len(self._frames(c, "session", S2)), 1)
            self.assertEqual(self.built.count(S2), 1, "and the first tab was built once: %r" % self.built)
        finally:
            gate.set()
            km.build_session = real
            km._lane_close(c)
            c.pop("lane", None); c.pop("closing", None)
            deadline = time.time() + 5
            while time.time() < deadline and (c.get("repairs") or {}).get("running"):
                time.sleep(0.02)

    # ── 3: a third quick click ──
    def test_65_a_third_quick_click_on_a_tab_the_kernel_holds_a_parse_of_is_answered_while_two_builds_run(self):
        # …and one on a tab never parsed waits for one of the two, as before: a cold build reads the whole transcript,
        # gigabytes for a large one, so cold builds stay two at a time
        for warm in (True, False):
            with self.subTest(warm=warm):
                self._three_clicks(warm)

    def _three_clicks(self, cold):
        import threading
        cold = bool(cold)                                    # (the parameter says whether the third tab is WARM)
        real_warm = km._repair_warm
        km._repair_warm = lambda sid: cold and sid == S4     # the kernel holds a parse of the third tab's transcript, or none
        self.addCleanup(setattr, km, "_repair_warm", real_warm)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear(); km._chat_baseline_raced.clear()
        km._chat_rested.clear()
        del km._clients[:]
        p4 = os.path.join(self.tmp, S4 + ".jsonl")
        with open(p4, "w") as f:
            f.write("x" * 100)
        self.paths[S4], self.n[S4] = p4, 4
        NAMES[S4] = "docs"
        self.live[S4] = dict(self.live[S1])
        rows = [{"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in TAB_ORDER + [S4]]
        km._chat_tab_sessions = lambda now, live_map: list(rows)
        c = self._client(active=S1)
        km._clients[:] = [c]
        km._push([c])                                        # every tab whole
        for sid in (S2, S3, S4):
            self._move(sid)
        km._push([c])                                        # api, tests and docs at rest
        self.assertEqual(sorted(f["id"] for f in self._frames(c, "status") if f.get("rest")), [S2, S3, S4])
        c["_frames"].clear()
        gate, started = threading.Event(), {S2: threading.Event(), S3: threading.Event()}
        real = km.build_session

        def slow(sid, now, live_map=None, **kw):
            if sid in started:                               # two large tabs: each build takes as long as the test lets it
                started[sid].set()
                gate.wait(30)
            return real(sid, now, live_map, **kw)
        km.build_session = slow
        lane = km._ws_arm_lane(c)
        try:
            put = lambda msg: km._lane_put(lane, msg["type"], lambda: km.Handler._dispatch_ws(_Self(), msg, c))
            for sid in (S2, S3):                             # two quick clicks on large tabs…
                put({"type": "activeTab", "id": sid}); put({"type": "needFull", "id": sid, "why": "rest-open"})
                self.assertTrue(started[sid].wait(10), "its build is under way")
            put({"type": "activeTab", "id": S4}); put({"type": "needFull", "id": S4, "why": "rest-open"})   # …then a small one
            deadline = time.time() + (10 if cold else 1.5)
            while time.time() < deadline and not self._frames(c, "session", S4):
                time.sleep(0.02)
            if cold:
                self.assertEqual(len(self._frames(c, "session", S4)), 1, "the tab on screen is answered while both earlier builds still run")
                self.assertEqual(self._frames(c, "session", S2) + self._frames(c, "session", S3), [])
                self.assertEqual(km.REPAIR_BUILDS_MAX, 3)
                deadline = time.time() + 5
                while time.time() < deadline and c["repairs"].get("oneShot"):
                    time.sleep(0.02)
                self.assertEqual((c["repairs"]["running"], c["repairs"].get("oneShot")), (2, 0),
                                 "the third thread answered that one ask and ended: it takes no ask for a tab the page left")
            else:
                self.assertEqual(self._frames(c, "session", S4), [], "a tab never parsed waits for one of the two, as before")
                self.assertEqual(c["repairs"]["running"], 2)
            gate.set()
            deadline = time.time() + 10
            while time.time() < deadline and not self._frames(c, "session", S4):
                time.sleep(0.02)
            self.assertEqual(len(self._frames(c, "session", S4)), 1, "…and is answered")
            deadline = time.time() + 10
            while time.time() < deadline and not (self._frames(c, "session", S2) and self._frames(c, "session", S3)):
                time.sleep(0.02)
            self.assertEqual((len(self._frames(c, "session", S2)), len(self._frames(c, "session", S3))), (1, 1), "the asks for the tabs it left are still answered")
        finally:
            gate.set()
            km.build_session = real
            km._lane_close(c)
            c.pop("lane", None); c.pop("closing", None)
            deadline = time.time() + 5
            while time.time() < deadline and (c.get("repairs") or {}).get("running"):
                time.sleep(0.02)
            NAMES.pop(S4, None)


S5 = "11111111-2222-3333-4444-121212121215"   # ops: a fifth tab


class AsksAndReads(_Base):
    """What the review of the change above found (2026-11-16): an ask left to the pusher behind the one-shot thread; a
    transcript that cannot be read building to its live cards; a failed read remembered under the file's stat; a counter
    bumped under a lock once per turn; and a /clear on a tab at rest, which must not be held back."""

    def setUp(self):
        super().setUp()
        self.notices = []
        self._real_notice = km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": self.notices.append((text, ok, kind))

    def tearDown(self):
        km._sync_notice = self._real_notice
        km._chat_rested.clear()
        km._chat_build_faults.clear()
        km._EMPTY_BUILD_NOTED.clear(); km._EMPTY_BUILD_BELLED.clear()
        for sid in (S4, S5):
            NAMES.pop(sid, None)
        super().tearDown()

    def _more_tabs(self, **names):
        """Tabs beside the base's three: {sid: name}. Returns the strip's rows."""
        for sid, name in names.items():
            path = os.path.join(self.tmp, sid + ".jsonl")
            with open(path, "w") as f:
                f.write("x" * 100)
            self.paths[sid], self.n[sid] = path, 4
            NAMES[sid] = name
            self.live[sid] = dict(self.live[S1])
        rows = [{"sid": sid, "name": NAMES[sid], "path": self.paths[sid], "anchor": sid} for sid in TAB_ORDER + list(names)]
        km._chat_tab_sessions = lambda now, live_map: list(rows)
        return rows

    def _wait(self, cond, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline and not cond():
            time.sleep(0.02)
        return cond()

    # ── 1: an ask is never left behind the one-shot thread ──
    def test_70_an_ask_that_arrives_while_only_the_one_shot_thread_runs_gets_a_thread_of_its_own(self):
        import threading
        self._more_tabs(**{S4: "docs", S5: "ops"})
        real_warm = km._repair_warm
        km._repair_warm = lambda sid: sid == S4              # the kernel holds a parse of docs
        self.addCleanup(setattr, km, "_repair_warm", real_warm)
        c = self._client(active=S1)
        km._clients[:] = [c]
        km._push([c])
        for sid in (S2, S3, S4, S5):
            self._move(sid)
        km._push([c])                                        # the four others at rest
        self.assertEqual(sorted(f["id"] for f in self._frames(c, "status") if f.get("rest")), [S2, S3, S4, S5])
        c["_frames"].clear()
        gates = {sid: threading.Event() for sid in (S2, S3, S4)}
        started = {sid: threading.Event() for sid in (S2, S3, S4)}
        real = km.build_session

        def slow(sid, now, live_map=None, **kw):
            if sid in gates and not gates[sid].is_set():     # each of these builds takes as long as the test lets it
                started[sid].set()
                gates[sid].wait(30)
            return real(sid, now, live_map, **kw)
        km.build_session = slow
        lane = km._ws_arm_lane(c)
        try:
            put = lambda msg: km._lane_put(lane, msg["type"], lambda: km.Handler._dispatch_ws(_Self(), msg, c))
            for sid in (S2, S3, S4):                         # two clicks, then a third on a tab the kernel holds a parse of
                put({"type": "activeTab", "id": sid}); put({"type": "needFull", "id": sid, "why": "rest-open"})
                self.assertTrue(started[sid].wait(10), "its build is under way")
            r = c["repairs"]
            self.assertEqual((r["running"], r.get("oneShot")), (2, 1), "two ordinary threads and the one for the tab on screen")
            gates[S2].set(); gates[S3].set()                 # the first two builds end; their threads find nothing waiting and end
            self.assertTrue(self._wait(lambda: r["running"] == 0), "only the one-shot thread is left: %r" % r)
            self.assertEqual(r.get("oneShot"), 1)
            # an ask for a tab the page is NOT showing (its idle prefetch, a second column's): the real dispatch
            put({"type": "needFull", "id": S5, "why": "prefetch"})
            self.assertTrue(self._wait(lambda: bool(self._frames(c, "session", S5)), 5),
                            "answered by a thread of its own while the one-shot is still building: not left to the pusher's next cycle")
            self.assertEqual(self._frames(c, "session", S4), [], "…which it still is")
            self.assertTrue(self._wait(lambda: S5 not in r["pending"] and r["running"] == 0), "and nothing stays listed: %r" % r)
            gates[S4].set()
            self.assertTrue(self._wait(lambda: bool(self._frames(c, "session", S4))))
            self.assertTrue(self._wait(lambda: not r.get("oneShot") and not r["pending"]))
        finally:
            for g in gates.values():
                g.set()
            km.build_session = real
            km._lane_close(c)
            c.pop("lane", None); c.pop("closing", None)
            self._wait(lambda: not (c.get("repairs") or {}).get("running") and not (c.get("repairs") or {}).get("oneShot"), 5)

    # ── 2: a read that fails stops the build, whatever cards the session carries ──
    def _cards_only(self, sid):
        """What a build of a transcript that could not be read came to when it went on: the head card and the live card."""
        return {"type": "session", "id": sid, "name": NAMES[sid],
                "events": [{"kind": "system", "uuid": "system:head", "model": "", "cwd": "~/proj"},
                           {"kind": "apiError", "uuid": "apiError", "text": "the agent backend is not installed"}],
                "status": {"state": "blocked", "sinceEpoch": None},
                "ledger": {"summary": "", "tree": [], "current": None, "recent": [], "workingNote": "", "needsInput": None}}

    def test_71_a_transcript_that_cannot_be_read_sends_nothing_for_a_tab_at_rest_or_a_watched_one_and_is_said_once(self):
        c = self._client()
        self._warm(c)
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S1}, c)   # web on screen: among the tabs the page watches
        self._move(S2)
        km._push([c])                                        # api at rest; web holds its base, 5 events on the page
        self.assertEqual([f.get("rest") for f in self._frames(c, "status", S2)], [True])
        c["_frames"].clear()
        unreadable, strict = set(), []
        real = km.build_session

        def build(sid, now, live_map=None, **kw):
            if sid in unreadable:
                strict.append(bool(getattr(km._BUILD_READ_STRICT, "on", False)))
                if strict[-1]:                               # the real build stops at the read under the push roads' flag…
                    raise km.TranscriptUnreadable("its transcript cannot be read (Permission denied)")
                return self._cards_only(sid)                 # …and without it went on to the live cards (what main sent)
            return real(sid, now, live_map, **kw)
        km.build_session = build
        try:
            unreadable.update((S1, S2))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
                km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "rest-open"}, c)   # the tab at rest, opened
                for _ in range(3):
                    self._move(S1); self._move(S2)
                    km._push([c])                            # …and the cycles, for it and for the tab watched all along
                km._push_session_now(S1)                     # the per-session road too
            self.assertTrue(strict and all(strict), "both push roads build under the flag: %r" % strict)
            self.assertEqual([f for f in c["_frames"] if f["type"] in ("session", "chatTail") and f.get("id") in (S1, S2)], [],
                             "no frame for either: the page keeps the transcript it holds, not two cards in its place")
            self.assertIn(S2, c.get("askedFull") or (), "the ask stands, so the tab at rest goes on saying it is catching up")
            self.assertIn(S1, c["echat"], "and the watched tab keeps its base")
            for sid, name in ((S1, "web"), (S2, "api")):
                mine = [n for n in self.notices if n[0].startswith("chat: %s is not updating" % name)]
                self.assertEqual(len(mine), 1, "said once for %s, not once per cycle: %r" % (name, self.notices))
                self.assertEqual(mine[0][1:], (False, "refused"))
                self.assertIn("cannot be read (Permission denied)", mine[0][0]); self.assertIn("until a read succeeds", mine[0][0])
            self.assertEqual(err.getvalue().count("push build: chat"), 2, "one stderr line each, and no traceback: %r" % err.getvalue()[:300])
            self.assertNotIn("Traceback", err.getvalue())
            # the read succeeds: the tab at rest gets its full, the watched one its delta, and a later failure is said anew
            unreadable.clear()
            self._move(S1); self._move(S2)
            km._push([c])
            self.assertEqual(len(self._frames(c, "session", S2)), 1)
            self.assertEqual(len(self._frames(c, "chatTail", S1)), 1)
            self.assertNotIn(S1, km._chat_build_faults); self.assertNotIn(S2, km._chat_build_faults)
        finally:
            km.build_session = real

    # ── 2 and 3, at the read itself: real files, the real parse ──
    def _transcript(self, path, sid, upto):
        prev = None
        with open(path, "w") as f:
            for i in range(1, upto + 1):
                u = "11111111-2222-3333-4444-7%011d" % i
                f.write(json.dumps({"type": "user" if i % 2 else "assistant", "uuid": u, "parentUuid": prev, "sessionId": sid,
                                    "timestamp": "2026-11-01T00:00:%02dZ" % i,
                                    "message": ({"role": "user", "content": "question %d about the notes api" % i} if i % 2 else
                                                {"role": "assistant", "content": [{"type": "text", "text": "answer %d" % i}]})}) + "\n")
                prev = u

    def test_72_a_failed_read_is_named_by_the_parse_not_kept_and_a_file_never_read_is_not_a_failure(self):
        sid = "11111111-2222-3333-4444-121212121272"       # a private sid: the parse store is the kernel's own
        path = os.path.join(self.tmp, sid + ".jsonl")
        now = int(time.time())
        self._transcript(path, sid, 6)
        tree = km._parse(path, sid, now)
        self.assertTrue(tree.get("turns"), "the transcript parses")
        self.assertIsNone(km._transcript_read_failed(path, tree))
        if os.geteuid() != 0:                                # (a superuser reads through any mode)
            self._transcript(path, sid, 8)                   # the session wrote more, and the file cannot be read
            os.chmod(path, 0)
            try:
                bad = km._parse(path, sid, now)
                self.assertFalse(bad.get("turns"))
                self.assertEqual(km._transcript_read_failed(path, bad), "its transcript cannot be read (Permission denied)",
                                 "the parse names the read that failed: an empty tree is not the session's word")
                self.assertEqual(km._transcript_read_failed(path, km._parse(path, sid, now)), "its transcript cannot be read (Permission denied)")
            finally:
                os.chmod(path, 0o644)
            # readable again with the SAME bytes, so the same mtime and size: the failed read was not kept under them
            again = km._parse(path, sid, now)
            self.assertTrue(again.get("turns"), "a later read is tried, and reads")
            self.assertIsNone(km._transcript_read_failed(path, again))
        # moved aside: a file this kernel read with content is gone
        os.rename(path, path + ".aside")
        gone = km._parse(path, sid, now)
        self.assertFalse(gone.get("turns"))
        self.assertEqual(km._transcript_read_failed(path, gone), "its transcript is missing")
        os.rename(path + ".aside", path)
        self.assertIsNone(km._transcript_read_failed(path, km._parse(path, sid, now)))
        # a file never read is a session that has not written yet, and a /clear or a resume names a NEW file: no failure
        fresh = os.path.join(self.tmp, "11111111-2222-3333-4444-121212121273.jsonl")
        self.assertIsNone(km._transcript_read_failed(fresh, km._parse(fresh, sid, now)),
                          "the session's next transcript, not written yet: its head cards and boundary card are its display")

    def test_75_a_read_that_fails_midway_is_not_repeated_every_cycle_and_is_tried_again_at_an_event(self):
        # the review of the change above: a read that fails AFTER it pulled bytes (a device error near the end of a large
        # file on network storage) was repeated whole at every ask and every cycle. A failed read is remembered under the
        # file's stat with its inode change time, and tried again when that moves or when a page asks for the tab
        import errno
        em = km.em
        sid = "11111111-2222-3333-4444-121212121275"
        path = os.path.join(self.tmp, sid + ".jsonl")
        self._transcript(path, sid, 400)
        size = os.path.getsize(path)
        fail_at = size - 40                                  # the read fails inside the last record
        pulled, broken = [0], [True]
        real_open = open

        class Failing:
            """The transcript's file object: every byte it hands out is counted, and while `broken` a read that reaches
            `fail_at` raises as a failing device does."""
            def __init__(self, fh):
                self.fh = fh

            def _take(self, data):
                if broken[0] and self.fh.tell() >= fail_at:
                    raise OSError(errno.EIO, "Input/output error")
                pulled[0] += len(data)
                return data

            def readline(self, *a):
                return self._take(self.fh.readline(*a))

            def read(self, *a):
                return self._take(self.fh.read(*a))

            def __getattr__(self, name):
                return getattr(self.fh, name)

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                self.fh.close()
                return False

        def fake_open(file, mode="r", *a, **kw):
            fh = real_open(file, mode, *a, **kw)
            return Failing(fh) if str(file) == path and "b" in mode else fh
        em.open = fake_open
        self.addCleanup(lambda: em.__dict__.pop("open", None))
        now = int(time.time())
        km._PERF_STATS.reset()
        first = km._parse(path, sid, now)
        self.assertEqual(km._transcript_read_failed(path, first, sid), "its transcript cannot be read (Input/output error)")
        once = pulled[0]
        self.assertGreater(once, size // 2, "the read pulled most of the file before it failed: %d of %d" % (once, size))
        for _ in range(10):                                  # ten cycles over the same failing file
            self.assertTrue(km._parse(path, sid, now).get("_readFailed"))
        self.assertEqual(pulled[0], once, "not read again: %d bytes over ten cycles beyond the first attempt's %d" % (pulled[0] - once, once))
        whole = km._PERF_STATS.snapshot()["parses"]["wholeBytes"]
        self.assertLessEqual(whole, once, "/perf books what the one attempt pulled (%d), not the file's size per attempt: %d" % (once, whole))
        self.assertGreater(whole, size // 2)
        # EVENT: a page opens the tab (its activeTab, its needFull): one more attempt
        c = self._client()
        km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": sid}, c)
        self.assertTrue(km._parse(path, sid, now).get("_readFailed"))
        after_open = pulled[0]
        self.assertGreater(after_open, once, "the page's open tries the read again")
        self.assertLessEqual(after_open, 3 * once, "one attempt (the fold of the file, then the whole read it falls back to)")
        for _ in range(10):
            km._parse(path, sid, now)
        self.assertEqual(pulled[0], after_open, "and the cycles after it read nothing")
        km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": sid, "why": "rest-open"}, c)   # its ask is such an act too
        self.assertTrue(km._parse(path, sid, now).get("_readFailed"))
        self.assertGreater(pulled[0], after_open)
        # EVENT: the file's stat moves with the SAME bytes (its permissions restored, a rename back: the inode change time)
        broken[0] = False
        self.assertTrue(km._parse(path, sid, now).get("_readFailed"), "nothing moved yet: the remembered failure stands")
        os.chmod(path, 0o644)
        good = km._parse(path, sid, now)
        self.assertFalse(good.get("_readFailed"))
        self.assertTrue(good.get("turns"), "read again at once, with no append")
        self.assertIsNone(km._transcript_read_failed(path, good, sid))
        self.assertIs(km._parse(path, sid, now), good, "and a read that succeeded is kept as before")

    def test_76_a_session_being_moved_is_not_a_missing_transcript(self):
        # a move renames the transcript and writes the session's folder record just after: between the two the old path
        # is gone and the kernel holds a parse of it. The registry marks the move (cwdPending), and once the record is
        # written it names the transcript's new place
        sid = "11111111-2222-3333-4444-121212121276"
        old = os.path.join(self.tmp, sid + ".jsonl")
        self._transcript(old, sid, 6)
        now = int(time.time())
        self.assertTrue(km._parse(old, sid, now).get("turns"))
        moved = os.path.join(self.tmp, "moved-" + sid + ".jsonl")
        os.rename(old, moved)                                # the CLI moved the file
        gone = km._parse(old, sid, now)
        saved = (km._thread_reg, km._session_row)
        try:
            km._thread_reg = lambda tsid: {"sid": tsid, "cwd": "~/proj", "cwdPending": "~/proj-next"}
            km._session_row = lambda tsid, now=None: {"sid": tsid, "path": old}
            self.assertIsNone(km._transcript_read_failed(old, gone, sid), "the move is under way: not a failure")
            km._thread_reg = lambda tsid: {"sid": tsid, "cwd": "~/proj-next"}
            km._session_row = lambda tsid, now=None: {"sid": tsid, "path": moved}
            self.assertIsNone(km._transcript_read_failed(old, gone, sid),
                              "the record is written and names the new place: the path in hand is last cycle's")
            km._session_row = lambda tsid, now=None: {"sid": tsid, "path": old}
            self.assertEqual(km._transcript_read_failed(old, gone, sid), "its transcript is missing",
                             "no move, and the record still names this file: it is gone")
        finally:
            km._thread_reg, km._session_row = saved

    # ── 5: a /clear on a tab at rest ──
    def test_73_a_cleared_sessions_new_display_reaches_a_tab_at_rest(self):
        c = self._client(active=S1)
        self._warm(c)
        self._move(S2)
        km._push([c])
        self.assertGreater(km._chat_rested.get(S2) or 0, 2, "the record of what its pages held: more than the cleared display has")
        c["_frames"].clear()
        real = km.build_session

        def cleared(sid, now, live_map=None, **kw):
            if sid != S2:
                return real(sid, now, live_map, **kw)
            m = real(sid, now, live_map, **kw)               # the session after /clear: the boundary card and the head card,
            m["events"] = [{"kind": "clear", "uuid": "clear:11111111-2222-3333-4444-7000000000c1", "episodes": 2},   # no turn yet
                           {"kind": "system", "uuid": "system:head", "model": "", "cwd": "~/proj"}]
            return m
        km.build_session = cleared
        try:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                km.Handler._dispatch_ws(_Self(), {"type": "activeTab", "id": S2}, c)
                km.Handler._dispatch_ws(_Self(), {"type": "needFull", "id": S2, "why": "rest-open"}, c)
        finally:
            km.build_session = real
        full = self._frames(c, "session", S2)
        self.assertEqual(len(full), 1, "the cleared conversation is the session's word: sent, not held back as a failed read")
        self.assertEqual([e["kind"] for e in full[0]["events"]], ["clear", "system"])
        self.assertNotIn(S2, c.get("askedFull") or ())
        self.assertEqual(self.notices, []); self.assertNotIn("EMPTY", err.getvalue())

    # ── 4: the task fold books its counts once a build ──
    def test_74_the_task_fold_takes_the_fold_lock_once_a_build_not_once_a_turn(self):
        sid = "11111111-2222-3333-4444-121212121274"
        turns = [{"id": "t%d" % i, "atoms": [{"uuid": "11111111-2222-3333-4444-8%011d" % i, "t": i, "kind": "assistant"}]} for i in range(60)]
        saved = (dict(km._task_fold_stats), km._chat_memo_bump)
        calls = []
        km._chat_memo_bump = lambda stats, key, n=1: (calls.append((key, n)), saved[1](stats, key, n))[1]
        for k in km._task_fold_stats:
            km._task_fold_stats[k] = 0
        try:
            self.assertIsNone(km._fold_tasks({"turns": turns}, sid))          # sixty turns scanned
            self.assertEqual((calls, dict(km._task_fold_stats)), ([("miss", 60)], {"hit": 0, "miss": 60}), "one booking, the same count")
            del calls[:]
            km._fold_tasks({"turns": turns}, sid)                               # the same turns: every one served from the memo
            self.assertEqual((calls, dict(km._task_fold_stats)), ([("hit", 60)], {"hit": 60, "miss": 60}))
        finally:
            km._chat_memo_bump = saved[1]
            km._task_fold_stats.clear(); km._task_fold_stats.update(saved[0])
            km._task_fold_memo.pop(sid, None)


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
