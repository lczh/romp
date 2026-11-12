#!/usr/bin/env python3
"""_task_outputs_for joins a notification to its launch by id, at a cost that does not grow with the
transcript's task count (2026-11-12).

A chat build calls it for every user turn that carries a task notification. Each call used to ask the
background scan for EVERY task the transcript records (a copy of each row) and search the copies for the
one launch, so a whole-history build of a transcript with N background tasks copied N rows N times: a lab
transcript of 4,131 notifications copied 17 million rows and spent 8 s there, in the one process every page
waits on after a restart. It now reads the launch by its id off the scan's own state (em.bg_task_reader),
and a build keeps that reader for its pass, so the scan is read once per build.

Three things are pinned:
  * the answer is the old function's, over a generated corpus (every launch and result shape the scan
    pairs, replayed launches, unknown ids, present and absent output files); the old implementation is
    kept below as the oracle;
  * the WORK of one join is the same whatever the task count, counted (calls made, rows the scan copied),
    never timed, so the test cannot flake and fails on the quadratic version;
  * a whole build reads the scan the same number of times however many notifications it renders.

Synthetic fixtures only: invented commands and text, placeholder ids, the notes-api demo world."""
import gc
import json
import os
import random
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()      # hermetic state BEFORE the load (import-time root)
os.environ.pop("ROMP_STATE_DIR", None)
km = load_source("romp_kernel_taskoutputs", os.path.join(BIN, "romp-kernel"))
em, jd = km.em, km.jd

SID = "7a1c0de5-0000-4000-8000-00000000e2e2"            # private to this module


def _old_task_outputs_for(reminders, path):
    """THE ORACLE: _task_outputs_for as it stood before the keyed read, verbatim but for the module prefix.
    One whole scan (a copy of every row) and one search of the copies per notification."""
    out = {}
    for r in reminders:
        note = km._parse_task_notification("<task-notification>%s</task-notification>" % r)
        tid = (note or {}).get("tool_use_id")
        if not tid:
            continue
        cmd = next((tk.get("command") or "" for tk in km._bg_scan_all_cached(path) if tk.get("id") == tid), "")
        tail = km._read_task_output(note.get("output_file"))
        if cmd or tail:
            out[tid] = {"command": cmd, "output": tail}
    return out


def _inner(tid, outfile="", status="completed", summary="job finished"):
    """A notification's INNER xml, as _split_reminders hands it to the build (outer wrapper peeled)."""
    return ("<task-id>b1</task-id>\n<tool-use-id>%s</tool-use-id>\n<output-file>%s</output-file>\n"
            "<status>%s</status>\n<summary>%s</summary>" % (tid, outfile, status, summary))


def _wrapped(tid, outfile="", status="completed"):
    return "<task-notification>\n%s\n</task-notification>" % _inner(tid, outfile, status)


def _bash(tid, cmd):
    inp = {"run_in_background": True, "description": "run %s" % tid}
    if cmd is not None:
        inp["command"] = cmd
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": inp}]}}


def _agent_use(tid, prompt, explicit_bg=False, name="Agent"):
    inp = {"description": "agent %s" % tid, "prompt": prompt}
    if explicit_bg:
        inp["run_in_background"] = True
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}


def _agent_ack(tid, prompt="", error=False):
    blk = {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": "Async agent launched."}]}
    if error:
        blk["is_error"] = True
    return {"type": "user", "toolUseResult": {"isAsync": True, "status": "async_launched", "agentId": "a-" + tid,
                                              "description": "agent %s" % tid, "prompt": prompt},
            "message": {"content": [blk]}}


def _monitor(tid, cmd):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tid, "name": "Monitor",
                                                          "input": {"command": cmd, "timeout_ms": 60000}}]}}


def _result(tid, shape, outfile=""):
    if shape == "block":        # the older wrapper: the notification inside a tool_result naming the launch
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                                         "content": _wrapped(tid, outfile)}]}}
    if shape == "string":       # the standalone record whose content IS the notification
        return {"type": "user", "message": {"content": _wrapped(tid, outfile)}}
    return {"type": "queue-operation", "operation": "enqueue", "content": _wrapped(tid, outfile)}


def _write(recs, path=None, mode="w"):
    if path is None:
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
    with open(path, mode) as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    return path


def _corpus(rng, n, outdir, start=0):
    """`n` generated tasks → (records, reminder lists to join). Every launch shape the scan registers and every
    result shape it pairs, with the odd cases the join must answer as the old search did: a launch replayed
    under its id with other words (the first registration stands), a launch with no command, an ack that
    errored, a task still running, an id the transcript never launched, a reminder that is no notification."""
    recs, asks, known = [], [], []
    for i in range(start, start + n):
        tid = "toolu_%05d" % i
        kind = rng.choice(("bash", "bash", "bash", "bash-nocmd", "agent", "agent-bg", "workflow", "monitor", "agent-err"))
        if kind == "bash":
            recs.append(_bash(tid, "make job-%d" % i))
        elif kind == "bash-nocmd":
            recs.append(_bash(tid, None))
        elif kind == "agent":
            recs += [_agent_use(tid, "survey the notes-api routes, part %d" % i), _agent_ack(tid)]
        elif kind == "agent-bg":
            recs += [_agent_use(tid, "check the search index, part %d" % i, explicit_bg=True), _agent_ack(tid, "ack words %d" % i)]
        elif kind == "workflow":
            recs += [_agent_use(tid, "", name="Workflow"), _agent_ack(tid, "sweep the tests, part %d" % i)]
        elif kind == "monitor":
            recs.append(_monitor(tid, "tail -f build-%d.log" % i))
        else:
            recs += [_agent_use(tid, "a launch that is refused %d" % i, explicit_bg=True), _agent_ack(tid, error=True)]
        if rng.random() < 0.15:                       # a replayed launch record: same id, other words
            recs.append(_bash(tid, "REPLAYED-%d" % i))
        of = ""
        r = rng.random()
        if r < 0.4:                                   # an output file that exists
            of = os.path.join(outdir, "%s.output" % tid)
            with open(of, "w") as f:
                f.write("output of %s\nline two\n" % tid)
        elif r < 0.55:                                # one the notification names and the disk does not hold
            of = os.path.join(outdir, "%s.gone" % tid)
        if rng.random() < 0.8:                        # most return; the rest are still running
            recs.append(_result(tid, rng.choice(("block", "string", "queue")), of))
        if known and rng.random() < 0.1:              # a result replayed for an earlier task
            recs.append(_result(rng.choice(known), "string"))
        known.append(tid)
        asks.append([_inner(tid, of)])
    for k in range(max(2, n // 10)):
        ghost = "toolu_never_%d" % (start + k)
        of = os.path.join(outdir, "ghost-%d.output" % (start + k))
        if k % 2:
            with open(of, "w") as f:
                f.write("a tail with no launch\n")
        asks.append([_inner(ghost, of)])              # an id the transcript never launched: the tail alone, or nothing
        asks.append(["Your context is getting full.", _inner(rng.choice(known)), "<status>completed</status>"])
        asks.append([_inner(rng.choice(known)), _inner(rng.choice(known)), _inner(ghost)])   # several on one turn
    asks.append([])
    return recs, asks


class SameAnswerAsTheOldFunction(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()
        km._bgall_cache.clear(); km._task_out_cache.clear()

    def test_every_join_matches_the_oracle_over_a_generated_corpus(self):
        for seed in (1, 2, 3):
            rng = random.Random(seed)
            recs, asks = _corpus(rng, 120, self.td)
            path = _write(recs)
            memo, joined = {}, 0
            for rem in asks:
                want = _old_task_outputs_for(rem, path)
                self.assertEqual(km._task_outputs_for(rem, path), want, "seed %d, a call of its own: %r" % (seed, rem))
                self.assertEqual(km._task_outputs_for(rem, path, memo), want, "seed %d, inside one build's pass: %r" % (seed, rem))
                joined += len(want)
            self.assertGreater(joined, 100, "the corpus exercises the join (seed %d)" % seed)
            self.assertEqual(list(memo), [path], "one reader per transcript per pass")

    def test_the_corpus_reaches_every_case_it_claims(self):
        # the oracle comparison above proves nothing about a case the generator never produced
        recs, asks = _corpus(random.Random(1), 120, self.td)
        path = _write(recs)
        got = [_old_task_outputs_for(rem, path) for rem in asks]
        flat = [v for g in got for v in g.values()]
        self.assertTrue(any(v["command"] and v["output"] for v in flat), "a command with its tail")
        self.assertTrue(any(v["command"] and not v["output"] for v in flat), "a command alone")
        self.assertTrue(any(not v["command"] and v["output"] for v in flat), "a tail alone (no command, or no launch)")
        self.assertTrue(any(not g for g, rem in zip(got, asks) if rem), "a reminder list that joins nothing")
        self.assertFalse(any("REPLAYED" in v["command"] for v in flat), "a replayed launch never replaces the first")
        self.assertTrue(any("REPLAYED" in json.dumps(r) for r in recs), "…and the corpus holds replays")
        rows = km._bg_scan_all_cached(path)
        self.assertEqual(len({r["id"] for r in rows}), len(rows), "one row per id: the row under an id is the first carrying it")

    def test_a_later_pass_sees_what_the_transcript_gained(self):
        rng = random.Random(7)
        recs, asks = _corpus(rng, 30, self.td)
        path = _write(recs)
        memo = {}
        late = [_inner("toolu_late_1")]
        self.assertEqual(km._task_outputs_for(late, path, memo), {}, "not launched yet")
        _write([_bash("toolu_late_1", "make late")], path, "a")
        os.utime(path, None)
        self.assertEqual(km._task_outputs_for(late, path), {"toolu_late_1": {"command": "make late", "output": ""}},
                         "a call of its own reads the scan again")
        self.assertEqual(km._task_outputs_for(late, path, {}), _old_task_outputs_for(late, path), "and so does the next build's pass")
        for rem in asks:
            self.assertEqual(km._task_outputs_for(rem, path, {}), _old_task_outputs_for(rem, path))

    def test_the_reader_hands_out_copies(self):
        path = _write([_bash("toolu_a", "make a")])
        row = km._bg_task_reader(path)
        got = row("toolu_a")
        got["command"] = "scribbled"
        self.assertEqual(row("toolu_a")["command"], "make a", "a caller's write never reaches the fold's state")
        self.assertEqual(km._bg_scan_all_cached(path)[0]["command"], "make a")
        self.assertIsNone(row("toolu_missing"))

    def test_a_cache_shaped_for_the_running_view_is_refused(self):
        path = _write([_bash("toolu_a", "make a")])
        cache = {}
        em.scan_bg_tasks_cached(path, cache, want_all=False)
        with self.assertRaises(ValueError):
            em.bg_task_reader(path, cache)


class _Work(object):
    """Count the work done on this thread inside the block: every Python and C call made (sys.setprofile; calls, not
    time), the rows the scan copied out of its state (em._bg_finish) and the reads of the fold (em.fold_records).
    The collector is held off for the block, so no callback of its own is counted among them."""

    def __enter__(self):
        self.calls = self.rows = self.folds = 0
        self._gc = gc.isenabled()
        gc.disable()
        self._fin, self._fold = em._bg_finish, em.fold_records

        def fin(state, want_all=False):
            got = self._fin(state, want_all)
            self.rows += len(got)
            return got

        def fold(cache, *a, **k):
            if cache is km._bgall_cache:
                self.folds += 1
            return self._fold(cache, *a, **k)
        em._bg_finish, em.fold_records = fin, fold
        self._prev = sys.getprofile()

        def prof(frame, event, arg):
            if event in ("call", "c_call"):
                self.calls += 1
        sys.setprofile(prof)
        return self

    def __exit__(self, *exc):
        sys.setprofile(self._prev)
        em._bg_finish, em.fold_records = self._fin, self._fold
        if self._gc:
            gc.enable()
        return False


def _n_bash(n):
    return [_bash("toolu_%05d" % i, "make job-%d" % i) for i in range(n)]


class WorkDoesNotGrowWithTheTaskCount(unittest.TestCase):
    def setUp(self):
        km._bgall_cache.clear()

    def _one_join(self, n):
        path = _write(_n_bash(n))
        last = "toolu_%05d" % (n - 1)                  # the newest launch: the old search's longest walk
        km._bg_scan_all_cached(path)                   # the fold is warm, as it is under a build (the parse read the file)
        with _Work() as w:
            got = km._task_outputs_for([_inner(last)], path)
        self.assertEqual(got, {last: {"command": "make job-%d" % (n - 1), "output": ""}})
        return w

    def test_one_join_costs_the_same_among_fifty_tasks_and_among_two_thousand(self):
        small, big = self._one_join(50), self._one_join(2000)
        self.assertEqual(big.rows, 0, "no row but the one asked for is copied (the old join copied all %d)" % 2000)
        self.assertLessEqual(big.calls, small.calls + 20,
                             "the calls one join makes do not grow with the task count: %d among 50, %d among 2000"
                             % (small.calls, big.calls))

    def test_a_pass_over_every_notification_reads_the_scan_once(self):
        n = 400
        path = _write(_n_bash(n))
        km._bg_scan_all_cached(path)
        memo = {}
        with _Work() as w:
            for i in range(n):
                tid = "toolu_%05d" % i
                self.assertEqual(km._task_outputs_for([_inner(tid)], path, memo)[tid]["command"], "make job-%d" % i)
        self.assertEqual(w.folds, 1, "one read of the scan for the pass, not one per notification")
        self.assertEqual(w.rows, 0)
        self.assertLess(w.calls, 60 * n, "linear in the notifications: %d calls for %d (the old join made over %d)"
                        % (w.calls, n, n * n // 2))


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ABuildReadsTheScanOnce(unittest.TestCase):
    """build_session over a session whose turns carry notifications: each card gets its command, and the number of
    times the build reads the every-task scan does not depend on how many notifications it renders."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "session-hosts").write_text("off")        # a state root of this test's own: no session host
        self.cdir = td / "launchdir"; self.cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(self.cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(self.cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map,
                      km._GLOBAL_CLAUDE_MD)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE = td / "captions", td / "archive", td / "goals", td
        km.NAMES, km._GLOBAL_CLAUDE_MD = names, td / "no-global-claude.md"
        self.now = int(time.time())
        self.tm = {SID: {"state": "idle", "since": self.now - 100, "model": "", "effort": "", "context": None,
                         "compactPct": None, "color": None}}
        km._live_map = lambda: self.tm
        self._reset()

    def _reset(self):
        km._chat_fold.clear(); km._parse_cache.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
        if isinstance(jd._discover_cache, dict):
            jd._discover_cache.clear()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map,
         km._GLOBAL_CLAUDE_MD) = self.saved
        self._reset()
        self.td.cleanup()

    def _session(self, n):
        """`n` turns, each a prompt, a background launch with its ack, a reply, then the notification's own record."""
        recs, t, last, k = [], self.now - 86400, None, [0]

        def uid():
            k[0] += 1
            return "bbbbbbbb-0000-0000-0000-%012d" % k[0]

        def rec(kind, content, **extra):
            nonlocal t, last
            t += 5
            u = uid()
            r = {"type": kind, "timestamp": _iso(t), "uuid": u, "parentUuid": last, "cwd": "/tmp/notes-api",
                 "version": "2.1.0", "gitBranch": "main", "message": dict({"role": kind, "content": content}, **extra)}
            last = u
            recs.append(r)
        for i in range(n):
            tid = "toolu_bg_%04d" % i
            rec("user", "step %d: run the notes-api suite in the background" % i)
            recs[-1]["promptSource"] = "typed"
            rec("assistant", [{"type": "tool_use", "id": tid, "name": "Bash",
                               "input": {"run_in_background": True, "command": "uv run pytest -q tests/part%d" % i,
                                         "description": "suite part %d" % i}}], model="claude-sonnet-4", stop_reason="tool_use")
            rec("user", [{"type": "tool_result", "tool_use_id": tid, "content": "Command running in background"}])
            rec("assistant", [{"type": "text", "text": "Started part %d." % i}], model="claude-sonnet-4", stop_reason="end_turn")
            rec("user", _wrapped(tid))
            rec("assistant", [{"type": "text", "text": "Part %d passed." % i}], model="claude-sonnet-4", stop_reason="end_turn")
        _write(recs, str(self.tpath))
        os.utime(self.tpath, None)

    def _build(self, n):
        self._reset()
        self._session(n)
        km._live_scope.names = km._names_snapshot()
        try:
            with _Work() as w:
                m = km.build_session(SID, self.now, self.tm, floor=0)
        finally:
            km._live_scope.names = None
        cards = {}
        for ev in m["events"]:
            cards.update(ev.get("taskOutputs") or {})
        self.assertEqual(cards, {"toolu_bg_%04d" % i: {"command": "uv run pytest -q tests/part%d" % i, "output": ""}
                                 for i in range(n)}, "every notification's card carries its launch's command")
        return w

    def test_the_scan_reads_of_a_build_do_not_grow_with_its_notifications(self):
        few, many = self._build(3), self._build(40)
        self.assertEqual(many.folds, few.folds,
                         "a whole-history build reads the every-task scan as often for 40 notifications as for 3 "
                         "(%d and %d): the old join read it once per notification" % (many.folds, few.folds))
        self.assertEqual(many.rows, 0, "and the join copies no row it was not asked for (the old one: 40 rows 40 times)")


if __name__ == "__main__":
    unittest.main()
