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
- the worker's bounds count what the JOB adds, from what the process held as its parse began: an interpreter that holds a
  gigabyte of address space before any job (a free-threaded build's allocator reserves one) builds a job within its bound,
  a job that grows past its bound still publishes nothing, and the limit stops a heap that outgrows it (2026-11-16);
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
    if os.environ.get("FAKE_DOC_WORKER_JOB"):
        with open(os.environ["FAKE_DOC_WORKER_JOB"], "a") as f:
            f.write(line)
    if mode == "crash":
        sys.exit(3)
    if mode == "failed-line":                              # a failure that is not memory: a fault of the worker's own
        print(json.dumps({"op": "done", "outcome": "failed", "wrote": False, "memory": False,
                          "reason": "ValueError('a fault only the worker has')"}))
        sys.exit(0)
    if mode == "lie":                                      # a written document that is not on disk
        print(json.dumps({"op": "done", "outcome": "written", "wrote": True, "memory": False, "reason": None,
                          "docCut": ["u", 1, 0, 1, 77], "newPre": 1}))
        sys.exit(0)
    if mode == "drift-exit":
        sys.exit(87)
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
    if mode in ("hold", "stubborn"):
        if mode == "stubborn":                             # a hang SIGTERM does not end
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
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
    if mode == "reserved":                                # the real worker, in a process that holds a gigabyte of address space
        import io, mmap, runpy                            #  (and none of memory) before the job, as a free-threaded build does
        hold = mmap.mmap(-1, 1 << 30, flags=mmap.MAP_PRIVATE, prot=0)
        sys.stdin = io.StringIO(line)
        runpy.run_path(os.environ["FAKE_DOC_WORKER_REAL"], run_name="__main__")   # (it leaves through os._exit)
    sys.exit(5)
''')

# The worker's own bounds at work on a heap, in a process of its own: its module loaded from the file, `held` bytes of
# address space reserved first (untouched: no memory), the baseline read, the limit set `limit` bytes over it, then small
# objects made, as a parse makes them, until an allocation fails or the heap has grown three times past the limit (a limit
# that does not bind). One JSON line: the baseline, the limit in force, whether the heap was stopped, how far the resident
# memory got, and what the soft bound answers at an eighth of the limit and at four times it.
PROBE = textwrap.dedent('''
    import importlib.util, json, mmap, resource, sys
    spec = importlib.util.spec_from_file_location("romp_doc_worker_probe", sys.argv[1])
    w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)
    limit, held = int(sys.argv[2]), int(sys.argv[3])
    hold = mmap.mmap(-1, held, flags=mmap.MAP_PRIVATE, prot=0) if held else None
    base = w._baseline()
    set_ = w._limit_address_space(limit, base[0])
    heap, stopped = [], False
    try:
        while w._vm_bytes("VmRSS") - base[1] < 3 * limit:
            heap.append([{"k": i, "v": "x" * 64} for i in range(8192)])
    except MemoryError:
        stopped = True
    heap.clear()                                          # room to answer in; the peaks stand
    print(json.dumps({"base": base, "set": set_, "rlimit": resource.getrlimit(resource.RLIMIT_AS)[0], "stopped": stopped,
                      "grew": w._vm_bytes("VmHWM") - base[1], "eighth": w._over_soft_bound(limit // 8, base),
                      "four": w._over_soft_bound(4 * limit, base), "unread": w._over_soft_bound(limit, (None, base[1]))}))
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
        km._DOC_WORKER_STATS.update(spawned=0, done={}, fallbacks={}, refused=0, cancelled=0, stopped=0, memory=0, verdicts=0,
                                    verdictHeld=0, seconds=0.0, peak=None)
        km._DOC_WORKER_VERDICTS.clear()
    with em._RECORD_COUNTS_LOCK:
        em._RECORD_COUNTS.clear()                         # (a fixture rewritten in place keeps its inode: count it afresh)


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
                 "FAKE_DOC_WORKER_GROW", "FAKE_DOC_WORKER_REAL", "FAKE_DOC_WORKER_JOB")
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
        os.environ["FAKE_DOC_WORKER_JOB"] = str(self.root / "jobs.jsonl")
        self.mem = PLENTY                                 # what the admission reads as available: a test's own number
        patch = mock.patch.object(km, "_mem_available_bytes", lambda: self.mem)
        patch.start(); self.addCleanup(patch.stop)
        self.notices = []
        patch = mock.patch.object(km, "_sync_notice", lambda text, ok=True, kind="sync": self.notices.append((text, ok)))
        patch.start(); self.addCleanup(patch.stop)
        self.problems = []                                # the error center's rows (_sdk_problem)
        patch = mock.patch.object(km, "_sdk_problem", self.problems.append)
        patch.start(); self.addCleanup(patch.stop)
        patch = mock.patch.object(km, "_restart_pending", lambda checkout=None: False)   # the checkout runs this code
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

    def jobs(self):
        """The job lines the stand-in was handed (FAKE_DOC_WORKER_JOB), parsed."""
        f = Path(os.environ["FAKE_DOC_WORKER_JOB"])
        return [json.loads(ln) for ln in f.read_text().splitlines()] if f.exists() else []

    def plan(self, leaf=None, sid=SID):
        return km._doc_worker_plan(leaf or self.leaf, sid, False)

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

    def _next_life(self):
        """The next kernel life, after a version bump refused the document: nothing in memory, the boot parses the leaf whole
        and notes it, its read gone (the converge pass cannot write it), the verdict file standing."""
        for f in (self.doc_file(), Path(str(self.doc_file()) + ".meta")):
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
        _reset_worker()
        self.fresh()
        self.assertEqual(self.boot_parse(), ["full"])
        self.evict_read()
        self.assertEqual(self.rebuild_stats()["pending"], 1)

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
        peak = st["peak"]
        self.assertGreater(peak["vmBase"], 0, "the worker reported what it held as its parse began")
        self.assertGreater(peak["rssBase"], 0)
        self.assertGreaterEqual(peak["vmPeak"], peak["vmBase"], "and what it cost")
        self.assertLess(peak["vmPeak"] - peak["vmBase"], peak["softBytes"], "the job's growth is what the bound is held against")
        self.assertLess(peak["vmHWM"] - peak["rssBase"], peak["softBytes"])
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
        # Its environment is the kernel's, with the allocator's reservation off whatever the kernel was started with: the
        # limit counts address space, which a free-threaded build's allocator otherwise takes a gigabyte at a time.
        # (Names and booleans alone are compared: no report renders a value of the environment.)
        self.assertEqual(kw["env"]["MIMALLOC_ARENA_RESERVE"], "0")
        self.assertTrue(kw["env"]["FAKE_DOC_WORKER_LOG"] == str(self.log), "the kernel's own environment is under it")
        with mock.patch.dict(os.environ, {"MIMALLOC_ARENA_RESERVE": "1048576"}):
            env = km._doc_worker_env()
            self.assertEqual(env["MIMALLOC_ARENA_RESERVE"], "0")
            self.assertEqual(set(env) ^ set(os.environ), set(), "every name of the kernel's environment, and no other")
            self.assertEqual([k for k, v in os.environ.items() if k != "MIMALLOC_ARENA_RESERVE" and env[k] != v], [],
                             "each with the kernel's value")


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
        self.assertEqual(len([p for p in self.problems if "failed 3 rebuilds in a row" in p]), 1, self.problems)
        self.assertEqual(out.count("3 jobs in a row were lost"), 1, out)
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

    def test_a_worker_that_answers_failed_falls_back_here_and_three_in_a_row_latch(self):
        """A done line that reports a failure not of memory is a fault the worker may have and the kernel not (another
        interpreter, another environment): the job runs here, counted, and it counts toward the latch, so a worker that fails
        every job cannot leave every session without a document with nothing but stderr to say so."""
        leaves = [self.session(sid, turns=260 + 10 * i, tag="s%d" % i) for i, sid in enumerate((SID, SID2, SID3, SID4))]
        self.switch("on")
        self.stand_in("failed-line")
        out = ""
        for i in range(3):
            out += self.cycle(NOW + 600 + i)
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["fallbacks"], st["latched"]), (3, {"failed": 3}, {"failed": 3}, True), st)
        self.assertEqual(len([lf for lf in leaves if self.doc_file(lf).exists()]), 3, "each failed job was built here: %s" % out)
        self.assertIn("gave no outcome (failed: ValueError('a fault only the worker has')); the rebuild runs in this kernel", out)
        self.assertEqual(len([p for p in self.problems if "failed 3 rebuilds in a row" in p]), 1, self.problems)
        self.assertIn("3 jobs in a row were lost (the last: failed)", out)
        self.cycle(NOW + 610)
        self.assertEqual(len(self.starts()), 3, "latched: no fourth worker")
        self.assertEqual(len([lf for lf in leaves if self.doc_file(lf).exists()]), 4)

    def test_a_done_line_whose_document_is_not_on_disk_is_a_failure(self):
        """A done line that says `written` while no document stands is not believed: the job is a failure the worker lost,
        it runs here, and nothing the line claimed (its cut) is recorded."""
        self.session()
        self.switch("on")
        self.stand_in("lie")
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["fallbacks"], st["lostInARow"]), (1, {"failed": 1}, {"failed": 1}, 1), st)
        self.assertIn("the worker reported a document that does not stand", out)
        self.assertTrue(self.doc_file().exists(), "built here instead")
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1, "by this process's writer")
        self.assertNotEqual(em._ASM_LAST_WRITE_CUT.get(os.path.realpath(self.leaf)), 77, "the line's cut was not recorded")
        rb = self.rebuild_stats()
        self.assertEqual((rb["claimed"], rb["written"], rb["failed"]), (1, 1, 0), "one claim, one outcome: %s" % rb)

    def _bounded(self, mode, floor=1.0):
        self.session()
        self.switch("on")
        self.stand_in(mode)
        with mock.patch.object(km, "DOC_WORKER_TIME_FLOOR_S", floor), mock.patch.object(km, "DOC_WORKER_TIME_RATE", 1 << 40), \
                mock.patch.object(km, "DOC_WORKER_KILL_GRACE_S", 1.0):
            out = self.cycle()
        pid = int(self.mark.read_text())
        self.assertTrue(_gone(pid), "the worker was ended and reaped")
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["fallbacks"], st["lostInARow"], st["memory"], st["running"]),
                         (1, {"timeout": 1}, 1, 0, None), st)
        self.assertTrue(self.doc_file().exists(), "the job ran here: %s" % out)
        self.assertEqual(len([p for p in self.problems if "stopped answering" in p]), 1, self.problems)
        self.assertIn("none within its bound", out)
        self.assertIsNone(em._ASM_REFRESH["inflight"], "the claim is back")
        return out

    def test_a_worker_that_never_answers_is_ended_at_its_bound_and_the_job_runs_here(self):
        self._bounded("hold")

    def test_a_hang_that_ignores_sigterm_is_killed_and_is_not_read_as_memory(self):
        """SIGKILL after the grace: the worker's end then looks like the OS's memory kill, and must read as the timeout."""
        self._bounded("stubborn")
        self.assertEqual(km._doc_worker_stats()["verdicts"], 0)

    def test_the_bound_grows_with_the_transcript(self):
        self.assertEqual(km._doc_worker_time_bound(0), km.DOC_WORKER_TIME_FLOOR_S)
        self.assertAlmostEqual(km._doc_worker_time_bound(300 << 20), 600 + 1200.0)

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
        need = self.plan()["limit"]
        self.mem = need - 1
        out = self.cycle() + self.cycle(NOW + 601)
        self.assertFalse(T._rebuild_alive())
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["refused"], st["heldForMemory"], st["fallbacks"]), (0, 2, 1, {}), st)
        self.assertEqual(out.count("waits for memory"), 1, "said once while it waits: %s" % out)
        self.assertEqual(len([p for p in self.problems if "free memory" in p]), 1, "and filed in the error center once: %s"
                         % self.problems)
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
        self.mem = self.plan(small, SID2)["limit"]
        self.assertLess(self.mem, self.plan(big, SID)["limit"], "the premise: room for the small one alone")
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
        """A worker that ran past its OWN memory bound on an exact count keeps a verdict beside the leaf's document, as the
        writer keeps an oversize one, and the job runs in this kernel now: the session is never worse off than with the
        switch off. In the next kernel life the verdict sends the leaf's rebuild to the kernel without starting a worker."""
        self.session()
        self.switch("on")
        self.stand_in(mode)
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["memory"], st["verdicts"], st["fallbacks"], st["lostInARow"]),
                         (1, 1, 1, {"memory": 1}, 0), st)
        self.assertTrue(self.doc_file().exists(), "rebuilt in this kernel, as with the switch off: %s" % out)
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1)
        rb = self.rebuild_stats()
        self.assertEqual((rb["claimed"], rb["written"], rb["retry"]), (1, 1, 0), "one claim, one outcome: %s" % rb)
        self.assertIn("ran past its memory bound", out)
        self.assertEqual(len([p for p in self.problems if "ran out of memory" in p]), 1, self.problems)
        self.assertTrue(km._doc_worker_verdict_file(self.leaf).exists(), "the verdict is on disk")
        self.assertIsNone(em._ASM_REFRESH["inflight"])
        self._next_life()
        out = self.cycle(NOW + 601)
        self.assertEqual(len(self.starts()), 1, "no worker is started for it again")
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["verdictHeld"], st["fallbacks"]), (0, 1, {"verdict": 1}), st)
        self.assertTrue(self.doc_file().exists(), "and it is rebuilt here, never dropped: %s" % out)
        self.assertEqual(self.rebuild_stats()["written"], 1)

    def test_a_worker_that_leaves_with_the_memory_exit_code_keeps_a_verdict_and_the_job_runs_here(self):
        self._memory_shaped("memory-exit")

    def test_a_done_line_that_says_memory_keeps_a_verdict_and_the_job_runs_here(self):
        self._memory_shaped("memory-line")

    def test_a_kill_from_outside_is_a_lost_job_and_keeps_no_verdict(self):
        """The OS's memory killer picks the worker (it asks to be picked first) when something else on the machine grows: that
        says nothing about this transcript, so it is a lost job, run here, and no verdict is kept."""
        self.session()
        self.switch("on")
        self.stand_in("killed")
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["memory"], st["verdicts"], st["fallbacks"], st["lostInARow"]), (0, 0, {"killed": 1}, 1), st)
        self.assertFalse(km._doc_worker_verdict_file(self.leaf).exists())
        self.assertTrue(self.doc_file().exists(), out)

    def test_no_verdict_is_kept_when_a_file_could_not_be_counted(self):
        self.session()
        self.switch("on")
        self.stand_in("memory-exit")
        with mock.patch.object(em, "transcript_records", lambda path: None):
            out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["memory"], st["verdicts"], st["fallbacks"]), (1, 0, {"memory": 1}), st)
        self.assertIn("no verdict is kept, since a file it reads could not be counted", out)
        self.assertFalse(km._doc_worker_verdict_file(self.leaf).exists())
        self.assertTrue(self.doc_file().exists())

    @unittest.skipUnless(LINUX, "the worker's memory is read from /proc")
    def test_the_real_worker_publishes_nothing_once_it_grew_past_its_soft_bound(self):
        """The soft bound end to end: with a bound any parse grows past, the real worker parses, reaches the replace, and
        publishes nothing: no document, no temp file, a failed rebuild with `memory` said, and no fallback."""
        self.session()
        self.switch("on")
        ck = jd.STATE / "checkpoints"
        with mock.patch.object(km, "_doc_worker_bounds", lambda est: (8 << 30, 1)):   # a soft bound any parse grows past
            out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["memory"], st["fallbacks"], st["verdicts"]),
                         (1, {"failed": 1}, 1, {"memory": 1}, 1), "%s\n%s" % (st, out))
        self.assertIn("past its bound", out)
        self.assertEqual([p.name for p in ck.iterdir() if p.name.endswith(".tmp")], [], "nothing left behind")
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1, "the document is this kernel's: the worker published none")
        self.assertEqual(self.rebuild_stats()["written"], 1)

    @unittest.skipUnless(LINUX, "the worker's memory is read from /proc")
    def test_what_the_process_held_before_the_job_is_not_counted_and_what_the_job_adds_still_is(self):
        """The bounds are on what the JOB adds (2026-11-16). A free-threaded interpreter holds about 1,060 MB of address space
        before any job, where a default one holds about 40: its allocator reserves a gigabyte. Counted from zero, the bounds
        (about 100 MB for a small job) refused every job there: no document, a memory verdict for each leaf. The stand-in is
        the real worker in a process that reserved a gigabyte first, so this holds on every build: a job within its bound
        is built, though the process was past the job's whole limit before the job began; and the same bytes under a bound
        of half what that job grew by publish nothing and keep a verdict, so a job that outgrows its bound is still
        stopped by it."""
        self.session(SID2)                                         # the same bytes under another name
        self.session()
        self.switch("on")
        self.stand_in("reserved")
        out = self.cycle()
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["memory"], st["fallbacks"]), (1, {"written": 1}, 0, {}),
                         "%s\n%s" % (st, out))
        peak = st["peak"]
        self.assertGreater(peak["vmBase"], peak["limitBytes"], "the premise: past the job's limit before the job began")
        self.assertGreater(peak["vmBase"], 1 << 30)
        grew = peak["vmHWM"] - peak["rssBase"]
        self.assertGreater(grew, 1 << 20, "the premise: the job added memory of its own: %s" % peak)
        self.assertLess(max(grew, peak["vmPeak"] - peak["vmBase"]), peak["softBytes"])
        with mock.patch.object(km, "_doc_worker_bounds", lambda est: (8 << 30, grew // 2)):
            out = self.cycle(NOW + 601)
        st = km._doc_worker_stats()
        self.assertEqual((st["spawned"], st["done"], st["memory"], st["fallbacks"], st["verdicts"]),
                         (2, {"written": 1, "failed": 1}, 1, {"memory": 1}, 1), "%s\n%s" % (st, out))
        self.assertIn("past its bound", out)
        leaf = [lf for lf in (self.leaf, str(self.proj / (SID2 + ".jsonl"))) if km._doc_worker_verdict_file(lf).exists()]
        self.assertEqual(len(leaf), 1, "the second job's leaf keeps the verdict, the first one's none")
        v = json.loads(km._doc_worker_verdict_file(leaf[0]).read_text())
        self.assertGreater(v["vmBase"], 1 << 30, "the verdict says where the count began")
        self.assertGreaterEqual(v["vmPeak"], v["vmBase"])
        self.assertEqual(em.asm_checkpoint_stats()["written"], 1, "that job's document is this kernel's")
        self.assertEqual(self.rebuild_stats()["written"], 2)


class Sizing(Harness):
    """The estimate: 64 MiB + the bytes + 8 KiB a record over every file the parse reads; the admitted figure, 1.5 times it,
    is both what MemAvailable must cover and the worker's address-space limit."""

    def _lines(self, leaf=None):
        with open(leaf or self.leaf, "rb") as f:
            return f.read().count(b"\n")

    def test_records_are_counted_exactly_even_where_a_sample_would_miss_them(self):
        """A run of tiny records between long ones, placed where windows read across the file would fall on either side of
        it (the review's case, scaled down: a sample counted 0.15 of the records): the count is exact."""
        f = str(self.proj / "uneven.jsonl")
        big = json.dumps({"type": "user", "pad": "x" * (32 << 10)}) + "\n"
        tiny = json.dumps({"t": 1}) + "\n"
        with open(f, "w") as fh:
            fh.write(big * 2 + tiny * 9000 + big * 60)
        want = self._lines(f)
        self.assertEqual(em.transcript_records(f), want)
        step = (os.path.getsize(f) - (64 << 10)) // 7               # where eight 64 KiB windows across it would read
        windows = b""
        with open(f, "rb") as fh:
            for i in range(8):
                fh.seek(i * step)
                windows += fh.read(64 << 10)
        self.assertLess(windows.count(b"\n") * os.path.getsize(f) // len(windows), want // 2,
                        "the premise: a sample of that shape would count under half")

    def test_a_file_is_counted_once_per_state_and_a_grown_one_from_where_the_count_stopped(self):
        self.session()
        n = self._lines()
        self.assertEqual(em.transcript_records(self.leaf), n)
        with mock.patch.object(em, "open", create=True, side_effect=AssertionError("counted again")):
            self.assertEqual(em.transcript_records(self.leaf), n, "the same file at the same size: a stat")
        size0 = os.path.getsize(self.leaf)
        with open(self.leaf, "a") as f:
            f.writelines(json.dumps(r) + "\n" for r in T.transcript(300)[-60:])
            f.write('{"partial": ')                                   # a last line still being written counts too
        seeks = []
        real_open = open

        def spy(path, mode="r", *a, **k):
            fh = real_open(path, mode, *a, **k)
            real_seek = fh.seek
            fh.seek = lambda off, *w: (seeks.append(off), real_seek(off, *w))[1]
            return fh
        with mock.patch.object(em, "open", create=True, side_effect=spy):
            self.assertEqual(em.transcript_records(self.leaf), self._lines() + 1)
        self.assertEqual(seeks, [size0], "the new bytes alone")
        tmp = self.leaf + ".new"
        Path(tmp).write_text("".join(json.dumps(r) + "\n" for r in T.transcript(100)))
        os.replace(tmp, self.leaf)                                 # replaced: another inode, counted from the start
        self.assertEqual(em.transcript_records(self.leaf), self._lines())
        self.assertIsNone(em.transcript_records(str(self.proj / "no-such.jsonl")))

    def test_the_estimate_and_the_admitted_figure(self):
        self.session()
        plan = self.plan()
        size, n = os.path.getsize(self.leaf), self._lines()
        self.assertEqual((plan["bytes"], plan["records"], plan["files"], plan["exact"]), (size, n, [self.leaf], True))
        self.assertEqual(plan["estimate"], (64 << 20) + size + (8 << 10) * n)
        self.assertEqual(plan["limit"], int(1.5 * plan["estimate"]))
        self.assertEqual(plan["soft"], plan["limit"] - min(256 << 20, plan["limit"] // 3))
        self.assertGreaterEqual(plan["soft"], plan["estimate"], "the soft bound is never under the estimate")
        big = km._doc_worker_bounds(4 << 30)
        self.assertEqual(big, (6 << 30, (6 << 30) - (256 << 20)), "a large job's guard is 256 MiB")
        least = km._doc_worker_bounds(km._doc_worker_estimate(0, 0))
        self.assertGreaterEqual(least[0] - least[1], 32 << 20, "and the smallest job's is no less than an allocator asks of "
                                                                 "the OS at a time (a free-threaded build's: 32 MiB)")

    def test_small_records_are_refused_where_a_factor_of_the_bytes_would_have_admitted(self):
        """At 3.3 x the bytes this transcript would need about a megabyte; a parse holds kilobytes a record, so it needs a
        hundred times that, and the admission refuses the job at the old figure's room."""
        self.session()
        self.switch("on")
        self.mem = int(1.5 * 3.3 * os.path.getsize(self.leaf)) + (1 << 20)
        self.stand_in("crash")
        self.cycle()
        self.assertEqual(self.starts(), [], "refused: %s" % km._doc_worker_stats())
        self.assertEqual(km._doc_worker_stats()["refused"], 1)

    def test_the_admission_and_the_bound_are_one_figure_over_every_file_the_parse_reads(self):
        """A leaf whose parse also reads another file (a fork's anchor): the estimate counts both, the admission refuses a byte
        under its figure and admits at it, and the worker's limit is that same figure."""
        self.session()
        anchor = str(self.proj / (SID3 + ".jsonl"))
        self.write(T.transcript(400, tag="q"), leaf=anchor)
        real = jd.asm_refresh_inputs

        def inputs(fsid, leaf, sdk_human=None, states=None):
            files, states, postal, human = real(fsid, leaf, sdk_human=sdk_human, states=states)
            return files + [anchor], states, postal, human
        self.switch("on")
        self.stand_in("memory-line")                               # (no fallback to run the two-file job here)
        with mock.patch.object(jd, "asm_refresh_inputs", inputs):
            plan = self.plan()
            self.assertEqual(plan["bytes"], os.path.getsize(self.leaf) + os.path.getsize(anchor))
            self.assertEqual((plan["records"], plan["exact"]), (self._lines() + self._lines(anchor), True))
            self.mem = plan["limit"] - 1
            self.cycle()
            self.assertEqual(self.starts(), [], "a byte short of the figure over both files: refused")
            self.mem = plan["limit"]
            self.cycle(NOW + 601)
        jobs = self.jobs()
        self.assertEqual(len(jobs), 1, "admitted at the figure")
        self.assertEqual((jobs[0]["limitBytes"], jobs[0]["softBytes"], jobs[0]["cands"]),
                         (plan["limit"], plan["soft"], [self.leaf, anchor]))


class MemoryVerdicts(Harness):
    def _verdict(self):
        self.session()
        self.switch("on")
        self.stand_in("memory-exit")
        self.cycle()
        self.assertIsNotNone(km._doc_worker_verdict(self.leaf), km._doc_worker_stats())

    def test_a_verdict_is_moot_once_the_transcript_is_replaced(self):
        self._verdict()
        tmp = self.leaf + ".new"
        Path(tmp).write_bytes(Path(self.leaf).read_bytes())
        os.replace(tmp, self.leaf)                                 # the same bytes in a new file
        self.assertIsNone(km._doc_worker_verdict(self.leaf))

    def test_a_verdict_is_moot_under_another_sizing(self):
        self._verdict()
        with mock.patch.object(km, "DOC_WORKER_SIZING_V", km.DOC_WORKER_SIZING_V + 1):
            self.assertIsNone(km._doc_worker_verdict(self.leaf))

    def test_a_verdict_holds_as_the_transcript_grows_and_leaves_with_it(self):
        self._verdict()
        with open(self.leaf, "a") as f:
            f.writelines(json.dumps(r) + "\n" for r in T.transcript(270)[-4:])
        _reset_worker()                                            # read from the file, as a new process does
        self.assertIsNotNone(km._doc_worker_verdict(self.leaf), "a live transcript grows: still the same one")
        f = km._doc_worker_verdict_file(self.leaf)
        os.unlink(self.leaf)
        em.checkpoint_sweep()
        self.assertFalse(f.exists(), "the boot's sweep takes a verdict whose transcript is gone")

    def test_with_the_switch_off_a_verdict_changes_nothing(self):
        self._verdict()
        self.switch("off")
        self._next_life()
        self.cycle(NOW + 601)
        self.assertTrue(self.doc_file().exists(), "the kernel's own road rebuilds it as it always did")
        self.assertEqual(km._doc_worker_stats()["verdictHeld"], 0, "the verdict is never read")

    def test_deleting_the_verdict_lets_a_worker_try_again_at_once(self):
        """No restart: the file is the verdict, statted at every question."""
        self._verdict()
        km._doc_worker_verdict_file(self.leaf).unlink()
        self.assertIsNone(km._doc_worker_verdict(self.leaf))
        self._next_life()
        self.stand_in("crash")
        self.cycle(NOW + 601)
        self.assertEqual(len(self.starts()), 2, "a worker was started for it again")

    def test_the_error_center_says_what_happened_in_plain_words_and_the_log_has_the_path(self):
        self.session()
        self.switch("on")
        self.stand_in("memory-exit")
        out = self.cycle()
        f = str(km._doc_worker_verdict_file(self.leaf))
        rows = [p for p in self.problems if "ran out of memory" in p]
        self.assertEqual(len(rows), 1, self.problems)
        self.assertIn("session %s" % SID[:8], rows[0], "it names the session")
        self.assertIn("nothing needs doing", rows[0])
        for raw in (f, self.leaf, ".asm.memory", str(self.root)):
            self.assertNotIn(raw, rows[0], "no path in the error center")
        self.assertIn("delete %s to let a worker try it again" % f, out, "the log carries it")
        self.mem = 0
        self._next_life()
        km._doc_worker_verdict_file(self.leaf).unlink()
        self.cycle(NOW + 601)
        rows = [p for p in self.problems if "free memory" in p]
        self.assertEqual(len(rows), 1, self.problems)
        self.assertNotIn(str(self.root), rows[0])


class Drift(Harness):
    """A checkout that moved after the kernel started holds a newer event model: a worker started from it writes documents
    this kernel refuses and deletes, and rebuilds them again, until the restart."""

    def test_with_the_checkout_ahead_of_the_kernel_the_job_runs_here_and_is_not_a_loss(self):
        leaf2 = self.session(SID2, tag="m")
        self.session()
        self.switch("on")
        self.stand_in("crash")                                     # a worker that would start, were one asked for
        with mock.patch.object(km, "_restart_pending", lambda checkout=None: True):
            out = self.cycle() + self.cycle(NOW + 601)
        self.assertEqual(self.starts(), [], "no worker runs the checkout's newer code")
        self.assertTrue(self.doc_file().exists() and self.doc_file(leaf2).exists(), "both built here")
        st = km._doc_worker_stats()
        self.assertEqual((st["fallbacks"], st["lostInARow"], st["latched"]), ({"drift": 2}, 0, False), st)
        self.assertEqual(out.count("kernel code this kernel does not run"), 1, "said once per checkout: %s" % out)

    def test_a_checkout_that_cannot_be_compared_is_treated_as_moved(self):
        self.session()
        self.switch("on")
        self.stand_in("crash")
        with mock.patch.object(km, "_restart_pending", lambda checkout=None: None):
            self.cycle()
        self.assertEqual(self.starts(), [])
        self.assertEqual(km._doc_worker_stats()["fallbacks"], {"drift": 1})

    def test_a_worker_that_finds_other_code_refuses_and_the_job_runs_here(self):
        """The race the question before the start cannot close: the checkout moves while the worker starts."""
        self.session()
        self.switch("on")
        self.stand_in("drift-exit")
        self.cycle()
        self.assertEqual(len(self.starts()), 1)
        self.assertTrue(self.doc_file().exists())
        st = km._doc_worker_stats()
        self.assertEqual((st["fallbacks"], st["lostInARow"]), ({"drift": 1}, 0), st)


@unittest.skipUnless(LINUX, "the parent death signal is Linux's")
class TheWorkerProcess(Harness):
    def _job(self, leaf, **over):
        job = {"op": "build", "leaf": leaf, "rompuuid": SID, "sdkHuman": False, "cands": [leaf],
               "states": str(jd.STATE / "states" / (SID + ".jsonl")), "postalLog": str(jd.STATE / "messages.jsonl"), "now": NOW,
               "rebuild": "noDocument", "ckptDir": str(jd.STATE / "checkpoints"), "softBytes": 0, "limitBytes": 0,
               "parent": os.getpid(), "codeIdent": km._code_ident()}
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
        self.assertGreater(done["vmBase"], 0, "what it held as the parse began")
        self.assertGreater(done["rssBase"], 0)
        self.assertGreaterEqual(done["vmPeak"], done["vmBase"])
        self.assertGreater(done["vmHWM"], done["rssBase"], "the parse added memory")

    def _probe(self, limit, held):
        p = subprocess.run([sys.executable, "-c", PROBE, str(Path(WORKER).resolve()), str(limit), str(held)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=km._doc_worker_env(), timeout=120)
        self.assertEqual(p.returncode, 0, p.stderr.decode())
        return json.loads(p.stdout.decode().splitlines()[-1])

    def test_the_limit_stops_a_heap_that_outgrows_it_counted_from_the_baseline(self):
        """The limit is the baseline plus what the job may add, and it binds: a heap of small objects is stopped by a failed
        allocation near the limit, in the environment the kernel gives a worker, with nothing held before the job and with
        a gigabyte of address space held. On a free-threaded build this is what the allocator setting is for: with its
        reservation on, the address space stands still for a gigabyte of growth and the heap is never stopped here. The
        soft bound reads the same growth: past it at an eighth of the limit, within it at four times, whatever was held
        before."""
        limit = 64 << 20
        for held in (0, 1 << 30):
            with self.subTest(held=held):
                got = self._probe(limit, held)
                self.assertTrue(got["set"])
                self.assertGreater(got["base"][0], held)
                self.assertEqual(got["rlimit"], got["base"][0] + limit, "the baseline plus the job's")
                self.assertTrue(got["stopped"], "a failed allocation stopped the heap short of three times the limit: %s" % got)
                self.assertLessEqual(got["grew"], limit + (64 << 20), "and near the limit: %s" % got)
                self.assertGreaterEqual(got["grew"], limit // 4, "having had room the limit names (the allocator takes it "
                                                                   "from the OS up to 32 MiB at a time): %s" % got)
                self.assertIn("past its bound of 8 MB", got["eighth"] or "", got)
                self.assertTrue(got["eighth"].startswith("memory: "), "the kernel reads a memory failure by this prefix")
                self.assertIsNone(got["four"], "within a bound four times the limit, whatever the process held before")
                self.assertIn("could not be read", got["unread"], "a baseline that cannot be read is not within the bound")

    def test_a_job_handed_a_limit_does_not_run_when_its_baseline_cannot_be_read(self):
        probe = ("import importlib.util, sys\n"
                 "spec = importlib.util.spec_from_file_location('romp_doc_worker_probe', sys.argv[1])\n"
                 "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
                 "print(mod._limit_address_space(64 << 20, None), mod._limit_address_space(0, None))\n")
        p = subprocess.run([sys.executable, "-c", probe, str(Path(WORKER).resolve())], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        self.assertEqual(p.stdout.decode().split(), ["False", "True"], p.stderr.decode())
        self.assertIn("the address-space limit could not be set", p.stderr.decode())

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


    def test_a_job_from_a_kernel_running_other_code_is_refused(self):
        self.write(T.transcript(260))
        for ident in ("0123456789ab", ""):
            with self.subTest(ident=ident):
                p = self._run((json.dumps(self._job(self.leaf, codeIdent=ident)) + "\n").encode())
                self.assertEqual((p.returncode, p.stdout), (km.DOC_WORKER_EXIT_DRIFT, b""), p.stderr.decode())
                self.assertIn("is not the kernel's", p.stderr.decode())
                self.assertFalse(self.doc_file().exists())

    def test_the_worker_hashes_the_kernel_code_as_the_kernel_does(self):
        """The worker's recipe, read from the worker's own module in a process of its own, against the kernel's."""
        root = str(Path(WORKER).resolve().parent.parent)
        probe = ("import importlib.util, sys\n"
                 "spec = importlib.util.spec_from_file_location('romp_doc_worker_probe', sys.argv[1])\n"
                 "mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)\n"
                 "print(mod._code_ident_of(sys.argv[2]), mod.EXIT_DRIFT, mod.EXIT_MEMORY)\n")
        p = subprocess.run([sys.executable, "-c", probe, str(Path(WORKER).resolve()), root], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=60)
        ident, drift, memory = p.stdout.decode().split()
        self.assertEqual(ident, km._code_ident_of(root)[0], p.stderr.decode())
        self.assertEqual(ident, km._code_ident(), "and the code this kernel booted is the code on disk")
        self.assertEqual((int(drift), int(memory)), (km.DOC_WORKER_EXIT_DRIFT, km.DOC_WORKER_EXIT_MEMORY))


if __name__ == "__main__":
    unittest.main()
