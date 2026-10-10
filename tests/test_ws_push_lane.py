"""A socket's ops run on a lane beside its read loop, and a drive op never waits on it (2026-10-09).

The read loop dispatched every op inline, and the `ready` arm and every `needFull` serve a whole connect push
(Handler._push_one), so on a kernel where that push took minutes nothing the page sent was read meanwhile: two chat
messages typed after a page load waited behind it, and the reload that followed closed the socket before the loop came
back to read them. Nothing on either side recorded them. These tests drive the real read loop (Handler._ws), the real
dispatch and the real _push_one over a scripted socket, with the connect push itself (_push) held open on an event, and
pin:

  - a drive op read after `ready` (a sendMessage) is dispatched while the connect push is still running;
  - every other op keeps the order it had on the handler thread: the connect push, the arrangement and the caps
    frame, then the next op, then a needFull's push;
  - a socket that ends mid-push: the read loop waits for its lane, so every op it read is still dispatched, while a
    connect push nobody can read any more is skipped, and the lane's worker ends;
  - a connect push whose pane has gone stops building at its next tab;
  - the read loop's drive-op predicate names exactly the ops _drive routes.

Synthetic ids only.
"""
import ast
import inspect
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
    set; the end of the list is the page closing the socket). The dispatch and Handler._push_one are the real ones; the
    connect push itself (_push) is a stub that records its call and blocks on `release`; _drive records a sendMessage
    instead of delivering it; the ready arm's two trailing frames are recorded by name."""

    def __init__(self, frames, block_pushes=1):
        self.frames = list(frames)
        self.events = []                  # what ran, in order: "push", "viewOrder", "caps", "send", "active"
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
            if isinstance(msg, dict) and msg.get("type") == "activeTab":
                h.note("active")
            return real_drive(msg, client)

        def push(targets, connect=False, live_map=None, only=None):
            with h.lock:
                h.pushes += 1
                n = h.pushes
            h.client = targets[0]
            h.note("push")
            if n <= h.block_pushes:
                h.release.wait(10)

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
            _push_one = km.Handler._push_one

        saved = (km._ws_recv, km._drive, km._push, km._send_view_order, km._send_caps)
        km._ws_recv = self.recv
        km._drive = drive
        km._push = push
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
        km._ws_recv, km._drive, km._push, km._send_view_order, km._send_caps = self._saved

    def wait_for(self, pred, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            with self.lock:
                if pred(list(self.events)):
                    return True
            time.sleep(0.01)
        return False


class OpsOffTheReadLoop(unittest.TestCase):
    def _harness(self, frames, **kw):
        h = _Harness(frames, **kw)
        self.addCleanup(h.restore)
        self.addCleanup(h.loop_done.wait, 10)
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

    def test_every_other_op_keeps_its_order_behind_the_push(self):
        h = self._harness([_frame({"type": "ready", "proto": 2}),
                           _frame({"type": "activeTab", "id": SID}),
                           _frame({"type": "needFull", "id": SID, "why": "gap"}),
                           _frame({"type": "sendMessage", "id": SID, "text": "add a test for the cache",
                                   "qid": "echo:" + "b" * 32}),
                           WAIT])
        h.run()
        self.assertTrue(h.wait_for(lambda ev: "send" in ev), "the send was dispatched")
        self.assertEqual([e for e in h.events if e != "send"], ["push"],
                         "the tab switch and the needFull wait behind the connect push, as they did on the handler thread")
        self.assertIsNone(h.client.get("active"), "the switch has not run yet")
        h.release.set()
        self.assertTrue(h.wait_for(lambda ev: ev.count("push") == 2))
        self.assertEqual([e for e in h.events if e != "send"], ["push", "viewOrder", "caps", "active", "push"],
                         "the connect push, the arrangement and the caps frame, the switch, then the needFull's push")
        self.assertEqual(h.client.get("active"), SID)
        self.assertLess(h.events.index("send"), h.events.index("caps"), "the send did not wait for any of it")
        h.hold.set()
        self.assertTrue(h.loop_done.wait(5))

    def test_a_socket_that_ends_mid_push_dispatches_what_it_read_and_skips_the_pushes(self):
        h = self._harness([_frame({"type": "ready", "proto": 2}),
                           WAIT,                                              # the connect push starts…
                           _frame({"type": "needFull", "id": SID, "why": "gap"}),
                           _frame({"type": "activeTab", "id": SID}),
                           _frame({"type": "needFull", "id": SID, "why": "prefetch"})])   # …then the page goes
        h.run()
        self.assertTrue(h.wait_for(lambda ev: "push" in ev), "the connect push is running")
        h.hold.set()                                                          # the rest is read, then the close
        end = time.time() + 3
        while time.time() < end and not h.client.get("closing"):
            time.sleep(0.01)
        self.assertFalse(h.loop_done.is_set(), "the read loop reached the close and waits for its lane")
        self.assertTrue(h.client.get("closing"), "the client is marked closing")
        h.release.set()
        self.assertTrue(h.loop_done.wait(5), "the lane drained and the loop tore the client down")
        self.assertEqual(h.pushes, 1, "neither needFull's connect push ran for the pane that went")
        self.assertIn("active", h.events, "an op the loop read before the close was still dispatched")
        self.assertFalse(h.client.get("alive"))
        self.assertFalse(h.client["lane"]["thread"].is_alive(), "the lane's worker ended")
        self.assertEqual(h.client["lane"]["pending"], 0)


class ConnectPushStopsForAGonePane(unittest.TestCase):
    def _run(self, flip):
        sids = ["11111111-2222-3333-4444-55555555%04x" % i for i in range(1, 4)]
        tabs = [{"sid": s, "name": n, "path": "/nonexistent/%s.jsonl" % s} for s, n in zip(sids, ("web", "api", "tests"))]
        built = []
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda s: None, "handshake": True, "proto": 2}

        def build(sid, now, live_map):
            built.append(sid)
            flip(client)                   # the page went while this tab built
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
        return sids, built

    def test_the_build_loop_stops_at_the_next_tab_once_its_socket_is_closing(self):
        sids, built = self._run(lambda c: c.__setitem__("closing", True))
        self.assertEqual(built, sids[:1], "one tab built, then the connect push saw its pane gone and built no more")

    def test_and_once_its_client_is_dead(self):
        sids, built = self._run(lambda c: c.__setitem__("alive", False))
        self.assertEqual(built, sids[:1])

    def test_a_push_that_had_not_started_is_skipped(self):
        called = []
        saved = km._push
        km._push = lambda targets, connect=False, live_map=None, only=None: called.append(connect)
        try:
            km.Handler._push_one(object.__new__(km.Handler), {"app": "chat", "closing": True})
            km.Handler._push_one(object.__new__(km.Handler), {"app": "chat"})
        finally:
            km._push = saved
        self.assertEqual(called, [True], "only the live client's connect push ran")


class DriveOpsAreTheOnesDriveRoutes(unittest.TestCase):
    def test_the_predicate_mirrors_drives_own_list(self):
        tree = ast.parse(inspect.getsource(km._drive).lstrip())
        own = None
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ID_OPS" for t in node.targets):
                own = tuple(ast.literal_eval(node.value))
        self.assertIsNotNone(own, "_drive keeps its ID_OPS list")
        self.assertEqual(own, km._DRIVE_ID_OPS, "the read loop's list is _drive's, op for op")
        for t in own:
            self.assertTrue(km._is_drive_op({"type": t, "id": SID}), t)
        self.assertTrue(km._is_drive_op({"type": "compact", "name": "web"}))
        self.assertTrue(km._is_drive_op({"type": "askFollowUp", "itemId": SID + ":g1", "cont": True}))
        for m in ({"type": "ready"}, {"type": "needFull", "id": SID}, {"type": "activeTab", "id": SID},
                  {"type": "sendMessage"}, {"type": "askFollowUp", "itemId": SID + ":g1"}, None, "x"):
            self.assertFalse(km._is_drive_op(m), m)


if __name__ == "__main__":
    unittest.main()
