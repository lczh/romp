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

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend_stalepick", os.path.join(BIN, "romp_sdk_backend.py"))

PICK = "claude-fable-5-2"
UNKNOWN = Exception('Model "claude-fable-5-2" is not a recognized model id. Run /model to see available models.')


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


if __name__ == "__main__":
    unittest.main()
