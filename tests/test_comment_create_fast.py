#!/usr/bin/env python3
"""Creating a comment thread answers in well under a second on a huge parent (2026-10-01).

Measured in a lab before this (synthetic notes-api sessions): Enter to thread took 0.5 s on a fresh 12 MB session, 2.1 s at
40 MB, 4.6 to 13.6 s at 110 MB and 25 to 30 s on a 12 MB session with ten threads, while the page's own socket thread was
held for all of it. Two reads were on the create's road: finding the cut re-read the parent's whole stitched transcript
(1 to 3.6 s at 110 MB), and the answer waited for a fresh comments frame, which parsed every changed thread whole, and a
thread's transcript is a full copy of its parent up to the cut (five 40 MB threads: 53 s cold).

Pinned here:
- the cut is walked over the parent's DISPLAY parse (its assembly entry, whole or restored from its document), reading no
  transcript a chat build has read, and on a restored entry only the pre-cut records the walk needs a body of; a
  pre-compaction passage and a restart seam still cut exactly as the whole read cuts them; a comment on or beside a
  message the person sent while the agent worked, which the CLI spliced into its running turn (a queued_command
  attachment, which turns() writes in every fifth turn), is walked on a whole entry and on a restored one, before its
  cut and past it;
- a comments frame reads each thread's own records past its cut, never the copied history;
- the create runs off the socket thread and answers the moment the thread row exists, carrying the thread's frame row;
  a frame built before that row was saved is never sent after the answer.

SYNTHETIC fixtures only: invented notes-api text, placeholder uuids. The state root is the test's own (session hosts off)."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import threading
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
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel", os.path.join(BIN, "romp-kernel"))

PARENT = "5e5e5e5e-1212-3434-5656-787878787878"     # private synthetic sids: this module mints no goals, but shares no slot
SEAM_NEW = "5e5e5e5e-1212-3434-5656-000000000001"
THREADS = ["7d7d7d7d-1212-3434-5656-00000000000%d" % i for i in range(1, 5)]
BASE = ("the notes route pages by cursor and the tags route filters by owner before the search index is rebuilt; "
        "the sync loop backs off with a jitter of ten percent and caps the delay at two minutes. ")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def pad(n, k=0):
    s = "[%d] " % k
    return s + (BASE * (n // len(BASE) + 1))[:max(0, n - len(s))]


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def tool_use(t, uuid, parent):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": "tu_" + uuid, "name": "Read", "input": {"path": "notes.py"}}]}}


def tool_result(t, uuid, parent, call, size):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_" + call,
                                                      "content": pad(size, hash(uuid) % 997)}]}}


def boundary(t, uuid, logical_parent):
    return {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": uuid,
            "parentUuid": None, "logicalParentUuid": logical_parent, "content": "Conversation compacted"}


def summary(t, uuid, parent):
    r = uline(t, "This session is being continued from a previous conversation. Summary: the sync loop backs off.", uuid, parent)
    r["isCompactSummary"] = True
    return r


def attachment(t, uuid, parent, **att):
    """An attachment record as the CLI chains one through a turn (uuid-bearing, parent-chained)."""
    return {"type": "attachment", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isSidechain": False,
            "attachment": att}


def splice(t, text, uuid, parent, **fields):
    """A message the person sent while the agent was working, as the CLI records it once spliced into the running turn: no
    user record but a queued_command attachment stamped with the send time."""
    return attachment(t, uuid, parent, type="queued_command", prompt=text, **fields)


def correction(k):
    return "step %d: keep the page size under fifty, please." % k


def blocks(text):
    """`text` as a content-block list, the prompt's other form (the SDK injection path, FileAdapter._ingest): two text
    blocks around an image, split at the step's colon, which the chat joins with a space (em._text_of)."""
    head, _, tail = text.partition(": ")
    return [{"type": "text", "text": head + ":"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}},
            {"type": "text", "text": tail}]


SPLICE_TURN = 3                  # the turns with a message sent mid-turn: k % 5 == 3 (never 6 or 30, whose tool calls
#                                  the tests below cut at the question above them)


def turns(t0, n, size, parent=None, tag="k", compact_after=None):
    """`n` notes-api turns, each a question, a tool call, its (padded) result and a prose answer, with two of the
    attachments the CLI chains through a turn: a token-count reminder at the request's start and a hook's result after the
    tool's. In every fifth turn (k % 5 == SPLICE_TURN) the agent says a sentence (kp<k>) before its tool call, the person
    sends a correction while the tool runs (kq<k>, a queued_command attachment), and the answer replies to it. A compaction
    (kb, its summary ks) after turn `compact_after` when given, after whose summary the latest correction, when one
    precedes it, is written again as a compaction's replay writes it (kc: the same send stamp and text, a new uuid).
    Returns (records, last uuid, next free time)."""
    out, t, last = [], t0, None
    for k in range(n):
        u, tu, tr, a = "%su%d" % (tag, k), "%st%d" % (tag, k), "%sr%d" % (tag, k), "%sa%d" % (tag, k)
        out += [uline(t, "step %d: how should the notes route page? %s" % (k, pad(200, k)), u, parent),
                attachment(t, "%sn%d" % (tag, k), u, type="total_tokens_reminder")]
        prev = "%sn%d" % (tag, k)
        spliced = k % 5 == SPLICE_TURN
        if spliced:
            out.append(aline(t + 1, "step %d: let me read the notes route first." % k, "%sp%d" % (tag, k), prev))
            prev = "%sp%d" % (tag, k)
        out += [tool_use(t + 1, tu, prev),
                tool_result(t + 2, tr, tu, tu, size),
                attachment(t + 2, "%sh%d" % (tag, k), tr, type="hook_success", hookEvent="PostToolUse", exitCode=0)]
        prev = "%sh%d" % (tag, k)
        if spliced:
            last = splice(t + 2, correction(k), "%sq%d" % (tag, k), prev)
            out.append(last)
            prev = last["uuid"]
        out.append(aline(t + 3, "step %d done: %s" % (k, pad(400, k + 1)), a, prev))
        parent, t = a, t + 10
        if compact_after is not None and k == compact_after:
            out += [boundary(t, "%sb" % tag, parent), summary(t + 1, "%ss" % tag, "%sb" % tag)]
            parent, t = "%ss" % tag, t + 10
            if last is not None:
                out.append(dict(last, uuid="%sc" % tag, parentUuid=parent))
                parent = "%sc" % tag
    return out, parent, t


def write(path, records):
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in records))
    return str(path)


def read_of(path):
    """Bytes the reader pulled off `path` since the counters were last cleared (every transcript read is counted)."""
    rp = os.path.realpath(str(path))
    return sum(n for k, n in em.read_bytes_report().items() if os.path.realpath(k) == rp)


def zero_reads():
    with em._READ_BYTES_LOCK:
        em._READ_BYTES.clear()


def zero_reads_cold():
    """zero_reads with the reader's records dropped too, so a whole read of the transcript costs its bytes again: cold(),
    the reference walk, leaves the reader holding the file, and a measure after it would see no whole read on a later
    anchor. The display entry keeps its graph (with_display_graph's currency test is the files' stat, never the reader's)."""
    with em._JSONL_CACHE_LOCK:
        em._JSONL_CACHE.clear()
    zero_reads()


def restart():
    """A kernel restart's in-memory side: every reader entry, assembly entry, body memo and parse slot gone."""
    with em._JSONL_CACHE_LOCK:
        em._JSONL_CACHE.clear()
    with em._ASM_LOCK:
        em._ASM_CACHE.clear()
    em._TRAILING_CACHE.clear()
    with em._ASM_CKPT_LOCK:
        em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
        em._ASM_DOC_MEMO.clear(); em._ASM_DOC_MEMO_BYTES[0] = 0
    em._LAZY_FILES.clear()
    jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
    km._parse_cache.clear()
    km._thread_msgs_cache.clear(); km._built_thread.clear()
    for memo in ("_thread_own_offsets", "_thread_own_memo", "_thread_tail_memo"):   # getattr: an older kernel reds on behavior
        getattr(km, memo, {}).clear()


SCAN_BLOCK = getattr(km, "_THREAD_SCAN_BLOCK", 1 << 20)   # the backward scan's largest read
GUARD = getattr(km, "_THREAD_OWN_GUARD", 256)              # the guard bytes either side of a thread's own-part offset


class Base(unittest.TestCase):
    def setUp(self):
        self._saved = (jd.STATE, jd.PROJECTS, km._sdk)
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        Path(self._td).mkdir(parents=True, exist_ok=True)
        (Path(self._td) / "session-hosts").write_text("off")   # this root is the test's own: no per-session host, ever
        jd._discover_cache.clear()
        restart()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        km._sdk = lambda: None
        self.roads0 = dict(getattr(km, "_CUT_ROADS", {}))
        self._saved_ckpt = em._CKPT_DIR_FN
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")   # the judge's wiring; a checkpoint test module in the same
        #                                                           process turns it off at its teardown

    def tearDown(self):
        em.set_checkpoint_dir(self._saved_ckpt)
        jd.STATE, jd.PROJECTS, km._sdk = self._saved[0], self._saved[1], self._saved[2]
        jd._rebind_state(self._saved[0])
        restart()
        shutil.rmtree(self._td, ignore_errors=True)

    def walks_reading_the_transcript(self, fn, *a):
        """fn(*a), and how many times it built the cut's graph from a read of the transcript (_anchor_adapter: the whole
        stitched chain, the walk's only road before). A reader cache that still holds the file serves that read without
        pulling bytes, so the byte count alone cannot see it; the rebuild of the 110 MB graph is the cost either way."""
        calls, real = [], km._anchor_adapter
        km._anchor_adapter = lambda *x, **k: (calls.append(x), real(*x, **k))[1]
        try:
            return fn(*a), len(calls)
        finally:
            km._anchor_adapter = real

    def roads(self):
        now = getattr(km, "_CUT_ROADS", {"whole": 0, "seeded": 0, "read": 0})
        return {k: now[k] - self.roads0.get(k, 0) for k in now}

    def parent(self, records, stem=PARENT):
        return write(self.proj / (stem + ".jsonl"), records)

    def cold(self, path, anchor, sid=PARENT):
        """The walk over a fresh read of the stitched transcript: the only road before this change, the reference."""
        if not hasattr(km, "_comment_cut_walk"):
            return km._comment_cut_resolve(path, sid, anchor)
        return km._comment_cut_walk(em.TranscriptGraph(km._anchor_adapter(path, sid)), path, anchor)

    def restored(self, path, sid=PARENT, turns_section=True):
        """The chat's parse, a document written from it, and a restart: the parse that follows restores the entry from the
        document (the road a large session takes at every boot), so the cut walk meets a SEEDED entry. `turns_section`
        False writes the document without the parsed tree, so the pre-cut part restores as an atoms-only prefix rather
        than the lazy index of the turns section."""
        tree = km._parse(path, sid, self.now)
        self.assertTrue(em.asm_checkpoint_write(path, sid, km._display_sdk_human(sid), tree=tree if turns_section else None),
                        "the fixture's document is written: %s" % em.asm_checkpoint_stats().get("skipped"))
        restart()
        km._parse(path, sid, self.now)
        self.assertEqual(km._parse_mode.get(path), "restore", "the parse after the restart restores from the document")


class CutFromTheDisplayParse(Base):
    def _session(self, n=60, size=40000, compact_after=None):
        recs, last, t = turns(self.now - 100000, n, size, compact_after=compact_after)
        return self.parent(recs), recs

    def test_a_whole_entry_answers_with_no_transcript_read(self):
        path, recs = self._session()
        km._parse(path, PARENT, self.now)                 # the chat's own parse of the session the comment is made on
        self.assertGreater(os.path.getsize(path), 2_000_000)
        for anchor in ("ka30", "kt30", "kr45"):           # prose, a tool call, a tool result (its prose ancestor is the cut)
            zero_reads_cold()
            got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, anchor)
            self.assertEqual(rebuilt, 0, "the walk read the graph the parse holds, never a rebuild from the transcript (%s)" % anchor)
            self.assertLess(read_of(path), 4096, "…and no bytes (%s)" % anchor)
            self.assertEqual(got, self.cold(path, anchor), anchor)
        self.assertEqual(self.roads()["whole"], 3)
        self.assertEqual(self.roads()["read"], 0)
        self.assertEqual(km._comment_cut_resolve(path, PARENT, "kt30")[0], "ku30", "a tool call cuts at the question above it")

    def test_a_restored_entry_reads_back_only_the_records_the_walk_needs(self):
        path, recs = self._session()
        self.restored(path)
        size = os.path.getsize(path)
        for anchor in ("ka10", "kt20", "kr25", "ka58"):   # all but the last are before the document's cut
            zero_reads_cold()
            got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, anchor)
            self.assertEqual(rebuilt, 0, anchor)
            self.assertLess(read_of(path), 64 * 1024, "a few records read back by offset, not the %d bytes before the cut (%s)"
                            % (size, anchor))
            self.assertEqual(got, self.cold(path, anchor), anchor)
        self.assertEqual(self.roads()["seeded"], 4)
        self.assertEqual(self.roads()["read"], 0)

    def test_a_transcript_rewritten_in_place_since_the_restore_is_read_and_said(self):
        # the file moved after its pre-cut records were hydrated, without its stat moving (rewritten in place at the same
        # size, its mtime put back): the entry and the memo still stand, a pre-cut record's by-offset read finds another
        # record at its offset and raises the lookup's miss, and the create reads the transcript, says so once on stderr,
        # and cuts as the cold walk does. Hydrated first, since an answer from the memo would meet no miss
        path, recs = self._session()
        self.restored(path)
        em.hydrate(km._parse(path, PARENT, self.now), PARENT, by="test")
        self.assertIn("ka10", em._HYDRATED, "the record the cut reads is in the memo before the file moves")
        lines = Path(path).read_bytes().split(b"\n")
        at = {json.loads(ln)["uuid"]: i for i, ln in enumerate(lines) if ln}
        a, b = at["ka10"], at["ka11"]
        self.assertEqual(len(lines[a]), len(lines[b]), "two answers of the same length")
        lines[a], lines[b] = lines[b], lines[a]
        st = os.stat(path)
        with open(path, "r+b") as fh:
            fh.write(b"\n".join(lines))
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        now = os.stat(path)
        self.assertEqual((now.st_size, now.st_mtime_ns, now.st_ino), (st.st_size, st.st_mtime_ns, st.st_ino))
        km._cut_read_said.discard((PARENT, "miss"))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = km._comment_cut_resolve(path, PARENT, "ka10")
        self.assertEqual(self.roads()["read"], 1)
        said = [ln for ln in err.getvalue().splitlines() if ln.startswith("comment cut for")]
        self.assertEqual(len(said), 1, err.getvalue())
        self.assertIn("miss (record ka10", said[0])
        self.assertEqual(got, self.cold(path, "ka10"))
        self.assertEqual(got[0], "ka10")

    def test_a_pre_compaction_passage_still_opens_a_tip_fork_with_what_surrounded_it(self):
        path, recs = self._session(compact_after=40)
        for road in ("whole", "seeded"):
            if road == "seeded":
                self.restored(path)
                entry = em._ASM_CACHE[(os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))]
                # kr33's walk after the passage reaches turn 33's message sent mid-turn, which lies before the cut
                self.assertEqual(self._side_of_cut(entry, "kq33"), "before",
                                 "premise: turn 33 has a splice before the cut")
                self.assertIn("kq33", em._precut_locations(entry, PARENT)[2], "premise: the parse made an atom of it")
            else:
                km._parse(path, PARENT, self.now)
            for anchor in ("ka20", "ku21", "kr33"):       # an answer, a question, a tool result: all before the boundary
                zero_reads_cold()
                got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, anchor)
                self.assertEqual(rebuilt, 0, "%s %s" % (road, anchor))
                self.assertLess(read_of(path), 64 * 1024, "%s %s" % (road, anchor))
                want = self.cold(path, anchor)
                self.assertEqual(got, want, "%s %s" % (road, anchor))
                self.assertEqual(got[0], "", "a tip fork: the CLI loads only post-boundary records")
                self.assertIn(km._PRECOMPACT_LEAD, got[3], "the opener carries what surrounded the passage")
            after = km._comment_cut_resolve(path, PARENT, "ka50")
            self.assertEqual(after, (("ka50", after[1], None, "")), "a passage after the boundary cuts at itself")
        self.assertEqual(self.roads()["read"], 0)

    # ── a message the person sent while the agent was working (the review of #37) ─────────────────────────────────────
    # The CLI records one it splices into the running turn as a queued_command attachment. The walk asks the
    # display parse's atom for the attachment who sent it: the entry's atoms on a whole entry or past a restored entry's
    # cut, the atom's row before the cut.

    ROADS = (("whole", None), ("seeded", True), ("seeded", False))   # a restored entry with the turns section, and without

    def _on_road(self, path, road, turns_section):
        """The parse `road` names for `path`: a whole entry, or one restored from a document written with or without the
        turns section. Returns the assembly entry."""
        restart()
        shutil.rmtree(jd.STATE / "checkpoints", ignore_errors=True)   # the next parse is whole, so a document can be written
        if road == "seeded":
            self.restored(path, turns_section=turns_section)
        else:
            km._parse(path, PARENT, self.now)
        entry = em._ASM_CACHE[(os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))]
        if road == "seeded":
            self.assertEqual(entry.get("index") is not None, turns_section, "premise: the pre-cut part's form")
        return entry

    def _side_of_cut(self, entry, u):
        """Where record `u` lies on a restored entry: "past" the document's cut (the tail's adapter holds it) or "before"
        it (a fact of the seed only)."""
        if u in entry["ad"].by_uuid:
            return "past"
        self.assertIn(u, entry["ad"].seed["types"], "premise: %s is a record of the session" % u)
        return "before"

    def _walk(self, path, anchor):
        """(the cut walk's answer for `anchor`, the uuids it read back by offset), with the whole-read and byte pins, and the
        pin that the parse's own atoms answer: the walk runs no splice emit of its own (FileAdapter._absorbed), which only
        the fresh read's road needs."""
        zero_reads_cold()
        read, real, emits, real_emit = [], em._read_record_at, [], em.FileAdapter._absorbed
        em._read_record_at = lambda p, at, u: (read.append(u), real(p, at, u))[1]
        em.FileAdapter._absorbed = lambda ad, *a, **k: (emits.append(1), real_emit(ad, *a, **k))[1]
        try:
            got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, anchor)
        finally:
            em._read_record_at, em.FileAdapter._absorbed = real, real_emit
        self.assertEqual(rebuilt, 0, "never a rebuild from the transcript")
        self.assertEqual(emits, [], "the display parse's atoms answer, never a splice emit of the walk's own")
        self.assertLess(read_of(path), 64 * 1024, "a few records read back by offset at most")
        self.assertEqual(got, self.cold(path, anchor), "a fresh read of the transcript answers the same")
        return got, read

    @staticmethod
    def _text(recs, u):
        c = next(r for r in recs if r.get("uuid") == u)["message"]["content"]
        return c if isinstance(c, str) else c[0]["text"]

    def test_a_message_sent_mid_turn_on_both_roads(self):
        # turn 33, before the compaction: the question ku33, a token reminder, the agent's sentence kp33, its tool call and
        # result, a hook's result, the correction kq33 sent while the tool ran, the answer ka33, then turn 34's question.
        # The correction's prompt is a string or a content-block list (blocks); on a restored entry it lies before the cut,
        # read back by its row's offset in either form
        path, recs = self._session(compact_after=40)
        q = lambda u: self._text(recs, u)
        want = {"kp33": (("I", q("ku33")), ("I", correction(33))),      # the agent's sentence: the correction follows it
                "kq33": (("you", q("kp33")), ("you", q("ka33"))),       # the correction itself: the agent on both sides
                "ka33": (("I", correction(33)), ("I", q("ku34")))}      # the answer: the correction it answers before it
        for shape, prompt in (("string", correction(33)), ("blocks", blocks(correction(33)))):
            write(path, [dict(r, attachment=dict(r["attachment"], prompt=prompt)) if r.get("uuid") == "kq33" else r
                         for r in recs])
            for road, form in self.ROADS:
                entry = self._on_road(path, road, form)
                if road == "seeded":
                    self.assertEqual(self._side_of_cut(entry, "kq33"), "before", "premise: the correction is before the cut")
                for anchor, (before, after) in want.items():
                    with self.subTest(prompt=shape, road=road, turns_section=form, anchor=anchor):
                        got, read = self._walk(path, anchor)
                        self.assertEqual(got[0], "", "a tip fork")
                        self.assertIn("Right before it, %s said:\n\n> %s\n\n" % before, got[3])
                        self.assertIn("Right after it, %s said:\n\n> %s\n\n" % after, got[3])
                        self.assertFalse(set(read) & {"kt33", "kr33"}, "a tool call and its result are never read back")
                        if road == "seeded":
                            self.assertIn("kq33", read, "premise: the correction is read back by its row's offset")
        self.assertEqual(self.roads()["read"], 0)

    def test_a_message_with_line_breaks_keeps_them_on_both_roads(self):
        # turn 33's correction written over eight lines, as a string or a content-block list (blocks): a blank line, one
        # ending in two spaces, one indented with spaces, one led by a tab and one holding only a space; and, as a
        # string, a correction whose every line is indented. Each excerpt beside it quotes every line as its own and
        # keeps the whitespace inside the words, on every road (on a restored entry the correction is before the cut,
        # read back by offset). Red if TranscriptGraph.splice_text, or the read back by offset before the cut, collapses
        # the words' whitespace, strips a line's leading or trailing spaces, expands a tab, empties a line that holds
        # only a space, or removes the margin every line shares
        path, recs = self._session(compact_after=40)
        lines = (correction(33) + "\n\nAnd:\n- log each page fetch  \n  - with its status code\n\t- and its time\n \n"
                 "- that is all")
        per_line = ("> %s\n> \n> And:\n> - log each page fetch  \n>   - with its status code\n> \t- and its time\n>  \n"
                    "> - that is all\n\n" % correction(33))
        margin = "    " + correction(33) + "\n    - log each page fetch"
        for shape, prompt, quoted in (("string", lines, per_line), ("blocks", blocks(lines), per_line),
                                      ("margin", margin, "> %s\n>     - log each page fetch\n\n" % correction(33))):
            write(path, [dict(r, attachment=dict(r["attachment"], prompt=prompt)) if r.get("uuid") == "kq33" else r
                         for r in recs])
            for road, form in self.ROADS:
                entry = self._on_road(path, road, form)
                if road == "seeded":
                    self.assertEqual(self._side_of_cut(entry, "kq33"), "before",
                                     "premise: the correction is before the cut")
                for anchor, label in (("kp33", "Right after it, I said:\n\n"),
                                      ("ka33", "Right before it, I said:\n\n")):
                    with self.subTest(prompt=shape, road=road, turns_section=form, anchor=anchor):
                        got, _read = self._walk(path, anchor)
                        self.assertIn(label + quoted, got[3])
        self.assertEqual(self.roads()["read"], 0)

    def test_two_messages_sent_at_one_tool_boundary_on_both_roads(self):
        # two messages the person sent while turn 33's tool ran, both spliced in at its boundary: the correction kq33 and a
        # second one, kq33b, chained after it before the answer ka33. A comment on either quotes the agent on both sides,
        # its sentence kp33 before the pair and the answer after it, so each walk passes over the other message on the
        # passage's own side; the sentence is followed by the first message and the answer preceded by the second. On a
        # restored entry both lie before the cut
        path, recs = self._session(compact_after=40)
        second = "step 33: and log each page fetch, please."
        q = next(r for r in recs if r.get("uuid") == "kq33")
        at = next(i for i, r in enumerate(recs) if r.get("uuid") == "ka33")
        recs[at] = dict(recs[at], parentUuid="kq33b")
        recs.insert(at, splice(int(em.parse_z(q["timestamp"])), second, "kq33b", "kq33"))
        write(path, recs)
        shown = [em._text_of(a["message"]["content"]) for a in em.FileAdapter([path], path).atoms(PARENT, {})
                 if a.get("absorbed")]
        self.assertEqual([shown.count(w) for w in (correction(33), second)], [1, 1],
                         "premise: the chat shows a bubble for each message")
        txt = lambda u: self._text(recs, u)
        want = {"kq33": (("you", txt("kp33")), ("you", txt("ka33"))),
                "kq33b": (("you", txt("kp33")), ("you", txt("ka33"))),
                "kp33": (("I", txt("ku33")), ("I", correction(33))),
                "ka33": (("I", second), ("I", txt("ku34")))}
        for road, form in self.ROADS:
            entry = self._on_road(path, road, form)
            if road == "seeded":
                self.assertEqual([self._side_of_cut(entry, u) for u in ("kq33", "kq33b")], ["before", "before"],
                                 "premise: both messages are before the cut")
            for anchor, (before, after) in want.items():
                with self.subTest(road=road, turns_section=form, anchor=anchor):
                    got, _read = self._walk(path, anchor)
                    self.assertEqual(got[0], "", "a tip fork")
                    self.assertIn("Right before it, %s said:\n\n> %s\n\n" % before, got[3])
                    self.assertIn("Right after it, %s said:\n\n> %s\n\n" % after, got[3])
        self.assertEqual(self.roads()["read"], 0)

    def test_a_message_sent_mid_turn_after_a_compaction_past_a_restored_cut(self):
        # an auto-compaction fires in the middle of turn 58, after its hook's result; the agent goes on with a second tool
        # call, and the person's correction kq58 lands after it. On a restored entry the document cuts at the boundary, so
        # the agent's sentence kp58 lies before the cut and the correction past it, where the tail's atoms answer for it:
        # a comment on kp58 quotes the correction after it on every road. Written over eight lines as a string or a
        # content-block list (blocks), with a blank line, one ending in two spaces, one indented with spaces, one led
        # by a tab and one holding only a space, or as a string whose every line is indented, each line is quoted as
        # its own and the whitespace inside the words is kept
        recs, _last, _t = turns(self.now - 100000, 60, 40000)
        at = next(i for i, r in enumerate(recs) if r.get("uuid") == "kq58")
        q = recs[at]
        t = int(em.parse_z(q["timestamp"]))
        recs[at:at + 1] = [boundary(t, "kbm", "kh58"), summary(t, "ksm", "kbm"),
                           tool_use(t + 1, "kt58b", "ksm"), tool_result(t + 1, "kr58b", "kt58b", "kt58b", 2000),
                           dict(q, parentUuid="kr58b", timestamp=iso(t + 1))]
        lines = (correction(58) + "\n\nAnd:\n- log each page fetch  \n  - with its status code\n\t- and its time\n \n"
                 "- that is all")
        per_line = ("> %s\n> \n> And:\n> - log each page fetch  \n>   - with its status code\n> \t- and its time\n>  \n"
                    "> - that is all\n\n" % correction(58))
        for shape, prompt, quoted in (("one line", correction(58), "> %s\n\n" % correction(58)),
                                      ("lines", lines, per_line), ("blocks", blocks(lines), per_line),
                                      ("margin", "    " + correction(58) + "\n    - log each page fetch",
                                       "> %s\n>     - log each page fetch\n\n" % correction(58))):
            path = self.parent([dict(r, attachment=dict(r["attachment"], prompt=prompt)) if r.get("uuid") == "kq58"
                                else r for r in recs])
            for road, form in self.ROADS:
                entry = self._on_road(path, road, form)
                with self.subTest(prompt=shape, road=road, turns_section=form):
                    if road == "seeded":
                        self.assertEqual([self._side_of_cut(entry, u) for u in ("kp58", "kq58")], ["before", "past"],
                                         "premise: the passage is before the document's cut and the correction past it")
                    got, _read = self._walk(path, "kp58")
                    self.assertEqual(got[0], "", "a tip fork")
                    self.assertIn("Right after it, I said:\n\n" + quoted, got[3])
        self.assertEqual(self.roads()["read"], 0)

    def test_a_replayed_copy_on_both_roads(self):
        # a copy of a correction carries its send stamp and text under a new uuid. Here the chat shows each correction
        # once (a premise), and the walk passes over the copies to the person's own words. Two copies of kq38 follow the
        # compaction's summary (kc as the fixture writes it, kc2 beside it): the passage before the compaction is
        # followed by the next question. A copy of kq33 (kc36) sits before turn 36's answer: that answer is preceded by
        # its own question. kq38 itself is still quoted beside its turn. On the restored entry kc36 lies before the
        # document's cut with no atom row to read it by, and kc and kc2 past it. The session ends one turn after the
        # compaction, so the document's cut falls at the boundary and the walk after ka40 crosses the summary in the
        # tail (a summary before the cut has no row to read it by)
        path, recs = self._session(n=42, compact_after=40)
        at = next(i for i, r in enumerate(recs) if r.get("uuid") == "kc")
        recs[at + 1] = dict(recs[at + 1], parentUuid="kc2")
        recs.insert(at + 1, dict(recs[at], uuid="kc2", parentUuid="kc"))
        at = next(i for i, r in enumerate(recs) if r.get("uuid") == "ka36")
        recs[at] = dict(recs[at], parentUuid="kc36")
        recs.insert(at, dict(next(r for r in recs if r.get("uuid") == "kq33"), uuid="kc36", parentUuid="kh36"))
        write(path, recs)
        shown = [em._text_of(a["message"]["content"]) for a in em.FileAdapter([path], path).atoms(PARENT, {})
                 if a.get("absorbed")]
        self.assertEqual([shown.count(correction(k)) for k in (33, 38)], [1, 1], "premise: the chat shows each correction once")
        q = lambda u: self._text(recs, u)
        want = {"ka40": "Right after it, I said:\n\n> %s\n\n" % q("ku41"),
                "ka36": "Right before it, I said:\n\n> %s\n\n" % q("ku36"),
                "ka38": "Right before it, I said:\n\n> %s\n\n" % correction(38)}
        for road, form in self.ROADS:
            entry = self._on_road(path, road, form)
            if road == "seeded":
                self.assertEqual([self._side_of_cut(entry, u) for u in ("kq38", "kc36", "ka40", "kc", "kc2")],
                                 ["before"] * 3 + ["past"] * 2, "premise: where the document's cut falls")
                self.assertNotIn("kc36", em._precut_locations(entry, PARENT)[2], "premise: kc36 has no atom row")
            for anchor, said in want.items():
                with self.subTest(road=road, turns_section=form, anchor=anchor):
                    got, _read = self._walk(path, anchor)
                    self.assertIn(said, got[3])
        self.assertEqual(self.roads()["read"], 0)

    def test_a_peers_message_spliced_before_the_cut_is_nobody_and_costs_no_read(self):
        # romp's own mid-turn feeds arrive the same way: a peer's mail, carrying its message marker, spliced where turn 33's
        # correction sat. The atom's author is the peer's, so on the restored entry its row answers without a read, and the
        # excerpt before the answer is the person's question above it
        path, recs = self._session(compact_after=40)
        mail = "The tags route is done on my side. <!-- romp-msg-id: 11111111-2222-3333-4444-666666666666 -->"
        recs = [dict(r, attachment=dict(r["attachment"], prompt=mail)) if r.get("uuid") == "kq33" else r for r in recs]
        write(path, recs)
        q = lambda u: self._text(recs, u)
        for road, form in self.ROADS:
            entry = self._on_road(path, road, form)
            with self.subTest(road=road, turns_section=form):
                if road == "seeded":
                    self.assertEqual(self._side_of_cut(entry, "kq33"), "before", "premise: the splice is before the cut")
                    self.assertIn("kq33", em._precut_locations(entry, PARENT)[2], "premise: the parse made an atom of it")
                got, read = self._walk(path, "ka33")
                self.assertIn("Right before it, I said:\n\n> %s\n\n" % q("ku33"), got[3])
                self.assertNotIn("tags route is done", got[3])
                self.assertNotIn("kq33", read, "the peer's splice is never read back")
        self.assertEqual(self.roads()["read"], 0)

    def test_a_splice_the_restored_entry_cannot_answer_for_reads_the_transcript(self):
        # here the person's correction kq33, before the cut: when its atom row gives no location to read the record by
        # (either form), or its turns-section row does not decode, the walk raises GraphMiss and the create reads the
        # transcript: the right excerpt, the read road counted, never one with the correction silently dropped
        path, recs = self._session(compact_after=40)
        for form, arm in ((True, "location"), (False, "location"), (True, "row")):
            entry = self._on_road(path, "seeded", form)
            with self.subTest(turns_section=form, arm=arm):
                if arm == "location":
                    real = em._SeededGraph._loc
                    em._SeededGraph._loc = lambda g, u: None if u == "kq33" else real(g, u)
                    undo = lambda: setattr(em._SeededGraph, "_loc", real)
                else:
                    index, bad = entry["index"], set(em._precut_locations(entry, PARENT)[2]["kq33"])
                    real = index._row

                    def row(k):
                        if k in bad:
                            raise em.LazyIndexError("a row of kq33 that does not decode")
                        return real(k)
                    index._row = row
                    undo = lambda: delattr(index, "_row")
                roads = dict(km._CUT_ROADS)
                try:
                    got = km._comment_cut_resolve(path, PARENT, "ka33")
                finally:
                    undo()
                self.assertEqual(km._CUT_ROADS["read"] - roads["read"], 1, "the read road counted once")
                self.assertIn("Right before it, I said:\n\n> %s\n\n" % correction(33), got[3])
                self.assertEqual(got, self.cold(path, "ka33"))

    def test_the_walk_builds_its_map_of_splices_once(self):
        # on a whole entry, each walk below asks for more than one attachment's atom and builds its map from attachment
        # to atom in one pass over the entry's atoms
        path, recs = self._session(compact_after=40)
        entry = self._on_road(path, "whole", None)

        class Counted(list):
            passes = 0

            def __iter__(self):
                self.passes += 1
                return super().__iter__()
        for anchor in ("kp33", "kq33", "kr33"):           # the agent's sentence, the correction, the tool's result
            with self.subTest(anchor=anchor):
                atoms, met = Counted(entry["atoms"]), []
                g = em.TranscriptGraph(entry["ad"], atoms)
                ask = g.splice_author
                g.splice_author = lambda u: (met.append(u), ask(u))[1]
                got = km._comment_cut_walk(g, path, anchor)
                self.assertGreaterEqual(len(met), 2, "premise: the walk meets attachments")
                self.assertEqual(atoms.passes, 1)
                self.assertEqual(got, self.cold(path, anchor))

    def _seamed(self):
        """A machine-cut resume that forked fresh-headed: the old records in the resumed-from file, the new ones in the
        current file, joined by the states resumeFork row."""
        old, _last, t = turns(self.now - 100000, 30, 20000, tag="o")
        write(self.proj / (PARENT + ".jsonl"), old)
        new, _l2, _t2 = turns(t + 100, 10, 20000, parent=None, tag="n")
        cur = write(self.proj / (SEAM_NEW + ".jsonl"), new)
        sdir = jd.STATE / "states"
        sdir.mkdir(parents=True, exist_ok=True)
        (sdir / (PARENT + ".jsonl")).write_text(json.dumps({"resumeFork": {"from": PARENT, "to": SEAM_NEW}, "t": t + 50}) + "\n")
        return cur

    def test_a_restart_seam_still_falls_back_to_a_tip_fork(self):
        cur = self._seamed()
        km._parse(cur, PARENT, self.now)
        for anchor, cut in (("oa10", ""), ("ot12", ""), ("na5", "na5"), ("nt6", "nu6")):
            zero_reads_cold()
            got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, cur, PARENT, anchor)
            self.assertEqual(rebuilt, 0, "the stitched chain the parse holds answers across the seam (%s)" % anchor)
            self.assertLess(read_of(cur) + read_of(self.proj / (PARENT + ".jsonl")), 4096, anchor)
            self.assertEqual(got, self.cold(cur, anchor), anchor)
            self.assertEqual(got[0], cut, anchor)
            self.assertIsNone(got[2])
        self.assertEqual(self.roads()["read"], 0)

    def test_an_unknown_or_rewound_anchor_is_refused_as_before(self):
        path, recs = self._session(n=20)
        branch = aline(self.now - 50, "an answer the user rewound away", "gone1", parent="ku5")
        write(path, recs + [branch])
        write(path, recs + [branch, uline(self.now - 40, "go on", "after1", parent=recs[-1]["uuid"])])
        km._parse(path, PARENT, self.now)
        got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, "nope")
        self.assertEqual((got[2], rebuilt), (km.ANCHOR_LAG_ERR, 0))
        got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, "gone1")
        self.assertIn("rewound away", got[2])
        self.assertEqual(rebuilt, 0)
        self.assertEqual(self.roads()["read"], 0)

    def test_an_idle_parent_whose_records_the_reader_dropped_still_answers_from_its_parse(self):
        # the reader lets an idle file's records go (the quiescence drop) while the parse's entry keeps its graph: the entry
        # is current by the files' stat, so the walk still reads nothing (keyed on the reader's generation, it read the
        # whole transcript twice: once to find the entry stale, once for the walk; 3 s at 110 MB in the lab)
        for road in ("whole", "seeded"):
            path, recs = self._session()
            if road == "seeded":
                self.restored(path)
            else:
                km._parse(path, PARENT, self.now)
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(path)
            zero_reads()
            got = km._comment_cut_resolve(path, PARENT, "kr25")
            self.assertLess(read_of(path), 64 * 1024, road)
            self.assertEqual(got, self.cold(path, "kr25"), road)
            restart()
        self.assertEqual(self.roads()["read"], 0)

    def test_a_document_write_over_the_parse_does_not_hold_the_walk(self):
        # the settle's document write holds the session's key lock for its whole length (14 s at 110 MB in the lab) and only
        # reads the entry, so the walk reads beside it; any other holder (a fold, a parse) is waited out as before
        path, recs = self._session()
        km._parse(path, PARENT, self.now)
        key = (os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))
        scope = em._DocWriterScope(key)
        scope.__enter__()
        out, done = {}, threading.Event()
        try:
            threading.Thread(target=lambda: (out.update(got=km._comment_cut_resolve(path, PARENT, "kr25")), done.set()),
                             daemon=True).start()
            self.assertTrue(done.wait(10), "answered while the document write holds the lock")
        finally:
            scope.__exit__(None, None, None)
        self.assertEqual(out["got"], self.cold(path, "kr25"))
        lock, waited = em._asm_key_lock(key), threading.Event()
        lock.acquire()
        try:
            threading.Thread(target=lambda: (km._comment_cut_resolve(path, PARENT, "kr25"), waited.set()), daemon=True).start()
            self.assertFalse(waited.wait(0.3), "a holder that may change the graph is waited out")
        finally:
            lock.release()
        self.assertTrue(waited.wait(10))
        self.assertEqual(self.roads()["read"], 0)

    def test_a_record_appended_after_the_parse_is_found_by_parsing_current_first(self):
        # the reply the user comments on can land between the chat's last build and the create: the entry is stale, the
        # parse folds the append, and the walk finds it there (no lag refusal, no whole read)
        path, recs = self._session(n=20)
        km._parse(path, PARENT, self.now)
        with open(path, "a") as fh:
            fh.write(json.dumps(aline(self.now - 5, "a fresh answer about the tags route", "fresh1", recs[-1]["uuid"])) + "\n")
        zero_reads()
        got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, "fresh1")
        self.assertEqual((got[0], rebuilt), ("fresh1", 0))
        self.assertLess(read_of(path), 64 * 1024, "only the appended record was read")
        self.assertEqual(self.roads()["read"], 0)


class StubBackend:
    """The reads a comments frame makes of the backend, for threads with no process (state ""): owns() True, so the thread
    builds through the chat's builder as a live SDK session's does."""
    def session_state(self, sid):
        return ""

    def launch_error(self, sid):
        return None

    def pending_cut(self, sid):
        return ""

    def owns(self, sid):
        return True

    def pending_queued(self, sid):
        return []

    def live_atoms(self, sid):
        return []

    def prune_live(self, sid, tx_uuids, tx_user_texts=(), human_floor=0):
        return None

    def live_sessions(self):
        return {}

    def session_since(self, sid):
        return 0

    def session_meta(self, sid):
        return {}


class ThreadFrameReadsOwnTails(Base):
    """A comments frame over threads whose transcripts each copy a large parent history reads each thread's own records."""

    def setUp(self):
        super().setUp()
        stub = StubBackend()
        km._sdk = lambda: stub

    def _seed(self, n_threads=3, tip=False):
        hist, last, t = turns(self.now - 100000, 40, 60000)
        cut = "ka39"
        rows, own_bytes = [], {}
        for i, tsid in enumerate(THREADS[:n_threads]):
            opener = km._comment_first_message("the notes route pages by cursor", "Why a cursor and not an offset? (%d)" % i)
            own = [uline(t + 100, opener, "c%du" % i, parent=cut),
                   aline(t + 110, "A cursor stays stable while notes are inserted. (%d)" % i, "c%da" % i, parent="c%du" % i)]
            p = write(self.proj / (tsid + ".jsonl"), hist + own)
            own_bytes[tsid] = sum(len(json.dumps(r)) + 1 for r in own) + len(json.dumps(hist[-1])) + 1
            (jd.SDKDIR / (tsid + ".json")).write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": self.cdir, "lastSid": tsid, "alive": True,
                 "threadOf": PARENT}))
            rows.append({"tid": tsid, "sid": tsid, "anchorUuid": cut, "cutUuid": "" if tip else cut,
                         "exact": "the notes route pages by cursor", "status": "open",
                         "createdT": (t + 50) if tip else (t + 90), "lastSeenT": t + 90, "name": "web-comment-%d" % (i + 1)})
            self.assertGreater(os.path.getsize(p), 2_000_000)
        km._save_comments(PARENT, {"threads": rows})
        return own_bytes

    def _check_frame(self, own_bytes, n):
        fr = km._comments_frame(PARENT)
        self.assertEqual(len(fr["threads"]), n)
        for th in fr["threads"]:
            i = THREADS.index(th["tid"])
            self.assertEqual([m["who"] for m in th["msgs"]], ["you", "agent"], th["msgs"])
            self.assertEqual(th["msgs"][0]["text"], "Why a cursor and not an offset? (%d)" % i, "the comment alone, its frame stripped")
            self.assertTrue(any(e.get("kind") == "assistant" for e in th["events"]), "the popover's events are built")
            self.assertFalse(any("step 3 done" in json.dumps(e) for e in th["events"]), "never the copied history")
        return fr

    def test_a_frame_reads_each_threads_own_records_not_the_copied_history(self):
        own = self._seed()
        zero_reads()
        self._check_frame(own, 3)
        for tsid in THREADS[:3]:
            p = self.proj / (tsid + ".jsonl")
            self.assertLess(read_of(p), own[tsid] + SCAN_BLOCK + 4 * GUARD,
                            "the thread's own records and one scan block, not its %d bytes" % os.path.getsize(p))
        # an unchanged thread is served: a stat and its guard
        zero_reads()
        self._check_frame(own, 3)
        for tsid in THREADS[:3]:
            self.assertLess(read_of(self.proj / (tsid + ".jsonl")), 4 * GUARD)
        # a reply landing reads that thread's own records again, nothing before them
        p = self.proj / (THREADS[0] + ".jsonl")
        with open(p, "a") as fh:
            fh.write(json.dumps(uline(self.now - 5, "and for the tags route?", "c0u2", parent="c0a")) + "\n")
        zero_reads()
        km._comments_frame(PARENT)
        self.assertLess(read_of(p), own[THREADS[0]] + 4096 + 4 * GUARD)

    def test_a_tip_forked_threads_own_part_starts_past_the_copied_history(self):
        own = self._seed(n_threads=2, tip=True)
        zero_reads()
        self._check_frame(own, 2)
        for tsid in THREADS[:2]:
            p = self.proj / (tsid + ".jsonl")
            self.assertLess(read_of(p), own[tsid] + SCAN_BLOCK + 4 * GUARD)

    def test_a_thread_whose_transcript_lacks_its_cut_reads_as_before(self):
        # a resumed thread's current file can start past the cut (an older file of its lineage holds it): no boundary in
        # this file, so the projections read the whole thread as they always did, and say what they can
        self._seed(n_threads=1)
        p = self.proj / (THREADS[0] + ".jsonl")
        write(p, [uline(self.now - 30, "a resumed thread's first record", "z1"),
                  aline(self.now - 20, "its reply", "z2", parent="z1")])
        self.assertIsNone(km._thread_tail_parse(THREADS[0], str(p), "ka39", 0, self.now))
        zero_reads()
        self.assertIsNone(km._thread_own_offset(THREADS[0], str(p), os.stat(p), "ka39", 0))
        self.assertEqual(read_of(p), 0, "a transcript scanned whole without its cut is not read back again")
        km._comments_frame(PARENT)                       # no raise: the whole road answers


class FakeBackend:
    def __init__(self):
        self.calls = []

    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="", model="", effort="", fast=""):
        self.calls.append(("fork", name, cut_uuid))
        reg = {"sid": sid, "name": name, "cwd": self.cdir, "lastSid": parent_sid, "alive": True, "threadOf": thread_of,
               "forkOf": parent_sid}
        (jd.SDKDIR / (sid + ".json")).write_text(json.dumps(reg))   # the fork pending, as the backend writes it
        return sid

    def connect(self, sid):
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid))
        return True

    def kill(self, sid):
        return True


class CreateAnswersFast(Base):
    """The create door on a huge parent: off the socket thread, answered the moment the row exists, with the thread's row."""

    def setUp(self):
        super().setUp()
        self.be = FakeBackend()
        self.be.cdir = self.cdir
        self._saved_fns = (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for, km._push_session_now,
                           km._live_map, km._kernel_knows, getattr(km, "_comment_create_spawn", None))
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._sdk_ready = lambda: True
        km._live_map = lambda: {}
        km._kernel_knows = lambda sid: sid == PARENT
        km._reveal_chat_for = lambda client, msg: None
        km._push_session_now = lambda sid: None
        (jd.NAMES / PARENT).write_text("web\t%s\t\t\n" % self.cdir)
        with km._create_lock:
            km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
        self.acked = threading.Event()
        self.sent = []

        def send(s):
            f = json.loads(s)
            self.sent.append(f)
            if f.get("type") in ("commentCreated", "commentCreateFailed"):
                self.acked.set()
        self.client = {"send": send, "app": "chat"}

    def tearDown(self):
        (km.Sessions.backend_for, km._sdk_ready, km._sessions, km._reveal_chat_for, km._push_session_now,
         km._live_map, km._kernel_knows, km._comment_create_spawn) = self._saved_fns
        super().tearDown()

    def _huge_parent(self, mb=100):
        n = 400
        recs, last, t = turns(self.now - 200000, n, mb * 1024 * 1024 // n)
        path = self.parent(recs)
        km._sessions = lambda now, window=None, forks=True: [{"sid": PARENT, "name": "web", "path": path, "mtime": self.now}]
        return path

    def _create(self, anchor, cid="g-1"):
        return {"type": "commentCreate", "id": PARENT, "uuid": anchor, "exact": "the notes route pages by cursor",
                "text": "Why a cursor and not an offset?", "name": "", "createId": cid}

    def test_the_answer_arrives_within_a_bound_on_a_hundred_megabyte_parent(self):
        path = self._huge_parent(100)
        self.assertGreater(os.path.getsize(path), 95 * 1024 * 1024)
        km._parse(path, PARENT, self.now)                 # the chat holds the session open: its parse is in hand
        zero_reads()
        t0 = time.monotonic()
        km._drive(self._create("ka200"), self.client)
        handler = time.monotonic() - t0
        self.assertTrue(self.acked.wait(30), "the create answered")
        answered = time.monotonic() - t0
        acks = [f for f in self.sent if f.get("type") == "commentCreated"]
        self.assertEqual(len(acks), 1, self.sent)
        self.assertLess(handler, 0.25, "the socket thread is handed back at once (%.3f s)" % handler)
        self.assertLess(answered, 1.0, "Enter to answer, on a 100 MB parent (%.3f s)" % answered)
        self.assertLess(read_of(path), 1024 * 1024, "the create read none of the parent's transcript")
        ack = acks[0]
        self.assertEqual(ack["createId"], "g-1")
        th = ack.get("thread") or {}
        self.assertEqual(th.get("tid"), ack["tid"], "the answer carries the new thread's row")
        self.assertEqual((th.get("status"), th.get("anchorUuid"), th.get("replyOwed")), ("open", "ka200", True))
        self.assertFalse(any(f.get("type") == "comments" for f in self.sent), "the frame is the pusher's, not the create's")
        self.assertEqual(self.be.calls[0], ("fork", th.get("name"), "ka200"))

    def test_the_socket_thread_is_free_while_the_create_runs(self):
        path = self._huge_parent(1)
        km._parse(path, PARENT, self.now)
        gate, entered = threading.Event(), threading.Event()
        real = km._comment_create

        def held(*a, **k):
            entered.set()
            gate.wait(10)
            return real(*a, **k)
        km._comment_create = held
        try:
            done = threading.Event()
            threading.Thread(target=lambda: (km._drive(self._create("ka20"), self.client), done.set()), daemon=True).start()
            self.assertTrue(entered.wait(5), "the create started")
            self.assertTrue(done.wait(2), "the door returned while the create was still running")
            self.assertFalse(self.acked.is_set(), "nothing answered yet")
            # a re-post of the same gesture meanwhile is answered busy at the door, never a second create
            km._drive(self._create("ka20"), self.client)
            self.assertEqual([f.get("transient") for f in self.sent if f.get("type") == "commentCreateFailed"], [True])
            gate.set()
            self.assertTrue(self._wait_for_ack(), "the create answered once it ran")
        finally:
            gate.set()
            km._comment_create = real
        self.assertEqual(len(km._load_comments(PARENT).get("threads") or []), 1)

    def _wait_for_ack(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if any(f.get("type") == "commentCreated" for f in self.sent):
                return True
            time.sleep(0.01)
        return False


class FrameAfterAnswer(Base):
    """A frame built from the store as it stood before a create saved its row never follows that create's answer."""

    def test_a_frame_built_before_a_save_is_dropped_and_the_next_one_sent(self):
        km._save_comments(PARENT, {"threads": []})
        sent, real = [], km._send_client
        km._send_client = lambda c, key, msg, *a, **k: sent.append((key, msg))
        try:
            gen = km._comments_gen.get(PARENT, 0)        # the pusher reads the generation before it builds
            fr = km._comments_frame(PARENT)
            row = {"tid": THREADS[0], "sid": THREADS[0], "anchorUuid": "a1", "cutUuid": "a1", "exact": "x", "status": "open",
                   "createdT": self.now, "lastSeenT": self.now, "name": "web-comment-1"}
            km._save_comments(PARENT, {"threads": [row]})   # a create saves its row while that frame was being built
            self.assertFalse(km._send_comments_frame([{"app": "chat"}], PARENT, fr, gen))
            self.assertEqual(sent, [], "the stale frame never reaches the page")
            gen2 = km._comments_gen.get(PARENT, 0)
            fr2 = km._comments_frame(PARENT)
            self.assertTrue(km._send_comments_frame([{"app": "chat"}], PARENT, fr2, gen2))
            self.assertEqual([t["tid"] for t in sent[0][1]["threads"]], [THREADS[0]])
        finally:
            km._send_client = real

    def test_the_answer_carries_the_row_the_next_frame_lists(self):
        row = {"tid": THREADS[1], "sid": THREADS[1], "anchorUuid": "a1", "cutUuid": "a1", "exact": "the tags route",
               "status": "open", "createdT": self.now, "lastSeenT": self.now, "name": "web-comment-2", "color": "#336699"}
        (jd.SDKDIR / (THREADS[1] + ".json")).write_text(json.dumps(
            {"sid": THREADS[1], "name": "web-comment-2", "cwd": self.cdir, "lastSid": PARENT, "alive": True,
             "threadOf": PARENT, "forkOf": PARENT}))
        km._save_comments(PARENT, {"threads": [row]})
        got = []
        km._answer_comment_created([{"send": lambda s: got.append(json.loads(s))}], PARENT, THREADS[1], "a1", "g-9")
        self.assertEqual(len(got), 1)
        ack = got[0]
        self.assertEqual((ack["type"], ack["tid"], ack["uuid"], ack["createId"]), ("commentCreated", THREADS[1], "a1", "g-9"))
        self.assertEqual(ack["thread"], km._comments_frame(PARENT)["threads"][0], "the same row the frame lists")


if __name__ == "__main__":
    unittest.main()
