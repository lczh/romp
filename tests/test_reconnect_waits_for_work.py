#!/usr/bin/env python3
"""A pick that needs a relaunch waits for a quiet session; only a Restart cuts, and it says what it cut.

What happened (2026-10-01): a billing login was switched across about twenty running sessions at once. Billing,
effort, fast mode's opt-in, bypass mode and per-session env are connect-time, so each pick relaunches the CLI, and
request_reconnect's default road relaunched at once when no turn was in flight and at the end of the turn otherwise,
whatever that turn had launched. A relaunch replaces the CLI process, and its subagents, timers and monitors end
with it: monitors the sessions were running had to be started again by hand.

The fix: request_reconnect's default is the standing ask a Model switch already used (want_switch_reconnect), carried
the moment nothing would be cut (quiet(): no turn in flight or queued, no live subagent, no background task, no
question waiting on the user), at the exact events that end that work. The end of a turn re-checks the work the turn
launched. The pick's pending marks stay up meanwhile, and the snapshot says what holds them (relaunchWaits). A
Restart (relaunch) and a rewind keep the road that accepts what it cuts (cut=True), and a Restart tells the session
which background tasks it lost with the existing notice (task_death_notice).

Apply now (the same day, later): a session whose monitors never let it go quiet kept its pick waiting for hours, so the
pick's control offers a way through, the Restart road with a cause of its own. The notice opens on what the person did
(CUT_BY: a Restart, a rewind, an Apply now), and the snapshot says what pushing the pick through would end (relaunchCuts).

The follow-up (the review of the first change, the same day): a waiting bypass pick keeps the CLI's confirmed mode and
waits as a mark of its own (modePending), its old CLI's consults are allowed without a false contract-breach row, a change
back withdraws it, and a pick made mid-turn is carried at that turn's result. A fresh CLI takes a new process generation
when its stamp moves the armed timers (_fresh_cli_stamp), so after a pick's relaunch or a Restart a ScheduleWakeup
one-shot on record with its due time and the minute the CLI named, which the old CLI could not yet have fired by the
fresh CLI's start, is the kernel's to deliver, except while the session is a comment thread's (the lost-wakeup sweep
skips those).

The REAL _amain runs here against a stand-in SDK module whose client records what it was fed (the harness of
tests/test_queued_sends_not_fused.py, trimmed to what these cases need). Every id is synthetic, every text invented.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from importlib.machinery import ModuleSpec
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()    # the transcripts the take check scans live here, not ~/.claude
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
sb = load_source("romp_sdk_backend_waitsforwork", os.path.join(BIN, "romp_sdk_backend.py"))
_KERNEL = []


def _kernel():
    """bin/romp-kernel, loaded on first use: the launch-ledger tests read its awaiting lift's _bg_ended_after"""
    if not _KERNEL:
        _KERNEL.append(load_source("romp_kernel_waitsforwork", os.path.join(BIN, "romp-kernel")))
    return _KERNEL[0]


SID = "11111111-2222-3333-4444-bbbbbbbbbb21"        # this module's own synthetic sid
FSID = "11111111-2222-3333-4444-ffffffffff21"       # the CLI's own session id, announced by the init
PARENT = "11111111-2222-3333-4444-aaaaaaaaaa21"     # a comment thread's parent session


class _TextBlock:
    def __init__(self, text): self.text = text
class _AssistantMessage:
    def __init__(self, content, model="claude-x", uuid="a1", parent_tool_use_id=None):
        self.content, self.model, self.uuid = content, model, uuid
        self.parent_tool_use_id, self.stop_reason, self.error = parent_tool_use_id, "end_turn", None
class _ResultMessage:
    pass
def _result(**fields):
    return type("ResultMessage", (_ResultMessage,), dict(fields))()
class _SystemMessage:
    def __init__(self, subtype, data=None, uuid=None):
        self.subtype, self.data, self.uuid = subtype, (data or {}), uuid
_TextBlock.__name__ = "TextBlock"; _AssistantMessage.__name__ = "AssistantMessage"
_ResultMessage.__name__ = "ResultMessage"; _SystemMessage.__name__ = "SystemMessage"
class _Allow:                                   # the permission callback's results (_can_use_tool imports them at call time)
    def __init__(self, **kw): self.behavior = kw.get("behavior")
class _Deny:
    def __init__(self, **kw): self.behavior = kw.get("behavior")


def _wake_call(delay, prompt, reason=None, called=None):
    """The PostToolUse input for a ScheduleWakeup call made at `called` (now by default), with the tool result's
    scheduledFor: the minute at or after the call plus the delay, in epoch ms."""
    t = time.time() if called is None else called
    inp = dict({"delaySeconds": delay, "prompt": prompt}, **({"reason": reason} if reason else {}))
    return {"tool_name": "ScheduleWakeup", "tool_input": inp,
            "tool_response": {"scheduledFor": int(-(-(t + delay) // 60) * 60 * 1000), "clampedDelaySeconds": delay,
                              "wasClamped": False}}


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class _Harness(unittest.TestCase):
    class _Client:
        instances = []

        def __init__(self, options=None, transport=None):
            self.options = options
            type(self).instances.append(self)
            self.loop = asyncio.get_running_loop()
            self.writes = []
            self.frames = asyncio.Queue()
            self.torn_down = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            self.torn_down = True
            return False

        async def query(self, prompt, session_id="default"):
            async for turn in prompt:
                self.writes.append(turn["message"]["content"][0]["text"])

        async def interrupt(self):
            pass

        async def get_context_usage(self):
            return {"percentage": 2, "model": "claude-x"}

        async def get_server_info(self):
            return {}

        async def receive_messages(self):
            while True:
                yield await self.frames.get()

    class _Options:
        def __init__(self, **kw):
            self.session_id = self.resume = None
            for k, v in kw.items():
                setattr(self, k, v)

    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()
        self._Client.instances = []
        fake = types.ModuleType("claude_agent_sdk")
        fake.__spec__ = ModuleSpec("claude_agent_sdk", loader=None)
        fake.ClaudeSDKClient, fake.ClaudeAgentOptions, fake.HookMatcher = self._Client, self._Options, (lambda **kw: types.SimpleNamespace(**kw))
        fake.AssistantMessage, fake.ResultMessage = _AssistantMessage, _ResultMessage
        fake.SystemMessage, fake.TextBlock = _SystemMessage, _TextBlock
        fake.PermissionResultAllow, fake.PermissionResultDeny = _Allow, _Deny
        self._saved_sdk = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake
        async def _noop_refresh(self): pass
        self._saved_refresh = sb.SdkSession._do_refresh_usage
        sb.SdkSession._do_refresh_usage = _noop_refresh
        self.state = tempfile.mkdtemp()
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                       # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd)
        self.lines = []
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None,
                                log=lambda m, **k: self.lines.append(str(m)))
        reg = {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd}
        sb.write_reg(self.be.state_dir, SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        async def _noop(): pass
        self.s._do_refresh_usage = _noop
        self.be.sessions[SID] = self.s
        self.n = 0

    def tearDown(self):
        s = self.s
        if s.thread is not None and s.thread.is_alive():
            s.shutdown()
        if s.thread is not None and s.thread.ident is not None:
            s.thread.join(timeout=10)
        sb.SdkSession._do_refresh_usage = self._saved_refresh
        if self._saved_sdk is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = self._saved_sdk
        shutil.rmtree(self.state, ignore_errors=True)

    # -- the scripted stream --
    def _wait(self, pred, what, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for %s; clients %d; pending %r; log tail %r"
                  % (what, len(self._Client.instances), self.s.pending(), self.lines[-6:]))

    def _settle(self, dt=0.2):
        """Every chance for a (wrong) relaunch to happen: the loop runs its wakeups within this."""
        time.sleep(dt)

    def _push(self, client, frame):
        client.loop.call_soon_threadsafe(client.frames.put_nowait, frame)

    def _uid(self):
        self.n += 1
        return "11111111-2222-3333-4444-%012d" % self.n

    def _init(self, client):
        self._push(client, _SystemMessage("init", {"model": "claude-x", "permissionMode": "acceptEdits",
                                                    "session_id": FSID}, uuid=self._uid()))

    def _result_frame(self, client):
        self._push(client, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1,
                                   session_id=FSID, duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                                   usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
                                   parent_tool_use_id=None))

    def _task(self, client, subtype, tid="t-watch", **data):
        """The CLI's background-task lifecycle frame (task_started … task_notification)."""
        self._push(client, _SystemMessage(subtype, dict({"task_id": tid}, **data), uuid=self._uid()))

    def _on_loop(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.s.loop).result(5)

    def _transcript_append(self, text):
        reg = sb.read_reg(self.state, SID) or {}
        p = sb.transcript_path(self.cwd, str(reg.get("lastSid") or SID))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "user", "uuid": self._uid(), "timestamp": _iso(time.time()),
                                "message": {"role": "user", "content": text}}) + "\n")

    def _first_turn(self):
        """Connect, feed a first turn, and let its init stream: the turn is open (inflight 1)."""
        s = self.s
        s.start()
        self._wait(lambda: s.client is not None, "the first connect")
        c = self._Client.instances[0]
        s.enqueue("first turn")
        self._wait(lambda: c.writes == ["first turn"], "the first turn fed")
        self._init(c)
        self._push(c, _AssistantMessage([_TextBlock("working on it")], uuid=self._uid()))
        self._wait(lambda: getattr(s, "_untaken", None) is None and s.resume_sid == FSID
                   and (sb.read_reg(self.state, SID) or {}).get("lastSid") == FSID, "the first turn open")
        self._transcript_append("first turn")
        return c

    def _end_turn(self, c):
        self._result_frame(c)
        self._wait(lambda: self.s.inflight == 0 and not self.s._cli_working, "the turn settled")

    def _with_a_background_task(self, c, desc="watch the spend"):
        self._task(c, "task_started", description=desc, task_type="local_bash")
        self._wait(lambda: self.s.live_work()[1] == 1, "the background task registered")

    def _relaunched(self):
        self._wait(lambda: len(self._Client.instances) == 2 and self._Client.instances[1] is self.s.client,
                   "the relaunch")
        return self._Client.instances[1]

    def _turn(self, c, text):
        """One more turn on client `c`, written to the transcript and ended by its result"""
        s = self.s
        s.enqueue(text)
        self._wait(lambda: text in c.writes, "the turn fed")
        self._init(c)
        self._push(c, _AssistantMessage([_TextBlock("on it")], uuid=self._uid()))
        self._wait(lambda: s._untaken is None and s.inflight == 1, "the turn open")
        self._transcript_append(text)
        self._end_turn(c)

    def _inside_the_hand_off(self, name, stand_in):
        """sb.<name> runs `stand_in` instead when the fresh-CLI stamp's armed-timer step (_hand_one_shots_to) calls it;
        every other caller gets the real one"""
        real = getattr(sb, name)

        def chosen(*a, **k):
            return (stand_in if sys._getframe(1).f_code.co_name == "_hand_one_shots_to" else real)(*a, **k)
        setattr(sb, name, chosen)
        self.addCleanup(setattr, sb, name, real)

    def _edit_reg(self, change):
        """the session's reg read, changed by `change` and written back in one section under the registry lock, which the
        session's own read-modify-writes of the reg hold too, so none of them lands between the read and the write"""
        with self.be._reg_lock:
            sb.write_reg(self.state, SID, change(sb.read_reg(self.state, SID)))

    def _all_due(self):
        """every armed timer on record past due"""
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, dueEpoch=time.time() - 30) for c in reg["sessionCrons"]]))

    def _open_turns_at_sigterm(self, c):
        """client `c`'s CLI process from here on: a stand-in that a Restart's SIGTERM ends at once, as it ends the installed
        CLI in about two seconds; the list returned records the session's open turn count at the signal"""
        seen = []
        c._transport = types.SimpleNamespace(_process=_OpenTurnAtSigterm(self.s, seen, getattr(c, "events", [])))
        return seen

    def _restart_cutting_the_turn(self, c):
        """Restart session while client `c`'s CLI runs a turn, with nothing to end that turn first: the Restart ends the CLI
        with SIGTERM at once (relaunch_now), so the turn is in flight when the signal reaches the CLI, and it is cut"""
        seen = self._open_turns_at_sigterm(c)
        self.assertEqual(self.be.relaunch(SID), "")
        self._wait(lambda: seen, "the Restart's SIGTERM")
        self.assertGreater(seen[0], 0, "the turn is in flight at the SIGTERM: the Restart cuts it")


class APickWaitsForAQuietSession(_Harness):
    def test_a_billing_pick_waits_for_a_background_task_and_applies_at_its_end(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "no relaunch while the background task runs")
        self.assertFalse(c1.torn_down)
        self.assertFalse(s._reconnect_when_idle, "never the deferred road: that one cuts what the turn left running")
        snap = s.snapshot()
        self.assertTrue(snap["authPending"], "the Billing row keeps saying the pick is applying")
        self.assertEqual(snap["relaunchWaits"], "1 background task", "…and on what")
        waits = [l for l in self.lines if "waiting for the session to go quiet" in l]
        self.assertTrue(waits and waits[-1].startswith("billing (web)") and "nothing is cut" in waits[-1], self.lines)
        self._task(c1, "task_notification", status="completed")        # the task's own end is the deciding event
        c2 = self._relaunched()
        self.assertTrue(c1.torn_down)
        self._wait(lambda: not s.snapshot()["authPending"], "the pick applied at the connect")
        self.assertEqual(s.snapshot()["relaunchWaits"], "")
        self.assertFalse(any("[romp]" in t for t in s.pending() + c2.writes), "nothing was cut, so nothing is reported")

    def test_an_effort_pick_waits_for_a_live_subagent_and_applies_at_its_stop(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self._on_loop(s._subagent_start_hook({"agent_id": "a-review", "agent_type": "general-purpose"}, None, None))
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "no relaunch while the subagent runs")
        snap = s.snapshot()
        self.assertTrue(snap["effortPending"], "the effort badge keeps its dots")
        self.assertEqual(snap["relaunchWaits"], "1 subagent")
        self._on_loop(s._subagent_stop_hook({"agent_id": "a-review"}, None, None))
        self._relaunched()
        self._wait(lambda: not s.snapshot()["effortPending"], "the effort applied at the connect")

    def test_the_end_of_a_turn_re_checks_the_work_that_turn_launched(self):
        """The pick lands mid-turn and the turn starts a background task: its result is not the moment. Before the
        fix the deferred reconnect fired at that result and ended the task with the CLI."""
        s, c1 = self.s, self._first_turn()
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "mid-turn: nothing yet")
        said = [s.snapshot().get("relaunchWaits")]
        self._with_a_background_task(c1, desc="tail the build log")
        said.append(s.snapshot().get("relaunchWaits"))
        self._end_turn(c1)
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "the turn's result re-checked: the task it launched holds the relaunch")
        self.assertFalse(c1.torn_down)
        said.append(s.snapshot().get("relaunchWaits"))
        self.assertEqual(said, ["the running turn", "the running turn and 1 background task", "1 background task"],
                         "what the pending mark says it waits on, at each step")
        self._task(c1, "task_notification", status="completed")
        self._relaunched()

    def test_a_quiet_session_still_applies_a_pick_at_once(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._relaunched()
        self.assertTrue(any("effort (web): the session is quiet — reconnecting now" in l for l in self.lines), self.lines)


class ARestartCutsAndSaysWhatItCut(_Harness):
    def test_a_restart_with_a_live_background_task_relaunches_now_and_tells_the_session(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        note = sb.task_death_notice([{"desc": "watch the spend"}], did=sb.CUT_BY["restart"])
        self._wait(lambda: c2.writes == [note], "the existing what-was-cut notice, fed to the fresh CLI")
        self.assertIn("[romp] I restarted Claude Code, which cut off 1 background task you had running: watch the spend.", note,
                      "it says what the person did, never a settings switch: a pick alone no longer cuts anything")
        self.assertIn("Check whether it is still running before relaunching it", note)

    def test_a_waiting_pick_rides_a_restart_and_is_not_applied_a_second_time(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._settle()
        self.assertEqual(s._switch_wanted, "billing")
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        self.assertEqual(s._switch_wanted, "", "the Restart's relaunch carried the pick")
        self._wait(lambda: not s.snapshot()["authPending"], "applied at the connect")
        self._wait(lambda: len(c2.writes) == 1, "the notice fed to the fresh CLI")
        self._init(c2)
        self._end_turn(c2)
        self._settle()
        self.assertEqual(len(self._Client.instances), 2, "no second relaunch for a pick already applied")


class ApplyNowPushesAWaitingPickThrough(_Harness):
    """Apply now (2026-10-01): a session whose monitors never let it go quiet kept a billing pick waiting for hours. The
    explicit way through is Restart session's road with a cause of its own: the CLI relaunches at once, the work the pick
    waited on ends, and the session is told so in the person's words, as a settings change applied now."""

    def test_a_waiting_billing_pick_is_applied_at_once_and_the_session_hears_why_its_task_ended(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "the pick waits for the background task")
        snap = s.snapshot()
        self.assertEqual((snap["authPending"], snap["relaunchWaits"], snap["relaunchCuts"]),
                         (True, "1 background task", "1 background task"), "the row's words: what pushing it through ends")
        self.assertEqual(self.be.apply_now(SID), "")
        c2 = self._relaunched()
        self.assertTrue(c1.torn_down, "the CLI the task ran in is gone: Apply now does not wait")
        note = sb.task_death_notice([{"desc": "watch the spend"}], did=sb.CUT_BY["apply"])
        self._wait(lambda: c2.writes == [note], "the what-was-cut notice, with Apply now's cause, fed to the fresh CLI")
        self.assertIn("[romp] I restarted Claude Code to apply a settings change now, which cut off 1 background task you "
                      "had running: watch the spend.", note)
        self._wait(lambda: not s.snapshot()["authPending"], "the pick applied at the connect")
        self.assertEqual((s.snapshot()["relaunchWaits"], s.snapshot()["relaunchCuts"]), ("", ""))
        self.assertTrue(any(l.startswith("apply now (web): ending its CLI with SIGTERM") for l in self.lines), self.lines)
        self.assertTrue(any("dropped 0 subagents and 1 background task on reconnect (apply)" in l for l in self.lines), self.lines)

    def test_with_nothing_waiting_it_changes_nothing(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertEqual(self.be.apply_now(SID), sb.NOTHING_TO_APPLY, "a no-op, said as one")
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "no relaunch")
        self.assertFalse(c1.torn_down, "the task's CLI runs on")
        self.assertEqual(s.live_work()[1], 1)

    def test_a_rewind_names_itself_and_a_relaunch_no_gesture_asked_for_keeps_the_plain_cause(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        s.request_reconnect(cut=True, cause="rewind")
        c2 = self._relaunched()
        rewound = sb.task_death_notice([{"desc": "watch the spend"}], did=sb.CUT_BY["rewind"])
        self._wait(lambda: c2.writes == [rewound], "the rewind's notice")
        self.assertIn("[romp] I rewound the conversation and restarted Claude Code, which cut off 1 background task", rewound)
        # the gesture was taken at its teardown: a later relaunch no gesture names is not reported as the rewind
        self._init(c2)
        self._with_a_background_task(c2, desc="tail the build log")
        self._end_turn(c2)                       # the fed notice's turn ends; the task it started runs on
        s.request_reconnect(cut=True)
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the second relaunch")
        c3 = self._Client.instances[2]
        plain = sb.task_death_notice([{"desc": "tail the build log"}])
        self._wait(lambda: c3.writes == [plain], "the plain notice")
        self.assertIn("ended (a restart or crash): tail the build log.", plain)


class _Proc:
    """The SDK transport's CLI process as _term_for_relaunch reaches it: SIGTERM ends it at once (the installed CLI exits
    in about two seconds), and the order of the end and the teardown is recorded on the client."""

    def __init__(self, events):
        self.pid, self.returncode, self.events = 4242, None, events
        self.on_term = None    # what the CLI writes on its way out (a test sets it: the cost-state record)

    def terminate(self):
        self.events.append("SIGTERM")
        if self.on_term is not None:
            self.on_term()
        self.returncode = -15


class _OpenTurnAtSigterm(_Proc):
    """_Proc that also records the session's open turn count (inflight) when the SIGTERM reaches it."""

    def __init__(self, s, seen, events):
        super().__init__(events)
        self.s, self.seen = s, seen

    def terminate(self):
        self.seen.append(self.s.inflight)
        super().terminate()


class _SigtermHarness(_Harness):
    """The harness with a stand-in client whose transport carries a CLI process (_Proc), so _term_for_relaunch's SIGTERM
    and the teardown are recorded in order on the client."""

    class _Client(_Harness._Client):
        instances = []

        def __init__(self, options=None, transport=None):
            super().__init__(options, transport)
            self.interrupts, self.events = 0, []
            self._transport = types.SimpleNamespace(_process=_Proc(self.events))

        async def __aexit__(self, *a):
            self.events.append("teardown")
            return await super().__aexit__(*a)

        async def interrupt(self):
            self.interrupts += 1


class ARestartEndsTheCliWithSigterm(_SigtermHarness):
    """Restart session ends the old CLI with SIGTERM, at once, and never sends it the interrupt control request (the user
    2026-10-17). On the installed CLI that request stops every background agent the session runs, recorded as stopped by
    the user, which SendMessage then refuses to resume: a pair of agents was lost to a Restart with a turn in flight that
    way, and an earlier pair to a Stop. SIGTERM records nothing of the kind (verified in a lab on the real CLI: the
    agent resumed by SendMessage after the relaunch), and unlike the teardown's EOF, which a CLI with a background agent
    running ignores until the host's end grace runs out, it ends the CLI in seconds. A text the CLI held untaken goes to
    the fresh CLI once the old one is proven gone; one that landed before the end is not fed twice. The REAL run loop
    against the stand-in client, whose transport process records the SIGTERM; every id synthetic."""

    def _with_a_background_agent(self, c):
        self._task(c, "task_started", tid="a-sleeper", description="review the parser", task_type="local_agent")
        self._wait(lambda: self.s.live_work()[1] == 1, "the background agent registered")

    def test_a_restart_with_a_turn_in_flight_and_a_background_agent_sends_no_interrupt_and_relaunches_now(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_agent(c1)
        self.assertEqual(s.inflight, 1, "the turn is in flight")
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()                     # no result frame was ever sent: nothing waits on the turn's end
        self.assertEqual(c1.interrupts, 0, "never the interrupt control request")
        self.assertEqual(c1.events, ["SIGTERM", "teardown"], "the CLI is ended with SIGTERM before the connection closes")
        self.assertTrue(any(l.startswith("relaunch (web): ending its CLI with SIGTERM, which cuts the running turn")
                            for l in self.lines), self.lines)
        self._wait(lambda: len(c2.writes) == 1, "romp's own what-was-cut notice, fed to the fresh CLI")
        note = c2.writes[0]
        # the cut-off background AGENT is asked to resume (the review of #281, 2026-10-18, wording the user approved):
        # Claude Code kept its progress and SendMessage resumes it by its id, the task id the CLI gave it; the
        # check-and-relaunch ask made the session start it over
        self.assertEqual(note[note.index("[romp]"):],
                         "[romp] I restarted Claude Code, which cut off 1 background agent you had running: review the "
                         "parser. Claude Code kept its progress: resume it by sending it a message (SendMessage to "
                         "a-sleeper) instead of starting it again. If it isn't needed any more, carry on.")
        self.assertEqual(note, sb.task_death_notice([{"desc": "review the parser", "type": "local_agent",
                                                      "taskId": "a-sleeper"}], did=sb.CUT_BY["restart"]))
        self.assertEqual(s.inflight, 1, "the notice's own turn, and nothing stranded from the cut one")

    def test_an_idle_restart_with_a_background_agent_ends_the_cli_with_sigterm_not_an_eof_wait(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_agent(c1)
        self._end_turn(c1)
        self.assertEqual(self.be.relaunch(SID), "")
        self._relaunched()
        self.assertEqual(c1.interrupts, 0)
        self.assertEqual(c1.events, ["SIGTERM", "teardown"],
                         "an EOF alone leaves a CLI with a background agent running until the host's end grace ends it")

    def test_a_text_the_cli_held_untaken_goes_to_the_fresh_cli_first(self):
        s, c1 = self.s, self._first_turn()
        s.enqueue("and cover the empty input too")
        self._wait(lambda: c1.writes == ["first turn", "and cover the empty input too"], "fed mid-turn")
        self._wait(lambda: s._untaken is not None, "held: the CLI has not taken it")
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        self._wait(lambda: c2.writes == ["and cover the empty input too"], "the held text, fed to the fresh CLI")
        self.assertEqual(c1.interrupts, 0)
        self.assertTrue(any("was holding a fed text it had not run: back at the head of the queue" in l for l in self.lines),
                        self.lines)

    def test_a_held_text_that_landed_before_the_end_is_not_fed_twice(self):
        s, c1 = self.s, self._first_turn()
        s.enqueue("and cover the empty input too")
        self._wait(lambda: s._untaken is not None and len(c1.writes) == 2, "fed mid-turn and held")
        self._transcript_append("and cover the empty input too")   # the CLI wrote its record before the SIGTERM
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        self._settle()
        self.assertEqual(c2.writes, [], "it reached the conversation already: the fresh CLI resumes it, no second copy")
        self.assertEqual(s.pending(), [])


class ARestartRecordsTheCutTurnsSpend(_SigtermHarness):
    """The review of #281 (2026-10-18): a Restart or an Apply now ends the CLI with SIGTERM and no result ever reports the
    running turn's cost, and the next connect reseeds the spend watermarks, so the spend guard and the rail never saw
    what the cut turn spent. The CLI writes a cost-state record on its way out (verified on 2.1.280 under a session host:
    exit 143, the record's total the last result's plus the cut turn's); the loop top records that record's totals less
    the watermarks, the way a result's delta is recorded, and only a record written past the transcript's end at the
    signal counts, so a record the CLI restored at its launch is never taken for its last one. Figures synthetic."""

    def _transcript(self):
        return sb.transcript_path(self.cwd, FSID)

    def _cost_state(self, total, tokens=None):
        rec = {"type": "cost-state", "sessionId": FSID, "totalCostUSD": total}
        if tokens:
            rec["modelUsage"] = {"claude-x": dict(zip(("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                       "cacheCreationInputTokens"), tokens), costUSD=total)}
        with open(self._transcript(), "a") as f:
            f.write(json.dumps(rec) + "\n")

    def _spend(self):
        try:
            d = json.load(open(os.path.join(self.state, "spend.json")))
        except OSError:
            return {}
        return next(iter(d.get("days", {}).values()), {})

    def _second_turn_in_flight(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)                                   # the last result: $0.01 recorded, the watermark at $0.01
        self.assertAlmostEqual(self._spend().get("usd"), 0.01)
        s.enqueue("second turn")
        self._wait(lambda: c1.writes[-1:] == ["second turn"], "the second turn fed")
        self._init(c1)
        self._push(c1, _AssistantMessage([_TextBlock("on it")], uuid=self._uid()))
        self._wait(lambda: getattr(s, "_untaken", None) is None and s.inflight == 1, "the second turn taken and running")
        self._transcript_append("second turn")
        return c1

    def test_a_mid_turn_restart_records_what_the_cut_turn_spent_from_the_record_the_ending_cli_wrote(self):
        c1 = self._second_turn_in_flight()
        c1._transport._process.on_term = lambda: self._cost_state(0.25, (100, 50, 1000, 10))
        self.assertEqual(self.be.relaunch(SID), "")
        self._relaunched()
        b = self._spend()
        self.assertAlmostEqual(b.get("usd"), 0.25, msg="the first result's $0.01 and the cut turn's $0.24")
        self.assertEqual(b.get("turns"), 2)
        self.assertEqual((b.get("tokIn"), b.get("tokOut"), b.get("tokCacheR"), b.get("tokCacheW")), (101, 51, 1000, 10),
                         "the cut's tokens beside the first result's flat count")
        self.assertAlmostEqual(b["bySid"][SID]["usd"], 0.25, msg="billed to the session, like any turn")
        self.assertTrue(any(l.startswith("spend (web): recorded $0.24 the relaunch cut before the running turn reported")
                            for l in self.lines), self.lines)

    def test_a_record_written_before_the_signal_is_not_taken_for_the_ending_clis(self):
        # a record already in the transcript (the one a CLI restores at its launch) sits before the mark: were it read as
        # the cut's, a CLI that had not restored it would have its whole history recorded as the cut turn
        c1 = self._second_turn_in_flight()
        self._cost_state(5.00, (9, 9, 9, 9))
        self.assertEqual(self.be.relaunch(SID), "")         # and the ending CLI writes none
        self._relaunched()
        self.assertAlmostEqual(self._spend().get("usd"), 0.01, msg="nothing taken from the older record")
        self.assertTrue(any(l.startswith("spend (web): the CLI a relaunch ended with SIGTERM wrote no cost-state record "
                                         "on its way out, so what the cut turn spent since its last result is not counted")
                            for l in self.lines), self.lines)

    def test_the_fresh_cli_s_first_result_is_folded_over_the_record_not_counted_twice(self):
        s, c1 = self.s, self._second_turn_in_flight()
        c1._transport._process.on_term = lambda: self._cost_state(0.25, (100, 50, 1000, 10))
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        s.enqueue("third turn")
        self._wait(lambda: c2.writes == ["third turn"], "the third turn fed to the fresh CLI")
        self._init(c2)
        self._push(c2, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=FSID,
                               duration_ms=1, duration_api_ms=1, total_cost_usd=0.30, result="ok", parent_tool_use_id=None,
                               usage={"input_tokens": 1, "output_tokens": 1},
                               model_usage={"claude-x": {"inputTokens": 103, "outputTokens": 52, "cacheReadInputTokens": 1000,
                                                         "cacheCreationInputTokens": 10, "costUSD": 0.30}}))
        self._wait(lambda: self._spend().get("turns") == 3, "the third turn's result folded")
        self.assertAlmostEqual(self._spend().get("usd"), 0.30, msg="the restored record's $0.25 is the seed, not spend again")


class ABypassPickWaitsAsAMarkOfItsOwn(_Harness):
    """A bypass pick relaunches the CLI with the flag (the CLI refuses a live switch into bypass), and since the relaunch
    waits for a quiet session, so does the pick (2026-10-01). Before, the pick set the CLI's confirmed mode at once: the
    badge read Bypass, each consult of the old CLI was auto-allowed with a problem row saying it broke a bypass contract it
    was never launched under, and the old CLI's next init put the mode back with no mark, after which its consults parked
    as prompts that held the very relaunch the pick waited for. Now the confirmed mode stays the CLI's, the pick waits as a
    mark of its own (modePending) that the old CLI's inits leave alone, those consults are auto-allowed per the declared
    mode with a plain log line, and changing back withdraws the pick."""

    def _consult(self):
        ctx = types.SimpleNamespace(suggestions=None, title=None, description=None)
        return asyncio.run_coroutine_threadsafe(self.s._can_use_tool("Bash", {"command": "uv run pytest -q"}, ctx), self.s.loop)

    def _problem_rows(self):
        return [p["text"] for p in self.be.problems()]

    def _bypass_waiting_on_a_task(self):
        c1 = self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "the relaunch waits for the background task")
        return c1

    def test_the_mark_outlasts_the_old_clis_init_its_consults_are_allowed_and_the_relaunch_applies_it(self):
        s, c1 = self.s, self._bypass_waiting_on_a_task()
        snap = s.snapshot()
        self.assertEqual((snap["mode"], snap.get("modePending")), ("acceptEdits", True),
                         "the mode the running CLI confirmed, and the pick's own mark")
        self.assertTrue(snap["relaunchWaits"] and snap["relaunchWaits"] == s.relaunch_waits(),
                        "…and what it waits on (WhatTheRelaunchWaitsFor owns the words): %r" % snap["relaunchWaits"])
        rows = self._problem_rows()                        # what stands before the consults (the harness's own boot rows)
        n = len(self.lines)
        self.assertEqual(self._consult().result(5).behavior, "allow", "the declared intent: no prompt")
        said = [l for l in self.lines[n:] if "Bash" in l]  # each consult's lines, found by the tool they name, in any wording
        self._init(c1)                                     # the old CLI opens a turn of its own: its init reports its launch mode
        self._push(c1, _AssistantMessage([_TextBlock("the build log shows a new warning")], uuid=self._uid()))
        self._wait(lambda: s._cli_working, "the old CLI's own turn open")
        snap = s.snapshot()
        self.assertEqual((snap["mode"], snap.get("modePending")), ("acceptEdits", True), "the init leaves the mark alone")
        n = len(self.lines)
        self.assertEqual(self._consult().result(5).behavior, "allow", "still allowed after that init, never a parked prompt")
        said2 = [l for l in self.lines[n:] if "Bash" in l]
        self.assertIsNone(self.be._pending_ask.get(SID), "no prompt stands to hold the relaunch")
        self.assertEqual(self._problem_rows(), rows, "that CLI was never launched in bypass: no problem row of any wording")
        self.assertEqual((len(said), len(said2)), (1, 1), "each consult is said once, as a plain line: %r" % (said + said2))
        self._result_frame(c1)
        self._wait(lambda: not s._cli_working, "the old CLI's turn ended")
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "the task still holds the relaunch")
        self._task(c1, "task_notification", status="completed")
        c2 = self._relaunched()
        self.assertEqual(c2.options.permission_mode, "bypassPermissions", "the relaunch carries the flag")
        self._wait(lambda: not s.snapshot().get("modePending"), "applied at the connect")
        self.assertEqual(s.snapshot()["mode"], "bypassPermissions", "the launch carried it: the badge reads Bypass now")
        self.assertNotIn("relaunchWait", sb.read_reg(self.state, SID) or {}, "nothing waits any more")

    def test_changing_back_before_it_applies_withdraws_the_mark_and_its_own_relaunch(self):
        s, c1 = self.s, self._bypass_waiting_on_a_task()
        self.assertEqual(s._switch_wanted, "bypass mode")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        snap = s.snapshot()
        self.assertEqual((snap["mode"], snap.get("modePending")), ("acceptEdits", False))
        self.assertEqual(s._switch_wanted, "", "the pick raised the ask alone, so the ask goes with it")
        self.assertNotIn("relaunchWait", sb.read_reg(self.state, SID) or {}, "…and the record a restart would re-raise it from")
        self._task(c1, "task_notification", status="completed")   # the event that would have carried the relaunch
        self._wait(lambda: s.live_work()[1] == 0, "the task's end handled")
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "no relaunch for a pick that was changed back")
        self.assertFalse(c1.torn_down)

    def test_changing_back_keeps_a_relaunch_another_pick_still_needs(self):
        s, c1 = self.s, self._first_turn()
        self._with_a_background_task(c1)
        self._end_turn(c1)
        self.assertTrue(self.be.set_auth(SID, "login"))     # a billing pick waits first: the ask is its own
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(s.snapshot().get("modePending"), "the premise: the bypass pick waits as a mark of its own")
        self.assertEqual(set(((sb.read_reg(self.state, SID) or {}).get("relaunchWait") or {}).get("picks") or {}),
                         {"billing", "mode"}, "…and on record beside the billing pick")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        snap = s.snapshot()
        self.assertEqual((snap.get("modePending"), snap["authPending"]), (False, True), "only the bypass pick is withdrawn")
        self.assertTrue(s._switch_wanted, "the billing pick still needs its relaunch")
        self.assertEqual(set(((sb.read_reg(self.state, SID) or {}).get("relaunchWait") or {}).get("picks") or {}), {"billing"})
        self._task(c1, "task_notification", status="completed")
        c2 = self._relaunched()
        self.assertEqual(c2.options.permission_mode, "acceptEdits", "it carries the mode as it stands now")
        self._wait(lambda: not s.snapshot()["authPending"], "the billing pick applied")

    def test_a_bypass_pick_made_mid_turn_relaunches_at_that_turns_end(self):
        """The turn's result carries a standing ask nothing else holds (the ResultMessage handler's switch arm). For a
        bypass pick, which no kernel door parks, it is the only event road; the kernel's 30 s tick is the backstop, and
        this harness has none."""
        s, c1 = self.s, self._first_turn()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "mid-turn: the relaunch waits for the turn")
        snap = s.snapshot()
        self.assertEqual((snap["mode"], snap.get("modePending")), ("acceptEdits", True))
        self.assertEqual((s.inflight, s.live_work()), (1, (0, 0)), "the premise: the open turn alone holds the relaunch")
        self.assertTrue(snap["relaunchWaits"] and snap["relaunchWaits"] == s.relaunch_waits(), snap["relaunchWaits"])
        self._end_turn(c1)
        c2 = self._relaunched()
        self.assertEqual(c2.options.permission_mode, "bypassPermissions")
        self._wait(lambda: not s.snapshot().get("modePending"), "applied at the connect")
        self.assertEqual(s.snapshot()["mode"], "bypassPermissions")


class AWakeupSurvivesThePicksRelaunch(_Harness):
    """A ScheduleWakeup one-shot lives in the CLI's memory, and a resumed CLI does not re-arm it. A pick on a quiet session
    relaunches at once (an armed timer does not hold quiet(): a dynamic /loop re-arms one every round), and the session
    object, with its process generation, survived the relaunch, so the kernel's lost-wakeup sweep read the record as the
    live CLI's to fire and the fresh CLI's first Stop hook dropped it: the wakeup never fired. A fresh CLI now takes a new
    generation when its stamp moves the armed timers (_fresh_cli_stamp), so a record with its due time and the minute the
    CLI named, which the old CLI could not yet have fired, is the kernel's to deliver at that time, except while the
    session is a comment thread's, which the sweep skips. A /loop's wakeup the kernel holds that way still follows the
    loop: the fresh CLI's stop ends it, and its new arm replaces it."""

    def _one_shots(self):
        return [c for c in self._crons() if not c.get("recurring")]

    def _crons(self):
        return (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or []

    def test_a_quiet_sessions_wakeup_is_delivered_after_a_pick_relaunched_it(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self._on_loop(s._sched_tool_hook(_wake_call(600, "check the notes-api build again"), "tu-wake", None))
        armed_by = s.proc_gen
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by], "armed by the running CLI's generation")
        self.assertTrue(s.quiet(), "an armed timer does not hold the relaunch")
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertNotEqual(s.proc_gen, armed_by, "the fresh CLI is a new generation")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "not due yet: nothing delivered early")
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's first turn end
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by], "the record outlives the fresh CLI's Stop hook")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1, "due, and no live CLI can fire it: the kernel delivers it")
        self._wait(lambda: c2.writes == ["check the notes-api build again"], "the wakeup's prompt, fed to the fresh CLI")
        self.assertEqual(self._one_shots(), [], "stripped first, so it is never delivered twice")

    def test_a_comment_threads_wakeup_armed_before_a_picks_relaunch_is_not_delivered_after_it(self):
        """deliver_lost_wakeups skips a comment thread's session (threadOf), so it does not deliver the wakeup the old CLI
        armed."""
        self._edit_reg(lambda reg: dict(reg, threadOf=PARENT))
        self.s.thread_of = PARENT
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self._on_loop(s._sched_tool_hook(_wake_call(600, "check the notes-api build again"), "tu-wake", None))
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's first turn end
        self.assertEqual(len(self._one_shots()), 1, "the premise: the record outlives the fresh CLI's Stop hook")
        self.assertEqual((sb.read_reg(self.state, SID) or {}).get("threadOf"), PARENT, "the premise: a comment thread's session")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(c2.writes, [])

    def test_a_one_shot_the_fresh_cli_lists_is_left_to_that_cli(self):
        """A one-shot cron this session object first recorded when the old CLI listed it has no due time, so the relaunch
        hands it to the fresh CLI, whose Stop hook lists it too and keeps it as that CLI's: the kernel delivers nothing."""
        s, c1 = self.s, self._first_turn()
        cron = {"id": "job-1", "schedule": "30 14 * * *", "prompt": "post the notes-api status", "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))   # the old CLI lists it
        self._end_turn(c1)
        armed_by = s.proc_gen
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by])
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))   # …and it lists the entry too
        self.assertEqual([c["procGen"] for c in self._one_shots()], [s.proc_gen], "the CLI that lists it owns it")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "due, but its CLI fires it: the kernel delivers nothing")
        self.assertEqual(c2.writes, [])

    def test_a_wakeup_an_earlier_session_object_recorded_is_left_to_the_cli_that_lists_it(self):
        """An entry the CLI lists in its own Stop hook payload takes the live generation, whichever generation recorded it:
        here a wakeup with its due time, recorded under an earlier session object's generation (a kernel restart that a
        session host's CLI outlived), so the kernel does not deliver it beside that CLI."""
        due = time.time() + 600
        at = time.localtime(due)
        entry = {"id": "a1b2c3d4", "cron": "%d %d * * *" % (at[4], at[3]), "prompt": "check the notes-api build again",
                 "kind": "", "recurring": False, "armedAt": time.time() - 60, "dueEpoch": due, "procGen": "gen-before-the-restart"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        listed = {"id": "a1b2c3d4", "schedule": entry["cron"], "prompt": entry["prompt"], "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [listed], "background_tasks": []}, None, None))
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "due, but the CLI that lists it fires it")
        self.assertEqual([c["procGen"] for c in self._one_shots()], [s.proc_gen])

    def test_a_one_shot_with_no_due_time_this_session_object_recorded_is_handed_to_the_fresh_cli_at_the_relaunch(self):
        """A one-shot this session object recorded with no due time (a one-shot cron, as a Stop hook payload lists it) takes
        the fresh CLI's generation at the relaunch, so the kernel does not deliver it before that CLI's first turn end, and
        that turn end keeps it when the CLI lists it."""
        s, c1 = self.s, self._first_turn()
        cron = {"id": "job-1", "schedule": "30 14 * * *", "prompt": "post the notes-api status", "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))   # the old CLI lists it
        self._end_turn(c1)
        self.assertEqual([c.get("dueEpoch") for c in self._one_shots()], [None], "the premise: no due time on record")
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "due before the fresh CLI's first turn end: the kernel delivers nothing")
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))   # the fresh CLI lists it
        self.assertEqual([(c["id"], c["procGen"]) for c in self._one_shots()], [("job-1", s.proc_gen)])
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(c2.writes, [])

    def test_a_one_shot_an_earlier_session_object_recorded_stays_the_kernels_after_the_first_connect(self):
        """One recorded under a generation this session object never had (an earlier object's, before a kernel restart) is
        left as it is by the first connect, and the kernel delivers it once it is past due."""
        due = time.localtime(time.time() - 120)
        entry = {"id": "job-2", "cron": "%d %d * * *" % (due[4], due[3]), "prompt": "post the notes-api status", "kind": "",
                 "recurring": False, "armedAt": time.time() - 600, "dueEpoch": None, "procGen": "gen-before-the-restart"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        c1 = self._first_turn()
        self.assertEqual([c["procGen"] for c in self._one_shots()], ["gen-before-the-restart"], "the first connect leaves it")
        self.assertEqual(self.be.deliver_lost_wakeups(), 1, "past due, and no live CLI holds it: the kernel delivers it")
        self._wait(lambda: "post the notes-api status" in c1.writes, "its prompt fed")

    def test_a_recurring_cron_carrying_a_wakeups_due_time_survives_a_quiet_picks_relaunch(self):
        """The Stop hook matches a listed entry to a call-time record by prompt, and a recurring schedule names no due
        minute, so a recurring cron listed with a pending wakeup's prompt takes that wakeup's due time."""
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, "check the notes-api queue"), "tu-wake", None))
        self._on_loop(s._stop_hook({"session_crons": [{"id": "cron-r", "schedule": "*/5 * * * *",
                                                       "prompt": "check the notes-api queue", "recurring": True}],
                                    "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertEqual([(c["id"], c["recurring"], bool(c.get("dueEpoch"))) for c in self._crons()], [("cron-r", True, True)],
                         "the premise: a recurring entry with a due time")
        t = time.time()
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, armedAt=t - 700, dueEpoch=t - 100, scheduledFor=t - 100)
                                                           for c in reg["sessionCrons"]]))
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._relaunched()
        self.assertEqual([c["id"] for c in self._crons()], ["cron-r"])

    def _listed(self, prompt="check the notes-api build again", delay=600, cid="a1b2c3d4"):
        due = time.localtime(time.time() + delay)
        return {"id": cid, "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": prompt, "recurring": False}

    def test_a_wakeup_two_turn_ends_listed_is_delivered_after_a_quiet_picks_relaunch(self):
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, "check the notes-api build again"), "tu-wake", None))
        self._on_loop(s._stop_hook({"session_crons": [self._listed()], "background_tasks": []}, None, None))
        self._end_turn(c1)
        self._turn(c1, "tighten the notes-api search")
        self._on_loop(s._stop_hook({"session_crons": [self._listed()], "background_tasks": []}, None, None))
        self.assertEqual([(c["id"], bool(c.get("dueEpoch"))) for c in self._one_shots()], [("a1b2c3d4", True)],
                         "the premise: listed by both turn ends under the CLI's id")
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertNotEqual(s.proc_gen, armed_by, "the fresh CLI is a new generation")
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's turn end
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: "check the notes-api build again" in c2.writes, "its prompt fed")

    def test_a_comment_threads_wakeup_kept_across_a_picks_relaunch_is_delivered_once_the_thread_is_promoted(self):
        self._edit_reg(lambda reg: dict(reg, threadOf=PARENT))
        self.s.thread_of = PARENT
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self._on_loop(s._sched_tool_hook(_wake_call(600, "check the notes-api build again"), "tu-wake", None))
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's turn end
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "not while it is a comment thread's session")
        self.assertTrue(self.be.promote_thread(SID, "web-search"))
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: "check the notes-api build again" in c2.writes, "its prompt fed")

    def test_a_cron_listed_with_a_pending_wakeups_prompt_and_minute_leaves_both_to_the_fresh_cli(self):
        """The turn end cannot tell which of the two listed entries is the wakeup, so the relaunch hands both to the fresh
        CLI, as it hands a one-shot cron this session object recorded, and the kernel delivers neither; that CLI's turn end
        drops the wakeup it does not list."""
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, "post the notes-api status"), "tu-wake", None))
        self._on_loop(s._stop_hook({"session_crons": [self._listed("post the notes-api status", cid="wake-1"),
                                                      self._listed("post the notes-api status", cid="job-9")],
                                    "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual(sorted((c["id"], c["procGen"]) for c in self._one_shots()),
                         [("job-9", s.proc_gen), ("wake-1", s.proc_gen)])
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._on_loop(s._stop_hook({"session_crons": [self._listed("post the notes-api status", cid="job-9")],
                                    "background_tasks": []}, None, None))   # the fresh CLI lists the cron alone
        self.assertEqual([c["id"] for c in self._one_shots()], ["job-9"], "the wakeup dropped")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(c2.writes, [])

    def test_a_relaunch_hands_over_the_one_shots_beside_an_entry_that_is_not_a_record(self):
        """Its premise is the relaunch's new generation; where the relaunch keeps the generation, the premise fails."""
        s, c1 = self.s, self._first_turn()
        cron = {"id": "job-1", "schedule": "30 14 * * *", "prompt": "post the notes-api status", "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))
        self._end_turn(c1)
        self._edit_reg(lambda reg: dict(reg, sessionCrons=["not-a-record"] + reg["sessionCrons"]))
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._relaunched()
        self.assertNotEqual(s.proc_gen, armed_by, "the premise: the fresh CLI is a new generation")
        crons = (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or []
        self.assertEqual([c if not isinstance(c, dict) else (c["id"], c["procGen"]) for c in crons],
                         ["not-a-record", ("job-1", s.proc_gen)])

    WAKE = "check the notes-api build again"

    def _the_kernels_wakeup(self):
        """a quiet session's CLI arms a wakeup 600 s out, then a quiet effort pick relaunches it: the fresh CLI's client"""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.WAKE), "tu-wake", None))
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by],
                         "the premise: on record under the generation of the CLI that armed it")
        return c2

    def _sent(self, c):
        """how many times the wakeup's prompt reached client `c`, fed or queued behind the first; a fed prompt counts
        once it reaches `c` (the input generator pops it, then writes the registry and a state row before it hands it
        over)"""
        self._settle(0.3)
        self._wait(lambda: self.WAKE in c.writes or not any(self.WAKE in str(f) for f in self.s.fed_texts()),
                   "a fed wakeup reaching the client")
        return c.writes.count(self.WAKE) + sum(1 for p in self.s.pending() if self.WAKE in str(p))

    def _a_sweep_inside(self, hook):
        """the first registry read `hook` makes starts a lost-wakeup sweep on another thread, as the producer runs it, and
        goes on once that sweep is done or a second has passed; what the sweeps send lands in the returned list"""
        real, sweeps, sent = sb.read_reg_for_rmw, [], []

        def read(*a, **k):
            r = real(*a, **k)
            if not sweeps and sys._getframe(1).f_code.co_name == hook:
                sweeps.append(threading.Thread(target=lambda: sent.append(self.be.deliver_lost_wakeups()), daemon=True))
                sweeps[0].start()
                sweeps[0].join(1.0)
            return r
        sb.read_reg_for_rmw = read
        self.addCleanup(setattr, sb, "read_reg_for_rmw", real)
        return sweeps, sent

    def _sent_once_beside(self, c2, sweeps, sent):
        self.assertEqual(len(sweeps), 1, "the premise: a sweep ran inside the hook's record")
        sweeps[0].join(10)
        sent.append(self.be.deliver_lost_wakeups())
        self.assertEqual(sum(sent), 1)
        self.assertEqual(self._sent(c2), 1)
        self.assertNotIn(self.WAKE, [c["prompt"] for c in self._one_shots()])

    def test_a_wakeup_the_kernel_holds_is_sent_once_when_a_sweep_runs_inside_the_fresh_clis_cron_create_call(self):
        c2 = self._the_kernels_wakeup()
        self._on_loop(self.s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # its first turn end
        self._all_due()
        sweeps, sent = self._a_sweep_inside("_sched_tool_hook")
        call = {"tool_name": "CronCreate", "tool_input": {"name": "nightly", "schedule": "0 3 * * *",
                                                          "prompt": "post the notes-api status"}}
        self._on_loop(self.s._sched_tool_hook(call, "tu-cron", None))
        self._sent_once_beside(c2, sweeps, sent)

    def test_a_wakeup_the_kernel_holds_is_sent_once_when_a_sweep_runs_inside_a_turn_end_that_lists_a_new_cron(self):
        c2 = self._the_kernels_wakeup()
        self._all_due()
        sweeps, sent = self._a_sweep_inside("_stop_hook_run")   # the turn-end body since #235 split _stop_hook
        cron = {"id": "job-9", "schedule": "0 3 * * *", "prompt": "post the notes-api status", "recurring": False}
        self._on_loop(self.s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))   # its first turn end
        self._sent_once_beside(c2, sweeps, sent)

    def test_a_wakeup_an_earlier_session_object_recorded_is_sent_once_when_a_sweep_runs_inside_a_cron_create_call(self):
        entry = {"id": "toolhook-1", "cron": "", "prompt": self.WAKE, "kind": "", "recurring": False,
                 "armedAt": time.time() - 660, "dueEpoch": time.time() - 60, "procGen": "gen-before-the-restart",
                 "src": "toolhook"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        c1 = self._first_turn()
        sweeps, sent = self._a_sweep_inside("_sched_tool_hook")
        call = {"tool_name": "CronCreate", "tool_input": {"name": "nightly", "schedule": "0 3 * * *",
                                                          "prompt": "post the notes-api status"}}
        self._on_loop(self.s._sched_tool_hook(call, "tu-cron", None))
        self._sent_once_beside(c1, sweeps, sent)

    def _for_the_sweep(self, name, stand_in):
        """sb.<name> runs `stand_in` instead when deliver_lost_wakeups calls it; every other caller gets the real one"""
        real = getattr(sb, name)

        def chosen(*a, **k):
            return (stand_in if sys._getframe(1).f_code.co_name == "deliver_lost_wakeups" else real)(*a, **k)
        setattr(sb, name, chosen)
        self.addCleanup(setattr, sb, name, real)
        return real

    def test_a_sweep_whose_strip_fails_sends_nothing_and_a_later_sweep_sends_the_wakeup_once(self):
        c2 = self._the_kernels_wakeup()
        self._all_due()

        def full(*a, **k):
            raise OSError("no space left on device")
        real = self._for_the_sweep("write_reg", full)
        n = len(self.lines)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        sb.write_reg = real
        self.assertEqual(self._sent(c2), 0)
        self.assertEqual(len([ln for ln in self.lines[n:] if "reg strip failed" in ln]), 1, self.lines[n:])
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self.assertEqual(self._sent(c2), 1)

    def test_a_sweep_whose_reg_will_not_read_sends_nothing_and_logs_it(self):
        c2 = self._the_kernels_wakeup()
        self._all_due()
        self._for_the_sweep("read_reg_for_rmw", lambda *a, **k: None)
        n = len(self.lines)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self.assertEqual(len([ln for ln in self.lines[n:] if "reg unreadable" in ln]), 1, self.lines[n:])
        self.assertEqual(self._sent(c2), 0)

    def test_a_sweep_from_a_listing_taken_before_the_session_was_marked_dead_sends_nothing(self):
        c2 = self._the_kernels_wakeup()
        self._all_due()
        stale = sb.list_regs(self.state)
        self._edit_reg(lambda reg: dict(reg, alive=False))
        real = sb.list_regs
        sb.list_regs = lambda *a, **k: stale
        self.addCleanup(setattr, sb, "list_regs", real)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        sb.list_regs = real
        self.assertEqual(self._sent(c2), 0)

    def _reads_by_a_sweep(self):
        real, reads = sb.read_reg_for_rmw, []
        self._for_the_sweep("read_reg_for_rmw", lambda *a, **k: reads.append(1) or real(*a, **k))
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        return len(reads)

    def test_a_sweep_reads_no_reg_while_the_wakeup_the_kernel_holds_is_not_due(self):
        self._the_kernels_wakeup()
        self.assertEqual(self._reads_by_a_sweep(), 0)

    def test_a_listed_wakeup_beside_an_entry_that_is_not_a_record_keeps_its_due_time_and_minute(self):
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.WAKE), "tu-wake", None))
        self._on_loop(s._stop_hook({"session_crons": ["not-a-record", self._listed()], "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertEqual([(c["id"], bool(c.get("dueEpoch")), bool(c.get("scheduledFor"))) for c in self._one_shots()],
                         [("a1b2c3d4", True, True)])

    def test_a_wakeup_a_picks_relaunch_left_to_the_kernel_is_delivered_once_after_a_later_stamp_that_cannot_read_it(self):
        s = self.s
        self._the_kernels_wakeup()
        armed_by = self._one_shots()[0]["procGen"]
        gen = s.proc_gen
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        self.assertTrue(self.be.set_effort(SID, "medium"))
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the second relaunch")
        c3 = self._Client.instances[2]
        self.assertEqual(s.proc_gen, gen, "the premise: the second stamp could not read the armed timers")
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # that CLI's turn end
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by], "kept at that turn end")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self.assertEqual(self._sent(c3), 1)

    # -- the dynamic /loop's own semantics over a wakeup the kernel now owns: a stop ends it, a new arm replaces it --
    LOOP = "run the next notes-api loop round"

    def _a_loop_round_then_a_relaunch(self, also=()):
        """A /loop round on the first CLI arms its next wakeup (the call-time record), and the turn's Stop hook lists it
        under the CLI's own id, which drops the call-time mark (a session_crons entry carries id, schedule, recurring and
        prompt, and no kind); `also` rides that payload. A quiet effort pick then relaunches the CLI."""
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.LOOP, "the next round"), "tu-wake", None))
        due = time.localtime(time.time() + 600)
        listed = {"id": "a1b2c3d4", "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": self.LOOP, "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [listed] + list(also), "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertEqual(sorted((c["id"], c.get("src"), bool(c.get("dueEpoch"))) for c in self._one_shots()),
                         sorted([("a1b2c3d4", None, True)] + [(c["id"], None, False) for c in also]),
                         "the premise: the CLI's own ids, no call-time mark left, the wakeup's exact due kept")
        self.assertTrue(self.be.set_effort(SID, "high"))
        return self._relaunched()

    def test_a_loop_stopped_on_the_fresh_cli_ends_the_wakeup_the_old_cli_armed(self):
        """ScheduleWakeup with stop ends the loop, and no further wakeup fires. After the relaunch the old CLI's wakeup is the
        kernel's to deliver, so the stop has to end it there too. A one-shot cron that CLI armed is not the loop's: the stop
        leaves it, and the fresh CLI's turn end drops it when that CLI does not list it."""
        cron = {"id": "job-1", "schedule": "30 14 * * *", "prompt": "post the notes-api status", "recurring": False}
        c2 = self._a_loop_round_then_a_relaunch(also=[cron])
        hourly = {"name": "notes-api-hourly", "schedule": "0 * * * *", "prompt": "check the notes-api queue"}
        self._on_loop(self.s._sched_tool_hook({"tool_name": "CronCreate", "tool_input": hourly}, "tu-cron", None))
        self._on_loop(self.s._sched_tool_hook({"tool_name": "ScheduleWakeup", "tool_input": {"stop": True}}, "tu-stop", None))
        self.assertEqual([c["id"] for c in (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or []],
                         ["job-1", "notes-api-hourly"], "the stop ends the loop's wakeup alone, never a cron")
        self._on_loop(self.s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # that turn's end
        self.assertEqual(self._one_shots(), [], "the loop's wakeup ended; the cron, not listed, dropped")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(c2.writes, [], "neither prompt")

    def test_a_loop_re_armed_on_the_fresh_cli_replaces_the_wakeup_the_old_cli_armed(self):
        """Arming a wakeup replaces the one pending, so a loop round on the fresh CLI leaves one wakeup, that CLI's, and the
        kernel sends no second round from the old CLI's record."""
        c2 = self._a_loop_round_then_a_relaunch()
        s = self.s
        self._on_loop(s._sched_tool_hook(_wake_call(900, self.LOOP, "the next round"), "tu-wake-2", None))
        due = time.localtime(time.time() + 900)
        self._on_loop(s._stop_hook({"session_crons": [{"id": "e5f6a7b8", "schedule": "%d %d * * *" % (due[4], due[3]),
                                                       "prompt": self.LOOP, "recurring": False}],
                                    "background_tasks": []}, None, None))
        self.assertEqual([(c["id"], c["procGen"]) for c in self._one_shots()], [("e5f6a7b8", s.proc_gen)],
                         "one wakeup, the fresh CLI's")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "the live CLI fires its own; nothing is the kernel's")
        self.assertEqual(c2.writes, [])


class ALoopPlaceholderWakeupIsNeverSentAsAMessage(_Harness):
    """An autonomous /loop (one given no task) passes one of Claude Code's placeholders as its wakeup's prompt, and only
    the scheduler of the CLI that armed the wakeup swaps it for the loop's instructions when the wakeup fires: CLI 2.1.282
    resolves it on that fire path alone (resolveLoopDefaultFire), so a user message carrying it reached the model as the
    bare placeholder, and the chat showed it as a message from the user. The kernel cannot carry such a wakeup across a
    relaunch: when the CLI that armed it is gone and the wakeup comes due, the lost-wakeup sweep strips it, sends nothing,
    and says in the error center that the loop could not be carried across the relaunch. Every road a fresh CLI comes by
    is covered (a setting's relaunch, Restart session, Apply now, a kernel restart), and on each an ordinary prompt is
    still sent exactly once."""

    WAKE = "check the notes-api build again"
    SENTINEL = "<<autonomous-loop-dynamic>>"                 # what an autonomous /loop passes to ScheduleWakeup

    def _one_shots(self):
        return [c for c in (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or [] if not c.get("recurring")]

    def _armed_then_replaced(self, road, prompt):
        """the first CLI arms a wakeup with `prompt` in a turn that ends, `road` replaces that CLI, and the fresh CLI's first
        turn end lists no timer; the fresh CLI's client, with every one-shot on record past due"""
        s, c1 = self.s, self._first_turn()
        if road == "apply now":
            self._with_a_background_task(c1)              # a pick waits for it, and Apply now pushes the pick through
        self._on_loop(s._sched_tool_hook(_wake_call(600, prompt), "tu-wake", None))
        self._end_turn(c1)
        if road == "pick":
            self.assertTrue(self.be.set_effort(SID, "high"))
        elif road == "restart":
            self.assertEqual(self.be.relaunch(SID), "")
        else:
            self.assertTrue(self.be.set_auth(SID, "login"))
            self._settle()
            self.assertEqual(len(self._Client.instances), 1, "the premise: the pick waits for the background task")
            self.assertEqual(self.be.apply_now(SID), "")
        c2 = self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's turn end
        self.assertEqual([c["prompt"] for c in self._one_shots()], [prompt], "the premise: the kernel holds the wakeup")
        self._all_due()
        return c2

    def _after_a_kernel_restart(self, *prompts):
        """wakeups an earlier session object recorded (their CLI ended with the old kernel), past due, and the session's
        fresh CLI in its first turn"""
        entries = [{"id": "toolhook-%d" % i, "cron": "", "prompt": p, "kind": "", "recurring": False,
                    "armedAt": time.time() - 660, "dueEpoch": time.time() - 60, "procGen": "gen-before-the-restart",
                    "src": "toolhook"} for i, p in enumerate(prompts, 1)]
        self._edit_reg(lambda reg: dict(reg, sessionCrons=entries))
        return self._first_turn()

    def _sent(self, c, text):
        """how many times `text` reached the CLI behind client `c` or waits in the session's queue for it"""
        return c.writes.count(text) + self.s.pending().count(text)

    def _loss_rows(self):
        return [p["text"] for p in self.be.problems() if "could not be carried across the relaunch" in p["text"]]

    def _not_sent(self, c, *prompts):
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "nothing delivered")
        self._settle(0.3)
        for p in prompts:
            self.assertEqual(self._sent(c, p), 0, "the placeholder never reaches the CLI as a message: %s" % p)
        self.assertEqual(self._one_shots(), [], "stripped, so no later sweep comes back to it")
        rows = self._loss_rows()
        self.assertEqual(len(rows), len(prompts), self.be.problems())
        for p, row in zip(prompts, rows):
            self.assertTrue(row.startswith("lost-wakeup (web): "), row)
            self.assertIn(p, row)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self.assertEqual(len(self._loss_rows()), len(prompts), "said once")

    def _sent_once(self, c, prompt):
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self._sent(c, prompt) == 1, "the wakeup's prompt, sent to the fresh CLI")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle(0.3)
        self.assertEqual(self._sent(c, prompt), 1, "exactly once")
        self.assertEqual(self._loss_rows(), [])

    def test_a_settings_relaunch_does_not_send_a_loops_placeholder_and_says_the_loop_stopped(self):
        self._not_sent(self._armed_then_replaced("pick", self.SENTINEL), self.SENTINEL)

    def test_a_restart_does_not_send_a_loops_placeholder_and_says_the_loop_stopped(self):
        self._not_sent(self._armed_then_replaced("restart", self.SENTINEL), self.SENTINEL)

    def test_apply_now_does_not_send_a_loops_placeholder_and_says_the_loop_stopped(self):
        self._not_sent(self._armed_then_replaced("apply now", self.SENTINEL), self.SENTINEL)

    def test_a_kernel_restart_sends_none_of_claude_codes_four_placeholders_and_says_each_loop_stopped(self):
        placeholders = ("<<autonomous-loop-dynamic>>", "<<autonomous-loop>>", "<<loop.md-dynamic>>", "<<loop.md>>")
        self._not_sent(self._after_a_kernel_restart(*placeholders), *placeholders)

    def test_a_settings_relaunch_still_sends_an_ordinary_wakeup_once(self):
        self._sent_once(self._armed_then_replaced("pick", self.WAKE), self.WAKE)

    def test_a_restart_still_sends_an_ordinary_wakeup_once(self):
        self._sent_once(self._armed_then_replaced("restart", self.WAKE), self.WAKE)

    def test_apply_now_still_sends_an_ordinary_wakeup_once(self):
        self._sent_once(self._armed_then_replaced("apply now", self.WAKE), self.WAKE)

    def test_a_kernel_restart_still_sends_an_ordinary_wakeup_once(self):
        self._sent_once(self._after_a_kernel_restart(self.WAKE), self.WAKE)

    def test_beside_a_loops_placeholder_an_ordinary_wakeup_is_still_sent_once(self):
        c1 = self._after_a_kernel_restart(self.SENTINEL, self.WAKE)
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self._sent(c1, self.WAKE) == 1, "the ordinary wakeup's prompt")
        self._settle(0.3)
        self.assertEqual((self._sent(c1, self.WAKE), self._sent(c1, self.SENTINEL)), (1, 0))
        self.assertEqual(len(self._loss_rows()), 1)


class ClaudeCodesLoopPlaceholders(unittest.TestCase):
    """is_loop_sentinel matches the CLI's own test (isLoopDefaultSentinel, CLI 2.1.282): an exact match on four strings, no
    pattern, so a prompt that only looks like one is an ordinary prompt, which the CLI's scheduler would also send as is."""

    def test_the_four_placeholders_match(self):
        for p in ("<<autonomous-loop>>", "<<autonomous-loop-dynamic>>", "<<loop.md>>", "<<loop.md-dynamic>>"):
            self.assertTrue(sb.is_loop_sentinel(p), p)

    def test_anything_else_does_not(self):
        for p in ("", "<<autonomous-loop-dynamic>> ", " <<autonomous-loop>>", "<<AUTONOMOUS-LOOP>>", "<<loop>>",
                  "<<autonomous-loop-dynamic>>\n", "run <<loop.md>> now", "check the notes-api build again", None, 7):
            self.assertFalse(sb.is_loop_sentinel(p), repr(p))


class AWakeupTheOldCliCouldHaveFiredIsDropped(_Harness):
    """The kernel cannot see whether the old CLI fired a wakeup before a relaunch replaced it, so at the fresh CLI a wakeup
    with a due time that a generation of this session object first recorded is dropped when the old CLI could have fired
    it by then, or when its record does not hold the minute the CLI's tool result named (wakeup_earliest_fire)."""

    WAKE = "check the notes-api build again"
    SENTINEL = "<<autonomous-loop-dynamic>>"                 # what an autonomous /loop passes to ScheduleWakeup
    EXPANDED = "# Autonomous loop check\n\nYou are running an autonomous loop. Review the notes-api work and decide the next step."

    def _one_shots(self):
        return [c for c in (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or [] if not c.get("recurring")]

    def _arm(self, c1, prompt, delay=600, listed=True):
        """the first turn arms a wakeup; its Stop hook lists it under the CLI's id with the cron's minute (`listed`), or
        the turn ends with no Stop hook"""
        s = self.s
        self._on_loop(s._sched_tool_hook(_wake_call(delay, prompt, "the next round"), "tu-wake", None))
        if listed:
            due = time.localtime(time.time() + delay)
            self._on_loop(s._stop_hook({"session_crons": [{"id": "a1b2c3d4", "schedule": "%d %d * * *" % (due[4], due[3]),
                                                           "prompt": prompt, "recurring": False}],
                                        "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertEqual([c["prompt"] for c in self._one_shots()], [prompt], "the premise: the wakeup on record")

    @staticmethod
    def _minute_start():
        return float(int(time.time()) // 60 * 60)

    def _as_armed(self, armed_at, delay, minute, cron=True):
        """the wakeup on record as if the CLI had armed it for `minute` (its tool result's scheduledFor) and the call-time
        hook had run at `armed_at` for `delay` s, with the cron's minute when the record has one (`cron`)"""
        def armed(reg):
            crons = []
            for c in reg.get("sessionCrons") or []:
                if not c.get("recurring"):
                    c = dict(c, armedAt=armed_at, dueEpoch=armed_at + delay, scheduledFor=minute)
                    if cron:
                        at = time.localtime(minute)
                        c["cron"] = "%d %d * * *" % (at[4], at[3])
                crons.append(c)
            return dict(reg, sessionCrons=crons)
        self._edit_reg(armed)

    def _fires(self, c, seen):
        """the old CLI fires the wakeup: its prompt hook sees `seen`, and the CLI opens a turn of its own"""
        self._on_loop(self.s._prompt_submit_hook({"prompt": seen}, None, None))
        self._init(c)
        self._push(c, _AssistantMessage([_TextBlock("running the scheduled check")], uuid=self._uid()))
        self._wait(lambda: self.s._cli_working, "the CLI's own turn open")

    def _restart_cutting(self, c1):
        self._restart_cutting_the_turn(c1)
        return self._relaunched()

    def _delivered(self, fresh):
        """how many wakeups the kernel delivers once every one-shot still on record is past due"""
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, dueEpoch=time.time() - 30)
                                                           for c in reg.get("sessionCrons") or []]))
        n = self.be.deliver_lost_wakeups()
        self._settle(0.3)
        return n

    def test_a_restart_cutting_a_turn_a_wakeup_started_delivers_nothing(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        m0 = self._minute_start()
        self._as_armed(m0 - 60, 60, m0)
        self._fires(c1, self.WAKE)
        c2 = self._restart_cutting(c1)
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_stopped_wakeup_turn_then_a_quiet_pick_delivers_nothing(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        m0 = self._minute_start()
        self._as_armed(m0 - 60, 60, m0)
        self._fires(c1, self.WAKE)
        self._result_frame(c1)                   # stopped: the turn ends with no Stop hook
        self._wait(lambda: not self.s._cli_working and self.s.inflight == 0, "the stopped turn ended")
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_restart_cutting_an_autonomous_loops_turn_delivers_nothing(self):
        c1 = self._first_turn()
        self._arm(c1, self.SENTINEL)
        m0 = self._minute_start()
        self._as_armed(m0 - 60, 60, m0)
        self._fires(c1, self.EXPANDED)           # the hook sees the CLI's own text for the sentinel
        c2 = self._restart_cutting(c1)
        self.assertEqual(self._delivered(c2), 0)
        self.assertEqual(c2.writes, [])

    def test_a_wakeup_fired_before_its_due_time_then_a_restart_delivers_nothing(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE, delay=300)
        m0 = self._minute_start()
        self._as_armed(m0 - 230, 300, m0)        # the cron's minute 70 s before the due time
        self.assertGreater(self._one_shots()[0]["dueEpoch"], time.time(), "the premise: not yet due")
        self._fires(c1, self.WAKE)
        c2 = self._restart_cutting(c1)
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_wakeup_with_no_stop_hook_record_fired_before_its_due_time_then_a_restart_delivers_nothing(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE, delay=300, listed=False)
        m0 = self._minute_start()
        self._as_armed(m0 - 230, 300, m0, cron=False)   # the minute 70 s before the due time
        self.assertEqual(self._one_shots()[0]["cron"], "", "the premise: no cron on record")
        self.assertGreater(self._one_shots()[0]["dueEpoch"], time.time(), "the premise: not yet due")
        self._fires(c1, self.WAKE)
        c2 = self._restart_cutting(c1)
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_past_due_wakeup_whose_prompt_the_hook_never_saw_is_dropped_at_a_quiet_picks_relaunch(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        m0 = self._minute_start()
        self._as_armed(m0 - 60, 60, m0)
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_restart_with_a_wakeup_not_yet_due_delivers_it_once(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 1)
        self._wait(lambda: c2.writes == [self.WAKE], "the wakeup's prompt, fed to the fresh CLI")

    def test_a_past_due_wakeup_an_earlier_session_object_recorded_is_delivered_after_the_first_connect(self):
        entry = {"id": "toolhook-1", "cron": "", "prompt": self.WAKE, "kind": "", "recurring": False,
                 "armedAt": time.time() - 660, "dueEpoch": time.time() - 60, "procGen": "gen-before-the-restart",
                 "src": "toolhook"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        c1 = self._first_turn()
        self.assertEqual([c["procGen"] for c in self._one_shots()], ["gen-before-the-restart"], "the first connect leaves it")
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in c1.writes, "its prompt fed")

    def test_a_past_due_wakeup_an_earlier_session_object_recorded_is_delivered_after_the_cli_fires_it_before_a_turn_end(self):
        """nothing on record ties the CLI to the wakeup's generation (no cliGen: an older kernel's registry, or a CLI with
        no host identity), so the sweep, run before any turn of that CLI ends, delivers it even though the CLI fires it
        itself: possibly twice, never lost. With the record, an attach to the same CLI takes its generation and the sweep
        leaves the wakeup to it (tests/test_pick_survives_restart.py, AWakeupTheSurvivingCliHoldsIsLeftToIt)"""
        t = time.time()
        entry = {"id": "toolhook-1", "cron": "", "prompt": self.WAKE, "kind": "", "recurring": False,
                 "armedAt": t - 610, "dueEpoch": t - 10, "scheduledFor": t - 10, "procGen": "gen-before-the-restart",
                 "src": "toolhook"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        s = self.s
        s.start()
        self._wait(lambda: s.client is not None, "the first connect")
        c = self._Client.instances[0]
        self._fires(c, self.WAKE)
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._result_frame(c)                    # the CLI's own turn ends
        self._wait(lambda: c.writes == [self.WAKE], "the kernel's copy fed")

    def test_a_wakeup_whose_call_time_hook_ran_late_fired_then_a_restart_delivers_nothing(self):
        """The call-time hook ran 20 s after the CLI's call, so the due time it recorded is 20 s late; the bound comes from
        the minute in the CLI's tool result."""
        c1 = self._first_turn()
        called = time.time() - 60.5                 # the CLI's call; it fired the wakeup at called + 60, half a second ago
        self._on_loop(self.s._sched_tool_hook(_wake_call(60, self.WAKE, "the next round", called=called), "tu-wake", None))
        self._end_turn(c1)
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, armedAt=called + 20, dueEpoch=called + 80)
                                                           for c in reg.get("sessionCrons") or []]))
        self.assertGreater(self._one_shots()[0]["dueEpoch"], time.time() + 10, "the premise: the recorded due time is ahead")
        self._fires(c1, self.WAKE)
        c2 = self._restart_cutting(c1)
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_an_unfired_wakeup_whose_minute_is_under_60_s_away_is_dropped_at_a_quiet_picks_relaunch(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE, delay=60)
        r = self._minute_start() + 60               # the next minute: under 60 s away, and the CLI has not fired it
        self._as_armed(r - 61, 60, r)
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_wakeup_whose_tool_result_named_no_minute_is_dropped_at_a_quiet_picks_relaunch(self):
        c1 = self._first_turn()
        self._on_loop(self.s._sched_tool_hook({"tool_name": "ScheduleWakeup",
                                               "tool_input": {"delaySeconds": 600, "prompt": self.WAKE}}, "tu-wake", None))
        self._end_turn(c1)
        self.assertNotIn("scheduledFor", self._one_shots()[0], "the premise: no minute on record")
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_listed_wakeup_whose_call_time_hook_ran_160_s_late_is_dropped_at_a_quiet_picks_relaunch(self):
        """The turn end takes a listed wakeup's due time and minute from its call-time record only while the due time that
        record holds is within 90 s of the listed minute."""
        s, c1 = self.s, self._first_turn()
        called = time.time() - 160
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.WAKE, "the next round", called=called), "tu-wake", None))
        at = time.localtime(-(-(called + 600) // 60) * 60)
        self._on_loop(s._stop_hook({"session_crons": [{"id": "a1b2c3d4", "schedule": "%d %d * * *" % (at[4], at[3]),
                                                       "prompt": self.WAKE, "recurring": False}],
                                    "background_tasks": []}, None, None))
        self._end_turn(c1)
        self.assertEqual([(c["id"], c.get("dueEpoch"), c.get("scheduledFor")) for c in self._one_shots()],
                         [("a1b2c3d4", None, None)], "the premise: listed with neither")
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's turn end
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def _a_fired_wakeup_then_a_restart_whose_stamp_fails(self, name, stand_in):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        m0 = self._minute_start()
        self._as_armed(m0 - 60, 60, m0)
        s, gen = self.s, self.s.proc_gen
        self._inside_the_hand_off(name, stand_in)
        rows = len(self.be.problems())
        self._fires(c1, self.WAKE)
        c2 = self._restart_cutting(c1)
        new = [p["text"] for p in self.be.problems()[rows:]]
        self.assertEqual(len([t for t in new if "armed timers" in t]), 1, new)
        self.assertEqual(s.proc_gen, gen, "the old generation kept")
        self.assertEqual(self._delivered(c2), 0)
        self.assertNotIn(self.WAKE, c2.writes)

    def test_a_stamp_that_cannot_read_the_armed_timers_files_a_problem_row_keeps_the_old_generation_and_delivers_nothing(self):
        self._a_fired_wakeup_then_a_restart_whose_stamp_fails("read_reg_for_rmw", lambda *a, **k: None)

    def test_a_stamp_whose_armed_timer_write_fails_files_a_problem_row_keeps_the_old_generation_and_delivers_nothing(self):
        def full(*a, **k):
            raise OSError("no space left on device")
        self._a_fired_wakeup_then_a_restart_whose_stamp_fails("write_reg", full)

    def test_a_sweep_that_read_the_registry_before_a_restarts_stamp_sends_nothing_the_old_cli_fired(self):
        c1 = self._first_turn()
        self._arm(c1, self.WAKE)
        r = time.time() - 30                        # the minute it fired at, 30 s ago
        self._as_armed(r - 60, 60, r)
        self._fires(c1, self.WAKE)
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "the premise: the running CLI's own")
        stale = sb.list_regs(self.state)            # the sweep's read of every reg, taken before the stamp
        self.assertEqual([c["prompt"] for r_ in stale if r_.get("sid") == SID for c in r_.get("sessionCrons") or []],
                         [self.WAKE], "the premise: that read lists the wakeup")
        c2 = self._restart_cutting(c1)
        real, reads = sb.list_regs, []
        sb.list_regs = lambda *a, **k: reads.append(1) or stale
        self.addCleanup(setattr, sb, "list_regs", real)
        n = self.be.deliver_lost_wakeups()
        sb.list_regs = real
        self.assertEqual(reads, [1], "the premise: the sweep went on from that read")
        self.assertEqual(n, 0)
        self._settle(0.3)
        self.assertNotIn(self.WAKE, c2.writes)

    CRON = "post the notes-api status"

    def _a_cron_the_old_cli_fired(self):
        """the old CLI listed a one-shot cron whose minute passed two minutes ago and fired it: its turn is open"""
        s, c1 = self.s, self._first_turn()
        t0 = time.localtime(time.time() - 120)
        cron = {"id": "job-1", "schedule": "%d %d * * *" % (t0[4], t0[3]), "prompt": self.CRON, "recurring": False}
        self._on_loop(s._stop_hook({"session_crons": [cron], "background_tasks": []}, None, None))
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, armedAt=time.time() - 600) for c in reg["sessionCrons"]]))
        self._end_turn(c1)
        self._fires(c1, self.CRON)
        return c1

    def _sweep_once(self, sweeps, sent):
        """a sweep on its own thread, as the producer runs it; the caller goes on once it is done or waiting"""
        if not sweeps:
            sweeps.append(threading.Thread(target=lambda: sent.append(self.be.deliver_lost_wakeups()), daemon=True))
            sweeps[0].start()
            sweeps[0].join(1.0)

    def _nothing_sent_by(self, c2, sweeps, sent):
        self.assertEqual(len(sweeps), 1, "the premise: _hand_one_shots_to's write started a sweep")
        sweeps[0].join(10)
        self.assertEqual(sent, [0])
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle(0.3)
        self.assertNotIn(self.CRON, c2.writes)

    def test_a_sweep_during_a_restarts_stamp_sends_nothing_the_old_cli_fired(self):
        """the sweep starts right after the stamp writes the armed timers: _hand_one_shots_to's write starts it, so a stamp
        with no such write starts none and fails the premise"""
        c1 = self._a_cron_the_old_cli_fired()
        real, sweeps, sent = sb.write_reg, [], []

        def write_then_sweep(*a, **k):
            real(*a, **k)
            self._sweep_once(sweeps, sent)
        self._inside_the_hand_off("write_reg", write_then_sweep)
        self._nothing_sent_by(self._restart_cutting(c1), sweeps, sent)

    def test_a_sweep_as_a_restarts_stamp_lets_go_of_the_registry_sends_nothing_the_old_cli_fired(self):
        """the sweep starts as the stamp releases the registry lock it wrote the armed timers under: _hand_one_shots_to's
        write arms it, so a stamp with no such write starts none and fails the premise"""
        c1 = self._a_cron_the_old_cli_fired()
        real, wrote, sweeps, sent = sb.write_reg, [], [], []
        lock = self.be._reg_lock

        def write(*a, **k):
            real(*a, **k)
            wrote.append(1)

        class Released:
            def __enter__(inner):
                lock.acquire()

            def __exit__(inner, *a):
                lock.release()
                if wrote:
                    self._sweep_once(sweeps, sent)
                return False
        self._inside_the_hand_off("write_reg", write)
        self.be._reg_lock = Released()
        self.addCleanup(setattr, self.be, "_reg_lock", lock)
        c2 = self._restart_cutting(c1)
        self.be._reg_lock = lock
        self._nothing_sent_by(c2, sweeps, sent)

    def test_an_apply_now_with_a_wakeup_not_yet_due_delivers_it_once(self):
        c1 = self._first_turn()
        self._with_a_background_task(c1)
        self._arm(c1, self.WAKE)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._settle()
        self.assertEqual(len(self._Client.instances), 1, "the premise: the pick waits for the background task")
        self.assertEqual(self.be.apply_now(SID), "")
        c2 = self._relaunched()
        self.assertEqual(self._delivered(c2), 1)
        self._wait(lambda: self.WAKE in c2.writes or self.WAKE in self.s.pending(),
                   "the wakeup's prompt, sent to the fresh CLI behind the notice of what was cut")


class TheEarliestAWakeupCouldHaveFired(unittest.TestCase):
    """wakeup_earliest_fire for each kind of entry on record"""

    def test_it_is_60_s_before_the_minute_the_clis_tool_result_named(self):
        r = 1790000040.0
        self.assertEqual(sb.wakeup_earliest_fire({"cron": "", "armedAt": r - 300, "dueEpoch": r + 20, "scheduledFor": r}),
                         r - 60)

    def test_an_entry_without_that_minute_has_none(self):
        t = 1790000000.0
        self.assertIsNone(sb.wakeup_earliest_fire({"cron": "", "armedAt": t, "dueEpoch": t + 600}))
        self.assertIsNone(sb.wakeup_earliest_fire({"cron": "30 14 * * *", "armedAt": t, "dueEpoch": None}))


class ARewindLeavesItsWakeupsToTheFreshCli(_Harness):
    """An edit or a delete relaunches the CLI on the rewind's own road (request_reconnect(cut=True, cause="rewind")), and
    that fresh CLI takes the one-shots a generation of this session object recorded, a wakeup a pick's relaunch left to
    the kernel included: the kernel delivers none of them, and the first Stop hook of the CLI holding them drops one it
    does not list, whether the rewind removed or kept the turn that armed it, and also when another relaunch comes before
    that Stop hook. A one-shot an earlier session object recorded is not handed over, also after the CLI listed it at a
    turn end."""

    WAKE = "check the notes-api build again"

    def _one_shots(self):
        return [c for c in (sb.read_reg(self.state, SID) or {}).get("sessionCrons") or [] if not c.get("recurring")]

    def _records(self):
        reg = sb.read_reg(self.state, SID) or {}
        with open(sb.transcript_path(self.cwd, str(reg.get("lastSid") or SID))) as f:
            return [json.loads(line)["uuid"] for line in f if line.strip()]

    def _arm(self):
        self._on_loop(self.s._sched_tool_hook(_wake_call(600, self.WAKE), "tu-wake", None))
        self.assertEqual([c["prompt"] for c in self._one_shots()], [self.WAKE], "the premise: the wakeup on record")

    def _stop(self, crons=()):
        self._on_loop(self.s._stop_hook({"session_crons": list(crons), "background_tasks": []}, None, None))

    def _listed(self):
        """the wakeup as a Stop hook payload lists it, under the CLI's own id"""
        due = time.localtime(time.time() + 600)
        return {"id": "a1b2c3d4", "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": self.WAKE, "recurring": False}

    def _nothing_delivered(self, fresh):
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "due, and the live CLI holds it: the kernel delivers nothing")
        self._stop()                             # that CLI's first turn end: it does not list the wakeup
        self.assertEqual(self._one_shots(), [], "that Stop hook drops it")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertNotIn(self.WAKE, fresh.writes)

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered(self):
        c1 = self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._arm()
        self._stop([self._listed()])
        self.assertEqual([(c["id"], bool(c.get("dueEpoch"))) for c in self._one_shots()], [("a1b2c3d4", True)],
                         "the premise: listed under the CLI's id, with the wakeup's exact due")
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        c2 = self._relaunched()
        self._nothing_delivered(c2)

    def test_a_wakeup_armed_in_a_turn_an_edit_keeps_is_not_delivered(self):
        c1 = self._first_turn()
        self._arm()                              # armed in the first turn, which the edit keeps
        self._end_turn(c1)
        self._stop([self._listed()])
        self.assertEqual([(c["id"], bool(c.get("dueEpoch"))) for c in self._one_shots()], [("a1b2c3d4", True)],
                         "the premise: listed under the CLI's id, with the wakeup's exact due")
        kept = self._records()[-1]
        self._turn(c1, "summarize the notes-api changes")
        self.assertEqual(self.be.rewind(SID, kept, "summarize the notes-api changes in one line"), (True, ""),
                         "edit the second message")
        c2 = self._relaunched()
        self._wait(lambda: c2.writes == ["summarize the notes-api changes in one line"], "the edit fed to the rewind's CLI")
        self._nothing_delivered(c2)

    def test_a_wakeup_a_quiet_picks_relaunch_left_to_the_kernel_is_not_delivered_after_a_rewind(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._arm()
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._turn(c2, "tighten the notes-api search")
        self._stop()                             # the fresh CLI's turn end keeps the old CLI's wakeup as the kernel's
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by],
                         "the premise: the wakeup is the kernel's, under the generation of the CLI that armed it")
        self.assertNotEqual(s.proc_gen, armed_by)
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn before the wakeup is due")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the rewind's relaunch")
        c3 = self._Client.instances[2]
        self._nothing_delivered(c3)

    def test_a_wakeup_a_quiet_picks_relaunch_left_to_the_kernel_is_not_delivered_after_a_delete_whose_stamp_cannot_read_it(self):
        """Its premise is the quiet pick's relaunch leaving the wakeup to the kernel, as
        test_a_quiet_sessions_wakeup_is_delivered_after_a_pick_relaunched_it pins; where that relaunch drops the wakeup, the
        premise fails."""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._arm()                              # armed in the turn the delete removes
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        c2 = self._relaunched()
        self._stop()                             # the fresh CLI's turn end keeps the old CLI's wakeup as the kernel's
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by],
                         "the premise: the wakeup is the kernel's, under the generation of the CLI that armed it")
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        gen, rows = s.proc_gen, len(self.be.problems())
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the rewind's relaunch")
        self.assertEqual((s.proc_gen, len([p for p in self.be.problems()[rows:] if "armed timers" in p["text"]])), (gen, 1),
                         "the premise: the stamp could not read the armed timers")
        self._nothing_delivered(self._Client.instances[2])

    def test_a_wakeup_a_delete_removed_is_dropped_at_a_later_relaunch_when_the_deletes_stamp_could_not_read_it(self):
        """the later relaunch comes before the delete's CLI ends a turn, after the wakeup's earliest fire"""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._arm()                              # armed in the turn the delete removes
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._relaunched()
        self._stop()                             # the fresh CLI's turn end keeps the old CLI's wakeup as the kernel's
        real = sb.read_reg_for_rmw
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        gen = s.proc_gen
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the rewind's relaunch")
        self.assertEqual(s.proc_gen, gen, "the premise: the delete's stamp could not read the armed timers")
        sb.read_reg_for_rmw = real
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, scheduledFor=time.time() + 30) for c in reg["sessionCrons"]]))
        self.assertTrue(self.be.set_effort(SID, "medium"))
        self._wait(lambda: len(self._Client.instances) == 4 and self._Client.instances[3] is s.client, "the later relaunch")
        self.assertEqual(self._one_shots(), [])
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertNotIn(self.WAKE, self._Client.instances[3].writes)

    def test_after_a_delete_whose_stamp_failed_a_later_picks_relaunch_still_leaves_its_wakeup_to_the_kernel(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        real = sb.read_reg_for_rmw
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        gen = s.proc_gen
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        c2 = self._relaunched()
        self.assertEqual(s.proc_gen, gen, "the premise: the delete's stamp could not read the armed timers")
        sb.read_reg_for_rmw = real
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "a pick's relaunch")
        c3 = self._Client.instances[2]
        self._arm()                              # armed on that CLI
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "medium"))
        self._wait(lambda: len(self._Client.instances) == 4 and self._Client.instances[3] is s.client, "the next relaunch")
        self.assertNotEqual(s.proc_gen, armed_by, "the premise: a new generation")
        self._stop()                             # that CLI's turn end
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in self._Client.instances[3].writes, "its prompt fed")
        self.assertNotIn(self.WAKE, c2.writes + c3.writes)

    def _recorded_under(self, gen):
        entry = {"id": "toolhook-1", "cron": "", "prompt": self.WAKE, "kind": "", "recurring": False,
                 "armedAt": time.time() - 60, "dueEpoch": time.time() + 600, "scheduledFor": time.time() + 600,
                 "procGen": gen, "src": "toolhook"}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))

    def _a_delete(self):
        c1 = self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn before the wakeup is due")
        return self._relaunched()

    def test_a_pending_wakeup_an_earlier_session_object_recorded_is_delivered_after_a_delete(self):
        self._recorded_under("gen-before-the-restart")
        c2 = self._a_delete()
        self._stop()                             # the rewind's CLI's first turn end
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in c2.writes, "its prompt fed")

    def test_a_wakeup_recorded_under_the_generation_the_session_object_was_born_with_is_not_delivered_after_a_delete(self):
        self._recorded_under(self.s.proc_gen)
        c2 = self._a_delete()
        self._nothing_delivered(c2)

    def _recorded_earlier_then_listed(self):
        """an earlier session object's wakeup on record, which the CLI this object attached to lists at a turn end"""
        self._recorded_under("gen-before-the-restart")
        c1 = self._first_turn()
        self._end_turn(c1)
        self._stop([self._listed()])
        self.assertEqual([c["id"] for c in self._one_shots()], ["a1b2c3d4"], "the premise: listed under the CLI's id")
        return c1

    def _delivered_once(self, fresh):
        self._stop()                             # that CLI's first turn end lists nothing
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in fresh.writes, "its prompt fed")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(fresh.writes.count(self.WAKE), 1)

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_delete(self):
        c1 = self._recorded_earlier_then_listed()
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._stop([self._listed()])             # that turn's end lists it again
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn before the wakeup is due")
        self._delivered_once(self._relaunched())

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_an_edit(self):
        c1 = self._recorded_earlier_then_listed()
        kept = self._records()[-1]
        self._turn(c1, "summarize the notes-api changes")
        self._stop([self._listed()])             # that turn's end lists it again
        edit = "summarize the notes-api changes in one line"
        self.assertEqual(self.be.rewind(SID, kept, edit), (True, ""), "edit the second message")
        c2 = self._relaunched()
        self._wait(lambda: c2.writes == [edit], "the edit fed to the rewind's CLI")
        self._init(c2)
        self._push(c2, _AssistantMessage([_TextBlock("on it")], uuid=self._uid()))
        self._wait(lambda: self.s._untaken is None and self.s.inflight == 1, "the edit's turn open")
        self._end_turn(c2)
        self._delivered_once(c2)

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_quiet_picks_relaunch(self):
        self._recorded_earlier_then_listed()
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._delivered_once(self._relaunched())

    def _earlier_listed_then(self, relaunch, due, minute, failing=False):
        """an earlier session object's wakeup due at `due`, with the minute the CLI named (`minute`) or none on record,
        which the CLI this object attached to lists at a turn end; then a quiet effort pick (`relaunch` "pick"), a Restart
        or a delete of a second turn, whose stamp cannot read the armed timers when `failing`: the next CLI's client"""
        entry = {"id": "toolhook-1", "cron": "", "prompt": self.WAKE, "kind": "", "recurring": False,
                 "armedAt": due - 600, "dueEpoch": due, "procGen": "gen-before-the-restart", "src": "toolhook"}
        if minute is not None:
            entry["scheduledFor"] = minute
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        at = time.localtime(due)
        self._stop([{"id": "a1b2c3d4", "schedule": "%d %d * * *" % (at[4], at[3]), "prompt": self.WAKE, "recurring": False}])
        self.assertEqual([c["id"] for c in self._one_shots()], ["a1b2c3d4"], "the premise: listed under the CLI's id")
        kept = self._records()[-1]
        if relaunch == "delete":
            self._turn(c1, "start the notes-api release loop")
        gen = s.proc_gen
        if failing:
            self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        if relaunch == "delete":
            self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        elif relaunch == "pick":
            self.assertTrue(self.be.set_effort(SID, "high"))
        else:
            self.assertEqual(self.be.relaunch(SID), "")
        fresh = self._relaunched()
        if failing:
            self.assertEqual(s.proc_gen, gen, "the premise: the stamp kept the old generation")
        return fresh

    def _in_the_minute(self, relaunch):
        """the minute 40 s away: the old CLI could have sent the wakeup by the fresh CLI's start, and it has not"""
        t = time.time() + 40
        return self._earlier_listed_then(relaunch, t, t)

    def _without_the_minute(self, relaunch):
        return self._earlier_listed_then(relaunch, time.time() + 600, None)

    def _with_a_stamp_that_cannot_read_it(self, relaunch):
        t = time.time() + 600
        return self._earlier_listed_then(relaunch, t, t, failing=True)

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_quiet_picks_relaunch_under_60_s_before_its_minute(self):
        self._delivered_once(self._in_the_minute("pick"))

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_restart_under_60_s_before_its_minute(self):
        self._delivered_once(self._in_the_minute("restart"))

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_delete_under_60_s_before_its_minute(self):
        self._delivered_once(self._in_the_minute("delete"))

    def test_a_wakeup_an_earlier_session_object_recorded_without_the_minute_and_the_cli_listed_is_delivered_once_after_a_quiet_picks_relaunch(self):
        self._delivered_once(self._without_the_minute("pick"))

    def test_a_wakeup_an_earlier_session_object_recorded_without_the_minute_and_the_cli_listed_is_delivered_once_after_a_restart(self):
        self._delivered_once(self._without_the_minute("restart"))

    def test_a_wakeup_an_earlier_session_object_recorded_without_the_minute_and_the_cli_listed_is_delivered_once_after_a_delete(self):
        self._delivered_once(self._without_the_minute("delete"))

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_quiet_picks_relaunch_whose_stamp_cannot_read_it(self):
        fresh = self._with_a_stamp_that_cannot_read_it("pick")
        self._stop()                             # that CLI's first turn end lists nothing
        self.assertEqual([(c["procGen"], "firstGen" in c) for c in self._one_shots()], [("gen-before-the-restart", False)],
                         "the record back under the earlier session object's generation")
        self._delivered_once(fresh)

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_after_a_delete_whose_stamp_cannot_read_it(self):
        self._delivered_once(self._with_a_stamp_that_cannot_read_it("delete"))

    def test_a_wakeup_an_earlier_session_object_recorded_and_the_cli_listed_is_delivered_once_due_before_the_first_turn_end_of_a_cli_whose_stamp_cannot_read_it(self):
        fresh = self._with_a_stamp_that_cannot_read_it("pick")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in fresh.writes, "its prompt fed")
        self._stop()                             # that CLI's first turn end lists nothing
        self.assertEqual(self._one_shots(), [])
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(fresh.writes.count(self.WAKE), 1)

    def test_after_a_stamp_that_cannot_read_it_an_earlier_session_objects_wakeup_a_later_turn_end_lists_is_left_to_that_cli(self):
        """the fresh CLI's first turn end does not list it; its next lists it under its id"""
        fresh = self._with_a_stamp_that_cannot_read_it("pick")
        self._stop()
        due = time.localtime(time.time() + 600)
        self._stop([{"id": "a1b2c3d4", "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": self.WAKE, "recurring": False}])
        self.assertEqual([c["procGen"] for c in self._one_shots()], [self.s.proc_gen], "the CLI that lists it owns it")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "due, but the CLI that lists it fires it")
        self._settle()
        self.assertNotIn(self.WAKE, fresh.writes)

    def _earlier_no_due_listed_then(self, relaunch, failing=False, prompt=None, cid="a1b2c3d4"):
        """an earlier session object's one-shot whose record holds no due time, which the CLI this object attached to lists
        at a turn end; then a quiet effort pick (`relaunch` "pick"), a Restart, an Apply now, a delete of a later turn or an
        edit of a later message, whose stamp cannot read the armed timers when `failing`: the next CLI's client"""
        prompt = prompt or self.WAKE
        at = time.localtime(time.time() + 600)
        cron = "%d %d * * *" % (at[4], at[3])
        entry = {"id": cid, "cron": cron, "prompt": prompt, "kind": "", "recurring": False,
                 "armedAt": time.time() - 100, "dueEpoch": None, "procGen": "gen-before-the-restart"}
        listed = {"id": cid, "schedule": cron, "prompt": prompt, "recurring": False}
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[entry]))
        s, c1 = self.s, self._first_turn()
        if relaunch == "apply":
            self._with_a_background_task(c1)
        self._end_turn(c1)
        self._stop([listed])
        self.assertEqual([(c["id"], c.get("dueEpoch")) for c in self._one_shots()], [(cid, None)],
                         "the premise: listed, with no due time")
        kept, edit = self._records()[-1], "start the notes-api release loop now"
        if relaunch in ("delete", "edit"):
            self._turn(c1, "start the notes-api release loop")
            self._stop([listed])                 # that turn's end lists it again
        gen = s.proc_gen
        if failing:
            self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        if relaunch == "pick":
            self.assertTrue(self.be.set_effort(SID, "high"))
        elif relaunch == "apply":
            self.assertTrue(self.be.set_effort(SID, "high"))
            self._settle()
            self.assertEqual(len(self._Client.instances), 1, "the premise: the pick waits for the background task")
            self.assertEqual(self.be.apply_now(SID), "")
        elif relaunch == "restart":
            self.assertEqual(self.be.relaunch(SID), "")
        elif relaunch == "delete":
            self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        else:
            self.assertEqual(self.be.rewind(SID, kept, edit), (True, ""), "edit the second message")
        fresh = self._relaunched()
        if relaunch == "edit":
            self._wait(lambda: fresh.writes == [edit], "the edit fed to the rewind's CLI")
            self._init(fresh)
            self._push(fresh, _AssistantMessage([_TextBlock("on it")], uuid=self._uid()))
            self._wait(lambda: s._untaken is None and s.inflight == 1, "the edit's turn open")
            self._end_turn(fresh)
        if failing:
            self.assertEqual(s.proc_gen, gen, "the premise: the stamp kept the old generation")
        return fresh

    def _its_minute_passes(self):
        """every one-shot on record past its minute, its record still holding no due time"""
        now = time.time()
        at = time.localtime(now - 120)
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[c if c.get("recurring") else
                                                           dict(c, cron="%d %d * * *" % (at[4], at[3]), armedAt=now - 400)
                                                           for c in reg.get("sessionCrons") or []]))

    def _sweeps(self):
        for _ in range(2):
            self.be.deliver_lost_wakeups()
            self._settle(0.3)

    def _kernel_sent(self, prompt):
        """the kernel's sends of `prompt`: fed to any of this session's CLIs, or still queued; a fed prompt counts once
        it reaches a client"""
        self._wait(lambda: any(prompt in c.writes for c in self._Client.instances)
                   or not any(prompt in str(f) for f in self.s.fed_texts()), "a fed prompt reaching its client")
        return (sum(c.writes.count(prompt) for c in self._Client.instances)
                + sum(1 for p in self.s.pending() if prompt in str(p)))

    def _then_its_minute(self):
        self._stop()                             # the next CLI's first turn end lists nothing
        self._its_minute_passes()
        self._sweeps()

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_quiet_picks_relaunch_whose_cli_does_not_list_it(self):
        self._earlier_no_due_listed_then("pick")
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_restart_whose_cli_does_not_list_it(self):
        self._earlier_no_due_listed_then("restart")
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_an_apply_now_whose_cli_does_not_list_it(self):
        self._earlier_no_due_listed_then("apply")
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_delete_whose_cli_does_not_list_it(self):
        self._earlier_no_due_listed_then("delete")
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_an_edit_whose_cli_does_not_list_it(self):
        self._earlier_no_due_listed_then("edit")
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_quiet_picks_relaunch_whose_stamp_cannot_read_it(self):
        self._earlier_no_due_listed_then("pick", failing=True)
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_restart_whose_stamp_cannot_read_it(self):
        self._earlier_no_due_listed_then("restart", failing=True)
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_once_after_a_delete_whose_stamp_cannot_read_it(self):
        self._earlier_no_due_listed_then("delete", failing=True)
        self._then_its_minute()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_listed_one_shot_with_no_due_time_is_sent_at_its_minute_before_the_first_turn_end_of_a_cli_whose_stamp_cannot_read_it(self):
        self._earlier_no_due_listed_then("pick", failing=True)
        self._its_minute_passes()
        self._sweeps()
        self.assertEqual(self._kernel_sent(self.WAKE), 1, "sent before that CLI ends a turn")
        self._stop()                             # that CLI's first turn end lists nothing
        self._sweeps()
        self.assertEqual(self._kernel_sent(self.WAKE), 1)

    def test_an_earlier_session_objects_daily_one_shot_cron_with_no_due_time_that_the_resumed_cli_re_arms_after_a_quiet_picks_relaunch_is_sent_twice_when_its_time_comes_before_the_fresh_clis_first_turn_end(self):
        """the kernel's send and the resumed CLI's own fire, after a quiet pick's relaunch"""
        cron = "post the notes-api status"
        fresh = self._earlier_no_due_listed_then("pick", prompt=cron, cid="job-4")
        self._its_minute_passes()                # its time comes, and the resumed CLI, which re-armed it, fires it
        answer = self._on_loop(self.s._prompt_submit_hook({"prompt": cron}, None, None))
        self.assertEqual(answer, {}, "the hook lets the resumed CLI's fire run")
        self._init(fresh)
        self._push(fresh, _AssistantMessage([_TextBlock("posting the status")], uuid=self._uid()))
        self._sweeps()
        self._result_frame(fresh)
        self._stop()                             # that turn's end lists nothing: the one-shot fired
        self._sweeps()
        cli_fires = 1                            # the turn the resumed CLI ran for it, driven above
        self.assertEqual(self._kernel_sent(cron) + cli_fires, 2, "the kernel's send and the resumed CLI's own fire")

    def test_a_wakeup_an_earlier_session_object_recorded_is_not_delivered_once_the_cli_that_listed_it_leaves_it_out(self):
        """no relaunch: that CLI's next turn end does not list it, as once it has sent it"""
        c1 = self._recorded_earlier_then_listed()
        self._turn(c1, "check the notes-api logs")
        self._stop()
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "the CLI that listed it held it")
        self.assertEqual(self._one_shots(), [])
        self._settle()
        self.assertNotIn(self.WAKE, c1.writes)

    def _left_by_a_restart_then_listed(self):
        """a wakeup this object armed, a Restart cutting the turn before its end, and the fresh CLI's turn end listing a
        one-shot with the wakeup's prompt and minute; then a turn on that CLI"""
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.WAKE, "the next round"), "tu-wake", None))
        self._restart_cutting_the_turn(c1)
        c2 = self._relaunched()
        due = time.localtime(time.time() + 600)
        self._stop([{"id": "job-5", "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": self.WAKE, "recurring": False}])
        self.assertEqual([(c["id"], bool(c.get("dueEpoch"))) for c in self._one_shots()], [("job-5", True)],
                         "the premise: the listed entry took the wakeup's due time")
        self._turn(c2, "start the notes-api release loop")
        return c2

    def _third_client(self):
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is self.s.client, "the relaunch")
        return self._Client.instances[2]

    def test_a_wakeup_this_session_object_recorded_and_a_later_cli_listed_is_dropped_at_a_quiet_picks_relaunch_under_60_s_before_its_minute(self):
        self._left_by_a_restart_then_listed()
        soon = time.time() + 30
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, dueEpoch=soon, scheduledFor=soon)
                                                           for c in reg["sessionCrons"]]))
        self.assertTrue(self.be.set_effort(SID, "high"))
        c3 = self._third_client()
        self._stop()                             # that CLI's first turn end lists nothing
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "its minute was 30 s away at the relaunch")
        self.assertEqual(self._one_shots(), [])
        self._settle()
        self.assertNotIn(self.WAKE, c3.writes)

    def test_a_wakeup_this_session_object_recorded_and_a_later_cli_listed_and_fired_is_not_delivered_after_a_restart_whose_stamp_cannot_read_it(self):
        c2 = self._left_by_a_restart_then_listed()
        s, gen, now = self.s, self.s.proc_gen, time.time()
        self._edit_reg(lambda reg: dict(reg, sessionCrons=[dict(c, dueEpoch=now, scheduledFor=now)
                                                           for c in reg["sessionCrons"]]))
        self._on_loop(s._prompt_submit_hook({"prompt": self.WAKE}, None, None))   # its minute: that CLI fires it
        self._init(c2)
        self._push(c2, _AssistantMessage([_TextBlock("running the scheduled check")], uuid=self._uid()))
        self._wait(lambda: s._cli_working, "the CLI's own turn open")
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        self._restart_cutting_the_turn(c2)
        c3 = self._third_client()
        self.assertEqual(s.proc_gen, gen, "the premise: the stamp kept the old generation")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 0, "the CLI that listed it fired it")
        self._stop()                             # the fresh CLI's first turn end lists nothing
        self.assertEqual(self._one_shots(), [])
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertNotIn(self.WAKE, c3.writes)

    def test_a_sweep_while_a_deletes_stamp_fails_delivers_nothing_a_quiet_picks_relaunch_left_to_the_kernel(self):
        """the sweep runs inside the call that logs the stamp's problem"""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._arm()                              # armed in the turn the delete removes
        armed_by = s.proc_gen
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._relaunched()
        self._stop()                             # the fresh CLI's turn end keeps the old CLI's wakeup as the kernel's
        self.assertEqual([c["procGen"] for c in self._one_shots()], [armed_by],
                         "the premise: the wakeup is the kernel's, under the generation of the CLI that armed it")
        self._all_due()
        swept, log = [], self.be._log

        def logging(msg, *a, **k):
            if "armed timers were not moved" in str(msg) and not swept:
                swept.append(self.be.deliver_lost_wakeups())
            return log(msg, *a, **k)
        self.be._log = logging
        self.addCleanup(setattr, self.be, "_log", log)
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the rewind's relaunch")
        self.assertEqual(swept, [0])
        self._nothing_delivered(self._Client.instances[2])

    def test_a_wakeup_a_restart_left_to_the_kernel_and_the_next_clis_turn_end_listed_is_not_delivered_after_a_delete(self):
        """a Restart cuts the turn that armed the wakeup before that turn's end; the fresh CLI's turn end lists a one-shot
        with the wakeup's prompt and minute; then a delete"""
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._sched_tool_hook(_wake_call(600, self.WAKE, "the next round"), "tu-wake", None))
        kept = self._records()[-1]
        self._restart_cutting_the_turn(c1)
        c2 = self._relaunched()
        due = time.localtime(time.time() + 600)
        self._stop([{"id": "job-5", "schedule": "%d %d * * *" % (due[4], due[3]), "prompt": self.WAKE, "recurring": False}])
        self.assertEqual([(c["id"], bool(c.get("dueEpoch"))) for c in self._one_shots()], [("job-5", True)],
                         "the premise: the listed entry took the wakeup's due time")
        self._turn(c2, "start the notes-api release loop")
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the rewind's relaunch")
        self._nothing_delivered(self._Client.instances[2])

    def test_a_later_fresh_cli_no_rewind_asked_for_leaves_the_rewind_clis_wakeup_to_the_kernel(self):
        c2 = self._a_delete()
        self._arm()                              # armed on the rewind's CLI
        gen = self.s.proc_gen
        self.s._fresh_cli_stamp(int(time.time()), "", mark_echoes=False)   # a later fresh CLI, with no teardown between
        self.assertNotEqual(self.s.proc_gen, gen, "the premise: a new generation")
        self.assertEqual([c["procGen"] for c in self._one_shots()], [gen], "left under the rewind CLI's generation")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in c2.writes, "its prompt fed")

    def _a_delete_then_a_relaunch_before_its_cli_ends_a_turn(self, armed_in, relaunch):
        """a wakeup armed in the turn a delete removes (`armed_in` "removed") or keeps ("kept"), listed at that turn's end;
        the delete; then, before the rewind's CLI ends a turn, a quiet effort pick (`relaunch` "pick") or a Restart: the
        next CLI's client"""
        s, c1 = self.s, self._first_turn()
        if armed_in == "kept":
            self._arm()
        self._end_turn(c1)
        if armed_in == "kept":
            self._stop([self._listed()])
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        if armed_in == "removed":
            self._arm()
            self._stop([self._listed()])
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        self._relaunched()
        if relaunch == "pick":
            self.assertTrue(self.be.set_effort(SID, "high"))
        else:
            self.assertEqual(self.be.relaunch(SID), "")
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the next relaunch")
        return self._Client.instances[2]

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_a_quiet_picks_relaunch_before_its_cli_ends_a_turn(self):
        self._nothing_delivered(self._a_delete_then_a_relaunch_before_its_cli_ends_a_turn("removed", "pick"))

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_a_restart_before_its_cli_ends_a_turn(self):
        self._nothing_delivered(self._a_delete_then_a_relaunch_before_its_cli_ends_a_turn("removed", "restart"))

    def test_a_wakeup_armed_in_a_turn_a_delete_keeps_is_not_delivered_after_a_quiet_picks_relaunch_before_its_cli_ends_a_turn(self):
        self._nothing_delivered(self._a_delete_then_a_relaunch_before_its_cli_ends_a_turn("kept", "pick"))

    def test_a_pending_wakeup_a_delete_removed_is_not_delivered_after_a_later_relaunch_when_the_deletes_stamp_could_not_read_it(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self._arm()                              # armed in the turn the delete removes, 600 s out
        self._stop([self._listed()])
        real = sb.read_reg_for_rmw
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        gen = s.proc_gen
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._relaunched()
        self.assertEqual(s.proc_gen, gen, "the premise: the delete's stamp could not read the armed timers")
        sb.read_reg_for_rmw = real
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "the later relaunch")
        self._nothing_delivered(self._Client.instances[2])

    def test_a_wakeup_the_rewind_clis_turn_end_lists_is_delivered_after_a_later_picks_relaunch(self):
        s, c1 = self.s, self._first_turn()
        self._arm()                              # armed in the first turn, which the delete keeps
        self._end_turn(c1)
        self._stop([self._listed()])
        kept = self._records()[-1]
        self._turn(c1, "summarize the notes-api changes")
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        self._relaunched()
        self._stop([self._listed()])             # the rewind's CLI's first turn end lists the wakeup
        listed_by = s.proc_gen
        self.assertEqual([c["procGen"] for c in self._one_shots()], [listed_by], "the premise: that CLI's own")
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "a pick's relaunch")
        c3 = self._Client.instances[2]
        self.assertEqual([c["procGen"] for c in self._one_shots()], [listed_by])
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in c3.writes, "its prompt fed")

    def test_a_wakeup_armed_after_the_turn_end_of_a_delete_whose_stamp_failed_is_delivered_after_a_picks_relaunch(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        real = sb.read_reg_for_rmw
        self._inside_the_hand_off("read_reg_for_rmw", lambda *a, **k: None)
        gen = s.proc_gen
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        c2 = self._relaunched()
        self.assertEqual(s.proc_gen, gen, "the premise: the delete's stamp could not read the armed timers")
        sb.read_reg_for_rmw = real
        self._turn(c2, "tighten the notes-api search")
        self._stop()                             # that turn's end
        self._arm()                              # armed on the delete's CLI after that turn end
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._wait(lambda: len(self._Client.instances) == 3 and self._Client.instances[2] is s.client, "a pick's relaunch")
        c3 = self._Client.instances[2]
        self._stop()                             # the next CLI's turn end
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1)
        self._wait(lambda: self.WAKE in c3.writes, "its prompt fed")


class ARewindsFreshCliTakesItsWakeupsWhenARestartOrApplyNowLandsDuringIt(_Harness):
    """A rewind's fresh CLI is the one whose launch applies a (target, leaf) pair with --resume-session-at that the CLI it
    replaces was not launched at, and its stamp hands it the one-shots this session object recorded, as
    ARewindLeavesItsWakeupsToTheFreshCli pins. A Restart or an Apply now that lands while a delete's relaunch is on its way
    renames the cut, so the notice the fresh CLI is fed names that gesture (_cut_cause). The roads: a Restart or an Apply
    now during the old CLI's teardown, a Restart during the relaunch's wait for the turn's end, and an Apply now while the
    relaunch waits for the turn's end; on the last two the gesture ends the turn at once by SIGTERM. The fresh CLI is still
    launched rewound, and a wakeup armed in the turn the delete removed is not delivered. Before, the stamp read the rewind
    from the renamed cause, took the fresh CLI for a plain relaunch's and left that wakeup to the kernel, which delivered it
    when it came due. The same holds when the CLI being replaced was itself launched for an earlier delete that no turn of
    it has settled yet: the second delete's launch is at a newer leaf. A later relaunch at the pair the replaced CLI was
    launched at is no rewind's (test_a_wakeup_the_rewind_clis_turn_end_lists_is_delivered_after_a_later_picks_relaunch).
    Nor is the relaunch of a delete whose flag it finds spent: a record landed past the leaf the delete recorded while the
    old CLI's teardown was held, so the launch applies no pair and the deleted turn stays, and a wakeup armed in that turn
    stays the kernel's, which delivers it once
    (test_a_delete_whose_flag_the_relaunch_finds_spent_resumes_plainly_and_the_wakeup_its_turn_armed_is_delivered_once).
    On every other road here the delete is applied, and each test checks that the fresh CLI is launched at the record the
    delete keeps: on an Apply now road during the teardown a delete made during a turn is armed by the settle of that
    turn's result, which comes with no Stop hook; on a road that waits for the turn's end the Stop hook arms it and the
    Restart's or the Apply now's SIGTERM ends the turn before any result.
    The REAL run loop against the stand-in client, whose transport process records the SIGTERM, and whose teardown can be
    held, as the EOF teardown of a CLI with a background agent running is, and as the exit of a CLI sent SIGTERM is until it
    comes; every id synthetic, every text invented."""

    WAKE = ARewindLeavesItsWakeupsToTheFreshCli.WAKE
    _one_shots = ARewindLeavesItsWakeupsToTheFreshCli._one_shots
    _records = ARewindLeavesItsWakeupsToTheFreshCli._records
    _arm = ARewindLeavesItsWakeupsToTheFreshCli._arm
    _stop = ARewindLeavesItsWakeupsToTheFreshCli._stop
    _listed = ARewindLeavesItsWakeupsToTheFreshCli._listed
    _nothing_delivered = ARewindLeavesItsWakeupsToTheFreshCli._nothing_delivered

    class _Client(ARestartEndsTheCliWithSigterm._Client):
        instances = []

        def __init__(self, options=None, transport=None):
            super().__init__(options, transport)
            self.hold, self.waiting, self.released = False, False, threading.Event()

        async def __aexit__(self, *a):
            if self.hold:                        # the teardown waits for the CLI's exit until the test lets it come
                self.waiting = True
                while not self.released.is_set():
                    await asyncio.sleep(0.005)
                self.waiting = False
            return await super().__aexit__(*a)

    def tearDown(self):
        for c in self._Client.instances:
            c.released.set()                     # a test that failed with a teardown held lets the loop end
        super().tearDown()

    def _client(self, k):
        """the k-th client, once it is the live one"""
        self._wait(lambda: len(self._Client.instances) == k and self._Client.instances[k - 1] is self.s.client,
                   "client %d" % k)
        return self._Client.instances[k - 1]

    def _rewound_at(self, c, kept):
        self.assertEqual((getattr(c.options, "extra_args", None) or {}).get("resume-session-at"), kept,
                         "the fresh CLI is launched rewound, at the record the delete keeps")

    def _open(self, c, text):
        """a turn fed to client `c` and opened, its record in the transcript: it runs"""
        s = self.s
        s.enqueue(text)
        self._wait(lambda: text in c.writes, "the turn fed")
        self._init(c)
        self._push(c, _AssistantMessage([_TextBlock("on it")], uuid=self._uid()))
        self._wait(lambda: s._untaken is None and s.inflight == 1, "the turn open")
        self._transcript_append(text)

    def _a_pick_waiting_for_a_background_agent(self, c):
        s = self.s
        self._task(c, "task_started", tid="a-sleeper", description="review the parser", task_type="local_agent")
        self._wait(lambda: s.live_work()[1] == 1, "the background agent registered")
        self.assertTrue(self.be.set_effort(SID, "high"))
        self._settle()
        self.assertEqual(s._effort_pending, "high", "the premise: the pick waits for the background agent")

    def _apply_now_during_the_held_teardown(self, c):
        """the delete's teardown of `c` is under way and held; Apply now renames the cut, then the teardown ends"""
        self._wait(lambda: c.waiting, "the delete's teardown under way and held")
        self.assertEqual(self.s._cut_cause, "rewind")
        self.assertEqual(self.be.apply_now(SID), "")
        self.assertEqual(self.s._cut_cause, "apply", "the premise: Apply now renamed the cut before the teardown took it")
        c.released.set()

    def _cut_during_the_turn_end_wait(self, c, gesture):
        """the delete's relaunch waits for the end of the turn `c` runs; a Restart (`gesture` "restart") or an Apply now
        ("apply") ends `c` with SIGTERM at once, and the exit that follows is held until the cause the teardown will take
        is read"""
        s = self.s
        self._wait(lambda: s._reconnect_when_idle, "the delete's relaunch waits for the turn's end")
        self.assertEqual((s._cut_cause, s.client), ("rewind", c), "the premise: no relaunch yet")
        seen = self._open_turns_at_sigterm(c)
        c.hold = True
        self.assertEqual(self.be.apply_now(SID) if gesture == "apply" else self.be.relaunch(SID), "")
        self._wait(lambda: c.waiting, "the exit of the CLI sent SIGTERM, held")
        self.assertEqual((c.events, len(seen), s._cut_cause), (["SIGTERM"], 1, gesture),
                         "the premise: the %s sent SIGTERM and renamed the cut before the teardown took it"
                         % ("Apply now" if gesture == "apply" else "Restart"))
        self.assertGreater(seen[0], 0, "the premise: the turn was in flight at the SIGTERM")
        c.released.set()

    def _a_delete_then_a_turn_on_its_cli(self):
        """a delete made while idle relaunches onto CLI 2, launched rewound; a turn on CLI 2 opens and arms the wakeup, and
        no turn of CLI 2 has settled, so that delete stays unconsumed. Returns CLI 2's client and the record the delete
        keeps, which a delete of that turn's message keeps as well"""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._turn(c1, "start the notes-api release loop")
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the second turn")
        c2 = self._client(2)
        self._rewound_at(c2, kept)
        self._open(c2, "start the notes-api deploy loop")
        self.assertEqual((s._rewind_armed, s._rewind_to), (True, kept),
                         "the premise: CLI 2 was launched for the delete, which no turn of it has consumed")
        self._arm()                              # armed in CLI 2's running turn, which the second delete removes
        return c2, kept

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_an_apply_now_during_the_deletes_teardown(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._open(c1, "start the notes-api release loop")
        self._arm()                              # armed in the second turn, which the delete removes
        self._stop([self._listed()])
        self._end_turn(c1)
        self._a_pick_waiting_for_a_background_agent(c1)
        c1.hold = True
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._apply_now_during_the_held_teardown(c1)
        c2 = self._client(2)
        self._rewound_at(c2, kept)
        note = sb.task_death_notice([{"desc": "review the parser", "type": "local_agent", "taskId": "a-sleeper"}],
                                    did=sb.CUT_BY["apply"])
        self._wait(lambda: c2.writes == [note], "the notice names Apply now")
        self._nothing_delivered(c2)
        self.assertEqual(len(self._Client.instances), 2, "one relaunch")

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_a_restart_while_the_delete_waits_for_the_turns_end(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._open(c1, "start the notes-api release loop")
        self._arm()                              # armed in the running turn, which the delete removes
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the running turn's message")
        self.assertTrue(s._rewind_wait, "the premise: a delete made during a turn waits for that turn's end")
        self._stop([self._listed()])             # that turn's Stop hook arms the delete's relaunch
        self._cut_during_the_turn_end_wait(c1, "restart")
        c2 = self._client(2)
        self._rewound_at(c2, kept)
        self._nothing_delivered(c2)
        self.assertEqual(len(self._Client.instances), 2, "one relaunch")

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_an_apply_now_while_the_delete_waits_for_the_turns_end(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._open(c1, "start the notes-api release loop")
        self._arm()                              # armed in the running turn, which the delete removes
        self._a_pick_waiting_for_a_background_agent(c1)
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the running turn's message")
        self.assertTrue(s._rewind_wait, "the premise: a delete made during a turn waits for that turn's end")
        self._stop([self._listed()])             # that turn's Stop hook arms the delete's relaunch
        self._cut_during_the_turn_end_wait(c1, "apply")
        c2 = self._client(2)
        self._rewound_at(c2, kept)
        self._nothing_delivered(c2)
        self.assertEqual(len(self._Client.instances), 2, "one relaunch")

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_an_apply_now_during_its_teardown_on_a_cli_an_unconsumed_delete_launched(self):
        s = self.s
        c2, kept = self._a_delete_then_a_turn_on_its_cli()
        first_leaf = s._rewind_leaf
        self._a_pick_waiting_for_a_background_agent(c2)
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the running turn's message")
        self.assertTrue(s._rewind_wait, "the premise: a delete made during a turn waits for that turn's end")
        c2.hold = True
        self._result_frame(c2)                   # the interrupted turn's result, with no Stop hook: its settle arms the delete
        self._apply_now_during_the_held_teardown(c2)
        c3 = self._client(3)
        self._rewound_at(c3, kept)
        self.assertNotEqual(s._rewind_leaf, first_leaf, "the premise: the second delete is at a newer leaf")
        self._nothing_delivered(c3)
        self.assertEqual(len(self._Client.instances), 3, "one relaunch for the second delete")

    def test_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered_after_a_restart_while_the_delete_waits_for_the_turns_end_on_a_cli_an_unconsumed_delete_launched(self):
        s = self.s
        c2, kept = self._a_delete_then_a_turn_on_its_cli()
        first_leaf = s._rewind_leaf
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the running turn's message")
        self.assertTrue(s._rewind_wait, "the premise: a delete made during a turn waits for that turn's end")
        self._stop([self._listed()])             # that turn's Stop hook arms the delete's relaunch
        self._cut_during_the_turn_end_wait(c2, "restart")
        c3 = self._client(3)
        self._rewound_at(c3, kept)
        self.assertNotEqual(s._rewind_leaf, first_leaf, "the premise: the second delete is at a newer leaf")
        self._nothing_delivered(c3)
        self.assertEqual(len(self._Client.instances), 3, "one relaunch for the second delete")

    def test_with_no_restart_or_apply_now_a_wakeup_armed_in_a_turn_a_delete_removes_is_not_delivered(self):
        c1 = self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._open(c1, "start the notes-api release loop")
        self._arm()
        self._stop([self._listed()])
        self._end_turn(c1)
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        c2 = self._client(2)
        self._rewound_at(c2, kept)
        self.assertEqual(c1.events, ["teardown"], "the delete's own teardown, no SIGTERM")
        self._nothing_delivered(c2)

    def test_a_delete_whose_flag_the_relaunch_finds_spent_resumes_plainly_and_the_wakeup_its_turn_armed_is_delivered_once(self):
        """a delete made while idle, whose teardown is held as the EOF teardown of a CLI with a background agent running
        is; a record lands during the hold, so the branch has grown past the leaf the delete recorded, and the relaunch's
        launch finds the flag spent: it resumes plainly, the deleted turn stays, and the fresh CLI is a plain relaunch's,
        which leaves the wakeup armed in that turn to the kernel"""
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        kept = self._records()[-1]
        self._open(c1, "start the notes-api release loop")
        self._arm()                              # armed in the turn the delete names
        self._stop([self._listed()])
        self._end_turn(c1)
        resolved = []
        self.be.rewind_resolved_cb = lambda sid, outcome: resolved.append(outcome)
        c1.hold = True
        self.assertEqual(self.be.rollback(SID, kept), (True, ""), "delete the turn that armed it")
        self._wait(lambda: c1.waiting, "the delete's teardown under way and held")
        self.assertEqual(self.be.pending_cut(SID), kept, "the premise: the delete's cut is pending")
        self._transcript_append("a record the old CLI writes during its teardown")   # the branch grows past the leaf
        c1.released.set()
        c2 = self._client(2)
        self.assertIsNone((getattr(c2.options, "extra_args", None) or {}).get("resume-session-at"),
                          "the flag is spent: the relaunch resumes plainly, at no record")
        self.assertEqual((resolved, s._rewind_to, s._rewind_armed), (["spent"], "", False), "the spent flag is dropped")
        self.assertEqual(self.be.pending_cut(SID), "", "no cut pending: the deleted turn stays in the conversation")
        self.assertEqual(c1.events, ["teardown"], "the delete's own teardown, no SIGTERM")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1,
                         "a plain relaunch's fresh CLI does not hold the wakeup: the kernel delivers it")
        self._wait(lambda: self.WAKE in c2.writes, "its prompt fed to the fresh CLI")
        self.assertEqual(self.be.deliver_lost_wakeups(), 0)
        self._settle()
        self.assertEqual(c2.writes.count(self.WAKE), 1, "delivered once")
        self.assertEqual(len(self._Client.instances), 2, "one relaunch")

    def test_a_restart_with_no_rewind_leaves_a_wakeup_not_yet_due_to_the_kernel_which_delivers_it_once(self):
        c1 = self._first_turn()
        self._arm()                              # armed in the first turn
        self._stop([self._listed()])
        self._end_turn(c1)
        self.assertEqual(self.be.relaunch(SID), "")
        c2 = self._client(2)
        self.assertEqual(((getattr(c2.options, "extra_args", None) or {}).get("resume-session-at"), c1.events),
                         (None, ["SIGTERM", "teardown"]), "the premise: a plain resume, after the Restart's SIGTERM")
        self._all_due()
        self.assertEqual(self.be.deliver_lost_wakeups(), 1, "the fresh CLI does not carry it: the kernel delivers it")
        self._wait(lambda: c2.writes == [self.WAKE], "the wakeup's prompt, fed to the fresh CLI")


class ARelaunchLeavesTheLaunchLedgerAlone(_Harness):
    """The launch ledger keeps the generation the session object was born with, never a fresh CLI's: a Monitor the old CLI
    launched keeps its entry through a relaunch, so the fresh CLI's first Stop hook files no processDied tombstone for it,
    and the kernel's awaiting lift finds no ending after a job stamp written before the relaunch."""

    def _a_monitor_then(self, relaunch):
        s, c1 = self.s, self._first_turn()
        self._on_loop(s._ledger_tool_hook({"tool_name": "Monitor",
                                           "tool_input": {"description": "watch the notes-api deploy log", "timeout_ms": 600000},
                                           "tool_response": {"task_id": "m-1"}}, "tu-mon", None))
        self._end_turn(c1)
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the old CLI's turn end
        self.assertEqual([e.get("tid") for e in (sb.read_reg(self.state, SID) or {}).get("bgLedger") or []], ["m-1"],
                         "the premise: a Monitor absent from the payload keeps its entry")
        stamp = time.time() - 5                  # a job stamp written before the relaunch
        relaunch()
        self._relaunched()
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the fresh CLI's first turn end
        reg = sb.read_reg(self.state, SID) or {}
        tombs = reg.get("bgLedgerEnded") or []
        self.assertEqual([e for e in tombs if e.get("why") == "processDied"], [], "no processDied tombstone")
        self.assertEqual(_kernel()._bg_ended_after([], tombs, int(reg.get("spawnedAt") or 0), stamp, time.time(), kind="job"),
                         0, "no ending after the job stamp")
        self.assertEqual([e.get("tid") for e in reg.get("bgLedger") or []], ["m-1"], "the entry stands")

    def test_a_quiet_picks_relaunch_files_no_processdied_tombstone_and_the_job_lift_finds_no_ending(self):
        self._a_monitor_then(lambda: self.assertTrue(self.be.set_effort(SID, "high")))

    def test_a_restart_files_no_processdied_tombstone_and_the_job_lift_finds_no_ending(self):
        self._a_monitor_then(lambda: self.assertEqual(self.be.relaunch(SID), ""))

    def test_a_task_adopted_from_a_stop_hook_payload_is_not_retired_as_processdied_at_that_clis_next_turn_end(self):
        s, c1 = self.s, self._first_turn()
        self._end_turn(c1)
        task = {"id": "task-zz99", "type": "shell", "status": "running", "description": "tail the notes-api log"}
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": [task]}, None, None))
        self.assertEqual([(e.get("tid"), e.get("src")) for e in (sb.read_reg(self.state, SID) or {}).get("bgLedger") or []],
                         [("task-zz99", "stopReconcile")], "the premise: adopted from the payload")
        self._on_loop(s._stop_hook({"session_crons": [], "background_tasks": []}, None, None))   # the same CLI's next turn end
        tombs = (sb.read_reg(self.state, SID) or {}).get("bgLedgerEnded") or []
        self.assertEqual([e for e in tombs if e.get("why") == "processDied"], [], "its CLI is the live one")


class WhatTheRelaunchWaitsFor(unittest.TestCase):
    def _sess(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "session-hosts"), "w") as f:
            f.write("off")
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        reg = {"sid": SID, "name": "web", "cwd": d, "alive": True}
        sb.write_reg(d, SID, reg)
        s = sb.SdkSession(be, reg)
        be.sessions[SID] = s
        return s

    def test_nothing_is_said_without_a_standing_ask(self):
        s = self._sess()
        s.inflight = 1
        self.assertEqual(s.relaunch_waits(), "")
        self.assertEqual(s.snapshot()["relaunchWaits"], "")

    def test_the_words_name_each_kind_and_count_an_agents_own_task_once(self):
        s = self._sess()
        s._switch_wanted = "billing"
        self.assertEqual(s.relaunch_waits(), "", "an ask the next event carries: nothing holds it")
        s._pending = ["one", "two"]
        self.assertEqual(s.relaunch_waits(), "2 queued messages")
        s._pending = []
        with s._sub_lock:
            s._subagents["a-1"] = {"type": "general-purpose", "since": 1}
            s._bg_tasks["a-1"] = {"desc": "the agent's own lifecycle task", "type": "local_agent", "since": 1}
            s._bg_tasks["t-1"] = {"desc": "watch", "type": "local_bash", "since": 1}
            s._bg_tasks["t-2"] = {"desc": "tail", "type": "local_bash", "since": 2}
        self.assertEqual(s.relaunch_waits(), "1 subagent and 2 background tasks")
        s.inflight = 1
        s.backend._pending_ask[SID] = {"kind": "permission"}
        self.assertEqual(s.relaunch_waits(), "the running turn, your answer to its question, 1 subagent and 2 background tasks")

    def test_what_applying_now_would_end_leaves_out_what_a_relaunch_keeps(self):
        s = self._sess()
        self.assertEqual(s.relaunch_cuts(), "", "no ask stands: nothing to push through")
        s._switch_wanted = "effort"
        s._pending = ["one", "two"]
        self.assertEqual(s.relaunch_waits(), "2 queued messages")
        self.assertEqual(s.relaunch_cuts(), "", "a queued message survives the relaunch: the fresh CLI takes it")
        s._untaken = {"text": "fed, not taken yet"}
        self.assertEqual(s.relaunch_waits(), "the running turn")
        self.assertEqual(s.relaunch_cuts(), "", "a fed text the CLI has not taken: the fresh CLI takes it first")
        s._untaken = None
        with s._sub_lock:
            s._bg_tasks["t-1"] = {"desc": "watch", "type": "local_bash", "since": 1}
        s.inflight = 1
        s.backend._pending_ask[SID] = {"kind": "picker"}
        self.assertEqual(s.relaunch_cuts(), "the running turn, its question to you and 1 background task")
        self.assertEqual(s.snapshot()["relaunchCuts"], "the running turn, its question to you and 1 background task")


if __name__ == "__main__":
    unittest.main()
