#!/usr/bin/env python3
"""Two per-call reads on the chat signature's path, taken once (kernel _sdk_transcript_path, _tree_of, _tree_branch,
_cycle_memo).

Every open tab's signature, every rebuilt tab and the feed's and timeline's per-session reads ask, each cycle, for a
session's would-be transcript path (the awaiting-stamp view, _session_stamp_read) and for the git tree and branch of
its directories. The path read the session's names entry from disk at every call; the tree walked the directory chain
for `.git` and stat'd the branch's HEAD at every call.

- The names entry is now read once per identity: its cwd field is kept under the entry's (mtime_ns, size, inode,
  ctime_ns), and a call whose stat finds that identity costs the stat and no read. An oracle over a scripted sequence
  (no entry; written; unchanged; rewritten at the same size with its mtime pinned, the writers' os.replace shape;
  relocated; an empty cwd field; removed; re-made through a symlinked directory; unreadable) checks every answer
  against the unoptimized read, inside a cycle scope and outside one.
- The tree's stats ARE its check, so no cheaper key exists: inside a cycle scope (_cycle_memo, which every scope
  opener marks by installing a fresh _live_scope.paths) the toplevel is taken once per directory and the branch once
  per tree, and the next scope takes them afresh. An oracle over a scripted sequence of other-process changes (a
  `.git` created and removed at different levels, a nested repository, a worktree added, a branch switched, a
  worktree re-made as a plain clone at the same path, a detached HEAD, an empty `.git` git rejects) checks every
  directory, inside a fresh scope at each step, against git itself. Inside one scope a repeated ask makes no stat
  and no fork, a change made mid-scope is seen by the next scope, and a fork that gave no answer is shared with no
  one.

SYNTHETIC fixtures only: placeholder UUIDs, the notes-api demo's web/api/tests sessions, an example GitHub remote.
"""
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source
from git_fixture import git, init_repo, forbid_background

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the load — the kernel resolves its state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
Path(_ROOT, "romp", "session-hosts").write_text("off\n")
km = load_source("romp_kernel_cycle_memo_stamp_tree", os.path.join(BIN, "romp-kernel"))
jd = km.jd

S_WEB = "11111111-2222-3333-4444-5555555555c1"
S_API = "11111111-2222-3333-4444-5555555555c2"
_IDENT = ("tests@example.com", "Tests")
ROOT_ONLY = hasattr(os, "geteuid") and os.geteuid() == 0


def _close_scope():
    """Close it the way every scope closer does: the paths dict cleared, nothing else touched."""
    km._live_scope.paths = None


def _open_scope():
    """A fresh cycle scope on this thread, the way every scope opener marks one (a new _live_scope.paths dict)."""
    km._live_scope.paths = {}


class NamesEntryMemo(unittest.TestCase):
    """_sdk_transcript_path: the names entry's cwd field kept under the entry's identity."""

    def setUp(self):
        _close_scope()
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES)
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        jd.PROJECTS = self.td / "projects"
        km.NAMES = jd.NAMES
        km.NAMES.mkdir(parents=True)
        km._sdk_cwd_memo.clear()
        self.work = self.td / "work"
        # two directories whose paths have the same length, so a relocation between them keeps the entry's size
        self.dir_a = self.work / "notes-api"
        self.dir_b = self.work / "notes-web"
        self.dir_c = self.work / "notes-docs-elsewhere"
        for d in (self.dir_a, self.dir_b, self.dir_c):
            d.mkdir(parents=True)
        self.entry = km.NAMES / S_WEB

    def tearDown(self):
        _close_scope()
        km._sdk_cwd_memo.clear()
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, km.NAMES = self.saved[1], self.saved[2]
        shutil.rmtree(self.td, ignore_errors=True)

    def oracle(self, sid):
        """The unoptimized read this memo replaced: the entry read whole at every call."""
        try:
            parts = (km.NAMES / sid).read_text().rstrip("\n").split("\t")
            cwd = parts[1] if len(parts) > 1 and parts[1] else os.path.expanduser("~")
        except OSError:
            cwd = os.path.expanduser("~")
        return jd._proj_dir(cwd) / (sid + ".jsonl")

    def publish(self, text, mtime_ns=None):
        """Publish the entry the way every writer does (a staged file, then os.replace), optionally pinning its mtime."""
        tmp = self.entry.with_suffix(".tmp")
        tmp.write_text(text)
        if mtime_ns is not None:
            os.utime(tmp, ns=(mtime_ns, mtime_ns))
        os.replace(tmp, self.entry)

    def check(self, what):
        want = self.oracle(S_WEB)
        self.assertEqual(km._sdk_transcript_path(S_WEB), want, "outside a scope: " + what)
        _open_scope()
        try:
            self.assertEqual(km._sdk_transcript_path(S_WEB), want, "inside a scope: " + what)
            self.assertEqual(km._sdk_transcript_path(S_WEB), want, "inside a scope, asked again: " + what)
        finally:
            _close_scope()
        return want

    def reads_of_entry(self, fn):
        seen = []
        real = Path.read_text

        def counted(p, *a, **k):
            if str(p) == str(self.entry):
                seen.append(1)
            return real(p, *a, **k)
        with mock.patch.object(Path, "read_text", counted):
            fn()
        return len(seen)

    def test_an_oracle_over_a_scripted_sequence_of_entry_changes(self):
        home = self.check("no entry is the home directory")
        self.assertEqual(home, jd._proj_dir(os.path.expanduser("~")) / (S_WEB + ".jsonl"))
        self.publish("web\t%s\t#336699\t#ffffff\n" % self.dir_a)
        self.assertEqual(self.check("written"), jd._proj_dir(str(self.dir_a)) / (S_WEB + ".jsonl"))
        self.check("unchanged")
        # rewritten at the same size, with the mtime pinned to the old one: the inode tells the two files apart. The old
        # file is kept alive by a second link, so its inode number cannot be handed straight back to the new one.
        old = os.stat(self.entry)
        keep = self.td / "old-entry"
        os.link(self.entry, keep)
        self.assertEqual(len(str(self.dir_a)), len(str(self.dir_b)))
        self.publish("web\t%s\t#336699\t#ffffff\n" % self.dir_b, mtime_ns=old.st_mtime_ns)
        new = os.stat(self.entry)
        self.assertEqual((new.st_size, new.st_mtime_ns), (old.st_size, old.st_mtime_ns), "the fixture: same size, same mtime")
        self.assertNotEqual(new.st_ino, old.st_ino)
        self.assertEqual(self.check("rewritten at the same size and mtime"), jd._proj_dir(str(self.dir_b)) / (S_WEB + ".jsonl"))
        self.publish("web\t%s\t#336699\t#ffffff\n" % self.dir_c)
        self.assertEqual(self.check("relocated"), jd._proj_dir(str(self.dir_c)) / (S_WEB + ".jsonl"))
        self.publish("web\t\t#336699\t#ffffff\n")
        self.assertEqual(self.check("an empty cwd field"), home)
        self.entry.unlink()
        self.assertEqual(self.check("removed"), home)
        self.assertNotIn(str(self.entry), km._sdk_cwd_memo, "a removed entry drops its memo")
        link = self.work / "linked-api"
        os.symlink(self.dir_a, link)
        self.publish("web\t%s\n" % link)
        self.assertEqual(self.check("re-made through a symlinked directory"),
                         jd._proj_dir(os.path.realpath(str(self.dir_a))) / (S_WEB + ".jsonl"))

    def test_an_unreadable_entry_is_the_home_directory_and_keeps_nothing(self):
        if ROOT_ONLY:
            self.skipTest("root reads through chmod 000")
        self.publish("web\t%s\n" % self.dir_a)
        os.chmod(self.entry, 0)
        try:
            self.check("unreadable")
            self.assertNotIn(str(self.entry), km._sdk_cwd_memo)
        finally:
            os.chmod(self.entry, 0o644)
        self.assertEqual(self.check("readable again"), jd._proj_dir(str(self.dir_a)) / (S_WEB + ".jsonl"))

    def test_an_unchanged_entry_is_read_once_and_a_rewrite_reads_it_again(self):
        self.publish("web\t%s\n" % self.dir_a)
        self.assertEqual(self.reads_of_entry(lambda: km._sdk_transcript_path(S_WEB)), 1, "the first call reads the entry")
        self.assertEqual(self.reads_of_entry(lambda: [km._sdk_transcript_path(S_WEB) for _ in range(5)]), 0,
                         "while the entry's identity stands, a call costs its stat and no read")
        self.publish("web\t%s\n" % self.dir_c)
        self.assertEqual(self.reads_of_entry(lambda: km._sdk_transcript_path(S_WEB)), 1, "a rewrite is read again")

    def test_the_project_directory_is_resolved_once_per_scope(self):
        self.publish("web\t%s\n" % self.dir_a)
        (km.NAMES / S_API).write_text("api\t%s\n" % self.dir_a)
        real = os.path.realpath
        with mock.patch("os.path.realpath", wraps=real) as rp:
            _open_scope()
            try:
                a = [km._sdk_transcript_path(S_WEB) for _ in range(3)] + [km._sdk_transcript_path(S_API)]
            finally:
                _close_scope()
            self.assertEqual(rp.call_count, 1, "one realpath for one directory in one scope, whichever session asks")
            _open_scope()
            try:
                km._sdk_transcript_path(S_WEB)
            finally:
                _close_scope()
            self.assertEqual(rp.call_count, 2, "the next scope resolves it afresh")
            km._sdk_transcript_path(S_WEB)
            km._sdk_transcript_path(S_WEB)
            self.assertEqual(rp.call_count, 4, "outside every scope each call resolves it, as before")
        self.assertEqual(a[0], jd._proj_dir(str(self.dir_a)) / (S_WEB + ".jsonl"))
        self.assertEqual(a[3], jd._proj_dir(str(self.dir_a)) / (S_API + ".jsonl"))


def _truth(d):
    """git's own answer for `d`: (toplevel, branch), ("", "") outside every tree; a detached HEAD is no branch."""
    r = subprocess.run(["git", "-C", d, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    top = r.stdout.strip() if r.returncode == 0 else ""
    if not top:
        return "", ""
    b = subprocess.run(["git", "-C", top, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True)
    br = b.stdout.strip() if b.returncode == 0 else ""
    return top, ("" if br == "HEAD" else br)


class TreeScope(unittest.TestCase):
    """_tree_of and _tree_branch inside cycle scopes, against git itself."""

    def setUp(self):
        _close_scope()
        self.root = os.path.realpath(tempfile.mkdtemp())
        self.main = os.path.join(self.root, "notes-api")
        os.makedirs(os.path.join(self.main, "src", "deep"))
        init_repo(self.main, "-q", "-b", "main", ident=_IDENT)
        Path(self.main, "src", "deep", "notes.py").write_text("x = 1\n")
        git(self.main, "add", "-A")
        git(self.main, "commit", "-q", "-m", "seed", ident=_IDENT)
        git(self.main, "remote", "add", "origin", "https://github.com/example/notes-api.git")
        self.web = os.path.join(self.root, "notes-api-web")
        git(self.main, "worktree", "add", "-q", "-b", "web", self.web, "HEAD")
        forbid_background(self.web)
        self.scratch = os.path.join(self.root, "scratch")
        os.makedirs(os.path.join(self.scratch, "a", "b"))
        self.tests_wt = os.path.join(self.root, "notes-api-tests")
        self.dirs = [self.main, os.path.join(self.main, "src"), os.path.join(self.main, "src", "deep"), self.web,
                     os.path.join(self.web, "src", "deep"), self.scratch, os.path.join(self.scratch, "a", "b"),
                     os.path.join(self.tests_wt, "src")]
        self.tick = 0

    def tearDown(self):
        _close_scope()
        shutil.rmtree(self.root, ignore_errors=True)

    def moved(self, tree):
        """A HEAD rewrite's own stamp: the branch memo is keyed on HEAD's mtime, and a coarse clock can give two writes a
        few milliseconds apart the same one, so the rewrite gets a time no earlier write shares."""
        head = git(tree, "rev-parse", "--git-path", "HEAD").stdout.strip()
        head = head if os.path.isabs(head) else os.path.join(tree, head)
        self.tick += 1
        t = os.stat(head).st_mtime + 100 * self.tick
        os.utime(head, (t, t))

    def check_step(self, what):
        """A fresh scope: every directory's answer equals git's, asked twice, and _git_branch agrees with it."""
        _open_scope()
        try:
            for d in self.dirs:
                if not os.path.isdir(d):
                    continue
                want = _truth(d)
                self.assertEqual(km._tree_of(d), want, "%s: %s" % (what, d))
                self.assertEqual(km._tree_of(d), want, "%s, asked again: %s" % (what, d))
                self.assertEqual(km._git_branch(d), want[1], "%s, the branch: %s" % (what, d))
        finally:
            _close_scope()
        for d in self.dirs:                              # and outside every scope, the per-call read agrees
            if os.path.isdir(d):
                self.assertEqual(km._tree_of(d), _truth(d), "%s, outside a scope: %s" % (what, d))

    def test_an_oracle_over_a_scripted_sequence_of_tree_changes(self):
        self.check_step("the world")
        init_repo(os.path.join(self.scratch, "a"), "-q", "-b", "side", ident=_IDENT)
        self.check_step("a .git created halfway up a plain chain")
        shutil.rmtree(os.path.join(self.scratch, "a", ".git"))
        self.check_step("that .git removed")
        init_repo(os.path.join(self.main, "src", "deep"), "-q", "-b", "nested", ident=_IDENT)
        self.check_step("a nested repository below a toplevel")
        shutil.rmtree(os.path.join(self.main, "src", "deep", ".git"))
        self.check_step("the nested repository removed")
        git(self.main, "worktree", "add", "-q", "-b", "tests", self.tests_wt, "HEAD")
        forbid_background(self.tests_wt)
        self.check_step("a worktree added")
        git(self.web, "checkout", "-q", "-b", "web-next")
        self.moved(self.web)
        self.check_step("a branch switched in a worktree")
        shutil.rmtree(self.web)
        git(self.main, "clone", "-q", "-b", "main", self.main, self.web)
        forbid_background(self.web)
        git(self.web, "checkout", "-q", "-b", "clone-branch")
        self.moved(self.web)
        self.check_step("a worktree re-made as a plain clone at the same path")
        git(self.main, "checkout", "-q", "--detach")
        self.moved(self.main)
        self.check_step("a detached HEAD")
        os.makedirs(os.path.join(self.scratch, ".git"))
        self.check_step("an empty .git that git rejects")

    def test_inside_a_scope_a_repeated_ask_makes_no_stat_and_no_fork(self):
        sub = os.path.join(self.web, "src", "deep")
        _open_scope()
        try:
            first = {d: (km._tree_of(d), km._git_branch(d), km._github_repo_of(d)) for d in (self.main, self.web, sub)}
            stats, forks = [], []
            real_stat, real_run = os.stat, km.subprocess.run

            def cstat(p, *a, **k):
                stats.append(p)
                return real_stat(p, *a, **k)

            def crun(*a, **k):
                forks.append(a)
                return real_run(*a, **k)
            with mock.patch.object(os, "stat", cstat), mock.patch.object(km.subprocess, "run", crun):
                again = {d: (km._tree_of(d), km._git_branch(d)) for d in (self.main, self.web, sub)}
            self.assertEqual(again, {d: v[:2] for d, v in first.items()})
            self.assertEqual(forks, [], "no fork in a scope that already holds the answers")
            self.assertEqual(stats, [], "no stat either: the walk, the pointer and HEAD were taken once")
        finally:
            _close_scope()
        self.assertEqual(first[self.web][0], (self.web, "web"))
        self.assertEqual(first[sub][0], (self.web, "web"))
        self.assertEqual(first[self.main][2], "example/notes-api")

    def test_a_change_made_mid_scope_is_seen_by_the_next_scope(self):
        sub = os.path.join(self.web, "src", "deep")
        _open_scope()
        try:
            self.assertEqual(km._tree_of(self.web), (self.web, "web"))
            git(self.web, "checkout", "-q", "-b", "web-next")              # another process, mid-cycle
            self.moved(self.web)
            self.assertEqual(km._tree_of(self.web), (self.web, "web"), "the scope's answer stands for the scope")
            self.assertEqual(km._tree_of(sub), (self.web, "web"), "a new directory of the tree reads the same branch")
        finally:
            _close_scope()
        _open_scope()
        try:
            self.assertEqual(km._tree_of(self.web), (self.web, "web-next"), "the next scope reads the world afresh")
            self.assertEqual(km._tree_of(sub), (self.web, "web-next"))
        finally:
            _close_scope()

    def test_a_fork_that_gave_no_answer_is_shared_with_no_one(self):
        fresh = os.path.join(self.main, "src")
        km._tree_cache.pop(fresh, None)
        real_run = km.subprocess.run

        def no_git(*a, **k):
            raise FileNotFoundError("git")
        _open_scope()
        try:
            with mock.patch.object(km.subprocess, "run", no_git):
                self.assertEqual(km._tree_of(fresh), ("", ""), "no git: no answer")
            self.assertEqual(km._tree_of(fresh), (self.main, "main"), "the next call in the same scope asks git again")
        finally:
            _close_scope()
        km._branch_cache.pop(self.web, None)
        _open_scope()
        try:
            with mock.patch.object(km.subprocess, "run", no_git):
                self.assertEqual(km._tree_branch(self.web), "", "no git: no branch")
            self.assertEqual(km._tree_branch(self.web), "web", "the next call in the same scope asks git again")
            with mock.patch.object(km.subprocess, "run", no_git):
                self.assertEqual(km._tree_branch(self.web), "web", "and the answer it gave is the scope's")
        finally:
            _close_scope()
        self.assertIs(km.subprocess.run, real_run)

    def test_a_scope_is_this_threads_own_and_ends_with_its_paths_dict(self):
        _open_scope()
        try:
            memo = km._cycle_memo("probe")
            self.assertIsNotNone(memo)
            self.assertIs(km._cycle_memo("probe"), memo, "one memo per name for the scope's life")
            km._live_scope.paths.pop("anything", None)            # a scope's own use of its paths dict ends nothing
            self.assertIs(km._cycle_memo("probe"), memo)
            out = []
            th = threading.Thread(target=lambda: out.append(km._cycle_memo("probe")))
            th.start(); th.join()
            self.assertEqual(out, [None], "another thread with no scope of its own reads afresh")
        finally:
            _close_scope()
        self.assertIsNone(km._cycle_memo("probe"), "closed: every call reads afresh")
        _open_scope()
        try:
            self.assertIsNot(km._cycle_memo("probe"), memo, "a new scope never serves the old one's memo")
            self.assertEqual(km._cycle_memo("probe"), {})
        finally:
            _close_scope()

    def test_the_cycle_and_the_jobs_pass_open_a_fresh_paths_dict_and_clear_it(self):
        """The memo's scope is the scope's _live_scope.paths dict: the pusher cycle and the jobs pass each install a new
        one at the open and clear it at the close."""
        import inspect
        for fn in (km._pusher_cycle, km._jobs_cycle):
            src = inspect.getsource(fn)
            self.assertIn("_live_scope.paths = {}", src, fn.__name__)
            self.assertIn("_live_scope.paths = None", src, fn.__name__)


if __name__ == "__main__":
    unittest.main()
