#!/usr/bin/env python3
"""Tracker endings, two gaps from the working-column audit (the user 2026-08-26: audit that all
Working cards should be working — the non-real evidence isolated to sender-side delegation trackers
whose ending event could no longer fire):
(1) ARCHIVED-DONE RECIPIENT — run_propagate's back-link scanned recipient stores live-only, so a
    recipient goal that completed and was then archived (the user cleared the done card) vanished
    from the scan and the sender's tracker never checked off. The recipient-side scan now reads
    live+archive merged (read-only on that side; propagate writes SENDER stores only).
(2) CLEARED-UNDONE RECIPIENT — the user dismissed the recipient's card without completion, killing
    the back-link's event forever; the reply sweep now covers a linked local tracker whose
    ref-joined recipient goal is cleared-without-done — the recipient's reply at/after the send is
    the report-back event (the exact quiet/cross-host rule), why-stamped as the dismissal shape it
    is. A LIVE linked recipient still defers to the back-link: a reply alone never ends a
    delegation whose card is still being worked.
And an ending that fired when it should not have (2026-10-23):
(3) A FOLLOW-UP AFTER THE COMPLETION — the back-link stamped its done with the pass clock and filed
    it again on every pass that found the tracker open, so when the user replied on a completed
    delegation card, the next pass re-completed the card: the chip cleared, and the planner's reopen
    for the reply folded under that done, so the reply was filed elsewhere. The back-link now files
    once per completion of the recipient's goal, at that completion's time raised to any reply floor:
    a follow-up after it stands, and a reply made while the recipient worked does not hold the
    tracker open. Once linked back, the tracker's own closer rules on the follow-up's work, so the
    card completes when that work is done (it stood down on every tracker before).
(4) A RULING AFTER THE REPLY (2026-10-27) — the reply sweep (quiet, cross-host and dismissed-recipient
    trackers) filed a done at the peer's reply time without asking whether it would land. A block or
    a planner reopen filed on the tracker after that reply kept the tracker open, so every pass
    appended the same done again and republished the store, until the log cap evicted the block and
    the card read Completed with nothing new from the peer. The sweep now asks the fold first and
    links each reply back once, the two rules the back-link got in (3).
(5) A FOLLOW-UP STEP LEFT OPEN (2026-10-30) — once the closer could rule a linked-back card done, the
    rollup still lifted the open children of every completed tracker, whoever completed it. When the
    closer ruled the card done but not the follow-up's step, the step popped out beside it as a new
    Working card. Only the delegation's own completion lifts now; a later done on the card rolls its
    steps down like any card's.
SYNTHETIC fixtures only; private synthetic sids."""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_trkend", os.path.join(BIN, "romp-judge"))

T = 1_787_300_000
SENDER = "a21e0001-1111-4222-8333-000000000001"   # private synthetic sids — never the shared placeholder
RECIP = "a21e0001-1111-4222-8333-000000000002"
MID = "1787299000.000001_1.TESTHOST"


def _node(nid, text, parent, t=T, **kw):
    base = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False,
            "blocked": False, "cleared": False, "trail": [], "t": t, "mt": t, "log": []}
    base.update(kw)
    return base


class World(unittest.TestCase):
    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SENDER, "/dev/null", None, "web"), (RECIP, "/dev/null", None, "api")]
        self.td = tempfile.TemporaryDirectory()
        self._msgs = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        jd.MESSAGES.write_text("")

    def tearDown(self):
        jd.discover = self._saved
        jd.MESSAGES = self._msgs
        for sid in (SENDER, RECIP):
            for d in (jd.GOALDIR, jd.GOALARCHDIR):
                try:
                    (d / (sid + ".json")).unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _sender_with_tracker(self):
        st = {"rompUuid": SENDER, "seq": 2, "nodes":
              {SENDER + ":g1": _node(SENDER + ":g1", "Ship the staged verification", None),
               SENDER + ":t1": _node(SENDER + ":t1", "↪ delegated to api: verify refs", SENDER + ":g1",
                                     handoff={"peer": RECIP, "msgId": MID})},
              "placements": {}, "status": {}}
        jd.save_goals(SENDER, st)

    def _recipient(self, done, cleared, archive):
        rn = _node(RECIP + ":g5", "Verify the staged refs", None,
                   origin={"peer": SENDER, "goalId": SENDER + ":t1", "msgId": MID})
        st = {"rompUuid": RECIP, "nodes": {RECIP + ":g5": rn}, "placements": {}, "status": {}}
        if done:
            jd.record_verdict(st, st["nodes"][RECIP + ":g5"], "closer", "done", T + 100,
                              why="refs verified; drift zero")
        if cleared:
            st["nodes"][RECIP + ":g5"]["cleared"] = True
        (jd.save_goal_archive if archive else jd.save_goals)(RECIP, st)
        if archive:
            jd.save_goals(RECIP, {"rompUuid": RECIP, "nodes": {}, "placements": {}, "status": {}})

    def _reply(self, at):
        jd.MESSAGES.write_text(json.dumps(
            {"t": at, "ev": "sent", "id": "r1", "from_id": RECIP, "to_id": SENDER,
             "kind": "coordinate", "body": "verified; drift is zero"}) + "\n")

    def _tracker(self):
        return jd.load_goals(SENDER)["nodes"][SENDER + ":t1"]


class ArchivedDoneRecipient(World):
    def test_the_back_link_reaches_an_archived_completion(self):
        self._sender_with_tracker()
        self._recipient(done=True, cleared=True, archive=True)
        jd.run_propagate(now=T + 900)
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"),
                        "the completion event FIRED — moving files must not erase it")
        self.assertIn("refs verified", nd.get("doneWhy") or "",
                      "the recipient's own resolution still travels")

    def test_a_live_done_recipient_is_byte_identical(self):
        self._sender_with_tracker()
        self._recipient(done=True, cleared=False, archive=False)
        jd.run_propagate(now=T + 900)
        self.assertTrue(self._tracker().get("nodeComplete"))


class DismissedRecipient(World):
    def test_a_cleared_undone_recipient_ends_on_the_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"))
        self.assertIn("dismissed", nd.get("doneWhy") or "",
                      "the why says what happened — a dismissal-shaped ending, not a completion claim")

    def test_no_reply_stays_open(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"),
                         "no report-back event yet — the standing wait machinery owns it")

    def test_a_reply_before_the_send_does_not_count(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=False)
        self._reply(T - 500)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"))

    def test_a_live_linked_recipient_never_ends_on_a_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=False, archive=False)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        self.assertFalse(self._tracker().get("nodeComplete"),
                         "the back-link owns a live linked delegation — a reply alone is not its end")

    def test_an_archived_cleared_undone_recipient_also_ends_on_the_reply(self):
        self._sender_with_tracker()
        self._recipient(done=False, cleared=True, archive=True)
        self._reply(T + 500)
        jd.run_propagate(now=T + 900)
        self.assertTrue(self._tracker().get("nodeComplete"),
                        "the dismissal shape resolves through the archive too")


WEB = "a21e0001-1111-4222-8333-000000000003"    # two more private sids: this class journals the user's
PEER = "a21e0001-1111-4222-8333-000000000004"   # follow-up, and every later load of the sid replays it
CARD = WEB + ":g1"
MID2 = "1787299000.000002_1.TESTHOST"
STEP = "cover the csv path"                     # the step web files for the user's follow-up on the card


class FollowUpAfterTheCompletion(unittest.TestCase):
    """web delegated an export to api; api's closer ruled its goal done at T + 150, unless a test seeds
    api still working."""

    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (WEB, "/dev/null", None, "web"), (PEER, "/dev/null", None, "api")]
        self.td = tempfile.TemporaryDirectory()
        self._msgs = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        jd.MESSAGES.write_text("")

    def tearDown(self):
        jd.discover = self._saved
        jd.MESSAGES = self._msgs
        for sid in (WEB, PEER):
            for p in (jd.GOALDIR / (sid + ".json"), jd.GOALARCHDIR / (sid + ".json"),
                      jd._overrides_dir() / (sid + ".jsonl")):
                try:
                    p.unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _seed(self, under_card=False, done=True):
        st = {"rompUuid": WEB, "seq": 1, "nodes": {}, "placements": {}, "status": {}}
        if under_card:
            st["nodes"][CARD] = _node(CARD, "Ship the notes export", None)
        self.track = jd._plant_handoff_track(st, CARD if under_card else None, "export module",
                                             PEER, "api", T, MID2)
        jd.save_goals(WEB, st)
        g = _node(PEER + ":g1", "Export the module", None, t=T + 50,
                  origin={"peer": WEB, "goalId": self.track, "msgId": MID2})
        rst = {"rompUuid": PEER, "nodes": {g["id"]: g}, "placements": {}, "status": {}}
        if done:
            jd.record_verdict(rst, g, "closer", "done", T + 150, why="module exported")
        jd.save_goals(PEER, rst)

    def _finish_turn(self, start, end):
        """api's closer rules its goal done the way apply_close files it: ev_t is the audited turn's
        start, end_ev the turn's last event."""
        rst = jd.load_goals(PEER)
        jd.record_verdict(rst, rst["nodes"][PEER + ":g1"], "closer", "done", start, why="module exported",
                          end_ev=end)
        jd.save_goals(PEER, rst)

    def _tracker(self):
        return jd.load_goals(WEB)["nodes"][self.track]

    def _checked_off_once(self, ev):
        for now in (T + 300, T + 400):
            self.assertEqual(jd.run_propagate(now=now), 0, "one completion, one link-back")
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"), "api finished after the reply: the tracker checks off")
        self.assertEqual([e.get("ev_t") for e in nd["log"] if e.get("src") == "courier"], [ev])

    def _complete_then_follow_up(self):
        self._seed()
        self.assertEqual(jd.run_propagate(now=T + 200), 1)
        self.assertTrue(self._tracker().get("nodeComplete"), "api's completion checks the card off")
        self.assertTrue(jd.optimistic_followup(WEB, self.track, text="also cover the csv path", now=T + 300))
        nd = self._tracker()
        self.assertFalse(nd.get("nodeComplete"), "the reply reopened the card")
        self.assertTrue(nd.get("followupPending"), "and it wears the follow-up chip")

    def test_the_done_carries_the_completion_time(self):
        self._seed()
        jd.run_propagate(now=T + 200)
        self.assertEqual([e.get("ev_t") for e in self._tracker()["log"] if e.get("kind") == "done"],
                         [T + 150], "api's completion is the evidence, not the pass clock")

    def test_the_next_pass_leaves_the_follow_up_standing(self):
        self._complete_then_follow_up()
        self.assertEqual(jd.run_propagate(now=T + 400), 0, "nothing new from api since the reply")
        st = jd.load_goals(WEB)
        nd = st["nodes"][self.track]
        self.assertFalse(nd.get("nodeComplete"), "a done stamped with the pass clock re-completed the card")
        self.assertTrue(nd.get("followupPending"), "the chip stays until a judge rules on the reply")
        self.assertEqual(st["status"].get(self.track), "working")

    def test_the_reply_can_still_be_filed_under_the_card(self):
        self._complete_then_follow_up()
        jd.run_propagate(now=T + 400)
        st = jd.load_goals(WEB)
        jd._reopen(st, self.track, by="followup", now=T + 300)    # the planner's continuation for the reply
        self.assertIn(self.track, [n["id"] for n in jd.open_menu(st, cap=None)],
                      "the reply's work reaches the card it was typed on")

    def test_a_newer_completion_checks_it_off_again(self):
        self._complete_then_follow_up()
        rst = jd.load_goals(PEER)
        jd._reopen(rst, PEER + ":g1", by="delegation", now=T + 320)    # the follow-up's work reaches api
        jd.record_verdict(rst, rst["nodes"][PEER + ":g1"], "closer", "done", T + 350, why="csv path covered")
        jd.save_goals(PEER, rst)
        self.assertEqual(jd.run_propagate(now=T + 400), 1)
        self.assertTrue(self._tracker().get("nodeComplete"), "evidence newer than the reply completes it")

    def test_a_reopen_after_the_completion_stands_and_no_row_is_added(self):
        self._seed()
        jd.run_propagate(now=T + 200)
        st = jd.load_goals(WEB)
        jd._reopen(st, self.track, by="delegation", now=T + 300)   # new delegated work lands on the card
        jd.save_goals(WEB, st)
        rows = len(self._tracker()["log"])
        for now in (T + 400, T + 500):
            self.assertEqual(jd.run_propagate(now=now), 0)
        nd = self._tracker()
        self.assertFalse(nd.get("nodeComplete"), "the older completion does not close the reopened card")
        self.assertEqual(len(nd["log"]), rows, "standing down writes nothing, pass after pass")

    def test_a_block_filed_after_the_completion_stands_and_no_row_is_added(self):
        self._seed()
        st = jd.load_goals(WEB)      # web's closer asks about the tracker on evidence newer than api's done
        jd.record_verdict(st, st["nodes"][self.track], "closer", "block", T + 180, why="which csv dialect?")
        jd.save_goals(WEB, st)
        rows = len(self._tracker()["log"])
        for now in (T + 200, T + 300):
            self.assertEqual(jd.run_propagate(now=now), 0)
        nd = self._tracker()
        self.assertTrue(nd.get("blocked"), "the newer question stands")
        self.assertEqual(len(nd["log"]), rows, "standing down writes nothing, pass after pass")

    def test_a_follow_up_on_the_parent_card_does_not_hold_its_tracker(self):
        # api's done (evidence T + 150) reaches the board only after the user replied on web's card
        self._seed(under_card=True)
        self.assertTrue(jd.optimistic_followup(WEB, CARD, text="add the docs too", now=T + 300))
        self.assertEqual(jd.run_propagate(now=T + 400), 1)
        nd = self._tracker()
        self.assertTrue(nd.get("nodeComplete"), "api's completion is news the reply had not seen")
        self.assertEqual([e.get("ev_t") for e in nd["log"] if e.get("src") == "courier"], [T + 300],
                         "raised to the reply's floor, where a done lands")

    def test_a_reply_on_the_parent_card_while_api_works_does_not_hold_the_tracker(self):
        self._seed(under_card=True, done=False)
        self.assertEqual(jd.run_propagate(now=T + 80), 0, "api has not finished")
        self.assertTrue(jd.optimistic_followup(WEB, CARD, text="add the docs too", now=T + 100))
        self._finish_turn(T + 60, T + 150)       # api's turn ran from T + 60 to T + 150
        self.assertEqual(jd.run_propagate(now=T + 200), 1)
        self._checked_off_once(T + 150)

    def test_a_reply_on_the_tracker_while_api_works_does_not_hold_it(self):
        self._seed(done=False)
        self.assertEqual(jd.run_propagate(now=T + 80), 0, "api has not finished")
        self.assertTrue(jd.optimistic_followup(WEB, self.track, text="add the docs too", now=T + 100))
        self._finish_turn(T + 60, T + 150)
        self.assertEqual(jd.run_propagate(now=T + 200), 1)
        self._checked_off_once(T + 150)

    def test_the_closer_completes_the_card_once_its_follow_up_work_is_done(self):
        # web does the follow-up itself: the planner files a step under the card, and the closer rules the
        # step done and then the card, which the link-back no longer re-completes on its own
        self._complete_then_follow_up()
        self.assertEqual(jd.run_propagate(now=T + 400), 0)
        st = jd.load_goals(WEB)
        jd._reopen(st, self.track, by="followup", now=T + 300)    # the planner's continuation for the reply
        menu = jd.open_menu(st, cap=None)
        gi = next(i for i, nd in enumerate(menu, 1) if nd["id"] == self.track)
        jd.apply_plan(st, "seg-f1", T + 300,
                      [{"do": "sub", "under": gi, "text": "cover the csv path", "why": "followed up on this goal"}],
                      menu)
        jd.rollup_status(st, False)
        jd.save_goals(WEB, st)
        st = jd.load_goals(WEB)
        step = next(n for n in st["nodes"].values() if n.get("text") == "cover the csv path")
        self.assertEqual(jd.apply_close(st, [step], {"done": {1: "csv path covered"}}, t=T + 500, t_end=T + 520),
                         [step["id"]])
        jd.rollup_status(st, False)
        jd.save_goals(WEB, st)
        st = jd.load_goals(WEB)
        self.assertFalse(st["nodes"][self.track].get("nodeComplete"), "the card waits for the closer's ruling")
        self.assertIn(self.track, [n["id"] for n in jd._subtree_done_candidates(st)],
                      "its steps are finished, so the card is put to the closer")
        self.assertEqual(jd.apply_close(st, [st["nodes"][self.track]], {"done": {1: "the follow-up is finished"}},
                                        t=T + 600, t_end=T + 620), [self.track],
                         "api already reported back, so the closer's done on the card lands")
        jd.rollup_status(st, False)
        jd.save_goals(WEB, st)
        for now in (T + 700, T + 800):
            self.assertEqual(jd.run_propagate(now=now), 0, "nothing new from api")
        st = jd.load_goals(WEB)
        nd = st["nodes"][self.track]
        self.assertTrue(nd.get("nodeComplete") and not nd.get("blocked"), "the finished follow-up is done")
        self.assertEqual([e.get("ev_t") for e in nd["log"] if e.get("src") == "courier"], [T + 150],
                         "api's one completion, linked back once")
        jd.rollup_status(st, True)              # the card is web's focus until it settles: here, web ends
        self.assertEqual(st["status"].get(self.track), "completed")

    def test_the_closer_still_stands_down_before_the_link_back(self):
        # dispatch-time prose ("queued to api") is still not the tracker's ending while api works
        self._seed(done=False)
        st = jd.load_goals(WEB)
        self.assertEqual(jd.apply_close(st, [st["nodes"][self.track]], {"done": {1: "queued to api"}},
                                        t=T + 20, t_end=T + 30), [])
        nd = st["nodes"][self.track]
        self.assertFalse(nd.get("nodeComplete"))
        self.assertEqual([e for e in nd["log"] if e.get("kind") == "done"], [], "a stand-down files nothing")

    def test_both_closer_stand_downs_end_at_the_link_back(self):
        # a linked-back, followed-up tracker takes the closer's done whether its handoff names a peer
        # (the first stand-down's shape) or not (the plain one)
        st = {"rompUuid": WEB, "seq": 1, "nodes": {}, "placements": {}, "status": {}}
        menu = []
        for k, h in ((1, {"peer": PEER, "msgId": MID2}), (2, {"msgId": MID2})):
            nid = "%s:t%d" % (WEB, k)
            nd = st["nodes"][nid] = _node(nid, "↪ delegated to api: step %d" % k, None, handoff=h)
            self.assertTrue(jd.record_verdict(st, nd, "courier", "done", T + 150, why="completed by api (delegated)"))
            self.assertTrue(jd.record_verdict(st, nd, "user", "reopen", T + 300, msg=True))
            menu.append(nd)
        self.assertEqual(jd.apply_close(st, menu, {"done": {1: "follow-up finished", 2: "follow-up finished"}},
                                        t=T + 600, t_end=T + 620), [n["id"] for n in menu])

    def _file_step(self):
        """web takes the follow-up on itself: the planner's continuation reopens the card and files a step under it."""
        st = jd.load_goals(WEB)
        jd._reopen(st, self.track, by="followup", now=T + 300)
        menu = jd.open_menu(st, cap=None)
        gi = next(i for i, nd in enumerate(menu, 1) if nd["id"] == self.track)
        jd.apply_plan(st, "seg-f1", T + 300,
                      [{"do": "sub", "under": gi, "text": STEP, "why": "followed up on this goal"}], menu)
        jd.rollup_status(st, False)
        jd.save_goals(WEB, st)
        st = jd.load_goals(WEB)
        step = next(nid for nid, nd in st["nodes"].items() if nd.get("text") == STEP)
        self.assertEqual(st["nodes"][step].get("parentId"), self.track, "the step is filed under the card")
        return st, step

    @staticmethod
    def _tops(st):
        return sorted(nid for nid, nd in st["nodes"].items() if nd.get("parentId") is None and not nd.get("cleared"))

    def test_the_closers_done_on_the_card_takes_its_open_follow_up_step_along(self):
        # the closer rules the card done and leaves the step unruled: the step rolls down with the card, as a plain
        # card's does, instead of popping out beside it as a Working card nobody asked for
        self._complete_then_follow_up()
        self.assertEqual(jd.run_propagate(now=T + 400), 0)
        st, step = self._file_step()
        self.assertEqual(jd.apply_close(st, [st["nodes"][self.track], st["nodes"][step]],
                                        {"done": {1: "the follow-up is finished"}}, t=T + 600, t_end=T + 620),
                         [self.track])
        jd.rollup_status(st, True)
        jd.save_goals(WEB, st)
        st = jd.load_goals(WEB)
        s = st["nodes"][step]
        self.assertEqual(s.get("parentId"), self.track, "the step stays under the card the closer completed")
        self.assertTrue(s.get("nodeComplete"), "and is done with it")
        self.assertEqual(self._tops(st), [self.track], "no new card on the board")
        self.assertEqual(st["status"].get(self.track), "completed")

    def test_the_planners_done_on_the_card_moots_its_follow_up_steps_question(self):
        # the same ending from the planner, with the step waiting on a question to the user: the card's done moots
        # it like any card's, so the question neither stays blocked under the done card nor comes back on its own
        self._complete_then_follow_up()
        self.assertEqual(jd.run_propagate(now=T + 400), 0)
        st, step = self._file_step()
        self.assertTrue(jd.record_verdict(st, st["nodes"][step], "closer", "block", T + 500, why="which csv dialect?"))
        menu = jd.open_menu(st, cap=None)
        gi = next(i for i, nd in enumerate(menu, 1) if nd["id"] == self.track)
        jd.apply_plan(st, "seg-f2", T + 600, [{"do": "done", "goal": gi, "why": "the follow-up is finished"}], menu)
        s = st["nodes"][step]
        self.assertIn(("planner", "unblock", "discharged with the completed parent"),
                      [(e.get("src"), e.get("kind"), e.get("why")) for e in s["log"]])
        jd.rollup_status(st, True)
        s = st["nodes"][step]
        self.assertEqual(s.get("parentId"), self.track, "the step stays under the card")
        self.assertTrue(s.get("nodeComplete") and not s.get("blocked"), "done with it, its question moot")
        self.assertEqual(self._tops(st), [self.track])
        self.assertEqual(st["status"].get(self.track), "completed")

    def test_a_newer_completion_from_api_still_lifts_the_open_step(self):
        # api finishing again is evidence about the delegation only: the step web filed for itself is not finished
        # by it, so it moves up beside the card and stays open (the 2026-08-24 lift, unchanged)
        self._complete_then_follow_up()
        st, step = self._file_step()
        rst = jd.load_goals(PEER)
        jd._reopen(rst, PEER + ":g1", by="delegation", now=T + 320)
        jd.record_verdict(rst, rst["nodes"][PEER + ":g1"], "closer", "done", T + 350, why="csv path covered")
        jd.save_goals(PEER, rst)
        self.assertEqual(jd.run_propagate(now=T + 400), 1)
        st = jd.load_goals(WEB)
        self.assertTrue(st["nodes"][self.track].get("nodeComplete"), "api's newer completion checks the card off")
        self.assertIsNone(st["nodes"][step].get("parentId"), "the open step moves up beside it")
        self.assertFalse(st["nodes"][step].get("nodeComplete"))


SID_W ="a21e0001-1111-4222-8333-000000000005"   # two more private sids: one test below journals the
SID_P = "a21e0001-1111-4222-8333-000000000006"   # kernel's block, and every later load of the sid replays it
MID3 = "1787299000.000003_1.TESTHOST"
R = T + 50                                        # api's interim reply


class RulingAfterTheReply(unittest.TestCase):
    """web delegated the notes endpoint to api at T, and api replied at R ("on it"). With no recipient goal
    to link back from (a quiet handoff, or a recipient card the user cleared), that reply is the
    tracker's report-back."""

    def setUp(self):
        self._saved = jd.discover
        jd.discover = lambda now, window=None, forks=True: [
            (SID_W, "/dev/null", None, "web"), (SID_P, "/dev/null", None, "api")]
        self.td = tempfile.TemporaryDirectory()
        self._msgs = jd.MESSAGES
        jd.MESSAGES = Path(self.td.name) / "messages.jsonl"
        jd.MESSAGES.write_text("")
        self._reply("r1", R, "on it, will report back")
        self.track = SID_W + ":t1"

    def tearDown(self):
        jd.discover = self._saved
        jd.MESSAGES = self._msgs
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]
        for sid in (SID_W, SID_P):
            for p in (jd.GOALDIR / (sid + ".json"), jd.GOALARCHDIR / (sid + ".json"),
                      jd._overrides_dir() / (sid + ".jsonl")):
                try:
                    p.unlink()
                except OSError:
                    pass
        self.td.cleanup()

    def _reply(self, mid, at, body):
        with jd.MESSAGES.open("a") as f:
            f.write(json.dumps({"t": at, "ev": "sent", "id": mid, "from_id": SID_P, "to_id": SID_W,
                                "kind": "coordinate", "body": body}) + "\n")
        jd._PEER_ASK_CACHE[:] = [None, ({}, {}, {})]   # the maps cache on (mtime, size); start from the file

    def _seed(self, quiet=True):
        h = {"peer": SID_P, "msgId": MID3}
        if quiet:
            h["quiet"] = True
        st = {"rompUuid": SID_W, "seq": 1, "placements": {}, "status": {},
              "nodes": {self.track: _node(self.track, "↪ delegated to api: add the notes endpoint", None,
                                          handoff=h)}}
        jd.rollup_status(st, False)
        jd.save_goals(SID_W, st)

    def _file(self, src, kind, t, why, journal=False):
        st = jd.load_goals(SID_W)
        self.assertTrue(jd.record_verdict(st, st["nodes"][self.track], src, kind, t, why=why))
        if journal:
            jd.append_block(SID_W, self.track, src, why, t)
        jd.rollup_status(st, False)
        jd.save_goals(SID_W, st)

    def _passes(self, k, start=T + 900):
        return [jd.run_propagate(now=start + i) for i in range(k)]

    def _tracker(self):
        return jd.load_goals(SID_W)["nodes"][self.track]

    def _courier_dones(self):
        return [e.get("ev_t") for e in self._tracker()["log"]
                if e.get("src") == "courier" and e.get("kind") == "done"]

    def test_a_block_after_the_reply_stands_and_no_row_is_added(self):
        self._seed()
        self._file("planner", "block", T + 100, "which schema should notes use?")
        rows = len(self._tracker()["log"])
        self.assertEqual(self._passes(3), [0, 0, 0], "a done that cannot land is not filed")
        nd = self._tracker()
        self.assertTrue(nd.get("blocked"), "the question asked after api's reply stands")
        self.assertEqual(len(nd["log"]), rows, "standing down writes nothing, pass after pass")

    def test_the_block_outlives_the_log_cap(self):
        self._seed()
        self._file("planner", "block", T + 100, "which schema should notes use?")
        self.assertEqual(sum(self._passes(jd.LOG_CAP + 6)), 0)
        st = jd.load_goals(SID_W)
        nd = st["nodes"][self.track]
        self.assertFalse(nd.get("nodeComplete"), "nothing new from api: the card does not complete by itself")
        self.assertTrue(nd.get("blocked"))
        self.assertFalse(nd.get("logTrunc"), "the card's history is intact")
        self.assertNotEqual(st["status"].get(self.track), "completed")

    def test_a_dismissed_recipient_leaves_the_dead_handoff_block_standing(self):
        # api replied while its card was live, so the back-link owned the ending; web then went dormant
        # and the kernel blocked the tracker (journaled, as its dead-handoff sweep does), and then the
        # user cleared api's unfinished card, which hands the ending to the reply
        self._seed(quiet=False)
        why = jd.dead_wait_block_why("the delegation to api (add the notes endpoint)")
        self._file("nudge", "block", T + 100, why, journal=True)
        g = _node(SID_P + ":g1", "Add the notes endpoint", None, t=T + 10, cleared=True,
                  origin={"peer": SID_W, "goalId": self.track, "msgId": MID3})
        jd.save_goals(SID_P, {"rompUuid": SID_P, "nodes": {g["id"]: g}, "placements": {}, "status": {}})
        self.assertEqual(self._passes(3), [0, 0, 0])
        self.assertTrue(self._tracker().get("blocked"), "the card still needs the user's call")
        self.assertEqual(self._courier_dones(), [])

    def test_a_newer_reply_still_reports_back(self):
        self._seed()
        self._file("planner", "block", T + 100, "which schema should notes use?")
        self.assertEqual(self._passes(1), [0])
        self._reply("r2", T + 150, "the notes endpoint is merged")
        self.assertEqual(self._passes(2, start=T + 1000), [1, 0])
        self.assertTrue(self._tracker().get("nodeComplete"), "api's report after the question ends the wait")
        self.assertEqual(self._courier_dones(), [T + 150])

    def _completed_then_reopened(self, at):
        self._seed()
        self.assertEqual(self._passes(1), [1])
        self.assertTrue(self._tracker().get("nodeComplete"), "api's reply reports back")
        self._file("planner", "reopen", at, "more work on the endpoint")
        self.assertFalse(self._tracker().get("nodeComplete"))
        self.assertEqual(self._passes(3, start=T + 1000), [0, 0, 0], "the reply was linked back already")
        self.assertFalse(self._tracker().get("nodeComplete"), "the reopen stands")
        self.assertEqual(self._courier_dones(), [R])

    def test_a_reopen_after_the_reply_completed_it_stands(self):
        self._completed_then_reopened(T + 200)

    def test_a_reopen_at_the_reply_time_stands(self):
        # the planner ruled on the reply itself: a reopen sharing the done's evidence time, filed after
        # it, so a second done at that time would sort after the reopen and complete the card again
        self._completed_then_reopened(R)


if __name__ == "__main__":
    unittest.main()
