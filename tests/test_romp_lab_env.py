#!/usr/bin/env python3
"""A lab started from inside a live session must not run as that session (2026-10-08).

tools/romp-lab/lab.sh booted its kernel with whatever environment it was started from. Started from a live romp session,
that is the session's identity: ROMP_SID and ROMP_SESSION_NAME, the Claude Code session id the postal tools read to know
who they are, the CLI's own messaging socket, the live kernel's port and its manager's pid, ROMP_SUPERVISED (which turns
per-session systemd scopes on) and the user bus that lets `systemctl --user` stop the live session's units. The kernel
hands its environment to every CLI it starts, so the lab's sessions, their hooks and their postal tools ran as the live
session.

This runs the lab's own entry, `lab.sh --env-only`, under a POLLUTED environment: every live-session name above and
then some, each carrying a canary value, plus a name nobody has invented yet, a name bash cannot import (bash passes
those through to its children untouched), and an exported bash function. The lab boots its real kernel; the kernel
starts a real session whose CLI is the lab's probe (tools/romp-lab/env-probe-cli.py), which writes down its
environment. The test then reads the raw evidence itself: the kernel's environment and the CLI's carry no canary, the
CLI runs under the lab's own new session id, and the lab's state root has per-session hosts off.

The Agent SDK is not installed where the suite runs, so the lab kernel finds a stand-in where it looks for the SDK
venv (the lab links $HOME/.local/state/romp/sdkvenv, and HOME here is a scratch directory): the served labs' fake
(tests/fixtures/fake_agent_sdk) with one addition, it spawns the CLI the options name with the options' env merged
over the kernel's own environment, which is what the real SDK's transport does (kernel/sdk_backend.py's _options
relies on that merge). Synthetic values only; no real session, port or path of this machine is used.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
LAB_SH = ROOT / "tools" / "romp-lab" / "lab.sh"
FAKE_SDK = HERE / "fixtures" / "fake_agent_sdk" / "claude_agent_sdk" / "__init__.py"

CANARY = "LIVE" + "CANARY"      # assembled here so no literal in this file looks like a value someone could reuse
LIVE_SID = "99999999-8888-7777-6666-555555555555"   # the "live" session the lab is started from: synthetic

# Every name a live session's shell carries that says who or where it is, and a few that would steer the lab to the
# live machine's services. Most values are canaries; a name the kernel parses (a port, a pid, a switch, the bind host)
# gets a plausible value instead (a canary there would only crash the kernel before it showed anything), and a path
# gets a directory in the test's scratch tree (a leak lands there, never in real state). Every value is also checked by
# equality, so a plausible one that leaked is caught as surely as a canary.
POLLUTION_NAMES = (
    "ROMP_SID", "ROMP_SESSION_NAME", "ROMP_STATE_TAG", "ROMP_STATE_DIR", "ROMP_SERVE_PORT", "ROMP_SERVE_HOST",
    "ROMP_KERNEL_PORT", "ROMP_SERVE_TOKEN", "ROMP_MANAGER_PORT", "ROMP_MANAGER_PID", "ROMP_SUPERVISED", "ROMP_DIR",
    "ROMP_BIN", "ROMP_CLI_REAL", "ROMP_CLAUDE_BIN", "ROMP_EXPECTED_AUTH", "ROMP_SHUTDOWN_GRACE_MS", "ROMP_POSTAL_PORT",
    "ROMP_HOST_NAME", "ROMP_CLI_SCOPE",
    "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDECODE",
    "CLAUDE_CODE_CHILD_SESSION", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_SESSION_ATTENDED",
    "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_AGENT_SDK_VERSION", "AI_AGENT",
    "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
    "INVOCATION_ID", "JOURNAL_STREAM", "MANAGERPID", "SYSTEMD_EXEC_PID", "MEMORY_PRESSURE_WATCH",
    "ROMP_ZZ_IDENTITY_NOT_INVENTED_YET",
)
PARSED = {"ROMP_SERVE_HOST": "127.0.0.1", "ROMP_SERVE_PORT": "9", "ROMP_KERNEL_PORT": "9", "ROMP_MANAGER_PORT": "9", "ROMP_POSTAL_PORT": "9",
          "ROMP_SHUTDOWN_GRACE_MS": "8000", "ROMP_SUPERVISED": "1", "ROMP_CLI_SCOPE": "1", "CLAUDECODE": "1",
          "CLAUDE_CODE_CHILD_SESSION": "1", "CLAUDE_CODE_SESSION_ATTENDED": "0"}
PIDS = ("ROMP_MANAGER_PID", "CLAUDE_PID", "MANAGERPID", "SYSTEMD_EXEC_PID")   # this test's own pid: alive, and no one's manager
PATHS = ("ROMP_STATE_DIR", "XDG_STATE_HOME", "XDG_CONFIG_HOME", "XDG_RUNTIME_DIR")
NOT_AN_IDENTIFIER = "ROMP-LIVE-IDENTITY"          # bash will not import this name, but hands it on to every child
EXPORTED_FUNCTION = "BASH_FUNC_romp_live%%"        # an exported bash function, as `export -f` puts it in the environment
# names that must not reach the lab at all, whatever their value: the live session's addressing and the user bus
ABSENT_IN_LAB = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDECODE",
                 "CLAUDE_PID", "ROMP_STATE_DIR", "ROMP_MANAGER_PID", "ROMP_SUPERVISED", "XDG_RUNTIME_DIR",
                 "DBUS_SESSION_BUS_ADDRESS", "INVOCATION_ID", "ROMP_ZZ_IDENTITY_NOT_INVENTED_YET", NOT_AN_IDENTIFIER,
                 EXPORTED_FUNCTION)
SDK_SHIM = '''"""The served labs' fake claude_agent_sdk, plus the real transport's spawn: the CLI the options name is run with the
options' env merged over this process's environment, then the scripted client carries on in-process."""
import os as _os
import subprocess as _subprocess

_FIXTURE = %r
exec(compile(open(_FIXTURE).read(), _FIXTURE, "exec"))
_ScriptedClient = ClaudeSDKClient


class ClaudeSDKClient(_ScriptedClient):
    async def __aenter__(self):
        o = self.options
        cli = getattr(o, "cli_path", None)
        if cli:
            _subprocess.run([str(cli)], env={**_os.environ, **(getattr(o, "env", None) or {})},
                            cwd=getattr(o, "cwd", None) or None, stdin=_subprocess.DEVNULL,
                            stdout=_subprocess.DEVNULL, stderr=_subprocess.DEVNULL, timeout=30)
        return await super().__aenter__()
'''


def _python_tag(path_env):
    """The lab kernel's interpreter (`env python3` on the PATH the lab keeps) as venv names its lib dir."""
    out = subprocess.run(["python3", "-c", "import sys; print('%d.%d%s' % (sys.version_info[0], sys.version_info[1], "
                          "'t' if 't' in getattr(sys, 'abiflags', '') else ''))"],
                         env={"PATH": path_env}, capture_output=True, text=True, check=True)
    return out.stdout.strip()


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "the lab entry is a bash script")
class TheLabStartsFromAnAllowlist(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="romp-lab-env-"))
        home = cls.scratch / "home"
        site = home / ".local" / "state" / "romp" / "sdkvenv" / "lib" / ("python" + _python_tag(os.environ["PATH"])) / "site-packages"
        (site / "claude_agent_sdk").mkdir(parents=True)
        (site / "claude_agent_sdk" / "__init__.py").write_text(SDK_SHIM % str(FAKE_SDK))
        (home / ".claude").mkdir()
        (cls.scratch / "tmp").mkdir()
        env = {"PATH": os.environ["PATH"], "HOME": str(home), "TMPDIR": str(cls.scratch / "tmp"), "LANG": "C.UTF-8",
               "CLAUDE_CONFIG_DIR": str(home / ".claude")}
        pollution = {}
        for name in POLLUTION_NAMES:
            pollution[name] = "%s-%s" % (CANARY, name.lower())
        pollution.update(PARSED)
        pollution.update({name: str(os.getpid()) for name in PIDS})
        pollution.update({name: str(cls.scratch / ("live-" + name.lower() + "-" + CANARY)) for name in PATHS})
        pollution["ROMP_SID"] = LIVE_SID
        pollution[NOT_AN_IDENTIFIER] = CANARY
        pollution[EXPORTED_FUNCTION] = "() { echo %s; }" % CANARY
        env.update(pollution)
        cls.pollution = pollution
        cls.lab_run = subprocess.run(["bash", str(LAB_SH), "--env-only", "--keep"], env=env, cwd=str(ROOT),
                                 capture_output=True, text=True, timeout=240)
        cls.lab = None
        for line in cls.lab_run.stdout.splitlines():
            if line.startswith("kept: "):
                cls.lab = Path(line[len("kept: "):].split(" ", 1)[0])
        cls.probe = cls.lab / "probe" if cls.lab else None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def _say(self):
        return "lab exit %s\n--- stdout\n%s\n--- stderr\n%s" % (self.lab_run.returncode, self.lab_run.stdout[-4000:],
                                                              self.lab_run.stderr[-4000:])

    def _kernel_env(self):
        p = self.probe / "kernel-env.json" if self.probe else None
        if p is None or not p.exists():
            if not Path("/proc/self/environ").exists():
                self.skipTest("no /proc here: the kernel's environment is read through it")
            self.fail("the lab wrote no kernel environment\n" + self._say())
        return json.loads(p.read_text())

    def _cli_envs(self):
        envs = [json.loads(p.read_text())["env"] for p in sorted(self.probe.glob("cli-*.json"))] if self.probe else []
        self.assertTrue(envs, "no CLI the lab kernel started wrote its environment\n" + self._say())
        return envs

    def _assert_clean(self, env, who):
        leaked = sorted(set(n for n, v in env.items() if CANARY in n or CANARY in v or LIVE_SID in v)
                        | set(n for n, v in self.pollution.items() if env.get(n) == v))
        self.assertEqual(leaked, [], "%s carries the live session's environment: %s" % (who, leaked))
        present = sorted(n for n in ABSENT_IN_LAB if n in env)
        self.assertEqual(present, [], "%s carries names a lab must never have: %s" % (who, present))

    def test_the_lab_entry_succeeds_under_a_polluted_environment(self):
        self.assertIsNotNone(self.lab, "the lab named no kept directory\n" + self._say())
        self.assertEqual(self.lab_run.returncode, 0, self._say())

    def test_the_lab_kernel_carries_nothing_of_the_live_session(self):
        self._assert_clean(self._kernel_env(), "the lab kernel")

    def test_the_cli_the_lab_kernel_starts_carries_nothing_of_the_live_session(self):
        for env in self._cli_envs():
            self._assert_clean(env, "a CLI the lab kernel started")

    def test_the_cli_runs_under_the_labs_own_new_session(self):
        new = json.loads((self.probe / "new.json").read_text())
        self.assertTrue(new.get("ok"), new)
        sid = new["id"]
        self.assertRegex(sid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertNotEqual(sid, LIVE_SID)
        mine = [e for e in self._cli_envs() if e.get("ROMP_SID") == sid]
        self.assertTrue(mine, "no CLI ran under the lab's session id %s\n%s" % (sid, self._say()))
        self.assertEqual({e.get("ROMP_SESSION_NAME") for e in mine}, {"envprobe"})

    def test_the_lab_kernel_reaches_no_service_of_the_live_machine(self):
        env = self._kernel_env()
        state = str(self.lab / "state")
        self.assertEqual(env.get("XDG_STATE_HOME"), state, "the lab's own state root")
        self.assertEqual(env.get("ROMP_MANAGER_PORT"), "1", "a dead manager port, never the live manager's")
        self.assertEqual(env.get("ROMP_CLI_SCOPE"), "0", "no per-session systemd scopes")
        self.assertEqual(env.get("ROMP_SERVE_PORT"), env.get("ROMP_KERNEL_PORT"), "the lab's own kernel, both spellings")
        self.assertEqual((env.get("ROMP_POSTAL_PEERS"), env.get("ROMP_POSTAL_CLIENT_ONLY"), env.get("ROMP_POSTAL_HERMETIC")),
                         ("0", "1", "1"), "a postal bus of the lab's own, never started")
        self.assertNotEqual(env.get("ROMP_POSTAL_PORT"), "25302", "never the machine's fixed bus port")
        self.assertEqual(env.get("ROMP_UPDATE_CHECK"), "off", "no update notice whose click would move the checkout")

    def test_session_hosts_are_off_in_the_labs_state_root(self):
        self.assertIsNotNone(self.lab, self._say())
        self.assertEqual((self.lab / "state" / "romp" / "session-hosts").read_text().strip(), "off")


FAKE_NODE = r"""#!/bin/sh
# Stands in for node in the lab's other modes: the bundle build is a no-op, and a phase driver (it gets LAB_DIR) writes
# down the environment of the lab kernel it was started beside (its sibling under lab.sh) instead of driving a browser.
case "$1" in *esbuild.js) exit 0 ;; esac
if [ -n "$LAB_DIR" ]; then
  k=$(pgrep -P "$PPID" -f bin/romp-kernel | head -1)
  [ -n "$k" ] && tr '\000' '\n' < "/proc/$k/environ" > "$LAB_DIR/kernel-env-$(basename "$1" .mjs).txt"
  exit 0
fi
[ -n "%(real)s" ] && exec "%(real)s" "$@"
exit 0
"""


@unittest.skipUnless(shutil.which("bash") and shutil.which("python3"), "the lab entry is a bash script")
class TheReadBackAllowsWhatMacOSWritesItself(unittest.TestCase):
    """lab.sh's read-back runs in python3, and on macOS CoreFoundation writes __CF_USER_TEXT_ENCODING into that
    process's own environment at startup, even under `env -i`. The read-back counted it as the caller's, so on a Mac the
    lab refused every start (2026-11-24). Driven straight at the read-back on every platform: the caller sets the
    re-exec marker itself (skipping the `env -i` that would drop the name on Linux), carries the macOS name and one
    stray name, and the refusal names the stray name alone. It stops at the read-back: no lab, no kernel."""

    def test_the_refusal_names_the_stray_name_and_not_the_macos_one(self):
        scratch = Path(tempfile.mkdtemp(prefix="romp-lab-readback-"))
        self.addCleanup(shutil.rmtree, scratch, True)
        env = {"PATH": os.environ["PATH"], "HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C.UTF-8",
               "ROMP_LAB_REEXEC": "1", "__CF_USER_TEXT_ENCODING": "0x0:0x0:0x0", "LAB_STRAY_" + CANARY: "1"}
        run = subprocess.run(["bash", str(LAB_SH), "--env-only"], env=env, cwd=str(ROOT), capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(run.returncode, 2, "the stray name is refused at the read-back\n%s" % run.stderr[-2000:])
        self.assertIn("outside the lab's allowlist (LAB_STRAY_%s)" % CANARY, run.stderr,
                      "the refusal names the stray name alone, not __CF_USER_TEXT_ENCODING")
        self.assertEqual(list(scratch.iterdir()), [], "refused before the lab made anything")


@unittest.skipUnless(Path("/proc/self/environ").exists() and shutil.which("pgrep"), "reads the kernel's environment through /proc")
class EveryModeRunsWithoutTheUpdateCheck(unittest.TestCase):
    """The lab kernel's update check offers a one-click update; a click fast-forwards the checkout lab.sh lives in, and
    on the maintainer's machine that move is pushed to the other kernel's machine, which restarts (the review of #173,
    2026-10-08). Only --env-only turned the check off. Every mode runs here with node replaced (no build, no browser), and
    each phase driver reads the real lab kernel's environment."""

    MODES = ("", "--banner-only", "--highlight-only", "--modes-only", "--login-only")

    @classmethod
    def setUpClass(cls):
        cls.scratch = Path(tempfile.mkdtemp(prefix="romp-lab-modes-"))
        bindir = cls.scratch / "bin"
        bindir.mkdir()
        node = bindir / "node"
        node.write_text(FAKE_NODE % {"real": shutil.which("node") or ""})
        node.chmod(0o755)
        (cls.scratch / "home").mkdir()
        (cls.scratch / "tmp").mkdir()
        cls.env = {"PATH": "%s:%s" % (bindir, os.environ["PATH"]), "HOME": str(cls.scratch / "home"),
                   "TMPDIR": str(cls.scratch / "tmp"), "LANG": "C.UTF-8"}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.scratch, ignore_errors=True)

    def _dumps(self, mode):
        run = subprocess.run(["bash", str(LAB_SH), "--keep"] + ([mode] if mode else []), env=self.env, cwd=str(ROOT),
                             capture_output=True, text=True, timeout=240)
        lab = next((Path(l[len("kept: "):].split(" ", 1)[0]) for l in run.stdout.splitlines() if l.startswith("kept: ")), None)
        self.assertIsNotNone(lab, "lab %s: exit %s\n%s\n%s" % (mode or "(all)", run.returncode, run.stdout[-3000:], run.stderr[-3000:]))
        dumps = sorted(lab.glob("kernel-env-*.txt"))
        self.assertTrue(dumps, "lab %s: no phase read the kernel's environment\n%s" % (mode or "(all)", run.stdout[-3000:]))
        return {p.name: dict(l.split("=", 1) for l in p.read_text().splitlines() if "=" in l) for p in dumps}

    def test_no_mode_runs_the_update_check(self):
        for mode in self.MODES:
            with self.subTest(mode=mode or "(all)"):
                for name, env in self._dumps(mode).items():
                    self.assertEqual(env.get("ROMP_UPDATE_CHECK"), "off", "%s %s: the update check stays off" % (mode, name))


if __name__ == "__main__":
    unittest.main()
