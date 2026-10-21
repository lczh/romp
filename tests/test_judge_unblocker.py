#!/usr/bin/env python3
"""The UNBLOCKER (the user 2026-07-11): a goal blocked on a question stays blocked forever unless
work files on that exact node — an answer given in passing files wherever the planner judges it to
serve, so a dormant blocked goal never hears it (nimbus: the card sat in Needs-you for hours on a
buried sub whose mAh/logging question the very next conversation stretch had answered). The pass
re-examines open blocked goals — subs AND, since 2026-07-16 (g48), tops, whose answer can land on a
sibling card's thread that no other heal path reads — against the conversation since their block and
lifts via the same record_verdict("unblock") every other lift uses. Event-gated per node (blockCheckT
vs the newest ended turn), model stubbed. All fixtures SYNTHETIC (invented text, placeholder UUIDs).
"""
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

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class UnblockerBase(unittest.TestCase):
    def setUp(self):
        self._saved_state = jd.STATE
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._saved_llm = jd.unblock_llm
        self.calls = []

    def tearDown(self):
        jd.unblock_llm = self._saved_llm
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _stub(self, reply):
        def fake(blocks_text, since_text, completed_text=""):
            self.calls.append((blocks_text, since_text, completed_text))
            return reply
        jd.unblock_llm = fake

    def _store(self, block_t, top_done=False, block_top_instead=False):
        """top > sub, the sub blocked at block_t on a concrete question. Built as plain dicts BEFORE
        save (protected flags are diary-owned once loaded — GuardedNode). block_top_instead puts the
        block on the TOP (sub stays open); top_done completes the ancestor."""
        top, sub = SID + ":g1", SID + ":g2"
        blk = {"blocked": True, "blockWhy": "what is the pack's mAh rating?",
               "log": [{"ev_t": block_t, "src": "planner", "kind": "block",
                        "why": "what is the pack's mAh rating?", "at": block_t}]}
        opn = {"blocked": False, "log": []}
        store = {"rompUuid": SID, "seq": 2, "lastNode": top, "placements": {}, "status": {},
                 "nodes": {
                     top: dict({"id": top, "text": "enable the autonomous run", "parentId": None,
                                "nodeComplete": top_done, "cleared": False,
                                "trail": [], "t": T0, "mt": T0},
                               **(blk if block_top_instead else opn)),
                     sub: dict({"id": sub, "text": "clarify the worker pool", "parentId": top,
                                "nodeComplete": False, "cleared": False,
                                "trail": [], "t": T0, "mt": block_t},
                               **(opn if block_top_instead else blk)),
                 }}
        jd.save_goals(SID, store)
        return top, sub

    def _transcript(self, turns):
        """Write a transcript of ENDED turns [(t, user_text, reply_text), ...] + return its path."""
        recs, prev = [], None
        for i, (t, ask, reply) in enumerate(turns):
            u, a = "u%d" % i, "a%d" % i
            recs.append(uline(t, ask, u, parent=prev))
            recs.append(aline(t + 5, reply, a, parent=u))
            prev = a
        # a final ended turn needs a successor or an idle terminator; a trailing user line ends the last reply turn
        p = Path(self._td) / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        return str(p)

    def _add_completed_sibling(self, done_at, why="the pool sizing table shipped", synth=False,
                               title="ship the pool sizing table"):
        """Rebuild the saved store with a completed sibling top carrying its own done verdict row.
        Written as plain dicts BEFORE save (protected flags are diary-owned once loaded)."""
        store = json.loads(json.dumps(jd.load_goals(SID)))
        other = SID + ":g5"
        store["nodes"][other] = {
            "id": other, "text": title, "parentId": None,
            "nodeComplete": True, "blocked": False, "cleared": False, "doneWhy": why,
            "trail": [], "t": T0, "mt": done_at,
            "log": [{"ev_t": done_at, "src": "closer", "kind": "done", "why": why,
                     **({"synth": True} if synth else {}), "at": done_at}]}
        jd.save_goals(SID, store)
        return other


class InterruptBlocksAreNotUnblockerBusiness(UnblockerBase):
    """INTERRUPT-src blocks are out of the candidate set (the user 2026-08-08): "waiting on your next
    instruction" is not a question session output can answer — the kernel lifts it on the user's
    re-engagement, and a done verdict completes over it. Re-examining one here lifted a stop-block
    seconds after placement, off the cut turn's own settling output, and the stopped session's card
    went back to Working with auto-nudge suppressed: invisible-blocked."""

    def _interrupt_store(self, block_t):
        top = SID + ":g1"
        why = jd.INTERRUPT_BLOCK_WHY
        store = {"rompUuid": SID, "seq": 1, "lastNode": top, "placements": {}, "status": {},
                 "nodes": {top: {"id": top, "text": "wire the widget", "parentId": None,
                                 "nodeComplete": False, "cleared": False, "blocked": True,
                                 "blockWhy": why, "trail": [], "t": T0, "mt": block_t,
                                 "log": [{"ev_t": block_t, "src": "interrupt", "kind": "block",
                                          "why": why, "at": block_t}]}}}
        jd.save_goals(SID, store)
        return top

    def test_a_stop_block_is_never_shown_to_the_model(self):
        top = self._interrupt_store(block_t=T0 + 100)
        self.assertEqual(jd._blocked_sub_candidates(jd.load_goals(SID)), [])
        path = self._transcript([(T0 + 200, "<task-notification>a background task finished</task-notification>",
                                  "picked the work back up and shipped it")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the session picked the work back up"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "no model call — the stop-block was never a candidate")
        self.assertTrue(jd.load_goals(SID)["nodes"][top]["blocked"],
                        "only the user's re-engagement lifts a stop-block")

    def test_a_newer_real_judge_block_re_enters_the_set(self):
        top = self._interrupt_store(block_t=T0 + 100)
        store = jd.load_goals(SID)
        jd.record_verdict(store, store["nodes"][top], "closer", "block", T0 + 300, why="pick a port")
        jd.save_goals(SID, store)
        cands = jd._blocked_sub_candidates(jd.load_goals(SID))
        self.assertEqual([nid for nid, _nd, _bt in cands], [top],
                         "the LATEST block row decides — a real question is examined again")


class Unblocker(UnblockerBase):
    def test_an_answered_in_passing_block_is_lifted(self):
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 50, "set up the load experiment", "planning it"),
                                 (T0 + 200, "it is a 10,000mAh pack, go ahead", "great, proceeding"),
                                 (T0 + 300, "how is it going?", "campaign armed")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the user said 10,000mAh in a later message"}]}')
        lifted = jd._unblock_session(SID, path, NOW)
        self.assertEqual(lifted, [sub])
        store = jd.load_goals(SID)
        self.assertFalse(store["nodes"][sub]["blocked"], "the stale block is lifted")
        self.assertIn("answered in passing", (store["nodes"][sub].get("log") or [])[-1].get("why", ""),
                      "the lift rides the diary with its provenance")
        self.assertIn("what is the pack's mAh rating?", self.calls[0][0], "the block's question is shown")
        self.assertIn("10,000mAh", self.calls[0][1], "the after-conversation is shown")

    def test_a_hold_keeps_the_block_and_the_watermark_prevents_reasking(self):
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "unrelated other work", "done that")])
        self._stub('{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][sub]["blocked"], "held: still genuinely waiting")
        self.assertGreater(store["nodes"][sub].get("blockCheckT") or 0, T0 + 100,
                           "the watermark advanced to the examined evidence")
        # same evidence again → no second model call (event-gated)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1, "no new ended turn → no re-ask")
        # a NEWER ended turn re-arms the examination
        path2 = self._transcript([(T0 + 200, "unrelated other work", "done that"),
                                  (T0 + 900, "more talk", "more replies"),
                                  (T0 + 950, "tail", "tail reply")])
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        jd._unblock_session(SID, path2, NOW)
        self.assertEqual(len(self.calls), 2, "new evidence → examined again")

    def test_a_blocked_top_is_examined_and_lifted(self):
        # Flipped 2026-07-16 (g48): a blocked TOP's designed heal paths — a reply on its own thread, a
        # placement under it — cover only answers landing ON the card. Two cards blocked on the same
        # clarification, the user answered on the sibling's thread, and the other top sat in Needs-you
        # with no mechanism able to reach it. Tops are unblocker candidates now.
        top, sub = self._store(block_t=T0 + 100, block_top_instead=True)
        path = self._transcript([(T0 + 200, "the plan is confirmed, 10,000mAh", "proceeding")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "confirmed on the sibling thread"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [top])
        store = jd.load_goals(SID)
        self.assertFalse(store["nodes"][top]["blocked"], "the answered top block is lifted")
        self.assertIn("what is the pack's mAh rating?", self.calls[0][0], "the top's question was shown")

    def test_a_cleared_blocked_top_is_not_examined(self):
        top, sub = self._store(block_t=T0 + 100, block_top_instead=True)
        store = jd.load_goals(SID)
        with jd._authority():
            store["nodes"][top]["cleared"] = True
        jd.save_goals(SID, store)
        path = self._transcript([(T0 + 200, "the plan is confirmed", "proceeding")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "x"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "a dismissed card is out of the candidate set")

    def test_a_block_under_a_completed_ancestor_is_skipped_as_moot(self):
        top, sub = self._store(block_t=T0 + 100, top_done=True)
        path = self._transcript([(T0 + 200, "later talk", "later reply")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "x"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "any_blocked already ignores blocks inside a completed subtree")

    def test_a_parse_failure_holds_and_gives_up_after_the_cap(self):
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "the answer is 10,000mAh", "noted")])
        self._stub("not json at all")
        for _ in range(jd.JUDGE_FAIL_CAP):
            self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][sub]["blocked"], "malformed replies never lift anything")
        self.assertGreater(store["nodes"][sub].get("blockCheckT") or 0, 0,
                           "after the give-up cap the watermark advances (a newer turn re-arms)")
        n_before = len(self.calls)
        jd._unblock_session(SID, path, NOW)
        self.assertEqual(len(self.calls), n_before, "given up on this evidence — no more calls")

    def test_a_lift_cut_off_at_its_last_brace_still_lands(self):
        # The greedy parse turned this reply into a parse failure; after the cap the lift was gone and
        # the answered card stayed in Needs-you. _json_obj closes a clean truncation instead.
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "the answer is 10,000mAh, go ahead", "noted, proceeding")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the user said 10,000mAh later"}]')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [sub])
        self.assertFalse(jd.load_goals(SID)["nodes"][sub]["blocked"], "the answered block is lifted")
        self.assertEqual(len(self.calls), 1, "one call, no parse retry")

    def test_a_lift_followed_by_an_aside_with_a_brace_still_lands(self):
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "the answer is 10,000mAh, go ahead", "noted, proceeding")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the user said 10,000mAh later"}]}\n\n'
                   'Note: the pack spec lives in {repo}/notes-api.')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [sub])
        self.assertFalse(jd.load_goals(SID)["nodes"][sub]["blocked"], "the answered block is lifted")

    def test_an_empty_reply_is_a_failed_call_and_retries_next_pass(self):
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "the answer is 10,000mAh", "noted")])
        self._stub("")
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        store = jd.load_goals(SID)
        self.assertFalse(store["nodes"][sub].get("blockCheckT"), "no watermark advance on a failed call")

    def test_a_mid_call_store_change_is_never_clobbered_and_the_drift_is_logged(self):
        # The model call takes seconds and save_goals is last-writer-wins: verdicts must apply to a
        # FRESH load, never the pre-call snapshot. Simulate a user acting mid-call: the stub (running
        # where the model call would) rewrites the store — resolves the blocked sub AND adds a new
        # node — then returns a lift. The lift must be SKIPPED (drift-skip row logged), the user's
        # resolution must stand, and the concurrently-added node must survive the pass's save.
        top, sub = self._store(block_t=T0 + 100)
        path = self._transcript([(T0 + 200, "it is a 10,000mAh pack", "noted")])
        other = SID + ":g9"

        def fake(blocks_text, since_text, completed_text=""):
            self.calls.append((blocks_text, since_text, completed_text))
            st = jd.load_goals(SID)
            jd.record_verdict(st, st["nodes"][sub], "user", "done", T0 + 500,
                              why="crossed off by the user mid-call")
            st["nodes"][other] = {"id": other, "text": "typed while the model ran", "parentId": None,
                                  "nodeComplete": False, "blocked": False, "cleared": False,
                                  "trail": [], "t": T0 + 500, "mt": T0 + 500, "log": []}
            jd.save_goals(SID, st)
            return '{"verdicts": [{"n": 1, "do": "lift", "why": "the user said 10,000mAh"}]}'
        jd.unblock_llm = fake

        self.assertEqual(jd._unblock_session(SID, path, NOW), [], "no lift lands on a node that moved on")
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][sub].get("nodeComplete"), "the user's mid-call resolution stands")
        self.assertIn(other, store["nodes"], "a node added mid-call survives the pass's save (fresh-load apply)")
        rows = [json.loads(line) for line in jd.ERRORS.read_text().splitlines()] if jd.ERRORS.exists() else []
        self.assertTrue(any(r.get("err") == "drift-skip" for r in rows),
                        "the race is observable: a drift-skip row lands in judge-errors")


class UnblockerCompletedSince(UnblockerBase):
    """The two-channel evidence gate (the user 2026-08-08): the session's DONE verdicts ride the
    examine as <completed-since> (durable — the 9k conversation tail scrolls, a completion doesn't),
    and a new done FILING is itself an arming event, so supersession can lift a blocked ask even when
    no new turn ever arrives. All fixtures synthetic."""

    def test_completed_since_rides_the_evidence_and_can_lift(self):
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 300)
        path = self._transcript([(T0 + 200, "keep going on the sizing", "on it"),
                                 (T0 + 400, "anything else?", "wrapping up")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the sizing table shipped past it"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [sub])
        self.assertIn("ship the pool sizing table", self.calls[0][2],
                      "the completed sibling is shown as evidence")
        self.assertIn("the pool sizing table shipped", self.calls[0][2],
                      "with the done verdict's why")
        self.assertIn("<completed-since>", jd.UNBLOCK_SYS, "the prompt names the section it receives")

    def test_a_done_filing_arms_the_examination_without_new_turns(self):
        # Every turn predates the block: the turn gate can never arm. A sibling completion filed
        # afterwards must — the font-size offer card sat 1.3h with blockCheckT=None while two dones
        # filed after its block (2026-08-08 study, synthetic equivalent here).
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 300)
        path = self._transcript([(T0 + 40, "please size the pool", "asking a question and idling")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the completion covers it"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [sub])
        store = jd.load_goals(SID)
        self.assertFalse(store["nodes"][sub]["blocked"],
                         "the lift lands even though its examine had no new turn (ev floor = the block)")
        self.assertEqual(self.calls[0][1], "", "no conversation since the block existed")
        self.assertIn("ship the pool sizing table", self.calls[0][2])

    def test_the_done_watermark_prevents_reasking_until_a_newer_filing(self):
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 300)
        path = self._transcript([(T0 + 40, "please size the pool", "asking and idling")])
        self._stub('{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1)
        # same filings again → no second call (blockCheckDoneT watermark)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1, "no new filing → no re-ask")
        # a NEWER done filing re-arms the examination
        store = json.loads(json.dumps(jd.load_goals(SID)))
        store["nodes"][SID + ":g5"]["log"].append(
            {"ev_t": T0 + 500, "src": "closer", "kind": "done",
             "why": "the follow-on table also shipped", "at": T0 + 500})
        jd.save_goals(SID, store)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 2, "a new done filing → examined again")

    def test_a_done_armed_examine_never_regresses_the_turn_watermark(self):
        # REGRESSION GUARD for the rejudging latch (PR #144): kernel _block_check_floor reads
        # blockCheckT against plain-reply TURN times. A done filing is wall-clock and sorts after
        # every turn, so it must advance only its OWN watermark — a filing time written into
        # blockCheckT would release the latch before any judge saw the reply it latched on.
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 300)
        path = self._transcript([(T0 + 40, "please size the pool", "asking and idling")])
        self._stub('{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}')
        jd._unblock_session(SID, path, NOW)
        store = jd.load_goals(SID)
        nd = store["nodes"][sub]
        newest_turn = T0 + 45                          # the one ended turn's reply line
        self.assertLessEqual(nd.get("blockCheckT") or 0, newest_turn,
                             "blockCheckT stays in the turn-time domain")
        self.assertEqual(nd.get("blockCheckDoneT"), T0 + 300,
                         "the filing advanced its own watermark only")

    def test_pre_block_dones_neither_arm_nor_ride(self):
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 50)   # finished BEFORE the ask existed
        path = self._transcript([(T0 + 40, "please size the pool", "asking and idling")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "x"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "nothing new since the block → no examine at all")

    def test_synth_settle_dones_neither_arm_nor_ride(self):
        # An episode-boundary settle asserts the conversation ended, not that work was delivered —
        # it is not supersession evidence and must not wake the examine.
        top, sub = self._store(block_t=T0 + 100)
        self._add_completed_sibling(done_at=T0 + 300, synth=True)
        path = self._transcript([(T0 + 40, "please size the pool", "asking and idling")])
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "x"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "a synth settle row is not an arming event")
        # and when a real turn arms the examine, the synth row still doesn't ride as evidence
        path2 = self._transcript([(T0 + 40, "please size the pool", "asking and idling"),
                                  (T0 + 400, "unrelated talk", "unrelated reply")])
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self._stub('{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}')
        jd._unblock_session(SID, path2, NOW)
        self.assertEqual(self.calls[-1][2], "", "the synth completion is excluded from the section")


class SameReplyDones(UnblockerBase):
    """A done the closer files in the SAME reply as a block is not news to that block. The closer journals both
    rows with ev_t = the turn's trigger and `at` = when it filed them, minutes later. The done arm and the
    completed-since cut read a done's filing time, so they read the block's filing time too (_block_filed_at):
    read against the block's evidence time, a reply saying "docs updated, shall I deploy?" armed a second look
    at the deploy question on the very evidence it was asked from, and a lift there moved the card back to
    Working on nothing new. The block rows in the classes above are filed at their evidence time, so none of
    them could see this. Fixtures synthetic."""

    HOLD = '{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}'

    def _closer_reply(self, later=(), older_block=False):
        """Two open tops, an ended turn at T0+100 (plus any `later` turns), and the REAL closer writer filing
        the docs done and the deploy block from that turn in one reply, the done first in menu order.
        older_block adds a third top blocked (and filed) at T0+50, before the reply.
        Returns the deploy node id, the transcript path and the block's filing time."""
        docs, deploy, region = SID + ":g1", SID + ":g2", SID + ":g3"
        nodes = {docs: {"id": docs, "text": "update the docs", "parentId": None,
                        "nodeComplete": False, "blocked": False, "cleared": False,
                        "trail": [], "t": T0, "mt": T0, "log": []},
                 deploy: {"id": deploy, "text": "deploy the notes-api release", "parentId": None,
                          "nodeComplete": False, "blocked": False, "cleared": False,
                          "trail": [], "t": T0, "mt": T0, "log": []}}
        if older_block:
            why = "which region should the notes-api run in?"
            nodes[region] = {"id": region, "text": "pick the notes-api region", "parentId": None,
                             "nodeComplete": False, "blocked": True, "blockWhy": why, "cleared": False,
                             "trail": [], "t": T0, "mt": T0 + 50,
                             "log": [{"ev_t": T0 + 50, "src": "planner", "kind": "block", "why": why,
                                      "at": T0 + 50}]}
        jd.save_goals(SID, {"rompUuid": SID, "seq": 3, "lastNode": deploy, "placements": {}, "status": {},
                            "nodes": nodes})
        path = self._transcript([(T0 + 100, "update the docs for the release",
                                  "Docs updated. Shall I deploy to prod now?")] + list(later))
        store = jd.load_goals(SID)
        jd.apply_close(store, [store["nodes"][docs], store["nodes"][deploy]],
                       {"done": {1: "docs updated"}, "block": {2: "deploy to prod now?"}, "awaiting": {}},
                       t=T0 + 100, touched=2)
        jd.save_goals(SID, store)
        store = jd.load_goals(SID)
        self.assertTrue(store["nodes"][docs]["nodeComplete"], "the closer filed the done")
        self.assertTrue(store["nodes"][deploy]["blocked"], "and the block, in the same reply")
        row = [e for e in store["nodes"][deploy]["log"] if e.get("kind") == "block"][-1]
        self.assertEqual(row["ev_t"], T0 + 100, "the block's evidence time is the turn")
        self.assertGreater(row["at"], T0 + 100, "its filing time is later, as a closer files it")
        return deploy, path, row["at"]

    def test_the_replys_own_done_neither_arms_nor_lifts(self):
        deploy, path, _filed = self._closer_reply()
        self._stub('{"verdicts": [{"n": 1, "do": "lift", "why": "the docs work was done anyway"}]}')
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(self.calls, [], "nothing was filed or said after the block: no second look")
        self.assertTrue(jd.load_goals(SID)["nodes"][deploy]["blocked"], "the card stays in Needs-you")

    def test_a_done_filed_after_the_block_still_arms_and_rides_alone(self):
        deploy, path, filed = self._closer_reply()
        self._add_completed_sibling(done_at=filed + 60, why="changelog written", title="write the changelog")
        self._stub(self.HOLD)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1, "a done filed after the block is new: examined")
        self.assertEqual(self.calls[0][2], "- write the changelog: changelog written",
                         "only the later completion rides; the block's own reply's done is not news to it")

    def test_a_later_turn_arms_without_the_replys_own_done_riding(self):
        deploy, path, _filed = self._closer_reply(later=[(T0 + 900, "what else is left?", "only the deploy")])
        self._stub(self.HOLD)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1, "a newer ended turn arms, as ever")
        self.assertIn("what else is left?", self.calls[0][1], "the conversation since the block is shown")
        self.assertEqual(self.calls[0][2], "", "the block's own reply's done is not shown as completed since it")

    def test_an_older_block_due_on_the_replys_done_does_not_take_the_new_one_along(self):
        # the reply's done IS news to a block filed before the reply, so that one is examined; the block the reply
        # filed is not due, and a lift in that call can only name the older one
        deploy, path, _filed = self._closer_reply(older_block=True)
        self._stub(self.HOLD)
        self.assertEqual(jd._unblock_session(SID, path, NOW), [])
        self.assertEqual(len(self.calls), 1, "the older block is examined on the later done")
        self.assertIn("which region should the notes-api run in?", self.calls[0][0])
        self.assertNotIn("deploy to prod now?", self.calls[0][0], "the reply's own block is not re-examined")
        self.assertIn("- update the docs: docs updated", self.calls[0][2], "the done rides for the older block")


class BlockEvidenceTime(unittest.TestCase):
    """jd._block_ev_t, the one rule for WHEN a block was asked: the unblocker's candidates, the feed card's blocked
    yield and its session-level twin, and the brief's stamps all read it. mt is not block evidence: a retitle, a
    merge, a duplicate step landing or a to-do sync moves it with nothing new said about the block. Pure over dict
    nodes; no store on disk."""

    def test_the_newest_block_rows_evidence_time_never_mt(self):
        nd = {"t": T0, "mt": T0 + 900, "log": [
            {"ev_t": T0 + 100, "src": "planner", "kind": "block", "at": T0 + 100},
            {"ev_t": T0 + 200, "src": "planner", "kind": "unblock", "at": T0 + 200},
            {"ev_t": T0 + 300, "src": "closer", "kind": "block", "at": T0 + 450},
            {"ev_t": T0 + 500, "src": "agent", "kind": "done", "at": T0 + 500}]}
        self.assertEqual(jd._block_ev_t(nd), T0 + 300, "the evidence time: not its arrival (450), not mt (900)")

    def test_a_row_without_an_evidence_time_falls_back_to_its_own_filing_time(self):
        nd = {"t": T0, "mt": T0 + 900, "log": [{"ev_t": None, "src": "agent", "kind": "block", "at": T0 + 120}]}
        self.assertEqual(jd._block_ev_t(nd), T0 + 120, "the row's own `at`, never the node's mt")

    def test_the_done_arms_floor_is_the_block_rows_filing_time(self):
        # the unblocker's done arm and completed-since cut read a done's filing time, so they read the block's
        # filing time (_block_filed_at), floored at its evidence time; the evidence rule itself is unchanged
        nd = {"t": T0, "mt": T0 + 900, "log": [
            {"ev_t": T0 + 100, "src": "planner", "kind": "block", "at": T0 + 100},
            {"ev_t": T0 + 300, "src": "closer", "kind": "block", "at": T0 + 450},
            {"ev_t": T0 + 500, "src": "agent", "kind": "done", "at": T0 + 600}]}
        self.assertEqual(jd._block_ev_t(nd), T0 + 300)
        self.assertEqual(jd._block_filed_at(nd, T0 + 300), T0 + 450, "the filing: not the evidence, not a done")
        legacy = {"log": [{"ev_t": T0 + 300, "src": "closer", "kind": "block"}]}
        self.assertEqual(jd._block_filed_at(legacy, T0 + 300), T0 + 300, "no filing time: the evidence time")

    def test_no_block_row_is_no_time(self):
        self.assertIsNone(jd._block_ev_t({"t": T0, "mt": T0 + 900, "blocked": True}), "a pre-diary node")
        self.assertIsNone(jd._block_ev_t({"t": T0, "mt": T0 + 900, "log": [
            {"ev_t": T0 + 5, "src": "agent", "kind": "done", "at": T0 + 5}]}), "rows, none of them a block")

    def test_the_unblockers_candidates_read_the_same_rule(self):
        # an mt bumped past the block never moves a candidate's block time; a pre-diary node with no row reads
        # from its birth, never its mt: an earlier bound shows the examiner more evidence, a later one could hide
        # the answer
        a, b = SID + ":a", SID + ":b"
        store = {"nodes": {
            a: {"id": a, "parentId": None, "blocked": True, "t": T0, "mt": T0 + 900,
                "log": [{"ev_t": T0 + 300, "src": "closer", "kind": "block", "at": T0 + 450}]},
            b: {"id": b, "parentId": None, "blocked": True, "t": T0 + 10, "mt": T0 + 900}}}
        self.assertEqual({nid: bt for nid, _nd, bt in jd._blocked_sub_candidates(store)},
                         {a: T0 + 300, b: T0 + 10})


if __name__ == "__main__":
    unittest.main()
