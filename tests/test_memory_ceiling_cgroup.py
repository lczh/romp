#!/usr/bin/env python3
"""The bounds that scale with the machine take the memory the PROCESS has (2026-11-14): MemTotal, or the cgroup memory
limit of the process where one sits below it. The record cache's default budget read half of MemTotal (33.7 GB on a
64 GB box, more than the memory that was really there under a 32 GB virtio balloon) and bound nothing; a service unit's
MemoryMax= or MemoryHigh=, or a container's limit, is the figure the process may actually use. Pinned here:
em._cgroup_memory_limit_bytes over a synthetic cgroup tree (v2's memory.max and memory.high at every ancestor, v1's
memory.limit_in_bytes, "max" everywhere, the root cgroup, no cgroup file at all); the record cache's default budget,
em._machine_memory_bytes and the kernel's _mem_total_bytes under a limit, and unchanged without one; the limit on
/perf recordCache. Synthetic texts and a temp directory only, no state, no kernel."""
import os
import sys
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
for _knob in ("ROMP_SPEND_GUARD_TREE_MEMO_BYTES", "ROMP_HYDRATED_CAP_MB", "ROMP_RECORD_CACHE_BUDGET_MB"):
    os.environ.pop(_knob, None)   # the bounds compared below honour these overrides at import: a developer shell exporting one
#                                   must not fail a test about the cgroup limit (review 2026-11-14), as KernelKnob clears the freeze knobs
em = load_source("romp_event_model_cgroup_ceiling", os.path.join(BIN, "romp-event-model"))
km = load_source("romp_kernel_cgroup_ceiling", os.path.join(BIN, "romp-kernel"))

GiB = 1024 ** 3
MEMINFO_64 = "MemTotal:       67108864 kB\nMemFree:        10000000 kB\n"     # a synthetic 64 GiB machine
V2_SELF = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/notes-api.service\n"
V2_PATH = ["user.slice", "user-1000.slice", "user@1000.service", "app.slice", "notes-api.service"]


def _write(root, segs, name, value):
    d = os.path.join(root, *segs)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name), "w") as f:
        f.write(value + "\n")


class CgroupLimit(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(prefix="cgroup-ceiling-")
        self.addCleanup(self._td.cleanup)
        self.root = self._td.name
        for n in range(len(V2_PATH) + 1):                      # every level says max for both files to begin with
            _write(self.root, V2_PATH[:n], "memory.max", "max")
            _write(self.root, V2_PATH[:n], "memory.high", "max")

    def test_the_smallest_of_max_and_high_over_the_cgroup_and_its_ancestors(self):
        _write(self.root, V2_PATH[:3], "memory.max", str(32 * GiB))          # user@1000.service: MemoryMax=32G
        _write(self.root, V2_PATH, "memory.high", str(16 * GiB))             # the unit itself: MemoryHigh=16G
        _write(self.root, V2_PATH[:1], "memory.max", str(48 * GiB))          # user.slice
        self.assertEqual(em._cgroup_memory_limit_bytes(V2_SELF, root=self.root), 16 * GiB)

    def test_max_at_every_level_is_no_limit(self):
        self.assertEqual(em._cgroup_memory_limit_bytes(V2_SELF, root=self.root), 0)

    def test_the_root_cgroup_without_the_files_is_no_limit(self):
        for n in range(len(V2_PATH) + 1):
            for name in ("memory.max", "memory.high"):
                os.unlink(os.path.join(self.root, *V2_PATH[:n], name))
        self.assertEqual(em._cgroup_memory_limit_bytes("0::/\n", root=self.root), 0)
        self.assertEqual(em._cgroup_memory_limit_bytes(V2_SELF, root=self.root), 0, "no file at any level")

    def test_an_unreadable_or_odd_text_is_no_limit(self):
        self.assertEqual(em._cgroup_memory_limit_bytes("", root=self.root), 0)
        self.assertEqual(em._cgroup_memory_limit_bytes("garbage\n", root=self.root), 0)
        _write(self.root, V2_PATH, "memory.max", "lots")
        self.assertEqual(em._cgroup_memory_limit_bytes(V2_SELF, root=self.root), 0, "a value that is not a number is skipped")
        self.assertEqual(em._cgroup_memory_limit_bytes(V2_SELF, root=os.path.join(self.root, "no-such-root")), 0,
                         "a host without a cgroup filesystem")

    def test_cgroup_v1_reads_the_memory_controllers_limit(self):
        text = "12:memory:/docker/abc\n11:cpu,cpuacct:/docker/abc\n"
        _write(self.root, ["memory", "docker", "abc"], "memory.limit_in_bytes", str(8 * GiB))
        _write(self.root, ["memory", "docker"], "memory.limit_in_bytes", "9223372036854771712")   # v1's "no limit"
        self.assertEqual(em._cgroup_memory_limit_bytes(text, root=self.root), 8 * GiB)
        _write(self.root, ["memory", "docker", "abc"], "memory.limit_in_bytes", "9223372036854771712")
        self.assertEqual(em._cgroup_memory_limit_bytes(text, root=self.root), 0, "PAGE_COUNTER_MAX pages is none")

    def test_the_process_reads_its_own_once_at_load_and_perf_shows_it(self):
        self.assertEqual(em._CGROUP_MEMORY_LIMIT, em._cgroup_memory_limit_bytes(), "the same read, the live files")
        self.assertEqual(em.record_cache_stats()["cgroupLimitBytes"], em._CGROUP_MEMORY_LIMIT)


class BudgetDefaultUnderALimit(unittest.TestCase):
    """The record cache's default budget: half of the lesser of MemTotal and the cgroup limit, the floor still under it."""

    def setUp(self):
        self._multiple = em._RECORD_CACHE_MULTIPLE
        em._RECORD_CACHE_MULTIPLE = 2.85
        self.floor = int(em.RECORD_CACHE_BUDGET_FLOOR_BYTES * 2.85)

    def tearDown(self):
        em._RECORD_CACHE_MULTIPLE = self._multiple

    def test_half_of_the_limit_when_it_sits_below_memtotal(self):
        self.assertEqual(em._record_cache_default_budget_bytes(MEMINFO_64), 32 * GiB, "no limit: half of MemTotal, as before")
        self.assertEqual(em._record_cache_default_budget_bytes(MEMINFO_64, cgroup_limit=32 * GiB), 16 * GiB,
                         "MemoryHigh=32G on a 64 GiB box: half of the limit, not half of the machine")
        self.assertEqual(em._record_cache_default_budget_bytes(MEMINFO_64, cgroup_limit=96 * GiB), 32 * GiB,
                         "a limit above MemTotal changes nothing")

    def test_the_floor_holds_under_a_small_limit(self):
        self.assertEqual(em._record_cache_default_budget_bytes(MEMINFO_64, cgroup_limit=16 * GiB), self.floor,
                         "8 GiB would be under the floor's 4 GiB of file bytes: the floor, as on a 16 GiB machine")

    def test_a_limit_alone_when_meminfo_is_unreadable(self):
        self.assertEqual(em._record_cache_default_budget_bytes("garbage", cgroup_limit=40 * GiB), 20 * GiB)
        self.assertEqual(em._record_cache_default_budget_bytes("garbage"), self.floor, "neither: the floor, as before")

    def test_a_given_text_is_the_whole_input(self):
        """A test's synthetic meminfo must answer the same on a limited CI container as on a bare machine."""
        with mock.patch.object(em, "_cgroup_memory_limit_bytes", lambda *a, **k: 8 * GiB):
            self.assertEqual(em._record_cache_default_budget_bytes(MEMINFO_64), 32 * GiB, "the text given, no live read")
            self.assertEqual(em._machine_memory_bytes(MEMINFO_64), 64 * GiB)

    def test_a_live_read_takes_the_process_limit(self):
        real_open = open

        def fake(p, *a, **k):
            if str(p) == "/proc/meminfo":
                import io
                return io.StringIO(MEMINFO_64)
            return real_open(p, *a, **k)
        with mock.patch.object(em, "open", fake, create=True), \
                mock.patch.object(em, "_cgroup_memory_limit_bytes", lambda *a, **k: 24 * GiB):
            self.assertEqual(em._record_cache_default_budget_bytes(), 12 * GiB, "the default the module binds at load")
            self.assertEqual(em._machine_memory_bytes(), 24 * GiB, "the ceilings' base: the limit, under MemTotal")


class MachineMemoryUnderALimit(unittest.TestCase):
    def test_event_model_machine_memory(self):
        self.assertEqual(em._machine_memory_bytes(MEMINFO_64), 64 * GiB)
        self.assertEqual(em._machine_memory_bytes(MEMINFO_64, cgroup_limit=8 * GiB), 8 * GiB)
        self.assertEqual(em._machine_memory_bytes(MEMINFO_64, cgroup_limit=0), 64 * GiB, "0: none")
        self.assertEqual(em._machine_memory_bytes("garbage", cgroup_limit=8 * GiB), 8 * GiB, "the limit alone without procfs")
        self.assertEqual(em._machine_memory_bytes("garbage"), 0, "neither: zero, so the floors apply, as before")

    def test_kernel_mem_total(self):
        with tempfile.NamedTemporaryFile("w", suffix="-meminfo", delete=False) as f:
            f.write(MEMINFO_64)
        self.addCleanup(os.unlink, f.name)
        self.assertEqual(km._mem_total_bytes(meminfo=f.name), 64 * GiB, "a named file: the whole input, no live cgroup read")
        self.assertEqual(km._mem_total_bytes(meminfo=f.name, cgroup_limit=24 * GiB), 24 * GiB)
        self.assertEqual(km._mem_total_bytes(meminfo=f.name, cgroup_limit=0), 64 * GiB)
        with mock.patch.object(km.em, "_cgroup_memory_limit_bytes", lambda *a, **k: 12 * GiB):
            self.assertEqual(km._mem_total_bytes(), min(12 * GiB, km._mem_total_bytes(cgroup_limit=0)),
                             "the default path reads the process's limit")
        self.assertEqual(km._mem_total_bytes(meminfo="/nonexistent/meminfo", cgroup_limit=3 * GiB), 3 * GiB,
                         "sysconf's total held under the limit too")

    def test_the_fractions_are_of_the_process_memory(self):
        """The import-time constants compose the live read: every MemTotal-scaled bound follows the limit when one is set."""
        self.assertEqual(km.SPEND_GUARD_TREE_MEMO_BYTES, km._mem_total_bytes() // 64)
        self.assertEqual(em._HYDRATED_CAP, max(256 * 1024 ** 2, em._machine_memory_bytes() // 48))


class TheReferenceSaysWhenTheLimitIsRead(unittest.TestCase):
    def test_the_unit_paragraph_says_the_limit_is_read_at_the_kernels_start(self):
        """Review 2026-11-14 (medium): the reference told the operator to set MemoryHigh= with set-property and read
        recordCache.cgroupLimitBytes, but the limit is read ONCE at import (em._CGROUP_MEMORY_LIMIT; every memory-scaled bound
        is an import-time constant), so set-property on the running unit changes nothing until the kernel restarts. The
        paragraph says so, and the code is what it says: the module constant, not a per-read call."""
        with open(os.path.join(ROOT, "docs", "reference.md")) as fh:
            paras = [p for p in fh.read().split("\n\n") if "set-property romp-manager.service MemoryHigh" in p]
        self.assertEqual(len(paras), 1, "one paragraph names the set-property command")
        para = paras[0]
        self.assertIn("restart", para, "the paragraph says the setting takes effect at the kernel's next restart: %r" % para[-400:])
        self.assertIn("at start", para.replace("at its start", "at start"), "and that the kernel reads the limit at start")
        self.assertIsInstance(em._CGROUP_MEMORY_LIMIT, int, "the limit is a module constant, read at load")
        self.assertEqual(em.record_cache_stats()["cgroupLimitBytes"], em._CGROUP_MEMORY_LIMIT, "/perf shows that constant")


if __name__ == "__main__":
    unittest.main()
