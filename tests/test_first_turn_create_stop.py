#!/usr/bin/env python3
"""A stop recorded before the parse's first atom makes no idle atom (event_model.synthesize_idle, 2026-10-25).

The SDK backend writes state "waiting" when it creates a session, and "working" when the first prompt is fed,
normally in the same whole second as the CLI's record of that prompt. The span between the two ended exactly at the
parse's first atom, so the old overlap test kept it: its idle atom sorted ahead of the prompt and led turn 0, which then
took the prompt in as a mid-turn input. The first turn carried no trigger, started at the create time, and held an idle
atom from its start, so the judges' "any idle atom in the last turn" readers (_session_closed, plan_units) read a
running first turn as closed: the open prompt unit became a 'work' unit planned on partial work. The first turn after a
/clear started at the previous conversation's last stop the same way. SYNTHETIC fixtures only: a private synthetic sid,
placeholder uuids, the notes-api demo, invented prompt text."""
import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_first_turn_create_stop", os.path.join(BIN, "romp-judge"))
em = jd.em

SID = "c7e1d0a5-3b2f-4e61-9a8d-5f0e2b7c4d19"     # private synthetic sid
T0 = 1_781_000_000                               # create(): state waiting
U1 = T0 + 5                                      # the first prompt is fed: state working, and the CLI writes u1
A1 = T0 + 40                                     # an assistant tool_use: the turn is still running
NOW = T0 + 60
U1_ID = "aaaaaaaa-0000-4000-8000-000000000001"
A1_ID = "aaaaaaaa-0000-4000-8000-000000000002"
CLEAR_ID = "aaaaaaaa-0000-4000-8000-0000000000c1"


def _iso(ep):
    return datetime.datetime.fromtimestamp(ep, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _prompt(t, uuid=U1_ID, text="add a search endpoint to notes-api"):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "message": {"role": "user", "content": text}}


def _tool_use(t, uuid=A1_ID):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid,
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "toolu_first_turn", "name": "Bash", "input": {"command": "ls api/"}}],
                "stop_reason": "tool_use"}}


def _reply(t, uuid=A1_ID, text="The search endpoint is in and its tests pass."):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


def _clear_cmd(t, uuid=CLEAR_ID):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid,
            "message": {"role": "user", "content": "<command-name>/clear</command-name>\n"
                                                   "<command-message>clear</command-message>\n"
                                                   "<command-args></command-args>"}}


class SynthesizeIdleSpanStart(unittest.TestCase):
    """The rule itself, over bare atoms: a span is kept only when it starts inside the atoms' timespan."""
    ATOMS = [{"t": U1, "session_id": SID}, {"t": A1, "end": A1, "session_id": SID}]

    def test_a_span_ending_at_the_first_atom_makes_no_idle_atom(self):
        self.assertEqual(em.synthesize_idle([{"t": T0, "state": "waiting"}, {"t": U1, "state": "working"}],
                                            self.ATOMS, now=NOW), [])

    def test_a_span_starting_before_the_first_atom_makes_none_however_long_it_runs(self):
        self.assertEqual(em.synthesize_idle([{"t": T0, "state": "waiting"}], self.ATOMS, now=NOW), [])

    def test_a_span_starting_inside_the_timespan_is_kept(self):
        out = em.synthesize_idle([{"t": U1, "state": "working"}, {"t": A1 + 1, "state": "waiting"}], self.ATOMS, now=NOW)
        self.assertEqual([(a["type"], a["t"], a["end"]) for a in out], [("idle", A1 + 1, NOW)])


class FirstTurnAfterTheCreateStop(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved = jd.STATE
        jd._rebind_state(Path(self.td.name))
        self.path = Path(self.td.name) / (SID + ".jsonl")
        self.states = Path(self.td.name) / "states" / (SID + ".jsonl")
        self.states.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._rebind_state(self.saved)
        self.td.cleanup()

    def _parse(self, states, recs, now):
        prev = None
        for r in recs:
            r["parentUuid"] = prev
            prev = r["uuid"]
        self.path.write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.states.write_text("".join(json.dumps(r) + "\n" for r in states))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        return jd.parsed_session(SID, [str(self.path)], now, states=str(self.states), sdk_human=True)

    def _assert_running_first_turn_open(self, s):
        self.assertEqual(len(s["turns"]), 1)
        turn = s["turns"][0]
        self.assertEqual((turn["trigger"] or {}).get("uuid"), U1_ID, "the first prompt opens turn 0")
        self.assertEqual(turn["t"], U1, "turn 0 starts at its prompt, not at the create row")
        self.assertFalse(any(a["type"] == "idle" for a in turn["atoms"]), "no idle atom inside the running turn")
        self.assertFalse(jd._session_closed(s), "a running first turn is not settled")
        units = jd.plan_units(s)
        self.assertEqual([(u[1], u[2]) for u in units], [("prompt", U1)], "the open segment yields its prompt unit only")

    def test_a_running_first_turn_reads_open_after_the_create_row(self):
        # the production shape: create() wrote waiting at T0, and the feed wrote working in u1's own second
        s = self._parse([{"t": T0, "state": "waiting"}, {"t": U1, "state": "working"}],
                        [_prompt(U1), _tool_use(A1)], NOW)
        self._assert_running_first_turn_open(s)

    def test_control_no_create_row(self):
        s = self._parse([{"t": U1, "state": "working"}], [_prompt(U1), _tool_use(A1)], NOW)
        self._assert_running_first_turn_open(s)

    def test_control_working_row_a_second_before_the_prompt(self):
        s = self._parse([{"t": T0, "state": "waiting"}, {"t": U1 - 1, "state": "working"}],
                        [_prompt(U1), _tool_use(A1)], NOW)
        self._assert_running_first_turn_open(s)

    def test_a_finished_first_turn_starts_at_its_prompt_and_still_reads_settled(self):
        stop = U1 + 35
        s = self._parse([{"t": T0, "state": "waiting"}, {"t": U1, "state": "working"}, {"t": stop, "state": "waiting"}],
                        [_prompt(U1), _reply(stop)], U1 + 100)
        self.assertEqual(len(s["turns"]), 1)
        turn = s["turns"][0]
        self.assertEqual((turn["trigger"] or {}).get("uuid"), U1_ID)
        self.assertEqual(turn["t"], U1)
        self.assertEqual([(a["type"], a["t"]) for a in turn["atoms"] if a["type"] == "idle"], [("idle", stop)],
                         "the stop after the reply still closes the turn, at its tail")
        self.assertTrue(jd._session_closed(s))

    def test_the_turn_after_a_clear_starts_at_the_clear(self):
        stop = T0                      # the previous conversation's last stop
        clr = T0 + 3 * 3600            # the /clear, three hours later
        s = self._parse([{"t": stop - 30, "state": "working"}, {"t": stop, "state": "waiting"},
                         {"t": clr, "state": "working"}, {"t": clr + 1, "state": "waiting"}],
                        [_clear_cmd(clr)], clr + 30)
        self.assertEqual(len(s["turns"]), 1)
        turn = s["turns"][0]
        self.assertEqual(turn["t"], clr, "the /clear turn starts at the /clear record, not at the stop before it")
        self.assertEqual([a["t"] for a in turn["atoms"] if a["type"] == "idle"], [clr + 1])


if __name__ == "__main__":
    unittest.main()
