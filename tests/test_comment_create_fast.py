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
  cut and past it; after the record cache's budget released the entry (2026-10-02), one create rebuilds it and the next
  builds no adapter, on a restart seam and a /clear continuation too, and on a session whose stored parse fell back no
  create parses a second time, even with no current road for the leaf (one whose rebuild itself falls back after a
  release parses again at each create until its files change: an induced state, disclosed and pinned);
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
CLEARED = "5e5e5e5e-1212-3434-5656-000000000002"     # the transcript a /clear opened under PARENT's session
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


def drop_reader(path):
    """The reader's records of `path` dropped (a release, a budget eviction), so a later read of it costs its bytes again."""
    rp = os.path.realpath(str(path))
    with em._JSONL_CACHE_LOCK:
        for k in [k for k in em._JSONL_CACHE if os.path.realpath(str(k)) == rp]:
            em._cache_pop_locked(k)


def cleared(test):
    """A /clear world under PARENT: the session's first transcript (20 turns, tag k) and the leaf the clear opened (10 turns,
    tag c, a parentless head). Returns (first, leaf, the leaf's last uuid, the next free time)."""
    old, _last, t = turns(test.now - 100000, 20, 20000)
    first = write(test.proj / (PARENT + ".jsonl"), old)
    new, last, t2 = turns(t + 100, 10, 20000, parent=None, tag="c")
    leaf = write(test.proj / (CLEARED + ".jsonl"), new)
    return first, leaf, last, t2


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
    for memo in ("_thread_own_offsets", "_thread_own_memo", "_thread_tail_memo",   # getattr: an older kernel reds on behavior
                 "_CUT_FIRST_UUIDS"):
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

    def adapters_built(self, fn, *a):
        """fn(*a), and how many transcript adapters (em.FileAdapter) it built: a read of the transcript or a rebuild of the
        entry, whatever road (counted by build, since a reader cache holding the file serves either with no bytes read)."""
        cls, n = em.FileAdapter, []
        real = cls.__init__

        def counting(self_, *x, **k):
            n.append(1)
            return real(self_, *x, **k)
        cls.__init__ = counting
        try:
            return fn(*a), len(n)
        finally:
            cls.__init__ = real

    def counted_create(self, path, anchor, sid=PARENT):
        """km._comment_cut_resolve(path, sid, anchor) and what it cost: (answer, adapters built, event model parses, reads
        of the transcript). A parse is any em.parse_session call (the cut's retry, or the parse store's on a miss); a read
        is the cut's own (_anchor_adapter); an adapter is either one's build."""
        parses, real = [], em.parse_session
        em.parse_session = lambda *x, **k: (parses.append(1), real(*x, **k))[1]
        try:
            (got, reads), built = self.adapters_built(self.walks_reading_the_transcript, km._comment_cut_resolve, path, sid,
                                                      anchor)
        finally:
            em.parse_session = real
        return got, built, len(parses), reads

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

    def test_after_a_budget_eviction_one_create_rebuilds_the_entry_and_the_next_builds_no_adapter(self):
        # the record cache's budget releases the chat's entry with the leaf (2026-10-02); for an idle session the display's
        # next build is then a parse-store hit that rebuilds nothing, so the retry through it found no entry again and every
        # create read the transcript (review round two: about 0.09 s a create on an 18 MB leaf). On "none" the retry
        # rebuilds the entry through the event model's parse with the display's inputs, once
        path, recs = self._session(n=20, size=20000)
        self._two_creates_after_a_budget_eviction(path, [path], ("kr10", "ka12"))

    def test_after_a_budget_eviction_a_seamed_session_is_rebuilt_over_its_states_log(self):
        # a restart seam's link lives in the states log, and the walk takes an entry only when its links are the walk's own
        # (em.with_display_graph): an entry the retry built without that log never matches, so every create would parse
        # again and read the transcript
        cur = self._seamed()
        self._two_creates_after_a_budget_eviction(cur, [cur, str(self.proj / (PARENT + ".jsonl"))], ("nr5", "oa10"))

    def test_after_a_budget_eviction_a_clear_continuation_is_rebuilt_over_its_own_transcript(self):
        # after a /clear the leaf is a new transcript beside the session's first one; its head has no parent, so the display
        # parses it alone (2026-10-03: the first transcript is no candidate of a /clear's leaf), and the entry the retry
        # rebuilds is over the leaf alone, matching the walk's candidates; the first transcript is read by neither
        first, leaf, _last, _t = cleared(self)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "one file: the /clear's leaf alone")
        self._two_creates_after_a_budget_eviction(leaf, [leaf, first], ("cr5", "ca7"))

    def test_a_pre_clear_message_answers_what_the_wide_walk_answers_on_a_whole_and_a_seeded_entry(self):
        # after a /clear the display parse reads the leaf alone, so its graph holds no message from before the clear; a
        # comment on one (selected in the expanded "Conversation cleared" card) answered lag there, and the create was parked
        # with nothing said (review round one). The lag answer is widened to the first transcript: the refusal the wide walk
        # gives, as before the leaf parsed alone. A post-clear target still reads no byte of the first transcript
        for road in ("whole", "seeded"):
            with self.subTest(road=road):
                restart()
                first, leaf, _last, _t = cleared(self)
                if road == "seeded":
                    self.restored(leaf)
                else:
                    km._parse(leaf, PARENT, self.now)
                for anchor in ("ka5", "kt12"):            # a pre-clear reply, a pre-clear tool call
                    got = km._comment_cut_resolve(leaf, PARENT, anchor)
                    want = self.cold(leaf, anchor)
                    self.assertIn("rewound away", want[2] or "", "premise: the wide walk refuses it (%s)" % anchor)
                    self.assertEqual(got, want, "%s: %s" % (road, anchor))
                drop_reader(first)
                zero_reads()
                got = km._comment_cut_resolve(leaf, PARENT, "ca7")
                self.assertEqual(read_of(first), 0, "%s: a post-clear target reads no byte of the first transcript" % road)
                self.assertEqual(got, self.cold(leaf, "ca7"), road)

    def test_a_post_clear_comment_on_the_read_road_reads_no_byte_of_the_first_transcript(self):
        # the read road (no display entry can answer) builds the leaf's own graph first and finds a post-clear target there
        first, leaf, _last, _t = cleared(self)
        real = km._comment_cut_from_parse
        km._comment_cut_from_parse = lambda *a, **k: None
        try:
            for anchor in ("ca7", "cr5"):
                drop_reader(first)
                zero_reads()
                got, reads = self.walks_reading_the_transcript(km._comment_cut_resolve, leaf, PARENT, anchor)
                self.assertEqual(reads, 1, "premise: the read road (%s)" % anchor)
                self.assertEqual(read_of(first), 0, "no byte of the first transcript (%s)" % anchor)
                self.assertEqual(got, self.cold(leaf, anchor), anchor)
        finally:
            km._comment_cut_from_parse = real

    def test_a_pre_clear_comment_on_the_read_road_is_refused_from_the_first_transcripts_uuids_and_reads_it_once(self):
        # review round three: the read road built the graph over both files for a target only the first transcript holds,
        # reading that file whole outside the record cache at every attempt, for the refusal its membership alone gives when
        # no record of the leaf links into it. The first attempt reads it once, for the session's uuid set; the second reads
        # none of it; neither leaves a record cache entry for it
        first, leaf, _last, _t = cleared(self)
        rp = os.path.realpath(first)
        real = km._comment_cut_from_parse
        km._comment_cut_from_parse = lambda *a, **k: None
        try:
            drop_reader(first)
            zero_reads()
            got1, reads = self.walks_reading_the_transcript(km._comment_cut_resolve, leaf, PARENT, "ka5")
            once = read_of(first)
            got2 = km._comment_cut_resolve(leaf, PARENT, "ka5")
            twice = read_of(first) - once
            with em._JSONL_CACHE_LOCK:
                held = [k for k in em._JSONL_CACHE if os.path.realpath(str(k)) == rp]
        finally:
            km._comment_cut_from_parse = real
        want = self.cold(leaf, "ka5")
        self.assertIn("rewound away", want[2] or "", "premise: the wide walk refuses it")
        self.assertEqual(reads, 1, "premise: the read road")
        self.assertEqual({"answers": [got1, got2], "the first attempt's bytes of the first transcript": once,
                          "the second attempt's": twice, "record cache entries for it": held},
                         {"answers": [want, want], "the first attempt's bytes of the first transcript": os.path.getsize(first),
                          "the second attempt's": 0, "record cache entries for it": []})

    def test_an_unreadable_first_transcript_leaves_a_read_road_comment_on_an_unwritten_reply_lagging_and_says_why(self):
        # review round four: on the read road, a target the leaf lacks is decided from the first transcript's uuid set alone,
        # so with that file unreadable (mode 0, restored in the finally) the lookup reports no membership, and a comment on a
        # reply not on disk yet keeps the lag answer, parked until it lands, never ANCHOR_REWOUND_ERR, which parks nothing
        # and would refuse it for good; and the read road says why on stderr, one line naming the error
        if os.geteuid() == 0:
            self.skipTest("root reads any file: the unreadable transcript cannot be staged")
        first, leaf, _last, _t = cleared(self)
        drop_reader(first)                                    # no whole entry to serve the set without a read
        km._CUT_FIRST_UUIDS.pop(PARENT, None)
        km._cut_read_said.discard((PARENT, "unreadable"))
        real, err, mode = km._comment_cut_from_parse, io.StringIO(), os.stat(first).st_mode & 0o777
        km._comment_cut_from_parse = lambda *a, **k: None    # no display entry answers: the read road
        os.chmod(first, 0)
        try:
            with self.assertRaises(PermissionError, msg="premise: the first transcript cannot be opened"):
                open(first, "rb").close()
            with contextlib.redirect_stderr(err):
                got = km._comment_cut_resolve(leaf, PARENT, "cz1")
        finally:
            os.chmod(first, mode)
            km._comment_cut_from_parse = real
        lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
        self.assertEqual({"answer": got[2], "stderr lines": len(lines)}, {"answer": km.ANCHOR_LAG_ERR, "stderr lines": 1},
                         "stderr: %s" % lines)
        self.assertIn("PermissionError", lines[0], "the line names the error")

    def test_the_first_transcripts_read_leaves_its_record_cache_entry_as_it_found_it(self):
        # review round two: the widening read the session's first transcript through the record cache's whole road, which
        # installed its records, or upgraded in place the tail a restore left, and nothing let them go. Its read now leaves the
        # entry as it found it: none stays none, a tail stays that same tail, and a whole entry standing at the file's stat
        # serves the uuids with no read and stays as it stood, at its place in the cache's LRU order (review round three: an
        # entry behind it, the leaf's, stays behind it); the session's set then answers with no read
        first, leaf, _last, _t = cleared(self)
        size = os.path.getsize(first)
        data = Path(first).read_bytes()
        lines = data.split(b"\n")
        k = len(lines) // 2
        off = sum(len(ln) + 1 for ln in lines[:k])
        tail_from = (off, k, data[off - em._JSONL_TAIL_GUARD:off])     # a checkpoint's cut halfway: a restore's tail entry

        def entry():
            with em._JSONL_CACHE_LOCK:
                return em._JSONL_CACHE.get(str(first))

        def order():
            with em._JSONL_CACHE_LOCK:
                return list(em._JSONL_CACHE)
        for held in ("none", "tail", "whole"):
            with self.subTest(held=held):
                restart()
                if held == "tail":
                    em._read_jsonl_entry(first, tail_ok=True, tail_from=tail_from)
                elif held == "whole":
                    em._read_jsonl_entry(first)
                em._read_jsonl_entry(leaf)                    # an entry after it in the LRU order
                before, lru = entry(), order()
                self.assertEqual(before is None, held == "none", "premise: %s" % held)
                if before is not None:
                    self.assertEqual(before[5] > 0, held == "tail", "premise: the entry's form (%s)" % held)
                    self.assertEqual(lru, [str(first), str(leaf)], "premise: the first transcript's entry is not the newest")
                zero_reads()
                uuids, recs, how = km._cut_first_uuids(PARENT, first)
                self.assertTrue({"ku0", "ka5", "kt12", "ka19"} <= uuids and not {"cu0", "ca5"} & uuids, held)
                self.assertEqual((how, recs is not None), ("whole" if held == "whole" else "read", True),
                                 "the first ask reads, or serves from the whole entry (%s)" % held)
                self.assertIs(entry(), before, "the entry as it found it (%s)" % held)
                self.assertEqual(order(), lru, "the LRU order as it found it (%s)" % held)
                self.assertEqual(read_of(first), 0 if held == "whole" else size, held)
                zero_reads()
                again = km._cut_first_uuids(PARENT, first)
                self.assertEqual((again[0], again[1], again[2]), (uuids, None, None), "the set answers while the stat stands")
                self.assertEqual(read_of(first), 0, held)
                self.assertIs(entry(), before, held)
                self.assertEqual(order(), lru, held)

    def test_the_first_transcripts_set_is_kept_only_after_a_read_at_a_stable_stat(self):
        # a read the file moved under (a stat before it unequal to one after) remembers nothing, so the next ask reads again and
        # sees what landed meanwhile
        first, _leaf, _last, _t = cleared(self)
        real = em.read_records_detached

        def moving(p, **k):
            got = real(p, **k)
            with open(first, "a") as fh:                      # a line lands while the file is read
                fh.write(json.dumps(uline(self.now - 50000, "a line written during the read", "kz1", "ka19")) + "\n")
            return got
        em.read_records_detached = moving
        try:
            self.assertEqual(km._cut_first_uuids(PARENT, first), (None, None, "read"), "the file moved under the read")
        finally:
            em.read_records_detached = real
        self.assertNotIn(PARENT, km._CUT_FIRST_UUIDS, "nothing remembered from it")
        uuids, _recs, how = km._cut_first_uuids(PARENT, first)
        self.assertTrue(how == "read" and "kz1" in uuids, "the next ask read the file again, the new line in it")
        self.assertEqual(km._CUT_FIRST_UUIDS[PARENT][1], uuids)

    def test_the_first_transcripts_uuid_sets_are_bounded_the_least_recently_consulted_out(self):
        first = write(self.proj / (PARENT + ".jsonl"), [uline(self.now - 900, "how does the notes route page?", "ku0"),
                                                       aline(self.now - 890, "By cursor, fifty notes a page.", "ka0", "ku0")])
        cap = km._CUT_FIRST_MAX
        sids = ["5e5e5e5e-1212-3434-5656-%012d" % (100 + i) for i in range(cap + 1)]
        for sid in sids[:cap]:
            self.assertIn("ka0", km._cut_first_uuids(sid, first)[0])
        self.assertEqual(list(km._CUT_FIRST_UUIDS), sids[:cap], "one set per session, in the order consulted")
        km._cut_first_uuids(sids[0], first)                   # consulted again: the most recently used now
        km._cut_first_uuids(sids[cap], first)                 # one session more than the bound
        self.assertEqual(len(km._CUT_FIRST_UUIDS), cap, "bounded at %d sessions" % cap)
        self.assertNotIn(sids[1], km._CUT_FIRST_UUIDS, "the least recently consulted went")
        self.assertIn(sids[0], km._CUT_FIRST_UUIDS)
        self.assertIn(sids[cap], km._CUT_FIRST_UUIDS)

    def test_a_widening_that_reads_a_file_counts_as_a_read_and_one_answered_from_the_set_as_its_display_road(self):
        # review round two: the widening's read counted as the display road's walk and said nothing on stderr, against the
        # rule above _CUT_ROADS. A widening that reads a file counts as a read, named on stderr once per session and cause;
        # one the session's uuid set answers reads nothing and counts as the display road it came from. Since review round
        # three, on a restored entry too (the seeded count), with the read's own row in /perf's wholeReads pinned: one
        # detached read of the first transcript's size at the first widening, none at the steps the set answers
        first, leaf, _last, _t = cleared(self)
        row_key = "detached<-_cut_first_records"

        def row():
            r = em.record_cache_stats()["wholeReads"].get(row_key) or {"count": 0, "bytes": 0}
            return r["count"], r["bytes"]
        for road in ("whole", "seeded"):
            with self.subTest(road=road):
                restart()
                if road == "seeded":
                    self.restored(leaf)                       # the chat's parse, restored from its document at a boot
                else:
                    km._parse(leaf, PARENT, self.now)         # the chat's whole entry
                km._cut_read_said.discard((PARENT, "widen"))
                size = os.path.getsize(first)

                def step(anchor):
                    r0 = self.roads()
                    got = km._comment_cut_resolve(leaf, PARENT, anchor)
                    r1 = self.roads()
                    return got, {k: r1[k] - r0[k] for k in r1}
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    zero_reads()
                    w0 = row()
                    pre, d1 = step("ka5")                     # the first widening: the first transcript's set is read
                    read1, w1 = read_of(first), row()
                    zero_reads()
                    _, d2 = step("kt12")                      # another pre-clear target: the set answers
                    lag, d3 = step("cz1")                     # a reply not flushed yet: the set answers
                    read2, w2 = read_of(first), row()
                    st = os.stat(first)
                    os.utime(first, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))   # the file's stat moves: read again
                    _, d4 = step("ka6")
                display = {"whole": int(road == "whole"), "seeded": int(road == "seeded"), "read": 0, "failed": 0}
                self.assertIn("rewound away", pre[2] or "", road)
                self.assertEqual(lag[2], km.ANCHOR_LAG_ERR, road)
                self.assertEqual(d1, {"whole": 0, "seeded": 0, "read": 1, "failed": 0}, "the set's read is a read (%s)" % road)
                self.assertEqual(read1, size, "…of the first transcript alone, once (%s)" % road)
                self.assertEqual((w1[0] - w0[0], w1[1] - w0[1]), (1, size),
                                 "one detached read of the first transcript's size on /perf (%s): %s" % (road, row_key))
                self.assertEqual((d2, d3), (display, display), "answered from the set: the display road (%s)" % road)
                self.assertEqual((read2, w2), (0, w1), "the steps the set answers read nothing (%s)" % road)
                self.assertEqual(d4, {"whole": 0, "seeded": 0, "read": 1, "failed": 0},
                                 "a moved stat reads again, and counts again (%s)" % road)
                said = [ln for ln in err.getvalue().splitlines() if ln.startswith("comment cut for")]
                self.assertEqual(len(said), 1, "once per session and cause (%s): %s" % (road, err.getvalue()))
                self.assertIn("widen", said[0])

    def test_a_widening_that_pulls_no_byte_of_the_first_transcript_counts_no_read(self):
        # review round three: the widening counted a read, with the read line on stderr, whenever the session's uuid set did not
        # answer, though two such widenings pull no byte: a set served from a whole record cache entry standing for the first
        # transcript, which counts as the display road it came from, and a read that failed, which counts as failed and says
        # so on its own line, naming the error, and once per session under its own cause, apart from the read's
        first, leaf, _last, _t = cleared(self)
        for case in ("whole entry", "unreadable"):
            if case == "unreadable" and os.geteuid() == 0:
                self.skipTest("root reads any file: the unreadable transcript cannot be staged")
            with self.subTest(case=case):
                restart()
                km._parse(leaf, PARENT, self.now)             # the chat's whole entry
                km._cut_read_said.difference_update({(PARENT, "widen"), (PARENT, "unreadable")})
                if case == "whole entry":
                    em._read_jsonl_entry(first)               # a whole record cache entry standing for the first transcript
                zero_reads()
                r0, err, mode = self.roads(), io.StringIO(), os.stat(first).st_mode & 0o777
                if case == "unreadable":
                    os.chmod(first, 0)
                try:
                    if case == "unreadable":
                        with self.assertRaises(PermissionError, msg="premise: the first transcript cannot be opened"):
                            open(first, "rb").close()
                    with contextlib.redirect_stderr(err):
                        got = km._comment_cut_resolve(leaf, PARENT, "ka5")
                finally:
                    os.chmod(first, mode)                     # restored inside the subtest
                r1 = self.roads()
                counted = {k: r1.get(k, 0) - r0.get(k, 0) for k in ("whole", "seeded", "read", "failed")}
                lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
                if case == "whole entry":
                    self.assertEqual({"answer": got[2], "counted": counted, "bytes": read_of(first), "stderr": lines},
                                     {"answer": km.ANCHOR_REWOUND_ERR, "counted": {"whole": 1, "seeded": 0, "read": 0, "failed": 0},
                                      "bytes": 0, "stderr": []})
                else:
                    self.assertEqual({"answer": got[2], "counted": counted, "bytes": read_of(first), "lines": len(lines)},
                                     {"answer": km.ANCHOR_LAG_ERR, "counted": {"whole": 0, "seeded": 0, "read": 0, "failed": 1},
                                      "bytes": 0, "lines": 1}, "the failure: %s" % lines)
                    self.assertIn("PermissionError", lines[0], "the line names the error")
                    self.assertNotIn("read the transcript", lines[0], "and is not the read's line")
                    os.chmod(first, 0)                        # a second failed read in the session: nothing more said
                    try:
                        with contextlib.redirect_stderr(err):
                            again = km._comment_cut_resolve(leaf, PARENT, "ka5")
                    finally:
                        os.chmod(first, mode)
                    self.assertEqual(again[2], km.ANCHOR_LAG_ERR)
                    self.assertEqual(len([ln for ln in err.getvalue().splitlines() if ln.strip()]), 1,
                                     "once per session and cause: %s" % err.getvalue())

    def test_a_pre_clear_target_behind_a_post_clear_link_into_the_first_transcript_is_walked_over_both_files(self):
        # outside the transcript model, a CLI that links a post-clear record into the old file: the leaf's head has no parent,
        # so the leaf parses alone, but a record of its graph names a parent in the first transcript. Only then is the graph
        # over both files built, and the walk over it answers, for the linked record and the records behind it alike, on the
        # read road too (review round three: the one road that still builds that graph there). Its first-file records are
        # handed in, so no record cache entry for that file is left behind, whether the set's read or a read again gave them.
        # Since review round four, what each resolve reads of that file and how it counts: one read of the file's size at
        # each, the first for the set and the graph alike (its records handed in, never read twice), the second again, as
        # the set keeps only uuids; and the second, which pulled the file's bytes, counts as a read, not as its display road
        old, _last, t = turns(self.now - 100000, 20, 20000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        new, _l2, _t2 = turns(t + 100, 10, 20000, parent=None, tag="c")
        for r in new:
            if r["uuid"] == "cu5":
                r["parentUuid"] = "ka19"                      # the sixth question links back to the first transcript's last answer
        leaf = write(self.proj / (CLEARED + ".jsonl"), new)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "premise: a parentless head, so the leaf parses alone")
        rp = os.path.realpath(first)
        size = os.path.getsize(first)

        def held():
            with em._JSONL_CACHE_LOCK:
                return [k for k in em._JSONL_CACHE if os.path.realpath(str(k)) == rp]
        real = km._comment_cut_from_parse
        for road in ("whole", "seeded", "read"):
            with self.subTest(road=road):
                restart()
                if road == "seeded":
                    self.restored(leaf)
                else:
                    km._parse(leaf, PARENT, self.now)
                for anchor in ("ka19", "ka18"):               # the linked answer, and one behind it (the set answers it)
                    drop_reader(first)                        # the cold walk below reads it through the record cache
                    zero_reads()                              # so the bytes below are this resolve's alone
                    if road == "read":
                        km._comment_cut_from_parse = lambda *a, **k: None
                    r0 = self.roads()
                    try:
                        got = km._comment_cut_resolve(leaf, PARENT, anchor)
                    finally:
                        km._comment_cut_from_parse = real
                    r1 = self.roads()
                    pulled, counted = read_of(first), {k: r1[k] - r0[k] for k in r1}
                    if road in ("whole", "read"):             # before the cold walk below reads that file
                        self.assertEqual(pulled, size, "one read of the first transcript (%s: %s)" % (road, anchor))
                    if road in ("whole", "seeded") and anchor == "ka18":
                        self.assertEqual({"counted": counted, "bytes": pulled},
                                         {"counted": {"whole": 0, "seeded": 0, "read": 1, "failed": 0}, "bytes": size},
                                         "the read again counts as a read (%s)" % road)
                    self.assertEqual(held(), [], "no record cache entry for the first transcript (%s: %s)" % (road, anchor))
                    want = self.cold(leaf, anchor)
                    self.assertEqual((want[0], want[2]), ("", None), "premise: over both files, a tip fork (%s)" % anchor)
                    self.assertEqual(got, want, "%s: %s" % (road, anchor))

    def _linked_world(self, link, parent="ka7", compact_after=None):
        """A /clear world under PARENT whose leaf links one record outside itself, outside the transcript model: the leaf's
        record `link` (cn5, the token-count attachment of its sixth turn, or cu5, that turn's question) takes `parent` as its
        parent, by default the first transcript's last answer (ka7), while the leaf's head stays parentless, so the leaf
        parses alone. Eight short turns in each file, the leaf compacted after its turn `compact_after` when given (its
        boundary cb). Returns (first, leaf, every uuid of both files)."""
        old, _last, t = turns(self.now - 100000, 8, 1000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        new, _l2, _t2 = turns(t + 100, 8, 1000, parent=None, tag="c", compact_after=compact_after)
        for r in new:
            if r["uuid"] == link:
                r["parentUuid"] = parent
        leaf = write(self.proj / (CLEARED + ".jsonl"), new)
        return first, leaf, [r["uuid"] for r in old + new]

    def test_a_non_prose_record_linked_into_the_first_transcript_and_the_rows_under_it_answer_as_the_walk_over_both_files(self):
        # review round four: where the leaf's record that links into the first transcript is not prose (the token-count
        # attachment of a turn), the walk over the leaf's own graph from it, or from the tool call, tool result and hook row
        # under it, stops at the parent that graph lacks and refuses the comment as unable to anchor a thread, where the walk
        # over both files reaches the first transcript's last answer and answers from the tip. Every uuid of both files now
        # answers what the walk over both files answers, on the display road and on the read road; with the question as the
        # linking record (the prose twin) that held already. Review round five: on a restored entry too (the seeded road, as
        # after a kernel restart, where the linking record, when it is before the document's cut, is the root of the pre-cut
        # spine and reads as parentless), and with a compaction after the link, where risk 1's one difference is left: a passage from before the compaction whose
        # other side last spoke before it in the first transcript (with the attachment linked, the sixth answer, whose
        # question is off the leaf's chain; with the question linked, that question) opens without the excerpt of what that
        # side said, which the walk over both files quotes, since the excerpt is looked up in the leaf's own graph. That one
        # record differs, in its prior context alone, on every road
        real = km._comment_cut_from_parse
        odd_of = {("cn5", None): None, ("cu5", None): None, ("cn5", 6): "ca5", ("cu5", 6): "cu5"}
        for (link, compact_after), odd in odd_of.items():
            with self.subTest(link=link, compact_after=compact_after):
                restart()
                first, leaf, uuids = self._linked_world(link, compact_after=compact_after)
                self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "premise: the leaf parses alone (%s)" % link)
                wide = em.TranscriptGraph(km._anchor_adapter(leaf, PARENT))      # the walk over both files, the reference
                want = {u: km._comment_cut_walk(wide, leaf, u) for u in uuids}
                own = em.TranscriptGraph(em.FileAdapter([leaf], leaf))         # the leaf's own graph
                refused = sorted(u for u in uuids
                                 if own.has(u) and "can't anchor" in (km._comment_cut_walk(own, leaf, u)[2] or ""))
                behind = ["ch5", "cn5", "cr5", "ct5"] if link == "cn5" else []
                self.assertEqual(refused, (["cb"] if compact_after else []) + behind,
                                 "premise: what the leaf's own graph refuses as unable to anchor a thread (%s)" % link)
                self.assertEqual([(want[u][0], want[u][2]) for u in behind], [("", None)] * len(behind),
                                 "premise: over both files, tip forks (%s)" % link)
                answers = {}
                for road in ("whole", "seeded", "read"):
                    restart()
                    # each world rewrites the leaf in place, before any document's cut: no earlier world's document may restore it
                    shutil.rmtree(jd.STATE / "checkpoints", ignore_errors=True)
                    if road == "seeded":
                        self.restored(leaf)                   # the chat's parse, restored from its document at a boot
                    else:
                        km._parse(leaf, PARENT, self.now)     # the chat's whole entry (the read road below ignores it)
                    if road == "read":
                        km._comment_cut_from_parse = lambda *a, **k: None
                    try:
                        answers[road] = {u: km._comment_cut_resolve(leaf, PARENT, u) for u in uuids}
                    finally:
                        km._comment_cut_from_parse = real
                differ = [(road, u, got[u][2]) for road, got in answers.items() for u in sorted(uuids) if got[u] != want[u]]
                self.assertEqual(differ, [(road, odd, None) for road in answers] if odd else [],
                                 "every uuid of both files, %d of them (%s)" % (len(uuids), link))
                if odd:
                    self.assertIn("step 7", want[odd][3], "premise: over both files, the prior quotes the first transcript")
                    for road, got in answers.items():
                        self.assertEqual(got[odd][:3], want[odd][:3], "only the prior differs (%s: %s)" % (road, odd))
                        self.assertNotIn("step 7", got[odd][3], "the prior lacks that excerpt (%s: %s)" % (road, odd))

    def test_the_no_prose_widening_reads_the_first_transcript_only_where_the_walk_stopped_at_a_parent_the_leaf_lacks(self):
        # review round four's widening (_cut_widen_linked): in the linked world its first answer reads the first transcript
        # once, the set's read with its records handed to the graph over both files, and counts a read; the next, which the
        # set answers, reads it once again and counts a read; and in a cleared leaf whose graph names no parent it lacks, a
        # row it cannot anchor keeps the refusal, reads none of that file and counts as the display road. Review round five:
        # where the parent the walk stopped at was written in neither file, the first refusal reads the first transcript
        # once, for its uuid set, and counts a read, and the next, which the set answers, reads none of it and counts as the
        # display road (the set's check on that parent); and a comment on a compaction boundary whose logical parent was
        # never written, a refusal no other file can change, reads none of it at either attempt, on either road
        none = {"whole": 0, "seeded": 0, "read": 0, "failed": 0}

        def resolve(anchor, read_road=False):
            drop_reader(first)
            zero_reads()
            if read_road:
                km._comment_cut_from_parse = lambda *a, **k: None
            r0 = self.roads()
            try:
                got = km._comment_cut_resolve(leaf, PARENT, anchor)
            finally:
                km._comment_cut_from_parse = real
            r1 = self.roads()
            return {"answer": (got[0], got[2]), "bytes": read_of(first), "counted": {k: r1[k] - r0[k] for k in r1}}
        real = km._comment_cut_from_parse
        first, leaf, _uuids = self._linked_world("cn5")
        size = os.path.getsize(first)
        km._parse(leaf, PARENT, self.now)
        for anchor in ("ct5", "cr5"):
            self.assertEqual(resolve(anchor), {"answer": ("", None), "bytes": size, "counted": dict(none, read=1)}, anchor)
        restart()
        first, leaf, _uuids = self._linked_world("cn5", parent="kz9")    # a parent written in neither file
        size = os.path.getsize(first)
        km._parse(leaf, PARENT, self.now)
        refusal = (None, km.ANCHOR_NO_PROSE_ERR)
        self.assertEqual([resolve("ct5"), resolve("cr5")],
                         [{"answer": refusal, "bytes": size, "counted": dict(none, read=1)},
                          {"answer": refusal, "bytes": 0, "counted": dict(none, whole=1)}],
                         "the first refusal reads the set, the next is answered from it")
        restart()
        old, _last, t = turns(self.now - 100000, 8, 1000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        new, _l, _t = turns(t + 100, 8, 1000, parent=None, tag="c", compact_after=5)
        for r in new:
            if r["uuid"] == "cb":
                r["logicalParentUuid"] = "kz9"                # a compaction boundary whose logical parent was never written
        leaf = write(self.proj / (CLEARED + ".jsonl"), new)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "premise: the leaf parses alone")
        self.assertEqual(self.cold(leaf, "cb")[2], km.ANCHOR_NO_PROSE_ERR, "premise: over both files, the same refusal")
        km._parse(leaf, PARENT, self.now)
        self.assertEqual([resolve("cb"), resolve("cb"), resolve("cb", read_road=True), resolve("cb", read_road=True)],
                         [{"answer": refusal, "bytes": 0, "counted": dict(none, whole=1)}] * 2
                         + [{"answer": refusal, "bytes": 0, "counted": none}] * 2,
                         "a compaction boundary's refusal reads none of the first transcript, at any attempt, on either road")
        restart()
        old, _last, t = turns(self.now - 100000, 8, 1000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        head = attachment(t + 100, "czh", None, type="total_tokens_reminder")    # a parentless head that is not prose
        new, _l, _t = turns(t + 101, 8, 1000, parent="czh", tag="c", compact_after=5)
        leaf = write(self.proj / (CLEARED + ".jsonl"), [head] + new)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "premise: the leaf parses alone")
        km._parse(leaf, PARENT, self.now)
        drop_reader(first)
        zero_reads()
        r0 = self.roads()
        got = km._comment_cut_resolve(leaf, PARENT, "czh")
        r1 = self.roads()
        self.assertEqual({"answer": got[2], "bytes": read_of(first), "counted": {k: r1[k] - r0[k] for k in r1}},
                         {"answer": km.ANCHOR_NO_PROSE_ERR, "bytes": 0,
                          "counted": {"whole": 1, "seeded": 0, "read": 0, "failed": 0}})

    def _off_chain_head_world(self):
        """A /clear world under PARENT whose leaf opens at a parentless head that is not prose (czh, a token-count
        attachment), holds an off-chain row parented into the first transcript's last answer (cx1, at ka7) and compacts
        after its sixth turn (boundary cb). Returns (first, leaf)."""
        old, _last, t = turns(self.now - 100000, 8, 1000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        head = attachment(t + 100, "czh", None, type="total_tokens_reminder")    # a parentless head that is not prose
        off = attachment(t + 100, "cx1", "ka7", type="total_tokens_reminder")    # an off-chain row parented into the first file
        new, _l, _t = turns(t + 101, 8, 1000, parent="czh", tag="c", compact_after=5)
        leaf = write(self.proj / (CLEARED + ".jsonl"), [head, off] + new)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf], "premise: the leaf parses alone")
        return first, leaf

    def test_a_refusal_no_other_file_can_change_reads_none_of_the_first_transcript_at_any_attempt(self):
        # review round five: the no-prose widening looked into the first transcript whenever the leaf's graph named any
        # parent it lacks, so in a cleared leaf with an off-chain row parented into that file, a comment on the leaf's
        # parentless head (not prose) or on its compaction boundary, refusals the walk over both files gives too, read that
        # file whole at every attempt. It now looks only where the walk stopped at a parent the leaf's graph lacks: both
        # refusals read none of it, at each of two attempts, on the display road and on the read road. Review round six: on
        # the restored road too, where the head is the root of the pre-cut spine, which that graph gives no parent, and
        # its document records that the head's own record names none either
        first, leaf = self._off_chain_head_world()
        for anchor in ("czh", "cb"):
            self.assertEqual(self.cold(leaf, anchor)[2], km.ANCHOR_NO_PROSE_ERR, "premise: over both files too (%s)" % anchor)
        real, seen, want = km._comment_cut_from_parse, {}, {}
        none = {"whole": 0, "seeded": 0, "read": 0, "failed": 0}
        for road in ("whole", "seeded", "read"):
            restart()
            if road == "seeded":
                self.restored(leaf)                           # the chat's parse, restored from its document at a boot
            else:
                km._parse(leaf, PARENT, self.now)             # the chat's whole entry (the read road below ignores it)
            for anchor in ("czh", "cb"):
                for attempt in (1, 2):
                    drop_reader(first)
                    zero_reads()
                    if road == "read":
                        km._comment_cut_from_parse = lambda *a, **k: None
                    r0 = self.roads()
                    try:
                        got = km._comment_cut_resolve(leaf, PARENT, anchor)
                    finally:
                        km._comment_cut_from_parse = real
                    r1 = self.roads()
                    seen[(road, anchor, attempt)] = (got[2], read_of(first), {k: r1[k] - r0[k] for k in r1})
                    counted = dict(none, **{road: 1}) if road != "read" else none
                    want[(road, anchor, attempt)] = (km.ANCHOR_NO_PROSE_ERR, 0, counted)
        self.assertEqual(seen, want)

    def test_an_unreadable_first_transcript_leaves_a_row_behind_a_link_refused_at_once_on_both_roads_and_says_why(self):
        # review round five: with the first transcript at mode 0 (restored in the finally) the no-prose widening's look at it
        # fails, and a comment on a row the leaf's own graph cannot anchor, behind a non-prose record linking into that file,
        # keeps that refusal at once, on the display road and on the read road, never the lag answer, which would park it
        # and drop it after _PARK_MAX_TRIES pusher cycles; one stderr line names the error and both effects
        # (_cut_first_unreadable), and the display road counts the look as failed
        if os.geteuid() == 0:
            self.skipTest("root reads any file: the unreadable transcript cannot be staged")
        first, leaf, _uuids = self._linked_world("cn5")
        real, mode = km._comment_cut_from_parse, os.stat(first).st_mode & 0o777
        for road in ("display", "read"):
            with self.subTest(road=road):
                restart()                                     # no uuid set and no whole entry to serve one
                km._parse(leaf, PARENT, self.now)             # the chat's whole entry
                km._cut_read_said.discard((PARENT, "unreadable"))
                err, r0 = io.StringIO(), self.roads()
                if road == "read":
                    km._comment_cut_from_parse = lambda *a, **k: None
                os.chmod(first, 0)
                try:
                    with self.assertRaises(PermissionError, msg="premise: the first transcript cannot be opened"):
                        open(first, "rb").close()
                    with contextlib.redirect_stderr(err):
                        got = km._comment_cut_resolve(leaf, PARENT, "ct5")
                finally:
                    os.chmod(first, mode)
                    km._comment_cut_from_parse = real
                r1 = self.roads()
                lines = [ln for ln in err.getvalue().splitlines() if ln.strip()]
                self.assertEqual({"answer": got[2], "stderr lines": len(lines), "failed": r1["failed"] - r0["failed"]},
                                 {"answer": km.ANCHOR_NO_PROSE_ERR, "stderr lines": 1, "failed": int(road == "display")},
                                 "stderr: %s" % lines)
                self.assertIn("PermissionError", lines[0], "the line names the error")
                self.assertIn("cannot anchor a thread", lines[0], "and the refusal it leaves (review round five)")

    def _two_creates_after_a_budget_eviction(self, path, files, anchors):
        """The chat's parse of the session whose leaf is `path` (its transcripts `files`) installs its entry; another file's
        read under a budget that holds only that file evicts the transcripts and releases the entry; the display's next
        build is a parse-store hit that rebuilds nothing; then two creates, on anchors[0] and anchors[1]. The first rebuilds
        the entry, one event model parse and one adapter and no read of the transcript; the second walks it: no parse, no
        adapter, no read."""
        km._parse(path, PARENT, self.now)
        key = (os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))
        self.assertIn(key, em._ASM_CACHE, "the chat's parse installed its entry")
        other = write(self.proj / "notes-api-other.jsonl", [uline(self.now - 10, "another session's prompt", "o1")])
        saved = em._JSONL_CACHE_BUDGET_BYTES
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
        try:
            em._read_jsonl_incremental(other)             # the cache's own loop evicts the leaf, and its release drops the entry
        finally:
            em._JSONL_CACHE_BUDGET_BYTES = saved
        self.assertNotIn(key, em._ASM_CACHE, "the budget released the chat's entry")
        km._parse(path, PARENT, self.now)
        self.assertNotIn(key, em._ASM_CACHE, "the display's next build is a parse-store hit: nothing rebuilt")
        got1, built1, parsed1, read1 = self.counted_create(path, anchors[0])
        zero_reads()
        got2, built2, parsed2, read2 = self.counted_create(path, anchors[1])
        self.assertEqual((built2, parsed2, read2), (0, 0, 0), "the second create walks the rebuilt entry: no adapter built, "
                                                              "no parse, no read of the transcript")
        self.assertLess(sum(read_of(f) for f in files), 4096, "…and no bytes of it")
        self.assertEqual((built1, parsed1, read1), (1, 1, 0), "the first create rebuilt the entry, one parse and one adapter, "
                                                              "and read nothing else")
        self.assertIn(key, em._ASM_CACHE)
        self.assertEqual((got1, got2), (self.cold(path, anchors[0]), self.cold(path, anchors[1])))
        self.assertEqual(self.roads()["read"], 0)

    def test_a_session_whose_stored_parse_fell_back_is_not_parsed_a_second_time_at_each_create(self):
        # when the assembly raises it serves a plain parse that installs nothing (em._assemble's "fallback"), so no entry
        # ever stands, and the "none" retry parsed again at every create, fell back again and read the transcript anyway
        # (review round three: 0.22 to 0.29 s a create on a 12.2 MB leaf, the fallback induced). The retry brings the
        # display's parse current first, here a parse-store hit whose road reads "fallback", and then parses no second time:
        # the create reads the transcript, one adapter. A session whose parse installs is still rebuilt by one parse at its
        # first create (the budget-eviction tests above). The kernel's road for the leaf is cleared before the creates, as
        # after a restart before the chat's first build, so the retry's own _parse is what finds the stored road (round
        # four). A session whose rebuild itself falls back after a release parses again at each create until its files
        # change, as the next test pins (an induced state, disclosed in the docstring of _comment_cut_from_parse)
        path, recs = self._session(n=20, size=20000)
        key = (os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))
        real = em._asm_restore

        def restore_raises(*a, **k):                      # checkpoints are on here: a leaf with no entry tries its document first
            raise RuntimeError("a synthetic restore failure")
        em._asm_restore = restore_raises
        try:
            km._parse(path, PARENT, self.now)
            self.assertEqual(km._parse_mode.get(path), "fallback", "the display's parse fell back")
            self.assertNotIn(key, em._ASM_CACHE, "…and installed no entry")
            km._parse_mode.pop(path, None)                # no current road: the retry's own _parse brings it back
            for anchor in ("kr10", "ka12"):
                got, built, parsed, reads = self.counted_create(path, anchor)
                self.assertEqual((parsed, reads, built), (0, 1, 1), "no event model parse: the create reads the transcript, "
                                                                     "its one adapter (%s)" % anchor)
                self.assertNotIn(key, em._ASM_CACHE, anchor)
                self.assertEqual(got, self.cold(path, anchor), anchor)
        finally:
            em._asm_restore = real
        self.assertEqual(self.roads()["read"], 2)

    def test_a_rebuild_that_falls_back_after_a_release_parses_again_at_each_create_until_the_file_changes(self):
        # the state the texts disclose (review round five): an entry a parse that succeeded installed, released by the
        # budget, and a rebuild whose restore or whole parse raises. The stored road stays the install's ("full"), so each
        # create runs one event-model parse that falls back and installs nothing, then reads the transcript; after an
        # append the parse that brings the leaf current falls back itself, its road reads "fallback", and the creates
        # after it parse no more. Three legs (review round six): checkpoints on with the restore raising (the whole parse
        # is never reached); checkpoints off with the whole parse raising; and checkpoints on with no document, so the
        # real restore answers None and the whole parse raises, the usual road for a session with no document.
        for leg, ckpt, restore_raises in (("the restore raises", True, True), ("checkpoints off", False, False),
                                          ("no document, the whole parse raises", True, False)):
            with self.subTest(leg=leg):
                restart()
                if not ckpt:
                    em.set_checkpoint_dir(None)
                path, recs = self._session(n=20, size=20000)
                km._parse_mode.pop(path, None)
                key = (os.path.realpath(path), PARENT, bool(km._display_sdk_human(PARENT)))
                km._parse(path, PARENT, self.now)
                self.assertIn(key, em._ASM_CACHE, "the chat's parse installed its entry")
                other = write(self.proj / "notes-api-other.jsonl",
                              [uline(self.now - 10, "another session's prompt", "o1")])
                saved = em._JSONL_CACHE_BUDGET_BYTES
                em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
                try:
                    em._read_jsonl_incremental(other)     # the budget evicts the leaf and releases the entry
                finally:
                    em._JSONL_CACHE_BUDGET_BYTES = saved
                self.assertNotIn(key, em._ASM_CACHE, "the budget released the chat's entry")
                real = (em._asm_restore, em._asm_full)

                def raises(*a, **k):
                    raise RuntimeError("a synthetic rebuild failure")
                em._asm_full = raises
                if restore_raises:
                    em._asm_restore = raises
                try:
                    for anchor in ("ka5", "kr10", "ka12"):
                        got, built, parsed, reads = self.counted_create(path, anchor)
                        self.assertEqual((parsed, reads, built), (1, 1, 2), "a parse that falls back, then the transcript "
                                                                             "read, at every create (%s)" % anchor)
                        self.assertEqual(km._parse_mode.get(path), "full",
                                         "the stored road is still the install's (%s)" % anchor)
                        self.assertNotIn(key, em._ASM_CACHE, anchor)
                        self.assertEqual(got, self.cold(path, anchor), anchor)
                    more, _, _ = turns(self.now - 500, 1, 2000, parent=recs[-1]["uuid"], tag="m")
                    write(path, recs + more)              # the file changes
                    self.counted_create(path, "ka12")
                    self.assertEqual(km._parse_mode.get(path), "fallback",
                                     "the parse that brought the leaf current fell back")
                    got, built, parsed, reads = self.counted_create(path, "kr10")
                    self.assertEqual((parsed, reads), (0, 1), "after the change no create parses: the transcript is read")
                    self.assertEqual(got, self.cold(path, "kr10"))
                finally:
                    em._asm_restore, em._asm_full = real
                    if not ckpt:
                        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

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


class CreateDoor(Base):
    """The create door's harness: a fake backend that forks, the parent registered as "web", and a page client that records
    what it is sent."""

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
        with km._create_lock:
            km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
        super().tearDown()

    def _create(self, anchor, cid="g-1"):
        return {"type": "commentCreate", "id": PARENT, "uuid": anchor, "exact": "the notes route pages by cursor",
                "text": "Why a cursor and not an offset?", "name": "", "createId": cid}

    def _wait_for_ack(self):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if any(f.get("type") == "commentCreated" for f in self.sent):
                return True
            time.sleep(0.01)
        return False


class CreateAnswersFast(CreateDoor):
    """The create door on a huge parent: off the socket thread, answered the moment the row exists, with the thread's row."""

    def _huge_parent(self, mb=100):
        n = 400
        recs, last, t = turns(self.now - 200000, n, mb * 1024 * 1024 // n)
        path = self.parent(recs)
        km._sessions = lambda now, window=None, forks=True: [{"sid": PARENT, "name": "web", "path": path, "mtime": self.now}]
        return path

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


class CreatesAfterAClear(CreateDoor):
    """The create door after a /clear (review round one, 2026-10-03): the display parse reads the leaf alone, so a comment on
    a message from before the clear found no record there and its create was parked as lag, with nothing said, until the
    pusher dropped it. It now gets the refusal it got when the first transcript was a candidate, and parks nothing; a
    post-clear reply whose record is not flushed yet stays a transient lag, and its retries read the first transcript at
    most once, on either road, then create the thread once the record lands. Since review round two the widening reads the
    first transcript alone, into a list nobody keeps, never the leaf, and leaves the record cache's entry for it as it was;
    the session's uuid set of that file answers later targets while its stat stands, and is kept only after a read that
    succeeded."""

    def _cleared(self):
        first, leaf, last, t = cleared(self)
        km._sessions = lambda now, window=None, forks=True: [{"sid": PARENT, "name": "web", "path": leaf, "mtime": self.now}]
        km._parse(leaf, PARENT, self.now)             # the chat holds the session open
        return first, leaf, last, t

    def _job(self, anchor, cid):
        msg = self._create(anchor, cid)
        key = km._create_key(PARENT, msg["uuid"], msg["exact"], msg["text"], cid)
        km._comment_create_job(PARENT, msg, key, self.client)
        return [f for f in self.sent if f.get("type") == "commentCreateFailed" and f.get("createId") == cid]

    def test_a_comment_on_a_pre_clear_message_is_refused_and_nothing_is_parked(self):
        first, leaf, _last, _t = self._cleared()
        failed = self._job("ka5", "g-pre")
        self.assertEqual([f.get("transient") for f in failed], [False], self.sent)
        self.assertIn("rewound away", failed[0].get("text") or "")
        self.assertIn("warn", [f.get("type") for f in self.sent], "the refusal is said")
        self.assertEqual(km._parked_creates, [], "nothing parked for the pusher to retry")
        self.assertEqual(self.be.calls, [], "no fork")

    def test_a_lagging_post_clear_reply_stays_transient_and_its_retries_read_the_first_transcript_at_most_once(self):
        for road in ("display", "read"):
            with self.subTest(road=road):
                restart()
                with km._create_lock:
                    km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
                self.sent.clear(); self.be.calls.clear()
                first, leaf, last, t = self._cleared()
                over_first, real_init = [], em.FileAdapter.__init__

                def counting(self_, files, *a, **k):
                    if any(os.path.realpath(str(f)) == os.path.realpath(first) for f in files):
                        over_first.append(1)
                    return real_init(self_, files, *a, **k)
                real_parse = km._comment_cut_from_parse
                em.FileAdapter.__init__ = counting
                if road == "read":
                    km._comment_cut_from_parse = lambda *a, **k: None
                try:
                    zero_reads()
                    failed = self._job("cz1", "g-lag-" + road)
                    self.assertEqual([f.get("transient") for f in failed], [True], self.sent)
                    self.assertEqual(len(km._parked_creates), 1, "parked for the pusher")
                    for n in range(3):
                        km._retry_parked_creates()
                        self.assertEqual([pk["tries"] for pk in km._parked_creates], [n + 1], "still lagging, still parked")
                    self.assertEqual(len(over_first), 0, "no graph over the first transcript for a target it lacks (%d)"
                                     % len(over_first))
                    self.assertLessEqual(read_of(first), os.path.getsize(first), "…which is read alone, once (%d bytes of %d)"
                                         % (read_of(first), os.path.getsize(first)))
                    with open(leaf, "a") as fh:            # the reply's record lands
                        fh.write(json.dumps(aline(t, "a reply about the tags route, flushed late", "cz1", last)) + "\n")
                    km._retry_parked_creates()
                    self.assertEqual(km._parked_creates, [], "created once the record landed")
                    self.assertEqual([c[2] for c in self.be.calls if c[0] == "fork"], ["cz1"], "forked at the reply")
                    self.assertEqual(len(over_first), 0)
                finally:
                    em.FileAdapter.__init__ = real_init
                    km._comment_cut_from_parse = real_parse

    def test_in_the_steady_state_the_widening_reads_the_first_transcript_alone_and_leaves_nothing_resident(self):
        # review round two: after a /clear the judges' per-file scan walks the first transcript once, writes its memo into
        # the file's fold document and drops its records (scan one); the next scan restores a tail of it from that document
        # (scan two). A gesture then, at the previous head, built the graph over both files: it read the leaf's restored tail
        # whole and the first transcript whole and upgraded both entries in place, at the same generation, so the next scan
        # served its memo from them and nothing let them go. Now the first transcript is read alone, once, into a list nobody
        # keeps: its tail stays a tail, the leaf is not read, and a second lagging target reads no byte of it
        saved_owner = jd._SDK_OWNER_FN
        jd._SDK_OWNER_FN = None                           # no backend answers for the parent: the registry road (none here)
        self.addCleanup(setattr, jd, "_SDK_OWNER_FN", saved_owner)
        em.checkpoint_cycle_begin(em._ckpt_cycle_default_cap())   # a pusher cycle begins: the drop's write budget is whole
        first, leaf, last, t = cleared(self)
        old = time.time() - 600
        os.utime(first, (old, old))                       # the pre-clear file, unchanged since the clear: past the quiet window
        km._sessions = lambda now, window=None, forks=True: [{"sid": PARENT, "name": "web", "path": leaf, "mtime": self.now}]
        self.restored(leaf)                               # the chat's parse, restored from its document at a boot
        size, lsize = os.path.getsize(first), os.path.getsize(leaf)

        def scan():
            _out, fails = jd._per_file_rewound(PARENT, jd._judge_candidates(PARENT, [leaf]))
            self.assertEqual(fails, 0)

        def entry(p):
            with em._JSONL_CACHE_LOCK:
                return em._JSONL_CACHE.get(str(p))

        def tail(p):
            e = entry(p)
            return e is not None and e[5] > 0
        zero_reads()
        scan()                                            # the first pass after the clear
        self.assertGreaterEqual(read_of(first), size, "premise: scan one walked the first transcript, a whole read")
        self.assertIsNone(entry(first), "premise: and dropped its records once its memo was written")
        scan()                                            # the next pass
        self.assertTrue(tail(first), "premise: scan two restored a tail of the first transcript from its document")
        self.assertTrue(tail(leaf), "premise: the leaf's entry is the tail its restore left")
        f0, l0 = read_of(first), read_of(leaf)
        refused = self._job("ka5", "g-pre")               # a message from before the clear: refused, nothing parked
        f1 = read_of(first)
        lagging = self._job("cz1", "g-lag")               # a post-clear reply not flushed yet: parked as lag
        km._retry_parked_creates()
        lagging += self._job("cz2", "g-lag-2")            # a second distinct lagging target
        f2 = read_of(first)
        scan()                                            # the pass after the gestures
        ent, leaf_read = entry(first), read_of(leaf) - l0
        got = {"refused, not transient": [(f.get("transient"), "rewound away" in (f.get("text") or "")) for f in refused],
               "lagging, parked": ([f.get("transient") for f in lagging], [pk["createId"] for pk in km._parked_creates]),
               "the first transcript's entry whole": ent is not None and ent[5] == 0,
               "the leaf's entry a tail": tail(leaf),
               "the leaf read within the guard bytes": leaf_read <= 4 * em._JSONL_TAIL_GUARD,
               "the refusal read the first transcript at most once": f1 - f0 <= size,
               "bytes of it the lagging targets read": f2 - f1,
               "at most two whole reads of it in all (scan one's walk, the widening's)":
                   read_of(first) <= 2 * size + 4 * em._JSONL_TAIL_GUARD,
               "forks": self.be.calls}
        self.assertEqual(got, {"refused, not transient": [(False, True)],
                               "lagging, parked": ([True, True], ["g-lag", "g-lag-2"]),
                               "the first transcript's entry whole": False,
                               "the leaf's entry a tail": True,
                               "the leaf read within the guard bytes": True,
                               "the refusal read the first transcript at most once": True,
                               "bytes of it the lagging targets read": 0,
                               "at most two whole reads of it in all (scan one's walk, the widening's)": True,
                               "forks": []},
                         "the leaf: %d of %d bytes read; the first transcript: %d bytes by the refusal, %d in all, of %d"
                         % (leaf_read, lsize, f1 - f0, read_of(first), size))

    def test_a_first_transcript_that_could_not_be_read_records_nothing_and_the_next_create_is_refused(self):
        # review round two: a miss was remembered after a failed read, so a comment on a pre-clear message made while the
        # first transcript could not be opened kept the lag answer, parked, once the file read again with its stat unchanged
        # (a chmod moves neither its mtime nor its size), until a restart. Nothing is remembered from a read that failed
        if os.geteuid() == 0:
            self.skipTest("root reads any file: the unreadable transcript cannot be staged")
        first, leaf, _last, _t = self._cleared()
        st0 = os.stat(first)
        os.chmod(first, 0)
        try:
            with self.assertRaises(PermissionError, msg="premise: the first transcript cannot be opened"):
                open(first, "rb").close()
            failed = self._job("ka5", "g-pre-1")
        finally:
            os.chmod(first, 0o644)
        st1 = os.stat(first)
        self.assertEqual((st1.st_ino, st1.st_mtime_ns, st1.st_size), (st0.st_ino, st0.st_mtime_ns, st0.st_size),
                         "premise: the stat the set keys on is unchanged")
        self.assertEqual([f.get("transient") for f in failed], [True], "the first create could not tell: lag, parked")
        remembered = PARENT in getattr(km, "_CUT_FIRST_UUIDS", {})
        failed = self._job("ka5", "g-pre-2")
        self.assertEqual([f.get("transient") for f in failed], [False], self.sent)
        self.assertIn("rewound away", failed[0].get("text") or "")
        self.assertEqual([pk["createId"] for pk in km._parked_creates], ["g-pre-1"], "the second create parked nothing")
        km._retry_parked_creates()
        self.assertEqual(km._parked_creates, [], "the first create, retried, is refused too and leaves the park")
        self.assertEqual(self.be.calls, [], "no fork")
        self.assertFalse(remembered, "nothing was remembered from the failed read")
        self.assertIn(PARENT, getattr(km, "_CUT_FIRST_UUIDS", {}), "the set is kept from the read that succeeded")

    def test_a_parked_create_whose_record_lands_in_the_first_transcript_leaves_the_park_at_the_next_retry(self):
        # the set is keyed on the first transcript's inode, mtime and size, never on its presence: a record that lands there
        # after the set was read (a CLI writing into the old file, outside the transcript model) moves the stat, and the next
        # retry reads the file again and finds it, a record no record of the leaf links to: refused, the park left. Since review
        # round three also with a whole record cache entry standing for that file when the set is built: the entry serves the
        # set only at the file's current stat, so after the append the retry reads the file, the new record in it
        for held in ("none", "whole entry"):
            with self.subTest(held=held):
                restart()
                with km._create_lock:
                    km._recent_creates.clear(); km._inflight_creates.clear(); km._parked_creates.clear()
                self.sent.clear(); self.be.calls.clear()
                first, leaf, _last, _t = self._cleared()
                if held == "whole entry":
                    em._read_jsonl_entry(first)
                    with em._JSONL_CACHE_LOCK:
                        self.assertEqual(em._JSONL_CACHE[str(first)][5], 0, "premise: a whole entry stands for the file")
                failed = self._job("cz9", "g-late")
                self.assertEqual([f.get("transient") for f in failed], [True], self.sent)
                self.assertEqual(len(km._parked_creates), 1, "parked for the pusher")
                with open(first, "a") as fh:
                    fh.write(json.dumps(aline(self.now - 50000, "a reply written into the first transcript late", "cz9", "ka19"))
                             + "\n")
                km._retry_parked_creates()
                self.assertEqual(km._parked_creates, [], "the next retry found the record and left the park (%s)" % held)
                self.assertEqual(self.be.calls, [], "refused, so no fork")


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
