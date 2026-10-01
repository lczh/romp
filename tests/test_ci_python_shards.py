#!/usr/bin/env python3
"""CI's Python suite runs as two shards per cell behind the four required Python checks (.github/workflows/ci.yml,
2026-09-30).

Once the served tests were sharded, the Python cells (14 to 19 minutes each, one pytest process) set the whole run's wall
time. Each cell's suite now runs as matrix shards, dealt by scripts/ci/shard_tests.py from the recorded per-file
durations in scripts/ci/python-durations.json. The ruleset requires "Python 3.10 (ubuntu-latest)" through 3.13 by exact
name, so those names belong to a per-cell verdict job that needs the shards and runs under always(). A matrix job's needs
result is one for all its legs, so when that result is not success the verdict reads its own cell's shards from the jobs
API: a red 3.10 shard reddens 3.10's check and no other.

Pinned here: the verdicts' names, always(), needs and permission; their script, run under bash on synthetic jobs through a
fake gh that applies the step's own jq filter, passing when its cell's shards are green and failing on a failed,
cancelled, skipped or missing shard or on an API error; the shards and the verdicts covering the same cells; --of
matching the matrix; the refresh crediting a case to the module that collected it and keeping the slowest version's time.
Source pins, as tests/test_ci_workflow_concurrency.py: no YAML library in the test deps. Synthetic names only.
"""
import glob
import json
import os
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
DURATIONS = os.path.join(REPO, "scripts", "ci", "python-durations.json")
CELL_NAME = "Python ${{ matrix.python-version }} (${{ matrix.os }})"

sys.path.insert(0, os.path.dirname(SCRIPT))
import shard_tests  # noqa: E402


def _job(src, job_id):
    m = re.search(r"^  %s:\n((?:    .*\n|\n)+)" % re.escape(job_id), src, re.M)
    assert m, "no %s job in ci.yml" % job_id
    return m.group(1)


def _matrix(job):
    """The job's matrix block, comments and the shard dimension dropped."""
    m = re.search(r"^      matrix:.*\n((?:        .*\n)+)", job, re.M)
    assert m, "the job has no matrix"
    lines = [re.sub(r"\s+#.*$", "", line) for line in m.group(1).splitlines()]
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#") and not line.strip().startswith("shard:")]


class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(WF, encoding="utf-8") as f:
            cls.src = f.read()
        cls.verdict = _job(cls.src, "python")
        cls.shards = _job(cls.src, "python-shard")

    def test_the_required_names_are_the_per_cell_verdicts_and_they_run_whatever_the_shards_did(self):
        self.assertIn("    name: %s\n" % CELL_NAME, self.verdict, "the ruleset requires Python X (ubuntu-latest) by exact name")
        self.assertEqual(self.src.count("name: %s\n" % CELL_NAME), 1, "exactly one job reports under the cell names")
        self.assertRegex(self.verdict, r"(?m)^    if: always\(\)$",
                         "without always() a failed shard SKIPS the verdict, and a skipped job passes a required check")
        self.assertRegex(self.verdict, r"(?m)^    needs: python-shard$")
        self.assertRegex(self.verdict, r"(?m)^      actions: read", "the jobs API needs the actions read permission")
        versions = re.search(r"(?m)^        python-version: \[([^\]]*)\]$", self.verdict)
        self.assertTrue(versions)
        self.assertEqual([v.strip().strip("'") for v in versions.group(1).split(",")], ["3.10", "3.11", "3.12", "3.13"],
                         "the four required Python checks")
        self.assertIn("|| '[\"ubuntu-latest\"]'", self.verdict, "a pull request's cells are the Linux ones the ruleset names")

    def test_the_shards_and_the_verdicts_cover_the_same_cells(self):
        self.assertEqual(_matrix(self.shards), _matrix(self.verdict),
                         "a cell with shards but no verdict reports nowhere; a verdict with no shards finds none and fails")

    def test_each_shard_is_named_for_its_cell_as_the_verdict_looks_it_up(self):
        self.assertIn("    name: %s shard ${{ matrix.shard }}\n" % CELL_NAME, self.shards)
        self.assertIn("          CELL: %s\n" % CELL_NAME, self.verdict)
        self.assertIn('startswith(env.CELL + " shard ")', self.verdict)

    def test_the_shard_count_follows_the_matrix_and_every_shard_runs_to_its_own_verdict(self):
        self.assertIn("fail-fast: false", self.shards, "a red shard must not cancel its siblings")
        shard = re.search(r"(?m)^        shard: \[([^\]]*)\]$", self.shards)
        self.assertTrue(shard, "the shards' matrix has no shard list")
        count = len(shard.group(1).split(","))
        self.assertEqual([int(k) for k in shard.group(1).split(",")], list(range(1, count + 1)))
        self.assertIn("ignores=$(python scripts/ci/shard_tests.py --durations scripts/ci/python-durations.json "
                      "--shard ${{ matrix.shard }} --of %d --ignore-others tests/test_*.py)" % count, self.shards,
                      "--of must equal the matrix's shard count, or a shard's files run nowhere")
        self.assertIn("python -m pytest tests $ignores ", self.shards,
                      "the directory minus the other shards' files: a long list of file arguments collects far slower")
        self.assertIn("--junitxml=python-junit.xml", self.shards, "the report the durations refresh reads")
        self.assertIn("name: python-junit-${{ matrix.python-version }}-${{ matrix.os }}-${{ matrix.shard }}", self.shards)

    def test_the_verdict_fails_unless_every_shard_of_its_cell_succeeded(self):
        if not shutil.which("jq"):
            self.skipTest("jq is not installed here (the GitHub images carry it)")
        run = re.search(r"        run: \|\n((?:          .*\n)+)", self.verdict)
        self.assertTrue(run, "the verdict step's script moved: re-anchor this pin")
        script = "\n".join(line[10:] for line in run.group(1).splitlines())
        d = tempfile.mkdtemp(prefix="python-verdict-")
        self.addCleanup(shutil.rmtree, d, True)
        gh = os.path.join(d, "gh")
        with open(gh, "w") as f:   # applies the step's own --jq filter to the synthetic jobs, as gh would
            f.write('#!/usr/bin/env bash\ntouch "$FAKE_GH_CALLED"\n'
                    '[ -z "$FAKE_GH_FAIL" ] || { echo "gh: HTTP 502" >&2; exit 1; }\n'
                    'while [ $# -gt 0 ]; do [ "$1" = --jq ] && { filter=$2; shift; }; shift; done\n'
                    'exec jq -r "$filter" "$FAKE_JOBS"\n')
        os.chmod(gh, 0o755)
        cell = "Python 3.10 (ubuntu-latest)"

        def verdict(result, jobs, api_fails=False):
            with open(os.path.join(d, "jobs.json"), "w") as f:
                json.dump({"jobs": [{"name": n, "conclusion": c} for n, c in jobs]}, f)
            called = os.path.join(d, "called")
            if os.path.exists(called):
                os.remove(called)
            env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"], RESULT=result, CELL=cell,
                       GITHUB_REPOSITORY="example/repo", GITHUB_RUN_ID="1", FAKE_JOBS=os.path.join(d, "jobs.json"),
                       FAKE_GH_CALLED=called, FAKE_GH_FAIL="1" if api_fails else "")
            p = subprocess.run(["bash", "-e", "-c", script], env=env, cwd=d, capture_output=True, text=True, timeout=60)
            return p.returncode, os.path.exists(called)

        mine = [(cell + " shard 1", "success"), (cell + " shard 2", "success")]
        other = [("Python 3.13 (ubuntu-latest) shard 1", "success"), ("Python 3.13 (ubuntu-latest) shard 2", "failure")]
        self.assertEqual(verdict("success", []), (0, False), "every shard passed: green, and the API is never asked")
        self.assertEqual(verdict("failure", mine + other + [(cell, "success")])[0], 0,
                         "another cell's red shard leaves this cell's check green")
        for bad in ("failure", "cancelled", "skipped"):
            jobs = [(cell + " shard 1", "success"), (cell + " shard 2", bad)] + other
            self.assertNotEqual(verdict("failure", jobs)[0], 0, "a %s shard of this cell turns its check red" % bad)
        self.assertNotEqual(verdict("cancelled", other)[0], 0, "no shard of this cell found: red, never green")
        self.assertNotEqual(verdict("failure", mine, api_fails=True)[0], 0, "an API error reddens the check")


class Refresh(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="python-shards-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def _report(self, name, cases):
        path = os.path.join(self.d, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">%s</testsuite></testsuites>'
                    % "".join('<testcase classname="%s" name="%s" file="%s" time="%s"/>' % c for c in cases))

    def _refresh(self):
        path = os.path.join(self.d, "durations.json")
        with open(path, "w") as f:
            json.dump({}, f)
        r = subprocess.run([sys.executable, SCRIPT, "--durations", path, "--refresh", self.d],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path) as f:
            return json.load(f)

    def test_an_inherited_case_counts_toward_the_module_that_collected_it(self):
        # the xunit1 file attribute names the DEFINING file, so the base module would be credited with the subclass's run
        self._report("r/python-junit.xml", [
            ("tests.test_derived.Derived", "test_inherited", "tests/test_base.py", "4.0"),
            ("tests.test_derived.Derived", "test_own", "tests/test_derived.py", "1.0"),
            ("tests.test_derived", "test_function", "tests/test_derived.py", "0.5"),
            ("tests.test_base.Base", "test_inherited", "tests/test_base.py", "2.0"),
        ])
        self.assertEqual(self._refresh(), {"tests/test_base.py": 2.0, "tests/test_derived.py": 5.5})

    def test_a_file_every_version_reports_keeps_the_slowest_versions_time(self):
        self._report("python-junit-3.10-ubuntu-latest-1/python-junit.xml", [
            ("tests.test_a.A", "test_one", "tests/test_a.py", "3.0"), ("tests.test_a.A", "test_two", "tests/test_a.py", "2.0")])
        self._report("python-junit-3.13-ubuntu-latest-1/python-junit.xml", [
            ("tests.test_a.A", "test_one", "tests/test_a.py", "1.0"), ("tests.test_a.A", "test_two", "tests/test_a.py", "1.0")])
        self._report("python-junit-3.13-ubuntu-latest-2/python-junit.xml", [
            ("tests.test_b.B", "test_one", "tests/test_b.py", "7.0")])
        self.assertEqual(self._refresh(), {"tests/test_a.py": 5.0, "tests/test_b.py": 7.0},
                         "summed within a report, the slowest report across versions, never the sum of every version")


class IgnoreOthers(unittest.TestCase):
    def test_a_shard_ignores_exactly_the_files_dealt_to_the_other_shards(self):
        d = tempfile.mkdtemp(prefix="python-shards-")
        self.addCleanup(shutil.rmtree, d, True)
        path = os.path.join(d, "durations.json")
        files = ["tests/test_f%02d.py" % i for i in range(9)]
        with open(path, "w") as f:
            json.dump({f: float(i + 1) for i, f in enumerate(files[:7])}, f)   # two unrecorded files too
        mine = []
        for k in (1, 2, 3):
            base = [sys.executable, SCRIPT, "--durations", path, "--shard", str(k), "--of", "3"]
            listed = subprocess.run(base + files, capture_output=True, text=True, timeout=60)
            ignores = subprocess.run(base + ["--ignore-others"] + files, capture_output=True, text=True, timeout=60)
            self.assertEqual((listed.returncode, ignores.returncode), (0, 0), listed.stderr + ignores.stderr)
            own = listed.stdout.split()
            skipped = [line[len("--ignore="):] for line in ignores.stdout.split()]
            self.assertTrue(all(line.startswith("--ignore=") for line in ignores.stdout.split()))
            self.assertEqual(sorted(own + skipped), files, "shard %d runs its own files and ignores every other one" % k)
            mine += own
        self.assertEqual(sorted(mine), files, "every file runs in exactly one shard")

    def test_pytest_collects_nothing_under_tests_that_the_glob_does_not_name(self):
        # the shards run `pytest tests` minus the other shards' files, so a test module the glob misses would run in
        # every shard: a module in a subdirectory, or one named by pytest's other default pattern (*_test.py)
        stray = [os.path.relpath(os.path.join(d, n), REPO) for d, _, names in os.walk(os.path.join(REPO, "tests"))
                 for n in names if n.endswith(".py") and (n.startswith("test_") or n.endswith("_test.py"))
                 and not (d == os.path.join(REPO, "tests") and n.startswith("test_"))]
        self.assertEqual(stray, [])


class CommittedDurations(unittest.TestCase):
    def test_the_committed_durations_file_is_well_formed(self):
        durations = shard_tests.load(DURATIONS)
        self.assertTrue(durations, "scripts/ci/python-durations.json is empty: every test file would be dealt by count")
        for f, secs in durations.items():
            self.assertRegex(f, r"^tests/test_\w+\.py$")
            self.assertIsInstance(secs, (int, float))
            self.assertGreaterEqual(secs, 0)

    def test_every_test_file_in_the_tree_lands_in_exactly_one_of_the_two_shards(self):
        files = sorted(os.path.relpath(p, REPO) for p in glob.glob(os.path.join(REPO, "tests", "test_*.py")))
        shards = shard_tests.plan(files, shard_tests.load(DURATIONS), 2)[0]
        self.assertTrue(all(shards), "an empty shard")
        dealt = [f for s in shards for f in s]
        self.assertEqual(sorted(dealt), files)


if __name__ == "__main__":
    unittest.main()
