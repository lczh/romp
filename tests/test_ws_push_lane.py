"""A pane's connect pushes run beside its socket's read loop, never in it (2026-10-09).

The `ready` arm and every `needFull` serve a whole connect push (Handler._push_one). They ran inline on the socket's
handler thread, the thread that also READS the socket, so on a kernel where that push took minutes every frame the
page sent meanwhile sat unread: two chat messages typed after a page load waited behind it, and the reload that
followed closed the socket before the loop came back to read them. Nothing on either side recorded them. These tests
drive the real read loop (Handler._ws) and the real dispatch over a scripted socket, with the connect push held open
on an event, and pin:

  - an op read after `ready` (a sendMessage) is dispatched while the connect push is still running;
  - the push work keeps its order: the connect push, the arrangement, the caps frame, then a needFull's push;
  - a socket that closes mid-push stops the lane: the queued pushes never run and the worker ends;
  - a connect push whose pane has gone stops building at its next tab.

Synthetic ids only.
"""
import io
import json
import os
import tempfile
import threading
import time
import unittest
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic state BEFORE the load: it resolves its root at import time
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_push_lane", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-5555555500a1"
TEXT = 0x1
WAIT = object()      # the scripted socket holds here (the page is still connected) until the test releases it


def _frame(msg):
    return (TEXT, json.dumps(msg).encode(), True)


class _Harness:
    """The real Handler._ws over a scripted socket: `frames` are read in order (WAIT blocks the read until `hold` is
    set; the end of the list is the page closing the socket). The connect push is a stub that records its call and
    blocks on `release`; the dispatch is the real one, with _drive recording a sendMessage instead of delivering it
    and the ready arm's two trailing frames recorded by name."""

    def __init__(self, frames, block_pushes=1):
        self.frames = list(frames)
        self.events = []                  # what ran, in order: "push", "viewOrder", "caps", "send"
        self.lock = threading.Lock()
        self.release = threading.Event()  # lets the held connect push return
        self.hold = threading.Event()     # lets a WAIT in the script pass
        self.block_pushes = block_pushes  # how many connect pushes block on `release` (the rest return at once)
        self.pushes = 0
        self.client = None
        self.loop_done = threading.Event()

    def note(self, what):
        with self.lock:
            self.events.append(what)

    def recv(self, rfile):
        while self.frames:
            f = self.frames.pop(0)
            if f is WAIT:
                self.hold.wait(10)
                continue
            return f
        return (0x8, b"", True)            # the page closed the socket

    def run(self):
        h = self
        real_drive = km._drive

        def drive(msg, client):
            h.client = client
            if isinstance(msg, dict) and msg.get("type") == "sendMessage":
                h.note("send")
                return True
            return real_drive(msg, client)

        class FakeSelf:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
            path = "/ws?app=chat&wid=w-test"
            rfile = io.BytesIO()
            wfile = io.BytesIO()
            connection = type("FakeSock", (), {"sendall": lambda self, b: None,
                                               "shutdown": lambda self, how: None})()
            close_connection = False
            def send_response(self, *a): pass
            def send_header(self, *a): pass
            def end_headers(self): pass
            _dispatch_ws = km.Handler._dispatch_ws

            def _push_one(self, client):
                with h.lock:
                    h.pushes += 1
                    n = h.pushes
                h.client = client
                h.note("push")
                if n <= h.block_pushes:
                    h.release.wait(10)

        saved = (km._ws_recv, km._drive, km._send_view_order, km._send_caps)
        km._ws_recv = self.recv
        km._drive = drive
        km._send_view_order = lambda client: h.note("viewOrder")
        km._send_caps = lambda client, views_seq=None: h.note("caps")
        self._saved = saved
        t = threading.Thread(target=self._loop, args=(FakeSelf(),), daemon=True)
        t.start()
        return t

    def _loop(self, fake):
        try:
            km.Handler._ws(fake)
        finally:
            self.loop_done.set()

    def restore(self):
        km._ws_recv, km._drive, km._send_view_order, km._send_caps = self._saved

    def wait_for(self, pred, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if pred(list(self.events)):
                    return True
            time.sleep(0.01)
        return False


class ConnectPushOffTheReadLoop(unittest.TestCase):
    def _harness(self, frames, **kw):
        h = _Harness(frames, **kw)
        self.addCleanup(h.restore)
        self.addCleanup(h.hold.set)
        self.addCleanup(h.release.set)
        return h

    def test_a_send_read_after_ready_is_dispatched_while_the_connect_push_runs(self):
        h = self._harness([_frame({"type": "ready", "proto": 2}),
                           _frame({"type": "sendMessage", "id": SID, "text": "rebuild the search index",
                                   "qid": "echo:" + "a" * 32}),
                           WAIT])
        h.run()
        self.assertTrue(h.wait_for(lambda ev: "push" in ev), "the ready arm reached its connect push")
        self.assertTrue(h.wait_for(lambda ev: "send" in ev),
                        "the send behind `ready` was dispatched while the connect push still ran: it never waits on a push")
        self.assertNotIn("caps", h.events, "…and the push it overtook has not finished yet")
        h.release.set()
        self.assertTrue(h.wait_for(lambda ev: "caps" in ev), "the ready arm finished on its lane")
        h.hold.set()
        self.assertTrue(h.loop_done.wait(5), "the read loop ended when the page closed the socket")

    def test_the_push_work_keeps_its_order(self):
        h = self._harness([_frame({"type": "ready", "proto": 2}),
                           _frame({"type": "needFull", "id": SID, "why": "gap"}),
                           _frame({"type": "sendMessage", "id": SID, "text": "add a test for the cache",
                                   "qid": "echo:" + "b" * 32}),
                           WAIT])
        h.run()
        self.assertTrue(h.wait_for(lambda ev: "send" in ev), "the send was dispatched")
        self.assertEqual(h.events.count("push"), 1, "the needFull's push waits behind the ready arm's, on the same lane")
        h.release.set()
        self.assertTrue(h.wait_for(lambda ev: ev.count("push") == 2))
        pushwork = [e for e in h.events if e != "send"]
        self.assertEqual(pushwork, ["push", "viewOrder", "caps", "push"],
                         "the connect push, the arrangement and the caps frame, then the needFull's push: the order the "
                         "handler thread gave them")
        self.assertLess(h.events.index("send"), h.events.index("caps"), "the send did not wait for any of it")
        h.hold.set()
        self.assertTrue(h.loop_done.wait(5))

    def test_a_socket_that_closes_mid_push_stops_the_lane(self):
        h = self._harness([_frame({"type": "ready", "proto": 2}),
                           _frame({"type": "needFull", "id": SID, "why": "gap"}),
                           _frame({"type": "needFull", "id": SID, "why": "prefetch"})])   # then the page goes
        h.run()
        self.assertTrue(h.loop_done.wait(5),
                        "the read loop reached the close while the connect push was still running")
        self.assertTrue(h.wait_for(lambda ev: "push" in ev))
        lane = h.client.get("pushLane")
        self.assertIsNotNone(lane, "the socket handler armed the client's push lane")
        self.assertFalse(h.client.get("alive"), "the closed socket's client is dead")
        h.release.set()
        end = time.time() + 5
        while time.time() < end and lane["running"]:
            time.sleep(0.01)
        self.assertFalse(lane["running"], "the lane's worker ended")
        self.assertEqual(len(lane["jobs"]), 0, "and dropped the pushes queued for the pane that went")
        self.assertEqual(h.pushes, 1, "neither queued needFull push ran after the socket closed")
        self.assertNotIn("caps", h.events, "the ready arm stops after its push once the pane has gone: no caps to a closed socket")

    def test_a_client_without_a_lane_runs_the_push_inline(self):
        # a direct dispatch (a test's stand-in client, no socket handler) keeps today's synchronous arm
        sent = []
        hs = object.__new__(km.Handler)
        hs._push_one = lambda c: sent.append("push")
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda s: sent.append(json.loads(s).get("type"))}
        km.Handler._dispatch_ws(hs, {"type": "needFull", "id": SID, "why": "gap"}, client)
        self.assertEqual(sent, ["push"], "no lane: the push ran before the dispatch returned")


class ConnectPushStopsForAGonePane(unittest.TestCase):
    def test_the_build_loop_stops_at_the_next_tab_once_its_pane_has_gone(self):
        sids = ["11111111-2222-3333-4444-55555555%04x" % i for i in range(1, 4)]
        tabs = [{"sid": s, "name": n, "path": "/nonexistent/%s.jsonl" % s} for s, n in zip(sids, ("web", "api", "tests"))]
        built = []
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda s: None, "handshake": True, "proto": 2}

        def build(sid, now, live_map):
            built.append(sid)
            client["alive"] = False        # the page closed its socket while this tab built
            return None

        saved = {k: getattr(km, k) for k in ("_chat_tab_sessions", "build_session", "_chat_build_sig", "_live_map")}
        km._chat_tab_sessions = lambda now, live_map: list(tabs)
        km.build_session = build
        km._chat_build_sig = lambda *a, **k: None
        km._live_map = lambda: {}
        try:
            km._push([client], connect=True)
        finally:
            for k, v in saved.items():
                setattr(km, k, v)
        self.assertEqual(built, sids[:1], "one tab built, then the connect push saw its pane gone and built no more")


if __name__ == "__main__":
    unittest.main()
