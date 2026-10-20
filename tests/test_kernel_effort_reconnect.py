"""Effort-switch UX (the user 2026-07-06): /effort has no SDK runtime control, so romp applies it by
RECONNECTING the session (resume) — which otherwise leaves nothing in the chat. Now the effort badge shows
switching-dots and the chat shows a transient "Reloading session…" element while the reconnect is pending,
both driven by an `effortPending` flag that mirrors `modelPending` end-to-end and clears when the new client
connects. Source pins on build_session + the SDK backend."""
import inspect
import json
import os
import shutil
import unittest
from romp_load import load_source
import tempfile

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_efr", os.path.join(BIN, "romp-kernel"))
BACKEND_SRC = open(os.path.join(BIN, "romp_sdk_backend.py")).read()
SDB = load_source("romp_sdk_backend_efr", os.path.join(BIN, "romp_sdk_backend.py"))


class EffortReconnect(unittest.TestCase):
    def test_build_session_emits_a_reconnecting_event_while_effort_pending(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('if (tm0 or {}).get("effortPending"):', src)
        self.assertIn('events.append({"kind": "reconnecting", "effort": (tm0 or {}).get("effort") or "",', src)
        # …and what the reload waits for, since a pick waits for a quiet session instead of cutting its work (2026-10-01)
        self.assertIn('"waits": (tm0 or {}).get("relaunchWaits") or ""})', src)

    def test_the_reconnecting_notice_precedes_the_queued_bubble(self):
        # like the compacting element, it must sit ABOVE any queued/provisional message
        src = inspect.getsource(km.build_session)
        i_recon = src.index('events.append({"kind": "reconnecting"')
        i_queued = src.index('events.append({"kind": "queued"')
        self.assertGreater(i_recon, 0)
        self.assertGreater(i_queued, i_recon)

    def test_status_dict_carries_effortPending_for_the_badge_dots(self):
        src = inspect.getsource(km.build_session)
        self.assertIn('"effortPending": bool(tm.get("effortPending")),', src)

    def test_sessions_live_passes_effortPending_through_from_the_sdk_backend(self):
        src = inspect.getsource(km._sdk_live_norm)
        self.assertIn('"effortPending": bool(st.get("effortPending")),', src)

    def test_what_a_pending_relaunch_waits_for_reaches_the_status_and_the_chat(self):
        # 2026-10-01: a pick waits for a quiet session instead of cutting its subagents and background tasks, so the
        # pending marks say what holds them: the backend's snapshot → the live row → the tab's status (the Billing
        # row's sub-line) and the reconnecting event (the effort change's line), tests/test_reconnect_waits_for_work.py
        self.assertIn('"relaunchWaits": st.get("relaunchWaits") or "",', inspect.getsource(km._sdk_live_norm))
        src = inspect.getsource(km.build_session)
        self.assertIn('"relaunchWaits": tm.get("relaunchWaits") or "",', src)
        self.assertIn('"waits": (tm0 or {}).get("relaunchWaits") or ""', src)
        self.assertIn('"relaunchWaits": self.relaunch_waits(),', BACKEND_SRC)

    def test_what_applying_now_would_end_reaches_the_status(self):
        # 2026-10-01, Apply now: the row a waiting pick's control offers says what pushing it through ends, and its confirm
        # names it; the backend's snapshot → the live row → the tab's status, beside relaunchWaits
        self.assertIn('"relaunchCuts": st.get("relaunchCuts") or "",', inspect.getsource(km._sdk_live_norm))
        self.assertIn('"relaunchCuts": tm.get("relaunchCuts") or "",', inspect.getsource(km.build_session))
        self.assertIn('"relaunchCuts": self.relaunch_cuts(),', BACKEND_SRC)

    def test_backend_set_effort_arms_the_pending_flag_and_reconnects(self):
        self.assertIn('s._effort_pending = value', BACKEND_SRC)
        self.assertIn('self._update_reg(sid, effort=value, effortPending=True)', BACKEND_SRC)
        self.assertIn('s.request_reconnect(why="effort")', BACKEND_SRC)   # the road that waits for a quiet session (2026-10-01)

    def test_every_effort_pick_is_remembered_ultracode_included(self):
        # the user 2026-08-14: they pick ultracode and expect NEW sessions to follow. The old guard
        # (`if value != "ultracode"`) deliberately never remembered it, so the seed sat on their one
        # historical max pick and every new session opened at max — reading as a downgrade. spawn
        # still hands each new session its own per-session launch shape (--effort xhigh + the
        # ultracode settings key), so the CLI's session-scoping is preserved.
        self.assertNotIn('if value != "ultracode"', BACKEND_SRC)
        self.assertIn("write_sdk_default(self.state_dir, effort=value)", BACKEND_SRC)
        # …and the seed round-trips through the defaults store (behavioral, hermetic state dir)
        import tempfile as _tf
        sb = load_source("romp_sdk_backend_efr", os.path.join(BIN, "romp_sdk_backend.py"))
        td = _tf.mkdtemp()
        sb.write_sdk_default(td, effort="ultracode")
        d = sb.read_sdk_defaults(td)
        self.assertEqual(d.get("effort"), "ultracode")
        self.assertIn("ultracode", sb.EFFORT_LEVELS,
                      "spawn's seed filter (in EFFORT_LEVELS) must accept the remembered ultracode")

    def test_setters_write_the_reg_through_the_locked_rmw(self):
        # the bare read→mutate→write raced the loop threads' own locked RMWs (queue/echo mirrors,
        # liveCtx) and could silently drop the just-picked field: the label looked right, then the
        # value reverted at the next respawn when __init__ re-read the reg (the user 2026-08-14,
        # whose ultracode sessions seemed to downgrade at random). The whole setter family goes
        # through _update_reg now.
        for pin in ('self._update_reg(sid, effort=value, effortPending=True)',
                    'self._update_reg(sid, auth=side, authLogin=login_id, **fields)',   # set_auth: the pick, and for a session no CLI serves its landing (2026-10-07)
                    'self._update_reg(sid, mode=mode)',
                    'self._update_reg(sid, fast=(value == "on"), fastOff=(value == "off"), fastRuleRefused="", liveFast=value)',   # + the explicit Slow and the switch's refusal memory (2026-09-17)
                    'self._update_reg(sid, name=new_name,',   # + the rename ping rides the same locked RMW when owed (2026-08-24/25)
                    'self._update_reg(sid, model=value, modelPending=pending)',   # the live model write
                    'self._update_reg(sid, model=value, liveModel=_alias_label(value), modelPending=False)'):
            self.assertIn(pin, BACKEND_SRC)

    def test_backend_clears_the_pending_flag_when_the_reconnect_lands(self):
        # cleared at the connect that reached a CLI LAUNCHED with the pick (2026-10-01: an attach to a CLI launched before
        # it keeps the dots; the connect's reconcile decides, tests/test_pick_survives_restart.py) — event-based
        self.assertIn('self._reconcile_waiting_picks(launch, launched_here, spawned_at=self._hello_spawned_at)', BACKEND_SRC)
        self.assertIn('fields["effortPending"] = False', BACKEND_SRC)
        # exposed on both the live snapshot and the reg-backed live_sessions (for dormant/all sessions)
        self.assertIn('"effortPending": bool(self._effort_pending),', BACKEND_SRC)
        self.assertIn('"effortPending": bool(reg.get("effortPending")),', BACKEND_SRC)

    def test_backend_clears_pending_on_thread_death_so_the_dots_never_trap(self):
        self.assertIn('if sess._effort_pending:', BACKEND_SRC)


class AWaitingBypassPickReachesThePage(unittest.TestCase):
    """A bypass pick waits for its relaunch as a mark of its own (2026-10-01): the backend's snapshot carries modePending
    beside the mode the running CLI confirmed, and the kernel carries both through the live row and into the tab's status,
    where the mode badge draws its dots (ui/webview/status-controls.test.ts). The live row, the built tab's status and a
    skeleton tab's status (_light_status) run through the REAL Sessions.live(), build_session and _light_status over a fake
    backend and a synthetic transcript; the comment popover's mark through the REAL SdkBackend.session_meta and
    _comment_frame_entry. The session loop's side is tests/test_reconnect_waits_for_work.py."""

    SID_ON = "11111111-2222-3333-4444-0000000ab671"
    SID_OFF = "11111111-2222-3333-4444-0000000ab672"

    def _snap(self, pending):
        return {"state": "waiting", "since": "1790000000", "model": "Opus 5", "effort": "high", "modelPending": False,
                "effortPending": False, "retryCount": 0, "retryInfo": None, "ctx": 10, "mode": "acceptEdits",
                "modePending": pending, "relaunchWaits": "1 background task" if pending else "", "subagents": [], "bgTasks": []}

    def test_the_live_row_carries_the_mark_beside_the_confirmed_mode(self):
        class _Fake:
            def __init__(s, rows): s.rows = rows
            def live_sessions(s): return {k: dict(v) for k, v in s.rows.items()}
        fake = _Fake({self.SID_ON: self._snap(True), self.SID_OFF: self._snap(False)})
        saved = (km._sdk, km._codex)
        km._sdk, km._codex = (lambda: fake), (lambda: None)
        try:
            out = km.Sessions.live()
        finally:
            km._sdk, km._codex = saved
            km._LIVE_LAST_ROWS.pop("sdk", None)
        self.assertEqual((out[self.SID_ON]["mode"], out[self.SID_ON]["modePending"], out[self.SID_ON]["relaunchWaits"]),
                         ("acceptEdits", True, "1 background task"))
        self.assertIs(out[self.SID_OFF]["modePending"], False)

    def _tabs(self):
        """two synthetic tabs, one with a bypass pick waiting: their session rows and the live map a kernel would hold"""
        td = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, td, True)
        rows = []
        for sid in (self.SID_ON, self.SID_OFF):
            leaf = os.path.join(td, sid + ".jsonl")
            with open(leaf, "w") as f:
                f.write(json.dumps({"type": "user", "uuid": "u-" + sid[-3:], "parentUuid": None, "timestamp": "2026-10-01T10:00:00.000Z",
                                    "message": {"role": "user", "content": "tighten the notes-api search"}}) + "\n")
            rows.append({"sid": sid, "name": "web", "path": leaf, "mtime": 1790000000, "anchor": sid})
        live = {}
        for sid, pending in ((self.SID_ON, True), (self.SID_OFF, False)):
            live[sid] = dict(self._snap(pending), since=1790000000, context=None, compactPct=None, color=None, fast="", backend="sdk")
        return rows, live

    def test_the_tabs_status_carries_the_mark_for_the_badge(self):
        rows, live = self._tabs()
        saved = (km._sessions, km._live_map)
        km._sessions, km._live_map = (lambda now, **kw: list(rows)), (lambda: live)
        try:
            on = km.build_session(self.SID_ON, 1790000100, live)["status"]
            off = km.build_session(self.SID_OFF, 1790000100, live)["status"]
        finally:
            km._sessions, km._live_map = saved
        self.assertEqual((on["mode"], on["modePending"], on["relaunchWaits"]), ("acceptEdits", True, "1 background task"),
                         "the confirmed mode, the pick's mark, and what it waits on: the badge's dots and its words")
        self.assertEqual((off["mode"], off["modePending"]), ("acceptEdits", False))

    def test_a_skeleton_tabs_status_carries_the_mark_as_the_built_one_does(self):
        # a tab no page has built yet carries _light_status, read from the live row
        rows, live = self._tabs()
        keys = ("mode", "modePending", "relaunchWaits")
        saved = (km._sessions, km._live_map)
        km._sessions, km._live_map = (lambda now, **kw: list(rows)), (lambda: live)
        try:
            for r in rows:
                built = km.build_session(r["sid"], 1790000100, live)["status"]
                light = km._light_status(r["sid"], r["path"], live[r["sid"]], 1790000100)
                with self.subTest(sid=r["sid"]):
                    self.assertTrue(light.get("provisional"), "the premise: the skeleton's own status, not a build")
                    self.assertEqual({k: light.get(k, "<absent>") for k in keys}, {k: built[k] for k in keys})
        finally:
            km._sessions, km._live_map = saved
        on = km._light_status(self.SID_ON, rows[0]["path"], live[self.SID_ON], 1790000100)
        self.assertEqual((on["mode"], on["modePending"], on["relaunchWaits"]), ("acceptEdits", True, "1 background task"))

    def test_the_comment_popover_reads_the_mark_through_session_meta(self):
        # a comment thread's popover takes its mode from session_meta, never the tab's status: a bypass pick made in the
        # popover wears the dots there too, and its picker's Bypass row has the words (ui/webview/codex-meta-choices.test.ts)
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "session-hosts"), "w") as f:
            f.write("off")
        be = SDB.SdkBackend(d, "/bin/true", lambda *a, **k: None, log=lambda m, **k: None)
        reg = {"sid": self.SID_ON, "name": "web", "cwd": d, "alive": True, "mode": "bypassPermissions"}
        SDB.write_reg(d, self.SID_ON, reg)
        s = SDB.SdkSession(be, dict(reg))
        s.thread = type("T", (), {"is_alive": lambda self: True})()
        be.sessions[self.SID_ON] = s
        s.perm_mode, s._mode_pending = "acceptEdits", "bypassPermissions"
        s.relaunch_waits = lambda: "1 background task"
        meta = be.session_meta(self.SID_ON)
        self.assertEqual((meta["mode"], meta["modePending"], meta["relaunchWaits"]), ("acceptEdits", True, "1 background task"))
        s._mode_pending = ""
        self.assertIs(be.session_meta(self.SID_ON)["modePending"], False)

    def test_the_comments_frame_carries_the_mark_to_the_popover(self):
        class _Be:
            def session_state(self, sid): return ""
            def session_meta(self, sid):
                return {"mode": "acceptEdits", "fast": "", "modePending": True, "relaunchWaits": "1 background task"}
        th = {"sid": self.SID_ON, "tid": "t-ab671", "status": "open", "createdT": 1790000000, "anchorUuid": "u-671",
              "exact": "tighten the notes-api search"}
        row = km._comment_frame_entry(th, _Be(), 1790000100)
        self.assertEqual((row["mode"], row["modePending"], row["relaunchWaits"]), ("acceptEdits", True, "1 background task"))


if __name__ == "__main__":
    unittest.main()
