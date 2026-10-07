#!/usr/bin/env python3
"""A session's billing is a SETTING that follows the machine default (plans/billing-follows-the-default.md, 2026-10-07).

What went wrong, during a day of switching every session between an enterprise login and the API key:
  * a billing pick on a busy session was PARKED in the chat's queue as an "/auth" chip while the Billing menu showed the
    old pick, a newer parked pick appended behind the older one, and a pick under an account hold waited behind the very
    hold it would have got past;
  * sessions whose registry said one billing kept running hosted CLIs launched on the other, and nothing compared them;
  * every session carried a pick of its own (spawn seeded one, forks copied the parent's), so changing the machine
    default moved nobody, and a session could not be set back to follow it.

The rules pinned here:
  * a pick is recorded at once whatever the session is doing, never parked; input sent after it goes out on the current
    account; a newer pick replaces an unapplied one, and ONE relaunch at the next quiet moment carries the latest;
  * a session follows the machine default unless it carries a pick of its own; "default" puts it back; a change of
    default re-targets every live follower (each relaunching at its own quiet moment) and leaves overrides alone;
  * spawn and fork mint no override; the launch record carries what the session is set to bill, the default included;
  * at every connect billing is compared with the CLI's launch record: a mismatch shows pending and relaunches when
    quiet, and a CLI no record vouches for is said so and relaunched once;
  * a move in flight is not quiet (the park held picks back from that window), and its settle carries a standing ask;
  * billing picks an earlier kernel parked migrate on load: the latest per session becomes its recorded pick.

The real _amain runs against the stand-in SDK client and scripted host of tests/test_pick_survives_restart.py, whose
harness is reused here. Every id is synthetic, every text invented; every state root writes session-hosts off.
"""
import inspect
import json
import os
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE any load: the loads resolve their state root at import time, and only pytest runs conftest's floor
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sys.path.insert(0, HERE)
import test_pick_survives_restart as psr  # noqa: E402  the stand-in client, host and harness
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_billingdefault", os.path.join(BIN, "romp-kernel"))
sb = psr.sb
jd = km.jd
km._PROMPT_HOLD_S = 0.0

SID = psr.SID
OTHER = "11111111-2222-3333-4444-cccccccccc32"
CLI_A, CLI_B = psr.CLI_A, psr.CLI_B


# ── the kernel: one door, no park ──────────────────────────────────────────────────────────────────────────────────

class _Recorder:
    def __init__(self):
        self.calls = []
        self.owned = {SID}

    def owns(self, sid):
        return sid in self.owned

    def forwards_sends(self):
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid, text))
        return True

    def set_auth(self, sid, value):
        self.calls.append(("auth", sid, value))
        return True

    def auth_unavailable_why(self, v):
        return ""


class _KernelWorld(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "session-hosts").write_text("off")
        names = ("_compacting_now", "_working_now", "_push_all", "_push_soon", "_mark_views_dirty", "_limit_hold",
                 "_kernel_knows", "_PENDING_OPS_FILE")
        self._saved = {n: getattr(km, n) for n in names}
        self._saved_backend = km.Sessions.backend_for
        self.be = _Recorder()
        self.working = self.compacting = False
        self.hold = None
        km._compacting_now = lambda sid, *a, **k: self.compacting
        km._working_now = lambda sid: self.working
        km._limit_hold = lambda sid: self.hold
        km._push_all = lambda: None
        km._push_soon = lambda *a, **k: None
        km._mark_views_dirty = lambda *a, **k: None
        km._kernel_knows = lambda sid: True
        km._PENDING_OPS_FILE = jd.STATE / "pending-ops.json"
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pending_ops.clear()
        km._drain_hold.clear()
        km._moving.clear()
        getattr(km, "_parked_auth_picks", {}).clear()   # (getattr: the red-first run at main, which has none)
        self.sent = []
        self.client = {"send": lambda s: self.sent.append(json.loads(s))}

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._saved_backend
        km._pending_ops.clear()
        km._drain_hold.clear()
        km._moving.clear()
        getattr(km, "_parked_auth_picks", {}).clear()   # (getattr: the red-first run at main, which has none)
        jd.STATE = self.saved_state
        self.td.cleanup()

    def _pick(self, value):
        km._drive({"type": "setAuth", "id": SID, "value": value}, self.client)


class APickIsRecordedAtOnce(_KernelWorld):
    def test_a_pick_on_a_busy_session_is_recorded_at_once_and_parks_nothing(self):
        for busy in ("an open turn", "a compaction", "a queue", "a move", "an account hold"):
            self.be.calls.clear()
            km._pending_ops.clear()
            km._moving.clear()
            self.working = busy == "an open turn"
            self.compacting = busy == "a compaction"
            self.hold = {"reason": "spend", "resetsAt": None, "what": "x"} if busy == "an account hold" else None
            if busy == "a queue":
                km._pending_ops[SID] = [("send", "queued before the pick", "human")]
            if busy == "a move":
                km._moving.add(SID)
            self._pick("key")
            self.assertEqual(self.be.calls, [("auth", SID, "key")], "recorded at once behind %s" % busy)
            self.assertFalse([o for o in km._pending_ops.get(SID) or [] if o[0] == "auth"],
                             "no billing chip in the chat's queue behind %s" % busy)
        self.assertFalse([m for m in self.sent if m.get("type") == "warn"], self.sent)

    def test_a_newer_pick_reaches_the_setter_as_itself_never_appended_behind_the_older(self):
        self.working = True
        self._pick("key")
        self._pick("login")
        self.assertEqual(self.be.calls, [("auth", SID, "key"), ("auth", SID, "login")])
        self.assertNotIn(SID, km._pending_ops)

    def test_input_sent_after_a_pick_goes_out_on_the_current_account(self):
        self.working = True
        self._pick("key")                                  # mid-turn: recorded, nothing parked
        self.working = False                               # the turn ends; the relaunch is the backend's, at quiet
        km._send_or_park(self.be, SID, "and then the follow-up", echo="human")
        self.assertEqual(self.be.calls[-1], ("send", SID, "and then the follow-up"),
                         "a billing preference never holds a conversation")

    def test_following_the_default_is_the_same_door(self):
        self._pick("default")
        self.assertEqual(self.be.calls, [("auth", SID, "default")])


class ParkedBillingPicksMigrate(_KernelWorld):
    def test_the_latest_parked_pick_per_session_becomes_its_recorded_pick_and_the_queue_keeps_its_order(self):
        km._PENDING_OPS_FILE.write_text(json.dumps({
            SID: [["send", "first", "human"], ["auth", "key"], ["send", "second", "human"], ["auth", "login"]],
            OTHER: [["auth", "key"]]}))
        q = km._load_pending_ops()
        self.assertEqual(q, {SID: [("send", "first", "human"), ("send", "second", "human")]},
                         "billing ops leave the queue; the rest keeps its order, and a queue of picks alone goes")
        self.assertEqual(km._parked_auth_picks, {SID: "login", OTHER: "key"}, "the LATEST pick per session")
        km._pending_ops.update(q)
        km._migrate_parked_auth(self.be)
        self.assertEqual([c for c in self.be.calls if c[0] == "auth"], [("auth", SID, "login")],
                         "recorded on the backend that owns the session; a session it does not own is dropped")
        self.assertEqual(km._parked_auth_picks, {}, "once")
        on_disk = json.loads(km._PENDING_OPS_FILE.read_text())
        self.assertEqual(on_disk, {SID: [["send", "first", "human"], ["send", "second", "human"]]},
                         "the mirror is rewritten without them, so a later boot cannot re-apply one over a newer pick")


# ── the backend: the setting, its default, and the attach ──────────────────────────────────────────────────────────

class _Unit(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        script = Path(self.cfg) / "helper.sh"
        script.write_text("#!/bin/sh\necho 'synthetic-helper-output-default'\n")
        script.chmod(0o700)
        (Path(self.cfg) / "settings.json").write_text(json.dumps({"apiKeyHelper": str(script)}))
        self._managed_before = sb._cred.managed_settings_path
        sb._cred.managed_settings_path = lambda: os.path.join(self.cfg, "no-managed-settings.json")
        sb._cred.forget_helper_key()
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.be.login_ok = lambda: True

    def tearDown(self):
        sb._cred.managed_settings_path = self._managed_before
        sb._cred.forget_helper_key()
        os.environ["CLAUDE_CONFIG_DIR"] = self._cfg_before

    def _live(self, sid, name, **reg):
        """A live session whose CLI was launched as it stands now (a running thread; the loop never starts)."""
        sb.write_reg(self.d, sid, dict({"sid": sid, "name": name, "cwd": self.d, "alive": True}, **reg))
        s = sb.SdkSession(self.be, sb.read_reg(self.d, sid))
        s.thread = types.SimpleNamespace(is_alive=lambda: True, ident=None)
        s._cli_launch = s.launch_values()
        s.asked = []
        s.request_reconnect = lambda *a, **k: s.asked.append(k.get("why"))
        self.be.sessions[sid] = s
        return s


class TheDefaultIsWhatFollowersBill(_Unit):
    def test_a_change_of_default_retargets_every_follower_and_leaves_overrides_alone(self):
        follower = self._live(SID, "web")
        override = self._live(OTHER, "api", auth="key")
        self.assertTrue(self.be.set_auth_default("login"))
        self.assertTrue(follower._auth_pending, "the follower's CLI runs Automatic's billing: it goes pending")
        self.assertEqual(follower.asked, ["billing default"], "…and relaunches once nothing would be cut")
        self.assertTrue(sb.read_reg(self.d, SID)["authPending"])
        self.assertEqual(sb.read_reg(self.d, SID)["relaunchWait"]["picks"]["billing"], {"auth": "login", "authLogin": ""})
        self.assertEqual((override._auth_pending, override.asked), ("", []), "a pick of its own is untouched")
        # back to Automatic before the relaunch: the follower's CLI already runs that, so nothing is pending any more
        self.assertTrue(self.be.set_auth_default("auto"))
        self.assertEqual(follower._auth_pending, "")
        self.assertFalse(sb.read_reg(self.d, SID).get("authPending"))

    def test_the_launch_record_of_a_follower_carries_the_default_it_launches_on(self):
        s = self._live(SID, "web")
        self.assertEqual((s.launch_values()["auth"], s.launch_values()["authLogin"]), ("", ""), "Automatic")
        sb.write_sdk_default(self.d, auth="key", authExplicit=True)
        self.assertEqual(s.launch_values()["auth"], "key", "an explicit default is what it is set to bill")
        sb.write_sdk_default(self.d, auth="login", authExplicit=False)
        self.assertEqual(s.launch_values()["auth"], "", "a retired seed is not a default")
        p = self._live(OTHER, "api", auth="key")
        self.assertEqual(p.launch_values()["auth"], "key", "an override records itself")

    def test_spawn_and_fork_mint_no_override(self):
        sb.write_sdk_default(self.d, auth="key")             # a seed an older kernel left: read by nothing
        sid = self.be.spawn("web", self.d)
        self.assertNotIn("auth", sb.read_reg(self.d, sid))
        parent = self.be.spawn("api", self.d, auth="login")
        self.assertEqual(sb.read_reg(self.d, parent)["auth"], "login", "a billing its creator chose is its own pick")
        child = self.be.fork("api-thread", parent)
        self.assertNotIn("auth", sb.read_reg(self.d, child), "a fork or a thread follows the default, not its parent's pick")
        self.assertNotIn("authLogin", sb.read_reg(self.d, child))

    def test_a_pick_on_a_live_session_shows_pending_until_its_cli_carries_it(self):
        s = self._live(SID, "web", auth="login")
        self.assertTrue(self.be.set_auth(SID, "key"))
        snap = s.snapshot()
        self.assertTrue(snap["authPending"] and snap["authOwn"], snap)
        self.assertEqual(s.asked, ["billing"])
        self.assertTrue(self.be.set_auth(SID, "default"))     # Automatic bills the key here (a helper is configured)
        self.assertTrue(s.snapshot()["authPending"], "still not what its CLI was launched with (the login)")
        self.assertFalse(s.snapshot()["authOwn"])


class AMoveInFlightIsWork(_Unit):
    def test_a_moves_set_cwd_round_trip_is_not_quiet_and_its_end_carries_the_standing_ask(self):
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})
        s = sb.SdkSession(self.be, sb.read_reg(self.d, SID))
        self.assertTrue(s.quiet())
        s._cwd_request_open = True
        self.assertFalse(s.quiet(), "set_cwd is in flight: a relaunch now would end the CLI inside the relocation")
        calls = []
        s.loop = types.SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=lambda fn, *a: calls.append(fn))
        s._switch_wanted = "billing"
        s._end_cwd_request()
        self.assertTrue(s.quiet())
        self.assertIn(s._try_switch_reconnect, calls, "the round trip's end is what the standing relaunch waited for")

    def test_a_stale_settle_arm_alone_holds_no_relaunch(self):
        """A move whose turn-less result never came leaves its settle arm standing, and a reconnect is one of the events that
        lower it (tests/test_queued_sends_not_fused.py): gated on the arm, the relaunch and the arm would wait on each other."""
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True})
        s = sb.SdkSession(self.be, sb.read_reg(self.d, SID))
        s._move_settle_expected = True
        self.assertTrue(s.quiet())

    def test_move_raises_the_gate_around_its_request_and_lowers_it_however_the_request_ends(self):
        body = inspect.getsource(sb.SdkBackend.move)
        self.assertIn("s._cwd_request_open = True", body)
        self.assertIn("finally:\n            s._end_cwd_request()", body)


class TheAttachComparesBilling(psr._Harness):
    """A surviving CLI is compared with the billing the session is set to, waiting pick or not."""

    def _own_pick(self, auth):
        reg = self._reg()
        reg["auth"] = auth                                # the session's own pick
        reg["spawnedAtCli"] = "%s:%s" % (CLI_A["pid"], CLI_A["start"])   # the CLI the host still runs
        sb.write_reg(self.state, SID, reg)

    def test_a_launch_record_naming_other_billing_shows_pending_and_relaunches_when_quiet(self):
        self._own_pick("key")
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": {"auth": "login", "authLogin": ""}},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)
        self._connect(s, 1)
        self._settle()
        self.assertEqual(len(psr._Client.instances), 1, "never at the attach itself")
        self.assertTrue(s.snapshot()["authPending"])
        self.assertTrue(self._reg().get("authPending"))
        self.assertIn("billing", (self._reg().get("relaunchWait") or {}).get("picks") or {}, "on record for a later kernel")
        self.assertTrue(any("waiting picks (web): billing not carried" in l for l in self.lines), self.lines)
        before = time.time()
        be.retry_model_upgrades(time.time())               # the tick: the session is quiet
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client, "the relaunch")
        self._wait(lambda: not s.snapshot()["authPending"], "applied at the launch")
        self.assertEqual(s._launch_record["auth"], "key")
        self.assertNotIn("relaunchWait", self._reg())
        self.assertGreaterEqual(float(self._reg().get("authSwitchedAt") or 0), before, "the switch landed")

    def test_no_launch_record_cannot_vouch_and_relaunches_once(self):
        self._own_pick("key")
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": None},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}], survives=True)
        self._connect(s, 1)
        self._settle()
        self.assertTrue(s.snapshot()["authPending"])
        self.assertTrue(any("cannot be vouched for" in l for l in self.lines), self.lines)
        be.retry_model_upgrades(time.time())
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client, "the relaunch")
        self._wait(lambda: not s.snapshot()["authPending"], "applied")
        self._settle()
        be.retry_model_upgrades(time.time())
        self._settle()
        self.assertEqual(len(psr._Client.instances), 2, "once: the relaunch's own record vouches for it")

    def test_a_matching_record_relaunches_nothing_and_writes_nothing(self):
        self._own_pick("key")
        be, s = self._kernel([{"attach": True, "cli": CLI_A, "launch": "current"}], survives=True)
        self._connect(s, 1)
        self._settle()
        be.retry_model_upgrades(time.time())
        self._settle()
        self.assertEqual(len(psr._Client.instances), 1)
        self.assertFalse(s.snapshot()["authPending"])
        self.assertNotIn("relaunchWait", self._reg())


class ANewerPickReplacesAnUnappliedOne(psr._Harness):
    def setUp(self):
        super().setUp()
        # a fixture apiKeyHelper in this module's synthetic Claude Code settings, so the key side can be picked
        cfg = Path(os.environ["CLAUDE_CONFIG_DIR"])
        script = cfg / "helper.sh"
        script.write_text("#!/bin/sh\necho 'synthetic-helper-output-default'\n")
        script.chmod(0o700)
        (cfg / "settings.json").write_text(json.dumps({"apiKeyHelper": str(script)}))
        self._managed_before = sb._cred.managed_settings_path
        sb._cred.managed_settings_path = lambda: str(cfg / "no-managed-settings.json")
        sb._cred.forget_helper_key()

    def tearDown(self):
        super().tearDown()
        (Path(os.environ["CLAUDE_CONFIG_DIR"]) / "settings.json").unlink(missing_ok=True)
        sb._cred.managed_settings_path = self._managed_before
        sb._cred.forget_helper_key()

    def test_three_picks_while_a_monitor_runs_make_one_relaunch_onto_the_last(self):
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        c1 = self._connect(s, 1)
        self._a_turn_that_leaves_a_monitor(s, c1)
        self.assertTrue(be.set_auth(SID, "key"))
        self.assertTrue(be.set_auth(SID, "login"))
        self.assertTrue(be.set_auth(SID, "key"))
        self._settle()
        self.assertEqual(len(psr._Client.instances), 1, "the monitor holds the relaunch")
        self.assertTrue(s.snapshot()["authPending"])
        self.assertEqual(self._reg()["relaunchWait"]["picks"]["billing"], {"auth": "key", "authLogin": ""}, "the latest pick")
        s.enqueue("a message typed meanwhile")
        self._wait(lambda: "a message typed meanwhile" in c1.writes, "input goes out on the current CLI")
        self._push(c1, psr._result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1, session_id=psr.FSID,
                                   duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                                   usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None))
        self._wait(lambda: s.inflight == 0 and not s._cli_working, "that turn settled")
        self.assertEqual(len(psr._Client.instances), 1, "the monitor still runs")
        self._monitor_ends(c1)
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client, "the relaunch")
        self._wait(lambda: not s.snapshot()["authPending"], "applied")
        self.assertEqual(s._launch_record["auth"], "key")
        self._settle()
        be.retry_model_upgrades(time.time())
        self._settle()
        self.assertEqual(len(psr._Client.instances), 2, "one relaunch for three picks")


if __name__ == "__main__":
    unittest.main()
