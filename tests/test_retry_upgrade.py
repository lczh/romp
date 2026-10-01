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
import sys
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


def _ring(be):
    """Rewire the backend's log to note, beside each line, whether SdkBackend._log puts it in the problems ring the
    dashboard's error center reads: by its `problem=` when one is given, else by whether an exception is being handled (a
    line logged inside an `except` block is that exception's report). Returns the list of (line, rung) pairs; be._logs
    still collects every line."""
    rung = []

    def log(msg, problem=None, key=None, ring_text=None):
        be._logs.append(msg)
        rung.append((msg, sys.exc_info()[0] is not None if problem is None else bool(problem)))
    be._log = log
    return rung


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


class _Refuses(_Cli):
    """_Cli whose set_model refuses the ids in `refuse`, raising `err`, and takes every other."""

    def __init__(self, ctx, refuse, err):
        super().__init__(ctx)
        self.refuse, self.err = tuple(refuse), err

    async def set_model(self, model=None):
        if model in self.refuse:
            raise self.err


async def _spin(n=5):
    for _ in range(n):
        await asyncio.sleep(0)


class _InOrderCli:
    """A CLI that handles control requests in the order they are written. set_model moves the model it runs at once
    (`resolves` maps a pick to the id it resolves to, None to the account default's); each context refresh reports the
    model the CLI ran when that request was written. `refresh` scripts each refresh's outcome in order: "answer", an
    exception to raise, or an asyncio.Event to hold the answer on. `log` records the requests in the order the CLI saw
    them."""

    def __init__(self, model, refresh, resolves=None):
        self.model = model
        self.refresh = list(refresh)
        self.resolves = dict(resolves or {"opus": "claude-opus-5", None: "claude-sonnet-5"})
        self.log = []

    async def set_model(self, model=None):
        self.log.append(("set_model", model))
        self.model = self.resolves.get(model, model)

    async def get_context_usage(self):
        at = self.model
        self.log.append(("refresh", at))
        how = self.refresh.pop(0) if self.refresh else "answer"
        if isinstance(how, asyncio.Event):
            await how.wait()
        elif isinstance(how, BaseException):
            raise how
        return {"percentage": 3, "model": at}


class APickOfTheUsersOwnIsNeverAFallback(unittest.TestCase):
    """(2026-09-30, the #33 follow-up) A model the user picked could be read as an automatic fallback: the pick was
    marked in flight only when the live name did not already carry the pick's family word, so claude-opus-4-8 picked on
    Opus 5.5, a Latest click (the alias `opus`) and the account default went unmarked, and when the pick's first report
    was a turn's init rather than the context refresh, the move read as Opus 5.5 falling back: a Completed card saying the
    model changed automatically and, with Retry upgrades on, a retry that reconnected the session every ten minutes (on
    the pick; only its log line named Opus 5.5), never ended by turns served on the pick. Since #33 reads versions inside
    a family, an older version and a Latest click are carded this way too; the default resolving to a lower family, and a
    pick to a lower family on the death roads, were carded before #33 as well. Every live pick is now in flight from the
    click until a report naming its family; a context refresh the CLI answered before it had the pick answers nothing for
    it; a session revived with a pick in flight starts from the pick's name, unless the pick is above the name it had; and
    a move onto a versioned pick's own model is that pick landing. A real fallback after the pick lands is still carded
    and armed, unless it lands at or above a versioned pick, where there is nothing to retry, and a pick the CLI refuses
    judges the retry again against the model it puts back. Every road drives the real
    backend: set_model, _do_set_model and _do_refresh_context against a fake client, and the learn."""

    LIVE = {"name": "web", "model": "claude-opus-5-5", "liveModel": "Opus 5.5", "liveModelId": "claude-opus-5-5"}
    ON48 = {"model": "claude-opus-4-8", "liveModel": "Opus 4.8", "liveModelId": "claude-opus-4-8"}
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
        self.assertIsNone(s._upgrade_retry, "%s: a pick of the user's own armed a retry as if the model had fallen back" % road)
        self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                         "%s: the tick reconnected the session as if the model had fallen back" % road)
        self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)
        self.assertEqual([m for m in be._logs if m.startswith("retry upgrade")], [], "%s: the retry was logged" % road)

    def _rings_once(self, rung, picked, road):
        """A refused pick is one entry in the problems ring, the refusal's own, and no line of the retry's is one: each is
        logged inside the refusal's `except` block, where a line that says nothing of its kind is classed as that
        exception's report (SdkBackend._log), and none of them is a failure."""
        self.assertEqual([m for m, p in rung if p and m.startswith("retry upgrade")], [],
                         "%s: a line of the retry's went into the problems ring as a failure" % road)
        self.assertEqual([m.split(":")[0] for m, p in rung if p], ["set_model (web -> %s) refused by the SDK" % picked],
                         "%s: the problems ring holds the refusal, once, and nothing else: %r" % (road, rung))

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
        road = "the pick's refresh answers first, the ordinary road"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            at_pick = self._pick(be, s, "claude-opus-4-8")
            self._settle(s, ["claude-opus-4-8"])
            after = (s._model_pending, s.model)
            self._init(s, "claude-opus-4-8"); self._served(s, "claude-opus-4-8")
            self._assert_nothing(be, s, h, road)
            self.assertEqual(after, ("", "Opus 4.8"), "%s: the refresh's report of the pick lands it" % road)
            self._assert_landed(be, s, "Opus 4.8", "claude-opus-4-8", at_pick, road)
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
        road = "a Latest click whose answer is lost, its refresh reporting the model before it, a later launch running it"
        with self.subTest(road=road), _Hooks() as h:
            # a lost answer says nothing about whether the CLI has the pick, so the refresh behind it cannot answer for it
            be, s = self._world()
            at_pick = self._pick(be, s, "opus")
            self._settle(s, ["claude-opus-5-5"], Exception("Control request timeout: set_model"))
            after = (s._model_pending, s.model)
            self._init(s, "claude-opus-5"); self._served(s, "claude-opus-5")   # the next launch's --model carried it
            self._assert_nothing(be, s, h, road)
            self.assertEqual(after, ("opus", "Opus 5.5"), "%s: with the CLI's answer lost, a report of the model before "
                                                          "the pick ended its switching dots" % road)
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
        for kind, pick, resolved, label, marker in picks[1:]:
            # order C: the stale answer lands before the pick's request has even reached the loop (set_model runs on the
            # kernel's thread; set_model_live hops to the loop later), so only a counter bumped AT the pick can tell it
            # from an answer to the pick. The versioned pick is closed twice already (_lands_on_pick).
            road = "%s pick, order C: the stale answer, before the pick's request leaves" % kind
            with self.subTest(pick=kind, order="C"), _Hooks() as h:
                be, s = self._world()
                seen = {}

                async def main():
                    old_ask = asyncio.Event()
                    s.client = _Cli([(old_ask, stale), RuntimeError(self.DOWN)])
                    before = asyncio.ensure_future(s._do_refresh_context())   # asked before the pick
                    await asyncio.sleep(0)
                    seen["at_pick"] = self._pick(be, s, pick)
                    old_ask.set()
                    await before                                              # answered before the hop runs
                    seen["after_stale"] = (s._model_pending, s.model, s._ctx_pct())
                    await s._do_set_model(*s._sent[-1])                       # the pick's request; its refresh raises
                    self._init(s, resolved); self._served(s, resolved)
                asyncio.run(main())
                self._assert_nothing(be, s, h, road)
                self.assertEqual(seen["after_stale"], (marker, "Opus 5.5", 37),
                                 "%s: a refresh answered before the pick's request left answered for the pick or moved "
                                 "the label (its percentage still lands)" % road)
                self._assert_landed(be, s, label, marker, seen["at_pick"], road)

    def test_a_refresh_written_before_the_picks_own_request_answers_nothing_for_it(self):
        # the pick's request reaches the CLI through a loop hop (set_model_live), so a refresh a turn's end scheduled can
        # take its first step after the pick was made and still be written before the pick's request, and a CLI that
        # answers in order reports the model before the pick. Driven through the real set_model_live on a running loop.
        # The versioned pick is closed twice already (_lands_on_pick): the roads are the Latest click and the default.
        for kind, pick, resolved, label, marker in (("alias", "opus", "claude-opus-5", "Opus 5", "opus"),
                                                   ("default", "default", "claude-sonnet-5", "Sonnet 5", "default")):
            for then in ("raises", "held"):
                road = "%s pick, the pick's own refresh %s" % (
                    kind, "raising" if then == "raises" else "answering after the next turn's init")
                with self.subTest(pick=kind, then=then), _Hooks() as h:
                    be, s = self._world(stub_live=False)
                    held = asyncio.Event()
                    cli = _InOrderCli("claude-opus-5-5", ["answer", RuntimeError(self.DOWN) if then == "raises" else held])
                    seen = {}

                    async def main():
                        s.loop, s.client = asyncio.get_running_loop(), cli
                        before = asyncio.ensure_future(s._do_refresh_context())   # scheduled at the turn's end, not yet run
                        seen["at_pick"] = self._pick(be, s, pick)                 # the drain's pick, before the loop yields
                        await _spin(10)
                        await before
                        self._init(s, resolved); self._served(s, resolved)       # the next turn, on the pick
                        held.set()
                        await _spin(10)
                    try:
                        asyncio.run(main())
                    finally:
                        s.loop = None
                    self.assertEqual(cli.log[:2], [("refresh", "claude-opus-5-5"), ("set_model", None if pick == "default" else pick)],
                                     "%s: the refresh reached the CLI before the pick's request" % road)
                    self._assert_nothing(be, s, h, road)
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
        on48 = self.ON48
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
        road = "the live model picked again, its refresh raising, a served fallback the pick's first report"
        with self.subTest(road=road), _Hooks() as h:
            # a turn the CLI opened itself can serve the pick's first report; that reply ends the pick's switching dots
            # and is not carded, so the card waits for the next report of the pick and the next reply below it: one turn
            # late, pinned here so it cannot go missing
            be, s = self._world()
            self._pick(be, s, "claude-opus-5-5")
            self._settle(s, [down])
            self._served(s, "claude-opus-4-8")
            first = (list(h.fallbacks), s._upgrade_retry)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-4-8")   # the next turn: the pick, then below it
            self.assertEqual(first, ([], None), "%s: the reply that ends the pick's switching dots is carded" % road)
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: the next reply below the pick is carded, "
                                                                      "exactly once" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: and arms the retry" % road)
        road = "no pick: a served fallback (the control)"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._served(s, "claude-opus-4-8")
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: carded" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: armed" % road)

    def test_a_real_fallback_armed_above_a_versioned_pick_ends_at_a_turn_served_on_the_pick(self):
        # the learn arms the retry from the live name, and after a versioned pick below the live model that name can
        # still sit above the pick when a real fallback lands: a reply of a turn already running on Opus 5.5 moved it
        # back after the pick landed, or the init of a turn begun before the CLI took the pick ended the pick's
        # switching dots on Opus 5.5. The retry's reconnect runs the pick, so the model it was armed from never comes
        # back, and a turn served on the pick's own version or above it ends it (the #33 follow-up's review, round
        # one). A pick that names no version keeps the old end, at the model it fell from: the controls
        down = RuntimeError(self.DOWN)
        for road in ("the pick lands, a reply of a turn already running on Opus 5.5 moves the name back, "
                     "then Sonnet 5 serves",
                     "the init of a turn begun before the CLI took the pick ends its dots on Opus 5.5, "
                     "then Sonnet 5 serves"):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world()
                self._pick(be, s, "claude-opus-4-8")
                if road.startswith("the pick lands"):
                    self._settle(s, ["claude-opus-4-8"])
                    self._served(s, "claude-opus-5-5")
                else:
                    self._init(s, "claude-opus-5-5")
                    self._settle(s, [down])
                self._served(s, "claude-sonnet-5")                       # a real fallback
                self.assertEqual(h.fallbacks, [("Opus 5.5", "Sonnet 5")], "%s: the real fallback is carded" % road)
                arm = s._upgrade_retry
                self.assertIsNotNone(arm, "%s: and armed" % road)
                self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", "Sonnet 5", "claude-opus-4-8"), road)
                now = time.time()
                self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
                self._init(s, "claude-opus-4-8"); self._served(s, "claude-sonnet-5")   # the attempt falls back again
                self.assertIsNotNone(s._upgrade_retry, "%s: a turn served below the pick ended the retry" % road)
                self._init(s, "claude-opus-4-8"); self._served(s, "claude-opus-4-8")   # a turn served on the pick
                self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick left the retry reconnecting the "
                                                    "session every ten minutes" % road)
                self.assertEqual(h.restored, [("Sonnet 5", "Opus 4.8")], "%s: the session is back on the pick" % road)
                self.assertEqual(be.retry_model_upgrades(now + 3 * sb.RETRY_UPGRADE_S), 0, "%s: no attempt after it" % road)
                self.assertEqual(len(s._asks), 1, road)
        for road, reg, pick, ctx, init_raw, low_raw, card in (
                ("a Latest click on a live Opus 5.5, answered by Opus 4.8 again", {}, "opus", ["claude-opus-5-5"],
                 "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                ("the account default resolving to Sonnet 5, answered by Haiku 4.5 again", {}, "default", [down],
                 "claude-sonnet-5", "claude-haiku-4-5", ("Sonnet 5", "Haiku 4.5"))):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**reg)
                self._pick(be, s, pick)
                self._settle(s, ctx)
                self._init(s, init_raw); self._served(s, low_raw)
                self.assertEqual(h.fallbacks, [card], "%s: carded" % road)
                self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "%s: due" % road)
                self._init(s, init_raw); self._served(s, low_raw)       # the attempt falls back to the same model
                self.assertIsNotNone(s._upgrade_retry, "%s: a pick that names no version ended the retry at the very "
                                                       "model it fell to" % road)
                self._init(s, init_raw); self._served(s, init_raw)
                self.assertIsNone(s._upgrade_retry, "%s: back on the model it fell from" % road)
                self.assertEqual(h.restored, [(card[1], card[0])], road)

    def test_a_retry_armed_under_a_pick_the_cli_then_refused_ends_only_on_the_model_its_reconnect_runs(self):
        # the pick's end of the retry reads the pick the reconnect asserts, not the one the arm recorded: a live pick
        # clears the arm, so the two differ only when a refusal puts the old model back under a standing arm (the #33
        # follow-up's review, round one). Opus 4.8 picked on Opus 5.5 and still out, a reply on Opus 5.5 ends its
        # dots, a real fallback to Sonnet 5 arms from Opus 5.5 with the pick on record, then the CLI refuses the pick:
        # the reconnect runs Opus 5.5 again, and a turn served on Opus 4.8 is a fallback from it, not the way back
        road = "Opus 4.8 picked on Opus 5.5, a fallback to Sonnet 5 armed, then the pick refused"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # a reply of the turn the pick was made in
            self._served(s, "claude-sonnet-5")                       # a real fallback
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the real fallback is armed" % road)
            self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", "Sonnet 5", "claude-opus-4-8"), road)
            self._settle(s, ["claude-opus-5-5"], lost=Exception("Unknown model: claude-opus-4-8"))   # the CLI's refusal
            self.assertEqual(s.chosen_model, "claude-opus-5-5", "%s: the refusal puts the accepted model back" % road)
            self.assertIs(s._upgrade_retry, arm, "%s: the refusal leaves the arm standing" % road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-4-8")   # the attempt falls to the refused version
            self.assertEqual(h.restored, [], "%s: a turn served on the refused pick, below the model the retry's reconnect "
                                             "runs, said the session was back" % road)
            self.assertIs(s._upgrade_retry, arm, "%s: that turn ended the retry" % road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the model it runs
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the model the reconnect runs left the retry "
                                                "standing" % road)
            self.assertEqual(h.restored, [("Sonnet 5", "Opus 5.5")], "%s: back on the model the reconnect runs" % road)

    def test_a_real_fallback_at_or_above_a_versioned_pick_arms_no_retry(self):
        # the learn arms from the live name, and after a versioned pick below the live model a reply from before the pick
        # can move that name back above the pick; a real fallback that then lands at or above the pick is carded, but
        # the session is already served at or above what was picked, so there is nothing to retry (the requested-model
        # mark and the switch flip's re-arm make the same comparison). Armed, the retry's end at the pick fired at the
        # next reply on the very model the session fell to: no attempt made, the log saying it was back after 0 attempts
        # and a "Model back on" card naming that model twice (the #33 follow-up's review, round one, third gate)
        for road, reg, pick, landed, before, low, card in (
                ("Sonnet 5 picked on Opus 5.5, the pick lands, a reply of a turn already running on Opus 5.5 moves the "
                 "name back, then a real fallback to Opus 4.8, above the pick", {}, "claude-sonnet-5", ["claude-sonnet-5"],
                 "claude-opus-5-5", "claude-opus-4-8", ("Opus 5.5", "Opus 4.8")),
                ("Opus 4.8 picked on Fable 5, the pick lands, a reply of a turn already running on Fable 5 moves the "
                 "name back, then a real fallback to Opus 5.5, above the pick",
                 {"model": "claude-fable-5", "liveModel": "Fable 5", "liveModelId": "claude-fable-5"},
                 "claude-opus-4-8", ["claude-opus-4-8"], "claude-fable-5", "claude-opus-5-5", ("Fable 5", "Opus 5.5")),
                ("Opus 4.8 picked on Opus 5.5, the init of a turn begun before the CLI took the pick ends its dots on "
                 "Opus 5.5, then a real fallback to Opus 5, between the two", {}, "claude-opus-4-8", None,
                 "claude-opus-5-5", "claude-opus-5", ("Opus 5.5", "Opus 5"))):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**reg)
                self._pick(be, s, pick)
                if landed is not None:
                    self._settle(s, landed)
                    self._served(s, before)
                else:
                    self._init(s, before)
                    self._settle(s, [RuntimeError(self.DOWN)])
                self._served(s, low)                                     # the real fallback
                armed = s._upgrade_retry
                self.assertEqual(h.fallbacks, [card], "%s: the real fallback is still carded" % road)
                self._served(s, low)                                     # the same turn's next reply, still on it
                self.assertEqual(h.restored, [], "%s: a 'Model back on' card for the very model the session fell to, "
                                                 "with no attempt made" % road)
                self.assertIsNone(armed, "%s: a fallback at or above the pick armed a retry" % road)
                self.assertIsNone(s._upgrade_retry, road)
                self.assertEqual([m for m in be._logs if m.startswith("retry upgrade")], [],
                                 "%s: the retry was logged" % road)
                self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                                 "%s: the tick reconnected the session" % road)
                self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)

    def test_a_real_fallback_below_a_versioned_pick_still_arms_and_ends_on_the_pick(self):
        # the control for the test above: the same roads, with the real fallback landing below the pick, arm from the live
        # name; an attempt that falls back to the same model leaves the arm standing, and the next turn served on the
        # pick ends it with the "back on" card
        for road, reg, pick, before, low, picked, card in (
                ("Sonnet 5 picked on Opus 5.5, a reply on Opus 5.5 moves the name back, then a real fallback to Haiku 4.5",
                 {}, "claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5", "claude-sonnet-5", ("Opus 5.5", "Haiku 4.5")),
                ("Opus 4.8 picked on Fable 5, a reply on Fable 5 moves the name back, then a real fallback to Opus 4.5, "
                 "an older version of the picked family",
                 {"model": "claude-fable-5", "liveModel": "Fable 5", "liveModelId": "claude-fable-5"},
                 "claude-opus-4-8", "claude-fable-5", "claude-opus-4-5", "claude-opus-4-8", ("Fable 5", "Opus 4.5"))):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**reg)
                self._pick(be, s, pick)
                self._settle(s, [picked])
                self._served(s, before)
                self._served(s, low)                                     # the real fallback
                self.assertEqual(h.fallbacks, [card], "%s: carded" % road)
                arm = s._upgrade_retry
                self.assertIsNotNone(arm, "%s: a fallback below the pick armed no retry" % road)
                self.assertEqual((arm["from"], arm["to"], arm["pick"]), card + (pick,), road)
                now = time.time()
                self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
                self._init(s, picked); self._served(s, low)              # the attempt falls back to the same model
                self.assertEqual(h.restored, [], "%s: a turn on the model it fell to said the session was back" % road)
                self.assertIs(s._upgrade_retry, arm, "%s: a turn served below the pick ended the retry" % road)
                self._init(s, picked); self._served(s, picked)           # a turn served on the pick
                self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick left the retry standing" % road)
                self.assertEqual(h.restored, [(card[1], sb.pretty_model(picked))], "%s: back on the pick" % road)
                self.assertEqual(be.retry_model_upgrades(now + 3 * sb.RETRY_UPGRADE_S), 0, "%s: no attempt after it" % road)
                self.assertEqual(len(s._asks), 1, road)

    def test_a_pick_the_cli_refuses_judges_the_retry_again_against_the_model_it_puts_back(self):
        # the learn judges a carded fallback's arm against the pick standing when it lands, and a live pick is what
        # otherwise clears or re-judges an arm; a refusal makes none, it puts back the model the session had. The arm
        # and the pick put back can then disagree both ways (the #33 follow-up's review, round one, fourth gate): a retry
        # standing at or above a versioned pick put back ended at the next reply on the very model the session fell to,
        # with a "Model back on" card and no attempt made, and a session left below the pick put back had the
        # requested-model mark promise a retry that nothing ran until the next turn's reply
        refused = Exception("Unknown model: claude-opus-4-8")
        stood_down = "retry upgrade (web): the pick went back to Sonnet 5"
        # the first road twice: the real fallback above the pick put back (Opus 4.5), and on it (Sonnet 5, the model the
        # session falls to), where a clear only strictly above it would leave the arm to end at the next reply on Sonnet 5
        # with a "Model back on" card and no attempt made (the #33 follow-up's review, round two); the clear's line, logged
        # inside the refusal's handling, is no failure, and the refusal stays the one entry in the problems ring
        for low, low_label in (("claude-opus-4-5", "Opus 4.5"), ("claude-sonnet-5", "Sonnet 5")):
            road = ("Sonnet 5 picked on Opus 5.5 and landed, a reply on Opus 5.5 moves the name back, Opus 4.8 picked and "
                    "its dots ended on Opus 5.5, a real fallback to %s below it, then the CLI refuses Opus 4.8" % low_label)
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world()
                rung = _ring(be)
                self._pick(be, s, "claude-sonnet-5")
                self._settle(s, ["claude-sonnet-5"])
                self._served(s, "claude-opus-5-5")                   # a reply of a turn already running
                self._pick(be, s, "claude-opus-4-8")
                self._served(s, "claude-opus-5-5")                   # ends the second pick's dots
                self._served(s, low)                                 # a real fallback, below Opus 4.8
                self.assertEqual(h.fallbacks, [("Opus 5.5", low_label)], "%s: the real fallback is carded" % road)
                arm = s._upgrade_retry
                self.assertIsNotNone(arm, "%s: and armed, below the pick" % road)
                self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", low_label, "claude-opus-4-8"), road)
                self._settle(s, ["claude-sonnet-5"], lost=refused)   # the CLI's refusal
                self.assertEqual(s.chosen_model, "claude-sonnet-5", "%s: the refusal puts the accepted model back" % road)
                after_refusal = s._upgrade_retry
                stand_downs = [m for m in be._logs if m.startswith(stood_down)]
                self._init(s, "claude-sonnet-5"); self._served(s, low)   # the next reply, still on the fallback model
                self.assertEqual(h.restored, [], "%s: a 'Model back on' card for the very model the session fell to, "
                                                 "with no attempt made" % road)
                self.assertIsNone(after_refusal, "%s: the arm stood, or was armed again, under a pick put back that the "
                                                 "session is served at or above" % road)
                self.assertEqual(len(stand_downs), 1, "%s: the retry's stand-down is logged exactly once: %r"
                                                      % (road, be._logs))
                self.assertEqual([m for m in be._logs if "back on" in m], [], "%s: the retry was logged as ending" % road)
                self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                                 "%s: the tick reconnected the session" % road)
                self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)
                self._rings_once(rung, "claude-opus-4-8", road)
        # the switch turned off under a standing retry, then a refusal: the retry stands down at the refusal's first read
        # of it, inside the refusal's handling, and that line is not a failure either
        road = ("Sonnet 5 picked on Opus 5.5 and landed, a reply on Opus 5.5 moves the name back, Opus 4.8 picked and its "
                "dots ended on Opus 5.5, a real fallback to Opus 4.5 armed below it, the switch turned off, then the CLI "
                "refuses Opus 4.8")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            rung = _ring(be)
            self._pick(be, s, "claude-sonnet-5")
            self._settle(s, ["claude-sonnet-5"])
            self._served(s, "claude-opus-5-5")                       # a reply of a turn already running
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # ends the second pick's dots
            self._served(s, "claude-opus-4-5")                       # a real fallback, below Opus 4.8
            self.assertIsNotNone(s._upgrade_retry, "%s: the real fallback is armed" % road)
            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
            mark = len(be._logs)
            self._settle(s, ["claude-sonnet-5"], lost=refused)       # the CLI's refusal
            self.assertIsNone(s._upgrade_retry, "%s: the retry outlived the switch" % road)
            self.assertEqual([m for m in be._logs[mark:] if m.startswith("retry upgrade")],
                             ["retry upgrade (web): the switch is off — standing down"],
                             "%s: the retry's lines from the refusal on" % road)
            self._rings_once(rung, "claude-opus-4-8", road)
        road = ("Opus 4.8 picked on Opus 5.5 and its dots ended on Opus 5.5, a real fallback to Opus 5 above it, then the "
                "CLI refuses Opus 4.8")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # a reply of the turn the pick was made in
            self._served(s, "claude-opus-5")                         # a real fallback, above Opus 4.8
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 5")], "%s: the real fallback is carded" % road)
            self.assertIsNone(s._upgrade_retry, "%s: at or above the pick, nothing to retry yet" % road)
            self._settle(s, ["claude-opus-5-5"], lost=refused)       # the CLI's refusal
            self.assertEqual(s.chosen_model, "claude-opus-5-5", "%s: the refusal puts the accepted model back" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
            self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", "Opus 5", "claude-opus-5-5"), road)
            mark = sb.model_fallback_row(s.chosen_model, s._served_model, "", arm, True)
            self.assertTrue(mark and mark["retry"]["armed"], "%s: the requested-model mark says a retry is armed" % road)
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self.assertEqual(len(s._asks), 1, road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the pick put back
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick put back left the retry standing" % road)
            self.assertEqual(h.restored, [("Opus 5", "Opus 5.5")], "%s: back on the pick put back" % road)
        # a retry the refusal clears falls through to the re-arm, which reads the model served now: a second hop while the
        # retry stood can leave the session below the pick put back though the model the retry fell to is not (the #33
        # follow-up's review, round one, fifth gate). Cleared and not re-armed, the session sat below its pick with the
        # requested-model mark showing and nothing asked for the pick until the next reply on the model it fell to
        road = ("Sonnet 5 picked on Opus 5.5 and landed, a reply on Opus 5.5 moves the name back, Opus 4.8 picked and its "
                "dots ended on Opus 5.5, a real fallback to Opus 4.5 armed below it, a second hop to Haiku 4.5, then the "
                "CLI refuses Opus 4.8")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "claude-sonnet-5")
            self._settle(s, ["claude-sonnet-5"])
            self._served(s, "claude-opus-5-5")                       # a reply of a turn already running
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # ends the second pick's dots
            self._served(s, "claude-opus-4-5")                       # a real fallback, below Opus 4.8
            self._served(s, "claude-haiku-4-5")                      # a second hop, below Sonnet 5 as well
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.5"), ("Opus 4.5", "Haiku 4.5")], "%s: both hops are "
                                                                                             "carded" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the first hop is armed" % road)
            self.assertEqual((arm["from"], arm["to"]), ("Opus 5.5", "Opus 4.5"), "%s: the arm stays the first hop's" % road)
            self._settle(s, ["claude-sonnet-5"], lost=refused)       # the CLI's refusal
            self.assertEqual(s.chosen_model, "claude-sonnet-5", "%s: the refusal puts the accepted model back" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
            self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Sonnet 5", "Haiku 4.5", "claude-sonnet-5"), road)
            mark = sb.model_fallback_row(s.chosen_model, s._served_model, "", arm, True)
            self.assertTrue(mark and mark["retry"]["armed"], "%s: the requested-model mark says a retry is armed" % road)
            self.assertEqual([m for m in be._logs if "nothing to retry" in m], [],
                             "%s: the log said nothing was left to retry, and the session was armed right after" % road)
            self.assertEqual(len([m for m in be._logs if m.startswith(stood_down)]), 1,
                             "%s: the cleared retry's stand-down is logged exactly once: %r" % (road, be._logs))
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self.assertEqual(len(s._asks), 1, road)
            self._init(s, "claude-sonnet-5"); self._served(s, "claude-sonnet-5")   # a turn served on the pick put back
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick put back left the retry standing" % road)
            self.assertEqual(h.restored, [("Haiku 4.5", "Sonnet 5")], "%s: back on the pick put back" % road)
        # the control: a pick put back that names no version (a Latest click) keeps a standing retry, which still ends
        # at the model it fell from
        road = ("a Latest click answered by Opus 5.5, Opus 4.8 picked and its dots ended on Opus 5.5, a real fallback to "
                "Opus 4.5 below it, then the CLI refuses Opus 4.8")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "opus")
            self._settle(s, ["claude-opus-5-5"])
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # ends the second pick's dots
            self._served(s, "claude-opus-4-5")                       # a real fallback, below Opus 4.8
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.5")], "%s: the real fallback is carded" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: and armed, below the pick" % road)
            self._settle(s, ["claude-opus-5-5"], lost=refused)       # the CLI's refusal
            self.assertEqual(s.chosen_model, "opus", "%s: the refusal puts the accepted Latest click back" % road)
            self.assertIs(s._upgrade_retry, arm, "%s: a pick put back that names no version cleared the retry" % road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the model it fell from
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the model it fell from left the retry standing" % road)
            self.assertEqual(h.restored, [("Opus 4.5", "Opus 5.5")], "%s: back on the model it fell from" % road)
        # A pick put back that had not reported yet (the #33 follow-up's review, round two). set_model blanks the served
        # model at every pick, and the revert cleared the pick put back's dots, so the re-arm read the live name from
        # before that pick and armed it as a fallback nobody had: a "fell back" log line, a reconnect ten minutes later
        # and a false "Model back on" card at its first reply. Opus 5.5 is picked on Opus 4.8 (or on Sonnet 5) and taken,
        # then Fable 5 is picked and refused before Opus 5.5 has reported; the revert now gives Opus 5.5 its dots back,
        # the re-arm stands aside for it as for any pick in flight, and the refusal's own refresh ends them.
        refused5 = Exception("Unknown model: claude-fable-5")
        down = RuntimeError(self.DOWN)
        lost = Exception("Control request timeout: set_model")   # no answer: the pick stands (_cli_refusal)
        sonnet = {"model": "claude-sonnet-5", "liveModel": "Sonnet 5", "liveModelId": "claude-sonnet-5"}

        def quiet(be, s, h, road):
            self.assertIsNone(s._upgrade_retry, "%s: the pick put back was armed as a fallback nobody had" % road)
            self.assertEqual([m for m in be._logs if m.startswith("retry upgrade")], [],
                             "%s: a fallback that never happened was logged" % road)
            self.assertEqual((s.chosen_model, s.model, s._model_pending), ("claude-opus-5-5", "Opus 5.5", ""),
                             "%s: the refusal's own refresh reports the pick put back and ends its dots" % road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                             "%s: the session was reconnected ten minutes later" % road)
            self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # the next turn, on the pick put back
            self.assertEqual(h.restored, [], "%s: a 'Model back on' card for a pick of the user's own" % road)
            self.assertEqual(h.fallbacks, [], "%s: a pick of the user's own was carded" % road)

        road = ("Opus 5.5 picked on Opus 4.8 and taken, then Fable 5 picked and refused before the refresh that follows "
                "Opus 5.5's answer has answered")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(**self.ON48)
            self._pick(be, s, "claude-opus-5-5")

            async def two_picks():
                hold = asyncio.Event()
                s.client = _Refuses([(hold, "claude-opus-5-5"), "claude-opus-5-5"], ("claude-fable-5",), refused5)
                first = asyncio.ensure_future(s._do_set_model(*s._sent[-1]))
                await _spin()                                     # Opus 5.5 taken; its refresh waits on the CLI
                self._pick(be, s, "claude-fable-5")
                await s._do_set_model(*s._sent[-1])               # Fable 5 refused while that refresh is out
                put_back = (s._upgrade_retry, s.chosen_model, s._model_pending,
                            sb.read_reg(be.state_dir, SID).get("modelPending"))
                hold.set()
                await first
                return put_back
            put_back = asyncio.run(two_picks())
            self.assertIsNone(put_back[0], "%s: the pick put back was armed as a fallback nobody had" % road)
            self.assertEqual(put_back[1:], ("claude-opus-5-5", "claude-opus-5-5", True),
                             "%s: the pick put back, not reported yet, keeps its switching dots" % road)
            quiet(be, s, h, road)
        for road, reg, first, before in (
                ("Opus 5.5 picked on Opus 4.8 and taken, its refresh failing, then Fable 5 picked and refused",
                 self.ON48, dict(ctx=[down]), None),
                ("Opus 5.5 picked on Opus 4.8, the CLI's answer to it lost, then Fable 5 picked and refused",
                 self.ON48, dict(ctx=["claude-opus-4-8"], lost=lost), None),
                # a reply of the turn already running when Opus 5.5 went live, served on the model before it: the model
                # served is then the world before the pick too, so it cannot stand in for a report of the pick
                ("Opus 5.5 picked on Sonnet 5, a reply of the turn already running served on Sonnet 5, the pick taken and "
                 "its refresh failing, then Fable 5 picked and refused", sonnet, dict(ctx=[down]), "claude-sonnet-5"),
                # and after the pick has reported, that model served stays on record until the next served reply, while
                # the re-arm reads the live name, which the pick's report moved: nothing is armed, as before
                ("Opus 5.5 picked on Opus 4.8, a reply of the turn already running served on Opus 4.8, the pick taken and "
                 "landed by its refresh, then Fable 5 picked and refused", self.ON48, dict(ctx=["claude-opus-5-5"]),
                 "claude-opus-4-8")):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**reg)
                self._pick(be, s, "claude-opus-5-5")
                if before:
                    self._served(s, before)
                self._settle(s, **first)
                self._pick(be, s, "claude-fable-5")
                self._settle(s, ["claude-opus-5-5"], lost=refused5)   # the CLI's refusal of Fable 5
                quiet(be, s, h, road)
        # the control for those roads: a fallback armed, then a pick refused with nothing served between, is armed again
        # at the refusal from the model the fallback left the live name on
        road = "a real fallback to Opus 4.8 armed on Opus 5.5, then Fable 5 picked and refused with nothing served between"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._served(s, "claude-opus-4-8")                       # a real fallback: carded and armed
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: the real fallback is carded" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: and armed" % road)
            self._pick(be, s, "claude-fable-5")                      # the pick clears the arm
            self.assertIsNone(s._upgrade_retry, road)
            self._settle(s, ["claude-opus-5-5"], lost=refused5)      # the CLI's refusal of Fable 5
            self.assertEqual(s.chosen_model, "claude-opus-5-5", "%s: the refusal puts the accepted model back" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
            self.assertEqual((arm["from"], arm["to"], arm["pick"]), ("Opus 5.5", "Opus 4.8", "claude-opus-5-5"), road)
            now = time.time()
            self.assertEqual(be.retry_model_upgrades(now + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self.assertEqual(len(s._asks), 1, road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the pick put back
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick put back left the retry standing" % road)
            self.assertEqual(h.restored, [("Opus 4.8", "Opus 5.5")], "%s: back on the pick put back" % road)
        # and the dots go back only with the pick they belong to: two picks refused in a row, the second written before the
        # first's refusal, put the model from before both back with no dots
        road = "Fable 5 then Sonnet 5 picked on Opus 5.5, both refused, the second written before the first's refusal"
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "claude-fable-5")
            self._pick(be, s, "claude-sonnet-5")
            s.client = _Cli(["claude-opus-5-5", "claude-opus-5-5"], Exception("Unknown model"))
            asyncio.run(s._do_set_model(*s._sent[0]))               # Fable 5 refused: Sonnet 5 owns every layer
            asyncio.run(s._do_set_model(*s._sent[1]))               # Sonnet 5 refused: Opus 5.5 goes back
            self.assertEqual((s.chosen_model, s._model_pending, sb.read_reg(be.state_dir, SID).get("modelPending")),
                             ("claude-opus-5-5", "", False),
                             "%s: the first refused pick's dots came back over the model from before it" % road)
        # Two picks refused together can put back a pick made before both of them that has not reported either (a later
        # check on round two's fix). Opus 5.5 is picked on Opus 4.8 and taken but has not reported, then Fable 5 and
        # Sonnet 5 are picked, Sonnet 5 written before Fable 5's answer, and the CLI refuses both. Fable 5's refusal is
        # superseded and puts back nothing, and the marker Sonnet 5 replaced names Fable 5, not Opus 5.5: kept by that
        # marker alone, Opus 5.5 lost its dots and the re-arm armed it from Opus 4.8, the name from before it, with a
        # "fell back" log line, a reconnect ten minutes later and a false "Model back on" card at its first reply. The
        # revert reads every pick still in flight when the refused pick was made
        refused2 = Exception("Unknown model")
        road = ("Opus 5.5 picked on Opus 4.8 and taken, then Fable 5 and Sonnet 5 picked together and both refused before "
                "the refresh that follows Opus 5.5's answer has answered")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(**self.ON48)
            self._pick(be, s, "claude-opus-5-5")

            async def three_picks():
                hold = asyncio.Event()
                s.client = _Refuses([(hold, "claude-opus-5-5"), "claude-opus-5-5"], ("claude-fable-5", "claude-sonnet-5"),
                                    refused2)
                first = asyncio.ensure_future(s._do_set_model(*s._sent[0]))
                await _spin()                                     # Opus 5.5 taken; its refresh waits on the CLI
                self._pick(be, s, "claude-fable-5")
                self._pick(be, s, "claude-sonnet-5")
                await s._do_set_model(*s._sent[1])                # Fable 5 refused: Sonnet 5 owns every layer
                await s._do_set_model(*s._sent[2])                # Sonnet 5 refused: Opus 5.5 goes back
                put_back = (s._upgrade_retry, s.chosen_model, s._model_pending,
                            sb.read_reg(be.state_dir, SID).get("modelPending"))
                hold.set()
                await first
                return put_back
            put_back = asyncio.run(three_picks())
            self.assertIsNone(put_back[0], "%s: the pick put back was armed as a fallback nobody had" % road)
            self.assertEqual(put_back[1:], ("claude-opus-5-5", "claude-opus-5-5", True),
                             "%s: the pick put back, not reported yet, keeps its switching dots" % road)
            quiet(be, s, h, road)
        for road, first in (
                ("Opus 5.5 picked on Opus 4.8 and taken, its refresh failing, then Fable 5 and Sonnet 5 picked together and "
                 "both refused", dict(ctx=[down])),
                ("Opus 5.5 picked on Opus 4.8, the CLI's answer to it lost, then Fable 5 and Sonnet 5 picked together and "
                 "both refused", dict(ctx=["claude-opus-4-8"], lost=lost))):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**self.ON48)
                self._pick(be, s, "claude-opus-5-5")
                self._settle(s, **first)
                self._pick(be, s, "claude-fable-5")
                self._pick(be, s, "claude-sonnet-5")
                s.client = _Cli(["claude-opus-5-5", "claude-opus-5-5"], refused2)
                asyncio.run(s._do_set_model(*s._sent[1]))           # Fable 5 refused: Sonnet 5 owns every layer
                asyncio.run(s._do_set_model(*s._sent[2]))           # Sonnet 5 refused: Opus 5.5 goes back
                quiet(be, s, h, road)
        # the control for those roads: with the model put back already reported, two picks refused together re-arm a real
        # fallback armed before them, as one refused pick does. Skipping the re-arm whenever the refused pick replaced a
        # pick in flight would keep the arm off the roads above and drop it here
        road = ("a real fallback to Opus 4.8 armed on Opus 5.5, then Fable 5 and Sonnet 5 picked together and both refused "
                "with nothing served between")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._served(s, "claude-opus-4-8")                       # a real fallback: carded and armed
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: the real fallback is carded" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: and armed" % road)
            self._pick(be, s, "claude-fable-5")                      # the pick clears the arm
            self._pick(be, s, "claude-sonnet-5")
            s.client = _Cli(["claude-opus-5-5", "claude-opus-5-5"], refused2)
            asyncio.run(s._do_set_model(*s._sent[0]))               # Fable 5 refused: Sonnet 5 owns every layer
            asyncio.run(s._do_set_model(*s._sent[1]))               # Sonnet 5 refused: Opus 5.5 goes back
            self.assertEqual(s.chosen_model, "claude-opus-5-5", "%s: the refusal puts the accepted model back" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
            self.assertEqual((arm["from"], arm["to"]), ("Opus 5.5", "Opus 4.8"), road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)
            self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the pick put back
            self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick put back left the retry standing" % road)
            self.assertEqual(h.restored, [("Opus 4.8", "Opus 5.5")], "%s: back on the pick put back" % road)
        # A reply of a turn the CLI opened itself, served on the old model in the first pick's own family, ends that pick's
        # dots before it has reported (a later check on round two's fix): a versioned pick's dots end at a report of its
        # family, and Opus 4.8 names Opus. Opus 5.5 is picked on Opus 4.8, such a reply is served on Opus 4.8, Opus 5.5 is
        # taken but has not reported (the refresh that follows its answer still out or failing, or the answer lost), then
        # Fable 5 is picked and refused. No dots stand when Fable 5 is picked, so no pick is in flight to put back with
        # them, and the re-arm read the live name, Opus 4.8, the world before Opus 5.5, and armed Opus 5.5 as a fallback
        # nobody had: a "fell back" log line, a reconnect ten minutes later and a false "Model back on" card at its first
        # reply. A versioned pick whose dots a report of another version ended is remembered until a report lands on its
        # version, and a refusal that puts it back skips the re-arm, leaving its dots ended
        road = ("Opus 5.5 picked on Opus 4.8, a reply of a turn the CLI opened itself served on Opus 4.8, the pick taken, then "
                "Fable 5 picked and refused before the refresh that follows Opus 5.5's answer has answered")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(**self.ON48)
            self._pick(be, s, "claude-opus-5-5")
            self._served(s, "claude-opus-4-8")                       # ends Opus 5.5's dots by its family word
            self.assertEqual((s.model, s._model_pending, h.fallbacks), ("Opus 4.8", "", []), "%s: the premise" % road)

            async def same_family():
                hold = asyncio.Event()
                s.client = _Refuses([(hold, "claude-opus-5-5"), "claude-opus-5-5"], ("claude-fable-5",), refused5)
                first = asyncio.ensure_future(s._do_set_model(*s._sent[-1]))
                await _spin()                                     # Opus 5.5 taken; its refresh waits on the CLI
                self._pick(be, s, "claude-fable-5")
                await s._do_set_model(*s._sent[-1])               # Fable 5 refused while that refresh is out
                put_back = (s._upgrade_retry, s.chosen_model, s._model_pending,
                            sb.read_reg(be.state_dir, SID).get("modelPending"))
                hold.set()
                await first
                return put_back
            put_back = asyncio.run(same_family())
            self.assertIsNone(put_back[0], "%s: the pick put back was armed as a fallback nobody had" % road)
            self.assertEqual(put_back[1:], ("claude-opus-5-5", "", False),
                             "%s: the pick put back, its dots ended by a report of its family, gets none back" % road)
            quiet(be, s, h, road)
        for road, first in (
                ("Opus 5.5 picked on Opus 4.8, a reply of a turn the CLI opened itself served on Opus 4.8, the pick taken and "
                 "its refresh failing, then Fable 5 picked and refused", dict(ctx=[down])),
                ("Opus 5.5 picked on Opus 4.8, a reply of a turn the CLI opened itself served on Opus 4.8, the CLI's answer "
                 "to the pick lost, then Fable 5 picked and refused", dict(ctx=["claude-opus-4-8"], lost=lost))):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**self.ON48)
                self._pick(be, s, "claude-opus-5-5")
                self._served(s, "claude-opus-4-8")                   # ends Opus 5.5's dots by its family word
                self._settle(s, **first)
                self._pick(be, s, "claude-fable-5")
                self._settle(s, ["claude-opus-5-5"], lost=refused5)   # the CLI's refusal of Fable 5
                quiet(be, s, h, road)
        road = ("Opus 5.5 picked on Opus 4.8, a reply of a turn the CLI opened itself served on Opus 4.8, the pick taken and "
                "its refresh failing, then Fable 5 and Sonnet 5 picked together and both refused")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(**self.ON48)
            self._pick(be, s, "claude-opus-5-5")
            self._served(s, "claude-opus-4-8")                       # ends Opus 5.5's dots by its family word
            self._settle(s, [down])
            self._pick(be, s, "claude-fable-5")
            self._pick(be, s, "claude-sonnet-5")
            s.client = _Cli(["claude-opus-5-5", "claude-opus-5-5"], refused2)
            asyncio.run(s._do_set_model(*s._sent[1]))               # Fable 5 refused: Sonnet 5 owns every layer
            asyncio.run(s._do_set_model(*s._sent[2]))               # Sonnet 5 refused: Opus 5.5 goes back
            quiet(be, s, h, road)
        # the controls for those roads: the memory ends when a report lands on the pick's version, and a pick whose first
        # report lands on it is never remembered (Opus 5.5 landed by its refresh, after that reply or with none, a real
        # fallback to Opus 4.8 armed, then Fable 5 refused with nothing served between, is armed again at the refusal);
        # and it is kept for a versioned pick only (a Latest click names no version, so the report of its family that ends
        # its dots is its landing: a real fallback to Sonnet 5 after it is armed again at the refusal too)
        for road, reply in (
                ("Opus 5.5 picked on Opus 4.8, a reply of a turn the CLI opened itself served on Opus 4.8, the pick landed by "
                 "its refresh, a real fallback to Opus 4.8 armed, then Fable 5 picked and refused with nothing served "
                 "between", True),
                ("Opus 5.5 picked on Opus 4.8 and landed by its refresh, a real fallback to Opus 4.8 armed, then Fable 5 "
                 "picked and refused with nothing served between", False)):
            with self.subTest(road=road), _Hooks() as h:
                be, s = self._world(**self.ON48)
                self._pick(be, s, "claude-opus-5-5")
                if reply:
                    self._served(s, "claude-opus-4-8")               # ends Opus 5.5's dots by its family word
                self._settle(s, ["claude-opus-5-5"])                 # the pick's refresh lands it
                self.assertEqual((s.model, s._model_pending), ("Opus 5.5", ""), "%s: the pick landed" % road)
                self._served(s, "claude-opus-4-8")                   # a real fallback: carded and armed
                self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: the real fallback is carded" % road)
                self.assertIsNotNone(s._upgrade_retry, "%s: and armed" % road)
                self._pick(be, s, "claude-fable-5")                  # the pick clears the arm
                self._settle(s, ["claude-opus-5-5"], lost=refused5)  # the CLI's refusal of Fable 5
                arm = s._upgrade_retry
                self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
                self.assertEqual((arm["from"], arm["to"]), ("Opus 5.5", "Opus 4.8"), road)
                self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1,
                                 "%s: due, one attempt" % road)
                self._init(s, "claude-opus-5-5"); self._served(s, "claude-opus-5-5")   # a turn served on the pick put back
                self.assertIsNone(s._upgrade_retry, "%s: a turn served on the pick put back left the retry standing" % road)
                self.assertEqual(h.restored, [("Opus 4.8", "Opus 5.5")], "%s: back on the pick put back" % road)
        road = ("a Latest click (opus) on Sonnet 5 landed by its refresh on Opus 5, a real fallback to Sonnet 5 armed, then "
                "Fable 5 picked and refused with nothing served between")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world(**sonnet)
            self._pick(be, s, "opus")
            self._settle(s, ["claude-opus-5"])                       # the pick's refresh: its family, its landing
            self.assertEqual((s.model, s._model_pending), ("Opus 5", ""), "%s: the pick landed" % road)
            self._served(s, "claude-sonnet-5")                       # a real fallback: carded and armed
            self.assertEqual(h.fallbacks, [("Opus 5", "Sonnet 5")], "%s: the real fallback is carded" % road)
            self.assertIsNotNone(s._upgrade_retry, "%s: and armed" % road)
            self._pick(be, s, "claude-fable-5")                      # the pick clears the arm
            self._settle(s, ["claude-opus-5"], lost=refused5)        # the CLI's refusal of Fable 5
            self.assertEqual(s.chosen_model, "opus", "%s: the refusal puts the accepted pick back" % road)
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the session sits below the pick put back and nothing retries it" % road)
            self.assertEqual((arm["from"], arm["to"]), ("Opus", "Sonnet 5"), road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1, "%s: due, one attempt" % road)

    def test_a_refusal_arms_nothing_for_a_newer_pick_still_in_flight(self):
        # the refusal's re-arm stands aside for a pick still in flight, as the switch flip's does. set_model runs on the
        # kernel thread, so a newer pick can land while the refusal puts the old one back, and the revert's
        # compare-and-swap then leaves the newer pick standing with its dots. Armed there, from the newer pick's name
        # against the model from before it, the retry ended at that pick's first served reply with a "Model back on"
        # card for a pick of the user's own (the #33 follow-up's review, round one, fifth gate)
        refused = Exception("Unknown model: claude-opus-4-8")
        road = ("Opus 4.8 picked on Opus 5.5 and its dots ended on Opus 5.5, then Fable 5 picked while the refusal of "
                "Opus 4.8 is being put back")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # ends the pick's dots
            self.assertIsNone(s._upgrade_retry, "%s: nothing armed before the refusal" % road)
            revert = be._revert_model

            def racing(sess, prev):
                be.set_model(SID, "claude-fable-5")                 # the kernel thread's pick, landing during the revert
                return revert(sess, prev)
            be._revert_model = racing
            self._settle(s, ["claude-opus-5-5"], lost=refused)       # the CLI's refusal of Opus 4.8
            self.assertEqual((s.chosen_model, s._model_pending), ("claude-fable-5", "claude-fable-5"),
                             "%s: the newer pick stands, still in flight" % road)
            self.assertIsNone(s._upgrade_retry, "%s: the refusal armed a retry from a pick still in flight" % road)
            self.assertEqual([m for m in be._logs if m.startswith("retry upgrade")], [], "%s: the retry was logged" % road)
            self._served(s, "claude-fable-5")                        # the newer pick's first served reply
            self.assertEqual(h.restored, [], "%s: a 'Model back on' card for a pick of the user's own" % road)
            self.assertEqual(h.fallbacks, [], "%s: a pick of the user's own was carded" % road)

    def _landing_in(self, be, s, method, read, value):
        """Wrap the session's `method` so that, while it runs, the first call to the module-level `read` (a read inside the
        window under test) lands a pick of `value`, as set_model does from the kernel thread, and then reads on. Returns a
        list that holds True once the pick landed. A read made under the session lock fails at once rather than
        deadlock on the pick."""
        landed = []
        real_read = getattr(sb, read)
        real_method = getattr(s, method)

        def landing(*a, **k):
            if not landed:
                self.assertFalse(s._lock.locked(), "the window's read runs under the session lock")
                landed.append(True)
                self.assertTrue(be.set_model(SID, value))
            return real_read(*a, **k)

        def window(*a, **k):
            with mock.patch.object(sb, read, landing):
                return real_method(*a, **k)
        setattr(s, method, window)
        return landed

    def test_a_pick_landing_between_the_refusals_check_and_its_arm_stands_the_arm_down(self):
        # the refusal's re-arm checks for a pick in flight and then arms, reading the Retry upgrades file twice in between
        # (_arm_if_below_pick, _arm_upgrade_retry); set_model runs on the kernel thread, so a pick can land in that window,
        # and the arm then stood from the world before it: the pick's first served reply ended it with a "Model back on"
        # card for a pick of the user's own (the #33 follow-up's review, round two). The arm is checked again under the
        # session lock once written, and stands down with its own log line. Neither that line nor the arm's own, both
        # logged inside the refusal's handling, is a failure: the refusal stays the one entry in the problems ring
        refused = Exception("Unknown model: claude-opus-4-8")
        road = ("Opus 4.8 picked on Opus 5.5 and its dots ended on Opus 5.5, a real fallback to Opus 5 above it, then Fable 5 "
                "picked while the refusal of Opus 4.8 arms the session below the pick put back")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            rung = _ring(be)
            self._pick(be, s, "claude-opus-4-8")
            self._served(s, "claude-opus-5-5")                       # ends the pick's dots
            self._served(s, "claude-opus-5")                         # a real fallback, above Opus 4.8
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 5")], "%s: the real fallback is carded" % road)
            self.assertIsNone(s._upgrade_retry, "%s: at or above the pick, nothing armed" % road)
            landed = self._landing_in(be, s, "_arm_if_below_pick", "retry_upgrade_on", "claude-fable-5")
            self._settle(s, ["claude-opus-5-5"], lost=refused)       # the CLI's refusal of Opus 4.8
            del s._arm_if_below_pick
            self.assertEqual(landed, [True], "%s: the re-arm never ran, so the window was never reached" % road)
            self.assertEqual((s.chosen_model, s._model_pending), ("claude-fable-5", "claude-fable-5"),
                             "%s: the newer pick stands, still in flight" % road)
            self.assertIsNone(s._upgrade_retry, "%s: the arm written from the world before the newer pick stood" % road)
            self.assertEqual(len([m for m in be._logs if m.startswith("retry upgrade (web): a newer pick (claude-fable-5)")]),
                             1, "%s: the arm's stand-down is logged exactly once: %r" % (road, be._logs))
            self._rings_once(rung, "claude-opus-4-8", road)
            self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 0,
                             "%s: the tick reconnected the session" % road)
            self.assertEqual(s._asks, [], "%s: a reconnect was asked for" % road)
            self._served(s, "claude-fable-5")                        # the newer pick's first served reply
            self.assertEqual(h.restored, [], "%s: a 'Model back on' card for a pick of the user's own" % road)

    def test_a_pick_landing_while_a_served_reply_is_judged_ends_nothing(self):
        # the retry's end reads the arm and then the current pick (_note_model_served, on the session's thread); a pick
        # landing between the two reads (set_model, on the kernel thread, clears the arm and writes the pick) had the end
        # judged against it, so a pick of the user's own at or below the model served minted a "Model back on" card. That
        # takes a pick racing the first frame of a turn the kernel did not feed (one the session opened itself, or a replay
        # after an attach). The arm and the pick are now read as one world under the session lock (the #33 follow-up's
        # review, round two)
        road = ("a real fallback to Opus 4.8 armed on Opus 5.5, then Opus 4.8 picked while the next reply on Opus 4.8 is "
                "judged against the retry")
        with self.subTest(road=road), _Hooks() as h:
            be, s = self._world()
            self._served(s, "claude-opus-4-8")                       # a real fallback: carded and armed
            arm = s._upgrade_retry
            self.assertIsNotNone(arm, "%s: the real fallback is armed" % road)
            self.assertEqual((arm["from"], arm["to"]), ("Opus 5.5", "Opus 4.8"), road)
            landed = self._landing_in(be, s, "_note_model_served", "_model_rank", "claude-opus-4-8")
            self._served(s, "claude-opus-4-8")                       # the next reply, the first frame of a turn
            del s._note_model_served
            self.assertEqual(landed, [True], "%s: the end was never judged, so the window was never reached" % road)
            self.assertEqual(s.chosen_model, "claude-opus-4-8", "%s: the pick stands" % road)
            self.assertEqual(h.restored, [], "%s: a 'Model back on' card for a pick of the user's own" % road)
            self.assertEqual([m for m in be._logs if "back on" in m], [], "%s: the retry was logged as ending" % road)
            self.assertEqual(h.fallbacks, [("Opus 5.5", "Opus 4.8")], "%s: one card, the real fallback's" % road)

    PICKS = (("versioned", "claude-opus-4-8", "claude-opus-4-8", "Opus 4.8"),
             ("alias", "opus", "claude-opus-5", "Opus"),
             ("default", "default", "claude-sonnet-5", ""),
             ("empty", "", "claude-sonnet-5", ""))      # a backend caller's empty pick is the default (set_model)

    @staticmethod
    def _revived(t):
        t.thread = _Alive()
        t._asks = []
        t.request_reconnect = lambda *a, **k: t._asks.append(a)
        return t

    def _stranded(self, be, pick, **reg):
        """The registry a pick in flight at a hard kernel death leaves behind."""
        sb.write_reg(be.state_dir, SID, dict({"sid": SID, "cwd": "/tmp", "alive": True, "lastSid": SID}, **dict(
            self.LIVE, **dict(reg, model=pick, modelPending=True))))

    @staticmethod
    def _sweep(be):
        sb.append_state(Path(be.state_dir), SID, "waiting")
        be._ensure = lambda sid, on_boot_settled=None: None
        with mock.patch.object(sb.subprocess, "run", return_value=mock.Mock(stdout="")):
            be._boot_reconcile([sb.read_reg(be.state_dir, SID)])
        del be._ensure

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
                    self._stranded(be, pick)
                    if where == "the boot sweep":
                        self._sweep(be)
                    s = self._revived(sb.SdkSession(be, sb.read_reg(be.state_dir, SID)))
                    be.sessions[SID] = s
                    revived, healed = s.model, sb.read_reg(be.state_dir, SID)
                    self._init(s, resolved); self._served(s, resolved)
                    self._assert_nothing(be, s, h, road)
                    self.assertEqual(revived, label, "%s: the session restarts from the pick's name" % road)
                    self.assertEqual((healed.get("liveModel"), healed.get("modelPending")), (label, False),
                                     "%s: the registry is healed onto the pick's name, its flag cleared" % road)
        for where in ("the session's construction", "the boot sweep"):
            road = "a row stranded with an effort switch only, its model picked long before, healed by %s" % where
            with self.subTest(where=where, pick="none in flight"):
                be = _backend()
                sb.write_reg(be.state_dir, SID, dict({"sid": SID, "cwd": "/tmp", "alive": True, "lastSid": SID},
                                                     **dict(self.LIVE, model="opus", effortPending=True)))
                if where == "the boot sweep":
                    self._sweep(be)
                s = sb.SdkSession(be, sb.read_reg(be.state_dir, SID))
                self.assertEqual((s.model, sb.read_reg(be.state_dir, SID).get("liveModel")), ("Opus 5.5", "Opus 5.5"),
                                 "%s: only a stranded MODEL pick moves the name; the one the session reported stays" % road)

    # (road, the registry before the pick, the pick, the model the CLI ran before it, the model the pick runs)
    UPWARD = (("an upward pick in the family", ON48, "claude-opus-5-5", "claude-opus-4-8", "claude-opus-5-5"),
              ("a Latest click from Sonnet", {"model": "claude-sonnet-5", "liveModel": "Sonnet 5", "liveModelId": "claude-sonnet-5"},
               "opus", "claude-sonnet-5", "claude-opus-5-5"),
              ("an upward pick to another family", {}, "fable", "claude-opus-5-5", "claude-fable-5-1"))

    def test_a_session_rebuilt_on_a_cli_still_before_an_upward_pick_reads_no_fallback(self):
        # an attach tells a surviving CLI nothing, so a session rebuilt with a pick in flight can report the model from
        # before the pick: its CLI never got the pick, or a reply from before it is replayed first (the host's acks trail
        # the stream). A pick above the old name keeps the old name, so that report changes nothing; relabeled onto the
        # pick, it read as the pick falling back, with Retry upgrades off (as here) too (the #33 follow-up's review)
        for rebuild in ("boot sweep", "construction", "idle thread death", "mid-turn thread death"):
            for road, reg, pick, old_raw, new_raw in self.UPWARD:
                for cli in ("never got the pick", "took it, a reply from before it replayed first"):
                    name = "%s, rebuilt by the %s, the CLI %s" % (road, rebuild, cli)
                    with self.subTest(rebuild=rebuild, road=road, cli=cli.split(",")[0]), _Hooks() as h, \
                            mock.patch.object(sb.SdkSession, "start", lambda self: None):
                        if rebuild.endswith("thread death"):
                            be, s = self._world(**reg)
                            Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("off")
                            self._pick(be, s, pick)
                            s.inflight = 1 if rebuild.startswith("mid-turn") else 0
                            be._on_session_gone(s)
                            if rebuild.startswith("idle"):
                                be._ensure(SID)
                            t = be.sessions.get(SID)
                            self.assertTrue(t is not None and t is not s, "%s: the session was revived" % name)
                        else:
                            be = _backend()                           # Retry upgrades off, the default
                            self._stranded(be, pick, **reg)
                            if rebuild == "boot sweep":
                                self._sweep(be)
                            t = sb.SdkSession(be, sb.read_reg(be.state_dir, SID))
                            be.sessions[SID] = t
                        revived = self._revived(t).model
                        if cli == "never got the pick":
                            self._refresh(t, RuntimeError(self.DOWN))    # the attach's refresh fails
                            self._init(t, old_raw); self._served(t, old_raw)
                        else:
                            self._served(t, old_raw)                     # the replayed reply from before the pick
                            self._init(t, new_raw); self._served(t, new_raw)
                        self._assert_nothing(be, t, h, name)
                        self.assertEqual(revived, sb.pretty_model(old_raw),
                                         "%s: a pick above the old name keeps the old name" % name)

    def test_the_re_arm_on_the_healed_name_takes_up_an_upward_pick_and_not_a_downward_one(self):
        # the re-arm (_arm_if_below_pick), called here directly on the name the boot sweep healed, as the attach calls it
        # before its refresh answers. Kept on the old name, a session below an upward pick is taken up as before this
        # change (the retry's reconnect launches on the pick, which is how a pick the surviving CLI never got reaches it)
        # and nothing is carded; a downward pick healed onto its own name has nothing to take up. The attach's own call is
        # pinned through the real connect loop in tests/test_sdk_backend.py (APickIsCarriedByALaunchNeverByAnAttach)
        for road, reg, pick, cli_raw, armed in (
                ("an upward pick the surviving CLI never got", self.ON48, "claude-opus-5-5", "claude-opus-4-8",
                 ("Opus 5.5", "Opus 4.8")),
                ("a downward pick the surviving CLI took", {}, "claude-opus-4-8", "claude-opus-4-8", None)):
            with self.subTest(road=road), _Hooks() as h:
                be = _backend()
                Path(be.state_dir, sb.RETRY_UPGRADE_STORE).write_text("on")
                self._stranded(be, pick, **reg)
                self._sweep(be)
                s = self._revived(sb.SdkSession(be, sb.read_reg(be.state_dir, SID)))
                be.sessions[SID] = s
                got = s._arm_if_below_pick()                     # the attach's re-arm, before its refresh answers
                self._refresh(s, cli_raw)                         # the attach's refresh: the surviving CLI's own report
                self._init(s, cli_raw); self._served(s, cli_raw)
                self.assertEqual(h.fallbacks, [], "%s: a pick of the user's own was carded as an automatic fallback" % road)
                if armed:
                    self.assertTrue(got, "%s: the session sits below its pick with Retry upgrades on and nothing asks "
                                         "for the pick" % road)
                    self.assertEqual((s._upgrade_retry["from"], s._upgrade_retry["to"]), armed)
                    self.assertEqual(be.retry_model_upgrades(time.time() + sb.RETRY_UPGRADE_S + 1), 1,
                                     "%s: due, the reconnect launches on the pick" % road)
                else:
                    self.assertFalse(got, "%s: armed with nothing to take up" % road)
                    self.assertIsNone(s._upgrade_retry)

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
