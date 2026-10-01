#!/usr/bin/env python3
"""Every chat event the kernel builds carries `turn`, the index of the kernel turn it belongs to (2026-10-01; caps `chatTurn`).

The page draws a history gap as its turn count, in the kernel's numbering, times a px-per-turn it measures off the rows it holds, and
it used to count those rows' turns itself, one per typed row. The kernel opens a turn on a peer session's message too, so on a session
that takes much mail the page read about half the turns and drew every gap about twice its height. The stamp lets the page count the
kernel's turns: pinned here over a synthetic session whose turns alternate between a typed prompt and a peer's message, the stamp is
the parse's own turn index on every event (a hydrated postal card included), it rides the full frame, the delta, a history page and a
gap's page, a folded build stamps its sealed prefix exactly as a whole build does, and the kernel announces the field in its caps.
Synthetic transcripts only (placeholder uuids, invented notes-api prose)."""
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


if __name__ == "__main__":
    unittest.main()
