#!/usr/bin/env python3
"""Split CI's browser-backed served-page tests into parallel shards, balanced by recorded duration.

  shard_tests.py --shard K --of N FILE...   print shard K's files (1-based), one per line
  shard_tests.py --refresh REPORT...        rewrite the durations file from pytest JUnit XML reports
                                            (files, or directories searched for *.xml)

The split is deterministic. Files with a recorded duration are dealt longest first, each to the shard with the least
recorded time so far (ties go to the lower shard; equal durations go in name order). A file with no recorded duration
still runs: once the recorded files are dealt, each new file is dealt the same way at the median recorded duration,
and a GitHub warning names it. Dealing new files last keeps every recorded file on the shard it had.

Refreshing: every shard uploads its JUnit report as an artifact named served-junit-K. From a green run on main:
  gh run download RUN_ID --repo safety-research/romp --pattern 'served-junit-*' --dir /tmp/served-junit
  python3 scripts/ci/shard_tests.py --refresh /tmp/served-junit
The refresh replaces the entries of the files the reports name and keeps the rest while their files exist, so a file with
no tests of its own (a base class other labs import, which no report names) keeps the 0 it was given by hand.
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
              "scripts/ci/served-durations.json (see scripts/ci/shard_tests.py)"
              % (f, next(i for i, s in enumerate(out, 1) if f in s), args.of, estimate), file=sys.stderr)
    mine = out[args.shard - 1]
    if not mine:
        sys.exit("shard_tests: shard %d of %d is empty (more shards than files?)" % (args.shard, args.of))
    print("shard %d of %d: %d files, about %.0f s (every shard: %s s)"
          % (args.shard, args.of, len(mine), totals[args.shard - 1], ", ".join("%.0f" % t for t in totals)),
          file=sys.stderr)
    print("\n".join(sorted(mine)))


def refresh(args):
    reports = []
    for p in args.reports:
        if os.path.isdir(p):
            reports += [os.path.join(d, n) for d, _, names in os.walk(p) for n in names if n.endswith(".xml")]
        else:
            reports.append(p)
    if not reports:
        sys.exit("shard_tests: no JUnit reports found in %s" % args.reports)
    seen = defaultdict(float)
    for path in sorted(reports):
        for case in ET.parse(path).iter("testcase"):
            if case.get("file"):   # the served step writes xunit1 reports, which name each case's file
                seen[os.path.normpath(case.get("file"))] += float(case.get("time") or 0)
    if not seen:
        sys.exit("shard_tests: the reports name no files (were they written with -o junit_family=xunit1?)")
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
