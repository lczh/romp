#!/usr/bin/env python3
"""Every transcript event the kernel builds carries `turn`, the index of the parse turn it belongs to (2026-10-01; caps `chatTurn`).

The page draws a history gap as its turn count, in the kernel's numbering, times a px-per-turn it measures off the rows it holds, and
it used to count those rows' turns itself, one per typed row. The kernel opens a turn on a peer session's message too, so on a session
that takes much mail the page read about half the turns and drew every gap about twice its height. The stamp lets the page count the
kernel's turns: pinned here over a synthetic session whose turns alternate between a typed prompt and a peer's message, the stamp is
the parse's own turn index on every transcript event (a hydrated postal card included), it rides the full frame, the delta, a history page and a
gap's page, a folded build stamps its sealed prefix exactly as a whole build does, and the kernel announces the field in its caps. A send
that never landed and sits in a synthetic turn of the build's list between two turns leaves every stamp the parse's turn: its own row
takes the parse turn before its synthetic turn, and one whose synthetic turn comes before every turn carries none. Synthetic
transcripts only (placeholder uuids, invented notes-api prose)."""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from test_chat_pages import NOW, SID, Harness, _client, _strip, jd, km   # noqa: E402

PEER = "bbbbbbbb-5555-4666-8777-888888888888"   # the session the mail comes from ("api")
TURNS = 40
T0 = NOW - 5 * 3600
BAR = "─" * 40


def _iso(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _mid(k):
    return "11111111-2222-3333-4444-%012d" % k


def _banner(k, t):
    """The delivered banner the postal service injects (bin/romp-postal-service format_push), as the CLI records it."""
    return "\n".join([BAR, "## from api", BAR, "The retry budget in notes-api is ready for review, part %d." % k,
                      "<!-- romp-msg-id: %s -->" % _mid(k), "<!-- romp-msg-kind: coordinate -->", BAR])


def world(turns, start=0, parent=None):
    """`turns` turns from turn `start`: an even turn is a typed prompt and its reply, an odd one a peer's message and its reply.
    Returns (records, postal log rows, last uuid)."""
    recs, log = [], []
    for k in range(start, start + turns):
        t = T0 + 60 * k
        u, a = "u%04d" % k, "a%04d" % k
        if k % 2 == 0:
            recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed", "sessionId": SID,
                         "message": {"role": "user", "content": "what is left on the notes index, step %d?" % k}})
        else:
            recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "sdk", "sessionId": SID,
                         "message": {"role": "user", "content": _banner(k, t)}})
            log.append({"t": t - 1, "ev": "sent", "id": _mid(k), "from": "api", "from_id": PEER, "to_id": SID,
                        "body": "The retry budget in notes-api is ready for review, part %d." % k, "kind": "coordinate", "from_host": ""})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t + 20), "sessionId": SID,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "Done with step %d of the notes index." % k}]}})
        parent = a
    return recs, log, parent


class TurnStamp(Harness):
    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")       # a state root of its own: no session host (T348)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / PEER).write_text("api\t/w/notes-api\t#1EA1EB\t#ffffff\n")
        self.recs, self.log, self.last = world(TURNS)
        self._write()

    def _write(self):
        self.write(self.recs)
        (jd.STATE / "timeline" / "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in self.log))

    def _append(self, n):
        recs, log, self.last = world(n, start=TURNS, parent=self.last)
        self.recs += recs
        self.log += log
        self._write()

    def _turn_of_uuid(self):
        """The parse's own numbering: each record's uuid → the index of the turn holding it."""
        turns = km._parse(self.leaf, SID, NOW)["turns"]
        return {a["uuid"]: i for i, t in enumerate(turns) for a in t.get("atoms") or [] if a.get("uuid")}, len(turns)

    def _whole(self):
        self.fresh()
        return _strip(km.build_session(SID, NOW, {}, floor=0)["events"])

    def test_every_event_carries_the_parse_turn_and_a_peer_message_opens_its_own(self):
        evs = self._whole()
        u2t, n = self._turn_of_uuid()
        self.assertEqual(n, TURNS, "the fixture's premise: a peer's message opens a turn of its own")
        placed = [e for e in evs if str(e.get("uuid") or "").split("#", 1)[0] in u2t]
        self.assertEqual(len(placed), 2 * TURNS, "every record is an event: %r" % [e.get("kind") for e in evs])
        for e in placed:
            self.assertEqual(e.get("turn"), u2t[e["uuid"].split("#", 1)[0]], "the stamp is the parse's turn: %r" % e)
        cards = [e for e in evs if e.get("kind") == "postal-service" and e.get("direction") == "in"]
        self.assertEqual(len(cards), TURNS // 2, "each peer message is hydrated into its card (what the page draws as a card, not a typed row)")
        self.assertEqual(sorted(e["turn"] for e in cards), list(range(1, TURNS, 2)), "…and a card carries its delivery's turn")
        typed = [e for e in evs if e.get("kind") == "user"]
        self.assertEqual(len(typed), TURNS // 2, "the typed rows are half the turns: the count the page kept read half")
        self.assertEqual(len({e["turn"] for e in placed}), TURNS, "the stamps name every turn once")

    def test_the_full_frame_and_the_delta_carry_the_stamp(self):
        self.fresh()
        m = km.build_session(SID, NOW, {}, floor=0)
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        frame = sent[-1]
        self.assertEqual(frame.get("proto"), 2)
        u2t, _ = self._turn_of_uuid()
        on_wire = [e for e in _strip(frame["events"]) if str(e.get("uuid") or "") in u2t]
        self.assertTrue(on_wire)
        self.assertEqual([e.get("turn") for e in on_wire], [u2t[e["uuid"]] for e in on_wire], "the full frame's events carry their turn")
        self._append(2)                                         # a typed turn and a peer's message land
        m2 = km.build_session(SID, NOW, {}, floor=0)
        km._send_chat_locked(c, m2, None, km._chat_diff(m["events"], m2["events"]), False)
        tail = sent[-1]
        self.assertEqual(tail.get("type"), "chatTail", "the new turns ride a delta: %r" % {k: tail.get(k) for k in ("type", "afterUuid")})
        new = [e for e in _strip(tail["events"]) if e.get("uuid") in ("u%04d" % TURNS, "u%04d" % (TURNS + 1))]
        self.assertEqual([(e["kind"], e.get("turn")) for e in new], [("user", TURNS), ("postal-service", TURNS + 1)],
                         "the delta's events carry their turns, the peer's message its own")

    def test_a_history_page_and_a_gaps_page_carry_turns_inside_their_span(self):
        self.fresh()
        km.build_session(SID, NOW, {}, floor=0)
        page = _strip(km._chat_history_page(SID, 8, 16, NOW))
        self.assertTrue(page)
        self.assertEqual(sorted({e.get("turn") for e in page}), list(range(8, 16)), "a page's events carry the turns it renders")
        r = km._chat_history_reply(SID, {"type": "loadTurns", "id": SID, "lo": 16, "hi": 32}, NOW)
        got = _strip(r["events"])
        self.assertTrue(got)
        self.assertEqual(sorted({e.get("turn") for e in got}), list(range(16, 32)), "a gap's page (loadTurns) too: %r" % r.get("span"))

    def test_a_folded_build_stamps_its_sealed_prefix_as_the_whole_build_does(self):
        self.fresh()
        km.build_session(SID, NOW, {}, floor=0)                 # seals the ended turns
        self._append(2)
        folded = _strip(km.build_session(SID, NOW, {}, floor=0)["events"])
        self.assertEqual(km._chat_fold_last_info().get("fold"), 1, "the second build reused the sealed prefix: %r" % km._chat_fold_last_info())
        whole = self._whole()
        self.assertEqual([(e.get("uuid"), e.get("turn")) for e in folded], [(e.get("uuid"), e.get("turn")) for e in whole])

    def test_the_kernel_announces_the_field_in_its_caps(self):
        self.assertIn("chatTurn", km.KERNEL_WS_CAPS)
        self.assertIn("chatTurn", km._version_info()["caps"])


ECHO = "echo:" + "e" * 32
ECHO_TEXT = "and rerun the notes index search suite after that"
LONG = 300            # turns: the full frame's wire tail begins past turn 101, the first turn after the echo


class _LiveTail(km._UnownedBackend):
    """The owning backend as the chat build reads it: a live tail the test sets, a prune that retires nothing."""

    def __init__(self):
        self.live = []

    def owns(self, sid):
        return True

    def live_atoms(self, sid):
        return sorted(self.live, key=lambda a: a.get("t", 0))

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        return None


class StaleEchoInAnIdleGap(Harness):
    """Each test has a send that never landed in a synthetic turn of the build's turn list (_place_stale_echoes, `echoTurn`), a turn
    the parse's list does not hold. Every stamp stays the parse's turn index: an echo's own row takes the parse turn before its
    synthetic turn, or none when that turn comes before every turn, and a card made from a live message with no uuid carries none."""

    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")       # a state root of its own: no session host (T348)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / PEER).write_text("api\t/w/notes-api\t#1EA1EB\t#ffffff\n")
        recs, log, _ = world(LONG)
        self.write(recs)
        (jd.STATE / "timeline" / "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log))
        self.be = _LiveTail()
        saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.addCleanup(setattr, km.Sessions, "backend_for", saved_bf)
        km._merge_sets_memo.clear()
        self.addCleanup(km._merge_sets_memo.clear)

    def _echo(self, t):
        """A dropped send (the backend flagged it never delivered), its text in no record."""
        return {"type": "user", "uuid": ECHO, "session_id": SID, "t": t, "parentUuid": None, "author": "human", "dropped": True,
                "_echo_text": ECHO_TEXT, "message": {"role": "user", "content": [{"type": "text", "text": ECHO_TEXT}]}}

    def _build(self):
        self.fresh()
        return km.build_session(SID, NOW, {}, floor=0)

    def _parse_turns(self):
        return km._parse(self.leaf, SID, NOW)["turns"]

    def _assert_stamps_are_the_parse_turns(self, evs):
        turns = self._parse_turns()
        tix = km._turn_index_of_events(evs, turns)
        u2t = {a["uuid"]: i for i, t in enumerate(turns) for a in t.get("atoms") or [] if a.get("uuid")}
        off = [(e.get("uuid"), e.get("turn"), tix[i]) for i, e in enumerate(evs) if "turn" in e and e["turn"] != tix[i]]
        self.assertEqual(off, [], "every stamp is the turn the regions number the event by (uuid, stamp, parse turn)")
        unstamped = [e.get("uuid") for e in evs if str(e.get("uuid") or "").split("#", 1)[0] in u2t and "turn" not in e]
        self.assertEqual(unstamped, [], "every record's event carries its stamp")
        return tix

    def test_an_echo_turn_between_two_turns_leaves_every_stamp_the_parse_turn_in_the_build_the_frame_and_a_gap_page(self):
        self.be.live.append(self._echo(T0 + 60 * 100 + 40))   # after turn 100's reply (+20), before turn 101 opens (+60)
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertEqual((len(self._parse_turns()), len(merged["turns"]), sum(1 for t in merged["turns"] if t.get("echoTurn"))),
                         (LONG, LONG + 1, 1), "the premise: the build's list holds one synthetic turn for the echo, the parse's none")
        m = self._build()
        evs = m["events"]
        self._assert_stamps_are_the_parse_turns(evs)
        self.assertEqual([e.get("turn") for e in evs if e.get("uuid") in (ECHO, "u0101")], [100, 101],
                         "the echo's row takes the turn before it; the turn after it keeps its own")
        c, sent = _client()
        km._send_chat_locked(c, m, None, 0, False)
        f = sent[-1]
        self.assertEqual(f.get("type"), "session")
        self.assertGreater(f.get("tailLo") or 0, 101, "the frame begins past the echo: %r" % f.get("tailLo"))
        self.assertEqual(next(e["turn"] for e in f["events"] if "turn" in e), f["tailLo"], "the frame's first stamp is its tailLo")
        self._assert_stamps_are_the_parse_turns(f["events"])
        r = km._chat_history_reply(SID, {"type": "loadTurns", "id": SID, "lo": 96, "hi": 112}, NOW)
        self.assertEqual(sorted({e["turn"] for e in r["events"] if "turn" in e}), list(range(96, 112)),
                         "the gap page's stamps are the turns of its span [96, 112): %r" % r.get("span"))
        self._assert_stamps_are_the_parse_turns(r["events"])

    def test_a_folded_build_with_an_echo_turn_between_two_turns_leaves_every_stamp_the_parse_turn(self):
        self.be.live.append(self._echo(T0 + 60 * 100 + 40))
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertEqual([i for i, t in enumerate(merged["turns"]) if t.get("echoTurn")], [101],
                         "the premise: the build's list holds one synthetic turn for the echo")
        self._build()                                           # seals the ended turns
        evs = km.build_session(SID, NOW, {}, floor=0)["events"]
        self.assertEqual(km._chat_fold_last_info().get("fold"), 1,
                         "the premise: the second build reused the sealed prefix: %r" % km._chat_fold_last_info())
        self._assert_stamps_are_the_parse_turns(evs)

    def test_dismissing_the_echo_moves_no_other_events_stamp(self):
        self.be.live.append(self._echo(T0 + 60 * 100 + 40))
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertEqual([i for i, t in enumerate(merged["turns"]) if t.get("echoTurn")], [101],
                         "the premise: the echo has a synthetic turn of its own, between parse turns 100 and 101")
        before = {e.get("uuid"): e.get("turn") for e in self._build()["events"]}
        self.assertIn(ECHO, before, "the premise: the echo's row is in the build")
        self.assertTrue(all(isinstance(before.get(u), int) for u in ("u0100", "u0101", "u0299")),
                        "the premise: the build with the echo stamps u0100, u0101 and u0299: %r"
                        % [before.get(u) for u in ("u0100", "u0101", "u0299")])
        self.be.live.clear()                                   # dismissed: the backend dropped it from the live tail
        after = {e.get("uuid"): e.get("turn") for e in self._build()["events"]}
        self.assertNotIn(ECHO, after)
        self.assertEqual({u: after[u] for u in after if u in before and after[u] != before[u]}, {},
                         "no event's stamp moves when the echo goes")

    def test_echo_turns_in_two_idle_gaps_shift_the_list_by_two_and_every_stamp_stays_the_parse_turn(self):
        second = self._echo(T0 + 60 * 150 + 40)                 # after turn 150's reply, before turn 151 opens
        text = "and refresh the notes index changelog too"
        second.update(uuid="echo:" + "f" * 32, _echo_text=text, message={"role": "user", "content": [{"type": "text", "text": text}]})
        self.be.live += [self._echo(T0 + 60 * 100 + 40), second]
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertEqual([i for i, t in enumerate(merged["turns"]) if t.get("echoTurn")], [101, 152],
                         "the premise: two synthetic turns, one per idle gap, so past the second the list is two ahead of the parse's")
        evs = self._build()["events"]
        self._assert_stamps_are_the_parse_turns(evs)
        self.assertEqual([e.get("turn") for e in evs if e.get("uuid") in (ECHO, "u0101", second["uuid"], "u0151")], [100, 101, 150, 151],
                         "each echo's row takes the turn before it; the turn after each keeps its own")

    def test_an_echo_turn_before_every_parse_turn_leaves_its_row_unstamped_and_every_turn_keeps_its_own(self):
        self.be.live.append(self._echo(T0 - 600))
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertTrue(merged["turns"][0].get("echoTurn"), "the premise: the echo's synthetic turn is the build's first")
        evs = self._build()["events"]
        tix = self._assert_stamps_are_the_parse_turns(evs)
        i = next(i for i, e in enumerate(evs) if e.get("uuid") == ECHO)
        self.assertNotIn("turn", evs[i], "its synthetic turn comes before every parse turn, so it carries none")
        self.assertEqual(tix[i], -1)
        self.assertEqual([e.get("turn") for e in evs if e.get("uuid") in ("u0000", "a0000")], [0, 0])

    def test_a_build_with_an_echo_and_a_live_message_with_no_uuid_completes_and_its_card_carries_no_stamp(self):
        """A guard on the `_tn is not None` checks of the stamp: the card made from a live message with no uuid has no turn to look up."""
        t = NOW - 30
        self.be.live += [self._echo(T0 + 60 * 100 + 40),
                         {"type": "user", "session_id": SID, "t": t, "parentUuid": None, "author": "peer",
                          "message": {"role": "user", "content": [{"type": "text", "text": _banner(LONG, t)}]}}]
        with open(jd.STATE / "timeline" / "messages.jsonl", "a") as f:
            f.write(json.dumps({"t": t - 1, "ev": "sent", "id": _mid(LONG), "from": "api", "from_id": PEER, "to_id": SID,
                                "body": "The retry budget in notes-api is ready for review, part %d." % LONG, "kind": "coordinate",
                                "from_host": ""}) + "\n")
        merged = km._merge_live_atoms(km._parse(self.leaf, SID, NOW), SID)
        self.assertEqual([i for i, x in enumerate(merged["turns"]) if x.get("echoTurn")], [101],
                         "the premise: the build's list holds a synthetic turn for the echo")
        evs = self._build()["events"]
        card = [e for e in evs if e.get("kind") == "postal-service" and e.get("mid") == _mid(LONG)]
        self.assertEqual(len(card), 1, "the premise: the live message is hydrated into its card: %r"
                         % [(e.get("kind"), e.get("uuid")) for e in evs[-4:]])
        self.assertNotIn("turn", card[0], "its card carries no stamp: %r" % card[0])


PENDING = "echo:" + "d" * 32
PENDING_TEXT = "start the notes index search suite"


class LiveTurnOfAnEmptyTranscript(Harness):
    """A session whose transcript holds no turn yet and whose live tail holds one pending send: _merge_live_atoms makes one live turn
    for it, which the parse's list does not hold."""

    def setUp(self):
        super().setUp()
        (jd.STATE / "session-hosts").write_text("off\n")       # a state root of its own: no session host (T348)
        self.write([])
        self.be = _LiveTail()
        saved_bf = km.Sessions.backend_for
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.addCleanup(setattr, km.Sessions, "backend_for", saved_bf)
        km._merge_sets_memo.clear()
        self.addCleanup(km._merge_sets_memo.clear)

    def test_the_pending_sends_row_is_stamped_0(self):
        self.be.live.append({"type": "user", "uuid": PENDING, "session_id": SID, "t": NOW - 5, "parentUuid": None, "author": "human",
                             "_echo_text": PENDING_TEXT, "message": {"role": "user", "content": [{"type": "text", "text": PENDING_TEXT}]}})
        parsed = km._parse(self.leaf, SID, NOW)
        merged = km._merge_live_atoms(parsed, SID)
        self.assertEqual((len(parsed.get("turns") or ()), [t.get("id") for t in merged["turns"]]), (0, ["live"]),
                         "the premise: no parse turn, one live turn")
        self.fresh()
        evs = km.build_session(SID, NOW, {}, floor=0)["events"]
        row = [e for e in evs if e.get("uuid") == PENDING]
        self.assertEqual(len(row), 1, "the premise: the pending send's row is in the build: %r" % [(e.get("kind"), e.get("uuid")) for e in evs])
        self.assertEqual(row[0].get("turn"), 0, "the live turn counts as turn 0: %r" % row[0])


if __name__ == "__main__":
    unittest.main()
