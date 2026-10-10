#!/usr/bin/env python3
"""The kernel answers every chat send, and recognises the page's resend of one (2026-10-09).

Two messages a user typed into a linked machine's session were lost without a trace: the kernel had not read them yet
when their socket closed, and nothing told the page whether they had arrived. The protocol pinned here closes that
gap from the kernel's side:

  - a sendMessage or askFollowUp carrying the press-minted id (`qid`) is answered on its socket with
    {type: "sendAck", id, qid, ok, dup} once the kernel has decided its fate (handed over or parked: ok; refused: not ok);
  - the page resends an unanswered send on its next socket; a send the kernel has already decided on is answered
    again as a duplicate and never delivered twice, also across a kernel restart (STATE/accepted-sends.jsonl), and a
    copy the session still holds under that id counts as decided;
  - an attempt that raises records nothing, so the page's resend tries again;
  - the keepalive says the kernel speaks the protocol (`acks`), and the caps frame lists `sendAck`.

Synthetic ids and texts only (the notes-api demo world).
"""
import json
import os
import tempfile
import threading
import time
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


class _SendFixture(unittest.TestCase):
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
        """The decided ids as a fresh kernel finds them before its boot reads the file: nothing in memory."""
        with km._SEND_ACCEPTS_LOCK:
            km._SEND_ACCEPTS.clear()
            km._SEND_ACCEPTS_STATE["said"] = False
            km._SEND_ACCEPTS_STATE["tailOk"] = False
            km._SEND_ACCEPTS_STATE.pop("bootReports", None)
        km._pending_ops.pop(SID, None)

    def _send(self, qid=Q1, text="add a test for the search cache", resend=False):
        m = {"type": "sendMessage", "id": SID, "text": text, "qid": qid}
        if resend:
            m["resend"] = True
        return km._drive(m, self.client)

    def acks(self):
        return [f for f in self.frames if f.get("type") == "sendAck"]


class SendAck(_SendFixture):
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
        self.assertTrue((self.state / "accepted-sends.jsonl").exists(), "the decision is on disk")
        self._reset_store()                  # a fresh kernel process: memory gone, the file stays…
        self.assertEqual(km._send_accepts_boot_load(), 1, "…and its boot reads it back (main)")
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
        self.assertEqual([(a["ok"], a.get("retry")) for a in self.acks()], [(False, True)],
                         "not delivered, retry: the page keeps the send and offers it again")
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


class SendAckOrdering(_SendFixture):
    """The copies of one send that arrive out of order or at once (the review of #198, 2026-10-09): every socket reads on its
    own thread, so the page's resend on a new socket can be read before the original on the old one, and a second tab can
    restore and resend the first tab's send. Counted at the session (what the backend was handed), not at the answers."""

    def _client(self):
        frames = []
        return {"app": "chat", "wid": "w2", "alive": True, "send": lambda s: frames.append(json.loads(s))}, frames

    def test_an_original_read_after_its_resend_is_delivered_once(self):
        self._send(resend=True)              # the new socket's resend, read first
        self._send()                         # the old socket's original, read after it
        self.assertEqual(len(self.be.sent), 1, "the session got the message once")
        self.assertEqual([(a["ok"], a["dup"]) for a in self.acks()], [(True, False), (True, True)])

    def test_two_copies_arriving_at_once_are_delivered_once_and_both_hear_the_outcome(self):
        gate, entered = threading.Event(), threading.Event()
        real = self.be.send

        def slow(sid, text, qid=None, user=False, paths=None):
            entered.set()
            gate.wait(5)                     # the first copy is mid-delivery…
            return real(sid, text, qid=qid, user=user, paths=paths)
        self.be.send = slow
        t = threading.Thread(target=self._send)
        t.start()
        self.assertTrue(entered.wait(5))
        other, frames = self._client()
        km._drive({"type": "sendMessage", "id": SID, "text": "add a test for the search cache", "qid": Q1, "resend": True}, other)
        self.assertEqual(frames, [], "…so the second is not answered ok before the outcome is known")
        gate.set()
        t.join(5)
        self.assertEqual(len(self.be.sent), 1, "delivered once")
        self.assertEqual([(f["ok"], f["dup"]) for f in frames], [(True, True)], "the waiting copy hears the outcome")

    def test_a_copy_waiting_on_a_refused_original_hears_the_refusal(self):
        gate, entered = threading.Event(), threading.Event()

        def refuse(sid, text, qid=None, user=False, paths=None):
            entered.set()
            gate.wait(5)
            return False
        self.be.send = refuse
        t = threading.Thread(target=self._send)
        t.start()
        self.assertTrue(entered.wait(5))
        other, frames = self._client()
        km._drive({"type": "sendMessage", "id": SID, "text": "add a test for the search cache", "qid": Q1, "resend": True}, other)
        gate.set()
        t.join(5)
        self.assertEqual([(f["ok"], f["dup"]) for f in frames], [(False, True)],
                         "refused: the resending page is told so, never ok, so it keeps the words (before: ok, and the copy dropped)")

    def test_the_decisions_on_disk_are_whole(self):
        self._send()
        rows = [json.loads(ln) for ln in (self.state / "accepted-sends.jsonl").read_text().splitlines()]
        self.assertEqual([(r["qid"], r["sid"], r["ok"]) for r in rows], [(Q1, SID, True)])
        self.assertEqual([p.name for p in self.state.iterdir() if ".tmp." in p.name], [], "no temp file left behind")


class WithdrawnBeforeItArrived(_SendFixture):
    """The ✎ or the ✕ on the page's own unconfirmed bubble, for a send this kernel never received (its frame lost in a socket
    that closed): the withdrawal holds, the words stay in the box, and a copy that arrives later is not delivered."""

    def _cancel(self, qid=Q1, text="add a test for the search cache"):
        km._drive({"type": "cancelQueued", "id": SID, "md": text, "qid": qid}, self.client)
        return [f for f in self.frames if f.get("type") == "cancelResult"][-1]

    def test_a_withdrawal_of_a_send_never_received_holds_and_a_late_copy_is_refused(self):
        res = self._cancel()
        self.assertTrue(res["ok"], "not 'too late': it never arrived, so nothing went through (before: ok false, and the box emptied)")
        self._send(resend=True)              # the old socket's late flush, or another tab's resend
        self.assertEqual(self.be.sent, [], "the withdrawn message is not delivered")
        self.assertEqual((self.acks()[-1]["ok"], self.acks()[-1]["dup"]), (False, True))

    def test_a_withdrawal_after_the_send_went_through_is_still_too_late(self):
        self._send()
        res = self._cancel()
        self.assertFalse(res["ok"], "handed over: no recall exists")


class Round3(_SendFixture):
    """The review of #200 (2026-10-09): a malformed record must never stop the kernel; a check that cannot be made must
    never read as delivered; a copy waiting on an attempt that raises is answered; the record is an appended log."""

    def _write_log(self, text):
        (self.state / "accepted-sends.jsonl").write_text(text)

    def test_a_malformed_row_or_a_garbage_file_never_stops_the_kernel(self):
        good = {"qid": Q1, "sid": SID, "t": int(time.time()) - 60, "ok": True}   # inside the seven days the boot's compaction keeps
        self._write_log(json.dumps(good) + "\n" + json.dumps(dict(good, qid=Q2, t="soon")) + "\nnot json at all\n"
                        + json.dumps({"qid": "s-1", "ok": True}) + "\n" + json.dumps(dict(good, qid="echo:" + "3" * 32, t=float("nan"))).replace("NaN", "1e999") + "\n")
        (self.state / "accepted-sends.json").write_text("\x00\x01 garbage, not json")
        self.assertEqual(km._send_accepts_boot_load(), 1, "the good row kept; boot does not raise")
        km._send_accepts_boot_report()       # what the load held, said once the boot passes have run (main)
        self.assertIn(Q1, km._SEND_ACCEPTS)
        self.assertNotIn(Q2, km._SEND_ACCEPTS, "the row whose time is not a number is skipped")
        rows = [json.loads(ln) for ln in (self.state / "session-events.jsonl").read_text().splitlines()]
        prob = [r for r in rows if r.get("kind") == "send-acks.unreadable"]
        self.assertEqual(len(prob), 1, "said once, as a problem row")
        self.assertEqual((prob[0]["badRows"], prob[0]["kept"]), (4, 1))
        # …and the whole-file form #198 wrote, holding the same bad row: it raised out of main() and the kernel could not start
        self._reset_store()
        (self.state / "accepted-sends.jsonl").unlink()
        (self.state / "accepted-sends.json").write_text(json.dumps({"accepted": [good, dict(good, qid=Q2, t="soon")]}))
        self.assertEqual(km._send_accepts_boot_load(), 1, "the good row kept, the bad one skipped")

    def test_a_check_that_cannot_be_made_is_not_taken_for_delivered(self):
        real = self.be.pending_queued_meta
        self.be.pending_queued_meta = lambda sid: (_ for _ in ()).throw(RuntimeError("queue unreadable"))
        self._send()
        self.assertEqual(self.be.sent, [], "not delivered, and not claimed to be")
        self.assertEqual([(a["ok"], a.get("retry")) for a in self.acks()], [(False, True)], "the page is told: not delivered, retry")
        self.assertNotIn(Q1, km._SEND_ACCEPTS, "nothing recorded")
        self.be.pending_queued_meta = real
        self._send(resend=True)
        self.assertEqual(len(self.be.sent), 1, "the page's next offer is delivered (before: answered 'delivered' forever, never sent)")

    def test_a_copy_waiting_on_an_attempt_that_raises_is_answered(self):
        gate, entered = threading.Event(), threading.Event()

        def boom(sid, text, qid=None, user=False, paths=None):
            entered.set()
            gate.wait(5)
            raise RuntimeError("the backend fell over")
        self.be.send = boom
        errs = []

        def first():
            try:
                self._send()
            except RuntimeError as e:
                errs.append(e)
        t = threading.Thread(target=first)
        t.start()
        self.assertTrue(entered.wait(5))
        frames = []
        other = {"app": "chat", "wid": "w2", "alive": True, "send": lambda s: frames.append(json.loads(s))}
        km._drive({"type": "sendMessage", "id": SID, "text": "add a test for the search cache", "qid": Q1, "resend": True}, other)
        gate.set()
        t.join(5)
        self.assertEqual(len(errs), 1)
        self.assertEqual([(a["ok"], a.get("retry")) for a in self.acks()], [(False, True)], "the original's page hears it")
        self.assertEqual([(f["ok"], f.get("retry")) for f in frames], [(False, True)], "and so does the waiting copy (before: neither)")

    def test_the_record_is_an_appended_log_compacted_from_the_live_decisions(self):
        (self.state / "accepted-sends.json").write_text(json.dumps({"accepted": [{"qid": Q2, "sid": SID, "t": int(time.time()) - 60, "ok": True}]}))
        self.assertEqual(km._send_accepts_boot_load(), 1, "#198's whole-file form is still read")
        self.assertFalse((self.state / "accepted-sends.json").exists(), "and folded into the log at boot (the review of #201)")
        self._send()
        self._send(qid="echo:" + "4" * 32)
        lines = (self.state / "accepted-sends.jsonl").read_text().splitlines()
        self.assertEqual([json.loads(ln)["qid"] for ln in lines], [Q2, Q1, "echo:" + "4" * 32], "one line per decision, appended")
        with km._SEND_WRITE_LOCK:
            km._send_log_compact_locked(time.time())
        rows = [json.loads(ln)["qid"] for ln in (self.state / "accepted-sends.jsonl").read_text().splitlines()]
        self.assertEqual(sorted(rows), sorted([Q2, Q1, "echo:" + "4" * 32]), "the compaction keeps every live decision")
        self.assertFalse((self.state / "accepted-sends.json").exists(), "and retires the whole-file form")


class Round4(_SendFixture):
    """The review of #201 (2026-10-10)."""

    def test_the_boot_load_never_builds_the_backend_and_says_its_problem_after_the_boot_passes(self):
        (self.state / "accepted-sends.jsonl").write_text(json.dumps({"qid": Q2, "sid": SID, "t": "soon", "ok": True}) + "\n")
        built = []
        saved = km._sdk
        km._sdk = lambda: built.append(1)
        try:
            km._send_accepts_boot_load()
            self.assertEqual(built, [], "the load did not build the Claude Code backend: the boot passes must come first")
            ev = self.state / "session-events.jsonl"
            self.assertFalse(ev.exists() and "send-acks.unreadable" in ev.read_text(), "the problem is held, not said yet")
            km._send_accepts_boot_report()
            self.assertIn("send-acks.unreadable", ev.read_text(), "and said once main reaches the backend's boot")
        finally:
            km._sdk = saved

    def test_a_torn_last_line_is_cut_and_the_next_decision_survives_the_next_boot(self):
        good = json.dumps({"qid": Q1, "sid": SID, "t": int(time.time()), "ok": True})
        (self.state / "accepted-sends.jsonl").write_text(good + "\n" + '{"qid": "echo:' + "2" * 20)   # a crash mid-append
        self.assertEqual(km._send_accepts_boot_load(), 1)
        self._send(qid="echo:" + "5" * 32)
        self._reset_store()
        self.assertEqual(km._send_accepts_boot_load(), 2, "both decisions read back: the new one was not written onto the torn line")
        self.assertTrue((self.state / "accepted-sends.jsonl").read_text().endswith("\n"))

    def test_an_append_never_lands_on_half_a_line_even_without_a_boot_load(self):
        (self.state / "accepted-sends.jsonl").write_text('{"qid": "echo:' + "2" * 20)
        km._SEND_ACCEPTS_STATE["tailOk"] = False
        self._send()
        self._reset_store()
        self.assertEqual(km._send_accepts_boot_load(), 1)
        self.assertIn(Q1, km._SEND_ACCEPTS)

    def test_a_withdrawal_for_a_session_this_kernel_does_not_have_resolves_quietly(self):
        other = "99999999-8888-7777-6666-555555555555"
        km._drive({"type": "cancelQueued", "id": other, "md": "rebuild the index", "qid": Q1}, self.client)
        self.assertEqual([(f["type"], f.get("ok"), f.get("gone"), f.get("qid")) for f in self.frames],
                         [("cancelResult", True, True, Q1)], "answered as done: no not-delivered dialog (before: the err frame, every reconnect)")
        self.assertFalse((self.state / "undelivered.jsonl").exists(), "and nothing filed as a lost message")

    def test_every_cancel_answer_names_the_send(self):
        km._drive({"type": "cancelQueued", "id": SID, "md": "add a test for the search cache", "qid": Q1}, self.client)
        self.assertEqual([f.get("qid") for f in self.frames if f.get("type") == "cancelResult"], [Q1])


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
