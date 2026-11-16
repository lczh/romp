# The kernel on free-threaded Python: run on 3.15t with the GIL on, make shared state safe, decide the rest later

**Status:** decision record and work queue, 2026-11-16. Built so far: item 0.1 (#479) and item 0.9; item 0.2 is open as #480 and waits for 0.9. The repository owner approved two stages on 2026-11-16: stage 0, the kernel on CPython 3.15t (the free-threaded build, the one that can run without the global interpreter lock, the GIL) with the GIL forced ON; and stage 1, making the kernel's shared state safe while the GIL is still on, as ordinary reviewed pull requests. Turning the GIL off is NOT approved. It is gated on the conditions under "What is gated, and on what" below, and stages 2 to 5 are out of scope until that decision is made.

This reopens the question `plans/process-split.md` closed on 2026-09-14. The investigation behind it (2026-11-12: one investigator, seven auditors, a lab that has since been deleted, a report kept outside the repo) analysed commit `4f195d62e`. Every line number in this document is a line at `4f195d62e`, given only to date a finding; locate code by the function named. The work queue was checked against main at `75fce42d5` on 2026-11-16, and one of the report's three interpreter findings was re-run and did not hold ("Checked on 2026-11-16" below says what was and was not verified).

## The decision

- **Target 3.15t, 3.15.2 or later.** Not 3.13t and not 3.14t: both crash the whole process on a pattern the kernel's code contains today.
- **Stage 0 now:** the kernel on 3.15t with the GIL forced on, in CI and then on the two machines. This changes no thread semantics. It does change the garbage collector, the allocator, memory layout and the standard library, so it measures the collector and memory at real scale before any risk is taken with the GIL off.
- **Stage 1 now:** the shared-state fixes the audit found, under the GIL. About half of them fix races that can fire today, with the GIL on.
- **The process split's documents and decode worker stay on their own track** (`plans/process-split.md`, stages one, one b and two). They help on any interpreter, and they shrink the heap, which shrinks the free-threaded collector's pauses.
- **The GIL comes off only after** the gates below pass, on the small machine first and the large one last.
- **The process split's later stages** (the judges and the frame builders as processes) are decided after stage 2 is measured. If free threading works they are probably unnecessary.

## What a free-threaded build is, and the three builds

| Build | Standing upstream | Single-thread cost, as upstream states it | What rules it in or out |
| --- | --- | --- | --- |
| 3.13t (3.13.14t) | Experimental (PEP 703 phase I) | "about 40% on the pyperformance suite", the specializing interpreter being off | Ruled out. cffi refuses to build on it, so `cryptography` and the SDK venv cannot be installed; pydantic-core, cryptography and rpds-py have no wheel; the 3.13 guide calls `sys._current_frames()` "generally not safe" there; the OrderedDict fix (gh-125996) was not backported |
| 3.14t (3.14.6t in the lab; 3.14.10t the latest release, 2026-11) | Officially supported but optional (PEP 779, accepted 2025-06-16); not the default | "roughly 5-10%" (What's New), "about 1% on macOS aarch64 to 8% on x86-64 Linux" (the guide) | Ruled out by the crashes below, all three still present in 3.14.10t |
| 3.15t (3.15.2t) | Released 2026-10-09; adds `abi3t`, a stable ABI for free-threaded builds (PEP 803), and `threading.serialize_iterator` | Not stated upstream; the lab put it level with 3.14t | The target. Fixes two of the three crashes |

What a free-threaded build changes besides the GIL, with the GIL on or off: objects come from mimalloc instead of pymalloc, which reserves address space 1 GiB at a time (an idle 3.15.2t holds 1,060 MB of it against 40 to 45 MB on 3.15.2, with 11 to 24 MB resident on either); object headers are larger (`None` is 32 bytes against 16); frees are deferred; the collector is a different, non-generational one; threads inherit the caller's context by default.

### The three interpreter crashes

Each of these takes the whole process down with a segmentation fault. Counts are runs that died out of runs made.

| Pattern | 3.13.14t | 3.14.6t | 3.14.10t | 3.15.2t | Any build, GIL on |
| --- | --- | --- | --- | --- | --- |
| `json.dumps(d)` while another thread mutates `d` (its keys, its values, a nested dict or list) | 4/4 | 4/4 | 4/4 | 0/4 | 0 |
| An OrderedDict iterated (`list(o)`, `list(o.items())`) while another thread inserts and deletes | 5 to 8 of 10 | 2 to 5 of 6 | 2 to 3 of 6 | the report: 0/4. **Re-run 2026-11-16: 3 to 4 of 6 at ten keys** | 0 |
| Two threads calling `next()` on one generator | not run | 4/4 | 4/4 | 0/4 (raises "generator already executing") | 0 |

**The OrderedDict crash is not fixed in 3.15.2t.** The report said 3.15.2t fixed all three. Re-running the reproductions on 2026-11-16 (3.14.6t from uv, 3.15.2t from the python-build-standalone `20261110` release, each run isolated with `-I`, on a loaded 4-core development VM) confirmed the other two and contradicted this one: with an OrderedDict of ten keys, 3 to 4 runs of 6 die on 3.15.2t, as they do on 3.14.6t in the same session (1 to 4 of 6); the fault is in a reader thread, in `PyObject_Hash` under the list call. At 100 keys 2 of 6 died on each build. At 1,000 keys 1 run of 6 died on 3.15.2t and none on 3.14.6t, which is probably why a larger probe read as fixed. With the GIL on it never dies on either build. A mutation-only OrderedDict used as an LRU (get, `move_to_end`, pop, set, `popitem`, never iterated) did not crash in 12 runs on 3.15.2t; it raised `KeyError`, as the report said.

What follows from that:

- 3.15t is still the target. `json.dumps` over an object another thread is mutating is the pattern the kernel has in unlocked form (the four published-object sites in the audit), and that one is fixed. The kernel has no shared generators.
- Stage 1's OrderedDict step is a requirement for turning the GIL off on any build that exists today, not hygiene. Every OrderedDict must either become a plain dict behind a lock, or be provably never iterated outside its lock, with the lint holding that line.
- The gate "no open free-threading crash affecting the patterns romp uses" is not met by 3.15.2 for this pattern.
- The reproduction is worth an upstream report. That is the owner's call; nothing has been filed.

Upstream is still fixing free-threading crashes in every patch release: 7 to 8 per release in 3.14.8, 3.14.9 and 3.14.10.

### The reproductions

The lab's scripts were deleted with it; these are the ones run on 2026-11-16. Run each with `-I` (a `python -c` run from a shared temporary directory once imported a stray `bisect.py` lying there) and with core dumps off.

```python
# 1. json.dumps of a dict while another thread replaces its values.
#    Segfaults 3.13.14t, 3.14.6t, 3.14.10t; survives 3.15.2t and any build with the GIL on.
import json, threading, time
d = {i: {"k": i} for i in range(1000)}; stop = threading.Event()
def mut():
    i = 0
    while not stop.is_set(): d[i % 1000] = {"k": i}; i += 1     # value replacement alone is enough
def rd():
    while not stop.is_set(): json.dumps(d)
ts = [threading.Thread(target=mut)] + [threading.Thread(target=rd) for _ in range(2)]
[t.start() for t in ts]; time.sleep(1); stop.set(); [t.join() for t in ts]
```

```python
# 2. An OrderedDict listed by two threads while a third deletes and re-inserts its keys.
#    Segfaults 3.14.6t AND 3.15.2t (N = 10: usually 3 to 4 runs of 6); survives with the GIL on.
import collections, threading, time
N = 10
o = collections.OrderedDict((i, i) for i in range(N)); stop = threading.Event()
def mut():
    i = 0
    while not stop.is_set():
        k = i % N
        if k in o: del o[k]
        else: o[k] = i
        i += 1
def rd(items):
    while not stop.is_set():
        try: list(o.items()) if items else list(o)
        except (RuntimeError, KeyError): pass
ts = [threading.Thread(target=mut), threading.Thread(target=rd, args=(False,)), threading.Thread(target=rd, args=(True,))]
[t.start() for t in ts]; time.sleep(3); stop.set(); [t.join() for t in ts]
```

```python
# 3. Two threads calling next() on one generator.
#    Segfaults 3.14.6t and 3.14.10t; on 3.15.2t each loser gets ValueError("generator already executing").
import threading, time
def gen():
    i = 0
    while True: yield i; i += 1
g = gen(); stop = threading.Event()
def rd():
    while not stop.is_set():
        try: next(g)
        except ValueError: pass
ts = [threading.Thread(target=rd) for _ in range(2)]
[t.start() for t in ts]; time.sleep(1); stop.set(); [t.join() for t in ts]
```

A fourth, which is not a crash of this kind and not about free threading (see "What the test suite showed"):

```python
# 4. The recursion limit against a function that calls itself through *args or **kwargs.
#    3.10.20, 3.11.15, 3.12.3, 3.13.14: RecursionError three times (3.14.10 was run for plain and star: both raise).
#    3.15.2 and 3.15.2t: plain raises RecursionError; star and kw reach 5000 with the limit at 1000.
import sys
def plain(n, a): return n if n >= 5000 else plain(n + 1, a)
def star(n, *a): return n if n >= 5000 else star(n + 1, *a)
def kw(n, **k): return n if n >= 5000 else kw(n + 1, **k)
for f, a, k in ((plain, (0, 1), {}), (star, (0, 1, 2), {}), (kw, (0,), {"a": 1})):
    try: print(sys.version.split()[0], f.__name__, "reached", f(*a, **k))
    except RecursionError: print(sys.version.split()[0], f.__name__, "RecursionError")
```

## Runtime facts the stages rest on

**Turning the GIL on and off.** On a free-threaded build `PYTHON_GIL=1` or `-X gil=1` forces the GIL on. Importing a C extension that lacks the `Py_mod_gil` slot turns it on by itself, with a `RuntimeWarning`. `sys._is_gil_enabled()` reports the state in force.

- **`PYTHON_GIL=0` hides unsafe extensions.** The lab built an extension with no slot: with the variable unset, importing it turned the GIL on and warned; with `PYTHON_GIL=0` the GIL stayed off and nothing was printed. So a deployment that wants the GIL off leaves the variable unset and asserts `sys._is_gil_enabled() is False` after boot. `PYTHON_GIL=0` is never the switch.
- **`-I` and `-E` silently ignore `PYTHON_GIL`.** `bin/romp-serve` ends in `exec "$PY" "$KERNEL"` with neither flag, so the variable reaches the kernel, and no spawn under `kernel/` or `bin/` passes either flag (checked on main).
- **With the GIL on, a free-threaded build kept a default build's THREAD semantics in every probe:** no lost updates, C-level iteration atomic, none of the three crashes. That is why stage 0 preserves thread semantics, and thread semantics are all it preserves. With the GIL on the build is still not a default build in its address space, in whose books its memory is counted, in when freed memory comes back, or in what starts a collection: "What the test suite showed" lists what the first run of the whole suite found (2026-11-16), one of them a kernel incompatibility (item 0.9).

**The kernel's children.** Four are started as `[sys.executable, ...]` and inherit the kernel's environment: session hosts (`SdkBackend._spawn_host`; re-exec'd through `SdkBackend._host_reexec_on_skew`), the judge child (`_judge_child_argv`), the postal service (`_bus_converge`, `_ensure_postal_bus`), and, since the audit, the document worker (`_doc_worker_argv`), whose environment also carries `MIMALLOC_ARENA_RESERVE=0` (`_doc_worker_env`, item 0.9).

**Installing the interpreter.** `uv python install --no-bin 3.14t` takes seconds. `--no-bin` matters: without it uv drops shims into `~/.local/bin`, which `romp-serve`'s newest-first fallback searches. Pin uv's minor-version path (`cpython-3.15+freethreaded-linux-x86_64-gnu/bin/python3.15t`) so a patch upgrade does not break the pin. The uv on the development VM (0.12.1) knows no 3.15 release, only a beta; 3.15.2t came from the python-build-standalone `20261110` release.

**Dependencies.** The kernel and its SDK venv import claude-agent-sdk with anyio, mcp, pydantic, pydantic-core, rpds-py, jsonschema, httpx2, starlette, uvicorn, opentelemetry, pyjwt and truststore, plus cryptography and cffi.

| Package | 3.14t wheel | 3.15t wheel | Declared free-threading support |
| --- | --- | --- | --- |
| cffi 2.1.1 | cp314-cp314t | cp315-cp315t | 2, Beta (3.14t and later only) |
| cryptography 50.0.2 | cp314-cp314t | cp315-abi3 (works) | 3, Stable |
| pydantic-core 2.50.4 | cp314-cp314t | cp315-cp315t | no classifier |
| rpds-py 2026.9.1 | cp314-cp314t | cp315-cp315t | no classifier |
| everything else | pure Python | pure Python | anyio: 2, Beta |

Nothing compiled on 3.14t, and 3.15t installed with `--only-binary :all:`. The venv in use today carries an abi3 cryptography wheel that cannot load on a `t` interpreter, so the venv is rebuilt for the new tag, which `bin/romp-sdk-setup` already does. An import probe loaded 105 modules (every stdlib module the kernel uses, C accelerators included, and every third-party module above), each in a fresh isolated process with `PYTHON_GIL` unset: all 105 left the GIL off on 3.14t and 3.15t, and the no-slot control extension proved the probe can see a module that turns it on.

**What stays atomic without the GIL, measured.** One mutator thread and two reader threads per idiom:

- **Still atomic snapshots** (0 errors on 3.14t and 3.15t): `list(d)`, `list(d.items())`, `list(d.values())`, `sorted(d.items())`, `d.copy()`, `dict(d)`, `list(s)`, `frozenset(s)`, `set(d)`, `s1 | s2`, `list(l)`, `pickle.dumps(d)`, `repr(d)`, `marshal.dumps(d)`; a shared `itertools.count` (no duplicates); concurrent `heapq` push and pop; `list(WeakSet)`.
- **Atomic only by accident of the GIL** (0 errors with it, about 10,000 RuntimeErrors a second without): `tuple(d)`, `sum(d.values())`, `max(d)`, `min(d)`, `any(...)` over a view, `",".join(map(str, d))`, `dict(d.items())`, `list(deque)`.
- **Raise with the GIL too, about 1,000 times more often without it:** Python loops and comprehensions over a shared dict, set or deque, and `copy.deepcopy(d)`.
- **Lost updates:** `cell[0] += 1` and `global n; n += 1` lost 30 to 66 percent of updates across four threads without the GIL, and none with it.
- **Walking other threads' frames** (`sys._current_frames()`) did not crash in 30 to 60 seconds of stress on any build tried, but the 3.13 guide calls it unsafe.

## What was measured

**The machines.** Everything was measured on a 4-core development VM: 4 virtual CPUs on 2 physical cores, shared with live kernels and other sessions, at a load of 3 to 8 throughout. Single stages varied up to 2 to 3 times in wall and CPU time on every build. The figures are medians of repetitions with the builds interleaved, and only differences of 2 times or more are claimed. Nothing was measured on the other machine the kernel runs on, a large shared host with 112 physical cores; what it should see is extrapolation and is marked so.

**The harness** (deleted with the lab; this is enough to rebuild it). The repo's own synthetic board and loader: `tests/bench_connect_push.py` (`write_world`, the `notes-api` world) and `tools/perf-bench.py`. Every run used a temp state root with `off` in `session-hosts` (asserted), `ROMP_CLAUDE_BIN=/bin/false`, zero spawns and zero refused writes, `nice -n 10`, at most four measured threads, and the code from a `git archive` export with bytecode cached outside the repo. Transcripts were synthetic, on the shape of `tests/bench_chatbuild.py`'s `Gen`: tool results of 0.2 to 24 KB duplicated into `toolUseResult`, about 215 records per MB, at 6.6 MB, 100 MB, 300 MB and 900 MB; a small-record shape, about 1,737 records per MB, came from `Gen` unchanged. Builds: 3.12 (the development VM's kernel today), 3.13, 3.14, 3.14t with `-X gil=1`, 3.14t with `-X gil=0`, 3.13t, 3.15t.

**The headline.** Four 100 MB transcripts parsed at once went from 28 MB/s on today's 3.12 to 86 MB/s on 3.14t with the GIL off, 3.1 times. A warm page connect while two 300 MB parses ran took 0.09 s without the GIL against 0.23 s on 3.12 (0.05 to 0.08 s unloaded). Single-threaded, the free-threaded build was within 10 percent of 3.14 with the GIL and about 20 percent faster than 3.12. Resident memory was 17 to 21 percent higher.

### Parse scaling: N threads, each parsing its own 100 MB transcript with `em.parse_session`

| Build | 1 thread, median s (n=8) | 2 threads, s | 4 threads, s | 4-thread throughput | Against its own 1 thread | CPU per parse at 4 | Resident at 4 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 3.12 | 2.74 (min 2.27) | 6.59 | 14.37 | 28 MB/s | 0.76x | 3.60 s | 813 MB |
| 3.14 | 2.16 (min 1.69) | 4.77 | 10.82 | 37 MB/s | 0.80x | 2.58 s | 820 MB |
| 3.14t, GIL on | 2.35 (min 1.74) | 5.60 | 9.48 | 42 MB/s | 0.99x | 2.38 s | 962 MB |
| **3.14t, GIL off** | **2.17** (min 1.71) | **3.26** | **4.63** | **86 MB/s** | **1.87x** | 2.68 s | 962 MB |

A later interleaved run (load 5 to 6, three repetitions):

| Build | 1 thread | 4 threads | Throughput |
| --- | --- | --- | --- |
| 3.15t, GIL off | 2.72 s | 5.76 s | 69 MB/s (1.89x its own) |
| 3.14t, GIL off | 2.71 s | 5.57 s | 72 MB/s |
| 3.14 | 2.82 s | 13.36 s | 30 MB/s |
| 3.15t, GIL on | 2.34 s | 10.89 s | 37 MB/s |

Under the GIL, threads make total throughput worse, 0.76 to 0.84 of serial. That is the convoy behind the waits after a restart. A 1.9 times gain over one thread on two physical cores is close to what that hardware allows.

### One large parse on one thread

| Build | 300 MB | 900 MB | Resident at 900 MB |
| --- | --- | --- | --- |
| 3.12 | 7.85 s | 24.99 s | 1,761 MB |
| 3.14 | 8.09 s | 22.06 s | 1,763 MB |
| 3.14t, GIL off | 6.72 s | 20.05 s | 2,132 MB (+21%) |
| 3.14t, GIL on | 5.51 s | 18.84 s | 2,133 MB |

No single-thread penalty shows above the noise, for two reasons: 3.14's interpreter is faster than 3.12's, and the free-threaded collector runs far less often during an allocation-heavy parse (12 collections against 794 in the small-record shape, 0.6 to 1.1 s of collecting against 2.5 to 7 s).

### A page connect while a big parse runs

`Handler._push_one` on a lane thread, the way a page connects, over a board of 8 sessions of 2,000 records, three repetitions. The background load is two threads parsing 300 MB transcripts.

| Build | Background | Cold connect, median s | Warm connect, median s | needFull, median s | Background parses finish |
| --- | --- | --- | --- | --- | --- |
| 3.12 | none | 2.61 | 0.065 | 0.011 | |
| 3.12 | 2 parses | 4.27 (1.6x) | 0.228 (3.5x) | 0.076 (7x) | 21.8 s |
| 3.14 | none | 1.89 | 0.049 | 0.009 | |
| 3.14 | 2 parses | 5.61 (3.0x) | 0.362 (7.4x) | 0.059 (6.6x) | 18.8 s |
| 3.14t, GIL on | none | 2.20 | 0.054 | 0.012 | |
| 3.14t, GIL on | 2 parses | 5.59 (2.5x) | 0.383 (7.1x) | 0.147 (12x) | 16.9 s |
| **3.14t, GIL off** | none | 2.97 | 0.075 | 0.015 | |
| **3.14t, GIL off** | 2 parses | 5.04 (1.7x) | **0.091 (1.2x)** | **0.023 (1.5x)** | **9.1 s** |
| 3.15t, GIL off (2 reps) | none | 2.94 to 4.42 | 0.068 to 0.075 | 0.013 to 0.016 | |
| 3.15t, GIL off (2 reps) | 2 parses | 4.18 to 9.22 | 0.082 to 0.085 | 0.016 to 0.017 | 10.4 to 14.2 s |

Short requests stay near their unloaded latency without the GIL and slow 4 to 12 times with it. The cold connect gains less here because three CPU-bound threads plus other users' load saturate two physical cores. In production, page loads have waited 100 to 500 s behind background parses on one interpreter.

### The 2026-09-14 result, re-examined

`plans/process-split.md` records a single hermetic run that closed this option: on 3.13t, parallel parses were 4 to 6 times slower than serial and the final serial stage did not recover. The lab re-ran that shape (twelve 6.6 MB transcripts per stage; stages of 1, 2, 4 and 1 threads in one process, the record cache carrying over) on today's event model and on that day's (`5f836e8e1`), on 3.13t and 3.14t, with two transcript shapes, with and without CPU quotas. Each cell is that stage's wall time in seconds.

| Run | Stage 1 (1 thread) | Stage 2 (2 threads) | Stage 3 (4 threads) | Stage 4 (1 thread) |
| --- | --- | --- | --- | --- |
| 3.12, today's model, large records | 1.63 | 2.12 | 2.77 | 1.94 |
| 3.13 | 1.75 | 1.84 | 2.22 | 1.25 |
| **3.13t** | 1.65 | **1.03** | **0.94** | 1.56 |
| 3.14 | 1.20 | 1.37 | 1.55 | 1.10 |
| **3.14t, GIL off** | 1.32 | **0.85** | **0.78** | 1.17 |
| 3.15t, GIL off (2 reps) | 1.81 to 2.33 | 1.20 to 1.44 | 1.14 to 1.36 | 2.38 to 2.44 |
| Small records, 3.13t | 8.82 | 5.70 | 5.18 | 8.76 |
| 2026-09-14 model, large records, 3.13t | 1.60 | 1.37 | 1.42 | 1.61 |
| 2026-09-14 model, small records, 3.13t | 20.96 (a load spike) | 7.02 | 7.37 | 11.22 |
| 1/3/6/1 threads under `CPUQuota=100%`, 3.13t (4 runs) | about 2.6 to 2.8 | about 2.8 to 2.9 | about 2.9 to 3.0 | **11.0 once**, 2.6 to 2.8 three times |
| 1/3/6/1 threads under `CPUQuota=100%`, 3.14t (4 runs) | about 2.0 to 2.4 | about 2.1 to 2.5 | about 2.2 to 2.5 | 2.0 to 2.4 |

- **It did not reproduce.** Parallel stages without the GIL were faster in every configuration, on both event models, and no run showed a systematic final-stage degradation. Repeated diagnostics (stages of 1/4/1/1 and 1/1/1/1 with rusage) put the last stage's user time over the first's at 0.90 to 1.07 on 3.14t with the GIL off, 0.90 to 1.31 with it on, and 0.97 to 1.47 on 3.14, with no major page fault and no difference between sequences with and without a parallel stage.
- **The likeliest explanation of the original.** Individual stages blew up 3 to 4 times in CPU time sporadically on this loaded, hyperthreaded machine: on 3.13t in about 6 percent of stages, mostly under quotas, and once on 3.14t with the GIL on. The 2026-09-14 figure was one run under a resource cap.
- **What is still owed.** The original harness lives in reports outside the repo and could not be run exactly. Before stage 2, re-run it if it can be found.
- **The rule that comes out of it:** never run a free-threaded kernel under a tight CPU quota.

### Garbage collection

A synthetic heap of transcript-shaped records, five containers each; a "collection" is a full `gc.collect()`.

| Records | Tracked containers | Heap | 3.12 | 3.14 | 3.13t | **3.14t** | 3.15t | 3.14t, heap frozen | 3.14t `freeze()` itself |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0.5 M | 1.5 to 2.5 M | 465 MB | 0.55 to 0.64 s | 0.60 to 0.76 s | 1.0 to 1.6 s | **0.16 to 0.18 s** | | 0.072 s | 0.022 s |
| 1 M | 3 to 5 M | 0.7 to 0.96 GB | 1.23 s | 1.31 to 1.62 s | 0.46 to 0.51 s | **0.37 s** | 0.37 s | 0.150 s | 0.050 s |
| 2 M | 6 to 10 M | 1.9 GB | 2.17 to 2.61 s | 2.49 to 3.12 s | 0.94 to 1.11 s | **0.66 to 0.75 s** | 0.78 s | 0.286 s | 0.107 s |

- **Default builds** pay about 0.27 to 0.31 s per million tracked containers for a full collection, and nothing for a frozen heap. That zero is what `kernel/gc_freeze.py` relies on (`plans/gc-full-collection-pause.md`).
- **Free-threaded builds** pay about 0.07 to 0.08 s per million, three to four times less. But a frozen heap still costs about 0.03 s per million, roughly 40 percent of the unfrozen walk, and `gc.freeze()` is itself a stop-the-world walk of about 0.01 to 0.015 s per million.
- **From the 3.14 source** (`gc_free_threading.c`): every automatic collection is a full stop-the-world walk. One starts only when net new containers exceed 2,000 and a quarter of the long-lived total, and resident memory has grown more than 10 percent since the last one (or 80,000 allocations are deferred). Frozen objects are still visited in the heap walk and skipped when references are updated.
- **For the kernel:** automatic collections become rare in steady state, but each one stops every thread for 0.03 to 0.08 s per million live containers. At the 5.6 million loaded objects `plans/gc-full-collection-pause.md` measured, that is 0.16 to 0.45 s. At 50 million it is 1.5 to 4 s.
- **Every `gc_freeze` load fold-in** (`gc.collect(); gc.freeze()` on busy cycles) becomes two full-heap walks on a free-threaded build. That policy has to be off or redesigned there. `apply_gen2_threshold` already declines on a free-threaded build; the controller itself (`GcFreeze`) has no such gate.
- **Stage 0 on the large host measures this at real scale**, with the GIL on and the same collector, before any risk is taken with the GIL off.

### Memory

The parse workload holds 17 to 21 percent more resident memory on a free-threaded build: 298 MB against 248 to 251 for one 100 MB parse, 962 against 813 to 820 for four, 2,132 against 1,761 at 900 MB. The raw object heap in the collector probe was only 3 percent larger, so the excess is mimalloc page retention, deferred frees and larger headers.

The kernel's `_cap_malloc_arenas` (glibc's `M_ARENA_MAX`) no longer governs Python object memory under mimalloc. The 12 GB arena-retention problem it fixed has to be measured again; `MIMALLOC_PURGE_DELAY` is the new knob. On the large host an 18 GB kernel would become roughly 21 to 22 GB, and each of about 230 session hosts (20 to 36 MB each) would grow 15 to 20 percent if they ran free-threaded, which is one reason they keep the GIL.

### What the large host should see (extrapolation, not measurement)

It has two sockets of 56 cores, two hardware threads a core, and four NUMA nodes.

- **Demand.** The 2026-09-14 review measured 1,430 s of interpreter demand in a 617 s window, about 2.3 cores' worth. Free threading removes the queue, not the work: a page build should run near its own CPU time while parses and judges run beside it.
- **Parse throughput** should scale roughly linearly to 4 to 8 threads. Past that it is capped by the event model's global locks ("What would serialize the parallel work anyway"), reference-count traffic on shared objects (the postal log every parse reads), memory bandwidth and NUMA.
- **Pin the kernel to one NUMA node** (systemd `CPUAffinity` and `NUMAPolicy`), with no CPU quota.
- **Measure before stage 2:** the lab bench there, with synthetic data and a temp root, at 1, 2, 4, 8 and 16 threads.

### What the test suite showed

Twenty unit modules and six modules that boot a real kernel process, serial and nice'd, from a clean export against the synthetic fixtures:

| Build | Unit tests | Real-kernel tests | Failures |
| --- | --- | --- | --- |
| 3.14t, GIL off | 702 passed, 1 failed | 22 passed | the GC-trigger test |
| 3.15t, GIL off | 704 passed, 2 failed | 22 passed | the same, plus the header-count test |
| 3.14t, GIL on | | | the GC-trigger test fails the same way |
| 3.12, 3.14 | | | both pass |

Neither failure is a threading failure. `LockFree.test_a_collection_triggered_under_the_stats_lock_finishes` (`tests/test_perf_gc_block.py`) assumes that allocating twice the young threshold starts a collection, which the free-threaded collector's trigger does not. `RelayExecuted.test_no_more_lines_reach_the_remote_than_the_browser_sent` (`tests/test_kernel_ws_deflate.py`) pins `http.client`'s 100-header limit, which 3.15 changed. These suites are mostly single-threaded, so passing them proves compatibility, not thread safety. The lab never ran the full suite on 3.14, 3.15 or either free-threaded build.

**The whole suite, first run on 2026-11-16** (the CI cells of #480: 3.14, 3.15, and 3.15t with the GIL forced on). 3.14 had 2 failures; 3.15 had 5 before its runner died with 1,596 tests unrun; 3.15t had 16 and the same dead runner. Nine causes. Eight were tests that had written down a fact about one interpreter (#479), four of them facts about 3.14 and 3.15 and not about free threading (the header count, the JSON decoder's depth, the listen backlog, the recursion limit below). The ninth was kernel code: the document worker's memory bound (item 0.9).

**Where a free-threaded build with the GIL ON is not a default build of the same version.** Each of these showed in the 3.15t cell, whose GIL is on; the figures are 3.15.2t's:

- **Address space.** The allocator takes address space from the OS 1 GiB at a time. An idle interpreter holds 1,060 MB of it against 40 to 45 MB, and a growing heap's address space stands still and then jumps a gigabyte. A limit on address space therefore bounds nothing there: `RLIMIT_AS` at 200 MB over the idle size stopped a heap at 1,173 MB resident, against 199 MB on a default build, and `RLIMIT_DATA` counts the same mappings and stopped it at the same place. A limit set below the idle size refuses the process outright, which is what the document worker did. `MIMALLOC_ARENA_RESERVE=0` in the process's environment turns the reservation off (CPython's bundled allocator, mimalloc 2.5.2 in 3.15.2, reads its `MIMALLOC_*` variables): the idle size falls to 100 MB, address space then follows use 32 MiB at a time, and the same limit stopped the heap at 213 MB. A default build run with `PYTHONMALLOC=mimalloc` reserves the same gigabyte.
- **Allocator accounting.** Objects are mimalloc's, so glibc's books describe none of them: a 32 MiB `bytes` object is not a glibc allocation there. `/perf`'s `process.malloc` block and `_cap_malloc_arenas` see only what C libraries allocate ("Found in passing" below).
- **Deferred frees.** One `gc.collect()` does not settle the heap: the collector keeps the bookkeeping of what it freed until a later collection. After one collection of 60,000 cycles tracemalloc still traced 967,472 bytes on 3.15t and 4,952 after the next, against 800 both times on 3.15. A measurement that takes its baseline after one collection reads the difference as a leak.
- **What starts a collection.** Allocating past the young threshold starts none by itself; the trigger also weighs the live heap and the resident size ("Garbage collection" above).
- **Memory per job.** The document worker's parse and write of a 28.9 MB synthetic transcript of 708-byte records grew by 417 MB, against 322 to 325 MB on 3.15.2 and 3.12.3 (29 percent more), and by 166 MB against 136 MB at 3.9 KB records (22 percent). The worker's estimate was measured on default builds (item 0.10).

**A known bug in CPython 3.15.2, on default and free-threaded builds alike: the recursion limit does not stop a function that calls itself through `*args` or `**kwargs`** (reproduction 4; 3.10 to 3.14 raise `RecursionError`). It is upstream's bug and nothing about free threading, but it is in the target interpreter. In the suite one such test handler recursed until both 3.15 cells' runners ran out of memory; under an address-space limit it ends in a segmentation fault. No kernel code is known to recurse this way, but a runaway recursion of that shape is no longer stopped at 1,000 frames. Nothing has been filed upstream from here; that is the owner's call.

## The thread-safety audit

### Method and scale

1. **An AST inventory of every kernel module:** module-level and class-level mutable containers; each mutation, iteration and read site, and whether it sits lexically inside a `with <lock>`; `global` rebinds and `+=` on globals and cells; instance attributes that hold containers. The tool was deleted with the lab; the lint in the work queue rebuilds it.
2. **Seven auditors read the code**, one per slice (kernel.py in four, event_model with the small modules, judge, sdk_backend with the hosts and Codex). They judged every site against the measured semantics above and checked callers wherever a site looked unguarded.

| Module | Lines | Module-level containers | Mutated in functions | Mutation sites (not inside a lock) | Iteration sites (not inside a lock) | Locks |
| --- | --- | --- | --- | --- | --- | --- |
| kernel.py | 92,524 | 551 | 477 | 1,266 (800) | 394 (201) | 94 Lock, 12 RLock, 1 Condition, 17 Event |
| judge.py | 21,937 | 103 | 99 | 223 (129) | 50 (16) | 30, plus 11 thread pools |
| event_model.py | 11,447 | 74 | 74 | 294 (70) | 72 (15) | 9, 1 RLock, 1 Condition, 64 read stripes |
| sdk_backend.py | 24,629 | 33 | 16 | 24 (23) | 10 (10) | 19, 3 RLock, plus per-session locks |
| others | about 9,100 | 25 | 11 | 22 (12) | 6 | about 15 |
| **Total** | **about 160,000** | **about 786** | **about 677** | **about 1,830 (about 1,030)** | **about 530 (about 250)** | **about 185** |

Also counted: about 137 instance attributes holding containers, 52 globals rebound through `global`, 14 OrderedDicts, and 63 thread spawn sites in kernel.py besides one thread per HTTP request. "Not inside a lock" is lexical: many such sites sit in `*_locked` helpers whose callers hold the lock, or are confined to one thread.

What that reduces to: about 1,000 benign sites, about 30 mechanisms that raise an exception, about 45 that corrupt state, and four places that mutate an object after publishing it. About half of the non-benign findings can already fire today under the GIL. The report's estimate was about 8 developer-days for the known sites, one to two weeks for the lint, stress and CI tooling, and one to three weeks to split the locks that would otherwise serialize the gain.

### Which threads run concurrently

- **HTTP:** `ThreadingHTTPServer`, one thread per request (`Handler`).
- **WebSocket:** per client a `ws-send` thread and `ws-lane` threads; `Handler._dispatch_ws` also spawns threads for revive, restart and apply.
- **Long-lived threads** started in `main()`: producer, pusher, timeline-build, doc-writer, jobs, asm-rebuild-start, heartbeat, `_ask_poll`, the update check, the postal bus, the tunnel supervisor, and since the audit the tail-first shadow.
- **Ad-hoc threads:** assembly refresh and rebuild, parse and boot warmers, comment-create, session move, push-pull peers, the model catalog, the repo index.
- **judge.py:** the `index`, `triage` and `serve-pass` tier threads, plus 11 `ThreadPoolExecutor` pools.
- **sdk_backend.py:** one `sdk:<name>` thread per session, each running its own asyncio loop, plus lease, deferral, end-host and canary threads. Codex has worker and pump threads.
- **Session hosts** are separate processes that talk over a Unix socket and files.

### The conventions the code already follows

The code is mostly written for threads already. A fix in stage 1 should land as one of these, not as a new idiom:

- Copy under the lock, work outside it.
- Publish by rebinding a whole immutable object, and treat a published build as immutable (copy before changing).
- One writer per piece of state: jobs-only, doc-writer-only, supervisor-only and pusher-only state, and try-acquire single-flight (`_AUTO_NUDGE_TICK_LOCK`).
- Thread-locals for per-thread scratch.
- A `*_locked` suffix meaning "the caller holds the lock".
- A lost race costs a refold, never a raise.
- Take the stat key before the read.
- Talk to a session's loop only through `call_soon_threadsafe` or `run_coroutine_threadsafe` (22 sites, all correct in direction).

Five comments state that a line is safe because of the GIL, and each is a place to re-read: `_held_mail_rearm`, `_heap_stats` and the comment above `_pending_ops_lock` in kernel.py; `SdkBackend._persist_echoes` and `SdkBackend.listing_rev` in sdk_backend.py. Of the idioms they describe, `tuple()` over a live set (`_held_mail_rearm`) is the one that raises without the GIL; `list(d.items())` and `dict.get` stay atomic.

### The lock order

Documented or observed. No auditor found an inversion.

- **Settings and views:** `_SETTINGS_LOCK` before `_catalog_lock`; `_order_lock` before `_views_lock` before `_views_file_lock`.
- **Notifications:** `_notify_prev_lock` before `_ncards_lock`; `_FEED_BUILD_LOCK` before `_notify_prev_lock` before `_push_ring_cond`.
- **Nudges:** the records lock before `_NUDGE_LOCK` before `_FILES_STAT_LOCK`.
- **Comments:** `_dropped_creates_file_lock` before `_create_lock`.
- **Sends and watches:** `_SEND_WRITE_LOCK` before `_SEND_ACCEPTS_LOCK`; `_pr_watch_lock` before `_SYNC_LOCK`.
- **Timeline:** `_TL_BUILD_LOCK` before `_TL_COLD_LANES_LOCK`.
- **Clients:** per client, `dlock` before `qlock`; `_clients_lock` is always released before any per-client lock is taken.
- **Judge child:** `_JudgeChild.exit_lock` is never taken behind `.lock`.
- **event_model:** a read stripe before `_JSONL_CACHE_LOCK`, `_CKPT_LOCK` and `_ASM_CKPT_LOCK`; a per-key lock before a stripe, `_ASM_LOCK` and `_MAT_LOCK`; `_ASM_BG_COND` before `_ASM_LOCK`; `_ASM_LOCK` and `_MAT_LOCK` never nested, nor `_JSONL_CACHE_LOCK` and `_CKPT_LOCK`.
- **judge:** leaf locks, plus `_GOAL_ARCH_LOCK` before `_FILE_MEMO_LOCK`.
- **sdk_backend:** `SdkBackend._lock` before `_reg_lock`. Codex: `norm_lock` before `s.lock`.

### Findings by subsystem

| Scope (lines at `4f195d62e`) | Raises an exception | Corrupts state | Crashes the process on 3.14t | Benign | Effort |
| --- | --- | --- | --- | --- | --- |
| kernel.py 1 to 23k | 9 sites, 5 mechanisms, plus 2 | about 23, of which 2 are safe only under the GIL (`_models_rev`, the `_UPDATE_STATE` latch) | the two frame walks | about 410 single operations, 15 counters, 20 log-once flags | 1.5 to 2 days |
| kernel.py 23 to 46k | 3, plus 2 reachable only at cache caps | 8 | | about 200 single operations, 35 counters | about 1 day |
| kernel.py 46 to 69k | 3, plus 1 to 2 | 4, plus 5 counters that matter | `_SUMMARY_ANCHOR_MEMO` is an unlocked OrderedDict, never iterated, so it raises | about 110 single operations, 25 counters | about 1 day |
| kernel.py 69 to 92.5k | 4 | 5 | | about 60 single operations, 25 counters | about half a day |
| event_model and the small modules | 2, rare | 1 (`loadsource`) | hydrate mutates shared atoms in place | about 35 single operations, 14 check-then-act | about 1 day, plus half a day of lock splitting |
| judge.py | 1, plus 1 already handled | 2, plus 1 minor | `_ParseStore`, latent (the bare-id paths) | about 45 single operations, 20 counters, 12 log-once flags | about 1 day |
| sdk_backend, hosts, Codex | 3 plus 2, plus the closed-loop class (18 call sites) | 2 counters, the live-atom flags, about 40 last-writer-wins writes | live atoms mutated in place (4 sites) | about 40 single operations | 1 to 1.5 days |

### Findings by category

- **A. Single operations on a shared container:** about 900 sites, benign.
- **B. Read-modify-write counters:** about 150 sites, of which about 10 matter because a consumer compares them (listed under item 1.9).
- **C. Check-then-act and lazy initialisation:** about 190 sites, of which about 25 matter (items 1.6 and 1.8).
- **D. Iterating a shared container while another thread mutates it:** about 25 sites that raise (the list below).
- **E. State spread across two containers or stores:** about 20, of which about 12 matter (item 1.7).
- **F. OrderedDict:** 14 instances at the audit, 13 fully locked, and `_SUMMARY_ANCHOR_MEMO` unlocked but never iterated.
- **G. asyncio across threads:** clean by design, with one class that can already fire today: a session's `loop` attribute is never cleared after `asyncio.run` returns, so a kernel-side `call_soon_threadsafe` can raise into an HTTP or WebSocket handler.
- **H. Shared objects mutated after they are published:** live atoms, hydrate, `_txKey`, the spend-tree memo. There are no shared generators.
- **I. Other reliance on the GIL:** the frame walks; `judge.yield_between_sessions` becomes a no-op; the double reads of `_built_feed[1]`.

### Sites that crash the process (the queue for items 1.1, 1.2 and 1.3)

A segmentation fault on 3.13t and 3.14t. On 3.15t the `json.dumps` sites produce a torn read instead; the OrderedDict site still crashes.

- **`json.dumps` over an object another thread mutates:**
  - `event_model.py`, `_hydrate_one` (lines 10537 to 10561): writes `message`, `toolUseResult` and `skillMd` and pops `lazy` on atom dicts that every served tree shares.
  - `sdk_backend.py`, `SdkBackend._mark_dropped_echoes` (20514, 20569 to 20571), `SdkBackend.mark_echo_refused` (20644 to 20647) and `SdkBackend.settle_echoes` (24121): the flags `_landed`, `dropped`, `stale`, `refused` and `refusedWhy` are written into dicts that `SdkBackend.live_atoms` (23966) hands out by reference.
  - `kernel.py`, `_parse` (42758): `_txKey` written into the shared cached parse tree.
  - `kernel.py`, `_persist_spend_trees` (56546): dumps a memo the jobs thread mutates; it already retries a `RuntimeError` three times and counts a skipped write.
- **Walking other threads' frames** (documented unsafe, not reproduced): `kernel.py`, `_thread_stacks` (1718 to 1729), reached from `/perf`, and `_first_cycle_sample` (33067), about once a second for the first three minutes of every boot.
- **OrderedDict, latent:** `judge.py`, `_ParseStore` (3220 to 3237): the bare-id paths (`_newest`, and through it `__getitem__` and `__contains__`) iterate the store and rely on the caller holding `_PARSE_CACHE_LOCK`.

### Sites that raise an exception (the queue for items 1.3 to 1.8 and 1.11)

Each kills a request, a cycle or a thread.

- **Iterating while another thread mutates:**
  - `kernel.py`, `_names_snapshot` (3142): a comprehension over `_names_entry_memo`, outside the function's `try`.
  - `kernel.py`, `_PerfStats._mine` (915): a loop over `self._owners.items()`.
  - `kernel.py`, `_heal_cold_folds` (14159): a comprehension over `em._FOLD_REG.items()`.
  - `kernel.py`, `_held_mail_rearm` (30280): `tuple(_HELD_MAIL_SAID)`, safe only under the GIL.
  - `kernel.py`, `_remotes_rows_for_save` (28216): a comprehension over each remote row's items under `_remotes_lock`, while the pollers from `_poll_remote_sessions` through `_clear_remote_views_fault` (28684 to 29073) add and pop the rows' keys outside it.
  - `kernel.py`, `_msg_summaries` (about 58697): the walk and the deletes over `_msg_sum_cache["per"]`, and the union rebuilt from it, with more than one build able to run it at once.
  - `judge.py`, `_discover_fingerprint` (10173): the eviction walk over `_namefp_memo`, reached from `discover()`.
  - `sdk_backend.py`, `SdkBackend.catchup_lost` (17667), `SdkBackend.catchup_unreported` (17707) and `list_regs` (5590, over `_REG_CACHE`).
  - `event_model.py`, `asm_index_stats` (7884), `built_atoms_estimate` (7918) and `restored_index_estimate` (7933): the WeakSet `_LIVE_INDEXES` listed while weak-reference callbacks remove from it unlocked.
  - `credentials.py`, `helper_env` (332): a comprehension over `os.environ.items()`.
- **Evictions written as `d.pop(next(iter(d)))` with no lock:** `kernel.py`, `_thread_own_offset` (21025 and 21033), `_thread_own_records` (21071), `_thread_tail_parse` (21104), `_thread_events` (21415, `_built_thread`), `_thread_out_files_set` (21495), `_read_task_store` (40275), `_merge_tx_sets` (47601) and `_push` (70269, `_built_chat`).
- **A membership test, then a separate index:**
  - `kernel.py`, `_send_active_chat` (73241, 73243): `_ACTIVE_CHAT_BY_WID` and `_ACTIVE_CHAT_NONCE_BY_WID`.
  - `kernel.py`, `_pr_watches_save_locked` (29193) against the unlocked pops in `_pr_watch_tick` (29645).
  - `kernel.py`, `_vouch_tree` (46978): `_top_shape`.
  - `kernel.py`, `_summary_anchor_memo_get` and `_summary_anchor_memo_put` (59072 to 59092): a get then `move_to_end`, and a `popitem` that can meet an empty dict.
- **A field read twice:**
  - `kernel.py`, `_pure_feed` (71808) and `_cached_feed` (71633): `_built_feed[1]` is tested and then read again, and the cell is a four-slot list written in place, so `GET /feed.json` can answer 200 with the body `null`.
  - `sdk_backend.py`, `SdkBackend.drain` (17979) and `SdkBackend.kill` (21068): `s._host`.
- **Calls on a closed loop** (`sdk_backend.py`, 18 call sites in 16 methods of `SdkSession`): `_wake_feeder` (7562), `enqueue` (7656), `enqueue_if_empty` (7672), `interrupt` (7860), `_signal_cli` (7879), `set_model_live` (7906), `set_mode_live` (7928), `_hold_live_pick` (7944), `resolve_ask` (7988), `shutdown` (7994, 7998, 8003), `request_reconnect` (8051), `relaunch_now` (8077), `want_switch_reconnect` (9829), `refresh_usage` (10825), `request_stop_task` (14867) and `request_rewind_files` (14917).

### What would serialize the parallel work anyway (stage 3's material, recorded so it is not lost)

- **kernel.py:** `_FEED_BUILD_LOCK` is held across the whole `build_feed`, about 1 to 1.6 s, plus an fsync'd write of `push-feed.json`. `_pure_feed_lock` is held across a whole feed build. `_TL_BUILD_LOCK` holds a warm timeline build. `_order_lock` does a disk read and a JSON parse of `session-order.json` on every build. `_NUDGE_LOCK` is held across `json.dumps` of the whole ledger. `_notice_lock` is held across a whole-archive parse. `_remotes_lock` is held across a `ps` subprocess and a `Popen`. `_sdk_lock` is taken on every `_sdk()` call. `_page_lock` is held across `json.dumps` of a history page.
- **judge.py:** `_RAW_STORE_LOCK` is held across `pickle.dumps` of a whole store on every `load_goals` miss. `_GOAL_ARCH_LOCK` covers every session's archive.
- **sdk_backend.py:** `_reg_lock` is held across the registry read-modify-write and its JSON. `SdkBackend._lock` is held in `_ensure`. `_rl_lock` is held across a parse and dump of `spend.json` at every turn end.
- **event_model.py:** `_READ_STRIPES` (64 RLocks) are held across whole-file decoding, seconds long and 46 s at the worst on record, and unrelated files collide one time in 64. `_MAT_LOCK` is taken per atom, `_ASM_CKPT_LOCK` per `_asm_stat` call.
- **Structural:** the pusher builds every chat tab one after another on one thread.

### Checked on 2026-11-16, against main at `75fce42d5`

Main moved 77 commits past the audited commit. Each site above was located by its enclosing function at `4f195d62e` and compared with the same function on main: of 107 enclosing functions, 96 are byte-identical and every listed line survives in the other 11, with these exceptions and additions.

- **Fixed since the audit, dropped from the queue:**
  - The comment-thread memo. `_comment_frame_entry` set an entry in `_comment_created_memo` and then cleared the whole memo past 512 entries, so the thread that tipped it over was built with the parent's history as its own and that result was cached. This was a single-threaded bug, fixed by #463.
  - One of the converge latches. `_in_place_converge` now checks and sets `_INPLACE_TRIED` under `_INPLACE_LOCK` (#464).
- **Changed but still open:** `_msg_summaries` (the union is now dropped and rebuilt, the unlocked walk stands), `_fire_api_retry` (a third caller arrived with the spend-cap retry; the check and the send are still two steps), `_thread_events` and `_push` (their evictions stand).
- **Found in passing, not in the report:**
  - **The host re-exec keeps the host's own environment.** `_reexec_now` in `kernel/session_host.py` calls `os.execv`, which carries the environment the host was first started with. Setting `PYTHON_GIL=1` where a host is spawned therefore does not reach a host that was already running when the interpreter moved. Item 0.6 covers all three roads.
  - **A fourth child.** The document worker (`_doc_worker_argv`, `bin/romp-doc-worker`) postdates the audit. It bounds itself with an address-space limit (`_limit_address_space` in `kernel/doc_worker.py`) sized against the default allocator; mimalloc reserves address space differently. Measured on 2026-11-16: on 3.15t the bound refused every job, and item 0.9 is the fix.
  - **glibc-only instruments.** `/perf`'s `process.malloc` block (2026-11-14) reads glibc's books, and `_cap_malloc_arenas` caps glibc's arenas. On a free-threaded build neither describes Python objects. `GcFreeze` has also grown a growth-owed backstop walk gated on resident memory (#469, #474), which a free-threaded policy has to cover beside the fold-ins.
  - **A fifteenth OrderedDict**, `_TAIL_FIRST_QUEUE`, fully under `_TAIL_FIRST_LOCK`. Four more `pop(next(iter(d)))` evictions arrived too (40 on main against 36 at the audit); three sit under a lock, and the fourth, in `event_model._tail_stamps_step`, was not traced to its guard.
  - **A clear-at-cap memo of the family #463 fixed.** `_fire_api_retry` clears all of `_auto_retried` past 256 sessions, which forgets every other session's record that its current error already got its one automatic retry. The consequence was read from the code, not reproduced.
  - **Code added since the audit has not been audited:** the tail-first shadow, the document worker, the memory bounds. The lint's first run is their audit.
- **Counts that read differently on inspection:** the report counted 29 comments in kernel.py as relying on the GIL. Twenty-nine lines there name the GIL or the interpreter lock; most describe its cost, and the five listed under the conventions state a safety reliance. The report's lines for `_msg_summaries` were three off; the function is right.
- **An inconsistency left standing:** the lab measured `list(WeakSet)` as raise-free without the GIL, yet the report lists the three `_LIVE_INDEXES` readers as sites that raise. They stay in the queue, and the stress harness decides.
- **Not re-verified:** every timing, memory and collector figure (the lab is gone and the machine was at a load near 10); the test counts on the free-threaded builds; the wheel table and the 105-module import probe; the lock order; the category counts; the crashes on 3.13.14t and 3.14.10t. Re-run and confirmed: the `json.dumps` and generator crashes on 3.14.6t and their absence on 3.15.2t. Re-run and contradicted: the OrderedDict crash on 3.15.2t.

## The work queue

Each entry is one pull request, small enough to review alone and mergeable without the ones after it unless it says otherwise. Each names what it changes, the test that fails first, and its tier. Stage 0 and stage 1 are independent tracks and run in parallel.

**How a race gets a failing test.** Three forms, in order of preference:

1. **An injected interleaving.** A stub on the call that sits between the two steps performs the other thread's write, or a dict subclass mutates itself when iterated. Deterministic, single-threaded, fails before the fix on every build. This is the form for every site marked "today".
2. **A hammer under `sys.setswitchinterval(1e-6)`**, the form `tests/test_kernel_jsonl_cache.py` and `tests/test_judge_save_goals_tmp_race.py` already use, when the window cannot be reached by a stub.
3. **A hammer that only runs where `sys._is_gil_enabled()` is false**, for a site the GIL makes safe in practice (a bare counter, `tuple()` over a set). It fails first in the advisory GIL-off cell (T.3) and is skipped elsewhere, and the lint rule (T.1) is what holds the line on default builds.

**The "today" mark.** Each stage 1 item says whether it fixes a race that can fire today, under the GIL. The report gives the proportion (about half) and marks a few sites; the rest of the marks here are read from the idiom on main (a Python-level loop, a call between the check and the act, a file read and write) against the measured semantics. Treat a mark as a prediction: if form 1 cannot be made to fail under the GIL, the site belongs to form 3, and the pull request says so.

**Tiers.** Tests-only and CI-only changes carry `docs` (the tier that was `tests-only`). A change with a failing-first test carries `fix`. A new instrument, setting or tool carries `feature`. Nothing in stages 0 and 1 is a `major-feature`.

### Stage 0: the kernel on 3.15t with the GIL forced on

- **0.1 The two known test incompatibilities.** `docs`. `LockFree.test_a_collection_triggered_under_the_stats_lock_finishes` starts its collection explicitly inside the locked region instead of counting on an allocation threshold, since the hazard it guards is a collection callback running under the stats lock, however the collection began. `RelayExecuted.test_no_more_lines_reach_the_remote_than_the_browser_sent` reads the header limit from the running `http.client` (or counts the lines the remote received) instead of assuming 100. *Fails first:* both fail today under `uv run --python 3.15t`, the first on any free-threaded build with the GIL on or off, the second on 3.15 of either build; both pass on 3.10 to 3.13 before and after.
- **0.2 CI cells for 3.14, 3.15 and 3.15t with `PYTHON_GIL=1`.** `docs`; touches `.github/`. The `python-shard` and `python` matrices in `.github/workflows/ci.yml` stop at 3.13 today. Add the three cells on Linux. The 3.15t cell's first step fails unless the interpreter is a free-threaded build and `sys._is_gil_enabled()` is true, so a cell that silently ran something else cannot report green. The new checks report without being required until they are green, and then the owner adds their names to the ruleset. *Fails first:* the cells themselves. Expect more than 0.1 found: the lab ran 26 modules, not the suite. The pull request merges when its own three cells are green on its head, which the 3.15t cell cannot be before 0.9 lands: eight tests of `tests/test_doc_worker.py` fail there on the worker's bound.
- **0.3 Whatever the cells find.** `fix` or `docs`, one pull request per cause, each merged before 0.2. *Fails first:* the failing cell. Found on 2026-11-16: eight test assumptions (#479) and the document worker's bound (0.9).
- **0.4 The build and the GIL said at boot and on `/perf`.** `feature`. One boot line on stderr naming the Python version, the build (default or free-threaded) and whether the GIL is on; the same three facts under `/perf`'s `process` block. On a free-threaded build `/perf` also says that `process.malloc` describes glibc and not the Python heap. *Fails first:* a snapshot test asserting the keys (absent today), and a boot-line test.
- **0.5 `bin/romp-serve` forces the GIL on for a free-threaded interpreter.** `feature`. This is the one place stage 0's "forced on" is decided: when the picked interpreter's `abiflags` carry `t`, `romp-serve` exports `PYTHON_GIL=1` before the `exec`. The kernel checks at boot, and a free-threaded build that finds its GIL off says so loudly (stderr and a problem row), because nothing in stage 0 should produce that state. Stage 2's setting later replaces the unconditional export. *Fails first:* a bats test with a stub interpreter reporting a `t` build asserts the exported variable; a kernel test with `sys._is_gil_enabled` stubbed false asserts the problem row.
- **0.6 Session hosts keep the GIL on every road.** `feature`. Hosts gain nothing from free threading and there are hundreds of them. Three roads: the fresh spawn (`SdkBackend._spawn_host` passes `PYTHON_GIL=1` in the child's environment); the re-exec (`_reexec_now` in `kernel/session_host.py` sets it before `os.execv`); and the launcher (`bin/romp-session-host` checks at start and, on a free-threaded build with the GIL off, re-executes itself once with the variable set, because a host started by older code is re-exec'd by its old `_reexec_now`, and the launcher is then the first new code to run). The judge child, the postal service and the document worker inherit the kernel's environment and need nothing. *Fails first:* a test capturing `_spawn_host`'s `Popen` environment (none is passed today); a `session_host` test that the environment at the exec carries the variable; a launcher test with the interpreter's state stubbed.
- **0.7 SDK venvs side by side, one per interpreter tag.** `feature`; put the design points in the body. Today `bin/romp-sdk-setup` runs `rm -rf` on the one venv (`STATE/sdkvenv`) when the tag changes, so a rollback needs a rebuild and the network. With one venv per tag, switching interpreters and switching back need neither. Design points to settle in the pull request: where the per-tag directories live and how the existing venv migrates; which venv `pick_python` follows when several exist and no pin is set (the three copies of `pick_python` in `romp-serve`, `romp-sdk-setup` and `romp-codex-setup` are kept byte for byte in sync); the kernel's `_sdk_venv_site_packages` and the launcher's lookup; the Codex venv (`STATE/codexvenv`) the same way. *Fails first:* a bats test that builds for one tag, runs setup for another with stub interpreters, and asserts the first venv is still there.
- **0.8 A free-threaded policy for `gc_freeze`.** `feature`. On a free-threaded build: no load fold-ins, at most one freeze after the boot's loads settle, and a stated decision for the release reclaim and the growth-owed backstop walk (each already a full walk on a default build; on this build the re-freeze is a second one). `GcFreeze` takes the build as an injected fact, as `apply_gen2_threshold` takes `free_threaded`, so the tests run on any interpreter, and `/perf`'s `gc.freeze` block names the policy in force. *Fails first:* in `tests/test_gc_freeze.py`, a controller told it is free-threaded runs no collection on a busy tick with inserts (today it folds in).
- **0.9 The document worker's memory bounds count the job, not the interpreter.** `fix`. It must land before 0.2: the three cells cannot all be green without it. The measurement showed the problem (2026-11-16). The worker bounded its whole address space, a free-threaded interpreter holds 1,060 MB of it before any job, and the bounds for a small job are about 100 MB, so every job was refused: in CI the worker died at its first import under the limit, and on a build with its extension modules linked in it ran and then refused its own result. Eight tests of `tests/test_doc_worker.py` failed and no document was written. The fix has two parts. The bounds count from what the worker holds as its parse begins: the limit is that plus the admitted figure, and the soft bound reads how far the address space's peak or the resident peak grew, whichever is larger. And the kernel starts the worker with `MIMALLOC_ARENA_RESERVE=0`, so a free-threaded worker's address space grows with its job and the limit stops it where it stops a default build's; without that, a job could grow a gigabyte past its limit. On a default build both bounds move by the worker's baseline and nothing else changes: 29 MB of address space on 3.12.3 and 48 MB on 3.15.2, or 57 and 52 MB on a first run, which compiles the event model. *Fails first:* the eight on 3.15t; on every build, the real worker in a process that reserved a gigabyte of address space before the job; on 3.15t, a heap under the worker's limit with the allocator setting taken away.
- **0.10 The document worker's sizing on a free-threaded build.** `fix`; not built. The estimate (64 MiB, plus the bytes, plus 8 KiB a record) was measured on default builds, and for a job estimated under 512 MiB the soft bound equals it. A free-threaded parse holds more per record: the 708-byte-record transcript above grew by 417 MB against an estimate of 412 MB and was refused by its own soft bound, where a default build grew by 322 MB. With 0.9 in, such a job hands itself to the kernel and keeps a memory verdict, as designed, so nothing is lost and nothing is silent; but a transcript of small records can meet that on 3.15t where it would not on a default build. Measure the per-byte and per-record figures on 3.15t over the three record shapes of the worker's review, and size the estimate by build, before the worker's switch is on under that interpreter. *Fails first:* a worker job over a small-record synthetic transcript on 3.15t, sized as the kernel sizes it, is refused for memory.

### Stage 0's deploy order

1. **CI first.** 0.1 to 0.3 and 0.9 merged and the three cells green; 0.4 to 0.8 merged and deployed on today's interpreters, where they change no behaviour beyond the boot line and where the venv lives.
2. **The 4-core development VM.** Install 3.15t with `uv python install --no-bin` (or the standalone build), pin the minor-version path in `ROMP_PYTHON` where the service reads its environment, build the venv for the new tag, restart. Hosts re-exec into the new interpreter with the GIL on. The four-step move is in `docs/reference.md`, "The kernel's Python". Read the baseline before the move: `/perf`'s gc block, the pusher's cycle p50 and p99, `connectPush`, resident size.
3. **Observe for one to two weeks** against that baseline. The boot line and `/perf` must say free-threaded with the GIL on, every day.
4. **The large shared host**, after the VM is clean. It runs the distribution's 3.13 with no `ROMP_PYTHON` pin today, so its move is also its first pin. Its stage 0 is the GC go or no-go: it measures the full-heap stop-the-world collection at the real heap, about 18 GB at the audit, with the GIL still on.
5. **Rollback, on either machine:** `ROMP_PYTHON` back and a restart. Hosts re-exec into the old interpreter. With 0.7 in, that needs no rebuild.

### Stage 1: shared state made safe, under the GIL

In the report's order, which is the order of what a GIL-off kernel would hit hardest. The items marked "today" fix live races and are worth landing whatever is decided about the GIL; they can go first.

- **1.1 Stop mutating published objects.** *Today: no.* With the GIL on, a reader sees one state or the other. Four pull requests, each `fix`:
  - **1.1a Live atoms.** A flag change replaces the stored atom with a changed copy under `_live_lock`, so a dict `live_atoms` has handed out never changes. Sites: `SdkBackend._mark_dropped_echoes`, `mark_echo_refused`, `settle_echoes`. The identity sets `_mark_dropped_echoes` builds over the atoms must keep working. *Fails first:* take `live_atoms(sid)`, run the flagging path, assert the dict already returned is unchanged and a fresh read shows the flag. Deterministic on any build.
  - **1.1b Hydrate.** `_hydrate_one` fills a lazy atom in place by design, and its write order was chosen so that a reader meets a whole body. Start with the test: dump a tree while another thread hydrates it and assert that every atom in the dump is either still lazy or whole. Change the code only if that fails on 3.15t, and put the design in the body, since the alternatives (fill a copy and swap the row; hydrate before every dump) both cost something on the hot path. *Fails first:* that test, in the GIL-off cell.
  - **1.1c `_txKey`.** `_parse` stamps the shared cached tree after `jd.parsed_session` returns it. Stamp it where the tree is built, before it is published, or carry the key beside the tree. *Fails first:* a parse served from the cache writes nothing into the tree (a tree wrapped to refuse writes after publication).
  - **1.1d The spend-tree memo.** `_persist_spend_trees` dumps a copy taken in one step (`dict(m["dirs"])`, `dict(m["files"])`) instead of the live memo. *Fails first:* a memo that mutates while it is dumped is written whole and `dumpSkipped` stays at zero.
- **1.2 Gate the frame walkers.** `fix`. *Today: no.* `_thread_stacks` and `_first_cycle_sample` do not call `sys._current_frames()` when the GIL is off; `/perf` says the stacks are unavailable and why, and the first-cycle sampler counts what it skipped. *Fails first:* with `sys._is_gil_enabled` stubbed false, the walkers are asserted not to walk.
- **1.3 OrderedDict.** Required before the GIL can come off, because the iteration crash survives in 3.15.2t.
  - **1.3a Lock `_SUMMARY_ANCHOR_MEMO`.** `fix`. *Today: yes.* The get with its `move_to_end`, the put with its eviction, and the memo's byte and entry counters move under one lock. *Fails first:* an injected eviction between the get and the `move_to_end` raises `KeyError`.
  - **1.3b `_ParseStore`'s bare-id readers.** `fix`. *Today: no, latent.* `_newest`, `__getitem__` and `__contains__` either take `_PARSE_CACHE_LOCK` themselves or the store asserts its caller holds it. *Fails first:* a bare-id read made without the lock is refused, or passes under mutation, depending on the form chosen.
  - **1.3c Plain dicts behind a lock for the rest.** `fix`, one pull request per two or three memos. A plain dict keeps insertion order, `pop` and re-insert is `move_to_end`, and iterating one under mutation raises where an OrderedDict segfaults. Fifteen instances on main: `_POSTAL_TAKEN`, `_SEND_ACCEPTS`, `_TAIL_FIRST_QUEUE`, `_FEED_SEG_HELD`, the held table in `_msg_sum_cache`, `_SUMMARY_ANCHOR_MEMO`, `_JUDGING_MARKS_MEMO`, `_STATE_IV_MEMO`, `_SLICE_CACHE`, `_GLOSSARY_CACHE` and `_PUSH_PULL_SEEN` in kernel.py; `_MAT_LRU` in event_model.py; `SdkSession._bg_ended`; `_NARR_MEMO` in narration.py; `_ParseStore` in judge.py. *Fails first:* each memo's existing eviction and order tests, plus the lint rule that forbids a new module-level OrderedDict.
- **1.4 Snapshot before iterating, and evictions that cannot raise.** *Today: yes for the loops and comprehensions; at a full cache for the evictions.*
  - **1.4a One eviction helper.** `fix`. The nine unlocked `d.pop(next(iter(d)))` sites listed above go through one helper that treats a lost race as nothing to do. *Fails first:* a dict that drops its first key when iterated makes the bare spelling raise `KeyError`.
  - **1.4b The simple snapshots in kernel.py.** `fix`. `_names_snapshot`, `_PerfStats._mine`, `_heal_cold_folds` and `_held_mail_rearm` iterate a `list()` taken in one step and delete with a default. *Fails first:* form 1 for the first three; `_held_mail_rearm`'s `tuple()` over a set cannot fail under the GIL and takes form 3.
  - **1.4c Remote rows.** `fix`; design points in the body. `_remotes_rows_for_save` iterates rows that the pollers mutate outside `_remotes_lock`. Either the pollers write a row's fields under the lock, or a row is replaced whole. `_remotes_lock` is on the list of locks held too long, so the second is the better fit. *Fails first:* a poller's write during the save's comprehension.
  - **1.4d `_msg_summaries`.** `fix`. More than one build walks and edits the per-session table at once. Single-flight it with a try-acquire, or guard the table. *Fails first:* a second build's delete during the first build's walk.
  - **1.4e The judge, the backend, the event model and credentials.** `fix`. `_discover_fingerprint`, `SdkBackend.catchup_lost`, `SdkBackend.catchup_unreported`, `list_regs`, the three `_LIVE_INDEXES` readers and `helper_env`. *Fails first:* form 1 each; the `_LIVE_INDEXES` readers by form 2, since the lab and the report disagree on whether they raise.
- **1.5 Read shared cells once.** `fix`. *Today: yes for the feed; unproven for the host field.* `_pure_feed` and `_cached_feed` read the payload into a local and test the local. `SdkBackend.drain` and `SdkBackend.kill` read `s._host` once. *Fails first:* a stub on `_pusher_has_feed_audience` that invalidates the cell makes `_pure_feed` return `None` today; a stand-in session whose `_host` answers once and then `None` makes `drain` raise.
- **1.6 Latches.** `fix`, one pull request per subsystem. *Today: yes where a call sits between the check and the act.* Each becomes a check-and-set under one lock or a try-acquire single-flight, the convention `_AUTO_NUDGE_TICK_LOCK` sets. Sites: `_run_update` and the update arm of `Handler.do_POST` (`_UPDATE_STATE`; the report marks this one as safe under the GIL in practice); `_run_main_update`, which two update requests can each start on a thread of its own; `_fire_api_retry` (`_auto_retried` read, then the send); `_pin_assoc` and `_pin_assoc_append` (`_PIN_ASSOC_MEMO`); `_reveal_request` and `_consume_pending_reveal` (`_PENDING_REVEAL`); `_fire_move`, `_move_now` and the move arm of `Handler.do_POST` (`_moving`); `loadsource.load_source` (`sys.modules` checked, then the module loaded). *Fails first:* two callers held at a barrier inside the stubbed slow step both proceed.
- **1.7 One tuple instead of two stores.** `fix`. *Today: yes.* The pair is published as one tuple in one slot and read once. Sites: `judge._delegates_to` (`_DELEG_BY_MID` is cleared and refilled beside `_DELEG_CACHE`; a reader in between finds no delegation, and the report marks the result as one that is persisted); `judge._postal_row` (`_postal_from_memo`); `_acct_read` (`_ACCT_CACHE`, which feeds login and billing decisions); `_judge_usage_rows` (duplicated judge rows); `_publish_dismissed_lanes` (cleared, then refilled); `_checkout_code_ident` (`_CHECKOUT_IDENT`); `_relay_active_chat` with `_send_active_chat` (a session id from one announcement beside the nonce of another). *Fails first:* a reader placed between the two writes sees a mismatched pair.
- **1.8 Read-modify-writes under a lock.** `fix`. *Today: yes.* `_end_on_idle_sweep` (the request file is loaded, edited and saved, so a request written in between is lost); the pending-tag journal (`_queue_pending_tag_edit`, `_apply_pending_tag_edits`); `_pr_watches_save_locked` against `_pr_watch_tick`'s unlocked pops; `_vouch_tree` (`_top_shape`). *Fails first:* a second writer between the load and the save loses its row.
- **1.9 Counters.** `fix`. *Today: no.* The lab lost no update with the GIL on. About ten are revisions and sequence numbers a consumer compares, and those go first: `_models_changed` (`_models_rev`), `_next_nonce`, `_note_ws_drop` (`_WS_DROP_SEQ`), `_node_anchor_uuids` (`_node_anchor_rev`), the `_SUMMARY_ANCHOR_STATS` bytes and fault counts (with 1.3a), `_views_dirty` (`_mark_views_dirty` and the write in `Handler._dispatch_ws`), `judge._session_file_written` (`SESSION_FILE_WRITES`), and `sdk_backend.write_reg` with `_reg_rows_note` (`REG_REV`, `REG_ROWS_REV`). A pure sequence becomes an `itertools.count`, which the lab measured duplicate-free without the GIL; the rest go through one small locked helper, as `_chat_memo_bump` already does for one memo. The other 140 or so are statistics and follow by the lint's list. *Fails first:* form 3, plus the lint rule on `X[0] += 1` outside a lock.
- **1.10 Single-owner assertions.** `feature`. *Today: no.* State the conventions call jobs-only, doc-writer-only, supervisor-only or pusher-only records its owner at first use; a write from another thread is counted on `/perf` and said once, never raised into the pusher's loop. *Fails first:* a pusher-only mutator called from another thread is reported (nothing reports it today).
- **1.11 Calls on a closed loop.** `fix`. *Today: yes; the report marks this class as already racy.* Not one of the report's ten steps; it comes from its exception list. One helper on `SdkSession` makes the cross-thread call and answers false when the loop is gone or closed, the 18 call sites use it, and the session clears `loop` when its `asyncio.run` returns. *Fails first:* a session whose loop has closed takes an `enqueue` without raising into the caller.

### Tooling

- **T.1 The lint.** `feature`. A static check in CI, rebuilt from the audit's inventory. It flags: a module-level OrderedDict; `tuple`, `sum`, `max`, `min`, `any`, `all`, `map`, `join` or a Python loop over a module-level container outside a lock; `X[0] += 1` or `global n; n += 1` outside a lock; `pop(next(iter(d)))`; a write into an object returned from a shared cache. The escape hatch is an annotation with a reason, `# ft: confined(<thread>)` or `# ft: published`. It lands with a baseline of today's findings, so it holds the line on new code from its first day and the baseline shrinks as stage 1 lands. Without it the audit rots: kernel.py grew from 62,900 to 92,500 lines in the two months before the audit and by another 2,800 in the four days after. *Fails first:* the lint's own test feeds it each pattern.
- **T.2 The stress harness.** `docs` (tests only). `tests/test_ft_stress_*` on the `notes-api` board. Threads hammer the three `build_feed` paths, connect pushes with needFull, `parse_session` on the same and on different files, the record cache's budget eviction, `discover()`, `load_goals` with `save_goals`, and `live_atoms` against `settle_echoes`. They assert no exception, the caches' invariants (byte accounting, LRU sizes), and frames equal to a serial run. Under `sys.setswitchinterval(1e-6)` on the default cells, natively in T.3's cell, sized to fit a CI shard.
- **T.3 An advisory GIL-off cell.** `docs`; touches `.github/`. 3.15t with `PYTHON_GIL` unset, asserting after the kernel's import that the GIL is off (never `PYTHON_GIL=0`). Advisory until stage 1 has landed. This is a CI cell, not a deployment: it is where form 3 tests fail first and where T.2 runs for real.
- **T.4 The comparison harness.** `feature`. A tool under `tools/`: one scripted event stream replayed into two lab kernels, one with the GIL on and one with it off, each on a temp state root with session hosts off and `ROMP_CLAUDE_BIN=/bin/false`, comparing every frame and the state directory. It runs outside CI, and it is the instrument stage 2's soak needs, so it is built in stage 1.

### A suggested first eight

1. 0.1, the two known test incompatibilities.
2. 0.2, the three CI cells (with 0.3 as they report, and 0.9, which its 3.15t cell needs).
3. 0.4, the build and the GIL at boot and on `/perf`.
4. 1.5, shared cells read once (the feed that can answer `null`).
5. 1.11, calls on a closed loop.
6. 0.5 with 0.6, the GIL forced on for the kernel and kept on for hosts.
7. T.1, the lint with its baseline.
8. 1.3a with 1.4a, the unlocked OrderedDict and the eviction helper.

Then 0.7 and 0.8 before the first interpreter move, and the rest of stage 1 in its order.

## What is gated, and on what

**Turning the GIL off (stage 2) waits for all of these:**

- Stage 1 landed, with T.3's cell green and required.
- The stress harness and the GIL-on against GIL-off comparison passing in a lab soak of 24 to 72 hours.
- A collection pause at the large host's real heap, measured in its stage 0, that the owner accepts.
- A 3.15t patch level with no open free-threading crash on a pattern the kernel uses. Today that means no OrderedDict left that can be iterated during a mutation (1.3), or an upstream fix.
- The 2026-09-14 harness re-run if it can be found, and the lab bench run on the large host at 1 to 16 threads.

**Out of scope until decided:**

| Stage | What | Effort, as the report estimated | Risk | Rollback |
| --- | --- | --- | --- | --- |
| 2. GIL off on the development VM, behind a setting | A setting `romp-serve` reads: off exports `PYTHON_GIL=1`; on leaves it unset and the kernel asserts the GIL is off. A lab soak with stress, the comparison and ThreadSanitizer, then the VM for at least two weeks | about 1 week, plus 2 to 3 weeks of soak | Medium: rare races | The setting off and a restart; no interpreter or venv change |
| 3. Make the parallelism pay | Per-path read locks in event_model; `_MAT_LOCK` sharded or given per-thread counters; the `_order_lock` read cached by mtime; serialization moved out of `_NUDGE_LOCK`, `_RAW_STORE_LOCK` and `_page_lock`; `_reg_lock` and `_rl_lock` narrowed; tabs built on a small worker pool | 1 to 3 weeks | Medium: lock redesign | Revert per pull request; all of it is valid under the GIL too |
| 4. The large host, GIL off | The lab bench there first; the kernel pinned to one NUMA node with no CPU quota; a canary with the setting | about 1 week, plus 2 to 4 weeks of soak | Medium to high: about 230 sessions | The setting off and a restart |
| 5. A default | Only when CPython's free-threading fix rate settles; GIL builds stay supported in `install.sh` | | | |

Later layers of proof belong to stage 2's soak, not to stages 0 and 1: `pytest-run-parallel` on the pure modules; a 3.15t built with ThreadSanitizer running the stress harness (it finds races in C code, the class behind the three crashes, not Python-level logic races); and a canary with `/perf` counters for thread exceptions, kernel restarts by the manager and a collection-pause histogram. The blast radius of a crash is bounded either way: hosted sessions survive a kernel restart, and the manager respawns a killed kernel in about two seconds.

The report's estimate for the whole road was 2.5 to 4 months elapsed and 8 to 10 engineer-weeks, of which stage 0 is about a week plus one to two weeks of observation and stage 1 about four engineer-weeks.

## Risks

1. **Garbage collection.** Full-heap stop-the-world collections, and a frozen heap still walked at about 40 percent of the cost. On the large host's heap a collection could pause every thread for seconds. Stage 0 there measures it.
2. **CPython itself.** Three crash classes on 3.14.10t, one of them still on 3.15.2t, and 7 to 8 free-threading fixes in each recent patch release. cffi declares itself Beta; pydantic-core and rpds-py declare nothing. `PYTHON_GIL=0` would hide an unsafe extension. And 3.15.2, on either build, no longer applies the recursion limit to a function that calls itself through `*args` or `**kwargs`.
3. **Rare races.** A static audit cannot prove absence. The layered tests, a setting that turns the GIL back on, and hosts that survive kernel restarts are what bound the damage.
4. **The audit rots.** About 160,000 lines, with kernel.py growing by thousands of lines a week. Without the lint and the stress tests in CI, safety decays.
5. **The big locks cap the gain** until stage 3 splits them.
6. **Memory.** 17 to 21 percent more resident memory, and up to 29 percent for a parse of small records; mimalloc retains pages and defers frees; the glibc arena cap no longer applies to Python objects; address space is reserved a gigabyte at a time, so a limit on it bounds nothing unless the reservation is turned off for that process.
7. **Single-thread speed.** Small: within 10 percent of 3.14 with the GIL in the lab, 1 to 8 percent slower by upstream's figures, and even or faster against today's 3.12 and 3.13.
8. **Session hosts and the SDK's loops.** Hosts are started with the kernel's interpreter and re-exec'd into it on a restart, so they follow the kernel onto 3.15t; they keep the GIL (0.6). 3.14 and later support parallel asyncio loops, and anyio keys its process-wide state per loop.
9. **Deployment.** Two machines on two different distribution Pythons, one with no pin; a venv that is deleted on a tag change until 0.7; shims that `--no-bin` must keep out of the fallback's path; `-I` and `-E` ignoring `PYTHON_GIL`.

## Beside the process split

| | The process split's decode workers | Free threading |
| --- | --- | --- |
| What runs in parallel | Cold JSON decoding (83 to 90 percent of a cold parse); only history before the last compaction, until its stage one b | Everything in the process: parses, builds, judges, HTTP, pushes |
| How data is shared | Documents on disk (26 MB compressed for 44 sessions, 0.2 s to restore one) | Zero-copy, one record cache |
| Isolation | A worker crash is not a kernel crash | An interpreter crash takes the kernel down (hosted sessions survive) |
| Python requirement | Any | 3.15t and its wheels |
| Safety work | Small: new IPC and single-flight across processes | About 150 known sites, the unknown ones, the lint and the stress tests |
| Memory and collection | The kernel's heap shrinks, so pauses shrink | More resident memory; full-heap stop-the-world collections |
| Steady-state contention between the judges and the pusher | Only with its stages three and four (more processes, more IPC) | Yes |

They combine. The split's documents and decode worker shrink the heap and isolate decoding, which takes the edge off the collector risk here; free threading reaches what the split cannot reach cheaply.

## Sources

- The free-threading guides for [3.14](https://docs.python.org/3.14/howto/free-threading-python.html) and [3.13](https://docs.python.org/3.13/howto/free-threading-python.html); What's New in [3.14](https://docs.python.org/3.14/whatsnew/3.14.html) and [3.15](https://docs.python.org/3.15/whatsnew/3.15.html); the [3.14 changelog](https://docs.python.org/3.14/whatsnew/changelog.html).
- [PEP 779](https://peps.python.org/pep-0779/) and the [thread-safety guarantees](https://docs.python.org/3/library/threadsafety.html).
- [gh-125996](https://github.com/python/cpython/issues/125996), the OrderedDict fix that was not backported to 3.13.
- [`gc_free_threading.c` on the 3.14 branch](https://raw.githubusercontent.com/python/cpython/3.14/Python/gc_free_threading.c) and [`test_free_threading/test_json.py` on main](https://raw.githubusercontent.com/python/cpython/main/Lib/test/test_free_threading/test_json.py), which the 3.14 branch lacks.
- The [ThreadSanitizer guide](https://py-free-threading.github.io/thread_sanitizer/), the [library compatibility tracker](https://py-free-threading.github.io/tracking) and [pytest-run-parallel](https://pypi.org/project/pytest-run-parallel).
- `plans/process-split.md` (the 2026-09-14 review and the measurement this reopens) and `plans/gc-full-collection-pause.md` (the collector on default builds and the freeze).
