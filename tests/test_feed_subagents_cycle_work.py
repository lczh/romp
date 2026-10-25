#!/usr/bin/env python3
"""Two reads the feed repeated when nothing had changed (2026-10-25).

THE WATCH CERTIFICATE. The walk memo (_SUBAGENT_TREES) served a standing subagents tree for one lstat per known directory,
once per cycle per root, and a workflow-heavy session's tree holds hundreds of workflows/wf_<id>/ directories; each lstat
gives up the interpreter lock, so a busy kernel paid a lock hand-off per directory per cycle for trees that had not
changed in hours. On Linux the memo now watches a tree's directories (inotify) and serves a tree whose directories have
all been watched since before a validation that matched, with no event for any of them since, that validation's stats
for the root's lstat alone. Pinned here, on a temp directory on a local filesystem (skipped where there is none, and off
Linux): (1) RED FIRST: the third sample of a standing tree takes one lstat (the root's), where the base took one per
directory, and answers what a fresh walk answers; (2) an oracle over a scripted sequence of changes (a quiet cycle, an
agent's transcript appended, a new agent file in a workflow directory, a new workflow directory two levels deep, a
sidecar landing in it, a chmod, a back-dated stamp, a directory moved away and another made in its place with a child of
the same name, a directory removed, the root removed and made again): after every change and every quiet sample the
memo's answer equals a fresh walk's, and a transcript's growth leaves the certificate standing; (3) a change landing
after the arming and before the certificate is stored is never certified (the epoch); (4) a full event queue voids every
certificate; (5) a tree on a filesystem other than a local one is never watched and is validated by lstat as before,
whether the walk or the certificate finds it so; (6) the walk memo's eviction removes a tree's watches; (7) a tree past
the watch budget is validated by lstat as before.

ONE READ OF A SESSION'S BACKGROUND-TASK FACTS PER FEED DERIVATION. A session idle with live background tasks asked
_bg_placed_tops which launches the judge placed from _session_awaiting, _bg_owner_tops, _bg_service_descs and once per
card in the awaiting column (_awaiting_task_descs), and each ask read the session's whole goal store again
(jd.load_goals_shared). Pinned here, over the real build_feed on the notes-api demo world (session web, idle, two
background commands, one placed and one pending, three goals): (8) RED FIRST: one derivation reads web's store twice,
the key's read and the first background ask's, where the base read it once per ask; (9) an oracle over a scripted
sequence (a command finishing, a new one starting, the judge placing a launch, a stamp landing, an agent file landing in
the subagents tree with a live agent, the transcript growing, a workflow directory removed): after every step the
memoized board equals a from-scratch build and a from-scratch build with the derivation memo switched off, byte for byte.

Synthetic fixtures only: private synthetic sids (the goal-store fixture rule), invented agent ids and text, temp
directories."""
import json
import os
import re
import shutil
import struct
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs conftest's
# floor. The root minted here is outside conftest's belt, so session hosts are switched off in it too.
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
km = load_source("romp_kernel_feed_subagents_cycle_work", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# This module's PRIVATE synthetic sids (never the shared placeholder: load_goals replays the per-sid override journal).
WEB = "5c4d3e2f-1a0b-4c98-8765-4321fedcb101"
TREE_SID = "5c4d3e2f-1a0b-4c98-8765-4321fedcb102"
NOW = 1781100000
T0 = NOW - 3600
WORKFLOWS = 24


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def aid_of(n):
    return "a%016x" % n


def _watch_reason():
    """Why the watch tests cannot run here, or None: the certificate is Linux's, and only on a local filesystem."""
    if not km._TREE_WATCH_ON:
        return "no inotify on this platform"
    w = km._SUBAGENT_TREE_WATCH
    with w.lock:
        ok = w._open()
        kind = w._fs_type(os.stat(tempfile.gettempdir()).st_dev) if ok else None
    if not ok:
        return "inotify could not be opened"
    if kind not in km._TREE_WATCH_LOCAL_FS:
        return "the temp directory is on %r, not a local filesystem" % (kind,)
    return None


class _Lstats:
    """Counts os.lstat calls on paths under `root` while open."""

    def __init__(self, root):
        self.root, self.n = str(root), 0

    def __enter__(self):
        real = os.lstat

        def lstat(p, *a, **k):
            s = os.fsdecode(p) if not isinstance(p, int) else ""
            if s == self.root or s.startswith(self.root + os.sep):
                self.n += 1
            return real(p, *a, **k)
        self._p = mock.patch.object(os, "lstat", lstat)
        self._p.start()
        return self

    def __exit__(self, *exc):
        self._p.stop()
        return False


class _Tree(unittest.TestCase):
    """A session's subagents tree on the temp directory: one agent at the top and WORKFLOWS workflow directories with one
    finished agent each. Every directory change is followed by a back-dated stamp unique to it (the tests/
    test_feed_subagent_key.py rule), so a change is never hidden inside one clock tick, and the racy window is closed as the
    walk memo's tests close it."""

    def setUp(self):
        reason = _watch_reason()
        if reason:
            self.skipTest(reason)
        self.td = tempfile.mkdtemp()
        self.root = Path(self.td) / "projects" / "-tmp-notes-api" / TREE_SID / "subagents"
        self.wfs = self.root / "workflows"
        self.wfs.mkdir(parents=True)
        self._stamp = time.time_ns() - 500 * 1_000_000_000
        self._n = 0x500
        self._agent(self.root)
        for i in range(WORKFLOWS):
            self._agent(self.wfs / ("wf_%016x" % (0x1000 + i)))
        self._bump(self.wfs)
        self._bump(self.root)
        self._racy = mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0)
        self._racy.start()

    def tearDown(self):
        self._racy.stop()
        km._SUBAGENT_TREES.pop(str(self.root), None)
        km._SUBAGENT_TREE_WATCH.drop(str(self.root))
        shutil.rmtree(self.td, ignore_errors=True)

    def _bump(self, d):
        self._stamp += 1_000_000_000
        os.utime(str(d), ns=(self._stamp, self._stamp))

    def _agent(self, where, meta=True):
        self._n += 1
        aid = aid_of(self._n)
        d = Path(where)
        if not d.exists():
            d.mkdir(parents=True)
            self._bump(d.parent)
        (d / ("agent-%s.jsonl" % aid)).write_text(json.dumps({"type": "user", "message": {"content": "audit"}}) + "\n")
        if meta:
            (d / ("agent-%s.meta.json" % aid)).write_text(json.dumps({"agentType": "general-purpose",
                                                                       "toolUseId": "toolu_" + aid[1:]}))
        self._bump(d)
        return aid

    def _reference(self):
        """What a fresh walk answers: the directories in os.walk's top-down sorted order and each one's identity."""
        out = []
        for r, ds, _fs in os.walk(str(self.root)):
            ds.sort()
            out.append(r)
        return out, [km._stat_ident(os.lstat(r)) for r in out]

    def _sample(self):
        dirs, stats = km._subagent_tree(str(self.root))
        return list(dirs), [km._stat_ident(s) for s in stats]

    def _stats(self):
        return km._subagent_tree_memo_report()


class AStandingTreeIsServedOnItsCertificate(_Tree):
    def test_the_third_sample_takes_the_roots_lstat_alone(self):
        """RED FIRST: on the base every sample of a standing tree took one lstat per directory (26 here)."""
        self._sample()                                   # the walk
        self._sample()                                   # a validation: the watches are armed before it, then certified
        before = self._stats()
        with _Lstats(self.root) as c:
            got = self._sample()
        after = self._stats()
        self.assertEqual(got, self._reference(), "the answer a fresh walk gives")
        self.assertEqual(c.n, 1, "one lstat, the root's, for %d directories" % len(got[0]))
        self.assertEqual((after["watched"] - before["watched"], after["hit"] - before["hit"]), (1, 1))
        self.assertEqual(after["dirStats"], before["dirStats"], "no directory beyond the root was stat'd")
        self.assertGreaterEqual(after["watches"], len(got[0]))
        self.assertGreaterEqual(after["watchCerts"], 1)

    def test_the_served_stats_are_one_object_while_the_tree_stands(self):
        """A reader memoizing on the stats list itself (the agent-file name index) hits on the same object."""
        self._sample()
        self._sample()
        a = km._subagent_tree(str(self.root))[1]
        b = km._subagent_tree(str(self.root))[1]
        self.assertIs(a, b)


class TheMemoAnswersWhatAFreshWalkAnswers(_Tree):
    """The oracle: a scripted sequence of changes; after each, three samples (the one that sees the change, the one that
    certifies again, and one served on the certificate), each equal to a fresh walk."""

    def _check(self, label):
        for i in range(3):
            self.assertEqual(self._sample(), self._reference(), "%s, sample %d" % (label, i + 1))

    def test_a_scripted_sequence_of_changes(self):
        wf = self.wfs / ("wf_%016x" % 0x1003)
        self._check("the start")
        base = self._stats()
        self._check("a quiet cycle")
        self.assertGreaterEqual(self._stats()["watched"] - base["watched"], 3, "a quiet tree is served on its certificate")
        # an agent's transcript grows: no directory's identity moves, and the certificate stands
        apath = next(wf.glob("agent-*.jsonl"))
        before = self._stats()
        with open(apath, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": "step two of the audit"}}) + "\n")
        self._check("a transcript appended")
        after = self._stats()
        self.assertEqual(after["watched"] - before["watched"], 3, "an append is no event: every sample served")
        self.assertEqual(after["miss"], before["miss"])
        # a new agent's file in an existing workflow directory
        self._agent(wf)
        self._check("a new agent file")
        # a new workflow directory, with a nested directory under it, and a sidecar landing there late
        deep = self.wfs / ("wf_%016x" % 0x2000) / "inner"
        aid = self._agent(deep, meta=False)
        self._bump(self.wfs / ("wf_%016x" % 0x2000))
        self._check("a new workflow directory two levels deep")
        (deep / ("agent-%s.meta.json" % aid)).write_text(json.dumps({"agentType": "Explore", "toolUseId": "toolu_x"}))
        self._bump(deep)
        self._check("a sidecar landing in it")
        # a chmod (ctime alone moves) and a back-dated stamp
        os.chmod(str(wf), 0o700)
        self._check("a chmod")
        self._bump(wf)
        self._check("a back-dated stamp")
        # a directory moved away and another made in its place, with a child of the same name, before any sample: the
        # paths the watches were added under now hold other inodes, and the moved inodes keep their watches
        moved = self.wfs / ("wf_%016x" % 0x1005)
        (moved / "inner").mkdir()
        self._bump(moved / "inner")
        self._bump(moved)
        self._check("a nested directory under the one that moves")
        os.rename(str(moved), str(self.wfs / ("wf_%016x" % 0x3000)))
        (moved / "inner").mkdir(parents=True)
        self._bump(moved / "inner")
        self._bump(moved)
        self._bump(self.wfs)
        self._check("moved away, another in its place")
        self._agent(moved / "inner")                     # lands in the NEW inner: the watch must be on it
        self._check("an agent landing in the new child")
        self._agent(self.wfs / ("wf_%016x" % 0x3000) / "inner")   # ...and one in the moved one, under its new path
        self._check("an agent landing in the moved child")
        # the same move with a sample between the move and the new directory
        moved2 = self.wfs / ("wf_%016x" % 0x1006)
        (moved2 / "inner").mkdir()
        self._bump(moved2 / "inner")
        self._bump(moved2)
        self._check("a nested directory under the second one that moves")
        os.rename(str(moved2), str(self.wfs / ("wf_%016x" % 0x3001)))
        self._bump(self.wfs)
        self._check("the second move")
        (moved2 / "inner").mkdir(parents=True)
        self._bump(moved2 / "inner")
        self._bump(moved2)
        self._bump(self.wfs)
        self._check("another directory in the second one's place")
        self._agent(moved2 / "inner")
        self._check("an agent landing in that new child")
        # a directory removed
        shutil.rmtree(str(self.wfs / ("wf_%016x" % 0x1007)))
        self._bump(self.wfs)
        self._check("a directory removed")
        # the root removed, then made again
        shutil.rmtree(str(self.root))
        self.assertEqual(km._subagent_tree(str(self.root)), ((), ()))
        self.assertNotIn(str(self.root), km._SUBAGENT_TREE_WATCH.roots, "a root gone leaves the watch tables")
        self.root.mkdir(parents=True)
        self._agent(self.root)
        self._bump(self.root)
        self._check("the root made again")

    def test_a_change_after_the_arming_and_before_the_certificate_is_stored_is_never_certified(self):
        """The epoch: the validation's lstat of the first workflow directory precedes a file landing there, so the
        validation matches; the event queued for it must keep the certificate from being stored. Stored anyway, the next
        sample would be served the stats from before the change."""
        self._sample()                                   # the walk
        first = str(self.wfs / ("wf_%016x" % 0x1000))
        last = str(self.wfs / ("wf_%016x" % (0x1000 + WORKFLOWS - 1)))
        real, fired = km._lstat_or_none, []

        def lstat(p):
            if p == last and not fired:
                fired.append(1)
                self._agent(first)                       # lands after `first` was stat'd, before the certificate
            return real(p)
        with mock.patch.object(km, "_lstat_or_none", lstat):
            self._sample()                               # the validation: matches, since `first` was read before
        self.assertEqual(fired, [1])
        self.assertEqual(self._sample(), self._reference(), "the next sample sees the agent that landed")
        self._check("after")


class QueueAndBounds(_Tree):
    def _certified(self):
        self._sample()
        self._sample()
        self.assertIn(str(self.root), km._SUBAGENT_TREE_WATCH.certs)

    def test_a_full_queue_voids_every_certificate(self):
        self._certified()
        w = km._SUBAGENT_TREE_WATCH
        real_read, fired = os.read, []

        def read(fd, n):
            if fd == w.fd and not fired:
                fired.append(1)
                return struct.pack("iIII", -1, km._IN_Q_OVERFLOW, 0, 0)
            return real_read(fd, n)
        before = self._stats()
        with mock.patch.object(os, "read", read), _Lstats(self.root) as c:
            got = self._sample()
        self.assertEqual(fired, [1])
        self.assertEqual(got, self._reference())
        self.assertEqual(c.n, len(got[0]), "validated by lstat: the certificate was void")
        self.assertEqual(self._stats()["watchOverflow"] - before["watchOverflow"], 1)

    def test_a_tree_on_another_filesystem_is_never_watched(self):
        """The walk's verdict: a root whose mount is not a local filesystem is never armed, so no watch is added."""
        w = km._SUBAGENT_TREE_WATCH
        real_add, adds = w.add, []

        def add(fd, p, mask):
            adds.append(p)
            return real_add(fd, p, mask)
        with mock.patch.object(w, "_fs_type", lambda dev: "nfs"), mock.patch.object(w, "add", add):
            self._sample()
            self._sample()
            with _Lstats(self.root) as c:
                got = self._sample()
        self.assertEqual(got, self._reference())
        self.assertEqual(c.n, len(got[0]), "one lstat per directory, as before the watches")
        self.assertEqual(adds, [], "not one watch added on a network mount")
        self.assertNotIn(str(self.root), w.roots)
        self.assertNotIn(str(self.root), w.certs)

    def test_a_tree_found_off_a_local_filesystem_at_the_certificate_is_not_certified(self):
        """The certificate's own check: a tree whose mount reads as a network one by the time its validation matched (the
        walk's verdict said local) is dropped from the watches and validated by lstat from then on."""
        w = km._SUBAGENT_TREE_WATCH
        kind = ["ext4"]
        with mock.patch.object(w, "_fs_type", lambda dev: kind[0]):
            self._sample()                               # the walk: local
            kind[0] = "nfs"
            self._sample()                               # the validation: armed, matched, refused at the certificate
            with _Lstats(self.root) as c:
                got = self._sample()
        self.assertEqual(got, self._reference())
        self.assertEqual(c.n, len(got[0]), "validated by lstat: no certificate")
        self.assertNotIn(str(self.root), w.roots, "its watches were removed")
        self.assertNotIn(str(self.root), w.certs)

    def test_the_walk_memos_eviction_removes_the_trees_watches(self):
        self._certified()
        held = len(km._SUBAGENT_TREE_WATCH.roots[str(self.root)])
        before = self._stats()["watches"]
        km._subagent_trees_forget([])                    # nobody alive owns the root
        self.assertNotIn(str(self.root), km._SUBAGENT_TREE_WATCH.roots)
        self.assertNotIn(str(self.root), km._SUBAGENT_TREE_WATCH.certs)
        self.assertGreaterEqual(before - self._stats()["watches"], held, "its directories are no longer watched")

    def test_a_tree_past_the_watch_budget_is_validated_by_lstat(self):
        w = km._SUBAGENT_TREE_WATCH
        with mock.patch.object(w, "cap", len(w.wds) + 3):
            before = self._stats()["watchFail"]
            self._sample()
            self._sample()
            with _Lstats(self.root) as c:
                got = self._sample()
        self.assertEqual(got, self._reference())
        self.assertEqual(c.n, len(got[0]))
        self.assertGreaterEqual(self._stats()["watchFail"] - before, 1)


# ── the feed derivation's background-task reads ──

def _dump(feed):
    """The payload's serialization with the declared clock fields stripped: the dedup's comparison."""
    return json.dumps({k: v for k, v in feed.items() if k not in km._DEDUP_VOLATILE}, sort_keys=True, default=str)


def _reset_memo():
    with km._FEED_SEG_LOCK:
        km._FEED_SEG_HELD.clear()
    with km._feed_memo_lock:
        km._feed_memo.clear()
        st = km._FEED_MEMO_STATS
        for k in ("hit", "miss", "evict", "derived", "entries", "bytes", "failed", "coldLive", "coldFlip"):
            st[k] = 0
        for k in st["miss_by"]:
            st["miss_by"][k] = 0
        for k in st["row_by"]:
            st["row_by"][k] = 0
        km._FEED_DERIVE_FAILED.clear()


def _memo_snapshot():
    with km._feed_memo_lock:
        snap = (dict(km._feed_memo), json.loads(json.dumps(km._FEED_MEMO_STATS)), dict(km._FEED_DERIVE_FAILED))
    with km._FEED_SEG_LOCK:
        snap += (dict(km._FEED_SEG_HELD), dict(km._FEED_SEG_STATS))
    return snap


def _memo_restore(snap):
    entries, stats, failed, held, seg = snap
    with km._FEED_SEG_LOCK:
        km._FEED_SEG_HELD.clear()
        km._FEED_SEG_HELD.update(held)
        km._FEED_SEG_STATS.clear()
        km._FEED_SEG_STATS.update(seg)
    with km._feed_memo_lock:
        km._feed_memo.clear()
        km._feed_memo.update(entries)
        km._FEED_MEMO_STATS.clear()
        km._FEED_MEMO_STATS.update(stats)
        km._FEED_DERIVE_FAILED.clear()
        km._FEED_DERIVE_FAILED.update(failed)


GOALS = ("wire the notes-api web client", "add the notes-api list endpoint", "cover the notes-api list endpoint")
CMD = {"t1": "run the list tests", "t2": "serve the docs site", "t3": "lint the web client"}


class _Board(unittest.TestCase):
    """The notes-api demo world with one session, web: idle with a warm parse, three goals, two background commands
    (t1 launched under the first prompt and not placed by the judge yet, t2 launched under the second and placed under the
    second goal), and a subagents tree of a few workflow directories."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "session-hosts").write_text("off\n")
        proj = td / "projects"
        names = td / "names"
        names.mkdir()
        cdir = td / "launch-web"
        cdir.mkdir()
        pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (WEB + ".jsonl")
        self.recs = [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "start on: " + GOALS[0]}},
            self._launch("t1", "a1", "u1", T0 + 10),
            self._ack("t1", "r1", "a1", T0 + 11),
            {"type": "user", "timestamp": iso(T0 + 100), "uuid": "u2", "parentUuid": "r1", "promptSource": "typed",
             "message": {"role": "user", "content": "and serve the docs while you " + GOALS[1]}},
            self._launch("t2", "a2", "u2", T0 + 110),
            self._ack("t2", "r2", "a2", T0 + 111),
            {"type": "assistant", "timestamp": iso(T0 + 140), "uuid": "a3", "parentUuid": "r2",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "Both are running."}],
                         "stop_reason": "end_turn"}},
        ]
        self._write()
        (names / WEB).write_text("web\t%s\t#1EA1EB\n" % cdir)
        self.root = Path(km._subagents_dir(self.tpath))
        self._stamp = time.time_ns() - 500 * 1_000_000_000
        for i in range(4):
            d = self.root / "workflows" / ("wf_%016x" % (0x4000 + i))
            d.mkdir(parents=True)
            (d / ("agent-%s.jsonl" % aid_of(0x700 + i))).write_text("")
            self._bump(d)
        self._bump(self.root / "workflows")
        self._bump(self.root)
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD)
        self.saved_memo = _memo_snapshot()
        jd._rebind_state(td)
        jd.PROJECTS = proj
        km.NAMES = jd.NAMES
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        self._racy = mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0)
        self._racy.start()
        km._live_scope.names = None
        km._live_scope.subagent_trees = None
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._PLACEMENT_IDX.clear()
        s = {"rompUuid": WEB, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}
        for i, text in enumerate(GOALS):
            jd.apply_plan(s, "s%d" % (i + 1), T0 + i, [{"do": "mint", "why": "a request", "text": text}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(WEB, s)
        self.tops = sorted(n for n, nd in s["nodes"].items() if nd.get("parentId") is None)
        self.assertEqual(len(self.tops), 3, "precondition: three goals")
        ps = km._parse(str(self.tpath), WEB, NOW)
        self.segs = km._seg_of_tool_uses(ps, s, ["t1", "t2"])
        self.assertEqual(set(self.segs), {"t1", "t2"}, "precondition: each launch resolves to its segment")
        km._task_seg_cache.clear()
        self._place("t2", self.tops[1])
        self.live = {WEB: {"state": "idle", "since": NOW - 100, "model": "", "effort": "", "context": None,
                           "compactPct": None, "color": None,
                           "bgTasks": [self._task("t1", NOW - 3000), self._task("t2", NOW - 2900)]}}
        km._live_scope.snapshot = self.live
        km._parse(str(self.tpath), WEB, NOW)
        jd._discover_cache.clear()
        km.build_feed(NOW - 1, self.live)        # the first sight adopts the session into the persisted order
        _reset_memo()

    def tearDown(self):
        self._racy.stop()
        _memo_restore(self.saved_memo)
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD = self.saved[1:]
        km._live_scope.names = None
        km._live_scope.snapshot = None
        km._live_scope.subagent_trees = None
        km._SUBAGENT_TREES.pop(str(self.root), None)
        km._SUBAGENT_TREE_WATCH.drop(str(self.root))
        km._task_seg_cache.clear()
        km._BG_TOPS_CACHE.clear()
        km._PLACEMENT_IDX.clear()
        jd._discover_cache.clear()
        self.td.cleanup()

    # ── the world's writers ──
    @staticmethod
    def _launch(tid, uuid, parent, t):
        return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "assistant", "content": [
                    {"type": "tool_use", "id": tid, "name": "Bash",
                     "input": {"command": "make %s" % tid, "description": CMD[tid], "run_in_background": True}}]}}

    @staticmethod
    def _ack(tid, uuid, parent, t):
        return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                         "content": "Command running in background"}]}}

    @staticmethod
    def _task(tid, since):
        return {"toolUseId": tid, "type": "local_bash", "desc": CMD[tid], "since": since}

    def _write(self):
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in self.recs))

    def _bump(self, d):
        self._stamp += 1_000_000_000
        os.utime(str(d), ns=(self._stamp, self._stamp))

    def _place(self, tid, top):
        s = jd.load_goals(WEB)
        s["placements"][self.segs[tid]] = top
        jd.save_goals(WEB, s)

    def _stamp_top(self, top):
        s = jd.load_goals(WEB)
        self.assertTrue(jd.record_verdict(s, s["nodes"][top], "closer", "awaiting", NOW - 50,
                                          why="waiting on the docs site", await_kind="task"))
        jd.save_goals(WEB, s)

    def _set_tasks(self, tids):
        self.live[WEB] = dict(self.live[WEB], bgTasks=[self._task(t, NOW - 3000 + i) for i, t in enumerate(tids)])

    # ── the reads ──
    def _build(self):
        km._live_scope.snapshot = self.live
        km._live_scope.subagent_trees = {}
        try:
            return km.build_feed(NOW, self.live)
        finally:
            km._live_scope.subagent_trees = None

    def _scratch(self, memo=True):
        snap = _memo_snapshot()
        _reset_memo()
        try:
            if memo:
                return self._build()
            with mock.patch.object(km, "_feed_bg_memo", lambda: None):
                return self._build()
        finally:
            _memo_restore(snap)

    def _cards(self, feed):
        return [c for c in feed["asks"] if c.get("sid") == WEB]


class OneDerivationReadsTheStoreOnce(_Board):
    def test_a_derivation_reads_the_store_for_the_key_and_the_first_background_ask_alone(self):
        """RED FIRST: on the base each background ask of the derivation read web's whole store again: _session_awaiting's,
        _bg_owner_tops', _bg_service_descs' and one per card in the awaiting column (three here), seven reads in all."""
        km._session_stamp_read(WEB)                      # the stamp view's own stat-keyed cache, warm (it reads the store once)
        calls, real = [], jd.load_goals_shared

        def spy(fsid):
            calls.append(fsid)
            return real(fsid)
        with mock.patch.object(jd, "load_goals_shared", spy):
            f = self._build()
        cards = self._cards(f)
        self.assertEqual(len(cards), 3)
        self.assertTrue(all(isinstance(c.get("awaiting"), dict) and c["awaiting"].get("tasks") == [CMD["t1"]]
                            for c in cards), "every goal awaits the pending command: %r" % [c.get("awaiting") for c in cards])
        self.assertEqual((f.get("bgServices") or {}).get("web"), [CMD["t2"]],
                         "the placed command is the session's service chip")
        self.assertEqual(calls.count(WEB), 2, "the key's read and the first background ask's: %r" % calls)
        self.assertEqual(_dump(f), _dump(self._scratch(memo=False)), "the board the base built")


class TheBoardEqualsTheUnmemoizedBuild(_Board):
    def _check(self, label):
        f = self._build()
        self.assertEqual(_dump(f), _dump(self._scratch()), "%s: the memoized board differs from a from-scratch build" % label)
        self.assertEqual(_dump(f), _dump(self._scratch(memo=False)),
                         "%s: the board differs from one built without the derivation memo" % label)
        return f

    def test_a_scripted_sequence_of_changes(self):
        f = self._check("the start")
        self.assertTrue(self._cards(f))
        self._set_tasks(["t2"])                          # t1 finished
        self._check("a command finished")
        self._set_tasks(["t2", "t3"])                    # a new command, its launch nowhere the judge placed
        self._check("a new command")
        self._set_tasks(["t1", "t2", "t3"])
        self._place("t1", self.tops[0])                  # the judge places t1's launch under the first goal
        self._check("the judge placed a launch")
        self._stamp_top(self.tops[1])                    # the closer affirms the wait on the second goal: t2 is awaited
        self._check("a stamp landed")
        aid = aid_of(0x7ff)                              # an agent's file lands in a workflow directory, the agent live
        d = self.root / "workflows" / ("wf_%016x" % 0x4001)
        (d / ("agent-%s.jsonl" % aid)).write_text("")
        (d / ("agent-%s.meta.json" % aid)).write_text(json.dumps({"agentType": "general-purpose",
                                                                  "toolUseId": "toolu_" + aid[1:]}))
        self._bump(d)
        self.live[WEB] = dict(self.live[WEB], subagents=[{"type": "general-purpose", "since": NOW - 40, "agentId": aid},
                                                         {"type": "Explore", "since": NOW - 30, "agentId": aid_of(0x700)}])
        self._check("an agent file landed with live agents")
        self.recs.append({"type": "assistant", "timestamp": iso(T0 + 200), "uuid": "a4", "parentUuid": "a3",
                          "message": {"role": "assistant", "content": [{"type": "text", "text": "Still waiting."}],
                                      "stop_reason": "end_turn"}})
        self._write()
        km._parse(str(self.tpath), WEB, NOW)
        self._check("the transcript grew")
        shutil.rmtree(str(self.root / "workflows" / ("wf_%016x" % 0x4002)))
        self._bump(self.root / "workflows")
        self._check("a workflow directory removed")
        self._set_tasks([])
        self.live[WEB] = dict(self.live[WEB], subagents=[])
        self._check("everything finished")


if __name__ == "__main__":
    unittest.main()
