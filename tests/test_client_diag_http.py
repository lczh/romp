#!/usr/bin/env python3
"""A page with no open socket can still report, and the kernel records each socket's life (2026-10-15).

Two gaps made a stuck browser unreadable from the kernel's files. Every client-diag row travelled over a websocket, so a
page that could not open one was silent by design; and the kernel recorded the sockets it accepted (`wsopen`) and nothing
else, so a browser that stopped reaching the kernel and a browser that stopped trying left the same record.

The HTTP door: POST /client-diag files a page's clientDiag rows through the same builder as the socket's rows
(_client_diag_rec), marked `via: http`, behind the gate every POST passes (the page's cookie with its own origin, or the
token), and bounded by its body (64 KiB, checked against Content-Length before a byte is read), its rows (25 a request)
and the rows and bytes a minute across every page (past them a 429, and the refused rows counted in one row once the next
window admits a request). The page holds a request to 16,000 characters of rows; a row that does not fit waits. The pane shim's side runs in ui/webview/pane-shim-stale.test.ts; the shell's side runs here, the real
landing script under node: once no shell socket has been open for 20 s, a close sends the queued rows and a `nosocket` row,
at most once a minute, and a filed row never rides the socket too.

The socket-life rows: one `wsend` per accepted socket (the twin of its wsopen row by cid: the cause, close with its code,
eof, error or kernel with the kernel's reason, the socket's age, the messages the page sent and how soon the first came),
and a `wsrefused` row for a dial turned away (the gate's 403, a missing upgrade key, a relay to no attached host) or a
`wsupgradefail` row for a 101 that could not be written, at most 30 a minute, the rest counted onto the next row, never
carrying the query string. A static request files nothing.

Synthetic only: ephemeral loopback servers over the run's own state root, invented dashboard ids, TESTHOST.
"""
import base64
import http.client
import json
import os
import pathlib
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads: they resolve their state root at import time (tests/test_state_isolation_order.py).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_client_diag_http", os.path.join(BIN, "romp-kernel"))


class _Clock:
    """A hand-advanced clock for a _RowBudget."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _State(unittest.TestCase):
    """A private state root per test, and fresh budgets (km.jd.STATE and the budgets are shared by every test here)."""
    def setUp(self):
        self._saved = (km.jd.STATE, km._CLIENT_DIAG_POST_BUDGET, km._CONN_ROW_BUDGET)
        # a handler thread may still be finishing a socket's teardown (its focus record, its drops) as a test ends, and
        # write there while the directory goes: the run's temp root is removed whole at exit, so a straggler is harmless
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        km.jd._rebind_state(pathlib.Path(self._td.name))
        self.fp = km.jd.STATE / "client-diag.jsonl"
        km._CLIENT_DIAG_POST_BUDGET = km._RowBudget(km.CLIENT_DIAG_POST_ROWS_PER_MIN)
        km._CONN_ROW_BUDGET = km._RowBudget(km.CONN_ROWS_PER_MIN)

    def tearDown(self):
        km.jd._rebind_state(self._saved[0])
        km._CLIENT_DIAG_POST_BUDGET, km._CONN_ROW_BUDGET = self._saved[1], self._saved[2]
        self._td.cleanup()

    def rows(self, what=None):
        if not self.fp.exists():
            return []
        out = [json.loads(ln) for ln in self.fp.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return out if what is None else [r for r in out if r["what"] == what]

    def wait_rows(self, what, pred=lambda r: True, n=1, deadline_s=5.0):
        end = time.time() + deadline_s
        while True:
            got = [r for r in self.rows(what) if pred(r)]
            if len(got) >= n or time.time() >= end:
                return got
            time.sleep(0.05)


class _Served(_State):
    """One hermetic kernel server for the class, on the loopback."""
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.origin = "http://127.0.0.1:%d" % cls.port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def page(self):
        """The credentials a dashboard page's own request carries: its cookie, and its own origin."""
        return {"Cookie": "romp_token=" + km.TOKEN, "Origin": self.origin}

    def post(self, body, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            data = raw if raw is not None else json.dumps(body).encode()
            c.request("POST", "/client-diag", body=data, headers=dict({"Content-Type": "text/plain;charset=UTF-8"}, **(headers or {})))
            r = c.getresponse()
            text = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(text)
            except ValueError:
                return r.status, text
        finally:
            c.close()

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request("GET", path, headers=headers or {})
            r = c.getresponse()
            r.read()
            return r.status
        finally:
            c.close()

    def upgrade(self, path, headers=None, key=True):
        """One raw upgrade request, the socket kept open -> (status, socket)."""
        lines = ["GET %s HTTP/1.1" % path, "Host: 127.0.0.1:%d" % self.port, "Upgrade: websocket", "Connection: Upgrade",
                 "Sec-WebSocket-Version: 13"]
        if key:
            lines.append("Sec-WebSocket-Key: %s" % base64.b64encode(os.urandom(16)).decode())
        for k, v in (headers or {}).items():
            lines.append("%s: %s" % (k, v))
        s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        s.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        parts = buf.split(b"\r\n", 1)[0].decode("latin-1").split(" ", 2)
        return (int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else -1), s


def _drain_close(s):
    """Close a dial's socket after reading what the server sent: unread bytes would make the close a reset, which the
    server's next read reports as a traceback (noise, not a finding)."""
    try:
        s.shutdown(socket.SHUT_WR)
        s.settimeout(2)
        while s.recv(65536):
            pass
    except OSError:
        pass
    finally:
        s.close()


def _frame(opcode, payload):
    """One masked client frame (payload under 126 bytes)."""
    mask = os.urandom(4)
    return bytes([0x80 | opcode, 0x80 | len(payload)]) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


class _RfileSpy:
    """A request body that records every read (tests/test_kernel_auth_hardening.py's shape)."""
    def __init__(self, data=b""):
        self.data, self.reads = data, []

    def read(self, n=-1):
        self.reads.append(n)
        out, self.data = self.data[:n], self.data[n:]
        return out


def _serve_post(path, headers, rfile):
    """Drive the real do_POST over a fake socket -> (status, response headers, handler)."""
    import io
    h = km.Handler.__new__(km.Handler)
    h.client_address = ("127.0.0.1", 0)
    h.headers, h.path, h.command, h.request_version = headers, path, "POST", "HTTP/1.1"
    h.wfile, h.rfile, h.close_connection = io.BytesIO(), rfile, False
    captured = {"headers": {}}
    h.send_response = lambda code, *a: captured.__setitem__("status", code)
    h.send_header = lambda k, v: captured["headers"].__setitem__(k, v)
    h.end_headers = lambda: None
    h.do_POST()
    return captured.get("status"), captured["headers"], h


# a marker for a query value that must never reach the file, assembled at run time so no credential-shaped literal sits in
# the source for the secret scanner to read (CLAUDE.md, the gitleaks section)
QMARK = "q" * 8 + "-query-marker"

MSG = {"type": "clientDiag", "surface": "pane-shim", "what": "probe", "data": {"i": 1, "nested": {"a": [1, 2]}}, "build": 77, "boot": "1.1"}


class TheHttpDoor(_Served):
    def test_a_row_lands_exactly_as_the_socket_files_it_marked_http(self):
        st, out = self.post({"wid": "W-http", "rows": [MSG, dict(MSG, what="nosocket", data={"downMs": 20000})]}, self.page())
        self.assertEqual((st, out), (200, {"ok": True, "filed": 2}))
        got = self.rows()
        self.assertEqual([r["what"] for r in got], ["probe", "nosocket"], "in the order sent")
        km.Handler._dispatch_ws(None, MSG, {"wid": "W-http"})            # the same message through the socket's real dispatch
        by_socket = self.rows()[-1]
        by_http = dict(got[0])
        self.assertEqual(by_http.pop("via"), "http")
        by_http.pop("t"), by_socket.pop("t")
        self.assertEqual(by_http, by_socket, "one builder: the same row whichever door carried it, but for `via`")
        self.assertIs(by_http["reconnect"], False, "no socket carried it, so no socket declared a redial")

    def test_the_gate_is_the_one_every_post_passes(self):
        for label, headers in (("no credential", {}),
                               ("the cookie from another site", {"Cookie": "romp_token=" + km.TOKEN, "Origin": "http://elsewhere.example"}),
                               ("a wrong token", {"X-Romp-Token": "not-the-token"})):
            with self.subTest(label):
                st, _ = self.post({"wid": "W-gate", "rows": [MSG]}, headers)
                self.assertEqual(st, 403)
        self.assertEqual(self.rows(), [], "nothing filed for a request the gate refused")
        self.assertEqual(self.post({"wid": "W-gate", "rows": [MSG]}, self.page())[0], 200, "the page's own cookie and origin")
        self.assertEqual(self.post({"wid": "W-gate", "rows": [MSG]}, {"X-Romp-Token": km.TOKEN})[0], 200, "the token, as a CLI sends it")
        self.assertEqual(len(self.rows("probe")), 2)

    def test_the_body_and_the_rows_are_bounded_and_a_bad_body_files_nothing(self):
        spy = _RfileSpy(b"x" * (km.CLIENT_DIAG_POST_MAX_BYTES + 1))
        st, hdrs, h = _serve_post("/client-diag", {"X-Romp-Token": km.TOKEN, "Content-Length": str(km.CLIENT_DIAG_POST_MAX_BYTES + 1)}, spy)
        self.assertEqual(st, 413, "past the body cap")
        self.assertEqual(spy.reads, [], "refused on the declared length, before a byte is read")
        self.assertTrue(h.close_connection)
        self.assertEqual(hdrs.get("Connection"), "close", "the unread body cannot stay on a keep-alive socket")
        st, out = self.post({"wid": "W-many", "rows": [MSG] * (km.CLIENT_DIAG_POST_MAX_ROWS + 1)}, self.page())
        self.assertEqual(st, 413, out)
        for raw in (b"not json", b"[]", b'{"rows": "x"}', b"{}"):
            with self.subTest(raw=raw):
                self.assertEqual(self.post(None, self.page(), raw=raw)[0], 400)
        self.assertEqual(self.rows(), [], "nothing filed for a refused body")
        st, _, _ = _serve_post("/client-diag", {"X-Romp-Token": km.TOKEN, "Content-Length": str(km.CLIENT_DIAG_POST_MAX_BYTES)},
                               _RfileSpy(json.dumps({"wid": "W-cap", "rows": [MSG]}).encode().ljust(km.CLIENT_DIAG_POST_MAX_BYTES)))
        self.assertEqual(st, 200, "a body at the cap is read")
        st, out = self.post({"wid": "W-mixed", "rows": [{"type": "ready"}, "x", MSG]}, self.page())
        self.assertEqual((st, out), (200, {"ok": True, "filed": 1}), "a row that is not a clientDiag message is skipped")
        self.assertEqual([r["wid"] for r in self.rows()], ["W-cap", "W-mixed"])

    def test_past_the_minutes_budget_a_request_is_refused_and_its_rows_counted_into_the_next_window(self):
        clock = _Clock()
        km._CLIENT_DIAG_POST_BUDGET = km._RowBudget(3, clock=clock)
        self.assertEqual(self.post({"wid": "W-rate", "rows": [MSG, MSG]}, self.page())[0], 200)
        st, out = self.post({"wid": "W-rate", "rows": [MSG, MSG]}, self.page())
        self.assertEqual(st, 429, out)
        self.assertEqual(len(self.rows("probe")), 2, "the refused request filed nothing")
        clock.t += 61
        self.assertEqual(self.post({"wid": "W-rate", "rows": [MSG]}, self.page())[0], 200)
        got = self.rows()
        self.assertEqual([r["what"] for r in got], ["probe", "probe", "diagPostRefused", "probe"])
        self.assertEqual(got[2]["data"]["rows"], 2, "the refused rows, counted, filed once the window admits a request")
        self.assertEqual(got[2]["surface"], "kernel")

    def test_past_the_minutes_byte_budget_a_request_is_refused_too(self):
        self.assertEqual(self._saved[1].bytes_per_window, km.CLIENT_DIAG_POST_BYTES_PER_MIN, "the route's budget counts bytes")
        clock = _Clock()
        km._CLIENT_DIAG_POST_BUDGET = km._RowBudget(100, clock=clock, bytes_per_window=3000)
        big = dict(MSG, data={"pad": "x" * 1800})
        self.assertEqual(self.post({"wid": "W-bytes", "rows": [big]}, self.page())[0], 200)
        self.assertEqual(self.post({"wid": "W-bytes", "rows": [big]}, self.page())[0], 429, "two rows in the minute, but past its bytes")
        clock.t += 61
        self.assertEqual(self.post({"wid": "W-bytes", "rows": [MSG]}, self.page())[0], 200)
        self.assertEqual([r["what"] for r in self.rows()], ["probe", "diagPostRefused", "probe"])

    def test_the_route_sits_after_the_gate_in_the_source(self):
        import inspect
        post = inspect.getsource(km.Handler.do_POST)
        gate = "ok, self._set_cookie, why = self._authorize(q)"
        self.assertEqual(post.count(gate), 1)
        self.assertGreater(post.index('u.path == "/client-diag"'), post.index(gate))
        self.assertLess(post.index('u.path == "/client-diag"'), post.index("raw_body, berr = self._read_post_body()", post.index(gate)),
                        "its own smaller cap, before the general read")


class SocketLife(_Served):
    def _client(self, wid):
        end = time.time() + 5
        while time.time() < end:
            with km._clients_lock:
                for c in km._clients:
                    if c.get("wid") == wid:
                        return c
            time.sleep(0.05)
        self.fail("no client registered for %s" % wid)

    def _gone(self, wids, deadline_s=5.0):
        """Wait for the handlers of these dashboards' sockets to leave the client list (their teardown's late step)."""
        end = time.time() + deadline_s
        while time.time() < end:
            with km._clients_lock:
                if not any(c.get("wid") in wids for c in km._clients):
                    return
            time.sleep(0.05)

    def test_each_accepted_socket_ends_with_a_row_saying_how(self):
        q = "&token=" + km.TOKEN
        st, s1 = self.upgrade("/ws?app=feed&wid=W-close" + q, {"Origin": self.origin})
        self.assertEqual(st, 101)
        s1.sendall(_frame(0x8, struct.pack(">H", 1000)))                  # the page's own close
        st, s2 = self.upgrade("/ws?app=feed&wid=W-eof" + q, {"Origin": self.origin})
        self.assertEqual(st, 101)
        s2.sendall(_frame(0x1, json.dumps({"type": "clientDiag", "surface": "t", "what": "x", "data": {}}).encode()))
        self.wait_rows("x")
        _drain_close(s2)                                                   # the connection ends with no Close frame
        st, s3 = self.upgrade("/ws?app=feed&wid=W-kernel" + q, {"Origin": self.origin})
        self.assertEqual(st, 101)
        km._drop_dead_ws_client(self._client("W-kernel"), "a test drop")    # the kernel ends it
        ends = {r["wid"]: r for r in self.wait_rows("wsend", lambda r: r["wid"].startswith("W-"), n=3)}
        opens = {r["wid"]: r for r in self.rows("wsopen")}
        for s in (s1, s3):
            s.close()
        self._gone(("W-close", "W-eof", "W-kernel"))
        self.assertEqual(sorted(ends), ["W-close", "W-eof", "W-kernel"])
        self.assertEqual((ends["W-close"]["data"]["cause"], ends["W-close"]["data"]["code"]), ("close", 1000))
        self.assertEqual((ends["W-close"]["data"]["msgsIn"], ends["W-close"]["data"]["firstInMs"]), (0, None),
                         "a socket the page never spoke on says so")
        self.assertEqual(ends["W-eof"]["data"]["cause"], "eof")
        self.assertEqual(ends["W-eof"]["data"]["msgsIn"], 1)
        self.assertIsInstance(ends["W-eof"]["data"]["firstInMs"], int)
        self.assertEqual((ends["W-kernel"]["data"]["cause"], ends["W-kernel"]["data"]["why"]), ("kernel", "a test drop"),
                         "the kernel's reason, not the EOF its shutdown caused")
        for w, r in ends.items():
            self.assertEqual(r["surface"], "kernel")
            self.assertEqual(r["data"]["cid"], opens[w]["data"]["cid"], "the twin of its wsopen row by cid")
            self.assertEqual((r["data"]["app"], r["data"]["kind"]), ("feed", "page"))
            self.assertIsInstance(r["data"]["lifeMs"], int)

    def test_a_dial_turned_away_is_a_row_bounded_a_minute_and_never_the_query(self):
        clock = _Clock()
        km._CONN_ROW_BUDGET = km._RowBudget(2, clock=clock)
        st, s = self.upgrade("/ws?app=feed&wid=W-ref1&token=" + QMARK, {"Origin": self.origin})
        _drain_close(s)
        self.assertEqual(st, 403)
        st, s = self.upgrade("/ws?app=chat%3Cb%3E&wid=W-ref2%20%22said%20so%22%3Cscript%3E",   # a cross-site page's chosen strings
                             {"Cookie": "romp_token=" + km.TOKEN, "Origin": 'http://elsewhere.example "x" <y>'})
        _drain_close(s)
        self.assertEqual(st, 403)
        st, s = self.upgrade("/ws?app=chat&wid=W-ref3")                    # past the minute's two rows
        _drain_close(s)
        self.assertEqual(st, 403)
        clock.t += 61
        st, s = self.upgrade("/ws?app=timeline&wid=W-ref4&token=" + km.TOKEN, key=False)
        _drain_close(s)
        self.assertEqual(st, 400, "no upgrade key")
        got = self.wait_rows("wsrefused", n=3)
        self.assertEqual([(r["wid"], r["data"]["status"]) for r in got], [("W-ref1", 403), ("W-ref2saidsoscript", 403), ("W-ref4", 400)],
                         "a dial's dashboard id is cut to the characters a real one uses: no spaces, quotes or markup")
        self.assertIn("token required", got[0]["data"]["why"])
        self.assertEqual((got[1]["data"]["why"], got[1]["data"]["app"], got[1]["data"]["origin"]),
                         ("cross-site origin", "chatb", "http://elsewhere.examplexy"), "the app and the Origin, cut the same way")
        self.assertEqual((got[2]["data"]["why"], got[2]["data"]["key"], got[2]["data"]["unfiled"]), ("no upgrade key", False, 1),
                         "the dial the full minute refused, counted onto the next row")
        self.assertEqual([r["data"]["path"] for r in got], ["/ws"] * 3)
        self.assertNotIn("unfiled", got[0]["data"])
        self.assertNotIn(QMARK, self.fp.read_text(encoding="utf-8"), "never the query string")

    def test_a_relay_to_no_attached_host_is_a_refusal_naming_the_host(self):
        st, s = self.upgrade("/remote/NOHOST/ws?app=chat&wid=W-relay&token=" + km.TOKEN, {"Origin": self.origin})
        _drain_close(s)
        self.assertEqual(st, 404)
        st, s = self.upgrade("/remote/NO%20HOST%22%3Cb%3E/ws?app=chat&wid=W-relay2&token=" + km.TOKEN, {"Origin": self.origin})
        _drain_close(s)
        self.assertEqual(st, 404)
        got = self.wait_rows("wsrefused", n=2)
        self.assertEqual([(r["data"]["path"], r["data"]["host"], r["data"]["status"]) for r in got],
                         [("/remote/*/ws", "NOHOST", 404), ("/remote/*/ws", "NOHOSTb", 404)], "the host the path named, cut to a host's characters")

    def test_a_static_request_files_nothing(self):
        for path, headers in (("/healthz", {}), ("/version", {}), ("/", self.page()), ("/ws", {})):
            self.get(path, headers)
        self.assertEqual([r["what"] for r in self.rows()], ["wsrefused"], "only the socket path's refusal (a GET /ws with no credential)")

    def test_a_101_that_cannot_be_written_files_wsupgradefail_and_the_error_goes_on(self):
        class Gone:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ==", "Origin": "http://127.0.0.1:1"}
            path = "/ws?app=chat&wid=W-fail&token=" + QMARK

            def send_response(self, *a):
                pass

            def send_header(self, *a):
                pass

            def end_headers(self):
                raise BrokenPipeError("the page went away")
        with self.assertRaises(BrokenPipeError):
            km.Handler._ws(Gone())
        got = self.rows("wsupgradefail")
        self.assertEqual(len(got), 1)
        self.assertEqual((got[0]["wid"], got[0]["data"]["status"], got[0]["data"]["app"]), ("W-fail", 101, "chat"))
        self.assertIn("BrokenPipeError", got[0]["data"]["why"])
        self.assertNotIn(QMARK, self.fp.read_text(encoding="utf-8"))
        self.assertEqual(self.rows("wsopen"), [], "no socket was accepted")


class TheEndRowsRules(_State):
    def test_the_cause_the_row_names(self):
        c = {"app": "chat", "kind": "page", "cid": "c1", "wid": "W1"}
        km._note_ws_end(c, {"t0": 100.0, "in": 0, "first": None, "cause": "close", "code": 1001}, now=101.5)
        km._note_ws_end(dict(c, endWhy="a dead peer"), {"t0": 100.0, "in": 2, "first": 100.25, "cause": "close", "code": 1000}, now=101.0)
        km._note_ws_end(dict(c, endWhy="a dead peer"), {"t0": 100.0, "in": 0, "first": None, "cause": "eof"}, now=101.0)
        km._note_ws_end(c, {"t0": 100.0, "in": 0, "first": None, "cause": "error", "why": "ConnectionResetError"}, now=101.0)
        got = [r["data"] for r in self.rows("wsend")]
        self.assertEqual([(d["cause"], d.get("code"), d.get("why")) for d in got],
                         [("close", 1001, None), ("close", 1000, None), ("kernel", None, "a dead peer"), ("error", None, "ConnectionResetError")],
                         "the page's own Close frame wins; otherwise the kernel's reason over the EOF its shutdown caused")
        self.assertEqual((got[0]["lifeMs"], got[1]["firstInMs"], got[1]["msgsIn"]), (1500, 250, 2))

    def test_the_read_tells_a_close_frame_from_an_eof(self):
        import io
        seen = []
        out = km._ws_recv_message(io.BytesIO(_frame(0x8, struct.pack(">H", 1001) + b"bye")), lambda p: None, on_close=seen.append)
        self.assertEqual(out, (None, None))
        self.assertEqual(seen, [struct.pack(">H", 1001) + b"bye"])
        self.assertEqual(km._ws_close_code(seen[0]), 1001)
        self.assertIsNone(km._ws_close_code(b""), "a Close frame may carry no code")
        seen.clear()
        km._ws_recv_message(io.BytesIO(b""), lambda p: None, on_close=seen.append)
        self.assertEqual(seen, [], "a bare EOF is not a close")

    def test_a_row_that_cannot_be_written_never_fails_the_socket_and_is_said_once(self):
        import contextlib
        import io
        from unittest import mock
        km._ws_life_row_failed = False
        err = io.StringIO()
        try:
            with mock.patch.object(km, "_client_diag_append", mock.Mock(side_effect=OSError("disk full"))), contextlib.redirect_stderr(err):
                self.assertFalse(km._note_ws_end({"app": "chat"}, {"cause": "eof"}))
                self.assertFalse(km._note_ws_refused({}, "/ws", {}, 403, "x"))
        finally:
            km._ws_life_row_failed = False
        self.assertEqual(len([ln for ln in err.getvalue().splitlines() if ln.strip()]), 1, err.getvalue())


# The shell's HTTP door, run: the real landing script (_LANDING_MOBILE_JS) under node with the stubs the mobile script needs
# (tests/test_pane_state_broadcast.py's harness, trimmed), a hand-driven clock, a shell socket that opens and closes on cue,
# and a fetch that holds each request until the scenario answers it.
_SHELL_HARNESS = r"""
'use strict';
let NOW = 1000000; Date.now = () => NOW;
let MATCHES = true, TAB = null;
const STORE = {};
global.window = global;
window.__rompPaneSourceOk = () => true;
global.innerHeight = 844; global.innerWidth = 390; global.scrollY = 0;
global.scrollTo = () => {};
global.matchMedia = (q) => ({ get matches() { return MATCHES; }, query: q, addEventListener: () => {} });
global.requestAnimationFrame = () => 1;
global.addEventListener = () => {};
global.visualViewport = { height: 844, scale: 1, addEventListener: () => {} };
const WSS = [], TIMERS = [], FETCHES = [];
global.WebSocket = class {
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; this.ls = []; WSS.push(this); }
  addEventListener(t, f) { if (t === 'close') this.ls.push(f); }
  send(s) { this.sent.push(JSON.parse(s)); }
  open() { this.readyState = 1; this.onopen && this.onopen(); }
  shut() { this.readyState = 3; this.ls.forEach((f) => f({})); this.onclose && this.onclose({}); }   // listeners first, then onclose: the order they were added
};
Object.defineProperty(global, 'navigator', { configurable: true, value: {} });
global.__rompPaneEnabled = () => true;
global.encodeURIComponent = (s) => s;
global.location = { protocol: 'http:', host: 'TESTHOST:1' };
global.sessionStorage = { getItem: () => 'wid1' };
global.setTimeout = (f, ms) => { TIMERS.push({ f, ms }); return TIMERS.length; };
global.clearTimeout = () => {};
global.setInterval = () => 0;   // the shell socket's watchdog ticks on an interval: a live one would keep node from exiting
global.fetch = (url, init) => { const e = { url, init, ok: null, fail: null }; FETCHES.push(e); return { then(ok, fail) { e.ok = ok; e.fail = fail; } }; };
let RAN = 0;
function redial() { const t = TIMERS.slice(RAN); RAN = TIMERS.length; t.filter((x) => x.f && x.f.name === 'shellWS').forEach((x) => x.f()); }
const pane = (id) => ({ id, classList: { toggle() {} }, contentDocument: {}, contentWindow: { addEventListener: () => {} }, addEventListener: () => {} });
const PANES = {};
['f-chat', 'f-fleet', 'f-feed', 'f-files', 'f-timeline'].forEach((id) => { PANES[id] = pane(id); });
const BUTTONS = {};
const button = (key) => BUTTONS[key] || (BUTTONS[key] = { hidden: false, getAttribute: (a) => (a === 'data-pane' ? key : null), classList: { toggle() {} }, addEventListener: () => {} });
const BAR = { offsetHeight: 44, querySelectorAll: (sel) => (sel === 'button[data-pane]' ? [button('chat'), button('timeline'), button('fleet'), button('feed'), button('files')] : []) };
global.document = {
  visibilityState: 'visible',
  addEventListener: () => {},
  documentElement: { scrollTop: 0, style: { setProperty() {} } },
  body: { setAttribute: (a, v) => { if (a === 'data-tab') TAB = v; }, getAttribute: (a) => (a === 'data-tab' ? TAB : null) },
  getElementById: (id) => (id === 'mtabs' ? BAR : (PANES[id] || null)),
};
global.localStorage = { getItem: (k) => (k in STORE ? STORE[k] : null), setItem: (k, v) => { STORE[k] = v; } };
window.__rompPanesTell = () => {};
"""
_SHELL_DRIVER = r"""
const out = {}, sent = (ws) => ws.sent.map((m) => (m.type === 'clientDiag' ? m.what : m.type));
const req = (f) => (f ? { url: f.url, method: f.init.method, keepalive: f.init.keepalive, body: JSON.parse(f.init.body) } : null);
const answer = (i, r) => { if (FETCHES[i]) FETCHES[i].ok(r); };   // a request that was never made has nothing to answer
window.__rompShellDiag('deeplink', { via: 'test' });   // queued: the shell's first dial is still connecting
out.early = FETCHES.length;
NOW += 25000;
WSS[0].shut();                                          // the dial never opened, 25 s after the load
out.first = FETCHES.map(req);
answer(0, { ok: true });                                // filed
redial(); WSS[1].open();
out.flushed = sent(WSS[1]);
WSS[1].shut();                                          // a second outage; the socket had opened
window.__rompShellDiag('tap', { n: 2 });                // queued
NOW += 30000; redial(); WSS[2].shut();
out.throttled = FETCHES.length;                         // 30 s down, but under a minute since the last request
NOW += 31000; redial(); WSS[3].shut();
out.second = req(FETCHES[1]);
answer(1, { ok: false, status: 429 });                  // refused
redial(); WSS[4].open();
out.flushed2 = sent(WSS[4]);
WSS[4].shut();                                          // a third outage: a row too large for one request, and a small one
window.__rompShellDiag('big', { pad: 'x'.repeat(17000) });
window.__rompShellDiag('small', { n: 3 });
NOW += 61000; redial(); WSS[5].shut();
out.third = req(FETCHES[2]);
console.log(JSON.stringify(out));
"""


class TheShellsDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = shutil.which("node")
        if not node:
            raise unittest.SkipTest("node not installed")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "shell.js")
            with open(path, "w", encoding="utf-8") as f:
                f.write(_SHELL_HARNESS + km._LANDING_MOBILE_JS + _SHELL_DRIVER)
            r = subprocess.run([node, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "the shell script threw: " + r.stderr[:1200]
        cls.out = json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_shell_with_no_socket_for_20_s_sends_its_rows_over_http(self):
        self.assertEqual(self.out["early"], 0, "nothing before 20 s without a socket")
        self.assertEqual(len(self.out["first"]), 1)
        f = self.out["first"][0]
        self.assertEqual((f["url"], f["method"], f["keepalive"], f["body"]["wid"]), ("/client-diag", "POST", True, "wid1"))
        self.assertEqual([r["what"] for r in f["body"]["rows"]], ["deeplink", "nosocket"])
        ns = f["body"]["rows"][1]
        self.assertEqual((ns["type"], ns["surface"]), ("clientDiag", "shell"))
        self.assertEqual({k: ns["data"][k] for k in ("app", "downMs", "attempts", "hidden", "queued")},
                         {"app": "shell", "downMs": 25000, "attempts": 1, "hidden": False, "queued": 1})

    def test_a_filed_row_never_rides_the_socket_and_a_refused_one_does(self):
        self.assertEqual(self.out["flushed"], ["ready"], "the row the kernel filed over HTTP is not sent again")
        self.assertEqual(self.out["throttled"], 1, "at most one request a minute")
        second = self.out["second"]
        self.assertIsNotNone(second, "a second request, a minute after the first")
        self.assertEqual([r["what"] for r in second["body"]["rows"]], ["tap", "nosocket"])
        self.assertEqual((second["body"]["rows"][1]["data"]["downMs"], second["body"]["rows"][1]["data"]["attempts"]), (61000, 2),
                         "timed from the close of the socket that had opened; the two dials since that never opened")
        self.assertEqual(self.out["flushed2"], ["ready", "tap"], "a refused row waits for the socket")

    def test_a_row_too_large_for_one_request_waits_and_the_rows_that_fit_go(self):
        self.assertIsNotNone(self.out["third"])
        self.assertEqual([r["what"] for r in self.out["third"]["body"]["rows"]], ["small", "nosocket"],
                         "the body stays under the route's cap; the large row is left for the socket")


if __name__ == "__main__":
    unittest.main()
