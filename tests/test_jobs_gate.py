#!/usr/bin/env python3
"""The jobs pass's gate in SHADOW (stage one of the event-driven jobs pass, 2026-10-05). The jobs loop rests JOBS_PASS_S after each
pass and then runs every job again, whether or not anything a job reads changed, so a cheaper job buys more passes, not less CPU.
The gate will run a job only when one of its declared inputs moved, a due it noted came, or it owes work. This stage observes each
job's inputs at its turn, says what an enforcing gate would have done, runs the job exactly as before, and counts every job it
called closed that acted anyway or found its own memo moved (a wrongQuiet: a signal the job's declaration lacks).

Every test drives the REAL _jobs_cycle over a private synthetic state root (session-hosts written off), the jobs stubbed quiet
(tests/test_jobs_thread_split.py's QUIET list) unless a test speaks for one, and the module state the gate's predicates read set to
a quiet world and restored after. A test that speaks for a hook calls the REAL function that carries it (a stubbed job calling the
real act, the real job over a fake backend, the real fault road), so a hook made a no-op turns that test red. Synthetic sids, a
notes-api session named api, nothing recorded from a real machine."""
import ast
import inspect
import io
import json
import math
import os
import re
import shutil
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
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
MANAGER = "11111111-2222-3333-4444-00009a7e0005"   # the peer that delegated api's goal (a relayed question goes there)
ASKER = "11111111-2222-3333-4444-00009a7e0006"     # a peer whose open ask the debt ladder reminds api of
T0 = 1_800_000_000                                 # a synthetic clock for the tests that move it


def _delta(now, before):
    """The counters of an {evidence: n} map that grew between two copies of it."""
    return {k: n - before.get(k, 0) for k, n in now.items() if n != before.get(k, 0)}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def _node(nid, text, parent=None, t=T0, **kw):
    """A goal node in the store's own shape (tests/test_peer_wait_blocks.py builds its stores the same way)."""
    d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False, "trail": [],
         "t": t, "mt": t, "log": []}
    d.update(kw)
    return d


class _FakeBackend:
    """A backend double that records every hand-off the jobs make to it (the spend guard's interrupt and message, the usage
    poll, the retry upgrade, the API retry's send, the idle drive), so a test sees the act the gate should have heard."""

    def __init__(self):
        self.calls = []
        self.queued = []

    def kinds(self):
        return [c[0] for c in self.calls]

    def send(self, sid, text):
        self.calls.append(("send", sid))
        return True

    def refresh_usage(self):
        self.calls.append(("refresh_usage",))

    def retry_model_upgrades(self, now):
        self.calls.append(("retry_model_upgrades",))

    def drive_idle_queue(self, cands):
        self.calls.append(("drive_idle_queue", tuple(c["sid"] for c in cands)))

    def interrupt(self, sid):
        self.calls.append(("interrupt", sid))
        return False

    def pending_queued(self, sid):
        return list(self.queued)

    def owns(self, sid):
        return True


class _LabLive:
    """A backend as the race read asks it for its live sessions (_jg_live_rows): the lab's live map in a backend's own shape (a
    since as the SDK backend ships it, a string), or a raise while `fail` holds an exception."""

    def __init__(self, test):
        self.test, self.fail, self.reads = test, None, 0

    def live_sessions(self):
        self.reads += 1
        if self.fail is not None:
            raise self.fail
        return {sid: {"state": row.get("state", ""), "since": str(row.get("since") or "")} for sid, row in self.test.live.items()}

    def owns(self, sid):
        return False


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
        self.assertEqual(set(km._JOBS_UNDECLARED), {"beginCheckpointCycle", "convergeCheckpoints"},
                         "the checkpoint cycle and the converge pass alone are owed every pass, wherever they run")
        write = re.findall(JOB_NAME_RE, inspect.getsource(km._document_write_phase))
        self.assertEqual(set(write) - {"settleWrites"}, set(km._JOBS_UNDECLARED),
                         "they run on the document writer's thread (the review of PR 155), where nothing is gated, beside the "
                         "settle writes the pass hands it")
        self.assertFalse(set(jobs) & set(write), "neither is a job of the gated pass")
        self.assertEqual([j for j in jobs if j not in km._JOBS_INPUTS], [],
                         "every job of the jobs pass is declared, so a quiet pass can count under wouldSkipPass")
        self.assertEqual((jobs[0], jobs[-1]), ("checkpointFirstSight", "persistCheckpoints"),
                         "the settle write's first sight opens the pass and the write closes it, both declared")

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
        """The gate stats a session's six own files by the same paths as _session_files_stat (outside a pass both stat fresh).
        All six exist, each with a size of its own, so a wrong path for any one of them reads zeros there and differs."""
        km = self.km
        root = Path(tempfile.mkdtemp())
        (root / "session-hosts").write_text("off\n")                 # a test that mints its own state root pins session hosts off
        self.addCleanup(shutil.rmtree, root, True)
        saved = km.jd.STATE
        km.jd._rebind_state(root)
        self.addCleanup(km.jd._rebind_state, saved)
        jd = km.jd
        paths = [root / "projects" / (SID + ".jsonl"), jd.STATE / "states" / (SID + ".jsonl"), jd.GOALDIR / (SID + ".json"),
                 jd._overrides_dir() / (SID + ".jsonl"), jd.GOALARCHDIR / (SID + ".json"), jd.EPIDIR / (SID + ".jsonl")]
        for n, p in enumerate(paths):
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x" * (3 + 4 * n))
        s = {"sid": SID, "path": str(paths[0])}
        six = km._jobs_gate_six(s)
        self.assertEqual(six[1::2], tuple(3 + 4 * n for n in range(6)), "each of the six read at its own path")
        self.assertEqual(six, tuple(km._session_files_stat(s)[:12]))


class FaultCensus(unittest.TestCase):
    """Point three of the review: an except in a declared job's road that lets the job go on (it continues, returns or falls
    through) owes the next pass (a _jobs_gate_owe call: "fault", which also leaves no record as after a raise, or the road's
    own reason, as the relay's per-store except owes "relay"), or is named in HEALED with what brings the job back without one: an owed predicate, an input whose stat moves when
    the fault heals, or a base that never retries the work either. The road is the function the job's thunk calls and, for the
    walk and the API retry, the functions their bodies hand each pass to. A new except in a road is a hit until it does one or
    the other; an except that re-raises is the job's own raise, which the gate already hears."""

    ROADS = {
        "liftSpentAwaiting": ("_lift_spent_awaiting",), "deathSweep": ("_death_sweep_tick",), "endOnIdle": ("_end_on_idle_sweep",),
        "deferralSweep": ("_deferral_sweep_tick",),
        "autoNudge": ("_auto_nudge_tick", "_auto_nudge_pass", "_relay_tick", "_relay_store", "_debt_backstop_tick",
                      "_dead_wait_sweep", "_awaiting_wake_outcomes"),
        "interruptBlock": ("_interrupt_block_tick",), "persistTickSeen": ("_persist_tick_seen",),
        "persistIntrMarks": ("_persist_intr_marks",), "persistSpendTrees": ("_persist_spend_trees",),
        "autoPauseOnLimit": ("_auto_pause_on_limit",), "usagePoll": ("_usage_poll_tick",), "retryUpgrade": ("_retry_upgrade_tick",),
        "autoPauseOnSpend": ("_auto_pause_on_spend_limit",), "spendGuard": ("_spend_guard_tick",),
        "autoResumeRetry": ("_auto_resume_retry",), "autoResumeSession": ("_auto_resume_session_retry",),
        "autoRetry": ("_auto_retry_tick", "_fire_api_retry"), "idleQueueDrive": ("_idle_queue_drive_tick",),
        "clearDoneNotes": ("_clear_done_working_notes",), "heldWorking": ("_held_working_pass",),
        "checkpointFirstSight": ("_checkpoint_first_sight",), "persistCheckpoints": ("_persist_checkpoints", "_ckpt_settle_write"),
    }
    UNRECORDED = "the evaluation its tick check opened is never recorded: the job is owed under unrecorded"
    DIRTY = "the memo stays dirty: the persist is owed under dirty"
    HEALED = {   # (function, exception type, its ordinal among that function's swallowing handlers of that type) -> what heals it
        ("_lift_spent_awaiting", "Exception", 0): "the raised ruling forgets its fingerprint: the lift is owed under unruled",
        ("_end_on_idle_sweep", "Exception", 0): "the request stands: the sweep is owed every pass while one does (requests)",
        ("_auto_nudge_pass", "OSError", 0): "the postal log's stat: absent reads as empty, and box:postal is the walk's own input",
        ("_relay_tick", "Exception", 0): "a malformed entry is dropped, its unlink noted as a write: nothing is left to retry",
        ("_relay_tick", "FileNotFoundError", 0): "a temp file gone by its stat (renamed into its entry, or swept): nothing to retry",
        ("_relay_store", "Exception", 0): "an entry's fault makes its store's pass not quiet: the walk is owed under relay",
        ("_dead_wait_sweep", "OSError", 0): "the boot's catch-up listing: the transition is spent at the base too",
        ("_dead_wait_sweep", "Exception", 0): "a peer-death conversion: the death transition is spent at the base too",
        ("_dead_wait_sweep", "Exception", 1): "a candidate's conversion: the death transition is spent at the base too",
        ("_interrupt_block_tick", "Exception", 0): UNRECORDED + " (with no key, an unproved ledger: ledgerFault)",
        ("_persist_tick_seen", "Exception", 0): DIRTY, ("_persist_tick_seen", "OSError", 0): DIRTY,
        ("_persist_intr_marks", "Exception", 0): DIRTY, ("_persist_intr_marks", "OSError", 0): DIRTY,
        ("_persist_spend_trees", "RuntimeError", 0): DIRTY, ("_persist_spend_trees", "Exception", 0): DIRTY,
        ("_persist_spend_trees", "OSError", 0): DIRTY, ("_persist_spend_trees", "OSError", 1): DIRTY,
        ("_persist_spend_trees", "Exception", 1): DIRTY,
        ("_spend_guard_tick", "Exception", 0): "the tree memos' sweep runs once a kernel life: spent at the base too",
        ("_auto_resume_retry", "OSError", 0): "the transcript's stat: files:transcript is the job's own input",
        ("_fire_api_retry", "Exception", 0): "a backend with no live queue falls through to the send, an act",
        ("_clear_done_working_notes", "Exception", 0): UNRECORDED,
        ("_held_working_pass", "OSError", 0): "the belt is owed every pass while a hold stands (held)",
        ("_held_working_pass", "Exception", 0): "the belt is owed every pass while a hold stands (held)",
        ("_held_working_pass", "Exception", 1): "the belt is owed every pass while a hold stands (held)",
        ("_ckpt_settle_write", "Exception", 0): "an assembly document that failed: the settle is recorded as written all the same, "
                                                "so the base never retries it before the next settle or period write either (the "
                                                "write itself runs on the document writer's thread while the loops run: review of "
                                                "PR 155)",
    }
    OWING = {   # the swallowing handlers that owe a fault: the review's seven first, then the census's own finds and the earlier fold's
        ("_auto_nudge_pass", "Exception", 0), ("_auto_nudge_pass", "Exception", 1), ("_auto_nudge_pass", "Exception", 2),
        ("_auto_nudge_pass", "Exception", 3), ("_awaiting_wake_outcomes", "Exception", 0), ("_auto_pause_on_limit", "Exception", 0),
        ("_auto_resume_retry", "Exception", 0),
        ("_relay_tick", "OSError", 0), ("_spend_guard_tick", "Exception", 1), ("_fire_api_retry", "Exception", 1),
        ("_relay_tick", "Exception", 1), ("_deferral_sweep_tick", "Exception", 0), ("_deferral_sweep_tick", "Exception", 1),
        ("_auto_retry_tick", "Exception", 0), ("_idle_queue_drive_tick", "Exception", 0),
    }

    @staticmethod
    def _handlers(fn):
        """Every except handler in `fn`'s own body (never a nested function's), in source order."""
        top = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
        out = []

        def visit(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    continue
                if isinstance(child, ast.ExceptHandler):
                    out.append(child)
                visit(child)
        visit(top)
        return sorted(out, key=lambda h: h.lineno)

    def test_every_swallowing_except_in_a_declared_jobs_road_owes_a_fault_or_is_named(self):
        km = kernel_module()
        thunks = dict(re.findall(r"_job_stage\('(\w+)', lambda: (\w+)\(", inspect.getsource(km._jobs_pass)))
        self.assertEqual(set(self.ROADS), set(km._JOBS_INPUTS), "a road for every declared job")
        for job, fns in self.ROADS.items():
            self.assertEqual(fns[0], thunks.get(job), "%s: its road starts at the function its thunk calls" % job)
        named, owing = {}, set()
        for job, fns in self.ROADS.items():
            for name in fns:
                fn = getattr(km, name)
                at = inspect.getsourcelines(fn)[1]
                seen = {}
                for h in self._handlers(fn):
                    if h.body and isinstance(h.body[-1], ast.Raise):
                        continue                                   # the job's own raise: the gate hears it
                    t = ast.unparse(h.type) if h.type is not None else "bare"
                    seen[t] = seen.get(t, -1) + 1
                    key = (name, t, seen[t])
                    if any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "_jobs_gate_owe" for c in ast.walk(h)):
                        owing.add(key)
                    else:
                        named[key] = "line %d: %s" % (at + h.lineno - 1, ast.unparse(h.body[0])[:90])
        extra = {k: v for k, v in named.items() if k not in self.HEALED}
        gone = sorted(k for k in self.HEALED if k not in named)
        self.assertEqual((extra, gone), ({}, []), "swallowing excepts that neither owe a fault nor are named, and names that "
                                                    "match no except (the census as found: %r)" % sorted(named.items()))
        self.assertEqual(sorted(self.OWING - owing), [], "the excepts that must owe a fault: %r" % sorted(owing))


class _GateLab(unittest.TestCase):
    """A hermetic world for the gate: a private state root (the account file and the price config under it too), the jobs pass's
    jobs stubbed quiet, one or two synthetic sessions alive, and every piece of module state the gate's predicates and the pass's
    prelude read saved, set quiet, and restored."""

    LIST_SLOTS = ("_prev_live_sids", "_TICK_SEEN_DIRTY", "_INTR_MARKS_DISK_DIRTY", "_NUDGE_WALK_CURSOR", "_auto_nudge_paused",
                  "_usage_poll_last", "_retry_upgrade_last", "_BOOT_HEALTH_DONE", "_JOBS_GATE_MODE")
    CONTAINERS = ("_lift_seen", "_SPEND_CAN_PREV", "_SPEND_GUARD", "_SPEND_TREE_CACHE", "_held_working", "_auto_nudge_drops_pending",
                  "_FILES_STAT_STANDING", "_compact_clicked", "_auto_retried", "_auto_retry_state", "_RELAY_QUIET", "_RELAY_SAID",
                  "_interrupt_clicked", "_CKPT_SETTLE_SEEN", "_CKPT_PERIODIC_SEEN")

    def setUp(self):
        km = self.km = kernel_module()
        self.root = Path(tempfile.mkdtemp())
        (self.root / "session-hosts").write_text("off\n")            # a test that mints its own state root pins session hosts off
        self.addCleanup(shutil.rmtree, self.root, True)
        saved_state = km.jd.STATE
        km.jd._rebind_state(self.root)
        self.addCleanup(km.jd._rebind_state, saved_state)
        self.live, self.sessions, self.revs = {}, [], {}
        test = self
        stubs = {nm: (lambda *a, **k: None) for nm in QUIET}
        stubs.update(_auto_nudge_tick=lambda *a, **k: None, _retry_upgrade_tick=lambda *a, **k: None,
                     _live_map=lambda: self.live, NAMES={}, _push_all=lambda live_map=None: None,
                     _sdk=lambda: None, WORKING_DIR=self.root / "working",
                     PRICE_CONFIG=self.root / "model-prices.json", JOBS_GATE_ACCOUNT_FILE=str(self.root / "claude.json"),
                     _alive_sessions=lambda now, live_map: [dict(s) for s in self.sessions if s["sid"] in live_map])
        patch = mock.patch.multiple(km, **stubs)
        patch.start()
        self.addCleanup(patch.stop)
        for attr, fn in (("live_rev", lambda sid, be=None: test.revs.get(str(sid), 0)), ("live", lambda: test.live)):
            p = mock.patch.object(km.Sessions, attr, staticmethod(fn))   # the backends' live rows, as the next prelude reads them
            p.start()
            self.addCleanup(p.stop)
        self.backend = _LabLive(self)                                 # ... and as the race read asks each backend for them
        p = mock.patch.object(km, "_JOBS_GATE_BACKENDS", (lambda: test.backend,), create=True)   # (create: the red-first run's
        p.start()                                                     #  kernel, the review's, had no such table)
        self.addCleanup(p.stop)
        saved_slots = {nm: getattr(km, nm)[0] for nm in self.LIST_SLOTS}
        saved_boxes = {nm: (type(getattr(km, nm)), list(getattr(km, nm).items()) if isinstance(getattr(km, nm), dict)
                            else list(getattr(km, nm))) for nm in self.CONTAINERS}
        saved_misc = (km._PREV_ALIVE, dict(km._FILES_STAT_DIRTY, sids=set(km._FILES_STAT_DIRTY["sids"])),
                      dict(km._FILES_STAT_OBSERVED), dict(km._JOBS_GATE, rec={}, lost={}), dict(km._TICK_SEEN),
                      set(km._JOBS_GATE.setdefault("said", set())), set(km._JOBS_GATE.setdefault("warned", set())),
                      list(km._SDK_BOOT_PROBLEMS))

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
            km._JOBS_GATE["said"].clear(); km._JOBS_GATE["said"].update(saved_misc[5])
            km._JOBS_GATE["warned"].clear(); km._JOBS_GATE["warned"].update(saved_misc[6])
            km._SDK_BOOT_PROBLEMS[:] = saved_misc[7]
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
        # (the getattr: the red-first run drives these tests over the kernel the review ran, which had no boot key)
        km._JOBS_GATE["modeKey"], km._JOBS_GATE["asked"], km._JOBS_GATE_MODE[0] = getattr(km, "_JOBS_GATE_BOOT_KEY", None), "", "shadow"
        km._JOBS_GATE["said"].clear(); km._JOBS_GATE["warned"].clear()
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

    def rec(self, job):
        return self.km._JOBS_GATE["rec"].get(job)

    def wrong(self, job):
        return dict(self.gate()["byJob"].get(job, {}).get("wrongQuiet") or {})

    def raced(self, job):
        return dict(self.gate()["byJob"].get(job, {}).get("raced") or {})

    def quiet(self, clock=None):
        """Three passes: the first is every job's first, the second the spend guard's (it skips the boot's first pass), and the
        third is the first on which every declared job has a record."""
        return self.passes(3, clock)

    def declared(self):
        jobs = re.findall(JOB_NAME_RE, inspect.getsource(self.km._jobs_pass))
        return [j for j in jobs if j in self.km._JOBS_INPUTS]

    def stub(self, name, fn):
        p = mock.patch.object(self.km, name, fn)
        p.start()
        self.addCleanup(p.stop)

    def real(self, *names):
        """The real functions behind the lab's quiet stubs, for the jobs a test speaks for."""
        for name in names:
            self.stub(name, _ORIGINALS[name])

    def fault_once(self, name, obj=None, match=None, exc=None):
        """`obj.name` (the kernel module by default) raises once, when `match` accepts its arguments, and is itself otherwise."""
        obj = self.km if obj is None else obj
        real = getattr(obj, name)
        armed = [True]

        def faulty(*a, **k):
            if armed[0] and (match is None or match(*a, **k)):
                armed[0] = False
                raise exc or RuntimeError("a synthetic fault in %s" % name)
            return real(*a, **k)
        p = mock.patch.object(obj, name, faulty)
        p.start()
        self.addCleanup(p.stop)
        return armed

    def ledger(self, **d):
        (self.root / "auto-nudge.json").write_text(json.dumps(d))

    def real_walk(self):
        """The REAL walk (_auto_nudge_tick and its pass, the relay tick, the debt backstop, the dead-wait sweep, the wake outcomes)
        with the toggle and task tracking on (the ledger's enabled, no tracking file), each session's look stubbed quiet."""
        km = self.km
        self.ledger(enabled=True)
        self.real("_auto_nudge_tick")
        self.stub("_auto_nudge_session", lambda s, now, live_map, nudged, waitfor, alive_ids=None, wake_only=False, cleared=None,
                  reminders=None: None)
        self.stub("_compact_suggest_tick", lambda sid, tm, now: False)
        self.assertTrue(km._task_tracking_on())

    def spy(self):
        """Every settled run's (job, verdict, acts): the acts of a run the gate did not call closed are visible here alone."""
        km = self.km
        real = km._jobs_gate_settle
        seen = []

        def settle(run, tick, wall, cpu, raised):
            seen.append((run["job"], run["verdict"], dict(run["acts"])))
            return real(run, tick, wall, cpu, raised)
        self.stub("_jobs_gate_settle", settle)
        return seen

    @staticmethod
    def acts(seen, job):
        return [a for j, _v, a in seen if j == job][-1]

    def owed_fault_after(self, job, arm, clock=None):
        """From a closed record: `arm()` plants a fault for one pass; that pass leaves the job no record (lost under fault), the next
        pass is owed under fault, and the one after it is closed again."""
        km = self.km
        self.assertEqual(self.last()[job], "closed", "%s closed before the fault: %r" % (job, self.last()))
        arm()
        with redirect_stderr(io.StringIO()) as err:
            self.passes(1, clock)
        self.assertNotIn(job, km._JOBS_GATE["rec"], "%s: the faulted run left no record" % job)
        self.assertEqual(km._JOBS_GATE["lost"].get(job), "fault", job)
        self.passes(1, clock)
        self.assertEqual(self.last()[job], "owed:fault", job)
        self.passes(1, clock)
        self.assertEqual(self.last()[job], "closed", "%s: the clean run after the fault recorded" % job)
        return err.getvalue()


class AQuietWorld(_GateLab):
    def test_on_a_quiet_pass_every_declared_job_is_called_closed_and_the_pass_would_have_been_skipped(self):
        """T2. Nothing moves between passes: from the third pass on, every declared job is closed, the pass counts under
        wouldSkipPass (every job closed: every job of the jobs pass is declared, the checkpoint settle write and its first
        sight among them, so none is owed as undeclared), quietDeclared counts it too, and the closed runs' wall and CPU are
        the saving."""
        self.add_session()
        g = self.quiet()
        last = self.last()
        for job in self.declared():
            self.assertEqual(last.get(job), "closed", "%s on a quiet pass: %r" % (job, last))
        undeclared_ran = [j for j, v in last.items() if v == "owed:undeclared"]
        self.assertEqual(undeclared_ran, [], "no job of the jobs pass is undeclared: %r" % last)
        self.assertIn("persistCheckpoints", self.declared())
        self.assertEqual(g["quietDeclared"], 1, "the third pass alone: %r" % g)
        self.assertEqual(g["wouldSkipPass"], 1, "the third pass alone: %r" % g)
        self.assertEqual(g["ticks"], 3)
        self.assertEqual(g["mode"], "shadow", "the knob's default is shadow")
        first = g["byJob"]["liftSpentAwaiting"]
        self.assertEqual(first["owed"], {"first": 1}, "the first pass is every job's first: no record, owed")
        self.assertEqual(first["closed"], 2)
        for job in self.declared():
            self.assertEqual(g["byJob"][job]["wrongQuiet"], {}, job)
            self.assertEqual(g["byJob"][job]["unobserved"], 0, job)
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

    def test_the_compaction_suggestions_idle_floor_is_the_walks_due(self):
        """The real _compact_suggest_tick on a session over a token threshold that settled ten seconds ago: not idle long enough,
        it notes the instant the idle floor passes as the walk's due."""
        km = self.km
        self.add_session()
        self.live[SID]["ctxTokens"] = 500_000
        with mock.patch.multiple(km, _auto_nudge_tick=lambda now, live_map: km._compact_suggest_tick(SID, live_map.get(SID), now),
                                 _compact_suggest_on=lambda: True, _settle_event_key=lambda sid: T0 - 10):
            self.passes(1, T0)
        self.assertEqual(self.rec("autoNudge")["dues"], [T0 - 10 + km.COMPACT_SUGGEST_IDLE_S])

    def test_a_running_dispatchs_expiry_is_the_lifts_due(self):
        """The real lift over a session with one dispatch running: the first whole second past its expiry is the lift's due."""
        km = self.km
        self.add_session()
        task = {"status": "running", "id": "toolu-0001", "toolUseId": "toolu-0001", "deadline": T0 + 100}
        with mock.patch.multiple(km, _lift_spent_awaiting=_ORIGINALS["_lift_spent_awaiting"],
                                 _bg_scan_all_cached=lambda path: [dict(task)]):
            self.passes(1, T0)
        self.assertEqual(self.rec("liftSpentAwaiting")["dues"], [math.floor(km.em._bg_expiry_t(task, 120.0)) + 1])


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
        self.assertEqual(g["byJob"]["liftSpentAwaiting"]["raced"], {}, "written before the pass, which no signal announced")
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
        reads `unobserved` and counts a gateFault, the job's row counts it under `unobserved`, stderr names the job and the
        exception type once (the two faults share both), and no record stands, since what the run's hooks said never reached one;
        so the next pass is owed under gateFault (a record left standing would call it closed), and the clean run after it records."""
        km = self.km
        self.add_session()
        calls = []
        with mock.patch.object(km, "_death_sweep_tick", lambda now, live_map: calls.append(now)), \
                redirect_stderr(io.StringIO()) as err:
            self.quiet()
            for n, target in enumerate(("_jobs_gate_predict", "_jobs_gate_settle"), 1):
                self.assertEqual(self.last()["deathSweep"], "closed", target)
                self._gate_fault_once(target, "deathSweep")
                g = self.passes()
                self.assertEqual((self.last()["deathSweep"], g["gateFaults"], g["byJob"]["deathSweep"]["unobserved"]),
                                 ("unobserved", n, n), target)
                self.assertNotIn("deathSweep", km._JOBS_GATE["rec"], "%s: the unobserved run left no record" % target)
                self.passes()
                self.assertEqual(self.last()["deathSweep"], "owed:gateFault", target)
                self.passes()
        self.assertEqual(self.last()["deathSweep"], "closed")
        self.assertEqual(len(calls), 9, "the job ran on every pass, the unobserved ones included")
        self.assertEqual(err.getvalue().count("jobs gate (deathSweep)"), 1, "one line per job and exception type: %r" % err.getvalue())
        self.assertIn("RuntimeError", err.getvalue())

    def test_a_raising_cpu_write_never_leaves_the_rest_of_the_pass_unobserved(self):
        """The review's case: the stage and CPU writes ran before the gate's close in one finally, so one raising write left the
        run open on the jobs thread and every later job of the pass unobserved. The close has a finally of its own: the job
        whose write raised is settled, every later job is observed, and the pass counts no gate fault."""
        km = self.km
        self.add_session()
        self.quiet()
        real = km._PERF_STATS.job_cpu
        armed = [True]

        def job_cpu(name, cpu):
            if armed[0] and name == "liftSpentAwaiting":
                armed[0] = False
                raise RuntimeError("a synthetic CPU write fault")
            return real(name, cpu)
        g0 = self.gate()
        with mock.patch.object(km._PERF_STATS, "job_cpu", job_cpu), redirect_stderr(io.StringIO()):
            g = self.passes()
        self.assertFalse(armed[0], "the write raised")
        last = self.last()
        self.assertEqual([j for j in self.declared() if last.get(j) != "closed"], [], last)
        self.assertEqual((g["gateFaults"], g["wouldSkipPass"]), (0, g0["wouldSkipPass"] + 1), "every job closed: the pass counts")

    def test_a_turn_that_finds_a_run_still_open_is_unobserved_and_no_skip(self):
        """A job's turn on a thread whose last observed run never closed (here a stage opened inside a job, the shape a stale run
        takes) is unobserved, never observed in the other run's place, so the pass is no wouldSkipPass."""
        km = self.km
        self.add_session()
        self.quiet()
        g0 = self.gate()
        with mock.patch.object(km, "_death_sweep_tick", lambda now, live_map: km._job_stage("probe", lambda: None)), \
                redirect_stderr(io.StringIO()) as err:
            g = self.passes()
        self.assertEqual(self.last().get("probe"), "unobserved")
        self.assertEqual(self.last()["deathSweep"], "closed", "the run that was open is the death sweep's own")
        self.assertEqual(g["wouldSkipPass"], g0["wouldSkipPass"], "a pass with an unobserved job is never a skip")
        self.assertIn("jobs gate (probe)", err.getvalue())

    def test_a_fault_in_the_passs_close_is_counted_and_said_once(self):
        """The pass's close never raises; its own fault counts one gateFault on every pass it faults, and stderr names its
        exception type once: two passes fault, two gateFaults, one line."""
        km = self.km
        self.add_session()
        self.quiet()
        g0 = self.gate()["gateFaults"]
        real = km._PERF_STATS.gate_update
        armed = [2]

        def gate_update(fn):
            if armed[0] and getattr(fn, "__qualname__", "") == "_jobs_gate_tick_close.<locals>.put":
                armed[0] -= 1
                raise RuntimeError("a synthetic close fault")
            return real(fn)
        with mock.patch.object(km._PERF_STATS, "gate_update", gate_update), redirect_stderr(io.StringIO()) as err:
            g = self.passes(2)
        self.assertEqual(armed[0], 0, "both passes' closes faulted")
        self.assertEqual(g["gateFaults"] - g0, 2)
        self.assertEqual(err.getvalue().count("the pass's close raised RuntimeError"), 1, err.getvalue())

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
        whole row would make every row reader's input a clock. A declared field the prelude's row observer does not watch (the
        retry count, the connection) opens the row readers by the row itself."""
        self.add_session()
        self.quiet()
        for n in (30, 29, 28):
            self.live[SID]["modelFallback"] = {"retry": {"nextIn": n}}
            self.passes()
            self.assertEqual(self.last()["autoRetry"], "closed", "the countdown at %d" % n)
        for field, value in (("retryCount", 3), ("connected", False)):
            self.live[SID][field] = value
            self.passes()
            self.assertEqual(self.last()["autoRetry"], "open:rows", field)
            self.passes()
            self.assertEqual(self.last()["autoRetry"], "closed", field)

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

    def test_the_account_file_and_the_price_config_live_under_the_labs_root(self):
        """The two box inputs outside the state directory read the lab's own files, so a write on the developer's machine never
        opens a job here."""
        km = self.km
        self.assertTrue(str(km._JOBS_GATE_BOX["account"]()).startswith(str(self.root)), km._JOBS_GATE_BOX["account"]())
        self.assertTrue(str(km._JOBS_GATE_BOX["prices"]()).startswith(str(self.root)), km._JOBS_GATE_BOX["prices"]())


class Detector(_GateLab):
    """The evidence: an act is a wrongQuiet; a memo that moved is raced only when an input of the memo's session moved after the
    gate observed it with a signal the next pass's gate will see (an input observed again, a prelude signal naming the session,
    or one naming every session over a view this pass took that a re-stat finds moved: _jobs_gate_raced), and a wrongQuiet
    otherwise, whatever later brings the job back. The files roads first run in the interrupt tick, a job that reads the
    session's own files and not the postal log, with a walk ahead of it taking the pass's shared snapshot as the real walk's key
    does (the re-stat's first road: the snapshot's own stat); then the lift's road (the gate's own stat at the pass's first job)
    and the walk's (the pass's fresh cache, the lift quiet); then the inputs observed again and the live row's since on a closed
    walk; then a memo that names its session, and memos of two sessions in one run. Each write lands on another thread while the
    job runs, announced the one way its test names; a write no signal covers stays a wrongQuiet."""

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

    # ── the files roads ──
    def _race(self, road, fresh=False, before=None):
        """Quiet, then one pass on which the interrupt tick is called closed and, while it runs, another thread does `road(store)`
        before the tick notes its memo moved. `fresh`: the session's floor has passed, so this pass's view of its files is a stat
        of this pass (the walk's and the gate's), never the standing one. `before`: a write before the pass. Returns the tick's
        (wrongQuiet, raced) on that pass."""
        km, jd = self.km, self.km.jd
        self.add_session()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {}, "rev": 1}))
        self._walk(lambda now, live_map: [km._session_files_stat(s) for s in km._alive_sessions(now, live_map)])
        armed = [None]

        def tick(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss")                                  # the tick's own memo: the store it reads is not the one it recorded
        self.stub("_interrupt_block_tick", tick)
        self.quiet()
        self.assertEqual(self.last()["interruptBlock"], "closed")
        w0, r0 = self.wrong("interruptBlock"), self.raced("interruptBlock")
        if before is not None:
            before(store)
        if fresh:
            km._FILES_STAT_STANDING[(SID, self.sessions[0]["path"])][1] -= km.NUDGE_FLOOR_S + 1
        armed[0] = lambda: road(store)
        self.passes()
        self.assertEqual(self.last()["interruptBlock"], "closed", "called closed: the write came after the gate looked")
        return _delta(self.wrong("interruptBlock"), w0), _delta(self.raced("interruptBlock"), r0)

    @staticmethod
    def _grow(store):
        store.write_text(json.dumps({"nodes": {}, "rev": 2, "pad": "x" * (40 + len(store.read_text()))}))

    def test_a_session_mark_during_the_run_is_a_race(self):
        km = self.km
        self.assertEqual(self._race(lambda store: (self._grow(store), km._files_stat_mark(SID))), ({}, {"miss": 1}))

    def test_a_box_wide_mark_during_the_run_is_a_race_for_a_view_of_this_pass(self):
        km = self.km
        self.assertEqual(self._race(lambda store: (self._grow(store), km._files_stat_mark()), fresh=True), ({}, {"miss": 1}))

    def test_a_judge_write_counted_for_the_session_is_a_race(self):
        jd = self.km.jd

        def judge(store):
            st = jd.load_goals(SID)
            st["note"] = "a judge's write"
            jd.save_goals(SID, st)
        self.assertEqual(self._race(judge), ({}, {"miss": 1}))

    def test_a_judge_write_of_a_shared_file_is_a_race_for_a_view_of_this_pass(self):
        jd = self.km.jd
        self.assertEqual(self._race(lambda store: (self._grow(store), jd._session_file_written(None)), fresh=True), ({}, {"miss": 1}))

    def test_a_live_row_whose_state_moved_is_a_race(self):
        self.assertEqual(self._race(lambda store: (self._grow(store), self.live[SID].__setitem__("state", "working"))),
                         ({}, {"miss": 1}))

    def test_a_live_tail_revision_that_moved_is_a_race(self):
        self.assertEqual(self._race(lambda store: (self._grow(store), self.revs.__setitem__(SID, 1))), ({}, {"miss": 1}))

    def test_a_postal_write_is_a_race_for_a_view_of_this_pass(self):
        def postal(store):
            self._grow(store)
            (self.root / "timeline").mkdir(exist_ok=True)
            with open(self.root / "timeline" / "messages.jsonl", "a") as f:
                f.write('{"kind": "question"}\n')
        self.assertEqual(self._race(postal, fresh=True), ({}, {"miss": 1}))

    def test_an_unannounced_write_before_the_pass_stays_a_wrong_quiet_though_a_box_wide_mark_lands_in_the_run(self):
        """PlantedMiss's shape: the store written before the pass with no mark, inside the floor, so the gate's view is the
        standing stat. A box-wide mark during the run makes the next pass re-stat every session, but a standing view cannot tell
        a write during the run from this one, so it is not re-statted: a blanket re-stat would file the pre-pass miss as raced."""
        km = self.km
        self.assertEqual(self._race(lambda store: km._files_stat_mark(), before=self._grow), ({"miss": 1}, {}))

    def test_an_unannounced_write_during_the_run_stays_a_wrong_quiet(self):
        """The rule is the signal's, not the next pass's outcome: another process's write during the run with no signal at all
        is a miss. Here the next pass serves the standing stat too (the walk's snapshot ahead of the tick took the stat before
        the write); on the lift's road the walk's later stat brings the lift back on the next pass, and the write is a miss
        there all the same (test_on_the_lifts_road_an_unannounced_write_is_a_miss_though_the_next_pass_opens_it)."""
        self.assertEqual(self._race(self._grow, fresh=True), ({"miss": 1}, {}))

    # ── the re-stat's other two roads: the gate's own stat at the lift's turn, the pass's fresh cache at the walk's ──
    def _lift_race(self, road, fresh):
        """The REAL lift, called closed, quiet first (a walk after it takes the pass's shared snapshot, as the real walk's key
        does); on the next pass another thread does `road(store)` from inside the lift's dispatch scan, which runs before the
        lift's fingerprint, so the lift loads. `fresh`: the floor has passed, so the gate's view at the lift's turn, the pass's
        first job, is the gate's own stat (no reader has taken the snapshot yet); else the standing stat. Returns the lift's
        (wrongQuiet, raced) on that pass."""
        km, jd = self.km, self.km.jd
        self.add_session()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {}, "rev": 1}))
        real_scan = km._bg_scan_all_cached
        armed = [None]

        def scan(path):
            fn, armed[0] = armed[0], None
            if fn is not None:
                th = threading.Thread(target=fn)
                th.start()
                th.join()
            return real_scan(path)
        self.stub("_bg_scan_all_cached", scan)
        self.real("_lift_spent_awaiting")
        self._walk(lambda now, live_map: [km._session_files_stat(s) for s in km._alive_sessions(now, live_map)])
        self.quiet()
        self.assertEqual(self.last()["liftSpentAwaiting"], "closed")
        w0, r0 = self.wrong("liftSpentAwaiting"), self.raced("liftSpentAwaiting")
        if fresh:
            km._FILES_STAT_STANDING[(SID, self.sessions[0]["path"])][1] -= km.NUDGE_FLOOR_S + 1
        armed[0] = lambda: road(store)
        self.passes()
        self.assertIsNone(armed[0], "the write ran inside the lift's scan")
        self.assertEqual(self.last()["liftSpentAwaiting"], "closed", "called closed: the write came after the gate looked")
        return _delta(self.wrong("liftSpentAwaiting"), w0), _delta(self.raced("liftSpentAwaiting"), r0)

    def test_on_the_lifts_road_a_box_wide_mark_past_the_floor_is_a_race(self):
        km = self.km
        self.assertEqual(self._lift_race(lambda store: (self._grow(store), km._files_stat_mark()), fresh=True), ({}, {"load": 1}))

    def test_on_the_lifts_road_a_shared_judge_write_past_the_floor_is_a_race(self):
        """The lift declares no judge counter: the shared write reaches it through its files alone (a postal write would not
        test this road, since the lift declares the postal log and observes it again)."""
        jd = self.km.jd
        self.assertEqual(self._lift_race(lambda store: (self._grow(store), jd._session_file_written(None)), fresh=True),
                         ({}, {"load": 1}))

    def test_on_the_lifts_road_a_box_wide_mark_inside_the_floor_is_a_miss(self):
        """Inside the floor the lift's view is the standing stat, which cannot tell this write from one before the pass."""
        km = self.km
        self.assertEqual(self._lift_race(lambda store: (self._grow(store), km._files_stat_mark()), fresh=False), ({"load": 1}, {}))

    def test_on_the_lifts_road_an_unannounced_write_is_a_miss_though_the_next_pass_opens_it(self):
        """Past the floor, the walk's stat after the lift refreshes the standing entry with the write, so the next pass opens
        the lift; the write, which no signal covered, is a miss all the same."""
        self.assertEqual(self._lift_race(self._grow, fresh=True), ({"load": 1}, {}))
        self.passes()
        self.assertEqual(self.last()["liftSpentAwaiting"], "open:files", "a later stat brought the lift back")

    def _walk_race(self, road, fresh):
        """The walk called closed with the lift quiet, and the shared snapshot taken only after the walk (by the interrupt tick),
        so at the walk's turn no reader has taken it: past the floor the walk's view is the gate's own stat from the lift's turn,
        kept in the pass's fresh cache; inside it, the standing stat. On the race pass another thread does `road(store)` while
        the walk runs, before it notes its memo moved. Returns the walk's (wrongQuiet, raced) on that pass."""
        km, jd = self.km, self.km.jd
        self.add_session()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {}, "rev": 1}))
        armed = [None]

        def walk(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss")
        self._walk(walk)
        self.stub("_interrupt_block_tick", lambda now, live_map: [km._session_files_stat(s) for s in km._alive_sessions(now, live_map)])
        self.quiet()
        self.assertEqual(self.last()["autoNudge"], "closed")
        w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
        if fresh:
            km._FILES_STAT_STANDING[(SID, self.sessions[0]["path"])][1] -= km.NUDGE_FLOOR_S + 1
        armed[0] = lambda: road(store)
        self.passes()
        self.assertIsNone(armed[0])
        self.assertEqual(self.last()["autoNudge"], "closed", "called closed: the write came after the gate looked")
        return _delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)

    def test_on_the_walks_road_a_box_wide_mark_past_the_floor_is_a_race(self):
        """A box-wide mark, not the postal log or a shared judge write: the walk declares both and observes them again."""
        km = self.km
        self.assertEqual(self._walk_race(lambda store: (self._grow(store), km._files_stat_mark()), fresh=True), ({}, {"miss": 1}))

    def test_on_the_walks_road_a_box_wide_mark_inside_the_floor_is_a_miss(self):
        km = self.km
        self.assertEqual(self._walk_race(lambda store: (self._grow(store), km._files_stat_mark()), fresh=False), ({"miss": 1}, {}))

    # ── the inputs observed again, and the live row's since, each moved on another thread during a closed walk ──
    def _walk_moves(self, move):
        """Quiet, then one pass on which the walk, called closed, has another thread do `move()` and then notes its memo moved.
        Returns the walk's (wrongQuiet, raced) on that pass."""
        km = self.km
        self.add_session()
        armed = [None]

        def walk(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss")
        self._walk(walk)
        self.quiet()
        self.assertEqual(self.last()["autoNudge"], "closed")
        w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
        armed[0] = move
        self.passes()
        self.assertIsNone(armed[0])
        self.assertEqual(self.last()["autoNudge"], "closed")
        return _delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)

    def test_a_box_wide_file_observed_again_is_a_race(self):
        clears = self.root / "cleared.jsonl"
        self.assertEqual(self._walk_moves(lambda: clears.write_text('{"id": "g1", "op": "clear"}\n')), ({}, {"miss": 1}))

    def test_an_in_memory_input_observed_again_is_a_race(self):
        jd = self.km.jd
        self.assertEqual(self._walk_moves(lambda: jd._active_change.__setitem__(0, jd._active_change[0] + 1)), ({}, {"miss": 1}))

    def test_a_registry_row_observed_again_is_a_race(self):
        jd = self.km.jd

        def reg():
            jd.SDKDIR.mkdir(parents=True, exist_ok=True)
            (jd.SDKDIR / (SID + ".json")).write_text(json.dumps({"sid": SID, "alive": True}))
        self.assertEqual(self._walk_moves(reg), ({}, {"miss": 1}))

    def test_a_live_row_whose_since_moved_is_a_race(self):
        self.assertEqual(self._walk_moves(lambda: self.live[SID].__setitem__("since", 2)), ({}, {"miss": 1}))

    # ── a memo that names its session ──
    def test_a_memo_naming_its_session_is_raced_by_that_sessions_inputs_alone(self):
        """Memo evidence names its session. Two sessions alive, and the REAL tick-seen check reports api's key moved (its memo
        stale behind the gate's back) while, on another thread, the other session's live-tail revision moves, or a judge saves
        the other session's store: neither is an input of api's, so the miss is a wrongQuiet. api's own revision is a race."""
        km, jd = self.km, self.km.jd
        self.add_session(SID, "api")
        self.add_session(SID2, "web")
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID2 + ".json")).write_text(json.dumps({"nodes": {}, "rev": 1}))
        armed = [None]

        def tick(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._TICK_SEEN[("interrupt-block", SID)] = tuple([0.0] * 20)     # api's memo, stale: the real check notes api's miss
            km._tick_job_check("interrupt-block", dict(self.sessions[0]))
        self.stub("_interrupt_block_tick", tick)

        def judge():
            st = jd.load_goals(SID2)
            st["note"] = "a judge's write for web"
            jd.save_goals(SID2, st)
        self.quiet()
        for label, move, want in (("web's revision", lambda: self.revs.__setitem__(SID2, 1), ({"miss": 1}, {})),
                                  ("web's store", judge, ({"miss": 1}, {})),
                                  ("api's revision", lambda: self.revs.__setitem__(SID, 1), ({}, {"miss": 1}))):
            self.passes(3)                                              # the case before settles: its moved input opens the
            self.assertEqual(self.last()["interruptBlock"], "closed", label)   #  tick once, and the passes after close it
            w0, r0 = self.wrong("interruptBlock"), self.raced("interruptBlock")
            armed[0] = move
            self.passes()
            self.assertIsNone(armed[0], label)
            self.assertEqual(self.last()["interruptBlock"], "closed", label)
            self.assertEqual((_delta(self.wrong("interruptBlock"), w0), _delta(self.raced("interruptBlock"), r0)), want, label)

    def test_memos_of_two_sessions_each_need_their_own_race(self):
        """Both sessions' tick-seen memos stale behind the gate's back, and only api's live-tail revision moves on another
        thread: api's miss raced, web's had no signal, so the run is a wrongQuiet."""
        km = self.km
        self.add_session(SID, "api")
        self.add_session(SID2, "web")
        armed = [None]

        def tick(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            for s in self.sessions:
                km._TICK_SEEN[("interrupt-block", s["sid"])] = tuple([0.0] * 20)
                km._tick_job_check("interrupt-block", dict(s))
        self.stub("_interrupt_block_tick", tick)
        self.quiet()
        self.assertEqual(self.last()["interruptBlock"], "closed")
        w0, r0 = self.wrong("interruptBlock"), self.raced("interruptBlock")
        armed[0] = lambda: self.revs.__setitem__(SID, 1)
        self.passes()
        self.assertIsNone(armed[0])
        self.assertEqual(self.last()["interruptBlock"], "closed")
        self.assertEqual((_delta(self.wrong("interruptBlock"), w0), _delta(self.raced("interruptBlock"), r0)), ({"miss": 2}, {}))


class TickSeen(_GateLab):
    """The tick jobs' memo hooks, reached through the real _tick_job_check from a job the gate called closed: a session never
    seen and a key that moved are the detector's evidence, a session with no transcript is owed, and each leaves its evaluation
    open until a done records it."""

    def _check(self, session=None, prev=None):
        km = self.km
        self.add_session()
        armed = [False]

        def tick(now, live_map):
            if armed[0]:
                armed[0] = False
                km._TICK_SEEN.pop(("interrupt-block", SID), None)
                if prev is not None:
                    km._TICK_SEEN[("interrupt-block", SID)] = prev
                km._tick_job_check("interrupt-block", dict(session or self.sessions[0]))
        self.stub("_interrupt_block_tick", tick)
        self.quiet()
        w0 = self.wrong("interruptBlock")
        armed[0] = True
        self.passes()
        return _delta(self.wrong("interruptBlock"), w0), set(self.rec("interruptBlock")["owed"])

    def test_a_session_never_seen_is_evidence_and_left_unrecorded(self):
        self.assertEqual(self._check(), ({"neverSeen": 1}, {"unrecorded"}))

    def test_a_key_that_moved_is_a_miss_and_left_unrecorded(self):
        self.assertEqual(self._check(prev=tuple([0.0] * 20)), ({"miss": 1}, {"unrecorded"}))

    def test_a_session_with_no_transcript_is_owed_and_left_unrecorded(self):
        gone = dict(path=str(self.root / "projects" / "no-such-transcript.jsonl"))
        km = self.km
        self.add_session()
        armed = [False]

        def tick(now, live_map):
            if armed[0]:
                armed[0] = False
                km._tick_job_check("interrupt-block", dict(self.sessions[0], **gone))
        self.stub("_interrupt_block_tick", tick)
        self.quiet()
        w0 = self.wrong("interruptBlock")
        armed[0] = True
        self.passes()
        self.assertEqual((_delta(self.wrong("interruptBlock"), w0), set(self.rec("interruptBlock")["owed"])),
                         ({}, {"noTranscript", "unrecorded"}))


class TheClickMap(_GateLab):
    """The interrupt tick's compaction check (the REAL _compacting_now, over a cached parse of the test's and no backend that
    brackets its own compaction) pops the click map the tick declares (mem:compactClicked) when the parse shows the
    compaction's boundary, and at the 180 s cap. Each pop is a write, an act: a closed run that popped the map is a wrongQuiet,
    never raced by its own write."""

    def setUp(self):
        super().setUp()
        km = self.km
        self.add_session()
        self.parse = {"turns": []}
        self.stub("_parse_cached", lambda path: self.parse)
        p = mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: None))
        p.start()
        self.addCleanup(p.stop)
        self.armed = [None]

        def tick(now, live_map):
            note, self.armed[0] = self.armed[0], None
            if note is None:
                return
            km._compacting_now(SID, tm=live_map.get(SID), path=self.sessions[0]["path"])
            if note:
                km._jobs_gate_note(note)                                # the tick's own memo moved too
        self.stub("_interrupt_block_tick", tick)
        km._compact_clicked[SID] = float(T0)                            # a /compact the kernel sent at T0
        self.quiet(T0 + 1)
        self.assertEqual(self.last()["interruptBlock"], "closed")

    def test_the_boundary_pop_is_a_write_so_the_closed_run_is_a_wrong_quiet(self):
        km = self.km
        w0, r0 = self.wrong("interruptBlock"), self.raced("interruptBlock")
        self.parse = {"turns": [{"atoms": [{"type": "system", "subtype": "compact_boundary", "t": T0 + 5}]}]}   # behind the gate
        self.armed[0] = "miss"
        self.passes(1, T0 + 10)
        self.assertNotIn(SID, km._compact_clicked, "the boundary ended the compaction")
        self.assertEqual(self.last()["interruptBlock"], "closed")
        self.assertEqual((_delta(self.wrong("interruptBlock"), w0), _delta(self.raced("interruptBlock"), r0)),
                         ({"write": 1, "miss": 1}, {}), "the run's own pop is an act, never the race its memo would read")

    def test_the_caps_pop_is_a_write(self):
        km = self.km
        seen = self.spy()
        self.armed[0] = ""
        self.passes(1, T0 + 181)
        self.assertNotIn(SID, km._compact_clicked, "the 180 s cap ended it")
        self.assertEqual(self.last()["interruptBlock"], "open:due", "the cap's instant is the tick's due")
        self.assertEqual(self.acts(seen, "interruptBlock"), {"write": 1})


class TheRaceRead(_GateLab):
    """The race read asks each backend for its live sessions directly (_jg_live_rows), so a backend whose read raises names
    every session for the raced test and adds nothing to the liveness read's own failure count (/version's liveReadFailures,
    which Sessions.live's stand-down keeps). The real backends' table and the real liveness read, over an SDK backend whose
    read raises."""

    def test_a_raising_backend_read_names_every_session_and_counts_no_live_read_failure(self):
        km, jd = self.km, self.km.jd
        self.add_session()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {}, "rev": 1}))
        be = _LabLive(self)
        be.fail = RuntimeError("a synthetic registry fault")
        self.stub("_sdk", lambda: be)
        self.stub("_codex", lambda: None)
        if _REAL_GATE_BACKENDS is not None:
            self.stub("_JOBS_GATE_BACKENDS", _REAL_GATE_BACKENDS)
        p = mock.patch.object(km.Sessions, "live", _REAL_SESSIONS_LIVE)
        p.start()
        self.addCleanup(p.stop)
        fails = km._LIVE_READ_FAILS
        saved = (fails["count"], dict(fails["last"]), dict(km._LIVE_LAST_ROWS))

        def restore():
            fails["count"] = saved[0]
            fails["last"].clear()
            fails["last"].update(saved[1])
            km._LIVE_LAST_ROWS.clear()
            km._LIVE_LAST_ROWS.update(saved[2])
        self.addCleanup(restore)
        armed = [None]

        def walk(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is not None:
                th = threading.Thread(target=fn)
                th.start()
                th.join()
            km._jobs_gate_note("miss")
        self.stub("_auto_nudge_tick", walk)
        with redirect_stderr(io.StringIO()):
            self.quiet()
            n0, reads0, w0 = fails["count"], be.reads, self.wrong("autoNudge")
            self.passes(5)
            self.assertEqual(fails["count"] - n0, 0, "the race read added nothing to liveReadFailures")
            self.assertGreaterEqual(be.reads - reads0, 5, "the race read asked the backend on each pass, and the read raised")
            self.assertEqual(_delta(self.wrong("autoNudge"), w0), {"miss": 5}, "nothing moved: a miss on each pass")
            r0 = self.raced("autoNudge")
            armed[0] = lambda: Detector._grow(store)
            self.passes()
        self.assertEqual(_delta(self.raced("autoNudge"), r0), {"miss": 1}, "the raise named every session; the re-stat found the store")


class MemoScope(_GateLab):
    """The re-observed channels scoped to a memo's session (_jg_scoped): with two sessions alive and a memo naming api, another
    session's registry row, a judge write counted for another session and another session's compaction click, each moved on
    another thread while the job runs, are no race of api's (a wrongQuiet), and api's own registry row is a race."""

    def _two(self):
        self.add_session(SID, "api")
        self.add_session(SID2, "web")

    def _tick_memos(self, which):
        km = self.km
        armed = [None]

        def tick(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            for s in which():
                km._TICK_SEEN[("interrupt-block", s["sid"])] = tuple([0.0] * 20)
                km._tick_job_check("interrupt-block", dict(s))
        self.stub("_interrupt_block_tick", tick)
        return armed

    def _walk_memo(self):
        km = self.km
        armed = [None]

        def walk(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is None:
                return
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss", SID)
        self.stub("_auto_nudge_tick", walk)
        return armed

    def _once(self, job, armed, move):
        self.assertEqual(self.last()[job], "closed")
        w0, r0 = self.wrong(job), self.raced(job)
        armed[0] = move
        self.passes()
        self.assertIsNone(armed[0])
        self.assertEqual(self.last()[job], "closed")
        return _delta(self.wrong(job), w0), _delta(self.raced(job), r0)

    def _reg(self, sid):
        jd = self.km.jd
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (sid + ".json")).write_text(json.dumps({"sid": sid, "alive": True}))

    def test_another_sessions_click_is_not_apis_race(self):
        km = self.km
        self._two()
        armed = self._tick_memos(lambda: self.sessions[:1])
        self.quiet()
        self.assertEqual(self._once("interruptBlock", armed, lambda: km._compact_clicked.__setitem__(SID2, time.time())),
                         ({"miss": 1}, {}))

    def test_another_sessions_registry_row_is_not_apis_race(self):
        """The stub's memo names api alone, as a look whose key carries no asker's row does. A look whose real key carries web's
        row, because web asked api something, names both, and that row is its race (TheLookScope)."""
        self._two()
        armed = self._walk_memo()
        self.quiet()
        self.assertEqual(self._once("autoNudge", armed, lambda: self._reg(SID2)), ({"miss": 1}, {}))

    def test_a_judge_write_for_another_session_is_not_apis_race(self):
        jd = self.km.jd
        self._two()
        armed = self._walk_memo()
        self.quiet()
        self.assertEqual(self._once("autoNudge", armed, lambda: jd._session_file_written(SID2)), ({"miss": 1}, {}))

    def test_apis_own_registry_row_is_a_race(self):
        self._two()
        armed = self._walk_memo()
        self.quiet()
        self.assertEqual(self._once("autoNudge", armed, lambda: self._reg(SID)), ({}, {"miss": 1}))


class SharedParts(_GateLab):
    """The shared inputs sessions own parts of, read by the memo's session for its race (_jg_apart): with api and web alive and a
    memo naming api, web's part of each moving on another thread while the job runs is no race of api's (a wrongQuiet: a real
    missing signal stays visible), and api's own part moving is one. A judge call ending, a record in the nudge ledger, a flag
    in the session flags, an entry in the relay queue (the walk's), an interrupt-block row in the ledger (the interrupt tick's)
    and a working note (the notes job's), each through the input's real file or the judge module's real call registry."""

    _two = MemoScope._two
    _once = MemoScope._once

    def _memo_job(self, name):
        """Stub job `name` to run an armed move on another thread and then note a memo naming api."""
        km = self.km
        armed = [None]

        def job(*a, **k):
            fn, armed[0] = armed[0], None
            if fn is None:
                return None
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss", SID)
        self.stub(name, job)
        return armed

    def _each(self, job, armed, moves):
        """For web's move and then api's: settle, move once while `job` runs, and return each pass's (wrongQuiet, raced)."""
        out = []
        for move in moves:
            self.passes(2)                                              # the case before settles: its moved input opens the job once
            out.append(self._once(job, armed, move))
        return out

    def test_a_judge_call_for_another_session_ending_is_not_apis_race(self):
        jd = self.km.jd
        self._two()
        armed = self._memo_job("_auto_nudge_tick")
        runs = {SID: jd._active_begin("triage", SID, 0), SID2: jd._active_begin("triage", SID2, 0)}
        self.addCleanup(lambda: [jd._active_end(r) for r in runs.values()])
        self.quiet()
        self.assertEqual(self._each("autoNudge", armed, (lambda: jd._active_end(runs[SID2]), lambda: jd._active_end(runs[SID]))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])

    def test_another_sessions_ledger_record_is_not_apis_race(self):
        self._two()
        armed = self._memo_job("_auto_nudge_tick")
        self.ledger(enabled=True, nudged={})
        self.quiet()
        rec = {"count": 1, "lastTurnId": "t1"}
        self.assertEqual(self._each("autoNudge", armed, (lambda: self.ledger(enabled=True, nudged={SID2 + ":g1": rec}),
                                                         lambda: self.ledger(enabled=True, nudged={SID2 + ":g1": rec,
                                                                                                   SID + ":g1": rec}))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])

    def test_a_switch_in_the_ledger_is_every_sessions_input(self):
        """A record naming no session (the on/off switch) is read by every memo."""
        self._two()
        armed = self._memo_job("_auto_nudge_tick")
        self.ledger(enabled=True, nudged={})
        self.quiet()
        self.assertEqual(self._once("autoNudge", armed, lambda: self.ledger(enabled=False, nudged={})), ({}, {"miss": 1}))

    def test_another_sessions_flag_is_not_apis_race(self):
        self._two()
        armed = self._memo_job("_auto_nudge_tick")
        flags = self.root / "session-flags.json"
        flags.write_text("{}")
        self.quiet()
        self.assertEqual(self._each("autoNudge", armed, (lambda: flags.write_text(json.dumps({SID2: {"hidden": True}})),
                                                         lambda: flags.write_text(json.dumps({SID2: {"hidden": True},
                                                                                              SID: {"hidden": True}})))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])

    def test_another_sessions_relay_entry_is_not_apis_race(self):
        km = self.km
        self._two()
        armed = self._memo_job("_auto_nudge_tick")
        q = km.jd._relay_queue_dir()
        q.mkdir(parents=True, exist_ok=True)
        self.quiet()

        def entry(sid):
            (q / ("%s__%s_g2.json" % (sid, sid))).write_text(json.dumps({"sid": sid, "nid": sid + ":g2", "marker": "m1"}))
        self.assertEqual(self._each("autoNudge", armed, (lambda: entry(SID2), lambda: entry(SID))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])

    def test_another_sessions_interrupt_block_row_is_not_apis_race(self):
        self._two()
        armed = self._memo_job("_interrupt_block_tick")
        self.ledger(enabled=True, intrBlocked={})
        self.quiet()
        row = {"gid": "g1", "t": T0}
        self.assertEqual(self._each("interruptBlock", armed, (lambda: self.ledger(enabled=True, intrBlocked={SID2: row}),
                                                              lambda: self.ledger(enabled=True, intrBlocked={SID2: row, SID: row}))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])

    def test_another_sessions_working_note_is_not_apis_race(self):
        self._two()
        armed = self._memo_job("_clear_done_working_notes")
        notes = self.root / "working"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / GONE).write_text("owns the notes-api docs\n")         # a note stands, so the job reads its whole declaration
        self.quiet()
        self.assertEqual(self._each("clearDoneNotes", armed, (lambda: (notes / SID2).write_text("owns the web client\n"),
                                                              lambda: (notes / SID).write_text("owns the exporter\n"))),
                         [({"miss": 1}, {}), ({}, {"miss": 1})])


class TheLookScope(_GateLab):
    """The walk's look names its session and the askers whose registry rows its key carries as one scope, through the REAL look
    check over a REAL key: web has an open question to api in the postal log, so api's key holds web's registry row, and that
    row rewritten on another thread before api's look is a race of api's memo, which the next pass's gate opens on (regs)."""

    def setUp(self):
        super().setUp()
        km, jd = self.km, self.km.jd
        self.add_session(SID, "api")
        self.add_session(SID2, "web")
        for sid in (SID, SID2):
            self.addCleanup(km._NUDGE_LOOK_ASKERS.pop, sid, None)
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.MESSAGES.write_text(json.dumps({"id": "m-ask-0001", "from_id": SID2, "to_id": SID, "t": T0, "kind": "question",
                                           "from": "web", "to": "api", "body": "which client should change?"}) + "\n")
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.SDKDIR / (SID2 + ".json")).write_text(json.dumps({"sid": SID2, "alive": True}))
        self.armed = [None]

        def walk(now, live_map):
            s = dict(self.sessions[0])
            fn, self.armed[0] = self.armed[0], None
            if fn is not None:
                th = threading.Thread(target=fn)
                th.start()
                th.join()
            skip, st, _v = km._nudge_look_check(s, now)
            if not skip:
                km._nudge_look_done(s, st, [], "working")
        self.stub("_auto_nudge_tick", walk)

    def test_the_debtors_key_carries_its_askers_row_and_the_row_is_its_race(self):
        km, jd = self.km, self.km.jd
        self.quiet()
        self.assertEqual(self.last()["autoNudge"], "closed")
        self.assertEqual(km._NUDGE_LOOK_ASKERS.get(SID), ([SID2], []), "web's open question keys api's memo on web's row")
        w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
        self.armed[0] = lambda: (jd.SDKDIR / (SID2 + ".json")).write_text(json.dumps({"sid": SID2, "alive": False, "t": T0}))
        self.passes()
        self.assertIsNone(self.armed[0])
        self.assertEqual(self.last()["autoNudge"], "closed")
        self.assertEqual((_delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)), ({}, {"miss": 1}))
        self.passes()
        self.assertEqual(self.last()["autoNudge"], "open:regs", "the row moved after the gate looked: the next pass opens on it")

    def test_a_ledger_write_inside_the_looks_key_is_a_race_whatever_it_names(self):
        """The ten-file key carries the nudge ledger's stat whole: a record for a session outside api's scope (neither api nor
        web, its keyed asker) that lands after the gate looked and before api's look takes a fresh key moves api's key there,
        and that memo counts the ledger whole (a memo noted with no key, as a stub notes it, reads another session's record as
        that session's: SharedParts)."""
        km = self.km
        self.ledger(enabled=True, nudged={})
        self.quiet()
        self.assertEqual(self.last()["autoNudge"], "closed")
        km._FILES_STAT_STANDING[(SID, self.sessions[0]["path"])][1] -= km.NUDGE_FLOOR_S + 1   # api's next view is a fresh stat
        w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
        self.armed[0] = lambda: self.ledger(enabled=True, nudged={GONE + ":g1": {"count": 1, "lastTurnId": "t1"}})
        self.passes()
        self.assertIsNone(self.armed[0])
        self.assertEqual(self.last()["autoNudge"], "closed")
        self.assertEqual((_delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)), ({}, {"miss": 1}))


class CallSites(_GateLab):
    """Each memo note that names its session, driven through its REAL call site with two sessions alive: web's live-tail revision
    moves on another thread during the run (a signal the next prelude names web by), and the memo names api, so the run is a
    wrongQuiet; a call site that dropped the session would read every session's inputs and file it raced. The lift's load, the
    walk's look check (neverSeen, miss, and the three clock refusals), the relay's quiet key (RelayScope) and the tick check's
    neverSeen (its miss is MemoScope's)."""

    def _two(self):
        self.add_session(SID, "api")
        self.add_session(SID2, "web")

    def _web_moves(self):
        self.revs[SID2] = self.revs.get(SID2, 0) + 1

    def test_the_lifts_load(self):
        km, jd = self.km, self.km.jd
        self._two()
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        store = jd.GOALDIR / (SID + ".json")
        store.write_text(json.dumps({"nodes": {}, "rev": 1}))
        real_scan = km._bg_scan_all_cached
        armed = [None]

        def scan(path):
            fn, armed[0] = armed[0], None
            if fn is not None:
                th = threading.Thread(target=fn)
                th.start()
                th.join()
            return real_scan(path)
        self.stub("_bg_scan_all_cached", scan)
        self.real("_lift_spent_awaiting")
        self.quiet()
        self.assertEqual(self.last()["liftSpentAwaiting"], "closed")
        w0, r0 = self.wrong("liftSpentAwaiting"), self.raced("liftSpentAwaiting")
        armed[0] = lambda: (Detector._grow(store), self._web_moves())   # api's store grows inside the floor, web's revision moves
        self.passes()
        self.assertIsNone(armed[0])
        self.assertEqual(self.last()["liftSpentAwaiting"], "closed")
        self.assertEqual((_delta(self.wrong("liftSpentAwaiting"), w0), _delta(self.raced("liftSpentAwaiting"), r0)),
                         ({"load": 1}, {}))

    def test_the_walks_look_check(self):
        km = self.km
        self._two()
        for sid in (SID, SID2):
            self.addCleanup(km._NUDGE_LOOK_ASKERS.pop, sid, None)
        armed = [None]

        def walk(now, live_map):
            plant, armed[0] = armed[0], None
            if plant is None:
                return
            th = threading.Thread(target=self._web_moves)
            th.start()
            th.join()
            s = dict(self.sessions[0])
            st = tuple(km._nudge_look_stat(s)[0])
            row = plant(st, now)
            with km._TICK_SEEN_LOCK:
                if row is None:
                    km._TICK_SEEN.pop(("auto-nudge", SID), None)
                else:
                    km._TICK_SEEN[("auto-nudge", SID)] = row
            km._nudge_look_check(s, now)
        self.stub("_auto_nudge_tick", walk)
        self.quiet()
        cases = (("neverSeen", lambda st, now: None, None),
                 ("miss", lambda st, now: (st[0] + 1.0,) + st[1:] + (-1.0, "working"), None),
                 ("clockParse", lambda st, now: st + (-1.0, "closer-unsettled"), False),   # recorded under the closer, read with it off
                 ("clockParse", lambda st, now: st + (None, "working"), None),             # an unbounded memo
                 ("clockParse", lambda st, now: st + (float(now - 1), "working"), None))   # a flip come due
        for n, (evidence, plant, closer) in enumerate(cases):
            self.passes(2)
            self.assertEqual(self.last()["autoNudge"], "closed", n)
            w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
            armed[0] = plant
            with mock.patch.object(km.jd, "CLOSER_ON", True if closer is None else closer):
                self.passes()
            self.assertIsNone(armed[0], n)
            self.assertEqual(self.last()["autoNudge"], "closed", n)
            self.assertEqual((_delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)), ({evidence: 1}, {}),
                             "case %d" % n)

    def test_the_tick_checks_never_seen(self):
        km = self.km
        self._two()
        armed = [False]

        def tick(now, live_map):
            if not armed[0]:
                return
            armed[0] = False
            th = threading.Thread(target=self._web_moves)
            th.start()
            th.join()
            km._TICK_SEEN.pop(("interrupt-block", SID), None)
            km._tick_job_check("interrupt-block", dict(self.sessions[0]))
        self.stub("_interrupt_block_tick", tick)
        self.quiet()
        w0, r0 = self.wrong("interruptBlock"), self.raced("interruptBlock")
        armed[0] = True
        self.passes()
        self.assertEqual(self.last()["interruptBlock"], "closed")
        self.assertEqual((_delta(self.wrong("interruptBlock"), w0), _delta(self.raced("interruptBlock"), r0)),
                         ({"neverSeen": 1}, {}))


class TheStandDown(_GateLab):
    """The exit's stand-down returns from _job_stage before the gate opens, so a pass that runs once the exit began, or in which
    the exit begins, leaves no observed run open: no gate fault, no job unobserved, nothing said on stderr. Set before a pass and
    from inside the pass's first job, cleared in cleanup."""

    def test_a_pass_the_exit_began_before_or_during_opens_no_run_it_leaves_open(self):
        km = self.km
        self.add_session()
        self.quiet()
        ev = km._EXIT_STAND_DOWN
        was = ev.is_set()
        self.addCleanup(lambda: ev.set() if was else ev.clear())
        g0 = self.gate()
        with redirect_stderr(io.StringIO()) as err:
            ev.set()
            self.passes()                                               # set before the pass
            self.assertEqual(self.last(), {}, "no job started")
            ev.clear()
            self.stub("_checkpoint_first_sight", lambda now: ev.set())   # set from inside the pass's first job
            self.passes()
            ev.clear()
        g = self.gate()
        self.assertEqual(g["gateFaults"], g0["gateFaults"], "no gate fault")
        self.assertEqual({j: r["unobserved"] for j, r in g["byJob"].items() if r["unobserved"]}, {}, "no job unobserved")
        self.assertEqual(self.last(), {"checkpointFirstSight": "closed"}, "the first job ran and closed; no job after it started")
        self.assertNotIn("jobs gate", err.getvalue(), err.getvalue())


class TheCheckpointJobs(_GateLab):
    """The checkpoint settle write (persistCheckpoints) and its first sight (checkpointFirstSight), both on the jobs pass since
    main moved the write off the pusher, are declared, so a quiet pass still counts under wouldSkipPass and the detector audits
    them. First sight's whole work is to record a listed session one of the write's two tables lacks: owed under `unseen` while
    one does, and nothing else. The write opens on its sessions' settle evidence (mem:ckptSettle: the turn-end key and the
    states log's stat, by the write's own reader) and is owed under `unseen`, `period` (a leaf moved since its last periodic
    write and the period passed or a second from passing) and `prune` (a table over its bound). The REAL two jobs run over the
    lab's one listed session (the listing stubbed to the lab's sessions, the turn-end key a test's own), with the write's
    fold, document and assembly collaborators recorded, never run: each test reads the verdict and what the real job did."""

    def setUp(self):
        super().setUp()
        km, jd = self.km, self.km.jd
        self.leaf = self.add_session()
        self.states = jd.STATE / "states" / (SID + ".jsonl")
        self.states.parent.mkdir(parents=True, exist_ok=True)
        self.states.write_text('{"state": "idle", "t": 1}\n')
        self.turn, self.dirty, self.persisted = {}, [], []
        self.stub("CKPT_PERIOD_S", 30.0)                                # the shipped period, whatever the environment says
        self.stub("_sessions", lambda now, window=None, forks=True: [dict(s) for s in self.sessions])
        self.stub("_turn_end_key", lambda sid, reg=None: self.turn.get(sid, 0))
        self.stub("_heal_cold_folds", lambda leaf, dropped_out=None: [])
        self.stub("_prime_leaf_folds", lambda leaf: True)
        self.stub("_session_fold_files", lambda sid, leaf: {str(leaf)})
        self.stub("_display_sdk_human", lambda sid: False)
        self.stub("_stored_tree", lambda leaf, sid: None)
        self.stub("_asm_refresh_maybe", lambda leaf, sid: False)
        for name, fn in (("checkpoint_dirty", lambda: list(self.dirty)), ("drop_cold_cursors", lambda path: []),
                         ("checkpoint_write_dirty_paths",
                          lambda paths=None, budget_s=None, stop=None: (self.persisted.extend(paths), list(paths))[1]),
                         ("asm_checkpoint_write", lambda *a, **k: False)):
            p = mock.patch.object(km.em, name, fn)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(km, "_CKPT_JUST_WRITTEN", set())
        p.start()
        self.addCleanup(p.stop)
        self.real("_checkpoint_first_sight", "_persist_checkpoints")
        self.quiet()
        self.assertEqual((self.last()["checkpointFirstSight"], self.last()["persistCheckpoints"]), ("closed", "closed"), self.last())
        self.assertIn(SID, km._CKPT_SETTLE_SEEN, "the real first sight recorded the lab's session on the first pass")

    def _pass(self):
        """One pass: (first sight's verdict, the write's verdict, first sight's acts, the write's acts)."""
        seen = self.spy()
        self.passes()
        last = self.last()
        return (last["checkpointFirstSight"], last["persistCheckpoints"], self.acts(seen, "checkpointFirstSight"),
                self.acts(seen, "persistCheckpoints"))

    def _period_age(self, age):
        """Date the session's last periodic write `age` seconds before now, as the period's clock would see it."""
        st, _t = self.km._CKPT_PERIODIC_SEEN[SID]
        self.km._CKPT_PERIODIC_SEEN[SID] = (st, time.monotonic() - age)

    def test_on_a_quiet_pass_both_are_closed_and_the_pass_counts(self):
        g0 = self.gate()
        self.assertEqual(self._pass(), ("closed", "closed", {}, {}))
        self.assertEqual(self.gate()["wouldSkipPass"] - g0["wouldSkipPass"], 1, "every job of the pass closed: the pass counts")
        self.assertEqual(self.persisted, [])

    def test_a_dirty_checkpoint_with_no_settle_and_no_period_is_no_work(self):
        """The dirty set alone takes no session: the write writes only on new settle evidence or a due period."""
        self.dirty = [str(self.leaf)]
        self.assertEqual(self._pass(), ("closed", "closed", {}, {}))
        self.assertEqual(self.persisted, [], "the real write wrote nothing")

    def test_first_sight_is_owed_for_a_listed_session_a_table_lacks_and_records_it(self):
        km = self.km
        for table in (km._CKPT_PERIODIC_SEEN, km._CKPT_SETTLE_SEEN):
            table.pop(SID)
            self.assertEqual(self._pass(), ("owed:unseen", "closed", {"write": 1}, {}), "first sight records it before the write")
            self.assertIn(SID, table)
            self.assertEqual(self._pass(), ("closed", "closed", {}, {}))
        self.assertEqual(self.persisted, [], "a first sight writes nothing")

    def test_a_settle_opens_the_write_and_the_write_writes(self):
        """A states-log row (a turn's settle) and then a turn-end key that moved: each opens the write on its input, the real
        write writes the leaf's document, and the next pass is closed."""
        self.dirty = [str(self.leaf)]
        for n, move in enumerate((lambda: self.states.write_text(self.states.read_text() + '{"state": "working", "t": 2}\n'),
                                  lambda: self.turn.__setitem__(SID, 1_800_000_100)), 1):
            move()
            self.assertEqual(self._pass(), ("closed", "open:ckptSettle", {}, {"write": 1}))
            self.assertEqual(self.persisted, [str(self.leaf)] * n)
            self.assertEqual(self._pass(), ("closed", "closed", {}, {}))

    def test_a_leaf_that_moved_is_owed_once_its_period_is_due_and_not_before(self):
        km = self.km
        self.dirty = [str(self.leaf)]
        with open(self.leaf, "a") as f:
            f.write('{"type": "assistant"}\n')                          # the turn runs on: the leaf grows, no settle
        self.assertEqual(self._pass(), ("closed", "closed", {}, {}), "inside the period: no write, and called closed")
        self._period_age(km.CKPT_PERIOD_S - 0.9)                         # 0.9 s from due: owed (the margin is a second), and
        self.assertEqual(self._pass(), ("closed", "owed:period", {}, {}))   # the write, its clock short of the period, writes nothing
        self.assertEqual(self.persisted, [])
        self._period_age(km.CKPT_PERIOD_S + 1.0)                         # past the period since the last periodic write
        self.assertEqual(self._pass(), ("closed", "owed:period", {}, {"write": 1}))
        self.assertEqual(self.persisted, [str(self.leaf)], "the real write took the period")
        self.assertEqual(self._pass(), ("closed", "closed", {}, {}), "the write reset the period's clock")

    def test_a_table_over_its_bound_is_owed_and_cleared(self):
        km = self.km
        for n in range(4097):
            km._CKPT_SETTLE_SEEN["11111111-2222-3333-4444-%012d" % n] = (0, None)
        self.assertEqual(self._pass(), ("closed", "owed:prune", {}, {"write": 1}))
        self.assertEqual(len(km._CKPT_SETTLE_SEEN), 0, "the write cleared the table")
        self.assertEqual(self._pass(), ("owed:unseen", "closed", {"write": 1}, {}), "first sight records the session again")

    def test_the_writes_own_first_sight_is_owed_when_the_passs_first_sight_has_not_run(self):
        km = self.km
        self.stub("_checkpoint_first_sight", lambda now: None)
        km._CKPT_SETTLE_SEEN.pop(SID)
        km._CKPT_PERIODIC_SEEN.pop(SID)
        self.assertEqual(self._pass()[1::2], ("owed:unseen", {"write": 1}))
        self.assertEqual((SID in km._CKPT_SETTLE_SEEN, SID in km._CKPT_PERIODIC_SEEN, self.persisted), (True, True, []))

    def test_a_period_passed_over_a_leaf_that_never_moved_is_no_work(self):
        """The period predicate's other half: a leaf untouched since its last periodic write owes nothing however old that write
        is, so both jobs are closed, nothing is persisted and the pass counts under wouldSkipPass (without the leaf's own test,
        a session idle past its period would hold the write owed on every pass and no pass could ever count)."""
        km = self.km
        self.dirty = [str(self.leaf)]
        self._period_age(km.CKPT_PERIOD_S + 5)
        g0 = self.gate()
        self.assertEqual(self._pass(), ("closed", "closed", {}, {}))
        self.assertEqual(self.persisted, [])
        self.assertEqual(self.gate()["wouldSkipPass"] - g0["wouldSkipPass"], 1, "every job of the pass closed: the pass counts")

    # ── the detector: each input moved behind the gate's back, after it looked and before the job read ──
    def _behind(self, move):
        """One pass on which the REAL write runs after `move()`, a change landing after the gate read the write's inputs and
        before the write reads them. Returns the write's (wrongQuiet, raced) on that pass."""
        real = _ORIGINALS["_persist_checkpoints"]
        armed = [False]

        def persist(now):
            if armed[0]:
                armed[0] = False
                move()
            return real(now)
        self.stub("_persist_checkpoints", persist)
        self.dirty = [str(self.leaf)]
        w0, r0 = self.wrong("persistCheckpoints"), self.raced("persistCheckpoints")
        armed[0] = True
        self.passes()
        self.assertFalse(armed[0])
        self.assertEqual(self.last()["persistCheckpoints"], "closed", "called closed: the change came after the gate looked")
        return _delta(self.wrong("persistCheckpoints"), w0), _delta(self.raced("persistCheckpoints"), r0)

    def test_a_settle_behind_the_gates_back_is_its_sessions_memo_and_raced(self):
        """The write's own read of the settle evidence differs from the gate's: its work is the session's memo, a miss, and the
        evidence read again moved, so the run raced (the next pass opens the write on it). An enforcing gate runs it then."""
        self.assertEqual(self._behind(lambda: self.states.write_text(self.states.read_text() + '{"state": "idle", "t": 3}\n')),
                         ({}, {"miss": 1}))
        self.assertEqual(self.persisted, [str(self.leaf)], "the real write wrote the leaf's document")
        self.passes()
        self.assertEqual(self.last()["persistCheckpoints"], "open:ckptSettle", "its input moved: the next pass opens it")

    def test_a_leaf_that_grew_behind_the_period_predicates_back_is_raced(self):
        """The period passed with the leaf untouched when the predicate statted it, so the gate called the write closed; the leaf
        grows before the write stats it, and the write takes the period. The write's stat differs from the predicate's: the
        session's memo, raced by the leaf statted again."""
        km = self.km
        self._period_age(km.CKPT_PERIOD_S + 1.0)

        def grow():
            with open(self.leaf, "a") as f:
                f.write('{"type": "assistant"}\n')
        self.assertEqual(self._behind(grow), ({}, {"miss": 1}))
        self.assertEqual(self.persisted, [str(self.leaf)], "the real write took the period")

    def test_a_settle_the_gate_saw_and_the_tables_lost_is_a_wrong_quiet_write(self):
        """The write's own table dropped the session's settle entry for an older key behind the gate's back while its evidence
        stood: the write finds a settle due on evidence the gate read too, so its work is an act and the closed run a wrongQuiet."""
        km = self.km
        self.assertEqual(self._behind(lambda: km._CKPT_SETTLE_SEEN.__setitem__(SID, (0, None))), ({"write": 1}, {}))

    def test_another_sessions_settle_evidence_is_not_this_sessions_race(self):
        """mem:ckptSettle read by the memo's session: with web listed too, a memo naming api and web's states log growing on
        another thread while the write runs is a wrongQuiet; api's own states log growing is a race."""
        km, jd = self.km, self.km.jd
        self.add_session(SID2, "web")
        web_states = jd.STATE / "states" / (SID2 + ".jsonl")
        web_states.write_text('{"state": "idle", "t": 1}\n')
        self.passes(3)
        self.assertEqual(self.last()["persistCheckpoints"], "closed", self.last())
        armed = [None]

        def persist(now):
            fn, armed[0] = armed[0], None
            if fn is None:
                return 0
            th = threading.Thread(target=fn)
            th.start()
            th.join()
            km._jobs_gate_note("miss", SID)                             # api's memo, by a stub of the write
            return 0
        self.stub("_persist_checkpoints", persist)
        for label, f, want in (("web's states log", web_states, ({"miss": 1}, {})),
                               ("api's states log", self.states, ({}, {"miss": 1}))):
            self.passes(2)
            self.assertEqual(self.last()["persistCheckpoints"], "closed", label)
            w0, r0 = self.wrong("persistCheckpoints"), self.raced("persistCheckpoints")
            armed[0] = lambda f=f: f.write_text(f.read_text() + '{"state": "working", "t": 2}\n')
            self.passes()
            self.assertIsNone(armed[0], label)
            self.assertEqual(self.last()["persistCheckpoints"], "closed", label)
            self.assertEqual((_delta(self.wrong("persistCheckpoints"), w0), _delta(self.raced("persistCheckpoints"), r0)), want, label)

    def test_a_first_sight_entry_dropped_behind_the_gates_back_is_a_wrong_quiet_write(self):
        km = self.km
        real = _ORIGINALS["_checkpoint_first_sight"]
        armed = [False]

        def sight(now):
            if armed[0]:
                armed[0] = False
                km._CKPT_PERIODIC_SEEN.pop(SID)
            return real(now)
        self.stub("_checkpoint_first_sight", sight)
        w0 = self.wrong("checkpointFirstSight")
        armed[0] = True
        self.passes()
        self.assertEqual(self.last()["checkpointFirstSight"], "closed")
        self.assertEqual(_delta(self.wrong("checkpointFirstSight"), w0), {"write": 1})
        self.assertIn(SID, km._CKPT_PERIODIC_SEEN)


class Acts(_GateLab):
    """The act hooks, each reached by a walk the gate called closed calling the REAL writer that carries it."""

    def _closed_walk_does(self, act):
        km = self.km
        self.add_session()
        armed = [False]

        def walk(now, live_map):
            if armed[0]:
                armed[0] = False
                act()
        self.stub("_auto_nudge_tick", walk)
        self.quiet()
        w0 = self.wrong("autoNudge")
        armed[0] = True
        self.passes()
        self.assertEqual(self.last()["autoNudge"], "closed")
        return _delta(self.wrong("autoNudge"), w0)

    def test_marking_the_views_dirty(self):
        self.assertEqual(self._closed_walk_does(self.km._mark_views_dirty), {"views": 1})

    def test_waking_the_pusher(self):
        self.assertEqual(self._closed_walk_does(self.km._push_soon), {"push": 1})

    def test_marking_a_sessions_files(self):
        self.assertEqual(self._closed_walk_does(lambda: self.km._files_stat_mark(SID)), {"mark": 1})

    def test_filing_an_error_center_problem(self):
        self.assertEqual(self._closed_walk_does(lambda: self.km._sdk_problem("a synthetic notes-api problem")), {"problem": 1})


class FakeBackendActs(_GateLab):
    """The hand-offs to a backend, each from the REAL job over a fake backend that records them. To make a run the gate calls
    closed hand something off, the job's own state is moved behind the gate's back (a planted miss): the hand-off is the act."""

    def test_a_usage_poll_the_gate_called_closed_is_heard_as_a_send(self):
        km = self.km
        self.add_session()
        be = _FakeBackend()
        km._usage_poll_last[0] = float(T0)
        self.real("_usage_poll_tick")
        self.stub("_sdk", lambda: be)
        self.quiet(T0 + 1)
        self.assertEqual((self.last()["usagePoll"], be.kinds()), ("closed", []))
        km._usage_poll_last[0] = 0.0                                    # the poll's clock forgotten behind the gate's back
        self.passes(1, T0 + 2)
        self.assertEqual((self.wrong("usagePoll"), be.kinds()), ({"send": 1}, ["refresh_usage"]))

    def test_a_retry_upgrade_the_gate_called_closed_is_heard_as_a_send(self):
        km = self.km
        self.add_session()
        be = _FakeBackend()
        km._retry_upgrade_last[0] = float(T0)
        self.real("_retry_upgrade_tick")
        self.stub("_sdk", lambda: be)
        self.quiet(T0 + 1)
        self.assertEqual((self.last()["retryUpgrade"], be.kinds()), ("closed", []))
        km._retry_upgrade_last[0] = 0.0
        self.passes(1, T0 + 2)
        self.assertEqual((self.wrong("retryUpgrade"), be.kinds()), ({"send": 1}, ["retry_model_upgrades"]))

    def test_a_spend_guard_crossing_and_its_clearing_are_acts(self):
        km = self.km
        self.add_session()
        be = _FakeBackend()
        rows = []
        armed = [None]

        def guard(now, live_map):
            fn, armed[0] = armed[0], None
            if fn is not None:
                fn(dict(self.sessions[0]), now)
        self.stub("_spend_guard_tick", guard)
        self.stub("_send_or_park", lambda be_, sid, text, **k: be_.send(sid, text))
        self.stub("_spend_guard_row", lambda kind, text, sid, name, be_, **fields: rows.append(kind))
        self.quiet()
        w0 = self.wrong("spendGuard")
        armed[0] = lambda s, now: km._spend_guard_fire(s, 9.0, 1.0, now, be, [])
        self.passes()
        w1 = self.wrong("spendGuard")
        armed[0] = lambda s, now: km._spend_guard_clear(s, 0.1, 1.0, now, be, [])
        self.passes()
        self.assertEqual((_delta(w1, w0), _delta(self.wrong("spendGuard"), w1)), ({"spendRow": 1}, {"spendRow": 1}))
        self.assertEqual((rows, be.kinds()), (["spend.ceiling", "spend.ceiling.cleared"], ["interrupt", "send"]))


class ApiRetry(_GateLab):
    """The API retry's roads, through the REAL _auto_retry_tick and _fire_api_retry over a fake backend: its send, the next rung
    as its due, the two holds it owes, and its two fault roads."""

    def setUp(self):
        super().setUp()
        km = self.km
        self.add_session()
        self.be = _FakeBackend()
        self.err = {"uuid": "err-0001", "text": "API Error: overloaded"}
        self.real("_auto_retry_tick")
        self.stub("_api_error", lambda path: dict(self.err) if self.err else None)
        p = mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be))
        p.start()
        self.addCleanup(p.stop)
        self.quiet(T0)                                                  # the first pass sends; the episode is then retried
        self.assertEqual((self.last()["autoRetry"], self.be.kinds()), ("closed", ["send"]))

    def test_a_retry_the_gate_called_closed_is_heard_as_a_send(self):
        km = self.km
        self.err["uuid"] = "err-0002"                                    # a new episode, and its ladder forgotten, behind the gate's back
        km._auto_retry_state.clear()
        self.passes(1, T0 + 5)
        self.assertEqual((self.wrong("autoRetry"), self.be.kinds()), ({"send": 1}, ["send", "send"]))

    def test_a_new_episode_inside_the_backoff_notes_the_next_rung_as_its_due(self):
        km = self.km
        self.err["uuid"] = "err-0003"
        nxt = km._retry_gate_state(SID)[1]
        self.assertGreater(nxt, T0)
        self.passes(1, T0)
        self.assertEqual((self.last()["autoRetry"], self.rec("autoRetry")["dues"]), ("closed", [math.ceil(nxt)]))
        self.passes(1, math.ceil(nxt))
        self.assertEqual((self.last()["autoRetry"], self.be.kinds()), ("open:due", ["send", "send"]))

    def test_an_unreadable_suppression_ledger_holds_the_retry_and_is_owed(self):
        with mock.patch.object(self.km, "_retry_suppress_unknown", lambda: True):
            self.err["uuid"] = "err-0004"
            self.passes(1, T0 + 5)
        self.assertEqual(set(self.rec("autoRetry")["owed"]), {"suppressUnknown"})

    def test_a_retry_still_queued_holds_the_next_and_is_owed(self):
        self.be.queued = [self.km.RETRY_MSG]
        self.err["uuid"] = "err-0005"
        self.passes(1, T0 + 5)
        self.assertEqual(set(self.rec("autoRetry")["owed"]), {"queuedRetry"})

    def test_an_error_record_that_cannot_be_read_owes_a_fault(self):
        self.owed_fault_after("autoRetry", lambda: self.fault_once("_path_of"), T0 + 5)

    def test_a_session_whose_retry_raised_owes_a_fault(self):
        self.owed_fault_after("autoRetry", lambda: self.fault_once("_fire_api_retry"), T0 + 5)


class IdleDrive(_GateLab):
    """The idle drive's roads, through the REAL _idle_queue_drive_tick over a fake backend: a young wake entry's floor as its due,
    the drive as an act that leaves the next pass owed, and its fault road."""

    def setUp(self):
        super().setUp()
        self.add_session()
        self.be = _FakeBackend()
        self.entries = [{"wrapper": True, "ts": _iso(T0 - 5), "pos": 7}]
        self.real("_idle_queue_drive_tick")
        self.stub("_sdk", lambda: self.be)
        self.stub("_undelivered_wake_tail", lambda path: ([dict(e) for e in self.entries], 7))
        self.stub("_api_error", lambda path: None)

    def test_a_young_wake_entrys_floor_is_the_drives_due(self):
        km = self.km
        self.quiet(T0)
        due = math.ceil(T0 - 5 + km._WAKE_DRIVE_FLOOR_S)
        self.assertEqual((self.last()["idleQueueDrive"], self.rec("idleQueueDrive")["dues"], self.be.kinds()), ("closed", [due], []))

    def test_a_drive_the_gate_called_closed_is_a_send_and_leaves_the_next_pass_owed(self):
        km = self.km
        self.quiet(T0)
        self.entries[0]["ts"] = _iso(T0 - 2 * km._WAKE_DRIVE_FLOOR_S)    # the entry aged behind the gate's back
        self.passes(1, T0 + 1)
        self.assertEqual((self.wrong("idleQueueDrive"), self.be.kinds()), ({"send": 1}, ["drive_idle_queue"]))
        self.assertEqual(set(self.rec("idleQueueDrive")["owed"]), {"drive"})

    def test_a_session_whose_tail_raised_owes_a_fault(self):
        self.quiet(T0)
        self.owed_fault_after("idleQueueDrive", lambda: self.fault_once("_undelivered_wake_tail"), T0 + 1)


class DeferralFaults(_GateLab):
    """The deferral sweep's two fault roads, through the REAL sweep over a record that stands (the to-do sync is due)."""

    def setUp(self):
        super().setUp()
        km, jd = self.km, self.km.jd
        self.add_session()
        gid = SID + ":g1"
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps({"rompUuid": SID, "seq": 1, "placements": {}, "confirming": [],
                                                              "nodes": {gid: _node(gid, "Ship the exporter")},
                                                              "status": {gid: "working"}, "rev": 1}))
        self.ledger(deferred={gid: {"sid": SID, "why": "the agent's to-do sync is due"}})
        self.real("_deferral_sweep_tick")
        self.stub("_plan_sync_pending", lambda sid, nodes: True)
        self.quiet()
        self.assertEqual(list(km._auto_nudge_data().get("deferred") or {}), [gid], "the record stands")

    def test_a_store_that_cannot_be_read_owes_a_fault(self):
        self.owed_fault_after("deferralSweep", lambda: self.fault_once("load_goals_shared", obj=self.km.jd))

    def test_a_sync_check_that_raised_owes_a_fault(self):
        self.owed_fault_after("deferralSweep", lambda: self.fault_once("_plan_sync_pending"))


class TheSevenFaults(_GateLab):
    """Point three of the review, driven: each except that swallowed a fault and kept the job's record now owes the fault, so the
    pass after it is owed and evaluates the job as if it had never completed. Seeded with the toggle and task tracking on (with
    either off and a session alive, the walk is owed every pass and hides the walk's roads)."""

    def _walk_world(self, **ledger):
        self.add_session()
        self.real_walk()
        if ledger:
            self.ledger(enabled=True, **ledger)
        self.quiet(T0)

    def test_a_look_that_raised_before_its_key_hook(self):
        self._walk_world()
        err = self.owed_fault_after("autoNudge", lambda: self.fault_once("_auto_nudge_session"), T0 + 1)
        self.assertIn("auto-nudge (session", err)

    def test_the_relay_tick(self):
        self._walk_world()
        self.assertIn("relay tick:", self.owed_fault_after("autoNudge", lambda: self.fault_once("_relay_tick"), T0 + 1))

    def test_the_debt_backstop(self):
        self._walk_world()
        self.assertIn("debt-backstop:", self.owed_fault_after("autoNudge", lambda: self.fault_once("_debt_backstop_tick"), T0 + 1))

    def test_the_awaiting_wake_outcomes(self):
        self._walk_world()
        err = self.owed_fault_after("autoNudge", lambda: self.fault_once("_awaiting_wake_outcomes"), T0 + 1)
        self.assertIn("awaiting-wake outcomes:", err)

    def test_one_awaiting_wake_record(self):
        """The outcomes' per-record except: a dead session's wake record whose store cannot be read."""
        self._walk_world(nudged={GONE + ":g1": {"wake": True, "at": T0 - 10}})
        err = self.owed_fault_after("autoNudge", lambda: self.fault_once("load_goals_shared", obj=self.km.jd,
                                                                         match=lambda sid, *a, **k: sid == GONE), T0 + 1)
        self.assertIn("awaiting-wake outcome (%s:g1)" % GONE, err)

    def test_the_limit_pauses_usage_read(self):
        self.add_session()
        self.real("_auto_pause_on_limit")
        self.stub("_account_limited", lambda: [])
        self.quiet()
        self.owed_fault_after("autoPauseOnLimit", lambda: self.fault_once("_account_limited"))

    def test_the_resumes_usage_read(self):
        self.add_session()
        (self.root / "retry-paused.json").write_text(json.dumps({"paused": True, "reason": "limit", "t": T0 - 60}))
        self.real("_auto_resume_retry")
        self.stub("_account_limited", lambda: ["fiveHour"])             # the window holds: the pause stands
        self.quiet()
        self.owed_fault_after("autoResumeRetry", lambda: self.fault_once("_account_limited"))

    def test_the_debt_backstops_due_only_work_survives_a_fault(self):
        """The due-only case: a reminder whose backstop instant is hours away is work with nothing to do but note its due. A fault
        before the note would leave a closed record with no due, so the escalation would never run under an enforcing gate; the
        fault is owed, the next run notes the due again, and the walk opens at it."""
        km = self.km
        fire = T0 - 100
        self._walk_world(debtNudged={"%s>%s:%d" % (ASKER, SID, T0 - 200): fire})
        due = math.floor(fire + km.NUDGE_DEFER_BACKSTOP_SECS) + 1
        self.passes(2, T0)
        self.assertEqual((self.last()["autoNudge"], self.rec("autoNudge")["dues"]), ("closed", [due]), "the backstop's instant")
        self.owed_fault_after("autoNudge", lambda: self.fault_once("_debt_backstop_tick"), T0 + 1)
        self.assertEqual(self.rec("autoNudge")["dues"], [due], "the runs after the fault noted it again")
        self.passes(1, due - 1)
        self.assertEqual(self.last()["autoNudge"], "closed")
        with redirect_stderr(io.StringIO()):
            self.passes(1, due)
        self.assertEqual(self.last()["autoNudge"], "open:due")

    def test_a_walk_that_finds_another_in_flight_is_owed(self):
        km = self.km
        self.add_session()
        self.real_walk()
        self.assertTrue(km._AUTO_NUDGE_TICK_LOCK.acquire(blocking=False))
        try:
            self.passes()
        finally:
            km._AUTO_NUDGE_TICK_LOCK.release()
        self.assertEqual(set(self.rec("autoNudge")["owed"]), {"lock"})

    def test_a_spend_rate_that_raised_owes_a_fault(self):
        """The census's own find: the spend guard's per-session rate read swallowed its fault and kept the record."""
        self.add_session()
        self.real("_spend_guard_tick")
        self.stub("_spend_rate_usd_per_hour", lambda *a, **k: 0.0)
        self.stub("_spend_guard_seed", lambda: None)
        self.stub("_sweep_spend_trees", lambda: None)
        self.quiet()
        err = self.owed_fault_after("spendGuard", lambda: self.fault_once("_spend_rate_usd_per_hour"))
        self.assertIn("spend-guard rate", err)


class TheLook(_GateLab):
    """The walk's look hooks, through the REAL _nudge_look_gated wrapper (around a look of the test's) and the real
    _nudge_look_done: a look that completes is on record, one that never parsed is left unrecorded, a recorded flip is the
    walk's due, a verdict under the closer toggle read with it off is owed, and a wake-only look is owed."""

    def _gated(self, parsed=True, verdict="working", wake_only=False):
        km = self.km
        self.add_session()
        self.addCleanup(km._NUDGE_LOOK_ASKERS.pop, SID, None)

        def look(s, now, live_map, nudged, waitfor, alive_ids, wake_only_, cleared, reminders):
            km._NUDGE_HORIZON.parsed = parsed
            return verdict
        gated = km._nudge_look_gated(look)
        self.stub("_auto_nudge_tick", lambda now, live_map: gated(dict(self.sessions[0]), now, live_map, {}, {}, {SID}, wake_only,
                                                                 None, True))
        self.passes(1, T0)
        return set(self.rec("autoNudge")["owed"])

    def test_a_look_that_completes_is_on_record(self):
        self.assertEqual(self._gated(), set())
        self.passes(1, T0 + 1)
        self.assertEqual(self.last()["autoNudge"], "closed")

    def test_a_look_that_never_parsed_is_left_unrecorded(self):
        self.assertEqual(self._gated(parsed=False), {"unrecorded"})

    def test_a_wake_only_look_is_owed(self):
        self.assertEqual(self._gated(wake_only=True), {"wakeOnly"})

    def _done(self, notes, verdict):
        km = self.km
        self.add_session()
        self.addCleanup(km._NUDGE_LOOK_ASKERS.pop, SID, None)

        def walk(now, live_map):
            s = dict(self.sessions[0])
            skip, st, _v = km._nudge_look_check(s, now)
            if not skip:
                km._nudge_look_done(s, st, notes, verdict)
        self.stub("_auto_nudge_tick", walk)
        self.passes(1, T0)
        return self.rec("autoNudge")

    def test_a_recorded_flip_is_the_walks_due(self):
        self.assertEqual(self._done([T0 + 40], "working")["dues"], [T0 + 40])

    def test_a_verdict_recorded_under_the_closer_read_with_it_off_is_owed(self):
        with mock.patch.object(self.km.jd, "CLOSER_ON", False):
            self.assertEqual(set(self._done([], "closer-unsettled")["owed"]), {"closer"})


class _RelayWorld(_GateLab):
    """A worker's block toward the peer that delegated its goal, filed by the REAL judge (apply_close over a delegated goal, no
    open ask), its store and queue entry published by the real save; the bus a fake that records sends and recalls. The walk the
    gate observes takes the pass's shared snapshot and runs the REAL relay tick (_relay_tick), nothing else, so the walk's other
    inputs stand still."""

    def setUp(self):
        super().setUp()
        km, jd = self.km, self.km.jd
        self.add_session()
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        for sid, name in ((SID, "api"), (MANAGER, "web")):
            (jd.NAMES / sid).write_text("%s\t/TESTDIR\t#abcdef\n" % name)
        jd.MESSAGES.parent.mkdir(parents=True, exist_ok=True)
        jd.MESSAGES.write_text("")
        jd._PEER_ASK_CACHE[0] = None
        jd._DELEG_CACHE[0] = None
        self.addCleanup(lambda: (jd._PEER_ASK_CACHE.__setitem__(0, None), jd._DELEG_CACHE.__setitem__(0, None)))
        bad = km._RELAY_BAD["n"]
        self.addCleanup(km._RELAY_BAD.__setitem__, "n", bad)
        self.t_close = int(time.time()) - 3600                          # the block's evidence time: an hour ago on the real clock
        self.sent, self.recalls = [], []
        self.answer = {"ok": True, "to": MANAGER}
        self.recall_says = "withdrawn"

        def bus_send(payload):
            self.sent.append(payload)
            return (True, "", False, dict(self.answer)) if self.answer is not None else (False, "the bus is down", False, {})

        def bus_recall(sid, mid):
            self.recalls.append(mid)
            return self.recall_says
        self.stub("_bus_send_relay", bus_send)
        self.stub("_bus_recall_relay", bus_recall)
        self.stub("_auto_nudge_tick", self.walk)
        top, self.step = SID + ":g1", SID + ":g2"
        t = self.t_close - 400
        self.store = {"rompUuid": SID, "seq": 2, "placements": {}, "status": {top: "working"}, "confirming": [],
                      "nodes": {top: _node(top, "Ship the exporter", t=t,
                                           origin={"peer": MANAGER, "goalId": MANAGER + ":g7", "msgId": "m-deleg-0001"}),
                                self.step: _node(self.step, "Ask which client to change", parent=top, t=t + 60)}}
        with redirect_stderr(io.StringIO()):
            jd.apply_close(self.store, [self.store["nodes"][self.step]],
                           {"done": {}, "block": {1: "cannot move further without an answer"}, "awaiting": {}}, t=self.t_close)
        self.assertIn("relayWanted", self.store["nodes"][self.step], "the judge filed the block as a relay toward the delegator")

    def walk(self, now, live_map):
        """The walk the gate observes: the shared snapshot taken as the real walk's key takes it, then the REAL relay tick."""
        km = self.km
        for s in km._alive_sessions(now, live_map):
            km._session_files_stat(s)
        return km._relay_tick(now, {SID})

    def queue(self):
        d = self.km.jd._relay_queue_dir()
        return sorted(f.name for f in d.iterdir()) if d.is_dir() else []

    def elsewhere(self, edit):
        """Load the store, `edit` it and publish it as another process would: no count in this process's judge module, no mark."""
        jd = self.km.jd
        st = jd.load_goals(SID)
        edit(st, st["nodes"][self.step])
        with mock.patch.object(jd, "_session_file_written", lambda fsid: None):
            jd.save_goals(SID, st)

    def end_the_wait(self, st, nd):
        self.km.jd.record_verdict(st, nd, "romp", "awaiting", self.t_close + 300, why="", lift=True, end_ev=self.t_close + 300)


class Relay(_RelayWorld):
    """Point two of the review: the relay's sends, recalls and writes are acts, and its own quiet key moving is a miss."""

    def test_an_entry_a_judge_thread_writes_and_the_relay_sends_in_one_run_is_a_wrong_quiet(self):
        """The judge's thread publishes the store and its entry while the walk runs; the relay sends the question in the same
        run. Its declared inputs moved during the run, but the run acted, and an act is never raced."""
        jd = self.km.jd
        self.quiet()
        self.assertEqual((self.last()["autoNudge"], self.queue()), ("closed", []))
        def walk(now, live_map):
            th = threading.Thread(target=lambda: jd.save_goals(SID, self.store))
            th.start()
            th.join()
            self.walk(now, live_map)
        self.stub("_auto_nudge_tick", walk)
        self.passes()
        self.assertEqual(len(self.sent), 1, "the relay sent the question")
        self.assertEqual((self.wrong("autoNudge"), self.raced("autoNudge")), ({"send": 1, "mark": 1, "write": 1}, {}))

    def test_a_recall_after_another_process_ended_the_wait_is_a_wrong_quiet(self):
        """The review's road: the question was handed to a far host (pending), another process ended the wait, and the next pass's
        relay, its quiet key moved, recalls it and saves the store. The gate called the walk closed: the store moved with no
        signal in this process."""
        jd = self.km.jd
        self.answer = {"ok": True, "id": "px-0001", "parked": "TESTHOST"}
        jd.save_goals(SID, self.store)
        self.quiet()
        self.passes()
        self.assertEqual((self.last()["autoNudge"], len(self.sent)), ("closed", 1), "parked, then quiet")
        w0 = self.wrong("autoNudge")
        self.elsewhere(self.end_the_wait)
        self.passes()
        self.assertEqual(self.recalls, ["px-0001"])
        self.assertEqual((self.last()["autoNudge"], _delta(self.wrong("autoNudge"), w0)),
                         ("closed", {"miss": 1, "send": 1, "mark": 1, "write": 1}))

    def test_a_marker_retired_on_a_far_host_is_recalled_by_the_sweep_an_act(self):
        """The judge retires a marker it had handed to a far host (a new wait replaces it): the node owes a recall, its own entry
        brings the relay to it, and the recall sweep asks the bus. The entry moved the queue, so the walk is open: the spy shows
        the acts of that run."""
        jd = self.km.jd
        self.answer = {"ok": True, "id": "px-0002", "parked": "TESTHOST"}
        jd.save_goals(SID, self.store)
        self.quiet()
        self.passes()
        seen = self.spy()
        st = jd.load_goals(SID)
        jd._relay_retire_marker(st, st["nodes"][self.step])
        jd.save_goals(SID, st)
        self.assertIn(jd._relay_recall_entry_path(SID, self.step).name, self.queue())
        self.passes()
        self.assertEqual(self.recalls, ["px-0002"])
        self.assertEqual(self.acts(seen, "autoNudge").get("send"), 1, seen)

    def test_a_recall_still_owed_with_no_entry_writes_its_entry_an_act(self):
        """A retired marker's entry spent while its recall is still owed (the bus could not be asked) and the recall's own entry
        gone: the store's pass writes that entry back, a write with no save of its own."""
        jd = self.km.jd
        self.answer = {"ok": True, "id": "px-0003", "parked": "TESTHOST"}
        self.recall_says = "unknown"
        jd.save_goals(SID, self.store)
        self.quiet()
        self.passes()
        st = jd.load_goals(SID)
        jd._relay_retire_marker(st, st["nodes"][self.step])
        jd.save_goals(SID, st)
        recall = jd._relay_recall_entry_path(SID, self.step)
        recall.unlink()
        seen = self.spy()
        with redirect_stderr(io.StringIO()):
            self.passes()
        self.assertTrue(recall.exists(), "the recall's entry was written back")
        self.assertGreaterEqual(self.acts(seen, "autoNudge").get("write", 0), 2, "the spent entry's unlink and the recall's write")

    def test_an_entry_for_a_marker_the_node_no_longer_carries_is_rewritten_an_act(self):
        jd = self.km.jd
        jd.save_goals(SID, self.store)
        entry = jd._relay_entry_path(SID, self.step)
        self.assertTrue(entry.exists())
        jd._relay_write_entry(SID, self.step, "an-older-marker", 0)     # the entry names a marker the node moved on from
        seen = self.spy()
        self.passes()
        self.assertEqual(self.acts(seen, "autoNudge").get("write"), 1, seen)
        self.assertEqual(json.loads(entry.read_text())["marker"], self.store["nodes"][self.step]["relayWanted"]["id"])

    def test_a_relay_the_bus_could_not_take_is_owed(self):
        jd = self.km.jd
        self.answer = None
        jd.save_goals(SID, self.store)
        with redirect_stderr(io.StringIO()):
            self.passes()
        self.assertEqual((len(self.sent), set(self.rec("autoNudge")["owed"])), (1, {"relay"}))

    def test_a_store_whose_pass_raised_is_owed(self):
        jd = self.km.jd
        jd.save_goals(SID, self.store)
        self.fault_once("_relay_store")
        with redirect_stderr(io.StringIO()) as err:
            self.passes()
        self.assertEqual(set(self.rec("autoNudge")["owed"]), {"relay"})
        self.assertIn("relay (%s)" % SID[:8], err.getvalue())

    def test_a_hold_on_an_unknown_send_is_the_walks_due_while_it_stands(self):
        """A send whose outcome is unknown is held for RELAY_UNKNOWN_HOLD: the pass that stores the hold notes its end as the
        walk's due, and so does each pass that skips on it."""
        km, jd = self.km, self.km.jd

        def unknown(payload):
            self.sent.append(payload)
            return False, "timed out", False, {"unknown": True}
        self.stub("_bus_send_relay", unknown)
        jd.save_goals(SID, self.store)
        with redirect_stderr(io.StringIO()):
            self.passes()
        self.assertEqual(set(self.rec("autoNudge")["owed"]), {"relay"}, "unknown: retried next pass")
        end = int(jd.load_goals(SID)["nodes"][self.step]["relayWanted"]["unknownAt"]) + km.RELAY_UNKNOWN_HOLD
        self.passes()
        self.assertEqual(self.rec("autoNudge")["dues"], [end], "the pass that stores the hold")
        self.passes()
        self.assertEqual((self.last()["autoNudge"], self.rec("autoNudge")["dues"]), ("closed", [end]), "a pass that skips on it")
        self.assertEqual(len(self.sent), 1)

    def test_a_crashed_writers_temp_file_is_due_at_its_sweep_and_its_unlink_is_an_act(self):
        jd = self.km.jd
        d = jd._relay_queue_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".tmp-g2-0-abcdef"
        tmp.write_text("{}")
        os.utime(tmp, (T0 - 10, T0 - 10))
        self.passes(1, T0)
        self.assertEqual(self.rec("autoNudge")["dues"], [T0 - 10 + 61], "the first second the sweep takes it")
        seen = self.spy()
        self.passes(1, T0 + 51)
        self.assertEqual((self.last()["autoNudge"], tmp.exists()), ("open:due", False))
        self.assertEqual(self.acts(seen, "autoNudge").get("write"), 1)

    def test_a_temp_file_gone_by_its_stat_owes_nothing_and_a_refused_stat_or_unlink_owes_a_fault(self):
        """A name the sweep's glob listed that is gone by its stat (a judge thread renaming its temp file into the entry it wrote
        lands between the two; a dangling name stands in for it here) is no fault, so the walk keeps its record and closes. An
        error that persists, the stat or the unlink refused, owes a fault on every pass it stands."""
        km, jd = self.km, self.km.jd
        d = jd._relay_queue_dir()
        d.mkdir(parents=True, exist_ok=True)
        gone = d / ".tmp-g2-0-renamed"
        os.symlink(str(self.root / "no-such-target"), str(gone))       # listed, and nothing there by the stat
        self.quiet()
        self.assertEqual((self.last()["autoNudge"], km._JOBS_GATE["lost"].get("autoNudge")), ("closed", None), "no fault")
        gone.unlink()
        denied = d / ".tmp-g2-0-denied"
        denied.write_text("{}")
        os.utime(denied, (T0 - 100, T0 - 100))                          # old enough that the sweep unlinks it
        cls = type(denied)
        for op in ("stat", "unlink"):
            real = getattr(cls, op)

            def refused(path, *a, _real=real, **k):
                if path.name == denied.name:
                    raise PermissionError(13, "a synthetic refusal", str(path))
                return _real(path, *a, **k)
            with mock.patch.object(cls, op, refused):
                for _ in range(2):
                    self.passes(1, T0)
                    self.assertEqual(km._JOBS_GATE["lost"].get("autoNudge"), "fault", "%s refused: owed while it stands" % op)
            self.assertTrue(denied.exists(), op)
        self.passes(1, T0)
        self.assertFalse(denied.exists(), "the refusal gone, the sweep takes it")

    def test_a_malformed_entrys_drop_is_an_act(self):
        jd = self.km.jd
        d = jd._relay_queue_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "junk.json").write_text("not an entry")
        seen = self.spy()
        with redirect_stderr(io.StringIO()):
            self.passes()
        self.assertEqual(self.acts(seen, "autoNudge").get("write"), 1)
        self.assertFalse((d / "junk.json").exists())


class RelayScope(_RelayWorld):
    """The relay's quiet key names its session (CallSites' fourth road): a pending relay of api's whose store another process
    rewrote moves api's quiet key, and the relay's pass over it does nothing more, so its only evidence is that miss; with
    another session alive and that session's live-tail revision moving while the walk runs, the miss is api's wrongQuiet."""

    def test_the_relays_quiet_key(self):
        jd = self.km.jd
        self.add_session(SID2, "tests")
        self.answer = {"ok": True, "id": "px-0005", "parked": "TESTHOST"}
        jd.save_goals(SID, self.store)
        self.quiet()
        self.passes()
        self.assertEqual((self.last()["autoNudge"], len(self.sent)), ("closed", 1), "parked, then quiet")
        w0, r0 = self.wrong("autoNudge"), self.raced("autoNudge")
        self.elsewhere(lambda st, nd: st.__setitem__("note", "a note another process wrote"))

        def walk(now, live_map):
            th = threading.Thread(target=lambda: self.revs.__setitem__(SID2, self.revs.get(SID2, 0) + 1))
            th.start()
            th.join()
            return self.walk(now, live_map)
        self.stub("_auto_nudge_tick", walk)
        self.passes()
        self.assertEqual((self.last()["autoNudge"], len(self.sent), self.recalls), ("closed", 1, []))
        self.assertEqual((_delta(self.wrong("autoNudge"), w0), _delta(self.raced("autoNudge"), r0)), ({"miss": 1}, {}))


class NotesLift(_GateLab):
    """The notes job's lift of a finished session's working note writes the notes directory, which the job declares: the lift is
    an act, so a run the gate called closed that lifted the note is a wrongQuiet, never raced by its own write; one that kept
    the note, its memo moved and nothing it reads moving, is a wrongQuiet too."""

    def test_a_lift_is_an_act_and_a_kept_note_with_a_moved_memo_a_miss(self):
        km = self.km
        self.add_session()
        notes = self.root / "working"
        notes.mkdir(parents=True, exist_ok=True)
        (notes / SID).write_text("owns the notes-api exporter\n")
        open_goal = [True]
        self.real("_clear_done_working_notes")
        self.stub("_session_working", lambda turns: False)
        self.stub("_open_top_goal", lambda sid: open_goal[0])
        p = mock.patch.object(km.jd, "parsed_session", lambda *a, **k: {"turns": [{"ended": True}]})
        p.start()
        self.addCleanup(p.stop)
        self.quiet()
        self.assertEqual(self.last()["clearDoneNotes"], "closed")
        w0, r0 = self.wrong("clearDoneNotes"), self.raced("clearDoneNotes")
        km._TICK_SEEN.pop(("working-notes", SID), None)                 # the job's memo forgets the session behind the gate's back
        self.passes()
        w1, r1 = self.wrong("clearDoneNotes"), self.raced("clearDoneNotes")
        self.assertEqual((_delta(w1, w0), _delta(r1, r0)), ({"neverSeen": 1}, {}), "kept: nothing it reads moved")
        open_goal[0] = False
        km._TICK_SEEN.pop(("working-notes", SID), None)
        self.passes()
        self.assertEqual(self.last()["clearDoneNotes"], "closed")
        self.assertEqual((_delta(self.wrong("clearDoneNotes"), w1), _delta(self.raced("clearDoneNotes"), r1)),
                         ({"neverSeen": 1, "write": 1}, {}), "lifted: an act, never raced by its own write")
        self.assertFalse((notes / SID).exists())


class ThePusher(_GateLab):
    """T7 and point five of the review: the gate observes the jobs thread's own runs alone."""

    def test_the_pushers_jobs_record_nothing_under_the_gate(self):
        """The cycle with no session alive, as tests/test_jobs_thread_split.py drives it: no pass is open anywhere."""
        km = self.km
        with redirect_stderr(io.StringIO()):
            km._pusher_cycle()
        g = self.gate()
        self.assertEqual((g["ticks"], g["byJob"], self.last()), (0, {}, {}))

    def test_a_hook_from_another_thread_during_an_observed_run_records_nothing(self):
        """A writer on another thread marks the views while the walk, called closed, runs: the mark is not the walk's act."""
        km = self.km
        self.add_session()
        armed = [False]

        def walk(now, live_map):
            if armed[0]:
                armed[0] = False
                th = threading.Thread(target=lambda: (km._mark_views_dirty(), km._jobs_gate_note("send"), km._jobs_gate_owe("x")))
                th.start()
                th.join()
        self.stub("_auto_nudge_tick", walk)
        self.quiet()
        armed[0] = True
        self.passes()
        self.assertEqual((self.last()["autoNudge"], self.wrong("autoNudge"), set(self.rec("autoNudge")["owed"])), ("closed", {}, set()))

    def test_a_pusher_cycle_between_jobs_while_the_jobs_thread_holds_a_pass_leaves_the_record_alone(self):
        """A pusher cycle on its own thread after the jobs thread opened its pass and before its first job (reached by wrapping
        _jobs_gate_tick_open; from inside a job the other thread would only meet the nested-run return): none of the pusher's
        jobs reaches lastPass or byJob."""
        km = self.km
        self.add_session()
        real_open = km._jobs_gate_tick_open
        ran = []

        def open_then_push(now, live_map):
            tick = real_open(now, live_map)
            th = threading.Thread(target=lambda: ran.append(km._pusher_cycle()))
            with redirect_stderr(io.StringIO()):
                th.start()
                th.join()
            return tick
        self.stub("_jobs_gate_tick_open", open_then_push)
        g = self.passes(2)
        self.assertEqual(len(ran), 2, "the pusher cycled during both passes")
        jobs = set(re.findall(JOB_NAME_RE, inspect.getsource(km._jobs_pass)))
        self.assertLessEqual(set(self.last()), jobs, self.last())
        self.assertLessEqual(set(g["byJob"]), jobs, sorted(g["byJob"]))
        self.assertEqual(g["ticks"], 2)


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

    def test_the_knob_reads_session_hosts_off_words_and_says_once_that_it_does_not_know_a_word(self):
        km = self.km
        self.add_session()
        knob = self.root / km.JOBS_GATE_FILE
        for word in ("0", "false", "No", "OFF"):
            knob.write_text(word + "\n")
            g = self.passes()
            self.assertEqual((g["mode"], g["asked"]), ("off", word.lower()), word)
        for word in ("yes", "1", "true", "shadow", ""):
            knob.write_text(word + "\n")
            g = self.passes()
            self.assertEqual(g["mode"], "shadow", word)
        with redirect_stderr(io.StringIO()) as err:
            knob.write_text("maybe\n")
            g = self.passes()
            self.assertEqual((g["mode"], g["asked"]), ("shadow", "maybe"), "a word it does not know reads as the default")
            knob.write_text("maybe  \n")                                 # read again, the same word: not said again
            self.passes()
        self.assertEqual(err.getvalue().count("jobs gate: jobs-gate says 'maybe'"), 1, err.getvalue())

    def test_the_first_pass_reads_the_knob_so_the_default_is_the_reads_whatever_the_boot_literal(self):
        """The mode key starts at a sentinel no stat equals and no missing file equals, so the first pass reads the knob: with no
        file the default is shadow even from a literal that said off. The starting value is the kernel's own, read when this
        module was imported, before any pass (the drive below sets the sentinel itself, so it alone would pass on any start)."""
        km = self.km
        self.assertEqual(_AT_IMPORT["modeKey"], km._JOBS_GATE_BOOT_KEY, "the kernel starts at the sentinel: %r" % (_AT_IMPORT,))
        self.assertIsNotNone(km._JOBS_GATE_BOOT_KEY, "a missing file's key (None) must not equal the start")
        self.add_session()
        km._JOBS_GATE_MODE[0] = "off"
        km._JOBS_GATE["modeKey"] = km._JOBS_GATE_BOOT_KEY
        g = self.passes()
        self.assertEqual((g["mode"], g["ticks"]), ("shadow", 1))


class Invariants(_GateLab):
    """Two of the description's must-not-change lines, each pinned by a test that breaks when the line does."""

    def test_the_gates_observation_is_never_inside_the_jobs_cpu(self):
        """The gate opens before the job's clocks start: an observation that burns CPU lands in tickCpuMs, never in the job's
        cpuByJob."""
        km = self.km
        self.add_session()
        self.quiet()
        real = km._jobs_gate_predict

        def slow(name, tick):
            if name == "deathSweep":
                t = time.thread_time()
                while time.thread_time() - t < 0.12:
                    pass
            return real(name, tick)
        self.stub("_jobs_gate_predict", slow)
        before = km._PERF_STATS.snapshot()["jobs"]
        self.passes()
        after = km._PERF_STATS.snapshot()["jobs"]
        job = after["cpuByJob"]["deathSweep"]["cpuMs"] - before["cpuByJob"]["deathSweep"]["cpuMs"]
        gate = after["gate"]["tickCpuMs"] - before["gate"]["tickCpuMs"]
        self.assertLess(job, 60.0, "the observation's 120 ms of CPU is not the job's")
        self.assertGreater(gate, 100.0, "it is the gate's")

    def test_the_gates_own_stat_never_fills_the_passs_shared_snapshot(self):
        """The gate stats a marked session at the lift's turn, before any reader; a job after the lift writes the session's store;
        the walk's own read of the shared snapshot must see that write, which it would not if the gate's stat had filled it."""
        km, jd = self.km, self.km.jd
        self.add_session()
        self.quiet()
        store = jd.GOALDIR / (SID + ".json")
        seen = []

        def sweep(now, live_map):
            jd.GOALDIR.mkdir(parents=True, exist_ok=True)
            store.write_text(json.dumps({"nodes": {}, "rev": 9, "pad": "x" * 64}))
        self.stub("_death_sweep_tick", sweep)
        self.stub("_auto_nudge_tick", lambda now, live_map: seen.append(km._session_files_stat(dict(self.sessions[0]))[5]))
        km._files_stat_mark(SID)                                        # the next pass stats the session afresh
        self.passes()
        self.assertEqual(seen, [store.stat().st_size], "the walk read the store the death sweep wrote")


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

    def test_each_of_the_spend_windows_three_finds_reaches_the_floor(self):
        """The REAL _spend_window_files over a synthetic agents tree: a hot file that grew, a directory that moved (a new agent
        file), and a file the loaded memo's spread re-stat found moved, each heard once as an unsignaled move."""
        km = self.km
        tx = self.add_session()
        sub = tx.with_suffix("") / "subagents"
        sub.mkdir(parents=True)
        a = sub / "agent-a.jsonl"
        a.write_text("{}\n")
        t0 = time.time()
        os.utime(a, (t0, t0))
        os.utime(sub, (t0 - 5, t0 - 5))
        self.stub("_spend_guard_tick", lambda now, live_map: km._spend_window_files(str(tx), time.time() - 600, time.time(), stat=True))
        self.quiet()                                                    # the boot pass skips the guard; the next lists the tree

        def unsignaled():
            return self.gate()["treeFloor"]["unsignaled"]
        n0 = unsignaled()
        os.utime(a, (t0 + 7, t0 + 7))                                   # a hot agent file grew
        self.passes()
        self.assertEqual(unsignaled() - n0, 1, "the hot file's stat")
        (sub / "agent-b.jsonl").write_text("{}\n")
        os.utime(sub, (t0 + 9, t0 + 9))                                 # a new agent file moved its directory
        self.passes()
        self.assertEqual(unsignaled() - n0, 2, "the directory's stat")
        km._SPEND_TREE_CACHE[str(tx)]["restat"] = [str(a)]              # a loaded memo's spread re-stat, its slice holding agent-a
        os.utime(a, (t0 + 11, t0 + 11))
        self.passes()
        self.assertEqual(unsignaled() - n0, 3, "the spread re-stat")


def _capture_originals():
    """The real functions behind the lab's quiet stubs, taken from the shared kernel module. A test module that assigns a stub
    and restores it late (tests/test_jobs_thread_split.py's walk stubs, whose cleanup default is read after the assignment)
    leaves its stub on the shared module, so these are taken when this module is imported, before any module's test runs, and
    a function that is not the kernel's own is refused loudly, never handed to a test as the real one."""
    km = kernel_module()
    out = {}
    for name in tuple(QUIET) + ("_auto_nudge_tick", "_retry_upgrade_tick"):
        fn = getattr(km, name)
        if getattr(fn, "__module__", None) != km.__name__:
            raise RuntimeError("tests/test_jobs_gate.py: the kernel's %s is %r, not the kernel's own function: a stub another "
                               "module left in place (the originals must be taken before any test runs)" % (name, fn))
        out[name] = fn
    return out


_ORIGINALS = _capture_originals()           # at import: before any module's test, so no module's leftover stub is taken
_AT_IMPORT = {"modeKey": kernel_module()._JOBS_GATE["modeKey"]}   # the knob's stat key as the kernel starts, before any pass
_REAL_GATE_BACKENDS = getattr(kernel_module(), "_JOBS_GATE_BACKENDS", None)   # the race read's own backends (TheRaceRead)
_REAL_SESSIONS_LIVE = kernel_module().Sessions.__dict__["live"]                 # the liveness read, as the class defines it


if __name__ == "__main__":
    unittest.main()
