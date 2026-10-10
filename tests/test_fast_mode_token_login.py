#!/usr/bin/env python3
"""Fast mode under a login riding the environment (2026-10-01): sessions billed to an enterprise seat's stored login
(a `claude setup-token` bearer handed to the CLI as CLAUDE_CODE_OAUTH_TOKEN) had fast mode refused with the reason
"preference", and the pick turned back off, on a seat that serves fast mode.

The cause, reproduced in a lab with a synthetic sid and a scratch config dir on Claude Code 2.1.282: the CLI gives an
environment token inference-only scopes, so it never asks the organization's fast-mode setting for that login (and the
endpoint refuses a setup-token anyway); it GUESSES from a flag cached in its config directory, and the guess read
"disabled". The launch-time fastMode opt-in and the runtime toggle were refused alike. With the CLI's
CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK the same launch served fast, and the API stays the authority: an organization that
has fast mode off answers a fast request with a 400 the CLI records as the server's "preference", which the skip does not
override.

The mechanics under test:
  * every launch whose login rides the environment and that carries the fast flag, on Claude Code 2.1.271 or later,
    carries the skip (sdk_backend.token_fast_org_env), a stored login and a machine login the manager's environment
    carries alike; a login the CLI holds in its own store, and a key launch, do not get it from this rule; a launch
    without the flag reads no version and carries no skip;
  * the skip only on Claude Code 2.1.271 or later (review 2026-10-01: below it the skip hides the API's refusal, the CLI
    reporting fast mode on while each fast request is refused and rerun at standard speed), the version read from the
    binary's own -v once per binary file, and again after a read that did not answer (claude_cli_version), its stdout
    only, as the SDK reads it;
    below the floor, or with no version, the CLI's own check stands and the kernel log says so once, and a refusal that
    check gives turns the pick off or holds Always fast back: recorded as checked below the floor; when the version is
    unknown, its checked mark is held in this kernel's memory alone and not written to the registry, where an older mark
    is cleared. A refusal at a launch without the flag is recorded as checked. A refusal recorded below the floor
    outlasts an update of Claude Code: a refused own pick stays off until fast mode is turned on for the session again,
    and Always fast stays held back until fast mode is turned on for the session again, until Always fast is turned off
    or on, or until the session launches on another billing; with both on, the refused own pick holds Always fast back
    from the pick's flagless relaunch on, with no Always fast line;
  * a "preference" from a CLI this kernel only attached to is blanked when ITS launch had the token and no skip (the
    launch record's fastGuess, which the host echoes in its hello), or when the hello carries no launch
    record (the kernel cannot tell, and the log line says it may be a guess), and today's options carry the token with a
    version read that is not below the floor, and no refusal is marked checked for the session, in the registry or in
    this kernel's memory (one a reg written before the fix carries does not count): the pick stays on, nothing is
    remembered, and the session relaunches on the quiet road when it wants fast mode (fast_effective), after which the
    CLI's own answer decides; from a CLI launched WITH the skip it is the API's answer, loud, from the first restart on
    (review 2026-10-01: the rule read today's options, and blanked such a refusal); the skip alone makes no token login
    (a key whose account allows fast mode carries it too, with no token);
  * a "preference" from a launch that carried the skip, or from a session the CLI can ask about (a managed policy with
    fastMode false, say), is real: loud, the pick back off, remembered as checked;
  * the toast and the Always fast line name Claude Code's own check for a "preference" from a CLI known to have been
    launched with the fast flag, the token and no skip, adding 2.1.271 when this connection's version read is below the
    floor and the unread version when that read failed; at an attach with no launch record they say the refusal may
    come from that check; otherwise they keep _FAST_REFUSALS' words;
  * a "preference" a reg written before the fix still carries no longer holds Always fast back, while a checked one, or
    any other reason, still does.
The judges' fast calls billed to such a login carry the skip too, under the same floor (tests/test_judge_fast_tiers.py).

Synthetic sids, names and tokens only; a token-shaped string is assembled at run time, never a literal. The claude a
backend launches is a stand-in script that prints the version a test names; no real CLI runs."""
import asyncio
import json
import os
import secrets
import sys
import tempfile
import time
import types
import unittest
import unittest.mock
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_fasttoken", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-00000000a101"   # private to this module (CLAUDE.md, goal-store fixtures)
FSID = "11111111-2222-4333-8444-00000000f101"
SKIP = {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1"}
GUESS = {"fast_mode_state": "off", "fast_mode_disabled_reason": "preference"}
ON = {"fast_mode_state": "on"}
ORG = "the account's organization has fast mode turned off"   # the refusal's words for a "preference" (_FAST_REFUSALS)
OWN = "Claude Code's own check refused it for this login"      # …for a "preference" from a CLI known to have been launched with the fast flag, the token and no skip
CLI ={"pid": 4242, "start": "100.5", "spawnedAt": 1700000100}   # the CLI a host keeps across the kernel's restart


def _claude(d, version, name="claude", extra=""):
    """A stand-in Claude Code binary in `d` that prints `version` the way `claude -v` does ("2.1.282 (Claude Code)"),
    or nothing when `version` is None; `extra` is shell run first. Never a real CLI."""
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write("#!/bin/sh\n%s%s" % (extra, ("echo '%s (Claude Code)'\n" % version) if version else "exit 0\n"))
    os.chmod(p, 0o755)
    return p


def _token():
    """A synthetic bearer of the setup-token's shape, assembled at run time (the scanner reads this file)."""
    return "".join(["sk", "-ant-", "oat01-", secrets.token_hex(12)])


def _backend(d=None, version="2.1.282", extra=""):
    """A backend over `d` (a fresh root by default) whose claude is a stand-in at `version` (`extra` as _claude's); a
    second one over the same root is the same machine after a kernel restart, its claude perhaps updated meanwhile."""
    d = d or tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    cli = _claude(tempfile.mkdtemp(), version, extra=extra)
    be = sb.SdkBackend(d, cli, lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    be._toasts = []
    be._notify = lambda kind, payload: be._toasts.append(payload)
    return be


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp", "liveModelId": "claude-opus-5"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[r["sid"]] = s
    s.asks = []
    s.want_switch_reconnect = lambda why: s.asks.append(("quiet", why))
    s.request_reconnect = lambda *a, **k: s.asks.append(("now",))
    return s


def _attached_token_cli(s):
    """What the connect leaves on a session that ATTACHED to a CLI surviving the kernel, its login a token, under a host
    spawned before the launch record (the hello carries none, so nothing says whether that launch carried the skip):
    _options (which runs for an attach too) marks the token login, its version read not below the floor, the transport
    marks the attach, the snapshot arms the flag."""
    s._fast_token_login = True
    s._host_is_attach = True
    s._hello_launch = None
    s._fast_unlocked = s.fast_effective()
    s._fast_guess_asked = False


def _relaunched(s):
    """A launch this kernel made: no attach, and the connect's snapshot of the flag."""
    s._host_is_attach = False
    s._fast_unlocked = s.fast_effective()
    s._fast_guess_asked = False


class TheRule(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.lines = []
        self.log = lambda msg, problem=False: self.lines.append((msg, problem))

    def test_a_token_in_the_launch_environment_carries_the_skip_and_nothing_else_does(self):
        """A guard of the token rule on a claude at the floor or later (2.1.282 here): a token in the launch environment
        carries the skip, and no other variable does."""
        cli = _claude(self.d, "2.1.282")
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}, cli), SKIP)
        self.assertEqual(sb.token_fast_org_env({}, cli), {})
        self.assertEqual(sb.token_fast_org_env(None, cli), {})
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": ""}, cli), {})
        self.assertEqual(sb.token_fast_org_env({"ANTHROPIC_AUTH_TOKEN": _token()}, cli), {},
                         "a gateway bearer is not a Claude login: the CLI's own check stands")

    def test_the_skip_needs_claude_code_2_1_271_and_below_it_the_clis_own_check_stands(self):
        """2.1.266 (bundle read): the availability rule gives the organization's reason only while the skip is unset, so
        under it the CLI reports fast mode on while the API refuses every fast request. The 2.1.278 bundle carries the
        exemption for the server's answer, and its changelog files that fix under 2.1.271."""
        env = {"CLAUDE_CODE_OAUTH_TOKEN": _token()}
        for version, want in (("2.1.271", SKIP), ("2.1.282", SKIP), ("2.2.0", SKIP), ("3.0.1", SKIP),
                              ("2.1.270", {}), ("2.1.266", {}), ("1.9.999", {})):
            with self.subTest(version=version):
                self.lines.clear()
                cli = _claude(tempfile.mkdtemp(), version)
                self.assertEqual(sb.token_fast_org_env(env, cli, self.log, set()), want)
                if want:
                    self.assertEqual(self.lines, [], "nothing to say at or above the floor")
                else:
                    self.assertEqual(len(self.lines), 1, self.lines)
                    msg, problem = self.lines[0]
                    self.assertIn("Claude Code %s is older than 2.1.271" % version, msg)
                    self.assertFalse(problem, "an old CLI is a fact to state, not a fault")
        self.assertEqual(sb.token_fast_org_env({}, _claude(self.d, "2.1.266"), self.log, set()), {})

    def test_an_unknown_version_keeps_the_clis_own_check_and_is_said_once_as_a_problem(self):
        env = {"CLAUDE_CODE_OAUTH_TOKEN": _token()}
        said = set()
        silent = _claude(self.d, None)
        self.assertEqual(sb.claude_cli_version(silent)[0], None)
        for _ in range(2):
            self.assertEqual(sb.token_fast_org_env(env, silent, self.log, said), {})
        self.assertEqual(len(self.lines), 1, "said once per line, however many launches")
        self.assertIn("printed no version", self.lines[0][0])
        self.assertIn("romp gives the org-check skip only to 2.1.271 or later", self.lines[0][0],
                      "the floor is romp's choice, not a limit of the CLI")
        self.assertTrue(self.lines[0][1], "no version to read is a fault the log ring shows")
        self.assertEqual(sb.token_fast_org_env(env, os.path.join(self.d, "missing"), self.log, said), {})
        self.assertIn("could not be read", self.lines[-1][0])
        self.assertEqual(sb.token_fast_org_env(env, None, self.log, said), {}, "no binary named: no skip")
        self.assertIn("no binary was named", self.lines[-1][0])

    def test_the_version_is_read_once_per_binary_file_and_again_after_an_upgrade(self):
        runs = os.path.join(self.d, "runs")
        count = "echo x >> '%s'\n" % runs
        p = _claude(self.d, "2.1.266", extra=count)
        self.assertEqual(sb.claude_cli_version(p), ((2, 1, 266), ""))
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 266))
        self.assertEqual(len(Path(runs).read_text().split()), 1, "one read per binary file")
        # a binary rewritten at the same path (the same inode): size and modification time each mark a new file alone,
        # here first only the size (the old time put back), then only the time (a rewrite of the same length)
        mtime = os.stat(p).st_mtime_ns
        ino = os.stat(p).st_ino
        _claude(self.d, "2.1.280", extra=count + ": updated\n")
        os.utime(p, ns=(mtime, mtime))
        self.assertEqual((os.stat(p).st_ino, os.stat(p).st_mtime_ns), (ino, mtime), "only the size moved")
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 280), "read again: a new size")
        size = os.path.getsize(p)
        _claude(self.d, "2.1.281", extra=count + ": updated\n")
        os.utime(p, ns=(mtime + 10 ** 9, mtime + 10 ** 9))
        self.assertEqual((os.stat(p).st_ino, os.path.getsize(p)), (ino, size), "only the time moved")
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 281), "read again: a new modification time")
        # the native installer repoints a symlink at the new version's file: the same path, the same size and time
        link = os.path.join(self.d, "claude-link")
        old, new = _claude(self.d, "2.1.266", name="v266"), _claude(self.d, "2.1.282", name="v282")
        for f in (old, new):
            os.utime(f, ns=(mtime, mtime))
        self.assertEqual(os.path.getsize(old), os.path.getsize(new))
        os.symlink(old, link)
        self.assertEqual(sb.claude_cli_version(link)[0], (2, 1, 266))
        os.unlink(link)
        os.symlink(new, link)
        self.assertEqual(sb.claude_cli_version(link)[0], (2, 1, 282), "the file the link names now, not the path")

    def test_a_new_file_put_over_the_path_with_the_same_size_and_time_is_read_again(self):
        """The inode alone marks a new file: an installer that renames a new file over the old one's path may keep the
        archive's modification time, and a new version of the same length then differs from the old file in nothing
        else."""
        p = _claude(self.d, "2.1.266")
        st = os.stat(p)
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 266))
        q = _claude(self.d, "2.1.282", name="claude.new")
        os.utime(q, ns=(st.st_mtime_ns, st.st_mtime_ns))
        os.replace(q, p)
        now = os.stat(p)
        self.assertEqual((now.st_size, now.st_mtime_ns), (st.st_size, st.st_mtime_ns), "the premise: the same size and time")
        self.assertNotEqual(now.st_ino, st.st_ino, "the premise: a new file at the path")
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 282), "read again: a new inode")

    def test_two_names_for_one_binary_file_read_it_once(self):
        """Once per binary FILE, not per name: a symlink and the file it names are one read."""
        runs = os.path.join(self.d, "runs")
        f = _claude(self.d, "2.1.282", name="claude-real", extra="echo x >> '%s'\n" % runs)
        link = os.path.join(self.d, "claude")
        os.symlink(f, link)
        self.assertEqual(sb.claude_cli_version(f)[0], (2, 1, 282))
        self.assertEqual(sb.claude_cli_version(link)[0], (2, 1, 282))
        self.assertEqual(len(Path(runs).read_text().split()), 1, "one read for the two names")

    def test_two_files_at_different_paths_with_one_inode_size_and_time_are_read_apart(self):
        """The real path is part of the key: two binaries at different paths, os.stat stubbed so that both report one
        inode, size and modification time, are each read for their own version."""
        a = _claude(tempfile.mkdtemp(), "2.1.266")
        b = _claude(tempfile.mkdtemp(), "2.1.282")
        real_stat = os.stat
        shared = real_stat(a)
        both = {os.path.realpath(a), os.path.realpath(b)}

        def stat(p, *args, **kw):
            return shared if isinstance(p, str) and p in both else real_stat(p, *args, **kw)
        with unittest.mock.patch.object(sb.os, "stat", stat):
            self.assertEqual(sb.os.stat(b).st_ino, real_stat(a).st_ino, "the premise: the stub answers for both paths")
            self.assertEqual(sb.claude_cli_version(a)[0], (2, 1, 266))
            self.assertEqual(sb.claude_cli_version(b)[0], (2, 1, 282))

    def test_the_version_read_gives_the_binary_no_input_to_wait_for(self):
        """`-v` runs with its stdin on /dev/null: a binary that reads its input to the end before answering gets end of
        file at once, though this process's own stdin is a pipe nothing writes to."""
        p = _claude(self.d, "2.1.282", extra="cat > /dev/null\n")
        r, w = os.pipe()
        saved = os.dup(0)
        os.dup2(r, 0)

        def restore():
            os.dup2(saved, 0)
            for fd in (saved, r, w):
                os.close(fd)
        self.addCleanup(restore)
        self.addCleanup(setattr, sb, "CLI_VERSION_TIMEOUT_S", sb.CLI_VERSION_TIMEOUT_S)
        sb.CLI_VERSION_TIMEOUT_S = 2.0
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 282))

    def test_the_version_is_the_x_y_z_the_output_opens_with_as_the_sdk_reads_it(self):
        """The SDK's minimum-version check strips the output and matches x.y.z at its start: leading whitespace is read,
        and a version after other text is no version (the CLI's own check stands, and the log says why)."""
        for out, want in (("  2.1.282 (Claude Code)", (2, 1, 282)), ("\t2.1.271", (2, 1, 271)),
                          ("Claude Code 2.1.282", None), ("v2.1.282", None)):
            with self.subTest(out=out):
                p = _claude(tempfile.mkdtemp(), None, extra="printf '%%s\\n' '%s'\n" % out)
                version, why = sb.claude_cli_version(p)
                self.assertEqual(version, want)
                if want is None:
                    self.assertIn("printed no version", why)

    def test_the_version_is_read_from_stdout_alone_as_the_sdk_reads_it(self):
        """The SDK's minimum-version check reads the CLI's stdout only. What the CLI writes to stderr first is not its
        version, a version-shaped warning included (read as the version, it would give an old CLI the skip), and a
        version printed on stderr alone is no version."""
        for err, out, want in (("9.9.9 synthetic warning on stderr", "2.1.266 (Claude Code)", (2, 1, 266)),
                               ("synthetic warning 9.9.9 on stderr", "2.1.282 (Claude Code)", (2, 1, 282)),
                               ("2.1.282 (Claude Code)", None, None)):
            with self.subTest(err=err, out=out):
                extra = "echo '%s' >&2\n" % err + ("echo '%s'\n" % out if out else "")
                p = _claude(tempfile.mkdtemp(), None, extra=extra)
                version, why = sb.claude_cli_version(p)
                self.assertEqual(version, want)
                if want is None:
                    self.assertIn("printed no version", why)
                elif want < sb.FAST_ORG_SKIP_MIN_CLI:
                    self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}, p), {},
                                     "the old CLI gets no skip, whatever its stderr said first")

    def test_a_log_that_raises_leaves_the_clis_own_check_standing(self):
        """The line is best effort: a log callable that raises (a broken log wire) must not fail the launch it was
        about. Below the floor the CLI's own check stands, said or not."""
        calls = []

        def log(msg, problem=False):
            calls.append(msg)
            raise OSError("log wire closed")
        cli = _claude(self.d, "2.1.266")
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}, cli, log, set()), {})
        self.assertEqual(len(calls), 1, "the premise: the line was said, and the log raised")

    def test_a_bare_name_is_the_binary_path_resolves_it_to(self):
        """ROMP_CLAUDE_BIN may name the CLI bare; the launch's spawn finds it on PATH, and so does the version read."""
        name = "claude-standin-%s" % os.path.basename(self.d)
        _claude(self.d, "2.1.282", name=name)
        with unittest.mock.patch.dict(os.environ, {"PATH": self.d + os.pathsep + os.environ.get("PATH", "")}):
            self.assertEqual(sb.claude_cli_version(name)[0], (2, 1, 282))
            self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}, name), SKIP)

    def test_a_binary_that_answered_without_a_version_is_not_asked_again(self):
        """What the binary answered is kept, a version or an output that carries none, until the file changes."""
        runs = os.path.join(self.d, "runs")
        p = _claude(self.d, None, extra="echo x >> '%s'\n" % runs)
        for _ in range(2):
            version, why = sb.claude_cli_version(p)
            self.assertIsNone(version)
            self.assertIn("printed no version", why)
        self.assertEqual(len(Path(runs).read_text().split()), 1, "one run: the answer is kept")

    def test_a_read_that_could_not_run_is_asked_again(self):
        """A binary the read could not start (here not executable, as a refused fork would be) is not kept: the file is the
        same, its mode aside, and the next read runs it again."""
        p = _claude(self.d, "2.1.282")
        os.chmod(p, 0o644)
        version, why = sb.claude_cli_version(p)
        self.assertIsNone(version)
        self.assertIn("could not run", why)
        os.chmod(p, 0o755)
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 282), "not kept: the next read asks again")

    def test_a_read_that_timed_out_is_asked_again(self):
        flag = os.path.join(self.d, "busy")
        Path(flag).write_text("")
        p = _claude(self.d, "2.1.282", extra="[ -e '%s' ] && exec sleep 30\n" % flag)
        saved = sb.CLI_VERSION_TIMEOUT_S
        sb.CLI_VERSION_TIMEOUT_S = 0.5
        try:
            version, why = sb.claude_cli_version(p)
        finally:
            sb.CLI_VERSION_TIMEOUT_S = saved
        self.assertIsNone(version)
        self.assertIn("did not answer", why)
        os.unlink(flag)
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 282), "not kept: the next read asks again")

    def test_a_read_a_signal_ended_is_asked_again(self):
        """A `-v` ended by a signal (an out-of-memory kill, a cgroup's) gave no answer of the binary's own, as a timeout
        gives none: not kept, the file unchanged, and the next read gets the version, and the skip with it."""
        flag = os.path.join(self.d, "killed")
        Path(flag).write_text("")
        p = _claude(self.d, "2.1.282", extra="[ -e '%s' ] && kill -9 $$\n" % flag)
        st = os.stat(p)
        version, why = sb.claude_cli_version(p)
        self.assertIsNone(version)
        os.unlink(flag)
        self.assertEqual((os.stat(p).st_ino, os.stat(p).st_size, os.stat(p).st_mtime_ns),
                         (st.st_ino, st.st_size, st.st_mtime_ns), "the premise: the same file")
        self.assertEqual(sb.claude_cli_version(p)[0], (2, 1, 282), "not kept: the next read asks again")
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}, p), SKIP)
        self.assertIn("ended by signal 9", why, "the line names the kill, not an answer without a version")


class _LaunchHarness(unittest.TestCase):
    """_options on a box with no apiKeyHelper and a signed-in machine login, its settings in a scratch config dir."""

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
        self.be.login_ok = lambda: True

    def tearDown(self):
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

    def _launch(self, s):
        kw = s.backend._options(s, dict)
        p = kw.get("settings")
        return kw["env"], (json.loads(Path(p).read_text()) if p else {})

    def _stored_login(self, be):
        tok = _token()
        rec = {"id": sb._logins.mint_id(), "label": "Work", "tokenCmd": "printf '%%s' '%s'" % tok,
               "addedAt": int(time.time()) - 86400}
        sb._logins.write_record(be.state_dir, rec)
        return rec, tok


class Launches(_LaunchHarness):
    def test_a_stored_login_launch_carries_the_skip_beside_its_token_and_the_opt_in(self):
        tok = _token()
        rec = {"id": sb._logins.mint_id(), "label": "Work", "tokenCmd": "printf '%%s' '%s'" % tok,
               "addedAt": int(time.time()) - 86400}
        sb._logins.write_record(self.be.state_dir, rec)
        s = _sess(self.be, auth="login", authLogin=rec["id"], fast=True)
        env, settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), tok, "the stored login rides the environment")
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1",
                         "the CLI cannot ask the org about this login: the skip, so the API's own answer decides")
        self.assertTrue(settings.get("fastMode"), "the launch-time opt-in rides beside it")
        self.assertTrue(s._fast_token_login)

    def test_a_machine_login_in_the_environment_carries_it_and_one_the_cli_holds_does_not(self):
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": _token()}
        s = _sess(self.be, auth="login", fast=True)
        env, _ = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1")
        self.assertTrue(s._fast_token_login)
        sb._STARTUP_AUTH_ENV = {}
        s = _sess(self.be, auth="login", fast=True)
        env, _ = self._launch(s)
        self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"),
                          "a login in the CLI's own credential store: the CLI asks the org itself")
        self.assertFalse(s._fast_token_login)
        self.assertIs(s._launch_record.get("fastGuess"), False, "no token: the CLI asks the org, nothing to guess")

    def test_the_launch_record_says_the_skip_rode_with_the_token_and_holds_no_token(self):
        rec, tok = self._stored_login(self.be)
        s = _sess(self.be, auth="login", authLogin=rec["id"], fast=True)
        env, _settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), tok, "the premise: the stored login's token rides the launch")
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "the premise: the skip rides beside it")
        self.assertIs(s._launch_record.get("fastGuess"), False, "the token with the skip: the CLI was not left to guess")
        self.assertNotIn(tok, json.dumps(s._launch_record), "a boolean rides the spawn spec, never the token")

    def test_a_launch_below_the_floor_carries_the_token_without_the_skip_and_records_that_it_guesses(self):
        be = _backend(version="2.1.266")
        be.login_ok = lambda: True
        rec, tok = self._stored_login(be)
        s = _sess(be, auth="login", authLogin=rec["id"], fast=True)
        env, settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), tok)
        self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "below 2.1.271 the skip would hide the API's refusal")
        self.assertTrue(settings.get("fastMode"), "the opt-in still rides: the CLI's own check answers it")
        self.assertEqual(s._fast_token_case, "floor", "a relaunch from these options would not settle a guess either")
        self.assertIs(s._launch_record.get("fastGuess"), True, "the CLI is left to its own guess, and the record says so")
        self._launch(s)
        said = [m for m in be._logs if "Claude Code 2.1.266 is older than 2.1.271" in m]
        self.assertEqual(len(said), 1, "said once per backend, however many launches: %r" % be._logs)

    def test_a_machine_login_below_the_floor_is_said_once_however_many_launches(self):
        be = _backend(version="2.1.266")
        be.login_ok = lambda: True
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": _token()}
        s = _sess(be, auth="login", fast=True)
        for _ in range(2):
            env, _settings = self._launch(s)
            self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "below 2.1.271 the skip would hide the refusal")
        self.assertIs(s._launch_record.get("fastGuess"), True)
        said = [m for m in be._logs if "Claude Code 2.1.266 is older than 2.1.271" in m]
        self.assertEqual(len(said), 1, "the machine login's launches share the backend's once: %r" % be._logs)

    def test_a_version_read_a_signal_ended_is_read_again_by_the_next_launch_that_asks_for_fast_mode(self):
        """The first launch's `-v` is killed (as an out-of-memory or cgroup kill would end it): that launch goes without the
        skip and says why, and the next launch that asks for fast mode, the binary unchanged and answering, reads 2.1.282
        and carries the skip."""
        flag = os.path.join(self.cfg, "kill-the-read")
        Path(flag).write_text("")
        self.be.claude_bin = _claude(tempfile.mkdtemp(), "2.1.282", extra="[ -e '%s' ] && kill -9 $$\n" % flag)
        rec, tok = self._stored_login(self.be)
        s = _sess(self.be, auth="login", authLogin=rec["id"], fast=True)
        env, _settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), tok)
        self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "the premise: the killed read gave no version")
        os.unlink(flag)
        env, _settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "read again: the kill was no answer")
        self.assertTrue(s._fast_token_login)
        self.assertIs(s._launch_record.get("fastGuess"), False)
        self.assertTrue(any("ended by signal 9" in m for m in self.be._logs), self.be._logs)

    def test_a_token_launch_without_the_fast_flag_reads_no_version_and_logs_nothing(self):
        """Fast mode off for the session (no pick of its own, Always fast off): the launch carries no flag, so it runs no
        `-v`, carries no skip, writes no line about the version and files no problem row. A stored login on a claude
        below the floor, and a machine login on one that prints no version; the backend's own log and problem ring."""
        for road, version in (("stored", "2.1.266"), ("machine", None)):
            with self.subTest(road=road):
                d = tempfile.mkdtemp()
                runs = os.path.join(d, "runs")
                root = tempfile.mkdtemp()
                Path(root, "session-hosts").write_text("off")   # outside the runner's belt (CLAUDE.md, 2026-09-11)
                lines = []
                be = sb.SdkBackend(root, _claude(d, version, extra="echo x >> '%s'\n" % runs), lines.append)
                be.login_ok = lambda: True
                if road == "stored":
                    rec, tok = self._stored_login(be)
                    s = _sess(be, auth="login", authLogin=rec["id"])
                else:
                    tok = _token()
                    sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": tok}
                    s = _sess(be, auth="login")
                env, settings = self._launch(s)
                self.assertEqual(env.get("CLAUDE_CODE_OAUTH_TOKEN"), tok, "the premise: the login rides the environment")
                self.assertFalse(settings.get("fastMode"), "the premise: the launch carries no fast flag")
                self.assertFalse(os.path.exists(runs), "no -v ran")
                self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"))
                self.assertEqual([m for m in lines if m.startswith("fast mode:")], [])
                self.assertEqual(be.problems(), [])
                self.assertEqual(s._fast_token_case, "unread")

    def test_the_flag_file_and_the_launch_record_take_the_same_answer_of_the_fast_rule(self):
        """fast_effective answers True and then False, a stand-in for fast mode turned off between two reads: the
        launch's flag file and its launch record both carry the first answer."""
        rec, _tok = self._stored_login(self.be)
        s = _sess(self.be, auth="login", authLogin=rec["id"])
        answers = iter([True] + [False] * 20)
        s.fast_effective = lambda: next(answers)
        _env, settings = self._launch(s)
        self.assertTrue(settings.get("fastMode"), "the flag file")
        self.assertIs(s._launch_record.get("fast"), True, "the launch record")


class AnAttachAnswersForItsOwnLaunch(_LaunchHarness):
    """A kernel restart under a session host: one kernel LAUNCHES the CLI (its options, the launch record the real host
    road writes into the spawn spec, the host's hello echoing it through the real _on_host_hello), the next one ATTACHES
    to it (its own options, the transport's attach flag, the same record in the hello, through the real _on_host_hello as
    before), and whether THAT launch carried the skip is read from its record, never from the new kernel's options (review
    2026-10-01: keyed on today's options, a refusal from a CLI launched WITH the skip was blanked as a pre-fix guess, the
    pick kept, no toast, a relaunch asked and a log line saying the CLI was launched without the check). The host's own
    echo of the record, fastGuess included, is tests/test_session_host.py's."""

    @staticmethod
    def _below_floor(version):
        return tuple(int(x) for x in version.split(".")) < sb.FAST_ORG_SKIP_MIN_CLI

    def _spawned_record(self, be, s):
        """The launch record the host road writes into the spawn spec (the real _host_transport_for, with the host's own
        start stubbed out, so no host runs), read back from hosts/<sid>/spawn.json, the file the host echoes it from. The
        options are an empty stand-in: the record does not come from them, and the token they would carry stays off
        disk."""
        hosts = Path(be.state_dir, "session-hosts")
        hosts.write_text("on")

        class Spawned(Exception):
            pass

        def spawn(sess, spec_path):
            raise Spawned(spec_path)
        be._spawn_host = spawn
        try:
            with self.assertRaises(Spawned) as cm:
                asyncio.run(be._host_transport_for(s, types.SimpleNamespace(), ()))
        finally:
            hosts.write_text("off")
        return json.loads(Path(cm.exception.args[0]).read_text())["launch"]

    def _hello(self, be, s, launch, attach):
        s._host_is_attach = attach
        s._host = types.SimpleNamespace(exit_info=None, ack_offset=0, end_grace=120.0, hello=None, _init_pending=False,
                                        detach_mode=False)
        be._on_host_hello(s, {"host": {"pid": 9000, "start": "1.0", "version": be.code_version},
                              "cli": dict(CLI, fsid=FSID, login="", launch=launch),
                              "journal": {"next": 0}, "parked": [], "inflight": 0})

    def _resume(self, be):
        """The session object a kernel builds from the stored reg (a restart keeps the reg, not the object)."""
        s = sb.SdkSession(be, dict(sb.read_reg(be.state_dir, SID)))
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        be.sessions[SID] = s
        s.asks = []
        s.want_switch_reconnect = lambda why: s.asks.append(("quiet", why))
        s.request_reconnect = lambda *a, **k: s.asks.append(("now", k.get("why")))
        return s

    def _launched(self, version="2.1.282", always=False, initialize=ON, extra="", **reg):
        """Kernel 1: a stored-login session LAUNCHED under a host on a claude at `version` (`extra` as _claude's; the
        machine's Always fast switch on when `always`), whose initialize says `initialize`. ON by default: a CLI under the
        skip says it before any fast request (2.1.282), and a CLI left to guess says it when the flag cached in its config
        directory reads true (sdk_backend.token_fast_org_env). GUESS is what a token login without the skip reported when
        that flag read anything else. Returns the state root and the launch record the host keeps in its spawn spec; the
        session is left in self.launched_session."""
        be = _backend(version=version, extra=extra)
        be.login_ok = lambda: True
        if always:
            Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        rec, _tok = self._stored_login(be)
        _sess(be, auth="login", authLogin=rec["id"], **reg)
        s = self._resume(be)
        env, settings = self._launch(s)
        guesses = self._below_floor(version) or not settings.get("fastMode")
        if not extra:
            self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), None if guesses else "1",
                             "the premise: a token launch carries the skip exactly when it has the flag on 2.1.271 or later")
        launch = self._spawned_record(be, s)
        if not extra:
            self.assertIs(launch.get("fastGuess"), guesses, "the record the host keeps says whether the CLI was left to guess")
        self._hello(be, s, launch, attach=False)
        s._fast_unlocked = s.fast_effective()
        s._fast_guess_asked = False
        s._adopt_fast_state(initialize)
        self.launched_session = s
        return be.state_dir, launch

    def _attached(self, root, launch, version="2.1.282", extra=""):
        """Kernel 2 over the same root: _options runs (it does for an attach), then the attach and its hello; the snapshot
        takes the launch's own fast flag, as the connect does."""
        be = _backend(root, version=version, extra=extra)
        be.login_ok = lambda: True
        s = self._resume(be)
        env, settings = self._launch(s)
        if not extra and version and settings.get("fastMode") and env.get("CLAUDE_CODE_OAUTH_TOKEN"):
            self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), None if self._below_floor(version) else "1",
                             "the premise: a token launch with the flag carries the skip exactly at 2.1.271 or later")
        self._hello(be, s, launch, attach=True)
        s._fast_unlocked = bool(launch.get("fast")) if isinstance(launch, dict) else s.fast_effective()
        s._fast_guess_asked = False
        return be, s

    def _busy(self):
        """A flag file and the stand-in shell (_claude's `extra`) that makes `-v` hang while the file exists, the read's
        timeout cut to 0.5 s for this test; delete the file and the binary answers."""
        flag = os.path.join(tempfile.mkdtemp(), "busy")
        Path(flag).write_text("")
        saved = sb.CLI_VERSION_TIMEOUT_S
        sb.CLI_VERSION_TIMEOUT_S = 0.5
        self.addCleanup(setattr, sb, "CLI_VERSION_TIMEOUT_S", saved)
        return flag, "[ -e '%s' ] && exec sleep 30\n" % flag

    def _guess_lines(self, be):
        """The line for a "preference" answered with a relaunch, in its three spellings: "its own guess" when the launch
        record says the CLI was left to guess, "which may be its own guess" when the hello carries no record, and no
        guess named when the record says the launch had no fast flag."""
        return [m for m in be._logs if "for a login it cannot ask the organization about" in m]

    def _refusal_text(self, be, always):
        """The one refusal text: the Always fast line when `always` (no pick of the session's own), else the warn toast."""
        if always:
            texts = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        else:
            texts = [t["text"] for t in be._toasts if t.get("type") == "warn"]
        self.assertEqual(len(texts), 1, (be._logs, be._toasts))
        return texts[0]

    def test_a_refusal_from_a_cli_launched_with_the_skip_is_loud_at_the_first_restart(self):
        root, launch = self._launched(fast=True)
        be, s = self._attached(root, launch)
        self.assertEqual(s._hello_launch, launch, "the hello's launch record is the one the connect reads")
        s._adopt_fast_state(ON)                       # the attach's own initialize
        s._adopt_fast_state(GUESS)                    # the next fast request, refused by the API: the server's "preference"
        reg = sb.read_reg(root, SID)
        self.assertFalse(s.fast_opt, "the API refused the user's pick: it goes back off")
        self.assertEqual((reg.get("fast"), reg.get("fastRefusalChecked"), reg.get("liveFastReason")), (False, True, "preference"))
        self.assertEqual([t["type"] for t in be._toasts], ["warn"])
        self.assertIn(ORG, be._toasts[0]["text"], "launched with the skip: the organization's words")
        self.assertNotIn("own check", be._toasts[0]["text"])
        self.assertNotIn(("quiet", "fast mode"), s.asks, "no relaunch for a guess: there was none")
        self.assertIn(("now", "fast mode refusal"), s.asks, "the refused pick's own flagless relaunch")
        self.assertEqual(self._guess_lines(be), [], "and no line saying the CLI was launched without the check")

    def test_under_always_fast_the_switch_is_held_and_said_once(self):
        root, launch = self._launched(always=True)
        self.assertIs(launch.get("fast"), True, "the switch armed the flag at that launch")
        be, s = self._attached(root, launch)
        self.assertTrue(s._fast_unlocked)
        s._adopt_fast_state(ON)
        s._adopt_fast_state(GUESS)
        s._adopt_fast_state(GUESS)
        reg = sb.read_reg(root, SID)
        self.assertEqual((reg.get("fastRuleRefused"), reg.get("fastRefusalChecked")), ("preference", True))
        self.assertFalse(s.fast_effective(), "the switch is held: the next launch carries no flag")
        refused = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        self.assertEqual(len(refused), 1, be._logs)
        self.assertEqual((s.asks, self._guess_lines(be)), ([], []))

    def test_a_launch_record_written_before_the_field_is_a_launch_that_carried_the_skip(self):
        """A kernel between the launch record (2026-10-01) and this field wrote records without fastGuess; it gave every
        launch whose login rides the environment the skip, so a "preference" from such a CLI is the API's answer."""
        root, launch = self._launched(fast=True)
        older = {k: v for k, v in launch.items() if k != "fastGuess"}
        be, s = self._attached(root, older)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt)
        self.assertEqual(len(be._toasts), 1)
        self.assertIn(ORG, be._toasts[0]["text"], "a launch that carried the skip: the organization's words")
        self.assertNotIn("own check", be._toasts[0]["text"])
        self.assertTrue(sb.read_reg(root, SID).get("fastRefusalChecked"))
        self.assertNotIn(("quiet", "fast mode"), s.asks)

    def test_a_hello_with_no_launch_record_may_still_be_guessing_and_says_only_that(self):
        root, _launch = self._launched(fast=True)
        be, s = self._attached(root, None)
        s._adopt_fast_state(GUESS)
        self.assertTrue(s.fast_opt, "a host spawned before the record: maybe a CLI launched before the skip, still guessing")
        self.assertEqual(be._toasts, [])
        self.assertEqual(s.asks, [("quiet", "fast mode")])
        self.assertFalse(sb.read_reg(root, SID).get("fastRefusalChecked"))
        lines = self._guess_lines(be)
        self.assertEqual(len(lines), 1, be._logs)
        self.assertIn("keeps no record of whether its launch carried the check", lines[0],
                      "it cannot say the CLI was launched without the check: it does not know")
        self.assertIn("'preference', which may be its own guess", lines[0], "nor that the 'preference' is a guess")
        self.assertNotIn("'preference', its own guess", lines[0],
                         "a CLI launched with the skip under a host that echoes no record gave the API's real answer")

    def test_a_cli_left_to_guess_below_the_floor_relaunches_after_an_update_of_claude_code(self):
        """A guard of the relaunch for a guess, for a CLI launched below the floor. The old CLI said on at its initialize
        (its cached flag read true), so no refusal is on record when the kernel restarts; its first "preference" after
        Claude Code is updated is answered with a relaunch that carries the skip."""
        root, launch = self._launched(version="2.1.266", fast=True)
        be, s = self._attached(root, launch, version="2.1.282")     # Claude Code updated across the restart
        s._adopt_fast_state(GUESS)
        self.assertTrue(s.fast_opt, "the old CLI's guess: the relaunch on the new one gets the API's answer")
        self.assertEqual(s.asks, [("quiet", "fast mode")])
        self.assertEqual(be._toasts, [])
        lines = self._guess_lines(be)
        self.assertTrue(lines and "this CLI was launched without the check that settles it" in lines[0], be._logs)
        self.assertIn("'preference', its own guess", lines[0], "the record says the CLI was left to guess: the line says so")
        self.assertIs(launch.get("fastGuess"), True, "what the host kept: the launch had the token and no skip")
        self.assertEqual(s._fast_token_case, "skip", "and today's options carry the skip")

    def test_a_cli_left_to_guess_below_the_floor_is_answered_as_launched_while_the_floor_still_holds(self):
        root, launch = self._launched(version="2.1.266", fast=True)
        be, s = self._attached(root, launch, version="2.1.266")
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt, "the CLI's own check stands below the floor, as at a launch: loud, the pick off")
        self.assertEqual(len(be._toasts), 1)
        self.assertIn(OWN + " (Claude Code 2.1.271 or later lets the API decide)", be._toasts[0]["text"])
        self.assertTrue(sb.read_reg(root, SID).get("fastRefusalChecked"))
        self.assertNotIn(("quiet", "fast mode"), s.asks, "a relaunch would only guess again")
        self.assertEqual(s._fast_token_case, "floor", "a launch now would carry no skip either")

    def test_a_guess_met_while_todays_version_read_times_out_relaunches_and_that_launch_carries_the_skip(self):
        """The CLI was launched left to guess (below the floor; its initialize said on). After the restart the attach's
        own version read times out, and the CLI reports "preference": read as the guess, so the pick stays on and one
        quiet relaunch is asked. That relaunch, the read now answering 2.1.282, carries the skip and the flag."""
        root, launch = self._launched(version="2.1.266", fast=True)
        flag, busy = self._busy()
        be, s = self._attached(root, launch, version="2.1.282", extra=busy)
        s._adopt_fast_state(GUESS)
        s._adopt_fast_state(GUESS)
        self.assertTrue(s.fast_opt, "the pick stays on")
        self.assertEqual(be._toasts, [])
        self.assertFalse(sb.read_reg(root, SID).get("fastRefusalChecked"))
        self.assertEqual(s.asks, [("quiet", "fast mode")], "one quiet relaunch")
        os.unlink(flag)
        env, settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "the relaunch reads 2.1.282: the skip")
        self.assertTrue(settings.get("fastMode"), "and the flag")

    def test_a_guess_met_while_todays_binary_answers_without_a_version_is_read_as_one(self):
        """As the timed-out read: a binary that answers `-v` without a version leaves the attach's version unknown, so the
        guess gets the quiet relaunch and is not answered as a refusal."""
        root, launch = self._launched(version="2.1.266", fast=True)
        be, s = self._attached(root, launch, version=None)
        s._adopt_fast_state(GUESS)
        self.assertTrue(s.fast_opt, "the pick stays on")
        self.assertEqual(be._toasts, [])
        self.assertFalse(sb.read_reg(root, SID).get("fastRefusalChecked"))
        self.assertEqual(s.asks, [("quiet", "fast mode")])
        self.assertEqual(s._fast_token_case, "retry")

    def test_below_the_floor_a_refusal_at_the_launch_outlasts_an_update_of_claude_code(self):
        """What a token login without the skip reported at its initialize: off, "preference". Below the floor that is the
        CLI's own check, which romp keeps there, so the launching kernel answers it as a refusal (the pick back off with one
        warn toast, or Always fast held) and records it as checked. The record outlasts an update of Claude Code: the
        attach on 2.1.282 asks no relaunch, and the session's next launch carries no fast-mode flag. A refused own pick
        stays off until fast mode is turned on for the session again, and Always fast stays held back until fast mode is
        turned on for the session again, until Always fast is turned off or on, or until the session launches on another
        billing (docs/reference.md, "Fast mode under a login that rides the
        environment"). The pick and Always fast are each tried alone here; a refused own pick with Always fast also on
        is test_a_refused_own_pick_under_always_fast_holds_always_fast_back_from_its_flagless_relaunch_until_the_next_pick's."""
        for always in (False, True):
            with self.subTest(always=always):
                root, launch = self._launched(version="2.1.266", always=always, initialize=GUESS, fast=not always)
                reg = sb.read_reg(root, SID)
                self.assertEqual((bool(reg.get("fast")), reg.get("fastRefusalChecked")), (False, True))
                self.assertEqual(reg.get("fastRuleRefused"), "preference" if always else None)
                be, s = self._attached(root, launch, version="2.1.282")     # Claude Code updated across the restart
                s._adopt_fast_state(GUESS)                                  # the old CLI, still running, says the same
                self.assertEqual((s.asks, self._guess_lines(be), be._toasts), ([], [], []),
                                 "the refusal already stands: no relaunch, no line, no toast at the attach")
                self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "and its record stays")
                self.assertFalse(s.fast_effective())
                env, settings = self._launch(s)
                self.assertFalse(settings.get("fastMode"), "no fast mode: the refusal recorded below the floor holds")
                self.assertIsNone(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "and a launch without the flag no skip")

    def test_under_always_fast_a_refusal_at_a_launch_whose_read_timed_out_holds_this_kernel_and_is_not_recorded_as_checked(self):
        """A launch whose `-v` timed out carries no skip, and the CLI's own check answers the flag Always fast armed (no
        pick of the session's own) with "preference". This kernel arms no flag again, the read timing out or answering,
        and the registry gets no checked mark."""
        flag, busy = self._busy()
        root, launch = self._launched(always=True, initialize=GUESS, extra=busy)
        s = self.launched_session
        self.assertEqual((launch.get("fast"), launch.get("fastGuess")), (True, True),
                         "the premise: the switch armed the flag, and the launch carried no skip")
        reg = sb.read_reg(root, SID)
        self.assertEqual(reg.get("fastRuleRefused"), "preference", "the switch is charged")
        self.assertIsNone(reg.get("fastRefusalChecked"), "no checked mark in the registry")
        flags = []
        for answering in (False, True):
            if answering:
                os.unlink(flag)
            for _ in range(2):
                _env, settings = self._launch(s)
                flags.append(bool(settings.get("fastMode")))
                _relaunched(s)
                if not s._fast_unlocked:
                    s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
        self.assertEqual(flags, [False] * 4, "no second flagged launch in this kernel's life")
        refused = [m for m in s.backend._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        self.assertEqual(len(refused), 1, s.backend._logs)

    def test_after_a_refusal_at_a_launch_whose_read_timed_out_the_next_kernel_relaunches_with_the_skip(self):
        """The state the last test leaves, then a restart onto a kernel whose read answers 2.1.282: the attach meets the
        same "preference" from the CLI launched without the skip and asks one quiet relaunch, which carries the skip and
        the flag. With the session's own pick on as well, refused at that launch and so back off, the pick's flagless
        relaunch holds Always fast back in that kernel, with no checked mark in the registry; the next kernel's launch
        carries the skip and the flag Always fast arms, and an organization with fast mode off refuses it: one Always
        fast line, the refusal recorded as checked, and no fast flag at the launch after it."""
        for pick in (False, True):
            with self.subTest(pick=pick):
                if pick:
                    self._own_pick_refused_at_a_launch_whose_read_timed_out_then_the_next_kernel()
                    continue
                _flag, busy = self._busy()
                root, launch = self._launched(always=True, initialize=GUESS, extra=busy)
                be, s = self._attached(root, launch, version="2.1.282")
                s._adopt_fast_state(GUESS)
                s._adopt_fast_state(GUESS)
                self.assertEqual(s.asks, [("quiet", "fast mode")], "one quiet relaunch")
                self.assertEqual(be._toasts, [])
                env, settings = self._launch(s)
                self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1")
                self.assertTrue(settings.get("fastMode"))

    def _own_pick_refused_at_a_launch_whose_read_timed_out_then_the_next_kernel(self):
        _flag, busy = self._busy()
        root, _launch = self._launched(always=True, initialize=GUESS, extra=busy, fast=True)
        reg = sb.read_reg(root, SID)
        self.assertEqual((reg.get("fast"), reg.get("fastRefusalChecked"), reg.get("fastRuleRefused")),
                         (False, None, None), "the premise: the pick back off, no checked mark, the refusal not charged "
                         "to Always fast")
        s = self.launched_session
        s.asks.clear()
        _env, settings = self._launch(s)
        self.assertFalse(settings.get("fastMode"), "the premise: the refused pick's relaunch is flagless")
        _relaunched(s)                                      # that relaunch, in the same kernel and on the same object
        opt_in = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        s._adopt_fast_state(opt_in)                         # its initialize
        s._adopt_fast_state(opt_in)                         # its first turn's init
        self.assertFalse(s.fast_effective(), "Always fast is held back in this kernel")
        reg = sb.read_reg(root, SID)
        self.assertEqual(reg.get("fastRuleRefused"), "preference", "the pick's refusal carried to Always fast")
        self.assertFalse("fastRefusalChecked" in reg, "and no checked mark in the registry")
        self.assertEqual(s.asks, [], "no relaunch asked")
        self.assertEqual([m for m in s.backend._logs if m.startswith("always fast (web): the CLI refused")], [],
                         "no Always fast line")
        be = _backend(root, version="2.1.282")                      # the next kernel, launching
        be.login_ok = lambda: True
        s = self._resume(be)
        env, settings = self._launch(s)
        self.assertEqual((env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), bool(settings.get("fastMode"))), ("1", True),
                         "Always fast arms the next kernel's launch, beside the skip")
        _relaunched(s)
        s._adopt_fast_state(GUESS)                          # the API's refusal: the organization has fast mode off
        refused = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        self.assertEqual(len(refused), 1, be._logs)
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "recorded as checked")
        _env, settings = self._launch(s)
        self.assertFalse(settings.get("fastMode"), "no fast flag at the launch after it")

    def test_an_own_pick_refused_at_a_launch_whose_read_timed_out_goes_off_and_is_not_recorded_as_checked(self):
        """As the last two, with the session's own pick (Always fast on and off): the pick goes back off with one warn
        toast and its flagless relaunch, and the registry gets no checked mark."""
        _flag, busy = self._busy()
        for always in (True, False):
            with self.subTest(always=always):
                root, launch = self._launched(always=always, initialize=GUESS, extra=busy, fast=True)
                s = self.launched_session
                self.assertIs(launch.get("fastGuess"), True, "the premise: the launch carried no skip")
                reg = sb.read_reg(root, SID)
                self.assertFalse(s.fast_opt, "the pick goes back off")
                self.assertIs(reg.get("fast"), False)
                self.assertEqual([t["type"] for t in s.backend._toasts], ["warn"])
                self.assertIn(OWN + " (Claude Code's version could not be read)", s.backend._toasts[0]["text"])
                self.assertIn(("now", "fast mode refusal"), s.asks)
                self.assertIsNone(reg.get("fastRefusalChecked"), "no checked mark in the registry")

    def test_after_an_own_pick_refused_at_a_launch_whose_read_timed_out_the_next_kernel_attaching_to_its_flagless_relaunch_relaunches_once_with_the_flag(self):
        """Under Always fast, the session's own Fast pick is refused at a launch whose `-v` timed out, and the pick's
        flagless relaunch holds Always fast back in that kernel, with no checked mark in the registry. The next kernel
        attaches to that relaunch's CLI, which says it lacks the fast-mode opt-in at its initialize and at its first turn's
        init: one relaunch is asked, it carries the flag, and the refusal at that launch gives one Always fast line. With
        the next kernel's `-v` answering 2.1.282 or 2.1.266, or still not answering."""
        opt_in = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        for version in ("2.1.282", "2.1.266", None):
            with self.subTest(version=version):
                _flag, busy = self._busy()
                root, _launch = self._launched(always=True, initialize=GUESS, extra=busy, fast=True)
                s = self.launched_session
                s.asks.clear()
                _env, settings = self._launch(s)
                self.assertFalse(settings.get("fastMode"), "the premise: the refused pick's relaunch is flagless")
                flagless = self._spawned_record(s.backend, s)
                _relaunched(s)                              # that relaunch, in the same kernel
                self._hello(s.backend, s, flagless, attach=False)
                s._adopt_fast_state(opt_in)                 # its initialize
                s._adopt_fast_state(opt_in)                 # its first turn's init
                self.assertFalse(s.fast_effective(), "the premise: Always fast is held back in that kernel")
                self.assertNotIn("fastRefusalChecked", sb.read_reg(root, SID), "the premise: no checked mark in the registry")
                be, s2 = self._attached(root, flagless, version=version or "2.1.282", extra="" if version else busy)

                def ask(why, s2=s2):                        # as the kernel's own: the ask stands until the relaunch
                    s2.asks.append(("quiet", why))
                    s2._switch_wanted = why
                s2.want_switch_reconnect = ask
                s2._adopt_fast_state(opt_in)                # the attach's initialize
                s2._adopt_fast_state(opt_in)                # its first turn's init
                flags = []
                for _ in range(3):                          # each relaunch asked, in turn
                    if not s2.asks:
                        break
                    s2.asks.clear()
                    s2._switch_wanted = ""
                    _env, settings = self._launch(s2)
                    flags.append(bool(settings.get("fastMode")))
                    _relaunched(s2)
                    for _ in range(2):                      # its initialize and its first turn's init
                        s2._adopt_fast_state(GUESS if settings.get("fastMode") else opt_in)
                self.assertEqual(flags, [True], "one relaunch, with the flag")
                refused = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
                self.assertEqual(len(refused), 1, be._logs)

    def _mark_kernel(self, version, root=None, extra="", always=False):
        be = _backend(root, version=version, extra=extra)
        be.login_ok = lambda: True
        if always:
            Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        return be

    def _mark_session(self, be, login, own=False):
        """The session, billed to a stored login, to the login the CLI holds itself ("cli"), or to a key; `own`: its own
        Fast pick."""
        reg = {"fast": True} if own else {}
        if login == "stored":
            reg.update(auth="login", authLogin=self._stored_login(be)[0]["id"])
        else:
            reg.update(auth="login" if login == "cli" else "key")
        _sess(be, cwd=tempfile.mkdtemp(), **reg)
        return self._resume(be)

    def _mark_connect(self, be, s, launch=None):
        """A connect as _amain makes it: the forget of a refusal given under another billing, the options, the host's
        hello (the spawn's record for a launch, `launch` for an attach), the flag the CLI carries, and the reconcile.
        Returns whether the forget ran, and the record the hello carried."""
        forgot = s._fast_refusal_new_billing()
        self._launch(s)
        if launch is None:
            launch = self._spawned_record(be, s)
            self._hello(be, s, launch, attach=False)
            record, here = s._launch_record, True
        else:
            self._hello(be, s, launch, attach=True)
            record, here = launch, False
        s._fast_guess_asked = False
        s._fast_unlocked = bool((record or {}).get("fast"))
        s._reconcile_waiting_picks(record, here, spawned_at=getattr(s, "_hello_spawned_at", None))
        return forgot, launch

    def _mark_before(self, before):
        """The last kernel: `before` is ("timed out", answer), a launch under Always fast on a stored login whose `-v`
        timed out, refused with `answer`; or (version, login, always, own, answer), a launch whose CLI answered `answer`.
        Returns the state root and that launch's record."""
        if before[0] == "timed out":
            flag, busy = self._busy()
            be = self._mark_kernel("2.1.282", extra=busy, always=True)
            s = self._mark_session(be, "stored")
            _forgot, launch = self._mark_connect(be, s)
            self.assertEqual(s._fast_token_case, "retry", "the premise: that launch's read timed out")
            s._adopt_fast_state(before[1])
            os.unlink(flag)
        else:
            version, login, always, own, answer = before
            be = self._mark_kernel(version, always=always)
            s = self._mark_session(be, login, own)
            _forgot, launch = self._mark_connect(be, s)
            s._adopt_fast_state(answer)
        return be.state_dir, launch

    def test_a_refusal_adopted_as_checked_for_the_first_time_is_recorded_in_the_registry(self):
        """Each case adopts a refusal as checked for the first time, at a launch or an attach, below 2.1.271 or at
        2.1.282: from Claude Code's own check (the launch carried no skip), from the API or the organization, or again
        after a flip of Always fast or a launch on another billing forgot one that was not checked. Each is recorded as
        checked in the registry. A launch whose `-v` timed out is the exception, pinned by
        test_after_a_refusal_at_a_launch_whose_read_timed_out_the_next_kernel_relaunches_with_the_skip."""
        B, A = "2.1.266", "2.1.282"
        usage = {"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}
        lacks = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        late, late_usage = ("timed out", GUESS), ("timed out", usage)
        cases = (   # case, road, today's version, the report, today's token case, the last kernel, the session, the change
            ("billing_at_or_above_attach_after_always_fast_flipped", "attach", A, usage, "skip", late_usage, None, "flip"),
            ("billing_at_or_above_attach_on_another_billing", "attach", A, usage, "skip", late_usage, None, "rebill"),
            ("billing_at_or_above_launch_after_always_fast_flipped", "launch", A, usage, "skip", late_usage, None, "flip"),
            ("billing_at_or_above_launch_on_another_billing", "launch", A, usage, "skip", late_usage, None, "rebill"),
            ("billing_below_attach_after_always_fast_flipped", "attach", B, usage, "floor", late_usage, None, "flip"),
            ("billing_below_attach_on_another_billing", "attach", B, usage, "floor", late_usage, None, "rebill"),
            ("billing_below_launch_after_always_fast_flipped", "launch", B, usage, "floor", late_usage, None, "flip"),
            ("billing_below_launch_on_another_billing", "launch", B, usage, "floor", late_usage, None, "rebill"),
            ("floor_at_or_above_attach_with_todays_options_off_the_token", "attach", A, GUESS, "",
             (A, "stored", False, False, lacks), None, "no token"),
            ("floor_at_or_above_launch_without_the_flag", "launch", A, GUESS, "unread", None, ("stored", False, False), None),
            ("floor_below_attach", "attach", B, GUESS, "floor", (B, "stored", True, False, ON), None, None),
            ("floor_below_attach_after_an_unread_refusal_in_the_last_kernel", "attach", B, GUESS, "floor", late, None, None),
            ("floor_below_launch_after_an_unread_refusal_in_the_last_kernel", "launch", B, GUESS, "floor", late, None, None),
            ("floor_below_launch_own_pick", "launch", B, GUESS, "floor", None, ("stored", False, True), None),
            ("settled_at_or_above_attach_after_an_unread_refusal_in_the_last_kernel", "attach", A, usage, "unread",
             late_usage, None, None),
            ("settled_at_or_above_attach_cli_login", "attach", A, GUESS, "", (A, "cli", False, True, ON), None, None),
            ("settled_at_or_above_attach_guessing_cli_extra_usage", "attach", A, usage, "skip",
             (B, "stored", True, False, ON), None, None),
            ("settled_at_or_above_attach_skip", "attach", A, GUESS, "skip", (A, "stored", True, False, ON), None, None),
            ("settled_at_or_above_launch_after_an_unread_refusal_in_the_last_kernel", "launch", A, GUESS, "skip", late,
             None, None),
            ("settled_at_or_above_launch_cli_login", "launch", A, GUESS, "", None, ("cli", False, True), None),
            ("settled_at_or_above_launch_key", "launch", A, usage, "", None, ("key", False, True), None),
            ("settled_at_or_above_launch_skip_extra_usage", "launch", A, usage, "skip", None, ("stored", False, True), None),
            ("settled_at_or_above_launch_skip_preference", "launch", A, GUESS, "skip", None, ("stored", True, False), None),
            ("settled_below_attach_cli_login", "attach", B, GUESS, "", (B, "cli", True, False, ON), None, None),
            ("settled_below_attach_floor_extra_usage", "attach", B, usage, "floor", (B, "stored", True, False, ON), None,
             None),
            ("settled_below_launch_cli_login", "launch", B, GUESS, "", None, ("cli", True, False), None),
            ("settled_below_launch_floor_extra_usage", "launch", B, usage, "floor", None, ("stored", True, False), None),
        )
        for case, road, version, report, token_case, before, session, change in cases:
            with self.subTest(case=case):
                forgot = False
                if before is None:                          # one kernel: it launches the session
                    login, always, own = session
                    be = self._mark_kernel(version, always=always)
                    s = self._mark_session(be, login, own)
                    self._mark_connect(be, s)
                else:
                    root, launch = self._mark_before(before)
                    be = self._mark_kernel(version, root=root)
                    if change in ("rebill", "no token"):    # another stored login, or the machine's own login
                        r = dict(sb.read_reg(root, SID))
                        r["authLogin"] = self._stored_login(be)[0]["id"] if change == "rebill" else ""
                        sb.write_reg(root, SID, r)
                    s = self._resume(be)
                    if change == "flip":
                        forgot = bool(be._forget_fast_refusals("Always fast flipped"))
                    connect_forgot, _launch = self._mark_connect(be, s, launch if road == "attach" else None)
                    forgot = forgot or connect_forgot
                self.assertEqual((s._host_is_attach, s._fast_token_case), (road == "attach", token_case),
                                 case + ": the premise: the road and today's token case")
                self.assertEqual(forgot, change in ("flip", "rebill"), case + ": the premise: the forget")
                self.assertFalse(s.fast_refusal_checked, case + ": the premise: nothing marked checked yet")
                self.assertIsNot(sb.read_reg(be.state_dir, SID).get("fastRefusalChecked"), True,
                                 case + ": the premise: nothing recorded yet")
                s._adopt_fast_state(report)
                self.assertEqual(s.fast_reason, report["fast_mode_disabled_reason"], case + ": the premise: adopted")
                self.assertIs(sb.read_reg(be.state_dir, SID).get("fastRefusalChecked"), True, case + ": recorded as checked")

    def test_a_refusal_at_an_attach_to_a_cli_launched_with_the_skip_is_recorded_while_todays_read_times_out(self):
        """The attached CLI's launch carried the skip (its record says so), so its "preference" is the API's answer and is
        recorded as checked, though the attach's own version read times out."""
        root, launch = self._launched(fast=True)
        self.assertIs(launch.get("fastGuess"), False, "the premise: that launch carried the skip")
        _flag, busy = self._busy()
        be, s = self._attached(root, launch, extra=busy)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt)
        self.assertEqual(len(be._toasts), 1)
        self.assertIn(ORG, be._toasts[0]["text"], "its launch carried the skip: the organization's words")
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True)

    def test_a_refusal_at_a_launch_whose_read_timed_out_leaves_no_older_checked_mark_for_the_next_kernel(self):
        """The registry holds the checked mark of an earlier refusal that has since lifted (no reason stands). Under Always
        fast with no pick of the session's own, a launch whose `-v` timed out is refused by the CLI's own check: this
        kernel holds Always fast, and the next kernel, reading 2.1.282, launches with the skip and the flag, or, attached
        to that CLI, asks one quiet relaunch that carries them. The registry keeps no checked mark, the older one
        included."""
        for road in ("launch", "attach"):
            with self.subTest(road=road):
                _flag, busy = self._busy()
                root, launch = self._launched(always=True, initialize=GUESS, extra=busy, fastRefusalChecked=True)
                self.assertEqual((launch.get("fast"), launch.get("fastGuess")), (True, True),
                                 "the premise: the switch armed the flag, and the launch carried no skip")
                self.assertFalse(self.launched_session.fast_effective(), "this kernel holds Always fast")
                if road == "launch":
                    be = _backend(root, version="2.1.282")
                    be.login_ok = lambda: True
                    s = self._resume(be)
                else:
                    be, s = self._attached(root, launch, version="2.1.282")
                    s._adopt_fast_state(GUESS)
                    self.assertEqual((s.asks, be._toasts), ([("quiet", "fast mode")], []), "one quiet relaunch")
                env, settings = self._launch(s)
                self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "the skip")
                self.assertTrue(settings.get("fastMode"), "and the flag")
                self.assertFalse(sb.read_reg(root, SID).get("fastRefusalChecked"), "no checked mark in the registry")

    def test_a_refusal_at_a_launch_with_the_skip_after_one_whose_read_timed_out_is_recorded_as_checked(self):
        """A guard of the checked mark for a refusal at a launch that carried the skip, in a kernel that already holds a
        mark in memory alone. The session's own pick under Always fast: the first launch's `-v` times out and the CLI's own
        check refuses, and the pick's flagless relaunch follows. The read answers 2.1.282 after that, the pick is turned on
        again, and the launch that carries the skip and the flag is refused by the API: the refusal is recorded as checked,
        and the next kernel's launch carries no fast flag. With no older mark in the registry, and with one that the first
        refusal clears; each with one session object, and with the object rebuilt from the registry between the two
        refusals."""
        for older in (None, True):
            for rebuilt in (False, True):
                with self.subTest(older=older, rebuilt=rebuilt):
                    self._skip_after_a_timed_out_read(older, rebuilt)

    def _skip_after_a_timed_out_read(self, older, rebuilt):
        flag, busy = self._busy()
        root, _launch = self._launched(always=True, initialize=GUESS, extra=busy, fast=True,
                                       **({"fastRefusalChecked": True} if older else {}))
        s = self.launched_session
        self.assertEqual(sb.read_reg(root, SID).get("fastRefusalChecked"), None if older is None else False,
                         "the premise: marked in memory only")
        _env, settings = self._launch(s)
        self.assertFalse(settings.get("fastMode"), "the premise: the refused pick's flagless relaunch")
        _relaunched(s)
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
        if rebuilt:
            s = self._resume(s.backend)
            self.assertIsNot(s, self.launched_session, "the premise: a new session object")
        os.unlink(flag)
        self.assertTrue(s.backend.set_fast(SID, "on"))
        env, settings = self._launch(s)
        self.assertEqual((env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), bool(settings.get("fastMode"))),
                         ("1", True), "the premise: the skip and the flag")
        _relaunched(s)
        s._adopt_fast_state(GUESS)                                   # the API's refusal
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "recorded as checked")
        be = _backend(root, version="2.1.282")                      # the next kernel, launching
        be.login_ok = lambda: True
        _env, settings = self._launch(self._resume(be))
        self.assertFalse(settings.get("fastMode"), "the refusal holds: no fast flag")

    def test_under_always_fast_a_session_rebuilt_in_the_same_kernel_keeps_a_refusal_at_a_launch_whose_read_timed_out(self):
        """A guard of the mark held in this kernel's memory. Under Always fast, a launch whose `-v` timed out is refused
        by the CLI's own check; then the session object is rebuilt from the registry by the same backend (as _ensure
        does after the CLI exits or an idle close). Its launch carries no fast flag, and the registry holds no checked
        mark. With no pick of the session's own and no older mark in the registry, with an older mark that the refusal
        clears, and with the session's own pick refused over such a mark."""
        for pick, older in ((False, False), (False, True), (True, True)):
            with self.subTest(pick=pick, older=older):
                _flag, busy = self._busy()
                reg = {"fast": True} if pick else {}
                if older:
                    reg["fastRefusalChecked"] = True
                root, _launch = self._launched(always=True, initialize=GUESS, extra=busy, **reg)
                self.assertEqual(sb.read_reg(root, SID).get("fastRefusalChecked"), False if older else None,
                                 "the premise: marked in memory only")
                s = self._resume(self.launched_session.backend)
                self.assertIsNot(s, self.launched_session, "the premise: a new session object")
                _env, settings = self._launch(s)
                self.assertFalse(settings.get("fastMode"), "no second flagged launch in this kernel's life")
                self.assertEqual(sb.read_reg(root, SID).get("fastRefusalChecked"), False if older else None,
                                 "no checked mark in the registry")

    def _no_live_object_after_a_refusal_held_in_memory(self, pick):
        """Under Always fast, a session on Opus 5.5 (with its own fast pick when `pick`) whose launch's `-v` timed out is
        refused by the CLI's own check; then the same kernel holds no live object for it. Returns the backend and the
        session's registry row."""
        _flag, busy = self._busy()
        root, _launch = self._launched(always=True, initialize=GUESS, extra=busy, model=sb.SUBAGENT_STANDARD_MODEL,
                                       **({"fast": True} if pick else {}))
        live = self.launched_session
        be = live.backend
        self.assertEqual((sb.read_reg(root, SID).get("fastRefusalChecked"), SID in be._fast_checked_sids), (None, True),
                         "the premise: the refusal is marked in this kernel's memory only")
        self.assertFalse(live.fast_effective(), "the premise: the live object holds fast mode back")
        be.sessions.pop(SID)
        return be, sb.read_reg(root, SID)

    def test_a_session_with_no_live_object_after_a_refusal_held_in_memory_is_not_offered_subagents_at_standard_speed(self):
        """Its row does not offer the Subagents at standard speed switch, and the registry gets no checked mark. With no
        pick of the session's own and with its own pick refused."""
        for pick in (False, True):
            with self.subTest(pick=pick):
                be, reg = self._no_live_object_after_a_refusal_held_in_memory(pick)
                self.assertIs(be._live_row(reg, SID).get("subagentStandardOffered"), False, "the switch is not offered")
                self.assertIsNone(sb.read_reg(be.state_dir, SID).get("fastRefusalChecked"), "no checked mark")

    def test_turning_subagents_at_standard_speed_on_for_a_session_with_no_live_object_after_a_refusal_held_in_memory_is_refused(self):
        """Turning the Subagents at standard speed switch on is refused with the reason, and the registry is unchanged.
        With no pick of the session's own and with its own pick refused."""
        for pick in (False, True):
            with self.subTest(pick=pick):
                be, reg = self._no_live_object_after_a_refusal_held_in_memory(pick)
                be.recipe_hold = lambda start=False, retry=False: ""        # the version check passed on this version
                self.assertIs(be.set_subagent_standard(SID, "on"), False, "turning it on is refused")
                self.assertTrue(be.last_subagent_standard_refusal.startswith("it needs fast mode on for this session"),
                                be.last_subagent_standard_refusal)
                self.assertEqual(sb.read_reg(be.state_dir, SID), reg, "the registry is unchanged")

    def test_a_reattach_in_the_same_kernel_after_a_refusal_at_a_launch_whose_read_timed_out_records_no_mark(self):
        """A guard of the mark rule at an attach. Under Always fast with no pick of the session's own, a launch whose `-v`
        timed out is refused by the CLI's own check. The same kernel attaches to that CLI again (its host's socket lost,
        the host alive), and the CLI says the same: the registry holds no checked mark, and the next kernel, reading
        2.1.282, launches with the skip and the flag."""
        _flag, busy = self._busy()
        root, launch = self._launched(always=True, initialize=GUESS, extra=busy)
        s = self.launched_session
        self.assertIsNone(sb.read_reg(root, SID).get("fastRefusalChecked"), "the premise: marked in memory only")
        self._launch(s)                                             # the reattach's own options
        self._hello(s.backend, s, launch, attach=True)
        s._fast_unlocked = bool(launch.get("fast"))
        s._fast_guess_asked = False
        s._adopt_fast_state(GUESS)
        self.assertIsNone(sb.read_reg(root, SID).get("fastRefusalChecked"), "no checked mark after the reattach")
        be = _backend(root, version="2.1.282")
        be.login_ok = lambda: True
        env, settings = self._launch(self._resume(be))
        self.assertEqual((env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), bool(settings.get("fastMode"))), ("1", True),
                         "the next kernel launches with the skip and the flag")

    def test_a_refusal_at_a_reattach_after_one_held_in_memory_is_recorded_when_the_guess_rule_does_not_cover_that_cli(self):
        """Under Always fast with no pick of the session's own, a launch whose `-v` timed out is refused by the CLI's
        own check. The same kernel later attaches to a CLI again (its host's socket lost, the host alive), and the
        refusal that CLI reports is recorded as checked and the next kernel's launch carries no fast flag, in three
        cases the guess rule does not cover. Two follow the flagless relaunch, the read answering and the pick turned
        on: at 2.1.282 the pick's launch carries the skip and the flag, and at 2.1.266 it is below the floor. In the
        third the session bills a login without the token, and the reattach to the first CLI has options that carry
        none."""
        for road in ("skip", "floor", "no token"):
            with self.subTest(road=road):
                self._reattach_after_a_refusal_held_in_memory(road)

    def _reattach_after_a_refusal_held_in_memory(self, road):
        version = "2.1.266" if road == "floor" else "2.1.282"
        flag, busy = self._busy()
        root, launch = self._launched(version=version, always=True, initialize=GUESS, extra=busy)
        s = self.launched_session
        self.assertEqual((launch.get("fastGuess"), s._fast_token_case), (True, "retry"),
                         "the premise: the launch carried no skip, its version unread")
        self.assertIsNone(sb.read_reg(root, SID).get("fastRefusalChecked"), "the premise: marked in memory only")
        os.unlink(flag)
        if road == "no token":
            s.auth_login = ""
        else:
            _env, settings = self._launch(s)
            self.assertFalse(settings.get("fastMode"), "the premise: the flagless relaunch")
            _relaunched(s)
            s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
            self.assertTrue(s.backend.set_fast(SID, "on"))
            env, settings = self._launch(s)
            self.assertEqual((env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), bool(settings.get("fastMode"))),
                             (None if road == "floor" else "1", True), "the premise: the pick's launch")
            launch = self._spawned_record(s.backend, s)
            self._hello(s.backend, s, launch, attach=False)
            _relaunched(s)
            s._adopt_fast_state(ON)
        self._launch(s)                                             # the reattach's own options
        self.assertEqual(s._fast_token_case, {"skip": "skip", "floor": "floor", "no token": ""}[road],
                         "the premise: the reattach's own read")
        self._hello(s.backend, s, launch, attach=True)
        s._fast_unlocked = bool(launch.get("fast"))
        s._fast_guess_asked = False
        s._adopt_fast_state(GUESS)
        self.assertNotIn(("quiet", "fast mode"), s.asks, "the premise: not read as a guess")
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "recorded as checked")
        be = _backend(root, version=version)                        # the next kernel, launching
        be.login_ok = lambda: True
        _env, settings = self._launch(self._resume(be))
        self.assertFalse(settings.get("fastMode"), "the refusal holds: no fast flag")

    def test_a_refusal_held_in_this_kernels_memory_is_recorded_when_a_launch_without_the_token_repeats_it(self):
        """A guard of the mark rule for a launch without the token. Under Always fast with no pick of the session's own, a
        launch whose `-v` timed out is refused by the CLI's own check. The session then bills the machine's login that
        Claude Code holds in its own store, so its next launch carries no token and no fast flag, and that CLI reports the
        same "preference" (a managed policy with fastMode false, say): the refusal is recorded as checked."""
        _flag, busy = self._busy()
        root, _launch = self._launched(always=True, initialize=GUESS, extra=busy)
        s = self.launched_session
        self.assertIsNone(sb.read_reg(root, SID).get("fastRefusalChecked"), "the premise: marked in memory only")
        s.auth_login = ""
        env, settings = self._launch(s)
        self.assertEqual((env.get("CLAUDE_CODE_OAUTH_TOKEN"), bool(settings.get("fastMode")), s._fast_token_case),
                         (None, False, ""), "the premise: no token and no flag")
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "recorded as checked")

    def test_a_flagless_relaunch_that_repeats_a_refusal_recorded_as_checked_keeps_the_mark(self):
        """A guard of the mark at a launch without the fast flag. Under Always fast with no pick of the session's own, the
        launch that carries the skip is refused and recorded as checked; its relaunch carries no flag and reports the same
        "preference" (a managed policy with fastMode false, say). The registry keeps the mark, and the next kernel holds
        Always fast back."""
        root, _launch = self._launched(always=True, initialize=GUESS)
        s = self.launched_session
        self.assertEqual(s._fast_token_case, "skip", "the premise: the refused launch carried the skip")
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "the premise: recorded as checked")
        self._launch(s)
        self.assertEqual(s._fast_token_case, "unread", "the premise: the relaunch carries no flag")
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "the registry keeps the mark")
        be = _backend(root)
        be.login_ok = lambda: True
        self.assertFalse(self._resume(be).fast_effective(), "the next kernel holds Always fast back")

    def test_a_refusal_for_another_reason_at_a_launch_left_to_guess_keeps_its_own_words(self):
        """A guard of the words for a reason other than "preference": a launch on 2.1.266 (left to the CLI's own check)
        refused with extra_usage_disabled gives that reason's words on the toast and the Always fast line, with nothing
        about Claude Code's own check."""
        usage = {"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}
        for always in (False, True):
            with self.subTest(always=always):
                self._launched(version="2.1.266", always=always, initialize=usage, fast=not always)
                text = self._refusal_text(self.launched_session.backend, always)
                self.assertIn(sb._FAST_REFUSALS["extra_usage_disabled"], text)
                self.assertNotIn("own check", text)

    def test_a_refusal_for_another_reason_at_an_attach_to_a_cli_left_to_guess_is_recorded_as_checked(self):
        """A guard of the mark at an attach the guess rule covers, for a reason other than "preference": a CLI launched
        on 2.1.266 (left to guess) said on, the kernel restarts onto 2.1.282, and that CLI reports extra_usage_disabled.
        The refusal is recorded as checked."""
        root, launch = self._launched(version="2.1.266", fast=True)
        be, s = self._attached(root, launch, version="2.1.282")
        self.assertEqual((launch.get("fastGuess"), s._fast_token_case), (True, "skip"),
                         "the premise: a CLI left to guess, today's options carrying the skip")
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual(s.fast_reason, "extra_usage_disabled", "the premise: adopted")
        self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "recorded as checked")

    def test_a_guess_at_an_attach_whose_options_carry_no_fast_flag_is_blanked_and_not_recorded(self):
        """A guard of the guess rule at an attach whose options carry no fast flag (Always fast turned off across the
        restart, so today's launch would read no version): the "preference" of a CLI launched left to guess is blanked
        and nothing is recorded, no relaunch and no toast, so Always fast turned on again arms the next launch."""
        root, launch = self._launched(version="2.1.266", always=True)        # left to guess; its initialize said on
        self.assertIs(launch.get("fastGuess"), True, "the premise: the launch carried no skip")
        Path(root, sb.ALWAYS_FAST_STORE).write_text("off")
        be, s = self._attached(root, launch, version="2.1.282")
        self.assertEqual(s._fast_token_case, "unread", "the premise: today's options carry no flag")
        s._adopt_fast_state(GUESS)
        self.assertEqual((s.asks, be._toasts), ([], []))
        self.assertEqual(s.fast_reason, "", "blanked")
        self.assertIsNone(sb.read_reg(root, SID).get("fastRefusalChecked"), "nothing recorded")
        Path(root, sb.ALWAYS_FAST_STORE).write_text("on")
        self.assertTrue(s.fast_effective(), "Always fast turned on again arms the next launch")

    def test_a_refusal_at_a_token_launch_without_the_fast_flag_keeps_the_organizations_words_and_is_recorded_as_checked(self):
        """A token launch without the fast flag (no pick, Always fast off) reads no version and carries no skip. With the
        session's pick turned on since (fast_opt), its relaunch not yet made, that CLI reports "preference" (a managed
        policy with fastMode false, say): the toast gives the organization's words, with nothing about Claude Code's own
        check, and the refusal is recorded as checked."""
        rec, _tok = self._stored_login(self.be)
        _sess(self.be, auth="login", authLogin=rec["id"])
        s = self._resume(self.be)
        self._launch(s)
        self.assertEqual((s._fast_token_case, s._launch_record.get("fastGuess")), ("unread", True),
                         "the premise: no version read, and the launch carried the token without the skip")
        _relaunched(s)
        s.fast_opt = True
        s._adopt_fast_state(GUESS)
        text = self._refusal_text(self.be, False)
        self.assertIn(ORG, text)
        self.assertNotIn(OWN, text)
        self.assertTrue(s.fast_refusal_checked, "marked in this kernel's memory")
        self.assertIs(sb.read_reg(self.be.state_dir, SID).get("fastRefusalChecked"), True, "recorded as checked")

    def test_a_refusal_at_a_token_launch_without_the_fast_flag_holds_always_fast_back_in_the_next_kernel(self):
        """A token launch without the fast flag (no pick, Always fast off) reports "preference" at its initialize (a
        managed policy with fastMode false, say). Always fast is then turned on by writing its store, with no
        apply_model_switches call (the call that sees a flip of Always fast and forgets the refusal): this kernel's next
        launch carries no fast flag, and neither does the next kernel's, with no Always fast line in either."""
        rec, _tok = self._stored_login(self.be)
        _sess(self.be, auth="login", authLogin=rec["id"])
        s = self._resume(self.be)
        _env, settings = self._launch(s)
        self.assertEqual((s._fast_token_case, bool(settings.get("fastMode"))), ("unread", False),
                         "the premise: a launch without the fast flag")
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        Path(self.be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        _env, settings = self._launch(s)
        self.assertFalse(settings.get("fastMode"), "this kernel's next launch carries no fast flag")
        be = _backend(self.be.state_dir)                             # the next kernel, launching
        be.login_ok = lambda: True
        _env, settings = self._launch(self._resume(be))
        self.assertFalse(settings.get("fastMode"), "the next kernel's launch carries no fast flag")
        lines = [m for b in (self.be, be) for m in b._logs if m.startswith("always fast (")]
        self.assertEqual(lines, [])

    def test_a_refusal_at_an_attach_to_a_cli_launched_without_the_fast_flag_keeps_the_organizations_words(self):
        """A token launch without the fast flag (no pick, Always fast off) reports "preference" at its initialize (a
        managed policy with fastMode false, say), recorded as checked. The session's pick is turned on and the kernel
        restarts before its relaunch: the next kernel, on 2.1.282 or on 2.1.266 (below the floor), attaches to that CLI,
        whose launch record says it carried no flag, and the CLI says the same. The pick goes back off with one warn
        toast in the organization's words, with nothing about Claude Code's own check."""
        for today in ("2.1.282", "2.1.266"):
            with self.subTest(today=today):
                be1 = _backend()
                be1.login_ok = lambda: True
                root = be1.state_dir
                rec, _tok = self._stored_login(be1)
                _sess(be1, auth="login", authLogin=rec["id"])
                s = self._resume(be1)
                self._launch(s)
                launch = self._spawned_record(be1, s)
                self.assertEqual((launch.get("fast"), launch.get("fastGuess")), (False, True),
                                 "the premise: no fast flag, and the token without the skip")
                self._hello(be1, s, launch, attach=False)
                s._fast_unlocked = s.fast_effective()
                s._fast_guess_asked = False
                s._adopt_fast_state(GUESS)
                self.assertIs(sb.read_reg(root, SID).get("fastRefusalChecked"), True, "the premise: recorded as checked")
                be1._update_reg(SID, fast=True)                      # the pick turned on, its relaunch not yet made
                be, s = self._attached(root, launch, version=today)
                self.assertEqual(s._fast_token_case, "floor" if today == "2.1.266" else "skip", "the premise: today's read")
                s._adopt_fast_state(GUESS)
                self.assertFalse(s.fast_opt, "the pick goes back off")
                text = self._refusal_text(be, False)
                self.assertIn(ORG, text)
                self.assertNotIn("own check", text)

    OPT_IN = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}

    def _flagless_then_always_fast(self, road, **reg):
        """Kernel 1: a token launch on 2.1.266 without the fast flag (no pick, Always fast off) whose CLI says
        sdk_opt_in_required. Always fast is turned on (and `reg` written) before a relaunch, and the kernel restarts.
        Kernel 2 attaches to that CLI on the `road`: "no guess field" (the record without fastGuess, as a kernel before
        the field wrote it; today 2.1.282) or "below the floor" (the record as written; today 2.1.266)."""
        root, launch = self._launched(version="2.1.266", initialize=self.OPT_IN)
        self.assertEqual((launch.get("fast"), launch.get("fastGuess")), (False, True), "the premise: no fast flag")
        Path(root, sb.ALWAYS_FAST_STORE).write_text("on")
        if reg:
            self.launched_session.backend._update_reg(SID, **reg)
        if road == "no guess field":
            launch = {k: v for k, v in launch.items() if k != "fastGuess"}
        return self._attached(root, launch, version="2.1.282" if road == "no guess field" else "2.1.266")

    def test_under_always_fast_a_preference_adopted_at_an_attach_to_a_cli_launched_without_the_flag_is_said_once(self):
        """Kernel 2's options carry the token, nothing is marked checked, and the CLI launched without the flag reports
        "preference" (a managed policy with fastMode false, say), which kernel 2 adopts on both roads of
        _flagless_then_always_fast. One Always fast line says it, in the organization's words; the switch is held back
        and nothing relaunches."""
        for road in ("no guess field", "below the floor"):
            with self.subTest(road=road):
                be, s = self._flagless_then_always_fast(road)
                self.assertFalse(s._fast_unlocked, "the premise: this connection's flag is the launch's")
                s._adopt_fast_state(GUESS)
                s._adopt_fast_state(GUESS)
                lines = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
                self.assertEqual(len(lines), 1, be._logs)
                self.assertIn(ORG, lines[0])
                self.assertNotIn("own check", lines[0])
                reg = sb.read_reg(be.state_dir, SID)
                self.assertEqual((reg.get("fastRuleRefused"), reg.get("fastRefusalChecked")), ("preference", True))
                self.assertFalse(s.fast_effective(), "the switch is held back")
                self.assertEqual((s.asks, be._toasts), ([], []))

    def test_no_always_fast_line_without_the_flag_for_another_reason_a_launch_here_no_token_a_checked_refusal_an_own_pick_or_slow(self):
        """Guards of that line, on _flagless_then_always_fast's "no guess field" road unless said: a reason other than
        "preference"; a launch kernel 1 made (no attach), the switch turned on before its CLI says "preference"; kernel
        2's options without the token (the billing moved to the machine's own login, which holds none here); a refusal
        already recorded as checked; the session's own pick on, with Always fast on and turned off again; the session put
        on Slow. No Always fast line and no fastRuleRefused in any of them."""
        usage = {"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}
        for case in ("another reason", "a launch here", "no token", "checked", "own pick", "own pick, Always fast off",
                     "slow"):
            with self.subTest(case=case):
                if case == "a launch here":
                    self._launched(version="2.1.266", initialize=self.OPT_IN)
                    s = self.launched_session
                    be = s.backend
                    Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
                    self.assertFalse(s._host_is_attach, "the premise: a launch this kernel made")
                else:
                    reg = {"no token": {"authLogin": ""}, "checked": {"fastRefusalChecked": True}, "own pick": {"fast": True},
                           "own pick, Always fast off": {"fast": True}, "slow": {"fastOff": True}}.get(case, {})
                    be, s = self._flagless_then_always_fast("no guess field", **reg)
                    if case == "own pick, Always fast off":
                        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("off")
                    self.assertEqual(s._fast_token_login, case != "no token", "the premise: today's token")
                    self.assertEqual(s.fast_opt, case.startswith("own pick"), "the premise: the session's own pick")
                s._adopt_fast_state(usage if case == "another reason" else GUESS)
                self.assertTrue(s.fast_reason, "the premise: adopted")
                self.assertEqual([m for m in be._logs if m.startswith("always fast (")], [])
                self.assertFalse(sb.read_reg(be.state_dir, SID).get("fastRuleRefused"))

    def test_a_preference_at_an_attach_to_a_cli_launched_without_the_flag_relaunches_with_a_line_that_names_no_guess(self):
        """A token launch on 2.1.282 without the fast flag (no pick, Always fast off), whose record says the launch had
        the token and no skip; the session's pick is turned on before the kernel restarts. Kernel 2's options carry the
        flag and the skip, and the CLI reports "preference": the session relaunches, and the line names no guess."""
        root, launch = self._launched(initialize=self.OPT_IN)
        self.assertEqual((launch.get("fast"), launch.get("fastGuess")), (False, True), "the premise: no fast flag")
        self.launched_session.backend._update_reg(SID, fast=True)
        be, s = self._attached(root, launch)
        self.assertEqual(s._fast_token_case, "skip", "the premise: today's read")
        s._adopt_fast_state(GUESS)
        self.assertEqual((s.asks, be._toasts), ([("quiet", "fast mode")], []))
        lines = self._guess_lines(be)
        self.assertEqual(len(lines), 1, be._logs)
        self.assertIn("the CLI reports 'preference' for a login it cannot ask", lines[0])
        self.assertNotIn("guess", lines[0])

    def test_a_checked_refusal_at_an_attach_to_a_cli_left_to_guess_with_the_flag_names_claude_codes_own_check_without_a_version_when_todays_read_skips(self):
        """A CLI launched on 2.1.266 with the fast flag (the token and no skip) said on, with a refusal already recorded
        as checked; the kernel restarts onto 2.1.282, whose read gives the skip, and the CLI reports "preference". The
        warn toast for the session's own pick, and the Always fast line without one, name Claude Code's own check with
        no version clause, and not the organization."""
        for always in (False, True):
            with self.subTest(always=always):
                root, launch = self._launched(version="2.1.266", always=always, fast=not always, fastRefusalChecked=True)
                be, s = self._attached(root, launch, version="2.1.282")
                self.assertEqual((launch.get("fast"), s._fast_token_case), (True, "skip"), "the premise")
                s._adopt_fast_state(GUESS)
                text = self._refusal_text(be, always)
                self.assertIn(OWN, text)
                self.assertNotIn(ORG, text)
                self.assertNotIn("2.1.271", text)
                self.assertNotIn("could not be read", text)

    def test_below_the_floor_the_refusal_names_claude_codes_own_check_and_the_version_that_lets_the_api_decide(self):
        """A launch on 2.1.266 left the CLI to its own check, which refused: the warn toast for the session's own pick,
        and the Always fast line without one, name that check and 2.1.271."""
        for always in (False, True):
            with self.subTest(always=always):
                self._launched(version="2.1.266", always=always, initialize=GUESS, fast=not always)
                text = self._refusal_text(self.launched_session.backend, always)
                self.assertIn(OWN + " (Claude Code 2.1.271 or later lets the API decide)", text)
                self.assertNotIn(ORG, text)

    def test_at_a_launch_whose_read_timed_out_the_refusal_names_claude_codes_own_check_and_the_unread_version(self):
        """A launch whose `-v` timed out left the CLI to its own check, which refused: the toast and the Always fast line
        name that check and say the version could not be read."""
        _flag, busy = self._busy()
        for always in (False, True):
            with self.subTest(always=always):
                self._launched(always=always, initialize=GUESS, extra=busy, fast=not always)
                text = self._refusal_text(self.launched_session.backend, always)
                self.assertIn(OWN + " (Claude Code's version could not be read)", text)
                self.assertNotIn(ORG, text)

    def test_at_an_attach_with_no_launch_record_the_refusal_says_it_may_come_from_claude_codes_own_check(self):
        """An attach to a CLI whose hello carries no launch record, under a token login on 2.1.266 (so the "preference" is
        answered, not relaunched as a guess): the toast and the Always fast line say the refusal may come from Claude
        Code's own check, and give neither the organization nor that check as its cause."""
        for always in (False, True):
            with self.subTest(always=always):
                root, _launch = self._launched(version="2.1.266", always=always, fast=not always)
                be, s = self._attached(root, None, version="2.1.266")
                s._adopt_fast_state(GUESS)
                text = self._refusal_text(be, always)
                self.assertIn("the refusal may come from Claude Code's own check", text)
                self.assertNotIn(ORG, text)
                self.assertNotIn(OWN, text)

    def test_below_the_floor_an_attach_whose_record_has_no_guess_field_keeps_the_clis_on_and_asks_no_relaunch(self):
        """An attach whose launch record has no fastGuess and names a stored login, on a Claude Code known to be below
        2.1.271: the CLI's "on" is kept, and no relaunch is asked."""
        root, launch = self._launched(version="2.1.266", fast=True)
        older = {k: v for k, v in launch.items() if k != "fastGuess"}
        self.assertTrue(older.get("authLogin"), "the premise: the record names the stored login")
        be, s = self._attached(root, older, version="2.1.266")
        self.assertEqual(s._fast_token_case, "floor", "the premise: today's version is known and below the floor")
        s._adopt_fast_state(ON)
        self.assertEqual((s.fast, s.fast_opt), ("on", True))
        self.assertEqual((s.asks, be._toasts), ([], []))

    def test_a_refused_own_pick_under_always_fast_holds_always_fast_back_from_its_flagless_relaunch_until_the_next_pick(self):
        """A session's own pick refused below the floor while the machine's Always fast switch is on too: the pick goes
        back off and the refusal is recorded as checked. The pick's flagless relaunch, in the same kernel, reports
        sdk_opt_in_required at its initialize and at its first turn's init, and from then on Always fast is held back
        for the session: fastRuleRefused "preference" in the registry, no relaunch asked, no Always fast line. The
        launch after it carries neither the fast flag nor the skip, in that kernel and after a restart onto Claude Code
        2.1.282 or 2.1.266. A Fast pick lifts the hold, as do turning Always fast off or on and a launch on another
        billing; this test makes only the Fast pick."""
        opt_in = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}
        for version in ("2.1.282", "2.1.266"):
            with self.subTest(launch_after=version):
                root, _launch = self._launched(version="2.1.266", always=True, initialize=GUESS, fast=True)
                s = self.launched_session
                be = s.backend
                reg = sb.read_reg(root, SID)
                self.assertEqual((bool(reg.get("fast")), reg.get("fastRefusalChecked"), reg.get("fastRuleRefused")),
                                 (False, True, None), "the premise: the pick refused and recorded as checked")
                self.assertIn(("now", "fast mode refusal"), s.asks, "the premise: the refusal asks for a relaunch")
                s.asks.clear()
                _env, settings = self._launch(s)
                self.assertFalse(settings.get("fastMode"), "the premise: the relaunch the refusal asks for is flagless")
                _relaunched(s)                                       # that relaunch, in the same kernel
                s._adopt_fast_state(opt_in)                          # its initialize
                s._adopt_fast_state(opt_in)                          # its first turn's init
                self.assertFalse(s.fast_effective(), "Always fast is held back")
                reg = sb.read_reg(root, SID)
                self.assertEqual((reg.get("fastRuleRefused"), reg.get("fastRefusalChecked")), ("preference", True),
                                 "the pick's refusal carried to Always fast, checked, in the registry")
                self.assertEqual((reg.get("liveFast"), reg.get("liveFastReason")), ("off", ""), "the badge reads Slow")
                self.assertEqual(s.asks, [], "no relaunch asked")
                self.assertEqual([m for m in be._logs if m.startswith("always fast (web): the CLI refused")], [],
                                 "no Always fast line")
                self.assertEqual([t["type"] for t in be._toasts], ["warn"], "only the pick's toast")
                env, settings = self._launch(s)
                self.assertEqual((bool(settings.get("fastMode")), env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK")),
                                 (False, None), "the launch after it in this kernel: no flag, no skip")
                be = _backend(root, version=version)                 # a restart onto the claude named
                be.login_ok = lambda: True
                s = self._resume(be)
                env, settings = self._launch(s)
                self.assertEqual((bool(settings.get("fastMode")), env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK")),
                                 (False, None), "the launch after the restart: no flag, no skip")
                self.assertTrue(be.set_fast(SID, "on"))              # the user's next Fast pick
                self.assertEqual((s.fast_rule_refused, sb.read_reg(root, SID).get("fastRuleRefused")), ("", ""),
                                 "the pick lifts the hold")
                env, settings = self._launch(s)
                self.assertEqual((bool(settings.get("fastMode")), env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK")),
                                 (True, "1" if version == "2.1.282" else None),
                                 "the next launch carries the flag, and the skip on 2.1.271 or later")

    def test_a_key_launch_carrying_its_accounts_skip_is_no_token_login_at_an_attach(self):
        """The skip alone makes no token login: a key whose account allows fast mode gets it too (key_fast_org_env, the
        kernel having asked that account itself), with no token in the launch, so there is no guess to settle. Attached
        under a host that keeps no launch record, its "preference" (a managed policy's, say) is a real refusal: the pick
        off, one warn toast, no relaunch asked as for a guess. The toast, as at any attach with no launch record, says the
        refusal may come from Claude Code's own check."""
        helper = Path(self.cfg, "helper.sh")
        helper.write_text("#!/bin/sh\necho synthetic-helper-output\n")
        helper.chmod(0o700)
        Path(self.cfg, "settings.json").write_text(json.dumps({"apiKeyHelper": str(helper)}))
        sb._cred.forget_helper_key()
        sb._FAST_ORG_VERDICTS.clear()
        self.addCleanup(sb._FAST_ORG_VERDICTS.clear)
        sb._fetch_key_fast_org = lambda key: True          # the key's account allows fast mode (restored by tearDown)
        be = _backend()
        be.login_ok = lambda: True
        self.assertTrue(be.key_available, "the premise: the box bills a key")
        s = _sess(be, auth="key", fast=True)
        env, _settings = self._launch(s)
        self.assertEqual(env.get("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK"), "1", "the premise: the key account's skip")
        self.assertIsNone(env.get("CLAUDE_CODE_OAUTH_TOKEN"), "the premise: no token rides the launch")
        self._hello(be, s, None, attach=True)
        s._fast_unlocked = True
        s._fast_guess_asked = False
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt, "a real refusal: the pick goes back off")
        self.assertEqual([t["type"] for t in be._toasts], ["warn"])
        self.assertIn("the refusal may come from Claude Code's own check", be._toasts[0]["text"],
                      "no launch record: the words assert no cause")
        self.assertNotIn(ORG, be._toasts[0]["text"])
        self.assertNotIn(("quiet", "fast mode"), s.asks, "no relaunch as for a token login's guess")
        self.assertEqual(self._guess_lines(be), [])


class AGuessFromAnAttachedCli(unittest.TestCase):
    """The deploy's own case: CLIs launched before the fix survive the kernel's restart under their hosts, still guessing."""

    def test_the_pick_stays_on_and_the_session_relaunches_onto_fast(self):
        be = _backend()
        s = _sess(be, fast=True)
        _attached_token_cli(s)
        s._adopt_fast_state(GUESS)
        self.assertTrue(s.fast_opt, "the guess is not the org's answer: the user's pick stands")
        self.assertTrue(sb.read_reg(be.state_dir, SID).get("fast"), "…on disk too")
        self.assertEqual(be._toasts, [], "no 'fast mode isn't available' toast for a guess")
        self.assertEqual(s.asks, [("quiet", "fast mode")], "a relaunch, on the road that cuts nothing")
        self.assertEqual(s.fast_reason, "", "the badge keeps its toggle: the reason is the one a relaunch cures")
        self.assertFalse(sb.read_reg(be.state_dir, SID).get("fastRefusalChecked"))
        self.assertTrue(any("fast mode (web): the CLI reports 'preference'" in m for m in be._logs), be._logs)
        s._adopt_fast_state(GUESS)
        self.assertEqual(len(s.asks), 1, "every init repeats the guess until the relaunch lands: asked once per connection")
        _relaunched(s)
        self.assertTrue(s._fast_unlocked, "the relaunch carries the opt-in")
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual((s.fast, s.fast_opt), ("on", True), "fast, with the pick on")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("liveFast"), "on")

    def test_always_fast_held_back_by_a_pre_fix_guess_re_arms_and_its_memory_lifts(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        # the reg a pre-fix kernel left: the switch's refusal memory and the live reason, both the CLI's guess, unchecked
        s = _sess(be, fastRuleRefused="preference", liveFastReason="preference")
        self.assertTrue(s.fast_effective(), "a pre-fix 'preference' no longer holds the session at normal speed")
        _attached_token_cli(s)
        s._adopt_fast_state(GUESS)
        self.assertFalse([m for m in be._logs if m.startswith("always fast (web): the CLI refused")],
                         "a guess is not charged to the switch")
        self.assertEqual(s.asks, [("quiet", "fast mode")])
        _relaunched(s)
        s._adopt_fast_state({"fast_mode_state": "on"})
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg.get("fastRuleRefused"), reg.get("liveFast")), ("", "on"), "the memory lifts on the CLI's 'on'")

    def test_an_attach_to_a_cli_that_is_not_a_token_login_is_answered_as_before(self):
        be = _backend()
        s = _sess(be, fast=True)
        s._fast_token_login = False                  # a login in the CLI's own store, or a key: the CLI asked the org
        s._host_is_attach = True
        s._fast_unlocked = True
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt)
        self.assertEqual(len(be._toasts), 1)


class ARealRefusalStaysLoud(unittest.TestCase):
    def test_a_preference_from_a_launch_that_carried_the_skip_is_the_apis_answer(self):
        be = _backend()
        s = _sess(be, fast=True)
        s._fast_token_login = True
        s._fast_token_case = "skip"
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt, "the API refused the fast request: the pick is back off")
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg.get("fast"), reg.get("fastRefusalChecked"), reg.get("liveFastReason")), (False, True, "preference"))
        self.assertEqual(len(be._toasts), 1)
        self.assertEqual(be._toasts[0]["type"], "warn")
        self.assertIn("the account's organization has fast mode turned off", be._toasts[0]["text"],
                      "the real reason, in words, not the CLI's token")
        self.assertIn(("now",), s.asks, "the flagless reconnect the refused pick has always taken")

    def test_a_managed_policy_with_fast_mode_false_refuses_loudly_and_turns_the_pick_off(self):
        """policySettings.fastMode false: the CLI answers "preference" whatever the login, before any org check. A guard of
        a launch with no token: the refusal is recorded as checked, and the toast keeps the organization's words."""
        be = _backend()
        s = _sess(be, fast=True)
        s._fast_token_login = False
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt)
        self.assertFalse(sb.read_reg(be.state_dir, SID).get("fast"))
        self.assertIs(sb.read_reg(be.state_dir, SID).get("fastRefusalChecked"), True, "recorded as checked")
        self.assertEqual(len(be._toasts), 1)
        self.assertIn("fast mode isn't available for web", be._toasts[0]["text"])
        self.assertIn(ORG, be._toasts[0]["text"])
        self.assertNotIn("own check", be._toasts[0]["text"])
        self.assertTrue(any(m.startswith("fast mode (web): the CLI refused the toggle") for m in be._logs), be._logs)

    def test_a_checked_refusal_holds_always_fast_and_is_not_re_asked_at_every_attach(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        s = _sess(be, fastRuleRefused="preference", liveFastReason="preference", fastRefusalChecked=True)
        self.assertFalse(s.fast_effective(), "a checked 'preference' is the org's answer: no refuse, relaunch, refuse loop")
        s._fast_token_login = True
        s._host_is_attach = True
        s._hello_launch = None                       # a host too old to say how its CLI was launched
        s._fast_unlocked = True                      # the CLI that refused, still running with the flag across a restart
        s._adopt_fast_state(GUESS)
        self.assertEqual(s.asks, [], "the answer already stands: no relaunch at each kernel restart")
        self.assertEqual(be._logs, [])
        self.assertEqual((s.fast_reason, sb.read_reg(be.state_dir, SID).get("liveFastReason")), ("preference", "preference"),
                         "the reason stands as the real one, not blanked as a guess")

    def test_a_checked_refusal_turns_a_pick_made_since_back_off_at_an_attach(self):
        """The guess rule's not-checked conjunct, with a pick to show it: a "preference" already adopted from a checked
        launch, the user's pick armed again since (its relaunch still waiting when the kernel restarted), meets the same
        "preference" at an attach to a CLI whose host keeps no launch record. It is the real answer standing: the pick
        goes back off with one warn toast, and no relaunch is asked as for a guess."""
        be = _backend()
        s = _sess(be, fast=True, liveFastReason="preference", fastRefusalChecked=True)
        _attached_token_cli(s)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt, "the pick goes back off")
        self.assertFalse(sb.read_reg(be.state_dir, SID).get("fast"))
        self.assertEqual([t["type"] for t in be._toasts], ["warn"])
        self.assertNotIn(("quiet", "fast mode"), s.asks, "no relaunch as for a guess")
        self.assertEqual(s.fast_reason, "preference")

    def test_a_refusal_after_on_is_remembered_in_this_kernel_so_the_next_connect_carries_no_flag(self):
        """The in-memory checked mark: a relaunched token-login session under Always fast hears "on" at initialize (the
        skip, no fast request yet), then the API's "preference". From then on fast_effective holds, in this kernel's
        memory as on disk, so the next connect carries no flag; without the session's own pick the connects after it
        stay flagless and the Always fast line is said once. Unmarked in memory, a "preference" would stand down as a
        pre-fix guess does, re-arming the flag at every connect of the kernel's life, a refused request each."""
        def connect(s):
            """One relaunch under an API that refuses fast mode: a flagged connect says on, then the refusal; a flagless
            one says the opt-in is missing. Returns whether it carried the flag."""
            _relaunched(s)
            if s._fast_unlocked:
                s._adopt_fast_state(ON)
                s._adopt_fast_state(GUESS)
            else:
                s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
            return s._fast_unlocked

        for own_pick in (False, True):
            with self.subTest(own_pick=own_pick):
                be = _backend()
                Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
                s = _sess(be, fast=own_pick)
                s._fast_token_login = True
                self.assertTrue(connect(s), "the first relaunch carries the flag")
                self.assertTrue(s.fast_refusal_checked)
                self.assertFalse(s.fast_effective(), "the refusal holds the switch at once")
                self.assertFalse(s.fast_opt)
                _relaunched(s)
                self.assertFalse(s._fast_unlocked, "the next connect carries no flag")
                if own_pick:
                    self.assertEqual([t["type"] for t in be._toasts], ["warn"])
                    continue
                s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
                self.assertEqual([connect(s) for _ in range(3)], [False, False, False], "nor the connects after it")
                refused = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
                self.assertEqual(len(refused), 1, be._logs)

    def test_a_pre_fix_reason_other_than_preference_still_holds(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        s = _sess(be, liveFastReason="extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "only the guess's reason stands down")
        s = _sess(be, fastRuleRefused="extra_usage_disabled")
        self.assertFalse(s.fast_effective())


if __name__ == "__main__":
    unittest.main()
