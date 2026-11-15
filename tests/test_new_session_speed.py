#!/usr/bin/env python3
"""A session's speed chosen where it is created (the user 2026-11-15, who wanted to start a session at standard speed
without the machine's Always fast switch moving it and without anything typed into its conversation):
`romp new --speed standard|fast <name>` rides POST /new as `speed`, and the kernel writes it as the session's OWN
fast-mode pick between spawn and the first connect (`fastOff` for standard, `fast` for fast), so the first launch
already runs at that speed. No relaunch, no "/fast" in the queue, nothing sent.

What is under test:
  * the first launch (the create door run for real over a real SdkBackend, the connect replaced by a reader of the
    options that launch would carry): standard carries no fast-mode opt-in even with Always fast on, fast carries it
    even with Always fast off, and no `speed` is today's behaviour either way;
  * the door: a value outside the two is a 400 before anything is created; null is "not asked"; a Codex target, new
    or already running, is refused by name;
  * a name that already runs: the pick is taken only where nothing has to be typed (a session that is not connected,
    a connection made without the opt-in, a CLI already at that speed) and refused, with nothing changed and a line
    naming the Fast badge, where the change is the literal "/fast" send;
  * a comment thread inherits the pick through the fork's own copy of `fastOff` and `fast`.

Hermetic state, synthetic sids and names (the notes-api demo's `web` and `api`), a stand-in claude that only prints a
version; no CLI runs and no real session is touched."""
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs conftest's floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_newspeed", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-00000000c501"      # private to this module (CLAUDE.md, goal-store fixtures)
SID2 = "11111111-2222-4333-8444-00000000c502"
THREAD = "11111111-2222-4333-8444-00000000c5f1"


def _claude(d):
    """A stand-in Claude Code binary that prints a version the way `claude -v` does. Never a real CLI."""
    p = os.path.join(d, "claude")
    with open(p, "w") as f:
        f.write("#!/bin/sh\necho '2.1.282 (Claude Code)'\n")
    os.chmod(p, 0o755)
    return p


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, _claude(tempfile.mkdtemp()), lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    be.login_ok = lambda: True
    be._wake_push = lambda: None
    be.sent = []
    be.send = lambda sid, text, *a, **k: (be.sent.append(text), True)[1]   # anything typed into a session lands here
    return be


def _always_fast(be, value):
    Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text(value)


def _live(be, sid=SID, **reg):
    """A session with a live connection, as the always-fast tests build one: no CLI, the thread a stand-in."""
    r = {"sid": sid, "name": "web", "cwd": "/tmp", "liveModelId": "claude-opus-5"}
    r.update(reg)
    sb.write_reg(be.state_dir, sid, dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    s.asks = []
    s.request_reconnect = lambda *a, **k: s.asks.append(k.get("why") or "reconnect")
    s.want_switch_reconnect = lambda why: s.asks.append(why)
    be.sessions[sid] = s
    return s


class _LaunchWorld(unittest.TestCase):
    """_options on a box with no apiKeyHelper and a signed-in machine login, its settings in a scratch config dir (the
    harness of tests/test_fast_mode_token_login.py), so a test can read the flag settings a launch would carry."""

    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self._saved = {k: getattr(sb, k) for k in ("_STARTUP_AUTH_ENV", "_fetch_key_fast_org")}
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        self._managed = sb._cred.managed_settings_path
        sb._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        sb._fetch_key_fast_org = lambda key: None            # the key account's probe is a real HTTPS GET otherwise
        sb._STARTUP_AUTH_ENV = {}
        sb._cred.forget_helper_key()
        self._fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if self._fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake
        self.be = _backend()
        self.cwd = tempfile.mkdtemp()
        sb.write_sdk_default(Path(self.be.state_dir), model="opus")   # every new session's pick can run fast mode
        self.launches = []
        self.be.connect = self._connect
        names = ("_sdk", "_pick_identity_color", "_commands_for_cwd", "_reset_unvouched_seed", "_push_soon",
                 "_mark_views_dirty", "_push_session_now", "_set_fast_or_park")
        self._km = {n: getattr(km, n) for n in names}
        self._backend_for = km.Sessions.backend_for
        km._sdk = lambda: self.be
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pick_identity_color = lambda: ("#123456", "white")
        km._commands_for_cwd = lambda cwd: None
        km._reset_unvouched_seed = lambda: None
        km._push_soon = km._mark_views_dirty = lambda: None
        km._push_session_now = lambda sid: None
        self.parked = []
        km._set_fast_or_park = lambda be, sid, v: (self.parked.append((sid, v)), (True, True))[1]

    def tearDown(self):
        for n, v in self._km.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._backend_for
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)
        for k, v in self._saved.items():
            setattr(sb, k, v)
        sb._cred.managed_settings_path = self._managed
        sb._cred.forget_helper_key()
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _connect(self, sid):
        """The first connect, up to the options its launch carries: the session object built from the registry row as
        _ensure builds it, and the flag settings _options writes for it. No CLI is started."""
        reg = sb.read_reg(self.be.state_dir, sid)
        s = sb.SdkSession(self.be, reg)
        kw = self.be._options(s, dict)
        p = kw.get("settings")
        settings = json.loads(Path(p).read_text()) if p else {}
        self.launches.append({"sid": sid, "fastMode": bool(settings.get("fastMode")),
                              "record": dict(s._launch_record or {}), "reg": reg})
        return True

    def _create(self, name, **prefs):
        sid, echo = km._create_sdk_session_inner(name, self.cwd, prefs=prefs)
        return sid, echo, self.launches[-1]


class TheFirstLaunch(_LaunchWorld):
    def test_standard_launches_without_the_opt_in_even_with_always_fast_on(self):
        _always_fast(self.be, "on")
        _sid0, _echo0, plain = self._create("api")
        self.assertTrue(plain["fastMode"], "the premise: with Always fast on, a session created with no speed launches "
                                           "with the fast-mode opt-in")
        sid, echo, launch = self._create("web", speed="standard")
        self.assertFalse(launch["fastMode"], "created at standard speed: its FIRST launch carries no fast-mode opt-in")
        self.assertIs(launch["record"].get("fast"), False, "and the launch record says so, so nothing relaunches it")
        self.assertEqual(echo.get("speed"), "standard", "the echo `romp new` reports as applied")
        self.assertEqual((launch["reg"].get("fast"), launch["reg"].get("fastOff")), (False, True),
                         "written as the session's own Slow pick, before the connect read the row")
        self.assertEqual(len(self.launches), 2, "one launch per session: the speed never costs a second one")

    def test_fast_launches_with_the_opt_in_even_with_always_fast_off(self):
        _always_fast(self.be, "off")
        _sid0, _echo0, plain = self._create("api")
        self.assertFalse(plain["fastMode"], "the premise: with Always fast off, a session created with no speed "
                                            "launches without the opt-in")
        sid, echo, launch = self._create("web", speed="fast")
        self.assertTrue(launch["fastMode"], "created fast: its first launch carries the opt-in with no switch")
        self.assertIs(launch["record"].get("fast"), True)
        self.assertEqual(echo.get("speed"), "fast")
        self.assertEqual((launch["reg"].get("fast"), launch["reg"].get("fastOff")), (True, False),
                         "written as the session's own Fast pick")

    def test_no_speed_is_todays_behaviour_and_writes_no_pick(self):
        for switch, flag in (("on", True), ("off", False)):
            _always_fast(self.be, switch)
            sid, echo, launch = self._create("web-" + switch)
            self.assertEqual(launch["fastMode"], flag, "the machine's Always fast switch decides, as before")
            self.assertNotIn("speed", echo, "nothing asked, nothing echoed")
            self.assertNotIn("fast", launch["reg"], "no pick of its own is written")
            self.assertNotIn("fastOff", launch["reg"])

    def test_nothing_is_typed_or_queued_for_the_session(self):
        _always_fast(self.be, "on")
        for speed in ("standard", "fast"):
            sid, _echo, launch = self._create("web-" + speed, speed=speed)
            self.assertEqual(self.be.sent, [], "nothing is sent into the conversation")
            self.assertEqual(self.parked, [], 'the pick never goes through the park-aware setter, whose queue would hold '
                                              'a "/fast" chip and send it later')
            self.assertFalse(km._pending_ops.get(sid), "no op is parked for the session")
            self.assertFalse(sb.read_reg(self.be.state_dir, sid).get("queue"), "and its persisted queue is empty")
            self.assertNotIn(sid, self.be.sessions, "the pick was a registry write: no session object existed to relaunch")

    def test_the_badge_reads_slow_like_any_session_with_its_own_slow_pick(self):
        _always_fast(self.be, "on")
        sid, _echo, _launch = self._create("web", speed="standard")
        row = self.be._live_row(sb.read_reg(self.be.state_dir, sid), sid)
        self.assertEqual(row["fast"], "off", 'the row the badge reads before the CLI reports: "off", which the badge '
                                             "spells Slow")
        picked = _live(self.be, sid=SID2, fastOff=True)   # a session put on Slow from its badge
        made = sb.SdkSession(self.be, sb.read_reg(self.be.state_dir, sid))
        self.assertEqual((made.fast_opt, made.fast_off), (picked.fast_opt, picked.fast_off),
                         "the same two fields a pick on the badge leaves")
        self.assertFalse(made.fast_effective())


class AThreadInherits(_LaunchWorld):
    def _thread_of(self, parent_sid, **kw):
        self.be.fork("thread-x", parent_sid, "a1", sid=THREAD, thread_of=parent_sid, **kw)
        return sb.read_reg(self.be.state_dir, THREAD)

    def test_a_thread_of_a_standard_session_launches_at_standard_speed(self):
        _always_fast(self.be, "on")
        sid, _echo, _launch = self._create("web", speed="standard")
        reg = self._thread_of(sid)
        self.assertTrue(reg.get("fastOff"), "the fork copies the parent's own Slow pick")
        self.assertNotIn("fast", reg)
        self._connect(THREAD)
        self.assertFalse(self.launches[-1]["fastMode"], "so the thread's first launch carries no opt-in although Always "
                                                        "fast is on and its model can run fast mode")

    def test_a_thread_of_a_fast_session_launches_fast(self):
        _always_fast(self.be, "off")
        sid, _echo, _launch = self._create("web", speed="fast")
        reg = self._thread_of(sid)
        self.assertTrue(reg.get("fast"), "the fork copies the parent's own Fast pick")
        self._connect(THREAD)
        self.assertTrue(self.launches[-1]["fastMode"])

    def test_a_thread_of_a_session_created_with_no_speed_carries_no_pick(self):
        _always_fast(self.be, "on")
        sid, _echo, _launch = self._create("web")
        reg = self._thread_of(sid)
        self.assertNotIn("fastOff", reg)
        self.assertNotIn("fast", reg, "the premise of the two tests above: the copy is of a pick, not a default")


class ANameThatAlreadyRuns(unittest.TestCase):
    """set_speed_pick on a session that exists: taken only where nothing has to be typed into its conversation."""

    def setUp(self):
        self.be = _backend()
        _always_fast(self.be, "on")

    def _reg(self, sid=SID):
        return sb.read_reg(self.be.state_dir, sid)

    def test_a_session_that_is_not_connected_takes_it_for_its_next_launch(self):
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "model": "opus", "alive": True})
        self.assertEqual(self.be.set_speed_pick(SID, "standard"), "")
        self.assertEqual((self._reg()["fast"], self._reg()["fastOff"]), (False, True))
        self.assertFalse(sb.SdkSession(self.be, self._reg()).fast_effective(), "the next launch carries no opt-in")
        self.assertEqual(self.be.set_speed_pick(SID, "fast"), "")
        self.assertEqual((self._reg()["fast"], self._reg()["fastOff"]), (True, False))
        self.assertEqual(self.be.sent, [])

    def test_a_connection_made_without_the_opt_in_takes_standard_at_once_and_fast_by_a_quiet_relaunch(self):
        _always_fast(self.be, "off")
        s = _live(self.be)
        s._fast_unlocked = False
        self.assertEqual(self.be.set_speed_pick(SID, "standard"), "")
        self.assertTrue(s.fast_off)
        self.assertTrue(self._reg()["fastOff"], "its own Slow pick, which a later Always fast leaves alone")
        self.assertEqual(s.asks, [], "already at standard speed: nothing to relaunch for")
        self.assertEqual(self.be.set_speed_pick(SID, "fast"), "")
        self.assertTrue(s.fast_opt)
        self.assertEqual(s.asks, ["fast mode"], "the opt-in rides a relaunch once nothing would be cut, the road an "
                                                "effort pick takes")
        self.assertEqual(self.be.sent, [], "and neither typed anything")

    def test_a_connection_made_with_the_opt_in_refuses_a_change_of_speed_and_changes_nothing(self):
        s = _live(self.be)
        s._fast_unlocked, s.fast = True, "on"
        before = self._reg()
        why = self.be.set_speed_pick(SID, "standard")
        self.assertIn("would type /fast off into its conversation", why)
        self.assertIn("Fast badge", why, "the refusal names where the speed of a running session is changed")
        self.assertIn('"web"', why)
        self.assertEqual(self._reg(), before, "refused before anything was written")
        self.assertFalse(s.fast_off)
        self.assertTrue(s.fast_effective(), "the session runs on as it was")
        self.assertEqual(self.be.sent, [], "and nothing was typed")
        self.assertEqual(s.asks, [])
        # the other direction: a CLI the user put on Slow from the badge, asked for fast
        s.fast = "off"
        why = self.be.set_speed_pick(SID, "fast")
        self.assertIn("would type /fast on into its conversation", why)
        self.assertEqual(self.be.sent, [])
        # a state the CLI has not reported yet is not known to match: refused too
        s.fast = ""
        self.assertTrue(self.be.set_speed_pick(SID, "standard"))
        self.assertEqual(self.be.sent, [])

    def test_a_cli_already_at_the_asked_speed_records_the_pick_and_sends_nothing(self):
        s = _live(self.be)
        s._fast_unlocked, s.fast = True, "on"        # running fast under the machine's switch
        self.assertEqual(self.be.set_speed_pick(SID, "fast"), "")
        self.assertTrue(self._reg()["fast"], "now its own pick: turning Always fast off no longer slows it")
        _always_fast(self.be, "off")
        self.assertTrue(s.fast_effective())
        s.fast = "cooldown"                          # rate-limited fast mode is fast mode on
        self.assertEqual(self.be.set_speed_pick(SID, "fast"), "")
        s.fast = "off"                               # a CLI that runs at standard speed although its launch carried the opt-in
        self.assertEqual(self.be.set_speed_pick(SID, "standard"), "")
        self.assertTrue(self._reg()["fastOff"])
        self.assertEqual(self.be.sent, [], "none of the three typed anything")

    def test_the_badge_still_sends_so_the_checks_above_are_not_vacuous(self):
        s = _live(self.be)
        s._fast_unlocked, s.fast = True, "on"
        self.assertTrue(self.be.fast_pick_needs_send(SID, "off"))
        self.assertTrue(self.be.set_fast(SID, "off"))
        self.assertEqual(self.be.sent, ["/fast off"], "the badge's toggle is the typed command, as before")

    def test_a_word_that_is_no_speed_is_refused(self):
        sb.write_reg(self.be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "alive": True})
        before = self._reg()
        self.assertTrue(self.be.set_speed_pick(SID, "turbo"))
        self.assertEqual(self._reg(), before)


class TheDoor(unittest.TestCase):
    """POST /new's `speed`, over the real Handler (the tests/test_new_route_prefs.py pattern): validated before anything
    is created, refused by name for a Codex target, and answered as `speed` or `refused` on a name that already runs."""

    class _Sdk:
        """A backend double with the speed pick (the SDK shape); `why` is what the next pick answers."""

        def __init__(self):
            self.picks, self.why = [], ""

        def set_speed_pick(self, sid, speed):
            self.picks.append((sid, speed))
            return self.why

    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.dir = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        self.be = self._Sdk()
        self.created, self.created_codex, self.parked = [], [], []
        names = ("_live_names", "_live_map", "_sdk_ready", "_create_sdk_session", "_push_soon", "_codex_ready",
                 "_create_codex_session", "_set_fast_or_park", "_set_effort_or_park")
        self._saved = {n: getattr(km, n) for n in names}
        self._backend_for = km.Sessions.backend_for
        km._live_map = lambda: []
        km._live_names = lambda *_: {}
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._codex_ready = lambda: True
        km._push_soon = lambda: None
        km._set_fast_or_park = lambda be, sid, v: (self.parked.append((sid, v)), (True, True))[1]
        km._set_effort_or_park = lambda be, sid, v: (True, False)
        km._create_sdk_session = (lambda nm, cwd, auth="", prefs=None, client=None, env=None, **kw:
                                  (self.created.append(nm), (SID2, km._apply_new_session_prefs(SID2, prefs or {})))[1])
        km._create_codex_session = (lambda nm, cwd, client=None, **kw: (self.created_codex.append(nm), (SID2, {}))[1])

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._backend_for

    def _post(self, body):
        req = urllib.request.Request("http://127.0.0.1:%d/new" % self.port, data=json.dumps(body).encode(),
                                     headers={"X-Romp-Token": os.environ["ROMP_SERVE_TOKEN"],
                                              "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def test_a_value_that_is_no_speed_refuses_the_whole_request(self):
        for bad in ("turbo", "slow", "on", "Standard", "", False, 0, 1, [], {"speed": "fast"}):
            code, body = self._post({"name": "web", "dir": self.dir, "speed": bad})
            self.assertEqual(code, 400, "speed %r must be a 400, not a session at the machine's speed" % (bad,))
            self.assertFalse(body["ok"])
            self.assertIn("standard", body["error"], "the refusal names the two speeds")
            self.assertIn("fast", body["error"])
        self.assertEqual(self.created, [], "nothing may be created on a refused request")
        self.assertEqual(self.be.picks, [])

    def test_a_bad_speed_is_refused_for_a_name_that_already_runs_too(self):
        km._live_names = lambda *_: {"web": SID}
        code, body = self._post({"name": "web", "dir": self.dir, "speed": "turbo", "effort": "high"})
        self.assertEqual(code, 400)
        self.assertEqual(self.be.picks, [], "validated before any pref is re-asserted")

    def test_null_means_not_asked(self):
        code, body = self._post({"name": "web", "dir": self.dir, "speed": None})
        self.assertEqual(code, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(self.created, ["web"])
        self.assertNotIn("speed", body)
        self.assertEqual(self.be.picks, [])

    def test_a_create_hands_the_speed_to_the_backends_pick_and_echoes_it(self):
        for speed in ("standard", "fast"):
            code, body = self._post({"name": "web", "dir": self.dir, "speed": speed})
            self.assertEqual(code, 200)
            self.assertTrue(body["ok"])
            self.assertEqual(body.get("speed"), speed, "the echo is the ack the CLI is loud about missing")
            self.assertNotIn("refused", body)
        self.assertEqual(self.be.picks, [(SID2, "standard"), (SID2, "fast")])
        self.assertEqual(self.parked, [], "never through the park-aware setter")

    def test_a_codex_session_is_not_created_at_a_speed_it_cannot_run_at(self):
        code, body = self._post({"name": "web", "dir": self.dir, "backend": "codex", "speed": "standard"})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"])
        self.assertIn("a Codex session has no fast mode", body["error"])
        self.assertEqual(self.created_codex, [], "refused before the create")
        code, body = self._post({"name": "web", "dir": self.dir, "backend": "codex"})
        self.assertTrue(body["ok"], "the premise: the same create without a speed goes through")
        self.assertEqual(self.created_codex, ["web"])

    def test_a_running_codex_session_refuses_by_name(self):
        km._live_names = lambda *_: {"web": SID}
        km.Sessions.backend_for = staticmethod(lambda sid: object())   # no speed pick: the Codex shape
        code, body = self._post({"name": "web", "dir": self.dir, "speed": "standard"})
        self.assertEqual(code, 200)
        self.assertFalse(body["ok"], "a session that has no speed to pick says so, the ask is not dropped")
        self.assertIn('"web" runs on the Codex backend, which has no fast mode', body["error"])

    def test_a_running_session_that_takes_the_pick_echoes_it(self):
        km._live_names = lambda *_: {"web": SID}
        code, body = self._post({"name": "web", "dir": self.dir, "speed": "standard"})
        self.assertTrue(body["ok"] and body["existing"])
        self.assertEqual(body.get("speed"), "standard")
        self.assertEqual(self.be.picks, [(SID, "standard")])
        self.assertEqual(self.parked, [], 'no "/fast" chip is queued for a running session')

    def test_a_running_session_that_cannot_take_it_untyped_answers_refused_in_the_backends_words(self):
        km._live_names = lambda *_: {"web": SID}
        self.be.why = "the reason the backend gave"
        code, body = self._post({"name": "web", "dir": self.dir, "speed": "standard", "effort": "high"})
        self.assertTrue(body["ok"] and body["existing"], "the open stands: the session runs, one pref was refused")
        self.assertNotIn("speed", body, "a refused speed is never echoed as applied")
        self.assertEqual(body.get("refused"), "Couldn't set speed 'standard': the reason the backend gave.")
        self.assertEqual(body.get("effort"), "high", "the pref it took is echoed on its own")
        self.assertEqual(self.parked, [])

    def test_the_doors_speeds_are_the_backends(self):
        self.assertEqual(sorted(km._SPEED_PICKS), sorted(sb.SPEED_PICKS),
                         "the door validates against its own copy (the backend module loads lazily): the two lists agree")


if __name__ == "__main__":
    unittest.main()
