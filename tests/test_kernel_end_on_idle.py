"""Self-close, deferred to idle (the user 2026-08-15): telling a session "close yourself after you've
done this thing" never worked — an agent could only kill its own process, which romp read as a CRASH
and kept the session visible as dormant. Now `romp end self` records the sid (end-on-idle.json, a
STATE file so the wish survives kernel restarts) and the pusher's sweep gives it the dashboard ×'s
clean death the moment its turn settles — the goodbye delivered first, never mid-own-turn.
SYNTHETIC fixtures only."""
import json
import os
import tempfile
import threading
import types
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_endidle", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_endidle", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"   # a comment thread on SID's session


class _FakeBackend:
    def __init__(self):
        self.killed = []

    def kill(self, sid):
        self.killed.append(sid)
        return True


class EndOnIdle(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = {k: getattr(km, k) for k in
                      ("_parse", "_path_of", "_session_working", "_record_death",
                       "_comment_kill_all", "_send_to_app", "_push_soon")}
        self.saved_state = km.jd.STATE
        km.jd.STATE = Path(self.td.name)
        self.be = _FakeBackend()
        self.saved_backend_for = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._parse = lambda path, sid, now: {"turns": []}
        km._path_of = lambda sid, now=None: "/p"
        km._session_working = lambda turns: False
        self.deaths = []
        km._record_death = lambda sid, now, by: self.deaths.append((sid, by))
        km._comment_kill_all = lambda sid, be: None
        self.sent = []
        km._send_to_app = lambda app, m: self.sent.append((app, m))
        km._push_soon = lambda *a, **k: None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km.Sessions.backend_for = self.saved_backend_for
        km.jd.STATE = self.saved_state
        self.td.cleanup()

    def test_the_wish_survives_in_state_and_round_trips(self):
        km._end_on_idle_save({SID})
        self.assertEqual(km._end_on_idle_load(), {SID})
        self.assertEqual(json.loads((km.jd.STATE / "end-on-idle.json").read_text()), [SID])

    def test_the_sweep_kills_at_the_turns_settle_with_the_clean_death(self):
        km._end_on_idle_save({SID})
        km._end_on_idle_sweep(1000, {SID: {"state": ""}})
        self.assertEqual(self.be.killed, [SID])
        self.assertEqual(self.deaths, [(SID, "kill")], "the dashboard ×'s intentional death, never a crash")
        self.assertIn(("chat", {"type": "closed", "id": SID}), self.sent, "the tab closes like the × path")
        self.assertEqual(km._end_on_idle_load(), set(), "the wish is spent")

    def test_an_open_turn_defers_the_kill(self):
        km._session_working = lambda turns: True
        km._end_on_idle_save({SID})
        km._end_on_idle_sweep(1000, {SID: {"state": ""}})
        self.assertEqual(self.be.killed, [], "the turn it asked from is still open — its end is the event")
        self.assertEqual(km._end_on_idle_load(), {SID}, "the wish stands for the next sweep")

    def test_a_sid_already_dead_retires_its_request_without_a_kill(self):
        km._end_on_idle_save({SID})
        km._end_on_idle_sweep(1000, {})              # not in the live map → dead by some other path
        self.assertEqual(self.be.killed, [])
        self.assertEqual(self.deaths, [], "no second death record over whatever really happened")
        self.assertEqual(km._end_on_idle_load(), set())

    def test_the_route_and_spawn_env_wiring_is_pinned(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('b.get("when") == "idle"', src, "/end honors the deferral")
        self.assertIn('{"ok": True, "deferred": True}', src)
        self.assertIn("_end_on_idle_sweep(now, live_map)", src, "the sweep rides the pusher's tick jobs")
        sdk = Path(os.path.dirname(BIN), "kernel", "sdk_backend.py").read_text()
        self.assertIn('"ROMP_SID": str(sess.sid)', sdk,
                      "the CLI process carries the session's stable identity for `romp end self`")
        cli = Path(BIN, "romp").read_text()
        self.assertIn('"romp end self: ROMP_SID is not set', cli)


def _running(state):
    """A stand-in for a running SdkSession: its thread is up and its snapshot reads `state`."""
    return types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True),
                                 snapshot=lambda retry_on=None: {"state": state})


class ThreadSelfClose(unittest.TestCase):
    """`romp end self` from inside a comment thread (2026-11-01). The thread's CLI carries the thread's own sid, so the
    route records the thread and answers deferred, and the sweep then reads the live map, which leaves every thread out.
    The sweep took that absence for death and dropped the request at its first pass: the thread kept running and nobody
    was told. A real SDK backend over a test root owns the session `web` and its thread; the route is the real one over
    loopback, and the sweep reads the live map the jobs pass hands it (Sessions.live())."""

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        (root / "session-hosts").write_text("off\n")   # no real session host for a backend over a test root
        self.saved_state = km.jd.STATE
        km.jd._rebind_state(root)                       # one root for the kernel and the backend: the registry it reads
        self.be = sb.SdkBackend(root, "/bin/true", lambda *a, **k: None)
        self.be.spawn("web", str(root), sid=SID)
        self.be.fork("web-comment-1", SID, "a1", sid=THREAD, thread_of=SID)
        self.be.sessions[SID] = _running("idle")
        self.be.sessions[THREAD] = _running("working")
        self.killed = []
        self.be.kill = lambda sid: (self.killed.append(sid), True)[1]
        self.saved = {k: getattr(km, k) for k in
                      ("_sdk", "_codex", "_parse", "_path_of", "_session_working", "_record_death",
                       "_comment_kill_all", "_send_to_app", "_push_soon")}
        km._sdk = lambda: self.be
        km._codex = lambda: None
        self.working = True                                     # the turn that ran `romp end self` is still open
        km._parse = lambda path, sid, now: {"turns": [path]}
        km._path_of = lambda sid, now=None: "/TESTDIR/" + sid
        km._session_working = lambda turns: self.working
        self.deaths = []
        km._record_death = lambda sid, now, by: self.deaths.append((sid, by))
        km._comment_kill_all = lambda sid, be: None
        km._send_to_app = lambda app, m: None
        km._push_soon = lambda *a, **k: None

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(km, k, v)
        km._ending_sids.pop(THREAD, None)
        km.jd._rebind_state(self.saved_state)
        self.td.cleanup()

    def _end_self(self, sid):
        """What `romp end self` sends from a CLI whose ROMP_SID is `sid`."""
        req = urllib.request.Request("http://127.0.0.1:%d/end" % self.srv.server_address[1], method="POST",
                                     data=json.dumps({"id": sid, "when": "idle"}).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())

    def test_a_thread_that_ends_itself_is_ended_once_its_turn_settles(self):
        self.assertEqual(self.be.thread_of(THREAD), SID)
        self.assertEqual(self._end_self(THREAD), (200, {"ok": True, "deferred": True}))
        self.assertEqual(km._end_on_idle_load(), {THREAD})
        self.assertNotIn(THREAD, km.Sessions.live(), "a thread is never in the live map the sweep reads")
        km._end_on_idle_sweep(1000, km.Sessions.live())
        self.assertEqual(self.killed, [], "the turn it asked from is still open")
        self.assertEqual(km._end_on_idle_load(), {THREAD}, "the request stands for the next pass, never dropped")
        self.working = False
        self.be.sessions[THREAD] = _running("idle")
        km._end_on_idle_sweep(1001, km.Sessions.live())
        self.assertEqual(self.killed, [THREAD], "ended at its settle, as a tab session is")
        self.assertEqual(self.deaths, [(THREAD, "kill")])
        self.assertEqual(km._end_on_idle_load(), set(), "the request is spent")

    def test_a_thread_whose_cli_is_not_running_retires_its_request_without_a_kill(self):
        del self.be.sessions[THREAD]                            # ended some other way: resolved, deleted, its parent ended
        km._end_on_idle_save({THREAD})
        km._end_on_idle_sweep(1000, km.Sessions.live())
        self.assertEqual(self.killed, [])
        self.assertEqual(self.deaths, [], "no death record over whatever really ended it")
        self.assertEqual(km._end_on_idle_load(), set())


if __name__ == "__main__":
    unittest.main()
