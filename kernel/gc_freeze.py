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
    the walk of every automatic full collection. Those come often after a freeze, since CPython's
    trigger (promoted objects reaching a quarter of the oldest generation's size at the last full
    collection) counts only unfrozen objects once one full collection has run, so each walked every
    post-freeze load again, a pause that grew with the load. A busy fold-in walks those same unfrozen
    objects once and takes them out. The initial freeze likewise waits for no idle cycle: the first
    cycle after the boot's first with anything loaded makes it, so a pusher that never idles still freezes.
  - a RELEASE reclaim (up to the full pause), at the pusher's IDLE boundary only, where the frozen-heap
    walk is paid with no browser waiting. STAGED: `gc.collect()` with the freeze IN PLACE first (cheap,
    it takes a released cycle allocated since the last freeze), then the owed refs are re-read and only a
    survivor drives `gc.unfreeze(); gc.collect()` (the frozen-heap walk); a `gc.freeze()` re-holds either
    way. A release the cheap walk took whole counts as a LOAD pass, so the backstop's bound does not stretch.

WHEN to reclaim is MEASURED, not guessed. Three review rounds each guessed cyclicity from a state flag
(a session's `client`, its thread) and each found a new path the guess missed (a teardown exception
after the client was cleared leaves a cyclic session with client None). So instead of deciding at the
pop, every session-end pop REGISTERS a `weakref.ref(session)` (with its worker thread) via `note_ended`,
and the idle tick judges each by observation: a dead ref died by reference counting (acyclic, no
reclaim); a live ref whose worker thread still runs is not garbage yet (kept for the next tick, a kill
mid-turn); a live ref whose thread has finished is a cycle the collector must take (reclaim once for all
such, then drop them). Exact on every path, no per-path reasoning. A BOUNDED BACKSTOP (after
`backstop_foldins` load fold-ins) covers any ended owner nobody registered. It is a frozen-heap walk
too, so an idle cycle runs it; a busy cycle runs it only once the fold-ins reach TWICE
`backstop_foldins`, a full window with no idle cycle to take it (2026-09-28: a busy fold-in can freeze a
live cycle that dies later, and on a pusher that never idles nothing else would ever reclaim it). A busy
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
"""
import gc as _gc_mod
import os
import threading
import time
import weakref

DEFAULT_LOAD_TREES = 8            # record-cache inserts since the last freeze that count as a material load
MIN_LOAD_TREES = 1               # floored here: a threshold of 0 or below would fold in every cycle
DEFAULT_BACKSTOP_FOLDINS = 1000  # a reclaim after this many load fold-ins since the last reclaim, bounding a cyclic release
#                                  no ended ref caught; at the ~97 fold-ins an hour measured when fold-ins ran only on idle
#                                  cycles it fired about once in ten hours; busy-cycle fold-ins (2026-09-28) come more often,
#                                  and the bound stays on fold-ins because a fold-in is what can freeze a live cycle
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


class GcFreeze:
    """The freeze controller. `gc` and `clock` are injected so a test drives a fake collector or a real one; the
    kernel passes the real `gc`. The `tick`/`tick_busy`/`_run` path runs on the pusher thread; `note_ended` runs on every
    thread that ends a session (the HTTP handler, housekeeping, a worker), so a small lock guards the ended list."""

    def __init__(self, enabled=True, load_trees=DEFAULT_LOAD_TREES, backstop_foldins=DEFAULT_BACKSTOP_FOLDINS,
                 gc=_gc_mod, clock=time.perf_counter):
        self.enabled = bool(enabled)
        self.load_trees = max(MIN_LOAD_TREES, int(load_trees))
        self.backstop_foldins = max(1, int(backstop_foldins))
        self._gc = gc
        self._clock = clock
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
        self.last_kind = None        # "initial" | "load" | "release" | "backstop", for /perf

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
        if self._foldins >= self.backstop_foldins:
            return self._run("backstop", inserts, [])
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
        the ended lock, where the judgement sets it): /perf's sids, survivors and kind then describe the same run, never a
        load beside the sids of an earlier release (the same review). Returns the kind run, or None."""
        if not self.enabled:
            return None
        if not self.frozen:
            self.resolve_ended()                     # no frozen heap yet: judge the pre-freeze refs and drop the owed, as the idle tick does
            kind = self._run("initial", inserts, []) if inserts > 0 else None
        else:
            if self._foldins >= 2 * self.backstop_foldins:
                kind = self._run("backstop", inserts, [])
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
        BACKSTOP is blind (no owed refs) and unfreezes straight away. After the collect and the re-freeze, an owed ref still
        alive is kept by a LIVE ROOT, not a cycle: a wasted pause, counted as a survivor and dropped (never re-registered).
        The re-read runs after the re-freeze, which is harmless: a freeze collects nothing, so a ref alive after the collect
        is alive after the freeze too."""
        t0 = self._clock()
        reclaimed = False
        if kind == "release":
            self._gc.collect(); self.collections += 1    # cheap: the freeze stays in place, so this walks only the unfrozen
            if any(sref() is not None for sref, _ in owed):
                self._gc.unfreeze()                  # a survivor: the released cycle is in the frozen set, lift it and walk
                self._gc.collect(); self.collections += 1
                reclaimed = True
            # else the cheap collect took the released cycle whole: no full pause, counted as a load pass below
        elif kind == "backstop":
            self._gc.unfreeze()                      # blind periodic reclaim: nothing owed to re-read, walk everything
            self._gc.collect(); self.collections += 1
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
        if reclaimed:
            self.last_kind = kind
            self.reclaims += 1
            self._foldins = 0
        else:
            self.last_kind = "initial" if not was_frozen else "load"   # a cheap-collect release folds in like a load
            self.freezes += 1
            if was_frozen:
                self._foldins += 1                   # a load fold-in; the initial freeze is not one
        return self.last_kind

    def perf(self):
        """The /perf gc block's freeze sub-block: the state and the reconcile trade, so a reconcile collection is
        told apart from an organic one. `active` is whether a freeze is held now (named to not collide with the
        integer `gc.frozen`); `frozenCount` (gc.get_freeze_count) is added by the caller."""
        return {"enabled": self.enabled, "active": self.frozen, "loadTrees": self.load_trees,
                "backstopFoldins": self.backstop_foldins, "freezes": self.freezes, "reclaims": self.reclaims,
                "busyRuns": self.busy_runs,
                "collections": self.collections, "survivors": self.survivors, "lastReleaseSurvivors": self.last_release_survivors,
                "lastReleaseSids": list(self.last_release_sids),
                "endedPending": len(self._ended), "lastReconcileMs": round(self.last_ms, 1),
                "lastReconcileKind": self.last_kind, "totalReconcileMs": round(self.total_ms, 1)}


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
