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
import ast
import json
import os
import sys
import tempfile
import threading
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
        s._served_billing = (s._cli_launch["auth"], s._cli_launch["authLogin"])   # a connect this life read its record
        s.asked = []
        s.request_reconnect = lambda *a, **k: s.asked.append(k.get("why"))
        self.be.sessions[sid] = s
        return s

    def _quiet(self, sid, name, relaunched, **reg):
        """A live, quiet session on the real relaunch road (want_switch_reconnect → _try_switch_reconnect → the launch
        budget), its loop running what is scheduled at once; the relaunch itself (the teardown and the connect) is recorded
        instead of run, so the slot it took stays held until the test says its CLI came up (_release_launch_slot)."""
        s = self._live(sid, name, **reg)
        del s.request_reconnect                                    # the real road
        s.loop = types.SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=lambda fn, *a: fn(*a))
        s._do_request_reconnect = lambda defer=True: relaunched.append(s.sid)
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



SIDS = ["11111111-2222-3333-4444-5555cccc%04d" % i for i in range(6)]


class RelaunchesShareTheLaunchBudget(_Unit):
    """A relaunch is a CLI launch: it takes a slot of the machine-wide budget every spawn takes (BOOT_RESUME_CONCURRENCY),
    without blocking, and the next freed slot asks again. One change of default used to relaunch every follower at once."""

    def test_a_change_of_default_relaunches_its_followers_a_budget_at_a_time(self):
        relaunched = []
        ss = [self._quiet(sid, "s%d" % i, relaunched) for i, sid in enumerate(SIDS)]
        self.assertTrue(self.be.set_auth_default("login"))
        self.assertEqual(len(relaunched), sb.BOOT_RESUME_CONCURRENCY, "only as many as the budget launch at once")
        self.assertTrue(all(s._auth_pending for s in ss), "every follower shows its switch pending meanwhile")
        waiting = [s for s in ss if s.sid not in relaunched]
        self.assertTrue(all(s._switch_wanted for s in waiting), "the rest keep their ask, in line for a slot")
        first = next(s for s in ss if s.sid in relaunched)
        first._release_launch_slot()                     # its relaunched CLI is up: the slot is offered to the next in line
        self.assertEqual(relaunched[sb.BOOT_RESUME_CONCURRENCY:], [waiting[0].sid], "the first waiter, on the release")

    def test_a_waiter_that_went_busy_passes_the_slot_on(self):
        relaunched = []
        ss = [self._quiet(sid, "s%d" % i, relaunched) for i, sid in enumerate(SIDS[:sb.BOOT_RESUME_CONCURRENCY + 2])]
        self.assertTrue(self.be.set_auth_default("login"))
        a, b = [s for s in ss if s.sid not in relaunched]
        a.inflight = 1                                   # a turn started while it waited
        next(s for s in ss if s.sid in relaunched)._release_launch_slot()
        self.assertEqual(relaunched[-1], b.sid, "the busy one keeps its ask for its own quiet moment; the slot goes on")
        self.assertTrue(a._switch_wanted)

    def test_the_tick_carries_standing_asks_through_the_same_budget(self):
        relaunched = []
        ss = [self._quiet(sid, "s%d" % i, relaunched) for i, sid in enumerate(SIDS)]
        for s in ss:
            s._switch_wanted = "billing"
        self.be.retry_model_upgrades(time.time())
        self.assertEqual(len(relaunched), sb.BOOT_RESUME_CONCURRENCY)


class ABypassPickChangedBackLeavesTheLineForASlot(_Unit):
    """A bypass pick whose relaunch waits in line for a launch slot, changed back before a slot frees on a session launched
    in the mode the change back restores, with nothing else waiting or set aside: the relaunch is withdrawn with its place
    in line (SdkBackend._withdraw_mode_pick)."""

    def test_a_change_back_while_the_relaunch_waits_for_a_slot_leaves_the_line_and_the_freed_slot_relaunches_nothing(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                             # every slot held elsewhere
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(relaunched, [], "the premise: no slot is free")
        self.assertTrue(slots.waiting(s), "the premise: the relaunch waits in line for a slot")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "", "the change back withdrew the relaunch the pick waited for alone")
        self.assertFalse(slots.waiting(s), "and its place in line")
        slots.release()
        self.assertEqual(relaunched, [], "the freed slot relaunches nothing")

    def test_a_record_from_an_older_host_that_lacks_a_key_a_launch_now_holds_at_its_default_reads_as_equal(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")
        s._cli_launch = {k: v for k, v in s._cli_launch.items() if k != "subagentStandard"}
        self.assertEqual(s.launch_values()["subagentStandard"], sb.LAUNCH_KEY_DEFAULTS["subagentStandard"], "the premise")
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                             # every slot held elsewhere
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the relaunch waits in line for a slot")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "", "the change back withdrew the relaunch")
        self.assertFalse(slots.waiting(s), "and its place in line")


class AChangeBackBesideAnotherPickKeepsThePlaceForASlot(_Unit):
    """A bypass pick changed back while the relaunch waits in line for a launch slot and another pick waits too: the ask
    stands, the session keeps its place in line, and the freed slot relaunches it (SdkBackend._withdraw_mode_pick)."""

    def _no_free_slot(self):
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                             # every slot held elsewhere
        return slots

    def _launched_as_the_change_back_leaves_it(self, s):
        self.assertEqual({k: v for k, v in s.launch_values().items() if sb.launch_value(s._cli_launch, k) != v}, {},
                         "the premise: a launch now differs in nothing from the launch record")

    def test_with_a_billing_pick_waiting_first_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, auth="login")
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertTrue(slots.waiting(s), "the premise: the billing pick's relaunch waits in line for a slot")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertFalse(getattr(s, "_mode_ask_alone", False), "the premise: the ask is not the bypass pick's alone")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self.assertTrue(s._switch_wanted, "the ask stands for the billing pick")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_an_effort_pick_waiting_too_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched)
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the bypass pick's relaunch waits in line for a slot")
        self.assertTrue(self.be.set_effort(SID, "high"))
        self.assertTrue(s._effort_pending, "the premise: the effort pick waits too")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self.assertTrue(s._switch_wanted, "the ask stands for the effort pick")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_an_env_pick_renaming_the_ask_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the bypass pick's relaunch waits in line for a slot")
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_MODE": "strict"}))
        self.assertEqual(s._switch_wanted, "session env", "the premise: the env pick renamed the ask")
        self.assertTrue(self.be.set_env(SID, {}))                  # the env goes back
        self.assertEqual(s._switch_wanted, "session env", "the premise: the ask keeps the env pick's name")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self._launched_as_the_change_back_leaves_it(s)
        self.assertTrue(s._switch_wanted, "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_bypass_picked_again_after_an_env_pick_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_MODE": "strict"}))
        self.assertTrue(self.be.set_env(SID, {}))                  # the env goes back
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._switch_wanted, getattr(s, "_mode_ask_alone", None)), ("bypass mode", False),
                         "the premise: the second bypass pick renamed the env pick's ask, so the ask is not its alone")
        self.assertTrue(slots.waiting(s), "the premise: the relaunch waits in line for a slot")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self._launched_as_the_change_back_leaves_it(s)
        self.assertTrue(s._switch_wanted, "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_an_effort_mark_up_and_no_ask_of_its_own_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the bypass pick's relaunch waits in line for a slot")
        s._effort_pending = "high"                       # a mark with no ask of its own, set directly
        self.assertEqual((s._switch_wanted, getattr(s, "_mode_ask_alone", None)), ("bypass mode", True),
                         "the premise: the ask is the bypass pick's alone")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self._launched_as_the_change_back_leaves_it(s)
        self.assertTrue(s._switch_wanted, "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_a_billing_mark_up_and_no_ask_of_its_own_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the bypass pick's relaunch waits in line for a slot")
        s._auth_pending = "key"                          # a mark with no ask of its own, set directly
        self.assertEqual((s._switch_wanted, getattr(s, "_mode_ask_alone", None)), ("bypass mode", True),
                         "the premise: the ask is the bypass pick's alone")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self._launched_as_the_change_back_leaves_it(s)
        self.assertTrue(s._switch_wanted, "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_a_fast_mark_up_and_no_ask_of_its_own_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")   # launched in the mode the change back restores
        slots = self._no_free_slot()
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the bypass pick's relaunch waits in line for a slot")
        s._fast_pending = "on"                           # a mark with no ask of its own, set directly
        self.assertEqual((s._switch_wanted, getattr(s, "_mode_ask_alone", None)), ("bypass mode", True),
                         "the premise: the ask is the bypass pick's alone")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(getattr(s, "_mode_pending", ""), "", "the premise: the change back withdrew the bypass pick")
        self._launched_as_the_change_back_leaves_it(s)
        self.assertTrue(s._switch_wanted, "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")


class AChangeBackThatKeepsTheRelaunchKeepsThePlaceForASlot(_Unit):
    """A bypass pick whose relaunch waits in line for a launch slot, its ask the pick's alone, changed back before a slot
    frees: when a reason was set aside for the relaunch, the launch now differs from the launch record, no launch record is
    known, or a relaunch is on its way, the ask stands and the session keeps its place in line; with nothing on its way, the
    freed slot relaunches it (SdkBackend._withdraw_mode_pick)."""

    def _waits_in_line(self, relaunched, mode="default", record=True):
        s = self._quiet(SID, "web", relaunched, mode=mode)
        if not record:
            s._cli_launch = None                                     # a host too old to report what its CLI was launched with
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                                     # every slot held elsewhere
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        self.assertTrue(slots.waiting(s), "the premise: the relaunch waits in line for a slot")
        return s, slots

    def _a_change_back_keeps_the_place(self, s, slots, relaunched):
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._mode_pending, "", "the premise: the change back took the mark down")
        self.assertEqual(s._switch_wanted, "bypass mode", "the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")

    def test_with_a_retry_set_aside_for_the_relaunch_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s, slots = self._waits_in_line(relaunched)
        Path(self.d, sb.RETRY_UPGRADE_STORE).write_text("on\n")
        s._upgrade_retry = {"from": "Opus 5", "to": "Sonnet 5", "pick": "opus", "since": 0, "next": 0, "attempts": 0}
        self.be.retry_model_upgrades(time.time())                     # an ask stands: no attempt on top of it
        self.assertEqual((s._upgrade_retry["attempts"], s._switch_riders), (0, {"retry upgrade"}),
                         "the premise: the retry stood down to the pick's ask")
        self._a_change_back_keeps_the_place(s, slots, relaunched)

    def test_with_the_launch_record_in_another_mode_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s, slots = self._waits_in_line(relaunched, mode="acceptEdits")   # launched in a mode the change back does not restore
        self._a_change_back_keeps_the_place(s, slots, relaunched)
        self.assertEqual((s._cli_launch["mode"], s.launch_values()["mode"]), ("acceptEdits", "default"),
                         "the premise: the launch now differs from the record in its mode")

    def test_with_no_launch_record_a_change_back_keeps_the_place_in_line_and_the_freed_slot_relaunches_it(self):
        relaunched = []
        s, slots = self._waits_in_line(relaunched, record=False)
        self._a_change_back_keeps_the_place(s, slots, relaunched)

    def _a_rewind(self, s):
        recorded = s._do_request_reconnect
        del s._do_request_reconnect                                  # the real arm of the cut road, called as a rewind calls
        #                                                              it: the one relaunch left that waits for the turn's end
        s.request_reconnect(cut=True, cause="rewind")
        s._do_request_reconnect = recorded

    def test_with_a_rewind_waiting_for_the_turns_end_a_change_back_keeps_the_place_in_line(self):
        relaunched = []
        s, slots = self._waits_in_line(relaunched)
        s.inflight = 1                                               # a turn started while it waited
        self._a_rewind(s)
        self.assertEqual((s._reconnect_when_idle, s._switch_wanted), (True, "bypass mode"),
                         "the premise: the rewind waits for the turn's end, the pick's ask still standing")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch is on its way: the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")

    def test_with_a_rewind_armed_and_bypass_picked_again_before_the_loop_runs_a_change_back_keeps_the_place_in_line(self):
        relaunched = []
        s, slots = self._waits_in_line(relaunched)
        self._a_rewind(s)                                            # quiet: armed at once
        self.assertEqual((s._reconnect, s._switch_wanted), (True, ""), "the premise: the rewind's relaunch is armed")
        s.loop = types.SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=lambda fn, *a: None)   # the loop not run
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        self.assertTrue(slots.waiting(s), "the premise: still in line")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch is on its way: the ask stands")
        self.assertTrue(slots.waiting(s), "and keeps its place in line")


class AReleaseNeverMissesAWaiter(unittest.TestCase):
    """take() asked for a slot and, finding none, joined the queue in a second step: a release landing between the two found
    no waiter to offer the freed slot to, and the waiter that queued behind it sat until something else asked (review
    2026-10-08). Here the release is forced into exactly that gap. The semaphore's failed non-blocking acquire starts it on
    another thread and lets take() go on only once the release has gone as far as it can: all the way (the gap is open), or
    up to the lock the queue is read under, where it waits for take() to finish queueing (the gap is closed). Events order
    every step; nothing sleeps."""

    def test_a_release_between_the_failed_acquire_and_the_enqueue_hands_the_freed_slot_to_the_waiter(self):
        slots = sb._LaunchSlots(1)
        self.assertTrue(slots.acquire(blocking=False))   # the one slot, held by a launch in flight
        progressed = threading.Event()                   # the release finished, or has to wait for the slots' lock
        releaser = []

        class _WatchedLock:
            """The slots' lock, saying when a thread has to wait for it because another holds it."""
            def __init__(self):
                self._l, self.owner = threading.Lock(), None

            def __enter__(self):
                if self.owner not in (None, threading.get_ident()):
                    progressed.set()
                self._l.acquire()
                self.owner = threading.get_ident()
                return self

            def __exit__(self, *exc):
                self.owner = None
                self._l.release()

        real = slots._sem

        class _GapSem:
            """The slots' semaphore, releasing the held slot on another thread right after take()'s acquire finds none."""
            def acquire(self, blocking=True, timeout=None):
                got = real.acquire(blocking, timeout)
                if not got and not releaser:
                    t = threading.Thread(target=lambda: (slots.release(), progressed.set()), daemon=True)
                    releaser.append(t)
                    t.start()
                    if not progressed.wait(10):          # a hang guard only: the event is the ordering
                        raise AssertionError("the release neither finished nor reached the slots' lock")
                return got

            def release(self):
                real.release()

        slots._lock, slots._sem = _WatchedLock(), _GapSem()

        class _Waiter:
            sid, ended, took = OTHER, False, None

            def _schedule_on_loop(self, fn):
                fn()                                     # its loop runs what is scheduled at once
                return True

            def _slot_offered(self):
                self.took = slots.take(self)             # an offered relaunch asks again (SdkSession._slot_offered)

        w = _Waiter()
        self.assertFalse(slots.take(w), "no slot at the ask: it queues")
        releaser[0].join(10)
        self.assertFalse(releaser[0].is_alive(), "the release finished")
        self.assertIs(w.took, True, "the slot the release freed went to the waiter, on that release")
        self.assertFalse(slots.waiting(w), "and it left the queue with it")


class AnUnknownLaunchRecordDecidesNothing(_Unit):
    """With no launch record on hand (between a teardown and the next connect, during an attach, while a connect keeps
    failing) a pick is recorded and the next connect's reconcile decides; it used to read as a mismatch."""

    def test_following_the_default_it_already_runs_relaunches_nothing(self):
        s = self._live(SID, "web")                       # follows Automatic, its last CLI launched on it
        s._cli_launch = None                             # between a teardown and the next connect
        self.assertTrue(self.be.set_auth(SID, "default"))
        self.assertEqual((s._auth_pending, s.asked), ("", []), "nothing to apply, nothing decided before the record")
        self.assertFalse(sb.read_reg(self.d, SID).get("authPending"))
        s._reconcile_waiting_picks(s.launch_values(), True)
        self.assertFalse(sb.read_reg(self.d, SID).get("authSwitchedAt"), "the same billing again lands nothing")
        self.assertFalse(s._switch_wanted)

    def test_a_real_change_is_decided_at_the_connect(self):
        s = self._live(SID, "web")
        s._cli_launch = None
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s.asked, [])
        before = time.time()
        s._reconcile_waiting_picks(s.launch_values(), True)          # the CLI that came up carries the pick
        self.assertGreaterEqual(float(sb.read_reg(self.d, SID).get("authSwitchedAt") or 0), before, "the switch landed")
        s2 = self._live(OTHER, "api")
        s2._cli_launch = None
        self.assertTrue(self.be.set_auth(OTHER, "key"))
        s2._reconcile_waiting_picks(dict(s2.launch_values(), auth=""), True)   # launched before the pick
        self.assertTrue(s2._auth_pending and s2._switch_wanted, "carried by nothing yet: pending, and a relaunch asked for")


class APickBackToWhatRunsDropsItsRelaunch(_Unit):
    def test_the_relaunch_a_billing_pick_asked_for_goes_when_the_pick_goes_back(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, auth="login")
        s.inflight = 1                                   # busy: the ask stands
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self.assertEqual((s._switch_wanted, s._auth_pending), ("", ""), "nothing left to apply")
        s.inflight = 0
        s._try_switch_reconnect()
        self.assertEqual(relaunched, [])

    def test_an_ask_with_another_reason_stands(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, auth="login")
        s.inflight = 1
        s.want_switch_reconnect("retry upgrade")         # a reason no launch record shows
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "retry upgrade", "a pick's reason never hides it")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self.assertEqual(s._switch_wanted, "retry upgrade")


class ACliThatEndsLandsTheWaitingSwitch(_Unit):
    """When the CLI serving a session ends with a switch waiting (a crash, a close, a host that died), its next launch
    carries the billing, so the switch has landed: the dormant row stops reading the dead CLI's side as pending."""

    def _pending_switch(self):
        s = self._live(SID, "web", auth="login", apiKeyAuth=False)
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertTrue(sb.read_reg(self.d, SID).get("authPending"))
        s.inflight = 0
        return s

    def test_the_switch_lands_and_the_dead_clis_report_goes(self):
        s = self._pending_switch()
        before = time.time()
        self.be._on_session_gone(s)
        reg = sb.read_reg(self.d, SID)
        self.assertFalse(reg.get("authPending"))
        self.assertNotIn("billing", (reg.get("relaunchWait") or {}).get("picks") or {})
        self.assertNotIsInstance(reg.get("apiKeyAuth"), bool, "the ended CLI's report describes nothing now")
        self.assertGreaterEqual(float(reg.get("authSwitchedAt") or 0), before)
        row = self.be.live_sessions()[SID]
        self.assertEqual((row["authLive"], row["authPending"], row["auth"]), ("", False, "key"))

    def test_a_detach_lands_nothing_its_host_keeps_the_cli(self):
        s = self._pending_switch()
        s.detached = True
        self.be._on_session_gone(s)
        reg = sb.read_reg(self.d, SID)
        self.assertTrue(reg.get("authPending"))
        self.assertIs(reg.get("apiKeyAuth"), False)
        self.assertFalse(reg.get("authSwitchedAt"))


class ALiveHostStillServes(_Unit):
    def test_a_pick_on_a_session_whose_host_still_runs_its_cli_lands_nothing_before_the_attach(self):
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True, "auth": "login",
                                   "apiKeyAuth": False})
        self.be._lease_survives = lambda sid, **_: True       # an unattached comment thread, an attach that stood down
        self.assertTrue(self.be.set_auth(SID, "key"))
        reg = sb.read_reg(self.d, SID)
        self.assertFalse(reg.get("authSwitchedAt"), "the old account's CLI still serves it")
        self.assertIs(reg.get("apiKeyAuth"), False, "…and its report stands until the attach compares")


class AnUnreadableLeaseIsNoEvidenceTheHostIsGone(_Unit):
    """_lease_survives read a lease it could not read as 'no host', and said nothing (review 2026-10-08). No lease file is no
    host; a lease that exists and cannot be read is said in the problem log once per episode, and every caller answers as
    if the host may still run the CLI: no landing, no 'not serving', no boot heal, no dropped flag, the waiting marks kept."""

    KEY = ("lease-unreadable", SID)

    def setUp(self):
        super().setUp()
        self.lines = []
        self.be._log_cb = lambda m: self.lines.append(str(m))

    def _unreadable(self, how="garbled"):
        p = sb.lease_path(self.d, SID)
        p.parent.mkdir(parents=True, exist_ok=True)
        if how == "garbled":
            p.write_text('{"sid": ')                     # not a lease record (write_lease is atomic: no reader sees half)
        else:
            p.mkdir()                                    # the read fails with an I/O error (IsADirectoryError)
        return p

    def _said(self, be=None):
        return [r for r in (be or self.be).problems() if r.get("key") == self.KEY]

    def test_no_lease_is_no_host_and_an_unreadable_one_answers_as_its_caller_states_said_once_per_episode(self):
        for answer in (True, False):
            self.assertIs(self.be._lease_survives(SID, if_unreadable=answer), False, "no lease file: no host")
        self.assertEqual(self._said(), [], "and nothing to say")
        p = self._unreadable()
        self.assertIs(self.be._lease_survives(SID, if_unreadable=True), True)
        self.assertIs(self.be._lease_survives(SID, if_unreadable=False), False)
        self.assertIs(self.be._lease_survives(SID, if_unreadable=True), True)
        rows = self._said()
        self.assertEqual(len(rows), 1, "one problem row for the episode, however often it is asked")
        self.assertIn("cannot read the session host's lease", rows[0]["text"])
        self.assertEqual(rows[0].get("count"), 1)
        p.unlink()                                       # it reads again (as no lease): the episode ends
        self.assertIs(self.be._lease_survives(SID, if_unreadable=True), False)
        self.assertEqual(sum("lease reads again" in l for l in self.lines), 1, self.lines)
        self._unreadable(how="io")                       # a new episode is said again
        self.assertIs(self.be._lease_survives(SID, if_unreadable=True), True)
        self.assertEqual(sum("cannot read the session host's lease" in l for l in self.lines), 2, self.lines)
        self.assertIn("IsADirectoryError", self.lines[-1])
        self.assertEqual(self._said()[0].get("count"), 2, "the ring counts the second episode on the same row")

    def test_a_pick_while_the_lease_cannot_be_read_lands_nothing(self):
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True, "auth": "login",
                                   "apiKeyAuth": False})
        self._unreadable()
        self.assertTrue(self.be.set_auth(SID, "key"))
        reg = sb.read_reg(self.d, SID)
        self.assertFalse(reg.get("authSwitchedAt"), "a host may still run the old account's CLI: serving, no landing")
        self.assertIs(reg.get("apiKeyAuth"), False, "…and that CLI's report stands")
        self.assertEqual(len(self._said()), 1, "said")

    def test_a_cli_end_while_the_lease_cannot_be_read_lands_nothing(self):
        s = self._live(SID, "web", auth="login", apiKeyAuth=False)
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertTrue(sb.read_reg(self.d, SID).get("authPending"))
        s.inflight = 0
        self._unreadable()
        self.be._on_session_gone(s)
        reg = sb.read_reg(self.d, SID)
        self.assertTrue(reg.get("authPending"), "the thread ended, but a host may still run the CLI the switch waits on")
        self.assertFalse(reg.get("authSwitchedAt"), "no landing stamped")
        self.assertIs(reg.get("apiKeyAuth"), False)
        self.assertEqual(len(self._said()), 1, "said")

    def test_the_boot_leaves_what_a_host_may_still_own(self):
        """A kernel boots over a session whose lease cannot be read: its awaiting stands, a held send past the age line is
        not flagged never delivered, a /clear its CLI had taken relights the bracket, and a pick waiting for its relaunch
        keeps its mark. Each of these the boot does when the lease reads as no host."""
        old = int(time.time()) - int(sb.REDELIVER_MAX_AGE_S) - 600   # read as gone, it would be flagged without a scan
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True,
                                   "echoes": [{"t": old, "text": "a synthetic held send", "uuid": "echo:held-1"}],
                                   "clearingTaken": ["echo:clear-1"], "effortPending": True,
                                   "relaunchWait": {"picks": {"effort": {"effort": "high"}}}})
        sb.append_awaiting(self.d, SID, True)
        self._unreadable()
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        self.assertIs(sb.last_awaiting(self.d, SID), True, "its awaiting may be real: not healed")
        echo = next(a for a in be.live_atoms(SID) if a.get("_echo_text") == "a synthetic held send")
        self.assertFalse(echo.get("dropped"), "the host may still hold the send: not flagged")
        s = sb.SdkSession(be, sb.read_reg(self.d, SID))
        self.assertEqual(s._taken_clear_qids, ["echo:clear-1"], "the take is restored")
        self.assertTrue(s._clearing, "…and the bracket relit")
        self.assertTrue(sb.read_reg(self.d, SID).get("effortPending"), "the pick still waits for its relaunch")
        self.assertEqual(len(self._said(be)), 1, "said once across every reading the boot made")


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


# ── a change back withdraws the bypass pick's relaunch only when it would change nothing (SdkBackend._withdraw_mode_pick) ──

LACKS_OPT_IN = {"fast_mode_state": "off", "fast_mode_disabled_reason": "sdk_opt_in_required"}   # the CLI's own report


class _ABypassPickChangedBack(_Unit):
    """A live session on the real relaunch road, the loop-side re-check included (the relaunch is armed, never run), launched
    in the default mode, so changing a bypass pick back restores everything its launch record holds."""

    def _session(self, queued=None, **reg):
        s = self._live(SID, "web", **dict({"mode": "default"}, **reg))
        del s.request_reconnect                                       # the real road
        run = (lambda fn, *a: queued.append((fn, a))) if queued is not None else (lambda fn, *a: fn(*a))
        s.loop = types.SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=run)
        return s

    @staticmethod
    def _run(queued):
        while queued:
            fn, a = queued.pop(0)
            fn(*a)

    def _bypass_waits(self, s, queued=None):
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(queued or [])
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")

    def _arm_retry(self, s):
        Path(self.d, sb.RETRY_UPGRADE_STORE).write_text("on\n")
        s._upgrade_retry = {"from": "Opus 5", "to": "Sonnet 5", "pick": "opus", "since": 0, "next": 0, "attempts": 0}

    def _opt_in_lost(self, queued=None):
        """Always fast on, an Opus session whose CLI was launched with the opt-in (its launch record says so) and lost it to
        a literal '/fast off' since, so the record cannot show that a launch now would give it back."""
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(queued, model="opus")
        self.assertTrue(s._cli_launch["fast"], "the premise: the launch record carries the opt-in")
        s._fast_unlocked, s._fast_off_sent = False, True
        return s

    def _change_back(self, s):
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._mode_pending, "", "the premise: the change back took the mark down")

    def _relaunched_at_quiet(self, s, queued=None):
        s.inflight, s._cli_working = 0, False
        s._try_switch_reconnect()
        self._run(queued or [])
        return s._reconnect


class AWantSetAsideForThePicksRelaunchKeepsItOnAChangeBack(_ABypassPickChangedBack):
    """A reason other than a pick that stood down to the bypass pick's relaunch, was folded into it, or was put off by its
    carry, keeps that relaunch when the pick is changed back, and the relaunch is armed at the first quiet moment."""

    def test_a_due_model_retry_that_stood_down_to_the_picks_ask(self):
        s = self._session()
        self._arm_retry(s)
        self._bypass_waits(s)
        self.be.retry_model_upgrades(time.time())                     # an ask stands: no attempt on top of it
        self.assertEqual(s._upgrade_retry["attempts"], 0, "the premise: the retry stood down to the pick's ask")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the retry stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))

    def test_a_retry_that_stood_down_to_the_armed_relaunch_a_veto_then_cut(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertTrue(self._relaunched_at_quiet(s), "the premise: the pick's relaunch is armed")
        self._arm_retry(s)                                            # armed after the carry, due at this tick
        self.assertEqual(s._switch_riders, set(), "the premise: nothing set aside yet")
        self.be.retry_model_upgrades(time.time())
        self.assertEqual(s._upgrade_retry["attempts"], 1, "the premise: the attempt stood down to the armed relaunch")
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the retry stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))

    def test_an_always_fast_ask_made_between_the_carry_and_the_arm_then_a_veto(self):
        q = []
        s = self._opt_in_lost(q)
        self._bypass_waits(s, q)
        s.inflight = 0
        self.assertTrue(s._try_switch_reconnect(), "the premise: the pick's ask is carried, its arm not run yet")
        self.assertEqual(self.be.apply_model_switches(), 1, "the premise: Always fast asks, no ask standing")
        self._run(q)                                                  # the arm, then the Always fast ask's own carry
        self.assertTrue(s._reconnect, "the premise: the pick's relaunch is armed")
        s.inflight = 1
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in was folded into stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_a_retry_asked_after_the_arm_whose_carry_had_not_run_at_the_veto(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        self._arm_retry(s)                                            # armed after the carry, due at this tick
        self.assertEqual(s._switch_riders, set(), "the premise: nothing set aside yet")
        self.be.retry_model_upgrades(time.time())                     # its ask's carry waits on the loop
        s.inflight = 1
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch first")
        self._run(q)
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the retry asked for stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))

    def test_an_always_fast_ask_that_stood_down_to_the_picks_ask_whose_carry_a_busy_recheck_handed_back(self):
        q = []
        s = self._opt_in_lost(q)
        self._bypass_waits(s, q)
        self.assertEqual(self.be.apply_model_switches(), 0, "the premise: Always fast stood down to the pick's ask")
        self.assertIsNone(getattr(s, "_upgrade_retry", None), "the premise: no upgrade retry is armed at the carry")
        s.inflight = 0
        self.assertTrue(s._try_switch_reconnect(), "the premise: the pick's ask is carried")
        s._cli_working = True                                         # the CLI opened a turn itself before the arm ran
        self._run(q)
        self.assertEqual((s._reconnect, s._switch_wanted), (False, "bypass mode"), "the premise: the re-check handed it back")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_an_always_fast_ask_set_aside_keeps_the_relaunch_at_a_second_change_back(self):
        q = []
        s = self._opt_in_lost(q)
        self._bypass_waits(s, q)
        self.assertEqual(self.be.apply_model_switches(), 0, "the premise: Always fast stood down to the pick's ask")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the first change back kept the relaunch")
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: carried and armed")
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))   # picked again while the relaunch is armed
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch")
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def _the_carry_puts_the_retry_off_then_a_busy_recheck_hands_the_ask_back(self, due_in):
        q = []
        s = self._session(queued=q)
        self._arm_retry(s)
        s._upgrade_retry["next"] = time.time() + due_in if due_in else 0
        self._bypass_waits(s, q)                                      # no retry tick while the pick's ask stands
        s.inflight = 0
        self.assertTrue(s._try_switch_reconnect(), "the premise: the pick's ask is carried")
        self.assertGreater(s._upgrade_retry["next"], time.time() + 300, "the premise: the carry put the retry's attempt off")
        s._cli_working = True                                         # the CLI opened a turn itself before the arm ran
        self._run(q)
        self.assertEqual((s._reconnect, s._switch_wanted), (False, "bypass mode"), "the premise: the re-check handed it back")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the carry put the retry off for stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))

    def test_a_due_retry_put_off_by_the_carry_of_the_picks_ask_that_a_busy_recheck_handed_back(self):
        self._the_carry_puts_the_retry_off_then_a_busy_recheck_hands_the_ask_back(0)

    def test_a_retry_not_yet_due_put_off_by_the_carry_of_the_picks_ask_that_a_busy_recheck_handed_back(self):
        self._the_carry_puts_the_retry_off_then_a_busy_recheck_hands_the_ask_back(200)

    def test_a_retry_not_yet_due_put_off_by_the_carry_of_the_picks_ask_whose_armed_relaunch_a_veto_cut(self):
        s = self._session()
        self._arm_retry(s)
        s._upgrade_retry["next"] = time.time() + 200                  # due in 200 s, and no retry tick runs here
        self._bypass_waits(s)
        self.assertTrue(self._relaunched_at_quiet(s), "the premise: the pick's relaunch is armed")
        self.assertGreater(s._upgrade_retry["next"], time.time() + 300, "the premise: the carry put the retry's attempt off")
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the carry put the retry off for stands")
        self.assertTrue(self._relaunched_at_quiet(s))

    def test_an_always_fast_ask_that_stood_down_to_the_picks_ask(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        self.assertEqual(self.be.apply_model_switches(), 0, "the premise: Always fast stood down to the pick's ask")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_the_clis_report_that_it_lacks_the_opt_in_stood_down_to_the_picks_ask(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        s._adopt_fast_state(dict(LACKS_OPT_IN))                       # this turn's init
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the opt-in's ask stood down to the pick's")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_a_model_change_that_wants_the_opt_in_stood_down_to_the_picks_ask(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        s._after_model_change("Sonnet 5", "Opus 5")
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the opt-in's ask stood down to the pick's")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def _a_report_on_a_cli_this_kernel_launched_with_the_opt_in(self, attach=False, fast_off=False, own=False):
        """An Opus session whose CLI this kernel launched carrying the fast-mode opt-in (its own launch record), wanted by
        Always fast or, with `own`, by the session's own Fast pick; the bypass pick's own ask, then the CLI's report that it
        lacks the opt-in. The relaunch the opt-in's ask stood down to stands after the change back, with the opt-in."""
        if not own:
            Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(model="opus", **({"fast": True} if own else {}))
        s._launch_record = dict(s._cli_launch)
        self.assertTrue(s._launch_record["fast"], "the premise: this kernel's launch record carries the opt-in")
        s._host_is_attach, s._fast_unlocked, s._fast_off_sent = attach, False, fast_off
        self._bypass_waits(s)
        s._adopt_fast_state(dict(LACKS_OPT_IN))
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the opt-in's ask stood down to the pick's")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the opt-in stood down to stands")
        self.assertTrue(self._relaunched_at_quiet(s))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_the_report_of_a_cli_this_kernel_launched_with_the_opt_in_after_a_fast_off_went_to_it(self):
        self._a_report_on_a_cli_this_kernel_launched_with_the_opt_in(fast_off=True)

    def test_the_report_of_a_cli_this_kernel_launched_with_the_opt_in_at_an_attach_to_it(self):
        self._a_report_on_a_cli_this_kernel_launched_with_the_opt_in(attach=True)

    def test_the_report_of_a_cli_launched_with_the_opt_in_the_sessions_own_fast_pick_wants(self):
        self._a_report_on_a_cli_this_kernel_launched_with_the_opt_in(fast_off=True, own=True)

    def test_an_always_fast_ask_a_busy_recheck_dropped_beside_the_picks_ask_before_an_attach(self):
        q = []
        s = self._opt_in_lost(q)
        s.inflight = 0
        self.assertEqual(self.be.apply_model_switches(), 1, "the premise: Always fast asks")
        fn, a = q.pop(0)
        fn(*a)
        self.assertEqual(s._switch_ask_pending, "always fast", "the premise: its ask is carried, its arm not run yet")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        s._cli_working = True                                         # the CLI opened a turn itself before the arm ran
        self._run(q)
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._reconnect), ("bypass mode", "", False),
                         "the premise: the re-check dropped the carried ask, since the pick's stands")
        s._cli_working = False
        s._do_request_reconnect(defer=False)                          # a planned reconnect, as a host re-exec's
        self.assertTrue(s._reconnect, "the premise: armed")
        s._reconnect = False                                          # the kernel attaches to the same CLI again
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the reconcile's ask")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the dropped ask was set aside for stands")
        self.assertTrue(self._relaunched_at_quiet(s, q))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")


class AChangeBackWithdrawsOnlyARelaunchThatWouldChangeNothing(_ABypassPickChangedBack):
    """The change back withdraws the bypass pick's relaunch only when a launch now differs in nothing from the serving CLI's
    launch record and nothing was set aside for it; Apply now and `romp apply` then report what still waits."""

    def test_with_nothing_set_aside_and_nothing_different_the_relaunch_is_withdrawn(self):
        s = self._session()
        self._bypass_waits(s)
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")

    def test_with_no_retry_armed_a_carry_that_a_busy_recheck_handed_back_sets_nothing_aside_so_the_relaunch_is_withdrawn(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertIsNone(getattr(s, "_upgrade_retry", None), "the premise: no upgrade retry is armed")
        s.inflight = 0
        self.assertTrue(s._try_switch_reconnect(), "the premise: the pick's ask is carried")
        s._cli_working = True                                         # the CLI opened a turn itself before the arm ran
        self._run(q)
        self.assertEqual((s._reconnect, s._switch_wanted), (False, "bypass mode"), "the premise: the re-check handed it back")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def _an_opt_in_that_cannot_take(self):
        """Always fast on, an Opus session whose CLI this kernel launched with the opt-in and sent no '/fast off', and whose
        first report said it lacks the opt-in: a relaunch would carry the same flag and hear the same."""
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(model="opus")
        s._launch_record = dict(s._cli_launch)
        s._host_is_attach, s._fast_unlocked, s._fast_off_sent = False, False, False
        s._adopt_fast_state(dict(LACKS_OPT_IN))
        self.assertEqual(s._switch_wanted, "", "the premise: nothing asked")
        self.assertTrue(s._opt_in_cannot_take(), "the premise: the opt-in cannot take on this CLI")
        return s

    def test_always_fast_with_an_opt_in_that_cannot_take_sets_nothing_aside_so_the_relaunch_is_withdrawn(self):
        s = self._an_opt_in_that_cannot_take()
        self._bypass_waits(s)
        self.assertEqual(self.be.apply_model_switches(), 0, "the premise: Always fast asks nothing")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")

    def test_a_model_change_with_an_opt_in_that_cannot_take_sets_nothing_aside_so_the_relaunch_is_withdrawn(self):
        s = self._an_opt_in_that_cannot_take()
        self._bypass_waits(s)
        s._after_model_change("Sonnet 5", "Opus 5")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")

    def test_the_report_of_an_attached_cli_whose_opt_in_cannot_take_sets_nothing_aside_so_the_relaunch_is_withdrawn(self):
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(model="opus")
        s._host = types.SimpleNamespace(hello={"cli": {"pid": 4242, "start": "1700000000"}})
        self.be._update_reg(SID, fastOptInLacking="4242:1700000000")   # the launching kernel's word on this CLI
        s._host_is_attach, s._fast_unlocked, s._fast_off_sent = True, False, False
        self._bypass_waits(s)
        self.assertTrue(s._opt_in_cannot_take(), "the premise: the opt-in cannot take on this CLI")
        s._adopt_fast_state(dict(LACKS_OPT_IN))
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")

    def test_the_report_of_a_cli_this_kernel_launched_with_the_opt_in_and_sent_no_fast_off_sets_nothing_aside_so_the_relaunch_is_withdrawn(self):
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(model="opus")
        s._launch_record = dict(s._cli_launch)
        self.assertTrue(s._launch_record["fast"], "the premise: this kernel's launch record carries the opt-in")
        s._host_is_attach, s._fast_unlocked, s._fast_off_sent = False, False, False
        self._bypass_waits(s)
        s._adopt_fast_state(dict(LACKS_OPT_IN))
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")

    def test_a_bypass_pick_made_while_another_picks_ask_was_carried_is_not_its_own_so_a_change_back_keeps_the_relaunch(self):
        q = []
        s = self._session(queued=q, auth="login")
        s.inflight = 0
        self.assertTrue(self.be.set_auth(SID, "key"))
        fn, a = q.pop(0)
        fn(*a)
        self.assertEqual(s._switch_ask_pending, "billing", "the premise: the billing pick's ask is carried, its arm not run yet")
        self.assertTrue(self.be.set_auth(SID, "login"))                # the pick goes back to what the CLI runs
        self.assertEqual(s._switch_wanted, "", "the premise: no ask stands")
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the pick's ask stands")
        s._cli_working = True                                         # the CLI opened a turn itself before the arm ran
        self._run(q)
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._reconnect), ("bypass mode", "", False),
                         "the premise: the re-check dropped the carried ask, since the pick's stands")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the ask is not the bypass pick's own: it stands")

    def test_with_no_launch_record_the_relaunch_stands(self):
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(model="opus")
        s._cli_launch = None                                          # a host too old to report what its CLI was launched with
        self._bypass_waits(s)
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "nothing shows the relaunch would change nothing: it stands")
        self.assertTrue(self._relaunched_at_quiet(s))
        self.assertTrue(s.launch_values()["fast"], "and carries the opt-in Always fast wants")

    def test_with_a_rewind_waiting_for_the_turns_end_the_ask_stands_and_apply_now_runs_it(self):
        s = self._session()
        self._bypass_waits(s)
        s.request_reconnect(cut=True, cause="rewind")
        self.assertTrue(s._reconnect_when_idle, "the premise: the rewind waits for the turn's end")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch is on its way: the ask stands")
        doors = []
        self.be._relaunch = lambda sid, cause: doors.append(cause) or ""
        self.assertEqual((self.be.apply_now(SID), doors), ("", ["apply"]), "and Apply now runs it")

    def test_with_a_rewinds_relaunch_armed_the_ask_stands_and_no_line_says_it_was_withdrawn(self):
        q = []
        s = self._session(queued=q)
        s.inflight = 0
        s.request_reconnect(cut=True, cause="rewind")
        self._run(q)
        self.assertTrue(s._reconnect, "the premise: the rewind's relaunch is armed")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        said = []
        self.be._log = lambda msg, *a, **k: said.append(msg)
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch is on its way: the ask stands")
        self.assertEqual([m for m in said if "withdrawn" in m], [], "and no line says a relaunch was withdrawn")

    def test_with_another_reasons_ask_carried_the_reconciles_ask_stands_and_no_line_says_it_was_withdrawn(self):
        s = self._session()
        s.inflight = 1
        s.mode, s._mode_pending = "bypassPermissions", "bypassPermissions"   # a bypass pick recorded as waiting
        self.be._note_pick_waits(s, "mode", "bypass mode")
        s._switch_ask_pending = "always fast"                         # another reason's ask, carried, its arm not run yet
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)   # an attach
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the reconcile's ask")
        said = []
        self.be._log = lambda msg, *a, **k: said.append(msg)
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch is on its way: the ask stands")
        self.assertEqual([m for m in said if "withdrawn" in m], [], "and no line says a relaunch was withdrawn")

    def _always_fast_off_across_a_restart(self):
        """Always fast is off now; the CLI a session host kept across a kernel restart was launched with the opt-in it
        wanted then, in the default mode, and the bypass pick made before the restart still waits."""
        sb.write_reg(self.d, SID, {"sid": SID, "name": "web", "cwd": self.d, "alive": True, "mode": "bypassPermissions",
                                   "model": "opus", "relaunchWait": {"why": "bypass mode", "t": 1700000150,
                                                                     "picks": {"mode": {"mode": "bypassPermissions"}}}})
        self.be._lease_survives = lambda sid, **_: True
        s = sb.SdkSession(self.be, sb.read_reg(self.d, SID))
        s.thread = types.SimpleNamespace(is_alive=lambda: True, ident=None)
        s.loop = types.SimpleNamespace(is_closed=lambda: False, call_soon_threadsafe=lambda fn, *a: fn(*a))
        self.be.sessions[SID] = s
        s.inflight = 1
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default", fast=True), False)   # the attach
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        self.assertFalse(s.launch_values()["fast"], "the premise: a launch now carries no opt-in")
        return s

    def test_with_always_fast_turned_off_the_relaunch_that_drops_the_opt_in_stands(self):
        s = self._always_fast_off_across_a_restart()
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch would take the opt-in away: it stands")
        self.assertTrue(self._relaunched_at_quiet(s))

    def test_apply_now_reports_the_relaunch_an_opt_in_ask_stood_down_to(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        s._adopt_fast_state(dict(LACKS_OPT_IN))
        self._change_back(s)
        doors = []
        self.be._relaunch = lambda sid, cause: doors.append(cause) or ""
        self.assertEqual((self.be.apply_now(SID), doors), ("", ["apply"]), "a relaunch waits, and Apply now runs it")

    def test_romp_apply_reports_the_relaunch_that_drops_the_opt_in(self):
        from unittest import mock
        self._always_fast_off_across_a_restart()
        self._change_back(self.be.sessions[SID])
        doors = []
        self.be._relaunch = lambda sid, cause: doors.append(cause) or ""
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: self.be)), \
                mock.patch.object(km, "_kernel_knows", lambda sid: True):
            detail = km._apply_now_detail(SID)                        # the answer of the /apply route `romp apply` posts to
        self.assertEqual((detail, doors), ("", ["apply"]), "a relaunch waits, and romp apply runs it")

    def test_a_change_back_to_a_mode_switched_to_live_since_the_launch_keeps_the_relaunch(self):
        s = self._session()
        s.set_mode_live = lambda mode, prev="default": None
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))         # a live switch: no relaunch
        self.assertEqual(s._cli_launch["mode"], "default", "the premise: the launch record holds the launch's mode")
        self._bypass_waits(s)
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the launch now differs from the record in its mode: it stands")
        self.assertTrue(self._relaunched_at_quiet(s))

    def test_a_retry_set_aside_whose_arm_cleared_without_a_launch_still_keeps_the_relaunch(self):
        s = self._session()
        self._arm_retry(s)
        self._bypass_waits(s)
        self.be.retry_model_upgrades(time.time())                     # an ask stands: no attempt on top of it
        s._note_model_served("Opus 5")                                # a parent turn served on the model it fell from
        self.assertIsNone(s._upgrade_retry, "the premise: the retry's arm cleared")
        self._change_back(s)
        self.assertEqual(s._switch_wanted, "bypass mode", "the retry's reason is still set aside: the relaunch stands")
        self.assertTrue(self._relaunched_at_quiet(s))


class ThePendingBypassMarkFollowsTheLiveMode(_ABypassPickChangedBack):
    """The pending-bypass mark matches the session's mode. The reconcile of waiting picks reads the mode and sets the
    mark under the session lock, which set_mode also takes when it writes the mode, and sets the mark only while the
    mode is bypassPermissions."""

    def test_the_mark_is_raised_only_while_the_mode_is_bypass(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertEqual((s.mode, s._mode_pending), ("bypassPermissions", "bypassPermissions"),
                         "the premise: the mode is bypassPermissions and the pending-bypass mark is set")
        real = self.be._update_reg
        applied = []

        def record(sid, **fields):
            if not applied and "relaunchWait" in fields:   # the first record write with relaunchWait sets the mode to default
                applied.append(1)
                self.assertTrue(self.be.set_mode(SID, "default"))
            return real(sid, **fields)

        self.be._update_reg = record
        try:
            # reconcile against launch values whose mode is default
            s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)
        finally:
            self.be._update_reg = real
        self.assertTrue(applied, "the record write called set_mode")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("default", "default", ""),
                         "the pending-bypass mark matches the session's mode: default, with the mark clear")


class TheWaitingModePickFollowsTheMode(_ABypassPickChangedBack):
    """After its registry write, the reconcile of waiting picks decides the mode pick again against the session's mode
    as read under the session lock, and sets the pending-bypass mark and the relaunch ask in the same hold: the pick
    keeps waiting only while the launch lacks that mode. When that mode is not the one the reconcile started from, the
    mode entry its record write carried is taken back out of the registry's waiting record, with the mode read again
    under the lock after each record write, until the record holds the entry exactly while the mode reads
    bypassPermissions."""

    def _reconcile(self, s, at_record="default", launched="default", at_drop=None):
        """Reconcile against launch values whose mode is `launched` (None: no launch record), with set_mode(at_record)
        called from the reconcile's first record write that carries relaunchWait and, when `at_drop` is given,
        set_mode(at_drop) called from the reconcile's own drop of the mode entry before that drop runs. Returns the
        set_mode calls made, in order."""
        calls = []
        real_update, real_drop = self.be._update_reg, self.be._drop_pick_wait

        def record(sid, **fields):
            if not calls and "relaunchWait" in fields:
                calls.append(at_record)
                self.assertTrue(self.be.set_mode(SID, at_record))
            return real_update(sid, **fields)

        def drop(sess, pick, **fields):
            if (at_drop and at_drop not in calls and pick == "mode"
                    and sys._getframe(1).f_code.co_name == "_reconcile_waiting_picks"):
                calls.append(at_drop)
                self.assertTrue(self.be.set_mode(SID, at_drop))
            return real_drop(sess, pick, **fields)

        self.be._update_reg, self.be._drop_pick_wait = record, drop
        try:
            s._reconcile_waiting_picks(None if launched is None else dict(s.launch_values(), mode=launched), False)
        finally:
            self.be._update_reg, self.be._drop_pick_wait = real_update, real_drop
        return calls

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def test_a_default_mode_leaves_no_mode_entry_and_no_ask(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertTrue(self._mode_entry(), "the premise: the waiting record holds the mode pick")
        self.assertEqual(self._reconcile(s), ["default"], "the record write called set_mode")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, s._mode_ask_alone, self._mode_entry()),
                         ("default", "", "", False, False),
                         "the mode is default: no pending-bypass mark, no relaunch ask for the mode pick and no "
                         "mode entry in the waiting record")
        self.assertFalse(self._relaunched_at_quiet(s), "nothing asks for a relaunch at the next quiet moment")

    def test_a_bypass_mode_keeps_the_mode_entry(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertEqual(self._reconcile(s, at_drop="bypassPermissions"), ["default", "bypassPermissions"],
                         "the record write called set_mode(default) and the reconcile's drop called set_mode(bypass)")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()),
                         ("bypassPermissions", "bypassPermissions", "bypass mode", True),
                         "the mode is bypassPermissions: its pick waits, with the mark, the relaunch ask and the "
                         "mode entry in the waiting record")

    def test_a_mode_the_launch_lacks_keeps_its_relaunch(self):
        s = self._session(mode="bypassPermissions", relaunchWait={"why": "bypass mode", "t": 1700000000,
                                                                  "picks": {"mode": {"mode": "bypassPermissions"}}})
        self.assertEqual((s._mode_pending, s._switch_wanted), ("", ""),
                         "the premise: the bypass pick is on record, with no mark and no ask in memory")
        self.assertEqual(self._reconcile(s, at_record="plan", launched="acceptEdits"), ["plan"],
                         "the record write called set_mode(plan)")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("plan", "", "bypass mode"),
                         "the launch lacks plan: the pick asks for its relaunch, with no pending-bypass mark")
        self.assertTrue(self._relaunched_at_quiet(s), "the relaunch is armed at the next quiet moment")

    def test_a_launch_with_no_record_keeps_its_relaunch_and_drops_the_mode_entry(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertEqual(self._reconcile(s, launched=None), ["default"], "the record write called set_mode")
        self.assertEqual((s.mode, s._mode_pending, s._mode_ask_alone, self._mode_entry()),
                         ("default", "", False, False),
                         "the mode is default: no pending-bypass mark and no mode entry in the waiting record")
        self.assertTrue(s._switch_wanted, "a CLI no launch record vouches for still relaunches once")

    @staticmethod
    def _watch(s):
        """Swap the session's class for one that notes, for every read of `mode` and every write of the pending-bypass
        mark or the relaunch ask made by the reconcile itself, whether the session lock is held. Returns the notes."""
        seen = []

        def note(what, self_):
            if sys._getframe(2).f_code.co_name == "_reconcile_waiting_picks":
                seen.append((what, self_._lock.locked()))

        def field(name, store, what, on_read=False):
            def get(self_):
                if on_read:
                    note(what, self_)
                return self_.__dict__.get(store, "")

            def put(self_, v):
                if not on_read:
                    note(what, self_)
                self_.__dict__[store] = v
            s.__dict__[store] = s.__dict__.pop(name, "")
            return property(get, put)

        s.__class__ = type("Watched", (type(s),), {"mode": field("mode", "_w_mode", "mode read", on_read=True),
                                                  "_mode_pending": field("_mode_pending", "_w_mark", "mark set"),
                                                  "_switch_wanted": field("_switch_wanted", "_w_ask", "ask set")})
        return seen

    def test_the_mode_is_read_and_the_mark_and_ask_set_under_the_session_lock(self):
        s = self._session(mode="bypassPermissions", relaunchWait={"why": "bypass mode", "t": 1700000000,
                                                                  "picks": {"mode": {"mode": "bypassPermissions"}}})
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("bypassPermissions", "", ""),
                         "the premise: the bypass pick is on record, with no mark and no ask in memory")
        seen = self._watch(s)
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)
        self.assertEqual((s._mode_pending, s._switch_wanted), ("bypassPermissions", "bypass mode"), "the pick waits")
        self.assertEqual({w for w, _ in seen}, {"mode read", "mark set", "ask set"}, seen)
        self.assertTrue(all(held for _, held in seen), "each is made under the session lock: %r" % (seen,))

    def test_the_mode_entry_is_settled_on_reads_under_the_session_lock(self):
        s = self._session()
        self._bypass_waits(s)
        seen = self._watch(s)
        self.assertEqual(self._reconcile(s), ["default"], "the record write called set_mode")
        self.assertEqual([w for w, _ in seen], ["mode read", "mode read", "mode read"],
                         "one read decides the pick, then one read after the record write and one after the drop")
        self.assertTrue(all(held for _, held in seen), "each read is made under the session lock: %r" % (seen,))
        self.assertFalse(self._mode_entry())


class _AHoldEndSessionLock:
    """Stands in for a session's lock with the same semantics, plus call_free(fn): fn runs at once while the lock is
    free, and while it is held fn runs as soon as that hold ends, before anything after the hold."""

    def __init__(self, inner):
        self.inner, self.queued = inner, []

    def acquire(self, blocking=True, timeout=-1):
        return self.inner.acquire(blocking, timeout)

    def release(self):
        self.inner.release()
        while self.queued:
            self.queued.pop(0)()

    def locked(self):
        return self.inner.locked()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
        return False

    def call_free(self, fn):
        if self.inner.locked():
            self.queued.append(fn)
        else:
            fn()


def _hooked_field(s, name, on_read=None, on_write=None):
    """Swap the session's class for one whose `name` is a property: on_read(caller, value) runs after each read takes its
    value, and on_write(caller, value) before each write stores it; `caller` is the name of the function that made the
    access."""
    store = "_hooked_" + name

    def get(self_):
        v = self_.__dict__.get(store, "")
        if on_read:
            on_read(sys._getframe(1).f_code.co_name, v)
        return v

    def put(self_, v):
        if on_write:
            on_write(sys._getframe(1).f_code.co_name, v)
        self_.__dict__[store] = v

    s.__dict__[store] = s.__dict__.pop(name, "")
    s.__class__ = type("Hooked", (type(s),), {name: property(get, put)})


def _refuse_mode_switch(s, mode, prev):
    """Run the session's live mode switch to `mode` against a client that refuses it (`prev`: the mode confirmed before)."""
    import asyncio

    async def refuse(_mode):
        raise RuntimeError("synthetic control request failure")
    s.client = types.SimpleNamespace(set_permission_mode=refuse)
    try:
        asyncio.run(s._do_set_mode(mode, prev))
    finally:
        s.client = None


class TheModeAndThePendingBypassMarkChangeInOneHold(_ABypassPickChangedBack):
    """set_mode writes the mode, the confirmed mode and the pending-bypass mark in one hold of the session lock, so a
    set_mode that takes the lock when another set_mode's hold ends finds that hold's mode and mark together."""

    def test_set_mode_writes_the_mode_and_the_mark_in_one_hold(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the bypass pick waits for quiet
        s._lock = lock = _AHoldEndSessionLock(s._lock)
        called = []

        def on_write(caller, value):
            if not called and caller == "set_mode" and value == "bypassPermissions":
                called.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "mode", on_write=on_write)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(called, [1], "set_mode(default) was called at the bypass pick's mode write")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("default", "default", ""),
                         "the mode is default: the confirmed mode is default and the pending-bypass mark is clear")
        self.assertFalse(s.snapshot().get("modePending"), "the badge shows no waiting bypass pick")


class TheAppliedModePickFollowsTheMode(_ABypassPickChangedBack):
    """When the launch carried the mode pick, the reconcile of waiting picks applies it under the session lock, and only
    while the session's mode still reads the launch's mode: the confirmed mode takes the launch's mode and the
    pending-bypass mark comes down. A mode set_mode wrote since then keeps the confirmed mode and the mark set_mode left
    with it."""

    def _reconcile_carrying_the_flag(self, s, mode):
        """Reconcile against launch values that carry the bypass flag, with set_mode(mode) called at the reconcile's read
        of the pending-bypass mark after its record write: at once while the session lock is free there, else as soon as
        the hold ends. Returns whether set_mode was called."""
        s._lock = lock = _AHoldEndSessionLock(s._lock)
        launch = dict(s.launch_values())
        self.assertEqual(launch["mode"], "bypassPermissions", "the premise: the launch carries the bypass flag")
        recorded, called = [], []
        real_update, real_dropping = self.be._update_reg, self.be._update_reg_dropping

        def update(sid, **fields):
            recorded.append(1)
            return real_update(sid, **fields)

        def dropping(sid, drop=(), **fields):
            recorded.append(1)
            return real_dropping(sid, drop=drop, **fields)

        def on_read(caller, value):
            if recorded and not called and caller == "_reconcile_waiting_picks":
                called.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, mode)))
        _hooked_field(s, "_mode_pending", on_read=on_read)
        self.be._update_reg, self.be._update_reg_dropping = update, dropping
        try:
            s._reconcile_waiting_picks(launch, True)
        finally:
            self.be._update_reg, self.be._update_reg_dropping = real_update, real_dropping
        self.assertTrue(recorded, "the premise: the reconcile wrote its record")
        return bool(called)

    def test_the_confirmed_mode_takes_the_launch_mode_only_while_the_mode_reads_it(self):
        s = self._session()
        self._bypass_waits(s)
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("bypassPermissions", "default", "bypassPermissions"),
                         "the premise: the bypass pick waits")
        self.assertTrue(self._reconcile_carrying_the_flag(s, "default"), "set_mode(default) was called")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("default", "default", ""),
                         "the mode is default: the confirmed mode is default and the pending-bypass mark is clear")

    def test_a_launch_that_carried_the_pick_confirms_it(self):
        s = self._session()
        self._bypass_waits(s)
        s._reconcile_waiting_picks(dict(s.launch_values()), True)
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("bypassPermissions", "bypassPermissions", ""),
                         "the launch carried the flag: the confirmed mode is bypassPermissions and the mark is clear")

    def test_a_bypass_pick_the_launch_lacks_keeps_its_mark(self):
        s = self._session(relaunchWait={"why": "bypass mode", "t": 1700000000,
                                        "picks": {"mode": {"mode": "bypassPermissions"}}})
        self.assertEqual((s.mode, s._mode_pending), ("default", ""),
                         "the premise: the mode pick is on record, the mode default and no mark up")
        s.inflight = 1                                                # a turn runs: a bypass pick waits for quiet
        real = self.be._update_reg_dropping
        called = []

        def dropping(sid, drop=(), **fields):
            out = real(sid, drop=drop, **fields)
            if not called:
                called.append(1)
                self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
            return out
        self.be._update_reg_dropping = dropping
        try:
            s._reconcile_waiting_picks(dict(s.launch_values()), True)    # the launch carries the default mode
        finally:
            self.be._update_reg_dropping = real
        self.assertEqual(called, [1], "set_mode(bypassPermissions) was called after the record write")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted),
                         ("bypassPermissions", "default", "bypassPermissions", "bypass mode"),
                         "the bypass pick waits: the confirmed mode is default, with the mark and the relaunch ask")
        self.assertIn("mode", (sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {},
                      "the waiting record holds the mode pick")


class ARefusedModeSwitchRevertsOnlyItsOwnPick(_ABypassPickChangedBack):
    """A mode switch the CLI refuses puts the mode, the confirmed mode and the registry's mode back to the mode confirmed
    before it only while the session's mode still reads the refused pick. A mode picked after it keeps the session's
    mode, its pending-bypass mark and its registry mode, and the confirmed mode goes back only while it still reads the
    refused pick. The mode is read and the revert written under the session lock."""

    def test_a_bypass_pick_keeps_its_mode_mark_and_record(self):
        s = self._session()
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))       # held: no client
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("bypassPermissions", "acceptEdits", "bypassPermissions"),
                         "the premise: the bypass pick waits beside the acceptEdits pick")
        _refuse_mode_switch(s, "acceptEdits", "default")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("bypassPermissions", "default", "bypassPermissions", "bypassPermissions"),
                         "the bypass pick keeps the mode, its mark and the registry's mode; the confirmed mode is default")

    def test_another_pick_keeps_the_mode_and_its_confirmed_mode(self):
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))       # held: no client
        self.assertTrue(self.be.set_mode(SID, "plan"))
        _refuse_mode_switch(s, "acceptEdits", "bypassPermissions")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("plan", "plan", "", "plan"),
                         "the plan pick keeps the mode, the confirmed mode and the registry's mode")

    def test_the_refused_pick_reverts_every_layer(self):
        s = self._session()
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))       # held: no client
        _refuse_mode_switch(s, "acceptEdits", "default")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "default", "", "default"))


class TheModeFieldsAreReadAndWrittenUnderTheSessionLock(_ABypassPickChangedBack):
    """set_mode, the reconcile of waiting picks and a refused switch's revert read and write the mode, the confirmed mode
    and the pending-bypass mark under the session lock: every read and write of them in set_mode, in its unwait and in
    the revert, and in the reconcile every read of the mode, every write of the mark and the applied pick's write of the
    confirmed mode."""

    WATCHED = ("set_mode", "_withdraw_mode_pick", "_do_set_mode", "_reconcile_waiting_picks")

    def _watch(self, s):
        """Swap the session's class for one that notes each read and write of `mode`, `perm_mode` and `_mode_pending` made
        by a function in WATCHED, with whether the session lock is held. Returns the notes: (function, field, "r" or
        "w", held)."""
        seen = []

        def note(self_, field, op):
            caller = sys._getframe(2).f_code.co_name
            if caller in self.WATCHED:
                seen.append((caller, field, op, self_._lock.locked()))

        def prop(field):
            store = "_watched_" + field

            def get(self_):
                note(self_, field, "r")
                return self_.__dict__.get(store, "")

            def put(self_, v):
                note(self_, field, "w")
                self_.__dict__[store] = v
            s.__dict__[store] = s.__dict__.pop(field, "")
            return property(get, put)

        s.__class__ = type("Watched", (type(s),), {f: prop(f) for f in ("mode", "perm_mode", "_mode_pending")})
        return seen

    def test_each_read_and_write_holds_the_session_lock(self):
        s = self._session()
        seen = self._watch(s)
        self._bypass_waits(s)                                         # set_mode: the bypass pick waits
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)   # the launch lacks it: still waits
        self.assertTrue(self.be.set_mode(SID, "default"))             # set_mode: the mark comes down
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        s._reconcile_waiting_picks(dict(s.launch_values()), True)    # the launch carried it: applied
        self.assertEqual((s.perm_mode, s._mode_pending), ("bypassPermissions", ""), "the premise: the pick applied")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))         # held: no client
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        _refuse_mode_switch(s, "acceptEdits", "bypassPermissions")   # refused, with another pick standing
        self.assertTrue(self.be.set_mode(SID, "plan"))                # held: no client
        _refuse_mode_switch(s, "plan", "default")                    # refused, the session's mode: reverted
        self.assertEqual(s.mode, "default", "the premise: the refused pick reverted")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        real = self.be._update_reg
        called = []

        def record(sid, **fields):
            if not called and "relaunchWait" in fields:
                called.append(1)
                self.assertTrue(self.be.set_mode(SID, "default"))
            return real(sid, **fields)
        self.be._update_reg = record
        try:
            s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)   # the record's entry settled
        finally:
            self.be._update_reg = real
        self.assertEqual(called, [1], "the premise: the record write called set_mode")
        required = [x for x in seen if x[0] in ("set_mode", "_withdraw_mode_pick", "_do_set_mode")
                    or (x[0] == "_reconcile_waiting_picks"
                        and (x[1], x[2]) in (("mode", "r"), ("_mode_pending", "w"), ("perm_mode", "w")))]
        kinds = {x[:3] for x in required}
        self.assertTrue({("set_mode", "mode", "w"), ("set_mode", "perm_mode", "r"), ("set_mode", "perm_mode", "w"),
                         ("set_mode", "_mode_pending", "r"), ("set_mode", "_mode_pending", "w"),
                         ("_reconcile_waiting_picks", "mode", "r"), ("_reconcile_waiting_picks", "perm_mode", "w"),
                         ("_reconcile_waiting_picks", "_mode_pending", "w"),
                         ("_do_set_mode", "mode", "r"), ("_do_set_mode", "mode", "w"),
                         ("_do_set_mode", "perm_mode", "r"), ("_do_set_mode", "perm_mode", "w")} <= kinds,
                        "the premise: every kind of access was made: %r" % (sorted(kinds),))
        mode_reads = [x for x in required if x[:3] == ("_reconcile_waiting_picks", "mode", "r")]
        self.assertEqual(len(mode_reads), 5,
                         "the premise: the reconcile read the mode for the waiting pick, for the applied pick, for the "
                         "pick decided again and for the record's entry twice: %r" % (mode_reads,))
        self.assertEqual([x for x in required if not x[3]], [], "each is made under the session lock")


class AReasonSetAsideClearsAtALaunchNotAtAnAttach(psr._Harness):
    """The reasons set aside for a relaunch clear when a CLI is launched from the session's state, never at an attach to the
    CLI already running, so a change back after a host re-exec still keeps the relaunch."""

    def _bypass_waits_with_a_retry_set_aside(self, be, s, c):
        self._a_turn_that_leaves_a_monitor(s, c)
        self.assertTrue(be.set_mode(SID, "bypassPermissions"))
        self._settle()
        with open(os.path.join(self.state, sb.RETRY_UPGRADE_STORE), "w") as f:
            f.write("on\n")
        s._upgrade_retry = {"from": "Opus 5", "to": "Sonnet 5", "pick": "", "since": 0, "next": 0, "attempts": 0}
        be.retry_model_upgrades(time.time())                          # an ask stands: the retry stands down to it
        self.assertEqual((s._switch_wanted, s._upgrade_retry["attempts"]), ("bypass mode", 0), "the premise")

    def test_a_launch_clears_them(self):
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        c1 = self._connect(s, 1)
        self._bypass_waits_with_a_retry_set_aside(be, s, c1)
        self._monitor_ends(c1)
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client, "the relaunch onto CLI B")
        self._wait(lambda: not s.snapshot().get("modePending"), "the pick applied at CLI B's launch")
        self.assertEqual(s._switch_riders, set(), "CLI B was launched from the session's state, the retry's pick included")

    def test_an_attach_keeps_them_so_a_change_back_keeps_the_relaunch(self):
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": True, "cli": CLI_A, "launch": "same"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"}])
        c1 = self._connect(s, 1)
        self._bypass_waits_with_a_retry_set_aside(be, s, c1)
        s.loop.call_soon_threadsafe(be._on_host_reexec_now, s)
        self._wait(lambda: s._reconnect, "the re-exec's planned reconnect")
        self._push(c1, psr._EOF)                                      # the host closes the socket for its exec
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client, "re-attached to CLI A")
        self._settle()
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the pick still waits for its relaunch")
        s.set_mode_live = lambda mode, prev="default": None
        self.assertTrue(be.set_mode(SID, "acceptEdits"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch the retry stood down to stands")
        self._monitor_ends(psr._Client.instances[1])
        self._wait(lambda: len(psr._Client.instances) == 3 and psr._Client.instances[2] is s.client, "the relaunch onto CLI B")


CLI_C = {"pid": 4444, "start": "300.5", "spawnedAt": 1700000300}   # room for a relaunch nothing should ask for


class _ARestartEndsTheCliWithSigterm(psr._Harness):
    """Restart's road since it relaunches at once (SdkSession.relaunch_now), under the scripted host: the host's signal
    frame is recorded, the CLI it ends is held in its exit until the test lets it go (the teardown waits for it), the
    release records the host's exit frame, the proof the loop top reads that the CLI is gone, and the interrupt control
    request, which a Restart never sends, is recorded. The plan has a third CLI, so a relaunch nothing should ask for is
    counted rather than failing on a plan with no step left."""

    def _restartable(self):
        """The kernel and its session with the stand-ins in place. Sets self.signals, self.held, self.gone (an Event: set
        it to let the held CLI exit) and self.interrupts."""
        import asyncio
        from unittest import mock
        be, s = self._kernel([{"attach": False, "cli": CLI_A, "launch": "spec"},
                              {"attach": False, "cli": CLI_B, "launch": "spec"},
                              {"attach": False, "cli": CLI_C, "launch": "spec"}])
        self.signals, self.held, self.gone, self.interrupts = [], [], threading.Event(), []
        self.addCleanup(self.gone.set)
        termed = []
        transport_for = be._host_transport_for

        async def _with_signal(sess, opts, classes):
            got = await transport_for(sess, opts, classes)
            host = sess._host

            async def _signal(sig):                                   # the host's signal frame: its CLI is sent `sig`
                self.signals.append(sig)
                termed.append(host)
            host.signal = _signal
            return got
        be._host_transport_for = _with_signal
        real_exit, real_interrupt = psr._Client.__aexit__, psr._Client.interrupt

        async def _exit(client, *a):
            if termed and client is psr._Client.instances[0]:
                self.held.append(client)                              # the teardown waits for the SIGTERM'd CLI to exit
                while not (self.gone.is_set() or s.ended):
                    await asyncio.sleep(0.01)
                termed[0].exit_info = {"code": None, "signal": 15, "cause": "end"}   # its exit frame: proven gone
            return await real_exit(client, *a)

        async def _interrupt(client):
            self.interrupts.append(client)
            return await real_interrupt(client)
        for name, stand_in in (("__aexit__", _exit), ("interrupt", _interrupt)):
            patch = mock.patch.object(psr._Client, name, stand_in)
            patch.start()
            self.addCleanup(patch.stop)
        return be, s

    def _a_turn_left_running(self, s, c1, served=None):
        """CLI A's first turn, left running, the CLI launched on Opus and reporting it; when `served` names a model, the
        turn's reply is served on it. A monitor starts, so every relaunch ask waits."""
        s.enqueue("first turn")
        self._wait(lambda: c1.writes == ["first turn"], "the turn fed")
        self._push(c1, psr._SystemMessage("init", {"model": "claude-opus-5", "permissionMode": "acceptEdits",
                                                   "session_id": psr.FSID}, uuid=self._uid()))
        if served:
            self._push(c1, psr._AssistantMessage([psr._TextBlock("on it")], model=served, uuid=self._uid()))
        self._push(c1, psr._SystemMessage("task_started", {"task_id": "t-mon", "description": "watch the spend",
                                                           "task_type": "local_bash"}, uuid=self._uid()))
        self._wait(lambda: s.live_work()[1] == 1, "the monitor registered")

    def _a_retry_after_a_fallback(self, s, c1):
        """Retry upgrades after downgrades on, the session's pick Opus, and CLI A's reply served by Sonnet, a fallback
        nobody asked for, which arms the retry (_learn_model)."""
        self.assertEqual(s.chosen_model, "opus", "the premise: the session's pick")
        self._a_turn_left_running(s, c1, served="claude-sonnet-5")
        self.assertEqual(s._served_model, "Sonnet 5", "the premise: the session is served a model below its pick")

    @staticmethod
    def _retry(s):
        arm = s._upgrade_retry
        return (arm["from"], arm["to"], arm["attempts"]) if arm else None


class ARestartBesideAWaitingBypassPickRuns(_ARestartEndsTheCliWithSigterm):
    """A Restart relaunches at once (SdkSession.relaunch_now): the CLI is ended with SIGTERM, never sent the interrupt, and
    the relaunch takes the ask standing into itself. It carries no switch reason, so the waker's last look never cuts it,
    and a change back while it waits for the old CLI to be gone never withdraws it. A due model retry that stood down to
    the bypass pick's ask is set aside for that ask and rides the relaunch: the fresh CLI comes up with the mode picked by
    then and on the pick the retry asks for, and nothing is left set aside."""

    def test_a_change_back_leaves_the_restarts_relaunch_to_run_with_the_due_retrys_pick(self):
        with open(os.path.join(self.state, sb.RETRY_UPGRADE_STORE), "w") as f:
            f.write("on\n")
        sb.write_reg(self.state, SID, dict(self._reg(), model="opus"))
        be, s = self._restartable()
        c1 = self._connect(s, 1)
        self._a_retry_after_a_fallback(s, c1)
        self.assertTrue(be.set_mode(SID, "bypassPermissions"))
        self._settle()
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S)     # the kernel's tick, the retry's attempt due
        self.assertEqual(be.relaunch(SID), "")
        self._wait(lambda: self.signals == ["TERM"] and self.held, "the Restart ending CLI A with SIGTERM while its turn runs")
        self.assertEqual((s._reconnect, s._switch_wanted, s._switch_riders, self._retry(s)),
                         (True, "", {"retry upgrade"}, ("Opus 5", "Sonnet 5", 0)),
                         "the relaunch is armed and takes the pick's ask into itself, and the due retry, which made no "
                         "attempt of its own, is set aside for it")
        s.set_mode_live = lambda mode, prev="default": None
        self.assertTrue(be.set_mode(SID, "acceptEdits"))             # changed back while the relaunch waits for CLI A to exit
        armed = s._reconnect
        self.assertTrue(be.set_mode(SID, "bypassPermissions"))       # and picked again
        self.gone.set()
        self.assertTrue(armed, "a change back during the SIGTERM wait leaves the Restart's relaunch armed")
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client,
                   "the Restart's relaunch onto CLI B")
        c2 = psr._Client.instances[1]
        self.assertEqual(getattr(c2.options, "model", None), "opus", "CLI B runs the pick the retry asks for")
        self.assertEqual((c2.options.permission_mode, self.launched[CLI_B["pid"]]["mode"]),
                         ("bypassPermissions", "bypassPermissions"), "CLI B is launched with the bypass flag")
        self.assertEqual(self.interrupts, [], "never the interrupt control request")
        self._wait(lambda: not s.snapshot().get("modePending"), "the pick applied at CLI B's launch")
        self.assertEqual((s._switch_riders, self._retry(s)), (set(), ("Opus 5", "Sonnet 5", 0)),
                         "CLI B was launched from the session's state, the retry's pick included: nothing is left set "
                         "aside, and the retry waits for a reply served on its pick")
        self._settle()
        self.assertEqual(len(psr._Client.instances), 2, "one relaunch: nothing asks for another")


class ARestartServesAnAskStandingBesideAWaitingBypassPick(_ARestartEndsTheCliWithSigterm):
    """The run behind EveryReasonSetAsideForARelaunchIsCounted's EXEMPT entry for SdkSession._do_relaunch_now: a Restart's
    relaunch clears the ask standing whatever its reason, not only a pick's, because the fresh CLI it launches serves
    that ask. An ask other than a pick (Always fast's opt-in, a due model retry) stands beside a waiting bypass pick, and
    Restart runs with a turn in flight: CLI A is ended with SIGTERM and CLI B carries the ask and the bypass flag. Once CLI
    B's turns end and the kernel's tick runs again, there is still exactly one relaunch, nothing waits or is set aside,
    and no interrupt was sent. A relaunch that left the ask standing would relaunch a second time for it."""

    def _restart_beside_a_waiting_bypass_pick(self, be, s, why):
        self.assertTrue(be.set_mode(SID, "bypassPermissions"))
        self._settle()
        self.assertEqual((s._switch_wanted, s._mode_pending, s._mode_ask_alone), (why, "bypassPermissions", False),
                         "the premise: the ask stands, and the bypass pick waits beside it")
        self.assertEqual(be.relaunch(SID), "")
        self._wait(lambda: self.signals == ["TERM"] and self.held, "the Restart ending CLI A with SIGTERM while its turn runs")
        self.assertTrue(s._reconnect, "the Restart's relaunch is armed")
        self.assertEqual(len(psr._Client.instances), 1, "the relaunch waits for CLI A to exit")
        self.gone.set()
        self._wait(lambda: len(psr._Client.instances) == 2 and psr._Client.instances[1] is s.client,
                   "the Restart's relaunch onto CLI B")
        c2 = psr._Client.instances[1]
        self._wait(lambda: not s.snapshot().get("modePending"), "the pick applied at CLI B's launch")
        self._wait(lambda: c2.writes, "the cut monitor's notice fed to CLI B")
        for _ in range(3):                                            # CLI B's turns, each ended
            if s.inflight or s._cli_working:
                self._push(c2, psr._SystemMessage("init", {"model": "claude-opus-5", "permissionMode": "bypassPermissions",
                                                           "session_id": psr.FSID}, uuid=self._uid()))
                self._push(c2, psr._result(uuid=self._uid(), subtype="success", is_error=False, num_turns=1,
                                           session_id=psr.FSID, duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                                           usage={"input_tokens": 1, "output_tokens": 1}, result="ok",
                                           parent_tool_use_id=None))
                self._wait(lambda: s.inflight == 0 and not s._cli_working, "CLI B's turn ended")
            self._settle()
        be.retry_model_upgrades(time.time())                          # the kernel's tick carries any ask still standing
        self._settle(1.0)
        self.assertEqual(len(psr._Client.instances), 2, "one relaunch: CLI B served the ask, nothing asks for another")
        self.assertEqual((c2.options.permission_mode, self.launched[CLI_B["pid"]]["mode"]),
                         ("bypassPermissions", "bypassPermissions"), "CLI B is launched with the bypass flag")
        reg = self._reg()
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._switch_riders, reg.get("relaunchWait")),
                         ("", "", set(), None), "nothing waits for a relaunch or is set aside for one")
        self.assertEqual(self.interrupts, [], "never the interrupt control request")
        return c2

    def test_always_fast_asking_for_the_opt_in(self):
        sb.write_reg(self.state, SID, dict(self._reg(), model="opus"))
        be, s = self._restartable()
        c1 = self._connect(s, 1)
        self._a_turn_left_running(s, c1)
        with open(os.path.join(self.state, sb.ALWAYS_FAST_STORE), "w") as f:
            f.write("on\n")
        self.assertEqual(be.apply_model_switches(), 1, "the premise: Always fast asks for the opt-in CLI A lacks")
        self._restart_beside_a_waiting_bypass_pick(be, s, "always fast")
        self.assertEqual((self.launched[CLI_A["pid"]].get("fast"), self.launched[CLI_B["pid"]].get("fast")), (False, True),
                         "CLI B carries the opt-in Always fast asked for")

    def test_a_due_model_retry(self):
        with open(os.path.join(self.state, sb.RETRY_UPGRADE_STORE), "w") as f:
            f.write("on\n")
        sb.write_reg(self.state, SID, dict(self._reg(), model="opus"))
        be, s = self._restartable()
        c1 = self._connect(s, 1)
        self._a_retry_after_a_fallback(s, c1)
        self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S), 1, "the premise: the due retry asks")
        c2 = self._restart_beside_a_waiting_bypass_pick(be, s, "retry upgrade")
        self.assertEqual((getattr(c2.options, "model", None), self._retry(s)), ("opus", ("Opus 5", "Sonnet 5", 1)),
                         "CLI B runs the pick the retry asks for, on the retry's one attempt")


class EveryReasonSetAsideForARelaunchIsCounted(unittest.TestCase):
    """Every function in kernel/ that writes the standing relaunch ask (_switch_wanted), or asks for a relaunch
    (want_switch_reconnect) after reading whether one stands or is on its way, is classified below, and each place that sets
    a reason other than a pick aside for that ask adds it to _switch_riders, which keeps the relaunch on a change back
    (SdkBackend._withdraw_mode_pick). A new such function, or a _switch_riders.add or update call in kernel/ that is not
    counted here, fails until it is classified."""

    RIDERS = {   # one reason per _switch_riders.add or update call, in each function
        "SdkBackend.retry_model_upgrades": ("a due retry stands down to the ask standing",),
        "SdkSession._after_model_change": ("Always fast's ask after a model change stands down to the ask standing or armed",),
        "SdkSession._ask_missing_opt_in": ("the opt-in's ask after the CLI's report stands down to the ask standing or armed",),
        "SdkSession._amain": ("the fast-mode ask after a toggle during the version read stands down to the ask standing or armed",),
        "SdkBackend.apply_model_switches": ("Always fast's ask after a flip stands down to the ask standing or armed",),
        "SdkSession._try_switch_reconnect": ("the ask standing stands down to a relaunch already armed",
                                             "the carry puts the upgrade retry's attempt off for the relaunch it carries"),
        "SdkSession._do_request_reconnect": ("the arm clears the ask standing, and a veto raises only the carried one again",),
        "SdkSession._switch_teardown_check": ("the veto replaces the ask standing with the armed relaunch's reason",),
        "SdkSession._re_raise_switch_ask": ("the busy re-check drops the carried reason when another ask stands",),
    }
    EXEMPT = {
        "SdkSession._on_message": ("a rewind's relaunch at the turn's end clears the ask standing without setting it aside, "
                                   "and the fresh CLI it launches serves that ask"),
        "SdkSession._do_relaunch_now": ("a Restart's or an Apply now's relaunch at once by SIGTERM clears the ask standing "
                                        "without setting it aside, and the fresh CLI it launches serves that ask, whichever "
                                        "of the two came first (a Restart's is run in "
                                        "ARestartServesAnAskStandingBesideAWaitingBypassPick)"),
    }
    OTHERS = {
        "SdkSession.__init__": "the ask starts empty",
        "SdkSession.want_switch_reconnect": "raises a reason, and a pick's never hides another",
        "SdkSession._reconcile_waiting_picks": "raises a pick's reason, and only when no ask stands",
        "SdkSession._drop_carried_ask": "drops an ask that picks alone made",
        "SdkBackend._withdraw_mode_pick": "drops the bypass pick's own ask, and reads the riders",
    }
    STATE = ("_switch_wanted", "_switch_ask_pending", "_reconnect", "_reconnect_when_idle")

    @staticmethod
    def _is_rider_add(n):
        return (isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("add", "update")
                and getattr(n.func.value, "attr", "") == "_switch_riders")

    def _scan(self):
        """Each function and method of kernel/ that touches the ask: whether it writes or stands down to it, and how many
        riders it adds; and every rider added in those files, inside a function or not."""
        writers, adds, total = set(), {}, 0
        for path in sorted(Path(os.path.dirname(HERE), "kernel").glob("*.py")):
            text = path.read_text()
            if not any(w in text for w in ("_switch_wanted", "want_switch_reconnect", "_switch_riders")):
                continue
            tree = ast.parse(text)
            total += sum(1 for n in ast.walk(tree) if self._is_rider_add(n))
            for top in tree.body:
                defs = (ast.FunctionDef, ast.AsyncFunctionDef)
                owned = ([(top.name + ".", f) for f in top.body if isinstance(f, defs)] if isinstance(top, ast.ClassDef)
                         else [("", top)] if isinstance(top, defs) else [])
                for prefix, f in owned:
                    writes = reads = asks = False
                    for n in ast.walk(f):
                        if isinstance(n, ast.Attribute) and n.attr == "_switch_wanted" and isinstance(n.ctx, ast.Store):
                            writes = True
                        elif isinstance(n, ast.Attribute) and n.attr in self.STATE and isinstance(n.ctx, ast.Load):
                            reads = True
                        elif isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "want_switch_reconnect":
                            asks = True
                        elif isinstance(n, ast.Call) and getattr(n.func, "id", "") == "getattr" and len(n.args) > 1 \
                                and getattr(n.args[1], "value", None) in self.STATE:
                            reads = True
                    if writes or (asks and reads):
                        writers.add(prefix + f.name)
                    k = sum(1 for n in ast.walk(f) if self._is_rider_add(n))
                    if k:
                        adds[prefix + f.name] = adds.get(prefix + f.name, 0) + k
        return writers, adds, total

    def test_each_function_is_classified_and_each_rider_add_is_counted(self):
        writers, adds, total = self._scan()
        self.assertEqual(sorted(writers), sorted(dict(self.RIDERS, **self.EXEMPT, **self.OTHERS)),
                         "a function writes the relaunch ask, or stands down to it, unclassified")
        self.assertEqual(adds, {name: len(whys) for name, whys in self.RIDERS.items()},
                         "a function adds a rider in a place not counted here, or a place counted here adds none")
        self.assertEqual(total, sum(adds.values()), "a rider is added outside a function")


if __name__ == "__main__":
    unittest.main()
