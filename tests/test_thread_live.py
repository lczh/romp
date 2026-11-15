#!/usr/bin/env python3
"""The comment threads' live map (thread-live-1, 2026-10-14): a thread stopped on a question or a permission prompt is
answerable from its popover, and the passage says it waits on you, without the thread ever joining the map that drives
tabs, lanes and the feed.

The kernel keeps a SEPARATE map of the open tabs' running threads (Sessions.thread_live, refreshed by the pusher into
_thread_live), each row normalized exactly as a tab's (_sdk_live_norm). The existing channels read it beside the live map:
the question poll replays a thread's question on the thread's own slot (_ask_poll_pass) and clears it once the thread
leaves the map; the thread's chat build gets its row as a tab's build does, so the comments frame carries the chat's own
state chip (needsInput on a prompt, blocked on an API error) for the popover's statusline and the passage's cue; and the
answer travels the chat's own path by the thread's sid (_drive's answerAsk). Synthetic fixtures only: placeholder UUIDs,
invented prose, a hermetic state root."""
import json
import os
import shutil
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_thread_live", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_thread_live", os.path.join(BIN, "romp_sdk_backend.py"))

km._limit_hold = lambda sid: None

PARENT = "11111111-2222-3333-4444-555555555555"
THREAD = "66666666-7777-8888-9999-aaaaaaaaaaaa"
OTHER_PARENT = "aaaaaaaa-1111-2222-3333-444444444444"
OTHER_THREAD = "bbbbbbbb-1111-2222-3333-444444444444"
ASK = {"kind": "single", "question": "Which cap should the notes-api retry loop use?", "sig": "q-cap",
       "options": [{"n": 1, "label": "Two minutes"}, {"n": 2, "label": "Five minutes"}]}
PERMISSION = {"kind": "single", "question": "Allow Bash: rm -rf build/?", "sig": "p-bash",
              "options": [{"n": 1, "label": "Allow"}, {"n": 2, "label": "Deny"}]}


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": stop}}


def err_line(t, uuid, parent):
    """An API error record, as the CLI writes one when a turn dies on the API."""
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "isApiErrorMessage": True,
            "apiErrorStatus": 500, "error": "server_error",
            "message": {"role": "assistant", "model": "<synthetic>", "stop_reason": "stop_sequence",
                        "content": [{"type": "text", "text": "API Error: 500 server_error"}]}}


def running(snap):
    """A stand-in for a running SdkSession: its thread is up and its snapshot is `snap`."""
    return types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True),
                                 snapshot=lambda retry_on=None: dict(snap))


def _hosts_off(root):
    Path(root, "session-hosts").write_text("off\n")   # no real session host for a backend over a test root (CLAUDE.md)


class BackendThreadRows(unittest.TestCase):
    """SdkBackend.thread_live_sessions: the running threads' rows, the very rows live_sessions leaves out."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        _hosts_off(self.td)
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("web", self.td, sid=PARENT)
        self.be.spawn("api", self.td, sid=OTHER_PARENT)
        self.be.fork("web-comment-1", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.be.fork("api-comment-1", OTHER_PARENT, "a1", sid=OTHER_THREAD, thread_of=OTHER_PARENT)

    def tearDown(self):
        shutil.rmtree(self.td, ignore_errors=True)

    def test_a_running_thread_is_listed_with_its_parent_and_never_in_live_sessions(self):
        self.be.sessions[THREAD] = running({"state": "permission", "model": "Opus 5", "effort": "high"})
        rows = self.be.thread_live_sessions()
        self.assertEqual(set(rows), {THREAD}, "only the RUNNING thread: a dormant one has no live row")
        self.assertEqual(rows[THREAD]["threadOf"], PARENT)
        self.assertEqual(rows[THREAD]["state"], "permission", "its running snapshot, as live_sessions would report it")
        self.assertNotIn(THREAD, self.be.live_sessions(), "the map that drives tabs, lanes and the feed never lists it")

    def test_parents_filter_the_threads_to_the_open_tabs(self):
        self.be.sessions[THREAD] = running({"state": "working"})
        self.be.sessions[OTHER_THREAD] = running({"state": "working"})
        self.assertEqual(set(self.be.thread_live_sessions({PARENT})), {THREAD})
        self.assertEqual(set(self.be.thread_live_sessions({PARENT, OTHER_PARENT})), {THREAD, OTHER_THREAD})
        self.assertEqual(self.be.thread_live_sessions(set()), {})

    def test_a_snapshot_that_raises_leaves_that_thread_out_and_keeps_the_others(self):
        def boom(retry_on=None):
            raise RuntimeError("a snapshot fault")
        self.be.sessions[THREAD] = types.SimpleNamespace(thread=types.SimpleNamespace(is_alive=lambda: True), snapshot=boom)
        self.be.sessions[OTHER_THREAD] = running({"state": "working"})
        self.assertEqual(set(self.be.thread_live_sessions()), {OTHER_THREAD})


class KernelThreadMap(unittest.TestCase):
    """Sessions.thread_live and the thread live map: a tab's normalization, kept apart from Sessions.live()."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        _hosts_off(self.td)
        self.be = sb.SdkBackend(Path(self.td), "/bin/true", lambda *a, **k: None)
        self.be.spawn("web", self.td, sid=PARENT)
        self.be.fork("web-comment-1", PARENT, "a1", sid=THREAD, thread_of=PARENT)
        self.be.sessions[THREAD] = running({"state": "picker", "model": "Opus 5", "effort": "high", "ctx": 41,
                                            "since": "1700000000"})
        self.saved = (km._sdk, km._codex, km._thread_live[0])
        km._sdk = lambda: self.be
        km._codex = lambda: None

    def tearDown(self):
        km._sdk, km._codex, km._thread_live[0] = self.saved
        shutil.rmtree(self.td, ignore_errors=True)

    def test_the_thread_row_is_normalized_as_a_tabs_row_and_never_enters_the_live_map(self):
        live = km.Sessions.live()
        self.assertIn(PARENT, live)
        self.assertNotIn(THREAD, live, "no tab, lane or feed card: Sessions.live() never lists a thread")
        tl = km.Sessions.thread_live({PARENT})
        self.assertEqual(set(tl), {THREAD})
        row = tl[THREAD]
        self.assertEqual(row["threadOf"], PARENT)
        self.assertEqual(row["state"], "picker")
        self.assertEqual(row["context"], 41, "the tab's normalization: ctx becomes context")
        self.assertEqual(set(row) - {"threadOf"}, set(km._sdk_live_norm({})), "exactly the keys a tab's row carries")

    def test_the_refresh_replaces_the_map_whole_and_leaves_the_live_map_alone(self):
        before = km._thread_live_map()
        m = km._thread_live_refresh([PARENT])
        self.assertIs(km._thread_live_map(), m)
        self.assertIsNot(m, before, "replaced, never mutated: a reader on another thread holds a consistent map")
        self.assertIn(THREAD, m)
        self.assertNotIn(THREAD, km.Sessions.live())
        self.assertEqual(km._thread_live_refresh([OTHER_PARENT]), {}, "only the open tabs' threads")


class PusherKeepsTheMap(unittest.TestCase):
    def test_the_comments_loop_refreshes_the_map_for_the_open_tabs_and_each_frame_reads_it(self):
        import inspect
        src = inspect.getsource(km._push)
        self.assertIn("_thread_live_refresh(tab_order)", src, "the open tabs' threads, once per full push, at the tab strip")
        self.assertLess(src.index("_thread_live_refresh("), src.index("_send_live_status(c, ls_chat, ls_allowed)"),
                        "refreshed before the live-status send that carries the threads' frames (thread-live-2)")
        self.assertLess(src.index("_thread_live_refresh("), src.index("fr = _comments_frame(s[\"sid\"], live_map)"),
                        "refreshed before the frames that read it")
        self.assertIn("thread_live = _thread_live_map() if thread_live is None else thread_live", inspect.getsource(km._comments_frame))
        self.assertNotIn("_thread_live", inspect.getsource(km._live_map), "never merged into the live map")


class AskPollThreads(unittest.TestCase):
    """_ask_poll_pass: a thread's question rides the chat's own channel, on the thread's own slot."""

    def setUp(self):
        self.sent = []
        self.asks = {THREAD: ASK}
        poll = self

        class Be:
            def current_ask(self, sid):
                return poll.asks.get(sid)
        self.saved = (km._send_client, km._live_map, km.Sessions.backend_for, km._thread_live[0])
        km._send_client = lambda c, key, payload: self.sent.append((c["id"], key, payload))
        km._live_map = lambda: {PARENT: {"state": "working"}}
        km.Sessions.backend_for = staticmethod(lambda sid: Be())
        km._thread_live[0] = {THREAD: {"state": "picker", "threadOf": PARENT}}
        self.clients = [{"id": "c1", "app": "chat"}, {"id": "c2", "app": "chat"}]

    def tearDown(self):
        km._send_client, km._live_map, km.Sessions.backend_for, km._thread_live[0] = (
            self.saved[0], self.saved[1], staticmethod(self.saved[2]), self.saved[3])

    def test_a_threads_question_goes_to_every_chat_client_on_its_own_slot(self):
        got = km._ask_poll_pass(self.clients, set())
        self.assertEqual(got, {THREAD})
        thread_frames = [(c, p) for c, k, p in self.sent if k == ("asklive", THREAD)]
        self.assertEqual(thread_frames, [("c1", {"type": "askLive", "id": THREAD, "ask": ASK}),
                                         ("c2", {"type": "askLive", "id": THREAD, "ask": ASK})])
        self.assertIn(("c1", ("asklive", PARENT), {"type": "askLiveClear", "id": PARENT}), self.sent,
                      "the tabs' own questions ride the same pass as before")

    def test_a_permission_prompt_rides_the_same_slot_and_an_answered_one_clears(self):
        self.asks[THREAD] = PERMISSION
        km._ask_poll_pass(self.clients, set())
        self.assertIn(("c1", ("asklive", THREAD), {"type": "askLive", "id": THREAD, "ask": PERMISSION}), self.sent)
        self.sent.clear()
        self.asks.pop(THREAD)
        km._ask_poll_pass(self.clients, {THREAD})
        self.assertIn(("c1", ("asklive", THREAD), {"type": "askLiveClear", "id": THREAD}), self.sent)

    def test_a_thread_that_leaves_the_map_gets_one_clear(self):
        prev = km._ask_poll_pass(self.clients, set())
        self.sent.clear()
        km._thread_live[0] = {}
        prev = km._ask_poll_pass(self.clients, prev)
        self.assertEqual(prev, set())
        self.assertEqual([(c, p) for c, k, p in self.sent if k == ("asklive", THREAD)],
                         [("c1", {"type": "askLiveClear", "id": THREAD}), ("c2", {"type": "askLiveClear", "id": THREAD})])
        self.sent.clear()
        km._ask_poll_pass(self.clients, prev)
        self.assertFalse([k for _c, k, _p in self.sent if k == ("asklive", THREAD)], "once, not every tick")


class AnswerPath(unittest.TestCase):
    """The answer reaches the thread through the chat's own path: _drive routes answerAsk by the thread's sid."""

    def test_answer_submit_and_cancel_route_to_the_threads_backend_by_its_sid(self):
        calls = []

        class Be:
            def on_ask(self, sid, kind, payload=None):
                calls.append((sid, kind, payload))
                return True

            def current_ask(self, sid):
                return ASK
        saved = (km._kernel_knows, km.Sessions.backend_for, km._mark_views_dirty, km._predict_working)
        km._kernel_knows = lambda sid: True
        km.Sessions.backend_for = staticmethod(lambda sid: Be())
        km._mark_views_dirty = lambda *a, **k: None
        km._predict_working = lambda *a, **k: None
        client = {"send": lambda s: None, "app": "chat"}
        try:
            self.assertTrue(km._drive({"type": "answerAsk", "id": THREAD, "target": 2}, client))
            self.assertTrue(km._drive({"type": "submitAsk", "id": THREAD}, client))
            self.assertTrue(km._drive({"type": "addCustomAsk", "id": THREAD, "text": "ninety seconds"}, client))
        finally:
            km._kernel_knows, km._mark_views_dirty, km._predict_working = saved[0], saved[2], saved[3]
            km.Sessions.backend_for = staticmethod(saved[1])
        self.assertEqual(calls, [(THREAD, "answer", 2), (THREAD, "submit", None), (THREAD, "custom", "ninety seconds")])


class _ThreadBackend:
    """A backend stub with the reads the comments frame and the thread's chat build make, reporting `state`."""

    def __init__(self, state):
        self.state = state

    def session_state(self, sid):
        return self.state

    def launch_error(self, sid):
        return None

    def pending_cut(self, sid):
        return ""

    def owns(self, sid):
        return True

    def pending_queued(self, sid):
        return []

    def live_atoms(self, sid):
        return []

    def prune_live(self, *a, **k):
        return None

    def live_sessions(self):
        return {}

    def session_since(self, sid):
        return 0

    def session_meta(self, sid):
        return {}

    def current_ask(self, sid):
        return None


class FrameChip(unittest.TestCase):
    """The comments frame's `chip`: the chat's own state chip for a running thread (its build gets its live row), "" for
    a dormant one."""

    def setUp(self):
        self._saved = (jd.STATE, jd.PROJECTS, km._sdk)
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._thread_msgs_cache.clear(); km._built_thread.clear(); km._parse_cache.clear()
        km._thread_reg_memo.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        jd._rebind_state(self._saved[0])
        jd.PROJECTS = self._saved[1]
        km._sdk = self._saved[2]
        km._built_thread.clear()
        shutil.rmtree(self._td, ignore_errors=True)

    def _seed(self, tail):
        t = self.now - 500
        recs = [uline(t, "how should the retry loop back off?", "u1"),
                aline(t + 5, "Use exponential backoff with a jitter of ten percent.", "a1", parent="u1"),
                uline(t + 100, km._comment_first_message("exponential backoff", "Which cap should it use?"), "cu1",
                      parent="a1")] + tail
        (self.proj / (THREAD + ".jsonl")).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        (jd.SDKDIR / (THREAD + ".json")).write_text(json.dumps(
            {"sid": THREAD, "name": "web-comment-1", "cwd": self.cdir, "lastSid": THREAD, "alive": True, "threadOf": PARENT}))
        km._save_comments(PARENT, {"threads": [
            {"tid": THREAD, "sid": THREAD, "anchorUuid": "a1", "cutUuid": "a1", "exact": "exponential backoff",
             "status": "open", "createdT": self.now - 400, "lastSeenT": self.now}]})

    def _frame(self, state, row_state=None):
        km._sdk = lambda: _ThreadBackend(state)
        km._built_thread.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        tl = {}
        if row_state is not None:
            tl[THREAD] = dict(km._sdk_live_norm({"state": row_state, "model": "Opus 5", "effort": "high"}), threadOf=PARENT)
        return km._comments_frame(PARENT, {}, thread_live=tl)["threads"][0]

    def test_a_thread_on_a_question_reads_needs_input_and_on_a_permission_prompt_too(self):
        self._seed([aline(self.now - 380, "Let me ask which cap you want.", "ca1", parent="cu1", stop="tool_use")])
        self.assertEqual(self._frame("picker", "picker")["chip"], "needsInput")
        self.assertEqual(self._frame("permission", "permission")["chip"], "needsInput")

    def test_a_thread_whose_turn_died_on_an_api_error_reads_blocked(self):
        self._seed([err_line(self.now - 380, "ce1", "cu1")])
        self.assertEqual(self._frame("waiting", "waiting")["chip"], "blocked", "the chat's chip: the stop shows on it")

    def test_a_working_thread_reads_working_and_a_dormant_one_ships_no_chip(self):
        self._seed([aline(self.now - 380, "Checking the sync loop first.", "ca1", parent="cu1", stop="tool_use")])
        self.assertEqual(self._frame("working", "working")["chip"], "working")
        dormant = self._frame("", None)
        self.assertEqual(dormant["chip"], "", "no live row: the page reads the raw state, as before")
        self.assertEqual(dormant["state"], "")

    def test_the_frame_says_this_kernel_answers_thread_questions(self):
        # the capability a page newer than its kernel reads to keep the old Break out note (the review of #232)
        self._seed([aline(self.now - 380, "Let me ask which cap you want.", "ca1", parent="cu1", stop="tool_use")])
        km._sdk = lambda: _ThreadBackend("picker")
        km._built_thread.clear()
        fr = km._comments_frame(PARENT, {}, thread_live={})
        self.assertIn("threadAsks", fr["caps"])

    def test_the_served_build_serves_its_chip_too(self):
        self._seed([aline(self.now - 380, "Let me ask which cap you want.", "ca1", parent="cu1", stop="tool_use")])
        km._sdk = lambda: _ThreadBackend("picker")
        km._built_thread.clear()
        tl = {THREAD: dict(km._sdk_live_norm({"state": "picker"}), threadOf=PARENT)}
        first = km._comments_frame(PARENT, {}, thread_live=tl)["threads"][0]
        self.assertIn(THREAD, km._built_thread)
        second = km._comments_frame(PARENT, {}, thread_live=tl)["threads"][0]
        self.assertEqual((first["chip"], second["chip"]), ("needsInput", "needsInput"))

    # ── thread-live-2: the popover's live painters read the build's own status and background tasks ──────────────────

    def _live_frame(self, row):
        km._sdk = lambda: _ThreadBackend(row.get("state") or "")
        km._built_thread.clear(); km._parse_cache.clear(); jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        tl = {THREAD: dict(km._sdk_live_norm(dict({"model": "Opus 5", "effort": "high"}, **row)), threadOf=PARENT)}
        return km._comments_frame(PARENT, {}, thread_live=tl)["threads"][0]

    def test_a_thread_waiting_on_a_background_command_reads_awaiting_with_its_rows_never_ready(self):
        self._seed([aline(self.now - 380, "The export runs in the background; I'll pick up when it lands.", "ca1", parent="cu1")])
        th = self._live_frame({"state": "waiting", "ctx": 41,
                               "bgTasks": [{"desc": "export the notes table", "type": "local_bash", "since": self.now - 300,
                                            "toolUseId": "tu-export", "lastTool": ""}]})
        self.assertEqual(th["chip"], "awaitingBg", "the chat's chip: Awaiting, not Ready")
        st = th["live"]["status"]
        self.assertEqual(st["state"], "awaitingBg")
        self.assertIn("export the notes table", st["awaitingWhy"])
        self.assertEqual([i["label"] for i in st["awaitingItems"]], ["export the notes table"], "the box's rows")
        self.assertEqual(st["awaitingTaskIds"], ["tu-export"])
        self.assertEqual(st["ctx"], "41", "the context gauge's fill, as the chat's status carries it")
        self.assertEqual(set(st) - set(km._THREAD_LIVE_STATUS_KEYS), set(), "a projection of the tab's status, no more")
        self.assertIsNotNone(th["live"]["bgTasks"], "the box's tracked tasks ride too")

    def test_a_thread_stopped_on_an_api_error_carries_what_its_card_reads(self):
        # the live API-error card in the popover is the chat's own, drawn for the THREAD: its line and its buttons read
        # the stop's class off the thread's status. The projection carried none of it, so a thread stopped on a spend cap
        # read as about to retry and offered Retry now (the review of PR 470, 2026-11-15). Invented error wording.
        classes = {"apiSpendLimit": "API Error: 429 the monthly spend limit for this workspace was reached",
                   "apiModelLimit": "You've reached your model limit. Run /usage-credits to continue or switch models with /model",
                   "apiAuthErr": "Not logged in",
                   "apiRefusal": "The model's safeguards flagged this message"}
        flags = ("apiSpendLimit", "apiModelLimit", "apiAuthErr", "apiRefusal", "apiNoRetry", "retrySuppressed")
        for flag, text in classes.items():
            line = err_line(self.now - 380, "ce1", "cu1")
            line["message"]["content"][0]["text"] = text
            self._seed([line])
            st = self._live_frame({"state": "waiting"})["live"]["status"]
            self.assertEqual({k: st.get(k) for k in flags}, {k: k == flag for k in flags}, flag)
            self.assertEqual(st["state"], "blocked")
        # a transient error carries no class, and the thread the person stopped says so
        self._seed([err_line(self.now - 380, "ce1", "cu1")])
        st = self._live_frame({"state": "waiting"})["live"]["status"]
        self.assertEqual({k: st.get(k) for k in flags}, {k: False for k in flags})
        saved = km._session_retry_suppressed
        km._session_retry_suppressed = lambda sid: sid == THREAD
        try:
            self.assertIs(self._live_frame({"state": "waiting"})["live"]["status"]["retrySuppressed"], True)
        finally:
            km._session_retry_suppressed = saved
        for k in flags + ("retryNextAt", "retryTries"):
            self.assertIn(k, km._THREAD_LIVE_STATUS_KEYS)
            self.assertIn(k, st, "the tab's status carries it, so the projection has it to carry")

    def test_a_retrying_thread_carries_the_chats_live_retry_card(self):
        self._seed([aline(self.now - 380, "Checking the sync loop first.", "ca1", parent="cu1", stop="tool_use")])
        th = self._live_frame({"state": "retrying", "retryCount": 3,
                               "retryInfo": {"attempt": 3, "max": 10, "error": "overloaded", "nextAt": self.now + 20}})
        self.assertEqual(th["chip"], "retrying")
        cards = [e for e in th["events"] if e.get("kind") == "retrying"]
        self.assertEqual(len(cards), 1, "the chat's inline retry card, in the thread's own events: %r" % [e.get("kind") for e in th["events"]])
        self.assertEqual(cards[0]["retries"], 3)

    def test_a_dormant_thread_ships_no_live_view(self):
        self._seed([aline(self.now - 380, "Done.", "ca1", parent="cu1")])
        km._sdk = lambda: _ThreadBackend("")
        km._built_thread.clear()
        th = km._comments_frame(PARENT, {}, thread_live={})["threads"][0]
        self.assertIsNone(th["live"])

    def test_the_lent_row_is_the_threads_alone_and_the_scope_comes_back(self):
        seen = {}
        real = km._session_awaiting

        def spy(sid, path, idle, stamp=False):
            seen[sid] = (km._live_map().get(sid) or {}).get("state"), sorted(km._live_map())
            return real(sid, path, idle, stamp=stamp)
        self._seed([aline(self.now - 380, "Done for now.", "ca1", parent="cu1")])
        km._session_awaiting = spy
        before = getattr(km._live_scope, "snapshot", None)
        try:
            km._sdk = lambda: _ThreadBackend("waiting")
            km._built_thread.clear()
            tl = {THREAD: dict(km._sdk_live_norm({"state": "waiting"}), threadOf=PARENT)}
            km._comments_frame(PARENT, {OTHER_PARENT: {"state": "working"}}, thread_live=tl)
        finally:
            km._session_awaiting = real
        self.assertEqual(seen.get(THREAD), ("waiting", sorted([OTHER_PARENT, THREAD])),
                         "inside the thread's build its row is read where a tab's is, beside every other session's")
        self.assertIs(getattr(km._live_scope, "snapshot", None), before, "the scope it found is back")


    # ── the review of PR 240: a background command's output tail rides its own slot, read fresh, never the frame ─────────

    def _seed_bg(self, out_file):
        """A thread that started a background command whose launch ack names its output file, then went quiet on it."""
        self._seed([
            {"type": "assistant", "timestamp": iso(self.now - 380), "uuid": "ca1", "parentUuid": "cu1",
             "message": {"role": "assistant", "stop_reason": "tool_use", "content": [
                 {"type": "text", "text": "Starting the export in the background."},
                 {"type": "tool_use", "id": "tu-export", "name": "Bash",
                  "input": {"run_in_background": True, "command": "make export", "description": "export the notes table"}}]}},
            {"type": "user", "timestamp": iso(self.now - 379), "uuid": "cr1", "parentUuid": "ca1",
             "toolUseResult": {"status": "async_launched", "outputFile": out_file},
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu-export",
                                                      "content": "Command running in background."}]}},
            aline(self.now - 378, "The export runs in the background; I'll pick up when it lands.", "ca2", parent="cr1")])

    def test_a_background_commands_tail_rides_its_own_slot_fresh_and_never_the_comments_frame(self):
        of = Path(self._td) / "export.output"
        of.write_text("exported 120 rows\n")
        self._seed_bg(str(of))
        row = {"state": "waiting", "bgTasks": [{"desc": "export the notes table", "type": "local_bash", "since": self.now - 379,
                                                "toolUseId": "tu-export", "lastTool": ""}]}
        th = self._live_frame(row)
        tasks = th["live"]["bgTasks"]["tasks"]
        self.assertEqual([t["id"] for t in tasks], ["tu-export"], "the box's row rides the frame: %r" % tasks)
        self.assertNotIn("output", tasks[0], "…without its output tail")
        self.assertEqual(km._thread_bg_out_frame(THREAD),
                         {"type": "threadBgOut", "id": THREAD, "out": {"tu-export": "exported 120 rows"}}, "the tail, on its own frame")
        # the command writes on: the tail is read fresh, with no rebuild, and the comments frame does not move for it
        with open(of, "a") as f:
            f.write("exported 240 rows\n")
        tl = {THREAD: dict(km._sdk_live_norm(dict({"model": "Opus 5", "effort": "high"}, **row)), threadOf=PARENT)}
        real = km.build_session
        calls = []
        km.build_session = lambda *a, **k: calls.append(a) or real(*a, **k)
        try:
            again = km._comments_frame(PARENT, {}, thread_live=tl)["threads"][0]
        finally:
            km.build_session = real
        self.assertEqual(calls, [], "the thread's build is served: the output is not part of its key")
        self.assertEqual(json.dumps(again["live"], sort_keys=True), json.dumps(th["live"], sort_keys=True),
                         "the comments frame is unchanged, so the push does not resend it for the tail")
        self.assertEqual(km._thread_bg_out_frame(THREAD)["out"], {"tu-export": "exported 120 rows\nexported 240 rows"})

    def test_an_agent_rows_transcript_is_never_read_as_a_tail(self):
        real = km._bg_scan_cached
        km._bg_scan_cached = lambda path: [{"id": "tu-agent", "outputFile": "/tmp/TESTHOST/agent.output"},
                                           {"id": "tu-export", "outputFile": "/tmp/TESTHOST/export.output"}]
        try:
            files = km._thread_bg_files("/tmp/TESTHOST/thread.jsonl", {"count": 2, "tasks": [
                {"id": "tu-agent", "agentId": "a0123456789abcdef"}, {"id": "tu-export"}]})
        finally:
            km._bg_scan_cached = real
        self.assertEqual(files, {"tu-export": "/tmp/TESTHOST/export.output"}, "the box shows no output on an agent's row")

    def test_the_push_sends_each_running_threads_tail_on_its_slot_for_the_framed_tabs_only(self):
        of = Path(self._td) / "export.output"
        of.write_text("exported 120 rows\n")
        saved = km._thread_live[0]
        km._thread_out_files.clear()
        try:
            km._thread_out_files_set(THREAD, {"tu-export": str(of)})
            km._thread_out_files_set(OTHER_THREAD, {"tu-sync": str(of)})
            km._thread_live[0] = {THREAD: {"state": "waiting", "threadOf": PARENT},
                                  OTHER_THREAD: {"state": "waiting", "threadOf": OTHER_PARENT}}
            sent = []
            c = {"app": "chat", "send": sent.append, "sent": {}}
            km._send_thread_bg_out([c], {PARENT})
            self.assertEqual([json.loads(x) for x in sent], [{"type": "threadBgOut", "id": THREAD, "out": {"tu-export": "exported 120 rows"}}],
                             "the framed tab's thread alone")
            km._send_thread_bg_out([c], {PARENT})
            self.assertEqual(len(sent), 1, "an unchanged tail costs a comparison, nothing on the wire")
            with open(of, "a") as f:
                f.write("exported 240 rows\n")
            km._send_thread_bg_out([c], {PARENT})
            self.assertEqual(json.loads(sent[-1])["out"], {"tu-export": "exported 120 rows\nexported 240 rows"}, "a moving tail goes")
            km._thread_out_files_set(THREAD, {})
            self.assertIsNone(km._thread_bg_out_frame(THREAD), "a build with no output file sends nothing")
        finally:
            km._thread_live[0] = saved
            km._thread_out_files.clear()
        import inspect
        src = inspect.getsource(km._push)
        self.assertLess(src.index('_send_comments_frame(chat_clients, s["sid"], fr, gen)'),
                        src.index('_send_thread_bg_out(chat_clients, {s["sid"] for s in _frame_tabs})'),
                        "sent after the comments frames whose builds name the files")


class LiveStatusForThreads(unittest.TestCase):
    """A thread's thinking line and its subagents' progress ride the chat's liveStatus frames, sent with the tabs'."""

    def test_the_threads_frames_join_the_tabs_in_one_set(self):
        tab_frames = {PARENT: ({"type": "liveStatus", "id": PARENT}, "{}")}
        thread_map = {THREAD: {"state": "working", "threadOf": PARENT, "thinkingLive": {"t": 100.0},
                               "bgTasks": [{"toolUseId": "tu-agent", "lastTool": "Grep", "agentProgress": {"calls": 4, "tokens": 900}}]}}
        frames, allowed = km._chat_live_status_set(tab_frames, {PARENT}, thread_map)
        self.assertEqual(set(frames), {PARENT, THREAD})
        self.assertEqual(allowed, {PARENT, THREAD}, "the thread's frame may go to the chat clients, beside its parent's")
        f = frames[THREAD][0]
        self.assertEqual(f["thinking"], {"t": 100.0})
        self.assertEqual(f["tasks"], {"tu-agent": {"calls": 4, "tokens": 900, "tool": "Grep"}})
        self.assertIs(km._chat_live_status_set(tab_frames, {PARENT}, {})[0], tab_frames, "no thread running: the tabs' own set")

    def test_the_push_sends_the_one_set(self):
        import inspect
        src = inspect.getsource(km._push)
        self.assertIn("ls_chat, ls_allowed = _chat_live_status_set(ls_frames[0], tab_set, _thread_live_map())", src)
        self.assertIn("_send_live_status(c, ls_chat, ls_allowed)", src)
        self.assertNotIn("_send_live_status(c, ls_frames[0], tab_set)", src, "never a second send that would clear the threads'")


if __name__ == "__main__":
    unittest.main()
