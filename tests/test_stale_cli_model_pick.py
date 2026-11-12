#!/usr/bin/env python3
"""A model pick an OLD Claude Code does not know, while a newer one is installed (2026-11-12, the switch to Fable 5.2).

Every live session ran a Claude Code launched before the model existed (2.1.280, 2.1.305) while the installed binary was
2.1.322. A live set_model to claude-fable-5-2 was refused with "is not a recognized model id", and romp reverted the
pick and the remembered default, forgot the family's pinned version and rang the error center, once per session. Now
such a refusal KEEPS the pick and asks for a quiet relaunch (want_switch_reconnect, never cutting a turn), which
launches the installed Claude Code on the pick with the fast-mode flag; that CLI then rules on the pick, and a refusal
there reverts loudly as before. A refusal from a CLI that IS the installed version reverts as before too.

Synthetic sids, names and payloads; a temp state root with session hosts off; no CLI, no network."""
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_stalepick", os.path.join(BIN, "romp_sdk_backend.py"))

PICK = "claude-fable-5-2"
UNKNOWN = Exception('Model "claude-fable-5-2" is not a recognized model id. Run /model to see available models.')
# the API's refusal a Claude Code too old for the model passes on as its set_model answer (the rollout of 2026-11-12: a session
# on 2.1.280 picking claude-fable-5-2), in the shape the SDK raised it
TOO_OLD = Exception('API error: 400 {"type":"error","error":{"type":"invalid_request_error","message":"Claude Code 2.1.280 '
                    'does not support this model; version 2.1.290 or newer is required. Run \'claude update\', or …"}}')


class _Client:
    """The SDK client as _do_set_model and the context refresh use it: set_model answers or refuses, the context
    refresh reports the model the CLI runs; the control-request sender records what romp asks (the fast re-arm)."""

    def __init__(self, refuse=None, runs="claude-opus-5-5"):
        self.refuse, self.runs, self.asked = refuse, runs, []
        outer = self

        class _Q:
            async def _send_control_request(self, req):
                outer.asked.append(req)
                return {}
        self._query = _Q()

    async def set_model(self, model=None):
        self.asked.append(("set_model", model))
        if self.refuse is not None and model == PICK:   # it refuses the id it does not know, and only that one
            raise self.refuse
        self.runs = model

    async def get_context_usage(self):
        return {"percentage": 3, "model": self.runs}


class _Sys:
    def __init__(self, data):
        self.subtype = "init"
        self.data = data


class StaleCliModelPick(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        Path(self.d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
        self.be = sb.SdkBackend(self.d, "/bin/false", lambda *a, **k: None)
        self.logs = []
        self.be._log_cb = self.logs.append
        self.be.cli_version = lambda: "2.1.322"           # the installed Claude Code
        self.forgot = []
        self._hook = getattr(sb.SdkBackend, "on_model_refused", None)
        sb.SdkBackend.on_model_refused = staticmethod(lambda sid, picked: self.forgot.append(picked))
        # the CLI says Fable 5.2 runs fast mode (the learned table), so a fast session's launch on it carries the flag
        sb._mf.note(self.d, {PICK: True, "fable": True, "claude-opus-5-5": True}, cli="2.1.322")

    def tearDown(self):
        if self._hook is None:
            try:
                del sb.SdkBackend.on_model_refused
            except AttributeError:
                pass
        else:
            sb.SdkBackend.on_model_refused = self._hook

    def _problems(self):
        """The error center's rows, less the lifecycle frames' own notice, which any synthetic init draws."""
        return [p for p in self.be.problems() if not p["text"].startswith("lifecycle (")]

    def _live(self, cli_seen="2.1.305", refuse=UNKNOWN):
        sid = self.be.spawn("web", self.d)
        self.assertTrue(self.be.set_model(sid, "opus"))
        self.be._update_reg(sid, fast=True)                # the session's own Fast pick
        s = sb.SdkSession(self.be, sb.read_reg(self.d, sid))
        s.model, s._model_id = "Opus 5.5", "claude-opus-5-5"
        s._cli_version_seen = cli_seen
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        self.be.sessions[sid] = s
        s.client = _Client(refuse=refuse)
        self.scheduled = []
        s.set_model_live = lambda model, prev=None: self.scheduled.append((model, prev))
        return sid, s

    def _pick_refused(self, s, sid):
        self.assertTrue(self.be.set_model(sid, PICK))
        asyncio.run(s._do_set_model(*self.scheduled[-1]))

    def test_an_old_clis_refusal_keeps_the_pick_and_asks_a_quiet_relaunch_with_nothing_rung(self):
        sid, s = self._live()
        asks = []
        s.want_switch_reconnect = asks.append
        self._pick_refused(s, sid)
        self.assertEqual(asks, ["model"], "the quiet relaunch road (want_switch_reconnect), never an immediate one")
        self.assertEqual(s.chosen_model, PICK, "the pick is kept: the relaunch's --model")
        reg = sb.read_reg(self.d, sid)
        self.assertEqual(reg["model"], PICK)
        self.assertEqual(sb.read_sdk_defaults(self.d).get("model"), PICK, "the remembered default stands")
        self.assertEqual(self.forgot, [], "the family's remembered version is not forgotten")
        self.assertEqual(self._problems(), [], "nothing in the error center: the CLI is old, not the pick wrong")
        self.assertEqual(s._model_pending, PICK, "the pick's switching dots stand: pending, not lost")
        self.assertTrue(reg.get("modelPending"))
        self.assertEqual(reg.get("stalePick"), {"pick": PICK, "back": "opus"}, "kept across a kernel restart")
        said = [m for m in self.logs if "does not know" in m]
        self.assertEqual(len(said), 1, self.logs)
        self.assertIn("Claude Code 2.1.305 does not know claude-fable-5-2", said[0])
        self.assertIn("Claude Code 2.1.322 is installed", said[0])
        self.assertFalse(any("did NOT apply" in m for m in self.logs), self.logs)

    def test_the_relaunch_waits_for_a_working_sessions_turn_to_end(self):
        sid, s = self._live()
        relaunched = []
        s.request_reconnect = lambda defer=True, cut=False, why="", cause="": relaunched.append((defer, cut))
        s.inflight = 1                                     # a turn is running
        self._pick_refused(s, sid)
        self.assertEqual(s._switch_wanted, "model", "the ask stands")
        self.assertEqual(relaunched, [], "nothing is cut: the turn runs on")
        self.assertEqual(s.relaunch_waits(), "the running turn", "what the pending pick waits for, as the status says it")
        s.inflight = 0                                     # the turn's result
        self.assertTrue(s._try_switch_reconnect())
        self.assertEqual(relaunched, [(False, False)], "applied once quiet, on the no-cut road")

    def test_the_relaunch_lands_on_the_pick_with_fast_mode_on(self):
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        # what the relaunch's launch carries: --model is the session's pick, and the fast-mode flag rides it
        self.assertEqual(s.chosen_model, PICK)
        self.assertTrue(s.fast_effective())
        self.assertTrue(s.launch_values()["fast"])
        fs = sb.flag_settings_path(self.d, sid, fast=s.fast_effective())
        self.assertIn('"fastMode": true', Path(fs).read_text())
        # the relaunch's connect: a CLI launched here (no attach), the installed version, which knows the pick
        s._host_is_attach = False
        s.client = _Client(runs=PICK)
        s._fast_unlocked = True

        async def _noop():
            pass

        async def go():
            s.loop = asyncio.get_running_loop()
            self.assertTrue(s._send_stale_pick(), "the relaunch's CLI is asked to rule on the pick")
            for _ in range(8):
                await asyncio.sleep(0)
            s._do_refresh_context = _noop
            s._on_message(_Sys({"model": PICK, "fast_mode_state": "on", "claude_code_version": "2.1.322"}),
                          type("A", (), {}), type("R", (), {}), _Sys)
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertIn(("set_model", PICK), s.client.asked)
        self.assertIsNone(s._stale_pick)
        self.assertNotIn("stalePick", sb.read_reg(self.d, sid))
        self.assertEqual(s._model_accepted, PICK)
        self.assertEqual(s.model, "Fable 5.2", "the session's model is the pick")
        self.assertEqual(s._model_pending, "", "its dots end at its own report")
        self.assertEqual(s.fast, "on", "fast mode is on from the launch")
        self.assertFalse([r for r in s.client.asked if isinstance(r, dict)], "no re-arm was needed")
        self.assertEqual(self._problems(), [])

    def test_a_refusal_from_the_installed_version_reverts_as_before(self):
        sid, s = self._live(cli_seen="2.1.322")
        asks = []
        s.want_switch_reconnect = asks.append
        self._pick_refused(s, sid)
        self.assertEqual(asks, [])
        self.assertEqual(s.chosen_model, "opus", "reverted to the accepted model")
        self.assertEqual(sb.read_reg(self.d, sid)["model"], "opus")
        self.assertEqual(self.forgot, [PICK], "the refused pin is forgotten, as before")
        self.assertEqual(len(self._problems()), 1)
        self.assertIn("did NOT apply", self._problems()[0]["text"])

    def test_a_cli_launched_here_from_the_installed_binary_reverts_as_before_with_no_version_reported(self):
        # this connect launched the CLI from the installed binary, unchanged since, and no init has said its version yet:
        # it IS the installed Claude Code, so its refusal is a verdict (the CI case: a pick refused moments after a launch)
        cli = Path(self.d, "claude")
        cli.write_text("#!/bin/sh\necho 2.1.322\n")
        self.be.claude_bin = str(cli)
        sid, s = self._live(cli_seen="")
        s._launched_bin = sb._bin_ident(cli)
        s.want_switch_reconnect = lambda why: self.fail("no relaunch: the CLI is the installed binary")
        self._pick_refused(s, sid)
        self.assertEqual(s.chosen_model, "opus")
        self.assertEqual(len(self._problems()), 1)
        # …while a reattached CLI with no version reported reads as older (the rollout's reattach after a restart)
        sid2, s2 = self._live(cli_seen="")
        asks = []
        s2.want_switch_reconnect = asks.append
        self._pick_refused(s2, sid2)
        self.assertEqual(asks, ["model"])
        self.assertEqual(s2.chosen_model, PICK)

    def test_a_refusal_that_is_not_about_an_unknown_id_reverts_as_before(self):
        sid, s = self._live(refuse=Exception("restricted_by_org: claude-fable-5-2 is not available to your organization"))
        s.want_switch_reconnect = lambda why: self.fail("no relaunch for an org restriction")
        self._pick_refused(s, sid)
        self.assertEqual(s.chosen_model, "opus")
        self.assertEqual(len(self._problems()), 1)

    def test_a_refusal_after_the_relaunch_reverts_loudly(self):
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        s._host_is_attach = False
        s.client = _Client(refuse=UNKNOWN, runs=PICK)     # the relaunch's CLI does not know it either
        s._cli_version_seen = "2.1.322"
        s._model_accept(PICK)                            # the relaunch's init took --model as accepted

        async def go():
            s.loop = asyncio.get_running_loop()
            self.assertTrue(s._send_stale_pick())
            for _ in range(12):
                await asyncio.sleep(0)
        asyncio.run(go())
        self.assertEqual(s.chosen_model, "opus", "reverted to the model before the pick, not to the pick the launch carried")
        self.assertEqual(sb.read_reg(self.d, sid)["model"], "opus")
        self.assertNotIn("stalePick", sb.read_reg(self.d, sid))
        self.assertEqual(self.forgot, [PICK])
        rows = self._problems()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("did NOT apply", rows[0]["text"])
        self.assertIn(("set_model", "opus"), s.client.asked, "the CLI launched on the refused id is put back on the model")

    def test_a_kernel_restart_before_the_relaunch_still_relaunches(self):
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        s2 = sb.SdkSession(self.be, sb.read_reg(self.d, sid))   # the next kernel's session object
        self.assertEqual(s2._stale_pick["pick"], PICK)
        self.assertEqual(s2._model_accepted, "opus", "the attached CLI never took the pick")
        asks = []
        s2.want_switch_reconnect = asks.append
        s2._host_is_attach = True                          # it reattaches to the same old CLI
        self.assertFalse(s2._send_stale_pick())
        self.assertEqual(asks, ["model"], "the relaunch is asked again")

    def test_a_kernel_restart_while_the_pick_waits_keeps_its_dots_and_arms_nothing(self):
        """The review of #455, with Retry upgrades after downgrades on: the restart's heal of pending picks ran before the
        saved pick was read, so the pick survived without its switching dots, the label stayed on the model before it,
        the reattach armed a retry and logged a fallback that never happened, and the relaunch's first reply minted a
        "Model back on Fable 5.2" card, a card moved with no new information. Now the dots are restored with the pick and
        a session whose saved pick stands is waiting, not downgraded."""
        Path(self.d, sb.RETRY_UPGRADE_STORE).write_text("on")
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        self.be._update_reg(sid, liveModel="Opus 5.5", liveModelId="claude-opus-5-5")   # what the old CLI reported last
        self.assertTrue(sb.read_reg(self.d, sid).get("modelPending"), "the premise: the waiting pick wears its dots")
        cards = []
        hooks = {k: getattr(sb.SdkBackend, k, None) for k in ("on_model_fallback", "on_model_restored")}
        sb.SdkBackend.on_model_fallback = staticmethod(lambda sid_, frm, to: cards.append(("fallback", frm, to)))
        sb.SdkBackend.on_model_restored = staticmethod(lambda sid_, frm, to: cards.append(("restored", frm, to)))
        try:
            # the kernel restarts: the next kernel's boot sweep over the registry, then the session's object
            be2 = sb.SdkBackend(self.d, "/bin/false", lambda *a, **k: None)
            logs = []
            be2._log_cb = logs.append
            be2.cli_version = lambda: "2.1.322"
            with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="", returncode=0)), \
                    mock.patch.object(sb.SdkSession, "start", lambda self_: None), \
                    mock.patch.object(be2, "_lease_watch_start"), mock.patch.object(be2, "_start_test_root_sweep"):
                be2._boot_reconcile([sb.read_reg(self.d, sid)])
            self.assertTrue(sb.read_reg(self.d, sid).get("modelPending"), "the boot sweep keeps the waiting pick's dots")
            s2 = sb.SdkSession(be2, sb.read_reg(self.d, sid))
            s2.thread = type("T", (), {"is_alive": lambda self: True})()
            be2.sessions[sid] = s2
            self.assertEqual(s2._stale_pick["pick"], PICK)
            self.assertEqual(s2._model_pending, PICK, "restored with its switching dots")
            self.assertTrue(sb.read_reg(self.d, sid).get("modelPending"))
            self.assertEqual(s2.model, "Opus 5.5", "the CLI it reattaches to runs the model from before the pick")
            # the reattach to the same old CLI: the relaunch is asked again, and nothing reads the session as fallen
            asks = []
            s2.want_switch_reconnect = asks.append
            s2._host_is_attach = True
            self.assertFalse(s2._send_stale_pick())
            self.assertEqual(asks, ["model"])
            self.assertFalse(s2._arm_if_below_pick(), "waiting for its relaunch, not downgraded: no retry arm")
            self.assertIsNone(s2._upgrade_retry)
            self.assertFalse([m for m in logs if "fell back" in m], logs)
            snap = s2.snapshot()
            self.assertTrue(snap["modelPending"], "the badge's dots")
            self.assertIsNone(snap["modelFallback"], "no fallback mark beside the pick")
            self.assertEqual(be2.fallback_row_for_reg(sb.read_reg(self.d, sid)), None, "nor on the dormant row")
            # the old CLI reports the model it runs (the attach's context refresh, a reply of a turn it opened itself)
            s2._learn_model("Opus 5.5", raw="claude-opus-5-5", served=True)
            self.assertEqual(s2._model_pending, PICK, "a report of the model before the pick ends nothing")
            # the relaunch lands on the installed Claude Code, which takes the pick, and its first reply is served on it
            s2._host_is_attach = False
            s2.client = _Client(runs=PICK)

            async def go():
                s2.loop = asyncio.get_running_loop()
                self.assertTrue(s2._send_stale_pick())
                for _ in range(8):
                    await asyncio.sleep(0)
            asyncio.run(go())
            s2._learn_model("Fable 5.2", raw=PICK, served=True)
            self.assertEqual(s2._model_pending, "", "its dots end at its own report")
            self.assertIsNone(s2._stale_pick)
            self.assertEqual(cards, [], "no card: the pick landed, nothing fell back or came back")
            self.assertFalse([m for m in logs if "retry upgrade" in m], logs)
        finally:
            for k, v in hooks.items():
                if v is None:
                    try:
                        delattr(sb.SdkBackend, k)
                    except AttributeError:
                        pass
                else:
                    setattr(sb.SdkBackend, k, v)

    def test_a_restart_without_retry_upgrades_shows_the_dots_not_the_fallback_mark(self):
        """With Retry upgrades off the restart minted no card, but the picker's requested-model mark (the yellow fallback
        tick) showed where the pick's switching dots belong. An earlier kernel's heal that already dropped the flag gets
        it back from the saved pick."""
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        self.be._update_reg(sid, liveModel="Opus 5.5", modelPending=False)   # healed by a kernel before this fix
        s2 = sb.SdkSession(self.be, sb.read_reg(self.d, sid))
        self.assertEqual(s2._model_pending, PICK)
        self.assertTrue(sb.read_reg(self.d, sid).get("modelPending"), "the flag is back with the saved pick")
        snap = s2.snapshot()
        self.assertTrue(snap["modelPending"])
        self.assertIsNone(snap["modelFallback"])

    def test_a_thread_death_while_the_pick_waits_keeps_its_dots(self):
        """The thread's death clears a pick in flight so its dots are not trapped; a pick waiting for its relaunch is not in
        flight to a CLI that died with them, it waits for the launch this death brings sooner."""
        sid, s = self._live()
        s.want_switch_reconnect = lambda why: None
        self._pick_refused(s, sid)
        s.ended = True                                    # the session is ending: no heal relaunches it here
        self.be._on_session_gone(s)
        self.assertEqual(s._model_pending, PICK)
        self.assertTrue(sb.read_reg(self.d, sid).get("modelPending"))

    def test_the_snapshot_names_the_waiting_pick_for_the_badge(self):
        """What the badge's dots wait for (the review of #455): the pick, beside relaunchWaits, while it waits for its
        relaunch; nothing once the relaunch's CLI has it, nor for an ordinary live pick still resolving."""
        sid, s = self._live()
        s.inflight = 1                                     # a turn is running: the relaunch waits for it
        s.request_reconnect = lambda **k: None
        self._pick_refused(s, sid)
        snap = s.snapshot()
        self.assertTrue(snap["modelPending"])
        self.assertEqual(snap["modelWait"], "Fable 5.2")
        self.assertEqual(snap["relaunchWaits"], "the running turn")
        s._stale_pick["phase"] = "relaunched"
        self.assertEqual(s.snapshot()["modelWait"], "", "handed to the relaunch's CLI: no longer waiting for a relaunch")
        sid2, s2 = self._live(cli_seen="2.1.322", refuse=None)
        self.assertTrue(self.be.set_model(sid2, PICK))     # a live pick the CLI takes, its answer not yet in
        self.assertTrue(s2.snapshot()["modelPending"])
        self.assertEqual(s2.snapshot()["modelWait"], "", "an ordinary pick resolves live: no relaunch to wait for")

    def test_the_apis_too_old_refusal_keeps_the_pick_when_the_installed_cli_is_new_enough(self):
        """The rollout of 2026-11-12: a session on Claude Code 2.1.280 had its pick of Fable 5.2 refused by the API, not by
        the CLI's catalog ("Claude Code 2.1.280 does not support this model; version 2.1.290 or newer is required"), and
        romp reverted it although 2.1.322 was installed. The same case said by the API: the pick is kept for the quiet
        relaunch onto the installed Claude Code, nothing rung. The message names the refusing version, so a reattached CLI
        that has reported none is read by it."""
        for seen in ("2.1.280", ""):
            sid, s = self._live(cli_seen=seen, refuse=TOO_OLD)
            asks = []
            s.want_switch_reconnect = asks.append
            self.logs.clear()
            self._pick_refused(s, sid)
            self.assertEqual(asks, ["model"], "seen %r: the quiet relaunch" % seen)
            self.assertEqual(s.chosen_model, PICK, "the pick is kept")
            self.assertEqual(sb.read_reg(self.d, sid)["model"], PICK)
            self.assertEqual(s._model_pending, PICK, "its switching dots stand")
            self.assertEqual(sb.read_reg(self.d, sid).get("stalePick"), {"pick": PICK, "back": "opus"})
            self.assertEqual(self.forgot, [], "the family's remembered version is not forgotten")
            self.assertEqual(self._problems(), [], "nothing in the error center")
            said = [m for m in self.logs if "is too old for" in m]
            self.assertEqual(len(said), 1, self.logs)
            self.assertIn("Claude Code 2.1.280 is too old for claude-fable-5-2", said[0])
            self.assertIn("Claude Code 2.1.322 is installed", said[0])

    def test_the_apis_too_old_refusal_reverts_loudly_when_no_installed_cli_reaches_the_minimum(self):
        """Installed below the minimum the API named, or unreadable: no relaunch would run the pick, so it reverts and rings
        as before, and the line says what would: update Claude Code to the minimum."""
        for installed, words in (("2.1.285", "Claude Code 2.1.285 is installed; update Claude Code to ≥2.1.290"),
                                 ("", "update Claude Code to ≥2.1.290")):
            self.be.cli_version = lambda installed=installed: installed
            self.forgot.clear()
            sid, s = self._live(cli_seen="2.1.280", refuse=TOO_OLD)
            s.want_switch_reconnect = lambda why: self.fail("no relaunch: the installed Claude Code is too old as well")
            self._pick_refused(s, sid)
            self.assertEqual(s.chosen_model, "opus", "installed %r: reverted to the accepted model" % installed)
            self.assertEqual(sb.read_reg(self.d, sid)["model"], "opus")
            self.assertNotIn("stalePick", sb.read_reg(self.d, sid))
            self.assertEqual(self.forgot, [PICK], "the refused pin is forgotten, as before")
            rows = [p["text"] for p in self._problems() if "did NOT apply" in p["text"]]
            self.assertTrue(any(t.endswith("; " + words) for t in rows), rows)

    def test_the_too_old_matcher_reads_both_versions_and_nothing_else(self):
        self.assertEqual(sb._cli_too_old(TOO_OLD), ("2.1.280", "2.1.290"))
        self.assertEqual(sb._cli_too_old(Exception("This model does not support this model; version 3.0.1 or newer is required")),
                         ("", "3.0.1"), "a message naming no CLI version still names the minimum")
        self.assertIsNone(sb._cli_too_old(UNKNOWN))
        self.assertIsNone(sb._cli_too_old(Exception("restricted_by_org: claude-fable-5-2 is not available to your organization")))
        self.assertLess(sb._vtuple("2.1.285"), sb._vtuple("2.1.290"))
        self.assertGreaterEqual(sb._vtuple("2.1.322"), sb._vtuple("2.1.290"))
        self.assertIsNone(sb._vtuple(""))


if __name__ == "__main__":
    unittest.main()
