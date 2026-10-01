#!/usr/bin/env python3
"""The bats step of CI carries a per-test bound from bats itself (.github/workflows/ci.yml, 2026-09-16).

The macOS bats leg (a manual dispatch) was cancelled at the job's 35-minute ceiling on every run, nameless:
the job annotation says only that the maximum execution time was exceeded, and the TAP stream stops at the
last test that finished. BATS_TEST_TIMEOUT is bats-core's own bound (since 1.8, in the pinned 1.11.1 and in
Homebrew's 1.14.0 alike): a background sleep, then pkill or ps on the test's children, and the test's TAP
line ends "# timeout after Ns", so a hung test fails in minutes and is named. No coreutils timeout, which the
macOS image lacks. Source pins, as tests/test_ci_workflow_concurrency.py: no YAML library in the test deps."""
import os
import re
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
WF = os.path.join(os.path.dirname(HERE), ".github", "workflows", "ci.yml")


class BatsStepBound(unittest.TestCase):
    def setUp(self):
        src = open(WF).read()
        m = re.search(r"^      - name: Run bats\n((?:        .*\n)*?)        run: (bats .*)\n", src, re.M)   # zero lines between: the step without an env
        self.assertTrue(m, "the Run bats step moved or was renamed: re-anchor this pin")
        self.head, self.cmd = m.group(1), m.group(2)

    def test_the_step_sets_bats_own_per_test_timeout(self):
        m = re.search(r'^          BATS_TEST_TIMEOUT: "?(\d+)"?$', self.head, re.M)
        self.assertTrue(m, "no BATS_TEST_TIMEOUT in the step's env: a hung test would eat the job's whole budget, nameless")
        secs = int(m.group(1))
        self.assertGreaterEqual(secs, 120, "below two minutes the slowest legitimate macOS test (the 60 s romp-serve probes) is at risk")
        self.assertLessEqual(secs, 600, "above ten minutes a hang still eats most of the 35-minute job")

    def test_the_step_still_runs_every_bats_file(self):
        self.assertIn("tests/*.bats", self.cmd)
        self.assertIn("--print-output-on-failure", self.cmd)


class PythonJobCeiling(unittest.TestCase):
    """The Python cells' ceiling is per shard, and per OS (since 2026-09-30, when each cell's suite became two shards).
    The rule: a shard's pytest step plus the 600 s per-test timeout plus setup, or a stall late in the step is cut by the
    cap before the per-test timeout names it. Unsharded the caps had kept climbing with the suite: Linux 25 to 35 and
    macOS 40 to 60 on 2026-09-24, when the release dispatch (run 35976250043) had the 3.10 macOS cell cancelled at
    40 min 20 s with no failure in its log. By 2026-09-28 the 3.10 macOS cell's pytest step took 51 min 40 s on the weekly
    run (36408096233), nine minutes under that cap, and the Linux cells took 14 to 19 minutes. A shard is about half:
    26 minutes on macOS, 10 on Linux. So macOS gets 50 (26 plus 10 plus setup is about 37, and a slow macOS runner or a
    split balanced on Linux timings can add several minutes), and Linux 30 (10 plus 10 plus setup is about 21). Past
    the ceilings the next step is a third shard, not a higher cap."""
    def setUp(self):
        src = open(WF).read()
        m = re.search(r"^  python-shard:\n((?:    .*\n|\n)+?)    strategy:\n", src, re.M)
        self.assertTrue(m, "the python shards' job head moved: re-anchor this pin")
        self.head = m.group(1)

    def test_macos_shards_get_fifty_minutes_and_linux_thirty_and_neither_reverts_below_its_floor(self):
        m = re.search(r"^    timeout-minutes: \$\{\{ matrix\.os == 'macos-latest' && (\d+) \|\| (\d+) \}\}$", self.head, re.M)
        self.assertTrue(m, "the python shards' timeout-minutes is not the per-OS expression (macos-latest && N || M)")
        macos, linux = int(m.group(1)), int(m.group(2))
        self.assertGreaterEqual(macos, 45, "a macOS shard: half of the 3.10 cell's 51 min 40 s pytest step (run 36408096233) is "
                                "about 26 minutes, plus the 600 s per-test timeout plus setup is about 37, and a slow runner or "
                                "a split balanced on Linux timings adds minutes, so a cap below 45 risks cutting a stall nameless")
        self.assertLessEqual(macos, 60, "a macOS shard that needs more than an hour wants a third shard, not a higher cap")
        self.assertGreaterEqual(linux, 25, "a Linux shard: half of the 3.10 cell's 19-minute pytest step plus the 600 s "
                                "per-test timeout plus setup is about 21, so a cap below 25 leaves a slow runner no room")
        self.assertLessEqual(linux, 35, "a Linux shard that needs more than 35 minutes wants a third shard, not a higher cap")


class ExtensionJobCeiling(unittest.TestCase):
    """The served labs grow with every lab added, and the job running them kept outgrowing its cap: 15, 25, 40, then 60
    minutes (PR 1790's run cut at 25 min 04 s mid lab on 2026-09-16, PR 2061's red run 35821212036 cut at 40 min 15 s),
    until the unsharded served step took 45 min 20 s on 2026-09-30 (run 36702816332). Since then the labs run as parallel
    shards (scripts/ci/shard_tests.py) and the cap is per shard, by the same rule: a shard's served step plus the 600 s
    per-test timeout plus setup. A quarter of the step is about 12 minutes; 30 leaves up to 18 for a slow runner and new
    labs. Below 25 a slow shard's stall is cut by the cap before the per-test timeout names it; past 35 the next step is
    another shard, not a higher cap."""
    def test_each_served_shard_gets_thirty_minutes_and_never_reverts_below_the_rules_figure(self):
        src = open(WF).read()
        m = re.search(r"^  vscode-extension-served:\n((?:    .*\n|\n)+?)    strategy:\n", src, re.M)
        self.assertTrue(m, "the served shards' job head moved: re-anchor this pin")
        t = re.search(r"^    timeout-minutes: (\d+)$", m.group(1), re.M)
        self.assertTrue(t, "the served shards' job has no plain timeout-minutes line")
        self.assertGreaterEqual(int(t.group(1)), 25, "a shard's served step of up to 13 minutes on a slow runner plus the 600 s "
                                                     "per-test timeout plus setup is about 25, so a cap below it cuts a stall nameless")
        self.assertLessEqual(int(t.group(1)), 35, "a shard that needs more than 35 minutes wants another shard, not a higher cap")


if __name__ == "__main__":
    unittest.main()
