#!/usr/bin/env python3
"""A background task's completion notice costs the closer one look, at the reply's end.

When a task the session started in the background ends (a backgrounded command, a Monitor, a background
agent), the CLI opens a turn by itself: the prompt is a harness <task-notification>, often answered in one
line. The notice is not an opener, so it folds into the turn before it, and that turn grows. The closer
re-judges a closed turn that grew, which is right: the notice can carry a real result (the job the goal
waited on finished, or failed and the reply asks what to do). But the growth landed in steps, and each
step cost a call over the whole turn's text: the notice record before any reply (the turn still read
ended, from the reply before the notice), each tool step of the reply (an idle span earlier in the turn
read as the turn's end), and the stop that closes the reply (an idle span adds no text). One notice now
costs one call, when its reply has ended; a notice the CLI never answered is still judged once the stop
lands. Synthetic fixtures only: invented text, placeholder ids."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))

SID = "5c0e7a1d-4b2f-4e8a-9d31-7f6a2b9c0e41"   # this module's own synthetic sid (CLAUDE.md, goal-store fixtures)
NOW = 1781100000
T0 = NOW - 3600
PREAMBLE = ("[SYSTEM NOTIFICATION - NOT USER INPUT]\n"
            "This is an automated background-task event, NOT a message from the user.\n")
NO_VERDICT = '{"done": [], "block": []}'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def notice(task, stamped=True):
    text = (PREAMBLE + "\n<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>toolu_%s</tool-use-id>\n"
            "<status>completed</status>\n<summary>Background command \"wait for the notes-api suite job\" "
            "completed (exit code 0)</summary>\n</task-notification>" % (task, task))
    return text, ({"kind": "task-notification"} if stamped else None)


class NotificationTurns(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._saved_llm = jd.closer_llm
        self.calls, self.replies = [], []
        self.recs, self.states, self.prev = [], [], None
        self.path = str(Path(self._td) / (SID + ".jsonl"))

        def fake(turn_text, menu_text, goal_history="", lift_whys=""):
            self.calls.append(turn_text)
            return self.replies.pop(0) if self.replies else NO_VERDICT
        jd.closer_llm = fake

    def tearDown(self):
        jd.closer_llm = self._saved_llm
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    # ── the transcript, the states log and the store ──
    def _user(self, t, text, uuid, ps="typed", origin=None):
        r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": self.prev, "promptSource": ps,
             "message": {"role": "user", "content": text}}
        if origin is not None:
            r["origin"] = origin
        self.recs.append(r)
        self.prev = uuid

    def _asst(self, t, text, uuid, tool=None):
        content = [{"type": "text", "text": text}] if text else []
        if tool:
            content.append({"type": "tool_use", "id": "tu_" + uuid, "name": tool, "input": {"file_path": "results.txt"}})
        self.recs.append({"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": self.prev,
                          "message": {"role": "assistant", "content": content,
                                      "stop_reason": "tool_use" if tool else "end_turn"}})
        self.prev = uuid

    def _result(self, t, uuid, of):
        self.recs.append({"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": self.prev,
                          "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + of,
                                                                     "content": "40 passed"}]}})
        self.prev = uuid

    def _state(self, t, state):
        self.states.append({"t": t, "state": state})

    def _flush(self):
        Path(self.path).write_text("".join(json.dumps(r) + "\n" for r in self.recs))
        (jd.STATESDIR / (SID + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in self.states))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

    def _turns(self, now):
        self._flush()
        return jd.parsed_session(SID, [self.path], now)["turns"]

    def _pass(self, now):
        """One closer pass over the transcript as it stands; the number of model calls it made."""
        self._flush()
        n = len(self.calls)
        jd._close_session(SID, self.path, now)
        return len(self.calls) - n

    def _open_goal(self):
        """The ask answered and its turn ended (a stop recorded), its one segment placed on an open goal."""
        self._state(T0, "working")
        self._user(T0, "Run the notes-api suite on the cluster and tell me when it passes.", "u1")
        self._asst(T0 + 20, "Submitted the suite as background jobs; I will report when they finish.", "a1")
        self._state(T0 + 21, "waiting")
        turns = self._turns(T0 + 30)
        self.assertEqual(len(turns), 1)
        top = SID + ":g1"
        store = {"rompUuid": SID, "seq": 1, "lastNode": top, "placementsV": jd.PLACEMENTS_V, "status": {},
                 "placements": {jd._segs(turns[0], None)[0]["id"]: top},
                 "nodes": {top: {"id": top, "text": "Run the notes-api suite", "parentId": None,
                                 "nodeComplete": False, "blocked": False, "cleared": False,
                                 "trail": [], "t": T0, "mt": T0, "log": []}}}
        jd.save_goals(SID, store)
        self.assertEqual(self._pass(T0 + 30), 1, "the ended turn is judged once")
        return top

    def _notice_lands(self, t, task, stamped=True):
        text, origin = notice(task, stamped)
        self._user(t, text, "n" + task, ps="sdk", origin=origin)
        self._state(t, "working")

    # ── the cases ──
    def test_one_notice_costs_one_call_at_the_reply_end(self):
        self._open_goal()
        t = T0 + 600
        self._notice_lands(t, "b1")
        self.assertEqual(self._pass(t + 1), 0, "the notice alone: its reply has not landed, the turn is still open")
        self._asst(t + 2, "", "r1a", tool="Read")
        self._result(t + 3, "r1r", "r1a")
        turns = self._turns(t + 4)
        self.assertTrue(jd._turn_open(turns[-1], turns),
                        "mid-reply: open, though an idle span sits earlier in the turn (it resumed, not ended)")
        self.assertEqual(self._pass(t + 4), 0, "no call while the reply runs")
        self._asst(t + 5, "Job 1 of 3 finished; waiting on the other two.", "r1b")
        self.assertEqual(self._pass(t + 6), 1, "the reply ended: the turn's growth is judged, once")
        self.assertIn("Job 1 of 3 finished", self.calls[-1], "the reply rides the call")
        self._state(t + 7, "waiting")
        self.assertEqual(self._pass(t + 8), 0, "the stop after the reply adds no text: no second call")
        store = jd.load_goals(SID)
        turns = self._turns(t + 8)
        self.assertEqual(store["closedSig"][turns[-1]["id"]], len(turns[-1]["atoms"]),
                         "the sig takes the new size, so the kernel's freshness check reads the turn as judged")
        self.assertEqual(self._pass(t + 9), 0, "and stays quiet")

    def test_a_one_line_reply_is_still_judged_and_can_complete_the_goal(self):
        top = self._open_goal()
        t = T0 + 600
        self._notice_lands(t, "b2")
        self.assertEqual(self._pass(t + 1), 0)
        self._asst(t + 3, "All three jobs passed; the suite is green.", "r2")
        self.replies.append('{"done": [{"goal": 1, "why": "the suite passed"}], "block": []}')
        self.assertEqual(self._pass(t + 4), 1, "a short reply with no tool call is judged: it can carry the result")
        self.assertTrue(jd.load_goals(SID)["nodes"][top]["nodeComplete"], "the notice's result completed the goal")

    def test_a_notice_the_cli_never_answered_is_judged_once_the_stop_lands(self):
        self._open_goal()
        t = T0 + 600
        self._notice_lands(t, "b3", stamped=False)       # an unstamped record: the text shape names it
        self.assertEqual(self._pass(t + 1), 0, "awaiting its reply")
        self._state(t + 2, "waiting")
        self.assertEqual(self._pass(t + 3), 1, "no reply came and the session stopped: the report itself is judged")

    def test_a_turn_judged_before_the_content_record_re_judges_on_any_growth(self):
        self._open_goal()
        store = jd.load_goals(SID)
        store.pop("closedContent", None)                 # as a store written before this change
        jd.save_goals(SID, store)
        self._state(T0 + 40, "working")
        self._state(T0 + 41, "waiting")                   # a second stop: an idle span, nothing else
        self.assertEqual(self._pass(T0 + 50), 1, "no record of what was read: the growth is judged, as before")
        self.assertEqual(self._pass(T0 + 51), 0)
        self._state(T0 + 60, "working")
        self._state(T0 + 61, "waiting")
        self.assertEqual(self._pass(T0 + 70), 0, "recorded now: the next idle-only growth takes no call")

    def test_a_reply_that_stopped_on_a_tool_step_is_judged(self):
        self._open_goal()
        t = T0 + 600
        self._notice_lands(t, "b4")
        self._asst(t + 2, "Checking the job log.", "r4a", tool="Read")
        self._state(t + 3, "waiting")                     # the reply stopped on its tool step, no end_turn
        turns = self._turns(t + 4)
        self.assertFalse(turns[-1]["ended"])
        self.assertFalse(jd._turn_open(turns[-1], turns), "an idle span at the tail ends the turn")
        self.assertEqual(self._pass(t + 4), 1, "its growth is judged")


if __name__ == "__main__":
    unittest.main()
