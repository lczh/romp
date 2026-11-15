#!/usr/bin/env python3
"""Road B for issue #1735: keep the loaded decoded heap out of the cycle collector's walk.

A generation-two (full) collection visits every tracked container in the interpreter; the
request-serving kernel holds millions of them in decoded transcript trees, so a warm full
collection pauses every thread (the pusher included) for seconds. `gc.freeze()` moves the
currently tracked objects into a permanent generation the collector never walks again, so a
later full collection walks only what was allocated since. Measured (plans/gc-full-collection-pause.md):
a warm full collection over 5.6 million loaded objects fell from 2.83 s to 0.1 ms once they were
frozen, and the freeze itself is a pointer move.

Two operations, both at the end of a pusher cycle and keyed on events (never a timer):

  - a LOAD fold-in (cheap): `gc.collect(); gc.freeze()`. The collect walks only the unfrozen objects
    loaded since the last freeze, so it costs milliseconds; the freeze folds their survivors in.
    Keyed on the record cache's insert counter growing by a material number of trees, on ANY cycle but
    the boot's first, busy or idle (2026-09-28). It used to wait for an idle cycle, and a pusher that
    almost never idles then almost never folded in: everything loaded after the last freeze stayed in
    the walk of every automatic full collection. CPython's trigger for one has two conditions (THE
    FULL-COLLECTION TRIGGER, below): more than `threshold2` generation-one collections since the last
    full collection, and the objects promoted since reaching a quarter of what survived it. Once one
    full collection has run after a freeze, the quarter counts only unfrozen objects and passes, so the
    count decides; at the stock threshold2 those came often, and each walked every post-freeze load
    again, a pause that grew with the load. A busy fold-in walks those same unfrozen objects once and
    takes them out. The initial freeze likewise waits for no idle cycle: the first
    cycle after the boot's first with anything loaded makes it, so a pusher that never idles still freezes.
  - a RELEASE reclaim (up to the full pause), at the pusher's IDLE boundary only, where the frozen-heap
    walk is paid with no browser waiting. STAGED: `gc.collect()` with the freeze IN PLACE first (cheap,
    it takes a released cycle allocated since the last freeze), then the owed refs are re-read and only a
    survivor drives `gc.unfreeze(); gc.collect()` (the frozen-heap walk); a `gc.freeze()` re-holds either
    way. A release the cheap walk took whole counts as a LOAD pass, so the backstop's bound does not stretch.

WHEN to reclaim is MEASURED, not guessed. Three review rounds each guessed cyclicity from a state flag
(a session's `client`, its thread) and each found a new path the guess missed (a teardown exception
after the client was cleared leaves a cyclic session with client None). So instead of deciding at the
pop, every session-end pop REGISTERS a `weakref.ref(session)` (with its worker thread) via `note_ended`, and the idle tick judges each by observation: a dead
ref died by reference counting (acyclic, no reclaim); a live ref whose worker thread still runs is not garbage yet (kept for the
next tick, a kill mid-turn); a live ref whose thread has finished is a cycle the collector must take (reclaim once for all
such, then drop them). Exact on every path, no per-path reasoning. A BOUNDED BACKSTOP (after
`backstop_foldins` load fold-ins) covers any ended owner nobody registered. It is a frozen-heap walk
too, so an idle cycle runs it; a busy cycle runs it only once the fold-ins reach TWICE
`backstop_foldins`, a full window with no idle cycle to take it (2026-09-28: a busy fold-in can freeze a
live cycle that dies later, and on a pusher that never idles nothing else would ever reclaim it).

THE BACKSTOP IS BOUNDED BY THE HEAP'S GROWTH TOO (2026-11-14). A fold-in freezes every live tracked object, not the loaded trees
alone: the cycles of every other thread's work in flight (a judge's parse, a handler's build, a session's task, a caught
exception's frame) are frozen live and die later, and from then on no collection reaches them until a frozen-heap walk. The
fold-in count was the only bound, and on a loaded kernel making about 40 fold-ins an hour the thousand is more than a day
(the two thousand of a pusher that never idles, two), while a kernel restarted for its memory before then never walked at
all; the frozen garbage of every fold-in in between is resident and invisible to every gauge. So each run reads the heap
after its freeze (`sys.getallocatedblocks()`, the small-object allocator's live blocks: about a millisecond per nine million,
where `gc.get_freeze_count()` is a 14 ms per million walk of the frozen list that would double a fold-in's pause), and once
the reading has grown past `backstop_growth` of the reading at the last walk (a quarter, CPython's own trigger for a full
collection applied to the generation the freeze hides from it) and past the floor (two million blocks, so a small kernel's
warm-up never walks), the next idle tick runs the backstop; a busy cycle takes it at a half, as it takes the count at twice.
The floor protects a small kernel's warm-up only: the baseline is the initial freeze's reading, the first loaded cycle, so a
loaded kernel's warm-up walks at every quarter of its fill, from a few million blocks to tens of millions, several walks in a
restart's first minutes, when it is already slowest catching up (a tenfold fill walks eight times). What bounds that is the
series, not the floor: each walk costs its heap's size and the sizes grow by a quarter, so their sum is under five times the
final heap's, under three on busy cycles at a half.
The walk's cost is linear in the frozen heap and it comes once per quarter of growth, so, like CPython's rule, it costs a
constant per allocated block whether the growth was garbage or a cache filling; a cache filling buys one wasted walk per
quarter, never more. Every reclaim records what its unfrozen collection found (`last_reclaim_collected`: the garbage the freeze
had locked away; a backstop, like a release, runs a cheap collect with the freeze in place first, so the young garbage since the
last collection is taken before the walk and not counted in it) and the frozen count after its re-freeze (`frozen_at_reclaim`, a
walk read only where a walk was just paid for), so /perf's plain read says how much the freeze held and holds without `?walk=1`.
THE WALK'S COST AT A LOADED KERNEL'S SIZE IS NOT MEASURED. The lab figure is 0.5 s per million tracked objects (the 2.83 s over 5.6
million above); a kernel at 8 GB holds tens of millions, so a walk there is plausibly several seconds, with every handler and
driver thread waiting on the GIL for it, four or five times in twelve hours at the live growth, and that is a projection until the
first walk is read: `last_reclaim_ms` is that reclaim's WHOLE run (both collections, the re-freeze and the frozen count read,
which is itself 14 ms per million frozen), held until the next reclaim where `last_ms` is the next fold-in's within minutes, and
`reclaim_ms_max` the largest since boot; the backstop's stderr line carries the same figure. `ROMP_GC_FREEZE_BACKSTOP_GROWTH=0`
turns the growth bound off while that reading is weighed.

THE GROWTH BOUND WAITS FOR MEMORY TO BE SCARCE (2026-11-15). The walk stops every thread for a time linear in the heap: a lab
fill of 240,000 synthetic records (0.86 GB resident, 12.5 million blocks) ran five growth walks of 108 to 491 ms where the
longest collection without the growth bound was 7 ms, and 1.7 s of collection where there had been 0.44 s, about 0.6 s per GB
at the last walk (a review of the growth bound read 0.7), once per quarter of growth and again through every restart's fill:
seconds on a 15 GB machine, tens of seconds where the kernel holds tens of GB, by that line. What the walk buys is the frozen
garbage's memory back, and a machine whose kernel is a small part of its memory is not short of it. So growth owes the walk only while the process's resident size is at or above `backstop_resident` (one half) of its memory
bound, the bound every memory-scaled cache takes: MemTotal, or the process's cgroup limit where one sits below it (a unit's
MemoryHigh= or MemoryMax=, the knob for a machine whose usable memory is under its MemTotal). A kernel of 30 GB on a 2 TB host
never walks for growth; one of 3 GB on a 15 GB machine not until it passes 7.5 GB; one under a limit, from half that limit.
The two readings are `resident` and `memory_bound`, injected (the kernel passes its _resident_bytes and its _mem_total_bytes).
THE BOUND IS READ ONCE, at the first run that needs it, and kept, as every memory-scaled bound is read once at start; the
resident size is read once per run, after its freeze, and never per tick. A run is on the pusher thread, where a syscall
costs more than its microseconds: each releases the GIL, and beside CPU-bound threads a release can cost the pusher a switch
interval before it has the interpreter back. The gate's first form read both at every run, 115 syscalls on a five-level
cgroup path (/proc/self/status, /proc/meminfo, /proc/self/cgroup and two files per cgroup level, each through a buffered
text file), and its review measured a median of 27 to 52 ms per fold-in so spent, 0.4 s at worst (a lab beside two
CPU-bound threads: a median of 521 ms an evaluation). A run now makes three: one open, one read and one close of
/proc/self/statm (the same lab: 21 ms; 0.04 ms uncontended). So a limit changed on a running kernel (a unit's MemoryHigh= set
live) applies at the kernel's next start, not at its next fold-in: not worth a hundred syscalls a fold-in. Under the
fraction a run reads neither `sys.getallocatedblocks()` (a walk of the
allocator's pools, 3 to 5 ms at that lab's 0.86 GB, 218 ms over its 119 runs) nor, on a reclaim, `gc.get_freeze_count()`: both
served the growth bound and its report alone, so a kernel with memory to spare pays for a run what it paid before the growth
bound (the same lab fill with the gate closed: no walk, a longest collection of 7.6 ms, 0.45 s of collection), plus that one
file read. `backstop_growth` at 0 still means never, and now reads none of it either. The fold-in count and a release walk
as they did, at any size.
WHEN THE GATE OPENS LATER there is no walk to measure growth from: nothing frozen while memory was plentiful was walked, and
memory is now scarce, so the walk is owed outright once the heap is past the floor, on a busy cycle too (a pusher that never
idles would otherwise never take it). From that walk on it is the quarter rule. The baseline moves only at a walk whose reading
was taken and stands across a spell back under the fraction, and a walk made while armed reads the heap whatever the gate says
after it (the walk frees memory, so it can take the kernel back under the fraction itself): crossing the fraction is never a
walk by itself, and after that first one two growth walks are never less than a quarter of growth apart, as before the gate.
THE GATE HOLDS A WALK BACK ON A READING, NEVER ON THE LACK OF ONE: a reader that raises or answers nothing positive leaves
growth owing the walk as it did before the gate, flagged (`growth_gate_unread`) for the kernel to say once (a bound that could
not be read is not read again, and the resident size is then not read at all); a controller given no readers has no gate. `ROMP_GC_FREEZE_BACKSTOP_RESIDENT` sets the fraction: 0 arms the growth walk at any size with neither
reading taken, the growth bound exactly as it was written. /perf carries the fraction, both readings, whether growth walks are
armed and how many ran. THE SIGNAL IS THE PROCESS'S OWN SHARE, not the machine's free memory: MemAvailable would see a balloon
that MemTotal does not, but it moves with every other process and the page cache, so on a shared host a neighbour's memory
would bring a walk of tens of seconds to a kernel that holds a hundredth of the machine, and under a cgroup limit it is not the
cgroup's figure at all.

A busy
cycle judges the ended sessions only before the first freeze, as the idle tick does: the initial freeze's
collect takes their pre-freeze garbage, so the owed refs are dropped at no cost. Once a freeze is held, a
busy cycle never judges them: their release waits for the idle tick. The record cache's
`released` counter is a /perf statistic, never a trigger: its entries are acyclic.

The thread test rests on CPython's own threading: a running session and its worker Thread reference each
other (the Thread holds the session as its `_target`), and `Thread.run` deletes `_target` in a `finally`
when it returns, so once the thread has finished it no longer holds the session. A live ref whose thread
has finished is therefore held by a DIFFERENT surviving cycle (a traceback frame, a stray reference), the
reclaim's target. An UNSTARTED thread reads as finished (`is_alive()` False) and still holds the session
(the Thread keeps `_target`, since `run`'s finally never fires for it), so the ref is alive on a cycle;
judging it a reclaim is correct precisely because ONLY the collector can take that cycle (refcounting never
will), which is what a reclaim does. A ref a live ROOT keeps (a helper thread still running when the tick
judges it, its frame and its target, not a cycle) reads the same, alive with a finished worker, and is TREATED
as a surviving cycle: the reclaim frees nothing, so it costs ONE reclaim, is counted a `survivor` on /perf,
and is dropped (never re-registered), never a pass per tick.

A request that arrives during a reconcile waits that one collection. The idle boundary is the best
moment for the frozen-heap walks, not a guarantee none arrives; a busy fold-in walks only what an
automatic full collection would have walked anyway. Default on; `ROMP_GC_FREEZE=off` (or `0`/`false`) off.

THE FULL-COLLECTION TRIGGER (2026-10-03, `apply_gen2_threshold`). CPython starts an automatic full collection when
generation two has seen more than `threshold2` (stock 10) young-generation collections AND the objects promoted since
the last full collection reach a quarter of what survived it. After an automatic full collection over a frozen heap,
what survived it is only the unfrozen part, which is small, so the quarter test passes and threshold2 decides: an
automatic full collection can come every eleventh generation-one collection, each walking whatever was promoted since,
often mid-build with the build's temporaries alive. (A fold-in's collection sets CPython's survivor count to everything
it kept and the freeze after it leaves that count alone, so the quarter test holds the next one back for a while after
each fold-in; on a loaded kernel most
full collections are still the automatic ones, which /perf tells apart: gc.gen."2".collections less
gc.freeze.collections.) So while the freeze is on, main() raises threshold2 to `DEFAULT_GEN2_THRESHOLD`
(`ROMP_GC_GEN2_THRESHOLD`, floored at the stock 10, and a value past the C int the collector takes falls back to the
default, so the knob can only raise it): at most one automatic full collection per 101 generation-one collections, and
each controller run (a fold-in, a release, a backstop: each a full collection) restarts that count. Young cycles are
still taken by generations zero and one at the stock rate. Not applied with the freeze off (the measurement switch
measures stock CPython) or on a free-threaded build (a different collector), and declined where the collector does not
keep the value: the third threshold is read back after the set, and if it does not read the new value the replaced one
is put back and the stock trigger stands (apply_gen2_threshold says what that check can and cannot see). Calling
apply_gen2_threshold again with the controller disabled puts back the value it replaced; nothing in the kernel makes
that call after boot today.
"""
import gc as _gc_mod
import os
import sys
import sysconfig
import threading
import time
import weakref

DEFAULT_LOAD_TREES = 8            # record-cache inserts since the last freeze that count as a material load
MIN_LOAD_TREES = 1               # floored here: a threshold of 0 or below would fold in every cycle
DEFAULT_BACKSTOP_FOLDINS = 1000  # a reclaim after this many load fold-ins since the last reclaim, bounding a cyclic release
#                                  no ended ref caught; at the ~97 fold-ins an hour measured when fold-ins ran only on idle
#                                  cycles it fired about once in ten hours; busy-cycle fold-ins (2026-09-28) come more often,
#                                  and the bound stays on fold-ins because a fold-in is what can freeze a live cycle. A loaded
#                                  kernel read at ~40 fold-ins an hour (2026-11-14), so this alone is more than a day: the heap's
#                                  growth (DEFAULT_BACKSTOP_GROWTH) bounds the walk in between
DEFAULT_GEN2_THRESHOLD = 100    # CPython's threshold2 while the freeze is on (stock 10): an automatic full collection at most
#                                  every 101 generation-one collections since the last full one, controller runs included
MIN_GEN2_THRESHOLD = 10          # the stock value: the knob can only raise the trigger, never make full collections more frequent
DEFAULT_BACKSTOP_GROWTH = 0.25   # the heap's growth since the last frozen-heap walk, as a fraction of the reading at that walk, that owes
#                                  the next walk (2026-11-14): CPython's own quarter (a full collection once the objects promoted since
#                                  the last one reach a quarter of what survived it), applied to the generation the freeze hides from
#                                  that rule. Read as sys.getallocatedblocks() after a run's freeze: the small-object allocator's live
#                                  blocks, about a millisecond per nine million, never gc.get_freeze_count (14 ms per million frozen)
DEFAULT_BACKSTOP_GROWTH_FLOOR_BLOCKS = 1 << 21   # and by at least this many blocks (a few hundred MB of small objects): a small kernel
#                                                  grows by a quarter in its first minutes of warm-up and owes no frozen-heap walk for it.
#                                                  A loaded kernel's warm-up is not protected by it: that one walks at every quarter of
#                                                  its fill, a geometric series summing to under five times the final heap (docstring)
DEFAULT_BACKSTOP_RESIDENT = 0.5  # growth owes the walk only while the process's resident size is at or above this fraction of its memory
#                                  bound (MemTotal, or the process's cgroup limit where lower), 2026-11-15: the walk stops every thread for
#                                  about 0.6 s per GB of heap, and a kernel that is a small part of its memory is not short of what it frees.
#                                  0 arms it at any size (the growth bound as written, 2026-11-14), with neither reading taken
MAX_GEN2_THRESHOLD = 2147483647  # the largest C int, the most gc.set_threshold takes: a knob past it falls back to the default
_OFF = ("off", "0", "false")


def enabled_from_env(env=None):
    """The freeze is on unless ROMP_GC_FREEZE names an off value (the measurement switch)."""
    v = (env if env is not None else os.environ).get("ROMP_GC_FREEZE")
    return not (v is not None and v.strip().lower() in _OFF)


def load_trees_from_env(env=None):
    """(load_trees, bad_raw): the ROMP_GC_FREEZE_LOAD_TREES knob, parsed with a fallback to the default on a
    value that is not a positive integer, floored at MIN_LOAD_TREES. `bad_raw` is the offending string when the
    default was substituted (the caller says it once and counts it), else None. Parsed here, never at the kernel's
    import with a bare int(): a bad value must not kill the kernel before it serves (#1735 verifier, the high)."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_LOAD_TREES")
    if raw is None or raw.strip() == "":
        return DEFAULT_LOAD_TREES, None
    try:
        n = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_LOAD_TREES, raw
    return max(MIN_LOAD_TREES, n), None


def backstop_growth_from_env(env=None):
    """(fraction, bad_raw): the ROMP_GC_FREEZE_BACKSTOP_GROWTH knob, parsed like `load_trees_from_env`: the heap growth,
    as a fraction of the reading at the last frozen-heap walk, that owes the next walk (DEFAULT_BACKSTOP_GROWTH). 0 turns
    the growth bound OFF, leaving the fold-in count as the only backstop (the switch a new multi-second full-collection
    trigger on a production kernel owes, 2026-11-14); a value above 1 is clamped to 1, a heap that doubled being bound
    enough; a value that is not a finite number, or is negative, falls back to the default and comes back as `bad_raw`
    (the caller says it once and counts it), else None. Never a bare float() at the kernel's import."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_BACKSTOP_GROWTH")
    if raw is None or raw.strip() == "":
        return DEFAULT_BACKSTOP_GROWTH, None
    try:
        f = float(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_BACKSTOP_GROWTH, raw
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        return DEFAULT_BACKSTOP_GROWTH, raw
    return min(1.0, f), None


def backstop_resident_from_env(env=None):
    """(fraction, bad_raw): the ROMP_GC_FREEZE_BACKSTOP_RESIDENT knob, parsed like `backstop_growth_from_env`: the fraction
    of its memory bound the process's resident size must reach before heap growth owes a frozen-heap walk
    (DEFAULT_BACKSTOP_RESIDENT, one half). 0 arms the growth walk at ANY size, the growth bound as it was written on
    2026-11-14 (for a machine whose usable memory is under its MemTotal with no cgroup limit to say so); a value above 1 is
    clamped to 1, armed only at the bound itself; a value that is not a finite number, or is negative, falls back to the
    default and comes back as `bad_raw` (the caller says it once and counts it), else None. Turning the growth walk OFF is
    the growth knob's 0, not this one's. Never a bare float() at the kernel's import."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_FREEZE_BACKSTOP_RESIDENT")
    if raw is None or raw.strip() == "":
        return DEFAULT_BACKSTOP_RESIDENT, None
    try:
        f = float(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_BACKSTOP_RESIDENT, raw
    if f != f or f in (float("inf"), float("-inf")) or f < 0:
        return DEFAULT_BACKSTOP_RESIDENT, raw
    return min(1.0, f), None


def gen2_threshold_from_env(env=None):
    """(threshold, bad_raw): the ROMP_GC_GEN2_THRESHOLD knob, parsed like `load_trees_from_env`: a value that is not an
    integer, or is above MAX_GEN2_THRESHOLD (the largest C int, the most gc.set_threshold takes, which raises past it),
    falls back to DEFAULT_GEN2_THRESHOLD and comes back as `bad_raw` (the caller says it once and counts it, once
    apply_gen2_threshold has applied that default), and any other integer is floored at MIN_GEN2_THRESHOLD, the stock
    value, so the knob can only raise the trigger."""
    raw = (env if env is not None else os.environ).get("ROMP_GC_GEN2_THRESHOLD")
    if raw is None or raw.strip() == "":
        return DEFAULT_GEN2_THRESHOLD, None
    try:
        n = int(raw.strip())
    except (TypeError, ValueError):
        return DEFAULT_GEN2_THRESHOLD, raw
    if n > MAX_GEN2_THRESHOLD:
        return DEFAULT_GEN2_THRESHOLD, raw
    return max(MIN_GEN2_THRESHOLD, n), None


def apply_gen2_threshold(controller, value=DEFAULT_GEN2_THRESHOLD, gc=None, free_threaded=None):
    """Set CPython's full-collection trigger (threshold2, the third value of `gc.set_threshold`) from the controller's
    state, and return the trigger this call set, or None when it set none. With the controller ENABLED on a build that
    is not free-threaded it keeps the first two thresholds, sets the third to `value` (floored at MIN_GEN2_THRESHOLD)
    and reads the third back: only once it reads what was set are the trigger and the value it replaced recorded. If it
    reads anything else, the collector does not keep the value (some interpreters read it back as 0 after any set), so the
    replaced value is put back, nothing is recorded, and /perf's gen2Threshold stays null. That check reads the STORED
    value, not the collector's behavior: a free-threaded build stores the raised value, yet a probe there ran no
    automatic full collection at either setting, so a read-back cannot say whether the value is honored there, and the
    free-threaded gate stays. With the controller DISABLED it changes nothing, unless this function raised the trigger
    earlier: then it puts back the value it replaced, provided the trigger still reads what it set (a value someone set
    since is theirs, and stays). A disabled controller that never raised it makes no collector call at all, so
    ROMP_GC_FREEZE=off leaves the stock thresholds. `gc` defaults to the controller's own collector; `free_threaded`
    defaults to this interpreter's build (injected so a test pins the gate)."""
    gc = controller._gc if gc is None else gc
    applied = controller.gen2_threshold
    if not controller.enabled:
        if applied is not None:
            t0, t1, t2 = gc.get_threshold()
            if t2 == applied:
                gc.set_threshold(t0, t1, controller.gen2_replaced)
            controller.gen2_threshold = None
        return None
    if free_threaded is None:
        free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if free_threaded:
        return None
    n = max(MIN_GEN2_THRESHOLD, int(value))
    t0, t1, t2 = gc.get_threshold()
    gc.set_threshold(t0, t1, n)
    if gc.get_threshold()[2] != n:               # the collector did not keep it: the replaced value back, nothing recorded
        gc.set_threshold(t0, t1, t2)
        return None
    if applied is None:
        controller.gen2_replaced = t2            # the stock value, put back if the controller is turned off
    controller.gen2_threshold = n
    return n


class GcFreeze:
    """The freeze controller. `gc` and `clock` are injected so a test drives a fake collector or a real one; the
    kernel passes the real `gc`. The `tick`/`tick_busy`/`_run` path runs on the pusher thread; `note_ended` runs on every
    thread that ends a session (the HTTP handler, housekeeping, a worker), so a small lock guards the ended list."""

    def __init__(self, enabled=True, load_trees=DEFAULT_LOAD_TREES, backstop_foldins=DEFAULT_BACKSTOP_FOLDINS,
                 gc=_gc_mod, clock=time.perf_counter, heap_blocks=sys.getallocatedblocks,
                 backstop_growth=DEFAULT_BACKSTOP_GROWTH, backstop_growth_floor=DEFAULT_BACKSTOP_GROWTH_FLOOR_BLOCKS,
                 backstop_resident=DEFAULT_BACKSTOP_RESIDENT, resident=None, memory_bound=None):
        self.enabled = bool(enabled)
        self.load_trees = max(MIN_LOAD_TREES, int(load_trees))
        self.backstop_foldins = max(1, int(backstop_foldins))
        self.backstop_growth = max(0.0, float(backstop_growth))   # 0 turns the growth bound off; the fold-in count still bounds the walk
        self.backstop_growth_floor = max(0, int(backstop_growth_floor))
        self._gc = gc
        self._clock = clock
        self._heap_blocks = heap_blocks   # the heap reading the growth bound compares (sys.getallocatedblocks; a test injects a counter)
        self.heap_blocks = None           # the reading after the last run's freeze (None until a run, when the reader raised, or when
        #                                   that run was under the growth gate and read none)
        self.heap_blocks_at_walk = None   # the reading after the last frozen-heap walk (the initial freeze, a full release, a backstop)
        self.last_reclaim_collected = 0   # what the last reclaim's unfrozen collection found: the garbage the freeze had locked away (the
        #                                   young garbage since the last collection taken by the staged cheap collect first, not counted here)
        self.reclaim_collected = 0        # the same, summed over every reclaim
        self.frozen_at_reclaim = None     # gc.get_freeze_count() after the last reclaim's re-freeze (the walk was paid for anyway); None
        #                                   when that reclaim ran with the growth gate closed, which reads no gauge
        self.backstop_resident = max(0.0, float(backstop_resident))   # the growth gate (2026-11-15): growth owes the walk only while the
        #                                   resident size is at or above this fraction of the memory bound; 0: at any size
        self._resident = resident         # the process's resident size in bytes, read once per run, and
        self._memory_bound = memory_bound   # its memory bound in bytes, read ONCE (the kernel passes its own readers; None: no gate)
        self._bound_read = False          # whether the bound was asked for: once, at the first run that needs it, never again
        self.resident_bytes = None        # the resident size at the last run, and the bound as read that once (None before a run,
        self.memory_bound_bytes = None    # where the gate takes no reading, or where the reader raised or answered nothing positive)
        self.growth_armed = self.backstop_growth > 0 and (self.backstop_resident <= 0 or resident is None or memory_bound is None)
        #                                   whether growth owes the walk: as of the last run's readings where there is a gate (False until
        #                                   the first run reads them), from the start where there is none
        self.growth_gate_unread = False   # the last run's gate lacked a reading and left the walk armed: the kernel says it once
        self.growth_walks = 0             # backstops that the heap's growth owed (the fold-in count's are reclaims less these and the releases)
        self.frozen = False
        self._ins_mark = 0           # cache inserts at the last freeze
        self._foldins = 0            # load fold-ins since the last reclaim (the backstop counts these)
        self._ended = []             # (weakref(session), weakref(thread)|None) registered at each session-end pop
        self._ended_lock = threading.Lock()   # note_ended runs on other threads than the tick; guard the list
        self.freezes = 0             # initial freeze plus load fold-ins
        self.reclaims = 0            # unfreeze/collect/re-freeze passes (a cyclic ended ref, or the backstop)
        self.busy_runs = 0           # of all the runs, those a BUSY cycle ran (an initial freeze, a load fold-in, the busy backstop)
        self.survivors = 0           # owed refs still alive after a reclaim's collect: kept by a LIVE ROOT, not a cycle (a wasted pause), counted once and dropped
        self.last_release_sids = []  # first 8 chars of each sid a reclaim was owed for, set under the ended lock, cleared per judgement and per busy run after the first freeze
        self.last_release_survivors = 0   # of the LAST RUN's owed refs, how many a live root kept through it (0 when the reclaim freed
        #                                   them, and 0 on any run that owed none, e.g. a load after a live-root release: read right after a release)
        self.last_kept_sids = []     # the sids of those survivors, likewise rebound each run (cleared to [] by a run that owed none); named kept-by-a-live-root in the release line
        self.collections = 0         # gc.collect() calls the RUN STEP issued (a full release issues two): /perf, so organic = gen2 collections less this, exactly
        self.last_ms = 0.0           # the last reconcile's collection pause
        self.total_ms = 0.0          # every reconcile's collection pause, summed
        self.last_reclaim_ms = 0.0   # the last RECLAIM's whole run, its collections, re-freeze and frozen count read (2026-11-14): held
        #                              until the next reclaim, where last_ms is the next fold-in's within minutes; the stall a thread
        #                              waiting on the GIL saw, and the only measurement of a frozen-heap walk at a loaded kernel's size
        self.reclaim_ms_max = 0.0    # the largest such run since the process started
        self.last_kind = None        # "initial" | "load" | "release" | "backstop", for /perf
        self.gen2_threshold = None   # the full-collection trigger apply_gen2_threshold set, None while the stock one stands (/perf)
        self.gen2_replaced = None    # the trigger it replaced, put back when the controller is turned off

    def note_ended(self, session, thread=None):
        """Register an ended session for the idle tick to judge (a weakref, never a strong ref, so it can die by
        reference counting). `thread` is the session's worker thread; the tick reads whether it still runs. Called
        from whatever thread ended the session, so it takes the ended lock."""
        if not self.enabled:
            return
        try:
            pair = (weakref.ref(session), weakref.ref(thread) if thread is not None else None)
        except TypeError:
            return                   # an object that cannot be weak-referenced: the backstop still covers it
        with self._ended_lock:
            self._ended.append(pair)

    def resolve_ended(self):
        """Judge the registered ended sessions by OBSERVATION and return the list of refs a reclaim is OWED for (empty
        when none, so it reads as a boolean too). A dead ref died by reference counting (acyclic, dropped, no reclaim);
        a live ref whose thread still runs is not garbage yet (kept); a live ref whose thread has finished is a cycle the
        collector must take (owed, and dropped: one reclaim then gone, never re-registered, so a live ROOT that keeps it
        costs exactly one wasted pause). Never reclaims for a dead ref: that is the whole point of measuring instead of
        guessing. Judges IN PLACE under the ended lock, so a registration landing on another thread mid-judgement is never
        dropped. The owed sids' first 8 characters are set on `last_release_sids` under the lock, cleared each judgement, so
        a release names the sessions it was owed for (the 2026-09-22 PR 1999 review, attribution)."""
        keep, owed = [], []
        with self._ended_lock:
            ended = self._ended
            self._ended = keep                       # new registrations land in `keep` while we judge the old list
            for sref, tref in ended:
                if sref() is None:                   # died by refcount: acyclic, gone, nothing to reclaim
                    continue
                t = tref() if tref is not None else None
                if t is not None and t.is_alive():
                    keep.append((sref, tref))        # the worker thread still runs: not garbage yet, judge again next tick
                else:
                    owed.append((sref, tref))        # alive with its thread finished: a surviving cycle the reclaim must take
            self.last_release_sids = [(getattr(sref(), "sid", "") or "")[:8] for sref, _ in owed if sref() is not None]
        return owed                                  # `keep` is already self._ended (set under the lock); no racy rebind here

    def growth_due(self, times=1):
        """Whether the heap has grown, since the last frozen-heap walk, by `times` the backstop_growth fraction of the reading
        at that walk (a quarter for an idle cycle, a half for a busy one) and by at least the floor. False with no reading of
        the heap (no run yet, a reader that raised, or a last run under the growth gate, which reads none: 2026-11-15) or
        with the fraction at 0: then the fold-in count alone bounds the walk. Armed with a reading and NO walk to measure it
        from (the gate opened after the first freeze), the walk is owed outright once the heap is past the floor, at any
        `times`: nothing frozen while memory was plentiful was ever walked (the module docstring)."""
        base, now = self.heap_blocks_at_walk, self.heap_blocks
        if now is None or self.backstop_growth <= 0 or not self.growth_armed:
            return False
        if base is None:
            return now >= self.backstop_growth_floor
        grown = now - base
        return grown >= self.backstop_growth_floor and grown >= times * self.backstop_growth * base

    def tick(self, inserts):
        """One idle-boundary pass: judge the ended sessions, then run the owed operation. Returns the kind run, or
        None. The reclaim is owed by an observed cyclic ended session or the backstop; the fold-in by a material
        load; the first freeze once anything is loaded."""
        if not self.enabled:
            return None
        owed = self.resolve_ended()
        if not self.frozen:
            return self._run("initial", inserts, []) if inserts > 0 else None
        if owed:
            return self._run("release", inserts, owed)
        by_count = self._foldins >= self.backstop_foldins
        if by_count or self.growth_due(1):
            kind = self._run("backstop", inserts, [])
            if not by_count:
                self.growth_walks += 1
            return kind
        if (inserts - self._ins_mark) >= self.load_trees:
            return self._run("load", inserts, [])
        return None

    def tick_busy(self, inserts):
        """One BUSY-cycle pass (2026-09-28): the initial freeze once anything is loaded, the load fold-in on a material load,
        and the backstop only once the fold-ins reach twice `backstop_foldins` (a full window passed with no idle cycle to
        take it). Judges the ended sessions only while no freeze is held, as the idle `tick` does there: the initial
        freeze's collect takes their pre-freeze garbage, so the owed refs are dropped at no cost (left unjudged, a ref a
        live root keeps would be frozen with the heap and cost a full release at the first idle cycle; the 2026-09-28
        review). Once a freeze is held it never judges them: a release, and the frozen-heap walk a survivor drives, waits
        for the idle `tick`. A busy load or backstop judged nothing and owed nothing, so it clears `last_release_sids` (under
        the ended lock, where `resolve_ended` sets it): /perf's sids, survivors and kind then describe the same run, never a
        load beside the sids of an earlier release (the same review). Returns the kind run, or None."""
        if not self.enabled:
            return None
        if not self.frozen:
            self.resolve_ended()                     # no frozen heap yet: judge the pre-freeze refs and drop the owed, as the idle tick does
            kind = self._run("initial", inserts, []) if inserts > 0 else None
        else:
            by_count = self._foldins >= 2 * self.backstop_foldins
            if by_count or self.growth_due(2):
                kind = self._run("backstop", inserts, [])
                if not by_count:
                    self.growth_walks += 1
            elif (inserts - self._ins_mark) >= self.load_trees:
                kind = self._run("load", inserts, [])
            else:
                kind = None
            if kind is not None:
                with self._ended_lock:
                    self.last_release_sids = []      # this run owed no reclaim: no sids, as its survivor count is 0
        if kind is not None:
            self.busy_runs += 1
        return kind

    def _run(self, kind, inserts, owed):
        """Run the owed collector operation and return the kind actually run. A RELEASE is STAGED (the 2026-09-22 PR 1999
        review): a plain collect with the freeze IN PLACE first (cheap, it takes a cycle allocated since the last freeze),
        then the owed refs are re-read; only when one survived does it unfreeze and walk the frozen heap (the up-to-full
        pause). A release the cheap walk took whole counts as a LOAD pass, so the backstop's bound does not stretch. A
        BACKSTOP is blind (no owed refs) and always unfreezes, after the same cheap collect with the freeze in place (2026-11-14:
        the fold-in this tick would otherwise have run; without it the walk's count was the frozen garbage plus the young garbage
        since the last collection, an upper bound where `last_reclaim_collected` is documented as what the freeze held). A
        backstop therefore runs two collections, as a full release does. After the collect and the re-freeze, an owed ref still
        alive is kept by a LIVE ROOT, not a cycle: a wasted pause, counted as a survivor and dropped (never re-registered).
        The re-read runs after the re-freeze, which is harmless: a freeze collects nothing, so a ref alive after the collect
        is alive after the freeze too. The growth gate is read once here, after the freeze (2026-11-15; one file read): the heap, and on a
        reclaim the frozen count, are read only while growth owes the walk, or after a walk made while it did (so that walk
        is the baseline even when it took the kernel back under the fraction)."""
        t0 = self._clock()
        was_armed = self.growth_armed
        reclaimed, found = False, 0
        if kind == "release":
            self._gc.collect(); self.collections += 1    # cheap: the freeze stays in place, so this walks only the unfrozen
            if any(sref() is not None for sref, _ in owed):
                self._gc.unfreeze()                  # a survivor: the released cycle is in the frozen set, lift it and walk
                found = self._gc.collect(); self.collections += 1
                reclaimed = True
            # else the cheap collect took the released cycle whole: no full pause, counted as a load pass below
        elif kind == "backstop":
            self._gc.collect(); self.collections += 1    # staged too (2026-11-14): the fold-in this tick would have run takes the young garbage first,
            self._gc.unfreeze()                      # blind reclaim (the fold-in count or the heap's growth): nothing owed to re-read, walk everything
            found = self._gc.collect(); self.collections += 1   # so this count is the garbage the freeze held, not that plus the young
            reclaimed = True
        else:
            self._gc.collect(); self.collections += 1    # initial / load fold-in: walk only the unfrozen
        self.last_ms = (self._clock() - t0) * 1000.0
        self.total_ms += self.last_ms
        self._gc.freeze()                            # (re)freeze: the survivors leave the collector's walk again
        was_frozen = self.frozen
        self.frozen = True
        self._ins_mark = inserts
        # the owed refs still alive after the re-freeze are kept by a LIVE ROOT, not a cycle: the reclaim freed nothing. Record
        # this release's survivor count and their sids (the release line names them kept, not "reclaimed"), and total them.
        kept = [(getattr(sref(), "sid", "") or "")[:8] for sref, _ in owed if sref() is not None]
        self.last_release_survivors = len(kept)
        self.last_kept_sids = kept
        self.survivors += len(kept)
        gauges = self._read_gate() or (reclaimed and was_armed)   # the gate's resident reading, once per run; under it no gauge is read
        if gauges:
            self._read_heap()                        # after the freeze: what the heap holds now, the garbage this run took gone
        else:
            self.heap_blocks = None                  # no reading this run: growth_due reads None as not due
        if reclaimed:
            self.last_kind = kind
            self.reclaims += 1
            self._foldins = 0
            self.last_reclaim_collected = int(found or 0)   # the unfrozen collection's count: the garbage the freeze had locked away, the
            #                                                  young garbage gone to the staged cheap collect before it
            self.reclaim_collected += self.last_reclaim_collected
            self.frozen_at_reclaim = self._read_frozen() if gauges else None
            if gauges:
                self.heap_blocks_at_walk = self.heap_blocks     # the growth bound measures from this walk; a walk under the gate
                #                                                 read nothing, so the last walk that did stays the baseline
            self.last_reclaim_ms = (self._clock() - t0) * 1000.0   # the whole run, the frozen count read included: what the GIL's waiters saw
            self.reclaim_ms_max = max(self.reclaim_ms_max, self.last_reclaim_ms)
        else:
            self.last_kind = "initial" if not was_frozen else "load"   # a cheap-collect release folds in like a load
            self.freezes += 1
            if was_frozen:
                self._foldins += 1                   # a load fold-in; the initial freeze is not one
            else:
                self.heap_blocks_at_walk = self.heap_blocks   # the initial freeze's collect walked everything: the first baseline
        return self.last_kind

    def _read_heap(self):
        """The heap reading after a run (sys.getallocatedblocks by default), or None when the reader raised: a reconcile never
        fails on its gauge, and growth_due reads None as not due."""
        try:
            self.heap_blocks = int(self._heap_blocks())
        except Exception:
            self.heap_blocks = None

    def _read_gate(self):
        """The growth gate, read once per run after its freeze: whether heap growth owes a frozen-heap walk at this size. Never
        with `backstop_growth` at 0 (nothing read); always with `backstop_resident` at 0 or with no readers given (no gate,
        nothing read); otherwise growth is armed while the resident size is at or above the fraction of the bound. The BOUND
        is read once, at the first run that comes here, and kept (the memory-scaled bounds are all read once at start, and a
        run is no place for the cgroup tree's files: the module docstring); the resident size is the one reading a run
        takes. A reading that cannot be had (the reader raised, or answered nothing positive) leaves it ARMED, as before the
        gate, and is flagged on `growth_gate_unread`: the gate holds a walk back on a reading that memory is plentiful, never
        on the lack of one. With no bound the resident size is not read: there is nothing to compare it with. Sets
        `growth_armed` and returns it."""
        resident, unread = None, False
        if self.backstop_growth <= 0:
            armed = False
        elif self.backstop_resident <= 0 or self._resident is None or self._memory_bound is None:
            armed = True
        else:
            if not self._bound_read:
                self._bound_read = True
                self.memory_bound_bytes = self._read_bytes(self._memory_bound)
            bound = self.memory_bound_bytes
            if bound is not None:
                resident = self._read_bytes(self._resident)
            unread = resident is None
            armed = unread or resident >= self.backstop_resident * bound
        self.resident_bytes, self.growth_gate_unread, self.growth_armed = resident, unread, armed
        return armed

    @staticmethod
    def _read_bytes(reader):
        """A gate reading in bytes, or None when the reader raised or answered nothing positive: a reconcile never fails on it."""
        try:
            n = int(reader())
        except Exception:
            return None
        return n if n > 0 else None

    def _read_frozen(self):
        """gc.get_freeze_count() after a reclaim's re-freeze, or None where the collector lacks it or the read raised. A walk of the
        frozen generation (14 ms per million in the lab), read only here, where the reclaim just walked that heap anyway."""
        read = getattr(self._gc, "get_freeze_count", None)
        if read is None:
            return None
        try:
            return int(read())
        except Exception:
            return None

    def perf(self):
        """The /perf gc block's freeze sub-block: the state and the reconcile trade, so a reconcile collection is
        told apart from an organic one. `active` is whether a freeze is held now (named apart from the gc block's
        `frozen` count, gc.get_freeze_count() on GET /perf?walk=1). `gen2Threshold` is the full-collection trigger the
        controller set (None while the stock one stands; the gc block's `thresholds` reads the collector itself). The growth
        gate's state (2026-11-15): `backstopResident` the fraction, `memoryBoundBytes` the bound as read once at the first
        run and `residentBytes` the resident size at the last one (None before a run, where the gate takes no reading, or
        where one could not be had), `growthArmed` whether growth owed the walk by them, `growthWalks` how many backstops
        growth has owed. Reads nothing."""
        return {"enabled": self.enabled, "active": self.frozen, "loadTrees": self.load_trees,
                "gen2Threshold": self.gen2_threshold,
                "backstopFoldins": self.backstop_foldins, "backstopGrowth": self.backstop_growth,
                "backstopResident": self.backstop_resident, "memoryBoundBytes": self.memory_bound_bytes,
                "residentBytes": self.resident_bytes, "growthArmed": self.growth_armed, "growthWalks": self.growth_walks,
                "heapBlocks": self.heap_blocks, "heapBlocksAtWalk": self.heap_blocks_at_walk,
                "lastReclaimCollected": self.last_reclaim_collected, "reclaimCollected": self.reclaim_collected,
                "frozenAtReclaim": self.frozen_at_reclaim,
                "freezes": self.freezes, "reclaims": self.reclaims,
                "busyRuns": self.busy_runs,
                "collections": self.collections, "survivors": self.survivors, "lastReleaseSurvivors": self.last_release_survivors,
                "lastReleaseSids": list(self.last_release_sids),
                "endedPending": len(self._ended), "lastReconcileMs": round(self.last_ms, 1),
                "lastReconcileKind": self.last_kind, "totalReconcileMs": round(self.total_ms, 1),
                "lastReclaimMs": round(self.last_reclaim_ms, 1), "reclaimMsMax": round(self.reclaim_ms_max, 1)}


def pusher_tick(controller, idle, first, stats_fn, on_error):
    """The pusher's end-of-cycle call, extracted so it is pinned in-process. Never on the boot's FIRST cycle. An IDLE cycle
    runs the full `tick` (the release, the backstop, the fold-in); a BUSY one runs `tick_busy` (the initial freeze, the
    fold-in, and the backstop only past its busy bound). 2026-09-28: this used to return on every busy cycle, which starved
    the fold-in on a pusher that almost never idles. `stats_fn` returns the record cache stats (its `inserts` keys the LOAD
    fold-in). A raising `stats_fn` or tick is handed to `on_error` and never propagates: the pusher must not die on the
    freeze. Returns the reconcile kind run, or None."""
    if first or not controller.enabled:
        return None
    try:
        inserts = int((stats_fn() or {}).get("inserts") or 0)
        return controller.tick(inserts) if idle else controller.tick_busy(inserts)
    except Exception as e:
        on_error(e)
    return None
