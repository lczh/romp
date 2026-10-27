#!/usr/bin/env python3
"""Mail the kernel took but answered late is not delivered to the session a second time (2026-10-27).

The bus posts each chunk of a recipient's mail to the kernel's /deliver with a 12 s cap, and reads every failure as a
kernel it could not reach, a read timeout after the request went out included. It then puts the mail back in new/
under the same ids, and its retry pass posts it again. The kernel queues the banner before it answers, so when the
answer came after the cap the first copy was already in the session's queue and the second post queued it again: the
session acted on the same handoff twice, after a restart storm or under load. Two halves fix it:

  - the kernel's /deliver route remembers the message ids it handed to each session's backend, and answers a banner
    whose ids were all handed over already as injected, without delivering it again;
  - the bus posts the chunk that did not land as its own chunk on its next push, ahead of newer mail, so the kernel
    sees those ids again rather than a banner that mixes them with mail it has never seen.

The bus and the kernel's real /deliver route run here over loopback; only the backend behind the route, and the loss
of the kernel's first answer, are stubbed. SYNTHETIC fixtures only: invented text, private placeholder uuids, the
notes-api demo's web and api sessions, hostname TESTHOST."""
import http.client
import itertools
import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_late_deliver", os.path.join(BIN, "romp-kernel"))
pm = load_source("romp_postal_late_deliver", os.path.join(BIN, "romp-postal-service"))

WEB = "6d2b9c4e-81f3-4a57-b0e2-3c9d7a1f5e60"     # the recipient (web), private to this module
PEER = "7e3cad5f-92a4-4b68-c1f3-4dae8b2a6f71"    # the sender (api)
_SEQ = itertools.count(1)


def _mid():
    """A fresh message id in the bus's shape, so no test sees an id another test handed over."""
    n = next(_SEQ)
    return "1800000000.%06d_%d.TESTHOST" % (n, n)


def _banner(*mids):
    """A banner exactly as the bus's _push hands it to /deliver: one message per id, the bus's own formatter."""
    return pm.format_push([{"from": "api", "from_id": PEER, "body": "please run the notes-api migration",
                            "id": m, "kind": "delegate", "date": ""} for m in mids])


class _Backend:
    """Stands in for the session's backend behind the real route: records the message ids of every banner it is
    handed, as SdkBackend.deliver queues one, and answers `takes`. `hold`, when set, keeps a deliver waiting
    inside the backend until it is released (a slow resume under load)."""

    def __init__(self):
        self.took = []
        self.takes = True
        self.hold = None
        self.entered = threading.Event()

    def deliver(self, sid, text):
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        if not self.takes:
            return False
        self.took.append((sid, em.POSTAL_RE.findall(text)))
        return True

    def ids(self):
        return [m for _, ms in self.took for m in ms]


class _World(unittest.TestCase):
    def setUp(self):
        self.be = _Backend()
        self._for = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.A, self.B = _mid(), _mid()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.be.hold is not None:
            self.be.hold.set()
        self.srv.shutdown()
        self.srv.server_close()
        km.Sessions.backend_for = self._for

    def _deliver(self, text, sid=WEB):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        c.request("POST", "/deliver", json.dumps({"id": sid, "text": text}),
                  {"Content-Type": "application/json", "X-Romp-Token": km.TOKEN})
        r = c.getresponse()
        out = json.loads(r.read().decode() or "{}")
        c.close()
        return out


class TheBusRetryAfterALateAnswer(_World):
    """The bus end to end: its push, its put-back and its retry pass against the kernel's real route."""

    def setUp(self):
        super().setUp()
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (pm.KERNEL_BASE, pm.SERVE_TOKEN, pm._push_disabled, pm._log, pm.local_agents, pm._kernel_post)
        pm.KERNEL_BASE = "http://127.0.0.1:%d" % self.port
        pm.SERVE_TOKEN = km.TOKEN
        pm._push_disabled = lambda: False
        self.logged = []
        pm._log = self.logged.append
        pm.local_agents = lambda threads=False: [{"id": WEB, "name": "web", "state": "idle"}]
        self.posted, self.lose = [], 0
        real = self.saved[5]

        def post(path, body, timeout=2):
            resp = real(path, body, timeout=timeout)       # the kernel handles the request either way
            if path == "/deliver":
                self.posted.append(em.POSTAL_RE.findall(body.get("text", "")))
                if self.lose:
                    self.lose -= 1
                    return None                            # its answer came after the bus's cap: read as unreachable
            return resp
        pm._kernel_post = post

    def tearDown(self):
        (pm.KERNEL_BASE, pm.SERVE_TOKEN, pm._push_disabled, pm._log, pm.local_agents, pm._kernel_post) = self.saved
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm.STREAKS.pop(WEB, None)
        super().tearDown()

    def _box(self):
        return [m.get("id") for m in pm.read_box(WEB, consume=False)]

    def test_a_prompt_answer_delivers_once_and_the_retry_posts_nothing(self):
        mid = pm.deliver(WEB, "api", PEER, "please run the notes-api migration", kind="delegate")
        self.assertTrue(pm._push(WEB, {"id": WEB, "state": "idle"}))
        pm._retry_pending()
        self.assertEqual(self.be.ids(), [mid])
        self.assertEqual(self.posted, [[mid]])

    def test_mail_whose_answer_came_late_is_not_delivered_again_by_the_retry(self):
        self.lose = 1
        mid = pm.deliver(WEB, "api", PEER, "please run the notes-api migration", kind="delegate")
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "idle"}), "the bus heard no answer")
        self.assertTrue(any("deferred (kernel unreachable)" in ln for ln in self.logged), self.logged)
        self.assertEqual(self._box(), [mid], "put back in new/ under its own id, as before")
        pm._retry_pending()                                # the RETRY_INTERVAL pass posts it again
        self.assertEqual(self.posted, [[mid], [mid]], "the retry posts the same message again")
        self.assertEqual(self.be.ids(), [mid],
                         "the session gets the message once; before the fix the retry queued a second copy")
        self.assertEqual(self._box(), [], "the kernel answered the re-post as taken, so the bus spent the mail")

    def test_newer_mail_does_not_carry_the_put_back_message_in_a_second_time(self):
        self.lose = 1
        first = pm.deliver(WEB, "api", PEER, "please run the notes-api migration", kind="delegate")
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "idle"}))
        second = pm.deliver(WEB, "api", PEER, "and then rebuild the notes-api index", kind="delegate")
        pm._retry_pending()
        self.assertEqual(self.posted, [[first], [first], [second]],
                         "the chunk that did not land goes out again as itself, ahead of the newer mail; mixed "
                         "into one banner with mail the kernel never saw, it could not be recognized")
        self.assertEqual(sorted(self.be.ids()), sorted([first, second]), "each message reaches the session once")
        self.assertEqual(self._box(), [])


class TheDeliverRouteDedupe(_World):
    """The kernel's half on its own: what the route remembers, and when it forgets."""

    def test_the_same_banner_again_is_answered_taken_without_a_second_delivery(self):
        self.assertEqual(self._deliver(_banner(self.A)), {"ok": True, "injected": True})
        self.assertEqual(self._deliver(_banner(self.A)), {"ok": True, "injected": True})
        self.assertEqual(self.be.ids(), [self.A])

    def test_the_same_ids_for_another_session_are_that_sessions_own(self):
        other = "8f4dbe60-a3b5-4c79-d204-5ebf9c3b7082"
        self._deliver(_banner(self.A))
        self._deliver(_banner(self.A), sid=other)
        self.assertEqual([sid for sid, _ in self.be.took], [WEB, other])

    def test_a_banner_the_backend_did_not_take_is_delivered_when_posted_again(self):
        self.be.takes = False
        self.assertEqual(self._deliver(_banner(self.A)), {"ok": True, "injected": False})
        self.be.takes = True
        self.assertEqual(self._deliver(_banner(self.A)), {"ok": True, "injected": True})
        self.assertEqual(self.be.ids(), [self.A], "a refusal is not remembered as taken")

    def test_a_banner_sharing_only_some_ids_is_delivered_whole(self):
        self._deliver(_banner(self.A))
        self.assertEqual(self._deliver(_banner(self.A, self.B)), {"ok": True, "injected": True})
        self.assertEqual(self.be.ids(), [self.A, self.A, self.B],
                         "the new message is never swallowed with the old one: a duplicate beats a loss")

    def test_a_post_while_the_first_is_still_being_handed_over_is_answered_not_taken(self):
        self.be.hold = threading.Event()
        answers = {}
        first = threading.Thread(target=lambda: answers.setdefault("first", self._deliver(_banner(self.A))))
        first.start()
        self.assertTrue(self.be.entered.wait(5), "the first post reached the backend")
        second = threading.Thread(target=lambda: answers.setdefault("second", self._deliver(_banner(self.A))))
        second.start()
        second.join(5)
        try:
            self.assertEqual(answers.get("second"), {"ok": True, "injected": False},
                             "not delivered beside the first copy, and not claimed as taken before the first "
                             "copy is: the bus puts it back and its retry learns how the first one ended")
        finally:
            self.be.hold.set()
            first.join(10)
            second.join(10)
        self.assertEqual(answers.get("first"), {"ok": True, "injected": True})
        self.assertEqual(self.be.ids(), [self.A])
        self.assertEqual(self._deliver(_banner(self.A)), {"ok": True, "injected": True}, "now it is taken")
        self.assertEqual(self.be.ids(), [self.A])

    def test_mail_the_kernel_hands_back_to_the_bus_is_delivered_again(self):
        # SdkSession._return_stranded_mail: a banner fed to a client that was torn down before its turn resulted goes
        # back to the bus by id, and the bus posts it again. That post is the re-delivery the hand-back asks for.
        outer = self

        class Bus(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                ask = json.loads(self.rfile.read(n) or b"{}")
                # the bus puts it back and posts it again before the kernel has read this answer
                outer.again = outer._deliver(_banner(*ask["mids"]), sid=ask["id"])
                data = json.dumps({"ok": True, "restored": ask["mids"]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        bus = ThreadingHTTPServer(("127.0.0.1", 0), Bus)
        threading.Thread(target=bus.serve_forever, daemon=True).start()
        saved = km.BUS_PORT
        try:
            km.BUS_PORT = bus.server_address[1]
            self._deliver(_banner(self.A))
            self.assertEqual(km._bus_restore_mail(WEB, [self.A]), {self.A})
        finally:
            km.BUS_PORT = saved
            bus.shutdown()
            bus.server_close()
        self.assertEqual(self.again, {"ok": True, "injected": True})
        self.assertEqual(self.be.ids(), [self.A, self.A], "the handed-back message is delivered again, not swallowed")


if __name__ == "__main__":
    unittest.main()
