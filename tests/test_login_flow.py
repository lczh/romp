#!/usr/bin/env python3
"""The in-dashboard LOGIN flow (T157, the user 2026-08-28: an expired login stalls everything when
they're on the phone over Tailscale — the dashboard becomes the login surface). The kernel runs
Claude Code's own `claude auth login --claudeai` over pipes: parse the code=true OAuth URL from its
stdout → stream it to the dashboard → PASS the user's code straight to its stdin → report the verdict
its EXIT STATUS gives, with the CLI's own stderr sentence when it fails.
HOUSE RULE UNDER TEST: the code and tokens exist nowhere but the stdin write and the CLI's own
store — never logged, never in state files (the secrecy pin scans for the fake code after a full
flow). All against a MOCKED login CLI (never a real account); hermetic state; synthetic values."""
import hashlib
import json
import os
import stat
import tempfile
import time
import unittest
from unittest import mock
from pathlib import Path
from romp_load import load_source
from urllib.parse import parse_qs, urlencode, urlsplit

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)

# the MOCK login CLI: the transcript of `claude auth login --claudeai` as captured from CLI 2.1.282 over
# pipes on 2026-09-30, with synthetic URL values. stdout: "Opening browser to sign in…", "If the browser
# didn't open, visit: <URL>", then the unterminated "Paste code here if prompted > ". A line without its
# "#state" half: "Invalid code. Please make sure the full code was copied." on stderr, and the CLI reads on.
# A wrong code: "Login failed: Request failed with status code 400" on stderr, exit 1. A good one: "Login
# successful." on stdout, exit 0. Any other invocation (the interactive REPL the kernel used to drive in a
# PTY) is refused at once, so a driver that still spawns the REPL fails here instead of hanging. It records
# its argv and a SHA-256 of each code it read (proof of arrival without the code existing anywhere readable).
_MOCK_DIR = tempfile.mkdtemp()
_MOCK = os.path.join(_MOCK_DIR, "claude")
_MARKER = os.path.join(_MOCK_DIR, "code-sha.txt")
_ARGV = os.path.join(_MOCK_DIR, "argv.json")
_GOOD = "SYNTH-GOOD-CODE#SYNTH-STATE"
_URL = "https://claude.com/cai/oauth/authorize?" + urlencode({
    "code": "true", "client_id": "11111111-2222-4333-8444-555555555555",
    "response_type": "code",
    "redirect_uri": "https://platform.claude.com/oauth/code/callback",
    "scope": "user:profile user:inference", "code_challenge": "SYNTHETIC-CHALLENGE",
    "code_challenge_method": "S256", "state": "SYNTHETIC-STATE",
})
with open(_MOCK, "w") as f:
    f.write('''#!/usr/bin/env python3
import sys, os, hashlib, json, time
def say(s):
    sys.stdout.write(s); sys.stdout.flush()
def complain(s):
    sys.stderr.write(s); sys.stderr.flush()
json.dump(sys.argv[1:], open(@@ARGV@@, "w"))
if sys.argv[1:] != ["auth", "login", "--claudeai"]:
    complain("mock: unexpected invocation\\n"); sys.exit(2)
mode = os.environ.get("ROMP_LOGIN_TEST_OUTPUT", "plain")
if mode == "refuse":
    complain("Managed settings on this machine configure a Cloud gateway sign-in; run interactive /login to authenticate.\\n")
    sys.exit(1)
url = @@URL@@
lead = "If the browser didn't open, visit: "
say("Opening browser to sign in\\u2026\\n")
if mode == "chunked":
    say(lead + url[:80])
    time.sleep(0.3)
    say(url[80:] + "\\n")
elif mode == "osc":
    say(lead + "\\x1b]8;id=1;" + url + "\\x1b\\\\" + url + "\\x1b]8;;\\x1b\\\\\\n")
elif mode == "wrapped":
    # Terminal layouts may wrap the hyperlink target as well as its visible label.
    wrapped = "\\r\\n  ".join(url[i:i+80] for i in range(0, len(url), 80))
    say(lead + "\\x1b]8;;" + wrapped + "\\x1b\\\\" + wrapped + "\\x1b]8;;\\x1b\\\\\\n")
elif mode == "ai":
    say(lead + url.replace("claude.com/cai/", "claude.ai/") + "\\n")
elif mode == "badlink":
    say(lead + "https://evil.example/oauth/authorize?x=1\\n")
else:
    say(lead + url + "\\n")
say("Paste code here if prompted > ")
for line in sys.stdin:
    code = line.strip()
    with open(@@MARKER@@, "a") as m:
        m.write(hashlib.sha256(code.encode()).hexdigest() + "\\n")
    part, _, state = code.partition("#")
    if not part or not state:
        complain("Invalid code. Please make sure the full code was copied.\\n")
        continue
    if mode == "quiet":
        sys.exit(0)                 # a success that prints nothing: the exit status alone decides
    if mode == "unfamiliar":
        complain("OAuth error: a sentence this kernel has never seen\\n"); sys.exit(1)
    if code == @@GOOD@@:
        say("Login successful.\\n"); sys.exit(0)
    complain("Login failed: Request failed with status code 400\\n"); sys.exit(1)
'''.replace("@@MARKER@@", repr(_MARKER)).replace("@@URL@@", repr(_URL)).replace("@@ARGV@@", repr(_ARGV))
   .replace("@@GOOD@@", repr(_GOOD)))
os.chmod(_MOCK, os.stat(_MOCK).st_mode | stat.S_IEXEC)
# The mock is installed as the CLI per TEST (LoginFlow.setUp), never at module level: a module-level write executes at
# collection and holds for every test after it, and this one reached every lab kernel of a whole run (2026-09-16, the
# conftest floor's comment tells the rest). The driver resolves the binary at each call (_claude_bin), so setUp is early enough.

load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_login", os.path.join(BIN, "romp-kernel"))
jd = km.jd


def _wait_state(want, seconds=15):
    t0 = time.time()
    while time.time() - t0 < seconds:
        st = km._login_state()
        if st["state"] == want:
            return st
        time.sleep(0.05)
    return km._login_state()


def _wait_err(seconds=15):
    t0 = time.time()
    while time.time() - t0 < seconds:
        st = km._login_state()
        if st["err"]:
            return st
        time.sleep(0.05)
    return km._login_state()


class LoginURL(unittest.TestCase):
    def test_plain_link_waits_at_every_read_boundary(self):
        for cut in range(len(_URL) + 1):
            with self.subTest(cut=cut):
                self.assertEqual(km._login_url(_URL[:cut]), "")
        self.assertEqual(km._login_url(_URL + "\r\nPaste code here"), _URL)

    def test_osc_link_waits_for_its_complete_terminator(self):
        for end in ("\x07", "\x1b\\"):
            stream = "\x1b]8;;" + _URL + end
            for cut in range(len(stream)):
                with self.subTest(terminator=repr(end), cut=cut):
                    self.assertEqual(km._login_url(stream[:cut]), "")
            self.assertEqual(km._login_url(stream), _URL)

    def test_wrapped_label_does_not_replace_full_hyperlink(self):
        stream = "\x1b]8;;" + _URL + "\x1b\\" + _URL[:80] + "\r\n"
        self.assertEqual(km._login_url(stream), _URL)

    def test_missing_parameters_never_produce_a_login_link(self):
        u = urlsplit(_URL)
        params = parse_qs(u.query)
        for key in params:
            incomplete = u._replace(query=urlencode({k: v for k, v in params.items()
                                                     if k != key}, doseq=True)).geturl()
            with self.subTest(missing=key):
                self.assertEqual(km._login_url(incomplete + "\n"), "")

    def test_partial_first_line_does_not_hide_a_complete_link_later(self):
        self.assertEqual(km._login_url(_URL[:80] + "\n" + _URL + "\n"), _URL)

    def test_unrelated_hosts_are_not_login_links(self):
        self.assertEqual(km._login_url(_URL.replace("claude.com", "claude.com.example.invalid")
                                      + "\n"), "")

    def test_the_captured_2_1_282_stdout_yields_its_link(self):
        # the subcommand's stdout exactly as 2.1.282 wrote it to a pipe (values synthetic): a plain URL
        # ended by a newline, then the prompt with no newline at all
        out = ("Opening browser to sign in…\nIf the browser didn't open, visit: " + _URL
               + "\nPaste code here if prompted > ")
        self.assertEqual(km._login_url(out), _URL)


class LoginSaid(unittest.TestCase):
    """What of the CLI's stderr reaches the payload (the login state rides /version)."""

    def test_the_clis_sentence_passes_through(self):
        self.assertEqual(km._login_said("Login failed: Request failed with status code 400\n"),
                         "Login failed: Request failed with status code 400")

    def test_escapes_controls_and_long_token_runs_are_removed(self):
        run = "Q" * 40
        said = km._login_said("\x1b[31mLogin failed:\x1b[0m code " + run + "#" + run + "\r\x07")
        self.assertNotIn(run, said)
        self.assertNotIn("\x1b", said)
        self.assertTrue(said.startswith("Login failed: code "), said)

    def test_it_is_capped(self):
        self.assertLessEqual(len(km._login_said("word " * 500)), 300)


class LoginFlow(unittest.TestCase):
    def setUp(self):
        self._bin_saved = os.environ.get("ROMP_CLAUDE_BIN")
        os.environ["ROMP_CLAUDE_BIN"] = _MOCK                 # this test's CLI, restored below (the conftest floor otherwise)
        km._login_cancel()
        for p in (_MARKER, _ARGV):
            try:
                os.unlink(p)
            except OSError:
                pass

    def tearDown(self):
        km._login_cancel()
        if self._bin_saved is None:
            os.environ.pop("ROMP_CLAUDE_BIN", None)
        else:
            os.environ["ROMP_CLAUDE_BIN"] = self._bin_saved

    def test_full_flow_url_code_done_and_the_secrecy_pin(self):
        self.assertEqual(km._login_start(), "")
        st = _wait_state("url")
        self.assertEqual(st["state"], "url", "the driver reached the URL: %r" % st)
        self.assertEqual(json.load(open(_ARGV)), ["auth", "login", "--claudeai"],
                         "the kernel runs the CLI's own login subcommand, not the interactive REPL")
        self.assertEqual(st["url"], _URL)
        self.assertTrue(st["url"].startswith("https://claude.com/cai/oauth/authorize?code=true"),
                        "the code=true URL, parsed clean: %r" % st["url"])
        self.assertNotIn("\x1b", st["url"], "no terminal escapes survive into the link")
        self.assertEqual(km._login_code(_GOOD), "")
        st = _wait_state("done")
        self.assertEqual(st["state"], "done", "success surfaces truthfully: %r" % st)
        self.assertEqual(open(_MARKER).read(), hashlib.sha256(_GOOD.encode()).hexdigest() + "\n",
                         "the code reached the CLI intact as ONE line (proved by hash, never by value)")
        # THE SECRECY PIN: the code exists NOWHERE in romp's state or this kernel module's world
        for root, _, files in os.walk(str(jd.STATE)):
            for fn in files:
                p = os.path.join(root, fn)
                try:
                    body = open(p, "rb").read()
                except OSError:
                    continue
                self.assertNotIn(b"SYNTH-GOOD-CODE", body,
                                 "the code leaked into state file %s — the pass-through rule broke" % p)

    def test_a_bad_code_fails_at_once_with_the_clis_own_reason(self):
        # the stuck state (2026-09-30): the REPL on 2.1.282 answered a rejected code with "OAuth error: …", a phrase the
        # old matcher never knew, and the flow sat at "checking the code…" until the ten-minute timeout. The CLI's exit
        # is the verdict now, and its sentence is the reason shown.
        self.assertEqual(km._login_start(), "")
        _wait_state("url")
        self.assertEqual(km._login_code("SYNTH-BAD-CODE#SYNTH-STATE"), "")
        st = _wait_state("error", seconds=5)
        self.assertEqual(st["state"], "error", "no waiting for a timeout: %r" % st)
        self.assertIn("Request failed with status code 400", st["err"], "the CLI's own reason: %r" % st["err"])
        self.assertIn("start", st["err"].lower(), "the copy names the retry: %r" % st["err"])
        self.assertNotIn("SYNTH-BAD-CODE", st["err"], "never the code")

    def test_an_incomplete_code_returns_to_the_paste_step(self):
        # a code without its "#state" half: the CLI says so and reads on, so the flow goes back to the paste step with
        # its sentence, and the next paste lands on the same CLI
        self.assertEqual(km._login_start(), "")
        _wait_state("url")
        self.assertEqual(km._login_code("SYNTH-HALF-CODE"), "")
        st = _wait_err(seconds=5)
        self.assertEqual(st["state"], "url", "back at the paste step: %r" % st)
        self.assertIn("Invalid code", st["err"])
        self.assertEqual(km._login_code(_GOOD), "", "a second paste is accepted")
        st = _wait_state("done", seconds=5)
        self.assertEqual(st["state"], "done", "the second paste completes the same flow: %r" % st)
        self.assertEqual(st["err"], "", "the complaint cleared with the new code")

    def test_the_exit_status_is_the_verdict_not_the_words(self):
        for mode, want in (("quiet", "done"), ("unfamiliar", "error")):
            with self.subTest(mode=mode):
                km._login_cancel()
                with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": mode}):
                    self.assertEqual(km._login_start(), "")
                _wait_state("url")
                self.assertEqual(km._login_code(_GOOD), "")
                st = _wait_state(want, seconds=5)
                self.assertEqual(st["state"], want, "%s: %r" % (mode, st))
                if want == "error":
                    self.assertIn("a sentence this kernel has never seen", st["err"],
                                  "an unfamiliar failure is shown in the CLI's words, not waited out")

    def test_a_cli_that_quits_before_the_link_says_why(self):
        # the old reader called any PTY end a timeout ("the login flow timed out") at once; the CLI's reason is the news
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "refuse"}):
            self.assertEqual(km._login_start(), "")
        st = _wait_state("error", seconds=5)
        self.assertEqual(st["state"], "error", st)
        self.assertIn("Cloud gateway", st["err"])
        self.assertNotIn("timed out", st["err"])

    def test_finished_states_do_not_decay_into_a_timeout(self):
        self.assertEqual(km._login_start(), "")
        _wait_state("url")
        self.assertEqual(km._login_code("SYNTH-BAD-CODE#SYNTH-STATE"), "")
        before = _wait_state("error", seconds=5)
        with km._login_lock:
            km._login_flow["t"] = time.time() - km.LOGIN_FLOW_TIMEOUT_S - 1
        self.assertEqual(km._login_state(), before, "a refusal keeps its reason past the flow clock")
        with km._login_lock:
            km._login_flow.update(state="done", err="", t=time.time() - km.LOGIN_FLOW_TIMEOUT_S - 1)
        self.assertEqual(km._login_state()["state"], "done", "a completed login never turns into a timeout")

    def test_url_waits_for_the_rest_of_a_read(self):
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "chunked"}):
            self.assertEqual(km._login_start(), "")
        self.assertEqual(_wait_state("url")["url"], _URL)

    def test_an_osc_8_link_is_read(self):
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "osc"}):
            self.assertEqual(km._login_start(), "")
        self.assertEqual(_wait_state("url")["url"], _URL)

    def test_wrapped_hyperlink_preserves_every_oauth_parameter(self):
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "wrapped"}):
            self.assertEqual(km._login_start(), "")
        self.assertEqual(_wait_state("url")["url"], _URL)

    def test_claude_ai_authorize_url_is_supported(self):
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "ai"}):
            self.assertEqual(km._login_start(), "")
        self.assertEqual(_wait_state("url", seconds=3)["url"],
                         _URL.replace("claude.com/cai/", "claude.ai/"))

    def test_one_flow_at_a_time_and_cancel(self):
        self.assertEqual(km._login_start(), "")
        _wait_state("url")
        self.assertNotEqual(km._login_start(), "", "a second start refuses while one runs")
        km._login_cancel()
        self.assertEqual(km._login_state()["state"], "", "cancel clears the flow")
        self.assertEqual(km._login_start(), "", "…and a fresh start is allowed after")
        km._login_cancel()

    def test_cancel_then_immediate_restart_reaches_the_url_again(self):
        # the fd race (review find on #931, 2026-09-07): cancel used to close the PTY master while the old
        # reader was parked in select on that fd number; the next start reused the number and the old
        # thread's os.read swallowed the new flow's first output. The reader owns the pipes; a restart right
        # after a cancel lands.
        for _ in range(3):
            self.assertEqual(km._login_start(), "")
            st = _wait_state("url")
            self.assertEqual(st["state"], "url", "a fresh flow reaches its URL after a cancel: %r" % st)
            km._login_cancel()

    def test_a_paste_prompt_with_no_readable_link_fails_loudly_at_once(self):
        # the CLI is already asking for the code, so it HAS printed its link — and _login_url found none it
        # will stand behind (allowlist rejected it / the shape moved). Waiting on would only report a
        # timeout ten minutes later; the event is the prompt, so the flow errors there (review find on
        # #931, 2026-09-07).
        with mock.patch.dict(os.environ, {"ROMP_LOGIN_TEST_OUTPUT": "badlink"}):
            self.assertEqual(km._login_start(), "")
        st = _wait_state("error", seconds=5)
        self.assertEqual(st["state"], "error", "no waiting for the timeout: %r" % st)
        self.assertIn("could not be read", st["err"])

    def test_code_without_a_flow_refuses(self):
        self.assertNotEqual(km._login_code("SYNTH-ORPHAN"), "")

    def test_stale_flow_self_reaps_via_state_read(self):
        self.assertEqual(km._login_start(), "")
        _wait_state("url")
        with km._login_lock:
            km._login_flow["t"] = time.time() - km.LOGIN_FLOW_TIMEOUT_S - 1
        st = km._login_state()
        self.assertEqual(st["state"], "error")
        self.assertIn("timed out", st["err"])

    def test_ops_payload_and_gear_wiring(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        for pin in ('msg.get("type") == "loginStart"', 'msg.get("type") == "loginCode"',
                    'msg.get("type") == "loginCancel"', '"login": _login_state(),',
                    '"acctLabel": _claude_account_label(),'):
            self.assertIn(pin, src)
        gear = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "gear.js")).read()
        for pin in ("loginStart", "loginCode", "rs-login-input", "rs-login-url", "v.acctLabel"):
            self.assertIn(pin, gear)
        feed = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "feed.ts")).read()
        self.assertIn('loginBtn.style.display = (showApiErr && authErr) ? "" : "none";', feed,
                      "the auth-expired card offers the fix, not just the problem")

    def test_the_dialog_narrates_every_wait_and_the_clis_complaint(self):
        # the dialog showed only its title and Cancel while Claude Code started and while the code was checked, and a
        # refusal or a complaint appeared only on the settings row behind it
        gear = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "gear.js")).read()
        self.assertIn("id=rs-login-status", gear, "the dialog has a status line of its own")
        self.assertIn("Starting Claude Code", gear)
        self.assertIn("Checking the code", gear)
        self.assertIn("lgStatus(", gear)


if __name__ == "__main__":
    unittest.main()
