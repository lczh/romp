"""Automatic remote update (the user 2026-07-24): romp pushes this machine's build to an attached remote
WITHOUT the mid-screen prompt — but only when the push is a STRAIGHT FAST-FORWARD (the remote's commit is an
ancestor of ours, so the push can destroy nothing). The setting is server-side and fleet-wide on purpose: the
push must fire once per advance from the kernel's supervisor, never once per open browser tab.

SYNTHETIC fixtures only — invented hosts and placeholder shas, no real machine names.
"""
import json
import os
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
km = load_source("romp_kernel_autoupd", os.path.join(BIN, "romp-kernel"))

LOCAL = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
REMOTE = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


class FastForwardGate(unittest.TestCase):
    """_is_fast_forward decides whether the AUTOMATIC push may fire. It must say no to anything it cannot
    prove is safe — a divergence, or a build this repo has never seen."""

    def _patched(self, behind, ahead, ood=True):
        saved = (km._remote_out_of_date, km._behind_info)
        km._remote_out_of_date = lambda r, head=None: ood
        km._behind_info = lambda sha, head=None: {"behind": behind, "ahead": ahead, "date": ""}
        return saved

    def _restore(self, saved):
        km._remote_out_of_date, km._behind_info = saved

    def _ff(self, behind, ahead, ood=True):
        saved = self._patched(behind, ahead, ood)
        try:
            return km._is_fast_forward({"host": "TESTHOST", "kernel_sha": REMOTE})
        finally:
            self._restore(saved)

    def test_behind_only_is_a_fast_forward(self):
        self.assertTrue(self._ff(behind=3, ahead=0), "the remote is a pure ancestor — the push only adds")

    def test_an_up_to_date_remote_is_not_pushed(self):
        self.assertFalse(self._ff(behind=0, ahead=0, ood=False), "nothing to push")
        self.assertFalse(self._ff(behind=0, ahead=0), "no commits to deliver → not a fast-forward")

    def test_a_remote_with_its_own_commits_is_left_alone(self):
        self.assertFalse(self._ff(behind=0, ahead=2), "ahead: a push would clobber its work")
        self.assertFalse(self._ff(behind=3, ahead=2), "diverged: never automatic")

    def test_an_unknown_build_is_left_alone(self):
        # _behind_info returns None/None when the remote's sha isn't in this repo at all. None is NOT zero:
        # an unprovable relationship is exactly what must not be auto-pushed.
        self.assertFalse(self._ff(behind=None, ahead=None),
                         "an unrecognised remote build is never auto-pushed")
        self.assertFalse(self._ff(behind=3, ahead=None), "a half-known relationship is still unproven")


class SettingIsServerSide(unittest.TestCase):
    """The toggle lives in the kernel, not the browser: one owner, so N open dashboards can't each fire
    their own push (the per-view fan-out that caused the retry storm)."""

    def setUp(self):
        self._prev = km._auto_update_remotes_on()

    def tearDown(self):
        km._set_auto_update_remotes(self._prev)

    def test_it_round_trips_and_defaults_off(self):
        km._set_auto_update_remotes(False)
        self.assertFalse(km._auto_update_remotes_on(), "off by default — pushing code unasked is opt-in")
        km._set_auto_update_remotes(True)
        self.assertTrue(km._auto_update_remotes_on())
        km._set_auto_update_remotes(False)
        self.assertFalse(km._auto_update_remotes_on())

    def test_an_unreadable_setting_reads_as_OFF(self):
        p = km.jd.STATE / "auto-update-remotes.json"
        p.write_text("{not json")
        try:
            self.assertFalse(km._auto_update_remotes_on(), "a corrupt setting must not start pushing code")
        finally:
            km._set_auto_update_remotes(self._prev)


class AutoPushFiring(unittest.TestCase):
    """_maybe_auto_push is the supervisor's hook: it fires at most one push per advance, per host."""

    def setUp(self):
        self._prev = km._auto_update_remotes_on()
        km._set_auto_update_remotes(True)
        km._auto_push.clear()
        km._auto_push_tried.clear()
        self._saved = (km._remote_out_of_date, km._behind_info, km._local_head)
        km._remote_out_of_date = lambda r, head=None: True
        km._behind_info = lambda sha, head=None: {"behind": 2, "ahead": 0, "date": ""}
        km._local_head = lambda short=False: (LOCAL[:8] if short else LOCAL)
        self.calls = []

    def tearDown(self):
        km._remote_out_of_date, km._behind_info, km._local_head = self._saved
        km._set_auto_update_remotes(self._prev)
        km._auto_push.clear()
        km._auto_push_tried.clear()

    def _row(self, host="TESTHOST"):
        return {"host": host, "kernel_sha": REMOTE}

    def _run(self, row):
        """Drive _maybe_auto_push with the worker replaced, so no ssh runs — we assert on the DECISION."""
        saved = km.threading.Thread
        calls = self.calls

        class _T:
            def __init__(self, target=None, args=(), daemon=None):
                self._a = args

            def start(self):
                calls.append(self._a[0])
        km.threading.Thread = _T
        try:
            km._maybe_auto_push(row)
        finally:
            km.threading.Thread = saved

    def test_a_fast_forward_advance_fires_one_push(self):
        self._run(self._row())
        self.assertEqual(self.calls, ["TESTHOST"], "the advance is pushed")

    def test_the_same_advance_is_not_pushed_twice(self):
        # the supervisor calls this every pass; without the episode gate a failing push would re-fire
        # forever, hammering another machine's sshd
        self._run(self._row())
        self._run(self._row())
        self._run(self._row())
        self.assertEqual(self.calls, ["TESTHOST"], "one attempt per (remote sha, local HEAD)")

    def test_a_new_advance_fires_again(self):
        self._run(self._row())
        km._local_head = lambda short=False: ("cccccccc" if short else "c" * 40)   # local moved on
        self._run(self._row())
        self.assertEqual(self.calls, ["TESTHOST", "TESTHOST"], "a genuinely new advance retries")

    def test_nothing_fires_when_the_setting_is_off(self):
        km._set_auto_update_remotes(False)
        self._run(self._row())
        self.assertEqual(self.calls, [], "off means off")

    def test_a_diverged_remote_never_fires(self):
        km._behind_info = lambda sha, head=None: {"behind": 1, "ahead": 1, "date": ""}
        self._run(self._row())
        self.assertEqual(self.calls, [], "a push that could clobber is never automatic")

    def test_an_up_to_date_host_clears_a_settled_phase(self):
        # the EVENT that ends a push is the remote reporting our sha — never a timer
        km._set_auto_push("TESTHOST", "waiting", "pushed; waiting for it to restart")
        km._remote_out_of_date = lambda r, head=None: False
        self._run(self._row())
        self.assertIsNone(km._auto_push_state("TESTHOST"), "the restarted remote matching us ends the phase")

    def test_a_push_already_in_flight_is_not_duplicated(self):
        km._set_auto_push("TESTHOST", "pushing", "pushing this machine's build over SSH")
        self._run(self._row())
        self.assertEqual(self.calls, [], "one push per host at a time")


class WhatAPushReports(unittest.TestCase):
    """The phase and the Log notice of an automatic push follow what the push did (the user 2026-11-12, who wanted pages
    to stay responsive after a deploy: a push that changes no kernel code leaves the kernel there running, converged in
    place). A restart is announced only when one is coming, which _update_remote records on the row as restartExpected;
    otherwise the push's own words are the report, and nothing claims the machine is restarting. _update_remote is
    stubbed: these are _auto_push_remote's words and its once-per-advance key."""
    INPLACE = "synced to aaaaaaaa; no kernel code changed, so the kernel there converged in place (no restart)"

    def setUp(self):
        self._saved = (km._update_remote, km._fresh_local_head, km._local_head, km._kernel_ver, km._behind_info,
                       km._auto_update_remotes_on(), km.threading.Thread)
        km._auto_push.clear()
        km._auto_push_tried.clear()
        km._fresh_local_head = lambda: LOCAL
        km._local_head = lambda short=False: (LOCAL[:8] if short else LOCAL)
        km._kernel_ver = lambda: "v9.9.9"
        km._remotes["TESTHOST"] = {"host": "TESTHOST", "status": "up", "kernel_sha": REMOTE[:7]}
        self.pushed = []

    def tearDown(self):
        (km._update_remote, km._fresh_local_head, km._local_head, km._kernel_ver, km._behind_info) = self._saved[:5]
        km._set_auto_update_remotes(self._saved[5])
        km.threading.Thread = self._saved[6]
        km._remotes.pop("TESTHOST", None)
        km._auto_push.clear()
        km._auto_push_tried.clear()

    def _answers(self, ok, detail, restarts):
        """What the next push does: its (ok, detail), and whether it leaves a restart expected on the row."""
        def push(host, head=None, restart="if-code-changed"):
            self.pushed.append((host, head))
            if restarts:
                km._remotes[host]["restartExpected"] = {"sha": head, "t": 1.0, "quiet": False}
            return ok, detail
        km._update_remote = push

    def _notice(self):
        return km._sync_notice_rows(limit=1)[0]

    def test_a_kernel_that_converged_in_place_is_not_said_to_be_restarting(self):
        self._answers(True, self.INPLACE, restarts=False)
        self.assertTrue(km._auto_push_remote("TESTHOST"))
        self.assertEqual(self.pushed, [("TESTHOST", LOCAL)])
        st = km._auto_push_state("TESTHOST")
        self.assertEqual((st["phase"], st["detail"]), ("waiting", self.INPLACE), "the row shows the push's own words")
        n = self._notice()
        self.assertEqual((n["text"], n["ok"]), ("pushed this machine's build (v9.9.9) to TESTHOST: " + self.INPLACE, True))
        self.assertNotIn("restarting", n["text"])

    def test_a_push_that_restarts_the_kernel_says_so_as_before(self):
        self._answers(True, "synced to aaaaaaaa + restarting now (through its manager)", restarts=True)
        self.assertTrue(km._auto_push_remote("TESTHOST"))
        self.assertEqual(km._auto_push_state("TESTHOST")["detail"], "pushed; waiting for it to restart")
        self.assertEqual(self._notice()["text"], "pushed this machine's build (v9.9.9) to TESTHOST; it is restarting into it")

    def test_a_converge_that_failed_and_fell_back_to_the_restart_reads_as_a_restart(self):
        # _update_remote answers ok with the fallback in its detail and a restart expected: the phase waits for it
        self._answers(True, "synced to aaaaaaaa + restarting now (through its manager); it was asked to converge in "
                            "place and could not (its kernel answered HTTP 404), so it is restarting instead", restarts=True)
        self.assertTrue(km._auto_push_remote("TESTHOST"))
        self.assertEqual(km._auto_push_state("TESTHOST")["detail"], "pushed; waiting for it to restart")

    def test_a_failed_push_stays_a_visible_failure(self):
        self._answers(False, "git push to TESTHOST failed: no route", restarts=False)
        self.assertFalse(km._auto_push_remote("TESTHOST"))
        st = km._auto_push_state("TESTHOST")
        self.assertEqual((st["phase"], st["detail"]), ("failed", "git push to TESTHOST failed: no route"))
        self.assertFalse(self._notice()["ok"])

    def test_one_advance_is_one_push_to_a_kernel_serving_a_checkout_newer_than_its_boot(self):
        # A kernel that converged in place reports a checkout past the commit it booted from. The supervisor's hook
        # keys an attempt on the checkout (_drift_sha); the push used to record it under the booted commit, so the
        # two keys never agreed for such a machine and every pass pushed again.
        km._remotes["TESTHOST"].update(kernel_sha="ccccccc", checkout_sha=REMOTE[:8])
        km._behind_info = lambda sha, head=None: {"behind": 2, "ahead": 0, "date": ""}
        km._set_auto_update_remotes(True)

        class _Now:                                                    # the worker, run inline
            def __init__(self, target=None, args=(), daemon=None):
                self._t, self._a = target, args

            def start(self):
                self._t(*self._a)
        km.threading.Thread = _Now
        self._answers(False, "git push to TESTHOST failed: no route", restarts=False)
        km._maybe_auto_push(dict(km._remotes["TESTHOST"]))
        km._maybe_auto_push(dict(km._remotes["TESTHOST"]))
        km._maybe_auto_push(dict(km._remotes["TESTHOST"]))
        self.assertEqual(len(self.pushed), 1, "three passes over one advance: one attempt")


class PublishedToTheClient(unittest.TestCase):
    """The popover has to be able to SAY what is happening — so the row carries the fast-forward verdict and
    any live phase, and the payload carries the setting."""

    def tearDown(self):
        km._auto_push.clear()

    def test_the_row_publishes_the_fast_forward_verdict_and_live_phase(self):
        saved = (km._remote_out_of_date, km._behind_info)
        km._remote_out_of_date = lambda r, head=None: True
        km._behind_info = lambda sha, head=None: {"behind": 2, "ahead": 0, "date": "2026-07-24"}
        km._set_auto_push("TESTHOST", "pushing", "pushing this machine's build over SSH")
        try:
            pub = km._remote_public({"host": "TESTHOST", "kernel_port": 1, "local_port": 2,
                                     "kernel_sha": REMOTE, "status": "up"})
        finally:
            km._remote_out_of_date, km._behind_info = saved
        self.assertTrue(pub["fastForward"], "the row says a push here only adds commits")
        self.assertEqual(pub["autoPush"]["phase"], "pushing")
        self.assertIn("SSH", pub["autoPush"]["detail"], "the phase carries a human-readable detail")

    def test_a_host_with_no_auto_push_publishes_none(self):
        pub = km._remote_public({"host": "OTHERHOST", "kernel_port": 1, "local_port": 2, "status": "up"})
        self.assertIsNone(pub["autoPush"], "no phase → nothing to show")


class BannerAndPopoverWiring(unittest.TestCase):
    """Source pins on the web popover + drift banner (the inline JS in kernel.py)."""

    def test_the_mid_screen_banner_stands_down_when_auto_update_is_on(self):
        # the whole point of the setting: no modal over your screen on every advance
        self.assertIn("if(d&&d.autoUpdate){box.classList.remove('show');phase='idle';return;}", km._RDRIFT_JS)

    def test_the_popover_offers_the_checkbox_and_writes_it_to_the_kernel(self):
        self.assertIn("Automatically update", km._landing())
        self.assertIn("id=rnet-auto", km._landing())
        self.assertIn("/tunnels/autoupdate", km._LANDING_REMOTES_JS)
        self.assertIn("if(autoCb&&!autoCb.disabled)autoCb.checked=_auto;", km._LANDING_REMOTES_JS,
                      "the box mirrors the kernel, and never clobbers a write in flight")

    def test_an_in_flight_push_animates_the_icon_and_hides_the_duplicate_button(self):
        self.assertIn("paintIcon(ts.some(function(t){return t.status==='up';}),busy||!!pushing.length,fleetNodes(ts));",
                      km._LANDING_REMOTES_JS, "the rail icon marches while romp pushes in the background")
        self.assertIn("t.fastForward&&!apx", km._LANDING_REMOTES_JS,
                      "no manual Push button for work already in flight — nor for a push that cannot succeed")
        self.assertIn("auto-update: ", km._LANDING_REMOTES_JS, "the row carries the live phase")

    def test_the_icon_tooltip_reports_the_live_phase(self):
        self.assertIn("auto-update: ", km._LANDING_REMOTES_JS)
        self.assertIn("t.autoPush?(", km._LANDING_REMOTES_JS.replace(" ", ""),
                      "the hover text names the phase without opening the panel")


if __name__ == "__main__":
    unittest.main()
