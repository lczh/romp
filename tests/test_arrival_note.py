#!/usr/bin/env python3
"""The ARRIVAL NOTE (the user 2026-11-12): a message the user TYPED that reaches a turn already running gets one short
note, honestly labelled from romp, as the hook's additional context on that delivery.

Models often answer a message spliced into a running turn only inside a thinking block and carry on, so the person
never sees an answer. The CLI runs the UserPromptSubmit hook for a message it splices in at a tool boundary (before it
reports the message `started`), and attaches the additional context the hook answers right after the message. romp's
own hook (SdkSession._prompt_submit_hook) answers ARRIVAL_NOTE exactly when the prompt is the text romp fed and the CLI
has not yet taken (the hold), its send door recorded it as typed into a page (the composer, a card's reply box, a
comment thread's opener or reply; never an untagged or tagged `romp send`, a relay, the Continue or Nudge button, or
anything romp sends on its own), it was fed while a turn ran and that turn has not ended. A message that starts a turn
gets none, and so does one the CLI takes only after the turn's result.

Each test below fails without the note: the excluded cases ride beside a typed mid-turn delivery that must get it.
Synthetic throughout: the notes-api demo, invented text, placeholder ids."""
import asyncio
import os
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from romp_load import load_source   # noqa: E402  the loader import comes FIRST (tests/test_state_isolation_order.py)
BIN = os.path.join(os.path.dirname(HERE), "bin")
_XDG = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _XDG
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.makedirs(os.path.join(_XDG, "romp"), exist_ok=True)
open(os.path.join(_XDG, "romp", "session-hosts"), "w").write("off")   # this module mints its own roots (the 2026-09-11 rule)
km = load_source("romp_kernel_arrival_note", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_arrival_note", os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py"))

SID = "7c7c7c7c-1111-4222-8333-966666666666"
TSID = "7d7d7d7d-1111-4222-8333-977777777777"
WORDS = "is the notes cache shared across the API workers?"
NOTE_OUT = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": sb.ARRIVAL_NOTE}}
NUDGE = "Where does this stand?\n\n<!-- romp-injected --><!-- romp-auto -->"


class RecordingBackend:
    """A backend that forwards its own sends (as the SDK's does) and records what each send was handed."""

    def __init__(self):
        self.sent = []

    def send(self, sid, text, qid=None, user=False, paths=None, typed=""):
        self.sent.append({"sid": sid, "text": text, "user": user, "typed": typed})
        return True

    def forwards_sends(self):
        return True

    def owns(self, sid):
        return True

    def fork(self, *a, **k):
        return None

    def connect(self, sid):
        return None

    def resume(self, *a, **k):
        return None

    def kill(self, sid):
        return None


class ResultMessage:
    uuid = "r1"


class _Frames:
    """The message classes _on_message takes (isinstance reads the class arguments)."""
    class AssistantMessage:
        pass

    class SystemMessage:
        pass


class Doors:
    """The kernel's send doors, each handing the backend `typed` (the words typed into a page) or nothing."""

    def doors_setup(self):
        self.be = RecordingBackend()
        self._saved_doors = (km.Sessions.backend_for, km._kernel_knows, km._push_soon, km._working_now,
                             km._compacting_now, km._limit_hold)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._kernel_knows = lambda sid: True
        km._push_soon = lambda *a, **k: None
        km._working_now = lambda sid: False
        km._compacting_now = lambda *a, **k: False
        km._limit_hold = lambda sid: None
        km._pending_ops.pop(SID, None)
        self.client = {"send": lambda frame: None, "alive": True}

    def doors_teardown(self):
        (km.Sessions.backend_for, km._kernel_knows, km._push_soon, km._working_now, km._compacting_now,
         km._limit_hold) = self._saved_doors
        km._pending_ops.pop(SID, None)

    def typed_by(self, door):
        """What `door` (a callable driving one kernel door) handed the backend as `typed`, and the text it sent."""
        n = len(self.be.sent)
        door()
        self.assertEqual(len(self.be.sent), n + 1, "the door handed the text to the backend")
        return self.be.sent[-1]["typed"], self.be.sent[-1]["text"]

    def thread(self):
        with km._comments_lock:
            km._save_comments(SID, {"threads": [{"tid": TSID, "sid": TSID, "name": "web-comment-1", "status": "open",
                                                 "anchorUuid": "aaaabbbb-1111-2222-3333-444455556666", "anchorT": 100,
                                                 "cutUuid": "aaaabbbb-1111-2222-3333-444455556666",
                                                 "exact": "the caching layer", "createdT": 100, "lastSeenT": 100}]})

    def unthread(self):
        with km._comments_lock:
            km._save_comments(SID, {"threads": []})


class ArrivalNote(Doors, unittest.TestCase):

    def setUp(self):
        self.doors_setup()
        self.d = tempfile.mkdtemp()
        open(os.path.join(self.d, "session-hosts"), "w").write("off")
        self.sbe = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": "/tmp/notes-api", "alive": True})
        self.s = sb.SdkSession(self.sbe, sb.read_reg(self.d, SID))
        self.s._lifecycle, self.s._raw_frames = True, True     # a CLI that writes command_lifecycle frames
        self.s._input_wake = asyncio.Event()

        async def caught_up():
            return True
        self.s._stream_caught_up = caught_up                    # the receive loop has read every frame (tested apart)

        async def _noop():
            pass
        self.s._do_refresh_context = _noop
        self.s._do_refresh_usage = _noop

    def tearDown(self):
        self.doors_teardown()

    # --- the session, as the feeder and the CLI drive it ---------------------------------------------------------------
    def feed(self, text, typed="", fresh=False):
        """Queue `text` (with the typed words its door recorded) and pop it for the CLI as the feeder does: the hold the
        CLI's lifecycle frames and the hook read. `fresh`: fed from idle, so it starts a turn."""
        self.s.enqueue(text, qid="echo:" + os.urandom(16).hex(), qts=int(time.time() * 1000), typed=typed)
        with self.s._lock:
            item, meta = self.s._pop_for_feed_locked(0)
        self.s._untaken = self.s._hold_for_feed(item, meta, fresh)
        self.s.inflight = 1                                    # the feed counts the turn it starts or joins
        return self.s._untaken["cu"]

    def frame(self, msg):
        async def run():
            self.s._on_message(msg, _Frames.AssistantMessage, ResultMessage, _Frames.SystemMessage)
            await asyncio.sleep(0)
        asyncio.run(run())

    def started(self, cu):
        self.frame(sb.CommandLifecycle({"command_uuid": cu, "state": "started"}))

    def hook(self, prompt):
        return asyncio.run(self.s._prompt_submit_hook({"hook_event_name": "UserPromptSubmit", "prompt": prompt},
                                                      None, None))

    def delivered_mid_turn_gets_the_note(self, text, typed):
        """The positive control every exclusion rides beside: a typed text delivered into a running turn."""
        cu = self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), NOTE_OUT)
        self.started(cu)                                       # the CLI reports the splice after the hook

    def problems(self):
        return [p for p in self.sbe.problems() if (p.get("key") or ("",))[0] == "arrival-note"]

    # --- the cases -----------------------------------------------------------------------------------------------------
    def test_a_typed_message_delivered_mid_turn_gets_the_note_exactly_once(self):
        cu = self.feed(WORDS, typed=WORDS)
        self.assertEqual(self.hook(WORDS), NOTE_OUT)
        self.started(cu)
        self.assertEqual(self.hook(WORDS), {}, "taken: a later prompt with the same words is not this delivery")
        self.assertEqual(self.hook("Run the migration next."), {}, "nor is any other prompt")

    def test_a_typed_message_that_starts_a_turn_gets_none(self):
        self.delivered_mid_turn_gets_the_note("and which TTL does it use?", "and which TTL does it use?")
        self.frame(ResultMessage())                            # the turn ends; the next message starts one
        cu = self.feed(WORDS, typed=WORDS, fresh=True)
        self.assertEqual(self.hook(WORDS), {}, "fed from idle: it opens the turn")
        self.started(cu)                                       # (the CLI reports a turn-start `started` before its hook)
        self.assertEqual(self.hook(WORDS), {})

    def test_romp_injected_text_gets_none(self):
        self.delivered_mid_turn_gets_the_note(WORDS, WORDS)
        self.assertEqual(self.typed_by(lambda: km.Sessions.backend_for(SID).send(SID, NUDGE))[0], "")
        self.feed(NUDGE)                                       # romp's own send records no typed words
        self.assertEqual(self.hook(NUDGE), {})
        self.started(self.s._untaken["cu"])
        self.feed("The fixtures are backfilled; over to you.")   # words that read like the person's, no typed origin
        self.assertEqual(self.hook("The fixtures are backfilled; over to you."), {})

    def test_an_untagged_or_tagged_romp_send_gets_none(self):
        self.delivered_mid_turn_gets_the_note(WORDS, WORDS)
        typed, text = self.typed_by(lambda: km._deliver_text(SID, "the fixtures are backfilled; over to you"))
        self.assertEqual(typed, "", "POST /send: another session or a script")
        self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), {})
        self.started(self.s._untaken["cu"])
        body = km._parse_send_body(b'{"id": "%s", "text": "nightly brief", "tag": "nightly"}' % SID.encode())
        typed, text = self.typed_by(lambda: km._deliver_text(SID, body["text"]))
        self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), {})

    def test_the_card_reply_box_gets_it_but_continue_and_nudge_do_not(self):
        typed, text = self.typed_by(lambda: km._drive({"type": "askFollowUp", "id": SID, "text": "use staging for it"},
                                                      self.client))
        self.assertEqual(typed, "use staging for it", "the words typed, not the wrapped body")
        cu = self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), NOTE_OUT)
        self.started(cu)
        for msg in ({"type": "askFollowUp", "id": SID, "cont": True},
                    {"type": "askFollowUp", "id": SID, "text": "where does this stand?", "nudge": True}):
            with self.subTest(msg=sorted(msg)):
                typed, text = self.typed_by(lambda: km._drive(msg, self.client))
                self.assertEqual(typed, "")
                cu = self.feed(text, typed=typed)
                self.assertEqual(self.hook(text), {})
                self.started(cu)

    def test_the_composer_door_records_the_words_typed(self):
        typed, text = self.typed_by(lambda: km._drive({"type": "sendMessage", "id": SID, "text": WORDS}, self.client))
        self.assertEqual((typed, text), (WORDS, WORDS))
        cu = self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), NOTE_OUT)

    def test_a_comment_thread_reply_mid_turn_gets_it(self):
        self.thread()
        saved = km._thread_reg
        km._thread_reg = lambda sid: {"alive": True}
        try:
            typed, text = self.typed_by(lambda: self.assertIsNone(km._comment_reply(SID, TSID, "why an hour, not a day?")))
        finally:
            km._thread_reg = saved
            self.unthread()
        self.assertEqual((self.be.sent[-1]["sid"], typed), (TSID, "why an hour, not a day?"))
        self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), NOTE_OUT)

    def test_a_comment_threads_relay_into_its_parent_gets_none(self):
        self.delivered_mid_turn_gets_the_note(WORDS, WORDS)
        self.thread()
        msgs = [{"who": "user", "text": "should the cache be write-through?", "t": 1},
                {"who": "assistant", "text": "Yes; it removes the stale-read window.", "t": 2}]
        saved = km._thread_exchange
        km._thread_exchange = lambda tsid, cut, floor_t=0: {"msgs": list(msgs), "capped": list(msgs), "sent": "",
                                                           "gap": "", "oldestT": 0}
        try:
            typed, text = self.typed_by(lambda: self.assertIsNone(km._comment_merge(SID, TSID)))
        finally:
            km._thread_exchange = saved
            self.unthread()
        self.assertEqual(typed, "")
        self.feed(text, typed=typed)
        self.assertEqual(self.hook(text), {})

    def test_a_message_delivered_after_the_turn_ends_gets_none(self):
        self.delivered_mid_turn_gets_the_note("and which TTL does it use?", "and which TTL does it use?")
        self.feed(WORDS, typed=WORDS)                          # fed while the model writes its final message
        self.frame(ResultMessage())                            # the turn ends first; the CLI then takes the text as a turn
        self.assertEqual(self.hook(WORDS), {})

    def test_the_hook_waits_for_the_turns_result_before_it_decides(self):
        """The SDK runs the hook beside the receive loop: the turn's result can still sit in the stream when the hook for
        the message the CLI drains after it runs. The hook decides only once the loop has read it."""
        self.delivered_mid_turn_gets_the_note("and which TTL does it use?", "and which TTL does it use?")
        self.feed(WORDS, typed=WORDS)

        async def loop_reads_the_result():
            self.s._on_message(ResultMessage(), _Frames.AssistantMessage, ResultMessage, _Frames.SystemMessage)
            return True
        self.s._stream_caught_up = loop_reads_the_result
        self.assertEqual(self.hook(WORDS), {})

    def test_a_stream_that_cannot_be_shown_caught_up_leaves_the_note_off_and_says_so(self):
        self.delivered_mid_turn_gets_the_note("and which TTL does it use?", "and which TTL does it use?")
        for answer in (False, None):
            with self.subTest(answer=answer):
                async def stream(a=answer):
                    return a
                self.s._stream_caught_up = stream
                cu = self.feed(WORDS, typed=WORDS)
                self.assertEqual(self.hook(WORDS), {})
                self.assertTrue(self.problems(), "a note left off is a logged problem, not a silent pass")
                self.started(cu)

    def test_a_typed_slash_command_gets_none(self):
        self.delivered_mid_turn_gets_the_note(WORDS, WORDS)
        self.feed("/compact", typed="/compact")
        self.assertEqual(self.hook("/compact"), {})

    def test_a_parked_typed_send_keeps_its_words_for_the_replay(self):
        km._working_now = lambda sid: True
        self.be.forwards_sends = lambda: False                 # parks while the turn runs
        km._drive({"type": "sendMessage", "id": SID, "text": WORDS}, self.client)
        km._deliver_text(SID, "from another session")
        ops = list(km._pending_ops.get(SID) or [])
        self.assertEqual([km._op_typed(op) for op in ops], [WORDS, ""])
        self.be.forwards_sends = lambda: True
        km._deliver_send_batch(self.be, SID, ops)
        self.assertEqual([d["typed"] for d in self.be.sent], [WORDS, ""], "the drain hands each copy its own origin")


class BackendCarry(unittest.TestCase):
    """The queued copy keeps the typed words beside its identity, across the persisted mirror and a stranded re-head."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        open(os.path.join(self.d, "session-hosts"), "w").write("off")
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": "/tmp/notes-api", "alive": True})
        self.s = sb.SdkSession(self.be, sb.read_reg(self.d, SID))

    def test_the_copy_keeps_the_words_and_the_mirror_restores_them(self):
        self.s.enqueue(WORDS, qid="echo:a1", qts=1, typed=WORDS)
        self.s.enqueue("from another session", qid="echo:a2", qts=2)
        self.assertEqual([(m or {}).get("typed") for m in self.s._pending_meta], [WORDS, None])
        metas = sb.queue_meta_from_reg(sb.read_reg(self.d, SID))
        self.assertEqual([(m or {}).get("typed") for m in metas], [WORDS, None], "the restart's restore keeps the origin")

    def test_the_words_kept_are_capped(self):
        self.s.enqueue("x " * 600, qid="echo:a3", qts=3, typed="x " * 600)
        self.assertEqual(len(self.s._pending_meta[-1]["typed"]), sb.TYPED_KEEP)

    def test_a_stranded_re_head_keeps_the_origin(self):
        self.s.enqueue(WORDS, qid="echo:a4", qts=4, typed=WORDS)
        with self.s._lock:
            text, meta = self.s._pop_for_feed_locked(0)
            back = self.s._unfeed_locked([text])
        self.assertEqual(back[0].get("typed"), WORDS)

    def test_only_typed_words_earn_the_note(self):
        self.assertTrue(sb.arrival_note_applies(WORDS))
        for t in ("", "   ", None, "/compact", True):
            self.assertFalse(sb.arrival_note_applies(t), repr(t))


class StreamCaughtUp(unittest.TestCase):
    """The hook waits for the receive loop to have handled every frame the CLI wrote before it called the hook."""

    def _sess(self, stats):
        s = sb.SdkSession.__new__(sb.SdkSession)
        s.client = types.SimpleNamespace(_query=types.SimpleNamespace(
            _message_receive=types.SimpleNamespace(statistics=stats)))
        return s

    def test_an_empty_stream_with_the_loop_waiting_is_caught_up(self):
        st = types.SimpleNamespace(current_buffer_used=0, tasks_waiting_receive=1)
        self.assertIs(asyncio.run(self._sess(lambda: st)._stream_caught_up()), True)

    def test_it_waits_while_the_loop_drains_the_backlog(self):
        left = [3]

        def stats():
            left[0] = max(0, left[0] - 1)
            return types.SimpleNamespace(current_buffer_used=left[0], tasks_waiting_receive=0 if left[0] else 1)
        self.assertIs(asyncio.run(self._sess(stats)._stream_caught_up()), True)

    def test_a_stream_that_never_settles_is_not_caught_up(self):
        st = types.SimpleNamespace(current_buffer_used=2, tasks_waiting_receive=0)
        self.assertIs(asyncio.run(self._sess(lambda: st)._stream_caught_up()), False)

    def test_no_stream_to_ask_is_unknown(self):
        s = sb.SdkSession.__new__(sb.SdkSession)
        s.client = None
        self.assertIsNone(asyncio.run(s._stream_caught_up()))


if __name__ == "__main__":
    unittest.main()
