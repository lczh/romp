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
  * inside the probe's own process the gate is open (it is the probe).

Synthetic fixtures only: placeholder sids, an invented session name, a fake binary.
"""
import json
import os
import stat
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


class TheProbesBilling(unittest.TestCase):
    """On a machine whose one login is a stored one (no login of the machine's own, no apiKeyHelper), every session
    follows the machine default and launches with that login's token. The probe's fresh state root had no default, so
    its CLI started with no credential at all, refused fast mode ("preference") and ended its turn in an error, while
    the sessions it gates ran fast. Its state now takes the kernel's machine default billing, the stored login's record
    with it, and its launch decides on the road an unpicked session's launch takes."""

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
        gone = sb._logins.mint_id()   # an id no record carries (minted at run time: a literal reads as a key to the scanner)
        sb.write_sdk_default(self.kernel_state, auth="login", authExplicit=True, authLogin=gone)
        self.assertEqual(sb.subagent_canary_billing(self.kernel_state, self.probe_state), "login")
        self.assertEqual(sb.read_sdk_defaults(self.probe_state).get("authLogin"), "")


if __name__ == "__main__":
    unittest.main()
