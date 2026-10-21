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


def _top(gid, text):
    return {"id": gid, "text": text, "parentId": None, "nodeComplete": False, "blocked": False,
            "cleared": False, "trail": [], "t": 1000, "mt": 2000, "log": []}


class _Base(unittest.TestCase):
    TARGETS = (G1,)

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self._old_state = jd.STATE
        jd._rebind_state(Path(self.td))          # GOALDIR, ERRORS and every derived dir move too
        jd._judge_ctx.last_call_fail = None
        jd._judge_ctx.paused = False
        titles = {G1: "Run the notes-api eval", G2: "Rebuild the notes-api search index"}
        jd.save_goals(SID, {"rompUuid": SID, "seq": len(self.TARGETS), "placementsV": jd.PLACEMENTS_V,
                            "nodes": {g: _top(g, titles[g]) for g in self.TARGETS},
                            "placements": {}, "status": {g: "working" for g in self.TARGETS},
                            "lastNode": self.TARGETS[-1]})
        self.calls, self.replies, self.refuse = [], [], False
        self._saved = (jd._judge_run_impl, jd.opener_llm, jd._group_store)

        def fake_impl(model, sys_prompt, user, **k):
            # the real plan_llm and the real _judge_run wrapper still run; only the model call is faked
            self.calls.append(k.get("judge"))
            jd._judge_ctx.paused = False
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


if __name__ == "__main__":
    unittest.main()
