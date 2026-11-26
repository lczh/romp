#!/usr/bin/env python3
"""Step S6's alternating driver (rust/SPIKE.md, "S6 method"): runs Python's measurement (tools/rust-spike/measure.py) and
Rust's (`romp-spike measure`) in turn, N times on the same dump, each run a fresh process, and keeps every run's raw
numbers in one JSON file, with a summary computed from them.

    python3 tools/rust-spike/measure_drive.py --dump DIR [--dump DIR ...] [--runs N] [--python EXE ...]
        [--cpu] [--mem] [--builds K] [--instrumented M] [--appends K] [--perf FILE] [--binary PATH] [--out FILE]
    python3 tools/rust-spike/measure_drive.py --summarize FILE

Order: run i measures every side once per dump. Even runs go Python first (each interpreter in the order given), then
Rust; odd runs go Rust first, then the interpreters in reverse, so neither side always meets the machine first. Every run
records the load average before and after it. `--cpu` (the default when neither is given) times the feed build; `--mem`
measures the record cache and the store: per run a Python run without tracemalloc (the resident size), one with it (the
traced bytes) and a Rust run, each on its own fresh copy of the dump's transcripts with mtimes near now, appending the
same bytes (each file's last line, PAYLOAD) K times. Copies live under one scratch directory in the system temp dir,
removed at the end. `--perf FILE` (measure.py perf's output) folds the live kernel's memo miss rate into the summary.

Nothing is written inside the repo. The output defaults to <system temp>/romp-s6-measure-<UTC stamp>.json; it names
interpreters by version and architecture and dumps by directory name, never by a path under the home directory.
"""
import argparse
import json
import os
import platform
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
SCHEMA = "romp-spike-measure-drive/1"
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
    try:
        rev = subprocess.run(["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                             timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", REPO, "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, timeout=30).stdout.strip()
        return {"rev": rev, "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        return None


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


def make_payload(dump, dst):
    tx = os.path.join(dump, "transcripts")
    os.makedirs(dst)
    for n in sorted(os.listdir(tx)):
        with open(os.path.join(dst, n), "wb") as f:
            f.write(last_line(os.path.join(tx, n)))


def fresh_copy(dump, dst):
    """A copy of the dump's transcripts with mtimes near now, in name order a millisecond apart, so the record cache
    keeps every entry (a file pinned in the past reads as quiescent: s2-design section 4, item 6)."""
    tx = os.path.join(dump, "transcripts")
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
                        "appends": a.appends},
           "interpreters": [i for _e, i in interps], "dumps": [dump_info(d) for d in dumps], "runs": []}
    if a.perf:
        with open(a.perf, encoding="utf-8") as f:
            doc["perf"] = json.load(f)
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="romp-s6-drive-"))
    env = child_env()
    try:
        payloads = {}
        if a.mem:
            for d in dumps:
                payloads[d] = os.path.join(scratch, "payload-" + os.path.basename(d))
                make_payload(d, payloads[d])
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
                                    str(a.instrumented)]
                        else:
                            argv = [exe, "measure", "cpu", d, "--builds", str(a.builds)]
                        started = utc()
                        rec = run_child(argv, env)
                        rec.update(run=i, kind="cpu", dump=label, side=side, interp=lab, order=pos, startedUtc=started)
                        doc["runs"].append(rec)
                        say(rec)
                if a.mem:
                    sides = []
                    for exe, info in interps:
                        sides += [("python", exe, info["label"], False), ("python", exe, info["label"], True)]
                    sides.append(("rust", binary, "rust", False))
                    if i % 2:
                        sides.reverse()
                    for pos, (side, exe, lab, trace) in enumerate(sides):
                        copy = os.path.join(scratch, "copy")
                        fresh_copy(d, copy)
                        try:
                            if side == "python":
                                argv = [exe, "-I", MEASURE, "mem", copy, payloads[d], "--appends", str(a.appends)]
                                if trace:
                                    argv.append("--tracemalloc")
                            else:
                                argv = [exe, "measure", "mem", copy, payloads[d], "--appends", str(a.appends)]
                            started = utc()
                            rec = run_child(argv, env)
                        finally:
                            shutil.rmtree(copy, ignore_errors=True)
                        rec.update(run=i, kind="mem", dump=label, side=side, interp=lab, tracemalloc=trace, order=pos,
                                   startedUtc=started)
                        doc["runs"].append(rec)
                        say(rec)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    doc["endedUtc"] = utc()
    doc["summary"] = summarize(doc)
    return doc


def say(rec):
    ok = "ok" if rec["rc"] == 0 and rec.get("result") is not None else "FAILED rc=%s" % rec["rc"]
    extra = " tracemalloc" if rec.get("tracemalloc") else ""
    sys.stderr.write("run %d %s %s %s%s: %s (%.1f s, load %.1f)\n" % (rec["run"], rec["kind"], rec["dump"], rec["interp"],
                                                                     extra, ok, rec["wallS"], rec["loadBefore"][0]))
    if rec["rc"] != 0:
        sys.stderr.write(rec["stderr"][-600:] + "\n")


# ───────────────────────────── the summary ─────────────────────────────
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


def summarize(doc):
    """Medians over runs (each run's own median over its builds first) and the comparisons rust/SPIKE.md's S6 method
    names. Every figure traces to doc["runs"]; nothing here is measured."""
    out = {}
    for dinfo in doc["dumps"]:
        label = dinfo["label"]
        runs = [r for r in doc["runs"] if r["dump"] == label and r["rc"] == 0 and r.get("result")]
        s = {"failedRuns": sum(1 for r in doc["runs"] if r["dump"] == label and (r["rc"] != 0 or not r.get("result")))}
        cpu = [r for r in runs if r["kind"] == "cpu"]
        rust = [r["result"] for r in cpu if r["side"] == "rust"]
        if rust:
            s["rust"] = {
                "coldMs": spread([ms(x["cold"]["cpuNs"]) for x in rust]),
                "warmMs": spread([ms(med([w["cpuNs"] for w in x["warm"]])) for x in rust]),
                "entriesMs": spread([ms(med([w["cpuNs"] for w in x["entries"]])) for x in rust]),
                "loadMs": spread([ms(x["load"]["cpuNs"]) for x in rust]),
                "warmCpuOverWall": med([med([w["cpuNs"] / w["wallNs"] for w in x["warm"]]) for x in rust]),
                "calibrationCpuOverWall": med([x["calibration"]["cpuNs"] / x["calibration"]["wallNs"] for x in rust]),
            }
        rust_warm = s.get("rust", {}).get("warmMs", {}) or {}
        rust_cold = s.get("rust", {}).get("coldMs", {}) or {}
        pys = {}
        for lab in sorted({r["interp"] for r in cpu if r["side"] == "python"}):
            res = [r["result"] for r in cpu if r["side"] == "python" and r["interp"] == lab]
            p = {"coldMs": spread([ms(x["cold"]["cpuNs"]) for x in res]),
                 "calibrationCpuOverWall": med([x["calibration"]["cpuNs"] / x["calibration"]["wallNs"] for x in res]),
                 "calibrationThreadOverWall": med([x["calibration"]["threadNs"] / x["calibration"]["wallNs"]
                                                   for x in res]),
                 "osThreads": sorted({x["threads"]["os"] for x in res if x["threads"]["os"] is not None}),
                 "rows": {}}
            for row in ("emptyMemo", "heldWarm", "memoServed"):
                r_ = {"buildMs": spread([ms(med([b["cpuNs"] for b in x["rows"][row]])) for x in res])}
                inst = [x["instrumentedRows"][row] for x in res if x.get("instrumentedRows")]
                if inst:
                    keys = inst[0]["runs"][0]["derived"].keys()
                    over = med([x["wrapperOverhead"]["perCallNs"] for x in res if x.get("wrapperOverhead")])
                    dv = {}
                    for k in keys:
                        vals = [med([run["derived"][k] for run in ir["runs"]]) for ir in inst]
                        dv[k[:-2] + "Ms" if k.endswith("Ns") else k] = med([ms(v) if k.endswith("Ns") else v
                                                                            for v in vals])
                    inner = med([med([sum(c for kk, (_t, c) in run["attrib"].items()
                                          if kk.endswith(("@entry", "@fold"))) for run in ir["runs"]]) for ir in inst])
                    dv["innerCallsInEntryAndFold"] = inner
                    dv["wrapperPerCallNs"] = over
                    if over is not None and inner is not None and dv.get("derivationPlusFoldMs") is not None:
                        dv["derivationPlusFoldCorrectedMs"] = dv["derivationPlusFoldMs"] - inner * over / 1e6
                    r_["instrumented"] = dv
                p["rows"][row] = r_
            # the comparisons: Python's comparable region over Rust's warm build (the port has no memo, so every Rust
            # build derives); the plain build total over it is the unfair upper bound (frame, key, memo, I/O included)
            cmp_ = {}
            if rust_warm.get("median"):
                rw = rust_warm["median"]
                for row in ("emptyMemo", "heldWarm"):
                    inst = p["rows"][row].get("instrumented") or {}
                    cmp_[row] = {"derivationPlusFoldOverRust": ratio(inst.get("derivationPlusFoldCorrectedMs"), rw),
                                 "noDecodeOverRust": ratio(inst.get("derivationPlusFoldNoDecodeMs"), rw),
                                 "plainBuildOverRust": ratio((p["rows"][row]["buildMs"] or {}).get("median"), rw)}
                inst = p["rows"]["memoServed"].get("instrumented") or {}
                cmp_["memoServed"] = {"servedOverRust": ratio(inst.get("servedMs"), rw),
                                      "plainBuildOverRust": ratio((p["rows"]["memoServed"]["buildMs"] or {})
                                                                  .get("median"), rw)}
            if rust_cold.get("median") and p["coldMs"]:
                cmp_["cold"] = {"plainBuildOverRustCold": ratio(p["coldMs"]["median"], rust_cold["median"])}
            p["compare"] = cmp_
            perf = (doc.get("perf") or {}).get("buildsFeed", {}).get("memo") or {}
            hit, miss = perf.get("hit"), perf.get("miss")
            if hit is not None and miss is not None and (hit + miss) and rust_warm.get("median"):
                m = miss / (hit + miss)
                held = perf.get("segHeld") or 0
                walked = perf.get("segWalkTurns") or 0
                blend = {"productionMissRate": m,
                         "productionHeldTurnShare": held / (held + walked) if (held + walked) else None}
                served = (p["rows"]["memoServed"].get("instrumented") or {}).get("servedMs")
                for row in ("emptyMemo", "heldWarm"):
                    dpf = (p["rows"][row].get("instrumented") or {}).get("derivationPlusFoldCorrectedMs")
                    if served is not None and dpf is not None:
                        b = served + m * (dpf - served)
                        blend[row] = {"pythonBlendedMs": b, "overRust": ratio(b, rust_warm["median"])}
                p["productionBlend"] = blend
            pys[lab] = p
        s["python"] = pys
        mem = [r for r in runs if r["kind"] == "mem"]
        if mem:
            s["memory"] = mem_summary(mem)
        out[label] = s
    return out


def delta_mb(a, b, field):
    """The change of a memory field between two readings, in MB (10**6 bytes); None when either is unknown."""
    ma, mb = a.get("memory", a) or {}, b.get("memory", b) or {}   # a reading, or a reading under "memory"
    va, vb = ma.get(field), mb.get(field)
    return None if va is None or vb is None else (vb - va) / 1e6


def mem_summary(mem):
    out = {}
    rust = [r["result"] for r in mem if r["side"] == "rust"]
    if rust:
        last = lambda x: x["rounds"][-1] if x["rounds"] else x["cold"]
        out["rust"] = {
            "residentColdMB": spread([delta_mb(x["before"], x["cold"], "resident") for x in rust]),
            "residentAfterAppendsMB": spread([delta_mb(x["before"], last(x), "resident") for x in rust]),
            "allocatedColdMB": spread([x["cold"]["counted"]["allocatedBytes"] / 1e6 for x in rust]),
            "allocatedAfterAppendsMB": spread([last(x)["counted"]["allocatedBytes"] / 1e6 for x in rust]),
            "records": sorted({last(x)["counted"]["records"] for x in rust}),
            "problems": sorted({p for x in rust for p in x["problems"]}),
        }
    for lab in sorted({r["interp"] for r in mem if r["side"] == "python"}):
        rss = [r["result"] for r in mem if r["side"] == "python" and r["interp"] == lab and not r["tracemalloc"]]
        tm = [r["result"] for r in mem if r["side"] == "python" and r["interp"] == lab and r["tracemalloc"]]
        last = lambda x: x["rounds"][-1] if x["rounds"] else x["cold"]
        p = {"residentColdMB": spread([delta_mb(x["before"], x["cold"], "resident") for x in rss]),
             "residentAfterAppendsMB": spread([delta_mb(x["before"], last(x), "resident") for x in rss]),
             "tracedColdMB": spread([(x["cold"]["traced"] - x["before"]["traced"]) / 1e6 for x in tm]),
             "tracedAfterAppendsMB": spread([(last(x)["traced"] - x["before"]["traced"]) / 1e6 for x in tm]),
             "tracedLeftAfterClearMB": spread([(x["cleared"]["traced"] - x["before"]["traced"]) / 1e6 for x in tm]),
             "records": sorted({last(x)["counted"]["records"] for x in rss + tm}),
             "problems": sorted({q for x in rss + tm for q in x["problems"]})}
        r_ = out.get("rust")
        if r_:
            p["rustOverPython"] = {
                "residentCold": ratio((r_["residentColdMB"] or {}).get("median"), (p["residentColdMB"] or {}).get("median")),
                "residentAfterAppends": ratio((r_["residentAfterAppendsMB"] or {}).get("median"),
                                              (p["residentAfterAppendsMB"] or {}).get("median")),
                "allocatedOverTracedCold": ratio((r_["allocatedColdMB"] or {}).get("median"),
                                                 (p["tracedColdMB"] or {}).get("median")),
                "allocatedOverTracedAfterAppends": ratio((r_["allocatedAfterAppendsMB"] or {}).get("median"),
                                                         (p["tracedAfterAppendsMB"] or {}).get("median")),
                "sameRecords": p["records"] == r_["records"]}
        out[lab] = p
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dump", action="append", default=[])
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--python", action="append", help="an interpreter to measure (repeatable; default this one)")
    ap.add_argument("--cpu", action="store_true")
    ap.add_argument("--mem", action="store_true")
    ap.add_argument("--builds", type=int, default=10)
    ap.add_argument("--instrumented", type=int, default=3)
    ap.add_argument("--appends", type=int, default=2)
    ap.add_argument("--perf")
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
    if a.runs < 1 or a.builds < 1 or a.instrumented < 0 or a.appends < 0:
        ap.error("--runs and --builds must be at least 1, --instrumented and --appends at least 0")
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
