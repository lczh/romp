#!/usr/bin/env python3
"""A lift filed after the closer ruled sends the turn back to the closer before any status check (2026-10-26).

The shape, all SYNTHETIC (the notes-api demo): the `api` session's card "Ship the pagination fix" is
blocked on "may the migration land on main?". The user says yes; the session lands it in its next turn
and goes quiet. In the judge pass that sees that turn end, the closer runs first and closes the turn with
the card still blocked. The unblocker runs after it in the same pass, under the same evidence, and lifts
the block: the turn shows the migration landed. The card drops to working while the turn already reads
judged at its size, so the nudge walk's closer gate opens and a status check goes out seconds later,
about finished work. The done arrives only from the check's own reply: a lifted goal waits for the
closer's next call (jd._lift_riders), and only a new turn makes one.

The fix keys on the lift itself. A goal's FIRST judge unblock drops the session's last turn from the
closer's book in the same save (jd._unblock_session through jd._reopen_closure), as a post-closure done
already does (jd._invalidate_closure). The closer judges the turn again next pass with the lifted goal on
its menu, and the walk's existing closer gate holds until it has: a done means no check, a goal still
working is checked once. A repeated judge unblock reopens nothing (the kernel holds that nudge itself,
WHY_UNBLOCK_UNSETTLED), and neither does a lift while the session is mid-turn. The release is the closer's
next ruling on the turn or its give-up at the kill cap; with the closer off the gate reads settled.

The done path has a shape of its own (TheDoneAtATurnBoundary): when the asking turn's idle tail ends in the
second the landing turn opens, the done must reopen the landing turn, the newer of the two that share it.

Driven end to end on real files under a private synthetic sid: the real unblocker, closer and nudge walk,
with only the model calls and the walk's session-level liveness reads stubbed.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_unblockreopen", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                        # the kernel's OWN judge instance: the walk reads what the judges write

SID = "5e8a1c3d-7b2f-4d96-a0e4-9c1b3f7d2a68"      # this module's private synthetic sid
G1 = SID + ":g1"
NOW = 1_788_100_000
T1 = NOW - 900                                    # the turn that asked
T2 = NOW - 120                                    # the turn that landed it: the session's last, ended
ASK = "may the migration land on main?"
LIFT = '{"verdicts": [{"n": 1, "do": "lift", "why": "the session landed the migration on main"}]}'
HOLD = '{"verdicts": [{"n": 1, "do": "hold", "why": ""}]}'
DONE = '{"done": [{"goal": 1, "why": "the migration landed on main"}], "block": [], "awaiting": []}'
NOTHING = '{"done": [], "block": [], "awaiting": []}'


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _user(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def _reply(t, text, uuid, parent):
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class _FakeBackend:
    def __init__(self):
        self.sent = []

    def send(self, sid, body):
        self.sent.append(body)


class _Base(unittest.TestCase):
    MID_TURN = False                              # a third, still-open turn after the landing turn

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        root = Path(self.td.name)
        self.saved_state = jd.STATE
        jd._rebind_state(root)                    # every state path, the parse cache and the shared view cleared
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (root / "session-hosts").write_text("off")
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._autonudge_cache.clear(); km._TICK_SEEN.clear(); km._SESSION_STAMP_CACHE.clear()
        self.saved_km = {n: getattr(km, n) for n in (
            "_session_flag", "_compacting_now", "_api_error", "_session_working", "_interrupt_suppresses_nudge",
            "_backend_queued", "_backend_rewind_pending", "_last_state", "_session_awaiting", "_revivers_pending",
            "_pending_ops", "_last_assistant_report", "_all_outstanding_delegated")}
        self.saved_jd = {n: getattr(jd, n) for n in ("plan_units", "unblock_llm", "closer_llm", "CLOSER_ON")}
        self.saved_backend = km.Sessions.backend_for
        self.saved_paused = getattr(jd._judge_ctx, "paused", False)
        # the session-level reads say idle, settled and unqueued, as they did in the incident: the gates under
        # test are the closer's book and the walk's own reading of it
        km._session_flag = lambda sid, flag: False
        km._compacting_now = lambda sid: False
        km._api_error = lambda path: None
        km._session_working = lambda turns: False
        km._interrupt_suppresses_nudge = lambda turns, sid="", **k: False
        km._backend_queued = lambda sid: False
        km._backend_rewind_pending = lambda sid: False
        km._last_state = lambda sid: ("", 0)
        km._session_awaiting = lambda *a, **k: False
        km._revivers_pending = lambda *a, **k: ""
        km._pending_ops = {}
        km._last_assistant_report = lambda path, cap=4000: ("", 0)   # no report to judge: the redundancy gate stands aside
        km._all_outstanding_delegated = lambda nodes, gid: False
        jd.plan_units = lambda session, store, **kw: []             # every unit placed: the planner gate is open
        jd._judge_ctx.paused = False
        self.fb = _FakeBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: self.fb)
        self.unblock_calls, self.close_menus = [], []
        recs = [_user(T1, "add cursor pagination to GET /notes", "u1"),
                _reply(T1 + 40, "Pagination is ready on its branch. " + ASK.capitalize(), "a1", "u1"),
                _user(T2, "yes, land it", "u2", "a1"),
                _reply(T2 + 30, "Landed the migration on main; the pagination fix is live.", "a2", "u2")]
        if self.MID_TURN:
            recs.append(_user(T2 + 60, "now add a page-size cap", "u3", "a2"))
        self.path = str(root / (SID + ".jsonl"))
        Path(self.path).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        self.turns = jd.parsed_session(SID, [self.path], NOW)["turns"]
        ended = [tn for tn in self.turns if not jd._turn_open(tn, self.turns)]
        self.assertEqual(len(ended), 2, "the fixture is two ended turns")
        self.t1, self.t2 = ended[0]["id"], ended[1]["id"]

    def tearDown(self):
        for n, v in self.saved_km.items():
            setattr(km, n, v)
        for n, v in self.saved_jd.items():
            setattr(jd, n, v)
        km.Sessions.backend_for = self.saved_backend
        jd._judge_ctx.paused = self.saved_paused
        jd._judge_ctx.last_call_fail = None
        journal = jd._overrides_dir() / (SID + ".jsonl")   # this sid's override journal goes with the test
        if journal.exists():
            journal.unlink()
        jd._rebind_state(self.saved_state)
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._autonudge_cache.clear(); km._TICK_SEEN.clear(); km._SESSION_STAMP_CACHE.clear()
        self.td.cleanup()

    def _save(self, log=None, placed=False):
        """The store as the pass's closer left it: both ended turns closed at their sizes, the card still
        blocked on the ask. Plain dicts before the save (a loaded node's verdict flags are diary-owned)."""
        log = log if log is not None else [{"ev_t": T1, "src": "closer", "kind": "block", "why": ASK, "at": T1 + 60}]
        store = {"rompUuid": SID, "seq": 1, "lastNode": G1, "placementsV": jd.PLACEMENTS_V, "placements": {},
                 "status": {G1: "blocked"},
                 "closedTurns": [self.t1, self.t2],
                 "closedSig": {tn["id"]: len(tn["atoms"]) for tn in self.turns if tn["id"] in (self.t1, self.t2)},
                 "nodes": {G1: {"id": G1, "text": "Ship the pagination fix", "parentId": None,
                                "nodeComplete": False, "blocked": True, "blockWhy": ASK, "cleared": False,
                                "trail": [], "t": T1, "mt": T1 + 60, "log": list(log)}}}
        if placed:                                # the turns' segments were placed under the card: it is on their own menu
            for tn in self.turns:
                for sg in jd._segs(tn, store):
                    store["placements"][sg["id"]] = G1
        jd.save_goals(SID, store)

    def _unblock(self, reply=LIFT):
        def fake(blocks_text, since_text, completed_text=""):
            self.unblock_calls.append(since_text)
            return reply
        jd.unblock_llm = fake
        return jd._unblock_session(SID, self.path, NOW)

    def _close(self, reply):
        def fake(turn_text, menu_text, goal_history="", lift_whys=""):
            self.close_menus.append(menu_text)
            return reply
        jd.closer_llm = fake
        return jd._close_session(SID, self.path, NOW)

    def _killed_close(self):
        def dead(*a, **k):                        # the call ran to the timer: the dead-CLI stash, as _judge_run leaves it
            jd._judge_ctx.last_call_fail = {"note": "the model CLI died with no output (exit -14)",
                                            "model": "sonnet", "kill": True}
            self.close_menus.append(None)
            return ""
        jd.closer_llm = dead
        return jd._close_session(SID, self.path, NOW)

    def _tick(self):
        nudged = dict(km._auto_nudge_data().get("nudged", {}))
        return km._auto_nudge_session({"sid": SID, "path": self.path}, NOW, {}, nudged, {})

    def _goal(self):
        st = jd.load_goals(SID)
        return st, st["nodes"][G1], st.get("status", {}).get(G1)

    @staticmethod
    def _done(st):
        """The card's done verdict landed: completed once the session settles, done-and-confirming until then (the
        rollup's own exports, as the walk's fire list reads them)."""
        return bool(st["nodes"][G1].get("nodeComplete")) and (
            st.get("status", {}).get(G1) == "completed" or G1 in (st.get("confirming") or []))


class TheIncident(_Base):
    def setUp(self):
        super().setUp()
        self._save()

    def test_the_lift_sends_the_closed_turn_back_to_the_closer_and_the_walk_waits(self):
        self.assertEqual(self._unblock(), [G1])
        verdict = self._tick()                    # the walk's next look, seconds after the lift
        self.assertEqual(self.fb.sent, [], "no status check while the closer has not ruled on the lifted card")
        self.assertEqual(verdict, "closer-unsettled")
        st, nd, status = self._goal()
        self.assertEqual(status, "working", "the lift dropped the card to working")
        self.assertNotIn(self.t2, st["closedTurns"], "the turn the closer ruled on before the lift is open to it again")
        self.assertNotIn(self.t2, st.get("closedSig") or {})
        self.assertIn(self.t1, st["closedTurns"], "only the session's last turn")

    def test_a_done_from_the_closers_second_look_means_no_check(self):
        self._unblock()
        self._tick()
        self._close(DONE)
        self.assertEqual(len(self.close_menus), 1, "one closer call: the reopened turn, and only it")
        self.assertIn("Goal #1's wait was ruled over", self.close_menus[0], "the lifted card rides that call")
        st, nd, status = self._goal()
        self.assertTrue(self._done(st), "the closer completed the card off the reopened turn")
        self.assertIn(self.t2, st["closedTurns"])
        self._tick()
        self.assertEqual(self.fb.sent, [], "a done filed off the turn: no check-in")

    def test_a_goal_still_working_after_the_closers_look_is_checked_once(self):
        self._unblock()
        self._tick()
        self._close(NOTHING)
        self.assertEqual(len(self.close_menus), 1)
        st, nd, status = self._goal()
        self.assertEqual(status, "working", "the closer left it open: it really is working")
        self._tick()
        self.assertEqual(len(self.fb.sent), 1, "the check goes out once the closer has ruled")
        self._tick()
        self.assertEqual(len(self.fb.sent), 1, "and only once")


class TheTurnsOwnMenu(_Base):
    """The same incident with the turns placed under the card, so the card is on the reopened turn's OWN menu and
    its done journals the turn's time, the same evidence time as the lift. The later filing wins the tie, so the
    closer's done is not shadowed by the lift (the stand-down would drop it, and the check would go out anyway)."""

    def setUp(self):
        super().setUp()
        self._save(placed=True)

    def test_the_closers_done_lands_over_the_lift(self):
        self._unblock()
        self.assertEqual(self._tick(), "closer-unsettled")
        self._close(DONE)
        self.assertEqual(len(self.close_menus), 1)
        self.assertNotIn("Goal #1's wait was ruled over", self.close_menus[0], "the card is the turn's own, not a rider")
        st, nd, status = self._goal()
        self.assertTrue(self._done(st), "the done lands: same evidence time as the lift, filed after it")
        self._tick()
        self.assertEqual(self.fb.sent, [])


class WhatReopensNothing(_Base):
    def test_a_repeated_judge_unblock_leaves_the_kernels_own_hold_to_it(self):
        self._save(log=[{"ev_t": T1 - 300, "src": "planner", "kind": "unblock", "why": "new work filed on this branch",
                         "at": T1 - 290},
                        {"ev_t": T1, "src": "closer", "kind": "block", "why": ASK, "at": T1 + 60}])
        self.assertEqual(self._unblock(), [G1])
        st, nd, status = self._goal()
        self.assertIn(self.t2, st["closedTurns"], "a repeat reopens nothing: the card is already ping-ponging")
        self._tick()
        self.assertEqual(self.fb.sent, [], "the kernel holds a repeated unblock until the closer's next word")
        km._autonudge_cache.clear()               # read the ledger as written
        deferred = (km._auto_nudge_data().get("deferred") or {}).get(G1) or {}
        self.assertEqual(deferred.get("why"), jd.WHY_UNBLOCK_UNSETTLED)

    def test_a_users_own_earlier_unblock_does_not_make_it_a_repeat(self):
        self._save(log=[{"ev_t": T1 - 300, "src": "user", "kind": "unblock", "at": T1 - 300},
                        {"ev_t": T1, "src": "closer", "kind": "block", "why": ASK, "at": T1 + 60}])
        self._unblock()
        st, nd, status = self._goal()
        self.assertNotIn(self.t2, st["closedTurns"], "only a judge's unblock counts as a prior one")
        self.assertEqual(self._tick(), "closer-unsettled")

    def test_a_hold_reopens_nothing(self):
        self._save()
        self.assertEqual(self._unblock(HOLD), [])
        st, nd, status = self._goal()
        self.assertEqual(status, "blocked")
        self.assertEqual(sorted(st["closedTurns"]), sorted([self.t1, self.t2]))


class ALiftMidTurn(_Base):
    MID_TURN = True

    def test_a_lift_while_the_session_works_reopens_nothing(self):
        # The session's last turn is still running: the walk cannot fire, and when that turn ends the closer judges it
        # with the lifted card riding its menu. Reopening the turn before it would buy one more closer call for nothing.
        self._save()
        self.assertEqual(self._unblock(), [G1])
        st, nd, status = self._goal()
        self.assertEqual(status, "working")
        self.assertEqual(sorted(st["closedTurns"]), sorted([self.t1, self.t2]))


class TheRelease(_Base):
    """The hold is the closer gate every ended turn already passes through, so it releases on what releases that gate.
    A failing closer never holds the check forever, and with the closer off there is nothing to wait for."""

    def setUp(self):
        super().setUp()
        self._save()

    def test_killed_closer_calls_give_the_turn_up_and_the_check_then_goes_out(self):
        self._unblock()
        for _ in range(jd.DISTILL_FAIL_CAP - 1):
            self._killed_close()
            self.assertEqual(self._tick(), "closer-unsettled", "a killed call is retried next pass; the check waits")
        self._killed_close()                      # the cap: the turn is adopted at its size, no verdicts
        st, nd, status = self._goal()
        self.assertIn(self.t2, st["closedTurns"])
        self._tick()
        self.assertEqual(len(self.fb.sent), 1, "the check goes out after the give-up, once")

    def test_with_the_closer_off_the_gate_reads_settled(self):
        jd.CLOSER_ON = False
        self._unblock()
        self._tick()
        self.assertEqual(len(self.fb.sent), 1, "no closer will ever rule: the check is not held for one")


class TheDoneAtATurnBoundary(_Base):
    """A done the planner files after the closer ruled (jd._invalidate_closure), on the landing turn, when the session's
    states rows put the asking turn's idle tail ending in the second the landing prompt opened its turn, or one second
    past it. Times are whole seconds, so both turns' spans hold the landing segment's start. The done reopens the
    landing turn, the session's last: the walk waits for the closer, whose next look files the done, and no status
    check goes out. Read oldest-first, the done reopened the asking turn instead and the check went out at once."""

    def _done_after_the_closer(self, working_t):
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        rows = [{"t": T1, "state": "working"}, {"t": T1 + 41, "state": "waiting"},
                {"t": working_t, "state": "working"}, {"t": T2 + 31, "state": "waiting"}]
        (jd.STATESDIR / (SID + ".jsonl")).write_text("".join(json.dumps(r) + "\n" for r in rows))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        session = jd.parsed_session(SID, [self.path], NOW)
        self.turns = session["turns"]
        ended = [tn for tn in self.turns if not jd._turn_open(tn, self.turns)]
        self.assertEqual([tn["id"] for tn in ended], [self.t1, self.t2], "the same two ended turns")
        self.assertGreaterEqual(ended[0]["end"], T2, "the asking turn's idle tail reaches the landing turn's second")
        store = {"rompUuid": SID, "seq": 1, "lastNode": G1, "placementsV": jd.PLACEMENTS_V, "placements": {},
                 "status": {G1: "working"}, "closedTurns": [self.t1, self.t2],
                 "closedSig": {tn["id"]: len(tn["atoms"]) for tn in ended},
                 "nodes": {G1: {"id": G1, "text": "Ship the pagination fix", "parentId": None,
                                "nodeComplete": False, "blocked": False, "cleared": False,
                                "trail": [], "t": T1, "mt": T1 + 60, "log": []}}}
        for tn in ended:                          # both turns' work placed under the card: it is on their own menus
            for sg in jd._segs(tn, store):
                store["placements"][sg["id"]] = G1
        jd.save_goals(SID, store)
        st = jd.load_goals(SID)
        seg_t = jd._segs(ended[1], st)[0]["t"]    # the landing turn's segment time, as the planner hands it with its done
        self.assertEqual(seg_t, T2)
        jd._invalidate_closure(st, session, seg_t)
        jd.save_goals(SID, st)

    def _check(self):
        verdict = self._tick()                    # the walk's next look, seconds after the done
        self.assertEqual(self.fb.sent, [], "no status check while the closer has not looked again")
        self.assertEqual(verdict, "closer-unsettled")
        st, nd, status = self._goal()
        self.assertNotIn(self.t2, st["closedTurns"], "the done reopened the turn it was filed off")
        self.assertIn(self.t1, st["closedTurns"], "and only that turn")
        self._close(DONE)
        self.assertEqual(len(self.close_menus), 1, "one closer call: the reopened turn")
        st, nd, status = self._goal()
        self.assertTrue(self._done(st), "the closer's second look files the done")
        self._tick()
        self.assertEqual(self.fb.sent, [], "a done filed: no check-in")

    def test_the_idle_tail_ends_in_the_second_the_landing_turn_opens(self):
        self._done_after_the_closer(T2)
        self._check()

    def test_the_idle_tail_ends_one_second_past_it(self):
        self._done_after_the_closer(T2 + 1)
        self._check()


class OneDefinition(unittest.TestCase):
    def test_the_kernels_nudge_holds_and_the_unblocker_read_the_same_sources(self):
        # Equal, and bound to the judge's name in the kernel's source: an identity check breaks when another
        # test module in the same run reloads the shared judge module, which rebinds the tuple.
        self.assertEqual(km.NUDGE_HOLD_EXEMPT_SRC, jd.NON_JUDGE_SRC)
        with open(os.path.join(BIN, "romp-kernel"), encoding="utf-8") as f:
            self.assertIn("\nNUDGE_HOLD_EXEMPT_SRC = jd.NON_JUDGE_SRC", f.read())


if __name__ == "__main__":
    unittest.main()
