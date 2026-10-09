#!/usr/bin/env python3
"""Subagents at standard speed: the version canary.

The workaround rests on undocumented Claude Code behaviour, so a Claude Code update can break it. Before the recipe
rides any session's launch on a Claude Code version romp has not seen it do its job on, romp runs a tiny probe of it in
an isolated lab (kernel/subagent_canary.py: its own temp state root, session hosts off, a fresh session id, a scratch
directory whose transcripts it deletes, no peer messaging): one fast main-loop turn that starts one subagent, judged by
the same speed check the sessions use. A pass clears the version and every session waiting on it relaunches with the
recipe once quiet; a failure holds the switch off on that version and says why once; a probe that cannot finish holds
it off too, said once, and runs again when the switch is turned on.

The mechanics under test, with the probe itself replaced by a stub (no CLI here):
  * the version is the binary's own `--version`, read once per binary (path, size, mtime);
  * a launch on a version with no verdict carries no recipe and starts the probe once; the row says it is checking;
  * a pass carries the recipe and asks the waiting sessions to relaunch; a failure holds it off, said once per version;
  * turning the switch on starts the probe when the version has none, and asks for no relaunch until it passes;
  * the probe's verdict from the speed check's figures (pure), and the probe's environment (no session identity, no
    recipe variables, the kernel's own login tokens, the canary marker);
  * a probe whose session never started says so with the backend's own reason, and stops waiting at once; a turn that
    ended in an error is no failure of the recipe;
  * the probe is handed, as arguments, where the kernel imports the Agent SDK from (a fresh interpreter lacks the venv
    entry the kernel put on its own path) and the kernel's state directory, whose machine default billing (a stored
    login's record with it) its launch then bills, as an unpicked session's does;
  * inside the probe's own process the gate is open (it is the probe);
  * a turn that ended in an error says what its result said (the settle keeps the result's own words on the session
    before the turn row lands), and stays inconclusive; a session that never started names only a launch error the
    backend recorded for it; a billing that could not apply (a stored login refused, expired or removed, or its token
    command failing) leads the verdict, in the backend's own words, and no verdict or log line carries a token;
  * the probe's launch falls exactly as an unpicked session's does when the default's stored login cannot bill, its
    token command sees the kernel's XDG_STATE_HOME, and the login record it copies is private (0700 / 0600) and goes
    with its temp root, at its own cleanup or, for a probe the kernel stopped at its time limit, at once.

Synthetic fixtures only: placeholder sids, an invented session name, a fake binary.
"""
import asyncio
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the loads
os.environ.pop("ROMP_STATE_DIR", None)
sb = load_source("romp_sdk_backend_canary_t", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-00000000ca11"   # private to this module


def _fake_claude(version="2.1.282"):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "claude")
    Path(p).write_text("#!/bin/sh\necho '%s (Claude Code)'\n" % version)
    os.chmod(p, os.stat(p).st_mode | stat.S_IEXEC)
    return p


def _backend(claude=None, runner=None):
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")
    be = sb.SdkBackend(d, claude or _fake_claude(), lambda *a, **k: None, log=lambda m, **k: None)
    be._canary_calls = []
    if runner is None:
        def runner(version):
            be._canary_calls.append(version)
            return None   # still running: the test finishes it by hand
    be._canary_runner = runner
    be._canary_sync = True   # run the probe on the calling thread in tests
    return be


def _reg(be, **fields):
    r = {"sid": SID, "name": "web", "cwd": str(be.state_dir), "alive": True, "model": "claude-opus-5-5", "fast": True,
         "subagentStandard": True}
    r.update(fields)
    sb.write_reg(Path(be.state_dir), SID, r)
    return r


def _problems(be, word):
    return [r["text"] for r in be._problems if word in r["text"]]


class Version(unittest.TestCase):
    def test_the_version_is_the_binarys_own_and_read_once_per_binary(self):
        claude = _fake_claude("2.1.290")
        be = _backend(claude)
        runs = []
        real_run = sb.subprocess.run
        sb.subprocess.run = lambda *a, **k: (runs.append(a), real_run(*a, **k))[1]
        try:
            self.assertEqual(be.cli_version(), "2.1.290")
            self.assertEqual(be.cli_version(), "2.1.290")
            self.assertEqual(len(runs), 1, "cached while the binary's path, size and mtime stand")
            st = os.stat(claude)
            os.utime(claude, (st.st_atime, st.st_mtime + 5))   # an update replaces the binary
            be.cli_version()
            self.assertEqual(len(runs), 2)
        finally:
            sb.subprocess.run = real_run

    def test_an_unreadable_version_holds_the_recipe_off_and_says_why(self):
        be = _backend("/nonexistent/claude")
        s = sb.SdkSession(be, _reg(be))
        self.assertFalse(s.subagent_standard_carried())
        self.assertIn("version", s.subagent_standard_hold())


class Gate(unittest.TestCase):
    def _options(self, be, s):
        fake = None
        if "claude_agent_sdk" not in sys.modules and not sb.sdk_importable():
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake
        try:
            kw = be._options(s, dict)
        finally:
            if fake is not None:
                sys.modules.pop("claude_agent_sdk", None)
        return json.loads(Path(kw["settings"]).read_text()) if kw.get("settings") else {}

    def test_a_version_with_no_verdict_launches_plainly_and_starts_the_probe_once(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        for _ in range(2):
            got = self._options(be, s)
            self.assertNotIn("modelOverrides", got)
        self.assertEqual(be._canary_calls, ["2.1.282"], "one probe per version")
        self.assertIn("checking", s.subagent_standard_hold())
        self.assertFalse(s._launch_record["subagentStandard"])

    def test_a_pass_carries_the_recipe_and_relaunches_the_waiting_sessions_once_quiet(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s.client = object()
        s.asks = []
        s.request_reconnect = lambda *a, **k: s.asks.append(k.get("why"))
        be.sessions[SID] = s
        self._options(be, s)
        s._sub_std_begin(dict(s._launch_record), True)   # this CLI launched without the recipe
        be._canary_finished("2.1.282", {"verdict": "pass", "why": "", "mainFast": 1, "subStandard": 1, "subFast": 0})
        self.assertEqual(s.subagent_standard_hold(), "")
        self.assertIn("subagent speed", s.asks, "the waiting session relaunches with it once quiet")
        self.assertIn("modelOverrides", self._options(be, s))
        verdicts = json.loads(Path(be.state_dir, sb.SUBAGENT_STANDARD_CANARY_STORE).read_text())
        self.assertEqual(verdicts["2.1.282"]["verdict"], "pass")

    def test_a_failure_holds_it_off_on_that_version_said_once(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        self._options(be, s)
        be._canary_finished("2.1.282", {"verdict": "fail", "why": "a subagent call ran fast"})
        for _ in range(2):
            self.assertNotIn("modelOverrides", self._options(be, s))
        self.assertIn("a subagent call ran fast", s.subagent_standard_hold())
        self.assertEqual(len(_problems(be, "failed its check")), 1)
        self.assertEqual(be._canary_calls, ["2.1.282"], "a failed version is not probed again")

    def test_a_new_version_is_probed_afresh(self):
        claude = _fake_claude("2.1.282")
        be = _backend(claude)
        be._canary_finished("2.1.282", {"verdict": "pass"})
        s = sb.SdkSession(be, _reg(be))
        self.assertTrue(s.subagent_standard_carried())
        Path(claude).write_text("#!/bin/sh\necho '2.1.300 (Claude Code)'\n")
        st = os.stat(claude)
        os.utime(claude, (st.st_atime, st.st_mtime + 5))
        self.assertFalse(s.subagent_standard_carried())
        self._options(be, s)
        self.assertEqual(be._canary_calls, ["2.1.300"])

    def test_an_inconclusive_probe_holds_it_off_said_once_and_runs_again_when_the_switch_is_turned_on(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        self._options(be, s)
        be._canary_finished("2.1.282", {"verdict": "inconclusive", "why": "the probe turn did not finish"})
        self._options(be, s)
        self.assertEqual(be._canary_calls, ["2.1.282"], "not retried on every launch")
        self.assertIn("could not finish", s.subagent_standard_hold())
        self.assertEqual(len(_problems(be, "could not check")), 1)
        _reg(be, subagentStandard=False)
        self.assertTrue(be.set_subagent_standard(SID, "on"))
        self.assertEqual(be._canary_calls, ["2.1.282", "2.1.282"], "the user's gesture runs it again")

    def test_turning_it_on_starts_the_probe_and_asks_for_no_relaunch_until_it_passes(self):
        be = _backend()
        r = _reg(be, subagentStandard=False)
        s = sb.SdkSession(be, r)
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s.client = object()
        s.asks = []
        s.request_reconnect = lambda *a, **k: s.asks.append(k.get("why"))
        be.sessions[SID] = s
        self.assertTrue(be.set_subagent_standard(SID, "on"))
        self.assertEqual(be._canary_calls, ["2.1.282"])
        self.assertEqual(s.asks, [], "nothing to relaunch for until the probe passes")
        self.assertIn("checking", s.snapshot()["subagentSpeed"])

    def test_inside_the_probe_the_gate_is_open(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        os.environ[sb.SUBAGENT_STANDARD_CANARY_ENV] = "1"
        try:
            self.assertEqual(s.subagent_standard_hold(), "")
            self.assertIn("modelOverrides", self._options(be, s))
        finally:
            os.environ.pop(sb.SUBAGENT_STANDARD_CANARY_ENV, None)
        self.assertEqual(be._canary_calls, [])


class Retry(unittest.TestCase):
    """A verdict that holds the switch off is never a lock: the user can ask for the check again, and a probe that proved
    nothing is retried once on a later turn of a session waiting on it."""

    def _live(self, be, **fields):
        s = sb.SdkSession(be, _reg(be, **fields))
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s.client = object()
        s.asks = []
        s.request_reconnect = lambda *a, **k: s.asks.append(k.get("why"))
        be.sessions[SID] = s
        return s

    def test_turning_it_on_again_rechecks_a_failed_version(self):
        be = _backend()
        s = self._live(be)
        be.recipe_hold(start=True)
        be._canary_finished("2.1.282", {"verdict": "fail", "why": "a subagent call ran in fast mode"})
        self.assertTrue(s.snapshot()["subagentStandardRetry"], "the fast menu offers the check again")
        self.assertTrue(be.set_subagent_standard(SID, "on"), "on, while already on: run the check again")
        self.assertEqual(be._canary_calls, ["2.1.282", "2.1.282"])
        self.assertIn("checking", s.subagent_standard_hold())

    def test_an_inconclusive_probe_is_retried_once_on_a_later_turn(self):
        be = _backend()
        s = self._live(be)
        be.recipe_hold(start=True)
        be._canary_finished("2.1.282", {"verdict": "inconclusive", "why": "the probe's turn did not finish"})
        be._canary_turn_retry(s)                      # a turn of a session waiting on the check ends
        self.assertEqual(be._canary_calls, ["2.1.282", "2.1.282"])
        be._canary_finished("2.1.282", {"verdict": "inconclusive", "why": "the probe's turn did not finish"})
        be._canary_turn_retry(s)
        self.assertEqual(be._canary_calls, ["2.1.282", "2.1.282"], "one automatic retry; then the user's gesture")
        self.assertTrue(be.set_subagent_standard(SID, "on"))
        self.assertEqual(len(be._canary_calls), 3)

    def test_a_turn_of_a_session_not_waiting_on_it_retries_nothing(self):
        be = _backend()
        s = self._live(be, subagentStandard=False)
        be.recipe_hold(start=True)
        be._canary_finished("2.1.282", {"verdict": "inconclusive", "why": "x"})
        be._canary_turn_retry(s)
        self.assertEqual(be._canary_calls, ["2.1.282"])


class HoldAndRelaunch(unittest.TestCase):
    """A canary hold is a wait for the probe, not a relaunch to make: a CLI launched with the recipe (on an earlier
    version) keeps it, and nothing invites the user to cut work for a relaunch that would carry no recipe."""

    def _live(self, be, launched):
        s = sb.SdkSession(be, _reg(be))
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        s.client = object()
        s._sub_std_begin({"subagentStandard": launched}, True)
        be.sessions[SID] = s
        return s

    def test_a_cli_carrying_the_recipe_is_not_pending_while_the_new_version_is_checked(self):
        be = _backend()
        s = self._live(be, launched=True)
        self.assertIn("checking", be.recipe_hold(start=True))
        self.assertFalse(s._sub_std_pending())
        self.assertFalse(s.snapshot()["fastPending"], "no dots, no Apply now: a relaunch now would drop the recipe")

    def test_a_cli_without_it_waits_for_the_probe_not_for_a_relaunch(self):
        be = _backend()
        s = self._live(be, launched=False)
        be.recipe_hold(start=True)
        self.assertFalse(s._sub_std_pending())
        self.assertFalse(s.snapshot()["fastPending"])

    def test_the_launch_record_is_the_launchs_own_decision(self):
        be = _backend()
        s = sb.SdkSession(be, _reg(be))
        answers = iter(["checking the workaround on Claude Code 2.1.282 first (one short probe turn)"])
        be.recipe_hold = lambda start=False, retry=False: next(answers, "")   # the verdict lands mid-_options
        got = Gate._options(self, be, s)
        self.assertNotIn("modelOverrides", got)
        self.assertIs(s._launch_record["subagentStandard"], False, "the record says what this launch carried")


class Leftovers(unittest.TestCase):
    """A probe killed before its cleanup leaves its temp directories and its transcripts' project directory; the next
    probe and the kernel's boot sweep them, matching the probe's own names and no other, and never a running probe's."""

    def test_the_sweep_removes_dead_probes_leftovers_only(self):
        be = _backend()
        tmp = Path(tempfile.mkdtemp())
        projects = Path(tempfile.mkdtemp())
        dead = tmp / "romp-subagent-canary-dead1"
        dead.mkdir()
        (dead / sb.SUBAGENT_CANARY_PID_FILE).write_text("999999999")        # a pid no process has
        dead_work = tmp / "romp-subagent-canary-work-dead2"
        dead_work.mkdir()
        (dead_work / sb.SUBAGENT_CANARY_PID_FILE).write_text("999999999")
        live = tmp / "romp-subagent-canary-live1"
        live.mkdir()
        (live / sb.SUBAGENT_CANARY_PID_FILE).write_text(str(os.getpid()))  # a running probe
        other = tmp / "romp-lab-state-keep"
        other.mkdir()
        slug = lambda p: "".join(c if c.isalnum() else "-" for c in os.path.realpath(str(p)))
        (projects / slug(dead_work)).mkdir()
        (projects / slug(tmp / "romp-subagent-canary-work-gone3")).mkdir()   # its work dir is already gone
        keep_proj = projects / slug(tmp / "somewhere-else")
        keep_proj.mkdir()
        be._sweep_canary_leftovers(tmp_root=tmp, projects_root=projects)
        self.assertFalse(dead.exists())
        self.assertFalse(dead_work.exists())
        self.assertTrue(live.exists(), "a running probe's directory stays")
        self.assertTrue(other.exists(), "only the probe's own names")
        self.assertEqual(sorted(p.name for p in projects.iterdir()), [keep_proj.name])

    def test_the_probe_marks_its_directories_with_its_pid(self):
        src = Path(os.path.dirname(HERE), "kernel", "subagent_canary.py").read_text()
        self.assertTrue("SUBAGENT_CANARY_PID_FILE" in src or "canary.pid" in src)


class Probe(unittest.TestCase):
    def test_the_verdict_from_the_speed_checks_figures(self):
        v = lambda *a, **k: sb.subagent_canary_verdict(*a, started=True, **k)
        ok = {"mainFast": 2, "mainStandard": 0, "subFast": 0, "subStandard": 1}
        self.assertEqual(v(ok, launched=True, finished=True, fast="on", fast_reason="")["verdict"], "pass")
        self.assertEqual(v(dict(ok, subFast=1), launched=True, finished=True, fast="on", fast_reason="")["verdict"], "fail")
        self.assertEqual(v(dict(ok, mainFast=0, mainStandard=2), launched=True, finished=True, fast="off",
                           fast_reason="")["verdict"], "fail")
        self.assertEqual(v(dict(ok, subStandard=0), launched=True, finished=True, fast="on", fast_reason="")["verdict"],
                         "inconclusive", "no subagent call: the probe proves nothing")
        self.assertEqual(v(ok, launched=True, finished=False, fast="on", fast_reason="")["verdict"], "inconclusive")
        self.assertEqual(v(dict(ok, mainFast=0), launched=True, finished=True, fast="off",
                           fast_reason="extra_usage_disabled")["verdict"], "inconclusive", "fast mode unavailable here")

    def test_the_probes_environment(self):
        base = {"HOME": "/home/x", "PATH": "/usr/bin", "ROMP_SID": "x", "ROMP_SESSION_NAME": "web",
                "ROMP_STATE_DIR": "/s", "XDG_STATE_HOME": "/live", "CLAUDE_CODE_SUBAGENT_MODEL": "x",
                "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1", "CLAUDE_ENV_FILE": "/f", "FEATURE_FLAG": "1"}
        env = sb.subagent_canary_env(base, {"CLAUDE_CODE_OAUTH_TOKEN": "t"})
        for gone in ("ROMP_SID", "ROMP_SESSION_NAME", "ROMP_STATE_DIR", "XDG_STATE_HOME", "CLAUDE_CODE_SUBAGENT_MODEL",
                     "CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_ENV_FILE"):
            self.assertNotIn(gone, env, gone)
        self.assertEqual((env["HOME"], env["PATH"], env["FEATURE_FLAG"]), ("/home/x", "/usr/bin", "1"))
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "t", "the kernel's own login, which it took out of its environment")
        self.assertEqual(env[sb.SUBAGENT_STANDARD_CANARY_ENV], "1")
        # the user-level romp hooks the probe's CLI loads stand down for it, as for the judges' nested calls: the Stop
        # drain would otherwise reach the live mail service, be refused, and block the probe's stop
        self.assertEqual(env["ROMP_SUMMARIZING"], "1")

    def test_the_probe_script_is_isolated(self):
        src = Path(os.path.dirname(HERE), "kernel", "subagent_canary.py").read_text()
        for pin in ('"session-hosts"', "tempfile.mkdtemp", "XDG_STATE_HOME", "shutil.rmtree", "be.kill(sid)",
                    "uuid.uuid4()"):
            self.assertTrue(pin in src, "the probe script lacks %s" % pin)
        self.assertNotIn("mcp_config", src, "no peer messaging in the probe")
        self.assertNotIn("no hooks of the kernel", src, "the probe's CLI loads the user's hooks; the docstring says so")


CANARY = os.path.join(os.path.dirname(HERE), "kernel", "subagent_canary.py")
cn = load_source("romp_subagent_canary_t", CANARY)


class _StandIn:
    """A backend stand-in for the probe's turn (probe_turn): what the real one shows the probe, nothing more."""

    def __init__(self, state, launch_error=None, session=None, row=None):
        self.state, self._launch_error, self._session, self._row = state, launch_error, session, row
        self.sessions, self._pending_ask, self.last_subagent_standard_refusal = {}, {}, ""

    def _update_reg(self, sid, **fields):
        pass

    def set_subagent_standard(self, sid, value):
        return True

    def send(self, sid, text, user=False):
        if self._session is not None:
            self.sessions[sid] = self._session
        if self._row is not None:
            Path(self.state, "turns.jsonl").write_text(json.dumps(dict(self._row, sid=sid)) + "\n")

    def launch_error(self, sid):
        return self._launch_error

    def problems(self, limit=0):
        return []


class ASessionThatNeverStarted(unittest.TestCase):
    """A probe whose session crashed at the start (on one machine the probe's interpreter could not import the Agent SDK)
    read "the probe's launch did not carry the workaround": the verdict blamed the recipe's launch for a session that
    never reached a CLI. It says the session did not start, with the backend's own reason, and stops waiting at once."""

    def test_the_verdict_names_the_start_failure_before_anything_else(self):
        v = sb.subagent_canary_verdict({}, launched=False, finished=False, fast="", fast_reason="", started=False,
                                       start_failure="No module named 'claude_agent_sdk'")
        self.assertEqual(v["verdict"], "inconclusive")
        self.assertEqual(v["why"], "the probe's session did not start: No module named 'claude_agent_sdk'")
        v = sb.subagent_canary_verdict({}, launched=False, finished=False, fast="", fast_reason="", started=False)
        self.assertIn("did not start", v["why"], "no reason recorded is still a session that did not start")

    def test_the_probe_reads_the_backends_launch_error_and_does_not_wait_out_the_turn(self):
        state = tempfile.mkdtemp()
        be = _StandIn(state, launch_error={"text": "the CLI could not start: synthetic reason", "at": 0})
        t0 = __import__("time").time()
        v = cn.probe_turn(sb, be, state, SID, timeout=30, tick=0.01)
        self.assertLess(__import__("time").time() - t0, 5, "a launch error ends the wait: nothing will finish")
        self.assertEqual(v["verdict"], "inconclusive")
        self.assertEqual(v["why"], "the probe's session did not start: the CLI could not start: synthetic reason")

    def test_a_turn_that_ended_in_an_error_is_no_failure_of_the_recipe(self):
        """A CLI that started without a credential ends its turn in an error before any call: no main-loop call ran
        fast, which the old order read as the recipe's breakage ("the main loop did not run in fast mode")."""
        state = tempfile.mkdtemp()
        sess = types.SimpleNamespace(_connected=threading.Event(), _sub_std_launched=True, fast="on", fast_reason="")
        sess._connected.set()
        be = _StandIn(state, session=sess, row={"isError": True})
        v = cn.probe_turn(sb, be, state, SID, timeout=30, tick=0.01)
        self.assertEqual((v["verdict"], v["why"]), ("inconclusive", "the probe's turn ended in an error"))
        ok = {"mainFast": 2, "subStandard": 1}
        self.assertEqual(sb.subagent_canary_verdict(ok, launched=True, finished=True, fast="on", fast_reason="",
                                                    started=True, errored=True)["verdict"], "pass",
                         "calls that already showed the recipe working stand over a later error")
        self.assertEqual(sb.subagent_canary_verdict(dict(ok, subFast=1), launched=True, finished=True, fast="on",
                                                    fast_reason="", started=True, errored=True)["verdict"], "fail")


class TheProbesInterpreter(unittest.TestCase):
    """The kernel's interpreter finds the Agent SDK through the venv entry kernel._ensure_sdk_on_path puts on its own
    path at run time; the probe is a fresh interpreter without it, so on a machine whose interpreter does not find the
    SDK by itself every probe's session died at the import. The kernel hands the probe that entry, as an argument."""

    def test_the_sdk_import_root_is_the_path_entry_the_package_resolves_from(self):
        site = tempfile.mkdtemp()
        os.makedirs(os.path.join(site, "romp_canary_fake_sdk_pkg"))
        Path(site, "romp_canary_fake_sdk_pkg", "__init__.py").write_text("")
        Path(site, "romp_canary_fake_sdk_mod.py").write_text("")
        sys.path.insert(0, site)
        try:
            self.assertEqual(sb.sdk_import_root("romp_canary_fake_sdk_pkg"), site)
            self.assertEqual(sb.sdk_import_root("romp_canary_fake_sdk_mod"), site)
        finally:
            sys.path.remove(site)
        self.assertEqual(sb.sdk_import_root("romp_canary_no_such_package"), "")

    def _run(self, be):
        seen = {}

        def run(argv, **kw):
            seen.update(argv=argv, env=kw.get("env"))
            return types.SimpleNamespace(stdout='{"verdict": "pass", "why": ""}\n', stderr="", returncode=0)
        saved = (sb.subprocess.run, sb.sdk_import_root)
        sb.subprocess.run = run
        return seen, saved

    def test_the_kernel_hands_the_probe_its_sdk_entry_and_its_state_directory(self):
        be = _backend()
        site = tempfile.mkdtemp()
        seen, saved = self._run(be)
        sb.sdk_import_root = lambda name="claude_agent_sdk": site
        try:
            self.assertEqual(be._run_canary_subprocess("2.1.282")["verdict"], "pass")
        finally:
            sb.subprocess.run, sb.sdk_import_root = saved
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--sdk-path") + 1], site)
        self.assertEqual(argv[argv.index("--kernel-state") + 1], str(be.state_dir))
        self.assertNotIn(site, str(seen["env"].get("PYTHONPATH") or ""),
                         "an argument, never PYTHONPATH: the probe's CLI and its hooks would inherit it")
        seen, saved = self._run(be)
        sb.sdk_import_root = lambda name="claude_agent_sdk": ""
        try:
            be._run_canary_subprocess("2.1.282")
        finally:
            sb.subprocess.run, sb.sdk_import_root = saved
        self.assertNotIn("--sdk-path", seen["argv"], "an SDK this process cannot import has no entry to hand over")

    def test_the_probe_puts_the_entry_on_its_path_and_takes_the_billing_before_its_backend_loads(self):
        """The probe script itself, run as the kernel runs it, over a stand-in kernel directory whose backend module
        imports the Agent SDK at its top (resolvable here only through --sdk-path) and reports what it found."""
        site = tempfile.mkdtemp()
        os.makedirs(os.path.join(site, "claude_agent_sdk"))
        Path(site, "claude_agent_sdk", "__init__.py").write_text('WHERE = "the entry the kernel handed over"\n')
        kdir = tempfile.mkdtemp()
        Path(kdir, "sdk_backend.py").write_text(
            "import os\n"
            "import claude_agent_sdk\n"
            "def subagent_canary_billing(kernel_state, probe_state):\n"
            "    open(os.path.join(probe_state, 'billing-from'), 'w').write(kernel_state)\n"
            "class SdkBackend:\n"
            "    def __init__(self, state, *a, **k):\n"
            "        billing = open(os.path.join(state, 'billing-from')).read()\n"
            "        raise RuntimeError('sdk: %s; billing: %s' % (claude_agent_sdk.WHERE, billing))\n")
        kstate = tempfile.mkdtemp()
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        import subprocess
        p = subprocess.run([sys.executable, CANARY, "--claude", "/bin/true", "--kernel-dir", kdir,
                            "--kernel-state", kstate, "--sdk-path", site], env=env, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        v = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(v["why"], "the probe failed: sdk: the entry the kernel handed over; billing: %s" % kstate,
                         p.stderr[-500:])


class _BillingScratch:
    """A scratch Claude config with no apiKeyHelper and no managed settings, no boot-claimed login tokens, a stand-in
    Agent SDK module when none is importable, and a kernel state and a probe state to copy between."""

    def setUp(self):
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg      # no apiKeyHelper, no machine login in this scratch config
        self._managed = sb._cred.managed_settings_path
        sb._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        self._startup = sb._STARTUP_AUTH_ENV
        sb._STARTUP_AUTH_ENV = {}
        self._fake_sdk = "claude_agent_sdk" not in sys.modules and not sb.sdk_importable()
        if self._fake_sdk:
            fake = types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: types.SimpleNamespace(**kw)
            sys.modules["claude_agent_sdk"] = fake
        self.kernel_state = Path(tempfile.mkdtemp())
        self.probe_state = Path(tempfile.mkdtemp())
        Path(self.probe_state, "session-hosts").write_text("off")

    def tearDown(self):
        if self._fake_sdk:
            sys.modules.pop("claude_agent_sdk", None)
        sb._cred.managed_settings_path = self._managed
        sb._STARTUP_AUTH_ENV = self._startup
        if self._cfg_before is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before


class TheProbesBilling(_BillingScratch, unittest.TestCase):
    """On a machine whose one login is a stored one (no login of the machine's own, no apiKeyHelper), every session
    follows the machine default and launches with that login's token. The probe's fresh state root had no default, so
    its CLI started with no credential at all, refused fast mode ("preference") and ended its turn in an error, while
    the sessions it gates ran fast. Its state now takes the kernel's machine default billing, the stored login's record
    with it, and its launch decides on the road an unpicked session's launch takes."""

    def _probe_launch_env(self):
        be = sb.SdkBackend(str(self.probe_state), "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        be.login_ok = lambda: False                       # no login of the machine's own
        sid = be.spawn("canary", str(self.probe_state), sid=SID)
        s = sb.SdkSession(be, dict(sb.read_reg(self.probe_state, sid), model=sb.SUBAGENT_STANDARD_MODEL, fast=True))
        return be._options(s, dict)["env"]

    def test_a_stored_login_default_reaches_the_probes_launch(self):
        token = "".join(["synthetic", "-probe-", "login"])   # not credential-shaped: the scanner reads this file
        rec = {"id": sb._logins.mint_id(), "label": "Work", "tokenCmd": "printf '%%s' '%s'" % token,
               "addedAt": int(__import__("time").time()) - 86400}
        sb._logins.write_record(self.kernel_state, rec)
        sb.write_sdk_default(self.kernel_state, model="claude-opus-5-5", auth="login", authExplicit=True,
                             authLogin=rec["id"])
        before = self._probe_launch_env()
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", before, "a fresh state root: no default, no credential")
        self.assertEqual(sb.subagent_canary_billing(self.kernel_state, self.probe_state), "login %s" % rec["id"])
        env = self._probe_launch_env()
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), token, "the stored login's command, run at the launch")
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "and the skip such a login carries")
        d = sb.read_sdk_defaults(self.probe_state)
        self.assertEqual(sorted(k for k in d if not k.endswith("Tok")), ["auth", "authExplicit", "authLogin"],
                         "the billing only: no model, effort or mode of the user's rides into the probe")

    def test_without_an_explicit_default_the_probe_launches_plain_as_an_unpicked_session_does(self):
        sb.write_sdk_default(self.kernel_state, auth="login", authExplicit=False)   # a retired seed, not a default
        self.assertEqual(sb.subagent_canary_billing(self.kernel_state, self.probe_state), "")
        self.assertFalse((self.probe_state / "sdk-defaults.json").exists())
        sb.write_sdk_default(self.kernel_state, auth="key", authExplicit=True)
        self.assertEqual(sb.subagent_canary_billing(self.kernel_state, self.probe_state), "key")
        self.assertFalse((self.probe_state / sb._logins.LOGINS_DIR).exists(), "a key default carries no record")

    def test_a_default_naming_a_removed_record_falls_to_the_machines_own_login(self):
        """Copied as it stands (the fall itself is the launch's, below in TheProbesBillingParity), so the probe can say
        which billing did not apply."""
        gone = sb._logins.mint_id()   # an id no record carries (minted at run time: a literal reads as a key to the scanner)
        sb.write_sdk_default(self.kernel_state, auth="login", authExplicit=True, authLogin=gone)
        self.assertEqual(sb.subagent_canary_billing(self.kernel_state, self.probe_state), "login %s" % gone)
        self.assertEqual(sb.read_sdk_defaults(self.probe_state).get("authLogin"), gone)
        self.assertFalse((self.probe_state / sb._logins.LOGINS_DIR).exists(), "no record to copy")



# ---- the post-merge review of the probe (2026-10-09) ----

class _Msg:
    """Message doubles by class name, the shapes SdkSession._on_message keys on."""
class _Assistant(_Msg):
    pass
class _Result(_Msg):
    uuid, num_turns = "r1", 1
class _System(_Msg):
    pass
_Assistant.__name__, _Result.__name__, _System.__name__ = "AssistantMessage", "ResultMessage", "SystemMessage"


def _result(**fields):
    return type("ResultMessage", (_Result,), dict(fields))()


async def _noop(*a, **k):
    return None


def _run_time_token():
    """A token-like value assembled at run time (the secret scanner reads this file): distinctive enough that a leak
    anywhere is found by a substring search."""
    return "-".join(["probe", "tok", __import__("secrets").token_hex(8)])


class TheErroredTurnsOwnWords(unittest.TestCase):
    """A probe turn the API refused (a new CLI rejecting one of the recipe's variables 400s the first main-loop call)
    was filed "the probe's turn ended in an error" and nothing more: the error center said the check could not finish and
    never what the CLI said. The settle keeps the result's own words on the session, and the verdict carries them."""

    REFUSAL = "API Error: 400 synthetic rejection of a request setting by a newer CLI"

    def test_the_results_own_words(self):
        self.assertEqual(sb.result_error_text(_result(is_error=True, result=self.REFUSAL, api_error_status=400)),
                         self.REFUSAL)
        self.assertEqual(sb.result_error_text(_result(is_error=True, result=None, subtype="error_during_execution",
                                                      errors=["synthetic execution fault"])),
                         "synthetic execution fault")
        self.assertEqual(sb.result_error_text(_result(is_error=True, result="", api_error_status=529)), "API error 529")
        self.assertEqual(sb.result_error_text(_result(is_error=False, result="DONE")), "", "a clean result is no error")

    def test_the_settle_keeps_the_error_before_the_row_and_the_probes_verdict_says_it(self):
        """Driven end to end below the CLI: a real session settles an errored result over the probe's state root (its
        turn row lands in turns.jsonl), and the probe's turn reads the row and the session's words, as in main()."""
        state = tempfile.mkdtemp()
        Path(state, "session-hosts").write_text("off")
        real = sb.SdkBackend(state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        sess = sb.SdkSession(real, {"sid": SID, "name": "canary", "cwd": state})
        sess._do_refresh_context = sess._do_refresh_usage = _noop
        sess._connected.set()
        sess._sub_std_launched, sess.fast, sess.fast_reason = True, "on", ""
        msg = _result(is_error=True, result=self.REFUSAL, api_error_status=400, subtype="success", duration_ms=5,
                      duration_api_ms=4)
        seen = {}

        class Settling(_StandIn):
            def send(self, sid, text, user=False):
                self.sessions[sid] = sess
                sess.inflight = 1
                asyncio.run(self._settle())

            async def _settle(self):
                sess._handle_stream_message(msg, _Assistant, _Result, _System)
                seen["at_row"] = sess._result_error   # what the probe reads, as the row lands
                await asyncio.sleep(0)
        v = cn.probe_turn(sb, Settling(state), state, SID, timeout=30, tick=0.01)
        self.assertEqual(seen["at_row"], self.REFUSAL)
        self.assertEqual(v["verdict"], "inconclusive",
                         "nothing in the result tells a refused recipe variable from any other refused request")
        self.assertEqual(v["why"], "the probe's turn ended in an error: " + self.REFUSAL)
        clean = _result(is_error=False, result="DONE", subtype="success")
        sess.inflight = 1

        async def settle_clean():
            sess._handle_stream_message(clean, _Assistant, _Result, _System)
            await asyncio.sleep(0)
        asyncio.run(settle_clean())
        self.assertEqual(sess._result_error, "", "a clean result clears it")

    def test_the_error_center_line_carries_the_words(self):
        be = _backend()
        msgs = []
        be._log = lambda m, **k: msgs.append(m)
        v = sb.subagent_canary_verdict({}, launched=True, finished=True, fast="on", fast_reason="", started=True,
                                       errored=True, error_text=self.REFUSAL)
        be._canary_finished("2.1.282", v)
        self.assertTrue(any(self.REFUSAL in m and "could not check" in m for m in msgs), msgs)
        self.assertIn(self.REFUSAL, be.recipe_hold(), "and the row's hold line")


class AStartFailureNamesOnlyItsOwnLaunch(unittest.TestCase):
    """With no launch error recorded, the reason fell back to the backend's newest problem of any kind, which could be
    a line about something else entirely. Only the probe's session's own launch error is named; otherwise the verdict
    says no reason was recorded."""

    def test_an_unrelated_problem_is_not_the_reason(self):
        state = tempfile.mkdtemp()

        class Noisy(_StandIn):
            def problems(self, limit=0):
                return [{"text": "synthetic unrelated problem about some other session"}]
        v = cn.probe_turn(sb, Noisy(state), state, SID, timeout=0.05, tick=0.01)
        self.assertEqual(v["verdict"], "inconclusive")
        self.assertEqual(v["why"], "the probe's session did not start: no reason was recorded for its launch")


class TheProbesBillingParity(_BillingScratch, unittest.TestCase):
    """The billing the probe was given, when it cannot apply: its launch falls exactly as an unpicked session's launch
    does on the kernel's own state (a refused, expired or removed stored login: to the machine's own login), the verdict
    says why in the backend's own words, a token command that fails only in the probe is said rather than read as a
    downstream symptom, and no verdict or log line carries a token."""

    def setUp(self):
        super().setUp()
        Path(self.kernel_state, "session-hosts").write_text("off")
        self.machine_token = _run_time_token()
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": self.machine_token}   # the machine's own login, claimed at boot
        self.lines = []
        self._xdg_before = os.environ.get("XDG_STATE_HOME")

    def tearDown(self):
        sb._cred._HELPER_ENV_PINNED.clear()
        if self._xdg_before is None:
            os.environ.pop("XDG_STATE_HOME", None)
        else:
            os.environ["XDG_STATE_HOME"] = self._xdg_before
        super().tearDown()

    def _launch(self, state, login_ok=True):
        """(the launch's environment, the backend) for an unpicked session's launch over `state`."""
        be = sb.SdkBackend(str(state), "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.lines.append(m))
        be.login_ok = lambda: login_ok
        sid = be.spawn("canary", str(state), sid=SID)
        s = sb.SdkSession(be, dict(sb.read_reg(Path(state), sid), model=sb.SUBAGENT_STANDARD_MODEL, fast=True))
        return be._options(s, dict)["env"], be

    def _stored(self, cmd, **extra):
        rec = dict({"id": sb._logins.mint_id(), "label": "Work", "tokenCmd": cmd,
                    "addedAt": int(__import__("time").time()) - 86400}, **extra)
        sb._logins.write_record(self.kernel_state, rec)
        sb.write_sdk_default(self.kernel_state, model="claude-opus-5-5", auth="login", authExplicit=True,
                             authLogin=rec["id"])
        return rec

    def test_a_refused_an_expired_and_a_removed_stored_login_fall_as_a_sessions_launch_does(self):
        year = 366 * 86400
        for case, extra, why in (("refused", {"refused": "synthetic refusal"}, "the Work login was refused: synthetic refusal"),
                                 ("expired", {"addedAt": int(__import__("time").time()) - year},
                                  "the Work login's token is a year old and has expired; add it again"),
                                 ("removed", None, "no stored login with that id")):
            with self.subTest(case=case):
                self.kernel_state, self.probe_state = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
                for d in (self.kernel_state, self.probe_state):
                    Path(d, "session-hosts").write_text("off")
                stored = _run_time_token()
                rec = self._stored("printf '%%s' '%s'" % stored, **(extra or {}))
                if extra is None:
                    sb._logins.remove(self.kernel_state, rec["id"])
                session_env, _ = self._launch(self.kernel_state)
                billing = sb.subagent_canary_billing(self.kernel_state, self.probe_state)
                probe_env, probe_be = self._launch(self.probe_state)
                self.assertEqual(session_env.get("CLAUDE_CODE_OAUTH_TOKEN"), self.machine_token,
                                 "the session's launch falls to the machine's own login")
                self.assertEqual(probe_env.get("CLAUDE_CODE_OAUTH_TOKEN"), session_env.get("CLAUDE_CODE_OAUTH_TOKEN"),
                                 "and the probe's falls the same way")
                self.assertNotIn(stored, json.dumps(probe_env) + json.dumps(session_env), "the dead login's command never ran")
                self.assertEqual(cn._billing_refusal(probe_be, billing), why)
                v = sb.subagent_canary_verdict({}, launched=True, finished=True, fast="off", fast_reason="preference",
                                               started=True, billing_refused=cn._billing_refusal(probe_be, billing))
                self.assertEqual(v["why"], "the probe could not use the machine's default billing (%s); fast mode is not "
                                           "available here (preference)" % why)

    def test_a_token_command_failing_in_the_probe_is_said_and_the_kernels_record_is_untouched(self):
        rec = self._stored("exit 3")
        billing = sb.subagent_canary_billing(self.kernel_state, self.probe_state)
        env, be = self._launch(self.probe_state, login_ok=False)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), self.machine_token, "the launch took the fall")
        why = cn._billing_refusal(be, billing)
        self.assertEqual(why, "the Work login was refused: the token command did not answer: the token command failed "
                              "(non-zero exit)")
        self.assertNotIn("refused", sb._logins.read_record(self.kernel_state, rec["id"]),
                         "the refusal is the probe's copy's, never the kernel's record")
        state = tempfile.mkdtemp()

        class Billed(_StandIn):
            def auth_unavailable_why(self, side, lid=""):
                return be.auth_unavailable_why(side, lid)
        sess = types.SimpleNamespace(_connected=threading.Event(), _sub_std_launched=True, fast="off",
                                     fast_reason="preference")
        sess._connected.set()
        v = cn.probe_turn(sb, Billed(state, session=sess, row={"isError": True}), state, SID, timeout=30, tick=0.01,
                          billing=billing)
        self.assertEqual(v["why"], "the probe could not use the machine's default billing (%s); the probe's turn ended "
                                   "in an error" % why)

    def test_the_token_command_sees_the_kernels_xdg_state_home(self):
        """The probe runs under XDG_STATE_HOME = its temp root; a token command that reads its store there worked at
        every session's launch and failed only in the probe. Pinned to the kernel's value, it sees what a session's does."""
        kernel_xdg = tempfile.mkdtemp()
        token = _run_time_token()
        os.makedirs(os.path.join(kernel_xdg, "synthetic-store"))
        Path(kernel_xdg, "synthetic-store", "value").write_text(token)
        rec = self._stored('cat "$XDG_STATE_HOME/synthetic-store/value"')
        billing = sb.subagent_canary_billing(self.kernel_state, self.probe_state)
        os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()           # the probe's own temp root
        env, be = self._launch(self.probe_state, login_ok=False)
        self.assertNotEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), token, "unpinned: the command reads the probe's root")
        self.assertIn("token command", cn._billing_refusal(be, billing))
        sb._logins.clear_refused(self.probe_state, rec["id"])
        sb._cred.pin_helper_env(XDG_STATE_HOME=kernel_xdg)
        env, be = self._launch(self.probe_state, login_ok=False)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), token, "pinned: the kernel's store, as at a session's launch")
        self.assertEqual(cn._billing_refusal(be, billing), "")
        sb._cred.pin_helper_env(XDG_STATE_HOME="")
        self.assertNotIn("XDG_STATE_HOME", sb._cred.helper_env(), "a kernel with none: the helper sees none")
        with self.assertRaises(ValueError):
            sb._cred.pin_helper_env(SOME_SECRET="x")                   # a pin never widens what a helper sees

    def test_the_copy_is_private_and_holds_the_command_not_the_token(self):
        token = _run_time_token()
        rec = self._stored("printf '%%s' '%s'" % token.replace("-", "_"))   # the command, whatever it prints
        os.chmod(sb._logins.logins_dir(self.kernel_state), 0o755)             # whatever the kernel's modes are
        sb.subagent_canary_billing(self.kernel_state, self.probe_state)
        d = sb._logins.logins_dir(self.probe_state)
        self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(sb._logins.record_path(self.probe_state, rec["id"])).st_mode), 0o600)
        self.assertEqual(sb._logins.read_record(self.probe_state, rec["id"]), rec, "metadata and the command, as stored")
        self.assertNotIn(token, sb._logins.record_path(self.probe_state, rec["id"]).read_text())

    def test_no_verdict_or_log_line_carries_a_token(self):
        """Three commands: one that answers (the token reaches the launch's environment, so the search below is not
        vacuous), one that prints the token and then fails, one that prints it twice on a line (no valid key). Neither
        the backend's log lines, nor its problem ring, nor the refusal, nor the verdict the probe prints carries it."""
        for case in ("answers", "prints-then-fails", "prints-junk"):
            with self.subTest(case=case):
                self.kernel_state, self.probe_state = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
                for d in (self.kernel_state, self.probe_state):
                    Path(d, "session-hosts").write_text("off")
                token = _run_time_token()
                cmd = {"answers": "printf '%%s' '%s'" % token,
                       "prints-then-fails": "printf '%%s' '%s'; exit 1" % token,
                       "prints-junk": "printf '%%s %%s' '%s' '%s'" % (token, token)}[case]
                self._stored(cmd)
                self.lines = []
                billing = sb.subagent_canary_billing(self.kernel_state, self.probe_state)
                env, be = self._launch(self.probe_state, login_ok=False)
                if case == "answers":
                    self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), token)
                else:
                    self.assertNotEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), token)
                refusal = cn._billing_refusal(be, billing)
                v = sb.subagent_canary_verdict({}, launched=True, finished=True, fast="off", fast_reason="preference",
                                               started=True, errored=True, error_text="API Error: 401 synthetic",
                                               billing_refused=refusal)
                emitted = "\n".join(self.lines + [r["text"] for r in be.problems()] + [json.dumps(v)])
                self.assertNotIn(token, emitted)
                if case != "answers":
                    self.assertIn("the token command", refusal)


class ATimedOutProbe(unittest.TestCase):
    """A probe killed at the kernel's time limit left its temp root, with the copied login record in it, until the next
    probe's start or the kernel's boot. The kernel sweeps right after the timeout: subprocess.run killed and reaped the
    probe first, so its pid file names no process; a running probe's directories stay."""

    def test_the_kernel_sweeps_a_timed_out_probes_directories_at_once(self):
        be = _backend()
        tmp = tempfile.mkdtemp()
        dead_root = Path(tmp, sb.SUBAGENT_CANARY_PREFIX + "timedout1")
        dead_work = Path(tmp, sb.SUBAGENT_CANARY_PREFIX + "work-timedout2")
        live_root = Path(tmp, sb.SUBAGENT_CANARY_PREFIX + "running3")

        def run(argv, **kw):
            for d, pid in ((dead_root, 999999999), (dead_work, 999999999), (live_root, os.getpid())):
                d.mkdir()
                (d / sb.SUBAGENT_CANARY_PID_FILE).write_text(str(pid))
            sb._logins.write_record(dead_root / "romp", {"id": sb._logins.mint_id(), "label": "Work", "tokenCmd": "true"})
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))
        saved = (sb.subprocess.run, sb.tempfile.tempdir)
        sb.subprocess.run, sb.tempfile.tempdir = run, tmp
        try:
            v = be._run_canary_subprocess("2.1.282")
        finally:
            sb.subprocess.run, sb.tempfile.tempdir = saved
        self.assertEqual(v["verdict"], "inconclusive")
        self.assertIn("did not finish within", v["why"])
        self.assertFalse(dead_root.exists(), "the timed-out probe's temp root and its login copy go at once")
        self.assertFalse(dead_work.exists())
        self.assertTrue(live_root.exists(), "never a running probe's")

    def test_the_kernel_hands_the_probe_its_xdg_state_home(self):
        be = _backend()
        seen = {}

        def run(argv, **kw):
            seen["argv"] = argv
            return types.SimpleNamespace(stdout='{"verdict": "pass", "why": ""}\n', stderr="", returncode=0)
        saved = sb.subprocess.run
        sb.subprocess.run = run
        try:
            be._run_canary_subprocess("2.1.282")
        finally:
            sb.subprocess.run = saved
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--helper-xdg-state") + 1], os.environ.get("XDG_STATE_HOME", ""))


class TheProbeScriptPinsAndCleansUp(unittest.TestCase):
    """The probe script itself, run as the kernel runs it, over a stand-in kernel directory: it pins the kernel's
    XDG_STATE_HOME for the helpers it runs before its backend loads, and its temp root (where the login record was
    copied) is gone when it exits."""

    def test_the_pin_before_the_backend_and_no_temp_root_after(self):
        kdir, kstate, tmp = tempfile.mkdtemp(), tempfile.mkdtemp(), tempfile.mkdtemp()
        Path(kdir, "sdk_backend.py").write_text(
            "import os, types\n"
            "PINNED = {}\n"
            "_cred = types.SimpleNamespace(pin_helper_env=lambda **kw: PINNED.update(kw))\n"
            "def subagent_canary_billing(kernel_state, probe_state):\n"
            "    os.makedirs(os.path.join(probe_state, 'logins'))\n"
            "    open(os.path.join(kernel_state, 'probe-state-was'), 'w').write(probe_state)\n"
            "    return ''\n"
            "class SdkBackend:\n"
            "    def __init__(self, state, *a, **k):\n"
            "        raise RuntimeError('pinned %r' % PINNED.get('XDG_STATE_HOME'))\n")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["TMPDIR"] = tmp
        p = subprocess.run([sys.executable, CANARY, "--claude", "/bin/true", "--kernel-dir", kdir, "--kernel-state", kstate,
                            "--helper-xdg-state", "/synthetic/kernel/xdg"], env=env, capture_output=True, text=True,
                           timeout=60, stdin=subprocess.DEVNULL)
        v = json.loads(p.stdout.strip().splitlines()[-1])
        self.assertEqual(v["why"], "the probe failed: pinned '/synthetic/kernel/xdg'", p.stderr[-500:])
        was = Path(kstate, "probe-state-was").read_text()
        self.assertTrue(was.startswith(tmp))
        self.assertFalse(os.path.exists(was), "the probe's state, and the record copied into it, are gone")
        self.assertEqual([n for n in os.listdir(tmp) if n.startswith(sb.SUBAGENT_CANARY_PREFIX)], [])


if __name__ == "__main__":
    unittest.main()
