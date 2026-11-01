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

The retry was not the only second reader (2026-11-01). Mail put back in new/ after an unanswered post was ordinary unread
mail until the next retry pass: the turn-end drain or check_inbox handed it over a second time beside the queued banner,
and a recall withdrew it and told the sender it was recalled while the session still read it. So mail whose post went
out and got no answer stays claimed, recorded as in doubt, until the kernel answers a re-post of it (MailInDoubt below).

The bus and the kernel's real /deliver route run here over loopback; only the backend behind the route, and the loss
of the kernel's first answer, are stubbed. SYNTHETIC fixtures only: invented text, private placeholder uuids, the
notes-api demo's web and api sessions, hostname TESTHOST."""
import http.client
import itertools
import json
import os
import shutil
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
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
        self.done = threading.Event()

    def deliver(self, sid, text):
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(10)
        try:
            if not self.takes:
                return False
            self.took.append((sid, em.POSTAL_RE.findall(text)))
            return True
        finally:
            self.done.set()

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


class _Bus(_World):
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
        self.posted, self.lose, self.cap = [], 0, None
        real = self.saved[5]

        def post(path, body, timeout=2):
            # the kernel handles the request either way; `cap`, when set, is a real read timeout on the bus's side
            resp = real(path, body, timeout=self.cap if path == "/deliver" and self.cap else timeout)
            if path == "/deliver":
                self.posted.append(em.POSTAL_RE.findall(body.get("text", "")))
                if self.lose:
                    self.lose -= 1
                    return {"ok": False, "unanswered": True}   # its answer came after the bus's cap, as _kernel_post says it
            return resp
        pm._kernel_post = post

    def tearDown(self):
        if self.be.hold is not None:
            self.be.hold.set()
            self.be.done.wait(10)
        (pm.KERNEL_BASE, pm.SERVE_TOKEN, pm._push_disabled, pm._log, pm.local_agents, pm._kernel_post) = self.saved
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm.STREAKS.pop(WEB, None)
        shutil.rmtree(pm.MAILROOT / WEB, ignore_errors=True)   # every test starts from an empty box and no record
        (pm.MAILDOUBT / WEB).unlink(missing_ok=True)
        pm._mark_pending(WEB)
        super().tearDown()

    def _box(self):
        return [m.get("id") for m in pm.read_box(WEB, consume=False)]


class TheBusRetryAfterALateAnswer(_Bus):
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
        self.assertTrue(any("deferred (no answer from the kernel)" in ln for ln in self.logged), self.logged)
        self.assertEqual(self._box(), [], "kept claimed, in doubt: the kernel may have queued it")
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
            self.assertEqual(answers.get("second"), {"ok": True, "injected": False, "busy": True},
                             "not delivered beside the first copy, and not claimed as taken before the first "
                             "copy is: busy, so the bus keeps it in doubt and its retry learns how the first one ended")
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


class MailInDoubt(_Bus):
    """Mail whose post went out and got no answer is the kernel's until it answers for it: no other reader takes it."""

    def _send(self, body="please run the notes-api migration"):
        return pm.deliver(WEB, "api", PEER, body, kind="delegate")

    def _turn_end_drain(self):
        """What the Stop hook hands the session when its turn ends: GET /drain, which is _drain(sid)."""
        pm.STREAKS.pop(WEB, None)
        return [m["id"] for m in pm._drain(WEB).get("messages", [])]

    def _in_doubt(self):
        return pm._doubt_read(WEB)

    def test_the_turn_end_drain_does_not_hand_over_mail_the_kernel_may_have_queued(self):
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertEqual(self.be.ids(), [mid], "the kernel queued the banner; only its answer was lost")
        self.assertEqual(self._turn_end_drain(), [],
                         "the session's turn ends before the next retry pass: on main the drain handed it over again")
        self.assertEqual(self._in_doubt(), [mid], "recorded on disk, so a restart of the bus does not strand it")
        pm._retry_pending()
        self.assertEqual(self.posted, [[mid], [mid]], "the retry asks the kernel about it, and the kernel answers taken")
        self.assertEqual(self.be.ids(), [mid], "the session gets it once")
        self.assertEqual((self._in_doubt(), self._box()), ([], []), "settled: nothing in doubt, nothing left unread")

    def test_check_inbox_does_not_hand_over_mail_the_kernel_may_have_queued(self):
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertEqual([m["id"] for m in pm.read_box(WEB, consume=True)], [], "GET /inbox, what check_inbox reads")
        pm._retry_pending()
        self.assertEqual(self.be.ids(), [mid])

    def test_a_recall_does_not_withdraw_mail_the_kernel_may_have_queued(self):
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertEqual(pm._recall(PEER, "web", mid), [],
                         "on main the recall removed it and told the sender it was recalled before it was read")
        pm._retry_pending()
        self.assertEqual(self.be.ids(), [mid], "the queued banner delivers it, as the answer to the recall implied")

    def test_a_real_read_timeout_while_the_kernel_still_hands_it_over(self):
        # The order under load: the bus's read times out while the route is still inside the backend's deliver (a slow
        # revive), the retry pass meanwhile is answered busy, the turn ends, and only then is the banner queued.
        self.be.hold = threading.Event()
        self.cap = 0.5
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertTrue(self.be.entered.wait(5), "the post reached the backend")
        pm._retry_pending()                                # the kernel answers busy: still in doubt
        self.assertEqual(self._turn_end_drain(), [], "on main the busy answer put it back and the drain took it")
        self.assertTrue(any("deferred (no answer from the kernel)" in ln for ln in self.logged), self.logged)
        self.assertEqual(self.posted, [[mid], [mid]])
        self.assertEqual(self._in_doubt(), [mid], "busy is not an answer about this mail")
        self.be.hold.set()                                 # the revive finishes and the banner is queued
        self.assertTrue(self.be.done.wait(10))
        self.cap = None
        pm._retry_pending()
        self.assertEqual(self.be.ids(), [mid])
        self.assertEqual((self._in_doubt(), self._box()), ([], []))

    def test_a_first_post_answered_busy_is_kept_in_doubt(self):
        # An earlier post of the same message is still inside the kernel (one the bus lost track of): its answer is
        # still to come, so the mail is not put back where the drain takes it.
        mid = self._send()
        self.be.hold = threading.Event()
        first = threading.Thread(target=self._deliver, args=(_banner(mid),))
        first.start()
        self.assertTrue(self.be.entered.wait(5))
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertEqual(self._turn_end_drain(), [])
        self.assertTrue(any("deferred (an earlier post of it still in the kernel)" in ln for ln in self.logged),
                        self.logged)
        self.be.hold.set()
        first.join(10)
        pm._retry_pending()
        self.assertEqual(self.be.ids(), [mid])
        self.assertEqual(self._in_doubt(), [])

    def test_mail_the_kernel_did_not_take_goes_back_for_the_drain(self):
        # The late answer was not-taken: a re-post is answered about this mail, so it goes back to the drain under its
        # own id rather than staying claimed.
        self.be.takes = False
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        self.assertEqual(self._turn_end_drain(), [])
        pm._retry_pending()
        self.assertEqual(self._in_doubt(), [])
        self.assertEqual(self._box(), [mid], "back in new/ under its own id")
        self.assertEqual(self._turn_end_drain(), [mid], "and the drain backstop delivers it")
        self.assertEqual(self.be.ids(), [])

    def test_mail_the_kernel_hands_back_is_no_longer_in_doubt(self):
        # A banner fed and lost (SdkSession._return_stranded_mail): the kernel forgets the ids and the bus puts the mail
        # back. It is pending mail now; once the drain has delivered it, the retry must not post it a second time.
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        km._postal_untake(WEB, [mid])
        saved = pm._wake_when_ready
        pm._wake_when_ready = lambda sid: None
        try:
            out, status = pm.restore_stranded({"id": WEB, "mids": [mid]})
        finally:
            pm._wake_when_ready = saved
        self.assertEqual((status, out["restored"]), (200, [mid]))
        self.assertEqual(self._in_doubt(), [])
        self.assertEqual(self._turn_end_drain(), [mid], "the re-delivery the hand-back asks for")
        pm._retry_pending()
        self.assertEqual(self.posted, [[mid]], "not posted again beside the drain")

    def test_with_the_push_turned_off_mail_in_doubt_goes_back_for_the_drain(self):
        self.lose = 1
        mid = self._send()
        self.assertFalse(pm._push(WEB, {"id": WEB, "state": "working"}))
        pm._push_disabled = lambda: True
        pm._retry_pending()
        self.assertEqual((self._in_doubt(), self._box()), ([], [mid]),
                         "no push will ask the kernel again: a possible duplicate beats mail nobody receives")


class KernelPostTellsNoAnswerFromNoKernel(unittest.TestCase):
    """_kernel_post: a request that went out and got no answer is not a kernel that could not be reached."""

    def setUp(self):
        self._seam = os.environ.pop("ROMP_SESSIONS_FILE", None)
        self.saved = (urllib.request.urlopen, pm._log, pm._kernel_post)
        self.logged = []
        pm._log = self.logged.append

    def tearDown(self):
        urllib.request.urlopen, pm._log, pm._kernel_post = self.saved
        if self._seam is not None:
            os.environ["ROMP_SESSIONS_FILE"] = self._seam
        pm._DASHBOARD_MISSED[0] = False

    def _post(self, exc):
        def urlopen(req, timeout=None):
            raise exc
        urllib.request.urlopen = urlopen
        return pm._kernel_post("/deliver", {"id": WEB, "text": "hello"})

    def test_a_read_timeout_or_a_dropped_connection_is_unanswered(self):
        self.assertEqual(self._post(socket.timeout("timed out")), {"ok": False, "unanswered": True})
        self.assertEqual(self._post(ConnectionResetError(104, "reset")), {"ok": False, "unanswered": True})

    def test_a_fault_before_the_request_went_out_is_unreachable(self):
        self.assertIsNone(self._post(urllib.error.URLError(ConnectionRefusedError(111, "refused"))))
        self.assertIsNone(self._post(urllib.error.URLError(socket.timeout("connect timed out"))))

    def test_an_unanswered_notice_reads_as_unanswered_not_refused(self):
        pm._DASHBOARD_MISSED[0] = False
        pm._kernel_post = lambda path, body, timeout=2: {"ok": False, "unanswered": True}
        pm._refused_notice("a synthetic notice")
        self.assertIn("the kernel did not answer", self.logged[-1])


if __name__ == "__main__":
    unittest.main()
