#!/usr/bin/env python3
"""The closer rules a RIDER from what its goal history shows, and a reply's subject stays its own
(2026-10-15). Two incidents, reproduced with synthetic fixtures:

  * A stale re-block. The user answered a card's question in a reply; the work that answered it was
    filed under the card's sub-goals, never on the card itself, so no verdict ever landed on it.
    Hours later a follow-up about a DIFFERENT card put it on the closer's list as a status rider. It
    was shown its own trail only, which ended at the question; it was still flagged "reopened by the
    user's own reply" from the old reply; and its block carried the unrelated turn's time, which
    cleared the reply's floor. The card went back to needs-you with the question already answered.
  * Cross-goal text. On a follow-up whose own goal the planner had already ruled before the closer
    ran, the closer's list held only riders, and the reply's outcome (or its question) was credited
    to whichever rider fit best: a verdict attaches by menu number alone.

One class per lever: the reply-reopened flag marks only the goals this turn's follow-up names
(IncidentStaleReblock), a reply whose subject is off a list that holds only riders names it and bars
crediting the riders the message does not name (ReplySubject), a rider is shown its subtree with any
descendant riding the same call pruned (RiderHistory), and a rider's BLOCK journals the newest segment
time in the history it was shown, so a block from evidence older than the user's latest reply stands
down (HistoryAnchor, LiftAnchor), while a rider's done keeps the turn's time, so a newer pause or
lift in its own diary never hides it (RiderDone).

Synthetic only: a private invented sid (never the shared placeholder; its override journal is removed
in tearDown), the notes-api demo world, invented goal titles and questions, session hosts off."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge_close_stale_riders", os.path.join(BIN, "romp-judge"))

SID = "6d2f8a41-93c7-4e15-b0a8-5c7e1f2d9b64"      # private synthetic sid, never the shared placeholder
NOW = 1781200000
T0 = NOW - 7200                                   # the card's question
T_REPLY = T0 + 600                                # the user's reply on that card (its transcript record)
T_LIFT = T0 + 900                                 # an unblocker lift, for the lift-rider cases
T_OTHER = T0 + 1800                               # a later follow-up about a different card

QUESTION = "Drafted a 10-per-minute limit for the api login route. Ship it now, or load-test it on staging first?"
ANSWER = "Load-tested on staging: no errors at 10 logins a minute. Shipped the limit as commit 4c9e2d1."
FLAG_LINE = "reopened by the user's own reply"
SUBJECT_LINE = "This turn's reply answers the user's message about a goal not listed above"


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _gid(g):
    return SID + ":" + g


def _followup(g, body):
    # the shape a typed card reply reaches the session in: the body, then the hidden goal marker (one per
    # goal when a bundled message names several)
    gs = [g] if isinstance(g, str) else list(g)
    return body + "\n\n" + "".join("<!-- romp-goal-id: %s -->" % _gid(x) for x in gs)


def _row(kind, src, ev, **kw):
    return {"ev_t": ev, "at": ev, "src": src, "kind": kind, **kw}


def _store():
    return {"rompUuid": SID, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {},
            "placements": {}, "status": {}}


def _node(s, g, text, parent=None, t=T0 - 60, trail=(), log=(), **extra):
    nd = {"id": _gid(g), "text": text, "parentId": _gid(parent) if parent else None,
          "nodeComplete": False, "blocked": False, "cleared": False, "trail": list(trail),
          "t": t, "mt": t, "log": list(log)}
    nd.update(extra)
    jd._materialize_node(nd)                      # the flag cache (blocked, followupAt...) from the diary
    s["nodes"][nd["id"]] = nd
    return nd


def _answered_card(s, seg_q):
    """The card the user answered: its question on its own trail, then the user's reply (the optimistic
    reopen, carrying the message) and the planner's file-under reopen. No verdict since."""
    return _node(s, "gT", "Rate-limit the api login route", trail=[seg_q],
                 log=[_row("block", "closer", T0, why="Ship the login rate limit now, or load-test it first?"),
                      _row("reopen", "user", T_REPLY - 1, msg=True),
                      _row("reopen", "planner", T_REPLY)],
                 closerLookT=NOW)                  # looked at since its last filing: not a steps-finished nominee


def _other_card(s, seg_r):
    """A different card the later follow-up is about, which the planner ruled done before the closer
    ran, so that turn's own list is empty."""
    _node(s, "gU", "Fix the web login timeout", t=T0 - 30, log=[_row("done", "planner", T_OTHER)])
    _node(s, "gUc", "Confirm the login fix is merged", parent="gU", t=T_OTHER, trail=[seg_r],
          log=[_row("done", "planner", T_OTHER)])
    s["placements"][seg_r] = _gid("gUc")


OTHER_EXCHANGE = (T_OTHER, _followup("gU", "Is the web login timeout fix merged?"),
                  "Yes: the web login timeout fix is merged and deployed to staging.")


class _Closer(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        Path(self.td, "session-hosts").write_text("off")
        self._state = jd.STATE
        jd._rebind_state(Path(self.td))
        self._llm = jd.closer_llm
        self.calls = []
        self.reply = '{"done": [], "block": []}'

        def stub(turn_text, menu_text, hist="", lift_text=""):
            self.calls.append({"menu": menu_text, "hist": hist, "lift": lift_text})
            return self.reply
        jd.closer_llm = stub

    def tearDown(self):
        jd.closer_llm = self._llm
        try:
            (jd._overrides_dir() / (SID + ".jsonl")).unlink()   # this sid's journal never outlives the test
        except OSError:
            pass
        jd._rebind_state(self._state)
        shutil.rmtree(self.td, ignore_errors=True)

    def _parse(self, exchanges):
        """[(t, user text, assistant text)] → (turns, [segment per turn], store, seg_by_id)."""
        recs, prev = [], None
        for k, (t, ask, answer) in enumerate(exchanges):
            recs.append({"type": "user", "timestamp": _iso(t), "uuid": "u%d" % k, "parentUuid": prev,
                         "promptSource": "typed", "message": {"role": "user", "content": ask}})
            recs.append({"type": "assistant", "timestamp": _iso(t + 20), "uuid": "a%d" % k,
                         "parentUuid": "u%d" % k,
                         "message": {"role": "assistant", "content": [{"type": "text", "text": answer}],
                                     "stop_reason": "end_turn"}})
            prev = "a%d" % k
        p = Path(self.td) / (SID + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        turns = jd.em.parse_session(str(p), rompuuid=SID, candidate_files=[str(p)], now=NOW)["turns"]
        self.assertEqual(len(turns), len(exchanges), "fixture: one turn per exchange")
        s = _store()
        segs = [jd._segs(t, s)[0] for t in turns]
        seg_by_id = {sg["id"]: sg for t in turns for sg in jd._segs(t, s)}
        return turns, segs, s, seg_by_id

    def _closer_rows(self, nd, kind):
        return [e for e in nd["log"] if e.get("src") == "closer" and e.get("kind") == kind]

    def _card_and_riding_step(self):
        """A card the user answered whose answer was filed under a step's sub-goal; on a later follow-up
        about another card, the card rides as a status rider (#2) and the step as a steps-finished
        rider (#1)."""
        turns, segs, s, seg_by_id = self._parse([
            (T0 - 200, "Start the notes-api release notes.",
             "Release notes started: outline in place. Should they cover this week or the whole month?"),
            (T0 + 300, _followup("gS", "This week. List the changes merged this week."),
             "Collected the merged changes: search paging and the login timeout fix."),
            OTHER_EXCHANGE])
        _node(s, "gS", "Write the notes-api release notes", t=T0 - 200, trail=[segs[0]["id"]], closerLookT=NOW,
              log=[_row("block", "closer", T0 - 200, why="This week or the whole month?"),
                   _row("reopen", "user", T0 + 299, msg=True), _row("reopen", "planner", T0 + 300)])
        _node(s, "gK", "Draft the changelog section", parent="gS", t=T0)
        _node(s, "gKc", "Collect the merged changes", parent="gK", t=T0 + 300, trail=[segs[1]["id"]],
              log=[_row("done", "closer", T0 + 300, why="collected")])
        s["placements"].update({segs[0]["id"]: _gid("gS"), segs[1]["id"]: _gid("gKc")})
        _other_card(s, segs[2]["id"])
        self.assertEqual([nd["id"] for nd in jd._subtree_done_candidates(s)], [_gid("gK")],
                         "fixture: the step rides as steps-finished")
        self.assertEqual([nd["id"] for nd in jd._status_report_candidates(s, turns[2])], [_gid("gS")],
                         "fixture: the card rides as a status rider")
        return turns, segs, s, seg_by_id

    def _stale_closes(self, nid):
        try:
            rows = [json.loads(l) for l in Path(jd.ERRORS).read_text().splitlines() if l.strip()]
        except OSError:
            return []
        return [r for r in rows if r.get("err") == "stale-close" and r.get("goal") == nid]


class IncidentStaleReblock(_Closer):
    """The first incident, synthetic: the reply's answer lives on a done sub-goal, and a later
    follow-up about another (already ruled) card puts the answered card on the list as a status rider."""

    def _incident(self):
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_REPLY, _followup("gT", "Load-test it on staging first, then ship."), ANSWER),
            OTHER_EXCHANGE])
        self.assertEqual(segs[1]["t"], T_REPLY, "fixture: the reply segment starts at the reply")
        top = _answered_card(s, segs[0]["id"])
        _node(s, "gC", "Load-test the limit and ship it", parent="gT", t=T_REPLY,
              trail=[segs[1]["id"]], log=[_row("done", "closer", T_REPLY, why="load-tested and shipped")])
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: _gid("gC")})
        _other_card(s, segs[2]["id"])
        self.assertEqual(top.get("followupAt"), T_REPLY - 1, "fixture: the reply's floor")
        self.assertEqual(jd._turn_menu(turns[2], s), [], "fixture: the planner already ruled the turn's own goal")
        self.assertEqual([nd["id"] for nd in jd._status_report_candidates(s, turns[2])], [top["id"]],
                         "fixture: the answered card rides as a status rider")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        self.assertEqual(len(self.calls), 1)
        return s, top, self.calls[0]

    def test_a_later_turn_does_not_carry_the_old_replys_flag(self):
        _s, _top, call = self._incident()
        self.assertNotIn(FLAG_LINE, call["menu"],
                         "the old reply's flag no longer tells the closer to rule the card from this turn")

    def test_the_rider_is_shown_the_answer_filed_under_its_steps(self):
        _s, _top, call = self._incident()
        self.assertIn("Ship it now, or load-test it on staging first?", call["hist"],
                      "its own trail still rides")
        self.assertIn("Shipped the limit as commit 4c9e2d1", call["hist"],
                      "and the sub-goal work that answered it, so the history no longer ends at the question")

    def test_the_replys_subject_is_named_and_the_rider_barred_from_its_credit(self):
        _s, _top, call = self._incident()
        self.assertIn(SUBJECT_LINE, call["menu"])
        self.assertIn("\n- Fix the web login timeout\n", call["menu"], "the subject, by its title")
        self.assertIn("Credit none of it to goal #1 unless", call["menu"], "the rider, by its number")


class ReplySubject(_Closer):
    def test_no_subject_line_when_the_replys_goal_is_on_the_list(self):
        # a follow-up whose reply was placed under its own card: the card is the turn's own goal, so
        # there is nothing to name
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_REPLY, _followup("gT", "Load-test it on staging first, then ship."), ANSWER)])
        top = _answered_card(s, segs[0]["id"])
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: top["id"]})
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertNotIn(SUBJECT_LINE, self.calls[0]["menu"])
        self.assertIn(FLAG_LINE, self.calls[0]["menu"], "the reply turn itself still flags its own card")

    def _new_thread(self, new_goal_done):
        """A reply on the answered card that the planner ruled a NEW thread: it dismissed the card (back
        to needs-you, off the list) and filed the reply under a new card. An unrelated open card rides
        as a status rider."""
        turns, segs, s, seg_by_id = self._parse([
            (T0 - 200, "Add paging to the notes-api search results.", "Paging added; merging now."),
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_OTHER, _followup("gT", "Separate thing: add a dark mode toggle to the web settings page."),
             "Added the dark mode toggle to the web settings page.")])
        _node(s, "gP", "Ship search paging", t=T0 - 200, trail=[segs[0]["id"]], closerLookT=NOW)
        card = _node(s, "gT", "Rate-limit the api login route", trail=[segs[1]["id"]], closerLookT=NOW,
                     log=[_row("block", "closer", T0, why="Ship the login rate limit now, or load-test it first?"),
                          _row("reopen", "user", T_OTHER - 1, msg=True),
                          _row("dismiss", "planner", T_OTHER, why="the reply started its own thread")])
        _node(s, "gN", "Add a dark mode toggle to the web settings", t=T_OTHER, trail=[segs[2]["id"]],
              log=[_row("done", "planner", T_OTHER, why="added")] if new_goal_done else [])
        s["placements"].update({segs[0]["id"]: _gid("gP"), segs[1]["id"]: card["id"], segs[2]["id"]: _gid("gN")})
        self.assertTrue(card.get("blocked"), "fixture: the dismiss restored the card's block")
        return turns, s, seg_by_id

    def test_no_subject_line_on_a_new_thread_turn(self):
        turns, s, seg_by_id = self._new_thread(new_goal_done=False)
        self.assertEqual([nd["id"] for nd in jd._turn_menu(turns[2], s)], [_gid("gN")],
                         "fixture: the new card is the turn's own goal")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        menu = self.calls[0]["menu"]
        self.assertIn("1. Add a dark mode toggle", menu)
        self.assertIn("2. Ship search paging", menu)
        self.assertNotIn(SUBJECT_LINE, menu, "the planner ruled the reply is not about the cited card")

    def test_a_dismissed_card_is_never_named_even_with_only_riders_listed(self):
        # the new thread's card was ruled in the same pass, so only the rider is listed: the dismissed card
        # is still not the reply's subject
        turns, s, seg_by_id = self._new_thread(new_goal_done=True)
        self.assertEqual(jd._turn_menu(turns[2], s), [], "fixture: nothing of the turn's own is listed")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        self.assertIn("1. Ship search paging", self.calls[0]["menu"])
        self.assertNotIn(SUBJECT_LINE, self.calls[0]["menu"])

    def test_no_subject_line_when_the_turn_lists_a_goal_of_its_own(self):
        # the reply's goal was ruled done, but the turn's work was filed under an open card, which is listed
        # first: the reply has a home on the list, so the line is not needed
        turns, segs, s, seg_by_id = self._parse([
            (T0 - 200, "Add paging to the notes-api search results.", "Paging added; merging now."),
            (T_OTHER, _followup("gU", "Is the web login timeout fix merged? Then add a dark mode toggle."),
             "Yes, the login timeout fix is merged. Added the dark mode toggle to the web settings page.")])
        _node(s, "gP", "Ship search paging", t=T0 - 200, trail=[segs[0]["id"]], closerLookT=NOW)
        _node(s, "gU", "Fix the web login timeout", t=T0 - 30, log=[_row("done", "planner", T_OTHER)])
        _node(s, "gN", "Add a dark mode toggle to the web settings", t=T_OTHER, trail=[segs[1]["id"]])
        s["placements"].update({segs[0]["id"]: _gid("gP"), segs[1]["id"]: _gid("gN")})
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        menu = self.calls[0]["menu"]
        self.assertIn("1. Add a dark mode toggle", menu, "fixture: the turn's own goal")
        self.assertIn("2. Ship search paging", menu, "fixture: a status rider")
        self.assertNotIn(SUBJECT_LINE, menu)

    def test_a_rider_the_message_also_names_is_not_barred(self):
        # one message about two cards (a bundled check-in): the planner ruled one before the closer ran,
        # the other rides the list. Only the rider the message does not name is barred from the credit
        turns, segs, s, seg_by_id = self._parse([
            (T0 - 200, "Add paging to the notes-api search results.", "Paging added; merging now."),
            (T0 - 100, "Write the notes-api changelog entry.", "Changelog entry drafted."),
            (T_OTHER, _followup(["gU", "gP"], "Where do the login timeout fix and the search paging stand?"),
             "The login timeout fix is merged. Search paging is merged and live.")])
        _node(s, "gP", "Ship search paging", t=T0 - 200, trail=[segs[0]["id"]], closerLookT=NOW)
        _node(s, "gL", "Publish the changelog entry", t=T0 - 100, trail=[segs[1]["id"]], closerLookT=NOW)
        s["placements"].update({segs[0]["id"]: _gid("gP"), segs[1]["id"]: _gid("gL")})
        _other_card(s, segs[2]["id"])
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        menu = self.calls[0]["menu"]
        self.assertIn("1. Ship search paging", menu, "fixture: the named card rides first")
        self.assertIn("2. Publish the changelog entry", menu)
        self.assertIn(SUBJECT_LINE, menu)
        self.assertIn("\n- Fix the web login timeout\n", menu)
        self.assertIn("The same message also asks about goal #1, listed above.", menu)
        self.assertIn("Credit none of it to goal #2 unless", menu, "only the rider the message does not name")


class RiderHistory(_Closer):
    def test_the_turns_own_chain_shows_each_entry_its_own_trail(self):
        # the turn worked on a step under an open card: both are the turn's own goals, listed as separate
        # entries, so the card's block must not repeat the step's work
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Build the notes-api search page.", "Search page scaffolded with an empty result list."),
            (T0 + 300, "Add paging to the search results.", "Paging added: 20 results per page.")])
        _node(s, "gP", "Ship the notes-api search page", trail=[segs[0]["id"]])
        _node(s, "gX", "Add search result paging", parent="gP", t=T0 + 300, trail=[segs[1]["id"]])
        s["placements"].update({segs[0]["id"]: _gid("gP"), segs[1]["id"]: _gid("gX")})
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        hist = self.calls[0]["hist"]
        self.assertEqual(hist.count("Paging added: 20 results per page."), 1, hist)
        card = hist.split("Goal #2")[0]
        self.assertIn("Search page scaffolded", card)
        self.assertNotIn("Paging added", card, "the card's own entry stays its own trail")

    def test_a_descendant_riding_the_same_call_is_shown_once(self):
        # a status-rider card and its own step, a steps-finished rider, in one call: each is shown its
        # subtree, and the card's walk prunes the step, whose entry already shows that work
        turns, segs, s, seg_by_id = self._card_and_riding_step()
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        hist = self.calls[0]["hist"]
        self.assertIn("Goal #1 (Draft the changelog section)", hist)
        self.assertIn("Goal #2 (Write the notes-api release notes)", hist)
        self.assertEqual(hist.count("Collected the merged changes"), 1, hist)
        step, card = hist.split("Goal #2")
        self.assertIn("Collected the merged changes", step, "the step shows the work under it")
        self.assertIn("outline in place", card)
        self.assertNotIn("Collected the merged changes", card, "the card's walk prunes the step riding beside it")


class HistoryAnchor(_Closer):
    BLOCK = '{"done": [], "block": [{"goal": %d, "why": "%s"}]}'

    def test_a_block_from_history_older_than_the_users_reply_stands_down(self):
        # the answer went to a NEW card, outside the answered one's subtree, so the answered card's history
        # still ends at its question. A rider block computed from that history predates the user's reply:
        # it stands down, loudly, instead of re-asking what the reply answered
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_REPLY, _followup("gT", "Load-test it on staging first, then ship."), ANSWER),
            OTHER_EXCHANGE])
        top = _answered_card(s, segs[0]["id"])
        _node(s, "gY", "Load-test and ship the login limit", t=T_REPLY, trail=[segs[1]["id"]],
              log=[_row("done", "closer", T_REPLY, why="shipped")])
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: _gid("gY")})
        _other_card(s, segs[2]["id"])
        self.reply = self.BLOCK % (1, "Ship the login rate limit now, or load-test it first?")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        self.assertEqual(len(self._closer_rows(top, "block")), 1, "only the original question's block")
        self.assertFalse(top.get("blocked"), "the answered card stays working")
        self.assertTrue(self._stale_closes(top["id"]), "the stand-down is logged, naming the card")

    def test_a_steps_finished_riders_block_stands_down_under_its_cards_reply(self):
        # the floor is the CARD's (the user replies on the card, never on its steps): a step whose own
        # finished work predates that reply rides as steps-finished, and its block from that older work
        # stands down too
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T0 + 300, "Write the limit's error message.", "Error message written. Mention the retry delay in it?"),
            (T_REPLY, _followup("gT", "Load-test it on staging first, then ship."), ANSWER),
            OTHER_EXCHANGE])
        top = _answered_card(s, segs[0]["id"])
        step = _node(s, "gF", "Word the limit's error message", parent="gT", t=T0 + 300)
        _node(s, "gFc", "Draft the error text", parent="gF", t=T0 + 300, trail=[segs[1]["id"]],
              log=[_row("done", "closer", T0 + 300, why="drafted")])
        _node(s, "gY", "Load-test and ship the login limit", t=T_REPLY, trail=[segs[2]["id"]],
              log=[_row("done", "closer", T_REPLY, why="shipped")])
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: _gid("gFc"), segs[2]["id"]: _gid("gY")})
        _other_card(s, segs[3]["id"])
        self.assertIn(step["id"], [nd["id"] for nd in jd._subtree_done_candidates(s)],
                      "fixture: the step rides as steps-finished")
        self.reply = self.BLOCK % (1, "Mention the retry delay in the error message?")
        jd._close_turn(s, turns[3], seg_by_id=seg_by_id)
        self.assertIn("1. Word the limit's error message", self.calls[0]["menu"],
                      "fixture: the step is #1 (steps-finished riders list ahead of status riders)")
        self.assertFalse(step.get("blocked"), "the step's block from pre-reply work stands down")
        self.assertEqual(self._closer_rows(step, "block"), [], "the card's floor voids it at the write")

    def test_a_block_the_newer_history_supports_still_lands(self):
        # the reply's own work, under the card, ends with a NEW question: that history is newer than the
        # user's reply, so the rider's block lands, anchored to the segment it came from
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_REPLY, _followup("gT", "Load-test it on staging first, then ship."),
             "Load-tested on staging: the limit also throttles the web app's own retries. Ship it anyway, or "
             "exempt the web app first?"),
            OTHER_EXCHANGE])
        top = _answered_card(s, segs[0]["id"])
        _node(s, "gC", "Load-test the limit", parent="gT", t=T_REPLY, trail=[segs[1]["id"]], closerLookT=NOW)
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: _gid("gC")})
        _other_card(s, segs[2]["id"])
        self.reply = self.BLOCK % (1, "Ship with the web app throttled, or exempt it first?")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        self.assertTrue(top.get("blocked"), "the block lands")
        self.assertEqual(self._closer_rows(top, "block")[-1]["ev_t"], segs[1]["t"],
                         "anchored to the newest segment the closer was shown, not the ruling turn")

    def test_a_riders_anchor_counts_the_work_shown_under_a_step_riding_beside_it(self):
        # the card's own block is pruned of the step riding the same call, but that step's work, newer
        # than the user's reply, is history the closer was shown about the card: a block it supports lands
        turns, segs, s, seg_by_id = self._card_and_riding_step()
        card = s["nodes"][_gid("gS")]
        self.assertEqual(card.get("followupAt"), T0 + 299, "fixture: the reply's floor sits between the two")
        self.reply = self.BLOCK % (2, "Include the login timeout fix, which shipped behind a flag?")
        jd._close_turn(s, turns[2], seg_by_id=seg_by_id)
        self.assertTrue(card.get("blocked"), "the block the step's newer work supports lands")
        self.assertEqual(self._closer_rows(card, "block")[-1]["ev_t"], segs[1]["t"])

    def test_the_turns_own_target_keeps_the_turns_time(self):
        # a follow-up ON the card, whose reply the planner filed under a new card: the answered card rides
        # as a status rider, but this reply is about it, so a block from the reply keeps the turn's time
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Rate-limit the api login route.", QUESTION),
            (T_REPLY, _followup("gT", "Load-test it on staging first."),
             "The limit also throttles the web app's own retries. Ship it anyway, or exempt the web app first?")])
        top = _answered_card(s, segs[0]["id"])
        _node(s, "gY", "Load-test the login limit", t=T_REPLY, trail=[segs[1]["id"]])
        s["placements"].update({segs[0]["id"]: top["id"], segs[1]["id"]: _gid("gY")})
        self.reply = self.BLOCK % (2, "Ship with the web app throttled, or exempt it first?")
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertIn(FLAG_LINE, self.calls[0]["menu"], "fixture: the reply turn flags the card it names")
        self.assertTrue(top.get("blocked"), "the reply's own question lands on the card it answers")
        self.assertEqual(self._closer_rows(top, "block")[-1]["ev_t"], turns[1]["t"])


class LiftAnchor(_Closer):
    """A lift rider (the unblocker ruled its wait over): its done keeps the lift's anchor, as before; its
    BLOCK anchors to the history it was shown, so a re-block drawn from history the lift already
    answered is shadowed by that lift and stands down (the closer and unblocker no longer trade the
    same question back and forth)."""

    def _lifted(self):
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Write the notes-api changelog entry for the search page.",
             "Entry written. Publish it as written?"),
            (T_OTHER, "Check the web build.", "The web build is green.")])
        nd = _node(s, "gL", "Publish the search page changelog entry", trail=[segs[0]["id"]],
                   log=[_row("block", "closer", T0, why="Publish the entry as written?"),
                        _row("unblock", "unblocker", T_LIFT, why="the user said yes on the card")])
        s["placements"][segs[0]["id"]] = nd["id"]
        self.assertEqual([n["id"] for n, _w in jd._lift_riders(s)], [nd["id"]], "fixture: a lift rider")
        return turns, s, seg_by_id, nd

    def test_a_block_from_history_the_lift_answered_stands_down(self):
        turns, s, seg_by_id, nd = self._lifted()
        self.reply = '{"done": [], "block": [{"goal": 1, "why": "Publish the entry as written?"}]}'
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertEqual(len(self._closer_rows(nd, "block")), 1, "only the original block")
        self.assertFalse(nd.get("blocked"))
        self.assertTrue(self._stale_closes(nd["id"]))

    def test_a_lift_riders_done_keeps_the_lift_anchor(self):
        turns, s, seg_by_id, nd = self._lifted()
        self.reply = '{"done": [{"goal": 1, "why": "published after the user said yes"}], "block": []}'
        self.assertEqual(jd._close_turn(s, turns[1], seg_by_id=seg_by_id), [nd["id"]])
        self.assertEqual(self._closer_rows(nd, "done")[-1]["ev_t"], T_LIFT)


class RiderDone(_Closer):
    """A rider's DONE keeps the ruling turn's time. Anchored to its history's newest segment instead, a
    done sorted before any newer state row in the goal's own diary (a stop's pause, a re-engage, a lift)
    and stood down: the finished goal the steps-finished channel exists to complete past a pause, and
    the status rider whose outcome the reply reports, both stayed open."""
    DONE = '{"done": [{"goal": %d, "why": "%s"}], "block": []}'

    def test_a_paused_goal_whose_steps_all_finished_completes(self):
        # the user's stop left a finished goal paused; it rides as steps-finished on an unrelated turn
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Add paging to the notes-api search results.", "Paging added and merged: 20 per page."),
            (T_OTHER, "Check the web build.", "The web build is green.")])
        top = _node(s, "gP", "Ship search paging",
                    log=[_row("block", "interrupt", T0 + 400, why="you stopped this session mid-turn")])
        _node(s, "gC", "Merge the paging change", parent="gP", t=T0, trail=[segs[0]["id"]],
              log=[_row("done", "closer", T0, why="merged")])
        _node(s, "gW", "Check the web build", t=T_OTHER, trail=[segs[1]["id"]])
        s["placements"].update({segs[0]["id"]: _gid("gC"), segs[1]["id"]: _gid("gW")})
        self.assertTrue(jd._intr_paused_only(top), "fixture: paused by the stop, nothing asked")
        self.assertIn(top["id"], [nd["id"] for nd in jd._subtree_done_candidates(s)],
                      "fixture: it rides as steps-finished")
        self.reply = self.DONE % (2, "paging merged")
        newly = jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertIn("2. Ship search paging", self.calls[0]["menu"])
        self.assertEqual(newly, [top["id"]])
        self.assertTrue(top.get("nodeComplete"), "the finished goal completes past the pause")
        self.assertEqual(self._closer_rows(top, "done")[-1]["ev_t"], turns[1]["t"])

    def test_a_status_riders_done_lands_past_a_pause_and_a_re_engage(self):
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Add paging to the notes-api search results.", "Paging added; merging now."),
            (T_OTHER, _followup("gU", "Is the web login timeout fix merged?"),
             "Yes, the login timeout fix is merged. Search paging also finished merging and is live.")])
        top = _node(s, "gP", "Ship search paging", trail=[segs[0]["id"]],
                    log=[_row("block", "interrupt", T0 + 400, why="you stopped this session mid-turn"),
                         _row("unblock", "user", T_OTHER, why="you re-engaged")])
        s["placements"][segs[0]["id"]] = top["id"]
        _other_card(s, segs[1]["id"])
        self.assertEqual([nd["id"] for nd in jd._status_report_candidates(s, turns[1])], [top["id"]],
                         "fixture: a status rider")
        self.reply = self.DONE % (1, "the reply says paging is merged and live")
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertTrue(top.get("nodeComplete"), "the status reply's done lands")

    def test_a_status_riders_done_lands_past_a_lifted_block(self):
        # a block stamped with its ruling turn's time, later than the goal's own history, then lifted
        turns, segs, s, seg_by_id = self._parse([
            (T0, "Write the notes-api changelog entry.", "Entry written. Publish it as written?"),
            (T_OTHER, _followup("gU", "Is the web login timeout fix merged?"),
             "Yes, merged. The changelog entry is published too.")])
        top = _node(s, "gL", "Publish the changelog entry", trail=[segs[0]["id"]],
                    log=[_row("block", "closer", T0 + 1000, why="Publish as written?"),
                         _row("unblock", "unblocker", T0 + 1100, why="the user said yes")])
        s["placements"][segs[0]["id"]] = top["id"]
        _other_card(s, segs[1]["id"])
        self.reply = self.DONE % (1, "the reply says the entry is published")
        jd._close_turn(s, turns[1], seg_by_id=seg_by_id)
        self.assertIn("1. Publish the changelog entry", self.calls[0]["menu"])
        self.assertTrue(top.get("nodeComplete"), "the status reply's done lands")


if __name__ == "__main__":
    unittest.main()
