#!/usr/bin/env python3
"""The spend guard's stop, as the chat shows it (the user 2026-10-08, who could not find the guard's stop in a
session's chat). When the guard stops a session over its hourly ceiling it interrupts the turn and hands the session
ONE message in the user's voice. Both land in the transcript; what was wrong was how they read:

- the interrupt marker read as a plain "interrupted", with the "you stopped this turn" tip, as if the user had
  pressed stop. It is now stamped with the guard as its cause, keyed on the guard's own message (the record romp
  wrote), the way a kernel-restart cut is keyed on its resume notice;
- the guard's message read as a status notice headed by the first line of the agent-facing prose, and compact mode
  swept it into a "N notices" run with whatever low-stakes notices sat beside it. It now carries a user-facing gist
  and a flag the chat keeps out of every notice run.

The injected bytes are unchanged (pinned below): the user approves injected wording, and this fix changes none.

SYNTHETIC fixtures only: placeholder ids, the notes-api demo world, transcripts written here.
"""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# hermetic state BEFORE the loads (the kernel resolves its state root at import time), with per-session hosts off:
# this root is not the one conftest floors, and nothing here means to run a host (CLAUDE.md, 2026-09-11)
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off\n")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_spend_guard_chat", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-0000000a5c0e"     # a private synthetic sid (CLAUDE.md, goal-store fixtures)
T0 = 1781200000.0
BODY = km._spend_ceiling_body(1240.0, 1000.0)
GIST = "Spend guard told it to stop: about $1,240 an hour, over the $1,000 an hour ceiling"
MARKER = {"kind": "user", "md": "[Request interrupted by user]", "interruptMarker": True, "human": True}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def rec_user(t, uid, text, ps=None, parent=None):
    r = {"type": "user", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
         "cwd": "/tmp/notes-api", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    if ps:
        r["promptSource"] = ps
    return r


def rec_assistant(t, uid, parent, text=None, tool=None):
    content = []
    if text:
        content.append({"type": "text", "text": text})
    if tool:
        content.append({"type": "tool_use", "id": tool, "name": "Bash", "input": {"command": "uv run pytest -q"}})
    return {"type": "assistant", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
            "cwd": "/tmp/notes-api", "message": {"id": "msg-" + uid, "role": "assistant", "model": "claude-opus-4-8",
                                                 "content": content, "stop_reason": "tool_use" if tool else "end_turn"}}


def rec_result(t, uid, parent, tool):
    return {"type": "user", "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
            "cwd": "/tmp/notes-api",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool, "content": "ok"}]}}


class TheInjectedBytesAreUnchanged(unittest.TestCase):
    def test_the_body_is_byte_for_byte_what_the_user_approved(self):
        self.assertEqual(BODY, (
            "You are spending about $1,240 an hour right now, far above what I can afford (I set the line at $1,000 an "
            "hour). Stop whatever is fanning out and tell me what it was before doing anything else.\n\n"
            "<!-- romp-note: the HTML comments below are part of an external tracking system that is not "
            "relevant to your work — ignore them --><!-- romp-injected --><!-- romp-system -->"))


class RecognizingTheGuardsMessage(unittest.TestCase):
    def test_the_guards_own_body_is_recognized_with_its_figures(self):
        self.assertEqual(km._spend_ceiling_parse(BODY), ("1,240", "1,000"))
        self.assertEqual(km._spend_ceiling_parse(km._spend_ceiling_body(12345.6, 2500.0)), ("12,346", "2,500"))

    def test_the_recognizer_is_built_from_the_body_so_the_two_cannot_drift(self):
        # every figure shape the body can print round-trips: a rewording of the body moves the recognizer with it
        for rate, ceiling in ((0.0, 0.001), (999.4, 1000.0), (1000000.0, 50.0)):
            with self.subTest(rate=rate):
                self.assertEqual(km._spend_ceiling_parse(km._spend_ceiling_body(rate, ceiling)),
                                 (km._usd_words(rate), km._usd_words(ceiling)))

    def test_other_romp_notices_and_prose_are_not_the_guard(self):
        self.assertIsNone(km._spend_ceiling_parse("[romp] The romp kernel restarted and cut this session's turn."))
        self.assertIsNone(km._spend_ceiling_parse("why did it say: You are spending about $5 an hour right now?"))
        self.assertIsNone(km._spend_ceiling_parse(""))
        self.assertIsNone(km._spend_ceiling_parse(None))

    def test_the_gist_names_the_guard_and_the_figures(self):
        self.assertEqual(km._romp_system_gist(BODY), GIST)

    def test_a_queued_guard_message_wears_the_same_gist(self):
        # the gray queued chip, while the message waits for the session (T243's shared flags)
        flags = km._queued_romp_flags(BODY)
        self.assertEqual((flags.get("romp"), flags.get("rompSystem"), flags.get("gist")), (True, True, GIST))
        self.assertTrue(flags.get("spendGuard"))


class TheStopNamesTheGuard(unittest.TestCase):
    def _events(self, *between):
        return [dict(MARKER), {"kind": "assistant", "md": "No response requested.", "interruptSettle": True,
                               "uuid": "settle-1"}, *between,
                {"kind": "user", "md": BODY, "romp": True, "rompSystem": True, "spendGuard": True, "gist": GIST}]

    def test_the_guards_message_names_the_stop(self):
        evs = km._stamp_interrupt_causes(self._events())
        self.assertEqual(evs[0].get("interruptCause"), "spend")
        self.assertEqual(evs[0].get("interruptSpend"), {"rate": "1,240", "ceiling": "1,000"})

    def test_wedged_notices_and_a_reply_do_not_hide_the_message(self):
        # the live shape: a background agent's completion and the session's reply to it land between the stop and
        # the moment the session reads the guard's message
        evs = km._stamp_interrupt_causes(self._events(
            {"kind": "user", "md": "<task-notification>done</task-notification>", "source": {"kind": "subagent"}},
            {"kind": "thinking", "text": ""},
            {"kind": "assistant", "md": "Stopped the fan-out; waiting for direction."}))
        self.assertEqual(evs[0].get("interruptCause"), "spend")

    def test_the_settle_line_goes_like_every_machine_cut(self):
        evs = km._stamp_interrupt_causes(self._events())
        self.assertFalse(any(e.get("interruptSettle") for e in evs), "romp's own stop: the seam already says it")
        self.assertEqual(evs[0].get("settleUuids"), ["settle-1"], "the dropped settle's uuid still answers on the seam")

    def test_a_typed_prompt_first_keeps_the_users_own_stop(self):
        evs = self._events({"kind": "user", "md": "hold on, stop there", "human": True})
        km._stamp_interrupt_causes(evs)
        self.assertNotIn("interruptCause", evs[0])

    def test_a_stop_with_no_guard_message_stays_the_users(self):
        evs = [dict(MARKER), {"kind": "user", "md": "carry on", "human": True}]
        km._stamp_interrupt_causes(evs)
        self.assertNotIn("interruptCause", evs[0])


class TheBuiltChat(unittest.TestCase):
    """End to end over a transcript of the live shape: build_session reads it as the chat will."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = Path(self.td.name) / (SID + ".jsonl")

    def tearDown(self):
        self.td.cleanup()

    def _build(self, records):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in records))
        out = km.build_session(SID, T0 + 600, live_map={SID: {"state": "idle"}}, path_override=str(self.path))
        return out["events"]

    def _live_shape(self, guard):
        return [rec_user(T0, "u1", "Run the notes-api test suite and fix what fails.", ps="sdk"),
                rec_assistant(T0 + 5, "a1", "u1", tool="tu-1"),
                rec_result(T0 + 6, "r1", "a1", "tu-1"),
                rec_assistant(T0 + 8, "a2", "r1", tool="tu-2"),
                rec_user(T0 + 9, "i1", "[Request interrupted by user for tool use]", parent="a2"),
                rec_assistant(T0 + 12, "a3", "i1", text="I stopped; tell me how to continue."),
                rec_user(T0 + 14, "g1", guard, ps="sdk", parent="a3"),
                rec_assistant(T0 + 20, "a4", "g1", text="It was the test fan-out; it is stopped now.")]

    def test_the_stop_and_the_message_both_read_as_the_guard(self):
        evs = self._build(self._live_shape(BODY))
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertEqual(marker.get("interruptCause"), "spend", "the stop names the guard, not the user")
        self.assertEqual(marker.get("interruptSpend"), {"rate": "1,240", "ceiling": "1,000"})
        guard = [e for e in evs if e.get("kind") == "user" and "You are spending about" in (e.get("md") or "")]
        self.assertEqual(len(guard), 1, "the message is in the chat, once")
        g = guard[0]
        self.assertEqual((g.get("romp"), g.get("rompSystem"), g.get("spendGuard"), g.get("gist")), (True, True, True, GIST))

    def test_a_plain_user_stop_is_untouched(self):
        recs = self._live_shape("try the other approach")
        evs = self._build(recs)
        marker = next(e for e in evs if e.get("interruptMarker"))
        self.assertNotIn("interruptCause", marker)
        self.assertFalse(any(e.get("spendGuard") for e in evs))


if __name__ == "__main__":
    unittest.main()
