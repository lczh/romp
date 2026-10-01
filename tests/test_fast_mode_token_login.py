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
  * every launch whose login rides the environment carries the skip (sdk_backend.token_fast_org_env), a stored login and
    a machine login the manager's environment carries alike; a login the CLI holds in its own store, and a key launch, do
    not get it from this rule;
  * a "preference" from a CLI this kernel only attached to, under such a login, is that CLI's guess when it predates the
    skip: the pick stays on, nothing is remembered, and the session relaunches on the quiet road, after which the CLI's
    own answer decides;
  * a "preference" from a launch that carried the skip, or from a session the CLI can ask about (a managed policy with
    fastMode false, say), is real: loud, the pick back off, remembered as checked;
  * a "preference" a reg written before the fix still carries no longer holds Always fast back, while a checked one, or
    any other reason, still does.
The judges' fast calls billed to such a login carry the skip too (tests/test_judge_fast_tiers.py).

Synthetic sids, names and tokens only; a token-shaped string is assembled at run time, never a literal."""
import json
import os
import secrets
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_fasttoken", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-00000000a101"   # private to this module (CLAUDE.md, goal-store fixtures)
SKIP = {"CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK": "1"}
GUESS = {"fast_mode_state": "off", "fast_mode_disabled_reason": "preference"}


def _token():
    """A synthetic bearer of the setup-token's shape, assembled at run time (the scanner reads this file)."""
    return "".join(["sk", "-ant-", "oat01-", secrets.token_hex(12)])


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
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
    """What the connect leaves on a session that ATTACHED to a CLI surviving the kernel, its login a token: _options
    (which runs for an attach too) marks the token login, the transport marks the attach, the snapshot arms the flag."""
    s._fast_token_login = True
    s._host_is_attach = True
    s._fast_unlocked = s.fast_effective()
    s._fast_guess_asked = False


def _relaunched(s):
    """A launch this kernel made: the skip rides it (token_fast_org_env), and it is no attach."""
    s._host_is_attach = False
    s._fast_unlocked = s.fast_effective()
    s._fast_guess_asked = False


class TheRule(unittest.TestCase):
    def test_a_token_in_the_launch_environment_carries_the_skip_and_nothing_else_does(self):
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": _token()}), SKIP)
        self.assertEqual(sb.token_fast_org_env({}), {})
        self.assertEqual(sb.token_fast_org_env(None), {})
        self.assertEqual(sb.token_fast_org_env({"CLAUDE_CODE_OAUTH_TOKEN": ""}), {})
        self.assertEqual(sb.token_fast_org_env({"ANTHROPIC_AUTH_TOKEN": _token()}), {},
                         "a gateway bearer is not a Claude login: the CLI's own check stands")


class Launches(unittest.TestCase):
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
        kw = self.be._options(s, dict)
        p = kw.get("settings")
        return kw["env"], (json.loads(Path(p).read_text()) if p else {})

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
        self.assertNotIn("CLAUDE_CODE_SKIP_FAST_MODE_ORG_CHECK", env,
                         "a login in the CLI's own credential store: the CLI asks the org itself")
        self.assertFalse(s._fast_token_login)


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
        """policySettings.fastMode false: the CLI answers "preference" whatever the login, before any org check."""
        be = _backend()
        s = _sess(be, fast=True)
        s._fast_token_login = False
        _relaunched(s)
        s._adopt_fast_state(GUESS)
        self.assertFalse(s.fast_opt)
        self.assertFalse(sb.read_reg(be.state_dir, SID).get("fast"))
        self.assertEqual(len(be._toasts), 1)
        self.assertIn("fast mode isn't available for web", be._toasts[0]["text"])
        self.assertTrue(any(m.startswith("fast mode (web): the CLI refused the toggle") for m in be._logs), be._logs)

    def test_a_checked_refusal_holds_always_fast_and_is_not_re_asked_at_every_attach(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        s = _sess(be, fastRuleRefused="preference", liveFastReason="preference", fastRefusalChecked=True)
        self.assertFalse(s.fast_effective(), "a checked 'preference' is the org's answer: no refuse, relaunch, refuse loop")
        s._fast_token_login = True
        s._host_is_attach = True
        s._fast_unlocked = True                      # the CLI that refused, still running with the flag across a restart
        s._adopt_fast_state(GUESS)
        self.assertEqual(s.asks, [], "the answer already stands: no relaunch at each kernel restart")
        self.assertEqual(be._logs, [])

    def test_a_pre_fix_reason_other_than_preference_still_holds(self):
        be = _backend()
        Path(be.state_dir, sb.ALWAYS_FAST_STORE).write_text("on")
        s = _sess(be, liveFastReason="extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "only the guess's reason stands down")
        s = _sess(be, fastRuleRefused="extra_usage_disabled")
        self.assertFalse(s.fast_effective())


if __name__ == "__main__":
    unittest.main()
