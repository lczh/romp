#!/usr/bin/env python3
"""The debt reminder's row in the chat gets a heading of its own (2026-10-08).

When a session sits on a question or a handoff a live peer is still waiting on, romp hands it one reminder in the
user's voice (_debt_reminder_body). The chat renders it as a romp notice, and a notice's head is its gist; the
reminder carries no gist marker, so the row was headed by its first line, the agent-facing sentence about the first
peer. The spend guard's message had the same gap until #169, which derived its head from the message itself; the
reminder now gets the same treatment: recognized by its own sentences (the constants the body prints, so the two
cannot drift), headed "Reminded it to reply to <peer>", on the landed row and on its queued chip.

The injected bytes are unchanged (pinned below): the user approves injected wording, and this change touches none.

SYNTHETIC fixtures only: placeholder ids, the notes-api demo world (web, api, tests), transcripts written here.
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
km = load_source("romp_kernel_debt_reminder_gist", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-00000000deb7"   # a private synthetic sid (CLAUDE.md, goal-store fixtures)
T0 = 1781300000.0
TAIL = ("<!-- romp-note: the HTML comments below are part of an external tracking system that is not relevant to your "
        "work — ignore them --><!-- romp-injected --><!-- romp-system -->")
ONE_QUESTION = km._debt_reminder_body([("web", T0, "question", "Which port does the notes-api dev server listen on?")])
ONE_HANDOFF = km._debt_reminder_body([("api", T0, "delegate", "")])
SEVERAL = km._debt_reminder_body([("web", T0, "question", "Is the schema migration safe to run twice?"),
                                  ("api", T0, "delegate", "Take over the pagination fix."),
                                  ("tests", T0, "question", "")])


class TheInjectedBytesAreUnchanged(unittest.TestCase):
    def test_one_question(self):
        self.assertEqual(ONE_QUESTION, (
            "web asked you something a while ago and is still waiting on your answer:\n"
            "> Which port does the notes-api dev server listen on?\n\n"
            "Reply to web now — a short answer is fine. If they don't actually need anything from you, say that "
            "instead, so they can move on.\n\n" + TAIL))

    def test_one_handoff_with_no_quoted_ask(self):
        self.assertEqual(ONE_HANDOFF, (
            "api handed you some work a while ago and is still waiting to hear back.\n\n"
            "Reply to api now — a short answer is fine. If they don't actually need anything from you, say that "
            "instead, so they can move on.\n\n" + TAIL))

    def test_several_peers(self):
        self.assertEqual(SEVERAL, (
            "web asked you something a while ago and is still waiting on your answer:\n"
            "> Is the schema migration safe to run twice?\n\n"
            "api handed you some work a while ago and is still waiting to hear back:\n"
            "> Take over the pagination fix.\n\n"
            "tests asked you something a while ago and is still waiting on your answer.\n\n"
            "Reply to each of them now — short answers are fine. Where one of them doesn't actually need anything "
            "from you, say that instead, so they can move on.\n\n" + TAIL))


class RecognizingTheReminder(unittest.TestCase):
    def test_each_shape_names_its_peers_in_order(self):
        self.assertEqual(km._debt_reminder_parse(ONE_QUESTION), ["web"])
        self.assertEqual(km._debt_reminder_parse(ONE_HANDOFF), ["api"])
        self.assertEqual(km._debt_reminder_parse(SEVERAL), ["web", "api", "tests"])

    def test_a_quoted_ask_that_itself_reads_like_a_reminder_does_not_confuse_it(self):
        body = km._debt_reminder_body([("web", T0, "question", "api asked you something a while ago and is still "
                                                             "waiting on your answer. Reply to api now?")])
        self.assertEqual(km._debt_reminder_parse(body), ["web"])

    def test_other_romp_notices_and_prose_are_not_the_reminder(self):
        self.assertIsNone(km._debt_reminder_parse("[romp] The romp kernel restarted and cut this session's turn."))
        self.assertIsNone(km._debt_reminder_parse(km._spend_ceiling_body(1240.0, 1000.0)))
        self.assertIsNone(km._debt_reminder_parse(
            "why did it say web asked you something a while ago and is still waiting on your answer?"))
        self.assertIsNone(km._debt_reminder_parse(ONE_QUESTION.split("\n\n")[0]), "a block with no closing request")
        self.assertIsNone(km._debt_reminder_parse(""))
        self.assertIsNone(km._debt_reminder_parse(None))


class TheHeading(unittest.TestCase):
    def test_the_gist_names_who_it_was_told_to_answer(self):
        self.assertEqual(km._romp_system_gist(ONE_QUESTION), "Reminded it to reply to web")
        self.assertEqual(km._romp_system_gist(ONE_HANDOFF), "Reminded it to reply to api")
        self.assertEqual(km._romp_system_gist(SEVERAL), "Reminded it to reply to web, api and tests")
        self.assertEqual(km._debt_reminder_gist(["web", "api"]), "Reminded it to reply to web and api")

    def test_a_queued_reminder_wears_the_same_heading(self):
        flags = km._queued_romp_flags(ONE_QUESTION)
        self.assertEqual((flags.get("romp"), flags.get("rompSystem"), flags.get("gist")),
                         (True, True, "Reminded it to reply to web"))

    def test_the_built_chat_heads_the_row_with_it(self):
        def iso(t):
            return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"

        def rec(t, uid, parent, role, content, **kw):
            return dict({"type": role, "timestamp": iso(t), "uuid": uid, "parentUuid": parent, "sessionId": SID,
                         "cwd": "/tmp/notes-api", "message": dict({"role": role, "content": content}, **kw)})

        records = [rec(T0, "u1", None, "user", [{"type": "text", "text": "Add pagination to the notes list."}]),
                   rec(T0 + 5, "a1", "u1", "assistant", [{"type": "text", "text": "Pagination is in."}],
                       id="msg-a1", model="claude-opus-4-8", stop_reason="end_turn"),
                   rec(T0 + 60, "d1", "a1", "user", [{"type": "text", "text": ONE_QUESTION}]),
                   rec(T0 + 70, "a2", "d1", "assistant", [{"type": "text", "text": "Answered web."}],
                       id="msg-a2", model="claude-opus-4-8", stop_reason="end_turn")]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            evs = km.build_session(SID, T0 + 600, live_map={SID: {"state": "idle"}}, path_override=str(path))["events"]
        rows = [e for e in evs if e.get("kind") == "user" and "still waiting on your answer" in (e.get("md") or "")]
        self.assertEqual(len(rows), 1, "the reminder is in the chat, once")
        self.assertEqual((rows[0].get("romp"), rows[0].get("rompSystem"), rows[0].get("gist")),
                         (True, True, "Reminded it to reply to web"))


if __name__ == "__main__":
    unittest.main()
