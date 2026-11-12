#!/usr/bin/env python3
"""Document rebuilds in a worker process (plans/process-split.md, stage two; its first step, 2026-11-12).

With STATE/doc-worker reading `on`, the kernel's rebuild road hands each job to a bin/romp-doc-worker process: a fresh
interpreter that parses the transcript whole, writes the document through the writer the kernel's own road uses, writes no
fold checkpoint, and answers one `done` line. The kernel then does the bookkeeping its own writer would have done and the
leaf's next parse restores from the document. Pinned here:

- the worker's document restores equal to a cold parse of the same transcript, and of one that grew while the worker ran;
- the worker writes nothing in the checkpoint directory but the document and its sidecar;
- the switch absent, `off` or unreadable starts no process and rebuilds on the kernel's own thread, as before;
- a worker that cannot be started, crashes or leaves without a done line falls back to the kernel's own road for that job,
  counted and said; three in a row latch that road for the kernel's life, and a done line starts the count again;
- a leaf whose use ends mid-job, and the kernel's exit, end the worker by pid with no fallback;
- a job the machine has no memory for stays pending, counted and said once, and a smaller one behind it still goes; a worker
  that fails for memory (its own soft bound at the replace, its exit code, the OS) is a failed rebuild, never repeated
  inside the kernel;
- the worker is started with no preexec_fn, refuses a job whose kernel is already gone, and ends when the process that
  started it does.

Built on the rebuild's harness (test_asm_document_rebuild): synthetic notes-api transcripts, placeholder uuids, a temp state
root with session hosts off, and no CLI (ROMP_CLAUDE_BIN is /bin/false). The stand-in workers are small scripts speaking
the worker's two lines, through ROMP_DOC_WORKER_CMD."""
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_asm_document_rebuild as T   # noqa: E402  the rebuild's harness (the module, not its classes: its tests run once)

km, jd, em = T.km, T.jd, T.em
SID, SID2, NOW = T.SID, T.SID2, T.NOW
SID3 = "5b5b5b5b-3333-4222-8333-555555555555"             # private synthetic sids (no goal store is minted here)
SID4 = "5b5b5b5b-4444-4222-8333-555555555555"
WORKER = os.path.join(os.path.dirname(HERE), "bin", "romp-doc-worker")
LINUX = sys.platform.startswith("linux")
PLENTY = 64 << 30
REAL_MEM_AVAILABLE = km._mem_available_bytes                # the harness stands a test's own number in for it

# A stand-in worker: it reads the job line and then does what FAKE_DOC_WORKER_MODE says. Every start is one line in
# FAKE_DOC_WORKER_LOG, so a test counts the processes the kernel started.
STAND_IN = textwrap.dedent('''
    import json, os, signal, subprocess, sys, time
    mode = os.environ.get("FAKE_DOC_WORKER_MODE", "crash")
    with open(os.environ["FAKE_DOC_WORKER_LOG"], "a") as f:
        f.write("%d %s\\n" % (os.getpid(), mode))
    line = sys.stdin.readline()
    if mode == "crash":
        sys.exit(3)
    if mode == "garbage":
        print("no outcome here")
        sys.exit(0)
    if mode == "memory-exit":
        sys.exit(86)
    if mode == "killed":
        os.kill(os.getpid(), signal.SIGKILL)
    if mode == "memory-line":
        print(json.dumps({"op": "done", "outcome": "failed", "wrote": False, "memory": True,
                          "reason": "memory: the stand-in's bound"}))
        sys.exit(0)
    if mode == "hold":
        with open(os.environ["FAKE_DOC_WORKER_MARK"], "w") as f:
            f.write(str(os.getpid()))
        time.sleep(120)
        sys.exit(4)
    if mode == "grow":                                    # the real worker, then the transcript grows before the kernel hears
        job = json.loads(line)
        job["parent"] = os.getpid()
        p = subprocess.run([sys.executable, os.environ["FAKE_DOC_WORKER_REAL"]], input=(json.dumps(job) + "\\n").encode(),
                           stdout=subprocess.PIPE)
        with open(job["leaf"], "a") as f, open(os.environ["FAKE_DOC_WORKER_GROW"]) as g:
            f.write(g.read())
        sys.stdout.write(p.stdout.decode())
        sys.exit(p.returncode)
    sys.exit(5)
''')


def _plain(tree):
    """A parsed session as JSON compares it (a restored tree hydrated first): the oracle's form, a whole parse's too."""
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)
    for turn in t["turns"]:
        for a in turn["atoms"]:
            a.pop("lazy", None)
    return t


def _reset_worker():
    with km._DOC_WORKER_LOCK:
        km._DOC_WORKER.update(job=None, lost=0, latched=False, switchFault=None, held=0)
        km._DOC_WORKER["said"].clear()
        km._DOC_WORKER_STATS.update(spawned=0, done={}, fallbacks={}, refused=0, cancelled=0, stopped=0, memory=0, seconds=0.0,
                                    peak=None)


def _gone(pid):
    """Whether the process has ended: no such pid, or one that only waits to be reaped."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


class Harness(T.Harness):
    def setUp(self):
        super().setUp()
        _reset_worker()
        self.addCleanup(_reset_worker)
        # The event model is ONE module for every kernel a test process loads, and its release hook is the last loaded
        # kernel's: under the whole suite that is another module's kernel, whose worker table is empty, so a release
        # here reached no worker (CI, 2026-11-12: green alone, the cancel tests red in the suite). The kernel under test
        # is this module's, so its hook is the one installed for the test, and the prior one put back after.
        prior = em._ASM_RELEASE_HOOK[0]
        em.set_assembly_release_hook(km._doc_worker_released)
        self.addCleanup(em.set_assembly_release_hook, prior)
        names = ("ROMP_DOC_WORKER_CMD", "FAKE_DOC_WORKER_MODE", "FAKE_DOC_WORKER_LOG", "FAKE_DOC_WORKER_MARK",
                 "FAKE_DOC_WORKER_GROW", "FAKE_DOC_WORKER_REAL")
        saved = {k: os.environ.get(k) for k in names}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None)
                                 for k, v in saved.items()])
        for k in names:
            os.environ.pop(k, None)
        self.script = self.root / "stand_in_worker.py"
        self.script.write_text(STAND_IN)
        self.log, self.mark = self.root / "starts.log", self.root / "held.pid"
        os.environ["FAKE_DOC_WORKER_LOG"] = str(self.log)
        os.environ["FAKE_DOC_WORKER_MARK"] = str(self.mark)
        os.environ["FAKE_DOC_WORKER_REAL"] = WORKER
        self.mem = PLENTY                                 # what the admission reads as available: a test's own number
        patch = mock.patch.object(km, "_mem_available_bytes", lambda: self.mem)
        patch.start(); self.addCleanup(patch.stop)
        self.notices = []
        patch = mock.patch.object(km, "_sync_notice", lambda text, ok=True, kind="sync": self.notices.append((text, ok)))
        patch.start(); self.addCleanup(patch.stop)

    def tearDown(self):
        self._end_held()                                  # before the base joins the rebuild's thread, which waits on it
        super().tearDown()

    def _end_held(self):
        """A held stand-in a failed test left behind: ended by the pid it wrote, never by a pattern."""
        try:
            pid = int(self.mark.read_text())
        except (OSError, ValueError):
            return
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)

    def switch(self, text="on"):
        (jd.STATE / km.DOC_WORKER_FILE).write_text(text + "\n")

    def stand_in(self, mode):
        os.environ["ROMP_DOC_WORKER_CMD"] = "%s %s" % (sys.executable, self.script)
        os.environ["FAKE_DOC_WORKER_MODE"] = mode

    def real_worker(self):
        os.environ.pop("ROMP_DOC_WORKER_CMD", None)

    def starts(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def session(self, sid=SID, turns=260, tag="n"):
        """A session over the rebuild floor with no document, as a boot's whole parse leaves it: noted for a rebuild, its
        whole read gone from the reader's cache. Returns its leaf."""
        leaf = str(self.proj / (sid + ".jsonl"))
        self.write(T.transcript(turns, tag=tag), leaf=leaf)
        self.rows.append({"sid": sid, "path": leaf})
        self.assertEqual(self.boot_parse(sid=sid, leaf=leaf), ["full"])
        self.evict_read(leaf)
        return leaf

    def cycle(self, now=NOW + 600):
        """One writer cycle and the rebuild it starts, to its end; what was said meanwhile."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.writer_cycle(now)
            T._join_rebuilds()
        return err.getvalue()

    def restored(self, sid=SID, leaf=None):
        """The next boot's parse of the leaf with its document in place: restored, hydrated, in the oracle's form."""
        self.fresh()
        modes = []
        tree = jd.parsed_session(sid, [leaf or self.leaf], NOW, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], em.asm_checkpoint_stats()["fallbacks"])
        em.hydrate(tree, sid)
        return _plain(tree)

    def cold(self, sid=SID, leaf=None):
        """The oracle: a whole parse of the leaf by a process with no checkpoint directory."""
        self.fresh()
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None
        try:
            modes = []
            tree = jd.parsed_session(sid, [leaf or self.leaf], NOW, asm_mode_out=modes)
            self.assertEqual(modes, ["full"])
            return _plain(tree)
        finally:
            em._CKPT_DIR_FN = saved

    def rebuild_stats(self):
        return em.asm_checkpoint_stats()["rebuild"]


@unittest.skipUnless(LINUX, "the worker runs on Linux (it asks for a parent death signal; elsewhere the kernel's own road runs)")
class TheWorkerBuildsTheDocument(Harness):
    def test_the_worker_writes_the_document_and_the_restore_equals_a_cold_parse(self):
        self.session()
        self.switch("on")
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["switch"], st["spawned"], st["done"], st["fallbacks"]), ("on", 1, {"written": 1}, {}), st)
        self.assertTrue(self.doc_file().exists(), out)
        self.assertEqual(self.doc()["av"], em._ASM_CKPT_V)
        self.assertEqual(em.asm_checkpoint_stats()["written"], 0, "this process's writer never ran: the worker wrote it")
        rb = self.rebuild_stats()
        self.assertEqual((rb["claimed"], rb["written"], rb["pending"]), (1, 1, 0), "counted as any rebuild: %s" % rb)
        self.assertEqual(out.count("rebuilt the document of %s (noDocument;" % self.leaf), 1, out)
        self.assertIsNone(em._ASM_REFRESH["inflight"], "the claim is back")
        self.assertIsNone(st["running"])
        self.assertGreater(st["peak"]["vmPeak"], 0, "the worker reported what it cost")
        self.assertLess(st["peak"]["vmPeak"], st["peak"]["softBytes"])
        self.assertEqual(km._PERF_STATS.snapshot()["docWorker"]["done"], {"written": 1}, "the block rides /perf")
        self.assertEqual(self.restored(), self.cold(), "restored from the worker's document equals the cold parse")
        self.restores_with_no_whole_parse()

    def test_the_handback_records_what_this_process_s_writer_would_have(self):
        """The worker's writer ran in another process, so what the kernel's writer keeps per leaf is brought level at the done
        line: the cut of the document last published (the same number the kernel's own road records for the same bytes), and
        a refusal of the previous document recorded before the job began is retired."""
        twin = self.session(SID2)                                  # the same bytes under another name, built on the kernel's road
        self.cycle()
        want = em._ASM_LAST_WRITE_CUT.get(os.path.realpath(twin))
        self.assertTrue(want, "the premise: the kernel's own writer recorded its cut")
        self.session()
        rp = os.path.realpath(self.leaf)
        with em._ASM_CKPT_LOCK:
            em._ASM_CKPT_REFUSED[rp] = ("version", time.monotonic())
        self.assertNotIn(rp, em._ASM_LAST_WRITE_CUT)
        self.switch("on")
        self.cycle(NOW + 601)
        self.assertEqual(km._doc_worker_stats()["done"], {"written": 1})
        self.assertEqual(em._ASM_LAST_WRITE_CUT.get(rp), want, "the published cut is recorded")
        self.assertNotIn(rp, em._ASM_CKPT_REFUSED, "the older refusal is retired by the fresh document")

    def test_a_document_past_the_cap_leaves_its_verdict_and_the_kernel_reads_it(self):
        """The worker's writer finds the document past the cap and keeps the verdict on disk. The kernel had already looked for
        one (none, memoized for the process): the handback drops that memo, so the verdict holds here too and the leaf is not
        handed to a worker again at every parse."""
        cap = 2048                                                 # bytes: under this transcript's document (tens of KB)
        saved = em._ASM_CKPT_CAP
        em._ASM_CKPT_CAP = cap
        self.addCleanup(setattr, em, "_ASM_CKPT_CAP", saved)
        saved_env = os.environ.get("ROMP_ASM_CKPT_CAP_MB")
        os.environ["ROMP_ASM_CKPT_CAP_MB"] = repr(cap / 1048576.0)   # the worker reads its cap from the environment it is given
        self.addCleanup(lambda: os.environ.__setitem__("ROMP_ASM_CKPT_CAP_MB", saved_env) if saved_env is not None
                        else os.environ.pop("ROMP_ASM_CKPT_CAP_MB", None))
        self.session()
        self.assertIsNone(em.asm_oversize_stands(self.leaf), "the premise: no verdict yet, and the kernel has looked")
        self.switch("on")
        self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["done"], st["fallbacks"]), ({"declined": 1}, {}), st)
        self.assertFalse(self.doc_file().exists())
        self.assertTrue(em._asm_oversize_file(self.leaf).exists(), "the worker kept the verdict")
        self.assertIsNotNone(em.asm_oversize_stands(self.leaf), "and the kernel reads it after the handback")
        with open(self.leaf, "a") as f:                            # a settled turn later, parsed again: not noted for a rebuild
            f.writelines(json.dumps(r) + "\n" for r in T.transcript(262)[-4:])
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.boot_parse()
        self.assertEqual(em.asm_rebuild_pending(), [], "no worker is started for it again")

    def test_a_refusal_recorded_after_the_job_began_outlives_the_handback(self):
        self.session()
        rp = os.path.realpath(self.leaf)
        ticket = em.asm_document_job(self.leaf, SID, False)
        with em._ASM_CKPT_LOCK:
            em._ASM_CKPT_REFUSED[rp] = ("version", time.monotonic() + 1.0)   # stamped after the ticket
        em.asm_document_handback(ticket, "noDocument", {"outcome": "written", "wrote": True, "docCut": ["u", 1, 0, 1, 77]},
                                 external=True)
        self.assertIn(rp, em._ASM_CKPT_REFUSED, "a refusal newer than the job is not the worker's to retire")
        self.assertEqual(em._ASM_LAST_WRITE_CUT.get(rp), 77)

    def test_a_transcript_that_grew_while_the_worker_ran_is_accepted_and_restores_equal(self):
        """The worker's document describes the transcript as the worker read it. Turns appended before the kernel hears the
        done line change nothing: the handback takes the document, and the restore reads the appended tail after its cut."""
        twin = str(self.proj / (SID2 + ".jsonl"))
        self.write(T.transcript(260), leaf=twin)
        turns0 = len(self.cold(SID2, twin)["turns"])               # what the transcript holds before it grows
        self.session()
        more = T.transcript(275)[len(T.transcript(260)):]
        grow = self.root / "grow.jsonl"
        grow.write_text("".join(json.dumps(r) + "\n" for r in more))
        os.environ["FAKE_DOC_WORKER_GROW"] = str(grow)
        size0 = os.path.getsize(self.leaf)
        self.switch("on")
        self.stand_in("grow")
        out = self.cycle()
        self.assertGreater(os.path.getsize(self.leaf), size0, "the premise: the transcript grew during the job")
        st = km._doc_worker_stats()
        self.assertEqual((st["done"], st["fallbacks"]), ({"written": 1}, {}), "%s\n%s" % (st, out))
        self.assertEqual(self.rebuild_stats()["written"], 1)
        got = self.restored()
        self.assertEqual(got, self.cold(), "restored over the grown transcript equals its cold parse")
        self.assertGreater(len(got["turns"]), turns0, "the appended turns are in it")

    @unittest.skipUnless(LINUX, "the process start is checked on Linux")
    def test_the_worker_is_started_without_a_preexec_fn_from_this_checkout(self):
        """A preexec_fn makes the interpreter fork the whole kernel to start a child; without one it does not."""
        self.session()
        self.switch("on")
        calls, real = [], subprocess.Popen

        def spy(argv, **kw):
            calls.append((list(argv), dict(kw)))
            return real(argv, **kw)
        with mock.patch.object(km.subprocess, "Popen", spy):
            self.cycle()
        self.assertEqual(len(calls), 1)
        argv, kw = calls[0]
        self.assertEqual(argv, [sys.executable, WORKER])
        self.assertNotIn("preexec_fn", kw)
        self.assertNotIn("start_new_session", kw)
        self.assertEqual(km._doc_worker_stats()["done"], {"written": 1})


class TheSwitch(Harness):
    def _no_process(self):
        boom = mock.Mock(side_effect=AssertionError("no process is started with the switch off"))
        with mock.patch.object(km.subprocess, "Popen", boom):
            out = self.cycle()
        self.assertEqual(boom.call_count, 0)
        return out

    def test_with_no_switch_file_the_rebuild_runs_on_the_kernel_s_thread_and_no_process_starts(self):
        self.session()
        self.assertFalse((jd.STATE / km.DOC_WORKER_FILE).exists(), "the premise: the default is no file")
        self._no_process()
        self.assertTrue(self.doc_file().exists())
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1, "this process's writer wrote it")
        st = km._doc_worker_stats()
        self.assertEqual((st["switch"], st["spawned"], st["done"], st["fallbacks"], st["refused"]), ("off", 0, {}, {}, 0), st)
        self.restores_with_no_whole_parse()

    def test_off_and_any_other_text_are_off(self):
        for i, text in enumerate(("off", "yes", "")):
            with self.subTest(text=text):
                sid = (SID, SID2, SID3)[i]
                leaf = self.session(sid, tag="s%d" % i)
                self.switch(text)
                self._no_process()
                self.assertTrue(self.doc_file(leaf).exists())
        self.assertEqual(km._doc_worker_stats()["spawned"], 0)
        self.assertEqual(em.asm_checkpoint_stats()["written"], 3)

    def test_the_memory_admission_is_not_asked_with_the_switch_off(self):
        self.session()
        self.mem = 0
        self._no_process()
        self.assertTrue(self.doc_file().exists(), "today's road has no admission: unchanged")
        self.assertEqual(km._doc_worker_stats()["refused"], 0)

    def test_a_switch_that_cannot_be_read_is_off_and_said_once(self):
        (jd.STATE / km.DOC_WORKER_FILE).mkdir()                    # a directory where the file belongs: an OSError at the read
        self.session()
        leaf2 = self.session(SID2, tag="m")
        out = self._no_process() + self._no_process()
        self.assertTrue(self.doc_file().exists() and self.doc_file(leaf2).exists())
        self.assertEqual(len([n for n in self.notices if "switch" in n[0] and n[1] is False]), 1, self.notices)
        self.assertEqual(out.count("documents are rebuilt in this kernel"), 1, out)
        self.assertTrue(km._doc_worker_stats()["switch"].startswith("unreadable"))


class LostWorkers(Harness):
    def test_a_worker_that_crashes_falls_back_to_this_kernel_and_is_counted(self):
        self.session()
        self.switch("on")
        self.stand_in("crash")
        out = self.cycle()
        self.assertEqual(len(self.starts()), 1, "one process was started")
        self.assertTrue(self.doc_file().exists(), "the job ran on the kernel's own road: %s" % out)
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1, "written by this process")
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["fallbacks"], st["lostInARow"], st["latched"]),
                         (1, {}, {"crash": 1}, 1, False), st)
        self.assertIn("gave no outcome (crash: exit 3); the rebuild runs in this kernel", out)
        rb = self.rebuild_stats()
        self.assertEqual((rb["claimed"], rb["written"]), (1, 1), "one claim, one outcome: %s" % rb)
        self.assertIsNone(em._ASM_REFRESH["inflight"])
        self.restores_with_no_whole_parse()

    def test_a_worker_that_answers_no_done_line_is_a_crash_too(self):
        self.session()
        self.switch("on")
        self.stand_in("garbage")
        self.cycle()
        self.assertTrue(self.doc_file().exists())
        self.assertEqual(km._doc_worker_stats()["fallbacks"], {"crash": 1})

    def test_three_workers_that_cannot_be_started_latch_the_kernel_s_own_road(self):
        leaves = [self.session(sid, turns=260 + 10 * i, tag="s%d" % i) for i, sid in enumerate((SID, SID2, SID3, SID4))]
        self.switch("on")
        os.environ["ROMP_DOC_WORKER_CMD"] = str(self.root / "no-such-worker")
        out = ""
        for i in range(3):
            out += self.cycle(NOW + 600 + i)
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["fallbacks"], st["latched"]), (0, {"spawn": 3}, True), st)
        self.assertEqual(len([n for n in self.notices if "lost 3 jobs in a row" in n[0] and n[1] is False]), 1, self.notices)
        self.assertEqual(out.count("3 jobs in a row were lost with no outcome"), 1, out)
        self.assertEqual(len([lf for lf in leaves if self.doc_file(lf).exists()]), 3, "each lost job was built here")
        self.stand_in("crash")                                     # a worker that WOULD start, were one asked for
        out = self.cycle(NOW + 610)
        self.assertEqual(self.starts(), [], "latched: no process is started for the fourth job")
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["fallbacks"]), (0, {"spawn": 3, "latched": 1}), "and that job is counted too: %s" % st)
        self.assertEqual(len([lf for lf in leaves if self.doc_file(lf).exists()]), 4)
        self.assertEqual(self.rebuild_stats()["written"], 4)

    @unittest.skipUnless(LINUX, "the real worker runs on Linux")
    def test_with_no_checkpoint_directory_no_worker_starts_and_the_job_is_the_kernel_s(self):
        leaf = self.session()
        boom = mock.Mock(side_effect=AssertionError("a worker with nowhere to write is not started"))
        with mock.patch.object(em, "_ckpt_dir", return_value=None), mock.patch.object(km.subprocess, "Popen", boom), \
                contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertIsNone(km._doc_worker_run(str(leaf), SID, False, "lab"), "None: the caller runs the job itself")
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["fallbacks"], st["lostInARow"]), (0, {"noCheckpointDir": 1}, 0), st)
        self.assertIn("nowhere to write; the rebuild runs in this kernel", err.getvalue())

    def test_a_done_line_starts_the_count_again(self):
        leaves = [self.session(sid, turns=260 + 10 * i, tag="s%d" % i) for i, sid in enumerate((SID, SID2, SID3, SID4))]
        self.switch("on")
        for i, mode in enumerate(("crash", "crash", None, "crash")):
            self.stand_in(mode) if mode else self.real_worker()
            self.cycle(NOW + 600 + i)
        st = km._doc_worker_stats()
        self.assertEqual((st["fallbacks"], st["done"], st["lostInARow"], st["latched"]),
                         ({"crash": 3}, {"written": 1}, 1, False), "three losses, never three in a row: %s" % st)
        self.assertEqual(len([lf for lf in leaves if self.doc_file(lf).exists()]), 4)


class EndedOnPurpose(Harness):
    def _held(self):
        self.session()
        self.switch("on")
        self.stand_in("hold")
        self.writer_cycle()
        self.assertTrue(T._until(self.mark.exists), "the worker started and holds")
        self.assertTrue(T._until(lambda: self.mark.read_text().strip().isdigit()))
        pid = int(self.mark.read_text())
        run = km._doc_worker_stats()["running"]
        self.assertEqual(run and run["pid"], pid, "the job in flight is on /perf")
        self.assertFalse(km._asm_rebuild_maybe(), "one at a time: no second job while one runs")
        return pid

    def test_a_leaf_whose_use_ends_mid_job_ends_its_worker(self):
        pid = self._held()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            em.release_assembly(self.leaf, SID)                    # a /clear, a retired fork, a dead lane
            T._join_rebuilds(timeout=30)
        self.assertFalse(T._rebuild_alive(), "the job's thread ended with its worker")
        self.assertTrue(_gone(pid), "ended and reaped")
        st = km._doc_worker_stats()
        self.assertEqual((st["cancelled"], st["fallbacks"], st["done"], st["running"]), (1, {}, {}, None), st)
        self.assertFalse(self.doc_file().exists(), "no document, and none built here instead")
        self.assertEqual(em.asm_checkpoint_stats()["written"], 0)
        rb = self.rebuild_stats()
        self.assertEqual((rb["released"], rb["pending"], rb["written"]), (1, 0, 0), rb)
        self.assertIsNone(em._ASM_REFRESH["inflight"], "the claim is back")
        self.assertIn("the leaf's use ended while it ran", err.getvalue())

    def test_another_leaf_s_release_leaves_the_worker_alone(self):
        pid = self._held()
        other = str(self.proj / (SID2 + ".jsonl"))
        em.release_assembly(other, SID2)
        em.release_assembly(self.leaf, SID2)                       # the same file under another session's entry
        self.assertFalse(_gone(pid))
        self.assertTrue(T._rebuild_alive())
        self.assertEqual(km._doc_worker_stats()["cancelled"], 0)
        em.release_assembly(self.leaf)                             # every session's use of the leaf
        T._join_rebuilds(timeout=30)
        self.assertTrue(_gone(pid))
        self.assertEqual(km._doc_worker_stats()["cancelled"], 1)

    def test_the_kernel_s_exit_ends_the_worker_and_it_is_not_a_crash(self):
        pid = self._held()
        self.assertTrue(km._doc_worker_stop())
        T._join_rebuilds(timeout=30)
        self.assertTrue(_gone(pid))
        st = km._doc_worker_stats()
        self.assertEqual((st["stopped"], st["fallbacks"], st["lostInARow"]), (1, {}, 0), st)
        self.assertFalse(self.doc_file().exists(), "nothing was built here instead")
        self.assertIsNone(em._ASM_REFRESH["inflight"])
        self.assertFalse(km._doc_worker_stop(), "no job in flight: nothing to signal")


class Memory(Harness):
    @unittest.skipUnless(LINUX, "the real worker runs on Linux")
    def test_a_job_the_machine_has_no_room_for_stays_pending_and_is_counted(self):
        self.session()
        self.switch("on")
        need = km._doc_worker_need(os.path.getsize(self.leaf))
        self.assertEqual(need, int(1.5 * 3.3 * os.path.getsize(self.leaf)), "1.5 x 3.3 x the transcript's bytes")
        self.mem = need - 1
        out = self.cycle() + self.cycle(NOW + 601)
        self.assertFalse(T._rebuild_alive())
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["refused"], st["heldForMemory"], st["fallbacks"]), (0, 2, 1, {}), st)
        self.assertEqual(out.count("waits for memory"), 1, "said once while it waits: %s" % out)
        rb = self.rebuild_stats()
        self.assertEqual((rb["pending"], rb["claimed"]), (1, 0), "still noted, never claimed: %s" % rb)
        self.assertFalse(self.doc_file().exists(), "and not built in this kernel instead")
        self.assertEqual(em.asm_checkpoint_stats()["written"], 0)
        self.mem = need                                            # the room is there at a later opening of the gate
        self.cycle(NOW + 602)
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["heldForMemory"]), (1, {"written": 1}, 0), st)
        self.assertTrue(self.doc_file().exists())

    @unittest.skipUnless(LINUX, "the real worker runs on Linux")
    def test_a_smaller_job_behind_a_refused_one_still_goes(self):
        big = self.session(SID, turns=420, tag="b")
        small = self.session(SID2, turns=260, tag="s")
        self.switch("on")
        self.mem = km._doc_worker_need(os.path.getsize(small)) + 1
        self.assertLess(self.mem, km._doc_worker_need(os.path.getsize(big)), "the premise: room for the small one alone")
        self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["refused"], st["heldForMemory"], st["done"]), (1, 1, 1, {"written": 1}), st)
        self.assertTrue(self.doc_file(small).exists())
        self.assertFalse(self.doc_file(big).exists())
        self.assertEqual(self.rebuild_stats()["pending"], 1)

    def test_free_memory_that_cannot_be_read_runs_the_job_here_and_is_counted(self):
        self.session()
        self.switch("on")
        self.mem = None
        self.stand_in("crash")
        out = self.cycle()
        self.assertEqual(self.starts(), [], "no worker without an admission")
        self.assertTrue(self.doc_file().exists())
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["fallbacks"], st["lostInARow"]), (0, {"noMemInfo": 1}, 0), st)
        self.assertEqual(out.count("free memory cannot be read"), 1, out)

    @unittest.skipUnless(LINUX, "MemAvailable is Linux's")
    def test_the_machine_s_free_memory_is_read(self):
        got = REAL_MEM_AVAILABLE()
        self.assertIsInstance(got, int)
        self.assertGreater(got, 1 << 20)

    def _memory_shaped(self, mode):
        self.session()
        self.switch("on")
        self.stand_in(mode)
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["memory"], st["fallbacks"], st["lostInARow"]), (1, 1, {}, 0), st)
        self.assertFalse(self.doc_file().exists(), "the same parse is not repeated inside the kernel")
        self.assertEqual(em.asm_checkpoint_stats()["written"], 0)
        rb = self.rebuild_stats()
        self.assertEqual((rb["retry"], rb["pending"]), (1, 1), "a failed rebuild: tried once more, through admission: %s" % rb)
        self.assertIn("failed for memory", out)
        self.assertIsNone(em._ASM_REFRESH["inflight"])
        out = self.cycle(NOW + 601)                                # the one retry, by a worker again, and then the loud line
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["memory"], st["fallbacks"]), (2, 2, {}), st)
        self.assertIn("FAILED TWICE", out)
        self.assertEqual(self.rebuild_stats()["pending"], 0)
        self.assertEqual(em.asm_checkpoint_stats()["written"], 0)

    def test_a_worker_that_leaves_with_the_memory_exit_code_is_not_repeated_in_the_kernel(self):
        self._memory_shaped("memory-exit")

    def test_a_worker_the_os_killed_is_not_repeated_in_the_kernel(self):
        self._memory_shaped("killed")

    def test_a_done_line_that_says_memory_is_not_repeated_in_the_kernel(self):
        self._memory_shaped("memory-line")

    @unittest.skipUnless(LINUX, "the worker's peak address space is read from /proc")
    def test_the_real_worker_publishes_nothing_once_its_address_space_passed_its_soft_bound(self):
        """The soft bound end to end: with a bound the interpreter itself is over, the real worker parses, reaches the replace,
        and publishes nothing: no document, no temp file, a failed rebuild with `memory` said, and no fallback."""
        self.session()
        self.switch("on")
        ck = jd.STATE / "checkpoints"
        with mock.patch.object(km, "DOC_WORKER_AS_FLOOR", 1), mock.patch.object(km, "DOC_WORKER_MEM_FACTOR", 0.0), \
                mock.patch.object(km, "DOC_WORKER_AS_GUARD", 8 << 30):
            out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["memory"], st["fallbacks"]), (1, {"failed": 1}, 1, {}), "%s\n%s" % (st, out))
        self.assertIn("past its bound", out)
        self.assertEqual([p.name for p in ck.iterdir() if ".asm." in p.name or p.name.endswith(".tmp")], [],
                         "nothing published, nothing left behind")
        self.assertEqual(self.rebuild_stats()["retry"], 1)


@unittest.skipUnless(LINUX, "the parent death signal is Linux's")
class TheWorkerProcess(Harness):
    def _job(self, leaf, **over):
        job = {"op": "build", "leaf": leaf, "rompuuid": SID, "sdkHuman": False, "cands": [leaf],
               "states": str(jd.STATE / "states" / (SID + ".jsonl")), "postalLog": str(jd.STATE / "messages.jsonl"), "now": NOW,
               "rebuild": "noDocument", "ckptDir": str(jd.STATE / "checkpoints"), "softBytes": 0, "limitBytes": 0,
               "parent": os.getpid()}
        job.update(over)
        return job

    def _run(self, job_line):
        return subprocess.run([sys.executable, WORKER], input=job_line, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=120)

    def test_one_job_in_one_done_line_out(self):
        self.write(T.transcript(260))
        p = self._run((json.dumps(self._job(self.leaf)) + "\n").encode())
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        lines = p.stdout.decode().splitlines()
        self.assertEqual(len(lines), 1, "the done line and nothing else on stdout: %r" % lines)
        done = json.loads(lines[0])
        self.assertEqual((done["op"], done["outcome"], done["wrote"], done["memory"]), ("done", "written", True, False), done)
        self.assertNotEqual(done["pid"], os.getpid())
        self.assertEqual(len(done["docCut"]), 5)
        self.assertTrue(self.doc_file().exists())

    def test_the_worker_writes_nothing_but_the_document(self):
        """No fold checkpoint is written by the worker: the kernel stays their only writer. A fold checkpoint the kernel wrote
        for the leaf stands untouched, byte for byte and stamp for stamp, and the worker adds the document and its sidecar
        alone, leaving no temp file."""
        self.write(T.transcript(260))
        jd.parsed_session(SID, [self.leaf], NOW)                   # the kernel read the leaf, and writes its fold checkpoint
        self.assertTrue(em.checkpoint_write(self.leaf, force=True))
        ck = jd.STATE / "checkpoints"

        def listing():
            return {p.name: (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(ck.iterdir())}
        before = listing()
        self.assertTrue(before, "the premise: the kernel's own fold checkpoint is on disk")
        self.assertFalse([n for n in before if ".asm." in n], "and no document yet")
        p = self._run((json.dumps(self._job(self.leaf)) + "\n").encode())
        self.assertEqual(json.loads(p.stdout.decode())["outcome"], "written", p.stderr.decode())
        after = listing()
        self.assertEqual({n: v for n, v in after.items() if n in before}, before, "what the kernel wrote is untouched")
        doc = self.doc_file().name
        self.assertEqual(sorted(set(after) - set(before)), sorted([doc, doc + ".meta"]),
                         "the worker's files: the document and its sidecar, no fold checkpoint, no temp file")

    def test_a_job_whose_kernel_is_gone_is_refused(self):
        self.write(T.transcript(260))
        p = self._run((json.dumps(self._job(self.leaf, parent=1)) + "\n").encode())
        self.assertEqual((p.returncode, p.stdout), (2, b""), p.stderr.decode())
        self.assertIn("left before the job began", p.stderr.decode())
        self.assertFalse(self.doc_file().exists())

    def test_no_job_is_refused(self):
        for line in (b"", b"not json\n", b'{"op": "other"}\n'):
            p = self._run(line)
            self.assertEqual((p.returncode, p.stdout), (2, b""), line)

    def test_the_worker_ends_when_the_process_that_started_it_does(self):
        """The worker asks for its own death signal: a starter that exits mid-job takes the worker with it. The job's states
        log here is a named pipe nobody writes, so the parse waits at its open for as long as the worker lives."""
        self._the_starter_leaves()

    def test_a_worker_started_with_sigterm_ignored_and_blocked_still_ends_with_its_starter(self):
        """An ignored SIGTERM and a blocked one are both inherited across exec, and SIGTERM is how a worker is ended (its
        starter's death, a cancel by pid): the worker gives itself the default disposition, unblocked, before it asks."""
        self._the_starter_leaves("import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                                 "signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})\n")

    def _the_starter_leaves(self, prelude=""):
        self.write(T.transcript(260))
        fifo = str(self.root / "states.fifo")
        os.mkfifo(fifo)
        pidfile = self.root / "worker.pid"
        starter = prelude + textwrap.dedent('''
            import json, os, subprocess, sys, time
            job = json.loads(sys.argv[2]); job["parent"] = os.getpid()
            p = subprocess.Popen([sys.executable, sys.argv[1]], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
            p.stdin.write((json.dumps(job) + "\\n").encode()); p.stdin.close()
            with open(sys.argv[3], "w") as f:
                f.write(str(p.pid))
            time.sleep(1.5)                               # the worker is past its start and waits at the pipe
            os._exit(0)                                   # the starter leaves without reaping it
        ''')
        subprocess.run([sys.executable, "-c", starter, WORKER, json.dumps(self._job(self.leaf, states=fifo)), str(pidfile)],
                       timeout=60, check=True)
        pid = int(pidfile.read_text())
        self.addCleanup(lambda: _gone(pid) or os.kill(pid, signal.SIGKILL))
        self.assertTrue(T._until(lambda: _gone(pid), timeout=20), "the worker ended with its starter")
        self.assertFalse(self.doc_file().exists(), "the premise: it never finished the job")


if __name__ == "__main__":
    unittest.main()
