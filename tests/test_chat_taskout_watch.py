#!/usr/bin/env python3
"""The taskout certificate (2026-10-29): a quiet push reads none of the task outputs, agent files and sibling roots the
chat tabs recorded, and a change costs the paths it touched.

Every chat tab's signature re-took the stat key of every path its cached build read outside the subagents trees it
samples (_chat_taskout_now's lone paths: each background task's output and the link an agent task's output is, each
Agent card's own transcript, the place a missing agent's file would land, the sibling sessions' subagents roots an agent
lookup read, a subagents directory not made yet), once per push for every path any open tab recorded. With many tabs
open on sessions that ran many background tasks, a quiet push stat'd thousands of files that had not changed since they
finished. On Linux those keys are now taken under inotify watches and served until an event touches the path
(_TaskoutWatch), and the record's plan keeps a group over the paths the certificate covers, so a quiet tab costs one
group check and the push one stat per anchor directory.

Pinned here, on a temp directory on a local filesystem (skipped where there is none, and off Linux), over eight tabs of the
notes-api demo whose records hold that shape (thirty outputs each, agent tasks' output links into the subagents trees,
agent files at the top of a tree and in workflow directories, a missing agent's place, two sibling roots, one of them for
a session whose directory was never made):
(1) RED FIRST, THE WORK: a quiet push stats, lstats or reads the link of none of the recorded paths (one stat per anchor
    directory), where the base stat'd every one; and one output growing re-reads that output alone.
(2) THE ORACLE: over a scripted run (an output written, appended, completed and back-dated, removed, replaced by a rename,
    rewritten in place; an agent's file growing behind its output link; a link retargeted; a file written through a hard
    link in another directory; a sibling root gaining a file; a subagents directory made; a missing agent's file landing;
    a workflow directory removed; the outputs directory and a chain directory moved away and made again; permissions
    taken off and given back; a full queue; a tab opened on a session with three hundred tasks; the directory above the
    outputs moved and made again, which no watch hears) and a seeded random run
    after it, every tab's component equals the one-stat-per-path evaluation at every push, a record that moved is
    replaced as the push's rebuild replaces it, and quiet pushes are served by the groups. The same holds for an agent
    task's output link when the directory above its target's project directory is renamed away, made unsearchable, or
    made again, none of which a watch hears: the link is never served its target's old key.
(3) WHERE IT IS NOT TRUSTED: a filesystem other than a local one, a sticky outputs directory, a spent watch budget and the
    switch off each read the paths as before, and the values stay exact.
Synthetic fixtures only: invented ids and text, temporary directories."""
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the load (the tests/test_subagent_tree_memo.py preamble), session hosts off in the minted root.
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_chat_taskout_watch", os.path.join(BIN, "romp-kernel"))

FSIDS = ["7e57a5c0-0b1d-4c2a-9e3f-%012x" % (0x100 + i) for i in range(10)]   # notes-api sessions; tabs on the first eight
TABS = 8
OUTS = 30                     # background-command outputs per tab
WFS = 3                       # workflow directories per tab's tree, one background agent each, its output a link to its file
SIB_TREE, SIB_NONE = 8, 9     # a sibling with a tree, and one whose session directory was never made


def _watch_reason():
    """Why the certificate cannot be exercised here, or None: it is Linux's, and only on a local filesystem."""
    if not km._TOUT_WATCH_ON:
        return "no inotify on this platform"
    w = km._TaskoutWatch()
    try:
        if not w._open():
            return "inotify could not be opened"
        kind = w._fs_type(os.stat(tempfile.gettempdir()).st_dev)
    finally:
        if w.fd:
            os.close(w.fd)
    if kind not in km._TREE_WATCH_LOCAL_FS:
        return "the temp directory is on %r, not a local filesystem" % (kind,)
    return None


def _reference(rec):
    """The taskout evaluation the certificate stands in for: one stat per recorded path."""
    return tuple((of, km._chat_stat_key(of)) for of, _k in rec["task_outs"])


class _World(unittest.TestCase):
    """The notes-api project: each tab's session has a subagents tree (an agent at the top, WFS workflow directories with a
    background agent each) and a store of task outputs (OUTS commands, one not written yet, and one link per background
    agent to the agent's own file); records note every path as a build reads it, keyed then."""

    def setUp(self):
        reason = _watch_reason()
        if reason:
            self.skipTest(reason)
        self.td = Path(tempfile.mkdtemp())
        self.w = km._TaskoutWatch()
        self.patches = [mock.patch.object(km, "_TOUT_WATCH", self.w), mock.patch.object(km, "_TOUT_WATCH_ON", True)]
        for p in self.patches:
            p.start()
        self.proj = self.td / "projects" / "-notes-api"
        self.tasks = self.td / "claude-tmp" / "-notes-api" / "tasks"
        self.other = self.td / "elsewhere"
        for d in (self.proj, self.tasks, self.other):
            d.mkdir(parents=True)
        self.tick = 0
        self.paths = {}
        for i in range(TABS):
            self.paths[FSIDS[i]] = self._session(i)
        (self.proj / FSIDS[SIB_TREE] / "subagents").mkdir(parents=True)
        self.recs = {sid: self.record(ps) for sid, ps in self.paths.items()}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        if self.w.fd:
            os.close(self.w.fd)
        km._live_scope.chat_shared = None
        for dirpath, dirnames, _files in os.walk(self.td):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.td, ignore_errors=True)

    # ── the world ────────────────────────────────────────────────────────────────────────────────────
    def _session(self, i, outs=OUTS, wfs=WFS):
        sid = FSIDS[i] if i < len(FSIDS) else "7e57a5c0-0b1d-4c2a-9e3f-%012x" % (0x100 + i)
        sub = self.proj / sid / "subagents"
        sub.mkdir(parents=True)
        top = sub / ("agent-a%04x0.jsonl" % i)
        top.write_text('{"note": "reviewed the notes"}\n')
        paths = [str(top)]
        for k in range(wfs):
            wf = sub / "workflows" / ("wf_%04x%02x" % (i, k))
            wf.mkdir(parents=True)
            af = wf / ("agent-a%04x%x.jsonl" % (i, k + 1))
            af.write_text('{"step": "tidy the readme"}\n')
            link = self.tasks / ("a%04x%x.output" % (i, k + 1))
            os.symlink(str(af), str(link))
            paths += [str(af), str(link)]
        for j in range(outs):
            out = self.tasks / ("b%02x%03d.output" % (i, j))
            out.write_text("make check: step %d\n" % j)
            paths.append(str(out))
        paths.append(str(self.tasks / ("b%02x999.output" % i)))        # launched, nothing written yet
        paths.append(str(sub / ("agent-a%04xf.jsonl" % i)))            # a missing agent's place in its own tree
        paths.append(str(self.proj / FSIDS[SIB_TREE] / "subagents"))   # the siblings its lookup read
        paths.append(str(self.proj / FSIDS[SIB_NONE] / "subagents"))
        return paths

    def record(self, paths):
        touts = [(p, km._chat_stat_key(p)) for p in paths]
        return {"task_outs": touts, "trees": (), "at_build": (tuple(touts), (), None)}

    def push(self):
        """One push's taskout components, every tab's, inside the push's memo slot (_chat_push_memo)."""
        km._live_scope.chat_shared = {}
        try:
            return {sid: km._chat_taskout_now(rec) for sid, rec in self.recs.items()}
        finally:
            km._live_scope.chat_shared = None

    def checked_push(self, what=""):
        """A push whose every tab's component must equal the one-stat-per-path evaluation; a tab whose component moved off
        its record's build keys is rebuilt (a fresh record), as the push's miss would rebuild it. Returns the tabs rebuilt."""
        got = self.push()
        rebuilt = []
        for sid, rec in list(self.recs.items()):
            ref = _reference(rec)
            self.assertEqual(got[sid], ref, "%s: tab %s's taskout component is the one-stat-per-path one" % (what, sid[-3:]))
            if ref != rec["at_build"][0]:
                self.recs[sid] = self.record(self.paths[sid])
                rebuilt.append(sid)
        return rebuilt

    def counted_push(self):
        """One push, with every os.stat, os.lstat and os.readlink call recorded as (call, path)."""
        calls = []
        reals = {k: getattr(os, k) for k in ("stat", "lstat", "readlink")}

        def wrap(kind):
            def f(p, *a, **k):
                if not isinstance(p, int):
                    calls.append((kind, os.fspath(p)))
                return reals[kind](p, *a, **k)
            return f
        with mock.patch.object(os, "stat", wrap("stat")), mock.patch.object(os, "lstat", wrap("lstat")), \
                mock.patch.object(os, "readlink", wrap("readlink")):
            got = self.push()
        for sid, rec in self.recs.items():
            self.assertEqual(got[sid], _reference(rec))
        return calls

    def output(self, i, j):
        return self.tasks / ("b%02x%03d.output" % (i, j))

    def agent(self, i, k):
        return self.proj / FSIDS[i] / "subagents" / "workflows" / ("wf_%04x%02x" % (i, k)) / ("agent-a%04x%x.jsonl" % (i, k + 1))

    def link(self, i, k):
        return self.tasks / ("a%04x%x.output" % (i, k + 1))


class Work(_World):
    def test_a_quiet_push_reads_no_recorded_path_and_a_change_only_its_own(self):
        self.checked_push("the first push")
        self.checked_push("the second push")
        recorded = {of for rec in self.recs.values() for of, _k in rec["task_outs"]}
        calls = self.counted_push()
        self.assertEqual(sorted(c for c in calls if c[1] in recorded), [],
                         "a quiet push reads no recorded output, link, agent file or sibling root")
        anchors = {km._tout_anchor(p) for p in recorded}
        self.assertEqual(anchors, {str(self.proj), str(self.tasks)}, "the fixture: two anchor directories")
        self.assertLessEqual(len(calls), len(anchors), "one stat per anchor directory, the push's whole cost")
        with open(self.output(3, 5), "a") as fh:
            fh.write("make check: done\n")
        calls = self.counted_push()
        touched = [c for c in calls if c[1] in recorded]
        self.assertTrue(touched, "the grown output is read again")
        self.assertEqual({p for _c, p in touched}, {str(self.output(3, 5))}, "and nothing else a tab recorded")
        self.assertLessEqual(len(touched), 2, "its re-arming: one look before the watch and one after")


class Oracle(_World):
    def _restore_perms(self):
        os.chmod(self.tasks, 0o755)

    def steps(self):
        """The scripted run: (name, change) pairs, each change a callable."""
        def write_new(i):
            (self.tasks / ("b%02x999.output" % i)).write_text("started\n")

        def append(i, j):
            with open(self.output(i, j), "a") as fh:
                fh.write("more output\n")

        def complete_backdated(i, j):
            append(i, j)
            os.utime(self.output(i, j), (1781000000 + j, 1781000000 + j))

        def remove(i, j):
            self.output(i, j).unlink()

        def replace(i, j):
            tmp = self.tasks / (".tmp-%d-%d" % (i, j))
            tmp.write_text("rewritten by rename %d\n" % self.tick)
            os.replace(tmp, self.output(i, j))

        def rewrite_same_size(i, j):
            p = self.output(i, j)
            data = p.read_bytes()
            with open(p, "r+b") as fh:
                fh.write(data[::-1])

        def grow_behind_link(i, k):
            with open(self.agent(i, k), "a") as fh:
                fh.write('{"step": "one more call"}\n')

        def retarget(i, k):
            tmp = self.tasks / (".lnk-%d-%d" % (i, k))
            os.symlink(str(self.agent(i, (k + 1) % WFS)), str(tmp))
            os.replace(tmp, self.link(i, k))

        def hard_link_write(i, j):
            h = self.other / ("hard-%d-%d" % (i, j))
            if not h.exists():
                os.link(self.output(i, j), h)
            with open(h, "a") as fh:
                fh.write("written through the other name\n")

        def sibling_gains_file():
            (self.proj / FSIDS[SIB_TREE] / "subagents" / ("agent-sib%d.jsonl" % self.tick)).write_text("{}\n")

        def make_missing_tree():
            (self.proj / FSIDS[SIB_NONE] / "subagents").mkdir(parents=True, exist_ok=True)

        def land_missing_agent(i):
            (self.proj / FSIDS[i] / "subagents" / ("agent-a%04xf.jsonl" % i)).write_text('{"late": true}\n')

        def drop_workflow(i, k):
            shutil.rmtree(self.agent(i, k).parent)

        def move_tasks_dir():
            old = self.tasks.parent / ("tasks-old-%d" % self.tick)
            os.rename(self.tasks, old)
            shutil.copytree(old, self.tasks, symlinks=True)       # the same names on new inodes...
            with open(self.output(1, 2), "a") as fh:              # ...and one of them different
                fh.write("after the move\n")

        def move_above_tasks():
            up = self.tasks.parent                                # no watch hears a rename above the anchor
            old = up.parent / ("-notes-api-old-%d" % self.tick)
            os.rename(up, old)
            shutil.copytree(old, up, symlinks=True)
            with open(self.output(1, 3), "a") as fh:
                fh.write("after the move above\n")

        def move_chain_dir(i):
            wfs = self.proj / FSIDS[i] / "subagents" / "workflows"
            if wfs.exists():
                old = wfs.parent / ("workflows-old-%d" % self.tick)
                os.rename(wfs, old)
                shutil.copytree(old, wfs, symlinks=True)

        def perms_off():
            os.chmod(self.tasks, 0)

        def full_queue():
            with self.w.lock:
                self.w._void_all()

        def open_big_tab():
            sid = "7e57a5c0-0b1d-4c2a-9e3f-%012x" % 0x1ff
            if sid not in self.paths:
                self.paths[sid] = self._session(0x1ff - 0x100, outs=300, wfs=2)
                self.recs[sid] = self.record(self.paths[sid])

        return [("quiet", lambda: None), ("an output written", lambda: write_new(2)), ("quiet", lambda: None),
                ("an output appended", lambda: append(0, 3)), ("an output completed, back-dated", lambda: complete_backdated(4, 7)),
                ("an output removed", lambda: remove(5, 1)), ("quiet", lambda: None),
                ("an output replaced by a rename", lambda: replace(6, 9)), ("rewritten in place", lambda: rewrite_same_size(7, 0)),
                ("an agent's file grows behind its link", lambda: grow_behind_link(1, 1)),
                ("a link retargeted", lambda: retarget(2, 0)), ("quiet", lambda: None),
                ("written through a hard link", lambda: hard_link_write(3, 11)),
                ("written through it again", lambda: hard_link_write(3, 11)),
                ("a sibling root gains a file", sibling_gains_file), ("a subagents directory made", make_missing_tree),
                ("a missing agent lands", lambda: land_missing_agent(5)), ("a workflow directory removed", lambda: drop_workflow(6, 2)),
                ("quiet", lambda: None), ("the outputs directory moved and made again", move_tasks_dir),
                ("quiet", lambda: None), ("the directory above the outputs moved and made again", move_above_tasks),
                ("quiet", lambda: None), ("a chain directory moved and made again", lambda: move_chain_dir(0)),
                ("an agent behind a moved chain grows", lambda: grow_behind_link(0, 0)),
                ("permissions taken off", perms_off), ("permissions given back", self._restore_perms),
                ("an output appended after", lambda: append(2, 2)), ("a full queue", full_queue), ("quiet", lambda: None),
                ("a tab on a session with three hundred tasks", open_big_tab), ("quiet", lambda: None),
                ("its output appended", lambda: append(0xff, 250)), ("quiet", lambda: None)]

    def test_every_push_equals_the_one_stat_per_path_evaluation(self):
        served = [0]
        real_holds = self.w.holds

        def holds(g, anchors, amemo):
            ok = real_holds(g, anchors, amemo)
            served[0] += ok
            return ok
        self.w.holds = holds
        rebuilt_any = False
        for n, (what, change) in enumerate(self.steps()):
            self.tick += 1
            change()
            rebuilt = self.checked_push("step %d (%s)" % (n, what))
            if what == "quiet":
                self.assertEqual(rebuilt, [], "step %d: a quiet push rebuilds nothing" % n)
            rebuilt_any = rebuilt_any or bool(rebuilt)
        self.assertTrue(rebuilt_any, "the changes moved tabs")
        self.assertGreater(served[0], 5 * TABS, "quiet tabs were served by their groups")
        rep = self.w.report()
        self.assertGreater(rep["toutServed"], 0)
        self.assertGreater(rep["toutCerts"], TABS * OUTS, "the certificate holds the recorded paths")

    def test_a_seeded_random_run_equals_the_one_stat_per_path_evaluation(self):
        rng = random.Random(20261029)
        moves = ("append", "remove", "write", "replace", "grow", "retarget", "touch", "sibling", "quiet", "quiet")
        for n in range(60):
            self.tick += 1
            m, i, j, k = rng.choice(moves), rng.randrange(TABS), rng.randrange(OUTS), rng.randrange(WFS)
            out = self.output(i, j)
            if m == "append" and out.exists():
                with open(out, "a") as fh:
                    fh.write("x" * rng.randrange(1, 40) + "\n")
            elif m == "remove" and out.exists():
                out.unlink()
            elif m == "write":
                out.write_text("fresh %d\n" % n)
            elif m == "replace":
                tmp = self.tasks / ".tmp"
                tmp.write_text("replaced %d\n" % n)
                os.replace(tmp, out)
            elif m == "grow" and self.agent(i, k).exists():
                with open(self.agent(i, k), "a") as fh:
                    fh.write("{}\n")
            elif m == "retarget":
                tmp = self.tasks / ".lnk"
                os.symlink(str(self.agent(i, rng.randrange(WFS))), str(tmp))
                os.replace(tmp, self.link(i, k))
            elif m == "touch" and out.exists():
                os.utime(out, (1781000000 + n, 1781000000 + n))
            elif m == "sibling":
                (self.proj / FSIDS[SIB_TREE] / "subagents" / ("agent-r%d.jsonl" % n)).write_text("{}\n")
            self.checked_push("random step %d (%s)" % (n, m))


class AboveTheLinkTarget(_World):
    """A rename or a permission change above a link target's anchor (the directory over the project directory) moves no
    watch, so only the anchor's stat sees it: a link riding a target whose certificate no longer stands is read as before,
    never served the target's old key, and the values stay exact when the directory comes back. Two more tabs: one that
    recorded only an agent task's output link, one that recorded the link before the agent's file."""

    def setUp(self):
        super().setUp()
        for sid, paths in (("link-only", [str(self.link(0, 0))]),
                           ("link-then-agent", [str(self.link(1, 1)), str(self.agent(1, 1))])):
            self.paths[sid] = paths
            self.recs[sid] = self.record(paths)

    def _settle(self):
        self.checked_push("the first push")
        self.assertEqual(self.checked_push("the second push"), [], "a quiet push rebuilds nothing")
        for i, k in ((0, 0), (1, 1)):
            c = self.w.certs.get(str(self.link(i, k)))
            self.assertIsNotNone(c, "the fixture: the link is certified")
            self.assertEqual(c[3], str(self.agent(i, k)), "on its target's certificate")

    def _pushes(self, what, n):
        for m in range(n):
            rebuilt = self.checked_push("%s, push %d" % (what, m))
            if m:
                self.assertEqual(rebuilt, [], "%s, push %d: a quiet push rebuilds nothing" % (what, m))

    def test_the_directory_above_the_project_renamed_away_and_back(self):
        self._settle()
        up = self.proj.parent
        away = self.td / "projects-away"
        os.rename(up, away)
        self._pushes("the directory above the project renamed away", 3)
        moved = away / self.agent(1, 1).relative_to(up)
        with open(moved, "a") as fh:                       # written while away, through its new path
            fh.write('{"step": "while away"}\n')
        os.rename(away, up)
        self._pushes("renamed back", 3)
        os.rename(up, away)
        shutil.copytree(away, up)                          # the same names on new inodes...
        with open(self.agent(0, 0), "a") as fh:            # ...and one of them different
            fh.write('{"step": "after the copy"}\n')
        self._pushes("made again", 3)

    def test_the_directory_above_the_project_made_unsearchable_and_searchable_again(self):
        if os.geteuid() == 0:
            self.skipTest("root searches any directory")
        self._settle()
        up = self.proj.parent
        os.chmod(up, 0)
        try:
            self._pushes("the directory above the project made unsearchable", 3)
        finally:
            os.chmod(up, 0o755)
        self._pushes("searchable again", 3)


class NotTrusted(_World):
    def _run(self, what):
        recorded = {of for rec in self.recs.values() for of, _k in rec["task_outs"]}
        self.checked_push(what)
        self.checked_push(what)
        calls = self.counted_push()
        with open(self.output(1, 1), "a") as fh:
            fh.write("more\n")
        os.unlink(self.output(2, 2))
        self.checked_push(what + ", after a change")
        return {p for _c, p in calls if p in recorded}

    def test_a_filesystem_other_than_a_local_one_reads_every_path_as_before(self):
        self.w._fs_type = lambda dev: "nfs"
        read = self._run("on nfs")
        recorded = {of for rec in self.recs.values() for of, _k in rec["task_outs"]}
        self.assertEqual(read, recorded, "every recorded path is read each push, as before")
        self.assertEqual(self.w.report()["toutCerts"], 0)

    def test_a_sticky_outputs_directory_is_never_an_anchor(self):
        os.chmod(self.tasks, 0o1777)
        read = self._run("under a sticky directory")
        outs = {of for rec in self.recs.values() for of, _k in rec["task_outs"] if of.startswith(str(self.tasks))}
        self.assertLessEqual(outs, read, "the outputs are read each push, as before")

    def test_a_spent_budget_lets_anchors_go_and_reads_the_rest_as_before(self):
        self.assertTrue(self.w.begin())
        self.w.cap = 40
        real_add = self.w.add
        peak = [0]

        def add(fd, path, mask):
            wd = real_add(fd, path, mask)
            peak[0] = max(peak[0], len(self.w.byw) + 1)
            return wd
        self.w.add = add
        self._run("on a small budget")
        self.assertLessEqual(peak[0], 40, "the watches never pass the budget")

    def test_the_switch_off_reads_every_path_as_before(self):
        with mock.patch.object(km, "_TOUT_WATCH_ON", False):
            read = self._run("switched off")
        recorded = {of for rec in self.recs.values() for of, _k in rec["task_outs"]}
        self.assertEqual(read, recorded)
        self.assertEqual(self.w.report()["toutCerts"], 0)


if __name__ == "__main__":
    unittest.main()
