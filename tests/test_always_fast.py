#!/usr/bin/env python3
"""Always fast (the user 2026-09-17): a kernel-side switch (Settings, Automation, Model; STATE/always-fast, "on"/"off") that runs
every session in Claude Code's fast mode whenever its model can — the Opus family. The SDK backend reads the store by path
at connect and on a model change (fast_effective), arms only the fastMode flag-settings opt-in (never the literal '/fast
on', which on a non-Opus session makes the CLI switch model), respects a session the user put on Slow (the reg's fastOff)
and a refusal the CLI answered with a reason (liveFastReason), and asks for a reconnect when a model that can run fast
arrives on a connection made without the flag. Hermetic state, synthetic sids, no CLI."""
import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000701"


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    return be


def _switch(be, name, value):
    Path(be.state_dir, name).write_text(value)


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[r["sid"]] = s
    return s


class TheStore(unittest.TestCase):
    def test_off_unless_the_file_says_on(self):
        d = tempfile.mkdtemp()
        self.assertFalse(sb.always_fast_on(d), "absent reads off: the opt-in must be provable")
        Path(d, sb.ALWAYS_FAST_STORE).write_text("off\n")
        self.assertFalse(sb.always_fast_on(d))
        Path(d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        self.assertTrue(sb.always_fast_on(d), "the kernel's bare store, stripped")
        self.assertFalse(sb.always_fast_on(None), "no state dir (a test double's backend) is off, never a raise")
        self.assertFalse(sb.retry_upgrade_on(d), "the sibling store is its own file")

    def test_fast_capable_is_the_opus_family_in_any_spelling(self):
        for m in ("opus", "Opus 5", "claude-opus-5", "us.anthropic.claude-opus-4-8[1m]"):
            self.assertTrue(sb.fast_capable(m), m)
        for m in ("fable", "Fable 5.1", "claude-fable-5-1", "sonnet", "", None, "default"):
            self.assertFalse(sb.fast_capable(m), repr(m))


class FastEffective(unittest.TestCase):
    def test_the_switch_arms_the_flag_for_an_opus_session_and_nothing_else(self):
        be = _backend()
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertFalse(s.fast_effective(), "switch off, no ask: no flag")
        _switch(be, sb.ALWAYS_FAST_STORE, "on")
        self.assertTrue(s.fast_effective(), "switch on, the CLI's id is Opus")
        s2 = _sess(be, liveModel="Opus 5")
        self.assertTrue(s2.fast_effective(), "the pretty name alone decides when no id is known")
        s3 = _sess(be, model="opus")
        self.assertTrue(s3.fast_effective(), "before any turn, the pick decides")
        s4 = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", model="fable")
        self.assertFalse(s4.fast_effective(), "Fable cannot run fast mode: the flag stays off (the CLI would report off anyway; the rule arms only where it takes)")
        s6 = _sess(be, liveModel="Sonnet 5", liveModelId="claude-sonnet-5", model="opus")
        self.assertTrue(s6.fast_effective(), "an Opus PICK served a Sonnet fallback: the pick arms the flag (any known face; harmless on the served model, and the snapshot no longer flaps with it)")
        s7 = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5", model="fable")
        self.assertTrue(s7.fast_effective(), "the user's case: Opus served under a Fable pick — the reported id arms it")
        s5 = _sess(be)
        self.assertFalse(s5.fast_effective(), "no model known at all: off, never a guess")

    def test_the_sessions_own_ask_a_slow_pick_and_a_refusal_each_have_their_say(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5", fastOff=True)
        self.assertFalse(s.fast_effective(), "the user put this session on Slow: the switch leaves it alone")
        s = _sess(be, liveModelId="claude-opus-5", liveFastReason="extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "the CLI refused fast mode for this session with a reason: re-arming would loop")
        s = _sess(be, liveModelId="claude-fable-5-1", fast=True)
        self.assertTrue(s.fast_effective(), "the session's own ask (the badge's On) stands whatever the switch or the model, as before")
        s = _sess(be, liveModelId="claude-opus-5", fast=True, fastOff=False)
        _switch(be, sb.ALWAYS_FAST_STORE, "off")
        self.assertTrue(s.fast_effective(), "…and needs no switch")

    def test_the_connect_and_the_snapshot_read_the_one_expression(self):
        # source pins: the flag settings take fast_effective's value (the call, or a name _options binds to it), and the
        # connection's snapshot is that expression or the launch record's flag, which _options sets from the same value
        # (executed: for the flag file and the record, tests/test_fast_mode_token_login.py,
        # Launches.test_the_flag_file_and_the_launch_record_take_the_same_answer_of_the_fast_rule; for the snapshot, a
        # toggle while _options waits on the version read, tests/test_pick_survives_restart.py,
        # AToggleWhileTheLaunchIsPrepared)
        import re
        opts = inspect.getsource(sb.SdkBackend._options)
        flag = re.search(r"\bfast=([\w.]+(?:\(\))?)", opts[opts.index("flag_settings_path("):]).group(1)
        if flag != "sess.fast_effective()":
            self.assertRegex(opts, r"\b%s = sess\.fast_effective\(\)" % re.escape(flag), "the one value reaches the flag settings")
        snap = re.search(r"self\._fast_unlocked = ([^#\n]+)", inspect.getsource(sb.SdkSession._amain)).group(1).strip()
        if snap != "self.fast_effective()":
            self.assertIn('_launch_record or {}).get("fast")', snap, "the snapshot takes the launch record's flag")
            self.assertRegex(opts, r"launch_values\(\), fast=%s\)" % re.escape(flag), "and the record carries the flag settings' value")
        rule_src = inspect.getsource(sb.SdkSession.fast_effective) + inspect.getsource(sb.SdkSession._after_model_change) + inspect.getsource(sb.SdkBackend.apply_model_switches)
        self.assertIn('"/fast ', inspect.getsource(sb.SdkBackend.set_fast), "the literal send, spelled as the toggle spells it (so the check below is not vacuous)")
        self.assertNotIn('"/fast', rule_src, "the rule never sends the literal toggle: on a non-Opus session the CLI answers it by switching model")
        self.assertNotIn(".send(", rule_src)


class SetFastRemembersSlow(unittest.TestCase):
    def test_off_writes_fastOff_and_on_clears_it(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5")
        s._fast_unlocked = True
        sent = []
        be.send = lambda sid, text: (sent.append(text), True)[1]
        be._wake_push = lambda: None
        self.assertTrue(be.set_fast(SID, "off"))
        self.assertEqual(sent, ["/fast off"], "the unlocked connection takes the literal send, as before")
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg["fast"], reg["fastOff"]), (False, True), "the reg records an explicit Slow")
        self.assertTrue(s.fast_off); self.assertFalse(s.fast_effective(), "…so the next connect carries no flag despite the switch")
        self.assertTrue(be.set_fast(SID, "on"))
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual((reg["fast"], reg["fastOff"]), (True, False), "an explicit On lifts the Slow")
        self.assertFalse(s.fast_off); self.assertTrue(s.fast_effective())


class AModelThatCanRunFastArrives(unittest.TestCase):
    def _learn(self, be, s, pm, raw):
        asks = []
        s.request_reconnect = lambda *a, **k: asks.append(a)
        s._learn_model(pm, raw=raw)
        return asks

    def test_a_locked_connection_asks_for_a_reconnect_once_the_switch_wants_the_flag(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1")
        s._fast_unlocked = False
        self.assertEqual(len(self._learn(be, s, "Opus 5", "claude-opus-5")), 1, "Opus arrived on a connection made without the flag, the session is quiet: reconnect now")
        self.assertTrue(any("always fast (web)" in m and "Opus 5" in m for m in be._logs), be._logs)

    def test_nothing_when_the_flag_is_already_there_the_switch_is_off_or_the_session_is_on_slow(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1"); s._fast_unlocked = True
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "the connection already carries the flag")
        be = _backend()
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1")
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "switch off")
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", fastOff=True)
        self.assertEqual(self._learn(be, s, "Opus 5", "claude-opus-5"), [], "the user put this session on Slow")
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        self.assertEqual(self._learn(be, s, "Fable 5.1", "claude-fable-5-1"), [], "a model that cannot run fast: nothing to arm")

    def test_the_context_refresh_runs_the_same_hook(self):
        self.assertIn("self._after_model_change(old, pm)", inspect.getsource(sb.SdkSession._do_refresh_context),
                      "a pick to Opus lands through the control channel's refresh first")
        self.assertIn("self._after_model_change(old, pm)", inspect.getsource(sb.SdkSession._learn_model))


class TheCliRefusesTheRule(unittest.TestCase):
    def test_a_reason_on_a_rule_armed_connection_is_said_once_and_stops_the_re_arm_with_no_reconnect(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5")
        s._fast_unlocked = True                     # the rule armed this connection's flag; no ask of the session's own
        asks = []
        s.request_reconnect = lambda *a, **k: asks.append(a)
        self.assertTrue(s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}))
        said = [m for m in be._logs if m.startswith("always fast (web): the CLI refused fast mode")]
        self.assertEqual(len(said), 1, be._logs)
        self.assertEqual(asks, [], "no reconnect: the ask's refusal path (a toast and a flagless reconnect) is for the user's own ask")
        self.assertEqual(s.fast_reason, "extra_usage_disabled")
        self.assertFalse(s.fast_effective(), "the persisted reason keeps the next connect flagless: no refuse → reconnect → refuse loop")
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual(len([m for m in be._logs if m.startswith("always fast (web): the CLI refused")]), 1, "the same reason again is not said again")
        self.assertIn('refused_ask = bool(reason) and self.fast_opt and fast != "on"', inspect.getsource(sb.SdkSession._adopt_fast_state),
                      "the user's own ask keeps its refusal path unchanged")


class TheAskWaitsForAQuietSession(unittest.TestCase):
    """2026-09-17: the rule's deferred turn's-end reconnect force-killed a CLI whose turn had ended with three subagents and a
    background task still running inside it. A switch's ask now stands until the session is QUIET (no turn in flight or
    queued, no live subagent, no background task) and is carried at the exact event that ends the last of it: the turn's
    result, a subagent's stop, a task's end — with the kernel's tick as the backstop. Never through request_reconnect's
    deferred road (cut=True), a Restart's or a rewind's, which accepts what it cuts; a pick's reconnect waits here too since
    2026-10-01 (tests/test_reconnect_waits_for_work.py)."""
    def _armed(self, **state):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1")
        s._fast_unlocked = False
        for k, v in state.items():
            setattr(s, k, v)
        s._asks = []; s.request_reconnect = lambda *a, **k: s._asks.append((a, k))
        s._learn_model("Opus 5", raw="claude-opus-5")
        return be, s

    def test_a_turn_in_flight_holds_the_ask_and_the_result_carries_it(self):
        be, s = self._armed(inflight=1)
        self.assertEqual(s._asks, [], "mid-turn: no reconnect of any kind")
        self.assertEqual(s._switch_wanted, "always fast", "the ask stands")
        self.assertFalse(s._reconnect_when_idle, "never the deferred turn's-end road: that one cuts live work")
        waits = [m for m in be._logs if "waiting for the session to go quiet" in m]
        self.assertEqual(len(waits), 1, be._logs); self.assertIn("a turn in flight", waits[0]); self.assertIn("nothing is cut", waits[0])
        s._learn_model("Sonnet 5", raw="claude-sonnet-5"); s._learn_model("Opus 5", raw="claude-opus-5")
        self.assertEqual(len([m for m in be._logs if "waiting for the session to go quiet" in m]), 1, "said once per ask")
        s.inflight = 0                       # the settle sets inflight 0, then asks (the elif at the result)
        self.assertTrue(s._try_switch_reconnect())
        self.assertEqual(s._asks, [((), {"defer": False})], "carried as an IMMEDIATE reconnect, whose loop-side re-check stands")
        self.assertEqual(s._switch_wanted, "")
        self.assertTrue(any("the session is quiet — reconnecting now" in m for m in be._logs), be._logs)

    def test_a_live_subagent_holds_the_ask_and_its_stop_hook_carries_it(self):
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"    # re-armed for the walk: the first learn found it quiet and asked
        with s._sub_lock:
            s._subagents["a1"] = {"type": "Task", "since": 1}
        self.assertFalse(s._try_switch_reconnect())
        self.assertEqual(s._asks, [])
        self.assertIn("1 subagent running", [m for m in be._logs if "waiting" in m][-1])
        import asyncio
        asyncio.run(s._subagent_stop_hook({"agent_id": "a1"}, None, None))
        self.assertEqual(len(s._asks), 1, "the last subagent's end is the event that carries the ask")

    @staticmethod
    def _cli_opens_turn(s):
        """The CLI opens a turn of its own (a notification's): its init counts it, as _on_message does at inflight 0."""
        s.inflight = 1
        s._turn_open_mono = time.monotonic()

    @staticmethod
    def _turn_result(s):
        """That turn's result, as the settle runs it: inflight to 0, the notification marks the turn answered lowered,
        then the standing ask tried (the ResultMessage handler's switch arm)."""
        s.inflight = 0
        s._lower_notice_marks()
        return s._try_switch_reconnect()

    def test_a_background_task_holds_the_ask_and_its_notification_turns_result_carries_it(self):
        # 2026-11-25: carried at the task's END, the ask's teardown raced the turn the CLI opens for the task's notification
        # in that same instant; the `end` closed the CLI's stdin, the turn started a new watcher, and the CLI lived until the
        # host's SIGKILL at the 1200 s switch grace. The end raises a "notification turn owed" mark; that turn's result carries
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"
        with s._sub_lock:
            s._bg_tasks["t1"] = {"desc": "x", "type": "local_bash", "since": 1, "toolUseId": "", "lastTool": ""}
        self.assertFalse(s._try_switch_reconnect())
        self.assertIn("1 background task running", [m for m in be._logs if "waiting" in m][-1])
        s._on_task_event("task_notification", {"task_id": "t1", "status": "completed"})
        self.assertEqual(s._asks, [], "the task's end does not carry the ask: the CLI owes its notification a turn")
        self.assertFalse(s.quiet(), "…and nothing reads quiet in the instant before that turn shows")
        s._switch_wait_said = ""
        self.assertFalse(s._try_switch_reconnect())
        self.assertIn("a turn owed for 1 finished background task running", [m for m in be._logs if "waiting" in m][-1])
        self.assertEqual(s.relaunch_waits(), "the turn for 1 finished background task", "the pick's words say what holds it")
        self._cli_opens_turn(s)
        s._on_task_event("task_started", {"task_id": "t2", "task_type": "local_bash"})   # the turn starts a new watcher
        s._cli_working = False
        self.assertFalse(self._turn_result(s), "the notification turn's result: its new task holds the ask, as designed")
        self.assertEqual(s._asks, [])
        s._switch_wait_said = ""; s._try_switch_reconnect()
        self.assertIn("1 background task running", [m for m in be._logs if "waiting" in m][-1])
        s._on_task_event("task_notification", {"task_id": "t2", "status": "completed"})
        self.assertEqual(s._asks, [], "t2's end: its notification turn is owed in turn")
        self._cli_opens_turn(s)
        self.assertTrue(self._turn_result(s), "that turn's result, nothing left running: carried")
        self.assertEqual(len(s._asks), 1)
        self.assertEqual(s._notice_marks, {}, "every mark lowered")

    def test_a_task_that_ends_mid_turn_is_not_answered_by_that_turns_result(self):
        # the running turn's result is not the notification turn: the notification is queued for the turn the CLI opens next
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"
        with s._sub_lock:
            s._bg_tasks["t1"] = {"desc": "x", "type": "local_bash", "since": 1, "toolUseId": "", "lastTool": ""}
        self._cli_opens_turn(s)                         # a turn is running when the task ends
        s._on_task_event("task_notification", {"task_id": "t1", "status": "completed"})
        self.assertFalse(self._turn_result(s), "the turn that was running when the task ended does not lower its mark")
        self.assertEqual(s._asks, [])
        self.assertEqual(list(s._notice_marks), ["t1"])
        self._cli_opens_turn(s)                         # the notification's own turn
        self.assertTrue(self._turn_result(s))
        self.assertEqual(len(s._asks), 1)

    def test_a_subagents_own_task_raises_no_mark_and_its_end_still_carries_the_ask(self):
        # owned_by_subagent: the notification goes to that subagent, so the main thread opens no turn for it
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"
        s._on_task_event("task_started", {"task_id": "t-sub", "task_type": "local_bash", "is_backgrounded": False,
                                          "owned_by_subagent": True})
        self.assertFalse(s._try_switch_reconnect())
        s._on_task_event("task_notification", {"task_id": "t-sub", "status": "completed"})   # no flag on this frame: the start's
        self.assertEqual(s._notice_marks, {}, "no mark for a subagent's own task")
        self.assertEqual(len(s._asks), 1, "its end is what the ask waited for: carried at once")

    def test_the_fallback_lowers_a_mark_no_turn_answered_and_the_tick_carries_the_ask(self):
        # a notification the CLI folded into a running turn, or answered with no turn: NOTICE_TURN_WAIT_S of nothing (no turn
        # open, no result, no work atom) lowers the mark, read by quiet() on the kernel's 30 s tick
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"
        with s._sub_lock:
            s._bg_tasks["t1"] = {"desc": "x", "type": "local_bash", "since": 1, "toolUseId": "", "lastTool": ""}
        s._on_task_event("task_notification", {"task_id": "t1", "status": "completed"})
        be.retry_model_upgrades(time.time())
        self.assertEqual(s._asks, [], "the tick inside the bound: the mark still stands")
        with s._sub_lock:                               # NOTICE_TURN_WAIT_S pass with nothing from the CLI
            s._notice_marks["t1"] -= sb.NOTICE_TURN_WAIT_S + 1
        s._notice_activity = time.monotonic() - sb.NOTICE_TURN_WAIT_S / 2
        be.retry_model_upgrades(time.time())
        self.assertEqual(s._asks, [], "a work atom inside the bound restarts the count")
        s._notice_activity = time.monotonic() - sb.NOTICE_TURN_WAIT_S - 1
        be.retry_model_upgrades(time.time())
        self.assertEqual(len(s._asks), 1, "the tick past the bound: the mark is lowered and the ask carried")
        self.assertEqual(s._notice_marks, {})
        said = [m for m in be._logs if "no turn came for" in m]
        self.assertEqual(said, ["background tasks (web): no turn came for 1 finished background task within %.0f s; a "
                                "relaunch no longer waits for one" % sb.NOTICE_TURN_WAIT_S], be._logs)

    def test_an_ask_on_a_truly_quiet_session_is_carried_at_once(self):
        # the road every quiet session takes (no task ended, or its notification turn long answered): unchanged
        be, s = self._armed()
        self.assertEqual(len(s._asks), 1, "the arming learn found the session quiet: carried at once")
        s._asks.clear(); s._switch_wanted = "always fast"
        with s._sub_lock:
            s._bg_tasks["t1"] = {"desc": "x", "type": "local_bash", "since": 1, "toolUseId": "", "lastTool": ""}
        s._on_task_event("task_notification", {"task_id": "t1", "status": "completed"})
        self._cli_opens_turn(s)
        self.assertTrue(self._turn_result(s))
        s._asks.clear(); s._switch_wanted = "always fast"
        self.assertTrue(s._try_switch_reconnect(), "a later ask: no mark lingers, quiet at once")
        self.assertEqual(len(s._asks), 1)

    def test_the_wakers_last_look_stands_down_while_a_notification_turn_is_owed(self):
        # a task's end landing between a carried ask and its teardown: the last look reads the mark and stands down
        import types
        be, s = self._armed()
        s._host = types.SimpleNamespace(end_grace=120.0)
        s._reconnect = True; s._reconnect_switch_why = "always fast"; s._switch_wanted = ""
        s._on_task_event("task_notification", {"task_id": "t-unseen", "status": "completed"})   # a task this kernel never saw start
        self.assertTrue(s._switch_teardown_check(), "vetoed: the CLI is opening the notification's turn")
        self.assertEqual((s._reconnect, s._switch_wanted, s._host.end_grace), (False, "always fast", 120.0))
        self.assertIn("the CLI started work between the ask and the reconnect — a turn owed for 1 finished background task; "
                      "the reconnect stands down", be._logs[-1])

    def test_a_teardown_drops_the_marks_with_the_cli_that_owed_them(self):
        be, s = self._armed()
        with s._sub_lock:
            s._bg_tasks["t1"] = {"desc": "x", "type": "local_bash", "since": 1, "toolUseId": "", "lastTool": ""}
        s._on_task_event("task_notification", {"task_id": "t1", "status": "completed"})
        self.assertTrue(s._notice_marks)
        s._drop_live_work("reconnect")
        self.assertEqual(s._notice_marks, {}, "a fresh CLI owes no notification turn")
        self.assertTrue(s.quiet())

    def test_a_queued_turn_holds_it_and_a_reconnect_already_on_its_way_stands_it_down(self):
        be, s = self._armed(_pending=["hi"])
        self.assertEqual(s._asks, []); self.assertIn("a queued turn", [m for m in be._logs if "waiting" in m][-1])
        s._reconnect_when_idle = True        # a Restart meanwhile: its reconnect carries the flag
        self.assertFalse(s._try_switch_reconnect())
        self.assertEqual((s._switch_wanted, s._asks), ("", []), "stood down to the reconnect on its way")

    def test_the_tick_is_the_backstop_and_a_dropped_immediate_reconnect_re_raises_the_ask(self):
        be, s = self._armed(inflight=1)
        self.assertEqual(be.retry_model_upgrades(time.time()), 0, "the retry switch is off: no attempt; the standing ask is still nudged")
        self.assertEqual(s._asks, [], "…and still busy: nothing")
        s.inflight = 0
        be.retry_model_upgrades(time.time())
        self.assertEqual(len(s._asks), 1, "quiet at the tick: carried")
        # the loop-side re-check of the immediate form dropped it (work registered meanwhile): the ask stands again
        s._switch_ask_pending = "always fast"; s._switch_wanted = ""
        s._re_raise_switch_ask()
        self.assertEqual((s._switch_wanted, s._switch_ask_pending), ("always fast", ""))

    def test_a_permission_or_picker_ask_waiting_on_the_user_holds_the_ask(self):
        # audit 2026-09-17: a turn parked on an ask has no feed in flight and the CLI is not producing, so it read as quiet;
        # a switch reconnect then cancelled the ask and the tool call with it
        be, s = self._armed()
        s._asks.clear(); s._switch_wanted = "always fast"
        be._pending_ask[s.sid] = {"kind": "permission"}
        self.assertFalse(s.quiet()); self.assertFalse(s._try_switch_reconnect())
        self.assertEqual(s._asks, [])
        self.assertIn("a question waiting on you", [m for m in be._logs if "waiting" in m][-1])
        be._pending_ask.pop(s.sid, None)
        self.assertTrue(s._try_switch_reconnect(), "the ask answered: quiet")

    def test_a_turn_the_cli_opened_itself_holds_the_ask(self):
        # 2026-09-17: inflight counts FED turns only; a background task's notification opens a turn the feeder never saw,
        # so three sessions read as quiet while running dozens of tool calls a minute and the host's end grace killed them
        be, s = self._armed(_cli_working=True)
        self.assertEqual(s._asks, [], "the CLI is producing on a turn of its own: no reconnect")
        self.assertIn("a turn the CLI opened itself", [m for m in be._logs if "waiting" in m][-1])
        s._cli_working = False           # the Result's settle marks 'waiting' (the settle then tries the ask)
        self.assertTrue(s._try_switch_reconnect())
        self.assertEqual(len(s._asks), 1)

    def test_the_immediate_re_check_refuses_the_clis_own_turn_and_re_raises_the_ask(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        s._wake_set = lambda: None
        s._switch_ask_pending = "retry upgrade"; s._cli_working = True
        s._do_request_reconnect(defer=False)
        self.assertFalse(s._reconnect, "not armed: the CLI is at work")
        self.assertEqual((s._switch_wanted, s._switch_ask_pending), ("retry upgrade", ""), "the ask stands again for the next quiet moment")
        self.assertTrue(any("not reconnected" in m for m in be._logs), be._logs)
        s._cli_working = False; s._switch_wanted = ""; s._switch_ask_pending = "retry upgrade"
        s._do_request_reconnect(defer=False)
        self.assertTrue(s._reconnect, "quiet: armed")
        self.assertEqual((s._reconnect_switch_why, s._switch_ask_pending), ("retry upgrade", ""), "the armed reconnect remembers it is a switch's")

    def test_the_wakers_last_look_stands_a_switch_reconnect_down_or_lengthens_the_end_grace(self):
        import types
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        s._host = types.SimpleNamespace(end_grace=120.0)
        # the CLI started work between the arm and the teardown: vetoed, the ask stands, the client stays
        s._reconnect = True; s._reconnect_switch_why = "always fast"; s._cli_working = True
        self.assertTrue(s._switch_teardown_check())
        self.assertEqual((s._reconnect, s._reconnect_switch_why, s._switch_wanted), (False, "", "always fast"))
        self.assertEqual(s._host.end_grace, 120.0, "no teardown: the grace is untouched")
        self.assertTrue(any("stands down" in m and "a turn the CLI opened itself" in m for m in be._logs), be._logs)
        # quiet: proceeding, with the long grace so a turn that starts in the last instant is finished, not killed
        s._reconnect = True; s._reconnect_switch_why = "retry upgrade"; s._cli_working = False; s._switch_wanted = ""
        self.assertFalse(s._switch_teardown_check())
        self.assertEqual(s._host.end_grace, sb.SWITCH_END_GRACE_S)
        self.assertTrue(s._reconnect)
        # …and bounded (2026-11-25): a CLI still alive a few seconds after a turn's result past the `end` is held by background
        # work, and the host transport ends it with SIGTERM then, not at the 1200 s grace (HostTransport.term_after_result)
        self.assertEqual(s._host.term_after_result, sb.SWITCH_POST_RESULT_TERM_S)
        self.assertLess(sb.SWITCH_POST_RESULT_TERM_S, 30, "a few seconds, not a grace")
        s._host.on_post_end_term(None)
        self.assertEqual([m for m in be._logs if "SIGTERM" in m][-1],
                         "retry upgrade (web): the CLI ran a turn after its input was closed and was still running 5 s after "
                         "that turn's result (background work it started holds it); asked the session host to end it with "
                         "SIGTERM instead of waiting out the 1200 s end grace")
        s._host.on_post_end_term(OSError("socket closed"))
        self.assertEqual([m for m in be._logs if "SIGTERM" in m][-1],
                         "retry upgrade (web): could not ask the session host to end the CLI with SIGTERM after its post-close "
                         "turn (OSError: socket closed); the end grace ends it")
        # a user's own reconnect (no switch why) is never vetoed nor re-graced
        s._host = types.SimpleNamespace(end_grace=120.0)
        s._reconnect_switch_why = ""; s._cli_working = True
        self.assertFalse(s._switch_teardown_check())
        self.assertEqual((s._reconnect, s._host.end_grace), (True, 120.0))
        self.assertFalse(hasattr(s._host, "term_after_result"), "…nor bounded: that road accepts what it cuts, as before")


class TheCliSaysWhichConnectionsHaveNoFlag(unittest.TestCase):
    def test_sdk_opt_in_required_reads_the_flag_back_as_absent_and_asks_the_rule_again(self):
        # An attach to a CLI that survived a kernel restart snapshots _fast_unlocked from the rule's answer, not the spawn's
        # fact; the CLI's sdk_opt_in_required is the fact (2026-09-17)
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")
        s._fast_unlocked = True
        s._asks = []; s.request_reconnect = lambda *a, **k: s._asks.append(a)
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
        self.assertFalse(s._fast_unlocked, "the CLI's own word: this connection was made without the flag")
        self.assertEqual(len(s._asks), 1, "…so the rule asks for the flag from the truth (quiet: now)")
        self.assertEqual(s.fast_reason, "", "the opt-in reason stays blanked for the badge, as before")
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual(len(s._asks), 1, "a flagged connection's reports move nothing")


if __name__ == "__main__":
    unittest.main()


class APickToOpusLandsThroughTheRefresh(unittest.TestCase):
    def test_the_control_channels_refresh_learns_the_id_before_the_hook_reads_it(self):
        # review 2026-09-17: the hook ran before the id was stored, so a session picked onto Opus stayed slow for good
        import asyncio
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", model="opus")
        s.model, s._model_id = "Fable 5.1", "claude-fable-5-1"; s.chosen_model = ""   # the id and name the CLI last reported; no pick face to lean on
        s._fast_unlocked = False
        asks = []
        s.request_reconnect = lambda *a, **k: asks.append(a)

        class Client:
            async def get_context_usage(self):
                return {"model": "claude-opus-5", "percentage": 12}
        s.client = Client()
        asyncio.run(s._do_refresh_context())
        self.assertEqual((s.model, s._model_id), ("Opus 5", "claude-opus-5"))
        self.assertEqual(len(asks), 1, "the reconnect that arms the flag")


class TheUsersOwnAskIsNotTheSwitchs(unittest.TestCase):
    def test_a_refused_ask_rings_only_its_own_line_when_the_switch_is_off(self):
        be = _backend()   # switch OFF
        s = _sess(be, liveModelId="claude-opus-5", fast=True)
        s._fast_unlocked = True
        s.request_reconnect = lambda *a, **k: None
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual([m for m in be._logs if m.startswith("always fast")], [], "the switch is off: the refusal is the ask's alone")
        self.assertTrue(any(m.startswith("fast mode (web): the CLI refused the toggle") for m in be._logs), be._logs)
        self.assertEqual(s.fast_rule_refused, "", "…and the switch's memory stays empty")

    def test_the_switchs_memory_survives_a_flagless_connect_and_lifts_on_a_reported_on_or_the_users_gesture(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModelId="claude-opus-5")
        s._fast_unlocked = True; s.request_reconnect = lambda *a, **k: None
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual(s.fast_rule_refused, "extra_usage_disabled"); self.assertFalse(s.fast_effective())
        self.assertEqual(sb.read_reg(be.state_dir, SID)["fastRuleRefused"], "extra_usage_disabled", "persisted: a restart remembers")
        # the next connect is flagless and reports the opt-in reason, which fast_reason blanks — the memory is not in fast_reason
        s._fast_unlocked = False
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"})
        self.assertEqual(s.fast_reason, "", "the pre-existing blanking"); self.assertFalse(s.fast_effective(), "…and still no flag: no refuse → reconnect → refuse, however stretched")
        self.assertEqual(len([m for m in be._logs if m.startswith("always fast (web): the CLI refused")]), 1, "said once")
        # the CLI reporting fast ON lifts it
        s._fast_unlocked = True
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual(s.fast_rule_refused, ""); self.assertTrue(s.fast_effective())
        # …and so does the user's own gesture on the session
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertTrue(s.fast_rule_refused)
        be.send = lambda sid, text: True; be._wake_push = lambda: None
        be.set_fast(SID, "on")
        self.assertEqual((s.fast_rule_refused, sb.read_reg(be.state_dir, SID)["fastRuleRefused"]), ("", ""))


class NoFlapWhenTheServedModelDiffersFromThePick(unittest.TestCase):
    def test_one_ask_per_connection_and_the_pick_keeps_the_flag_across_a_served_fallback(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5", model="opus")
        s._fast_unlocked = False
        asks = []
        def ask(*a, **k): asks.append(a); s._reconnect_when_idle = True   # what request_reconnect does mid-turn
        s.request_reconnect = ask
        s._learn_model("Sonnet 5", raw="claude-sonnet-5", served=True)   # the API served a fallback below the pick
        self.assertEqual(len(asks), 1, "flagless connection, the pick is Opus: one ask")
        s._learn_model("Opus 5", raw="claude-opus-5")                    # the next init reports the configured model again
        s._learn_model("Sonnet 5", raw="claude-sonnet-5", served=True)
        self.assertEqual(len(asks), 1, "a reconnect already requested is not asked for again")
        s._reconnect_when_idle = False; s._fast_unlocked = True          # the reconnect happened, flag on (the pick decided)
        s._learn_model("Opus 5", raw="claude-opus-5")
        s._learn_model("Sonnet 5", raw="claude-sonnet-5", served=True)
        self.assertEqual(len(asks), 1, "flagged: nothing more, whatever the served model does")


class TheSwitchReachesRunningSessions(unittest.TestCase):
    def test_apply_model_switches_reconnects_the_sessions_whose_flag_should_change_and_arms_the_fallen(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        opus = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5"); opus._fast_unlocked = False
        fable = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", sid="11111111-2222-4333-8444-000000000702")
        own = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5", fast=True, sid="11111111-2222-4333-8444-000000000703"); own._fast_unlocked = True
        for x in (opus, fable, own):
            x._asks = []; x.request_reconnect = (lambda self_: (lambda *a, **k: self_._asks.append(a)))(x)
        self.assertEqual(be.apply_model_switches(), 1)
        self.assertEqual((len(opus._asks), len(fable._asks), len(own._asks)), (1, 0, 0), "the idle Opus session gets the flag; Fable has nothing to arm; the session's own ask already has it")
        _switch(be, sb.ALWAYS_FAST_STORE, "off")
        opus._fast_unlocked = True
        self.assertEqual(be.apply_model_switches(), 1)
        self.assertEqual((len(opus._asks), len(own._asks)), (2, 0), "off: the session the switch flagged drops it; the session's own ask keeps it")

    def test_fork_carries_an_explicit_slow_and_a_parents(self):
        src = inspect.getsource(sb.SdkBackend.fork)
        self.assertIn('if fast == "off" or (not fast and parent.get("fastOff")):', src)
        self.assertIn('reg["fastOff"] = True', src)


class _UserMessage:
    """The SDK's UserMessage by name (the kernel duck-types the stream's classes): content, uuid, no subagent tag."""
    def __init__(self, content, uuid="u-echo", parent_tool_use_id=None):
        self.content, self.uuid, self.parent_tool_use_id = content, uuid, parent_tool_use_id
        self.tool_use_result, self.origin = None, None
class _AssistantMessage:
    pass
class _ResultMessage:
    pass
class _SystemMessage:
    def __init__(self, subtype="init", data=None):
        self.subtype, self.data = subtype, (data or {})
_UserMessage.__name__ = "UserMessage"; _AssistantMessage.__name__ = "AssistantMessage"
_ResultMessage.__name__ = "ResultMessage"; _SystemMessage.__name__ = "SystemMessage"
# the line the CLI streams for a set_model control request (its own fixed wording, not session text)
SWITCH_ECHO = "<local-command-stdout>Set model to `%s`</local-command-stdout>"


class AModelSwitchOnAnIdleCliOpensNoTurn(unittest.TestCase):
    """2026-10-09: sessions whose model was switched in place on an idle CLI read as a turn in flight for good. The CLI
    answers set_model by streaming its command output as a user record (SWITCH_ECHO, flagged isReplay), with no init before
    it and no result after it; the count of turns the CLI opens itself (_on_message, by _turn_frame) took that record for
    such a turn, and nothing ever settled it. The card read Working, and Always fast's reconnect, which waits for a quiet
    session, waited at every event and every tick for a result that was never coming. Synthetic session, no CLI."""

    def _switched(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "on")
        s = _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", model="claude-fable-5-1")
        s.model, s._model_id, s._fast_unlocked = "Fable 5.1", "claude-fable-5-1", False
        s._asks = []; s.request_reconnect = lambda *a, **k: s._asks.append((a, k))

        class Client:   # the CLI's answer, in its order: the echo streams first, then set_model's control response
            async def set_model(self, model):
                s._on_message(_UserMessage(SWITCH_ECHO % model), _AssistantMessage, _ResultMessage, _SystemMessage)

            async def get_context_usage(self):
                return {"model": "claude-opus-5-5", "percentage": 3}
        s.client = Client()
        be.set_model(SID, "claude-opus-5-5")   # no loop here, so the live request is run by hand below
        import asyncio
        asyncio.run(s._do_set_model("claude-opus-5-5", {"picked": "claude-opus-5-5", "tok": None,
                                                        "seq": s._pick_seq, "chain": ()}))
        return be, s

    def test_the_clis_echo_of_the_switch_opens_no_turn_and_always_fast_reconnects_at_once(self):
        be, s = self._switched()
        self.assertEqual((s.model, s._model_id), ("Opus 5.5", "claude-opus-5-5"), "the switch landed")
        self.assertEqual(s.inflight, 0, "the echo is no turn: nothing will ever settle a count it raises")
        self.assertTrue(s.quiet())
        self.assertEqual(s.live_state()[0], "waiting", "an idle session does not read as Working")
        self.assertEqual(s._asks, [((), {"defer": False})], "Always fast's reconnect is carried at once, the session being quiet")
        self.assertFalse([m for m in be._logs if "waiting for the session to go quiet" in m], be._logs)

    def test_the_turn_frames_are_unchanged_but_for_the_echo(self):
        tf = lambda m: sb.SdkSession._turn_frame(m, _AssistantMessage, _ResultMessage, _SystemMessage)
        self.assertFalse(tf(_UserMessage(SWITCH_ECHO % "claude-opus-5-5")), "a control request's command output")
        self.assertFalse(tf(_UserMessage([{"type": "text", "text": SWITCH_ECHO % "fable"}])), "the same line as a text block")
        self.assertTrue(tf(_UserMessage("a turn the CLI runs for a notification")), "the CLI's own user record still counts")
        self.assertTrue(tf(_SystemMessage("init")), "a turn's init still counts")
        self.assertTrue(tf(_AssistantMessage()))
        self.assertFalse(tf(_UserMessage("x", parent_tool_use_id="toolu_1")), "a subagent's record still does not")


LACKS = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}


class TheClisWordAtEveryConnectAsksForTheOptIn(unittest.TestCase):
    """2026-10-09: a kernel restart lost the ask for the fast-mode opt-in, and sessions ran at normal speed until someone
    restarted them by hand. Two roads led there. Always fast's ask lives in one kernel's memory (a model switch raised it),
    and the kernel that re-attached to the surviving CLI saw no model change, so nothing asked again. And a session's own
    fast pick, recorded as waiting for its relaunch, read as applied at the attach because the CLI's launch record carries
    the opt-in, although a literal '/fast off' sent to that CLI earlier had taken the opt-in away (the CLI then reports
    sdk_opt_in_required for the rest of its life; seen on the CLI 2.1.282). The CLI says which of its connections lack the
    opt-in in the initialize answer every connect gets, an attach included, and in every turn's init: that word, read
    against what a launch from the session's state would carry (fast_effective), is what asks for the relaunch, carried
    when the session is quiet. Synthetic sessions, no CLI."""

    def _attached(self, launch_fast, **reg):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, reg.pop("switch", "on"))
        s = _sess(be, **dict({"liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5", "model": "claude-opus-5-5",
                              "liveFast": "off"}, **reg))
        s._host_is_attach = True                   # an earlier kernel launched the CLI this connect reached
        s._fast_unlocked = bool(launch_fast)       # the connect's reading of that CLI's launch record (cli.launch)
        s._asks = []; s.request_reconnect = lambda *a, **k: s._asks.append((a, k))
        return be, s

    def test_always_fast_asks_at_an_attach_to_a_cli_launched_without_the_opt_in(self):
        be, s = self._attached(launch_fast=False)
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [((), {"defer": False})], "quiet: the relaunch that carries the opt-in, now")
        self.assertTrue(any(m.startswith("always fast (web):") and "opt-in" in m for m in be._logs), be._logs)

    def test_the_sessions_own_pick_asks_when_its_cli_lost_the_opt_in(self):
        for switch in ("on", "off"):
            be, s = self._attached(launch_fast=True, fast=True, switch=switch)
            s._adopt_fast_state(LACKS)
            self.assertEqual(len(s._asks), 1, "the launch record says the flag rode the launch; the CLI says it has none "
                                              "now: the CLI's word wins (switch %s)" % switch)
            self.assertEqual(s._fast_pending, "on", "the pick's dots until a launch carries it")
            self.assertTrue(any(m.startswith("fast mode (web):") and "opt-in" in m for m in be._logs), be._logs)

    def test_the_incident_a_waiting_pick_read_as_applied_at_the_attach_then_the_cli_says_otherwise(self):
        be, s = self._attached(launch_fast=True, fast=True, fastOff=False)
        sb.write_reg(be.state_dir, SID, dict(sb.read_reg(be.state_dir, SID),
                                             relaunchWait={"why": "fast mode", "t": 1, "picks": {"fast": {"fast": True}}}))
        s._fast_pending = "on"
        launch = dict(s.launch_values())           # an earlier kernel's launch record, carrying the opt-in
        s._reconcile_waiting_picks(launch, launched_here=False, spawned_at=1)
        self.assertEqual(s._fast_pending, "", "the record carries the pick, so the reconcile reads it as applied")
        s._adopt_fast_state(LACKS)                 # the attach's initialize answer
        self.assertEqual(s._asks, [((), {"defer": False})], "the CLI's word re-raises the ask; quiet, so it is carried now")

    def test_a_busy_session_keeps_the_ask_standing_and_nothing_is_cut(self):
        be, s = self._attached(launch_fast=False)
        s.inflight = 1
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [], "mid-turn: no reconnect")
        self.assertEqual(s._switch_wanted, "always fast")
        s._adopt_fast_state(LACKS)                 # the next init says the same: one ask, not two
        self.assertEqual(s._switch_wanted, "always fast")
        self.assertEqual(len([m for m in be._logs if m.startswith("always fast (web):") and "opt-in" in m]), 1, be._logs)

    def test_no_ask_when_the_cli_has_the_opt_in_or_nothing_wants_it(self):
        be, s = self._attached(launch_fast=False)
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual(s._asks, [], "the CLI reports fast on: nothing to relaunch for")
        for reg, why in (({"fastOff": True}, "the user put this session on Slow"),
                         ({"switch": "off"}, "the switch is off and the session has no ask of its own"),
                         ({"fastRuleRefused": "extra_usage_disabled"}, "the CLI refused the switch's flag: no refuse loop"),
                         ({"liveFastReason": "extra_usage_disabled"}, "a refusal stands for this session"),
                         ({"liveModel": "Fable 5.1", "liveModelId": "claude-fable-5-1", "model": "claude-fable-5-1"},
                          "a model that cannot run fast")):
            be, s = self._attached(launch_fast=False, **reg)
            s._adopt_fast_state(LACKS)
            self.assertEqual(s._asks, [], why)
            s._adopt_fast_state(LACKS)             # the next turn's init says the same (2026-10-09, the review of #197)
            self.assertEqual(s._asks, [], "%s, at the second report too: the first one's blanked reason must not "
                                          "erase what held it back" % why)

    def test_a_cli_launched_here_with_the_opt_in_that_still_lacks_it_is_said_once_and_never_relaunched(self):
        be, s = self._attached(launch_fast=True)
        s._host_is_attach = False                  # THIS kernel launched it, carrying the flag
        s._launch_record = dict(s.launch_values())
        self.assertTrue(s._launch_record["fast"])
        s._adopt_fast_state(LACKS)
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [], "a relaunch would carry the same flag: relaunch → lacks → relaunch is a loop")
        said = [m for m in be._logs if "opt-in" in m and "launched" in m]
        self.assertEqual(len(said), 1, be._logs)

    def test_a_literal_slow_then_on_on_the_same_cli_relaunches_it(self):
        # '/fast off' on a connection made with the flag takes the CLI's opt-in away, so the user's next On is refused by
        # that CLI; its next report says it lacks the opt-in, and only a relaunch can carry it again
        be, s = self._attached(launch_fast=True, fast=True)
        s._host_is_attach = False
        s._launch_record = dict(s.launch_values())
        be.send = lambda sid, text: True; be._wake_push = lambda: None
        be.set_fast(SID, "off")
        be.set_fast(SID, "on")                     # still unlocked as far as romp knows: the literal send
        s._adopt_fast_state(LACKS)
        self.assertEqual(len(s._asks), 1, "the CLI's own word after the literal Slow: relaunch to carry the opt-in again")


class TheRegistryFollowsTheClisWord(unittest.TestCase):
    """2026-10-09: a relaunch that carried a waiting fast pick flipped the in-memory fast state to on (the reconcile's
    optimistic flip) without the registry, so the CLI's own "on" changed nothing in memory and was never written: the
    registry kept "off" for a session running fast, and a restarted kernel and every reader of the registry believed it."""

    def test_the_clis_word_is_written_when_the_registry_differs_though_memory_already_agrees(self):
        be = _backend(); _switch(be, sb.ALWAYS_FAST_STORE, "off")
        s = _sess(be, liveModelId="claude-opus-5-5", model="claude-opus-5-5", fast=True, liveFast="off",
                  relaunchWait={"why": "fast mode", "t": 1, "picks": {"fast": {"fast": True}}})
        s._fast_pending = "on"
        s._reconcile_waiting_picks(dict(s.launch_values()), launched_here=True)
        self.assertEqual(s.fast, "on", "the reconcile's flip (memory only)")
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual(sb.read_reg(be.state_dir, SID)["liveFast"], "on")

    def test_an_unchanged_word_the_registry_already_has_writes_nothing(self):
        be = _backend()
        s = _sess(be, liveModelId="claude-opus-5-5", liveFast="on", liveFastReason="")
        writes = []
        real = be._update_reg
        be._update_reg = lambda sid, **kw: (writes.append(kw), real(sid, **kw))
        s._adopt_fast_state({"fast_mode_state": "on"})
        self.assertEqual(writes, [])


CLI_X = {"pid": 4242, "start": "1700000001"}    # a hosted CLI's identity, as the host's hello names it (pid:start)
CLI_Y = {"pid": 4343, "start": "1700000002"}
CLI_Z = {"pid": 4444, "start": "1700000003"}


def _kernel(state_dir=None):
    """A kernel's backend over `state_dir` (a fresh one when None), Always fast on, with the REAL problem ring behind the
    log so a test can count its entries."""
    if state_dir is None:
        state_dir = tempfile.mkdtemp()
        Path(state_dir, "session-hosts").write_text("off")   # outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(state_dir, "/bin/true", lambda *a, **k: None)
    be._logs = []
    real = be._log
    be._log = lambda msg, *a, **k: (be._logs.append(msg), real(msg, *a, **k))[1]
    _switch(be, sb.ALWAYS_FAST_STORE, "on")
    return be


def _session_from_reg(be):
    """The session as a kernel that just started builds it: from the registry, nothing in memory."""
    s = sb.SdkSession(be, sb.read_reg(be.state_dir, SID))
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    be.sessions[SID] = s
    return s


def _connect(s, cli, attach, launch_fast=None):
    """One connect to the hosted CLI `cli`, as _amain leaves it: the loop top's per-connection reset, the hello's identity,
    and the launch the connect reads (this connect's own record for a launch; the host's echo for an attach)."""
    s._fast_off_sent = s._fast_opt_in_said = False
    s._host = type("H", (), {"hello": {"cli": dict(cli)}, "exit_info": None})()
    s._host_is_attach = attach
    if attach:
        s._hello_launch = {"fast": bool(launch_fast)}
        s._fast_unlocked = bool(launch_fast)
    else:
        s._launch_record = dict(s.launch_values())
        s._fast_unlocked = s.fast_effective()
    s._asks = []
    s.request_reconnect = lambda *a, **k: s._asks.append((a, k))
    s._switch_wanted = ""


class TheGuardBelongsToTheCliNotTheConnection(unittest.TestCase):
    """2026-10-09, the review of #197: a CLI launched with the fast-mode opt-in that still says it lacks it is never
    relaunched for it (a relaunch carries the same flag and hears the same). That guard lived in one connection's memory and
    skipped every attach, so after a kernel restart or a host re-exec the kernel attached to the same CLI and asked for the
    relaunch anyway: a Claude Code that ignores or renames the opt-in cost every Opus session one quiet relaunch per deploy,
    and each trip added a fresh row to the 100-entry problem ring. The guard is keyed on the CLI's identity now and kept in
    the registry, and it holds on a live model change too. Synthetic sessions and CLI identities, no CLI."""

    def _launched_and_tripped(self):
        be = _kernel()
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        self.assertTrue(s._launch_record["fast"], "this kernel launched the CLI carrying the opt-in")
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [], "the guard: no relaunch for a flag that cannot take")
        return be, s

    def _said(self, be):
        return [m for m in be._logs if "launched with the fast-mode opt-in" in m]

    def test_the_next_kernel_attaching_to_the_same_cli_asks_nothing_and_says_why_once(self):
        be, _ = self._launched_and_tripped()
        self.assertEqual(len(self._said(be)), 1)
        be2 = _kernel(be.state_dir)               # the kernel restarts; the host keeps the CLI
        s2 = _session_from_reg(be2)
        _connect(s2, CLI_X, attach=True, launch_fast=True)
        s2._adopt_fast_state(LACKS)               # the attach's initialize answer
        s2._adopt_fast_state(LACKS)               # …and the next turn's init
        self.assertEqual(s2._asks, [], "the same CLI: a relaunch would hear the same, after a restart as before it")
        self.assertEqual(s2._switch_wanted, "", "…and no ask is left standing for the next quiet moment either")
        # the review of #199 (2026-10-10): the launching kernel's line went with its problem ring, and the session then sat
        # at normal speed under a standing Fast pick or Always fast with no reason anywhere. The next kernel says it from the
        # registry's record: once, however many reports and re-attaches to the same CLI follow
        self.assertEqual(len(self._said(be2)), 1, "said again by the kernel that attached, from the registry's record")
        _connect(s2, CLI_X, attach=True, launch_fast=True)   # a host re-exec inside this kernel: the same CLI again
        s2._adopt_fast_state(LACKS)
        self.assertEqual(len(self._said(be2)), 1, "once per CLI in a kernel's life")
        rows = [r for r in be2.problems() if "launched with the fast-mode opt-in" in r["text"]]
        self.assertEqual(len(rows), 1, "the reason is in this kernel's error center: %r" % rows)

    def test_a_host_re_exec_inside_one_kernel_keeps_the_guard(self):
        be, s = self._launched_and_tripped()
        _connect(s, CLI_X, attach=True, launch_fast=True)   # the host re-execs into new code and the kernel attaches again
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [])
        self.assertEqual(len(self._said(be)), 1)

    def test_an_attach_to_a_cli_the_guard_never_named_still_asks(self):
        # a CLI an earlier kernel launched with the opt-in may have lost it to a '/fast off' that kernel sent (the #197
        # incident); only the launching kernel can tell the two apart, so an attach no record names asks as before
        be, _ = self._launched_and_tripped()
        be2 = _kernel(be.state_dir)
        s2 = _session_from_reg(be2)
        _connect(s2, CLI_Y, attach=True, launch_fast=True)
        s2._adopt_fast_state(LACKS)
        self.assertEqual(len(s2._asks), 1, "another CLI: the record names CLI X, not this one")

    def test_the_problem_ring_holds_one_entry_per_session_however_many_clis_trip(self):
        be, s = self._launched_and_tripped()
        _connect(s, CLI_Y, attach=False)          # the user's Fast click relaunched it: a new CLI, the same answer
        s._adopt_fast_state(LACKS)
        self.assertEqual(s._asks, [])
        self.assertEqual(len(self._said(be)), 2, "said once per CLI in the log")
        rows = [r for r in be.problems() if "launched with the fast-mode opt-in" in r["text"]]
        self.assertEqual(len(rows), 1, "one ring entry for the session, counting its CLIs: %r" % rows)
        self.assertEqual(rows[0].get("count"), 2)

    def test_a_live_model_change_on_a_guarded_cli_asks_nothing(self):
        be, s = self._launched_and_tripped()
        s._after_model_change("claude-opus-5-5", "claude-opus-5-5[1m]")
        self.assertEqual(s._asks, [], "the CLI's report cleared _fast_unlocked; the guard holds for a model change too")
        self.assertEqual(s._switch_wanted, "")
        be2 = _kernel(be.state_dir)               # …and on the next kernel, attached to the same CLI
        s2 = _session_from_reg(be2)
        _connect(s2, CLI_X, attach=True, launch_fast=True)
        s2._adopt_fast_state(LACKS)
        s2._after_model_change("claude-opus-5-5", "claude-opus-5-5[1m]")
        self.assertEqual(s2._asks, [])

    def test_a_model_change_on_a_connection_made_without_the_flag_still_asks(self):
        be = _kernel()
        _sess(be, liveModel="Fable 5.1", liveModelId="claude-fable-5-1", model="claude-fable-5-1", liveFast="off")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        self.assertFalse(s._launch_record["fast"], "launched on Fable: no flag")
        s._adopt_fast_state(LACKS)
        s.model, s._model_id = "Opus 5.5", "claude-opus-5-5"
        s._after_model_change("Fable 5.1", "Opus 5.5")
        self.assertEqual(len(s._asks), 1, "Opus arrived on a connection that never carried the flag: the guard is not this")


class ARememberedRefusalHoldsAcrossReports(unittest.TestCase):
    """2026-10-09, the review of #197: a refusal standing only in liveFastReason held back the report that blanked it, and
    the next report asked for a flagged relaunch whose answer only refused again. The real road: your own Fast pick refused
    with a reason under Always fast. Synthetic session, no CLI."""

    def test_a_refused_fast_pick_under_always_fast_costs_no_extra_relaunch(self):
        be = _kernel()
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        self.assertTrue(s._fast_unlocked, "the switch armed this connection")
        be.send = lambda sid, text: True; be._wake_push = lambda: None; be._notify = lambda *a, **k: None
        self.assertTrue(be.set_fast(SID, "on"))   # the literal send on a flagged connection
        s._adopt_fast_state({"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"})
        self.assertEqual(len(s._asks), 1, "the refused pick: cleared, and the flagless relaunch asked for (as before)")
        self.assertFalse(s.fast_opt)
        _connect(s, CLI_Y, attach=False)          # that relaunch
        self.assertFalse(s._launch_record["fast"], "flagless: the refusal held the switch back at the launch")
        s._adopt_fast_state(LACKS)                # its initialize answer
        s._adopt_fast_state(LACKS)                # …and the next turn's init
        self.assertEqual(s._asks, [], "no flagged relaunch just to hear the same refusal")
        self.assertEqual(s.fast_reason, "", "the badge still comes back: the opt-in reason is blanked as before")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fastRuleRefused"), "extra_usage_disabled",
                         "remembered where the next kernel reads it")
        self.assertEqual([m for m in be._logs if m.startswith("always fast (web): the CLI refused")], [],
                         "the refused pick was said by its own toast; the switch says nothing on top")
        be.set_fast(SID, "on")                    # the user's next pick lifts it, as any gesture lifts the switch's memory
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fastRuleRefused"), "")

    def test_with_the_switch_off_nothing_is_remembered_for_it(self):
        be = _kernel(); _switch(be, sb.ALWAYS_FAST_STORE, "off")
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", liveFast="off", liveFastReason="extra_usage_disabled")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        s._adopt_fast_state(LACKS)
        self.assertEqual((s.fast_rule_refused, s.fast_reason), ("", ""))
        self.assertFalse(sb.read_reg(be.state_dir, SID).get("fastRuleRefused"))



REFUSED = {"fast_mode_state": "off", "fast_mode_disabled_reason": "extra_usage_disabled"}


class TheReviewOf199(unittest.TestCase):
    """2026-10-10, the review of #199. Toggling a Model switch (here or from a peer) relaunched every session whose CLI was
    launched with the opt-in and still lacks it; a remembered refusal could only be lifted by a Fast or Slow pick or the CLI
    reporting fast on, so a session refused under one login stayed at normal speed after billing moved to a login that
    has fast mode. Synthetic sessions, CLI identities and billing, no CLI."""

    OTHER = "11111111-2222-4333-8444-000000000799"

    def _guarded(self):
        be = _kernel()
        be._always_fast_seen = True               # the switch as this kernel booted with it
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        s._adopt_fast_state(LACKS)
        self.assertTrue(s._opt_in_cannot_take(), "the guard stands for CLI X")
        return be, s

    def test_toggling_either_switch_never_relaunches_a_guarded_cli(self):
        be, s = self._guarded()
        _switch(be, sb.RETRY_UPGRADE_STORE, "on")
        self.assertEqual(be.apply_model_switches(), 0, "a Retry upgrades toggle (before: one quiet relaunch to hear the same)")
        self.assertEqual(be.apply_model_switches(), 0, "a peer re-sending the same value")
        _switch(be, sb.ALWAYS_FAST_STORE, "off"); be.apply_model_switches()
        _switch(be, sb.ALWAYS_FAST_STORE, "on")
        self.assertEqual(be.apply_model_switches(), 0, "Always fast off and on again")
        self.assertEqual((s._asks, s._switch_wanted), ([], ""))
        be2 = _kernel(be.state_dir)               # …and on the next kernel, attached to the same CLI
        s2 = _session_from_reg(be2)
        _connect(s2, CLI_X, attach=True, launch_fast=True)
        s2._adopt_fast_state(LACKS)
        self.assertEqual(be2.apply_model_switches(), 0)
        self.assertEqual(s2._asks, [])

    def test_a_flip_of_always_fast_forgets_every_remembered_refusal_and_nothing_else_does(self):
        be = _kernel()
        be._always_fast_seen = True
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off")
        _sess(be, sid=self.OTHER, name="api", liveModel="Opus 5.5", liveModelId="claude-opus-5-5", liveFast="off",
              liveFastReason="extra_usage_disabled", fastRuleRefused="extra_usage_disabled")
        be.sessions.pop(self.OTHER)              # dormant: its registry is all there is
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        s._adopt_fast_state(REFUSED)              # the switch armed the flag and the CLI refused with a reason
        _connect(s, CLI_Y, attach=False)          # a flagless relaunch since (the refusal held the switch back)
        self.assertFalse(s._launch_record["fast"])
        s._adopt_fast_state(LACKS)

        def refused(sid):
            r = sb.read_reg(be.state_dir, sid)
            return (r.get("fastRuleRefused") or "", r.get("liveFastReason") or "")
        _switch(be, sb.RETRY_UPGRADE_STORE, "on"); be.apply_model_switches()
        be.apply_model_switches()                 # a peer re-sending the same values
        self.assertEqual((s.fast_rule_refused, refused(SID)[0], refused(self.OTHER)[0]), ("extra_usage_disabled",) * 3,
                         "no flip of Always fast: nothing is forgotten")
        _switch(be, sb.ALWAYS_FAST_STORE, "off"); be.apply_model_switches()
        self.assertEqual((s.fast_rule_refused, s.fast_reason, refused(SID), refused(self.OTHER)), ("", "", ("", ""), ("", "")),
                         "the flip forgets the refusal, live and dormant (before: only a Fast or Slow pick did)")
        self.assertEqual(s._asks, [], "off: nothing wants the flag")
        _switch(be, sb.ALWAYS_FAST_STORE, "on")
        self.assertEqual(be.apply_model_switches(), 1, "on again: the next launch asks the CLI again")
        _connect(s, CLI_Z, attach=False)
        self.assertTrue(s._launch_record["fast"], "the relaunch carries the opt-in")
        s._adopt_fast_state(REFUSED)
        self.assertEqual(refused(SID)[0], "extra_usage_disabled", "the CLI's answer is remembered again: the reason still applies")

    def test_a_launch_on_another_billing_asks_again_though_the_old_cli_refused_meanwhile(self):
        be = _kernel()
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off")
        s = _session_from_reg(be)
        _connect(s, CLI_X, attach=False)
        s._cli_launch = {"auth": "", "authLogin": ""}   # the reconcile's record: launched on the automatic billing
        s._adopt_fast_state(REFUSED)
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fastRefusalBilling"), ["", ""], "kept with the billing it came under")
        self.assertFalse(s._fast_refusal_new_billing(), "a launch on the same billing: the refusal holds")
        self.assertFalse(s.fast_effective())
        sb.write_sdk_default(be.state_dir, auth="login", authExplicit=True, authLogin="")   # billing moves (the default it follows)
        s._adopt_fast_state(REFUSED)              # the old CLI's next turn, before the relaunch for the new billing
        self.assertTrue(s._fast_refusal_new_billing(), "the launch onto the new billing forgets the old one's refusal")
        self.assertTrue(s.fast_effective(), "…so it carries the opt-in (before: normal speed until a Fast or Slow pick)")
        r = sb.read_reg(be.state_dir, SID)
        self.assertEqual((r.get("fastRuleRefused"), r.get("liveFastReason"), "fastRefusalBilling" in r), ("", "", False))
        _connect(s, CLI_Y, attach=False)
        s._cli_launch = {"auth": "login", "authLogin": ""}
        s._adopt_fast_state(REFUSED)              # the new login refuses too: remembered again, under it
        self.assertEqual(s.fast_rule_refused, "extra_usage_disabled")
        self.assertEqual(sb.read_reg(be.state_dir, SID).get("fastRefusalBilling"), ["login", ""])
        self.assertFalse(s._fast_refusal_new_billing(), "and holds while the billing stays")

    def test_a_dormant_session_asks_again_at_its_next_launch_after_a_restart(self):
        be = _kernel()
        _sess(be, liveModel="Opus 5.5", liveModelId="claude-opus-5-5", model="claude-opus-5-5", liveFast="off",
              fastRuleRefused="extra_usage_disabled", fastRefusalBilling=["", ""])
        sb.write_sdk_default(be.state_dir, auth="login", authExplicit=True, authLogin="")
        s = _session_from_reg(_kernel(be.state_dir))   # the next kernel builds it from the registry
        self.assertEqual(s.fast_refusal_billing, ("", ""))
        self.assertTrue(s._fast_refusal_new_billing())
        self.assertTrue(s.fast_effective())
        # …and a refusal no billing was recorded with (a registry written before this) stands, as before
        _sess(be, sid=self.OTHER, name="api", liveModel="Opus 5.5", liveModelId="claude-opus-5-5",
              fastRuleRefused="extra_usage_disabled")
        o = sb.SdkSession(be, sb.read_reg(be.state_dir, self.OTHER))
        self.assertFalse(o._fast_refusal_new_billing())
        self.assertFalse(o.fast_effective())

    def test_every_launch_reads_the_billing_check_before_the_options(self):
        src = inspect.getsource(sb.SdkSession)
        i, j = src.find("self._fast_refusal_new_billing()"), src.find("opts = self.backend._options(self, ClaudeAgentOptions)")
        self.assertTrue(0 < i < j, "the check runs before _options and the snapshot read fast_effective")
