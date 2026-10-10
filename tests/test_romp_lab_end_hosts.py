#!/usr/bin/env python3
"""A lab's cleanup ends the lab's own session hosts and their CLIs, and nothing else (2026-10-08).

A per-session host outlives the kernel that spawned it by design and starts in a session of its own, so a
`--session-hosts` lab whose cleanup killed only its kernel left the hosts running: an idle one for its unattached grace
(900 s), and a CLI mid-turn finishing that turn, spending, after the lab directory was deleted. tools/romp-lab/end-hosts.py
reads the lab's own state root (each host's identity.json, each host-held lease) and signals a process only while its
start time still matches the one recorded there; lab.sh's cleanup runs it on the lab's root once the kernel is down.

Real processes stand in for a host and its CLI (a host leads its own process group, its CLI runs in it), beside decoys
the sweep must leave alone: an unrelated process, a lease naming that process's pid with another start time (a reused
pid), and a kernel-held lease. Synthetic ids only.
"""
import contextlib
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOL = ROOT / "tools" / "romp-lab" / "end-hosts.py"
LAB_SH = ROOT / "tools" / "romp-lab" / "lab.sh"
SID_HOST = "11111111-2222-3333-4444-0000000e0d01"
SID_REUSED = "11111111-2222-3333-4444-0000000e0d02"
SID_KERNEL = "11111111-2222-3333-4444-0000000e0d03"

from romp_load import load_source  # noqa: E402  (above the state preamble: the direct run's temp-root floor lands here)

# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor

sb = load_source("romp_sdk_backend_end_hosts", ROOT / "kernel" / "sdk_backend.py")
ht = load_source("romp_host_transport_end_hosts", ROOT / "kernel" / "host_transport.py")

HOST_PY = ("import subprocess, sys, time\n"
           "c = subprocess.Popen(['sleep', '300'])\n"
           "open(sys.argv[1], 'w').write(str(c.pid))\n"
           "time.sleep(300)\n")
STUBBORN_PY = ("import signal, sys, time\n"
               "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
               "open(sys.argv[1], 'w').write('up')\n"
               "time.sleep(300)\n")
DYING_PY = ("import signal, sys, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
            "signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})\n"
            "open(sys.argv[1], 'w').write('up')\n"
            "time.sleep(300)\n")
# The launcher starts the process in a session of its own and exits, so the process is an orphan the way a host is
# once its kernel is gone (its parent, init or the user manager, reaps it), never a child this test would have to reap.
# It gives the process SIGTERM's default disposition, unblocked, as a real host has (the kernel that spawns it handles
# SIGTERM, and a handled signal is reset at exec): an ignored SIGTERM and a blocked one are both inherited across exec,
# and a CI runner hands its steps one or the other.
LAUNCH_PY = ("import signal, subprocess, sys\n"
             "signal.signal(signal.SIGTERM, signal.SIG_DFL)\n"
             "signal.pthread_sigmask(signal.SIG_UNBLOCK, {signal.SIGTERM})\n"
             "p = subprocess.Popen(sys.argv[1:], start_new_session=True, stdin=subprocess.DEVNULL,\n"
             "                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
             "print(p.pid)\n")


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:                                                   # a zombie awaiting its reaper has ended
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return True


def wait_for(path, within=10.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if path.exists() and path.read_text():
            return path.read_text()
        time.sleep(0.02)
    raise AssertionError("%s never appeared" % path)


@unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "POSIX process groups")
class EndingTheLabsOwnHosts(unittest.TestCase):

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name) / "state" / "romp"
        self.groups = []

    def tearDown(self):
        for pgid in self.groups:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.td.cleanup()

    def _orphan(self, *argv):
        out = subprocess.run([sys.executable, "-c", LAUNCH_PY, *argv], capture_output=True, text=True, timeout=30, check=True)
        pid = int(out.stdout)
        self.groups.append(pid)
        return pid

    def _write(self, rel, obj):
        path = self.state / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj))

    def _world(self):
        mark = Path(self.td.name) / "cli.pid"
        host = self._orphan(sys.executable, "-c", HOST_PY, str(mark))
        cli = int(wait_for(mark))
        decoy = self._orphan("sleep", "300")
        hstart, cstart, dstart = sb.proc_start(host), sb.proc_start(cli), sb.proc_start(decoy)
        self.assertTrue(hstart and cstart and dstart)
        hdir = ht.host_dir(self.state, SID_HOST)
        self._write(hdir.relative_to(self.state) / "identity.json", {"pid": host, "start": hstart})
        self._write(Path(sb.LEASE_DIR) / (SID_HOST + ".json"), {
            "sid": SID_HOST, "pid": cli, "start": cstart, "holder": {"pid": host, "start": hstart, "kind": "host"}})
        # a reused pid: the decoy's pid under a start time it does not have
        self._write(Path(sb.LEASE_DIR) / (SID_REUSED + ".json"), {
            "sid": SID_REUSED, "pid": decoy, "start": "1", "holder": {"pid": decoy, "start": "1", "kind": "host"}})
        # a kernel-held lease naming a live process: the lab kernel's own CLI, never a host's
        self._write(Path(sb.LEASE_DIR) / (SID_KERNEL + ".json"), {
            "sid": SID_KERNEL, "pid": decoy, "start": dstart, "holder": {"pid": decoy, "start": dstart, "kind": "kernel"}})
        return host, cli, decoy

    def _run(self):
        return subprocess.run([sys.executable, str(TOOL), str(self.state)], capture_output=True, text=True, timeout=60)

    def _gone(self, *pids, within=10.0):
        deadline = time.monotonic() + within
        while time.monotonic() < deadline and any(alive(p) for p in pids):
            time.sleep(0.05)
        return not any(alive(p) for p in pids)

    def test_the_layout_it_reads_is_the_backends(self):
        self.assertEqual(sb.LEASE_DIR, "leases", "end-hosts.py reads leases/<sid>.json")
        self.assertEqual(ht.host_dir(self.state, SID_HOST), self.state / "hosts" / SID_HOST, "and hosts/<sid>/identity.json")
        host_src = (ROOT / "kernel" / "session_host.py").read_text()
        self.assertIn("self.dir = self.spec_path.parent", host_src, "a host's directory is its spawn spec's (hosts/<sid>/)")
        self.assertIn('(self.dir / "identity.json").write_text(json.dumps({"pid": os.getpid(), "start": '
                      'self.lease_api["proc_start"](os.getpid())', host_src, "where it writes its pid and start time")

    def test_a_host_and_its_cli_are_ended_and_nothing_else(self):
        host, cli, decoy = self._world()
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._gone(host, cli), "the lab's host and its CLI are ended\n" + r.stdout + r.stderr)
        self.assertEqual(r.stdout.strip(), "lab hosts: ended 1 host(s) and 1 CLI(s)", "on SIGTERM, no SIGKILL needed")
        self.assertTrue(alive(decoy), "an unrelated process, a reused pid and a kernel-held lease's process are left alone")

    def test_an_empty_root_is_a_quiet_no_op(self):
        r = self._run()
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "lab hosts: none"))

    def test_a_host_that_ignores_sigterm_is_killed(self):
        mark = Path(self.td.name) / "up"
        host = self._orphan(sys.executable, "-c", STUBBORN_PY, str(mark))
        wait_for(mark)
        self._write(Path("hosts") / SID_HOST / "identity.json", {"pid": host, "start": sb.proc_start(host)})
        r = self._run()
        self.assertTrue(self._gone(host), r.stdout + r.stderr)
        self.assertIn("needed SIGKILL", r.stdout)


@unittest.skipUnless(sys.platform.startswith("linux"), "procfs: the two reads a reaper can land between")
class AReapBetweenTheTwoReads(unittest.TestCase):
    """A process that ends on SIGTERM is reaped by its parent at a moment the sweep does not choose, and the sweep's
    liveness check reads twice: the start time, then the state. A reap landing between the two reads means the
    process has ended. Read as running, it made the sweep report "(1 needed SIGKILL)" for a host that had ended on
    SIGTERM: the CI failure of test_a_host_and_its_cli_are_ended_and_nothing_else in eight Python jobs from 2026-10-08
    to 10-10, in runs that took 0.2 s, so the 5 s grace was never waited out. Here the reap lands there every time.

    The stand-in is this test's own child, so it stays a zombie until the test reaps it. The tool runs in-process with
    its backend's proc_start wrapped: once the tool has signalled the child, each look first waits for the child to
    finish dying (a zombie, unreaped), and the look after the one that met the zombie has the reap land between its
    start-time read and the tool's state read."""

    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.state = Path(td.name) / "state" / "romp"
        mark = Path(td.name) / "up"
        self.child = subprocess.Popen([sys.executable, "-c", DYING_PY, str(mark)], start_new_session=True,
                                      stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(self._end_child)
        wait_for(mark)
        identity = self.state / "hosts" / SID_HOST / "identity.json"
        identity.parent.mkdir(parents=True)
        identity.write_text(json.dumps({"pid": self.child.pid, "start": sb.proc_start(self.child.pid)}))

    def _end_child(self):
        if self.child.returncode is None:              # not reaped yet: it is still this test's child, so its pid is too
            try:
                os.killpg(self.child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.child.wait()

    def _until_ended(self, pid, within=10.0):
        """Wait for the child to become a zombie, without reaping it (WNOWAIT)."""
        deadline = time.monotonic() + within
        while os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT | os.WNOHANG) is None:
            if time.monotonic() > deadline:
                raise AssertionError("the stand-in never ended on SIGTERM")
            time.sleep(0.01)

    def _sweep(self, between_the_reads, tool_open=None):
        """Run end-hosts.py's main on the state root, calling `between_the_reads` after the start-time read of the look
        that follows the one that met the zombie. Returns what the tool printed."""
        pid, signalled, looks = self.child.pid, [], []
        real_kill, real_killpg = os.kill, os.killpg

        def kill(p, sig):
            if p == pid and sig:
                signalled.append(sig)
            return real_kill(p, sig)

        def killpg(g, sig):
            if g == pid:
                signalled.append(sig)
            return real_killpg(g, sig)

        def proc_start(p):
            if p == pid and signalled:
                self._until_ended(pid)
            start = sb.proc_start(p)
            if p == pid and signalled:
                looks.append(start)
                if len(looks) == 2:
                    between_the_reads()
            return start

        spec = importlib.util.spec_from_file_location("romp_end_hosts_reap_race", TOOL)
        tool = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tool)
        if tool_open is not None:
            tool.open = tool_open                      # a module global: the tool's own open() calls find it first
        backend = types.SimpleNamespace(proc_start=proc_start)
        loadsource = types.ModuleType("loadsource")
        loadsource.load_source = lambda name, path: backend
        out = io.StringIO()
        with mock.patch.dict(sys.modules, {"loadsource": loadsource}), mock.patch.object(sys, "path", list(sys.path)), \
                mock.patch.object(os, "kill", kill), mock.patch.object(os, "killpg", killpg), contextlib.redirect_stdout(out):
            rc = tool.main(["end-hosts.py", str(self.state)])
        self.assertEqual(rc, 0)
        self.assertEqual(signalled[:1], [signal.SIGTERM], "the tool sent the stand-in SIGTERM first")
        self.assertGreaterEqual(len(looks), 2, "the tool looked again after meeting the zombie, so the reap was placed")
        return out.getvalue().strip()

    def test_a_process_reaped_between_the_reads_ended_on_sigterm(self):
        line = self._sweep(self.child.wait)            # its parent reaps it: the second read finds no process
        self.assertEqual(self.child.returncode, -signal.SIGTERM, "SIGTERM ended it")
        self.assertEqual(line, "lab hosts: ended 1 host(s) and 0 CLI(s)", "so no SIGKILL was needed")

    def test_a_process_caught_mid_reap_has_ended(self):
        # Inside the reap itself the state reads X (dead, its reaper collecting it). No test can hold a real reaper
        # there, so the tool's state read after the placed moment is shown X for the zombie it is.
        stat, mid_reap = "/proc/%d/stat" % self.child.pid, []

        def tool_open(path, *args, **kwargs):
            if path == stat and mid_reap:
                mid_reap.clear()
                with open(path) as f:
                    head, tail = f.read().rsplit(")", 1)
                fields = tail.split()
                self.assertEqual(fields[0], "Z", "the stand-in is a zombie at the placed moment")
                return io.StringIO("%s) X %s\n" % (head, " ".join(fields[1:])))
            return open(path, *args, **kwargs)

        line = self._sweep(lambda: mid_reap.append(True), tool_open)
        self.assertEqual(mid_reap, [], "the tool's state read after the placed moment was shown X")
        self.assertEqual(line, "lab hosts: ended 1 host(s) and 0 CLI(s)", "a process mid-reap has ended; no SIGKILL needed")


class TheLabsCleanupRunsIt(unittest.TestCase):
    def test_cleanup_ends_the_labs_hosts_after_the_kernel_and_before_the_directory_goes(self):
        src = LAB_SH.read_text()
        body = re.search(r"^cleanup\(\) \{\n(.*?)^\}", src, re.S | re.M)
        self.assertIsNotNone(body, "lab.sh has a cleanup function")
        body = body.group(1)
        kill, sweep = body.find('kill "$KPID"'), body.find('end-hosts.py" "$LAB/state/romp"')
        rm = body.find('rm -rf "$LAB"')
        self.assertTrue(0 <= kill < sweep < rm, "the kernel first (it spawns hosts), then the lab's own hosts, then the directory:\n" + body)
        self.assertIn("trap cleanup EXIT", src)


if __name__ == "__main__":
    unittest.main()
