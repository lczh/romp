#!/usr/bin/env python3
"""The per-restart CUT LEDGER (T121, 2026-08-27): every restart writes ONE row to
restart-cuts.jsonl naming what it cut — the drain's effect is measurable only if clean restarts
write rows too (an empty cutTurns list is the success metric, not noise). The row joins the
restart-audit tail for WHO asked, counts the persisted kernel watches (which SURVIVE restarts by
construction — they ride as context, not cuts), and its docstring documents the two things the
kernel cannot do: count in-session watchers/Claude-side workflows (invisible here — the kernel
watch primitive is the fix), and un-write the CLI's own interrupted-by-user transcript stamps
(romp never rewrites CLI transcripts; romp's own records already distinguish machine cuts).
Hermetic state; synthetic sids only."""
import contextlib
import errno
import gc
import io
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — module import runs boot reconcile against the state root.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_cuts", os.path.join(BIN, "romp-kernel"))
jd = km.jd
cxb = load_source("romp_codex_backend_cuts", os.path.join(os.path.dirname(HERE), "kernel", "codex_backend.py"))
rm = load_source("romp_restart_metrics_cuts", os.path.join(BIN, "romp-restart-metrics"))
# The ledger as a reader sees it: since the exit path (2026-10-05) an exit writes its cut row at its start and a done row
# after the drain, and the product's reader folds the pair into one record (cli/restart_metrics.py fold_exit_done). The
# assertions below read the folded ledger, so each keeps its meaning; a reader without the fold reads the rows as written.
FOLD = getattr(rm, "fold_exit_done", None) or (lambda rows: rows)

SID = "11111111-2222-3333-4444-00000000c001"


def _raw_rows(f):
    """The ledger's rows exactly as written."""
    return [json.loads(l) for l in f.read_text().strip().splitlines()] if f.exists() and f.read_text().strip() else []


def _ledger(f):
    """The ledger as the reader folds it (FOLD above)."""
    return FOLD(_raw_rows(f))


def _fire(case, backend=None, quiesce=False, broadcast=None):
    """Run the SIGTERM handler in-process: the drain has no backend to stop unless one is given, the broadcast is a
    no-op unless one is given, and os._exit is caught so the runner survives the handler's unconditional exit (asserted
    reached once, with 0). The exit's quiesce (_exit_quiesce: the collector off and the loops stopped, interpreter-wide)
    is a no-op by default, a stand-in when `quiesce` is a callable, and the kernel's own when it is None (the one test
    that runs it puts everything back). create=True: a kernel without the quiesce runs its handler unchanged."""
    patches = [mock.patch.object(km, "_broadcast_restarting", broadcast or (lambda *a, **k: None)),
               mock.patch.object(km, "_sdk_backend", backend)]
    if quiesce is not None:
        patches.append(mock.patch.object(km, "_exit_quiesce", (lambda: None) if quiesce is False else quiesce, create=True))
    with contextlib.ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        ex = stack.enter_context(mock.patch.object(km.os, "_exit", side_effect=SystemExit))
        with case.assertRaises(SystemExit):
            km._graceful_term(signal.SIGTERM, None)
    ex.assert_called_once_with(0)


class _WriteClock:
    """A monotonic clock that stands still until the test moves it (advance), patched as the `time` global of the kernel
    and the event model for one exit, so the exit's caps and the checkpoint writer's budget count only what the test's
    stand-ins say they took. It answers only on the thread that made it, the test's, where the exit runs: a module's
    global is every thread's, so patched onto those two modules it was the clock of every thread running kernel or
    event-model code (another test's kernel thread, a backend's sweep taking its deadline), the weakness #152 closed in
    three other files. Every other thread reads the real clock, and assert_confined has one read it inside the window so
    the confinement cannot quietly regress. Everything else (time, sleep) is the real module's."""

    def __init__(self):
        self.now = time.monotonic()
        self.mine = threading.get_ident()

    def monotonic(self):
        return self.now if threading.get_ident() == self.mine else time.monotonic()

    def advance(self, seconds):
        self.now += seconds

    def assert_confined(self, case):
        """Inside the patch window: another thread reads both patched modules' clocks and gets the real one."""
        seen = []

        def read():
            a = time.monotonic()
            got = (km.time.monotonic(), km.em.time.monotonic())
            b = time.monotonic()
            seen.append(all(a <= v <= b for v in got))
        th = threading.Thread(target=read, name="another-thread-reads-the-clock")
        th.start()
        th.join(10)
        case.assertEqual(seen, [True], "another thread reads the real clock, never this test's")

    def __getattr__(self, name):
        return getattr(time, name)


# The kernel under test's own state root: bound when it loaded under this module's XDG_STATE_HOME, and
# where its import-time constants (RESTART_CUTS_FILE) live.
STATE_ROOT = km.RESTART_CUTS_FILE.parent

# bin/romp's `romp refresh` row: caller attribution with no action field, as the CLI writes it.
REFRESH_CLI_ROW = {"ppid": 4242, "parent": "bash", "sid": "", "name": "", "tty": "/dev/pts/0"}


class DeadTty:
    """A stderr whose every write raises: the kernel writes on the stdio the manager spawned it with, and
    a pty whose master closed answers EIO (a reset journal stream answers EPIPE the same way)."""

    def write(self, s):
        raise OSError(errno.EIO, "Input/output error")

    def flush(self):
        pass


class LiveDrain:
    """A backend with one session in flight: drain() records that it ran and names the turn it cut. It says
    so through the kernel's backend log wire before returning, as SdkBackend.drain does after its work: a
    wire that raised there carried the finished drain's result away (an empty cutTurns row, a drainError
    naming the stderr fault), so the dead-stderr cases below drive that line too."""
    called = False

    def drain(self, timeout):
        self.called = True
        km._backend_log("drain: stopped 1 session(s), 1 in-flight turn(s) interrupted")
        return {"cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1}


def _own_state_root(case):
    """Aim the shared judge module's STATE at this kernel's own root for one test, and put the previous
    binding back afterwards; returns the audit path both sides now use. The kernel writes the restart
    audit through jd.STATE at CALL time, and the judge module is shared by every test module in the
    worker, so the binding at run time is whatever the last test left there: a path captured at import
    points at a root a later module's kernel load rebound away from, and a path read at run time
    inherits a preceding class's leak (a test that rebinds STATE to a tempdir and removes it without
    restoring leaves the kernel's open() failing inside its never-raises guard, so every reader here
    sees no file, only when that module ran earlier in the same worker). Binding our own root removes
    the dependence on what ran before and on which module loaded the judge last. addCleanup runs after
    tearDown, so the unlinks there still see these paths."""
    saved = jd.STATE
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    jd._rebind_state(STATE_ROOT)
    case.addCleanup(jd._rebind_state, saved)
    return STATE_ROOT / "restart-audit.jsonl"


class OwnStateRoot(unittest.TestCase):
    """_own_state_root: the binding a preceding test left behind decides nothing about where this
    module's kernel writes, and comes back untouched afterwards."""

    def test_a_dangling_binding_left_by_an_earlier_test_is_not_inherited(self):
        saved = jd.STATE
        gone = Path(tempfile.mkdtemp())
        gone.rmdir()
        jd._rebind_state(gone)          # a tempdir root removed and never restored
        try:
            case = unittest.TestCase()
            audit = _own_state_root(case)
            self.assertEqual(jd.STATE, STATE_ROOT)
            self.assertEqual(audit.parent, STATE_ROOT)
            self.assertTrue(audit.parent.is_dir())
            km._audit_unrequested_signal(signal.SIGTERM, now=1)
            self.assertEqual(len(audit.read_text().strip().splitlines()), 1,
                             "the kernel wrote where this module reads")
            audit.unlink()
            case.doCleanups()
            self.assertEqual(jd.STATE, gone, "the previous binding comes back, whatever it was")
        finally:
            jd._rebind_state(saved)


class CutRow(unittest.TestCase):
    def setUp(self):
        self.AUDIT = _own_state_root(self)

    def test_row_shape_from_a_cutting_drain(self):
        row = km._restart_cut_row({"stopped": 3, "inflight": 2, "unjoined": 1, "reaped": 1,
                                   "cutTurns": [{"sid": SID, "name": "web"}]},
                                  watches_armed=4, audit_reason="kernel-asks-manager-restart-all: self-update",
                                  now=1_781_000_000)
        self.assertEqual(row["t"], 1_781_000_000)
        self.assertEqual(row["cutTurns"], [{"sid": SID, "name": "web"}])
        self.assertEqual((row["stopped"], row["unjoined"], row["reaped"]), (3, 1, 1))
        self.assertEqual(row["watchesArmed"], 4, "persisted watches SURVIVE — context, never cuts")
        self.assertIn("self-update", row["reason"])

    def test_a_clean_drain_still_writes_its_row(self):
        # the success metric: a restart that cut NOTHING is a row with an empty list
        row = km._restart_cut_row({"stopped": 5, "inflight": 0, "unjoined": 0, "reaped": 0,
                                   "cutTurns": []}, now=1)
        self.assertEqual(row["cutTurns"], [])
        row = km._restart_cut_row(None, now=1)   # no backend at all — still a row
        self.assertEqual(row["cutTurns"], [])

    def test_append_is_jsonl_and_never_raises(self):
        km._append_restart_cut(km._restart_cut_row({"cutTurns": []}, now=2))
        km._append_restart_cut(km._restart_cut_row({"cutTurns": [{"sid": SID, "name": "api"}]}, now=3))
        rows = [json.loads(l) for l in km.RESTART_CUTS_FILE.read_text().strip().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["cutTurns"][0]["sid"], SID)
        km.RESTART_CUTS_FILE.unlink()

    def test_reason_skips_rows_that_requested_no_restart(self):
        # T240 nit: an in-place converge (main-converge-skip) writes an audit row but restarts nothing;
        # reading only the LAST row labeled a real cut (the parked p2p deploy from 3 min earlier) as
        # the skip. Rows that request no restart are walked past.
        audit = self.AUDIT
        audit.write_text("".join(json.dumps(r) + "\n" for r in [
            {"t": 1000, "action": "p2p-update", "reason": "from X to abc1234", "when": "quiet"},
            {"t": 1150, "action": "main-converge-skip", "tag": "def5678"},
            {"t": 1160, "action": "bus-converge", "tag": "def5678"},
            {"t": 1170, "action": "end-on-idle", "tag": "11111111-2222-3333-4444-555555555555"},
        ]))
        try:
            self.assertIn("p2p-update", km._recent_restart_reason(window=90, now=1200))
            audit.write_text(json.dumps({"t": 1150, "action": "main-converge-skip"}) + "\n")
            self.assertEqual(km._recent_restart_reason(window=90, now=1200), "",
                             "a skip alone names nothing — the cut was anonymous")
        finally:
            audit.unlink()

    def test_a_consumed_audit_row_never_names_a_later_anonymous_cut(self):
        # a quiet p2p row stays inside its 20-minute window long after its restart landed; the cut
        # that consumed it records auditT, and an unaudited SIGTERM 15 min later reads anonymous
        audit = self.AUDIT
        audit.write_text(json.dumps({"t": 1000, "action": "p2p-update", "reason": "from X to abc1234",
                                     "when": "quiet"}) + "\n")
        try:
            self.assertIn("p2p-update", km._recent_restart_reason(now=1240), "the restart it asked for")
            km._append_restart_cut({"t": 1240, "reason": "p2p-update: from X to abc1234", "cutTurns": [],
                                    "auditT": 1000})
            self.assertEqual(km._recent_restart_reason(now=1500), "", "spent — the later cut is anonymous")
            # …and that anonymous cut's own row (no auditT) must not reset consumption: the NEXT
            # anonymous cut inside the window used to re-inherit the row (rows alternated
            # consumed / anonymous / consumed — review find)
            km._append_restart_cut({"t": 1500, "reason": "", "cutTurns": []})
            self.assertEqual(km._recent_restart_reason(now=1900), "", "still spent after an anonymous cut")
            km._append_restart_cut({"t": 1900, "reason": "", "cutTurns": []})
            self.assertEqual(km._recent_restart_reason(now=2100), "")
            # a torn line NEWER than the consuming cut never disables the guard
            with open(km.RESTART_CUTS_FILE, "a") as f:
                f.write('{"t": 2100, "reason": "", "cutTur\n')
            self.assertEqual(km._recent_restart_reason(now=2150), "", "a malformed line is skipped, not fatal")
        finally:
            audit.unlink()
            km.RESTART_CUTS_FILE.unlink()

    def test_reason_joins_the_recent_audit_tail_only(self):
        audit = self.AUDIT
        audit.write_text(json.dumps({"t": 1000, "action": "kernel-asks-manager-restart-all",
                                     "reason": "self-update"}) + "\n")
        # `started`: the rows are synthetic and predate this test process, whose start is the default bound
        self.assertIn("self-update", km._recent_restart_reason(window=90, now=1050, started=900))
        self.assertEqual(km._recent_restart_reason(window=90, now=5000, started=900), "",
                         "a stale audit row is not this restart's cause")
        audit.unlink()
        self.assertEqual(km._recent_restart_reason(now=1050, started=900), "", "no audit → anonymous, honestly")

    def test_a_cutting_drain_counts_mid_shutdown_and_threadless_sessions(self):
        # T143's two undercounts, executed: a session already flagged `ended` with a live in-flight
        # turn IS a cut (its CLI is reaped all the same — the old `not s.ended` clause filtered it
        # out of cutTurns: 10 transcript-verified cuts vs 7 rows), and a constructed-but-never-
        # started session (thread=None) must not crash the whole drain recordless.
        import types as _t
        sbmod = km.sb if hasattr(km, "sb") else None
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "sdk_backend.py")).read()
        # …and a session under a per-session host is detached, never cut (T315): the join keeps that filter
        self.assertIn("return [{\"sid\": s.sid, \"name\": s.name} for s in sessions\n                if s.inflight and getattr(s, \"_host\", None) is None and not getattr(s, \"_host_intent\", False)]", src,
                      "every in-flight session is a cut — ended included (the join, not a filter); the predicate is cut_list (T352)")
        self.assertIn("        cut = self.cut_list(sessions)\n        inflight = len(cut)", src, "the drain records cut_list's answer")
        self.assertIn("if s.thread is not None:", src,
                      "a threadless session can no longer crash the drain")

    def test_sigterm_handler_writes_the_ledger(self):
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        block = src[src.index("def _graceful_term"):src.index("def main():")]
        self.assertIn("row = _restart_cut_row(res, watches_armed=len(_pr_watches) + len(_watches),",
                      block, "the row counts BOTH persisted watch stores (T143) — and is built in the")
        self.assertIn("finally:", block)
        self.assertIn("_append_restart_cut(_exit_late_row(cut_row, done))", block,
                      "…FINALLY block, so a raising drain still writes what it knew (T143: 2 of 18 "
                      "restarts died recordless)")
        done_src = src[src.index("def _exit_done_row("):src.index("\ndef ", src.index("def _exit_done_row("))]
        self.assertIn('row["drainError"]', done_src, "an errored drain's row names the error (the done row, and the late "
                                                    "row it is joined into: _exit_late_row)")
        self.assertIn("err=err, reason_err=reason_err", block, "and the exit hands the drain's error to it")
        self.assertIn("rec = _recent_restart_audit()", block, "the cut row joins — and CONSUMES — the audit row (auditT)")
        self.assertIn("_drain_and_exit(_audit_reason_text(rec), signum=signum", block,
                      "the request on record is read AT SIGNAL TIME, before the drain: a row that lands "
                      "during the drain did not send this signal")
        self.assertIn("reason = _unrequested_signal_reason(signum, be)", block,
                      "a SIGTERM with no request on record leaves its own audit row (a direct signal to "
                      "the kernel pid used to have no row anywhere and an empty cut reason)")
        self.assertIn("audit_reason=reason", block)
        self.assertIn("if not _EXIT_ONCE.acquire(blocking=False):", block,
                      "a second SIGTERM mid-drain must not nest a second drain and a second cut row")
        # the exit path (2026-10-05): the cut row FIRST, the done row after the drain (behavior: ExitPath below); the
        # row as it was, after the drain, only when the early one could not be built
        drain = block[block.index("def _drain_and_exit("):]
        self.assertLess(drain.index("_append_restart_cut(cut_row)"), drain.index("be.drain("), "the cut row precedes the drain")
        self.assertLess(drain.index("be.drain("), drain.index("_append_restart_cut(done)"), "the done row follows it")
        self.assertLess(drain.index("if cut_row is not None and _cut_landed:"),
                        drain.index("_append_restart_cut(_exit_late_row(cut_row, done))"),
                        "the late row is the fallback for an early row that could not be built or never landed")

    def test_the_backends_log_wire_is_best_effort(self):
        # SdkBackend.drain logs its summary after every session is shut down, joined and reaped, right
        # before its return, through the kernel's `log=` callback. Wired as a bare sys.stderr.write, a dead
        # stderr raised there out of be.drain with the work done, and _drain_and_exit filed an empty
        # cutTurns row with a drainError naming the stderr fault: the misfiled row the exit path's own
        # _exit_log guards close, reopened by the one line they did not cover. The wire is _backend_log,
        # _exit_log with the backend's prefix; the LiveDrain fake logs through it, so the two
        # test_a_raising_stderr_does_not_skip_the_drain cases drive this line through the handlers
        src = open(os.path.join(os.path.dirname(HERE), "kernel", "kernel.py")).read()
        self.assertIn("log=_backend_log,", src, "the backend is wired to the best-effort line")
        self.assertNotIn('log=lambda m: sys.stderr.write("sdk-backend', src,
                         "never the bare write: it raised out of be.drain under a dead stderr")
        line = "drain: stopped 1 session(s), 0 in-flight turn(s) interrupted"
        with mock.patch.object(km.sys, "stderr", DeadTty()):
            km._backend_log(line)                    # no raise: the line is lost, the caller's result is not
        with mock.patch.object(km.sys, "stderr", new_callable=io.StringIO) as err:
            km._backend_log(line)
        self.assertEqual(err.getvalue(), "sdk-backend: %s\n" % line,
                         "with a healthy stderr the line is the one the kernel log always carried")


class UnrequestedSignal(unittest.TestCase):
    """A SIGTERM nobody asked the manager for (a stray kill, a test that fired a real restart, a
    supervisor stop) used to leave NOTHING: no restart-audit row, an empty cut reason. Such a signal
    once restarted the kernel onto a different python, and the outage that followed had no first
    cause on disk. The handler now records what it can know: the signal, its own and its parent's
    pid, the manager it was told about, whether a manager restart was parked, and that no request
    for it was on record. Sender attribution is out of reach here (a signal.signal handler gets no
    siginfo), so the row says so by what it omits."""

    def setUp(self):
        self.AUDIT = _own_state_root(self)   # this kernel's root, whatever an earlier test left bound
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        km._EXIT_ONCE = threading.Lock()     # the handler takes it and never gives it back (it exits)
        # no wait for the manager's note by default: the tests that want the wait pass their own.
        # create=True so that on a kernel without the constant the handler tests fail on the behaviour
        # they pin (no row, an empty reason), not on this patch
        self._wait = mock.patch.object(km, "SIGNAL_MANAGER_NOTE_WAIT_S", 0, create=True)
        self._wait.start()
        # the environment decides whether a manager is thought to exist: default to none (standalone),
        # so a pytest run inside a supervised session does not inherit the live manager's pid
        self._env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                 if k != "ROMP_MANAGER_PID"}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._wait.stop()
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _rows(self, f):
        return _ledger(f)                   # the cuts file folded as the reader folds it; the audit file passes unchanged

    def _dead_pid(self):
        """A pid nothing owns: a child spawned and reaped (never a guess at a number)."""
        p = subprocess.Popen(["true"])
        p.wait()
        return p.pid

    def test_the_row_carries_what_is_knowable(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": "4242"}):
            reason = km._audit_unrequested_signal(signal.SIGTERM, pending=True, now=1_781_000_000)
        self.assertEqual(reason, "signal, not requested through the manager")
        rows = self._rows(self.AUDIT)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["action"], "signal")
        self.assertEqual(row["signal"], "SIGTERM")
        self.assertEqual(row["t"], 1_781_000_000)
        self.assertEqual(row["pid"], os.getpid())
        self.assertEqual(row["ppid"], os.getppid())
        self.assertEqual(row["managerPid"], 4242)
        self.assertIs(row["managerRequested"], False)
        self.assertIs(row["managerStopped"], False)
        self.assertIs(row["managerRestartPending"], True)
        self.assertEqual(row["reason"], reason, "the cut row and the audit row say the same thing")

    def test_no_manager_in_the_environment_is_recorded_as_none(self):
        km._audit_unrequested_signal(signal.SIGTERM, pending=False, now=5)
        row = self._rows(self.AUDIT)[0]
        self.assertIsNone(row["managerPid"], "standalone kernel: no manager to have asked")
        self.assertIs(row["managerRestartPending"], False)

    def test_the_managers_own_kill_row_does_not_hide_who_asked(self):
        # the manager notes every SIGTERM it sends (bin/romp-manager auditSigterm); the requester's row
        # underneath still names WHO, so the cut reason keeps naming the cause, not the messenger
        self.AUDIT.write_text(
            json.dumps({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update"}) + "\n"
            + json.dumps({"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"}) + "\n")
        self.assertIn("self-update", km._recent_restart_reason(window=90, now=1050, started=900))

    def test_the_managers_kill_row_alone_is_still_a_request_on_record(self):
        # a `romp-manager restart` or a service stop: the manager asked, nobody audited before it; the
        # manager's own row is what keeps this from reading as an unrequested signal
        self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                          "reason": "stop"}) + "\n")
        self.assertEqual(km._recent_restart_reason(window=90, now=1050, started=900), "manager-sigterm: stop")
        self.assertEqual(km._recent_restart_reason(window=90, now=5000, started=900), "")

    def _fire(self, backend=None):
        """Run the SIGTERM handler in-process (the module's _fire: no broadcast, the quiesce a no-op, os._exit caught)."""
        _fire(self, backend)

    def test_the_handler_files_the_row_and_the_cut_reason_when_nothing_asked(self):
        self._fire()
        audit = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in audit], ["signal"])
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "signal, not requested through the manager",
                         "the cut row used to say nothing at all here")
        self.assertEqual(cuts[0]["cutTurns"], [])
        self.assertNotIn("drainError", cuts[0], "a clean exit's row carries neither fault field")
        self.assertNotIn("reasonError", cuts[0], "(each appears only when its fault did)")

    def test_a_raising_reason_helper_still_leaves_the_cut_row(self):
        # the helper reads ROMP_MANAGER_PID (a pid too large for os.kill raises OverflowError out of
        # _pid_alive) and writes to a stderr the dying supervisor may have closed; a raise there used
        # to skip the cut row this exit exists to leave. The row lands with the plain verdict instead,
        # and names the fault: the helper died before it filed the `signal` row, so the cut row is the
        # only record of this exit, and without the field it read as a healthy unrequested one
        with mock.patch.object(km, "_unrequested_signal_reason", side_effect=OverflowError("pid")), \
             mock.patch.object(km.sys, "stderr", new_callable=io.StringIO) as err:
            self._fire()
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1, "the cut row is written whether or not the reason helper survives")
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertEqual(cuts[0]["cutTurns"], [])
        self.assertEqual(cuts[0]["reasonError"], "OverflowError: pid",
                         "the row names the fault the helper died of, as drainError names a drain's")
        self.assertNotIn("drainError", cuts[0], "the drain itself ran clean; the fault is the helper's")
        self.assertEqual(self._rows(self.AUDIT), [], "no `signal` row on this path: the cut row is the only record")
        self.assertIn("reasonError", err.getvalue(), "and the fault is said on stderr")

    def test_a_raising_reason_helper_under_a_dead_stderr_still_leaves_the_cut_row(self):
        # the two faults arrive together in practice: a ROMP_MANAGER_PID the kernel cannot use, on a
        # stream that is gone. The line that names the helper's fault is best-effort like every other on
        # the exit path; were it not, its raise would leave the inner except for the outer one, which
        # writes no row, and the cut row this exit exists to leave would be lost. The stderr is a closed
        # file object (its write raises ValueError), so the exit path's guard is pinned for any exception,
        # not one errno, as the helper's own guard is (test_a_closed_stderr_... below)
        closed = io.StringIO()
        closed.close()
        with mock.patch.object(km, "_unrequested_signal_reason", side_effect=OverflowError("pid")), \
             mock.patch.object(km.sys, "stderr", closed):
            self._fire()
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1, "the cut row lands under a dead stderr and a dead helper")
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertEqual(cuts[0]["reasonError"], "OverflowError: pid")
        self.assertEqual(self._rows(self.AUDIT), [])

    def test_an_unlabeled_row_is_neither_the_request_nor_consumed(self):
        # the CLI's actionless refresh row alone, fresh, and the SIGTERM arrives (the manager's note never
        # came: a manager older than the note, or a stray kill first). The reader returns nothing, so the
        # handler files its own `signal` row, and the cut row records no auditT: a row that named nothing
        # was not the request this cut consumed. Taking the row as the answer would file the exit with an
        # empty reason and no row of its own, and spend the CLI row on it
        row = dict(REFRESH_CLI_ROW, t=int(time.time()))
        self.AUDIT.write_text(json.dumps(row) + "\n")
        self.assertIsNone(km._recent_restart_audit(window=90, now=row["t"], started=row["t"] - 10))
        self._fire()
        self.assertEqual([r.get("action") for r in self._rows(self.AUDIT)], [None, "signal"])
        cut = self._rows(km.RESTART_CUTS_FILE)[0]
        self.assertEqual(cut["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertNotIn("auditT", cut, "an unlabeled row is not consumed by a cut it did not explain")

    def test_a_parked_manager_restart_is_read_from_the_backends_drain_lease(self):
        # `managerRestartPending` comes from the backend's drain lease (held while a manager restart is
        # parked for the quiet window), not from a caller's flag; a backend without the method reads False
        class Holding:
            def drain_holding(inner):
                return True

        class NoLease:
            pass
        km._unrequested_signal_reason(signal.SIGTERM, be=Holding(), wait=0, now=1000)
        km._unrequested_signal_reason(signal.SIGTERM, be=NoLease(), wait=0, now=1001)
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["signal", "signal"])
        self.assertIs(rows[0]["managerRestartPending"], True)
        self.assertIs(rows[1]["managerRestartPending"], False)

    def test_a_manager_that_exits_during_the_wait_without_a_note_is_a_stop(self):
        # a manager older than the note (it writes none) going down with us: alive when the signal arrived,
        # gone when the wait for its note runs out. The pid is read again then, so the row says the manager
        # was stopped too instead of calling a service stop a stray signal. A stand-in pid and a stubbed
        # liveness read: nothing here signals or probes a real process
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": "4242"}), \
             mock.patch.object(km, "_pid_alive", side_effect=[True, False]) as alive:
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000, started=900)
        self.assertEqual(alive.call_args_list, [mock.call(4242), mock.call(4242)])
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertIs(self._rows(self.AUDIT)[0]["managerStopped"], True)

    def test_the_managers_stop_note_is_found_behind_a_busy_tail(self):
        # a service stop on a busy machine: the manager's stop note for us, then a dozen sessions' own
        # end-on-idle rows land before the handler reads. The note is still the manager going down with us;
        # a short tail would miss it and file the stop as a stray signal
        note = {"t": 1000, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "stop", "trigger": "stop"}
        self.AUDIT.write_text(json.dumps(note) + "\n" + "".join(
            json.dumps({"t": 1000, "action": "end-on-idle", "sid": "s%d" % i}) + "\n" for i in range(12)))
        self.assertTrue(km._manager_sigterm_row_for_us(now=1001, started=900))
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1001, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)

    def test_the_handler_leaves_a_requested_restart_alone(self):
        self.AUDIT.write_text(json.dumps({"t": int(time.time()),
                                          "action": "kernel-asks-manager-restart-all",
                                          "reason": "self-update"}) + "\n")
        self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["kernel-asks-manager-restart-all"],
                         "a request on record is the cause; no signal row is added on top of it")
        self.assertIn("self-update", self._rows(km.RESTART_CUTS_FILE)[0]["reason"])

    def test_the_managers_note_alone_is_consumed_like_a_request_row(self):
        # Design pick: with no request row on record the manager's own sigterm note answers, and the cut row
        # CONSUMES it as it would a request row (auditT = the note's t). A note is not a deploy request, so
        # _last_deploy_restart_t reads such a cut as attributed elsewhere with or without the stamp; this pins
        # the stamp so a change on either side is a visible decision.
        t = int(time.time())
        self.AUDIT.write_text(json.dumps({"t": t, "action": "manager-sigterm", "kernel": "main",
                                          "trigger": "restart", "reason": "restart"}) + "\n")
        self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["manager-sigterm"],
                         "the note is the request on record; no signal row is added on top of it")
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "manager-sigterm: restart", "the note's trigger is the label")
        self.assertEqual(cuts[0]["auditT"], t, "the cut row records the note's t as the audit row it consumed")
        self.assertEqual(km._consumed_audit_t(), t)

    def test_a_stderr_that_refuses_the_write_keeps_the_cut_row_agreeing_with_the_signal_row(self):
        # the kernel runs on the manager's stdio, and a stream that is gone (a reset journal stream, a
        # closed tty) makes every stderr write raise (EPIPE). _unrequested_signal_reason files the `signal`
        # row, logs a line, and returns the reason; the line is best-effort. Were it not, the raise would
        # leave _drain_and_exit its fallback, the plain unrequested verdict, on a cut row whose `signal`
        # row says the manager was stopped: two records of one exit disagreeing. So the manager pid here
        # is a reaped child's (stopped), and the cut row must carry the verdict the row does. The exit
        # path's own stderr lines are best-effort too, so the row carries no drainError (the cases below)
        class Gone:
            def write(self, s):
                raise OSError(errno.EPIPE, "Broken pipe")

            def flush(self):
                pass
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}), \
             mock.patch("sys.stderr", Gone()):
            self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["signal"])
        self.assertIs(rows[0]["managerStopped"], True)
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1, "the cut row is written whatever stderr does")
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED,
                         "the cut row carries the verdict the signal row does, not the fallback")
        self.AUDIT.unlink()
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}), \
             mock.patch("sys.stderr", Gone()):
            self.assertEqual(km._unrequested_signal_reason(signal.SIGTERM, wait=0), km.SIGNAL_REASON_MANAGER_STOPPED,
                             "the reason comes back with the log line lost, not the other way round")

    def test_a_raising_stderr_does_not_skip_the_drain(self):
        # the drain announcement was the first statement of the drain's try, so a stderr that raises (a
        # closed tty on a kernel that outlived the hangup, a reset journal stream, a full log disk) jumped
        # to the except before be.drain ran: no session was drained, their claude children orphaned with
        # in-flight turns uninterrupted, and the row read as a clean drain whose drainError named the
        # stderr fault. Every stderr line on the exit path is best-effort: the drain runs, and drainError
        # names a fault of the drain alone
        be = LiveDrain()
        with mock.patch.object(km.sys, "stderr", DeadTty()):
            self._fire(backend=be)
        self.assertTrue(be.called, "the drain runs whatever stderr does")
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["cutTurns"], [{"sid": SID, "name": "web"}])
        self.assertNotIn("drainError", cuts[0], "drainError is the drain's fault, never stderr's")

    def test_a_raising_stderr_when_the_cut_row_fails_still_exits(self):
        # a row builder that raises reaches the outer except, whose stderr line stood before os._exit(0)
        # unguarded: with stderr dead the line raised out of the handler and the exit never ran. From
        # _graceful_term that ended the interpreter with no row; from _parent_watch it ended that thread
        # alone, and the kernel lived on with the exit lock held, so every later SIGTERM returned at once
        with mock.patch.object(km.sys, "stderr", DeadTty()), \
             mock.patch.object(km, "_restart_cut_row", side_effect=RuntimeError("row")):
            self._fire()                             # asserts os._exit(0) was reached, once
        self.assertEqual(self._rows(km.RESTART_CUTS_FILE), [], "no row to write; the exit still ran")

    def test_a_stray_sigterm_with_a_quiet_park_on_record_does_not_spend_the_park(self):
        # a quiet converge parked with the manager (the row _run_main_update writes), then a SIGTERM the
        # manager did not send: no manager-sigterm note for this pid. The manager still holds the park and
        # delivers it to whatever kernel runs at the quiet window, so the park is not this signal's request.
        # Taking it stamped the park's t as auditT, and _consumed_audit_t then hid the park from every reader
        # under this root (a sibling kernel sharing it, the successor's drift stand-down); the cut row named
        # the deploy, no `signal` row was filed, and _last_deploy_restart_t counted a stray kill as a deploy
        # landing. The signal is filed as unrequested and the park stays on record
        t = int(time.time())
        park = {"t": t, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        self.AUDIT.write_text(json.dumps(park) + "\n")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):   # alive, and wrote no note
            self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["main-converge", "signal"])
        self.assertIs(rows[1]["managerStopped"], False)
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)
        self.assertNotIn("auditT", cuts[0], "the park is not consumed by a signal that did not deliver it")
        self.assertEqual(km._recent_restart_audit(window=90, now=t, started=t - 10)["t"], t,
                         "still the request on record for a sibling kernel or the successor")
        self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t, "the drift stand-down still holds")

    def test_the_managers_delivery_of_a_quiet_park_consumes_it(self):
        # the same park with the manager's restart-all note for this pid above it: that SIGTERM is the
        # delivery, so the cut row names the park and consumes it, as it did before the stray-kill rule
        t = int(time.time())
        park = {"t": t, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "restart", "trigger": "restart-all"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                         "delivered: no signal row")
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "main-converge")
        self.assertEqual(cuts[0]["auditT"], t, "the park is consumed by the kernel the manager noted before killing it")
        self.assertEqual(km._parked_quiet_deploy("1111111", now=t), 0, "and nothing is parked any more")

    def test_the_stale_manager_self_bounce_delivers_a_quiet_park_too(self):
        # a variant of the delivery test: the note is the stale-manager self-bounce's, reason `stop`
        # with trigger `refresh` (bin/romp-manager restartAllOrSelf: a supervised manager whose binary changed
        # since it started stops every kernel so the supervisor respawns it on the new code, and the quiet
        # apply goes through the same path). shutdownAll clears the manager's park before it notes and kills,
        # so this stop note is the park's delivery like a restart note is, and the cut row consumes the park.
        # Pinned at the handler because the `stop` member of the predicate's reasons had no case of its own:
        # dropping it turned nothing red, and the exit then took the unrequested path (a `signal` row with
        # managerStopped, the park left on record for a manager that no longer holds it). The trigger is what
        # keeps this note a delivery: a stop note with trigger `stop` is the one _graceful_term reads as none.
        # Distinct seconds, so the auditT stamp says which row was consumed (_consumed_audit_t keys on t
        # alone); the process start is pinned so the park sits inside this kernel's lifetime
        t = int(time.time())
        park = {"t": t - 5, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "stop", "trigger": "refresh"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
            self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                             "delivered: no signal row")
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 1)
            self.assertEqual(cuts[0]["reason"], "main-converge")
            self.assertEqual(cuts[0]["auditT"], t - 5, "the park is consumed, not the note")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), 0, "and nothing is parked any more")

    def test_the_managers_restart_of_this_kernel_alone_delivers_a_quiet_park_too(self):
        # the park filed by this kernel, then the manager's `restart` note for this pid with trigger `restart`
        # (POST /restart naming one kernel, bin/romp-manager restartKernel). Every restart the manager sends
        # clears its park before it notes and kills, so this note is the delivery like the restart-all note
        # is, and the cut row names the park and consumes it. Distinct seconds, so the auditT stamp says which
        # row was consumed; the process start is pinned so the park sits inside this kernel's lifetime
        t = int(time.time())
        park = {"t": t - 5, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "restart", "trigger": "restart"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
            self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                             "delivered: no signal row")
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 1)
            self.assertEqual(cuts[0]["reason"], "main-converge")
            self.assertEqual(cuts[0]["auditT"], t - 5, "the park is consumed, not the note")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), 0, "and nothing is parked any more")

    def test_a_stop_of_this_kernel_alone_does_not_spend_a_quiet_park_on_record(self):
        # the same park, then the manager's `stop` note for this pid with trigger `stop`. That note is POST
        # /stop naming one kernel (bin/romp-manager stopKernel), which leaves the manager's park armed for
        # whatever kernel runs at the quiet window, or a service stop, which writes the same note for every
        # kernel and drops the park with the manager; the note cannot tell them apart, and neither is the
        # park's delivery. Reading it as one stamped the park's t as auditT, which hid the park from every
        # reader under this root (_consumed_audit_t): the drift stand-down for the kernel the window will
        # restart read nothing parked, and _last_deploy_restart_t counted the stop as a deploy landing. The
        # cut row names the note instead, as the reader's walk already did for a sibling kernel born after
        # the park, and the park stays on record
        t = int(time.time())
        park = {"t": t - 5, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                "reason": "stop", "trigger": "stop"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):   # alive: one kernel's stop
            self._fire()
            self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                             "the note names the cut: no signal row")
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 1)
            self.assertEqual(cuts[0]["reason"], "manager-sigterm: stop", "the note, not the park")
            self.assertEqual(cuts[0]["auditT"], t, "the note is consumed; the park is not")
            self.assertNotEqual(km._consumed_audit_t(), t - 5, "every reader under this root still sees the park")
            self.assertEqual(km._recent_restart_audit(window=90, now=t, started=t - 10)["t"], t - 5,
                             "still the request on record for the kernel the window will restart")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t - 5, "the drift stand-down still holds")

    def test_a_stop_note_without_a_trigger_reads_as_the_stop_of_this_kernel_alone(self):
        # the same rows with no `trigger` key on the note. The reader takes such a note by its reason
        # (test_a_note_without_a_trigger_falls_back_to_its_reason), and the handler's default mirrors that
        # and the manager's own writer (trigger, else reason): a trigger-less `stop` note is the stop of this
        # kernel alone, so it names the cut and the park stays on record. Without the default the note would
        # pass for a delivery and consume the park
        t = int(time.time())
        park = {"t": t - 5, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "stop"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
            self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                             "the note names the cut: no signal row")
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 1)
            self.assertEqual(cuts[0]["reason"], "manager-sigterm: stop", "the note, read by its reason")
            self.assertEqual(cuts[0]["auditT"], t, "the note is consumed; the park is not")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t - 5, "the drift stand-down still holds")

    def test_a_sibling_born_after_the_park_files_the_same_row_for_the_stop_of_itself(self):
        # the same park and stop/stop note read by a kernel started AFTER the park (a dynamic kernel or a
        # stateDir-less aux under this root; its start sits between the two rows). To this kernel's walk the
        # park is a predecessor's and the note answers, so the cut row names the note, as it did before the
        # change; the kernel that filed the park files the same row now (the case above), so which kernel
        # the manager stopped no longer decides the record, and the park stays visible to the one that filed it
        t = int(time.time())
        park = {"t": t - 5, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": t, "action": "manager-sigterm", "kernel": "k29900", "pid": os.getpid(),
                "reason": "stop", "trigger": "stop"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(note) + "\n")
        with mock.patch.object(km, "_STARTED", t - 3), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["main-converge", "manager-sigterm"],
                         "the note names the cut: no signal row")
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "manager-sigterm: stop", "the same row the kernel that filed the park files")
        self.assertEqual(cuts[0]["auditT"], t)
        with mock.patch.object(km, "_STARTED", t - 10):      # the kernel that filed the park
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t - 5, "still parked for the kernel that filed it")

    def _service_stop_left_on_record(self, t):
        """What a service stop during a parked quiet converge now leaves under the root: the park, the
        stop/stop note for the kernel it stopped (a pid nothing owns any more), and that kernel's cut row
        naming the note (auditT = the note's t; the park unconsumed). The successor starts after all three."""
        gone = self._dead_pid()
        park = {"t": t - 20, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        stopped = {"t": t - 15, "action": "manager-sigterm", "kernel": "main", "pid": gone,
                   "reason": "stop", "trigger": "stop"}
        self.AUDIT.write_text(json.dumps(park) + "\n" + json.dumps(stopped) + "\n")
        cut = {"t": t - 15, "pid": gone, "cutTurns": [], "stopped": 0, "unjoined": 0, "reaped": 0,
               "watchesArmed": 0, "reason": "manager-sigterm: stop", "auditT": t - 15}
        km.RESTART_CUTS_FILE.write_text(json.dumps(cut) + "\n")

    def test_a_restart_of_the_successor_settles_the_park_a_service_stop_left_on_record(self):
        # the successor reads the park a service stop left as a live predecessor's: the note names another
        # pid, and a stop/stop note settles nothing in the walk (the manager may hold the park). That is inert
        # while the successor runs the checkout (_main_drift_check consults the park only when it does not)
        # and expires at RESTART_EXPECT_MAX_S; the manager's next restart note for the successor settles it
        # before then. The note names the successor's cut, and nothing reads as parked afterwards
        t = int(time.time())
        self._service_stop_left_on_record(t)
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t - 20,
                             "the park a service stop left reads as a live predecessor's")
            restart = {"t": t, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                       "reason": "restart", "trigger": "restart"}
            with self.AUDIT.open("a") as f:
                f.write(json.dumps(restart) + "\n")
            self._fire()
            self.assertEqual([r["action"] for r in self._rows(self.AUDIT)],
                             ["main-converge", "manager-sigterm", "manager-sigterm"], "no signal row")
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 2)
            self.assertEqual(cuts[1]["reason"], "manager-sigterm: restart")
            self.assertEqual(cuts[1]["auditT"], t, "the note names this cut; the stale park is settled, not consumed")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), 0, "and nothing reads as parked any more")

    def test_a_stray_kill_of_the_successor_leaves_the_park_a_service_stop_left_on_record(self):
        # the same record and a SIGTERM the manager did not send to the successor: no note for its pid, so
        # the park is not this signal's request. The exit is filed as unrequested with the manager alive, the
        # cut row consumes nothing, and the park stays where the service stop left it
        t = int(time.time())
        self._service_stop_left_on_record(t)
        with mock.patch.object(km, "_STARTED", t - 10), \
             mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire()
            rows = self._rows(self.AUDIT)
            self.assertEqual([r["action"] for r in rows], ["main-converge", "manager-sigterm", "signal"])
            self.assertIs(rows[2]["managerStopped"], False)
            cuts = self._rows(km.RESTART_CUTS_FILE)
            self.assertEqual(len(cuts), 2)
            self.assertEqual(cuts[1]["reason"], km.SIGNAL_REASON_UNREQUESTED)
            self.assertNotIn("auditT", cuts[1], "a signal that delivered nothing consumes nothing")
            self.assertEqual(km._parked_quiet_deploy("1111111", now=t), t - 20, "still parked, as the service stop left it")

    def test_a_second_sigterm_mid_drain_does_not_write_a_second_row(self):
        # a service stop signals the kernel, then the manager's shutdownAll signals it again while the
        # first handler drains; the second invocation returns and the first finishes: ONE cut row
        class Nested:
            def drain(inner, timeout):
                km._graceful_term(signal.SIGTERM, None)     # re-entry: must return, not exit
                return {"cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1}
        self._fire(backend=Nested())
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["cutTurns"], [{"sid": SID, "name": "web"}],
                         "the FIRST drain's count is the row; the nested call used to write a partial one")

    # ---- the wording: what the handler can honestly say about a signal with no request on record ----

    def test_manager_alive_and_silent_is_the_unrequested_wording(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):    # alive: us
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        row = self._rows(self.AUDIT)[0]
        self.assertIs(row["managerStopped"], False)
        self.assertEqual(row["reason"], reason)

    def test_manager_pid_gone_is_the_service_stop_wording(self):
        # SIGTERM to kernel and manager at once, and the manager went first: the kernel can see that
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000)
        self.assertEqual(reason, "signal; the manager was stopped too (a service stop or restart)")
        row = self._rows(self.AUDIT)[0]
        self.assertEqual(row["action"], "signal")
        self.assertIs(row["managerStopped"], True)
        self.assertIs(row["managerRequested"], False, "still not a request: nobody asked the manager")

    def test_a_closed_stderr_does_not_undo_the_service_stop_verdict(self):
        # the helper's own log line comes AFTER the `signal` row lands and BEFORE the return, so a stderr
        # that raises there must not carry the verdict away with it: _drain_and_exit would take its
        # fallback, the plain wording, and the cut row would contradict the row already on disk. The
        # case above drives a broken pipe (an OSError) through _graceful_term and then calls the helper
        # alone asserting the return only; this one's stderr is a closed file object, whose write raises
        # ValueError (the guard is for any exception, not one errno: a supervisor that reset the stream
        # leaves that kind behind), and it asserts on the direct call that the audit row's managerStopped
        # and reason agree with the verdict returned
        closed = io.StringIO()
        closed.close()
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}), \
             mock.patch.object(km.sys, "stderr", closed):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000)
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)
        rows = self._rows(self.AUDIT)
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["managerStopped"], True)
        self.assertEqual(rows[0]["reason"], reason, "the verdict returned is the one the row carries")

    def test_the_managers_stop_note_landing_after_our_signal_is_the_service_stop_wording(self):
        # the manager writes its note BEFORE it kills, so a note that lands AFTER our signal did not
        # send it: the manager is going down alongside us (node's handler ran after ours)
        audit = self.AUDIT

        def note_lands(_secs):
            with open(audit, "a") as f:
                f.write(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                    "pid": os.getpid(), "reason": "stop"}) + "\n")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=5, sleep=note_lands, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertEqual([r["action"] for r in self._rows(audit)], ["manager-sigterm", "signal"])

    def test_a_note_about_another_kernel_is_not_ours(self):
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "aux",
                                              "pid": os.getpid() + 100000, "reason": "stop"}) + "\n")
            self.assertEqual(km._recent_restart_reason(window=90, now=1000, started=900), "",
                             "the manager's note about an aux kernel is not a request on record here")
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)

    def test_the_handler_end_to_end_for_a_service_stop(self):
        # no sessions to drain, the manager already gone: the cut row says so, in one row each
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(self._dead_pid())}):
            self._fire()
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["signal"])
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(cuts[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED)

    def test_the_handler_reads_the_request_at_signal_time_not_after_the_drain(self):
        # the manager's note lands DURING the drain (sessions were live): it did not send this signal,
        # so the row says the manager was stopped too, not that the manager asked
        audit = self.AUDIT

        class LateNote:
            def drain(inner, timeout):
                with open(audit, "a") as f:
                    f.write(json.dumps({"t": int(time.time()), "action": "manager-sigterm",
                                        "kernel": "main", "pid": os.getpid(), "reason": "stop"}) + "\n")
                return {"cutTurns": []}
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            self._fire(backend=LateNote())
        self.assertEqual(self._rows(km.RESTART_CUTS_FILE)[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertEqual([r["action"] for r in self._rows(audit)], ["manager-sigterm", "signal"])

    def test_the_managers_restart_note_landing_after_our_signal_is_not_a_stop(self):
        # a stray kill starts the drain; a second later the rail's restart button has the manager note a
        # `restart` for this pid. The manager is alive and about to respawn us: that is not a service stop,
        # and the row must not say the manager was stopped too
        audit = self.AUDIT
        landed = []

        def note_lands(secs):        # once, then the wait runs its course: a restart note does not end it
            if not landed:
                landed.append(1)
                with open(audit, "a") as f:
                    f.write(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                        "pid": os.getpid(), "reason": "restart", "trigger": "restart"}) + "\n")
            time.sleep(secs)
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0.2, sleep=note_lands, now=1000, started=900)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        rows = self._rows(audit)
        self.assertEqual([r["action"] for r in rows], ["manager-sigterm", "signal"])
        self.assertIs(rows[1]["managerStopped"], False)

    def test_a_stop_note_for_our_pid_from_before_this_kernel_started_is_a_predecessors(self):
        # a reboot: the manager notes `stop` for kernel pid P; the machine is back inside the window and the
        # kernel comes up as pid P again; a stray kill then finds the pre-reboot note and must not file the
        # exit as a service stop while the manager is alive and about to respawn us
        self.AUDIT.write_text(json.dumps({"t": 1000, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertTrue(km._manager_sigterm_row_for_us(now=1041, started=900), "a kernel running at t=1000: its note")
        self.assertFalse(km._manager_sigterm_row_for_us(now=1041, started=1003), "a kernel born after it: a predecessor's")
        self.assertTrue(km._manager_sigterm_row_for_us(now=1005, started=1000.4), "whole seconds, as the rows are")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            reason = km._unrequested_signal_reason(signal.SIGTERM, wait=0, now=1041, started=1003)
        self.assertEqual(reason, km.SIGNAL_REASON_UNREQUESTED)
        self.assertIs(self._rows(self.AUDIT)[-1]["managerStopped"], False)

    def test_the_stop_note_bound_defaults_to_this_process_start(self):
        # as the handler calls it, with no `started`: a note from before _STARTED is not ours, one from its
        # own second is
        born = int(km._STARTED)
        self.AUDIT.write_text(json.dumps({"t": born - 5, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertFalse(km._manager_sigterm_row_for_us(now=born + 10))
        self.AUDIT.write_text(json.dumps({"t": born, "action": "manager-sigterm", "kernel": "main",
                                          "pid": os.getpid(), "reason": "stop", "trigger": "stop"}) + "\n")
        self.assertTrue(km._manager_sigterm_row_for_us(now=born + 10))

    # ---- a previous kernel's own verdict rows are not this kernel's request ----

    def _previous_kernels_verdict(self, action, reason):
        """A verdict row a predecessor filed on its own exit, seconds ago (inside the window, after this
        process started): the old pid, the action, the reason it concluded."""
        rec = {"t": int(time.time()), "action": action, "pid": self._dead_pid(),
               "ppid": 1, "managerPid": None, "reason": reason}
        if action == "signal":
            rec.update({"signal": "SIGTERM", "managerRequested": False, "managerStopped": False,
                        "managerRestartPending": False})
        self.AUDIT.write_text(json.dumps(rec) + "\n")

    def test_a_previous_kernels_signal_verdict_is_not_this_kernels_request(self):
        # two stray kills within 90 seconds: the second must not read the first's `signal` row as the
        # request on record, copy its reason onto the cut row and write no row of its own
        self._previous_kernels_verdict("signal", km.SIGNAL_REASON_MANAGER_STOPPED)
        self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["signal", "signal"], "a fresh row for a fresh signal")
        self.assertEqual(rows[1]["pid"], os.getpid())
        self.assertEqual(rows[1]["reason"], km.SIGNAL_REASON_UNREQUESTED, "this exit's own verdict")
        self.assertEqual(self._rows(km.RESTART_CUTS_FILE)[0]["reason"], km.SIGNAL_REASON_UNREQUESTED)

    def test_a_previous_kernels_parent_gone_verdict_is_not_this_kernels_request(self):
        # the manager was SIGKILLed, the kernel followed it (parent-gone), the supervisor brought both back;
        # a service restart 40 seconds later reaches the new kernel, whose cut row must not name a manager
        # exit that did not happen, and must get a `signal` row for the restart
        self._previous_kernels_verdict("parent-gone", km.PARENT_GONE_REASON)
        self._fire()
        rows = self._rows(self.AUDIT)
        self.assertEqual([r["action"] for r in rows], ["parent-gone", "signal"])
        cut = self._rows(km.RESTART_CUTS_FILE)[0]["reason"]
        self.assertEqual(cut, km.SIGNAL_REASON_UNREQUESTED)
        self.assertNotIn("the manager exited", cut)


class RequestOnRecord(unittest.TestCase):
    """_recent_restart_reason: which row of the audit tail explains a SIGTERM arriving now. A row with an
    action is a request; the manager's `manager-sigterm` note is a mechanism note that never outranks a
    request beneath it; a row with no action is skipped, never the answer (`romp refresh` writes an
    actionless caller-attribution row, and taking its empty label as the verdict would file every deploy
    as an unrequested signal), except an unlabeled quiet request, the park the drift stand-down reads.
    Exact row orders, as the writers produce them."""


    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def tearDown(self):
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _write(self, *rows):
        self.AUDIT.write_text("".join(json.dumps(r) + "\n" for r in rows))

    def _reason(self, now=1002, started=900, window=90):
        """The reader as the SIGTERM handler calls it, for a kernel that started at `started` (before
        every row below unless a test says otherwise; the default bound is this test process's start,
        which every synthetic row predates)."""
        return km._recent_restart_reason(window=window, now=now, started=started)

    def test_a_refresh_from_the_cli_is_a_request_on_record(self):
        # `romp refresh`: the CLI's actionless row, then the manager's restart note, then the SIGTERM.
        # The manager's note is what names it; "" here would file a deploy as a stray kill
        self._write(dict(REFRESH_CLI_ROW, t=1000),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "restart"})
        self.assertEqual(self._reason(), "manager-sigterm: restart")

    def test_a_refresh_that_labels_its_row_is_named_directly(self):
        # the shape a labeled writer produces (an `action: refresh` row): the request wins over the note
        self._write(dict(REFRESH_CLI_ROW, t=1000, action="refresh"),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(), "refresh")

    def test_a_restart_all_the_kernel_asked_for(self):
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(),
                         "kernel-asks-manager-restart-all: self-update")

    def test_a_request_row_outranks_the_managers_stop_note_above_it(self):
        # a request row followed by the manager's own stop note, in that order: the request is the
        # cause, and the note beneath it must not rewrite it as the manager's doing
        self._write({"t": 1000, "action": "rail-button", "reason": "stop"},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "stop"})
        self.assertEqual(self._reason(), "rail-button: stop")

    def test_the_managers_note_alone_answers_when_nothing_else_does(self):
        self._write({"t": 1000, "action": "manager-sigterm", "kernel": "main", "reason": "stop"})
        self.assertEqual(self._reason(), "manager-sigterm: stop")

    def test_a_romp_down_outranks_the_managers_cli_down_note_above_it(self):
        # `romp down`: the CLI's `down` row, then the service stop, then the manager's note with the
        # trigger it read off the marker. The request names the cut; the note beneath it does not
        # rewrite it as the manager's doing
        self._write({"t": 1000, "action": "down", "ppid": 7},
                    {"t": 1002, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "cli-down"})
        self.assertEqual(self._reason(now=1003), "down")

    def test_a_down_that_did_not_land_is_no_request_for_a_later_signal(self):
        # the stop failed and `romp down` filed a superseding `down-failed` row: neither row is the
        # request for a signal that arrives later, so the kernel files its own verdict instead
        self._write({"t": 1000, "action": "down", "ppid": 7},
                    {"t": 1001, "action": "down-failed", "reason": "the login service did not stop", "ppid": 7})
        self.assertEqual(self._reason(now=1003), "")
        self.assertIsNone(km._recent_restart_audit(window=90, now=1003, started=900))

    def test_a_down_failed_supersedes_only_the_down_beneath_it(self):
        # a failed `romp down`, then a `romp refresh` that did cut this kernel: the refresh is the request
        self._write({"t": 1000, "action": "down", "ppid": 7},
                    {"t": 1001, "action": "down-failed", "reason": "a manager still answers on :7432", "ppid": 7},
                    {"t": 1002, "action": "refresh", "ppid": 8})
        self.assertEqual(self._reason(now=1003), "refresh")

    def test_the_managers_cli_down_note_answers_when_the_down_row_is_out_of_reach(self):
        # a kernel that started after the `down` row was written (the row predates this process) still
        # reads the manager's note, and its trigger names the CLI as what set the stop off
        self._write({"t": 950, "action": "down", "ppid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "cli-down"})
        self.assertEqual(self._reason(now=1002, started=1000), "manager-sigterm: cli-down")

    def test_a_labeled_request_beneath_an_unlabeled_row_is_the_answer(self):
        # the skip, executed against the order it exists for: the CLI's actionless row lands ABOVE a labeled
        # request (a `romp refresh` typed while the dashboard's restart is on record) and is walked past;
        # returning it with its empty label would file the exit as unrequested with a request on record
        self._write({"t": 1000, "action": "http-restart", "reason": "rail"},
                    dict(REFRESH_CLI_ROW, t=1001))
        self.assertEqual(self._reason(), "http-restart: rail")
        self.assertEqual(km._recent_restart_audit(window=90, now=1002, started=900)["t"], 1000,
                         "the request row is what the cut consumes, not the unlabeled one above it")

    def test_a_request_consumed_inside_this_kernels_lifetime_is_not_named_again(self):
        # the request landed in the second this kernel started (a predecessor consumed it on its way out:
        # the cut row's auditT is its t) and the manager's stop note for us sits above it; the note answers,
        # and the spent request does not come back as the reason for a second exit
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update"},
                    {"t": 1040, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1000, "cutTurns": [], "auditT": 1000}) + "\n")
        self.assertEqual(km._consumed_audit_t(), 1000)
        self.assertEqual(self._reason(now=1041, started=1000), "manager-sigterm: stop")
        km.RESTART_CUTS_FILE.unlink()
        self.assertEqual(self._reason(now=1041, started=1000), "kernel-asks-manager-restart-all: self-update",
                         "unconsumed, the same request inside the lifetime is this exit's")

    def test_an_actionless_row_alone_is_not_an_answer(self):
        # a fresh actionless row with nothing else: no verdict in it, so nothing is on record
        self._write(dict(REFRESH_CLI_ROW, t=1000))
        self.assertEqual(self._reason(), "")

    def test_an_unlabeled_quiet_request_is_the_park_until_the_note_labels_it(self):
        # `romp refresh --quiet`: the CLI's actionless row with when=quiet is the parked deploy the drift
        # stand-down reads through the same walk, so the walk returns it while nothing else is on record;
        # at the quiet window the manager's restart-all note is what labels the exit
        park = dict(REFRESH_CLI_ROW, t=1000, when="quiet", sha="1111111")
        self._write(park)
        self.assertEqual(km._recent_restart_audit(window=90, now=1600, started=900), park)
        self.assertEqual(self._reason(now=1600), "", "the row itself has no label to answer with")
        self._write(park, {"t": 1500, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                           "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1501), "manager-sigterm: restart-all")
        self.assertIsNone(km._recent_restart_audit(window=90, now=1510, started=1502),
                          "delivered before this kernel started: neither the note nor the park is its own")

    def test_a_genuinely_unrequested_signal_has_nothing_on_record(self):
        self.assertEqual(self._reason(), "", "no file at all")
        self._write({"t": 100, "action": "kernel-asks-manager-restart-all", "reason": "self-update"},
                    {"t": 101, "action": "manager-sigterm", "kernel": "main", "reason": "restart"})
        self.assertEqual(self._reason(), "", "rows from an old restart")

    def test_a_note_aimed_at_another_kernel_pid_is_skipped(self):
        self._write({"t": 1000, "action": "manager-sigterm", "kernel": "aux", "pid": os.getpid() + 100000,
                     "reason": "restart"})
        self.assertEqual(self._reason(), "")

    def test_the_managers_note_answers_with_its_trigger(self):
        # the manager's `reason` is only restart|stop; `trigger` is what set the SIGTERM off, and it is
        # what tells a `romp refresh`, the stale-manager self-bounce and a hand stop apart
        for trigger, reason in (("restart-all", "restart"), ("refresh", "stop"), ("stop", "stop"),
                                ("restart", "restart")):
            with self.subTest(trigger=trigger):
                self._write(dict(REFRESH_CLI_ROW, t=1000),
                            {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                             "reason": reason, "trigger": trigger})
                self.assertEqual(self._reason(), "manager-sigterm: " + trigger)

    def test_a_note_without_a_trigger_falls_back_to_its_reason(self):
        self._write({"t": 1001, "action": "manager-sigterm", "kernel": "main", "reason": "stop"})
        self.assertEqual(self._reason(), "manager-sigterm: stop")

    def test_a_request_older_than_this_kernel_is_a_predecessors(self):
        # t=1000 the previous kernel's self-update; the manager notes `restart` for it and kills it; this
        # kernel starts at t=1003. At t=1040 a service stop: the manager notes `stop` for THIS pid. The
        # self-update is still inside the window and must not be returned as the reason the service stopped
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                     "reason": "restart", "trigger": "restart-all"},
                    {"t": 1040, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        self.assertEqual(self._reason(now=1041, started=1003), "manager-sigterm: stop")
        self.assertEqual(self._reason(now=1041, started=900), "kernel-asks-manager-restart-all: self-update",
                         "the same rows for a kernel that WAS running at t=1000: the request is its own")

    def test_a_request_older_than_this_kernel_with_no_note_is_nothing_on_record(self):
        # a stray kill of the kernel that a refresh just started: the refresh row is a predecessor's
        self._write(dict(REFRESH_CLI_ROW, t=1000, action="refresh"),
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                     "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1030, started=1003), "")

    def test_a_request_in_the_same_second_as_the_start_is_kept(self):
        # the bound is whole seconds, as the rows are: a request filed in the start's own second stands
        self._write(dict(REFRESH_CLI_ROW, t=1003, action="refresh"))
        self.assertEqual(self._reason(now=1005, started=1003.4), "refresh")

    def test_a_quiet_request_older_than_this_kernel_yields_to_the_note(self):
        # a `when: quiet` request parked while a predecessor ran, delivered to this kernel at the quiet
        # window: the manager's note for us names it (restart-all), the stale row does not
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                     "when": "quiet"},
                    {"t": 1500, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "restart", "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1501, started=1003), "manager-sigterm: restart-all")

    def test_a_quiet_request_older_than_this_kernel_names_the_cut_when_no_note_does(self):
        # the manager parks a quiet request and restarts whatever kernel runs at the quiet window, up to
        # RESTART_EXPECT_MAX_S, so the request outlives the kernel that filed it. A manager build that wrote
        # no note leaves the row as the only thing on record, and the start bound must not drop it
        self._write({"t": 1000, "action": "p2p-update", "reason": "from TESTHOST to 1111111", "when": "quiet"})
        self.assertEqual(self._reason(now=1600, started=1003), "p2p-update: from TESTHOST to 1111111")
        self.assertEqual(self._reason(now=1600, started=900), "p2p-update: from TESTHOST to 1111111",
                         "and the same for the kernel that filed it")
        self.assertEqual(self._reason(now=1000 + km.RESTART_EXPECT_MAX_S + 1, started=1003), "",
                         "the far manager's backstop bounds how long the park can be pending")

    def test_a_quiet_request_delivered_or_dropped_before_this_kernel_is_consumed(self):
        # delivered: any restart the manager sent clears the park in passing (an aux kernel's rail button
        # here); dropped: the manager's own shutdown (the stale-manager self-bounce noting `refresh`), or a
        # verdict that it was gone, park included. None leaves the quiet row as the request for a later
        # stray kill of this kernel
        for above in ({"t": 1010, "action": "manager-sigterm", "kernel": "aux", "pid": os.getpid() + 100000,
                       "reason": "restart", "trigger": "restart"},
                      {"t": 1010, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid() + 100000,
                       "reason": "stop", "trigger": "refresh"},
                      {"t": 1010, "action": "parent-gone", "pid": os.getpid() + 100000, "reason": km.PARENT_GONE_REASON},
                      {"t": 1010, "action": "signal", "pid": os.getpid() + 100000, "managerStopped": True,
                       "reason": km.SIGNAL_REASON_MANAGER_STOPPED}):
            for now, started in ((1080, 1003), (1101, 1003), (1080, 1011)):
                with self.subTest(above=above["action"] + ":" + str(above.get("trigger") or above.get("reason")),
                                  now=now, started=started):
                    self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                                 "when": "quiet"}, above)
                    self.assertEqual(self._reason(now=now, started=started), "",
                                     "settled wherever the row sits: inside the bounds, past the window, or before this kernel")
        # a stop note for THIS kernel above the park: the manager is stopping us, and that note is the answer
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "when": "quiet"},
                    {"t": 1010, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                     "reason": "stop", "trigger": "stop"})
        self.assertEqual(self._reason(now=1080, started=1003), "manager-sigterm: stop")
        self.assertEqual(self._reason(now=1101, started=1003), "", "a note older than the window is not this exit's")
        self.assertEqual(self._reason(now=1080, started=1011), "", "nor is one from before this kernel started")

    def test_a_quiet_request_survives_a_stop_of_one_other_kernel(self):
        # POST /stop naming one kernel (a dynamic kernel under this root has no root of its own) writes a
        # `stop` note with trigger `stop` for that pid and leaves the manager's park armed; the row cannot be
        # told from the stop that took every kernel down, so the park stays on record, and so does a stray
        # kill of the predecessor with the manager alive. The held converge self-heals at the backstop bound;
        # a released one would cut the turns the quiet window was to spare. The row above settles nothing
        # wherever it sits: a note or a verdict is classified before the 90 s window and the start bound end
        # the walk, so one older than the window (now=1101) or older than this kernel (started=1011) is
        # passed over and the park beneath it is still read
        for above in ({"t": 1010, "action": "manager-sigterm", "kernel": "k29900", "pid": os.getpid() + 100000,
                       "reason": "stop", "trigger": "stop"},
                      {"t": 1010, "action": "signal", "pid": os.getpid() + 100000, "managerStopped": False,
                       "reason": km.SIGNAL_REASON_UNREQUESTED}):
            for now, started in ((1080, 1003), (1101, 1003), (1080, 1011)):
                with self.subTest(above=above["action"], now=now, started=started):
                    self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update",
                                 "when": "quiet"}, above)
                    self.assertEqual(self._reason(now=now, started=started),
                                     "kernel-asks-manager-restart-all: self-update")
                    self.assertEqual((km._recent_restart_audit(window=90, now=now, started=started) or {}).get("t"),
                                     1000, "the park the drift stand-down reads")

    def test_a_park_this_kernel_filed_is_its_own_past_a_settling_row_at_any_age(self):
        # the kernel that filed a quiet converge (started 900, park at 1000) sees another kernel's restart
        # noted above it at 1010 (POST /restart of that kernel): the note is classified before the bounds and
        # settles a PREDECESSOR's park only, so the row stays this kernel's own request inside the window
        # (now=1080, as before) and past it (now=1101, where a note older than 90 s used to end the walk
        # and the same park read as nothing on record). The same rows for a kernel started after the park
        # are a predecessor's park settled by the restart above it, at either age
        park = {"t": 1000, "action": "main-converge", "tag": "restart", "when": "quiet", "sha": "1111111"}
        note = {"t": 1010, "action": "manager-sigterm", "kernel": "k29900", "pid": os.getpid() + 100000,
                "reason": "restart", "trigger": "restart"}
        self._write(park, note)
        for now in (1080, 1101):
            with self.subTest(now=now):
                self.assertEqual(self._reason(now=now, started=900), "main-converge")
                self.assertEqual(km._recent_restart_audit(window=90, now=now, started=900), park)
                self.assertEqual(self._reason(now=now, started=1003), "",
                                 "a predecessor's park, settled by the restart noted above it")

    def test_a_previous_kernels_verdict_rows_are_never_the_request(self):
        # the kernel's own `signal` and `parent-gone` rows have an action and the OLD pid; with the bound
        # they are also older than this kernel, but the rule holds on its own (a verdict is not a request)
        for action, reason in (("signal", km.SIGNAL_REASON_UNREQUESTED), ("parent-gone", km.PARENT_GONE_REASON)):
            with self.subTest(action=action):
                self._write({"t": 1000, "action": action, "pid": os.getpid() + 100000, "reason": reason})
                self.assertEqual(self._reason(), "", "a lone old verdict row is nothing on record")
                self._write({"t": 1000, "action": action, "pid": os.getpid() + 100000, "reason": reason},
                            {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                             "reason": "stop", "trigger": "stop"})
                self.assertEqual(self._reason(), "manager-sigterm: stop", "the note answers, not the verdict")

    def test_a_request_a_sibling_kernels_cut_spent_still_names_this_kernels_cut_under_its_own_note(self):
        # one restart-all signals two kernels under one root: the manager notes each pid before its SIGTERM, and the sibling's
        # cut row, written at its exit's START (2026-10-05), consumes the request milliseconds later, before this kernel reads
        # the tail. With the manager's note for THIS pid above the request, the request still names this cut (the base read it
        # before the sibling's row landed, after its drain); with no such note, or spent by this pid's own cut, it stays spent.
        # The sibling is a live stand-in process, as one still draining is; its cut row, written after this kernel started, is
        # what marks it a sibling (the review of 2026-10-06; the next two tests take a sibling whose exit has finished and one
        # whose cut row falls in this kernel's first second). And only a note naming THIS pid un-spends: a note naming no pid
        # counts as this kernel's for its label, so it answers with that label and the spent request stays spent
        stand_in = subprocess.Popen(["sleep", "60"])
        self.addCleanup(stand_in.wait)
        self.addCleanup(stand_in.kill)
        sib = stand_in.pid
        request = {"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7}
        sib_note = {"t": 1001, "action": "manager-sigterm", "kernel": "aux", "pid": sib, "reason": "restart", "trigger": "restart-all"}
        our_note = {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "restart",
                    "trigger": "restart-all"}

        def spent_by(pid):
            km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1001, "pid": pid, "cutTurns": [], "auditT": 1000,
                                                        "expectsDone": True}) + "\n")
        spent_by(sib)
        self._write(request, sib_note, our_note)
        self.assertEqual(self._reason(), "kernel-asks-manager-restart-all: self-update",
                         "the sibling spent it on the restart whose note for this pid lies above it")
        self.assertEqual((km._recent_restart_audit(window=90, now=1002, started=900) or {}).get("t"), 1000,
                         "and the row comes back, so this cut row consumes it too")
        self._write(request, sib_note)
        self.assertEqual(self._reason(), "", "no note for this pid above it: the spent request names nothing")
        self._write(request, sib_note, {k: v for k, v in our_note.items() if k != "pid"})
        self.assertEqual(self._reason(), "manager-sigterm: restart-all",
                         "a note naming no pid: its label answers, and the request the sibling spent stays spent")
        self.assertEqual((km._recent_restart_audit(window=90, now=1002, started=900) or {}).get("t"), 1001,
                         "the note is the row this cut consumes, not the request")
        spent_by(os.getpid())
        self._write(request, sib_note, our_note)
        self.assertEqual(self._reason(), "manager-sigterm: restart-all", "spent by this pid's own cut: the note answers")

    def test_a_request_a_sibling_spent_still_names_this_cut_when_the_siblings_exit_has_finished(self):
        # the review of 2026-10-06, its second round: the manager notes and signals every kernel under the root in one loop, and an idle
        # sibling's whole exit takes about a tenth of a second, so it can be gone before this kernel reads the tail. Its cut
        # row was written after this kernel started (t 1001, this kernel started at 900), which a predecessor's never is, so
        # the request it spent still names this cut. Liveness alone read the finished sibling as a predecessor
        gone = subprocess.Popen(["true"])
        gone.wait()                                  # the sibling's pid: its exit finished before this kernel read the tail
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1001, "pid": gone.pid, "cutTurns": [], "auditT": 1000,
                                                    "expectsDone": True}) + "\n")
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "aux", "pid": gone.pid, "reason": "restart",
                     "trigger": "restart-all"},
                    {"t": 1001, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "restart",
                     "trigger": "restart-all"})
        self.assertEqual(self._reason(), "kernel-asks-manager-restart-all: self-update",
                         "a cut row written after this kernel started is a sibling's, alive or not")
        with mock.patch.object(km, "_STARTED", 900.0):
            self.assertEqual(km._recent_restart_reason(window=90, now=1002), "kernel-asks-manager-restart-all: self-update",
                             "the handler passes no start: the bound is this process's own (_STARTED)")
        km.RESTART_CUTS_FILE.write_text(json.dumps({"pid": gone.pid, "cutTurns": [], "auditT": 1000,
                                                    "expectsDone": True}) + "\n")
        self.assertEqual(self._reason(), "manager-sigterm: restart-all",
                         "a cut row with no whole-second t says nothing about when it was written: liveness decides,"
                         " and an exited consumer's spend stands")

    def test_a_request_a_live_sibling_spent_in_the_second_this_kernel_started_still_names_this_cut(self):
        # the tie-break: the sibling's cut row falls in the wall-clock second this kernel started (t 1000, started 1000.5),
        # so its t cannot tell it from a predecessor's, and the sibling still running is what says it is one
        stand_in = subprocess.Popen(["sleep", "60"])
        self.addCleanup(stand_in.wait)
        self.addCleanup(stand_in.kill)
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1000, "pid": stand_in.pid, "cutTurns": [], "auditT": 1000,
                                                    "expectsDone": True}) + "\n")
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": 7},
                    {"t": 1000, "action": "manager-sigterm", "kernel": "aux", "pid": stand_in.pid, "reason": "restart",
                     "trigger": "restart-all"},
                    {"t": 1000, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "restart",
                     "trigger": "restart-all"})
        self.assertEqual(self._reason(now=1001, started=1000.5), "kernel-asks-manager-restart-all: self-update",
                         "a sibling still running spent it in this kernel's first second")

    def test_a_request_a_predecessor_spent_in_the_second_this_kernel_started_stays_spent(self):
        # the review of 2026-10-06: the start bound compares whole seconds, so a successor started 0.7 s after the self-update
        # that restarted its predecessor reads the request as filed inside its own lifetime. Stopped through the manager within
        # 90 s (the manager's stop note for this pid above the request) with no request row of its own, it filed its cut as that
        # self-update and consumed it a second time: the predecessor's spend read as a sibling's, since the sibling rule told
        # the two apart by the consumer's pid alone. A predecessor's cut row precedes this kernel's start and its kernel has
        # exited, so its spend stands
        gone = subprocess.Popen(["true"])
        gone.wait()                                  # the predecessor's pid: it exited before this kernel started
        km.RESTART_CUTS_FILE.write_text(json.dumps({"t": 1000, "pid": gone.pid, "cutTurns": [], "auditT": 1000,
                                                    "expectsDone": True}) + "\n")
        self._write({"t": 1000, "action": "kernel-asks-manager-restart-all", "reason": "self-update", "pid": gone.pid},
                    {"t": 1000, "action": "manager-sigterm", "kernel": "main", "pid": gone.pid, "reason": "restart",
                     "trigger": "restart-all"},
                    {"t": 1040, "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(), "reason": "stop",
                     "trigger": "stop"})
        self.assertEqual(self._reason(now=1041, started=1000.7), "manager-sigterm: stop",
                         "the stop note names the cut; the self-update the predecessor spent does not come back")
        self.assertEqual((km._recent_restart_audit(window=90, now=1041, started=1000.7) or {}).get("t"), 1040,
                         "and the note is what this cut consumes")


class ParentGone(unittest.TestCase):
    """_parent_watch: the manager that spawned the kernel is gone, and the kernel follows it. That exit
    used to be a bare os._exit with nothing in either file; now it leaves an audit row (action
    `parent-gone`) and a cut row with the drained turns, like every other exit."""

    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        # Both halves of the kernel's one-exit-path guard: the lock, and the flag whoever takes the lock
        # sets (_TERMINATING, the T240 stand-down). The handler tests above run _graceful_term in-process
        # and leave the flag set (in the kernel the process exits right after), so without this reset
        # _parent_watch stands down here and never reaches os._exit.
        km._EXIT_ONCE = threading.Lock()
        km._TERMINATING[0] = False

    def tearDown(self):
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _rows(self, f):
        return _ledger(f)

    def test_the_exit_leaves_both_rows(self):
        p = subprocess.Popen(["true"])
        p.wait()                                     # a pid nothing owns
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}), \
             mock.patch.object(km, "_sdk_backend", None), \
             mock.patch.object(km, "_exit_quiesce", lambda: None, create=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                km._parent_watch()
        ex.assert_called_once_with(0)
        audit = self._rows(self.AUDIT)
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]["action"], "parent-gone")
        self.assertEqual(audit[0]["reason"], "the manager exited; the kernel followed it")
        self.assertEqual(audit[0]["managerPid"], p.pid)
        self.assertEqual(audit[0]["pid"], os.getpid())
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["reason"], "parent-gone: the manager exited; the kernel followed it")

    def test_a_raising_stderr_does_not_skip_the_drain(self):
        # the exit a closed tty reaches: a manager running in its own session with the tty on its stderr
        # dies of its own write fault when the tty goes, and this watchdog notices with the kernel's stderr,
        # the same descriptor, dead too. The drain still runs and both rows land
        p = subprocess.Popen(["true"])
        p.wait()
        be = LiveDrain()
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}), \
             mock.patch.object(km, "_sdk_backend", be), \
             mock.patch.object(km.sys, "stderr", DeadTty()), \
             mock.patch.object(km, "_exit_quiesce", lambda: None, create=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                km._parent_watch()
        ex.assert_called_once_with(0)
        self.assertTrue(be.called, "the drain runs whatever stderr does")
        self.assertEqual([r["action"] for r in self._rows(self.AUDIT)], ["parent-gone"])
        cuts = self._rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(cuts), 1)
        self.assertEqual(cuts[0]["cutTurns"], [{"sid": SID, "name": "web"}])
        self.assertNotIn("drainError", cuts[0])

    def test_standalone_kernel_has_no_parent_to_watch(self):
        env = {k: v for k, v in os.environ.items() if k != "ROMP_MANAGER_PID"}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            km._parent_watch()
        ex.assert_not_called()
        self.assertFalse(self.AUDIT.exists())

    def test_stands_down_when_the_sigterm_handler_owns_the_exit(self):
        p = subprocess.Popen(["true"])
        p.wait()
        km._EXIT_ONCE.acquire()                      # the handler is mid-drain
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            km._parent_watch()
        ex.assert_not_called()
        self.assertFalse(self.AUDIT.exists(), "no second row: the handler writes the one that counts")


class CodexCutTurns(unittest.TestCase):
    """The exit drained the SDK backend alone, so a Codex turn open at a restart (its app-server is the kernel's
    child and ends with it) was cut under an empty cutTurns row: a clean restart to every reader of the ledger
    (2026-09-22). The row names it beside the SDK drain's cuts; the Codex backend is read, never built."""

    CX_SID = "11111111-2222-3333-4444-00000000c0de"

    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        km._EXIT_ONCE = threading.Lock()
        self._env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                                 if k != "ROMP_MANAGER_PID"}, clear=True)
        self._env.start()

    def tearDown(self):
        self._env.stop()
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _exit(self, sdk=None, codex=None):
        with mock.patch.object(km, "_broadcast_restarting", lambda *a, **k: None), \
             mock.patch.object(km, "_sdk_backend", sdk), \
             mock.patch.object(km, "_codex_backend", codex), \
             mock.patch.object(km, "_exit_quiesce", lambda: None, create=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                km._drain_and_exit("kernel-asks-manager-restart-all: refresh", what="SIGTERM")
        return _ledger(km.RESTART_CUTS_FILE)

    def test_an_open_codex_turn_is_named_in_the_cut_row(self):
        cx = mock.Mock(spec=["inflight_turns"])
        cx.inflight_turns.return_value = [{"sid": self.CX_SID, "name": "api", "backend": "codex"}]
        rows = self._exit(sdk=LiveDrain(), codex=cx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cutTurns"], [{"sid": SID, "name": "web"},
                                               {"sid": self.CX_SID, "name": "api", "backend": "codex"}])

    def test_a_running_compaction_is_named_in_the_cut_row(self):
        # 2026-09-23, the promise in #2055's body: the row and the restart gates read one predicate. The row read open
        # turns alone, so a restart over a compaction running as its own turn wrote an empty row, a clean restart to
        # every ledger reader, while /busy had held the quiet window for that compaction and the next load ended it
        # with its restart notice. A real CodexBackend (no app-server, no worker); synthetic sids
        cx = cxb.CodexBackend(tempfile.mkdtemp(), client_factory=lambda: None, log=lambda m: None)
        running = cxb._Session(self.CX_SID, "T-1", "api", "/TESTDIR")
        running.compacting, running.compact_active_seen, running.state = True, True, "compacting"   # seen active
        latched = cxb._Session("11111111-2222-3333-4444-00000000c0df", "T-2", "tests", "/TESTDIR")
        latched.compacting, latched.state = True, "compacting"   # latched at the ACK, never seen running: no cut
        for s in (running, latched):
            cx._put_session(s)
        rows = self._exit(sdk=LiveDrain(), codex=cx)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cutTurns"], [{"sid": SID, "name": "web"},
                                               {"sid": self.CX_SID, "name": "api", "backend": "codex"}],
                         "the running compaction is cut with the app-server, beside the SDK drain's cuts")
        self.assertEqual(cx.would_cut(), [{"sid": self.CX_SID, "name": "api"}], "the compaction the gates waited on")

    def test_no_codex_backend_built_is_no_codex_cut(self):
        for unbuilt in (None, False):          # never built / module unavailable
            with self.subTest(unbuilt=unbuilt):
                if km.RESTART_CUTS_FILE.exists():
                    km.RESTART_CUTS_FILE.unlink()
                km._EXIT_ONCE = threading.Lock()
                self.assertEqual(self._exit(codex=unbuilt)[0]["cutTurns"], [])

    def test_a_codex_read_that_raises_still_leaves_the_row(self):
        cx = mock.Mock(spec=["inflight_turns"])
        cx.inflight_turns.side_effect = RuntimeError("synthetic read fault")
        rows = self._exit(sdk=LiveDrain(), codex=cx)
        self.assertEqual(len(rows), 1, "the exit still writes its one row")
        self.assertEqual(rows[0]["cutTurns"], [{"sid": SID, "name": "web"}], "with the SDK drain's cuts in it")

    def test_the_codex_turns_are_in_the_cut_row_as_written(self):
        # the folded record takes the done row's list, which reads the Codex turns again after the drain, so the tests above
        # pass with the cut row's own Codex turns missing; an exit killed before its done row leaves the cut row alone, and
        # that row as it landed on disk must carry them (with a prediction from the SDK backend and without one)
        cx = mock.Mock(spec=["inflight_turns"])
        codex = {"sid": self.CX_SID, "name": "api", "backend": "codex"}
        cx.inflight_turns.return_value = [codex]
        web = {"sid": SID, "name": "web"}

        class Predicting:
            sessions = {SID: object()}

            def would_cut(inner):
                return [dict(web)]

            def drain(inner, timeout):
                return {"cutTurns": [dict(web)], "stopped": 1}
        for sdk, want in ((Predicting(), [web, codex]), (LiveDrain(), [codex])):
            with self.subTest(sdk=type(sdk).__name__):
                if km.RESTART_CUTS_FILE.exists():
                    km.RESTART_CUTS_FILE.unlink()
                km._EXIT_ONCE = threading.Lock()
                self._exit(sdk=sdk, codex=cx)
                first = _raw_rows(km.RESTART_CUTS_FILE)[0]
                self.assertEqual(first["cutTurns"], want, "the Codex turns in the cut row as written, before the drain")
                self.assertIs(first.get("expectsDone"), True, "the row an exit killed before its done row leaves")


class ExitPath(unittest.TestCase):
    """The exit path (2026-10-05). The manager allows a stopping kernel a grace (SIGTERM, then SIGKILL), and the exit's work
    had budgets that summed to nearly all of it, counted from a moment after the handler's own start, with the cut row (the
    one record of what the restart interrupted) written last: a busy exit met the SIGKILL and its row was lost. Now the
    exit runs against ONE deadline from the handler's entry, writes the cut row first and a done row after the drain,
    turns the collector off and stands the loops down at its start, writes the leaves it primed first, logs one line per
    phase, and reaches the process exit whatever raises. Synthetic: placeholder sids, /synthetic/ paths, names web/api/tests."""

    LEAVES = {"web": "/synthetic/web/leaf.jsonl", "api": "/synthetic/zz-api/leaf.jsonl", "tests": "/synthetic/zz-tests/leaf.jsonl"}
    SIDS = {"web": "11111111-2222-3333-4444-00000000c011", "api": "11111111-2222-3333-4444-00000000c012",
            "tests": "11111111-2222-3333-4444-00000000c013"}

    def setUp(self):
        self.AUDIT = _own_state_root(self)
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()
        km._EXIT_ONCE = threading.Lock()
        km._TERMINATING[0] = False
        self._wait = mock.patch.object(km, "SIGNAL_MANAGER_NOTE_WAIT_S", 0, create=True)
        self._wait.start()
        self._env = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items() if k != "ROMP_MANAGER_PID"}, clear=True)
        self._env.start()
        stand = getattr(km, "_EXIT_STAND_DOWN", None)
        if stand is not None:
            self.addCleanup(stand.clear)

    def tearDown(self):
        self._env.stop()
        self._wait.stop()
        for f in (self.AUDIT, km.RESTART_CUTS_FILE):
            if f.exists():
                f.unlink()

    def _sessions(self, *names):
        return [{"sid": self.SIDS[n], "path": self.LEAVES[n], "name": n} for n in names]

    def _request(self):
        t = int(time.time())
        self.AUDIT.write_text(json.dumps({"t": t, "action": "kernel-asks-manager-restart-all", "reason": "self-update"}) + "\n")
        return t

    def test_the_cut_row_is_on_disk_before_the_drain_runs(self):
        # the row a SIGKILL during the drain used to erase: written before the drain, with what the drain is about to cut
        seen = []

        class Peeking:
            sessions = {SID: object()}

            def would_cut(inner):
                return [{"sid": SID, "name": "web"}]

            def drain(inner, timeout):
                seen.extend(_raw_rows(km.RESTART_CUTS_FILE))
                return {"cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1}
        self._request()
        _fire(self, Peeking())
        self.assertEqual(len(seen), 1, "one row on disk when the drain began (the base wrote it after the drain)")
        self.assertEqual(seen[0]["cutTurns"], [{"sid": SID, "name": "web"}], "the turns the drain will cut, by its own predicate")
        self.assertEqual(seen[0]["reason"], "kernel-asks-manager-restart-all: self-update", "the request read at signal time")
        self.assertIs(seen[0].get("expectsDone"), True, "and a done row is promised")
        self.assertEqual(seen[0]["stopped"], 1, "the backend's sessions as the exit began")

    def test_one_cut_row_and_one_done_row_and_the_done_row_is_invisible_to_the_ledgers_readers(self):
        t = self._request()
        _fire(self, LiveDrain())
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(raw), 2, "the cut row and the done row: %r" % raw)
        cut, done = raw
        self.assertEqual([r for r in raw if "cutTurns" in r], [cut], "still exactly one row with cutTurns per exit")
        for k in ("cutTurns", "reason", "auditT", "bootSettled", "bootHealth"):
            self.assertNotIn(k, done, "the done row carries no %s: every reader keyed on it reads the cut row alone" % k)
        self.assertEqual((done["exitDone"], done["cutT"], done["pid"]), (True, cut["t"], cut["pid"]))
        self.assertEqual(done["drainCutTurns"], [{"sid": SID, "name": "web"}],
                         "a backend with no would_cut predicted nothing; the done row names what the drain cut")
        self.assertEqual(cut["auditT"], t)
        self.assertEqual(km._consumed_audit_t(), t, "the request is consumed by the cut row, once")
        with mock.patch.object(km, "_kernel_process_sample", lambda: {}), \
             mock.patch.dict(km._BOOT_MARKS, {"attachDone": 1.0}, clear=True):
            km._append_boot_settled(cut["t"] + 5.0, cut["t"] + 5.5)       # the next kernel's boot row, under the same root
        boot = _raw_rows(km.RESTART_CUTS_FILE)[-1]
        self.assertEqual((boot.get("prevCutT"), boot.get("outageS")), (cut["t"], 5.0), "the outage runs from the cut row")
        folded = [r for r in _ledger(km.RESTART_CUTS_FILE) if "cutTurns" in r]
        self.assertEqual(len(folded), 1)
        self.assertEqual((folded[0]["cutTurns"], folded[0]["stopped"], folded[0]["exitDone"]), ([{"sid": SID, "name": "web"}], 1, True))
        for k in ("rowS", "memosS", "listS", "primeS", "writeS", "ckptS", "drainS"):
            self.assertIn(k, done["phases"], "the done row times the %s phase" % k)
        self.assertNotIn("asmS", done["phases"], "no assembly phase (2026-10-08)")
        self.assertEqual(done["deadlineS"], round(float(km.EXIT_DEADLINE_S), 2))

    def test_one_deadline_from_the_handlers_entry_holds_the_drains_reserve(self):
        # deadline 1.2 s, reserve 0.4 s: everything before the drain ends by 0.8 s after the handler's ENTRY, even with the
        # broadcast taking 0.3 s of it and 200 dirty files at 20 ms each (the caps are set past the deadline, so the line
        # that stops the writes is the pre-drain line); the drain gets the rest of the deadline
        calls = []
        dirty = ["/synthetic/ckpt/%03d.jsonl" % i for i in range(200)]

        class Timed:
            def drain(inner, timeout):
                calls.append((time.monotonic(), timeout))
                return {"cutTurns": [], "stopped": 0}

        def slow_write(p, force=False):
            time.sleep(0.02)
            if p in dirty:
                dirty.remove(p)
            return True
        with mock.patch.object(km, "EXIT_DEADLINE_S", 1.2, create=True), \
             mock.patch.object(km, "EXIT_DRAIN_BUDGET_S", 0.4), \
             mock.patch.object(km, "EXIT_PRIME_BUDGET_S", 5.0), \
             mock.patch.object(km, "EXIT_CKPT_WRITE_BUDGET_S", 5.0), \
             mock.patch.object(km, "_sessions", lambda now, *a, **k: []), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: list(dirty)), \
             mock.patch.object(km.em, "checkpoint_write", slow_write):
            entry = time.monotonic()
            _fire(self, Timed(), broadcast=lambda *a, **k: time.sleep(0.3))
        self.assertEqual(len(calls), 1)
        at, timeout = calls[0]
        self.assertLessEqual(at - entry, 0.8 + 0.15, "the drain starts by the pre-drain line from the handler's entry: %.2f s" % (at - entry))
        self.assertLessEqual(at + timeout - entry, 1.2 + 0.15, "and is handed what the deadline has left: ends by %.2f s" % (at + timeout - entry))
        self.assertGreaterEqual(timeout, 0.4 - 0.15, "its reserve intact: %.2f s" % timeout)
        done = [r for r in _raw_rows(km.RESTART_CUTS_FILE) if r.get("exitDone")]
        self.assertEqual(len(done), 1)
        self.assertGreater(done[0]["ckptLeft"], 0, "the writes stopped at the line, leaving the rest dirty for the next writer")
        self.assertGreater(done[0]["ckptWritten"], 0)

    def test_no_collection_runs_once_the_exit_has_begun(self):
        # the kernel's own quiesce, for real (everything it touches is put back below): a drain allocating 5,000 two-object
        # cycles sees no collection start, the collector off, the freeze controller off and the loops stopped
        starts = []

        def hook(phase, info):
            if phase == "start":
                starts.append(info.get("generation"))
        was_enabled, frozen_before, ctl = gc.isenabled(), gc.get_freeze_count(), km._GC_FREEZE.enabled

        def restore():
            if frozen_before == 0:
                gc.unfreeze()
            if was_enabled:
                gc.enable()
            km._GC_FREEZE.enabled = ctl
            km._LOOPS_STOP.clear()
            stand = getattr(km, "_EXIT_STAND_DOWN", None)
            if stand is not None:
                stand.clear()
        self.addCleanup(restore)
        gc.enable()

        class Cycles:
            state = None

            def drain(inner, timeout):
                gc.callbacks.append(hook)
                try:
                    for _ in range(5000):
                        a, b = [], []
                        a.append(b)
                        b.append(a)
                finally:
                    gc.callbacks.remove(hook)
                stand = getattr(km, "_EXIT_STAND_DOWN", None)
                inner.state = (gc.isenabled(), km._GC_FREEZE.enabled, km._LOOPS_STOP.is_set(), stand is not None and stand.is_set())
                inner.frozen = gc.get_freeze_count()
                return {"cutTurns": []}
        be = Cycles()
        _fire(self, be, quiesce=None)
        self.assertEqual(starts, [], "no collection started during the drain (the base collected %d times)" % len(starts))
        self.assertEqual(be.state, (False, False, True, True), "collector off, controller off, loops stopped, the stand-down set")
        self.assertGreater(be.frozen, frozen_before, "and the heap frozen: every object tracked when the exit began moved to the "
                                                     "permanent generation, so a collection anyone still issues walks only what "
                                                     "the exit allocates")

    def test_the_quiesce_comes_before_the_broadcast_and_the_broadcast_before_the_drain(self):
        order = []

        class Recording:
            def drain(inner, timeout):
                order.append("drain")
                return {"cutTurns": []}
        _fire(self, Recording(), quiesce=lambda: order.append("quiesce"), broadcast=lambda *a, **k: order.append("broadcast"))
        self.assertEqual(order, ["quiesce", "broadcast", "quiesce", "drain"],
                         "the handler quiesces as soon as it owns the exit, then announces; the drain's tail quiesces again for "
                         "the parent watch's road")

    def test_one_log_line_per_phase_in_order_and_a_dead_stderr_still_exits(self):
        with mock.patch.object(km.sys, "stderr", new_callable=io.StringIO) as err:
            _fire(self, LiveDrain())
        lines = [l for l in err.getvalue().splitlines() if l.startswith("romp-kernel: exit ")]
        self.assertEqual([l.split()[2] for l in lines],
                         ["cutRow", "memos", "listing", "prime", "checkpoints", "drain", "doneRow", "done"], lines)
        shape = re.compile(r"romp-kernel: exit \w+ \d+\.\d\d s \(window \d+\.\d\d s; \d+\.\d\d s left of the \d+\.\d s deadline\)")
        for l in lines[:-1]:
            self.assertRegex(l, shape)
        self.assertIn("1 stopped, 0 unjoined, 0 reaped", lines[5], "the drain's line carries its counts")
        self.assertRegex(lines[-1], r"^romp-kernel: exit done in \d+\.\d\d s of the \d+\.\d s grace$")
        km.RESTART_CUTS_FILE.unlink()
        km._EXIT_ONCE = threading.Lock()
        with mock.patch.object(km.sys, "stderr", DeadTty()):
            _fire(self, LiveDrain())                 # every line raises; the exit is still reached, once
        self.assertEqual([bool(r.get("exitDone")) for r in _raw_rows(km.RESTART_CUTS_FILE)], [False, True], "both rows land")

    def test_the_unrequested_verdict_stays_late_and_the_done_row_carries_it(self):
        # the manager's stop note lands DURING the drain: the cut row, written before it, has the provisional verdict (no
        # wait, no `signal` row); the verdict after the drain is the one it always was, filed once, and the done row says so
        audit = self.AUDIT

        class LateNote:
            def drain(inner, timeout):
                with open(audit, "a") as f:
                    f.write(json.dumps({"t": int(time.time()), "action": "manager-sigterm", "kernel": "main",
                                        "pid": os.getpid(), "reason": "stop"}) + "\n")
                return {"cutTurns": []}
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}):
            _fire(self, LateNote())
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(raw), 2, raw)
        cut, done = raw
        self.assertEqual(cut["reason"], km.SIGNAL_REASON_UNREQUESTED, "nothing on record at signal time, and no wait before the row")
        self.assertEqual(done["finalReason"], km.SIGNAL_REASON_MANAGER_STOPPED)
        self.assertEqual(FOLD(raw)[0]["reason"], km.SIGNAL_REASON_MANAGER_STOPPED, "the reader's record carries the late verdict")
        self.assertEqual([r["action"] for r in _raw_rows(audit)], ["manager-sigterm", "signal"], "one `signal` row, after the drain")

    def test_a_signal_with_the_manager_already_gone_is_named_a_stop_in_the_cut_row_itself(self):
        p = subprocess.Popen(["true"])
        p.wait()                                     # a pid nothing owns: the manager went down first
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(p.pid)}):
            _fire(self, LiveDrain())
        cut, done = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(cut["reason"], km.SIGNAL_REASON_MANAGER_STOPPED, "visible at once, so the provisional verdict has it")
        self.assertNotIn("finalReason", done, "and the late verdict agrees")
        self.assertEqual([r["action"] for r in _raw_rows(self.AUDIT)], ["signal"], "filed once, by the late verdict")

    def test_a_cut_row_that_cannot_be_built_at_the_start_is_written_after_the_drain(self):
        # the early row's builder raises once: the row is written after the drain as before, from the drain's own result,
        # with no expectsDone and no done row (still one row with cutTurns)
        real, calls = km._restart_cut_row, []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("synthetic early-row fault")
            return real(*a, **k)
        t = self._request()
        before = time.time()
        with mock.patch.object(km, "_restart_cut_row", side_effect=flaky):
            _fire(self, LiveDrain(), broadcast=lambda *a, **k: time.sleep(1.1))   # the row is built after the clock has moved
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual([r.get("cutTurns") for r in raw], [[{"sid": SID, "name": "web"}]], raw)
        self.assertNotIn("expectsDone", raw[0])
        self.assertEqual(raw[0]["auditT"], t)
        self.assertEqual(len(calls), 2)
        self.assertIs(raw[0].get("exitDone"), True, "the exit's one row is complete: no done row follows it")
        self.assertIn("drainS", raw[0].get("phases") or {}, "and carries the phases a done row would have")
        self.assertIn("deadlineS", raw[0])
        self.assertEqual({k: type(raw[0].get(k)) for k in km.EXIT_DONE_COUNTS}, {k: int for k in km.EXIT_DONE_COUNTS},
                         "the checkpoint counts a done row would carry (the review of 2026-10-06: none was asserted)")
        took = time.time() - before
        self.assertGreaterEqual(raw[0].get("elapsedS", 0), 1.1, "the seconds the exit took from the handler's entry: the "
                                                                "broadcast alone took 1.1 s (%r)" % raw[0].get("elapsedS"))
        self.assertLessEqual(raw[0]["elapsedS"], took + 0.05, "and no more than the test saw (%.2f s)" % took)
        self.assertLessEqual(raw[0]["t"], int(before + 0.1), "its t is the handler's entry, as the early row's would be")
        self.assertIs(FOLD(raw)[0]["exitDone"], True, "the reader reads it as an exit that finished, not an older kernel's")

    def test_the_leaves_primed_at_exit_are_written_before_any_other_dirty_file(self):
        # the priming exists so a leaf's checkpoint carries a cursor for every fold; written in the store's sorted order, the
        # files the budget reached were the alphabetically first, not the leaves just primed. Two of three sessions prime,
        # and their leaves sort last among ten dirty files.
        # The cap is measured on a clock only the writes move (_WriteClock: each write takes one second of it), so how many
        # writes a cap allows is decided by the exit's code and never by the runner's scheduling. With real 0.2 s sleeps
        # against a 0.3 s cap, a macOS runner whose first sleep ran past 0.3 s wrote one leaf instead of two (CI, 2026-10-08)
        sess = self._sessions("web", "api", "tests")
        primed = [self.LEAVES["api"], self.LEAVES["tests"]]      # in the order the exit primes them: the listing's order
        dirty = sorted(["/synthetic/a%02d.jsonl" % i for i in range(8)] + primed)
        self.assertEqual(dirty[-2:], primed, "the primed leaves sort last")
        written = []
        clock = _WriteClock()

        def run(cap_s):
            del written[:]
            km.RESTART_CUTS_FILE.unlink() if km.RESTART_CUTS_FILE.exists() else None
            km._EXIT_ONCE = threading.Lock()

            def write(p, force=False):
                clock.advance(1.0)
                written.append(p)
                return True
            with mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sess)), \
                 mock.patch.object(km, "_prime_leaf_folds", lambda leaf: leaf in primed), \
                 mock.patch.object(km.em, "checkpoint_dirty", lambda: [p for p in dirty if p not in written]), \
                 mock.patch.object(km.em, "checkpoint_write", write), \
                 mock.patch.object(km, "_stored_tree", lambda leaf, sid: None), \
                 mock.patch.object(km, "_display_sdk_human", lambda sid: False), \
                 mock.patch.object(km, "EXIT_DEADLINE_S", 30.0, create=True), \
                 mock.patch.object(km, "EXIT_DRAIN_BUDGET_S", 1.0), \
                 mock.patch.object(km, "EXIT_PRIME_BUDGET_S", 10.0), \
                 mock.patch.object(km, "EXIT_CKPT_WRITE_BUDGET_S", cap_s), \
                 mock.patch.object(km, "time", clock), mock.patch.object(km.em, "time", clock):
                clock.assert_confined(self)
                _fire(self)
            return list(written)
        everything = run(20.0)
        self.assertEqual(everything[:2], primed, "the primed leaves first: %r" % everything)
        self.assertEqual(everything[2:], dirty[:8], "then every other dirty file, in the store's order: %r" % everything)
        self.assertEqual(run(0.5), primed[:1], "a cap shorter than one write: the one write the writer always makes goes to the "
                                               "first leaf primed")
        self.assertEqual(run(1.5), primed, "a cap that fits two writes: both went to the leaves just primed")
        self.assertEqual(run(2.5), primed + dirty[:1], "a third write: the store's first other dirty file")

    def test_the_stand_down_skips_the_tick_jobs_and_the_settle_writes_loop(self):
        stand = getattr(km, "_EXIT_STAND_DOWN", None)
        self.assertIsNotNone(stand, "the exit's stand-down event (the base has none)")
        ran, primed = [], []
        sess = self._sessions("web", "api")
        with mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sess)), \
             mock.patch.object(km, "_ckpt_settle_key", lambda sid: ("moved",)), \
             mock.patch.object(km, "_stat_key", lambda p: ("synthetic", 1)), \
             mock.patch.object(km, "_heal_cold_folds", lambda leaf, dropped_out=None: []), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: primed.append(leaf) or True), \
             mock.patch.object(km, "_session_fold_files", lambda sid, leaf: {leaf}), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
             mock.patch.object(km.em, "asm_checkpoint_write", lambda *a, **k: False), \
             mock.patch.object(km, "_asm_refresh_maybe", lambda leaf, sid: False), \
             mock.patch.object(km, "_display_sdk_human", lambda sid: False), \
             mock.patch.object(km, "_stored_tree", lambda leaf, sid: None):
            for set_ in (False, True):
                (stand.set if set_ else stand.clear)()
                with mock.patch.dict(km._CKPT_SETTLE_SEEN, {s["sid"]: ("before",) for s in sess}, clear=True), \
                     mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True):
                    km._job_stage("probe", lambda: ran.append(set_))
                    del primed[:]
                    km._persist_checkpoints(0)
                    if set_:
                        self.assertEqual(primed, [], "under the stand-down the settle write leaves before its first session")
                    else:
                        self.assertEqual(primed, [s["path"] for s in sess], "with it clear, both sessions' settles prime")
        stand.clear()
        self.assertEqual(ran, [False], "the tick job ran with the stand-down clear and not under it")

    def test_a_settle_write_running_when_the_signal_lands_starts_no_write_after_it(self):
        # the jobs thread is inside _persist_checkpoints (its first session's fold document write) when SIGTERM lands: the exit
        # does not wait for it, writes its own rows and its own checkpoint phase, and the settle write finishes the write in
        # flight and starts no other: not the session's assembly document (a write of seconds for a long leaf, which the base
        # began after the signal, competing with the exit for the interpreter), and no later session
        sess = self._sessions("web", "api", "tests")
        leaves = [s["path"] for s in sess]
        entered, release = threading.Event(), threading.Event()
        jobs_writes, exit_writes, jobs, docs = [], [], [], []

        def write_paths(paths=None, budget_s=None, stop=None):
            if jobs and threading.current_thread() is jobs[0]:
                jobs_writes.append(list(paths))
                if len(jobs_writes) == 1:
                    entered.set()
                    release.wait(10)
            else:
                exit_writes.extend(paths)
            return list(paths)
        stand = getattr(km, "_EXIT_STAND_DOWN", None)
        with mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sess)), \
             mock.patch.object(km, "_ckpt_settle_key", lambda sid: ("moved",)), \
             mock.patch.object(km, "_stat_key", lambda p: ("synthetic", 1)), \
             mock.patch.object(km, "_heal_cold_folds", lambda leaf, dropped_out=None: []), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
             mock.patch.object(km, "_session_fold_files", lambda sid, leaf: {leaf}), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: list(leaves)), \
             mock.patch.object(km.em, "drop_cold_cursors", lambda path: []), \
             mock.patch.object(km.em, "checkpoint_write_dirty_paths", write_paths), \
             mock.patch.object(km.em, "asm_checkpoint_write", lambda *a, **k: docs.append(a[0]) or False), \
             mock.patch.object(km, "_asm_refresh_maybe", lambda leaf, sid: False), \
             mock.patch.object(km, "_display_sdk_human", lambda sid: False), \
             mock.patch.object(km, "_stored_tree", lambda leaf, sid: None), \
             mock.patch.object(km, "_CKPT_JUST_WRITTEN", set()), \
             mock.patch.dict(km._CKPT_SETTLE_SEEN, {s["sid"]: ("before",) for s in sess}, clear=True), \
             mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True):
            th = threading.Thread(target=lambda: km._persist_checkpoints(0), name="jobs-probe", daemon=True)
            jobs.append(th)
            th.start()
            try:
                self.assertTrue(entered.wait(10), "the settle write is inside its first session's write")
                _fire(self, LiveDrain(), quiesce=(stand.set if stand is not None else (lambda: None)))
                self.assertTrue(th.is_alive(), "the exit finished without waiting for the settle write")
                self.assertEqual([bool(r.get("exitDone")) for r in _raw_rows(km.RESTART_CUTS_FILE)], [False, True],
                                 "and both of its rows are on disk")
                self.assertEqual(sorted(exit_writes), sorted(leaves), "the exit's own phase wrote the dirty set")
            finally:
                release.set()
                th.join(10)
        self.assertFalse(th.is_alive())
        self.assertEqual(jobs_writes, [[leaves[0]]], "the settle write finished the write in flight and began no other session's")
        self.assertEqual(docs, [], "nor the session's assembly document after the signal (the base began it)")

    def test_a_signal_landing_between_two_fold_documents_of_one_session_starts_neither_the_next_nor_the_assembly(self):
        # one session whose settle owes three fold documents (the leaf, its states log, the shared postal log): the stand-down is
        # set while the first is being written; the writer's own loop asks before every path, so the second and third are never
        # begun, and neither is the leaf's assembly document. The base handed the whole set to a writer that never asked
        sess = self._sessions("web")
        leaf = sess[0]["path"]
        files = sorted([leaf, "/synthetic/states/web.jsonl", "/synthetic/timeline/messages.jsonl"])
        stand = km._EXIT_STAND_DOWN
        written, docs = [], []

        def write(p, force=False):
            written.append(p)
            stand.set()                                       # SIGTERM lands while this document is written
            return True
        with mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sess)), \
             mock.patch.object(km, "_ckpt_settle_key", lambda sid: ("moved",)), \
             mock.patch.object(km, "_stat_key", lambda p: ("synthetic", 1)), \
             mock.patch.object(km, "_heal_cold_folds", lambda leaf, dropped_out=None: []), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
             mock.patch.object(km, "_session_fold_files", lambda sid, leaf: set(files)), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: list(files)), \
             mock.patch.object(km.em, "drop_cold_cursors", lambda path: []), \
             mock.patch.object(km.em, "checkpoint_write", write), \
             mock.patch.object(km.em, "asm_checkpoint_write", lambda *a, **k: docs.append(a[0]) or False), \
             mock.patch.object(km, "_asm_refresh_maybe", lambda leaf, sid: False), \
             mock.patch.object(km, "_display_sdk_human", lambda sid: False), \
             mock.patch.object(km, "_stored_tree", lambda leaf, sid: None), \
             mock.patch.object(km, "_CKPT_JUST_WRITTEN", set()), \
             mock.patch.dict(km._CKPT_SETTLE_SEEN, {sess[0]["sid"]: ("before",)}, clear=True), \
             mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True):
            try:
                km._persist_checkpoints(0)
            finally:
                stand.clear()
        self.assertEqual(written, files[:1], "the write in flight finished; the next two were never begun")
        self.assertEqual(docs, [], "and no assembly document was begun after the signal")

    def test_a_raise_past_every_phase_guard_still_reaches_the_exit(self):
        # the body runs inside one try whose finally reaches the process exit: a kernel with its collector off and its
        # loops stopped must never be left holding the port until the SIGKILL. The fault is the exit's own log line raising
        # (its guard is the thing patched away), the first statement of the drain's tail at the base, unguarded there
        quiesced = []
        with mock.patch.object(km, "_exit_log", side_effect=RuntimeError("synthetic log fault")):
            _fire(self, LiveDrain(), quiesce=lambda: quiesced.append(1))   # asserts os._exit(0) was reached, once
        self.assertTrue(quiesced, "the exit had quiesced before the fault")

    # ── the review of 2026-10-05, round two ────────────────────────────────────────────────────────────────────────────
    def _reset(self):
        if km.RESTART_CUTS_FILE.exists():
            km.RESTART_CUTS_FILE.unlink()
        km._EXIT_ONCE = threading.Lock()
        km._TERMINATING[0] = False

    def _work(self, sessions=(), prime=None, dirty=None, write=None, asm=None, deadline=None, reserve=None, caps=None):
        """The exit's work patched, as one context: the session listing, the priming, the dirty checkpoints and their writer,
        the assembly writer, and the exit's deadline, reserve and caps (by module name). Unnamed parts do nothing."""
        ps = [mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sessions)),
              mock.patch.object(km, "_prime_leaf_folds", prime or (lambda leaf: False)),
              mock.patch.object(km.em, "checkpoint_dirty", lambda: list(dirty or [])),
              mock.patch.object(km, "_stored_tree", lambda leaf, sid: None),
              mock.patch.object(km, "_display_sdk_human", lambda sid: False)]
        if write is not None:
            ps.append(mock.patch.object(km.em, "checkpoint_write", write))
        if asm is not None:
            ps.append(mock.patch.object(km.em, "asm_checkpoint_write", asm))
        if deadline is not None:
            ps.append(mock.patch.object(km, "EXIT_DEADLINE_S", deadline, create=True))
        if reserve is not None:
            ps.append(mock.patch.object(km, "EXIT_DRAIN_BUDGET_S", reserve))
        for name, value in (caps or {}).items():
            ps.append(mock.patch.object(km, name, value))
        stack = contextlib.ExitStack()
        for p in ps:
            stack.enter_context(p)
        return stack

    def _done(self):
        done = [r for r in _raw_rows(km.RESTART_CUTS_FILE) if r.get("exitDone") and "cutTurns" not in r]
        self.assertEqual(len(done), 1, _raw_rows(km.RESTART_CUTS_FILE))
        return done[0]

    def test_no_checkpoint_write_starts_past_the_pre_drain_line(self):
        # the writer always attempts its first path (checkpoint_write_dirty_paths), so the exit calls it only while its window
        # is open: a priming item that ran past the line, a reserve as long as the deadline, or a zero checkpoint cap leaves
        # every dirty file to the next writer, where the head started one more whole write past the line in each case. The
        # priming item takes its seconds on a clock only the test moves (_WriteClock), so where it ends against the line is
        # the code's arithmetic and never the runner's scheduling (a real sleep against a 0.2 s margin before); a control
        # whose item ends inside the line has every dirty file written, so the three refusals are the line's and not a
        # writer that never runs
        # The reserve-at-the-deadline case runs at a clock reading where (t + 1.0) - 1.0 lands one float step past t
        # (15.219903 does): the line taken as the deadline less the reserve then stood one step past the exit's entry, and every
        # dirty file was written at the entry (CI, 2026-10-08, at whatever reading the runner's clock had); it is the window's
        # length, the deadline less the reserve, from the entry, so a zero window is the entry exactly
        sess = self._sessions("web")
        dirty = ["/synthetic/ckpt/%02d.jsonl" % i for i in range(5)]
        clock = _WriteClock()
        for label, prime_s, deadline, reserve, cap, want, at in (("a priming item past the line", 0.6, 1.0, 0.6, 5.0, (0, 5), None),
                                                                   ("the reserve at the deadline", 0.0, 1.0, 1.0, 5.0, (0, 5), 15.219903),
                                                                   ("a zero checkpoint cap", 0.0, 2.0, 0.5, 0.0, (0, 5), None),
                                                                   ("control: a priming item inside the line", 0.3, 1.0, 0.6, 5.0, (5, 0), None)):
            with self.subTest(label):
                self._reset()
                starts = []
                if at is not None:
                    clock.now = at
                    self.assertGreater((at + deadline) - reserve, at, "the premise: this reading rounds the line past the entry")

                def write(p, force=False):
                    starts.append(clock.monotonic())
                    return True

                def prime(leaf):
                    clock.advance(prime_s)                    # the item's seconds, on the exit's clock
                    return True
                with self._work(sessions=sess, prime=prime, dirty=dirty, write=write, deadline=deadline, reserve=reserve,
                                caps={"EXIT_PRIME_BUDGET_S": 5.0, "EXIT_CKPT_WRITE_BUDGET_S": cap}), \
                     mock.patch.object(km, "time", clock), mock.patch.object(km.em, "time", clock):
                    clock.assert_confined(self)
                    entry = clock.monotonic()
                    _fire(self, LiveDrain())
                line = deadline - reserve
                self.assertEqual([round(t - entry, 2) for t in starts if t - entry >= line], [], "no write started past the line")
                done = self._done()
                if want[0] == 0:
                    self.assertEqual((done["ckptWritten"], done["ckptLeft"]), want, "every dirty file left for the next writer")
                else:                                         # (the stand-in dirty set does not shrink as it is written)
                    self.assertEqual((done["ckptWritten"], len(starts)), (want[0], want[0]), "the control writes them all")

    def test_the_exit_writes_no_assembly_document_and_the_drain_follows_the_checkpoint_writes(self):
        # the exit's assembly phase is gone (2026-10-08): the documents' writers run while the kernel lives (the settle, the
        # periodic write of a long turn, the converge pass), a stale one costs the next boot a longer tail parse and is never
        # wrong, and an exit-time write could not be stopped once begun (4.86 s against its 0.28 s cap in a lab, the drain
        # left its floor). Three listed sessions, their leaves dirty and primed: the exit writes their checkpoints, never
        # calls the document writer (so it never waits on a leaf's key lock), and the drain is the next thing it does. The
        # done row names no assembly phase or count
        sess = self._sessions("web", "api", "tests")
        order, docs = [], []

        def write(p, force=False):
            order.append(("checkpoint", p))
            return True

        class Drain:
            def drain(inner, timeout):
                order.append(("drain",))
                return {"cutTurns": [], "stopped": 0}
        with self._work(sessions=sess, prime=lambda leaf: True, dirty=[x["path"] for x in sess], write=write,
                        asm=lambda *a, **k: docs.append(a[:2]) or True):
            _fire(self, Drain())
        self.assertEqual(docs, [], "the exit wrote assembly documents: %r" % docs)
        self.assertEqual(order, [("checkpoint", x["path"]) for x in sess] + [("drain",)],
                         "the checkpoint writes, then the drain, nothing between")
        done = self._done()
        self.assertNotIn("asmS", done["phases"])
        for k in ("asmSkipped", "asmBusy"):
            self.assertNotIn(k, done, "the done row counts no assembly documents")

    def test_the_assembly_share_went_to_the_checkpoint_writes_and_the_drain_gets_the_rest(self):
        # the budgets at the manager's 8 s grace: deadline 3.2 s, the drain's reserve 1.12 s, the window before it 2.08 s, of
        # which the checkpoint writes now take 70 percent (1.456 s; 45 percent, 0.936 s, while the assembly documents held
        # 25). Each stand-in write takes 0.1 s of a clock only the writes move (_WriteClock), so the count is the code's:
        # fifteen writes (the sixteenth would start 1.5 s in, past the cap), where the old share allowed ten, and the drain is
        # handed the rest of the deadline, 3.2 - 1.5 = 1.7 s
        b = km._exit_budgets(8.0, env={})
        self.assertEqual(sorted(b), ["ckpt", "deadline", "drain", "prime", "work"], "no assembly-document cap: %r" % b)
        self.assertAlmostEqual(b["ckpt"], 0.70 * b["work"], places=6)
        dirty = ["/synthetic/ckpt/%02d.jsonl" % i for i in range(40)]
        clock, written, handed = _WriteClock(), [], []

        def write(p, force=False):
            clock.advance(0.1)
            written.append(p)
            return True

        class Drain:
            def drain(inner, timeout):
                handed.append(timeout)
                return {"cutTurns": [], "stopped": 0}
        with self._work(sessions=self._sessions("web"), dirty=dirty, write=write, deadline=b["deadline"], reserve=b["drain"],
                        caps={"EXIT_PRIME_BUDGET_S": b["prime"], "EXIT_CKPT_WRITE_BUDGET_S": b["ckpt"]}), \
             mock.patch.object(km, "time", clock), mock.patch.object(km.em, "time", clock):
            clock.assert_confined(self)
            _fire(self, Drain())
        self.assertEqual(written, dirty[:15], "the checkpoint writes the cap allowed: %d" % len(written))
        self.assertEqual(len(handed), 1)
        self.assertAlmostEqual(handed[0], b["deadline"] - 1.5, places=6, msg="the drain is handed the rest of the deadline")
        done = self._done()
        self.assertEqual((done["ckptWritten"], done["ckptLeft"]), (15, 40))

    def test_the_priming_phase_skips_at_its_cap_and_at_the_line(self):
        # three sessions whose priming takes 0.25 s each, on a clock only the items move (_WriteClock), so the count is the
        # code's: a real sleep per item with 0.15 s of slack on the drain's start read a loaded runner's scheduling. Cap past
        # the deadline: the pre-drain line (0.35 s after the entry) is what stops the loop. Cap below the line: the loop stops
        # at its own cap (0.3 s). Either way the first two items start inside the bound (at 0 and 0.25 s), the third, due at
        # 0.5 s, is skipped and counted, and the drain starts when the item in flight at the bound ends: 0.5 s, the bound plus
        # at most one item
        sess = self._sessions("web", "api", "tests")
        item = 0.25
        clock = _WriteClock()
        for label, deadline, reserve, cap in (("caps past the deadline", 0.7, 0.35, 5.0), ("caps below the line", 3.0, 1.0, 0.3)):
            with self.subTest(label):
                self._reset()
                calls = []

                class Timed:
                    def drain(inner, timeout):
                        calls.append(clock.monotonic())
                        return {"cutTurns": [], "stopped": 0}

                def prime(leaf):
                    clock.advance(item)                       # the item's seconds, on the exit's clock
                    return True
                with self._work(sessions=sess, prime=prime, deadline=deadline, reserve=reserve,
                                caps={"EXIT_PRIME_BUDGET_S": cap, "EXIT_CKPT_WRITE_BUDGET_S": cap}), \
                     mock.patch.object(km, "time", clock), mock.patch.object(km.em, "time", clock):
                    clock.assert_confined(self)
                    entry = clock.monotonic()
                    _fire(self, Timed())
                done = self._done()
                self.assertEqual((done["primed"], done["primeSkipped"]), (2, 1),
                                 "two items started inside the bound, the third skipped and counted: %r" % done)
                bound = min(cap, deadline - reserve)
                self.assertAlmostEqual(calls[0] - entry, 2 * item, places=6,
                                       msg="the drain starts as the item in flight at the bound ends: %.3f s" % (calls[0] - entry))
                self.assertLessEqual(calls[0] - entry, bound + item, "by the bound plus one item")

    def test_a_drain_that_raises_leaves_the_prediction_and_the_session_count_standing(self):
        web = {"sid": SID, "name": "web"}

        class Raising:
            sessions = {SID: object()}

            def would_cut(inner):
                return [dict(web)]

            def drain(inner, timeout):
                raise RuntimeError("synthetic drain fault")
        self._request()
        _fire(self, Raising())
        cut, done = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertIn("RuntimeError: synthetic drain fault", done["drainError"])
        for k in ("stopped", "unjoined", "reaped", "drainCutTurns"):
            self.assertNotIn(k, done, "the drain returned nothing: no %s on its done row" % k)
        rec = FOLD([cut, done])[0]
        self.assertEqual((rec["cutTurns"], rec["stopped"], rec["drainError"], rec["exitDone"]), ([web], 1, done["drainError"], True),
                         "the prediction and the session count stand (the head wrote an empty list and 0 stopped over them)")

    def test_a_cut_row_whose_append_fails_is_written_after_the_drain_as_the_exits_one_row(self):
        real, calls = km._append_restart_cut, []

        def first_fails(row):
            calls.append(dict(row))
            return False if len(calls) == 1 else real(row)   # the cut row's line never lands (a full disk, an unwritable ledger)
        t = self._request()
        with mock.patch.object(km, "_append_restart_cut", side_effect=first_fails):
            _fire(self, LiveDrain())
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(raw), 1, raw)
        self.assertEqual((raw[0].get("cutTurns"), raw[0].get("auditT"), raw[0].get("exitDone")), ([{"sid": SID, "name": "web"}], t, True),
                         "the late full row, from the drain's own result, where the head wrote a done row no reader joins")
        self.assertNotIn("expectsDone", raw[0])
        self.assertEqual([r.get("expectsDone") for r in calls[:1]], [True], "the early row was built and its append failed")

    # ── the review of 2026-10-06, posted after the merge ───────────────────────────────────────────────────────────────
    def _staged_sample(self, stages):
        """A stand-in for the kernel's process sample that says WHEN it was taken: rssKb 1 before the broadcast, 2 after
        it, 3 after the drain (each stage appends to `stages`), so a row's sample names the moment it came from."""
        return mock.patch.object(km, "_kernel_process_sample", lambda: {"rssKb": len(stages) + 1, "cpuS": 0.5})

    def _bucket(self, raw):
        (b,) = rm.build_buckets(rm.parse_restarts(raw), [], [], [], [], [], {}, "day", None, "UTC")
        return b

    def test_a_late_full_row_after_a_failed_append_keeps_the_prediction_a_raising_drain_left_standing(self):
        # the cut row was built (its prediction names web, one session) but its append failed, and the drain raised: before this
        # fix the late row was built from the drain's result alone, 0 stopped and no turn, which the reader counts as a clean restart.
        # It is the built row with the done row's fields applied as the reader joins the pair, and its process sample is the
        # one the handler took at its entry, before the broadcast
        web, stages, calls = {"sid": SID, "name": "web"}, [], []
        real = km._append_restart_cut

        def first_fails(row):
            calls.append(dict(row))
            return False if len(calls) == 1 else real(row)

        class Raising:
            sessions = {SID: object()}

            def would_cut(inner):
                return [dict(web)]

            def drain(inner, timeout):
                stages.append("drain")
                raise RuntimeError("synthetic drain fault")
        t = self._request()
        with mock.patch.object(km, "_append_restart_cut", side_effect=first_fails), self._staged_sample(stages):
            _fire(self, Raising(), broadcast=lambda *a, **k: stages.append("broadcast"))
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(raw), 1, raw)
        late = raw[0]
        self.assertEqual((late.get("cutTurns"), late.get("stopped"), late.get("auditT"), late.get("exitDone")), ([web], 1, t, True),
                         "the prediction and the session count stand (before this fix: [] and 0 stopped, from a drain that returned nothing)")
        self.assertIn("RuntimeError: synthetic drain fault", late.get("drainError", ""))
        self.assertNotIn("expectsDone", late)
        self.assertEqual(late.get("rssKb"), 1, "the process as the exit began: sampled at the handler's entry, not after the drain")
        built = calls[0]
        self.assertEqual({k: v for k, v in late.items() if k in built}, {k: v for k, v in built.items() if k != "expectsDone"},
                         "every field the built row had, the late row keeps (its drain returned nothing to override them)")
        b = self._bucket(raw)
        self.assertEqual((b["measuredRestarts"], b["cleanRestarts"], b["cutTurns"]), (1, 0, 1),
                         "measured by its prediction, and not clean (before this fix it read as a clean restart)")

    def test_a_late_full_row_with_no_prediction_behind_it_says_so(self):
        # the cut row could not be built at the start, and the drain raised: the late row is the drain's own result (nothing)
        # and no prediction stands behind its turns, so it carries cutTurnsError (the build's fault) and the reader counts it
        # unmeasured, where before this fix the row (0 stopped, no turn, no cutTurnsError) read as a clean restart
        real, built, stages = km._restart_cut_row, [], []

        def flaky(*a, **k):
            built.append(1)
            if len(built) == 1:
                raise RuntimeError("synthetic early-row fault")
            return real(*a, **k)

        class Raising:
            def drain(inner, timeout):
                stages.append("drain")
                raise RuntimeError("synthetic drain fault")
        with mock.patch.object(km, "_restart_cut_row", side_effect=flaky), self._staged_sample(stages):
            _fire(self, Raising(), broadcast=lambda *a, **k: stages.append("broadcast"))
        raw = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(len(raw), 1, raw)
        late = raw[0]
        self.assertIn("RuntimeError: synthetic early-row fault", late.get("cutTurnsError", ""),
                      "no prediction stands behind its turns, and the row says why")
        self.assertIn("RuntimeError: synthetic drain fault", late.get("drainError", ""))
        self.assertEqual((late.get("cutTurns"), late.get("exitDone")), ([], True))
        self.assertEqual(late.get("rssKb"), 1, "the sample taken at the handler's entry, passed in (before this fix: sampled after the drain)")
        b = self._bucket(raw)
        self.assertEqual((b["measuredRestarts"], b["unmeasuredRestarts"], b["cleanRestarts"]), (0, 1, 0),
                         "its turns are unknown: out of the rate, and not a clean restart")

    def test_the_late_full_row_reads_as_the_pair_would_have(self):
        # _exit_late_row applies the done row's fields exactly as the reader's fold joins a cut row and its done row: for a
        # drain that returned (its own counts and turn list, a late verdict) and one that raised (nothing but the error)
        late_row = getattr(km, "_exit_late_row", None)
        self.assertIsNotNone(late_row, "the late full row is the built row joined as the reader joins the pair (no such helper before this fix)")
        web, api = {"sid": SID, "name": "web"}, {"sid": self.SIDS["api"], "name": "api"}
        cut = {"t": 1000, "pid": os.getpid(), "cutTurns": [web], "stopped": 2, "unjoined": 0, "reaped": 0, "watchesArmed": 1,
               "reason": km.SIGNAL_REASON_UNREQUESTED, "auditT": 999, "rssKb": 2048, "cpuS": 9.5,
               "cutTurnsError": "RuntimeError: synthetic predicate fault", "expectsDone": True}
        phases = {"rowS": 0.0, "memosS": 0.1, "listS": 0.0, "primeS": 0.2, "writeS": 0.3, "ckptS": 0.5, "drainS": 0.4}
        counts = {"ckptWritten": 4, "ckptLeft": 1, "primed": 2, "primeSkipped": 0}
        for label, res, turns, reason, err in (
                ("a drain that returned", {"cutTurns": [web, api], "stopped": 3, "unjoined": 1, "reaped": 1}, [web, api],
                 km.SIGNAL_REASON_MANAGER_STOPPED, ""),
                ("a drain that raised", {}, [], cut["reason"], "Traceback (most recent call last):\nRuntimeError: synthetic drain fault")):
            with self.subTest(label):
                done = km._exit_done_row(cut, res, turns, reason, err=err, reason_err="ValueError: synthetic", phases=phases,
                                         counts=counts, deadline_s=3.2, elapsed_s=2.9, now=1003)
                want = FOLD([dict(cut), dict(done)])[0]
                for k in ("doneT", "expectsDone"):
                    want.pop(k, None)
                self.assertEqual(late_row(dict(cut), done), want)

    def test_a_failed_append_is_said_once_on_the_log(self):
        # the review of 2026-10-06: _append_restart_cut returned False without a word, and the done row's result was dropped, so
        # a completed exit whose done row was lost read as killed with nothing on the log. Here the SECOND append (the done row)
        # fails for real: the drain puts a directory where the ledger file was. One line names the row and the fault; the first
        # append, which landed, says nothing
        ledger = km.RESTART_CUTS_FILE

        class Swapping:
            def drain(inner, timeout):
                ledger.unlink()
                ledger.mkdir()                 # an unwritable ledger from here: open(..., "a") raises IsADirectoryError
                return {"cutTurns": [], "stopped": 0}
        try:
            with mock.patch.object(km.sys, "stderr", new_callable=io.StringIO) as err:
                _fire(self, Swapping())
        finally:
            if ledger.is_dir():
                ledger.rmdir()
        said = [l for l in err.getvalue().splitlines() if "restart ledger did not take" in l]
        self.assertEqual(len(said), 1, "one line for the one append that failed: %r" % err.getvalue().splitlines())
        self.assertIn("the exit's done row", said[0])
        self.assertIn("IsADirectoryError", said[0])

    def test_the_session_in_flight_starts_no_document_refresh_once_the_stand_down_is_set(self):
        # the stand-down is read at the settle write's loop top; the session in flight could still start a document refresh (a
        # whole-leaf parse on a thread of its own) after the quiesce. The stand-down lands during this session's assembly write
        stand = km._EXIT_STAND_DOWN
        sess = self._sessions("web")
        refreshed, land = [], []

        def asm_write(*a, **k):
            if land:
                stand.set()                    # the exit's quiesce, while this session's write runs
            return False                       # a restored entry: nothing written, so the refresh branch is next
        with mock.patch.object(km, "_sessions", lambda now, *a, **k: list(sess)), \
             mock.patch.object(km, "_ckpt_settle_key", lambda sid: ("moved",)), \
             mock.patch.object(km, "_stat_key", lambda p: ("synthetic", 1)), \
             mock.patch.object(km, "_heal_cold_folds", lambda leaf, dropped_out=None: []), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
             mock.patch.object(km, "_session_fold_files", lambda sid, leaf: {leaf}), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
             mock.patch.object(km.em, "asm_checkpoint_write", asm_write), \
             mock.patch.object(km, "_asm_refresh_maybe", lambda leaf, sid: refreshed.append(leaf)), \
             mock.patch.object(km, "_display_sdk_human", lambda sid: False), \
             mock.patch.object(km, "_stored_tree", lambda leaf, sid: None):
            for during in (False, True):
                stand.clear()
                land[:] = [1] if during else []
                with mock.patch.dict(km._CKPT_SETTLE_SEEN, {s["sid"]: ("before",) for s in sess}, clear=True), \
                     mock.patch.dict(km._CKPT_PERIODIC_SEEN, {}, clear=True):
                    km._persist_checkpoints(0)
        stand.clear()
        self.assertEqual(refreshed, [sess[0]["path"]], "the refresh started with the stand-down clear, and not once it was set")

    def test_the_pusher_sends_no_frame_under_the_stand_down(self):
        stand = km._EXIT_STAND_DOWN
        pushed = []
        with mock.patch.object(km, "_job_stage", lambda name, thunk: None), \
             mock.patch.object(km, "_push_all", lambda live_map=None: pushed.append(dict(live_map))):
            for set_ in (False, True):
                (stand.set if set_ else stand.clear)()
                km._pusher_cycle_jobs(0, {"standDown": set_}, True)
        stand.clear()
        self.assertEqual(pushed, [{"standDown": False}], "a client connected: pushed with the stand-down clear, not under it")

    def test_a_prediction_that_raises_leaves_a_cut_row_that_says_so(self):
        class Unpredictable:
            sessions = {SID: object()}

            def would_cut(inner):
                raise RuntimeError("synthetic predicate fault")

            def drain(inner, timeout):
                return {"cutTurns": [{"sid": SID, "name": "web"}], "stopped": 1}
        _fire(self, Unpredictable())
        cut, done = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(cut["cutTurns"], [], "nothing predicted")
        self.assertIn("RuntimeError: synthetic predicate fault", cut.get("cutTurnsError", ""), "and the row says why")
        self.assertEqual(done["drainCutTurns"], [{"sid": SID, "name": "web"}], "the done row names what the drain cut")

    def test_the_cut_rows_time_is_the_handlers_entry(self):
        # the outage clock (the next boot's prevCutT and outageS) starts at the handler's entry, not when the row is built: a
        # broadcast of 1.1 s puts the row's build in a later second
        before = time.time()
        _fire(self, LiveDrain(), broadcast=lambda *a, **k: time.sleep(1.1))
        cut = _raw_rows(km.RESTART_CUTS_FILE)[0]
        self.assertGreaterEqual(cut["t"], int(before))
        self.assertLessEqual(cut["t"], int(before + 0.1), "the entry's second, not the row's (%s, entry %.2f)" % (cut["t"], before))

    def test_the_done_row_leaves_out_a_turn_list_equal_to_the_prediction(self):
        web, api = {"sid": SID, "name": "web"}, {"sid": self.SIDS["api"], "name": "api"}
        for cut_by_drain, want in (([web], None), ([web, api], [web, api])):
            with self.subTest(cut_by_drain=len(cut_by_drain)):
                self._reset()

                class Predicting:
                    sessions = {SID: object()}

                    def would_cut(inner):
                        return [dict(web)]

                    def drain(inner, timeout):
                        return {"cutTurns": [dict(x) for x in cut_by_drain], "stopped": 1}
                _fire(self, Predicting())
                self.assertEqual(self._done().get("drainCutTurns"), want,
                                 "named only when the drain cut something other than the prediction")

    def test_a_stop_note_already_on_record_names_the_cut_row_a_stop(self):
        # the shared tail reached with no request (an empty reason with a signal) while the manager, alive, has already noted
        # its stop of this kernel: the provisional verdict reads the note, so the cut row AS WRITTEN says the manager was
        # stopped (the folded record cannot show it: the late verdict would say the same)
        with open(self.AUDIT, "a") as f:
            f.write(json.dumps({"t": int(time.time()), "action": "manager-sigterm", "kernel": "main", "pid": os.getpid(),
                                "reason": "stop", "trigger": "stop"}) + "\n")
        with mock.patch.dict(os.environ, {"ROMP_MANAGER_PID": str(os.getpid())}), \
             mock.patch.object(km, "_sdk_backend", LiveDrain()), \
             mock.patch.object(km, "_exit_quiesce", lambda: None, create=True), \
             mock.patch.object(km.os, "_exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                km._drain_and_exit("", signum=signal.SIGTERM, what="SIGTERM")
        ex.assert_called_once_with(0)
        cut, done = _raw_rows(km.RESTART_CUTS_FILE)
        self.assertEqual(cut["reason"], km.SIGNAL_REASON_MANAGER_STOPPED, "the stop note was visible at the row's write")
        self.assertNotIn("finalReason", done, "and the late verdict agrees")

    def test_the_drain_is_handed_its_floor_when_the_deadline_is_spent(self):
        # a 0.35 s broadcast spends a 0.2 s deadline before the drain begins: the drain still gets its 0.25 s floor
        calls = []

        class Timed:
            def drain(inner, timeout):
                calls.append(timeout)
                return {"cutTurns": [], "stopped": 0}
        with self._work(deadline=0.2, reserve=0.1):
            _fire(self, Timed(), broadcast=lambda *a, **k: time.sleep(0.35))
        self.assertEqual(calls, [0.25])

    def test_the_judges_child_is_joined_after_the_done_row_with_what_the_deadline_has_left(self):
        seen = []

        class End:
            def finish(inner, remaining):
                seen.append(([bool(r.get("exitDone")) for r in _raw_rows(km.RESTART_CUTS_FILE)], remaining))

        class Child:
            def end(inner, background=False):
                return End() if background else None
        with mock.patch.object(km, "_JUDGE_CHILD", Child()), self._work(deadline=2.0, reserve=0.9):
            entry = time.monotonic()
            _fire(self, LiveDrain())
            took = time.monotonic() - entry
        self.assertEqual(len(seen), 1, "joined once")
        rows, remaining = seen[0]
        self.assertEqual(rows, [False, True], "both rows on disk when the join ran")
        self.assertGreater(remaining, 0.0)
        self.assertLessEqual(remaining, 2.0 - took + 0.05, "with what the deadline had left (%.2f s, the exit took %.2f s)" % (remaining, took))


if __name__ == "__main__":
    unittest.main()
