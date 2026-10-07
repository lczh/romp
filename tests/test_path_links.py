#!/usr/bin/env python3
"""Chat file links are filesystem-VERIFIED, and shortened mentions are FIXED.

The client linkifies path-shaped tokens by shape alone (path-links.ts CLICKABLE_PATH_RE), so a bare
`render.js` in a reply became a blue link that 404'd on click — the token resolved against the
session's cwd, where no such file lives. The kernel is the machine with the filesystem, so at
message-build time it resolves every shape-matched token in three tiers (exact stat; unique
"/"+token suffix in the repo list; unique basename) and ships {token: open target} as pathLinks on
the chat event. Zero or several matches → absent from the map → the client leaves the token prose:
a silently-wrong link is worse than no link. The per-message cache is deliberately asymmetric —
hits latch for the message's life (a link never flaps away), misses retry every build, because
agents mention `report.md` moments before the Write that creates it.

The repo list is `git ls-files -co --exclude-standard` in the SESSION's cwd, so untracked files
count and gitignored ones never do. Client-side gates are pinned in chat-path-links.test.ts; the
tokenizer parity between the Python port and CLICKABLE_PATH_RE is pinned on both sides over
tests/fixtures/path_token_parity.json. Synthetic fixtures only.
"""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
km = load_source("romp_kernel_pathlinks", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"


def _clear_listings():
    for c in (km._REPO_SERVED, km._repo_index_cache, km._repo_part_cache):
        c.clear()


class _Repo(unittest.TestCase):
    """A synthetic git repo as the session's cwd: tracked, untracked and ignored files."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.cwd = Path(self.td.name)
        subprocess.run(["git", "init", "-q"], cwd=self.cwd, check=True)
        self.write("tests/test_x.py", "def test_ok(): pass\n")
        self.write("ui/render.js", "// unique basename\n")
        self.write("kernel/sub/deep.py", "# tier-2 target\n")
        self.write("a/dup.py", "# ambiguous basename\n")
        self.write("b/dup.py", "# ambiguous basename\n")
        self.write("a/x/same.py", "# ambiguous suffix\n")
        self.write("b/x/same.py", "# ambiguous suffix\n")
        self.write(".gitignore", "ignored/\n")
        self.write("ignored/secret.log", "never listed\n")
        # half the repo TRACKED, half untracked — ls-files -co must surface both
        subprocess.run(["git", "add", "tests", "ui", ".gitignore"], cwd=self.cwd, check=True)
        self._saved_cwd_of = km._cwd_of
        km._cwd_of = lambda sid: str(self.cwd) if sid == SID else ""
        km._PATH_LINK_CACHE.clear()
        # the listing's refresh runs INLINE here, so each resolve reads a listing taken after the files the test just
        # wrote: the tiers' answers are this module's subject; the off-thread refresh has its own class below
        self._saved_spawn = km._repo_index_spawn
        km._repo_index_spawn = km._repo_index_refresh
        _clear_listings()
        self._n = 0

    def tearDown(self):
        km._cwd_of = self._saved_cwd_of
        km._repo_index_spawn = self._saved_spawn
        km._PATH_LINK_CACHE.clear()
        _clear_listings()
        self.td.cleanup()

    def write(self, rel, text):
        p = self.cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return str(p)

    def _uuid(self):
        self._n += 1
        return "msg-%d" % self._n

    def links(self, md, uuid=None):
        return km._path_links(md, SID, uuid or self._uuid(), {})


class ThreeTiers(_Repo):
    def test_tier1_an_exact_path_links_as_written(self):
        self.assertEqual(self.links("see tests/test_x.py for the check"),
                         {"tests/test_x.py": "tests/test_x.py"})

    def test_tier2_a_unique_suffix_is_fixed_to_the_real_file(self):
        self.assertEqual(self.links("edit sub/deep.py next"),
                         {"sub/deep.py": "kernel/sub/deep.py"})

    def test_tier3_a_unique_basename_is_fixed_even_untracked(self):
        # ui/render.js is tracked; an UNTRACKED unique file must fix too (agents create files constantly)
        self.assertEqual(self.links("the bug is in render.js"), {"render.js": "ui/render.js"})
        self.write("docs/fresh.md", "brand new, never git-added\n")
        self.assertEqual(self.links("wrote fresh.md"), {"fresh.md": "docs/fresh.md"})

    def test_ambiguity_is_never_guessed_at(self):
        # two dup.py, two x/same.py — a silently-wrong link is worse than no link
        self.assertEqual(self.links("check dup.py and x/same.py"), {})

    def test_a_file_that_exists_nowhere_stays_out_of_the_map(self):
        self.assertEqual(self.links("will write missing.md soon"), {})

    def test_prose_shaped_tokens_resolve_to_nothing(self):
        self.assertEqual(self.links("and/or 24/7, np.array"), {})

    def test_an_empty_map_still_ships_but_no_tokens_ships_nothing(self):
        # {} tells the client a verdict WAS rendered (gate everything off); None means no candidates at
        # all, so the event carries no key — that absence is also the old-kernel fallback signal.
        self.assertEqual(self.links("mentions missing.md only"), {})
        self.assertIsNone(self.links("no path shaped tokens here"))

    def test_a_tracked_but_deleted_file_never_links(self):
        (self.cwd / "ui" / "render.js").unlink()   # still in the index (ls-files -c) — but not on disk
        self.assertEqual(self.links("see render.js"), {})

    def test_gitignored_files_are_invisible_to_tiers_2_and_3(self):
        self.assertEqual(self.links("check secret.log and ignored/secret.log"),
                         {"ignored/secret.log": "ignored/secret.log"},
                         "the exact path still stats (tier 1); the ignored file never FIXES a bare name")

    def test_file_uris_are_not_the_kernels_to_gate(self):
        self.assertIsNone(self.links("file:///tmp/somewhere.pdf"),
                          "explicit absolute — the client keeps today's verbatim link, ungated")


class AsymmetricCache(_Repo):
    def test_a_miss_that_later_resolves_appears(self):
        u = self._uuid()
        self.assertEqual(self.links("writing report.md now", u), {},
                         "mentioned BEFORE the Write that creates it")
        self.write("report.md", "# now it exists\n")
        self.assertEqual(self.links("writing report.md now", u), {"report.md": "report.md"},
                         "the creating Write triggers the rebuild that must turn the mention into a link")

    def test_a_miss_that_later_resolves_by_repo_list_appears(self):
        u = self._uuid()
        self.assertEqual(self.links("adding late.js", u), {})
        self.write("ui/late.js", "// created after the mention\n")
        self.assertEqual(self.links("adding late.js", u), {"late.js": "ui/late.js"})

    def test_a_hit_latches_for_the_messages_life(self):
        u = self._uuid()
        self.assertEqual(self.links("see tests/test_x.py", u), {"tests/test_x.py": "tests/test_x.py"})
        (self.cwd / "tests" / "test_x.py").unlink()
        self.assertEqual(self.links("see tests/test_x.py", u), {"tests/test_x.py": "tests/test_x.py"},
                         "a link never flaps away without user action; a NEW message re-checks")
        self.assertEqual(self.links("see tests/test_x.py"), {}, "…and the new message sees the deletion")

    def test_no_uuid_short_circuits(self):
        self.assertIsNone(km._path_links("see tests/test_x.py", SID, None, {}))


class RepoListEdges(_Repo):
    def test_a_non_git_cwd_keeps_tier1_and_stands_down_tiers_2_and_3(self):
        with tempfile.TemporaryDirectory() as plain:
            (Path(plain) / "real.md").write_text("x\n")
            (Path(plain) / "sub").mkdir()
            (Path(plain) / "sub" / "only.md").write_text("x\n")
            km._cwd_of = lambda sid: plain if sid == SID else ""
            self.assertEqual(self.links("see real.md"), {"real.md": "real.md"}, "tier 1 needs no git")
            self.assertEqual(self.links("see only.md"), {},
                             "no repo list → no basename fixing, silently")

    def test_a_runaway_listing_skips_tiers_2_and_3(self):
        saved = km._REPO_LIST_MAX
        try:
            km._REPO_LIST_MAX = 1
            self.assertEqual(self.links("see render.js but tests/test_x.py works"),
                             {"tests/test_x.py": "tests/test_x.py"})
        finally:
            km._REPO_LIST_MAX = saved

    def test_the_repo_list_honors_ignore_and_dedupes(self):
        idx = km._repo_file_index(str(self.cwd))
        self.assertNotIn("secret.log", idx)
        self.assertEqual(sorted(idx["dup.py"]), ["a/dup.py", "b/dup.py"])
        self.assertEqual(idx["render.js"], ["ui/render.js"])

    def test_a_stand_down_is_said_once_per_cwd_on_stderr(self):
        # T364: the index returned None silently on any git failure or a runaway listing, so a bare filename that stayed
        # plain text left no trace in the kernel log; one stderr line per cwd names the reason
        import contextlib
        import io
        km._REPO_INDEX_STOOD_DOWN.clear()
        saved = km._REPO_LIST_MAX
        err = io.StringIO()
        try:
            km._REPO_LIST_MAX = 1
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._repo_file_index(str(self.cwd)))
                self.assertIsNone(km._repo_file_index(str(self.cwd)))
        finally:
            km._REPO_LIST_MAX = saved
        lines = [l for l in err.getvalue().splitlines() if "stood down" in l]
        self.assertEqual(len(lines), 1, "said once per cwd and reason: %r" % err.getvalue())
        self.assertIn("past the 1-file ceiling", lines[0]); self.assertIn("bare filenames in this session's messages stay plain text", lines[0])
        # a CHANGED reason for the same cwd is a new line (the ceiling moved: the same cwd stands down for another reason)
        try:
            km._REPO_LIST_MAX = 2
            with contextlib.redirect_stderr(err):
                self.assertIsNone(km._repo_file_index(str(self.cwd)))
        finally:
            km._REPO_LIST_MAX = saved
        lines = [l for l in err.getvalue().splitlines() if "stood down" in l]
        self.assertEqual(len(lines), 2, "a changed reason is said: %r" % err.getvalue()); self.assertIn("past the 2-file ceiling", lines[1])
        with tempfile.TemporaryDirectory() as plain, contextlib.redirect_stderr(err):
            self.assertIsNone(km._repo_file_index(plain))
        self.assertIn("git ls-files exited", err.getvalue(), "a cwd that is no repo says git's own exit: %r" % err.getvalue())

    def test_the_index_is_built_at_most_once_per_build_pass(self):
        calls = []
        saved = km._repo_index_served
        try:
            km._repo_index_served = lambda cwd: calls.append(cwd) or saved(cwd)
            memo = {}
            km._path_links("see render.js", SID, self._uuid(), memo)
            km._path_links("see sub/deep.py", SID, self._uuid(), memo)
            self.assertEqual(len(calls), 1, "one listing serves every message in the pass")
        finally:
            km._repo_index_served = saved


class _ListingCalls:
    """subprocess.run seen through a tally of the kernel's `git ls-files` halves ("c" tracked, "o" untracked), optionally
    parking every listing on `gate` (bounded, so a kernel that lists on the asking thread fails the timing assertion
    instead of hanging the run). Everything else passes straight through."""

    def __init__(self, gate=None):
        self.gate, self.calls, self._real = gate, [], km.subprocess.run

    def __call__(self, argv, *a, **kw):
        if isinstance(argv, (list, tuple)) and list(argv[:2]) == ["git", "ls-files"] and ("-c" in argv or "-o" in argv or "-co" in argv):
            self.calls.append("o" if ("-o" in argv or "-co" in argv) else "c")
            if self.gate is not None:
                self.gate.wait(6)
        return self._real(argv, *a, **kw)


class ListingOffTheAskingThread(_Repo):
    """The repo listing is never taken on the thread that asks for it (2026-10-07). On a 25k-file research repo on a
    network filesystem the untracked walk took ten seconds, and the git index moved every two to four seconds (the
    sessions working there refresh it with every `git status`), so a listing keyed on the raw mtimes never served
    from its cache: every tab of that cwd listed on the pusher at every signature check, one cycle took 14.5 minutes,
    a connect push four, and a history page asked behind it waited as long. The hot paths read the last completed
    listing (_repo_index_served); a moved key starts one refresh per cwd on a thread of its own, whose landing takes a
    new generation and wakes the pusher; and each half of the listing is re-taken only on its own events."""

    def _wait_served(self, gen_before, deadline=10.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < deadline:          # loop-ok: bounded by the deadline
            ent = km._REPO_SERVED.get(str(self.cwd))
            if ent is not None and ent[2] != gen_before and str(self.cwd) not in km._REPO_REFRESHING:
                return ent
            time.sleep(0.01)
        self.fail("the refresh never landed")

    def test_a_slow_listing_never_holds_the_asking_thread(self):
        km._repo_index_spawn = self._saved_spawn                      # the production spawn: a thread of its own
        gate = threading.Event()
        calls = _ListingCalls(gate)
        u = self._uuid()
        try:
            with mock.patch.object(km.subprocess, "run", calls):
                t0 = time.monotonic()
                first = self.links("the bug is in render.js", u)
                pre = km._chat_pl_precheck(SID, [(u, "the bug is in render.js")])   # the pusher's signature check, same tab
                for _ in range(5):                                    # five more tabs of the same cwd asking meanwhile
                    km._repo_index_served(str(self.cwd))
                took = time.monotonic() - t0
                self.assertLess(took, 2.0, "the asking thread waited on git for %.1f s" % took)
                self.assertEqual(first, {}, "no listing yet: tiers 2/3 stand down for this build, never a wait")
                self.assertEqual(pre[1], ("listing", 0), "the pre-check names the listing in hand: none yet")
                t1 = time.monotonic()
                while not calls.calls and time.monotonic() - t1 < 5:   # loop-ok: the refresh thread reaching git, bounded
                    time.sleep(0.01)
                self.assertEqual(calls.calls, ["c"], "one listing per cwd however many tabs ask, parked on the gate")
                km._pusher_wake.clear()
                gate.set()
                ent = self._wait_served(0)
            self.assertIsNotNone(ent[1], "the refresh published the listing")
            st = km._repo_index_stats()
            self.assertGreaterEqual(st["refreshes"], 1, st)
            self.assertEqual(st["inflight"], 0, "/perf repoIndex shows the refresh done: %r" % st)
            self.assertTrue(km._pusher_wake.is_set(), "a listing that answers differently wakes the pusher")
            self.assertEqual(self.links("the bug is in render.js", u), {"render.js": "ui/render.js"},
                             "the rebuild after the landing resolves the token")
            self.assertNotEqual(km._chat_pl_precheck(SID, [(u, "the bug is in render.js")])[1], pre[1],
                                "the new generation re-resolves the tab's tokens")
        finally:
            gate.set()

    def test_a_git_index_touch_re_lists_only_the_tracked_half_and_keeps_the_generation(self):
        cwd = str(self.cwd)
        u = self._uuid()
        idx, gen = km._repo_index_served(cwd)
        self.assertIn("render.js", idx)
        pre = km._chat_pl_precheck(SID, [(u, "see deep.py")])
        calls = _ListingCalls()
        with mock.patch.object(km.subprocess, "run", calls):
            gi = self.cwd / ".git" / "index"
            st = gi.stat()
            os.utime(gi, (st.st_atime, st.st_mtime + 5))              # what a `git status` refreshing stat data does
            idx2, gen2 = km._repo_index_served(cwd)
            self.assertEqual(calls.calls, ["c"], "the tracked half alone: the ten-second walk is not re-taken for it")
            self.assertEqual((idx2, gen2), (idx, gen), "same listing, same generation")
            self.assertEqual(km._chat_pl_precheck(SID, [(u, "see deep.py")], pre)[1], pre[1],
                             "the pre-check's key held, so the tab re-resolves nothing")
            self.write("ui/late.js", "// created in an existing subdir\n")
            idx3, gen3 = km._repo_index_served(cwd)
            self.assertEqual(calls.calls, ["c", "o"], "a subdir that moved re-walks the untracked half (the index did not move)")
            self.assertNotEqual(gen3, gen)
            self.assertEqual(idx3["late.js"], ["ui/late.js"])

    def test_the_tracked_half_losing_a_path_re_walks_the_untracked_half(self):
        cwd = str(self.cwd)
        km._repo_index_served(cwd)
        calls = _ListingCalls()
        with mock.patch.object(km.subprocess, "run", calls):
            subprocess.run(["git", "rm", "-q", "--cached", "ui/render.js"], cwd=self.cwd, check=True)   # untracked now, still on disk
            idx, _gen = km._repo_index_served(cwd)
        self.assertEqual(calls.calls[-2:], ["c", "o"], "the one index move that can add to the untracked set re-walks it")
        self.assertEqual(idx.get("render.js"), ["ui/render.js"], "the file is listed as untracked, not lost between the halves")

    def test_an_unchanged_tree_starts_nothing(self):
        cwd = str(self.cwd)
        km._repo_index_served(cwd)
        calls = _ListingCalls()
        with mock.patch.object(km.subprocess, "run", calls):
            for _ in range(3):
                km._repo_index_served(cwd)
        self.assertEqual(calls.calls, [], "the key held: no listing")


class TokenizerParity(unittest.TestCase):
    """The Python port and path-links.ts CLICKABLE_PATH_RE must agree on what a token IS — the map's keys
    are what the client looks up, so a tokenizer drift silently unlinks. The client side of the same
    fixture runs in chat-path-links.test.ts."""

    def test_the_shared_fixture_tokenizes_identically(self):
        with open(os.path.join(HERE, "fixtures", "path_token_parity.json")) as f:
            cases = json.load(f)["cases"]
        self.assertGreaterEqual(len(cases), 10, "the fixture is the parity surface — keep it broad")
        for c in cases:
            self.assertEqual(km._path_tokens(c["text"]), c["tokens"], c["text"])


class BuildSessionWiring(unittest.TestCase):
    def test_both_event_kinds_carry_the_map_when_a_verdict_exists(self):
        import inspect
        src = inspect.getsource(km.build_session)
        self.assertIn('pl = _path_links(prompt, sid, a.get("uuid"), _pl_memo)', src,
                      "user events get the resolver's verdict")
        self.assertIn('pl = _path_links(txt, sid, a.get("uuid"), _pl_memo)', src,
                      "assistant events get the resolver's verdict")
        self.assertIn('ev["pathLinks"] = pl', src, "the map rides the event")
        self.assertIn("if pl is not None:", src,
                      "an EMPTY map still ships — its presence is what gates the client")
        self.assertIn("_pl_memo = {}", src, "one repo listing per build pass")


if __name__ == "__main__":
    unittest.main()
