#!/usr/bin/env python3
"""One memo mutant task's result line, for mutants.sbatch (rust/SPIKE.md, S6.1): what each check said and the task's
status, with a build failure, a crash and a timeout kept apart from a caught mutant so the sweep's table cannot be
misread (S6.1's review: a panicking mutant used to read as not caught, and the sweep runs no tests).

    mutant_status.py TASK NAME_JSON BUILD_RC EDGE_RC EDGE_LINE FUZZ_RC FUZZ_LINE REVISION
    mutant_status.py --self-test

EDGE is `romp-spike memo memo_edge.json`: exit 0 with its `memo cases: ... 0 differences in 0 steps` summary line
passed, 1 differences (the summary line counts them), 2 an error it reported, 128 + N a signal (134 is the SIGABRT of a
panic under panic = "abort"). FUZZ is `dump_memo.py --fuzz`: exit 0 with its `memo fuzz seed S: OK, 0 differences` line
passed, 1 differences (its `memo fuzz seed S: FAILED, N differences` line) or a Python traceback, 2 when romp-spike
exited without its summary line (the line then names the exit: negative for a signal). Either: 124 is `timeout`'s, -1
not run (the build failed). A check passes only on exit 0 AND its clean summary line (an exit 0 without it is an error).
Statuses: caught (a check found a difference), crashed (none did, but a check's binary died), timedOut, missed (both
passed), buildFailed, error; the control's is passed or failed. The result line carries REVISION (the code's commit).

Exit status: 0 for a result (caught, missed, crashed, timedOut, and the control's passed), 1 for buildFailed, error and a
control that did not pass, so the array task shows FAILED in sacct and collate.py names it.
"""
import json
import re
import sys

EDGE_DIFFS = re.compile(r"^memo cases.* (\d+) differences in \d+ steps")
FUZZ_DIFFS = re.compile(r"^memo fuzz seed \d+: (OK|FAILED), (\d+) differences")
EXITED = re.compile(r"exited (-?\d+) without its summary line")
TIMEOUT = 124
RESULTS = ("caught", "missed", "crashed", "timedOut", "passed")   # a task with any other status exits 1


def edge_status(rc, line):
    if rc == -1:
        return "not run"
    m = EDGE_DIFFS.match(line)
    if rc == 0:
        return "passed" if m and int(m.group(1)) == 0 else "error"
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
    m = FUZZ_DIFFS.match(line)
    if rc == 0:
        return "passed" if m and m.group(1) == "OK" and int(m.group(2)) == 0 else "error"
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


def result(i, name, build, edge_rc, edge_line, fuzz_rc, fuzz_line, revision):
    m = json.loads(name)
    control = m.get("mutant") == "control"
    build, edge_rc, fuzz_rc = int(build), int(edge_rc), int(fuzz_rc)
    edge, fuzz = edge_status(edge_rc, edge_line), fuzz_status(fuzz_rc, fuzz_line)
    status = task_status(control, build, edge, fuzz)
    return {"task": int(i), "mutant": m.get("mutant"), "name": m.get("name"), "revision": revision, "status": status,
            "built": build == 0,
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
        # an exit 0 counts only with its clean summary line (S6.1's second review)
        ((mut, 0, 0, "", 0, f0), "error", "error", "passed"),
        ((mut, 0, 0, e5, 0, f0), "error", "error", "passed"),
        ((mut, 0, 0, e0, 0, ""), "error", "passed", "error"),
        ((mut, 0, 0, e0, 0, f20), "error", "passed", "error"),
        ((mut, 0, 0, e0, 0, "memo fuzz seed 1: OK, 3 differences"), "error", "passed", "error"),
        ((mut, 0, 0, e0, 0, crash), "error", "passed", "error"),
        ((ctl, 0, 0, "", 0, f0), "failed", "error", "passed"),
        ((ctl, 0, 0, e0, 0, "memo fuzz seed 1: FAILED, 0 differences"), "failed", "passed", "error"),
        ((ctl, 101, -1, "", -1, ""), "buildFailed", "not run", "not run"),
    ]
    bad = 0
    for (name, build, erc, eline, frc, fline), status, edge, fuzz in cases:
        r = result(7, name, build, erc, eline, frc, fline, "0" * 40)
        got = (r["status"], r["edge"]["status"], r["fuzz"]["status"])
        if got != (status, edge, fuzz) or r["revision"] != "0" * 40:
            bad += 1
            print("mutant_status: %r gave %r, expected %r" % ((erc, eline[:30], frc, fline[:30]), got, (status, edge, fuzz)))
    print("mutant_status self-test: %d cases, %d wrong" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-test"]:
        sys.exit(self_test())
    if len(sys.argv) != 9:
        sys.exit(__doc__)
    r = result(*sys.argv[1:])
    print(json.dumps(r))
    sys.exit(0 if r["status"] in RESULTS else 1)
