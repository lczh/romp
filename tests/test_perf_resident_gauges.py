#!/usr/bin/env python3
"""The /perf snapshot in real memory units (the kernel memory diagnosis, 2026-10-01, its fix 1).

The kernel's memory grew for days, and the snapshot could not say where. Its largest gauge, recordCache.bytes,
counts the transcripts' bytes ON DISK, while the kernel's resident size grew with about 2.85 times those bytes: the
decoded records weigh about 2.85 times the file. Other holders reported a count and no size,
and a leaf the record cache dropped while an assembly entry still held the same records fell out of the count while the
memory stayed. Pinned here, all through the kernel's own heap block:

- recordCacheResident: every record-cache entry, classed as a whole leaf, a leaf's tail past a cut, a subagent's
  transcript or a log under the state root (a file under the Codex sessions' transcripts there, or under the Claude
  projects root when it sits there, is never a log and falls into the three classes before it), with its file bytes (the
  classes sum to recordCache.bytes in one snapshot) and those bytes times the record cache's decoded multiple in force,
  read when the gauge runs (the pinned gauge's too), weighed after the record cache's lock;
- pinnedByAssembly: the records an assembly entry's adapter holds that the record cache no longer does (a drop, the
  count cap's eviction through the reader, where a budget eviction releases the entry instead, the reader's pop of a file
  that vanished or cannot be read, whose bytes go to no record-cache byte counter while the pop count rises, a from-zero
  re-read that decoded a second copy, a restored tail the reader upgraded to the whole file, before and after the fold
  that moves the entry onto the whole list), and nothing for an append, which shares the records; a folded leaf weighed
  by the bytes of the list it was folded onto, or by its bytes per record when the reader moved on before the re-seat;
  two entries over one prior file, holding two lists that share records, counted once by the longer list; its identity
  checks run after the record cache's lock;
- builtAtoms: the live materialized atoms times the measured size of an atom, and the slots a collected list left times
  an LRU entry's size, with a build, an eviction, a release and a dropped index each moving the live count; never more
  atoms priced than the LRU has slots, a drift past them shown as overSlots;
- restoredIndexes: each live lazy index's encoded rows (summed once, when the index is built), record rows (once per
  document list, however many indexes share it) and light facts, gone with the index;
- every gauge is O(entries): none walks a records list or an index's rows, in Python or in C, and each heap read and the
  fold's re-seat read a list by index a bounded number of times, the shared-list checks of an append not yet folded and
  of the re-seat included;
- checkpoints.readByPath on a plain read carries the 50 paths with the most bytes and readPaths says how many the whole
  table has; GET /perf?paths=all carries the whole table without the heap walks, and ?walk=1 carries it too; the plain
  read and both routes take the table, readBytes and documentBytes in one acquisition of the reader's lock; a failed read
  of the whole table leaves the plain read's rows on both, said once, and the walk still fills its two slots;
- the running counters keep their names and say they are counters (docstrings and the reference), and the assembly
  entries' own size is said to be a count alone.

Synthetic fixtures only: placeholder uuids, the notes-api demo names, invented text."""
import gc
import io
import json
import os
import shutil
import tempfile
import threading
import types
import unittest
import weakref
from contextlib import contextmanager, nullcontext, redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the tests/test_perf_heap_block.py preamble): the modules resolve their state root at
# import, and only pytest runs conftest's floor. The root minted here is outside conftest's belt, so session hosts are
# switched off in it as well (no session is connected here; the file makes that so whatever a later test does).
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_perf_resident", os.path.join(BIN, "romp-kernel"))
em, jd = km.em, km.jd

SID = "11111111-2222-3333-4444-%012d"
T0 = 1790000000.0
NEW_KEYS = ("recordCacheResident", "pinnedByAssembly", "builtAtoms", "restoredIndexes")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _recs(n, start=0, parent=None):
    """n records of a linear user/assistant chain, each about the same size (the fold estimate reads bytes per record)."""
    out = []
    for k in range(start, start + n):
        u = "aaaaaaaa-2222-3333-4444-%012d" % k
        if k % 2 == 0:
            msg = {"role": "user", "content": "list the notes in the web view, page %04d" % k}
            typ = "user"
        else:
            msg = {"role": "assistant", "content": [{"type": "text", "text": "the web view lists twelve notes, page %04d" % k}]}
            typ = "assistant"
        out.append({"type": typ, "timestamp": _iso(T0 + k), "uuid": u, "parentUuid": parent, "cwd": "/w/notes-api",
                    "message": msg})
        parent = u
    return out


def _lines(recs):
    return "".join(json.dumps(r) + "\n" for r in recs)


def _compacting():
    """A leaf whose assembly document has a cut: a turn, a manual compaction (its boundary and summary) and a turn after it,
    the shape of the golden suite's compaction scenario with invented text. The document's cut leaves a tail of the four
    records from the boundary on."""
    u = ["eeeeeeee-2222-3333-4444-%012d" % k for k in range(6)]
    return [
        {"type": "user", "timestamp": _iso(T0), "uuid": u[0], "parentUuid": None, "promptSource": "typed",
         "message": {"role": "user", "content": "refactor the notes list in the web view"}},
        {"type": "assistant", "timestamp": _iso(T0 + 30), "uuid": u[1], "parentUuid": u[0],
         "message": {"role": "assistant", "stop_reason": "end_turn",
                     "content": [{"type": "text", "text": "Working through it."},
                                 {"type": "tool_use", "id": "tu_notes_0", "name": "Edit", "input": {}}]}},
        {"type": "system", "subtype": "compact_boundary", "timestamp": _iso(T0 + 500), "uuid": u[2], "parentUuid": None,
         "logicalParentUuid": u[1], "isMeta": False,
         "compactMetadata": {"trigger": "manual", "preTokens": 263239, "postTokens": 6514}},
        {"type": "user", "timestamp": _iso(T0 + 505), "uuid": u[3], "parentUuid": u[2], "isCompactSummary": True,
         "isVisibleInTranscriptOnly": True, "message": {"role": "user", "content": "summary of the notes-api work so far"}},
        {"type": "user", "timestamp": _iso(T0 + 520), "uuid": u[4], "parentUuid": u[3], "promptSource": "sdk",
         "message": {"role": "user", "content": "continue with the api"}},
        {"type": "assistant", "timestamp": _iso(T0 + 530), "uuid": u[5], "parentUuid": u[4],
         "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Continuing."}]}},
    ]


def _turn_after(recs, k):
    """One more turn chained on the last record, stamped after every record (the fold's timestamp gate)."""
    t = max(em.parse_z(r["timestamp"]) for r in recs if r.get("timestamp")) + 100
    u, a = "ffffffff-2222-3333-4444-%012d" % (2 * k), "ffffffff-2222-3333-4444-%012d" % (2 * k + 1)
    return [{"type": "user", "timestamp": _iso(t), "uuid": u, "parentUuid": recs[-1]["uuid"], "promptSource": "sdk",
             "message": {"role": "user", "content": "and then the tests for the api"}},
            {"type": "assistant", "timestamp": _iso(t + 10), "uuid": a, "parentUuid": u,
             "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Two more."}]}}]


class _NoWalk(list):
    """A list a gauge may measure (its length, a few reads by index) but never walk. Iteration raises, and so does every route
    a walk can take in C (review of 2026-10-02: a pin on iteration alone passed a slice-and-map identity check): a slice
    longer than DELTA, the two-record delta a test's fold slices off the reader's list, membership, comparison, reversal,
    index, count, copy, concatenation and repetition. Reads by integer index are counted in `reads`, so a test can bound a
    Python loop by index as well."""
    DELTA = 2

    def __init__(self, *a):
        super().__init__(*a)
        self.reads = 0

    def __iter__(self):
        raise AssertionError("a gauge iterated a records list or an index's rows")

    def _walked(self, *a, **kw):
        raise AssertionError("a gauge walked a records list or an index's rows in C")

    def __getitem__(self, key):
        if isinstance(key, slice):
            n = len(range(*key.indices(len(self))))
            if n > self.DELTA:
                raise AssertionError("a gauge sliced %d records off a list" % n)
        else:
            self.reads += 1
        return list.__getitem__(self, key)

    __contains__ = __reversed__ = reverse = index = count = copy = _walked
    __eq__ = __ne__ = __lt__ = __le__ = __gt__ = __ge__ = _walked
    __add__ = __radd__ = __iadd__ = __mul__ = __rmul__ = __imul__ = _walked


class _OwnedLock:
    """A module's lock, delegating to the real one, that remembers which thread holds it: a plain Lock's locked() is
    true as well while a leftover thread from an earlier module holds it, so a spy asks whether the CALLING thread does.
    `after_release`, when given, runs after every release, with the real lock free."""

    def __init__(self, lock, after_release=None):
        self.lock, self.owner, self.after_release = lock, None, after_release

    def acquire(self, *a, **kw):
        got = self.lock.acquire(*a, **kw)
        if got:
            self.owner = threading.get_ident()
        return got

    def release(self):
        self.owner = None
        self.lock.release()
        if self.after_release is not None:
            self.after_release()

    def locked(self):
        return self.lock.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()

    def mine(self):
        return self.owner == threading.get_ident()


def _heap():
    return km._heap_stats()


def _collect_leftovers():
    """A collection before a test reads the live indexes as its baseline: a test that failed earlier in the process leaves its
    indexes alive in the traceback's frames, a cycle the collector breaks, and a baseline taken with them alive goes wrong when
    this test's own collection frees them (seen 2026-10-01: one red test's two indexes made a later test red as well)."""
    gc.collect()


def _settle_atom_lru():
    """Before a test reads builtAtoms as its baseline, what earlier test modules in the same process left in the atom LRU is
    put right, as the kernel itself never leaves it: the dead entries of lists they dropped are expired (a new list can reuse
    a dead list's id, and its registration then pops that entry, so the slot count rises by one fewer), and each live index's
    count is set to the entries its lists hold (a module that clears _MAT_LRU directly leaves the counts of the indexes it
    kept; seen 2026-10-01 in CI's shard order). O(entries), in the test only."""
    _collect_leftovers()
    with em._MAT_LOCK:
        held = {}
        for key, val in list(em._MAT_LRU.items()):
            ref = val[0] if isinstance(val, tuple) and val else None
            la = ref() if callable(ref) else None
            if la is None:
                em._MAT_LRU.pop(key, None)
                continue
            ix = getattr(la, "_index", None)
            held[id(ix)] = held.get(id(ix), 0) + 1
        for ix in list(em._LIVE_INDEXES):
            ix._built = held.get(id(ix), 0)


def _delta(a, b, *keys):
    return {k: b[k] - a[k] for k in keys}


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()   # real paths: the reader's key and the assembly cache's realpath key agree
        self.proj = self.td / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.paths, self.leaves, self.asm_keys, self.mat = [], [], [], []
        km._HEAP_SAID.clear()
        # every parse here reads the postal log too, and the record-cache pins below count its entries: the log is pinned to a
        # file that never exists (2026-10-05). The shared event model binds MESSAGES_LOG at each load from the environment as
        # it stands then, so in a whole-suite shard it can name another module's root that holds a written postal log, and
        # that log's record entry sat beside the leaf's (red whenever a shard's composition put such a module first)
        _msgs = mock.patch.object(em, "MESSAGES_LOG", self.td / "no-postal-log.jsonl")
        _msgs.start()
        self.addCleanup(_msgs.stop)

    def tearDown(self):
        for p in self.leaves:
            em.evict_document(str(p))                   # both caches: the assembly entry and the record entry
        with em._ASM_LOCK:
            for k in self.asm_keys:
                em._ASM_CACHE.pop(k, None)
        with em._JSONL_CACHE_LOCK:
            for p in self.paths:
                em._cache_pop_locked(str(p))
        with em._MAT_LOCK:
            for k in self.mat:
                em._MAT_LRU.pop(k, None)
        for p in self.paths:
            if str(p).startswith(str(jd.STATE)):
                Path(p).unlink(missing_ok=True)
        shutil.rmtree(self.td, ignore_errors=True)
        km._HEAP_SAID.clear()

    def write(self, path, recs):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_lines(recs))
        self.paths.append(path)
        return path

    def leaf(self, name, recs):
        p = self.write(self.proj / ("%s.jsonl" % name), recs)
        self.leaves.append(p)
        return p

    def append(self, path, recs):
        with open(path, "a") as fh:
            fh.write(_lines(recs))

    def weight(self, path):
        with em._JSONL_CACHE_LOCK:
            return em._entry_weight(em._JSONL_CACHE[str(path)])

    def pop(self, path):
        with em._JSONL_CACHE_LOCK:
            return em._cache_pop_locked(str(path))


class RecordCacheResident(Base):
    def test_each_class_counts_its_entries_and_file_bytes_and_weighs_them_by_the_multiple(self):
        """Red on main: the heap block had no record-cache gauge; recordCache.bytes was the file bytes of every entry in one
        number, with nothing to say which entries hold whole transcripts and what they weigh in memory."""
        whole = self.leaf("web", _recs(8))
        tailp = self.leaf("api", _recs(10))
        sub = self.write(self.proj / (SID % 1) / "subagents" / "agent-a0123456789abcdef.jsonl", _recs(4))
        log = self.write(jd.STATE / "states" / ("%s.jsonl" % (SID % 2)), [{"t": T0 + k, "state": "busy"} for k in range(5)])
        data = tailp.read_bytes()
        cut = 0
        for _ in range(6):                              # the cut after the sixth record: a tail of four
            cut = data.index(b"\n", cut) + 1
        h0 = _heap()["recordCacheResident"]
        for p in (whole, sub, log):
            self.assertIsNotNone(em._read_jsonl_entry(str(p)))
        ent = em._read_jsonl_entry(str(tailp), tail_ok=True, tail_from=(cut, 6, data[max(0, cut - 64):cut]))
        self.assertEqual((ent[5], len(ent[4])), (6, 4), "a tail entry from the cut")
        h1 = _heap()["recordCacheResident"]
        m = em.RECORD_DECODED_MULTIPLE
        self.assertEqual(h1["multiple"], m)
        self.assertTrue(2.5 <= m <= 3.5, "the decoded multiple the live fit and the lab measured (2.85, 2.86): %r" % m)
        for cls, p in (("wholeLeaf", whole), ("tail", tailp), ("subagent", sub), ("logs", log)):
            d = _delta(h0["classes"][cls], h1["classes"][cls], "entries", "fileBytes")
            self.assertEqual(d, {"entries": 1, "fileBytes": self.weight(p)}, cls)
        self.assertEqual(h1["classes"]["tail"]["fileBytes"] - h0["classes"]["tail"]["fileBytes"], len(data) - cut,
                         "a tail weighs the bytes past its cut, never the whole file")
        self.assertEqual(h1["classes"]["wholeLeaf"]["fileBytes"] - h0["classes"]["wholeLeaf"]["fileBytes"], whole.stat().st_size)
        for cls, row in h1["classes"].items():
            self.assertEqual(row["bytes"], int(round(row["fileBytes"] * m)), cls)
        self.assertEqual(set(h1["classes"]), {"wholeLeaf", "tail", "subagent", "logs"})
        self.assertEqual(h1["fileBytes"], sum(r["fileBytes"] for r in h1["classes"].values()))
        self.assertEqual(h1["bytes"], sum(r["bytes"] for r in h1["classes"].values()))
        self.assertEqual(h1["entries"], sum(r["entries"] for r in h1["classes"].values()))

    def test_the_classes_sum_to_the_record_cache_counted_bytes_in_one_snapshot(self):
        """recordCache.bytes is the cache's running ledger, which the kernel keeps in step on every insert and pop. A test
        module that clears _JSONL_CACHE directly leaves that ledger stale for the next module in the same process (CI runs
        a shard's modules in one process; seen 2026-10-01 after tests/test_perf_parse_roads.py and test_asm_checkpoint.py),
        so the ledger is first set to what the held entries weigh, and the insert must then move both figures alike."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE_BYTES[0] = sum(em._entry_weight(e) for e in em._JSONL_CACHE.values())
        s0 = km._PerfStats().snapshot()
        self.leaf("web", _recs(6))
        em._read_jsonl_entry(str(self.leaves[0]))
        snap = km._PerfStats().snapshot()
        json.dumps(snap)
        rc, est = snap["recordCache"], snap["heap"]["recordCacheResident"]
        self.assertEqual((est["entries"], est["fileBytes"]), (rc["entries"], rc["bytes"]),
                         "the same entries and the same file bytes as the counted cache, now in memory units beside them")
        rc0, est0 = s0["recordCache"], s0["heap"]["recordCacheResident"]
        self.assertEqual((est["entries"] - est0["entries"], est["fileBytes"] - est0["fileBytes"]),
                         (rc["entries"] - rc0["entries"], rc["bytes"] - rc0["bytes"]), "one insert moves both alike")
        self.assertEqual(est["fileBytes"] - est0["fileBytes"], self.weight(self.leaves[0]))
        self.assertGreater(est["bytes"], est["fileBytes"])

    def test_a_codex_session_transcript_under_the_state_root_is_a_leaf_not_a_log(self):
        """Red at the first cut: every entry under the state root was a log, and the Codex backend writes its session
        transcripts there (STATE/codex/projects/<cwd>/<thread>.jsonl, judge.CODEXDIR), Claude-shaped leaves the whole read
        side parses; on a machine running Codex sessions they filled `logs` and left wholeLeaf and tail short."""
        codex = self.write(jd.CODEXDIR / "projects" / "-w-notes-api" / ("%s.jsonl" % (SID % 5)), _recs(8))
        self.addCleanup(self._rmdirs, codex.parent)
        h0 = _heap()["recordCacheResident"]["classes"]
        self.assertIsNotNone(em._read_jsonl_entry(str(codex)))
        h1 = _heap()["recordCacheResident"]["classes"]
        d = {c: (h1[c]["entries"] - h0[c]["entries"], h1[c]["fileBytes"] - h0[c]["fileBytes"]) for c in h1}
        self.assertEqual(d, {"wholeLeaf": (1, codex.stat().st_size), "tail": (0, 0), "subagent": (0, 0), "logs": (0, 0)},
                         "a Codex transcript under %s is a whole leaf" % jd.STATE)

    def test_a_claude_transcript_under_the_state_root_is_a_leaf_not_a_log(self):
        """Red before the 2026-10-02 review's fold: only the Codex root was kept out of `logs`, and the logs check runs before
        the subagent and tail checks, so with the Claude config directory placed inside the state root (a hand-written
        layout bin/romp-manager accepts) every Claude transcript there was a log and wholeLeaf, tail and subagent read 0. The
        kernel passes the Claude projects root (judge.PROJECTS) beside the Codex one."""
        projects = jd.STATE / "claude-config" / "projects"
        leaf = self.write(projects / "-w-notes-api" / ("%s.jsonl" % (SID % 14)), _recs(8))
        self.addCleanup(self._rmdirs, leaf.parent, 3)
        with mock.patch.object(jd, "PROJECTS", projects):
            h0 = _heap()["recordCacheResident"]["classes"]
            self.assertIsNotNone(em._read_jsonl_entry(str(leaf)))
            h1 = _heap()["recordCacheResident"]["classes"]
        d = {c: (h1[c]["entries"] - h0[c]["entries"], h1[c]["fileBytes"] - h0[c]["fileBytes"]) for c in h1}
        self.assertEqual(d, {"wholeLeaf": (1, leaf.stat().st_size), "tail": (0, 0), "subagent": (0, 0), "logs": (0, 0)},
                         "a Claude transcript under %s is a whole leaf" % jd.STATE)

    def test_a_claude_subagent_transcript_under_the_state_root_is_a_subagent(self):
        """Review round five (2026-10-02): files under the transcript roots are never logs and take the other classes by the
        usual rules, so a subagent's transcript under a Claude projects root inside the state root is a subagent, not the
        whole leaf the reference's earlier wording promised."""
        projects = jd.STATE / "claude-config" / "projects"
        sub = self.write(projects / "-w-notes-api" / (SID % 15) / "subagents" / "agent-0001.jsonl", _recs(5))
        self.addCleanup(self._rmdirs, sub.parent, 5)
        with mock.patch.object(jd, "PROJECTS", projects):
            h0 = _heap()["recordCacheResident"]["classes"]
            self.assertIsNotNone(em._read_jsonl_entry(str(sub)))
            h1 = _heap()["recordCacheResident"]["classes"]
        d = {c: (h1[c]["entries"] - h0[c]["entries"], h1[c]["fileBytes"] - h0[c]["fileBytes"]) for c in h1}
        self.assertEqual(d, {"wholeLeaf": (0, 0), "tail": (0, 0), "subagent": (1, self.weight(sub)), "logs": (0, 0)},
                         "a Claude subagent transcript under %s is a subagent" % jd.STATE)

    @staticmethod
    def _rmdirs(d, levels=2):
        """The directories a test made under the state root, `levels` of them from `d` up, removed while empty (another
        test's files keep them)."""
        for p in [d, *d.parents][:levels]:
            try:
                p.rmdir()
            except OSError:
                return


class PinnedByAssembly(Base):
    def pinned(self):
        return _heap()["pinnedByAssembly"]

    def test_a_leaf_the_record_cache_dropped_is_still_counted_while_its_assembly_entry_holds_it(self):
        """Red on main: no gauge. The instructive exception of the diagnosis: the record cache let go of a leaf, its counted
        bytes fell, and the assembly entry's adapter still held the very same records list, so the memory did not. The pop
        stands for the count cap's eviction, which pops without the pin check (the next test evicts through the reader by
        the count cap and by the byte budget, which releases the entry as well): since #87 a quiet
        drop or a release under a whole entry with no document keeps the reader entry, counted recordCache.pinnedKept, and
        the gauge does not move, as the first step here pins; a quiet drop under a restored entry pops it, and after a
        whole reader has upgraded the leaf, while the entry still has only the tail it read, the pop counts under
        recordCache.dropped alone (tests/test_asm_reseat.py pins that road)."""
        leaf = self.leaf("web", _recs(6))
        em.parse_session(leaf)
        p0, r0 = self.pinned(), _heap()["recordCacheResident"]
        w = self.weight(leaf)
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE.get(str(leaf))
        self.assertEqual(em._drop_unless_pinned(str(leaf), ent), "kept", "the pin keeps a whole entry's leaf (#87)")
        self.assertEqual(_delta(p0, self.pinned(), "lists", "records", "fileBytes"), {"lists": 0, "records": 0, "fileBytes": 0},
                         "a kept drop moves no pinned bytes")
        self.pop(leaf)                                   # what the count cap's eviction does to the record cache
        p1, r1 = self.pinned(), _heap()["recordCacheResident"]
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 1, "records": 6, "fileBytes": w})
        self.assertEqual(p1["bytes"], int(round(p1["fileBytes"] * em.RECORD_DECODED_MULTIPLE)))
        self.assertEqual(r0["fileBytes"] - r1["fileBytes"], w, "the record cache's count fell by the leaf; the pinned gauge holds it")

    def test_an_eviction_through_the_reader_pins_by_the_count_cap_and_releases_the_entry_by_the_budget(self):
        """Review round seven (2026-10-03): the two eviction roads, taken through the record reader on the six-record leaf. The
        count cap pops the leaf without the pin check, so the gauge rises by its list, records and weight, and the share counts
        under recordCache.evictedBytes alone, never under a drop counter. A budget eviction releases the assembly entry that
        read the leaf (recordCache.budgetReleased, #84), so the gauge does not move. Each case empties the record cache first
        (restored after): either road evicts the oldest entries first, and a budget of one byte evicts every one."""
        drops = ("droppedHeld", "droppedHeldBytes", "dropped", "droppedBytes", "pinnedKept")

        def over(leaf):
            with em._ASM_LOCK:
                return [e for e in em._ASM_CACHE.values() if e.get("path") == str(leaf)]
        for road, name, cap in (("count cap", "web", "_JSONL_CACHE_MAX"), ("byte budget", "api", "_JSONL_CACHE_BUDGET_BYTES")):
            with self.subTest(road=road), mock.patch.dict(em._JSONL_CACHE, clear=True), \
                    mock.patch.object(em, "_JSONL_CACHE_BYTES", [0]), mock.patch.dict(em._BUDGET_EVICTED):
                leaf = self.leaf(name, _recs(6))
                other = self.write(self.td / "elsewhere" / ("%s.jsonl" % name), _recs(2))
                em.parse_session(leaf)
                w = self.weight(leaf)
                self.assertEqual((list(em._JSONL_CACHE), len(over(leaf))), ([str(leaf)], 1), "one entry to evict, one over it")
                p0, s0 = self.pinned(), em.record_cache_stats()
                with mock.patch.object(em, cap, 1):
                    self.assertIsNotNone(em._read_jsonl_entry(str(other)))
                p1, s1 = self.pinned(), em.record_cache_stats()
                moved = _delta(p0, p1, "lists", "records", "fileBytes")
                count = _delta(s0, s1, "evictions", "budgetEvictions", "budgetReleased", "evictedBytes", "released")
                if road == "count cap":
                    self.assertEqual(moved, {"lists": 1, "records": 6, "fileBytes": w})
                    self.assertEqual(count, {"evictions": 1, "budgetEvictions": 0, "budgetReleased": 0, "evictedBytes": w,
                                             "released": 1})
                    self.assertEqual(_delta(s0, s1, *drops), dict.fromkeys(drops, 0), "an eviction moves no drop counter")
                    self.assertEqual(len(over(leaf)), 1, "the count cap releases no assembly entry")
                else:
                    self.assertEqual(count, {"evictions": 1, "budgetEvictions": 1, "budgetReleased": 1, "evictedBytes": w,
                                             "released": 1})
                    self.assertEqual(over(leaf), [], "the budget's eviction released the entry that read the leaf")
                    self.assertEqual(moved, {"lists": 0, "records": 0, "fileBytes": 0}, "so the pinned gauge does not move")

    def test_a_vanished_or_unreadable_file_popped_by_the_reader_moves_no_byte_counter(self):
        """Review rounds eight and nine (2026-10-03): the record reader's pop of a file that vanished or cannot be read leaves
        the records an assembly entry still holds to the pinned gauge, which rises by the leaf's list, records and weight,
        while no record-cache byte counter takes the share: evictedBytes, the drop counts and their bytes stay where they
        were, and recordCache.released counts the pop as it counts every pop. One subtest for each of the reader's three
        OSError branches: the first stat, before any lock, on a file already unlinked ("unlinked"); the stat under the path's
        read stripe, on a file that goes while the reader waits on the stripe ("gone under the stripe": the leaf's mtime
        moved, so neither hit check serves the cached entry, and the stripe unlinks the file once it is taken); and the open,
        on a mode-0 file with its mtime moved ("unreadable"), skipped as root, which reads such a file."""
        drops = ("droppedHeld", "droppedHeldBytes", "dropped", "droppedBytes", "evictedBytes", "evictions")
        real_stripe, taken = em._read_stripe, []

        @contextmanager
        def unlink_once_taken(path):                          # the file goes while the reader waits behind another read
            with real_stripe(path):
                taken.append(path)
                os.unlink(path)
                yield

        def moved(leaf):                                      # an unchanged stat serves the cached entry without a read
            st = os.stat(str(leaf))
            os.utime(str(leaf), ns=(st.st_atime_ns, st.st_mtime_ns + 5 * 10 ** 9))
        for how, name in (("unlinked", "web"), ("gone under the stripe", "tests"), ("unreadable", "api")):
            with self.subTest(how=how), mock.patch.dict(em._JSONL_CACHE, clear=True), mock.patch.object(em, "_JSONL_CACHE_BYTES", [0]):
                if how == "unreadable" and hasattr(os, "geteuid") and os.geteuid() == 0:
                    self.skipTest("root reads a mode-0 file: the unreadable case cannot be made")
                leaf = self.leaf(name, _recs(6))
                em.parse_session(leaf)
                w = self.weight(leaf)
                self.assertEqual(list(em._JSONL_CACHE), [str(leaf)], "the leaf's entry stands before it goes")
                p0, s0 = self.pinned(), em.record_cache_stats()
                stripe = nullcontext()
                if how == "unlinked":
                    os.unlink(str(leaf))
                elif how == "gone under the stripe":
                    moved(leaf)
                    stripe = mock.patch.object(em, "_read_stripe", unlink_once_taken)
                else:
                    os.chmod(str(leaf), 0)
                    moved(leaf)
                try:
                    with stripe:
                        self.assertIsNone(em._read_jsonl_entry(str(leaf)), "the reader returns no entry for the file")
                finally:
                    if how == "unreadable":
                        os.chmod(str(leaf), 0o644)
                if how == "gone under the stripe":
                    self.assertEqual(taken, [str(leaf)], "the first stat saw the file; it went once the stripe was taken")
                p1, s1 = self.pinned(), em.record_cache_stats()
                self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 1, "records": 6, "fileBytes": w},
                                 "the pinned gauge holds the records the assembly entry still has")
                self.assertEqual(_delta(s0, s1, "released"), {"released": 1}, "the pop is counted as every pop is")
                self.assertEqual(_delta(s0, s1, *drops), dict.fromkeys(drops, 0), "no byte counter takes the popped share")

    def test_a_from_zero_reread_counts_both_copies_until_the_entry_is_rebuilt(self):
        leaf = self.leaf("api", _recs(6))
        em.parse_session(leaf)
        p0 = self.pinned()
        w = self.weight(leaf)
        self.pop(leaf)
        em._read_jsonl_entry(str(leaf))                  # the next whole reader decodes a second copy
        p1, r1 = self.pinned(), _heap()["recordCacheResident"]
        self.assertEqual(_delta(p0, p1, "lists", "fileBytes"), {"lists": 1, "fileBytes": w}, "the adapter's copy is still counted")
        self.assertGreaterEqual(r1["fileBytes"], w, "and the record cache counts the new one")
        mode = []
        em.parse_session(leaf, asm_mode_out=mode)        # the generation moved: the gates demote, the whole parse rebuilds the entry
        self.assertEqual(mode, ["full"])
        p2 = self.pinned()
        self.assertEqual(_delta(p0, p2, "lists", "records", "fileBytes"), {"lists": 0, "records": 0, "fileBytes": 0},
                         "the rebuilt entry holds the record cache's own list")

    def test_an_append_shares_the_records_and_pins_nothing(self):
        """An append stores a NEW list, records + new: the adapter's older list is a different object holding the same
        records, so nothing is pinned beyond the cache's own count."""
        leaf = self.leaf("tests", _recs(6))
        em.parse_session(leaf)
        p0 = self.pinned()
        self.append(leaf, _recs(2, start=6, parent="aaaaaaaa-2222-3333-4444-%012d" % 5))
        ent = em._read_jsonl_entry(str(leaf))
        self.assertEqual(len(ent[4]), 8)
        p1 = self.pinned()
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 0, "records": 0, "fileBytes": 0})

    def test_a_folded_leaf_is_weighed_by_the_list_it_was_folded_onto(self):
        """The fold re-seats the adapter on the reader's longer list and weighs it by that entry, so a later drop moves the
        two gauges by the same file bytes (the first cut scaled the first read's bytes per record over the longer list)."""
        leaf = self.leaf("web", _recs(6))
        em.parse_session(leaf)
        self.append(leaf, _recs(4, start=6, parent="aaaaaaaa-2222-3333-4444-%012d" % 5))
        mode = []
        em.parse_session(leaf, asm_mode_out=mode)
        self.assertEqual(mode, ["fold"], "the append folded into the entry")
        p0 = self.pinned()
        w = self.weight(leaf)
        self.assertEqual(w, leaf.stat().st_size)
        self.pop(leaf)
        p1 = self.pinned()
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 1, "records": 10, "fileBytes": w})

    def test_a_list_the_adapter_cannot_weigh_as_it_stands_is_weighed_by_its_bytes_per_record(self):
        """The fallback when a fold could not weigh its list (the reader's entry had moved on by the re-seat): the recorded
        list's bytes per record."""
        self.assertEqual(em._list_file_bytes((600, 6), 6), 600)
        self.assertEqual(em._list_file_bytes((600, 6), 10), 1000)
        self.assertEqual(em._list_file_bytes((600, 0), 10), 0)
        self.assertEqual(em._list_file_bytes(None, 10), 0)

    def test_a_list_the_reader_moved_on_from_before_the_re_seat_is_weighed_by_its_bytes_per_record(self):
        """Review of 2026-10-02: the road the fallback serves was never driven. A reader moves the leaf's entry on between the
        fold's gates and its re-seat (a second append, read through the reader), so the cache no longer holds the list the
        fold re-seats on, and once the cache drops the leaf the adapter's ten records are weighed by the bytes per record of
        the six it first took: int(w0 * 10 / 6), itself an estimate. Weighing by whatever entry the cache holds for the path
        (the moved-on one, twelve records) or dropping the scaling reads another figure."""
        head = "aaaaaaaa-2222-3333-4444-%012d"
        leaf = self.leaf("web", _recs(6))
        em.parse_session(leaf)
        w0 = self.weight(leaf)
        self.append(leaf, _recs(4, start=6, parent=head % 5))
        real, ran = em._asm_reseat_src, []

        def moved_on(ad, path, recs):
            ran.append(path)
            self.append(leaf, _recs(2, start=10, parent=head % 9))
            self.assertEqual(len(em._read_jsonl_entry(str(leaf))[4]), 12, "the reader's entry moved on by two records")
            return real(ad, path, recs)
        modes = []
        with mock.patch.object(em, "_asm_reseat_src", moved_on):
            em.parse_session(leaf, asm_mode_out=modes)
        self.assertEqual(modes, ["fold"], "the first append folded")
        self.assertEqual(ran, [str(leaf)], "the re-seat ran once, for the leaf")
        p0 = self.pinned()
        self.pop(leaf)
        p1 = self.pinned()
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"),
                         {"lists": 1, "records": 10, "fileBytes": int(w0 * 10 / 6)})

    def test_a_restored_tail_the_reader_upgraded_stays_counted_after_the_fold_moves_the_entry(self):
        """Red at the first cut (review of 2026-10-01). A restored entry's adapter holds the leaf's tail; a whole reader (the
        judges' road over a restored live leaf) upgrades the reader's entry to the whole file under the same generation, with
        freshly decoded records; the next append folds and re-seats the adapter on the whole list. The gauge decided pinned
        only by the adapter's current list, so it went to zero there while the adapter's maps still held every tail record:
        the gauge said the copy had gone and the memory stayed."""
        sid = SID % 6
        saved = em._CKPT_DIR_FN
        ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: ck)
        self.addCleanup(em.set_checkpoint_dir, saved)
        recs = _compacting()
        leaf = self.leaf(sid, recs)

        def parse(modes=None):
            return em.parse_session(leaf, rompuuid=sid, name="api", dir="/TESTDIR", candidate_files=[leaf], states=None,
                                    postal_log=[], now=T0 + 3600, asm_mode_out=modes)
        with mock.patch.object(em, "_ASM_FIRST_DOC_MIN", 0):   # the suite's setting (conftest), held for a direct run too
            tree = parse()
            self.assertTrue(em.asm_checkpoint_write(str(leaf), sid, tree=tree), em.asm_checkpoint_stats())
        em.evict_document(str(leaf))                     # a restart's in-memory side for this leaf: neither cache holds it
        modes = []
        parse(modes)
        self.assertEqual(modes, ["restore"])
        with em._JSONL_CACHE_LOCK:
            tail = em._JSONL_CACHE[str(leaf)]
        self.assertGreater(tail[5], 0, "the reader holds the leaf's tail past the document's cut")
        n_tail, w_tail = len(tail[4]), em._entry_weight(tail)
        p0 = self.pinned()
        whole = em._read_jsonl_entry(str(leaf))          # a whole reader: the tail upgraded, the same generation, new objects
        self.assertEqual((whole[5], whole[6], len(whole[4])), (0, tail[6], len(recs)))
        p1 = self.pinned()
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 1, "records": n_tail, "fileBytes": w_tail},
                         "the adapter's tail, before any fold")
        more = _turn_after(recs, 0)
        self.append(leaf, more)
        modes = []
        parse(modes)
        self.assertEqual(modes, ["fold"], "the append folds onto the whole list")
        p2 = self.pinned()
        self.assertEqual(_delta(p0, p2, "lists", "records", "fileBytes"), {"lists": 1, "records": n_tail, "fileBytes": w_tail},
                         "the fold moved the entry onto the whole list and the tail's records are still held: still counted")
        w = self.weight(leaf)
        self.pop(leaf)
        p3 = self.pinned()
        self.assertEqual(_delta(p0, p3, "lists", "records", "fileBytes"),
                         {"lists": 2, "records": n_tail + len(recs) + len(more), "fileBytes": w_tail + w},
                         "once the record cache drops the whole list too, both copies, the whole one weighed by its own bytes")

    def test_two_entries_over_one_prior_file_count_its_records_once_by_the_longer_list(self):
        """Two sessions over one prior file (a /clear's old leaf, a fork's parent): each assembly entry's adapter takes the
        reader's list for it, and an append to the prior file between the two parses leaves them two DIFFERENT lists, the
        later one holding every record of the earlier (the reader stores records + new). Once the record cache lets the file
        go, its records count once, by the longer list: a key on the list object counted the shared records twice, and
        keeping the first list met missed the appended ones. Review of 2026-10-01: the test this replaces copied one entry,
        so both held the very same list and any key passed."""
        head = "aaaaaaaa-2222-3333-4444-%012d" % 5
        prior = self.write(self.proj / ("%s.jsonl" % (SID % 11)), _recs(6))
        a = self.leaf(SID % 12, _recs(2, start=100, parent=head))
        b = self.leaf(SID % 13, _recs(2, start=200, parent=head))
        em.parse_session(a, candidate_files=[prior, a])
        self.append(prior, _recs(2, start=300, parent=head))
        self.assertEqual(len(em._read_jsonl_entry(str(prior))[4]), 8, "an append: a new list holding the old one's records")
        em.parse_session(b, candidate_files=[prior, b])
        with em._ASM_LOCK:
            ads = {e.get("path"): e["ad"] for e in em._ASM_CACHE.values() if e.get("path") in (str(a), str(b))}
        la, lb = ads[str(a)]._src[str(prior)], ads[str(b)]._src[str(prior)]
        self.assertEqual((len(la), len(lb)), (6, 8))
        self.assertIsNot(la, lb, "two lists, one per entry")
        self.assertIs(la[0], lb[0], "sharing the earlier list's records")
        p0 = self.pinned()
        w = self.weight(prior)
        self.pop(prior)
        p1 = self.pinned()
        self.assertEqual(_delta(p0, p1, "lists", "records", "fileBytes"), {"lists": 1, "records": 8, "fileBytes": w},
                         "the prior file's records once, by the longer list and its own bytes")


class MultipleInForce(Base):
    def test_the_estimates_read_the_record_caches_multiple_when_they_run(self):
        """Review round seven (2026-10-03): the merge of #84 weighs the estimates by the record cache's multiple in force,
        so ROMP_RECORD_CACHE_DECODED_MULTIPLE moves the budget and the gauges alike, yet no test failed with the estimates
        set back to 2.85, and the binding was a copy taken at load. A fresh load with the variable at 2 prices the record-cache
        estimate (the heap block's recordCacheResident) and a pinned list at 2, beside recordCache.decodedMultiple; rebinding
        the multiple in the loaded module moves the heap block's estimates with it."""
        with self.subTest(case="a fresh load at 2"):
            env = {k: v for k, v in os.environ.items() if k != "ROMP_RECORD_CACHE_BUDGET_MB"}
            env["ROMP_RECORD_CACHE_DECODED_MULTIPLE"] = "2"
            with mock.patch.dict(os.environ, env, clear=True):
                fresh = load_source("romp_event_model_gauges_multiple", os.path.join(BIN, "romp-event-model"))
            leaf = self.leaf("web", _recs(6))
            self.addCleanup(fresh.evict_document, str(leaf))
            fresh.parse_session(leaf)
            st, est = fresh.record_cache_stats(), fresh.record_cache_resident()
            self.assertEqual((est["multiple"], st["decodedMultiple"]), (2.0, 2.0))
            self.assertEqual(est["fileBytes"], st["bytes"])
            self.assertEqual(est["bytes"], st["realBytes"], "the estimate reads the real bytes the budget weighs")
            with fresh._JSONL_CACHE_LOCK:
                w = fresh._cache_pop_locked(str(leaf))
            p = fresh.asm_pinned_records()
            self.assertEqual((p["lists"], p["records"], p["fileBytes"], p["bytes"]), (1, 6, w, 2 * w), "a pinned list at 2 too")
        with self.subTest(case="the multiple rebound to 3.5"):
            leaf = self.leaf("api", _recs(6))
            em.parse_session(leaf)
            w = self.weight(leaf)
            with mock.patch.object(em, "_RECORD_CACHE_MULTIPLE", 3.5):
                rc, st, p0 = _heap()["recordCacheResident"], em.record_cache_stats(), _heap()["pinnedByAssembly"]
                self.pop(leaf)
                p1 = _heap()["pinnedByAssembly"]
            self.assertEqual((rc["multiple"], st["decodedMultiple"]), (3.5, 3.5))
            for cls, row in rc["classes"].items():
                self.assertEqual(row["bytes"], int(round(row["fileBytes"] * 3.5)), cls)
            self.assertEqual(p1["fileBytes"] - p0["fileBytes"], w)
            self.assertEqual(p1["bytes"], int(round(p1["fileBytes"] * 3.5)), "the pinned bytes at 3.5 too")
            self.assertEqual(_heap()["recordCacheResident"]["multiple"], em.RECORD_DECODED_MULTIPLE, "and back once restored")


class _Gone:
    """Something a weak reference can outlive: the stand-in for a collected LazyAtoms in a planted LRU entry."""


class BuiltAtoms(Base):
    def test_a_dead_slot_weighs_an_lru_entry_and_a_live_atom_an_atom(self):
        _settle_atom_lru()
        h0 = _heap()
        gone = _Gone()
        dead = weakref.ref(gone)
        del gone
        self.assertIsNone(dead())
        with em._MAT_LOCK:
            for i in range(7):
                k = ("synthetic-resident", i)
                em._MAT_LRU[k] = (dead, i)               # an entry whose list has been collected: it waits to expire at the old end
                self.mat.append(k)
        h1 = _heap()
        b0, b1 = h0["builtAtoms"], h1["builtAtoms"]
        self.assertEqual((b1["slots"] - b0["slots"], b1["live"] - b0["live"]), (7, 0), "seven slots, no live atom among them")
        self.assertEqual(b1["slots"], h1["materializedLruSlots"], "the same read as the slot count beside it")
        self.assertEqual((b1["atomBytes"], b1["slotBytes"]), (em.MAT_ATOM_BYTES, em.MAT_SLOT_BYTES))
        self.assertTrue(1300 <= em.MAT_ATOM_BYTES <= 2100, "inside the 1.3 to 2.1 KB an atom measured over real indexes")
        self.assertTrue(200 <= em.MAT_SLOT_BYTES <= 250, "the 218.6 to 234.1 bytes a dead entry measured")
        self.assertEqual(b1["bytes"] - b0["bytes"], 7 * em.MAT_SLOT_BYTES)
        self.assertEqual(b1["bytes"], b1["live"] * em.MAT_ATOM_BYTES + (b1["slots"] - b1["live"]) * em.MAT_SLOT_BYTES)
        self.assertEqual(b1["overSlots"], 0, "no live count past the slots")

    def test_bytes_never_price_more_atoms_than_the_lru_has_slots(self):
        """Red before review round two's fold (2026-10-01): `live` is the indexes' own sum, left uncut so a miscount shows, and
        `bytes` priced every one of them at an atom's size, so an over-count (a list collected while its index lives keeps
        its slots in `live`) priced atoms the LRU could not hold. The LRU registers every built atom, so `bytes` prices at
        most one per slot, and the drift past the slots is published as overSlots instead."""
        _settle_atom_lru()
        h0 = _heap()["builtAtoms"]
        self.assertLessEqual(h0["live"], h0["slots"], "settled: every live atom has its slot")
        self.assertEqual(h0["overSlots"], 0)
        rows = [json.dumps({"syn": True, "s": {"uuid": "bbbbbbbb-2222-3333-4444-%012d" % k, "type": "assistant", "t": T0 + k}})
                for k in range(3)]
        ix = em.LazyIndex({"atoms": rows, "records": [], "fsids": []}, SID % 9, str(self.proj / "tests.jsonl"))
        with em._MAT_LOCK:
            ix._built = h0["slots"] - h0["live"] + 5     # a count that drifted five past the slots
        h1 = _heap()["builtAtoms"]
        self.assertEqual((h1["slots"], h1["live"], h1["overSlots"]), (h0["slots"], h0["slots"] + 5, 5),
                         "live stays the indexes' own sum, the drift said")
        self.assertEqual(h1["bytes"], h1["slots"] * em.MAT_ATOM_BYTES, "every slot an atom at most, none priced twice")
        with em._MAT_LOCK:
            ix._built = 0
        del ix
        gc.collect()
        self.assertEqual(_heap()["builtAtoms"], h0, "the index gone, the block as it was")

    def test_a_build_an_eviction_a_release_and_a_dropped_tree_move_the_live_count(self):
        """Red at the first cut (review of 2026-10-01): every slot was priced as a whole atom, the slots a collected list
        leaves until they expire included, so near the cap the gauge could report about 1.75 GB that was largely gone."""
        rows = [json.dumps({"syn": True, "s": {"uuid": "bbbbbbbb-2222-3333-4444-%012d" % k, "type": "assistant", "t": T0 + k}})
                for k in range(12)]
        _settle_atom_lru()
        ix = em.LazyIndex({"atoms": rows, "records": [], "fsids": []}, SID % 7, str(self.proj / "web.jsonl"))
        lists = [em.LazyAtoms(ix, list(range(0, 6))), em.LazyAtoms(ix, list(range(6, 12)))]
        self.addCleanup(lambda: ix.release() if ix is not None else None)
        h0 = _heap()["builtAtoms"]
        for la in lists:
            for i in range(4):
                la[i]
        h1 = _heap()["builtAtoms"]
        self.assertEqual((h1["slots"] - h0["slots"], h1["live"] - h0["live"]), (8, 8), "eight built, eight live")
        self.assertEqual(h1["bytes"] - h0["bytes"], 8 * em.MAT_ATOM_BYTES)
        with em._MAT_LOCK:                               # an eviction at the cap, of this index's entry (moved to the old end, so
            em._MAT_LRU.move_to_end((id(lists[0]), 0), last=False)   # the trim meets it and no other test's)
            with mock.patch.object(em, "_MAT_CAP", len(em._MAT_LRU) - 1):
                em._mat_trim()
        self.assertIs(list.__getitem__(lists[0], 0), em._UNMAT, "the evicted slot is back to the placeholder")
        h2 = _heap()["builtAtoms"]
        self.assertEqual((h2["slots"] - h0["slots"], h2["live"] - h0["live"]), (7, 7), "the eviction took one off")
        self.assertEqual(ix.release(), 7)
        h3 = _heap()["builtAtoms"]
        self.assertEqual((h3["slots"], h3["live"]), (h0["slots"], h0["live"]), "the release took the rest off")
        for la in lists:                                 # a tree that outlived the release builds again, then is dropped whole
            la[5]
        h4 = _heap()["builtAtoms"]
        self.assertEqual((h4["slots"] - h0["slots"], h4["live"] - h0["live"]), (2, 2))
        self.mat.extend((id(la), 5) for la in lists)     # tearDown pops the entries the collected lists leave
        del la, lists
        ix = None
        gc.collect()
        h5 = _heap()["builtAtoms"]
        self.assertEqual((h5["slots"] - h0["slots"], h5["live"] - h0["live"]), (2, 0),
                         "the dropped tree's slots stay until they expire, and none of them is a live atom")
        self.assertEqual(h5["bytes"] - h0["bytes"], 2 * em.MAT_SLOT_BYTES)


class RestoredIndexes(Base):
    def test_rows_records_and_facts_of_each_live_index_and_gone_with_it(self):
        rows = [json.dumps({"syn": True, "s": {"uuid": "bbbbbbbb-2222-3333-4444-%012d" % k, "type": "assistant", "t": T0 + k},
                            "m": {"role": "assistant", "content": [{"type": "text", "text": "note %d of the web view" % k}]}})
                for k in range(40)]
        recs = [["cccccccc-2222-3333-4444-%012d" % k, "k", "u", None, 1000 + k, T0 + k, 0, 300 + k, 0,
                 "cccccccc-2222-3333-4444-%012d" % (k - 1)] for k in range(30)]
        _collect_leftovers()
        h0 = _heap()["restoredIndexes"]
        ix = em.LazyIndex({"atoms": rows, "records": recs, "fsids": []}, SID % 3, str(self.proj / "web.jsonl"))
        ix._user_facts.update({k: {"type": "user"} for k in range(3)})
        h1 = _heap()["restoredIndexes"]
        rb = sum(len(r.encode("utf-8")) for r in rows)
        self.assertEqual(_delta(h0, h1, "indexes", "rows", "rowBytes", "records", "userFacts"),
                         {"indexes": 1, "rows": 40, "rowBytes": rb, "records": 30, "userFacts": 3})
        self.assertEqual(h1["bytes"] - h0["bytes"],
                         rb + 40 * em.INDEX_ROW_OVERHEAD_BYTES + 30 * em.INDEX_RECORD_ROW_BYTES + 3 * em.USER_FACT_BYTES)
        del ix
        gc.collect()
        self.assertEqual(_heap()["restoredIndexes"], h0, "a dropped index takes its share with it")

    def test_indexes_over_one_document_count_its_record_rows_once(self):
        """Red at the first cut (review of 2026-10-01): an index keeps its document's record rows by reference, and the
        restore road's document is the memo's shared object, so every restore of one leaf while the memo holds the document
        builds an index over the very same list; with two such indexes alive the gauge counted the rows twice. The atom rows
        are encoded per index, so those still count per index."""
        rows = [json.dumps({"syn": True, "s": {"uuid": "bbbbbbbb-2222-3333-4444-%012d" % k, "type": "assistant", "t": T0 + k}})
                for k in range(5)]
        recs = [["cccccccc-2222-3333-4444-%012d" % k, "k", "u", None, 1000 + k, T0 + k, 0, 300 + k, 0, None] for k in range(9)]
        doc = {"atoms": rows, "records": recs, "fsids": []}
        _collect_leftovers()
        h0 = _heap()["restoredIndexes"]
        a = em.LazyIndex(doc, SID % 8, str(self.proj / "web.jsonl"))
        b = em.LazyIndex(doc, SID % 8, str(self.proj / "web.jsonl"))
        self.assertIs(a.records, b.records, "both indexes hold the document's one list")
        h1 = _heap()["restoredIndexes"]
        self.assertEqual(_delta(h0, h1, "indexes", "rows", "records"), {"indexes": 2, "rows": 10, "records": 9})
        del a
        gc.collect()
        h2 = _heap()["restoredIndexes"]
        self.assertEqual(_delta(h0, h2, "indexes", "rows", "records"), {"indexes": 1, "rows": 5, "records": 9},
                         "the list stays counted while one index holds it")
        del b
        gc.collect()
        self.assertEqual(_heap()["restoredIndexes"], h0)


class Cheap(Base):
    READS = 4      # reads by index per list in one heap read or one fold, at most (a loop by index over these lists read one 6 times in a heap read)

    def test_no_gauge_iterates_a_records_list_or_an_index_rows(self):
        """Every new gauge costs O(entries): a length, a few identities, a number the holder keeps. The record cache's lists,
        an adapter's lists and an index's rows here raise if walked, and every gauge still answers. Two shapes of an
        adapter's list are held: one the record cache dropped (pinned, weighed by its length), and one whose leaf was
        appended through the reader and not yet folded, so the cache's list is a different object holding the adapter's
        records: the shared-list check runs on that pair at every read (review of 2026-10-01: a check that walked the two
        lists gave the same answers at O(records) and passed when only the dropped list was wrapped). The fold that follows
        runs the same check on its re-seat (_asm_reseat_src), with the reader's list it folds from wrapped too. Review of
        2026-10-02: the lists refused iteration alone, so a walk done in C (a slice and a map) and a loop by integer index
        passed; they now refuse the C routes (_NoWalk), and every heap read and the fold read each list by index at most
        READS times, against 6 in one heap read for a loop by index over these six-record lists."""
        whole = self.leaf("web", _recs(6))
        em.parse_session(whole)
        other = self.leaf("api", _recs(4))
        em._read_jsonl_entry(str(other))
        grown = self.leaf("tests", _recs(6))
        em.parse_session(grown)
        self.append(grown, _recs(2, start=6, parent="aaaaaaaa-2222-3333-4444-%012d" % 5))
        ent_g = em._read_jsonl_entry(str(grown))         # the append through the reader: a new list over the same records
        self.pop(whole)                                  # the adapter's list is pinned: the gauge measures it
        with em._ASM_LOCK:
            ads = {e.get("path"): e["ad"] for e in em._ASM_CACHE.values() if e.get("path") in (str(whole), str(grown))}
        ad, ad_g = ads[str(whole)], ads[str(grown)]
        ad._src[str(whole)] = _NoWalk(ad._src[str(whole)])
        held_g = ad_g._src[str(grown)]
        self.assertIsNot(held_g, ent_g[4], "the adapter holds the list before the append")
        self.assertIs(held_g[0], ent_g[4][0], "which shares its records with the cache's list")
        ad_g._src[str(grown)] = _NoWalk(held_g)          # the elements, and so their identities, are kept
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE[str(other)]
            em._JSONL_CACHE[str(other)] = ent[:4] + (_NoWalk(ent[4]),) + ent[5:]
            em._JSONL_CACHE[str(grown)] = ent_g[:4] + (_NoWalk(ent_g[4]),) + ent_g[5:]
        ix = em.LazyIndex({"atoms": [json.dumps({"syn": True, "s": {"uuid": "dddd", "t": T0}})] * 5, "records": [["dddd"]] * 3,
                           "fsids": []}, SID % 4, str(self.proj / "api.jsonl"))
        ix.rowb, ix.records = _NoWalk(ix.rowb), _NoWalk(ix.records)
        with em._JSONL_CACHE_LOCK:
            lists = [ad._src[str(whole)], ad_g._src[str(grown)], em._JSONL_CACHE[str(other)][4],
                     em._JSONL_CACHE[str(grown)][4], ix.rowb, ix.records]

        def bounded(what):
            reads = [l.reads for l in lists]
            self.assertLessEqual(max(reads), self.READS, "%s read a list by index %r times" % (what, reads))
            for l in lists:
                l.reads = 0
        h = _heap()
        bounded("the heap read")
        for k in NEW_KEYS:
            self.assertIsInstance(h[k], dict, "%s answered without a walk (None means the read raised): %r" % (k, h[k]))
        self.assertGreaterEqual(h["pinnedByAssembly"]["records"], 6)
        self.assertGreaterEqual(h["restoredIndexes"]["rows"], 5)
        modes = []
        em.parse_session(grown, asm_mode_out=modes)      # the gates take the cache's (wrapped) list; the fold re-seats on it
        bounded("the fold and its re-seat")
        self.assertEqual(modes, ["fold"], "the append folded, its re-seat's check walking neither list")
        with em._JSONL_CACHE_LOCK:
            cached = em._JSONL_CACHE[str(grown)][4]
        self.assertIs(ad_g._src[str(grown)], cached, "the adapter re-seated on the reader's list")
        h2 = _heap()
        bounded("the heap read after the fold")
        for k in NEW_KEYS:
            self.assertIsInstance(h2[k], dict, "%s after the fold: %r" % (k, h2[k]))
        self.assertEqual(h2["pinnedByAssembly"], h["pinnedByAssembly"], "the fold shared the records: nothing more pinned")
        del ix


class OutsideTheLock(Base):
    """What the gauges do with the record cache's entries runs after its lock, which every record-cache hit takes, so a hit
    never waits on a /perf read's checks or weighing. A spy records, at each call on the test's own thread, whether that
    thread holds the lock (_OwnedLock)."""

    def spied(self, name):
        lock, real, calls, me = _OwnedLock(em._JSONL_CACHE_LOCK), getattr(em, name), [], threading.get_ident()

        def spy(*a):
            if threading.get_ident() == me:
                calls.append(lock.mine())
            return real(*a)
        with mock.patch.object(em, "_JSONL_CACHE_LOCK", lock), mock.patch.object(em, name, spy):
            h = _heap()
        return h, calls

    def test_the_identity_checks_run_after_the_record_cache_lock(self):
        """Review of 2026-10-02: asm_pinned_records looks its files up under the lock and checks identities after it (1.30 ms
        under the lock with the checks inside, 0.13 ms with them after, at the caps), and putting the checks back under the
        lock left every test green."""
        leaf = self.leaf("web", _recs(6))
        em.parse_session(leaf)                           # an entry whose adapter holds the reader's list: one check per read
        h, calls = self.spied("_records_shared")
        self.assertIsInstance(h["pinnedByAssembly"], dict)
        self.assertTrue(calls, "the gauge checked the entry's list")
        self.assertNotIn(True, calls, "an identity check ran while the calling thread held the record cache's lock")

    def test_the_record_cache_gauge_weighs_its_entries_after_the_lock(self):
        """Red before the 2026-10-02 review's fold: record_cache_resident weighed every entry inside the lock (a median
        0.224 ms under it at the count cap, 0.016 ms once the entries are copied and weighed after). An entry is a tuple a
        read replaces and never changes in place, so the copy weighs the same."""
        leaf = self.leaf("web", _recs(6))
        em._read_jsonl_entry(str(leaf))
        h, calls = self.spied("_entry_weight")
        self.assertIsInstance(h["recordCacheResident"], dict)
        self.assertTrue(calls, "the gauge weighed the entries")
        self.assertNotIn(True, calls, "an entry was weighed while the calling thread held the record cache's lock")


class ReadByPath(unittest.TestCase):
    def setUp(self):
        self.keys = []

    def tearDown(self):
        with em._READ_BYTES_LOCK:
            for k in self.keys:
                em._READ_BYTES.pop(k, None)

    def plant(self, n):
        with em._READ_BYTES_LOCK:
            for i in range(n):
                k = "/TESTDIR/notes-api/read-%03d.jsonl" % i
                em._READ_BYTES[k] = 10 ** 12 + i         # larger than anything a real read left, and distinct
                self.keys.append(k)

    @staticmethod
    def full():
        rb = em.read_bytes_report()
        rb.pop("total")
        return rb

    def test_the_plain_reply_carries_the_fifty_largest_and_the_path_count(self):
        """Red on main: the plain reply carried one row per path ever read (within minutes of a boot, most of the snapshot's
        bytes), on every poll."""
        self.plant(120)
        ck = km._PerfStats().snapshot()["checkpoints"]
        full = self.full()
        self.assertEqual(em.READ_BY_PATH_TOP, 50)
        self.assertEqual(ck["readByPath"], dict(sorted(full.items(), key=lambda kv: -kv[1])[:50]), "the 50 largest, exact")
        self.assertEqual(ck["readPaths"], len(full), "how many paths the whole table has")
        self.assertEqual(ck["readBytes"], em.read_bytes_total(), "the total is the whole table's")

    def test_walk_fills_the_whole_table(self):
        self.plant(120)
        snap = km._perf_walks(km._PerfStats().snapshot())     # what GET /perf?walk=1 (romp perf --json --walk) runs
        full = self.full()
        self.assertEqual(snap["checkpoints"]["readByPath"], full)
        self.assertEqual(snap["checkpoints"]["readPaths"], len(full))
        json.dumps(snap)

    def test_paths_all_fills_the_whole_table_and_walks_nothing(self):
        """Red before review round two's fold (2026-10-01): the whole table rode ?walk=1 alone, so a reader that wanted the
        table (an hourly snapshot diffed for re-reads) paid the allocator's walk and the frozen count with it. GET
        /perf?paths=all (romp perf --json --paths) runs _perf_paths: the whole table, and the two walked slots stay null.
        The served route's own test is tests/test_perf_stats.py's."""
        self.plant(120)
        snap = km._PerfStats().snapshot()
        with mock.patch.object(km.sys, "getallocatedblocks", wraps=km.sys.getallocatedblocks) as blocks, \
             mock.patch.object(km.gc, "get_freeze_count", wraps=km.gc.get_freeze_count) as frozen:
            snap = km._perf_paths(snap)
        self.assertEqual((blocks.call_count, frozen.call_count), (0, 0), "neither whole-heap walk ran")
        full = self.full()
        self.assertEqual(snap["checkpoints"]["readByPath"], full)
        self.assertEqual(snap["checkpoints"]["readPaths"], len(full))
        self.assertIsNone(snap["heap"]["allocatedBlocks"])
        self.assertIsNone(snap["gc"]["frozen"])
        json.dumps(snap)

    def test_the_whole_table_and_its_total_come_from_one_copy(self):
        """Red before the 2026-10-02 review's fold: ?paths=all and ?walk=1 replaced the table from a second copy and
        left readBytes from the snapshot's first, so a read between the two put the rows ahead of the total beside them (141
        of 300 served snapshots on ?paths=all under reader load) and a sampler diffing the two read a negative remainder.
        Here a 7-byte read lands between the snapshot and the table's read, and the rows still sum to the total on both
        routes. Rows another module put in the table or took out of it without the total are measured first, as an offset.
        A 7-byte read of a checkpoint document lands there too, and documentBytes equals the checkpoint directory's rows on
        both routes (red by 7 before review round five's fold, when it stayed the snapshot's figure)."""
        path = "/TESTDIR/notes-api/one-copy.jsonl"
        ckpt = Path("/TESTDIR/notes-api/checkpoints")
        doc = str(ckpt / "one-copy.json")
        self.keys += [path, doc]
        with mock.patch.object(em, "_CKPT_DIR_FN", lambda: ckpt):
            for route in (km._perf_paths, km._perf_walks):
                with self.subTest(route=route.__name__):
                    with em._READ_BYTES_LOCK:
                        offset = em._READ_BYTES_TOTAL[0] - sum(em._READ_BYTES.values())
                    snap = km._PerfStats().snapshot()
                    em._count_read(path, 7)              # a read between the snapshot and the table's copy
                    em._count_read(doc, 7)               # and a checkpoint document's
                    ck = route(snap)["checkpoints"]
                    rows = sum(ck["readByPath"].values()) + offset
                    self.assertEqual(rows - ck["readBytes"], 0,
                                     "the rows ran %d bytes ahead of the total" % (rows - ck["readBytes"]))
                    docs = sum(n for p, n in ck["readByPath"].items() if p.startswith(str(ckpt) + os.sep))
                    self.assertEqual(docs - ck["documentBytes"], 0,
                                     "the checkpoint rows ran %d bytes ahead of documentBytes" % (docs - ck["documentBytes"]))

    def test_the_table_and_its_figures_come_from_one_acquisition(self):
        """Review round five (2026-10-02): the docstrings say the table, readBytes and documentBytes come from one acquisition
        of the reader's lock, and a total taken in a second one (em.read_bytes_total() in _perf_paths, or a second acquisition
        inside read_by_path) passed every other test. Here the module's lock is a wrapper whose every release on this thread
        lands a 7-byte read on one checkpoint row and the total, under the real lock, so whatever a second acquisition takes
        differs from the first's. The plain read's 50-row cut is lifted so its rows are the whole table. Round six: a second
        name bound to the same lock would take both acquisitions past the wrapper, so no other name in the event model may
        refer to the lock, and the wrapper's release must fire during each read under test. Round seven: a split that takes
        one of the two reads outside the wrapped lock lands no read between them, so every way of reading the table (its
        views, a copy, iteration) and the total (its index, iteration) is spied, each recording whether the calling thread
        holds the wrapper, and every read under test must read the table and read neither outside it (round eight: the
        spies first watched one spelling of each read)."""
        ckpt = Path("/TESTDIR/notes-api/checkpoints")
        path, prefix = str(ckpt / "one-acquisition.json"), str(ckpt) + os.sep
        self.keys.append(path)
        real, me = em._READ_BYTES_LOCK, threading.get_ident()
        self.assertEqual(sorted(k for k, v in vars(em).items() if v is real), ["_READ_BYTES_LOCK"],
                         "the reader's lock is reached through its one name, the one this test wraps")
        fired, quiet, seen = [0], [False], {"items": 0, "outside": []}

        def land():
            if threading.get_ident() == me:
                fired[0] += 1
                quiet[0] = True                           # the hook's own reads and writes are not the read under test
                try:
                    with real:
                        em._READ_BYTES[path] = em._READ_BYTES.get(path, 0) + 7
                        em._READ_BYTES_TOTAL[0] += 7
                finally:
                    quiet[0] = False
        wrapper = _OwnedLock(real, after_release=land)

        def note(what):                                   # a read on the test's thread, and whether it held the wrapper
            if threading.get_ident() == me and not quiet[0]:
                if what == "the table":
                    seen["items"] += 1
                if not wrapper.mine():
                    seen["outside"].append(what)

        class Table(dict):                                # every way of reading the table: its views, a copy, and
            def items(self):                              #  iteration (overriding __iter__ makes the dict constructor
                note("the table")                         #  read through keys)
                return super().items()

            def keys(self):
                note("the table")
                return super().keys()

            def values(self):
                note("the table")
                return super().values()

            def copy(self):
                note("the table")
                return super().copy()

            def __iter__(self):
                note("the table")
                return super().__iter__()

        class Total(list):                                # the total by its index or by iteration (unpacking, a sum)
            def __getitem__(self, i):
                note("the total")
                return super().__getitem__(i)

            def __iter__(self):
                note("the total")
                return super().__iter__()
        with real:
            offset = em._READ_BYTES_TOTAL[0] - sum(em._READ_BYTES.values())
            table, total = Table(em._READ_BYTES), Total(em._READ_BYTES_TOTAL)
        with mock.patch.object(em, "_CKPT_DIR_FN", lambda: ckpt), mock.patch.object(em, "READ_BY_PATH_TOP", None), \
             mock.patch.object(em, "_READ_BYTES", table), mock.patch.object(em, "_READ_BYTES_TOTAL", total), \
             mock.patch.object(em, "_READ_BYTES_LOCK", wrapper):
            for route in (None, km._perf_paths, km._perf_walks):
                name = route.__name__ if route else "the plain read"
                with self.subTest(read=name):
                    n0 = fired[0]
                    seen["items"], seen["outside"] = 0, []
                    snap = km._PerfStats().snapshot()
                    n1 = fired[0]
                    if route:                                 # a route's own read is the one under test
                        seen["items"], seen["outside"] = 0, []
                    ck = (route(snap) if route else snap)["checkpoints"]
                    self.assertGreater(seen["items"], 0, "%s: the read under test read the table" % name)
                    self.assertEqual(seen["outside"], [], "%s: read outside the wrapped lock: %s" % (name, seen["outside"]))
                    self.assertGreater(fired[0], n1 if route else n0,
                                       "%s: the read under test went through the wrapped lock" % name)
                    gap = sum(ck["readByPath"].values()) + offset - ck["readBytes"]
                    self.assertEqual(gap, 0, "%s: the rows ran %d bytes %s the total"
                                     % (name, abs(gap), "ahead of" if gap > 0 else "behind"))
                    gap = sum(n for p, n in ck["readByPath"].items() if p.startswith(prefix)) - ck["documentBytes"]
                    self.assertEqual(gap, 0, "%s: the checkpoint rows ran %d bytes %s documentBytes"
                                     % (name, abs(gap), "ahead of" if gap > 0 else "behind"))

    def test_a_failed_table_read_leaves_the_plain_rows_and_the_walk_still_answers(self):
        """Review round three (2026-10-01): ?walk=1 and ?paths=all read the whole table through _heap_read, its lookup inside
        the lambda. Beside an event model (or a test's stub) without read_by_path, both keep the plain read's 50 rows and
        readPaths, the walk still fills its two slots, and the failure is said once over three reads. A lookup evaluated
        before the guard would raise past it, and both routes would answer 500. Round six (2026-10-02): the same holds beside
        one without checkpoint_document_bytes, whose lookup once ran after the guard had returned and failed both routes,
        and documentBytes keeps its plain value with the other three. Round seven: one 7-byte read sits under the checkpoint
        directory, below the 50-row cut, so that plain value is not 0 and a failed read that zeroes the figure or
        recomputes it from the cut rows turns red."""
        self.plant(120)
        ckpt = Path("/TESTDIR/notes-api/checkpoints")
        doc = str(ckpt / "failed-read.json")
        with em._READ_BYTES_LOCK:
            em._READ_BYTES[doc] = 7                       # one document read, small enough to fall outside the cut
        self.keys.append(doc)
        with mock.patch.object(em, "_CKPT_DIR_FN", lambda: ckpt):   # the stub's copied functions read these globals too
            for missing in ("read_by_path", "checkpoint_document_bytes"):
                with self.subTest(missing=missing):
                    walk, paths = km._PerfStats().snapshot(), km._PerfStats().snapshot()
                    before = {id(s): (dict(s["checkpoints"]["readByPath"]), s["checkpoints"]["readPaths"],
                                      s["checkpoints"]["readBytes"], s["checkpoints"]["documentBytes"]) for s in (walk, paths)}
                    for rows, count, _, documents in before.values():
                        self.assertEqual(len(rows), 50)
                        self.assertGreaterEqual(count, 120)
                        self.assertEqual(documents, 7, "the premise: the plain read's document figure counts the planted row")
                    stub = types.SimpleNamespace(**{k: v for k, v in vars(em).items() if k != missing})   # _em_lacking's shape
                    said = set(km._HEAP_SAID)
                    km._HEAP_SAID.clear()
                    err = io.StringIO()
                    try:
                        with mock.patch.object(km, "em", stub), redirect_stderr(err):
                            km._perf_walks(walk)                       # GET /perf?walk=1, twice
                            km._perf_walks(walk)
                            km._perf_paths(paths)                      # GET /perf?paths=all
                    finally:
                        km._HEAP_SAID.clear()
                        km._HEAP_SAID.update(said)
                    for snap in (walk, paths):
                        rows, count, total, documents = before[id(snap)]
                        self.assertEqual(snap["checkpoints"]["readByPath"], rows, "the plain read's 50 rows stay")
                        self.assertEqual(snap["checkpoints"]["readPaths"], count, "and so does the whole table's size")
                        self.assertEqual(snap["checkpoints"]["readBytes"], total, "and the total taken with them")
                        self.assertEqual(snap["checkpoints"]["documentBytes"], documents, "and the document figure")
                        json.dumps(snap)
                    self.assertIsInstance(walk["heap"]["allocatedBlocks"], int, "the walk still answers")
                    self.assertIsInstance(walk["gc"]["frozen"], int, "the walk still answers")
                    self.assertIsNone(paths["heap"]["allocatedBlocks"], "?paths=all still walks nothing")
                    self.assertIsNone(paths["gc"]["frozen"], "?paths=all still walks nothing")
                    lines = [l for l in err.getvalue().splitlines() if l.startswith("perf: checkpoints.readByPath unavailable")]
                    self.assertEqual(len(lines), 1, "said once over three reads: %r" % err.getvalue())
                    self.assertIn("AttributeError", lines[0])
                    self.assertIn(missing, lines[0], "the line names what was missing")


class Counters(unittest.TestCase):
    COUNTERS = (("checkpoints", "documentBytes"), ("checkpoints", "readBytes"), ("asmCheckpoint", "hydratedBytes"),
                ("asmCheckpoint", "hydratedAtoms"), ("asmIndex", "restoredTurns"), ("asmIndex", "rowDecodes"))

    def test_the_counters_keep_their_names(self):
        snap = km._PerfStats().snapshot()
        for block, key in self.COUNTERS + (("recordCache", "bytes"), ("recordCache", "entries")):
            self.assertIn(key, snap[block], "%s.%s is read by scripts and tests: never renamed" % (block, key))

    def test_the_docstrings_call_the_running_counters_counters(self):
        for fn, keys in ((em.checkpoint_stats, ("documentBytes", "readBytes")),
                         (em.asm_checkpoint_stats, ("hydratedBytes", "hydratedAtoms")),
                         (em.asm_index_stats, ("restoredTurns", "rowDecodes"))):
            doc = " ".join((fn.__doc__ or "").split())
            for k in keys:
                self.assertRegex(doc, "`?%s`?[^.]*\\bcounters?\\b" % k, "%s's docstring calls %s a counter" % (fn.__name__, k))

    def test_the_reference_names_the_new_gauges_and_calls_the_counters_counters(self):
        doc = Path(HERE).parent.joinpath("docs", "reference.md").read_text()
        sec = doc[doc.index("## Kernel performance counters"):]
        sec = sec[:sec.index("\n## ", 5)]
        para = sec[sec.index("- `heap`:"):]
        para = para[:para.index("\n- `", 10)]
        for k in NEW_KEYS:
            self.assertIn("`%s`" % k, para, k)
        for _, k in self.COUNTERS:
            at = sec.index("`%s`" % k)
            self.assertIn("counter", " ".join(sec[at:at + 300].split()), "the reference glosses %s as a counter" % k)
        at = sec.index("`readByPath`")
        window = " ".join(sec[at:at + 400].split())
        self.assertIn("`readPaths`", window)
        self.assertIn("paths=all", window)
        self.assertIn("walk=1", window)

    def test_the_assembly_entries_own_size_is_said_to_be_a_count_alone(self):
        """Review of 2026-10-01: pinnedByAssembly weighs only the records an entry holds after the record cache let them go,
        and reads 0 in the steady state, while a whole entry's own atoms and lookup maps (about 0.9 to 1.1 KB per record of
        its leaf, measured) stay unsized. The kernel's heap docstring, the gauge's docstring and the reference say so, so a 0
        is never read as entries that cost nothing."""
        for name, text in (("_heap_stats", km._heap_stats.__doc__), ("asm_pinned_records", em.asm_pinned_records.__doc__)):
            flat = " ".join((text or "").split())
            self.assertIn("assemblyEntries", flat, name)
            self.assertIn("never says the entries are free", flat, name)
        doc = Path(HERE).parent.joinpath("docs", "reference.md").read_text()
        para = doc[doc.index("- `heap`:"):]
        para = " ".join(para[:para.index("\n- `", 10)].split())
        self.assertIn("the assembly entries' own atoms and lookup maps are still only the count `assemblyEntries`", para)
        self.assertIn("does not mean the entries are free", para)


if __name__ == "__main__":
    unittest.main()
