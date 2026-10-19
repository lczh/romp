#!/usr/bin/env python3
"""Kernel half of the SDK-lifecycle hardening (2026-07-05):

  * parked-ops persistence — _pending_ops mirrors to pending-ops.json on every mutation and is
    restored at boot, so a kernel restart can't silently drop messages the user queued against a
    busy session;
  * POST /interrupt + /end — the headless control routes mirroring the WS drive ops (before this a
    session could be FED without a browser but never STOPPED);
  * wiring pins — main() installs the SIGTERM drain handler and constructs the SDK backend eagerly
    with reconcile=True (source-pinned, same style as test_sdk_kernel's dispatch pins);
  * _sdk() single-flight — the eager boot thread races handler threads, and an unlocked
    check-then-act built 2-3 duplicate SdkBackends whose reconciles reaped each other's live CLIs
    (2026-07-06 kill storm: sessions dying with exit 143 mid-turn).

XDG_STATE_HOME is pointed at a temp dir BEFORE the kernel module loads, so jd.STATE — and with it
pending-ops.json and every state write — stays out of the live user state (see
[[distiller-giveup-rearm]]: leaking test state into ~/.local/state/romp corrupts live behavior).
"""
import json
import os
import tempfile
import unittest
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
_STATE_TMP = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _STATE_TMP
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_headless", os.path.join(BIN, "romp-kernel"))

# The ACCOUNT gate (_limit_hold: a usage limit / monthly spend cap parks every drive op, tested in
# tests/test_kernel_limit_queue.py) is a SEPARATE axis from the compaction/busy gates this module
# covers. Neutralize it here: left live, these tests would read the REAL machine's usage.json and
# start parking — correctly, but for a reason none of them is about — the moment that account hit a
# limit. Pinning it off keeps them hermetic.
km._limit_hold = lambda sid: None

# The PROMPT HOLD (_hold_drain: a turn-opening delivery whose backend did not read busy() True inside
# send() holds the sid for a moment; built for the tmux backend, removed 2026-09-11, and kept as a defensive
# arm; tested in tests/test_kernel_parked_ops_liveness.py) is a separate axis: off here, so back-to-back
# _apply_pending_ops calls stand for successive cycles.
km._PROMPT_HOLD_S = 0.0


class PendingOpsPersistence(unittest.TestCase):
    def setUp(self):
        km._pending_ops.clear()
        try:
            os.unlink(km._PENDING_OPS_FILE)
        except OSError:
            pass

    def test_state_dir_is_isolated(self):
        self.assertTrue(str(km._PENDING_OPS_FILE).startswith(_STATE_TMP),
                        "the test must never write the live pending-ops.json")

    def test_park_mirrors_to_disk_and_reload_restores(self):
        km._park_op("sid-1", ("send", "queued while busy", "human"))
        km._park_op("sid-1", ("model", "opus"))
        km._park_op("sid-1", ("model", "sonnet"))     # replace-in-place also persists
        on_disk = json.loads(km._PENDING_OPS_FILE.read_text())
        self.assertEqual(on_disk, {"sid-1": [["send", "queued while busy", "human"],
                                             ["model", "sonnet"]]})
        # a fresh kernel's boot path restores the same queues, as tuples
        self.assertEqual(km._load_pending_ops(),
                         {"sid-1": [("send", "queued while busy", "human"), ("model", "sonnet")]})

    def test_delivery_shrinks_the_disk_mirror(self):
        km._park_op("sid-2", ("effort", "high"))
        fake = mock.Mock()
        with mock.patch.object(km, "_compacting_now", return_value=False), \
             mock.patch.object(km, "_working_now", return_value=False), \
             mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            km._apply_pending_ops()
        fake.set_effort.assert_called_once_with("sid-2", "high")
        self.assertEqual(km._load_pending_ops(), {}, "a delivered op leaves the disk mirror")

    def test_missing_file_loads_empty(self):
        self.assertEqual(km._load_pending_ops(), {})


class HeadlessRoutes(unittest.TestCase):
    """POST /interrupt and /end over the REAL handler on loopback (the ServeSecurity pattern)."""

    @classmethod
    def setUpClass(cls):
        import threading
        from http.server import ThreadingHTTPServer
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        # the End latch is module state (2026-09-21): the end route's test latches the synthetic sid it ends, and a later
        # test that parks a pick for the same sid would be refused as a park on an ending session; a live kernel lifts
        # the latch when the session comes back under its sid, which a Mock backend never does
        km._ending_sids.clear()

    def _post(self, path, body):
        import urllib.request, urllib.error
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     method="POST", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Romp-Token": km.TOKEN})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode() or "{}")

    def test_interrupt_route_mirrors_the_ws_op(self):
        fake = mock.Mock()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            code, resp = self._post("/interrupt", {"name": "runaway"})
        self.assertEqual((code, resp), (200, {"ok": True}))
        fake.interrupt.assert_called_once()
        sid = fake.interrupt.call_args[0][0]
        self.assertIn(str(sid), km._interrupt_clicked,
                      "the chat chip flips to 'interrupting' exactly like the WS op")

    def test_interrupt_route_paints_nothing_when_the_backend_refuses(self):
        # the WS op's gate, mirrored (2026-09-11): a stop the backend refused — a Codex session with no
        # turn in flight, a dead tab — interrupted nothing, so the chip must not read Interrupting… for
        # the 120 s cap, the only thing that could clear a stamp whose stop record never comes
        fake = mock.Mock()
        fake.interrupt.return_value = False
        fake.busy.return_value = None                     # a dead tab: no backend, no in-flight signal (a bare Mock is truthy)
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            code, resp = self._post("/interrupt", {"name": "web"})
        self.assertEqual(code, 200)
        fake.interrupt.assert_called_once()
        sid = fake.interrupt.call_args[0][0]
        self.assertNotIn(str(sid), km._interrupt_clicked, "a refused stop leaves no optimistic stamp")

    def test_interrupt_route_says_when_the_stop_did_not_land(self):
        # a refusal WITH work in flight (the Codex backend's False while a turn's start is still being
        # acknowledged, with its client gone, or after a failed interrupt RPC) is a stop that did not land:
        # the /send route's refusal shape, so `romp interrupt` exits non-zero instead of printing ok
        fake = mock.Mock()
        fake.interrupt.return_value = False
        fake.busy.return_value = True
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            code, resp = self._post("/interrupt", {"name": "web"})
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, "a dropped stop is never answered ok")
        self.assertEqual(resp.get("error"), "the stop was not delivered: web is still working")
        sid = fake.interrupt.call_args[0][0]
        self.assertNotIn(str(sid), km._interrupt_clicked, "no stop landed, so nothing reads Interrupting…")
        # (a bare Mock's compacting() answers a truthy Mock, not True: the compaction arm below keys on identity, so
        # this fake still reads as working, 2026-09-21)

    def test_interrupt_route_says_when_the_session_is_compacting(self):
        # Esc, Stop, POST /interrupt and `romp interrupt` during a compaction answered "still working" under a chip
        # reading Compacting (review find, 2026-09-21): busy() is True under the bracket and interrupt() is the no-turn
        # False, so both doors fell to the busy branch. The backend's own compacting() verdict, read by identity ahead
        # of busy, is worded for the state and names no backend.
        fake = mock.Mock()
        fake.interrupt.return_value = False
        fake.compacting.return_value = True
        fake.busy.return_value = True
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)):
            code, resp = self._post("/interrupt", {"name": "web"})
        self.assertEqual(code, 200)
        self.assertIs(resp.get("ok"), False, "a stop a compaction cannot take is never answered ok")
        self.assertEqual(resp.get("error"),
                         "the stop was not delivered: web is compacting its conversation, and a compaction runs to its end")
        sid = fake.interrupt.call_args[0][0]
        self.assertNotIn(str(sid), km._interrupt_clicked, "nothing reads Interrupting…")

    def test_end_route_kills_and_announces_close(self):
        fake = mock.Mock()
        sent = []
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
             mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))):
            code, resp = self._post("/end", {"id": "sid-x"})
        self.assertEqual((code, resp), (200, {"ok": True}))
        fake.kill.assert_called_once()
        self.assertIn(("chat", {"type": "closed", "id": fake.kill.call_args[0][0]}), sent)

    def test_end_route_counts_the_typed_messages_it_handed_back(self):
        # The route answered ok whatever the hand-back did (review find, 2026-09-21): a shell caller, often a peer
        # session with no chat pane open, read a plain ok while the message it had typed went to a not-delivered
        # frame that reached nobody. The answer carries the count of typed texts handed back, present only when
        # nonzero, so the nothing-parked answer above stays the plain ok; a machine's send parked beside the typed
        # one is dropped with a log line and not counted.
        import contextlib, io
        sid = "33333333-4444-5555-6666-777777777777"
        path = km.jd.STATE / "undelivered.jsonl"
        before = path.read_bytes() if path.exists() else None
        fake = mock.Mock()
        sent = []
        km._pending_ops.clear()
        try:
            km._park_op(sid, ("send", "typed while it was busy", "human", None, True))
            km._park_op(sid, ("send", "a notice the kernel composed", "human"))
            log = io.StringIO()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_send_to_app", side_effect=lambda app, m: sent.append((app, m))), \
                 contextlib.redirect_stderr(log):
                code, resp = self._post("/end", {"id": sid})
            self.assertEqual((code, resp), (200, {"ok": True, "undelivered": 1}),
                             "the answer counts the typed text handed back, and only that")
            fake.kill.assert_called_once()
            self.assertEqual([m.get("copy") for app, m in sent if app == "chat" and m.get("type") == "err"],
                             ["typed while it was busy"], "the typed text took the not-delivered path once")
            self.assertIn("parked send op dropped with the ending session %s" % sid, log.getvalue())
            self.assertIn(("chat", {"type": "closed", "id": sid}), sent, "the tab closes as before")
            self.assertNotIn(sid, km._pending_ops, "the ending session's queue is gone with it")
        finally:
            km._pending_ops.clear()
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)

    def test_a_tagged_send_parks_as_a_machines_and_lifts_nothing(self):
        # T315 (the commit-14 review's sixth item, driven through the route): `romp send --tag` is a machine's
        # message: the parked op carries no fifth slot, and a delivered one hands the backend no user flag
        fake = mock.Mock()
        fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: True):
                code, resp = self._post("/send", {"id": "sid-q", "text": "/nightly report", "tag": "cron"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
            [[op]] = list(km._pending_ops.values())
            self.assertEqual(op[:2], ("command", "/nightly report\n\n<!-- romp-tag: cron -->"))
            self.assertFalse(km._op_user(op), "a tagged send is a machine's: no fifth slot")
            fake.send.assert_not_called()
            km._pending_ops.clear()
            class Speaking:                       # a send that TAKES the keyword, so the route's decision is what is recorded
                def __init__(self): self.calls = []
                def busy(self, sid): return None
                def send(self, sid, text, qid=None, user=False):
                    self.calls.append((text, user)); return True
            spk = Speaking()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: spk)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: False):
                code, resp = self._post("/send", {"id": "sid-q", "text": "a scripted note", "tag": "cron"})
                self.assertEqual(code, 200)
                self.assertEqual(spk.calls[-1][1], False, "a tagged send is handed over as a machine's: it lifts no stand-down")
                code, resp = self._post("/send", {"id": "sid-q", "text": "typed by hand"})
                self.assertEqual(code, 200)
                self.assertEqual(spk.calls[-1], ("typed by hand", True), "an untagged send is the user's")
        finally:
            km._pending_ops.clear()

    def test_send_route_reports_queued_vs_sent(self):
        # `queued` says which arm the send took (2026-09-03): an agent sending ITSELF a slash command from
        # inside its own turn read 'ok' and could not know the command was parked until that turn ended
        fake = mock.Mock()
        fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: True):
                code, resp = self._post("/send", {"id": "sid-q", "text": "/frobnicate now"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
            self.assertEqual(list(km._pending_ops.values()), [[("command", "/frobnicate now", None, None, True)]],
                             "an untagged POST /send is the user's words: the fifth slot says so (T315), no id in the fourth")
            fake.send.assert_not_called()
            km._pending_ops.clear()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: False):
                code, resp = self._post("/send", {"id": "sid-q", "text": "/frobnicate now"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
            fake.send.assert_called_once()
            self.assertEqual(fake.send.call_args[0][1], "/frobnicate now")
        finally:
            km._pending_ops.clear()

    def test_send_route_reports_a_parked_meta_command_as_queued(self):
        # /model, /effort and /fast take the kernel's own setters (_route_meta_command), which park under
        # the same gate as a text send — the route must say `queued` for them too (review find, 2026-09-03)
        fake = mock.Mock()
        fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: True):
                code, resp = self._post("/send", {"id": "sid-m", "text": "/effort high"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
            self.assertEqual(list(km._pending_ops.values()), [[("effort", "high")]], "parked as the setter's op")
            fake.set_effort.assert_not_called()
            km._pending_ops.clear()
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_compacting_now", lambda sid, **k: False), \
                 mock.patch.object(km, "_working_now", lambda sid: False):
                code, resp = self._post("/send", {"id": "sid-m", "text": "/effort high"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
            fake.set_effort.assert_called_once()
        finally:
            km._pending_ops.clear()

    def test_send_route_reads_the_setters_locked_verdict_not_a_second_gate(self):
        # #954 moved the deciding park under the queue lock (_gate_or_park); the route must report the
        # setter's OWN verdict, not a separate unlocked _ops_gate read that can disagree (review find on
        # #954, 2026-09-07: a parked /model answered queued:false). Force the two apart: _gate_or_park
        # parks (True) while _ops_gate reads False.
        fake = mock.Mock(); fake.busy.return_value = None
        km._pending_ops.clear()
        try:
            with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
                 mock.patch.object(km, "_ops_gate", lambda sid: False), \
                 mock.patch.object(km, "_gate_or_park", lambda sid, op: (km._pending_ops.setdefault(str(sid), []).append(op) or True)):
                code, resp = self._post("/send", {"id": "sid-x", "text": "/model sonnet"})
            self.assertEqual((code, resp), (200, {"ok": True, "queued": True}),
                             "the route reports the setter's locked park, not the unlocked gate")
            fake.set_model.assert_not_called()
        finally:
            km._pending_ops.clear()

    def test_send_route_passes_a_remote_kernels_queued_through(self):
        # a session living on another kernel: its answer's `queued` rides back to the caller; an older
        # remote without the field reads as not queued (today's behaviour)
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: {"ok": True, "queued": True}):
            code, resp = self._post("/send", {"id": "sid-r", "text": "/frobnicate now"})
        self.assertEqual((code, resp), (200, {"ok": True, "queued": True}))
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: {"ok": True}):
            code, resp = self._post("/send", {"id": "sid-r", "text": "hello"})
        self.assertEqual((code, resp), (200, {"ok": True, "queued": False}))
        # …and a far kernel's REFUSAL rides back as itself, never rewritten into an ok (review find, #904)
        refusal = {"ok": False, "error": "isolation: the target session's mailbox is OFF"}
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: dict(refusal)):
            code, resp = self._post("/send", {"id": "sid-r", "text": "hello"})
        self.assertEqual((code, resp), (200, refusal))

    def test_missing_who_is_a_400(self):
        code, resp = self._post("/interrupt", {})
        self.assertEqual(code, 400)
        self.assertFalse(resp.get("ok"))

    def _nothing_running(self):
        # no backend owns any sid (the real unowned route) and none lists a live row; the names registry is the module's
        # temp state, so only a sid a test registers there is one this kernel has had
        import contextlib
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: km._UNOWNED)))
        stack.enter_context(mock.patch.object(km.Sessions, "live", staticmethod(lambda: {})))
        stack.enter_context(mock.patch.object(km, "_thread_names", lambda: {}))
        stack.enter_context(mock.patch.object(km, "_host_for_sid", lambda sid: None))
        stack.enter_context(mock.patch.object(km, "_send_to_app", lambda app, m: None))
        return stack

    def test_interrupt_and_end_refuse_a_session_this_kernel_never_had(self):
        # 2026-10-18: a name or id no session here has ever had (a typo, a stale name, or `romp end --now web` posting
        # `--now`) fell through both routes to ok, and /end wrote a death record and an idle row under the raw string,
        # so the CLI exited 0 while the session meant kept running. Refused in /apply's words, with nothing written.
        unknown_sid = "7c0e5a1d-2b3f-4e6a-8d9c-0f1e2d3c4b5a"   # a private synthetic id no test registers
        with self._nothing_running():
            for path in ("/interrupt", "/end"):
                for key, who in (("name", "no-such"), ("name", "--now"), ("id", unknown_sid)):
                    code, resp = self._post(path, {key: who})
                    self.assertEqual((code, resp), (200, {"ok": False, "error": "no session named %s on this romp kernel" % who}),
                                     "%s for %s is refused, never an ok" % (path, who))
                    self.assertFalse((km.jd.STATE / "gone" / (who + ".json")).exists(), "no death record under the raw string")
                    self.assertFalse((km.jd.STATE / "states" / (who + ".jsonl")).exists(), "no idle row under the raw string")
                    self.assertNotIn(who, km._ending_sids, "nothing latched as ending")
            code, resp = self._post("/end", {"name": "no-such", "when": "idle"})
        self.assertEqual((code, resp), (200, {"ok": False, "error": "no session named no-such on this romp kernel"}))
        self.assertNotIn("no-such", km._end_on_idle_load(), "no deferred end recorded for it either")

    def test_a_session_this_kernel_has_that_is_not_running_keeps_its_answers(self):
        # the other side of the refusal above: a session in the names registry with no backend running it (a dead tab)
        # is ours, so the routes answer as they always did, and an end records its death
        sid = "9a8b7c6d-5e4f-4a3b-8c2d-1e0f9a8b7c6d"     # a private synthetic id, registered here only
        km.NAMES.mkdir(parents=True, exist_ok=True)
        reg = km.NAMES / sid
        reg.write_text("web\t/TESTDIR\t\t\n")
        gone = km.jd.STATE / "gone" / (sid + ".json")
        states = km.jd.STATE / "states" / (sid + ".jsonl")
        for p in (reg, gone, states):
            self.addCleanup(p.unlink, missing_ok=True)
        with self._nothing_running():
            self.assertEqual(self._post("/interrupt", {"id": sid}), (200, {"ok": True}))
            self.assertEqual(self._post("/end", {"id": sid}), (200, {"ok": True}))
        self.assertTrue(gone.exists(), "the end of a session we have records its death, as before")

    def test_apply_route_pushes_a_waiting_pick_through_and_reads_nothing_waiting_as_a_no_op(self):
        # `romp apply <session>` (2026-10-01): the dashboard's Apply now, headless, so a script can push the picks waiting
        # on many sessions through. Nothing waiting is an ok the CLI exits 0 on (applied: false), never a refusal, so a
        # loop over every session is safe; a refusal is the backend's own words.
        fake = mock.Mock()
        with mock.patch.object(km.Sessions, "backend_for", staticmethod(lambda sid: fake)), \
             mock.patch.object(km, "_kernel_knows", lambda sid: True), \
             mock.patch.object(km, "_host_for_sid", lambda sid: None):
            fake.apply_now.return_value = ""
            self.assertEqual(self._post("/apply", {"name": "web"}), (200, {"ok": True, "applied": True}))
            fake.apply_now.return_value = km.sb.NOTHING_TO_APPLY
            self.assertEqual(self._post("/apply", {"name": "web"}),
                             (200, {"ok": True, "applied": False, "why": km.sb.NOTHING_TO_APPLY}))
            fake.apply_now.return_value = "this session is not running — revive it to bring it back"
            self.assertEqual(self._post("/apply", {"name": "web"}),
                             (200, {"ok": False, "error": "this session is not running — revive it to bring it back"}))
        self.assertEqual(fake.apply_now.call_count, 3)
        with mock.patch.object(km, "_kernel_knows", lambda sid: False), mock.patch.object(km, "_host_for_sid", lambda sid: None):
            self.assertEqual(self._post("/apply", {"name": "ghost"}),
                             (200, {"ok": False, "error": "no session named ghost on this romp kernel"}))
        self.assertEqual(self._post("/apply", {})[0], 400)

    def test_apply_route_forwards_a_remote_session_and_says_when_its_kernel_is_silent(self):
        seen = []
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: seen.append((path, body)) or {"ok": True, "applied": False, "why": "far"}):
            self.assertEqual(self._post("/apply", {"id": "sid-r"}), (200, {"ok": True, "applied": False, "why": "far"}))
        self.assertEqual(seen, [("/apply", {"id": "sid-r"})], "the far kernel's answer, verbatim")
        with mock.patch.object(km, "_host_for_sid", lambda sid: {"host": "TESTHOST"}), \
             mock.patch.object(km, "_remote_forward", lambda r, path, body: None):
            code, resp = self._post("/apply", {"id": "sid-r"})
        self.assertEqual(code, 200)
        self.assertFalse(resp["ok"])
        self.assertIn("isn't answering — nothing was applied", resp["error"])


class CodexRuntimeSelection(unittest.TestCase):
    # The kernel loads codex_backend.py through load_source (kernel/loadsource.py): patch that.
    def test_path_codex_does_not_override_managed_runtime(self):
        fake_mod = mock.Mock()
        with mock.patch.object(km, "_codex_backend", None), \
             mock.patch.object(km, "load_source", return_value=fake_mod), \
             mock.patch.object(km.shutil, "which", return_value="/TESTBIN/codex"):
            backend = km._codex()
            self.assertIs(backend, fake_mod.CodexBackend.return_value)
            self.assertIs(km._codex(), backend)
        fake_mod.CodexBackend.assert_called_once()
        self.assertIsNone(fake_mod.CodexBackend.call_args.kwargs.get("codex_bin"),
                          "the backend must resolve its managed runtime even when codex is on PATH")

    def test_romp_codex_bin_overrides_the_session_runtime(self):
        # PATH is ignored, but the one explicit knob the judges already read (ROMP_CODEX_BIN) governs
        # sessions too — an opt-in, not the ambient PATH accident #929 closed (review fold, 2026-09-07)
        fake_mod = mock.Mock()
        with mock.patch.object(km, "_codex_backend", None), \
             mock.patch.object(km, "load_source", return_value=fake_mod), \
             mock.patch.dict(km.os.environ, {"ROMP_CODEX_BIN": "/opt/codex/bin/codex"}), \
             mock.patch.object(km.shutil, "which", return_value="/TESTBIN/codex"):
            km._codex()
        self.assertEqual(fake_mod.CodexBackend.call_args.kwargs.get("codex_bin"), "/opt/codex/bin/codex",
                         "the explicit knob is forwarded; PATH is still not")


class SdkSingleFlight(unittest.TestCase):
    """Concurrent _sdk() calls must construct exactly ONE backend. The 2026-07-06 storm: the eager
    boot thread + handler threads each passed the unlocked `if _sdk_backend is None` check and built
    their own SdkBackend; every duplicate ran its own boot reconcile, and each reconcile reaped the
    others' freshly-resumed LIVE CLIs (no ppid filter then), killing sessions mid-turn."""

    def test_concurrent_calls_build_one_backend(self):
        import threading
        built = []
        gate = threading.Event()

        class FakeBackend:
            def __init__(self):
                gate.wait(2)                       # hold construction open so every racer arrives
                built.append(self)

        fake_mod = mock.Mock()
        fake_mod.SdkBackend = lambda *a, **k: FakeBackend()
        prev = km._sdk_backend
        # _sdk() wires the judge's hooks to the backend it builds, here a FakeBackend from a Mock module; left behind,
        # jd._LOGIN_AUTH_ENV_FN (the Mock's startup_auth_env) broke every later login-billed judge call in the process
        # until another test built a real backend (2026-09-30, found when CI split the suite into shards).
        hooks = {h: getattr(km.jd, h) for h in ("_LOGIN_AUTH_ENV_FN", "_USAGE_REFRESH_FN", "_DEFAULT_AUTH_FN",
                                                  "_DEFAULT_LOGIN_FN", "_API_HEALTH_NOTE_FN")}
        try:
            km._sdk_backend = None
            results = [None] * 6
            with mock.patch.object(km, "load_source", return_value=fake_mod), \
                 mock.patch.object(km, "_ensure_sdk_on_path", return_value=True):
                ts = [threading.Thread(target=lambda i=i: results.__setitem__(i, km._sdk()))
                      for i in range(6)]
                for t in ts:
                    t.start()
                gate.set()
                for t in ts:
                    t.join(5)
            self.assertEqual(len(built), 1, "one construction, however many racers")
            self.assertTrue(all(r is built[0] for r in results),
                            "every caller gets the same singleton")
        finally:
            km._sdk_backend = prev
            for h, v in hooks.items():
                setattr(km.jd, h, v)


class WiringPins(unittest.TestCase):
    """Source pins (the test_sdk_kernel style) for boot/shutdown wiring that can't run in-process:
    delivering a real SIGTERM would os._exit the test runner."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(BIN, "romp-kernel")) as f:
            cls.src = f.read()

    def test_sigterm_handler_installed_in_main(self):
        self.assertIn("signal.signal(signal.SIGTERM, _graceful_term)", self.src)
        self.assertIn("be.drain(", self.src, "the handler drains the SDK backend")
        self.assertIn("os._exit(0)", self.src, "and always exits — a hung drain can't wedge the restart")

    def test_backend_constructed_eagerly_with_reconcile(self):
        self.assertIn("reconcile=True", self.src,
                      "the kernel opts into the boot reconcile (tests construct without it)")
        self.assertIn('threading.Thread(target=_sdk, daemon=True, name="sdk-boot").start()', self.src,   # named for the stack sample (T401)
                      "main() constructs the backend at boot so the reconcile isn't lazy")

    def test_graceful_term_never_constructs_the_backend(self):
        body = self.src.split("def _graceful_term", 1)[1].split("\ndef ", 1)[0]
        self.assertNotIn("_sdk()", body,
                         "shutdown must use the existing singleton only — constructing the backend "
                         "while dying makes no sense and can hang the drain")


if __name__ == "__main__":
    unittest.main()
