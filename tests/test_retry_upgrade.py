#!/usr/bin/env python3
"""Retry upgrades after downgrades (the user 2026-09-17): a kernel-side switch (Settings, Automation, Model; STATE/retry-upgrade)
under which a session whose model fell to a lower tier without a pick asks for its pick again every RETRY_UPGRADE_S at a
quiet moment — a reconnect (want_switch_reconnect: now if nothing runs, else at the event that ends the last of it) whose connect re-asserts the pick —
until a parent turn is SERVED on the pick's tier again (the AssistantMessage learn, never the init's report or a context
refresh), which mints the "back on" card through the kernel-wired hook; a pick of the user's own, the session's end or the
switch going off end it too. A fallback that happens again while armed is logged, not carded again. The fallback's cause
is outside romp's view (a trigger in the task's context that ages out of the window), so the cadence is the designed read;
the boundary is the event. Hermetic state, synthetic sids, no CLI."""
import asyncio
import inspect
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-4333-8444-000000000711"


def _backend():
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # this root is outside the runner's belt (CLAUDE.md, 2026-09-11)
    be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
    be._logs = []
    be._log = lambda msg, *a, **k: be._logs.append(msg)
    return be


def _sess(be, **reg):
    r = {"sid": SID, "name": "web", "cwd": "/tmp", "model": "fable", "liveModel": "Fable 5.1", "liveModelId": "claude-fable-5-1"}
    r.update(reg)
    sb.write_reg(be.state_dir, r["sid"], dict(r, alive=True))
    s = sb.SdkSession(be, r)
    s.thread = type("T", (), {"is_alive": lambda self: True})()
    s.request_reconnect = lambda *a, **k: s._asks.append(a)
    s._asks = []
    be.sessions[r["sid"]] = s
    return s


class _Hooks:
    """The kernel's two class-level hooks, wired for a test and unwired after (the class is shared by every test)."""
    def __init__(self):
        self.fallbacks, self.restored = [], []

    def __enter__(self):
        sb.SdkBackend.on_model_fallback = staticmethod(lambda sid, frm, to: (self.fallbacks.append((frm, to)), "%s:g%d" % (sid, len(self.fallbacks)))[1])
        sb.SdkBackend.on_model_restored = staticmethod(lambda sid, frm, to: self.restored.append((frm, to)))
        return self

    def __exit__(self, *a):
        del sb.SdkBackend.on_model_fallback
        del sb.SdkBackend.on_model_restored


class Arming(unittest.TestCase):
    def test_a_downgrade_arms_the_retry_only_with_the_switch_on(self):
        be = _backend()
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            self.assertEqual(h.fallbacks, [("Fable 5.1", "Opus 5")], "the card, as before")
            self.assertIsNone(s._upgrade_retry, "switch off: nothing armed")
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            s = _sess(be)
            t0 = time.time()
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm)
            self.assertEqual((arm["from"], arm["to"], arm["pick"], arm["attempts"]), ("Fable 5.1", "Opus 5", "fable", 0))
            self.assertGreaterEqual(arm["next"], t0 + sb.RETRY_UPGRADE_S - 1, "the first attempt waits a full cadence")
            self.assertTrue(any(m.startswith("retry upgrade (web): Fable 5.1 fell back to Opus 5") for m in be._logs), be._logs)

    def test_a_fallback_while_armed_asks_the_store_again_and_is_logged_once_per_attempt(self):
        # review 2026-09-17: the card is the store's call (mint_fallback_card's existence-keyed dedupe mints nothing
        # while the swap's card stands, a fresh one once the user cleared it); the log says it once per attempt
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)        # the fallback: card + arm
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")               # a connect reports the pick (the init: not served)
            self.assertIsNotNone(s._upgrade_retry, "the CLI running on the pick is not the API serving it")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)        # …and the API falls back again, before any attempt
            self.assertEqual(len(h.fallbacks), 2, "the hook is asked each time; the store dedupes")
            self.assertEqual([m for m in be._logs if "fell back to Opus 5 again" in m], [], "no attempt yet: nothing to report")
            be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1)     # attempt 1
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            again = [m for m in be._logs if "fell back to Opus 5 again after attempt 1" in m]
            self.assertEqual(len(again), 1, "said once for attempt 1, with the wait to the next: %r" % be._logs)
            self.assertIn("next attempt in", again[0])
            self.assertIsNotNone(s._upgrade_retry, "still armed")


class TheTick(unittest.TestCase):
    def test_a_due_session_is_asked_once_per_cadence_by_a_deferred_reconnect(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now), 0, "not due yet")
            self.assertEqual(s._asks, [])
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "due: one ask")
            self.assertEqual(len(s._asks), 1, "the session is quiet: one immediate reconnect (defer=False)")
            self.assertEqual(s._asks[0], (), "…through request_reconnect(defer=False), a keyword the stub drops")
            self.assertEqual(s._upgrade_retry["attempts"], 1)
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 2), 0, "…and not again until the next cadence")
            self.assertEqual(be.retry_model_upgrades(now + 2 * sb.RETRY_UPGRADE_S + 3), 1)
            self.assertEqual(s._upgrade_retry["attempts"], 2)
            self.assertTrue(any(m.startswith("retry upgrade (web): attempt 2") for m in be._logs), be._logs)

    def test_the_switch_going_off_ends_a_standing_retry(self):
        # review 2026-09-17: an arm that outlived the switch would have minted a "back on" card crediting a retry that never ran
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0)
            self.assertEqual(s._asks, []); self.assertIsNone(s._upgrade_retry, "the tick clears it")
            self.assertTrue(any("standing down" in m for m in be._logs), be._logs)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)
            self.assertEqual(h.restored, [], "no card credits a retry that never ran")
            # a reader that meets the switch off first clears it too
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            s._learn_model("Opus 5", raw="claude-opus-5", served=True); self.assertIsNotNone(s._upgrade_retry)
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)
            self.assertIsNone(s._upgrade_retry); self.assertEqual(h.restored, [])

    def test_turning_the_switch_on_takes_up_a_session_that_already_sits_below_its_pick(self):
        be = _backend()
        with _Hooks():
            s = _sess(be, liveModel="Opus 5", liveModelId="claude-opus-5")   # pick fable, running on Opus already
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            be.apply_model_switches()
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "the very card the user was looking at")
            self.assertEqual(arm["to"], "Opus 5"); self.assertTrue(arm["from"]); self.assertEqual(arm["pick"], "fable")
            t = _sess(be, sid="11111111-2222-4333-8444-000000000712", model="", liveModel="Opus 5", liveModelId="claude-opus-5")
            be.apply_model_switches()
            self.assertIsNone(t._upgrade_retry, "no pick, no tier to sit below")

    def test_an_ended_session_is_skipped(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s.ended = True
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0)


class TheWayBack(unittest.TestCase):
    def test_a_served_turn_on_the_picks_tier_ends_the_retry_and_mints_the_card(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1")                # the init after the reconnect: configured, not served
            self.assertIsNotNone(s._upgrade_retry); self.assertEqual(h.restored, [])
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)   # a parent turn served on Fable
            self.assertIsNone(s._upgrade_retry, "done")
            self.assertEqual(h.restored, [("Opus 5", "Fable 5.1")], "the hook: (the fallback it sat on, the model it is back on)")
            self.assertTrue(any(m.startswith("retry upgrade (web): back on Fable 5.1") for m in be._logs), be._logs)

    def test_a_higher_tier_than_the_one_it_fell_from_counts_too_and_a_lower_one_does_not(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be, model="opus", liveModel="Opus 5", liveModelId="claude-opus-5")
            s._learn_model("Sonnet 5", raw="claude-sonnet-5", served=True)     # Opus → Sonnet
            self.assertIsNotNone(s._upgrade_retry)
            s._learn_model("Haiku 4.5", raw="claude-haiku-4-5", served=True)   # lower still: a second fallback, same arm
            self.assertIsNotNone(s._upgrade_retry)
            s._learn_model("Fable 5.1", raw="claude-fable-5-1", served=True)   # above the tier it fell from
            self.assertIsNone(s._upgrade_retry)
            self.assertEqual(h.restored[-1], ("Sonnet 5", "Fable 5.1"))

    def test_a_pick_of_the_users_own_ends_the_retry(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            self.assertIsNotNone(s._upgrade_retry)
            try:
                be.set_model(SID, "opus")
            except Exception:
                pass   # the acknowledging chip needs a chat this lab has none of; the arm clears under the lock before it
            self.assertIsNone(s._upgrade_retry, "the user chose: nothing to retry")



class AnOlderVersionOfThePickedFamily(unittest.TestCase):
    """A safeguards fallback that stays inside the family (a newer Opus answered by an older one, first seen 2026-09-23): the family
    rank alone read it as lateral, so no retry armed, the tick never asked, and the session ran on the older model until
    something unrelated happened to reconnect it, for hours and once for days. The pick here is a versioned id, the shape
    the model picker writes; the learn compares the badge names."""

    PICK = {"name": "api", "model": "claude-opus-5-5", "liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5"}

    def test_the_fallback_arms_the_retry_the_tick_asks_and_a_served_turn_on_the_pick_ends_it(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be, **self.PICK)
            s._learn_model("Opus 4.8", raw="claude-opus-4-8", served=True)
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "carded like any other downgrade")
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "the retry arms")
            self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", "Opus 4.8", "claude-opus-5-5"))
            self.assertTrue(any(m.startswith("retry upgrade (api): Opus 5.5 fell back to Opus 4.8") for m in be._logs), be._logs)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "due: the pick is asked for again")
            self.assertEqual(len(s._asks), 1)
            s._learn_model("Opus 5.5", raw="claude-opus-5-5")                   # the reconnect's init: configured, not served
            self.assertIsNotNone(s._upgrade_retry, "the CLI running on the pick is not the API serving it")
            s._learn_model("Opus 4.8", raw="claude-opus-4-8", served=True)      # refused again: back on the older version
            self.assertIsNotNone(s._upgrade_retry, "still below the pick: the arm stands")
            self.assertEqual(h.restored, [])
            self.assertTrue(any("fell back to Opus 4.8 again after attempt 1" in m for m in be._logs), be._logs)
            s._learn_model("Opus 5.5", raw="claude-opus-5-5")
            s._learn_model("Opus 5.5", raw="claude-opus-5-5", served=True)      # served on the pick
            self.assertIsNone(s._upgrade_retry, "done")
            self.assertEqual(h.restored, [("Opus 4.8", "Opus 5.5")])

    def test_a_point_release_down_counts_and_a_newer_version_than_the_pick_does_not(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks() as h:
            s = _sess(be, **self.PICK)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)          # 5.5 answered by 5
            self.assertIsNotNone(s._upgrade_retry)
            s._learn_model("Opus 5.5", raw="claude-opus-5-5", served=True)
            self.assertIsNone(s._upgrade_retry)
            t = _sess(be, sid="11111111-2222-4333-8444-000000000713", name="tests", model="claude-opus-4-8",
                      liveModel="Opus 4.8", liveModelId="claude-opus-4-8")
            t._learn_model("Opus 5.5", raw="claude-opus-5-5", served=True)
            self.assertIsNone(t._upgrade_retry, "a newer version than the pick is nobody's fallback")
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 5")], "…and no card for it")

    def test_turning_the_switch_on_or_an_attach_takes_up_a_session_already_on_the_older_version(self):
        be = _backend()
        with _Hooks():
            s = _sess(be, name="api", model="claude-opus-5-5", liveModel="Opus 4.8", liveModelId="claude-opus-4-8",
                      servedModel="Opus 4.8")
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            be.apply_model_switches()
            arm = s._upgrade_retry
            self.assertIsNotNone(arm)
            self.assertEqual((arm["from"], arm["to"]), ("Opus 5.5", "Opus 4.8"))
            t = _sess(be, sid="11111111-2222-4333-8444-000000000714", name="tests", model="opus", liveModel="Opus 4.8",
                      liveModelId="claude-opus-4-8", servedModel="Opus 4.8")
            be.apply_model_switches()
            self.assertIsNone(t._upgrade_retry, "a bare alias pick names no version, so nothing says it sits below it")


class _Alive:
    def is_alive(self):
        return True


class _Cli:
    """The two control requests a pick's roads use. `ctx` scripts get_context_usage's answers in order: a model id, a
    dict answered as it stands, an exception to raise, "NONDICT" (a non-dict answer), "NOMODEL" (a dict without a
    model), or (event, answer) to hold the answer until the test sets the event. `lost` is raised by set_model."""

    def __init__(self, ctx, lost=None):
        self.ctx = list(ctx)
        self.lost = lost

    async def set_model(self, model=None):
        if self.lost is not None:
            raise self.lost

    async def get_context_usage(self):
        item = self.ctx.pop(0) if self.ctx else RuntimeError("no answer scripted")
        if isinstance(item, tuple):
            await item[0].wait()
            item = item[1]
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return item
        if item == "NONDICT":
            return None
        if item == "NOMODEL":
            return {"percentage": 3}
        return {"percentage": 3, "model": item}


async def _spin(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


class APickOfTheUsersOwnIsNeverAFallback(unittest.TestCase):
    """(2026-09-30, the #33 follow-up) Since the learn reads versions inside a family, a model the user picked could be
    read as an automatic fallback: the pick was marked in flight only when the live name did not already carry the
    pick's family word, so claude-opus-4-8 picked on Opus 5.5, a Latest click (the alias `opus`) and the account default
    went unmarked, and when the pick's first report was a turn's init rather than the context refresh, the move read as
    Opus 5.5 falling back: a Completed card saying the model changed automatically and, with Retry upgrades on, a retry
    asking for the model the user left every ten minutes, never ended by turns served on the pick. Every live pick is
    now in flight from the click until a report of it lands; a refresh asked before the pick answers nothing for it; a
    session revived with a pick in flight starts from the pick's name; and a move onto a versioned pick's own model is
    that pick landing. A real fallback after the pick lands is still carded and armed. Every road drives the real
    backend: set_model, _do_set_model and _do_refresh_context against a fake client, and the learn."""

    LIVE = {"name": "web", "model": "claude-opus-5-5", "liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5"}
    DOWN = "control channel down"

    def _world(self, stub_live=True, **reg):
        be = _backend()
        Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        be._ack_cmd_chip = lambda *a, **k: None          # the acknowledging chip needs a chat this lab has none of
        s = _sess(be, **dict(self.LIVE, **reg))
        s._sent = []
        if stub_live:                                   # the loop hop, captured: the test runs _do_set_model itself
            s.set_model_live = lambda model, prev=None: s._sent.append((model, prev))
        return be, s

    def _pick(self, be, s, value):
        self.assertTrue(be.set_model(SID, value))
        return (s._model_pending, sb.read_reg(be.state_dir, SID).get("modelPending"))

    def _settle(self, s, ctx, lost=None):
        s.client = _Cli(ctx, lost)
        asyncio.run(s._do_set_model(*s._sent[-1]))

    def _refresh(self, s, *answers):
        s.client = _Cli(list(answers))
        asyncio.run(s._do_refresh_context())

    @staticmethod
    def _init(s, raw):
        s._learn_model(sb.pretty_model(raw), raw=raw)

    @staticmethod
    def _served(s, raw):
        s._learn_model(sb.pretty_model(raw), raw=raw, served=True)

    def _assert_nothing(self, be, s, h, road):
        self.assertEqual(h.fallbacks, [], "%s: a pick of the user's own was carded as an automatic fallback" % road)
        self.assertIsNone(s._upgrade_retry, "%s: a pick of the user's own armed a retry of the model the user left" % road)
        self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                         "%s: the tick asked for the model the user left" % road)
        self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)
        self.assertEqual([m for m in be._logs if m.startswith("retry upgrade")], [], "%s: the retry was logged" % road)

    def _assert_landed(self, be, s, label, marker, at_pick, road):
        reg = sb.read_reg(be.state_dir, SID)
        self.assertEqual(s.model, label, "%s: the badge ends on the pick" % road)
        self.assertEqual((s._model_pending, reg.get("modelPending")), ("", False),
                         "%s: the first report of the pick ends its switching dots" % road)
        self.assertEqual(at_pick, (marker, True), "%s: every live pick is in flight from the click until a report of it lands" % road)

    def test_an_older_version_picked_is_not_a_fallback_whatever_reports_it_first(self):
        down = RuntimeError(self.DOWN)
        lost = Exception("Control request timeout: set_model")   # no answer: the pick stands (_cli_refusal)
        roads = (("the pick's refresh raises", "claude-opus-4-8", [down], None, "Opus 4.8"),
                 ("the pick's refresh answers a non-dict", "claude-opus-4-8", ["NONDICT"], None, "Opus 4.8"),
                 ("the pick's refresh answers without a model", "claude-opus-4-8", ["NOMODEL"], None, "Opus 4.8"),
                 ("the CLI's answer to the pick is lost", "claude-opus-4-8", [down], lost, "Opus 4.8"),
                 ("a 1M-tagged pin", "claude-opus-4-8[1m]", [down], None, "Opus 4.8"),
                 ("a point release down", "claude-opus-5", [down], None, "Opus 5"))
        for road, pick, ctx, err, label in roads:
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world()
                at_pick = self._pick(be, s, pick)
                self._settle(s, ctx, err)
                self._init(s, pick); self._served(s, pick)          # the next turn: its init, then its reply, on the pick
                self._assert_nothing(be, s, h, road)
                self._assert_landed(be, s, label, pick, at_pick, road)
        road = "the next turn's init lands while the pick's refresh is still out"
        with self.subTest(road=road), _Hooks() as h:
            # a pick made mid-turn parks, a message typed after it parks behind it, and both leave in one pass at the
            # turn's end: the send's init can arrive before the refresh the pick asks for once the CLI took it
            be, s = self._world()
            at_pick = self._pick(be, s, "claude-opus-4-8")

            async def main():
                held = asyncio.Event()
                s.client = _Cli([(held, "claude-opus-4-8")])
                t = asyncio.ensure_future(s._do_set_model(*s._sent[-1]))
                await _spin()
                self._init(s, "claude-opus-4-8"); self._served(s, "claude-opus-4-8")
                held.set()
                await t
            asyncio.run(main())
            self._assert_nothing(be, s, h, road)
            self._assert_landed(be, s, "Opus 4.8", "claude-opus-4-8", at_pick, road)

    def test_a_latest_click_resolving_below_the_live_version_is_not_a_fallback(self):
        road = "a Latest click, its refresh raising, the alias resolving to Opus 5"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            at_pick = self._pick(be, s, "opus")
            self._settle(s, [RuntimeError(self.DOWN)])
            self._init(s, "claude-opus-5"); self._served(s, "claude-opus-5")
            self._refresh(s, "claude-opus-5")                                   # the turn-end refresh
            self._init(s, "claude-opus-5"); self._served(s, "claude-opus-5")    # a second turn
            self._assert_nothing(be, s, h, road)
            self._assert_landed(be, s, "Opus 5", "opus", at_pick, road)
        road = "a Latest click with no client up, the connect's first init beating its refresh"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(stub_live=False)       # no loop, no client: nothing is sent; the next connect carries it
            at_pick = self._pick(be, s, "opus")
            self._init(s, "claude-opus-5"); self._served(s, "claude-opus-5")
            self._refresh(s, "claude-opus-5")
            self._assert_nothing(be, s, h, road)
            self._assert_landed(be, s, "Opus 5", "opus", at_pick, road)

    def test_a_default_pick_resolving_to_a_lower_family_is_not_a_fallback(self):
        for pick in ("default", ""):
            road = "the account default picked as %r, its refresh raising, resolving to Sonnet 5" % pick
            with self.subTest(pick=pick), _Hooks() as h:
                be, s = self._world()
                at_pick = self._pick(be, s, pick)
                self.assertIsNone(s._sent[-1][0], "%s: the account default goes out as set_model(None)" % road)
                self._settle(s, [RuntimeError(self.DOWN)])
                self._init(s, "claude-sonnet-5"); self._served(s, "claude-sonnet-5")
                self._refresh(s, "claude-sonnet-5")
                self._assert_nothing(be, s, h, road)
                self._assert_landed(be, s, "Sonnet 5", "default", at_pick, road)

    def test_a_refresh_asked_before_the_pick_neither_answers_for_it_nor_moves_the_label(self):
        # a turn-end refresh still out when the pick goes live: the pick's own refresh folds into its rerun
        # (_ctx_refresh_again), and the answer that lands first describes the world before the pick
        stale = {"percentage": 37, "model": "claude-opus-5-5"}
        picks = (("versioned", "claude-opus-4-8", "claude-opus-4-8", "Opus 4.8", "claude-opus-4-8"),
                 ("alias", "opus", "claude-opus-5", "Opus 5", "opus"),
                 ("default", "default", "claude-sonnet-5", "Sonnet 5", "default"))
        for kind, pick, resolved, label, marker in picks:
            for order in ("A: the stale answer, then the send's init and reply", "B: the send's init, then the stale answer"):
                road = "%s pick, order %s" % (kind, order)
                with self.subTest(pick=kind, order=order[0]), _Hooks() as h:
                    be, s = self._world()
                    seen = {}

                    async def main():
                        old_ask, rerun = asyncio.Event(), asyncio.Event()
                        s.client = _Cli([(old_ask, stale), (rerun, {"percentage": 41, "model": resolved})])
                        before = asyncio.ensure_future(s._do_refresh_context())   # asked before the pick
                        await asyncio.sleep(0)
                        seen["at_pick"] = self._pick(be, s, pick)
                        t = asyncio.ensure_future(s._do_set_model(*s._sent[-1]))
                        await _spin()
                        if order[0] == "B":
                            self._init(s, resolved)
                        old_ask.set()
                        await _spin()
                        seen["after_stale"] = (s._model_pending, s.model, s._ctx_pct())
                        if order[0] == "A":
                            self._init(s, resolved)
                        self._served(s, resolved)
                        rerun.set()
                        await before
                        await t
                    asyncio.run(main())
                    self._assert_nothing(be, s, h, road)
                    if order[0] == "A":
                        self.assertEqual(seen["after_stale"], (marker, "Opus 5.5", 37),
                                         "%s: a refresh asked before the pick answered for it or moved the label "
                                         "(its percentage still lands)" % road)
                    else:
                        self.assertEqual(seen["after_stale"][1:], (label, 37),
                                         "%s: a refresh asked before the pick moved the label back above the pick "
                                         "(its percentage still lands)" % road)
                    self._assert_landed(be, s, label, marker, seen["at_pick"], road)

    def test_a_move_onto_a_versioned_pick_is_the_pick_landing(self):
        # a reply of a turn the CLI opened by itself, still streaming on Opus 5.5 when the pick went live, can move the
        # live name back above the pick after the pick's first report cleared its marker
        roads = ("the pick's refresh lands, a reply of a turn already running on Opus 5.5, then a turn on the pick",
                 "the pick's refresh lands, then one turn serves Opus 5.5 and then the pick",
                 "the init of a turn begun before the CLI took the pick reports Opus 5.5, then the pick is served",
                 "no client at the pick: the connect's refresh reports Opus 5.5, a later connect's init the pick")
        for i, road in enumerate(roads):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(stub_live=(i != 3))
                self._pick(be, s, "claude-opus-4-8")
                if i == 0:
                    self._settle(s, ["claude-opus-4-8"])
                    self._served(s, "claude-opus-5-5")
                    self._init(s, "claude-opus-4-8"); self._served(s, "claude-opus-4-8")
                elif i == 1:
                    self._settle(s, ["claude-opus-4-8"])
                    self._served(s, "claude-opus-5-5"); self._served(s, "claude-opus-4-8")
                elif i == 2:
                    self._init(s, "claude-opus-5-5")
                    self._served(s, "claude-opus-4-8")
                    self._settle(s, ["claude-opus-4-8"])
                else:
                    self._refresh(s, "claude-opus-5-5")
                    self._init(s, "claude-opus-4-8"); self._served(s, "claude-opus-4-8")
                self._assert_nothing(be, s, h, road)
                self.assertEqual(s.model, "Opus 4.8", "%s: the badge ends on the pick" % road)

    def test_a_real_fallback_right_after_a_pick_is_still_carded_and_armed(self):
        # the guard against hiding too much: the init and every refresh report the model the CLI is configured with,
        # the pick; only a served reply shows what the API served, and a served reply below the pick's resolution
        # after the pick landed is a fallback, carded and armed exactly as before
        down = RuntimeError(self.DOWN)
        on48 = {"model": "claude-opus-4-8", "liveModel": "Opus 4.8", "liveModelId": "claude-opus-4-8"}
        roads = (("a versioned upward pick whose refresh answers", on48, "claude-opus-5-5", ["claude-opus-5-5"],
                  "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                 ("a versioned upward pick whose refresh raises", on48, "claude-opus-5-5", [down],
                  "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                 ("a Latest click from Sonnet whose refresh raises",
                  {"model": "claude-sonnet-5", "liveModel": "Sonnet 5", "liveModelId": "claude-sonnet-5"}, "opus", [down],
                  "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                 ("a Latest click on a live Opus 5.5", {}, "opus", ["claude-opus-5-5"],
                  "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                 ("the live model picked again, its refresh raising", {}, "claude-opus-5-5", [down],
                  "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                 ("the account default resolving to Sonnet 5, then Haiku served", {}, "default", [down],
                  "claude-sonnet-5", "claude-haiku-4-5", ("Sonnet 5", "Haiku 4.5")))
        for i, (road, reg, pick, ctx, init_raw, served_raw, card) in enumerate(roads):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**reg)
                self._pick(be, s, pick)
                self._settle(s, ctx)
                self._init(s, init_raw); self._served(s, served_raw)
                self.assertEqual(h.fallbacks, [card], "%s: a real fallback after the pick landed is carded, exactly once" % road)
                arm = s._upgrade_retry
                self.assertIsNotNone(arm, "%s: a real fallback after the pick landed arms the retry" % road)
                self.assertEqual((arm["from"], arm["to"]), card, "%s: the retry asks for the pick's resolution" % road)
                if i == 0:
                    self.assertEqual(arm["pick"], "claude-opus-5-5")
                    self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1,
                                     "%s: due, the pick is asked for again" % road)
                    self.assertEqual(len(s._asks), 1)
                    self._served(s, "claude-opus-5-5")
                    self.assertEqual(h.restored, [("Opus 4.8", "Opus 5.5")], "%s: a turn served on the pick ends it" % road)
                    self.assertIsNone(s._upgrade_retry)
        road = "no pick: a served fallback (the control)"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._served(s, "claude-opus-4-8")
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: carded" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: armed" % road)

    PICKS = (("versioned", "claude-opus-4-8", "claude-opus-4-8", "Opus 4.8"),
             ("alias", "opus", "claude-opus-5", "Opus"),
             ("default", "default", "claude-sonnet-5", ""))

    @staticmethod
    def _revived(t):
        t.thread = _Alive()
        t._asks = []
        t.request_reconnect = lambda *a, **k: t._asks.append(a)
        return t

    def test_a_thread_that_dies_before_the_pick_lands_revives_on_the_picks_label(self):
        for when in ("idle", "mid-turn"):
            for kind, pick, resolved, label in self.PICKS:
                road = "a %s pick, the thread dying %s before its refresh lands" % (kind, when)
                with self.subTest(when=when, pick=kind), _Hooks() as h, \
                        mock.patch.object(sb.SdkSession, "start", lambda self: None):
                    be, s = self._world()
                    self._pick(be, s, pick)
                    s.inflight = 1 if when == "mid-turn" else 0   # mid-turn: the heal resumes it (_heal_cut_session)
                    be._on_session_gone(s)
                    if when == "idle":
                        be._ensure(SID)                            # the next send revives it
                    t = be.sessions.get(SID)
                    self.assertTrue(t is not None and t is not s, "%s: the session was revived" % road)
                    revived = self._revived(t).model
                    self._init(t, resolved); self._served(t, resolved)
                    self._assert_nothing(be, t, h, road)
                    self.assertEqual(revived, label, "%s: the revived session starts from the pick's name, not the "
                                                     "model it ran before the pick" % road)

    def test_a_pick_stranded_by_a_hard_death_restarts_from_the_picks_label(self):
        for where in ("the session's construction", "the boot sweep"):
            for kind, pick, resolved, label in self.PICKS:
                road = "a %s pick in flight at a hard kernel death, healed by %s" % (kind, where)
                with self.subTest(where=where, pick=kind), _Hooks() as h:
                    be = _backend()
                    Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
                    sb.write_reg(be.state_dir, SID, {"sid": SID, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": SID,
                                                     "model": pick, "liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5",
                                                     "modelPending": True})   # what a marked pick leaves behind
                    if where == "the boot sweep":
                        sb.append_state(Path(be.state_dir), SID, "waiting")
                        be._ensure = lambda sid, on_boot_settled=None: None
                        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
                            be._boot_reconcile([sb.read_reg(be.state_dir, SID)])
                        del be._ensure
                    s = self._revived(sb.SdkSession(be, sb.read_reg(be.state_dir, SID)))
                    be.sessions[SID] = s
                    revived, healed = s.model, sb.read_reg(be.state_dir, SID)
                    self._init(s, resolved); self._served(s, resolved)
                    self._assert_nothing(be, s, h, road)
                    self.assertEqual(revived, label, "%s: the session restarts from the pick's name" % road)
                    self.assertEqual((healed.get("liveModel"), healed.get("modelPending")), (label, False),
                                     "%s: the registry is healed onto the pick's name, its flag cleared" % road)

    def test_the_switch_turned_on_while_a_pick_is_in_flight_arms_nothing(self):
        with _Hooks() as h:
            be, s = self._world(model="claude-opus-4-8", liveModel="Opus 4.8", liveModelId="claude-opus-4-8")
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            self._pick(be, s, "claude-opus-5-5")                          # an upward pick; its refresh has not answered
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
            be.apply_model_switches()
            self.assertIsNone(s._upgrade_retry, "the flip armed a retry from a live name that is the world before the pick")
            self.assertIsNone(s.snapshot()["modelFallback"], "a pick still resolving is switching dots, never a fallback mark")
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")
            self.assertEqual(h.restored, [], "a card said the session was back on a model nothing had moved it off")
            self.assertEqual(h.fallbacks, [])


class KernelWiring(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            self.src = f.read()

    def test_the_hook_the_tick_and_the_stage_are_wired(self):
        self.assertIn("type(_sdk_backend).on_model_restored = staticmethod(", self.src)
        self.assertIn("jd.mint_restored_card(sid, frm, to), _push_soon()", self.src)
        self.assertIn("_job_stage('retryUpgrade', lambda: _retry_upgrade_tick(now))", self.src)
        self.assertIn("def _retry_upgrade_tick(now):", self.src)
        self.assertIn("be.retry_model_upgrades(now)", self.src)
        src = inspect.getsource(sb.SdkSession._on_message)
        self.assertIn('self._learn_model(pretty_model(m), raw=str(m), served=True)', src, "the parent AssistantMessage is the served evidence")
        self.assertEqual(src.count("served=True"), 1, "…and the ONLY served learn: the init's report of the configured model passes no flag")
        self.assertIn('self._learn_model(pretty_model(d.get("model")), raw=str(d.get("model") or ""))', src, "the init learn, flagless")


class ABusySessionIsNotCut(unittest.TestCase):
    def test_a_due_attempt_on_a_busy_session_waits_for_quiet_and_no_second_attempt_stacks_on_it(self):
        be = _backend(); Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
        with _Hooks():
            s = _sess(be)
            s._learn_model("Opus 5", raw="claude-opus-5", served=True)
            s.inflight = 1
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "due: the attempt is asked for")
            self.assertEqual(s._asks, [], "…but nothing reconnects mid-turn, and never through the deferred road")
            self.assertFalse(s._reconnect_when_idle)
            self.assertEqual(s._switch_wanted, "retry upgrade")
            self.assertTrue(any("once the session is quiet" in m for m in be._logs if m.startswith("retry upgrade (web): attempt 1")), be._logs)
            self.assertEqual(be.retry_model_upgrades(now + 2 * sb.RETRY_UPGRADE_S + 2), 0, "a cadence later, still busy: no attempt stacks on the standing ask")
            self.assertEqual(s._upgrade_retry["attempts"], 1)
            s.inflight = 0
            self.assertTrue(s._try_switch_reconnect(), "the settle's try: quiet now, carried")
            self.assertEqual(len(s._asks), 1)
            self.assertGreaterEqual(s._upgrade_retry["next"], time.time() + sb.RETRY_UPGRADE_S - 5, "the next attempt counts from the carried reconnect")


if __name__ == "__main__":
    unittest.main()
