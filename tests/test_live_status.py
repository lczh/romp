#!/usr/bin/env python3
"""Live status (2026-10-08): four indicators, each driven only by an authoritative Claude Code event.

  1. A thinking stretch (SdkSession._thinking): started by the first system/thinking_tokens frame, ended by the next
     main-thread assistant message, the result, an api_retry, an interrupt or the CLI going away; snapshot()'s
     thinkingLive reads it only inside a live turn, and says `quiet` once no frame has come for THINKING_QUIET_S.
     One push wake at the start and one at the end, never per frame.
  2. The exact thinking tokens of each API message on its first thinking row (kernel _thought_tokens, from
     usage.output_tokens_details.thinking_tokens), counted once per message id.
  3. A tool waiting for its result stamped `running` while its turn is live (kernel _stamp_running_tools), and each
     running subagent's task_progress (its last tool, tool-call and token counts) on its task row.
  4. Background tasks reconciled from system/background_tasks_changed, the CLI's full live set (REPLACE semantics), so
     a lost bookend or a late progress frame cannot leave a task reading "running".

The live fields reach the page through the liveStatus frame (kernel _live_status_frame / _send_live_status) and are
dropped from the chat-build key, so their churn costs no chat build. Synthetic data only (notes-api demo domain,
placeholder ids).
"""
import asyncio
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_livestatus", os.path.join(BIN, "romp_sdk_backend.py"))
km = load_source("romp_kernel_livestatus", os.path.join(BIN, "romp-kernel"))
jd = km.jd

SID = "11111111-2222-3333-4444-5555deadbeef"   # a private synthetic sid (no goals are minted here)
NOW = 1750000000


# --- duck-typed stream messages: the handler matches on the classes it is handed (no SDK needed) ---
class _Sys:
    def __init__(self, subtype, data=None):
        self.subtype, self.data = subtype, (data if data is not None else {})


class _Asst:
    def __init__(self, parent=None):
        self.content, self.model, self.uuid, self.stop_reason = [], "claude-x", "a1", None
        self.parent_tool_use_id, self.error, self.usage, self.message_id = parent, None, None, None


class _Res:
    uuid = "r1"
    subtype, is_error, num_turns, session_id, usage, total_cost_usd = "success", False, 1, "fsid", None, None


_Asst.__name__ = "AssistantMessage"
_Res.__name__ = "ResultMessage"


class _Client:
    async def interrupt(self):
        return None


def _session(td):
    (Path(td) / "session-hosts").write_text("off")   # a backend over a state root this test mints: no hosts
    be = sb.SdkBackend(td, "/bin/true", lambda *a, **k: None)
    s = sb.SdkSession(be, {"sid": SID, "name": "api", "cwd": td, "mode": "acceptEdits"})
    be._wakes = []
    be._wake_push = lambda: be._wakes.append(time.time())
    return s


def _feed(s, msg):
    return s._handle_stream_message(msg, _Asst, _Res, _Sys)


class _LoopCase(unittest.TestCase):
    """A turn-end result takes the real settle, which schedules refreshes on the current loop (never stepped here)."""

    def setUp(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.td = tempfile.mkdtemp()

    def tearDown(self):
        pending = asyncio.all_tasks(self._loop)
        for t in pending:
            t.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        asyncio.set_event_loop(None)
        self._loop.close()
        shutil.rmtree(self.td, ignore_errors=True)


class ThinkingStretch(_LoopCase):
    def _live(self):
        s = _session(self.td)
        s.client = _Client()
        s.inflight = 1
        return s

    def _think(self, s, n=1):
        for _ in range(n):
            _feed(s, _Sys("thinking_tokens", {"estimated_tokens": 40, "estimated_tokens_delta": 40}))

    def test_the_first_frame_starts_the_stretch_and_wakes_the_pusher_once(self):
        s = self._live()
        t0 = time.time()
        self._think(s, 5)
        row = s.snapshot()["thinkingLive"]
        self.assertIsNotNone(row, "a thinking_tokens frame inside a live turn starts the stretch")
        self.assertAlmostEqual(row["t"], int(t0), delta=1)
        self.assertNotIn("quiet", row, "frames are flowing: no quiet stamp")
        self.assertEqual(len(s.backend._wakes), 1, "one push at the start; later frames push nothing")

    def test_the_estimate_is_never_carried(self):
        s = self._live()
        self._think(s, 3)
        self.assertEqual(set(s.snapshot()["thinkingLive"]), {"t"}, "the frame's token estimate (up to 2x off) is not shown")

    def test_a_silent_stream_reads_quiet_with_its_last_frame(self):
        s = self._live()
        self._think(s)
        last = s._thinking["last"]
        self.assertNotIn("quiet", s._thinking_row(now=last + sb.THINKING_QUIET_S - 1))
        row = s._thinking_row(now=last + sb.THINKING_QUIET_S + 5)
        self.assertEqual(row["quiet"], int(last), "silence past the threshold names the last frame's time")
        self._think(s)   # a frame arrives again: no longer quiet
        self.assertNotIn("quiet", s._thinking_row(now=s._thinking["last"] + 1))

    def test_a_frame_outside_a_live_turn_starts_nothing(self):
        s = _session(self.td)
        self._think(s)
        self.assertIsNone(s._thinking, "no CLI client: nothing is thinking")
        s.client = _Client()
        self._think(s)
        self.assertIsNone(s._thinking, "no turn in flight")
        s.inflight = 1
        s._interrupted = True
        self._think(s)
        self.assertIsNone(s._thinking, "a stop under way")
        self.assertEqual(s.backend._wakes, [])

    def test_the_main_threads_next_assistant_message_ends_it(self):
        s = self._live()
        self._think(s)
        _feed(s, _Asst())
        self.assertIsNone(s._thinking)
        self.assertIsNone(s.snapshot()["thinkingLive"])
        self.assertEqual(len(s.backend._wakes), 2, "one push at the end")

    def test_a_subagents_message_does_not_end_the_main_threads_stretch(self):
        s = self._live()
        self._think(s)
        _feed(s, _Asst(parent="toolu_11111111"))
        self.assertIsNotNone(s.snapshot()["thinkingLive"], "a subagent's reply says nothing about the main conversation")

    def test_the_result_ends_it(self):
        s = self._live()
        self._think(s)
        _feed(s, _Res())   # the settle's finally runs whatever the bookkeeping did with a bare result
        self.assertIsNone(s._thinking)
        self.assertIsNone(s.snapshot()["thinkingLive"])

    def test_an_api_retry_ends_it_and_the_retry_card_owns_the_turn(self):
        s = self._live()
        self._think(s)
        _feed(s, _Sys("api_retry", {"attempt": 1, "max_retries": 10, "error_status": 529, "error": "overloaded"}))
        self.assertIsNone(s._thinking, "the request that was thinking failed")
        self._think(s)   # the retry's own frames start a new stretch…
        self.assertIsNotNone(s._thinking)
        self.assertIsNone(s.snapshot()["thinkingLive"], "…which the retrying state hides until real output clears it")

    def test_an_interrupt_ends_it(self):
        s = self._live()
        self._think(s)
        self._loop.run_until_complete(s._do_interrupt())
        self.assertIsNone(s._thinking)
        self.assertIsNone(s.snapshot()["thinkingLive"])

    def test_the_cli_going_away_ends_it(self):
        s = self._live()
        self._think(s)
        s.client = None          # the teardown's own clear sits beside this; the snapshot gates on the client too
        self.assertIsNone(s.snapshot()["thinkingLive"], "never outlives its CLI")
        s.client = _Client()
        s.inflight = 0           # …nor its turn
        self.assertIsNone(s.snapshot()["thinkingLive"])

    def test_a_compaction_hides_it(self):
        s = self._live()
        self._think(s)
        s._compacting = True
        self.assertIsNone(s.snapshot()["thinkingLive"], "a compaction wears its own live card")

    def test_the_teardown_clears_it_beside_the_client(self):
        src = Path(BIN, "romp_sdk_backend.py").read_text()
        self.assertIn("self.client = None\n                        self._thinking = None", src,
                      "the receive loop's teardown drops the stretch with the client it belonged to")

    def test_the_merged_row_carries_it_for_the_frame(self):
        rows = {SID: {"state": "working", "thinkingLive": {"t": NOW - 14}}}
        with mock.patch.object(km, "_backend_rows", side_effect=lambda name, be: rows if name == "sdk" else {}), \
                mock.patch.object(km, "_sdk", return_value=object()), mock.patch.object(km, "_codex", return_value=None):
            live = km.Sessions.live()
        self.assertEqual(live[SID]["thinkingLive"], {"t": NOW - 14})


class LiveStatusFrame(unittest.TestCase):
    def test_the_frame_carries_thinking_and_each_subagents_progress_by_its_call(self):
        tm = {"state": "working", "thinkingLive": {"t": NOW - 14, "quiet": NOW - 3},
              "bgTasks": [{"toolUseId": "toolu_A", "taskId": "a1111111", "lastTool": "Bash",
                           "agentProgress": {"calls": 12, "tokens": 41000}},
                          {"toolUseId": "toolu_B", "taskId": "b2222222", "lastTool": ""},
                          {"toolUseId": "", "taskId": "c3333333", "agentProgress": {"calls": 2}}]}
        f = km._live_status_frame(SID, tm)
        self.assertEqual(f, {"type": "liveStatus", "id": SID, "thinking": {"t": NOW - 14, "quiet": NOW - 3},
                             "tasks": {"toolu_A": {"tool": "Bash", "calls": 12, "tokens": 41000}}})
        self.assertEqual(km._live_status_frame(SID, None), {"type": "liveStatus", "id": SID, "thinking": None, "tasks": {}})

    def test_live_fields_never_move_the_chat_build_key(self):
        base = {"state": "working", "since": 5, "bgTasks": [{"toolUseId": "toolu_A", "taskId": "a1", "desc": "d",
                                                              "type": "local_agent", "since": 1}]}
        moved = json.loads(json.dumps(base))
        moved["thinkingLive"] = {"t": NOW, "quiet": NOW + 12}
        moved["bgTasks"][0].update(lastTool="Read", agentProgress={"calls": 3, "tokens": 900})
        self.assertEqual(km._chat_row_sig(base), km._chat_row_sig(moved),
                         "a thinking stretch or a progress frame rebuilds no chat payload")

    def test_send_goes_only_when_something_is_live_and_clears_once(self):
        out = []
        c = {"app": "chat", "send": out.append, "sent": {}}
        live = {SID: {"state": "working"}}
        km._send_live_status(c, km._live_status_frames(live)[0], {SID})
        self.assertEqual(out, [], "an idle board puts no frame on the wire")
        live[SID]["thinkingLive"] = {"t": NOW}
        km._send_live_status(c, km._live_status_frames(live)[0], {SID})
        km._send_live_status(c, km._live_status_frames(live)[0], {SID})
        self.assertEqual(len(out), 1, "the start goes once; an unchanged status is deduped")
        self.assertEqual(json.loads(out[0])["thinking"], {"t": NOW})
        live.pop(SID)   # the CLI went away: the row left the map
        km._send_live_status(c, km._live_status_frames(live)[0], set())
        self.assertEqual(len(out), 2)
        self.assertIsNone(json.loads(out[1])["thinking"], "the frame that clears it reaches a client shown something")
        km._send_live_status(c, km._live_status_frames(live)[0], set())
        self.assertEqual(len(out), 2, "…and then nothing more")
        self.assertNotIn(("liveStatus", SID), c["sent"])
        self.assertEqual(c["liveShown"], set())

    def test_frames_are_built_once_per_push_whatever_the_number_of_clients(self):
        # the second review of #171: ~5.6 ms a push at 130 sessions x 7 clients when each client rebuilt every frame
        live = {"11111111-2222-3333-4444-%012d" % i: {"state": "working"} for i in range(130)}
        live["11111111-2222-3333-4444-000000000007"]["thinkingLive"] = {"t": NOW}
        calls = []
        real = km._live_status_frame
        with mock.patch.object(km, "_live_status_frame", side_effect=lambda *a: calls.append(a) or real(*a)):
            frames = km._live_status_frames(live)
            clients = [{"app": "chat", "send": lambda s: None, "sent": {}} for _ in range(7)]
            for c in clients:
                km._send_live_status(c, frames[0], set(live))
        self.assertEqual(len(calls), 1, "one frame built: the one session with something live, once for every client")

    def test_the_sessions_pane_gets_thinking_and_never_progress(self):
        live = {SID: {"state": "working", "bgTasks": [{"toolUseId": "toolu_A", "taskId": "a1", "lastTool": "Bash",
                                                        "agentProgress": {"calls": 2}}]}}
        chat, pane = km._live_status_frames(live)
        self.assertIn(SID, chat)
        self.assertNotIn(SID, pane, "a progress-only status is nothing the Sessions pane draws: no frame")
        live[SID]["thinkingLive"] = {"t": NOW}
        chat, pane = km._live_status_frames(live)
        self.assertEqual(pane[SID][0]["tasks"], {}, "the pane's twin carries the thinking alone")
        self.assertEqual(pane[SID][0]["thinking"], {"t": NOW})


class BackgroundLevel(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.s = _session(self.td)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def _ev(self, subtype, data):
        _feed(self.s, _Sys(subtype, data))

    def _ids(self):
        return sorted(self.s._bg_tasks)

    def test_a_task_the_level_no_longer_lists_has_ended_even_without_its_bookend(self):
        self._ev("task_started", {"task_id": "b1", "tool_use_id": "toolu_b1", "description": "watch the deploy",
                                  "task_type": "local_bash", "is_backgrounded": True})
        self._ev("task_started", {"task_id": "b2", "tool_use_id": "toolu_b2", "description": "tail the logs",
                                  "task_type": "monitor"})
        self._ev("background_tasks_changed", {"tasks": [{"task_id": "b1", "task_type": "local_bash", "description": "watch the deploy"},
                                                        {"task_id": "b2", "task_type": "monitor", "description": "tail the logs"}]})
        self.assertEqual(self._ids(), ["b1", "b2"])
        # b1's notification is lost (a TaskStop can suppress it); the level is the truth
        self._ev("background_tasks_changed", {"tasks": [{"task_id": "b2", "task_type": "monitor", "description": "tail the logs"}]})
        self.assertEqual(self._ids(), ["b2"])
        self.assertEqual([t["desc"] for t in sb.read_reg(self.s.backend.state_dir, SID)["bgTasks"]], ["tail the logs"],
                         "the registry mirror follows, so a later boot does not report a dead task")
        self._ev("background_tasks_changed", {"tasks": []})
        self.assertEqual(self._ids(), [], "an empty level ends every background task")

    def test_a_late_progress_frame_never_brings_an_ended_task_back(self):
        self._ev("task_started", {"task_id": "b1", "tool_use_id": "toolu_b1", "description": "watch", "is_backgrounded": True})
        self._ev("background_tasks_changed", {"tasks": []})
        self._ev("task_progress", {"task_id": "b1", "description": "watch", "usage": {"tool_uses": 1, "total_tokens": 10,
                                                                                       "duration_ms": 5}})
        self.assertEqual(self._ids(), [], "progress chatter after the end is late, not a restart")
        self._ev("task_notification", {"task_id": "b1", "status": "completed", "output_file": "/tmp/x", "summary": "done"})
        self.assertEqual(self._ids(), [])
        self._ev("task_started", {"task_id": "b1", "tool_use_id": "toolu_b1", "description": "watch again"})
        self.assertEqual(self._ids(), ["b1"], "an explicit start brings an id back (a resumed agent reuses its id)")

    def test_an_id_the_level_names_is_live_and_learns_its_call_from_the_bookend(self):
        # in practice the level precedes the bookends
        self._ev("background_tasks_changed", {"tasks": [{"task_id": "b9", "task_type": "local_agent", "description": "review the diff"}]})
        self.assertEqual(self._ids(), ["b9"])
        self.assertEqual(self.s._bg_tasks["b9"]["desc"], "review the diff")
        self._ev("task_started", {"task_id": "b9", "tool_use_id": "toolu_b9", "description": "review the diff",
                                  "task_type": "local_agent", "is_backgrounded": True})
        self.assertEqual(self.s._bg_tasks["b9"]["toolUseId"], "toolu_b9", "stop_task and the chat join need the call's id")
        self.assertEqual([t["toolUseId"] for t in sb.read_reg(self.s.backend.state_dir, SID)["bgTasks"]], ["toolu_b9"],
                         "…and the registry mirror learns it too")

    def test_a_foreground_task_is_left_to_its_bookends_until_it_is_backgrounded(self):
        self._ev("task_started", {"task_id": "f1", "tool_use_id": "toolu_f1", "description": "explore the router",
                                  "task_type": "local_agent", "is_backgrounded": False})
        self._ev("background_tasks_changed", {"tasks": []})
        self.assertEqual(self._ids(), ["f1"], "a synchronous agent is never in the background level")
        self._ev("task_updated", {"task_id": "f1", "patch": {"is_backgrounded": True}})
        self._ev("background_tasks_changed", {"tasks": [{"task_id": "f1", "task_type": "local_agent", "description": "explore the router"}]})
        self.assertEqual(self._ids(), ["f1"])
        self._ev("background_tasks_changed", {"tasks": []})
        self.assertEqual(self._ids(), [], "backgrounded, the level speaks for it")

    def test_a_foreground_task_survives_a_restart_and_the_first_level_after_it(self):
        # the registry mirror carries the foreground flag, so the attach's seed restores it and the CLI's first background
        # level after the restart (which never lists a foreground task) does not end it (the second review of #171)
        self._ev("task_started", {"task_id": "f1", "tool_use_id": "toolu_f1", "description": "explore the router",
                                  "task_type": "local_agent", "is_backgrounded": False})
        mirror = sb.read_reg(self.s.backend.state_dir, SID)["bgTasks"]
        self.assertEqual([(t["taskId"], t.get("fg")) for t in mirror], [("f1", True)])
        fresh = sb.SdkSession(self.s.backend, {"sid": SID, "name": "api", "cwd": self.td, "mode": "acceptEdits"})
        self.s.backend._seed_surviving_work(fresh)
        self.assertEqual(sorted(fresh._bg_tasks), ["f1"])
        _feed(fresh, _Sys("background_tasks_changed", {"tasks": []}))
        self.assertEqual(sorted(fresh._bg_tasks), ["f1"], "still the foreground agent's own bookends' to end")

    def test_a_level_end_retires_the_agent_with_its_task(self):
        self.s._subagents["a1111111"] = {"type": "general-purpose", "since": NOW}
        self._ev("background_tasks_changed", {"tasks": [{"task_id": "a1111111", "task_type": "local_agent", "description": "d"}]})
        self._ev("background_tasks_changed", {"tasks": []})
        self.assertNotIn("a1111111", self.s._subagents, "a Task agent's task id IS its agent id: its end is the agent's end")

    def test_progress_lands_on_the_task_row_for_the_frame(self):
        self._ev("task_started", {"task_id": "a1", "tool_use_id": "toolu_a1", "description": "write the tests",
                                  "task_type": "local_agent", "is_backgrounded": False})
        self._ev("task_progress", {"task_id": "a1", "description": "write the tests", "last_tool_name": "Edit",
                                   "usage": {"tool_uses": 7, "total_tokens": 18234, "duration_ms": 41000}})
        row = self.s.snapshot()["bgTasks"][0]
        self.assertEqual((row["lastTool"], row["agentProgress"]), ("Edit", {"calls": 7, "tokens": 18234}))
        f = km._live_status_frame(SID, {"bgTasks": [row]})
        self.assertEqual(f["tasks"], {"toolu_a1": {"tool": "Edit", "calls": 7, "tokens": 18234}})
        self.assertEqual(len(self.s.backend._wakes), 0, "progress chatter wakes nothing; the pusher's cycle reads it")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(t, uuid, content, parent=None):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main", "message": {"role": "user", "content": content}}


def _arec(t, uuid, blocks, parent, mid=None, think=None, stop=None):
    m = {"role": "assistant", "model": "claude-fable-5-1", "content": blocks}
    if stop:
        m["stop_reason"] = stop
    if mid:
        m["id"] = mid
    if think is not None:
        m["usage"] = {"input_tokens": 12, "output_tokens": think + 40, "output_tokens_details": {"thinking_tokens": think}}
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": SID,
            "cwd": "/tmp/notes-api", "version": "2.0.0", "gitBranch": "main", "message": m}


def _tool(tid, cmd):
    return {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": cmd, "description": "run " + cmd}}


def _result(tid, text="ok"):
    return [{"type": "tool_result", "tool_use_id": tid, "content": text}]


class _BuildCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.mkdtemp()
        self._saved_state = jd.STATE
        jd._rebind_state(Path(self._td))
        self.path = Path(self._td) / "transcript.jsonl"

    def tearDown(self):
        jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, recs):
        self.path.write_text("".join(json.dumps(r) + "\n" for r in recs))

    def _events(self, row):
        m = km.build_session(SID, NOW, live_map={SID: row}, path_override=str(self.path))
        self.assertIsNotNone(m)
        return m["events"]


class ThoughtTokens(_BuildCase):
    def test_each_messages_exact_figure_rides_its_first_thinking_row_once(self):
        self._write([
            _urec(NOW - 100, "u1", "add pagination to the notes list"),
            # one API message, two thinking blocks: the CLI writes a record per block, each with the message's usage
            _arec(NOW - 95, "a1", [{"type": "thinking", "thinking": "Where is the list handler?"}], "u1", mid="msg_1", think=300),
            _arec(NOW - 94, "a2", [{"type": "thinking", "thinking": "And its tests."}], "a1", mid="msg_1", think=300),
            _arec(NOW - 93, "a3", [_tool("toolu_1", "ls")], "a2", mid="msg_1", think=300),
            _urec(NOW - 90, "u2", _result("toolu_1"), "a3"),
            _arec(NOW - 85, "a4", [{"type": "thinking", "thinking": "Now the cursor."}], "u2", mid="msg_2", think=120),
            _arec(NOW - 84, "a5", [{"type": "text", "text": "Pagination added."}], "a4", mid="msg_2", think=120),
            # a record with no message id (an older transcript): the row says nothing rather than guess
            _arec(NOW - 80, "a6", [{"type": "thinking", "thinking": "Double-check."}], "a5", think=50),
        ])
        th = [e for e in self._events({}) if e.get("kind") == "thinking"]
        self.assertEqual([e.get("thinkTokens") for e in th], [300, None, 120, None])

    def test_the_message_id_survives_normalization(self):
        self.assertEqual(km.em._norm_message({"role": "assistant", "id": "msg_9", "content": []})["id"], "msg_9")


class RunningTools(_BuildCase):
    def _recs(self):
        return [
            _urec(NOW - 100, "u1", "run the notes-api tests"),
            _arec(NOW - 95, "a1", [_tool("toolu_A", "pytest -q")], "u1", mid="msg_1"),
            _urec(NOW - 90, "u2", _result("toolu_A"), "a1"),
            _arec(NOW - 85, "a2", [_tool("toolu_B", "pytest -q tests/test_api.py")], "u2", mid="msg_2"),
        ]

    def _tools(self, row):
        return {e["toolUseId"] if e.get("toolUseId") else e.get("uuid"): e for e in self._events(row) if e.get("kind") == "tool"}

    def _states(self, *recs):
        """The session's state log (the event model's turn-end source: a "waiting" record is a result, "idle" an
        interrupt), at the path the kernel's parse reads it (STATE/states/<sid>.jsonl)."""
        d = Path(self._td) / "states"
        d.mkdir(exist_ok=True)
        (d / (SID + ".jsonl")).write_text("".join(json.dumps({"t": t, "state": st}) + "\n" for t, st in recs))

    def test_a_tool_waiting_in_the_live_turn_is_running(self):
        self._write(self._recs())
        self._states((NOW - 100, "working"))
        evs = [e for e in self._events({"state": "working"}) if e.get("kind") == "tool"]
        self.assertEqual(len(evs), 2)
        done, waiting = evs
        self.assertNotIn("running", done, "a tool whose result landed carries no flag")
        self.assertIs(waiting["running"], True)

    def test_a_tool_waiting_on_a_permission_prompt_is_still_running(self):
        # the session reports the prompt as its state while its turn stays open: the call's result is still coming
        self._write(self._recs())
        self._states((NOW - 100, "working"), (NOW - 84, "permission"))
        for state in ("permission", "picker"):
            waiting = [e for e in self._events({"state": state}) if e.get("kind") == "tool"][-1]
            self.assertIs(waiting["running"], True, "%s: an approval box open over the call is no ended turn" % state)

    def test_a_restart_before_the_re_attach_does_not_end_it(self):
        # after a kernel restart the dormant row reads "waiting" until the session re-attaches to its live CLI; the turn
        # is still open in the transcript and its state log, so the call keeps running instead of flapping to "no result"
        self._write(self._recs())
        self._states((NOW - 100, "working"))
        for row in ({"state": "waiting"}, None):
            waiting = [e for e in self._events(row) if e.get("kind") == "tool"][-1]
            self.assertIs(waiting["running"], True, "row %r: nothing ended the turn" % (row,))

    def test_it_stops_running_when_the_turn_truly_ended(self):
        # a result ("waiting" in the state log) or an interrupt ("idle") ends the turn: the result can no longer come
        for end in ("waiting", "idle"):
            self._write(self._recs())
            self._states((NOW - 100, "working"), (NOW - 80, end))
            waiting = [e for e in self._events({"state": "waiting"}) if e.get("kind") == "tool"][-1]
            self.assertIs(waiting["running"], False, "%s: the turn ended" % end)

    def test_an_orphan_in_an_older_turn_never_runs_again(self):
        # the turn holding toolu_B ended without its result (a cut CLI), and a later turn is working now
        recs = self._recs() + [_arec(NOW - 70, "a2b", [{"type": "text", "text": "Stopping here."}], "a2", mid="msg_2b",
                                     stop="end_turn"),
                               _urec(NOW - 60, "u3", "and the lint", "a2b"),
                               _arec(NOW - 55, "a3", [_tool("toolu_C", "ruff check")], "u3", mid="msg_3")]
        self._write(recs)
        self._states((NOW - 100, "working"), (NOW - 70, "waiting"), (NOW - 60, "working"))
        evs = [e for e in self._events({"state": "working"}) if e.get("kind") == "tool"]
        self.assertEqual(len({e.get("turn") for e in evs}), 2, "the fixture spans two turns")
        self.assertEqual([e.get("running") for e in evs], [None, False, True],
                         "the cut tool of the earlier turn stays not-running while a new turn works")

    def test_parallel_calls_waiting_in_the_live_turn_all_run(self):
        self._write(self._recs()[:1] + [_arec(NOW - 95, "a1", [_tool("toolu_A", "pytest -q")], "u1", mid="msg_1"),
                                        _arec(NOW - 95, "a1b", [_tool("toolu_A2", "ruff check")], "a1", mid="msg_1")])
        self._states((NOW - 100, "working"))
        evs = [e for e in self._events({"state": "working"}) if e.get("kind") == "tool"]
        self.assertEqual([e.get("running") for e in evs], [True, True])

    def test_a_message_landing_mid_turn_does_not_end_the_call_it_arrived_during(self):
        # the CLI takes a message sent mid-turn into the turn it is running: the parse opens a new turn for it, but nothing
        # stopped, and the call it arrived during still gets its result
        self._write(self._recs() + [_urec(NOW - 80, "u3", "also check the lint", "a2")])
        self._states((NOW - 100, "working"))
        waiting = [e for e in self._events({"state": "working"}) if e.get("kind") == "tool"][-1]
        self.assertIs(waiting["running"], True)

    def test_the_stamp_is_pure_over_its_inputs(self):
        stop = km.iso(NOW - 50)
        tail = [{"kind": "tool", "ts": km.iso(NOW - 40)}, {"kind": "tool", "ts": km.iso(NOW - 40), "resultUuid": "u"},
                {"kind": "tool", "ts": km.iso(NOW - 40), "isError": True}, {"kind": "tool", "ts": km.iso(NOW - 40), "agentRunning": True},
                {"kind": "tool", "ts": km.iso(NOW - 60)}, {"kind": "assistant", "ts": km.iso(NOW - 40)}]
        km._stamp_running_tools(tail, stop, True)
        self.assertEqual([e.get("running") for e in tail], [True, None, None, None, False, None])
        km._stamp_running_tools(tail, stop, False)
        self.assertEqual(tail[0]["running"], False, "a turn that is not open runs nothing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
