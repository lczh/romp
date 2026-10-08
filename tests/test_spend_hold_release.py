#!/usr/bin/env python3
"""The spend-cap hold on a session's input lets go on its own events (2026-10-07).

The deadlock this closes: during an organisation's monthly spend cap, the account hold (_limit_hold) held a session's
input, and two of its arms had no release a held session could reach.
  * The TRANSCRIPT arm (the session's own last record is a cap error) released only on a new prompt reaching the
    transcript, which is exactly what the hold withholds: a session whose last record was a cap error stayed frozen
    after the global pause lifted, and whatever account it had been switched to.
  * The GLOBAL pause held every session, though it records which billing the cap was on (`bills`) and its own lift
    already ignores sessions on the other one.

The rules pinned here:
  * a session's own cap record holds only while it is news: newer than the pause's last lift (`liftedAt`, the rule the
    engage already used) and newer than the moment the session's CLI came up on a new billing (`authSwitchedAt`); a cap
    error the new account answers with is newer, and holds again (_spend_record_stands, shared by the engage);
  * the global pause holds only the sessions on the billing it recorded, through the ONE helper its lift reads
    (_on_paused_billing);
  * the landing is an EVENT, never the pick: the backend stamps authSwitchedAt where the reconcile finds a waiting
    billing pick carried by the CLI the session connected to (now for a launch made here, that CLI's spawn for one an
    earlier kernel made), and until then the CLI's own report (authLive) stands, so a session whose switch still waits
    reads as the account its running CLI serves it on. A session with no CLI running lands at the pick itself.

SYNTHETIC fixtures only: placeholder sids, invented texts; every state root this module mints writes session-hosts off.
"""
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_spendhold", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_spendhold", os.path.join(BIN, "romp_sdk_backend.py"))
km._PROMPT_HOLD_S = 0.0
jd = km.jd

SID = "11111111-2222-3333-4444-5555aaaa0701"        # this module's own synthetic sids
OTHER = "11111111-2222-3333-4444-5555aaaa0702"
CAP = "This organization has reached its monthly spend limit."   # invented wording the cap check reads as a cap


class _FakeBackend:
    def __init__(self):
        self.calls = []

    def forwards_sends(self):
        return True

    def send(self, sid, text):
        self.calls.append(("send", sid, text))
        return True


class _Kernel(unittest.TestCase):
    """The kernel's hold over a stubbed world: one transcript record per sid, one live row per sid."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.saved_state = jd.STATE
        jd.STATE = Path(self.td.name)
        (jd.STATE / "session-hosts").write_text("off")
        (jd.STATE / "usage.json").write_text(json.dumps({"t": int(time.time()),
                                                         "five_hour": {"pct": 5, "resets_at": int(time.time()) + 3600},
                                                         "seven_day": {"pct": 5, "resets_at": int(time.time()) + 3600}}))
        names = ("_compacting_now", "_working_now", "_push_all", "_mark_views_dirty", "_path_of", "_api_error",
                 "_live_map", "_launch_error")
        self._saved = {n: getattr(km, n) for n in names}
        self._saved_backend = km.Sessions.backend_for
        self.be = _FakeBackend()
        self.records = {}                      # sid -> the session's last transcript error record, or None
        self.rows = {}                         # sid -> its live row
        km._compacting_now = lambda sid, *a, **k: False
        km._working_now = lambda sid: False
        km._push_all = lambda: None
        km._mark_views_dirty = lambda *a, **k: None
        km._launch_error = lambda sid: None
        km._path_of = lambda sid, *a: ("/synthetic/%s.jsonl" % sid) if sid in self.records else None
        km._api_error = lambda path: self.records.get(Path(path).stem)
        km._live_map = lambda: self.rows
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        km._pending_ops.clear()
        km._drain_hold.clear()

    def tearDown(self):
        for n, v in self._saved.items():
            setattr(km, n, v)
        km.Sessions.backend_for = self._saved_backend
        km._pending_ops.clear()
        km._drain_hold.clear()
        jd.STATE = self.saved_state
        self.td.cleanup()

    def _capped(self, sid, t, exact=None):
        """A cap record as _api_error reads one: `t` in the CLI's whole seconds, `tExact` to its millisecond (mid-second
        unless given)."""
        self.records[sid] = {"text": CAP, "spendLimit": True, "t": int(t), "tExact": float(t) + 0.5 if exact is None else exact,
                             "uuid": "11111111-2222-3333-4444-%012d" % int(t)}

    def _pause(self, bills=""):
        self.assertTrue(km._set_retry_paused(True, reason="spend", bills=bills))

    def _lift(self, at):
        self.assertTrue(km._set_retry_paused(False, lifted_at=at))


class AnOwnCapRecordHoldsWhileItIsNews(_Kernel):
    def test_a_record_older_than_the_pause_lift_no_longer_holds(self):
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        self._pause(bills="login")
        self._lift(2000)                                     # a login session served after the cap was raised
        self.assertIsNone(km._limit_hold(SID), "the lift outranked the record, as it does for the engage")

    def test_a_record_newer_than_the_lift_holds(self):
        self._pause(bills="login")
        self._lift(2000)
        self._capped(SID, 2500)                              # capped again after the lift: new information
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend")

    def test_a_record_older_than_the_sessions_landing_on_a_new_billing_no_longer_holds(self):
        self._capped(SID, 1000)                              # written by the login's CLI
        self.rows[SID] = {"authLive": "", "auth": "key", "authSwitchedAt": 1500.0}   # the key's CLI came up at 1500
        self.assertIsNone(km._limit_hold(SID), "the record describes an account the session left")

    def test_a_cap_error_the_new_account_answers_with_holds_again(self):
        self._capped(SID, 1600)                              # the key's CLI capped too, after the landing
        self.rows[SID] = {"authLive": "key", "auth": "key", "authSwitchedAt": 1500.0}
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend")

    def test_with_no_landing_and_no_lift_the_record_holds_as_before(self):
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend")

    def test_the_engage_skips_a_record_from_the_account_the_session_left(self):
        """The engage reads the same rule: a record the old account wrote must not put the pause back on, and must
        not record the billing the session has moved to as the capped one."""
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "", "auth": "key", "authSwitchedAt": 1500.0}
        km_alive = km._alive_sessions
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": "/synthetic/%s.jsonl" % SID}]
        try:
            self.assertIsNone(km._spend_capped_session(time.time(), self.rows))
            self.rows[SID]["authSwitchedAt"] = 900.0          # the record is newer than the landing: it stands
            self.assertEqual((km._spend_capped_session(time.time(), self.rows) or {}).get("sid"), SID)
        finally:
            km._alive_sessions = km_alive


class TheGlobalPauseHoldsItsOwnBillingSide(_Kernel):
    def test_a_session_on_the_other_billing_is_not_held(self):
        self._pause(bills="login")
        self.rows[SID] = {"authLive": "key", "auth": "key"}
        self.rows[OTHER] = {"authLive": "login", "auth": "login"}
        self.assertIsNone(km._limit_hold(SID), "the cap is on the login; the key serves")
        self.assertEqual((km._limit_hold(OTHER) or {}).get("reason"), "spend")

    def test_a_session_whose_switch_still_waits_reads_as_the_account_its_cli_serves(self):
        self._pause(bills="login")
        self.rows[SID] = {"authLive": "login", "auth": "key", "authPending": True}   # picked the key; the login's CLI runs
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend",
                         "until the relaunch lands, the capped account is the one serving it")

    def test_a_pause_that_recorded_no_billing_holds_everyone(self):
        self._pause(bills="")
        self.rows[SID] = {"authLive": "key", "auth": "key"}
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend")

    def test_the_lift_and_the_hold_read_one_billing_rule(self):
        """The pause's two readers agree because they ask one rule: a session the hold lets through is one whose output
        cannot lift the pause, and the other way round, whatever that rule says."""
        self._pause(bills="login")
        self.rows[SID] = {"authLive": "key", "auth": "key"}
        self.rows[OTHER] = {"authLive": "login", "auth": "login"}
        asked = []
        real = km._on_paused_billing
        km._on_paused_billing = lambda tm, bills: (asked.append((tm or {}).get("auth")), real(tm, bills))[1]
        saved = (km._alive_sessions, km._api_last_output_t)
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": "/synthetic/k"}, {"sid": OTHER, "path": "/synthetic/l"}]
        km._api_last_output_t = lambda path: time.time() + 60   # both sessions answered after the pause began
        try:
            self.assertIsNone(km._limit_hold(SID))
            self.assertIsNotNone(km._limit_hold(OTHER))
            self.assertEqual(asked, ["key", "login"], "the hold asked the rule for each session")
            asked.clear()
            km._auto_resume_retry(time.time(), self.rows)
            self.assertEqual(asked, ["key", "login"], "…and so did the lift: the key session's output was passed over")
            self.assertFalse(km._retry_paused_on(), "the login session's output lifted it")
        finally:
            km._on_paused_billing = real
            km._alive_sessions, km._api_last_output_t = saved

    def test_the_engage_and_the_hold_read_one_record_rule(self):
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "", "auth": "key", "authSwitchedAt": 1500.0}
        asked = []
        real = km._spend_record_stands
        km._spend_record_stands = lambda e, tm, lifted=0: (asked.append(e["t"]), real(e, tm, lifted))[1]
        saved = km._alive_sessions
        km._alive_sessions = lambda now, live: [{"sid": SID, "path": "/synthetic/%s.jsonl" % SID}]
        try:
            self.assertIsNone(km._limit_hold(SID))
            self.assertIsNone(km._spend_capped_session(time.time(), self.rows))
            self.assertEqual(asked, [1000, 1000], "the hold and the engage each asked the one rule about the record")
        finally:
            km._spend_record_stands = real
            km._alive_sessions = saved


class TheLandingIsOrderedExactly(_Kernel):
    """The landing is stamped by this kernel's clock to the sub-second; a cap record's `t` is the CLI's whole second, so a
    record in the landing's own second read as older. Each floor is now compared in its own precision."""

    def test_a_cap_error_in_the_landings_second_is_ordered_to_the_millisecond(self):
        self.rows[SID] = {"authLive": "key", "auth": "key", "authSwitchedAt": 1500.4}
        self._capped(SID, 1500, exact=1500.6)            # the new account's CLI capped 200 ms after the landing
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend", "newer than the landing: it holds")
        self._capped(SID, 1500, exact=1500.2)            # the old account's last word, 200 ms before it
        self.assertIsNone(km._limit_hold(SID))

    def test_the_lift_stays_in_the_clis_whole_seconds(self):
        self._pause(bills="login")
        self._lift(1500)                                 # the lifting output's own stamp: whole seconds
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        self._capped(SID, 1500, exact=1500.2)
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend", "a record of the lift's second reads as new")

    def test_a_transcripts_cap_record_carries_its_millisecond_stamp(self):
        path = jd.STATE / "web.jsonl"
        path.write_text(json.dumps({"type": "assistant", "isApiErrorMessage": True, "uuid": "11111111-2222-3333-4444-000000000001",
                                    "timestamp": "2026-10-07T12:00:00.600Z",
                                    "message": {"content": [{"type": "text", "text": CAP}]}}) + "\n")
        e = self._saved["_api_error"](str(path))
        self.assertTrue(e["spendLimit"])
        self.assertAlmostEqual(e["tExact"] - e["t"], 0.6, places=3)


class TheHoldReadsTheRowOnce(_Kernel):
    def test_a_pause_and_a_cap_record_together_cost_one_liveness_read(self):
        self._pause(bills="key")                          # not this session's side: the hold goes on to its own record
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        reads = []
        km._live_map = lambda: (reads.append(1), self.rows)[1]
        self.assertEqual((km._limit_hold(SID) or {}).get("reason"), "spend")
        self.assertEqual(len(reads), 1, "outside a cycle each read is every backend's rows")


class AHeldQueueDrains(_Kernel):
    def test_input_held_by_a_stale_own_record_goes_out_once_the_pause_lifted(self):
        """The reported freeze: the session's last record is a cap error from before the lift. Its queued input
        used to wait for a prompt the hold itself withheld."""
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "login", "auth": "login"}
        self._pause(bills="login")
        km._send_or_park(self.be, SID, "pick up the migration where it stopped", echo="human")
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "held while the pause holds the login")
        self._lift(2000)
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", SID, "pick up the migration where it stopped")])

    def test_input_held_behind_the_old_accounts_record_goes_out_once_the_switch_lands(self):
        self._capped(SID, 1000)
        self.rows[SID] = {"authLive": "login", "auth": "key", "authPending": True}
        km._send_or_park(self.be, SID, "carry on", echo="human")
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [], "the login's CLI still serves it")
        self.rows[SID] = {"authLive": "", "auth": "key", "authSwitchedAt": 1500.0}   # the relaunch landed
        km._apply_pending_ops()
        self.assertEqual(self.be.calls, [("send", SID, "carry on")])


class TheLiveMapCarriesTheLanding(_Kernel):
    def test_the_merge_copies_the_backends_stamp(self):
        km._live_map = self._saved["_live_map"]
        row = {"state": "waiting", "since": "", "auth": "key", "authSwitchedAt": 1500.0}
        fake = types.SimpleNamespace(live_sessions=lambda: {SID: dict(row)}, owns=lambda sid: sid == SID)
        saved_sdk, saved_cx = km._sdk, km._codex
        km._sdk, km._codex = (lambda: fake), (lambda: None)
        try:
            self.assertEqual(km.Sessions.live()[SID]["authSwitchedAt"], 1500.0)
        finally:
            km._sdk, km._codex = saved_sdk, saved_cx


# ── the backend's side: the stamp at the landing, the report kept until it ─────────────────────────────────────────

FAKE_KEY = "synthetic-helper-output-spendhold"


class _Backend(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")
        self.cfg = tempfile.mkdtemp()
        self._cfg_before = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = self.cfg
        script = Path(self.cfg) / "helper.sh"
        script.write_text("#!/bin/sh\necho '%s'\n" % FAKE_KEY)
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

    def _reg(self):
        return sb.read_reg(self.d, SID) or {}

    def _session(self, serving, **reg):
        base = {"sid": SID, "name": "web", "cwd": self.d, "mode": "acceptEdits", "alive": True, "auth": "login",
                "apiKeyAuth": False}
        base.update(reg)
        sb.write_reg(self.d, SID, base)
        s = sb.SdkSession(self.be, dict(self._reg()))
        if serving:
            s.thread = types.SimpleNamespace(is_alive=lambda: True, ident=None)   # a CLI runs (the loop is never started)
            s._cli_launch = s.launch_values()                                     # …launched as the session stands now
            s._served_billing = (s._cli_launch["auth"], s._cli_launch["authLogin"])
        self.be.sessions[SID] = s
        return s


class APickKeepsTheServingReport(_Backend):
    def test_a_pick_on_a_session_whose_cli_runs_keeps_its_report_until_the_switch_lands(self):
        s = self._session(serving=True)
        self.assertEqual(s.auth_live, "login")
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertEqual(s.auth_live, "login", "the login's CLI still serves the session")
        self.assertIs(self._reg().get("apiKeyAuth"), False, "…and its persisted report stands")
        self.assertEqual(s.snapshot()["authLive"], "login")
        self.assertFalse(self._reg().get("authSwitchedAt"), "the pick is not the landing")

    def test_a_pick_on_a_session_with_no_cli_lands_at_once(self):
        s = self._session(serving=False)
        before = time.time()
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertNotIsInstance(self._reg().get("apiKeyAuth"), bool, "nothing serves it: the old report goes")
        self.assertGreaterEqual(float(self._reg().get("authSwitchedAt") or 0), before,
                                "its next CLI launches on the pick: the old account's record no longer describes it")
        self.assertEqual(s.snapshot()["authSwitchedAt"], self._reg()["authSwitchedAt"])

    def test_a_pick_of_the_billing_it_already_has_lands_nothing(self):
        self._session(serving=False)
        self.assertTrue(self.be.set_auth(SID, "login"))
        self.assertFalse(self._reg().get("authSwitchedAt"), "no change of account, no release")


class TheReconcileStampsTheLanding(_Backend):
    def _waiting(self, serving=True):
        s = self._session(serving=serving)
        self.assertTrue(self.be.set_auth(SID, "key"))
        self.assertIn("billing", (self._reg().get("relaunchWait") or {}).get("picks") or {})
        return s

    def test_a_launch_made_here_carrying_the_pick_lands_it_now(self):
        s = self._waiting()
        before = time.time()
        s._reconcile_waiting_picks(s.launch_values(), True)
        reg = self._reg()
        self.assertFalse(reg.get("authPending"))
        self.assertGreaterEqual(float(reg.get("authSwitchedAt") or 0), before)
        self.assertEqual(s.auth_switched_at, reg["authSwitchedAt"])
        self.assertEqual(s.auth_live, "", "the new CLI has reported nothing yet: the side reads its launch's intent")
        self.assertNotIsInstance(reg.get("apiKeyAuth"), bool)
        self.assertEqual(s.snapshot()["authSwitchedAt"], reg["authSwitchedAt"])

    def test_an_attached_cli_an_earlier_kernel_launched_with_the_pick_landed_at_its_spawn(self):
        s = self._waiting()
        s._reconcile_waiting_picks(s.launch_values(), False, spawned_at=1700000200)
        self.assertEqual(float(self._reg().get("authSwitchedAt") or 0), 1700000200.0)
        self.assertEqual(s.auth_live, "", "the report the reg restored is the replaced CLI's: it goes at every landing")
        self.assertNotIsInstance(self._reg().get("apiKeyAuth"), bool)

    def test_a_cli_that_does_not_carry_the_pick_lands_nothing(self):
        s = self._waiting()
        launch = dict(s.launch_values(), auth="login")
        s._reconcile_waiting_picks(launch, False, spawned_at=1700000200)
        self.assertTrue(self._reg().get("authPending"))
        self.assertFalse(self._reg().get("authSwitchedAt"))
        self.assertEqual(s.auth_live, "login")

    def test_a_connect_with_no_switch_waiting_lands_nothing(self):
        s = self._session(serving=True)
        s._reconcile_waiting_picks(s.launch_values(), True)
        self.assertFalse(self._reg().get("authSwitchedAt"))
        self.assertEqual(s.auth_live, "login")


if __name__ == "__main__":
    unittest.main()
