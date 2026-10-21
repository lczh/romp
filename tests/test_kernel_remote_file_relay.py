#!/usr/bin/env python3
"""The /remote/<host>/file relay — previews for a FEDERATED session's mentioned paths.

A remote session's chat mentions paths on the REMOTE machine's disk, but a preview <img> can only
dial the origin that served the page — its /file hit the local kernel, read the local disk, 404'd,
and every mentioned plot/screenshot on a federated session silently hid itself (2026-07-31). The
kernel now relays: GET/HEAD /remote/<host>/file forwards the one request to the attached host's
kernel through the same ssh -L tunnel the /remote/<host>/ws splice uses, after the normal local
auth gate, rewriting the remote kernel's own token into the forwarded query so the per-host trust
boundary is unchanged (THAT kernel still runs its allowlist, size cap and path resolution).

These tests run the real Handler against a fake "remote kernel" (an HTTP server that answers
/file and records what it was asked). Synthetic only: host name `gpu1`, invented tokens, no
session state touched.
"""
import os
import re
import socket
import threading
import unittest
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source
from tests.document_navigations import NAVIGATIONS, credential   # the navigations each opener-policy shape is asked under
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Mirror tests/test_kernel_ws_auth.py's load order. The token env keeps _load_token() away from
# the real state dir; NO_OPEN keeps the import from launching a browser.
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

REMOTE_TOKEN = "remote-token-DO-NOT-USE"
# The opener policy Handler._send declares (tests/test_kernel_auth_hardening.py OpenerIsolation holds the local routes to
# it), written out rather than read from the kernel, so a change to the value is a change to this test.
OPENER_POLICY = "same-origin-allow-popups"
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png-bytes"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"   # a minimal synthetic PDF
PY_BYTES = b"print('hello from the remote box')\n"
SVG_BYTES = (b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
             b'<script>document.title = "ran at the local origin"</script></svg>')   # synthetic
# the download fixture: NUL-ridden, off every view allowlist, and BIGGER than one relay stream chunk,
# so the pass-through provably crosses a chunk boundary intact
BIN_BYTES = bytes(range(256)) * ((km._DOWNLOAD_CHUNK // 256) + 60)
# the video fixture (2026-10-21): synthetic bytes, never decoded by anything here, and bigger than one stream chunk so
# the relay's streamed copy provably crosses a chunk boundary intact
VIDEO_BYTES = bytes(range(255, -1, -1)) * ((km._DOWNLOAD_CHUNK // 256) + 40)


class _FakeRemoteFileHandler(BaseHTTPRequestHandler):
    """The attached host's kernel behind the ssh -L port, /file route only: serves PNG_BYTES for
    path=/tmp/plot.png and BIN_BYTES for path=/tmp/data.bin&download=1 (recording the request
    lines), 404s anything else."""
    requests = []               # class-level: the recorded request lines
    ranges = []                 # …and the Range header each video request carried ("" for none)
    ctype = "image/png"         # what this remote CLAIMS the bytes are (a hostile one lies)
    dl_ctype = "application/octet-stream"          # …and the download-side claims (a hostile one lies)
    dl_disp = 'attachment; filename="data.bin"'
    dl_truncate = False         # short body then a clean close, as _file_download sends when the file shrank
    video_ctype = "video/mp4"   # what this remote CLAIMS a video is (a hostile one lies)
    video_crange = None         # a hostile remote's Content-Range on its 206, in place of the true one
    video_truncate = False      # half the promised body then a clean close, as the route does when the file shrank
    video_clen = None           # a hostile remote's Content-Length on its video reply, in place of the true one
    dl_clen = None              # …and on its download reply
    png_clen = None             # …and on its picture reply (the HEAD a preview probes with)
    dl_chunked = False          # a download reply framed two ways: Transfer-Encoding: chunked AND a (short) Content-Length

    def _opener_policy(self):
        # A real romp kernel answers through Handler._send, which sends Cross-Origin-Opener-Policy on every response it
        # builds: the GET 200 of a file, every refusal (404, 413), and since 2026-10-21 its streamed replies (a video's
        # 200, any 206). Its HEAD replies are written header by header and carry none. The relay sends its own copy
        # through its own _send, so a relay that also mirrored the remote's would send the header twice, and a browser
        # applies no policy from a doubled header.
        self.send_header("Cross-Origin-Opener-Policy", OPENER_POLICY)

    def _serve(self, head):
        _FakeRemoteFileHandler.requests.append(self.path)
        if "download=1" in self.path and "data.bin" in self.path and _FakeRemoteFileHandler.dl_chunked:
            # a hostile or broken remote: the whole file in chunks, under a length that claims a few bytes
            self.send_response(200)
            self.send_header("Content-Type", _FakeRemoteFileHandler.dl_ctype)
            self.send_header("Transfer-Encoding", "chunked")
            self.send_header("Content-Length", "10")
            self.end_headers()
            if not head:
                for at in range(0, len(BIN_BYTES), 4096):
                    part = BIN_BYTES[at:at + 4096]
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(part), part))
                self.wfile.write(b"0\r\n\r\n")
                self.close_connection = True
            return
        if "download=1" in self.path and "data.bin" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", _FakeRemoteFileHandler.dl_ctype)
            self.send_header("Content-Disposition", _FakeRemoteFileHandler.dl_disp)
            self.send_header("Content-Length", _FakeRemoteFileHandler.dl_clen or str(len(BIN_BYTES)))
            self.end_headers()
            if not head:
                if _FakeRemoteFileHandler.dl_truncate:
                    # the remote kernel's own truncation path: a short body, then a clean close
                    self.wfile.write(BIN_BYTES[: len(BIN_BYTES) // 2])
                    self.close_connection = True
                else:
                    self.wfile.write(BIN_BYTES)
            return
        if "clip.mp4" in self.path:
            # the remote's own video route, as _file_preview answers it: one byte range honoured (the same forms the
            # relay forwards), a 416 naming the size for one past the end, otherwise the whole file
            rng = self.headers.get("Range") or ""
            _FakeRemoteFileHandler.ranges.append(rng)
            n = len(VIDEO_BYTES)
            status, start, end = 200, 0, n - 1
            m = re.match(r"^bytes=(\d*)-(\d*)$", rng)
            if m and m.group(1):
                status, start = 206, int(m.group(1))
                end = min(int(m.group(2)), n - 1) if m.group(2) else n - 1
            elif m and m.group(2):
                status, start = 206, max(0, n - int(m.group(2)))
            if start >= n:
                body = b"range not satisfiable: the file is %d bytes" % n
                self.send_response(416)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Range", "bytes */%d" % n)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                return
            body = VIDEO_BYTES[start:end + 1]
            self.send_response(status)
            self.send_header("Content-Type", _FakeRemoteFileHandler.video_ctype)
            self.send_header("Content-Length", _FakeRemoteFileHandler.video_clen or str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            if status == 206:
                self.send_header("Content-Range", _FakeRemoteFileHandler.video_crange or "bytes %d-%d/%d" % (start, end, n))
            if not head:
                self._opener_policy()       # a real kernel's streamed replies go out through its _send
            self.end_headers()
            if not head:
                if _FakeRemoteFileHandler.video_truncate:
                    self.wfile.write(body[: len(body) // 2])
                    self.close_connection = True
                else:
                    self.wfile.write(body)
            return
        if "chart.svg" in self.path:
            # the remote's own .svg: whole, or the tail a suffix Range asks for (its _file_preview's 206 shape).
            # The remote CLAIMS image/svg+xml here; the relay derives its own type and policy regardless.
            rng = self.headers.get("Range") or ""
            start = int(rng[len("bytes="):-1]) if rng.startswith("bytes=") and rng.endswith("-") else 0
            body = SVG_BYTES[start:]
            self.send_response(206 if start else 200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Content-Length", str(len(body)))
            if start:
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, len(SVG_BYTES) - 1, len(SVG_BYTES)))
            elif not head:
                self._opener_policy()
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if "app.py" in self.path:
            self.send_response(200)
            self.send_header("Content-Type", _FakeRemoteFileHandler.ctype)
            self.send_header("Content-Length", str(len(PY_BYTES)))
            if not head:
                self._opener_policy()
            self.end_headers()
            if not head:
                self.wfile.write(PY_BYTES)
            return
        if "torn.png" in self.path:
            # a lying remote: a partial reply, asked for or not, whose Content-Range is not the bytes N-M/T the relay
            # mirrors, so the relay refuses it with its own 502
            self.send_response(206)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Range", "bytes 3-/99")
            self.send_header("Content-Length", str(len(PNG_BYTES)))
            self.end_headers()
            if not head:
                self.wfile.write(PNG_BYTES)
            return
        if "big.pdf" in self.path:
            # the remote's own size cap: its _file_preview's prose verdict, text/plain, no disposition
            body = b"too large to show: /tmp/big.pdf (95.4 MB, limit 47.7 MB)"
            self.send_response(413)
            self.send_header("Content-Type", "text/plain")
            self._opener_policy()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        if "paper.pdf" in self.path:
            # a (hostile) remote's own disposition — an instruction to this browser, which the relay must
            # never echo: ours is derived from the requested name
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", 'attachment; filename="evil.pdf"')
            self.send_header("Content-Length", str(len(PDF_BYTES)))
            if not head:
                self._opener_policy()
            self.end_headers()
            if not head:
                self.wfile.write(PDF_BYTES)
            return
        if "plot.png" not in self.path:
            body = b"not found: /tmp/gone" if "download=1" in self.path else b""
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self._opener_policy()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", _FakeRemoteFileHandler.ctype)
        self.send_header("Content-Length", _FakeRemoteFileHandler.png_clen or str(len(PNG_BYTES)))
        if not head:
            self._opener_policy()
        self.end_headers()
        if not head:
            self.wfile.write(PNG_BYTES)

    def do_GET(self):
        self._serve(head=False)

    def do_HEAD(self):
        self._serve(head=True)

    def log_message(self, *a):  # keep the test output clean
        pass


class _RecorderSink:
    def __init__(self, sink):
        self.sink = sink

    def write(self, b):
        self.sink.append(bytes(b))


class _RelayRecorder:
    """Just enough Handler surface for a direct _relay_download call: records status, headers and
    every wfile.write, and starts with close_connection False — so a test can see the one thing an
    over-HTTP client cannot: whether the relay itself decided to close the connection."""

    def __init__(self):
        self.writes, self.headers, self.status = [], {}, None
        self.close_connection = False
        self.wfile = _RecorderSink(self.writes)

    def send_response(self, code):
        self.status = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    _send = km.Handler._send      # the error paths route through the real _send, captured by the fakes
    _relay_video_body = km.Handler._relay_video_body   # a direct _remote_file call reaches the video arm through self


class RemoteFileRelay(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.fake = ThreadingHTTPServer(("127.0.0.1", 0), _FakeRemoteFileHandler)
        threading.Thread(target=self.fake.serve_forever, daemon=True).start()
        _FakeRemoteFileHandler.requests = []
        _FakeRemoteFileHandler.ctype = "image/png"
        _FakeRemoteFileHandler.dl_ctype = "application/octet-stream"
        _FakeRemoteFileHandler.dl_disp = 'attachment; filename="data.bin"'
        _FakeRemoteFileHandler.dl_truncate = False
        _FakeRemoteFileHandler.ranges = []
        _FakeRemoteFileHandler.video_ctype = "video/mp4"
        _FakeRemoteFileHandler.video_crange = None
        _FakeRemoteFileHandler.video_truncate = False
        _FakeRemoteFileHandler.video_clen = None
        _FakeRemoteFileHandler.dl_clen = None
        _FakeRemoteFileHandler.png_clen = None
        _FakeRemoteFileHandler.dl_chunked = False
        self._saved_remotes = dict(km._remotes)

    def tearDown(self):
        with km._remotes_lock:
            km._remotes.clear()
            km._remotes.update(self._saved_remotes)
        for s in (self.srv, self.fake):
            s.shutdown()
            s.server_close()

    def _register(self, host, local_port, token=REMOTE_TOKEN):
        with km._remotes_lock:
            km._remotes[host] = {"host": host, "kernel_port": 29855, "local_port": local_port,
                                 "token": token, "status": "up"}

    def _get(self, path, token=True, method="GET"):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method)
        if token:
            req.add_header("X-Romp-Token", km.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def test_relays_the_bytes_and_rewrites_the_token(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get(
            "/remote/gpu1/file?path=%2Ftmp%2Fplot.png&sid=11111111-2222-3333-4444-555555555555"
            "&token=whatever-the-browser-sent")
        self.assertEqual(status, 200)
        self.assertEqual(body, PNG_BYTES, "the remote's bytes must pass through unmodified")
        self.assertEqual(headers.get("Content-Type"), "image/png")
        # the forwarded request: path /file, the REMOTE's token (not what the browser sent, and
        # never the local serve token), the sid + path intact
        (req,) = _FakeRemoteFileHandler.requests
        self.assertTrue(req.startswith("/file?"), req)
        self.assertIn("token=" + REMOTE_TOKEN, req)
        self.assertNotIn("whatever-the-browser-sent", req)
        self.assertNotIn(km.TOKEN, req)
        self.assertIn("path=%2Ftmp%2Fplot.png", req)
        self.assertIn("sid=11111111-2222-3333-4444-555555555555", req)

    # A remote session's file opened in a tab of its own is a document on this origin too, so each shape the relay
    # answers through _send declares Cross-Origin-Opener-Policy: same-origin-allow-popups as the local /file route does
    # (tests/test_kernel_auth_hardening.py OpenerIsolation), so the dashboard's window.open still returns a handle. Read
    # off the wire, every copy: exactly one header, since a browser that receives two cannot parse it and applies no
    # policy (a dict of the headers keeps one copy and would hide the second). Each shape is asked once per entry of
    # NAVIGATIONS (tests/document_navigations.py): bare, and with the headers a browser sends on a navigation, typed,
    # opened by the dashboard and opened by another origin, since a browser reads the policy on a navigation only, and
    # each with that entry's credential (a navigation's romp_token cookie, a bare request's header: credential). The
    # fake remote sends its own copy on each of its replies, as a real romp kernel does
    # (_FakeRemoteFileHandler._opener_policy), so a relay that mirrored the remote's header beside its own fails here.
    # One test per shape, since each one's extra headers (the mirrored mtimes, the SVG sandbox, the PDF's name, the 413
    # page) come from its own code, and so do the relay's own refusals: a host that is not attached (detached), an
    # extension the relay will not preview (unviewable), a tunnel that does not answer (502), a remote reply longer than
    # the local cap (the backstop 413), and a partial reply whose Content-Range is not byte arithmetic (malformed 502).
    def _coop_each(self, path, want_status):
        """GET `path` once per NAVIGATIONS entry; assert the status and one COOP header, OPENER_POLICY, each time. Returns
        {entry: (body, header message)}."""
        seen = {}
        for how, nav in NAVIGATIONS:
            status, body, msg = self._get_msg(path, headers=dict(nav, **credential(nav, km.TOKEN)), token=False)
            self.assertEqual(status, want_status, "%s: %r" % (how, body[:80]))
            self.assertEqual(msg.get_all("Cross-Origin-Opener-Policy"), [OPENER_POLICY],
                             how + ": one Cross-Origin-Opener-Policy header on the wire, " + OPENER_POLICY)
            seen[how] = (body, msg)
        return seen

    def _relayed_coop(self, path, want_status):
        """_coop_each on the relay of `path` to an attached host, which is asked each time."""
        self._register("gpu1", self.fake.server_address[1])
        asked = len(_FakeRemoteFileHandler.requests)
        seen = self._coop_each("/remote/gpu1/file?path=" + urllib.parse.quote(path, safe=""), want_status)
        self.assertEqual(len(_FakeRemoteFileHandler.requests), asked + len(NAVIGATIONS), "the remote was asked each time")
        self.assertIn(path, _FakeRemoteFileHandler.requests[-1].replace("%2F", "/"))
        return seen

    def test_a_relayed_image_carries_the_openers_policy_like_a_local_one(self):
        for how, (body, _) in self._relayed_coop("/tmp/plot.png", 200).items():
            self.assertEqual(body, PNG_BYTES, how)

    def test_a_relayed_pdf_carries_the_openers_policy_once(self):
        for how, (body, msg) in self._relayed_coop("/tmp/paper.pdf", 200).items():
            self.assertEqual(body, PDF_BYTES, how)
            self.assertEqual(msg.get("Content-Disposition"), 'inline; filename="paper.pdf"', how)

    def test_a_relayed_svg_carries_the_openers_policy_once(self):
        for how, (body, msg) in self._relayed_coop("/tmp/chart.svg", 200).items():
            self.assertEqual(body, SVG_BYTES, how)
            self.assertIn("sandbox", msg.get_all("Content-Security-Policy"), how)

    def test_a_relayed_text_file_carries_the_openers_policy_once(self):
        for how, (body, _) in self._relayed_coop("/tmp/app.py", 200).items():
            self.assertEqual(body, PY_BYTES, how)

    def test_the_relays_oversize_pdf_page_carries_the_openers_policy_once(self):
        # a navigation gets the relay's own way-out page; a bare request the remote's plain refusal
        for how, (body, msg) in self._relayed_coop("/tmp/big.pdf", 413).items():
            if how == "a bare request":
                self.assertEqual(msg.get("Content-Type"), "text/plain", how)
            else:
                self.assertTrue(msg.get("Content-Type", "").startswith("text/html"), how + ": the way-out page, a document")
                self.assertIn(b"too large to show: /tmp/big.pdf", body, how)

    def test_a_relayed_refusal_carries_the_openers_policy_once(self):
        for how, (_, msg) in self._relayed_coop("/tmp/gone.png", 404).items():
            self.assertEqual(msg.get("Content-Type"), "text/plain", how)

    def test_the_relays_detached_refusal_carries_the_openers_policy_once(self):
        # no attached host by that name: the relay's own 404
        for how, (body, _) in self._coop_each("/remote/nosuch/file?path=%2Ftmp%2Fplot.png", 404).items():
            self.assertIn(b"no attached host", body, how)
        self.assertEqual(_FakeRemoteFileHandler.requests, [], "nothing was dialed")

    def test_the_relays_unviewable_refusal_carries_the_openers_policy_once(self):
        # an extension the relay will not preview: its own 404, before the remote is asked
        self._register("gpu1", self.fake.server_address[1])
        for how, (body, _) in self._coop_each("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin", 404).items():
            self.assertEqual(body, b"not found", how)
        self.assertEqual(_FakeRemoteFileHandler.requests, [], "the remote was not asked")

    def test_the_relays_backstop_413_carries_the_openers_policy_once(self):
        # the remote answered 200 with more bytes than the local cap (a stand-in for _PREVIEW_MAX_BYTES here, the remote's
        # own cap being what normally refuses first): the relay's own refusal, from its own _send
        from unittest import mock
        with mock.patch.object(km, "_PREVIEW_MAX_BYTES", 4):
            self.assertGreater(len(PNG_BYTES), 4)
            for how, (body, msg) in self._relayed_coop("/tmp/plot.png", 413).items():
                self.assertEqual(body, b"too large to preview", how)
                self.assertEqual(msg.get("Content-Type"), "text/plain", how)

    def test_the_relays_malformed_partial_reply_refusal_carries_the_openers_policy_once(self):
        # a 206 whose Content-Range is not byte arithmetic: the relay mirrors none of it and answers its own 502
        for how, (body, _) in self._relayed_coop("/tmp/torn.png", 502).items():
            self.assertEqual(body, b"malformed partial reply from gpu1", how)

    def test_the_relays_dead_tunnel_refusal_carries_the_openers_policy_once(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        for how, (body, _) in self._coop_each("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", 502).items():
            self.assertIn(b"is not answering", body, how)

    def test_a_remote_pdf_is_served_inline_with_its_own_name_derived_here_never_the_remotes(self):
        # a remote session's PDF opens in its own browser tab too (2026-09-06): the tab's title and a Save's
        # name ride Content-Disposition, which the local route sends — so the relay must as well, from the
        # REQUESTED basename, on GET and on the HEAD probe. The remote's header is discarded like its
        # Content-Type: an instruction to this browser, not a fact about its disk.
        self._register("gpu1", self.fake.server_address[1])
        for method in ("GET", "HEAD"):
            status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fpaper.pdf", method=method)
            self.assertEqual(status, 200, method)
            self.assertEqual(headers.get("Content-Type"), "application/pdf")
            self.assertEqual(headers.get("Content-Disposition"), 'inline; filename="paper.pdf"', method)
            self.assertNotIn("evil", headers.get("Content-Disposition") or "")
            self.assertEqual(body, PDF_BYTES if method == "GET" else b"")
        # a remote 404 for a .pdf name carries no disposition — there is no file to name
        status, _, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.pdf")
        self.assertEqual(status, 404)
        self.assertIsNone(headers.get("Content-Disposition"))
        # …and an image never gets one, exactly as locally
        status, _, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 200)
        self.assertIsNone(headers.get("Content-Disposition"))

    def test_a_remote_error_verdict_is_prose_and_an_oversize_pdf_tab_gets_the_relays_own_way_out(self):
        # a PDF opens in its own TAB now (2026-09-06): the remote's 413/404 prose used to come back labelled
        # with OUR media mime, which an <img> merely failed on but a tab shows as a corrupt-PDF error
        # (skeptic find). Every non-success verdict is text/plain; a navigation to an oversize PDF gets the
        # same way-out page the local route serves, linking THIS relay's download half.
        self._register("gpu1", self.fake.server_address[1])
        sid = "11111111-2222-3333-4444-555555555555"
        qp = "/remote/gpu1/file?path=%2Ftmp%2Fbig.pdf&sid=" + sid
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, qp),
                                     headers={"X-Romp-Token": km.TOKEN, "Sec-Fetch-Dest": "document"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, body, headers = r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            status, body, headers = e.code, e.read(), dict(e.headers)
        self.assertEqual(status, 413)
        self.assertTrue(headers.get("Content-Type", "").startswith("text/html"), headers.get("Content-Type"))
        page = body.decode("utf-8")
        self.assertIn("too large to show: /tmp/big.pdf", page)
        dq = urllib.parse.urlencode({"path": "/tmp/big.pdf", "download": "1", "sid": sid})
        self.assertIn('href="' + km._html_esc("/remote/gpu1/file?" + dq) + '"', page, "the relay's download half")
        # the same without the navigation marker: the remote's prose, labelled as prose
        status, body, headers = self._get(qp)
        self.assertEqual(status, 413)
        self.assertEqual(headers.get("Content-Type"), "text/plain")
        self.assertTrue(body.startswith(b"too large to show:"), body[:40])
        status, body, headers = self._get(qp, method="HEAD")
        self.assertEqual((status, body, headers.get("Content-Type")), (413, b"", "text/plain"))
        # a remote 404 for a .pdf name: prose too, never application/pdf
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.pdf")
        self.assertEqual((status, headers.get("Content-Type")), (404, "text/plain"))

    def test_head_relays_the_verdict_without_a_body(self):
        # the PDF chip's existence probe: headers only, the remote's real length
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Length"), str(len(PNG_BYTES)))
        (req,) = _FakeRemoteFileHandler.requests
        self.assertTrue(req.startswith("/file?"), req)

    def test_remote_404_passes_through(self):
        # a deleted/hallucinated path: the REMOTE's 404 reaches the <img> so it hides itself
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.png")
        self.assertEqual(status, 404)

    def test_text_relays_with_a_locally_derived_type_the_remote_cannot_override(self):
        """The text half of /file relays too — this gate predated it, so a remote session's
        .py/.md 404'd here while the LOCAL route served the same file (the viewer just failed
        on every federated text file). The defense is unchanged and now covers text: the type
        is OURS — a lying remote's text/html arrives as inert text/plain + nosniff. (.html is
        on _TEXT_EXT, so it too relays as text/plain now — same inertness, matching the local
        route; the never-asked decline for off-every-allowlist extensions is pinned by the
        .bin test below.)"""
        self._register("gpu1", self.fake.server_address[1])
        _FakeRemoteFileHandler.ctype = "text/html"
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fapp.py")
        self.assertEqual(status, 200)
        self.assertEqual(body, PY_BYTES)
        self.assertEqual(headers.get("Content-Type"), "text/plain; charset=utf-8")
        self.assertNotIn("html", (headers.get("Content-Type") or "").lower())

    def test_unknown_host_404s(self):
        status, _, _ = self._get("/remote/nosuch/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 404)
        self.assertEqual(_FakeRemoteFileHandler.requests, [])

    def test_unauthorized_403s_before_any_dial(self):
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", token=False)
        self.assertEqual(status, 403, "the local auth gate must run before the relay")
        self.assertEqual(_FakeRemoteFileHandler.requests, [],
                         "an unauthorized request must never touch the tunnel")

    # ── the download half (the user 2026-08-09): /remote/<host>/file?download=1 relays ANY file the
    # remote will serve, streamed through, with the attachment headers derived on THIS side ──

    def test_download_relays_any_extension_and_the_disposition_survives(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get(
            "/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1&token=whatever-the-browser-sent")
        self.assertEqual(status, 200)
        self.assertEqual(body, BIN_BYTES,
                         "bigger than one stream chunk, so the pass-through crossed a boundary intact")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')
        self.assertEqual(headers.get("Content-Type"), "application/octet-stream")
        self.assertEqual(headers.get("Content-Length"), str(len(BIN_BYTES)))
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        # forwarded intact: download=1 rides through, the REMOTE's token replaces the browser's
        (req,) = _FakeRemoteFileHandler.requests
        self.assertIn("download=1", req)
        self.assertIn("token=" + REMOTE_TOKEN, req)
        self.assertNotIn("whatever-the-browser-sent", req)

    def test_download_headers_are_derived_locally_never_mirrored_from_the_remote(self):
        # the lying-remote rule, download edition: an attached host serves its own bytes but never
        # chooses how this browser handles them — a hostile Content-Type/Disposition is discarded
        self._register("gpu1", self.fake.server_address[1])
        _FakeRemoteFileHandler.dl_ctype = "text/html"
        _FakeRemoteFileHandler.dl_disp = "inline"
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1")
        self.assertEqual(status, 200)
        self.assertEqual(body, BIN_BYTES)
        self.assertEqual(headers.get("Content-Type"), "application/octet-stream")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')

    def test_the_view_relay_still_declines_that_same_extension(self):
        # without download=1 nothing changed: a .bin is off _PREVIEW_MIME, 404'd HERE, remote unasked
        self._register("gpu1", self.fake.server_address[1])
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin")
        self.assertEqual(status, 404)
        self.assertEqual(_FakeRemoteFileHandler.requests, [])

    def test_a_remote_download_404_passes_through(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fgone.bin&download=1")
        self.assertEqual(status, 404)
        self.assertIn(b"not found", body, "the remote's own verdict, which names the path IT resolved")

    def test_head_download_relays_the_attachment_without_a_body(self):
        self._register("gpu1", self.fake.server_address[1])
        status, body, headers = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1",
                                          method="HEAD")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(headers.get("Content-Disposition"), 'attachment; filename="data.bin"')
        self.assertEqual(headers.get("Content-Length"), str(len(BIN_BYTES)))

    def test_a_remote_that_truncates_cleanly_still_closes_this_connection_short(self):
        """The remote's own _file_download truncation path sends SHORT and then closes cleanly — on
        this side http.client's read() answers that with b'' and NO exception, so only counting the
        copied bytes against the Content-Length already forwarded can spot the broken promise.
        Without the close, this keep-alive (HTTP/1.1) connection leaves the browser waiting on
        bytes that will never come — a hung download instead of a visibly failed one. Pinned by a
        direct _relay_download call: over HTTP a test client's own Connection: close (urllib sends
        it always) would close the connection for the wrong reason and hide exactly this bug."""
        _FakeRemoteFileHandler.dl_truncate = True
        rec = _RelayRecorder()
        km.Handler._relay_download(rec, "gpu1", self.fake.server_address[1], REMOTE_TOKEN,
                                   {"path": ["/tmp/data.bin"], "download": ["1"]})
        self.assertEqual(rec.status, 200)
        self.assertEqual(rec.headers.get("Content-Length"), str(len(BIN_BYTES)),
                         "the remote's promised length went out before the truncation")
        self.assertEqual(b"".join(rec.writes), BIN_BYTES[: len(BIN_BYTES) // 2],
                         "the bytes that did arrive still pass through")
        self.assertTrue(rec.close_connection,
                        "short of the forwarded Content-Length must CLOSE, or the browser hangs")

    def test_a_dead_tunnel_502s_the_download_too(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1")
        self.assertEqual(status, 502)

    def test_dead_tunnel_502s(self):
        # a registered host whose forwarded port has no listener (the ssh died mid-flight)
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        self._register("gpu1", dead_port)
        status, _, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 502)

    def _get_msg(self, path, method="GET", headers=None, token=True):
        # the raw header MESSAGE: a dict keeps one value per name, and the SVG sandbox policy rides beside
        # _send's frame-ancestors one under the same header name. `token`: present the X-Romp-Token header (a caller
        # that brings its own credential in `headers`, a navigation's cookie, passes False)
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), method=method,
                                     headers=headers or {})
        if token:
            req.add_header("X-Romp-Token", km.TOKEN)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers

    def test_a_remote_svg_is_a_sandboxed_document_by_the_relays_own_policy(self):
        # The relay derives every header that tells THIS browser how to interpret the bytes (the type, the
        # disposition) from the requested extension and discards the remote's, so the local route's SVG
        # defence (tests/test_kernel_preview.py SvgSandboxPolicy: a tab navigated to an SVG runs its inline
        # script at the serving origin) has to be restated here, from OUR mime — a remote session's .svg
        # opened in its own tab is a document at this kernel's origin, cookie and all (the 1204 review,
        # 2026-09-10). All three success shapes; a remote PNG carries none.
        self._register("gpu1", self.fake.server_address[1])
        csp = lambda msg: "; ".join(msg.get_all("Content-Security-Policy") or [])
        for method, headers, want in (("GET", None, 200), ("HEAD", None, 200), ("GET", {"Range": "bytes=1-"}, 206)):
            status, body, msg = self._get_msg("/remote/gpu1/file?path=%2Ftmp%2Fchart.svg", method=method, headers=headers)
            self.assertEqual(status, want, (method, headers))
            self.assertEqual(msg.get("Content-Type"), "image/svg+xml", (method, headers))
            self.assertIn("sandbox", msg.get_all("Content-Security-Policy") or [], (method, headers, csp(msg)))   # exact value: a weakened `sandbox allow-scripts` must fail
            self.assertEqual(body, b"" if method == "HEAD" else SVG_BYTES[1 if headers else 0:], (method, headers))
        status, _, msg = self._get_msg("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
        self.assertEqual(status, 200)
        self.assertNotIn("sandbox", csp(msg), csp(msg))

    # ── video (2026-10-21): a remote session's recording plays through the relay as a local one does. Every single
    # range the local route honours is forwarded (re-serialised, so byte arithmetic and nothing else reaches the
    # remote), the remote's 200/206 is STREAMED back rather than read whole (a recording has no size cap), and the
    # headers that tell this browser how to treat the bytes are ours: the type from the requested extension,
    # Accept-Ranges because this relay serves every range it forwards. ──

    VIDEO_PATH = "/remote/gpu1/file?path=%2Ftmp%2Fclip.mp4"

    def _video(self, rng=None, method="GET"):
        self._register("gpu1", self.fake.server_address[1])
        return self._get_msg(self.VIDEO_PATH, method=method, headers={"Range": rng} if rng is not None else None)

    def _relayed_partial(self, rng, start, end):
        status, body, msg = self._video(rng)
        n = len(VIDEO_BYTES)
        self.assertEqual(status, 206, rng)
        self.assertEqual(body, VIDEO_BYTES[start:end + 1], rng)
        self.assertEqual(msg.get("Content-Range"), "bytes %d-%d/%d" % (start, end, n), rng)
        self.assertEqual(msg.get("Content-Length"), str(end - start + 1), rng)
        self.assertEqual(msg.get("Content-Type"), "video/mp4", rng)
        self.assertEqual(msg.get("Accept-Ranges"), "bytes", rng)
        self.assertEqual(msg.get("X-Content-Type-Options"), "nosniff", rng)

    def test_a_remote_video_relays_whole_under_our_type_with_ranges_advertised(self):
        _FakeRemoteFileHandler.video_ctype = "text/html"           # a lying remote: its type never reaches the browser
        status, body, msg = self._video()
        self.assertEqual(status, 200)
        self.assertEqual(body, VIDEO_BYTES, "bigger than one stream chunk, so the copy crossed a boundary intact")
        self.assertEqual(msg.get("Content-Type"), "video/mp4")
        self.assertEqual(msg.get("Content-Length"), str(len(VIDEO_BYTES)))
        self.assertEqual(msg.get("Accept-Ranges"), "bytes")
        self.assertEqual(msg.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(_FakeRemoteFileHandler.ranges, [""], "no Range asked, none forwarded")

    def test_a_relayed_video_carries_the_openers_policy_once(self):
        for how, (body, msg) in self._relayed_coop("/tmp/clip.mp4", 200).items():
            self.assertEqual(body, VIDEO_BYTES, how)
            self.assertEqual(msg.get("Content-Type"), "video/mp4", how)

    def test_safaris_two_byte_probe_is_forwarded_and_its_partial_reply_mirrored(self):
        self._relayed_partial("bytes=0-1", 0, 1)
        self.assertEqual(_FakeRemoteFileHandler.ranges, ["bytes=0-1"])

    def test_every_single_range_form_is_forwarded(self):
        n = len(VIDEO_BYTES)
        self._relayed_partial("bytes=-100", n - 100, n - 1)          # the suffix: the last 100 bytes
        self._relayed_partial("bytes=5000-", 5000, n - 1)            # a seek
        self._relayed_partial("bytes=10-99", 10, 99)                 # closed
        self._relayed_partial("bytes=0-", 0, n - 1)
        self.assertEqual(_FakeRemoteFileHandler.ranges, ["bytes=-100", "bytes=5000-", "bytes=10-99", "bytes=0-"])

    def test_a_header_that_is_not_one_valid_range_is_dropped_never_forwarded(self):
        for rng in ("bytes=0-1,5-9", "bytes=9-5", "items=0-1", "bytes=0-1; evil=1"):
            status, body, msg = self._video(rng)
            self.assertEqual((status, body), (200, VIDEO_BYTES), rng)
        self.assertEqual(_FakeRemoteFileHandler.ranges, ["", "", "", ""], "the remote saw no Range at all")

    def test_a_remote_416_passes_through_with_its_size(self):
        status, _, msg = self._video("bytes=%d-" % (len(VIDEO_BYTES) + 5))
        self.assertEqual(status, 416)
        self.assertEqual(msg.get("Content-Range"), "bytes */%d" % len(VIDEO_BYTES))
        self.assertEqual(msg.get("Content-Type"), "text/plain", "the remote's prose, labelled as prose")

    def test_a_partial_reply_whose_range_is_not_byte_arithmetic_502s(self):
        _FakeRemoteFileHandler.video_crange = "bytes 0-/<script>"
        status, body, _ = self._video("bytes=0-1")
        self.assertEqual(status, 502)
        self.assertEqual(body, b"malformed partial reply from gpu1")

    # A Content-Length the relay cannot read as a length: past Python's int() digit limit (4,300; one more raises, and
    # the browser got a 500 with a traceback, the review of the video relay, 2026-10-21), signed, spaced, or not a
    # number at all. Each is a reply this relay cannot frame, so it is the same 502 as a reply carrying no length,
    # whole or partial, sent before any header goes out.
    HOSTILE_LENGTHS = ("9" * 4301, "1" + "0" * 30, "-5", "+5", "0x10", "1_000")

    def test_a_video_reply_whose_length_is_not_a_bounded_decimal_502s(self):
        for clen in self.HOSTILE_LENGTHS:
            for rng in (None, "bytes=0-1"):
                _FakeRemoteFileHandler.video_clen = clen
                status, body, msg = self._video(rng)
                self.assertEqual((status, body), (502, b"reply from gpu1 carried no length"), (clen[:12], len(clen), rng))

    def test_a_download_whose_length_is_not_a_bounded_decimal_502s_before_any_header(self):
        # the download arm forwarded the remote's Content-Length verbatim and converted it only after the body, so a
        # hostile one reached the browser as a header and then raised; it is refused up front, as the video arm does
        for clen in self.HOSTILE_LENGTHS:
            _FakeRemoteFileHandler.dl_clen = clen
            rec = _RelayRecorder()
            km.Handler._relay_download(rec, "gpu1", self.fake.server_address[1], REMOTE_TOKEN,
                                       {"path": ["/tmp/data.bin"], "download": ["1"]})
            self.assertEqual(rec.status, 502, (clen[:12], len(clen)))
            self.assertNotIn("Content-Disposition", rec.headers, "nothing of the 200 went out")

    def test_a_head_whose_length_is_not_a_bounded_decimal_502s(self):
        # the HEAD a preview probes with passed the remote's Content-Length to the browser as it came, the one arm the
        # bounded-decimal check had not reached (the review of #349, 2026-10-21); a picture's and a video's alike
        self._register("gpu1", self.fake.server_address[1])
        for clen in self.HOSTILE_LENGTHS:
            _FakeRemoteFileHandler.png_clen = clen
            status, body, msg = self._get_msg("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", method="HEAD")
            self.assertEqual((status, body), (502, b""), (clen[:12], len(clen)))
            self.assertNotEqual(msg.get("Content-Length"), clen, "the hostile length never reaches the browser")
            _FakeRemoteFileHandler.video_clen = clen
            status, body, msg = self._video(method="HEAD")
            self.assertEqual((status, body), (502, b""), ("video", clen[:12], len(clen)))
        _FakeRemoteFileHandler.png_clen = _FakeRemoteFileHandler.video_clen = None
        status, _, msg = self._get_msg("/remote/gpu1/file?path=%2Ftmp%2Fplot.png", method="HEAD")
        self.assertEqual((status, msg.get("Content-Length")), (200, str(len(PNG_BYTES))), "a real length still passes")

    def test_a_download_framed_both_chunked_and_by_length_502s_before_any_header(self):
        # http.client reads a chunked body to its end and ignores the length, so the relay advertised the remote's
        # Content-Length (10) and then copied the whole file after it: the excess was the start of the browser's next
        # response on a keep-alive connection (the review of #349, 2026-10-21). Refused up front, a clean 502
        _FakeRemoteFileHandler.dl_chunked = True
        for head in (False, True):
            rec = _RelayRecorder()
            km.Handler._relay_download(rec, "gpu1", self.fake.server_address[1], REMOTE_TOKEN,
                                       {"path": ["/tmp/data.bin"], "download": ["1"]}, head=head)
            self.assertEqual(rec.status, 502, "head" if head else "get")
            self.assertNotIn("Content-Disposition", rec.headers, "nothing of the 200 went out")
            self.assertNotIn(BIN_BYTES[:64], b"".join(rec.writes), "no byte of the file went out")
        # over HTTP too: the browser sees the relay's 502 and its reason
        self._register("gpu1", self.fake.server_address[1])
        status, body, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fdata.bin&download=1")
        self.assertEqual((status, body), (502, b"reply from gpu1 carried both a length and a transfer encoding"))

    def test_the_largest_bounded_length_still_relays(self):
        # the bound is a length no file reaches (a 16-digit byte count is 9 PB), never a cap on a real one
        self.assertEqual(km._relay_length("9" * 16), 9999999999999999)
        self.assertIsNone(km._relay_length("9" * 17))
        self.assertEqual(km._relay_length("0"), 0)
        self.assertEqual(km._relay_length(str(len(VIDEO_BYTES))), len(VIDEO_BYTES))
        self.assertIsNone(km._relay_length(None))

    def test_head_relays_the_real_length_and_ranges_under_our_type(self):
        _FakeRemoteFileHandler.video_ctype = "text/html"
        status, body, msg = self._video(method="HEAD")
        self.assertEqual((status, body), (200, b""))
        self.assertEqual(msg.get("Content-Type"), "video/mp4")
        self.assertEqual(msg.get("Content-Length"), str(len(VIDEO_BYTES)))
        self.assertEqual(msg.get("Accept-Ranges"), "bytes")

    def test_a_video_past_the_slurp_cap_still_relays_while_an_image_does_not(self):
        # the backstop 413 guards the slurped arm (images, PDFs) only: a streamed video holds one chunk at a time
        from unittest import mock
        with mock.patch.object(km, "_PREVIEW_MAX_BYTES", 4):
            status, body, _ = self._video()
            self.assertEqual((status, body), (200, VIDEO_BYTES))
            self._relayed_partial("bytes=0-1", 0, 1)
            status, body, _ = self._get("/remote/gpu1/file?path=%2Ftmp%2Fplot.png")
            self.assertEqual((status, body), (413, b"too large to preview"))

    def test_the_relayed_video_arrives_as_bounded_chunks_never_one_slurp(self):
        # direct call, so the WRITES are visible: the over-HTTP tests above only see the reassembled body
        self._register("gpu1", self.fake.server_address[1])
        rec = _RelayRecorder()
        km.Handler._remote_file(rec, "gpu1", "path=%2Ftmp%2Fclip.mp4")
        body = [w for w in rec.writes if w]          # _send writes the empty body of the header block
        self.assertEqual(rec.status, 200)
        self.assertEqual(rec.headers.get("Content-Length"), str(len(VIDEO_BYTES)))
        self.assertGreater(len(body), 1, "one write means the reply was slurped")
        self.assertTrue(all(len(w) <= km._DOWNLOAD_CHUNK for w in body))
        self.assertEqual(b"".join(body), VIDEO_BYTES)
        self.assertFalse(rec.close_connection)

    def test_a_remote_that_truncates_a_video_cleanly_closes_this_connection_short(self):
        # the remote sends half and closes cleanly (http.client reads b'' with no exception): only counting the
        # copied bytes against the forwarded Content-Length spots the broken promise (the download arm's reasoning)
        _FakeRemoteFileHandler.video_truncate = True
        self._register("gpu1", self.fake.server_address[1])
        rec = _RelayRecorder()
        km.Handler._remote_file(rec, "gpu1", "path=%2Ftmp%2Fclip.mp4")
        self.assertEqual(rec.headers.get("Content-Length"), str(len(VIDEO_BYTES)))
        self.assertEqual(b"".join(rec.writes), VIDEO_BYTES[: len(VIDEO_BYTES) // 2])
        self.assertTrue(rec.close_connection, "short of the forwarded length must CLOSE, or the browser hangs")


if __name__ == "__main__":
    unittest.main()
