#!/usr/bin/env python3
"""The spend guard covers comment threads (2026-10-29). A comment thread is a full session that can fan out agents at any
rate on the same account, but the live map the guard walked leaves every thread out on purpose (no tab, lane or card of its
own), and its transcript and agent files sit outside its parent's window, so a thread spending far over the ceiling was
never stopped, told, toasted or logged. The guard now reads every running thread (Sessions.thread_live) beside the live
sessions, whether or not a dashboard is open, and prices each from its own transcript and agents, from the fork's moment on:
the transcript opens with the parent's history copied in under its original timestamps, which is the parent's spend, not
the thread's. A thread whose fork has not landed has no transcript of its own yet and is not read. The same holds for a
plain fork and for a thread broken out into a session of its own (2026-10-31): both are live sessions whose discovery rows
carry no floor, so the guard takes each one's fork moment from its own registry record, and the parent's spend just before
the fork no longer stops a fork that has spent next to nothing.

Driven through the real tick (no `sessions` seam), the real SDK backend's live_sessions and thread_live_sessions, and a
stand-in backend for the stop and the message. SYNTHETIC fixtures only: placeholder uuids, the notes-api demo's web session,
a hermetic state root, transcripts written here."""
import calendar
import json
import os
import shutil
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs conftest's floor
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_spend_guard_threads", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_spend_guard_threads", os.path.join(BIN, "romp_sdk_backend.py"))
jd = km.jd

WEB = "11111111-2222-3333-4444-0000000e2a01"
THREAD = "11111111-2222-3333-4444-0000000e2b02"
THREAD_FSID = "11111111-2222-3333-4444-0000000e2c03"
FORK = "11111111-2222-3333-4444-0000000e2d04"          # web-alt, a plain fork of web (a tab of its own, no threadOf)
NOW = 1781200000.0
FORK_T = NOW - 450                                       # the thread was forked seven and a half minutes ago, inside the window
PRICES = {"claude-opus-4-8": {"in": 5e-6, "out": 25e-6, "cache_w": 6.25e-6, "cache_r": 0.5e-6}}


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def assistant(sid, t, mid, usd):
    """One opus response costing `usd` in output tokens alone."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": mid + "-u", "sessionId": sid,
            "message": {"id": mid, "role": "assistant", "model": "claude-opus-4-8",
                        "usage": {"input_tokens": 0, "output_tokens": int(usd / 25e-6),
                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                        "content": [{"type": "text", "text": "working"}]}}


def user(sid, t, uid):
    return {"type": "user", "timestamp": iso(t), "uuid": uid, "sessionId": sid, "message": {"role": "user", "content": "go"}}


def write_jsonl(path, records):
    """The file, its mtime at its newest record's moment, as the CLI leaves it."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("".join(json.dumps(r) + "\n" for r in records))
    t = max(calendar.timegm(time.strptime(r["timestamp"][:19], "%Y-%m-%dT%H:%M:%S")) for r in records)
    os.utime(path, (t, t))


class FakeBackend:
    """The stop and the message, recorded; the real SDK backend answers only the liveness reads."""

    def __init__(self):
        self.interrupts, self.sends, self.logs, self.sessions = [], [], [], {}

    def owns(self, sid):
        return True

    def interrupt(self, sid):
        self.interrupts.append(sid)
        return True

    def send(self, sid, text, qid=None, user=False):
        self.sends.append((sid, text))
        return True

    def busy(self, sid):
        return False

    def _log(self, m, problem=None, key=None, ring_text=None):
        self.logs.append((str(m), problem, ring_text))


def running(snap):
    """A stand-in for a running SdkSession: its thread is up and its snapshot is `snap`."""
    return types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True),
                                 snapshot=lambda retry_on=None: dict(snap))


class ThreadSpend(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()                     # one state root: the kernel's and the SDK backend's
        Path(self.td, "session-hosts").write_text("off\n")   # no real session host over a test root (CLAUDE.md)
        self.cwd = os.path.join(self.td, "notes-api")    # the sessions' working directory
        os.makedirs(self.cwd)
        self.saved = jd.STATE
        jd.STATE = Path(self.td)
        jd._state_cache.clear()
        for p in (mock.patch.object(jd, "PROJECTS", Path(self.td, "projects")),   # transcripts under the test root
                  mock.patch.object(km, "_send_or_park",
                                    side_effect=lambda be, sid, text, echo=None, qid=None, user=False:
                                    (False if be.send(sid, text) else None))):
            p.start(); self.addCleanup(p.stop)
        self._reset()
        self.saved_thread_live = km._thread_live[0]
        km._thread_live[0] = {}                          # no dashboard connected: the pusher's map of the open tabs' threads is empty
        # the real SDK backend: web is a session with a tab, its comment thread a fork with threadOf, running a turn
        self.sdk = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.sdk.spawn("web", self.cwd, sid=WEB)
        self.sdk.fork("web-comment-1", WEB, "a1", sid=THREAD, thread_of=WEB)
        self.sdk.sessions[THREAD] = running({"state": "working"})
        self.fork_reg = sb.read_reg(Path(self.td), THREAD)   # as the fork wrote it: forkOf set, lastSid on web's transcript
        p = mock.patch.object(km, "_sdk", return_value=self.sdk)
        p.start(); self.addCleanup(p.stop)
        self._set_reg(t=FORK_T)
        self.web_leaf = str(jd._proj_dir(self.cwd) / (WEB + ".jsonl"))
        self.thread_leaf = str(jd._proj_dir(self.cwd) / (THREAD_FSID + ".jsonl"))
        self.be, self.toasts = FakeBackend(), []
        self.clients = [{"app": "chat", "wid": w, "alive": True, "send": lambda payload: self.toasts.append(json.loads(payload))}
                        for w in ("w1", "w2")]

    def _reset(self):
        km._SPEND_GUARD.clear()
        km._SPEND_GUARD_SEEDED[0] = False
        km._SPEND_ROWS_CACHE.clear()
        km._SPEND_CAN_PREV.clear()
        km._SPEND_TREE_CACHE.clear()
        for s in (WEB, THREAD, FORK):
            km._interrupt_clicked.pop(s, None)
            km._thread_reg_memo.pop(s, None)

    def tearDown(self):
        self._reset()
        km._thread_live[0] = self.saved_thread_live
        jd.STATE = self.saved
        jd._state_cache.clear()
        shutil.rmtree(self.td, ignore_errors=True)

    def _set_reg(self, t, landed=True):
        """The thread's registry entry: forked at `t`, and (`landed`) its first init done, which flips lastSid off the
        parent's transcript onto its own and spends forkOf, as every thread that has run a turn reads."""
        reg = dict(self.fork_reg)
        reg["forkedFrom"] = dict(reg.get("forkedFrom") or {}, t=int(t))
        if landed:
            reg["lastSid"] = THREAD_FSID
            reg.pop("forkOf", None)
        sb.write_reg(Path(self.td), THREAD, reg)
        km._thread_reg_memo.pop(THREAD, None)

    def _tick(self, also=()):
        """One pass as the jobs pass runs it: the live map from live_sessions (which leaves the thread out) and discovery's
        rows (web alone: a thread has no names/ entry), no `sessions` seam. `also`: (sid, name, transcript) for each further
        session with a tab of its own (a fork, a thread broken out), listed as discovery lists one, with no floor."""
        live_map = self.sdk.live_sessions()
        self.assertIn(WEB, live_map)
        rows = [{"sid": WEB, "name": "web", "anchor": WEB, "path": self.web_leaf, "mtime": NOW}]
        for sid, name, path in also:
            self.assertIn(sid, live_map, "%s is a session of its own" % name)
            rows.append({"sid": sid, "name": name, "anchor": sid, "path": path, "mtime": NOW})
        if THREAD not in [sid for sid, _, _ in also]:
            self.assertNotIn(THREAD, live_map, "the live map leaves the thread out, as it does on main")
        with mock.patch.object(km, "_sessions", return_value=rows), mock.patch.object(km, "_discover_wide", return_value={}):
            km._spend_guard_tick(NOW, live_map, be=self.be, clients=self.clients, prices=PRICES)

    def _rows(self, kind="spend.ceiling"):
        p = Path(self.td, "session-events.jsonl")
        rows = [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []
        return [(r.get("sid"), r.get("name")) for r in rows if r.get("kind") == kind]

    def test_a_running_thread_over_the_ceiling_is_stopped_told_and_logged(self):
        # web idle at $30 an hour; its comment thread fans out at $1,800 an hour: $20 in its own transcript, $280 across a
        # Task agent and a workflow agent, all in the last ten minutes
        write_jsonl(self.web_leaf, [user(WEB, NOW - 400, "wu"), assistant(WEB, NOW - 300, "msg_w", 5.0)])
        write_jsonl(self.thread_leaf, [user(THREAD, NOW - 2000, "cu"), assistant(THREAD, NOW - 1900, "msg_c", 5.0),   # copied
                                       user(THREAD, NOW - 400, "tu"), assistant(THREAD, NOW - 300, "msg_t", 20.0)])
        sub = Path(os.path.splitext(self.thread_leaf)[0]) / "subagents"
        write_jsonl(sub / "agent-1.jsonl", [assistant(THREAD, NOW - 200, "msg_s1", 140.0)])
        write_jsonl(sub / "workflows" / "wf_abc" / "agent-w1.jsonl", [assistant(THREAD, NOW - 100, "msg_w1", 140.0)])
        self._tick()
        self.assertEqual(self.be.interrupts, [THREAD], "the thread is stopped; web, under the ceiling, is not")
        self.assertEqual([s for s, _ in self.be.sends], [THREAD], "and the thread alone is handed the message")
        self.assertIn("about $1,800 an hour", self.be.sends[0][1])
        self.assertEqual(len(self.toasts), 2, "every connected dashboard, once")
        self.assertTrue(self.toasts[0]["text"].startswith("web-comment-1 was spending about $1,800 an hour at "), self.toasts[0]["text"])
        self.assertEqual(self._rows(), [(THREAD, "web-comment-1")], "one spend.ceiling row, naming the thread")
        self.assertTrue(km._SPEND_GUARD[THREAD]["over"], "latched: the next pass at the same rate says nothing more")
        cut = Path(self.td, "states", THREAD + ".jsonl")
        self.assertTrue(cut.exists() and '"spendCut"' in cut.read_text(),
                        "the thread's own live row said a turn was open, so the press records the cut the chat names the stop by")
        self._tick()
        self.assertEqual((self.be.interrupts, len(self._rows())), ([THREAD], 1), "once per crossing")

    def test_the_history_copied_in_by_the_fork_is_not_the_threads_spend(self):
        # web spent $200 just before the thread was forked ($1,200 an hour: over); the fork copied that response into the
        # thread's transcript under its original timestamp, and the thread itself has spent $1 since
        write_jsonl(self.web_leaf, [user(WEB, NOW - 560, "wu"), assistant(WEB, NOW - 500, "msg_w", 200.0)])
        write_jsonl(self.thread_leaf, [user(THREAD, NOW - 560, "wu"), assistant(THREAD, NOW - 500, "msg_w", 200.0),   # copied
                                       user(THREAD, NOW - 400, "tu"), assistant(THREAD, NOW - 300, "msg_t", 1.0)])
        self._tick()
        self.assertEqual(self.be.interrupts, [WEB], "web's spend is web's alone: the thread, at $6 an hour, is left running")
        self.assertEqual(self._rows(), [(WEB, "web")])
        self.assertNotIn(THREAD, km._SPEND_GUARD)

    def test_a_thread_whose_fork_has_not_landed_is_not_read(self):
        # the thread's CLI has not run its first init yet: forkOf stands and lastSid still names web's transcript, which web
        # keeps writing at $1,200 an hour after the fork
        self._set_reg(t=FORK_T, landed=False)
        self.assertEqual(km._thread_transcript_path(km._thread_reg(THREAD), THREAD), self.web_leaf,
                         "before the fork lands the thread's path is its parent's transcript")
        write_jsonl(self.web_leaf, [user(WEB, NOW - 400, "wu"), assistant(WEB, NOW - 300, "msg_w", 200.0)])
        self._tick()
        self.assertEqual(self.be.interrupts, [WEB], "web is stopped once; its spend is never billed to the thread as well")
        self.assertEqual(self._rows(), [(WEB, "web")])

    def _fork(self, own_usd):
        """web-alt, a plain fork of web made at FORK_T and landed (its first init flipped lastSid onto its own transcript and
        spent forkOf), its transcript opening with web's $200 response copied in under its original timestamp and message
        id, then `own_usd` of its own; web's transcript holds the same $200 response. Returns the fork's transcript."""
        self.sdk.fork("web-alt", WEB, "", sid=FORK)
        reg = sb.read_reg(Path(self.td), FORK)
        self.assertEqual((reg.get("forkOf"), reg.get("threadOf")), (WEB, None), "a plain fork: a tab of its own, no threadOf")
        reg.update(lastSid=FORK, forkedFrom=dict(reg.get("forkedFrom") or {}, t=int(FORK_T)))
        reg.pop("forkOf", None)
        sb.write_reg(Path(self.td), FORK, reg)
        km._thread_reg_memo.pop(FORK, None)
        fork_leaf = str(jd._proj_dir(self.cwd) / (FORK + ".jsonl"))
        write_jsonl(self.web_leaf, [user(WEB, NOW - 560, "wu"), assistant(WEB, NOW - 500, "msg_w", 200.0)])
        write_jsonl(fork_leaf, [user(FORK, NOW - 560, "wu"), assistant(FORK, NOW - 500, "msg_w", 200.0),   # copied
                                user(FORK, NOW - 400, "fu"), assistant(FORK, NOW - 300, "msg_f", own_usd)])
        return fork_leaf

    def test_the_history_copied_in_by_a_plain_fork_is_not_the_forks_spend(self):
        # web spent $200 just before it was forked ($1,200 an hour: over); the fork, which has spent $1 of its own since
        # ($6 an hour), is left running, untold and unlogged
        fork_leaf = self._fork(1.0)
        self._tick(also=[(FORK, "web-alt", fork_leaf)])
        self.assertEqual(self.be.interrupts, [WEB], "web's spend is web's alone: the fork, at $6 an hour, is left running")
        self.assertEqual([s for s, _ in self.be.sends], [WEB], "and only web is handed the message")
        self.assertEqual(self._rows(), [(WEB, "web")])
        self.assertEqual(len(self.toasts), 2, "web's crossing alone, on each dashboard")
        self.assertNotIn(FORK, km._SPEND_GUARD)

    def test_a_fork_over_the_ceiling_on_its_own_spend_is_still_stopped(self):
        # the floor is not a pass: the fork spends $200 of its own after the fork ($1,200 an hour) and is stopped, at the
        # rate of its own spend alone (the copied response would have read $2,400)
        fork_leaf = self._fork(200.0)
        self._tick(also=[(FORK, "web-alt", fork_leaf)])
        self.assertEqual(self.be.interrupts, [WEB, FORK])
        self.assertEqual(self._rows(), [(WEB, "web"), (FORK, "web-alt")])
        self.assertIn("about $1,200 an hour", dict(self.be.sends)[FORK])

    def test_a_thread_broken_out_into_a_session_is_not_billed_for_the_history_it_copied(self):
        # web's comment thread (landed at FORK_T) is broken out into web-side, a session with a tab of its own: it leaves
        # the threads and joins the live map, and its registry record keeps forkedFrom
        self.assertTrue(self.sdk.promote_thread(THREAD, "web-side"))
        km._thread_reg_memo.pop(THREAD, None)
        reg = sb.read_reg(Path(self.td), THREAD)
        self.assertEqual((reg.get("threadOf"), (reg.get("forkedFrom") or {}).get("t")), (None, int(FORK_T)))
        write_jsonl(self.web_leaf, [user(WEB, NOW - 560, "wu"), assistant(WEB, NOW - 500, "msg_w", 200.0)])
        write_jsonl(self.thread_leaf, [user(THREAD, NOW - 560, "wu"), assistant(THREAD, NOW - 500, "msg_w", 200.0),   # copied
                                       user(THREAD, NOW - 400, "tu"), assistant(THREAD, NOW - 300, "msg_t", 1.0)])
        self._tick(also=[(THREAD, "web-side", self.thread_leaf)])
        self.assertEqual(self.be.interrupts, [WEB], "the broken-out thread, at $6 an hour of its own, is left running")
        self.assertEqual(self._rows(), [(WEB, "web")])
        self.assertNotIn(THREAD, km._SPEND_GUARD)


if __name__ == "__main__":
    unittest.main()
