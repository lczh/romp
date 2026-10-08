#!/usr/bin/env python3
"""The housekeeping jobs run on their own thread (2026-09-13). The pusher's cycle used to run every tick job after its push, on
one thread, so at boot the reminder walk and the interrupt tick (a cold read of every session's transcript: 24 s and 6 s on the
11:55 AM boot that decided this) held every browser frame behind a 30 to 60 s first cycle, and all day a slow job delayed the
next refresh. The user asked why the reminder walk had to finish before the UI showed at all. Now `_pusher_cycle_jobs` keeps what
feeds a frame, `_jobs_pass` runs the rest in the order it always had on the jobs thread (`_jobs_loop`, one pass per JOBS_PASS_S)
with its own liveness snapshot, /perf keeps the two loops' splits apart (`pusher` and `jobs`), and the boot-health row carries
both firsts (`firstCycleS`, the browser's wait; `jobsFirstPassS`, the housekeeping's). The checkpoint settle write
(`_persist_checkpoints`) followed on 2026-10-05, as the jobs pass's last job: on the pusher every write it made delayed the next
frame; the settle write's first sight of each session opens the pass (`_checkpoint_first_sight`), so a turn that settles while the
pass runs is written at its end. The checkpoint cycle and the converge pass followed on 2026-10-08, into the jobs thread's write
phase after the pass: the converge pass's first candidate always went, so an idle session's assembly document was written
inside the frame's cycle (9.9 s of one cycle over a 38 MB session in a lab). Then every document moved to a thread of its own,
the document writer (`_doc_writer_loop`, the review of PR 155): one big document held the jobs pass for its whole write (about
11 s for an idle 38 MB transcript, about a minute for a 225 MB one), and every decision of the pass with it. The pass decides
and hands its settle writes over, wakes the writer at its end, and the writer runs them, the checkpoint cycle and the converge
pass (`_document_write_phase`), taking the settle write's paths on its own thread, one after the other."""
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

PUSHER_JOBS = ("sessionsListing", "applyPendingOps", "artifactsSignal", "turnNotify", "bootRowBackstop", "kernelSample", "apiHealth")
WRITER_JOBS = ("settleWrites", "beginCheckpointCycle", "convergeCheckpoints")   # the document writer's cycle (review of PR 155)
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

    def test_the_three_lists_partition_the_jobs_census_in_the_old_order(self):
        km = self.km
        pusher = re.findall(JOB_NAME_RE, inspect.getsource(km._pusher_cycle_jobs))
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(km._jobs_pass))
        write = re.findall(JOB_NAME_RE, inspect.getsource(km._document_write_phase))
        self.assertEqual(tuple(pusher), PUSHER_JOBS, "the pusher keeps what feeds a frame, and no document writer")
        self.assertEqual(tuple(jobs), HOUSEKEEPING, "the housekeeping, in the order it always ran, the checkpoint settle write last")
        self.assertEqual(tuple(write), WRITER_JOBS, "the document writer's cycle: the handed settle writes, the checkpoint cycle, "
                                                    "then the converge pass")
        self.assertFalse(set(pusher) & (set(jobs) | set(write)) or set(jobs) & set(write), "no job in two places")
        self.assertEqual(set(pusher) | set(jobs) | set(write), set(km._PerfStats.JOBS), "together they are the JOBS census")
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
        self.assertIn('threading.Thread(target=_doc_writer_loop, daemon=True, name="doc-writer").start()', src)
        self.assertLess(src.index("target=_doc_writer_loop"), src.index("_DOC_WRITER_READY.wait()"))
        self.assertLess(src.index("_DOC_WRITER_READY.wait()"), src.index("target=_jobs_loop"),
                        "the writer holds its role before the jobs pass's first decision, so that pass hands its writes over")
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
        """The change itself: the reminder walk sleeping 0.4 s is the jobs pass's work and never the pusher cycle's. Asserted as
        the property, not as a wall-clock bound on the cycle: a loaded runner's cycle can pass any bound for reasons of its own
        (the persist's twin below failed at 305 ms against a 300 ms bound on CI, 2026-10-08). The walk records the loop whose
        split it runs in: it never runs during the pusher's cycle, the pusher's split names no such stage, and the jobs pass
        runs it once and carries its sleep, a lower bound. At the base the walk ran inside the pusher's cycle."""
        km = self.km
        calls = []
        km._auto_nudge_tick = lambda now, live_map: (calls.append(km._PERF_STATS._mine()), time.sleep(0.4))
        self.addCleanup(setattr, km, "_auto_nudge_tick", self.saved_jobs.get("_auto_nudge_tick", km._auto_nudge_tick))
        km._pusher_cycle()
        self.assertEqual(calls, [], "the pusher's cycle ran no walk")
        cycle_row = km._PERF_STATS.snapshot()["pusher"]["stageRing"][-1]["stages"]
        self.assertNotIn("jobs.autoNudge", cycle_row, "nor carried its stage: %r" % sorted(cycle_row))
        t0 = time.monotonic(); km._jobs_cycle(); pas = time.monotonic() - t0
        self.assertEqual(calls, ["jobs"], "the jobs pass ran it once, on its own split")
        self.assertGreaterEqual(pas, 0.4)
        snap = km._PERF_STATS.snapshot()
        self.assertEqual(snap["jobs"]["passes"], 1, "counted under /perf jobs, not pusher")
        self.assertEqual(snap["pusher"]["cycles"], 1)
        self.assertGreaterEqual(snap["stages_ms"]["jobs.autoNudge"], 400.0)
        self.assertGreaterEqual(snap["jobs"]["stageRing"][-1]["stages"]["jobs.autoNudge"]["ms"], 400.0, "on the pass's split")

    def test_a_slow_checkpoint_persist_lengthens_the_jobs_pass_and_not_the_pusher_cycle(self):
        """The checkpoint settle write's decision (2026-10-05): on the pusher's cycle each write it made held up the next frame.
        A persist sleeping 0.4 s is the jobs pass's work and never the pusher cycle's: it never runs during the pusher's cycle,
        the pusher's split names neither it nor any of the document writer's jobs, and the jobs pass runs it once, on its own
        split and its cpuByJob, carrying the sleep (a lower bound). The documents themselves are the writer's (the review of
        PR 155): the pass's split carries none of its jobs. Asserted as the property: this test bounded the pusher's cycle
        below 300 ms of wall and failed at 305 ms on a loaded CI runner (2026-10-08), with nothing of the persist in it. At
        the base the persist ran inside the pusher's cycle."""
        km = self.km
        calls = []

        def slow(now):
            calls.append(km._PERF_STATS._mine())                  # the owner kind whose split the call lands in
            time.sleep(0.4)
            return 0
        km._persist_checkpoints = slow                             # put back by tearDown (QUIET)
        km._pusher_cycle()
        self.assertEqual(calls, [], "the pusher's cycle ran no settle write")
        cycle_row = km._PERF_STATS.snapshot()["pusher"]["stageRing"][-1]["stages"]
        for job in ("persistCheckpoints",) + WRITER_JOBS:
            self.assertNotIn("jobs." + job, cycle_row, "the pusher's split carries no %s: %r" % (job, sorted(cycle_row)))
        t0 = time.monotonic(); km._jobs_cycle(); pas = time.monotonic() - t0
        self.assertEqual(calls, ["jobs"], "the jobs pass ran it, once, on its own split")
        self.assertGreaterEqual(pas, 0.4)
        snap = km._PERF_STATS.snapshot()
        pass_row = snap["jobs"]["stageRing"][-1]["stages"]
        self.assertGreaterEqual(pass_row["jobs.persistCheckpoints"]["ms"], 400.0, sorted(pass_row))
        for job in WRITER_JOBS:
            self.assertNotIn("jobs." + job, pass_row, "the documents are the writer's, never the pass's: %r" % sorted(pass_row))
        self.assertEqual(snap["jobs"]["cpuByJob"]["persistCheckpoints"]["runs"], 1, "one run of the jobs thread's own")

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
        self.assertEqual(order[order.index("persistCheckpoints") + 1:], [],
                         "the pass's last job; the documents follow on the writer's thread: %r" % order)
        self.assertEqual(order.count("persistCheckpoints"), 1, order)
        for job in ("autoNudge", "interruptBlock", "heldWorking"):
            self.assertLess(order.index(job), order.index("persistCheckpoints"), job)
        self.assertEqual(seen, [(True, "jobs")], "inside the pass's scope, on the jobs thread's split")

    def test_the_documents_run_on_the_writers_cycle_with_the_passs_snapshot_and_never_in_a_pass_or_a_cycle(self):
        """The review of PR 155: the checkpoint cycle (the budget whole again, the owed and handed drops paid) and the converge
        pass ran in the jobs thread's write phase after its gated pass (2026-10-08; on the pusher's cycle before), where one big
        document held the next pass for its whole write. Now neither the pusher's cycle nor the jobs cycle runs any of the
        writer's jobs; the jobs cycle wakes the writer with its live snapshot, and the writer's cycle runs the handed settle
        writes, the checkpoint cycle and the converge pass, once each in that order, on the writer's own split, with the
        gate's observation closed (they are no job it gates), the pass's snapshot served as its live map and a discover memo
        of its own. The writer loop holds the document writer; the jobs loop does not. At the base the jobs cycle ran both."""
        km = self.km
        calls, order = [], []
        real = km._job_stage
        live = {"11111111-2222-3333-4444-555555555501": {"state": "waiting"}}

        def recording(name, thunk):
            order.append(name)
            return real(name, thunk)

        def probe(name):
            def run(*a, **k):
                calls.append((name, km._PERF_STATS._mine(), getattr(km._JOBS_GATE_TL, "tick", None) is None,
                              isinstance(getattr(km._live_scope, "sessions", None), dict), km._live_scope.snapshot is live))
                return 0
            return run
        km._live_map = lambda: live                                 # the pass's snapshot (put back by tearDown)
        with mock.patch.object(km, "_job_stage", recording), \
                mock.patch.object(km, "_ckpt_settle_writes_pay", probe("settleWrites")), \
                mock.patch.object(km, "_begin_checkpoint_cycle", probe("beginCheckpointCycle")), \
                mock.patch.object(km, "_converge_checkpoints", probe("convergeCheckpoints")), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="doc-writer-probe") as writer:
            km._DOC_WRITER_WAKE.clear()
            km._pusher_cycle()
            self.assertEqual(calls, [], "the pusher's cycle ran none of the writer's jobs")
            km._jobs_cycle()
            self.assertEqual(calls, [], "nor did the jobs cycle: %r" % order)
            self.assertTrue(km._DOC_WRITER_WAKE.is_set(), "it woke the writer")
            km._live_map = lambda: {}                               # the world moves on: the writer serves the pass's snapshot
            writer.submit(km._doc_writer_cycle).result(timeout=30)
        self.assertEqual(calls, [("settleWrites", "writer", True, True, True), ("beginCheckpointCycle", "writer", True, True, True),
                                 ("convergeCheckpoints", "writer", True, True, True)],
                         "once each on the writer's split, in order, the gate closed, with the pass's snapshot and a memo")
        self.assertEqual(order[-3:], list(WRITER_JOBS), order)
        self.assertEqual(km._PERF_STATS.snapshot()["writer"]["cycles"], 1, "counted under /perf writer")
        self.assertIn("with em.document_writer():", inspect.getsource(km._doc_writer_loop), "the writer loop holds the role")
        self.assertNotIn("document_writer", inspect.getsource(km._jobs_loop), "the jobs loop does not")
        self.assertIsNone(km._live_scope.snapshot, "the writer's cycle closed its scope")

    def test_a_writer_job_that_raises_is_caught_and_named_and_the_cycle_still_closes(self):
        """A raise from each of the writer's jobs through the REAL writer cycle: nothing escapes the cycle (an escape is counted
        under writer.cycleFailed), stderr names the job that raised, the other jobs still run, and the cycle is counted. In
        the jobs thread's write phase on 2026-10-08 and on the pusher before, where a raise was caught the same way."""
        km = self.km
        names = {"_ckpt_settle_writes_pay": ("checkpoint settle writes: ", "settle"),
                 "_begin_checkpoint_cycle": ("checkpoint-cycle: ", "cycle"), "_converge_checkpoints": ("checkpoint converge: ", "converge")}
        for name, (line, tag) in names.items():
            with self.subTest(name):
                ran = []

                def boom(*a, **k):
                    raise RuntimeError("synthetic writer fault")
                patches = {fn: (boom if fn == name else (lambda *a, _t=t, **k: ran.append(_t))) for fn, (_l, t) in names.items()}
                err = io.StringIO()
                before = km._PERF_STATS.writer.get("cycles", 0)
                with mock.patch.multiple(km, **patches), mock.patch.object(km.sys, "stderr", err), \
                        concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="doc-writer-probe") as writer:
                    writer.submit(km._doc_writer_cycle).result(timeout=30)   # raises here if the fault escaped the cycle
                self.assertIn(line, err.getvalue(), err.getvalue()[-400:])
                self.assertIn("RuntimeError: synthetic writer fault", err.getvalue())
                self.assertEqual(ran, [t for fn, (_l, t) in names.items() if fn != name], "the other jobs still ran")
                self.assertEqual(km._PERF_STATS.writer.get("cycles", 0) - before, 1, "and the cycle was counted")

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


class TheSettleWriteHandsItsPathsToTheConvergePass(unittest.TestCase):
    """The hand-off between the two halves. The settle write (_persist_checkpoints, the jobs pass's last job) records the paths
    it wrote in _CKPT_JUST_WRITTEN; the converge pass skips them and empties the set. From 2026-10-05 the two ran on two
    threads, the converge pass on the pusher's, under a lock; since 2026-10-08 the converge pass runs in the jobs thread's
    write phase right after the pass, so the hand-off is one thread's again. Beside it, what the moves put on the jobs
    thread: the settle write's first sight at every pass's start (the settle key and the leaf's stat with its time stamp,
    each taken once per session, whichever pass first lists it), and the fault lines of first sight and of each half.
    Synthetic sids and paths; every collaborator of the two functions is stubbed, so nothing is read or written."""

    SID_A = "11111111-2222-3333-4444-555555555501"
    SID_B = "11111111-2222-3333-4444-555555555502"
    SID_C = "11111111-2222-3333-4444-555555555503"
    LEAF_A = "/synthetic/web/leaf.jsonl"
    LEAF_B = "/synthetic/api/leaf.jsonl"
    LEAF_C = "/synthetic/tests/leaf.jsonl"

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
        stub(km.em, "checkpoint_write_dirty_paths", lambda paths=None, budget_s=None, stop=None: (self.persisted.extend(paths), list(paths))[1])
        stub(km.em, "asm_checkpoint_write", lambda *a, **k: False)
        stub(km, "_display_sdk_human", lambda sid: False)
        stub(km, "_stored_tree", lambda leaf, sid: None)
        stub(km, "_asm_refresh_maybe", lambda leaf, sid: False)
        stack.enter_context(mock.patch.dict(km._CKPT_SETTLE_SEEN, {}, clear=True))
        stack.enter_context(mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True))
        if hasattr(km, "_CKPT_SETTLE_HANDED"):               # the writes the pass hands the document writer (review of PR 155)
            stack.enter_context(mock.patch.dict(km._CKPT_SETTLE_HANDED, {}, clear=True))
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
        quiet = {fn: (lambda *a, **k: None) for src in (km._jobs_pass, km._pusher_cycle_jobs, km._document_write_phase)
                 for _job, fn in re.findall(JOB_FN_RE, inspect.getsource(src)) if fn not in keep}
        quiet.update(_api_health_frame=lambda *a, **k: None, _files_stat_pass_open=lambda live_map: False,
                     _files_stat_pass_close=lambda opened: None)
        self.assertGreater(len(quiet), 20, sorted(quiet))
        return quiet

    def _cycles_and_passes(self, patches, between=None, n=3, on_jobs_thread=None):
        """The pusher's cycle, then the jobs thread's pass, `n` times, one thread each as in the kernel, under `patches` (the
        quiet jobs and the world a test sets); `between(i)` runs before the i-th cycle, where the world moves between passes.
        Each pass is recorded as the kernel's jobs loop records it (_PERF_STATS.jobs_pass, in the same submitted call on the
        jobs thread), so a job that tells the boot's first pass by the pass count (or by the first-pass split it records)
        sees the later passes as later (review of 2026-10-05, round four). The jobs loop's other signal for the boot's first
        pass, the boot marks (_BOOT_HEALTH_DONE and _BOOT_FIRST, which _jobs_cycle reads before the pass and reports after
        it), is not advanced here: every pass of this harness reads as the boot's first by those marks (round five).
        `on_jobs_thread` runs first on the jobs thread (the pool's initializer): a test that patches the process's clock
        learns that thread's identity there, so the patch reads only on the two threads the harness runs."""
        km = self.km
        before = km._PERF_STATS.jobs.get("passes", 0)

        def one_pass(now):
            km._jobs_pass(now, {})
            km._PERF_STATS.jobs_pass(0.0)
        with mock.patch.multiple(km, **patches), \
                concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="jobs-probe", initializer=on_jobs_thread) as jobs:
            for i in range(n):
                if between is not None:
                    between(i)
                km._pusher_cycle_jobs(NOW + i, {}, False)
                jobs.submit(one_pass, NOW + i).result(timeout=30)
        self.assertEqual(km._PERF_STATS.jobs.get("passes", 0) - before, n, "each pass recorded as the jobs loop records it")

    def test_a_path_the_settle_write_wrote_is_skipped_by_the_write_phase_that_follows_it(self):
        """The real jobs pass and the real write phase after it, one thread as in the kernel, every other job quiet: the pass's
        settle write writes the leaf's document and hands the path over, and the write phase's converge pass, with that leaf
        its only candidate, writes nothing for it and empties the set; the next write phase has nothing to skip and writes the
        candidate. Neither the pusher's cycles in between writes anything."""
        km = self.km
        quiet = self._quiet({"_persist_checkpoints", "_converge_checkpoints"})
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}]
        km._CKPT_SETTLE_SEEN[self.SID_A] = (1, ("synthetic", 1))   # the last write saw turn-end key 1; it is 2 now: a settle
        self.cands = [self.LEAF_A]
        with mock.patch.multiple(km, **quiet):
            km._jobs_pass(NOW, {})
            self.assertEqual(self.persisted, [self.LEAF_A], "the jobs pass's settle write wrote the leaf's document")
            self.assertEqual(km._CKPT_JUST_WRITTEN, {self.LEAF_A}, "and handed the path over")
            km._pusher_cycle_jobs(NOW, {}, False)
            self.assertEqual((self.writes, km._CKPT_JUST_WRITTEN), ([], {self.LEAF_A}), "the pusher's cycle took nothing")
            km._document_write_phase(NOW)
            self.assertEqual(self.writes, [], "the write phase's converge pass skipped the path the settle had just written")
            self.assertEqual(km._CKPT_JUST_WRITTEN, set(), "and emptied the set at its look")
            km._document_write_phase(NOW + 1)
        self.assertEqual(self.writes, [self.LEAF_A], "the next look has nothing to skip: the candidate is written")
        self.assertEqual(self.persisted, [self.LEAF_A], "and the write phases wrote no settle document of their own")

    def _writer_thread(self):
        """A thread holding the document writer's role, as the kernel's writer loop does for its life, for the test's length;
        work submitted to it runs on that thread. The role is put back on the way out."""
        km = self.km
        prev = km.em._DOC_WRITER["thread"]
        self.addCleanup(km.em._DOC_WRITER.__setitem__, "thread", prev)
        ex = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="doc-writer-probe",
                                                   initializer=lambda: km.em.document_writer().__enter__())
        self.addCleanup(ex.shutdown)
        ex.submit(lambda: None).result(timeout=30)          # the role is held before the pass decides anything
        return ex

    def test_while_the_document_writer_runs_the_pass_hands_its_settle_writes_over_and_writes_none(self):
        """The review of PR 155: the jobs pass wrote each due session's documents itself, as its last job, so one big document
        held the pass (and its nudges, spend guard, interrupt moves and retries) for the whole write: about 11 s for an idle
        38 MB transcript, about a minute for a 225 MB one. While the document writer runs, the pass now decides and hands
        over: it writes nothing, records the session's evidence as decided, and the writer's cycle writes the documents and
        hands the paths to the converge pass on its own thread. At the base the pass wrote the leaf's document itself."""
        km = self.km
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}]
        km._CKPT_SETTLE_SEEN[self.SID_A] = (1, ("synthetic", 1))   # the last write saw turn-end key 1; it is 2 now: a settle
        writer = self._writer_thread()
        km._persist_checkpoints(NOW)                                # the jobs pass's settle job, on this thread
        self.assertEqual(self.persisted, [], "the pass wrote no document")
        self.assertEqual(km._CKPT_SETTLE_SEEN[self.SID_A][0], 2, "the session's evidence recorded as decided")
        self.assertEqual(dict(km._CKPT_SETTLE_HANDED), {self.SID_A: (self.LEAF_A, True)}, "handed to the writer, settle-due")
        self.assertEqual(writer.submit(km._ckpt_settle_writes_pay).result(timeout=30), 1)
        self.assertEqual(self.persisted, [self.LEAF_A], "the writer wrote it")
        self.assertEqual(km._CKPT_JUST_WRITTEN, {self.LEAF_A}, "and handed the path to the converge pass")
        self.assertEqual(dict(km._CKPT_SETTLE_HANDED), {}, "nothing left handed")
        km._persist_checkpoints(NOW + 1)                            # no new evidence: nothing handed again
        self.assertEqual(dict(km._CKPT_SETTLE_HANDED), {})

    def test_a_handed_settle_write_that_raises_is_its_own_and_the_rest_are_written(self):
        """Each handed settle write is its own on the writer: the first session's raising (its leaf's heal here) is said with
        its session, and the second's documents are still written; past the exit's stand-down none starts and what is left
        stays handed."""
        km = self.km
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        for sid in (self.SID_A, self.SID_B):
            km._CKPT_SETTLE_SEEN[sid] = (1, ("synthetic", 1))
        writer = self._writer_thread()
        km._persist_checkpoints(NOW)

        def heal(leaf, dropped_out=None):
            if leaf == self.LEAF_A:
                raise OSError("synthetic heal fault")
            return []
        err = io.StringIO()
        with mock.patch.object(km, "_heal_cold_folds", heal), mock.patch.object(km.sys, "stderr", err):
            writer.submit(km._ckpt_settle_writes_pay).result(timeout=30)
        self.assertEqual(self.persisted, [self.LEAF_B], "the second session's documents written")
        self.assertIn("checkpoint settle write (%s)" % self.SID_A[:8], err.getvalue())
        self.assertIn("synthetic heal fault", err.getvalue())
        km._ckpt_settle_hand(self.SID_C, self.LEAF_C, False)
        km._EXIT_STAND_DOWN.set()
        try:
            writer.submit(km._ckpt_settle_writes_pay).result(timeout=30)
        finally:
            km._EXIT_STAND_DOWN.clear()
        self.assertEqual(self.persisted, [self.LEAF_B], "past the stand-down no write starts")
        self.assertEqual(list(km._CKPT_SETTLE_HANDED), [self.SID_C], "and what is left stays handed")

    # ── the review of PR 158: a stuck document writer is loud, and its backlog shows in /perf ─────────────────────────────
    def test_the_bound_is_the_writers_own_history_and_a_working_write_is_never_called_stuck(self):
        """The watch's rule on a tracker driven by explicit times and thread CPU readings."""
        km = self.km
        t = km._DocWriterProgress()
        cpu = [5.0]
        t._cpu = lambda item: cpu[0]
        prev = km.em._DOC_WRITER["thread"]
        self.addCleanup(km.em._DOC_WRITER.__setitem__, "thread", prev)
        km.em._DOC_WRITER["thread"] = threading.get_ident()         # this thread is the writer for the test
        t.begin("fold", "/synthetic/web/a.jsonl", 1000, at=0.0)
        t.end(wrote=True, at=0.1)                                  # the kind learns: longest 0.1 s, 0.1 ms per byte
        t.begin("fold", "/synthetic/web/b.jsonl", 500, at=0.5)
        t.end(wrote=False, at=9.5)                                 # a write that wrote nothing teaches nothing
        t.begin("fold", "/synthetic/web/c.jsonl", 2000, at=1.0)   # expected at most max(0.1, 2000 x 0.0001) = 0.2 s
        self.assertIsNone(t.watch(at=1.05), "first sight: no whole pass watched yet")
        self.assertIsNone(t.watch(at=1.15), "within its expectation")
        got = t.watch(at=1.5)
        self.assertIsNotNone(got, "past it, and no CPU since the last watch: overdue")
        item, age, exp = got
        self.assertEqual((item["kind"], item["path"], round(age, 2), round(exp, 2)), ("fold", "/synthetic/web/c.jsonl", 0.5, 0.2))
        self.assertIsNone(t.watch(at=2.0), "said once per write")
        self.assertEqual(t.report(at=2.0)["inFlight"][0]["overdue"], True)
        t.end(wrote=True, at=3.0)                                  # the episode ends with the write (it also teaches: 2 s)
        t.begin("fold", "/synthetic/web/d.jsonl", 2000, at=10.0)  # expected at most max(2.0, 2000 x 0.001) = 2.0 s
        t.watch(at=10.1)
        cpu[0] = 6.0
        self.assertIsNone(t.watch(at=13.0), "past its expectation but the writer worked since the last watch: slow, not stuck")
        self.assertIsNotNone(t.watch(at=13.5), "no CPU across the next whole pass: overdue, a new episode")
        self.assertEqual(t.overdue, 2)
        t.end(at=14.0)
        t.begin("assembly", "/synthetic/web/leaf.jsonl", 10 ** 8, at=20.0)
        for at in (20.1, 100.0, 1000.0):
            self.assertIsNone(t.watch(at=at), "a kind with no completed write has no expectation: never judged")
        t.end(at=1001.0)
        t._cpu = lambda item: None
        t.begin("fold", "/synthetic/web/e.jsonl", 10, at=2000.0)
        for at in (2000.1, 3000.0, 4000.0):
            self.assertIsNone(t.watch(at=at), "no thread CPU clock: never judged")

    def test_a_write_stuck_on_the_real_writer_is_said_once_and_its_backlog_shows_in_perf(self):
        """The real writer's settle writes on a thread holding the role, the real jobs-pass end (_doc_writer_wake) as the watch:
        one heal completes and teaches its kind, the next blocks (waiting on a lock it never gets, as a deadlock would), and
        within a few passes a problem row names it, its size and how long; while it is stuck a second session's settle write
        handed over shows in /perf writer's backlog, and the write in flight shows overdue. Released, it ends and the writer
        goes on. At the base nothing said it: no row, and /perf writer had no live part."""
        km = self.km
        leaf = self.td_leaf()
        self.sessions = [{"sid": self.SID_A, "path": leaf}]
        entered, release = threading.Event(), threading.Event()
        heals = []

        def heal(path, dropped_out=None):
            heals.append(path)
            if len(heals) == 1:
                return ["agentLaunches"]                           # a heal that did work: the kind learns its pace
            entered.set()
            release.wait(30)                                       # stuck: waiting, no CPU spent
            return []
        writer = self._writer_thread()
        before_rows = len(km._SDK_BOOT_PROBLEMS)
        err = io.StringIO()
        with mock.patch.object(km, "_heal_cold_folds", heal), mock.patch.object(km.sys, "stderr", err):
            km._ckpt_settle_hand(self.SID_A, leaf, True)
            writer.submit(km._ckpt_settle_writes_pay).result(timeout=30)
            km._ckpt_settle_hand(self.SID_A, leaf, True)
            stuck = writer.submit(km._ckpt_settle_writes_pay)
            self.assertTrue(entered.wait(30), "the second heal began")
            rows, deadline = [], time.monotonic() + 20
            while not rows and time.monotonic() < deadline:
                km._doc_writer_wake(NOW, {})                       # a jobs pass ends: the writer's watch
                rows = [r for r in km._SDK_BOOT_PROBLEMS[before_rows:] if "document writer" in r["text"]]
                if not rows:
                    threading.Event().wait(0.05)                   # (the next pass's end)
            self.assertEqual(len(rows), 1, "one problem row for the stuck write: %r" % km._SDK_BOOT_PROBLEMS[before_rows:])
            text = rows[0]["text"]
            self.assertIn("heal", text); self.assertIn(leaf, text); self.assertIn("MB", text)
            self.assertRegex(text, r"spent [0-9.]+ s", "how long")
            km._ckpt_settle_hand(self.SID_B, self.LEAF_B, True)     # handed while the writer is stuck
            for _ in range(3):
                km._doc_writer_wake(NOW, {})
            self.assertEqual(len([r for r in km._SDK_BOOT_PROBLEMS[before_rows:] if "document writer" in r["text"]]), 1,
                             "said once per write")
            w = km._PERF_STATS.snapshot()["writer"]
            self.assertEqual([(f["kind"], f["path"], f["overdue"]) for f in w["inFlight"]], [("heal", leaf, True)], w["inFlight"])
            self.assertGreater(w["inFlight"][0]["ageS"], w["inFlight"][0]["expectS"])
            self.assertEqual(w["handed"]["settles"], 1, "the backlog grows while it is stuck: %r" % w["handed"])
            self.assertGreaterEqual(w["handedPeak"]["settles"], 1)
            self.assertEqual(w["byKind"]["heal"]["writes"], 1)
            release.set()
            stuck.result(timeout=30)
        self.assertIn("finished after", err.getvalue(), "the episode's end is said on stderr")
        self.assertEqual(km._PERF_STATS.snapshot()["writer"]["inFlight"], [])

    def td_leaf(self):
        """A real leaf file of a known size (the progress events read its size)."""
        import shutil
        import tempfile
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        p = os.path.join(d, self.SID_A + ".jsonl")
        with open(p, "w") as fh:
            fh.write("x" * 2048 + "\n")
        return p

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

    def test_with_the_period_at_zero_a_leaf_that_grows_during_the_boots_first_pass_is_written_at_that_passs_end(self):
        """First sight's other half (the review's round two): the leaf's stat, the periodic trigger's evidence, is taken at the
        pass's START beside the settle key. No turn ends here and the states log stands (the settle key is fixed), the period
        is 0, and web's leaf grows during the first pass's reminder walk; api's stands. The settle write at the pass's end
        sees web's leaf moved since its first sight and writes that document, once, and api's not at all. With the leaf's
        first sight left to the write, at the pass's end, the growth fell before it and the leaf waited for its next move.
        The write lands at this pass's end only because the period is 0: with the shipped period it waits until the period
        has passed since first sight (the clock test below)."""
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

    def test_a_session_first_listed_at_a_later_pass_whose_turn_settles_during_it_is_written_at_its_end(self):
        """First sight at every pass's start, not on the boot's first pass alone (the review's round three): a session first
        seen after the boot is one of the reasons _checkpoint_first_sight's docstring gives. Web and api are listed from the
        boot's first pass, their evidence standing; tests is first listed before the second pass (before the pusher's second
        cycle), and its turn settles during that pass's reminder walk (its turn-end key moves from 1 to 2). The second pass's
        start records tests' key as 1, so the settle write at its end sees the move and writes tests' document, once, and no
        other session's. A first sight taken on the boot's first pass alone, told by the pass count or the first-pass split,
        would leave tests to the write's own first sight, at the second pass's end, after the move: nothing written, its
        documents waiting for its next settle or a leaf move past the period. A limit told by the jobs loop's boot marks is
        not caught here, since the harness does not advance them (see _cycles_and_passes)."""
        km = self.km
        patches = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        turn = {"key": 1}
        walks = []

        def walk(now, live_map):
            walks.append(now)
            if len(walks) == 2:
                turn["key"] = 2                                     # tests' turn settles during the second pass

        def between(i):
            if i == 1:                                              # tests is first listed after the boot's first pass
                self.sessions.append({"sid": self.SID_C, "path": self.LEAF_C})
        patches.update(_auto_nudge_tick=walk, _turn_end_key=lambda sid, reg=None: turn["key"] if sid == self.SID_C else 1)
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        self._cycles_and_passes(patches, between)
        self.assertEqual(len(walks), 3, "the walk ran in every pass")
        self.assertEqual(self.persisted, [self.LEAF_C], "the settle during the pass that first listed tests was written, once, "
                                                        "and only it")
        self.assertEqual(km._CKPT_SETTLE_SEEN[self.SID_C][0], 2, "and its key recorded as written")

    def test_with_the_period_at_zero_a_leaf_that_grows_between_two_passes_is_written_at_the_next_passs_end(self):
        """The same for the leaf's stat: the settle key fixed and the period at 0, web's leaf grows BETWEEN the first pass and
        the second; api's stands. The second pass's start leaves web's recorded stat alone, so the settle write at its end
        sees the leaf moved and writes web's document, once. A leaf stat retaken at every pass's start would record the
        growth there with a fresh stamp and the write would find the leaf unmoved; and since the stamp would reset each
        pass, the periodic trigger could never fire within a pass shorter than the period. The write lands at the next
        pass's end only because the period is 0 (the clock test below holds the shipped case)."""
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

    def test_a_leaf_that_grows_between_two_passes_is_written_once_the_period_has_passed_since_first_sight(self):
        """First sight's time stamp (the review's round three). First sight records the leaf's stat with the monotonic time,
        and that stamp is what makes a periodic write wait CKPT_PERIOD_S after first sight. The kernel's clock is patched as
        PeriodicCheckpoints._run in tests/test_restart_phases.py patches it, but it starts at ten periods (from 0.0, a stamp
        of 0.0 and a true one agree), and the period is 30 s, not 0. The boot's first pass takes first sight at its start and
        runs 10 s (its reminder walk moves the clock); web's leaf grows before the second pass, 15 s after first sight: no
        write there, and web's entry still holds first sight's stat and stamp. The clock then moves to 35 s after first
        sight, 25 s after the first pass's end: the third pass writes web's document, once, and api's, whose leaf stands,
        never. A stamp of 0.0 or of mono - 1e9 writes at the second pass; a leaf stat left to the write's own first sight,
        at the first pass's end, starts the period 10 s late and writes nothing at the third."""
        km = self.km
        period = 30.0
        t0 = 10 * period
        clock = {"t": t0}
        leaf = {"size": 1}
        walks, after_second = [], {}

        def walk(now, live_map):
            walks.append(clock["t"])
            if len(walks) == 1:
                clock["t"] = t0 + 10.0                              # the boot's first pass runs 10 s past its first sight

        def between(i):
            seen = []
            other = threading.Thread(target=lambda: seen.append(time.monotonic()), name="another-thread-reads-the-clock")
            other.start()
            other.join()
            self.assertNotEqual(seen, [clock["t"]], "another thread reads the real clock, never this test's")
            if i == 1:
                leaf["size"] = 2                                    # web's leaf grows before the second pass, inside the period
                clock["t"] = t0 + 15.0
            elif i == 2:
                after_second.update(persisted=list(self.persisted), entry=km._CKPT_PERIODIC_SEEN.get(self.SID_A))
                clock["t"] = t0 + period + 5.0                      # past the period since first sight, not since the pass's end
        patches = self._quiet({"_checkpoint_first_sight", "_persist_checkpoints", "_converge_checkpoints"})
        patches.update(_auto_nudge_tick=walk, CKPT_PERIOD_S=period,
                       _stat_key=lambda path: ("synthetic", leaf["size"] if str(path) == self.LEAF_A else 1))
        self.sessions = [{"sid": self.SID_A, "path": self.LEAF_A}, {"sid": self.SID_B, "path": self.LEAF_B}]
        # The patched clock reads only on this test's two threads, the pusher's (this one) and the jobs thread the harness
        # starts. `km.time` is the process's one time module, so the patch is process-wide, and every other thread (another
        # test's kernel thread, a backend's sweep taking its deadline) read this test's clock, as a thread's read took a
        # step of tests/test_perf_stats.py's scripted clock (PR #150). Another thread gets the real clock, and one reads it
        # before every cycle (`between`) to keep it so.
        mine, real_clock = {threading.get_ident()}, time.monotonic

        def _clock():
            return clock["t"] if threading.get_ident() in mine else real_clock()
        with mock.patch.object(km.time, "monotonic", _clock):
            self._cycles_and_passes(patches, between, on_jobs_thread=lambda: mine.add(threading.get_ident()))
        self.assertEqual(walks, [t0, t0 + 15.0, t0 + period + 5.0], "the walk ran in every pass, on the patched clock")
        self.assertEqual(after_second["persisted"], [], "the growth inside the period since first sight was not written")
        self.assertEqual(after_second["entry"], (("synthetic", 1), t0), "and web's entry kept first sight's stat and stamp")
        self.assertEqual(self.persisted, [self.LEAF_A], "past the period since first sight the growth was written, once, "
                                                        "and only it")
        self.assertEqual(km._CKPT_PERIODIC_SEEN[self.SID_A], (("synthetic", 2), t0 + period + 5.0),
                         "and the leaf's stat recorded with the write's time")

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

    def _run_loops(self, until, jobs_cycle=None):
        """The REAL jobs loop and, when this kernel has it, the REAL document writer loop, each on a thread of its own as main
        starts them, the writer first and holding its role before the jobs loop starts; `until` (an Event) ends both. A loop
        still running after its stop is a failure, never a hang."""
        km = self.km
        saved_stop = km._LOOPS_STOP.is_set(); km._LOOPS_STOP.clear()
        km._DOC_WRITER_WAKE.clear() if hasattr(km, "_DOC_WRITER_WAKE") else None
        threads = []
        if hasattr(km, "_doc_writer_loop"):
            km._DOC_WRITER_READY.clear()
            threads.append(threading.Thread(target=km._doc_writer_loop, name="doc-writer-lab", daemon=True))
            threads[-1].start()
            self.assertTrue(km._DOC_WRITER_READY.wait(30), "the writer took its role")
        threads.append(threading.Thread(target=km._jobs_loop, name="jobs-lab", daemon=True))
        threads[-1].start()
        try:
            until.wait(30)
        finally:
            km._LOOPS_STOP.set()
            for th in threads:
                th.join(30)
            stuck = [th.name for th in threads if th.is_alive()]
            if not saved_stop:
                km._LOOPS_STOP.clear()
        return stuck

    def test_a_document_write_that_runs_long_holds_no_jobs_pass(self):
        """The review of PR 155: every document was written on the jobs thread, so one big write (an idle 225 MB transcript's
        assembly document: about a minute) held the next pass, and every decision the pass makes, for its whole length. Here
        the real loops run, and the converge pass's write blocks until the test releases it: while it is blocked the jobs loop
        completes pass after pass. The property is the event, three passes completed during the write, never a time bound
        (the waits below only bound a failure). At the base the jobs loop ran the write itself and no pass completed while it
        was blocked."""
        km = self.km
        writing, released, three = threading.Event(), threading.Event(), threading.Event()
        passes_during = []

        def long_write(now):
            writing.set()
            released.wait(30)
            return 0
        real_cycle = km._jobs_cycle

        def counted():
            real_cycle()
            if writing.is_set() and not released.is_set():
                passes_during.append(1)
                if len(passes_during) >= 3:
                    three.set()
        with mock.patch.object(km, "_converge_checkpoints", long_write), mock.patch.object(km, "_jobs_cycle", counted), \
                mock.patch.object(km, "JOBS_PASS_S", 0.01):
            done = threading.Event()

            def watch():
                three.wait(30)
                released.set()
                done.set()
            threading.Thread(target=watch, daemon=True).start()
            stuck = self._run_loops(done)
        self.assertTrue(writing.is_set(), "the write began")
        self.assertGreaterEqual(len(passes_during), 3, "passes completed while the write was blocked: %d" % len(passes_during))
        self.assertEqual(stuck, [], "both loops stopped")

    def test_the_writer_loop_holds_the_document_writer_for_its_life_and_lets_it_go(self):
        """The document writer loop is the process's document writer while it runs (the review of PR 155; the jobs loop was,
        on 2026-10-08): inside a jobs pass this thread hands its drops over (documents_write_here is False on it) while the
        writer's thread writes; once the loops stop, the role is let go, so the process (a test's, here) is left as it was
        found: any thread writes its own."""
        km = self.km
        em = km.em
        seen = {}
        passed = threading.Event()
        real_cycle = km._jobs_cycle

        def cycle():
            seen.setdefault("jobs", em.documents_write_here())
            real_cycle()
            passed.set()
        real_phase = km._document_write_phase

        def phase(now):
            seen.setdefault("writer", em.documents_write_here())
            return real_phase(now)
        wrote = threading.Event()
        with mock.patch.object(km, "_jobs_cycle", cycle), mock.patch.object(km, "_document_write_phase",
                                                                             lambda now: (phase(now), wrote.set())), \
                mock.patch.object(km, "JOBS_PASS_S", 0.01):
            stuck = self._run_loops(wrote)
        self.assertEqual(stuck, [])
        self.assertEqual(seen, {"jobs": False, "writer": True}, "the jobs pass hands its writes over; the writer writes")
        after = []
        th = threading.Thread(target=lambda: after.append(em.documents_write_here()), name="pusher-probe")
        th.start(); th.join(10)
        self.assertEqual(after, [True], "after the loops: any thread writes its own again")

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
