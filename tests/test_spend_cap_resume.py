#!/usr/bin/env python3
"""The retry after a monthly spend cap lifts (2026-11-15).

A session stopped on a spend cap is never retried while the cap stands: a retry cannot raise a cap. Until this
change nothing retried it after the cap LIFTED either. The pause cleared, on another session's output or on the
person's Resume, and every capped session stayed on its record until someone typed into it: with dozens of
sessions, dozens of prompts, for a fact the kernel can read from the transcripts.

The rules under test (_spend_resume_pass, driven by the jobs pass's autoRetry job), each with its class below:

  R1  parked: a session with a live CLI whose transcript ends on a spend-cap record; a stopped thread, a dormant
      session, one mid-compaction, one with the person's own input held or being handed over is skipped
  R2  evidence: another session on the same billing (the same login) was served a request it MADE after the record
  R3  on evidence, a parked session whose record does not answer romp's own retry gets ONE retry
  R4  a cap record that answers romp's own retry is the person's: evidence never retries it again
  R5  the person's Resume: one probe per capped billing; served, every record parked at the press on that billing
      gets its retry, R4's included; refused, the pause re-engages marked `still` and nothing else is sent
  R6  at most three retries out; a slot frees on the API's answer (an error as much as output) or the session's end;
      a press that finds the slots full keeps its probe
  R7  the person's Stop cancels the rest and holds, through the manual pause clearing itself
  R8  a kernel restart sends nothing twice and keeps the count of retries out: both come from the transcripts and
      the backend's queue
  R9  `still` rides the globalRetryPaused frame and clears with the next write of the pause (the page's half is
      rendered in ui/webview/apierror-line-browser.test.ts)
  R10 afterRetry is never a guess: a tail window that cannot settle it is widened

Synthetic fixtures only: a private synthetic sid family, the notes-api demo's session names, invented error text.
"""
import contextlib
import inspect
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

SIDS = ["99999999-aaaa-4bbb-8ccc-%012d" % i for i in range(1, 15)]     # a private synthetic family for this module
NAMES = ["web", "api", "tests", "docs", "build", "lint", "deploy", "search", "mail", "sync", "cron", "notes", "index", "stream"]
SPEND_TEXT = "API Error: 429 You have reached your monthly spend limit for this workspace."   # invented wording
OTHER_TEXT = "API Error: 529 the service is overloaded."                                      # invented wording
STREAM = 13                                                              # the session the cap never stopped, where a test has one


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % int(round((t % 1) * 1000))


def _line(kind, t, **extra):
    rec = {"type": kind, "uuid": "%s-%.3f" % (kind[:4], t), "timestamp": _iso(t)}
    rec.update(extra)
    return json.dumps(rec) + "\n"


def _stamp(line):
    return datetime.strptime(json.loads(line)["timestamp"], "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc).timestamp()


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
    """The kernel module's `time`, reading exactly the instant the test sets. Every transcript's mtime is its newest
    record's own time (_World.append), so the kernel's clock, the records' stamps and the files' mtimes are one
    timeline, as they are on a real machine: the pause's manual-lift rule compares an mtime with the pause's floor."""

    def __init__(self, at):
        self.at = float(at)

    def time(self):
        return self.at

    def __getattr__(self, name):
        return getattr(time, name)


class _Backend:
    """A backend that records each send and plays the CLI's side of the hand-over: a retry sits in the backend's queue
    (pending_queued) until the CLI takes it, and is then a prompt record in the transcript, stamped by the kernel's
    clock. `deliver` says whether the CLI takes a send at once."""

    def __init__(self, world):
        self.world = world
        self.sent = []            # the session names retried, in order
        self.queued = {}          # sid -> texts handed over and not yet in the transcript
        self.deliver = True
        self.busy_sids, self.compacting_sids = set(), set()

    def pending_queued(self, sid):
        return list(self.queued.get(sid, []))

    def busy(self, sid):
        return sid in self.busy_sids

    def compacting(self, sid):
        return sid in self.compacting_sids

    def send(self, sid, text):
        self.world.assertEqual(text, km.RETRY_MSG, "the retry text is the existing one, unchanged")
        self.sent.append(self.world.name[sid])
        self.queued.setdefault(sid, []).append(text)
        if self.deliver:
            self.land(sid)
        return True

    def land(self, sid):
        """The CLI takes what was handed over: it leaves the queue and is a prompt in the transcript."""
        for text in self.queued.pop(sid, []):
            self.world.append(sid, prompt_line(self.world.clock.at, text))


class _World(unittest.TestCase):
    """The jobs pass's pause and retry jobs, in their order, over real synthetic transcripts and a private state
    root. T is a round second; every record, every kernel instant and every file's mtime is T plus something."""

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
            self.restart()
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
        self.restart()

    def restart(self):
        """Everything the kernel holds in memory about retries is gone, as after a restart: what stands is the pause
        file, the transcripts and the backend's queue. (The names absent before this change read as empty.)"""
        for name in ("_auto_retried", "_auto_retry_state", "_spend_press", "_spend_stopped", "_spend_resume_inflight",
                     "_spend_served_seen", "_api_err_cache", "_api_last_failed_cache"):
            vars(km).get(name, {}).clear()
        vars(km).get("_spend_door", [None])[0] = None
        vars(km).get("_spend_probe_asked", [False])[0] = False
        vars(km).get("_RETRY_PAUSE_STILL", [False])[0] = False

    # ── the world's pieces ──
    def session(self, i, *lines, auth="key", **row):
        """Alive session number `i` of the family with a transcript of `lines`; its live row says a CLI serves it."""
        sid = SIDS[i]
        p = self.dir / (NAMES[i] + ".jsonl")
        p.write_text("")
        self.path[sid], self.name[sid] = str(p), NAMES[i]
        for line in lines:
            self.append(sid, line)
        self.roster = [s for s in self.roster if s["sid"] != sid] + [{"sid": sid, "name": NAMES[i], "path": str(p)}]
        self.live[sid] = dict({"state": "idle", "auth": auth, "authLive": auth, "backend": "sdk", "connected": True}, **row)
        return sid

    def capped(self, i, at=-90, **kw):
        """Session `i`, stopped on a spend cap `at` seconds from T (before the pause, by default)."""
        return self.session(i, out_line(self.T + at - 30), err_line(self.T + at), **kw)

    def refused_once(self, i, at=-90, **kw):
        """Session `i`, whose cap record answers a retry romp already sent it: the person's to move."""
        return self.session(i, out_line(self.T + at - 60), err_line(self.T + at - 30),
                            prompt_line(self.T + at - 1, km.RETRY_MSG), err_line(self.T + at), **kw)

    def serving(self, i, at=-60, **kw):
        return self.session(i, out_line(self.T + at), **kw)

    def append(self, sid, line):
        with open(self.path[sid], "a") as f:
            f.write(line)
        t = _stamp(line)                                         # the file's mtime is its newest record's own time
        os.utime(self.path[sid], (t, t))

    def serves(self, sid, at, asked=None):
        """The session is served `at` seconds from T: a tool result goes to the API `asked` seconds from T (a second
        before the answer, by default) and the answer comes back."""
        self.append(sid, tool_line(self.T + (at - 1 if asked is None else asked)))
        self.append(sid, out_line(self.T + at))

    def answer(self, sid, at, ok=True, text=SPEND_TEXT):
        """The API's answer to the session's newest prompt, `at` seconds from T: output, or an error (the cap again)."""
        self.append(sid, out_line(self.T + at) if ok else err_line(self.T + at, text))

    def cycle(self, at):
        """One jobs pass `at` seconds from T: the engages, the lift, the retry, in the pass's own order."""
        self.clock.at = self.T + at
        now = int(self.clock.at)
        km._auto_pause_on_limit()
        km._auto_pause_on_spend_limit(now, self.live)
        km._auto_resume_retry(now, self.live)
        km._auto_retry_tick(now, self.live)

    def press(self, at, paused):
        """The person's press on Stop/Resume all, through the real door."""
        self.clock.at = self.T + at
        got = []
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda raw: got.append(json.loads(raw))}
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": paused}, client)
        self.assertEqual(got, [], "a landed press sends no frame of its own")

    def press_resume(self, at):
        self.press(at, False)

    def press_stop(self, at):
        self.press(at, True)

    def paused(self):
        return km._retry_paused_on()

    def unanswered(self, sid):
        """Whether the session's newest prompt is romp's retry with no assistant record after it, read off the
        transcript here (the test's own reading, not the kernel's)."""
        for line in reversed(Path(self.path[sid]).read_text().splitlines()):
            rec = json.loads(line)
            if rec["type"] == "assistant":
                return False
            content = (rec.get("message") or {}).get("content")
            if rec["type"] == "user" and isinstance(content, list) and content and content[0].get("type") == "text":
                return content[0]["text"].endswith(km.RETRY_MSG)
        return False

    def out(self):
        """The sessions whose retry is delivered and unanswered, by name."""
        return sorted(self.name[s] for s in self.path if self.unanswered(s))

    def evidence(self, n, streamer=True):
        """`n` parked sessions and, with `streamer`, one the cap never stopped; the pause engaged; then the cap is
        raised and the streamer is served a request it made after every record. Returns the parked sids."""
        parked = [self.capped(i) for i in range(n)]
        if streamer:
            self.serving(STREAM)
        self.cycle(0)
        self.assertTrue(self.paused())
        if streamer:
            self.serves(SIDS[STREAM], 5)
        return parked


class LiftOnEvidence(_World):
    """R2 and R3: a request another session on the same billing made after the record, and was served, is the
    evidence; each parked session whose record it outranks gets one retry."""

    def test_a_lift_on_evidence_retries_each_capped_session_once(self):
        web, api = self.capped(0), self.capped(1)
        stream = self.serving(STREAM)
        self.cycle(0)
        self.assertTrue(self.paused())
        self.assertEqual(self.be.sent, [], "while the cap stands nothing is retried: a retry cannot raise it")
        self.cycle(1)
        self.assertEqual(self.be.sent, [], "and nothing on the next pass either")
        self.serves(stream, 5)                                   # the cap was raised: the key account serves again
        self.cycle(6)
        self.assertFalse(self.paused(), "the lift, on that output")
        self.assertEqual(self.be.sent, ["web", "api"], "one retry each, on the same pass as the lift")
        self.assertIsNone(km._api_error(self.path[web]), "the retry is a prompt in the session: its card clears")
        self.answer(web, 8)
        self.answer(api, 9)
        for at in (10, 11, 12):
            self.serves(SIDS[STREAM], at)
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web", "api"], "served: nothing more to retry")
        self.assertFalse(self.paused())

    def test_a_second_lift_does_not_retry_the_same_record_again(self):
        # the retry for web's record is still in the backend's hands (not yet in the transcript) when a third
        # session hits the cap, the pause comes back, and a second answer lifts it again: web's record is the same
        # record, and it has had its retry
        self.be.deliver = False                                  # the CLI has not taken the retry yet
        (web,) = self.evidence(1)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web"])
        self.assertTrue(km._api_error(self.path[web])["spendLimit"], "web still sits on the same record")
        self.session(3, out_line(self.T - 60), err_line(self.T + 7))   # a new record, after the lift's evidence
        self.cycle(8)
        self.assertTrue(self.paused(), "new information: the pause re-engages")
        self.serves(SIDS[STREAM], 12)
        self.cycle(13)
        self.assertFalse(self.paused(), "the second lift")
        self.assertEqual(self.be.sent, ["web", "docs"], "docs' record gets its one retry; web's is not retried twice")
        self.cycle(14)
        self.assertEqual(self.be.sent, ["web", "docs"])

    def test_a_request_in_flight_when_the_cap_fell_is_answered_after_it_and_retries_nobody(self):
        # the streamer asked the API before web was refused and is answered after the pause engaged. The pause's own
        # lift reads the answer's stamp and lifts, as it always did; the retry reads when the request was MADE, which
        # is before web's record, so nothing says the cap lifted for web
        self.capped(0)
        stream = self.serving(STREAM)
        self.append(stream, tool_line(self.T - 100))             # asked ten seconds before web's refusal
        self.cycle(0)
        self.append(stream, out_line(self.T + 5))                # ... and answered after the pause engaged
        self.cycle(6)
        self.assertFalse(self.paused(), "the lift's own rule is unchanged")
        self.assertEqual(self.be.sent, [], "an answer to a request made before the record is no evidence about the cap")
        self.cycle(7)
        self.assertEqual(self.be.sent, [])
        self.serves(stream, 9)                                   # a request made after it, and served
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
        self.serves(tests, 8)                                    # the same login serves
        self.cycle(9)
        self.assertEqual(self.be.sent, ["web"])

    def test_a_record_newer_than_the_evidence_is_not_retried_and_engages_the_pause(self):
        self.evidence(1)
        self.session(1, out_line(self.T - 60), err_line(self.T + 6))    # hit the cap AFTER that request
        self.cycle(7)
        # the engage ran first, over a paused file; then the lift; the retry reads api's record as newer than the request
        self.assertEqual(self.be.sent, ["web"], "only the record the request outranks")
        self.cycle(8)
        self.assertTrue(self.paused(), "api's record is new information: the pause is back")
        self.assertEqual(self.be.sent, ["web"])


class Parked(_World):
    """R1: who is parked, and who is left alone."""

    def test_suppressed_dormant_held_and_compacting_sessions_are_skipped(self):
        web = self.capped(0)                                     # a thread the person stopped
        api = self.capped(1, connected=False)                    # dormant: no CLI serves it
        docs = self.capped(3)                                    # input of the person's own is held for it
        build = self.capped(4)                                   # the one that qualifies
        deploy = self.capped(6)                                  # mid-compaction
        self.session(5, out_line(self.T - 120), err_line(self.T - 90, OTHER_TEXT))   # an ordinary error: the ladder's
        stream = self.serving(STREAM)
        self.stopped.add(web)
        self.be.compacting_sids.add(deploy)
        km._pending_ops[docs] = [("send", "carry on with the import", None)]
        self.cycle(0)
        self.serves(stream, 5)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.cycle(6)
            self.cycle(7)
        self.assertFalse(self.paused())
        self.assertEqual(sorted(self.be.sent), ["build", "lint"], "the parked session, and the ordinary error's own ladder "
                                                                  "retry; the stopped thread, the dormant session, the one "
                                                                  "with held input and the one compacting are left")
        self.assertIn("auto-retry: one retry to build after its spend cap (its billing serves again)", err.getvalue())
        # each skip lasts exactly as long as its reason holds
        self.answer(build, 8)
        self.stopped.clear()
        self.live[api]["connected"] = True
        del km._pending_ops[docs]
        self.cycle(9)
        self.assertEqual(sorted(self.be.sent), ["api", "build", "docs", "lint", "web"])
        self.be.compacting_sids.clear()
        self.answer(web, 10)
        self.cycle(11)
        self.assertEqual(sorted(self.be.sent), ["api", "build", "deploy", "docs", "lint", "web"])

    def test_a_session_already_taking_input_is_not_retried(self):
        # the held message was popped and handed over a moment ago: the backend reads busy, the transcript does not
        # show the prompt yet. A retry now would land as a second turn behind the person's own.
        (web,) = self.evidence(1)
        self.be.busy_sids.add(web)
        self.cycle(6)
        self.assertEqual((self.paused(), self.be.sent), (False, []))
        self.append(web, prompt_line(self.T + 6.5, "carry on with the import"))    # the prompt lands: its card clears
        self.be.busy_sids.clear()
        self.cycle(7)
        self.assertEqual(self.be.sent, [], "that turn was its retry")

    def test_a_codex_row_is_never_retried_here(self):
        self.capped(0, backend="codex", connected=None)
        stream = self.serving(STREAM)
        self.cycle(0)
        self.serves(stream, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, [])

    def test_a_usage_limit_pause_covers_every_parked_session(self):
        km._usage = lambda: {"limited": {"fiveHour": True}}      # the login's window is full: the limit pause engages first
        self.capped(0)
        stream = self.serving(STREAM)
        self.cycle(0)
        self.assertEqual(km._retry_pause_reason(), "limit")
        self.serves(stream, 5)                                   # the key account serves: web's record is outranked
        self.cycle(6)
        self.cycle(7)
        self.assertEqual((self.paused(), self.be.sent), (True, []), "a pause that covers the session holds its retry")
        km._usage = lambda: {"limited": None}                    # the window resets
        self.cycle(8)
        self.assertEqual(self.be.sent, ["web"], "the record is still outranked, and is retried the pass the pause lifts")


class StrictLoopBreaker(_World):
    """R4. A cap is per request: it can serve a small request and refuse a large one, so sessions the cap never stopped
    may stream beside sessions it refuses every time. A record that answers romp's own retry is the person's: nothing
    another session does retries it again."""

    def drive(self, passes, start, refused=(), streamer=True, served_by_hand=()):
        """`passes` jobs passes from `start`, two seconds apart: the streamer and every session in `served_by_hand`
        are served a request each pass, and each retry out is answered on the pass after it was delivered, refused
        for the sessions in `refused`. Returns (failed retries by session name, the pause's engages)."""
        failed, engages = {}, 0
        for k in range(passes):
            at = start + 2 * k
            for sid in list(self.path):
                if self.unanswered(sid):                         # a retry out: the API answers it
                    ok = sid not in refused
                    self.answer(sid, at - 0.5, ok=ok)
                    if not ok:
                        failed[self.name[sid]] = failed.get(self.name[sid], 0) + 1
            for sid in ([SIDS[STREAM]] if streamer else []) + list(served_by_hand):
                self.serves(sid, at - 0.25)
            before = self.paused()
            self.cycle(at)
            engages += (not before) and self.paused()
        return failed, engages

    def test_a_streaming_session_draws_one_retry_not_one_per_answer(self):
        (web,) = self.evidence(1)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web"])
        failed, engages = self.drive(30, 8, refused={web})
        self.assertEqual((self.be.sent, failed), (["web"], {"web": 1}), "one failed request in all")
        self.assertEqual(engages, 1, "the refusal engages once, the stream lifts it once, and it stays lifted")
        self.assertFalse(self.paused())
        self.assertTrue(km._api_error(self.path[web])["afterRetry"])

    def test_four_served_and_six_always_refused_cost_one_failed_retry_per_refused_session(self):
        # the six the cap refuses are retried first and the four it serves after them, so each of the four comes back
        # from a refusal AFTER the six were refused: read as evidence against the six's new records, that drew a
        # second and a third failed retry out of every one of them
        parked = self.evidence(10)
        refused = set(parked[:6])
        self.cycle(6)
        failed, engages = self.drive(120, 8, refused=refused)
        self.assertEqual(sorted(self.be.sent), sorted(NAMES[:10]), "ten parked, ten retries: one each")
        self.assertEqual(failed, {n: 1 for n in NAMES[:6]}, "exactly one failed retry per refused session")
        self.assertLessEqual(engages, len(refused), "the pause re-engages at most once per refusal, never once per pass")
        sent = list(self.be.sent)
        _failed, later = self.drive(60, 300, refused=refused)
        self.assertEqual((self.be.sent, later), (sent, 0), "and then it is quiet: no retry, no engage, pass after pass")
        self.assertFalse(self.paused())

    def test_ten_refused_and_four_rescued_by_hand_cost_no_further_retries(self):
        parked = self.evidence(10)
        self.cycle(6)
        failed, _ = self.drive(60, 8, refused=set(parked))
        self.assertEqual((sorted(self.be.sent), failed), (sorted(NAMES[:10]), {n: 1 for n in NAMES[:10]}))
        sent = list(self.be.sent)
        # the cap is raised, and the person types into four of them: they are served, and go on being served
        for sid in parked[:4]:
            self.append(sid, prompt_line(self.T + 200, "try that again"))
            self.answer(sid, 201)
        failed, engages = self.drive(120, 210, refused=set(parked[4:]), served_by_hand=parked[:4])
        self.assertEqual((self.be.sent, failed, engages), (sent, {}, 0),
                         "the six are the person's: four sessions coming back beside them retry nobody")

    def test_a_session_served_on_retry_and_refused_later_is_retried_for_its_new_record_alone(self):
        (web,) = self.evidence(1)
        self.cycle(6)
        self.answer(web, 8)                                      # served
        self.append(web, tool_line(self.T + 20))                 # ... the work goes on, and the cap falls on it again
        self.answer(web, 21, ok=False)
        self.assertFalse(km._api_error(self.path[web])["afterRetry"], "this refusal answers the work, not a retry")
        self.cycle(22)
        self.assertTrue(self.paused(), "a new record: the pause engages")
        failed, _ = self.drive(20, 24, refused={web})
        self.assertEqual((self.be.sent, failed), (["web", "web"], {"web": 1}), "one retry for the new record, then none")

    def test_the_person_s_own_failed_prompt_is_a_record_evidence_may_retry_once(self):
        (web,) = self.evidence(1)
        self.cycle(6)
        self.answer(web, 7, ok=False)
        self.append(web, prompt_line(self.T + 10, "try that again"))   # the person's own attempt, refused too
        self.answer(web, 11, ok=False)
        self.assertFalse(km._api_error(self.path[web])["afterRetry"], "this record answers the person, not romp")
        failed, _ = self.drive(20, 12, refused={web})
        self.assertEqual((self.be.sent, failed), (["web", "web"], {"web": 1}), "their message moved it: one more, then none")


class ResumeProbe(_World):
    """R5. The person's Resume is a request, not evidence: one probe per capped billing. A probe served opens the
    press for every record parked at it on that billing; a probe refused re-engages the pause, marked `still`."""

    def test_a_resume_probes_one_session_and_its_success_brings_the_rest(self):
        capped = self.evidence(5, streamer=False)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.press_resume(20)
            self.assertFalse(self.paused(), "the press lifts the pause, as it always did")
            self.cycle(21)
        self.assertEqual(self.be.sent, ["web"], "one probe")
        self.assertIn("auto-retry: Resume pressed with 5 session(s) stopped on a spend cap, on 1 billing(s)", err.getvalue())
        for at in (22, 23):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "the rest wait for its answer: the press is not evidence")
        self.answer(capped[0], 25)                               # the cap was raised: the probe is served
        self.cycle(26)
        self.assertEqual(self.be.sent, NAMES[:4], "the probe's own success opens the press: three at a time from here")
        self.assertFalse(self.paused())
        for sid in capped[1:4]:
            self.answer(sid, 27)
        self.cycle(28)
        self.assertEqual(self.be.sent, NAMES[:5])
        self.cycle(29)
        self.assertEqual(len(km._spend_press), 1, "the press stands while its last retry is unanswered")
        self.answer(capped[4], 30)
        self.cycle(31)
        self.assertEqual(km._spend_press, {}, "every record of the press has had its retry and its answer: the press is done")

    def test_a_served_probe_brings_the_records_that_answer_a_retry_too(self):
        # 'api' and 'tests' were retried once already and refused: evidence never moves them. The person's press does.
        web = self.capped(0)
        self.refused_once(1)
        self.refused_once(2)
        stream = self.serving(STREAM)
        self.cycle(0)
        self.serves(stream, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, ["web"], "evidence: the one record that answers no retry")
        self.answer(web, 7)
        for at in (8, 9, 10):
            self.serves(stream, at)
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "the other two are the person's")
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual(self.be.sent, ["web", "api"], "the press: one probe")
        self.answer(SIDS[1], 23)
        self.cycle(24)
        self.assertEqual(self.be.sent, ["web", "api", "tests"], "served: the press covers the record that answers a retry")

    def test_one_probe_per_capped_billing_and_each_billing_follows_its_own_probe(self):
        key = [self.capped(i) for i in range(3)]                             # web, api, tests bill the key
        login = [self.capped(i, auth="login") for i in (3, 4)]              # docs, build bill the login
        self.cycle(0)
        self.press_resume(20)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.cycle(21)
            self.assertEqual(self.be.sent, ["web", "docs"], "one probe on each billing")
            self.answer(key[0], 23, ok=False)                    # the key's cap still stands
            self.answer(login[0], 23)                            # the login's was raised
            self.cycle(24)
        self.assertEqual(self.be.sent, ["web", "docs", "build"], "the login's sessions follow their probe; the key's do not")
        self.assertEqual((self.paused(), km._retry_pause_reason(), km._retry_pause_bills()), (True, "spend", "key"),
                         "the pause is back for the billing whose probe was refused")
        self.assertTrue(km._RETRY_PAUSE_STILL[0])
        for at in (25, 26, 27):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web", "docs", "build"], "nothing else is sent on the refused billing")

    def test_a_failed_probe_re_engages_and_retries_nothing_else(self):
        capped = self.evidence(5, streamer=False)
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
        self.assertIn("the monthly spend limit is still in force (key billing)", err.getvalue())
        self.assertNotIn("auto-engaged", err.getvalue())
        for at in (25, 26, 27):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "one failed request, and nothing else was retried")
        self.assertEqual((self.out(), km._spend_press), ([], {}), "no retry is out, and the press is spent")

    def test_a_probe_refused_in_the_press_s_own_second_still_re_engages(self):
        # the press lands at .7 of a second, the probe goes out on the pass that follows and is refused at .9 of the
        # SAME second. Read by its stamp, in whole seconds, the refusal is older than the press and engages nothing;
        # it is a new record in the probe's session, and is read as one
        capped = self.evidence(3, streamer=False)
        self.press_resume(20.7)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.cycle(20.75)
            self.assertEqual(self.be.sent, ["web"])
            self.answer(capped[0], 20.9, ok=False)
            self.cycle(20.95)
        self.assertTrue(self.paused(), "the refusal re-engages the pause")
        self.assertTrue(km._RETRY_PAUSE_STILL[0], "marked still in force")
        self.assertIn("retry-pause: re-engaged: the retry sent to web on Resume was refused, so the monthly spend limit "
                      "is still in force (key billing)", err.getvalue())
        for at in (22, 23):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"])

    def test_a_wave_retry_refused_does_not_end_the_press_and_is_marked_too(self):
        # the probe is served, the press opens; one of the sessions it then retries is refused. That refusal re-engages
        # the pause, marked still; the press is the person's act for every record parked at it, so once the served
        # session's next answer lifts the pause the rest still get their one retry
        capped = [self.refused_once(i) for i in range(5)]       # all five answer a retry already: only a press moves them
        self.cycle(0)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual(self.be.sent, ["web"])
        self.answer(capped[0], 23)
        self.cycle(24)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.answer(capped[1], 26, ok=False)                     # refused
        self.answer(capped[2], 26)
        self.answer(capped[3], 26)
        self.cycle(27)
        self.assertEqual((self.paused(), km._RETRY_PAUSE_STILL[0]), (True, True))
        self.assertEqual(self.be.sent, NAMES[:4], "paused: nothing is sent")
        self.serves(capped[0], 29)                               # the key account goes on serving: the pause lifts
        self.cycle(30)
        self.assertEqual((self.paused(), self.be.sent), (False, NAMES[:5]), "the press still covers the last record")
        self.answer(capped[4], 31)
        for at in (32, 33, 34):
            self.serves(capped[0], at - 0.5)
            self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:5], "and the refused one is the person's again: no second retry")

    def test_a_second_resume_probes_again(self):
        # day one: the probe (web) is refused. Day two: the cap is raised and the person presses Resume again; web's
        # record answers a retry, the press covers it, and it is the probe once more
        capped = self.evidence(3, streamer=False)
        self.press_resume(20)
        self.cycle(21)
        self.answer(capped[0], 23, ok=False)
        self.cycle(24)
        self.assertTrue(self.paused())
        self.press_resume(50_000)
        self.cycle(50_001)
        self.assertEqual(self.be.sent, ["web", "web"])
        self.answer(capped[0], 50_003)
        self.cycle(50_004)
        self.assertEqual(self.be.sent, ["web", "web", "api", "tests"])

    def test_a_resume_with_no_parked_session_owes_nothing(self):
        km._set_retry_paused(True)                               # a manual stop, nobody parked
        self.serving(STREAM)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual((self.be.sent, km._spend_press, km._spend_door[0]), ([], {}, None), "the press is spent on the pass that read it")
        self.capped(0, at=30)                                    # a session parked LATER inherits no probe
        self.cycle(31)
        self.cycle(32)
        self.assertEqual(self.be.sent, [])
        self.assertTrue(self.paused())

    def test_the_door_hands_the_press_to_the_next_pass(self):
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda raw: None}
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": True}, client)
        self.assertEqual(km._spend_door[0], "stop")
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": False}, client)
        self.assertEqual(km._spend_door[0], "resume", "the newest press is the one the pass reads")


class Stagger(_World):
    """R6. Dozens of parked sessions wake a few at a time: at most SPEND_RESUME_CONCURRENCY retries are out, and a slot
    frees on the API's answer to that session, an error as much as output, never on a clock."""

    def test_the_stagger_holds(self):
        self.assertEqual(km.SPEND_RESUME_CONCURRENCY, 3, "the launch stagger's default")
        capped = self.evidence(8)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3], "the first three, in the sessions' order")
        for at in (7, 8, 9, 5_000):                              # passes go by, the clock jumps, nothing is answered
            self.cycle(at)
        self.assertEqual((self.be.sent, self.out()), (NAMES[:3], sorted(NAMES[:3])), "no answer, no next retry: time frees no slot")
        self.answer(capped[1], 5_001)                            # one answer: one slot
        self.cycle(5_002)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.cycle(5_003)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.answer(capped[0], 5_004, ok=False, text=OTHER_TEXT) # an ERROR is an answer too: an overload, the ladder's from here
        self.answer(capped[2], 5_004)
        self.answer(capped[3], 5_004)
        self.cycle(5_005)
        self.assertEqual(sorted(self.be.sent), sorted(NAMES[:7] + ["web"]), "three answers, three more (and the ladder's own "
                                                                            "retry of the overload)")
        self.assertEqual(self.out(), sorted(NAMES[4:7] + ["web"]), "three cap retries out (and the ladder's, which holds no slot here)")
        self.cycle(5_006)
        self.assertEqual(sorted(self.be.sent), sorted(NAMES[:7] + ["web"]), "the fourth waits for an answer")

    def test_a_retry_handed_over_and_not_yet_in_the_transcript_holds_its_slot(self):
        self.be.deliver = False                                  # the CLI is slow to take what it is handed
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        for at in (7, 8, 9):
            self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:3], "three in the backend's hands are three out: none sent twice, none added")
        self.clock.at = self.T + 10
        for sid in capped[:3]:
            self.be.land(sid)
        self.cycle(11)
        self.assertEqual((self.be.sent, self.out()), (NAMES[:3], sorted(NAMES[:3])), "delivered, unanswered: still out")
        self.answer(capped[0], 12)
        self.cycle(13)
        self.assertEqual(self.be.sent, NAMES[:4])

    def test_a_slot_frees_when_its_session_leaves_goes_dormant_or_is_stopped(self):
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.roster = [s for s in self.roster if s["sid"] != capped[0]]     # ended
        del self.live[capped[0]]
        self.live[capped[1]]["connected"] = False                # its CLI went away
        self.stopped.add(capped[2])                              # the person interrupted it
        self.cycle(7)
        self.assertEqual(self.be.sent, NAMES[:6], "three slots freed without an answer, three more retries")

    def test_a_press_that_finds_the_slots_full_keeps_its_probe(self):
        # three key-billed retries are out when the person presses Resume; the login's two sessions sit on records
        # that answer a retry, so only the press can move them, and its probe needs a slot
        key = [self.capped(i) for i in range(3)]
        self.refused_once(3, auth="login")
        self.refused_once(4, auth="login")
        stream = self.serving(STREAM)
        self.cycle(0)
        self.serves(stream, 5)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.press_resume(8)
            for at in (9, 10, 11):
                self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:3], "no slot, no probe yet")
        self.answer(key[0], 12)
        self.cycle(13)
        self.assertEqual(self.be.sent, NAMES[:3] + ["docs"], "a slot frees: the probe goes; the press was not spent on a full pass")
        self.assertEqual(err.getvalue().count("auto-retry: 3 retries are already out: the Resume's probe on login billing "
                                              "goes when one is answered"), 1, "said once, not once per pass\n" + err.getvalue())


class StopHolds(_World):
    """R7. "Stop all auto-retries" cancels the rest and holds. The manual pause it sets clears itself on the first
    healthy write, and a session this very wave woke makes one within seconds, so the pause alone holds nothing."""

    def test_a_stop_cancels_the_rest_of_the_wave_and_holds_through_the_pause_clearing_itself(self):
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3], "the wave: three out")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.press_stop(7)
            self.cycle(7.5)
        self.assertEqual((self.paused(), km._retry_pause_reason()), (True, ""), "the person's own stop")
        for sid in capped[:3]:                                   # the sessions the wave woke answer: healthy writes
            self.answer(sid, 8)
        self.cycle(9)
        self.assertFalse(self.paused(), "the manual pause clears itself on such a write, as it always did")
        for at in range(10, 40, 2):                              # the streamer and the woken sessions go on being served
            self.serves(SIDS[STREAM], at - 0.5)
            self.serves(capped[0], at - 0.25)
            self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:3], "and the three the wave had not reached get no retry: the Stop holds")
        self.assertFalse(self.paused())
        self.assertIn("auto-retry: Stop pressed: 3 session(s) stopped on a spend cap get no retry until a Resume or a "
                      "message of yours", err.getvalue())
        # the person's Resume lifts their own Stop: the three it held are parked on records the streamer's requests
        # outrank, so with the hold gone they get their retry (one of them as the press's probe)
        self.press_stop(41)
        self.press_resume(42)
        self.cycle(43)
        self.assertEqual(self.be.sent, NAMES[:6])
        self.assertEqual(km._spend_stopped, {})

    def test_a_stop_drops_a_resume_s_press(self):
        capped = self.evidence(4, streamer=False)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual(self.be.sent, ["web"])
        self.press_stop(22)
        self.cycle(22.5)
        self.assertEqual(km._spend_press, {})
        self.answer(capped[0], 23)                               # the probe is served after all, and clears the manual pause
        for at in (24, 25, 26):
            self.serves(capped[0], at - 0.5)
            self.cycle(at)
        self.assertEqual((self.paused(), self.be.sent), (False, ["web"]), "served or not, the Stop holds the rest")

    def test_a_held_session_the_person_writes_to_is_theirs_to_move(self):
        capped = self.evidence(4)
        self.press_stop(1)
        self.cycle(2)
        km._set_retry_paused(False)                              # (the manual pause aside: the hold's own reading is under test)
        self.cycle(6)
        self.assertEqual(self.be.sent, [], "held")
        self.append(capped[1], prompt_line(self.T + 10, "try that again"))   # their own message, refused on the cap too
        self.answer(capped[1], 11, ok=False)
        self.cycle(12)
        self.assertTrue(self.paused(), "a new record engages the pause")
        self.serves(SIDS[STREAM], 14)
        self.cycle(15)
        self.assertEqual(self.be.sent, ["api"], "the new record is not the one the Stop held: evidence retries it once")


class Restart(_World):
    """R8. Nothing the kernel remembers has to survive a restart: a session already retried is no longer parked (its
    retry is a prompt in its transcript) or has its retry in the backend's queue, and the retries out are counted
    from the same two places."""

    def test_a_restart_mid_wave_sends_nothing_twice_and_keeps_the_count(self):
        capped = self.evidence(8)
        self.be.deliver = False
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.clock.at = self.T + 6.5
        self.be.land(capped[0])                                  # web's and api's retries are prompts in their transcripts;
        self.be.land(capped[1])                                  # tests' is still in the backend's queue
        self.restart()                                           # the kernel forgets everything it held in memory
        for at in (7, 8, 9):
            self.serves(SIDS[STREAM], at - 0.5)
            self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:3], "no extra retry after the restart: three are out, and the kernel reads that")
        self.clock.at = self.T + 9.5
        self.be.land(capped[2])
        self.restart()
        self.cycle(10)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.be.deliver = True
        for i, sid in enumerate(capped[:3]):
            self.answer(sid, 11 + i)
        self.restart()
        self.cycle(15)
        self.assertEqual(self.be.sent, NAMES[:6], "answered: the next three, each once")
        for sid in capped[3:6]:
            self.answer(sid, 16)
        self.restart()
        self.cycle(17)
        self.assertEqual(self.be.sent, NAMES[:8], "eight parked, eight retries, across four restarts")

    def test_a_record_refused_before_the_restart_stays_the_person_s_after_it(self):
        (web,) = self.evidence(1)
        self.cycle(6)
        self.answer(web, 7, ok=False)
        self.restart()
        for at in range(8, 30, 2):
            self.serves(SIDS[STREAM], at - 0.5)
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "afterRetry is the transcript's own reading: a restart changes nothing")


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

    def test_a_request_from_before_a_session_landed_is_no_evidence_about_its_new_billing(self):
        # the streamer was served on the login, then moved to the key; 'web' is capped on the key. That request was the login's.
        self.capped(0)
        stream = self.serving(STREAM, auth="login")
        self.cycle(0)
        self.serves(stream, 5)
        self.live[stream].update(auth="key", authLive="key", authSwitchedAt=self.T + 6.5)
        km._set_retry_paused(False)                              # (the pause aside: the retry's own reading is under test)
        self.cycle(7)
        self.assertEqual(self.be.sent, [])
        self.serves(stream, 9)                                   # its first request on the key
        self.cycle(10)
        self.assertEqual(self.be.sent, ["web"])


class TheReadings(_World):
    """The transcript readings the rules stand on, from the pass that already reads the tail."""

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

    def test_the_served_readings_are_when_the_request_was_made_and_whether_a_retry_is_out(self):
        sid = self.session(0, out_line(self.T - 100))
        served = lambda: km._api_served_times(self.path[sid])
        self.assertEqual(served(), (0, False), "an answer with no request in sight claims nothing")
        self.append(sid, err_line(self.T - 90, OTHER_TEXT))
        self.append(sid, prompt_line(self.T - 85, km.RETRY_MSG))
        self.assertEqual(served(), (0, False), "the ladder's retry of an overload is not a cap retry: it holds no slot")
        self.append(sid, out_line(self.T - 80))
        self.assertEqual(served(), (self.T - 85, False), "asked at the prompt")
        self.append(sid, err_line(self.T - 70))
        self.assertEqual(served(), (0, False), "refused on the cap: no answer stands, and nothing is out")
        self.append(sid, prompt_line(self.T - 65, km.RETRY_MSG))
        self.assertEqual(served(), (0, True), "romp's retry of the cap record, delivered and unanswered: out")
        self.append(sid, _line("system", self.T - 64, subtype="note"))
        self.assertEqual(served(), (0, True), "a record that is no answer frees nothing")
        self.append(sid, out_line(self.T - 60))
        self.assertEqual(served(), (self.T - 65, False), "answered: served what it asked at the prompt")
        self.append(sid, tool_line(self.T - 55))
        self.append(sid, out_line(self.T - 50))
        self.assertEqual(served(), (self.T - 55, False), "the newest request moves on")
        self.assertEqual(km._api_last_output_t(self.path[sid]), self.T - 50, "the pause's reading is the answer's stamp, as before")
        self.append(sid, err_line(self.T - 40))
        self.append(sid, prompt_line(self.T - 35, km.RETRY_MSG))
        self.append(sid, err_line(self.T - 34, OTHER_TEXT))
        self.assertEqual(served(), (0, False), "an error answers a retry as much as output does")
        self.append(sid, err_line(self.T - 33))
        self.append(sid, prompt_line(self.T - 30, km.RETRY_MSG))
        self.append(sid, prompt_line(self.T - 29, "never mind, try the other route"))
        self.assertEqual(served(), (0, False), "the person's own prompt after it: theirs now")
        self.assertEqual(km._api_served_times(str(self.dir / "absent.jsonl")), (0, False))


class TailWindow(_World):
    """R10. afterRetry is never a guess. The read starts near the tail; a window that begins after the record that
    settles afterRetry (the retry prompt, or the cap record that stood when it was sent) does not decide a spend-cap
    verdict, so the read widens until the answer is the whole file's."""

    def setUp(self):
        super().setUp()
        saved = km._API_ERR_TAIL_WINDOW
        self.addCleanup(lambda: setattr(km, "_API_ERR_TAIL_WINDOW", saved))

    def _read(self, sid, window):
        km._API_ERR_TAIL_WINDOW = window
        km._api_err_cache.clear()
        km._api_last_failed_cache.clear()
        return km._api_error(self.path[sid])

    def test_the_earlier_cap_record_above_the_window_does_not_make_a_refused_retry_read_as_a_first_refusal(self):
        retry, last = prompt_line(self.T - 20, km.RETRY_MSG), err_line(self.T - 19)
        sid = self.session(0, out_line(self.T - 100), err_line(self.T - 90), retry, last)
        whole = self._read(sid, 1 << 20)
        self.assertTrue(whole["afterRetry"], "premise: read whole, the record answers a retry of a cap record")
        # the window holds the retry and its refusal, not the cap record the retry was for
        self.assertTrue(self._read(sid, len(retry) + len(last) + 10)["afterRetry"])
        # the window holds the refusal alone
        self.assertTrue(self._read(sid, len(last) + 10)["afterRetry"])
        start = os.path.getsize(self.path[sid]) - (len(last) + 10)
        self.assertEqual(km._api_error_pass(self.path[sid], start)[1], False, "that window decides nothing: the caller widens")

    def test_a_first_refusal_behind_a_long_tail_still_reads_as_one(self):
        # widening settles it both ways: nothing is assumed to answer a retry either
        big = tool_line(self.T - 20)
        last = err_line(self.T - 19)
        sid = self.session(0, prompt_line(self.T - 100, "run the import"), out_line(self.T - 90), big, last)
        for window in (len(last) + 10, len(big) + len(last) + 10, 1 << 20):
            self.assertFalse(self._read(sid, window)["afterRetry"], "window %d" % window)

    def test_every_window_agrees_with_the_whole_file(self):
        lines = [out_line(self.T - 100), err_line(self.T - 90), prompt_line(self.T - 80, km.RETRY_MSG), err_line(self.T - 79),
                 prompt_line(self.T - 70, "go on"), err_line(self.T - 69), prompt_line(self.T - 60, km.RETRY_MSG),
                 tool_line(self.T - 59), err_line(self.T - 58)]
        sid = self.session(0)
        for n in range(1, len(lines) + 1):
            Path(self.path[sid]).write_text("".join(lines[:n]))
            whole = self._read(sid, 1 << 20)
            for window in (40, 300, 700, 1500):
                self.assertEqual(self._read(sid, window), whole, "%d records, window %d" % (n, window))


class Wiring(unittest.TestCase):
    def test_the_retry_goes_through_the_one_retry_decision_with_the_existing_text(self):
        src = inspect.getsource(km._spend_resume_pass)
        self.assertIn("_fire_api_retry(sid, be, served=key)", src)
        self.assertNotIn("be.send(", src, "no send of its own: the one decision's gates and its marker stand")
        self.assertEqual(km.RETRY_MSG, "retry\n\n<!-- romp-injected -->", "the injected text is unchanged")
        tick = inspect.getsource(km._auto_retry_tick)
        self.assertIn("_spend_resume_pass(now, live_map, rows, capped)", tick, "inside the autoRetry job: no new job, no new pass")

    def test_the_frame_carries_still_and_the_page_reads_it_into_the_card_s_one_line_decider(self):
        # R9. The kernel's half: the flag rides the globalRetryPaused frame. The page's half: render.ts reads it off the
        # frame and hands it to apiCardLine, which words the cap's card and the paused line (ui/webview/api-card-line.ts);
        # the card is RENDERED with the flag set and cleared in ui/webview/apierror-line-browser.test.ts.
        src = Path(BIN, "romp-kernel").read_text()
        self.assertIn('"still": _RETRY_PAUSE_STILL[0],', src)
        self.assertIn('"reason": _retry_pause_reason()})', src)
        ui = Path(os.path.dirname(HERE), "ui", "webview")
        render = (ui / "render.ts").read_text()
        self.assertIn("globalRetryStill = m.still === true;", render, "read off every frame: it clears with the first frame that carries none")
        self.assertIn("reason: globalRetryReason, still: globalRetryStill }", render)
        line = (ui / "api-card-line.ts").read_text()
        self.assertIn('"spend limit still in force — raise it at claude.ai/settings/usage"', line)
        self.assertIn('pause.paused && pause.reason === "spend" && pause.still', line)
        self.assertIn("await pause(page, true, \"spend\", { still: true });", (ui / "apierror-line-browser.test.ts").read_text())

    def test_still_clears_with_the_next_write_of_the_pause(self):
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
            km._set_retry_paused(True, reason="spend", bills="key", still=True)
            km._set_retry_paused(True)
            self.assertFalse(km._RETRY_PAUSE_STILL[0], "and the person's Stop")
        finally:
            km.jd.STATE = saved
            km._RETRY_PAUSE_STILL[0] = False
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
