#!/usr/bin/env python3
"""Change gates on the pusher's idle path (2026-09-08): waiting must not cost CPU.

A profile of an idle devbox kernel (py-spy plus the kernel's own /perf) put most of a core into work whose
inputs had not changed: every comment thread's chat rebuilt per cycle (tests/test_comment_threads.py pins
that gate), the fold prefixes of those threads evicted just before they were rebuilt, the comments stores
re-decoded per cycle, the tasks root re-scanned per build for a session whose store never joins (a negative
memo declared and read but never written), the dormant regs' state tails re-read on every liveness read,
and the working notes re-read on every GET /sessions (polled about once a second by every session's postal
service). Each gate is stat-keyed: an unchanged input costs a stat; a moved input misses exactly.
Synthetic sids and paths; hermetic state."""
import contextlib
import copy
import errno
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from romp_load import load_source
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
# The tasks root TaskJoinMiss writes under is $CLAUDE_CONFIG_DIR/tasks (default ~/.claude/tasks): floored here
# like the state root, before the loads, so a bare `python` or `pytest --noconftest` run never writes its
# synthetic stores into a developer's live ~/.claude (it did, review 2026-09-08). Under pytest, conftest's
# per-test fixture re-asserts its own floor; either way the root is a temp dir.
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp()
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ["ROMP_SERVE_TOKEN"] = "testtok"
jd = load_source("romp_judge_gates", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_gates", os.path.join(BIN, "romp-kernel"))
sb = load_source("romp_sdk_backend_gates", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-555555555555"


class CommentsStoreMemo(unittest.TestCase):
    def setUp(self):
        km._comments_memo.clear()
        km._comments_read_failed.clear()
        (jd.STATE / "comments").mkdir(parents=True, exist_ok=True)

    def test_decoded_once_per_file_version_and_never_shared(self):
        km._save_comments(SID, {"threads": [{"tid": "t1", "sid": "s1", "status": "open"}]})
        real = km._read_comments
        calls = []
        km._read_comments = lambda sid: (calls.append(sid), real(sid))[1]
        try:
            a = km._load_comments_cached(SID)
            b = km._load_comments_cached(SID)
            self.assertEqual(calls, [SID], "one decode for two reads of the same file version")
            self.assertEqual(a, b)
            a["threads"].append({"tid": "junk"})
            self.assertEqual(len(km._load_comments_cached(SID)["threads"]), 1, "a reader's copy leaks nowhere")
            st0 = os.stat(km._comments_path(SID))
            km._save_comments(SID, {"threads": []})          # a rewrite (a rename) moves the key
            # pinned back to the PREVIOUS mtime_ns: the size and the inode still differ, and they must carry the
            # miss on their own (a same-tick rename-publish is exactly the case the three-part key exists for)
            os.utime(km._comments_path(SID), ns=(st0.st_atime_ns, st0.st_mtime_ns))
            self.assertEqual(km._load_comments_cached(SID)["threads"], [], "the new content is read on size and inode alone")
            self.assertEqual(len(calls), 2)
        finally:
            km._read_comments = real

    def test_a_missing_store_reads_empty_and_drops_the_memo(self):
        km._save_comments(SID, {"threads": [{"tid": "t1"}]})
        km._load_comments_cached(SID)
        os.unlink(km._comments_path(SID))
        self.assertEqual(km._load_comments_cached(SID), {"threads": []})
        self.assertNotIn(SID, km._comments_memo)

    def test_the_read_only_callers_use_the_memo_and_the_writers_do_not(self):
        import inspect
        self.assertIn("_load_comments_cached(sid)", inspect.getsource(km._comments_frame))
        self.assertIn("_load_comments_cached(sid)", inspect.getsource(km._comment_markers))
        self.assertNotIn("_load_comments_cached", inspect.getsource(km._comment_thread), "writers read fresh")

    # 2026-10-02: the memo stored whatever the read returned under the key of a stat taken before it, so one EMFILE or
    # bad decode on a store that exists was remembered as no threads.
    ONE = [{"tid": "t1", "sid": "s1", "status": "open", "anchorT": 5, "anchorUuid": "u1"}]
    TWO = ONE + [{"tid": "t2", "sid": "s2", "status": "open", "anchorT": 6, "anchorUuid": "u2"}]
    BAD = '{"threads": ['
    SAID = "comments store for %s could not be read" % SID

    def _new_version_unread(self):
        """The store at ONE, read into the memo, then saved at TWO: the next read of it is a miss, so it reads the file."""
        km._save_comments(SID, {"threads": self.ONE})
        self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1"])
        km._save_comments(SID, {"threads": self.TWO})

    @contextlib.contextmanager
    def _read_fault(self, kind, times=1):
        """The store's next `times` reads fail: "emfile" raises OSError(EMFILE) at the open; "cut" opens the real file and
        hands back its bytes three short, so the decode fails. Every road to the bytes opens through Path.open. Yields the
        list of faults that fired."""
        store, real, fired = km._comments_path(SID), Path.open, []

        class Cut:
            def __init__(self, f):
                self.f = f

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self.f.close()

            def fileno(self):
                return self.f.fileno()

            def read(self, *a):
                return self.f.read()[:-3]

        def fake(path, *a, **k):
            if path == store and len(fired) < times:
                fired.append(kind)
                if kind == "emfile":
                    raise OSError(errno.EMFILE, os.strerror(errno.EMFILE), str(path))
                return Cut(real(path, *a, **k))
            return real(path, *a, **k)

        with mock.patch.object(Path, "open", fake):
            yield fired

    @contextlib.contextmanager
    def _stat_fault(self, code):
        """Every stat of the store inside the block fails with errno `code`, whether or not the store is on disk, through
        os.stat and Path.stat (Python 3.10's Path.stat does not look os.stat up when it runs). Yields the `error` text a
        frame built inside the block carries."""
        store, real_os_stat, real_path_stat, fired = km._comments_path(SID), os.stat, Path.stat, []

        def fail(path):
            fired.append(1)
            raise OSError(code, os.strerror(code), str(path))

        def os_stat(path, *a, **k):
            if isinstance(path, (str, os.PathLike)) and os.fspath(path) == str(store):
                fail(path)
            return real_os_stat(path, *a, **k)

        def path_stat(path, *a, **k):
            if path == store:
                fail(path)
            return real_path_stat(path, *a, **k)

        with mock.patch.object(os, "stat", os_stat), mock.patch.object(Path, "stat", path_stat):
            yield os.strerror(code)
        self.assertTrue(fired, "the fault reached the stat")

    @contextlib.contextmanager
    def _unreadable(self, kind):
        """Every attempt to read the store inside the block fails, and the store holds TWO after it. "emfile": each open
        is refused while TWO is on disk. "stat": each stat of the store fails with EIO while TWO is on disk (_stat_fault).
        "corrupt": BAD is published over the store, a version no read decodes (stores are published whole, so a bad
        version stays bad until the next save), and TWO is saved when the block ends. Yields the `error` text a frame
        built inside the block carries."""
        if kind == "emfile":
            with self._read_fault("emfile", times=10 ** 6) as fired:
                yield os.strerror(errno.EMFILE)
            self.assertTrue(fired, "the fault reached the read")
            return
        if kind == "stat":
            with self._stat_fault(errno.EIO) as why:
                yield why
            return
        try:
            json.loads(self.BAD)
        except ValueError as e:
            why = str(e)
        km._atomic_write(km._comments_path(SID), self.BAD)
        yield why
        km._save_comments(SID, {"threads": self.TWO})

    def test_a_read_that_fails_is_never_memoized(self):
        for kind in ("emfile", "cut"):
            with self.subTest(kind=kind):
                self._new_version_unread()
                with self._read_fault(kind) as fired, contextlib.redirect_stderr(io.StringIO()):
                    with contextlib.suppress(OSError, ValueError):
                        km._load_comments_cached(SID)
                self.assertEqual(fired, [kind], "the fault reached the read")
                self.assertNotIn(SID, km._comments_memo, "a failed read is not memoized")
                self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1", "t2"],
                                 "the next read lists the store as it is on disk")

    def test_a_store_whose_stat_fails_is_an_error_frame_and_no_squares(self):
        km._save_comments(SID, {"threads": self.TWO})
        with mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_comment_frame_entry", lambda th, be, now, live_map=None: {"tid": th["tid"]}):
            for code in (errno.EIO, errno.EACCES, errno.ENOTDIR):
                for caller in ("markers", "frame"):
                    with self.subTest(code=errno.errorcode[code], caller=caller):
                        build = km._comment_markers if caller == "markers" else km._comments_frame
                        km._comments_read_failed.clear()
                        err = io.StringIO()
                        with self._stat_fault(code) as why, contextlib.redirect_stderr(err):
                            got = [build(SID), build(SID)]
                        if caller == "markers":
                            self.assertEqual(got, [[], []], "no squares, and no raise inside the build")
                        else:
                            self.assertEqual(got, [{"type": "comments", "id": SID, "error": why}] * 2,
                                             "the frame carries the error and no thread list")
                        self.assertEqual(err.getvalue().count(self.SAID), 1, "this caller says the episode once")
                        if caller == "markers":
                            self.assertEqual([m["tid"] for m in build(SID)], ["t1", "t2"], "the next build reads the store again")
                        else:
                            self.assertEqual(build(SID)["threads"], [{"tid": "t1"}, {"tid": "t2"}],
                                             "the next build reads the store again")

    def test_the_store_check_finding_no_store_ends_a_failure_episode(self):
        km._comments_path(SID).unlink(missing_ok=True)
        err = io.StringIO()
        with mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_comment_frame_entry", lambda th, be, now, live_map=None: {"tid": th["tid"]}):
            with self._stat_fault(errno.EIO), contextlib.redirect_stderr(err):
                self.assertEqual(km._comment_markers(SID), [])
            self.assertEqual(km._comment_markers(SID), [], "the stat finds no store")
            self.assertIsNone(km._comments_frame(SID))
            km._save_comments(SID, {"threads": self.ONE})
            with self._read_fault("emfile") as fired, contextlib.redirect_stderr(err):
                self.assertEqual(km._comments_frame(SID), {"type": "comments", "id": SID, "error": os.strerror(errno.EMFILE)})
            self.assertEqual(fired, ["emfile"], "the fault reached the read")
        self.assertEqual(err.getvalue().count(self.SAID), 2,
                         "a session whose stat failed, then found no store, says its new store's failed read again")

    def test_an_unreadable_store_is_an_error_frame_said_once_on_stderr(self):
        real, reads = km._read_comments, []
        with mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_comment_frame_entry", lambda th, be, now, live_map=None: {"tid": th["tid"]}), \
                mock.patch.object(km, "_read_comments", lambda sid: (reads.append(sid), real(sid))[1]):
            for kind in ("emfile", "corrupt"):
                with self.subTest(kind=kind):
                    self._new_version_unread()
                    del reads[:]
                    err = io.StringIO()
                    with self._unreadable(kind) as why, contextlib.redirect_stderr(err):
                        frames = [km._comments_frame(SID), km._comments_frame(SID)]
                    self.assertTrue(why)
                    self.assertEqual(frames, [{"type": "comments", "id": SID, "error": why}] * 2,
                                     "the frame carries the error and no thread list")
                    self.assertEqual(reads, [SID, SID], "each build read the file")
                    self.assertEqual(err.getvalue().count(self.SAID), 1, "one stderr line for the episode")
                    self.assertEqual(km._comments_frame(SID)["threads"], [{"tid": "t1"}, {"tid": "t2"}],
                                     "the next build reads the store again")
            km._save_comments(SID, {"threads": self.ONE})
            err = io.StringIO()
            with self._read_fault("emfile"), contextlib.redirect_stderr(err):
                km._comments_frame(SID)
            self.assertEqual(err.getvalue().count(self.SAID), 1,
                             "a read that worked ended the episode, so the next failure is said again")

    def test_the_loader_finding_the_store_missing_ends_a_failure_episode(self):
        km._save_comments(SID, {"threads": self.ONE})
        err = io.StringIO()
        with self._read_fault("emfile"), contextlib.redirect_stderr(err), contextlib.suppress(OSError):
            km._load_comments_cached(SID)
        os.unlink(km._comments_path(SID))
        self.assertEqual(km._load_comments_cached(SID), {"threads": []})
        km._save_comments(SID, {"threads": self.ONE})
        with self._read_fault("emfile"), contextlib.redirect_stderr(err), contextlib.suppress(OSError):
            km._load_comments_cached(SID)
        self.assertEqual(err.getvalue().count(self.SAID), 2,
                         "a store the loader found missing, then came back and failed, is said again")

    def test_a_failed_stat_in_the_loader_raises_is_said_once_and_leaves_no_memo(self):
        km._save_comments(SID, {"threads": self.TWO})
        km._load_comments_cached(SID)
        self.assertIn(SID, km._comments_memo)
        err = io.StringIO()
        with self._stat_fault(errno.EIO), contextlib.redirect_stderr(err):
            for _ in range(2):
                with self.assertRaises(OSError):
                    km._load_comments_cached(SID)
        self.assertEqual(err.getvalue().count(self.SAID), 1, "the loader's own stat failure is said once")
        self.assertNotIn(SID, km._comments_memo, "and leaves nothing in the memo")
        self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1", "t2"])

    def test_a_decoded_store_that_is_not_an_object_reads_as_no_threads(self):
        for body in ("[]", "null"):
            with self.subTest(body=body):
                km._atomic_write(km._comments_path(SID), body)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(km._load_comments_cached(SID), {"threads": []})
                    self.assertEqual(km._comment_markers(SID), [], "no squares, and no raise inside the build")
                self.assertEqual(err.getvalue(), "")

    def test_a_store_nested_too_deep_is_an_error_frame_and_no_squares(self):
        def nest(n):
            return "[" * n + "]" * n
        too_deep = '{"threads": %s}' % nest(10 ** 5)
        with self.assertRaises(RecursionError, msg="premise: too deep to decode"):
            json.loads(too_deep)
        decodes = '{"threads": [], "x": %s}' % nest(sys.getrecursionlimit() * 3 // 5)
        decoded = json.loads(decodes)                       # premise: this one decodes...
        with self.assertRaises(RecursionError, msg="premise: ...and is too deep to copy"):
            copy.deepcopy(decoded)
        for where, body in (("to decode", too_deep), ("to copy", decodes)):
            with self.subTest(where=where):
                km._atomic_write(km._comments_path(SID), body)
                err = io.StringIO()
                with mock.patch.object(km, "_sdk", lambda: None), contextlib.redirect_stderr(err):
                    squares = km._comment_markers(SID)
                    frame = km._comments_frame(SID)
                self.assertEqual(squares, [], "no squares, and no raise inside the build")
                self.assertEqual(sorted(frame), ["error", "id", "type"], "the frame carries an error and no thread list")
                self.assertEqual(err.getvalue().count(self.SAID), 1, "one stderr line")
                km._save_comments(SID, {"threads": self.TWO})
                self.assertEqual([m["tid"] for m in km._comment_markers(SID)], ["t1", "t2"],
                                 "the next version is read")

    def test_load_comments_reads_a_store_it_cannot_decode_as_no_threads(self):
        too_deep = '{"threads": %s}' % ("[" * 10 ** 5 + "]" * 10 ** 5)
        with self.assertRaises(RecursionError, msg="premise: too deep to decode"):
            json.loads(too_deep)
        for where, body in (("not JSON", self.BAD), ("too deep to decode", too_deep)):
            with self.subTest(where=where):
                km._atomic_write(km._comments_path(SID), body)
                self.assertEqual(km._load_comments(SID), {"threads": []})

    def test_a_store_that_starts_with_a_byte_order_mark_decodes(self):
        km._comments_path(SID).write_bytes(b"\xef\xbb\xbf" + json.dumps({"threads": self.TWO}).encode())   # UTF-8 BOM
        self.assertEqual([t["tid"] for t in km._load_comments(SID)["threads"]], ["t1", "t2"])
        self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1", "t2"])

    def test_the_key_stored_is_the_one_of_the_bytes_read(self):
        store, real = km._comments_path(SID), Path.open
        for when in ("before the open", "after the open"):
            with self.subTest(when=when):
                km._save_comments(SID, {"threads": self.ONE})
                one, swapped = os.stat(store), []

                def fake(path, *a, **k):                   # the store is rewritten once, on one side of the memo's open
                    if path != store or swapped:
                        return real(path, *a, **k)
                    swapped.append(1)
                    if when == "before the open":
                        km._save_comments(SID, {"threads": self.TWO})
                        return real(path, *a, **k)
                    f = real(path, *a, **k)
                    km._save_comments(SID, {"threads": self.TWO})
                    return f

                km._comments_memo.pop(SID, None)
                with mock.patch.object(Path, "open", fake):
                    got = [t["tid"] for t in km._load_comments_cached(SID)["threads"]]
                self.assertEqual(swapped, [1])
                read = os.stat(store) if when == "before the open" else one
                self.assertEqual(got, ["t1", "t2"] if when == "before the open" else ["t1"])
                self.assertEqual(km._comments_memo[SID][0], (read.st_mtime_ns, read.st_size, read.st_ino),
                                 "the bytes decoded are filed under their own version's key")
                self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1", "t2"],
                                 "the next read lists the store as it is on disk")

    def test_a_held_version_is_served_under_a_full_descriptor_table(self):
        km._save_comments(SID, {"threads": self.TWO})
        self.assertEqual([t["tid"] for t in km._load_comments_cached(SID)["threads"]], ["t1", "t2"])
        err = io.StringIO()
        with mock.patch.object(km, "_sdk", lambda: None), \
                mock.patch.object(km, "_comment_frame_entry", lambda th, be, now, live_map=None: {"tid": th["tid"]}), \
                self._read_fault("emfile", times=10 ** 6) as fired, contextlib.redirect_stderr(err):
            frame = km._comments_frame(SID)
            squares = [m["tid"] for m in km._comment_markers(SID)]
        self.assertEqual(fired, [], "a hit opens nothing")
        self.assertEqual(frame.get("threads"), [{"tid": "t1"}, {"tid": "t2"}], "so the frame lists the held version")
        self.assertEqual(squares, ["t1", "t2"], "and so do the squares")
        self.assertNotIn(self.SAID, err.getvalue())

    def test_the_squares_are_back_on_the_build_after_a_failed_read(self):
        for kind in ("emfile", "corrupt"):
            with self.subTest(kind=kind):
                self._new_version_unread()
                with self._unreadable(kind), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(km._comment_markers(SID), [],
                                     "no squares for the build that could not read, and no raise")
                self.assertEqual([m["tid"] for m in km._comment_markers(SID)], ["t1", "t2"])

    def test_the_pusher_sends_the_error_on_the_sessions_slot_and_the_push_goes_on(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, SID + ".jsonl")
            with open(path, "w") as f:
                f.write("{}\n")
            sess = {"sid": SID, "name": "web", "anchor": None, "path": path, "mtime": 0}
            live = {SID: {"state": "waiting", "color": "#888888", "since": 0, "model": "", "effort": "", "context": None,
                          "backend": "sdk"}}
            frame = {"type": "session", "id": SID, "name": "web", "color": None, "status": {"state": "ready"},
                     "ledger": None, "events": []}
            chat = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None}
            lanes = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: None}
            sent, subagents = [], []

            def cycle():
                """One push to a chat page and a timeline page (a first connect: the timeline's live lanes are built in
                the cycle). Returns the keys sent, the session's comments frames and each timeline frame's squares."""
                del sent[:], subagents[:]
                with mock.patch.object(km, "_alive_sessions", lambda now, tm: [dict(sess)]), \
                        mock.patch.object(km, "_chat_tab_sessions", lambda now, tm: [dict(sess)]), \
                        mock.patch.object(km, "_timeline_sessions", lambda now, lm, live_only=False: [dict(sess)]), \
                        mock.patch.object(km, "_built_timeline", [None, None, 0.0, 0.0]), \
                        mock.patch.object(km, "_live_map", lambda: dict(live)), \
                        mock.patch.object(km, "_sdk", lambda: None), \
                        mock.patch.object(km, "build_session", lambda sid, now, live_map=None, **kw: dict(frame)), \
                        mock.patch.object(km, "_cached_feed", lambda *a, **k: {"working": [], "awaiting": [], "asks": []}), \
                        mock.patch.object(km, "_comment_frame_entry", lambda th, be, now, live_map=None: {"tid": th["tid"]}), \
                        mock.patch.object(km, "_push_subagents", lambda *a, **k: subagents.append(1)), \
                        mock.patch.object(km, "_send_client",
                                          lambda c, key, msg, pre=None, sig=None, kind="full": sent.append((key, msg))):
                    km._push([chat, lanes], connect=True, live_map=live)
                return ([k for k, m in sent], [m for k, m in sent if k == ("comments", SID)],
                        [[[q["tid"] for q in s.get("comments") or []] for s in m["data"]["sessions"]]
                         for k, m in sent if k == ("timeline",)])

            for kind in ("emfile", "corrupt", "stat"):
                with self.subTest(kind=kind):
                    self._new_version_unread()
                    with self._unreadable(kind) as why, contextlib.redirect_stderr(io.StringIO()):
                        keys, comments, squares = cycle()
                    self.assertEqual(comments, [{"type": "comments", "id": SID, "error": why}],
                                     "the session's slot carries the error")
                    self.assertEqual(subagents, [1], "the chat stage went on past the comments loop")
                    self.assertIn(("working",), keys, "and so did the feed")
                    self.assertEqual(squares, [[[]]], "the timeline frame went out, with no squares on the lane")
                    self.assertIn(("timelinebars",), keys, "and the push reached its send stage")
                    keys, comments, squares = cycle()
                    self.assertEqual(comments, [{"type": "comments", "id": SID, "threads": [{"tid": "t1"}, {"tid": "t2"}]}],
                                     "the next cycle sends the threads")
                    self.assertEqual(squares, [[["t1", "t2"]]], "and the squares")


class TaskJoinMiss(unittest.TestCase):
    """The negative memo _task_store_resolve reads was never written: same fold pairs, the whole tasks root
    re-read on every call. The tasks root is $CLAUDE_CONFIG_DIR/tasks, floored to a temp dir by this module's
    preamble (and by conftest under pytest); tearDown removes the stores setUp and the tests wrote."""

    def setUp(self):
        km._task_join_miss.clear(); km._task_dir_hint.clear()
        self.root = km._task_store_dir("x").parent
        self._root_was_there = self.root.exists()
        self.root.mkdir(parents=True, exist_ok=True)
        self.made = []
        self._store("session-aaaaaaaa", [(1, "alpha")])
        self._store("session-bbbbbbbb", [(2, "beta")])

    def tearDown(self):
        for d in self.made:
            shutil.rmtree(d, ignore_errors=True)
        if not self._root_was_there:
            shutil.rmtree(self.root, ignore_errors=True)
        km._task_join_miss.clear(); km._task_dir_hint.clear()

    def _store(self, name, tasks):
        d = self.root / name; d.mkdir(exist_ok=True); self.made.append(d)
        for i, subj in tasks:
            (d / ("%d.json" % i)).write_text(json.dumps({"id": str(i), "subject": subj}))
        return d

    def test_a_failed_join_is_remembered_until_the_fold_or_the_root_changes(self):
        # the root scan (a scandir plus a stat per store dir) runs on every call by design: it is the memo's
        # second key; what the memo saves is listing and decoding every task file under every store
        fold = [{"id": "7", "subject": "gamma"}]            # in no store: the join fails
        listed = []
        real = os.listdir
        with mock.patch.object(km.os, "listdir", side_effect=lambda p: (listed.append(str(p)), real(p))[1]):
            self.assertIsNone(km._task_store_resolve(SID, fold))
            self.assertEqual(len(listed), 2, "the first miss reads every store once")
            self.assertEqual(km._task_join_miss[SID][0], {("7", "gamma")}, "the memo carries the pairs...")
            self.assertEqual(sorted(n for n, _m in km._task_join_miss[SID][1]), ["session-aaaaaaaa", "session-bbbbbbbb"],
                             "...and the root's listing")
            self.assertIsNone(km._task_store_resolve(SID, fold))
            self.assertEqual(len(listed), 2, "the same fold under the same root does not re-read the stores")
            fold2 = [{"id": "7", "subject": "gamma"}, {"id": "8", "subject": "delta"}]
            self.assertIsNone(km._task_store_resolve(SID, fold2))
            self.assertEqual(len(listed), 4, "a changed fold re-reads")

    def test_a_miss_a_read_fault_produced_is_not_remembered(self):
        # a task file mid-rewrite decodes as nothing, so its pair is missing and the join fails; remembered, that
        # transient miss held until the fold next changed (status updates never change the pairs), and the todo
        # card showed the store as unreadable for the rest of the session (review 2026-09-08)
        fold = [{"id": "1", "subject": "alpha"}]
        (self.root / "session-aaaaaaaa" / "1.json").write_text('{"id": "1", "sub')      # a partial write
        self.assertIsNone(km._task_store_resolve(SID, fold))
        self.assertNotIn(SID, km._task_join_miss, "a fault is not a verdict")
        (self.root / "session-aaaaaaaa" / "1.json").write_text(json.dumps({"id": "1", "subject": "alpha"}))
        d = km._task_store_resolve(SID, fold)
        self.assertEqual(d.name, "session-aaaaaaaa", "the next call joins")
        self.assertNotIn(SID, km._task_join_miss)

    def test_a_store_that_appears_after_a_genuine_miss_joins_on_the_next_call(self):
        # Claude Code writes the store a moment after the TaskCreate the fold saw: the pairs do not change, the
        # root's listing does, and that is the memo's second key (review 2026-09-08)
        fold = [{"id": "3", "subject": "gamma"}]
        self.assertIsNone(km._task_store_resolve(SID, fold))
        self.assertIn(SID, km._task_join_miss, "premise: a genuine miss is remembered")
        self._store("session-cccccccc", [(3, "gamma")])
        d = km._task_store_resolve(SID, fold)
        self.assertIsNotNone(d, "the appeared store is found without the fold changing")
        self.assertEqual(d.name, "session-cccccccc")
        self.assertNotIn(SID, km._task_join_miss)

    def test_a_successful_join_clears_the_miss_memo(self):
        fold = [{"id": "1", "subject": "alpha"}]
        km._task_join_miss[SID] = {("9", "nothing")}
        d = km._task_store_resolve(SID, fold)
        self.assertEqual(d.name, "session-aaaaaaaa")
        self.assertNotIn(SID, km._task_join_miss)


class LastStateMemo(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        (Path(self.td) / "states").mkdir()
        self.p = Path(self.td) / "states" / (SID + ".jsonl")
        sb._LAST_STATE_MEMO.clear()

    def test_the_tail_is_read_once_per_file_version(self):
        self.p.write_text('{"state": "working", "t": 1}\n{"state": "waiting", "t": 2}\n')
        reads = []
        real = sb._lines_from_end
        sb._lines_from_end = lambda p, block=65536: (reads.append(str(p)), real(p, block))[1]
        try:
            a = sb.last_state(Path(self.td), SID)
            b = sb.last_state(Path(self.td), SID)
            self.assertEqual(a, {"state": "waiting", "t": 2}); self.assertEqual(a, b)
            self.assertEqual(len(reads), 1, "the second read is a stat")
            a["state"] = "mutated"; b["state"] = "mutated too"       # the build-path result AND the hit-path one
            self.assertEqual(sb.last_state(Path(self.td), SID)["state"], "waiting", "callers get copies on both paths")
            with open(self.p, "a") as fh:
                fh.write('{"state": "working", "t": 3}\n')
            self.assertEqual(sb.last_state(Path(self.td), SID)["t"], 3, "an appended record is read")
            self.assertEqual(len(reads), 2)
        finally:
            sb._lines_from_end = real

    def test_a_missing_file_reads_empty_and_forgets(self):
        self.p.write_text('{"state": "waiting", "t": 2}\n')
        sb.last_state(Path(self.td), SID)
        self.p.unlink()
        self.assertEqual(sb.last_state(Path(self.td), SID), {})
        self.assertNotIn(str(self.p), sb._LAST_STATE_MEMO)


class WorkingNotesMemo(unittest.TestCase):
    def setUp(self):
        km._working_notes_memo[0] = None
        km.WORKING_DIR.mkdir(parents=True, exist_ok=True)
        for f in km.WORKING_DIR.iterdir():
            f.unlink()

    def test_read_once_per_directory_version(self):
        (km.WORKING_DIR / SID).write_text("editing the notes API\n")
        real = Path.read_text
        reads = []
        with mock.patch.object(Path, "read_text", lambda self, *a, **k: (reads.append(str(self)), real(self, *a, **k))[1]):
            a = km._working_notes(); b = km._working_notes()
            self.assertEqual(a, {SID: "editing the notes API"}); self.assertEqual(a, b)
            self.assertEqual(len(reads), 1)
            a[SID] = "mutated"; b.pop(SID)                           # the build-path result AND the hit-path one
            self.assertEqual(km._working_notes(), {SID: "editing the notes API"}, "callers get copies on both paths")
            (km.WORKING_DIR / SID).write_text("done, idle\n")
            st = os.stat(km.WORKING_DIR / SID); os.utime(km.WORKING_DIR / SID, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
            self.assertEqual(km._working_notes()[SID], "done, idle")
            (km.WORKING_DIR / SID).unlink()
            self.assertEqual(km._working_notes(), {}, "a removed note is gone at once")

    def test_a_note_that_vanishes_between_readdir_and_stat_costs_only_itself(self):
        # one try around the whole listing returned {} for the call, and every live session read as owning
        # nothing (which the postal contract takes as free ownership); the vanished entry alone is skipped
        gone = "11111111-2222-3333-4444-666666666666"
        (km.WORKING_DIR / SID).write_text("editing the notes API\n")
        (km.WORKING_DIR / gone).write_text("unlinked by a clear before the stat\n")
        real = os.scandir

        class Vanished:
            def __init__(self, e):
                self.name, self.path = e.name, e.path

            def stat(self, *a, **k):
                raise FileNotFoundError(self.path)

        @contextlib.contextmanager
        def scan(p):
            with real(p) as it:
                yield (Vanished(e) if e.name == gone else e for e in it)

        with mock.patch.object(km.os, "scandir", scan):
            self.assertEqual(km._working_notes(), {SID: "editing the notes API"}, "the other note is still returned")


class FoldEvictionKeepsThreads(unittest.TestCase):
    """_push evicts every fold entry whose sid is not a shown tab, EXCEPT the comment threads the previous
    cycle's comments loop built: evicting those made every thread build a cold reshape. Pinned at the source
    and driven for real (the test_chat_empty_build_guard.py harness: one chat client, one tab, the builders
    and the sends stubbed)."""

    PSID = "11111111-2222-3333-4444-777777777777"           # the one shown tab
    TSID = "66666666-7777-8888-9999-aaaaaaaaaaaa"           # a thread: never a tab

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.td.name, self.PSID + ".jsonl")
        with open(self.path, "w") as f:
            f.write("{}\n")                                 # stat-able: the build cache keys on it
        self.sess = {"sid": self.PSID, "name": "web", "anchor": None, "path": self.path, "mtime": 0}
        self.live = {self.PSID: {"state": "waiting", "color": "#888888", "since": 0, "model": "", "effort": "",
                                 "context": None, "backend": "sdk"}}
        self.client = {"app": "chat", "alive": True, "sent": {}, "send": lambda s: None}
        self._saved_keep = [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])]
        km._thread_fold_keep[0], km._thread_fold_keep[1] = set(), set()
        self._forget()

    def tearDown(self):
        km._thread_fold_keep[0], km._thread_fold_keep[1] = self._saved_keep
        self._forget()
        self.td.cleanup()

    def _forget(self):
        with km._chat_fold_lock:
            km._chat_fold.pop(self.TSID, None)
        for d in (km._built_chat, km._prev_chat_events, km._prev_chat_ledger):
            d.pop(self.PSID, None)

    def _cycle(self):
        frame = {"type": "session", "id": self.PSID, "name": "web", "color": None, "status": {"state": "ready"},
                 "ledger": None, "events": [{"kind": "assistant", "uuid": "11111111-2222-3333-4444-0000000000a1", "md": "hi"}]}
        with mock.patch.object(km, "_alive_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_chat_tab_sessions", lambda now, tm: [dict(self.sess)]), \
                mock.patch.object(km, "_warm_fleet_bg", lambda now: None), \
                mock.patch.object(km, "_live_map", lambda: dict(self.live)), \
                mock.patch.object(km, "build_session", lambda sid, now, live_map=None, **kw: dict(frame)), \
                mock.patch.object(km, "_cached_feed", lambda *a, **k: {"working": [], "awaiting": [], "asks": []}), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, sig=None, kind="full": None):
            km._push([self.client], live_map=self.live)

    def _held(self):
        with km._chat_fold_lock:
            return self.TSID in km._chat_fold

    def test_a_thread_built_last_cycle_keeps_its_fold_prefix_and_ages_out_after_one_idle_cycle(self):
        km._chat_fold_put(self.TSID, {"probe": 1})
        km._thread_fold_keep[1].add(self.TSID)              # what a thread build does inside a cycle's comments loop
        self._cycle()
        self.assertTrue(self._held(), "the thread the previous cycle built keeps its prefix")
        km._thread_fold_keep[1].add(self.TSID)              # built again this cycle
        self._cycle()
        self.assertTrue(self._held(), "a thread built every cycle is never evicted")
        self._cycle()                                       # nothing rebuilt it
        self.assertFalse(self._held(), "a thread that stopped being built is evicted after one cycle")

    def test_push_keeps_the_fold_prefixes_of_last_cycles_threads(self):
        import inspect
        src = inspect.getsource(km._push)
        swap = "_thread_fold_keep[0], _thread_fold_keep[1] = _thread_fold_keep[1], set()"
        keep = "keep = shown_sids | _thread_fold_keep[0]"
        self.assertIn(keep, src)
        self.assertIn(swap, src)
        self.assertIn("if sid not in keep:", src, "the eviction tests the keep set, not the shown tabs")
        self.assertLess(src.index(swap), src.index(keep),
                        "the swap runs BEFORE the eviction, so the keep set is last cycle's, not the one before")
        self.assertIn("_thread_fold_keep[1].add(tsid)", inspect.getsource(km._thread_events))


if __name__ == "__main__":
    unittest.main()
