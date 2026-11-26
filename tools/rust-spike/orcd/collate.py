#!/usr/bin/env python3
"""The pool checks of the Rust spike's cluster jobs (rust/SPIKE.md, S6.1; the runbook in setup.sh's header): whether a
job's outputs are this revision's, complete and intact, and the memo mutant sweep's table. Reads only; stdlib only.

    collate.py outputs --revision REV DIR...      a worlds, fuzz, measure or setup output directory each
    collate.py mutants --revision REV [--tasks 46,47] DIR     the mutants directory (default: every task, 0 to N)
    collate.py --self-test

`outputs`: each DIR's run.txt names REV on its first line (`revision <40 hex digits>`), its steps.tsv has at least one
row and every row's exit status (the second column) is 0, and its SHA256SUMS lists every file in DIR and each file
matches. A job that refused or failed early leaves none of these that pass: it set its earlier outputs aside before
any check that could fail (common.sh's set_aside), so an older run's files cannot stand in for it.

`mutants`: for each task I (0 to N-1 the mutants of tools/rust-spike/memo_mutants.py, N the control) I.json is there,
is one JSON object for task I, names REV, and I.sha256 verifies both I.json and I.log; the control's controlPasses is
true; no task's status is buildFailed or error. Then the table: each task's status and its two checks, the counts by
status, and every mutant not caught named. A missed mutant is a finding to explain (equivalent, or a gap in the
checks), not a failure of the sweep.

Exit status: 0 when every check holds, 1 when one fails (each failure printed), 2 on bad arguments.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
REV_RE = re.compile(r"\brevision ([0-9a-f]{40}|unknown)\b")
STATUSES = ("caught", "missed", "crashed", "timedOut", "buildFailed", "error", "passed", "failed")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def read_sums(path):
    """`sha256sum` lines: {relative path: hash}, or a reason the file cannot be read."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.rstrip("\n")
            m = re.fullmatch(r"([0-9a-f]{64}) [ *](.+)", line)
            if not m:
                raise ValueError("%s line %d is not a sha256sum line" % (os.path.basename(path), n))
            out[os.path.normpath(m.group(2))] = m.group(1)
    return out


def check_dir(d, rev):
    """The failures of one output directory (empty when it checks out), and a one-line summary."""
    bad = []
    if not os.path.isdir(d):
        return ["no such directory"], ""
    run = os.path.join(d, "run.txt")
    if not os.path.isfile(run):
        bad.append("no run.txt")
    else:
        with open(run, encoding="utf-8", errors="replace") as f:
            first = f.readline()
        m = REV_RE.search(first)
        if not m:
            bad.append("run.txt's first line names no revision")
        elif m.group(1) != rev:
            bad.append("run.txt names revision %s, not %s" % (m.group(1), rev))
    steps = os.path.join(d, "steps.tsv")
    rows = []
    if not os.path.isfile(steps):
        bad.append("no steps.tsv")
    else:
        with open(steps, encoding="utf-8", errors="replace") as f:
            rows = [ln.rstrip("\n").split("\t") for ln in f if ln.strip()]
        if not rows:
            bad.append("steps.tsv has no rows (no step ran)")
        failed = [r[0] + " (" + (r[1] if len(r) > 1 else "?") + ")" for r in rows if len(r) < 2 or r[1] != "0"]
        if failed:
            bad.append("steps that did not exit 0: " + ", ".join(failed))
    sums = os.path.join(d, "SHA256SUMS")
    files = 0
    if not os.path.isfile(sums):
        bad.append("no SHA256SUMS (the job did not seal its outputs)")
    else:
        try:
            listed = read_sums(sums)
        except ValueError as e:
            listed = {}
            bad.append(str(e))
        have = set()
        for root, _dirs, names in os.walk(d):
            for n in names:
                rel = os.path.normpath(os.path.relpath(os.path.join(root, n), d))
                if rel != "SHA256SUMS":
                    have.add(rel)
        files = len(have)
        if have - set(listed):
            bad.append("files SHA256SUMS does not list: " + ", ".join(sorted(have - set(listed))[:5]))
        if set(listed) - have:
            bad.append("files SHA256SUMS lists that are gone: " + ", ".join(sorted(set(listed) - have)[:5]))
        changed = sorted(p for p in have & set(listed) if sha256(os.path.join(d, p)) != listed[p])
        if changed:
            bad.append("files that do not match SHA256SUMS: " + ", ".join(changed[:5]))
    return bad, "%d steps, %d files" % (len(rows), files)


def outputs(rev, dirs):
    nbad = 0
    for d in dirs:
        bad, summary = check_dir(d, rev)
        if bad:
            nbad += 1
            print("FAIL %s: %s" % (d, "; ".join(bad)))
        else:
            print("ok   %s: %s, revision %s" % (d, summary, rev[:12]))
    print("outputs: %d directories, %d failing" % (len(dirs), nbad))
    return 1 if nbad else 0


def mutant_count():
    spec = importlib.util.spec_from_file_location("romp_spike_memo_mutants", os.path.join(HERE, "..", "memo_mutants.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return len(mod.MUTANTS)


def load_task(d, i, rev, n):
    """(the task's JSON or None, its failures)."""
    bad = []
    js, log, sums = (os.path.join(d, "%d.%s" % (i, ext)) for ext in ("json", "log", "sha256"))
    if not os.path.isfile(js):
        return None, ["no %d.json" % i]
    try:
        with open(js, encoding="utf-8") as f:
            r = json.loads(f.read())
        if not isinstance(r, dict):
            raise ValueError("not an object")
    except ValueError as e:
        return None, ["%d.json does not parse as one JSON object (%s)" % (i, e)]
    if not os.path.isfile(sums):
        bad.append("no %d.sha256" % i)
    else:
        try:
            listed = read_sums(sums)
        except ValueError as e:
            listed = {}
            bad.append(str(e))
        for p in ("%d.json" % i, "%d.log" % i):
            path = os.path.join(d, p)
            if p not in listed:
                bad.append("%d.sha256 does not list %s" % (i, p))
            elif not os.path.isfile(path) or sha256(path) != listed[p]:
                bad.append("%s does not match %d.sha256" % (p, i))
    if r.get("task") != i:
        bad.append("%d.json is task %r" % (i, r.get("task")))
    if r.get("revision") != rev:
        bad.append("%d.json names revision %r, not %s" % (i, r.get("revision"), rev))
    want = "control" if i == n else i
    if r.get("mutant") != want:
        bad.append("%d.json is mutant %r, expected %r" % (i, r.get("mutant"), want))
    if r.get("status") not in STATUSES:
        bad.append("%d.json has status %r" % (i, r.get("status")))
    if i == n and r.get("controlPasses") is not True:
        bad.append("the control (task %d) did not pass: status %r, edge %r, fuzz %r" % (
            i, r.get("status"), (r.get("edge") or {}).get("status"), (r.get("fuzz") or {}).get("status")))
    if r.get("status") in ("buildFailed", "error"):
        bad.append("task %d's status is %s" % (i, r.get("status")))
    return r, bad


def mutants(rev, d, tasks=None, n=None):
    n = mutant_count() if n is None else n
    tasks = list(range(n + 1)) if tasks is None else tasks
    out_of_range = [i for i in tasks if not 0 <= i <= n]
    if out_of_range:
        print("collate: tasks %r: the sweep has tasks 0 to %d" % (out_of_range, n))
        return 2
    bad, counts, rows = [], {}, []
    for i in tasks:
        r, b = load_task(d, i, rev, n)
        bad += b
        if r is None:
            continue
        counts[r.get("status")] = counts.get(r.get("status"), 0) + 1
        rows.append(r)
    print("mutant sweep in %s, revision %s: %d of %d tasks (%d mutants and the control)" % (
        d, rev[:12], len(rows), len(tasks), n))
    for r in rows:
        e, f = r.get("edge") or {}, r.get("fuzz") or {}
        print("  %3s  %-11s  edge %-11s %4s  fuzz %-11s %4s  %s" % (
            r.get("task"), r.get("status"), e.get("status"), e.get("rc"), f.get("status"), f.get("rc"), r.get("name")))
    print("status counts: %s" % json.dumps(dict(sorted(counts.items()))))
    for r in rows:
        if r.get("mutant") != "control" and r.get("status") != "caught":
            print("not caught: mutant %s (%s): %s" % (r.get("task"), r.get("status"), r.get("name")))
    for b in bad:
        print("FAIL " + b)
    print("mutants: %s" % ("FAILED, %d problems" % len(bad) if bad else "OK"))
    return 1 if bad else 0


# ───────────────────────────── self-test ─────────────────────────────
def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _seal(d):
    lines = []
    for root, _dirs, names in os.walk(d):
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(root, n), d)
            if rel != "SHA256SUMS":
                lines.append("%s  ./%s\n" % (sha256(os.path.join(d, rel)), rel))
    _write(os.path.join(d, "SHA256SUMS"), "".join(sorted(lines, key=lambda s: s.split("  ", 1)[1])))


def _good_dir(d, rev):
    _write(os.path.join(d, "run.txt"), "world small, revision %s, node, 2026-11-26T00:00:00Z\nworld small: 2 steps\n" % rev)
    _write(os.path.join(d, "steps.tsv"), "dump\t0\t3\tT\nfeed\t0\t1\tT\n")
    _write(os.path.join(d, "feed.txt"), "feed: identical\n")
    _write(os.path.join(d, "sub", "x.json"), "{}\n")
    _seal(d)


def _task(d, i, rev, n, status="caught", control_passes=None, seal=True):
    r = {"task": i, "mutant": "control" if i == n else i, "name": "m%d" % i, "revision": rev, "status": status,
         "edge": {"status": "passed", "rc": 0}, "fuzz": {"status": "differences", "rc": 1},
         "caught": None if i == n else status == "caught", "controlPasses": control_passes if i == n else None}
    _write(os.path.join(d, "%d.json" % i), json.dumps(r) + "\n")
    _write(os.path.join(d, "%d.log" % i), "task %d\n" % i)
    if seal:
        _write(os.path.join(d, "%d.sha256" % i), "".join("%s  %d.%s\n" % (sha256(os.path.join(d, "%d.%s" % (i, e))), i, e)
                                                       for e in ("json", "log")))


def self_test():
    import contextlib
    import io
    rev, old = "a" * 40, "b" * 40
    tmp = tempfile.mkdtemp(prefix="collate-self-test-")
    cases = []

    def quiet(fn, *a, **k):
        with contextlib.redirect_stdout(io.StringIO()):
            return fn(*a, **k)
    try:
        def dir_case(label, mutate, expect):
            d = os.path.join(tmp, "out", label.replace(" ", "-"))
            _good_dir(d, rev)
            mutate(d)
            cases.append((label, quiet(outputs, rev, [d]), expect))
        dir_case("good", lambda d: None, 0)
        dir_case("earlier revision", lambda d: (_good_dir(d, old)), 1)
        dir_case("unknown revision", lambda d: (_write(os.path.join(d, "run.txt"), "x, revision unknown\n"), _seal(d)), 1)
        dir_case("step failed", lambda d: (_write(os.path.join(d, "steps.tsv"), "dump\t0\t3\tT\nfeed\t2\t1\tT\n"),
                                           _seal(d)), 1)
        dir_case("no steps", lambda d: (_write(os.path.join(d, "steps.tsv"), ""), _seal(d)), 1)
        dir_case("not sealed", lambda d: os.remove(os.path.join(d, "SHA256SUMS")), 1)
        dir_case("unlisted file", lambda d: _write(os.path.join(d, "late.txt"), "x\n"), 1)
        dir_case("changed file", lambda d: _write(os.path.join(d, "feed.txt"), "feed: 3 differences\n"), 1)
        dir_case("missing file", lambda d: os.remove(os.path.join(d, "sub", "x.json")), 1)
        dir_case("no run.txt", lambda d: (os.remove(os.path.join(d, "run.txt")), _seal(d)), 1)
        cases.append(("no directory", quiet(outputs, rev, [os.path.join(tmp, "absent")]), 1))

        n = 3

        def sweep_case(label, mutate, expect, tasks=None):
            d = os.path.join(tmp, "mut", label.replace(" ", "-"))
            for i in range(n):
                _task(d, i, rev, n, status="missed" if i == 1 else "caught")
            _task(d, n, rev, n, status="passed", control_passes=True)
            mutate(d)
            cases.append((label, quiet(mutants, rev, d, tasks=tasks, n=n), expect))
        sweep_case("sweep good", lambda d: None, 0)
        sweep_case("sweep subset", lambda d: os.remove(os.path.join(d, "0.json")), 0, tasks=[2, 3])
        sweep_case("sweep missing json", lambda d: os.remove(os.path.join(d, "2.json")), 1)
        sweep_case("sweep earlier revision", lambda d: _task(d, 0, old, n), 1)
        sweep_case("sweep control failed", lambda d: _task(d, n, rev, n, status="failed", control_passes=False), 1)
        sweep_case("sweep build failed", lambda d: _task(d, 2, rev, n, status="buildFailed"), 1)
        sweep_case("sweep error", lambda d: _task(d, 2, rev, n, status="error"), 1)
        sweep_case("sweep no sha256", lambda d: os.remove(os.path.join(d, "1.sha256")), 1)
        sweep_case("sweep changed log", lambda d: _write(os.path.join(d, "1.log"), "edited\n"), 1)
        sweep_case("sweep wrong task", lambda d: os.replace(os.path.join(d, "0.json"), os.path.join(d, "2.json")), 1)
        sweep_case("sweep task out of range", lambda d: None, 2, tasks=[4])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    wrong = [(label, got, want) for label, got, want in cases if got != want]
    for label, got, want in wrong:
        print("collate self-test: %s gave %d, expected %d" % (label, got, want))
    print("collate self-test: %d cases, %d wrong" % (len(cases), len(wrong)))
    return 1 if wrong else 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-test"]:
        return self_test()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="what", required=True)
    o = sub.add_parser("outputs")
    o.add_argument("--revision", required=True)
    o.add_argument("dirs", nargs="+")
    m = sub.add_parser("mutants")
    m.add_argument("--revision", required=True)
    m.add_argument("--tasks", help="a comma-separated subset (the smoke pass's), default every task")
    m.add_argument("dir")
    a = ap.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", a.revision):
        print("collate: --revision %r is not a 40-digit commit id (the REVISION file beside the code)" % a.revision)
        return 2
    if a.what == "outputs":
        return outputs(a.revision, a.dirs)
    try:
        tasks = [int(t) for t in a.tasks.split(",")] if a.tasks else None
    except ValueError:
        print("collate: --tasks %r is not a list of task numbers" % a.tasks)
        return 2
    return mutants(a.revision, a.dir, tasks)


if __name__ == "__main__":
    sys.exit(main())
