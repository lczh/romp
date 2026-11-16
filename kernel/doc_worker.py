#!/usr/bin/env python3
"""romp-doc-worker: build ONE session's snapshot document in a process of its own (plans/process-split.md, stage two; its
first step, 2026-11-12).

A document rebuild is a whole parse of a transcript and a document write: seconds to minutes of interpreter time that every
page, the pusher and the judges wait behind when it runs on a thread of the kernel, whose one interpreter runs one thread at
a time. With STATE/doc-worker reading `on` the kernel starts this script instead (kernel.py, _doc_worker_run), in a fresh
interpreter, spawned and never a fork of the kernel, and the two talk in two lines:

  stdin   one JSON line, the job: {"op": "build", "leaf", "rompuuid", "sdkHuman", "cands", "states", "postalLog", "now",
          "rebuild", "ckptDir", "softBytes", "limitBytes", "parent", "codeIdent"}; the arguments the kernel's own road
          hands em.asm_refresh_document, the checkpoint directory, the memory bounds, the kernel's pid and the identity
          of the kernel code it runs
  stdout  one JSON line, the outcome: {"op": "done", "pid", "outcome", "wrote", "reason", "tb", "newPre", "docCut", "ms",
          "vmBase", "rssBase", "vmPeak", "vmHWM", "memory"}; em.asm_build_document's result plus what the job cost: the
          address space and resident memory this process held as the parse began, and their peaks

The build is em.asm_build_document, the same function the kernel's own road runs: a private whole parse, then the
document's writer. What crosses back is the document on disk and that one line, never the parse (a parse is a tree of
dictionaries, and pickling it costs about what decoding the JSON did). The kernel restores from the document at the leaf's
next parse, as it does after any writer.

What this process does NOT do: it writes no fold checkpoint (the kernel stays their only writer: set_document_stop makes
every drop a hand-off, and no document writer runs here to take one), installs nothing a later job could reuse (one job, then
it leaves), and never prints anything but the done line on its stdout (fd 1 is kept for the protocol; a stray print goes to
stderr, which is the kernel's).

It ends with the kernel: PR_SET_PDEATHSIG, requested HERE as the first thing after the job is read, never between a fork
and an exec in the kernel (a preexec_fn there forces a real fork of a many-gigabyte, many-threaded process). The request
is tied to the thread that started this process, the kernel's rebuild thread, which waits for the done line.

Memory. The kernel admits a job only when the machine has room for it, and hands two bounds, both on what the JOB adds:
they count from this process's own baseline (_baseline: its address space and resident memory with the kernel code
loaded, read as the parse is about to begin). What an interpreter holds before any job is the interpreter's, and it
differs by build: about 40 MB of address space on a default CPython 3.15, about 1,060 MB on a free-threaded one, whose
allocator reserves 1 GiB of it before anything asks (2026-11-16: counted from zero, the bounds refused every job there).
`limitBytes` is how far the address space may grow past the baseline (RLIMIT_AS at their sum), the backstop: past it an
allocation fails. A failed allocation is a MemoryError, and the parser, written for a process with no such limit, has
places that read ANY error on a record as a malformed line and skip it, so a document built after one could be missing
history and still look whole. `softBytes`, below the limit by a margin no single allocation of the parse approaches,
closes that: at the replace, the last moment before a document is published, growth past `softBytes` publishes nothing
and the job reports `failed` with `memory` set. Growth is the address space's peak over its baseline or the resident
peak over its own, whichever is larger: address space is what the limit counts, so it is what says an allocation may
have failed, and resident memory is what the job took from the machine, which an allocator that reserves address space
ahead of use shows nowhere else. A MemoryError that surfaces is reported the same way, and one outside the build leaves
through exit code EXIT_MEMORY. The process also asks to be the first one taken if the machine itself runs out
(oom_score_adj), so that it is this process, never the kernel.

The limit binds because the kernel starts this process with MIMALLOC_ARENA_RESERVE=0 (kernel.py, _doc_worker_env). The
allocator of a free-threaded build (mimalloc) otherwise takes address space 1 GiB at a time, and a job then grows by
up to that much with its address space standing still: measured, a limit of 200 MB over the baseline stopped a heap at
1,173 MB there, and at 213 MB with the reservation off, where the address space grows 32 MiB at a time as the job
does (199 MB on a default build, which the variable does not touch).

The same code as the kernel's. A checkout that moved after the kernel started holds a newer event model, whose documents
the running kernel refuses and deletes, and a worker started from it would rebuild them until the restart. So after it
loads the event model, this process hashes kernel/*.py as the kernel hashes its own at import (_code_ident_of here and in
kernel.py, pinned equal by the tests) and refuses the job when that differs from the job's `codeIdent`: hashed after the
load, a move at any moment reads as a difference, never the other way round. The kernel asks first (_restart_pending), so
this is the race's backstop.

Exit codes: 0 after a done line, whatever the outcome; EXIT_MEMORY (86) for a memory failure outside the build;
EXIT_DRIFT (87) for a job refused for other code; anything else with no done line is a crash to the kernel, which runs
the job on its own thread and counts the fallback."""
import hashlib
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EXIT_MEMORY = 86                  # no done line, and the cause was memory: the kernel does not repeat the job in its own process
EXIT_REFUSED = 2                  # no done line: a job this process could not take (unreadable, no death signal, no limit)
EXIT_DRIFT = 87                   # no done line: the kernel code this process loaded is not the kernel's (see above)


def _protocol_out():
    """The stream the done line goes to: the process's stdout as it was handed, under a new descriptor. fd 1 and sys.stdout
    then point at stderr, so nothing the parse or an import prints can land in the kernel's pipe ahead of the line."""
    out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    return out


def _say(text):
    try:
        sys.stderr.write("romp-doc-worker: %s\n" % text)
        sys.stderr.flush()
    except Exception:
        pass


def _die_with_parent(parent):
    """Ask the OS to end this process (SIGTERM) when the thread that started it ends (Linux: PR_SET_PDEATHSIG = 1), then
    check the parent is still the one the job names: one that left before the request was in place would never be noticed.
    SIGTERM is also how the kernel ends a job on purpose, so this process first makes sure it takes one: the default
    disposition, unblocked (an ignored SIGTERM and a blocked one are both inherited across exec, from whatever started
    the kernel). False when the request cannot be made or the parent is already gone."""
    try:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, int(signal.SIGTERM), 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "prctl(PR_SET_PDEATHSIG) refused")
    except Exception as e:
        _say("no parent death signal on this platform (%s: %s); the job is the kernel's own to run" % (type(e).__name__, e))
        return False
    if parent and os.getppid() != int(parent):
        _say("the kernel (pid %s) left before the job began" % parent)
        return False
    return True


def _background():
    """The lowest CPU priority, as the kernel's own rebuild thread runs, and the first process the OS takes if the machine
    runs out of memory. Both best effort: neither changes what is built."""
    try:
        os.nice(19)
    except OSError:
        pass
    try:
        with open("/proc/self/oom_score_adj", "w", encoding="ascii") as f:
            f.write("1000")
    except OSError:
        pass


def _limit_address_space(nbytes, base):
    """RLIMIT_AS at `base` + `nbytes`: the address space this process holds already, plus what the job may add (never above
    a limit already in force). False when it cannot be set, or the baseline was not read: a job handed a limit does not
    run without one."""
    if not nbytes:
        return True
    try:
        import resource
        if base is None:
            raise OSError("this process's address space could not be read")
        soft, hard = resource.getrlimit(resource.RLIMIT_AS)
        want = int(base) + int(nbytes)
        for cur in (soft, hard):
            if cur != resource.RLIM_INFINITY:
                want = min(want, int(cur))
        resource.setrlimit(resource.RLIMIT_AS, (want, hard))
        return True
    except Exception as e:
        _say("the address-space limit could not be set (%s: %s)" % (type(e).__name__, e))
        return False


def _vm_bytes(field):
    """A Vm* field of /proc/self/status in bytes (VmSize and VmPeak: the address space and its peak; VmRSS and VmHWM: the
    resident memory and its peak), None when it cannot be read."""
    try:
        with open("/proc/self/status", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith(field + ":"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _baseline():
    """What this process holds before the job's parse, in bytes: (its address space, its resident memory), each None when it
    cannot be read. The job's bounds count from here (Memory, above), so it is read after everything the process loads
    whatever the job, and it is the size now, never a peak: a peak the loading left behind is then counted as growth,
    which errs toward publishing nothing."""
    return _vm_bytes("VmSize"), _vm_bytes("VmRSS")


def _over_soft_bound(soft, base):
    """The reason this process must publish nothing, or None: since the baseline (`base`, _baseline's pair) its address space
    or its resident memory grew, at its peak, past the soft bound (or a figure cannot be read, which a job handed a bound
    treats the same way)."""
    if not soft:
        return None
    soft = int(soft)
    grown = (("address space", _vm_bytes("VmPeak"), base[0]), ("resident memory", _vm_bytes("VmHWM"), base[1]))
    for what, peak, start in grown:
        if peak is None or start is None:
            return "memory: the worker's %s could not be read, so its bound of %.0f MB cannot be shown to hold" % (
                what, soft / 1048576.0)
    for what, peak, start in grown:
        if peak - start > soft:
            return "memory: the worker's %s grew by %.0f MB over the %.0f MB it began the job with, past its bound of %.0f MB" % (
                what, (peak - start) / 1048576.0, start / 1048576.0, soft / 1048576.0)
    return None


def _code_ident_of(root):
    """The identity of the kernel code under `root`/kernel: a short sha1 over the bytes of kernel/*.py in path order, the
    recipe of kernel.py's _code_ident_of (the tests pin the two equal), or "" when it cannot be read."""
    h = hashlib.sha1()
    try:
        files = sorted(Path(root, "kernel").glob("*.py"))
        for f in files:
            h.update(f.name.encode())
            h.update(f.read_bytes())
    except OSError:
        return ""
    return h.hexdigest()[:12] if files else ""


def _event_model():
    spec = importlib.util.spec_from_file_location("romp_loadsource", str(HERE / "loadsource.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.load_source("romp_event_model", HERE / "event_model.py")


def run(job, out):
    """Build the job's document and print the done line. Returns the exit code."""
    t0 = time.perf_counter()
    em = _event_model()
    mine = _code_ident_of(HERE.parent)                    # after the load: a checkout that moved at any moment differs
    if not job.get("codeIdent") or mine != job["codeIdent"]:
        _say("the kernel code here (%s) is not the kernel's (%s): the job is the kernel's own to run"
             % (mine or "unreadable", job.get("codeIdent") or "none named"))
        return EXIT_DRIFT
    ckpt = Path(job["ckptDir"])
    em.set_checkpoint_dir(lambda: ckpt)
    em.set_document_stop(lambda: True)                    # no fold checkpoint is written here: the kernel is their only writer
    base = _baseline()                                    # the code is loaded, the parse not begun: the bounds count from here
    if not _limit_address_space(job.get("limitBytes"), base[0]):
        return EXIT_REFUSED
    soft = job.get("softBytes")
    res = em.asm_build_document(job["leaf"], job["rompuuid"], bool(job.get("sdkHuman")),
                                candidate_files=list(job.get("cands") or [job["leaf"]]), states=job.get("states"),
                                postal_log=job.get("postalLog"), now=job.get("now"), rebuild=job.get("rebuild"),
                                refuse=(lambda: _over_soft_bound(soft, base)) if soft else None)
    reason = res.get("reason") or ""
    done = dict(res, op="done", pid=os.getpid(), ms=round((time.perf_counter() - t0) * 1000.0, 1),
                vmBase=base[0], rssBase=base[1], vmPeak=_vm_bytes("VmPeak"), vmHWM=_vm_bytes("VmHWM"),
                memory=bool(res.get("outcome") == "failed" and (reason.startswith("MemoryError") or reason.startswith("memory:"))))
    out.write(json.dumps(done, separators=(",", ":")) + "\n")
    out.flush()
    return 0


def main():
    out = _protocol_out()
    try:
        job = json.loads(sys.stdin.readline())
        if not (isinstance(job, dict) and job.get("op") == "build" and job.get("leaf") and job.get("rompuuid")
                and job.get("ckptDir")):
            raise ValueError("not a build job")
    except Exception as e:
        _say("no job on stdin (%s: %s)" % (type(e).__name__, e))
        return EXIT_REFUSED
    if not _die_with_parent(job.get("parent")):
        return EXIT_REFUSED
    _background()
    try:
        return run(job, out)
    except MemoryError:
        return EXIT_MEMORY


if __name__ == "__main__":
    _code = main()
    try:
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(_code)               # the heap of a whole parse is not walked on the way out: the done line is already flushed
