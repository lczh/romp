#!/usr/bin/env python3
"""CI's served-page tests run as parallel shards behind one required check (.github/workflows/ci.yml, 2026-09-30).

The browser-backed served-page tests were 45 of the extension job's 50 minutes, one pytest process, so a flaky lab cost
the whole 50 again on a rerun. They now run as matrix shards, each dealt its files by scripts/ci/shard_tests.py from the
recorded per-file durations in scripts/ci/served-durations.json; the typecheck, node tests and build run once, in their
own job. The ruleset requires a check named "vscode-extension (typecheck + test + build)", so that name belongs to a
verdict job that needs every part and runs under always(): GitHub reports a job skipped by a conditional as a success
to a required check, so a verdict skipped over a failed shard would let the shard's red merge.

Pinned here: the dealer (every file in exactly one shard, the same split for the same inputs whatever their order,
balanced by time rather than by count, a file with no recorded duration still dealt and warned about, recorded files
keeping their shards when a new one arrives, the refresh summing a JUnit report per file), and the workflow's shape (the
verdict's exact name, its always(), its needs, and its step run under bash on synthetic results: success passes and any
failure, cancellation or skip fails). Source pins for the YAML, as tests/test_ci_workflow_concurrency.py: no YAML library
in the test deps. Synthetic file names only.
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)
WF = os.path.join(REPO, ".github", "workflows", "ci.yml")
SCRIPT = os.path.join(REPO, "scripts", "ci", "shard_tests.py")
REQUIRED = "vscode-extension (typecheck + test + build)"

sys.path.insert(0, os.path.dirname(SCRIPT))
import shard_tests  # noqa: E402


class Dealer(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="served-shards-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _cli(self, *args, durations=None):
        path = os.path.join(self.d, "durations.json")
        with open(path, "w") as f:
            json.dump(durations or {}, f)
        return subprocess.run([sys.executable, SCRIPT, "--durations", path] + list(args),
                              capture_output=True, text=True, timeout=60)

    def test_every_file_lands_in_exactly_one_shard(self):
        files = ["tests/test_f%02d_served.py" % i for i in range(23)]
        durations = {f: float((i * 37) % 11 + 1) for i, f in enumerate(files[:20])}
        shards, _, _, _ = shard_tests.plan(files, durations, 4)
        dealt = [f for s in shards for f in s]
        self.assertEqual(sorted(dealt), sorted(files), "every file dealt")
        self.assertEqual(len(dealt), len(set(dealt)), "no file dealt twice")

    def test_the_same_inputs_give_the_same_split_whatever_their_order(self):
        files = ["tests/test_f%02d_browser.py" % i for i in range(30)]
        durations = {f: float(i % 7) + 0.5 for i, f in enumerate(files)}   # many equal durations: the name breaks ties
        first = shard_tests.plan(files, durations, 4)[0]
        for seed in range(5):
            shuffled = files[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(shard_tests.plan(shuffled, durations, 4)[0], first)
        outs = [self._cli("--shard", "2", "--of", "4", *order, durations=durations).stdout for order in (files, files[::-1])]
        self.assertEqual(outs[0], outs[1], "the command prints the same shard for either argument order")

    def test_the_split_balances_time_not_file_count(self):
        durations = {"tests/test_big_served.py": 100.0}
        durations.update({"tests/test_small%d_served.py" % i: 10.0 for i in range(10)})
        shards, totals, _, _ = shard_tests.plan(list(durations), durations, 2)
        self.assertEqual(totals, [100.0, 100.0])
        self.assertEqual(shards[0], ["tests/test_big_served.py"], "the long file runs alone; the ten short ones share a shard")

    def test_a_file_with_no_recorded_duration_still_runs_and_its_shard_warns(self):
        known = {"tests/test_a_served.py": 30.0, "tests/test_b_served.py": 20.0, "tests/test_c_browser.py": 10.0}
        files = sorted(known) + ["tests/test_new_browser.py"]
        runs = [self._cli("--shard", str(k), "--of", "2", *files, durations=known) for k in (1, 2)]
        for r in runs:
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("::warning file=tests/test_new_browser.py::", r.stderr, "each shard's log names the unrecorded file")
        printed = [r.stdout.split() for r in runs]
        self.assertEqual(sum(p.count("tests/test_new_browser.py") for p in printed), 1, "the new file runs in exactly one shard")
        self.assertEqual(sorted(f for p in printed for f in p), sorted(files))

    def test_a_new_file_leaves_every_recorded_file_on_its_shard(self):
        known = {"tests/test_f%02d_served.py" % i: float(i + 1) for i in range(12)}
        before = shard_tests.plan(list(known), known, 3)[0]
        after = shard_tests.plan(list(known) + ["tests/test_zz_new_served.py", "tests/test_00_new_browser.py"], known, 3)[0]
        self.assertEqual([[f for f in s if f in known] for s in after], before)

    def test_an_empty_shard_is_an_error_rather_than_a_bare_pytest_run(self):
        # an empty file list would hand pytest no paths, and a bare pytest collects the whole suite
        r = self._cli("--shard", "3", "--of", "3", "tests/test_a_served.py", "tests/test_b_served.py")
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        self.assertIn("empty", r.stderr)

    def test_the_refresh_sums_each_files_cases_and_keeps_what_the_reports_do_not_name(self):
        report = os.path.join(self.d, "junit")
        os.mkdir(report)
        with open(os.path.join(report, "served-junit.xml"), "w") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
                    '<testcase classname="tests.test_a_served.A" name="test_one" file="tests/test_a_served.py" time="12.25"/>'
                    '<testcase classname="tests.test_a_served.A" name="test_two" file="tests/test_a_served.py" time="3.5"/>'
                    '<testcase classname="tests.test_b_browser.B" name="test_one" file="tests/test_b_browser.py" time="7.0"/>'
                    '</testsuite></testsuites>')
        path = os.path.join(self.d, "durations.json")
        own = os.path.relpath(os.path.realpath(__file__), REPO)
        with open(path, "w") as f:
            json.dump({"tests/test_a_served.py": 99.0, own: 5.0, "tests/test_gone_served.py": 8.0}, f)
        r = subprocess.run([sys.executable, SCRIPT, "--durations", path, "--refresh", report],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path) as f:
            self.assertEqual(json.load(f), {"tests/test_a_served.py": 15.8, "tests/test_b_browser.py": 7.0, own: 5.0},
                             "a reported file's cases are summed, an unreported file that still exists keeps its entry, "
                             "and a file no longer in the tree loses its")

    def test_the_committed_durations_file_is_well_formed(self):
        durations = shard_tests.load()
        self.assertTrue(durations, "scripts/ci/served-durations.json is empty: every lab would be dealt by count")
        for f, secs in durations.items():
            self.assertRegex(f, r"^tests/test_\w+_(browser|served)\.py$")
            self.assertIsInstance(secs, (int, float))
            self.assertGreaterEqual(secs, 0)


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as f:
            cls.src = f.read()

    def _job(self, job_id):
        m = re.search(r"^  %s:\n((?:    .*\n|\n)+)" % re.escape(job_id), self.src, re.M)
        self.assertTrue(m, "no %s job in ci.yml" % job_id)
        return m.group(1)

    def test_the_required_name_is_the_verdict_and_it_runs_whatever_its_parts_did(self):
        job = self._job("vscode-extension")
        self.assertIn("    name: %s\n" % REQUIRED, job, "the ruleset requires this exact check name")
        self.assertEqual(self.src.count("name: %s\n" % REQUIRED), 1, "exactly one job reports under the required name")
        self.assertRegex(job, r"(?m)^    if: always\(\)$",
                         "without always() a failed shard SKIPS the verdict, and a skipped job passes a required check")
        needs = re.search(r"(?m)^    needs: \[([^\]]*)\]$", job)
        self.assertTrue(needs, "the verdict names its parts")
        self.assertEqual(sorted(n.strip() for n in needs.group(1).split(",")),
                         ["vscode-extension-served", "vscode-extension-unit"])

    def test_the_verdict_fails_unless_every_part_succeeded(self):
        if not shutil.which("jq"):
            self.skipTest("jq is not installed here (the GitHub images carry it)")
        run = re.search(r"        run: \|\n((?:          .*\n)+)", self._job("vscode-extension"))
        self.assertTrue(run, "the verdict step's script moved: re-anchor this pin")
        script = "\n".join(line[10:] for line in run.group(1).splitlines())

        def verdict(unit, served):
            needs = {"vscode-extension-unit": {"result": unit, "outputs": {}},
                     "vscode-extension-served": {"result": served, "outputs": {}}}
            p = subprocess.run(["bash", "-e", "-c", script], env=dict(os.environ, NEEDS=json.dumps(needs)),
                               capture_output=True, text=True, timeout=60)
            return p.returncode

        self.assertEqual(verdict("success", "success"), 0)
        for bad in ("failure", "cancelled", "skipped"):
            self.assertNotEqual(verdict("success", bad), 0, "a %s shard must turn the required check red" % bad)
            self.assertNotEqual(verdict(bad, "success"), 0, "a %s unit job must turn the required check red" % bad)

    def test_the_shards_deal_by_the_matrix_and_run_to_their_own_verdicts(self):
        job = self._job("vscode-extension-served")
        self.assertIn("fail-fast: false", job, "a red shard must not cancel its siblings")
        self.assertIn("scripts/ci/shard_tests.py --shard ${{ matrix.shard }} --of ${{ strategy.job-total }} ", job,
                      "the shard count follows the matrix, so adding a shard is one edit")
        self.assertIn("python -m pytest $files ", job)
        self.assertIn("--junitxml=served-junit.xml -o junit_family=xunit1", job, "the report the durations refresh reads")

    def test_the_typecheck_node_tests_and_build_run_once_not_per_shard(self):
        unit = self._job("vscode-extension-unit")
        for cmd in ("npm run typecheck", "npm test", "node --test tests/ui-bench.test.mjs"):
            self.assertIn("run: %s\n" % cmd, unit)
            self.assertEqual(self.src.count(cmd), 1, "%s runs in the unit job alone" % cmd)
        self.assertNotIn("npm run typecheck", self._job("vscode-extension-served"))


if __name__ == "__main__":
    unittest.main()
