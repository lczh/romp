#!/usr/bin/env python3
"""Permission-mode truth (T139, 2026-08-28; the exp specimen: a session in CONFIRMED
bypassPermissions got a permission ask for a plain main-thread Bash and sat blocked eleven hours).

Two truth rules, both the T124 family:
  * THE DECLARED-INTENT GUARD: the SDK's contract says bypass auto-approves every call before
    can_use_tool is consulted (deny rules aside) — when the CLI consults anyway (the specimen:
    CLI 2.1.221, once, against its own contract), romp — the registered permission authority —
    re-imposes the declared intent: auto-allow + a problems ring, never a block. Other modes keep
    asking: a consult under default/plan/acceptEdits is the CLI honestly delegating what its own
    evaluation could not auto-decide.
  * A REFUSED LIVE SWITCH REVERTS: set_mode flips the displayed mode optimistically and fires
    set_permission_mode; a refusal used to only log, leaving the switcher asserting a mode the CLI
    never accepted. Every layer now reverts to the last CONFIRMED mode (snapshot, session, reg —
    the next connect must not re-apply the refused pick) and the problems ring names it.

Hermetic state; synthetic sids; the SDK client is stubbed."""
import asyncio
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
# the callback imports PermissionResult* lazily — a hermetic box has no real SDK, so a stub module
# stands in AT CALL TIME ONLY (installed per-test in _Harness.setUp, removed in tearDown): a
# module-scope sys.modules entry leaked into the whole pytest process and made every real-SDK-gated
# test elsewhere stop skipping and run against the stub (30 failures in the full suite).
_fake = types.ModuleType("claude_agent_sdk")
class _PRA:
    def __init__(self, behavior="allow", updated_input=None, updated_permissions=None):
        self.behavior, self.updated_input, self.updated_permissions = behavior, updated_input, updated_permissions
class _PRD:
    def __init__(self, behavior="deny", message="", interrupt=False):
        self.behavior, self.message, self.interrupt = behavior, message, interrupt
_fake.PermissionResultAllow = _PRA
_fake.PermissionResultDeny = _PRD
_fake.PermissionUpdate = lambda **kw: kw
sb = load_source("romp_sdk_backend_modetruth", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-00000000d139"


class _Harness(unittest.TestCase):
    def setUp(self):
        self._sdk_before = sys.modules.get("claude_agent_sdk")
        if self._sdk_before is None:
            sys.modules["claude_agent_sdk"] = _fake   # call-time only; removed in tearDown
        self.d = tempfile.mkdtemp()
        self.logs = []
        self.be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None, log=self.logs.append)
        sb.write_reg(Path(self.d), SID, {"sid": SID, "name": "spec", "cwd": "/tmp", "mode": "bypassPermissions"})
        self.s = sb.SdkSession(self.be, sb.read_reg(Path(self.d), SID))
        self.be.sessions[SID] = self.s

    def tearDown(self):
        if self._sdk_before is None:
            sys.modules.pop("claude_agent_sdk", None)

    def _problems(self):
        return [p["text"] for p in self.be.problems(20)]


class DeclaredIntentGuard(_Harness):
    def test_a_consult_under_bypass_auto_allows_and_rings(self):
        # the specimen, replayed: perm_mode is CONFIRMED bypass; the CLI consults anyway
        self.s.perm_mode = "bypassPermissions"
        res = asyncio.run(self.s._can_use_tool("Bash", {"command": "mkdir -p x"}, object()))
        self.assertEqual(getattr(res, "behavior", None), "allow",
                         "romp re-imposes the declared intent — never a block under bypass")
        self.assertTrue(any("bypassPermissions" in t and "contract" in t for t in self._problems()),
                        "…and the CLI's contract breach is VISIBLE, never silent: %r" % self._problems())

    def test_a_consult_while_a_bypass_pick_waits_is_allowed_without_a_contract_row(self):
        # the CLI asking was launched without the flag, so its consult is honest; the pick is the declared intent (2026-10-01)
        self.s.perm_mode = "acceptEdits"
        self.s._mode_pending = "bypassPermissions"
        rows = [p["text"] for p in self.be.problems()]   # what stands before the consult (the harness's own boot rows)
        n = len(self.logs)
        res = self._consult_or_fail("mkdir -p x")
        self.assertEqual(getattr(res, "behavior", None), "allow", "the declared intent: no prompt while the pick waits")
        self.assertEqual([p["text"] for p in self.be.problems()], rows, "no CLI broke anything: no problem row of any wording")
        said = [l for l in self.logs[n:] if "Bash" in str(l)]   # found by the tool it names, in any wording
        self.assertEqual(len(said), 1, "the consult is said once, as a plain line: %r" % self.logs[n:])
        # …and a seeded perm_mode (a restart's construction reads the reg's bypass before any init) changes nothing
        self.s.perm_mode = "bypassPermissions"
        self._consult_or_fail("mkdir -p y")
        self.assertEqual([p["text"] for p in self.be.problems()], rows, "the mark says this CLI is not the launch that carried it")

    def _consult_or_fail(self, cmd):
        try:   # bounded: a consult that parks as a prompt waits for an answer this harness never gives
            return asyncio.run(asyncio.wait_for(self.s._can_use_tool("Bash", {"command": cmd}, object()), 5))
        except asyncio.TimeoutError:
            self.fail("the consult parked as a prompt instead of being allowed")

    def test_other_modes_still_ask(self):
        # a consult under default is the CLI honestly delegating — the ask machinery must engage.
        # The ask blocks on user input, so run it with a pre-resolved answer future.
        self.s.perm_mode = "default"
        async def drive():
            loop = asyncio.get_running_loop()
            self.s.loop = loop
            task = asyncio.ensure_future(self.s._can_use_tool("Bash", {"command": "x"}, object()))
            await asyncio.sleep(0.05)          # let it park on the ask
            self.assertIsNotNone(self.s._cur_ask_fut, "the ask engaged (the session marked permission)")
            self.s.resolve_ask("answer", "1")  # the user allows
            return await task
        res = asyncio.run(drive())
        self.assertEqual(getattr(res, "behavior", None), "allow")
        self.assertFalse(any("contract" in t for t in self._problems()),
                         "an honest consult rings nothing")


class RefusedSwitchReverts(_Harness):
    class _RefusingClient:
        async def set_permission_mode(self, mode):
            raise RuntimeError("control request rejected")

    class _AckingClient:
        async def set_permission_mode(self, mode):
            return None

    def test_refusal_reverts_every_layer_and_rings(self):
        # the real refused-live fixture (probed on CLI 2.1.221): 'auto' is model-dependent —
        # 'Cannot set permission mode to auto: auto mode unavailable for this model'
        self.s.perm_mode = "acceptEdits"       # the last CONFIRMED mode
        self.s.client = self._RefusingClient()
        async def drive():
            self.s.loop = asyncio.get_running_loop()
            self.assertTrue(self.be.set_mode(SID, "auto"))
            self.assertEqual(self.s.perm_mode, "auto", "the optimistic flip shows at once")
            await asyncio.sleep(0.05)          # the control request refuses
        asyncio.run(drive())
        self.assertEqual(self.s.perm_mode, "acceptEdits", "the snapshot reverted to the last confirmed mode")
        self.assertEqual(self.s.mode, "acceptEdits", "…and the connect-time mode")
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("mode"), "acceptEdits",
                         "…and the registry — the next connect must not re-apply the refused pick")
        self.assertTrue(any("did NOT apply" in t and "reverted" in t for t in self._problems()),
                        "the failed switch is unmissable: %r" % self._problems())

    def test_a_bypass_pick_applies_via_reconnect_never_the_live_call(self):
        # probed on CLI 2.1.221 (T139): set_permission_mode INTO bypass is refused unless the
        # process was LAUNCHED with --dangerously-skip-permissions — so the pick reconnects
        # (effort's pattern; the relaunch carries the mode) instead of firing a doomed live call
        self.s.perm_mode = "acceptEdits"
        calls = []
        self.s.set_mode_live = lambda mode, prev="default": calls.append(("live", mode))
        self.s.request_reconnect = lambda *a, **k: calls.append(("reconnect",) + (("cut",) if k.get("cut") else ()))
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(calls, [("reconnect",)], "bypass = reconnect, never the refused live call")
        # the relaunch waits for a quiet session, so the pick waits as a mark of its own (2026-10-01): perm_mode stays the
        # mode the running CLI confirmed until a launch carries the flag (tests/test_reconnect_waits_for_work.py drives it)
        self.assertEqual((self.s.perm_mode, self.s.mode, self.s._mode_pending), ("acceptEdits", "bypassPermissions", "bypassPermissions"))
        self.assertTrue(self.s.snapshot()["modePending"])
        self.assertEqual(self.s.snapshot()["mode"], "acceptEdits")
        calls.clear()
        self.s.perm_mode, self.s._mode_pending = "bypassPermissions", ""   # the relaunch landed: its launch carried the flag
        self.assertTrue(self.be.set_mode(SID, "default"))
        self.assertEqual(calls, [("live", "default")], "leaving bypass switches live (the flag only gates entry)")

    def test_changing_back_withdraws_the_relaunch_only_when_it_is_the_bypass_picks_alone(self):
        # SdkBackend._withdraw_mode_pick (2026-10-01): the ask holds one reason at a time, so a change back withdraws it only
        # when the bypass pick raised it with nothing else standing or in flight and no other pick's mark is up; the
        # end-to-end cases are tests/test_reconnect_waits_for_work.py ABypassPickWaitsAsAMarkOfItsOwn
        self.s.perm_mode = "acceptEdits"
        self.s._cli_launch = dict(self.s.launch_values(), mode="acceptEdits")   # the serving CLI's launch record
        self.s.set_mode_live = lambda mode, prev="default": None
        def ask(*a, **k):
            self.s._switch_wanted = k.get("why") or "a settings change"
        self.s.request_reconnect = ask
        cases = (("nothing else", {}, ""),
                 ("an ask carried, its relaunch not yet armed", {"_switch_ask_pending": "effort"}, "bypass mode"),
                 ("another pick's ask standing", {"_switch_wanted": "billing"}, "bypass mode"),
                 ("the fast opt-in's mark up", {"_fast_pending": "on"}, "bypass mode"),
                 ("an effort pick's mark up", {"_effort_pending": "high"}, "bypass mode"),
                 ("a billing pick's mark up", {"_auth_pending": "login"}, "bypass mode"))
        for what, before, left in cases:
            self.s._switch_wanted = self.s._switch_ask_pending = self.s._mode_pending = ""
            self.s._fast_pending = self.s._effort_pending = self.s._auth_pending = ""
            self.s._mode_ask_alone = False
            for k, v in before.items():
                setattr(self.s, k, v)
            self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
            self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("bypassPermissions", "bypass mode"), what)
            self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
            self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("", left), what)

    def test_a_later_ask_is_not_withdrawn_by_a_change_back(self):
        # an env pick renames the ask standing and carries no mark of its own (a Model switch stands down instead and sets
        # its reason aside), so withdrawing that ask on a change back would leave the env pick waiting until some other
        # relaunch carries it
        self.s.perm_mode = "acceptEdits"
        self.s.set_mode_live = lambda mode, prev="default": None
        def ask(*a, **k):
            self.s._switch_wanted = k.get("why") or "a settings change"
        self.s.request_reconnect = ask
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted, self.s._mode_ask_alone),
                         ("bypassPermissions", "bypass mode", True), "the premise: the pick raised the ask, its own")
        self.s.request_reconnect(why="session env")
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("bypassPermissions", "session env"),
                         "the later ask renamed the one standing, the mark still up")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("", "session env"))

    def test_re_picking_bypass_while_it_waits_stays_on_the_waiting_road(self):
        # every picker row posts setMode, the current one included, and after a kernel restart construction seeds perm_mode
        # from the reg's bypass: a re-pick must keep the mark and its record, never take the live call the CLI refuses
        calls = []
        self.s.set_mode_live = lambda mode, prev="default": calls.append(("live", mode))
        def ask(*a, **k):
            calls.append(("reconnect",))
            self.s._switch_wanted = k.get("why") or "a settings change"
        self.s.request_reconnect = ask
        self.s.perm_mode = self.s.mode = "bypassPermissions"
        self.s._mode_pending, self.s._switch_wanted = "bypassPermissions", "bypass mode"
        self.be._note_pick_waits(self.s, "mode", "bypass mode")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual(calls, [("reconnect",)], "the waiting road, never the live call")
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("bypassPermissions", "bypass mode"))
        self.assertEqual(set(((sb.read_reg(Path(self.d), SID) or {}).get("relaunchWait") or {}).get("picks") or {}), {"mode"})

    def test_a_second_bypass_pick_keeps_the_ask_its_own(self):
        # Bypass picked again before the first pick applies (the menu reopened, or from a thread's popover): the second pick
        # finds the ask its own first pick raised, so a change back still withdraws it
        self.s.perm_mode = "acceptEdits"
        self.s._cli_launch = dict(self.s.launch_values(), mode="acceptEdits")   # the serving CLI's launch record
        self.s.set_mode_live = lambda mode, prev="default": None
        def ask(*a, **k):
            self.s._switch_wanted = k.get("why") or "a settings change"
        self.s.request_reconnect = ask
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted, self.s._mode_ask_alone),
                         ("bypassPermissions", "bypass mode", True), "the premise: the first pick raised the ask, its own")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted, self.s._mode_ask_alone),
                         ("bypassPermissions", "bypass mode", True), "the second pick keeps it its own")
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("", ""))

    def test_a_re_pick_after_a_later_ask_leaves_that_ask_to_the_pick_that_renamed_it(self):
        # Bypass, then an env pick (no mark of its own) renames the ask, then Bypass again: the ask is not the bypass pick's
        # any more, whatever it was called when it was raised, so a change back must leave it to the env pick
        self.s.perm_mode = "acceptEdits"
        self.s.set_mode_live = lambda mode, prev="default": None
        def ask(*a, **k):
            self.s._switch_wanted = k.get("why") or "a settings change"
        self.s.request_reconnect = ask
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((self.s._switch_wanted, self.s._mode_ask_alone), ("bypass mode", True), "the premise: its own ask")
        self.assertTrue(self.be.set_env(SID, {"NOTES_API_MODE": "strict"}))
        self.assertEqual(self.s._switch_wanted, "session env", "the premise: the env pick renamed the ask")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("bypassPermissions", "bypass mode"))
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual((self.s._mode_pending, self.s._switch_wanted), ("", "bypass mode"),
                         "the env pick still needs the relaunch")
        self.assertEqual(set(((sb.read_reg(Path(self.d), SID) or {}).get("relaunchWait") or {}).get("picks") or {}), {"env"})

    def test_a_change_back_of_a_mark_the_record_lacks_files_no_problem_row(self):
        # a mark with no entry in the record (a pick made before the record existed, or a _note_pick_waits that failed):
        # the change back takes the mark down, and there is nothing to drop from the record
        self.s.perm_mode, self.s.mode, self.s._mode_pending = "acceptEdits", "bypassPermissions", "bypassPermissions"
        self.s.set_mode_live = lambda mode, prev="default": None
        self.assertNotIn("relaunchWait", sb.read_reg(Path(self.d), SID) or {}, "the premise: the record lacks the pick")
        rows = [p["text"] for p in self.be.problems()]   # the harness's own boot rows stand before the change back
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual(self.s._mode_pending, "")
        self.assertEqual([p["text"] for p in self.be.problems()], rows, "no problem row for a pick the record never held")

    def test_a_change_back_whose_record_cannot_be_dropped_files_a_problem_row(self):
        # the record is what a kernel restart raises the pick again from, so a drop that fails must be visible, never silent
        self.s.perm_mode = "acceptEdits"
        self.s.set_mode_live = lambda mode, prev="default": None
        self.s.request_reconnect = lambda *a, **k: setattr(self.s, "_switch_wanted", k.get("why") or "a settings change")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        self.assertIn("mode", ((sb.read_reg(Path(self.d), SID) or {}).get("relaunchWait") or {}).get("picks") or {},
                      "the premise: the pick is on record")
        rows = len(self.be.problems())
        real = sb.write_reg
        def write_reg(state_dir, sid, reg):
            if "relaunchWait" not in reg:      # the drop's write: the record's one pick leaving it
                raise OSError("synthetic write failure")
            return real(state_dir, sid, reg)
        sb.write_reg = write_reg
        self.addCleanup(setattr, sb, "write_reg", real)
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        sb.write_reg = real
        self.assertEqual(self.s._mode_pending, "", "the mark comes down all the same")
        self.assertIn("mode", ((sb.read_reg(Path(self.d), SID) or {}).get("relaunchWait") or {}).get("picks") or {},
                      "the premise: the drop did fail")
        self.assertEqual(len(self.be.problems()), rows + 1, "one problem row, of any wording")

    def test_a_change_back_whose_record_will_not_read_files_a_problem_row(self):
        self.s.perm_mode = "acceptEdits"
        self.s.set_mode_live = lambda mode, prev="default": None
        self.s.request_reconnect = lambda *a, **k: setattr(self.s, "_switch_wanted", k.get("why") or "a settings change")
        self.assertTrue(self.be.set_mode(SID, "bypassPermissions"))
        rows = len(self.be.problems())
        real = self.be._withdraw_mode_pick

        def unreadable(s, *a):                    # the record answers no read for the withdraw alone
            saved = sb.read_reg, sb.read_reg_for_rmw
            sb.read_reg = sb.read_reg_for_rmw = lambda *a, **k: None
            try:
                return real(s, *a)
            finally:
                sb.read_reg, sb.read_reg_for_rmw = saved
        self.be._withdraw_mode_pick = unreadable
        self.assertTrue(self.be.set_mode(SID, "acceptEdits"))
        self.assertEqual(self.s._mode_pending, "", "the mark comes down all the same")
        self.assertEqual(len(self.be.problems()), rows + 1, "one problem row, of any wording")

    def test_an_acked_switch_stays(self):
        self.s.perm_mode = "acceptEdits"
        self.s.client = self._AckingClient()
        async def drive():
            self.s.loop = asyncio.get_running_loop()
            self.be.set_mode(SID, "plan")      # plan ACKs live (probed)
            await asyncio.sleep(0.05)
        asyncio.run(drive())
        self.assertEqual(self.s.perm_mode, "plan")
        self.assertEqual((sb.read_reg(Path(self.d), SID) or {}).get("mode"), "plan")
        self.assertFalse(any("did NOT apply" in t for t in self._problems()))


if __name__ == "__main__":
    unittest.main()
