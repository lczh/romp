#!/usr/bin/env python3
"""The work a connect push does, pinned by counts (2026-10-10). A page's `ready` and every `needFull` run Handler._push_one on the
socket's lane thread, which holds no pusher-cycle scope. Two costs scaled with the board there: every read below the push that the
pusher's cycle shares (the liveness map, discovery) was taken afresh, several times per tab (a synthetic board of 38 sessions read
the liveness map 381 times and walked discovery 82 times in one cold push; on the main machine a liveness read costs about 27 ms);
and a needFull, which asks for ONE tab, ran the whole board push, so the page's idle prefetch (one needFull per skeleton tab)
cost N whole-board pushes to fill N tabs. Now the connect push opens the cycle's scopes once (_connect_push_scope) and a needFull
serves the asked tab alone (_push's `only`). Pinned here: one liveness read and one discovery walk per connect push whatever the
board's size; a needFull that signs and builds only the asked tab and hands the page one frame; the scopes closed on the lane after
the push and a thread that holds a snapshot left as it was; and the board the page ends with after its prefetch equal to the board
a page served whole receives. Drives the REAL _push over fake clients with build_session stubbed (the build's own liveness and
discovery reads mimicked), real temp transcript files; synthetic only (the notes-api demo world, placeholder ids)."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
with open(os.path.join(os.environ["XDG_STATE_HOME"], "romp", "session-hosts"), "w") as _f:
    _f.write("off")                       # a minted state root pins the hosts off (the 2026-09-11 rule)
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_connect_push_work", os.path.join(BIN, "romp-kernel"))

NAMES = ["web", "api", "tests", "docs", "search", "deploy", "notes", "auth"]
SIDS = ["11111111-2222-3333-4444-7777777777%02d" % i for i in range(len(NAMES))]
NAME = dict(zip(SIDS, NAMES))
WATCHED = SIDS[0]


def _on_lane(fn):
    """Run fn on a fresh thread, as the socket lane runs a connect push: no pusher-cycle scope is open there."""
    box = {}

    def run():
        try:
            box["out"] = fn()
        except BaseException as e:   # noqa: BLE001 - re-raised on the caller's thread
            box["err"] = e
    t = threading.Thread(target=run, name="ws-lane")
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]
    return box.get("out")


class ConnectPushWork(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = {}
        for i, sid in enumerate(SIDS):
            p = os.path.join(self.tmp, sid + ".jsonl")
            with open(p, "w") as f:
                f.write("x" * (1000 + 100 * i))            # sizes rank the skeleton set; content is never parsed (build stubbed)
            self.paths[sid] = p
        self.live = {sid: {"state": "waiting", "since": 1781100000, "model": "", "effort": "", "mode": "", "backend": "sdk"}
                     for sid in SIDS}
        self.calls = {"live": 0, "discover": 0, "sig": [], "build": []}
        self._saved = (km._chat_tab_sessions, km.Sessions.__dict__["live"], km.jd.discover, km._cached_feed, km.build_session,
                       km._chat_build_sig, km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, list(km._clients))

        def live():
            self.calls["live"] += 1
            return {k: dict(v) for k, v in self.live.items()}
        km.Sessions.live = staticmethod(live)

        def discover(now, window=None, forks=True):
            self.calls["discover"] += 1
            return [(sid, self.paths[sid], sid, NAME[sid]) for sid in SIDS]
        km.jd.discover = discover

        def tabs(now, live_map):
            km._sessions(now)                              # the tab list's own discovery read, as the real one takes
            return [{"sid": sid, "name": NAME[sid], "path": self.paths[sid], "anchor": sid} for sid in SIDS]
        km._chat_tab_sessions = tabs
        km._cached_feed = lambda *a, **k: None

        def build(sid, now, live_map=None, **kw):
            # the real build reads liveness several times per tab (its background-task and awaiting reads) and discovery
            # for its paths: mimicked, so the counts below say whether those reads were served from one snapshot
            for _ in range(3):
                km._live_map()
            km._sessions(now)
            self.calls["build"].append(sid)
            return {"type": "session", "id": sid, "name": NAME[sid],
                    "events": [{"kind": "assistant", "uuid": "%s-u%d" % (sid[-2:], i), "md": "reply %d" % i} for i in range(4)],
                    "status": {"state": "ready", "sinceEpoch": None}, "ledger": None}
        km.build_session = build
        real_sig = self._saved[5]

        def sig(s, *a, **k):
            self.calls["sig"].append(s["sid"])
            return real_sig(s, *a, **k)
        km._chat_build_sig = sig
        km._comments_frame = lambda sid, live_map: None
        km._push_subagents = lambda clients, now, live_map: None
        km.NAMES = Path(self.tmp) / "names"; km.NAMES.mkdir()
        km.jd.STATE = Path(self.tmp) / "state"; km.jd.STATE.mkdir(parents=True, exist_ok=True)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        del km._clients[:]
        km._pusher_wake.clear()
        km._PERF_STATS.reset()
        self.handler = km.Handler.__new__(km.Handler)       # the socket's dispatch: the needFull arm as the lane runs it

    def tearDown(self):
        (km._chat_tab_sessions, live, km.jd.discover, km._cached_feed, km.build_session, km._chat_build_sig,
         km._comments_frame, km._push_subagents, km.NAMES, km.jd.STATE, clients) = self._saved
        km.Sessions.live = live
        del km._clients[:]; km._clients.extend(clients)
        km._built_chat.clear(); km._prev_chat_events.clear(); km._prev_chat_ledger.clear()
        km._PERF_STATS.reset()

    def _client(self, **kw):
        frames = []
        c = {"app": "chat", "alive": True, "sent": {}, "proto": 2, "handshake": True, "ready": True,
             "send": lambda s: frames.append(json.loads(s)), "_frames": frames}
        c.update(kw)
        return c

    @staticmethod
    def _sessions_of(c):
        return [f for f in c["_frames"] if f["type"] == "session"]

    def _connect(self, c):
        return _on_lane(lambda: km.Handler._push_one(self.handler, c))

    def _need_full(self, c, sid):
        return _on_lane(lambda: self.handler._dispatch_ws({"type": "needFull", "id": sid, "why": "prefetch"}, c))

    def _reset_calls(self):
        self.calls.update({"live": 0, "discover": 0, "sig": [], "build": []})

    def test_01_a_connect_push_reads_liveness_once_and_walks_discovery_once_whatever_the_board(self):
        c = self._client(active=WATCHED)                    # a page that declared no diet: every tab built
        km._clients[:] = [c]
        self._connect(c)
        self.assertEqual(sorted(self.calls["build"]), sorted(SIDS), "every tab built: %r" % self.calls["build"])
        self.assertEqual(self.calls["live"], 1, "ONE liveness read for the whole push (%d tabs, three build reads each): %d"
                         % (len(SIDS), self.calls["live"]))
        # the push's rows (the tab list and every build) share ONE walk; the feed's view signature asks discover() directly once
        # (_producer_sig, served by discover's own fingerprint cache), whatever the board's size
        self.assertLessEqual(self.calls["discover"], 2, "one discovery walk for the push's rows, plus the feed signature's: %d"
                             % self.calls["discover"])
        self.assertEqual({s["id"] for s in self._sessions_of(c)}, set(SIDS), "the page was served the whole board")

    def test_02_the_scopes_close_on_the_lane_after_the_push_and_a_thread_holding_a_snapshot_is_left_alone(self):
        c = self._client(active=WATCHED)
        km._clients[:] = [c]

        def push_then_look():
            km.Handler._push_one(self.handler, c)
            return {k: getattr(km._live_scope, k, None) for k in ("snapshot", "snapshot_t", "names", "paths", "sessions", "auth",
                                                                    "subagent_trees", "msgsum", "chat_shared")}
        left = _on_lane(push_then_look)
        self.assertEqual({k: v for k, v in left.items() if v is not None}, {}, "nothing the push opened outlives it on the lane")

        def inside_a_cycle():
            mine = {"sentinel": {"state": "waiting"}}
            km._live_scope.snapshot = mine
            try:
                with km._connect_push_scope() as lm:
                    return lm, km._live_scope.snapshot is mine
            finally:
                km._live_scope.snapshot = None
        lm, kept = _on_lane(inside_a_cycle)
        self.assertIsNone(lm, "a thread that holds a snapshot opens nothing")
        self.assertTrue(kept, "…and keeps its own snapshot")

    def test_03_a_needfull_signs_builds_and_sends_the_asked_tab_alone(self):
        c = self._client(reconnect=True, dietSkeleton=True, active=WATCHED)   # the skeleton=1 dial, past the ready arm
        km._clients[:] = [c]
        self._connect(c)
        self.assertEqual(self.calls["build"], [WATCHED], "the diet: the watched tab alone built: %r" % self.calls["build"])
        skel = list(c.get("skeletonOrder") or [])
        self.assertEqual(set(skel), set(SIDS) - {WATCHED})
        asked = skel[0]
        self._reset_calls()
        n0 = len(self._sessions_of(c))
        self._need_full(c, asked)
        self.assertEqual(self.calls["build"], [asked], "the asked tab built, no other: %r" % self.calls["build"])
        self.assertEqual(self.calls["sig"].count(asked), len(self.calls["sig"]), "only the asked tab signed: %r" % self.calls["sig"])
        self.assertLessEqual(len(self.calls["sig"]), 2, "its pre- and post-build signatures, nothing per board tab: %r" % self.calls["sig"])
        self.assertEqual(self.calls["live"], 1, "one liveness read for the repair: %d" % self.calls["live"])
        new = self._sessions_of(c)[n0:]
        self.assertEqual([f["id"] for f in new], [asked], "one full frame, the asked tab's")
        cp = km._PERF_STATS.snapshot()["pusher"]["connectPush"]
        self.assertEqual((cp["count"], cp["repair"]["count"]), (1, 1), "the repair is counted apart from the page load: %r" % cp)

    def test_04_the_prefetch_fills_the_board_in_n_repairs_of_one_tab_each(self):
        c = self._client(reconnect=True, dietSkeleton=True, active=WATCHED)
        km._clients[:] = [c]
        self._connect(c)
        skel = list(c.get("skeletonOrder") or [])
        self._reset_calls()
        for sid in skel:                                    # the page's idle prefetch: one needFull per skeleton tab, in order
            self._need_full(c, sid)
        self.assertEqual(self.calls["build"], skel, "each repair built its own tab once: %r" % self.calls["build"])
        self.assertLessEqual(len(self.calls["sig"]), 2 * len(skel), "signatures scale with the tabs filled, not tabs x board: %d for %d"
                             % (len(self.calls["sig"]), len(skel)))
        self.assertEqual(self.calls["live"], len(skel), "one liveness read per repair: %d" % self.calls["live"])
        self.assertEqual(self.calls["discover"], len(skel), "one discovery walk per repair: %d" % self.calls["discover"])
        self.assertEqual(c.get("skeleton") or set(), set(), "every skeleton released")
        # the board the page ends with equals the board a page served whole receives
        whole = self._client(active=WATCHED)
        km._clients[:] = [c, whole]
        self._connect(whole)

        def board(cl):
            out = {}
            for f in self._sessions_of(cl):
                out[f["id"]] = [e["uuid"] for e in f.get("events") or []]
            return out
        self.assertEqual(board(c), board(whole), "the dieted page, prefetched, holds the same board as a page served whole")
        self.assertEqual(set(board(c)), set(SIDS))


class FirstDialDiet(unittest.TestCase):
    """The client half (2026-10-10): the main chat pane dials the first socket of EVERY page load as a skeleton client, and the
    federated dial terms carry the page's diet to every linked kernel, ungated by the local socket's history."""

    def test_every_main_chat_page_load_dials_the_diet(self):
        chat = km._shim("chat")
        self.assertIn("var RESTART_DIET=false;if(!COL&&!SKEL){try{sessionStorage.removeItem('romp:reloadReason');}catch(e){}RESTART_DIET=true;}",
                      chat, "a main chat pane diets on every load, and consumes the reload record so it never outlives the load")
        self.assertIn('((SKEL||(RESTART_DIET&&!everConnected))?"&skeleton=1":"")', chat,
                      "the local dial states it on the page's first socket; a redial carries the diet through reconnect=1")
        for other in ("feed", "fleet", "timeline", "files", "settings"):
            self.assertIn("var RESTART_DIET=false;", km._shim(other), other)
            self.assertNotIn("RESTART_DIET=true", km._shim(other), other)

    def test_the_federated_dial_terms_carry_the_diet_to_every_linked_kernel(self):
        chat = km._shim("chat")
        terms = chat[chat.index("window.__rompDialTerms=function(){"):]
        terms = terms[:terms.index("};};") + 4]
        self.assertIn("skeleton:((SKEL||RESTART_DIET)?1:0)", terms,
                      "a linked kernel's dial carries the page's diet: the local socket has always opened before federation dials one")
        self.assertNotIn("everConnected", terms, "the remote term is not gated on the LOCAL socket's history")


if __name__ == "__main__":
    unittest.main()
