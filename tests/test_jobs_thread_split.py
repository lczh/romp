#!/usr/bin/env python3
"""The housekeeping jobs run on their own thread (2026-09-13). The pusher's cycle used to run every tick job after its push, on
one thread, so at boot the reminder walk and the interrupt tick (a cold read of every session's transcript: 24 s and 6 s on the
11:55 AM boot that decided this) held every browser frame behind a 30 to 60 s first cycle, and all day a slow job delayed the
next refresh. The user asked why the reminder walk had to finish before the UI showed at all. Now `_pusher_cycle_jobs` keeps what
feeds a frame or shares the cycle's checkpoint byte budget, `_jobs_pass` runs the rest in the order it always had on the jobs
thread (`_jobs_loop`, one pass per JOBS_PASS_S) with its own liveness snapshot, /perf keeps the two loops' splits apart (`pusher`
and `jobs`), and the boot-health row carries both firsts (`firstCycleS`, the browser's wait; `jobsFirstPassS`, the housekeeping's).
The checkpoint settle write (`_persist_checkpoints`) followed on 2026-10-05, as the jobs pass's last job: it feeds no frame and
charges no budget itself, and on the pusher every write it made delayed the next frame. The converge pass stays on the pusher (it spends
the cycle's budget) and takes the settle write's paths through a locked hand-off (`_CKPT_JUST_LOCK`); the settle write's first sight of
each session opens the pass (`_checkpoint_first_sight`), so a turn that settles while the pass runs is written at its end."""
import concurrent.futures
import contextlib
import inspect
import io
import json
import os
import re
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import kernel_module   # noqa: E402

JOB_NAME_RE = r"_job_stage\(['\"](\w+)['\"]"
JOB_FN_RE = r"_job_stage\('(\w+)', lambda: (_\w+)\("     # a job's name and the function its thunk calls

PUSHER_JOBS = ("beginCheckpointCycle", "sessionsListing", "applyPendingOps", "artifactsSignal", "turnNotify", "convergeCheckpoints",
               "bootRowBackstop", "kernelSample", "apiHealth")
HOUSEKEEPING = ("checkpointFirstSight", "liftSpentAwaiting", "deathSweep", "endOnIdle", "deferralSweep", "autoNudge", "interruptBlock",
                "persistTickSeen", "persistIntrMarks", "persistSpendTrees", "autoPauseOnLimit", "usagePoll", "retryUpgrade", "autoPauseOnSpend",
                "spendGuard", "autoResumeRetry", "autoResumeSession", "autoRetry", "idleQueueDrive", "clearDoneNotes",
                "heldWorking", "persistCheckpoints")   # the checkpoint settle write's first sight opens the pass, the write closes it
QUIET = ("_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep", "_deferral_sweep_tick", "_interrupt_block_tick",
         "_persist_tick_seen", "_persist_intr_marks", "_persist_spend_trees", "_auto_pause_on_limit", "_usage_poll_tick",
         "_auto_pause_on_spend_limit", "_spend_guard_tick", "_auto_resume_retry", "_auto_resume_session_retry",
         "_auto_retry_tick", "_idle_queue_drive_tick", "_clear_done_working_notes", "_held_working_pass", "_turn_notify_tick", "_apply_pending_ops",
         "_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints", "_kernel_sample_tick", "_api_health_push",
         "_api_health_frame", "_artifacts_signal")   # the frame too (the review of 2026-10-05): built for real, it builds the SDK
#                                                       backend, whose git status of the checkout carried the first cycle past the
#                                                       slow-job bounds below on a cold index


class Partition(unittest.TestCase):
    """Which job runs where, pinned by exact membership: a job added to either list must be placed on purpose."""

    def setUp(self):
        self.km = kernel_module()

    def test_the_two_lists_partition_the_jobs_census_in_the_old_order(self):
        km = self.km
        pusher = re.findall(JOB_NAME_RE, inspect.getsource(km._pusher_cycle_jobs))
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(km._jobs_pass))
        self.assertEqual(tuple(pusher), PUSHER_JOBS, "the pusher keeps what feeds a frame or shares the checkpoint budget")
        self.assertEqual(tuple(jobs), HOUSEKEEPING, "the housekeeping, in the order it always ran, the checkpoint settle write last")
        self.assertFalse(set(pusher) & set(jobs), "no job on both threads")
        self.assertEqual(set(pusher) | set(jobs), set(km._PerfStats.JOBS), "together they are the JOBS census")
        self.assertEqual([nm for nm in QUIET if not hasattr(km, nm)], [], "every name the lab cycles quiet is in this kernel")

    def test_the_jobs_pass_keeps_its_ordering_reasons(self):
        src = inspect.getsource(self.km._jobs_pass)
        order = [src.index(s) for s in ("_lift_spent_awaiting(now, live_map)", "_deferral_sweep_tick(now)",
                                        "_auto_nudge_tick(now, live_map)", "_interrupt_block_tick(now, live_map)",
                                        "_persist_tick_seen()", "_auto_pause_on_spend_limit(now, live_map)",
                                        "_spend_guard_tick(now, live_map)", "_auto_resume_retry(now, live_map)")]
        self.assertEqual(order, sorted(order), "lift before the walk before the interrupt tick before the memo persist; "
                                               "the deferral sweep before the walk; the pause before the guard before the resume")
        self.assertIn('_PERF_STATS.jobs.get("passes", 0) >= 1', src, "the guard skips the boot's first PASS, its own loop's first")
        self.assertNotIn("_push_all", src, "the jobs thread never builds a frame")
        self.assertIn('_PERF_STATS.stage("jobsPass"', src, "the pass is its own container stage")

    def test_main_starts_the_jobs_thread_and_the_docs_list_the_kind(self):
        km = self.km
        src = inspect.getsource(km.main)
        self.assertIn('threading.Thread(target=_jobs_loop, daemon=True, name="jobs").start()', src)
        self.assertLess(src.index("_JOBS_THREAD_STARTED[0] = True"), src.index("target=_jobs_loop"),
                        "the boot row learns of the thread before its first pass can report")
        doc = open(os.path.join(os.path.dirname(HERE), "docs", "reference.md"), encoding="utf-8").read()
        self.assertIn("`jobs` (the housekeeping loop split off the pusher)", doc, "the thread kind is documented")
        self.assertIn("`jobsFirstPassS`", doc, "the boot row's second first is documented")

    def test_the_two_persisted_memos_stage_under_a_per_thread_tmp_name(self):
        km = self.km
        for fn in (km._persist_tick_seen, km._persist_spend_trees):
            src = inspect.getsource(fn)
            self.assertIn('".tmp.%d.%x" % (os.getpid(), threading.get_ident())', src,
                          "%s: the exit's force write runs beside the jobs thread's; two writers under one tmp name unlink "
                          "each other's" % fn.__name__)


class _LabCycles(unittest.TestCase):
    """A hermetic kernel module, every job quiet unless a test speaks for it, the boot row captured, the two loops' first
    marks re-armed as a fresh boot's."""

    def setUp(self):
        self.km = km = kernel_module()
        self.saved = (km.NAMES, km._live_map, km._push_all, km._append_restart_cut, km._BOOT_HEALTH_DONE[0],
                      km._JOBS_THREAD_STARTED[0], dict(km._BOOT_FIRST), km._BOOT_FIRST_CLOSED_MONO[0])
        self.saved_jobs = {nm: getattr(km, nm) for nm in QUIET if hasattr(km, nm)}   # a name an older kernel lacks is skipped, so
        for nm in self.saved_jobs:                                                      #  the module runs over the base for its reds
            setattr(km, nm, lambda *a, **k: None)
        km.NAMES = {}
        km._live_map = lambda: {}
        km._push_all = lambda live_map=None: None
        self.rows = []
        km._append_restart_cut = lambda row: self.rows.append(row)
        km._BOOT_HEALTH_DONE[0] = False
        km._JOBS_THREAD_STARTED[0] = False
        km._BOOT_FIRST.update({"pusher": None, "jobs": None})
        km._BOOT_FIRST_CLOSED_MONO[0] = None
        self.saved_samplers = (dict(km._FIRST_CYCLE_SAMPLER), dict(km._FIRST_PASS_SAMPLER))
        for slot in (km._FIRST_CYCLE_SAMPLER, km._FIRST_PASS_SAMPLER):
            slot.update({"started": False, "stop": threading.Event(), "rows": [], "thread": None, "failed": 0})
        km._PERF_STATS.reset()

    def tearDown(self):
        km = self.km
        (km.NAMES, km._live_map, km._push_all, km._append_restart_cut, km._BOOT_HEALTH_DONE[0],
         km._JOBS_THREAD_STARTED[0], first, km._BOOT_FIRST_CLOSED_MONO[0]) = self.saved
        km._BOOT_FIRST.update(first)
        for nm, fn in self.saved_jobs.items():
            setattr(km, nm, fn)
        for slot, saved in zip((km._FIRST_CYCLE_SAMPLER, km._FIRST_PASS_SAMPLER), self.saved_samplers):
            slot.clear(); slot.update(saved)
        km._PERF_STATS.reset()


class TheBrowserNeverWaitsOnTheHousekeeping(_LabCycles):
    def test_a_slow_housekeeping_job_lengthens_the_jobs_pass_and_not_the_pusher_cycle(self):
        """The change itself: the reminder walk sleeping 0.4 s costs the jobs pass 0.4 s and the pusher's cycle nothing. At
        the base the walk ran inside the pusher's cycle, so the cycle carried the sleep."""
        km = self.km
        calls = []
        km._auto_nudge_tick = lambda now, live_map: (calls.append(1), time.sleep(0.4))
        self.addCleanup(setattr, km, "_auto_nudge_tick", self.saved_jobs.get("_auto_nudge_tick", km._auto_nudge_tick))
        t0 = time.monotonic(); km._pusher_cycle(); cycle = time.monotonic() - t0
        self.assertEqual(calls, [], "the pusher's cycle ran no walk")
        # a wall-clock bound: when it reds, the message names the stages that carried the time (the cycle's own split), so a
        # loaded runner's red says WHAT waited rather than only that something did (round three of PR 1951, 2026-09-21)
        stages = km._PERF_STATS.snapshot()["stages_ms"]
        carried = sorted(((ms, name) for name, ms in stages.items() if ms), reverse=True)[:6]
        self.assertLess(cycle, 0.3, "and did not carry its sleep: %.3f s; the stages that carried the time (ms): %r" % (cycle, carried))
        t0 = time.monotonic(); km._jobs_cycle(); pas = time.monotonic() - t0
        self.assertEqual(calls, [1], "the jobs pass ran it once")
        self.assertGreaterEqual(pas, 0.4)
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(snap["jobs"]["passes"], 1, "counted under /perf jobs, not pusher")
        self.assertEqual(snap["pusher"]["cycles"], 1)
        self.assertGreaterEqual(snap["stages_ms"]["jobs.autoNudge"], 400.0)
        self.assertLess(snap["pusher"]["cycle_ms_max"], 300.0, "the pusher's own ring never saw the sleep")

    def test_a_slow_checkpoint_persist_lengthens_the_jobs_pass_and_not_the_pusher_cycle(self):
        """The checkpoint settle write (2026-10-05): on the pusher's cycle each write it made held up the next frame. A persist
        sleeping 0.4 s now costs the jobs pass 0.4 s and the pusher's cycle nothing; its stage rides the jobs thread's ring
        and its run that thread's cpuByJob, while the converge pass, which spends the cycle's checkpoint byte budget, stays in
        the pusher's split. At the base the persist ran inside the pusher's cycle, which carried the sleep."""
        km = self.km
        calls = []

        def slow(now):
            calls.append(km._PERF_STATS._mine())                  # the owner kind whose split the call lands in
            time.sleep(0.4)
            return 0
        km._persist_checkpoints = slow                             # put back by tearDown (QUIET)
        t0 = time.monotonic(); km._pusher_cycle(); cycle = time.monotonic() - t0
        self.assertEqual(calls, [], "the pusher's cycle wrote no checkpoint")
        stages = km._PERF_STATS.snapshot()["stages_ms"]
        carried = sorted(((ms, name) for name, ms in stages.items() if ms), reverse=True)[:6]
        self.assertLess(cycle, 0.3, "and did not carry the write: %.3f s; the stages that carried the time (ms): %r" % (cycle, carried))
        t0 = time.monotonic(); km._jobs_cycle(); pas = time.monotonic() - t0
        self.assertEqual(calls, ["jobs"], "the jobs pass wrote them, once, on its own split")
        self.assertGreaterEqual(pas, 0.4)
        snap = km._PERF_STATS.snapshot()
        cycle_row = snap["pusher"]["stageRing"][-1]["stages"]
        pass_row = snap["jobs"]["stageRing"][-1]["stages"]
        self.assertNotIn("jobs.persistCheckpoints", cycle_row, sorted(cycle_row))
        self.assertIn("jobs.convergeCheckpoints", cycle_row, "the converge pass stays on the pusher: %r" % sorted(cycle_row))
        self.assertGreaterEqual(pass_row["jobs.persistCheckpoints"]["ms"], 400.0, sorted(pass_row))
        self.assertNotIn("jobs.convergeCheckpoints", pass_row)
        self.assertEqual(snap["jobs"]["cpuByJob"]["persistCheckpoints"]["runs"], 1, "one run of the jobs thread's own")
        self.assertLess(snap["pusher"]["cycle_ms_max"], 300.0, "the pusher's own ring never saw the write")

    def test_the_checkpoint_persist_closes_the_jobs_pass(self):
        """The persist is the pass's LAST job, after the needs-you jobs (the reminder walk, the interrupt tick, the held-working
        belt), so no decision of the pass waits on a write; it runs once per pass, inside the pass's scope (the discover memo
        _persist_checkpoints reads its sessions through) and on the jobs thread's split."""
        km = self.km
        order, seen = [], []
        real = km._job_stage

        def recording(name, thunk):
            order.append(name)
            return real(name, thunk)
        km._job_stage = recording
        self.addCleanup(setattr, km, "_job_stage", real)

        def persist(now):
            seen.append((isinstance(getattr(km._live_scope, "sessions", None), dict), km._PERF_STATS._mine()))
            return 0
        km._persist_checkpoints = persist                          # put back by tearDown (QUIET)
        km._jobs_cycle()
        self.assertEqual(order[-1], "persistCheckpoints", order)
        self.assertEqual(order.count("persistCheckpoints"), 1, order)
        for job in ("autoNudge", "interruptBlock", "heldWorking"):
            self.assertLess(order.index(job), order.index("persistCheckpoints"), job)
        self.assertEqual(seen, [(True, "jobs")], "inside the pass's scope, on the jobs thread's split")

    def test_the_jobs_pass_has_its_own_split_and_scope(self):
        km = self.km
        km._jobs_cycle()
        snap = km._PERF_STATS.snapshot()
        first = snap["jobs"]["firstPass"]
        self.assertIsNotNone(first, "the boot's first pass's split is kept under jobs.firstPass")
        self.assertIn("jobsPass", first["stages"], sorted(first["stages"]))
        self.assertIn("jobs.prelude", first["stages"])
        self.assertTrue(any(k.startswith("jobs.") and k != "jobs.prelude" for k in first["stages"]), "the jobs as sub-stages")
        self.assertIsNone(snap["pusher"]["firstCycle"], "the pusher's split is untouched by the jobs thread's pass")
        self.assertEqual(snap["jobs"]["stageRingLen"], 1)
        for k in ("pass_ms_p50", "pass_ms_p90", "pass_ms_ring_max", "passFailed", "splitFailed"):
            self.assertIn(k, snap["jobs"], k)
        self.assertIn("jobsPass", snap["stages_ms"]); self.assertIn("jobs.prelude", snap["stages_ms"])
        self.assertIsNone(km._live_scope.snapshot, "the pass closes its scope")
        self.assertIsNone(km._live_scope.names)

    def test_the_nudge_walk_names_its_parts_on_the_pass_split(self):
        """plans/nudge-walk-events.md, the measurement's first step: the walk's key stats, its snapshot reads and its looks are
        sub-stages of jobs.autoNudge on the pass's split, so a pass that spikes names what it paid; the parts sum to at most
        the job."""
        km = self.km
        # the pass body directly, under the job's own stage: a test earlier in this module leaves a pass in flight on a blocked
        # thread, and the tick's single-flight guard would stand this walk down
        now = int(time.time())
        km._job_stage("autoNudge", lambda: km._auto_nudge_pass(now, km._live_map(), False))
        st = km._PERF_STATS.snapshot()["stages_ms"]
        have = sorted(k for k in st if k.startswith("jobs.autoNudge"))
        self.assertEqual(have, ["jobs.autoNudge", "jobs.autoNudge.key", "jobs.autoNudge.looks", "jobs.autoNudge.snapshot"], have)
        parts = sum(st[k] for k in st if k.startswith("jobs.autoNudge."))
        self.assertLessEqual(parts, st["jobs.autoNudge"] + 1.0, "the parts sum to at most the job")

    def test_the_stats_keep_two_owners_apart(self):
        """A stage closed on the jobs thread lands in the jobs split and never in the pusher's, and the other way round, while
        the cumulative totals take both."""
        km = self.km
        ps = km._PerfStats()
        ps.cycle_begin()                                              # this thread is the pusher
        ps.stage("push.chat", 0.010); ps.stage("push", 0.010)
        done = threading.Event()
        def jobs_thread():
            ps.cycle_begin("jobs")
            ps.stage("jobs.autoNudge", 0.020); ps.stage("jobsPass", 0.020)
            ps.jobs_pass(0.021)
            done.set()
        th = threading.Thread(target=jobs_thread); th.start(); th.join(5)
        self.assertTrue(done.is_set())
        ps.stage("jobs.apiHealth", 0.001); ps.stage("jobs", 0.001)
        ps.cycle(0.012)
        snap = ps.snapshot()
        self.assertEqual(sorted(snap["pusher"]["firstCycle"]["stages"]), ["jobs", "jobs.apiHealth", "push", "push.chat"])
        self.assertEqual(sorted(snap["jobs"]["firstPass"]["stages"]), ["jobs.autoNudge", "jobsPass"])
        self.assertAlmostEqual(snap["jobs"]["firstPass"]["stages"]["jobsPass"]["ms"], 20.0)
        self.assertAlmostEqual(snap["stages_ms"]["jobs.autoNudge"], 20.0, "the totals take every thread's stages")
        self.assertEqual(ps._mine(), "pusher")
        self.assertEqual(snap["jobs"]["passes"], 1)
        self.assertEqual(snap["pusher"]["cycles"], 1)


NOW = 1_800_000_000


class _WatchedLock:
    """A lock that names its holder and its waiters, so a probe can tell a thread blocked on it from one that finished without
    a clock: `owner` is the holding thread's ident (None when free), `waiting` the idents blocked in acquire, and `cond` is
    notified at every change. It wraps `inner`, the lock that does the excluding (the kernel's own when the probe hands it
    one; a fresh one otherwise)."""

    def __init__(self, inner=None):
        self._lock = inner if inner is not None else threading.Lock()
        self.cond = threading.Condition()
        self.owner = None
        self.waiting = set()

    def acquire(self, blocking=True, timeout=-1):
        me = threading.get_ident()
        with self.cond:
            self.waiting.add(me); self.cond.notify_all()
        got = self._lock.acquire(blocking, timeout)
        with self.cond:
            self.waiting.discard(me)
            if got:
                self.owner = me
            self.cond.notify_all()
        return got

    def release(self):
        with self.cond:
            self.owner = None; self.cond.notify_all()
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class TheSettleWriteHandsItsPathsToTheConvergePass(unittest.TestCase):
    """The hand-off across the two threads (2026-10-05). The settle write (_persist_checkpoints, the jobs pass's last job)
    records the paths it wrote in _CKPT_JUST_WRITTEN; the converge pass (the pusher's, against the cycle's byte budget) skips
    them and empties the set. The two now run at once, so the add and the take share _CKPT_JUST_LOCK: the take copies and
    clears in one step, and a write landing in between waits for the clear and is kept for the pass's next look. Beside it,
    what the move put on the jobs pass: the settle write's first sight at the pass's start (the settle key and the leaf's
    stat, each taken once per session), and the fault lines of first sight and of each half.
    Synthetic sids and paths; every collaborator of the two functions is stubbed, so nothing is read or written."""

    SID_A = "11111111-2222-3333-4444-555555555501"
    SID_B = "11111111-2222-3333-4444-555555555502"
    LEAF_A = "/synthetic/web/leaf.jsonl"
    LEAF_B = "/synthetic/api/leaf.jsonl"

    def setUp(self):
        self.km = km = kernel_module()
        km._PERF_STATS.reset()                               # the jobs pass below registers its probe thread as the jobs owner
        self.addCleanup(km._PERF_STATS.reset)
        self.sessions, self.cands = [], []
        self.persisted, self.writes = [], []                # the settle write's documents; the converge pass's own
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)

        def stub(target, name, new):
            stack.enter_context(mock.patch.object(target, name, new))
        # the settle write's world: the sessions a test names, each with a turn-end key moved since its last write (2 now)
        stub(km, "_sessions", lambda now, *a, **k: list(self.sessions))
        stub(km, "_turn_end_key", lambda sid, reg=None: 2)
        stub(km, "_stat_key", lambda path: ("synthetic", 1))
        stub(km, "_heal_cold_folds", lambda leaf, dropped_out=None: [])
        stub(km, "_prime_leaf_folds", lambda leaf: True)
        stub(km, "_session_fold_files", lambda sid, leaf: {str(leaf)})
        stub(km.em, "checkpoint_dirty", lambda: [s["path"] for s in self.sessions])
        stub(km.em, "drop_cold_cursors", lambda path: [])
        stub(km.em, "checkpoint_write_dirty_paths", lambda paths=None, budget_s=None: (self.persisted.extend(paths), list(paths))[1])
        stub(km.em, "asm_checkpoint_write", lambda *a, **k: False)
        stub(km, "_display_sdk_human", lambda sid: False)
        stub(km, "_stored_tree", lambda leaf, sid: None)
        stub(km, "_asm_refresh_maybe", lambda leaf, sid: False)
        stack.enter_context(mock.patch.dict(km._CKPT_SETTLE_SEEN, {}, clear=True))
        stack.enter_context(mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True))
        # the converge pass's world: work to do, the candidates a test names, a document write that is recorded
        stub(km, "CKPT_CONVERGE_MS", 150)
        stub(km.em, "checkpoint_has_work", lambda: True)
        stub(km.em, "checkpoint_converge_candidates", lambda: list(self.cands))
        stub(km, "_converge_skipped", lambda path: False)
        stub(km, "_converge_skip", lambda path: None)
        stub(km, "_converge_assembly", lambda now, t0: 0)
        stub(km, "_read_bytes_of", lambda path: 0)
        stub(km.em, "converge_stat", lambda *a, **k: None)
        stub(km.em, "file_quiescent", lambda path: False)
        stub(km.em, "cold_fold_reasons", lambda path: {})
        stub(km.em, "hold_quiescent_drops", contextlib.nullcontext)
        stub(km.em, "entry_whole_resident", lambda path: False)
        stub(km.em, "pay_held_drops", lambda: {})
        stub(km.em, "checkpoint_cycle_room", lambda n: True)
        stub(km.em, "checkpoint_cycle_charge", lambda n: True)
        stub(km.em, "_ckpt_file", lambda path: Path("/synthetic/no-such-document.json"))
        stub(km.em, "checkpoint_write", lambda path, force=False: self.writes.append(path) or True)
        stub(km, "_CKPT_JUST_WRITTEN", set())

    def _quiet(self, keep):
        """Every job of both lists quiet but the functions named in `keep` (read off the two functions' own sources, so a job
        added to either is quiet here too), with the API health frame and the pass's shared stat snapshot beside them."""
        km = self.km
        quiet = {fn: (lambda *a, **k: None) for src in (km._jobs_pass, km._pusher_cycle_jobs)
                 for _job, fn in re.findall(JOB_FN_RE, inspect.getsource(src)) if fn not in keep}
        quiet.update(_api_health_frame=lambda *a, **k: None, _files_stat_pass_open=lambda live_map: False,
                     _files_stat_pass_close=lambda opened: None)
        self.assertGreater(len(quiet), 20, sorted(quiet))
        return quiet

    def _cycles_and_passes(self, patches, between=None, n=3):
        """The pusher's cycle, then the jobs thread's pass, `n` times, one thread each as in the kernel, under `patches` (the
        quiet jobs and the world a test sets); `between(i)` runs before the i-th cycle, where the world moves between passes."""
        km = self.km
        with mock.patch.multiple(km, **patches), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jobs-probe") as jobs:
            for i in range(n):
                if between is not None:
                    between(i)
                km._pusher_cycle_jobs(NOW + i, {}, False)
                jobs.submit(km._jobs_pass, NOW + i, {}).result(timeout=30)

    def test_a_path_the_jobs_pass_wrote_is_skipped_by_the_pushers_converge_pass(self):
        """The jobs pass on a thread of its own (the jobs thread's place), then the pusher's jobs on this one, every job of both
        lists quiet but the two checkpoint halves: the pass's settle write writes the leaf's document and hands the path over,
        and the pusher's converge pass, with that leaf its only candidate, writes nothing for it and empties the set; the
        look after has nothing to skip and writes the candidate. At the base the jobs pass wrote nothing."""
        km = self.km
        quiet = self._quiet({"_persist_checkpoints", "_converge_checkpoints"})
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}]
        km._CKPT_SETTLE_SEEN[self.SID_A] = (1, ("synthetic", 1))   # the last write saw turn-end key 1; it is 2 now: a settle
        errors = []

        def jobs_pass():
            try:
                km._jobs_pass(NOW, {})
            except BaseException as e:                              # carried to the test's thread
                errors.append(e)
        with mock.patch.multiple(km, **quiet):
            th = threading.Thread(target=jobs_pass, name="jobs-probe")
            th.start(); th.join(10)
            self.assertFalse(th.is_alive())
            self.assertEqual(errors, [])
            self.assertEqual(self.persisted, [self.LEAF_A], "the jobs pass's settle write wrote the leaf's document")
            self.assertEqual(km._CKPT_JUST_WRITTEN, {self.LEAF_A}, "and handed the path over")
            self.cands = [self.LEAF_A]
            km._pusher_cycle_jobs(NOW, {}, False)
            self.assertEqual(self.writes, [], "the pusher's converge pass skipped the path the settle had just written")
            self.assertEqual(km._CKPT_JUST_WRITTEN, set(), "and emptied the set at its look")
            km._pusher_cycle_jobs(NOW + 1, {}, False)
        self.assertEqual(self.writes, [self.LEAF_A], "the next look has nothing to skip: the candidate is written")
        self.assertEqual(self.persisted, [self.LEAF_A], "and the pusher's cycles wrote no settle document of their own")

    def test_a_settle_write_landing_while_the_pass_takes_the_set_is_kept_for_its_next_look(self):
        """The race the lock closes. The take is a copy and a clear, and the settle write's add is made to land between them: a
        hook on the clear starts the settle write on another thread and waits until that thread has finished or is blocked on
        the lock this thread holds (the watched lock tells the two apart, no clock). With both sides under _CKPT_JUST_LOCK the
        add waits for the clear, so its path is in the set after the pass and the next look skips it. With either side
        unlocked (the base had no lock: `create=True` lets the probe stand in for one nobody takes) the add lands first and
        the clear drops it unseen, and the next look rewrites a document just written. The watcher wraps the kernel's SHIPPED
        lock, so the exclusion under test is that lock's (a no-op context there fails on its missing acquire); the base, which
        had none, gets a fresh one that only the probe takes, and keeps its red."""
        km = self.km
        lock = _WatchedLock(getattr(km, "_CKPT_JUST_LOCK", None))
        me = threading.get_ident()
        state = {"hooked": 0, "thread": None, "done": False, "ready": None, "error": None}

        def settle():
            try:
                km._persist_checkpoints(NOW)
            except BaseException as e:                              # carried to the test's thread
                state["error"] = e
            finally:
                with lock.cond:
                    state["done"] = True; lock.cond.notify_all()

        class TakeProbe(set):
            def clear(probe):
                state["hooked"] += 1
                if state["hooked"] == 1:
                    th = state["thread"] = threading.Thread(target=settle, name="jobs-probe")
                    th.start()
                    with lock.cond:
                        state["ready"] = lock.cond.wait_for(
                            lambda: state["done"] or (th.ident in lock.waiting and lock.owner == me), timeout=30)
                super().clear()

        self.sessions = [{"sid": self.SID_B, "path": self.LEAF_B}]
        km._CKPT_SETTLE_SEEN[self.SID_B] = (1, ("synthetic", 1))
        with mock.patch.object(km, "_CKPT_JUST_WRITTEN", TakeProbe({self.LEAF_A})), mock.patch.object(km, "_CKPT_JUST_LOCK", lock, create=True):
            self.cands = [self.LEAF_A]
            self.assertEqual(km._converge_checkpoints(NOW), 0)
            self.assertGreaterEqual(state["hooked"], 1, "the take clears the set in place (a take that swaps the object needs a new probe)")
            state["thread"].join(10)
            self.assertFalse(state["thread"].is_alive(), "the settle write finished")
            self.assertIsNone(state["error"])
            self.assertTrue(state["ready"], "the settle write neither finished nor waited on the lock")
            self.assertEqual(self.writes, [], "the pass skipped the path in the set when it looked")
            self.assertEqual(self.persisted, [self.LEAF_B])
            self.assertEqual(set(km._CKPT_JUST_WRITTEN), {self.LEAF_B},
                             "the path written during the take is kept for the next look, not cleared unseen")
            self.cands = [self.LEAF_B]
            self.assertEqual(km._converge_checkpoints(NOW + 1), 0)
            self.assertEqual(self.writes, [], "the next look skipped it: no second write of a document just written")
            self.assertEqual(set(km._CKPT_JUST_WRITTEN), set())

    def test_the_converge_pass_takes_the_set_on_its_off_and_its_no_work_roads(self):
        """The take runs on every road of the converge pass, as each road cleared the set before the hand-off was locked (the
        review of 2026-10-05): a path the settle write handed over is taken with the pass off (ROMP_CKPT_CONVERGE_MS=0) and
        with nothing to converge. A take moved into the has-work branch would keep every settle path in the set for the
        process's life with the pass off, and skip a path once at a later has-work look however it was dirtied since; an off
        road returning before the take would do the first."""
        km = self.km
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}]
        roads = (("off", mock.patch.object(km, "CKPT_CONVERGE_MS", 0)),
                 ("no work", mock.patch.object(km.em, "checkpoint_has_work", lambda: False)))
        for road, patch in roads:
            with self.subTest(road=road):
                km._CKPT_SETTLE_SEEN[self.SID_A] = (1, ("synthetic", 1))   # the last write saw turn-end key 1; it is 2 now: a settle
                km._persist_checkpoints(NOW)
                self.assertEqual(km._CKPT_JUST_WRITTEN, {self.LEAF_A}, "the settle write handed the path over")
                with patch:
                    self.assertEqual(km._converge_checkpoints(NOW), 0)
                self.assertEqual(km._CKPT_JUST_WRITTEN, set(), "the %s road took the set" % road)
        self.assertEqual(self.writes, [], "and wrote nothing of its own")

    def test_a_turn_that_settles_during_the_boots_first_pass_is_written_at_that_passs_end(self):
        """The boot's first sight (the review of 2026-10-05). The settle write records a session it has not seen as first sight
        and writes nothing. Here the boot's first jobs pass runs beside the pusher's cycles, one thread each as in the kernel,
        and one session's turn settles during the pass's reminder walk (its turn-end key moves from 1 to 2); the other's
        stands. First sight is taken at the pass's START (_checkpoint_first_sight), so the settle write at its end sees the
        move and writes that leaf's document, once, and the other session's not at all (a first sight read in another shape
        than the write's would make every session due at the pass's end). When the write took first sight itself, at the
        pass's end, the move fell before it and the leaf was never written over three cycles and passes; when the write ran
        on the pusher, its first cycle took first sight before the move and its second wrote."""
        km = self.km
        quiet = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        turn = {"key": 1}

        def walk(now, live_map):
            turn["key"] = 2                                         # web's turn settles while the first pass runs
        quiet["_auto_nudge_tick"] = walk
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        key = lambda sid, reg=None: turn["key"] if sid == self.SID_A else 1   # noqa: E731  (api's turn does not move)
        with mock.patch.object(km, "_turn_end_key", key), mock.patch.multiple(km, **quiet), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jobs-probe") as jobs:
            for i in range(3):                                      # the pusher's cycle, then the jobs thread's pass, three times
                km._pusher_cycle_jobs(NOW + i, {}, False)
                jobs.submit(km._jobs_pass, NOW + i, {}).result(timeout=30)
        self.assertEqual(turn["key"], 2, "the walk ran: the turn settled during the first pass")
        self.assertEqual(self.persisted, [self.LEAF_A], "the settle during the boot's first pass was written, once, and only it")
        self.assertEqual(km._CKPT_SETTLE_SEEN[self.SID_A][0], 2, "and its key recorded as written")

    def test_a_leaf_that_grows_during_the_boots_first_pass_is_written_at_that_passs_end(self):
        """First sight's other half (the review's round two): the leaf's stat, the periodic trigger's evidence, is taken at the
        pass's START beside the settle key. No turn ends here and the states log stands (the settle key is fixed), the period
        is 0, and web's leaf grows during the first pass's reminder walk; api's stands. The settle write at the pass's end
        sees web's leaf moved since its first sight and writes that document, once, and api's not at all. With the leaf's
        first sight left to the write, at the pass's end, the growth fell before it and the leaf waited for its next move."""
        km = self.km
        patches = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        leaf = {"size": 1}

        def walk(now, live_map):
            leaf["size"] = 2                                        # web's leaf grows while the first pass runs
        patches.update(_auto_nudge_tick=walk, CKPT_PERIOD_S=0,
                       _stat_key=lambda path: ("synthetic", leaf["size"] if str(path) == self.LEAF_A else 1))
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        self._cycles_and_passes(patches)
        self.assertEqual(leaf["size"], 2, "the walk ran: the leaf grew during the first pass")
        self.assertEqual(self.persisted, [self.LEAF_A], "the growth during the boot's first pass was written, once, and only it")
        self.assertEqual(km._CKPT_PERIODIC_SEEN[self.SID_A][0], ("synthetic", 2), "and the leaf's stat recorded as written")

    def test_a_turn_that_settles_between_two_passes_is_written_at_the_next_passs_end(self):
        """First sight records only a session the table does not hold. Web's turn-end key moves from 1 to 2 BETWEEN the first
        pass and the second (before the pusher's second cycle); api's stands. The second pass's start leaves web's recorded
        key alone, so the settle write at its end sees the move and writes web's document, once. A first sight retaken at
        every pass's start would record 2 there and the write would find nothing new: a turn that settled between one pass's
        write and the next pass's start would get no settle write, for any session."""
        km = self.km
        patches = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        turn = {"key": 1}

        def between(i):
            if i == 1:
                turn["key"] = 2                                     # web's turn settles after the first pass's write
        patches["_turn_end_key"] = lambda sid, reg=None: turn["key"] if sid == self.SID_A else 1
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        self._cycles_and_passes(patches, between)
        self.assertEqual(self.persisted, [self.LEAF_A], "the settle between the passes was written, once, and only it")
        self.assertEqual(km._CKPT_SETTLE_SEEN[self.SID_A][0], 2, "and its key recorded as written")

    def test_a_leaf_that_grows_between_two_passes_is_written_at_the_next_passs_end(self):
        """The same for the leaf's stat: the settle key fixed and the period at 0, web's leaf grows BETWEEN the first pass and
        the second; api's stands. The second pass's start leaves web's recorded stat alone, so the settle write at its end
        sees the leaf moved and writes web's document, once. A leaf stat retaken at every pass's start would record the
        growth there with a fresh stamp and the write would find the leaf unmoved; and since the stamp would reset each
        pass, the periodic trigger could never fire within a pass shorter than the period."""
        km = self.km
        patches = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        leaf = {"size": 1}

        def between(i):
            if i == 1:
                leaf["size"] = 2                                    # web's leaf grows after the first pass's write
        patches.update(CKPT_PERIOD_S=0, _stat_key=lambda path: ("synthetic", leaf["size"] if str(path) == self.LEAF_A else 1))
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        self._cycles_and_passes(patches, between)
        self.assertEqual(self.persisted, [self.LEAF_A], "the growth between the passes was written, once, and only it")
        self.assertEqual(km._CKPT_PERIODIC_SEEN[self.SID_A][0], ("synthetic", 2), "and the leaf's stat recorded as written")

    def test_a_settle_write_that_raises_is_caught_and_named_and_the_pass_still_closes(self):
        """A raise from the settle write through the REAL jobs pass, every other job quiet: nothing escapes the pass (an escape
        is counted under jobs.passFailed, backed off, and skips the files-stat close and the jobsPass stage), stderr names the
        half that raised, and the close and the stage still run. Before the move the jobs pass ran no settle write."""
        km = self.km
        quiet = self._quiet(set())
        closed = []

        def boom(now):
            raise RuntimeError("synthetic settle-write fault")
        quiet.update(_persist_checkpoints=boom, _files_stat_pass_close=lambda opened: closed.append(opened))
        err = io.StringIO()
        with mock.patch.multiple(km, **quiet), mock.patch.object(km._PERF_STATS, "stage", wraps=km._PERF_STATS.stage) as stage, \
                mock.patch.object(km.sys, "stderr", err), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jobs-probe") as jobs:
            jobs.submit(km._jobs_pass, NOW, {}).result(timeout=30)   # re-raises here if the fault escaped the pass
        self.assertIn("checkpoint persist: ", err.getvalue(), err.getvalue()[-400:])
        self.assertIn("RuntimeError: synthetic settle-write fault", err.getvalue())
        self.assertNotIn("checkpoint converge: ", err.getvalue(), "the line names its own half")
        self.assertEqual(closed, [False], "the files-stat close ran")
        self.assertIn("jobsPass", [c.args[0] for c in stage.call_args_list], "and the pass closed its stage")

    def test_a_first_sight_that_raises_is_caught_and_named_and_the_jobs_after_it_run(self):
        """A raise from the settle write's first sight through the REAL jobs pass, every other job quiet: nothing escapes the
        pass, stderr names first sight and neither checkpoint half, and the jobs after it still run, the reminder walk, the
        interrupt tick, the held-working check and the settle write among them. First sight opens the pass, so a raise that
        escaped it would end every pass there while its cause lasted, each counted under jobs.passFailed and backed off."""
        km = self.km
        quiet = self._quiet(set())
        ran, closed = [], []

        def boom(now):
            raise RuntimeError("synthetic first-sight fault")

        def job(name):
            return lambda *a, **k: ran.append(name)
        quiet.update(_checkpoint_first_sight=boom, _lift_spent_awaiting=job("liftSpentAwaiting"), _auto_nudge_tick=job("autoNudge"),
                     _interrupt_block_tick=job("interruptBlock"), _held_working_pass=job("heldWorking"),
                     _persist_checkpoints=job("persistCheckpoints"), _files_stat_pass_close=lambda opened: closed.append(opened))
        err = io.StringIO()
        with mock.patch.multiple(km, **quiet), mock.patch.object(km.sys, "stderr", err), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jobs-probe") as jobs:
            jobs.submit(km._jobs_pass, NOW, {}).result(timeout=30)   # re-raises here if the fault escaped the pass
        self.assertIn("checkpoint first sight: ", err.getvalue(), err.getvalue()[-400:])
        self.assertIn("RuntimeError: synthetic first-sight fault", err.getvalue())
        self.assertNotIn("checkpoint persist: ", err.getvalue(), "the line names its own job")
        self.assertNotIn("checkpoint converge: ", err.getvalue(), "the line names its own job")
        self.assertEqual(ran, ["liftSpentAwaiting", "autoNudge", "interruptBlock", "heldWorking", "persistCheckpoints"],
                         "the jobs after it ran, the settle write last")
        self.assertEqual(closed, [False], "and the files-stat close")

    def test_a_converge_pass_that_raises_is_caught_and_named_and_the_jobs_after_it_run(self):
        """A raise from the converge pass through the REAL pusher jobs, every other job quiet: nothing escapes the cycle's jobs,
        stderr names the half that raised, and the boot row backstop, the kernel sample and the API health frame after it
        still run. Before the move both halves shared one catch and one line."""
        km = self.km
        quiet = self._quiet(set())
        ran = []

        def boom(now):
            raise RuntimeError("synthetic converge fault")
        quiet.update(_converge_checkpoints=boom, _boot_row_backstop=lambda now: ran.append("bootRowBackstop"),
                     _kernel_sample_tick=lambda now: ran.append("kernelSample"), _api_health_push=lambda frame: ran.append("apiHealth"))
        err = io.StringIO()
        with mock.patch.multiple(km, **quiet), mock.patch.object(km.sys, "stderr", err):
            km._pusher_cycle_jobs(NOW, {}, False)                   # raises here if the fault escaped the cycle's jobs
        self.assertIn("checkpoint converge: ", err.getvalue(), err.getvalue()[-400:])
        self.assertIn("RuntimeError: synthetic converge fault", err.getvalue())
        self.assertNotIn("checkpoint persist: ", err.getvalue(), "the line names its own half")
        self.assertEqual(ran, ["bootRowBackstop", "kernelSample", "apiHealth"], "the jobs after it still ran")


class TheBootRowCarriesBothFirsts(_LabCycles):
    def test_with_the_jobs_thread_started_the_row_waits_for_the_later_first(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._pusher_cycle()
        self.assertEqual(self.rows, [], "the pusher's first cycle alone writes no row: the jobs pass is still open")
        self.assertFalse(km._BOOT_HEALTH_DONE[0])
        km._auto_nudge_tick = lambda now, live_map: time.sleep(0.05)
        self.addCleanup(setattr, km, "_auto_nudge_tick", self.saved_jobs.get("_auto_nudge_tick", km._auto_nudge_tick))
        km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "the jobs thread's first pass, the later of the two, wrote it")
        row = self.rows[0]
        self.assertTrue(row["bootHealth"])
        self.assertIn("firstCycleS", row); self.assertIn("slow", row)
        self.assertIn("jobsFirstPassS", row); self.assertIn("jobsSlow", row)
        self.assertGreaterEqual(row["jobsFirstPassS"], 0.05)
        self.assertIn("prelude", row["stages"], "the pusher's split rides the row")
        self.assertIn("jobsPass", row["stages"], "and the jobs thread's")
        self.assertIn("jobs.autoNudge", row["stages"])
        self.assertIsInstance(row["firstCycleStacks"], list)
        self.assertIsInstance(row["firstPassStacks"], list, "the jobs thread's first pass was sampled too")
        self.assertNotIn("jobsFirstPassPending", row)
        self.assertTrue(km._BOOT_HEALTH_DONE[0])
        self.assertEqual(km._BOOT_FIRST, {"pusher": None, "jobs": None}, "the firsts are cleared once the row is written")
        km._pusher_cycle(); km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "later cycles and passes write nothing")

    def test_whichever_loop_finishes_its_first_last_writes_the_row(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._jobs_cycle()
        self.assertEqual(self.rows, [], "the jobs pass first: the row waits for the pusher")
        km._pusher_cycle()
        self.assertEqual(len(self.rows), 1)
        self.assertIn("jobsFirstPassS", self.rows[0]); self.assertIn("firstCycleS", self.rows[0])

    def test_without_a_jobs_thread_the_pushers_first_cycle_writes_the_row_at_once(self):
        """A test driving one cycle, or a kernel whose main never started the thread: the old contract holds."""
        km = self.km
        km._pusher_cycle()
        self.assertEqual(len(self.rows), 1)
        self.assertIn("firstCycleS", self.rows[0])
        self.assertNotIn("jobsFirstPassS", self.rows[0])
        self.assertNotIn("firstPassStacks", self.rows[0], "no jobs thread, no second sample list")
        self.assertIsNone(km._boot_health_first_cycle(0.1), "the second cycle is not the boot's first")

    def test_a_jobs_pass_that_never_ends_still_leaves_a_row_by_the_backstop(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._pusher_cycle()
        self.assertEqual(self.rows, [])
        self.assertIsNone(km._boot_health_row_backstop(time.monotonic()), "well inside the bound: nothing yet")
        with mock.patch.object(km, "BOOT_JOBS_PASS_ROW_BACKSTOP_S", 0.0):
            km._pusher_cycle()                                        # a later cycle runs the backstop
        self.assertEqual(len(self.rows), 1, "the row without the jobs pass")
        row = self.rows[0]
        self.assertTrue(row.get("jobsFirstPassPending"))
        self.assertIn("firstCycleS", row); self.assertNotIn("jobsFirstPassS", row)
        self.assertTrue(km._BOOT_HEALTH_DONE[0])
        km._jobs_cycle()
        self.assertEqual(len(self.rows), 1, "the late pass writes no second row")

    def test_the_slow_lines_name_their_thread(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        err = io.StringIO()
        with mock.patch.object(km, "BOOT_FIRST_CYCLE_BOUND_S", 0.0), mock.patch.object(km.sys, "stderr", err):
            km._pusher_cycle(); km._jobs_cycle()
        self.assertIn("the first pusher cycle took", err.getvalue())
        self.assertIn("the first housekeeping pass took", err.getvalue())
        self.assertIn("the browser did not wait on it", err.getvalue())


class TheBootRowCarriesEachSplitsGcDelta(_LabCycles):
    """The row's `gc`: each first split's collector delta on its own, `firstCycle` and `firstPass`, never summed (the tallies
    are process-wide, so a collection inside both windows is in both deltas and a sum would count it twice), absent when
    neither split has one (review round)."""

    def _row_with(self, cyc, pas):
        km = self.km
        km._BOOT_FIRST.update({"pusher": 0.5, "jobs": 0.7})
        with mock.patch.object(km._PERF_STATS, "first_cycle_split", return_value=cyc), \
             mock.patch.object(km._PERF_STATS, "first_pass_split", return_value=pas):
            row = km._boot_health_row()
        self.assertEqual(self.rows, [row])
        return row

    def test_both_deltas_ride_the_row_unsummed(self):
        cyc_gc = {"n0": 3, "n1": 1, "n2": 1, "ms2": 12.5}            # one full collection inside the pusher's window...
        pas_gc = {"n0": 5, "n1": 1, "n2": 1, "ms2": 12.5}            # ...and inside the jobs pass's, which overlapped it
        cyc = {"s": 0.5, "t": 1.0, "gc": cyc_gc,
               "stages": {"push": {"ms": 400.0, "bytes": 0, "hydrated": 0}, "jobs.other": {"ms": 10.0, "bytes": 0, "hydrated": 0}}}
        pas = {"s": 0.7, "t": 1.2, "gc": pas_gc,
               "stages": {"jobsPass": {"ms": 700.0, "bytes": 0, "hydrated": 0}, "jobs.other": {"ms": 5.0, "bytes": 0, "hydrated": 0}}}
        row = self._row_with(cyc, pas)
        self.assertEqual(row["gc"], {"firstCycle": cyc_gc, "firstPass": pas_gc})
        self.assertEqual(row["gc"]["firstCycle"]["n2"] + row["gc"]["firstPass"]["n2"], 2,
                         "the same collection in both windows: a sum would have said two")
        self.assertEqual(row["stages"]["jobs.other"]["ms"], 15.0, "a stage key both own is still summed; the gc deltas are not")
        json.dumps(row)

    def test_a_split_without_a_delta_reads_null_beside_the_others(self):
        cyc = {"s": 0.5, "t": 1.0, "stages": {}, "gc": None}          # closed without an opening mark
        pas = {"s": 0.7, "t": 1.2, "stages": {}, "gc": {"n0": 2, "n1": 0, "n2": 0, "ms2": 0.0}}
        row = self._row_with(cyc, pas)
        self.assertEqual(row["gc"], {"firstCycle": None, "firstPass": {"n0": 2, "n1": 0, "n2": 0, "ms2": 0.0}})

    def test_no_delta_in_either_split_leaves_the_key_off(self):
        row = self._row_with({"s": 0.5, "t": 1.0, "stages": {}, "gc": None}, None)   # and no jobs pass at all
        self.assertNotIn("gc", row)
        self.assertNotIn("stages", row)

    def test_a_driven_boot_carries_the_deltas_from_the_real_splits(self):
        km = self.km
        km._JOBS_THREAD_STARTED[0] = True
        km._pusher_cycle(); km._jobs_cycle()
        row = self.rows[0]
        self.assertEqual(set(row["gc"]), {"firstCycle", "firstPass"})
        for k in ("firstCycle", "firstPass"):
            self.assertEqual(set(row["gc"][k]), {"n0", "n1", "n2", "ms2"}, k)


class TheJobsLoop(_LabCycles):
    def test_a_pass_that_raises_is_counted_and_the_loop_goes_on(self):
        km = self.km
        n = [0]
        def cycle():
            n[0] += 1
            if n[0] <= 2:
                raise RuntimeError("no thread slot")
            km._LOOPS_STOP.set()
        err = io.StringIO()
        saved_stop = km._LOOPS_STOP.is_set()
        with mock.patch.object(km, "_jobs_cycle", cycle), mock.patch.object(km, "_JOBS_FAILED_SAID", {}), \
             mock.patch.object(km.sys, "stderr", err), mock.patch.dict(km._PERF_STATS.jobs, {"passFailed": 0}), \
             mock.patch.object(km, "PUSHER_FAIL_BACKOFF_S", (0.01, 0.01, 0.01, 0.01)):
            try:
                km._jobs_loop()
                self.assertEqual(n[0], 3, "two raising passes were skipped and the third ran")
                self.assertEqual(km._PERF_STATS.jobs["passFailed"], 2)
            finally:
                if not saved_stop:
                    km._LOOPS_STOP.clear()
        self.assertEqual(err.getvalue().count("jobs: a pass raised RuntimeError"), 1, "said once per kind: %r" % err.getvalue())
        self.assertIn("passFailed", km._PERF_STATS.snapshot()["jobs"])

    def test_the_loop_paces_at_the_pushers_backstop_and_stops_with_the_loops(self):
        km = self.km
        self.assertEqual(km.JOBS_PASS_S, 0.5)
        n = [0]
        def cycle():
            n[0] += 1
            if n[0] == 2:
                km._LOOPS_STOP.set()
        saved_stop = km._LOOPS_STOP.is_set(); km._LOOPS_STOP.clear()
        t0 = time.monotonic()
        try:
            with mock.patch.object(km, "_jobs_cycle", cycle), mock.patch.object(km, "JOBS_PASS_S", 0.05):
                km._jobs_loop()
        finally:
            if saved_stop: km._LOOPS_STOP.set()
            else: km._LOOPS_STOP.clear()
        self.assertEqual(n[0], 2)
        self.assertGreaterEqual(time.monotonic() - t0, 0.05, "one pace between the two passes")


if __name__ == "__main__":
    unittest.main()
