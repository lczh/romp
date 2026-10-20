#!/usr/bin/env python3
"""Restart session (the user 2026-09-23): ONE action that relaunches a session's own CLI process in place.

A session keeps the Claude Code binary it launched with, so a long-running one cannot reach a model only a
newer CLI knows — its alias resolves to the old model and the new id is refused as unrecognized. The only
in-product way onto the new binary was End session then Revive: two destructive-looking steps, through a
confirm dialog, for something that destroys nothing.

What this pins, on both sides of the backend seam:

  * SdkBackend.relaunch, the primitive. Its run loop leaves its ClaudeSDKClient and re-enters it, spawning a
    FRESH CLI from the `claude` path the kernel resolved. So the registry row is never flipped dead, no death
    record is written, and the session is live throughout: no surface can paint it dead on the way through.
    The old CLI is ended with SIGTERM at once (SdkSession.relaunch_now, 2026-10-17), whatever it runs, and is
    NEVER sent the interrupt control request, which on the installed CLI stops every background agent as
    stopped by the user, beyond any SendMessage's reach; nor any of Stop's signals. A running turn is cut with
    the process; a queued one survives for the fresh CLI. A dormant row has no process to replace and is simply
    connected. A sid romp has no record of, and a row that is not alive, refuse in words the user reads.

  * _restart_session, the door behind the WS restartSession op. It hands the work to the OWNING backend
    (nothing here duplicates the revive's resume), runs off the recv loop, and answers the pane that asked
    in the window that asked: `restarted` on success, `restartFailed` carrying the reason otherwise. It
    never focuses anything (the tab you are looking at is yours), never records a death, and leaves the
    names registry and the registry row exactly as it found them.

  * CodexBackend.relaunch, a refusal in Codex's own terms (2026-09-24, the post-merge note on #2125). Every
    Codex session runs on the kernel's one Codex app-server, so no session has a process of its own to
    replace, and End then Revive leaves it on the same app-server: End cuts a running turn and Revive resumes
    on the installed client. The refusal says what does start a fresh one, a restart of the romp kernel, and
    touches nothing; an ended Codex row is pointed at Revive, as an ended Claude row is.

  * Apply now (2026-10-01), the same road for a pick waiting on a quiet session: SdkBackend.apply_now relaunches
    with the cut named "apply" (the session's notice says a settings change was applied now) while a pick waits, and
    answers the no-op NOTHING_TO_APPLY when none does; _apply_now, the door behind the WS applyNow op, answers the
    asking pane only when it refused.

Every leg reds at the merge base on the behaviour, not on a missing name: the two doors do not exist there,
so the tests say so through the AttributeError the getattr guards raise as an explicit failure. Synthetic
fixtures only: a hermetic state root, private placeholder sids, the notes-api demo names.
"""
import asyncio
import json
import os
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")

# Hermetic state BEFORE the loads — they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_CLI_SCOPE"] = "0"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_restart", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_restart", os.path.join(BIN, "romp_sdk_backend.py"))
cb = load_source("romp_codex_backend_restart", os.path.join(ROOT, "kernel", "codex_backend.py"))

SID = "3a7c0001-2222-3333-4444-555555555555"      # a running session named web (this module's own sid)
OTHER = "3a7c0002-2222-3333-4444-555555555555"    # one this kernel has never heard of
UNHELD = "3a7c0003-2222-3333-4444-555555555555"   # one this kernel knows that no backend holds a row for


def _no_app_server():
    raise RuntimeError("this lab runs no Codex app-server")


def _codex_backend(dead=False):
    """A real CodexBackend over a hermetic state root holding one Codex row, web under SID, and no app-server. The
    row is seeded in the backend's own registry file, so nothing starts a worker or a pump: there is no queue to
    recover at load, and no client is ever built."""
    d = tempfile.mkdtemp()
    root = Path(d, "codex")
    root.mkdir(parents=True)
    (root / "registry.json").write_text(json.dumps({SID: {"tid": "T-1", "name": "web", "cwd": "/TESTDIR",
                                                          "dead": dead}}))
    return cb.CodexBackend(d, client_factory=_no_app_server, log=lambda m: None)


class _AppServerClient:
    """The installed Codex client, the one every Codex session's turns run on: records every call made on it."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append(name)


def _backend(d):
    # per-session hosts OFF in this bare state root (they are on by default, T348): nothing here means to
    # start a real bin/romp-session-host, and relaunch's dormant road calls the real connect()
    Path(d, "session-hosts").write_text("off")
    return sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)


def _reg(d, sid=SID, **extra):
    r = {"sid": sid, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": sid, "effort": "high"}
    r.update(extra)
    sb.write_reg(Path(d), sid, r)
    return r


class _Sess:
    """The SdkSession as relaunch touches it: the lock and the counter it reads, and every call it could make,
    recorded in the order they land. The contract since 2026-10-17 is ONE call, relaunch_now: never the interrupt,
    which stops the session's background agents for good, and never the old cut road (request_reconnect cut=True)."""

    def __init__(self, calls, inflight=0, name="web"):
        self.calls, self.inflight, self.name, self.sid = calls, inflight, name, SID
        self._lock = threading.RLock()

    def request_reconnect(self, defer=True, cut=False, why="", cause=""):
        self.calls.append(("request_reconnect", "cut" if cut else defer))   # a Restart takes the cut road (2026-10-01: a pick waits)
        self.causes = getattr(self, "causes", []) + [cause]                  # …naming itself for the notice (CUT_BY)

    def interrupt(self):
        self.calls.append(("interrupt",))              # a Restart must never come here (relaunch_now's docstring)
        return "sent"

    def relaunch_now(self, cause=""):
        self.calls.append(("relaunch_now", cause))     # the SIGTERM relaunch, named for the notice (CUT_BY)
        return True


class RelaunchPrimitive(unittest.TestCase):
    """SdkBackend.relaunch: what it does to the process, and what it deliberately leaves alone."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.calls = []

    def relaunch(self, sid=SID):
        fn = getattr(self.be, "relaunch", None)
        self.assertIsNotNone(fn, "SdkBackend has no relaunch: Restart session has no primitive to ride")
        return fn(sid)

    def test_an_idle_session_relaunches_now_and_is_never_interrupted(self):
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls)
        self.assertEqual(self.relaunch(), "", "an idle running session relaunches")
        self.assertEqual(self.calls, [("relaunch_now", "restart")],
                         "the SIGTERM relaunch, named for the notice: the session hears it was restarted")

    def test_a_running_turn_is_cut_by_the_sigterm_relaunch_and_never_interrupted(self):
        # the user 2026-10-17: the interrupt a Restart sent a running turn stopped the session's background agents as
        # stopped by the user, which no SendMessage can resume; the CLI is ended with SIGTERM instead, at once
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls, inflight=1)
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(self.calls, [("relaunch_now", "restart")], "no interrupt, and no wait for the turn's result")

    def test_a_queued_turn_with_nothing_running_takes_the_same_road(self):
        # busy() also answers True for a turn merely QUEUED; nothing is running to cut, and the queue survives the
        # relaunch (the fresh CLI takes it)
        _reg(self.d)
        s = _Sess(self.calls)
        s._pending = ["please pick the search work back up"]
        self.be.sessions[SID] = s
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(self.calls, [("relaunch_now", "restart")])

    def test_the_session_is_never_marked_dead_and_no_death_is_recorded(self):
        # the whole point: End + Revive reached the same place by killing the session first, and every
        # surface saw it dead on the way through
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls, inflight=1)
        self.relaunch()
        reg = sb.read_reg(Path(self.d), SID)
        self.assertEqual((reg.get("alive"), reg.get("name"), reg.get("lastSid"), reg.get("effort")),
                         (True, "web", SID, "high"), "the row is untouched: alive, named, on its newest transcript")
        self.assertIn(SID, self.be.sessions, "and the session object is still the backend's")

    def test_a_dormant_row_has_no_process_to_replace_and_is_simply_connected(self):
        _reg(self.d)
        connects = []
        self.be.connect = lambda sid: (connects.append(sid) or True)
        self.assertEqual(self.relaunch(), "")
        self.assertEqual(connects, [SID], "connect() starts a fresh CLI, which is the same outcome by the shortest road")

    def test_a_dormant_row_whose_cli_will_not_start_says_so(self):
        _reg(self.d)
        self.be.connect = lambda sid: False
        self.assertIn("did not start", self.relaunch())

    def test_a_row_that_is_not_alive_refuses_and_points_at_the_revive(self):
        _reg(self.d, alive=False)
        r = self.relaunch()
        self.assertIn("not running", r)
        self.assertIn("revive", r)
        self.assertEqual(self.calls, [])

    def test_a_sid_with_no_record_refuses(self):
        self.assertIn("no record of this session", self.relaunch(OTHER))

    def test_a_backend_with_no_relaunch_primitive_refuses_in_words_for_no_backend_in_particular(self):
        # the ABC's default (session_backend.move's shape), which the unowned route and any future backend
        # inherit — so a backend never reads as relaunchable by omission
        self.assertIn("no way to relaunch", km._UNOWNED.relaunch(SID))
        self.assertIn("revive", km._UNOWNED.relaunch(SID))


class ApplyNowPrimitive(unittest.TestCase):
    """SdkBackend.apply_now (2026-10-01): Restart session's road for a pick waiting on a quiet session, named "apply",
    and a no-op said as one when nothing waits."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.calls = []

    def apply(self, sid=SID):
        fn = getattr(self.be, "apply_now", None)
        self.assertIsNotNone(fn, "SdkBackend has no apply_now: a waiting pick has no way through")
        return fn(sid)

    def test_a_waiting_pick_takes_the_restart_road_named_apply(self):
        _reg(self.d)
        for mark in ("_switch_wanted", "_effort_pending", "_auth_pending", "_fast_pending", "_mode_pending"):
            self.calls = []
            s = _Sess(self.calls, inflight=1)
            setattr(s, mark, "billing" if mark == "_switch_wanted" else "high")
            self.be.sessions[SID] = s
            self.assertEqual(self.apply(), "", mark)
            self.assertEqual(self.calls, [("relaunch_now", "apply")],
                             mark + ": a Restart's road, the SIGTERM relaunch and no interrupt, named for the notice")

    def test_nothing_waiting_is_a_no_op_said_as_one(self):
        _reg(self.d)
        self.be.sessions[SID] = _Sess(self.calls)
        self.assertEqual(self.apply(), sb.NOTHING_TO_APPLY)
        self.assertEqual(self.calls, [], "nothing relaunched, nothing interrupted")
        self.assertEqual(sb.NOTHING_TO_APPLY, km.sb.NOTHING_TO_APPLY, "one text, which the kernel's /apply route compares")

    def test_a_dormant_row_with_a_recorded_waiting_pick_is_connected_and_one_without_is_left_alone(self):
        _reg(self.d, relaunchWait={"why": "billing", "t": 1, "picks": {"billing": {"auth": "login", "authLogin": ""}}})
        connects = []
        self.be.connect = lambda sid: (connects.append(sid) or True)
        self.assertEqual(self.apply(), "")
        self.assertEqual(connects, [SID], "its connect is the relaunch, and carries the pick")
        _reg(self.d)
        self.assertEqual(self.apply(), sb.NOTHING_TO_APPLY)
        self.assertEqual(connects, [SID], "no pick on record: nothing started")

    def test_refusals_say_why_in_words(self):
        self.assertIn("no record of this session", self.apply(OTHER))
        _reg(self.d, alive=False, relaunchWait={"why": "effort", "t": 1, "picks": {"effort": {"effort": "max"}}})
        self.assertIn("revive", self.apply())

    def test_a_backend_with_no_relaunch_primitive_has_nothing_waiting(self):
        self.assertEqual(km._UNOWNED.apply_now(SID), km.sb.NOTHING_TO_APPLY)


class CodexRelaunch(unittest.TestCase):
    """CodexBackend.relaunch on the rows the door cannot tell apart by routing alone (2026-09-24, the post-merge
    note on #2125): a sid the backend has no row for, and a row ended between the routing and the call."""

    def relaunch(self, be, sid=SID):
        fn = getattr(be, "relaunch", None)
        self.assertIsNotNone(fn, "CodexBackend has no relaunch of its own: a Codex session reads the generic End then "
                                 "Revive advice, which on Codex cuts a running turn and keeps the same app-server")
        return fn(sid)

    def test_a_sid_it_has_no_row_for_refuses_in_words(self):
        self.assertIn("no record of this session", self.relaunch(_codex_backend(), OTHER))

    def test_an_ended_row_is_pointed_at_revive_not_at_a_kernel_restart(self):
        r = self.relaunch(_codex_backend(dead=True))
        self.assertIn("not running", r)
        self.assertIn("revive", r)
        self.assertNotIn("app-server", r, "an ended row has no turn to lose: Revive is what brings it back")


class _Loop:
    """The session's event loop as interrupt() touches it: what it schedules is counted, never run."""

    def __init__(self):
        self.scheduled = 0

    def call_soon_threadsafe(self, fn):
        self.scheduled += 1


class ARestartNeverInterrupts(unittest.TestCase):
    """A Restart sends the CLI no interrupt control request and no Stop-ladder signal (the user 2026-10-17). Its old ask,
    the polite interrupt it sent a running turn, makes the installed CLI stop every background agent the session runs as
    stopped by the user, and SendMessage then refuses to resume any of them: two pairs of agents were lost that way,
    one pair to a Stop and the next to a Restart. The relaunch ends the CLI with SIGTERM instead (relaunch_now), which
    the CLI does not record as the user's stop, so the fresh CLI's session can resume them. A real SdkSession, its
    channel up and a turn in flight; its loop records what is scheduled and runs none of it, and the signal rung is
    recorded instead of sent."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        _reg(self.d)
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = s
        s.inflight = 1
        s.loop, s.client = _Loop(), object()
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append(action)
        self.old_road = []
        s.request_reconnect = lambda *a, **k: self.old_road.append((a, k))   # the cut road, which a Restart no longer takes

    def _interrupt_rows(self):
        """The rows the backend wrote to this session's state log to settle a turn it asked to stop."""
        p = Path(self.d, "states", SID + ".jsonl")
        rows = [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
        return [(r.get("state"), r.get("by")) for r in rows if r.get("by") == "interrupt"]

    def test_a_restart_with_a_turn_in_flight_sends_no_interrupt_and_no_signal(self):
        for _ in range(3):
            self.assertEqual(self.be.relaunch(SID), "")
        self.assertEqual(self.signals, [], "never Stop's SIGINT or SIGKILL")
        self.assertFalse(self.s._interrupted, "no interrupt went out, so nothing reads Interrupting…")
        self.assertEqual(self.s._intr_level, 0, "Stop's episode is untouched")
        self.assertEqual(self._interrupt_rows(), [], "and nothing settled as interrupted")
        self.assertEqual(self.old_road, [], "never the old cut road, which waited for the interrupted turn's result")
        self.assertEqual(self.s.loop.scheduled, 3, "each press arms the SIGTERM relaunch on the loop, and that is all")
        self.assertEqual(self.s._cut_cause, "restart", "named for the notice the fresh CLI is fed")

    def test_stop_after_a_restart_still_starts_with_the_polite_request(self):
        self.assertEqual(self.be.relaunch(SID), "")
        self.assertEqual(self.s.interrupt(), "sent", "a Restart takes no rung of Stop's ladder")
        self.assertEqual(self.s.interrupt(), "sigint")
        self.assertEqual(self.signals, ["sigint"])

    def test_the_arm_on_the_loop_marks_the_sigterm_and_carries_every_standing_ask(self):
        s = self.s
        s._switch_wanted, s._reconnect_when_idle = "billing", True
        s._do_relaunch_now()
        self.assertEqual((s._reconnect, s._relaunch_term, s._reconnect_when_idle, s._switch_wanted, s._reconnect_switch_why),
                         (True, True, False, "", ""), "armed now, the SIGTERM marked, the waiting pick riding it")

    def test_a_session_with_no_loop_yet_arms_nothing_and_its_next_connect_is_the_fresh_cli(self):
        lines = []
        self.be._log_cb = lines.append
        self.s.loop = None
        self.assertEqual(self.be.relaunch(SID), "")
        self.assertFalse(self.s._relaunch_term)
        self.assertTrue(any(m.startswith("relaunch (web): its CLI is not connected yet") for m in lines), lines)


def _timed_out():
    """No answer within the SDK's timeout: a bare Exception raised from TimeoutError (see _cli_refusal)."""
    e = Exception("Control request timeout: interrupt")
    e.__cause__ = TimeoutError()
    return e


def _refused():
    """The CLI's error answer, which the SDK raises at once as a bare Exception carrying the CLI's text."""
    return Exception("no current client")


class _HostSide:
    """A session's host transport as _term_for_relaunch reaches it: the signal request it sends, and the exit frame the
    teardown's close waits for."""

    def __init__(self, fail=None):
        self.sent, self.fail, self.exit_info = [], fail, None

    async def signal(self, sig):
        if self.fail:
            raise self.fail
        self.sent.append(sig)


class TheSigtermReachesTheCli(unittest.TestCase):
    """_term_for_relaunch, both roads: under a session host the host is asked to signal its own child (the kernel never
    knows the CLI's pid), and a kernel child's own process is terminated through the SDK transport. Either records
    what it signalled so the loop top can read whether that CLI is gone; a failure is said and leaves the teardown to
    end the CLI as before."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.lines = []
        self.be._log_cb = self.lines.append
        _reg(self.d)
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.s._relaunch_term = True

    def test_under_a_host_the_host_signals_its_cli_and_the_exit_frame_proves_it_gone(self):
        host = self.s._host = _HostSide()
        asyncio.run(self.s._term_for_relaunch(object()))
        self.assertEqual(host.sent, ["TERM"])
        self.assertFalse(self.s._relaunch_term, "spent")
        termed = self.s._relaunch_termed
        self.assertEqual(termed, ("host", host))
        self.assertFalse(self.s._termed_cli_gone(termed), "no exit frame yet: not proven gone")
        host.exit_info = {"code": None, "signal": 15, "cause": "end"}
        self.assertTrue(self.s._termed_cli_gone(termed))

    def test_a_kernel_childs_process_is_terminated(self):
        events = []

        class _P:
            pid, returncode = 4242, None

            def terminate(self):
                events.append("SIGTERM")
                self.returncode = -15

        client = types.SimpleNamespace(_transport=types.SimpleNamespace(_process=_P()))
        asyncio.run(self.s._term_for_relaunch(client))
        self.assertEqual(events, ["SIGTERM"])
        self.assertTrue(self.s._termed_cli_gone(self.s._relaunch_termed))
        self.assertTrue(any("ended the CLI (pid 4242) with SIGTERM" in m for m in self.lines), self.lines)

    def test_a_host_that_cannot_be_asked_is_said_and_proves_nothing(self):
        self.s._host = _HostSide(fail=ConnectionResetError("the host's socket closed"))
        asyncio.run(self.s._term_for_relaunch(object()))
        self.assertIsNone(self.s._relaunch_termed)
        self.assertFalse(self.s._termed_cli_gone(self.s._relaunch_termed))
        self.assertTrue(any("could not ask the session host to end the CLI" in m for m in self.lines), self.lines)

    def test_no_process_to_end_is_said(self):
        asyncio.run(self.s._term_for_relaunch(types.SimpleNamespace()))
        self.assertIsNone(self.s._relaunch_termed)
        self.assertTrue(any("no running CLI process to end" in m for m in self.lines), self.lines)


class _FailingControl:
    """The SDK client as _do_interrupt awaits it: every control request fails, after the gate when one is given
    (a request still waiting on its answer)."""

    def __init__(self, failure, gate=None):
        self.failure, self.gate, self.requests = failure, gate, 0

    async def interrupt(self):
        self.requests += 1
        if self.gate is not None:
            await asyncio.get_running_loop().run_in_executor(None, self.gate.wait, 10)
        raise self.failure()


class AStopsFailedRequestStillEscalates(unittest.TestCase):
    """Stop's polite request that FAILS escalates on the same press when a turn is in flight (terminal parity,
    2026-07-10), and a Restart, which sends the CLI no request at all since 2026-10-17, leaves that escalation alone.
    A real event loop runs the request, the client fails it the ways the SDK fails, and the signal rung is recorded
    instead of sent. A shutdown's request escalates the same way."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.lines = []
        self.be._log_cb = self.lines.append
        _reg(self.d)
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = s
        s.inflight = 1
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append(action)
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="restart-test-loop", daemon=True)
        self.thread.start()
        s.loop = self.loop
        self.addCleanup(self._close_loop)

    def _close_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()

    def _settled(self, failures):
        """Wait out the requests sent: each has failed and logged, the loop step that failed it has finished, and
        any signal thread that step started has run."""
        deadline = time.monotonic() + 10
        while sum("control request failed" in m for m in self.lines) < failures:
            if time.monotonic() > deadline:
                self.fail("the control request never failed; log %r" % self.lines[-6:])
            time.sleep(0.01)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=10)
        for t in threading.enumerate():
            if t.name == "sdk-intr:web":
                t.join(timeout=10)

    def test_a_stops_own_failed_request_still_signals_on_that_press(self):
        # the escalation is Stop's and stays (terminal parity, 2026-07-10): a turn in flight that the polite
        # channel could not stop gets SIGINT on the same press
        self.s.client = _FailingControl(_refused)
        self.assertTrue(self.be.interrupt(SID))
        self._settled(1)
        self.assertEqual((self.signals, self.s._intr_level), (["sigint-auto"], 2))

    def test_a_restart_while_a_stops_request_is_pending_leaves_that_stops_escalation_alone(self):
        # a Restart clicked while Stop's request waits sends the CLI nothing (its relaunch ends the CLI with SIGTERM
        # once the loop gets to it), and that request's failure still escalates on Stop's press
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.s.client = _FailingControl(_timed_out, gate)
        self.assertTrue(self.be.interrupt(SID))             # Stop: its request goes out and waits
        self.assertEqual(self.be.relaunch(SID), "")         # Restart: no request of its own
        gate.set()                                          # Stop's request times out
        self._settled(1)
        self.assertEqual(self.s.client.requests, 1)
        self.assertEqual(self.signals, ["sigint-auto"])

    def test_a_shutdowns_failed_request_still_signals(self):
        # shutdown() schedules _do_interrupt(): a turn in flight that the polite channel could not stop still gets SIGINT
        self.s.client = _FailingControl(_refused)
        self.s.shutdown()
        self._settled(1)
        self.assertEqual(self.s.client.requests, 1, "shutdown sent the polite request")
        self.assertEqual((self.signals, self.s._intr_level), (["sigint-auto"], 2))


class _Be:
    """The owning backend as the door touches it."""

    def __init__(self, answer="", entered=None, gate=None):
        self.answer, self.entered, self.gate, self.calls, self.threads = answer, entered, gate, [], []
        self.applied = []

    def apply_now(self, sid):
        self.applied.append(sid)
        self.threads.append(threading.current_thread())
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer

    def relaunch(self, sid):
        self.calls.append(sid)
        self.threads.append(threading.current_thread())
        if self.entered is not None:
            self.entered.set()
            self.gate.wait(timeout=10)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class _DoorLab(unittest.TestCase):
    """The kernel around a door: the views it answers, the routing to a stand-in backend, the names it knows."""

    def patch(self, name, value):
        saved = getattr(km, name)
        setattr(km, name, value)
        self.addCleanup(setattr, km, name, saved)

    def setUp(self):
        self.sent = []
        self.patch("_send_to_view", lambda app, msg, wid: self.sent.append((app, msg, wid)))
        self.patch("_push_soon", lambda: None)
        self.reveals = []
        self.patch("_reveal_chat_for", lambda client, msg: self.reveals.append((client, msg)))
        self.patch("_name_of", lambda sid: "web" if sid == SID else None)
        self.patch("_kernel_knows", lambda sid: sid == SID)
        self.be = _Be()
        saved = self.real_backend_for = km.Sessions.__dict__["backend_for"]
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self.addCleanup(setattr, km.Sessions, "backend_for", saved)
        self.client = {"app": "chat", "wid": "win-A"}

    def frames(self):
        return [(app, m["type"], m["id"], wid) for app, m, wid in self.sent]


class RestartDoor(_DoorLab):
    """_restart_session and the WS op behind it."""

    def setUp(self):
        super().setUp()
        self.door = getattr(km, "_restart_session", None)
        self.assertIsNotNone(self.door, "the kernel has no _restart_session door")

    def test_a_restart_relaunches_through_the_owning_backend_and_answers_the_asking_pane(self):
        self.door(SID, self.client)
        self.assertEqual(self.be.calls, [SID], "the backend that owns the sid does the work")
        self.assertEqual(self.frames(), [("chat", "restarted", SID, "win-A")],
                         "the pane that asked, in the window that asked, and nowhere else")
        self.assertEqual(self.sent[0][1]["name"], "web", "named, as every answer about a session is")
        self.assertEqual(self.reveals, [], "a restart never moves the focus: the tab you are looking at is yours")

    def test_the_Sessions_panes_own_ask_is_answered_there(self):
        self.door(SID, {"app": "fleet", "wid": "win-B"})
        self.assertEqual(self.frames(), [("fleet", "restarted", SID, "win-B")])

    def test_an_asker_with_no_pane_of_ours_is_answered_in_both_that_offer_the_row(self):
        self.door(SID, {"app": "timeline", "wid": "win-C"})
        self.assertEqual([a for a, _, _, _ in self.frames()], ["chat", "fleet"])

    def test_a_backend_refusal_reaches_the_asker_verbatim(self):
        self.be.answer = "this session is not running — revive it to bring it back"
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        self.assertEqual(self.sent[0][1]["text"], "this session is not running — revive it to bring it back")

    def test_a_backend_that_raises_is_a_named_failure_not_a_silent_drop(self):
        self.be.answer = RuntimeError("the control channel is gone")
        self.door(SID, self.client)
        self.assertEqual([t for _, t, _, _ in self.frames()], ["restartFailed"])
        self.assertIn("the control channel is gone", self.sent[0][1]["text"])

    def test_a_backend_without_the_primitive_answers_the_base_refusal_not_an_attribute_error(self):
        # a backend may duck-type the interface without subclassing SessionBackend, and then it inherits no relaunch:
        # the door met the missing method as an AttributeError and sent its text as the toast (the review of #2059,
        # 2026-09-23). Such a backend answers the base refusal, as the move door does.
        class DuckTyped:
            pass
        self.be = DuckTyped()
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("no way to relaunch", text, "the base refusal's words")
        self.assertNotIn("has no attribute", text, "never a Python error as the toast")

    def test_a_codex_session_is_told_what_starts_a_fresh_app_server_and_nothing_is_touched(self):
        # every Codex session shares the kernel's one Codex app-server, so the base refusal's End then Revive did not
        # reach a newer binary: End cut a running turn, and Revive resumed on the same installed client (2026-09-24,
        # the post-merge note on #2125). The refusal names what does start a fresh one, and it changes nothing: the
        # turn keeps running on the client it started on, and the client is neither asked anything nor replaced.
        self.be = _codex_backend()
        s = self.be._session(SID)
        client = self.be._client = _AppServerClient()
        generation = self.be._client_generation
        with s.lock:
            s.turn_id = "t-1"                                  # a turn in flight on the shared app-server
        row = (self.be.root / "registry.json").read_bytes()
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("app-server", text, "why there is no process of this session's own to relaunch")
        self.assertIn("romp kernel", text, "what starts a fresh app-server")
        self.assertNotIn("revive", text.lower(), "End then Revive keeps a Codex session on the app-server it had")
        self.assertNotIn("has no attribute", text)
        self.assertEqual(client.calls, [], "nothing is asked of the shared app-server: no interrupt, no close")
        self.assertIs(self.be._client, client, "and the client every Codex session runs on is not replaced")
        self.assertEqual(self.be._client_generation, generation)
        self.assertEqual((s.dead, s.turn_id), (False, "t-1"), "the session is not ended and its turn is not cut")
        self.assertEqual((self.be.root / "registry.json").read_bytes(), row, "the registry row is not rewritten")

    def routed(self, be):
        """The kernel's own routing, Sessions.backend_for, instead of the stub, with this Codex backend as the kernel's
        and no Claude Code backend to own the sid first."""
        km.Sessions.backend_for = self.real_backend_for
        self.patch("_sdk", lambda: None)
        self.patch("_codex", lambda: be)

    def test_the_kernels_routing_hands_a_live_codex_session_to_the_codex_backend(self):
        self.routed(_codex_backend())
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        self.assertIn("app-server", self.sent[0][1]["text"], "the Codex backend's own refusal, not the base one")

    def test_an_ended_codex_session_is_pointed_at_revive_as_an_ended_claude_session_is(self):
        # owns() is live-only by design, so the routing alone hands an ended Codex row to the unowned route, whose
        # base refusal told the user to end a session that had already ended; the revive door routes that row to the
        # Codex backend by its record, and so does this one (2026-09-24, the post-merge note on #2125)
        self.routed(_codex_backend(dead=True))
        self.door(SID, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", SID, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("not running", text)
        self.assertIn("revive", text)
        self.assertNotIn("end it", text, "the session has already ended")

    def test_a_sid_the_codex_backend_holds_no_row_for_is_not_handed_to_it(self):
        # the ended-row routing keys on the Codex backend's record, not on the unowned route alone: a sid this kernel
        # knows but no backend holds is not a Codex session, so it keeps the base refusal instead of the Codex
        # backend's "no record" (2026-09-24, the post-merge note on #2125)
        self.patch("_kernel_knows", lambda sid: sid in (SID, UNHELD))
        self.routed(_codex_backend())
        self.door(UNHELD, self.client)
        self.assertEqual(self.frames(), [("chat", "restartFailed", UNHELD, "win-A")])
        text = self.sent[0][1]["text"]
        self.assertIn("no way to relaunch", text, "the unowned route's base refusal")
        self.assertNotIn("no record", text, "the Codex backend was never asked about a sid it holds no row for")

    def test_a_sid_this_kernel_does_not_have_is_refused_before_any_backend_is_asked(self):
        self.door(OTHER, self.client)
        self.assertEqual(self.be.calls, [], "no backend is asked about a session this kernel has never heard of")
        self.assertEqual(self.frames(), [("chat", "restartFailed", OTHER, "win-A")])
        self.assertIn("no session with id", self.sent[0][1]["text"])

    def test_the_ws_op_answers_off_the_recv_loop(self):
        # the relaunch takes as long as an interrupt and a reconnect take; the socket must not wait on it
        entered, gate = threading.Event(), threading.Event()
        self.be = _Be(entered=entered, gate=gate)
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        client = dict(self.client, alive=True, send=lambda raw: None)
        km.Handler._dispatch_ws(None, {"type": "restartSession", "id": SID}, client)
        self.assertTrue(entered.wait(timeout=10), "the op was never handled")
        self.assertEqual(self.sent, [], "the dispatch returned while the relaunch was still in flight")
        gate.set()
        for _ in range(500):
            if self.sent:
                break
            threading.Event().wait(0.01)
        self.assertEqual(self.frames(), [("chat", "restarted", SID, "win-A")])
        self.assertIsNot(self.be.threads[0], threading.current_thread(), "…on a thread of its own")

    def test_the_op_is_advertised_so_a_newer_page_meets_the_unknownOp_refusal_not_silence(self):
        self.assertIn("restartSession", km.KERNEL_WS_CAPS)

    def test_an_op_with_no_id_takes_no_thread_and_is_answered_unknownOp_like_any_unhandled_frame(self):
        sent = []
        client = dict(self.client, alive=True, send=lambda raw: sent.append(json.loads(raw)))
        km.Handler._dispatch_ws(None, {"type": "restartSession"}, client)
        self.assertEqual(self.be.calls, [])
        self.assertEqual([m.get("type") for m in sent], ["unknownOp"])


class ApplyNowDoor(_DoorLab):
    """_apply_now and the WS applyNow op (2026-10-01): the owning backend's apply_now, answered to the asking pane only
    when it refused; success says nothing (the pick's own marks clear on the next build) and never moves the focus."""

    def setUp(self):
        super().setUp()
        self.pushes = []
        self.patch("_push_soon", lambda: self.pushes.append(1))
        self.door = getattr(km, "_apply_now", None)
        self.assertIsNotNone(self.door, "the kernel has no _apply_now door")

    def test_it_applies_through_the_owning_backend_and_says_nothing_on_success(self):
        self.door(SID, self.client)
        self.assertEqual((self.be.applied, self.be.calls), ([SID], []), "apply_now, never the plain relaunch")
        self.assertEqual(self.sent, [], "a success is the pick's marks clearing, not a frame")
        self.assertEqual(self.pushes, [1], "the next build carries the relaunch")
        self.assertEqual(self.reveals, [])

    def test_a_refusal_and_a_raced_no_op_reach_the_asker_as_applyFailed(self):
        for answer in ("this session is not running — revive it to bring it back", sb.NOTHING_TO_APPLY):
            self.sent.clear()
            self.be.answer = answer
            self.door(SID, self.client)
            self.assertEqual(self.frames(), [("chat", "applyFailed", SID, "win-A")])
            self.assertEqual((self.sent[0][1]["text"], self.sent[0][1]["name"]), (answer, "web"))

    def test_a_backend_that_raises_or_has_no_apply_now_is_named_not_an_attribute_error(self):
        self.be.answer = RuntimeError("the control channel is gone")
        self.door(SID, self.client)
        self.assertIn("the control channel is gone", self.sent[0][1]["text"])
        self.sent.clear()
        class DuckTyped:
            pass
        self.be = DuckTyped()
        self.door(SID, self.client)
        self.assertEqual(self.sent[0][1]["text"], km.sb.NOTHING_TO_APPLY, "the base no-op: it relaunches nothing")

    def test_a_sid_this_kernel_does_not_have_is_refused_before_any_backend_is_asked(self):
        self.door(OTHER, self.client)
        self.assertEqual(self.be.applied, [])
        self.assertEqual(self.frames(), [("chat", "applyFailed", OTHER, "win-A")])
        self.assertIn("no session with id", self.sent[0][1]["text"])

    def test_the_ws_op_answers_off_the_recv_loop_and_is_advertised(self):
        self.be.answer = "a refusal, so the door answers"
        client = dict(self.client, alive=True, send=lambda raw: None)
        km.Handler._dispatch_ws(None, {"type": "applyNow", "id": SID}, client)
        for _ in range(500):
            if self.sent:
                break
            threading.Event().wait(0.01)
        self.assertEqual(self.frames(), [("chat", "applyFailed", SID, "win-A")])
        self.assertIsNot(self.be.threads[0], threading.current_thread(), "…on a thread of its own")
        self.assertIn("applyNow", km.KERNEL_WS_CAPS, "advertised: a page newer than its kernel meets unknownOp, not silence")


class RelaunchLogsWhatItDoes(unittest.TestCase):
    """relaunch's log line says what it does: the CLI is ended with SIGTERM and a fresh one resumes the same
    conversation, plus, with a turn in flight, that the turn is cut; a session with no loop to arm on says its next
    connect is the fresh CLI. Stop's presses keep reporting the rung each took. A real SdkSession whose loop records
    what is scheduled and runs none of it; the signal rung is recorded instead of sent."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.be = _backend(self.d)
        self.lines = []
        self.be._log_cb = self.lines.append
        _reg(self.d)
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = s
        s.loop, s.client = _Loop(), object()
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append(action)

    def _line(self, gesture=None, prefix="relaunch (web)"):
        before = len(self.lines)
        self.assertEqual((gesture or self.be.relaunch)(SID), "")
        mine = [m for m in self.lines[before:] if m.startswith(prefix)]
        self.assertEqual(len(mine), 1, "one line per press; log %r" % self.lines[before:])
        return mine[0]

    def test_a_running_turn_is_said_to_be_cut(self):
        self.s.inflight = 1
        self.assertEqual(self._line(), "relaunch (web): ending its CLI with SIGTERM, which cuts the running turn; a fresh "
                         "one resumes the same conversation")

    def test_an_idle_session_says_only_what_it_does(self):
        self.assertEqual(self._line(), "relaunch (web): ending its CLI with SIGTERM; a fresh one resumes the same "
                         "conversation")

    def test_apply_now_says_it_is_apply_now(self):
        self.s._switch_wanted = "billing"
        self.assertEqual(self._line(self.be.apply_now, "apply now (web)"),
                         "apply now (web): ending its CLI with SIGTERM; a fresh one resumes the same conversation")

    def test_each_stop_press_reports_what_it_did(self):
        self.s.inflight = 1
        self.assertEqual(self.s.interrupt(), "sent")
        self.assertEqual(self.s.interrupt(), "sigint")
        self.assertEqual(self.s.interrupt(), "sigkill")
        self.assertEqual(self.signals, ["sigint", "sigkill"])


if __name__ == "__main__":
    unittest.main()
