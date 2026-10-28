#!/usr/bin/env python3
"""discover() takes ONE fingerprint per scope: a pusher cycle, a jobs pass, a judge pass (judge.py, the comment block
at _DISCOVER_EPOCH).

The fingerprint is discover()'s validity check, a stat walk over every names/ entry, its project dir, its registration
and its recorded transcript dir, and it ran on every call, a cache hit included, although almost every call in a cycle
or a pass found nothing had changed. Inside a scope the fingerprint is taken at the scope's first call and again only
after an in-process write to the namespace it signs (the epoch, judge.discover_namespace_moved) or when a call has to
walk anyway; outside every scope each call takes its own, as before.

Pinned here:
- the scope itself: one fingerprint for many calls, closed scopes and threads without one take theirs per call;
- an oracle over a scripted sequence (a session added, renamed, retired; a registry relocated; a /clear lastSid flip; a
  fork in a project dir; a Codex row), each made by "another process" (plain file writes) while a scope is open: every
  answer inside the scope equals the unoptimized walk at the scope's start, and the next scope's equals the walk at
  ITS start, which carries the change: seen one scope later, never more;
- write-then-read for every in-process writer (sdk_backend.write_name and write_reg, the Codex backend's names and
  registry writers, the kernel's _set_name): the next call in the same scope sees the write;
- a walk inside a scope is stored under a fingerprint taken right before it, never under the scope's older one;
- threads: a thread scope is the thread's own; a pass frame's is shared by its pass threads and no one else;
- the kernel opens and closes the scope around _pusher_cycle and _jobs_cycle.

SYNTHETIC fixtures only: placeholder UUIDs, the notes-api demo's web/api/tests sessions.
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import types
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
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_discover_scope", os.path.join(BIN, "romp-kernel"))
jd = km.jd                                     # the module the backends' hook reaches (sys.modules["romp_judge"])
sb = load_source("romp_sdk_backend", os.path.join(BIN, "romp_sdk_backend.py"))   # the name the kernel loads it under
cb = load_source("romp_codex_backend_discover_scope", os.path.join(os.path.dirname(HERE), "kernel", "codex_backend.py"))

S_WEB = "11111111-2222-3333-4444-555555555501"
S_API = "11111111-2222-3333-4444-555555555502"
S_TESTS = "11111111-2222-3333-4444-555555555503"
S_NEW = "11111111-2222-3333-4444-555555555504"
S_IDLE = "11111111-2222-3333-4444-555555555505"
CLEAR_FSID = "66666666-7777-8888-9999-aaaaaaaaaa01"
FORK = "66666666-7777-8888-9999-aaaaaaaaaa02"
CX_SID = "11111111-2222-3333-4444-555555555506"
CX_TID = "01911111-2222-7333-8444-555555555507"
OLD = 1000                                     # every file the world starts with is back-dated this far, so a later
#                                                write always moves its stamp (a coarse clock cannot tie them)


def _enc(d):
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(d))


class _World(unittest.TestCase):
    """A small notes-api world on a private state root: web (launch-dir walk, a same-title fork lane later), api (an SDK
    registration, relocated later), tests (an SDK registration, /clear'd later), an idle session past the window, and
    an empty Codex registry. Every scope and every pass frame is closed after each test."""

    def setUp(self):
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES)
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        jd.PROJECTS = self.td / "projects"
        km.NAMES = jd.NAMES
        for d in (jd.NAMES, jd.SDKDIR, jd.CODEXDIR, jd.PROJECTS):
            d.mkdir(parents=True, exist_ok=True)
        jd._discover_cache.clear()
        jd._namefp_memo.clear()
        self.now = time.time()
        self.cdir = str(self.td / "work" / "notes-api")
        self.cdir_b = str(self.td / "work" / "notes-api-b")
        for c in (self.cdir, self.cdir_b):
            os.makedirs(c, exist_ok=True)
        self.pdir = jd._proj_dir(self.cdir)
        self.pdir.mkdir(parents=True)
        self._transcript(self.pdir, S_WEB, "web")
        self._transcript(self.pdir, S_API, "api")
        self._transcript(self.pdir, S_TESTS, "tests")
        idle = self._transcript(self.pdir, S_IDLE, "idle")
        for sid, name in ((S_WEB, "web"), (S_API, "api"), (S_TESTS, "tests"), (S_IDLE, "idle")):
            (jd.NAMES / sid).write_text("%s\t%s\t#abcdef\tblack\n" % (name, self.cdir))
        for sid in (S_API, S_TESTS):
            (jd.SDKDIR / (sid + ".json")).write_text(json.dumps({"sid": sid, "cwd": self.cdir, "alive": True}))
        (jd.CODEXDIR / "registry.json").write_text("{}")
        back = self.now - OLD
        for root in (jd.STATE, jd.PROJECTS):
            for dirpath, dirs, files in os.walk(root):
                for f in files + dirs:
                    os.utime(os.path.join(dirpath, f), (back, back))
                os.utime(dirpath, (back, back))
        stale = self.now - 3 * 86400                 # idle three days: outside the 48 h window
        os.utime(idle, (stale, stale))
        jd.discover_scope_close()

    def tearDown(self):
        jd.discover_scope_close()
        if jd._frame is not None:
            jd.end_pass_frame(True)
        jd._judge_ctx.in_pass = False
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, km.NAMES = self.saved[1], self.saved[2]
        jd._discover_cache.clear()
        jd._namefp_memo.clear()
        shutil.rmtree(self.td, ignore_errors=True)

    # ── helpers ──
    def _transcript(self, pdir, stem, title):
        p = Path(pdir) / (stem + ".jsonl")
        p.write_text(json.dumps({"type": "custom-title", "customTitle": title}) + "\n"
                     + json.dumps({"type": "user", "uuid": "u-" + stem[-4:]}) + "\n")
        return p

    @staticmethod
    def _ext_write(path, text):
        """Another process's write: published by rename like romp's writers, but through no romp writer (no epoch)."""
        path = Path(path)
        tmp = path.with_name(path.name + ".ext.tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    def _keys(self):
        """The calls a scope makes: the 48 h list with and without fork lanes, the picker's wide one, and the 48 h list
        again with a later clock."""
        return [(self.now, None, True), (self.now, None, False), (self.now, 30 * 86400, True), (self.now + 60, None, True)]

    def _oracle(self):
        """The unoptimized discover() for every key, now: the walk itself, through no cache and no scope."""
        return [jd._discover_impl(n, w, f) for n, w, f in self._keys()]

    def _answers(self):
        return [jd.discover(n, w, f) for n, w, f in self._keys()]

    def _count_fps(self):
        """Patch the fingerprint with a counting wrapper; returns the list each call appends to."""
        real = jd._discover_fingerprint
        calls = []

        def counting():
            calls.append(threading.get_ident())
            return real()
        p = mock.patch.object(jd, "_discover_fingerprint", counting)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def _open(self, kind):
        if kind == "thread":
            jd.discover_scope_open()
            return None
        return jd.begin_pass_frame()

    def _close(self, kind, own):
        if kind == "thread":
            jd.discover_scope_close()
        else:
            jd.end_pass_frame(own)


class ScopeBasics(_World):
    def test_outside_a_scope_every_call_takes_its_own_fingerprint(self):
        fps = self._count_fps()
        for _ in range(4):
            jd.discover(self.now)
        self.assertEqual(len(fps), 4, "no scope: the fingerprint is the per-call validity check, as before")

    def test_a_thread_scope_takes_one_fingerprint_for_many_calls(self):
        jd.discover(self.now)                                  # warm: the cache holds the 48 h list
        fps = self._count_fps()
        jd.discover_scope_open()
        first = jd.discover(self.now)
        for _ in range(5):
            self.assertIs(jd.discover(self.now), first, "a hit hands out the cached list itself")
        self.assertEqual(len(fps), 1, "one fingerprint for the scope, at its first call")
        jd.discover_scope_close()
        jd.discover(self.now)
        self.assertEqual(len(fps), 2, "closed: the next call takes its own again")

    def test_a_pass_frame_is_one_scope_for_every_pass_thread(self):
        jd.discover(self.now)
        fps = self._count_fps()
        own = jd.begin_pass_frame()
        a = jd.discover(self.now)
        with jd.ThreadPoolExecutor(max_workers=2) as ex:      # the module's pools: pass threads (_TimedPool)
            got = [ex.submit(jd.discover, self.now).result() for _ in range(3)]
        self.assertTrue(all(g is a for g in got))
        self.assertEqual(len(fps), 1, "the frame's one fingerprint serves the pass thread and its pool workers")
        jd.end_pass_frame(own)
        jd.discover(self.now)
        self.assertEqual(len(fps), 2, "the frame ended: per call again")

    def test_a_thread_outside_the_frame_takes_its_own_fingerprints(self):
        """The kernel's pusher thread is no pass thread: during a pass it reads the live namespace per call (or through its
        own cycle scope), never the frame's."""
        jd.discover(self.now)
        fps = self._count_fps()
        own = jd.begin_pass_frame()
        jd.discover(self.now)
        out = []
        t = threading.Thread(target=lambda: out.extend(jd.discover(self.now) for _ in range(3)))
        t.start(); t.join()
        self.assertEqual(len(fps), 4, "the frame's one, plus one per call on the non-pass thread")
        jd.end_pass_frame(own)

    def test_thread_scopes_are_not_shared(self):
        jd.discover(self.now)
        jd.discover_scope_open()
        jd.discover(self.now)
        mine = jd._discover_scope.memo
        seen = {}

        def other():
            seen["before"] = getattr(jd._discover_scope, "memo", None)
            jd.discover_scope_open()
            jd.discover(self.now)
            seen["memo"] = jd._discover_scope.memo
            jd.discover_scope_close()
        t = threading.Thread(target=other)
        t.start(); t.join()
        self.assertIsNone(seen["before"], "a scope is its own thread's")
        self.assertIsNot(seen["memo"], mine)
        self.assertIs(jd._discover_scope.memo, mine, "the other thread's open and close left this scope as it was")

    def test_an_external_change_mid_scope_is_seen_by_a_thread_without_the_scope(self):
        jd.discover(self.now)
        jd.discover_scope_open()
        before = jd.discover(self.now)
        jd._proj_dir(self.cdir_b).mkdir(parents=True)
        self._transcript(jd._proj_dir(self.cdir_b), S_NEW, "web-b")
        self._ext_write(jd.NAMES / S_NEW, "web-b\t%s\t#abcdef\tblack\n" % self.cdir_b)   # another process adds a session
        out = {}
        t = threading.Thread(target=lambda: out.setdefault("rows", jd.discover(self.now)))
        t.start(); t.join()
        self.assertIn(S_NEW, [r[0] for r in out["rows"]], "no scope on that thread: it sees the change at once")
        self.assertNotIn(S_NEW, [r[0] for r in before])
        self.assertEqual(jd.discover(self.now), jd._discover_impl(self.now),
                         "the scope's answer is still a walk's: the other thread's fresher entry is taken whole")

    def test_a_write_landing_while_the_fingerprint_is_taken_is_seen_at_the_next_call(self):
        """The epoch is read BEFORE the fingerprint: a write that lands after the stats (here inside the call, as another
        thread's would) leaves the scope holding a fingerprint of the old epoch, which the next call drops."""
        jd.discover(self.now)                                    # the cache holds the 48 h list
        real = jd._discover_fingerprint
        fired = []

        def stats_then_a_write():
            got = real()
            if not fired:
                fired.append(1)
                sb.write_name(jd.STATE, S_WEB, "web-renamed", self.cdir, "#abcdef", "black")
            return got
        with mock.patch.object(jd, "_discover_fingerprint", stats_then_a_write):
            jd.discover_scope_open()
            first = jd.discover(self.now)
            second = jd.discover(self.now)
        self.assertNotIn((S_WEB, "web-renamed"), [(r[0], r[3]) for r in first], "that call began before the write")
        self.assertIn((S_WEB, "web-renamed"), [(r[0], r[3]) for r in second])
        self.assertEqual(second, jd._discover_impl(self.now))

    def test_the_window_check_still_runs_on_every_call(self):
        """An idle session typed into again crosses back into the window with no namespace change: inside a scope as
        outside, the call that follows sees it (the per-call window check is unchanged)."""
        jd.discover_scope_open()
        a = jd.discover(self.now)
        self.assertNotIn(S_IDLE, [r[0] for r in a])
        with (self.pdir / (S_IDLE + ".jsonl")).open("a") as fh:
            fh.write(json.dumps({"type": "user", "uuid": "u-again"}) + "\n")
        b = jd.discover(time.time())
        self.assertIn(S_IDLE, [r[0] for r in b])
        self.assertEqual(b, jd._discover_impl(time.time()))


class OracleOverASequence(_World):
    """Each step is another process's change, made while a scope is open: the open scope keeps answering what the walk
    answered at its start, and the next scope answers what the walk answers at ITS start, which carries the change."""

    def _steps(self):
        pdir_b = jd._proj_dir(self.cdir_b)
        wt = str(self.td / "work" / "notes-api" / ".worktrees" / "wt1")

        def add():
            pdir_b.mkdir(parents=True, exist_ok=True)
            self._transcript(pdir_b, S_NEW, "web-b")
            self._ext_write(jd.NAMES / S_NEW, "web-b\t%s\t#abcdef\tblack\n" % self.cdir_b)

        def rename():
            self._ext_write(jd.NAMES / S_WEB, "web-renamed\t%s\t#abcdef\tblack\n" % self.cdir)

        def remove():
            (jd.NAMES / S_NEW).unlink()

        def relocate():
            os.makedirs(wt, exist_ok=True)
            pw = jd._proj_dir(wt)
            pw.mkdir(parents=True, exist_ok=True)
            rec = self._transcript(pw, S_API, "api")
            self._ext_write(jd.SDKDIR / (S_API + ".json"),
                            json.dumps({"sid": S_API, "cwd": self.cdir, "alive": True, "transcriptPath": str(rec)}))

        def clear():
            self._transcript(self.pdir, CLEAR_FSID, "tests")
            self._ext_write(jd.SDKDIR / (S_TESTS + ".json"),
                            json.dumps({"sid": S_TESTS, "cwd": self.cdir, "alive": True, "lastSid": CLEAR_FSID}))

        def fork():
            self._transcript(self.pdir, FORK, "web-renamed")       # a same-title lane of web (renamed above)

        def codex():
            cx = jd.CODEXDIR / "projects" / _enc(self.cdir)
            cx.mkdir(parents=True, exist_ok=True)
            self._transcript(cx, CX_TID, "cx")
            self._ext_write(jd.CODEXDIR / "registry.json",
                            json.dumps({CX_SID: {"tid": CX_TID, "name": "tests-cx", "cwd": self.cdir}}))

        return [("entry added", add), ("entry renamed", rename), ("entry removed", remove),
                ("registry relocated", relocate), ("fork in a project dir", fork),
                ("/clear lastSid flip", clear), ("codex row added", codex)]

    def _run(self, kind):
        steps = self._steps()
        fps = self._count_fps()
        for i in range(len(steps) + 1):
            oracle = self._oracle()                         # the unoptimized answers at this scope's start
            if i:
                self.assertNotEqual(oracle, prev, "step %r changes what discover answers" % steps[i - 1][0])
            n0 = len(fps)
            own = self._open(kind)
            try:
                self.assertEqual(self._answers(), oracle, "scope %d: the answers are the walk's at its start" % i)
                if i < len(steps):
                    label, step = steps[i]
                    step()                                  # another process, mid-scope
                    self.assertEqual(self._answers(), oracle,
                                     "%s mid-scope: this scope keeps its start's answers" % label)
                    self.assertEqual(self._answers(), oracle)
            finally:
                self._close(kind, own)
            self.assertLessEqual(len(fps) - n0, len(set((w, f) for _n, w, f in self._keys())),
                                 "at most one fingerprint per walk the scope had to make")
            prev = oracle
        # a scope over an unchanged world: one fingerprint, every answer a hit
        n0 = len(fps)
        own = self._open(kind)
        try:
            self.assertEqual(self._answers(), self._oracle())
        finally:
            self._close(kind, own)
        self.assertEqual(len(fps) - n0, 1)

    def test_oracle_in_thread_scopes(self):
        self._run("thread")

    def test_oracle_in_pass_frames(self):
        self._run("frame")


class _WriterTests:
    """Every in-process writer of the namespace moves the epoch after its publish: the next call in the SAME scope (and
    in every other open scope) takes a fresh fingerprint and sees the write. Run under both scope kinds (the classes
    below the mixin)."""
    KIND = None

    def _scoped(self, write, check):
        jd.discover(self.now)
        jd.discover(self.now, forks=False)
        own = self._open(self.KIND)
        try:
            before = jd.discover(self.now)
            ep = jd._DISCOVER_EPOCH[0]
            write()
            self.assertIsNot(jd._DISCOVER_EPOCH[0], ep, "the writer moved the epoch")
            after = jd.discover(self.now)
            self.assertEqual(after, jd._discover_impl(self.now), "the same scope's next call is the walk's answer now")
            check(before, after)
        finally:
            self._close(self.KIND, own)

    def _codex_stub(self):
        stub = types.SimpleNamespace(root=jd.CODEXDIR, state=jd.STATE, _names_lock=threading.Lock())
        stub._reg_path = lambda: jd.CODEXDIR / "registry.json"
        return stub

    def test_sdk_write_name_creates_a_session(self):
        def write():
            pdir_b = jd._proj_dir(self.cdir_b)
            pdir_b.mkdir(parents=True, exist_ok=True)
            self._transcript(pdir_b, S_NEW, "web-b")           # the CLI's file, then the names entry LAST (create's order)
            sb.write_name(jd.STATE, S_NEW, "web-b", self.cdir_b, "#abcdef", "black")

        def check(before, after):
            self.assertNotIn(S_NEW, [r[0] for r in before])
            self.assertIn(S_NEW, [r[0] for r in after])
        self._scoped(write, check)

    def test_sdk_write_name_renames_a_session(self):
        self._scoped(lambda: sb.write_name(jd.STATE, S_WEB, "web-renamed", self.cdir, "#abcdef", "black"),
                     lambda b, a: self.assertIn((S_WEB, "web-renamed"), [(r[0], r[3]) for r in a]))

    def test_sdk_write_reg_lastsid_flip(self):
        def write():
            self._transcript(self.pdir, CLEAR_FSID, "tests")
            sb.write_reg(jd.STATE, S_TESTS, {"sid": S_TESTS, "cwd": self.cdir, "alive": True, "lastSid": CLEAR_FSID})
        self._scoped(write, lambda b, a: self.assertEqual([r[1].name for r in a if r[0] == S_TESTS],
                                                          [CLEAR_FSID + ".jsonl"]))

    def test_sdk_write_reg_transcript_relocation(self):
        wt = str(self.td / "work" / "notes-api" / ".worktrees" / "wt1")

        def write():
            os.makedirs(wt, exist_ok=True)
            pw = jd._proj_dir(wt)
            pw.mkdir(parents=True, exist_ok=True)
            rec = self._transcript(pw, S_API, "api")
            sb.write_reg(jd.STATE, S_API, {"sid": S_API, "cwd": self.cdir, "alive": True, "transcriptPath": str(rec)})
        self._scoped(write, lambda b, a: self.assertEqual([str(r[1].parent) for r in a if r[0] == S_API],
                                                          [str(jd._proj_dir(wt))]))

    def test_codex_registry_write(self):
        def write():
            cx = jd.CODEXDIR / "projects" / _enc(self.cdir)
            cx.mkdir(parents=True, exist_ok=True)
            self._transcript(cx, CX_TID, "cx")                 # touched BEFORE the row names it (the backend's order)
            cb.CodexBackend._write_registry_locked(
                self._codex_stub(), {CX_SID: {"tid": CX_TID, "name": "tests-cx", "cwd": self.cdir}})

        def check(before, after):
            self.assertNotIn(CX_SID, [r[0] for r in before])
            self.assertIn(CX_SID, [r[0] for r in after])
        self._scoped(write, check)

    def test_codex_names_write(self):
        """The Codex backend's names entry: discovery lists a Codex session through its registry, but the entry is in the
        namespace the fingerprint signs, so its publish moves the epoch like every other writer's (here the entry's
        launch dir holds a transcript of that name, so the answer changes with it)."""
        def write():
            pdir_b = jd._proj_dir(self.cdir_b)
            pdir_b.mkdir(parents=True, exist_ok=True)
            self._transcript(pdir_b, S_NEW, "web-b")
            cb.CodexBackend._write_name(self._codex_stub(),
                                        types.SimpleNamespace(sid=S_NEW, name="web-b", cwd=self.cdir_b, color="#abcdef"))
        self._scoped(write, lambda b, a: self.assertIn(S_NEW, [r[0] for r in a]))

    def test_kernel_set_name(self):
        self._scoped(lambda: km._set_name(S_WEB, "web-renamed"),
                     lambda b, a: self.assertIn((S_WEB, "web-renamed"), [(r[0], r[3]) for r in a]))

    def test_kernel_color_write_leaves_the_answer_unchanged(self):
        """The kernel's color writers (_set_session_color, _set_palette) rewrite only an entry's color fields, which
        discover never reads: the scope's answer stays the walk's with no epoch move."""
        bg = km.pal.colors(next(iter(km.pal.PALETTES)))[0]
        jd.discover(self.now)
        own = self._open(self.KIND)
        try:
            before = jd.discover(self.now)
            self.assertTrue(km._set_session_color(S_WEB, bg))
            self.assertEqual(jd.discover(self.now), jd._discover_impl(self.now))
            self.assertEqual(jd.discover(self.now), before)
        finally:
            self._close(self.KIND, own)

    def test_a_write_on_another_thread_reaches_this_scope(self):
        jd.discover(self.now)
        own = self._open(self.KIND)
        try:
            jd.discover(self.now)
            t = threading.Thread(target=lambda: sb.write_name(jd.STATE, S_WEB, "web-renamed", self.cdir, "#abcdef",
                                                              "black"))
            t.start(); t.join()
            self.assertIn((S_WEB, "web-renamed"), [(r[0], r[3]) for r in jd.discover(self.now)],
                          "the epoch is the process's: a write on any thread drops every open scope's fingerprint")
        finally:
            self._close(self.KIND, own)

    def test_clear_race_lastsid_before_the_file(self):
        """The /clear race: the kernel writes lastSid (in process) before the CLI writes the new transcript (another
        process). The scope takes a fresh fingerprint at the write and, as discover does in that window, keeps the leaf
        it handed out; the file's arrival is seen at the next scope."""
        jd.discover(self.now)
        own = self._open(self.KIND)
        try:
            jd.discover(self.now)
            sb.write_reg(jd.STATE, S_TESTS, {"sid": S_TESTS, "cwd": self.cdir, "alive": True, "lastSid": CLEAR_FSID})
            mid = jd.discover(self.now)
            self.assertEqual([r[1].name for r in mid if r[0] == S_TESTS], [S_TESTS + ".jsonl"])
            self._transcript(self.pdir, CLEAR_FSID, "tests")         # the CLI's file lands
            self.assertEqual(jd.discover(self.now), mid, "this scope: the leaf it had")
        finally:
            self._close(self.KIND, own)
        own = self._open(self.KIND)
        try:
            nxt = jd.discover(self.now)
        finally:
            self._close(self.KIND, own)
        self.assertEqual([r[1].name for r in nxt if r[0] == S_TESTS], [CLEAR_FSID + ".jsonl"], "the next scope reads it")
        self.assertEqual(nxt, jd._discover_impl(self.now))


class InProcessWritersThreadScope(_WriterTests, _World):
    KIND = "thread"


class InProcessWritersPassFrame(_WriterTests, _World):
    KIND = "frame"


class WalksStoreUnderTheirOwnFingerprint(_World):
    def test_a_walk_inside_a_scope_is_never_stored_under_the_scopes_older_fingerprint(self):
        """A key first asked mid-scope walks the namespace as it is NOW. Stored under the scope's older fingerprint, that
        walk would be served whenever the namespace signs as it did at the scope's start again: here a session added in a
        launch dir of its own and then retired leaves every signed stamp as it was, so the next scope would list a
        session whose names entry is gone."""
        jd.discover_scope_open()
        jd.discover(self.now)                                       # the scope's fingerprint; the 48 h key only
        pdir_b = jd._proj_dir(self.cdir_b)
        pdir_b.mkdir(parents=True)
        self._transcript(pdir_b, S_NEW, "web-b")
        self._ext_write(jd.NAMES / S_NEW, "web-b\t%s\t#abcdef\tblack\n" % self.cdir_b)   # another process adds a session
        wide = jd.discover(self.now, window=30 * 86400)             # a key not cached yet: walks now
        self.assertIn(S_NEW, [r[0] for r in wide])
        jd.discover_scope_close()
        (jd.NAMES / S_NEW).unlink()                                 # ...and retires it: only an unsigned dir keeps a trace
        jd.discover_scope_open()
        try:
            again = jd.discover(self.now, window=30 * 86400)
        finally:
            jd.discover_scope_close()
        self.assertNotIn(S_NEW, [r[0] for r in again])
        self.assertEqual(again, jd._discover_impl(self.now, 30 * 86400))


class KernelScopeHooks(unittest.TestCase):
    """The kernel opens the scope as the pusher cycle and the jobs pass begin and closes it in their finally."""

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km.Sessions.live, km._sdk, km._turn_notify_tick,
                      km._api_health_frame, km._api_health_push, km._lift_spent_awaiting)
        jd._rebind_state(self.td / "state")
        jd.PROJECTS = self.td / "projects"
        km.NAMES = jd.NAMES
        cdir = str(self.td / "work")
        os.makedirs(cdir)
        pdir = jd._proj_dir(cdir)
        pdir.mkdir(parents=True)
        jd.NAMES.mkdir(parents=True)
        (pdir / (S_WEB + ".jsonl")).write_text(json.dumps({"type": "user", "uuid": "u1"}) + "\n")
        (jd.NAMES / S_WEB).write_text("web\t%s\t#abcdef\tblack\n" % cdir)
        jd._discover_cache.clear()
        km.Sessions.live = lambda: {}
        km._sdk = lambda: None
        km._api_health_frame = lambda now, live_map: None
        km._api_health_push = lambda frame: None
        self.calls = []
        passes = mock.patch.dict(km._PERF_STATS.jobs, {"passes": max(km._PERF_STATS.jobs.get("passes", 0), 1)})
        passes.start()
        self.addCleanup(passes.stop)

    def tearDown(self):
        (jd.STATE, jd.PROJECTS, km.NAMES, km.Sessions.live, km._sdk, km._turn_notify_tick,
         km._api_health_frame, km._api_health_push, km._lift_spent_awaiting) = self.saved
        jd._rebind_state(self.saved[0])
        jd.discover_scope_close()
        for slot in ("snapshot", "sessions", "paths", "names", "files_stat"):
            setattr(km._live_scope, slot, None)
        jd._discover_cache.clear()
        shutil.rmtree(self.td, ignore_errors=True)

    def _probe(self, *_a):
        for _ in range(3):
            self.calls.append(jd.discover(time.time()))
            self.calls.append(jd.discover(time.time(), forks=False))
        self.calls.append(getattr(jd._discover_scope, "memo", None) is not None)

    def _fps(self):
        real = jd._discover_fingerprint
        calls = []
        p = mock.patch.object(jd, "_discover_fingerprint", lambda: (calls.append(1), real())[1])
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_the_pusher_cycle_is_one_scope(self):
        km._turn_notify_tick = lambda now, live_map: self._probe()
        km._pusher_cycle()                                      # warm: every key the cycle asks for is cached
        self.calls.clear()
        fps = self._fps()
        km._pusher_cycle()
        self.assertTrue(self.calls[-1], "the probe ran inside the cycle's scope")
        self.assertGreaterEqual(len(self.calls) - 1, 6)
        self.assertEqual(len(fps), 1, "one fingerprint for the whole cycle")
        self.assertIsNone(getattr(jd._discover_scope, "memo", None), "the scope closes with the cycle")

    def test_the_jobs_pass_is_one_scope(self):
        km._lift_spent_awaiting = lambda now, live_map: self._probe()
        km._jobs_cycle()
        self.calls.clear()
        fps = self._fps()
        km._jobs_cycle()
        self.assertTrue(self.calls[-1], "the probe ran inside the pass's scope")
        self.assertEqual(len(fps), 1, "one fingerprint for the whole pass")
        self.assertIsNone(getattr(jd._discover_scope, "memo", None), "the scope closes with the pass")


if __name__ == "__main__":
    unittest.main()
