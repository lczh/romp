#!/usr/bin/env python3
"""A delegation reporting back never answers the user's question filed under it (2026-10-26).

The shape, synthetically (the notes-api demo): the web session delegated a migration dry run to api,
and a question the session had put to the user (should the retry cover the search endpoint too?) sat under
that delegation card. When api reported back, the link-back completed the delegation, and
_mark_node_done discharged every block below it, the user's open question included. The next rollup
then lifted the question up beside the completed delegation as a card of its own
(_lift_completed_handoffs: a delegation's completion is evidence about the delegation only), but by
then it read Working: the status check fired on it, and the session asked the user the same question
a second time.

Pinned: the question keeps its block through every completer that reaches _mark_node_done on a
delegation card (the reply sweep's quiet report-back, the origin link-back, the planner's done op);
its card reads needs-you (rollup status "blocked", the value both the board's column and the nudge
walk read); and the nudge's fire-time re-read drops it. The scope guard: a done on an ordinary goal
still discharges the blocks of everything under it, an open delegation and its children included (a
checked-off goal's child-blocks are moot), exactly as before.

SYNTHETIC fixtures only. Private synthetic sids; every test runs under its own state root
(_rebind_state), which pins per-session hosts off and is removed whole, override journals included."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_discharge", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                        # the kernel's OWN judge instance: the fire list reads it

SENDER = "d15c4a7e-0001-4000-8000-00000000000a"   # "web": delegated the dry run, owes the user a question
PEER = "d15c4a7e-0002-4000-8000-00000000000b"     # "api": ran the dry run and reported back
MID = "msg-discharge-0001"
T = 1_787_700_000
ASK_T, REPLY_T = T + 30, T + 100                  # the question to the user, then api's report-back
TOP, DELEG, ASK = SENDER + ":g1", SENDER + ":g2", SENDER + ":g3"
QUESTION = "should the retry cover the notes-api search endpoint too?"
WHY = "discharged with the completed parent"


def _node(nid, text, parent=None, t=T, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return jd.GuardedNode(base)


def _store(nodes):
    return {"rompUuid": SENDER, "seq": 3, "nodes": dict(nodes), "placements": {}, "status": {}}


def _world(nested, quiet=False, st=None):
    """The sender's tree, built into `st` (a fresh in-memory store when None): the delegation card (a
    top, or a step of the user's release card when `nested`) with the session's question to the user
    filed under it, blocked on that question."""
    h = {"peer": PEER, "msgId": MID}
    if quiet:
        h["quiet"] = True
    nodes = {}
    parent = None
    if nested:
        nodes[TOP] = _node(TOP, "Get the notes-api release out", askAnchor="human")
        parent = TOP
    nodes[DELEG] = _node(DELEG, "↪ delegated to api: run the migration dry run", parent, handoff=h)
    nodes[ASK] = _node(ASK, "Decide the retry scope for notes-api", DELEG, t=T + 20, askAnchor="human")
    if st is None:
        st = _store({})
    st["nodes"].update(nodes)
    jd.record_verdict(st, st["nodes"][ASK], "closer", "block", ASK_T, why=QUESTION)
    jd.rollup_status(st, False)
    return st


class _Base(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "session-hosts").write_text("off")
        self._state = jd.STATE
        jd._rebind_state(self.root)
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        self._discover = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (PEER, "/dev/null", None, "api")]
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]

    def tearDown(self):
        jd.discover = self._discover
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        for sid in (SENDER, PEER):
            (self.root / "overrides" / (sid + ".jsonl")).unlink(missing_ok=True)
        jd._rebind_state(self._state)
        shutil.rmtree(self.root, ignore_errors=True)

    def assertQuestionStands(self, st, card):
        """The question is still the user's to answer, on the card the lift left it on."""
        jd.rollup_status(st, False)                   # any writer's next rollup: the block must survive it
        ask = st["nodes"][ASK]
        self.assertTrue(st["nodes"][DELEG].get("nodeComplete"), "precondition: the delegation completed")
        self.assertNotEqual(ask.get("parentId"), DELEG, "precondition: the lift moved the question up")
        self.assertTrue(ask.get("blocked"),
                        "nobody answered the question: the delegation reporting back is no answer to it")
        self.assertEqual(ask.get("blockWhy"), QUESTION, "the ask the card presents is the one still owed")
        self.assertEqual([e for e in ask.get("log") or [] if e.get("kind") == "unblock"], [],
                         "no discharge row lands on a node the completion does not absorb")
        self.assertEqual(st["status"].get(card), "blocked",
                         "the card reads needs-you, not Working (the board's column and the nudge walk read this)")
        self.assertEqual(km._nudge_fire_list(st, [(card, 1, False)], arm_t=REPLY_T, seen_t=REPLY_T), [],
                         "no status check on a card that is waiting on the user")


class ReportBackKeepsTheQuestion(_Base):
    def test_quiet_report_back_keeps_a_question_filed_under_the_delegation(self):
        # the reported shape: a quiet-filed delegation card, a top, completes on the peer's reply
        jd.save_goals(SENDER, _world(nested=False, quiet=True, st=jd.load_goals(SENDER)))
        jd.MESSAGES.write_text(json.dumps({"t": REPLY_T, "ev": "sent", "id": "r1", "from_id": PEER,
                                           "to_id": SENDER, "kind": "coordinate",
                                           "body": "dry run clean, no drift"}) + "\n")
        self.assertEqual(jd.run_propagate(now=REPLY_T + 60), 1, "the report-back completed the delegation")
        st = jd.load_goals(SENDER)
        self.assertIsNone(st["nodes"][ASK].get("parentId"), "lifted to a card of its own")
        self.assertQuestionStands(st, ASK)

    def test_origin_link_back_keeps_a_question_filed_under_the_delegation(self):
        # the recipient's own goal completes and the link-back checks the delegation step off; the
        # question moves up onto the release card, which must keep reading needs-you
        jd.save_goals(SENDER, _world(nested=True, st=jd.load_goals(SENDER)))
        rst = {"rompUuid": PEER, "seq": 1, "placements": {}, "status": {},
               "nodes": {PEER + ":g1": _node(PEER + ":g1", "Run the migration dry run",
                                             origin={"peer": SENDER, "goalId": DELEG, "msgId": MID})}}
        jd.record_verdict(rst, rst["nodes"][PEER + ":g1"], "closer", "done", REPLY_T, why="dry run clean")
        jd.save_goals(PEER, rst)
        self.assertEqual(jd.run_propagate(now=REPLY_T + 60), 1, "the link-back completed the delegation")
        st = jd.load_goals(SENDER)
        self.assertEqual(st["nodes"][ASK].get("parentId"), TOP, "lifted beside the delegation")
        self.assertQuestionStands(st, TOP)

    def test_planner_done_on_the_delegation_keeps_the_question(self):
        # the lift covers every completer, so the discharge must agree with it for the planner's done too
        st = _world(nested=False)
        jd.apply_plan(st, "seg-report", REPLY_T, [{"do": "done", "goal": 1, "why": "api reported the dry run clean"}],
                      [{"id": DELEG}])
        self.assertQuestionStands(st, ASK)


class OrdinaryDoneStillDischarges(_Base):
    def test_a_done_on_the_card_above_the_delegation_still_moots_the_question(self):
        # the scope guard: the release card itself ruled done is the judges' word on the whole tree, so the
        # designed absorb applies to everything under it, the open delegation and its question included
        st = _world(nested=True)
        jd.apply_plan(st, "seg-ship", REPLY_T, [{"do": "done", "goal": 1, "why": "the release went out"}],
                      [{"id": TOP}])
        ask = st["nodes"][ASK]
        self.assertFalse(ask.get("blocked"), "a checked-off goal's child-blocks are moot")
        self.assertIn(("planner", "unblock", WHY), [(e.get("src"), e.get("kind"), e.get("why")) for e in ask["log"]])
        jd.rollup_status(st, True)
        self.assertEqual(st["status"].get(TOP), "completed")
        self.assertEqual(ask.get("parentId"), DELEG, "no lift: the ruling was on the card, not the delegation")


if __name__ == "__main__":
    unittest.main()
