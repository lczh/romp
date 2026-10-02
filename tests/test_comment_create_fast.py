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
  pre-compaction passage and a restart seam still cut exactly as the whole read cuts them; after the record cache's
  budget released the entry (2026-10-02), one create rebuilds it and the next builds no adapter, on a restart seam and
  a /clear continuation too, and on a session whose stored parse fell back no create parses a second time, even with
  no current road for the leaf (one whose rebuild itself falls back after a release parses again at each create until
  its files change: an induced state, disclosed and pinned);
- a comments frame reads each thread's own records past its cut, never the copied history;
- the create runs off the socket thread and answers the moment the thread row exists, carrying the thread's frame row;
  a frame built before that row was saved is never sent after the answer.

SYNTHETIC fixtures only: invented notes-api text, placeholder uuids. The state root is the test's own (session hosts off)."""
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


def turns(t0, n, size, parent=None, tag="k", compact_after=None):
    """`n` notes-api turns, each a question, a tool call, its (padded) result and a prose answer; a compaction after turn
    `compact_after` when given. Returns (records, last uuid, next free time)."""
    out, t = [], t0
    for k in range(n):
        u, tu, tr, a = "%su%d" % (tag, k), "%st%d" % (tag, k), "%sr%d" % (tag, k), "%sa%d" % (tag, k)
        out += [uline(t, "step %d: how should the notes route page? %s" % (k, pad(200, k)), u, parent),
                tool_use(t + 1, tu, u),
                tool_result(t + 2, tr, tu, tu, size),
                aline(t + 3, "step %d done: %s" % (k, pad(400, k + 1)), a, tr)]
        parent, t = a, t + 10
        if compact_after is not None and k == compact_after:
            out += [boundary(t, "%sb" % tag, parent), summary(t + 1, "%ss" % tag, "%sb" % tag)]
            parent, t = "%ss" % tag, t + 10
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

    def restored(self, path, sid=PARENT):
        """The chat's parse, a document written from it, and a restart: the parse that follows restores the entry from the
        document (the road a large session takes at every boot), so the cut walk meets a SEEDED entry."""
        tree = km._parse(path, sid, self.now)
        self.assertTrue(em.asm_checkpoint_write(path, sid, km._display_sdk_human(sid), tree=tree),
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
            zero_reads()
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
            zero_reads()
            got, rebuilt = self.walks_reading_the_transcript(km._comment_cut_resolve, path, PARENT, anchor)
            self.assertEqual(rebuilt, 0, anchor)
            self.assertLess(read_of(path), 64 * 1024, "a few records read back by offset, not the %d bytes before the cut (%s)"
                            % (size, anchor))
            self.assertEqual(got, self.cold(path, anchor), anchor)
        self.assertEqual(self.roads()["seeded"], 4)
        self.assertEqual(self.roads()["read"], 0)

    def test_a_pre_compaction_passage_still_opens_a_tip_fork_with_what_surrounded_it(self):
        path, recs = self._session(compact_after=40)
        for road in ("whole", "seeded"):
            if road == "seeded":
                self.restored(path)
            else:
                km._parse(path, PARENT, self.now)
            for anchor in ("ka20", "ku21", "kr33"):       # an answer, a question, a tool result: all before the boundary
                zero_reads()
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
            zero_reads()
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

    def test_after_a_budget_eviction_a_clear_continuation_is_rebuilt_over_both_transcripts(self):
        # after a /clear the leaf is a new transcript beside the session's first one, which the display parses with it (the
        # anchor candidate); an entry the retry built over the leaf alone never matches the walk's candidates
        old, _last, t = turns(self.now - 100000, 20, 20000)
        first = write(self.proj / (PARENT + ".jsonl"), old)
        new, _l2, _t2 = turns(t + 100, 10, 20000, parent=None, tag="c")
        leaf = write(self.proj / (CLEARED + ".jsonl"), new)
        self.assertEqual(jd._judge_candidates(PARENT, [leaf]), [leaf, first], "two files: the leaf and the first transcript")
        self._two_creates_after_a_budget_eviction(leaf, [leaf, first], ("cr5", "ca7"))

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
        # budget, and a rebuild whose restore and whole parse raise. The stored road stays the install's ("full"), so each
        # create runs one event-model parse that falls back and installs nothing, then reads the transcript; after an
        # append the parse that brings the leaf current falls back itself, its road reads "fallback", and the creates
        # after it parse no more. With checkpoints on and off, which reach the whole parse by different roads.
        for ckpt in (True, False):
            with self.subTest(checkpoints=ckpt):
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
                em._asm_restore = em._asm_full = raises
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
