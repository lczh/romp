#!/usr/bin/env python3
"""Interrupt escalation, terminal parity (the user 2026-07-10): in a terminal ctrl+c is a SIGNAL the
process cannot silently drop, but the SDK interrupt was a control-channel REQUEST — a wedged CLI ignored
it ('no current client' swallowed by a bare except), a missing client made the press a silent no-op, and
the stop button had no path to the kill the design itself named as the recovery (nimbus sat unresponsive
until a manual restart). Now each press climbs interrupt_action's ladder — control request, SIGINT the
CLI, SIGKILL (crash-heal respawns) — every rung logs, a failed control request auto-escalates when a turn
is provably in flight, and the episode resets when a turn settles or a fresh one starts, or when the CLI
answers the request with no turn to settle (an idle session's Ctrl+C pressed twice once sent SIGINT, which
ends the CLI). find_session_cli is the signal's targeting: it can only ever match our OWN child resuming our
sid. Synthetic fixtures."""
import asyncio
import os
import signal
import threading
import time
import unittest
from pathlib import Path
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_intr", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"
IDLE_SID = "7c1a0001-2222-3333-4444-555555555555"   # the answered-stop lab's own session, web
FORK = "99999999-8888-7777-6666-555555555555"
KERNEL = 4242
MARK = "--input-format stream-json"


def ps_line(pid, ppid, sid=SID, mark=True):
    cmd = "/usr/local/bin/claude --output-format stream-json --resume %s %s" % (sid, MARK if mark else "")
    return "%5d %5d %s" % (pid, ppid, cmd)


class InterruptLadder(unittest.TestCase):
    """interrupt_action: what a stop press does, given the episode's rung and the channel's health."""

    def test_first_press_with_a_live_channel_is_the_polite_request(self):
        self.assertEqual(sb.interrupt_action(0, True), ("control", 1))

    def test_first_press_with_no_channel_goes_straight_to_sigint(self):
        self.assertEqual(sb.interrupt_action(0, False), ("sigint", 2),
                         "no client/loop means the request would be the old silent no-op")

    def test_second_press_escalates_even_with_a_live_channel(self):
        self.assertEqual(sb.interrupt_action(1, True), ("sigint", 2),
                         "the first request produced no settle event — don't just ask again")

    def test_third_press_and_beyond_kill(self):
        self.assertEqual(sb.interrupt_action(2, True), ("sigkill", 3))
        self.assertEqual(sb.interrupt_action(3, False), ("sigkill", 3))


class _AnsweringControl:
    """The SDK client as _do_interrupt awaits it: every control request is answered, after the gate when one is given
    (a request still waiting on its answer)."""

    def __init__(self, gate=None):
        self.gate, self.requests, self.answered = gate, 0, 0

    async def interrupt(self):
        self.requests += 1
        if self.gate is not None:
            await asyncio.get_running_loop().run_in_executor(None, self.gate.wait, 10)
        self.answered += 1


class AnAnsweredStopOnAnIdleSessionEndsItsEpisode(unittest.TestCase):
    """The ladder climbs only past a rung that failed to produce its settle event. On an idle session the CLI's answer
    to the polite request IS that event (no turn is left to emit a ResultMessage), so the next press starts polite
    again; before, the rung stayed raised and a second Ctrl+C in an idle composer sent SIGINT, which ends the CLI and
    its background tasks, and a third sent SIGKILL. A turn in flight keeps its rung for its own ResultMessage. A real
    backend over a hermetic state root, a real session, a real event loop running the requests; the signal rung is
    recorded instead of sent."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")   # nothing here means to start a real session host
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.lines = []
        self.be._log_cb = self.lines.append
        sb.write_reg(Path(self.d), IDLE_SID, {"sid": IDLE_SID, "name": "web", "cwd": self.d, "alive": True,
                                              "lastSid": IDLE_SID, "effort": "high"})
        s = self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), IDLE_SID))
        self.be.sessions[IDLE_SID] = s
        s.inflight = 0
        self.signals = []
        s._signal_cli = lambda sig, action: self.signals.append((action, sig))
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="intr-test-loop", daemon=True)
        self.thread.start()
        s.loop = self.loop
        s.client = _AnsweringControl()
        self.addCleanup(self._close_loop)

    def _close_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=10)
        self.loop.close()

    def _answered(self, n):
        """Wait until n requests have been answered and the loop step that took each answer has finished."""
        deadline = time.monotonic() + 10
        while self.s.client.answered < n:
            if time.monotonic() > deadline:
                self.fail("the control request was never answered; log %r" % self.lines[-6:])
            time.sleep(0.01)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), self.loop).result(timeout=10)

    def test_repeated_presses_on_an_idle_session_each_send_the_polite_request(self):
        actions = []
        for n in (1, 2, 3):
            actions.append(self.s.interrupt())
            if actions[-1] != "sent":
                break                                   # a signal rung sends no request to wait on
            self._answered(n)
        self.assertEqual(actions, ["sent", "sent", "sent"])
        self.assertEqual((self.signals, self.s.client.requests, self.s._intr_level), ([], 3, 0))

    def test_the_kernels_door_pressed_twice_on_an_idle_session_never_signals(self):
        # SdkBackend.interrupt is what the WS interrupt op and POST /interrupt call, for every press
        self.assertTrue(self.be.interrupt(IDLE_SID))
        self._answered(1)
        self.assertTrue(self.be.interrupt(IDLE_SID))
        self.assertEqual(self.signals, [], "the second press on an idle session signalled its CLI")
        self._answered(2)
        self.assertEqual(self.s.client.requests, 2)

    def test_a_turn_in_flight_keeps_its_rung_when_its_request_is_answered(self):
        # the design kept: a turn that never results after an answered request is SIGINTed on the next press
        self.s.inflight = 1
        self.assertEqual(self.s.interrupt(), "sent")
        self._answered(1)
        self.assertEqual(self.s.interrupt(), "sigint")
        self.assertEqual(self.signals, [("sigint", signal.SIGINT)])

    def test_a_turn_the_cli_is_working_keeps_its_rung(self):
        self.s._cli_working = True
        self.assertEqual(self.s.interrupt(), "sent")
        self._answered(1)
        self.assertEqual(self.s.interrupt(), "sigint")

    def test_a_press_that_signalled_before_the_answer_keeps_its_rung(self):
        # the second press came while the first request still waited, so it already signalled: the late answer
        # does not wind the episode back, and the next press kills
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.s.client = _AnsweringControl(gate)
        self.assertEqual(self.s.interrupt(), "sent")
        self.assertEqual(self.s.interrupt(), "sigint")
        gate.set()
        self._answered(1)
        self.assertEqual(self.s._intr_level, 2)
        self.assertEqual(self.s.interrupt(), "sigkill")


class FindSessionCli(unittest.TestCase):
    """The escalation may only signal our own child resuming our sid."""

    def test_matches_our_child_resuming_our_sid(self):
        lines = [ps_line(100, KERNEL)]
        self.assertEqual(sb.find_session_cli(lines, [SID], KERNEL), 100)

    def test_matches_the_lastsid_fork_twin(self):
        lines = [ps_line(101, KERNEL, sid=FORK)]
        self.assertEqual(sb.find_session_cli(lines, [SID, FORK], KERNEL), 101)

    def test_rejects_orphans_other_parents_other_sids_and_tmux(self):
        lines = [ps_line(200, 1),                             # orphan — the reaper's territory
                 ps_line(201, 7777),                          # another kernel's child
                 ps_line(202, KERNEL, sid=FORK),              # our child, someone else's sid
                 ps_line(203, KERNEL, mark=False)]            # tmux-style CLI: no stream-json mark
        self.assertIsNone(sb.find_session_cli(lines, [SID], KERNEL))

    def test_junk_lines_are_skipped(self):
        self.assertIsNone(sb.find_session_cli(["", "not a ps line", "x y"], [SID], KERNEL))

    def test_equals_flag_spelling_matches(self):
        # The SDK's current argv spells it `--resume=<sid>`; the space-only match left the
        # escalation unable to find its own child to signal (2026-07-25).
        lines = ["  100 %d /x/claude --resume=%s --input-format stream-json" % (KERNEL, SID)]
        self.assertEqual(sb.find_session_cli(lines, [SID], KERNEL), 100)


class SourcePins(unittest.TestCase):
    """Wiring pins: the press routes through the ladder, failures are loud, episodes reset."""

    def setUp(self):
        self.src = open(os.path.join(BIN, "romp_sdk_backend.py")).read()

    def test_interrupt_routes_through_the_ladder(self):
        self.assertIn("interrupt_action(self._intr_level, bool(self.loop and self.client))", self.src)

    def test_failed_control_request_is_logged_and_auto_escalates_inflight(self):
        self.assertIn("control request failed", self.src)
        self.assertIn('self._intr_level = max(self._intr_level, 2)', self.src)
        self.assertNotIn("await self.client.interrupt()\n        except Exception:\n            pass",
                         self.src, "the silent swallow must not return")

    def test_episode_resets_on_settle_fresh_turn_and_reconnect(self):
        self.assertGreaterEqual(self.src.count("self._intr_level = 0"), 4,
                                "init + fresh turn + reconnect settle + ResultMessage")

    def test_signal_path_is_loud_when_there_is_nothing_to_signal(self):
        self.assertIn("nothing to signal", self.src)

    def test_fresh_cli_spawn_stamps_and_heals(self):
        # the block stamps the epoch it is handed with the CLI's identity (a host's spawn time at the hello; now for a kernel
        # child at the connect, 2026-09-14), and heals the stale awaiting
        self.assertIn('spawnedAt=int(spawned_at), spawnedAtCli=str(cli_ident or "")', self.src)
        self.assertIn('self._fresh_cli_stamp(int(time.time()), "", mark_echoes=not deliberate)', self.src)
        self.assertIn("self.backend._heal_stale_awaiting(self.sid)", self.src)


if __name__ == "__main__":
    unittest.main()
