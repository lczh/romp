#!/usr/bin/env python3
"""Tests for bin/romp-version (`romp --version`) — the report that shows working tree vs running kernel
vs served-vs-built bundles. Network/git are stubbed so the formatting + the stale-bundle flag are pinned
deterministically."""
import os
from romp_load import load_source
import tempfile

BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
ver = load_source("romp_version", os.path.join(BIN, "romp-version"))


def test_report_flags_stale_served_bundle(monkeypatch=None):
    ver._git_sha = lambda: "abc1234-dirty"
    ver._disk_bundles = lambda: {"feed.js": 200, "render.js": 100}
    # kernel reports it is SERVING an older feed.js (100) than what's on disk (200) → must flag it
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {"feed.js": 100, "render.js": 100}})
    out = ver.report()
    assert "abc1234-dirty" in out
    assert "pid=9" in out
    assert "feed.js" in out and "romp refresh" in out    # stale flag present on the newer-on-disk bundle


def test_report_shows_the_live_fold_rate():
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 129, "fold": 2078, "serve": 446,
                                                       "bypass": 0, "fallback": 0,
                                                       "g:boundary": 5, "g:ts": 1}})
    out = ver.report()
    assert "fold 94%" in out                      # 2078 / (2078+129)
    assert "2078 fold / 129 full / 446 served" in out
    assert "boundary 5" in out and "ts 1" in out  # per-gate demotes, named
    assert "fallbacks" not in out                 # zero fallbacks stay quiet


def test_report_lists_a_prompt_id_demotion_once_with_its_arm_as_a_breakdown():
    # the promptid gate counts each demotion under g:promptid and again under the part that filed it (g:promptid:wrapper,
    # g:promptid:boundary, g:promptid:passedDemote); listed flat, every such demotion read twice under "demotes". The seed's
    # count (g:promptid:boundarySeed) is a share of the boundary arm's, so it is listed inside that arm and the parts add up
    # to the reason's count (review round two: listed beside the arms they added to 7 of 5). The folds the narrowed arm saved
    # (fold:attachedPass) are no demotion and stay out of that list (2026-10-03)
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 10, "fold": 90, "serve": 5,
                                                       "bypass": 0, "fallback": 0,
                                                       "g:descent": 1, "g:promptid": 5, "g:promptid:wrapper": 1,
                                                       "g:promptid:boundary": 3, "g:promptid:boundarySeed": 2,
                                                       "g:promptid:passedDemote": 1, "fold:attachedPass": 7}})
    out = ver.report()
    line = next(ln for ln in out.splitlines() if ln.startswith("parse"))
    demotes = line.split("  demotes: ", 1)[1].split("  ")[0]
    assert demotes == "descent 1, promptid 5 (boundary 3 (seed 2), passedDemote 1, wrapper 1)", line   # once each; parts add up
    assert "attachedPass=7" in line, line                                      # the saved folds shown beside, not as a demote
    assert "attachedPass" not in demotes, line


def test_report_lists_the_boundary_arms_reasons_inside_it():
    # the boundary arm counts each demotion again under the reason the entry could not vouch for the id
    # (g:promptid:boundary:<reason>, 2026-10-05): listed inside the arm, so the parts in parentheses still add up to the
    # reason's count, with boundarySeed, the older name of the nested seed, listed once, and seedEpisode (a restored
    # entry's guard, counted apart from the shapes) listed as a reason of its own
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 10, "fold": 90, "serve": 5,
                                                       "bypass": 0, "fallback": 0,
                                                       "g:promptid": 7, "g:promptid:wrapper": 1,
                                                       "g:promptid:boundary": 6, "g:promptid:boundary:offpath": 3,
                                                       "g:promptid:boundary:adopted": 1, "g:promptid:boundary:seed": 1,
                                                       "g:promptid:boundary:seedEpisode": 1,
                                                       "g:promptid:boundarySeed": 1, "fold:attachedPass": 4,
                                                       "fold:attachedPass:offpath": 2}})
    out = ver.report()
    line = next(ln for ln in out.splitlines() if ln.startswith("parse"))
    demotes = line.split("  demotes: ", 1)[1].split("  ")[0]
    assert demotes == "promptid 7 (boundary 6 (adopted 1, offpath 3, seed 1, seedEpisode 1), wrapper 1)", line
    assert "attachedPass=4" in line, line


def test_report_shows_an_all_fallback_kernel():
    # _asm_full counts "full" as its LAST act — a kernel whose every parse errors into the
    # fallback has fold+full == 0 but the loudest possible story; it must render (T210 review)
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 0, "fold": 0, "serve": 0,
                                                       "bypass": 0, "fallback": 3}})
    out = ver.report()
    assert "fallbacks=3" in out


def test_report_shows_ts_repair_when_present():
    # the stderr repair note points at "parse stats" — the stats line must actually show it
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 4, "fold": 96, "serve": 10,
                                                       "bypass": 0, "fallback": 0,
                                                       "ts-repair": 2}})
    out = ver.report()
    assert "ts-repair=2" in out


def test_report_stays_quiet_before_any_parse():
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("version", {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 5,
                                             "dist_ver": 100, "url": "http://127.0.0.1:29855",
                                             "bundles": {},
                                             "parse": {"full": 0, "fold": 0, "serve": 0,
                                                       "bypass": 0, "fallback": 0}})
    out = ver.report()
    assert "parse" not in out                     # a 0/0 rate is noise, not a report


def test_report_handles_down_kernel():
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {"feed.js": 200}
    ver._probe_kernel = lambda: ("down", None)
    out = ver.report()
    assert "not reachable" in out


def test_report_handles_predating_kernel():
    ver._git_sha = lambda: "abc1234"
    ver._disk_bundles = lambda: {}
    ver._probe_kernel = lambda: ("stale", "http://127.0.0.1:29855")
    out = ver.report()
    assert "predates /version" in out


def _kernel_line(**fields):
    ver._git_sha = lambda: "1111111"
    ver._disk_bundles = lambda: {}
    k = {"kernel_sha": "abc1234", "pid": 9, "uptime_s": 65, "dist_ver": 100, "url": "http://127.0.0.1:29855",
         "bundles": {}}
    k.update(fields)
    ver._probe_kernel = lambda: ("version", k)
    return next(ln for ln in ver.report().splitlines() if ln.startswith("kernel"))


def test_report_names_the_commit_a_converge_in_place_serves():
    # the review of the push converge, 2026-11-12: a push or pull that changes no kernel code is served without a
    # restart, kernel_sha stays the booted commit, and the report printed only that one, so the kernel looked behind
    # its own checkout and invited a `romp refresh` nothing needed
    line = _kernel_line(converged_sha="11111111", restart_pending=False)
    assert line.startswith("kernel   sha=11111111 (booted from abc1234, converged in place; no restart pending) pid=9"), line
    assert "romp refresh" not in line, line


def test_report_says_when_a_restart_is_pending():
    line = _kernel_line(converged_sha="abc1234", restart_pending=True)
    assert line.startswith("kernel   sha=abc1234 (⚠ restart pending — `romp refresh`) pid=9"), line


def test_report_keeps_the_plain_line_for_a_kernel_on_its_boot_or_one_older_than_the_field():
    assert _kernel_line(converged_sha="abc1234", restart_pending=False).startswith("kernel   sha=abc1234 pid=9")
    assert _kernel_line().startswith("kernel   sha=abc1234 pid=9"), "a kernel without converged_sha: its booted commit"


if __name__ == "__main__":
    test_report_flags_stale_served_bundle()
    test_report_handles_down_kernel()
    test_report_handles_predating_kernel()
    print("ok")
