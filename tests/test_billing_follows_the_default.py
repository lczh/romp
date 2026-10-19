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
import re
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
    keeps waiting only while the launch lacks that mode. When that mode is not the one the reconcile read with the launch
    values, the mode entry its record write carried is taken back out of the registry's waiting record, with the mode
    read again under the lock after each record write, until the record holds the entry exactly while the mode reads
    bypassPermissions. A test here that picks a mode during the reconcile picks it after the reconcile has read the
    launch values."""

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
    """set_mode writes the mode, the confirmed mode and the pending-bypass mark in one hold of the session lock."""

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
    before it only while the session's mode still reads the refused pick. A mode picked after it by a set_mode that has
    returned when the refusal is handled keeps the session's mode, its pending-bypass mark and its registry mode, and the
    confirmed mode goes back only while it still reads the refused pick. The registry's mode goes back only while the
    record still holds the refused pick, read and written in one hold of the registry lock (SdkBackend._revert_reg_mode):
    three tests make a default pick as the revert's record write takes the registry lock, with the refusal handled as the
    default pick's hold of the session lock is entered, and at the revert's read of the record; the record keeps the
    default mode. The record is written back only while the session's mode read in the revert's hold is the refused
    pick: a fourth test picks default and then the refused mode again, with the refusal handled as that second pick's hold
    of the session lock is entered, and the record keeps the refused mode, which is the session's mode. That the revert
    reads the mode and writes in one hold of the session lock is pinned by TheModeFieldsAreReadAndWrittenUnderTheSessionLock
    and EachModeHoldReadsAndWritesInOneHold, not here."""

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

    def test_a_default_pick_as_the_reverts_record_write_takes_the_registry_lock_keeps_its_record(self):
        """The CLI was launched with the flag and a plan pick is refused. Default is picked as the revert's record write
        takes the registry lock: the record holds the default mode set_mode wrote, not the refused pick, and keeps it."""
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "plan"))              # held: no client

        def pick_default():
            client, s.client = s.client, None                         # its live switch is held, not sent to the refusing client
            try:
                self.assertTrue(self.be.set_mode(SID, "default"))
            finally:
                s.client = client
        self.be._reg_lock = lock = _StepWhenTheLockIsTaken(self.be._reg_lock, "_do_set_mode", pick_default, depth=2)
        _refuse_mode_switch(s, "plan", "bypassPermissions")
        self.assertTrue(lock.done, "the premise: default was picked as the revert's record write took the registry lock")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "default", "", "default"), "the record's mode is the session's mode")

    def test_a_refusal_handled_at_a_default_picks_hold_keeps_the_record_that_pick_wrote(self):
        """The CLI was launched with the flag and a plan pick is refused. Default is picked, and the refusal is handled as
        that pick's hold of the session lock is entered: the revert puts the mode back in its own hold, and its record
        write finds the default mode recorded, not the refused pick, and leaves it."""
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "plan"))              # held: no client
        s._lock = lock = _StepWhenTheLockIsTaken(s._lock, "set_mode",
                                                 lambda: _refuse_mode_switch(s, "plan", "bypassPermissions"))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(lock.done, "the premise: the refusal was handled as the default pick's hold was entered")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "default", "", "default"), "the record's mode is the session's mode")

    def test_the_refused_mode_picked_again_after_a_default_pick_keeps_its_record(self):
        """The CLI was launched with the flag and a plan pick is refused. Default is picked, then plan again, and the
        refusal of the first plan pick is handled as the second plan pick's hold of the session lock is entered: the
        revert's hold reads the default mode, so the revert writes no record, and the record keeps the plan mode the
        second plan pick wrote."""
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "plan"))              # held: no client
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._lock = lock = _StepWhenTheLockIsTaken(s._lock, "set_mode",
                                                 lambda: _refuse_mode_switch(s, "plan", "bypassPermissions"))
        self.assertTrue(self.be.set_mode(SID, "plan"))
        self.assertTrue(lock.done, "the premise: the refusal was handled as the second plan pick's hold was entered")
        self.assertEqual((s.mode, sb.read_reg(self.d, SID).get("mode")), ("plan", "plan"),
                         "the record's mode is the session's mode")

    def test_the_reverts_record_read_and_write_are_one_hold(self):
        """The revert reads the record and writes it in one hold of the registry lock (SdkBackend._revert_reg_mode): a
        default pick made at that read, through the registry lock's stand-in (_AHoldEndSessionLock), runs as soon as the
        hold ends, and the record keeps the default mode it writes."""
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "plan"))              # held: no client
        lock = self.be._reg_lock = _AHoldEndSessionLock(self.be._reg_lock)
        real, made = sb.read_reg, []

        def pick_default():
            client, s.client = s.client, None                         # its live switch is held, not sent to the refusing client
            try:
                self.assertTrue(self.be.set_mode(SID, "default"))
            finally:
                s.client = client

        def read(*a, **k):
            out = real(*a, **k)
            if not made and sys._getframe(1).f_code.co_name == "_revert_reg_mode":
                made.append(1)
                lock.call_free(pick_default)
            return out
        sb.read_reg = read
        try:
            _refuse_mode_switch(s, "plan", "bypassPermissions")
        finally:
            sb.read_reg = real
        self.assertEqual(made, [1], "the premise: default was picked at the revert's read of the record")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "default", "", "default"), "the record's mode is the session's mode")


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
                         ("_withdraw_mode_pick", "_mode_pending", "r"),
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


# ── the withdrawal and every path that sets the relaunch ask hold the session lock (SdkBackend._withdraw_mode_pick) ──

def _stepped_at_read(s, name, caller, step):
    """Swap the session's class for one whose `name` is a property that, at the first read made by `caller`, runs step()
    and then returns the value as it stands after it. Returns a list that holds 1 once step ran."""
    store = "_stepped_" + name
    ran = []

    def get(self_):
        if not ran and sys._getframe(1).f_code.co_name == caller:
            ran.append(1)
            step()
        return self_.__dict__.get(store, "")

    def put(self_, v):
        self_.__dict__[store] = v

    s.__dict__[store] = s.__dict__.pop(name, "")
    s.__class__ = type("Stepped", (type(s),), {name: property(get, put)})
    return ran


def _made_at_call(obj, name, caller, step):
    """Replace obj.`name` with a wrapper that, at its first call made from `caller`, runs the real one, then step(), and
    returns what the real one returned. Returns a list that holds 1 once step ran."""
    real = getattr(obj, name)
    ran = []

    def wrapper(*a, **k):
        out = real(*a, **k)
        if not ran and sys._getframe(1).f_code.co_name == caller:
            ran.append(1)
            step()
        return out
    setattr(obj, name, wrapper)
    return ran


def _made_before_call(obj, name, caller, step):
    """Replace obj.`name` with a wrapper that, at its first call made from `caller`, runs step() and then the real one.
    Returns a list that holds 1 once step ran."""
    real = getattr(obj, name)
    ran = []

    def wrapper(*a, **k):
        if not ran and sys._getframe(1).f_code.co_name == caller:
            ran.append(1)
            step()
        return real(*a, **k)
    setattr(obj, name, wrapper)
    return ran


class TheWithdrawalAndEveryPathThatSetsTheAskHoldTheSessionLock(_ABypassPickChangedBack):
    """The change back's withdrawal of the bypass pick's relaunch reads the pick's word, the standing relaunch ask, what is
    on its way and the reasons set aside, and clears the ask, in one hold of the session lock (SdkBackend.
    _withdraw_mode_pick), and every path that reads the ask and then sets it, or sets a reason aside, does so under that
    lock. Each test calls set_mode(default), or another path's step, at a read inside one of those paths; through the
    session lock's stand-in (_AHoldEndSessionLock) a step called inside a hold runs as soon as that hold ends."""

    def _default_picked_at_the_read(self, s, caller, nth=1):
        """set_mode(default) made at `caller`'s nth read of the standing relaunch ask, through the lock's stand-in.
        Returns a list that holds 1 once it was made."""
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == caller:
                reads.append(value)
                if len(reads) == nth and not made:
                    made.append(1)
                    lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        return made

    def _keeps_a_relaunch(self, s, made, fast=False):
        self.assertEqual((made, s.mode, s._mode_pending), ([1], "default", ""), "the premise: set_mode(default) was called")
        after = (s._switch_wanted, sorted(s._switch_riders))
        self.assertTrue(self._relaunched_at_quiet(s), "the reason set aside keeps a relaunch, armed at the first quiet "
                                                      "moment (after the change back: the ask %r, set aside %r)" % after)
        if fast:
            self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_always_fasts_ask_after_a_model_change_keeps_a_relaunch_with_the_opt_in(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        made = self._default_picked_at_the_read(s, "_after_model_change")
        s._after_model_change("Sonnet 5", "Opus 5")
        self._keeps_a_relaunch(s, made, fast=True)

    def test_the_opt_in_the_clis_report_asks_for_keeps_a_relaunch_with_the_opt_in(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        made = self._default_picked_at_the_read(s, "_ask_missing_opt_in")
        s._adopt_fast_state(dict(LACKS_OPT_IN))                       # this turn's init
        self._keeps_a_relaunch(s, made, fast=True)

    def test_always_fasts_ask_when_the_switch_flips_keeps_a_relaunch_with_the_opt_in(self):
        s = self._opt_in_lost()
        self._bypass_waits(s)
        made = self._default_picked_at_the_read(s, "apply_model_switches")
        self.be.apply_model_switches()
        self._keeps_a_relaunch(s, made, fast=True)

    def test_a_due_model_retry_keeps_a_relaunch(self):
        s = self._session()
        self._arm_retry(s)
        self._bypass_waits(s)
        made = self._default_picked_at_the_read(s, "retry_model_upgrades", nth=3)   # after the tick's look at the ask
        self.be.retry_model_upgrades(time.time())
        self.assertEqual(s._upgrade_retry["attempts"], 0, "the premise: the retry stood down to the ask")
        self._keeps_a_relaunch(s, made)

    def test_an_ask_raised_at_the_withdrawals_read_of_the_launch_stands(self):
        s = self._session()
        self._bypass_waits(s)
        made = _made_at_call(s, "launch_values", "_withdraw_mode_pick", lambda: s.want_switch_reconnect("always fast"))
        self._change_back(s)
        self.assertEqual(made, [1], "the premise: the ask was raised at the withdrawal's read of the launch")
        self.assertEqual(s._switch_wanted, "always fast", "the ask raised stands")

    def test_a_relaunch_cut_at_the_withdrawals_read_of_the_relaunch_on_its_way_keeps_its_ask_with_the_opt_in(self):
        q = []
        s = self._opt_in_lost(q)
        s.inflight = 0
        self.assertEqual(self.be.apply_model_switches(), 1, "the premise: Always fast asks")
        self._run(q)                                                  # its carry, then its arm
        self.assertEqual((s._reconnect, s._reconnect_switch_why), (True, "always fast"), "the premise: armed")
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))   # its ask's own step waits on the loop
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = _stepped_at_read(s, "_reconnect", "_withdraw_mode_pick", lambda: lock.call_free(s._switch_teardown_check))
        self._change_back(s)
        self.assertEqual(made, [1], "the premise: the waker's last look was made at the withdrawal's read")
        self.assertEqual(s._switch_wanted, "always fast", "the relaunch the waker's last look cut keeps its ask")
        self.assertTrue(self._relaunched_at_quiet(s, q))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_a_relaunch_cut_with_nothing_set_aside_is_withdrawn_at_its_read_of_the_ask(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        s.inflight = 1                                                # the CLI took a turn before the teardown
        made = self._default_picked_at_the_read(s, "_switch_teardown_check")
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look cut the relaunch")
        self.assertEqual((made, s.mode, s._mode_pending), ([1], "default", ""), "the premise: set_mode(default) was called")
        self.assertEqual(s._switch_wanted, "", "nothing was set aside and the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_the_bypass_picks_ask_and_its_mark_are_read_in_one_hold(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "want_switch_reconnect":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: set_mode(default) was called at want_switch_reconnect's read of the ask")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", ""),
                         "the mode is default, with no mark and no relaunch ask")

    def test_a_carry_carries_only_the_ask_its_hold_reads(self):
        s = self._session()
        self._bypass_waits(s)
        s.inflight, s._cli_working = 0, False
        made = _made_before_call(s, "quiet", "_try_switch_reconnect",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertFalse(s._try_switch_reconnect(), "nothing is carried")
        self.assertEqual(made, [1], "the premise: set_mode(default) was called at the carry's quiet read")
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._reconnect), ("", "", False),
                         "the withdrawn relaunch is neither carried nor armed")

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def test_bypass_picked_again_at_the_withdrawals_read_of_the_launch_keeps_its_ask_its_own(self):
        """The withdrawal is applied before a pick made after the change back: Bypass picked again at the withdrawal's read
        of the launch waits with its ask its own and its entry on record, as when the withdrawal had run first, so a second
        change back withdraws it."""
        s = self._session()
        self._bypass_waits(s)
        made = _made_at_call(s, "launch_values", "_withdraw_mode_pick",
                             lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked again at the withdrawal's read of the launch")
        self.assertEqual((s._mode_pending, s._switch_wanted, s._mode_ask_alone, self._mode_entry()),
                         ("bypassPermissions", "bypass mode", True, True),
                         "the pick made after the change back waits, its ask its own, its entry on record")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s._mode_pending, s._switch_wanted, s._mode_ask_alone, self._mode_entry()), ("", "", False, False),
                         "a second change back withdraws it")

    def test_the_tick_raises_the_bypass_picks_ask_only_while_its_mark_is_up(self):
        s = self._session()
        self._bypass_waits(s)
        made, reads = [], []

        def on_read(who, value):
            if who == "retry_model_upgrades":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the tick has read the ask it asks for again
                    made.append(1)
                    self.assertTrue(self.be.set_mode(SID, "default"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.be.retry_model_upgrades(time.time())
        self.assertEqual((made, reads[:2]), ([1], ["bypass mode", "bypass mode"]),
                         "the premise: set_mode(default) was called after the tick read the ask")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", ""),
                         "the mode is default, with no mark and no relaunch ask")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")

    def test_the_bypass_picks_ask_is_raised_only_while_its_mark_is_up(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        made = _made_at_call(self.be, "_note_pick_waits", "set_mode",
                             lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: set_mode(default) was called at the pick's record write")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", ""),
                         "the mode is default, with no mark and no relaunch ask")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")

    def test_a_new_ask_says_once_what_it_waits_for(self):
        s = self._session()
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        self._bypass_waits(s)
        made = _made_before_call(s, "quiet", "_try_switch_reconnect",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        start = len(lines)
        s._try_switch_reconnect()                                     # the next event that ends live work
        self.assertEqual(made, [1], "the premise: set_mode(default) was called at the step's quiet read")
        waiting = "waiting for the session to go quiet"
        self.assertEqual([m for m in lines[start:] if waiting in m], [], "nothing waits once the relaunch is withdrawn")
        start = len(lines)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))   # picked again while the turn runs
        self.assertEqual(len([m for m in lines[start:] if waiting in m]), 1, "the new ask says once what it waits for")


class ABypassPickMadeAtTheWithdrawalsUnwaitKeepsItsPlaceInLine(_Unit):
    """A bypass pick made at the withdrawal's step that takes the withdrawn relaunch out of line for a launch slot keeps a
    place in line, and the freed slot relaunches it (SdkBackend._withdraw_mode_pick)."""

    def test_the_freed_slot_relaunches_the_new_pick(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                             # every slot held elsewhere
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(slots.waiting(s), "the premise: the relaunch waits in line for a slot")
        made = _made_before_call(slots, "unwait", "_withdraw_mode_pick",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked again at the withdrawal's step out of line")
        self.assertEqual((s._mode_pending, s._switch_wanted), ("bypassPermissions", "bypass mode"),
                         "the pick made after the change back waits with its ask")
        self.assertTrue(slots.waiting(s), "in line for a slot")
        slots.release()
        self.assertEqual(relaunched, [SID], "the freed slot relaunches it")


class _ALoop:
    """Stands in for a session's loop: call_soon_threadsafe queues a callback, and run() runs the queue in order."""

    def __init__(self):
        self.queued = []

    def is_closed(self):
        return False

    def call_soon_threadsafe(self, fn, *a):
        self.queued.append((fn, a))

    def run(self):
        while self.queued:
            fn, a = self.queued.pop(0)
            fn(*a)


WITHDRAWAL = ("_withdraw_mode_pick",)   # the change back's withdrawal of the bypass pick's relaunch


class TheWithdrawalBesideOtherStepsOfTheSession(_ABypassPickChangedBack):
    """The change back's withdrawal beside a step of the session that sets a reason aside, a mode picked after it and a
    session whose loop has closed."""

    def test_a_reason_the_loop_sets_aside_keeps_the_relaunch_with_the_opt_in(self):
        """A step of the session's loop that sets a reason aside for the ask standing (Always fast's ask after a model
        change, SdkSession._after_model_change) is made at the withdrawal's read of the reasons set aside: the relaunch
        keeps that reason, and is armed at the first quiet moment with the opt-in."""
        loop = _ALoop()
        s = self._opt_in_lost()
        s.loop = loop
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        loop.run()
        self.assertEqual((s._switch_wanted, s._mode_ask_alone, set(s._switch_riders)), ("bypass mode", True, set()),
                         "the premise: the pick's own ask, nothing set aside")
        lock = s._lock = _AHoldEndSessionLock(s._lock)                # the step takes the session lock
        stepped = []

        class SetAsideAtTheRead(set):
            def __len__(self_):
                n = set.__len__(self_)
                if not stepped and sys._getframe(1).f_code.co_name in WITHDRAWAL:
                    stepped.append(1)
                    lock.call_free(lambda: s._after_model_change("Sonnet 5", "Opus 5"))
                return n
        s._switch_riders = SetAsideAtTheRead()
        self.assertTrue(self.be.set_mode(SID, "default"))
        loop.run()
        self.assertTrue(stepped, "the premise: the loop's step was made at the withdrawal's read of the reasons set aside")
        after = (s._switch_wanted, sorted(s._switch_riders))
        s.inflight, s._cli_working = 0, False
        s._try_switch_reconnect()
        loop.run()
        self.assertTrue(s._reconnect, "the reason set aside keeps a relaunch, armed at the first quiet moment (after the "
                                      "change back: the ask %r, set aside %r)" % after)
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def _bypass_waits_on(self, loop):
        s = self._session()
        s.loop = loop
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        loop.run()
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", True), "the premise: the pick's own ask")
        return s

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def test_a_mode_picked_after_the_change_back_is_switched_to_after_the_withdrawal(self):
        """The withdrawal is applied before a mode picked after the change back: it is done before the change back's live
        switch and the later pick's start, and it decides on the launch the change back made, not on the later mode."""
        loop = _ALoop()
        s = self._bypass_waits_on(loop)
        s.client = object()                                           # connected: a live switch is a step on the loop
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))         # before the loop runs
        live = []
        while loop.queued:                                            # the loop, in order; a live switch's own coroutine is
            fn, a = loop.queued.pop(0)                                # not run here, the ask is noted as it would start
            if "set_mode_live" in getattr(fn, "__qualname__", ""):
                live.append(s._switch_wanted)
            else:
                fn(*a)
        self.assertEqual(live, ["", ""], "the relaunch is withdrawn before either live switch starts")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("acceptEdits", "", ""),
                         "and stays withdrawn with the mode picked after the change back")

    def test_with_the_loop_closed_the_withdrawal_runs_at_once(self):
        """A session whose thread has ended and closed its loop withdraws the relaunch at once, as when the withdrawal ran
        where set_mode was called."""
        s = self._bypass_waits_on(_ALoop())

        def closed(fn, *a):
            raise AssertionError("nothing is scheduled on a closed loop")
        s.loop = types.SimpleNamespace(is_closed=lambda: True, call_soon_threadsafe=closed)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s._mode_pending, s._switch_wanted, self._mode_entry()), ("", "", False),
                         "the relaunch is withdrawn when set_mode returns")


# ── the withdrawal's faces: the mode compared, the drop of a carried ask, the waiting record and the last look ──

class TheWithdrawalComparesALaunchInTheModeSetModeWasCalledWith(_ABypassPickChangedBack):
    """The withdrawal of the bypass pick's relaunch compares the serving CLI's launch record with a launch in the mode
    set_mode was called with, the other keys as they read (SdkBackend._withdraw_mode_pick). A mode another set_mode
    writes does not decide it: a mode other than bypassPermissions is switched live, and a bypass pick raises the
    pending-bypass mark, which the withdrawal's hold reads."""

    def _picked_at_the_launch_read(self, s, before=None, after=None):
        """At the withdrawal's first read of the launch, before() runs, then the read, then after(). Returns a list that
        holds 1 once they ran."""
        real = s.launch_values
        made = []

        def read():
            if made or sys._getframe(1).f_code.co_name != "_withdraw_mode_pick":
                return real()
            made.append(1)
            if before:
                before()
            out = real()
            if after:
                after()
            return out
        s.launch_values = read
        return made

    def _withdrawn(self, s, mode):
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted), (mode, mode, "", ""),
                         "a launch in the mode set_mode(default) was called with changes nothing: the relaunch is withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")

    def test_another_mode_then_default_picked_at_the_launch_read(self):
        s = self._session()
        self._bypass_waits(s)
        calls = []
        real = self.be._withdraw_mode_pick
        self.be._withdraw_mode_pick = lambda *a: (calls.append(1), real(*a))[1]
        made = self._picked_at_the_launch_read(s, before=lambda: self.assertTrue(self.be.set_mode(SID, "acceptEdits")),
                                               after=lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((made, calls), ([1], [1]), "the premise: acceptEdits, then default, were picked at the withdrawal's "
                                                    "read of the launch, and neither found the mark up")
        self._withdrawn(s, "default")

    def test_bypass_then_default_picked_at_the_launch_read(self):
        """The second default pick finds the pending-bypass mark up and runs a withdrawal of its own once the first has
        returned."""
        s = self._session()
        self._bypass_waits(s)
        real = self.be._withdraw_mode_pick
        running, waiting = [], []

        def withdraw(*a):
            if running:
                waiting.append(a)
                return
            running.append(1)
            try:
                real(*a)
            finally:
                running.pop()
        self.be._withdraw_mode_pick = withdraw
        made = self._picked_at_the_launch_read(s, before=lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")),
                                               after=lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((made, len(waiting)), ([1], 1), "the premise: Bypass, then default, were picked at the "
                                                         "withdrawal's read of the launch; the second withdrawal waits")
        while waiting:
            real(*waiting.pop(0))
        self._withdrawn(s, "default")

    def test_another_mode_picked_at_the_launch_read(self):
        s = self._session()
        self._bypass_waits(s)
        made = self._picked_at_the_launch_read(s, before=lambda: self.assertTrue(self.be.set_mode(SID, "acceptEdits")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: acceptEdits was picked at the withdrawal's read of the launch")
        self._withdrawn(s, "acceptEdits")


class ADropOfAnAskTheCliCarriesLeavesItWhileAPickWaits(_ABypassPickChangedBack):
    """_drop_carried_ask drops the standing relaunch ask only while no pick's pending mark is up (the pending-bypass mark,
    and the effort, billing and fast-mode marks) in the hold where it reads the ask again, as the withdrawal's hold reads
    them (SdkSession._drop_carried_ask): a bypass pick or a billing pick, each of which raises its mark, made at the drop's
    read of the launch keeps its relaunch ask. The hold reads no value of a pick that raises no mark (the session env,
    the subagent speed), and these tests make no such pick."""

    def test_a_bypass_pick_at_the_drops_launch_read_keeps_its_ask(self):
        s = self._session(auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", "bypass mode"),
                         "the premise: the default pick kept the relaunch, the billing pick waiting beside it")
        made = _made_at_call(s, "launch_values", "_drop_carried_ask",
                             lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        self.assertTrue(self.be.set_auth(SID, "login"))               # the billing pick goes back to what the CLI runs
        self.assertEqual(made, [1], "the premise: Bypass was picked at the drop's read of the launch")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted),
                         ("bypassPermissions", "bypassPermissions", "bypass mode"), "the bypass pick waits with its relaunch ask")
        self.assertTrue(self._relaunched_at_quiet(s), "armed at the first quiet moment")

    def test_a_billing_pick_at_the_drops_launch_read_keeps_its_ask(self):
        s = self._session(auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        made = _made_at_call(s, "launch_values", "_drop_carried_ask", lambda: self.assertTrue(self.be.set_auth(SID, "key")))
        self.assertTrue(self.be.set_auth(SID, "login"))
        self.assertEqual(made, [1], "the premise: the billing pick was made at the drop's read of the launch")
        self.assertEqual((s.auth, s._auth_pending, s._switch_wanted), ("key", "key", "billing"),
                         "the billing pick waits with its relaunch ask")


class ADropOfAnAskTheCliCarriesLeavesItWhileAReasonIsSetAside(_ABypassPickChangedBack):
    """_drop_carried_ask leaves the standing relaunch ask while a reason other than a pick is set aside for it
    (_switch_riders), read in the hold where it reads the ask again, as the withdrawal's hold reads them
    (SdkSession._drop_carried_ask): a billing pick set to what the serving CLI runs keeps the relaunch for the reason set
    aside, armed at the first quiet moment."""

    def _billing_asks(self, s, q):
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        self.assertTrue(self.be.set_auth(SID, "key"))
        self._run(q)
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask stands")

    def _kept(self, s, q, reason):
        self.assertEqual((s._auth_pending, s._switch_wanted, sorted(s._switch_riders)), ("", "billing", [reason]),
                         "the billing pick is what the CLI runs, and its ask stands for the reason set aside")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")

    def test_a_retry_set_aside_for_a_billing_ask_keeps_the_relaunch(self):
        q = []
        s = self._session(queued=q, auth="login")
        self._arm_retry(s)
        self._billing_asks(s, q)
        self.be.retry_model_upgrades(time.time())
        self._run(q)
        self.assertEqual(sorted(s._switch_riders), ["retry upgrade"], "the premise: the retry is set aside for the ask")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self._kept(s, q, "retry upgrade")

    def test_always_fast_set_aside_for_a_billing_ask_keeps_the_relaunch_with_the_opt_in(self):
        """Always fast on, an Opus session whose CLI was launched with the opt-in and lost it to a literal '/fast off'
        since: the switch's reason is set aside for the billing pick's ask."""
        q = []
        Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
        s = self._session(queued=q, auth="login", model="opus")
        self.assertTrue(s._cli_launch["fast"], "the premise: the launch record carries the opt-in")
        s._fast_unlocked, s._fast_off_sent = False, True
        self._billing_asks(s, q)
        self.be.apply_model_switches()
        self._run(q)
        self.assertEqual(sorted(s._switch_riders), ["always fast"], "the premise: Always fast is set aside for the ask")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self._kept(s, q, "always fast")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_always_fast_turned_on_at_the_drops_launch_read_keeps_the_relaunch_with_the_opt_in(self):
        """A CLI launched without the opt-in: Always fast turns on and its pass runs at the drop's read of the launch, and
        the switch's reason is set aside for the ask."""
        q = []
        s = self._session(queued=q, auth="login", model="opus")
        self.assertFalse(s._cli_launch["fast"], "the premise: the CLI was launched without the opt-in")
        self._billing_asks(s, q)

        def turn_on():
            Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
            self.be.apply_model_switches()
        made = _made_at_call(s, "launch_values", "_drop_carried_ask", turn_on)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertEqual(made, [1], "the premise: Always fast turned on at the drop's read of the launch")
        self._kept(s, q, "always fast")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_always_fast_turned_on_as_the_drops_hold_is_entered_keeps_the_relaunch_with_the_opt_in(self):
        """A CLI launched without the opt-in: Always fast turns on and its pass runs as the drop's hold of the session lock
        is entered, and the switch's reason is set aside for the ask."""
        q = []
        s = self._session(queued=q, auth="login", model="opus")
        self.assertFalse(s._cli_launch["fast"], "the premise: the CLI was launched without the opt-in")
        self._billing_asks(s, q)

        def turn_on():
            Path(self.d, sb.ALWAYS_FAST_STORE).write_text("on\n")
            self.be.apply_model_switches()
        lock = s._lock = _StepWhenTheLockIsTaken(s._lock, "_drop_carried_ask", turn_on)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertTrue(lock.done, "the premise: Always fast turned on as the drop's hold was entered")
        self._kept(s, q, "always fast")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")


class TheReconcileOfWaitingPicksClearsNoStandingAsk(_ABypassPickChangedBack):
    """The reconcile of waiting picks (SdkSession._reconcile_waiting_picks) clears no standing relaunch ask: it sets one
    only when none stands. A session-env pick's ask stands with the env set back to what the CLI was launched with, so
    the launch carries every pick, and a second env pick is made at the read of the launch by the drop of a carried ask
    (SdkSession._drop_carried_ask) if the reconcile calls that drop, else as the reconcile returns: the second pick's ask
    stands, and its relaunch is armed at the first quiet moment."""

    def test_a_second_env_pick_made_as_the_reconcile_ends_keeps_its_relaunch(self):
        q = []
        s = self._session(queued=q)
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_FLAG": "1"}))
        self._run(q)
        self.assertTrue(self.be.set_env(SID, {}))                    # set back to what the CLI was launched with
        self._run(q)
        self.assertEqual(s._switch_wanted, "session env", "the premise: the env pick's ask stands")
        self.assertEqual(s.launch_values()["env"], s._cli_launch["env"], "the premise: the launch carries every pick")

        def second_pick():
            self.assertTrue(self.be.set_env(SID, {"NOTES_API_FLAG": "2"}))
        made = _made_at_call(s, "launch_values", "_drop_carried_ask", second_pick)
        s._reconcile_waiting_picks(dict(s.launch_values()), True)
        if not made:
            second_pick()                                             # the reconcile made no drop: as it returns
        self._run(q)
        self.assertNotEqual(s.launch_values()["env"], s._cli_launch["env"], "the premise: the launch lacks the second pick")
        self.assertEqual(s._switch_wanted, "session env", "the second env pick's ask stands")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")


class TheWaitingRecordHoldsTheModeEntryWhileThePendingBypassMarkIsUp(_ABypassPickChangedBack):
    """The registry's waiting record (relaunchWait) gains the mode entry only while the pending-bypass mark is up and loses
    it only while the mark is down: _note_pick_waits and _drop_pick_wait read the mark in the hold of the registry lock in
    which they write the record, and take no session lock there."""

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def test_a_default_pick_before_the_bypass_picks_record_write_leaves_no_entry(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        made = _made_before_call(self.be, "_note_pick_waits", "set_mode",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: set_mode(default) was called before the pick's record write")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()), ("default", "", "", False),
                         "the mode is default: no mark, no relaunch ask and no mode entry")

    def test_a_default_pick_at_the_bypass_picks_mode_write_leaves_no_entry(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_write(caller, value):
            if not made and caller == "set_mode" and value == "bypassPermissions":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "mode", on_write=on_write)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: set_mode(default) was called at the bypass pick's mode write")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()), ("default", "", "", False),
                         "the mode is default: no mark, no relaunch ask and no mode entry")

    def test_a_bypass_pick_before_the_withdrawals_record_drop_keeps_its_entry(self):
        s = self._session()
        self._bypass_waits(s)
        made = _made_before_call(self.be, "_drop_pick_wait", "_withdraw_mode_pick",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked again before the withdrawal's record drop")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, s._mode_ask_alone, self._mode_entry()),
                         ("bypassPermissions", "bypassPermissions", "bypass mode", True, True),
                         "the pick made after the default pick waits, its ask its own, its entry on record")


class TheLastLookBeforeATeardownWithdrawsWhatTheWithdrawalWould(_ABypassPickChangedBack):
    """When the waker's last look (SdkSession._switch_teardown_check) stands an armed relaunch down, it withdraws it instead
    of raising its ask again when the withdrawal's rule holds in its hold (SdkSession._mode_relaunch_withdrawn): the
    relaunch was armed for the bypass pick's ask, marked that pick's alone; the pending-bypass mark and every other pick's
    mark are down; no ask of another reason stands and nothing else is on its way; nothing is set aside for it; and the
    launch, read after the quiet read and before the hold, equals the serving CLI's launch record, still the one serving.
    A withdrawal made while the relaunch is on its way leaves the pick's word for this look."""

    def test_a_default_pick_before_the_last_look_withdraws_the_relaunch(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look stood the relaunch down")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted), ("default", "default", "", ""),
                         "nothing was set aside and the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_a_reason_set_aside_keeps_the_relaunch_at_the_last_look(self):
        """Control: a reason other than a pick set aside for the armed relaunch keeps it, raised again for the next quiet
        moment."""
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        s.want_switch_reconnect("always fast")                        # stands down to the relaunch on its way: set aside
        self._run(q)
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("", ["always fast"]), "the premise: set aside")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look stood the relaunch down")
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch keeps its ask")
        self.assertTrue(self._relaunched_at_quiet(s, q), "and is armed at the first quiet moment")


class ARefusedSwitchPutsBypassBackOnlyWhileTheModeReadsIt(_ABypassPickChangedBack):
    """A refused switch puts the confirmed mode back to a confirmed bypassPermissions only while the session's mode reads
    bypassPermissions (SdkSession._do_set_mode): a confirmed mode written since, by the CLI's init or by the reconcile of
    waiting picks from the mode of a launch, stays."""

    def _init_reports(self, s, mode):
        """The CLI's init, read as _on_message reads it, reports `mode` as its permission mode."""
        import asyncio
        SM = type("SystemMessage", (), {"__init__": lambda self_, st, d: setattr(self_, "__dict__", {"subtype": st, "data": d})})

        async def read():
            s._on_message(SM("init", {"model": "claude-x", "permissionMode": mode}), type("A", (), {}), type("R", (), {}), SM)
            await asyncio.sleep(0)
        asyncio.run(read())

    def test_two_picks_from_bypass_and_an_init_before_the_first_is_refused(self):
        s = self._session(mode="bypassPermissions")
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))       # held: no client
        self.assertTrue(self.be.set_mode(SID, "default"))           # held: no client
        self._init_reports(s, "acceptEdits")
        self.assertEqual(s.perm_mode, "acceptEdits", "the premise: the init confirmed acceptEdits")
        _refuse_mode_switch(s, "acceptEdits", "bypassPermissions")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "acceptEdits", "", "default"),
                         "the mode is default: the confirmed mode stays what the init confirmed, not bypassPermissions")

    def test_two_picks_at_the_reconciles_read_of_the_confirmed_mode_and_a_refusal(self):
        """The reconcile of waiting picks takes the confirmed mode from the mode of a launch that lacks the bypass flag
        (SdkSession._reconcile_waiting_picks). With acceptEdits and then default picked and the CLI refusing acceptEdits,
        the confirmed mode stays the launch's: the revert's clause the test above pins for the CLI's init
        (SdkSession._do_set_mode), here for the reconcile's write of the confirmed mode."""
        s = self._session(mode="bypassPermissions", relaunchWait={"why": "bypass mode", "t": 1700000000,
                                                                  "picks": {"mode": {"mode": "bypassPermissions"}}})
        s.inflight = 1                                                # a turn runs
        self.assertEqual(s.perm_mode, "bypassPermissions", "the premise: the confirmed mode is the bypass pick on record")
        made = []

        def on_read(caller, value):
            if (not made and caller == "_reconcile_waiting_picks" and value == "bypassPermissions"
                    and not s._lock.locked()):
                made.append(1)
                self.assertTrue(self.be.set_mode(SID, "acceptEdits"))   # held: no client
                self.assertTrue(self.be.set_mode(SID, "default"))       # held: no client
        _hooked_field(s, "perm_mode", on_read=on_read)
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="acceptEdits"), False)
        self.assertEqual(made, [1], "the premise: both picks were made at the reconcile's read of the confirmed mode")
        self.assertEqual((s.mode, s.perm_mode), ("default", "acceptEdits"),
                         "the premise: the reconcile took the confirmed mode from the launch's mode")
        _refuse_mode_switch(s, "acceptEdits", "bypassPermissions")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, sb.read_reg(self.d, SID).get("mode")),
                         ("default", "acceptEdits", "", "default"),
                         "the mode is default: the confirmed mode stays the launch's mode, not bypassPermissions")


class EachModeHoldReadsAndWritesInOneHold(_ABypassPickChangedBack):
    """The applied pick's hold, the pick decided again and a refused switch's revert each read the session's mode and write
    the confirmed mode and the pending-bypass mark in one hold of the session lock (SdkSession._reconcile_waiting_picks,
    SdkSession._do_set_mode). Each test calls set_mode at that hold's read of the mode, through the session lock's
    stand-in (_AHoldEndSessionLock), so it runs as soon as the hold ends."""

    def _picked_at_the_mode_read(self, s, caller, mode):
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == caller:
                made.append(value)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, mode)))
        _hooked_field(s, "mode", on_read=on_read)
        return made

    def test_the_applied_picks_read_and_write_are_one_hold(self):
        s = self._session(relaunchWait={"why": "bypass mode", "t": 1700000000,
                                        "picks": {"mode": {"mode": "bypassPermissions"}}})
        s.inflight = 1                                                # a turn runs: a bypass pick waits for quiet
        made = self._picked_at_the_mode_read(s, "_reconcile_waiting_picks", "bypassPermissions")
        s._reconcile_waiting_picks(dict(s.launch_values()), True)
        self.assertEqual(made, ["default"], "the premise: Bypass was picked at the applied pick's read of the mode")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("bypassPermissions", "default", "bypassPermissions"),
                         "the bypass pick waits: the confirmed mode is default, with the mark")

    def test_the_pick_decided_agains_read_and_write_are_one_hold(self):
        s = self._session()
        self._bypass_waits(s)
        made = self._picked_at_the_mode_read(s, "_reconcile_waiting_picks", "default")
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)
        self.assertEqual(made, ["bypassPermissions"], "the premise: default was picked at the reconcile's read of the mode")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("default", "default", ""),
                         "the mode is default: the confirmed mode is default and the mark is down")

    def test_the_reverts_read_and_write_are_one_hold(self):
        s = self._session()
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))       # held: no client
        made = self._picked_at_the_mode_read(s, "_do_set_mode", "bypassPermissions")
        _refuse_mode_switch(s, "acceptEdits", "default")
        self.assertEqual(made, ["acceptEdits"], "the premise: Bypass was picked at the revert's read of the mode")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending), ("bypassPermissions", "default", "bypassPermissions"),
                         "the bypass pick keeps the mode and its mark; the confirmed mode is default")


class EachStepActsOnTheAskItsHoldReads(_ABypassPickChangedBack):
    """Each step that reads the standing relaunch ask, what is on its way or the serving CLI's launch record and then
    writes the ask, a reason set aside or the pick's word acts on what it read in that same hold: the arm of a relaunch,
    the ask raised again after the loop-side re-check, the carry, the stand-down to a relaunch on its way, the drop of
    an ask the CLI carries and the withdrawal. Each test makes another step at that read; through the session lock's
    stand-in (_AHoldEndSessionLock) a step made inside a hold runs as soon as that hold ends."""

    def _carried(self, q):
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        return s

    def test_an_ask_raised_at_the_arms_read_of_the_ask_is_set_aside(self):
        q = []
        s = self._carried(q)
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "_do_request_reconnect":
                made.append(1)
                lock.call_free(lambda: s.want_switch_reconnect("always fast"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self._run(q)
        self.assertEqual((made, s._reconnect), ([1], True), "the premise: armed, with an ask raised at the arm's read")
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("", ["always fast"]),
                         "the ask raised is set aside for the relaunch armed")

    def test_a_default_pick_at_the_re_raises_read_withdraws_the_relaunch(self):
        q = []
        s = self._carried(q)
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertEqual(s._switch_ask_pending, "bypass mode", "the premise: carried, its relaunch not armed")
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "_re_raise_switch_ask":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_switch_ask_pending", on_read=on_read)
        s._cli_working = True                                         # a turn the CLI opened itself
        self._run(q)                                                  # the arm's re-check finds it and raises the ask again
        self.assertEqual((made, s.mode, s._mode_pending), ([1], "default", ""), "the premise: set_mode(default) was called")
        self.assertEqual(s._switch_wanted, "", "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_an_ask_raised_at_the_carrys_read_of_the_ask_stands(self):
        q = []
        s = self._carried(q)
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == "_try_switch_reconnect":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the carry's hold reads the ask
                    made.append(1)
                    lock.call_free(lambda: s.want_switch_reconnect("always fast"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask the carry's hold read is carried")
        self.assertEqual((made, s._switch_ask_pending), ([1], "bypass mode"), "the premise: an ask was raised at that read")
        self.assertEqual(s._switch_wanted, "always fast", "the ask raised at the carry's read stands")

    def test_an_ask_raised_at_the_stand_downs_read_is_set_aside(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        s.want_switch_reconnect("effort")                             # a pick's ask; its step waits on the loop
        made = _stepped_at_read(s, "_reconnect", "_try_switch_reconnect", lambda: s.want_switch_reconnect("always fast"))
        self._run(q)
        self.assertEqual(made, [1], "the premise: an ask was raised at the step's read of the relaunch on its way")
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("", ["always fast"]),
                         "the ask raised is set aside for the relaunch on its way")

    def test_an_ask_raised_at_the_drops_launch_read_stands(self):
        s = self._session(auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask")
        made = _made_at_call(s, "launch_values", "_drop_carried_ask", lambda: s.want_switch_reconnect("always fast"))
        self.assertTrue(self.be.set_auth(SID, "login"))               # the billing pick goes back to what the CLI runs
        self.assertEqual(made, [1], "the premise: an ask was raised at the drop's read of the launch")
        self.assertEqual(s._switch_wanted, "always fast", "the ask raised stands")

    def test_a_launch_record_replaced_at_the_withdrawals_launch_read_keeps_the_relaunch(self):
        s = self._session()
        self._bypass_waits(s)
        made = _made_at_call(s, "launch_values", "_withdraw_mode_pick",
                             lambda: setattr(s, "_cli_launch", dict(s._cli_launch, effort="low")))
        self._change_back(s)
        self.assertEqual(made, [1], "the premise: the serving CLI's launch record was replaced at the withdrawal's read")
        self.assertEqual(s._switch_wanted, "bypass mode", "the record compared is not the one serving: the relaunch stands")


# ── the busy re-check's hand-back, the last look's own reason, the reconcile's record write and a reattach ──

class AHandedBackRelaunchIsWithdrawnByTheWithdrawalsRule(_ABypassPickChangedBack):
    """When the loop-side re-check finds work and hands the bypass pick's carried ask back
    (SdkSession._re_raise_switch_ask), it withdraws that relaunch instead of raising the ask again when the withdrawal's
    rule holds in its hold (SdkSession._mode_relaunch_withdrawn, which takes the mode read in the hold as the mode of the
    launch read before it), and a standing ask of the same reason goes with it. A bypass pick made while its ask is
    carried keeps the word that the ask is its alone when the carried ask was (SdkBackend.set_mode). While the rule does
    not hold the ask stands for the next quiet moment."""

    def _carried(self, q):
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertEqual((s._switch_ask_pending, s._switch_wanted), ("bypass mode", ""), "the premise: carried, not armed")
        return s

    def _withdrawn(self, s, q):
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted, s._switch_ask_pending, s._reconnect),
                         ("default", "default", "", "", "", False),
                         "the relaunch would change nothing: withdrawn, with no ask standing")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_a_default_pick_and_then_a_turn_before_the_arm(self):
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        s.inflight = 1                                                # a turn runs when the loop reaches the arm
        self._run(q)
        self._withdrawn(s, q)

    def test_a_default_pick_and_then_a_turn_the_cli_opened_before_the_arm(self):
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True                                         # a turn the CLI opened itself
        self._run(q)
        self._withdrawn(s, q)

    def test_bypass_picked_again_and_then_default_before_the_arm(self):
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the pick made while its own ask is carried asks")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self._withdrawn(s, q)

    def test_a_default_pick_while_a_reattach_to_the_same_cli_is_armed(self):
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.be._on_host_reexec_now(s)                                # a reattach to the same CLI, which launches nothing
        self.assertTrue((s._reconnect, s._reattach_keeps_cli) == (True, True), "the premise: the reattach is armed")
        s._cli_working = True
        self._run(q)
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, s._switch_ask_pending), ("default", "", "", ""),
                         "the relaunch would change nothing: withdrawn, with no ask standing")

    def _default_at_the_hand_backs_launch_read(self, q, turn):
        s = self._carried(q)
        made = _made_at_call(s, "launch_values", "_re_raise_switch_ask",
                             lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        setattr(s, turn, 1 if turn == "inflight" else True)
        self._run(q)
        self.assertEqual(made, [1], "the premise: default was picked at the hand-back's read of the launch")
        return s

    def test_a_default_pick_at_the_hand_backs_launch_read_withdraws_the_relaunch(self):
        """Default is picked at the hand-back's read of the launch: the hold reads the mode set_mode wrote under the
        session lock, so the launch it compares is in the default mode."""
        q = []
        s = self._default_at_the_hand_backs_launch_read(q, "_cli_working")
        self._withdrawn(s, q)

    def test_a_default_pick_at_the_hand_backs_launch_read_with_a_turn_withdraws_the_relaunch(self):
        q = []
        s = self._default_at_the_hand_backs_launch_read(q, "inflight")
        self._withdrawn(s, q)

    def test_a_default_pick_as_the_hand_backs_hold_is_entered_withdraws_the_relaunch(self):
        """Default is picked as the hand-back's hold of the session lock is entered: the rule takes the mode read in the
        hold, so the launch it compares is in the default mode."""
        q = []
        s = self._carried(q)
        lock = s._lock = _StepWhenTheLockIsTaken(s._lock, "_re_raise_switch_ask",
                                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        s._cli_working = True
        self._run(q)
        self.assertTrue(lock.done, "the premise: default was picked as the hand-back's hold was entered")
        self._withdrawn(s, q)

    def test_bypass_picked_again_before_the_arm_keeps_its_ask(self):
        """With the pending-bypass mark up, the ask handed back stands, the pick's own, and set_mode keeps the word that
        the carried ask was the pick's alone."""
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        s._cli_working = True
        self._run(q)
        self.assertEqual((s._mode_pending, s._switch_wanted, s._mode_ask_alone), ("bypassPermissions", "bypass mode", True),
                         "the pick waits with its own ask")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")

    def test_a_retry_set_aside_keeps_the_relaunch(self):
        """Control: a reason set aside for the relaunch carried keeps it at the hand-back."""
        q = []
        s = self._session(queued=q)
        self._arm_retry(s)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: carried")
        self.assertEqual(sorted(s._switch_riders), ["retry upgrade"], "the premise: the carry set the retry aside")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch keeps its ask")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")

    def test_a_launch_record_in_another_mode_keeps_the_relaunch(self):
        """Control: a launch now that differs from the serving CLI's record keeps the relaunch at the hand-back."""
        q = []
        s = self._carried(q)
        s._cli_launch = dict(s._cli_launch, mode="acceptEdits")      # the serving CLI runs another mode
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch that changes the mode keeps its ask")


    def test_an_always_fast_ask_handed_back_with_the_picks_word_up_stands(self):
        """Control: only the bypass pick's relaunch is withdrawn at the hand-back. Always fast's ask, carried after it took
        the place of the pick's ask and handed back with the pick's word up, stands, and its relaunch carries the opt-in."""
        q = []
        s = self._opt_in_lost(q)
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(q)
        s.want_switch_reconnect("always fast")
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("always fast", True),
                         "the premise: Always fast's ask stands, the pick's word up")
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: Always fast's ask is carried")
        self.assertEqual(s._switch_ask_pending, "always fast", "the premise: carried, not armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "always fast", "Always fast's ask stands")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")


    def test_a_bypass_pick_made_while_another_picks_ask_is_carried_is_not_its_alone(self):
        """A bypass pick made while another pick's ask is carried is not marked its alone, even after an earlier bypass pick
        was: the carried ask, handed back while it stands, would stand for that pick too."""
        q = []
        s = self._session(queued=q, auth="login")
        self._bypass_waits(s, q)
        self.assertTrue(self.be.set_auth(SID, "key"))
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("billing", True),
                         "the premise: the billing pick's ask stands, the earlier pick's word up")
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the billing pick's ask is carried")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._mode_ask_alone), ("bypass mode", "billing", False),
                         "the pick's ask stands beside the billing pick's carried ask, not marked its alone")


    def test_an_always_fast_ask_handed_back_beside_the_picks_ask_is_set_aside(self):
        """Control: Always fast's ask, carried and handed back while the bypass pick's ask stands, is set aside for that
        ask, which keeps the relaunch with the opt-in."""
        q = []
        s = self._opt_in_lost(q)
        s.inflight = 1
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(q)
        s.want_switch_reconnect("always fast")
        self._run(q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: Always fast's ask is carried")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s._switch_wanted, s._switch_ask_pending), ("bypass mode", "always fast"),
                         "the premise: the pick's ask stands beside Always fast's carried ask")
        s._cli_working = True
        self._run(q)
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("bypass mode", ["always fast"]),
                         "Always fast's ask is set aside for the ask standing")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")


class TheLastLooksRuleBesideEveryOtherState(_ABypassPickChangedBack):
    """The waker's last look (SdkSession._switch_teardown_check) withdraws a relaunch armed for the bypass pick's ask
    only when every term of the withdrawal's rule holds in its hold (SdkSession._mode_relaunch_withdrawn, which takes the
    mode read in the hold as the mode of the launch read before it); a standing ask of the same reason does not hold the
    relaunch and goes with it. Each test sets one other term and shows the relaunch kept, or makes a step at a read
    inside the hold, through the session lock's stand-in (_AHoldEndSessionLock), or at the launch read before it."""

    def _armed(self, q, **reg):
        s = self._session(queued=q, **reg)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        self.assertEqual((s._reconnect, s._reconnect_switch_why), (True, "bypass mode"), "the premise: armed for the pick")
        return s

    def _looked(self, s, q):
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look stood the relaunch down")
        self._run(q)

    def test_bypass_then_default_picked_before_the_last_look(self):
        q = []
        s = self._armed(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: the second pick's ask stands")
        self._looked(s, q)
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted), ("default", "default", "", ""),
                         "the relaunch would change nothing: withdrawn with the ask of the same reason")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_an_always_fast_ask_standing_keeps_the_relaunch_with_the_opt_in(self):
        q = []
        s = self._opt_in_lost(q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s.want_switch_reconnect("always fast")
        self.assertEqual(s._switch_wanted, "always fast", "the premise: its ask stands, its step waiting on the loop")
        s.inflight = 1
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look stood the relaunch down")
        self.assertEqual(sorted(s._switch_riders), ["always fast"], "Always fast's ask is set aside for the relaunch")
        self.assertTrue(self._relaunched_at_quiet(s, q), "the relaunch is armed at the first quiet moment")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_a_default_pick_at_the_last_looks_launch_read_withdraws_the_relaunch(self):
        """Default is picked at the last look's read of the launch: the hold reads the mode set_mode wrote under the
        session lock, so the launch it compares is in the default mode."""
        q = []
        s = self._armed(q)
        made = _made_at_call(s, "launch_values", "_switch_teardown_check",
                             lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self._looked(s, q)
        self.assertEqual(made, [1], "the premise: default was picked at the last look's read of the launch")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted, s._switch_ask_pending),
                         ("default", "default", "", "", ""), "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_a_default_pick_as_the_last_looks_hold_is_entered_withdraws_the_relaunch(self):
        """Default is picked as the last look's hold of the session lock is entered: the rule takes the mode read in the
        hold, so the launch it compares is in the default mode."""
        q = []
        s = self._armed(q)
        lock = s._lock = _StepWhenTheLockIsTaken(s._lock, "_switch_teardown_check",
                                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self._looked(s, q)
        self.assertTrue(lock.done, "the premise: default was picked as the last look's hold was entered")
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted, s._switch_ask_pending),
                         ("default", "default", "", "", ""), "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")

    def test_a_launch_record_in_another_mode_keeps_the_relaunch(self):
        q = []
        s = self._armed(q)
        s._cli_launch = dict(s._cli_launch, mode="acceptEdits")      # the serving CLI runs another mode
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._looked(s, q)
        self.assertEqual(s._switch_wanted, "bypass mode", "a relaunch that changes the mode keeps its ask")

    def test_an_env_pick_keeps_the_relaunch(self):
        q = []
        s = self._armed(q)
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_FLAG": "1"}))
        self._run(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._looked(s, q)
        self.assertEqual(s._switch_wanted, "bypass mode", "the relaunch an env pick needs keeps its ask")
        self.assertTrue(self._relaunched_at_quiet(s, q), "and is armed at the first quiet moment")

    def test_bypass_picked_again_keeps_the_relaunch(self):
        q = []
        s = self._session(queued=q, mode="bypassPermissions")
        self.assertEqual(s._cli_launch["mode"], "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "default"))            # no client: held, the confirmed mode default
        self._run(q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))   # picked again before the last look
        self._run(q)
        self.assertEqual(s._mode_pending, "bypassPermissions", "the premise: the mark is up")
        self._looked(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the waiting bypass pick keeps a relaunch")

    def test_an_ask_that_was_not_the_picks_alone_keeps_the_relaunch(self):
        q = []
        s = self._session(queued=q, auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self._run(q)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", False), "the premise: not the pick's alone")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertEqual(s._auth_pending, "", "the premise: the billing pick is back to what the CLI runs")
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._looked(s, q)
        self.assertEqual(s._switch_wanted, "bypass mode", "an ask that was not the pick's alone keeps the relaunch")

    def test_an_effort_mark_up_keeps_the_relaunch(self):
        q = []
        s = self._armed(q)
        orig = s.effort
        self.assertTrue(self.be.set_effort(SID, "low" if orig != "low" else "high"))
        self.assertTrue(self.be.set_effort(SID, orig))               # back to what the CLI runs; the mark stays up
        self._run(q)
        self.assertTrue(s._effort_pending, "the premise: the effort mark is up")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._looked(s, q)
        self.assertEqual(s._switch_wanted, "bypass mode", "with the effort mark up the relaunch keeps its ask")

    def test_an_always_fast_relaunch_armed_keeps_its_ask(self):
        """Only a relaunch armed for the bypass pick's ask is withdrawn: an Always fast relaunch with the pick's word up
        keeps its ask."""
        q = []
        s = self._opt_in_lost(q)
        s._mode_ask_alone = True                                      # a word left up for a relaunch on its way
        s.inflight = 0
        self.assertEqual(self.be.apply_model_switches(), 1, "the premise: Always fast asks")
        self._run(q)
        self.assertEqual((s._reconnect, s._reconnect_switch_why), (True, "always fast"), "the premise: armed")
        s.inflight = 1
        self.assertTrue(s._switch_teardown_check())
        self.assertEqual(s._switch_wanted, "always fast", "the Always fast relaunch keeps its ask")

    def test_bypass_picked_at_the_hold_read_of_the_word_waits_with_its_own_ask(self):
        q = []
        s = self._armed(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s.inflight = 1
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "_mode_relaunch_withdrawn":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        _hooked_field(s, "_mode_ask_alone", on_read=on_read)
        self.assertTrue(s._switch_teardown_check())
        self._run(q)
        self.assertEqual(made, [1], "the premise: Bypass was picked at the last look's read of the pick's word")
        self.assertEqual((s._mode_pending, s._switch_wanted, s._mode_ask_alone), ("bypassPermissions", "bypass mode", True),
                         "the pick made at that read waits, its ask its own")

    def test_a_default_pick_at_the_holds_first_write_withdraws_the_relaunch(self):
        q = []
        s = self._armed(q)
        s.inflight = 1
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_write(who, value):
            if not made and who == "_switch_teardown_check" and value is False:
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_reconnect", on_write=on_write)
        self.assertTrue(s._switch_teardown_check())
        self._run(q)
        self.assertEqual(made, [1], "the premise: default was picked at the last look's first write")
        self.assertFalse(self._relaunched_at_quiet(s, q), "the relaunch would change nothing: nothing is armed")


    def test_a_relaunch_waiting_for_the_turns_end_keeps_the_ask_for_it(self):
        """A relaunch waiting for the turn's end is on its way: the last look raises the bypass pick's ask again, and no line
        says the relaunch was withdrawn."""
        q = []
        s = self._armed(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s._reconnect_when_idle = True                                 # a rewind waits for the turn's end
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        self._looked(s, q)
        self.assertEqual(s._switch_wanted, "bypass mode", "the ask stands for the relaunch on its way")
        self.assertEqual([m for m in lines if "withdrawn" in m], [], "no line says the relaunch was withdrawn")


class TheWithdrawalsHoldReadsTheMarkAndWhatIsOnItsWayWithItsWrite(_ABypassPickChangedBack):
    """The withdrawal (SdkBackend._withdraw_mode_pick) reads the pending-bypass mark and what is on its way in the hold
    where it clears the ask. A relaunch armed for a reattach to the same CLI is not on its way: the reattach launches
    nothing, and its connect's reconcile asks again only for a pick its CLI lacks."""

    def test_bypass_picked_at_the_holds_read_of_the_mark_keeps_its_ask(self):
        s = self._session()
        self._bypass_waits(s)
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "_withdraw_mode_pick":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        _hooked_field(s, "_mode_pending", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked at the withdrawal's read of the mark")
        self.assertEqual((s._mode_pending, s._switch_wanted, s._mode_ask_alone), ("bypassPermissions", "bypass mode", True),
                         "the pick made at that read waits with its own ask")

    def test_a_carry_at_the_holds_read_of_what_is_on_its_way_carries_nothing(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "_withdraw_mode_pick":
                made.append(1)
                lock.call_free(s._try_switch_reconnect)
        _hooked_field(s, "_reconnect_when_idle", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        self.assertEqual(made, [1], "the premise: the carry was made at the withdrawal's read of what is on its way")
        self.assertEqual((s._switch_wanted, s._switch_ask_pending, s._reconnect), ("", "", False),
                         "the withdrawn relaunch is neither carried nor armed")

    def test_a_default_pick_while_a_reattach_to_the_same_cli_is_armed(self):
        s = self._session()
        self._bypass_waits(s)
        self.be._on_host_reexec_now(s)
        self.assertTrue(s._reconnect, "the premise: the reattach is armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._reconnect, s._reconnect_switch_why = False, ""             # the connect loop's next pass, at its top
        s._reconcile_waiting_picks(dict(s._cli_launch), False)       # the same CLI, attached again
        self.assertEqual((s.mode, s.perm_mode, s._mode_pending, s._switch_wanted), ("default", "default", "", ""),
                         "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")


    def test_a_default_pick_while_a_reattach_follows_the_picks_armed_relaunch(self):
        """A reattach armed after the pick's relaunch was armed for it leaves that relaunch on its way: the withdrawal
        leaves the pick's word, and the last look that stands the relaunch down withdraws it."""
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        self.be._on_host_reexec_now(s)
        self.assertEqual((s._reconnect, s._reconnect_switch_why, s._reattach_keeps_cli), (True, "bypass mode", True),
                         "the premise: a reattach armed beside the pick's relaunch")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s.inflight = 1                                                # the CLI took a turn before the teardown
        self.assertTrue(s._switch_teardown_check(), "the premise: the waker's last look stood the relaunch down")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", ""),
                         "the relaunch would change nothing: withdrawn")
        self.assertFalse(self._relaunched_at_quiet(s, q), "and nothing is armed at the first quiet moment")


class TheReconcilesRecordWriteKeepsABypassPicksEntry(_ABypassPickChangedBack):
    """The reconcile of waiting picks writes the registry's waiting record as its registry read found it; while the
    pending-bypass mark is up and that write carried no mode entry, it writes the entry again through _note_pick_waits,
    which reads the mark in the registry lock's hold (SdkSession._reconcile_waiting_picks)."""

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def test_a_bypass_pick_at_the_reconciles_launch_read_keeps_its_entry(self):
        s = self._session(relaunchWait={"why": "effort", "t": 1700000000, "picks": {"effort": {"effort": "high"}}})
        launch = dict(s.launch_values())
        s.inflight = 1                                                # a turn runs: a bypass pick waits for quiet
        made = _made_at_call(s, "launch_values", "_reconcile_waiting_picks",
                             lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        s._reconcile_waiting_picks(launch, False)
        self.assertEqual(made, [1], "the premise: Bypass was picked at the reconcile's read of the launch")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()),
                         ("bypassPermissions", "bypassPermissions", "bypass mode", True),
                         "the waiting pick keeps its mode entry")

    def test_a_default_pick_after_it_leaves_no_entry(self):
        """Control: the entry follows the mark, so a default pick after the reconcile leaves none."""
        s = self._session(relaunchWait={"why": "effort", "t": 1700000000, "picks": {"effort": {"effort": "high"}}})
        launch = dict(s.launch_values())
        s.inflight = 1
        _made_at_call(s, "launch_values", "_reconcile_waiting_picks",
                      lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        s._reconcile_waiting_picks(launch, False)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()), ("default", "", "", False),
                         "no mark, no relaunch ask and no mode entry")

    def test_a_launch_that_carried_the_pick_leaves_no_entry(self):
        """Control: the pick the launch carried is applied and its entry goes."""
        s = self._session()
        self._bypass_waits(s)
        self.assertTrue(self._mode_entry(), "the premise: the waiting record holds the mode entry")
        s._reconcile_waiting_picks(dict(s.launch_values()), True)
        self.assertEqual((s.perm_mode, s._mode_pending, self._mode_entry()), ("bypassPermissions", "", False),
                         "applied: the confirmed mode is bypassPermissions, with no mark and no mode entry")


    def test_a_record_write_that_carried_the_entry_keeps_its_why_and_time(self):
        """Control: while the bypass pick waits and the record write carried its entry, the record keeps the why and the
        time it was written with."""
        s = self._session(mode="bypassPermissions", effort="max",
                          relaunchWait={"why": "effort", "t": 1700000000,
                                        "picks": {"effort": {"effort": "max"}, "mode": {"mode": "bypassPermissions"}}})
        s._mode_pending = "bypassPermissions"                         # the waiting pick's mark, as a host's restart seeds it
        s.inflight = 1
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default", effort="low"), False)
        self.assertEqual((s._mode_pending, s._switch_wanted), ("bypassPermissions", "effort"),
                         "the premise: both picks still wait, with the record's reason asked for")
        wait = sb.read_reg(self.d, SID).get("relaunchWait") or {}
        self.assertEqual((wait.get("why"), wait.get("t"), sorted(wait.get("picks") or {})),
                         ("effort", 1700000000, ["effort", "mode"]), "the record as it was written, both picks waiting")


class EachHoldThatSetsTheAskReadsWhatItDecidesOn(_ABypassPickChangedBack):
    """Each hold of the session lock that sets the relaunch ask, the line said for it or a reason set aside reads what it
    decides on in that hold: want_switch_reconnect's ask and mark, the stand-down to a relaunch on its way, the line said
    while the session is busy, the line said while it waits for a launch slot, the drop of an ask the serving CLI carries
    and the arm at a turn's end. Each test makes a step at a read inside the hold, through the session lock's stand-in
    (_AHoldEndSessionLock), and shows its end."""

    def test_an_always_fast_ask_at_want_switch_reconnects_read_of_the_ask_keeps_its_relaunch(self):
        s = self._opt_in_lost()
        s.inflight = 1
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "want_switch_reconnect" and s._mode_pending:
                made.append(1)
                lock.call_free(lambda: s.want_switch_reconnect("always fast"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: Always fast asked at the bypass pick's read of the ask")
        self._change_back(s)
        after = (s._switch_wanted, sorted(s._switch_riders))
        self.assertTrue(self._relaunched_at_quiet(s), "the Always fast ask keeps a relaunch: %r" % (after,))
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")

    def test_a_default_pick_at_want_switch_reconnects_read_of_the_mark_leaves_no_ask(self):
        s = self._session()
        s.inflight = 1
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "want_switch_reconnect" and value:
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_mode_pending", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: default was picked at want_switch_reconnect's read of the mark")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("default", "", ""), "no mark and no relaunch ask")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")

    def test_an_ask_raised_at_the_stand_downs_check_is_set_aside(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        s.want_switch_reconnect("effort")                             # a pick's ask; its step waits on the loop
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == "_try_switch_reconnect":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the stand-down hold's read of the ask
                    made.append(1)
                    lock.call_free(lambda: s.want_switch_reconnect("always fast"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self._run(q)
        self.assertEqual(made, [1], "the premise: an ask was raised at the stand-down's check")
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("", ["always fast"]),
                         "the ask raised is set aside for the relaunch on its way")

    def test_a_default_pick_at_the_busy_lines_read_leaves_the_next_ask_its_line(self):
        s = self._session()
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        s.inflight = 1
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == "_try_switch_reconnect":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the busy line's hold reads the ask
                    made.append(1)
                    lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: default was picked at the busy line's read of the ask")
        self.assertEqual((s.mode, s._switch_wanted), ("default", ""), "the premise: withdrawn")
        start = len(lines)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))   # picked again while the turn runs
        waiting = "waiting for the session to go quiet"
        self.assertEqual(len([m for m in lines[start:] if waiting in m]), 1, "the new ask says once what it waits for")

    def _no_free_slot(self, s):
        slots = self.be._spawn_sem
        while slots.acquire(blocking=False):
            pass                                                      # every slot held elsewhere
        return slots

    def test_a_default_pick_before_the_slot_line_leaves_the_next_ask_its_line(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        slots = self._no_free_slot(s)
        made = _made_before_call(slots, "take", "_try_switch_reconnect",
                                 lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: default was picked before the slot was asked for")
        self.assertEqual((s.mode, s._switch_wanted), ("default", ""), "the premise: withdrawn")
        slotline = "waiting for a launch slot"
        self.assertEqual([m for m in lines if slotline in m], [], "no slot line for a withdrawn ask")
        start = len(lines)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(len([m for m in lines[start:] if slotline in m]), 1, "the new ask says its slot line once")

    def test_a_default_pick_at_the_slot_lines_read_leaves_the_next_ask_its_line(self):
        relaunched = []
        s = self._quiet(SID, "web", relaunched, mode="default")
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        self._no_free_slot(s)
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == "_try_switch_reconnect":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the slot line's hold reads the ask
                    made.append(1)
                    lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: default was picked at the slot line's read of the ask")
        self.assertEqual((s.mode, s._switch_wanted), ("default", ""), "the premise: withdrawn")
        start = len(lines)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        slotline = "waiting for a launch slot"
        self.assertEqual(len([m for m in lines[start:] if slotline in m]), 1, "the new ask says its slot line once")

    def test_an_ask_raised_at_the_drops_read_of_the_ask_stands(self):
        s = self._session(auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask")
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made, reads = [], []

        def on_read(who, value):
            if who == "_drop_carried_ask":
                reads.append(value)
                if len(reads) == 2 and not made:                      # the drop's hold reads the ask
                    made.append(1)
                    lock.call_free(lambda: s.want_switch_reconnect("always fast"))
        _hooked_field(s, "_switch_wanted", on_read=on_read)
        self.assertTrue(self.be.set_auth(SID, "login"))               # the billing pick goes back to what the CLI runs
        self.assertEqual(made, [1], "the premise: an ask was raised at the drop's read of the ask")
        self.assertEqual(s._switch_wanted, "always fast", "the ask raised stands")

    def test_a_default_pick_at_the_turn_ends_arm_says_nothing_was_withdrawn(self):
        """A rewind waiting for the turn's end arms its relaunch at the result (SdkSession._on_message) in one hold; a
        default pick made at that hold's first write finds a relaunch on its way, and no line says one was withdrawn."""
        import asyncio
        s = self._session()
        self._bypass_waits(s)
        s._reconnect_when_idle = True                                 # a rewind waits for the turn's end
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_write(who, value):
            if not made and who == "_on_message" and value is False:
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_reconnect_when_idle", on_write=on_write)
        msg = psr._result(uuid="11111111-2222-3333-4444-555555555501", subtype="success", is_error=False, num_turns=1,
                          session_id=psr.FSID, duration_ms=1, duration_api_ms=1, total_cost_usd=0.01,
                          usage={"input_tokens": 1, "output_tokens": 1}, result="ok", parent_tool_use_id=None)

        async def read():
            s._on_message(msg, psr._AssistantMessage, psr._ResultMessage, psr._SystemMessage)
            await asyncio.sleep(0)
        asyncio.run(read())
        self.assertEqual(made, [1], "the premise: default was picked at the arm's first write")
        self.assertTrue(s._reconnect, "the premise: the rewind's relaunch is armed")
        self.assertEqual([m for m in lines if "withdrawn" in m], [], "no line says a relaunch was withdrawn")

    def test_a_default_pick_at_the_reconciles_mark_write_leaves_no_ask(self):
        s = self._session(mode="bypassPermissions", relaunchWait={"why": "bypass mode", "t": 1700000000,
                                                                  "picks": {"mode": {"mode": "bypassPermissions"}}})
        s.inflight = 1
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted), ("bypassPermissions", "", ""),
                         "the premise: the pick on record, its mark not up")
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_write(who, value):
            if not made and who == "_reconcile_waiting_picks" and value == "bypassPermissions":
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        _hooked_field(s, "_mode_pending", on_write=on_write)
        s._reconcile_waiting_picks(dict(s.launch_values(), mode="default"), False)
        self.assertEqual(made, [1], "the premise: default was picked at the reconcile's write of the mark")
        self.assertEqual((s.mode, s._mode_pending), ("default", ""), "the mode is default, the mark down")
        self.assertFalse(self._relaunched_at_quiet(s), "and nothing is armed at the first quiet moment")


class ADropOfAnAskReadsEveryPicksMark(_ABypassPickChangedBack):
    """_drop_carried_ask keeps the standing ask while an effort pick's mark or the fast-mode mark is up (SdkSession.
    _drop_carried_ask)."""

    def test_an_effort_mark_up_keeps_the_ask(self):
        s = self._session(auth="login")
        s.inflight = 1
        orig = s.effort
        self.assertTrue(self.be.set_effort(SID, "low" if orig != "low" else "high"))
        self.assertTrue(self.be.set_effort(SID, orig))
        self.assertTrue(s._effort_pending, "the premise: the effort mark is up")
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self.assertEqual(s._switch_wanted, "billing", "with the effort mark up the ask stands")

    def test_a_fast_mark_up_keeps_the_ask_with_the_opt_in(self):
        """The fast-mode pick's ask, carried and handed back while a billing pick's ask stands, is set aside for it; the
        billing pick going back to what the CLI runs leaves that ask while the fast-mode mark is up, and the relaunch at
        the first quiet moment carries the opt-in."""
        q = []
        s = self._session(queued=q, auth="login", model="opus")
        s._cli_launch = dict(s._cli_launch, fast=True)               # launched with the opt-in, lost since
        s._fast_unlocked, s._fast_off_sent = False, True
        s.inflight, s._cli_working = 0, False
        self.assertTrue(self.be.set_fast(SID, "on"))
        fn, a = q.pop(0)                                              # the ask's step on the loop: the carry, no further
        fn(*a)
        self.assertEqual((s._fast_pending, s._switch_ask_pending), ("on", "fast mode"),
                         "the premise: the fast-mode pick's ask is carried, its arm waiting on the loop")
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s._switch_wanted, "billing", "the premise: the billing pick's ask")
        s.inflight = 1
        self._run(q)
        self.assertEqual((s._switch_wanted, sorted(s._switch_riders)), ("billing", ["fast mode"]),
                         "the premise: the hand-back set the fast-mode reason aside for the billing ask")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertEqual(s._switch_wanted, "billing", "with the fast-mode mark up the ask stands")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")
        self.assertTrue(s.launch_values()["fast"], "with the opt-in")


class _StepWhenTheLockIsTaken:
    """Stands in for a lock (the backend's registry lock, a session's lock): at the first `with` taken in a function
    whose caller `depth` frames up is `caller` (depth 1: the function that takes the lock), step() runs just before the
    lock is taken."""

    def __init__(self, inner, caller, step, depth=1):
        self.inner, self.caller, self.step, self.depth, self.done = inner, caller, step, depth, False

    def __enter__(self):
        if not self.done and sys._getframe(self.depth).f_code.co_name == self.caller:
            self.done = True
            self.step()
        return self.inner.__enter__()

    def __exit__(self, *a):
        return self.inner.__exit__(*a)

    def acquire(self, *a, **k):
        return self.inner.acquire(*a, **k)

    def release(self):
        return self.inner.release()

    def locked(self):
        return self.inner.locked()


class TheWaitingRecordReadsTheMarkInTheRegistryLocksHold(_ABypassPickChangedBack):
    """_note_pick_waits and _drop_pick_wait read the pending-bypass mark inside the registry lock's hold where they write
    the record, in one hold: a pick made just before that lock is taken decides the entry, and a pick made at that read
    of the mark, through the registry lock's stand-in (_AHoldEndSessionLock), runs as soon as the hold ends, so its own
    record steps decide the entry."""

    def _mode_entry(self):
        return "mode" in ((sb.read_reg(self.d, SID).get("relaunchWait") or {}).get("picks") or {})

    def _picked_at_the_mark_read(self, s, caller, mode):
        lock = self.be._reg_lock = _AHoldEndSessionLock(self.be._reg_lock)
        made = []

        def on_read(who, value):
            if not made and who == caller:
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, mode)))
        _hooked_field(s, "_mode_pending", on_read=on_read)
        return made

    def test_a_default_pick_at_the_record_writes_read_of_the_mark_leaves_no_entry(self):
        s = self._session()
        s.inflight = 1                                                # a turn runs: the pick waits for quiet
        made = self._picked_at_the_mark_read(s, "_note_pick_waits", "default")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(made, [1], "the premise: default was picked at the record write's read of the mark")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted, self._mode_entry()), ("default", "", "", False),
                         "no mark, no relaunch ask and no mode entry")

    def test_a_bypass_pick_at_the_record_drops_read_of_the_mark_keeps_its_entry(self):
        s = self._session()
        self._bypass_waits(s)
        made = self._picked_at_the_mark_read(s, "_drop_pick_wait", "bypassPermissions")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked at the record drop's read of the mark")
        self.assertEqual((s.mode, s._mode_pending, self._mode_entry()), ("bypassPermissions", "bypassPermissions", True),
                         "the waiting pick keeps its entry")

    def test_a_default_pick_as_the_record_write_takes_the_lock_leaves_no_entry(self):
        s = self._session()
        s.inflight = 1
        self.be._reg_lock = lock = _StepWhenTheLockIsTaken(
            self.be._reg_lock, "_note_pick_waits", lambda: self.assertTrue(self.be.set_mode(SID, "default")))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertTrue(lock.done, "the premise: default was picked as the record write took the lock")
        self.assertEqual((s.mode, s._mode_pending, self._mode_entry()), ("default", "", False), "no mode entry")

    def test_a_bypass_pick_as_the_record_drop_takes_the_lock_keeps_its_entry(self):
        s = self._session()
        self._bypass_waits(s)
        self.be._reg_lock = lock = _StepWhenTheLockIsTaken(
            self.be._reg_lock, "_drop_pick_wait", lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(lock.done, "the premise: Bypass was picked as the record drop took the lock")
        self.assertEqual((s.mode, s._mode_pending, self._mode_entry()), ("bypassPermissions", "bypassPermissions", True),
                         "the waiting pick keeps its entry")


class TheHandBacksRuleBesideEveryOtherState(_ABypassPickChangedBack):
    """The busy re-check's hand-back (SdkSession._re_raise_switch_ask) keeps the bypass pick's carried ask when a term
    of the withdrawal's rule does not hold in its hold (SdkSession._mode_relaunch_withdrawn): each test reaches one such
    state by picks and asks, or sets a relaunch waiting for the turn's end directly, and shows the ask kept."""

    def _carried(self, q, **reg):
        s = self._session(queued=q, **reg)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertEqual((s._switch_ask_pending, s._switch_wanted), ("bypass mode", ""),
                         "the premise: carried, not armed")
        return s

    def test_bypass_picked_again_keeps_the_relaunch(self):
        """A CLI launched with the flag whose confirmed mode is default: Bypass picked again while its ask is carried
        raises the mark, and the hand-back keeps the ask for that pick."""
        q = []
        s = self._session(queued=q, mode="bypassPermissions")
        self.assertEqual(s._cli_launch["mode"], "bypassPermissions", "the premise: the CLI was launched with the flag")
        self.assertTrue(self.be.set_mode(SID, "default"))            # no client: held, the confirmed mode default
        self._run(q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((s._mode_pending, s._mode_ask_alone), ("bypassPermissions", True),
                         "the premise: the mark is up")
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "bypass mode", "the waiting bypass pick keeps its ask")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")

    def test_an_ask_that_was_not_the_picks_alone_keeps_the_relaunch(self):
        q = []
        s = self._session(queued=q, auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self._run(q)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", False),
                         "the premise: not the pick's alone")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertEqual(s._auth_pending, "", "the premise: the billing pick is back to what the CLI runs")
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried")
        self.assertEqual(s._switch_ask_pending, "bypass mode", "the premise: carried, not armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "bypass mode", "an ask that was not the pick's alone keeps the relaunch")

    def test_a_retry_ask_standing_keeps_its_relaunch(self):
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        s.want_switch_reconnect("retry upgrade")                      # its step waits on the loop behind the arm
        self.assertEqual(s._switch_wanted, "retry upgrade", "the premise: the retry's ask stands")
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "retry upgrade", "the retry's ask stands after the hand-back")
        self.assertTrue(self._relaunched_at_quiet(s, q), "armed at the first quiet moment")

    def test_a_relaunch_waiting_for_the_turns_end_keeps_the_ask_for_it(self):
        """A relaunch waiting for the turn's end is on its way: the hand-back raises the bypass pick's ask again, and no
        line says the relaunch was withdrawn."""
        q = []
        s = self._carried(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._reconnect_when_idle = True                                 # a rewind waits for the turn's end
        lines = []
        self.be._log = lambda m, *a, **k: lines.append(m)
        s._re_raise_switch_ask()
        self.assertEqual(s._switch_wanted, "bypass mode", "the ask stands for the relaunch on its way")
        self.assertEqual([m for m in lines if "withdrawn" in m], [], "no line says the relaunch was withdrawn")


class EachTermOfTheWithdrawalsRuleIsReadAtBothSteps(_ABypassPickChangedBack):
    """The hand-back (SdkSession._re_raise_switch_ask) and the last look (SdkSession._switch_teardown_check) each read
    every term of the withdrawal's rule (SdkSession._mode_relaunch_withdrawn) at their own call. Each case sets one term
    directly on a session where every other term holds, whether or not picks and asks reach that state at that step, and
    shows the bypass pick's relaunch kept; the base cases show it withdrawn. Two terms are left out where the step's own
    hold clears them before the rule reads them: the carried ask at the hand-back, and the relaunch armed (with its
    switch reason) at the last look."""

    def _at_the_hand_back(self, change):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual((s._mode_pending, s._mode_ask_alone, s._switch_ask_pending), ("", True, "bypass mode"),
                         "the premise: the default pick left the carried ask and the pick's word")
        change(s, "_re_raise_switch_ask")
        s._re_raise_switch_ask()
        return s._switch_wanted

    def _at_the_last_look(self, change):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        self.assertTrue(self._relaunched_at_quiet(s, q), "the premise: the pick's relaunch is armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        self.assertEqual((s._mode_pending, s._mode_ask_alone, s._reconnect_switch_why), ("", True, "bypass mode"),
                         "the premise: the default pick left the armed relaunch and the pick's word")
        s.inflight = 1
        change(s, "_switch_teardown_check")
        self.assertTrue(s._switch_teardown_check(), "the premise: the last look stood the relaunch down")
        return s._switch_wanted

    @staticmethod
    def _set(**fields):
        def change(s, _caller):
            for k, v in fields.items():
                setattr(s, k, v)
        return change

    def _record_replaced_by_an_equal_one(self, s, caller):
        _made_at_call(s, "launch_values", caller, lambda: setattr(s, "_cli_launch", dict(s._cli_launch)))

    def _launch_differs(self, s, _caller):
        s.effort = "low" if s.effort != "low" else "high"            # no mark: the launch now differs from the record

    def _kept_cases(self):
        return {
            "the pending-bypass mark is up": self._set(_mode_pending="bypassPermissions"),
            "the ask was not the pick's alone": self._set(_mode_ask_alone=False),
            "the effort mark is up": self._set(_effort_pending="low"),
            "the billing mark is up": self._set(_auth_pending="key"),
            "the fast-mode mark is up": self._set(_fast_pending="on"),
            "an ask of another reason stands": self._set(_switch_wanted="retry upgrade"),
            "a relaunch waits for the turn's end": self._set(_reconnect_when_idle=True),
            "a launch now differs from the record": self._launch_differs,
            "the record was replaced by an equal one": self._record_replaced_by_an_equal_one,
            "a reason is set aside": self._set(_switch_riders={"retry upgrade"}),
        }

    def test_the_hand_back_withdraws_only_when_every_term_holds(self):
        self.assertEqual(self._at_the_hand_back(self._set()), "", "the base: withdrawn")
        self.assertEqual(self._at_the_hand_back(self._set(_switch_wanted="bypass mode")), "",
                         "a standing ask of the same reason goes with it")
        self.assertEqual(self._at_the_hand_back(self._set(_reconnect=True, _reconnect_switch_why="",
                                                          _reattach_keeps_cli=True)), "",
                         "a reattach to the same CLI that no switch armed is not on its way")
        cases = dict(self._kept_cases(), **{
            "the reason handed back is not the pick's": self._set(_switch_ask_pending="session env"),
            "a relaunch armed is no reattach": self._set(_reconnect=True, _reconnect_switch_why="",
                                                         _reattach_keeps_cli=False),
            "a switch armed the relaunch beside a reattach": self._set(_reconnect=True,
                                                                       _reconnect_switch_why="always fast",
                                                                       _reattach_keeps_cli=True),
        })
        self.assertEqual({k for k, change in cases.items() if self._at_the_hand_back(change) == ""}, set(),
                         "each term that does not hold keeps an ask")

    def test_the_last_look_withdraws_only_when_every_term_holds(self):
        self.assertEqual(self._at_the_last_look(self._set()), "", "the base: withdrawn")
        self.assertEqual(self._at_the_last_look(self._set(_switch_wanted="bypass mode")), "",
                         "a standing ask of the same reason goes with it")
        cases = dict(self._kept_cases(), **{
            "the reason armed is not the pick's": self._set(_reconnect_switch_why="session env"),
            "an ask is carried": self._set(_switch_ask_pending="retry upgrade"),
        })
        self.assertEqual({k for k, change in cases.items() if self._at_the_last_look(change) == ""}, set(),
                         "each term that does not hold keeps an ask")


class SetModesWordBesideAnotherPicksAsk(_ABypassPickChangedBack):
    """set_mode marks a bypass pick's ask its alone only when no ask stands or is carried other than one that was
    already the pick's alone (SdkBackend.set_mode): a bypass ask merged with a billing pick's keeps the word down
    through the carry, a default pick and a second bypass pick, and the relaunch is kept."""

    def _merged_with_a_billing_pick(self, q):
        s = self._session(queued=q, auth="login")
        s.inflight = 1
        self.assertTrue(self.be.set_auth(SID, "key"))
        self._run(q)
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self._run(q)
        self.assertEqual((s._switch_wanted, s._mode_ask_alone), ("bypass mode", False),
                         "the premise: not the pick's alone")
        self.assertTrue(self.be.set_auth(SID, "login"))
        self._run(q)
        self.assertEqual(s._auth_pending, "", "the premise: the billing pick is back to what the CLI runs")
        return s

    def test_a_carried_ask_that_was_not_the_picks_alone_keeps_the_word_down(self):
        q = []
        s = self._merged_with_a_billing_pick(q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried")
        self.assertEqual(s._switch_ask_pending, "bypass mode", "the premise: carried, not armed")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertFalse(s._mode_ask_alone, "the carried ask was not the pick's alone: the new pick's word stays down")
        self.assertTrue(self.be.set_mode(SID, "default"))
        s._cli_working = True
        self._run(q)
        self.assertEqual(s._switch_wanted, "bypass mode", "an ask that was not the pick's alone keeps the relaunch")

    def test_a_standing_ask_that_was_not_the_picks_alone_keeps_the_word_down(self):
        q = []
        s = self._merged_with_a_billing_pick(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "bypass mode", "the premise: not the pick's alone, the ask stands")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertFalse(s._mode_ask_alone, "the standing ask was not the pick's alone: the new pick's word stays down")
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(s._switch_wanted, "bypass mode", "an ask that was not the pick's alone keeps the relaunch")

    def test_a_bypass_pick_at_the_default_picks_read_of_the_mark_keeps_its_mark(self):
        """set_mode reads and lowers the pending-bypass mark in one hold: a bypass pick made at the default pick's read
        of the mark keeps its own mark and ask."""
        s = self._session()
        self._bypass_waits(s)
        lock = s._lock = _AHoldEndSessionLock(s._lock)
        made = []

        def on_read(who, value):
            if not made and who == "set_mode" and value:
                made.append(1)
                lock.call_free(lambda: self.assertTrue(self.be.set_mode(SID, "bypassPermissions")))
        _hooked_field(s, "_mode_pending", on_read=on_read)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(made, [1], "the premise: Bypass was picked at the default pick's read of the mark")
        self.assertEqual((s.mode, s._mode_pending, s._switch_wanted),
                         ("bypassPermissions", "bypassPermissions", "bypass mode"),
                         "the later pick waits with its mark and its ask")


class TheArmDropsAPicksReasonInsteadOfSettingItAside(_ABypassPickChangedBack):
    """The arm of a relaunch (SdkSession._do_request_reconnect) sets aside only a reason other than a pick's: a pick's
    ask standing at the arm is answered by the relaunch, so once every pick is back to what the CLI runs the last look
    withdraws it."""

    def test_an_env_pick_at_the_arm_then_set_back(self):
        q = []
        s = self._session(queued=q)
        self._bypass_waits(s, q)
        s.inflight, s._cli_working = 0, False
        self.assertTrue(s._try_switch_reconnect(), "the premise: the ask is carried; its arm waits on the loop")
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_FLAG": "1"}))
        self.assertEqual(s._switch_wanted, "session env", "the premise: the env pick's ask stands at the arm")
        self._run(q)
        self.assertTrue(s._reconnect, "the premise: armed")
        self.assertEqual(s._switch_riders, set(), "the env pick's reason is not set aside")
        self.assertTrue(self.be.set_env(SID, {}))
        self._run(q)
        self.assertTrue(self.be.set_mode(SID, "default"))
        self._run(q)
        s.inflight = 1
        self.assertTrue(s._switch_teardown_check())
        self.assertEqual(s._switch_wanted, "", "every pick back to what the CLI runs: withdrawn at the last look")


class EveryWriteOfTheRelaunchAskIsMadeUnderTheSessionLock(unittest.TestCase):
    """Every write in kernel/ of the standing relaunch ask (_switch_wanted), the line said for it (_switch_wait_said), the
    ask carried (_switch_ask_pending), the turn's-end relaunch (_reconnect_when_idle), the bypass pick's word that the ask
    is its alone (_mode_ask_alone) and the reasons set aside (_switch_riders, and each call that changes it) is made inside
    a hold of that session's lock (`with <session>._lock:`, in the same function), the lock the change back's withdrawal
    holds where it reads and clears them. A session's construction is exempt: no other path reaches the session before
    it is built."""

    FIELDS = ("_switch_wanted", "_switch_wait_said", "_switch_ask_pending", "_reconnect_when_idle", "_mode_ask_alone",
              "_switch_riders")
    CHANGES = ("add", "update", "discard", "remove", "clear", "pop", "difference_update", "intersection_update",
               "symmetric_difference_update")
    EXEMPT = ("SdkSession.__init__",)

    def _writes(self):
        """(place, held) for every write of FIELDS in kernel/: held is whether `with <name>._lock:` encloses it in the same
        function, <name> being the object written to."""
        found = []

        def walk(node, qual, held, where):
            if isinstance(node, ast.ClassDef):
                for c in node.body:
                    walk(c, qual + node.name + ".", (), where)
                return
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for c in node.body:
                    walk(c, qual + node.name + ".", (), where)   # a body runs when called, under none of its maker's holds
                return
            if isinstance(node, ast.Lambda):
                walk(node.body, qual + "<lambda>.", (), where)
                return
            if isinstance(node, (ast.With, ast.AsyncWith)):
                inner = held + tuple(i.context_expr.value.id for i in node.items
                                     if isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock"
                                     and isinstance(i.context_expr.value, ast.Name))
                for i in node.items:
                    walk(i, qual, held, where)
                for c in node.body:
                    walk(c, qual, inner, where)
                return
            target = None
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)) and node.attr in self.FIELDS:
                target = node.value
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in self.CHANGES
                  and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_switch_riders"):
                target = node.func.value.value
            elif (isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("setattr", "delattr")
                  and len(node.args) > 1 and getattr(node.args[1], "value", None) in self.FIELDS):
                target = node.args[0]
            if target is not None and qual.rstrip(".") not in self.EXEMPT:
                name = getattr(target, "id", None)
                found.append(("%s:%d %s" % (where, node.lineno, qual.rstrip(".")), name is not None and name in held))
            for c in ast.iter_child_nodes(node):
                walk(c, qual, held, where)

        for path in sorted(Path(os.path.dirname(HERE), "kernel").glob("*.py")):
            text = path.read_text()
            if any(f in text for f in self.FIELDS):
                walk(ast.parse(text), "", (), path.name)
        return found

    def test_each_write_holds_the_session_lock(self):
        found = self._writes()
        self.assertGreater(len(found), 20, "the premise: the scan found the writes (%d)" % len(found))
        self.assertEqual([place for place, held in found if not held], [], "each write is made under the session lock")

    def test_what_each_write_acts_on_is_read_in_its_own_hold(self):
        """A hold that writes one of FIELDS acts on what that hold reads: no local it reads was last assigned inside another
        hold of the session lock in the same function, so a value read under the lock is never carried into a later hold
        and written there."""
        found = []

        def holds_of(fn):
            """[(first line, last line)] of every `with <name>._lock:` in fn's own body, nested defs left out."""
            out, stack = [], list(fn.body)
            while stack:
                n = stack.pop()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(n, (ast.With, ast.AsyncWith)) and any(
                        isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock" for i in n.items):
                    out.append(n)
                stack.extend(ast.iter_child_nodes(n))
            return out

        def own(nodes):
            out, stack = [], list(nodes)
            while stack:
                n = stack.pop()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                out.append(n)
                stack.extend(ast.iter_child_nodes(n))
            return out

        def writes(nodes):
            for n in nodes:
                if isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del)) and n.attr in self.FIELDS:
                    return True
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in self.CHANGES
                        and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_switch_riders"):
                    return True
            return False

        for path in sorted(Path(os.path.dirname(HERE), "kernel").glob("*.py")):
            text = path.read_text()
            if not any(f in text for f in self.FIELDS):
                continue
            for fn in ast.walk(ast.parse(text)):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                holds = holds_of(fn)
                if len(holds) < 2:
                    continue
                stores = [(n.lineno, n.id) for n in own(fn.body) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)]
                for h in holds:
                    body = own(h.body)
                    if not writes(body):
                        continue
                    for n in body:
                        if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
                            continue
                        before = [line for line, name in stores if name == n.id and line < n.lineno]
                        if not before:
                            continue
                        last = max(before)
                        inside = [o for o in holds if o is not h and o.lineno <= last <= o.end_lineno
                                  and not (h.lineno <= last <= h.end_lineno)]
                        if inside:
                            found.append("%s:%d %s reads %s, last assigned at %d in the hold at %d"
                                         % (path.name, n.lineno, fn.name, n.id, last, inside[0].lineno))
        self.assertEqual(sorted(set(found)), [], "each write's hold reads what it acts on")

    # What a hold that writes the ask decides on: the FIELDS, a relaunch armed, each pick's mark and the mode. The armed
    # relaunch's reason (_reconnect_switch_why) is left out: only the session's loop writes it (the arm, the relaunch
    # now, the loop top and the last look), and every step that reads it runs there.
    DECIDES = FIELDS + ("_reconnect", "_mode_pending", "_effort_pending", "_auth_pending", "_fast_pending", "mode")

    def test_a_value_read_before_its_hold_is_read_again_in_it(self):
        """A hold that writes one of FIELDS and acts on a local the function assigned outside every hold of the session
        lock, from a direct read of one of DECIDES (an attribute, or getattr with the field's name), reads that field
        again in its own body. Reads made through a call are not followed, before the hold or in it: a method the hold
        calls that reads the field does not count as the hold reading it, and a field reached through a call made before
        the hold (the launch, launch_values()) is not checked here; the tests that make a pick at a step's read of the
        launch and as its hold is entered pin that the withdrawal's rule takes the mode read in the hold."""
        found = []

        def own(nodes):
            out, stack = [], list(nodes)
            while stack:
                n = stack.pop()
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                    continue
                out.append(n)
                stack.extend(ast.iter_child_nodes(n))
            return out

        def writes(nodes):
            return any((isinstance(n, ast.Attribute) and isinstance(n.ctx, (ast.Store, ast.Del))
                        and n.attr in self.FIELDS)
                       or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in self.CHANGES
                           and isinstance(n.func.value, ast.Attribute) and n.func.value.attr == "_switch_riders")
                       for n in nodes)

        def reads(nodes):
            out = set()
            for n in nodes:
                if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Load) and n.attr in self.DECIDES:
                    out.add(n.attr)
                if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "getattr" and len(n.args) > 1 \
                        and getattr(n.args[1], "value", None) in self.DECIDES:
                    out.add(n.args[1].value)
            return out

        scanned = 0
        for path in sorted(Path(os.path.dirname(HERE), "kernel").glob("*.py")):
            text = path.read_text()
            if not any(f in text for f in self.FIELDS):
                continue
            tree = ast.parse(text)
            for c in ast.walk(tree):
                if not isinstance(c, ast.ClassDef):
                    continue
                for fn in [d for d in c.body if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                    nodes = own(fn.body)
                    holds = [n for n in nodes if isinstance(n, (ast.With, ast.AsyncWith)) and any(
                        isinstance(i.context_expr, ast.Attribute) and i.context_expr.attr == "_lock" for i in n.items)]
                    assigns = [n for n in nodes if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign))]
                    for h in holds:
                        body = own(h.body)
                        if not writes(body):
                            continue
                        scanned += 1
                        again = reads(body)
                        for n in body:
                            if not (isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)):
                                continue
                            src = [a for a in assigns if a.lineno < n.lineno and any(
                                isinstance(x, ast.Name) and x.id == n.id
                                for tg in (a.targets if isinstance(a, ast.Assign) else [a.target])
                                for x in ast.walk(tg))]
                            if not src:
                                continue
                            last = max(src, key=lambda a: a.lineno)
                            if last.value is None or any(o.lineno <= last.lineno <= o.end_lineno for o in holds):
                                continue
                            missing = reads(own([last.value])) - again
                            if missing:
                                found.append("%s:%d %s.%s acts on %s, read at %d before the hold at %d from %s, "
                                             "which the hold does not read again"
                                             % (path.name, n.lineno, c.name, fn.name, n.id, last.lineno, h.lineno,
                                                sorted(missing)))
        self.assertGreater(scanned, 20, "the premise: the scan found the holds that write the ask (%d)" % scanned)
        self.assertEqual(sorted(set(found)), [], "each hold decides on what it reads")


class _LockCensus:
    """The locks that can be taken while a lock is held, from kernel/*.py's source: every hold of the lock is followed call
    by call to its bottom, and so is every hold of each lock taken there, until nothing new is reached. NoKernelLockIsTaken
    WhileASessionLockIsHeld says how calls resolve and what the census leaves out."""

    STDLIB = frozenset(("time", "uuid", "asyncio", "os", "json", "threading", "re", "math", "hashlib", "datetime", "_dt",
                        "traceback", "types", "shutil", "subprocess", "signal", "errno", "difflib", "tempfile", "sys",
                        "collections", "itertools", "functools", "random", "struct", "base64", "zlib", "socket", "select",
                        "Path", "deque", "OrderedDict", "contextlib", "importlib", "gc", "io", "glob", "fcntl", "shlex",
                        "urllib", "html", "copy", "heapq", "bisect", "textwrap", "unicodedata", "string", "secrets",
                        "statistics", "operator", "platform", "logging", "queue", "weakref", "inspect", "pwd", "stat",
                        "ast", "csv", "codecs", "binascii", "dataclasses", "enum", "typing", "datetime", "date",
                        "timedelta", "timezone"))
    BACKEND = ("self.backend", "be", "backend", "self._backend")
    SESSION = ("s", "sess", "session", "ss")
    LOCK_TYPES = ("Lock", "RLock", "Condition", "Semaphore", "BoundedSemaphore")
    # A callable handed to one of these runs later, on a loop or a thread of its own, never inside the caller's hold.
    SCHEDULERS = frozenset(("call_soon_threadsafe", "call_soon", "call_later", "call_at", "create_task", "ensure_future",
                            "run_coroutine_threadsafe", "_schedule_on_loop", "Thread", "Timer", "submit",
                            "run_in_executor", "add_done_callback", "_call_on_loop"))
    # Bare names that are no def, each read at its source, keyed by (module, function, name): the parameter, local or
    # module handle each is, and what it runs.
    PARAMS = {
        # its parameter: _boot_attach_settled's `settled`, or a launch slot's release (_LaunchSlots.release, which hands
        # the slot over under _LaunchSlots._lock), at each of _ensure's callers
        ("sdk_backend", "SdkBackend._ensure", "on_boot_settled"): (
            ("nested", "sdk_backend", "SdkBackend._boot_attach_settled", "settled"),
            ("def", "sdk_backend", "_LaunchSlots.release")),
        ("sdk_backend", "SdkBackend._ensure", "adopt"): (("def", "sdk_backend", "SdkSession._adopt_queue_mirror"),),
        ("sdk_backend", "SdkBackend._boot_attach_settled", "release"): (),   # a launch slot's release (a Semaphore's)
        ("sdk_backend", "lease_state", "start"): (("def", "sdk_backend", "proc_start"),),   # proc_start unless a test's
        ("sdk_backend", "proc_start", "run"): (),                          # subprocess.run unless a test's
        ("sdk_backend", "_ht", "load_source"): (),                         # kernel/loadsource.py's module load
        ("host_transport", "host_lease_state", "load_source"): (),
        ("sdk_backend", "SdkSession._schedule_on_loop", "closed"): (),   # the loop's is_closed
    }
    # Receivers of library classes whose method shares a name with a method of the backend or a session, each read at its
    # source, keyed by (module, function, receiver): what each is.
    LIBRARY = {
        ("sdk_backend", "SdkBackend._lease_open", "t"): "a threading.Thread",
        ("sdk_backend", "SdkBackend._lease_watch_start", "th"): "a threading.Thread",
        ("sdk_backend", "SdkBackend._end_host_by_lease", "th"): "a threading.Thread",
        ("sdk_backend", "SdkSession.start", "self.thread"): "a threading.Thread",
    }
    MODCALLS = {("sdk_backend", "_ht()"): "host_transport", ("host_transport", "sb"): "sdk_backend"}
    SDK = ("_sdk", "_sdk_locked")   # kernel.py's first-use construction of the backend: a call to either is noted

    def __init__(self, kdir):
        import builtins
        self.builtins = set(dir(builtins))
        self.defs, self.nested, self.aliases, self.classes, self.constructed = {}, {}, {}, set(), set()
        self.props = {}                          # (class, name): {"@get": (module, qual), "@set": (module, qual)}
        takes, made = [], []                     # (module, function qual, function node, with/acquire node); instances
        for path in sorted(Path(kdir).glob("*.py")):
            m = path.stem
            tree = ast.parse(path.read_text())
            self.aliases[m] = al = {}
            self._scan(m, tree, takes)
            for n in tree.body:
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                    text = ast.unparse(n.value)
                    hit = re.search(r"HERE / ['\"](\w+)\.py['\"]", text)
                    if hit and "load_source" in text:
                        al[n.targets[0].id] = ("mod", hit.group(1))
                    elif isinstance(n.value, ast.Attribute) and isinstance(n.value.value, ast.Name):
                        al[n.targets[0].id] = ("attr", n.value.value.id, n.value.attr)
                    elif isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name):
                        made.append((m, n.targets[0].id, n.value.func.id))
            for name, a in list(al.items()):
                if a[0] == "attr" and a[1] in al and al[a[1]][0] == "mod":
                    al[name] = ("def", al[a[1]][1], a[2])
        self.prop_names = {name for _c, name in self.props}
        # a module name assigned an instance of one of the module's own classes: _PERF_STATS = _PerfStats()
        self.instances = {(m, name): c for m, name, c in made if (m, c) in self.classes}
        self.methods = {q.split(".", 1)[1] for (m, q) in self.defs if m == "sdk_backend"
                        and q.split(".", 1)[0] in ("SdkBackend", "SdkSession") and "." in q}
        self.callbacks = self._wiring()
        self.regions, self.unresolved_locks, self.unresolved_session_shaped, aliases = {}, set(), set(), {}
        for m, qual, fn, w in takes:
            if (m, qual) not in aliases:
                aliases[(m, qual)] = self._fn_aliases(m, qual, fn)
            items = [w.func.value] if isinstance(w, ast.Call) else [i.context_expr for i in w.items]
            for e in items:
                lid = self._lock_id(m, self._cls(m, qual), e, aliases[(m, qual)])
                if self._is_lock(e, lid):
                    if lid.startswith("?"):
                        self.unresolved_locks.add("%s:%d %s" % (m, w.lineno, ast.unparse(e)))
                        if re.search(r"(\._lock|['\"]_lock['\"])\b", lid):
                            self.unresolved_session_shaped.add("%s:%d %s (in %s)" % (m, w.lineno, lid, qual))
                    self.regions.setdefault(lid, []).append((m, qual, fn, w))
        self.unclassified, self._memo, self._info_memo = set(), {}, {}

    def _scan(self, m, tree, takes):
        """One walk of a module: its defs (methods as Class.name), the defs nested in each (keyed by the outermost def),
        every `with` and acquire() with the function it is in, and every lock it constructs (a module name or a class
        attribute assigned threading.Lock, RLock, Condition, Semaphore or BoundedSemaphore)."""
        def walk(node, cls, top, qual, fn):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, ast.ClassDef):
                    if fn is None:
                        self.classes.add((m, ch.name))
                        walk(ch, ch.name, None, None, None)
                    continue
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if fn is None:
                        q = (cls + "." if cls else "") + ch.name
                        self.defs[(m, q)] = ch
                        side = self._property_side(ch) if cls else None
                        if side:
                            # a property's getter and setter, each its own def (the setter shares the getter's name)
                            self.defs[(m, q + side)] = ch
                            self.props.setdefault((cls, ch.name), {})[side] = (m, q + side)
                        walk(ch, cls, q, q, ch)
                    else:
                        self.nested.setdefault((m, top, ch.name), ch)
                        walk(ch, cls, top, top + ".<n>." + ch.name, ch)
                    continue
                if isinstance(ch, ast.Assign) and isinstance(ch.value, ast.Call) and (
                        getattr(ch.value.func, "attr", None) in self.LOCK_TYPES
                        or getattr(ch.value.func, "id", None) in self.LOCK_TYPES):
                    for t in ch.targets:
                        if isinstance(t, ast.Name) and fn is None:
                            self.constructed.add("%s.%s" % (m, t.id))
                        elif isinstance(t, ast.Attribute) and ast.unparse(t.value) == "self" and cls:
                            self.constructed.add("%s.%s" % (cls, t.attr))
                if fn is not None and (isinstance(ch, (ast.With, ast.AsyncWith))
                                       or (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute)
                                           and ch.func.attr == "acquire")):
                    takes.append((m, qual, fn, ch))
                walk(ch, cls, top, qual, fn)
        walk(tree, None, None, None, None)

    @staticmethod
    def _property_side(fn):
        """"@get" for a def decorated @property (or functools.cached_property), "@set" for @<name>.setter, else None."""
        for d in fn.decorator_list:
            text = ast.unparse(d)
            if text in ("property", "cached_property", "functools.cached_property"):
                return "@get"
            if text.endswith(".setter"):
                return "@set"
        return None

    def _wiring(self):
        """The kernel's callbacks on the backend, read from kernel._sdk_locked: the constructor's arguments, through
        SdkBackend.__init__'s assignments of its parameters, and the attributes assigned on the backend or its type after
        it is built. {attr: ((module, def), ...)}"""
        init = self.defs[("sdk_backend", "SdkBackend.__init__")]
        params = [a.arg for a in init.args.args][1:] + [a.arg for a in init.args.kwonlyargs]
        attr_of = {}
        for st in ast.walk(init):
            if isinstance(st, ast.Assign) and isinstance(st.value, ast.Name) and st.value.id in params:
                for t in st.targets:
                    if isinstance(t, ast.Attribute) and ast.unparse(t.value) == "self":
                        attr_of[st.value.id] = t.attr
        wired = {}

        def targets(v):
            """The defs a wired value runs, or None for a callable reference (a name or an attribute) that resolves to
            none, which the census cannot follow (_resolve notes a call to it)."""
            out = []
            for ch in ast.walk(v.body if isinstance(v, ast.Lambda) else v):
                if isinstance(ch, ast.Name) and ("kernel", ch.id) in self.defs:
                    out.append(("kernel", ch.id))
                elif isinstance(ch, ast.Attribute) and isinstance(ch.value, ast.Name) and ch.value.id == "jd":
                    out.append(("judge", ch.attr))
                elif isinstance(ch, ast.Attribute) and isinstance(ch.value, ast.Name) \
                        and ("kernel", ch.value.id) in self.instances:
                    out.append(("kernel", self.instances[("kernel", ch.value.id)] + "." + ch.attr))   # _pusher_wake.set
            out = tuple(t for t in out if t in self.defs)
            return None if not out and isinstance(v, (ast.Name, ast.Attribute)) else out
        self.unwired = {t.attr for st in ast.walk(init) if isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant)
                        and st.value.value is None for t in st.targets
                        if isinstance(t, ast.Attribute) and ast.unparse(t.value) == "self"}
        made = self.defs[("kernel", "_sdk_locked")]
        for ch in ast.walk(made):
            if isinstance(ch, ast.Call) and isinstance(ch.func, ast.Attribute) and ch.func.attr == "SdkBackend":
                for p, v in list(zip(params, ch.args)) + [(k.arg, k.value) for k in ch.keywords]:
                    if p in attr_of:
                        wired[attr_of[p]] = targets(v)
            if isinstance(ch, ast.Assign):
                for t in ch.targets:
                    if isinstance(t, ast.Attribute) and ast.unparse(t.value) in ("_sdk_backend", "type(_sdk_backend)"):
                        v = ch.value.args[0] if (isinstance(ch.value, ast.Call) and ast.unparse(ch.value.func) == "staticmethod"
                                                 and ch.value.args) else ch.value
                        wired[t.attr] = targets(v)
        # a hook __init__ leaves at None that neither the kernel nor any line of sdk_backend.py sets runs nothing
        for (m, _q), fn in self.defs.items():
            if m != "sdk_backend" or fn is init:
                continue
            for st in ast.walk(fn):
                if isinstance(st, ast.Assign) and not (isinstance(st.value, ast.Constant) and st.value.value is None):
                    self.unwired -= {t.attr for t in st.targets if isinstance(t, ast.Attribute)}
        self.unwired -= set(wired)
        return wired

    @staticmethod
    def _lockish(e):
        t = ast.unparse(e)
        return bool(re.search(r"lock", t, re.I)) and "clock" not in t.lower()

    def _is_lock(self, e, lid):
        """Whether a `with` item or an acquire() receiver is a lock: its text says lock, it resolves to a lock the
        kernel constructs, or it is a local alias of an expression that says lock (g = s._lock; with g:)."""
        if self._lockish(e) or lid in self.constructed:
            return True
        tail = lid.split(":", 1)[-1].lower()
        return isinstance(e, ast.Name) and "lock" in tail and "clock" not in tail

    def _lock_id(self, m, cls, e, al):
        if isinstance(e, ast.BoolOp):
            for x in e.values:
                r = self._lock_id(m, cls, x, al)
                if not r.startswith("?"):
                    return r
            return "?" + ast.unparse(e)
        if isinstance(e, ast.Call) and getattr(e.func, "id", "") == "getattr" and len(e.args) >= 2 \
                and isinstance(e.args[1], ast.Constant):
            return self._lock_id(m, cls, ast.Attribute(value=e.args[0], attr=e.args[1].value, ctx=ast.Load()), al)
        base = e.func if isinstance(e, ast.Call) else e
        if isinstance(base, ast.Name):
            return al.get(base.id) or "%s.%s" % (m, base.id)
        if isinstance(base, ast.Attribute):
            owner = self._owner(m, cls, base.value, al)
            if owner:
                return "%s.%s" % (owner, base.attr)
            a = self.aliases[m].get(ast.unparse(base.value))
            if a and a[0] == "mod":
                return "%s.%s" % (a[1], base.attr)
            if isinstance(base.value, ast.Name) and (m, base.value.id) in self.classes:
                return "%s.%s" % (base.value.id, base.attr)        # a class attribute: SdkSession._stream_fail_lock
        return "?%s:%s" % (m, ast.unparse(e))

    def _owner(self, m, cls, e, env):
        """The class an expression's value is an instance of, where the census can tell: self is its class; in
        sdk_backend.py the names a module gives the backend and a session (BACKEND, SESSION), and a session's `backend`;
        a local the function assigned from any of these (env, by name); getattr(x, "attr") as x.attr."""
        if isinstance(e, ast.Call) and getattr(e.func, "id", "") == "getattr" and len(e.args) >= 2 \
                and isinstance(e.args[1], ast.Constant):
            e = ast.Attribute(value=e.args[0], attr=e.args[1].value, ctx=ast.Load())
        text = ast.unparse(e)
        if isinstance(e, ast.Name):
            if e.id == "self":
                return cls
            if isinstance(env.get(e.id), tuple) and env[e.id][0] == "owner":
                return env[e.id][1]
            if (m, e.id) in self.instances and e.id not in env:
                return self.instances[(m, e.id)]
        if m != "sdk_backend":
            return None
        if text in self.BACKEND:
            return "SdkBackend"
        if isinstance(e, ast.Name) and e.id in self.SESSION:
            return "SdkSession"
        if isinstance(e, ast.Attribute) and e.attr in ("backend", "_backend") \
                and self._owner(m, cls, e.value, env) == "SdkSession":
            return "SdkBackend"
        return None

    def _cls(self, m, qual):
        head = qual.split(".<n>.")[0]
        return head.split(".")[0] if "." in head and (m, head.split(".")[0]) in self.classes else None

    def _own(self, body):
        """The nodes of `body`, nested defs and classes left out (a nested def runs when it is called)."""
        out, stack = [], list(body)
        while stack:
            n = stack.pop()
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            out.append(n)
            stack.extend(ast.iter_child_nodes(n))
        return out

    def _fn_aliases(self, m, qual, fn):
        """The function's local names: for a lock (lock = getattr(self, "_lock", None), or g = _SESSIONS_LISTING_FLIGHT, a
        name or attribute the kernel constructs a lock in), and for an object whose class the census can tell
        (b = self.backend)."""
        memo = self.__dict__.setdefault("_alias_memo", {})
        if (m, qual, id(fn)) not in memo:
            memo[(m, qual, id(fn))] = self._fn_aliases_read(m, qual, fn)
        return memo[(m, qual, id(fn))]

    def _fn_aliases_read(self, m, qual, fn):
        cls, al = self._cls(m, qual), {}
        for ch in sorted(self._own(fn.body if isinstance(fn.body, list) else [fn.body]),
                         key=lambda n: getattr(n, "lineno", 0)):
            if isinstance(ch, ast.Assign) and len(ch.targets) == 1 and isinstance(ch.targets[0], ast.Name):
                if self._lockish(ch.value):
                    al[ch.targets[0].id] = self._lock_id(m, cls, ch.value, {})
                    continue
                lid = self._lock_id(m, cls, ch.value, al)
                if lid in self.constructed:
                    al[ch.targets[0].id] = lid              # g = _SESSIONS_LISTING_FLIGHT: an alias whatever its name
                    continue
                owner = self._owner(m, cls, ch.value, al)
                if owner:
                    al[ch.targets[0].id] = ("owner", owner)
        return al

    def _items(self, n):
        return [n.func.value] if isinstance(n, ast.Call) else [i.context_expr for i in n.items]

    def _events(self, m, qual, fn, body=None):
        """(locks, calls) made by the function's own body, or by `body` within it. A call to kernel._sdk or
        kernel._sdk_locked is noted as the lock "kernel._sdk()"."""
        cls, al = self._cls(m, qual), self._fn_aliases(m, qual, fn)
        params = self._params(fn)
        locks, calls = [], []
        for n in self._own(body if body is not None else (fn.body if isinstance(fn.body, list) else [fn.body])):
            if isinstance(n, (ast.With, ast.AsyncWith)) or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                                              and n.func.attr == "acquire"):
                for e in self._items(n):
                    if isinstance(e, ast.Name) and e.id in params and e.id not in al:
                        # a parameter the census does not bind to its callers' arguments: an unknown lock
                        locks.append(("?param:%s:%d %s (in %s)" % (m, n.lineno, e.id, qual), n))
                        continue
                    lid = self._lock_id(m, cls, e, al)
                    if self._is_lock(e, lid):
                        locks.append((lid, n))
            if isinstance(n, ast.Call):
                calls.append(n)
                if m == "kernel" and getattr(n.func, "id", None) in self.SDK:
                    locks.append(("kernel._sdk()", n))
            elif isinstance(n, ast.Attribute) and n.attr in self.prop_names and isinstance(n.ctx, (ast.Load, ast.Store)):
                side = "@get" if isinstance(n.ctx, ast.Load) else "@set"
                owner = self._owner(m, cls, n.value, al)
                if owner and side in self.props.get((owner, n.attr), {}):
                    # a property's read or write runs its getter or setter: a call
                    calls.append(ast.Call(func=ast.Attribute(value=n.value, attr=n.attr + side, ctx=ast.Load()),
                                          args=[], keywords=[], lineno=n.lineno))
        return locks, calls

    @staticmethod
    def _params(fn):
        a = getattr(fn, "args", None)
        if a is None:
            return set()
        out = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} - {"self", "cls"}
        return out | {x.arg for x in (a.vararg, a.kwarg) if x is not None}

    def _acquired(self, fn, w):
        """The nodes a bare acquire() `w` holds its lock over: everything in the function's own body from the acquire's
        line to the first release() of the same lock after it, or to the function's end."""
        target = ast.unparse(w.func.value)
        end = max(getattr(n, "end_lineno", 0) or 0 for n in self._own(fn.body))
        for n in self._own(fn.body):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "release" \
                    and ast.unparse(n.func.value) == target and n.lineno > w.lineno:
                end = min(end, n.lineno)
        return [n for n in self._own(fn.body) if w.lineno <= getattr(n, "lineno", 0) <= end and n is not w]

    def _callees(self, m, qual, fn, call):
        """What a call runs now: what its function resolves to, plus each callable handed to it as an argument that
        resolves to a def or a callback (functools.partial, a key function, a callback handed on), unless the call is a
        SCHEDULERS one, whose callable runs later and outside the hold."""
        out = list(self._resolve(m, qual, fn, call))
        name = getattr(call.func, "attr", None) or getattr(call.func, "id", None)
        if name not in self.SCHEDULERS:
            for a in list(call.args) + [k.value for k in call.keywords]:
                if isinstance(a, (ast.Name, ast.Attribute, ast.IfExp, ast.BoolOp)) or (
                        isinstance(a, ast.Subscript) and isinstance(a.value, (ast.Dict, ast.List, ast.Tuple))):
                    out.extend(self._resolve(m, qual, fn, ast.Call(func=a, args=[], keywords=[], lineno=call.lineno),
                                             quiet=True))
        return out

    def _resolve(self, m, qual, fn, call, quiet=False):
        """((kind, ...), ...): ("def", module, qual), ("nested", module, top, name), or () for a leaf. A call shaped as a
        callback that neither the wiring nor PARAMS resolves, and a method of the backend or a session called on an object
        the census cannot tell the class of, are noted in self.unclassified."""
        f, cls = call.func, self._cls(m, qual)
        top = qual.split(".<n>.")[0]
        if isinstance(f, ast.Call) and getattr(f.func, "id", "") == "getattr" and len(f.args) >= 2 \
                and isinstance(f.args[1], ast.Constant):
            f = ast.Attribute(value=f.args[0], attr=f.args[1].value, ctx=ast.Load())   # getattr(x, "m"[, d])()
        if isinstance(f, ast.Call) and (getattr(f.func, "attr", None) == "partial" or getattr(f.func, "id", None) == "partial") \
                and f.args:
            return self._resolve(m, qual, fn, ast.Call(func=f.args[0], args=[], keywords=[], lineno=call.lineno), quiet)
        if isinstance(f, (ast.IfExp, ast.BoolOp)) or (isinstance(f, ast.Subscript)
                                                      and isinstance(f.value, (ast.Dict, ast.List, ast.Tuple))):
            # (a if c else b)(), (a or b)(), {"k": a, ...}[k]() and [a, b][i](): each callable it can pick counts
            picks = ([f.body, f.orelse] if isinstance(f, ast.IfExp) else f.values if isinstance(f, ast.BoolOp)
                     else f.value.values if isinstance(f.value, ast.Dict) else f.value.elts)
            out = []
            for p_ in picks:
                if p_ is not None and not isinstance(p_, ast.Constant):
                    out.extend(self._resolve(m, qual, fn, ast.Call(func=p_, args=[], keywords=[], lineno=call.lineno),
                                             quiet))
            return tuple(out)
        if isinstance(f, ast.Name):
            if (m, top, f.id) in self.nested:
                return (("nested", m, top, f.id),)
            if (m, f.id) in self.defs:
                return (("def", m, f.id),)
            a = self.aliases[m].get(f.id)
            if a and a[0] == "def" and (a[1], a[2]) in self.defs:
                return (("def", a[1], a[2]),)
            if (m, f.id) in self.classes:
                return (("def", m, f.id + ".__init__"),) if (m, f.id + ".__init__") in self.defs else ()
            if (m, top, f.id) in self.PARAMS:
                return self.PARAMS[(m, top, f.id)]
            if quiet or f.id in self.builtins or f.id in self.STDLIB:
                return ()
            self.unclassified.add("%s:%d %s (in %s)" % (m, call.lineno, f.id, qual))
            return ()
        if isinstance(f, ast.Lambda):
            return ()   # its body is part of the function's own nodes, so its calls are followed in place
        if not isinstance(f, ast.Attribute):
            if not quiet:
                self.unclassified.add("%s:%d %s, a callee of no shape the census reads (in %s)"
                                      % (m, call.lineno, ast.unparse(f), qual))
            return ()
        v, attr = ast.unparse(f.value), f.attr
        owner = self._owner(m, cls, f.value, self._fn_aliases(m, qual, fn) if fn is not None else {})
        if owner:
            if (m, owner + "." + attr) in self.defs:
                return (("def", m, owner + "." + attr),)
            if owner == "SdkBackend" and attr in self.callbacks:
                if self.callbacks[attr] is None:
                    if not quiet:
                        self.unclassified.add("%s:%d %s.%s, a callback wired to nothing the census can follow (in %s)"
                                              % (m, call.lineno, v, attr, qual))
                    return ()
                return tuple(("def",) + t for t in self.callbacks[attr])
            if owner == "SdkBackend" and attr in self.unwired:
                return ()
            if not quiet and owner in ("SdkBackend", "SdkSession") and m == "sdk_backend" and not hasattr(set, attr) \
                    and not hasattr(dict, attr) and not hasattr(list, attr):
                self.unclassified.add("%s:%d %s.%s (in %s)" % (m, call.lineno, v, attr, qual))
            return ()
        a = self.aliases[m].get(v)
        if a and a[0] == "mod":
            return (("def", a[1], attr),) if (a[1], attr) in self.defs else ()
        if (m, v) in self.MODCALLS:
            return (("def", self.MODCALLS[(m, v)], attr),) if (self.MODCALLS[(m, v)], attr) in self.defs else ()
        if isinstance(f.value, ast.Name) and (m, f.value.id) in self.classes and (m, f.value.id + "." + attr) in self.defs:
            return (("def", m, f.value.id + "." + attr),)
        if isinstance(f.value, ast.Name) and (m, f.value.id) in self.instances:
            c = self.instances[(m, f.value.id)]
            return (("def", m, c + "." + attr),) if (m, c + "." + attr) in self.defs else ()   # else a library base's
        if not quiet and m == "sdk_backend" and (attr in self.methods or attr in self.callbacks) and not any(
                hasattr(t, attr) for t in (set, dict, list, str, bytes, tuple)) and (m, top, v) not in self.LIBRARY:
            self.unclassified.add("%s:%d %s.%s on an object of unknown class (in %s)" % (m, call.lineno, v, attr, qual))
        return ()

    def _node(self, r):
        if r[0] == "def":
            return r[1], r[2], self.defs.get((r[1], r[2]))
        return r[1], r[2] + ".<n>." + r[3], self.nested.get((r[1], r[2], r[3]))

    def _info(self, key, fn):
        """(locks, edges) of one def: [(lock, line)] its own body takes, and [(callee key, callee node, line)] for each
        call it makes. Read once per def."""
        hit = self._info_memo.get(key)
        if hit is None:
            m, qual = key
            hit = ([], [])
            if fn is not None:
                locks, calls = self._events(m, qual, fn)
                edges = []
                for c in calls:
                    for r in self._callees(m, qual, fn, c):
                        m2, q2, fn2 = self._node(r)
                        edges.append(((m2, q2), fn2, c.lineno))
                hit = ([(lid, w.lineno) for lid, w in locks], edges)
            self._info_memo[key] = hit
        return hit

    def _reach(self, m, qual, fn):
        """{lock: chain}: what calling (m, qual) can take, directly or through the calls it makes. The call graph from it
        is cut into its strongly connected parts (Tarjan's walk, iterative); each part is solved once, to a fixpoint,
        after every part it calls, and every def's result is kept, so a cycle is walked once whoever calls into it."""
        key = (m, qual)
        if key in self._memo:
            return self._memo[key]
        nodes, index, low, stack, on = {key: fn}, {key: 0}, {key: 0}, [key], {key}
        work = [(key, iter(self._info(key, fn)[1]))]
        while work:
            v, it = work[-1]
            pushed = False
            for w, fn_w, _line in it:
                if w in self._memo:
                    continue
                if w not in index:
                    nodes[w], index[w], low[w] = fn_w, len(index), len(index)
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(self._info(w, fn_w)[1])))
                    pushed = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if pushed:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                part = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    part.append(w)
                    if w == v:
                        break
                self._solve(part, nodes)
        return self._memo[key]

    def _solve(self, part, nodes):
        """Every def of one strongly connected part: its own locks, then what each call out of the part takes, then
        what each call inside it takes, until nothing new is added. A chain is fixed when its lock is first added."""
        inside, out = set(part), {}
        for v in part:
            locks, edges = self._info(v, nodes[v])
            o = {}
            for lid, line in locks:
                o.setdefault(lid, ["%s:%d %s" % (v[0], line, v[1])])
            for w, _fn, line in edges:
                if w not in inside:
                    for lid, chain in self._memo[w].items():
                        o.setdefault(lid, ["%s:%d %s" % (v[0], line, v[1])] + chain)
            out[v] = o
        grew = len(part) > 1 or any(w in inside for w, _f, _l in self._info(part[0], nodes[part[0]])[1])
        while grew:
            grew = False
            for v in part:
                for w, _fn, line in self._info(v, nodes[v])[1]:
                    if w in inside:
                        for lid, chain in out[w].items():
                            if lid not in out[v]:
                                out[v][lid] = ["%s:%d %s" % (v[0], line, v[1])] + chain
                                grew = True
        for v in part:
            self._memo[v] = out[v]

    def taken_per_hold(self, held):
        """[{lock: chain}], one per hold of `held` (each `with`, and each acquire() followed to its release): every lock
        that hold takes, directly or through the calls it makes, with the first chain found from it to the take."""
        out = []
        for m, qual, fn, w in self.regions.get(held, ()):
            taken = {}
            body = self._acquired(fn, w) if isinstance(w, ast.Call) else w.body
            locks, calls = self._events(m, qual, fn, body=body)
            for lid, _w in locks:
                taken.setdefault(lid, ["%s:%d %s (holds %s)" % (m, w.lineno, qual, held)])
            for c in calls:
                for r in self._callees(m, qual, fn, c):
                    m2, q2, fn2 = self._node(r)
                    for lid, chain in self._reach(m2, q2, fn2).items():
                        taken.setdefault(lid, ["%s:%d %s (holds %s)" % (m, c.lineno, qual, held)] + chain)
            out.append(taken)
        return out

    def taken_in(self, held):
        """{lock: chain} for every lock a hold of `held` takes, directly or through the calls it makes, its own
        acquire() regions included (each followed to its release), with the chain of the first hold that takes it."""
        taken = {}
        for per in self.taken_per_hold(held):
            for lid, chain in per.items():
                taken.setdefault(lid, chain)
        return taken

    def closure(self, start):
        """{lock: the chain from a hold of `start` to its take} for every lock reachable while `start` is held, the
        first-use construction "kernel._sdk()" included. `start` itself is left out: retakes keeps, for each lock reached
        (and `start` itself), one chain per hold of it that takes `start`."""
        done, todo, found = set(), [start], {}
        self.retakes = {}
        while todo:
            held = todo.pop()
            if held in done:
                continue
            done.add(held)
            per_hold = self.taken_per_hold(held)
            taken = {}
            for per in per_hold:
                if start in per:
                    self.retakes.setdefault(held, []).append(per[start])
                for lid, chain in per.items():
                    taken.setdefault(lid, chain)
            for lid, chain in taken.items():
                if lid != start and lid not in found:
                    found[lid] = chain
                    todo.append(lid)
        return found

    def held_around_sdk(self):
        """{lock: chain} for every lock some hold of which makes a call that can reach kernel._sdk or kernel._sdk_locked:
        the locks that can be held when a first _sdk() call takes a session lock. From the call graph of every def and
        nested def in kernel/, searched back from those two."""
        nodes = [(("def",) + k, k[0], k[1], fn) for k, fn in self.defs.items()]
        nodes += [(("nested",) + k, k[0], k[1] + ".<n>." + k[2], fn) for k, fn in self.nested.items()]
        callers = {}
        for key, m, qual, fn in nodes:
            for c in self._events(m, qual, fn)[1]:
                for r in self._callees(m, qual, fn, c):
                    callers.setdefault(self._node(r)[:2], []).append(((m, qual), c.lineno))
        toward, todo = {("kernel", t): None for t in self.SDK}, [("kernel", t) for t in self.SDK]
        while todo:
            f = todo.pop(0)
            for g, line in callers.get(f, ()):
                if g not in toward:
                    toward[g] = (f, line)
                    todo.append(g)

        def chain(f):
            out = []
            while toward.get(f):
                nxt, line = toward[f]
                out.append("%s:%d %s" % (f[0], line, f[1]))
                f = nxt
            return out + ["%s.%s" % f]
        held = {}
        for lid, regions in self.regions.items():
            for m, qual, fn, w in regions:
                body = self._acquired(fn, w) if isinstance(w, ast.Call) else w.body
                for c in self._events(m, qual, fn, body=body)[1]:
                    hits = [self._node(r)[:2] for r in self._callees(m, qual, fn, c)]
                    if m == "kernel" and getattr(c.func, "id", None) in self.SDK:
                        hits.append(("kernel", c.func.id))
                    for h in hits:
                        if h in toward and lid not in held:
                            held[lid] = ["%s:%d %s (holds %s)" % (m, c.lineno, qual, lid)] + chain(h)
        return held

    def unresolved_locks_in(self, module):
        return {u for u in self.unresolved_locks if u.startswith(module + ":")}


class NoKernelLockIsTakenWhileASessionLockIsHeld(unittest.TestCase):
    """While a session's lock (SdkSession._lock) is held, no path takes a kernel lock that can be held when a first _sdk()
    call takes a session lock. Under kernel._sdk_lock, _sdk_locked's _migrate_parked_auth calls set_auth, whose billing
    retarget asks for a relaunch through want_switch_reconnect, which reads and sets the ask under the session lock; so
    every lock held around a call that can reach _sdk() or _sdk_locked comes before the session lock, and none may come
    after it.

    Both sides are derived from kernel/*.py's source (_LockCensus). The locks held around such a call: every hold of every
    lock whose calls can reach _sdk() or _sdk_locked in the call graph of every def in kernel/ (held_around_sdk). The locks
    taken while a session lock is held: every hold of the session lock followed call by call to its bottom, then every
    hold of each lock taken there, anywhere in kernel/, until nothing new is reached (closure). The two must not meet, and
    the second may hold only the locks in TAKEN_UNDER_A_SESSION_LOCK, so any other lock taken under a session lock fails
    whether or not it is held around _sdk() today. HELD_AROUND_SDK names the locks the derivation must find, a premise
    that it reads the paths at all; it is not the list the test asserts on.

    A lock is any `with` item or acquire() receiver whose name says lock, that resolves to a name or a class attribute
    assigned threading.Lock, RLock, Condition, Semaphore or BoundedSemaphore, or that is a local alias of an expression
    that says lock or of such a name or attribute, whatever the alias is called (g = _SESSIONS_LISTING_FLIGHT; with g:);
    a lock taken by a bare acquire() is held from that line to the first release() of the same lock after it in the
    function, or to its end. A `with` or acquire() on a bare parameter of the function is not bound to what its callers
    pass: it counts as an unknown lock (?param:...), which fails the test wherever a session lock's hold reaches it.
    Calls resolve by receiver: self.m to its class's method; in sdk_backend.py
    the backend's and a session's methods by the names the module gives them (self.backend, be, backend; s, sess,
    session), by a session's `backend`, and by a local assigned from any of these; in any module, a module name assigned
    an instance of one of its classes (_PERF_STATS = _PerfStats()) to that class's method; getattr(x, "m"[, default])()
    as x.m(); a bare name to a nested def of its function, then to its module's def, then to another module's def
    through an alias; and (a if c else b)(), (a or b)(), {"k": a}[k]() and [a, b][i]() to every callable they can pick.
    functools.partial(f, ...) and every def or callback handed to a call as an argument, in any of those shapes, count
    as called, unless the call schedules it to run later (_LockCensus.SCHEDULERS). The kernel's callbacks on the backend
    resolve through the wiring kernel._sdk_locked makes, a bound method of a module's instance included
    (push=_pusher_wake.set). A read of a property (@property, or functools.cached_property) on a receiver whose class the
    census can tell counts as a call to its getter, and a write to it as a call to its setter. The call graph is solved
    by its strongly connected parts, each once, so a failing run takes about as long as a passing one.

    It fails loudly where it cannot read: a call shaped as a callback (an attribute of the backend or a session that is
    no method, or a bare name that is no def) that resolves through neither the wiring nor _LockCensus.PARAMS; a
    callback the kernel wires to a name or an attribute that resolves to no def; a callee of any other shape (a call's
    result, a subscript of anything but a literal); a method name of the backend or a session called in sdk_backend.py
    on an object of no class the census can tell, unless _LockCensus.LIBRARY names it; a lock taken in sdk_backend.py
    whose owner it cannot tell; and a take of a lock named _lock, in any module of kernel/, whose owner it cannot tell
    (a session's lock held outside sdk_backend.py would otherwise be a hold it never reads). Calls on other objects
    (files, events, threads, regex matches) end the path: the census's stated limits.

    Two more bounds: a session lock's hold never takes a session lock (the lock is not reentrant), and the registry lock
    (SdkBackend._reg_lock) is never held when a session lock is taken. One pair in both orders is accepted and named in
    RETAKES: SdkBackend._ensure holds SdkBackend._lock and takes the lock of the session it has just built
    (_adopt_queue_mirror) before that session's thread exists, while a session's own loop thread takes SdkBackend._lock
    under its own lock (drain_holding); the two never meet on one session's lock. Every hold of each lock reached is read
    for a take of a session lock, with the first chain found from that hold, so a hold of SdkBackend._lock in any
    function other than SdkBackend._ensure, or one whose chain takes a session lock in any function other than
    _adopt_queue_mirror, fails; a hold whose calls reach a session lock by two paths shows only the first."""

    HELD_AROUND_SDK = ("_sdk_lock", "_FEED_BUILD_LOCK", "_SEND_WRITE_LOCK", "_SETTINGS_LOCK", "_TL_BUILD_LOCK",
                       "_goals_snap_lock", "_pure_feed_lock", "_SESSIONS_LISTING_FLIGHT", "_client_lock",
                       "_AUTO_NUDGE_TICK_LOCK")
    TAKEN_UNDER_A_SESSION_LOCK = frozenset((
        "SdkBackend._lock", "SdkBackend._problem_lock", "SdkBackend._reg_lock", "SdkBackend._boot_attach_lock",
        "_LaunchSlots._lock", "_LaunchSlots._sem", "sdk_backend._HT_LOCK", "sdk_backend._LEASE_EPISODE_LOCK",
        "kernel._BOOT_MARKS_LOCK"))
    RETAKES = {"SdkBackend._lock": [("SdkBackend._ensure", "SdkSession._adopt_queue_mirror")]}

    def test_no_session_lock_hold_reaches_a_kernel_lock_held_around_a_first_sdk_call(self):
        self.maxDiff = None
        census = _LockCensus(Path(os.path.dirname(HERE), "kernel"))
        self.assertGreater(len(census.regions.get("SdkSession._lock", ())), 60,
                           "the premise: the census found the session lock's holds")
        self.assertIn(("kernel", "_mark_boot"), census.callbacks.get("_boot_phase", ()),
                      "the premise: the census read the kernel's wiring of its callbacks")
        self.assertIn(("kernel", "_CountedEvent.set"), census.callbacks.get("_push_cb") or (),
                      "the premise: the census reads a bound method of a module's instance in the wiring")
        found = census.closure("SdkSession._lock")
        retakes, unclassified = dict(census.retakes), sorted(census.unclassified)
        self.assertIn("SdkBackend._lock", found, "the premise: the census follows calls (drain_holding)")
        self.assertIn("kernel._BOOT_MARKS_LOCK", found,
                      "the premise: the census follows the kernel's callbacks on the backend (_boot_phase, _mark_boot)")
        held = census.held_around_sdk()
        self.assertEqual([n for n in self.HELD_AROUND_SDK if "kernel." + n not in held], [],
                         "the premise: the derivation finds the locks held around a first _sdk() call: %r" % (sorted(held),))
        self.assertEqual({lid: (found[lid], held[lid]) for lid in sorted(set(found) & set(held))}, {},
                         "no lock held around a first _sdk() call is taken while a session lock is held")
        self.assertEqual({lid: found[lid] for lid in sorted(found) if lid not in self.TAKEN_UNDER_A_SESSION_LOCK}, {},
                         "no other lock is taken while a session lock is held")
        self.assertEqual({held_: sorted({(chain[0].split(" ")[1], chain[-1].split(" ")[1]) for chain in chains})
                          for held_, chains in retakes.items()},
                         self.RETAKES, "a session lock is taken again only under the accepted pair, from every hold that "
                                       "takes one, never under its own hold: %r" % (retakes,))
        self.assertNotIn("SdkSession._lock", census.closure("SdkBackend._reg_lock"),
                         "no session lock is taken while the registry lock is held")
        self.assertEqual(sorted(census.unresolved_locks_in("sdk_backend")), [],
                         "every lock taken in sdk_backend.py resolves to its owner")
        self.assertEqual(sorted(census.unresolved_session_shaped), [],
                         "every take of a lock named _lock, in every module of kernel/, resolves to its owner")
        self.assertEqual(unclassified, [], "every call reached while a session lock is held is classified")


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
