#!/usr/bin/env python3
"""Comment threads (the user 2026-08-13): highlight a passage in a session's chat, comment on it,
and a side conversation opens there — a popover backed by a FORK of the session cut at the anchored
message, kept OFF the board (reg threadOf, no names/ entry) until "Break out" promotes it.

Covered here: the inclusive cut-target resolution, the thread-fork's invisibility contract (no
names/, skipped by live_sessions, skipped by discover), the opening message's frame + its strip,
the transcript→popover projection, the create/reply/resolve/promote ops, and promotion's seeding
order. All fixtures SYNTHETIC: invented text, placeholder UUIDs.
"""
import contextlib
import errno
import io
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_ct", os.path.join(BIN, "romp_sdk_backend.py"))

km._limit_hold = lambda sid: None

PARENT = "11111111-2222-3333-4444-555555555555"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None, meta=False):
    r = {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
         "promptSource": "typed", "message": {"role": "user", "content": text}}
    if meta:
        r["isMeta"] = True
    return r


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def tline(t, uuid, parent):
    """A tool_use-only assistant record — a spine node that is not prose."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant",
                        "content": [{"type": "tool_use", "id": "tu_" + uuid, "name": "Read", "input": {}}]}}


def boundary(t, uuid, parent):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t),
            "uuid": uuid, "parentUuid": parent}


def cli_boundary(t, uuid, logical_parent):
    """The compact_boundary as the CLI writes it: no parentUuid, the pre-compaction leaf in logicalParentUuid."""
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": logical_parent, "content": "Conversation compacted"}


def summary_line(t, uuid, parent):
    """The compaction's summary record: a user record the CLI marks isCompactSummary (nobody said it)."""
    r = uline(t, "This session is being continued from a previous conversation. Summary: the sync loop backs off.",
              uuid, parent=parent)
    r["isCompactSummary"] = True
    return r


def tool_result_line(t, uuid, parent, tool_uuid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + tool_uuid,
                                                      "content": "the file's contents"}]}}


def compacted_records(t):
    """A notes-api session compacted once: p1..a3 before the boundary, the summary and u4..a4 after it. The passage
    a2 is the agent's answer, reached through a tool call, with the person's question before it and their follow-up
    after it."""
    return [uline(t, "Set up the notes-api sync loop.", "p1"),
            aline(t + 5, "Done: the loop polls the server every thirty seconds.", "pa1", parent="p1"),
            uline(t + 60, "How should it back off when the server is down?", "p2", parent="pa1"),
            tline(t + 62, "pt2", "p2"),
            tool_result_line(t + 63, "pr2", "pt2", "pt2"),
            aline(t + 65, "Use exponential backoff with a jitter of ten percent.", "pa2", parent="pr2"),
            uline(t + 120, "and the cap?", "p3", parent="pa2"),
            aline(t + 125, "Cap the delay at two minutes.", "pa3", parent="p3"),
            cli_boundary(t + 200, "pb1", "pa3"),
            summary_line(t + 201, "ps1", "pb1"),
            uline(t + 300, "now wire the dashboard", "p4", parent="ps1"),
            aline(t + 305, "The dashboard shows each endpoint's retry state.", "pa4", parent="p4")]


def attline(t, prompt, uuid, parent, **fields):
    """A message the person sent while the agent was working, spliced into the running turn, as the CLI records it: no
    user record but a queued_command attachment stamped with the send time, uuid-bearing and parent-chained
    (tests/test_event_model_golden.py's attline). `fields` go on the attachment (commandMode, origin)."""
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
            "attachment": dict({"type": "queued_command", "prompt": prompt}, **fields)}


def other_attline(t, uuid, parent, **attachment):
    """An attachment of another kind on the same chain, its fields `attachment`: for example the token-count reminder
    the CLI writes at a request's start (tests/test_event_model_golden.py's reminder_line), or a hook's result after a
    tool's."""
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
            "attachment": attachment}


CORRECTION = "Keep the jitter under five percent, please."
# how the relay opens each message (kernel _merge_body: the record is tagged, the user 2026-10-06), and closes one
ME, THEM, END = '<message from="me">\n', '<message from="the other assistant">\n', "\n</message>"
WHOLE_START = "<discussion>\n<message"   # a relay missing nothing opens its discussion on the first message


def spliced_records(t, prompt=CORRECTION, **fields):
    """compacted_records' session with a message sent mid-turn: the agent's answer pa2 comes before the tool call it goes
    on to make, the person's correction q1 is spliced in after that call's result (stamped with its send time, before the
    result landed), the agent's reply pa3 answers it, and the person's typed follow-up p3 comes after that (so the later
    replies are pa4 and pa5)."""
    return [uline(t, "Set up the notes-api sync loop.", "p1"),
            aline(t + 5, "Done: the loop polls the server every thirty seconds.", "pa1", parent="p1"),
            uline(t + 60, "How should it back off when the server is down?", "p2", parent="pa1"),
            aline(t + 65, "Use exponential backoff with a jitter of ten percent.", "pa2", parent="p2"),
            tline(t + 66, "pt2", "pa2"),
            tool_result_line(t + 70, "pr2", "pt2", "pt2"),
            attline(t + 68, prompt, "q1", "pr2", **fields),
            aline(t + 75, "Understood: five percent it is.", "pa3", parent="q1"),
            uline(t + 120, "and the cap?", "p3", parent="pa3"),
            aline(t + 125, "Cap the delay at two minutes.", "pa4", parent="p3"),
            cli_boundary(t + 200, "pb1", "pa4"),
            summary_line(t + 201, "ps1", "pb1"),
            uline(t + 300, "now wire the dashboard", "p4", parent="ps1"),
            aline(t + 305, "The dashboard shows each endpoint's retry state.", "pa5", parent="p4")]


def forget_parses():
    """Every parse of a transcript these tests rewrite in place under one path (the reader's records, the assembly entries,
    the parse slots), so the next resolve parses the file as it now stands."""
    with em._JSONL_CACHE_LOCK:
        em._JSONL_CACHE.clear()
    with em._ASM_LOCK:
        em._ASM_CACHE.clear()
    jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
    km._parse_cache.clear()


class CommentBase(unittest.TestCase):
    """Temp state root shared by kernel + judge (km.jd IS the loaded jd module), synthetic
    transcripts under a temp projects/ dir — the test_sdk_clear_fork conventions."""

    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_msgs_cache.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        # never build the real SDK backend in here — the frame reads state "" from a None backend
        self._saved_sdk = km._sdk
        km._sdk = lambda: None

    def tearDown(self):
        km._sdk = self._saved_sdk
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, stem, records):
        p = self.proj / (stem + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return p

    def _parent_records(self):
        t = self.now - 600
        return [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 60, "and the cap?", "u2", parent="a1"),
                aline(t + 65, "Cap the delay at two minutes.", "a2", parent="u2")]


# ── the cut target: inclusive, guarded ────────────────────────────────────────────────────────────

class CutTarget(CommentBase):
    def test_an_assistant_anchor_cuts_at_itself_not_its_ancestor(self):
        p = self._write(PARENT, self._parent_records())
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(err)
        self.assertEqual(cut, "a1", "the thread must HOLD the highlighted answer — inclusive cut")
        self.assertGreater(cut_t, 0, "the cut record's own time rides along for the timeline square")

    def test_a_tool_row_anchor_falls_to_its_nearest_prose_ancestor(self):
        # a tool_use record is type "assistant" but NOT a clean cut — including it would leave the
        # fork's history ending on a dangling tool call; the nearest prose record carries the anchor
        t = self.now - 300
        recs = self._parent_records() + [tline(t, "tu9", "a2")]
        p = self._write(PARENT, recs)
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "tu9")
        self.assertIsNone(err)
        self.assertEqual(cut, "a2")

    def test_a_pre_compaction_anchor_opens_a_tip_fork(self):
        # the user 2026-09-30: this used to be refused ("predates the last context compaction"). The CLI still cannot
        # cut there, so the thread takes the whole conversation (the restart-seam precedent) instead of an error
        t = self.now - 600
        recs = [uline(t, "old ask", "u1"),
                aline(t + 5, "old answer", "a1", parent="u1"),
                boundary(t + 100, "b1", "a1"),
                uline(t + 200, "fresh ask", "u2", parent="b1")]
        p = self._write(PARENT, recs)
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(err)
        self.assertEqual(cut, "", "a tip fork: the CLI loads only post-boundary records")
        self.assertEqual(cut_t, int(em.parse_z(iso(t + 5))), "the anchor's own time stamps the row (the square lands on it)")

    def test_an_unknown_anchor_is_refused(self):
        p = self._write(PARENT, self._parent_records())
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "nope")
        self.assertIsNone(cut)
        self.assertTrue(err)

    def _seamed_session(self):
        """A machine-cut resume that forked fresh-headed: old records in the resumed-from file,
        new ones in the current file, joined only by the states resumeFork lineage row — the shape
        that read every pre-seam message as 'not in the transcript' (the user 2026-08-15)."""
        old_fsid, new_fsid = PARENT, "cccccccc-dddd-eeee-ffff-000000000000"
        t = self.now - 900
        self._write(old_fsid, [uline(t, "the pre-seam ask", "u1"),
                               aline(t + 5, "the pre-seam answer, the one worth a comment", "a1", parent="u1")])
        p = self._write(new_fsid, [uline(t + 300, "the post-seam ask", "u9", parent=None),
                                   aline(t + 305, "the post-seam answer", "a9", parent="u9")])
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (PARENT + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": old_fsid, "to": new_fsid}, "t": t + 250}) + "\n")
        return p

    def test_a_pre_seam_anchor_is_found_and_falls_back_to_a_tip_fork(self):
        p = self._seamed_session()
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a1")
        self.assertIsNone(err, "the stitched chain must FIND the message the chat shows")
        self.assertEqual(cut, "", "behind the seam the CLI can't address it — tip fork instead")
        self.assertGreater(cut_t, 0, "the anchor's own time still stamps the row")

    def test_a_post_seam_anchor_still_cuts_at_itself(self):
        p = self._seamed_session()
        cut, cut_t, err = km._comment_cut_target(str(p), PARENT, "a9")
        self.assertIsNone(err)
        self.assertEqual(cut, "a9")

    def test_rewind_names_the_seam_instead_of_denying_the_message_exists(self):
        p = self._seamed_session()
        cut, err = km._rewind_target(str(p), PARENT, "u1")
        self.assertIsNone(cut)
        self.assertIn("restart seam", err)


class PreCompactionContext(CommentBase):
    """A thread on a passage from BEFORE the last compaction (the user 2026-09-30): a tip fork, whose opening message
    carries what the compaction summarized away around the passage: the nearest thing the other side said before the
    passage's turn and after it (capped, labelled in the person's voice) and where the transcript is saved."""

    def _resolve(self, anchor, records=None):
        p = self._write(PARENT, records or compacted_records(self.now - 900))
        return p, km._comment_cut_resolve(str(p), PARENT, anchor)

    def test_the_agents_answer_is_framed_by_the_persons_question_and_follow_up(self):
        p, (cut, cut_t, err, prior) = self._resolve("pa2")
        self.assertIsNone(err)
        self.assertEqual(cut, "", "a tip fork")
        self.assertEqual(cut_t, int(em.parse_z(iso(self.now - 900 + 65))), "the passage's own time")
        # the tool call and its result between the question and the answer are the answer's own turn, never quoted
        self.assertEqual(prior, km._PRECOMPACT_LEAD + " Right before it, I said:\n\n"
                         "> How should it back off when the server is down?\n\n"
                         "Right after it, I said:\n\n> and the cap?\n\n"
                         + km._PRECOMPACT_FILE_LEAD + os.path.abspath(str(p))
                         + " (this passage is the entry with uuid pa2) if you need more.")

    def test_the_persons_message_is_framed_by_what_the_agent_said(self):
        _, (cut, _t, err, prior) = self._resolve("p2")
        self.assertIsNone(err)
        self.assertIn(km._PRECOMPACT_LEAD + " Right before it, you said:\n\n"
                      "> Done: the loop polls the server every thirty seconds.\n\n", prior)
        self.assertIn("Right after it, you said:\n\n> Use exponential backoff with a jitter of ten percent.\n\n", prior,
                      "past the tool traffic to the reply itself")

    def test_the_first_message_has_nothing_before_it(self):
        _, (_c, _t, err, prior) = self._resolve("p1")
        self.assertIsNone(err)
        self.assertNotIn("Right before it", prior)
        self.assertTrue(prior.startswith(km._PRECOMPACT_LEAD + " Right after it, you said:\n\n"
                                         "> Done: the loop polls the server every thirty seconds.\n\n"), prior)

    def test_the_last_message_has_nothing_after_it(self):
        t = self.now - 900
        recs = compacted_records(t)[:8] + [cli_boundary(t + 200, "pb1", "pa3"), summary_line(t + 201, "ps1", "pb1")]
        _, (cut, _t, err, prior) = self._resolve("pa3", recs)
        self.assertIsNone(err)
        self.assertEqual(cut, "")
        self.assertIn("Right before it, I said:\n\n> and the cap?\n\n", prior)
        self.assertNotIn("Right after it", prior, "the compaction's summary is nobody talking")
        self.assertIn("\n\n" + km._PRECOMPACT_FILE_LEAD, prior)

    def test_neither_side_leaves_the_lead_and_the_pointer(self):
        t = self.now - 900
        recs = [aline(t, "A note the agent left before anything was asked.", "n1"),
                cli_boundary(t + 100, "nb1", "n1"), summary_line(t + 101, "ns1", "nb1")]
        p, (cut, _t, err, prior) = self._resolve("n1", recs)
        self.assertIsNone(err)
        self.assertEqual(prior, km._PRECOMPACT_LEAD + "\n\n" + km._PRECOMPACT_FILE_LEAD + os.path.abspath(str(p))
                         + " (this passage is the entry with uuid n1) if you need more.",
                         "no Right before / Right after sentences: the summary sentence and the file line")

    def test_excerpts_are_capped_on_a_word_boundary_tail_before_head_after(self):
        t = self.now - 900
        long_q = " ".join("question%d" % i for i in range(400))          # ~4,400 characters
        long_f = " ".join("followup%d" % i for i in range(400))
        recs = [uline(t, long_q, "q1"), aline(t + 5, "Use exponential backoff.", "qa1", parent="q1"),
                uline(t + 60, long_f, "q2", parent="qa1"), aline(t + 65, "Two minutes.", "qa2", parent="q2"),
                cli_boundary(t + 100, "qb1", "qa2"), summary_line(t + 101, "qs1", "qb1")]
        _, (_c, _t, err, prior) = self._resolve("qa1", recs)
        self.assertIsNone(err)
        paras = prior.split("\n\n")
        before, after = paras[1], paras[3]
        cap = km._PRECOMPACT_EXCERPT_CAP
        self.assertTrue(before.startswith("> …question"), "the TAIL of what came before, from a whole word")
        self.assertTrue(before.endswith("question399"), "…running right up to the passage")
        self.assertLessEqual(len(before) - 2, cap + 1)
        self.assertTrue(after.startswith("> followup0 "), "the HEAD of what came after")
        self.assertTrue(after.endswith("…") and not after[:-1].endswith(" "), "cut at a word boundary, ellipsis")
        self.assertRegex(after[:-1], r"followup\d+$", "a whole word before the ellipsis")
        self.assertLessEqual(len(after) - 2, cap + 1)

    def test_a_passage_behind_a_restart_seam_points_at_its_own_file(self):
        old_fsid, new_fsid = PARENT, "cccccccc-dddd-eeee-ffff-000000000000"
        t = self.now - 900
        old = self._write(old_fsid, compacted_records(t)[:8])            # the passage lives in the resumed-from file
        cur = self._write(new_fsid, [uline(t + 300, "the post-seam ask", "u9", parent=None),
                                     aline(t + 305, "the post-seam answer", "a9", parent="u9"),
                                     cli_boundary(t + 400, "b9", "a9"), summary_line(t + 401, "s9", "b9"),
                                     uline(t + 500, "keep going", "u10", parent="s9")])
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (PARENT + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": old_fsid, "to": new_fsid}, "t": t + 250}) + "\n")
        cut, cut_t, err, prior = km._comment_cut_resolve(str(cur), PARENT, "pa2")
        self.assertIsNone(err)
        self.assertEqual(cut, "")
        self.assertIn(km._PRECOMPACT_FILE_LEAD + os.path.abspath(str(old)) + " (this passage", prior,
                      "the file that holds the passage's record, not the current one")
        self.assertNotIn(new_fsid, prior)

    def test_a_post_compaction_anchor_is_unchanged(self):
        _, (cut, _t, err, prior) = self._resolve("pa4")
        self.assertIsNone(err)
        self.assertEqual(cut, "pa4", "a cut fork at the passage, as ever")
        self.assertEqual(prior, "", "nothing extra rides its opening message")

    def test_a_restart_seam_anchor_with_no_compaction_carries_nothing_extra(self):
        old_fsid, new_fsid = PARENT, "cccccccc-dddd-eeee-ffff-000000000000"
        t = self.now - 900
        self._write(old_fsid, [uline(t, "the pre-seam ask", "u1"), aline(t + 5, "the pre-seam answer", "a1", parent="u1")])
        cur = self._write(new_fsid, [uline(t + 300, "the post-seam ask", "u9", parent=None)])
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (PARENT + ".jsonl")).write_text(
            json.dumps({"resumeFork": {"from": old_fsid, "to": new_fsid}, "t": t + 250}) + "\n")
        self.assertEqual(km._comment_cut_resolve(str(cur), PARENT, "a1")[1:], (int(em.parse_z(iso(t + 5))), None, ""))

    # ── a message the person sent while the agent was working (the review of #37) ─────────────────────────────────
    # The CLI records a message spliced into the running turn as a queued_command attachment. Whether an attachment
    # is such a message (and the original, not a copy) and who sent it is the parse's atom for it (the display parse's;
    # on the fresh read, the same emit run over that read); when that atom names the person, its words go through a user
    # record's screens. Each case below is resolved through the create's road (the chat's parse, a whole entry here) and
    # checked against a fresh read of the transcript, the walk's other road; tests/test_comment_create_fast.py runs
    # several of them on restored entries too. PROMPTS holds the prompt as a string and as a content-block list, two
    # text blocks around an image that the chat joins with a space (em._text_of), as the excerpt does.
    PROMPTS = (CORRECTION, [{"type": "text", "text": "Keep the jitter"},
                            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
                            {"type": "text", "text": "under five percent, please."}])

    def _spliced(self, anchor, prompt=CORRECTION, records=None, **fields):
        p = self._write(PARENT, records or spliced_records(self.now - 900, prompt, **fields))
        forget_parses()                                   # an earlier subtest's parse of the same path
        roads = dict(km._CUT_ROADS)
        got = km._comment_cut_resolve(str(p), PARENT, anchor)
        self.assertEqual(km._CUT_ROADS["whole"] - roads["whole"], 1, "premise: the chat's parse answered, a whole entry")
        self.assertEqual(got, km._comment_cut_walk(em.TranscriptGraph(km._anchor_adapter(str(p), PARENT)), str(p), anchor),
                         "a fresh read of the transcript answers the same")
        return got

    def _shown(self, records, text):
        """How many bubbles the chat shows with `text` for messages sent mid-turn: the parse's absorbed atoms."""
        p = self._write(PARENT, records)
        return [em._text_of(a["message"]["content"]) for a in em.FileAdapter([str(p)], str(p)).atoms(PARENT, {})
                if a.get("absorbed")].count(text)

    def test_the_agents_answer_before_a_message_sent_while_it_worked(self):
        for prompt in self.PROMPTS:
            with self.subTest(prompt=type(prompt).__name__):
                cut, _t, err, prior = self._spliced("pa2", prompt)
                self.assertIsNone(err)
                self.assertEqual(cut, "")
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n"
                              "> How should it back off when the server is down?\n\n", prior)
                self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior,
                              "the correction sent mid-turn, not the typed follow-up after the agent's reply to it")

    def test_a_comment_on_a_message_sent_mid_turn(self):
        for prompt in self.PROMPTS:
            with self.subTest(prompt=type(prompt).__name__):
                cut, _t, err, prior = self._spliced("q1", prompt)
                self.assertIsNone(err)
                self.assertEqual(cut, "")
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, you said:\n\n"
                              "> Use exponential backoff with a jitter of ten percent.\n\n", prior,
                              "the passage is the person's, so the excerpts are the agent's")
                self.assertIn("Right after it, you said:\n\n> Understood: five percent it is.\n\n", prior,
                              "the agent's reply to the correction")

    def test_the_agents_reply_to_a_message_sent_mid_turn(self):
        for prompt in self.PROMPTS:
            with self.subTest(prompt=type(prompt).__name__):
                cut, _t, err, prior = self._spliced("pa3", prompt)
                self.assertIsNone(err)
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n> " + CORRECTION + "\n\n", prior,
                              "the correction it answers, not the person's question before the agent's first answer")
                self.assertIn("Right after it, I said:\n\n> and the cap?\n\n", prior)

    def test_a_message_sent_mid_turn_keeps_its_line_breaks(self):
        # each line of the message is quoted as a line of its own, the blank one included, in both prompt forms. Red
        # before the fix (no splice was read), and red if the walk reads the words with their whitespace collapsed
        lines = "Keep the jitter under five percent, please.\n\nAnd:\n- log each retry"
        quoted = "> Keep the jitter under five percent, please.\n> \n> And:\n> - log each retry\n\n"
        for prompt in (lines, [{"type": "text", "text": lines}]):
            for anchor, label in (("pa2", "Right after it, I said:\n\n"), ("pa3", "Right before it, I said:\n\n")):
                with self.subTest(prompt=type(prompt).__name__, anchor=anchor):
                    _c, _t, err, prior = self._spliced(anchor, prompt)
                    self.assertIsNone(err)
                    self.assertIn(label + quoted, prior)

    def test_two_messages_sent_at_one_tool_boundary(self):
        # two messages the person sends here while one tool runs, which the CLI splices in at that tool's boundary, one
        # attachment chained after the other before the agent's reply: a comment on either is framed by the agent's text,
        # its answer before the pair and its reply after it, so each walk passes over the other message on the passage's
        # own side. The agent's answer is followed by the first message, and its reply preceded by the second. Red before
        # the fix (no splice was read), and red if a walk stops at a message on the passage's own side
        t = self.now - 900
        second = "And log each retry, please."
        recs = spliced_records(t)
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "pa3")
        recs[at] = dict(recs[at], parentUuid="q2")
        recs.insert(at, attline(t + 69, second, "q2", "q1"))
        self.assertEqual([self._shown(recs, w) for w in (CORRECTION, second)], [1, 1],
                         "premise: the chat shows a bubble for each message")
        said = lambda who, text: "%s said:\n\n> %s\n\n" % (who, text)
        answer = said("you", "Use exponential backoff with a jitter of ten percent.")
        reply = said("you", "Understood: five percent it is.")
        for anchor, before, after in (
                ("q1", answer, reply), ("q2", answer, reply),
                ("pa2", said("I", "How should it back off when the server is down?"), said("I", CORRECTION)),
                ("pa3", said("I", second), said("I", "and the cap?"))):
            with self.subTest(anchor=anchor):
                _c, _t, err, prior = self._spliced(anchor, records=recs)
                self.assertIsNone(err)
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, " + before, prior)
                self.assertIn("Right after it, " + after, prior)

    def test_a_task_notification_spliced_mid_turn_is_nobody(self):
        # the excluded case: each row here stamped as a task notification (the attachment's older commandMode spelling,
        # or its origin field) is the harness talking, by the stamp alone, for both texts below; one stamped as another
        # session's (origin kind peer) is nobody either; and so is one with no stamp whose prompt is wholly the
        # notification block, which the event model files as the system's by its text. The person's next words after
        # the passage are still their typed follow-up. A guard: before the fix no splice was read at all. Three checks
        # refuse each block row: the atom's author, the reminder peel (which takes the whole block, leaving no words)
        # and the harness-wrapper screen, so a block row stays green while any one of them holds; the plain rows are
        # refused by the author alone
        note = ("<task-notification>\n<task-id>b1</task-id>\n<status>completed</status>\n"
                "<summary>The notes-api build finished.</summary>\n</task-notification>")
        plain = "The notes-api build finished."
        for shape, prompt, fields in (("block", note, {"commandMode": "task-notification"}),
                                      ("block", note, {"origin": {"kind": "task-notification"}}),
                                      ("plain", plain, {"commandMode": "task-notification"}),
                                      ("plain", plain, {"origin": {"kind": "task-notification"}}),
                                      ("plain", plain, {"origin": {"kind": "peer", "from": "web"}}),
                                      ("block", note, {"commandMode": "prompt"}),
                                      ("block list", [{"type": "text", "text": note}], {"commandMode": "prompt"})):
            with self.subTest(fields=fields, prompt=shape):
                _c, _t, err, prior = self._spliced("pa2", prompt, **fields)
                self.assertIsNone(err)
                self.assertIn("Right after it, I said:\n\n> and the cap?\n\n", prior)
                self.assertNotIn("build finished", prior)

    def test_the_screens_on_a_message_sent_mid_turn(self):
        # when its atom names the person, its words go through a user record's screens, in the excerpt after the agent's
        # answer before it (pa2) and in the one before the agent's reply (pa3): a reminder the harness appended is
        # peeled off (red before the fix, which read no splice). A slash command's wrapper is dropped by its screen
        # though the atom names the person: this row pins that residual as it stands (red when it is closed, and red if
        # a splice skips that screen). A harness banner is nobody (a guard), refused twice: by the atom's author and by
        # the harness-wrapper screen, so the row stays green without either one
        ask = "How should it back off when the server is down?"
        for name, prompt, after, before in (
                ("trailing reminder", CORRECTION + "\n\n<system-reminder>Be brief.</system-reminder>", CORRECTION,
                 CORRECTION),
                ("slash command", "<command-name>/compact</command-name>", "and the cap?", ask),
                ("harness banner", "[SYSTEM NOTIFICATION - NOT USER INPUT] A background task finished.", "and the cap?",
                 ask)):
            for anchor, said in (("pa2", "Right after it, I said:\n\n> " + after),
                                 ("pa3", "Right before it, I said:\n\n> " + before)):
                with self.subTest(name, anchor=anchor):
                    if name == "slash command":
                        self.assertEqual(em.author_of([{"type": "text", "text": prompt}], None, {}, False, None),
                                         "human", "premise: the atom names the person")
                    _c, _t, err, prior = self._spliced(anchor, prompt)
                    self.assertIsNone(err)
                    self.assertIn(said + "\n\n", prior)
                    for gone in ("Be brief", "background task", "/compact"):
                        self.assertNotIn(gone, prior)

    def test_a_splice_stamped_as_the_persons_own(self):
        # an origin of kind human is the person's own stamp, no injected one: the splice is still their correction
        _c, _t, err, prior = self._spliced("pa2", origin={"kind": "human"})
        self.assertIsNone(err)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior)

    def test_a_splice_the_chat_does_not_show_as_the_persons_is_nobody(self):
        # romp's own mid-turn feeds and the harness's arrive as the same attachment, and the atom's author says they
        # are not the person's for each row below. Without the author, the text screens alone (_comment_persons_words)
        # would pass six of them (`alone` below, a premise): the mail and romp's injection each behind a reminder tag
        # left open (the reminder split cuts the text at the open tag, and the marker with it), romp's marker written
        # without its spaces (the marker screen reads the one spelling romp writes), the teammate's message, the
        # scheduled prompt, and the words after the reminder once it is peeled.
        # The premise on the author is only that it is not the person's; the comments over the rows say whose the
        # event model files each as today, which the walk does not depend on
        marker = "<!-- romp-msg-id: 11111111-2222-3333-4444-666666666666 -->"
        for name, prompt, alone in (
                # filed as a peer's
                ("peer mail", "The tags route is done on my side.\n\n" + marker, False),
                ("peer mail behind an open reminder",
                 "The tags route is done on my side.\n\n<system-reminder>Be brief.\n\n" + marker, True),
                # filed as romp's
                ("romp's injection", "<!-- romp-injected -->\nWhere does the tags route stand?", False),
                ("romp's injection behind an open reminder",
                 "Where does the tags route stand?\n\n<system-reminder>Be brief.\n\n<!-- romp-injected -->", True),
                ("romp's marker without its spaces", "<!--romp-injected-->\nWhere does the tags route stand?", True),
                # filed as a teammate's, the SDK's and the system's, in that order
                ("teammate", "Another Claude session sent a message while you were working: the tags route is done.",
                 True),
                ("scheduled prompt", "[SCHEDULED TASK - AUTOMATED FIRING OF A CONFIGURED PROMPT] Check the tags route.",
                 True),
                ("after a harness reminder", "<system-reminder>Context for the model.</system-reminder>\n"
                 "The tags route is done.", True)):
            with self.subTest(name):
                got = em.author_of([{"type": "text", "text": prompt}], None, {}, False, None)
                self.assertNotEqual(got, "human", "premise: the chat does not show the text as the person's")
                self.assertEqual(km._comment_persons_words(prompt, lambda t: False) is not None, alone,
                                 "premise: whether the text screens alone would pass it")
                _c, _t, err, prior = self._spliced("pa2", prompt)
                self.assertIsNone(err)
                self.assertIn("Right after it, I said:\n\n> and the cap?\n\n", prior)
                self.assertNotIn("tags route", prior)

    def test_a_reply_from_a_card_sent_mid_turn_is_dropped_as_it_stands(self):
        # pins a residual as it stands (_comment_spliced): a reply the person sends from a card, and the card's Continue
        # button, carry romp's goal marker (the Continue reply its canned marker too); the chat shows them as the
        # person's, and the marker screen drops them from the excerpt after the agent's answer before them (pa2) and from
        # the one before the agent's reply (pa3). Red when that residual is closed, and red if a splice skips the marker
        # screen. The third case here, a message with no stamp, no marker and no harness wording, reads as the person's
        # at both anchors, as the chat reads it (red before the fix, which read no splice)
        card = km._followup_body(PARENT + ":g1", "Retry loop: back off with jitter", CORRECTION)
        cont = km._followup_body(PARENT + ":g1", "Retry loop: back off with jitter",
                                 km.CONTINUE_TEXT + "\n\n<!-- romp-canned: continue -->")
        plain = "The tags route is done; the cap is two minutes."
        ask = "How should it back off when the server is down?"
        for name, prompt, after, before in (("card reply", card, "and the cap?", ask),
                                            ("Continue", cont, "and the cap?", ask),
                                            ("no sign it came from elsewhere", plain, plain, plain)):
            for anchor, said in (("pa2", "Right after it, I said:\n\n> " + after),
                                 ("pa3", "Right before it, I said:\n\n> " + before)):
                with self.subTest(name, anchor=anchor):
                    self.assertEqual(em.author_of([{"type": "text", "text": prompt}], None, {}, False, None), "human",
                                     "premise: the chat shows it as the person's")
                    _c, _t, err, prior = self._spliced(anchor, prompt)
                    self.assertIsNone(err)
                    self.assertIn(said + "\n\n", prior)
                    if prompt != plain:
                        self.assertNotIn("Retry loop", prior)

    def test_a_comment_on_a_message_the_screens_drop_quotes_the_person_on_both_sides(self):
        # pins a residual as it stands: a card reply, a Continue reply and a slash command's wrapper, each sent mid-turn
        # and named the person's by its atom, are dropped by the screens, so a comment on one takes the passage as the
        # agent's and both excerpts quote the person. Red when that is closed
        card = km._followup_body(PARENT + ":g1", "Retry loop: back off with jitter", CORRECTION)
        cont = km._followup_body(PARENT + ":g1", "Retry loop: back off with jitter",
                                 km.CONTINUE_TEXT + "\n\n<!-- romp-canned: continue -->")
        for name, prompt in (("card reply", card), ("Continue", cont),
                             ("slash command", "<command-name>/compact</command-name>")):
            with self.subTest(name):
                self.assertEqual(em.author_of([{"type": "text", "text": prompt}], None, {}, False, None), "human",
                                 "premise: the atom names the person")
                _c, _t, err, prior = self._spliced("q1", prompt)
                self.assertIsNone(err)
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n"
                              "> How should it back off when the server is down?\n\n", prior)
                self.assertIn("Right after it, I said:\n\n> and the cap?\n\n", prior)

    def test_a_replayed_copy_of_the_message_past_the_boundary(self):
        # a compaction or a resume writes the splice again under a new uuid with its send stamp and text. Here the chat
        # shows the message once (a premise) whether the copy is written verbatim, with its whitespace changed or as a
        # content-block list, and past the boundary the walk passes over the copy. Guards (no splice was read before the
        # fix); red if the walk reads a copy
        t = self.now - 900
        for shape, copy in (("verbatim", CORRECTION), ("whitespace", "Keep the jitter  under five percent,\nplease."),
                            ("list", [{"type": "text", "text": CORRECTION}])):
            with self.subTest(copy=shape):
                recs = spliced_records(t)
                at = next(i for i, r in enumerate(recs) if r["uuid"] == "p4")
                recs[at] = dict(recs[at], parentUuid="q1r")
                recs.insert(at, attline(t + 68, copy, "q1r", "ps1"))
                self.assertEqual(self._shown(recs, CORRECTION), 1, "premise: the chat shows the message once")
                _c, _t, err, prior = self._spliced("pa4", records=recs)
                self.assertIsNone(err)
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n> and the cap?\n\n", prior)
                self.assertIn("Right after it, I said:\n\n> now wire the dashboard\n\n", prior)
                self.assertNotIn("Keep the jitter", prior)

    def test_three_copies_of_the_message(self):
        # the original and two copies, one after each of two compactions. Here the chat shows the message once (a
        # premise), the agent's reply quotes the original before it (red before the fix), and the passage before each
        # compaction is followed by the person's next typed message, not a copy (guards)
        t = self.now - 900
        recs = spliced_records(t)[:12] + [
            attline(t + 68, CORRECTION, "q1r", "ps1"),
            uline(t + 300, "now wire the dashboard", "p4", parent="q1r"),
            aline(t + 305, "The dashboard shows each endpoint's retry state.", "pa5", parent="p4"),
            cli_boundary(t + 400, "pb2", "pa5"),
            summary_line(t + 401, "ps2", "pb2"),
            attline(t + 68, CORRECTION, "q1s", "ps2"),
            uline(t + 500, "next, the alerts", "p6", parent="q1s"),
            aline(t + 505, "The alerts fire after three failed retries.", "pa6", parent="p6")]
        self.assertEqual(self._shown(recs, CORRECTION), 1, "premise: the chat shows the message once")
        for anchor, said in (("pa3", "Right before it, I said:\n\n> " + CORRECTION + "\n\n"),
                             ("pa4", "Right after it, I said:\n\n> now wire the dashboard\n\n"),
                             ("pa5", "Right after it, I said:\n\n> next, the alerts\n\n")):
            with self.subTest(anchor=anchor):
                _c, _t, err, prior = self._spliced(anchor, records=recs)
                self.assertIsNone(err)
                self.assertIn(said, prior)
                self.assertLessEqual(prior.count("Keep the jitter"), 1)
                if anchor != "pa3":
                    self.assertNotIn("Keep the jitter", prior)

    def test_a_replayed_copy_before_the_passage(self):
        # after a second compaction, a passage the agent wrote after the first compaction's copy of the message. Here the
        # chat shows the message once (a premise), and the passage is preceded by the person's last typed message, not by
        # the copy. A guard; red if the walk back reads a copy
        t = self.now - 900
        recs = spliced_records(t)[:12] + [
            attline(t + 68, CORRECTION, "q1r", "ps1"),
            aline(t + 230, "Noted: the jitter stays under five percent.", "pa6", parent="q1r"),
            uline(t + 260, "ship it", "p7", parent="pa6"),
            aline(t + 265, "Shipped.", "pa7", parent="p7"),
            cli_boundary(t + 400, "pb2", "pa7"),
            summary_line(t + 401, "ps2", "pb2"),
            uline(t + 500, "next, the alerts", "p8", parent="ps2"),
            aline(t + 505, "The alerts fire after three failed retries.", "pa8", parent="p8")]
        self.assertEqual(self._shown(recs, CORRECTION), 1, "premise: the chat shows the message once")
        _c, _t, err, prior = self._spliced("pa6", records=recs)
        self.assertIsNone(err)
        self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n> and the cap?\n\n", prior)
        self.assertIn("Right after it, I said:\n\n> ship it\n\n", prior)
        self.assertNotIn(CORRECTION, prior)

    def test_the_message_is_its_copy_on_the_kept_chain_when_the_first_is_rewound_away(self):
        # the same send written twice, the first copy on a branch the person rewound away and its twin on the kept chain:
        # the parse makes no atom of a copy off the kept chain, so the twin is the message the chat shows, and the walk
        # quotes it on both roads it runs here. Red before the fix (no splice was read), and red on the fresh read's
        # road if its choice ignored the kept chain
        t = self.now - 900
        recs = spliced_records(t)
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "q1")
        recs[at:at] = [attline(t + 68, CORRECTION, "q1x", "pr2"),
                       aline(t + 72, "An answer the person rewound away.", "pa3x", parent="q1x")]
        p = self._write(PARENT, recs)
        ad = em.FileAdapter([str(p)], str(p))
        self.assertEqual([a["uuid"] for a in ad.atoms(PARENT, {}) if a.get("absorbed")], ["q1"],
                         "premise: the chat shows the kept twin, once")
        self.assertNotIn("q1x", ad.kept_uuids(ad.active_path()), "premise: the first copy is off the kept chain")
        _c, _t, err, prior = self._spliced("pa2", records=recs)
        self.assertIsNone(err)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior)
        self.assertNotIn("rewound away", prior)

    def test_a_message_first_written_off_the_spine_is_skipped_as_it_stands(self):
        # pins a residual as it stands: the send's first record sits on a chain the parse keeps off the spine (here a
        # fragment whose parent is in no transcript), and a copy of it, same stamp and text under a new uuid, sits on
        # the spine. The chat's atom is the fragment's record. The walk follows the spine, where the copy has no atom,
        # so both excerpts skip the message. Red when that is closed, and red on the fresh read's road if its choice of
        # the original took the spine alone for the kept chain
        t = self.now - 900
        recs = spliced_records(t)
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "q1")
        recs[at] = dict(recs[at], parentUuid="lost0")      # a parent in no transcript
        recs.insert(at + 1, attline(t + 68, CORRECTION, "q1c", "pr2"))
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "pa3")
        recs[at] = dict(recs[at], parentUuid="q1c")
        p = self._write(PARENT, recs)
        ad = em.FileAdapter([str(p)], str(p))
        active = ad.active_path()
        self.assertEqual([a["uuid"] for a in ad.atoms(PARENT, {}) if a.get("absorbed")], ["q1"],
                         "premise: the chat shows the fragment's record, once")
        self.assertEqual(("q1" in active, "q1" in ad.kept_uuids(active), "q1c" in active), (False, True, True),
                         "premise: the fragment is off the spine and kept; the copy is on the spine")
        for anchor in ("pa2", "pa3"):
            with self.subTest(anchor=anchor):
                _c, _t, err, prior = self._spliced(anchor, records=recs)
                self.assertIsNone(err)
                self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n"
                              "> How should it back off when the server is down?\n\n", prior)
                self.assertIn("Right after it, I said:\n\n> and the cap?\n\n", prior)
                self.assertNotIn(CORRECTION, prior)

    def test_the_same_words_sent_again_later(self):
        # a copy repeats the send stamp as well as the text: here the same words are sent again mid-turn under a later
        # stamp, after the agent's reply to the first and a second tool call, and the chat shows two bubbles (a
        # premise). Red before the fix (no splice was read)
        t = self.now - 900
        recs = spliced_records(t)[:8] + [
            tline(t + 80, "pt3", "pa3"), tool_result_line(t + 85, "pr3", "pt3", "pt3"),
            attline(t + 82, CORRECTION, "q2", "pr3"),
            aline(t + 90, "Understood: five percent in the retry helper too.", "pa3b", parent="q2"),
            uline(t + 120, "and the cap?", "p3", parent="pa3b"),
            aline(t + 125, "Cap the delay at two minutes.", "pa4", parent="p3"),
            cli_boundary(t + 200, "pb1", "pa4"),
            summary_line(t + 201, "ps1", "pb1"),
            uline(t + 300, "now wire the dashboard", "p4", parent="ps1"),
            aline(t + 305, "The dashboard shows each endpoint's retry state.", "pa5", parent="p4")]
        self.assertEqual(self._shown(recs, CORRECTION), 2, "premise: the chat shows two bubbles")
        _c, _t, err, prior = self._spliced("pa3", records=recs)
        self.assertIsNone(err)
        self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n> " + CORRECTION + "\n\n", prior)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior,
                      "the second send, not the typed follow-up after the agent's reply to it")

    def test_the_same_words_with_unreadable_stamps_are_two_messages_where_the_chat_shows_two(self):
        # two sends of the same words whose stamps do not parse, with readable records between them: here they key apart
        # and the chat shows two bubbles (a premise); the excerpts follow the parse, so the second is quoted after the
        # agent's reply to the first. Red before the fix (no splice was read)
        t = self.now - 900
        recs = spliced_records(t)[:8] + [
            tline(t + 80, "pt3", "pa3"), tool_result_line(t + 85, "pr3", "pt3", "pt3"),
            attline(t + 82, CORRECTION, "q2", "pr3"),
            aline(t + 90, "Understood: five percent in the retry helper too.", "pa3b", parent="q2"),
            uline(t + 120, "and the cap?", "p3", parent="pa3b"),
            aline(t + 125, "Cap the delay at two minutes.", "pa4", parent="p3"),
            cli_boundary(t + 200, "pb1", "pa4"),
            summary_line(t + 201, "ps1", "pb1")]
        recs = [dict(r, timestamp="not a time") if r["uuid"] in ("q1", "q2") else r for r in recs]
        self.assertEqual(self._shown(recs, CORRECTION), 2, "premise: the chat shows two bubbles")
        _c, _t, err, prior = self._spliced("pa3", records=recs)
        self.assertIsNone(err)
        self.assertIn(km._PRECOMPACT_LEAD + " Right before it, I said:\n\n> " + CORRECTION + "\n\n", prior)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior)

    def test_a_different_message_with_the_same_stamp_is_no_copy(self):
        # nor is a splice that shares only the send stamp: a task notification enqueued in the same instant as the person's
        # correction leaves the correction a message of its own. Red before the fix (no splice was read)
        t = self.now - 900
        recs = spliced_records(t)
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "q1")
        recs[at] = dict(recs[at], parentUuid="tn1")
        recs.insert(at, attline(t + 68, "<task-notification>The build finished.</task-notification>", "tn1", "pr2",
                                commandMode="task-notification"))
        self.assertEqual(recs[at]["timestamp"], recs[at + 1]["timestamp"], "premise: one stamp")
        _c, _t, err, prior = self._spliced("pa2", records=recs)
        self.assertIsNone(err)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior)

    def test_other_attachments_on_the_chain_are_nobody(self):
        # the CLI chains attachments of other kinds through the same turns: a hook's result after a tool's, a token-count
        # reminder at a request's start. The parse makes no atom of them, nor of an attachment of a kind it does not know,
        # even one carrying a prompt, so each walk below crosses them and no excerpt changes. A guard: before the fix no
        # attachment was read at all
        t = self.now - 900
        plain = spliced_records(t)
        recs = list(plain)
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "q1")
        recs[at] = dict(recs[at], parentUuid="uk1")
        recs[at:at] = [other_attline(t + 71, "hk1", "pr2", type="hook_success", hookEvent="PostToolUse",
                                     stdout="", stderr="", exitCode=0),
                       other_attline(t + 71, "uk1", "hk1", type="unknown_kind", prompt="Not the person's words.")]
        at = next(i for i, r in enumerate(recs) if r["uuid"] == "pa3")
        recs[at] = dict(recs[at], parentUuid="rm1")
        recs.insert(at, other_attline(t + 72, "rm1", "q1", type="total_tokens_reminder"))
        for anchor in ("pa2", "q1", "pa3"):
            with self.subTest(anchor=anchor):
                want = self._spliced(anchor, records=plain)
                self.assertIn("Right before it, ", want[3], "premise: the block this compares against has excerpts")
                self.assertIn("Right after it, ", want[3], "premise: the block this compares against has excerpts")
                self.assertEqual(self._spliced(anchor, records=recs), want)

    def test_a_message_sent_mid_turn_is_still_no_cut_point(self):
        # the cut walk still passes over the attachment, as over a tool row: the prose above it carries the cut. A guard
        t = self.now - 900
        recs = spliced_records(t) + [tline(t + 306, "pt5", "pa5"), tool_result_line(t + 308, "pr5", "pt5", "pt5"),
                                     attline(t + 307, "Show the retry count too.", "q5", "pr5"),
                                     aline(t + 315, "The dashboard shows the retry count too.", "pa6", parent="q5")]
        cut, _t, err, prior = self._spliced("q5", records=recs)
        self.assertIsNone(err)
        self.assertEqual(cut, "pa5")
        self.assertEqual(prior, "")

    # Checks beside the walk: what the fresh read's road costs the parse stats, that the chat and the walk read a
    # splice's prompt alike, and a user record's origin screen.

    def test_the_fresh_reads_walk_counts_no_clamp_in_the_parse_stats(self):
        # on the fresh read's road the walk runs the parse's own splice emit (_absorbed) over its adapter to learn which
        # attachments are messages and whose; that is no parse, so a splice the parse clamps to its send time (sent a second
        # after its file-order predecessor was written) is not counted again in landedT-clamp, the parse stat that shows a
        # change in the CLI's write order. Red if the walk's emit counts
        t = self.now - 900
        recs = [dict(r, timestamp=iso(t + 71)) if r["uuid"] == "q1" else r for r in spliced_records(t)]
        p = str(self._write(PARENT, recs))
        forget_parses()
        clamps = lambda: em._ASM_STATS.get("landedT-clamp", 0)
        n0 = clamps()
        em.FileAdapter([p], p).atoms(PARENT, {})
        self.assertEqual(clamps() - n0, 1, "premise: a parse clamps the splice and counts it")
        g = em.TranscriptGraph(km._anchor_adapter(p, PARENT))
        n0 = clamps()
        _c, _t, err, prior = km._comment_cut_walk(g, p, "pa2")
        self.assertEqual(clamps(), n0, "the walk's own emit counts no clamp")
        self.assertIsNone(err)
        self.assertIn("Right after it, I said:\n\n> " + CORRECTION + "\n\n", prior, "premise: the walk read the splice")

    def test_the_chat_and_the_walk_read_a_splices_prompt_alike(self):
        # the chat fills a restored entry's lazy bubble for a message sent mid-turn from its record (em._hydrate_one,
        # the atom kind "b"), and the walk reads the record's words (TranscriptGraph.splice_text): each gives the text
        # the whole parse's atom carries, for a string or a content-block list, and for a string with whitespace at its
        # edges
        edges = ("\n\n" + CORRECTION + "\n", "    " + CORRECTION)
        for label, prompt, text in (("string", self.PROMPTS[0], CORRECTION), ("blocks", self.PROMPTS[1], CORRECTION),
                                    ("blank lines around", edges[0], edges[0]), ("indented", edges[1], edges[1])):
            with self.subTest(prompt=label):
                recs = spliced_records(self.now - 900, prompt)
                p = str(self._write(PARENT, recs))
                forget_parses()
                ad = em.FileAdapter([p], p)
                whole = [a["message"]["content"] for a in ad.atoms(PARENT, {}) if a.get("uuid") == "q1"]
                self.assertEqual(whole, [[{"type": "text", "text": text}]], "premise: the whole parse's atom")
                atom = {"uuid": "q1", "lazy": {"k": "b"}}
                em._hydrate_one(atom, next(r for r in recs if r["uuid"] == "q1"))
                self.assertEqual(atom["message"]["content"], [{"type": "text", "text": text}])
                self.assertEqual(em.TranscriptGraph(ad).splice_text("q1"), text)

    def test_a_user_record_stamped_with_an_injected_origin_is_nobody(self):
        # a user record stamped as another session's or a task notification's is nobody; with the person's own stamp,
        # or none, the plain text below is theirs. A guard; red if _comment_said drops its origin screen
        text = "The notes-api build finished."
        for origin, want in (({"kind": "peer", "from": "web"}, None), ({"kind": "task-notification"}, None),
                             ({"kind": "human"}, ("me", text)), (None, ("me", text))):
            with self.subTest(origin=origin):
                rec = uline(self.now, text, "u0")
                if origin is not None:
                    rec = dict(rec, origin=origin)
                self.assertEqual(km._comment_said(rec, lambda t: False), want)

    def test_rewind_still_refuses_a_pre_compaction_message(self):
        p = self._write(PARENT, compacted_records(self.now - 900))
        cut, err = km._rewind_target(str(p), PARENT, "p3")
        self.assertIsNone(cut)
        self.assertIn("compaction", err, "editing a past message is out of this change: the CLI can't rewind there")


# ── the thread fork's invisibility contract ───────────────────────────────────────────────────────

class ThreadForkInvisibility(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("parent", self.td, sid=PARENT)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_fork_observability_stamps_the_source_size_for_the_spend_log(self):
        # T156, measured no-build: the tail-copy optimization died on evidence (~5s of a ~70s wall
        # was file size), so every fork records its resume-source size at mint; the spend site logs
        # size + create-to-spend duration, and a recurrence of 200s-class boots reopens the case
        # with data instead of speculation.
        import os as _os
        tp = sb.transcript_path(self.td, PARENT)
        _os.makedirs(_os.path.dirname(tp), exist_ok=True)
        with open(tp, "w") as f:
            f.write('{"type":"user","uuid":"a1"}\n' * 100)
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertEqual(reg.get("forkSrcBytes"), _os.path.getsize(tp))
        self.assertGreater(reg.get("forkedFrom", {}).get("t", 0), 0, "the duration base is the mint stamp")
        # the spend site reads both back into one log line (source pin — the spend needs a live CLI)
        import inspect
        spend = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn('self.backend._log("fork spent (%s): src %.1fMB, create->spend %ds"', spend)

    def test_a_thread_fork_writes_no_names_entry_and_carries_threadOf(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertFalse((Path(self.td) / "names" / THREAD).exists(),
                         "names/ is the discoverability trigger — a thread must never write it")
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertEqual(reg.get("threadOf"), PARENT)
        self.assertEqual(reg.get("forkOf"), PARENT)
        self.assertEqual(reg.get("forkAt"), "a1")

    def test_fast_mode_rides_the_fork_like_model_and_effort(self):
        # the user 2026-08-25: a comment made from an Opus-high-FAST session came up slow — the fork
        # reg seeded mode/effort/model from the parent but never fast; fast_opt reads reg["fast"] at
        # connect, so inheriting it here makes the thread fast from its first frame
        preg_path = Path(self.td) / "sdk" / (PARENT + ".json")
        preg = json.loads(preg_path.read_text())
        preg["fast"] = True
        preg_path.write_text(json.dumps(preg))
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertTrue(reg.get("fast"), "a fast parent's new thread is fast")

    def test_a_slow_parent_stays_slow(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("fast", reg, "no inherited fast key when the parent never asked for it")

    def test_a_fork_fast_on_arms_the_ask_on_a_slow_parent(self):
        # the user 2026-08-29: the default-comment setting (or a dialog pick) can arm fast for the
        # thread regardless of the parent; the reg's `fast` is what fast_opt reads at connect
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT, fast="on")
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertTrue(reg.get("fast"), "an explicit on arms the ask on a slow parent")

    def test_a_fork_fast_off_launches_plain_from_a_fast_parent(self):
        preg_path = Path(self.td) / "sdk" / (PARENT + ".json")
        preg = json.loads(preg_path.read_text())
        preg["fast"] = True
        preg_path.write_text(json.dumps(preg))
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT, fast="off")
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("fast", reg, "off launches plain; the parent keeps its own ask untouched")

    def test_the_fast_ask_reaches_the_clis_flag_settings(self):
        # the chain the reg key rides (source pins — the flag file needs a live CLI connect):
        # reg["fast"] → fast_opt at construct → _options hands it to flag_settings_path → the
        # CLI's documented fastMode opt-in key. A refusal clears the ask + toasts, same model.
        import inspect
        self.assertIn('self.fast_opt = bool(reg.get("fast"))', inspect.getsource(sb.SdkSession.__init__))
        import re
        opts = inspect.getsource(sb.SdkBackend._options)
        # the flag settings take fast_effective's value (the session's ask, or the machine's Always fast switch, 2026-09-17):
        # the call itself, or a name _options binds to it
        flag = re.search(r"\bfast=([\w.]+(?:\(\))?)", opts[opts.index("flag_settings_path("):]).group(1)
        if flag != "sess.fast_effective()":
            self.assertRegex(opts, r"\b%s = sess\.fast_effective\(\)" % re.escape(flag))
        self.assertIn('keys["fastMode"] = True', inspect.getsource(sb.flag_settings_path))
        refusal = inspect.getsource(sb.SdkSession._adopt_fast_state)
        self.assertIn('refused_ask = bool(reason) and self.fast_opt and fast != "on"', refusal)
        self.assertIn('kw["fast"] = False', refusal, "a refused ask is cleared, never left armed")

    def test_a_plain_fork_still_writes_its_names_entry(self):
        self.be.fork("fork-x", PARENT, "a1", sid=THREAD)
        self.assertTrue((Path(self.td) / "names" / THREAD).exists())

    def test_a_thread_fork_arms_no_opener_its_first_message_is_framed_at_create(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("forkOpener", reg)

    def test_live_sessions_skips_threads_so_no_tab_is_born(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertIn(PARENT, self.be.live_sessions())
        self.assertNotIn(THREAD, self.be.live_sessions(),
                         "a thread's only surface is the parent chat's comment UI")

    def test_promote_writes_names_and_clears_threadOf(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertTrue(self.be.promote_thread(THREAD, "sidework", "#123456", "#ffffff"))
        self.assertTrue((Path(self.td) / "names" / THREAD).exists())
        reg = json.loads((Path(self.td) / "sdk" / (THREAD + ".json")).read_text())
        self.assertNotIn("threadOf", reg)
        self.assertEqual(reg.get("name"), "sidework")
        self.assertIn(THREAD, self.be.live_sessions(), "a promoted thread is an ordinary session")

    def test_promote_refuses_a_non_thread(self):
        self.assertFalse(self.be.promote_thread(PARENT, "nope"))

    def test_session_state_reads_a_dormant_thread_as_empty(self):
        self.be.fork("thread-x", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.assertEqual(self.be.session_state(THREAD), "")


class ThreadDiscoverBlindness(CommentBase):
    def test_discover_never_lists_a_thread(self):
        self._write(PARENT, self._parent_records())
        (jd.NAMES / PARENT).write_text("parent\t%s" % self.cdir)
        self._write(THREAD, self._parent_records())     # the fork's transcript exists on disk
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        sids = [r[0] for r in jd.discover(self.now)]
        self.assertIn(PARENT, sids)
        self.assertNotIn(THREAD, sids, "no names/ entry → no judge pass, no cards, no lane")


# ── the opening message: frame + strip ────────────────────────────────────────────────────────────

class OpeningMessage(unittest.TestCase):
    def test_frame_quotes_the_passage_and_carries_the_comment(self):
        body = km._comment_first_message("Cap the delay at two minutes.", "Why two minutes and not five?")
        self.assertIn("> Cap the delay at two minutes.", body)
        self.assertTrue(body.endswith("Why two minutes and not five?"))
        self.assertIn("\n\n" + km._COMMENT_FRAME_HEAD + "\n\n", body)

    def test_the_opener_says_what_the_thread_is_before_the_quote(self):
        # 2026-09-24: a thread opened with only the quote and the comment took the parent's copied history
        # (the parent's role and plans) as its own and relaunched the parent's work. The one message sure to be
        # in the thread's context, its first, now opens by saying what this conversation is.
        body = km._comment_first_message("Cap the delay at two minutes.", "Why two minutes and not five?")
        self.assertEqual(body, km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD
                         + "\n\n> Cap the delay at two minutes.\n\nWhy two minutes and not five?")
        # the wording the user approved (2026-09-24), pinned verbatim: a thread may act on a request, not only answer
        self.assertEqual(km._THREAD_FRAME,
                         "I've split this off from our main conversation to ask about one part of it. The main "
                         "conversation carries on separately; just deal with what I ask here, and don't continue "
                         "its work or pick up its unfinished tasks or background work.")

    def test_strip_returns_exactly_the_comment(self):
        body = km._comment_first_message("line one\nline two", "The comment.\n\nWith two paragraphs.")
        self.assertEqual(km._comment_strip_frame(body), "The comment.\n\nWith two paragraphs.")

    def test_strip_reads_a_thread_opened_before_the_identity_line(self):
        # a thread already on disk keeps its first message as it was written (the history is append-only)
        old = km._COMMENT_FRAME_HEAD + "\n\n> line one\n\nThe comment."
        self.assertEqual(km._comment_strip_frame(old), "The comment.")

    def test_strip_takes_the_fork_line_off_a_dispatched_thread(self):
        self.assertEqual(km._comment_strip_frame(km._FORK_FRAME + "\n\nReview the tracked edits."),
                         "Review the tracked edits.")

    def test_strip_leaves_an_unframed_message_alone(self):
        self.assertEqual(km._comment_strip_frame("plain reply"), "plain reply")

    TRANSCRIPT = "/TESTDIR/.claude/projects/-TESTDIR-notes-api/11111111-2222-3333-4444-555555555555.jsonl"
    PRIOR = km._precompact_block(("me", "How should it back off?"), ("me", "and the cap?"), TRANSCRIPT, "pa2")

    def test_a_pre_compaction_opener_reads_as_the_user_approved_it(self):
        # the user 2026-09-30, reviewing the first draft: arranged for what the model parses most easily, the comment
        # last and labelled as the ask. Pinned verbatim, the identity line included
        body = km._comment_first_message("Use exponential backoff.", "Why ten percent?", self.PRIOR)
        self.assertEqual(body, km._THREAD_FRAME + "\n\n"
                         "About this part of the conversation:\n\n"
                         "> Use exponential backoff.\n\n"
                         "It's from before our conversation was summarized, so you may only have the summary of what was "
                         "around it. Right before it, I said:\n\n"
                         "> How should it back off?\n\n"
                         "Right after it, I said:\n\n"
                         "> and the cap?\n\n"
                         "The full conversation is saved in " + self.TRANSCRIPT + " (this passage is the entry with uuid "
                         "pa2) if you need more.\n\n"
                         "What I'm asking:\n\n"
                         "Why ten percent?")
        self.assertEqual(km._comment_strip_frame(body), "Why ten percent?", "the bubble shows the comment alone")

    def test_an_ordinary_opener_is_unchanged_and_wears_no_ask_label(self):
        body = km._comment_first_message("Use exponential backoff.", "Why ten percent?")
        self.assertEqual(body, km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> Use exponential backoff.\n\n"
                         "Why ten percent?")
        self.assertNotIn(km._COMMENT_ASK_HEAD, body)

    def test_the_strip_keeps_a_multi_paragraph_comment_whole(self):
        body = km._comment_first_message("line one\nline two", "The comment.\n\n> quoting myself\n\nAnd more.", self.PRIOR)
        self.assertEqual(km._comment_strip_frame(body), "The comment.\n\n> quoting myself\n\nAnd more.")

    def test_a_dispatched_thread_on_such_a_passage_opens_with_the_fork_line(self):
        body = km._comment_first_message("Use exponential backoff.", "Review the cap.", self.PRIOR, frame=km._FORK_FRAME)
        self.assertTrue(body.startswith(km._FORK_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD))
        self.assertEqual(km._comment_strip_frame(body), "Review the cap.")

    # ── a passage shorter than a sentence (the user 2026-10-14, who approved the wording): the opener names the words
    # and quotes the sentence they sit in, which the page read from the rendered message at the selection ────────────
    SENTENCE = "\u2026We should revisit the placement of the legend before shipping the deck\u2026"

    def test_a_short_passage_names_its_words_and_quotes_its_sentence(self):
        body = km._comment_first_message("placement", "Where would it go instead?", context=self.SENTENCE)
        self.assertEqual(body, km._THREAD_FRAME + "\n\n"
                         'About "placement" in this part of the conversation:\n\n'
                         "> \u2026We should revisit the placement of the legend before shipping the deck\u2026\n\n"
                         "Where would it go instead?")
        self.assertEqual(km._comment_strip_frame(body), "Where would it go instead?", "the bubble shows the comment alone")
        sent, said = km._comment_opener_parts("placement", "Where would it go instead?", context=self.SENTENCE)
        self.assertEqual(km._comment_opener_split(body), (sent, said), "the note shows what romp added, verbatim")

    def test_a_short_passages_marked_occurrence_rides_as_the_page_marked_it(self):
        ctx = "Move the legend below the chart, and keep the **legend**'s font small."
        body = km._comment_first_message("legend", "Which one moves?", context=ctx)
        self.assertIn('About "legend" in this part of the conversation:\n\n> ' + ctx + "\n\n", body)

    def test_a_short_passage_on_every_layout_splits_back(self):
        for prior, frame in (("", None), ("", km._FORK_FRAME), (self.PRIOR, None), (self.PRIOR, km._FORK_FRAME)):
            with self.subTest(prior=bool(prior), frame=bool(frame)):
                sent, said = km._comment_opener_parts("placement", "> quoting myself\n\nWhy?", prior, frame,
                                                      context=self.SENTENCE)
                self.assertIn('About "placement" in this part of the conversation:\n\n> ' + self.SENTENCE, sent)
                self.assertEqual(km._comment_opener_split(sent + "\n\n" + said), (sent, "> quoting myself\n\nWhy?"))

    # ── review of #229 (2026-10-14): code is named over its line, a code passage keeps its line breaks, and only a few
    # words are ever named ─────────────────────────────────────────────────────────────────────────────────────────────
    def test_a_word_in_a_line_of_code_is_named_over_that_line(self):
        body = km._comment_first_message("refill", "Why call it here?", context="x = refill(cache)")
        self.assertEqual(body, km._THREAD_FRAME + "\n\n"
                         'About "refill" in this part of the conversation:\n\n> x = refill(cache)\n\nWhy call it here?')

    def test_a_code_passage_over_several_lines_is_quoted_as_its_lines(self):
        exact = "def refill(cache):    return cache.load()"          # the rendered rows join with no line break
        lines = "def refill(cache):\n    return cache.load()\n\nx = 1"
        exact2 = exact + "x = 1"
        body = km._comment_first_message(exact2, "Why load?", shown=lines)
        self.assertEqual(body, km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n"
                         "> def refill(cache):\n>     return cache.load()\n>\n> x = 1\n\nWhy load?")
        sent, said = km._comment_opener_parts(exact2, "Why load?", shown=lines)
        self.assertEqual(km._comment_opener_split(body), (sent, said), "the note shows it verbatim, the comment splits off")
        self.assertEqual(km._comment_first_message(exact, "Why?", shown="something else entirely"),
                         km._comment_first_message(exact, "Why?"), "lines that are not the passage are not used")

    def test_a_long_passage_is_never_named_whatever_comes_with_it(self):
        exact = " ".join("w%d" % i for i in range(40))           # 150 characters, no full stop
        body = km._comment_first_message(exact, "Why?", context="\u2026" + exact + " w40\u2026")
        self.assertEqual(body, km._comment_first_message(exact, "Why?"), "the head never repeats a long selection")

    def test_no_sentence_or_one_without_the_words_is_the_plain_opener(self):
        plain = km._comment_first_message("placement", "Why?")
        self.assertIn("\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> placement\n\n", plain, "a whole-sentence passage, as before")
        self.assertEqual(km._comment_first_message("placement", "Why?", context=""), plain)
        self.assertEqual(km._comment_first_message("placement", "Why?", context="An unrelated line."), plain,
                         "a sentence that does not hold the words is not this passage's")

    # ── review of #231 (2026-10-14): the opening message holds the message box's 4,000-character cap, with the cut marked,
    # and quotes the passage as the page read it, its controls, labels and closed folds left out ──────────────────────
    LONG = " ".join("Sentence number %d talks about item %d." % (i, i) for i in range(400))   # about 16,500 characters

    def _quote(self, sent):
        return sent.split(km._COMMENT_FRAME_HEAD + "\n\n", 1)[1]

    def test_a_passage_longer_than_the_cap_is_quoted_cut_there_with_the_cut_marked(self):
        cut = "> " + self.LONG[:3999].rstrip() + "…"
        self.assertEqual(self._quote(km._comment_opener_parts(self.LONG, "Thoughts?")[0]), cut, "the passage itself")
        self.assertEqual(self._quote(km._comment_opener_parts(self.LONG, "Thoughts?", shown=self.LONG)[0]), cut,
                         "the passage as the page read it")
        self.assertEqual(len(km._cap_quote(self.LONG)), 4000)
        self.assertEqual(km._cap_quote("short"), "short")
        body = km._comment_first_message(self.LONG, "Thoughts?")
        self.assertEqual(km._comment_opener_split(body), km._comment_opener_parts(self.LONG, "Thoughts?"), "it splits back")

    def test_lines_that_leave_out_the_pages_labels_and_folds_are_the_passages(self):
        exact = "x = refill(cache)Copy\nMore detailText inside the closed fold.\nThe paragraph after the fold."   # the range's text
        shown = "x = refill(cache)\n\nMore detail\n\nThe paragraph after the fold."                            # as it reads
        sent, _ = km._comment_opener_parts(exact, "Why?", shown=shown)
        self.assertEqual(self._quote(sent), "> x = refill(cache)\n>\n> More detail\n>\n> The paragraph after the fold.")
        # a chip's passage, cut at the cap with labels in it: its text as it reads runs on past where the cut words end
        raw = km._cap_quote("List the files1 line " + self.LONG)
        sent, _ = km._comment_opener_parts(raw, "Why?", shown=km._cap_quote(self.LONG))
        self.assertEqual(self._quote(sent), "> " + km._cap_quote(self.LONG), "compared as far as the cut passage goes")
        self.assertEqual(km._comment_opener_parts(raw, "Why?", shown="Words that were never in it.")[0],
                         km._comment_opener_parts(raw, "Why?")[0], "text that is not the passage's is still left out")

    # ── what romp sent, read back from a stored opening message (the popover's note, the user 2026-09-30) ──────────
    def test_the_split_gives_back_exactly_what_romp_added_and_the_users_words(self):
        for prior, frame in (("", None), (self.PRIOR, None), (self.PRIOR, km._FORK_FRAME)):
            sent, said = km._comment_opener_parts("line one\nline two", "Why ten percent?", prior, frame)
            opener = km._comment_first_message("line one\nline two", "Why ten percent?", prior, frame)
            self.assertEqual(opener, sent + "\n\n" + said, "the opener is romp's part, then the user's")
            self.assertEqual(km._comment_opener_split(opener), (sent, "Why ten percent?"), (prior[:20], frame))
        sent, _ = km._comment_opener_parts("line one", "c", self.PRIOR)
        self.assertTrue(sent.endswith(self.PRIOR + "\n\n" + km._COMMENT_ASK_HEAD), "the ask label is romp's, sent with it")

    def test_older_and_dispatched_openers_split_to_what_romp_added_in_their_case(self):
        split = km._comment_opener_split
        # a thread from before the identity line: the quote head and the quote
        self.assertEqual(split(km._COMMENT_FRAME_HEAD + "\n\n> line one\n\nThe comment."),
                         (km._COMMENT_FRAME_HEAD + "\n\n> line one", "The comment."))
        # a dispatched thread: the fork line only
        self.assertEqual(split(km._FORK_FRAME + "\n\nReview the tracked edits."), (km._FORK_FRAME, "Review the tracked edits."))
        # romp added nothing (an unframed send, a dispatched thread from before the fork line): no note
        self.assertEqual(split("Review the tracked edits."), ("", "Review the tracked edits."))
        self.assertEqual(split(km._FORK_FRAME), ("", km._FORK_FRAME), "nothing but the line leaves no comment: whole")
        self.assertEqual(split(""), ("", ""))

    def test_a_stored_opener_whose_comment_opens_with_a_quote(self):
        # Each comment here opens with a markdown quote and splits off whole, on each layout and on an opener from
        # before the identity line. The second passage ends in a blank line, which the builder quotes as a bare ">"
        # (premise below), a line romp's quote must take. The last comment puts a pasted context block under its own
        # quote, and the block stays the comment's.
        layouts = (("", None), ("", km._FORK_FRAME), (self.PRIOR, None), (self.PRIOR, km._FORK_FRAME))
        comments = ("> ten percent\nwhy not twenty?", "> ten percent\n\nwhy not twenty?", "> ten percent",
                    "> first\n>\n> second\n\nand why?", "> ten percent\n\n" + self.PRIOR + "\n\nwhy not twenty?")
        for exact, bare_end in (("line one\n\nline two", False), ("line one\n\n", True)):
            # romp's quote read off the builder: one line per passage line, each starting with ">" and none empty,
            # and for the second passage a bare ">" last line. How a blank line inside the passage is spelled is left
            # free.
            built = km._comment_opener_parts(exact, "c")[0]
            quote = built.split(km._COMMENT_FRAME_HEAD + "\n\n", 1)[1].split("\n\n", 1)[0]
            qlines = quote.split("\n")
            self.assertEqual(len(qlines), len(exact.splitlines()), "premise: a quote line per passage line, none empty")
            self.assertTrue(all(ln.startswith(">") for ln in qlines), "premise: every line of romp's quote is a > line")
            self.assertEqual(qlines[-1] == ">", bare_end, "premise: a blank last line is quoted as a bare >")
            for comment in comments:
                self.assertTrue(comment.startswith(">"), "premise: the comment opens with a markdown quote")
                for prior, frame in layouts:
                    with self.subTest(exact=exact, comment=comment, prior=bool(prior), frame=bool(frame)):
                        sent, said = km._comment_opener_parts(exact, comment, prior, frame)
                        self.assertEqual(said, comment)
                        self.assertIn("\n\n" + quote + "\n\n", sent + "\n\n",
                                      "premise: every layout quotes the passage alike, a blank line after it")
                        self.assertEqual(km._comment_opener_split(sent + "\n\n" + said), (sent, comment))
                with self.subTest(exact=exact, comment=comment, before_the_identity_line=True):
                    head = km._COMMENT_FRAME_HEAD + "\n\n" + quote
                    self.assertEqual(km._comment_opener_split(head + "\n\n" + comment), (head, comment))

    def test_a_comment_that_opens_with_a_pasted_context_block_right_after_romps_quote_is_misread(self):
        # A known case, pinned as it stands: on an ordinary opener and on one from before the identity line, a comment
        # that opens with the context block's lead sentence and has a line starting with its file pointer, as a pasted
        # block would, reads as romp's through the pointer (and an ask label under it, if any); with nothing after
        # the pointer, the whole opener comes back. On a pre-compaction opener the same comments split right.
        split = km._comment_opener_split
        ask = "\n\n" + km._COMMENT_ASK_HEAD
        cases = (("text after the pointer", self.PRIOR + "\n\nwhy not twenty?", self.PRIOR),
                 ("the ask label and text", self.PRIOR + ask + "\n\nwhy not twenty?", self.PRIOR + ask),
                 ("nothing after the pointer", self.PRIOR, None))
        for after, comment, romps in cases:
            self.assertTrue(comment.startswith(km._PRECOMPACT_LEAD), "premise: the comment opens with the lead")
            self.assertIn("\n" + km._PRECOMPACT_FILE_LEAD, comment, "premise: and has a line starting with the pointer")
            sent, said = km._comment_opener_parts("line one", comment)
            self.assertEqual(said, comment, "premise: the builder sends the comment as typed")
            older = km._COMMENT_FRAME_HEAD + "\n\n> line one"
            for layout, head in (("ordinary", sent), ("before the identity line", older)):
                with self.subTest(layout=layout, after=after):
                    opener = head + "\n\n" + comment
                    if romps is None:
                        self.assertEqual(split(opener), ("", opener), "no comment is left: the whole opener comes back")
                    else:
                        self.assertEqual(split(opener), (head + "\n\n" + romps, "why not twenty?"),
                                         "through the pointer, and any ask label under it, reads as romp's")
            with self.subTest(layout="pre-compaction", after=after):
                sent, said = km._comment_opener_parts("line one", comment, self.PRIOR)
                self.assertEqual(split(sent + "\n\n" + said), (sent, comment), "romp's own block comes first")

    def test_a_stored_opener_with_a_context_block_after_the_comments_text_or_its_lead_sentence_without_a_pointer(self):
        # Each comment splits off whole, on an ordinary opener and on one from before the identity line: a pasted
        # context block after the comment's own text, below it or on the same line, with and without text after the
        # block, and the block's lead sentence with no pointer line.
        split = km._comment_opener_split
        below = ("why not twenty?\n\n" + self.PRIOR, "why not twenty?\n\n" + self.PRIOR + "\n\nand the cap?")
        same_line = ("see this: " + self.PRIOR, "see this: " + self.PRIOR + "\n\nwhy not twenty?")
        no_pointer = km._PRECOMPACT_LEAD + " Why ten percent?"
        for comment in below + same_line:
            self.assertFalse(comment.startswith(km._PRECOMPACT_LEAD), "premise: the comment opens with its own text")
            self.assertIn("\n" + km._PRECOMPACT_FILE_LEAD, comment, "premise: and has a line starting with the pointer")
        for comment in below:
            self.assertIn("\n" + km._PRECOMPACT_LEAD, comment, "premise: a line starting with the lead comes after it")
        for comment in same_line:
            self.assertIn(km._PRECOMPACT_LEAD, comment.splitlines()[0], "premise: the lead is on the first line")
        self.assertNotIn(km._PRECOMPACT_FILE_LEAD, no_pointer, "premise: no pointer line")
        for comment in below + same_line + (no_pointer,):
            sent, said = km._comment_opener_parts("line one", comment)
            self.assertEqual(said, comment, "premise: the builder sends the comment as typed")
            older = km._COMMENT_FRAME_HEAD + "\n\n> line one"
            for layout, head in (("ordinary", sent), ("before the identity line", older)):
                with self.subTest(layout=layout, comment=comment):
                    self.assertEqual(split(head + "\n\n" + comment), (head, comment))

    def test_the_split_misreads_a_prompt_that_opens_with_the_quote_head_right_after_the_fork_line(self):
        # A known case, pinned as it stands: right after the fork line, this prompt, which opens with the quote head,
        # reads as romp's through the head and its quote.
        prompt = km._COMMENT_FRAME_HEAD + "\n\n> the retry section\n\nTighten the wording here."
        opener = km._FORK_FRAME + "\n\n" + prompt
        self.assertEqual(km._comment_opener_split(opener),
                         (km._FORK_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> the retry section",
                          "Tighten the wording here."), "the prompt's head and quote read as romp's")

    def test_a_text_with_nothing_after_romps_head_quote_pointer_or_ask_label_comes_back_whole(self):
        # Each text here stops before any comment and comes back whole; a step that read past the last line would
        # raise instead. The last two end at a pre-compaction opener's pointer line and at the ask label under it.
        head = km._COMMENT_FRAME_HEAD
        asked = km._comment_opener_parts("a", "c", self.PRIOR)[0]
        ask = "\n\n" + km._COMMENT_ASK_HEAD
        self.assertTrue(asked.endswith(self.PRIOR + ask), "premise: romp's part ends at the ask label")
        pointed = asked[:-len(ask)]
        self.assertTrue(pointed.splitlines()[-1].startswith(km._PRECOMPACT_FILE_LEAD), "premise: the pointer line")
        texts = (head, head + "\n\n", head + "\n\n> a", head + "\n\n> a\n>", head + "\n\n> a\n\n",
                 km._THREAD_FRAME + "\n\n" + head + "\n\n> a\n>", km._FORK_FRAME + "\n\n" + head + "\n\n> a",
                 pointed, asked)
        for text in texts:
            with self.subTest(text=text):
                self.assertEqual(km._comment_opener_split(text), ("", text))


# ── the transcript → popover projection ───────────────────────────────────────────────────────────

class ThreadProjection(CommentBase):
    def _thread_records(self):
        """The fork copy (u1..a1, verbatim uuids) + the side conversation after the cut."""
        t = self.now - 500
        return [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 100, km._comment_first_message(
                    "exponential backoff", "Why jitter at all?"), "cu1", parent="a1"),
                aline(t + 110, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                aline(t + 111, "It also spreads retries across the window.", "ca2", parent="ca1")]

    def _seed_thread(self, records=None, seen=None, last_sid=None):
        """`last_sid` names the transcript the reg points at when it is not the thread sid itself: what a
        resume (a new fsid) or a /clear leaves behind. The default is the fresh fork, lastSid == sid."""
        self._write(last_sid or THREAD, records or self._thread_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": last_sid or THREAD, "alive": True, "threadOf": PARENT}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "exponential backoff", "status": "open",
             "createdT": self.now - 400, "lastSeenT": seen if seen is not None else self.now}]})

    def test_the_thread_build_is_served_while_its_inputs_stand_and_rebuilt_when_they_move(self):
        # 2026-09-08: the popover's build_session ran for every thread on every pusher cycle; it is now
        # served on the thread's exact change key (the active tab's own) and rebuilt when an input moves
        self._seed_thread()
        km._built_thread.clear()
        km._views_dirty[0] = 0.0
        calls = []
        real = km.build_session
        km.build_session = lambda sid, now, tm=None, **kw: (calls.append(sid), real(sid, now, tm, **kw))[1]
        try:
            fr1 = km._comments_frame(PARENT)
            n1 = len(calls)
            self.assertGreaterEqual(n1, 1, "the first frame builds the thread")
            fr2 = km._comments_frame(PARENT)
            self.assertEqual(len(calls), n1, "unchanged inputs: served, not rebuilt")
            self.assertEqual(fr1["threads"][0].get("unread"), fr2["threads"][0].get("unread"), "the same frame")
            recs = self._thread_records()
            recs.append(aline(self.now - 5, "one more thought on jitter", "a9", parent=recs[-1]["uuid"]))
            self._write(THREAD, recs)                  # a moved input (the thread's transcript)
            km._parse_cache.clear()
            km._comments_frame(PARENT)
            self.assertEqual(len(calls), n1 + 1, "a moved input rebuilds once")
            km._comments_frame(PARENT)
            self.assertEqual(len(calls), n1 + 1, "…and is served again afterwards")
            km._views_dirty[0] = time.time() + 1       # a kernel-side optimistic mutation rebuilds too
            km._comments_frame(PARENT)
            self.assertEqual(len(calls), n1 + 2)
        finally:
            km.build_session = real
            km._views_dirty[0] = 0.0
            km._built_thread.clear()

    def test_a_build_that_raises_stores_nothing_and_the_next_frame_rebuilds(self):
        # a transient read fault mid-build must not be served as the thread's (empty) events: nothing is stored
        # for it, the next frame builds again, and that good build is what gets served (review 2026-09-08)
        self._seed_thread()
        km._built_thread.clear()
        km._views_dirty[0] = 0.0
        calls = []
        real = km.build_session

        def flaky(sid, now, tm=None, **kw):
            calls.append(sid)
            if calls.count(THREAD) == 1 and sid == THREAD:
                raise OSError(errno.EIO, "a transient read fault")
            return real(sid, now, tm, **kw)

        km.build_session = flaky
        try:
            fr1 = km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), 1, "premise: the first frame built the thread once, and it raised")
            self.assertEqual(fr1["threads"][0]["events"], [], "the raised build reads as no events this frame")
            self.assertNotIn(THREAD, km._built_thread, "a raised build stores nothing")
            km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), 2, "the next frame rebuilds")
            self.assertIn(THREAD, km._built_thread, "the good build is cached")
            km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), 2, "and served from then on")
        finally:
            km.build_session = real
            km._views_dirty[0] = 0.0
            km._built_thread.clear()

    def test_a_states_write_under_the_thread_sid_rebuilds_a_thread_whose_lastsid_moved(self):
        # After a resume or a /clear the reg's lastSid names a NEW transcript while the backend keeps writing the
        # thread's state rows under the romp sid; keyed on the transcript's fsid alone, a states-only write (an
        # interrupt settle's idle row, a retry marker) changed the events with no key change (review 2026-09-08)
        resumed = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
        self._seed_thread(last_sid=resumed)
        km._built_thread.clear()
        km._views_dirty[0] = 0.0
        calls = []
        real = km.build_session
        km.build_session = lambda sid, now, tm=None, **kw: (calls.append(sid), real(sid, now, tm, **kw))[1]
        try:
            km._comments_frame(PARENT)
            n1 = calls.count(THREAD)
            self.assertGreaterEqual(n1, 1, "premise: the first frame builds the thread from the resumed transcript")
            km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), n1, "premise: served while nothing moved")
            states = jd.STATE / "states" / (THREAD + ".jsonl")
            states.parent.mkdir(parents=True, exist_ok=True)
            with open(states, "a") as fh:
                fh.write(json.dumps({"t": self.now, "state": "idle"}) + "\n")
            km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), n1 + 1, "a states-only write under the thread's own sid rebuilds")
            km._comments_frame(PARENT)
            self.assertEqual(calls.count(THREAD), n1 + 1, "and is served again afterwards")
        finally:
            km.build_session = real
            km._views_dirty[0] = 0.0
            km._built_thread.clear()

    def test_a_signature_that_raises_on_the_thread_path_is_said_once_per_episode(self):
        """One of the signature's component reads raising for a thread: the fault is written to stderr with
        its traceback and filed as one refused bell row naming the thread, ONCE per fault episode (the chat
        loop's rule for the same signature); a build is attempted every frame and nothing is stored while
        the key cannot be taken. The same fault the next frame says nothing, a different fault is a new
        episode, a signature that is taken ends it, and the same fault after that is said anew."""
        self._seed_thread()
        km._built_thread.clear()
        km._views_dirty[0] = 0.0
        saved = (km._watch_awaiting, km.build_session, list(km._SYNC_NOTICES), dict(km._chat_sig_faults))
        del km._SYNC_NOTICES[:]
        km._chat_sig_faults.clear()
        fail = ["synthetic: the watch rows cannot be read"]
        orig, real = saved[0], saved[1]
        calls = []

        def watch(sid):
            if sid == THREAD and fail[0]:
                raise OSError(fail[0])
            return orig(sid)

        km._watch_awaiting = watch
        km.build_session = lambda sid, now, tm=None, **kw: (calls.append(sid), real(sid, now, tm, **kw))[1]

        head = "push build: chat signature %s" % THREAD[:8]
        seen = []

        def frame():
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                km._comments_frame(PARENT)
            seen.append(err.getvalue())
            return seen[-1].count(head)
        try:
            self.assertEqual(frame(), 1, "the first frame says it")
            self.assertIn(head + ": Traceback (most recent call last)", seen[-1], "...with the traceback")
            self.assertIn(fail[0], seen[-1], "...naming the fault")
            self.assertEqual(frame(), 0, "the second frame, same fault: not again")
            self.assertEqual(frame(), 0)
            self.assertEqual(calls.count(THREAD), 3, "a build is attempted every frame while the key cannot be taken")
            self.assertNotIn(THREAD, km._built_thread, "nothing is stored")
            self.assertEqual(len(km._SYNC_NOTICES), 1, "one episode, one bell row")
            self.assertIn("thread-x", km._SYNC_NOTICES[-1]["text"], "the row names the thread")
            self.assertEqual(km._SYNC_NOTICES[-1].get("kind"), "refused")
            fail[0] = "synthetic: a different fault on the same thread"
            self.assertEqual(frame(), 1, "a different fault is a new episode")
            self.assertEqual(len(km._SYNC_NOTICES), 2)
            fail[0] = ""
            self.assertEqual(frame(), 0)
            self.assertNotIn(THREAD, km._chat_sig_faults, "a signature that is taken ends the episode")
            self.assertIn(THREAD, km._built_thread, "...and the build is cached like any other")
            fail[0] = "synthetic: the watch rows cannot be read"
            self.assertEqual(frame(), 1, "the same fault after a success is a new episode, said anew")
        finally:
            km._watch_awaiting = orig
            km.build_session = real
            del km._SYNC_NOTICES[:]
            km._SYNC_NOTICES.extend(saved[2])
            km._chat_sig_faults.clear()
            km._chat_sig_faults.update(saved[3])
            km._views_dirty[0] = 0.0
            km._built_thread.clear()

    def test_projection_starts_after_the_cut_and_strips_the_frame(self):
        self._seed_thread()
        msgs = km._thread_messages(THREAD, "a1")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"])
        self.assertEqual(msgs[0]["text"], "Why jitter at all?",
                         "the popover shows the comment, not its quoting frame")
        self.assertNotIn("thundering", msgs[0]["text"])

    def test_consecutive_agent_records_merge_into_one_reply(self):
        self._seed_thread()
        msgs = km._thread_messages(THREAD, "a1")
        self.assertIn("thundering herds", msgs[1]["text"])
        self.assertIn("spreads retries", msgs[1]["text"])

    def test_frame_reports_unread_from_the_watermark(self):
        self._seed_thread(seen=self.now - 450)      # replies landed after the last look
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["type"], "comments")
        self.assertEqual(fr["id"], PARENT)
        self.assertTrue(fr["threads"][0]["unread"])
        km._comment_seen(PARENT, THREAD)
        km._thread_msgs_cache.clear()
        fr = km._comments_frame(PARENT)
        self.assertFalse(fr["threads"][0]["unread"])

    # ── T237 (the user 2026-09-03, twice): the mark turns yellow only when the reply has LANDED ────────
    # yellow = a FINISHED reply you have not seen. The kernel is the one truth: `unread` keys on the
    # thread's turn having ENDED (the event model's own turn-end — the chat's working predicate, never
    # the backend's flapping state alone) plus agent content newer than the watermark in whichever
    # projection the popover renders (events, else msgs); `replyOwed` says a reply is still owed (the
    # user's message is newest, the turn is in progress, or the thread has no exchange yet) — the
    # client's green wash keys on that, not on a gesture latch it can lose.
    class _State:
        """A backend stub reporting one live state — with the reads the frame's parse path makes
        (pending_cut / session_since / session_meta), so the transcript is genuinely parsed."""
        def __init__(self, st):
            self.st = st
        def session_state(self, sid):
            return self.st
        def launch_error(self, sid):
            return None
        def pending_cut(self, sid):
            return ""
        def owns(self, sid):
            return True                           # the thread is a live SDK session: build_session runs, events are REAL
        queued = []
        live = []
        def pending_queued(self, sid):
            return list(self.queued)
        def live_atoms(self, sid):
            return list(self.live)
        unfloored = False                         # True → retire by bare text like the pre-T237b prune (order-sensitive)
        def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
            # the real backend's text retirement, floored when the caller ships text → newest record time
            keep = []
            for a in self.live:
                et = (a.get("_echo_text") or "").strip()
                if isinstance(tx_user_texts, dict) and not type(self).unfloored:
                    landed = et in tx_user_texts and float(tx_user_texts[et] or 0) >= float(a.get("t") or 0)
                else:
                    landed = et in tx_user_texts
                if not landed:
                    keep.append(a)
            type(self).live = keep
        def live_sessions(self):
            return {}
        def session_since(self, sid):
            return 0
        def session_meta(self, sid):
            return {}

    def _thread_side(self, *tail):
        t = self.now - 500
        return [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 100, km._comment_first_message("exponential backoff", "Why jitter at all?"),
                      "cu1", parent="a1")] + list(tail)

    def _frame_thread(self, records, state=""):
        self._write(THREAD, records)
        km._thread_msgs_cache.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._sdk = lambda: self._State(state)
        return km._comments_frame(PARENT)["threads"][0]

    def _tool_result(self, t, uuid, parent, tool_uuid):
        return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + tool_uuid,
                                                          "content": "the file's contents"}]}}

    def _partial_text(self, t, text, uuid, parent):
        """An intermediate prose block of a turn that goes on to call a tool — stop_reason tool_use."""
        return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "assistant", "stop_reason": "tool_use",
                            "content": [{"type": "text", "text": text},
                                        {"type": "tool_use", "id": "tu_" + uuid, "name": "Read", "input": {}}]}}

    def test_a_partial_turn_is_not_unread_and_a_reply_is_still_owed(self):
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        # (i) an assistant record lands while the turn is in progress — an intermediate text block that
        # goes on to a tool call: NOT a landed reply, whatever the backend's state says
        th = self._frame_thread(self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1")),
                                state="working")
        self.assertFalse(th["unread"], "an intermediate record is not a finished reply")
        self.assertTrue(th["replyOwed"], "the reply is still being worked on")
        # (vi) between records the backend still reports working (a live turn always snapshots
        # working/waiting/retrying — session_state reads "" only when no process exists, T237b B)
        th = self._frame_thread(self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                                  self._tool_result(t + 112, "cr1", "cp1", "cp1")), state="working")
        self.assertFalse(th["unread"])
        self.assertTrue(th["replyOwed"], "the in-flight wash holds through the tool call")
        # the turn ENDS (end_turn) — the reply has landed: yellow now, owed no more
        th = self._frame_thread(self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                                  self._tool_result(t + 112, "cr1", "cp1", "cp1"),
                                                  aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cr1")),
                                state="")
        self.assertTrue(th["unread"], "the finished reply is newer than the watermark")
        self.assertFalse(th["replyOwed"])

    def test_a_multi_record_turn_flips_unread_exactly_once_at_its_end(self):
        # (iii) tool_use → tool_result → final text: unread stays False through the partials, True at the end
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        steps = [tline(t + 110, "ct1", "cu1"),
                 self._tool_result(t + 112, "cr1", "ct1", "ct1"),
                 aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cr1")]
        flips = []
        for i in range(1, len(steps) + 1):
            th = self._frame_thread(self._thread_side(*steps[:i]), state="working" if i < len(steps) else "waiting")
            flips.append(th["unread"])
        self.assertEqual(flips, [False, False, True])

    def test_the_transcripts_turn_end_is_the_landing_not_the_backends_state(self):
        # the chat's own working predicate decides: an end_turn record IS the landing even while the
        # backend still reports the turn in flight for a push; an open turn stays busy even when the
        # backend already says waiting
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        th = self._frame_thread(self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1")),
                                state="working")
        self.assertTrue(th["unread"]); self.assertFalse(th["replyOwed"])
        th = self._frame_thread(self._thread_side(tline(t + 110, "ct1", "cu1")), state="waiting")
        self.assertFalse(th["unread"]); self.assertTrue(th["replyOwed"])

    def test_unread_follows_what_the_popover_can_render(self):
        # (ii) the popover renders events when it has them, else the msgs projection; unread reads the same
        # source — and with BOTH empty (the popover holds its loader) nothing is unread
        self._seed_thread(seen=self.now - 450)
        saved = km._thread_events
        try:
            km._thread_events = lambda *a, **k: []
            th = self._frame_thread(self._thread_records(), state="")
            self.assertEqual(th["events"], [])
            self.assertTrue(th["unread"], "the msgs projection shows the finished reply — so does the mark")
            saved_m = km._thread_messages
            km._thread_messages = lambda *a, **k: []
            try:
                th = self._frame_thread(self._thread_records(), state="")
                self.assertFalse(th["unread"], "nothing renderable → nothing unread")
                self.assertTrue(th["replyOwed"], "an open thread with no exchange yet still owes its first reply")
            finally:
                km._thread_messages = saved_m
        finally:
            km._thread_events = saved

    def test_a_fresh_thread_owes_a_reply_from_its_first_frame(self):
        # (vii)'s kernel half: pre-fork (reg still carries forkOf) the projections are empty by design —
        # the comment exists, no reply has landed: owed, not unread
        self._seed_thread()
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": PARENT,
             "alive": True, "threadOf": PARENT, "forkOf": PARENT}))
        th = self._frame_thread(self._thread_records(), state="")
        self.assertEqual((th["msgs"], th["events"]), ([], []))
        self.assertTrue(th["replyOwed"])
        self.assertFalse(th["unread"])

    def test_a_compaction_boundary_mid_reply_is_not_a_landing(self):
        # the CLI writes a compact_boundary system record mid-reply; the event model files it as its own
        # ENDED turn (no assistant atoms) — that is bookkeeping, not the reply: still owed, not unread,
        # whether the backend reads compacting or has flapped to ""
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        recs = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 self._tool_result(t + 112, "cr1", "cp1", "cp1"),
                                 boundary(t + 113, "cb1", "cr1"))
        for state in ("compacting", "working"):
            th = self._frame_thread(recs, state=state)
            self.assertFalse(th["unread"], "state=%r" % state)
            self.assertTrue(th["replyOwed"], "a compaction mid-reply is not the reply — state=%r" % state)
        # …but with NO process ("" — T237b B) the compacted, unlanded turn is dead: nothing more is coming
        th = self._frame_thread(recs, state="")
        self.assertFalse(th["replyOwed"], "no process after the compaction → owe nothing")
        # …and the reply landing after the boundary flips both
        th = self._frame_thread(recs + [aline(t + 130, "Jitter prevents thundering herds.", "ca1", parent="cb1")], state="")
        self.assertTrue(th["unread"]); self.assertFalse(th["replyOwed"])

    def test_the_users_interrupt_closes_the_turn_and_owes_nothing(self):
        # the CLI's own "[Request interrupted by user]" record ends the turn: nothing more is coming, so
        # no reply is owed (the mark must not stay green), and what did land is simply there to read
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        stop = uline(t + 115, "[Request interrupted by user]", "ci1", parent="cp1")
        th = self._frame_thread(self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"), stop),
                                state="waiting")
        self.assertFalse(th["replyOwed"], "an interrupted send owes nothing until the next send")
        self.assertTrue(th["unread"], "the partial reply that did land is newer than the watermark")

    def test_agent_landed_after_reads_the_events_arm_when_events_exist(self):
        # the projection the popover renders decides: assistant/tool events newer than the watermark
        ev = lambda kind, t: {"kind": kind, "ts": iso(t), "uuid": "e-%d" % t}
        seen = self.now - 450
        self.assertTrue(km._agent_landed_after([ev("user", seen - 10), ev("assistant", seen + 5)], [], seen))
        self.assertTrue(km._agent_landed_after([ev("tool", seen + 5)], [], seen), "a tool event is agent content")
        self.assertFalse(km._agent_landed_after([ev("assistant", seen - 5)], [{"who": "agent", "t": seen + 9, "text": "x"}], seen),
                         "with events present, msgs are not consulted — the popover shows the events")
        self.assertFalse(km._agent_landed_after([ev("user", seen + 5), {"kind": "branch"}], [], seen),
                         "user events and ts-less inserts are not agent content")
        self.assertTrue(km._agent_landed_after([], [{"who": "agent", "t": seen + 1, "text": "x"}], seen), "no events → the msgs projection")
        self.assertFalse(km._agent_landed_after([], [{"who": "agent", "t": seen, "text": "x"}], seen), "t == seen is already seen")

    # ── the opening message as the popover shows it, and the note of what romp sent (the user 2026-09-30) ──────────
    PRIOR = km._precompact_block(("me", "how should the retry loop back off?"), None,
                                 "/TESTDIR/.claude/projects/-TESTDIR-notes-api/" + PARENT + ".jsonl", "a1")

    def _seed_precompact_thread(self, comment="Why jitter at all?"):
        """A thread as _comment_create leaves one on a passage from before the last compaction: a TIP fork (cutUuid ""),
        the row carrying what romp sent with the comment, the opener itself after the copied history."""
        t = self.now - 500
        sent, said = km._comment_opener_parts("exponential backoff", comment, self.PRIOR)
        recs = [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(self.now - 100, sent + "\n\n" + said, "cu1", parent="a1"),
                aline(self.now - 90, "Jitter prevents thundering herds.", "ca1", parent="cu1")]
        self._write(THREAD, recs)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "", "anchorT": t + 5,
             "exact": "exponential backoff", "status": "open", "sent": sent, "beforeSummary": True,
             "createdT": self.now - 110, "lastSeenT": self.now}]})
        return recs

    def test_the_frame_ships_what_romp_sent_from_the_row(self):
        recs = self._seed_precompact_thread()
        th = self._frame_thread(recs, state="")
        sent, _ = km._comment_opener_parts("exponential backoff", "c", self.PRIOR)
        self.assertEqual(th["sent"], sent, "verbatim, from the row: it survives a reload")
        self.assertTrue(th["sentBeforeSummary"], "the line names the turns around the passage")

    def test_a_thread_from_before_rows_kept_it_reads_what_romp_sent_back_from_its_opener(self):
        # every thread gets the note, the ones created before this change included: their row has no copy, so the
        # frame reads it back from the stored opening message (the identity line and the quote)
        self._seed_thread()
        th = self._frame_thread(self._thread_side(aline(self.now - 380, "Jitter prevents thundering herds.", "ca1",
                                                        parent="cu1")), state="")
        self.assertEqual(th["sent"], km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> exponential backoff")
        self.assertFalse(th["sentBeforeSummary"])
        self.assertEqual(th["msgs"][0]["text"], "Why jitter at all?", "the bubble is still the comment alone")

    def test_a_long_threads_note_outlives_the_projection_cap(self):
        # the msgs projection keeps the newest 40 rows; the opener is read before that cap, so a long thread keeps its note
        self._seed_thread()
        t = self.now - 500
        tail, prev = [], "cu1"
        for i in range(60):
            tail.append(aline(t + 101 + i, "reply %d" % i, "r%d" % i, parent=prev) if i % 2
                        else uline(t + 101 + i, "follow-up %d" % i, "r%d" % i, parent=prev))
            prev = "r%d" % i
        th = self._frame_thread(self._thread_side(*tail), state="")
        self.assertNotIn("Why jitter at all?", [m["text"] for m in th["msgs"]], "the opener is past the cap")
        self.assertTrue(th["sent"].startswith(km._THREAD_FRAME))

    def test_romp_added_nothing_so_there_is_no_note(self):
        # a dispatched thread from before the fork line: its opening message is the caller's prompt, verbatim
        self._seed_thread(records=[uline(self.now - 500, "how should the retry loop back off?", "u1"),
                                   aline(self.now - 495, "Use exponential backoff.", "a1", parent="u1"),
                                   uline(self.now - 400, "Review the tracked edits.", "cu1", parent="a1")])
        th = self._frame_thread([uline(self.now - 500, "how should the retry loop back off?", "u1"),
                                 aline(self.now - 495, "Use exponential backoff.", "a1", parent="u1"),
                                 uline(self.now - 400, "Review the tracked edits.", "cu1", parent="a1")], state="")
        self.assertNotIn("sent", th)
        self.assertNotIn("sentBeforeSummary", th)

    def test_a_dispatched_threads_note_is_the_fork_line(self):
        self._seed_thread()
        recs = [uline(self.now - 500, "how should the retry loop back off?", "u1"),
                aline(self.now - 495, "Use exponential backoff.", "a1", parent="u1"),
                uline(self.now - 400, km._FORK_FRAME + "\n\nReview the tracked edits.", "cu1", parent="a1")]
        th = self._frame_thread(recs, state="")
        self.assertEqual(th["sent"], km._FORK_FRAME)
        self.assertEqual(th["msgs"][0]["text"], "Review the tracked edits.")

    def test_the_popover_events_show_the_opening_message_as_the_comment_alone(self):
        # the events arm (what the popover renders) showed the whole opener: the identity line, the quote (a second
        # time, under the quote the popover already shows) and, for this thread, the context block the note shows
        recs = self._seed_precompact_thread()
        th = self._frame_thread(recs, state="")
        users = [e for e in th["events"] if e.get("kind") == "user"]
        self.assertTrue(users, "the events projection is live in this harness")
        self.assertEqual(users[0]["md"], "Why jitter at all?")
        self.assertEqual(th["msgs"][0], {"who": "you", "text": "Why jitter at all?", "t": self.now - 100})

    def test_an_ordinary_threads_popover_events_show_the_comment_alone_too(self):
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        th = self._frame_thread(self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                                  uline(t + 130, "About this part of the conversation: a later ask",
                                                        "cu2", parent="ca1")), state="")
        users = [e for e in th["events"] if e.get("kind") == "user"]
        self.assertEqual(users[0]["md"], "Why jitter at all?")
        self.assertEqual(users[1]["md"], "About this part of the conversation: a later ask", "only the opener is stripped")

    # Comments that open with a quote, through both popover views, the note and the relay.
    QUOTE_LED = ("> ten percent\nwhy not twenty?", "> ten percent\n\nwhy not twenty?", "> ten percent")

    def _seed_quote_led_thread(self, comment, row_sent, older=False):
        """With `older`, the opener of a thread opened before the identity line: it starts at the quote head."""
        t = self.now - 500
        sent, said = km._comment_opener_parts("exponential backoff", comment)
        self.assertTrue(said.startswith(">"), "premise: the comment opens with a markdown quote")
        if older:
            self.assertTrue(sent.startswith(km._THREAD_FRAME + "\n\n"), "premise: the identity line opens romp's part")
            sent = sent[len(km._THREAD_FRAME + "\n\n"):]
            self.assertTrue(sent.startswith(km._COMMENT_FRAME_HEAD), "premise: the quote head comes next")
        recs = [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 100, sent + "\n\n" + said, "cu1", parent="a1"),
                aline(t + 120, "Twenty stretches the waits.", "ca1", parent="cu1")]
        self._seed_thread(records=recs)
        if row_sent:
            km._comment_update(PARENT, THREAD, sent=sent)
        return recs, sent

    def _assert_quote_led_popover(self, row_sent, kinds=(False,)):
        for older in kinds:
            for comment in self.QUOTE_LED:
                with self.subTest(comment=comment, before_the_identity_line=older):
                    recs, sent = self._seed_quote_led_thread(comment, row_sent, older)
                    self.assertEqual("sent" in km._comment_thread(PARENT, THREAD), row_sent, "premise: the row's copy")
                    th = self._frame_thread(recs, state="")
                    users = [e for e in th["events"] if e.get("kind") == "user"]
                    self.assertTrue(users, "the events projection is live in this harness")
                    self.assertEqual(users[0]["md"], comment, "the events view's bubble keeps the comment's quote")
                    self.assertEqual(th["msgs"][0]["text"], comment, "and so does the msgs view's")
                    self.assertEqual(th["sent"], sent, "the note is what romp added, and only that")

    def test_a_quote_led_comment_in_the_popover_on_a_row_with_its_copy(self):
        self._assert_quote_led_popover(row_sent=True)

    def test_a_quote_led_comment_in_the_popover_on_a_row_without_its_copy(self):
        # an ordinary thread, and one opened before the identity line
        self._assert_quote_led_popover(row_sent=False, kinds=(False, True))

    def test_a_quote_led_comment_in_the_relay_to_the_parent(self):
        be = FakeBackend()
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon)
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        km._push_soon = lambda: None

        def restore():
            km.Sessions.backend_for, km._push_soon = saved
        self.addCleanup(restore)
        # rows with and without romp's copy hand the comment over alike, and so does a thread opened before the identity
        # line, on a row without it
        for comment in self.QUOTE_LED:
            for row_sent, older in ((True, False), (False, False), (False, True)):
                with self.subTest(comment=comment, row_sent=row_sent, before_the_identity_line=older):
                    self._seed_quote_led_thread(comment, row_sent, older)
                    self.assertEqual("sent" in km._comment_thread(PARENT, THREAD), row_sent, "premise: the row's copy")
                    km._thread_msgs_cache.clear()
                    del be.sent[:]
                    self.assertIsNone(km._comment_merge(PARENT, THREAD))
                    self.assertEqual([sid for sid, _ in be.sent], [PARENT], "one handoff, to the parent")
                    body = be.sent[0][1]
                    self.assertIn(ME + comment, body, "the person's turn starts at the comment's quote line")
                    self.assertIn(THEM + "Twenty stretches the waits." + END, body)

    def _seed_passage_thread(self, exact, comment, precompact=False):
        """A thread on `exact` whose row keeps what romp sent: an ordinary one cut at the passage, or with `precompact`
        a tip fork on a passage from before the last compaction, as _seed_precompact_thread leaves one."""
        t = self.now - 500
        sent, said = km._comment_opener_parts(exact, comment, self.PRIOR if precompact else "")
        self.assertEqual(said, comment, "premise: the builder sends the comment as typed")
        ot = self.now - 100 if precompact else t + 100
        recs = [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(ot, sent + "\n\n" + said, "cu1", parent="a1"),
                aline(ot + 20, "Twenty stretches the waits.", "ca1", parent="cu1")]
        self._write(THREAD, recs)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": THREAD, "alive": True,
             "threadOf": PARENT}))
        row = {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "" if precompact else "a1",
               "anchorT": t + 5, "exact": exact, "status": "open", "sent": sent,
               "createdT": self.now - (110 if precompact else 400), "lastSeenT": self.now}
        if precompact:
            row["beforeSummary"] = True
        km._save_comments(PARENT, {"threads": [row]})
        km._built_thread.clear(); km._comments_memo.clear(); km._comment_created_memo.clear()
        return recs, sent

    def _relay_body(self):
        """What _comment_merge hands the parent for the seeded thread, through a fake backend."""
        be = FakeBackend()
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, km._sdk)
        km.Sessions.backend_for = staticmethod(lambda sid: be)
        km._push_soon = lambda: None
        km._sdk = lambda: None
        try:
            km._thread_msgs_cache.clear()
            self.assertIsNone(km._comment_merge(PARENT, THREAD))
        finally:
            km.Sessions.backend_for, km._push_soon, km._sdk = saved
        self.assertEqual([sid for sid, _ in be.sent], [PARENT], "one handoff, to the parent")
        return be.sent[0][1]

    def test_an_unclosed_tag_in_the_passage_still_builds_the_frame(self):
        # A passage holding an unclosed <system-reminder> or <task-notification> tag: the chat build folds each comment
        # here away (premise below). The frame still builds, with each comment whole in the msgs view and the note
        # equal to the row's copy.
        plain, quoted = "Why not twenty?", "> ten percent\nwhy not twenty?"
        self.assertTrue(quoted.startswith(">"), "premise: the second comment opens with a markdown quote")
        for exact in ("before\n<system-reminder>\nnote", "before\n<task-notification>x"):
            for comment in (plain, quoted):
                with self.subTest(exact=exact, comment=comment):
                    recs, sent = self._seed_passage_thread(exact, comment)
                    th = self._frame_thread(recs, state="")
                    users = [e for e in th["events"] if e.get("kind") == "user"]
                    self.assertTrue(users, "the events projection is live in this harness")
                    folded = any(comment in r for r in users[0].get("reminders") or [])
                    self.assertTrue(folded and comment not in users[0]["md"],
                                    "premise: the chat build folded the comment away")
                    self.assertEqual(th["msgs"][0]["text"], comment, "the msgs view shows the comment whole")
                    self.assertEqual(th["sent"], sent, "the note equals the row's copy")

    def test_a_passage_that_ends_in_an_image_chip(self):
        # A passage that ends in an "[Image #N]" chip, on a turn that carries an image (premise below). The msgs view
        # and the relay show each comment whole and the note equals the row's copy. The events view shows each comment
        # whole except in a known case, pinned as it stands: on the thread without a context block it shows the comment
        # that opens with a one-line quote without that line, and the comment that is only a quote line as a text that
        # starts with the identity line and ends with the comment.
        exact = "see the chart [Image #1]"
        plain = "why not twenty? /TESTDIR/notes-api/shot.png"
        quoted = "> ten percent /TESTDIR/notes-api/shot.png\nwhy not twenty?"
        quote_only = "> ten percent /TESTDIR/notes-api/shot.png"
        self.assertTrue(quoted.startswith(">") and quote_only.startswith(">"),
                        "premise: the second and third comments open with a markdown quote")
        for precompact in (False, True):
            for comment in (plain, quoted, quote_only):
                with self.subTest(precompact=precompact, comment=comment):
                    recs, sent = self._seed_passage_thread(exact, comment, precompact=precompact)
                    th = self._frame_thread(recs, state="")
                    users = [e for e in th["events"] if e.get("kind") == "user"]
                    self.assertTrue(users, "the events projection is live in this harness")
                    self.assertTrue(users[0].get("images"), "premise: the turn carries an image")
                    self.assertEqual(th["msgs"][0]["text"], comment, "the msgs view shows the comment whole")
                    self.assertEqual(th["sent"], sent, "the note equals the row's copy")
                    if comment == quoted and not precompact:
                        self.assertEqual(users[0]["md"], "why not twenty?", "the known case, as it stands")
                    elif comment == quote_only and not precompact:
                        md = users[0]["md"]
                        self.assertTrue(md.startswith(km._THREAD_FRAME) and md.endswith(comment),
                                        "the known case, as it stands: it starts with the identity line, ends with "
                                        "the comment")
                    else:
                        self.assertEqual(users[0]["md"], comment, "the events view shows the comment whole")
                    body = self._relay_body()
                    self.assertIn(ME + comment + END, body, "the relay hands the parent the comment whole")
                    self.assertIn(THEM + "Twenty stretches the waits." + END, body)

    def test_a_closed_tag_line_in_the_passage(self):
        # A passage line that is a whole <system-reminder> or <task-notification> block. _split_reminders, which the chat
        # build runs on the message, leaves a bare ">" line in romp's quote (premise below). Each comment shows whole on
        # both views.
        plain, quoted = "Why not twenty?", "> ten percent\nwhy not twenty?"
        self.assertTrue(quoted.startswith(">"), "premise: the second comment opens with a markdown quote")
        seen, real = [], km._comment_strip_frame     # the events view's split (_thread_opener_shown)

        def spy(text):
            seen.append(text)
            return real(text)
        km._comment_strip_frame = spy
        self.addCleanup(setattr, km, "_comment_strip_frame", real)
        for exact in ("before\n<system-reminder>x</system-reminder>\nafter",
                      "before\n<task-notification>x</task-notification>\nafter",
                      "<system-reminder>x</system-reminder>"):
            for comment in (plain, quoted):
                for precompact in (False, True):
                    with self.subTest(exact=exact, comment=comment, precompact=precompact):
                        recs, sent = self._seed_passage_thread(exact, comment, precompact=precompact)
                        shown = km._split_reminders(sent + "\n\n" + comment)[0]
                        quote = shown.split(km._COMMENT_FRAME_HEAD + "\n\n", 1)[1].split("\n\n", 1)[0]
                        self.assertIn(">", quote.split("\n"), "premise: a bare > line in romp's quote")
                        del seen[:]
                        th = self._frame_thread(recs, state="")
                        self.assertIn(shown, seen, "premise: the events view splits that text")
                        users = [e for e in th["events"] if e.get("kind") == "user"]
                        self.assertTrue(users, "the events projection is live in this harness")
                        self.assertEqual(users[0]["md"], comment, "the events view shows the comment whole")
                        self.assertEqual(th["msgs"][0]["text"], comment, "the msgs view shows the comment whole")

    def test_a_comment_that_opens_with_an_image_chip_two_spaces_and_a_pasted_context_block(self):
        # A comment that opens with an "[Image #N]" chip, two spaces and a pasted context block, on a turn that carries
        # an image. The events view splits a text whose comment is a space and then the block (premise below). The block
        # stays the comment's on both views.
        block = self.PRIOR + "\n\nwhy not twenty? /TESTDIR/notes-api/shot.png"
        comment = "[Image #3]  " + block
        seen, real = [], km._comment_strip_frame     # the events view's split (_thread_opener_shown)

        def spy(text):
            seen.append(text)
            return real(text)
        km._comment_strip_frame = spy
        self.addCleanup(setattr, km, "_comment_strip_frame", real)
        recs, sent = self._seed_passage_thread("exponential backoff", comment)
        th = self._frame_thread(recs, state="")
        users = [e for e in th["events"] if e.get("kind") == "user"]
        self.assertTrue(users, "the events projection is live in this harness")
        self.assertTrue(users[0].get("images"), "premise: the turn carries an image")
        self.assertIn(sent + "\n\n " + block, seen, "premise: the events view splits a space, then the block")
        self.assertEqual(users[0]["md"], block, "the events view keeps the block as the comment's")
        self.assertEqual(th["msgs"][0]["text"], comment, "the msgs view shows the comment whole")

    def test_the_built_events_are_not_edited_in_place(self):
        recs = self._seed_precompact_thread()
        self._frame_thread(recs, state="")
        cached = km._built_thread[THREAD][2]   # (key, cut, events, started, chip)
        opener = next(e for e in cached if e.get("kind") == "user")
        self.assertEqual(opener["md"], "Why jitter at all?", "the cache holds the shown copy")
        ev = {"kind": "user", "md": km._comment_first_message("q", "c")}
        out = km._thread_opener_shown([ev])
        self.assertEqual(out[0]["md"], "c")
        self.assertEqual(ev["md"], km._comment_first_message("q", "c"), "the input event is untouched")

    def test_a_tip_fork_starts_at_its_opener_not_on_the_copied_head_cards(self):
        # a pre-compaction thread is a TIP fork: the copied history is floored by its (older) stamps, but the build's
        # head cards (its headCards: the system card, a clear's card) are no part of the thread, and the unstamped
        # system card stayed, so the popover opened on "System context" above the thread's own first message
        self._seed_precompact_thread()
        km._comment_created_memo[THREAD] = self.now - 110
        stamp = lambda t: iso(t)
        head = {"kind": "system", "uuid": "system:head", "model": "Opus 5"}
        built = [head,
                 {"kind": "user", "uuid": "u1", "ts": stamp(self.now - 500), "md": "how should the retry loop back off?"},
                 {"kind": "user", "uuid": "cu1", "ts": stamp(self.now - 100),
                  "md": km._comment_first_message("exponential backoff", "Why jitter at all?", self.PRIOR)},
                 {"kind": "branch", "uuid": "br1"},                  # an unstamped insert AFTER the floor stays
                 {"kind": "assistant", "uuid": "ca1", "ts": stamp(self.now - 90), "text": "Jitter prevents thundering herds."}]
        real = km.build_session
        km.build_session = lambda sid, now, tm=None, **kw: {"events": [dict(e) for e in built], "headCards": [dict(head)]}
        km._built_thread.pop(THREAD, None)
        try:
            evs = km._thread_events(THREAD, "", self.now, {})
        finally:
            km.build_session = real
            km._built_thread.pop(THREAD, None)
        self.assertEqual([e["uuid"] for e in evs], ["cu1", "br1", "ca1"])
        self.assertEqual(evs[0]["md"], "Why jitter at all?")

    def test_a_long_opener_strips_before_the_msgs_cap(self):
        # the msgs projection capped each text at 4,000 characters BEFORE the opener's strip: a long quote plus the
        # context block ran past the cap, and the strip of the capped text never reached the comment
        t = self.now - 500
        long_quote = "\n".join("line %d of a long highlighted passage about the retry loop" % i for i in range(120))
        opener = km._comment_first_message(long_quote, "Why jitter at all?", self.PRIOR)
        self.assertGreater(len(opener), 4000)
        recs = self._seed_precompact_thread()
        recs[2] = uline(self.now - 100, opener, "cu1", parent="a1")
        th = self._frame_thread(recs, state="")
        self.assertEqual(th["msgs"][0]["text"], "Why jitter at all?")

    def test_the_frame_tests_read_unread_from_real_events(self):
        # the events projection is populated in this harness (the stub OWNS the thread), so the frame-level
        # unread verdicts above are decided by the arm the popover renders in production — not msgs alone
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        th = self._frame_thread(self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1")), state="")
        self.assertTrue(th["events"], "build_session's events reach the frame")
        self.assertIn("assistant", [e.get("kind") for e in th["events"]])
        saved = km._thread_messages
        km._thread_messages = lambda *a, **k: [{"who": "you", "text": "Why jitter at all?", "t": t + 100}]
        try:
            th = self._frame_thread(self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1")), state="")
            self.assertTrue(th["unread"], "with events present, unread reads the EVENTS — a blinded msgs projection changes nothing")
        finally:
            km._thread_messages = saved

    def test_a_stop_after_a_mid_reply_compaction_reads_dead_not_owed(self):
        # round-2 review: the idle atoms a stop mints AFTER a trailing compaction boundary fold into the
        # boundary turn; skipping that turn dropped the evidence and left the mark green for good
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        recs = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 self._tool_result(t + 112, "cr1", "cp1", "cp1"),
                                 boundary(t + 113, "cb1", "cr1"))
        states = jd.STATE / "states" / (THREAD + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text("".join(json.dumps(r) + "\n" for r in (
            {"t": t + 100, "state": "working"}, {"t": t + 114, "state": "waiting"}, {"t": t + 116, "state": "idle"})))
        try:
            for state in ("waiting", ""):
                th = self._frame_thread(recs, state=state)
                self.assertFalse(th["replyOwed"], "a thread that stopped after compacting owes nothing — state=%r" % state)
        finally:
            states.unlink()

    def test_prose_that_merely_quotes_a_command_tag_is_the_users_message(self):
        # round-3 review: the wrapper test is ANCHORED (the event model's CMD_WRAP_RE) — a comment or follow-up
        # that quotes <command-name>…</command-name> in prose is what the user said, kept as a "you" row
        t = self.now - 500
        self._seed_thread(seen=self.now)
        quoting = "the CLI writes a <command-name>/model</command-name> record first — why is that a command?"
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                 uline(t + 130, quoting, "cq1", parent="ca1"))
        th = self._frame_thread(recs, state="working")
        self.assertEqual([m["who"] for m in th["msgs"]], ["you", "agent", "you"])
        self.assertTrue(th["replyOwed"], "the user asked again, a process is on it — a reply is owed")

    def test_a_command_confirmation_or_settle_event_is_not_a_landed_reply(self):
        # round-3 review: the events arm skips a slash command's own output (an assistant atom flagged
        # `command`) and the interrupt's null settle — neither is a reply to read
        seen = self.now - 450
        ev = lambda t, **k: dict({"kind": "assistant", "ts": iso(t), "uuid": "e-%d" % t}, **k)
        self.assertFalse(km._agent_landed_after([ev(seen + 5, command=True)], [], seen))
        self.assertFalse(km._agent_landed_after([ev(seen + 5, interruptSettle=True)], [], seen))
        self.assertTrue(km._agent_landed_after([ev(seen + 5)], [], seen))
        # …and through the frame: the reply was READ, then /model ran — no yellow for its confirmation
        t = self.now - 500
        self._seed_thread(seen=t + 125)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                 uline(t + 130, "<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>sonnet</command-args>", "cc1", parent="ca1"),
                                 uline(t + 131, "<local-command-stdout>Set model to sonnet</local-command-stdout>", "cc2", parent="cc1"))
        th = self._frame_thread(recs, state="")
        self.assertTrue(th["events"], "the events projection is live in this harness")
        self.assertTrue(any(e.get("command") for e in th["events"]), "the command's output rides the events flagged")
        self.assertFalse(th["unread"], "a command confirmation is not a reply to read")
        self.assertFalse(th["replyOwed"])

    def test_a_machine_cut_interrupt_record_is_not_the_users_stop(self):
        # round-3 review: a crash/restart resume mints the same "[Request interrupted by user]" record;
        # the backend's machineCut stamp says whose stop it was — romp's cut keeps the reply in progress
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        settle = lambda sr: {"type": "assistant", "timestamp": iso(t + 115), "uuid": "cs1", "parentUuid": "ci1",
                             "message": {"role": "assistant", "model": "<synthetic>", "stop_reason": sr,
                                         "content": [{"type": "text", "text": "No response requested."}]}}
        base = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 uline(t + 115, "[Request interrupted by user]", "ci1", parent="cp1"))
        states = jd.STATE / "states" / (THREAD + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text(json.dumps({"t": t + 120, "machineCut": "restart"}) + "\n")
        try:
            # the shape every restart cut writes: stop record THEN the CLI's null settle (round-4 review: with
            # the settle as the turn's tail the stamp was never consulted and its end_turn read as a landing)
            for recs in (base, base + [settle("end_turn")], base + [settle("stop_sequence")]):
                km._machine_cut_cache.clear() if hasattr(km, "_machine_cut_cache") and hasattr(km._machine_cut_cache, "clear") else None
                th = self._frame_thread(recs, state="working")
                self.assertTrue(th["replyOwed"], "romp cut it and is resuming — still owed (%d records)" % len(recs))
                self.assertFalse(th["unread"], "nothing landed yet (%d records)" % len(recs))
        finally:
            states.unlink()
        recs = base
        # a genuine stop (no machine-cut stamp at or after it) still closes the turn
        km._machine_cut_cache.clear() if hasattr(km, "_machine_cut_cache") and hasattr(km._machine_cut_cache, "clear") else None
        th = self._frame_thread(recs, state="waiting")
        self.assertFalse(th["replyOwed"]); self.assertTrue(th["unread"])

    def test_a_send_fed_to_the_cli_but_unwritten_is_owed_via_its_echo(self):
        # round-4 review: the backend pops a queued send to the CLI within milliseconds, so `_pending` is
        # empty while the CLI still holds it unwritten; the backend's own INPUT ECHO atom spans that window
        # (it lives until the record lands) — counted exactly as the chat's queued indicator counts it
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        echo = {"type": "user", "author": "human", "t": t + 130, "uuid": "echo:1", "_echo_text": "and the cap?"}
        self._State.live = [echo]
        try:
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 1, "the echo is a held send")
            self.assertTrue(th["replyOwed"], "owed while the CLI holds it")
            # a slash command's echo is consumed without a reply — never owed
            self._State.live = [dict(echo, _echo_text="/model sonnet", command=True)]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 0); self.assertFalse(th["replyOwed"])
            # an echo the transcript has caught up on is retired by the same text rule the chat uses
            self._State.live = [echo]
            th = self._frame_thread(recs + [uline(t + 130, "and the cap?", "cu2", parent="ca1"),
                                            aline(t + 140, "Cap it at two minutes.", "ca2", parent="cu2")], state="")
            self.assertEqual(th["queued"], 0, "landed → not held")
            self.assertFalse(th["replyOwed"])
        finally:
            self._State.live = []

    def test_a_slash_sends_echo_is_landed_by_its_wrapper_record(self):
        # a slash or skill command typed into the composer: the CLI records it as a <command-name> wrapper
        # (no verbatim copy of the typed text), which parses to "/deploy staging now" with one space where
        # the sender typed a space and two newlines. The held count reads the landing off that record;
        # before, the echo was held forever and the thread owed a reply that had already come. The record
        # lands in the SAME second as the send: a strictly later human turn would read the echo as
        # overtaken (a loss, not held) whatever its text, and this case is about the landing rule alone.
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        echo = {"type": "user", "author": "human", "t": t + 130, "uuid": "echo:1", "_echo_text": "/deploy \n\nstaging now"}
        wrap = lambda args: ("<command-message>deploy</command-message>\n<command-name>/deploy</command-name>\n"
                             "<command-args>%s</command-args>\n<skill-format>true</skill-format>" % args)
        try:
            self._State.live = [echo]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 1, "held until its record lands")
            th = self._frame_thread(recs + [uline(t + 130, wrap("staging now"), "cc1", parent="ca1", meta=True),
                                            aline(t + 140, "Deploying staging now.", "ca2", parent="cc1")], state="")
            self.assertEqual(th["queued"], 0, "the wrapper record is this send's landing")
            self.assertFalse(th["replyOwed"])
            self._State.live = [echo]
            th = self._frame_thread(recs + [uline(t + 130, wrap("production now"), "cc1", parent="ca1", meta=True),
                                            aline(t + 140, "Deploying production now.", "ca2", parent="cc1")], state="")
            self.assertEqual(th["queued"], 1, "another command's record is not this send's landing")
        finally:
            self._State.live = []

    def test_a_dropped_echo_and_a_repeat_text_send_are_read_right(self):
        # round-5 review, two faults in the echo fold: (1) an echo the backend flagged `dropped` (the send was
        # LOST on a reconnect; the popover shows "never delivered") counted as held → green forever; (2) an
        # echo whose text repeats ANY earlier user text (copied history, "ok", a re-send) read as landed
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        try:
            self._State.live = [{"type": "user", "author": "human", "t": t + 130, "uuid": "echo:1",
                                 "_echo_text": "and the cap?", "dropped": True}]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 0, "a dropped send is not held")
            self.assertFalse(th["replyOwed"], "the kernel adjudicated it lost — no green promise")
            # a re-send of the thread's opening question (its text is already in the copied history)
            repeat = "how should the retry loop back off?"
            self._State.live = [{"type": "user", "author": "human", "t": t + 130, "uuid": "echo:2", "_echo_text": repeat}]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 1, "the earlier identical text is not THIS send's landing")
            self.assertTrue(th["replyOwed"])
            # …until a record written at/after the send carries it
            th = self._frame_thread(recs + [uline(t + 131, repeat, "cu2", parent="ca1")], state="working")
            self.assertEqual(th["queued"], 0, "landed by its own record")
            self.assertTrue(th["replyOwed"], "the user's message is newest and a process is on it — owed by the transcript now")
        finally:
            self._State.live = []

    def test_an_echo_the_backends_scan_found_is_not_held(self):
        # 2026-09-06 (send durability): the backend's boot/spawn scan reads a landing off the transcript
        # itself and records the verdict on the echo (`_landed`) — a delivered send, merely un-pruned. The
        # held count excludes it like a landed one, even though no record in the thread's own projection
        # carries the text yet (the scan read a queued_command attachment the projection takes in later)
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        echo = {"type": "user", "author": "human", "t": t + 130, "uuid": "echo:1", "_echo_text": "and the cap?"}
        try:
            self._State.live = [dict(echo, _landed=True)]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 0, "found by the scan: delivered, nothing is held for it")
            self.assertFalse(th["replyOwed"], "…and no green promise rides on it")
            self._State.live = [dict(echo)]
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 1, "the same echo without the verdict is a held send")
            self.assertTrue(th["replyOwed"])
        finally:
            self._State.live = []

    def test_the_newest_record_uuid_moves_when_a_consumed_slash_command_lands(self):
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        a = self._frame_thread(recs, state="")["lastUuid"]
        b = self._frame_thread(recs + [uline(t + 130, "<command-name>/model</command-name>", "cc1", parent="ca1"),
                                       uline(t + 131, "<local-command-stdout>Set model to sonnet</local-command-stdout>", "cc2", parent="cc1")], state="")["lastUuid"]
        self.assertTrue(a and b and a != b, "a consumed slash command moves the newest record though msgs does not change")

    def test_a_send_the_backend_holds_is_owed_before_any_projection_sees_it(self):
        # round-3 review: a follow-up typed mid-turn waits in the backend until the turn ends; the frame
        # ships `queued` and counts it as owed, so the green never drops between the landing and the write
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        th = self._frame_thread(recs, state="")
        self.assertEqual(th["queued"], 0); self.assertFalse(th["replyOwed"])
        self._State.queued = ["and the cap?"]
        try:
            th = self._frame_thread(recs, state="")
            self.assertEqual(th["queued"], 1)
            self.assertTrue(th["replyOwed"], "a held send is owed")
        finally:
            self._State.queued = []

    # ── T237b (the manager's post-merge review, 2026-09-04) ─────────────────────────────────────────────
    def test_a_dormant_thread_with_an_unended_turn_owes_nothing(self):
        # (B) cut mid-reply with NO interrupt record (SIGKILL / the drain reaping the CLI) and never resumed
        # (boot reconcile skips a thread with no persisted queue): its states tail says working, the backend
        # says "" — no process exists. Not a reply in progress: owe nothing; the partial that landed is
        # what there is to read
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        states = jd.STATE / "states" / (THREAD + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text(json.dumps({"t": t + 100, "state": "working"}) + "\n")
        recs = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 self._tool_result(t + 112, "cr1", "cp1", "cp1"))
        try:
            for _ in range(2):                    # nothing changes between pushes — the verdict must not either
                th = self._frame_thread(recs, state="")
                self.assertFalse(th["replyOwed"], "no process → nothing is coming")
                self.assertTrue(th["unread"], "the partial that landed is newer than the watermark")
            # the same transcript with a live process is a reply in progress
            th = self._frame_thread(recs, state="working")
            self.assertTrue(th["replyOwed"]); self.assertFalse(th["unread"])
        finally:
            states.unlink()

    def test_a_machine_cut_stays_open_before_the_resumed_process_exists(self):
        # (C) a restart-cut thread WITH a persisted queue, during the boot stagger: state "" before the
        # spawn, the interrupt record at/before the machineCut stamp — being resumed, so open: no unread
        # flick on the rail and dot, the wash owed throughout
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        recs = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 uline(t + 115, "[Request interrupted by user]", "ci1", parent="cp1"))
        states = jd.STATE / "states" / (THREAD + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text(json.dumps({"t": t + 120, "machineCut": "restart"}) + "\n")
        self._State.queued = ["and the cap?"]
        try:
            km._machine_cut_cache.clear() if hasattr(km, "_machine_cut_cache") and hasattr(km._machine_cut_cache, "clear") else None
            th = self._frame_thread(recs, state="")
            self.assertTrue(th["replyOwed"]); self.assertFalse(th["unread"], "romp's cut, being resumed — nothing landed")
            # …but a cut thread with NO process and NOTHING queued was never resumed, or its resume died
            # again before writing a record (the crash-loop stand-down): the cut is over — dead, owe nothing,
            # the partial reads as what there is (the manager's pre-read, T237b)
            self._State.queued = []
            km._machine_cut_cache.clear() if hasattr(km, "_machine_cut_cache") and hasattr(km._machine_cut_cache, "clear") else None
            th = self._frame_thread(recs, state="")
            self.assertFalse(th["replyOwed"], "no process, nothing queued: the resume is over or never happened")
            self.assertTrue(th["unread"])
            # a live resumed process keeps it open regardless of the queue
            th = self._frame_thread(recs, state="working")
            self.assertTrue(th["replyOwed"]); self.assertFalse(th["unread"])
        finally:
            self._State.queued = []
            states.unlink()

    def test_live_echoes_are_read_before_the_projection_build_prunes_them(self):
        # (A) build_session → _merge_live_atoms → prune_live runs inside _thread_events; the fold used to read
        # live_atoms AFTER it — a repeat-text send (its text already in the fork-copied history) was retired
        # by the unfloored text match before the fold ever saw it, and the frame read "nothing owed" while
        # the CLI held the send. The fold reads first, and the prune is floored at the send's own time.
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"))
        repeat = "how should the retry loop back off?"     # verbatim in the copied parent history (u1)
        self._State.live = [{"type": "user", "author": "human", "t": t + 130, "uuid": "echo:r", "_echo_text": repeat}]
        try:
            th = self._frame_thread(recs, state="")
            self.assertTrue(th["events"], "the projection was built (and its prune ran)")
            self.assertEqual(th["queued"], 1, "the send the CLI holds is counted — read before the prune, and the prune is floored")
            self.assertTrue(th["replyOwed"])
            self.assertEqual(len(self._State.live), 1, "the floored prune keeps an echo no record at/after its send carries")
            # its own record lands → retired by the same floored rule
            self._State.live = [dict(self._State.live[0])]
            th = self._frame_thread(recs + [uline(t + 131, repeat, "cu2", parent="ca1")], state="")
            self.assertEqual(th["queued"], 0)
            self.assertEqual(self._State.live, [], "landed by a record written after the send")
            # the projection parses the thread's OWN records alone (2026-10-01): a text only the fork-copied
            # history carries is in no parse the prune reads, so even an unfloored prune (an older backend's
            # shape) cannot retire the echo on it
            self._State.live = [{"type": "user", "author": "human", "t": t + 130, "uuid": "echo:r3", "_echo_text": repeat}]
            self._State.unfloored = True
            try:
                th = self._frame_thread(recs, state="")
                self.assertEqual(th["queued"], 1)
                self.assertEqual(len(self._State.live), 1, "the copied history's text retires nothing")
            finally:
                self._State.unfloored = False
            # the read ORDER alone, with an unfloored prune: the projection build retires a repeat-text echo whose
            # text an EARLIER record of the thread's own exchange carries, yet the fold — having read live_atoms
            # first — still counts it
            again = "ok, go on"
            recs2 = recs + [uline(t + 125, again, "cu2", parent="ca1")]
            self._State.live = [{"type": "user", "author": "human", "t": t + 130, "uuid": "echo:r2", "_echo_text": again}]
            self._State.unfloored = True
            try:
                th = self._frame_thread(recs2, state="")
                self.assertEqual(th["queued"], 1, "read before the (unfloored) prune retired it")
                self.assertEqual(self._State.live, [], "…and the prune did retire it — so the order is what kept the count")
            finally:
                self._State.unfloored = False
        finally:
            self._State.live = []

    def test_the_backends_prune_floor_is_the_at_or_after_send_rule(self):
        # (A) the real prune_live: handed text → newest record time, an echo is retired by text only through
        # a record at or after its send; a plain set keeps the older unfloored match for older callers
        be = sb.SdkBackend.__new__(sb.SdkBackend)
        be._live = {THREAD: {"e1": {"uuid": "e1", "t": 130, "_echo_text": "ok"},
                             "e2": {"uuid": "e2", "t": 130, "_echo_text": "and the cap?"}}}
        be._persist_echoes = lambda sid: None
        sb.SdkBackend.prune_live(be, THREAD, set(), {"ok": 100, "and the cap?": 131}, 0)
        self.assertEqual(sorted(be._live[THREAD]), ["e1"], "an earlier 'ok' is not this send's landing; a later record is")
        be._live = {THREAD: {"e1": {"uuid": "e1", "t": 130, "_echo_text": "ok"}}}
        sb.SdkBackend.prune_live(be, THREAD, set(), {"ok"}, 0)
        self.assertNotIn(THREAD, be._live, "a plain set keeps the unfloored match")

    def test_a_broken_thread_tells_the_user_in_the_popover(self):
        # (D) the missing-transcript verdict reached only stderr while the popover held its loader forever;
        # it now rides the frame's error channel, in the user's words
        self._seed_thread()
        (self.proj / (THREAD + ".jsonl")).unlink()
        km._thread_msgs_cache.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_unreadable_warned.clear()
        km._sdk = lambda: self._State("")
        th = km._comments_frame(PARENT)["threads"][0]
        self.assertTrue(th["unreachable"])
        self.assertIn("can't be found", th["error"], "the popover shows the verdict instead of the loader")
        for word in ("kernel", "frame", "card", "board"):
            self.assertNotIn(word, th["error"].lower())

    def test_the_fork_boot_window_is_fresh_not_broken(self):
        # nit: forkOf just spent, the process live, the transcript not yet on disk — owed quietly, not shouted
        import contextlib, io
        self._seed_thread()
        (self.proj / (THREAD + ".jsonl")).unlink()
        km._thread_msgs_cache.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_unreadable_warned.clear()
        km._sdk = lambda: self._State("working")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            th = km._comments_frame(PARENT)["threads"][0]
        self.assertTrue(th["replyOwed"]); self.assertFalse(th["unreachable"]); self.assertEqual(th["error"], "")
        self.assertNotIn("transcript missing", err.getvalue())

    def test_a_sigkill_cut_thread_with_a_queue_reads_resuming_before_its_process_spawns(self):
        # scoped review: a kernel crash / SIGKILL writes NO interrupt record; boot reconcile still resumes a
        # thread WITH a persisted queue (machineCut "restart" stamped, spawn staggered). Until the spawn the
        # backend reads "" — that is a pending resume, not a dead thread: open, no unread flick
        t = self.now - 500
        self._seed_thread(seen=self.now - 450)
        recs = self._thread_side(self._partial_text(t + 110, "Let me check the code.", "cp1", "cu1"),
                                 self._tool_result(t + 112, "cr1", "cp1", "cp1"))
        states = jd.STATE / "states" / (THREAD + ".jsonl")
        states.parent.mkdir(parents=True, exist_ok=True)
        states.write_text(json.dumps({"t": t + 100, "state": "working"}) + "\n"
                          + json.dumps({"t": t + 120, "machineCut": "restart"}) + "\n")
        self._State.queued = ["resume me", "and the cap?"]
        try:
            km._machine_cut_cache.clear() if hasattr(km, "_machine_cut_cache") and hasattr(km._machine_cut_cache, "clear") else None
            th = self._frame_thread(recs, state="")
            self.assertTrue(th["replyOwed"])
            self.assertFalse(th["unread"], "a pending resume is in progress — no yellow flick before the spawn")
        finally:
            self._State.queued = []
            states.unlink()

    def test_a_dormant_thread_whose_last_row_is_the_users_owes_nothing(self):
        # scoped review: cut before any output — the user's follow-up is the newest record, no process, nothing
        # queued: the reply will never come, so the trailing-"you" arm must not re-owe it
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                 uline(t + 130, "and the cap?", "cu2", parent="ca1"))
        th = self._frame_thread(recs, state="")
        self.assertFalse(th["replyOwed"], "no process, nothing queued: nothing is coming")
        th = self._frame_thread(recs, state="working")
        self.assertTrue(th["replyOwed"], "a live process on it is a reply in progress")

    def test_the_echo_is_stamped_before_the_send_is_handed_to_the_cli(self):
        # scoped review: the frame's landing floor is record.t >= echo.t with no skew, which holds by
        # construction only if the echo's stamp is taken BEFORE enqueue hands the text to the CLI
        import inspect
        src = inspect.getsource(sb.SdkBackend.send)
        self.assertLess(src.index("int(time.time())"), src.index("s.enqueue(text)"),
                        "the echo's time is minted before the enqueue, so the CLI's record can never predate it")

    def test_a_slash_command_after_a_landed_reply_owes_nothing(self):
        # the CLI's <command-name>/<local-command-stdout> wrapper records are bookkeeping, not the user's
        # message: they neither show as "you" rows nor re-owe a reply
        t = self.now - 500
        self._seed_thread(seen=self.now)
        recs = self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                 uline(t + 130, "<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args>sonnet</command-args>", "cc1", parent="ca1"),
                                 uline(t + 131, "<local-command-stdout>Set model to sonnet</local-command-stdout>", "cc2", parent="cc1"))
        th = self._frame_thread(recs, state="")
        self.assertEqual([m["who"] for m in th["msgs"]], ["you", "agent"], "the command wrappers are not conversation")
        self.assertFalse(th["replyOwed"])
        self.assertFalse(th["unread"])

    def test_a_missing_transcript_owes_nothing_and_is_shouted(self):
        # round-2 review: `not msgs` used to owe a reply forever over a MISSING transcript, in silence
        import contextlib, io
        self._seed_thread()
        (self.proj / (THREAD + ".jsonl")).unlink()
        km._thread_msgs_cache.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_unreadable_warned.clear()
        km._sdk = lambda: self._State("")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            th = km._comments_frame(PARENT)["threads"][0]
            th2 = km._comments_frame(PARENT)["threads"][0]
        self.assertEqual(th["msgs"], [])
        self.assertFalse(th["replyOwed"], "nothing can land: no green promise")
        self.assertTrue(th["unreachable"], "…and the frame says so, for the client's latch")
        self.assertFalse(th2["replyOwed"])
        self.assertEqual(err.getvalue().count("transcript missing or unreadable"), 1, "shouted once, not per push")
        # …while a genuinely fresh thread (fork pending) still owes its first reply, quietly
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": PARENT,
             "alive": True, "threadOf": PARENT, "forkOf": PARENT}))
        err2 = io.StringIO()
        with contextlib.redirect_stderr(err2):
            th = km._comments_frame(PARENT)["threads"][0]
        self.assertTrue(th["replyOwed"]); self.assertEqual(err2.getvalue(), "")

    def test_a_users_follow_up_owes_a_reply_again(self):
        t = self.now - 500
        self._seed_thread(seen=self.now)
        th = self._frame_thread(self._thread_side(aline(t + 120, "Jitter prevents thundering herds.", "ca1", parent="cu1"),
                                                  uline(t + 200, "and the cap?", "cu2", parent="ca1")), state="working")
        self.assertTrue(th["replyOwed"], "the user's message is the newest and a process is on it — a reply is owed")
        self.assertFalse(th["unread"], "nothing new from the agent since the last look")

    def test_no_store_no_frame(self):
        self.assertIsNone(km._comments_frame(PARENT))

    def test_a_promoted_thread_whose_session_ended_drops_off_the_frame(self):
        # the user 2026-08-13: broke a thread out, closed the resulting session, and the highlight
        # kept claiming "now its own session" — a dead promotion is done, not a stale pointer
        self._write(THREAD, self._thread_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "sidework", "cwd": self.cdir, "lastSid": THREAD, "alive": True}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1",
             "exact": "exponential backoff", "status": "promoted", "promotedName": "sidework",
             "createdT": self.now - 400, "lastSeenT": self.now}]})
        fr = km._comments_frame(PARENT)
        self.assertEqual(len(fr["threads"]), 1, "still alive — the mark and chip stay")
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "sidework", "cwd": self.cdir, "lastSid": THREAD, "alive": False}))
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["threads"], [], "ended — no highlight, no badge, nothing on the message")
        # the row survives in the store: reviving the session from the Fleet should bring it back
        self.assertEqual(len(km._load_comments(PARENT)["threads"]), 1)

    def test_pre_fork_thread_reads_nothing_not_the_parent(self):
        # Until the CLI init spends forkOf, the thread reg's lastSid points at the PARENT
        # transcript — reading it would present the parent's post-anchor turns as the thread's own.
        self._write(PARENT, self._parent_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": PARENT,
             "alive": True, "threadOf": PARENT, "forkOf": PARENT, "forkAt": "a1"}))
        self.assertEqual(km._thread_messages(THREAD, "a1"), [])

    def test_an_injected_marker_message_is_not_the_users(self):
        recs = self._thread_records()
        recs.append({"type": "user", "timestamp": iso(self.now - 80), "uuid": "inj1",
                     "parentUuid": "ca2", "promptSource": "typed",
                     "message": {"role": "user",
                                 "content": "<!-- romp-injected -->Where does this stand?"}})
        self._seed_thread(records=recs)
        msgs = km._thread_messages(THREAD, "a1")
        self.assertNotIn("Where does this stand", json.dumps(msgs))

    def test_frame_surfaces_a_thread_launch_error(self):
        self._seed_thread()
        class _Stub:
            def session_state(self, sid):
                return ""
            def launch_error(self, sid):
                return {"text": "its process couldn't start (a synthetic reason)", "at": 0, "limit": False}
        km._sdk = lambda: _Stub()
        fr = km._comments_frame(PARENT)
        self.assertIn("couldn't start", fr["threads"][0]["error"])

    def test_a_compaction_summary_record_is_not_a_message(self):
        recs = self._thread_records()
        recs.append({"type": "user", "timestamp": iso(self.now - 90), "uuid": "cs1",
                     "parentUuid": "ca2", "isCompactSummary": True,
                     "message": {"role": "user", "content": "summary of the earlier exchange"}})
        self._seed_thread(records=recs)
        msgs = km._thread_messages(THREAD, "a1")
        self.assertNotIn("summary of the earlier exchange", json.dumps(msgs))

    def test_a_large_transcript_reads_from_the_tail_window(self):
        # the copied history can be huge; the side conversation sits at the END, so the projection
        # must come out of the tail window without a full reparse — and be IDENTICAL to it
        filler = [aline(self.now - 550 + i, "filler %d " % i + "x" * 4000, "f%d" % i,
                        parent=("u1" if i == 0 else "f%d" % (i - 1))) for i in range(120)]
        t = self.now - 500
        recs = ([uline(self.now - 600, "how should the retry loop back off?", "u1")] + filler +
                [aline(t + 5, "Use exponential backoff.", "a1", parent="f119"),
                 uline(t + 100, km._comment_first_message("exponential backoff", "Why jitter?"),
                       "cu1", parent="a1"),
                 aline(t + 110, "Jitter prevents thundering herds.", "ca1", parent="cu1")])
        self._seed_thread(records=recs)
        p = self.proj / (THREAD + ".jsonl")
        self.assertGreater(p.stat().st_size, km._THREAD_TAIL_BYTES,
                           "the fixture must actually overflow the tail window")
        msgs = km._thread_messages(THREAD, "a1")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"])
        self.assertEqual(msgs[0]["text"], "Why jitter?")
        self.assertIn("thundering herds", msgs[1]["text"])


# ── the ops: create / reply / resolve / promote ───────────────────────────────────────────────────

class FakeBackend:
    """Records calls; shaped like SdkBackend where the ops touch it."""

    def __init__(self):
        self.calls = []
        self.sent = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="",
             model="", effort="", fast=""):
        self.calls.append(("fork", name, parent_sid, cut_uuid, sid, thread_of))
        self.forked_meta = (model, effort)
        self.forked_fast = fast
        self.forked_bg = bg
        return sid

    def connect(self, sid):
        self.calls.append(("connect", sid))
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid))
        self.sent.append((sid, text))
        return True

    def resume(self, name, sid, cwd=None):
        self.calls.append(("resume", sid))
        return True

    def interrupt(self, sid):
        self.calls.append(("interrupt", sid))
        return True

    def kill(self, sid):
        self.calls.append(("kill", sid))
        return True

    def promote_thread(self, sid, name, bg="", fg=""):
        self.calls.append(("promote", sid, name))
        self.promoted_color = (bg, fg)
        return True


@contextlib.contextmanager
def _views_reads_fault():
    """Fail every byte read of the views store with an EIO for the duration of the block (its proved reader
    reads bytes); everything else reads normally."""
    real_rb, real_rt = Path.read_bytes, Path.read_text
    tgt = str(jd.STATE / "timeline-views.json")

    def rb(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rb(self, *a, **k)

    def rt(self, *a, **k):
        if str(self) == tgt:
            raise OSError(errno.EIO, "injected EIO")
        return real_rt(self, *a, **k)
    Path.read_bytes, Path.read_text = rb, rt
    try:
        yield
    finally:
        Path.read_bytes, Path.read_text = real_rb, real_rt


class CommentOps(CommentBase):
    def setUp(self):
        super().setUp()
        self.be = FakeBackend()
        self._saved_backend_for = km.Sessions.backend_for
        self._saved_ready = km._sdk_ready
        self._saved_sessions = km._sessions
        self._saved_reveal = km._reveal_chat_for
        self._saved_push_now = km._push_session_now
        self._saved_live_map = km._live_map
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._live_map = lambda: {}   # the create/promote doors' live snapshot (names reserved atomically) — never the machine's live sessions
        p = self.parent_path = self._write(PARENT, self._parent_records())
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "parent", "path": str(p), "mtime": self.now}]
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None

    def tearDown(self):
        km.Sessions.backend_for = self._saved_backend_for
        km._sdk_ready = self._saved_ready
        km._sessions = self._saved_sessions
        km._reveal_chat_for = self._saved_reveal
        km._push_session_now = self._saved_push_now
        km._live_map = self._saved_live_map
        self._clear_defaults()   # the module shares one hermetic STATE — never leak across tests
        super().tearDown()

    def _clear_defaults(self):
        for f in ("comment-model", "comment-effort", "comment-fast"):
            try:
                (km.jd.STATE / f).unlink()
            except OSError:
                pass
        km.jd._state_cache.clear()

    def _put_default(self, name, value):
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / name).write_text(value)
        km.jd._state_cache.clear()   # same-second writes share an mtime; the cache is not under test

    def test_create_forks_a_thread_and_sends_the_framed_opener(self):
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why jitter at all?")
        self.assertIsNone(err)
        self.assertTrue(tid)
        kinds = [c[0] for c in self.be.calls]
        self.assertEqual(kinds, ["fork", "connect", "send"])
        fork = self.be.calls[0]
        self.assertEqual(fork[3], "a1", "inclusive cut — the thread holds the highlighted answer")
        self.assertEqual(fork[5], PARENT, "born as a threadOf fork, never a board session")
        self.assertTrue(self.be.sent[0][1].startswith(km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD),
                        "the first message opens by saying what this conversation is, then quotes")
        self.assertIn("> exponential backoff", self.be.sent[0][1])
        self.assertTrue(self.be.sent[0][1].endswith("Why jitter at all?"))
        row = km._comment_thread(PARENT, tid)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["anchorUuid"], "a1")

    def test_create_on_a_pre_compaction_passage_forks_the_tip_and_sends_what_surrounded_it(self):
        # the user 2026-09-30: a passage from before the last compaction opens a thread (it was refused). The thread takes
        # the whole conversation, whose history holds only a summary around the passage, so the opening message adds the
        # turns on either side and where the full transcript is, and labels the comment as the ask; the row keeps what
        # romp added verbatim for the popover's note
        t = self.now - 900
        self._write(PARENT, compacted_records(t))
        err, tid = km._comment_create(PARENT, "pa2", "exponential backoff with a jitter", "Why ten percent?")
        self.assertIsNone(err)
        fork = self.be.calls[0]
        self.assertEqual(fork[0], "fork")
        self.assertEqual(fork[3], "", "a tip fork: the CLI can't cut before the boundary")
        row = km._comment_thread(PARENT, tid)
        self.assertEqual((row["anchorUuid"], row["cutUuid"]), ("pa2", ""))
        self.assertEqual(row["anchorT"], int(em.parse_z(iso(t + 65))), "the square and the mark land on the passage itself")
        self.assertTrue(row["beforeSummary"])
        sent = row["sent"]
        self.assertTrue(sent.startswith(km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> exponential backoff"))
        self.assertIn("\n\n" + km._PRECOMPACT_LEAD + " Right before it, I said:", sent)
        self.assertIn(km._PRECOMPACT_FILE_LEAD + os.path.abspath(str(self.parent_path)) + " (this passage is the entry "
                      "with uuid pa2) if you need more.\n\n" + km._COMMENT_ASK_HEAD, sent)
        self.assertTrue(sent.endswith(km._COMMENT_ASK_HEAD))
        self.assertEqual(self.be.sent[0][1], sent + "\n\nWhy ten percent?",
                         "the opener is exactly what the row says romp sent, then the comment")

    def test_create_on_a_post_compaction_passage_is_unchanged(self):
        self._write(PARENT, compacted_records(self.now - 900))
        err, tid = km._comment_create(PARENT, "pa4", "retry state", "Which endpoints?")
        self.assertIsNone(err)
        self.assertEqual(self.be.calls[0][3], "pa4", "a cut fork at the passage")
        row = km._comment_thread(PARENT, tid)
        self.assertNotIn("beforeSummary", row)
        self.assertEqual(self.be.sent[0][1], km._comment_first_message("retry state", "Which endpoints?"))
        self.assertEqual(row["sent"], km._THREAD_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD + "\n\n> retry state",
                         "every thread's row keeps what romp sent: the identity line and the quote")

    def test_a_dispatched_thread_on_a_pre_compaction_passage_takes_the_same_tip_fork(self):
        # the raw-opener door (a caller-authored prompt behind the fork line) rides the same road when it names a passage
        self._write(PARENT, compacted_records(self.now - 900))
        err, tid = km._comment_create(PARENT, "pa2", "exponential backoff", "Review the cap.", raw_opener=True)
        self.assertIsNone(err)
        self.assertEqual(self.be.calls[0][3], "")
        row = km._comment_thread(PARENT, tid)
        self.assertTrue(row["sent"].startswith(km._FORK_FRAME + "\n\n" + km._COMMENT_FRAME_HEAD))
        self.assertEqual(self.be.sent[0][1], row["sent"] + "\n\nReview the cap.")

    def test_a_dispatched_thread_with_no_passage_keeps_the_fork_line_as_what_romp_sent(self):
        err, tid = km._comment_create(PARENT, "", "a review thread", "Review the tracked edits.", raw_opener=True)
        self.assertIsNone(err)
        self.assertEqual(self.be.sent[0][1], km._FORK_FRAME + "\n\nReview the tracked edits.", "the opener as before")
        self.assertEqual(km._comment_thread(PARENT, tid)["sent"], km._FORK_FRAME)

    def test_break_out_sends_nothing_and_leaves_the_opener_as_it_was(self):
        # break-out promotes the running thread; its first message already said what it is, and a
        # promotion never rewrites or re-sends anything (the history stays exactly as the model saw it)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        opener = self.be.sent[0][1]
        self._promotable(tid)
        self.be.calls.clear()
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        self.assertNotIn("send", [c[0] for c in self.be.calls], "break-out injects no message")
        self.assertEqual(len(self.be.sent), 1)
        self.assertEqual(self.be.sent[0][1], opener)

    def _stub(self, name, value):
        self.addCleanup(setattr, km, name, getattr(km, name))
        setattr(km, name, value)

    def test_a_parent_idle_past_the_discovery_window_still_takes_a_comment(self):
        # the user 2026-09-14: a 4-day-idle session's popover said "no transcript for this session yet"
        # while its chat rendered fine — _sessions' 48h horizon is caption/walk cost, not a permission.
        # The door now resolves an SDK parent through its registry (cwd + lastSid) like build_session does.
        km._sessions = lambda now, window=None, forks=True: []
        self._stub("_sdk", lambda: type("Owner", (), {"owns": staticmethod(lambda sid: sid == PARENT)})())
        real_reg = km._thread_reg
        self._stub("_thread_reg", lambda sid: ({"name": "parent", "lastSid": PARENT, "cwd": str(Path(str(self.parent_path)).parent)}
                                               if sid == PARENT else real_reg(sid)))
        real_tpath = km._thread_transcript_path
        self._stub("_thread_transcript_path",
                   lambda reg, sid: str(self.parent_path) if sid == PARENT else real_tpath(reg, sid))
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why jitter at all?")
        self.assertIsNone(err)
        self.assertEqual([c[0] for c in self.be.calls], ["fork", "connect", "send"])
        self.assertEqual(self.be.calls[0][3], "a1", "the cut resolved against the registry's transcript")
        self.assertEqual(km._comment_thread(PARENT, tid)["name"], "parent-comment-1",
                         "the default name still comes off the resolved row's name")

    def test_a_parent_with_no_transcript_anywhere_is_still_refused(self):
        km._sessions = lambda now, window=None, forks=True: []
        self._stub("_sdk", lambda: None)
        self._stub("_discover_wide", lambda now, window: {})
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertIn("no transcript", err)
        self.assertIsNone(tid)
        self.assertEqual(self.be.calls, [])

    def test_threads_autoname_by_count_and_accept_an_edited_name(self):
        _, tid1 = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        _, tid2 = km._comment_create(PARENT, "a1", "the cap", "And this?")
        self.assertEqual(km._comment_thread(PARENT, tid1)["name"], "parent-comment-1")
        self.assertEqual(km._comment_thread(PARENT, tid2)["name"], "parent-comment-2")
        self.assertEqual(self.be.calls[0][1], "parent-comment-1",
                         "the thread's reg wears the name — a break-out inherits it")
        _, tid3 = km._comment_create(PARENT, "a1", "jitter", "Named.", name="my.question")
        self.assertEqual(km._comment_thread(PARENT, tid3)["name"], "my.question")
        err, _ = km._comment_create(PARENT, "a1", "jitter", "Bad.", name="no spaces!")
        self.assertIn("letters, digits", err)
        fr = km._comments_frame(PARENT)
        self.assertEqual(fr["threads"][0]["name"], "parent-comment-1",
                         "the popover titles threads by name off the frame")

    def test_model_and_effort_picks_ride_the_fork_untouched_by_default(self):
        km._comment_create(PARENT, "a1", "exponential backoff", "Why?", model="haiku", effort="low")
        self.assertEqual(self.be.forked_meta, ("haiku", "low"))
        km._comment_create(PARENT, "a1", "the cap", "Plain.")
        self.assertEqual(self.be.forked_meta, ("", ""), "no pick = inherit; the parent is never touched")

    def test_the_comments_identity_color_rides_create_fork_row_and_frame(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?", color="#a3be8c")
        self.assertEqual(self.be.forked_bg, "#a3be8c")
        self.assertEqual(km._comment_thread(PARENT, tid)["color"], "#a3be8c")
        self.assertEqual(km._comments_frame(PARENT)["threads"][0]["color"], "#a3be8c")
        _, tid2 = km._comment_create(PARENT, "a1", "the cap", "Junk color.", color="not-a-hex")
        self.assertEqual(self.be.forked_bg, "", "a non-hex color falls to the backend's own pick")
        self.assertNotIn("color", km._comment_thread(PARENT, tid2))

    def test_settings_defaults_ride_every_new_thread(self):
        # the user 2026-08-29, who wanted every new comment thread on one model/effort/fast pick
        # regardless of the session it branches from: the kernel-side default applies when the
        # dialog is left untouched
        self._put_default("comment-model", "claude-opus-5")
        self._put_default("comment-effort", "high")
        self._put_default("comment-fast", "on")
        err, _ = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertIsNone(err)
        self.assertEqual(self.be.forked_meta, ("claude-opus-5", "high"))
        self.assertEqual(self.be.forked_fast, "on")

    def test_the_dialog_pick_beats_the_setting(self):
        self._put_default("comment-model", "haiku")
        self._put_default("comment-fast", "on")
        km._comment_create(PARENT, "a1", "exponential backoff", "Why?", model="fable", fast="off")
        self.assertEqual(self.be.forked_meta[0], "fable")
        self.assertEqual(self.be.forked_fast, "off", "a per-thread pick deviates BOTH ways")

    def test_the_session_sentinel_is_a_byte_identical_noop(self):
        # "session" (the shipped default) resolves to the empty override — exactly the fork()
        # arguments a create sent before the setting existed
        for f in ("comment-model", "comment-effort", "comment-fast"):
            self._put_default(f, "session")
        km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertEqual(self.be.forked_meta, ("", ""))
        self.assertEqual(self.be.forked_fast, "")

    def test_absent_files_resolve_the_same_as_the_sentinel(self):
        self.assertEqual(km._comment_launch_prefs(), ("", "", ""))

    def test_the_setters_validate_like_every_kernel_setting(self):
        km._set_comment_model("gpt-99")
        self.assertFalse((km.jd.STATE / "comment-model").exists(), "garbage never reaches the file")
        km._set_comment_model("claude-opus-5")
        self.assertEqual((km.jd.STATE / "comment-model").read_text(), "claude-opus-5")
        km._set_comment_effort("bogus")
        self.assertFalse((km.jd.STATE / "comment-effort").exists())
        km._set_comment_fast("true")
        self.assertFalse((km.jd.STATE / "comment-fast").exists(), '"on"/"session" only — never a bool word')
        km._set_comment_fast("on")
        self.assertEqual((km.jd.STATE / "comment-fast").read_text(), "on")

    def test_a_harness_task_notification_never_renders_as_the_users_words(self):
        recs = self._parent_records()
        t = self.now - 200
        recs += [uline(t, km._comment_first_message("exponential backoff", "Why?"), "cu1", parent="a2"),
                 uline(t + 5, "<task-notification>\n<task-id>b1</task-id>\n<status>stopped</status>"
                       "\n</task-notification>", "tn1", parent="cu1"),
                 aline(t + 10, "Because herds.", "ca1", parent="tn1")]
        self._write(THREAD, recs)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "t", "cwd": self.cdir, "lastSid": THREAD,
             "alive": True, "threadOf": PARENT}))
        msgs = km._thread_messages(THREAD, "a2")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"],
                         "the harness notice is for the AGENT, not a popover bubble")
        self.assertNotIn("task-notification", json.dumps(msgs))

    def test_a_refused_cut_leaves_no_thread_row_behind(self):
        err, tid = km._comment_create(PARENT, "missing-uuid", "text", "comment")
        self.assertTrue(err)
        self.assertIsNone(tid)
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [])
        self.assertEqual(self.be.calls, [])

    def test_reply_reaches_an_open_thread(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        err = km._comment_reply(PARENT, tid, "one more question")
        self.assertIsNone(err)
        self.assertEqual(self.be.sent[-1], (tid, "one more question"))

    def test_a_closed_thread_never_reopens(self):
        # the user's standing rule (2026-09-01): a thread they closed stays on disk and is never
        # revived — replying is refused with the reason (this retires the 2026-08-22/T145
        # "replying IS the reopen gesture" arm, which resumed the CLI and flipped the row open)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_resolve(PARENT, tid)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "resolved")
        n_sent = len(self.be.sent)
        err = km._comment_reply(PARENT, tid, "one more question")
        self.assertTrue(err and "closed" in err, err)
        self.assertNotIn(("resume", tid), self.be.calls, "nothing wakes a closed thread")
        self.assertEqual(len(self.be.sent), n_sent, "nothing is delivered to it either")
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "resolved", "the row stays closed")

    def test_relaying_a_resolved_thread_keeps_it_closed(self):
        # resolved -> relay -> reply must not become the reopen door the direct reply refuses
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_resolve(PARENT, tid)
        saved = km._thread_exchange
        msgs = [{"who": "user", "text": "the ask", "t": 5}, {"who": "assistant", "text": "the answer", "t": 6}]
        km._thread_exchange = lambda tsid, cut, floor_t=0: {"msgs": msgs, "capped": msgs, "sent": "", "gap": "",
                                                            "oldestT": 5}
        try:
            self.assertIsNone(km._comment_merge(PARENT, tid))
        finally:
            km._thread_exchange = saved
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "resolved", "sent back, still closed")
        err = km._comment_reply(PARENT, tid, "one more")
        self.assertTrue(err and "closed" in err, err)

    def test_a_relayed_thread_stays_talkable(self):
        # T145 (the user 2026-08-28): a relay is NOT a close — the explicit reply continues the
        # thread, so only RESOLVED threads fall under the never-reopen rule
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="merged")
        err = km._comment_reply(PARENT, tid, "afterthought")
        self.assertIsNone(err)
        self.assertIn(("resume", tid), self.be.calls, "the reply is the explicit gesture that wakes it")
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "open")

    def test_delete_interrupts_the_inflight_reply_before_the_kill(self):
        # deleting a thread mid-generation must STOP the work, not just its cue (the user 2026-08-17)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_delete(PARENT, tid)
        kinds = [c[0] for c in self.be.calls if c[0] in ("interrupt", "kill")]
        self.assertEqual(kinds, ["interrupt", "kill"], "cut the turn first, then shut the CLI down")

    def test_delete_removes_the_row(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_resolve(PARENT, tid)
        km._comment_delete(PARENT, tid)
        self.assertIsNone(km._comment_thread(PARENT, tid))

    def test_promote_seeds_before_names_and_floors_past_the_exchange(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        t = self.now - 200
        self._write(tid, [uline(self.now - 600, "how should the retry loop back off?", "u1"),
                          aline(self.now - 595, "Use exponential backoff.", "a1", parent="u1"),
                          uline(t, "opener", "cu1", parent="a1"),
                          aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))
        order = []
        saved_seed = km._seed_fork_stores
        km._seed_fork_stores = lambda *a, **k: order.append("seed") or saved_seed(*a, **k)
        real_promote = self.be.promote_thread
        self.be.promote_thread = lambda *a, **k: order.append("names") or real_promote(*a, **k)
        try:
            err = km._comment_promote(PARENT, tid, "sidework")
        finally:
            km._seed_fork_stores = saved_seed
        self.assertIsNone(err)
        self.assertEqual(order, ["seed", "names"],
                         "judge seeds must land before the names/ write — the fork() contract")
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoted")
        self.assertEqual(km._comment_thread(PARENT, tid)["promotedName"], "sidework")
        floor = jd.episode_floor(tid)
        self.assertIsNotNone(floor)
        self.assertGreaterEqual(floor, t + 10,
                                "the floor sits at the thread's leaf — the popover exchange is settled history")

    def _promotable(self, tid):
        """The transcript + reg a thread needs before _comment_promote will touch it."""
        t = self.now - 200
        self._write(tid, [uline(t, "opener", "cu1"), aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))

    def test_promote_keeps_the_threads_own_color(self):
        # the color the dialog suggested rides create → row → PROMOTE (the user 2026-08-19: it used
        # to be re-picked at break-out, so the session never matched the color the thread had worn)
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?", color="#F9D849")
        self._promotable(tid)
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        self.assertEqual(self.be.promoted_color, ("#F9D849", "black"),
                         "the row's color, with the palette's readable fg — never a fresh pick")

    def test_promote_picks_fresh_only_for_a_colorless_row(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self._promotable(tid)
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        bg, fg = self.be.promoted_color
        self.assertTrue(bg.startswith("#") and fg in ("white", "black"),
                        "a pre-color row still gets a real identity")

    def test_promote_refuses_a_bad_name(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        err = km._comment_promote(PARENT, tid, "bad name!")
        self.assertIn("letters, digits", err)

    def _tag_members(self, name):
        km._flags_cache.clear()
        t = next((t for t in km._timeline_views()["tags"] if t["name"] == name), None)
        return sorted(m["sid"] for m in (t or {"members": []})["members"])

    def test_a_thread_inherits_no_tags_at_create_but_does_when_promoted(self):
        # tab groups on tags (the user 2026-09-04): a comment thread has no tab, so tagging its hidden
        # sid at create would only inflate the member lists — it inherits the parent's tags at the
        # moment it BECOMES a tab (promote), before the connect that precedes the direct push
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [PARENT]},
                                                          {"id": "g2", "name": "other", "members": ["x"]}]})
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertEqual(self._tag_members("pool"), [PARENT], "the thread fork stays out of the parent's tags")
        self._promotable(tid)
        seen_at_connect = []
        real_connect = self.be.connect
        self.be.connect = lambda sid: (seen_at_connect.append(self._tag_members("pool")), real_connect(sid))[1]
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        self.assertEqual(self._tag_members("pool"), sorted([PARENT, tid]), "promoted = a tab now, in the parent's group")
        self.assertEqual(self._tag_members("other"), ["x"], "a tag the parent is not in is untouched")
        self.assertIn(tid, seen_at_connect[0], "membership landed before connect, ahead of the direct push")

    def test_a_faulting_views_store_leaves_the_promotion_standing_and_says_it_did_not_inherit(self):
        # the views store's proved read: a fault under the inherit RAISES (never an empty parent read off a
        # fabricated store), and the promotion site's boundary keeps the spawn -- the thread is a session by
        # then -- and says what did not happen: one refused-kind notice naming the child, nothing written
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [PARENT]}]})
        km._flags_cache.clear()                      # a cold display cache: the fault must be met by the READ, not bypassed by a hit
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self._promotable(tid)
        p = jd.STATE / "timeline-views.json"
        before = p.read_bytes()
        connected = []
        real_connect = self.be.connect
        self.be.connect = lambda sid: (connected.append(sid), real_connect(sid))[1]
        notices, saved = [], km._sync_notice
        km._sync_notice = lambda text, ok=True, kind="sync": notices.append((text, ok, kind))
        try:
            with _views_reads_fault():
                self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"), "the promotion stands")
        finally:
            km._sync_notice = saved
        self.assertEqual(connected, [tid], "the promoted session was connected as ever")
        self.assertEqual(p.read_bytes(), before, "nothing was written to the store")
        rows = [(t, k) for t, ok, k in notices if not ok and "did not inherit" in t]
        self.assertEqual(len(rows), 1, "one notice for the one thing that did not happen")
        self.assertIn('"sidework" did not inherit the tags of', rows[0][0])
        self.assertIn("the tag store could not be read (read failed: [Errno 5]", rows[0][0])
        self.assertEqual(rows[0][1], "refused")
        self.assertEqual(self._tag_members("pool"), [PARENT], "the parent keeps its tag; the thread is not in it")

    def test_a_session_spawned_from_inside_a_thread_inherits_the_threads_parents_tags(self):
        # a thread's CLI carries the THREAD's sid as ROMP_SID, so `romp new` run from its shell names
        # the thread as parent; the thread holds no tags (no tab), but it lives in the parent's chat —
        # _resolve_parent_sid walks up to the threadOf session, and the child lands in ITS group
        CHILD = "66666666-7777-8888-9999-000000000000"
        km._flags_cache.clear()
        km._set_timeline_views({"active": "all", "tags": [{"id": "g1", "name": "pool", "members": [PARENT]}]})
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self._promotable(tid)                       # the thread's reg, threadOf = PARENT
        reg = lambda sid: json.loads((jd.SDKDIR / (sid + ".json")).read_text()) if (jd.SDKDIR / (sid + ".json")).exists() else None
        self.be.owns = lambda sid: reg(sid) is not None                       # SdkBackend.owns: a reg exists
        self.be.thread_of = lambda sid: str((reg(sid) or {}).get("threadOf") or "")   # SdkBackend.thread_of
        self.assertEqual(km._resolve_parent_sid(tid, {}), (PARENT, None), "the thread resolves to the session it is of")
        names, err = km._tag_new_session(CHILD, km._resolve_parent_sid(tid, {})[0], [])
        self.assertIsNone(err)
        self.assertEqual(names, ["pool"], "the child inherits from the session the thread is of")
        self.assertEqual(self._tag_members("pool"), sorted([PARENT, CHILD]), "…and the thread itself still holds none")

    def test_the_promoting_latch_refuses_resolve_delete_and_reply(self):
        # promote seeds for seconds on a big transcript; ops landing in that window must refuse
        # THROUGH the CAS, or a racing resolve kills the just-promoted board session
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoting")
        calls_before = list(self.be.calls)
        for op in (km._comment_resolve, km._comment_delete):
            err = op(PARENT, tid)
            self.assertIn("becoming its own session", err)
        err = km._comment_reply(PARENT, tid, "hello?")
        self.assertIn("becoming its own session", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoting")
        self.assertEqual(self.be.calls, calls_before, "no kill, no send — the latch holds")

    def test_a_failed_promote_reverts_the_latch(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        t = self.now - 200
        self._write(tid, [uline(t, "opener", "cu1"), aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))
        saved = km._seed_fork_stores
        km._seed_fork_stores = lambda *a, **k: "seeding failed on purpose"
        try:
            err = km._comment_promote(PARENT, tid, "sidework")
        finally:
            km._seed_fork_stores = saved
        self.assertIn("seeding failed", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "open",
                         "the latch must never stick on a failed promote")

    def test_seed_fork_stores_refuses_a_vanished_cut(self):
        p = self.proj / (PARENT + ".jsonl")
        err = km._seed_fork_stores(PARENT, THREAD, str(p), "gone-uuid")
        self.assertIn("isn't in the conversation anymore", err)

    def test_resolve_refuses_a_promoted_thread_so_delete_can_never_kill_its_session(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoted", promotedName="sidework")
        err = km._comment_resolve(PARENT, tid)
        self.assertIn("its own session", err)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoted",
                         "resolve must never overwrite promoted — that hands delete a session to kill")
        km._comment_delete(PARENT, tid)                 # removing the highlight row is fine…
        self.assertNotIn(("kill", tid), self.be.calls)  # …but the promoted session is never killed
        self.assertIsNone(km._comment_thread(PARENT, tid))

    def test_a_missing_cut_shows_nothing_never_the_copied_history(self):
        self._write(THREAD, self._parent_records())
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir,
             "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        self.assertEqual(km._thread_messages(THREAD, "not-in-transcript"), [])

    def test_ending_the_parent_sweeps_its_threads_clis(self):
        _, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        km._comment_update(PARENT, tid, status="promoted", promotedName="kept")
        _, tid2 = km._comment_create(PARENT, "a1", "exponential backoff", "And the cap?")
        km._comment_kill_all(PARENT, self.be)
        self.assertIn(("kill", tid2), self.be.calls, "open threads die with their only surface")
        self.assertNotIn(("kill", tid), self.be.calls, "a promoted thread is a board session — untouched")

    def test_a_failed_create_kills_the_half_born_reg(self):
        self.be.send = lambda sid, text: (_ for _ in ()).throw(RuntimeError("boom"))
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertIn("boom", err)
        self.assertIsNone(tid)
        self.assertEqual(km._load_comments(PARENT).get("threads", []), [])
        self.assertIn(("kill",), {c[:1] for c in self.be.calls},
                      "the forked reg/CLI must not outlive the removed row")

    def test_drive_ops_are_registered(self):
        src = (Path(BIN) / "romp-kernel").resolve().read_text()
        for op in ("commentCreate", "commentReply", "commentResolve", "commentDelete",
                   "commentSeen", "commentPromote"):
            self.assertIn('"%s"' % op, src)


class ForkDoorsLeaveTheHistoryToTheCli(CommentBase):
    """Every door that makes a session from a copy of another (a comment thread, a dispatched thread, the plain
    fork, and break-out) against the REAL SdkBackend, no CLI: romp rewrites no transcript. The copy is the
    CLI's, made from the parent's own file (resume + fork_session + the cut), so every message record reaches
    the fork exactly as the parent's (prompt caching matches on the prefix; newer models refuse an edited
    history). The parent's background work is kept out by the CLI's source-alive boundary, not by editing
    anything, and what the new session is rides in its first NEW message (the user 2026-09-24).

    Measured on 2.1.280 against a live fork: every copied message's content is the parent's; the CLI changes
    only its own bookkeeping fields (slug, promptId, usage zeroed at the compaction seam, neutralizedByFork on
    refusal-fallback system rows), and the queue-operation rows at the head of a fork's file are the fork's
    own enqueue of its first message, never copies of the parent's."""

    OPENER = "Please review the tracked edits in this note and reply in the review thread."

    def setUp(self):
        super().setUp()
        import sys as _sys
        import types as _types
        (jd.STATE / "session-hosts").write_text("off")      # a minted state root pins hosts off (repo CLAUDE.md)
        if "claude_agent_sdk" not in _sys.modules and not sb.sdk_importable():
            fake = _types.ModuleType("claude_agent_sdk")
            fake.HookMatcher = lambda **kw: _types.SimpleNamespace(**kw)
            _sys.modules["claude_agent_sdk"] = fake
            self.addCleanup(_sys.modules.pop, "claude_agent_sdk", None)
        self._stub_sb("_fetch_key_fast_org", lambda key: None)
        self.rbe = sb.SdkBackend(jd.STATE, "/bin/true", lambda *a, **k: None, log=lambda *a, **k: None)
        self.sent = []
        self.rbe._ensure = lambda sid: _types.SimpleNamespace(
            enqueue=lambda text, qid=None, qts=None, paths=None: self.sent.append((sid, text)))
        self.rbe.connect = lambda sid: True
        self.rbe.spawn("parent", self.cdir, sid=PARENT)
        self.parent_path = self._write(PARENT, self._parent_records())
        self.cli_parent = Path(sb.transcript_path(self.cdir, PARENT))   # where the CLI itself would read it
        self.cli_parent.parent.mkdir(parents=True, exist_ok=True)
        self.cli_parent.write_bytes(self.parent_path.read_bytes())
        self.before = self.parent_path.read_bytes()
        for name, value in (("_sdk_ready", lambda: True), ("_live_map", lambda: {}),
                            ("_reveal_chat_for", lambda client, msg: None), ("_push_session_now", lambda sid: None),
                            ("_mark_views_dirty", lambda: None), ("NAMES", jd.NAMES),
                            ("_sessions", lambda now, window=None, forks=True: [
                                {"sid": PARENT, "name": "parent", "path": str(self.parent_path), "mtime": self.now}])):
            self._stub_km(name, value)
        self._stub_km_attr(km.Sessions, "backend_for", staticmethod(lambda sid: self.rbe))
        self._stub_km_attr(km.Sessions, "live", staticmethod(lambda: {PARENT: {}}))

    def _stub_km(self, name, value):
        self.addCleanup(setattr, km, name, getattr(km, name))
        setattr(km, name, value)

    def _stub_sb(self, name, value):
        self.addCleanup(setattr, sb, name, getattr(sb, name))
        setattr(sb, name, value)

    def _stub_km_attr(self, obj, name, value):
        self.addCleanup(setattr, obj, name, obj.__dict__[name])
        setattr(obj, name, value)

    def _reg(self, sid):
        return json.loads((jd.SDKDIR / (sid + ".json")).read_text())

    def _new_sid(self, **want):
        for f in sorted(jd.SDKDIR.glob("*.json")):
            r = json.loads(f.read_text())
            if r.get("forkOf") == PARENT and all(r.get(k) == v for k, v in want.items()):
                return r["sid"]
        self.fail("no fork reg")

    def _assert_history_left_to_the_cli(self, sid, cut):
        kw = self.rbe._options(sb.SdkSession(self.rbe, self._reg(sid)), dict)
        self.assertEqual((kw.get("resume"), kw.get("fork_session"), kw.get("session_id")), (PARENT, True, sid),
                         "the CLI copies the parent's own file into the new session")
        self.assertEqual((kw.get("extra_args") or {}).get("resume-session-at", ""), cut)
        self.assertTrue(kw["env"][sb.RESUME_SOURCE_ALIVE_ENV].startswith(sid + "|"),
                        "the parent's background work is kept out by the CLI's boundary, not by an edit")
        self.assertEqual(self.parent_path.read_bytes(), self.before, "the parent's transcript is untouched")
        self.assertEqual(self.cli_parent.read_bytes(), self.before)
        self.assertFalse((self.proj / (sid + ".jsonl")).exists(), "romp writes no transcript for the fork")
        self.assertFalse(os.path.exists(sb.transcript_path(self.cdir, sid)))

    def test_a_comment_thread(self):
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why jitter at all?")
        self.assertIsNone(err)
        self._assert_history_left_to_the_cli(tid, "a1")
        self.assertEqual(self.sent, [(tid, km._comment_first_message("exponential backoff", "Why jitter at all?"))])
        self.assertTrue(self.sent[0][1].startswith(km._THREAD_FRAME))

    def test_a_dispatched_thread(self):
        res = km._fork_comment_request({"id": PARENT, "text": self.OPENER})
        self.assertTrue(res.get("ok"), res)
        self._assert_history_left_to_the_cli(res["forkId"], "")
        self.assertEqual(self.sent, [(res["forkId"], km._FORK_FRAME + "\n\n" + self.OPENER)])

    def test_the_plain_fork(self):
        self.assertIsNone(km._fork_session(PARENT, "u2", "api-fork"))
        sid = self._new_sid(name="api-fork")
        self._assert_history_left_to_the_cli(sid, "a1")
        self.assertEqual(self.sent, [], "the eager connect sends nothing: the line waits for the first message")
        self.assertTrue(self.rbe.send(sid, "Now add pagination.", user=True))
        self.assertEqual(self.sent, [(sid, km._FORK_FRAME + "\n\nNow add pagination.")])

    def test_break_out(self):
        err, tid = km._comment_create(PARENT, "a1", "exponential backoff", "Why?")
        self.assertIsNone(err)
        self._assert_history_left_to_the_cli(tid, "a1")      # the fork launch (stamps the CLI boundary)
        # the CLI's copy, as it would have written it, plus the thread's own exchange; the init spent the flags
        t = self.now - 200
        thread_path = self._write(tid, self._parent_records()[:2] + [
            uline(t, self.sent[0][1], "cu1", parent="a1"), aline(t + 10, "Jitter spreads the retries.", "ca1", "cu1")])
        self.rbe._update_reg(tid, forkOf="", forkAt="", lastSid=tid)
        thread_before = thread_path.read_bytes()
        self.sent.clear()
        self.assertIsNone(km._comment_promote(PARENT, tid, "sidework"))
        self.assertEqual(self.sent, [], "break-out injects nothing and re-sends nothing")
        self.assertEqual(thread_path.read_bytes(), thread_before, "the thread's history, first message included, stands")
        self.assertEqual(self.parent_path.read_bytes(), self.before)
        kw = self.rbe._options(sb.SdkSession(self.rbe, self._reg(tid)), dict)
        self.assertEqual(kw["resume"], tid)
        self.assertTrue(kw["env"][sb.RESUME_SOURCE_ALIVE_ENV].startswith(tid + "|"),
                        "the broken-out session still skips the parent's copied history on every connect")


class ExchangeLatchReplacedThePushCount(unittest.TestCase):
    """T102 (the user 2026-08-26): the push-count settle (settledPushes / _comment_settle_step) is
    RETIRED — it was a proxy for the real ending event, and it broke both ends: the fork-birth
    frames read all-quiet so the create-window pulse died until the CLI booted, and any stall in
    the 0→1→2 stepping parked the pulse green forever. The client's pulse is exchange-scoped now —
    latched at the send gesture, cleared by the agent's reply RECORD arriving in msgs — so the
    frame carries the exchange's records (msgs) and no per-push counter."""

    def test_the_push_count_is_gone_root_and_branch(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertNotIn("settledPushes", src.replace("settledPushes — is RETIRED", ""),
                         "no counter rides the frame (the tombstone comment is the one mention)")
        self.assertNotIn("_comment_settle_step", src)
        ui = open(os.path.join(os.path.dirname(HERE), "ui", "webview", "comments.ts")).read()
        self.assertNotIn("settledPushes", ui)
        self.assertNotIn("SETTLE_CONFIRM_PUSHES", ui)

    def test_the_frame_still_carries_the_exchange_records_and_epoch(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('"sinceEpoch": since_ms,', src)
        self.assertIn('"msgs": msgs, "events": events', src)


# ── the /fork-comment + /fork-promote route brains ───────────────────────────────────────────────
# Parallel review dispatch (the user 2026-08-31): an external local tool (the Obsidian track-changes
# plugin) POSTs /fork-comment to open a transcript-comment-STYLE thread on a session — TIP-anchored
# (no highlighted record exists on that door) with the caller's own text as the opening message,
# VERBATIM — and /fork-promote to break a thread out by forkId alone. The brains are factored from
# the routes (_compact_request precedent), so these run without HTTP.

class ForkCommentRoutes(CommentBase):
    def setUp(self):
        super().setUp()
        self.be = FakeBackend()
        self._saved_backend_for = km.Sessions.backend_for
        self._saved_live = km.Sessions.live
        self._saved_ready = km._sdk_ready
        self._saved_sessions = km._sessions
        self._saved_push_now = km._push_session_now
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km.Sessions.live = staticmethod(lambda: {})   # hermetic: never consult the machine's live sessions
        km._sdk_ready = lambda: True
        # km.NAMES is bound at import (module-scope constant) — _rebind_state moves only jd's copy,
        # so _name_of would read the import-time root and miss the per-test registry entry
        self._saved_names = km.NAMES
        km.NAMES = jd.NAMES
        p = self._write(PARENT, self._parent_records())
        (jd.NAMES / PARENT).write_text("parent\t%s" % self.cdir)
        km._sessions = lambda now, window=None, forks=True: [
            {"sid": PARENT, "name": "parent", "path": str(p), "mtime": self.now}]
        km._push_session_now = lambda sid: None

    def tearDown(self):
        km.Sessions.backend_for = self._saved_backend_for
        km.Sessions.live = self._saved_live
        km.NAMES = self._saved_names
        km._sdk_ready = self._saved_ready
        km._sessions = self._saved_sessions
        km._push_session_now = self._saved_push_now
        for f in ("comment-model", "comment-effort", "comment-fast"):
            try:
                (km.jd.STATE / f).unlink()
            except OSError:
                pass
        km.jd._state_cache.clear()
        super().tearDown()

    OPENER = ("Please review the tracked edits in this note and reply in the review thread.\n\n"
              "Use the thread id t-123 with every reply.")

    def test_fork_comment_creates_a_tip_thread_with_the_text_verbatim(self):
        res = km._fork_comment_request({"id": PARENT, "text": self.OPENER,
                                        "meta": {"thread": "t-123", "note": "notes/demo.md"}})
        self.assertTrue(res.get("ok"), res)
        tid = res["forkId"]
        self.assertEqual([c[0] for c in self.be.calls], ["fork", "connect", "send"])
        self.assertEqual(self.be.calls[0][3], "", "TIP fork — no highlighted record on this door")
        self.assertEqual(self.be.calls[0][5], PARENT, "born as a threadOf fork, never a board session")
        self.assertEqual(self.be.sent[0][1], km._FORK_FRAME + "\n\n" + self.OPENER,
                         "the caller authored the whole prompt — sent verbatim behind the line that says what "
                         "this conversation is (2026-09-24), never re-framed as a quote")
        self.assertNotIn(km._COMMENT_FRAME_HEAD, self.be.sent[0][1])
        self.assertEqual(km._comment_strip_frame(self.be.sent[0][1]), self.OPENER,
                         "the popover shows the caller's prompt alone")
        row = km._comment_thread(PARENT, tid)
        self.assertEqual(row["status"], "open")
        self.assertEqual(row["cutUuid"], "")
        self.assertEqual(row["anchorUuid"], "")
        self.assertEqual(row["meta"], {"thread": "t-123", "note": "notes/demo.md"})
        self.assertEqual(row["exact"], "notes/demo.md",
                         "the popover's about-line names the note under review")
        self.assertEqual(row["name"], "parent-comment-1", "the standard autoname idiom")

    def test_fork_comment_thread_reaches_the_popover_frame(self):
        res = km._fork_comment_request({"id": PARENT, "text": self.OPENER, "meta": {"thread": "t-1"}})
        fr = km._comments_frame(PARENT)
        self.assertEqual([t["tid"] for t in fr["threads"]], [res["forkId"]],
                         "a dispatched review thread is a normal popover thread")

    def test_fork_comment_resolves_a_live_session_name(self):
        km.Sessions.live = staticmethod(lambda: {PARENT: {}})
        res = km._fork_comment_request({"name": "parent", "text": self.OPENER})
        self.assertTrue(res.get("ok"), res)

    def test_fork_comment_applies_the_default_comment_settings(self):
        km.jd.STATE.mkdir(parents=True, exist_ok=True)
        (km.jd.STATE / "comment-model").write_text("haiku")
        km.jd._state_cache.clear()
        km._fork_comment_request({"id": PARENT, "text": self.OPENER})
        self.assertEqual(self.be.forked_meta[0], "haiku",
                         "the gear trio governs these forks exactly like dialog-created threads")

    def test_fork_comment_refusals_are_honest(self):
        self.assertEqual(km._fork_comment_request(None)["_status"], 400)
        self.assertEqual(km._fork_comment_request({"id": PARENT})["_status"], 400)
        self.assertEqual(km._fork_comment_request({"id": PARENT, "text": "  "})["_status"], 400)
        res = km._fork_comment_request({"name": "no-such-session", "text": self.OPENER})
        self.assertEqual(res["_status"], 404)
        self.assertIn("no session named", res["error"])
        km.Sessions.backend_for = staticmethod(lambda sid: object())   # a backend with no fork machinery (the unowned route's shape)
        res = km._fork_comment_request({"id": PARENT, "text": self.OPENER})
        self.assertNotIn("_status", res)
        self.assertIn("another backend", res["error"])

    def test_fork_comment_holds_the_postal_isolation_gate(self):
        saved_shaped, saved_iso = km._postal_shaped, km._postal_isolated
        km._postal_shaped, km._postal_isolated = (lambda t: True), (lambda s: True)
        try:
            res = km._fork_comment_request({"id": PARENT, "text": self.OPENER})
        finally:
            km._postal_shaped, km._postal_isolated = saved_shaped, saved_iso
        self.assertFalse(res.get("ok"))
        self.assertIn("isolation", res["error"])
        self.assertEqual(self.be.calls, [], "refused before any fork")

    def test_fork_comment_forwards_a_remote_target_and_passes_the_forkId_through(self):
        saved_host, saved_fwd = km._host_for_sid, km._remote_forward
        sent = []
        km._host_for_sid = lambda sid: {"host": "TESTHOST", "local_port": 1, "token": ""}
        km._remote_forward = lambda r, path, body: sent.append((path, body)) or {"ok": True, "forkId": "far-tid"}
        try:
            res = km._fork_comment_request({"id": PARENT, "text": self.OPENER, "meta": {"note": "n.md"}})
            self.assertEqual(res, {"ok": True, "forkId": "far-tid"})
            self.assertEqual(sent, [("/fork-comment", {"id": PARENT, "text": self.OPENER,
                                                       "meta": {"note": "n.md"}})])
            km._remote_forward = lambda r, path, body: None   # the tunnel didn't answer — say so
            res = km._fork_comment_request({"id": PARENT, "text": self.OPENER})
            self.assertFalse(res.get("ok"))
            self.assertIn("TESTHOST", res["error"])
        finally:
            km._host_for_sid, km._remote_forward = saved_host, saved_fwd
        self.assertEqual(self.be.calls, [], "a remote target never forks locally")

    def _promotable(self, tid):
        t = self.now - 200
        self._write(tid, [uline(t, "opener", "cu1"), aline(t + 10, "reply", "ca1", parent="cu1")])
        (jd.SDKDIR / (tid + ".json")).write_text(json.dumps(
            {"sid": tid, "name": "thread-x", "cwd": self.cdir,
             "lastSid": tid, "alive": True, "threadOf": PARENT}))

    def test_fork_promote_materializes_under_the_threads_own_name(self):
        tid = km._fork_comment_request({"id": PARENT, "text": self.OPENER})["forkId"]
        self._promotable(tid)
        res = km._fork_promote_request({"forkId": tid})
        self.assertEqual(res, {"ok": True, "name": "parent-comment-1"})
        self.assertIn(("promote", tid, "parent-comment-1"), self.be.calls)
        self.assertEqual(km._comment_thread(PARENT, tid)["status"], "promoted")
        again = km._fork_promote_request({"forkId": tid})
        self.assertEqual(again, {"ok": True, "name": "parent-comment-1"},
                         "already promoted = idempotent success, never a refusal")

    def test_fork_promote_refusals_ride_non_2xx(self):
        # the shipped caller reads ANY 2xx JSON as success, so every refusal must carry a status
        self.assertEqual(km._fork_promote_request(None)["_status"], 400)
        self.assertEqual(km._fork_promote_request({})["_status"], 400)
        self.assertEqual(km._fork_promote_request({"forkId": "not-a-thread"})["_status"], 404)
        tid = km._fork_comment_request({"id": PARENT, "text": self.OPENER})["forkId"]
        res = km._fork_promote_request({"forkId": tid})   # no transcript yet → promote refuses
        self.assertEqual(res["_status"], 409)
        self.assertFalse(res.get("ok"))

    def test_fork_promote_scans_attached_remotes_for_an_unknown_forkId(self):
        saved_remotes, saved_fwd = km._remotes, km._remote_forward
        asked = []
        km._remotes = {"TESTHOST": {"host": "TESTHOST", "local_port": 1, "token": ""}}
        km._remote_forward = lambda r, path, body: asked.append((r["host"], path, body)) or {"ok": True, "name": "far-name"}
        try:
            res = km._fork_promote_request({"forkId": "far-tid"})
        finally:
            km._remotes, km._remote_forward = saved_remotes, saved_fwd
        self.assertEqual(res, {"ok": True, "name": "far-name"})
        self.assertEqual(asked, [("TESTHOST", "/fork-promote", {"forkId": "far-tid"})])

    def test_routes_are_wired_and_pop_the_status(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('if u.path in ("/fork-comment", "/fork-promote"):', src)
        self.assertIn('res = (_fork_comment_request(b) if u.path == "/fork-comment"', src)
        self.assertIn('return self._send(res.pop("_status", 200), json.dumps(res), "application/json")', src)



# ── the relay hands over the WHOLE discussion (2026-10-05) ────────────────────────────────────────────────────────────
MISSING = "99999999-8888-7777-6666-555555555555"   # a uuid no record of the transcript carries


class RelayWholeDiscussion(CommentBase):
    """A thread's relay sends the whole discussion from the cut on. It used to walk the thread's records by parentUuid
    alone, so a context compaction (whose boundary record has no parentUuid, the leaf before it in logicalParentUuid)
    ended the walk and a relay after one carried only the exchange since it, with no word that anything was missing
    (the user 2026-10-05: a thirteen-message discussion relayed as its last two). The same projection capped every
    message at 4000 characters and kept the newest 40, and left out what the person sent while the agent was working;
    the relay inherited all of it. The 48,000-character backstop cut from the front, the opening question first.
    SYNTHETIC: a notes-api thread cut at the agent's answer a1."""

    def setUp(self):
        super().setUp()
        (Path(self._td) / "session-hosts").write_text("off")   # a minted state root starts no session host
        self.t0 = self.now - 3000
        self.be = FakeBackend()
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._push_soon = lambda: None

        def restore():
            km.Sessions.backend_for, km._push_soon = saved
        self.addCleanup(restore)

    def _copied(self):
        t = self.t0
        return [uline(t, "How should the notes-api sync loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with ten percent jitter.", "a1", parent="u1")]

    def _before(self):
        """The thread's exchange before its compaction: the opener and the agent's answer, a follow-up answered
        through a tool call."""
        t = self.t0
        return [uline(t + 100, km._comment_first_message("exponential backoff", "Why jitter at all?"), "cu1",
                      parent="a1"),
                aline(t + 110, "Jitter keeps the clients from retrying in lockstep.", "ca1", parent="cu1"),
                uline(t + 200, "Would five percent be enough?", "cu2", parent="ca1"),
                tline(t + 205, "ct2", "cu2"),
                tool_result_line(t + 206, "cr2", "ct2", "ct2"),
                aline(t + 210, "Five percent is enough at this scale.", "ca2", parent="cr2")]

    def _after(self, link="ca2", **boundary_fields):
        """The compaction as the CLI writes it (the summary stamped a second BEFORE its boundary, as it often is), then
        the exchange after it."""
        t = self.t0
        b = cli_boundary(t + 300, "cb1", link)
        b.update(boundary_fields)
        return [b, summary_line(t + 299, "cs1", "cb1"),
                uline(t + 400, "Then write it into the README.", "cu3", parent="cs1"),
                aline(t + 410, "Done: the README documents five percent jitter.", "ca3", parent="cu3")]

    WHOLE = [ME + "Why jitter at all?" + END, THEM + "Jitter keeps the clients from retrying in lockstep." + END,
             ME + "Would five percent be enough?" + END, THEM + "Five percent is enough at this scale." + END,
             ME + "Then write it into the README." + END, THEM + "Done: the README documents five percent jitter." + END]

    def _seed(self, records, cut="a1", status="open", relayed=0):
        self._write(THREAD, records)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": THREAD, "alive": True,
             "threadOf": PARENT}))
        row = {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": cut, "exact": "exponential backoff",
               "status": status, "createdT": self.t0 + 90, "lastSeenT": self.now}
        if relayed:
            row["relayedT"] = relayed
        km._save_comments(PARENT, {"threads": [row]})
        km._thread_msgs_cache.clear(); km._built_thread.clear(); km._comments_memo.clear()
        forget_parses()

    def _relay(self, notes=None):
        """(error, the body the parent received or None) for one relay of the seeded thread; `notes`, a list, receives
        what the person is told beside the result."""
        del self.be.sent[:]
        km._thread_msgs_cache.clear()
        err = km._comment_merge(PARENT, THREAD) if notes is None else km._comment_merge(PARENT, THREAD, notes=notes)
        sent = [text for sid, text in self.be.sent if sid == PARENT]
        self.assertLessEqual(len(sent), 1)
        return err, (sent[0] if sent else None)

    def _assert_in_order(self, body, lines):
        at = [body.find(ln) for ln in lines]
        self.assertNotIn(-1, at, "every line reaches the relay: %r" % [ln for ln, i in zip(lines, at) if i < 0])
        self.assertEqual(at, sorted(at), "in the order it was said")

    def test_the_relay_carries_the_discussion_from_before_a_compaction(self):
        self._seed(self._copied() + self._before() + self._after())
        err, body = self._relay()
        self.assertIsNone(err)
        self._assert_in_order(body, self.WHOLE)
        self.assertNotIn("being continued from a previous conversation", body, "the summary is nobody's message")
        self.assertNotIn("How should the notes-api sync loop back off?", body, "the copied history is the parent's")
        self.assertIn(WHOLE_START, body, "nothing is missing, so nothing says so")
        self.assertNotIn(km.MERGE_GAP_NOTE, body)
        self.assertEqual(km._comment_thread(PARENT, THREAD)["relayedT"], self.t0 + 410)

    def test_the_frames_messages_include_those_from_before_a_compaction(self):
        self._seed(self._copied() + self._before() + self._after())
        msgs = km._thread_messages(THREAD, "a1")
        self.assertEqual([m["who"] for m in msgs], ["you", "agent"] * 3)
        self.assertEqual(msgs[0]["text"], "Why jitter at all?")
        self.assertEqual(msgs[3]["text"], "Five percent is enough at this scale.")

    def test_the_popover_shows_the_discussion_from_before_a_compaction(self):
        self._seed(self._copied() + self._before() + self._after())
        st = ThreadProjection._State("")
        km._sdk = lambda: st
        km.Sessions.backend_for = staticmethod(lambda sid: st)
        th = km._comments_frame(PARENT)["threads"][0]
        users = [e.get("md") for e in th["events"] if e.get("kind") == "user"]
        self.assertEqual(users, ["Why jitter at all?", "Would five percent be enough?", "Then write it into the README."])
        self.assertEqual([m["text"] for m in th["msgs"] if m["who"] == "you"], users)

    def test_a_second_relay_across_a_compaction_sends_only_what_is_new(self):
        first = self._before()[:2]
        self._seed(self._copied() + first)
        err, body = self._relay()
        self.assertIsNone(err)
        self.assertEqual(km._comment_thread(PARENT, THREAD)["relayedT"], self.t0 + 110)
        # the person replies (the thread reopens), the discussion goes on, and the thread compacts mid-way
        self._seed(self._copied() + self._before() + self._after(), status="open", relayed=self.t0 + 110)
        err, body = self._relay()
        self.assertIsNone(err)
        self._assert_in_order(body, self.WHOLE[2:])
        self.assertNotIn("Why jitter at all?", body, "the first relay's part is not sent again")
        self.assertNotIn("retrying in lockstep", body)
        self.assertIn(WHOLE_START, body)
        # …and with nothing past the floor, nothing goes
        err, body = self._relay()
        self.assertIn("already relayed", err or "")

    def test_a_second_relay_says_nothing_of_a_break_wholly_before_the_first(self):
        before = self._before()
        before[2] = dict(before[2], parentUuid=MISSING)        # the record cu2 links to was never written
        self._seed(self._copied() + before)
        err, body = self._relay()
        self.assertIsNone(err)
        self.assertIn("<discussion>\n" + km.MERGE_GAP_NOTE + "\n<message", body)
        self._seed(self._copied() + before + self._after(), status="open", relayed=self.t0 + 210)
        notes = []
        err, body = self._relay(notes)
        self.assertIsNone(err)
        self._assert_in_order(body, self.WHOLE[4:])
        self.assertNotIn(km.MERGE_GAP_NOTE, body, "the unreadable part was before the first relay's floor")
        self.assertIn(WHOLE_START, body)
        self.assertEqual(notes, [])

    def test_the_first_message_survives_the_length_cap(self):
        t = self.t0
        recs = self._copied() + self._before()[:2]
        prev = "ca1"
        for i in range(30):
            recs.append(uline(t + 300 + 20 * i, "And case %d?" % i, "cq%d" % i, parent=prev))
            recs.append(aline(t + 305 + 20 * i, ("Case %d: " % i) + "the backoff holds. " * 130, "cr%d" % i,
                              parent="cq%d" % i))
            prev = "cr%d" % i
        self._seed(recs)
        notes = []
        err, body = self._relay(notes)
        self.assertIsNone(err)
        self.assertEqual(len(notes), 1, "the person is told the relay was cut")
        self.assertIn("part of the middle cut", notes[0])
        self.assertIn(ME + "Why jitter at all?" + END, body, "the question the discussion is about always goes")
        self.assertIn(km.MERGE_TRIM_NOTE, body, "the cut says where it is")
        self.assertIn(THEM + "Case 29: " + "the backoff holds. " * 129 + "the backoff holds." + END, body,
                      "the newest reply goes whole")
        self.assertIn(END + "\n" + km.MERGE_TRIM_NOTE + "\n<message from=", body, "the note stands between two messages")
        self.assertLess(body.index(ME + "Why jitter at all?"), body.index(km.MERGE_TRIM_NOTE))
        self.assertLess(body.index(km.MERGE_TRIM_NOTE), body.index("Case 29: "))
        self.assertNotIn("Case 0: ", body, "the middle is what is cut")
        self.assertLess(len(body), km.MERGE_BODY_CAP + 2000)

    def test_a_long_reply_reaches_the_relay_whole(self):
        long = "The retry budget explained: " + "each attempt waits longer. " * 250
        self.assertGreater(len(long), 6000)
        recs = self._copied() + self._before()[:1] + [aline(self.t0 + 110, long, "ca1", parent="cu1")]
        self._seed(recs)
        err, body = self._relay()
        self.assertIsNone(err)
        self.assertIn(THEM + long.strip() + END, body)
        # the frame's copy stays capped: it rides every push
        self.assertEqual(len(km._thread_messages(THREAD, "a1")[1]["text"]), km._THREAD_MSG_CAP)

    def test_a_message_sent_while_the_agent_worked_reaches_the_relay(self):
        t = self.t0
        recs = self._copied() + self._before()[:2] + [
            tline(t + 115, "ct1", "ca1"),
            tool_result_line(t + 120, "cr1", "ct1", "ct1"),
            attline(t + 118, CORRECTION, "cq1", "cr1"),
            aline(t + 125, "Understood: under five percent.", "ca9", parent="cq1")]
        self._seed(recs)
        err, body = self._relay()
        self.assertIsNone(err)
        self._assert_in_order(body, [ME + "Why jitter at all?", THEM + "Jitter keeps the clients",
                                     ME + CORRECTION + END, THEM + "Understood: under five percent." + END])

    def test_a_task_notification_spliced_mid_turn_is_not_the_persons(self):
        t = self.t0
        recs = self._copied() + self._before()[:2] + [
            tline(t + 115, "ct1", "ca1"),
            tool_result_line(t + 120, "cr1", "ct1", "ct1"),
            attline(t + 118, "<task-notification>the build finished</task-notification>", "cq1", "cr1",
                    commandMode="task-notification"),
            aline(t + 125, "The build is green.", "ca9", parent="cq1")]
        self._seed(recs)
        err, body = self._relay()
        self.assertIsNone(err)
        self.assertNotIn("the build finished", body)
        self.assertIn("The build is green.", body)

    def test_a_prompt_restored_after_the_compaction_is_sent_once(self):
        t = self.t0
        after = self._after()
        replay = uline(t + 301, "Would five percent be enough?", "cy1", parent="cs1")   # a CLI restoring its recent tail
        after[2] = dict(after[2], parentUuid="cy1")
        self._seed(self._copied() + self._before() + after[:2] + [replay] + after[2:])
        err, body = self._relay()
        self.assertIsNone(err)
        self.assertEqual(body.count(ME + "Would five percent be enough?" + END), 1)
        self._assert_in_order(body, self.WHOLE)

    def test_a_compaction_link_to_a_record_never_written_is_repaired_from_its_preserved_segment(self):
        self._seed(self._copied() + self._before()
                   + self._after(link=MISSING, compactMetadata={"preservedSegment": {"tailUuid": "ca2"}}))
        err, body = self._relay()
        self.assertIsNone(err)
        self._assert_in_order(body, self.WHOLE)
        self.assertIn(WHOLE_START, body)

    def test_a_break_in_the_record_is_said_in_the_relay_and_to_the_person(self):
        for name, recs in (
                ("a record links to one never written",
                 self._copied() + self._before()[:2] + [dict(self._before()[2], parentUuid=MISSING)]
                 + self._before()[3:] + self._after()),
                ("a compaction links to a record never written",
                 self._copied() + self._before() + self._after(link=MISSING))):
            with self.subTest(name):
                self._seed(recs)
                notes = []
                err, body = self._relay(notes)
                self.assertIsNone(err)
                self.assertIn("<discussion>\n" + km.MERGE_GAP_NOTE + "\n" + ME, body,
                              "the relay says its beginning is missing, before its first message")
                self.assertNotIn("Why jitter at all?", body, "premise: the opener is past the break")
                self.assertIn(ME + "Then write it into the README." + END, body, "what could be read still goes")
                self.assertEqual(len(notes), 1, "the person is told too")
                self.assertIn("without the start of the discussion", notes[0])
                self.assertIn("breaks off before its first message", notes[0])
                km._comment_update(PARENT, THREAD, status="open")

    def test_a_thread_resumed_into_a_new_file_relays_from_both(self):
        # a resume that forked the transcript: the new file opens on a fresh head (no link back), and the backend's
        # resumeFork row in the thread's states log is the record of which file it continues
        resumed = "66666666-7777-8888-9999-bbbbbbbbbbbb"
        self._seed(self._copied() + self._before())
        t = self.t0
        self._write(resumed, [uline(t + 400, "Then write it into the README.", "cu3"),
                              aline(t + 410, "Done: the README documents five percent jitter.", "ca3", parent="cu3")])
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "thread-x", "cwd": self.cdir, "lastSid": resumed, "alive": True,
             "threadOf": PARENT}))
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        (jd.STATESDIR / (THREAD + ".jsonl")).write_text(json.dumps(
            {"t": t + 390, "resumeFork": {"from": THREAD, "to": resumed}}) + "\n")
        forget_parses()
        err, body = self._relay()
        self.assertIsNone(err)
        self._assert_in_order(body, self.WHOLE)
        self.assertIn(WHOLE_START, body)

    def test_a_thread_whose_start_is_not_in_its_transcript_is_refused_loudly(self):
        self._seed(self._before()[1:] + self._after())                # no cut record a1 (and no opener)
        err, body = self._relay()
        self.assertIn("could not be read", err or "")
        self.assertIn("nothing was sent", err or "")
        self.assertIsNone(body)
        self.assertEqual(km._comment_thread(PARENT, THREAD)["status"], "open", "the latch is released")

    def test_a_thread_whose_transcript_is_gone_is_refused_loudly(self):
        self._seed(self._copied() + self._before())
        (self.proj / (THREAD + ".jsonl")).unlink()
        err, body = self._relay()
        self.assertIn("transcript can't be read", err or "")
        self.assertIsNone(body)

    def test_the_socket_handler_shows_the_person_what_the_relay_left_out(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn('err = _comment_merge(sid, str(msg["tid"]), notes=notes)', src)
        self.assertIn('for text in ([err] if err else notes):', src)

    def test_the_length_cap_cuts_the_middle_and_keeps_the_first_line(self):
        first = {"who": "you", "text": "Why jitter at all?"}
        mid = [{"who": "agent", "text": "x" * 20000}, {"who": "you", "text": "and then?"},
               {"who": "agent", "text": "y" * 20000}]
        last = {"who": "agent", "text": "z" * 10000}
        body = km._merge_body("q", [first] + mid + [last])
        self.assertIn(ME + "Why jitter at all?" + END + "\n" + km.MERGE_TRIM_NOTE + "\n" + ME + "and then?" + END, body)
        self.assertIn(THEM + "z" * 10000 + END + "\n</discussion>", body)
        self.assertNotIn("x" * 100, body)
        # a newest reply too long to fit whole keeps its end
        body = km._merge_body("q", [first, {"who": "agent", "text": "w" * 30000 + "the end"},
                                    {"who": "agent", "text": "v" * 60000 + "the very end"}])
        self.assertIn(ME + "Why jitter at all?" + END, body)
        self.assertIn(km.MERGE_TRIM_NOTE + "\n" + THEM + "…", body)
        self.assertTrue(body.split("<!--")[0].rstrip().endswith("account for it in everything you do on this from here "
                                                                "on."))
        self.assertIn("the very end" + END + "\n</discussion>", body, "the cut message still closes")
        self.assertLess(len(body), km.MERGE_BODY_CAP + 1000)
        # under the cap nothing is cut
        self.assertNotIn(km.MERGE_TRIM_NOTE, km._merge_body("q", [first] + mid[:2]))



class ThreadReadsWhereTheCliWrites(CommentBase):
    """A thread whose agent cd'd lost its popover after the person replied in it (2026-10-06). Each turn's init reports
    the CLI's current directory, which follows the agent's shell, and the registry took it as the session's cwd; every
    thread read derived the transcript from that cwd, a projects dir that does not exist. The popover then held only the
    backend's echo of the reply, rendered as the thread's opening message ("sending…" under the quote), with every other
    message gone, and a relay had nothing to send. The thread reads where the CLI said it writes (the reg's
    transcriptPath); the registry no longer takes a cwd that holds no transcript (test_create_dir_case covers that).
    SYNTHETIC: a notes-api thread whose agent cd'd into tests/, as a tip fork (a passage from before the summary) and as
    a cut fork."""

    PRIOR = km._precompact_block(("me", "How should the sync loop back off?"), None,
                                 "/TESTDIR/.claude/projects/-TESTDIR-notes-api/" + PARENT + ".jsonl", "a1")

    def setUp(self):
        super().setUp()
        (Path(self._td) / "session-hosts").write_text("off")   # a minted state root starts no session host
        self.t0 = self.now - 3000
        self.be = FakeBackend()
        saved = (km.Sessions.__dict__["backend_for"], km._push_soon, ThreadProjection._State.live)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._push_soon = lambda: None

        def restore():
            km.Sessions.backend_for, km._push_soon = saved[0], saved[1]
            ThreadProjection._State.live = saved[2]
        self.addCleanup(restore)
        self.moved = os.path.join(self.cdir, "notes-api", "tests")   # where the agent's shell went
        os.makedirs(self.moved)

    def _seed(self, cut):
        """The copied history, then the thread's own exchange: the opening comment, the agent's answer after a cd into
        tests/ (its records carry the new cwd, as the CLI writes them), the person's reply and the answer to it. The reg
        as the init adoption left it: cwd the directory the shell moved to, transcriptPath where the CLI writes."""
        t = self.t0
        sent, said = km._comment_opener_parts("exponential backoff", "Why jitter at all?", "" if cut else self.PRIOR)

        def at(r, cwd):
            r["cwd"] = cwd
            return r
        home, moved = self.cdir, self.moved
        recs = [at(uline(t, "How should the sync loop back off?", "u1"), home),
                at(aline(t + 5, "Use exponential backoff with ten percent jitter.", "a1", parent="u1"), home),
                at(uline(t + 100, sent + "\n\n" + said, "cu1", parent="a1"), home),
                at(tline(t + 105, "ct1", "cu1"), home),
                at(tool_result_line(t + 106, "cr1", "ct1", "ct1"), moved),
                at(aline(t + 110, "Jitter keeps the clients from retrying in lockstep.", "ca1", parent="cr1"), moved),
                at(uline(t + 200, "Would five percent be enough?", "cu2", parent="ca1"), moved),
                at(aline(t + 210, "Five percent is enough at this scale.", "ca2", parent="cu2"), moved)]
        path = self._write(THREAD, recs)
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "notes-api-comment-1", "cwd": moved, "lastSid": THREAD, "alive": True,
             "threadOf": PARENT, "transcriptPath": str(path)}))
        self.assertFalse(os.path.exists(km.jd._proj_dir(moved) / (THREAD + ".jsonl")), "the moved cwd holds no transcript")
        row = {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": cut, "anchorT": t + 5,
               "exact": "exponential backoff", "status": "open", "sent": sent, "createdT": t + 90, "lastSeenT": t + 205}
        if not cut:
            row["beforeSummary"] = True
        km._save_comments(PARENT, {"threads": [row]})
        km._thread_msgs_cache.clear(); km._built_thread.clear(); km._comments_memo.clear()
        forget_parses()

    WHO = ["you", "agent", "you", "agent"]
    SAID = ["Why jitter at all?", "Jitter keeps the clients from retrying in lockstep.",
            "Would five percent be enough?", "Five percent is enough at this scale."]

    def _popover(self):
        """The comments frame's thread, its backend still holding the echo of the reply (the backend retires an echo
        when the transcript holds its text, which only a read of the transcript can see)."""
        st = ThreadProjection._State("")
        ThreadProjection._State.live = [{"type": "user", "author": "human", "t": self.t0 + 199, "uuid": "echo:r1",
                                         "_echo_text": "Would five percent be enough?"}]
        km._sdk = lambda: st
        km.Sessions.backend_for = staticmethod(lambda sid: st)
        return km._comments_frame(PARENT)["threads"][0]

    def _assert_whole(self, cut):
        floor = 0 if cut else self.t0 + 90
        msgs = km._thread_messages(THREAD, cut, floor)
        self.assertEqual([m["who"] for m in msgs], self.WHO, "every message of the thread")
        self.assertEqual([m["text"] for m in msgs], self.SAID)
        self.assertEqual(km._thread_exchange(THREAD, cut, floor)["gap"], "", "the walk reached the thread's start")
        th = self._popover()
        users = [e.get("md") for e in th["events"] if e.get("kind") == "user"]
        self.assertEqual(users, ["Why jitter at all?", "Would five percent be enough?"],
                         "the opening comment first, the reply after it")
        self.assertFalse([e for e in th["events"] if str(e.get("uuid") or "").startswith("echo:")],
                         "the landed reply is no unsent echo")
        self.assertIn("Five percent is enough at this scale.", [e.get("md") for e in th["events"]])
        self.assertEqual([m["text"] for m in th["msgs"]], self.SAID)
        self.assertFalse(th["replyOwed"], "the reply was answered")

    def test_a_tip_fork_shows_every_message_after_its_agent_cd(self):
        self._seed("")
        self._assert_whole("")

    def test_a_cut_fork_shows_every_message_after_its_agent_cd(self):
        self._seed("a1")
        self._assert_whole("a1")

    def test_the_relay_sends_the_whole_thread_after_its_agent_cd(self):
        for cut in ("", "a1"):
            self._seed(cut)
            del self.be.sent[:]
            self.assertIsNone(km._comment_merge(PARENT, THREAD))
            body = [text for sid, text in self.be.sent if sid == PARENT][0]
            lines = [(ME if w == "you" else THEM) + s + END for w, s in zip(self.WHO, self.SAID)]
            at = [body.find(ln) for ln in lines]
            self.assertNotIn(-1, at, "cut %r: every message goes" % cut)
            self.assertEqual(at, sorted(at))
            self.assertIn(WHOLE_START, body, "nothing is missing")

    def test_a_thread_with_nothing_recorded_still_reads_from_its_cwd(self):
        # before its first turn ends the CLI has said nothing about where it writes: the cwd derivation stands
        self._seed("a1")
        reg = json.loads((jd.SDKDIR / (THREAD + ".json")).read_text())
        reg.pop("transcriptPath"); reg["cwd"] = self.cdir
        self.assertEqual(km._thread_transcript_path(reg, THREAD), str(self.proj / (THREAD + ".jsonl")))
        # …and a recorded file the lastSid moved on from leaves the read on the lastSid's file beside it
        self._write("77777777-8888-9999-aaaa-bbbbbbbbbbbb", [])
        reg = dict(reg, transcriptPath=str(self.proj / (THREAD + ".jsonl")),
                   lastSid="77777777-8888-9999-aaaa-bbbbbbbbbbbb", cwd=self.moved)
        self.assertEqual(km._thread_transcript_path(reg, THREAD),
                         str(self.proj / "77777777-8888-9999-aaaa-bbbbbbbbbbbb.jsonl"))


if __name__ == "__main__":
    unittest.main()
