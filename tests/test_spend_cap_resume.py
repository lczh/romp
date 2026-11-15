#!/usr/bin/env python3
"""The retry after a monthly spend cap lifts (2026-11-15).

A session stopped on a spend cap is never retried while the cap stands: a retry cannot raise a cap. Until this
change nothing retried it after the cap LIFTED either. The pause cleared, on another session's output or on the
person's Resume, and every capped session stayed on its record until someone typed into it: with dozens of
sessions, dozens of prompts, for a fact the kernel can read from the transcripts.

The rule under test (_spend_resume_pass, driven by the jobs pass's autoRetry job): one retry, the existing RETRY_MSG
through the one retry decision, to each live session on a cap record the evidence outranks, once per record, a few
at a time. The evidence is another session on the same billing serving after the record, or the session's own
landing on another billing; the person's Resume is a request and earns ONE session a retry as a probe, whose own
success is the evidence that brings the rest. A record that answers a retry romp sent is outranked only by a
refused session coming back, never by a session the cap did not stop, so a cap that serves small requests and
refuses large ones cannot draw a retry per output record out of the kernel.

Synthetic fixtures only: a private synthetic sid family, the notes-api demo's session names, invented error text.
"""
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_spend_resume", os.path.join(BIN, "romp-kernel"))

SIDS = ["99999999-aaaa-4bbb-8ccc-%012d" % i for i in range(1, 13)]     # a private synthetic family for this module
NAMES = ["web", "api", "tests", "docs", "build", "lint", "deploy", "search", "mail", "sync", "cron", "notes"]
SPEND_TEXT = "API Error: 429 You have reached your monthly spend limit for this workspace."   # invented wording
OTHER_TEXT = "API Error: 529 the service is overloaded."                                      # invented wording


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % int(round((t % 1) * 1000))


def _line(kind, t, **extra):
    rec = {"type": kind, "uuid": "%s-%.3f" % (kind[:4], t), "timestamp": _iso(t)}
    rec.update(extra)
    return json.dumps(rec) + "\n"


def out_line(t):
    return _line("assistant", t, message={"role": "assistant", "content": [{"type": "text", "text": "done"}]})


def err_line(t, text=SPEND_TEXT):
    return _line("assistant", t, isApiErrorMessage=True, apiErrorStatus=429, error="billing_error",
                 message={"role": "assistant", "content": [{"type": "text", "text": text}]})


def prompt_line(t, text="please continue"):
    return _line("user", t, message={"role": "user", "content": [{"type": "text", "text": text}]})


def tool_line(t):
    return _line("user", t, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_test", "content": "ok"}]})


class _Clock:
    """The kernel module's `time`, reading exactly the instant the test sets (always ahead of the real clock, so
    the pause floor and a Resume's instant land where the synthetic records' stamps say they do)."""

    def __init__(self, at):
        self.at = float(at)

    def time(self):
        return self.at

    def __getattr__(self, name):
        return getattr(time, name)


class _Backend:
    """A backend that records each send and plays the CLI: the text reaches the session's transcript as a prompt
    record at the kernel's clock, unless the test holds deliveries back."""

    def __init__(self, world):
        self.world = world
        self.sent = []            # the session names retried, in order
        self.queued = {}          # sid -> texts still in the backend's queue
        self.deliver = True

    def pending_queued(self, sid):
        return list(self.queued.get(sid, []))

    def send(self, sid, text):
        self.world.assertEqual(text, km.RETRY_MSG, "the retry text is the existing one, unchanged")
        self.sent.append(self.world.name[sid])
        if self.deliver:
            self.world.append(sid, prompt_line(self.world.clock.at, text))
        return True


class _World(unittest.TestCase):
    """The jobs pass's pause and retry jobs, in their order, over real synthetic transcripts and a private state
    root. T is a round second ahead of the real clock; every record and every kernel instant is T plus something."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.dir = Path(self.td.name)
        (self.dir / "session-hosts").write_text("off\n")       # a test that mints its own state root pins session hosts off
        self.T = int(time.time()) + 10_000
        self.clock = _Clock(self.T)
        self.be = _Backend(self)
        self.roster, self.live, self.path, self.name = [], {}, {}, {}
        self.stopped = set()
        saved = {n: getattr(km, n) for n in ("time", "_alive_sessions", "_push_all", "_usage", "_auth_key_present", "_path_of",
                                             "_session_retry_suppressed", "_retry_suppress_unknown", "_pending_ops")}
        saved_backend, saved_state, saved_rearm = km.Sessions.__dict__["backend_for"], km.jd.STATE, km.jd.rearm_failed_summaries
        wake = km._pusher_wake.is_set()

        def restore():
            for n, v in saved.items():
                setattr(km, n, v)
            km.Sessions.backend_for, km.jd.STATE, km.jd.rearm_failed_summaries = saved_backend, saved_state, saved_rearm
            (km._pusher_wake.set if wake else km._pusher_wake.clear)()
            self._reset()
            self.td.cleanup()
        self.addCleanup(restore)
        km.time = self.clock
        km.jd.STATE = self.dir
        km.jd.rearm_failed_summaries = lambda now, **k: 0
        km._alive_sessions = lambda now, live: list(self.roster)
        km._push_all = lambda *a, **k: self.fail("a tick job built a push inline")
        km._usage = lambda: {"limited": None}                  # no usage window is involved
        km._auth_key_present = lambda: True
        km._path_of = lambda sid, now=None: self.path.get(sid)
        km._session_retry_suppressed = lambda sid: sid in self.stopped
        km._retry_suppress_unknown = lambda: False
        km._pending_ops = {}
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self._reset()

    def _reset(self):
        for name in ("_auto_retried", "_auto_retry_state", "_spend_resume_inflight", "_spend_served_seen",
                     "_api_err_cache", "_api_last_failed_cache"):
            vars(km).get(name, {}).clear()                       # (the retry's own tables are absent before the change)
        vars(km).get("_spend_probe_asked", [False])[0] = False
        vars(km).get("_RETRY_PAUSE_STILL", [False])[0] = False

    # ── the world's pieces ──
    def session(self, i, *lines, auth="key", **row):
        """Alive session number `i` of the family with a transcript of `lines`; its live row says a CLI serves it."""
        sid = SIDS[i]
        p = self.dir / (NAMES[i] + ".jsonl")
        p.write_text("".join(lines))
        self.path[sid], self.name[sid] = str(p), NAMES[i]
        self.roster = [s for s in self.roster if s["sid"] != sid] + [{"sid": sid, "name": NAMES[i], "path": str(p)}]
        self.live[sid] = dict({"state": "idle", "auth": auth, "authLive": auth, "backend": "sdk", "connected": True}, **row)
        return sid

    def capped(self, i, at=-90, **kw):
        """Session `i`, stopped on a spend cap `at` seconds from T (before the pause, by default)."""
        return self.session(i, out_line(self.T + at - 30), err_line(self.T + at), **kw)

    def serving(self, i, at=-60, **kw):
        return self.session(i, out_line(self.T + at), **kw)

    def append(self, sid, line):
        with open(self.path[sid], "a") as f:
            f.write(line)

    def serves(self, sid, at, asked=None):
        """The session is served `at` seconds from T: a tool result goes to the API `asked` seconds from T (a second
        before the answer, by default) and the answer comes back."""
        self.append(sid, tool_line(self.T + (at - 1 if asked is None else asked)))
        self.append(sid, out_line(self.T + at))

    def answer(self, sid, at, ok=True):
        """The API's answer to the session's newest prompt, `at` seconds from T: output, or the cap again."""
        self.append(sid, out_line(self.T + at) if ok else err_line(self.T + at))

    def cycle(self, at):
        """One jobs pass `at` seconds from T: the engages, the lift, the retry, in the pass's own order."""
        self.clock.at = self.T + at
        now = int(self.clock.at)
        km._auto_pause_on_limit()
        km._auto_pause_on_spend_limit(now, self.live)
        km._auto_resume_retry(now, self.live)
        km._auto_retry_tick(now, self.live)

    def press_resume(self, at):
        """The person's Resume, through the real door."""
        self.clock.at = self.T + at
        got = []
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda raw: got.append(json.loads(raw))}
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": False}, client)
        self.assertEqual(got, [], "a landed press sends no frame of its own")

    def paused(self):
        return km._retry_paused_on()


class LiftOnEvidence(_World):
    """The pause's own lift, fresh output from a session on the capped billing, is the evidence: each session
    parked on an older cap record gets one retry."""

    def test_a_lift_on_evidence_retries_each_capped_session_once(self):
        web, api = self.capped(0), self.capped(1)
        tests = self.serving(2)
        self.cycle(0)
        self.assertTrue(self.paused())
        self.assertEqual(self.be.sent, [], "while the cap stands nothing is retried: a retry cannot raise it")
        self.cycle(1)
        self.assertEqual(self.be.sent, [], "and nothing on the next pass either")
        self.serves(tests, 5)                 # the cap was raised: the key account serves again
        self.cycle(6)
        self.assertFalse(self.paused(), "the lift, on that output")
        self.assertEqual(self.be.sent, ["web", "api"], "one retry each, on the same pass as the lift")
        self.assertIsNone(km._api_error(self.path[web]), "the retry is a prompt in the session: its card clears")
        self.answer(web, 8)
        self.answer(api, 9)
        for at in (10, 11, 12):
            self.serves(tests, at)
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web", "api"], "served: nothing more to retry")
        self.assertFalse(self.paused())

    def test_a_second_lift_does_not_retry_the_same_record_again(self):
        # the retry for web's record is still in the backend's hands (not yet in the transcript) when a third
        # session hits the cap, the pause comes back, and a second output lifts it again: web's record is the same
        # record, and it has had its retry
        web = self.capped(0)
        tests = self.serving(2)
        self.cycle(0)
        self.be.deliver = False                                  # the CLI has not taken the retry yet
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web"])
        self.assertTrue(km._api_error(self.path[web])["spendLimit"], "web still sits on the same record")
        docs = self.session(3, out_line(self.T - 60), err_line(self.T + 7))   # a new record, after the lift's evidence
        self.cycle(8)
        self.assertTrue(self.paused(), "new information: the pause re-engages")
        self.serves(tests, 12)
        self.cycle(13)
        self.assertFalse(self.paused(), "the second lift")
        self.assertEqual(self.be.sent, ["web", "docs"], "docs' record gets its one retry; web's is not retried twice")
        self.cycle(14)
        self.assertEqual(self.be.sent, ["web", "docs"])

    def test_a_request_in_flight_when_the_cap_fell_is_answered_after_it_and_retries_nobody(self):
        # 'tests' asked the API before web was refused and is answered after the pause engaged. The pause's own lift
        # reads the answer's stamp and lifts, as it always did; the retry reads when the request was MADE, which is
        # before web's record, so nothing says the cap lifted for web
        self.capped(0)
        tests = self.serving(2)
        self.append(tests, tool_line(self.T - 100))              # asked ten seconds before web's refusal
        self.cycle(0)
        self.append(tests, out_line(self.T + 5))                 # ... and answered after the pause engaged
        self.cycle(6)
        self.assertFalse(self.paused(), "the lift's own rule is unchanged")
        self.assertEqual(self.be.sent, [], "an answer to a request made before the record is no evidence about the cap")
        self.cycle(7)
        self.assertEqual(self.be.sent, [])
        self.serves(tests, 9)                                    # a request made after it, and served
        self.cycle(10)
        self.assertEqual(self.be.sent, ["web"])

    def test_the_other_billing_s_output_is_no_evidence(self):
        # 'web' bills the key and sits on its cap; 'api' bills the login and streams. A login session's output
        # says nothing about the key's cap: no lift, no retry
        self.capped(0)
        api = self.serving(1, auth="login")
        for at in range(0, 6):
            self.serves(api, at + 0.5)
            self.cycle(at + 1)
        self.assertTrue(self.paused())
        self.assertEqual(self.be.sent, [])

    def test_two_logins_are_two_caps(self):
        # both bill a login, so the pause (which records the side) lifts on either's output; the retry reads
        # which login. 'web' is capped on one stored login, 'api' serves on another: no retry for web.
        self.capped(0, auth="login", authLogin="login-a")
        api = self.serving(1, auth="login", authLogin="login-b")
        tests = self.serving(2, auth="login", authLogin="login-a", at=-200)
        self.cycle(0)
        self.serves(api, 5)
        self.cycle(6)
        self.assertFalse(self.paused(), "the pause's own rule is by side, unchanged")
        self.assertEqual(self.be.sent, [], "another login's output is not evidence about this login's cap")
        self.serves(tests, 8)                 # the same login serves
        self.cycle(9)
        self.assertEqual(self.be.sent, ["web"])

    def test_a_record_newer_than_the_evidence_is_not_retried_and_engages_the_pause(self):
        self.capped(0)
        tests = self.serving(2)
        self.cycle(0)
        self.serves(tests, 5)
        self.session(1, out_line(self.T - 60), err_line(self.T + 6))    # hit the cap AFTER that answer
        self.cycle(7)
        # the engage ran first, over a paused file; then the lift; the retry reads api's record as newer than the answer
        self.assertEqual(self.be.sent, ["web"], "only the record the answer outranks")
        self.cycle(8)
        self.assertTrue(self.paused(), "api's record is new information: the pause is back")
        self.assertEqual(self.be.sent, ["web"])


class SkippedSessions(_World):
    def test_suppressed_dormant_and_held_sessions_are_skipped(self):
        web = self.capped(0)                                     # a thread the person stopped
        api = self.capped(1, connected=False)                    # dormant: no CLI serves it
        docs = self.capped(3)                                    # input of the person's own is held for it
        build = self.capped(4)                                   # the one that qualifies
        self.session(5, out_line(self.T - 120), err_line(self.T - 90, OTHER_TEXT))   # an ordinary error: the ladder's
        tests = self.serving(2)
        self.stopped.add(web)
        km._pending_ops[docs] = [("send", "carry on with the import", None)]
        self.cycle(0)
        self.serves(tests, 5)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.cycle(6)
            self.cycle(7)
        self.assertFalse(self.paused())
        self.assertEqual(sorted(self.be.sent), ["build", "lint"], "the capped session with a live CLI, and the ordinary "
                                                                  "error's own ladder retry; the stopped thread, the "
                                                                  "dormant session and the one with held input are left")
        self.assertIn("auto-retry: one retry to build after its spend cap (its billing serves again)", err.getvalue())
        # each skip lasts exactly as long as its reason holds: the stop is lifted, a CLI comes up, the held input is
        # gone from the queue
        self.answer(build, 8)
        self.stopped.clear()
        self.live[api]["connected"] = True
        del km._pending_ops[docs]
        self.cycle(9)
        self.assertEqual(sorted(self.be.sent), ["api", "build", "docs", "lint", "web"])

    def test_a_session_already_taking_input_is_not_retried(self):
        # the held message was popped and handed over a moment ago: the backend reads busy, the transcript does not
        # show the prompt yet. A retry now would land as a second turn behind the person's own.
        web = self.capped(0)
        tests = self.serving(2)
        busy = {web}
        self.be.busy = lambda sid: sid in busy
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual((self.paused(), self.be.sent), (False, []))
        self.append(web, prompt_line(self.T + 6.5, "carry on with the import"))    # the prompt lands: its card clears
        busy.clear()
        self.cycle(7)
        self.assertEqual(self.be.sent, [], "that turn was its retry")

    def test_a_codex_row_is_never_retried_here(self):
        self.capped(0, backend="codex", connected=None)
        tests = self.serving(2)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, [])

    def test_a_usage_limit_pause_or_a_manual_stop_holds_every_retry(self):
        km._usage = lambda: {"limited": {"fiveHour": True}}      # the login's window is full: the limit pause engages first
        self.capped(0)
        tests = self.serving(2)
        self.cycle(0)
        self.assertEqual(km._retry_pause_reason(), "limit")
        self.serves(tests, 5)                 # the key account serves: web's record is outranked
        self.cycle(6)
        self.cycle(7)
        self.assertEqual((self.paused(), self.be.sent), (True, []), "a pause that covers the session holds its retry")
        km._usage = lambda: {"limited": None}                    # the window resets
        self.cycle(8)
        self.assertEqual(self.be.sent, ["web"], "the record is still outranked, and is retried the pass the pause lifts")
        self.capped(1, at=-80)
        km._set_retry_paused(True)                               # the person's own Stop
        self.cycle(9)
        self.cycle(10)
        self.assertEqual(self.be.sent, ["web"], "a manual stop holds it too")
        km._set_retry_paused(False)
        self.cycle(11)
        # api's record was never ruled on by a spend lift, so with the stop gone it engages the spend pause first,
        # as any standing cap record does; the key account's next answer lifts that, and the retry follows the lift
        self.assertEqual((km._retry_pause_reason(), self.be.sent), ("spend", ["web"]))
        self.serves(tests, 12)
        self.cycle(13)
        self.assertEqual((self.paused(), self.be.sent), (False, ["web", "api"]))


class Stagger(_World):
    """Dozens of capped sessions wake a few at a time: at most SPEND_RESUME_CONCURRENCY retries are out unanswered,
    and a slot frees on the API's answer to that session, never on a clock."""

    def test_the_stagger_holds(self):
        n = km.SPEND_RESUME_CONCURRENCY
        self.assertEqual(n, 3, "the launch stagger's default")
        capped = [self.capped(i) for i in range(8)]
        tests = self.serving(9)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3], "the first three, in the sessions' order")
        for at in (7, 8, 9):                                     # passes go by, the clock moves, nothing is answered
            self.cycle(at)
        self.clock.at = self.T + 5_000
        self.cycle(5_000)
        self.assertEqual(self.be.sent, NAMES[:3], "no answer, no next retry: time frees no slot")
        self.answer(capped[1], 5_001)                            # one answer: one slot
        self.cycle(5_002)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.cycle(5_003)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.answer(capped[0], 5_004)
        self.answer(capped[2], 5_004)
        self.answer(capped[3], 5_004)
        self.cycle(5_005)
        self.assertEqual(self.be.sent, NAMES[:7], "three answers, three more")
        for i in (4, 5, 6):
            self.answer(capped[i], 5_006)
        self.cycle(5_007)
        self.assertEqual(self.be.sent, NAMES[:8], "and the last")
        self.answer(capped[7], 5_008)
        self.cycle(5_009)
        self.assertEqual((self.be.sent, km._spend_resume_inflight), (NAMES[:8], {}), "eight sessions, eight retries, no slot held")

    def test_a_slot_frees_when_its_session_leaves_goes_dormant_or_is_stopped(self):
        capped = [self.capped(i) for i in range(6)]
        tests = self.serving(9)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.roster = [s for s in self.roster if s["sid"] != capped[0]]     # ended
        del self.live[capped[0]]
        self.live[capped[1]]["connected"] = False                # its CLI went away
        self.stopped.add(capped[2])                              # the person interrupted it
        self.cycle(7)
        self.assertEqual(self.be.sent, NAMES[:6], "three slots freed without an answer, three more retries")


class ResumeProbe(_World):
    """The person's Resume is a request, not evidence: ONE capped session is retried as a probe. Its success is the
    evidence that brings the rest; its refusal is a new cap record, and the pause comes back."""

    def test_a_resume_probes_one_session_and_its_success_brings_the_rest(self):
        capped = [self.capped(i) for i in range(5)]
        self.cycle(0)
        self.assertTrue(self.paused())
        self.press_resume(20)
        self.assertFalse(self.paused(), "the press lifts the pause, as it always did")
        self.cycle(21)
        self.assertEqual(self.be.sent, ["web"], "one probe")
        for at in (22, 23):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "the rest wait for its answer: the press is not evidence")
        self.answer(capped[0], 25)                               # the cap was raised: the probe is served
        self.cycle(26)
        self.assertEqual(self.be.sent, NAMES[:4], "the probe's own success is the evidence: three at a time from here")
        self.assertFalse(self.paused())
        for sid in capped[1:4]:
            self.answer(sid, 27)
        self.cycle(28)
        self.assertEqual(self.be.sent, NAMES[:5])

    def test_a_failed_probe_re_engages_and_retries_nothing_else(self):
        capped = [self.capped(i) for i in range(5)]
        self.cycle(0)
        self.press_resume(20)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.cycle(21)
            self.assertEqual(self.be.sent, ["web"])
            self.answer(capped[0], 23, ok=False)                 # the cap still stands: the probe is refused
            self.cycle(24)
        self.assertTrue(self.paused(), "the refusal is a new record: the pause re-engages")
        self.assertEqual((km._retry_pause_reason(), km._retry_pause_bills()), ("spend", "key"))
        self.assertTrue(km._RETRY_PAUSE_STILL[0], "and it is marked: the limit is STILL in force")
        self.assertIn("retry-pause: re-engaged: the retry sent after the spend limit looked lifted was refused, so the "
                      "monthly spend limit is still in force (key billing)", err.getvalue())
        self.assertNotIn("auto-engaged", err.getvalue())
        for at in (25, 26, 27):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "one failed request, and nothing else was retried")
        self.assertEqual(km._spend_resume_inflight, {}, "the refused probe's slot is free")

    def test_a_second_resume_probes_again_and_a_served_probe_brings_the_refused_one_too(self):
        # day one: the probe (web) is refused. Day two: the cap is raised, the person presses Resume again; the
        # probe goes to the next session, is served, and web, whose record answers a retry, follows with the rest
        capped = [self.capped(i) for i in range(3)]
        self.cycle(0)
        self.press_resume(20)
        self.cycle(21)
        self.answer(capped[0], 23, ok=False)
        self.cycle(24)
        self.assertTrue(self.paused())
        self.press_resume(50_000)
        self.cycle(50_001)
        self.assertEqual(self.be.sent, ["web", "web"], "web's record is a new one, and the press asks for one probe")
        self.answer(capped[0], 50_003)
        self.cycle(50_004)
        self.assertEqual(self.be.sent, ["web", "web", "api", "tests"])

    def test_a_resume_with_no_capped_session_owes_nothing(self):
        km._set_retry_paused(True)                               # a manual stop, nobody capped
        tests = self.serving(2)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual(self.be.sent, [])
        self.assertFalse(km._spend_probe_asked[0], "the ask is spent on the pass that read it")
        self.capped(0, at=30)                                    # a session capped LATER inherits no probe
        self.cycle(31)
        self.cycle(32)
        self.assertEqual(self.be.sent, [])
        self.assertTrue(self.paused())

    def test_the_door_asks_for_the_probe_only_on_a_resume_that_landed(self):
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda raw: None}
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": True}, client)
        self.assertFalse(km._spend_probe_asked[0], "a Stop asks for nothing")
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": False}, client)
        self.assertTrue(km._spend_probe_asked[0])


class NothingLoops(_World):
    """A cap is per request: it can serve a small request and refuse a large one, so a session the cap never
    stopped may stream beside one that is refused every time. Its output earns the refused session ONE retry; the
    record that answers that retry is outranked only by a refused session coming back."""

    def test_a_streaming_session_draws_one_retry_not_one_per_output(self):
        web = self.capped(0)
        tests = self.serving(2)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web"])
        self.answer(web, 7, ok=False)                            # refused again
        engages = 0
        for at in range(8, 40, 2):                               # 'tests' streams on, an answer every other second
            self.serves(tests, at)
            before = self.paused()
            self.cycle(at + 1)
            engages += (not before) and self.paused()
        self.assertEqual(self.be.sent, ["web"], "one failed request in all: the refusal of romp's own retry is not retried")
        self.assertEqual(engages, 1, "the new record engages once, the stream lifts it once, and it stays lifted")
        self.assertFalse(self.paused())
        self.assertTrue(km._api_error(self.path[web])["afterRetry"])

    def test_a_refused_session_coming_back_outranks_a_record_that_answers_a_retry(self):
        web, api = self.capped(0), self.capped(1)
        tests = self.serving(2)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web", "api"])
        self.answer(web, 7, ok=False)                            # both refused: the cap stands for them
        self.answer(api, 7, ok=False)
        for at in (8, 10, 12):
            self.serves(tests, at)
            self.cycle(at + 1)
        self.assertEqual(self.be.sent, ["web", "api"])
        # the cap is raised and the person types into web: it is served. A session that had been refused serves again.
        self.append(web, prompt_line(self.T + 20, "try that again"))
        self.answer(web, 22)
        self.cycle(23)
        self.assertEqual(self.be.sent, ["web", "api", "api"], "api's record answers a retry, and this evidence outranks it")

    def test_a_person_s_own_failed_prompt_is_a_record_the_stream_may_retry_once(self):
        web = self.capped(0)
        tests = self.serving(2)
        self.cycle(0)
        self.serves(tests, 5)
        self.cycle(6)
        self.answer(web, 7, ok=False)
        self.append(web, prompt_line(self.T + 10, "try that again"))   # the person's own attempt, refused too
        self.answer(web, 11, ok=False)
        self.assertFalse(km._api_error(self.path[web])["afterRetry"], "this record answers the person, not romp")
        for at in (12, 14, 16, 18):
            self.serves(tests, at)
            self.cycle(at + 1)
        self.assertEqual(self.be.sent, ["web", "web"], "one more, for the new record, then none")


class BillingSwitch(_World):
    """A session whose CLI came up on another billing after its cap record: the record describes an account it
    left, and the landing is the evidence, for that session alone and under a pause that covers only the account
    it left."""

    def test_a_landing_on_another_billing_retries_that_session_under_the_other_billing_s_pause(self):
        web, api = self.capped(0, auth="login"), self.capped(1, auth="login")
        self.cycle(0)
        self.assertEqual((self.paused(), km._retry_pause_bills()), (True, "login"))
        self.live[web].update(auth="key", authLive="key", authSwitchedAt=self.T + 30.25)   # the switch lands
        self.cycle(31)
        self.assertTrue(self.paused(), "the login's cap stands, and its pause with it")
        self.assertEqual(self.be.sent, ["web"], "the session that left is retried; the one still on the login is not")
        self.cycle(32)
        self.assertEqual(self.be.sent, ["web"])

    def test_a_cap_on_the_new_billing_is_a_new_record_and_is_not_retried_on_the_landing(self):
        web = self.capped(0, auth="login")
        self.cycle(0)
        self.live[web].update(auth="key", authLive="key", authSwitchedAt=self.T + 30.25)
        self.cycle(31)
        self.assertEqual(self.be.sent, ["web"])
        self.answer(web, 33, ok=False)                           # the key account is capped too
        for at in (34, 35, 36):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"])

    def test_output_from_before_a_session_landed_is_no_evidence_about_its_new_billing(self):
        # 'tests' answered on the login, then moved to the key; 'web' is capped on the key. That answer is the login's.
        self.capped(0)
        tests = self.serving(2, auth="login")
        self.cycle(0)
        self.serves(tests, 5)
        self.live[tests].update(auth="key", authLive="key", authSwitchedAt=self.T + 6.5)
        km._set_retry_paused(False)                              # (the pause aside: the retry's own reading is under test)
        self.cycle(7)
        self.assertEqual(self.be.sent, [])
        self.serves(tests, 9)                 # its first answer on the key
        self.cycle(10)
        self.assertEqual(self.be.sent, ["web"])


class TheReadings(_World):
    """The two transcript readings the rule stands on, from the pass that already reads the tail."""

    def test_after_retry_marks_a_record_that_answers_romp_s_retry_of_a_cap_record_and_nothing_else(self):
        sid = self.session(0, out_line(self.T - 120), err_line(self.T - 110, OTHER_TEXT),
                           prompt_line(self.T - 105, km.RETRY_MSG), err_line(self.T - 90))
        self.assertFalse(km._api_error(self.path[sid])["afterRetry"],
                         "the ladder's retry of an overload, answered by the cap: this session's FIRST refusal")
        self.append(sid, prompt_line(self.T - 80, km.RETRY_MSG))
        self.append(sid, err_line(self.T - 79))
        self.assertTrue(km._api_error(self.path[sid])["afterRetry"])
        self.append(sid, err_line(self.T - 78))                  # the CLI's own second attempt of the same prompt
        self.assertTrue(km._api_error(self.path[sid])["afterRetry"], "still the answer to that retry")
        self.append(sid, prompt_line(self.T - 70, "retry the import, please"))
        self.append(sid, err_line(self.T - 69))
        self.assertFalse(km._api_error(self.path[sid])["afterRetry"], "a person's words that begin the same way are theirs")
        self.append(sid, prompt_line(self.T - 60, "[romp] This session was renamed: it is now 'web'.\n\n" + km.RETRY_MSG))
        self.append(sid, _line("user", self.T - 59.5, isMeta=True, message={"role": "user", "content": "a note the CLI added"}))
        self.append(sid, err_line(self.T - 59))
        self.assertTrue(km._api_error(self.path[sid])["afterRetry"], "a notice ahead of the retry, a meta record after it")
        self.append(sid, prompt_line(self.T - 50, km.RETRY_MSG))
        self.append(sid, out_line(self.T - 49))                  # the retry was served, and the work went on
        self.append(sid, err_line(self.T - 40))
        self.assertFalse(km._api_error(self.path[sid])["afterRetry"], "output came between: this refusal answers the work")

    def test_the_served_readings_are_when_the_answered_request_was_made(self):
        sid = self.session(0, out_line(self.T - 100))
        served = lambda: km._api_served_times(self.path[sid])
        self.assertEqual(served(), (0, 0), "an answer with no request in sight claims nothing")
        self.append(sid, err_line(self.T - 90, OTHER_TEXT))
        self.append(sid, prompt_line(self.T - 85, km.RETRY_MSG))
        self.append(sid, out_line(self.T - 80))
        self.assertEqual(served(), (self.T - 85, 0), "asked at the prompt; back from an overload, not from the cap")
        self.append(sid, err_line(self.T - 70))
        self.assertEqual(served(), (0, 0), "refused on the cap: no answer stands")
        self.append(sid, prompt_line(self.T - 65, "go on"))
        self.assertEqual(served(), (0, 0), "a prompt is not the API's answer")
        self.append(sid, out_line(self.T - 60))
        self.assertEqual(served(), (self.T - 65, self.T - 65), "refused on the cap, then served what it asked at the prompt")
        self.append(sid, tool_line(self.T - 55))
        self.append(sid, out_line(self.T - 50))
        self.assertEqual(served(), (self.T - 55, self.T - 65), "the newest request moves on; the recovery is the edge's own")
        self.assertEqual(km._api_last_output_t(self.path[sid]), self.T - 50, "the pause's reading is the answer's stamp, as before")
        self.append(sid, err_line(self.T - 40, OTHER_TEXT))
        self.assertEqual(served(), (0, 0), "an error after it: no fresh answer stands")
        self.assertEqual(km._api_served_times(str(self.dir / "absent.jsonl")), (0, 0))


class Wiring(unittest.TestCase):
    def test_the_retry_goes_through_the_one_retry_decision_with_the_existing_text(self):
        import inspect
        src = inspect.getsource(km._spend_resume_pass)
        self.assertIn("_fire_api_retry(sid, be, served=key)", src)
        self.assertNotIn("be.send(", src, "no send of its own: the one decision's gates and its marker stand")
        self.assertEqual(km.RETRY_MSG, "retry\n\n<!-- romp-injected -->", "the injected text is unchanged")
        tick = inspect.getsource(km._auto_retry_tick)
        self.assertIn("_spend_resume_pass(now, live_map, rows, capped)", tick, "inside the autoRetry job: no new job, no new pass")

    def test_the_frame_carries_still(self):
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('"still": _RETRY_PAUSE_STILL[0],', src)
        self.assertIn('"reason": _retry_pause_reason()})', src)

    def test_still_is_set_by_every_write_of_the_pause(self):
        saved = km.jd.STATE
        td = tempfile.TemporaryDirectory()
        try:
            km.jd.STATE = Path(td.name)
            km._set_retry_paused(True, reason="spend", bills="key", still=True)
            self.assertTrue(km._RETRY_PAUSE_STILL[0])
            self.assertEqual(set(json.loads((Path(td.name) / "retry-paused.json").read_text())), {"paused", "t", "reason", "bills"},
                             "memory only: the file's shape is unchanged")
            km._set_retry_paused(False)
            self.assertFalse(km._RETRY_PAUSE_STILL[0], "a lift clears it")
            km._set_retry_paused(True, reason="spend", bills="key", still=True)
            km._set_retry_paused(True, reason="limit")
            self.assertFalse(km._RETRY_PAUSE_STILL[0], "and so does a pause of another kind")
        finally:
            km.jd.STATE = saved
            km._RETRY_PAUSE_STILL[0] = False
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
