"""`romp update` and `romp version` talk to this romp's kernel on the port every other `romp` command uses
(ROMP_KERNEL_PORT, then ROMP_SERVE_PORT, else 29855), and to no other port.

Both used to try 29855, 7878 and 7432 in turn and take the first that answered the unauthenticated
GET /version. The documented reason to set ROMP_KERNEL_PORT is that something else holds 29855, so with this
romp's kernel on 29900 and another listener on 29855 (another account's romp, or any server answering
/version), `romp update` sent this romp's serve token to that listener with GET /tunnels?fresh=1 and pushed to
whatever remotes it listed, and `romp version` reported the other kernel's sha and pid as this romp's, or
"not reachable" while this romp's kernel was up on 29900. SYNTHETIC ports, token and host; urllib's urlopen is
replaced by a recorder, so nothing connects."""
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads (tests/test_state_isolation_order.py's ratchet): romp modules resolve
# their state root at import time, and only pytest runs conftest's floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ru = load_source("romp_update_kernel_port", os.path.join(BIN, "romp-update"))
ver = load_source("romp_version_kernel_port", os.path.join(BIN, "romp-version"))

TOKEN = "synthetic-serve-token"
OWN = 29900                       # this romp's kernel, moved off the default port
OTHER_PORTS = (29855, 7878, 7432)  # the ports the old probe list tried

OTHER = {"/version": {"kernel_sha": "other00", "pid": 111, "uptime_s": 5, "dist_ver": 1, "bundles": {}},
         "/healthz": b"ok",
         "/tunnels": {"tunnels": [{"host": "TESTHOST", "outOfDate": True}]},
         "/tunnels/update": {"ok": True, "detail": "updated by the other kernel"}}
MINE = {"/version": {"kernel_sha": "own0000", "pid": 222, "uptime_s": 5, "dist_ver": 1, "bundles": {}},
        "/healthz": b"ok",
        "/tunnels": {"tunnels": []}}


class _Resp:
    status = 200

    def __init__(self, body):
        self._b = body

    def read(self, n=-1):
        return self._b if n is None or n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_net(listening):
    """listening = {port: {path: body}}. A port not listed refuses the connection; a path a listener does not
    serve answers 401. Returns (urlopen, calls), calls being (port, path, X-Romp-Token or None) per request."""
    calls = []

    def urlopen(req, data=None, timeout=None, **kw):
        if isinstance(req, urllib.request.Request):
            url, tok = req.full_url, req.get_header("X-romp-token")
        else:
            url, tok = req, None
        p = urllib.parse.urlsplit(url)
        calls.append((p.port, p.path, tok))
        routes = listening.get(p.port)
        if routes is None:
            raise urllib.error.URLError("connection refused")
        if p.path not in routes:
            raise urllib.error.HTTPError(url, 401, "unauthorized", {}, io.BytesIO(b"{}"))
        body = routes[p.path]
        return _Resp(body if isinstance(body, bytes) else json.dumps(body).encode())

    return urlopen, calls


def _env(**ports):
    """The environment for one case: the synthetic token, and exactly the port variables given (the suite's
    dead-port floor removed, so an unset variable is really unset)."""
    env = {k: v for k, v in os.environ.items() if k not in ("ROMP_KERNEL_PORT", "ROMP_SERVE_PORT")}
    env["ROMP_SERVE_TOKEN"] = TOKEN
    env.update({k: str(v) for k, v in ports.items()})
    return mock.patch.dict(os.environ, env, clear=True)


def _others():
    """Another listener answering on every port the old probe list tried."""
    return {p: OTHER for p in OTHER_PORTS}


class UpdateUsesTheConfiguredKernelPort(unittest.TestCase):
    def _update(self, net, **ports):
        urlopen, calls = fake_net(net)
        out, err = io.StringIO(), io.StringIO()
        with _env(**ports), mock.patch("urllib.request.urlopen", urlopen), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            base = ru._kernel()
            rc = ru.main([])
        return base, rc, out.getvalue(), err.getvalue(), calls

    def test_the_token_goes_only_to_the_kernel_on_ROMP_KERNEL_PORT(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual(base, "http://127.0.0.1:%d" % OWN)
        self.assertEqual((rc, err), (0, ""), out + err)
        self.assertIn("no remotes attached", out, "this romp's own remotes were listed")
        self.assertEqual({p for p, _, tok in calls if tok}, {OWN}, "the serve token went to another port: %r" % calls)
        self.assertEqual({p for p, _, _ in calls}, {OWN}, "no other port is even asked: %r" % calls)

    def test_ROMP_SERVE_PORT_is_the_same_port_by_its_other_name(self):
        base, rc, out, err, calls = self._update({**_others(), OWN: MINE}, ROMP_SERVE_PORT=OWN)
        self.assertEqual(base, "http://127.0.0.1:%d" % OWN)
        self.assertEqual(rc, 0, out + err)
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_a_down_kernel_is_reported_and_no_other_listener_takes_its_place(self):
        # this romp's kernel is down; every port the old list tried answers. Nothing may be listed or pushed
        # through another listener, and the refusal names the port it asked
        base, rc, out, err, calls = self._update(_others(), ROMP_KERNEL_PORT=OWN)
        self.assertIsNone(base)
        self.assertEqual(rc, 2)
        self.assertNotIn("updating", out, "nothing is pushed through another kernel")
        self.assertIn("127.0.0.1:%d" % OWN, err, "the refusal names the port it asked")
        self.assertEqual([c for c in calls if c[2]], [], "no request carried the serve token")
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_with_no_port_variable_the_default_port_is_used(self):
        base, rc, out, err, calls = self._update({29855: MINE})
        self.assertEqual(base, "http://127.0.0.1:29855")
        self.assertEqual(rc, 0, out + err)
        self.assertIn("no remotes attached", out)


class VersionReportsTheConfiguredKernel(unittest.TestCase):
    def _probe(self, net, **ports):
        urlopen, calls = fake_net(net)
        with _env(**ports), mock.patch("urllib.request.urlopen", urlopen):
            return ver._probe_kernel(), calls

    def test_the_kernel_on_ROMP_KERNEL_PORT_is_reported_not_the_default_ports(self):
        (kind, k), calls = self._probe({**_others(), OWN: MINE}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual(kind, "version")
        self.assertEqual((k.get("kernel_sha"), k.get("pid"), k.get("url")),
                         ("own0000", 222, "http://127.0.0.1:%d" % OWN))

    def test_the_kernel_is_found_when_nothing_answers_on_the_default_port(self):
        (kind, k), calls = self._probe({OWN: MINE}, ROMP_SERVE_PORT=OWN)
        self.assertEqual((kind, k.get("kernel_sha")), ("version", "own0000"))

    def test_a_kernel_predating_version_on_the_configured_port_is_stale(self):
        (kind, k), calls = self._probe({**_others(), OWN: {"/healthz": b"ok"}}, ROMP_KERNEL_PORT=OWN)
        self.assertEqual((kind, k), ("stale", "http://127.0.0.1:%d" % OWN))

    def test_a_down_kernel_is_down_whatever_else_answers(self):
        urlopen, calls = fake_net(_others())
        with _env(ROMP_KERNEL_PORT=OWN), mock.patch("urllib.request.urlopen", urlopen), \
                mock.patch.object(ver, "_git_sha", lambda: "abc1234"), \
                mock.patch.object(ver, "_disk_bundles", lambda: {}):
            kind, k = ver._probe_kernel()
            out = ver.report()
        self.assertEqual((kind, k), ("down", None))
        self.assertNotIn("other00", out, "another kernel's sha is never reported as this romp's")
        self.assertIn("not reachable at http://127.0.0.1:%d" % OWN, out, "the report names the port it asked")
        self.assertEqual({p for p, _, _ in calls}, {OWN}, calls)

    def test_with_no_port_variable_the_default_port_is_used(self):
        (kind, k), calls = self._probe({29855: MINE})
        self.assertEqual((kind, k.get("url")), ("version", "http://127.0.0.1:29855"))


if __name__ == "__main__":
    unittest.main()
