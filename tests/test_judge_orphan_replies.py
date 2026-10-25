#!/usr/bin/env python3
"""The judge parse must SEE salvaged replies (found by a peer session 2026-07-25).

orphanReply markers in states/<sid>.jsonl hold assistant text that streamed live but the transcript
never kept (an API-errored try). The kernel's CHAT build has interleaved them since 2026-07-21, but
jd.parsed_session read states/ only for idle atoms — so every judge (planner/closer/distiller/briefer)
saw those turns as reply-less: before the workless-segment guard the planner confabulated outcomes for
them, and after it those segments read as workless, so genuinely FINISHED work never filed as done
("cards never complete", "briefs don't match the chat"). event_model.parse_session now synthesizes the
markers into real assistant atoms — dedup'd by uuid, and by either-way text prefix against what the disk
kept near the marker's time (the chat's window: its turn and the turn on each side), with the CLI's own
"API Error: …" texts excluded (pre-tagging markers hold that noise). SYNTHETIC only."""
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
em = load_source("romp_em_orphan", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge_orphan", os.path.join(BIN, "romp-judge"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1781100000
T0 = NOW - 3600


def _atom(uuid, t, text, typ="assistant"):
    return {"type": typ, "uuid": uuid, "session_id": SID, "t": t, "fsid": "f", "parentUuid": None,
            "message": {"role": typ, "content": [{"type": "text", "text": text}]}, "_seq": t}


def _marker(t, uuid, text):
    return {"t": t, "orphanReply": {"uuid": uuid, "text": text}}


def _settled(replies):
    """One typed ask per entry, 300 s apart, each answered 20 s later by a kept reply that ends its turn. None is a reply
    the transcript never kept: that turn stays open, so the next ask rides it (segment_turns absorbs it)."""
    atoms = []
    for i, reply in enumerate(replies):
        t = 100 + 300 * i
        ask = _atom("u%d" % i, t, "ask %d about the notes-api" % i, typ="user")
        ask["author"] = "human"
        atoms.append(ask)
        if reply is not None:
            a = _atom("a%d" % i, t + 20, reply)
            a["message"]["stop_reason"] = "end_turn"
            atoms.append(a)
    return atoms


# turns: [u0 a0] [u1 a1] [u2 a2] [u3 a3] [u4, u5 a5]; a marker at 1360 sits in the last, whose window is it and [u3 a3]
FAR = ["Done.", "Let me check the migration log first.", "GET /notes and POST /notes are open.",
       "Added a limit of ten writes per minute.", None, "Bumped to 0.4.1."]


class SynthesizeOrphans(unittest.TestCase):
    def test_a_lost_reply_becomes_a_real_assistant_atom_at_its_timestamp(self):
        atoms = [_atom("u1", 100, "the ask", typ="user")]
        got = em.synthesize_orphans([_marker(160, "a9", "the salvaged answer")], atoms)
        self.assertEqual(len(got), 1)
        a = got[0]
        self.assertEqual((a["type"], a["uuid"], a["t"]), ("assistant", "a9", 160))
        self.assertTrue(a.get("orphaned"))
        self.assertNotIn("isApiError", a, "a salvaged reply is WORK — never error noise")
        self.assertEqual(a["message"]["stop_reason"], "end_turn")   # the marker is written at settle

    def test_dedup_by_uuid_and_either_way_text_prefix(self):
        atoms = [_atom("u1", 100, "the ask", typ="user"),
                 _atom("a1", 150, "the full reply the retry re-wrote")]
        # same uuid on disk → skip; partial text of a kept reply → skip; kept reply's completion → skip
        got = em.synthesize_orphans([
            _marker(160, "a1", "anything"),
            _marker(161, "a2", "the full reply"),
            _marker(162, "a3", "the full reply the retry re-wrote plus a tail"),
            _marker(163, "a4", "a genuinely different lost reply"),
        ], atoms)
        self.assertEqual([g["uuid"] for g in got], ["a4"])

    def test_a_reply_kept_far_from_the_markers_time_does_not_eat_it(self):
        """The chat dedups a marker only against the replies kept in the turn at its time and the turn on each side (kernel
        _texts_near) and shows it otherwise; this parse matched the whole session, so a short reply an early turn also
        said, or a partial stream an early reply extends, was in the chat and in no judge's tree (2026-10-25)."""
        atoms = _settled(FAR)
        for text in ("Done. The migration ran and all 12 tests pass.", "Let me check", "Done."):
            with self.subTest(text=text):
                got = em.synthesize_orphans([_marker(1360, "lost-4", text)], atoms)
                self.assertEqual([(g["uuid"], g["message"]["content"][0]["text"]) for g in got], [("lost-4", text)])

    def test_a_reply_kept_in_the_markers_window_still_dedups(self):
        atoms = _settled(FAR)
        for text in ("Added a limit of ten writes per minute.", "Added a limit", "Bumped to 0.4.1. Tagged it too."):
            with self.subTest(text=text):
                self.assertEqual(em.synthesize_orphans([_marker(1360, "lost-4", text)], atoms), [],
                                 "the turn before and the marker's own turn kept it")

    def test_an_earlier_marker_dedups_a_later_one_near_it_and_not_one_far_from_it(self):
        # turns: [u0 a0] [u1, u2 a2] [u3 a3] [u4, u5 a5]; the first marker rides the second turn, the others the last
        atoms = _settled(["Started.", None, "Listed the routes.", "Added the limit.", None, "Bumped to 0.4.1."])
        got = em.synthesize_orphans([_marker(460, "lost-1", "Done."), _marker(1360, "lost-4", "Done."),
                                     _marker(1400, "lost-4b", "Done.")], atoms)
        self.assertEqual([g["uuid"] for g in got], ["lost-1", "lost-4"],
                         "two turns apart, each its own reply; the re-orphan beside the second is that reply again")

    def test_duplicate_markers_for_the_same_reply_synthesize_once(self):
        atoms = [_atom("u1", 100, "the ask", typ="user")]
        got = em.synthesize_orphans([_marker(160, "a9", "the answer"),
                                     _marker(200, "a9", "the answer"),
                                     _marker(240, "b1", "the answer")], atoms)
        self.assertEqual(len(got), 1, "settle re-orphans dedup by uuid AND text")

    def test_api_error_texts_never_resurface_as_work(self):
        atoms = [_atom("u1", 100, "the ask", typ="user")]
        got = em.synthesize_orphans(
            [_marker(160, "e1", "API Error: 529 Overloaded. This is a server-side issue.")], atoms)
        self.assertEqual(got, [], "pre-tagging markers hold the CLI's error text — noise, not work")

    def test_a_textless_disk_twin_does_not_eat_the_salvage(self):
        """(the user 2026-07-28): on some model+tool combinations (observed: fable-5 replying before an
        AskUserQuestion) the CLI persists the streamed reply text as an EMPTY thinking record under the
        SAME uuid — the very loss the marker salvages. The uuid dedup counted that twin as 'the disk
        kept it' and dropped the marker; a textless record must not dedup a texty marker."""
        atoms = [_atom("u1", 100, "the ask", typ="user"),
                 {"type": "assistant", "uuid": "a5", "session_id": SID, "t": 150, "fsid": "f",
                  "parentUuid": None, "_seq": 150,
                  "message": {"role": "assistant",
                              "content": [{"type": "thinking", "thinking": ""}]}}]
        got = em.synthesize_orphans([_marker(160, "a5", "the explanation before the picker")], atoms)
        self.assertEqual([g["uuid"] for g in got], ["a5"])
        self.assertEqual(got[0]["message"]["content"][0]["text"], "the explanation before the picker")

    def test_a_reply_landed_on_an_abandoned_branch_never_resurrects(self):
        """The rollback ghost (the user 2026-08-03): the reply LANDED, then a chat delete forked the
        spine past it — off the kept path it leaves `atoms`, but it is not a loss, and once the
        rollback is consumed the leaf_override filter no longer applies. landed_text_uuids carries
        every text-bearing uuid on ANY branch, and a marker whose uuid is in it must stay dead."""
        atoms = [_atom("u1", 100, "the ask", typ="user")]
        got = em.synthesize_orphans([_marker(160, "a9", "the abandoned reply")], atoms,
                                    landed_text_uuids={"a9"})
        self.assertEqual(got, [])

    def test_landed_elsewhere_does_not_suppress_a_genuine_loss(self):
        atoms = [_atom("u1", 100, "the ask", typ="user")]
        got = em.synthesize_orphans([_marker(160, "a9", "the lost reply")], atoms,
                                    landed_text_uuids={"zz"})
        self.assertEqual([g["uuid"] for g in got], ["a9"],
                         "only the marker's OWN uuid landing suppresses it")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class JudgeSeesSalvagedWork(unittest.TestCase):
    """End-to-end: the exact regression — a question answered ONLY in a salvaged reply now completes."""

    def _run(self, records, markers, llm_reply):
        calls = []
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            tpath = td / (SID + ".jsonl")
            tpath.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            (td / "states").mkdir()
            (td / "states" / (SID + ".jsonl")).write_text(
                "\n".join(json.dumps(m) for m in markers) + "\n")
            saved = (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store)
            jd.GOALDIR, jd.PCACHE, jd.STATESDIR = td / "goals", td / "pcache", td / "states"
            def fake(text, *a, **k):
                calls.append(text)
                return llm_reply
            jd.plan_llm = jd.opener_llm = fake
            jd._group_store = lambda *a, **k: None
            try:
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                jd._plan_session(SID, str(tpath), NOW)
                store = jd.load_goals(SID)
            finally:
                (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            return calls, store

    def test_a_salvaged_answer_reaches_the_planner_and_the_goal_can_complete(self):
        records = [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed",
             "message": {"role": "user", "content": "explain the demo board's boot sequence"}},
            # the turn's reply was LOST to an API-errored try — nothing lands on disk; a later
            # unrelated turn ends the session's tail
            {"type": "user", "timestamp": iso(T0 + 400), "uuid": "u2", "parentUuid": "u1",
             "promptSource": "typed",
             "message": {"role": "user", "content": "unrelated: bump the version"}},
            {"type": "assistant", "timestamp": iso(T0 + 410), "uuid": "a2", "parentUuid": "u2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Bumped."}],
                         "stop_reason": "end_turn"}},
        ]
        markers = [{"t": T0 + 350, "orphanReply": {
            "uuid": "lost1", "text": "Boot order: ROM loader, then the staged image, then user code."}}]
        calls, store = self._run(records, markers,
                                 '{"ops":[{"why":"asked about boot","do":"mint","text":"Explain the boot sequence"},'
                                 '{"why":"the boot order was laid out in full","do":"done","ref":1}]}')
        self.assertTrue(any("Boot order: ROM loader" in c for c in calls),
                        "the salvaged reply is IN the planner's evidence")
        node = next((nd for nd in store["nodes"].values()
                     if "boot" in (nd.get("text") or "").lower()), None)
        self.assertIsNotNone(node)
        self.assertTrue(node.get("nodeComplete"),
                        "delivered-then-lost work files as done — the guard no longer reads it workless")

    def test_an_error_only_marker_still_reads_workless(self):
        records = [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None,
             "promptSource": "typed",
             "message": {"role": "user", "content": "explain the demo board's boot sequence"}},
            {"type": "user", "timestamp": iso(T0 + 400), "uuid": "u2", "parentUuid": "u1",
             "promptSource": "typed",
             "message": {"role": "user", "content": "unrelated: bump the version"}},
            {"type": "assistant", "timestamp": iso(T0 + 410), "uuid": "a2", "parentUuid": "u2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Bumped."}],
                         "stop_reason": "end_turn"}},
        ]
        markers = [{"t": T0 + 350, "orphanReply": {
            "uuid": "e1", "text": "API Error: 529 Overloaded. Try again in a moment."}}]
        calls, store = self._run(records, markers,
                                 '{"ops":[{"why":"answered","do":"mint","text":"Explain the boot sequence"},'
                                 '{"why":"answered fully","do":"done","ref":1}]}')
        node = next((nd for nd in store["nodes"].values()
                     if "boot" in (nd.get("text") or "").lower()), None)
        self.assertIsNotNone(node, "the ask still gets its card")
        self.assertFalse(node.get("nodeComplete"),
                         "error noise in a marker must not un-guard the fabrication hole")


FSID = "5a1e0000-2222-4333-8444-000000000c01"     # a private synthetic sid (the planner run mints goals under it)
SALVAGED = "Done. The migration ran on the staging copy and all 12 tests pass."


def _rec(t, uuid, parent, text, role="user", **extra):
    r = {"type": role, "timestamp": iso(t), "uuid": uuid, "parentUuid": parent}
    if role == "user":
        r.update(promptSource="typed", message={"role": "user", "content": text})
    else:
        r["message"] = {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}
    r.update(extra)
    return r


class FarKeptReplyHidesNoSalvage(unittest.TestCase):
    """End-to-end through the judges' parse (jd.parsed_session) and the planner: the notes-api session whose fourth ask
    was answered only in a stream an API-errored try lost, while the first ask's kept reply ("Done.") prefixes that
    stream. The chat shows the salvaged reply (nothing near its time kept it); the judges must read it too."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.leaf = self.td / (FSID + ".jsonl")
        (self.td / "states").mkdir()
        saved = (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.GOALDIR, jd.PCACHE, jd.STATESDIR = self.td / "goals", self.td / "pcache", self.td / "states"
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

        def restore():
            (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
            shutil.rmtree(self.td, ignore_errors=True)
        self.addCleanup(restore)

    def _write(self, rows, first="Done.", turn4=(), more=()):
        recs = [_rec(T0, "u1", None, "run the migration"),
                _rec(T0 + 20, "a1", "u1", first, role="assistant"),
                _rec(T0 + 300, "u2", "a1", "list the open endpoints in notes-api"),
                _rec(T0 + 320, "a2", "u2", "GET /notes, POST /notes and DELETE /notes/:id are open.", role="assistant"),
                _rec(T0 + 600, "u3", "a2", "add a rate limit to POST /notes"),
                _rec(T0 + 620, "a3", "u3", "Added a limit of ten writes per minute to POST /notes.", role="assistant"),
                _rec(T0 + 900, "u4", "a3", "rerun the migration on the staging copy and run the tests")]
        recs += list(turn4)
        recs += [_rec(T0 + 1200, "u5", recs[-1]["uuid"], "thanks, now bump the version"),
                 _rec(T0 + 1220, "a5", "u5", "Bumped to 0.4.1.", role="assistant")]
        recs += list(more)
        self.leaf.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        (self.td / "states" / (FSID + ".jsonl")).write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    def _turn4(self):
        """The judges' tree's turn holding the fourth ask: its atoms' uuids, and (uuid, salvaged, text) per reply."""
        tree = jd.parsed_session(FSID, [str(self.leaf)], NOW)
        turn = next(t for t in tree["turns"] if any(a.get("uuid") == "u4" for a in t["atoms"]))
        em.hydrate(turn["atoms"], FSID)
        return ([a.get("uuid") for a in turn["atoms"]],
                [(a.get("uuid"), bool(a.get("orphaned")), em._text_of(em._content(a.get("message"))))
                 for a in turn["atoms"] if a.get("type") == "assistant"])

    def test_the_judges_parse_holds_a_salvaged_reply_an_early_turn_prefixes(self):
        self._write([_marker(T0 + 960, "lost-4", SALVAGED)])
        uuids, replies = self._turn4()
        self.assertEqual(replies, [("lost-4", True, SALVAGED)])
        self.assertNotIn("u5", uuids, "the salvaged reply ends its turn: the next ask opens its own")

    def test_the_planner_reads_it(self):
        self._write([_marker(T0 + 960, "lost-4", SALVAGED)])
        calls = []

        def fake(text, *a, **k):
            calls.append(text)
            return '{"ops":[]}'
        jd.plan_llm = jd.opener_llm = fake
        jd._group_store = lambda *a, **k: None
        jd._plan_session(FSID, str(self.leaf), NOW)
        self.assertTrue(any("all 12 tests pass" in c for c in calls), "the salvaged reply is in the planner's evidence")

    def test_a_reply_its_own_turn_kept_still_dedups(self):
        self._write([_marker(T0 + 960, "lost-4", "Done. The migration ran on the staging copy")],
                    turn4=[_rec(T0 + 940, "a4", "u4", SALVAGED, role="assistant")])
        self.assertEqual(self._turn4()[1], [("a4", False, SALVAGED)])

    def test_the_idle_span_after_the_try_places_the_window_as_the_chat_reads_it(self):
        # the fourth try ended on the CLI's error record before the settle; the idle span from the settle to the next ask
        # stretches that turn past the marker, so the window is the third to the fifth turn, as in the tree the chat reads,
        # and the sixth turn's "Done. …" is not this reply (without the span the window slid to the fourth to the sixth)
        err = _rec(T0 + 930, "e4", "u4", "API Error: 529 Overloaded. This is a server-side issue.", role="assistant",
                   isApiErrorMessage=True)
        err["message"]["stop_reason"] = "stop_sequence"
        self._write([_marker(T0 + 960, "lost-4", "Done."), {"t": T0 + 960, "state": "waiting"},
                     {"t": T0 + 1200, "state": "working"}], first="Started the migration.", turn4=[err],
                    more=[_rec(T0 + 1500, "u6", "a5", "tag the release"),
                          _rec(T0 + 1520, "a6", "u6", "Done. Tagged v0.4.1.", role="assistant")])
        self.assertIn(("lost-4", True, "Done."), self._turn4()[1])


FORK = "66666666-7777-8888-9999-bbbbbbbbbbbb"   # the transcript a /clear opened under SID's session


def _iso_ms(t, ms):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".%03dZ" % ms


class SalvageFloorAfterAClear(unittest.TestCase):
    """2026-10-03: a /clear's leaf parses without the session's first transcript (the anchor), which no
    record of the new conversation links back into. That file was also what deduped a pre-clear salvaged
    reply whose text DID land there (landed_text_uuids over every file read), and a pre-clear reply that
    never landed leaked into every post-clear parse already. parse_session floors the markers at the first
    record of its own candidate files: a pre-clear marker is no reply of the conversation the parse
    reads. Here the parse is handed the leaf alone, the input the judge's candidates hand it for a /clear."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.anchor = self.td / (SID + ".jsonl")
        self.leaf = self.td / (FORK + ".jsonl")
        self.addCleanup(shutil.rmtree, self.td, True)

    def _write(self, p, records):
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")

    def _world(self, leaf_head_parent=None, head_ms=0):
        self._write(self.anchor, [
            {"type": "user", "timestamp": iso(T0), "uuid": "u_pre", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "how does the notes-api page its index?"}},
            {"type": "assistant", "timestamp": iso(T0 + 30), "uuid": "a_pre", "parentUuid": "u_pre",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "By cursor, fifty notes a page."}],
                         "stop_reason": "end_turn"}}])
        self._write(self.leaf, [
            {"type": "user", "timestamp": _iso_ms(T0 + 600, head_ms), "uuid": "u_post", "parentUuid": leaf_head_parent,
             "promptSource": "typed", "message": {"role": "user", "content": "now add a tags filter to the search route"}},
            {"type": "assistant", "timestamp": iso(T0 + 660), "uuid": "a_post", "parentUuid": "u_post",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Added the tags filter."}],
                         "stop_reason": "end_turn"}}])

    def _markers(self):
        return [_marker(T0 + 30, "a_pre", "By cursor, fifty notes a page, the reply as it streamed."),   # landed in the anchor
                _marker(T0 + 40, "lost_pre", "A pre-clear reply that never reached the transcript."),
                _marker(T0 + 650, "lost_post", "A post-clear reply that never reached the transcript.")]

    def _parse(self, path, files, markers):
        return em.parse_session(str(path), rompuuid=SID, candidate_files=[str(f) for f in files],
                                states=markers, postal_log=[], now=NOW)

    @staticmethod
    def _salvaged(tree):
        return [a["uuid"] for t in tree["turns"] for a in t["atoms"] if a.get("orphaned")]

    def test_a_cleared_session_synthesizes_no_pre_clear_salvaged_reply(self):
        self._world()
        tree = self._parse(self.leaf, [self.leaf], self._markers())
        self.assertEqual(self._salvaged(tree), ["lost_post"],
                         "only the post-clear salvage: the landed pre-clear reply does not come back through a parse that no "
                         "longer reads the file it landed in, and the never-landed one belongs to the cleared conversation")

    def test_a_salvaged_reply_in_the_heads_second_is_kept(self):
        # the head's stamp carries milliseconds; a marker written in that same second (stamped by the whole second) is a
        # reply of the new conversation, and losing a reply is the worse error
        self._world(head_ms=700)
        tree = self._parse(self.leaf, [self.leaf], [_marker(T0 + 600, "same_sec", "A reply salvaged in the head's own second.")])
        self.assertEqual(self._salvaged(tree), ["same_sec"])

    def test_the_floor_is_a_no_op_with_the_first_file_among_the_candidates(self):
        # a session that never cleared (its own first file is the leaf), and a fork whose head links back into the first
        # file (its history is the conversation): every marker the floor-free parse salvages, the floored one salvages too,
        # a pre-fork one included, so the floor is the EARLIEST head over the candidates, never the leaf's own
        real = em._conversation_floor
        self.addCleanup(setattr, em, "_conversation_floor", real)
        markers = [_marker(T0 + 40, "lost_pre", "A reply from before the fork that never reached the transcript."),
                   _marker(T0 + 650, "lost_post", "A reply from after the fork that never reached the transcript.")]
        for name, parent, path, files in (("never cleared", None, self.anchor, [self.anchor]),
                                          ("back-linked", "a_pre", self.leaf, [self.leaf, self.anchor])):
            self._world(leaf_head_parent=parent)
            em._conversation_floor = real
            got = self._parse(path, files, markers)
            em._conversation_floor = lambda files: None
            ref = self._parse(path, files, markers)
            em._conversation_floor = real
            self.assertEqual(self._salvaged(ref), ["lost_pre", "lost_post"], "%s: the premise, the floor-free salvage" % name)
            self.assertEqual(self._salvaged(got), self._salvaged(ref), "%s: the floor changes nothing" % name)

    def test_a_head_record_holding_an_invalid_byte_is_the_floor_the_candidate_rule_reads(self):
        # review round two: the candidate rule reads a head with bad bytes replaced (judge.transcript_head), and the floor read
        # raw bytes, so with one invalid UTF-8 byte inside the head record's text (a byte the CLI never writes) the rule left
        # the first file out while the floor skipped that record. With a record after it, the floor fell to that record's
        # stamp and dropped the first turn's salvaged reply; with none, there was no floor, and the reply that landed in the
        # first file came back as salvage. The floor decodes as the rule does, and finds the same head
        for later in (True, False):
            with self.subTest(record_after_the_head=later):
                self._world()
                raw = self.leaf.read_bytes().split(b"\n")
                self.assertIn(b"tags filter", raw[0], "premise: the head is the first line")
                raw[0] = raw[0].replace(b"tags filter", b"tags \xff filter")
                self.leaf.write_bytes(b"\n".join(raw if later else [raw[0], b""]))
                em._FILE_HEAD_T.pop(os.path.realpath(str(self.leaf)), None)
                jd._head_memo.pop(str(self.leaf), None)
                self.assertEqual(jd._judge_candidates(SID, [str(self.leaf)]), [str(self.leaf)],
                                 "premise: the rule reads that head and leaves the first file out")
                tree = self._parse(self.leaf, [self.leaf], self._markers())
                self.assertEqual(self._salvaged(tree), ["lost_post"],
                                 "floored at the head: the first turn's salvaged reply stays, nothing from before the clear")


PSID = "5a1e0000-2222-4333-8444-000000000a01"     # private synthetic sids (the goal-store fixture rule: the planner run mints)
PFORK = "5a1e0000-2222-4333-8444-000000000a02"
PRE_TEXTS = ("how does the notes-api page its index?", "By cursor, fifty notes a page",
             "A pre-clear reply that never reached the transcript.")


class NoPreClearSalvageReachesTheJudges(unittest.TestCase):
    """The judges' own road after a /clear (2026-10-03): jd.parsed_session hands the leaf alone to the parse (the anchor is
    unreachable from a parentless head), and the floor keeps every pre-clear salvaged reply out of what the planner reads,
    the one that landed in the pre-clear file included, so nothing from the cleared conversation is minted again."""

    def setUp(self):
        td = Path(tempfile.mkdtemp())
        self.td = td
        self.anchor, self.leaf = td / (PSID + ".jsonl"), td / (PFORK + ".jsonl")
        self.anchor.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": iso(T0), "uuid": "u_pre", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": PRE_TEXTS[0]}},
            {"type": "assistant", "timestamp": iso(T0 + 30), "uuid": "a_pre", "parentUuid": "u_pre",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "By cursor, fifty notes a page."}],
                         "stop_reason": "end_turn"}}]) + "\n")
        self.leaf.write_text("\n".join(json.dumps(r) for r in [
            {"type": "user", "timestamp": iso(T0 + 600), "uuid": "u_post", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "now add a tags filter to the search route"}},
            {"type": "assistant", "timestamp": iso(T0 + 660), "uuid": "a_post", "parentUuid": "u_post",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Added the tags filter."}],
                         "stop_reason": "end_turn"}}]) + "\n")
        (td / "states").mkdir()
        (td / "states" / (PSID + ".jsonl")).write_text("\n".join(json.dumps(m) for m in [
            _marker(T0 + 30, "a_pre", "By cursor, fifty notes a page, the reply as it streamed."),
            _marker(T0 + 40, "lost_pre", PRE_TEXTS[2]),
            _marker(T0 + 650, "lost_post", "A post-clear reply that never reached the transcript.")]) + "\n")
        saved = (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store)
        jd.GOALDIR, jd.PCACHE, jd.STATESDIR = td / "goals", td / "pcache", td / "states"
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()

        def restore():
            (jd.GOALDIR, jd.PCACHE, jd.STATESDIR, jd.plan_llm, jd.opener_llm, jd._group_store) = saved
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
            shutil.rmtree(td, ignore_errors=True)
        self.addCleanup(restore)

    @staticmethod
    def _texts(tree):
        return json.dumps([a.get("message") for t in tree["turns"] for a in t["atoms"]])

    def test_the_judges_parse_of_a_cleared_session_holds_no_pre_clear_salvage(self):
        texts = self._texts(jd.parsed_session(PSID, [str(self.leaf)], NOW))
        for t in PRE_TEXTS:
            self.assertNotIn(t, texts, "nothing of the cleared conversation: %r" % t)
        self.assertIn("A post-clear reply that never reached the transcript.", texts, "the post-clear salvage stands")
        self.assertEqual(jd._judge_candidates(PSID, [str(self.leaf)]), [str(self.leaf)], "the parse read the leaf alone")

    def test_no_planner_call_sees_pre_clear_text_after_a_clear(self):
        calls = []

        def fake(text, *a, **k):
            calls.append(text)
            return '{"ops":[{"why":"asked for a filter","do":"mint","text":"Add a tags filter to the search route"}]}'
        jd.plan_llm = jd.opener_llm = fake
        jd._group_store = lambda *a, **k: None
        jd._plan_session(PSID, str(self.leaf), NOW)
        store = jd.load_goals(PSID)
        self.assertTrue(any("tags filter" in c for c in calls), "premise: the planner read the post-clear conversation")
        for c in calls:
            for t in PRE_TEXTS:
                self.assertNotIn(t, c, "a planner call carried pre-clear text: %r" % t)
        self.assertFalse([nd for nd in store["nodes"].values() if nd.get("promptUuid") in ("u_pre", "a_pre", "lost_pre")],
                         "no card anchored in the cleared conversation")


RSID = "5a1e0000-2222-4333-8444-000000000b01"     # private synthetic sids: a session, its /clear's file, and the file a
RL1 = "5a1e0000-2222-4333-8444-000000000b02"      #  resume of that file forked to
RL2 = "5a1e0000-2222-4333-8444-000000000b03"
MID_TEXT = "A reply salvaged after the clear and before the resume."


class SalvageFloorOverTheResumeLineage(unittest.TestCase):
    """The salvage floor is the earliest head over the lineage CLOSURE of the parse's candidates, on the parse's side
    (parse_session) and on the chat's (jd.conversation_floor), never over the candidates alone (review round one,
    2026-10-03). After a /clear to L1 and a resume fork from L1 to L2 the candidates are L2 alone, the anchor being
    unreachable, but the parse reads L1 through the lineage: a reply salvaged between L1's head and L2's head belongs to
    the conversation it reads and stays, while one from before the clear still goes."""

    def setUp(self):
        td = Path(tempfile.mkdtemp())
        self.anchor, self.l1, self.l2 = (td / (s + ".jsonl") for s in (RSID, RL1, RL2))

        def write(p, rows):
            p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        def turn(t, u, a, ask, reply):
            return [{"type": "user", "timestamp": iso(t), "uuid": u, "parentUuid": None, "promptSource": "typed",
                     "message": {"role": "user", "content": ask}},
                    {"type": "assistant", "timestamp": iso(t + 60), "uuid": a, "parentUuid": u,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": reply}], "stop_reason": "end_turn"}}]
        write(self.anchor, turn(T0, "u_pre", "a_pre", "how does the notes-api page its index?", "By cursor."))
        write(self.l1, turn(T0 + 600, "u_l1", "a_l1", "now add a tags filter to the search route", "Added the tags filter."))
        write(self.l2, turn(T0 + 1200, "u_l2", "a_l2", "and cache the tag counts", "Cached them."))   # the resume's fresh head
        (td / "states").mkdir()
        (td / "states" / (RSID + ".jsonl")).write_text("\n".join(json.dumps(m) for m in [
            _marker(T0 + 40, "lost_pre", "A pre-clear reply that never reached the transcript."),
            _marker(T0 + 700, "lost_mid", MID_TEXT),
            {"t": T0 + 1190, "resumeFork": {"from": RL1, "to": RL2}}]) + "\n")
        saved = jd.STATESDIR
        jd.STATESDIR = td / "states"
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._RESUME_LINKS_MEMO.clear()

        def restore():
            jd.STATESDIR = saved
            jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear(); jd._RESUME_LINKS_MEMO.clear()
            shutil.rmtree(td, ignore_errors=True)
        self.addCleanup(restore)

    def test_the_judges_parse_keeps_a_reply_salvaged_between_the_clear_and_the_resume(self):
        self.assertEqual(jd._judge_candidates(RSID, [str(self.l2)]), [str(self.l2)], "premise: the anchor is unreachable")
        tree = jd.parsed_session(RSID, [str(self.l2)], NOW)
        texts = json.dumps([a.get("message") for t in tree["turns"] for a in t["atoms"]])
        self.assertIn("Added the tags filter.", texts, "premise: the parse reads L1 through the resume lineage")
        self.assertEqual([a["uuid"] for t in tree["turns"] for a in t["atoms"] if a.get("orphaned")], ["lost_mid"],
                         "floored at L1's head: the reply after the clear stays, the one before it goes")

    def test_the_chats_floor_is_the_head_of_the_clears_file(self):
        self.assertEqual(jd.conversation_floor(RSID, str(self.l2)), T0 + 600,
                         "the earliest head over the closure (L2 and L1), not L2's own")


if __name__ == "__main__":
    unittest.main()
