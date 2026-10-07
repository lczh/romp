#!/usr/bin/env python3
"""A pick waiting for its relaunch survives a kernel restart that keeps the CLI; effort is recorded applied only when it is.

The gap (2026-10-01, the follow-up to tests/test_reconnect_waits_for_work.py): a billing, effort or fast-mode pick waits
for a quiet session before it relaunches the CLI. Under a session host the CLI outlives the kernel, so a pick still
waiting when the kernel restarted was dropped: the new kernel attached to the CLI launched before the pick, cleared the
pick's pending marks as moot at construction, and nothing ever relaunched. Inside one kernel's life, a re-attach to the
same CLI (a host re-exec at a turn's end, an attach retry) cleared the marks at the connect and wrote the effort's
durable "effort set to X" marker for an effort that never reached the CLI, and retired the CLI's running background
tasks as cut, so quiet() then read an empty set and the relaunch cut them for real.

The fix, pinned here:
  * the waiting pick is persisted (the reg's relaunchWait: which picks, with the values asked for);
  * every launch records the picks it carries (launch_values), the host spawn writes that record into its spec, and the
    host echoes it in every hello (cli.launch: tests/test_session_host.py);
  * every connect compares the waiting picks with the connected CLI's launch record: a pick the launch carries is
    applied (marks cleared, the effort marker written at the launch's moment); one it does not carry, or that no record
    can vouch for (a hello from a host older than the record), keeps its marks and its wait-for-quiet relaunch;
  * a kernel attaching to a surviving CLI seeds the background tasks it runs from the reg's mirror, and a re-attach to the
    same CLI inside one kernel's life keeps them, so the re-armed relaunch waits for them instead of cutting them;
  * the fast badge wears its dots while the opt-in waits (fastPending) instead of flipping to Fast at the pick.

The REAL _amain runs against a stand-in SDK client (the harness of tests/test_reconnect_waits_for_work.py) and a stand-in
host: the backend's _host_transport_for is replaced by one that plays a scripted host (a spawn or an attach, with the
hello a real host sends) and returns no transport, since the stand-in client reads none. A kernel restart is a second
SdkBackend and SdkSession over the same state directory, the first one's thread ended as a drain ends it. Every id is
synthetic, every text invented; the state roots write session-hosts off (only the stand-in host runs).
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
from importlib.machinery import ModuleSpec
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
sb = load_source("romp_sdk_backend_picksurvives", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-cccccccccc31"        # this module's own synthetic sid
FSID = "11111111-2222-3333-4444-ffffffffff31"
CLI_A = {"pid": 4242, "start": "100.5", "spawnedAt": 1700000100}   # the CLI the first kernel's host launched
CLI_B = {"pid": 4343, "start": "200.5", "spawnedAt": 1700000200}   # the CLI the relaunch puts in its place


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
_EOF = object()   # pushed as a frame: the stream ends (the host closed its socket for a re-exec)


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
            f = await self.frames.get()
            if f is _EOF:
                return
            yield f


class _Options:
    def __init__(self, **kw):
        self.session_id = self.resume = None
        for k, v in kw.items():
            setattr(self, k, v)


class _Harness(unittest.TestCase):
    def setUp(self):
        sb.SdkSession._stream_fail_seen.clear()
        _Client.instances = []
        fake = types.ModuleType("claude_agent_sdk")
        fake.__spec__ = ModuleSpec("claude_agent_sdk", loader=None)
        fake.ClaudeSDKClient, fake.ClaudeAgentOptions, fake.HookMatcher = _Client, _Options, (lambda **kw: types.SimpleNamespace(**kw))
        fake.AssistantMessage, fake.ResultMessage = _AssistantMessage, _ResultMessage
        fake.SystemMessage, fake.TextBlock = _SystemMessage, _TextBlock
        self._saved_sdk = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake
        async def _noop_refresh(self): pass
        self._saved_refresh = sb.SdkSession._do_refresh_usage
        sb.SdkSession._do_refresh_usage = _noop_refresh
        self.state = tempfile.mkdtemp()
        with open(os.path.join(self.state, "session-hosts"), "w") as f:
            f.write("off")                       # this root is outside the runner's belt; only the stand-in host runs
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd)
        sb.write_reg(self.state, SID, {"sid": SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd})
        self.lines = []
        self.sessions = []
        self.launched = {}                 # CLI pid → the launch record its spawn carried
        self.n = 0

    def tearDown(self):
        for s in self.sessions:
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

    # -- a kernel: a backend, its session, and the scripted host the session's connects reach --
    def _kernel(self, plan, survives=False):
        """`plan`: one step per connect, each {"attach": bool, "cli": CLI_x, "launch": ...}. The launch the hello echoes:
        "spec" is the record this connect's own _options wrote into the spawn spec (what a real host echoes for its
        spawn), "same" what that CLI's spawn carried earlier in this test, "current" the session's picks as they stand
        (a CLI launched with them), a dict as given, None for a host older than the record."""
        be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: self.lines.append(str(m)))
        be.session_hosts_on = lambda: True
        be._lease_survives = lambda sid: survives          # the lease a live host holds across the restart
        steps = list(plan)

        async def _transport_for(sess, opts, classes):
            step = steps.pop(0)
            sess._host_is_attach = step["attach"]
            sess._host = types.SimpleNamespace(exit_info=None, ack_offset=0, end_grace=120.0, hello=None,
                                               _init_pending=False, detach_mode=False)
            how = step["launch"]
            launch = (dict(getattr(sess, "_launch_record", None) or {}) if how == "spec"
                      else dict(self.launched[step["cli"]["pid"]]) if how == "same"
                      else sess.launch_values() if how == "current" else how)
            if how == "spec":
                self.launched[step["cli"]["pid"]] = dict(launch)
            be._on_host_hello(sess, {"host": {"pid": 9000, "start": "1.0", "version": be.code_version},
                                     "cli": dict(step["cli"], fsid=FSID, login="", launch=launch),
                                     "journal": {"next": 0}, "parked": [], "inflight": 0})
            return None                                    # the stand-in client reads no transport
        be._host_transport_for = _transport_for
        s = sb.SdkSession(be, dict(sb.read_reg(self.state, SID)))
        async def _noop(): pass
        s._do_refresh_usage = _noop
        be.sessions[SID] = s
        self.sessions.append(s)
        return be, s

    def _end_kernel(self, s):
        """The kernel's drain: the session detaches and its thread ends; the host keeps the CLI."""
        s.detached = True
        s.shutdown()
        s.thread.join(timeout=10)
        self.assertFalse(s.thread.is_alive())

    # -- the scripted stream --
    def _wait(self, pred, what, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for %s; clients %d; log tail %r" % (what, len(_Client.instances), self.lines[-8:]))

    def _settle(self, dt=0.25):
        time.sleep(dt)

    def _push(self, client, frame):
        client.loop.call_soon_threadsafe(client.frames.put_nowait, frame)

    def _uid(self):
        self.n += 1
        return "11111111-2222-3333-4444-%012d" % self.n

    def _connect(self, s, k):
        """Start the session and wait for its k-th client (counted across kernels) to be the live one."""
        s.start()
        self._wait(lambda: len(_Client.instances) >= k and _Client.instances[k - 1] is s.client, "connect %d" % k)
        return _Client.instances[k - 1]

    def _a_turn_that_leaves_a_monitor(self, s, c, desc="watch the spend"):
        s.enqueue("first turn")
        self._wait(lambda: c.writes == ["first turn"], "the turn fed")
        self._push(c, _SystemMessage("init", {"model": "claude-x", "permissionMode": "acceptEdits", "session_id": FSID},
                                     uuid=self._uid()))
        self._push(c, _SystemMessage("task_started", {"task_id": "t-mon", "description": desc, "task_type": "local_bash"},
                                     uuid=self._uid()))
        self._wait(lambda: s.live_work()[1] == 1, "the monitor registered")
        self._push(c, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=FSID,
                              duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                              usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None))
        self._wait(lambda: s.inflight == 0 and not s._cli_working, "the turn settled")

    def _monitor_ends(self, c):
        self._push(c, _SystemMessage("task_notification", {"task_id": "t-mon", "status": "completed"}, uuid=self._uid()))

    def _effort_markers(self):
        p = os.path.join(self.state, "states", SID + ".jsonl")
        if not os.path.exists(p):
            return []
        return [r for r in (json.loads(l) for l in open(p) if l.strip()) if "effortApplied" in r]

    def _reg(self):
        return sb.read_reg(self.state, SID) or {}


class APickWaitingAcrossAKernelRestart(_Harness):
    def test_the_picks_survive_the_restart_wait_for_the_surviving_monitor_and_apply_once_quiet(self):
        be1, s1 = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"}])
        c1 = self._connect(s1, 1)
        self._a_turn_that_leaves_a_monitor(s1, c1)
        self.assertTrue(be1.set_auth(SID, "login"))
        self.assertTrue(be1.set_effort(SID, "max"))
        self.assertTrue(be1.set_fast(SID, "on"))
        self._settle()
        self.assertEqual(len(_Client.instances), 1, "the monitor holds every pick")
        snap = s1.snapshot()
        self.assertTrue(snap["fastPending"], "the fast badge wears its dots while the opt-in waits")
        self.assertNotEqual(snap["fast"], "on", "…and never reads Fast before a launch carries the flag")
        wait = self._reg().get("relaunchWait") or {}
        self.assertEqual(set(wait.get("picks") or {}), {"billing", "effort", "fast"}, "the waiting picks are on record")
        self.assertEqual(wait["picks"]["effort"], {"effort": "max"})
        self.assertEqual(wait["picks"]["billing"], {"auth": "login", "authLogin": ""})
        self._end_kernel(s1)

        # the restart: a new kernel over the same state, the host still running CLI A with its monitor
        be2, s2 = self._kernel([{"attach": True, "cli": CLI_A, "launch": "same"},
                                {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)
        self.assertEqual((s2._effort_pending, s2._fast_pending), ("max", "on"),
                         "the marks stand from construction: the pick was not applied")
        self.assertEqual(s2._auth_pending, "", "billing is the connect's to decide, against the CLI's launch record (2026-10-07)")
        c2 = self._connect(s2, 2)
        self._settle()
        self.assertEqual(len(_Client.instances), 2, "attached, not relaunched: the surviving monitor is still running")
        snap = s2.snapshot()
        self.assertTrue(snap["authPending"] and snap["effortPending"] and snap["fastPending"], snap)
        self.assertEqual(snap["relaunchWaits"], "1 background task", "the monitor CLI A runs, from the mirror")
        self.assertEqual(self._effort_markers(), [], "an attach to the CLI launched before the pick applies nothing")
        self.assertFalse(s2._fast_unlocked, "the launch's own word: CLI A runs without the fast flag")
        self.assertTrue(any("waiting picks (web)" in l and "not carried by the CLI this kernel attached to" in l
                            for l in self.lines), self.lines)
        self._monitor_ends(c2)                             # the deciding event: nothing left to cut
        self._wait(lambda: len(_Client.instances) == 3 and _Client.instances[2] is s2.client, "the relaunch onto CLI B")
        self.assertTrue(c2.torn_down)
        self._wait(lambda: not s2.snapshot()["authPending"], "applied at CLI B's launch")
        snap = s2.snapshot()
        self.assertFalse(snap["effortPending"] or snap["fastPending"])
        self.assertEqual(snap["fast"], "on")
        self.assertEqual((s2._launch_record["auth"], s2._launch_record["effort"], s2._launch_record["fast"]),
                         ("login", "max", True), "CLI B carries every pick")
        self.assertNotIn("relaunchWait", self._reg(), "nothing waits any more")
        self.assertEqual([m["effortApplied"] for m in self._effort_markers()], ["max"], "recorded once, at the launch that applied it")
        self.assertFalse(any("[romp]" in t for t in s2.pending() + _Client.instances[2].writes), "nothing was cut")

    def test_a_cli_the_record_shows_launched_with_the_pick_is_applied_at_the_attach(self):
        """The previous kernel relaunched with the pick and died before its connect cleared the record: the host's echo of
        CLI B's launch vouches for the pick, so it is applied at the attach (marker at CLI B's own spawn time), with no
        second relaunch."""
        reg = self._reg()
        reg.update(effort="max", effortPending=True, spawnedAtCli="%s:%s" % (CLI_B["pid"], CLI_B["start"]),
                   relaunchWait={"why": "effort", "t": 1700000150, "picks": {"effort": {"effort": "max"}}})
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_B, "launch": "current"}], survives=True)
        self._connect(s, 1)
        self._wait(lambda: not s.snapshot()["effortPending"], "applied at the attach")
        self._settle()
        self.assertEqual(len(_Client.instances), 1, "no relaunch: the CLI already carries the pick")
        self.assertEqual(s._switch_wanted, "")
        self.assertNotIn("relaunchWait", self._reg())
        self.assertEqual([(m["effortApplied"], m["t"]) for m in self._effort_markers()], [("max", CLI_B["spawnedAt"])],
                         "the effort became real when CLI B launched, and is recorded so")

    def test_a_hello_with_no_launch_record_cannot_vouch_and_the_pick_relaunches_once_quiet(self):
        """A host spawned by code older than the launch record says nothing about its launch: the waiting pick is not
        assumed applied. It relaunches at the next quiet moment, here the kernel's tick, the backstop for a session that
        went quiet with no event of its own."""
        reg = self._reg()
        reg.update(auth="login", authPending=True, spawnedAtCli="%s:%s" % (CLI_A["pid"], CLI_A["start"]),
                   relaunchWait={"why": "billing", "t": 1700000150, "picks": {"billing": {"auth": "login", "authLogin": ""}}})
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": None},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)
        self._connect(s, 1)
        self._settle()
        self.assertEqual(len(_Client.instances), 1, "never at the attach itself: its replay lands first")
        self.assertTrue(s.snapshot()["authPending"])
        self.assertTrue(any("cannot be vouched for" in l for l in self.lines), self.lines)
        be.retry_model_upgrades(time.time())               # the tick: the session is quiet
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "the relaunch")
        self._wait(lambda: not s.snapshot()["authPending"], "applied at the launch")
        self.assertNotIn("relaunchWait", self._reg())


class AReattachToTheSameCliInsideOneKernel(_Harness):
    def test_a_host_re_exec_keeps_the_monitor_and_the_pick_and_records_no_effort_until_it_applies(self):
        """A host re-exec at a turn's end reconnects the kernel to the SAME CLI. Before the fix the loop top retired the
        monitor as cut (and told the session so), and the connect cleared the effort's dots and recorded the effort as
        applied while CLI A still ran the old one."""
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": True, "cli": CLI_A, "launch": "same"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        c1 = self._connect(s, 1)
        self._a_turn_that_leaves_a_monitor(s, c1)
        self.assertTrue(be.set_effort(SID, "max"))
        self._settle()
        self.assertEqual(len(_Client.instances), 1)
        self.assertNotEqual(self.launched[CLI_A["pid"]]["effort"], "max", "precondition: CLI A was launched before the pick")
        s.loop.call_soon_threadsafe(be._on_host_reexec_now, s)
        self._wait(lambda: s._reconnect, "the re-exec's planned reconnect")
        self._push(c1, _EOF)                               # the host closes the socket for its exec
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "re-attached to CLI A")
        c2 = _Client.instances[1]
        self._settle()
        self.assertEqual(s.live_work()[1], 1, "the monitor lives on in CLI A")
        self.assertFalse(any("[romp]" in t for t in s.pending()), "no notice that it was cut: it was not")
        self.assertTrue(s.snapshot()["effortPending"], "the effort's dots stay: CLI A still runs the old effort")
        self.assertEqual(self._effort_markers(), [], "never recorded as applied at a re-attach")
        self.assertEqual(len(_Client.instances), 2, "the monitor still holds the relaunch")
        self._monitor_ends(c2)
        self._wait(lambda: len(_Client.instances) == 3 and _Client.instances[2] is s.client, "the relaunch onto CLI B")
        self._wait(lambda: not s.snapshot()["effortPending"], "applied")
        self.assertEqual([m["effortApplied"] for m in self._effort_markers()], ["max"])


if __name__ == "__main__":
    unittest.main()
