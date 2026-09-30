#!/usr/bin/env python3
"""Split one of CI's pytest suites into parallel shards, balanced by recorded duration.

  shard_tests.py [--durations FILE] --shard K --of N FILE...   print shard K's files (1-based), one per line
       ... --ignore-others ...                                 or --ignore=FILE for every file of the other shards
  shard_tests.py [--durations FILE] --refresh REPORT...        rewrite the durations file from pytest JUnit XML
                                                               reports (files, or directories searched for *.xml)

Each suite has its own durations file: served-durations.json (the default) for the browser-backed served-page tests,
python-durations.json for the Python suite, which each Python version runs whole, in shards of its own. The Python
shards run `pytest tests` with --ignore-others rather than a list of files: pytest collects a long list of file
arguments inside a package far more slowly than the directory (about 100 s for a shard's 467 files on a runner,
2026-09-30, against about 30 s for all 935 as the directory).

The split is deterministic. Files with a recorded duration are dealt longest first, each to the shard with the least
recorded time so far (ties go to the lower shard; equal durations go in name order). A file with no recorded duration
still runs: once the recorded files are dealt, each new file is dealt the same way at the median recorded duration,
and a GitHub warning names it. Dealing new files last keeps every recorded file on the shard it had.

Refreshing: every shard uploads its JUnit report as an artifact (served-junit-K; python-junit-VERSION-OS-K). From a
green run on main:
  gh run download RUN_ID --repo safety-research/romp --pattern 'served-junit-*' --dir /tmp/served-junit
  python3 scripts/ci/shard_tests.py --refresh /tmp/served-junit
  gh run download RUN_ID --repo safety-research/romp --pattern 'python-junit-*' --dir /tmp/python-junit
  python3 scripts/ci/shard_tests.py --durations scripts/ci/python-durations.json --refresh /tmp/python-junit
A case counts toward the file that collected it (its class's module), and a file several reports name (each Python
version runs every file) keeps the slowest. The refresh replaces the entries of the files the reports name and keeps the
rest while their files exist, so a file with no tests of its own (a base class other labs import, which no report names)
keeps the 0 it was given by hand.
Standard library only, so CI runs it before any install.
"""
import argparse
import json
import os
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DURATIONS = os.path.join(HERE, "served-durations.json")


def load(path=DURATIONS):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def plan(files, durations, shards):
    """Deal files to `shards` lists. Returns (shards, totals, unrecorded files, the estimate used for them)."""
    files = sorted({os.path.normpath(f) for f in files})
    known = [f for f in files if f in durations]
    new = [f for f in files if f not in durations]
    estimate = statistics.median(durations[f] for f in known) if known else 1.0
    out, totals = [[] for _ in range(shards)], [0.0] * shards
    for f, secs in sorted(((f, durations[f]) for f in known), key=lambda x: (-x[1], x[0])) + [(f, estimate) for f in new]:
        k = min(range(shards), key=lambda i: (totals[i], i))
        out[k].append(f)
        totals[k] += secs
    return out, totals, new, estimate


def shard(args):
    if not 1 <= args.shard <= args.of:
        sys.exit("shard_tests: --shard must be between 1 and --of (%d)" % args.of)
    out, totals, new, estimate = plan(args.files, load(args.durations), args.of)
    for f in new:
        print("::warning file=%s::no recorded duration, so shard %d of %d runs it at an estimated %.0f s; refresh "
              "%s (see scripts/ci/shard_tests.py)"
              % (f, next(i for i, s in enumerate(out, 1) if f in s), args.of, estimate,
                 os.path.relpath(args.durations, REPO)), file=sys.stderr)
    mine = out[args.shard - 1]
    if not mine:
        sys.exit("shard_tests: shard %d of %d is empty (more shards than files?)" % (args.shard, args.of))
    print("shard %d of %d: %d files, about %.0f s (every shard: %s s)"
          % (args.shard, args.of, len(mine), totals[args.shard - 1], ", ".join("%.0f" % t for t in totals)),
          file=sys.stderr)
    if args.ignore_others:
        print("\n".join("--ignore=" + f for i, fs in enumerate(out, 1) if i != args.shard for f in sorted(fs)))
    else:
        print("\n".join(sorted(mine)))


def collected_by(classname):
    """The file that collected a case, from its JUnit classname: tests.test_x.Lab -> tests/test_x.py. Not the xunit1
    file attribute, which names the file that DEFINES the test, so an inherited test would count toward its base."""
    parts = classname.split(".")
    for i, part in enumerate(parts):
        if part.startswith("test_"):
            return os.path.join(*parts[:i + 1]) + ".py"
    return None


def refresh(args):
    reports = []
    for p in args.reports:
        if os.path.isdir(p):
            reports += [os.path.join(d, n) for d, _, names in os.walk(p) for n in names if n.endswith(".xml")]
        else:
            reports.append(p)
    if not reports:
        sys.exit("shard_tests: no JUnit reports found in %s" % args.reports)
    seen = {}
    for path in sorted(reports):
        mine = defaultdict(float)
        for case in ET.parse(path).iter("testcase"):
            f = collected_by(case.get("classname") or "")
            if f:
                mine[f] += float(case.get("time") or 0)
        for f, secs in mine.items():   # each Python version's report names every file: keep the slowest
            seen[f] = max(seen.get(f, 0.0), secs)
    if not seen:
        sys.exit("shard_tests: the reports name no test modules")
    old = load(args.durations)
    merged = {f: s for f, s in old.items() if os.path.exists(os.path.join(REPO, f))}   # a deleted file's entry goes
    merged.update({f: round(s, 1) for f, s in seen.items()})
    with open(args.durations, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(merged.items())), f, indent=1)
        f.write("\n")
    print("wrote %d files (%d from these reports, %.0f s in total) to %s"
          % (len(merged), len(seen), sum(seen.values()), args.durations), file=sys.stderr)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--durations", default=DURATIONS, help="the durations file (default: %(default)s)")
    p.add_argument("--shard", type=int)
    p.add_argument("--of", type=int)
    p.add_argument("--ignore-others", action="store_true",
                   help="print --ignore=FILE for every file dealt to another shard, for `pytest DIR` to skip")
    p.add_argument("--refresh", nargs="+", metavar="REPORT", dest="reports")
    p.add_argument("files", nargs="*")
    args = p.parse_args(argv)
    if args.reports:
        refresh(args)
    elif args.shard and args.of and args.files:
        shard(args)
    else:
        p.error("give --shard K --of N FILE..., or --refresh REPORT...")


if __name__ == "__main__":
    main()
