#!/usr/bin/env python3
"""The jobs pass's gate in SHADOW (stage one of the event-driven jobs pass, 2026-10-05). The jobs loop rests JOBS_PASS_S after each
pass and then runs every job again, whether or not anything a job reads changed, so a cheaper job buys more passes, not less CPU.
The gate will run a job only when one of its declared inputs moved, a due it noted came, or it owes work. This stage observes each
job's inputs at its turn, says what an enforcing gate would have done, runs the job exactly as before, and counts every job it
called closed that acted anyway or found its own memo moved (a wrongQuiet: a signal the job's declaration lacks).

Every test drives the REAL _jobs_cycle over a private synthetic state root (session-hosts written off), the jobs stubbed quiet
(tests/test_jobs_thread_split.py's QUIET list) unless a test speaks for one, and the module state the gate's predicates read set to
a quiet world and restored after. Synthetic sids, a notes-api session named api, nothing recorded from a real machine."""
import inspect
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import kernel_module   # noqa: E402
from test_jobs_thread_split import QUIET        # noqa: E402  (the jobs pass's quiet stubs: one list for both modules)

JOB_NAME_RE = r"_job_stage\(['\"](\w+)['\"]"
SID = "11111111-2222-3333-4444-00009a7e0001"     # private synthetic sids (CLAUDE.md: goal-store fixtures use their own)
SID2 = "11111111-2222-3333-4444-00009a7e0002"
GONE = "11111111-2222-3333-4444-00009a7e0003"
T0 = 1_800_000_000                                 # a synthetic clock for the tests that move it


class Census(unittest.TestCase):
    """T1: the inputs table and the pass agree, and every channel a spec names is one the gate can observe."""

    def setUp(self):
        self.km = kernel_module()

    def test_every_job_of_the_pass_is_declared_or_named_undeclared_and_every_entry_is_a_real_job(self):
        km = self.km
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(km._jobs_pass))
        self.assertGreater(len(jobs), 15, jobs)
        for job in jobs:
            self.assertTrue(job in km._JOBS_INPUTS or job in km._JOBS_UNDECLARED, "%s: declared, or named undeclared" % job)
        for job in list(km._JOBS_INPUTS) + list(km._JOBS_UNDECLARED):
            self.assertIn(job, km._PerfStats.JOBS, "%s: a real job (a _job_stage name of the census)" % job)
        self.assertFalse(set(km._JOBS_INPUTS) & set(km._JOBS_UNDECLARED))
        self.assertEqual(set(km._JOBS_UNDECLARED), {"persistCheckpoints", "convergeCheckpoints"},
                         "the checkpoint persist and converge are owed every pass wherever they run")

    def test_every_channel_a_spec_names_resolves(self):
        km = self.km
        for job, spec in km._JOBS_INPUTS.items():
            self.assertLessEqual(set(spec), {"inputs", "active", "idle", "due", "owed"}, job)
            if "idle" in spec:
                self.assertIn("active", spec, job)
            for ch in tuple(spec.get("inputs", ())) + tuple(spec.get("idle", ())):
                kind, _, arg = ch.partition(":")
                if kind == "box":
                    self.assertIn(arg, km._JOBS_GATE_BOX, "%s: %s" % (job, ch))
                elif kind == "mem":
                    self.assertIn(arg, km._JOBS_GATE_MEM, "%s: %s" % (job, ch))
                elif kind == "files":
                    self.assertTrue(arg and all(p in km._JOBS_GATE_POSITIONS for p in arg.split(",")), "%s: %s" % (job, ch))
                else:
                    self.assertIn(ch, ("rows", "members", "regs"), "%s: %s" % (job, ch))
        self.assertEqual(km._JOBS_GATE_POSITIONS, km._TICK_KEY_FILES[:6])
        # the critique's F15: an unsignaled wake entry is seen by the files floor before its own drive is due, so the idle drive's
        # read on the floor is never later than today's; a change to either constant must not reopen that gap
        self.assertLess(km.NUDGE_FLOOR_S, km._WAKE_DRIVE_FLOOR_S)

    def test_the_gates_six_files_are_the_shared_snapshots_first_twelve(self):
        """The gate stats a session's six own files by the same paths as _session_files_stat (outside a pass both stat fresh)."""
        km = self.km
        root = Path(tempfile.mkdtemp())
        (root / "session-hosts").write_text("off\n")                 # a test that mints its own state root pins session hosts off
        self.addCleanup(shutil.rmtree, root, True)
        saved = km.jd.STATE
        km.jd._rebind_state(root)
        self.addCleanup(km.jd._rebind_state, saved)
        tx = root / "projects" / (SID + ".jsonl")
        tx.parent.mkdir(parents=True)
        tx.write_text("{}\n")
        (root / "states").mkdir()
        (root / "states" / (SID + ".jsonl")).write_text('{"state": "idle"}\n')
        km.jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.GOALDIR / (SID + ".json")).write_text("{}")
        s = {"sid": SID, "path": str(tx)}
        self.assertEqual(km._jobs_gate_six(s), tuple(km._session_files_stat(s)[:12]))


class _GateLab(unittest.TestCase):
    """A hermetic world for the gate: a private state root, the jobs pass's jobs stubbed quiet, one or two synthetic sessions
    alive, and every piece of module state the gate's predicates and the pass's prelude read saved, set quiet, and restored."""

    LIST_SLOTS = ("_prev_live_sids", "_TICK_SEEN_DIRTY", "_INTR_MARKS_DISK_DIRTY", "_NUDGE_WALK_CURSOR", "_auto_nudge_paused",
                  "_usage_poll_last", "_retry_upgrade_last", "_BOOT_HEALTH_DONE", "_JOBS_GATE_MODE")
    CONTAINERS = ("_lift_seen", "_SPEND_CAN_PREV", "_SPEND_GUARD", "_SPEND_TREE_CACHE", "_held_working", "_auto_nudge_drops_pending",
                  "_FILES_STAT_STANDING", "_compact_clicked")

    def setUp(self):
        km = self.km = kernel_module()
        self.root = Path(tempfile.mkdtemp())
        (self.root / "session-hosts").write_text("off\n")            # a test that mints its own state root pins session hosts off
        self.addCleanup(shutil.rmtree, self.root, True)
        saved_state = km.jd.STATE
        km.jd._rebind_state(self.root)
        self.addCleanup(km.jd._rebind_state, saved_state)
        self.live, self.sessions = {}, []
        stubs = {nm: (lambda *a, **k: None) for nm in QUIET}
        stubs.update(_auto_nudge_tick=lambda *a, **k: None, _retry_upgrade_tick=lambda *a, **k: None,
                     _live_map=lambda: self.live, NAMES={}, _push_all=lambda live_map=None: None,
                     _sdk=lambda: None, WORKING_DIR=self.root / "working",
                     _alive_sessions=lambda now, live_map: [dict(s) for s in self.sessions if s["sid"] in live_map])
        patch = mock.patch.multiple(km, **stubs)
        patch.start()
        self.addCleanup(patch.stop)
        rev = mock.patch.object(km.Sessions, "live_rev", staticmethod(lambda sid, be=None: 0))
        rev.start()
        self.addCleanup(rev.stop)
        saved_slots = {nm: getattr(km, nm)[0] for nm in self.LIST_SLOTS}
        saved_boxes = {nm: (type(getattr(km, nm)), list(getattr(km, nm).items()) if isinstance(getattr(km, nm), dict)
                            else list(getattr(km, nm))) for nm in self.CONTAINERS}
        saved_misc = (km._PREV_ALIVE, dict(km._FILES_STAT_DIRTY, sids=set(km._FILES_STAT_DIRTY["sids"])),
                      dict(km._FILES_STAT_OBSERVED), dict(km._JOBS_GATE, rec={}, lost={}), dict(km._TICK_SEEN))

        def restore():
            for nm, v in saved_slots.items():
                getattr(km, nm)[0] = v
            for nm, (_t, items) in saved_boxes.items():
                box = getattr(km, nm)
                box.clear()
                (box.update(items) if isinstance(box, dict) else box.update(items))
            km._PREV_ALIVE = saved_misc[0]
            km._FILES_STAT_DIRTY.clear(); km._FILES_STAT_DIRTY.update(saved_misc[1])
            km._FILES_STAT_OBSERVED.clear(); km._FILES_STAT_OBSERVED.update(saved_misc[2])
            km._jobs_gate_reset()
            km._JOBS_GATE["modeKey"], km._JOBS_GATE["asked"] = saved_misc[3]["modeKey"], saved_misc[3]["asked"]
            km._TICK_SEEN.clear(); km._TICK_SEEN.update(saved_misc[4])
            km._PERF_STATS.reset()
        self.addCleanup(restore)
        for nm in self.CONTAINERS:
            getattr(km, nm).clear()
        for nm in ("_prev_live_sids", "_NUDGE_WALK_CURSOR", "_auto_nudge_paused"):
            getattr(km, nm)[0] = None
        km._TICK_SEEN_DIRTY[0] = km._INTR_MARKS_DISK_DIRTY[0] = False
        km._BOOT_HEALTH_DONE[0] = True                                # the boot's first-pass sampler and row are not this module's
        km._usage_poll_last[0] = km._retry_upgrade_last[0] = 0.0
        km._PREV_ALIVE = None
        km._FILES_STAT_DIRTY.clear(); km._FILES_STAT_DIRTY.update({"sids": set(), "all": False})
        km._FILES_STAT_OBSERVED.clear()
        km._FILES_STAT_OBSERVED.update({"postal": None, "rows": {}, "jdAll": None, "jdBy": {}, "sig": None, "sigMsgs": None})
        km._jobs_gate_reset()
        km._JOBS_GATE["modeKey"], km._JOBS_GATE["asked"], km._JOBS_GATE_MODE[0] = None, "", "shadow"
        km._PERF_STATS.reset()

    # ── the world ──
    def add_session(self, sid=SID, name="api", state="idle"):
        km = self.km
        tx = self.root / "projects" / (sid + ".jsonl")
        tx.parent.mkdir(parents=True, exist_ok=True)
        tx.write_text(json.dumps({"type": "user", "uuid": "aaaaaaaa-0000-4000-8000-0000000000a1",
                                  "timestamp": "2026-10-05T00:00:00Z",
                                  "message": {"role": "user", "content": "check the notes-api build"}}) + "\n")
        self.sessions.append({"sid": sid, "name": name, "path": str(tx), "mtime": tx.stat().st_mtime, "anchor": sid})
        self.live[sid] = {"state": state, "since": 1, "subagents": [], "bgTasks": [], "backend": "sdk"}
        km._lift_seen[sid] = ("ruled",)                               # the quiet world: the lift has ruled on it,
        km._SPEND_CAN_PREV[sid] = False                               #  the guard's edge after spending has passed,
        km._prev_live_sids[0] = set(self.live)                        #  the death sweep and the dead-wait sweep have seen it
        km._PREV_ALIVE = set(self.live)
        return tx

    def passes(self, n=1, clock=None):
        """Drive n REAL jobs passes (at the synthetic `clock` when given) and return the gate's block on /perf."""
        km = self.km
        for _ in range(n):
            if clock is None:
                km._jobs_cycle()
            else:
                with mock.patch.object(km.time, "time", lambda: float(clock)):
                    km._jobs_cycle()
        return self.gate()

    def gate(self):
        return self.km._PERF_STATS.snapshot()["jobs"]["gate"]

    def last(self):
        return dict(self.km._JOBS_GATE["last"])

    def quiet(self, clock=None):
        """Three passes: the first is every job's first, the second the spend guard's (it skips the boot's first pass), and the
        third is the first on which every declared job has a record."""
        return self.passes(3, clock)

    def declared(self):
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(self.km._jobs_pass))
        return [j for j in jobs if j in self.km._JOBS_INPUTS]


class AQuietWorld(_GateLab):
    def test_on_a_quiet_pass_every_declared_job_is_called_closed_and_the_pass_would_have_been_skipped(self):
        """T2. Nothing moves between passes: from the third pass on, every declared job is closed, the pass counts under
        wouldSkipPass (every job closed; undeclared jobs, if the pass runs any, are owed and keep it from counting, and
        quietDeclared counts it then), and the closed runs' wall and CPU are the saving."""
        self.add_session()
        g = self.quiet()
        last = self.last()
        for job in self.declared():
            self.assertEqual(last.get(job), "closed", "%s on a quiet pass: %r" % (job, last))
        undeclared_ran = [j for j, v in last.items() if v == "owed:undeclared"]
        self.assertEqual(g["quietDeclared"], 1, "the third pass alone: %r" % g)
        self.assertEqual(g["wouldSkipPass"], 0 if undeclared_ran else 1, (undeclared_ran, g))
        self.assertEqual(g["ticks"], 3)
        self.assertEqual(g["mode"], "shadow", "the knob's default is shadow")
        first = g["byJob"]["liftSpentAwaiting"]
        self.assertEqual(first["owed"], {"first": 1}, "the first pass is every job's first: no record, owed")
        self.assertEqual(first["closed"], 2)
        for job in self.declared():
            self.assertEqual(g["byJob"][job]["wrongQuiet"], {}, job)
        self.assertAlmostEqual(g["closedMs"], sum(r["closedMs"] for r in g["byJob"].values()), places=6,
                               msg="the pass's closed wall is its jobs' closed walls")
        self.assertAlmostEqual(g["wouldRunCpuMs"] + g["closedCpuMs"], sum(r["runCpuMs"] + r["closedCpuMs"] for r in g["byJob"].values()),
                               places=6, msg="every job's run lands on one side or the other")
        if g["wouldSkipPass"]:
            self.assertLessEqual(g["wouldSkipPassCpuMs"], g["passCpuMs"])

    def test_the_shadow_never_skips_a_job_it_calls_closed(self):
        """The job runs whatever the verdict: a counting death sweep is called on every pass, closed or not."""
        km = self.km
        self.add_session()
        calls = []
        with mock.patch.object(km, "_death_sweep_tick", lambda now, live_map: calls.append(now)):
            self.quiet()
            self.passes(2)
        self.assertEqual(self.last()["deathSweep"], "closed")
        self.assertEqual(len(calls), 5, "called on all five passes, the four it was called closed on included")


class Marks(_GateLab):
    """T3. A single-session mark opens exactly the jobs that read that session's files (with reason `files`), even when the
    re-stat looks the same; a box-wide mark with nothing written re-stats and opens nothing (the design critique's F6: every
    in-process writer marks every session, so a box-wide mark carries no session's news), and both are counted."""

    FILE_READERS = {"liftSpentAwaiting", "autoNudge", "interruptBlock", "autoPauseOnSpend", "spendGuard", "autoResumeRetry",
                    "autoRetry", "idleQueueDrive"}

    def test_a_single_session_mark_opens_exactly_its_file_readers(self):
        km = self.km
        self.add_session()
        self.quiet()
        g0 = self.gate()
        km._files_stat_mark(SID)
        g = self.passes()
        last = self.last()
        opened = {j for j, v in last.items() if v != "closed" and j in km._JOBS_INPUTS}   # (an undeclared job is owed every pass)
        self.assertEqual(opened, self.FILE_READERS, last)
        for job in self.FILE_READERS:
            self.assertEqual(last[job], "open:files", job)
        for job in ("usagePoll", "retryUpgrade", "deathSweep", "endOnIdle", "autoPauseOnLimit", "persistTickSeen", "heldWorking",
                    "deferralSweep", "clearDoneNotes", "autoResumeSession"):
            self.assertEqual(last[job], "closed", "%s reads none of the session's files (or has nothing to do): %r" % (job, last))
        self.assertEqual(g["marks"]["session"] - g0["marks"]["session"], 1)
        expected = {j for j, s in km._JOBS_INPUTS.items()
                    if any(c.startswith("files:") for c in s["inputs"]) and s.get("active") is None}
        self.assertEqual(expected, self.FILE_READERS, "the table's own file readers, the ones always active")

    def test_a_box_wide_mark_with_nothing_written_opens_nothing(self):
        km = self.km
        self.add_session()
        self.quiet()
        g0 = self.gate()
        km._files_stat_mark()
        g = self.passes()
        self.assertEqual({j for j, v in self.last().items() if v != "closed" and j in km._JOBS_INPUTS}, set(), self.last())
        self.assertEqual(g["marks"]["all"] - g0["marks"]["all"], 1, "the box-wide mark is counted under marks.all")
        self.assertEqual(g["marks"]["session"], g0["marks"]["session"])

    def test_a_ledger_write_opens_its_readers_and_not_a_job_that_reads_only_its_own_row(self):
        """The walk reads the whole ledger; the interrupt tick reads only its session's intrBlocked row and the deferral sweep only
        its deferred map, so a ledger write that touches neither leaves both closed."""
        km = self.km
        self.add_session()
        self.quiet()
        (self.root / "auto-nudge.json").write_text(json.dumps({"enabled": False, "compactSuggested": {SID: [1]}}))
        self.passes()
        last = self.last()
        self.assertEqual(last["autoNudge"], "open:ledger", last)
        self.assertEqual(last["interruptBlock"], "closed", last)
        self.assertEqual(last["deferralSweep"], "closed", last)


class Clocks(_GateLab):
    """T4 and F8: a due is consumed by a run at or after it, and the first pass at or after a noted due opens the job."""

    def test_the_usage_poll_is_closed_before_its_interval_and_open_at_it(self):
        km = self.km
        self.add_session()
        km._usage_poll_last[0] = float(T0)
        with mock.patch.object(km, "_usage_poll_tick", _ORIGINALS["_usage_poll_tick"]):    # the real tick (no backend: no poke)
            self.passes(1, T0 + 10)
            self.passes(1, T0 + 11)
            self.passes(1, T0 + km.USAGE_POLL_SECS - 1)
            self.assertEqual(self.last()["usagePoll"], "closed", "one second before its interval")
            self.passes(1, T0 + km.USAGE_POLL_SECS)
            self.assertEqual(self.last()["usagePoll"], "open:due", "at it")
            self.assertEqual(km._usage_poll_last[0], T0 + km.USAGE_POLL_SECS, "and the real tick ran its poll")
            self.passes(1, T0 + km.USAGE_POLL_SECS + 1)
            self.assertEqual(self.last()["usagePoll"], "closed", "the run at the due consumed it")

    def test_the_walk_is_open_at_a_recorded_flip_and_not_one_pass_before(self):
        """The walk's memo for the session records a clock leg's flip; the look skips until it, noting the flip as the walk's due."""
        km = self.km
        self.add_session()
        s = dict(self.sessions[0])
        self.addCleanup(km._NUDGE_LOOK_ASKERS.pop, SID, None)            # the look's own side map, keyed by this private sid
        key = km._nudge_look_stat(s)[0]
        km._TICK_SEEN[("auto-nudge", SID)] = tuple(key) + (float(T0 + 50), "walk-completed")
        looks = []
        with mock.patch.object(km, "_auto_nudge_tick", lambda now, live_map: looks.append(km._nudge_look_check(dict(s), now))):
            self.quiet(T0)
            self.assertTrue(looks[-1][0], "the look skips on the memo: %r" % (looks[-1],))
            self.passes(1, T0 + 49)
            self.assertEqual(self.last()["autoNudge"], "closed", "one second before the flip")
            self.passes(1, T0 + 50)
            self.assertEqual(self.last()["autoNudge"], "open:due", "at the flip")
            self.assertFalse(looks[-1][0], "where the look itself refuses the memo (clockDue)")

    def test_a_due_noted_in_the_past_is_consumed_and_holds_nothing_open(self):
        """F8's first reading would hold a job open forever on a due left in the past (an on-you error's rung); the run at or
        after a due consumes it."""
        km = self.km
        self.add_session()
        with mock.patch.object(km, "_auto_retry_tick", lambda now, live_map: km._jobs_gate_due(now - 5)):
            self.quiet(T0)
            self.passes(1, T0 + 1)
        self.assertEqual(self.last()["autoRetry"], "closed")

    def test_a_memo_flip_already_passed_keeps_the_walk_owed(self):
        """F8's second reading, the walk's form: a look that records a flip at or before its own pass leaves a memo that refuses
        to serve (clockDue) until a later look records anew, so the walk is owed, never closed on a due its run consumed."""
        km = self.km
        self.add_session()
        with mock.patch.object(km, "_auto_nudge_tick", lambda now, live_map: km._jobs_gate_flip(now - 1)):
            self.quiet(T0)
        self.assertEqual(self.last()["autoNudge"], "owed:clockDue")

    def test_a_clock_note_outside_any_look_is_the_walks_due(self):
        """F7: the walk's tail notes clocks outside a look (the wake outcomes' lost-send dead-man); the gate takes them."""
        km = self.km
        self.add_session()
        with mock.patch.object(km, "_auto_nudge_tick", lambda now, live_map: km._nudge_clock(T0 + 30)):
            self.quiet(T0)
            self.passes(1, T0 + 29)
            self.assertEqual(self.last()["autoNudge"], "closed")
            self.passes(1, T0 + 30)
            self.assertEqual(self.last()["autoNudge"], "open:due")


class PlantedMiss(_GateLab):
    """T5, the detector checked on a planted miss: a goal store written behind the kernel's back (no mark, the standing stat
    inside the floor). The gate calls the lift closed, the lift's own fresh fingerprint moves and it loads, and wrongQuiet counts
    exactly one with evidence `load`. The floor's re-stat then finds the store moved (floorCaught counts the position), and the
    same write through the in-process writer marks the session: the gate calls the lift open and wrongQuiet stays one. If the
    shadow shows zero wrongQuiet on a day this test shows it catching a planted one, the zero means something."""

    def _world(self):
        km = self.km
        self.add_session()
        p = mock.patch.multiple(km, _lift_spent_awaiting=_ORIGINALS["_lift_spent_awaiting"],
                                _auto_nudge_tick=lambda now, live_map: [km._session_files_stat(s) for s in km._alive_sessions(now, live_map)])
        p.start()                       # the real lift; a walk that takes the pass's shared snapshot (as the real walk's key does)
        self.addCleanup(p.stop)

    def test_an_unmarked_store_write_is_a_wrong_quiet_the_floor_catches_and_the_writer_marks(self):
        km, jd = self.km, self.km.jd
        self._world()
        self.quiet()
        lift = self.gate()["byJob"]["liftSpentAwaiting"]
        self.assertEqual((self.last()["liftSpentAwaiting"], lift["wrongQuiet"]), ("closed", {}), "quiet first")
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({"nodes": {}, "rev": 1}))   # behind the kernel's back
        g = self.passes()
        self.assertEqual(self.last()["liftSpentAwaiting"], "closed", "the standing stat stands: the gate saw nothing")
        self.assertEqual(g["byJob"]["liftSpentAwaiting"]["wrongQuiet"], {"load": 1}, "the lift's own fingerprint moved: %r" % g["byJob"]["liftSpentAwaiting"])
        self.assertEqual(g["wrongQuietLast"][-1]["job"], "liftSpentAwaiting")
        self.assertEqual(g["floorCaught"], {}, "the floor has not passed")
        key = (SID, self.sessions[0]["path"])
        km._FILES_STAT_STANDING[key][1] -= km.NUDGE_FLOOR_S + 1        # the floor passes
        g = self.passes()
        self.assertEqual(g["floorCaught"], {"store": 1}, "the floor's re-stat found the store moved with no mark")
        self.assertEqual(self.last()["liftSpentAwaiting"], "open:files")
        self.assertEqual(g["byJob"]["liftSpentAwaiting"]["wrongQuiet"], {"load": 1})
        self.passes()
        writes = dict(jd.SESSION_FILE_WRITES["by"])
        store = jd.load_goals(SID)
        store["note"] = "an in-process write"
        jd.save_goals(SID, store)                                       # through the writer: the judge module counts it
        self.assertNotEqual(dict(jd.SESSION_FILE_WRITES["by"]).get(SID), writes.get(SID), "the writer counted its write")
        g = self.passes()
        self.assertEqual(self.last()["liftSpentAwaiting"], "open:files", "the observer marked the session")
        self.assertEqual(g["byJob"]["liftSpentAwaiting"]["wrongQuiet"], {"load": 1}, "no second wrongQuiet")
        self.assertEqual(g["floorCaught"], {"store": 1})

    def test_a_postal_write_opens_the_lift_by_its_declared_input(self):
        """The lift's fingerprint carries the postal log: a message that lands opens it by that input, so its load is no miss.
        (A mutant that drops box:postal from the lift's declaration turns this load into a wrongQuiet.)"""
        self._world()
        self.quiet()
        (self.root / "timeline").mkdir(exist_ok=True)
        (self.root / "timeline" / "messages.jsonl").write_text('{"kind": "question"}\n')
        g = self.passes()
        self.assertEqual(g["byJob"]["liftSpentAwaiting"]["wrongQuiet"], {}, "the gate saw the input the lift read: %r" % g["byJob"]["liftSpentAwaiting"])
        self.assertEqual(self.last()["liftSpentAwaiting"], "open:postal")


class Owed(_GateLab):
    """T6 and F4: work a job owes the next pass keeps it open with no event, and a fault leaves no record."""

    def test_the_held_working_belt_is_owed_while_its_held_set_stands(self):
        """The instructive exception: the belt's at-rest rule is relative to the previous pass, so after the last change of the
        held session's transcript nothing moves, and only an owed rule brings the belt back to read the file at rest."""
        km = self.km
        self.add_session()
        self.quiet()
        km._held_working[SID] = {"since": T0, "stat": None, "parsed": None, "said": False}
        self.passes()
        self.assertEqual(self.last()["heldWorking"], "open:held", "the held set moved")
        self.passes()
        self.assertEqual(self.last()["heldWorking"], "owed:held", "nothing moved, and the belt is still owed")
        km._held_working.clear()
        self.passes(2)
        self.assertEqual(self.last()["heldWorking"], "closed")

    def test_a_job_that_raises_leaves_no_record_and_is_evaluated_next_pass(self):
        km = self.km
        self.add_session()
        self.quiet()
        fail = [True]

        def sweep(now, live_map):
            if fail[0]:
                raise RuntimeError("a synthetic sweep fault")
        with mock.patch.object(km, "_death_sweep_tick", sweep), redirect_stderr(io.StringIO()):
            g = self.passes()
            self.assertEqual(g["byJob"]["deathSweep"]["wrongQuiet"], {"raised": 1}, "called closed, and it raised")
            fail[0] = False
            self.passes()
            self.assertEqual(self.last()["deathSweep"], "owed:fault", "the fault left no record")
            self.passes()
            self.assertEqual(self.last()["deathSweep"], "closed", "the clean run recorded")

    def test_a_noted_fault_leaves_no_record_either(self):
        """A job that notes a fault and returns (the deferral sweep's, which heals without a file write) leaves no record, as a
        raise does: the next pass is owed under `fault`, and the clean run after it records."""
        km = self.km
        self.add_session()
        once = [True]

        def sweep(now):
            if once[0]:
                once[0] = False
                km._jobs_gate_owe("fault")
        with mock.patch.object(km, "_deferral_sweep_tick", sweep):
            self.passes()
            self.assertNotIn("deferralSweep", km._JOBS_GATE["rec"], "the noted fault left no record")
            self.assertEqual(km._JOBS_GATE["lost"].get("deferralSweep"), "fault")
            self.passes()
            self.assertEqual(self.last()["deferralSweep"], "owed:fault")
            self.passes()
            self.assertEqual(self.last()["deferralSweep"], "closed", "two passes after the fault, the clean run's record stands")
            self.assertEqual(self.gate()["byJob"]["deferralSweep"]["owed"], {"first": 1, "fault": 1})

    def _gate_fault_once(self, target, job):
        """Patch the gate's `target` (_jobs_gate_predict or _jobs_gate_settle) to raise once, for `job` alone. The settle's fault
        lands after the real settle wrote its record, so the test also shows that no record a faulted settle wrote stands."""
        km = self.km
        real = getattr(km, target)
        once = [True]

        def faulty(run_or_name, *rest):
            hit = (run_or_name if isinstance(run_or_name, str) else run_or_name["job"]) == job and once[0]
            if hit and target == "_jobs_gate_settle":
                real(run_or_name, *rest)
            if hit:
                once[0] = False
                raise RuntimeError("a synthetic gate fault")
            return real(run_or_name, *rest)
        p = mock.patch.object(km, target, faulty)
        p.start()
        self.addCleanup(p.stop)

    def test_a_gate_fault_leaves_the_run_unobserved_and_no_record(self):
        """The gate's own fault, at the job's turn (the observation) or after its run (the settle): the job still runs, the pass
        reads `unobserved` and counts a gateFault, and no record stands, since what the run's hooks said never reached one; so
        the next pass is owed under gateFault (a record left standing would call it closed), and the clean run after it records."""
        km = self.km
        self.add_session()
        calls = []
        with mock.patch.object(km, "_death_sweep_tick", lambda now, live_map: calls.append(now)):
            self.quiet()
            for n, target in enumerate(("_jobs_gate_predict", "_jobs_gate_settle"), 1):
                self.assertEqual(self.last()["deathSweep"], "closed", target)
                self._gate_fault_once(target, "deathSweep")
                g = self.passes()
                self.assertEqual((self.last()["deathSweep"], g["gateFaults"]), ("unobserved", n), target)
                self.assertNotIn("deathSweep", km._JOBS_GATE["rec"], "%s: the unobserved run left no record" % target)
                self.passes()
                self.assertEqual(self.last()["deathSweep"], "owed:gateFault", target)
                self.passes()
        self.assertEqual(self.last()["deathSweep"], "closed")
        self.assertEqual(len(calls), 9, "the job ran on every pass, the unobserved ones included")

    def test_an_evaluation_left_unrecorded_is_owed_and_a_recorded_one_is_not(self):
        """Derived from the job's own memo, mechanically: an evaluation its memo did not skip and its done did not record (a
        parse that raised, an unproved key) leaves the job owed; once recorded, the job closes."""
        km = self.km
        self.add_session()
        s = dict(self.sessions[0])
        record = [False]

        def tick(now, live_map):
            skip, st = km._tick_job_check("interrupt-block", s)
            if not skip and record[0]:
                km._tick_job_done("interrupt-block", s, st)
        km._TICK_SEEN.pop(("interrupt-block", SID), None)
        with mock.patch.object(km, "_interrupt_block_tick", tick):
            self.quiet()
            self.assertEqual(self.last()["interruptBlock"], "owed:unrecorded")
            record[0] = True
            self.passes(2)
            self.assertEqual(self.last()["interruptBlock"], "closed")
        km._TICK_SEEN.pop(("interrupt-block", SID), None)

    def test_a_death_the_dead_wait_sweep_stood_down_on_keeps_the_walk_owed(self):
        km = self.km
        self.add_session()
        self.quiet()
        km._PREV_ALIVE = {SID, GONE}
        self.passes()
        self.assertEqual(self.last()["autoNudge"], "owed:deadWait")


class Inputs(_GateLab):
    """The critique's input findings: the deferral sweep's in-memory and file inputs (F3), the declared row fields (F5), the
    credential store (F9)."""

    def test_the_deferral_sweep_reads_the_judge_registry_and_the_pause_file_while_it_holds_records(self):
        km, jd = self.km, self.km.jd
        self.add_session()
        (self.root / "auto-nudge.json").write_text(json.dumps({"deferred": {SID + ":g1": {"sid": SID, "why": jd.WHY_JUDGING}}}))
        self.quiet()
        self.assertEqual(self.last()["deferralSweep"], "closed")
        jd._active_change[0] += 1                                       # a judge call began or ended, writing nothing
        self.passes()
        self.assertEqual(self.last()["deferralSweep"], "open:judgeActive")
        (self.root / "retry-paused.json").write_text(json.dumps({"paused": False}))
        self.passes()
        self.assertEqual(self.last()["deferralSweep"], "open:retryPaused")
        (self.root / "auto-nudge.json").write_text(json.dumps({"deferred": {}}))
        self.passes(2)
        self.assertEqual(self.last()["deferralSweep"], "closed")
        jd._active_change[0] += 1
        self.passes()
        self.assertEqual(self.last()["deferralSweep"], "closed", "with no record standing the sweep reads its map alone")

    def test_a_row_field_no_job_reads_is_no_input_and_a_declared_one_is(self):
        """F5: a fallen-back session's model-fallback row carries a retry countdown that moves every second; a fingerprint of the
        whole row would make every row reader's input a clock."""
        km = self.km
        self.add_session()
        self.quiet()
        for n in (30, 29, 28):
            self.live[SID]["modelFallback"] = {"retry": {"nextIn": n}}
            self.passes()
            self.assertEqual(self.last()["autoRetry"], "closed", "the countdown at %d" % n)
        self.live[SID]["state"] = "working"
        self.passes()
        self.assertIn(self.last()["autoRetry"], ("open:files", "open:rows"),
                      "a declared field moved (the prelude's row observer also marks the session, which opens its files first)")

    def test_a_logout_opens_the_limit_pause_and_its_lift(self):
        """F9: the limit pause reads the account through the credential store; a logout lifts it with nothing else moving."""
        km = self.km
        self.add_session()
        acct = self.root / "account.json"
        with mock.patch.object(km, "JOBS_GATE_ACCOUNT_FILE", str(acct)):
            self.quiet()
            acct.write_text("{}")
            self.passes()
        last = self.last()
        self.assertEqual((last["autoPauseOnLimit"], last["autoResumeRetry"]), ("open:account", "open:account"), last)


class Detector(_GateLab):
    """The evidence: an act is a wrongQuiet; a memo that moved is one only when none of the job's declared inputs moved while it
    ran, else it raced (the next pass would have opened the job: later, not lost)."""

    def _walk(self, fn):
        p = mock.patch.object(self.km, "_auto_nudge_tick", fn)
        p.start()
        self.addCleanup(p.stop)

    def test_a_memo_that_moved_with_no_input_moving_is_wrong_and_with_one_moving_raced(self):
        km = self.km
        self.add_session()
        mode = ["quiet"]
        clears = self.root / "cleared.jsonl"

        def walk(now, live_map):
            if mode[0] == "miss":
                km._jobs_gate_note("miss")
            elif mode[0] == "race":
                clears.write_text('{"id": "g1", "op": "clear"}\n')           # another writer's clear lands while the walk runs
                km._jobs_gate_note("miss")
            elif mode[0] == "act":
                clears.write_text('{"id": "g2", "op": "clear"}\n')
                km._jobs_gate_note("views")
        self._walk(walk)
        self.quiet()
        mode[0] = "miss"
        g = self.passes()
        self.assertEqual(g["byJob"]["autoNudge"]["wrongQuiet"], {"miss": 1})
        mode[0] = "quiet"
        self.passes()
        mode[0] = "race"
        g = self.passes()
        self.assertEqual((g["byJob"]["autoNudge"]["wrongQuiet"], g["byJob"]["autoNudge"]["raced"]), ({"miss": 1}, {"miss": 1}))
        mode[0] = "quiet"
        self.passes(2)
        mode[0] = "act"
        g = self.passes()
        self.assertEqual(g["byJob"]["autoNudge"]["wrongQuiet"], {"miss": 1, "views": 1}, "an act is never raced")


class ThePusher(_GateLab):
    def test_the_pushers_jobs_record_nothing_under_the_gate(self):
        """T7: the gate observes the jobs thread's pass alone; the pusher's _job_stage calls are never observed (the cycle with no
        session alive, as tests/test_jobs_thread_split.py drives it)."""
        km = self.km
        with redirect_stderr(io.StringIO()):
            km._pusher_cycle()
        g = self.gate()
        self.assertEqual((g["ticks"], g["byJob"], self.last()), (0, {}, {}))


class TheKnob(_GateLab):
    def test_off_observes_nothing_on_reads_as_shadow_and_a_flip_forgets_the_records(self):
        km = self.km
        self.add_session()
        self.quiet()
        knob = self.root / km.JOBS_GATE_FILE
        knob.write_text("off\n")
        g = self.passes(2)
        self.assertEqual((g["mode"], g["asked"], g["ticks"]), ("off", "off", 3), "off: no pass observed")
        knob.write_text("on\n")
        g = self.passes()
        self.assertEqual((g["mode"], g["asked"], g["ticks"]), ("shadow", "on", 4), "on reads as shadow in this stage")
        self.assertEqual(self.last()["deathSweep"], "owed:first", "nothing was watched while off: every record went")
        knob.unlink()
        g = self.passes()
        self.assertEqual((g["mode"], g["asked"]), ("shadow", ""), "no file: the default, shadow")


class Pace(unittest.TestCase):
    """F1: the projection never assumes passes per hour stay fixed. Skipping work shortens the period, so whatever still runs
    every pass runs more often, and a job owed on every pass is counted at the faster pace."""

    def test_the_projected_pace_rises_as_closed_runs_leave_the_pass(self):
        km = kernel_module()
        g = km._PerfStats._gate_zero()
        g.update(ticks=10, passMs=15000.0, passCpuMs=10000.0, tickMs=50.0, tickCpuMs=50.0, closedMs=10000.0, closedCpuMs=8000.0)
        g["byJob"]["spendGuard"] = {"ticks": 10, "open": {}, "owed": {"canSpend": 10}, "closed": 0, "closedMs": 0.0,
                                    "closedCpuMs": 0.0, "runMs": 2000.0, "runCpuMs": 1000.0, "wrongQuiet": {}, "raced": {}}
        r = km._jobs_gate_report(g)
        rest = km.JOBS_PASS_S * 1000.0
        self.assertAlmostEqual(r["today"]["periodMs"], (15000.0 - 50.0) / 10 + rest, places=1)
        self.assertAlmostEqual(r["projected"]["periodMs"], (15000.0 - 10000.0) / 10 + rest, places=1)
        self.assertGreater(r["projected"]["passesPerHour"], r["today"]["passesPerHour"], "the enforcing loop's passes come faster")
        owed = r["byJob"]["spendGuard"]["projectedCpuMsPerHour"]
        self.assertAlmostEqual(owed, 100.0 * r["projected"]["passesPerHour"], delta=1.0, msg="the owed job at the projected pace")
        self.assertGreater(owed, 100.0 * r["today"]["passesPerHour"], "more than the same runs at today's pace")


class TreeFloor(_GateLab):
    """The spend guard's sixty-second floor, simulated: the user set the check for agent-file writes no event announces at once a
    minute once the gate enforces; the shadow reports how often that floor would have caught one, and how late."""

    def test_an_unsignaled_agent_file_move_is_caught_by_the_floor_or_a_later_signal(self):
        km = self.km
        tx = self.add_session()
        move = [False]
        with mock.patch.object(km, "_spend_guard_tick", lambda now, live_map: km._jobs_gate_tree(str(tx)) if move[0] else None):
            self.passes(1, T0)
            self.passes(1, T0 + 1)                                      # the guard's first run: the floor's clock starts
            self.passes(1, T0 + 2)
            move[0] = True
            self.passes(1, T0 + 10)                                     # the poll finds a move no signal announced
            move[0] = False
            self.passes(1, T0 + 30)
            g = self.passes(1, T0 + 61)                                 # the floor's check, sixty seconds after its last stat
            t = g["treeFloor"]
            self.assertEqual((t["unsignaled"], t["caught"], t["checks"], t["floorS"]), (1, 1, 1, 60), t)
            self.assertEqual(t["lagMsMax"], 51000.0, "found by the floor 51 s after the per-pass poll found it")
            move[0] = True
            self.passes(1, T0 + 70)
            move[0] = False
            self.live[SID]["state"] = "working"                         # a signal for the session before the floor comes
            g = self.passes(1, T0 + 75)
        t = g["treeFloor"]
        self.assertEqual((t["unsignaled"], t["signalCaught"], t["caught"]), (2, 1, 1), t)
        self.assertEqual(t["lagMsSum"], 51000.0 + 5000.0)


_ORIGINALS = {}


def setUpModule():
    km = kernel_module()
    for name in ("_lift_spent_awaiting", "_usage_poll_tick"):
        _ORIGINALS[name] = getattr(km, name)


if __name__ == "__main__":
    unittest.main()
