#!/usr/bin/env python3
"""Progress notes render as the agent's prose, not as Thought rows (2026-11-06).

On Claude Opus 5.5 a note the model writes between tool calls (what it found, what it does next, an answer to a message
sent mid-turn) comes back as a `thinking` block once it runs past a sentence or two. The chat drew every thinking block
as a collapsed Thought row, so those notes never reached the person. The CLI lists the note blocks on each assistant
frame it streams (`narration_block_indexes`); the transcript does not carry the list. The kernel files the list as the
raw frames arrive (SdkSession._frames → kernel/narration.py) and the chat build turns a listed block with words into an
assistant message flagged `narration`; every other thinking block stays as it was, and so does history from before the
store.

Covered here: reading the marks off raw frames (one-block and multi-block frames, a reasoning summary that says nothing,
the frames the kernel cannot read reported once per session), the store (persisted, reloaded, a replayed frame changing
nothing), the reader on the raw road and on the parsed-only road, and the chat build (notes as prose and reasoning as a
thinking row, the live tail and a reload, unmarked history staying a thinking row, a textless listed block staying
hidden). The fold's gate and the signature's component live with their own suites (test_chat_fold.py,
test_chat_build_sig_inputs.py). Synthetic fixtures only: invented text, placeholder ids, the notes-api demo world."""
import asyncio
import json
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()     # hermetic state BEFORE the loads (import-time root)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_narration", os.path.join(BIN, "romp-kernel"))
jd = km.jd
sbk = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))
nar = km.nar

SID = "5e5e5e5e-1111-2222-3333-444455556666"            # this module's private synthetic sids
SID_B = "5e5e5e5e-1111-2222-3333-444455557777"
SIG = "c3ludGhldGljLW5hcnJhdGlvbi1zaWduYXR1cmU="       # a synthetic signature: every block is signed, as the API's are
NOW = 1781200000
REASONING = "Checking how the list handler builds its query before changing it."
NOTE_1 = "The handler in `src/routes/notes.ts` returns every row. I'll add a cursor on the note id and a default page size of 20."
NOTE_2 = "Got it: the default page size is 50 now, and I'm updating the one test that pinned 20."
REPLY = "Pagination is in. `GET /notes` takes `limit` (default 50) and `cursor`, and the notes tests pass."


def think(text=""):
    return {"type": "thinking", "thinking": text, "signature": SIG}


def frame(blocks, uuid="bbbbbbbb-0000-0000-0000-000000000001", idx=None, display=None, parent=None):
    """A raw assistant frame as the CLI streams it to the SDK."""
    d = {"type": "assistant", "message": {"role": "assistant", "model": "claude-opus-5-5", "content": blocks},
         "parent_tool_use_id": parent, "session_id": "cccccccc-0000-0000-0000-000000000000", "uuid": uuid}
    if idx is not None:
        d["narration_block_indexes"] = idx
    if display is not None:
        d["thinking_display"] = display
    return d


def _state_root():
    d = Path(tempfile.mkdtemp(prefix="narration-"))
    (d / "session-hosts").write_text("off\n")            # a test that mints its own state root runs no host (root CLAUDE.md)
    return d


class FrameMarks(unittest.TestCase):
    """narration_from_frame: what one raw frame says."""

    def test_a_one_block_note_frame_is_marked_by_its_index_and_ordinal(self):
        got = nar.narration_from_frame(frame([think(NOTE_1)], idx=[0], display="summarized"))
        self.assertEqual((got["idx"], got["ords"], got["why"]), ([0], [0], None))
        self.assertEqual(got["uuid"], "bbbbbbbb-0000-0000-0000-000000000001")

    def test_a_multi_block_frame_maps_the_clis_indexes_to_thinking_ordinals(self):
        # [text, reasoning, note, tool_use]: the CLI's index 2 is the frame's SECOND thinking block
        blocks = [{"type": "text", "text": "Looking."}, think(REASONING), think(NOTE_1),
                  {"type": "tool_use", "id": "toolu_n1", "name": "Read", "input": {"file_path": "src/routes/notes.ts"}}]
        got = nar.narration_from_frame(frame(blocks, idx=[2]))
        self.assertEqual((got["idx"], got["ords"], got["why"]), ([2], [1], None))

    def test_a_reasoning_summary_frame_says_nothing(self):
        # under "summarized" an unlisted block with words is a reasoning summary: the ordinary case
        self.assertIsNone(nar.narration_from_frame(frame([think(REASONING)], display="summarized")))
        self.assertIsNone(nar.narration_from_frame(frame([think("")], display="updates")), "no words: nothing to tell apart")
        self.assertIsNone(nar.narration_from_frame(frame([{"type": "text", "text": REPLY}])), "no thinking block")

    def test_an_updates_frame_with_words_and_no_list_is_a_frame_the_kernel_cannot_read(self):
        got = nar.narration_from_frame(frame([think(NOTE_1)], display="updates"))
        self.assertEqual(got["idx"], [])
        self.assertIn("updates display", got["why"])

    def test_a_thinking_frame_from_a_cli_too_old_to_list_notes_is_a_frame_the_kernel_cannot_read(self):
        got = nar.narration_from_frame(frame([think(NOTE_1)]), lists=False)
        self.assertEqual(got["idx"], [])
        self.assertIn("older than 2.1.260", got["why"])
        self.assertIsNone(nar.narration_from_frame(frame([think(NOTE_1)]), lists=None), "an unknown CLI: no judgement")
        self.assertIsNone(nar.narration_from_frame(frame([think(NOTE_1)]), lists=True), "a CLI that lists: a summary")

    def test_a_subagent_frame_is_read_for_its_list_alone(self):
        # the CLI adds no thinking_display to a subagent's frames, so a missing one says nothing there
        self.assertIsNone(nar.narration_from_frame(frame([think(REASONING)], parent="toolu_agent")))
        got = nar.narration_from_frame(frame([think(NOTE_1)], idx=[0], parent="toolu_agent"))
        self.assertEqual((got["ords"], got["why"]), ([0], None))

    def test_a_listed_index_off_the_thinking_blocks_is_reported_and_files_nothing_for_it(self):
        got = nar.narration_from_frame(frame([think(NOTE_1), {"type": "text", "text": REPLY}], idx=[0, 1]))
        self.assertEqual(got["ords"], [0], "the thinking block it does name is still filed")
        self.assertIn("other than its thinking blocks", got["why"])
        got = nar.narration_from_frame(frame([think(NOTE_1)], idx="0"))
        self.assertEqual(got["idx"], [])
        self.assertTrue(got["why"])


class Store(unittest.TestCase):
    def setUp(self):
        self.root = _state_root()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _drop_memo(self):
        with nar._NARR_LOCK:
            for k in [k for k in nar._NARR_MEMO if k[0] == str(self.root)]:
                nar._NARR_MEMO.pop(k)

    def test_marks_persist_reload_and_a_replayed_frame_changes_nothing(self):
        self.assertEqual((nar.narration_rev(self.root, SID), dict(nar.narration_marks(self.root, SID))), (0, {}))
        self.assertTrue(nar.record_narration(self.root, SID, "u-note-1", [0], [0]))
        self.assertTrue(nar.record_narration(self.root, SID, "u-multi", [2], [1]))
        rev, marks = nar.narration_rev(self.root, SID), nar.narration_marks(self.root, SID)
        self.assertGreater(rev, 0)
        self.assertEqual((marks.get("u-note-1"), marks.get("u-multi")), (frozenset([0]), frozenset([1])))
        self.assertFalse(nar.record_narration(self.root, SID, "u-note-1", [0], [0]), "a replayed frame files nothing")
        self.assertEqual(nar.narration_rev(self.root, SID), rev, "…and moves no revision")
        rows = [json.loads(ln) for ln in (self.root / "narration" / (SID + ".jsonl")).read_text().splitlines()]
        self.assertEqual(rows, [{"u": "u-note-1", "i": [0], "k": [0]}, {"u": "u-multi", "i": [2], "k": [1]}])
        # a kernel restart: the memo is gone, the file is read again, the same marks come back
        with open(self.root / "narration" / (SID + ".jsonl"), "a") as f:
            f.write('{"u": "torn-li')                     # a torn tail line costs that line alone
        self._drop_memo()
        self.assertEqual(dict(nar.narration_marks(self.root, SID)), {"u-note-1": frozenset([0]), "u-multi": frozenset([1])})
        self.assertEqual(dict(nar.narration_marks(self.root, SID_B)), {}, "another session's store is its own")

    def test_marked_since_names_what_a_revision_did_not_hold(self):
        nar.record_narration(self.root, SID, "u1", [0], [0])
        rev = nar.narration_rev(self.root, SID)
        nar.record_narration(self.root, SID, "u2", [0], [0])
        nar.record_narration(self.root, SID, "u-sub", [0], [0], sub=True)
        self.assertEqual(nar.narration_marked_since(self.root, SID, rev), ["u2"], "main-conversation marks only")
        self._drop_memo()                                    # read from the file after `rev`: which came later is unknown
        self.assertEqual(sorted(nar.narration_marked_since(self.root, SID, rev)), ["u-sub", "u1", "u2"], "then every mark")

    def test_one_frozenset_for_the_common_zero_and_one_per_other_set(self):
        # nearly every record holds one block, so nearly every mark is {0}: one object for all of them, on both roads
        for i in range(3):
            nar.record_narration(self.root, SID, "z%d" % i, [0], [0])
            nar.record_narration(self.root, SID, "m%d" % i, [0, 2], [0, 1])
        m = nar.narration_marks(self.root, SID)
        self.assertTrue(m["z0"] is m["z1"] is m["z2"] is nar._NARR_ZERO)
        self.assertTrue(m["m0"] is m["m1"] is m["m2"])
        self._drop_memo()
        m = nar.narration_marks(self.root, SID)
        self.assertTrue(m["z0"] is m["z2"] is nar._NARR_ZERO, "read back from the file: interned too")
        self.assertTrue(m["m0"] is m["m2"])

    def test_the_journal_rotates_like_the_ledgers_and_the_reader_reads_both(self):
        p = self.root / "narration" / (SID + ".jsonl")
        with mock_rotate(64):
            nar.record_narration(self.root, SID, "old-1", [0], [0])
            nar.record_narration(self.root, SID, "old-2", [0], [0])   # past 64 bytes: the next append rotates first
            nar.record_narration(self.root, SID, "mid-1", [0], [0])
            nar.record_narration(self.root, SID, "mid-2", [0], [0])
            nar.record_narration(self.root, SID, "new-1", [0], [0])
        self.assertTrue(p.with_name(p.name + ".1").exists())
        self.assertLess(p.stat().st_size, 200)
        self._drop_memo()
        held = set(nar.narration_marks(self.root, SID))
        self.assertIn("new-1", held)
        self.assertIn("mid-2", held, "the predecessor is read too")
        self.assertNotIn("old-1", held, "the older predecessor was dropped: the bound")

    def test_the_memo_holds_the_sessions_read_most_recently_and_a_revision_survives_a_drop(self):
        saved = nar._NARR_MEMO_MAX
        nar._NARR_MEMO_MAX = 2
        self.addCleanup(setattr, nar, "_NARR_MEMO_MAX", saved)
        with nar._NARR_LOCK:
            nar._NARR_MEMO.clear()
        nar.record_narration(self.root, SID, "a1", [0], [0])
        rev = nar.narration_rev(self.root, SID)
        nar.narration_marks(self.root, SID_B)
        nar.narration_marks(self.root, "5e5e5e5e-1111-2222-3333-444455558888")   # a third: the least recently read goes
        self.assertNotIn((str(self.root), SID), nar._NARR_MEMO)
        self.assertIn((str(self.root), SID_B), nar._NARR_MEMO)
        self.assertEqual(nar.narration_rev(self.root, SID), rev, "the revision is not the memo's: dropping it reuses nothing")
        self.assertEqual(nar.narration_marks(self.root, SID).get("a1"), frozenset([0]), "read back on its next build")
        nar.record_narration(self.root, SID_B, "b1", [0], [0])
        self.assertNotEqual(nar.narration_rev(self.root, SID_B), rev, "one counter for the process: no value comes twice")

    def test_main_and_subagent_marks_keep_separate_revisions(self):
        nar.record_narration(self.root, SID, "m1", [0], [0])
        main, sub = nar.narration_rev(self.root, SID), nar.narration_rev(self.root, SID, sub=True)
        nar.record_narration(self.root, SID, "s1", [0], [0], sub=True)
        self.assertEqual(nar.narration_rev(self.root, SID), main, "a subagent's note leaves the parent chat's revision")
        self.assertNotEqual(nar.narration_rev(self.root, SID, sub=True), sub)
        sub2 = nar.narration_rev(self.root, SID, sub=True)
        nar.record_narration(self.root, SID, "m2", [0], [0])
        self.assertEqual(nar.narration_rev(self.root, SID, sub=True), sub2, "a parent note leaves the subagent viewers' key")

    def test_a_revision_is_read_without_reading_the_store(self):
        # the chat signature reads every tab's revision every cycle: memory only, never the file
        (self.root / "narration").mkdir(parents=True, exist_ok=True)
        (self.root / "narration" / (SID + ".jsonl")).write_text(json.dumps({"u": "x", "i": [0], "k": [0]}) + "\n")
        with mock.patch("builtins.open", side_effect=AssertionError("the revision read a file")):
            self.assertEqual(nar.narration_rev(self.root, SID), 0)
        self.assertNotIn((str(self.root), SID), nar._NARR_MEMO, "nor loaded the memo")


class mock_rotate:
    def __init__(self, n):
        self.n = n

    def __enter__(self):
        self.saved = nar.NARRATION_ROTATE_BYTES
        nar.NARRATION_ROTATE_BYTES = self.n

    def __exit__(self, *a):
        nar.NARRATION_ROTATE_BYTES = self.saved


class OlderClaudeCode(unittest.TestCase):
    """Whether a CLI lists notes is its version's answer (the init frame's claude_code_version), or the field seen in the
    session: never the frame-level thinking_display, which CLIs 2.1.260 to 2.1.29x do not send."""

    def test_the_version_threshold(self):
        self.assertEqual([nar.cli_lists_notes(v) for v in ("2.1.259", "2.1.260", "2.1.282", "2.1.316", "3.0.0", "", None, "dev")],
                         [False, True, True, True, True, None, None, None])

    def test_a_2_1_282_reasoning_summary_is_no_older_cli(self):
        # 2.1.282 lists notes but sends no thinking_display: its reasoning summary is the ordinary case
        summary = frame([think(REASONING)])
        self.assertIsNone(nar.narration_from_frame(summary, nar.cli_lists_notes("2.1.282")))
        self.assertIsNone(nar.narration_from_frame(summary, None), "no init seen: no judgement rather than a false one")
        got = nar.narration_from_frame(summary, nar.cli_lists_notes("2.1.250"))
        self.assertIn("older than 2.1.260", got["why"])
        self.assertEqual(nar.narration_from_frame(frame([think(NOTE_1)], idx=[0]), True)["ords"], [0], "its notes are filed")

class _Backend:
    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.logs = []

    def _log(self, m, problem=None, key=None, ring_text=None):
        self.logs.append({"text": m, "problem": problem, "key": key})


def _session(root, sid=SID):
    s = sbk.SdkSession.__new__(sbk.SdkSession)       # the reader's own state only: no client, no thread
    s.backend = _Backend(root)
    s.sid = sid
    s.name = "web"
    return s


class _Raw:
    def __init__(self, frames):
        self.frames = frames

    async def receive_messages(self):
        for f in self.frames:
            yield f


class _RawClient:
    def __init__(self, frames):
        self._query = _Raw(frames)


class AssistantMessage:                                 # the SDK's class names are what the parsed-only road reads
    def __init__(self, content, parent_tool_use_id=None, uuid=None):
        self.content, self.parent_tool_use_id, self.uuid = content, parent_tool_use_id, uuid
        self.model, self.stop_reason, self.error = "claude-opus-5-5", None, None


class ThinkingBlock:
    def __init__(self, thinking, signature=SIG):
        self.thinking, self.signature = thinking, signature


class _ParsedOnlyClient:
    def __init__(self, msgs):
        self.msgs = msgs

    async def receive_messages(self):
        for m in self.msgs:
            yield m


class Reader(unittest.TestCase):
    """SdkSession._frames files each raw frame's marks before the parsed message goes on, and reports a frame it
    cannot read once per session."""

    def setUp(self):
        self.root = _state_root()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _drain(self, s, client):
        seen = []

        async def go():
            async for m in s._frames(client):
                u = m.get("uuid") if isinstance(m, dict) else getattr(m, "uuid", None)
                seen.append((u, nar.narration_marks(self.root, s.sid).get(u)))
        with mock.patch.object(sbk, "_sdk_parse_message", lambda: (lambda d: d)):   # the parser's stand-in: the frame itself
            asyncio.run(go())
        return seen

    def test_the_raw_road_files_marks_before_the_message_goes_on_and_says_an_unreadable_frame_once(self):
        frames = [
            {"type": "system", "subtype": "init", "uuid": "init-1"},
            frame([think(REASONING)], uuid="u-reason", display="summarized"),
            frame([think(NOTE_1)], uuid="u-note-1", idx=[0], display="summarized"),
            frame([{"type": "tool_use", "id": "toolu_n1", "name": "Read", "input": {}}], uuid="u-tool"),
            frame([think(NOTE_2)], uuid="u-unlisted-1", display="updates"),   # cannot be read: said once
            frame([think(NOTE_2)], uuid="u-unlisted-2", display="updates"),
        ]
        s = _session(self.root)
        seen = self._drain(s, _RawClient(frames))
        self.assertEqual([u for u, _ in seen], ["init-1", "u-reason", "u-note-1", "u-tool", "u-unlisted-1", "u-unlisted-2"])
        self.assertEqual(dict(seen)["u-note-1"], frozenset([0]), "the mark is in the store when its message goes on")
        marks = nar.narration_marks(self.root, SID)
        self.assertEqual(set(k for k in ("u-reason", "u-note-1", "u-tool", "u-unlisted-1") if marks.get(k)),
                         {"u-note-1"}, "only the listed block is filed")
        probs = [r for r in s.backend.logs if r["problem"]]
        self.assertEqual(len(probs), 1, "said once per session: %r" % probs)
        self.assertEqual(probs[0]["key"][0], nar.NARRATION_PROBLEM_KEY)
        self.assertIn("Thought rows", probs[0]["text"])
        s2 = _session(self.root, SID_B)
        self._drain(s2, _RawClient([frame([think(NOTE_2)], uuid="u-unlisted-3", display="updates")]))
        self.assertEqual(len([r for r in s2.backend.logs if r["problem"]]), 1, "another session says it again")

    def test_the_init_frames_version_decides_an_older_cli_and_a_2_1_282_stream_says_nothing(self):
        # 2.1.282 lists notes but sends no frame-level thinking_display; its reasoning summaries are ordinary
        init = lambda v: {"type": "system", "subtype": "init", "uuid": "init-" + v, "claude_code_version": v}
        stream = [frame([think(REASONING)], uuid="u-reason-1"), frame([think(NOTE_1)], uuid="u-note-1", idx=[0]),
                  frame([think(REASONING)], uuid="u-reason-2")]
        s = _session(self.root)
        self._drain(s, _RawClient([init("2.1.282")] + stream))
        self.assertEqual([r for r in s.backend.logs if r["problem"]], [], "no false older-CLI notice")
        self.assertEqual(nar.narration_marks(self.root, SID).get("u-note-1"), frozenset([0]))
        old = _session(self.root, SID_B)
        self._drain(old, _RawClient([init("2.1.250"), frame([think(REASONING)], uuid="u-old-1"),
                                     frame([think(REASONING)], uuid="u-old-2")]))
        probs = [r for r in old.backend.logs if r["problem"]]
        self.assertEqual(len(probs), 1, "an older CLI is said once: %r" % probs)
        self.assertIn("older than 2.1.260", probs[0]["text"])
        # no init (an attach to a running CLI): the field on any frame says the CLI lists notes
        late = _session(self.root, "5e5e5e5e-1111-2222-3333-444455559999")
        self._drain(late, _RawClient(stream))
        self.assertEqual([r for r in late.backend.logs if r["problem"]], [])
        self.assertIs(late._narration_lists, True)

    def test_a_fault_reading_one_frame_costs_that_frame_alone(self):
        s = _session(self.root)
        with mock.patch.object(nar, "record_narration", side_effect=RuntimeError("disk on fire")):
            seen = self._drain(s, _RawClient([frame([think(NOTE_1)], uuid="u-note-1", idx=[0], display="summarized"),
                                              frame([{"type": "text", "text": REPLY}], uuid="u-reply")]))
        self.assertEqual([u for u, _ in seen], ["u-note-1", "u-reply"], "the stream goes on")
        self.assertTrue(any(r["problem"] and "RuntimeError" in r["text"] for r in s.backend.logs))

    def test_the_parsed_only_road_says_once_that_it_cannot_read_the_marks(self):
        s = _session(self.root)
        msgs = [AssistantMessage([ThinkingBlock(NOTE_1)], uuid="u1"), AssistantMessage([ThinkingBlock(NOTE_2)], uuid="u2"),
                AssistantMessage([ThinkingBlock(REASONING)], parent_tool_use_id="toolu_agent", uuid="u3")]
        seen = self._drain(s, _ParsedOnlyClient(msgs))
        self.assertEqual(len(seen), 3)
        self.assertFalse(s._raw_frames)
        probs = [r for r in s.backend.logs if r["problem"]]
        self.assertEqual(len(probs), 1)
        self.assertIn("does not pass the raw frames", probs[0]["text"])
        self.assertEqual(dict(nar.narration_marks(self.root, SID)), {})


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(t, uuid, text, parent):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "promptSource": "typed", "message": {"role": "user", "content": text}}


def _arec(t, uuid, block, parent, mid, think_tokens=0, stop="tool_use"):
    usage = {"input_tokens": 10, "output_tokens": 200}
    if think_tokens:
        usage["output_tokens_details"] = {"thinking_tokens": think_tokens}
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "message": {"id": mid, "role": "assistant", "model": "claude-opus-5-5",
                                                 "content": [block], "stop_reason": stop, "usage": usage}}


def _trec(t, uuid, tid, parent):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                             "content": "ok"}]}}


def _turn_records():
    """One turn, one content block per record as Claude Code writes it: a reasoning summary and a progress note before
    the first tool, a message sent mid-turn, a note answering it before the second tool, then the final reply."""
    return [
        _urec(NOW - 100, "u-prompt", "Add cursor pagination to GET /notes.", None),
        _arec(NOW - 95, "u-reason", think(REASONING), "u-prompt", "msg_1", think_tokens=1200),
        _arec(NOW - 94, "u-note-1", think(NOTE_1), "u-reason", "msg_1", think_tokens=1200),
        _arec(NOW - 93, "u-read", {"type": "tool_use", "id": "toolu_read", "name": "Read",
                                   "input": {"file_path": "/tmp/notes-api/src/routes/notes.ts"}}, "u-note-1", "msg_1",
              think_tokens=1200),
        _trec(NOW - 90, "u-read-out", "toolu_read", "u-read"),
        _urec(NOW - 80, "u-midturn", "keep the default page size at 50, not 20", "u-read-out"),
        _arec(NOW - 75, "u-note-2", think(NOTE_2), "u-midturn", "msg_2", think_tokens=300),
        _arec(NOW - 74, "u-edit", {"type": "tool_use", "id": "toolu_edit", "name": "Edit",
                                   "input": {"file_path": "/tmp/notes-api/src/routes/notes.ts", "old_string": "20",
                                             "new_string": "50"}}, "u-note-2", "msg_2", think_tokens=300),
        _trec(NOW - 70, "u-edit-out", "toolu_edit", "u-edit"),
        _arec(NOW - 60, "u-reply", {"type": "text", "text": REPLY}, "u-edit-out", "msg_3", stop="end_turn"),
    ]


def _shape(events):
    """(kind, narration, text) for the agent's side of the chat."""
    out = []
    for e in events:
        if e.get("kind") == "assistant":
            out.append(("assistant", bool(e.get("narration")), e.get("md")))
        elif e.get("kind") == "thinking":
            out.append(("thinking", False, e.get("text")))
        elif e.get("kind") == "tool":
            out.append(("tool", False, e.get("name")))
    return out


class _Render(unittest.TestCase):
    def setUp(self):
        self.root = _state_root()
        self.addCleanup(shutil.rmtree, self.root, True)
        self._saved_state = jd.STATE
        jd._rebind_state(self.root)
        self.addCleanup(jd._rebind_state, self._saved_state)
        for cache in (km._PATH_LINK_CACHE, km._SPACE_PATH_CACHE):
            for k in [k for k in cache if k[0] == SID]:
                cache.pop(k, None)
        self.path = self.root / "transcript.jsonl"

    def write(self, recs):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in recs))

    def build(self):
        # path_override: the read-only render of exactly this transcript (the episode road); the live_map stub makes
        # the sid resolvable without a registry
        m = km.build_session(SID, NOW, live_map={SID: {}}, path_override=str(self.path))
        self.assertIsNotNone(m)
        return m["events"]


class Render(_Render):
    def test_marked_notes_render_as_prose_and_reasoning_stays_a_thinking_row(self):
        self.write(_turn_records())
        nar.record_narration(jd.STATE, SID, "u-note-1", [0], [0])
        nar.record_narration(jd.STATE, SID, "u-note-2", [0], [0])
        evs = self.build()
        self.assertEqual(_shape(evs), [
            ("thinking", False, REASONING),
            ("assistant", True, NOTE_1),
            ("tool", False, "Read"),
            ("assistant", True, NOTE_2),                  # the answer to the mid-turn message reaches the person
            ("tool", False, "Edit"),
            ("assistant", False, REPLY),
        ])
        by = {e.get("uuid"): e for e in evs if e.get("kind") in ("assistant", "thinking")}
        self.assertEqual(by["u-reason"].get("thinkTokens"), 1200, "the message's figure rides its first thinking block")
        self.assertNotIn("thinkTokens", by["u-note-1"], "…counted once per message")
        self.assertEqual(by["u-note-2"].get("thinkTokens"), 300, "a message whose only thinking is a note: the note carries it")
        self.assertNotIn("narration", by["u-reply"])
        self.assertIn("pathLinks", by["u-note-1"], "a note's prose gets a reply's path fields (its path token judged)")

    def test_unmarked_history_keeps_its_thinking_rows(self):
        self.write(_turn_records())
        self.assertEqual([k for k, _n, _t in _shape(self.build())],
                         ["thinking", "thinking", "tool", "thinking", "tool", "assistant"],
                         "no marks (history from before the store, or a CLI that never listed them): as before")

    def test_a_listed_block_with_no_words_stays_an_ordinary_thinking_row(self):
        recs = _turn_records()
        recs[2] = _arec(NOW - 94, "u-note-1", think(""), "u-reason", "msg_1", think_tokens=1200)
        self.write(recs)
        nar.record_narration(jd.STATE, SID, "u-note-1", [0], [0])
        ev = next(e for e in self.build() if e.get("uuid") == "u-note-1")
        self.assertEqual((ev["kind"], ev["encrypted"]), ("thinking", True))

    def test_the_ordinal_picks_the_note_among_a_records_thinking_blocks(self):
        # a record holding two thinking blocks (a reasoning summary, then the note) and the tool call
        recs = [_urec(NOW - 100, "u-prompt", "Add cursor pagination to GET /notes.", None),
                {"type": "assistant", "uuid": "u-multi", "parentUuid": "u-prompt", "timestamp": _iso(NOW - 95),
                 "sessionId": SID, "cwd": "/tmp/notes-api",
                 "message": {"id": "msg_m", "role": "assistant", "model": "claude-opus-5-5", "stop_reason": "tool_use",
                             "content": [think(REASONING), think(NOTE_1),
                                         {"type": "tool_use", "id": "toolu_m", "name": "Read", "input": {}}]}}]
        self.write(recs)
        nar.record_narration(jd.STATE, SID, "u-multi", [1], [1])
        self.assertEqual(_shape(self.build()), [("thinking", False, REASONING), ("assistant", True, NOTE_1),
                                                ("tool", False, "Read")])


class KernelCaches(_Render):
    """The caches above the build see the marks they draw from, and only those: a history page re-renders when a record it
    drew as thinking is marked late (finding 5); the subagent viewer's key moves on a subagent's mark and not on a parent's,
    and the parent chat's signature the other way round (finding 4)."""

    def test_a_history_page_rerenders_when_a_record_it_drew_as_thinking_is_marked_late(self):
        evs = [{"kind": "thinking", "text": NOTE_1, "uuid": "u-page-note"}, {"kind": "thinking", "text": "", "uuid": "u-opaque"},
               {"kind": "assistant", "md": REPLY, "uuid": "u-page-reply"}]
        rec = km._page_live_record(SID, evs, {"agents": [], "outs": []}, nar.narration_rev(jd.STATE, SID))
        self.assertFalse(km._page_live_moved(SID, rec, {}))
        nar.record_narration(jd.STATE, SID, "u-elsewhere", [0], [0])
        self.assertFalse(km._page_live_moved(SID, rec, {}), "a mark for a record the page does not draw: stands")
        nar.record_narration(jd.STATE, SID, "u-opaque", [0], [0])
        self.assertFalse(km._page_live_moved(SID, rec, {}), "a textless block stays a thinking row either way")
        nar.record_narration(jd.STATE, SID, "u-page-note", [0], [0])
        self.assertTrue(km._page_live_moved(SID, rec, {}), "the page's own thinking row is a note now: render it again")

    def test_the_subagent_viewer_keys_on_subagent_marks_and_the_chat_on_main_ones(self):
        built = []
        fake = lambda sid, aid, now, live_map=None: built.append(aid) or {"type": "subagent", "id": sid, "agentId": aid}
        with mock.patch.object(km, "build_subagent", fake), mock.patch.object(km, "_path_of", lambda sid, now: "/tmp/notes-api/t.jsonl"), \
                mock.patch.object(km, "_subagent_file", lambda p, a: None), mock.patch.object(km, "_subagent_meta", lambda p, a, f: {}), \
                mock.patch.object(km, "_agent_running_for", lambda *a, **k: False):
            km._SUBAGENT_FRAMES.pop((SID, "agent-1"), None)
            self.addCleanup(km._SUBAGENT_FRAMES.pop, (SID, "agent-1"), None)
            km._subagent_frame_cached(SID, "agent-1", NOW, {})
            km._subagent_frame_cached(SID, "agent-1", NOW, {})
            self.assertEqual(len(built), 1, "cached while nothing moved")
            nar.record_narration(jd.STATE, SID, "u-parent-note", [0], [0])
            km._subagent_frame_cached(SID, "agent-1", NOW, {})
            self.assertEqual(len(built), 1, "a parent's note does not re-key the subagent viewer")
            nar.record_narration(jd.STATE, SID, "u-agent-note", [0], [0], sub=True)
            km._subagent_frame_cached(SID, "agent-1", NOW, {})
            self.assertEqual(len(built), 2, "a subagent's note does")


class _TailBackend:
    """An owning backend whose live tail the test fills (the stream's atoms ahead of the disk)."""

    def __init__(self):
        self.atoms = []

    def owns(self, sid): return True
    def live_atoms(self, sid): return [dict(a) for a in self.atoms]
    def pending_queued(self, sid): return []
    def compacting(self, sid): return False
    def clearing(self, sid): return False
    def pending_cut(self, sid): return ""
    def prune_live(self, sid, *a, **k): return None


class LiveAndReload(unittest.TestCase):
    """The live tail (an SDK stream atom ahead of its record) and a reload (the record on disk, a fresh memo after a
    kernel restart) render the same note as prose."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp(prefix="narration-live-"))
        self.addCleanup(shutil.rmtree, self.td, True)
        self.cdir = self.td / "notes-api"
        self.cdir.mkdir()
        proj = self.td / "projects"
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        state = self.td / "state"
        state.mkdir()
        (state / "session-hosts").write_text("off\n")
        saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._live_map, km._sdk, km.Sessions.backend_for)
        jd._rebind_state(state)
        jd.PROJECTS = proj
        jd.NAMES.mkdir()
        (jd.NAMES / SID).write_text("web\t%s\t#1EA1EB\twhite\n" % self.cdir)
        km.NAMES = jd.NAMES
        self.live_map = {SID: {"state": "working", "since": NOW - 100, "model": "", "effort": "", "context": None,
                               "compactPct": None, "color": None, "backend": "sdk"}}
        km._live_map = lambda: self.live_map
        km._sdk = lambda: None
        self.be = _TailBackend()
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)

        def restore():
            jd._rebind_state(saved[0])
            jd.PROJECTS, km.NAMES, km._live_map, km._sdk = saved[1], saved[2], saved[3], saved[4]
            km.Sessions.backend_for = saved[5]
            km._built_chat.clear()
            km._chat_fold.pop(SID, None)
        self.addCleanup(restore)
        km._chat_fold.pop(SID, None)
        recs = _turn_records()
        self.disk = recs[:5]                              # the prompt, the reasoning, the first note, the Read and its result
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in self.disk))
        self.landing = recs[5:7]                          # the mid-turn message and the second note's record

    def build(self):
        m = km.build_session(SID, NOW, self.live_map)
        self.assertIsNotNone(m)
        return _shape(m["events"])

    def test_a_streamed_note_renders_as_prose_live_and_after_its_record_lands_and_the_kernel_restarts(self):
        # the second note streams: the live atom the SDK message becomes (msg_to_atom), its mark filed by the reader
        msg = AssistantMessage([ThinkingBlock(NOTE_2)], uuid="u-note-2")
        atom = sbk.msg_to_atom(msg, SID, SID, NOW - 75)
        self.assertEqual(atom["message"]["content"][0]["type"], "thinking")
        self.be.atoms = [atom]
        nar.record_narration(jd.STATE, SID, "u-note-1", [0], [0])
        nar.record_narration(jd.STATE, SID, "u-note-2", [0], [0])
        live = self.build()
        self.assertIn(("assistant", True, NOTE_2), live, "live: %r" % live)
        self.assertIn(("assistant", True, NOTE_1), live)
        self.assertIn(("thinking", False, REASONING), live)
        # its record lands and the tail lets it go; then a restart drops the in-memory store and the file is read again
        with open(self.tpath, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in self.landing))
        self.be.atoms = []
        with nar._NARR_LOCK:
            nar._NARR_MEMO.pop((str(jd.STATE), SID), None)
        km._chat_fold.pop(SID, None)
        after = self.build()
        self.assertEqual([x for x in after if x[0] != "tool"], [("thinking", False, REASONING), ("assistant", True, NOTE_1),
                                                                ("assistant", True, NOTE_2)], "after reload: %r" % after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
