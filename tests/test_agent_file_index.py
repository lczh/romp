#!/usr/bin/env python3
"""The agent-file lookup's name index (2026-10-22): _subagent_file answers through per-directory listings of the
agent-<id>.jsonl files, held while each directory's identity stands, and holds an answer for as long as the directories
that could change it for THAT name stand.

The pusher's history warm renders pages of old history, and every Agent card on a page asks where its agent's file is.
The lookup used to test every directory of the session's subagents tree with isfile (and, for an agent whose file sits
under the fsid the session was resumed from, every sibling fsid's tree in the project), and its memo was keyed on the
stamp of every directory that walk read, re-taken with a stat each on every hit; a workflow agent landing anywhere in
the project moved one of those stamps, so on a working box each card walked again every cycle, and the memo was emptied
outright past 1,024 answers. The history warm spent most of its time there.

Pinned here: (1) an oracle, a brute-force walk written the way the old lookup read the disk, agrees with _subagent_file
for every agent id after every step of a seeded run of changes to a synthetic project (agents landing flat, nested and
in sibling fsids, leaving, an fsid directory renamed as a /clear fork does, the own tree removed and made again, symlinks
and a directory where a file should be), outside any scope, inside per-cycle scopes, and with the racy window closed so
the held listings are what answers; (2) a miss reports to a running chat build exactly what the walk reported; (3) a
nested agent's lookup tests only the directory that lists it; (4) inside one cycle a repeated lookup takes no stat and
lists nothing; (5) an agent landing elsewhere in the project leaves a sibling-held answer standing, untested; (6) the
memo holds past 1,024 answers, and a listing inside the racy window is taken again at the next sample; (7) a history page rendered through the held answers equals one rendered with every memo
emptied, before and after the tree changes; (8) a path token named in several messages of one build pass is resolved
once; (9) the name index is bounded by the alive sessions' project directories. Red on stock: (3) to (6) and (8).
Synthetic fixtures only: placeholder ids, the notes-api demo world, a temp directory."""
import json
import os
import random
import shutil
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the load (the tests/test_subagent_tree_memo.py preamble); the root minted here is outside
# conftest's belt, so session hosts are switched off in it too.
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_agent_file_index", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID = "11111111-2222-3333-4444-555555555555"       # web: the session whose cards ask
FORK = "11111111-2222-3333-4444-5555555555f0"      # the fsid web was resumed from
NOW = 1781400000
STEPS = 36                                         # changes per oracle run, each followed by a full round of lookups
AGENT_RECORDS = "".join(json.dumps(r) + "\n" for r in (     # an agent's own transcript: one prompt, one call, one reply
    {"type": "user", "uuid": "s1", "parentUuid": None, "timestamp": "2026-06-11T00:00:00.000Z", "isSidechain": True,
     "message": {"role": "user", "content": "review the api module"}},
    {"type": "assistant", "uuid": "s2", "parentUuid": "s1", "timestamp": "2026-06-11T00:00:02.000Z", "isSidechain": True,
     "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "toolu_s2", "name": "Read",
                                                   "input": {"file_path": "api/notes.py"}}]}},
    {"type": "assistant", "uuid": "s3", "parentUuid": "s2", "timestamp": "2026-06-11T00:00:09.000Z", "isSidechain": True,
     "message": {"role": "assistant", "content": [{"type": "text", "text": "The api module reads cleanly."}]}}))


def _forget():
    """Every memo the lookup reads, emptied (a kernel restart's view)."""
    km._SUBAGENT_FILE_CACHE.clear()
    km._SUBAGENT_TREES.clear()
    km._SUBAGENT_META_CACHE.clear()
    for name in ("_AGENT_TREE_NAMES", "_AGENT_PROJECT_KIDS"):
        getattr(km, name, {}).clear()


# ── the oracle: the old walk's reading of the disk, with no memo at all ──────────────────────────────────────────────
def _walk(root):
    """The subagents tree at `root` in top-down sorted order, no symlink followed, a directory that cannot be listed
    kept (the walk memo's documented shape); [] when `root` is not a real directory."""
    try:
        st = os.lstat(root)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode):
        return []
    out, stack = [], [root]
    while stack:
        cur = stack.pop()
        out.append(cur)
        try:
            with os.scandir(cur) as it:
                subs = sorted(e.name for e in it if e.is_dir(follow_symlinks=False))
        except OSError:
            subs = []
        stack.extend(os.path.join(cur, n) for n in reversed(subs))
    return out


def _find(root, name):
    real = os.path.realpath(root)
    for d in _walk(root):
        c = os.path.join(d, name)
        if os.path.isfile(c) and not os.path.islink(c) and os.path.realpath(c).startswith(real + os.sep):
            return Path(c)
    return None


def _stat_key(p):
    try:
        st = os.stat(p)
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


def oracle(path, aid, notes=None):
    """Where agent `aid`'s file is for the transcript at `path`, read fresh: the own tree in walk order, then each
    directory of the project in name order (its subagents tree, the own one skipped). `notes` collects what a miss
    reports to a chat build, as the walk reported it."""
    name = "agent-%s.jsonl" % aid
    own = Path(path).with_suffix("") / "subagents"
    hit = _find(str(own), name)
    if hit is not None:
        return hit
    if notes is not None:
        notes.append((str(own / name), None))
    try:
        for d in sorted(Path(path).parent.iterdir()):
            if d.is_dir():
                sd = d / "subagents"
                if notes is not None:
                    notes.append((str(sd), _stat_key(str(sd))))
                if sd != own:
                    hit = _find(str(sd), name)
                    if hit is not None:
                        return hit
    except OSError:
        pass
    return None


class _Project(unittest.TestCase):
    """A notes-api project directory: web's transcript and its subagents tree, the fsid web was resumed from, and a
    handful of departed sessions' trees; every directory change stamped with a fresh, unique mtime (`touch`), so the
    held listings can be vouched for with the racy window closed."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.proj = Path(self.td) / "projects" / "-notes-api"
        self.proj.mkdir(parents=True)
        self.tpath = self.proj / (SID + ".jsonl")
        self.tpath.write_text("")
        self.own = self.tpath.with_suffix("") / "subagents"
        self.clock = time.time_ns() - 3600 * 10**9      # a fake clock an hour back: every stamp it hands out is unique
        km._chat_dep_scope.deps = None
        km._live_scope.subagent_trees = None
        _forget()

    def tearDown(self):
        km._chat_dep_scope.deps = None
        km._live_scope.subagent_trees = None
        _forget()
        for r, ds, _fs in os.walk(self.td):
            for d in ds:
                try:
                    os.chmod(os.path.join(r, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.td, ignore_errors=True)

    def touch(self, *dirs):
        for d in dirs:
            if os.path.isdir(d) and not os.path.islink(d):
                self.clock += 10**9
                os.utime(d, ns=(self.clock, self.clock))

    def put(self, d, aid):
        """Land agent `aid`'s file (and sidecar) in directory `d`, making the directories down to it; stamps every
        directory whose entries changed."""
        d = Path(d)
        made = []
        p = d
        while not p.exists():
            made.append(p)
            p = p.parent
        d.mkdir(parents=True, exist_ok=True)
        (d / ("agent-%s.jsonl" % aid)).write_text(AGENT_RECORDS)
        (d / ("agent-%s.meta.json" % aid)).write_text(json.dumps({"toolUseId": "toolu_" + aid}))
        self.touch(*([d] + [m.parent for m in made]))
        return d / ("agent-%s.jsonl" % aid)

    def ask_all(self, aids, scoped):
        """Every id through _subagent_file, against the oracle; one cycle scope around the round when `scoped`."""
        if scoped:
            km._live_scope.subagent_trees = {}
        try:
            got = {a: km._subagent_file(str(self.tpath), a) for a in aids}
        finally:
            km._live_scope.subagent_trees = None
        want = {a: oracle(str(self.tpath), a) for a in aids}
        return got, want


class OracleAgrees(_Project):
    def _run(self, seed, scoped, racy_closed):
        rnd = random.Random(seed)
        aids = ["a%016x" % rnd.getrandbits(64) for _ in range(28)]
        roots = [self.own, self.proj / FORK / "subagents"] + \
                [self.proj / ("11111111-2222-3333-4444-%012x" % k) / "subagents" for k in range(4)]
        wfs = {r: [] for r in roots}

        def place(a):
            r = rnd.choice(roots)
            if rnd.random() < 0.3:
                return self.put(r, a)
            if not wfs[r] or rnd.random() < 0.4:
                wfs[r].append(r / "workflows" / ("wf_%016x" % rnd.getrandbits(64)))
            return self.put(rnd.choice(wfs[r]), a)
        for a in aids[:14]:
            place(a)
        # the shapes the lookup refuses: a symlinked workflow directory, a symlinked agent file, a directory by an agent
        # file's name, a plain file where an fsid's subagents directory would be, a transcript file beside them
        outside = Path(self.td) / "outside"
        self.put(outside, aids[24])
        (self.own / "workflows").mkdir(parents=True, exist_ok=True)
        os.symlink(str(outside), str(self.own / "workflows" / "wf_link"))
        os.symlink(str(outside / ("agent-%s.jsonl" % aids[24])), str(self.own / ("agent-%s.jsonl" % aids[25])))
        (self.own / ("agent-%s.jsonl" % aids[26])).mkdir()
        (self.proj / "11111111-2222-3333-4444-00000000ffff").mkdir()
        (self.proj / "11111111-2222-3333-4444-00000000ffff" / "subagents").write_text("")
        (self.proj / (FORK + ".jsonl")).write_text("")
        self.touch(self.own, self.own / "workflows", self.proj, self.proj / "11111111-2222-3333-4444-00000000ffff")
        seen = set()
        for step in range(STEPS):
            got, want = self.ask_all(aids, scoped)
            self.assertEqual(got, want, "seed %d step %d (scoped=%s, racy closed=%s)" % (seed, step, scoped, racy_closed))
            for p in got.values():
                seen.add("none" if p is None else "own" if str(p).startswith(str(self.own) + os.sep) else "sibling")
            op = rnd.random()
            files = [p for r in roots if r.is_dir() for p in r.rglob("agent-*.jsonl") if p.is_file() and not p.is_symlink()]
            if op < 0.35:
                place(rnd.choice(aids[:24]))
            elif op < 0.55 and files:
                f = rnd.choice(files)
                f.unlink()
                self.touch(f.parent)
            elif op < 0.65:
                # a /clear fork: web's own fsid directory moves to a new name, its agents now a sibling's
                if self.own.parent.is_dir():
                    new = self.proj / ("11111111-2222-3333-4444-%012x" % rnd.getrandbits(40))
                    os.rename(str(self.own.parent), str(new))
                    roots.append(new / "subagents")
                    wfs[new / "subagents"] = [Path(str(w).replace(str(self.own.parent), str(new))) for w in wfs[self.own]]
                    wfs[self.own] = []
                    self.touch(self.proj)
            elif op < 0.72 and files:
                # an agent file replaced by a directory of its name, or by a symlink to another agent's file
                f = rnd.choice(files)
                f.unlink()
                if rnd.random() < 0.5:
                    f.mkdir()
                else:
                    os.symlink(str(rnd.choice(files)), str(f)) if len(files) > 1 else f.mkdir()
                self.touch(f.parent)
            elif op < 0.8:
                # a new departed session's tree appears with an agent already in it
                r = self.proj / ("11111111-2222-3333-4444-%012x" % rnd.getrandbits(40)) / "subagents"
                roots.append(r)
                wfs[r] = []
                self.put(r / "workflows" / ("wf_%016x" % rnd.getrandbits(64)), rnd.choice(aids[:24]))
                self.touch(self.proj)
            else:
                place(rnd.choice(aids[:24]))
        self.assertEqual(seen, {"none", "own", "sibling"}, "the run met every kind of answer")

    def test_the_oracle_agrees_outside_a_scope(self):
        self._run(1, scoped=False, racy_closed=False)

    def test_the_oracle_agrees_across_cycle_scopes(self):
        self._run(4, scoped=True, racy_closed=False)

    def test_the_oracle_agrees_with_the_racy_window_closed_so_held_listings_answer(self):
        """Every listing is vouched as soon as it is taken (the fixture stamps each changed directory with a fresh,
        unique mtime), so an unchanged directory is never listed again: the answers come from the held listings."""
        with mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0):
            self._run(6, scoped=True, racy_closed=True)
            self.tearDown(); self.setUp()
            self._run(16, scoped=False, racy_closed=True)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root lists every directory")
    def test_a_directory_that_cannot_be_listed_is_tested_directly(self):
        """A workflow directory with search but no read permission stays in the tree (the walk memo keeps it) and its
        file is found by testing the name in it, as the walk's isfile did."""
        aid = "a00000000000000c1"
        wf = self.own / "workflows" / "wf_00000000000000c1"
        self.put(wf, aid)
        os.chmod(str(wf), 0o311)
        try:
            self.assertEqual(km._subagent_file(str(self.tpath), aid), oracle(str(self.tpath), aid))
            self.assertEqual(km._subagent_file(str(self.tpath), aid), wf / ("agent-%s.jsonl" % aid))
        finally:
            os.chmod(str(wf), 0o755)


class TheRacyWindow(_Project):
    def test_a_listing_inside_the_racy_window_is_not_held_and_one_past_it_is(self):
        """The walk memo's rule on the listings: a directory stamped within _SUBAGENT_DIR_RACY_NS of its listing could
        still take an entry in the stamp's own tick, which would not move it, so its listing is taken again at the next
        sample; once quiet it is held."""
        aid = "a00000000000000e1"
        wf = self.own / "workflows" / "wf_00000000000000e1"
        self.put(wf, aid)
        os.utime(str(wf), None)                                          # stamped now: inside the window
        self.assertEqual(km._subagent_file(str(self.tpath), aid), wf / ("agent-%s.jsonl" % aid))
        held = km._AGENT_TREE_NAMES[str(self.own)][0]
        self.assertIsNone(held[str(wf)][0], "listed, but not held: taken again at the next sample")
        self.assertEqual(held[str(wf)][1], frozenset({"agent-%s.jsonl" % aid}))
        self.touch(wf)                                                   # an hour back: quiet
        with mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0):        # (ctime moved with the utime; the window closed)
            self.assertEqual(km._subagent_file(str(self.tpath), aid), wf / ("agent-%s.jsonl" % aid))
        self.assertIsNotNone(km._AGENT_TREE_NAMES[str(self.own)][0][str(wf)][0], "past the window: held")


class WhatAMissReports(_Project):
    def test_a_miss_reports_what_the_walk_reported(self):
        """The own file's place, then every directory of the project up to the one that held the file (the own fsid's
        among them), each under its stat key; and the whole project for an agent nowhere."""
        aid, nowhere = "a00000000000000a1", "a00000000000000a2"
        self.put(self.own / "workflows" / "wf_00000000000000b1", "a00000000000000b1")
        self.put(self.proj / ("11111111-2222-3333-4444-%012x" % 1) / "subagents", "a00000000000000b2")
        self.put(self.proj / FORK / "subagents" / "workflows" / "wf_00000000000000a1", aid)
        self.put(self.proj / ("11111111-2222-3333-4444-%012x" % 0xffffff) / "subagents", "a00000000000000b3")
        self.touch(self.proj)
        for a in (aid, nowhere):
            with self.subTest(aid=a):
                _forget()
                km._chat_dep_scope.deps = {"task_outs": [], "postal_any": False}
                try:
                    got = km._subagent_file(str(self.tpath), a)
                    notes = list(km._chat_dep_scope.deps["task_outs"])
                finally:
                    km._chat_dep_scope.deps = None
                want_notes = []
                self.assertEqual(got, oracle(str(self.tpath), a, want_notes))
                self.assertEqual(notes, want_notes)


class TheCostIsTheChange(_Project):
    """Red on stock: the walk tested every directory, re-stat'd every directory it had read on a hit, re-walked when any
    of them moved, and emptied its memo past 1,024 answers."""

    def _wide(self, n=40):
        """web's tree with `n` workflow directories, an agent in each; returns the last one's (id, path)."""
        last = None
        for k in range(n):
            aid = "a%016x" % (0xa000 + k)
            last = (aid, self.put(self.own / "workflows" / ("wf_%016x" % k), aid))
        return last

    @staticmethod
    def _count(fn_name):
        calls = []
        real = getattr(os.path, fn_name)

        def spy(p, *a, **k):
            calls.append(str(p))
            return real(p, *a, **k)
        return calls, mock.patch.object(os.path, fn_name, spy)

    def test_a_nested_agents_lookup_tests_only_the_directory_that_lists_it(self):
        aid, path = self._wide()
        calls, patch = self._count("isfile")
        with patch:
            self.assertEqual(km._subagent_file(str(self.tpath), aid), path)
        self.assertEqual(len(calls), 1, "one isfile, in the directory whose listing holds the name: %d" % len(calls))

    def test_inside_one_cycle_a_repeated_lookup_takes_no_stat_and_lists_nothing(self):
        aid, path = self._wide()
        sib_aid = "a0000000000000f01"
        sib_path = self.put(self.proj / FORK / "subagents" / "workflows" / "wf_0000000000000f01", sib_aid)
        self.touch(self.proj)
        km._live_scope.subagent_trees = {}
        try:
            self.assertEqual(km._subagent_file(str(self.tpath), aid), path)
            self.assertEqual(km._subagent_file(str(self.tpath), sib_aid), sib_path)
            seen = []
            real = {k: getattr(os, k) for k in ("stat", "lstat", "scandir", "listdir")}

            def wrap(k):
                def f(*a, **kw):
                    seen.append(k)
                    return real[k](*a, **kw)
                return f
            with mock.patch.multiple(os, **{k: wrap(k) for k in real}):
                self.assertEqual(km._subagent_file(str(self.tpath), aid), path)
                self.assertEqual(km._subagent_file(str(self.tpath), sib_aid), sib_path)
        finally:
            km._live_scope.subagent_trees = None
        self.assertEqual(seen, [], "the cycle's samples and the held answers serve both: %r" % seen[:8])

    def test_an_agent_landing_elsewhere_leaves_a_sibling_held_answer_standing(self):
        """web was resumed from FORK, so this card's agent lives in FORK's tree; web keeps landing workflow agents in its
        own. The next cycle asks again and tests nothing: no directory that could hold THIS name moved."""
        self._wide(12)
        aid = "a0000000000000f02"
        path = self.put(self.proj / FORK / "subagents" / "workflows" / "wf_0000000000000f02", aid)
        self.touch(self.proj)
        with mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0):
            km._live_scope.subagent_trees = {}
            try:
                self.assertEqual(km._subagent_file(str(self.tpath), aid), path)
            finally:
                km._live_scope.subagent_trees = None
            self.put(self.own / "workflows" / "wf_00000000000000ee", "a00000000000000ee")   # a workflow agent lands in web's tree
            self.put(self.proj / ("11111111-2222-3333-4444-%012x" % 9) / "subagents", "a00000000000000ef")   # and in another's
            self.touch(self.proj)
            calls, patch = self._count("isfile")
            km._live_scope.subagent_trees = {}
            try:
                with patch:
                    self.assertEqual(km._subagent_file(str(self.tpath), aid), path)
            finally:
                km._live_scope.subagent_trees = None
        self.assertEqual(calls, [], "the answer stood: nothing was tested")

    def test_the_memo_holds_past_a_thousand_answers(self):
        aids = ["a%016x" % (0xb0000 + k) for k in range(1100)]
        self.own.mkdir(parents=True)
        for a in aids:
            (self.own / ("agent-%s.jsonl" % a)).write_text("")
        self.touch(self.own)
        km._live_scope.subagent_trees = {}
        try:
            for a in aids:
                self.assertIsNotNone(km._subagent_file(str(self.tpath), a))
        finally:
            km._live_scope.subagent_trees = None
        calls, patch = self._count("isfile")
        km._live_scope.subagent_trees = {}
        try:
            with patch:
                self.assertEqual(km._subagent_file(str(self.tpath), aids[0]), self.own / ("agent-%s.jsonl" % aids[0]))
        finally:
            km._live_scope.subagent_trees = None
        self.assertEqual(calls, [], "the first answer is still held after 1,100")


class Bounded(_Project):
    def test_the_name_index_is_bounded_by_the_alive_sessions_projects(self):
        other = Path(self.td) / "projects" / "-notes-web"
        other.mkdir()
        (other / (FORK + ".jsonl")).write_text("")
        self.put(self.own / "workflows" / "wf_00000000000000d1", "a00000000000000d1")
        self.put(self.proj / FORK / "subagents", "a00000000000000d2")
        self.put(other / FORK / "subagents", "a00000000000000d3")
        km._subagent_file(str(self.tpath), "a00000000000000d2")          # web's tree and its project's siblings
        km._subagent_file(str(other / (FORK + ".jsonl")), "a00000000000000d3")
        roots = set(km._AGENT_TREE_NAMES)
        self.assertIn(str(self.proj / FORK / "subagents"), roots)
        self.assertIn(str(other / FORK / "subagents"), roots)
        km._subagent_trees_forget([{"sid": SID, "path": str(self.tpath)}, {"sid": "no-path"}])
        self.assertIn(str(self.own), km._AGENT_TREE_NAMES, "the alive session's own tree stays")
        self.assertIn(str(self.proj / FORK / "subagents"), km._AGENT_TREE_NAMES, "…and its project's siblings")
        self.assertNotIn(str(other / FORK / "subagents"), km._AGENT_TREE_NAMES, "a project nobody alive is in leaves")
        self.assertIn(str(self.proj), km._AGENT_PROJECT_KIDS)
        self.assertNotIn(str(other), km._AGENT_PROJECT_KIDS)


class PathTokensOncePerPass(unittest.TestCase):
    def test_a_token_named_in_several_messages_of_one_pass_is_resolved_once(self):
        td = tempfile.mkdtemp()
        sid = "11111111-2222-3333-4444-5555555555c0"
        cwd = os.path.join(td, "notes-api")
        os.makedirs(cwd)
        saved = (km._cwd_of, km._repo_index_served)
        km._cwd_of = lambda s: cwd
        km._repo_index_served = lambda c: (None, 0)
        target = os.path.join(cwd, "reports", "summary.md")
        calls = []
        real = os.path.isfile

        def spy(p):
            calls.append(str(p))
            return real(p)
        try:
            memo = {}
            with mock.patch.object(os.path, "isfile", spy):
                for k in range(3):
                    out = km._path_links("message %d: see reports/summary.md" % k, sid, "u-%d-%s" % (k, td), memo)
                    self.assertEqual(out, {}, "unresolved: the verdict is rendered, no link")
            self.assertEqual(calls.count(target), 1, "one test of the file for the pass: %r" % calls)
            os.makedirs(os.path.dirname(target))
            Path(target).write_text("x\n")
            self.assertEqual(km._path_links("again: reports/summary.md", sid, "u-next-" + td, {}),
                             {"reports/summary.md": "reports/summary.md"}, "a new pass sees the file")
        finally:
            km._cwd_of, km._repo_index_served = saved
            shutil.rmtree(td, ignore_errors=True)


class PagesEqualAColdRender(_Project):
    """A history page's Agent cards read the held answers; the page equals one rendered with every memo emptied, and
    after the trees change (an agent's file lands under the fork, another leaves) the re-render equals a cold one too."""

    def setUp(self):
        super().setUp()
        self.saved = (jd.STATE, km._sessions, km._live_map, km._sdk, km.NAMES)
        jd._rebind_state(Path(self.td) / "state")
        for d in ("states", "goals", "sdk", "checkpoints", "names", "episodes", "gone"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        km.NAMES = jd.NAMES
        cwd = Path(self.td) / "notes-api"
        (cwd / "api").mkdir(parents=True)
        (cwd / "api" / "notes.py").write_text("x\n")
        (jd.NAMES / SID).write_text("web\t%s\t#abcdef\n" % cwd)
        rnd = random.Random(11)
        recs, last, self.aids = [], None, []
        t0 = NOW - 86400
        for i in range(48):
            a = "a%016x" % rnd.getrandbits(64)
            self.aids.append(a)
            r = i % 4
            if r == 0:
                self.put(self.own, a)
            elif r == 1:
                self.put(self.own / "workflows" / ("wf_%016x" % (i // 8)), a)
            elif r == 2:
                self.put(self.proj / FORK / "subagents" / "workflows" / ("wf_%016x" % (i // 8)), a)
            t, tu = t0 + i * 600, "toolu_launch_%d" % i
            ack = {"isAsync": True, "status": "async_launched", "agentId": a, "description": "review %d" % i,
                   "taskType": "local_agent"}
            u, a1, r1, a2 = "u%d" % i, "a%d" % i, "r%d" % i, "x%d" % i
            iso = lambda s: time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(s))   # noqa: E731
            recs += [{"type": "user", "uuid": u, "parentUuid": last, "timestamp": iso(t), "sessionId": SID, "cwd": str(cwd),
                      "promptSource": "typed",
                      "message": {"role": "user", "content": "turn %d: check api/notes.py and reports/run-%d.md" % (i, i % 3)}},
                     {"type": "assistant", "uuid": a1, "parentUuid": u, "timestamp": iso(t + 2), "sessionId": SID,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                                  "content": [{"type": "tool_use", "id": tu, "name": "Agent",
                                               "input": {"description": "review %d" % i, "prompt": "Review.",
                                                         "subagent_type": "general-purpose", "run_in_background": True}}]}},
                     {"type": "user", "uuid": r1, "parentUuid": a1, "timestamp": iso(t + 3), "sessionId": SID,
                      "toolUseResult": ack, "message": {"role": "user", "content": [
                          {"type": "tool_result", "tool_use_id": tu, "content": "Async agent launched."}]}},
                     {"type": "assistant", "uuid": a2, "parentUuid": r1, "timestamp": iso(t + 5), "sessionId": SID,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "Running; see api/notes.py meanwhile."}]}}]
            last = a2
        self.tpath.write_text("".join(json.dumps(r) + "\n" for r in recs))
        rows = [{"sid": SID, "name": "web", "path": str(self.tpath), "mtime": NOW, "anchor": SID}]
        km._sessions = lambda now, **kw: list(rows)
        km._live_map = lambda: {}
        km._sdk = lambda: None

    def tearDown(self):
        jd._rebind_state(self.saved[0])
        km._sessions, km._live_map, km._sdk, km.NAMES = self.saved[1:]
        with km._page_lock:
            km._PAGE_CACHE.clear()
        super().tearDown()

    def _pages(self, cold):
        if cold:
            _forget()
            km._PATH_LINK_CACHE.clear()
        with km._page_lock:
            km._PAGE_CACHE.clear()
        km._live_scope.subagent_trees = {}
        try:
            out = []
            for lo in range(0, 48, 16):
                out.append(json.loads(json.dumps(km._chat_history_page(SID, lo, lo + 16, NOW), default=str)))
            return out
        finally:
            km._live_scope.subagent_trees = None

    def _agent_cards(self, pages):
        return {e.get("agentId"): e for p in pages for e in p if e.get("kind") == "tool" and e.get("agentId")}

    def test_pages_through_the_held_answers_equal_a_cold_render_before_and_after_a_change(self):
        with mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0):
            warm = self._pages(cold=True)
            cards = self._agent_cards(warm)
            self.assertEqual(len(cards), 48, "every launch is an Agent card with its id")
            for a, ev in cards.items():
                self.assertEqual("stepsTotal" in ev, oracle(str(self.tpath), a) is not None, a)
            self.assertEqual(self._pages(cold=False), self._pages(cold=True), "held answers: the same pages")
            self._pages(cold=False)
            # the trees change: a missing agent's file lands under the fork, a flat one leaves
            missing = next(a for i, a in enumerate(self.aids) if i % 4 == 3)
            self.put(self.proj / FORK / "subagents" / "workflows" / "wf_00000000000000aa", missing)
            gone = self.own / ("agent-%s.jsonl" % self.aids[0])
            gone.unlink()
            self.touch(self.own, self.proj)
            again = self._pages(cold=False)
            self.assertEqual(again, self._pages(cold=True), "after the change: the same pages as a cold render")
            cards = self._agent_cards(again)
            self.assertIn("stepsTotal", cards[missing], "the landed file is read")
            self.assertNotIn("stepsTotal", cards[self.aids[0]], "the departed one is not")


if __name__ == "__main__":
    unittest.main()
