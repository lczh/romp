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
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import types
import unittest
from importlib.machinery import ModuleSpec
from unittest import mock
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
        self.controls = []                 # the model and permission-mode requests this client got, in order
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

    async def set_model(self, model):
        self.controls.append(("model", model))

    async def set_permission_mode(self, mode):
        self.controls.append(("mode", mode))

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


class _InitializeTimesOutOnce(_Client):
    """The first client's connect fails as an initialize that timed out does; every later one connects."""
    failed = 0

    async def __aenter__(self):
        if not type(self).failed:
            type(self).failed += 1
            raise TimeoutError("the initialize timed out")
        return self


class _RefusingClient(_Client):
    """A client whose CLI refuses every model and permission-mode request, as its error answer does (a bare Exception)."""

    async def set_model(self, model):
        self.controls.append(("model", model))
        raise Exception("Unknown model: %s" % model)

    async def set_permission_mode(self, mode):
        self.controls.append(("mode", mode))
        raise Exception("Unknown permission mode: %s" % mode)


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
    def _kernel(self, plan, survives=False, cli="/bin/true"):
        """`plan`: one step per connect, each {"attach": bool, "cli": CLI_x, "launch": ...}. The launch the hello echoes:
        "spec" is the record this connect's own _options wrote into the spawn spec (what a real host echoes for its
        spawn), "same" what that CLI's spawn carried earlier in this test, "current" the session's picks as they stand
        (a CLI launched with them), a dict as given, None for a host older than the record. `cli` is the claude the
        backend launches and reads the version of."""
        be = sb.SdkBackend(self.state, cli, lambda *a, **k: None, log=lambda m, **k: self.lines.append(str(m)))
        be.session_hosts_on = lambda: True
        be._lease_survives = lambda sid, **_: survives          # the lease a live host holds across the restart
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


class TheSpeedCheckAtTheConnect(_Harness):
    def test_a_connect_to_a_cli_launched_with_subagents_at_standard_speed_begins_its_speed_check(self):
        """A session with Subagents at standard speed on, on Opus 5.5 with its own fast pick and the version check
        passed, connects: its launch carries the recipe, and the connect begins the speed check's episode for that CLI,
        in memory and in the registry."""
        reg = self._reg()
        reg.update(model=sb.SUBAGENT_STANDARD_MODEL, fast=True, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"}])
        be.recipe_hold = lambda start=False, retry=False: ""        # the version check passed on this version
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        self._connect(s, 1)
        ran = threading.Event()
        s.loop.call_soon_threadsafe(ran.set)                        # runs once the connect's block has yielded
        self.assertTrue(ran.wait(10), "the premise: the connect's block ran")
        self.assertIs(self.launched[CLI_A["pid"]].get("subagentStandard"), True, "the premise: the launch carried the recipe")
        self.assertEqual((s._sub_std_launched, isinstance(self._reg().get("subagentSpeedScan"), dict)), (True, True),
                         "the connect began the speed check's episode for this CLI")



MOVED ="changed while the launch was being prepared"   # the log line of the relaunch for a change made meanwhile
LACKS = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}   # the initialize answer of a CLI launched without the fast flag
OPT_IN_ASK = "always fast (web): the CLI reports it lacks the fast-mode opt-in"   # the log line of the relaunch that answer asks for under Always fast


def _record(fast, **picks):
    """A launch record as an earlier launch wrote it (launch_values' keys), with fast flag `fast`."""
    return dict({"effort": sb.DEFAULT_EFFORT, "auth": "", "authLogin": "", "fast": fast, "mode": "acceptEdits",
                 "env": sb.env_digest(None)}, **picks)


class AToggleWhileTheLaunchIsPrepared(_Harness):
    def _waiter(self):
        """A file `reading` that marks the wait started, and a file `hold` the wait lasts while (up to 10 s)."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        reading, hold = os.path.join(d, "reading"), os.path.join(d, "hold")
        open(hold, "w").close()
        return d, reading, hold

    def _token_read_waits(self):
        """A login riding the environment (a synthetic token), and a stand-in claude whose -v waits (_waiter) and then
        says 2.1.282; returns the claude's path, `reading` and `hold`."""
        saved = sb._STARTUP_AUTH_ENV
        self.addCleanup(setattr, sb, "_STARTUP_AUTH_ENV", saved)
        sb._STARTUP_AUTH_ENV = {"CLAUDE_CODE_OAUTH_TOKEN": "".join(["sk", "-ant-", "oat01-", "0" * 24])}
        d, reading, hold = self._waiter()
        cli = os.path.join(d, "claude")
        with open(cli, "w") as f:
            f.write("#!/bin/sh\n: > '%s'\nn=0\nwhile [ -e '%s' ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done\n"
                    "echo '2.1.282 (Claude Code)'\n" % (reading, hold))
        os.chmod(cli, 0o755)
        return cli, reading, hold

    def _key_answer_waits(self):
        """A box that bills a key (an apiKeyHelper in this module's config dir), whose key account's fast-mode answer
        waits (_waiter) and then allows fast mode; returns `reading` and `hold`."""
        d, reading, hold = self._waiter()
        helper = os.path.join(d, "helper.sh")
        with open(helper, "w") as f:
            f.write("#!/bin/sh\necho synthetic-helper-output\n")
        os.chmod(helper, 0o700)
        settings = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "settings.json")
        before = open(settings).read() if os.path.exists(settings) else None

        def restore():
            if before is None:
                if os.path.exists(settings):
                    os.unlink(settings)
            else:
                with open(settings, "w") as f:
                    f.write(before)
        self.addCleanup(restore)
        with open(settings, "w") as f:
            json.dump({"apiKeyHelper": helper}, f)
        managed = sb._cred.managed_settings_path
        self.addCleanup(setattr, sb._cred, "managed_settings_path", managed)
        sb._cred.managed_settings_path = lambda: os.path.join(d, "no-managed-settings.json")
        sb._cred.forget_helper_key()
        self.addCleanup(sb._cred.forget_helper_key)
        sb._FAST_ORG_VERDICTS.clear()
        self.addCleanup(sb._FAST_ORG_VERDICTS.clear)
        fetch = sb._fetch_key_fast_org
        self.addCleanup(setattr, sb, "_fetch_key_fast_org", fetch)

        def answer(key):
            open(reading, "w").close()
            n = 0
            while os.path.exists(hold) and n < 200:
                time.sleep(0.05)
                n += 1
            return True
        sb._fetch_key_fast_org = answer
        return reading, hold

    def _spawns(self, be, yield_first=False):
        """Each launch as its connect reaches the host: the flag file's fastMode, the effort, and the effort markers
        written by then (one flag file per session, which the next launch rewrites). `yield_first`: the connect yields to
        the loop there once, as a real host connect does."""
        seen = []
        spawn = be._host_transport_for

        async def transport_for(sess, opts, classes):
            if yield_first:
                await asyncio.sleep(0)
            p, flags = getattr(opts, "settings", None), {}
            if p:
                with open(p) as f:
                    flags = json.load(f)
            seen.append({"fast": bool(flags.get("fastMode")), "effort": getattr(opts, "effort", None),
                         "markers": [m["effortApplied"] for m in self._effort_markers()]})
            return await spawn(sess, opts, classes)
        be._host_transport_for = transport_for
        return seen

    def _relaunch_waited(self):
        """Up to 4 s for a second client, then half a second for anything after it."""
        end = time.monotonic() + 4.0
        while time.monotonic() < end and len(_Client.instances) < 2:
            time.sleep(0.05)
        self._settle(0.5)

    def test_fast_mode_turned_off_during_the_version_read_relaunches_the_session_without_the_flag(self):
        """A guard of the token launch's version read. A session on a login riding the environment, with its own fast
        pick, connects: _options decides the flag, then reads the Claude Code version, and fast mode is turned off while
        that read waits. The launch record says the flag the flag file carried, and the session relaunches without the
        flag once it is quiet."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        seen = self._spawns(be)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_fast(SID, "off"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self.assertIs(self.launched[CLI_A["pid"]]["fast"], True, "the launch record says the flag")
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "the relaunch")
        self.assertEqual([x["fast"] for x in seen], [True, False],
                         "the flag rode the first launch, and the relaunch carries none")
        self.assertFalse(s._fast_unlocked)

    def test_fast_mode_turned_off_while_the_key_accounts_answer_waits_relaunches_the_session_without_the_flag(self):
        """A key-billed session with its own fast pick connects: _options decides the flag, then asks the key's account
        about fast mode, and fast mode is turned off while that answer waits. The launch record says the flag the first
        launch carried, the session relaunches without the flag, and nothing is sent to either CLI."""
        reading, hold = self._key_answer_waits()
        reg = self._reg()
        reg.update(auth="key", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        be.login_ok = lambda: True
        self.assertTrue(be.key_available, "the premise: the box bills a key")
        seen = self._spawns(be)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the key account's answer")
        self.assertTrue(be.set_fast(SID, "off"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._relaunch_waited()
        self.assertIs(self.launched[CLI_A["pid"]]["fast"], True, "the launch record says the flag")
        self.assertEqual([x["fast"] for x in seen], [True, False],
                         "the flag rode the first launch, and the relaunch carries none")
        self.assertFalse(s._fast_unlocked)
        self.assertEqual([c.writes for c in _Client.instances], [[], []], "nothing sent to either CLI")

    def test_an_effort_picked_while_the_key_accounts_answer_waits_applies_at_the_relaunch_that_carries_it(self):
        """A key-billed session with its own fast pick connects, and an effort is picked while the key account's
        fast-mode answer waits. The launch record says the effort that launch carried; the session relaunches with the
        pick, and the effort is recorded applied only then."""
        reading, hold = self._key_answer_waits()
        reg = self._reg()
        reg.update(auth="key", fast=True, effort="high")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        be.login_ok = lambda: True
        seen = self._spawns(be)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the key account's answer")
        self.assertTrue(be.set_effort(SID, "max"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._relaunch_waited()
        self.assertEqual(self.launched[CLI_A["pid"]]["effort"], "high", "the launch record says the effort it carried")
        self.assertEqual([x["effort"] for x in seen], ["high", "max"], "the relaunch carries the pick")
        self.assertEqual(seen[1]["markers"], [], "no effort recorded applied before the relaunch")
        self._wait(lambda: [m["effortApplied"] for m in self._effort_markers()] == ["max"], "the effort recorded applied")

    def test_an_attach_to_a_cli_launched_without_the_flag_after_always_fast_was_turned_on_is_relaunched_once_for_the_opt_in_and_the_toggle_adds_nothing(self):
        """A guard of the relaunch for a change made while the launch was prepared. Always fast was turned on across the
        restart, and nothing changes while today's options are built. The attached CLI, launched without the flag, answers
        the initialize as such a CLI does, lacking the fast-mode opt-in: that answer asks one relaunch, which carries the
        flag, and the relaunch for a change made meanwhile adds none and logs no such change."""
        with open(os.path.join(self.state, sb.ALWAYS_FAST_STORE), "w") as f:
            f.write("on")
        reg = self._reg()
        reg.update(liveModelId="claude-opus-5")              # Always fast arms only a session whose model can run fast mode
        reg.update(liveModel=sb.pretty_model("claude-opus-5"))   # as the last kernel wrote it: the CLI's report of it changes nothing
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(False)},
                              {"attach": False, "cli": CLI_B, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)

        class _AnswersAsLaunched(_Client):
            async def get_server_info(self):                 # as the CLI the host's hello names was launched
                return {"fast_mode_state": "on"} if (s._hello_launch or {}).get("fast") else dict(LACKS)

            async def get_context_usage(self):               # the session's model
                return {"percentage": 2, "model": "claude-opus-5"}
        sys.modules["claude_agent_sdk"].ClaudeSDKClient = _AnswersAsLaunched
        self.assertTrue(s.fast_effective(), "the premise: Always fast arms today's options")
        s.start()
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._relaunch_waited()
        self._settle(0.6)
        self.assertEqual(len(_Client.instances), 2, "exactly one relaunch, asked by the CLI's answer that it lacks the opt-in")
        self.assertEqual([l for l in self.lines if MOVED in l], [], "no relaunch for a change made while the launch was prepared")
        self.assertEqual(len([l for l in self.lines if OPT_IN_ASK in l]), 1, self.lines)
        self.assertIs(self.launched[CLI_B["pid"]]["fast"], True, "the relaunch carries the flag")

    def test_an_attach_to_a_cli_launched_with_the_flag_after_always_fast_was_turned_off_is_not_relaunched_at_the_connect(self):
        """A guard of the relaunch for a change made while the launch was prepared. Always fast is off, the CLI was
        launched with the flag, and nothing changes while today's options are built: the attach logs no such change and
        relaunches nothing at the connect. The CLI's answer at the connect cannot ask a relaunch here: with Always fast off
        and no fast pick of the session's own, the session does not want fast mode."""
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(True)},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)
        self.assertFalse(s.fast_effective(), "the premise: today's options carry no flag")
        s.start()
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._settle(0.6)
        self.assertEqual([l for l in self.lines if MOVED in l], [])
        self.assertEqual(len(_Client.instances), 1, "no relaunch at the connect")

    def test_fast_mode_turned_off_during_the_version_read_at_an_attach_to_a_cli_launched_without_the_flag_relaunches_nothing(self):
        """A guard of the relaunch for a change made while the launch was prepared. The session's own fast pick is
        turned off while today's options wait on the version read, and the attached CLI was launched without the flag:
        it already runs as asked, so nothing is logged as changed and nothing relaunches."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(False, auth="login")},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True, cli=cli)
        be.login_ok = lambda: True
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_fast(SID, "off"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._settle(0.6)
        self.assertEqual([l for l in self.lines if MOVED in l], [])
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def test_always_fast_turned_off_during_the_version_read_at_an_attach_with_a_waiting_effort_pick_leaves_the_relaunch_to_that_pick(self):
        """A guard of the relaunch for a change made while the launch was prepared. Always fast armed the attached CLI's
        launch, an effort pick waits across the restart, and Always fast is turned off while today's options wait on the
        version read. The waiting pick asks its relaunch: no line about the change, and no relaunch at the connect. The
        CLI's answer at the connect cannot ask a relaunch for fast mode here: by then Always fast is off and the session has
        no fast pick of its own."""
        cli, reading, hold = self._token_read_waits()
        with open(os.path.join(self.state, sb.ALWAYS_FAST_STORE), "w") as f:
            f.write("on")
        reg = self._reg()
        reg.update(auth="login", effort="max", liveModelId="claude-opus-5",
                   relaunchWait={"why": "effort", "t": int(time.time()), "picks": {"effort": {"effort": "max"}}})
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(True, auth="login", effort="high")},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True, cli=cli)
        be.login_ok = lambda: True
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        with open(os.path.join(self.state, sb.ALWAYS_FAST_STORE), "w") as f:
            f.write("off")
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        self._settle(0.6)
        self.assertTrue(any("effort not carried by the CLI this kernel attached to" in l for l in self.lines),
                        "the premise: the waiting pick asked its relaunch")
        self.assertEqual([l for l in self.lines if MOVED in l], [])
        self.assertEqual(len(_Client.instances), 1, "no relaunch at the connect")

    def test_fast_mode_turned_off_during_the_version_read_is_left_to_a_restart_asked_meanwhile(self):
        """A guard of the relaunch for a change made while the launch was prepared. The session's own fast pick is
        turned off and a Restart is asked while the first launch's version read waits: the Restart's relaunch carries
        no flag, and no line about the change is logged."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        seen = self._spawns(be, yield_first=True)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_fast(SID, "off"))
        self.assertEqual(be.relaunch(SID), "")
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "the Restart's relaunch")
        self._settle(0.5)
        self.assertEqual([x["fast"] for x in seen], [True, False], "the Restart's relaunch carries no flag")
        self.assertEqual([l for l in self.lines if MOVED in l], [])

    def test_fast_mode_turned_off_during_the_version_read_is_left_to_a_restart_waiting_behind_a_queued_message(self):
        """A guard of the relaunch for a change made while the launch was prepared. While the first launch's version read
        waits, a message is queued, the session's own fast pick is turned off and a Restart is asked: the Restart waits
        for the queued message's turn, its relaunch carries no flag, and no line about the change is logged."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        seen = self._spawns(be, yield_first=True)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        s.enqueue("first turn")
        self.assertTrue(be.set_fast(SID, "off"))
        self.assertEqual(be.relaunch(SID), "")
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        c = _Client.instances[0]
        self._wait(lambda: c.writes == ["first turn"], "the queued turn fed")
        self.assertTrue(s._reconnect_when_idle, "the premise: the Restart waits for the turn")
        self._push(c, _SystemMessage("init", {"model": "claude-x", "permissionMode": "acceptEdits", "session_id": FSID},
                                     uuid=self._uid()))
        self._push(c, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=FSID,
                              duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                              usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None))
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "the Restart's relaunch")
        self._settle(0.5)
        self.assertEqual([x["fast"] for x in seen], [True, False], "the Restart's relaunch carries no flag")
        self.assertEqual([l for l in self.lines if MOVED in l], [])

    def _picks_during_the_version_read(self, plan, survives, stored=False, live=0, prepare=None):
        """A session on a login riding the environment (the machine's own, or a stored login when `stored`), with its own
        fast pick, connects on `plan` (`prepare(backend)` runs first), and a model and then a permission mode are picked
        while today's version read waits. Returns the session and its client number `live` (0 is the first) once that
        client is the live one."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5")
        if stored:
            rec = {"id": sb._logins.mint_id(), "label": "Work", "addedAt": int(time.time()) - 86400,
                   "tokenCmd": "printf '%%s' '%s'" % "".join(["sk", "-ant-", "oat01-", "1" * 24])}
            sb._logins.write_record(self.state, rec)
            reg.update(authLogin=rec["id"])
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel(plan, survives=survives, cli=cli)
        be.login_ok = lambda: True
        if prepare:
            prepare(be)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        self.assertTrue(be.set_mode(SID, "plan"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) > live and _Client.instances[live] is s.client, "connect %d" % (live + 1))
        return s, _Client.instances[live]

    def test_a_model_and_a_mode_picked_during_the_version_read_reach_the_cli_once_the_launch_is_up(self):
        """The launch took the model and the permission mode before its version read. A model and a mode picked while
        the read waits are sent to the CLI once the connect is up, in the order they were made, and nothing relaunches."""
        s, c = self._picks_during_the_version_read([{"attach": False, "cli": CLI_A, "launch": "spec"},
                                                    {"attach": False, "cli": CLI_B, "launch": "spec"}], False)
        self.assertEqual((getattr(c.options, "model", None), getattr(c.options, "permission_mode", None)),
                         ("claude-opus-5", "acceptEdits"), "the premise: the launch took both before the read")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._settle(0.5)
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def test_a_model_and_a_mode_picked_during_the_version_read_at_an_attach_reach_the_cli_once_it_is_up(self):
        """The kernel restarted and attaches to the CLI a host kept (launched with the flag), and today's options read
        the version. A model and a mode picked while the read waits are sent to that CLI once the attach is up, in the
        order they were made, and nothing relaunches."""
        s, c = self._picks_during_the_version_read([{"attach": True, "cli": CLI_A, "launch": _record(True, auth="login")},
                                                    {"attach": False, "cli": CLI_B, "launch": "spec"}], True)
        self.assertTrue(s._host_is_attach, "the premise: an attach")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._settle(0.5)
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def test_a_model_and_a_mode_picked_during_a_stored_logins_version_read_reach_the_cli_once_the_launch_is_up(self):
        """As the launch test, on a stored login: the model and the mode picked while the read waits are sent to the CLI
        once the connect is up."""
        s, c = self._picks_during_the_version_read([{"attach": False, "cli": CLI_A, "launch": "spec"},
                                                    {"attach": False, "cli": CLI_B, "launch": "spec"}], False, stored=True)
        self.assertTrue(self.launched[CLI_A["pid"]]["authLogin"], "the premise: the launch billed the stored login")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")

    def test_a_model_and_a_mode_picked_during_the_version_read_reach_the_cli_at_the_attach_that_retries_its_initialize(self):
        """The launch's host said hello and its initialize timed out, so the connect retries as an attach to that CLI,
        which was launched with the model and the mode from before the read. The model and the mode picked while the
        read waited are sent to it once the retry is up."""
        _InitializeTimesOutOnce.failed = 0
        sys.modules["claude_agent_sdk"].ClaudeSDKClient = _InitializeTimesOutOnce

        def prepare(be):
            spawn = be._host_transport_for

            async def transport_for(sess, opts, classes):
                out = await spawn(sess, opts, classes)
                if not _InitializeTimesOutOnce.failed:       # the launch: the host said hello, its initialize will time out
                    sess._host.hello, sess._host._init_pending = {"host": {"pid": 9000}}, True
                return out
            be._host_transport_for = transport_for
        s, c = self._picks_during_the_version_read([{"attach": False, "cli": CLI_A, "launch": "spec"},
                                                    {"attach": True, "cli": CLI_A, "launch": "same"},
                                                    {"attach": False, "cli": CLI_B, "launch": "spec"}], False, live=1,
                                                   prepare=prepare)
        self.assertEqual((_InitializeTimesOutOnce.failed, s._host_is_attach), (1, True), "the premise: a retry's attach")
        self.assertEqual((getattr(_Client.instances[0].options, "model", None), self.launched[CLI_A["pid"]]["mode"]),
                         ("claude-opus-5", "acceptEdits"), "the premise: the CLI was launched before the picks")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")

    def test_a_model_and_a_mode_picked_during_the_version_read_that_the_cli_refuses_go_back_to_the_launchs(self):
        """The CLI refuses the model and the mode picked while the read waited: the session and the registry go back to
        the model and the mode the launch carried, with no pending mark."""
        sys.modules["claude_agent_sdk"].ClaudeSDKClient = _RefusingClient
        s, c = self._picks_during_the_version_read([{"attach": False, "cli": CLI_A, "launch": "spec"},
                                                    {"attach": False, "cli": CLI_B, "launch": "spec"}], False)
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._wait(lambda: (s.chosen_model, s.mode) == ("claude-opus-5", "acceptEdits"), "both put back")
        self._settle(0.5)
        reg = self._reg()
        self.assertEqual((reg.get("model"), bool(reg.get("modelPending")), reg.get("mode")),
                         ("claude-opus-5", False, "acceptEdits"))
        self.assertFalse(s._model_pending)

    def test_a_model_and_a_mode_picked_during_the_version_read_are_sent_once_and_not_again_at_a_re_attach(self):
        """The picks held during the read are sent once the launch is up; a host re-exec then re-attaches to the same
        CLI and sends nothing."""
        s, c1 = self._picks_during_the_version_read([{"attach": False, "cli": CLI_A, "launch": "spec"},
                                                     {"attach": True, "cli": CLI_A, "launch": "same"},
                                                     {"attach": False, "cli": CLI_B, "launch": "spec"}], False)
        self._wait(lambda: c1.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        s.loop.call_soon_threadsafe(s.backend._on_host_reexec_now, s)
        self._wait(lambda: s._reconnect, "the re-exec's planned reconnect")
        self._push(c1, _EOF)
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "re-attached to CLI A")
        self._settle(0.5)
        self.assertTrue(s._host_is_attach, "the premise: an attach")
        self.assertEqual(_Client.instances[1].controls, [])

    def _picked_while_the_connect_comes_up(self, road):
        """A session on claude-opus-5 in acceptEdits connects, and its connect waits once _options has built the
        options: at the session host, for a launch (`road` "launch") or for an attach to the CLI a host kept across a
        kernel restart ("attach"), or with session hosts off at the client's own connect to its CLI ("child"). A model
        and a mode are picked meanwhile, from another thread as the kernel's routes pick. Returns the session and the
        client once it is the live one."""
        reg = self._reg()
        reg.update(model="claude-opus-5")
        sb.write_reg(self.state, SID, reg)
        entered, release = threading.Event(), threading.Event()

        async def gate():
            entered.set()
            for _ in range(1000):                          # at most 10 s, so a failing test still lets the thread end
                if release.is_set():
                    return
                await asyncio.sleep(0.01)
        if road != "child":
            first = ({"attach": True, "cli": CLI_A, "launch": _record(False)} if road == "attach"
                     else {"attach": False, "cli": CLI_A, "launch": "spec"})
            be, s = self._kernel([first, {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=road == "attach")
            spawn = be._host_transport_for

            async def transport_for(sess, opts, classes):
                await gate()
                return await spawn(sess, opts, classes)
            be._host_transport_for = transport_for
        else:
            class _ConnectWaits(_Client):
                async def __aenter__(self):
                    await gate()
                    return self
            sys.modules["claude_agent_sdk"].ClaudeSDKClient = _ConnectWaits
            be, s = self._kernel([])
            be.session_hosts_on = lambda: False
        s.start()
        self._wait(entered.is_set, "the connect, waiting after the options were built")
        self.assertIsNone(s.client, "the premise: no client yet")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        self.assertTrue(be.set_mode(SID, "plan"))
        release.set()
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        c = _Client.instances[0]
        self.assertEqual((getattr(c.options, "model", None), getattr(c.options, "permission_mode", None)),
                         ("claude-opus-5", "acceptEdits"), "the premise: the launch was built before the picks")
        return s, c

    def test_a_model_and_a_mode_picked_while_the_session_host_brings_the_launch_up_reach_the_cli(self):
        """The launch's options were built, and its connect waits on the session host: the model and the mode picked
        meanwhile are sent to the CLI once the connect is up, in the order they were made, and nothing relaunches."""
        s, c = self._picked_while_the_connect_comes_up("launch")
        self.assertFalse(s._host_is_attach, "the premise: a launch")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._settle(0.5)
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def test_a_model_and_a_mode_picked_while_the_attach_to_a_kept_cli_comes_up_reach_the_cli(self):
        """As the launch, at an attach to the CLI a host kept across a kernel restart: the model and the mode picked while
        the attach waits on the host are sent to that CLI once the attach is up."""
        s, c = self._picked_while_the_connect_comes_up("attach")
        self.assertTrue(s._host_is_attach, "the premise: an attach")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._settle(0.5)
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def test_a_model_and_a_mode_picked_while_a_kernel_child_connects_reach_the_cli(self):
        """As the launch, with session hosts off: the client's own connect to its CLI waits, and the model and the mode
        picked meanwhile are sent to that CLI once the connect is up."""
        s, c = self._picked_while_the_connect_comes_up("child")
        self.assertIsNone(s._host, "the premise: a kernel child, no session host")
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self._settle(0.5)
        self.assertEqual(len(_Client.instances), 1, "no relaunch")

    def _a_model_the_subagent_speed_recipe_excludes(self, during_the_read):
        """A session with Subagents at standard speed on, on Opus 5.5 with its own fast pick, a login riding the
        environment and the version check passed, whose launch carries the recipe; claude-sonnet-5, a model the recipe
        excludes, is picked while the launch's version read waits, or once the launch is up. Returns the picks the
        registry holds as waiting at each relaunch ask, once the pick has reached the CLI and a relaunch has had time to
        follow."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"},
                              {"attach": False, "cli": dict(CLI_B, pid=4444), "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        be.recipe_hold = lambda start=False, retry=False: ""        # the version check passed on this version
        waits, note = [], be._note_pick_waits

        def noted(sess, pick, why):
            note(sess, pick, why)
            waits.append(sorted((self._reg().get("relaunchWait") or {}).get("picks") or {}))
        be._note_pick_waits = noted
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        if during_the_read:
            self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and (during_the_read or _Client.instances[0] is s.client),
                   "connect 1")
        c = _Client.instances[0]
        if not during_the_read:
            self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        self._wait(lambda: ("model", "claude-sonnet-5") in c.controls, "the pick sent")
        self._relaunch_waited()
        self.assertIs(self.launched[CLI_A["pid"]].get("subagentStandard"), True, "the premise: the launch carried the recipe")
        return waits

    def _relaunched_once_without_the_recipe(self, waits):
        self.assertEqual((len(_Client.instances), self.launched.get(CLI_B["pid"], {}).get("subagentStandard")), (2, False),
                         "one relaunch, without the recipe")
        self.assertEqual(getattr(_Client.instances[1].options, "model", None), "claude-sonnet-5", "on the picked model")
        self.assertEqual(waits, [["subagentStandard"]], "the switch recorded as waiting for that relaunch")

    def test_a_model_the_subagent_speed_recipe_excludes_picked_during_the_version_read_relaunches_the_session_without_it(self):
        """The pick reaches the CLI once the launch is up, and the session relaunches once without the recipe, on the
        picked model, the switch recorded as waiting for that relaunch."""
        self._relaunched_once_without_the_recipe(self._a_model_the_subagent_speed_recipe_excludes(True))

    def test_a_model_the_subagent_speed_recipe_excludes_picked_once_the_launch_is_up_relaunches_the_session_without_it(self):
        """As the last test, for a pick made once the launch is up."""
        self._relaunched_once_without_the_recipe(self._a_model_the_subagent_speed_recipe_excludes(False))

    def test_a_model_the_subagent_speed_recipe_excludes_picked_during_the_version_read_at_an_attach_relaunches_the_session_without_it(self):
        """At an attach to a CLI a host kept, launched with the recipe, claude-sonnet-5, a model the recipe excludes, is
        picked while the attach's version read waits: the pick reaches that CLI once the attach is up, and the session
        relaunches once without the recipe, on the picked model, the switch recorded as waiting for that relaunch."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        plan = [{"attach": True, "cli": CLI_A, "launch": None},
                {"attach": False, "cli": CLI_B, "launch": "spec"},
                {"attach": False, "cli": dict(CLI_B, pid=4444), "launch": "spec"}]
        be, s = self._kernel(plan, survives=True, cli=cli)
        be.login_ok = lambda: True
        be.recipe_hold = lambda start=False, retry=False: ""        # the version check passed on this version
        plan[0]["launch"] = dict(s.launch_values())                  # the attached CLI was launched with these values
        self.assertIs(plan[0]["launch"].get("subagentStandard"), True,
                      "the premise: the attached CLI carries the recipe")
        waits, note = [], be._note_pick_waits

        def noted(sess, pick, why):
            note(sess, pick, why)
            waits.append(sorted((self._reg().get("relaunchWait") or {}).get("picks") or {}))
        be._note_pick_waits = noted
        s.start()
        self._wait(lambda: os.path.exists(reading), "the attach's version read")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "the attach")
        c = _Client.instances[0]
        self._wait(lambda: ("model", "claude-sonnet-5") in c.controls, "the pick sent")
        self._relaunch_waited()
        self._relaunched_once_without_the_recipe(waits)

    def test_a_launch_without_the_recipe_whose_version_check_passed_while_it_waited_for_its_host_is_not_relaunched_at_the_connect(self):
        """A session with Subagents at standard speed on, on Opus 5.5 with its own fast pick, launches while the
        version check has not yet passed, so the launch leaves the recipe out. The check passes while the launch waits
        for its host, before the connect is up. Nothing is picked meanwhile, and the connect asks no relaunch: one
        client, and no launch carries the recipe."""
        _, reading, hold = self._waiter()
        reg = self._reg()
        reg.update(fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"},
                              {"attach": False, "cli": dict(CLI_B, pid=4444), "launch": "spec"}])
        verdict = ["not yet checked on this version"]
        be.recipe_hold = lambda start=False, retry=False: verdict[0]
        spawn = be._host_transport_for

        async def transport_for(sess, opts, classes):
            if not _Client.instances:                      # the launch waits for its host while `hold` exists
                open(reading, "w").close()
                n = 0
                while os.path.exists(hold) and n < 500:
                    await asyncio.sleep(0.02)
                    n += 1
            return await spawn(sess, opts, classes)
        be._host_transport_for = transport_for
        s.start()
        self._wait(lambda: os.path.exists(reading), "the launch waiting for its host")
        self.assertIsNone(s.client, "the premise: the check passes before the connect is up")
        verdict[0] = ""
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        be._canary_finished("2.1.282", {"verdict": "pass"})
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and s.client is not None, "connect 1")
        self._relaunch_waited()
        self.assertIs(self.launched[CLI_A["pid"]].get("subagentStandard"), False,
                      "the premise: the launch left the recipe out")
        self.assertEqual((len(_Client.instances), self.launched.get(CLI_B["pid"], {}).get("subagentStandard")),
                         (1, None), "no relaunch at the connect")

    def test_an_attach_to_a_cli_launched_without_the_recipe_whose_version_check_passed_while_it_waited_for_its_host_is_not_relaunched_at_the_connect(self):
        """A session with Subagents at standard speed on, on Opus 5.5 with its own fast pick, attaches to a CLI a host
        kept, launched without the recipe while the version check had not passed. The check passes while the attach
        waits for its host, before the connect is up. Nothing is picked meanwhile, and the connect asks no relaunch: one
        client."""
        _, reading, hold = self._waiter()
        reg = self._reg()
        reg.update(fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        plan = [{"attach": True, "cli": CLI_A, "launch": None},
                {"attach": False, "cli": CLI_B, "launch": "spec"},
                {"attach": False, "cli": dict(CLI_B, pid=4444), "launch": "spec"}]
        be, s = self._kernel(plan, survives=True)
        verdict = ["not yet checked on this version"]
        be.recipe_hold = lambda start=False, retry=False: verdict[0]
        plan[0]["launch"] = dict(s.launch_values())                  # the attached CLI was launched with these values
        self.assertIs(plan[0]["launch"].get("subagentStandard"), False,
                      "the premise: the attached CLI was launched without the recipe")
        spawn, attaches = be._host_transport_for, []

        async def transport_for(sess, opts, classes):
            if not _Client.instances:                      # the attach waits for its host while `hold` exists
                open(reading, "w").close()
                n = 0
                while os.path.exists(hold) and n < 500:
                    await asyncio.sleep(0.02)
                    n += 1
            out = await spawn(sess, opts, classes)
            attaches.append(sess._host_is_attach)
            return out
        be._host_transport_for = transport_for
        s.start()
        self._wait(lambda: os.path.exists(reading), "the attach waiting for its host")
        self.assertIsNone(s.client, "the premise: the check passes before the connect is up")
        verdict[0] = ""
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        be._canary_finished("2.1.282", {"verdict": "pass"})
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and s.client is not None, "connect 1")
        self._relaunch_waited()
        self.assertEqual(attaches[:1], [True], "the premise: the connect is an attach")
        self.assertEqual((len(_Client.instances), self.launched.get(CLI_B["pid"], {}).get("subagentStandard")),
                         (1, None), "no relaunch at the connect")

    def _with_session_hosts_off(self, be):
        """`be` reads this state root's session-hosts file, which says off, so each connect launches the CLI as the
        kernel's own child. Returns the subagentStandard of each launch's record, in launch order."""
        del be.session_hosts_on
        records, options = [], be._options

        def recorded(sess, C):
            o = options(sess, C)
            records.append((getattr(sess, "_launch_record", None) or {}).get("subagentStandard"))
            return o
        be._options = recorded
        return records

    def test_a_model_the_subagent_speed_recipe_excludes_picked_during_the_version_read_with_session_hosts_off_relaunches_the_session_without_it(self):
        """With session hosts off, a session with Subagents at standard speed on, on Opus 5.5 with its own fast pick, a
        login riding the environment and the version check passed, launches its CLI with the recipe as the kernel's own
        child. claude-sonnet-5, a model the recipe excludes, is picked while the launch's version read waits: the pick
        reaches the CLI once the launch is up, and the session relaunches once without the recipe, on the picked model,
        the switch recorded as waiting for that relaunch."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([], cli=cli)
        records = self._with_session_hosts_off(be)
        be.login_ok = lambda: True
        be.recipe_hold = lambda start=False, retry=False: ""        # the version check passed on this version
        waits, note = [], be._note_pick_waits

        def noted(sess, pick, why):
            note(sess, pick, why)
            waits.append(sorted((self._reg().get("relaunchWait") or {}).get("picks") or {}))
        be._note_pick_waits = noted
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1, "connect 1")
        c = _Client.instances[0]
        self._wait(lambda: ("model", "claude-sonnet-5") in c.controls, "the pick sent")
        self._relaunch_waited()
        self.assertIsNone(s._host, "the premise: no session host")
        self.assertEqual(records[:1], [True], "the premise: the launch carried the recipe")
        self.assertEqual((len(_Client.instances), records), (2, [True, False]), "one relaunch, without the recipe")
        self.assertEqual(getattr(_Client.instances[1].options, "model", None), "claude-sonnet-5", "on the picked model")
        self.assertEqual(waits, [["subagentStandard"]], "the switch recorded as waiting for that relaunch")

    def test_a_launch_without_the_recipe_with_session_hosts_off_whose_version_check_passed_before_its_connect_was_up_is_not_relaunched_at_the_connect(self):
        """With session hosts off, a session with Subagents at standard speed on, on Opus 5.5 with its own fast pick,
        launches its CLI as the kernel's own child while the version check has not yet passed, so the launch leaves the
        recipe out. The check passes before the connect is up. Nothing is picked meanwhile, and the connect asks no
        relaunch: one client, and no launch carries the recipe."""
        _, reading, hold = self._waiter()

        class _ConnectWaits(_Client):
            async def __aenter__(self):
                if len(_Client.instances) == 1:            # the first connect waits while `hold` exists
                    open(reading, "w").close()
                    n = 0
                    while os.path.exists(hold) and n < 500:
                        await asyncio.sleep(0.02)
                        n += 1
                return self
        sys.modules["claude_agent_sdk"].ClaudeSDKClient = _ConnectWaits
        reg = self._reg()
        reg.update(fast=True, model=sb.SUBAGENT_STANDARD_MODEL, subagentStandard=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([])
        records = self._with_session_hosts_off(be)
        verdict = ["not yet checked on this version"]
        be.recipe_hold = lambda start=False, retry=False: verdict[0]
        s.start()
        self._wait(lambda: os.path.exists(reading), "the launch's connect waiting")
        self.assertIsNone(s.client, "the premise: the check passes before the connect is up")
        verdict[0] = ""
        self.assertEqual(s.subagent_standard_refusal(), "", "the premise: the switch's conditions hold")
        be._canary_finished("2.1.282", {"verdict": "pass"})
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and s.client is not None, "connect 1")
        self._relaunch_waited()
        self.assertIsNone(s._host, "the premise: no session host")
        self.assertEqual(records[:1], [False], "the premise: the launch left the recipe out")
        self.assertEqual((len(_Client.instances), records), (1, [False]), "no relaunch at the connect")

    def test_a_launch_sends_no_pick_an_earlier_connect_held_since_its_options_carry_it(self):
        """A guard of the held picks' life: a model an earlier connect's read held, whose connect never came up, rides
        the next launch's options, and that launch does not send it."""
        reading, hold = self._key_answer_waits()
        os.unlink(hold)                                    # the key account answers at once
        reg = self._reg()
        reg.update(auth="key", fast=True, model="claude-sonnet-5")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        be.login_ok = lambda: True
        s._picks_held = [(s._do_set_model, "claude-sonnet-5", {"picked": "claude-sonnet-5"})]   # held by a connect that never came up
        s.start()
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        self._settle(0.5)
        self.assertTrue(os.path.exists(reading), "the premise: a key-billed launch")
        self.assertEqual(getattr(_Client.instances[0].options, "model", None), "claude-sonnet-5", "the premise: carried")
        self.assertEqual(_Client.instances[0].controls, [], "nothing sent")

    def _taken_back(self, kind):
        """A session on a login riding the environment, with its own fast pick, model claude-opus-5 and permission mode
        default, launches. A model or a mode (`kind`) is picked while the version read waits and taken back as the connect
        reaches the host. Returns the first client once it is the live one, and the session."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5", mode="default")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        pick, back = (("claude-sonnet-5", "claude-opus-5") if kind == "model" else ("acceptEdits", "default"))
        setter = be.set_model if kind == "model" else be.set_mode
        spawn = be._host_transport_for

        async def transport_for(sess, opts, classes):
            setter(SID, back)                              # after _options, before the client is up
            return await spawn(sess, opts, classes)
        be._host_transport_for = transport_for
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(setter(SID, pick))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        self._settle(0.5)
        return _Client.instances[0], s

    def test_a_model_picked_during_the_version_read_and_taken_back_before_the_connect_is_up_is_not_sent(self):
        c, s = self._taken_back("model")
        self.assertEqual([v for k, v in c.controls if k == "model"], [], "no model request")
        self.assertEqual((getattr(c.options, "model", None), s.chosen_model), ("claude-opus-5", "claude-opus-5"),
                         "the CLI runs the model the session shows")

    def test_a_mode_picked_during_the_version_read_and_taken_back_before_the_connect_is_up_is_not_sent(self):
        c, s = self._taken_back("mode")
        self.assertEqual([v for k, v in c.controls if k == "mode"], [], "no mode request")
        self.assertEqual((getattr(c.options, "permission_mode", None), s.mode), ("default", "default"),
                         "the CLI runs the mode the session shows")

    def test_a_higher_model_picked_during_the_version_read_at_an_attach_arms_no_retry_and_mints_no_card(self):
        """Retry upgrades after downgrades is on, and the kernel restarted onto a CLI a host kept, the session on Sonnet,
        whose transcript's last swap is a refusal fallback to Sonnet. Opus is picked while today's version read waits:
        the attach sends it, arms no retry, reads no fallback cause off the transcript, and a turn served on Opus mints no
        card."""
        cli, reading, hold = self._token_read_waits()
        with open(os.path.join(self.state, sb.RETRY_UPGRADE_STORE), "w") as f:
            f.write("on")
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-sonnet-5", liveModel="Sonnet 5", servedModel="Sonnet 5")
        sb.write_reg(self.state, SID, reg)
        swap = {"type": "system", "subtype": "model_refusal_fallback", "uuid": "11111111-2222-3333-4444-00000000a7a7",
                "fallbackModel": "claude-sonnet-5", "apiRefusalCategory": "synthetic-category", "scope": "session"}
        for fsid in (SID, FSID):
            p = sb.transcript_path(self.cwd, fsid)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w") as f:
                f.write(json.dumps({"type": "user", "uuid": "11111111-2222-3333-4444-00000000a7a6"}) + "\n")
                f.write(json.dumps(swap) + "\n")
            self.addCleanup(os.unlink, p)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(True, auth="login")},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True, cli=cli)
        be.login_ok = lambda: True
        cards = []
        had = "on_model_restored" in vars(sb.SdkBackend)
        saved = vars(sb.SdkBackend).get("on_model_restored")
        sb.SdkBackend.on_model_restored = staticmethod(lambda *a, **k: cards.append(a))
        self.addCleanup(lambda: setattr(sb.SdkBackend, "on_model_restored", saved) if had
                        else delattr(sb.SdkBackend, "on_model_restored"))
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_model(SID, "claude-opus-5"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        c = _Client.instances[0]
        self._wait(lambda: c.controls == [("model", "claude-opus-5")], "the pick sent")
        self._settle(1.0)
        self.assertTrue(s._host_is_attach, "the premise: an attach")
        self.assertIsNone(s._upgrade_retry, "no retry armed")
        self.assertEqual((s._fallback_cause, self._reg().get("fallbackCause", "")), ("", ""), "no fallback cause")
        self.assertEqual([l for l in self.lines if l.startswith("fallback cause (")], [])
        s.enqueue("a turn")
        self._wait(lambda: "a turn" in c.writes, "the turn fed")
        self._push(c, _SystemMessage("init", {"model": "claude-opus-5", "permissionMode": "acceptEdits", "session_id": FSID},
                                     uuid=self._uid()))
        self._push(c, _AssistantMessage([_TextBlock("ok")], model="claude-opus-5", uuid=self._uid()))
        self._push(c, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=FSID,
                              duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                              usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None))
        self._wait(lambda: s.inflight == 0 and not s._cli_working, "the turn settled")
        self._settle(0.5)
        self.assertEqual(cards, [], "no card")
        self.assertEqual([l for l in self.lines if l.startswith("retry upgrade (")], [])

    def _at_an_attach_with_a_standing_fallback(self, pick, back=None):
        """Retry upgrades after downgrades is on, and the kernel restarted onto a CLI a host kept, the session's pick Opus
        served by Sonnet. `pick(backend)` runs while today's version read waits, and `back(backend)`, when given, as the
        attach reaches the host. Returns the session and its client once the attach is up."""
        cli, reading, hold = self._token_read_waits()
        with open(os.path.join(self.state, sb.RETRY_UPGRADE_STORE), "w") as f:
            f.write("on")
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5", liveModel="Sonnet 5", servedModel="Sonnet 5")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": _record(True, auth="login")},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True, cli=cli)
        be.login_ok = lambda: True
        if back:
            spawn = be._host_transport_for

            async def transport_for(sess, opts, classes):
                back(be)                                   # after _options, before the client is up
                return await spawn(sess, opts, classes)
            be._host_transport_for = transport_for
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        pick(be)
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        self._settle(0.5)
        self.assertTrue(s._host_is_attach, "the premise: an attach")
        return s, _Client.instances[0]

    def _armed(self, s):
        arm = s._upgrade_retry
        return (arm["from"], arm["to"]) if arm else None

    def test_a_mode_picked_during_the_version_read_at_an_attach_leaves_the_retry_of_a_standing_fallback_armed(self):
        s, c = self._at_an_attach_with_a_standing_fallback(lambda be: self.assertTrue(be.set_mode(SID, "plan")))
        self._wait(lambda: c.controls == [("mode", "plan")], "the pick sent")
        self.assertEqual(self._armed(s), ("Opus 5", "Sonnet 5"), "the retry armed")

    def test_a_model_picked_during_the_version_read_and_taken_back_at_an_attach_leaves_the_retry_of_a_standing_fallback_armed(self):
        s, c = self._at_an_attach_with_a_standing_fallback(lambda be: self.assertTrue(be.set_model(SID, "claude-sonnet-5")),
                                                          back=lambda be: be.set_model(SID, "claude-opus-5"))
        self.assertEqual(c.controls, [], "nothing sent")
        self.assertEqual(self._armed(s), ("Opus 5", "Sonnet 5"), "the retry armed")

    def test_a_mode_picked_during_the_version_read_and_taken_back_at_an_attach_is_not_sent(self):
        """The mode the attached CLI runs is the one confirmed before the pick: a pick taken back to it sends nothing."""
        s, c = self._at_an_attach_with_a_standing_fallback(lambda be: self.assertTrue(be.set_mode(SID, "plan")),
                                                          back=lambda be: be.set_mode(SID, "acceptEdits"))
        self.assertEqual((c.controls, s.mode), ([], "acceptEdits"), "nothing sent")

    def test_the_account_default_picked_during_the_version_read_reaches_the_cli_as_no_model(self):
        """The account's default ("default", which goes out as set_model(None)) picked while a launch's version read
        waits is sent once the launch is up, and its dots end."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        self.assertTrue(be.set_model(SID, "default"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        c = _Client.instances[0]
        self.assertEqual(getattr(c.options, "model", None), "claude-opus-5", "the premise: the launch took the model before the read")
        self._wait(lambda: c.controls == [("model", None)], "the pick sent")
        self._wait(lambda: not s._model_pending, "the dots end")

    def test_a_model_picked_during_a_relaunchs_version_read_is_sent_when_an_earlier_read_held_the_pick_it_replaced(self):
        """The first launch's read holds Sonnet, and its initialize times out after the host's hello. The retry is a
        fresh launch whose options carry Sonnet, and Opus is picked while its read waits: that launch sends Opus."""
        cli, reading, hold = self._token_read_waits()
        _InitializeTimesOutOnce.failed = 0
        sys.modules["claude_agent_sdk"].ClaudeSDKClient = _InitializeTimesOutOnce
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        spawn = be._host_transport_for
        rearmed = []

        async def transport_for(sess, opts, classes):
            if not rearmed:                                # the next connect's read waits too
                st = os.stat(cli)
                os.utime(cli, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))   # a new file key: read again
                open(hold, "w").close()
                os.unlink(reading)
                rearmed.append(1)
            out = await spawn(sess, opts, classes)
            if not _InitializeTimesOutOnce.failed:         # the first launch: the host said hello, its initialize will time out
                sess._host.hello, sess._host._init_pending = {"host": {"pid": 9000}}, True
            return out
        be._host_transport_for = transport_for
        s.start()
        self._wait(lambda: os.path.exists(reading), "the first read")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        os.unlink(hold)
        self._wait(lambda: rearmed and os.path.exists(reading), "the second read")
        self.assertTrue(be.set_model(SID, "claude-opus-5"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 2 and _Client.instances[1] is s.client, "connect 2")
        c = _Client.instances[1]
        self.assertEqual((_InitializeTimesOutOnce.failed, s._host_is_attach, getattr(c.options, "model", None)),
                         (1, False, "claude-sonnet-5"), "the premise: a fresh launch whose options carry Sonnet")
        self._wait(lambda: c.controls == [("model", "claude-opus-5")], "the pick sent")

    def test_a_pick_that_finds_a_client_once_it_has_the_lock_goes_to_it_live_and_is_not_held(self):
        """A guard of the hold's second look, under the session lock: a pick that found no client, whose connect came up
        before the pick had the lock (and so already sent what was held), goes to that client live and is not held."""
        be, s = self._kernel([])
        scheduled = []

        class ConnectingLock:
            def __enter__(lk):
                # the connect came up between the pick's first look and the lock
                s.loop, s.client = types.SimpleNamespace(call_soon_threadsafe=scheduled.append), object()
            def __exit__(lk, *a):
                return False
        s._lock = ConnectingLock()
        s._hold_live_pick(s._do_set_model, "claude-sonnet-5", {"picked": "claude-sonnet-5"})
        self.assertEqual((len(scheduled), getattr(s, "_picks_held", None) or []), (1, []))

    def test_a_relaunch_gives_its_launch_slot_back_once_its_cli_is_up_and_sends_the_picks_held_during_its_version_read(self):
        """The machine has one launch slot. An effort pick relaunches a quiet session, the relaunch takes the slot,
        another session waits for one, and a model and a mode are picked while the relaunch's version read waits. Once
        the relaunched CLI is up, the slot is free and the waiting session is offered it, and the CLI gets the model and
        the mode."""
        cli, reading, hold = self._token_read_waits()
        os.unlink(hold)                                    # the first launch's read answers at once
        reg = self._reg()
        reg.update(auth="login", fast=True, model="claude-opus-5")
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        slots = be._spawn_sem = sb._LaunchSlots(1)
        self._connect(s, 1)
        self._settle(0.3)
        st = os.stat(cli)
        os.utime(cli, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))   # a new file key: the relaunch reads again
        open(hold, "w").close()
        os.unlink(reading)
        self.assertTrue(be.set_effort(SID, "max"))
        self._wait(lambda: os.path.exists(reading), "the relaunch's version read")
        offered, offer = [], (lambda: None)
        waiter = types.SimpleNamespace(sid="11111111-2222-3333-4444-dddddddddd31", ended=False, _slot_offered=offer,
                                       _schedule_on_loop=lambda fn: offered.append(fn) or True)
        self.assertFalse(slots.take(waiter), "the premise: the relaunch holds the one slot, and the other session waits")
        self.assertTrue(be.set_model(SID, "claude-sonnet-5"))
        self.assertTrue(be.set_mode(SID, "plan"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 2 and _Client.instances[1] is s.client, "the relaunch")
        c = _Client.instances[1]
        self._wait(lambda: c.controls == [("model", "claude-sonnet-5"), ("mode", "plan")], "the picks sent")
        self.assertEqual((s._launch_slot, offered), (False, [offer]),
                         "the relaunch gave its slot back once its CLI was up, and the waiting session was offered it")
        self.assertTrue(slots.acquire(blocking=False), "the slot is free")
        slots.release()

    def test_fast_mode_turned_off_during_the_version_read_and_left_to_a_billing_relaunch_relaunches_once_billing_goes_back(self):
        """A guard of the relaunch for a change made while the launch was prepared. While the launch's version read
        waits, a message is queued, the session's own fast pick is turned off and its billing is set to follow the
        default, so the connect leaves the change of fast mode to the billing pick's relaunch. Billing then goes back to
        the login the CLI was launched on, and once the queued turn ends the session relaunches without the flag."""
        cli, reading, hold = self._token_read_waits()
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], cli=cli)
        be.login_ok = lambda: True
        seen = self._spawns(be)
        s.start()
        self._wait(lambda: os.path.exists(reading), "the version read")
        s.enqueue("first turn")
        self.assertTrue(be.set_fast(SID, "off"))
        self.assertTrue(be.set_auth(SID, "default"))
        os.unlink(hold)
        self._wait(lambda: len(_Client.instances) >= 1 and _Client.instances[0] is s.client, "connect 1")
        c = _Client.instances[0]
        self._wait(lambda: c.writes == ["first turn"], "the queued turn fed")
        self._settle(0.3)
        self.assertEqual((s._switch_wanted, [l for l in self.lines if MOVED in l]), ("billing", []),
                         "the premise: the change of fast mode is left to the billing pick's relaunch")
        self.assertTrue(be.set_auth(SID, "login"))
        self._settle(0.3)
        self._push(c, _SystemMessage("init", {"model": "claude-x", "permissionMode": "acceptEdits", "session_id": FSID},
                                     uuid=self._uid()))
        self._push(c, _result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=FSID,
                              duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                              usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None))
        self._wait(lambda: len(_Client.instances) == 2 and _Client.instances[1] is s.client, "the relaunch")
        self.assertEqual([x["fast"] for x in seen], [True, False], "the relaunch carries no flag")

    def test_a_relaunch_keeps_its_launch_slot_while_its_version_read_waits_and_another_sessions_relaunch_waits_for_the_read(self):
        """Pins current behaviour, a limit the PR states; not a fix. The machine has one launch slot. This session, on a
        login riding the environment with its own fast pick, relaunches for an effort pick, and its version read waits. A
        second session, with no fast pick and so no version to read, then relaunches for an effort pick too. Its relaunch
        does not reach _options while the read waits, and reaches it once the read has ended."""
        cli, reading, hold = self._token_read_waits()
        os.unlink(hold)                                    # the first launch's read answers at once
        sid2, fsid2 = "11111111-2222-3333-4444-eeeeeeeeee31", "11111111-2222-3333-4444-ffffffffee31"
        reg = self._reg()
        reg.update(auth="login", fast=True)
        sb.write_reg(self.state, SID, reg)
        cwd2 = os.path.join(self.state, "proj-api")
        os.makedirs(cwd2)
        sb.write_reg(self.state, sid2, {"sid": sid2, "name": "api", "mode": "acceptEdits", "alive": True, "cwd": cwd2})
        be, s = self._kernel([], cli=cli)
        be.login_ok = lambda: True
        slots = be._spawn_sem = sb._LaunchSlots(1)
        s2 = sb.SdkSession(be, dict(sb.read_reg(self.state, sid2)))
        be.sessions[sid2] = s2
        self.sessions.append(s2)
        held = {SID: [], sid2: []}                         # per session, at each _options: whether the read still waited
        connects = {SID: 0, sid2: 0}
        options = be._options

        def _options(sess, C):
            held[sess.sid].append(os.path.exists(hold))
            return options(sess, C)
        be._options = _options

        async def _transport_for(sess, opts, classes):
            connects[sess.sid] += 1
            pid = (5000 if sess.sid == SID else 6000) + connects[sess.sid]
            sess._host_is_attach = False
            sess._host = types.SimpleNamespace(exit_info=None, ack_offset=0, end_grace=120.0, hello=None,
                                               _init_pending=False, detach_mode=False)
            be._on_host_hello(sess, {"host": {"pid": 9000 + pid, "start": "1.0", "version": be.code_version},
                                     "cli": {"pid": pid, "start": "%d.5" % pid, "spawnedAt": 1700000000 + pid,
                                             "fsid": FSID if sess.sid == SID else fsid2, "login": "",
                                             "launch": dict(getattr(sess, "_launch_record", None) or {})},
                                     "journal": {"next": 0}, "parked": [], "inflight": 0})
            return None
        be._host_transport_for = _transport_for
        s.start()
        s2.start()
        self._wait(lambda: connects == {SID: 1, sid2: 1} and s.client is not None and s2.client is not None
                   and s._connected.is_set() and s2._connected.is_set(), "both sessions up")
        self._settle(0.3)
        st = os.stat(cli)
        os.utime(cli, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000_000))   # a new file key: the relaunch reads again
        open(hold, "w").close()
        os.unlink(reading)
        self.assertTrue(be.set_effort(SID, "max"))
        self._wait(lambda: os.path.exists(reading), "the relaunch's version read")
        self.assertTrue(be.set_effort(sid2, "max"))
        queued = "effort (api): the session is quiet; waiting for a launch slot"
        self._wait(lambda: len(held[sid2]) > 1 or any(queued in l for l in self.lines),
                   "the second session's relaunch to wait for a slot or reach _options")
        self.assertEqual((held[sid2], slots.waiting(s2), s._launch_slot), ([False], True, True),
                         "while the version read waits, the relaunch keeps the slot, and the second session's relaunch "
                         "waits for it and does not reach _options")
        os.unlink(hold)
        self._wait(lambda: len(held[sid2]) > 1, "the second session's relaunch at _options once the read has ended")
        self._wait(lambda: connects == {SID: 2, sid2: 2}, "both relaunches")


class AReattachAtBoot(_Harness):
    _waiter = AToggleWhileTheLaunchIsPrepared._waiter
    _token_read_waits = AToggleWhileTheLaunchIsPrepared._token_read_waits
    A, B = "11111111-2222-3333-4444-aaaaaaaaaa41", "11111111-2222-3333-4444-bbbbbbbbbb41"

    def _boot(self, cli, hold):
        """A kernel over two sessions whose hosts kept their CLIs: A, on a login riding the environment with its own fast
        pick, then B, with no fast pick and so no version to read. Returns the backend, the regs for _boot_reconcile,
        `held` (per session, at each _options: whether the read still waited) and `hellos` (per session, the hellos of
        its re-attaches)."""
        a, b = self.A, self.B
        self._alive, regs = {}, []
        for i, (sid, name, picks) in enumerate(((a, "web", {"auth": "login", "fast": True}), (b, "api", {}))):
            cwd = os.path.join(self.state, "proj-" + name)
            os.makedirs(cwd)
            sb.write_reg(self.state, sid, dict({"sid": sid, "name": name, "mode": "acceptEdits", "alive": True,
                                                "cwd": cwd}, **picks))
            regs.append(dict(sb.read_reg(self.state, sid)))
            cpid, hpid = 910000000 + 2 * i, 910000001 + 2 * i      # the CLI, and the host that kept it
            sb.write_lease(self.state, {"sid": sid, "fsid": sid, "pid": cpid, "start": "1", "version": "",
                                        "t": time.time(), "holder": {"pid": hpid, "start": "2", "kind": "host"}})
            self._alive[cpid], self._alive[hpid] = "1", "2"
        be, _s = self._kernel([], cli=cli)
        be.login_ok = lambda: True
        held = {a: [], b: []}
        hellos = {a: 0, b: 0}
        options = be._options

        def _options(sess, C):
            held[sess.sid].append(os.path.exists(hold))
            return options(sess, C)
        be._options = _options

        async def _transport_for(sess, opts, classes):
            pid = 5000 if sess.sid == a else 6000
            sess._host_is_attach = True
            sess._host = types.SimpleNamespace(exit_info=None, ack_offset=0, end_grace=120.0, hello=None,
                                               _init_pending=False, detach_mode=False)
            be._on_host_hello(sess, {"host": {"pid": 9000 + pid, "start": "1.0", "version": be.code_version},
                                     "cli": {"pid": pid, "start": "%d.5" % pid, "spawnedAt": 1700000000 + pid,
                                             "fsid": sess.sid, "login": "",
                                             "launch": dict(getattr(sess, "_launch_record", None) or {})},
                                     "journal": {"next": 0}, "parked": [], "inflight": 0})
            hellos[sess.sid] += 1
            return None
        be._host_transport_for = _transport_for
        return be, regs, held, hellos

    @contextlib.contextmanager
    def _reconciling(self, be, regs, hold):
        """The real _boot_reconcile over `regs` on its own thread while the block runs; after it, the read is let go and
        the thread joined. The lease classifier reads proc_start from the module registered as romp_sdk_backend, so this
        module's backend is registered under that one key, and only that key is put back after: mock.patch.dict over
        sys.modules would refill the whole mapping and drop any module another thread imported meanwhile."""
        prev = sys.modules.get("romp_sdk_backend")
        sys.modules["romp_sdk_backend"] = sb
        try:
            with mock.patch.object(sb, "proc_start", lambda p, run=None: self._alive.get(p)):
                th = threading.Thread(target=be._boot_reconcile, args=(regs,), daemon=True)
                th.start()
                try:
                    yield
                finally:
                    if os.path.exists(hold):
                        os.unlink(hold)
                    th.join(10)
        finally:
            if prev is None:
                sys.modules.pop("romp_sdk_backend", None)
            else:
                sys.modules["romp_sdk_backend"] = prev
            self.sessions.extend(x for x in (be.sessions.get(self.A), be.sessions.get(self.B)) if x is not None)

    def test_a_boot_reattach_keeps_its_attach_slot_while_its_version_read_waits_and_another_sessions_reattach_waits_for_the_read(self):
        """Pins current behaviour, a limit the PR states; not a fix. The boot has one attach slot and two sessions whose
        hosts kept their CLIs. The first, on a login riding the environment with its own fast pick, re-attaches first,
        and its version read waits. The second, with no fast pick and so no version to read, asks for the slot once that
        read has started. Its re-attach does not reach _options while the read waits, and reaches it once the read has
        ended."""
        cli, reading, hold = self._token_read_waits()
        a, b = self.A, self.B
        be, regs, held, hellos = self._boot(cli, hold)
        asks = []                              # per ask for the attach slot: whether it was free

        class Slots(threading.Semaphore):
            def acquire(slots, blocking=True, timeout=None):
                if asks:                       # the second session asks once the first one's read has started
                    end = time.monotonic() + 10
                    while not os.path.exists(reading) and time.monotonic() < end:
                        time.sleep(0.01)
                asks.append(threading.Semaphore.acquire(slots, False))
                return asks[-1] or threading.Semaphore.acquire(slots, blocking, timeout)
        be._attach_sem = Slots(1)
        with self._reconciling(be, regs, hold):
            self._wait(lambda: len(asks) > 1 and (not asks[1] or held[b]),
                       "the second session's ask for the attach slot")
            self.assertEqual((held[b], asks), ([], [True, False]),
                             "while the version read waits, the first re-attach keeps the attach slot, and the "
                             "second session's re-attach waits for it and does not reach _options")
            os.unlink(hold)
            self._wait(lambda: held[b] and hellos == {a: 1, b: 1},
                       "the second session's re-attach once the read has ended")

    def test_the_boot_attach_phase_ends_only_once_a_boot_reattachs_version_read_has_ended(self):
        """Pins current behaviour, a limit the PR states; not a fix. The boot has two attach slots and two sessions whose
        hosts kept their CLIs. The first, on a login riding the environment with its own fast pick, re-attaches first,
        and its version read waits. The second, with no fast pick, re-attaches and has its hello while that read waits.
        The boot's attach phase (attachDone) does not end while the read waits, and ends once the read has ended."""
        cli, reading, hold = self._token_read_waits()
        a, b = self.A, self.B
        be, regs, held, hellos = self._boot(cli, hold)
        phases = []                            # each boot milestone, with whether the read still waited
        be._boot_phase = lambda kind: phases.append((kind, os.path.exists(hold)))
        be._attach_sem = threading.Semaphore(2)
        with self._reconciling(be, regs, hold):
            self._wait(lambda: os.path.exists(reading) and hellos[b] == 1,
                       "the first re-attach's version read, and the second re-attach's hello")
            self._settle(0.3)
            self.assertEqual(([w for k, w in phases if k == "attachDone"], hellos), ([], {a: 0, b: 1}),
                             "while the version read waits, the attach phase does not end, though the second "
                             "re-attach has had its hello")
            os.unlink(hold)
            self._wait(lambda: hellos[a] == 1 and any(k == "attachDone" for k, _w in phases),
                       "the first re-attach's hello and the attach phase's end once the read has ended")
            self.assertEqual([w for k, w in phases if k == "attachDone"], [False],
                             "the attach phase ends once, after the read has ended")


if __name__ == "__main__":
    unittest.main()
