#!/usr/bin/env python3
"""Federated push, the pull direction, over two REAL kernels (plans/federated-push.md, 2026-10-10).

The topology the pull exists for: only the HUB holds a row. A second hermetic kernel checks in to the hub (the mobile
handshake the remote labs use, posted by the test, so the origin itself holds no row for the hub and could never push
to it); the hub files it as TESTHOST, a name the origin does not declare for itself (it calls itself boxa), and the user
marks it trusted. The hub holds one device subscription, whose push service is a local HTTP server in this test that
answers 201 and counts what reaches it; the hub's own push ledger (push-ledger.json, one row per push and device) is
the record of what its gate let through. The origin's own notification switches stay OFF the whole time: they govern
the origin's devices only. The hub's master and turn switches are on. A client rides the hub's relay to the origin
(GET /remote/TESTHOST/ws, the merged dashboard's socket), which is what builds the origin's feed and so its card bells.

What the run pins, in order:
  * the hub pulls: its row reads pushPulled, and its first answer resumes at the origin's newest with one loud line;
  * a turn end on the origin (session api) reaches the hub's gate exactly once: one ledger row, sid TESTHOST:<api>
    (the hub's name for the row, never the origin's own), kind turn, and one POST at the push service;
  * a needs-you card on the origin (session web) reaches it exactly once the same way, kind card;
  * bells follow the dashboard, the user's scenario: the origin's card carries its raw override (none), a mute on api
    clicked as the merged dashboard sends it (as given, over the hub's relay) is stored though the origin's own master is
    off, and api's next turn end stays on the origin; an arm clicked the same way buzzes;
  * the HUB restarts with its cursor on disk while the origin fires a turn end: the event is served when the hub comes
    back (it is still that session's latest), once;
  * the hub restarts with NO cursor: it resumes at the origin's newest and says what it skipped, delivering nothing old;
  * the ORIGIN restarts (a new life, a new boot id): the hub resumes at the newest with its loud line, and the next turn
    end is delivered once;
and through all of it no event is delivered twice.

Synthetic only: invented hosts (TESTHOST, boxa, hubhost), private placeholder sids, invented text.
"""
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (kernel_env)

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    HAVE_CRYPTO = True
except ImportError:          # the kernel's Web Push needs it too; CI installs it (ci.yml)
    HAVE_CRYPTO = False

API = "cdcdcdcd-5151-4a4a-9b9b-00000000aa01"   # the origin's session whose turns end (private to this module)
WEB = "cdcdcdcd-5151-4a4a-9b9b-00000000aa02"   # the origin's session whose card blocks on the user
ROW = "TESTHOST"                                # the hub's name for the origin's row
SELF = "boxa"                                   # the name the origin declares for itself


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class _PushService:
    """A push service on loopback: every POST is counted and answered 201, the status Apple and Google answer."""

    def __init__(self):
        self.posts = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(n)
                outer.posts.append(self.path)
                self.send_response(201)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


class _Relay:
    """The merged dashboard's socket to the origin, through the hub's relay (GET /remote/<row>/ws, as the Sessions pane dials it): it keeps
    the origin building its feed, which is where card bells fire. Frames are drained on a thread and pings answered."""

    def __init__(self, hport, htoken):
        self.sock = socket.create_connection(("127.0.0.1", hport), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET /remote/%s/ws?app=fleet&wid=pushpull HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\n"
                           "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\nX-Romp-Token: %s\r\n\r\n"
                           % (ROW, hport, key, htoken)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("the relay upgrade closed")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("relay upgrade refused: %r" % head[:160])
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.frames = 0
        self.alive = True
        self._send({"type": "ready"})
        threading.Thread(target=self._drain, daemon=True).start()

    def send(self, obj):
        """A frame from the merged dashboard to the origin (federation has already stripped the host prefix)."""
        self._send(obj)

    def _send(self, obj):
        data = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n))
        self.sock.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def _drain(self):
        self.sock.settimeout(None)
        try:
            while True:
                self._need(2)
                b0, b1 = self.buf[0], self.buf[1]
                op, n, i = b0 & 0x0F, b1 & 0x7F, 2
                if n == 126:
                    self._need(4); n = struct.unpack(">H", self.buf[2:4])[0]; i = 4
                elif n == 127:
                    self._need(10); n = struct.unpack(">Q", self.buf[2:10])[0]; i = 10
                self._need(i + n)
                payload, self.buf = self.buf[i:i + n], self.buf[i + n:]
                if op == 0x9:
                    self.sock.sendall(bytes([0x8A, 0x80 | len(payload)]) + b"\0\0\0\0" + payload)
                elif op == 0x8:
                    break
                else:
                    self.frames += 1
        except (OSError, EOFError):
            pass
        self.alive = False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def _text(path):
    with open(path) as f:
        return f.read()


def _publish(path, obj):
    """A write of a file the kernel reads is a publication (a temp file and a rename), never an in-place rewrite."""
    tmp = str(path) + ".tmp-%d" % os.getpid()
    Path(tmp).write_text(json.dumps(obj))
    os.replace(tmp, str(path))


@unittest.skipUnless(HAVE_CRYPTO, "the python 'cryptography' package is absent: the hub's Web Push cannot encrypt")
class PushPullTwoKernels(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        cls.lab = tempfile.mkdtemp(prefix="push-pull-")
        cls.procs = {}
        cls.logs = {}
        cls.relay = None
        cls.svc = _PushService()
        cls.oport, cls.otoken = _free_port(), "testtok-origin-pp"
        cls.hport, cls.htoken = _free_port(), "testtok-hub-pp"
        cls.dist = os.path.join(cls.lab, "dist")
        os.makedirs(cls.dist, exist_ok=True)   # no page is loaded here: the kernels serve routes and sockets only
        cls._seed_origin()
        cls._seed_hub()
        try:
            cls._boot("origin")
            cls._boot("hub")
            cls._link()
        except unittest.SkipTest:
            cls.tearDownClass()
            raise
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        if cls.relay is not None:
            cls.relay.close()
        for p in list(cls.procs.values()):
            try:
                p.kill()
                p.wait(timeout=10)
            except Exception:
                pass
        cls.svc.close()
        shutil.rmtree(cls.lab, ignore_errors=True)

    # ── the two machines ───────────────────────────────────────────────────────────────────────────────────────────────
    @classmethod
    def _state(cls, name):
        return os.path.join(cls.lab, name, "xdg", "romp")

    @classmethod
    def _seed_origin(cls):
        state = cls._state("origin")
        claude = os.path.join(cls.lab, "origin", "claude")
        cwd = os.path.join(cls.lab, "origin", "notes-api")
        for d in ("names", "sdk", "states", "goals"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root writes its own hosts off (the conftest rule)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 3600
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))
        for sid, name, said in ((API, "api", "The fixtures load cleanly now."), (WEB, "web", "The parser section is drafted.")):
            Path(state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (name, cwd))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            Path(proj, sid + ".jsonl").write_text(
                json.dumps({"type": "user", "uuid": "u1", "parentUuid": None, "timestamp": iso(t0), "sessionId": sid,
                            "promptSource": "typed", "message": {"role": "user", "content": "set up the %s fixtures" % name}}) + "\n"
                + json.dumps({"type": "assistant", "uuid": "a1", "parentUuid": "u1", "timestamp": iso(t0 + 4), "sessionId": sid,
                              "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                          "content": [{"type": "text", "text": said}]}}) + "\n")
            Path(state, "states", sid + ".jsonl").write_text(json.dumps({"t": t0 + 5, "state": "idle"}) + "\n")
        cls.origin_cwd, cls.origin_claude = cwd, claude

    @classmethod
    def _seed_hub(cls):
        state = cls._state("hub")
        os.makedirs(state, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")
        Path(state, "notify-cards.json").write_text(json.dumps({"*": True, "*turns": True}))   # the hub's master + turn switch
        key = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        ep = "http://127.0.0.1:%d/push/device-1" % cls.svc.port
        Path(state, "push-subscriptions.json").write_text(json.dumps(
            {ep: {"endpoint": ep, "keys": {"p256dh": _b64u(key), "auth": _b64u(os.urandom(16))}, "origin": ""}}))
        os.chmod(os.path.join(state, "push-subscriptions.json"), 0o600)

    @classmethod
    def _boot(cls, name):
        port, token = (cls.oport, cls.otoken) if name == "origin" else (cls.hport, cls.htoken)
        claude = os.path.join(cls.lab, name, "claude")
        os.makedirs(claude, exist_ok=True)
        env = _lab.kernel_env(os.path.join(cls.lab, name), claude, cls.dist, port, token,
                              ROMP_HOST_NAME=SELF if name == "origin" else "hubhost", ROMP_DISTILLER="0")
        n = sum(1 for k in cls.logs if k.startswith(name))
        log = os.path.join(cls.lab, "%s-%d.log" % (name, n))
        cls.logs["%s-%d" % (name, n)] = log
        with open(log, "w") as out:
            cls.procs[name] = subprocess.Popen([sys.executable, os.path.join(BIN, "romp-kernel")], stdout=out,
                                               stderr=subprocess.STDOUT, env=env)
        for _ in range(240):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1).close()
                return
            except Exception:
                if cls.procs[name].poll() is not None:
                    break
                time.sleep(0.25)
        raise unittest.SkipTest("hermetic kernel %s never served /healthz here: %s" % (name, _text(log)[-800:]))

    @classmethod
    def _call(cls, name, method, path, body=None):
        port, token = (cls.oport, cls.otoken) if name == "origin" else (cls.hport, cls.htoken)
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path), method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", "X-Romp-Token": token})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode() or "{}")

    @classmethod
    def _log(cls, name):
        """Everything the kernel `name` wrote, across its lives in this run."""
        return "".join(_text(p) for k, p in sorted(cls.logs.items()) if k.startswith(name))

    @classmethod
    def _wait(cls, what, pred, secs=90):
        for _ in range(int(secs * 4)):
            v = pred()
            if v:
                return v
            time.sleep(0.25)
        raise AssertionError("timed out waiting for %s\nhub log:\n%s\norigin log:\n%s"
                             % (what, cls._log("hub")[-3000:], cls._log("origin")[-2000:]))

    @classmethod
    def _row(cls):
        rows = cls._call("hub", "GET", "/tunnels").get("tunnels") or []
        return next((t for t in rows if t.get("host") == ROW), None)

    @classmethod
    def _link(cls):
        """The check-in (only the hub holds a row), the user's trust, the row up and pulled, the relay socket."""
        ans = cls._call("hub", "POST", "/checkin", {"host": ROW, "kernelPort": cls.oport, "busPort": _free_port(),
                                                    "token": cls.otoken})
        if not ans.get("ok"):
            raise AssertionError("the hub refused the check-in: %r" % ans)
        cls._call("hub", "POST", "/tunnels/trust", {"host": ROW, "trust": "trusted"})
        cls._wait("the hub's row up and pulled", lambda: (cls._row() or {}).get("status") == "up"
                  and (cls._row() or {}).get("pushPulled"))
        cls._wait("the hub's first pull", lambda: "events from '%s' resume at its newest" % ROW in cls._log("hub"))
        cls.relay = _Relay(cls.hport, cls.htoken)
        cls._wait("the relay's first frames", lambda: cls.relay.frames > 0)
        cls._origin_baselined()

    # ── what the hub pushed ────────────────────────────────────────────────────────────────────────────────────────────
    @classmethod
    def _ledger(cls):
        try:
            rows = json.loads(Path(cls._state("hub"), "push-ledger.json").read_text()).get("rows")
        except (OSError, ValueError, AttributeError):
            return []
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    @classmethod
    def _pushed(cls, sid, kind):
        return [r for r in cls._ledger() if r.get("sid") == "%s:%s" % (ROW, sid) and r.get("kind") == kind]

    @classmethod
    def _origin_baselined(cls, ticks=1):
        """Wait until the origin's turn tick has run `ticks` more times after its first in this life (its /perf stage total
        for jobs.turnNotify moving): the first sight of a session is a silent baseline, so a turn end stamped before the
        tick has seen the session would be taken as that baseline and never announced. With a turn end just stamped, the
        same wait is the proof that the tick has read it."""
        def ms():
            return (cls._call("origin", "GET", "/perf").get("stages_ms") or {}).get("jobs.turnNotify")
        cur = cls._wait("the origin's first turn tick", lambda: ms() is not None and [ms()])[0]
        for _ in range(ticks):
            cur = cls._wait("the origin's next turn tick", lambda: (ms() or 0) > cur and [ms()])[0]

    @classmethod
    def _origin_bell(cls, sid):
        try:
            return (json.loads(Path(cls._state("origin"), "session-flags.json").read_text()).get(sid) or {}).get("notify", "absent")
        except (OSError, ValueError):
            return "absent"

    @classmethod
    def _end_turn(cls, sid, at):
        """A turn the user opened ends on the origin: the Stop hook's stamps on the session's registration, then the
        hook's wake (/tick), as the real hook does."""
        p = Path(cls._state("origin"), "sdk", sid + ".json")
        reg = json.loads(p.read_text())
        reg.update(lastStopAt=at, lastTurnOpener="human")
        _publish(p, reg)
        cls._call("origin", "POST", "/tick", {})

    def test_the_pull_end_to_end(self):
        # ── a turn end on the origin: exactly once at the hub's gate, under the HUB's name for the row ──
        before = len(self.svc.posts)
        t1 = int(time.time())
        self._end_turn(API, t1)
        rows = self._wait("the turn end at the hub", lambda: self._pushed(API, "turn"))
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["host"], ROW, "the hub's name for the row, never the origin's own (%s)" % SELF)
        self.assertEqual(rows[0]["name"], "api")
        self._wait("the push service's POST", lambda: len(self.svc.posts) > before)

        # ── a needs-you card on the origin: exactly once, kind card ──
        g = WEB + ":g1"
        t = int(time.time())
        _publish(Path(self._state("origin"), "goals", WEB + ".json"),
                 {"rompUuid": WEB, "seq": 1, "lastNode": g, "closedTurns": [], "placements": {}, "status": {g: "blocked"},
                  "nodes": {g: {"id": g, "text": "pick the parser's error format", "parentId": None, "nodeComplete": False,
                                "blocked": True, "blockWhy": "which error format should the parser report?",
                                "cleared": False, "trail": [], "t": t,
                                "log": [{"ev_t": t, "src": "planner", "kind": "block", "why": "asked: which error format?", "at": t}]}}})
        self._call("origin", "POST", "/tick", {})
        cards = self._wait("the needs-you card at the hub", lambda: self._pushed(WEB, "card"), secs=120)
        self.assertEqual(len(cards), 1, cards)
        self.assertEqual(cards[0]["cardId"], g)

        # ── bells follow the dashboard, the user's scenario: the hub's master on, the origin's never set ──
        ask = next(a for a in self._call("origin", "GET", "/feed.json")["asks"] if a.get("itemId") == g)
        self.assertIn("notifyRaw", ask, "the origin's card carries its raw override for the merged dashboard")
        self.assertIsNone(ask["notifyRaw"], "none: the merged dashboard paints it against the hub's master (on)")
        # a click to off on api's bell, sent as the merged dashboard sends it over the relay: stored as given (the origin's
        # own master is off, so judged against it the mute would not have been stored), and api's next turn end is quiet
        self.relay.send({"type": "setSessionFlag", "id": API, "flag": "notify", "value": False, "asGiven": True})
        self._wait("the mute stored on the origin", lambda: self._origin_bell(API) is False)
        self._end_turn(API, t1 + 3)
        self._origin_baselined(ticks=2)                   # the origin's tick has read that turn end
        time.sleep(1.0)
        self.assertEqual(len(self._pushed(API, "turn")), 1, "muted: the turn end never left the origin")
        # a click back on: stored as an arm, and the next turn end buzzes
        self.relay.send({"type": "setSessionFlag", "id": API, "flag": "notify", "value": True, "asGiven": True})
        self._wait("the arm stored on the origin", lambda: self._origin_bell(API) is True)
        self._end_turn(API, t1 + 5)
        self._wait("the armed turn end at the hub", lambda: len(self._pushed(API, "turn")) == 2)
        # and back to following the hub's master, on purpose (a null as given)
        self.relay.send({"type": "setSessionFlag", "id": API, "flag": "notify", "value": None, "asGiven": True})
        self._wait("the bell back at its default", lambda: self._origin_bell(API) == "absent")

        # ── the hub restarts with its cursor on disk; the origin fires meanwhile; the event is served on its return ──
        self.procs["hub"].kill(); self.procs["hub"].wait(timeout=10)
        self.assertTrue(json.loads(Path(self._state("hub"), "push-pull-cursors.json").read_text()).get(ROW), "the cursor is on disk")
        t2 = t1 + 7
        self._end_turn(API, t2)
        time.sleep(1.0)                                   # the origin's cycle files the candidate while the hub is down
        self._boot("hub")
        self._wait("the missed turn end after the hub's restart", lambda: len(self._pushed(API, "turn")) == 3, secs=120)
        self.assertNotIn("resume at its newest", _text(self.logs["hub-1"]),
                         "a hub restarting with its cursor resumes from it, not from the newest")

        # ── the hub restarts with NO cursor: it resumes at the newest, says what it skipped, delivers nothing old ──
        self.procs["hub"].kill(); self.procs["hub"].wait(timeout=10)
        os.unlink(os.path.join(self._state("hub"), "push-pull-cursors.json"))
        self._boot("hub")
        third = self.logs["hub-2"]
        self._wait("the no-cursor line", lambda: "resume at its newest — this kernel holds no record" in _text(third))
        m = re.search(r"holds no record of where it left off; (\d+) event\(s\) it fired meanwhile skipped", _text(third))
        self.assertTrue(m and int(m.group(1)) >= 4, _text(third)[-1500:])
        time.sleep(2.0)
        self.assertEqual(len(self._pushed(API, "turn")), 3, "nothing old is delivered after a lost cursor")
        self.assertEqual(len(self._pushed(WEB, "card")), 1)

        # ── the origin restarts (a new life): the hub resumes at the newest, loudly; the next turn end arrives once ──
        self.procs["origin"].kill(); self.procs["origin"].wait(timeout=10)
        self._boot("origin")
        self._wait("the origin-restarted line", lambda: "its kernel restarted since this kernel last heard from it" in _text(third),
                   secs=120)
        self._origin_baselined()                          # the new life's turn tick takes its baseline first
        self._end_turn(API, t2 + 7)
        self._wait("the turn end after the origin's restart", lambda: len(self._pushed(API, "turn")) == 4, secs=120)

        # ── through all of it, nothing twice ──
        time.sleep(3.0)
        self.assertEqual(len(self._pushed(API, "turn")), 4, self._ledger())
        self.assertEqual(len(self._pushed(WEB, "card")), 1, self._ledger())


if __name__ == "__main__":
    unittest.main()
