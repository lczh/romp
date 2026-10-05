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
the cycle's budget) and takes the settle write's paths through a locked hand-off (`_CKPT_JUST_LOCK`)."""
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
HOUSEKEEPING = ("liftSpentAwaiting", "deathSweep", "endOnIdle", "deferralSweep", "autoNudge", "interruptBlock",
                "persistTickSeen", "persistIntrMarks", "persistSpendTrees", "autoPauseOnLimit", "usagePoll", "retryUpgrade", "autoPauseOnSpend",
                "spendGuard", "autoResumeRetry", "autoResumeSession", "autoRetry", "idleQueueDrive", "clearDoneNotes",
                "heldWorking", "persistCheckpoints")   # the checkpoint settle write closes the pass (2026-10-05)
QUIET = ("_lift_spent_awaiting", "_death_sweep_tick", "_end_on_idle_sweep", "_deferral_sweep_tick", "_interrupt_block_tick",
         "_persist_tick_seen", "_persist_intr_marks", "_persist_spend_trees", "_auto_pause_on_limit", "_usage_poll_tick",
         "_auto_pause_on_spend_limit", "_spend_guard_tick", "_auto_resume_retry", "_auto_resume_session_retry",
         "_auto_retry_tick", "_idle_queue_drive_tick", "_clear_done_working_notes", "_held_working_pass", "_turn_notify_tick", "_apply_pending_ops",
         "_persist_checkpoints", "_converge_checkpoints", "_kernel_sample_tick", "_api_health_push", "_artifacts_signal")


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
        self.saved_jobs = {nm: getattr(km, nm) for nm in QUIET}
        for nm in QUIET:
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
    notified at every change."""

    def __init__(self):
        self._lock = threading.Lock()
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
    clears in one step, and a write landing in between waits for the clear and is kept for the pass's next look. Synthetic
    sids and paths; every collaborator of the two functions is stubbed, so nothing is read or written."""

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
        stub(km, "_heal_cold_folds", lambda leaf: [])
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

    def test_a_path_the_jobs_pass_wrote_is_skipped_by_the_pushers_converge_pass(self):
        """The jobs pass on a thread of its own (the jobs thread's place), then the pusher's jobs on this one, every job of both
        lists quiet but the two checkpoint halves: the pass's settle write writes the leaf's document and hands the path over,
        and the pusher's converge pass, with that leaf its only candidate, writes nothing for it and empties the set; the
        look after has nothing to skip and writes the candidate. At the base the jobs pass wrote nothing."""
        km = self.km
        keep = {"_persist_checkpoints", "_converge_checkpoints"}
        quiet = {fn: (lambda *a, **k: None) for src in (km._jobs_pass, km._pusher_cycle_jobs)
                 for _job, fn in re.findall(JOB_FN_RE, inspect.getsource(src)) if fn not in keep}
        quiet.update(_api_health_frame=lambda *a, **k: None, _files_stat_pass_open=lambda live_map: False,
                     _files_stat_pass_close=lambda opened: None)
        self.assertGreater(len(quiet), 20, sorted(quiet))
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
        the clear drops it unseen, and the next look rewrites a document just written."""
        km = self.km
        lock = _WatchedLock()
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
