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
      gets its retry, R4's included; a retry of the press refused, the pause re-engages marked `still` and nothing
      else of that press is sent; the next write of the pause, or a restart, spends the press
  R6  at most three retries out; a slot frees on the API's answer (an error as much as output) or the session's end;
      a press that finds the slots full keeps its probe; the log names the unanswered sessions the rest wait on
  R7  the person's Stop cancels the rest and holds, through the manual pause clearing itself and through a restart
      (the door's own write carries it); only their own message to a session, or their Resume, lifts its hold
  R8  a kernel restart sends nothing twice and keeps the count of retries out: both come from the transcripts and
      the backend's queue (RealBackendHandOver drives the real backend)
  R9  `still` is per billing: it rides the status of the sessions on the billing the refused retry was on, and clears
      with the next write of the pause (the page's half is rendered in ui/webview/apierror-line-browser.test.ts)
  R10 afterRetry is never a guess: a tail window that cannot settle it is widened
  and an ANSWER is a record the model wrote in this conversation: not one the CLI writes itself (model "<synthetic>"),
  not a subagent's (isSidechain), for the evidence, for a slot's release and for the pause's own lift (RealAnswers);
  whether a session is BLOCKED, and the health latch, read every record exactly as before this change (AsOnMain)

Synthetic fixtures only: a private synthetic sid family, the notes-api demo's session names, invented error text.
"""
import asyncio
import contextlib
import errno
import inspect
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timezone
from importlib.machinery import ModuleSpec
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs
# conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()    # the real backend below keeps its transcripts here, never ~/.claude
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_spend_resume", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_spend_resume", os.path.join(BIN, "romp_sdk_backend.py"))

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


def out_line(t, **extra):
    return _line("assistant", t, message={"role": "assistant", "model": "claude-test-1", "content": [{"type": "text", "text": "done"}]}, **extra)


def synthetic_line(t):
    """The record the CLI writes itself after a stop: no request was served to produce it."""
    return _line("assistant", t, message={"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": "No response requested."}]})


def err_line(t, text=SPEND_TEXT, **extra):
    return _line("assistant", t, isApiErrorMessage=True, apiErrorStatus=429, error="billing_error",
                 message={"role": "assistant", "model": "<synthetic>", "content": [{"type": "text", "text": text}]}, **extra)


def prompt_line(t, text="please continue", **extra):
    return _line("user", t, message={"role": "user", "content": [{"type": "text", "text": text}]}, **extra)


NUDGE = "Where does this stand?\n\n<!-- romp-injected -->"     # a prompt romp put into the session: not the person's


def tool_line(t, **extra):
    return _line("user", t, message={"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_test", "content": "ok"}]}, **extra)


def stop_line(t):
    """The CLI's own stop record, written for the person's Esc and for a turn a restart cut alike."""
    return prompt_line(t, "[Request interrupted by user]")


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
        for name in ("_auto_retried", "_auto_retry_state", "_spend_press", "_spend_stopped", "_api_err_cache", "_api_last_failed_cache"):
            vars(km).get(name, {}).clear()
        for name, empty in (("_spend_door", None), ("_spend_wait_said", None), ("_spend_press_seq", 0), ("_RETRY_PAUSE_STILL", None),
                            ("_RETRY_PAUSE_SEQ", 0), ("_spend_doc_fault", False)):
            vars(km).get(name, [None])[0] = empty

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
            if rec.get("isSidechain"):
                continue                                         # a subagent's record
            if rec["type"] == "assistant":
                if rec["message"].get("model") == "<synthetic>" and not rec.get("isApiErrorMessage"):
                    continue                                     # the CLI's own record
                return False                                     # the model's answer, or an API error
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


class RealAnswers(_World):
    """An answer is a record the MODEL wrote in this conversation. The CLI writes records of its own (model "<synthetic>":
    "No response requested." after a stop), and older transcripts hold a subagent's records beside the conversation's
    (isSidechain). Neither says the API served this billing a request, and neither answers a retry."""

    def test_a_record_the_cli_wrote_itself_lifts_no_pause_and_retries_nobody(self):
        # a hard cap: nothing is served. An idle session on the same billing is interrupted, and the CLI writes its
        # settle. Read as output, that one record lifted the pause and sent three retries into the standing cap.
        self.evidence(3, streamer=False)
        idle = self.serving(STREAM)
        self.append(idle, stop_line(self.T + 4))
        self.append(idle, synthetic_line(self.T + 5))
        for at in (6, 7, 8):
            self.cycle(at)
        self.assertTrue(self.paused(), "the pause's own lift reads a model's answer, not the CLI's settle")
        self.assertEqual(self.be.sent, [])
        self.assertEqual((km._api_last_output_t(self.path[idle]), km._api_served_times(self.path[idle])), (self.T - 60, (0, False, 0.0)),
                         "the newest answer is still the one before it, no request stands served, and nobody wrote")

    def test_a_subagent_s_record_is_another_conversation_s(self):
        (web,) = self.evidence(1, streamer=False)
        idle = self.serving(STREAM)
        # a subagent's request and answer in an idle session on the capped billing: no lift, no evidence
        self.append(idle, tool_line(self.T + 4, isSidechain=True))
        self.append(idle, out_line(self.T + 5, isSidechain=True))
        for at in (6, 7, 8):
            self.cycle(at)
        self.assertEqual((self.paused(), self.be.sent), (True, []))
        self.assertEqual((km._api_last_output_t(self.path[idle]), km._api_served_times(self.path[idle])), (self.T - 60, (0, False, 0.0)))
        # ... and in the capped session's own transcript. Whether that session is BLOCKED is not this change's to
        # redefine (AsOnMain): it reads as it always did, moved on. What this change reads still passes over the
        # record: it lifts no pause and is nobody's evidence
        self.append(web, tool_line(self.T + 9, isSidechain=True))
        self.append(web, out_line(self.T + 10, isSidechain=True))
        self.assertIsNone(km._api_error(self.path[web]))
        for at in (11, 12, 13):
            self.cycle(at)
        self.assertEqual((self.paused(), self.be.sent), (True, []))
        self.assertEqual((km._api_last_output_t(self.path[web]), km._api_served_times(self.path[web])), (0, (0, False, 0.0)))

    def test_only_a_model_s_answer_or_an_api_error_frees_a_slot(self):
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.append(capped[0], synthetic_line(self.T + 7))                    # the CLI's own record
        self.append(capped[1], out_line(self.T + 7, isSidechain=True))        # a subagent's
        self.append(capped[2], tool_line(self.T + 7))                         # a tool result
        self.cycle(8)
        self.assertEqual((self.be.sent, self.out()), (NAMES[:3], sorted(NAMES[:3])), "none of the three is an answer: three out still")
        self.answer(capped[0], 9)                                             # the model's answer
        self.answer(capped[1], 9, ok=False, text=OTHER_TEXT)                  # an API error
        self.cycle(10)
        self.assertEqual(sorted(self.be.sent), sorted(NAMES[:5] + ["api"]), "two answers, two more (and the ladder's retry of the overload)")

    def test_three_sessions_that_never_answer_hold_every_slot_and_the_log_names_them(self):
        capped = self.evidence(6)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.cycle(6)
            for at in range(7, 27):                              # twenty passes: only records that are no answer arrive
                self.append(capped[at % 3], _line("system", self.T + at - 0.5, subtype="note"))
                self.serves(SIDS[STREAM], at - 0.25)
                self.cycle(at)
        self.assertEqual(self.be.sent, NAMES[:3])
        line = "auto-retry: retries after the spend cap are waiting on 3 unanswered session(s): web, api, tests\n"
        self.assertEqual(err.getvalue().count(line), 1, "said once for the set, not once a pass\n" + err.getvalue())
        # a slot frees when its session ends, loses its CLI or is stopped; the set changes, and the log says the new one
        self.live[capped[0]]["connected"] = False
        with contextlib.redirect_stderr(err):
            self.cycle(28)
            self.cycle(29)
        self.assertEqual(self.be.sent, NAMES[:4])
        self.assertEqual(err.getvalue().count("waiting on 3 unanswered session(s): api, tests, docs\n"), 1, err.getvalue())


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

    def test_romp_s_own_prompts_do_not_reopen_a_refused_record(self):
        # a nudge, a notice, another session's mail: prompts the PERSON did not write. Each one opens a turn that the
        # cap refuses, and each refusal is a new record; read as "a prompt other than the retry", five of them drew
        # five more retries. The refusal that follows romp's own prompt still answers no one who asked.
        (web,) = self.evidence(1)
        self.cycle(6)
        self.answer(web, 7, ok=False)
        self.assertTrue(km._api_error(self.path[web])["afterRetry"])
        for k in range(5):
            at = 10 + 4 * k
            self.append(web, prompt_line(self.T + at, NUDGE))
            self.answer(web, at + 1, ok=False)
            self.assertTrue(km._api_error(self.path[web])["afterRetry"], "romp's own prompt: the record is the person's still")
            self.serves(SIDS[STREAM], at + 2)
            self.cycle(at + 3)
        self.assertEqual(self.be.sent, ["web"], "five nudges, five refusals, no further retry")
        # mail from another session (the CLI's own stamp says a peer wrote it) reopens nothing either
        self.append(web, prompt_line(self.T + 40, "the import is unblocked on my side", origin={"kind": "peer"}))
        self.answer(web, 41, ok=False)
        self.assertTrue(km._api_error(self.path[web])["afterRetry"])
        # the person's own message does
        self.append(web, prompt_line(self.T + 50, "try that again", promptSource="sdk"))
        self.answer(web, 51, ok=False)
        self.assertFalse(km._api_error(self.path[web])["afterRetry"])

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
        self.assertEqual(km._RETRY_PAUSE_STILL[0], ("sdk", "key", ""), "still in force, on the key's billing")
        # R9: the mark rides the status of the sessions on THAT billing; a session on the login was not asked about
        self.assertEqual([km._spend_still(self.live[sid]) for sid in key], [True, True, True])
        self.assertEqual([km._spend_still(self.live[sid]) for sid in login], [False, False])
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
        self.assertEqual(km._RETRY_PAUSE_STILL[0], ("sdk", "key", ""), "and it is marked: on that billing the limit is STILL in force")
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
        self.assertEqual(km._RETRY_PAUSE_STILL[0], ("sdk", "key", ""), "marked still in force")
        self.assertIn("retry-pause: re-engaged: the retry sent to web on Resume was refused, so the monthly spend limit "
                      "is still in force (key billing)", err.getvalue())
        for at in (22, 23):
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"])

    def test_the_first_refused_wave_retry_ends_the_press(self):
        # the probe is served, the press opens, and the cap refuses every other session. The first refusal ends the
        # press: what is out is out, and nothing more is sent, so a press over a cap that serves one session and
        # refuses nine costs the retries already out (three at most), not nine and three engages
        capped = [self.refused_once(i) for i in range(10)]      # all answer a retry already: only a press moves them
        self.cycle(0)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual(self.be.sent, ["web"])
        self.answer(capped[0], 23)                               # the probe: served
        self.cycle(24)
        self.assertEqual(self.be.sent, NAMES[:4], "the press opens: three out")
        engages = 0
        for k, at in enumerate(range(26, 86, 2)):                # every retry out is refused; the served probe goes on being served
            for sid in capped[1:]:
                if self.unanswered(sid):
                    self.answer(sid, at - 0.5, ok=False)
            self.serves(capped[0], at - 0.25)
            before = self.paused()
            self.cycle(at)
            engages += (not before) and self.paused()
        self.assertEqual(self.be.sent, NAMES[:4], "three refused, and the other six were never sent a retry")
        self.assertEqual((engages, km._spend_press), (1, {}), "one engage, and the press is over")

    def test_a_press_is_spent_by_the_next_write_of_the_pause(self):
        # the probe is out and unanswered when a usage window fills and empties (an engage and a lift: two writes of
        # the pause, neither the press's own). A day later the probe is served. The press is long spent: nothing follows.
        capped = [self.refused_once(i) for i in range(4)]
        self.cycle(0)
        self.press_resume(20)
        self.cycle(21)
        self.assertEqual((self.be.sent, len(km._spend_press)), (["web"], 1))
        km._usage = lambda: {"limited": {"fiveHour": True}}
        self.cycle(30)
        self.assertEqual(km._retry_pause_reason(), "limit")
        km._usage = lambda: {"limited": None}
        self.cycle(40)
        self.assertEqual((self.paused(), km._spend_press), (False, {}), "the pause was written by something else: the press is spent")
        self.answer(capped[0], 86_400)                           # the probe, answered a day later
        for at in (86_401, 86_402, 86_403):
            self.serves(capped[0], at - 0.5)
            self.cycle(at)
        self.assertEqual(self.be.sent, ["web"], "a stale press opens nothing: the three are the person's still")

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
        # a Resume waits in memory for the pass half a second on (a restart spends it); a Stop is written into the
        # pause file by the door itself (StopHolds), and the newest press is the one the pass reads
        client = {"app": "chat", "wid": "w1", "alive": True, "send": lambda raw: None}
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": False}, client)
        self.assertEqual((km._spend_door[0], km._retry_pause_doc().get("stop")), ("resume", None))
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": True}, client)
        self.assertEqual((km._spend_door[0], km._retry_pause_doc().get("stop")), (None, float(self.T)))
        km.Handler._dispatch_ws(None, {"type": "setGlobalRetryPaused", "value": False}, client)
        self.assertEqual((km._spend_door[0], km._retry_pause_doc().get("stop")), ("resume", None))


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
        self.assertEqual(err.getvalue().count("auto-retry: retries after the spend cap are waiting on 3 unanswered session(s): web, api, tests; "
                                              "the Resume's probe on login billing goes when one is answered"), 1,
                         "said once, not once per pass\n" + err.getvalue())


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
        self.assertNotIn("held", km._retry_pause_doc(), "the Resume lifted the hold in its own write")

    def test_a_stop_holds_through_a_restart(self):
        # deploy restarts are routine: a Stop that a restart undid sent the held sessions a retry nobody asked for.
        # The hold is in the pause file, by session with the Stop's instant, and the next kernel reads it there
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.press_stop(7)
        self.cycle(7.5)
        for sid in capped[:3]:
            self.answer(sid, 8)
        self.restart()                                           # the kernel forgets everything it held in memory
        for at in range(9, 39, 2):
            self.serves(SIDS[STREAM], at - 0.5)
            self.cycle(at)
        self.assertEqual((self.paused(), self.be.sent), (False, NAMES[:3]), "the pause cleared itself; the hold did not")
        self.assertEqual(km._retry_pause_doc()["held"], {sid: self.T + 7.0 for sid in capped[3:]},
                         "the three it had not reached, held since the Stop, carried by every write since")
        self.restart()
        self.press_resume(50)                                    # their Resume, after another restart: it lifts the hold
        self.cycle(51)
        self.assertEqual(self.be.sent, NAMES[:6])

    def test_a_stop_holds_when_the_kernel_dies_before_its_next_pass(self):
        # the door's own write carries the Stop, so it is on file before any pass has looked. Kept in memory for the
        # pass half a second on, a kernel that died in that half second forgot it: the next one retried the rest
        capped = self.evidence(6)
        self.cycle(6)
        self.assertEqual(self.be.sent, NAMES[:3])
        self.press_stop(7)
        self.assertEqual(km._retry_pause_doc().get("stop"), self.T + 7.0, "written by the door, with the press's instant")
        self.restart()                                           # ... and the kernel dies before a pass reads it
        for sid in capped[:3]:
            self.answer(sid, 8)
        for at in range(9, 39, 2):
            self.serves(SIDS[STREAM], at - 0.5)
            self.cycle(at)
        self.assertEqual((self.paused(), self.be.sent), (False, NAMES[:3]), "the three the wave had not reached are held")
        doc = km._retry_pause_doc()
        self.assertEqual((doc["held"], doc.get("stop")), ({sid: self.T + 7.0 for sid in capped[3:]}, None),
                         "the first pass of the next kernel turned the marker into the hold")

    def test_a_pass_that_reads_no_row_for_a_held_session_leaves_its_hold(self):
        # one liveness read that misses a session (or a CLI between two lives) is not the person writing to it
        capped = self.evidence(4)
        self.press_stop(1)
        self.cycle(2)
        km._set_retry_paused(False)                              # (the manual pause aside: the hold's own reading is under test)
        row, entry = self.live.pop(capped[0]), [r for r in self.roster if r["sid"] == capped[0]]
        self.roster = [r for r in self.roster if r["sid"] != capped[0]]
        self.cycle(6)                                            # a pass with no row for web
        self.live[capped[0]] = row
        self.roster = entry + self.roster
        for at in (7, 8, 9):
            self.serves(SIDS[STREAM], at - 0.5)
            self.cycle(at)
        self.assertEqual(self.be.sent, [], "web is held still, with the other three")
        self.assertEqual(sorted(km._retry_pause_doc()["held"]), sorted(capped))
        # the person writes to a held session, and it is served: it leaves the hold on the pass that reads their message
        self.append(capped[0], prompt_line(self.T + 10, "carry on"))
        self.cycle(10.5)
        self.assertEqual(sorted(km._retry_pause_doc()["held"]), sorted(capped[1:]))
        self.answer(capped[0], 11)
        self.cycle(12)
        self.assertEqual(sorted(km._retry_pause_doc()["held"]), sorted(capped[1:]))

    def test_a_prompt_romp_put_in_does_not_spend_the_hold(self):
        # only the person's own message, or their Resume, lifts a hold. A nudge is romp's: the refusal that answers it
        # is a new record on a held session, and read as the session having moved on it drew one retry
        capped = self.evidence(4)
        self.press_stop(1)
        self.cycle(2)
        km._set_retry_paused(False)                              # (the manual pause aside: the hold's own reading is under test)
        self.cycle(6)
        self.assertEqual(self.be.sent, [], "held")
        self.append(capped[1], prompt_line(self.T + 10, NUDGE))
        self.cycle(10.5)                                         # seen between the prompt and its refusal: on no record at all
        self.answer(capped[1], 11, ok=False)
        self.cycle(12)
        self.assertTrue(self.paused(), "a new record engages the pause")
        self.serves(SIDS[STREAM], 14)
        self.cycle(15)
        self.assertEqual((self.paused(), self.be.sent), (False, []), "the billing serves again, and the nudged session is held still")
        # a turn something cut leaves the CLI's stop record, the same one as the person's Esc, and romp's next prompt
        # follows it: neither is a message of theirs
        self.append(capped[2], stop_line(self.T + 20))
        self.append(capped[2], prompt_line(self.T + 21, NUDGE))
        self.answer(capped[2], 22, ok=False)
        self.cycle(23)
        self.serves(SIDS[STREAM], 25)
        self.cycle(26)
        self.assertEqual(self.be.sent, [])
        self.assertEqual(km._retry_pause_doc()["held"], {sid: self.T + 1.0 for sid in capped}, "all four, held since the Stop")
        # their own message is: refused on the cap too, its record is one evidence may retry once
        self.append(capped[3], prompt_line(self.T + 30, "try that again"))
        self.answer(capped[3], 31, ok=False)
        self.cycle(32)
        self.serves(SIDS[STREAM], 34)
        self.cycle(35)
        self.assertEqual(self.be.sent, ["docs"])
        self.assertEqual(sorted(km._retry_pause_doc()["held"]), sorted(capped[:3]))

    def test_a_second_stop_holds_beside_the_first(self):
        capped = self.evidence(2)
        self.press_stop(1)
        self.cycle(2)
        late = self.capped(2, at=4)                              # parked after the first Stop
        self.append(capped[1], prompt_line(self.T + 5, NUDGE))   # a held session with romp's prompt out: on no record now
        self.press_stop(6)
        self.cycle(7)
        self.assertEqual(km._retry_pause_doc()["held"], {capped[0]: self.T + 6.0, capped[1]: self.T + 1.0, late: self.T + 6.0},
                         "the sessions parked at the second Stop, and the one the first still holds")
        km._set_retry_paused(False)                              # (the manual pause aside)
        self.answer(capped[1], 8, ok=False)
        for at in (10, 12, 14):
            self.serves(SIDS[STREAM], at)
            self.cycle(at + 1)
        self.assertEqual(self.be.sent, [])

    def test_the_hold_is_written_without_touching_the_pause_and_rides_every_later_write(self):
        km._set_retry_paused(True, reason="spend", bills="key")
        before = km._retry_pause_doc()
        seq = km._RETRY_PAUSE_SEQ[0]
        self.assertTrue(km._spend_held_write({"s1": 5.0}, before))
        self.assertEqual(km._retry_pause_doc(), dict(before, held={"s1": 5.0}), "the pause as it stood, and the hold")
        self.assertEqual(km._RETRY_PAUSE_SEQ[0], seq, "no pause write: no frame, and a standing press is not spent by it")
        km._set_retry_paused(False, lifted_at=self.T + 5)        # the lift
        km._set_retry_paused(True, reason="limit")               # an engage of another kind
        km._set_retry_paused(False)                              # its lift
        self.clock.at = self.T + 9
        km._set_retry_paused(True, stop=True)                    # a second Stop at the door: its marker, beside the hold
        km._set_retry_paused(True, reason="spend", bills="key")  # ... and an engage before the pass that reads the marker
        doc = km._retry_pause_doc()
        self.assertEqual((doc["held"], doc["stop"]), ({"s1": 5.0}, self.T + 9.0), "both ride every write that is not a Resume")
        # a Resume that lands between a pass's read of the file and its write is not answered with a hold computed
        # before it: the write stands down, and the next pass reads the file again
        km._set_retry_paused(False, resumed=True)
        self.assertFalse(km._spend_held_write({"s1": 5.0, "s2": self.T + 9.0}, doc))
        after = km._retry_pause_doc()
        self.assertEqual((after.get("held"), after.get("stop")), (None, None), "the person's Resume dropped both")
        self.assertTrue(km._spend_held_write({}, after))
        self.assertNotIn("held", km._retry_pause_doc())

    def test_a_pause_file_that_cannot_be_read_sends_nothing(self):
        # the hold lives in the pause file. A read fault on it (a kernel out of descriptors, a flaky mount) is not the
        # file saying nobody is held: read that way, the held sessions were retried for the span of the fault
        self.evidence(4)
        self.press_stop(1)
        self.cycle(2)
        km._set_retry_paused(False)                              # (the manual pause aside: the hold's own reading is under test)
        real = Path.read_text

        def faulting(p, *a, **k):
            if p.name == "retry-paused.json":
                raise OSError(errno.EMFILE, "too many open files")
            return real(p, *a, **k)
        err = io.StringIO()
        with mock.patch.object(Path, "read_text", faulting), contextlib.redirect_stderr(err):
            for at in (7, 8, 9):
                self.serves(SIDS[STREAM], at - 0.5)
                self.cycle(at)
        self.assertEqual(self.be.sent, [])
        self.assertEqual(err.getvalue().count("auto-retry: the pause file could not be read, so no retry after a spend cap is sent "
                                              "until it reads again\n"), 1, "said once for the fault, not once a pass")
        self.serves(SIDS[STREAM], 10)
        self.cycle(11)                                           # it reads again: the hold is there
        self.assertEqual(self.be.sent, [])

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


class RealBackendHandOver(unittest.TestCase):
    """R8 through the REAL backend. "Already retried" rests on two readings: the transcript (a retry prompt after the
    cap record) and, for a retry handed over and not yet recorded, the backend. The classes above fake the backend's
    side; here the real SdkBackend and the real session loop (its feeder, its echo ledger) take the kernel's send, over a
    stand-in SDK module whose client records what it was fed and writes nothing (the connect-loop harness of
    tests/test_queued_sends_not_fused.py). The CLI holds the retry, the transcript does not show it, the backend's
    queue is empty: what stands between the kernel and a second retry is the backend's own unlanded echo."""

    SID = "99999999-aaaa-4bbb-8ccc-00000000aa01"
    STREAMER = "99999999-aaaa-4bbb-8ccc-00000000aa02"

    class _Client:
        instances = []

        def __init__(self, options=None, transport=None):
            self.options, self.writes = options, []
            self.frames = asyncio.Queue()                    # the CLI's stream: nothing is ever pushed, so no turn is answered
            type(self).instances.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def query(self, prompt, session_id="default"):
            async for turn in prompt:
                self.writes.append(turn["message"]["content"][0]["text"])

        async def interrupt(self):
            pass

        async def get_context_usage(self):
            return {"percentage": 2, "model": "claude-x"}

        async def get_server_info(self):
            return {}

        async def receive_messages(self):
            while True:
                yield await self.frames.get()

    class _Options:
        def __init__(self, **kw):
            self.session_id = self.resume = None
            for k, v in kw.items():
                setattr(self, k, v)

    def setUp(self):
        self._Client.instances = []
        fake = types.ModuleType("claude_agent_sdk")
        fake.__spec__ = ModuleSpec("claude_agent_sdk", loader=None)
        fake.ClaudeSDKClient, fake.ClaudeAgentOptions, fake.HookMatcher = self._Client, self._Options, (lambda **kw: types.SimpleNamespace(**kw))
        for name in ("AssistantMessage", "ResultMessage", "SystemMessage", "TextBlock"):
            setattr(fake, name, type(name, (), {}))
        self._saved_sdk = sys.modules.get("claude_agent_sdk")
        sys.modules["claude_agent_sdk"] = fake

        async def _noop_refresh(self):
            pass
        self._saved_refresh = sb.SdkSession._do_refresh_usage
        sb.SdkSession._do_refresh_usage = _noop_refresh
        self.state = tempfile.mkdtemp()
        os.makedirs(self.state, exist_ok=True)
        open(os.path.join(self.state, "session-hosts"), "w").write("off")    # its own state root: the plain-child road, a fake client
        self.cwd = os.path.join(self.state, "proj")
        os.makedirs(self.cwd)
        self.be = sb.SdkBackend(self.state, "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        reg = {"sid": self.SID, "name": "web", "mode": "acceptEdits", "alive": True, "cwd": self.cwd, "auth": "key"}
        sb.write_reg(self.be.state_dir, self.SID, reg)
        self.s = sb.SdkSession(self.be, dict(reg))
        self.be.sessions[self.SID] = self.s
        # the kernel's side: web sits on a spend-cap record, and a session on its billing was served a request made after it
        self.T = int(time.time())
        self.dir = Path(self.state)
        self.paths = {self.SID: str(self.dir / "web.jsonl"), self.STREAMER: str(self.dir / "stream.jsonl")}
        Path(self.paths[self.SID]).write_text(out_line(self.T - 120) + err_line(self.T - 90))
        Path(self.paths[self.STREAMER]).write_text(out_line(self.T - 200) + tool_line(self.T - 10) + out_line(self.T - 9))
        self.rows = [{"sid": self.SID, "name": "web", "path": self.paths[self.SID]},
                     {"sid": self.STREAMER, "name": "stream", "path": self.paths[self.STREAMER]}]
        saved = {n: getattr(km, n) for n in ("_alive_sessions", "_path_of", "_session_retry_suppressed", "_retry_suppress_unknown",
                                             "_pending_ops", "_auth_key_present")}
        saved_backend, saved_state = km.Sessions.__dict__["backend_for"], km.jd.STATE

        def restore():
            for n, v in saved.items():
                setattr(km, n, v)
            km.Sessions.backend_for, km.jd.STATE = saved_backend, saved_state
            self._forget()
        self.addCleanup(restore)
        km.jd.STATE = self.dir
        km._alive_sessions = lambda now, live: list(self.rows)
        km._path_of = lambda sid, now=None: self.paths.get(sid)
        km._session_retry_suppressed = lambda sid: False
        km._retry_suppress_unknown = lambda: False
        km._auth_key_present = lambda: True
        km._pending_ops = {}
        km.Sessions.backend_for = staticmethod(lambda sid: self.be)
        self._forget()

    def tearDown(self):
        if self.s.thread.is_alive():
            self.s.shutdown()
        if self.s.thread.ident is not None:
            self.s.thread.join(timeout=10)
        sb.SdkSession._do_refresh_usage = self._saved_refresh
        if self._saved_sdk is None:
            sys.modules.pop("claude_agent_sdk", None)
        else:
            sys.modules["claude_agent_sdk"] = self._saved_sdk
        shutil.rmtree(self.state, ignore_errors=True)

    def _forget(self):
        """What a kernel restart forgets."""
        for name in ("_auto_retried", "_auto_retry_state", "_spend_press", "_api_err_cache", "_api_last_failed_cache"):
            vars(km).get(name, {}).clear()
        for name, empty in (("_spend_door", None), ("_spend_wait_said", None), ("_RETRY_PAUSE_STILL", None)):
            vars(km).get(name, [None])[0] = empty

    def _wait(self, pred, what, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if pred():
                return
            time.sleep(0.01)
        self.fail("timed out waiting for " + what)

    def _live(self):
        """The live map as the kernel reads it: web's row is the real backend's own, the streamer's a row on the same billing."""
        row = km._sdk_live_norm(self.be.live_sessions()[self.SID])
        return {self.SID: row, self.STREAMER: dict(row)}

    def test_a_retry_the_real_backend_holds_is_not_sent_twice(self):
        self.s.start()
        self._wait(lambda: self.s.client is not None, "the first connect")
        client = self._Client.instances[0]
        live = self._live()
        self.assertTrue(live[self.SID]["connected"], "premise: the real row says a CLI serves it")
        km._auto_retry_tick(self.T, live)
        self._wait(lambda: client.writes == [km.RETRY_MSG], "the kernel's retry, fed to the CLI by the real feeder")
        # handed over: the CLI has it, its transcript does not show it, and it has left the backend's queue
        self.assertTrue(km._api_error(self.paths[self.SID])["spendLimit"], "web's transcript still ends on its cap record")
        self._wait(lambda: self.be.pending_queued(self.SID) == [], "the feeder took it off the queue")
        self.assertTrue(km._retry_handed_over(self.be, self.SID), "the backend's own unlanded echo says it is out")
        self.assertEqual(km._spend_retries_out(self.rows, live, {self.SID: None}), ["web"], "and it holds a slot")
        # the kernel restarts: everything it remembered about the retry is gone
        for _ in range(3):
            self._forget()
            km._auto_retry_tick(self.T + 1, self._live())
            time.sleep(0.15)                                 # every chance for a second feed to happen
        self.assertEqual(client.writes, [km.RETRY_MSG], "one retry reached the CLI: no duplicate")


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
        served = lambda: km._api_served_times(self.path[sid])[:2]
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
        self.assertEqual(km._api_served_times(str(self.dir / "absent.jsonl")), (0, False, 0.0))

    def test_the_person_s_newest_own_message_is_timed_and_nothing_else_is_theirs(self):
        sid = self.session(0, out_line(self.T - 100))
        wrote = lambda: km._api_served_times(self.path[sid])[2]
        self.assertEqual(wrote(), 0.0)
        self.append(sid, prompt_line(self.T - 90.25, "run the import"))
        self.assertEqual(wrote(), self.T - 90.25, "to the millisecond the CLI stamps")
        for line in (out_line(self.T - 89), err_line(self.T - 80), prompt_line(self.T - 70, km.RETRY_MSG), err_line(self.T - 69),
                     prompt_line(self.T - 60, NUDGE), err_line(self.T - 59), stop_line(self.T - 50), synthetic_line(self.T - 49),
                     prompt_line(self.T - 40, "<task-notification>done</task-notification>", origin={"kind": "task-notification"}),
                     prompt_line(self.T - 30, "search the notes table", isSidechain=True), tool_line(self.T - 20)):
            self.append(sid, line)
            self.assertEqual(wrote(), self.T - 90.25, "romp's prompts, the CLI's stop record and notices, a subagent's prompt: none is theirs")
        self.append(sid, prompt_line(self.T - 10, "carry on"))
        self.assertEqual(wrote(), self.T - 10)


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


class AsOnMain(_World):
    """Whether a session is BLOCKED on an API error (_api_error) and the health latch beside it (_api_last_failed) are
    not this change's to redefine: the card, the feed, the nudge walk, the bottom bar's API cell and the subagent
    viewer read them, and each must read every transcript as it did before. What this change reads passes over the
    CLI's own records and a subagent's (RealAnswers); the verdict and the latch pass over nothing."""

    KEYS = {"text", "status", "category", "uuid", "t", "tExact", "tooLong", "spendLimit", "modelLimit", "authErr",
            "parentUuid", "refusal", "noRetry"}                   # the error dict before this change, which adds afterRetry
    KINDS = (out_line, synthetic_line, err_line, lambda t: err_line(t, OTHER_TEXT),
             lambda t: prompt_line(t, "go on"), lambda t: prompt_line(t, km.RETRY_MSG), lambda t: prompt_line(t, NUDGE), stop_line,
             tool_line, lambda t: _line("user", t, isMeta=True, message={"role": "user", "content": "a note the CLI added"}),
             lambda t: out_line(t, isSidechain=True), lambda t: err_line(t, isSidechain=True),
             lambda t: prompt_line(t, "search the notes table", isSidechain=True), lambda t: tool_line(t, isSidechain=True))

    @staticmethod
    def before(lines):
        """The rule before this change, in full -> (the uuid of the error record _api_error reports, the latch's), None
        for none. An API error record stands for both. Assistant output clears both, whoever wrote it. A user record
        that is not a tool result clears the first and leaves the latch."""
        err = latched = None
        for line in lines:
            o = json.loads(line)
            content = (o.get("message") or {}).get("content")
            if o["type"] == "assistant":
                if o.get("isApiErrorMessage"):
                    err = latched = o["uuid"]
                elif any(b.get("type") in ("text", "tool_use", "thinking") for b in content):
                    err = latched = None
            elif o["type"] == "user" and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                err = None
        return err, latched

    def now(self, sid, lines, window=1 << 20):
        saved = km._API_ERR_TAIL_WINDOW
        try:
            km._API_ERR_TAIL_WINDOW = window
            Path(self.path[sid]).write_text("".join(lines))
            km._api_err_cache.clear()
            km._api_last_failed_cache.clear()
            return km._api_error(self.path[sid]), km._api_last_failed(self.path[sid])
        finally:
            km._API_ERR_TAIL_WINDOW = saved

    def test_every_short_transcript_reads_as_it_did(self):
        # every sequence of up to three records of every kind the readings tell apart, read whole and through a tail
        # window of about one record (the read that widens)
        sid = self.session(0)
        seqs = [()]
        for _ in range(3):
            seqs = [q + (k,) for q in seqs for k in range(len(self.KINDS))]
            for q in seqs:
                lines = [self.KINDS[k](self.T - 100 + i) for i, k in enumerate(q)]
                want = self.before(lines)
                for window in (1 << 20, 300):
                    got = self.now(sid, lines, window)
                    self.assertEqual(tuple(d and d["uuid"] for d in got), want, "kinds %r, window %d" % (q, window))
                    for d in got:
                        self.assertEqual(set(d or self.KEYS) - {"afterRetry"}, self.KEYS, "kinds %r" % (q,))

    def test_an_error_then_the_cli_s_settle_reads_recovered(self):
        sid = self.session(0)
        lines = [prompt_line(self.T - 50, "run the import"), err_line(self.T - 49), synthetic_line(self.T - 40)]
        self.assertEqual(self.now(sid, lines), (None, None), "the card leaves the session and the latch clears, as before")
        self.assertEqual(km._api_last_output_t(self.path[sid]), 0, "... while the pause's lift reads no answer in it")

    def test_an_error_a_retry_an_interrupt_and_the_settle_read_recovered(self):
        sid = self.session(0)
        lines = [prompt_line(self.T - 50, "run the import"), err_line(self.T - 49), prompt_line(self.T - 40, km.RETRY_MSG),
                 stop_line(self.T - 35), synthetic_line(self.T - 34)]
        for upto in (3, 4):                                      # the retry un-blocks the card, and the latch holds through it
            err, latched = self.now(sid, lines[:upto])
            self.assertEqual((err, latched["uuid"]), (None, json.loads(lines[1])["uuid"]), "%d records" % upto)
        self.assertEqual(self.now(sid, lines), (None, None), "the settle clears the latch, as before")
        self.assertEqual((km._api_last_output_t(self.path[sid]), km._api_served_times(self.path[sid])[:2]), (0, (0, False)),
                         "... while the pause's lift reads no answer in it, and the retry the interrupt cut holds no slot")

    def test_an_agent_s_own_file_yields_its_api_error(self):
        # the subagent viewer reads an agent's file, where every record is a sidechain one
        sid = self.session(0)
        last = err_line(self.T - 30, OTHER_TEXT, isSidechain=True, parentUuid="user-parent")
        lines = [prompt_line(self.T - 50, "search the notes table", isSidechain=True), out_line(self.T - 45, isSidechain=True),
                 tool_line(self.T - 40, isSidechain=True), last]
        want = {"text": OTHER_TEXT, "status": 429, "category": "billing_error", "uuid": json.loads(last)["uuid"], "t": self.T - 30,
                "tExact": self.T - 30.0, "tooLong": False, "spendLimit": False, "modelLimit": False, "authErr": False,
                "parentUuid": "user-parent", "refusal": False, "noRetry": False, "afterRetry": False}
        self.assertEqual(self.now(sid, lines), (want, want))
        cap = err_line(self.T - 20, isSidechain=True)
        got = self.now(sid, lines + [cap], window=300)[0]
        self.assertEqual((got["uuid"], got["spendLimit"]), (json.loads(cap)["uuid"], True), "a cap in it too, through a tail window")


class Wiring(unittest.TestCase):
    def test_the_retry_goes_through_the_one_retry_decision_with_the_existing_text(self):
        src = inspect.getsource(km._spend_resume_pass)
        self.assertIn("_fire_api_retry(sid, be, served=key)", src)
        self.assertNotIn("be.send(", src, "no send of its own: the one decision's gates and its marker stand")
        self.assertEqual(km.RETRY_MSG, "retry\n\n<!-- romp-injected -->", "the injected text is unchanged")
        tick = inspect.getsource(km._auto_retry_tick)
        self.assertIn("_spend_resume_pass(now, live_map, rows, capped)", tick, "inside the autoRetry job: no new job, no new pass")

    def test_still_rides_the_status_of_the_sessions_on_the_refused_billing_and_the_page_words_it(self):
        # R9. The kernel's half: the mark is a billing, and each session's status says whether it is on that billing
        # (the built status, the cold tab's, the chat build's signature, a comment thread's projection). The page's half:
        # the card's one decider reads it off the status (ui/webview/api-card-line.ts); the card is RENDERED with it
        # set and cleared in ui/webview/apierror-line-browser.test.ts.
        src = Path(BIN, "romp-kernel").read_text()
        self.assertEqual(src.count('"spendStill": _spend_still(tm),'), 2, "the built status and the cold tab's")
        self.assertIn("sig.append((_session_retry_suppressed(sid),) + tuple(_retry_gate_state(sid)) + (_spend_still(tm),))", src,
                      "the chat build is rebuilt when it flips")
        self.assertIn("spendStill", km._THREAD_LIVE_STATUS_KEYS)
        self.assertNotIn('"still":', inspect.getsource(km._push_all) if hasattr(km, "_push_all") else "", "no pause-wide copy on the frame")
        self.assertIn('"reason": _retry_pause_reason()})', src)
        key, login = {"backend": "sdk", "auth": "key", "authLive": "key"}, {"backend": "sdk", "auth": "login", "authLive": "login"}
        other = dict(login, authLogin="login-b")
        try:
            for mark, want in ((None, (False, False, False)), (km._billing_key(key), (True, False, False)),
                               (km._billing_key(other), (False, False, True))):
                km._RETRY_PAUSE_STILL[0] = mark
                self.assertEqual(tuple(km._spend_still(r) for r in (key, login, other)), want)
        finally:
            km._RETRY_PAUSE_STILL[0] = None
        ui = Path(os.path.dirname(HERE), "ui", "webview")
        line = (ui / "api-card-line.ts").read_text()
        self.assertIn('"spend limit still in force — raise it at claude.ai/settings/usage"', line)
        self.assertIn("if (st?.apiSpendLimit) return remedy(st.spendStill ? SPEND_STILL_REMEDY : SPEND_CAP_REMEDY);", line)
        self.assertIn("spendStill: true", (ui / "apierror-line-browser.test.ts").read_text())

    def test_still_clears_with_the_next_write_of_the_pause(self):
        saved = km.jd.STATE
        td = tempfile.TemporaryDirectory()
        bill = ("sdk", "key", "")
        try:
            km.jd.STATE = Path(td.name)
            km._set_retry_paused(True, reason="spend", bills="key", still=bill)
            self.assertEqual(km._RETRY_PAUSE_STILL[0], bill)
            self.assertEqual(set(json.loads((Path(td.name) / "retry-paused.json").read_text())), {"paused", "t", "reason", "bills"},
                             "memory only: the file's shape is unchanged")
            km._set_retry_paused(False)
            self.assertIsNone(km._RETRY_PAUSE_STILL[0], "a lift clears it")
            km._set_retry_paused(True, reason="spend", bills="key", still=bill)
            km._set_retry_paused(True, reason="limit")
            self.assertIsNone(km._RETRY_PAUSE_STILL[0], "and so does a pause of another kind")
            km._set_retry_paused(True, reason="spend", bills="key", still=bill)
            km._set_retry_paused(True)
            self.assertIsNone(km._RETRY_PAUSE_STILL[0], "and the person's Stop")
        finally:
            km.jd.STATE = saved
            km._RETRY_PAUSE_STILL[0] = None
            td.cleanup()


if __name__ == "__main__":
    unittest.main()
