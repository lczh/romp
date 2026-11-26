#!/usr/bin/env python3
"""Step S6's alternating driver (rust/SPIKE.md, "S6 method"): runs Python's measurement (tools/rust-spike/measure.py) and
Rust's (`romp-spike measure`) in turn, N times on the same dump, each run a fresh process, and keeps every run's raw
numbers in one JSON file, with a summary computed from them.

    python3 tools/rust-spike/measure_drive.py --dump DIR [--dump DIR ...] [--runs N] [--python EXE ...]
        [--cpu] [--mem] [--memo] [--builds K] [--instrumented M] [--appends K] [--schedule active|all] [--active N]
        [--growth blocks,double] [--relief] [--mem-dir DIR ...] [--perf FILE [--perf FILE]] [--binary PATH] [--out FILE]
    python3 tools/rust-spike/measure_drive.py --summarize FILE

Order: run i measures every side once per dump. Even runs go Python first (each interpreter in the order given), then
Rust; odd runs go Rust first, then the interpreters in reverse, so neither side always meets the machine first. Every run
records the load average before and after it. `--cpu` (the default when neither is given) times the feed build; `--mem`
measures the record cache and the store: per run, for each interpreter a Python run without tracemalloc (the process's
memory), one with --relief when asked, and one with tracemalloc (the traced bytes); for each growth policy a Rust run,
and one with --relief when asked. Each runs on its own fresh copy of the transcripts with mtimes near now and appends
the same bytes: K rounds of the schedule. `--schedule active` (the default) appends to the N largest files only (`--active`,
default 4), one to four of the file's own records per round, picked at random with a fixed seed so their sizes vary;
`--schedule all` appends every file's last line every round (S3's check). `--mem-dir DIR` measures a directory of
transcripts besides the dumps' (read-only: it is copied first); real transcripts need lczh's OK. Copies live under one
scratch directory in the system temp dir, removed at the end. `--perf FILE` (measure.py perf's output) folds the live
kernel's memo counters into the summary: given twice (a snapshot before and after an active window), the difference;
given once, the counters since the kernel booted (flagged). `--memo` (step S6.1) adds both sides' memo rows to every CPU
run (measure.py --memo: the real key over the recorded facts; romp-spike measure --memo: the port's memo) and the
summary's memo comparisons: hit against hit, one miss on the largest session against the same, and the production blend
with both sides memoized.

Nothing is written inside the repo. The output defaults to <system temp>/romp-s6-measure-<UTC stamp>.json; it names
interpreters by version and architecture and dumps by directory name, never by a path under the home directory.
"""
import argparse
import json
import os
import platform
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
MEASURE = os.path.join(HERE, "measure.py")
SCHEMA = "romp-spike-measure-drive/2"
TIMEOUT_S = 900


class DriveError(Exception):
    pass


def utc():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def interp_label(exe):
    code = ("import json, platform, sys; print(json.dumps({'version': platform.python_version(), "
            "'machine': platform.machine(), 'implementation': platform.python_implementation()}))")
    r = subprocess.run([exe, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise DriveError("cannot run %s: %s" % (exe, r.stderr.strip()[-300:]))
    info = json.loads(r.stdout)
    info["label"] = "%s-%s-%s" % (info["implementation"].lower(), info["version"], info["machine"])
    return info


def git_rev():
    """The code's revision: the checkout's HEAD and whether tracked files differ from it; else, for a copy without .git
    (the cluster, where tools/rust-spike/orcd/setup.sh's runbook ships `git archive` of one commit), the revision
    orcd/common.sh exports as ROMP_SPIKE_REVISION ("dirty" then unknown: null). Refuses with neither, so no run file
    goes unattributed, and when the two disagree."""
    given = os.environ.get("ROMP_SPIKE_REVISION", "").strip()
    if given and not (12 <= len(given) <= 40 and all(c in "0123456789abcdef" for c in given)):
        raise DriveError("ROMP_SPIKE_REVISION=%r is not a git revision (12 to 40 lowercase hex digits)" % given)
    rev = ""
    if os.path.exists(os.path.join(REPO, ".git")):
        try:
            p = subprocess.run(["git", "-C", REPO, "rev-parse", "--short=12", "HEAD"], capture_output=True, text=True,
                               timeout=30)
            rev = p.stdout.strip() if p.returncode == 0 else ""
            if rev:
                dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--untracked-files=no"],
                                       capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            rev = ""
    if rev:
        if given and given[:len(rev)] != rev:
            raise DriveError("the checkout's HEAD is %s but ROMP_SPIKE_REVISION names %s" % (rev, given))
        return {"rev": rev, "dirty": bool(dirty)}
    if given:
        return {"rev": given[:12], "dirty": None, "source": "ROMP_SPIKE_REVISION"}
    raise DriveError("no revision to record: %s has no git checkout and ROMP_SPIKE_REVISION is not set (on the cluster, "
                     "orcd/common.sh exports it from the REVISION file beside the code)" % REPO)


def machine():
    out = {"machine": platform.machine(), "system": platform.system(), "release": platform.release(),
           "cpus": os.cpu_count()}
    try:
        out["memBytes"] = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                             timeout=10).stdout.strip())
        out["cpuBrand"] = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                                         text=True, timeout=10).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return out


def dump_info(d):
    with open(os.path.join(d, "snapshot.json"), encoding="utf-8") as f:
        snap = json.load(f)
    tx = os.path.join(d, "transcripts")
    names = sorted(os.listdir(tx))
    return {"label": os.path.basename(os.path.normpath(d)), "alive": len(snap["alive"]), "transcripts": len(names),
            "transcriptBytes": sum(os.path.getsize(os.path.join(tx, n)) for n in names)}


def last_line(path):
    """The file's last complete line, with its newline: the bytes both sides append per round."""
    size = os.path.getsize(path)
    want = 1 << 16
    with open(path, "rb") as f:
        while True:
            start = max(0, size - want)
            f.seek(start)
            tail = f.read(size - start)
            if not tail.endswith(b"\n"):
                raise DriveError("%s does not end with a newline" % path)
            i = tail.rfind(b"\n", 0, len(tail) - 1)
            if i >= 0:
                return tail[i + 1:]
            if start == 0:
                return tail
            want *= 4


def record_lines(path):
    """The file's complete lines that are JSON objects (the records both readers keep), with their newlines."""
    out = []
    with open(path, "rb") as f:
        for ln in f:
            if not ln.endswith(b"\n"):
                continue
            try:
                if isinstance(json.loads(ln), dict):
                    out.append(ln)
            except ValueError:
                pass
    return out


def make_payload(tx, dst, rounds, schedule, active):
    """The append schedule: dst/<round>/<file name> holds the bytes appended to that file in that round (rounds from 1;
    a file with no payload in a round is not appended). `all`: every file's last line, every round. `active`: the
    `active` largest files only, each round one to four of the file's own records picked at random under a fixed seed
    (so the sizes vary as real appends do). Returns the schedule's description."""
    names = sorted(os.listdir(tx))
    sizes = {n: os.path.getsize(os.path.join(tx, n)) for n in names}
    if schedule == "all":
        chosen = names
        tails = {n: last_line(os.path.join(tx, n)) for n in names}
    else:
        chosen = sorted(names, key=lambda n: (-sizes[n], n))[:active]
        pools = {n: record_lines(os.path.join(tx, n)) for n in chosen}
    per_round = []
    for r in range(1, rounds + 1):
        rd = os.path.join(dst, str(r))
        os.makedirs(rd)
        total = recs = 0
        for n in chosen:
            if schedule == "all":
                data = tails[n]
                k = 1
            else:
                rng = random.Random("%s/%d" % (n, r))
                k = rng.randint(1, 4)
                data = b"".join(rng.choice(pools[n]) for _ in range(k)) if pools[n] else b""
            with open(os.path.join(rd, n), "wb") as f:
                f.write(data)
            total += len(data)
            recs += k
        per_round.append({"round": r, "bytes": total, "records": recs})
    return {"schedule": schedule, "rounds": rounds, "files": len(names), "fileBytes": sum(sizes.values()),
            "appendedTo": len(chosen), "appendedToBytes": sum(sizes[n] for n in chosen), "perRound": per_round}


def fresh_copy(tx, dst):
    """A copy of the transcripts with mtimes near now, in name order a millisecond apart, so the record cache keeps
    every entry (a file pinned in the past reads as quiescent: s2-design section 4, item 6)."""
    os.makedirs(dst)
    now = time.time()
    for i, n in enumerate(sorted(os.listdir(tx))):
        p = os.path.join(dst, n)
        shutil.copyfile(os.path.join(tx, n), p)
        t = now - 5 - i * 0.001
        os.utime(p, (t, t))


def run_child(argv, env=None):
    t0 = time.monotonic()
    before = os.getloadavg()
    r = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S, env=env)
    rec = {"startedUtc": None, "loadBefore": [round(x, 2) for x in before],
           "loadAfter": [round(x, 2) for x in os.getloadavg()], "wallS": round(time.monotonic() - t0, 3),
           "rc": r.returncode, "stderr": r.stderr[-2000:]}
    lines = [ln for ln in r.stdout.splitlines() if ln.strip()]
    try:
        rec["result"] = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        rec["result"] = None
        rec["stdoutTail"] = r.stdout[-2000:]
    return rec


def child_env():
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def drive(a):
    pythons = a.python or [sys.executable]
    binary = os.path.realpath(a.binary)
    if not os.path.isfile(binary):
        raise DriveError("no romp-spike binary at %s (cargo build --offline --release -p romp-spike)" % binary)
    interps = [(exe, interp_label(exe)) for exe in pythons]
    dumps = [os.path.realpath(d) for d in a.dump]
    doc = {"schema": SCHEMA, "startedUtc": utc(), "machine": machine(), "git": git_rev(),
           "settings": {"runs": a.runs, "cpu": a.cpu, "mem": a.mem, "builds": a.builds, "instrumented": a.instrumented,
                        "appends": a.appends, "schedule": a.schedule, "active": a.active, "growth": a.growth,
                        "relief": a.relief, "memo": a.memo},
           "interpreters": [i for _e, i in interps], "dumps": [dump_info(d) for d in dumps], "runs": []}
    if a.perf:
        doc["perf"] = []
        for pf in a.perf:
            with open(pf, encoding="utf-8") as f:
                doc["perf"].append(json.load(f))
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="romp-s6-drive-"))
    env = child_env()
    try:
        payloads, sources = {}, []
        if a.mem:
            sources = [(os.path.basename(d), os.path.join(d, "transcripts")) for d in dumps]
            sources += [("dir-" + os.path.basename(os.path.normpath(m)), os.path.realpath(m)) for m in a.mem_dir]
            doc["memSources"] = {}
            for label, tx in sources:
                payloads[label] = os.path.join(scratch, "payload-" + label)
                doc["memSources"][label] = make_payload(tx, payloads[label], a.appends, a.schedule, a.active)
        growths = [g for g in a.growth.split(",") if g]
        for i in range(a.runs):
            for d in dumps:
                label = os.path.basename(d)
                if a.cpu:
                    sides = [("python", exe, info["label"]) for exe, info in interps] + [("rust", binary, "rust")]
                    if i % 2:
                        sides.reverse()
                    for pos, (side, exe, lab) in enumerate(sides):
                        if side == "python":
                            argv = [exe, "-I", MEASURE, "cpu", d, "--builds", str(a.builds), "--instrumented",
                                    str(a.instrumented)] + (["--memo"] if a.memo else [])
                        else:
                            argv = [exe, "measure", "cpu", d, "--builds", str(a.builds)] + (["--memo"] if a.memo else [])
                        started = utc()
                        rec = run_child(argv, env)
                        rec.update(run=i, kind="cpu", dump=label, side=side, interp=lab, order=pos, startedUtc=started)
                        doc["runs"].append(rec)
                        say(rec)
            for label, tx in (sources if a.mem else []):
                variants = [False, True] if a.relief else [False]
                sides = []
                for exe, info in interps:
                    sides += [("python", exe, info["label"], False, rl, None) for rl in variants]
                    sides.append(("python", exe, info["label"], True, False, None))
                for g in growths:
                    sides += [("rust", binary, "rust-" + g, False, rl, g) for rl in variants]
                if i % 2:
                    sides.reverse()
                for pos, (side, exe, lab, trace, rl, g) in enumerate(sides):
                    copy = os.path.join(scratch, "copy")
                    fresh_copy(tx, copy)
                    try:
                        if side == "python":
                            argv = [exe, "-I", MEASURE, "mem", copy, payloads[label], "--appends", str(a.appends)]
                            if trace:
                                argv.append("--tracemalloc")
                        else:
                            argv = [exe, "measure", "mem", copy, payloads[label], "--appends", str(a.appends),
                                    "--growth", g]
                        if rl:
                            argv.append("--relief")
                        started = utc()
                        rec = run_child(argv, env)
                    finally:
                        shutil.rmtree(copy, ignore_errors=True)
                    rec.update(run=i, kind="mem", dump=label, side=side, interp=lab, tracemalloc=trace, relief=rl,
                               growth=g, order=pos, startedUtc=started)
                    doc["runs"].append(rec)
                    say(rec)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    doc["endedUtc"] = utc()
    doc["summary"] = summarize(doc)
    return doc


def say(rec):
    ok = "ok" if rec["rc"] == 0 and rec.get("result") is not None else "FAILED rc=%s" % rec["rc"]
    extra = (" tracemalloc" if rec.get("tracemalloc") else "") + (" relief" if rec.get("relief") else "")
    sys.stderr.write("run %d %s %s %s%s: %s (%.1f s, load %.1f)\n" % (rec["run"], rec["kind"], rec["dump"], rec["interp"],
                                                                     extra, ok, rec["wallS"], rec["loadBefore"][0]))
    if rec["rc"] != 0:
        sys.stderr.write(rec["stderr"][-600:] + "\n")


# ───────────────────────────── the summary ─────────────────────────────
# The CPU gate's decision rule (rust/SPIKE.md, "S6 method", "The decision rule"), fixed before S6 proper runs.
GATE = {"world": "large", "interpreter": "cpython-3.13.18-arm64",
        "python": "heldWarm region (instrumented, wrappers subtracted)", "rust": "held (fold_on with the held aggregate)",
        "statistic": "median over runs of the paired ratio (each Python run over the Rust run beside it in the "
                     "alternation); per run, each row's figure is the mean of its even-round and odd-round medians, "
                     "first build dropped, gen2 builds dropped; at least 6 runs",
        "threshold": 5.0}
SPIN_TOLERANCE = 0.10                                # a run's spin rate this far from its side's median is flagged
DRIFT_MB = 8.0                                        # resident minus footprint moving more than this is flagged
DECODED_MULTIPLE = 2.85                               # event_model's estimate of decoded bytes per file byte
LAB_RESIDENT_MULTIPLE = 3.1                           # S3's lab resident growth per file byte (streamed)


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def spread(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {"median": statistics.median(xs), "min": min(xs), "max": max(xs), "n": len(xs)}


def ms(ns):
    return None if ns is None else ns / 1e6


def ratio(a, b):
    return None if (a is None or not b) else a / b


def kept(builds, drop_first=True):
    """A row's builds for its statistic: the first dropped (it pays the row's warm-up: heldWarm drifted from 21.8 to
    17.4 ms within one row before), and any build that ran a full (gen2) collection dropped and counted."""
    bs = builds[1:] if drop_first and len(builds) > 1 else list(builds)
    full = [len(b.get("gc") or []) > 2 and b["gc"][2] > 0 for b in bs]
    return [b for b, f in zip(bs, full) if not f], sum(full)


def balanced(vals):
    """A row's figure for one run: the mean of its even-round and odd-round medians. Both sides alternate their row order
    between rounds, and a row's predecessor moves it (Python's held rows by about 10%, the port's held row by about 25%
    in the smoke runs), so a plain median would lean to whichever position has more builds. `vals` is [(round, value)]
    after the drops; with only one position present, its median."""
    ev = [v for i, v in vals if i % 2 == 0 and v is not None]
    od = [v for i, v in vals if i % 2 == 1 and v is not None]
    parts = [statistics.median(x) for x in (ev, od) if x]
    return sum(parts) / len(parts) if parts else None


def row_ms(builds):
    full = [len(b.get("gc") or []) > 2 and b["gc"][2] > 0 for b in builds]
    vals = [(i, b["cpuNs"]) for i, (b, f) in enumerate(zip(builds, full)) if i > 0 and not f]
    if not vals and builds:
        vals = [(0, builds[0]["cpuNs"])]
    return ms(balanced(vals))


def inst_runs(x, row):
    """The instrumented builds of a row as (round, run), the first dropped when there are at least two."""
    runs = ((x.get("instrumentedRows") or {}).get(row) or {}).get("runs") or []
    pairs = list(enumerate(runs))
    return pairs[1:] if len(pairs) > 1 else pairs


def inst_ms(x, row, key):
    return ms(balanced([(i, r["derived"].get(key)) for i, r in inst_runs(x, row)]))


def memo_inst_ms(x, row, key):
    """A memo-phase row's instrumented figure (measure.py --memo), balanced as `inst_ms`."""
    runs = ((((x.get("memoRows") or {}).get("instrumentedRows") or {}).get(row) or {}).get("runs")) or []
    pairs = list(enumerate(runs))
    pairs = pairs[1:] if len(pairs) > 1 else pairs
    return ms(balanced([(i, r["derived"].get(key)) for i, r in pairs]))


def memo_row_ms(x, row):
    rows = (x.get("memoRows") or {}).get("rows") or {}
    return row_ms(rows[row]) if row in rows else None


def rust_row(x, row):
    rows = x.get("rows") or {}
    return row_ms(rows[row]) if row in rows else None


def spin_flags(results, label):
    """Runs whose spin rate (start or end) is more than SPIN_TOLERANCE from the side's median: a slower core or clock."""
    rates = [(i, k, (x.get(k) or {}).get("loopsPerCpuUs")) for i, x in results for k in ("calibration", "calibrationEnd")]
    m = med([r for _i, _k, r in rates])
    out = {"medianLoopsPerCpuUs": m, "flagged": []}
    if m:
        for i, k, r in rates:
            if r is not None and abs(r / m - 1) > SPIN_TOLERANCE:
                out["flagged"].append({"run": i, "at": k, "loopsPerCpuUs": r, "side": label})
    return out


def perf_window(doc):
    """The live kernel's builds.feed counters: the difference of two snapshots (an active window), or one snapshot's
    counters since boot (flagged: cold boot builds included)."""
    snaps = doc.get("perf") or []
    if isinstance(snaps, dict):
        snaps = [snaps]
    if not snaps:
        return None

    def flat(sn):
        bf = sn.get("buildsFeed") or {}
        memo = bf.get("memo") or {}
        out = {k: memo.get(k) for k in ("hit", "miss", "derived", "segHeld", "segWalkTurns", "segWalks", "foldDecodes",
                                        "entries")}
        out.update(built=bf.get("built"), cached=bf.get("cached"), ms=bf.get("ms"), uptime_s=sn.get("uptime_s"))
        return out
    a = flat(snaps[0])
    if len(snaps) == 1:
        w = dict(a, window="since boot (one snapshot: cumulative, cold boot builds included)")
    else:
        b = flat(snaps[-1])
        if snaps[0].get("since") != snaps[-1].get("since") or (b["uptime_s"] or 0) < (a["uptime_s"] or 0):
            return {"window": "invalid: the kernel restarted between the two snapshots", "missRate": None}
        w = {k: (None if a[k] is None or b[k] is None else b[k] - a[k]) for k in a if k != "entries"}
        w["entries"] = b["entries"]
        w["windowS"] = w.pop("uptime_s")
        w["window"] = "difference of two snapshots, %s to %s" % (snaps[0].get("fetchedUtc"), snaps[-1].get("fetchedUtc"))
    hit, miss = w.get("hit"), w.get("miss")
    w["missRate"] = miss / (hit + miss) if hit is not None and miss is not None and (hit + miss) else None
    w["missesPerBuild"] = ratio(miss, w.get("built"))
    w["sessionsPerBuild"] = ratio((hit or 0) + (miss or 0), w.get("built"))
    w["builtShare"] = ratio(w.get("built"), (w.get("built") or 0) + (w.get("cached") or 0))
    held, walked = w.get("segHeld") or 0, w.get("segWalkTurns") or 0
    w["heldTurnShare"] = held / (held + walked) if (held + walked) else None
    w["heldTurnsPerWalk"] = ratio(held, w.get("segWalks"))
    return w


def paired(pairs):
    """{median, min, max, n} of Python-over-Rust ratios, one per run (the runs beside each other in the alternation)."""
    return spread([ratio(p, r) for p, r in pairs])


def seg_of(builds):
    keys = ("segWalks", "segHeld", "segWalkTurns", "segReset", "segPartial")
    return sorted({tuple((b.get("seg") or {}).get(k) for k in keys) for b in builds})


def summarize(doc):
    """Everything rust/SPIKE.md's S6 method names, computed from doc["runs"]; nothing here is measured. CPU comparisons
    are paired: each Python run's figure over the Rust run of the same run index (the two ran next to each other)."""
    out = {"gateRule": GATE, "perf": perf_window(doc)}
    perf = out["perf"]
    for dinfo in doc["dumps"] + [{"label": k} for k in (doc.get("memSources") or {}) if k.startswith("dir-")]:
        label = dinfo["label"]
        runs = [r for r in doc["runs"] if r["dump"] == label and r["rc"] == 0 and r.get("result")]
        s = {"failedRuns": sum(1 for r in doc["runs"] if r["dump"] == label and (r["rc"] != 0 or not r.get("result"))),
             "problems": []}
        cpu = [r for r in runs if r["kind"] == "cpu"]
        rust_by_run = {r["run"]: r["result"] for r in cpu if r["side"] == "rust"}
        if rust_by_run:
            rs = list(rust_by_run.values())
            rows = rs[0].get("rowOrder") or []
            s["rust"] = {"coldMs": spread([ms(x["cold"]["cpuNs"]) for x in rs]),
                         "loadMs": spread([ms(x["load"]["cpuNs"]) for x in rs]),
                         "rows": {row: spread([rust_row(x, row) for x in rs]) for row in rows},
                         "byPositionMs": {row: {"evenRounds": spread([ms(med([b["cpuNs"] for b in x["rows"][row][2::2]]))
                                                                      for x in rs]),
                                                "oddRounds": spread([ms(med([b["cpuNs"] for b in x["rows"][row][1::2]]))
                                                                     for x in rs])} for row in rows},
                         "heldSeg": seg_of([b for x in rs for b in x["rows"].get("held", [])]),
                         "heldColdSeg": seg_of([b for x in rs for b in x["rows"].get("heldCold", [])]),
                         "warmCpuOverWall": med([med([w["cpuNs"] / w["wallNs"] for w in x["rows"]["warm"]]) for x in rs]),
                         "spin": spin_flags(sorted(rust_by_run.items()), "rust"),
                         "build": rs[0].get("build")}
            s["problems"] += ["spin rate: %r" % f for f in s["rust"]["spin"]["flagged"]]
        pys = {}
        for lab in sorted({r["interp"] for r in cpu if r["side"] == "python"}):
            by_run = {r["run"]: r["result"] for r in cpu if r["side"] == "python" and r["interp"] == lab}
            res = list(by_run.values())
            p = {"coldMs": spread([ms(x["cold"]["cpuNs"]) for x in res]),
                 "calibrationCpuOverWall": med([x["calibration"]["cpuNs"] / x["calibration"]["wallNs"] for x in res]),
                 "calibrationThreadOverWall": med([x["calibration"]["threadNs"] / x["calibration"]["wallNs"]
                                                   for x in res]),
                 "osThreads": sorted({x["threads"]["os"] for x in res if x["threads"]["os"] is not None}),
                 "spin": spin_flags(sorted(by_run.items()), lab), "rows": {}}
            s["problems"] += ["spin rate: %r" % f for f in p["spin"]["flagged"]]
            for row in (res[0].get("rowOrder") or ["emptyMemo", "heldWarm", "memoServed"]):
                gen2 = sum(kept(x["rows"][row])[1] for x in res)
                r_ = {"buildMs": spread([row_ms(x["rows"][row]) for x in res]), "gen2BuildsDropped": gen2,
                      "firstBuildMs": spread([ms(x["rows"][row][0]["cpuNs"]) for x in res]),
                      # the rows alternate their order between rounds (measure.py ROWS_ODD): even and odd rounds apart,
                      # since a row's predecessor moves it (about 15% for the held rows in the smoke runs)
                      "byPositionMs": {"evenRounds": spread([ms(med([b["cpuNs"] for b in x["rows"][row][2::2]]))
                                                             for x in res]),
                                       "oddRounds": spread([ms(med([b["cpuNs"] for b in x["rows"][row][1::2]]))
                                                            for x in res])},
                      "seg": seg_of([b for x in res for b in x["rows"][row]][-1:])}
                if any(x.get("instrumentedRows") for x in res):
                    keys = ("regionCorrectedNs", "regionNs", "derivationPlusFoldNs", "entryNs", "foldNs",
                            "workingInKeyNs", "foldDecodeNs", "memoDumpsNs", "memoSerdeNs", "memoInFoldNs",
                            "standinsNs", "wrapperUncertaintyNs", "keyOwnNs", "memoBookNs", "frameNs", "preloopNs",
                            "parkedNs", "noticeNs", "residualNs", "residualFloorNs", "hitNs", "missNs",
                            "totalNs", "totalLessWrappersNs")
                    r_["instrumentedMs"] = {k[:-2] + "Ms": spread([inst_ms(x, row, k) for x in res]) for k in keys}
                    r_["standinsCalls"] = med([med([q["derived"]["standinsCalls"] for _i, q in inst_runs(x, row)])
                                               for x in res])
                    # the wrappers' correction checked: the plain build against the instrumented total less the wrappers
                    tl = (r_["instrumentedMs"]["totalLessWrappersMs"] or {}).get("median")
                    pb = (r_["buildMs"] or {}).get("median")
                    r_["wrapperCheck"] = ratio(tl, pb)
                    if r_["wrapperCheck"] is not None and abs(r_["wrapperCheck"] - 1) > 0.10:
                        s["problems"].append("%s %s: the instrumented total less the wrappers is %.2fx the plain build"
                                             % (lab, row, r_["wrapperCheck"]))
                p["rows"][row] = r_
            # held path proven: Python's heldWarm counters equal the port's held build's, emptyMemo's the heldCold's
            if rust_by_run:
                for prow, rrow in (("heldWarm", "held"), ("emptyMemo", "heldCold")):
                    pseg = seg_of([b for x in res for b in x["rows"].get(prow, [])])
                    rseg = seg_of([b for x in rust_by_run.values() for b in x["rows"].get(rrow, [])])
                    if pseg != rseg:
                        s["problems"].append("%s %s's held counters %r differ from the port's %s %r"
                                             % (lab, prow, pseg, rrow, rseg))
            # the comparisons, paired by run index
            cmp_ = {}

            def pair(pfun, rrow):
                return paired([(pfun(by_run[i]), rust_row(rust_by_run[i], rrow)) for i in sorted(by_run)
                               if i in rust_by_run])
            if rust_by_run:
                cmp_["deciding: heldWarm region / held"] = pair(lambda x: inst_ms(x, "heldWarm", "regionCorrectedNs"),
                                                                "held")
                cmp_["secondary, same work, interpreter vs compiled: noHold region / region"] = pair(
                    lambda x: inst_ms(x, "noHold", "regionCorrectedNs"), "region")
                cmp_["emptyMemo region / heldCold"] = pair(lambda x: inst_ms(x, "emptyMemo", "regionCorrectedNs"),
                                                           "heldCold")
                cmp_["without the landing memo the port lacks: heldNoLanding region / held"] = pair(
                    lambda x: inst_ms(x, "heldNoLanding", "regionCorrectedNs"), "held")
                cmp_["reference, the cheap bound: heldWarm region / bound"] = pair(
                    lambda x: inst_ms(x, "heldWarm", "regionCorrectedNs"), "bound")
                cmp_["memo hit: memoServed hit / held"] = pair(lambda x: inst_ms(x, "memoServed", "hitNs"), "held")
                for row in ("noHold", "emptyMemo", "heldWarm", "heldNoLanding", "memoServed"):
                    cmp_["unfair upper bound: plain %s build / warm" % row] = pair(
                        lambda x, row=row: row_ms(x["rows"][row]), "warm")
                cmp_["unfair upper bound: plain cold build / Rust cold"] = paired(
                    [(ms(by_run[i]["cold"]["cpuNs"]), ms(rust_by_run[i]["cold"]["cpuNs"])) for i in sorted(by_run)
                     if i in rust_by_run])
            # S6.1: both sides memoized (measure.py --memo, romp-spike measure --memo)
            if rust_by_run and any(x.get("memoRows") for x in res) and "memo" in (s["rust"]["rows"] or {}):
                cmp_["memo, hit against hit: memoKeyed region / memo"] = pair(
                    lambda x: memo_inst_ms(x, "memoKeyed", "regionCorrectedNs"), "memo")
                cmp_["memo, one miss on the largest session: memoKeyedMiss region / memoMiss"] = pair(
                    lambda x: memo_inst_ms(x, "memoKeyedMiss", "regionCorrectedNs"), "memoMiss")
                cmp_["memo, the port's memo against S6's memo-less port: Rust held / Rust memo"] = spread(
                    [ratio(rust_row(rust_by_run[i], "held"), rust_row(rust_by_run[i], "memo")) for i in sorted(rust_by_run)])
                cmp_["unfair upper bound: plain memoKeyed build / memoBuild"] = pair(
                    lambda x: memo_row_ms(x, "memoKeyed"), "memoBuild")
                p["memo"] = {
                    "pythonMs": {k: spread([memo_inst_ms(x, r, k2) for x in res]) for k, r, k2 in (
                        ("memoKeyedRegion", "memoKeyed", "regionCorrectedNs"),
                        ("memoKeyedMissRegion", "memoKeyedMiss", "regionCorrectedNs"),
                        ("memoKeyedKeyLessStandins", "memoKeyed", "keyLessStandinsNs"),
                        ("memoKeyedKeyStandins", "memoKeyed", "keyStandinsNs"),
                        ("memoKeyedFold", "memoKeyed", "foldNs"), ("memoKeyedBook", "memoKeyed", "memoBookNs"))},
                    "pythonPlainMs": {r: spread([memo_row_ms(x, r) for x in res]) for r in ("memoKeyed", "memoKeyedMiss")},
                    "rustMs": {r: s["rust"]["rows"].get(r) for r in ("memo", "memoMiss", "memoBuild")},
                    "decisions": {"python": sorted({json.dumps(b.get("memo"), sort_keys=True) for x in res
                                                    for r in ("memoKeyed", "memoKeyedMiss")
                                                    for b in ((x.get("memoRows") or {}).get("rows") or {}).get(r, [])}),
                                  "rust": sorted({json.dumps({k: (b.get("memo") or {}).get(k) for k in ("hits", "misses",
                                                                                                    "raises")},
                                                             sort_keys=True)
                                                  for x in rust_by_run.values() for r in ("memo", "memoMiss")
                                                  for b in x["rows"].get(r, [])})}}
                dec = p["memo"]["decisions"]
                if dec["python"] != dec["rust"]:
                    s["problems"].append("%s: the memo rows' decisions differ: python %r, rust %r" % (lab, dec["python"],
                                                                                                     dec["rust"]))
                if perf and perf.get("missesPerBuild") is not None:
                    mpb = min(1.0, perf["missesPerBuild"])

                    def blend(hit, miss1):
                        return None if hit is None or miss1 is None else hit + mpb * (miss1 - hit)
                    pairs_ = []
                    for i in sorted(by_run):
                        if i not in rust_by_run:
                            continue
                        x, rx = by_run[i], rust_by_run[i]
                        pb = blend(memo_inst_ms(x, "memoKeyed", "regionCorrectedNs"),
                                   memo_inst_ms(x, "memoKeyedMiss", "regionCorrectedNs"))
                        rb = blend(rust_row(rx, "memo"), rust_row(rx, "memoMiss"))
                        pairs_.append((pb, rb))
                    p["memo"]["productionBlend"] = {
                        "missesPerBuild": mpb, "note": "hit + misses per build x (one miss on the largest session - hit), "
                                                       "both sides memoized; production's misses all on the largest session",
                        "pythonMs": spread([a for a, _b in pairs_]), "rustMs": spread([b for _a, b in pairs_]),
                        "pythonOverRust": paired(pairs_)}
            p["compare"] = cmp_
            # the production blend: hits cost the memoServed row's hit, misses the heldWarm (or emptyMemo) row's miss,
            # both without the key; m per session per build, uniform over the world's sessions, or production's misses
            # per build all on the largest session
            if perf and perf.get("missRate") is not None and rust_by_run:
                m, mpb = perf["missRate"], perf["missesPerBuild"]
                turns = res[0].get("sessionTurns") or {}
                big = max(turns, key=lambda k: (turns[k], k)) if turns else None
                blend = {"missRate": m, "missesPerBuild": mpb, "largestSession": {"sid": big, "turns": turns.get(big)},
                         "worldSessions": len(turns)}

                def sid_med(x, row, key):
                    vals = {}
                    for _i, q in inst_runs(x, row):
                        for sid, v in (q.get("bySid") or {}).items():
                            vals.setdefault(sid, []).append(v[key])
                    return {sid: med(v) for sid, v in vals.items()}
                for mrow in ("heldWarm", "emptyMemo"):
                    uni, lg = [], []
                    for i in sorted(by_run):
                        x = by_run[i]
                        hit, miss = sid_med(x, "memoServed", "hitNs"), sid_med(x, mrow, "missNs")
                        if not hit or not miss:
                            continue
                        hit_all = sum(hit.values())
                        uni.append(ms(hit_all + m * sum(miss.get(k, 0) - hit.get(k, 0) for k in miss)))
                        if big in miss and mpb is not None:
                            lg.append(ms(hit_all + min(1.0, mpb) * (miss[big] - hit.get(big, 0))))
                    blend[mrow] = {"uniformMs": spread(uni), "largestSessionMs": spread(lg)}
                rw = s["rust"]["rows"]
                blend["rustPerBuildMs"] = {"held": (rw.get("held") or {}).get("median"),
                                           "region": (rw.get("region") or {}).get("median")}
                for mrow in ("heldWarm", "emptyMemo"):
                    for kind in ("uniformMs", "largestSessionMs"):
                        v = (blend[mrow][kind] or {}).get("median")
                        blend[mrow][kind.replace("Ms", "OverRustHeld")] = ratio(v, blend["rustPerBuildMs"]["held"])
                blend["note"] = ("the memo-less port derives every session every build, so against a memo hit rate "
                                 "this high it cannot pass the 5x gate unless the memo is ported too")
                p["productionBlend"] = blend
            pys[lab] = p
        s["python"] = pys
        g = pys.get(GATE["interpreter"])
        if label == GATE["world"] and g is not None:
            dec = g["compare"].get("deciding: heldWarm region / held")
            s["gate"] = {"rule": GATE, "pairedRatio": dec,
                         "verdict": (None if not dec else
                                     "insufficient runs" if dec["n"] < 6 else
                                     "fail" if dec["median"] < GATE["threshold"] else
                                     "marginal pass" if dec["min"] < GATE["threshold"] else "pass")}
        mem = [r for r in runs if r["kind"] == "mem"]
        if mem:
            s["memory"] = mem_summary(mem, (doc.get("memSources") or {}).get(label))
            s["problems"] += s["memory"].pop("problems", [])
        out[label] = s
    return out


def reading_of(r, relieved=False):
    """A reading's memory fields; with `relieved`, the reading taken after the pressure relief (when there is one)."""
    if relieved:
        rl = r.get("relieved")
        return (rl or {}).get("memory") if rl else None
    return r.get("memory")


def delta(a, b, field, relieved=False):
    ma, mb = reading_of(a, relieved), reading_of(b, relieved)
    if not ma or not mb:
        return None
    va, vb = ma.get(field), mb.get(field)
    return None if va is None or vb is None else (vb - va) / 1e6


def curve(x, relieved=False):
    """The footprint and resident deltas over the baseline at the cold read and after every round, with the file bytes
    held then and the bytes per file byte."""
    pts = []
    fb = x["fileBytes"]
    for r in [x["cold"]] + list(x.get("rounds") or []):
        fb_now = fb + sum(q.get("appendedBytes", 0) for q in (x.get("rounds") or [])[:r.get("round", 0)])
        fp = delta(x["before"], r, "footprint", relieved)
        pts.append({"round": r.get("round", 0), "footprintMB": fp, "residentMB": delta(x["before"], r, "resident", relieved),
                    "mallocInUseMB": delta(x["before"], r, "mallocInUse", relieved),
                    "mallocTotalMB": delta(x["before"], r, "mallocTotal", relieved),
                    "fileMB": fb_now / 1e6, "footprintPerFileByte": None if fp is None else fp * 1e6 / fb_now,
                    "compressedMB": ((reading_of(r, relieved) or {}).get("compressed") or 0) / 1e6})
    return pts


def mean_curve(xs, relieved=False):
    curves = [curve(x, relieved) for x in xs]
    if not curves:
        return []
    out = []
    for i in range(min(len(c) for c in curves)):
        pt = {"round": curves[0][i]["round"]}
        for k in ("footprintMB", "residentMB", "mallocInUseMB", "mallocTotalMB", "fileMB", "footprintPerFileByte",
                  "compressedMB"):
            pt[k] = med([c[i][k] for c in curves])
        out.append(pt)
    return out


def drift_problems(xs, lab):
    """Resident minus footprint should stay where the baseline put it; a move means resident counts pages macOS has not
    taken back (or footprint counts what resident does not). Compressed bytes mean resident under-reads."""
    probs = []
    for x in xs:
        base = reading_of(x["before"]) or {}
        if base.get("resident") is None or base.get("footprint") is None:
            continue
        b0 = base["resident"] - base["footprint"]
        for r in [x["cold"]] + list(x.get("rounds") or []):
            m = reading_of(r) or {}
            d = (m["resident"] - m["footprint"] - b0) / 1e6
            fp = abs(delta(x["before"], r, "footprint") or 0)
            if abs(d) > max(DRIFT_MB, 0.05 * fp):
                probs.append("%s round %s: resident minus footprint moved %.1f MB from the baseline" % (lab, r.get("round", 0), d))
            if m.get("compressed"):
                probs.append("%s round %s: %.1f MB compressed (resident under-reads)" % (lab, r.get("round", 0),
                                                                                      m["compressed"] / 1e6))
    return sorted(set(probs))


def mem_summary(mem, source):
    """Footprint (phys_footprint) is the figure; resident beside it. Ratios are Rust's delta over Python's, cold and
    after every round, with each side's bytes per file byte beside them, and the projections that rescale Python to
    event_model's 2.85 decoded bytes per file byte and to S3's 3.1 resident."""
    out = {"source": source, "problems": []}
    rust = {}
    for r in mem:
        if r["side"] == "rust":
            rust.setdefault((r["growth"], bool(r.get("relief"))), []).append(r["result"])
    pyth = {}
    for r in mem:
        if r["side"] == "python":
            pyth.setdefault((r["interp"], bool(r.get("tracemalloc")), bool(r.get("relief"))), []).append(r["result"])
    for (g, rl), xs in sorted(rust.items()):
        lab = "rust-%s%s" % (g, "-relief" if rl else "")
        last = lambda x: (x["rounds"] or [x["cold"]])[-1]
        out[lab] = {"curve": mean_curve(xs), "curveRelieved": mean_curve(xs, True) if rl else None,
                    "allocatedColdMB": spread([x["cold"]["counted"]["allocatedBytes"] / 1e6 for x in xs]),
                    "allocatedLastMB": spread([last(x)["counted"]["allocatedBytes"] / 1e6 for x in xs]),
                    "blocksLast": med([last(x)["counted"].get("blocks") for x in xs]),
                    "records": sorted({last(x)["counted"]["records"] for x in xs}),
                    "released": [((last(x).get("relieved") or {}).get("released")) for x in xs] if rl else None}
        out["problems"] += sorted({"%s: %s" % (lab, q) for x in xs for q in x["problems"]})
        out["problems"] += drift_problems(xs, lab)
    for (lab_i, tm, rl), xs in sorted(pyth.items()):
        lab = "%s%s%s" % (lab_i, "-traced" if tm else "", "-relief" if rl else "")
        last = lambda x: (x["rounds"] or [x["cold"]])[-1]
        p = {"curve": mean_curve(xs) if not tm else None, "curveRelieved": mean_curve(xs, True) if rl else None,
             "records": sorted({last(x)["counted"]["records"] for x in xs})}
        if tm:
            p["tracedColdMB"] = spread([(x["cold"]["traced"] - x["before"]["traced"]) / 1e6 for x in xs])
            p["tracedLastMB"] = spread([(last(x)["traced"] - x["before"]["traced"]) / 1e6 for x in xs])
            p["tracedLeftAfterClearMB"] = spread([(x["cleared"]["traced"] - x["before"]["traced"]) / 1e6 for x in xs])
            p["tracedPerFileByteCold"] = med([(x["cold"]["traced"] - x["before"]["traced"]) / x["fileBytes"] for x in xs])
        else:
            out["problems"] += drift_problems(xs, lab)
        out["problems"] += sorted({"%s: %s" % (lab, q) for x in xs for q in x["problems"]})
        out[lab] = p
    # the ratios: Rust's footprint delta over Python's, per round; the decided policy (blocks) first
    ratios = {}
    for (g, rl) in sorted(rust):
        rlab = "rust-%s%s" % (g, "-relief" if rl else "")
        rc = out[rlab]["curveRelieved"] if rl else out[rlab]["curve"]
        for (lab_i, tm, prl) in sorted(pyth):
            if tm or prl != rl:
                continue
            plab = "%s%s" % (lab_i, "-relief" if prl else "")
            pc = out[plab]["curveRelieved"] if prl else out[plab]["curve"]
            pts = []
            for a, b in zip(rc, pc):
                pts.append({"round": a["round"], "footprintRatio": ratio(a["footprintMB"], b["footprintMB"]),
                            "residentRatio": ratio(a["residentMB"], b["residentMB"]),
                            "rustPerFileByte": a["footprintPerFileByte"], "pythonPerFileByte": b["footprintPerFileByte"],
                            # projections, not measurements: Python rescaled to real transcripts' expansion
                            "overDecoded285": ratio(a["footprintMB"], a["fileMB"] * DECODED_MULTIPLE),
                            "overLabResident31": ratio(a["footprintMB"], a["fileMB"] * LAB_RESIDENT_MULTIPLE)})
            ratios["%s / %s" % (rlab, plab)] = pts
    out["ratios"] = ratios
    recs = {tuple(v["records"]) for k, v in out.items() if isinstance(v, dict) and "records" in v}
    out["sameRecords"] = len(recs) == 1
    if not out["sameRecords"]:
        out["problems"].append("the sides counted different records: %r" % sorted(recs))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", action="append", default=[])
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--python", action="append", help="an interpreter to measure (repeatable; default this one)")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--mem", action="store_true")
    ap.add_argument("--memo", action="store_true", help="add both sides' memo rows to the CPU runs (step S6.1)")
    ap.add_argument("--builds", type=int, default=11, help="rounds per run; odd, so after the first is dropped both "
                    "positions keep as many builds")
    ap.add_argument("--instrumented", type=int, default=5)
    ap.add_argument("--appends", type=int, default=6, help="append rounds (default 6)")
    ap.add_argument("--schedule", choices=["active", "all"], default="active")
    ap.add_argument("--active", type=int, default=4, help="with --schedule active: how many of the largest files grow")
    ap.add_argument("--growth", default="blocks,double", help="the store's growth policies to measure, comma-separated")
    ap.add_argument("--relief", action="store_true", help="add a run per side reading after malloc pressure relief")
    ap.add_argument("--mem-dir", action="append", default=[], help="a transcript directory to measure besides the dumps")
    ap.add_argument("--perf", action="append", help="measure.py perf's output; twice: before and after a window")
    ap.add_argument("--binary", default=os.path.join(REPO, "rust", "target", "release", "romp-spike"))
    ap.add_argument("--out")
    ap.add_argument("--summarize", metavar="FILE", help="recompute the summary of a saved run file and print it")
    a = ap.parse_args(argv)
    if a.summarize:
        with open(a.summarize, encoding="utf-8") as f:
            doc = json.load(f)
        print(json.dumps(summarize(doc), indent=1))
        return 0
    if not a.dump:
        ap.error("--dump is required")
    if not (a.cpu or a.mem):
        a.cpu = True
    if a.runs < 1 or a.builds < 1 or a.instrumented < 0 or a.appends < 1 or a.active < 1:
        ap.error("--runs, --builds, --appends and --active must be at least 1, --instrumented at least 0")
    if a.perf and len(a.perf) > 2:
        ap.error("--perf takes one snapshot, or two (before and after a window)")
    if any(g not in ("blocks", "double") for g in a.growth.split(",") if g):
        ap.error("--growth takes blocks and/or double")
    out = a.out or os.path.join(tempfile.gettempdir(), "romp-s6-measure-%s.json" % time.strftime("%Y%m%dT%H%M%SZ",
                                                                                                 time.gmtime()))
    try:
        doc = drive(a)
    except DriveError as e:
        sys.stderr.write("measure_drive.py: %s\n" % e)
        return 2
    with open(out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    failed = sum(1 for r in doc["runs"] if r["rc"] != 0 or not r.get("result"))
    sys.stderr.write("wrote %s: %d runs, %d failed\n" % (out, len(doc["runs"]), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
