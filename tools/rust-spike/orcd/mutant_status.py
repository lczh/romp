#!/usr/bin/env python3
"""One memo mutant task's result line, for mutants.sbatch (rust/SPIKE.md, S6.1): what each check said and the task's
status, with a build failure, a crash and a timeout kept apart from a caught mutant so the sweep's table cannot be
misread (S6.1's review: a panicking mutant used to read as not caught, and the sweep runs no tests).

    mutant_status.py TASK NAME_JSON BUILD_RC EDGE_RC EDGE_LINE FUZZ_RC FUZZ_LINE
    mutant_status.py --self-test

EDGE is `romp-spike memo memo_edge.json`: exit 0 passed, 1 differences (its `memo cases: ...` summary line counts
them), 2 an error it reported, 128 + N a signal (134 is the SIGABRT of a panic under panic = "abort"). FUZZ is
`dump_memo.py --fuzz`: 0 OK, 1 differences (its `memo fuzz seed S: FAILED, N differences` line) or a Python traceback,
2 when romp-spike exited without its summary line (the line then names the exit: negative for a signal). Either: 124 is
`timeout`'s, -1 not run (the build failed). Statuses: caught (a check found a difference), crashed (none did, but a
check's binary died), timedOut, missed (both passed), buildFailed, error; the control's is passed or failed.
"""
import json
import re
import sys

EDGE_DIFFS = re.compile(r"^memo cases.* (\d+) differences in \d+ steps")
FUZZ_DIFFS = re.compile(r"^memo fuzz seed \d+: (OK|FAILED), (\d+) differences")
EXITED = re.compile(r"exited (-?\d+) without its summary line")
TIMEOUT = 124


def edge_status(rc, line):
    if rc == -1:
        return "not run"
    if rc == 0:
        return "passed"
    m = EDGE_DIFFS.match(line)
    if rc == 1 and m and int(m.group(1)) > 0:
        return "differences"
    if rc == TIMEOUT:
        return "timedOut"
    if rc >= 128:
        return "crashed"
    return "error"


def fuzz_status(rc, line):
    if rc == -1:
        return "not run"
    if rc == 0:
        return "passed"
    m = FUZZ_DIFFS.match(line)
    if rc == 1 and m and m.group(1) == "FAILED" and int(m.group(2)) > 0:
        return "differences"
    if rc == TIMEOUT:
        return "timedOut"
    e = EXITED.search(line)
    if rc == 2 and e and (int(e.group(1)) < 0 or int(e.group(1)) >= 128):
        return "crashed"
    return "error"


def task_status(control, build, edge, fuzz):
    if build != 0:
        return "buildFailed"
    if control:
        return "passed" if edge == fuzz == "passed" else "failed"
    for status in ("differences", "crashed", "timedOut"):
        if status in (edge, fuzz):
            return "caught" if status == "differences" else status
    return "missed" if edge == fuzz == "passed" else "error"


def result(i, name, build, edge_rc, edge_line, fuzz_rc, fuzz_line):
    m = json.loads(name)
    control = m.get("mutant") == "control"
    build, edge_rc, fuzz_rc = int(build), int(edge_rc), int(fuzz_rc)
    edge, fuzz = edge_status(edge_rc, edge_line), fuzz_status(fuzz_rc, fuzz_line)
    status = task_status(control, build, edge, fuzz)
    return {"task": int(i), "mutant": m.get("mutant"), "name": m.get("name"), "status": status, "built": build == 0,
            "edge": {"status": edge, "rc": edge_rc, "summary": edge_line},
            "fuzz": {"status": fuzz, "rc": fuzz_rc, "summary": fuzz_line},
            "caught": None if control else status == "caught",
            "controlPasses": (status == "passed") if control else None}


def self_test():
    e0 = ("memo cases: 2 cases, 70 steps, 140 builds compared (279 session decisions: 193 hits, 84 misses, 6 raises), 61 "
          "memo-served builds equal to their fresh build, 7 stale where Python's are, 0 differences in 0 steps (2 fresh "
          "comparisons skipped where Python's frame differs; 41 ms)")
    e5 = e0.replace("0 differences in 0 steps", "15 differences in 1 steps")
    f0, f20 = "memo fuzz seed 1: OK, 0 differences", "memo fuzz seed 1: FAILED, 20 differences"
    crash = "dump_memo: romp-spike memo exited -6 without its summary line: "
    refused = "dump_memo: romp-spike memo exited 2 without its summary line: memo_fuzz.json: no cases"
    mut, ctl = json.dumps({"mutant": 3, "name": "x"}), json.dumps({"mutant": "control", "name": "y"})
    cases = [
        ((mut, 0, 1, e5, 0, f0), "caught", "differences", "passed"),
        ((mut, 0, 0, e0, 1, f20), "caught", "passed", "differences"),
        ((mut, 0, 134, "", 2, crash), "crashed", "crashed", "crashed"),
        ((mut, 0, 134, "", 1, f20), "caught", "crashed", "differences"),
        ((mut, 0, 0, e0, 0, f0), "missed", "passed", "passed"),
        ((mut, 101, -1, "", -1, ""), "buildFailed", "not run", "not run"),
        ((mut, 0, 0, e0, 1, "Traceback (most recent call last):"), "error", "passed", "error"),
        ((mut, 0, 2, "memo_edge.json: schema", 2, refused), "error", "error", "error"),
        ((mut, 0, 1, e0, 0, f0), "error", "error", "passed"),
        ((mut, 0, TIMEOUT, "", 0, f0), "timedOut", "timedOut", "passed"),
        ((ctl, 0, 0, e0, 0, f0), "passed", "passed", "passed"),
        ((ctl, 0, 1, e5, 0, f0), "failed", "differences", "passed"),
    ]
    bad = 0
    for (name, build, erc, eline, frc, fline), status, edge, fuzz in cases:
        r = result(7, name, build, erc, eline, frc, fline)
        got = (r["status"], r["edge"]["status"], r["fuzz"]["status"])
        if got != (status, edge, fuzz):
            bad += 1
            print("mutant_status: %r gave %r, expected %r" % ((erc, eline[:30], frc, fline[:30]), got, (status, edge, fuzz)))
    print("mutant_status self-test: %d cases, %d wrong" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    if len(sys.argv) != 8:
        sys.exit(__doc__)
    print(json.dumps(result(*sys.argv[1:])))
