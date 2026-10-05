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

The REAL _amain runs here against a stand-in SDK module whose client records what it was fed (the harness of
tests/test_queued_sends_not_fused.py, trimmed to what these cases need). Every id is synthetic, every text invented.
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
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

SID = "11111111-2222-3333-4444-bbbbbbbbbb21"        # this module's own synthetic sid
FSID = "11111111-2222-3333-4444-ffffffffff21"       # the CLI's own session id, announced by the init


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
        self.assertTrue(any(l.startswith("apply now (web): idle, reconnecting now") for l in self.lines), self.lines)
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
        self.assertEqual(s.relaunch_cuts(), "", "a fed text the CLI has not taken: the relaunch waits for that turn's end")
        s._untaken = None
        with s._sub_lock:
            s._bg_tasks["t-1"] = {"desc": "watch", "type": "local_bash", "since": 1}
        s.inflight = 1
        s.backend._pending_ask[SID] = {"kind": "picker"}
        self.assertEqual(s.relaunch_cuts(), "the running turn, its question to you and 1 background task")
        self.assertEqual(s.snapshot()["relaunchCuts"], "the running turn, its question to you and 1 background task")


if __name__ == "__main__":
    unittest.main()
