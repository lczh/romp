#!/usr/bin/env python3
"""The kernel answers every chat send, and recognises the page's resend of one (2026-10-09).

Two messages a user typed into a linked machine's session were lost without a trace: the kernel had not read them yet
when their socket closed, and nothing told the page whether they had arrived. The protocol pinned here closes that
gap from the kernel's side:

  - a sendMessage or askFollowUp carrying the press-minted id (`qid`) is answered on its socket with
    {type: "sendAck", id, qid, ok, dup} once the kernel has decided its fate (handed over or parked: ok; refused: not ok);
  - the page resends an unanswered send on its next socket; a send the kernel has already decided on is answered
    again as a duplicate and never delivered twice, also across a kernel restart (STATE/accepted-sends.json), and a
    copy the session still holds under that id counts as decided;
  - an attempt that raises records nothing, so the page's resend tries again;
  - the keepalive says the kernel speaks the protocol (`acks`), and the caps frame lists `sendAck`.

Synthetic ids and texts only (the notes-api demo world).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic state BEFORE the load: it resolves its root at import time
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_send_ack", os.path.join(BIN, "romp-kernel"))

SID = "7c1d2e3f-4a5b-4c6d-8e7f-90a1b2c3d4e5"     # private synthetic sid
Q1 = "echo:" + "1" * 32
Q2 = "echo:" + "2" * 32


class _Backend:
    """A backend that takes every send (or refuses, or raises), recording what it was handed."""

    def __init__(self):
        self.sent = []
        self.answer = True
        self.raises = None
        self.held = set()                  # ids the session holds a copy under (a queued copy, a live echo)

    def send(self, sid, text, qid=None, user=False, paths=None):
        if self.raises:
            raise self.raises
        self.sent.append((sid, text, qid))
        return self.answer

    def pending_queued_meta(self, sid):
        return [{"qid": q} for q in self.held]

    def live_atoms(self, sid):
        return []


class SendAck(unittest.TestCase):
    def setUp(self):
        self.state = Path(tempfile.mkdtemp())
        self.be = _Backend()
        self.frames = []
        self.client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda s: self.frames.append(json.loads(s))}
        names = ("_kernel_knows", "_compacting_now", "_limit_hold", "_working_now", "_push_soon", "_name_of")
        self._saved = {n: getattr(km, n) for n in names}
        self._saved_backend_for = km.Sessions.backend_for
        self._saved_state = km.jd.STATE
        km.jd.STATE = self.state
        km._kernel_knows = lambda sid: sid == SID
        km._compacting_now = lambda sid: False
        km._limit_hold = lambda sid: None
        km._working_now = lambda sid: False
        km._push_soon = lambda *a, **k: None
        km._name_of = lambda sid: "web" if sid == SID else None
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self._reset_store()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._saved_backend_for
        km.jd.STATE = self._saved_state
        self._reset_store()

    def _reset_store(self):
        """The decided ids as a fresh kernel finds them: nothing in memory, the file read again on first use."""
        with km._SEND_ACCEPTS_LOCK:
            km._SEND_ACCEPTS.clear()
            km._SEND_ACCEPTS_STATE["loaded"] = False
            km._SEND_ACCEPTS_STATE["said"] = False
        km._pending_ops.pop(SID, None)

    def _send(self, qid=Q1, text="add a test for the search cache", resend=False):
        m = {"type": "sendMessage", "id": SID, "text": text, "qid": qid}
        if resend:
            m["resend"] = True
        return km._drive(m, self.client)

    def acks(self):
        return [f for f in self.frames if f.get("type") == "sendAck"]

    def test_a_send_is_answered_once_it_is_handed_over(self):
        self.assertTrue(self._send())
        self.assertEqual(self.be.sent, [(SID, "add a test for the search cache", Q1)], "delivered once, under the press's id")
        self.assertEqual(self.acks(), [{"type": "sendAck", "id": SID, "qid": Q1, "ok": True, "dup": False}])

    def test_a_resend_of_a_delivered_send_is_answered_and_never_delivered_twice(self):
        self._send()
        self._send(resend=True)
        self.assertEqual(len(self.be.sent), 1, "the resend reached the kernel; the session got the message once")
        self.assertEqual([(a["ok"], a["dup"]) for a in self.acks()], [(True, False), (True, True)])

    def test_the_kernel_recognises_a_resend_across_its_own_restart(self):
        self._send()
        self.assertTrue((self.state / "accepted-sends.json").exists(), "the decision is on disk")
        self._reset_store()                  # a fresh kernel process: memory gone, the file stays
        self._send(resend=True)
        self.assertEqual(len(self.be.sent), 1, "a restart between the send and the page's resend delivers nothing twice")
        self.assertTrue(self.acks()[-1]["dup"])

    def test_a_copy_the_session_still_holds_counts_as_received(self):
        # a send already queued under this id (its record lost, or written by a kernel from before the store): before,
        # the kernel refused the id, minted its own and delivered the words a second time
        self.be.held.add(Q2)
        self._send(qid=Q2, resend=True)
        self.assertEqual(self.be.sent, [], "not delivered again")
        self.assertEqual(self.acks(), [{"type": "sendAck", "id": SID, "qid": Q2, "ok": True, "dup": True}])

    def test_a_refused_send_is_answered_not_ok_and_stays_refused(self):
        self.be.answer = False
        self._send()
        self.assertEqual(self.acks()[-1]["ok"], False, "refused: the page never resends it")
        self.be.answer = True
        self._send(resend=True)
        self.assertEqual(len(self.be.sent), 1, "a resend of a refused send is not delivered behind the refusal's back")
        self.assertEqual((self.acks()[-1]["ok"], self.acks()[-1]["dup"]), (False, True))

    def test_an_attempt_that_raises_records_nothing_so_the_resend_tries_again(self):
        self.be.raises = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            self._send()
        self.assertEqual(self.acks(), [], "no answer: the page keeps the send")
        self.be.raises = None
        self._send(resend=True)
        self.assertEqual(len(self.be.sent), 1, "the resend delivered it")
        self.assertEqual((self.acks()[-1]["ok"], self.acks()[-1]["dup"]), (True, False))

    def test_a_send_for_a_session_this_kernel_does_not_have_is_answered_not_ok(self):
        other = "99999999-8888-7777-6666-555555555555"
        km._drive({"type": "sendMessage", "id": other, "text": "did you get this?", "qid": Q1}, self.client)
        self.assertEqual(self.acks(), [{"type": "sendAck", "id": other, "qid": Q1, "ok": False, "dup": False}])

    def test_a_send_without_an_id_is_unchanged(self):
        km._drive({"type": "sendMessage", "id": SID, "text": "rebuild the index"}, self.client)
        self.assertEqual(len(self.be.sent), 1)
        self.assertEqual(self.acks(), [], "an id-less send (an older page) gets no answer, as before")

    def test_a_follow_up_is_answered_and_recognised(self):
        saved = (km._followup_body, km.jd.optimistic_followup, km._predict_working, km._ack_card_move)
        km._followup_body = lambda iid, title, text, injected=False: text
        km.jd.optimistic_followup = lambda *a, **k: False
        km._predict_working = lambda *a, **k: None
        km._ack_card_move = lambda *a, **k: None
        try:
            m = {"type": "askFollowUp", "itemId": SID + ":g3", "text": "and the fix?", "sid": SID, "qid": Q2}
            km._drive(m, self.client)
            km._drive(dict(m, resend=True), self.client)
        finally:
            km._followup_body, km.jd.optimistic_followup, km._predict_working, km._ack_card_move = saved
        self.assertEqual(len(self.be.sent), 1, "the follow-up reached the session once")
        self.assertEqual([(a["ok"], a["dup"]) for a in self.acks()], [(True, False), (True, True)])


class AcksAnnounced(unittest.TestCase):
    def test_the_keepalive_and_the_caps_say_the_kernel_answers_sends(self):
        got = []
        c = {"app": "chat", "alive": True, "send": lambda s: got.append(json.loads(s))}
        saved = list(km._clients)
        with km._clients_lock:
            km._clients[:] = [c]
        try:
            km._keepalive_all()
        finally:
            with km._clients_lock:
                km._clients[:] = saved
        self.assertEqual(got[0].get("type"), "ka")
        self.assertEqual(got[0].get("acks"), 1, "every keepalive says it: a page resends only on a socket that did")
        self.assertIn("sendAck", km.KERNEL_WS_CAPS)

    def test_the_page_shim_turns_the_keepalives_word_into_an_event_once_per_socket(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        # a bare membership test: assertIn would print the whole kernel source on a miss
        self.assertTrue('if(msg&&msg.type==="ka"&&msg.acks&&!acksSeen){acksSeen=true;window.__rompAcksLocal=true;'
                        'try{window.dispatchEvent(new CustomEvent("romp:hostAcks",{detail:{host:""}}));}catch(e){}}' in src,
                        "the first keepalive that says so dispatches romp:hostAcks for this page's own kernel")
        self.assertTrue('resumeProvisional=0;acksSeen=false;window.__rompAcksLocal=false;' in src,
                        "a new socket has said nothing yet")


if __name__ == "__main__":
    unittest.main()
