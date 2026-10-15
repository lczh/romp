#!/usr/bin/env python3
"""The kernel's server listens with a backlog of 128, not socketserver's 5 (2026-10-15).

The kernel accepts connections on its main thread, which needs the interpreter lock like every other thread. While the
kernel stalls (a long garbage collection, memory paged back in from swap) nothing is accepted, and the connections that
arrive meanwhile wait in the listen queue. With socketserver's default backlog of 5 the queue was full after a handful
of redials from one dashboard, and the system dropped every connection past it: the browser's dial hung and was retried
on the system's own schedule, long after the stall had ended. _LoopbackServer sets request_queue_size to 128.

Pinned three ways: the class attribute (with the stock class's 5 as the control); the value listen() is actually called
with when the server is built (recorded, the stock class's call as the control); and, on Linux, the behavior itself: a
burst of dials at a server that accepts nothing completes every handshake against the kernel's class and stalls past
the backlog against the stock class. Synthetic: ephemeral loopback servers that never serve a request.
"""
import os
import select
import socket
import sys
import tempfile
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

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
km = load_source("romp_kernel_listen_backlog", os.path.join(BIN, "romp-kernel"))

BURST = 24   # more than the stock backlog lets through (5 + 1 on Linux), well under 128


class _Quiet(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def _build(cls):
    """Build a server of `cls` on a free loopback port, recording every listen() backlog -> (server, backlogs). The
    reverse lookup the stock bind makes is answered without the resolver (tests/test_server_bind_skips_reverse_dns.py)."""
    backlogs = []
    real_listen = socket.socket.listen

    def listen(self, *a):
        backlogs.append(a[0] if a else None)
        return real_listen(self, *a)
    with mock.patch.object(socket, "getfqdn", lambda name="": name or "recorded"), \
            mock.patch.object(socket.socket, "listen", listen):
        srv = cls(("127.0.0.1", 0), _Quiet)
    return srv, backlogs


def _handshakes_completed(port, n, deadline_s=2.0):
    """Dial `port` n times without anyone accepting, and count the dials whose handshake completes within the deadline
    (the socket turns writable). Past a full listen queue Linux drops the SYN, so such a dial stays pending."""
    socks = []
    try:
        for _ in range(n):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setblocking(False)
            try:
                s.connect(("127.0.0.1", port))
            except BlockingIOError:
                pass
            socks.append(s)
        pending, done = set(socks), set()
        end = time.monotonic() + deadline_s
        while pending:
            left = end - time.monotonic()
            if left <= 0:
                break
            _r, w, _x = select.select([], list(pending), [], left)
            for s in w:
                if s.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR) == 0:
                    done.add(s)
                pending.discard(s)
        return len(done)
    finally:
        for s in socks:
            s.close()


class ListenBacklog(unittest.TestCase):
    def test_the_class_asks_for_128_where_the_stock_class_asks_for_5(self):
        self.assertEqual(ThreadingHTTPServer.request_queue_size, 5, "the control: socketserver's default")
        self.assertEqual(km._LoopbackServer.request_queue_size, 128)

    def test_listen_is_called_with_the_backlog_when_the_server_is_built(self):
        srv, backlogs = _build(ThreadingHTTPServer)
        srv.server_close()
        self.assertEqual(backlogs, [5], "the control: the stock server listens with 5")
        srv, backlogs = _build(km._LoopbackServer)
        srv.server_close()
        self.assertEqual(backlogs, [128], "the kernel's server listens with 128")

    @unittest.skipUnless(sys.platform.startswith("linux"), "the accept-queue drop below is Linux's behavior")
    def test_a_burst_of_dials_at_a_server_that_accepts_nothing_completes_past_the_stock_backlog(self):
        try:
            with open("/proc/sys/net/core/somaxconn") as f:
                if int(f.read().strip()) < BURST:
                    self.skipTest("somaxconn caps the backlog below the burst on this host")
        except (OSError, ValueError):
            pass
        stock, _ = _build(ThreadingHTTPServer)
        try:
            got = _handshakes_completed(stock.server_address[1], BURST)
        finally:
            stock.server_close()
        self.assertLess(got, BURST, "the control: past a backlog of 5 the system drops the dials (%d of %d completed)" % (got, BURST))
        kernel, _ = _build(km._LoopbackServer)
        try:
            got = _handshakes_completed(kernel.server_address[1], BURST)
        finally:
            kernel.server_close()
        self.assertEqual(got, BURST, "every dial of the burst waits in the kernel's queue for the stall to end")


if __name__ == "__main__":
    unittest.main()
