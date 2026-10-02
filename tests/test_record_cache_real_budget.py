#!/usr/bin/env python3
"""The record cache's byte budget counts real memory (2026-10-01).

The shared reader keeps each transcript's decoded records in one cache (_JSONL_CACHE), under a count cap and a byte
budget of half of MemTotal. Each entry weighed its FILE bytes, but decoded records take about 2.85 bytes of memory per
file byte: the reader's own lab figure is 2.86 live heap bytes per source byte, and the old kernel's 147 samples, 5
minutes to 145 hours of uptime (about six days), agree, fitting resident size in millions of kB = 3.44 + 2.85 x the
cache's file GB (R^2 0.92), about 2.92 bytes of memory per file byte. So on a 31 GiB machine the budget meant about
48 GB of decoded records and could not bind before the machine swapped: the cache's figure in the last sample, 3.72 GB
of file bytes, was under a quarter of it, while the kernel grew to 16.4 GB resident plus swap. The budget now compares
the held file bytes times the decoded multiple (RECORD_CACHE_DECODED_MULTIPLE; ROMP_RECORD_CACHE_DECODED_MULTIPLE
overrides it, and 1 restores the file-byte budget) with the same half of memory, so half of memory means half of real
memory. /perf keeps `bytes` in file bytes and adds `realBytes` and `decodedMultiple`.

Two things keep it from thrashing (the 2026-09-11 lesson: a budget under the files read every cycle misses on every
read). The floor stays 4 GiB of FILE bytes (the floor times the multiple, in memory), so wherever it binds (a machine
under about 23 GiB, or one without /proc/meminfo, such as macOS) the cache holds 4 GiB of file bytes, never less than
the floor has held since 2026-09-11. And budgetRereads counts an evicted path read back, the direct signal that the
budget has started to thrash once it binds; budgetRereadBytes counts what those reads decoded again, a tail restored
first and the whole read that upgrades it both.

Pinned here: eviction by real weight under a small budget; a working set of 3.72 GB of file bytes under this machine's
default evicts nothing, and the default binds only past budget / multiple; the floor in file bytes, with and without
procfs, on the real read path too; budgetRereads stays zero under cold churn past a hot set that fits and equals the
evictions when the hot set does not fit, and a re-read that comes back as a tail counts its upgrade's bytes; the
multiple's default (on a plain load too), its override, the values refused, and a multiple of 1 restoring the old
budget on a fresh load; both environment variables end to end, and both test runners unsetting them; the /perf fields,
the judge child's gauge list and the reference. Review round one (2026-10-02) added: two named mixes' measured memory
per file byte; a budget eviction releasing the chat's assembly entry over the evicted file, outside the cache lock; the
multiple's cap at 1000; a mid-read eviction not counted as a re-read; and a tail evicted again each cycle. Round two
(2026-10-02) added: a restored tail evicted while a whole read upgrades it; the release's population (a prior lineage
file alone, a skip-marked file, two chats' leaves in one insert) and its count, budgetReleased; and a path evicted during
its own read keeping its chat's entry. Round three (2026-10-02) added: that entry folding the append at the chat's next
build, and a read that raises mid-scan leaving no in-flight mark. Synthetic files and synthetic cache entries only."""
import ast
import contextlib
import gc
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model_real_budget", os.path.join(BIN, "romp-event-model"))

MEASURED = 2.85                                   # the constant's figure: the lab's 2.86 heap bytes per file byte, rounded
#                                                   to the nearest 0.05 (the six-day fit's 2.85 is millions of kB per file
#                                                   GB, about 2.92 bytes per byte)
MiB = 1024 ** 2
GiB = 1024 ** 3
BOX_MEMINFO = "MemTotal:       32860372 kB\n"     # a 31 GiB machine: the one the six-day series ran on
WORKING_SET_FILE_BYTES = 3_720_000_000            # a snapshot, not a ceiling: the old kernel's cache at 145 hours of uptime;
#                                                   the kernel at 5f3569d39 held 3.4 GB at 12.2 hours and 4.0 GB at 15.4
#                                                   hours, its count cap full since 12.5 (2026-10-01)
FLOOR_FILE_BYTES = 4 * GiB                        # the default's floor, in FILE bytes
SID = "11111111-2222-3333-4444-0000b0d9e7a1"     # this module's own placeholder session ids
SID2 = "11111111-2222-3333-4444-0000b0d9e7a2"
NOW = 1781200000
FILLER = ("the search endpoint ranks notes by recency, the tokenizer splits on punctuation, "
          "and the pagination cursor carries the last note id so a reload resumes where it stopped")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _conversation(path, turns, tag=""):
    """A synthetic leaf of `turns` typed prompts and replies in the notes-api demo world (uuids prefixed with `tag`)."""
    parent = None
    with open(path, "w") as f:
        for k in range(turns):
            t, u, a = NOW - 200000 + 600 * k, "%su%05d" % (tag, k), "%sa%05d" % (tag, k)
            ask = {"role": "user", "content": "step %d: %s" % (k, FILLER)}
            said = {"id": "msg_%05d" % k, "role": "assistant", "model": "claude-test-1", "stop_reason": "end_turn",
                    "content": [{"type": "text", "text": "Step %d is in: %s." % (k, FILLER)}]}
            f.write(json.dumps({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed",
                                "cwd": "/w/notes-api", "message": ask}) + "\n")
            f.write(json.dumps({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t + 30),
                                "cwd": "/w/notes-api", "message": said}) + "\n")
            parent = a
    return path


def _traced(fn):
    """(result, live): the bytes fn() left live under tracemalloc, as a delta from what was held when it started (the
    harness of tests/test_reader_stream_peak.py); a tracer already running is used and left running."""
    gc.collect()
    tracing = tracemalloc.is_tracing()
    if not tracing:
        tracemalloc.start()
    try:
        held = tracemalloc.get_traced_memory()[0]
        out = fn()
        gc.collect()
        cur = tracemalloc.get_traced_memory()[0]
    finally:
        if not tracing:
            tracemalloc.stop()
    return out, cur - held


def _entry(size):
    """A synthetic WHOLE entry (base 0) of `size` file bytes: the weight is a pure function of the tuple, so a test can
    stand a 286 MB leaf in the cache without a file."""
    return (0.0, int(size), int(size), b"", [], 0, 0, None)


def _stat(key):
    return em._RECORD_CACHE_STATS.get(key, 0)


class _CacheCase(unittest.TestCase):
    """A private directory, the multiple pinned at the measured figure whatever the runner's environment says, an empty
    cache and an empty record of budget-evicted paths; the budget and the multiple restored after."""

    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="real-budget-")
        self.addCleanup(self._td.cleanup)
        self.dir = self._td.name
        self._budget = em._JSONL_CACHE_BUDGET_BYTES
        self._multiple = getattr(em, "_RECORD_CACHE_MULTIPLE", None)
        em._RECORD_CACHE_MULTIPLE = MEASURED
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        getattr(em, "_BUDGET_EVICTED", {}).clear()
        self.ev0 = _stat("budgetEvictions")

    def tearDown(self):
        em._JSONL_CACHE_BUDGET_BYTES = self._budget
        if self._multiple is None:
            vars(em).pop("_RECORD_CACHE_MULTIPLE", None)
        else:
            em._RECORD_CACHE_MULTIPLE = self._multiple
        em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        getattr(em, "_BUDGET_EVICTED", {}).clear()

    def _evicted(self):
        return _stat("budgetEvictions") - self.ev0

    def _file(self, name, n=40):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            for i in range(n):
                f.write(json.dumps({"uuid": "11111111-2222-3333-4444-%012d" % i, "type": "user"}) + "\n")
        return path

    def _leaf(self, name, nbytes):
        """A synthetic transcript of about `nbytes` (whole records of about 960 bytes each)."""
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            n = i = 0
            while n < nbytes:
                line = json.dumps({"uuid": "11111111-2222-3333-4444-%012d" % i, "type": "user", "text": "x" * 900}) + "\n"
                f.write(line); n += len(line); i += 1
        return path

    def _insert(self, path, size):
        with em._JSONL_CACHE_LOCK:
            em._cache_insert_locked(path, _entry(size))


class RealUnitBudget(_CacheCase):
    def test_the_budget_evicts_by_real_weight_not_file_bytes(self):
        files = [self._file("notes-api-%d.jsonl" % i) for i in range(4)]
        one = os.path.getsize(files[0])
        self.assertEqual({os.path.getsize(p) for p in files}, {one}, "four files of one size")
        em._JSONL_CACHE_BUDGET_BYTES = int(2.5 * one * MEASURED)      # room for two such files weighed at the multiple;
        for p in files:                                               #  seven by file bytes
            em._read_jsonl_incremental(p)
        self.assertEqual(list(em._JSONL_CACHE), files[2:], "the two most recently used stay: each file weighed at the "
                         "multiple, 2.85 times its size "
                         "(the file-byte budget held all four, %d bytes under a %d budget)" % (4 * one, em._JSONL_CACHE_BUDGET_BYTES))
        self.assertEqual(self._evicted(), 2, "two budget evictions, oldest first")
        st = em.record_cache_stats()
        self.assertEqual(st["bytes"], 2 * one, "the ledger stays in file bytes")
        self.assertLessEqual(st["realBytes"], st["budgetBytes"], "the cache's real weight sits under the budget")

    def test_a_large_working_set_does_not_evict_under_this_machines_default(self):
        budget = em._record_cache_default_budget_bytes(BOX_MEMINFO)
        self.assertEqual(budget, 16_824_510_464, "half of this machine's memory")
        em._JSONL_CACHE_BUDGET_BYTES = budget
        subagents = [("notes-api/subagents/agent-%03d.jsonl" % i, 650_000) for i in range(100)]
        largest = 286_000_000
        rest = (WORKING_SET_FILE_BYTES - largest - sum(s for _, s in subagents)) // 32
        leaves = [("notes-api/leaf-00.jsonl", largest)] + [("notes-api/leaf-%02d.jsonl" % i, rest) for i in range(1, 33)]
        for path, size in leaves + subagents:
            self._insert(path, size)
        st = em.record_cache_stats()
        self.assertEqual(self._evicted(), 0, "33 leaves and 100 subagent files, %d file bytes: nothing evicted" % st["bytes"])
        self.assertEqual(st["entries"], 133)
        self.assertAlmostEqual(st["bytes"], WORKING_SET_FILE_BYTES, delta=64)
        self.assertEqual(st["realBytes"], int(st["bytes"] * MEASURED))
        self.assertLess(st["realBytes"], budget, "about 10.6 GB decoded under 16.8 GB")

    def test_the_default_binds_only_past_budget_over_the_multiple(self):
        budget = em._record_cache_default_budget_bytes(BOX_MEMINFO)
        em._JSONL_CACHE_BUDGET_BYTES = budget
        leaf = 100_000_000
        fit = int(budget / MEASURED) // leaf
        self.assertEqual(fit, 59, "the budget binds at about 5.9 GB of file bytes on this machine")
        for i in range(fit):
            self._insert("notes-api/leaf-%02d.jsonl" % i, leaf)
        self.assertEqual(self._evicted(), 0, "59 leaves of 100 MB fit")
        self._insert("notes-api/leaf-%02d.jsonl" % fit, leaf)
        self.assertEqual(self._evicted(), 1, "the 60th evicts one (the file-byte budget would have held 168)")
        self.assertNotIn("notes-api/leaf-00.jsonl", em._JSONL_CACHE, "the least recently used went")
        self.assertEqual(len(em._JSONL_CACHE), fit)
        self.assertLessEqual(em.record_cache_stats()["realBytes"], budget)

    def test_an_entry_larger_than_the_real_budget_still_inserts_alone(self):
        em._JSONL_CACHE_BUDGET_BYTES = 10 * MiB
        self._insert("notes-api/web.jsonl", 1 * MiB)
        self._insert("notes-api/api.jsonl", 5 * MiB)                  # 14.25 MiB decoded, over 10 MiB on its own
        self.assertEqual(list(em._JSONL_CACHE), ["notes-api/api.jsonl"], "a leaf is never refused; the budget then holds it alone")

    def test_perf_reports_file_bytes_beside_real_bytes(self):
        before = em.record_cache_stats()             # the counters are process-wide: other tests' evictions stand in them
        for i in range(3):
            self._insert("notes-api/tests-%d.jsonl" % i, 1000)
        st = em.record_cache_stats()
        self.assertEqual(st["bytes"], 3000, "bytes stays the held FILE bytes: the kernel's samples and the six-day fit read it")
        self.assertEqual(st["realBytes"], int(3000 * MEASURED), "realBytes: the same entries decoded, the budget's unit")
        self.assertEqual(st["decodedMultiple"], MEASURED, "the multiple in force")
        self.assertEqual(st["budgetBytes"], em._JSONL_CACHE_BUDGET_BYTES)
        self.assertEqual((st["budgetRereads"], st["budgetRereadBytes"]), (before["budgetRereads"], before["budgetRereadBytes"]),
                         "the thrash counters ride the same block, unmoved by three first reads (a bare unittest run orders the "
                         "re-read tests before this one)")


class DefaultWithoutProcfs(_CacheCase):
    """Where /proc/meminfo cannot be read (macOS, a container without procfs) the default is the floor, as on main; and the
    floor is 4 GiB of FILE bytes wherever it binds. Review find, 2026-10-01: a floor weighed decoded admitted 1.40 GiB of
    transcripts on every such machine, near the 1 GiB that thrashed, and a machine reading 2 GB of live leaves every cycle
    re-read all of them every cycle."""

    def _default(self):
        """The default budget on this machine with /proc/meminfo unreadable: the floor, whatever memory the machine has,
        since nothing else is read (main's behavior on macOS)."""
        real_open = open

        def no_procfs(p, *a, **k):
            if str(p) == "/proc/meminfo":
                raise FileNotFoundError(p)
            return real_open(p, *a, **k)
        with mock.patch.object(em, "open", no_procfs, create=True):
            return em._record_cache_default_budget_bytes()

    def test_the_floor_is_four_gib_of_file_bytes_wherever_it_binds(self):
        floor = int(FLOOR_FILE_BYTES * MEASURED)
        for label, got in (("a machine without procfs, whatever its memory", self._default()),
                           ("a meminfo with no MemTotal line", em._record_cache_default_budget_bytes("garbage")),
                           ("an 8 GB machine with procfs", em._record_cache_default_budget_bytes("MemTotal:  8000000 kB\n"))):
            self.assertEqual(got, floor, label)
            self.assertGreaterEqual(got / MEASURED, FLOOR_FILE_BYTES - 1, "%s holds 4 GiB of file bytes, as before 2026-10-01" % label)

    def test_a_small_machine_reading_its_leaves_every_cycle_does_not_thrash(self):
        """The real read path, sizes scaled by 1/1000: a small machine, or any machine without procfs, reads 2.0 GB of live
        leaves every cycle under the floor. Under a floor of 4 GiB of memory (1.40 GiB of file bytes) every read after the
        first cycle missed."""
        scale = 1000
        em._JSONL_CACHE_BUDGET_BYTES = self._default() // scale
        leaves = [self._leaf("notes-api-%02d.jsonl" % i, 100_000) for i in range(20)]
        total = sum(os.path.getsize(p) for p in leaves)
        rows = []
        for _ in range(3):
            r0, e0, rr0 = em.read_bytes_total(), _stat("budgetEvictions"), _stat("budgetRereads")
            for p in leaves:
                em._read_jsonl_incremental(p)
            rows.append((em.read_bytes_total() - r0, _stat("budgetEvictions") - e0, _stat("budgetRereads") - rr0))
        self.assertGreaterEqual(rows[0][0], total, "the first cycle reads every leaf")
        self.assertEqual([rows[0][1:]] + rows[1:], [(0, 0), (0, 0, 0), (0, 0, 0)],
                         "%d file bytes of leaves (scaled): read once, then served; nothing evicted, nothing read back" % total)


class BudgetRereads(_CacheCase):
    """budgetRereads counts inserts of a path the byte budget had evicted: the event a thrash is made of. Under cold churn
    past a hot set that fits it stays zero while the budget evicts; when the files read every cycle outgrow the budget,
    the LRU misses on every read and the counter equals the evictions."""

    def test_cold_churn_past_a_hot_set_that_fits_reads_nothing_back(self):
        hot = [self._file("notes-api-hot-%d.jsonl" % i) for i in range(3)]
        one = os.path.getsize(hot[0])
        em._JSONL_CACHE_BUDGET_BYTES = int(5.5 * one * MEASURED)      # room for five such files decoded
        rr0 = _stat("budgetRereads")
        hot_read = lambda: sum(em._READ_BYTES.get(p, 0) for p in hot)
        hot0 = hot_read()
        for c in range(6):
            for p in hot:
                em._read_jsonl_incremental(p)
            for j in range(2):
                em._read_jsonl_incremental(self._file("notes-api-cold-%d-%d.jsonl" % (c, j)))
            if c == 0:
                first = hot_read() - hot0
        self.assertEqual(self._evicted(), 10, "the budget binds: two cold files go each cycle after the first")
        self.assertEqual(_stat("budgetRereads") - rr0, 0, "no evicted path came back")
        self.assertGreaterEqual(first, 3 * one)
        self.assertEqual(hot_read() - hot0, first, "the hot files were read once, then served from the cache")

    def test_a_hot_set_past_the_budget_misses_on_every_read_and_the_counter_says_so(self):
        hot = [self._file("notes-api-hot-%d.jsonl" % i) for i in range(6)]
        one = os.path.getsize(hot[0])
        em._JSONL_CACHE_BUDGET_BYTES = int(4.5 * one * MEASURED)      # room for four; six are read every cycle
        keys = ("budgetRereads", "budgetRereadBytes", "budgetEvictions")
        rows = []
        for _ in range(3):
            before = [_stat(k) for k in keys] + [em.read_bytes_total()]
            for p in hot:
                em._read_jsonl_incremental(p)
            after = [_stat(k) for k in keys] + [em.read_bytes_total()]
            rows.append(tuple(a - b for a, b in zip(after, before)))
        first = rows[0][3]
        self.assertGreaterEqual(first, 6 * one)
        self.assertEqual(rows[0][:3], (0, 0, 2), "the first cycle: six first reads, two evictions")
        self.assertEqual(rows[1:], [(6, 6 * one, 6, first)] * 2, "then every read misses: six read back, six evicted, every "
                         "byte read again; an LRU under cyclic reads larger than itself does not degrade partway")

    def test_it_remembers_the_last_count_cap_of_evicted_paths(self):
        cap = em._JSONL_CACHE_MAX
        em._JSONL_CACHE_BUDGET_BYTES = int(1000 * MEASURED)           # room for one 1000-byte entry decoded
        rr0 = _stat("budgetRereads")
        for i in range(cap + 500):
            self._insert("notes-api/agent-%05d.jsonl" % i, 1000)
        self.assertEqual(self._evicted(), cap + 499)
        self.assertEqual(len(em._BUDGET_EVICTED), cap, "the last countCap evicted paths, no more")
        self._insert("notes-api/agent-00000.jsonl", 1000)             # evicted longest ago, forgotten
        self.assertEqual(_stat("budgetRereads") - rr0, 0)
        self._insert("notes-api/agent-%05d.jsonl" % (cap + 498), 1000)  # evicted just before it: counted
        self.assertEqual((_stat("budgetRereads") - rr0, len(em._BUDGET_EVICTED)), (1, cap))
        self._insert("notes-api/agent-%05d.jsonl" % (cap + 498), 2000)  # the same file grown: an append re-inserts it
        self.assertEqual(_stat("budgetRereads") - rr0, 1, "a path read back counts once; its appends after that do not")

    def test_a_reread_that_comes_back_as_a_tail_counts_the_whole_read_that_upgrades_it(self):
        """The first reader back after a budget eviction is often a tail reader (the assembly gate, the pusher), which
        restores the tail from the checkpoint; the judges' whole read then upgrades it. One re-read, and its bytes are the
        tail's and the whole file's: before, the whole read was not counted (the path had left the record at the tail)."""
        def tail(size, first=6000):              # a TAIL entry (base 5): it weighs the bytes past its first held record
            return (0.0, size, size, b"", [{"uuid": "11111111-2222-3333-4444-000000000001"}], 5, 0, [first])
        p, q = "notes-api/web.jsonl", "notes-api/api.jsonl"
        em._JSONL_CACHE_BUDGET_BYTES = int(15000 * MEASURED)          # room for one 10,000-byte entry decoded, not two
        self._insert(p, 10000); self._insert(q, 10000)
        self.assertEqual((self._evicted(), p in em._JSONL_CACHE), (1, False), "the budget evicted the leaf")
        em._JSONL_CACHE_BUDGET_BYTES = 10 ** 12                       # nothing more is evicted below
        rr0, rb0 = _stat("budgetRereads"), _stat("budgetRereadBytes")
        delta = lambda: (_stat("budgetRereads") - rr0, _stat("budgetRereadBytes") - rb0)
        with em._JSONL_CACHE_LOCK:
            em._cache_insert_locked(p, tail(10000))                   # a tail restore comes back first: 4,000 bytes
        self.assertEqual(delta(), (1, 4000))
        with em._JSONL_CACHE_LOCK:
            em._cache_insert_locked(p, tail(11000))                   # that tail grows by an append: not a re-read
        self.assertEqual(delta(), (1, 4000))
        self._insert(p, 11000)                                        # the whole read upgrading it: the rest of the cost
        self.assertEqual(delta(), (1, 15000), "one re-read: the tail's 4,000 bytes and the whole file's 11,000")
        self._insert(p, 12000)                                        # an append to the whole entry: not a re-read
        self.assertEqual(delta(), (1, 15000))
        self.assertNotIn(p, em._BUDGET_EVICTED)

    def test_a_tail_that_left_by_another_road_does_not_lend_its_mark(self):
        """A path back as a tail that then leaves by the count cap or a drop is no longer the budget's: its next whole read
        is that road's miss, not part of the budget's re-read."""
        p = "notes-api/tests.jsonl"
        em._JSONL_CACHE_BUDGET_BYTES = int(15000 * MEASURED)
        self._insert(p, 10000); self._insert("notes-api/web.jsonl", 10000)
        em._JSONL_CACHE_BUDGET_BYTES = 10 ** 12
        with em._JSONL_CACHE_LOCK:
            em._cache_insert_locked(p, (0.0, 10000, 10000, b"", [{"uuid": "11111111-2222-3333-4444-000000000002"}], 5, 0, [6000]))
            em._cache_pop_locked(p)                                   # gone by another road (a drop)
        rr0, rb0 = _stat("budgetRereads"), _stat("budgetRereadBytes")
        self._insert(p, 10000)
        self.assertEqual((_stat("budgetRereads") - rr0, _stat("budgetRereadBytes") - rb0), (0, 0))
        self.assertNotIn(p, em._BUDGET_EVICTED)

    def test_a_path_the_count_cap_evicted_is_not_a_budget_reread(self):
        em._JSONL_CACHE_BUDGET_BYTES = 10 ** 12                       # the byte budget never binds here
        rr0 = _stat("budgetRereads")
        with mock.patch.object(em, "_JSONL_CACHE_MAX", 4):
            for i in range(6):
                self._insert("notes-api/states-%d.log" % i, 1000)
            self.assertNotIn("notes-api/states-0.log", em._JSONL_CACHE, "the count cap evicted the oldest")
            self._insert("notes-api/states-0.log", 1000)
        self.assertEqual((self._evicted(), _stat("budgetRereads") - rr0), (0, 0), "the count cap's misses are not the budget's")

    @staticmethod
    def _tail(size, first):
        """A TAIL entry (base 5) of a `size`-byte file whose first held record sits at `first`: it weighs size - first."""
        return (0.0, size, size, b"", [{"uuid": "11111111-2222-3333-4444-000000000003"}], 5, 0, [first])

    def test_a_tail_evicted_again_each_cycle_is_read_back_each_cycle(self):
        """A path back as a tail and evicted by the budget again is a fresh eviction, so its next return is a fresh re-read
        (review find, 2026-10-02: with the eviction keeping the "tail" mark, every other re-read went uncounted). Two paths
        under a one-entry budget, the tail evicting the whole file and the whole file evicting the tail, every cycle."""
        p, q = "notes-api/web.jsonl", "notes-api/api.jsonl"
        em._JSONL_CACHE_BUDGET_BYTES = int(15000 * MEASURED)          # room for one 10,000-byte entry, not two
        self._insert(p, 10000); self._insert(q, 10000)                # p evicted
        keys = ("budgetEvictions", "budgetRereads")
        rows = []
        for _ in range(4):
            before = [_stat(k) for k in keys]
            with em._JSONL_CACHE_LOCK:
                em._cache_insert_locked(p, self._tail(10000, 4000))   # p back as a tail of 6,000 bytes: q goes
            self._insert(q, 10000)                                    # q back whole: the tail goes
            rows.append(tuple(_stat(k) - b for k, b in zip(keys, before)))
        self.assertEqual(rows, [(2, 2)] * 4, "every cycle: two evictions, two read back")

    def test_a_tail_evicted_again_is_the_newest_evicted_path(self):
        """The eviction of a tail moves its path to the newest end of the record (pop, then set), so the 1024-path memory
        forgets the oldest evictions first. A third path evicted between the tail's return and its next eviction stands
        before it."""
        p = "notes-api/web.jsonl"
        em._JSONL_CACHE_BUDGET_BYTES = int(25000 * MEASURED)          # room for two 10,000-byte entries, not three
        self._insert(p, 10000); self._insert("notes-api/api.jsonl", 10000); self._insert("notes-api/tests.jsonl", 10000)
        with em._JSONL_CACHE_LOCK:
            em._cache_insert_locked(p, self._tail(10000, 4000))       # p back as a tail: api.jsonl goes
        self.assertEqual(em._BUDGET_EVICTED.get(p), "tail")
        self._insert("notes-api/states.log", 10000)                   # tests.jsonl goes, after the tail's return
        self.assertEqual(list(em._BUDGET_EVICTED)[-1], "notes-api/tests.jsonl")
        self._insert("notes-api/messages.jsonl", 10000)               # the tail goes
        self.assertEqual(list(em._BUDGET_EVICTED)[-1:], [p], "the tail's eviction is the newest: %r" % list(em._BUDGET_EVICTED))
        self.assertIs(em._BUDGET_EVICTED[p], True)

    def test_an_eviction_while_a_read_runs_from_its_cached_entry_is_not_a_reread(self):
        """The reader lets the cache lock go while it decodes, so another thread's insert can evict the path mid-read; the
        read's own insert then found a fresh eviction mark and counted a re-read of the whole entry, though it read only
        the append (review find, 2026-10-02). The read keeps the mark it began with; a read that begins uncached after
        an eviction still counts, a restore from a checkpoint's cut included."""
        a, b = self._file("notes-api-web.jsonl"), self._file("notes-api-api.jsonl")
        em._JSONL_CACHE_BUDGET_BYTES = 10 ** 12
        em._read_jsonl_incremental(a)                                 # cached whole
        with open(a, "a") as f:
            f.write(json.dumps({"uuid": "11111111-2222-3333-4444-000000000099", "type": "user"}) + "\n")
        em._JSONL_CACHE_BUDGET_BYTES = int(1.5 * os.path.getsize(a) * MEASURED)   # room for one of the two
        real_scan = em._scan_jsonl_stream

        def scan_beside_an_insert(*args, **kw):                       # a's read of its append, from the cached entry
            em._scan_jsonl_stream = real_scan
            em._read_jsonl_entry(b)                                   # the other insert, mid-read: it evicts a
            return real_scan(*args, **kw)
        keys = ("budgetEvictions", "budgetRereads")
        before = [_stat(k) for k in keys]
        with mock.patch.object(em, "_scan_jsonl_stream", scan_beside_an_insert):
            em._read_jsonl_incremental(a)
        self.assertEqual([_stat(k) - x for k, x in zip(keys, before)], [2, 0],
                         "a evicted mid-read, then b at a's insert; a read back nothing for its eviction")
        self.assertEqual((a in em._JSONL_CACHE, a in em._BUDGET_EVICTED), (True, False))
        em._read_jsonl_incremental(b)                                 # b begins uncached: a re-read, and a goes
        self.assertEqual([_stat(k) - x for k, x in zip(keys, before)], [3, 1])
        data = open(a, "rb").read()
        cut = [i + 1 for i, ch in enumerate(data) if ch == 0x0A][9]   # the offset of a's eleventh record
        ent = em._read_jsonl_entry(a, tail_ok=True, tail_from=(cut, 10, data[cut - 16:cut]))
        self.assertEqual(ent[5], 10, "a restored from the cut, a tail entry")
        self.assertEqual([_stat(k) - x for k, x in zip(keys, before)], [4, 2], "the restore is a re-read; b goes")
        # the restored-tail half (review round two): a whole read upgrading that tail, the tail evicted mid-scan by a third
        # file's first read; the read keeps the "tail" mark and the entry it began from, so the upgrade adds the whole
        # file's bytes to the same re-read and counts no new one
        self.assertEqual(em._BUDGET_EVICTED.get(a), "tail", "armed: a is back as the tail of a budget re-read")
        c, mid = self._file("notes-api-tests.jsonl"), []

        def scan_beside_a_first_read(*args, **kw):                    # a's whole read upgrading its tail
            em._scan_jsonl_stream = real_scan
            em._read_jsonl_entry(c)                                   # c's first read, mid-scan: it evicts the tail
            mid.append(a in em._JSONL_CACHE)
            return real_scan(*args, **kw)
        keys = ("budgetEvictions", "budgetRereads", "budgetRereadBytes")
        before = [_stat(k) for k in keys]
        with mock.patch.object(em, "_scan_jsonl_stream", scan_beside_a_first_read):
            em._read_jsonl_incremental(a)
        self.assertEqual(mid, [False], "the tail evicted mid-read")
        self.assertEqual([_stat(k) - x for k, x in zip(keys, before)], [2, 0, os.path.getsize(a)],
                         "the tail, then c at a's insert; no new re-read, and the file's size added to the one under way")
        self.assertEqual((em._JSONL_CACHE[a][5], a in em._BUDGET_EVICTED), (0, False))


class MeasuredMixes(_CacheCase):
    """What realBytes stands for, measured: two named synthetic mixes decoded through the reader under tracemalloc, live
    bytes per file byte within 35% of the figure recorded on Python 3.12, a band that covers the interpreters CI runs
    (one shape measured 6.48 on 3.12 and 3.13, 6.97 on 3.11 and 7.71 on 3.10). It pins each mix's ratio, not that 2.85
    fits production: content moves the ratio from 1.44 to 7.7 by shape, and the constant estimates the bulk, whole live
    leaves. A reader keeping a second decoded copy doubles the figure and fails here; the budget's other tests count file
    bytes and passed it (review find, 2026-10-02)."""
    BAND = 0.35
    RECORDED = {"metadata rows": 6.87,            # _file's rows: a uuid and a type, nothing else
                "text rows": 1.45}                # _leaf's rows: a uuid, a type and 900 characters of text

    def test_two_mixes_decode_to_their_recorded_bytes_per_file_byte(self):
        em._JSONL_CACHE_BUDGET_BYTES = 10 ** 12
        for name, path in (("metadata rows", self._file("notes-api-meta.jsonl", n=20000)),
                           ("text rows", self._leaf("notes-api-text.jsonl", 2_000_000))):
            recs, live = _traced(lambda: em._read_jsonl_incremental(path))
            size, want = os.path.getsize(path), self.RECORDED[name]
            got = live / size
            print("%s: %d records, %d file bytes, %.2f bytes of memory per file byte (recorded %.2f; cache realBytes "
                  "weighs %.2f)" % (name, len(recs), size, got, want, em.record_cache_stats()["decodedMultiple"]))
            self.assertGreater(len(recs), 1000, name)
            self.assertGreaterEqual(got, want * (1 - self.BAND), "%s: %.2f bytes per file byte" % (name, got))
            self.assertLessEqual(got, want * (1 + self.BAND), "%s: %.2f bytes per file byte" % (name, got))


class BudgetReleasesAssemblyEntries(_CacheCase):
    """A budget eviction releases every assembly entry (a chat's copy) that read the evicted file (the owner's call,
    2026-10-02): the entry's adapter holds the very records the cache let go, so before, the eviction freed 0.4% of the
    leaf's memory. The release runs after the reader lets the cache lock go: it takes the assembly lock and then the
    materialization lock, one at a time, so it never waits on an assembly lock under the cache lock."""

    def setUp(self):
        super().setUp()
        self._ckpt = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None                    # checkpoints off: the rebuild after a release is the whole parse
        em._ASM_CACHE.clear()
        em._READ_IN_FLIGHT.clear()                # a mark left by a read that raised elsewhere would hide a release here

    def tearDown(self):
        em._ASM_CACHE.clear()
        em._READ_IN_FLIGHT.clear()
        em._CKPT_DIR_FN = self._ckpt
        super().tearDown()

    def _parse(self, leaf, sid=SID, cands=None):
        modes = []
        tree = em.parse_session(leaf, rompuuid=sid, name="web", dir="/w/notes-api", candidate_files=list(cands or [leaf]),
                                states=None, postal_log=[], now=NOW, asm_mode_out=modes)
        return tree, modes

    def _held(self, leaf):
        return [e for e in em._ASM_CACHE.values() if e.get("path") == leaf]

    def test_an_eviction_releases_the_chats_copy_and_frees_its_records(self):
        """Measured with no parsed tree held (the test drops its own before the eviction): an idle session's tree in the
        judges' parse store shares the records' strings, so in the kernel their text stays until the store replaces or
        drops that slot, a shown tab's build memos and the live-merge memo of the tree go; what this frees is the
        records' lists and the entry's copy."""
        leaf = _conversation(os.path.join(self.dir, SID + ".jsonl"), 600)
        other = self._file("notes-api-states.jsonl")
        size = os.path.getsize(leaf)
        tracing = tracemalloc.is_tracing()
        if not tracing:
            tracemalloc.start()
        try:
            tree, modes = self._parse(leaf)
            want = json.dumps(em.plain_tree(tree), sort_keys=True, default=lambda o: "<unserializable>")
            del tree
            self.assertEqual((modes[-1:], len(self._held(leaf))), (["full"], 1), "a whole parse installed the chat's entry")
            em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))   # the other file's read evicts the rest
            r0 = _stat("budgetReleased")
            _, delta = _traced(lambda: em._read_jsonl_incremental(other))
        finally:
            if not tracing:
                tracemalloc.stop()
        print("a %d-byte leaf evicted by the budget behind a chat's entry: %d bytes freed" % (size, -delta))
        self.assertNotIn(leaf, em._JSONL_CACHE)
        self.assertEqual(self._held(leaf), [], "the chat's assembly entry went with the evicted leaf")
        self.assertEqual(_stat("budgetReleased") - r0, 1, "counted under budgetReleased")
        self.assertGreater(-delta, size, "the eviction freed %d bytes of a %d-byte leaf's records and entry" % (-delta, size))
        tree, modes = self._parse(leaf)
        self.assertEqual(modes[-1:], ["full"], "the next build parses whole (no document here)")
        self.assertEqual(json.dumps(em.plain_tree(tree), sort_keys=True, default=lambda o: "<unserializable>"), want,
                         "the rebuilt tree is the tree")
        self.assertEqual(len(self._held(leaf)), 1)

    def test_an_eviction_from_another_thread_never_waits_on_the_assembly_lock_under_the_cache_lock(self):
        """The assembly lock held here, another thread's read evicts the leaf: its release waits for the assembly lock with
        the cache lock free (a release under the cache lock would hold it, and a thread holding an assembly lock while it
        reads would deadlock against it), then finishes once the assembly lock is let go. Every wait is bounded."""
        leaf = _conversation(os.path.join(self.dir, SID + ".jsonl"), 20)
        other = self._file("notes-api-states.jsonl")
        self._parse(leaf)
        self.assertEqual(len(self._held(leaf)), 1)
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
        ev0, done = _stat("budgetEvictions"), threading.Event()

        def read_other():
            em._read_jsonl_incremental(other)
            done.set()
        t = threading.Thread(target=read_other, daemon=True)
        em._ASM_LOCK.acquire()
        try:
            t.start()
            deadline = time.monotonic() + 30
            while _stat("budgetEvictions") == ev0 and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(_stat("budgetEvictions"), ev0, "the other thread's read evicted the leaf")
            time.sleep(0.2)                                            # it reaches the release and waits there
            free = em._JSONL_CACHE_LOCK.acquire(timeout=10)
            if free:
                em._JSONL_CACHE_LOCK.release()
            self.assertTrue(free, "the cache lock is free while the release waits on the assembly lock")
            self.assertFalse(done.is_set(), "the read waits to release the chat's entry")
        finally:
            em._ASM_LOCK.release()
        t.join(30)
        self.assertFalse(t.is_alive(), "the read finished once the assembly lock was free")
        self.assertEqual(self._held(leaf), [])

    def test_an_eviction_during_the_paths_own_read_keeps_the_chats_entry(self):
        """Another thread's insert can evict a path while that path's read decodes its append from the cached entry. The
        read then re-inserts the very records the chat's entry holds, so a release there freed nothing and cost the chat a
        rebuild at its next build (review round two). A path whose own read is under way is left out of the release; once
        that read has inserted, an eviction of the path releases as before."""
        leaf = _conversation(os.path.join(self.dir, SID + ".jsonl"), 20)
        other = self._file("notes-api-states.jsonl")
        self._parse(leaf)
        held = self._held(leaf)
        self.assertEqual(len(held), 1)
        with open(leaf, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u00020", "parentUuid": "a00019", "timestamp": _iso(NOW - 100),
                                "promptSource": "typed", "cwd": "/w/notes-api",
                                "message": {"role": "user", "content": "step 20: %s" % FILLER}}) + "\n")
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(leaf))   # the grown leaf fits alone, not beside the other
        real_scan, mid = em._scan_jsonl_stream, []

        def scan_beside_an_insert(*args, **kw):                       # the leaf's read of its append, from the cached entry
            em._scan_jsonl_stream = real_scan
            em._read_jsonl_entry(other)                               # the other insert, mid-read: it evicts the leaf
            mid.append((leaf in em._JSONL_CACHE, [e is held[0] for e in self._held(leaf)]))
            return real_scan(*args, **kw)
        with mock.patch.object(em, "_scan_jsonl_stream", scan_beside_an_insert):
            em._read_jsonl_incremental(leaf)
        self.assertEqual(mid, [(False, [True])], "the leaf evicted mid-read, the chat's entry standing")
        self.assertEqual([e is held[0] for e in self._held(leaf)], [True], "the chat's entry stands after the read")
        self.assertNotIn(leaf, em._READ_IN_FLIGHT, "the mark cleared")
        _, modes = self._parse(leaf)                                  # the chat's next build, with no eviction since the read
        self.assertEqual(modes[-1:], ["fold"], "the re-inserted records kept their generation: the entry folds the append "
                                               "(a fresh one would read as a rewrite and parse whole)")
        em._read_jsonl_incremental(other)                             # the leaf's insert evicted it; read back, it evicts the leaf
        self.assertEqual(self._held(leaf), [], "the read over, an eviction of the leaf releases its entry as before")

    def test_a_read_that_raises_mid_scan_leaves_no_mark_and_a_later_eviction_releases(self):
        """The in-flight mark is cleared in a finally, whatever road the read leaves by (review round three): a read of the
        leaf's append that raises mid-scan (anything but OSError, which the reader answers itself) propagates and leaves no
        mark, so a budget eviction of the leaf with no read of it in between releases the chat's entry. A mark left standing
        would skip that release, once, until the path's next read cleared it."""
        leaf = _conversation(os.path.join(self.dir, SID + ".jsonl"), 20)
        other = self._file("notes-api-states.jsonl")
        self._parse(leaf)
        self.assertEqual(len(self._held(leaf)), 1)
        with open(leaf, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": "u00020", "parentUuid": "a00019", "timestamp": _iso(NOW - 100),
                                "promptSource": "typed", "cwd": "/w/notes-api",
                                "message": {"role": "user", "content": "step 20: %s" % FILLER}}) + "\n")

        def scan_raises(*args, **kw):                                 # the leaf's read of its append, from the cached entry
            raise ValueError("a synthetic decode failure")
        with mock.patch.object(em, "_scan_jsonl_stream", scan_raises):
            with self.assertRaises(ValueError):
                em._read_jsonl_incremental(leaf)
        self.assertNotIn(leaf, em._READ_IN_FLIGHT, "the mark cleared though the read raised")
        self.assertIn(leaf, em._JSONL_CACHE, "the entry the read began from stands")
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
        r0 = _stat("budgetReleased")
        em._read_jsonl_incremental(other)                             # evicts the leaf, which was not read since the raise
        self.assertNotIn(leaf, em._JSONL_CACHE)
        self.assertEqual(self._held(leaf), [], "the eviction released the chat's entry")
        self.assertEqual(_stat("budgetReleased") - r0, 1)

    def test_a_prior_lineage_file_evicted_alone_releases_every_chat_that_read_it(self):
        """The release matches every file an entry read, not its leaf alone: two chats whose candidate files share one prior
        file both go when the budget evicts that file and neither leaf, and budgetReleased counts both."""
        prior = _conversation(os.path.join(self.dir, "notes-api-prior.jsonl"), 5, tag="p")
        leaves = [_conversation(os.path.join(self.dir, sid + ".jsonl"), 20) for sid in (SID, SID2)]
        other = self._file("notes-api-states.jsonl")
        for sid, leaf in zip((SID, SID2), leaves):
            self._parse(leaf, sid, [prior, leaf])
            self.assertEqual([prior in e["recs"] for e in self._held(leaf)], [True], "the chat's entry read the prior file")
        for leaf in leaves:
            em._read_jsonl_incremental(leaf)                          # the leaves most recently used: the prior file is oldest
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(sum(os.path.getsize(p) for p in leaves + [other]))
        ev0, r0 = _stat("budgetEvictions"), _stat("budgetReleased")
        em._read_jsonl_incremental(other)
        self.assertEqual((_stat("budgetEvictions") - ev0, prior in em._JSONL_CACHE, [p in em._JSONL_CACHE for p in leaves]),
                         (1, False, [True, True]), "the prior file alone evicted")
        self.assertEqual([self._held(p) for p in leaves], [[], []], "both chats that read it released")
        self.assertEqual(_stat("budgetReleased") - r0, 2)

    def test_a_skip_marked_file_evicted_from_another_readers_copy_releases_nothing(self):
        """A restored entry marks a lineage file wholly before its document's cut ("skip"): it read none of that file, so
        another reader's copy of it leaving by the budget frees nothing of the entry's. A synthetic entry stands for the
        restored one (a resumed lineage and its document): the release reads only the entry's recs."""
        prior, other = self._file("notes-api-prior.jsonl"), self._file("notes-api-states.jsonl")
        leaf = os.path.join(self.dir, SID + ".jsonl")
        entry = {"path": leaf, "recs": {prior: ("skip",), leaf: (1, 0, 40)}}
        em._ASM_CACHE[("synthetic-restored", SID, False)] = entry
        em._read_jsonl_incremental(prior)                             # another reader's copy of the prior file
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
        r0 = _stat("budgetReleased")
        em._read_jsonl_incremental(other)                             # evicts it
        self.assertNotIn(prior, em._JSONL_CACHE)
        self.assertIs(em._ASM_CACHE.get(("synthetic-restored", SID, False)), entry, "the entry that skipped the file stands")
        self.assertEqual(_stat("budgetReleased") - r0, 0)

    def test_one_insert_evicting_two_chats_leaves_releases_both(self):
        leaves = [_conversation(os.path.join(self.dir, sid + ".jsonl"), 20) for sid in (SID, SID2)]
        other = self._file("notes-api-states.jsonl")
        for sid, leaf in zip((SID, SID2), leaves):
            self._parse(leaf, sid)
        self.assertEqual([len(self._held(p)) for p in leaves], [1, 1])
        em._JSONL_CACHE_BUDGET_BYTES = em._real_bytes(os.path.getsize(other))
        ev0, r0 = _stat("budgetEvictions"), _stat("budgetReleased")
        em._read_jsonl_incremental(other)
        self.assertEqual(_stat("budgetEvictions") - ev0, 2, "one insert evicted both leaves")
        self.assertEqual([self._held(p) for p in leaves], [[], []], "both chats released")
        self.assertEqual(_stat("budgetReleased") - r0, 2)


class DecodedMultiple(unittest.TestCase):
    def test_the_constant_is_pinned_at_its_value_2_85(self):
        """The constant's value, against the literal above: what memory the multiple stands for is measured in
        MeasuredMixes, not here."""
        self.assertEqual(em.RECORD_CACHE_DECODED_MULTIPLE, MEASURED)
        self.assertEqual(em._record_cache_decoded_multiple(None), MEASURED, "unset: the constant")
        self.assertEqual(em._record_cache_decoded_multiple(""), MEASURED, "empty: the same")

    def test_the_environment_overrides_it_and_a_value_outside_0_to_1000_is_refused_aloud(self):
        self.assertEqual(em._record_cache_decoded_multiple("1"), 1.0, "1 restores the file-byte budget")
        self.assertEqual(em._record_cache_decoded_multiple("3.5"), 3.5)
        self.assertEqual(em._record_cache_decoded_multiple("1000"), 1000.0, "the cap itself is accepted")
        for bad in ("0", "-2", "nan", "inf", "three", "1001", "1e298", "1e300"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(em._record_cache_decoded_multiple(bad), MEASURED, bad)
            self.assertIn("ROMP_RECORD_CACHE_DECODED_MULTIPLE", err.getvalue(), "%r is refused on stderr, never silently" % bad)

    def test_a_multiple_past_the_cap_is_refused_at_load_with_one_line(self):
        """A finite multiple large enough to overflow (review find, 2026-10-02): 1e300 passed the old range test, and the
        floor's int() of 4 GiB times it raised OverflowError at import with nothing on stderr. Capped at 1000, a fresh
        load imports, says so once on stderr and weighs at 2.85."""
        env = {k: v for k, v in os.environ.items() if k != "ROMP_RECORD_CACHE_BUDGET_MB"}
        env["ROMP_RECORD_CACHE_DECODED_MULTIPLE"] = "1e300"
        err = io.StringIO()
        with mock.patch.dict(os.environ, env, clear=True), contextlib.redirect_stderr(err):
            fresh = load_source("romp_event_model_real_budget_huge", os.path.join(BIN, "romp-event-model"))
        said = [ln for ln in err.getvalue().splitlines() if "ROMP_RECORD_CACHE_DECODED_MULTIPLE" in ln]
        self.assertEqual(len(said), 1, "one stderr line: %r" % err.getvalue()[-2000:])
        self.assertEqual(fresh._RECORD_CACHE_MULTIPLE, MEASURED)
        self.assertEqual(fresh._JSONL_CACHE_BUDGET_BYTES, fresh._record_cache_default_budget_bytes(), "the derived default")

    def test_a_plain_load_weighs_at_the_measured_multiple(self):
        """A module loaded with neither variable set (production's case) weighs entries at 2.85 and derives its budget:
        two 2 MiB entries straddle a 10 MiB budget / 2.85, so the second evicts the first (at 1 both would stay)."""
        env = {k: v for k, v in os.environ.items() if k not in ("ROMP_RECORD_CACHE_DECODED_MULTIPLE", "ROMP_RECORD_CACHE_BUDGET_MB")}
        with mock.patch.dict(os.environ, env, clear=True):
            fresh = load_source("romp_event_model_real_budget_plain", os.path.join(BIN, "romp-event-model"))
        self.assertEqual(fresh._RECORD_CACHE_MULTIPLE, MEASURED, "no variable: the measured multiple, not file bytes")
        self.assertEqual(fresh._JSONL_CACHE_BUDGET_BYTES, fresh._record_cache_default_budget_bytes(), "no variable: the derived default")
        fresh._JSONL_CACHE_BUDGET_BYTES = 10 * MiB
        with fresh._JSONL_CACHE_LOCK:
            for name in ("notes-api/web.jsonl", "notes-api/api.jsonl"):
                fresh._cache_insert_locked(name, _entry(2 * MiB))
        st = fresh.record_cache_stats()
        self.assertEqual((st["entries"], st["budgetEvictions"]), (1, 1))

    def test_a_multiple_of_one_restores_the_file_byte_budget(self):
        """The undo after deploy: ROMP_RECORD_CACHE_DECODED_MULTIPLE=1 on a fresh load gives main's default exactly (the
        floor of 4 GiB on an 8 GB machine, half of MemTotal on a 31 GiB one) and weighs file bytes. Review find,
        2026-10-01: a floor weighed at the constant instead of the multiple in force passed every test."""
        env = {k: v for k, v in os.environ.items() if k != "ROMP_RECORD_CACHE_BUDGET_MB"}
        env["ROMP_RECORD_CACHE_DECODED_MULTIPLE"] = "1"
        with mock.patch.dict(os.environ, env, clear=True):
            fresh = load_source("romp_event_model_real_budget_one", os.path.join(BIN, "romp-event-model"))
        self.assertEqual(fresh._RECORD_CACHE_MULTIPLE, 1.0)
        self.assertEqual(fresh._record_cache_default_budget_bytes("MemTotal:  8000000 kB\n"), 4 * GiB, "main's floor")
        self.assertEqual(fresh._record_cache_default_budget_bytes(BOX_MEMINFO), max(4 * GiB, 32860372 * 1024 // 2), "main's half")
        self.assertEqual(fresh._record_cache_default_budget_bytes("garbage"), 4 * GiB, "main's floor without MemTotal")
        fresh._JSONL_CACHE_BUDGET_BYTES = 10 * MiB
        with fresh._JSONL_CACHE_LOCK:
            for i in range(2):
                fresh._cache_insert_locked("notes-api/leaf-%d.jsonl" % i, _entry(4 * MiB))   # 8 MiB of file bytes under 10
        self.assertEqual(fresh.record_cache_stats()["budgetEvictions"], 0, "file bytes weighed as file bytes: both fit")
        with fresh._JSONL_CACHE_LOCK:
            fresh._cache_insert_locked("notes-api/leaf-2.jsonl", _entry(4 * MiB))            # 12 MiB: one goes
        st = fresh.record_cache_stats()
        self.assertEqual((st["budgetEvictions"], st["entries"], st["realBytes"]), (1, 2, 8 * MiB))

    def test_both_variables_reach_a_fresh_load(self):
        """End to end: a module loaded with ROMP_RECORD_CACHE_BUDGET_MB=64 and ROMP_RECORD_CACHE_DECODED_MULTIPLE=2 has a
        budget of 64 MiB of memory: three entries of 10 MiB of file bytes fit (60 MiB decoded), a fourth evicts one (the
        file-byte budget held six)."""
        with mock.patch.dict(os.environ, {"ROMP_RECORD_CACHE_BUDGET_MB": "64", "ROMP_RECORD_CACHE_DECODED_MULTIPLE": "2"}):
            fresh = load_source("romp_event_model_real_budget_env", os.path.join(BIN, "romp-event-model"))
        self.assertEqual(fresh._JSONL_CACHE_BUDGET_BYTES, 64 * MiB)
        with fresh._JSONL_CACHE_LOCK:
            for i in range(3):
                fresh._cache_insert_locked("notes-api/leaf-%d.jsonl" % i, _entry(10 * MiB))
        self.assertEqual(fresh.record_cache_stats()["budgetEvictions"], 0)
        with fresh._JSONL_CACHE_LOCK:
            fresh._cache_insert_locked("notes-api/leaf-3.jsonl", _entry(10 * MiB))
        st = fresh.record_cache_stats()
        self.assertEqual((st["budgetEvictions"], st["entries"], st["decodedMultiple"]), (1, 3, 2.0))
        self.assertEqual(st["realBytes"], 60 * MiB)


class Surfaces(unittest.TestCase):
    def test_the_judge_child_carries_the_real_figures_as_gauges(self):
        """The judge child's done line differences every counter per pass; a gauge left off its list would read realBytes
        as a growth and the multiple as 0.0 from the second pass on. The re-read counters are counters: per-pass deltas."""
        tree = ast.parse(Path(ROOT, "kernel", "judge.py").read_text())
        found = [ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "_SERVE_GAUGES" for t in n.targets)]
        self.assertEqual(len(found), 1, "one _SERVE_GAUGES assignment")
        for key in ("entries", "bytes", "realBytes", "decodedMultiple", "budgetBytes", "countCap"):
            self.assertIn(key, found[0]["recordCache"], key)
        for key in ("budgetRereads", "budgetRereadBytes", "budgetReleased"):
            self.assertNotIn(key, found[0]["recordCache"], key)

    def test_both_test_runners_unset_the_budget_variables(self):
        """conftest.py unsets them for pytest; tests/__init__.py is the unittest runner's twin and must too, or a multiple
        or a budget exported in the shell moves every budget fixture of a bare unittest run."""
        env = dict(os.environ, ROMP_RECORD_CACHE_DECODED_MULTIPLE="0.4", ROMP_RECORD_CACHE_BUDGET_MB="1")
        out = subprocess.run([sys.executable, "-c", "import os, tests; print([os.environ.get(k) for k in "
                              "('ROMP_RECORD_CACHE_DECODED_MULTIPLE', 'ROMP_RECORD_CACHE_BUDGET_MB')])"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        self.assertEqual(out.stdout.strip().splitlines()[-1], "[None, None]", "the unittest runner's package unsets both")

    def test_the_reference_says_which_unit_each_figure_is_in(self):
        doc = Path(ROOT, "docs", "reference.md").read_text()
        para = doc[doc.index("- `recordCache`: the reader's record cache"):]
        para = para[:para.index("\n- `", 10)]
        for k in ("`bytes`", "`realBytes`", "`decodedMultiple`", "`budgetBytes`", "file bytes", "half of MemTotal",
                  "without `/proc/meminfo` such as macOS", "never under 4 GiB of file bytes", "`ROMP_RECORD_CACHE_BUDGET_MB`",
                  "megabytes of memory", "`ROMP_RECORD_CACHE_DECODED_MULTIPLE`", "`budgetRereads`", "`budgetRereadBytes`",
                  "the whole read that upgrades", "in memory, per process", "1.44 to 7.7 by shape", "at most 1000",
                  "releases every assembly entry", "may rebuild repeatedly", "rewritten after its eviction counted too",
                  "until the parse store replaces or drops it", "installs after it keeps what it read",
                  "evicted during its own read is not released", "rebuilds the entry once", "`budgetReleased`",
                  "replaces or drops it and a shown tab's build memos of it go at that tab's next build",
                  "replaces or drops that tree and a shown tab's build memos of it go, or the budget evicts"):
            self.assertIn(k, " ".join(para.split()), k)
        heap = doc[doc.index("- `heap`:"):]
        heap = heap[:heap.index("\n- `", 10)]
        self.assertIn("`realBytes` against `budgetBytes`", heap, "the heap bullet points at the figure the budget compares")


if __name__ == "__main__":
    unittest.main()
