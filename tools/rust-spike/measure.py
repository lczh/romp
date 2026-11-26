#!/usr/bin/env python3
"""Step S6's measurement, the Python side (rust/SPIKE.md, "S6 method"). Each mode runs in a fresh process and prints one
JSON line; tools/rust-spike/measure_drive.py alternates it with `romp-spike measure` and keeps every run.

    python3 tools/rust-spike/measure.py cpu DUMP [--builds K] [--instrumented M]
    python3 tools/rust-spike/measure.py mem DIR PAYLOAD [--appends K] [--tracemalloc]
    python3 tools/rust-spike/measure.py perf --out FILE

`cpu`: Python's feed build in replay mode (dump_world.py's Replay harness: the dumped trees, stores and recorded helper
answers, an empty state root, perf-bench's guards), timed with time.process_time_ns() in a single-threaded process,
beside wall, thread and getrusage clocks. The first build is checked byte for byte against the dump's oracle (canonical,
wire and every memo entry) before anything is reported; the last build of every row is checked again. Rows:
  cold        the first build in the process: every memo empty, the interpreter's own caches cold;
  emptyMemo   K builds, each after clearing the feed memo, the fold's record, the held anchor aggregates
              (_FEED_SEG_HELD), _SUMMARY_ANCHOR_MEMO and _node_anchor_last: every session derives, every turn walked;
  heldWarm    K builds, each after clearing only the feed memo and the fold's record: every session derives, but the
              anchor walk serves its closed turns from the held aggregates and the landing memo is warm, as a
              production miss re-derives (no seam is stamped between builds, so the S4.5 stale landing cannot arise);
  memoServed  K builds with nothing cleared: every session is a memo hit (the replay's key is constant), no derivation.
Then M instrumented builds per row, with a wrapper on each function of build_feed's parts (the key, the derivation, the
fold, the parked handoffs, the notice cards, the frame's helpers, the pre-loop reads, the memo's bookkeeping, the
replay's helper stand-ins, _session_working, and the kernel's json.dumps/json.loads), each wrapped call's process CPU
attributed to its label and to the part it was called from. The plain rows carry no wrapper; the instrumented totals
show the wrappers' cost.

`mem`: Python's record cache (event_model._read_jsonl_incremental over _JSONL_CACHE) for every file of DIR: the
resident size (task_info through ctypes, getrusage's peak) before and after the cold read, then K rounds of appending
PAYLOAD/<file name> to each file and reading every file again, measured after each round. With --tracemalloc, the
traced bytes too (a separate run: tracing inflates the resident size), and after the last round the cache is cleared to
show the traced delta was the cache's. The driver makes DIR a fresh copy of a dump's transcripts with mtimes near now.

`perf`: GET /perf from the live kernel (port and token from its state directory; the token is never printed) and keep
only builds.feed (the memo's hit and miss counts, the held-walk counters, the build times), for the production miss
rate the driver folds in. Read-only; writes FILE (mode 0600) and nothing else.

Nothing is installed: stdlib only. Nothing is written inside the repo; scratch roots live under the system temp dir.
"""
import argparse
import ctypes
import gc
import json
import os
import platform
import resource
import shutil
import sys
import tempfile
import threading
import time
import tracemalloc
import urllib.request
from collections import Counter, defaultdict

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
SCHEMA = "romp-spike-measure-python/1"

# build_feed's parts, by label. OUTER labels open a region: a wrapped call inside them is attributed to them. Every wrapped
# call's time is kept under "label@region", the region being the innermost open outer label, or "build" at top level, so
# the "@build" keys partition the build's attributed time without double counting.
OUTER_KM = {
    "key": ["_feed_session_key"],          # the replay's key (dump_world.Replay.install), not production's
    "entry": ["_feed_session_entry"],      # the derivation (layer 6)
    "fold": ["_feed_fold_session"],        # the fold (layer 7), the decode of the entry string included
    "parked": ["_parked_handoffs"],        # postal-log fold and maildir stats (file work the port does not do)
    "notice": ["_notice_cards"],           # notice directory listing and memo checks (likewise)
    "frame": ["_views_payload", "_judge_limit_view", "_state_unknown_names", "_boards_data", "_undo_stack_ids",
              "_cleared_foreign", "_session_order", "_chat_tab_sessions", "_github_repo_of", "_session_cwd",
              "_boundary_clear_notices", "_sdk_problem_rows", "_sync_notice_rows", "_self_host", "_warm_fleet_bg"],
    "nameColor": ["_name_color"],          # at top level: the frame's sessions list and the parked cards
    "preloop": ["_cleared_ids_display", "_judge_error_rows", "_alive_sessions", "_wait_for_graph", "_stalled_goals",
                "_colormap", "_notify_cards", "_feed_bell_reader"],
    "memo": ["_feed_memo_get", "_feed_memo_put", "_feed_memo_miss", "_feed_memo_count", "_feed_key_with_deps",
             "_feed_fold_kept", "_feed_memo_forget", "_feed_fold_forget", "_subagent_trees_forget", "_feed_sub_record",
             "_feed_derive_recovered", "_feed_seg_build_begin", "_wire_default_in", "_feed_sub_reads"],
}
OUTER_JD = {"preloop": ["_debug_mode", "_auth_down_map", "active_runs"]}
INNER_KM = {"working": ["_session_working"],
            "standins": ["_captions", "_feed_goals_keyed", "_parse_cached", "_parse"]}   # plus every RECORDED helper
INNER_JD = {"standins": ["load_goals_shared_or_fault", "load_goals_shared", "load_goals"]}
EXCLUDED = ("parked", "notice", "frame", "nameColor", "preloop", "memo")


class MeasureError(Exception):
    pass


# ───────────────────────────── clocks and memory ─────────────────────────────
def clocks():
    ru = resource.getrusage(resource.RUSAGE_SELF)
    return (time.process_time_ns(), time.thread_time_ns(), int(round((ru.ru_utime + ru.ru_stime) * 1e9)),
            time.perf_counter_ns())


def span(a):
    b = clocks()
    return {"cpuNs": b[0] - a[0], "threadNs": b[1] - a[1], "ruNs": b[2] - a[2], "wallNs": b[3] - a[3]}


def calibrate(ms=100):
    """Spin for `ms` of wall time: a CPU clock that under-reports (time.thread_time() read about 42x low on this Mac under
    Rosetta, s2-design section 4) shows as a CPU/wall ratio far under 1."""
    a = clocks()
    end = time.perf_counter_ns() + ms * 1_000_000
    x = 0
    while time.perf_counter_ns() < end:
        for i in range(1000):
            x = (x * 6364136223846793005 + i) & 0xFFFFFFFFFFFFFFFF
    return span(a)


_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        _LIB = ctypes.CDLL(None)
    return _LIB


def _task():
    return ctypes.c_uint.in_dll(_lib(), "mach_task_self_").value


def _task_words(flavor):
    lib = _lib()
    lib.task_info.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint)]
    buf = (ctypes.c_uint * 128)()
    cnt = ctypes.c_uint(128)
    if lib.task_info(_task(), flavor, buf, ctypes.byref(cnt)) != 0:
        return None
    return buf, cnt.value


def _w64(ans, byte):
    if ans is None:
        return None
    buf, cnt = ans
    i = byte // 4
    return (buf[i] | (buf[i + 1] << 32)) if cnt >= i + 2 else None


def mem():
    """The process's memory now, in bytes: resident (MACH_TASK_BASIC_INFO), footprint and compressed (TASK_VM_INFO), the
    peak resident size (getrusage). The same calls romp-spike measure makes, through ctypes; null off macOS."""
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    out = {"resident": None, "footprint": None, "compressed": None, "maxrss": maxrss}
    if sys.platform == "darwin":
        try:
            basic, vm = _task_words(20), _task_words(22)
            out.update(resident=_w64(basic, 8), footprint=_w64(vm, 144), compressed=_w64(vm, 120))
        except (OSError, ValueError, AttributeError):
            pass
    return out


def os_threads():
    """The process's OS threads (task_threads), or None: process CPU counts every thread, so the timing needs one."""
    if sys.platform != "darwin":
        return None
    try:
        lib = _lib()
        task = _task()
        lst = ctypes.POINTER(ctypes.c_uint)()
        cnt = ctypes.c_uint(0)
        lib.task_threads.argtypes = [ctypes.c_uint, ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
                                     ctypes.POINTER(ctypes.c_uint)]
        if lib.task_threads(task, ctypes.byref(lst), ctypes.byref(cnt)) != 0:
            return None
        n = cnt.value
        lib.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
        for i in range(n):
            lib.mach_port_deallocate(task, lst[i])
        lib.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_size_t, ctypes.c_size_t]
        lib.vm_deallocate(task, ctypes.cast(lst, ctypes.c_void_p).value, n * 4)
        return n
    except (OSError, ValueError, AttributeError):
        return None


def interpreter():
    return {"version": platform.python_version(), "implementation": platform.python_implementation(),
            "machine": platform.machine(), "processTranslated": translated()}


def translated():
    """True when this process runs under Rosetta (sysctl.proc_translated), False native, None unknown."""
    if sys.platform != "darwin":
        return None
    try:
        lib = _lib()
        val = ctypes.c_int(0)
        size = ctypes.c_size_t(ctypes.sizeof(val))
        lib.sysctlbyname.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p,
                                     ctypes.c_size_t]
        if lib.sysctlbyname(b"sysctl.proc_translated", ctypes.byref(val), ctypes.byref(size), None, 0) != 0:
            return False
        return bool(val.value)
    except (OSError, AttributeError):
        return None


# ───────────────────────────── the attribution wrappers ─────────────────────────────
class Meter:
    """Wraps functions so each call's process CPU is kept under "label@region" (see OUTER_KM). Installed only for the
    instrumented builds."""

    def __init__(self):
        self.stack = []
        self.t = defaultdict(int)
        self.n = Counter()
        self.saved = []
        self.missing = []

    def wrap(self, obj, name, label, outer):
        if not hasattr(obj, name):
            self.missing.append(name)
            return
        fn = getattr(obj, name)
        stack, t, n, pt = self.stack, self.t, self.n, time.process_time_ns

        def w(*a, **k):
            region = stack[-1] if stack else "build"
            if outer:
                stack.append(label)
            t0 = pt()
            try:
                return fn(*a, **k)
            finally:
                dt = pt() - t0
                if outer:
                    stack.pop()
                key = label + "@" + region
                t[key] += dt
                n[key] += 1
        self.saved.append((obj, name, fn))
        setattr(obj, name, w)

    def wrap_json(self, km):
        """The kernel's own json.dumps and json.loads, timed by where they are called from: at top level the memo's
        serialization of a derived entry, inside the fold the decode of the entry string."""
        real = km.json

        class Proxy:
            def __getattr__(self, attr):
                return getattr(real, attr)
        p = Proxy()
        p.dumps, p.loads = real.dumps, real.loads
        self.wrap(p, "dumps", "json.dumps", False)
        self.wrap(p, "loads", "json.loads", False)
        self.saved.append((km, "json", real))
        km.json = p

    def install(self, km, recorded):
        for label, names in OUTER_KM.items():
            for name in names:
                self.wrap(km, name, label, True)
        for label, names in OUTER_JD.items():
            for name in names:
                self.wrap(km.jd, name, label, True)
        for label, names in INNER_KM.items():
            for name in names:
                self.wrap(km, name, label, False)
        for name in recorded:
            self.wrap(km, name, "standins", False)
        for label, names in INNER_JD.items():
            for name in names:
                self.wrap(km.jd, name, label, False)
        self.wrap_json(km)

    def uninstall(self):
        for obj, name, fn in reversed(self.saved):
            setattr(obj, name, fn)
        self.saved = []

    def take(self):
        out = {k: [self.t[k], self.n[k]] for k in sorted(self.t)}
        self.t.clear()
        self.n.clear()
        return out


def wrapper_overhead(n=20000):
    """One wrapped call's added process CPU (two clock reads and the bookkeeping), from n calls of a no-op."""
    class Holder:
        @staticmethod
        def f():
            return None
    bare = Holder.f
    a = time.process_time_ns()
    for _ in range(n):
        bare()
    bare_ns = time.process_time_ns() - a
    m = Meter()
    m.wrap(Holder, "f", "cal", True)
    f = Holder.f
    a = time.process_time_ns()
    for _ in range(n):
        f()
    wrapped_ns = time.process_time_ns() - a
    m.uninstall()
    return {"calls": n, "perCallNs": (wrapped_ns - bare_ns) / n}


def derived(attrib, total):
    """build_feed's process CPU split into the region both sides compute and the parts reported apart (rust/SPIKE.md,
    "S6 method"). `attrib` is Meter.take()'s {label@region: [ns, calls]}."""
    g = lambda k: attrib.get(k, [0, 0])[0]
    top = {k[:-len("@build")]: v[0] for k, v in attrib.items() if k.endswith("@build")}
    entry, fold, working = g("entry@build"), g("fold@build"), g("working@key")
    decode = g("json.loads@fold")
    out = {
        "totalNs": total,
        "derivationPlusFoldNs": entry + fold + working,
        "derivationPlusFoldNoDecodeNs": entry + fold + working - decode,
        "entryNs": entry, "foldNs": fold, "foldDecodeNs": decode, "workingInKeyNs": working,
        "standinsInEntryNs": g("standins@entry"), "standinsInEntryCalls": attrib.get("standins@entry", [0, 0])[1],
        "keyOwnNs": g("key@build") - working,
        "memoNs": top.get("memo", 0) + top.get("json.dumps", 0) + top.get("json.loads", 0),
        "memoDumpsNs": top.get("json.dumps", 0),
        "frameNs": top.get("frame", 0) + top.get("nameColor", 0),
        "preloopNs": top.get("preloop", 0), "parkedNs": top.get("parked", 0), "noticeNs": top.get("notice", 0),
        "residualNs": total - sum(top.values()),
        # what a memo hit costs: the key (the replay's stands in for production's, which stats files and reads stores),
        # the memo's bookkeeping and the fold of the kept cards; the memoServed row's comparable figure
        "servedNs": g("key@build") + top.get("memo", 0) + top.get("json.dumps", 0) + top.get("json.loads", 0) + fold,
        "wrappedCalls": sum(v[1] for v in attrib.values()),
    }
    return out


# ───────────────────────────── cpu ─────────────────────────────
def cpu_main(dump, builds, instrumented):
    import dump_world as dw
    out_doc = {"side": "python", "mode": "cpu", "schema": SCHEMA, "interpreter": interpreter(), "builds": builds,
               "instrumented": instrumented,
               "clocks": {"cpu": "time.process_time_ns", "thread": "time.thread_time_ns",
                          "ru": "resource.getrusage(RUSAGE_SELF) user+sys", "wall": "time.perf_counter_ns",
                          "memory": "task_info MACH_TASK_BASIC_INFO resident_size, TASK_VM_INFO phys_footprint and "
                                    "compressed (ctypes), getrusage ru_maxrss"}}
    mem()                                            # load ctypes' machinery before the first reading
    out_doc["calibration"] = calibrate(100)
    out_doc["memory"] = {"start": mem()}
    dump = os.path.realpath(dump)
    a = clocks()
    snap, trees = dw.load_dump(dump)
    out_doc["load"] = span(a)
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="romp-s6-py-"))
    try:
        root = os.path.join(scratch, "root")
        os.makedirs(root)
        dw.clean_env(root)
        state = os.path.join(root, "state", "romp")
        claude = os.path.join(root, "claude")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(os.path.join(claude, "projects"), exist_ok=True)
        a = clocks()
        _bcp, _pb, km, rec = dw.load_romp(root, state, claude)
        out_doc["kernelLoad"] = span(a)
        rp = dw.Replay(km, snap, trees)
        clear_all(dw, km)
        rp.install()
        real_time = time.time
        time.time = lambda: float(dw.NOW)           # a plain function: mock.patch's MagicMock would cost per call
        km._STARTED = float(dw.NOW)
        try:
            run_rows(dw, km, rp, dump, builds, instrumented, out_doc)
        finally:
            time.time = real_time
            rp.uninstall()
        out_doc["guards"] = {"spawns": len(rec["spawns"]), "refusedWrites": len(rec["refused_writes"]),
                             "threadStarts": rec["thread_starts"], "warmCalls": len(rec["warm_calls"])}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    problems = out_doc.setdefault("problems", [])
    if out_doc["guards"]["spawns"] or out_doc["guards"]["threadStarts"] or out_doc["guards"]["refusedWrites"]:
        problems.append("the guards saw a spawn, a thread start or a refused write: %r" % out_doc["guards"])
    return out_doc


def clear_all(dw, km):
    """Every memo the build reads across builds, as a fresh process has them (the port keeps none)."""
    dw.clear_build_memos(km)                         # the feed memo, _FEED_SEG_HELD, the fold's record, the landing memo
    km._node_anchor_last.clear()


def clear_feed_memo(km):
    """Only the feed memo and the fold's record: the next build derives every session, with the held anchor aggregates,
    the landing memo and the anchor table as the previous build left them."""
    with km._feed_memo_lock:
        km._feed_memo.clear()
        km._FEED_FOLD.clear()


def timed_build(km, now):
    g0 = [s["collections"] for s in gc.get_stats()]
    a = clocks()
    raw = km.build_feed(now, km._live_map())
    s = span(a)
    s["gc"] = [x["collections"] - y for x, y in zip(gc.get_stats(), g0)]
    return raw, s


def check(dw, km, rp, dump, raw, what):
    entries = {sid: km._feed_memo[sid][1] for sid in km._feed_memo}
    res = dw.compare_replay(dump, rp.assemble(raw), raw, rp, entries)
    ok = res["canonEqual"] and res["wireEqual"] and res["entriesEqual"]
    if not ok:
        raise MeasureError("the %s build differs from the oracle (%s): nothing is reported" % (what, res["firstDiff"]))
    return {"canonEqual": res["canonEqual"], "wireEqual": res["wireEqual"], "entriesEqual": res["entriesEqual"],
            "absentSids": res["absentSids"], "missingRecorded": res["missingRecorded"]}


def run_rows(dw, km, rp, dump, builds, instrumented, doc):
    now = dw.NOW
    gen2 = getattr(km, "_gc_gen2_apply", None)
    if gen2 is not None:
        gen2()                                       # the kernel's main() raises the full-collection trigger this way
    gc.collect()
    gc.freeze()                                      # the loaded trees out of the collector's walk, as the kernel's
    #                                                  gc_freeze does with its loaded heap (kernel/gc_freeze.py)
    doc["gc"] = {"frozen": gc.get_freeze_count(), "thresholds": list(gc.get_threshold()), "gen2Applied": gen2 is not None}
    doc["threads"] = {"python": threading.active_count(), "os": os_threads()}
    doc["memory"]["loaded"] = mem()
    feed = None
    feed, doc["cold"] = timed_build(km, now)
    doc["checks"] = {"cold": check(dw, km, rp, dump, feed, "cold")}
    rows = {}
    for row in ("emptyMemo", "heldWarm", "memoServed"):
        spans = []
        for _ in range(builds):
            feed = None
            if row == "emptyMemo":
                clear_all(dw, km)
            elif row == "heldWarm":
                clear_feed_memo(km)
            feed, s = timed_build(km, now)
            spans.append(s)
        rows[row] = spans
        doc["checks"][row] = check(dw, km, rp, dump, feed, row)
    doc["rows"] = rows
    held = km._FEED_SEG_STATS if hasattr(km, "_FEED_SEG_STATS") else {}
    doc["heldStats"] = {k: held.get(k) for k in ("segWalks", "segHeld", "segWalkTurns", "segReset", "segPartial")}
    doc["threads"]["osAfter"] = os_threads()
    if instrumented:
        doc["wrapperOverhead"] = wrapper_overhead()
        inst = {}
        for row in ("emptyMemo", "heldWarm", "memoServed"):
            meter = Meter()
            if row == "memoServed":
                clear_feed_memo(km)                  # fill the memo once, uninstrumented
                feed = None
                feed, _s = timed_build(km, now)
            meter.install(km, list(dw.RECORDED))
            runs = []
            try:
                for _ in range(instrumented):
                    feed = None
                    if row == "emptyMemo":
                        clear_all(dw, km)
                    elif row == "heldWarm":
                        clear_feed_memo(km)
                    feed, s = timed_build(km, now)
                    attrib = meter.take()
                    runs.append({"span": s, "attrib": attrib, "derived": derived(attrib, s["cpuNs"])})
            finally:
                meter.uninstall()
            inst[row] = {"runs": runs, "unwrapped": meter.missing}
            doc["checks"]["instrumented " + row] = check(dw, km, rp, dump, feed, row)
        doc["instrumentedRows"] = inst
    doc["memory"]["end"] = mem()


# ───────────────────────────── mem ─────────────────────────────
def sorted_files(d):
    names = sorted(n for n in os.listdir(d) if os.path.isfile(os.path.join(d, n)))
    if not names:
        raise MeasureError("no files in %s" % d)
    return [os.path.join(d, n) for n in names]


def cache_counted(em, paths):
    files = records = held = 0
    bases, gens = [], {}
    for p in paths:
        ent = em._JSONL_CACHE.get(p)
        if ent is None:
            continue
        files += 1
        records += len(ent[4])
        held += int(ent[1])
        bases.append(int(ent[5]))
        gens[p] = ent[6]
    return {"files": files, "records": records, "heldBytes": held, "cacheBytes": em._JSONL_CACHE_BYTES[0],
            "nonWholeEntries": sum(1 for b in bases if b != 0)}, gens


def mem_main(d, payload, appends, trace):
    import check_store as cs
    doc = {"side": "python", "mode": "mem", "schema": SCHEMA, "interpreter": interpreter(), "appends": appends,
           "tracemalloc": trace, "problems": []}
    mem()
    paths = sorted_files(os.path.realpath(d))
    doc["files"] = len(paths)
    doc["fileBytes"] = sum(os.path.getsize(p) for p in paths)
    scratch = os.path.realpath(tempfile.mkdtemp(prefix="romp-s6-pymem-"))
    try:
        em = cs.load_event_model(scratch)
        doc["cacheLimits"] = {"countCap": em._JSONL_CACHE_MAX, "budgetBytes": em._JSONL_CACHE_BUDGET_BYTES,
                              "decodedMultiple": em._RECORD_CACHE_MULTIPLE}
        gc.collect()
        if trace:
            tracemalloc.start()
        traced = (lambda: tracemalloc.get_traced_memory()[0]) if trace else (lambda: None)
        gc.collect()
        doc["before"] = {"memory": mem(), "traced": traced()}
        a = clocks()
        for p in paths:
            em._read_jsonl_incremental(p)
        read = span(a)
        em._LAST_ENTRY.ent = None
        gc.collect()
        counted, gens = cache_counted(em, paths)
        doc["cold"] = {"memory": mem(), "traced": traced(), "counted": counted, "read": read}
        if counted["files"] != len(paths) or counted["nonWholeEntries"]:
            doc["problems"].append("the cold read left %d of %d files whole in the cache" % (counted["files"], len(paths)))
        rounds = []
        held = counted["heldBytes"]
        for r in range(appends):
            added = 0
            for p in paths:
                with open(os.path.join(payload, os.path.basename(p)), "rb") as f:
                    data = f.read()
                with open(p, "ab") as f:
                    f.write(data)
                added += len(data)
            a = clocks()
            for p in paths:
                em._read_jsonl_incremental(p)
            read = span(a)
            em._LAST_ENTRY.ent = None
            gc.collect()
            counted, gens2 = cache_counted(em, paths)
            moved = [os.path.basename(p) for p in paths if gens2.get(p) != gens.get(p)]
            if moved:
                doc["problems"].append("round %d: not an append read (a new generation): %s" % (r + 1, moved[:5]))
            if not added or counted["heldBytes"] != held + added:
                doc["problems"].append("round %d: the cache holds %d bytes, not %d + %d appended"
                                       % (r + 1, counted["heldBytes"], held, added))
            held = counted["heldBytes"]
            rounds.append({"round": r + 1, "memory": mem(), "traced": traced(), "counted": counted, "read": read})
        doc["rounds"] = rounds
        stats = em._RECORD_CACHE_STATS
        doc["cacheStats"] = {k: stats.get(k) for k in ("inserts", "evictions", "budgetEvictions", "dropped")}
        if stats.get("evictions") or stats.get("dropped"):
            doc["problems"].append("the cache evicted or dropped entries: %r" % doc["cacheStats"])
        # the self-check: clearing the cache must give the traced bytes back, so the delta measured was the cache's
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
            em._JSONL_CACHE_BYTES[0] = 0
        gc.collect()
        doc["cleared"] = {"memory": mem(), "traced": traced()}
        if trace:
            doc["tracedPeak"] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return doc


# ───────────────────────────── perf ─────────────────────────────
def perf_main(out):
    st = os.path.join(os.path.expanduser("~"), ".local", "state", "romp")
    with open(os.path.join(st, "serve-port"), encoding="utf-8") as f:
        port = int(f.read().strip())
    with open(os.path.join(st, "serve-token"), encoding="utf-8") as f:
        token = f.read().strip()
    req = urllib.request.Request("http://127.0.0.1:%d/perf" % port, headers={"X-Romp-Token": token})
    del token
    with urllib.request.urlopen(req, timeout=20) as r:
        d = json.load(r)
    feed = (d.get("builds") or {}).get("feed") or {}
    keep = {"schema": "romp-spike-perf/1", "fetchedUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "uptime_s": d.get("uptime_s"), "since": d.get("since"), "now": d.get("now"),
            "buildsFeed": {k: feed.get(k) for k in ("built", "cached", "ms", "memo")}}
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(keep, f, indent=1, sort_keys=True)
    m = keep["buildsFeed"].get("memo") or {}
    print("wrote %s: builds.feed (built, cached, ms, memo with %d counters)" % (out, len(m)))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    c = sub.add_parser("cpu")
    c.add_argument("dump")
    c.add_argument("--builds", type=int, default=10)
    c.add_argument("--instrumented", type=int, default=3)
    m = sub.add_parser("mem")
    m.add_argument("dir")
    m.add_argument("payload")
    m.add_argument("--appends", type=int, default=2)
    m.add_argument("--tracemalloc", action="store_true")
    p = sub.add_parser("perf")
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        if a.mode == "perf":
            return perf_main(a.out)
        if a.mode == "cpu":
            if a.builds < 1 or a.instrumented < 0:
                ap.error("--builds must be at least 1 and --instrumented at least 0")
            doc = cpu_main(a.dump, a.builds, a.instrumented)
        else:
            doc = mem_main(a.dir, a.payload, a.appends, a.tracemalloc)
    except MeasureError as e:
        sys.stderr.write("measure.py: %s\n" % e)
        return 2
    print(json.dumps(doc))
    return 1 if doc.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())
