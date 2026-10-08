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


class Probe(unittest.TestCase):
    def test_the_verdict_from_the_speed_checks_figures(self):
        v = sb.subagent_canary_verdict
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

    def test_the_probe_script_is_isolated(self):
        src = Path(os.path.dirname(HERE), "kernel", "subagent_canary.py").read_text()
        for pin in ('"session-hosts"', "tempfile.mkdtemp", "XDG_STATE_HOME", "shutil.rmtree", "be.kill(sid)",
                    "uuid.uuid4()"):
            self.assertTrue(pin in src, "the probe script lacks %s" % pin)
        self.assertNotIn("mcp_config", src, "no peer messaging in the probe")


if __name__ == "__main__":
    unittest.main()
