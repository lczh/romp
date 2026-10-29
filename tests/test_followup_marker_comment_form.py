#!/usr/bin/env python3
"""A card reply names its card by the marker in its tail, never by a mention of the marker's name above
it (2026-10-29).

A reply typed under a card goes out as the card's summary (quoted), the typed words, and a tail whose
`<!-- romp-goal-id: <id> -->` comment names the card (kernel _followup_body). The judge's FOLLOWUP_RE took
the FIRST bare "romp-goal-id:" anywhere in that text, and nothing neutralizes the summary or the typed
words. So a card in a session working on the marker parser itself, summarized as "the parser now reads
romp-goal-id: markers", handed the planner the id "markers": the follow-up branch looked that id up, found
no such card and never ruled on the reply, and the cited card kept its optimistic reopen hold. A summary
quoting another card's real id retargeted the reply to that card. The kernel's twin (_FOLLOWUP_GOAL_RE)
read the same first match as a parked reply's card id.

Both regexes now take the comment form only, as the event model's ROMP_INJECT_RE does for its marker.
Pinned here: the parse reads the tail's id under each kind of mention, a bundled nudge and two folded
sends still list every id in order, the planner rules on the cited card end to end, and the kernel's
parked-reply hand-back names the cited card.

SYNTHETIC fixtures only: placeholder UUIDs, the neutral notes-api demo domain.
"""
import json
import os
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
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_followup_marker_form", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781300000
SID = "11111111-2222-3333-4444-0f0110a0d029"   # private to this module (goal-store fixtures rule)
G1 = SID + ":g1"                                # the card the user replies to
G5 = SID + ":g5"                                # another card in the same session
T0 = NOW - 3600
T_R = NOW - 1800
TYPED = "looks good, ship it"

SUMMARY_PLAIN = "The notes-api parser now reads the hidden markers from the trigger"
SUMMARY_NAME = "The notes-api parser now reads romp-goal-id: markers from the trigger"
SUMMARY_OTHER_ID = "Moved the romp-goal-id: %s marker to the end of the message" % G5

SKIP = '{"ops":[{"why":"nothing to file","do":"skip"}]}'
FU_SUB = '{"ops":[{"why":"the user approved shipping","do":"sub","under":1,"text":"Ship the parser change"}]}'


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def seg_of(text):
    """A segment whose human trigger atom carries `text`, the shape _seg_followup reads."""
    return {"id": "s1", "t": T_R, "trigger": "r1",
            "atoms": [{"uuid": "r1", "type": "user", "author": "human", "t": T_R,
                       "message": {"content": [{"type": "text", "text": text}]}}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._state = jd.STATE
        jd._rebind_state(Path(self.td))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._saved = (jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None
        self.calls = []

        def fake(*a, **k):
            self.calls.append(bool(k.get("followup")))
            return FU_SUB if k.get("followup") else SKIP
        jd.plan_llm = fake

    def tearDown(self):
        jd.plan_llm, jd.opener_llm, jd._group_store = self._saved
        jd._rebind_state(self._state)

    def _seed(self, summary):
        st = {"rompUuid": SID, "seq": 5, "placementsV": jd.PLACEMENTS_V,
              "status": {G1: "completed", G5: "completed"}, "placements": {"s0": G1},
              "nodes": {G1: {"id": G1, "text": "Teach the notes-api parser to read the hidden markers",
                             "parentId": None, "nodeComplete": True, "blocked": False, "cleared": False,
                             "trail": ["s0"], "t": T0 - 100, "mt": T0 - 100, "doneWhy": "shipped",
                             "summary": summary},
                        G5: {"id": G5, "text": "Tidy the notes-api deploy script", "parentId": None,
                             "nodeComplete": True, "blocked": False, "cleared": False,
                             "trail": [], "t": T0 - 300, "mt": T0 - 300, "doneWhy": "tidied"}}}
        jd.migrate_store(st)
        jd.save_goals(SID, st)
        return st

    def _reply(self, summary, typed=TYPED):
        """The body a reply typed under G1 goes out as, built by the kernel's own composer."""
        self._seed(summary)
        body = km._followup_body(G1, None, typed)
        self.assertTrue(body.endswith("<!-- romp-goal-id: %s -->" % G1), "the tail names the cited card")
        return body


class ParseReadsTheTailId(Base):
    def _assert_targets_g1(self, body):
        self.assertEqual(jd._seg_followup(seg_of(body)), G1, "the reply targets the card it was sent from")
        self.assertEqual(jd._seg_followup_all(seg_of(body)), [G1], "and names no other id")

    def test_control_a_plain_summary(self):
        self._assert_targets_g1(self._reply(SUMMARY_PLAIN))

    def test_a_summary_that_names_the_marker(self):
        body = self._reply(SUMMARY_NAME)
        self.assertIn(SUMMARY_NAME, body, "the mention rides above the tail, unneutralized")
        self._assert_targets_g1(body)

    def test_typed_words_that_name_the_marker(self):
        self._assert_targets_g1(self._reply(SUMMARY_PLAIN, "the romp-goal-id: parsing looks fine, ship it"))

    def test_a_summary_quoting_another_cards_id(self):
        body = self._reply(SUMMARY_OTHER_ID)
        self.assertIn(G5, body)
        self._assert_targets_g1(body)

    def test_a_bundled_nudge_and_two_folded_sends_keep_every_id_in_order(self):
        st = self._seed(SUMMARY_NAME)
        bundle = km._nudge_bundle_body([G1, G5], st["nodes"], set())
        self.assertEqual(jd._seg_followup_all(seg_of(bundle)), [G1, G5])
        self.assertEqual(jd._seg_followup(seg_of(bundle)), G1, "the primary id is the first listed")
        folded = km._followup_body(G1, None, TYPED) + "\n\n" + km._followup_body(G5, None, "and tidy this")
        self.assertEqual(jd._seg_followup_all(seg_of(folded)), [G1, G5])

    def test_the_marker_without_inner_spaces_still_reads(self):
        self.assertEqual(jd._seg_followup(seg_of("ok <!--romp-goal-id:%s-->" % G1)), G1)


class PlannerRulesOnTheCitedCard(Base):
    def _run(self, summary):
        body = self._reply(summary)
        jd.optimistic_followup(SID, G1, text=TYPED, now=T_R)   # the send-time reopen hold
        recs = [uline(T0, "Teach the notes-api parser to read the hidden markers", "u1"),
                aline(T0 + 60, "Done, the parser reads them.", "a1", "u1"),
                uline(T_R, body, "r1", "a1"),
                aline(T_R + 20, "Shipped the parser change.", "ar1", "r1")]
        path = os.path.join(self.td, SID + ".jsonl")
        with open(path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._plan_session(SID, path, NOW)
        st = jd.load_goals(SID)
        subs = [nd["id"] for nd in st["nodes"].values() if nd.get("parentId") == G1]
        return st["nodes"][G1], subs

    def _assert_ruled(self, g1, subs):
        self.assertIn(True, self.calls, "the planner's follow-up branch ruled on the cited card")
        self.assertEqual(len(subs), 1, "the reply's work filed under the cited card")
        self.assertFalse(g1.get("followupPending"), "the ruling released the reopen hold")

    def test_control_a_plain_summary(self):
        self._assert_ruled(*self._run(SUMMARY_PLAIN))

    def test_a_summary_that_names_the_marker(self):
        self._assert_ruled(*self._run(SUMMARY_NAME))


class KernelReadsTheTailId(Base):
    def test_a_parked_reply_hands_back_the_cited_card(self):
        verb, frame = km._handed_back(("send", self._reply(SUMMARY_NAME)))
        self.assertEqual(verb, "askFollowUp")
        self.assertEqual(frame["itemId"], G1, "the hand-back names the card the reply was typed under")
        self.assertEqual(frame["text"], TYPED, "and returns the typed words alone")

    def test_a_plain_message_that_names_the_marker_is_no_reply(self):
        text = "> a line I quoted\n\nthe romp-goal-id: parser is fine"
        self.assertEqual(km._split_followup(text), (None, text, False, None),
                         "a mention of the marker's name does not make a message a card reply")


if __name__ == "__main__":
    unittest.main()
