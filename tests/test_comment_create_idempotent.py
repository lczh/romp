#!/usr/bin/env python3
"""T289: a comment CREATE is idempotent on its own identity.

A create parked for transcript lag is retried by BOTH the kernel (the pusher's _retry_parked_creates) and
the client (its re-post on a reconnect), and a create whose ack was lost is sent again by the popover; before
this the second copy either collided on its explicit name and was refused with the create toast the user
saw, or, with the name now left to the kernel, would have minted a SECOND thread for one comment. The
kernel remembers each create it completed and answers a repeat with the SAME thread's ack; a lag-parked
create is parked once.

The identity is the client's createId when the frame carries one: the popover mints it at the send gesture
and every re-post of that gesture carries the same one, so a repeat is exactly a frame with an id the kernel
has seen. Keyed on the words instead (parent sid, anchor uuid, passage, text), the memo also swallowed a
DELIBERATE second comment in the same words on the same passage: one thread, two acks, the second name
nowhere on disk (review of the memo, 2026-09-09). A frame without a createId still keys on the words.
SYNTHETIC fixtures."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "aaaaaaaa-1111-2222-3333-444444444444"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


class FakeBackend:
    def __init__(self):
        self.calls = []
        # the live tail (2026-10-11): the lagging anchor these tests comment on is a reply that STREAMED and whose record the
        # CLI has not written yet, so it is a work atom of the session's tail, the event a park waits on (_anchor_streaming)
        self.streaming = {"a9"}

    def live_atoms(self, sid):
        return [{"type": "assistant", "uuid": u} for u in sorted(self.streaming)]

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="", model="", effort="", fast=""):
        self.calls.append(("fork", name)); return sid

    def connect(self, sid):
        return True

    def send(self, sid, text):
        return True

    def kill(self, sid):
        return True

    def rename(self, sid, name):
        self.calls.append(("rename", name)); return True

    def promote_thread(self, sid, name, bg="", fg=""):
        self.calls.append(("promote", name)); return True


class _Harness(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_msgs_cache.clear()
        # the create memos start empty as well (review, 2026-09-09): the tests reuse the same create ids
        # under one parent sid, so an id a previous test noted would name a thread id this test's fresh
        # store can hold too, and a fresh gesture here would be answered as a repeat; random thread ids
        # keep that from happening by chance today, and the clears make the order not matter. The noted
        # creates are the memo a passing test leaves behind; the in-flight set and the parked list are
        # empty at the end of every passing test, so their clears keep a test that failed part-way from
        # failing the next one too. Under the lock every writer of the three memos takes.
        with km._create_lock:
            km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
            km._dropped_creates.clear()                  # a dropped gesture's answer (2026-10-11) is a memo of the same kind
        self.now = int(time.time())
        cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / PARENT).write_text("web\t%s\t\t\n" % cdir)   # the names row: the refusal names the parent by it
        self.be = FakeBackend()
        self._saved_fns = (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
                           km._push_session_now, km._live_map, km._kernel_knows, km._comment_create_spawn)
        km._comment_create_spawn = lambda target, args: target(*args)   # the door's create runs inline: answers in order
        km._sdk = lambda: None
        km._kernel_knows = lambda sid: sid == PARENT     # the dispatcher's ownership guard is not under test
        self._saved_km_names = km.NAMES
        km.NAMES = jd.NAMES                              # the kernel's own binding of the names dir follows the rebind
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._live_map = lambda: {}
        t = self.now - 600
        p = self.proj / (PARENT + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in [
            uline(t, "how should the notes-api retry loop back off?", "u1"),
            aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1")]) + "\n")
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "web", "path": str(p), "mtime": self.now}]
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s)), "app": "chat"}

    def tearDown(self):
        # …and they end empty too (review of PR 211, 2026-10-11): a drop no fake pane acknowledged stays on offer, and every
        # module in the run shares this kernel, so another module's pane saying ready would be handed this test's dialog
        with km._create_lock:
            km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
            km._dropped_creates.clear()
        (km._sdk, km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for,
         km._push_session_now, km._live_map, km._kernel_knows, km._comment_create_spawn) = self._saved_fns
        km.NAMES = self._saved_km_names
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _drive(self, msg):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            handled = km._drive(msg, self.client)
        return handled, buf.getvalue()

    def _warns(self):
        return [f["text"] for f in self.sent if f.get("type") == "warn"]

    def _create(self, text="Why jitter at all?", uuid="a1", name="", create_id=None):
        msg = {"type": "commentCreate", "id": PARENT, "uuid": uuid, "exact": "exponential backoff",
               "text": text, "name": name}
        if create_id is not None:
            msg["createId"] = create_id                 # the client's per-gesture stamp (absent from an older client)
        return self._drive(msg)

    def _rows(self):
        return km._load_comments(PARENT).get("threads") or []

    def _acks(self):
        return [f for f in self.sent if f.get("type") == "commentCreated"]


class CreateIsIdempotent(_Harness):
    def test_the_same_create_twice_yields_one_thread_and_two_acks_naming_it(self):
        self._create(); self._create()
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        acks = self._acks()
        self.assertEqual(len(acks), 2, "the repeat is answered, so the popover adopts the thread")
        self.assertEqual(acks[0]["tid"], acks[1]["tid"])
        self.assertEqual(self._warns(), [])

    def test_a_different_comment_on_the_same_passage_is_a_second_thread(self):
        self._create("Why jitter at all?"); self._create("And the cap?")
        self.assertEqual(len(self._rows()), 2)
        self.assertEqual(len({a["tid"] for a in self._acks()}), 2)

    def test_a_lag_parked_create_is_parked_once(self):
        # the anchor is not in the transcript yet: the create parks, the client hears a transient nack and
        # re-posts on the next frame — the park must not double
        km._parked_creates.clear()
        self._create(uuid="a9"); self._create(uuid="a9")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one create")
        km._parked_creates.clear()

    def test_a_parked_create_that_landed_answers_the_clients_repost_with_the_same_thread(self):
        km._parked_creates.clear()
        self._create(uuid="a9")
        self.assertEqual(len(km._parked_creates), 1)
        # the transcript catches up: the pusher's retry lands the thread
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        # the client's own re-post of the same create arrives a beat later: the same thread, no second one
        self._create(uuid="a9")
        self.assertEqual(len(self._rows()), 1, "the repeat is the same comment")
        acks = self._acks()
        self.assertTrue(acks and acks[-1]["tid"] == rows[0]["tid"], acks)
        self.assertEqual(self._warns(), [])

    def _write_unrelated(self, n, parent):
        """`n` records that are not the anchor, appended to the parent's transcript, each followed by two retry passes (the second
        sees no new write); returns the last record's uuid, the chain's new leaf."""
        p = Path(km._sessions(0)[0]["path"])
        for i in range(n):
            u = "w%02d" % i
            with p.open("a") as fh:
                fh.write(json.dumps(aline(self.now - 400 + i, "the notes-api step %d" % i, u, parent=parent)) + "\n")
            parent = u
            km._retry_parked_creates()
            km._retry_parked_creates()
        return parent

    def test_a_parked_creates_budget_counts_its_transcripts_writes_not_the_retries(self):
        # (2026-10-11) the budget counted retries, sized for pusher cycles, and every needFull's repair ran the retry too, so a
        # page's idle prefetch (37 repairs on the measured board) dropped a parked create in seconds, before its anchor landed:
        # a silent loss. A try is spent now only when the transcript was written since the last one, the event the park waits on
        km._parked_creates.clear()
        self._create(uuid="a9")
        self.assertEqual(len(km._parked_creates), 1, "premise: the anchor is not on disk yet, so the create parked")
        for _ in range(km._PARK_MAX_TRIES + 10):         # more passes than the budget, the transcript quiet
            km._retry_parked_creates()
        self.assertEqual([pk["tries"] for pk in km._parked_creates], [0], "a quiet transcript spends nothing: the park stands")
        leaf = self._write_unrelated(km._PARK_MAX_TRIES - 1, "a1")
        self.assertEqual([pk["tries"] for pk in km._parked_creates], [km._PARK_MAX_TRIES - 1],
                         "one try per write that still lacks the record, however many passes ran")
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:                            # the record lands at last
            fh.write(json.dumps(aline(self.now - 100, "Add a jitter to the backoff.", "a9", parent=leaf)) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [], "created once it landed")
        self.assertEqual(len(self._rows()), 1)

    def test_a_parked_create_leaves_the_park_after_the_budgets_writes_without_its_record(self):
        km._parked_creates.clear()
        self._create(uuid="a9")
        self.assertEqual(len(km._parked_creates), 1)
        self._write_unrelated(km._PARK_MAX_TRIES - 1, "a1")
        self.assertEqual(len(km._parked_creates), 1, "one write short of the budget: still parked")
        with km._clients_lock:
            saved_clients = list(km._clients)
            km._clients[:] = [self.client]
        try:
            self._write_unrelated(1, "w%02d" % (km._PARK_MAX_TRIES - 2))
        finally:
            with km._clients_lock:
                km._clients[:] = saved_clients
        self.assertEqual(km._parked_creates, [], "the budget's last write without the record: the record isn't coming")
        self.assertEqual(self._rows(), [])
        last = [f for f in self.sent if f.get("type") == "commentCreateFailed"][-1]
        self.assertEqual((last["transient"], last.get("dropped")), (False, True),
                         "the drop is said to the page, never a silent loss (review of PR 209, 2026-10-11)")

    def test_a_repost_arriving_while_the_pushers_retry_creates_is_not_a_second_thread(self):
        # THE RACE (review, 2026-09-09): the pusher sends the chat frame before it retries parked creates,
        # the client re-posts the create on that frame, and the re-post lands on a WS thread while the
        # pusher's own create is in flight — neither copy is noted yet, so both minted. The identity must
        # be reserved before the create runs, on both doors, and the pusher must consult the memo.
        import threading
        km._parked_creates.clear()
        self._create(uuid="a9")                       # lag-parked (the anchor is not on disk yet)
        self.assertEqual(len(km._parked_creates), 1)
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        real = km._comment_create
        seen = {"n": 0, "nested": None}

        def racing(*a, **k):
            seen["n"] += 1
            if seen["n"] == 1:
                # the pusher's create is in flight: the client's re-post arrives on another thread NOW
                before = len(self.sent)
                t = threading.Thread(target=lambda: self._create(uuid="a9"))
                t.start(); t.join(10)
                seen["nested"] = self.sent[before:]
            return real(*a, **k)
        km._comment_create = racing
        try:
            km._retry_parked_creates()
        finally:
            km._comment_create = real
        self.assertEqual(seen["n"], 1, "the re-post never reached a second create: %r" % (seen["nested"],))
        self.assertEqual(len(self._rows()), 1, "one comment, one thread")
        kinds = [f.get("type") for f in (seen["nested"] or [])]
        self.assertTrue("commentCreateFailed" in kinds or "commentCreated" in kinds,
                        "the re-post is answered while the pusher's copy is in flight (a typed transient nack, or the ack): %r" % kinds)
        self.assertEqual(km._parked_creates, [])
        self.assertEqual(self._warns(), [])

    def test_a_repeat_after_the_thread_was_resolved_is_a_new_comment_not_a_dropped_one(self):
        # a resolved (or merged, or promoted) thread cannot take a message: answering a same-worded comment
        # with its ack would drop the user's words silently (review, 2026-09-09)
        self._create()
        tid = self._acks()[0]["tid"]
        self.assertIsNone(km._comment_resolve(PARENT, tid))
        self._create()
        rows = self._rows()
        self.assertEqual(len(rows), 2, "a second, open thread for the re-asked comment")
        self.assertEqual(self._warns(), [])

    def test_a_fresh_gesture_in_the_same_words_on_the_same_passage_is_a_second_thread(self):
        # the user posts a comment, takes the ack, and posts the SAME words on the same passage again under
        # another name: two gestures, two comments. Keyed on the words alone the memo answered the second
        # with the first thread's ack, and the second name was nowhere on disk (review, 2026-09-09).
        self._create(name="first-look", create_id="g-1111")
        first = self._acks()[-1]["tid"]
        self._create(name="second-look", create_id="g-2222")
        rows = self._rows()
        self.assertEqual(len(rows), 2, "two gestures are two comments, whatever their words")
        self.assertEqual(sorted(r["name"] for r in rows), ["first-look", "second-look"], "both names on disk")
        acks = self._acks()
        self.assertEqual(len(acks), 2)
        self.assertNotEqual(acks[1]["tid"], first, "the second ack names the second thread")
        self.assertEqual(self._warns(), [])

    def test_a_retried_frame_with_the_same_create_id_is_one_thread_answered_twice(self):
        # the re-post side of the same rule: a frame the client sends again (a lost ack, a lag retry) carries
        # the id it minted at the gesture, and the kernel answers it with the thread that gesture made
        self._create(create_id="g-1111"); self._create(create_id="g-1111")
        self.assertEqual(len(self._rows()), 1, "one gesture, one thread")
        acks = self._acks()
        self.assertEqual(len(acks), 2, "the repeat is answered, so the popover adopts the thread")
        self.assertEqual(acks[0]["tid"], acks[1]["tid"])
        self.assertEqual(self._warns(), [])

    def test_a_fresh_gesture_after_a_parked_create_landed_is_a_second_thread_and_its_repost_is_not(self):
        # the parked copy carries the gesture's id through the pusher's retry: the client's re-post of THAT
        # gesture is the same comment, and a new gesture in the same words a moment later is a new one
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1)
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        landed = self._rows()
        self.assertEqual(len(landed), 1)
        self._create(uuid="a9", create_id="g-1111")   # the client's own re-post of the parked gesture
        self.assertEqual(len(self._rows()), 1, "the re-post is the same comment")
        self.assertEqual(self._acks()[-1]["tid"], landed[0]["tid"])
        self._create(uuid="a9", create_id="g-2222")   # a new gesture, same words, same passage
        rows = self._rows()
        self.assertEqual(len(rows), 2, "a fresh gesture is a second thread")
        self.assertNotEqual(self._acks()[-1]["tid"], landed[0]["tid"])
        self.assertEqual(self._warns(), [])

    def test_two_gestures_in_the_same_words_are_both_parked_while_the_anchor_lags(self):
        # two comments posted before the anchor reached the transcript park separately and land as two
        # threads; the same gesture posted twice still parks once
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111"); self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one gesture")
        self._create(uuid="a9", create_id="g-2222")
        self.assertEqual(len(km._parked_creates), 2, "a second gesture is its own parked create")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 3)
        self.assertTrue(all(n["transient"] for n in nacks))
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [])
        self.assertEqual(len(self._rows()), 2, "both gestures landed as threads")
        self.assertEqual(self._warns(), [])

    def test_a_frame_without_a_create_id_still_keys_on_the_words(self):
        # an older client sends no id: the words identity stands for it, and a stamped frame in the same
        # words is not answered from the words memo (nor the other way round)
        self._create(); self._create()
        self.assertEqual(len(self._rows()), 1)
        self._create(create_id="g-1111")
        self.assertEqual(len(self._rows()), 2, "a stamped gesture is not a repeat of an unstamped one")
        self._create()
        self.assertEqual(len(self._rows()), 2, "and the unstamped repeat still answers from its own memo")
        self.assertEqual(self._warns(), [])

    def test_a_repost_of_a_parked_gesture_is_refused_at_the_door_before_the_create_runs(self):
        # the busy nack for a re-post of a parked gesture comes from the reservation, which finds the parked
        # copy by the gesture's id, so the create never runs a second time (compared by its words instead, a
        # stamped re-post would go free, re-run the create into the lag and rely on the park-once check)
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-1111")
        self.assertEqual(len(km._parked_creates), 1)
        real = km._comment_create
        calls = []

        def counting(*a, **k):
            calls.append(a)
            return real(*a, **k)
        km._comment_create = counting
        try:
            self._create(uuid="a9", create_id="g-1111")
        finally:
            km._comment_create = real
        self.assertEqual(calls, [], "the re-post is busy at the door; no second create")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        self.assertEqual(len(km._parked_creates), 1)
        km._parked_creates.clear()

    def test_a_repost_landing_between_the_doors_release_and_its_park_is_parked_once(self):
        # the door releases its reservation before it parks, and the park-once check covers that window: a
        # re-post of the same gesture on another server thread lands in it, runs the create into the lag and
        # parks first; this door then finds that parked copy by the gesture's id and parks no twin (the other
        # door runs whole inside the window here, in place of a second thread, so the order is exact)
        km._parked_creates.clear()
        real = km._release_create
        raced = []

        def release_then_race(key):
            real(key)
            if not raced:
                raced.append(key)
                self._create(uuid="a9", create_id="g-1111")
        km._release_create = release_then_race
        try:
            self._create(uuid="a9", create_id="g-1111")
        finally:
            km._release_create = real
        self.assertEqual(len(raced), 1)
        self.assertEqual(len(km._parked_creates), 1, "one parked copy for one gesture, whichever door parked it")
        nacks = [f for f in self.sent if f.get("type") == "commentCreateFailed"]
        self.assertEqual(len(nacks), 2)
        self.assertTrue(all(n["transient"] for n in nacks))
        km._parked_creates.clear()

    def test_an_answered_repeat_is_said_in_the_kernel_log(self):
        self._create()
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            km._drive({"type": "commentCreate", "id": PARENT, "uuid": "a1", "exact": "exponential backoff",
                       "text": "Why jitter at all?", "name": ""}, self.client)
        self.assertIn("comment create repeated", buf.getvalue(), "a collapse is visible, never silent")

    def test_every_answer_names_the_gesture_it_answers(self):
        # 2026-10-01: the page held its creates by the anchor turn, so an ack for one comment adopted the new thread into
        # a second comment's open box on the same session (the half-typed second comment was lost) and retired the
        # second's hold. Every answer now echoes the gesture's createId: the page moves exactly that send's state.
        def last(kind):
            return [f for f in self.sent if f.get("type") == kind][-1]
        self._create(create_id="g-created")
        self.assertEqual(last("commentCreated")["createId"], "g-created", "a thread made")
        self._create(create_id="g-created")
        self.assertEqual(last("commentCreated")["createId"], "g-created", "a repeat answered with the same thread")
        km._parked_creates.clear()
        self._create(uuid="a9", create_id="g-parked")
        self.assertEqual(last("commentCreateFailed")["createId"], "g-parked", "a lag-parked create's transient nack")
        self._create(uuid="a9", create_id="g-parked")
        self.assertEqual(last("commentCreateFailed")["createId"], "g-parked", "a repost of the parked one, refused at the door")
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        with km._clients_lock:
            saved_clients = list(km._clients)
            km._clients[:] = [self.client]
        try:
            km._retry_parked_creates()
        finally:
            with km._clients_lock:
                km._clients[:] = saved_clients
        self.assertEqual(last("commentCreated")["createId"], "g-parked", "the parked create's broadcast when it lands")
        self._create(text="And the cap?", name="not a name", create_id="g-refused")   # a name the kernel refuses
        self.assertEqual(last("commentCreateFailed")["createId"], "g-refused", "a refusal")
        self.assertFalse(last("commentCreateFailed")["transient"])
        self._create(text="An older client", create_id=None)
        self.assertEqual(last("commentCreated")["createId"], "", "a frame with no id is answered with an empty one")



WORDS = "Why a ten percent jitter and not a fixed delay?"


class ParkEndsInAnAnswer(_Harness):
    """A parked create ends in an answer every page hears, on the event it waits for (review of PR 209, 2026-10-11).

    The park waits for its anchor's record, and that record comes while the anchor is a streamed message the CLI has not
    written (a work atom of the session's live tail) or not at all: an atom leaves the tail only once its record is on disk
    or its turn is over. Keyed on the transcript's writes alone, a quiet session's park (its turn over, nothing written)
    waited forever, never dropped and never said, and the page's own give-up counted that session's frames, so its mark
    pended forever too; where the page did give up, it handed the words back while the park could still make the thread
    later, and a user who sent them again got two threads; after a reload nothing said the drop at all. Now the first retry
    that finds the anchor gone from the tail and the record still missing drops the park, out loud: the typed failure to
    every page, a dialog with the words to the page that sent it (else one watching the session), the words verbatim in
    undelivered.jsonl, one log line without them. The drop is the gesture's one answer: nothing creates it later."""

    def setUp(self):
        super().setUp()
        with km._clients_lock:
            saved = list(km._clients)
            km._clients[:] = [self.client]               # the page that sent it, connected

        def restore():
            with km._clients_lock:
                km._clients[:] = saved
        self.addCleanup(restore)

    def _retry(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            km._retry_parked_creates()
        return buf.getvalue()

    def _undelivered(self):
        p = jd.STATE / "undelivered.jsonl"
        return [json.loads(ln) for ln in p.read_text().splitlines()] if p.exists() else []

    @staticmethod
    def _of(frames, kind, cid):
        return [f for f in frames if f.get("type") == kind and (cid is None or f.get("createId") == cid)]

    def test_a_quiet_sessions_park_ends_once_its_anchor_stops_streaming(self):
        # on main: the park stood through every retry once the turn was over (a quiet transcript spends nothing), forever
        self._create(uuid="a9", create_id="g-quiet", text=WORDS)
        self.assertEqual(len(km._parked_creates), 1, "premise: parked while the reply streams and its record lags")
        for _ in range(5):
            self._retry()
        self.assertEqual(len(km._parked_creates), 1, "the reply still streams: its record is coming, the park waits")
        self.be.streaming.clear()                        # the turn ended: its atom retired, and no record was ever written
        log = self._retry()
        self.assertEqual(km._parked_creates, [], "the next retry ends the park, with not one write to the transcript")
        nack = self._of(self.sent, "commentCreateFailed", "g-quiet")[-1]
        self.assertEqual((nack["transient"], nack.get("dropped"), nack["text"]), (False, True, km.ANCHOR_GONE_ERR),
                         "the page's hold ends on the kernel's answer: its mark goes and the words come back")
        errs = self._of(self.sent, "err", None)
        self.assertEqual(len(errs), 1, "one dialog, on the page that sent it")
        self.assertEqual((errs[0]["copy"], errs[0]["sid"]), (WORDS, PARENT), "the words in its copy slot, for its session")
        self.assertIn("web", errs[0]["text"], "it names the session")
        self.assertIn("comment create dropped (aaaaaaaa, session web, create g-quiet)", log, "the kernel log says it")
        self.assertNotIn(WORDS, log, "never the words in the log")
        row = self._undelivered()[-1]
        self.assertEqual((row["op"], row["sid"], row["createId"], row["text"]), ("commentCreate", PARENT, "g-quiet", WORDS),
                         "the words verbatim in undelivered.jsonl")
        self.assertEqual(self._rows(), [])

    def test_a_lag_answer_for_an_anchor_that_is_not_streaming_is_refused_at_the_door(self):
        # a passage between two /clears, a reply salvaged after the CLI discarded it, an unreadable first transcript: the
        # record is not coming, so nothing is parked to wait on it (on main it parked, and on a quiet session for good)
        self.be.streaming.clear()
        self._create(uuid="a9", create_id="g-gone", text=WORDS)
        self.assertEqual(km._parked_creates, [], "nothing to wait for: nothing parked")
        nack = self._of(self.sent, "commentCreateFailed", "g-gone")[-1]
        self.assertEqual((nack["transient"], nack["text"]), (False, km.ANCHOR_GONE_ERR))
        self.assertIn(km.ANCHOR_GONE_ERR, self._warns(), "a real refusal, said at the gesture like any other")

    def test_after_the_drop_nothing_makes_the_thread_later_and_the_words_sent_again_make_one(self):
        # on main the page gave up on its own count while the park stood, and the park made the thread later: with the words
        # sent again, two threads for one comment. The drop is the gesture's answer now, whatever turns up after it
        self._create(uuid="a9", create_id="g-late", text=WORDS)
        self.be.streaming.clear()
        self._retry()
        self.assertEqual(km._parked_creates, [], "premise: dropped, the words handed back")
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:                          # the record turns up after all
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        self._retry()
        self._create(uuid="a9", create_id="g-late", text=WORDS)   # the page's re-post, crossing the drop on the wire
        self.assertEqual(self._rows(), [], "no late thread, from the pusher or from the re-post")
        nack = self._of(self.sent, "commentCreateFailed", "g-late")[-1]
        self.assertEqual((nack["transient"], nack.get("dropped")), (False, True), "the re-post hears the drop again")
        self.assertEqual(len(self._of(self.sent, "err", None)), 1, "…and is not shown its dialog a second time")
        self._create(uuid="a9", create_id="g-again", text=WORDS)  # the user sends the handed-back words: a new gesture
        self.assertEqual(len(self._rows()), 1, "exactly one thread")

    def test_a_drop_reaches_the_page_open_after_a_reload_with_the_words(self):
        # on main the drop said nothing to anyone: the reloaded page held no record of the create, and the park went silently
        self._create(uuid="a9", create_id="g-reload", text=WORDS)
        self.client["alive"] = False                     # the page reloads: the socket that sent the create is gone
        fresh_sent = []
        # …and its bundle said ready: a socket whose page is still loading is no pick for a dialog (review of PR 218)
        fresh = {"send": lambda s: fresh_sent.append(json.loads(s)), "app": "chat", "alive": True, "active": PARENT,
                 "ready": True}
        with km._clients_lock:
            km._clients[:] = [self.client, fresh]
        self.be.streaming.clear()
        before = len(self.sent)
        self._retry()
        errs = self._of(fresh_sent, "err", None)
        self.assertEqual(len(errs), 1, "the page open now is told: %r" % fresh_sent)
        self.assertEqual((errs[0]["copy"], errs[0]["sid"], errs[0]["title"]), (WORDS, PARENT, "That comment did not start a thread"),
                         "with the words to copy back, for its session")
        self.assertEqual([f.get("dropped") for f in self._of(fresh_sent, "commentCreateFailed", "g-reload")], [True])
        self.assertEqual(self.sent[before:], [], "the dead socket is sent nothing")
        self.assertEqual(self._undelivered()[-1]["text"], WORDS)

    def test_a_drop_no_pane_heard_is_shown_to_the_page_when_it_reposts(self):
        # no page connected at the drop (a phone asleep): the reopened page re-posts the create it still holds, and the
        # door shows it the dialog the drop could not deliver, once
        self._create(uuid="a9", create_id="g-unheard", text=WORDS)
        with km._clients_lock:
            km._clients[:] = []
        self.client["alive"] = False
        self.be.streaming.clear()
        self._retry()
        self.assertEqual(km._parked_creates, [])
        self.client["alive"] = True
        self.sent.clear()
        self._create(uuid="a9", create_id="g-unheard", text=WORDS)
        self.assertEqual([f["type"] for f in self.sent], ["commentCreateFailed", "err"])
        self.assertEqual((self.sent[0].get("dropped"), self.sent[1]["copy"]), (True, WORDS))
        self.sent.clear()
        self._create(uuid="a9", create_id="g-unheard", text=WORDS)
        self.assertEqual([f["type"] for f in self.sent], ["commentCreateFailed"], "shown once")
        self.assertEqual(self._rows(), [])


class _Pane(dict):
    """A connected pane as the kernel holds it: its socket's frames land in `got`. `ok=False` is a socket whose sends fail."""
    def __init__(self, app="chat", active=PARENT, ok=True):
        super().__init__(app=app, alive=True, active=active)
        self.got = []

        def send(s):
            if not ok:
                raise OSError("the socket is gone")
            self.got.append(json.loads(s))
        self["send"] = send

    def of(self, kind):
        return [f for f in self.got if f.get("type") == kind]


class _HandlerSelf:
    """The handler's `self` for Handler._dispatch_ws: the ready arm's connect push is not under test here."""
    def _push_one(self, client, only=None):
        pass


class _PaneHarness(_Harness):
    """Panes connected to the kernel as it holds them (_Pane), the door's create jobs held until run (_run_jobs), and the
    ready arm driven for real (_ready)."""

    def setUp(self):
        super().setUp()
        with km._clients_lock:
            saved = list(km._clients)
            km._clients[:] = []

        def restore():
            with km._clients_lock:
                km._clients[:] = saved
        self.addCleanup(restore)
        self.jobs = []
        km._comment_create_spawn = lambda target, args: self.jobs.append((target, args))   # held: the page reconnects first

    def _connect(self, *panes):
        with km._clients_lock:
            km._clients[:] = list(panes)

    def _drive_on(self, client, msg):
        with contextlib.redirect_stderr(io.StringIO()):
            return km._drive(msg, client)

    def _create_on(self, client, uuid, cid, text=WORDS):
        return self._drive_on(client, {"type": "commentCreate", "id": PARENT, "uuid": uuid, "exact": "exponential backoff",
                                       "text": text, "name": "", "createId": cid})

    def _run_jobs(self):
        with contextlib.redirect_stderr(io.StringIO()):
            while self.jobs:
                target, args = self.jobs.pop(0)
                target(*args)

    def _drop_unheard(self, cid="g-unseen"):
        """A parked create dropped while no pane is connected (a phone asleep, the page reloaded): the dialog reaches no
        one, and the page that held the create is gone, so no re-post will ever come."""
        sender = _Pane()
        self._connect(sender)
        self._create_on(sender, "a9", cid)
        self._run_jobs()
        self.assertEqual(len(km._parked_creates), 1, "premise: parked while the reply streams")
        sender["alive"] = False
        self._connect()
        self.be.streaming.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [], "premise: dropped")
        self.assertEqual(sender.of("err"), [], "premise: no pane drew it")

    def _ready(self, pane):
        self._connect(*[c for c in km._clients if c is not pane], pane)
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(_HandlerSelf(), {"type": "ready"}, pane)


class OutcomeReachesThePageWhereverItIs(_PaneHarness):
    """A create's outcome reaches the page that holds it on whatever socket it holds now, and a drop's dialog counts as told
    only once a page has drawn it (review of PR 211, 2026-10-11).

    The door's create runs on a thread of its own and can take seconds (a large transcript's cut, the fork's start). A page
    whose socket dropped and reopened meanwhile re-posted the create on the new socket and was answered busy, the transient
    answer that keeps its mark; the job then answered the socket that SENT it, which was dead, so a refusal left the mark
    pending until the next reconnect or a reload. A dropped park's dialog counted as told the moment a socket took the write,
    so a stalled socket swallowed it and nothing showed it again; and with no pane connected at the drop, the words were in
    undelivered.jsonl and the log alone, with no dialog and no Log entry anywhere."""

    def _reconnect_mid_job(self, uuid, cid):
        """The create is sent, its job starts, and the page's socket drops and reopens before the job ends: the shim's
        romp:wsup re-posts the held create on the new socket, which the door answers busy while the job holds it."""
        old = _Pane()
        self._connect(old)
        self._create_on(old, uuid, cid)
        self.assertEqual(len(self.jobs), 1, "premise: the create's job is running")
        old["alive"] = False                             # the socket dropped (Handler._ws's finally)
        new = _Pane()
        self._connect(new)
        self._create_on(new, uuid, cid)                  # the page's re-post on the reopened socket
        self.assertEqual([(f["transient"], f["createId"]) for f in new.of("commentCreateFailed")], [(True, cid)],
                         "premise: answered busy, the mark kept while the job runs")
        self._run_jobs()
        return old, new

    def test_a_thread_made_while_the_page_reconnected_reaches_it_on_the_new_socket(self):
        old, new = self._reconnect_mid_job("a1", "g-mid-ok")
        acks = new.of("commentCreated")
        self.assertEqual([a["createId"] for a in acks], ["g-mid-ok"],
                         "the thread's answer reaches the page on the socket it holds now: %r" % new.got)
        self.assertEqual(acks[0]["tid"], self._rows()[0]["tid"])
        self.assertEqual(len(self._rows()), 1)

    def test_a_refusal_made_while_the_page_reconnected_reaches_it_with_the_reason(self):
        self.be.streaming.clear()                        # the anchor is not coming: the job refuses it outright
        old, new = self._reconnect_mid_job("a9", "g-mid-refused")
        last = new.of("commentCreateFailed")[-1]
        self.assertEqual((last["createId"], last["transient"], last["text"]), ("g-mid-refused", False, km.ANCHOR_GONE_ERR),
                         "the refusal ends the page's hold on its new socket, its mark gone and the words back: %r" % new.got)
        self.assertIs(last.get("say"), True, "and that page, sent no warn, is told to say the reason itself")
        self.assertEqual(new.of("warn"), [], "no warn on a socket the create was not sent on")
        self.assertEqual(km._parked_creates, [])

    def test_a_refusal_reaches_the_sending_page_as_before_and_other_pages_hear_it_quietly(self):
        sender, other = _Pane(), _Pane()
        self._connect(sender, other)
        self.be.streaming.clear()
        self._create_on(sender, "a9", "g-refused")
        self._run_jobs()
        self.assertEqual([f["text"] for f in sender.of("warn")], [km.ANCHOR_GONE_ERR], "the sender is warned, as before")
        self.assertNotIn("say", sender.of("commentCreateFailed")[-1], "…so its own copy carries no second telling")
        self.assertIs(other.of("commentCreateFailed")[-1].get("say"), True,
                      "another page holds no such create and ignores it; were it the holder, it would say why")

    def test_a_drop_no_pane_heard_surfaces_on_the_next_pane_that_listens(self):
        self._drop_unheard()
        page = _Pane()
        self._ready(page)                                # a page opens (the reload, the phone waking): its bundle says ready
        errs = page.of("err")
        self.assertEqual(len(errs), 1, "the drop's dialog reaches the next page that listens: %r" % page.got)
        self.assertEqual((errs[0]["copy"], errs[0]["sid"], errs[0]["createId"]), (WORDS, PARENT, "g-unseen"),
                         "with the words to copy back, for its session, naming the create its acknowledgement answers")
        other = _Pane()
        self._ready(other)
        self.assertEqual(other.of("err"), [], "one pane at a time: a live pane holds it until it acknowledges or closes")

    def test_a_drop_counts_as_told_only_once_a_pane_acknowledges_it(self):
        stalled = _Pane()                                # its peer stopped reading: writes still enqueue, nothing is drawn
        self._connect(stalled)
        self._create_on(stalled, "a9", "g-stalled")
        self._run_jobs()
        self.be.streaming.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            km._retry_parked_creates()
        self.assertEqual(len(stalled.of("err")), 1, "premise: the write went to the socket that sent the create")
        self.assertFalse(km._dropped_creates[(PARENT, "g-stalled")]["told"], "a socket write is not a page seeing it")
        stalled["alive"] = False                         # the page redials (_register_ws_client retires the twin socket)
        page = _Pane()
        self._connect(page)
        self._create_on(page, "a9", "g-stalled")         # its re-post on the reopened socket
        self.assertEqual([f["type"] for f in page.got], ["commentCreateFailed", "err"],
                         "the re-post hears the drop and is shown the dialog the stalled socket swallowed")
        self._create_on(page, "a9", "g-stalled")
        self.assertEqual(len(page.of("err")), 1, "a live pane holds it: not shown twice while it is up")
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(_HandlerSelf(), {"type": "commentDropSeen", "id": PARENT, "createId": "g-stalled"}, page)
        self.assertTrue(km._dropped_creates[(PARENT, "g-stalled")]["told"], "the page drew it and said so: told")
        page["alive"] = False
        later = _Pane()
        self._ready(later)
        self.assertEqual(later.of("err"), [], "a drop a page acknowledged is offered to no one again")
        self.assertEqual(self._rows(), [])

    def test_a_pane_that_closes_without_acknowledging_hands_the_dialog_to_one_still_connected(self):
        self._drop_unheard("g-handed-on")
        first, second = _Pane(), _Pane(active="")
        self._ready(first)
        self._ready(second)
        self.assertEqual((len(first.of("err")), len(second.of("err"))), (1, 0), "premise: one pane holds it")
        first["alive"] = False                           # its socket closes before the page drew it (Handler._ws's finally)
        self._connect(second)
        km._reoffer_drops_of(first)
        self.assertEqual([e["createId"] for e in second.of("err")], ["g-handed-on"],
                         "the pane still connected is shown it; it never waits for a pane that may not come back")

    def test_the_listen_events_offer_untold_drops_and_a_close_hands_them_on(self):
        import inspect
        src = inspect.getsource(km.Handler._dispatch_ws)
        arm = src[src.find('msg.get("type") == "ready"'):]
        self.assertLess(arm.index("_consume_pending_reveal(client)"), arm.index("_offer_untold_drops(client, fresh=True)"),
                        "the ready arm offers them where it consumes a parked reveal: the pane listens from there, and holds "
                        "nothing it was sent before (review of PR 218)")
        mod = Path(km.__file__).read_text() if getattr(km, "__file__", None) else inspect.getsource(km)
        for name in ("_push", "_push_session_now", "_confirm_close_now"):   # read from the file: the harness stubs one of them
            i = mod.index("\ndef %s(" % name)
            s = mod[i:mod.index("\ndef ", i + 1)]
            self.assertLess(s.index('_consume_pending_reveal(c, why="the pane\'s redial")'), s.index("_offer_untold_drops(c)"),
                            name + ": a redial's first strip, the ready a redial never posts")
        s = inspect.getsource(km.Handler._ws)
        self.assertLess(s.index("_clients.remove(client)"), s.index("_reoffer_drops_of(client)"),
                        "a closed socket hands on what it held, once it is out of the pick")


class DropsReachAPageThatListens(_PaneHarness):
    """A dropped create's dialog goes only to a pane whose bundle listens, and survives a kernel restart until a page draws
    it (review of PR 218, 2026-10-11).

    The fallback pick (_one_err_pane) took any connected pane, and a page whose bundle was still loading could win it on
    lastIn: the frame vanished into a document with no listener, that pane held the drop, and at its ready its own offer
    was refused because a live pane, itself, held it, so the drop stayed untold until it closed. And the memo lived in
    memory alone: a drop with no page open, then a restart before one opened, left undelivered.jsonl and the log as its
    only record, and a page that still held the create re-posted it to a kernel that no longer knew the drop."""

    def _park(self, cid):
        sender = _Pane()
        self._connect(sender)
        self._create_on(sender, "a9", cid)
        self._run_jobs()
        self.assertEqual(len(km._parked_creates), 1, "premise: parked while the reply streams")
        sender["alive"] = False                          # the page that sent it is gone
        return sender

    def _drop_now(self):
        self.be.streaming.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [], "premise: dropped")

    def _restart(self):
        """The kernel restarts: the memo in memory is gone and the boot restores what the last kernel kept."""
        with km._create_lock:
            km._dropped_creates.clear()
        with contextlib.redirect_stderr(io.StringIO()):
            return km._dropped_creates_load()

    def test_the_fallback_pick_skips_a_pane_whose_bundle_has_not_said_ready(self):
        self._park("g-loading")
        loading, page = _Pane(), _Pane()
        loading["lastIn"] = 200                          # the shim's frames: a socket that answers, its bundle still loading
        page["ready"], page["lastIn"] = True, 100
        self._connect(loading, page)
        self._drop_now()
        self.assertEqual(loading.of("err"), [], "a page that cannot hear the dialog yet is not handed it")
        self.assertEqual([e["createId"] for e in page.of("err")], ["g-loading"], "the page that listens is: %r" % page.got)
        self._ready(loading)
        self.assertEqual(loading.of("err"), [], "one pane at a time: the listening page holds it until it acknowledges")

    def test_a_redialed_pane_listens_from_its_handshake(self):
        # the shim dials ?reconnect=1 only once the kernel answered its bundle's ready, and a redial posts no ready again
        self._park("g-redial")
        loading, redialed = _Pane(), _Pane(app="feed", active="")
        loading["lastIn"] = 200
        redialed["redial"], redialed["lastIn"] = True, 100
        self._connect(loading, redialed)
        self._drop_now()
        self.assertEqual(loading.of("err"), [])
        self.assertEqual([e["createId"] for e in redialed.of("err")], ["g-redial"])

    def test_a_pane_sent_the_drop_before_it_listened_is_shown_it_at_its_ready(self):
        self._drop_unheard("g-early")
        early = _Pane()
        self._connect(early)
        entry = km._dropped_creates[(PARENT, "g-early")]
        self.assertTrue(km._offer_dropped_create(entry, early), "premise: the frame went to its socket")
        early.got.clear()                                # …into a document with no listener yet: lost
        self._ready(early)
        self.assertEqual([e["createId"] for e in early.of("err")], ["g-early"],
                         "its ready says it holds nothing it was sent before: the drop it held comes again")
        self.assertFalse(km._dropped_creates[(PARENT, "g-early")]["told"], "still untold until the page acknowledges it")

    def test_a_drop_no_page_saw_before_a_restart_is_shown_to_the_first_page_after_it(self):
        self._drop_unheard("g-restart")
        before = km._dropped_creates[(PARENT, "g-restart")]["frame"]
        kept = (jd.STATE / "comment-drops.json").read_text()
        self.assertIn("g-restart", kept)
        self.assertNotIn(WORDS, kept, "the words stay in undelivered.jsonl alone")
        self.assertEqual(self._restart(), 1, "the boot restores the untold drop")
        page = _Pane()
        self._ready(page)
        errs = page.of("err")
        self.assertEqual(errs, [before], "the first page after the restart is shown the same dialog, words and all")
        self.assertEqual(errs[0]["copy"], WORDS)

    def test_after_a_restart_a_repost_of_a_dropped_create_hears_the_drop_and_makes_no_thread(self):
        holder = self._park("g-repost")                  # the page holding it is asleep through the drop and the restart
        self._connect()
        self._drop_now()
        self._restart()
        p = Path(km._sessions(0)[0]["path"])
        with p.open("a") as fh:                          # the record has landed meanwhile: a fresh create would succeed
            fh.write(json.dumps(aline(self.now - 300, "Add a jitter to the backoff.", "a9", parent="a1")) + "\n")
        holder["alive"] = True
        holder.got.clear()
        self._connect(holder)
        self._create_on(holder, "a9", "g-repost")        # its re-post on the restarted kernel
        self._run_jobs()
        self.assertEqual(self._rows(), [], "the gesture's answer stands across the restart: no thread beside the dialog")
        self.assertEqual([(f["type"], f.get("dropped")) for f in holder.got], [("commentCreateFailed", True), ("err", None)],
                         "the re-post hears the drop and is shown its dialog")

    def test_a_drop_a_page_acknowledged_is_not_shown_again_after_a_restart(self):
        self._drop_unheard("g-told")
        page = _Pane()
        self._ready(page)
        self.assertEqual(len(page.of("err")), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            km.Handler._dispatch_ws(_HandlerSelf(), {"type": "commentDropSeen", "id": PARENT, "createId": "g-told"}, page)
        page["alive"] = False
        self.assertEqual(self._restart(), 0, "acknowledged on disk too")
        self.assertTrue(km._dropped_creates[(PARENT, "g-told")]["told"], "…and still the gesture's answer")
        later = _Pane()
        self._ready(later)
        self.assertEqual(later.of("err"), [])

    def test_a_restored_drop_whose_words_cannot_be_read_back_is_shown_saying_so(self):
        self._drop_unheard("g-nowords")
        (jd.STATE / "undelivered.jsonl").unlink()
        self._restart()
        page = _Pane()
        self._ready(page)
        errs = page.of("err")
        self.assertEqual(len(errs), 1, "shown all the same: the user learns the comment did not start a thread")
        self.assertEqual(errs[0]["copy"], "")
        self.assertIn("could not read your text back from undelivered.jsonl", errs[0]["text"])
        self.assertIn("the file is missing", errs[0]["text"])

    def test_the_boot_restores_the_drops_before_the_server_binds(self):
        import inspect
        s = inspect.getsource(km.main)
        self.assertLess(s.index("_dropped_creates_load()"), s.index("_LoopbackServer("),
                        "restored before any socket can re-post a dropped create")


if __name__ == "__main__":
    unittest.main()
