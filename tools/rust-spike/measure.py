#!/usr/bin/env python3
"""Step S6's measurement, the Python side (rust/SPIKE.md, "S6 method"). Each mode runs in a fresh process and prints one
JSON line; tools/rust-spike/measure_drive.py alternates it with `romp-spike measure` and keeps every run.

    python3 tools/rust-spike/measure.py cpu DUMP [--builds K] [--instrumented M] [--memo]
    python3 tools/rust-spike/measure.py mem DIR PAYLOAD [--appends K] [--tracemalloc] [--relief]
    python3 tools/rust-spike/measure.py perf --out FILE

`cpu`: Python's feed build in replay mode (dump_world.py's Replay harness: the dumped trees, stores and recorded helper
answers, an empty state root, perf-bench's guards), timed with time.process_time_ns() in a single-threaded process,
beside wall, thread and getrusage clocks. The first build is checked byte for byte against the dump's oracle (canonical,
wire and every memo entry) before anything is reported; the last build of every row is checked again. Rows:
  cold        the first build in the process: every memo empty, the interpreter's own caches cold;
  noHold      every memo cleared as for emptyMemo, and the replay key hands no leaf head (ctx["leaf_head"] = None, read
              only by _feed_seg_walk), so the walk computes no hold key, copies no map and stores no aggregate: the
              port's algorithm (every turn walked, nothing held), the same work on both sides;
  emptyMemo   each build after clearing the feed memo, the fold's record, the held anchor aggregates (_FEED_SEG_HELD),
              _SUMMARY_ANCHOR_MEMO and _node_anchor_last: every session derives, every turn walked, and the walk also
              builds the hold keys and stores each session's aggregate;
  heldWarm    each build after clearing only the feed memo and the fold's record: every session derives, but the anchor
              walk serves its closed turns from the held aggregates and the landing memo is warm, as a production miss
              re-derives (no seam is stamped between builds, so the S4.5 stale landing cannot arise);
  heldNoLanding  heldWarm with _SUMMARY_ANCHOR_MEMO cleared too: the held aggregates serve, the landing memo does not,
              as the port, which has no landing memo (the size of the one Python cache the port still lacks);
  memoServed  nothing cleared: every session is a memo hit (the replay's key is constant), no derivation.
With --memo (step S6.1), a second phase after these rows, with the REAL _feed_session_key and _feed_key_with_deps over
the dump's recorded key facts (tools/rust-spike/dump_memo.py's KeyHarness: the files the key stats and the revisions it
reads answered from the snapshot's keyFacts, the same inputs the port's key reads), warmed by three builds (cold, the peers
settle, served whole; the third checked against the oracle and required to be all hits). Two rows, round-robin:
  memoKeyed      nothing cleared: every session's real key is computed and every session is a hit;
  memoKeyedMiss  the largest session's recorded transcript identity moves before each build (same inode, so its held
                 aggregate serves): that session misses and derives, every other session hits.
Their instrumented builds report `regionNs`: the key, the memo's bookkeeping, the entry's serialization and decode, the
derivation and the fold, the replay's stand-ins (inside the key: the harness's leaves, the recorded helpers and the store
copy) out, the same loop the port's memo rows time.
The rows run round-robin, one build of each per round, even rounds in the order above and odd rounds in ROWS_ODD's (the
held rows need the aggregates emptyMemo just stored, memoServed a memo a build just filled), K rounds; each build keeps its collections per generation and the
held walk's counters (segHeld, segWalkTurns, ...). Then M instrumented builds per row, with a wrapper on each function
of build_feed's parts (the key, the derivation, the fold, the parked handoffs, the notice cards, the frame's helpers,
the pre-loop reads, the memo's bookkeeping, the replay's helper stand-ins, _session_working, and the kernel's
json.dumps/json.loads), each wrapped call's process CPU attributed to its label, to the part it was called from and to
the session whose turn of the loop it ran in, round-robin as the plain rows. What the wrappers record between two builds
(the clears) is discarded.
The plain rows carry no wrapper; the instrumented totals show the wrappers' cost. A calibration spin at the start and at
the end counts its loop iterations per CPU microsecond (a run that met a slower core or clock shows a lower rate).

`mem`: Python's record cache (event_model._read_jsonl_incremental over _JSONL_CACHE) for every file of DIR: the process's
memory (resident size, footprint and compressed bytes through task_info by ctypes; getrusage's peak) before and after
the cold read, then K rounds: append PAYLOAD/<round>/<file name> to each file that has one (rounds counted from 1), read
every file again, measure. With --tracemalloc, the traced bytes too (a separate run: tracing inflates the resident
size), and after the last round the cache is cleared: the traced bytes must fall back to the baseline (within 1% of the
cold growth, or 64 KiB for the reader's read counters), which shows the delta was the cache's. With --relief, every
reading is followed by a second one after malloc_zone_pressure_relief(NULL, 0), which hands back what the allocator
keeps free. The driver makes DIR a fresh copy of the transcripts with mtimes near now and writes the append schedule.

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
# The calls that tell whose turn of build_feed's per-session loop it is (called at top level): the per-session
# attribution keeps every wrapped call under that session until _feed_memo_forget, which build_feed calls after the loop.
SID_OF = {"_feed_memo_get": lambda a: a[0], "_feed_session_key": lambda a: a[0]["sid"],
          "_feed_session_entry": lambda a: a[0]["sid"], "_feed_fold_session": lambda a: a[0]}
LOOP_END = "_feed_memo_forget"
ROWS = ("noHold", "emptyMemo", "heldWarm", "heldNoLanding", "memoServed")
# Odd rounds run this order instead, so no row always follows the same one (a row's predecessor moved it by about 15%
# in the first smoke runs: heldNoLanding, right after heldWarm, came out faster than heldWarm). Both orders keep what
# each row needs: the held rows come after emptyMemo with no noHold between, memoServed after a build filled the memo.
ROWS_ODD = ("emptyMemo", "heldNoLanding", "heldWarm", "memoServed", "noHold")
SEG_KEYS = ("segWalks", "segHeld", "segWalkTurns", "segReset", "segPartial", "segRaised", "segEvict", "segOverBound")


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
    n = 0
    while time.perf_counter_ns() < end:
        for i in range(1000):
            x = (x * 6364136223846793005 + i) & 0xFFFFFFFFFFFFFFFF
        n += 1000
    s = span(a)
    s["iterations"] = n
    s["loopsPerCpuUs"] = n * 1000 / s["cpuNs"] if s["cpuNs"] > 0 else 0.0
    return s


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
    default malloc zone's bytes in use and total (mstats), the peak resident size (getrusage). The same calls romp-spike
    measure makes, through ctypes; null off macOS."""
    maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
    out = {"resident": None, "footprint": None, "compressed": None, "maxrss": maxrss, "mallocInUse": None,
           "mallocTotal": None}
    if sys.platform == "darwin":
        try:
            basic, vm = _task_words(20), _task_words(22)
            out.update(resident=_w64(basic, 8), footprint=_w64(vm, 144), compressed=_w64(vm, 120))
            lib = _lib()
            lib.mstats.restype = _Mstats
            m = lib.mstats()
            out.update(mallocInUse=m.bytes_used, mallocTotal=m.bytes_total)
        except (OSError, ValueError, AttributeError):
            pass
    return out


class _Mstats(ctypes.Structure):
    """<malloc/malloc.h> struct mstats: the default zone's bytes (pymalloc's arenas are mapped outside it)."""
    _fields_ = [("bytes_total", ctypes.c_size_t), ("chunks_used", ctypes.c_size_t), ("bytes_used", ctypes.c_size_t),
                ("chunks_free", ctypes.c_size_t), ("bytes_free", ctypes.c_size_t)]


def relieved():
    """malloc_zone_pressure_relief(NULL, 0) and the memory after it: what the system allocator kept free, handed back
    (pymalloc's arenas are not malloc's; an empty arena is unmapped when it empties)."""
    released = None
    if sys.platform == "darwin":
        try:
            lib = _lib()
            lib.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
            lib.malloc_zone_pressure_relief.restype = ctypes.c_size_t
            released = lib.malloc_zone_pressure_relief(None, 0)
        except (OSError, AttributeError):
            pass
    return {"released": released, "memory": mem()}


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
        self.bysid = defaultdict(lambda: defaultdict(int))
        self.sid = [None]
        self.saved = []
        self.missing = []

    def wrap(self, obj, name, label, outer):
        if not hasattr(obj, name):
            self.missing.append(name)
            return
        fn = getattr(obj, name)
        stack, t, n, pt, bysid, cur = self.stack, self.t, self.n, time.process_time_ns, self.bysid, self.sid
        sid_of = SID_OF.get(name)
        ends_loop = name == LOOP_END

        def w(*a, **k):
            region = stack[-1] if stack else "build"
            if region == "build":
                if sid_of is not None:
                    try:
                        cur[0] = str(sid_of(a))
                    except (IndexError, KeyError, TypeError):
                        cur[0] = None
                elif ends_loop:
                    cur[0] = None
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
                if cur[0] is not None:
                    bysid[cur[0]][key] += dt
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
        self.sid[0] = None
        return out

    def take_sids(self):
        out = {sid: dict(sorted(v.items())) for sid, v in sorted(self.bysid.items())}
        self.bysid.clear()
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


def derived(attrib, total, per_call_ns=None):
    """build_feed's process CPU split into the region both sides compute and the parts reported apart (rust/SPIKE.md,
    "S6 method"). `attrib` is Meter.take()'s {label@region: [ns, calls]}.

    The region (`regionNs`) is the derivation plus the fold plus the _session_working call inside the key (the port
    computes working inside its derivation), less the memo's serialization (the fold's json.loads of the entry string
    and the miss path's json.dumps, a memo artifact the port does not have, reported together as `memoSerdeNs`), less
    the memo's bookkeeping the fold calls (_feed_fold_kept, `memoInFoldNs`) and less the replay's helper stand-ins (deep
    copies of recorded answers, `standinsNs`; the port looks the same answers up in its snapshot). `regionCorrectedNs` takes the wrappers' own cost out: per_call_ns for every wrapped call made inside
    the region that stays in it, half of it for those subtracted (their own clock reads sit partly inside what was
    subtracted), with `wrapperUncertaintyNs` the other half as the bound on that split.

    What a memo hit and a miss cost (`hitNs`, `missNs`) leave out the key (the replay's stand-in key understates
    production's, which stats files and reads stores) and the stand-ins: hit = the memo's bookkeeping + its
    serialization + the fold; miss = hit + the derivation + working."""
    g = lambda k: attrib.get(k, [0, 0])[0]
    c = lambda k: attrib.get(k, [0, 0])[1]
    top = {k[:-len("@build")]: v[0] for k, v in attrib.items() if k.endswith("@build")}
    top_calls = sum(v[1] for k, v in attrib.items() if k.endswith("@build"))
    entry, fold, working = g("entry@build"), g("fold@build"), g("working@key")
    decode, dumps = g("json.loads@fold"), g("json.dumps@build")
    standins = g("standins@entry") + g("standins@fold")
    memo_fold = g("memo@fold")
    sub_calls = c("json.loads@fold") + c("standins@entry") + c("standins@fold") + c("memo@fold")
    inner_calls = sum(v[1] for k, v in attrib.items() if k.endswith(("@entry", "@fold")))
    region = entry + fold + working - decode - standins - memo_fold
    residual = total - sum(top.values())
    out = {
        "totalNs": total,
        "regionNs": region,
        "derivationPlusFoldNs": entry + fold + working,
        "entryNs": entry, "foldNs": fold, "foldDecodeNs": decode, "workingInKeyNs": working,
        "memoDumpsNs": dumps, "memoSerdeNs": decode + dumps, "memoInFoldNs": memo_fold,
        "standinsNs": standins, "standinsCalls": c("standins@entry") + c("standins@fold"),
        "nameColorInEntryNs": g("nameColor@entry"),
        "keyOwnNs": g("key@build") - working,
        "memoBookNs": top.get("memo", 0) + top.get("json.loads", 0),
        "frameNs": top.get("frame", 0) + top.get("nameColor", 0),
        "preloopNs": top.get("preloop", 0), "parkedNs": top.get("parked", 0), "noticeNs": top.get("notice", 0),
        "hitNs": top.get("memo", 0) + top.get("json.loads", 0) + dumps + fold,
        "missNs": top.get("memo", 0) + top.get("json.loads", 0) + dumps + fold + entry + working - standins,
        "residualNs": residual,
        "topCalls": top_calls,
        # a residual below this is not clock quantization (1 us steps, unbiased): something is counted twice
        "residualFloorNs": -2000 * top_calls,
        "innerCallsInRegion": inner_calls,
        "subtractedCalls": sub_calls,
        "wrappedCalls": sum(v[1] for v in attrib.values()),
    }
    if per_call_ns is not None:
        keep = inner_calls - sub_calls
        out["regionCorrectedNs"] = region - keep * per_call_ns - sub_calls * per_call_ns / 2
        out["wrapperUncertaintyNs"] = sub_calls * per_call_ns / 2
        out["totalLessWrappersNs"] = total - out["wrappedCalls"] * per_call_ns
    return out


def per_session(bysid):
    """The per-session hit and miss figures from Meter.take_sids() (the same definitions as `derived`)."""
    out = {}
    for sid, a in bysid.items():
        g = lambda k: a.get(k, 0)
        hit = g("memo@build") + g("json.loads@build") + g("json.dumps@build") + g("fold@build")
        out[sid] = {"hitNs": hit,
                    "missNs": hit + g("entry@build") + g("working@key") - g("standins@entry") - g("standins@fold"),
                    "regionNs": (g("entry@build") + g("fold@build") + g("working@key") - g("json.loads@fold")
                                 - g("standins@entry") - g("standins@fold") - g("memo@fold"))}
    return out


# ───────────────────────────── cpu ─────────────────────────────
# the harness's leaves (dump_memo.KeyHarness) the memo phase counts as stand-ins, beside the recorded helpers
MEMO_STANDINS_KM = ("_chat_ident", "_feed_reg_sig", "_postal_session_slice", "_rewind_hold_get", "_watch_awaiting",
                    "_usage_cap_open", "_auth_key_present", "_auto_nudge_data", "_nudge_times", "_postal_maps_indexed",
                    "_bg_live_norm")
MEMO_STANDINS_JD = ("_store_identity", "shared_replay_wrote")
MEMO_ROWS = ("memoKeyed", "memoKeyedMiss")


def derived_memo(attrib, total, per_call_ns=None):
    """A memo-phase build split as `derived` splits the others: `regionNs` is everything build_feed's per-session loop
    does (the key, the memo's bookkeeping, the serialization and decode, the derivation, the fold) less the replay's
    stand-ins called inside it; `regionCorrectedNs` takes the wrappers' cost out the same way."""
    g = lambda k: attrib.get(k, [0, 0])[0]
    c = lambda k: attrib.get(k, [0, 0])[1]
    parts = ("key", "memo", "json.dumps", "json.loads", "entry", "fold")
    top = sum(g(p + "@build") for p in parts)
    sub = g("standins@key") + g("standins@entry") + g("standins@fold") + g("standins@memo")
    sub_calls = c("standins@key") + c("standins@entry") + c("standins@fold") + c("standins@memo")
    inner_calls = sum(v[1] for k, v in attrib.items() if k.endswith(("@key", "@entry", "@fold", "@memo")))
    out = {"totalNs": total, "regionNs": top - sub, "keyNs": g("key@build"), "keyStandinsNs": g("standins@key"),
           "keyLessStandinsNs": g("key@build") - g("standins@key"), "memoBookNs": g("memo@build"),
           "memoSerdeNs": g("json.dumps@build") + g("json.loads@build") + g("json.loads@fold"),
           "entryNs": g("entry@build"), "foldNs": g("fold@build"), "standinsNs": sub, "standinsCalls": sub_calls,
           "frameNs": g("frame@build") + g("nameColor@build"), "preloopNs": g("preloop@build"),
           "wrappedCalls": sum(v[1] for v in attrib.values())}
    if per_call_ns is not None:
        keep = inner_calls - sub_calls
        out["regionCorrectedNs"] = out["regionNs"] - keep * per_call_ns - sub_calls * per_call_ns / 2
        out["wrapperUncertaintyNs"] = sub_calls * per_call_ns / 2
        out["totalLessWrappersNs"] = total - out["wrappedCalls"] * per_call_ns
    return out


def run_memo_rows(dw, km, dump, snap, trees, builds, instrumented, doc):
    """The --memo phase (the module docstring): the real key over the recorded facts, two rows round-robin."""
    import copy
    import importlib.util
    import dump_memo as dm
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer6", os.path.join(HERE, "dump_layer6.py"))
    l6 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l6)
    now = dw.NOW
    snap = copy.deepcopy(snap)
    turns = {sid: len((t or {}).get("turns") or []) for sid, t in trees.items()}
    big = min(turns, key=lambda k: (-turns[k], k))
    clear_all(dw, km)
    real_key, real_deps = km._feed_session_key, km._feed_key_with_deps
    rp = dw.Replay(km, snap, trees)
    rp.install()
    h = dm.KeyHarness(dw, l6, km, rp, snap)
    h.install(real_key, real_deps)
    km._HEAL_LOG.update(n=10 ** 9, h=10 ** 9)
    files = snap.setdefault("keyFacts", {}).setdefault("files", {})
    stamp = [0]

    def move_big():
        stamp[0] += 1
        files["transcript:" + big] = [0, stamp[0], 0, 0]

    def counts():
        d = list(h.decisions.values())
        return {"hits": sum(1 for x in d if x.get("hit")), "misses": sum(1 for x in d if "miss" in x),
                "raises": sum(1 for x in d if "raise" in x)}
    out = {"missSession": big, "checks": {}, "rows": {r: [] for r in MEMO_ROWS}}
    try:
        for what in ("cold", "peers settle", "served"):
            h.decisions = {}
            feed, s = timed_build(km, now)
            out["checks"][what] = dict(check(dw, km, rp, dump, feed, "memo " + what), memo=counts())
        if out["checks"]["served"]["memo"]["hits"] != len(snap["alive"]):
            raise MeasureError("the third memo build is not served whole: %r" % out["checks"]["served"]["memo"])
        for i in range(builds):
            for row in (MEMO_ROWS[::-1] if i % 2 else MEMO_ROWS):
                if row == "memoKeyedMiss":
                    move_big()
                h.decisions = {}
                feed, s = timed_build(km, now)
                s["memo"] = counts()
                out["rows"][row].append(s)
        out["checks"]["after rounds"] = check(dw, km, rp, dump, feed, "memo after the rounds")
        if instrumented:
            per_call = (doc.get("wrapperOverhead") or wrapper_overhead())["perCallNs"]
            inst = {row: {"runs": []} for row in MEMO_ROWS}
            meter = Meter()
            meter.install(km, list(dw.RECORDED) + [n for n in MEMO_STANDINS_KM if n not in dw.RECORDED])
            for n in MEMO_STANDINS_JD:
                meter.wrap(km.jd, n, "standins", False)
            try:
                for i in range(instrumented):
                    for row in (MEMO_ROWS[::-1] if i % 2 else MEMO_ROWS):
                        if row == "memoKeyedMiss":
                            move_big()
                        h.decisions = {}
                        meter.take()
                        meter.take_sids()
                        feed, s = timed_build(km, now)
                        attrib = meter.take()
                        meter.take_sids()
                        inst[row]["runs"].append({"span": s, "attrib": attrib, "memo": counts(),
                                                  "derived": derived_memo(attrib, s["cpuNs"], per_call)})
            finally:
                meter.uninstall()
            out["instrumentedRows"] = inst
    finally:
        h.uninstall()
        rp.uninstall()
    doc["memoRows"] = out


def cpu_main(dump, builds, instrumented, memo=False):
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
    out_doc["sessionTurns"] = {sid: len((t or {}).get("turns") or []) for sid, t in trees.items()}
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
        if memo:
            time.time = lambda: float(dw.NOW)
            try:
                run_memo_rows(dw, km, dump, snap, trees, builds, instrumented, out_doc)
            finally:
                time.time = real_time
        out_doc["guards"] = {"spawns": len(rec["spawns"]), "refusedWrites": len(rec["refused_writes"]),
                             "threadStarts": rec["thread_starts"], "warmCalls": len(rec["warm_calls"])}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    out_doc["calibrationEnd"] = calibrate(100)
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


def seg_stats(km):
    st = getattr(km, "_FEED_SEG_STATS", None) or {}
    return {k: st.get(k, 0) for k in SEG_KEYS}


def timed_build(km, now):
    g0 = [s["collections"] for s in gc.get_stats()]
    seg0 = seg_stats(km)
    a = clocks()
    raw = km.build_feed(now, km._live_map())
    s = span(a)
    s["gc"] = [x["collections"] - y for x, y in zip(gc.get_stats(), g0)]
    seg1 = seg_stats(km)
    s["seg"] = {k: seg1[k] - seg0[k] for k in SEG_KEYS}
    return raw, s


class NoHold:
    """The noHold row's key: the replay's key, then ctx["leaf_head"] = None, which only _feed_seg_walk reads (no leaf
    head: no hold key computed, nothing served from or stored in _FEED_SEG_HELD). Installed for that row's builds."""

    def __init__(self, km):
        self.km = km
        self.real = None

    def __enter__(self):
        real = self.real = self.km._feed_session_key

        def key(s, tm, ctx, prev):
            k = real(s, tm, ctx, prev)
            ctx["leaf_head"] = None
            return k
        self.km._feed_session_key = key
        return self

    def __exit__(self, *exc):
        self.km._feed_session_key = self.real
        return False


def setup_row(dw, km, row):
    if row in ("noHold", "emptyMemo"):
        clear_all(dw, km)
    elif row == "heldWarm":
        clear_feed_memo(km)
    elif row == "heldNoLanding":
        clear_feed_memo(km)
        km._SUMMARY_ANCHOR_MEMO.clear()


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
    doc["segBound"] = getattr(km, "FEED_SEG_HELD_BYTES", None)
    doc["memory"]["loaded"] = mem()
    feed = None
    feed, doc["cold"] = timed_build(km, now)
    doc["checks"] = {"cold": check(dw, km, rp, dump, feed, "cold")}
    rows = {row: [] for row in ROWS}
    for i in range(builds):
        for row in (ROWS_ODD if i % 2 else ROWS):    # round-robin: one build of every row per round
            feed = None
            setup_row(dw, km, row)
            if row == "noHold":
                with NoHold(km):
                    feed, s = timed_build(km, now)
            else:
                feed, s = timed_build(km, now)
            rows[row].append(s)
            if i == builds - 1:
                doc["checks"][row] = check(dw, km, rp, dump, feed, row)
    doc["rowOrder"] = list(ROWS)
    doc["rows"] = rows
    doc["threads"]["osAfter"] = os_threads()
    if instrumented:
        doc["wrapperOverhead"] = wrapper_overhead()
        per_call = doc["wrapperOverhead"]["perCallNs"]
        inst = {row: {"runs": []} for row in ROWS}
        meter = Meter()
        meter.install(km, list(dw.RECORDED))
        try:
            for i in range(instrumented):
                for row in (ROWS_ODD if i % 2 else ROWS):   # round-robin, as the plain rows
                    feed = None
                    setup_row(dw, km, row)
                    outside = meter.take()           # what the clears ran through wrapped functions: not the build's
                    meter.take_sids()
                    if row == "noHold":
                        with NoHold(km):             # outside the meter's wrapper of the key: its cost is residual
                            feed, s = timed_build(km, now)
                    else:
                        feed, s = timed_build(km, now)
                    attrib = meter.take()
                    bysid = meter.take_sids()
                    inst[row]["runs"].append({"span": s, "attrib": attrib, "derived": derived(attrib, s["cpuNs"], per_call),
                                              "bySid": per_session(bysid),
                                              "outsideBuildNs": sum(v[0] for v in outside.values())})
                    if i == instrumented - 1:
                        doc["checks"]["instrumented " + row] = check(dw, km, rp, dump, feed, row)
        finally:
            meter.uninstall()
        for row in ROWS:
            inst[row]["unwrapped"] = meter.missing
            for k, run in enumerate(inst[row]["runs"]):
                d = run["derived"]
                if d["residualNs"] < d["residualFloorNs"]:
                    doc.setdefault("problems", []).append(
                        "instrumented %s build %d: the residual %.3f ms is below -(top-level calls x 2 us) = %.3f ms: "
                        "a part is counted twice" % (row, k, d["residualNs"] / 1e6, d["residualFloorNs"] / 1e6))
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


def mem_main(d, payload, appends, trace, relief):
    import check_store as cs
    doc = {"side": "python", "mode": "mem", "schema": SCHEMA, "interpreter": interpreter(), "appends": appends,
           "tracemalloc": trace, "relief": relief, "problems": []}
    mem()
    if relief:
        relieved()                                   # load the call before the first reading
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

        def reading(**extra):
            r = dict(extra, memory=mem(), traced=traced())
            if relief:
                r["relieved"] = relieved()
            return r
        gc.collect()
        doc["before"] = reading()
        a = clocks()
        for p in paths:
            em._read_jsonl_incremental(p)
        read = span(a)
        em._LAST_ENTRY.ent = None
        gc.collect()
        counted, gens = cache_counted(em, paths)
        doc["cold"] = reading(counted=counted, read=read)
        if counted["files"] != len(paths) or counted["nonWholeEntries"]:
            doc["problems"].append("the cold read left %d of %d files whole in the cache" % (counted["files"], len(paths)))
        rounds = []
        held = counted["heldBytes"]
        appended_total = 0
        for r in range(1, appends + 1):
            rdir = os.path.join(payload, str(r))
            if not os.path.isdir(rdir):
                raise MeasureError("no payload for round %d under %s" % (r, payload))
            added = files_appended = 0
            for p in paths:
                src = os.path.join(rdir, os.path.basename(p))
                if not os.path.isfile(src):
                    continue
                with open(src, "rb") as f:
                    data = f.read()
                with open(p, "ab") as f:
                    f.write(data)
                added += len(data)
                files_appended += 1
            a = clocks()
            for p in paths:
                em._read_jsonl_incremental(p)
            read = span(a)
            em._LAST_ENTRY.ent = None
            gc.collect()
            counted, gens2 = cache_counted(em, paths)
            moved = [os.path.basename(p) for p in paths if gens2.get(p) != gens.get(p)]
            if moved:
                doc["problems"].append("round %d: not an append read (a new generation): %s" % (r, moved[:5]))
            if not added:
                doc["problems"].append("round %d: the append added nothing" % r)
            if counted["heldBytes"] != held + added:
                doc["problems"].append("round %d: the cache holds %d bytes, not %d + %d appended"
                                       % (r, counted["heldBytes"], held, added))
            held = counted["heldBytes"]
            appended_total += added
            rounds.append(reading(round=r, counted=counted, read=read, appendedBytes=added, filesAppended=files_appended))
        doc["rounds"] = rounds
        doc["appendedBytes"] = appended_total
        stats = em._RECORD_CACHE_STATS
        doc["cacheStats"] = {k: stats.get(k) for k in ("inserts", "evictions", "budgetEvictions", "dropped")}
        if stats.get("evictions") or stats.get("budgetEvictions") or stats.get("dropped"):
            doc["problems"].append("the cache evicted or dropped entries: %r" % doc["cacheStats"])
        # the self-check: clearing the cache must give the traced bytes back, so the delta measured was the cache's
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
            em._JSONL_CACHE_BYTES[0] = 0
        gc.collect()
        doc["cleared"] = reading()
        if trace:
            doc["tracedPeak"] = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            # 1% of the cold growth, with a 64 KiB floor: the reader's read counters by path and by caller
            # (_READ_BYTES and the byte tables _read_jsonl_entry_unlocked keeps) are not the cache and stay after it
            # clears, a few hundred bytes per file and caller (11 KB on the small world's 4 files, 1.2% of its growth)
            grew = doc["cold"]["traced"] - doc["before"]["traced"]
            left = doc["cleared"]["traced"] - doc["before"]["traced"]
            doc["clearedCheck"] = {"leftBytes": left, "coldGrowthBytes": grew, "limitBytes": max(0.01 * grew, 65536)}
            if left > max(0.01 * grew, 65536):
                doc["problems"].append("clearing the cache left %d traced bytes over the baseline, more than 1%% of the "
                                       "cold growth (%d) and 64 KiB: the traced delta is not only the cache's"
                                       % (left, grew))
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
    c.add_argument("--builds", type=int, default=11)
    c.add_argument("--instrumented", type=int, default=5)
    c.add_argument("--memo", action="store_true", help="add the memo phase (step S6.1: the real key, memoKeyed rows)")
    m = sub.add_parser("mem")
    m.add_argument("dir")
    m.add_argument("payload")
    m.add_argument("--appends", type=int, default=6)
    m.add_argument("--tracemalloc", action="store_true")
    m.add_argument("--relief", action="store_true")
    p = sub.add_parser("perf")
    p.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    try:
        if a.mode == "perf":
            return perf_main(a.out)
        if a.mode == "cpu":
            if a.builds < 1 or a.instrumented < 0:
                ap.error("--builds must be at least 1 and --instrumented at least 0")
            doc = cpu_main(a.dump, a.builds, a.instrumented, a.memo)
        else:
            doc = mem_main(a.dir, a.payload, a.appends, a.tracemalloc, a.relief)
    except MeasureError as e:
        sys.stderr.write("measure.py: %s\n" % e)
        return 2
    print(json.dumps(doc))
    return 1 if doc.get("problems") else 0


if __name__ == "__main__":
    sys.exit(main())
