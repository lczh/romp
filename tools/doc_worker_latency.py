#!/usr/bin/env python3
"""doc_worker_latency: what a small chat build waits while a document rebuild runs, on each of the rebuild's two roads.

A document rebuild is a whole parse of a transcript and a document write. On the kernel's own thread it shares the kernel's
one interpreter with every page; with STATE/doc-worker reading `on` it runs in a bin/romp-doc-worker process and the kernel
only waits for its line (kernel.py, _doc_worker_run; plans/process-split.md, stage two). This tool measures the difference
a page would feel, in a lab, with nothing live:

  for each size, one synthetic transcript of that many megabytes (the leaf that is rebuilt) and one small one (the probe's),
  both invented notes-api text in the CLI's record shapes, under a temporary root; then one child process per road, each a
  fresh interpreter that loads this checkout's kernel against a state directory of its own (session hosts off, no CLI,
  nothing a live kernel exports in its environment):

    baseline   no rebuild runs: the probe alone
    thread     the switch absent: the rebuild runs on the kernel's own `asm-rebuild` thread, as before the worker
    worker     the switch `on`: the rebuild runs in a romp-doc-worker process

  The probe is km.build_session of the small session with its chat fold cleared first, so every probe reshapes the session
  as a page's first build does, every --gap-ms. Each one is timed from the instant it was DUE to its return, so the wait
  for the interpreter before its first line counts. By default the probe holds the background gate for its span
  (em.asm_foreground), as a page connect's push and the pusher's cycle do: the kernel's own road then waits at its chunk
  points, which is the behaviour this road has today. --gate none leaves the gate to the probe's own parse, the least a
  build on a request thread holds.

One line per road: the probes' count, p50, p90, p99 and maximum in milliseconds, the rebuild's outcome and wall seconds, the
kernel process's resident peak, the machine's load average as the road ended, and for the worker road the worker's own
peaks against the bound it was handed. One run per point, on whatever else the machine is doing (the load average says
how much that was): a measurement, not a distribution.

    nice -n 10 tools/doc_worker_latency.py                       # 100 and 300 MB, the three roads
    nice -n 10 tools/doc_worker_latency.py --sizes 20 --json /tmp/doc-worker-latency.json

Outside CI on purpose (hundreds of megabytes and minutes). The temporary root is removed afterwards unless --keep."""
import argparse
import importlib.util
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TOOL = os.path.realpath(__file__)
REPO = os.path.dirname(os.path.dirname(TOOL))
BIG = "7c7c7c7c-1111-4222-8333-777777777777"              # invented session ids
SMALL = "7c7c7c7c-2222-4222-8333-777777777777"
ROADS = ("baseline", "thread", "worker")
WORDS = ("notes", "route", "pages", "cursor", "tags", "filter", "owner", "search", "index", "rebuilt", "retry", "cap",
         "backoff", "review", "branch", "merge", "green", "fixture", "suite", "schema")
COMPACT_EVERY = 40


# ── the synthetic world ─────────────────────────────────────────────────────────────────────────────────────────────
def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


def write_transcript(path, tag, nbytes=None, turns=None, seed=0):
    """A transcript of invented notes-api turns in the CLI's shapes: a typed prompt and an assistant's text per turn, a tool
    call beside some, a Read and its result after every third, and a compaction boundary with its summary every
    COMPACT_EVERY turns. Written until it holds `nbytes` or `turns`. Returns (bytes, turns)."""
    rnd = random.Random(seed)
    pool = {n: [" ".join(rnd.choice(WORDS) for _ in range(n)) for _ in range(257)] for n in (40, 200, 250, 300)}
    t, parent, k, size = int(time.time()) - 86400 * 400, None, 0, 0
    with open(path, "w", encoding="utf-8") as f:
        def put(rec):
            line = json.dumps(rec) + "\n"
            f.write(line)
            return len(line)
        while (nbytes is None or size < nbytes) and (turns is None or k < turns):
            if k and k % COMPACT_EVERY == 0:
                b, s = "%s_b%d" % (tag, k), "%s_s%d" % (tag, k)
                size += put({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None,
                             "logicalParentUuid": parent, "timestamp": _iso(t),
                             "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
                size += put({"type": "user", "uuid": s, "parentUuid": b, "timestamp": _iso(t + 1), "isCompactSummary": True,
                             "message": {"role": "user", "content": "summary so far: " + rnd.choice(pool[200])}})
                parent = s
                t += 2
            u, a = "%s_u%d" % (tag, k), "%s_a%d" % (tag, k)
            size += put({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed",
                         "cwd": "/w/notes-api", "version": "2.1.0", "gitBranch": "web",
                         "message": {"role": "user", "content": rnd.choice(pool[40]) + " %d" % k}})
            blocks = [{"type": "text", "text": rnd.choice(pool[300])}]
            if k % 7 == 3:
                blocks.append({"type": "tool_use", "id": "toolu_%s_%d" % (tag, k), "name": "Bash",
                               "input": {"command": "npm test", "description": "run the notes-api tests"}})
            if k % 5 == 1:
                blocks.append({"type": "tool_use", "id": "toolu_%s_%de" % (tag, k), "name": "Edit",
                               "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}})
            size += put({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t + 20), "cwd": "/w/notes-api",
                         "version": "2.1.0", "gitBranch": "web",
                         "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}})
            parent = a
            if k % 3 == 2:
                tu, tr, tid = "%s_t%d" % (tag, k), "%s_x%d" % (tag, k), "toolu_%s_r%d" % (tag, k)
                size += put({"type": "assistant", "uuid": tu, "parentUuid": parent, "timestamp": _iso(t + 25),
                             "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                                 {"type": "tool_use", "id": tid, "name": "Read",
                                  "input": {"file_path": "/w/notes-api/web/app%d.ts" % k}}]}})
                size += put({"type": "user", "uuid": tr, "parentUuid": tu, "timestamp": _iso(t + 26),
                             "message": {"role": "user", "content": [
                                 {"type": "tool_result", "tool_use_id": tid, "content": rnd.choice(pool[250])}]}})
                parent = tr
            t += 60
            k += 1
    return size, k


# ── one road, in a process of its own ───────────────────────────────────────────────────────────────────────────────
def _vm(field, pid="self"):
    try:
        with open("/proc/%s/status" % pid, encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith(field + ":"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _quantiles(lat):
    s = sorted(lat)

    def q(p):                                             # nearest rank
        return round(s[min(len(s) - 1, max(0, int(math.ceil(p * len(s))) - 1))] * 1000.0, 2) if s else None
    return {"n": len(s), "p50": q(0.50), "p90": q(0.90), "p99": q(0.99), "max": round(s[-1] * 1000.0, 2) if s else None,
            "mean": round(sum(s) / len(s) * 1000.0, 2) if s else None}


def child(a):
    """One road: load the kernel against the lab's state directory, warm the probe's session, start the rebuild the road
    names, and probe until it ends. Prints one JSON line."""
    state = Path(os.environ["XDG_STATE_HOME"], "romp")
    spec = importlib.util.spec_from_file_location("romp_loadsource", os.path.join(REPO, "kernel", "loadsource.py"))
    ls = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ls)
    km = ls.load_source("romp_kernel_doc_worker_lab", os.path.join(REPO, "kernel", "kernel.py"))
    jd, em = km.jd, km.em
    if Path(jd.STATE).resolve() != state.resolve():
        raise SystemExit("the kernel's state directory is %s, not the lab's %s: refusing to run" % (jd.STATE, state))
    for d in ("states", "goals", "sdk", "checkpoints"):
        (state / d).mkdir(parents=True, exist_ok=True)
    em.set_checkpoint_dir(lambda: state / "checkpoints")
    now = int(time.time())
    rows = [{"sid": SMALL, "name": "web", "path": a.small, "mtime": now}, {"sid": BIG, "name": "api", "path": a.big, "mtime": now}]
    km._sessions = lambda now, **kw: list(rows)           # the lab's two sessions, as the rebuild's tests stand them in
    km._live_map = lambda: {}
    km._display_sdk_human = lambda sid: False
    km.ASM_REBUILD = True
    gap = max(0.0, a.gap_ms / 1000.0)

    def probe_once():
        with km._chat_fold_lock:
            km._chat_fold.clear()                         # every probe reshapes the session: a page's first build of it
        km._built_chat.clear()
        km._prev_chat_events.clear()
        if a.gate == "push":
            with em.asm_foreground():                     # a page connect's push holds the gate for its span
                return km.build_session(SMALL, now, {})
        return km.build_session(SMALL, now, {})

    def probes(done):
        lat = []
        while not done():
            due = time.perf_counter() + gap
            time.sleep(gap)
            m = probe_once()
            lat.append(time.perf_counter() - due)
            if not (m and m.get("events")):
                raise SystemExit("the probe built nothing: %r" % (m,))
        return lat

    for _ in range(5):
        probe_once()                                      # the small session's parse and the builders' own caches are warm
    out = {"road": a.road, "gate": a.gate, "gapMs": a.gap_ms, "bigBytes": os.path.getsize(a.big),
           "smallBytes": os.path.getsize(a.small), "events": len(probe_once()["events"])}
    em.asm_background_idle(True)                          # no pusher runs here: the gate stands open between the probes
    if a.road == "baseline":
        end = time.perf_counter() + a.baseline_s
        out["probe"] = _quantiles(probes(lambda: time.perf_counter() >= end))
    else:
        if not em.asm_rebuild_note(a.big, BIG, "lab"):
            raise SystemExit("the leaf was not noted for a rebuild (under the floor, or checkpoints off)")
        t0 = time.perf_counter()
        if not km._asm_rebuild_maybe():
            raise SystemExit("no rebuild started: %s" % json.dumps(km._doc_worker_stats()))

        def running():
            return any(t.name == "asm-rebuild" and t.is_alive() for t in threading.enumerate())
        lat = probes(lambda: not running())
        out["rebuildS"] = round(time.perf_counter() - t0, 2)
        out["probe"] = _quantiles(lat)
        rs = em.asm_checkpoint_stats()["rebuild"]
        out["rebuild"] = {k: round(rs[k], 1) for k in ("written", "declined", "failed", "yields", "yieldMs") if rs.get(k)}
        dw = km._doc_worker_stats()
        out["docWorker"] = {k: dw.get(k) for k in ("switch", "spawned", "done", "fallbacks", "refused", "memory", "peak")}
        doc = em._asm_ckpt_file(a.big)
        out["documentBytes"] = doc.stat().st_size if doc.exists() else None
    out["kernelVmHWM"] = _vm("VmHWM")                     # read before the restore below, which is the tool's own check
    if a.road != "baseline":
        modes = []                                        # the point of the document: the leaf's next parse restores from it
        jd.parsed_session(BIG, [a.big], now, asm_mode_out=modes)
        out["nextParse"] = modes
    out["load"] = round(os.getloadavg()[0], 1)            # the machine's one-minute load as the road ended: what else ran
    sys.stdout.write("\n" + json.dumps(out) + "\n")
    sys.stdout.flush()
    os._exit(0)                                           # the heap of a whole parse is not walked on the way out


# ── the lab ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def lab_env(state_home, claude_dir):
    """The child's environment: nothing a live kernel exports (every ROMP_* and CLAUDE* name dropped), then the lab's own
    state root, a config directory under the lab root, no CLI, dead ports, and the young-session floor off as in the suite."""
    env = {k: v for k, v in os.environ.items() if not (k.startswith("ROMP_") or k.startswith("CLAUDE"))}
    env.update(XDG_STATE_HOME=state_home, CLAUDE_CONFIG_DIR=claude_dir, ROMP_KERNEL_NO_OPEN="1", ROMP_SERVE_TOKEN="lab",
               ROMP_CLAUDE_BIN="/bin/false", ROMP_MANAGER_PORT="1", ROMP_KERNEL_PORT="1", ROMP_SERVE_PORT="1",
               ROMP_POSTAL_HERMETIC="1", ROMP_POSTAL_CLIENT_ONLY="1", ROMP_MODEL_CATALOG="off", ROMP_CLI_SCOPE="0",
               ROMP_GC_FREEZE="off", ROMP_CKPT_FIRST_DOC_KB="0",
               ROMP_SERVICE_ENV_FILE=os.path.join(state_home, "no-such-service.env"),
               ROMP_SERVICE_ENV=os.path.join(state_home, "no-such-service.env"))
    return env


def run_road(root, road, big, small, a):
    state_home = os.path.join(root, "state-" + road)
    state = os.path.join(state_home, "romp")
    os.makedirs(state)
    Path(state, "session-hosts").write_text("off\n")       # no per-session host in a lab
    if road == "worker":
        Path(state, "doc-worker").write_text("on\n")
    cmd = [sys.executable, TOOL, "--child", "--road", road, "--big", big, "--small", small, "--gap-ms", str(a.gap_ms),
           "--baseline-s", str(a.baseline_s), "--gate", a.gate]
    with open(os.path.join(root, "stderr-" + road), "wb") as err:   # the kernel's own lines: shown when the road gave no result
        p = subprocess.run(cmd, env=lab_env(state_home, os.path.join(root, "claude")), stdout=subprocess.PIPE, stderr=err,
                           cwd=root)
    last = p.stdout.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
    try:
        res = json.loads(last[0])
    except ValueError:
        res = {"road": road, "error": "exit %s, no result line" % p.returncode}
        with open(os.path.join(root, "stderr-" + road), "rb") as err:
            sys.stderr.write(err.read().decode("utf-8", "replace")[-4000:])
    shutil.rmtree(state_home, ignore_errors=True)
    return res


def _mb(n):
    return "-" if n is None else "%.1f" % (n / 1048576.0)


def say(size_mb, res):
    pr = res.get("probe") or {}
    line = "%4d MB  %-9s n=%-5s p50 %-8s p90 %-8s p99 %-8s max %-9s" % (
        size_mb, res.get("road"), pr.get("n", "-"), pr.get("p50", "-"), pr.get("p90", "-"), pr.get("p99", "-"),
        pr.get("max", "-"))
    if res.get("error"):
        line += "  %s" % res["error"]
    if "rebuildS" in res:
        line += "  rebuild %s in %.1f s, document %s MB, next parse %s" % (
            json.dumps(res.get("rebuild")), res["rebuildS"], _mb(res.get("documentBytes")), "/".join(res.get("nextParse") or []))
    line += "  kernel peak %s MB, load %s" % (_mb(res.get("kernelVmHWM")), res.get("load", "-"))
    peak = (res.get("docWorker") or {}).get("peak")
    if peak:
        line += ("  worker resident peak %s MB (%.2f x the transcript; %s records, estimated %s MB), address space %s MB of "
                 "%s MB allowed" % (_mb(peak.get("vmHWM")), (peak.get("vmHWM") or 0) / float(max(1, peak.get("bytes") or 1)),
                                    peak.get("records"), _mb(peak.get("estimate")), _mb(peak.get("vmPeak")),
                                    _mb(peak.get("softBytes"))))
    dw = res.get("docWorker") or {}
    if dw.get("fallbacks") or dw.get("refused") or dw.get("memory"):
        line += "  (worker: fallbacks %s, refused %s, memory %s)" % (dw.get("fallbacks"), dw.get("refused"), dw.get("memory"))
    print(line, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sizes", default="100,300", help="the rebuilt transcript's sizes in MB (default 100,300)")
    ap.add_argument("--roads", default=",".join(ROADS), help="which of %s to run" % ", ".join(ROADS))
    ap.add_argument("--probe-turns", type=int, default=40, help="turns of the probe's small session (default 40)")
    ap.add_argument("--gap-ms", type=float, default=20.0, help="the pause between two probes (default 20)")
    ap.add_argument("--baseline-s", type=float, default=20.0, help="how long the baseline probes (default 20)")
    ap.add_argument("--gate", choices=("push", "none"), default="push",
                    help="push: the probe holds the background gate for its span, as a connect's push does (default); "
                         "none: only its own parse does")
    ap.add_argument("--json", help="also write every result to this file")
    ap.add_argument("--keep", action="store_true", help="keep the temporary root")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--road", help=argparse.SUPPRESS)
    ap.add_argument("--big", help=argparse.SUPPRESS)
    ap.add_argument("--small", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return child(a)
    roads = [r for r in a.roads.split(",") if r]
    for r in roads:
        if r not in ROADS:
            ap.error("no such road: %s" % r)
    results = []
    root = tempfile.mkdtemp(prefix="romp-doc-worker-lab-")
    try:
        proj = os.path.join(root, "claude", "projects", "-w-notes-api")
        os.makedirs(proj)
        small = os.path.join(proj, SMALL + ".jsonl")
        nb, turns = write_transcript(small, "s", turns=a.probe_turns, seed=1)
        print("probe session: %d turns, %.2f MB; a probe every %g ms; gate: %s; load average %.1f on %d cores" % (
            turns, nb / 1048576.0, a.gap_ms, a.gate, os.getloadavg()[0], os.cpu_count() or 1), flush=True)
        big = os.path.join(proj, BIG + ".jsonl")
        for size in [int(s) for s in a.sizes.split(",") if s]:
            nb, turns = write_transcript(big, "b", nbytes=size * 1048576, seed=size)
            print("rebuilt transcript: %.0f MB, %d turns" % (nb / 1048576.0, turns), flush=True)
            for road in roads:
                res = run_road(root, road, big, small, a)
                res["sizeMB"] = size
                results.append(res)
                say(size, res)
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=1)
    finally:
        if a.keep:
            print("kept: %s" % root)
        else:
            shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
