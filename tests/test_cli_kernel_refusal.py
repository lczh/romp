#!/usr/bin/env python3
"""The CLI's POST verbs say the kernel's own reason when it refuses a request with HTTP 400, never "kernel not reachable".

`romp login add|remove|link|unlink`, `romp watch-pr`, `romp watch` and `romp card` used to POST with `curl -sf`. The
kernel answers its validation refusals with a 400 and the reason in a JSON body, and -f turns every non-2xx into curl's
exit 22 with the body thrown away, so the CLI printed "kernel not reachable on :<port> (is romp running?)". The person
was sent to restart a kernel that was up, and the reason was lost, including the warning to rotate a credential that a
--cmd carried in its own text. They now share bin/romp's _romp_post, which reads the status from curl's -w and says a
non-2xx JSON refusal the way the verb says the kernel's 200 refusals.

End to end: the REAL kernel Handler served on a loopback port, the real bin/romp, the real curl. The first test pins
that the kernel does answer these requests with a 400, so the CLI tests below exercise the 400 path; the controls show
that a 200 refusal and a good add still read as before. Synthetic only: the credential-shaped value is assembled at run
time (the secret scanner reads this file)."""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_cli_refusal", os.path.join(BIN, "romp-kernel"))

ROMP = os.path.join(BIN, "romp")
# a --cmd that carries a setup-token-shaped value in its own text: the case the kernel's shape rule refuses with the
# warning to rotate it
PASTED = "echo " + "".join(["sk", "-ant-", "oat01-", "A" * 12])
GOOD_CMD = "cat ~/.secrets/notes-api-token"


class CliSaysTheKernelsRefusal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def setUp(self):
        # a private state root for the stored logins the good add writes: km.jd.STATE is one root shared by every
        # module in a run, and a login left there would show up in another module's listing
        self.tmp = tempfile.mkdtemp()
        self._state = km.jd.STATE
        km.jd.STATE = Path(self.tmp) / "state"
        km.jd.STATE.mkdir()

    def tearDown(self):
        km.jd.STATE = self._state
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _romp(self, *args):
        # km.TOKEN, not os.environ: the token the handler checks is the one the kernel captured at import
        env = dict(os.environ, ROMP_KERNEL_PORT=str(self.port), ROMP_SERVE_PORT=str(self.port), ROMP_MANAGER_PORT="1",
                   ROMP_SERVE_TOKEN=km.TOKEN, HOME=self.tmp)
        env.pop("ROMP_SID", None)
        return subprocess.run(["bash", ROMP] + list(args), env=env, capture_output=True, text=True, timeout=60)

    def _post(self, path, body):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())

    def _refused(self, p, line):
        self.assertEqual(p.returncode, 1, p.stderr)
        self.assertNotIn("not reachable", p.stderr, "a kernel that answered 400 was reported as down")
        self.assertIn(line, p.stderr)

    def test_the_kernel_answers_each_of_these_requests_with_a_400_and_its_reason(self):
        for path, body, reason in (
                ("/logins", {"add": {"label": "Work", "tokenCmd": PASTED}}, "rotate it"),
                ("/logins", {"add": {"label": "L" * 81, "tokenCmd": GOOD_CMD}}, "add.label"),
                ("/watch-pr", {"pr": 12, "repo": "https://github.com/o/r", "name": "web"}, "repo (owner/name)"),
                ("/watch", {"cmd": " ", "name": "web"}, "cmd (the predicate"),
                ("/notice", {"key": "k1", "title": " "}, "key and title required")):
            st, r = self._post(path, body)
            self.assertEqual(st, 400, (path, r))
            self.assertFalse(r.get("ok"), (path, r))
            self.assertIn(reason, r.get("error") or "", (path, r))

    def test_login_add_with_a_credential_in_the_command_says_to_rotate_it(self):
        p = self._romp("login", "add", "Work", "--cmd", PASTED)
        self._refused(p, "romp login: refused: add.tokenCmd: the command text looks like it carries the credential itself")
        self.assertIn("rotate it", p.stderr)

    def test_login_add_with_an_overlong_label_says_the_label_rule(self):
        self._refused(self._romp("login", "add", "L" * 81, "--cmd", GOOD_CMD), "romp login: refused: add.label (1 to 80 characters")

    def test_watch_pr_with_a_url_for_a_repo_says_the_repo_rule(self):
        p = self._romp("watch-pr", "12", "--repo", "https://github.com/o/r", "--session", "web")
        self._refused(p, "romp watch-pr: refused — pr (number), repo (owner/name)")

    def test_watch_with_a_blank_predicate_says_the_kernels_rule(self):
        self._refused(self._romp("watch", "--cmd", " ", "--session", "web"), "romp watch: refused — cmd (the predicate")

    def test_card_with_a_blank_title_says_the_kernels_rule(self):
        self._refused(self._romp("card", " "), "romp card: refused — key and title required")

    def test_control_a_200_refusal_still_reads_as_a_refusal(self):
        self._refused(self._romp("login", "remove", "Nope"), "romp login: refused: no stored login named 'Nope'")

    def test_control_a_good_add_still_succeeds(self):
        p = self._romp("login", "add", "Good", "--cmd", GOOD_CMD)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("romp login: added 'Good'", p.stdout)


if __name__ == "__main__":
    unittest.main()
