#!/usr/bin/env python3
"""A nudge reply whose planner call fails stays due; it is not retired as an empty ruling (2026-10-21).

The bug, reproduced SYNTHETICALLY: a working goal gets the automatic status check, and the agent answers
that the eval job is still running, a watcher is armed, and it needs nothing. The planner pass that reads
that reply hits a transient API failure (a 529, a usage limit, a timeout, the retry pause), so plan_llm
returns ''. The nudge branch of _plan_session turned that into empty ops and applied them, which wrote
placements[seg] = None: the reply was marked processed for good and never planned again. The kernel's
failed-nudge gate (_nudge_response_ready) reads a placed key as "the planner ruled on the reply", and with
the goal still working it filed the needs-your-direction block, a false interrupt on a reply that said
nothing was needed. A reply that did not parse took the same path on its first try.

Under test, the work-run's two guards now applied per nudge target:
  - a transient failure writes nothing and leaves the unit due (the stage marked incomplete), so the next
    pass plans the reply and files its own verdict, and the kernel's gate keeps waiting meanwhile;
  - a parse reject or a content refusal takes a PLAN_PARSE_RETRIES strike, and only the capping strike
    retires the target unresolved, with one give-up row (a refusal must not retry forever);
  - in a BUNDLED nudge the bare key, ordered last, is the one the kernel reads for every target, so a
    failure on any target stops the bundle before that key is placed.

And the regression that change brought (the review of #334): the nudge branch reopens the goal BEFORE the
planner call, because the call's menu is read from the reopened goal, so a call that failed and stopped the
branch with no write still left that reopen in the store, and the save at the end of the pass published it.
Every failed pass appended another identical "reopened (nudge)" row: 70 passes left 64 of them (LOG_CAP),
evicting the goal's whole earlier history, and a blocked goal read Working after one failed call. Under test:
  - no ruling, no reopen: a failed call, a struck reply and a give-up leave the goal's diary as it was, and a
    pass that changes nothing writes nothing;
  - the same for the other exits that apply nothing (the review of #340): a goal with no menu, a plan that
    stands down at the write (after a ruling, or after the give-up, which undoes twice), and a served
    ruling with no op, which still retires the reply once but leaves a blocked goal blocked;
  - once the planner serves, the reply is placed once, with the one reopen that rides its ruling;
  - a reply held by a failure that never strikes (no refusal flag, no pause) is said ONCE in the error log,
    naming the reply, not once per pass.
All fixtures are SYNTHETIC: a private placeholder uuid, the notes-api demo, invented reply text."""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
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
km = load_source("romp_kernel_nudge_failed_call", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# a PRIVATE synthetic sid (the goal-store fixtures rule, 2026-08-24): this module mints goals, and the
# shared 11111111-2222-… placeholder can be re-flagged by other modules' journaled overrides
SID = "11111111-2222-4333-8444-000000001021"
G1, G2 = SID + ":g1", SID + ":g2"
T = 3000
AWAITING = ('{"ops":[{"do":"awaiting","goal":1,"why":"the eval job is still running; a watcher is armed",'
            '"kind":"task"}]}')
REFUSAL = "API Error: Sonnet 5 can't help with this. Start a new session to continue."


def _iso(ep):
    return datetime.fromtimestamp(ep, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _top(gid, text, log=()):
    return {"id": gid, "text": text, "parentId": None, "nodeComplete": False, "blocked": False,
            "cleared": False, "trail": [], "t": 1000, "mt": 2000, "log": [dict(e) for e in log]}


class _Base(unittest.TestCase):
    TARGETS = (G1,)
    HISTORY = ()                                  # earlier diary rows on G1, all before the nudge (T)

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._old_state = jd.STATE
        jd._rebind_state(Path(self.td))          # GOALDIR, ERRORS and every derived dir move too
        jd._judge_ctx.last_call_fail = None
        jd._judge_ctx.paused = False
        jd._NUDGE_HELD.clear()                   # the per-process held-reply count: a fresh store, a fresh count
        titles = {G1: "Run the notes-api eval", G2: "Rebuild the notes-api search index"}
        jd.save_goals(SID, {"rompUuid": SID, "seq": len(self.TARGETS), "placementsV": jd.PLACEMENTS_V,
                            "nodes": {g: _top(g, titles[g], self.HISTORY if g == G1 else ())
                                      for g in self.TARGETS},
                            "placements": {}, "status": {g: "working" for g in self.TARGETS},
                            "lastNode": self.TARGETS[-1]})
        self.calls, self.replies, self.refuse, self.pause = [], [], False, False
        self._saved = (jd._judge_run_impl, jd.opener_llm, jd._group_store)

        def fake_impl(model, sys_prompt, user, **k):
            # the real plan_llm and the real _judge_run wrapper still run; only the model call is faked
            self.calls.append(k.get("judge"))
            jd._judge_ctx.paused = False
            if self.pause:                         # the retry pause / rate gate: skipped, no call went out
                jd._judge_ctx.paused = True
                return ""
            if self.refuse:
                jd._judge_ctx.last_call_fail = {"note": REFUSAL, "model": model, "refusal": True}
                return ""
            return self.replies.pop(0) if self.replies else ""
        jd._judge_run_impl = fake_impl
        jd.opener_llm = lambda *a, **k: ""
        jd._group_store = lambda *a, **k: None   # never fire the grouper model after a placement
        self.path = os.path.join(self.td, SID + ".jsonl")
        nudge = ("Where does this stand: what's done, what's left, and is anything blocked?"
                 "<!-- romp-injected --><!-- romp-auto -->"
                 + "".join("<!-- romp-goal-id: %s -->" % g for g in self.TARGETS))
        recs = [{"type": "user", "uuid": "n1", "parentUuid": None, "timestamp": _iso(T),
                 "message": {"role": "user", "content": nudge}},
                {"type": "assistant", "uuid": "a1", "parentUuid": "n1", "timestamp": _iso(T + 2),
                 "message": {"role": "assistant", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "Still running; I armed a watcher for the "
                                                                  "eval job and need nothing from you."}]}}]
        with open(self.path, "w") as f:
            f.write("\n".join(json.dumps(r) for r in recs) + "\n")

    def tearDown(self):
        (jd._judge_run_impl, jd.opener_llm, jd._group_store) = self._saved
        jd._judge_ctx.last_call_fail = None
        jd._judge_ctx.paused = False
        jd._NUDGE_HELD.clear()
        try:                                       # this sid's override journal (the goal-store fixtures rule)
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()
        except OSError:
            pass
        jd._rebind_state(self._old_state)
        shutil.rmtree(self.td, ignore_errors=True)

    def _seg(self):
        sess = jd.parsed_session(SID, [self.path], time.time())
        units = [u for u in jd.plan_units(sess) if u[1] == "nudge"]
        self.assertEqual(len(units), 1, "premise: the reply is one nudge unit")
        return units[0][0], sess

    def _pass(self):
        jd.parse_cache_clear()
        jd._CHAIN_MEMO.clear()
        jd._judge_ctx.stage_incomplete = False
        jd._plan_session(SID, self.path, time.time())

    def _placements(self):
        return jd.load_goals(SID)["placements"]

    def _errors(self, kind):
        try:
            rows = [json.loads(l) for l in Path(jd.ERRORS).read_text().splitlines()]
        except OSError:
            return []
        return [r for r in rows if r.get("err") == kind]

    def _ready(self, sess, gid):
        ready, resp = km._nudge_response_ready(sess["turns"], jd.load_goals(SID), {"count": 1, "at": T - 5},
                                               gid, time.time())
        self.assertIsNotNone(resp, "premise: the gate sees the reply segment")
        return ready


class FailedCall(_Base):
    def test_a_failed_call_leaves_the_reply_due(self):
        seg, _ = self._seg()
        self._pass()                                   # the API answers 529: plan_llm gives ''
        self.assertEqual(self.calls, ["planner"], "one call went out")
        self.assertTrue(jd._judge_ctx.stage_incomplete, "the pass is marked incomplete, so it runs again")
        self.assertNotIn(seg, self._placements(),
                         "no ruling is not an empty ruling: a failed call must not retire the reply")
        self.assertEqual(jd.load_goals(SID).get("parseFails", {}), {},
                         "a transient failure takes no strike (a rate-limit window must not burn the retries)")

    def test_the_next_pass_plans_the_reply_and_files_its_verdict(self):
        seg, _ = self._seg()
        self._pass()
        self.replies = [AWAITING]                      # the API is back
        self._pass()
        self.assertEqual(self.calls, ["planner", "planner"], "the reply is planned again on the next pass")
        self.assertIn(seg, self._placements(), "and ruled now")
        log = jd.load_goals(SID)["nodes"][G1].get("log") or []
        self.assertTrue(any(e.get("kind") == "awaiting" for e in log), "the reply's own verdict lands")

    def test_the_kernel_waits_for_a_ruling_instead_of_blocking(self):
        _, sess = self._seg()
        self._pass()
        self.assertFalse(self._ready(sess, G1),
                         "the planner never ruled on the reply: the failed-nudge gate must wait, not escalate")


class StrikesAtTheCap(_Base):
    def test_a_reply_that_does_not_parse_retires_only_at_the_cap(self):
        seg, sess = self._seg()
        cap = jd.PLAN_PARSE_RETRIES
        for i in range(1, cap):
            self.replies = ["I think the goal is still going."]   # prose, no JSON
            self._pass()
            self.assertNotIn(seg, self._placements(), "strike %d of %d leaves the reply due" % (i, cap))
            self.assertEqual(jd.load_goals(SID).get("parseFails", {}).get(seg), i)
            self.assertFalse(self._ready(sess, G1), "and the gate waits")
        self.replies = ["I think the goal is still going."]
        self._pass()
        self.assertIn(seg, self._placements(), "the capping strike retires the reply unresolved")
        self.assertEqual(jd.load_goals(SID).get("parseFails", {}), {}, "and clears its counter")
        self.assertEqual(len(self._errors("parse")), cap, "each reject is logged as one")
        give = self._errors("give-up")
        self.assertEqual(len(give), 1, "one loud give-up row")
        self.assertIn("parse rejects", give[0]["note"])
        self._pass()
        self.assertEqual(len(self.calls), cap, "no call after the give-up")

    def test_a_content_refusal_strikes_instead_of_retrying_forever(self):
        seg, _ = self._seg()
        self.refuse = True                             # the filter rules on this reply's text, every time
        cap = jd.PLAN_PARSE_RETRIES
        for _ in range(cap + 2):
            self._pass()
        self.assertEqual(len(self.calls), cap, "one doomed call per pass until the cap, then none")
        self.assertIn(seg, self._placements())
        give = self._errors("give-up")
        self.assertEqual(len(give), 1)
        self.assertIn("content refusals", give[0]["note"])
        self.assertIn("can't help with this", give[0]["note"], "the envelope that parked it is readable")

    def test_a_served_reply_forgets_earlier_strikes(self):
        seg, _ = self._seg()
        self.replies = ["not json"]
        self._pass()
        self.assertEqual(jd.load_goals(SID).get("parseFails", {}).get(seg), 1)
        self.replies = [AWAITING]
        self._pass()
        self.assertIn(seg, self._placements())
        self.assertEqual(jd.load_goals(SID).get("parseFails", {}), {})


class Bundle(_Base):
    TARGETS = (G1, G2)

    def test_a_failed_call_stops_the_bundle_before_its_bare_key(self):
        # processing order puts G2 (seg#n2) first and G1 (the bare seg key) last; the kernel reads the
        # bare key for BOTH goals, so G2's failed call must not let G1's ruling place it
        seg, sess = self._seg()
        self.replies = ["", AWAITING]
        self._pass()
        self.assertEqual(self.calls, ["planner"], "the bundle stops at the failed target")
        pl = self._placements()
        self.assertNotIn(seg + "#n2", pl)
        self.assertNotIn(seg, pl, "the bare key stays open, so the whole unit is collected again")
        self.assertFalse(self._ready(sess, G2), "the gate waits for G2's ruling")

    def test_a_ruled_target_is_kept_and_the_failed_one_retried(self):
        seg, sess = self._seg()
        self.replies = [AWAITING, ""]                  # G2 is ruled, then G1's call fails
        self._pass()
        pl = self._placements()
        self.assertIn(seg + "#n2", pl, "the ruled target keeps its ruling")
        self.assertNotIn(seg, pl, "the failed target's key stays open")
        self.assertFalse(self._ready(sess, G1))
        self.replies = [AWAITING]
        self._pass()
        self.assertEqual(len(self.calls), 3, "the next pass asks only about the target still owed")
        self.assertIn(seg, self._placements())


# Earlier history on the goal: a question asked and answered, five times over, all before the nudge. Enough
# rows that an eviction would show, every one of them older than the reply.
_ASKED = [r for i in range(5) for r in (
    {"ev_t": 1100 + 20 * i, "src": "planner", "kind": "block", "why": "needs the notes-api staging token (%d)" % i,
     "at": 1100 + 20 * i},
    {"ev_t": 1110 + 20 * i, "src": "user", "kind": "unblock", "why": "answered in the session (%d)" % i,
     "at": 1110 + 20 * i})]
OUTAGE = 70                                        # passes with every call failing: past LOG_CAP (64) reopen rows


def _rows(log):
    return [(e.get("src"), e.get("kind"), e.get("why")) for e in log]


class NoRulingNoReopen(_Base):
    HISTORY = tuple(_ASKED)

    def _log(self):
        return list(jd.load_goals(SID)["nodes"][G1].get("log") or [])

    def _reopens(self):
        return [e for e in self._log() if e.get("kind") == "reopen"]

    def test_an_outage_adds_no_reopen_rows_and_keeps_the_history(self):
        seg, _ = self._seg()
        self.assertEqual(_rows(self._log()), _rows(_ASKED), "premise: the history loads as written")
        for _ in range(OUTAGE):
            self._pass()
        self.assertEqual(len(self.calls), OUTAGE, "every pass asked again: the reply stayed due")
        self.assertNotIn(seg, self._placements())
        self.assertEqual(self._reopens(), [],
                         "no ruling, no reopen: a failed call must leave no reopen row (70 passes left 64)")
        self.assertEqual(_rows(self._log()), _rows(_ASKED), "and every earlier row survives")
        self.assertFalse(jd.load_goals(SID)["nodes"][G1].get("logTrunc"), "nothing was evicted")

    def test_a_failing_pass_writes_nothing(self):
        self._seg()
        self._pass()
        store = jd.GOALDIR / (SID + ".json")
        body = store.read_bytes()
        for _ in range(5):
            self._pass()
        self.assertEqual(store.read_bytes(), body, "a pass whose only news is a failed call publishes nothing")

    def test_once_the_planner_serves_the_reply_is_placed_once_with_one_reopen(self):
        seg, _ = self._seg()
        for _ in range(OUTAGE):
            self._pass()
        self.replies = [AWAITING]                  # the outage ends
        self._pass()
        self.assertIn(seg, self._placements(), "the reply is ruled")
        log = self._log()
        self.assertEqual(_rows(log[:len(_ASKED)]), _rows(_ASKED), "with the history intact")
        self.assertEqual([(e.get("src"), e.get("why")) for e in self._reopens()], [("planner", "reopened (nudge)")],
                         "and exactly the one reopen that rides the ruling")
        self.assertEqual([e.get("kind") for e in log[len(_ASKED):]], ["reopen", "awaiting"],
                         "nothing else landed on the goal")
        n = len(self.calls)
        for _ in range(3):
            self._pass()
        self.assertEqual(len(self.calls), n, "placed once: the reply is never planned again")
        self.assertEqual(_rows(self._log()), _rows(log), "and the diary does not grow")

    def test_struck_replies_and_the_give_up_add_no_reopen_rows(self):
        seg, _ = self._seg()
        for _ in range(jd.PLAN_PARSE_RETRIES):
            self.replies = ["I think the goal is still going."]   # served, but no JSON: a strike each
            self._pass()
        self.assertIn(seg, self._placements(), "premise: the capping strike retired the reply")
        self.assertEqual(self._reopens(), [], "a strike's save and the give-up carry no ruling, so no reopen")
        self.assertEqual(_rows(self._log()), _rows(_ASKED))


class FullDiary(_Base):
    # a diary already at LOG_CAP: the reopen's append evicts the oldest row, so taking the reopen back by
    # popping the row it appended would still lose that one; the undo restores the goal as it stood
    HISTORY = tuple({"ev_t": 1000 + i, "src": "planner" if i % 2 == 0 else "user",
                     "kind": "block" if i % 2 == 0 else "unblock", "why": "row %d" % i, "at": 1000 + i}
                    for i in range(jd.LOG_CAP))

    def test_a_failed_call_on_a_full_diary_loses_no_row(self):
        self._seg()
        self._pass()
        nd = jd.load_goals(SID)["nodes"][G1]
        self.assertEqual(_rows(nd.get("log") or []), _rows(self.HISTORY), "the oldest row is still there")
        self.assertFalse(nd.get("logTrunc"), "and nothing reads as evicted")


class _Blocked(_Base):
    # a block that landed while the reply waited to be planned (its evidence just before the nudge, filed
    # just after it fired): the nudge's reopen unblocks, so a reopen that outlived its exit moved the card
    # from Needs-you to Working on no new information
    HISTORY = (dict(_ASKED[0], ev_t=T - 1, at=T + 1),)

    def setUp(self):
        super().setUp()
        store = jd.load_goals(SID)
        jd._materialize_node(store["nodes"][G1])  # the flag cache from the diary, as a rollup derives it
        jd.save_goals(SID, store)

    def _assert_unmoved(self, why):
        nd = jd.load_goals(SID)["nodes"][G1]
        self.assertTrue(nd.get("blocked"), why)
        self.assertEqual([e.get("kind") for e in nd.get("log") or []], ["block"],
                         "the diary as it was: no reopen row, nothing else")


class BlockedGoalStaysBlocked(_Blocked):
    def test_a_failed_call_does_not_move_the_card(self):
        seg, _ = self._seg()
        self.assertTrue(jd.load_goals(SID)["nodes"][G1].get("blocked"), "premise: the goal is blocked")
        for _ in range(3):
            self._pass()
        nd = jd.load_goals(SID)["nodes"][G1]
        self.assertNotIn(seg, self._placements())
        self.assertTrue(nd.get("blocked"), "no ruling: the card stays where it was")
        self.assertEqual([e.get("kind") for e in nd.get("log") or []], ["block"])


SKIP = '{"ops":[{"do":"skip","why":"the reply reports no new work"}]}'


class NoOpRulingKeepsNoReopen(_Blocked):
    # The planner served the reply and it parsed, but the plan carries nothing to apply: a skip-only reply (the
    # planner's "no real work here", which the nudge branch drops), or one whose done verdicts were all
    # stripped because the segment holds no work to evidence them. The reply retires (placements[seg] = None,
    # so the kernel reads it as ruled and nothing loops), but no verdict lands on the goal, so the reopen that
    # was read for the menu has nothing to stand beside: kept, it alone moved a blocked card to Working.
    def test_a_skip_only_reply_leaves_the_goal_blocked_and_retires_once(self):
        seg, sess = self._seg()
        self.replies = [SKIP]
        self._pass()
        self.assertEqual(self.calls, ["planner"], "premise: the planner was asked and served")
        pl = self._placements()
        self.assertIn(seg, pl, "the ruling retires the reply")
        self.assertIsNone(pl[seg], "with nothing placed")
        self._assert_unmoved("a ruling with no op is no new information: the card stays in Needs-you")
        self.assertTrue(self._ready(sess, G1), "the kernel's gate reads the reply as ruled, so it is not re-asked")
        for _ in range(3):
            self._pass()
        self.assertEqual(self.calls, ["planner"], "retired once: the reply is never planned again")
        self._assert_unmoved("and later passes leave the card alone")


class StandDownTakesTheReopenBack(_Blocked):
    # a rewind lands DURING the planner call (the unit loop's own check, before the call, saw the prompt live),
    # so apply_plan_guarded stands down at the write: "away" retires the reply, "pending" defers it. Nothing is
    # applied, so the reopen goes too.
    def setUp(self):
        super().setUp()
        self.away, self.cut = False, None
        self._rewound = jd._rewound_away
        jd._rewound_away = lambda *a, **k: self.away
        served = jd._judge_run_impl

        def call_then_cut(*a, **k):
            out = served(*a, **k)
            if self.cut is not None:
                self.away = self.cut           # the rewind armed while the model was answering
            return out
        jd._judge_run_impl = call_then_cut

    def tearDown(self):
        jd._rewound_away = self._rewound
        super().tearDown()

    def _stood_down(self, seg, kind="rewind-stand-down"):
        return [r for r in self._errors(kind) if r.get("seg") == seg]   # plan-sync logs its own, segment-less

    def test_a_ruling_that_stands_down_takes_its_reopen_back(self):
        seg, _ = self._seg()
        self.replies, self.cut = [AWAITING], True
        self._pass()
        self.assertEqual(self.calls, ["planner"])
        self.assertEqual(len(self._stood_down(seg)), 1, "premise: the reply's write stood down")
        self.assertIsNone(self._placements().get(seg, "absent"), "and retired the reply")
        self._assert_unmoved("a ruling that never landed must not move the card")

    def test_a_ruling_deferred_by_a_pending_cut_takes_its_reopen_back(self):
        seg, _ = self._seg()
        self.replies, self.cut = [AWAITING], "pending"
        self._pass()
        self.assertEqual(len(self._stood_down(seg, "rewind-stand-down-pending")), 1, "premise: the write deferred")
        self.assertNotIn(seg, self._placements(), "the reply stays due")
        self._assert_unmoved("a deferred ruling must not move the card")

    def test_a_give_up_that_then_stands_down_undoes_once_and_cleanly(self):
        # the give-up undoes the reopen, then its empty plan stands down at the write and the branch undoes again
        seg, _ = self._seg()
        cap = jd.PLAN_PARSE_RETRIES
        for i in range(cap):
            self.replies = ["I think the goal is still going."]
            if i == cap - 1:
                self.cut = True                # the capping strike's call is the one the rewind lands during
            self._pass()
        self.assertEqual(len(self._errors("give-up")), 1, "premise: the strikes ran out")
        self.assertEqual(len(self._stood_down(seg)), 1, "premise: and the give-up's write stood down")
        self.assertIsNone(self._placements().get(seg, "absent"))
        self._assert_unmoved("two undos of one reopen leave the card exactly as it was")


class NoMenuTakesTheReopenBack(_Base):
    # The nudge names a blocked step whose card already completed. Its reopen writes (a reopen row, the block
    # lifted), but a step under a sealed card is not open, so the scoped menu is empty and the branch stops
    # without asking the planner. Kept, that reopen was published by every pass's save: a row per pass.
    TARGETS = (G2,)

    def setUp(self):
        super().setUp()
        store = jd.load_goals(SID)
        nodes = store["nodes"]
        nodes[G1] = _top(G1, "Ship the notes-api release",
                         log=[{"ev_t": 1500, "src": "planner", "kind": "done", "why": "released", "at": 1500}])
        nodes[G2] = dict(_top(G2, nodes[G2]["text"], log=[dict(_ASKED[0], ev_t=T - 1, at=T + 1)]), parentId=G1)
        for g in (G1, G2):
            jd._materialize_node(nodes[g])
        store["status"] = {G1: "completed"}
        jd.save_goals(SID, store)

    def test_a_goal_with_no_menu_keeps_no_reopen(self):
        self._seg()
        self.assertEqual(jd._scoped_menu(jd.load_goals(SID), G2), [], "premise: the sealed step has no menu")
        for _ in range(3):
            self._pass()
        self.assertEqual(self.calls, [], "premise: the planner is never asked")
        nodes = jd.load_goals(SID)["nodes"]
        self.assertTrue(nodes[G2].get("blocked"), "the step stays blocked")
        self.assertEqual([e.get("kind") for e in nodes[G2].get("log") or []], ["block"],
                         "no reopen row, and none added per pass")
        self.assertTrue(nodes[G1].get("nodeComplete"), "the completed card stays completed")
        self.assertEqual([e.get("kind") for e in nodes[G1].get("log") or [] if e.get("kind") in ("reopen", "unblock")],
                         [], "and nothing reopened it")


class HeldReplyIsSaidOnce(_Base):
    def _held(self):
        return self._errors("nudge-held")

    def test_an_unflagged_failure_streak_is_said_once_naming_the_reply(self):
        seg, _ = self._seg()
        for _ in range(OUTAGE):
            self._pass()                           # a failure with no refusal flag (Codex's never carries one)
        held = self._held()
        self.assertEqual(len(held), 1, "one row for the held reply, not one per pass")
        self.assertEqual((held[0]["seg"], held[0]["goal"]), (seg, G1), "naming the reply and its goal")
        self.assertIn("Run the notes-api eval", held[0]["note"])
        self.assertIn("%d passes" % jd.PLAN_PARSE_RETRIES, held[0]["note"])

    def test_a_blip_says_nothing(self):
        seg, _ = self._seg()
        for _ in range(jd.PLAN_PARSE_RETRIES - 1):
            self._pass()
        self.replies = [AWAITING]
        self._pass()
        self.assertIn(seg, self._placements())
        self.assertEqual(self._held(), [], "a failure the next passes recover from is not a held reply")

    def test_a_pause_is_not_a_failure(self):
        self._seg()
        self.pause = True                          # the retry pause: the call never went out, already announced
        for _ in range(jd.PLAN_PARSE_RETRIES + 2):
            self._pass()
        self.assertEqual(self._held(), [])

    def test_the_kind_is_documented(self):
        doc = (Path(BIN).parent / "docs" / "judges.md").read_text()
        self.assertIn("nudge-held", doc, "docs/judges.md lists every judge-errors kind")
        self.assertIn('"nudge-held"', jd._log_judge_error.__doc__, "and so does the row contract")

    def test_a_served_reply_restarts_the_count(self):
        self._seg()
        for _ in range(jd.PLAN_PARSE_RETRIES - 1):
            self._pass()
        self.replies = ["not json"]                # the call is served (a strike), so the streak ends
        self._pass()
        for _ in range(jd.PLAN_PARSE_RETRIES - 1):
            self._pass()
        self.assertEqual(self._held(), [], "two short streaks are not one long one")


if __name__ == "__main__":
    unittest.main()
