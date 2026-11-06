#!/usr/bin/env python3
"""A session's status carries `turnOpen`, whether its turn is OPEN as the event model reads it (progress notes, round six,
2026-11-06).

The chat keys its fork spots and its "worked …" line on the turn's END, never on the status chip: a permission prompt, a
question, a retry and a restart's reattach move the chip while the turn goes on, and a spot or a footer keyed on the chip came
mid-turn and went again when the turn resumed, moving the rows under it. turnOpen is the reading the running-call stamp takes
(_session_working over the CLI's own turns): pinned here over a synthetic session, true with the row reading working, a
permission prompt, a picker, a retry and a dormant row's "waiting" while the turn is open (each with the chip it moves to), and
false once the turn ended (a result's "waiting" in the state log, an interrupt's "idle", the reply's end_turn). Synthetic
transcripts only (placeholder uuids, invented notes-api prose)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_chat_pages import NOW, SID, Harness, jd, km   # noqa: E402


def _iso(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _recs(end=False):
    """A prompt, a call with its result, a second call still waiting for its result; with `end`, its result and the reply."""
    def rec(k, t, typ, content, parent, stop=None):
        r = {"type": typ, "uuid": "11111111-2222-3333-4444-%012d" % k, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
             "message": {"role": typ, "content": content}}
        if typ == "assistant":
            r["message"].update(model="claude-opus-5-5", stop_reason=stop)
        return r
    tool = lambda tid, cmd: [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd, "description": "ran " + cmd}}]
    res = lambda tid: [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]
    out = [rec(1, NOW - 100, "user", "run the notes-api tests", None),
           rec(2, NOW - 95, "assistant", tool("toolu_A", "pytest -q"), None, "tool_use"),
           rec(3, NOW - 90, "user", res("toolu_A"), None),
           rec(4, NOW - 85, "assistant", tool("toolu_B", "pytest -q tests/test_api.py"), None, "tool_use")]
    if end:
        out += [rec(5, NOW - 75, "user", res("toolu_B"), None),
                rec(6, NOW - 70, "assistant", [{"type": "text", "text": "The notes-api tests pass."}], None, "end_turn")]
    for prev, r in zip(out, out[1:]):
        r["parentUuid"] = prev["uuid"]
    return out


class StatusTurnOpen(Harness):
    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")       # a state root of its own: no session host (T348)

    def _states(self, *recs):
        (jd.STATE / "states" / (SID + ".jsonl")).write_text("".join(json.dumps({"t": t, "state": st}) + "\n" for t, st in recs))

    def _status(self, state):
        self.fresh()
        row = {"state": state, "since": "", "model": "Opus 5.5", "effort": "high", "context": None}
        return km.build_session(SID, NOW, {SID: row}, floor=0)["status"]

    def test_turn_open_holds_through_a_prompt_a_question_a_retry_and_a_reattach(self):
        self.write(_recs())
        self._states((NOW - 100, "working"), (NOW - 84, "permission"))
        chips = {}
        for state in ("working", "permission", "picker", "retrying", "waiting"):
            st = self._status(state)
            chips[state] = st["state"]
            self.assertIs(st["turnOpen"], True, "%s: the turn is still open" % state)
        self.assertEqual((chips["working"], chips["permission"], chips["picker"], chips["retrying"]),
                         ("working", "needsInput", "needsInput", "retrying"), "the chip moved while the turn did not")

    def test_turn_open_turns_false_when_the_turn_ends(self):
        for end in ("waiting", "idle"):        # a result, an interrupt: the state log's stop records
            self.write(_recs())
            self._states((NOW - 100, "working"), (NOW - 80, end))
            self.assertIs(self._status("waiting")["turnOpen"], False, "%s: the turn ended" % end)
        self.write(_recs(end=True))            # the reply's end_turn
        self._states((NOW - 100, "working"))
        self.assertIs(self._status("waiting")["turnOpen"], False, "end_turn: the turn ended")


if __name__ == "__main__":
    unittest.main()
