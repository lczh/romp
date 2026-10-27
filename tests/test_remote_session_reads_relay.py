#!/usr/bin/env python3
"""GET /remote/<host>/mcp and /remote/<host>/commands: the MCP panel's read and the composer's "/" menu's read for a
session on an attached host, relayed to the kernel that runs it. The dashboard names such a session "<host>:<uuid>",
and this kernel owns no session by that id: before the relay, the panel asked this kernel's /mcp, which said no
running backend owned the session, and the menu asked this kernel's /commands, which listed this machine's commands.
Pinned here:
  - each route relays the peer's own JSON answer for the bare id, with the peer's own token and only `sid` crossing;
  - 404 for an unknown host, 502 and a redial for a dead tunnel, the peer's refusal mirrored in prose (an older
    build without the route), 502 naming the host for a body that is not a JSON object;
  - the routes sit behind the local auth gate.
The client half (render.ts builds these URLs through media.ts sessionReadUrl) is ui/webview/remote-session-reads.test.ts.
Synthetic throughout: TESTHOST, placeholder ids, the notes-api demo's api session."""
import json
import os
import socket
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
# the rail test's kernel load (hermetic XDG, ROMP_SERVE_TOKEN, NO_OPEN): one kernel module for these routes
from tests.test_api_health_rail import km  # noqa: E402

PEER_PASS = "peer-pass-DO-NOT-USE"
SID_PEER = "33333333-2222-3333-4444-555555555555"     # the peer's own id for its session "api"
MCP_DOC = {"servers": [{"name": "notes-db", "status": "connected", "tools": [{"name": "query"}]}], "error": ""}
CMDS_DOC = {"commands": [{"name": "deploy-notes-api", "description": "a project command on the peer"}],
            "warming": False}


class _FakePeer(BaseHTTPRequestHandler):
    """The attached host's kernel behind the tunnel: answers /mcp and /commands to its own token only."""
    answers = {}
    seen = []

    def do_GET(self):
        _FakePeer.seen.append((self.path, self.headers.get("X-Romp-Token")))
        if (self.headers.get("X-Romp-Token") or "") != PEER_PASS:
            self.send_response(403); self.end_headers(); self.wfile.write(b"bad token"); return
        route = self.path.split("?", 1)[0]
        if route not in _FakePeer.answers:
            payload = b"not found"
            self.send_response(404)
        else:
            ans = _FakePeer.answers[route]
            payload = ans if isinstance(ans, bytes) else json.dumps(ans).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a):
        pass


def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


class Relay(unittest.TestCase):

    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        _FakePeer.answers, _FakePeer.seen = {"/mcp": MCP_DOC, "/commands": CMDS_DOC}, []
        self.peer = ThreadingHTTPServer(("127.0.0.1", 0), _FakePeer)
        threading.Thread(target=self.peer.serve_forever, daemon=True).start()
        with km._remotes_lock:
            self._saved = dict(km._remotes)
            km._remotes.clear()
            km._remotes["TESTHOST"] = {"host": "TESTHOST", "kernel_port": 29855, "local_port": self.peer.server_address[1],
                                       "token": PEER_PASS, "status": "up", "sids": [SID_PEER], "trust": "directed"}
            km._remotes["DEADHOST"] = {"host": "DEADHOST", "kernel_port": 29855, "local_port": _free_port(),
                                       "token": PEER_PASS, "status": "up", "sids": [], "trust": "directed"}

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear(); km._remotes.update(self._saved)
        self.srv.shutdown(); self.srv.server_close()
        self.peer.shutdown(); self.peer.server_close()

    def _get(self, path, token=True):
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=15)
        c.request("GET", path, headers=({"X-Romp-Token": km.TOKEN} if token else {}))
        r = c.getresponse(); body = r.read(); ct = r.getheader("Content-Type") or ""; c.close()
        return r.status, body, ct

    def test_the_mcp_read_reaches_the_kernel_that_runs_the_session(self):
        st, body, ct = self._get("/remote/TESTHOST/mcp?sid=" + SID_PEER)
        self.assertEqual(st, 200, body[:200])
        self.assertTrue(ct.startswith("application/json"))
        self.assertEqual(json.loads(body), MCP_DOC, "the peer's servers, not this kernel's unowned-session answer")
        self.assertEqual(_FakePeer.seen, [("/mcp?sid=" + SID_PEER, PEER_PASS)],
                         "the peer saw its OWN token and the bare id, never the caller's credential")

    def test_the_commands_read_reaches_the_kernel_that_runs_the_session(self):
        st, body, ct = self._get("/remote/TESTHOST/commands?sid=" + SID_PEER)
        self.assertEqual(st, 200, body[:200])
        self.assertTrue(ct.startswith("application/json"))
        self.assertEqual(json.loads(body), CMDS_DOC, "the peer's list for its session, not this machine's")
        self.assertEqual(_FakePeer.seen, [("/commands?sid=" + SID_PEER, PEER_PASS)])

    def test_only_the_sid_crosses(self):
        st, body, _ = self._get("/remote/TESTHOST/mcp?sid=" + SID_PEER + "&path=notes.md&extra=1")
        self.assertEqual(st, 200, body[:200])
        self.assertEqual([p for p, _ in _FakePeer.seen], ["/mcp?sid=" + SID_PEER])

    def test_an_unknown_host_is_404_and_a_dead_tunnel_is_502_with_a_redial(self):
        st, body, _ = self._get("/remote/NOSUCHHOST/mcp?sid=" + SID_PEER)
        self.assertEqual(st, 404)
        self.assertIn(b"no attached host", body)
        with mock.patch.object(km, "_demand_redial") as redial:
            st, body, _ = self._get("/remote/DEADHOST/commands?sid=" + SID_PEER)
        self.assertEqual(st, 502)
        self.assertIn(b"not answering", body)
        self.assertEqual([c.args[0] for c in redial.call_args_list], ["DEADHOST"])

    def test_a_peer_without_the_route_is_mirrored_in_prose(self):
        _FakePeer.answers = {}
        st, body, ct = self._get("/remote/TESTHOST/mcp?sid=" + SID_PEER)
        self.assertEqual(st, 404)
        self.assertTrue(ct.startswith("text/plain"))
        self.assertEqual(body, b"not found")

    def test_a_body_that_is_not_a_json_object_is_a_502_naming_the_host(self):
        _FakePeer.answers = {"/commands": [1, 2], "/mcp": b"<html>not json</html>"}
        for route in ("/commands", "/mcp"):
            st, body, _ = self._get("/remote/TESTHOST%s?sid=%s" % (route, SID_PEER))
            self.assertEqual(st, 502, route)
            self.assertIn(b"TESTHOST", body)

    def test_the_relay_is_behind_the_local_auth_gate(self):
        for route in ("/mcp", "/commands"):
            st, _, _ = self._get("/remote/TESTHOST%s?sid=%s" % (route, SID_PEER), token=False)
            self.assertIn(st, (401, 403), route)
        self.assertEqual(_FakePeer.seen, [], "an unauthenticated read never reaches the peer")


if __name__ == "__main__":
    unittest.main()
