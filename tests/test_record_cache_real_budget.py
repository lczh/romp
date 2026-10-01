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
the judge child's gauge list and the reference. Synthetic files and synthetic cache entries only."""
import ast
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
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
        em._JSONL_CACHE_BUDGET_BYTES = int(2.5 * one * MEASURED)      # room for two such files decoded; seven by file bytes
        for p in files:
            em._read_jsonl_incremental(p)
        self.assertEqual(list(em._JSONL_CACHE), files[2:], "the two most recently used stay: each file weighed 2.85x its size "
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


class DecodedMultiple(unittest.TestCase):
    def test_the_default_is_the_measured_figure(self):
        self.assertEqual(em.RECORD_CACHE_DECODED_MULTIPLE, MEASURED)
        self.assertEqual(em._record_cache_decoded_multiple(None), MEASURED, "unset: the measured figure")
        self.assertEqual(em._record_cache_decoded_multiple(""), MEASURED, "empty: the same")

    def test_the_environment_overrides_it_and_a_value_that_is_no_positive_number_is_refused_aloud(self):
        self.assertEqual(em._record_cache_decoded_multiple("1"), 1.0, "1 restores the file-byte budget")
        self.assertEqual(em._record_cache_decoded_multiple("3.5"), 3.5)
        for bad in ("0", "-2", "nan", "inf", "three"):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(em._record_cache_decoded_multiple(bad), MEASURED, bad)
            self.assertIn("ROMP_RECORD_CACHE_DECODED_MULTIPLE", err.getvalue(), "%r is refused on stderr, never silently" % bad)

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
        for key in ("budgetRereads", "budgetRereadBytes"):
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
                  "bounds what this cache holds, not the records' memory", "`g:rewrite`", "the whole read that upgrades"):
            self.assertIn(k, " ".join(para.split()), k)
        heap = doc[doc.index("- `heap`:"):]
        heap = heap[:heap.index("\n- `", 10)]
        self.assertIn("`realBytes` against `budgetBytes`", heap, "the heap bullet points at the figure the budget compares")


if __name__ == "__main__":
    unittest.main()
