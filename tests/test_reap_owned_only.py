#!/usr/bin/env python3
"""A booting kernel reaps only what it can prove it started (2026-09-23).

systemd user units and processes are machine-wide, and a session id is not a kernel's. A lab kernel with a state
directory of its own held a session under the same id as a live session of the machine's real kernel, and each boot of
the lab kernel stopped the live session's host (its CLI exited on SIGTERM), because all three boot reapers decided
ownership from the session id plus the booting kernel's OWN state directory:
  * the orphan CLI reap (lease_census, then _end_cli_tree): any stream-json `claude` on the machine resuming one of our
    ids, with no lease in our directory and no kernel for a parent — a host-held CLI of another kernel is exactly that;
  * the leftover session-scope sweep: every `romp-session-<sid8>-*.scope` on the machine whose CLI we do not own;
  * the leftover host-scope sweep: every `romp-host-<sid8>-*.scope` with no valid lease in our directory.
Now every session CLI carries its kernel's state tag in its environment (ROMP_STATE_TAG) and every session or host scope
carries `romp-state=<tag>` in its Description, and a boot reaps only what carries its own tag. Another kernel's tag is
left alone; no tag at all (an older build's unit or CLI) is left alone too, and one log line names everything left.
A session whose conversation a spared process still holds is not resumed by that boot either (its resume would put a
second CLI on the transcript, two writers on one conversation): it stays down, said once, whatever its state tail says.

Synthetic fixtures only: placeholder session ids, fake pids above pid_max, injected listings and stop calls, and every
signal recorded, never sent. Nothing here lists, creates or stops a real systemd unit, and every backend's state root
has session hosts off. What the tests do read or run on the machine includes this test's own pid in each live host
lease (a live host needs a live holder), three short-lived children the environment-tag test reads, and the kernel's
sweep of dead test roots in the temporary directory, which each boot starts.
"""
import contextlib
import hashlib
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic BEFORE the load
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_CLI_SCOPE"] = "0"
sb = load_source("romp_sdk_backend_reap_owned", os.path.join(BIN, "romp_sdk_backend.py"))

SID = "11111111-2222-3333-4444-5555aaaa0923"          # the one session id both kernels hold
SID2 = "11111111-2222-3333-4444-5555bbbb0923"         # a second session of the booting kernel, held by nobody else
LINUX = sys.platform.startswith("linux") and os.path.isdir("/proc")


def _pid_max() -> int:
    try:
        return int(open("/proc/sys/kernel/pid_max").read().strip())
    except (OSError, ValueError):
        return 4194304


P = _pid_max()     # fake pids above pid_max: no real process can wear one
MANAGER = P + 1                     # the `systemd --user` subreaper an orphan re-parents to
HOST_A, CLI_A = P + 10, P + 11      # kernel A's live session: its host, and the CLI the host holds
CLI_B = P + 20                      # kernel B's own orphan from its earlier life
CLI_U = P + 30                      # an orphan-shaped CLI whose environment carries no tag (an older build's)
GONE = P + 40                       # a CLI that is gone, its scope still loaded


def _tag(d) -> str:
    """The state tag, written out: the first 16 hex digits of the SHA-256 of the resolved state directory. It is a
    contract across kernel versions (a kernel must recognise the units its previous build made), so it is pinned here
    by hand rather than read back from the code."""
    return hashlib.sha256(os.path.realpath(str(d)).encode()).hexdigest()[:16]


def _root() -> str:
    d = tempfile.mkdtemp()
    Path(d, "session-hosts").write_text("off")   # no real bin/romp-session-host from any backend in this file
    return d


def _reg(d, sid, name="api"):
    r = {"sid": sid, "name": name, "cwd": "/tmp", "alive": True, "lastSid": sid}
    sb.write_reg(Path(d), sid, r)
    return r


def _cli(pid, ppid, sid=SID):
    return "  %d %d /x/claude --output-format stream-json --resume %s --input-format stream-json" % (pid, ppid, sid)


def _scope_line(unit, desc):
    return "%s loaded active running %s" % (unit, desc)


class Boot:
    """One boot of a backend over `d`, against a scripted machine: `ps` (PS_ARGV lines), the two scope listings, the
    CLIs' environment tags and the processes' start times. Records every systemctl stop, every signal, every log
    line and every session the boot went on to start (`started`: the sids handed to _ensure, which is stubbed); nothing
    reaches a real process or unit. `sids` are the registry rows the boot reconciles, SID alone by default."""

    def __init__(self, d, ps, sessions, hosts, tags, starts=None, sids=(SID,)):
        self.d, self.ps, self.sessions, self.hosts = d, ps, sessions, hosts
        self.tags, self.starts, self.sids = tags, dict(starts or {}), tuple(sids)
        self.runs, self.killed, self.said, self.started = [], [], [], []
        self.ps_fault = None        # an exception the process listing raises instead of answering (a ps that cannot run)

    def run(self, argv, **kw):
        self.runs.append(list(argv))
        out = ""
        if argv == sb.PS_ARGV and self.ps_fault is not None:
            raise self.ps_fault
        if argv == sb.PS_ARGV:
            out = "\n".join(self.ps) + "\n"
        elif argv == sb.SCOPE_LIST_ARGV:
            out = "\n".join(self.sessions) + "\n"
        elif argv == sb.HOST_SCOPE_LIST_ARGV:
            out = "\n".join(self.hosts) + "\n"
        return mock.Mock(stdout=out, stderr="", returncode=0)

    def _machine(self):
        return [
            mock.patch.object(sb.subprocess, "run", side_effect=self.run),
            mock.patch.object(sb.os, "kill", side_effect=lambda p, s: self.killed.append((p, s))),
            mock.patch.object(sb.os, "killpg", side_effect=lambda p, s: self.killed.append((p, s))),
            mock.patch.object(sb, "proc_start", lambda p, run=None: self.starts.get(p)),
            mock.patch.object(sb, "proc_state_tag", lambda p, **k: self.tags.get(p), create=True),
            mock.patch.object(sb.SdkBackend, "_pid_alive", lambda self_, p: False),
            mock.patch.object(sb.SdkBackend, "_ensure", lambda self_, sid, **k: self.started.append(sid)),
            # the deferral watch (a boot decision an unreadable lease could not settle) is driven by hand: retake()
            mock.patch.object(sb.SdkBackend, "_lease_watch_start", lambda self_: None)]

    def go(self):
        be = sb.SdkBackend(self.d, "/bin/true", lambda *a, **k: None)
        be._log = lambda m, *a, **k: self.said.append(m)
        with contextlib.ExitStack() as stack:
            for pt in self._machine():
                stack.enter_context(pt)
            be._boot_reconcile([sb.read_reg(Path(self.d), s) for s in self.sids])
        return be

    def retake(self, be):
        """One pass of the deferral watch over `be` (SdkBackend._lease_deferral_once), against the same scripted machine."""
        with contextlib.ExitStack() as stack:
            for pt in self._machine():
                stack.enter_context(pt)
            return be._lease_deferral_once()

    def stops(self):
        return [a[-1] for a in self.runs if a[:3] == ["systemctl", "--user", "stop"]]

    def signaled(self):
        return sorted({p for p, _ in self.killed})


class TwoKernelsShareASessionId(unittest.TestCase):
    """Kernel A (its own state root) runs SID live through a host; kernel B (another state root) holds a copy of SID,
    lease file and all, and boots. B's boot must leave A's CLI, A's session scope and A's host scope alone, and still
    reap its OWN leftovers of the same id from its earlier life."""

    def test_booting_the_second_kernel_never_touches_the_firsts_session_and_still_reaps_its_own(self):
        dA, dB = _root(), _root()
        _reg(dA, SID)
        _reg(dB, SID)
        now = time.time()
        # A's host holds A's CLI; A's lease names them. B's directory holds a COPY of that lease (the lab kernel's copy of
        # the session), whose beat went stale the moment it was copied: by B's reading, a lease that does not hold.
        lease = {"sid": SID, "fsid": SID, "pid": CLI_A, "start": "1000", "version": "",
                 "holder": {"pid": HOST_A, "start": "500", "kind": "host"}, "t": now}
        sb.write_lease(dA, lease)
        sb.write_lease(dB, dict(lease, t=now - 600))
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
              "  %d %d /usr/bin/python3 /x/romp/bin/romp-session-host /x/state/hosts/%s/spawn.json" % (HOST_A, MANAGER, SID),
              _cli(CLI_A, HOST_A),
              _cli(CLI_B, MANAGER)]            # B's own orphan: its kernel died, it re-parented to the user manager
        a_session = "romp-session-%s-%d-1757374800.scope" % (SID[:8], CLI_A)
        a_host = "romp-host-%s-1757374800000.scope" % SID[:8]
        b_session = "romp-session-%s-%d-1757374801.scope" % (SID[:8], CLI_B)
        b_gone = "romp-session-%s-%d-1757374802.scope" % (SID[:8], GONE)      # B's CLI gone, its tool's children live on
        b_host = "romp-host-%s-1757374700000.scope" % SID[:8]                  # B's dead host's scope, still loaded
        sessions = [_scope_line(a_session, "romp session %s romp-state=%s" % (SID, _tag(dA))),
                    _scope_line(b_session, "romp session %s romp-state=%s" % (SID, _tag(dB))),
                    _scope_line(b_gone, "romp session %s romp-state=%s" % (SID, _tag(dB)))]
        hosts = [_scope_line(a_host, "romp session host %s romp-state=%s" % (SID, _tag(dA))),
                 _scope_line(b_host, "romp session host %s romp-state=%s" % (SID, _tag(dB)))]
        boot = Boot(dB, ps, sessions, hosts, tags={CLI_A: _tag(dA), CLI_B: _tag(dB)},
                    starts={CLI_A: "1000", HOST_A: "500"})
        boot.go()
        self.assertNotIn(CLI_A, boot.signaled(), "kernel A's live CLI is never signaled by kernel B's boot")
        self.assertNotIn(HOST_A, boot.signaled())
        self.assertNotIn(a_session, boot.stops(), "kernel A's session scope is never stopped")
        self.assertNotIn(a_host, boot.stops(), "kernel A's host scope is never stopped")
        # …and B's own earlier life is reaped exactly as before
        self.assertEqual(boot.signaled(), [CLI_B], "B's own orphan is ended")
        self.assertEqual(sorted(boot.stops()), sorted([b_session, b_gone, b_host]),
                         "B's orphan's scope, its leftover session scope and its dead host's scope are stopped")
        # no row claims A's CLI was reaped (the copied lease's verdict was 'reaped as an orphan' before)
        rows = [json.loads(l) for l in (Path(dB) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        self.assertFalse([r for r in rows if r.get("cliPid") == CLI_A and "reaped" in (r.get("text") or "")], rows)
        self.assertFalse([r for r in rows if r.get("kind") == "reconcile.orphan-reaped" and r.get("cliPid") == CLI_A])
        # what was left alone is said once, by name
        left = [m for m in boot.said if "left alone" in m]
        self.assertEqual(len(left), 1, boot.said)
        for name in (str(CLI_A), a_host):
            self.assertIn(name, left[0])
        self.assertNotIn(a_session, left[0], "the spared CLI's own scope is spared with it, not named twice")
        self.assertIn("another kernel", left[0])
        self.assertIsNotNone(sb.read_lease(dA, SID), "A's own lease is untouched (B never writes outside its root)")


class AnUnreadableLeaseIsNoEvidence(unittest.TestCase):
    """The boot ends a CLI, and stops a host's scope, for want of a valid lease. A lease file that exists but cannot be read
    is no evidence its host is gone (the rule every lease reader keeps since 2026-10-08): it was skipped as nobody's claim,
    so a booting kernel ended a CLI and stopped the host scope of a session whose live host it simply could not read. Both
    are left alone and named on one line, the session stays down with its CLI, and the lease is left as it stands."""

    def test_the_boot_spares_the_cli_and_the_host_scope_of_a_session_whose_lease_cannot_be_read(self):
        d = _root()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "api", "cwd": "/tmp", "alive": True, "lastSid": SID})
        sb.append_state(Path(d), SID, "working")
        lp = sb.lease_path(d, SID)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text('{"sid": "a lease cut short')                        # not a lease record: write_lease is atomic
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
              "  %d %d /usr/bin/python3 /x/romp/bin/romp-session-host /x/state/hosts/%s/spawn.json" % (HOST_A, MANAGER, SID),
              _cli(CLI_A, HOST_A)]
        host = "romp-host-%s-1757374800000.scope" % SID[:8]
        boot = Boot(d, ps, [], [_scope_line(host, "romp session host %s romp-state=%s" % (SID, _tag(d)))],
                    tags={CLI_A: _tag(d)}, starts={CLI_A: "1000", HOST_A: "500"})
        with mock.patch.object(sb, "proc_session_id", lambda p, **k: {CLI_A: SID}.get(p)):
            boot.go()
        self.assertEqual(boot.signaled(), [], "the CLI of a session whose lease cannot be read is not ended")
        self.assertEqual(boot.stops(), [], "nor its host's scope stopped")
        self.assertEqual(boot.started, [], "and the session is not resumed beside it")
        self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [], "no restart notice")
        self.assertTrue(lp.exists(), "the lease is left as it stands")
        said = [m for m in boot.said if "host lease cannot be read" in m and "left alone" in m]
        self.assertEqual(len(said), 2, boot.said)                            # the CLI's line, and the host scope's
        self.assertIn("claude pid %d" % CLI_A, said[0] + said[1])
        self.assertIn(host, said[0] + said[1])


class AnUnreadableLeaseIsJudgedByItsAge(unittest.TestCase):
    """The review of #150. A lease every holder rewrites each LEASE_HEARTBEAT_S that has gone LEASE_TTL_S without a rewrite
    has no holder behind it, readable or not: a damaged one that old is stale and spares nothing, as a stale lease spares
    nothing. One rewritten within LEASE_TTL_S may still be a live host's, and what it spares is said for what it is, with
    a problem row naming the file, and judged again when the lease reads or goes stale (never left for the next boot)."""

    def _setup(self, age=None):
        d = _root()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "api", "cwd": "/tmp", "alive": True, "lastSid": SID})
        sb.append_state(Path(d), SID, "working")
        lp = sb.lease_path(d, SID)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text('{"sid": "a lease cut short')                        # not a lease record: write_lease is atomic
        if age is not None:
            t = time.time() - age
            os.utime(lp, (t, t))
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
              "  %d %d /usr/bin/python3 /x/romp/bin/romp-session-host /x/state/hosts/%s/spawn.json" % (HOST_A, MANAGER, SID),
              _cli(CLI_A, HOST_A)]
        host = "romp-host-%s-1757374800000.scope" % SID[:8]
        boot = Boot(d, ps, [], [_scope_line(host, "romp session host %s romp-state=%s" % (SID, _tag(d)))],
                    tags={CLI_A: _tag(d)}, starts={CLI_A: "1000", HOST_A: "500"})
        return d, lp, host, boot

    def _go(self, boot):
        with mock.patch.object(sb, "proc_session_id", lambda p, **k: {CLI_A: SID}.get(p)):
            return boot.go()

    @staticmethod
    def _rows(be, boot):
        """Every saying of the lease's episode row: the backend's constructor meets the lease first (its awaiting heal
        reads every alive session's lease, through the log the boot's harness has not replaced yet), the boot after it."""
        return ([r["text"] for r in be.problems() if "cannot read the session host's lease" in r.get("text", "")]
                + [m for m in boot.said if "cannot read the session host's lease" in m])

    def test_a_cli_spared_for_its_lease_holds_its_session_down_with_the_true_reason_and_a_problem_row(self):
        """#150 spared the CLI and then held its session down with the tag rule's line, 'a claude process this kernel did
        not start', false of a CLI carrying this kernel's tag, and before the session's own lease read: no problem row
        said the lease could not be read, and the left-alone line gave no remedy."""
        d, lp, host, boot = self._setup()
        be = self._go(boot)
        self.assertEqual(boot.signaled(), [])
        self.assertEqual(boot.started, [])
        self.assertFalse(any("did not start" in m for m in boot.said), boot.said)
        self.assertTrue(any("stays down: the host lease of %s cannot be read" % SID[:8] in m for m in boot.said), boot.said)
        rows = self._rows(be, boot)
        self.assertEqual(len(rows), 1, "said once for the episode, however many readers met it: %r" % rows)
        self.assertIn(str(lp), rows[0], "the row names the file")
        left = [m for m in boot.said if "left alone" in m and "host lease cannot be read" in m]
        self.assertTrue(left and all("judged again when the lease reads" in m for m in left), left)

    def test_a_stale_damaged_lease_spares_nothing_at_the_boot(self):
        """Nobody has rewritten the file for longer than LEASE_TTL_S: no holder beats behind it. The CLI is the orphan the
        census makes it and is ended, the host's scope is stopped, the cut turn resumes, and the file goes with its CLI,
        as a dead lease does; said once, as a stale lease, naming the file."""
        d, lp, host, boot = self._setup(age=sb.LEASE_TTL_S + 30)
        be = self._go(boot)
        self.assertIn(CLI_A, boot.signaled(), "the orphan is ended")
        self.assertIn(host, boot.stops(), "the dead host's scope is stopped")
        self.assertEqual(boot.started, [SID], "the cut turn resumes")
        self.assertFalse(lp.exists(), "the stale file goes with its CLI")
        rows = self._rows(be, boot)
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(str(lp), rows[0])
        self.assertIn("has not been rewritten for", rows[0])
        self.assertFalse(any("left alone" in m and "lease" in m for m in boot.said), boot.said)

    def test_a_held_cli_is_judged_again_when_its_lease_goes_stale(self):
        """The lease was rewritten just before the boot and nobody rewrites it after: once it is LEASE_TTL_S old the boot's
        decision is taken again, the CLI it spared ended as the orphan it then is, and the session resumed."""
        d, lp, host, boot = self._setup()
        be = self._go(boot)
        self.assertEqual(boot.retake(be), 1, "fresh still: nothing decided")
        self.assertEqual((boot.signaled(), boot.started), ([], []))
        t = time.time() - sb.LEASE_TTL_S - 5
        os.utime(lp, (t, t))
        self.assertEqual(boot.retake(be), 0)
        self.assertEqual(boot.signaled(), [CLI_A], "ended now that its lease spares nothing")
        self.assertEqual(boot.started, [SID], "and the session resumed")
        self.assertIn(sb.BOOT_RESUME_NUDGE, sb.read_reg(Path(d), SID).get("queue") or [])

    def test_a_held_cli_whose_process_listing_fails_once_its_lease_settles_stays_deferred_for_the_next_pass(self):
        """A failed process listing is no verdict on the CLI the boot spared. The deferral used to leave the set before the
        listing was read, so one failed `ps` left the session down until something else connected it, with only a log
        line. It stays deferred and the next pass reads the listing again; the failure is said once for the episode, and
        the listing reading again ends it."""
        d, lp, host, boot = self._setup()
        be = self._go(boot)
        t = time.time() - sb.LEASE_TTL_S - 5
        os.utime(lp, (t, t))                                                   # the lease settled: stale
        boot.ps_fault = OSError("synthetic: ps could not run")
        for _ in range(3):
            self.assertEqual(boot.retake(be), 1, "no listing, no verdict: still deferred")
        self.assertEqual((boot.signaled(), boot.started), ([], []), "nothing ended, nothing started")
        said = [m for m in boot.said if "process listing" in m]
        self.assertEqual(len(said), 1, "said once for the episode, not once per pass: %r" % said)
        self.assertIn("synthetic: ps could not run", said[0])
        boot.ps_fault = None
        self.assertEqual(boot.retake(be), 0, "the listing reads: judged, and decided again")
        self.assertEqual(boot.signaled(), [CLI_A], "the orphan ended now that the listing names it")
        self.assertEqual(boot.started, [SID], "and the session resumed")
        self.assertIn(sb.BOOT_RESUME_NUDGE, sb.read_reg(Path(d), SID).get("queue") or [])
        self.assertEqual(sum("process listing reads again" in m for m in boot.said), 1, boot.said[-4:])

    def test_a_held_cli_stays_with_its_host_when_the_lease_reads_as_the_live_hosts(self):
        """The lease reads again a beat later, naming the spared CLI under its live host: the CLI is owned, nothing is
        ended, and the session attaches as the boot's re-attach."""
        d, lp, host, boot = self._setup()
        be = self._go(boot)
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": CLI_A, "start": "1000", "version": "",
                           "holder": {"pid": HOST_A, "start": "500", "kind": "host"}, "t": time.time()})
        # the host road's classification reads identities through its own load of the lease rules: handed the scripted
        # machine's start times through its `start` seam, as the census here reads them
        ht = sb._ht()
        classify = ht.host_lease_state
        with mock.patch.object(ht, "host_lease_state", lambda lease, now, start=None: classify(lease, now, boot.starts.get)):
            self.assertEqual(boot.retake(be), 0)
        self.assertEqual(boot.signaled(), [])
        self.assertEqual(boot.started, [SID])
        self.assertIn(SID, be._boot_attach_sids)
        self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [], "no restart notice: the host kept the turn")


class UntaggedIsLeftAloneAndNamed(unittest.TestCase):
    """A unit or CLI with no state tag at all (an older build's, or one whose environment cannot be read) cannot be
    attributed to this kernel: it is left alone, and ONE log line names every such thing, never stopped silently."""

    def test_untagged_units_and_cli_are_left_alone_and_one_line_names_them(self):
        d = _root()
        _reg(d, SID)
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
        u_session = "romp-session-%s-%d-1757374800.scope" % (SID[:8], GONE)
        u_cli_scope = "romp-session-%s-%d-1757374801.scope" % (SID[:8], CLI_U)
        u_host = "romp-host-%s-1757374800000.scope" % SID[:8]
        sessions = [_scope_line(u_session, "romp session %s" % SID), _scope_line(u_cli_scope, "romp session %s" % SID)]
        hosts = [_scope_line(u_host, "romp session host %s" % SID)]
        boot = Boot(d, ps, sessions, hosts, tags={})       # no tag readable for any CLI
        boot.go()
        self.assertEqual(boot.signaled(), [], "an untagged CLI is never signaled")
        self.assertEqual(boot.stops(), [], "no untagged unit is stopped")
        left = [m for m in boot.said if "left alone" in m]
        self.assertEqual(len(left), 1, "one line, not one per unit: %r" % boot.said)
        for name in (str(CLI_U), u_session, u_host):
            self.assertIn(name, left[0])
        self.assertNotIn(u_cli_scope, left[0], "the spared CLI's own scope is spared with it, not listed twice")

    def test_a_clean_boot_logs_no_left_alone_line(self):
        d = _root()
        _reg(d, SID)
        boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER], [], [], tags={})
        boot.go()
        self.assertEqual([m for m in boot.said if "left alone" in m], [])


class OwnCrashLeftoverIsStillReaped(unittest.TestCase):
    def test_own_tag_and_a_lease_whose_holder_is_gone_still_reaps(self):
        """The crash case the reaper exists for, unchanged: this kernel died, its CLI carries its tag and a lease whose
        holder is gone; the boot ends the CLI and stops its scope."""
        d = _root()
        _reg(d, SID)
        sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": CLI_B, "start": "7", "version": "",
                           "holder": {"pid": P + 99, "start": "1"}, "t": time.time()})
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER)]
        unit = "romp-session-%s-%d-1757374800.scope" % (SID[:8], CLI_B)
        boot = Boot(d, ps, [_scope_line(unit, "romp session %s romp-state=%s" % (SID, _tag(d)))], [],
                    tags={CLI_B: _tag(d)}, starts={CLI_B: "7"})
        boot.go()
        self.assertEqual(boot.signaled(), [CLI_B])
        self.assertEqual(boot.stops(), [unit])
        self.assertIsNone(sb.read_lease(d, SID), "the lease that did not hold went with its CLI")
        self.assertEqual([m for m in boot.said if "left alone" in m], [])

    def test_own_tag_and_a_lease_whose_holder_is_gone_ends_the_cli_and_its_session_with_a_cut_turn_resumes(self):
        """This kernel died mid-turn: its CLI carries its tag and the session's lease, whose holder is gone, and resumes
        the session's lastSid, or the session's own sid after a /clear inside it moved the lastSid on. The boot ends the
        CLI, holds nothing, and resumes the session's cut turn with the continuation nudge."""
        moved = "11111111-2222-3333-4444-5555cdcd0923"
        for world, last in (("resumes its lastSid", SID), ("cleared inside it", moved)):
            with self.subTest(world=world):
                d = _root()
                sb.write_reg(Path(d), SID, {"sid": SID, "name": "api", "cwd": "/tmp", "alive": True, "lastSid": last})
                sb.append_state(Path(d), SID, "working")
                sb.write_lease(d, {"sid": SID, "fsid": SID, "pid": CLI_B, "start": "7", "version": "",
                                   "holder": {"pid": P + 99, "start": "1"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER)]
                boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, starts={CLI_B: "7"})
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [CLI_B])
                self.assertEqual([m for m in boot.said if "stays down" in m], [])
                self.assertEqual(boot.started, [SID])
                self.assertEqual((sb.read_reg(Path(d), SID).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                self.assertTrue([r for r in _states(d, SID) if r.get("machineCut") == "restart"], _states(d, SID))
                self.assertIsNone(sb.read_lease(d, SID))


class ASparedOrphanHoldsItsConversation(unittest.TestCase):
    """The resume rule meets the tag check. A session whose state tail is machine-active (its turn was cut by the
    kernel's death) is resumed at boot, and until the state tag its orphan was ended FIRST, so the resume never met a
    second writer. An orphan the tag check spares keeps its conversation, so the same boot must not resume that session
    on top of it: _ensure with --resume would be two writers on one transcript, the hazard the reap exists to prevent.
    The session stays down, said once, with nothing of the resume on its disk; a session with no spared orphan resumes
    exactly as before, and so does one whose orphan was this kernel's own and was ended."""

    def _states(self, d, sid):
        return [json.loads(l) for l in (Path(d) / "states" / (sid + ".jsonl")).read_text().splitlines() if l.strip()]

    TASKS = [{"toolUseId": "toolu_01", "desc": "watch the build", "type": "local_bash", "since": 100}]
    QUEUED = ["a message typed to api"]

    # a session's reasons to start, as (its state tail, what its reg carries): a cut turn, a question it had up when
    # the old kernel died (pendingAsk), or background tasks that died with it (bgTasks), each alone, and either of the
    # last two beside a cut turn or a queued message
    START_REASONS = {"a cut turn": ("working", {}),
                     "a question it had up": ("waiting", {"pendingAsk": True}),
                     "dead background tasks": ("waiting", {"bgTasks": TASKS}),
                     "a question it had up and a cut turn": ("working", {"pendingAsk": True}),
                     "dead background tasks and a cut turn": ("working", {"bgTasks": TASKS}),
                     "a question it had up and a queued message": ("waiting", {"pendingAsk": True, "queue": QUEUED}),
                     "dead background tasks and a queued message": ("waiting", {"bgTasks": TASKS, "queue": QUEUED})}

    def test_the_spared_orphans_session_stays_down_and_a_session_without_one_still_resumes(self):
        dA, dB = _root(), _root()
        _reg(dB, SID)
        _reg(dB, SID2, name="web")
        sb.append_state(Path(dB), SID, "working")      # a machine-active tail on each: the resume rule fires on both
        sb.append_state(Path(dB), SID2, "working")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
              _cli(CLI_A, MANAGER)]         # kernel A's CLI on SID: an orphan by B's census, spared by the tag check
        boot = Boot(dB, ps, [], [], tags={CLI_A: _tag(dA)}, sids=(SID, SID2))
        boot.go()
        self.assertNotIn(CLI_A, boot.signaled())
        self.assertEqual(boot.started, [SID2], "the spared orphan's session is never started; the other one resumes")
        down = [m for m in boot.said if "stays down" in m]
        self.assertEqual(len(down), 1, boot.said)
        self.assertIn("api", down[0], "the session is named")
        self.assertIn("did not start", down[0])
        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, "the spared process is still named once")
        # nothing of the resume reached SID's disk: no nudge in its queue, no machine-cut stamp on its tail, and the cut
        # tail itself is still there for the boot that finds the conversation free
        self.assertEqual(sb.read_reg(Path(dB), SID).get("queue") or [], [])
        self.assertFalse([r for r in self._states(dB, SID) if "machineCut" in r], self._states(dB, SID))
        self.assertEqual(sb.last_state_value(Path(dB), SID), "working")
        # ...and all of it reached SID2's, as before
        self.assertEqual((sb.read_reg(Path(dB), SID2).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
        self.assertTrue([r for r in self._states(dB, SID2) if r.get("machineCut") == "restart"])
        rows = [json.loads(l) for l in (Path(dB) / sb.SESSION_EVENTS_FILE).read_text().splitlines()]
        summary = [r for r in rows if r.get("kind") == "reconcile.boot"]
        self.assertEqual(len(summary), 1, rows)
        self.assertEqual((summary[0]["sessions"], summary[0]["resumed"], summary[0]["toStart"]), (2, 1, 1))

    def test_an_untagged_orphan_holds_the_conversation_too(self):
        d = _root()
        _reg(d, SID)
        sb.append_state(Path(d), SID, "retrying")       # any machine-active state, not just working
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
        boot = Boot(d, ps, [], [], tags={})             # no tag readable: an older build's CLI, spared
        boot.go()
        self.assertEqual(boot.signaled(), [])
        self.assertEqual(boot.started, [], "no resume on top of the untagged CLI")
        self.assertEqual(len([m for m in boot.said if "stays down" in m]), 1, boot.said)
        self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [])
        self.assertFalse([r for r in self._states(d, SID) if "machineCut" in r])

    def test_a_session_whose_own_orphan_was_ended_resumes_as_before(self):
        d = _root()
        _reg(d, SID)
        sb.append_state(Path(d), SID, "working")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER)]
        boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)})   # this kernel's own tag: ended first, as before
        boot.go()
        self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended first")
        self.assertEqual(boot.started, [SID], "...and the session resumes, the conversation free")
        self.assertEqual([m for m in boot.said if "stays down" in m], [])
        self.assertEqual((sb.read_reg(Path(d), SID).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
        self.assertTrue([r for r in self._states(d, SID) if r.get("machineCut") == "restart"])

    def test_a_session_whose_conversation_moved_on_is_held_by_its_current_conversation(self):
        # a /clear moved the session's conversation on from its sid, and an untagged CLI resumes that current
        # conversation: its --resume alone, or among other flags in claude-agent-sdk's order, with or without a
        # rewind's cut (--resume-session-at) among them, with no appended prompt, or with the thinking-summaries flags.
        # The session's lastSid is what the spared CLI writes, not its sid. Each subtest gives the session one set of
        # reasons to start (START_REASONS), and what it has stays on its reg
        moved = "11111111-2222-3333-4444-5555cccc0923"
        sdk = "  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
        cut = "--resume-session-at 0f0f0f0f-0000-4000-8000-000000000001"
        resumes = {"a plain resume": _cli(CLI_U, MANAGER, sid=moved),
                   "a plain resume among other flags in claude-agent-sdk's order":
                       sdk % (CLI_U, MANAGER, _sdk_flags("--resume=%s" % moved)),
                   "the same with a rewind's cut": sdk % (CLI_U, MANAGER, _sdk_flags("--resume=%s" % moved, cut=cut)),
                   "a plain resume among other flags in claude-agent-sdk's order, with no appended prompt":
                       sdk % (CLI_U, MANAGER, _sdk_flags("--resume=%s" % moved, prompt=_SDK_NO_PROMPT)),
                   "a plain resume among other flags in claude-agent-sdk's order, with the thinking-summaries flags":
                       sdk % (CLI_U, MANAGER, _sdk_flags("--resume=%s" % moved, thinking=_SDK_THINKING))}
        for reason, process in [(reason, process) for reason in self.START_REASONS for process in resumes]:
            tail, extra = self.START_REASONS[reason]
            with self.subTest(session_has=reason, process=process):
                d = _root()
                sb.write_reg(Path(d), SID, dict({"sid": SID, "name": "api", "cwd": "/tmp", "alive": True,
                                                 "lastSid": moved}, **extra))
                sb.append_state(Path(d), SID, tail)
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, resumes[process]]
                boot = Boot(d, ps, [], [], tags={})
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [])
                self.assertEqual(_named(boot.started), [],
                                 "no start beside the CLI writing the session's current conversation")
                down = [m for m in boot.said if "stays down" in m]
                self.assertEqual(len(down), 1, boot.said)
                self.assertIn("api", down[0])
                reg = sb.read_reg(Path(d), SID)
                self.assertEqual(reg.get("queue") or [], extra.get("queue", []),
                                 "no notice reached its queue, and its own message stays there")
                for key, value in extra.items():
                    self.assertEqual(reg.get(key), value, "it stays on the session's reg")
                self.assertFalse([r for r in self._states(d, SID) if "machineCut" in r])
                self.assertEqual(sb.last_state_value(Path(d), SID), tail)

    def test_a_session_cleared_inside_its_leased_spared_process_whose_command_line_names_its_own_sid_is_held_by_it(self):
        # a session that is not a fork, cleared inside its CLI: its lastSid moved on while the CLI's argv still resumes
        # the session's own sid, which is no live row's lastSid. The census admits the CLI by its lease in this state
        # directory (in lease_census's loop over the leases whose process is not among the CLIs whose argv names a live
        # row's lastSid; the lease's kernel holder is gone) and the tag check spares it (no tag: a build from before the
        # state tag). The CLI writes the session's current conversation, so the session stays down, held by the own-sid
        # half of the stay-down match: a start would put a second CLI on that conversation. Each subtest gives the
        # session one set of reasons to start (START_REASONS), and what it has stays on its reg
        moved = "11111111-2222-3333-4444-5555dddd0923"
        holder = P + 70
        for reason, (tail, extra) in self.START_REASONS.items():
            with self.subTest(session_has=reason):
                d = _root()
                sb.write_reg(Path(d), SID, dict({"sid": SID, "name": "api", "cwd": "/tmp", "alive": True,
                                                 "lastSid": moved}, **extra))
                sb.append_state(Path(d), SID, tail)
                sb.write_lease(d, {"sid": SID, "fsid": SID, "name": "api", "pid": CLI_U, "start": "7", "version": "",
                                   "holder": {"kind": "kernel", "pid": holder, "start": "3"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
                boot = Boot(d, ps, [], [], tags={}, starts={CLI_U: "7"})
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [])
                self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                 "the census admitted it and it was spared")
                self.assertEqual(_named(boot.started), [],
                                 "no start beside the CLI writing the session's current conversation")
                down = [m for m in boot.said if "stays down" in m]
                self.assertEqual(len(down), 1, boot.said)
                self.assertIn("api", down[0])
                reg = sb.read_reg(Path(d), SID)
                self.assertEqual(reg.get("queue") or [], extra.get("queue", []),
                                 "no notice reached its queue, and its own message stays there")
                for key, value in extra.items():
                    self.assertEqual(reg.get(key), value, "it stays on the session's reg")
                self.assertFalse([r for r in self._states(d, SID) if "machineCut" in r])
                self.assertEqual(sb.last_state_value(Path(d), SID), tail)

    def test_a_new_sessions_leased_first_process_before_its_first_init_holds_it_when_spared_and_its_reaped_row_names_it_when_ended(self):
        # a new session whose turn was cut before its first CLI's init, so its lastSid is still "" (spawn), while that
        # CLI, a fresh launch (--session-id=<its sid>, no --resume), survives with a lease whose kernel holder is gone,
        # and the census admits it by that lease. Spared (no tag), it holds the session by its own sid; ended (this
        # root's tag), its orphan-reaped row names the session. api, with a lastSid, makes the boot read the listing
        holder = P + 70
        for spared in (True, False):
            with self.subTest(spared=spared):
                d = _root()
                _reg(d, SID)
                sb.write_reg(Path(d), NEW, {"sid": NEW, "name": "fresh", "cwd": "/tmp", "alive": True, "lastSid": ""})
                sb.append_state(Path(d), NEW, "working")
                sb.write_lease(d, {"sid": NEW, "fsid": NEW, "name": "fresh", "pid": CLI_U, "start": "7",
                                   "version": "", "holder": {"kind": "kernel", "pid": holder, "start": "3"},
                                   "t": time.time()})
                cli = ("  %d %d /x/claude --output-format stream-json --session-id=%s --input-format stream-json"
                       % (CLI_U, MANAGER, NEW))
                boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, cli], [], [],
                            tags={} if spared else {CLI_U: _tag(d)}, starts={CLI_U: "7"}, sids=(SID, NEW))
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [] if spared else [CLI_U])
                down = [m for m in boot.said if "stays down" in m]
                reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                if spared:
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                     "the census admitted it and it was spared")
                    self.assertEqual(_named(boot.started), [], "no start beside the CLI writing the session")
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("fresh", down[0])
                    self.assertEqual(sb.read_reg(Path(d), NEW).get("queue") or [], [])
                    self.assertEqual(sb.last_state_value(Path(d), NEW), "working")
                    self.assertEqual(reaped, [])
                else:
                    self.assertEqual(down, [], "nothing is held once the orphan is gone")
                    self.assertEqual(_named(boot.started), ["fresh"])
                    self.assertEqual(len(reaped), 1, _rows(d))
                    self.assertEqual([reaped[0].get("cliPid"), reaped[0].get("name")]
                                     + _named([reaped[0].get("sid"), reaped[0].get("fsid")]),
                                     [CLI_U, "fresh", "fresh", "fresh"])

    def test_a_spared_processs_lease_whose_start_time_is_not_the_processs_does_not_hold_the_leases_session(self):
        # web's lease on this state root names the pid of a surviving plain resume of api's conversation (no tag), with
        # a start time that is not the process's. Both sessions have a cut turn. api stays down, held by the process's
        # command line, and web is started. In either order
        for order in ((SID, WEB), (WEB, SID)):
            with self.subTest(first=_named(order)[0]):
                d = _root()
                _reg(d, SID)
                _reg(d, WEB, name="web")
                sb.append_state(Path(d), SID, "working")
                sb.append_state(Path(d), WEB, "working")
                sb.write_lease(d, {"sid": WEB, "fsid": WEB, "name": "web", "pid": CLI_U, "start": "9", "version": "",
                                   "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
                boot = Boot(d, ps, [], [], tags={}, starts={CLI_U: "7"}, sids=order)
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [])
                self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                self.assertEqual(_named(boot.started), ["web"])
                down = [m for m in boot.said if "stays down" in m]
                self.assertEqual(len(down), 1, boot.said)
                self.assertIn("api", down[0])

    def test_flag_text_in_a_spared_processs_prompt_ahead_of_its_flags_leaves_its_session_held(self):
        # an appended system prompt ahead of the CLI's own flags (the argv order cli_writes_sids describes) puts
        # flag-shaped text on a plain resume's command line before its own --resume. The boot reads the process's own
        # flags, from its last --resume of ours on (cli_writes_sids), so the session the process resumes stays down
        # with each prompt text here. Another session of ours that the prompt names, which the process does not write,
        # resumes, except after a --session-id: a --session-id of ours ahead of the --resume is also what a launch that
        # put its own --session-id first would show, so that session is held too, on the safe side
        resumes = {"--session-id x": "--session-id x", "--resume x": "--resume x", "--fork-session": "--fork-session",
                   "both fork flags": "use --fork-session with --session-id x",
                   "both fork flags, another kernel's id": "--session-id=%s --fork-session" % ELSE,
                   "tests' id after --resume": "--resume=%s" % SID2}
        held_too = {"tests' id after --session-id": "--session-id %s" % SID2,
                    "a fork's command line naming both": "claude --resume=%s --session-id=%s --fork-session"
                                                         % (SID, SID2)}
        who = {SID: "api", SID2: "tests"}.get                 # names, not uuids, in a failure's output
        for key, text in list(resumes.items()) + list(held_too.items()):
            for order in ((SID, SID2), (SID2, SID)):
                with self.subTest(prompt_text=key, first=who(order[0])):
                    d = _root()
                    _reg(d, SID)
                    _reg(d, SID2, name="tests")
                    sb.append_state(Path(d), SID, "working")
                    sb.append_state(Path(d), SID2, "working")
                    cli = ("  %d %d /x/claude --output-format stream-json --append-system-prompt see %s for details "
                           "--resume=%s --input-format stream-json" % (CLI_U, MANAGER, text, SID))
                    boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, cli], [], [], tags={},
                                sids=order)
                    boot.go()
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual([who(s) for s in boot.started], [] if key in held_too else ["tests"],
                                     "no resume beside the process")
                    down = [m for m in boot.said if "stays down" in m]
                    self.assertEqual(len(down), 2 if key in held_too else 1, boot.said)
                    self.assertEqual(len([m for m in down if "api" in m]), 1, down)
                    self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [])
                    self.assertEqual((sb.read_reg(Path(d), SID2).get("queue") or [])[:1],
                                     [] if key in held_too else [sb.BOOT_RESUME_NUDGE])

    def test_a_spared_fresh_launchs_session_stays_down_beside_another_sessions_id_in_its_prompt_ahead_of_its_flags(self):
        # a fresh launch (--session-id=<its sid> and no --resume: _options' first launch of a session, which its CLI
        # keeps until it respawns) with another session's id in its appended prompt, ahead of its flags. Its own id is
        # the last --session-id of ours on the command line, so the session it writes stays down and the session the
        # prompt names resumes, in either row order
        who = {SID: "api", SID2: "tests"}.get                 # names, not uuids, in a failure's output
        texts = {"tests' id after --session-id": "--session-id %s" % SID2,
                 "a fork's command line naming both": "claude --resume=%s --session-id=%s --fork-session" % (SID, SID2)}
        for key, text in texts.items():
            for order in ((SID, SID2), (SID2, SID)):
                with self.subTest(prompt_text=key, first=who(order[0])):
                    d = _root()
                    _reg(d, SID)
                    _reg(d, SID2, name="tests")
                    sb.append_state(Path(d), SID, "working")
                    sb.append_state(Path(d), SID2, "working")
                    cli = ("  %d %d /x/claude --output-format stream-json --append-system-prompt see %s for details "
                           "--session-id=%s --input-format stream-json" % (CLI_U, MANAGER, text, SID))
                    boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, cli], [], [], tags={},
                                sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual([who(s) for s in boot.started], ["tests"],
                                     "no resume beside the process; the session it does not write resumes")
                    down = [m for m in boot.said if "stays down" in m]
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("api", down[0])
                    self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [])
                    self.assertEqual(sb.last_state_value(Path(d), SID), "working")
                    self.assertEqual((sb.read_reg(Path(d), SID2).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])

    def test_a_closed_sessions_spared_fresh_launch_holds_no_live_session_named_in_its_prompt_ahead_of_its_flags(self):
        # a fresh launch of tests, a closed session, survives (no tag) with api's id after a flag in its appended
        # prompt, ahead of its flags, which is how the census admits it. Its own id is the last --session-id of ours on
        # the command line, so no live session is held and api resumes its cut turn, in either order
        who = {SID: "api", SID2: "tests"}.get                 # names, not uuids, in a failure's output
        texts = {"api's id after --session-id": "--session-id %s" % SID, "api's id after --resume": "--resume %s" % SID}
        for key, text in texts.items():
            for order in ((SID, SID2), (SID2, SID)):
                with self.subTest(prompt_text=key, first=who(order[0])):
                    d = _root()
                    _reg(d, SID)
                    sb.write_reg(Path(d), SID2, {"sid": SID2, "name": "tests", "cwd": "/tmp", "alive": False,
                                                 "lastSid": SID2})
                    sb.append_state(Path(d), SID, "working")
                    cli = ("  %d %d /x/claude --output-format stream-json --append-system-prompt see %s for details "
                           "--session-id=%s --input-format stream-json" % (CLI_U, MANAGER, text, SID2))
                    boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, cli], [], [], tags={},
                                sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                     "the census admitted it and it was spared")
                    self.assertEqual([m for m in boot.said if "stays down" in m], [], boot.said)
                    self.assertEqual([who(s) for s in boot.started], ["api"])
                    self.assertEqual((sb.read_reg(Path(d), SID).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])

    def test_an_unleased_process_naming_only_a_closed_sessions_sid_that_no_live_row_has_as_its_lastsid_is_neither_ended_nor_named(self):
        # tests, a closed session, has a surviving plain resume of its own sid, which is no live row's lastSid, and no
        # lease. The census admits a CLI whose argv names a live row's lastSid, or one its lease identifies; this one is
        # neither, so it is not ended with this root's tag, nor named on a left-alone line without one, and api
        # resumes its cut turn
        for tag in ("this root's", "none"):
            with self.subTest(tag=tag):
                d = _root()
                _reg(d, SID)
                sb.write_reg(Path(d), SID2, {"sid": SID2, "name": "tests", "cwd": "/tmp", "alive": False,
                                             "lastSid": SID2})
                sb.append_state(Path(d), SID, "working")
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER, sid=SID2)]
                boot = Boot(d, ps, [], [], tags={CLI_U: _tag(d)} if tag == "this root's" else {}, sids=(SID, SID2))
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [])
                self.assertEqual([m for m in boot.said if "left alone" in m], [], boot.said)
                self.assertEqual([r for r in _rows(d) if r.get("cliPid") == CLI_U], [], _rows(d))
                self.assertEqual(_named(boot.started), ["api"])

    def test_a_resume_of_a_closed_sessions_moved_on_conversation_that_no_live_row_has_as_its_lastsid_is_read_from_the_resume_of_ours_in_its_prompt(self):
        # tests, a closed session, was cleared before it closed, so its lastSid moved on from its sid. A plain resume of
        # that moved-on conversation survives with api's id after a --resume in its appended prompt, which is how the
        # census admits it. The ids the boot reads a CLI's flags by are each live row's lastSid and every row's own sid,
        # so the process's own --resume names none of them and its flags are read from the prompt's --resume of api on:
        # with no tag the process holds api, as a live kernel's child its census row names api, and with this root's
        # tag it is ended, its orphan-reaped row names api and api resumes its cut turn
        moved = "11111111-2222-3333-4444-5555abcd0923"      # tests' conversation after its /clear
        for kind in ("no tag", "a live kernel's child", "this root's tag"):
            with self.subTest(process=kind):
                d = _root()
                _reg(d, SID)
                sb.write_reg(Path(d), SID2, {"sid": SID2, "name": "tests", "cwd": "/tmp", "alive": False,
                                             "lastSid": moved})
                sb.append_state(Path(d), SID, "working")
                child = kind == "a live kernel's child"
                own = kind == "this root's tag"
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
                      "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                      "  %d %d /x/claude --output-format stream-json --append-system-prompt see --resume %s for details "
                      "--resume=%s --input-format stream-json" % (CLI_U, KERNEL_K if child else MANAGER, SID, moved)]
                boot = Boot(d, ps, [], [], tags={CLI_U: _tag(d)} if own else {}, sids=(SID, SID2))
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [CLI_U] if own else [])
                down = [m for m in boot.said if "stays down" in m]
                if own:
                    reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                    self.assertEqual(len(reaped), 1, _rows(d))
                    self.assertEqual([reaped[0].get("cliPid"), reaped[0].get("name")]
                                     + _named([reaped[0].get("sid"), reaped[0].get("fsid")]),
                                     [CLI_U, "api", "api", "api"])
                    self.assertEqual(down, [], "nothing is held once the process is ended")
                    self.assertEqual(_named(boot.started), ["api"])
                    self.assertEqual((sb.read_reg(Path(d), SID).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                elif child:
                    rows = [r for r in _rows(d) if r.get("kind") == "lease.cli-without-lease"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                     [CLI_U, "api", "api"])
                    self.assertEqual(down, [], "a live kernel's child holds nothing")
                    self.assertEqual(_named(boot.started), ["api"])
                else:
                    self.assertEqual(_named(boot.started), [], "the process holds api")
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("api", down[0])

    def test_the_stays_down_line_says_a_process_may_still_be_writing_the_conversation(self):
        d = _root()
        _reg(d, SID)
        sb.append_state(Path(d), SID, "working")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
        boot = Boot(d, ps, [], [], tags={})
        boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        down = [m for m in boot.said if "stays down" in m]
        self.assertEqual(len(down), 1, boot.said)
        self.assertIn("api stays down: a claude process this kernel did not start may still be writing its "
                      "conversation", down[0])

    def test_a_lease_census_that_raises_is_logged_as_reap_failed(self):
        # a boot test here that asserts no line says "reap failed" reads the line the boot logs when its reap raises
        # past its own handling, here from the lease census
        d = _root()
        _reg(d, SID)
        sb.append_state(Path(d), SID, "working")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER)]
        boot = Boot(d, ps, [], [], tags={})
        with mock.patch.object(sb, "lease_census", side_effect=RuntimeError("a synthetic failure")):
            boot.go()
        self.assertEqual(len([m for m in boot.said if "reap failed" in m]), 1, boot.said)
        self.assertEqual(boot.signaled(), [])

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_spared_plain_resume_of_a_host_leased_sessions_conversation_keeps_it_down_unattached(self):
        # a live host lease on SID and an untagged CLI resuming SID's conversation: SID's own sid, or the conversation a
        # /clear moved SID on to, its lastSid. SID carries nothing else, a question it had up or dead background tasks.
        # The stay-down test runs ahead of the attach branch, so the session is held and not attached, with what it
        # carries left on its reg
        moved = "11111111-2222-3333-4444-5555abab0923"
        me = os.getpid()
        st = sb.proc_start(me)
        carries = {"nothing else": {}, "a question it had up": {"pendingAsk": True},
                   "dead background tasks": {"bgTasks": self.TASKS}}
        for case, conv in {"its own sid": SID, "its lastSid after a /clear": moved}.items():
            for what, extra in carries.items():
                with self.subTest(conversation=case, session_has=what):
                    d = _root()
                    sb.write_reg(Path(d), SID, dict({"sid": SID, "name": "api", "cwd": "/tmp", "alive": True,
                                                     "lastSid": conv}, **extra))
                    sb.append_state(Path(d), SID, "waiting")
                    sb.write_lease(d, {"sid": SID, "fsid": conv, "name": "api", "pid": me, "start": st, "version": "",
                                       "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()})
                    ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_U, MANAGER, sid=conv)]
                    boot = Boot(d, ps, [], [], tags={}, starts={me: st})
                    be = boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [])
                    rows = [json.loads(l) for l in (Path(d) / sb.SESSION_EVENTS_FILE).read_text().splitlines()
                            if l.strip()]
                    (summary,) = [r for r in rows if r.get("kind") == "reconcile.boot"]
                    self.assertEqual(summary.get("attached"), 0, "the held session's live host is not attached")
                    self.assertEqual(_named(sorted(be._boot_attach_sids)), [])
                    self.assertEqual(_named(boot.started), [], "no attach and no start")
                    down = [m for m in boot.said if "stays down" in m]
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("api", down[0])
                    reg = sb.read_reg(Path(d), SID)
                    for key, value in extra.items():
                        self.assertEqual(reg.get(key), value, "it stays on the session's reg")


# A parent session and two sessions started from its conversation: a comment thread and a copied session (a fork that is
# not a thread; SdkBackend.fork makes both).
WEB = "11111111-2222-4333-8444-000000000930"
THR = "33333333-4444-4555-8666-000000000930"
THR2 = "44444444-5555-4666-8777-000000000930"     # the thread's conversation after a /clear inside its CLI
WEB2 = "22222222-3333-4444-5555-6666bbbb0930"     # web's conversation after a /clear inside its CLI
ELSE = "66666666-7777-4888-8999-000000000930"     # a session of another kernel's; no row here
CPY = "55555555-6666-4777-8888-000000000930"
TH2 = "99999999-aaaa-4bbb-8ccc-000000000930"      # a second comment thread of web's
CP2 = "55555555-6666-4777-8888-000000c20930"      # a second copied session of web's
NEW = "77777777-1111-4222-8333-000000000930"      # a new session whose first CLI has not reached its init
CLI_F = P + 50                      # a claude process with --fork-session on its command line
KERNEL_K = P + 60                   # another live kernel on this state root (the census row's case)
OWN = object()                      # a tags value: this state root's own tag, filled in once the root exists
_NAMES = {WEB: "web", THR: "web thread", CPY: "web copy", TH2: "thread two", CP2: "web copy two", SID: "api",
          NEW: "fresh"}


def _named(sids):
    """Session ids as their names, so a failure reads as names (the runner's output scrubber replaces a uuid)."""
    return [_NAMES.get(s, s) for s in sids]


def _fork_cli(pid, ppid, parent, child):
    """A fork launch (_options: resume, fork_session and session_id) with --session-id and --fork-session after
    --resume: the CLI loads `parent`'s conversation and writes its own, `child`, the new session's id."""
    return ("  %d %d /x/claude --output-format stream-json --resume=%s --session-id=%s --fork-session "
            "--input-format stream-json" % (pid, ppid, parent, child))


# the system prompt flag claude-agent-sdk writes: the appended prompt, or, with no prompt to append, --system-prompt
# and an empty value
_SDK_PROMPT = "--append-system-prompt see the notes"
_SDK_NO_PROMPT = "--system-prompt "
# the flags claude-agent-sdk writes when the kernel's thinking-summaries toggle is on (_options passes
# thinking=THINKING_SUMMARIES_KW)
_SDK_THINKING = "--thinking adaptive --thinking-display summarized"


def _sdk_fork_cli(pid, ppid, parent, child, prompt=_SDK_PROMPT, thinking=""):
    """The same fork launch with its flags in the order claude-agent-sdk writes them: --verbose, the system prompt flag
    (`prompt`), --model, --permission-prompt-tool and --permission-mode, then --resume=, --session-id=, --settings,
    --mcp-config, --fork-session, the cut, `--resume-session-at <id>` (the kernel passes the cut in extra_args, which
    the SDK writes as two tokens), `thinking` when given, and --effort."""
    return ("  %d %d /x/claude --output-format stream-json --verbose %s --model opus --permission-prompt-tool stdio "
            "--permission-mode bypassPermissions --resume=%s --session-id=%s --settings /x/settings.json "
            "--mcp-config /x/mcp.json --fork-session --resume-session-at 0f0f0f0f-0000-4000-8000-000000000001 "
            "%s--effort xhigh --input-format stream-json"
            % (pid, ppid, prompt, parent, child, thinking + " " if thinking else ""))


def _sdk_fork_cli_no_prompt(pid, ppid, parent, child):
    """_sdk_fork_cli with no appended prompt."""
    return _sdk_fork_cli(pid, ppid, parent, child, prompt=_SDK_NO_PROMPT)


def _sdk_fork_cli_thinking(pid, ppid, parent, child):
    """_sdk_fork_cli with the thinking-summaries flags."""
    return _sdk_fork_cli(pid, ppid, parent, child, thinking=_SDK_THINKING)


def _sdk_flags(own, cut="", prompt=_SDK_PROMPT, thinking=""):
    """`own`, a plain resume's `--resume=<id>` or a fresh launch's `--session-id=<id>`, among other flags in the order
    claude-agent-sdk writes them: --verbose, the system prompt flag (`prompt`), --model, --permission-prompt-tool and
    --permission-mode ahead of it, then --settings, --mcp-config, a rewind's `cut` when given, `thinking` when given,
    and --effort."""
    return ("--verbose %s --model opus --permission-prompt-tool stdio --permission-mode bypassPermissions %s "
            "--settings /x/settings.json --mcp-config /x/mcp.json %s%s--effort xhigh"
            % (prompt, own, cut + " " if cut else "", thinking + " " if thinking else ""))


def _apart_fork_cli(pid, ppid, parent, child):
    """The same fork launch with each flag apart from its value (`--resume <id> --session-id <id>`)."""
    return ("  %d %d /x/claude --output-format stream-json --resume %s --session-id %s --fork-session "
            "--input-format stream-json" % (pid, ppid, parent, child))


def _rows(d):
    p = Path(d) / sb.SESSION_EVENTS_FILE
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def _states(d, sid):
    p = Path(d) / "states" / (sid + ".jsonl")
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


class ASparedForkHoldsItsOwnConversation(unittest.TestCase):
    """A comment thread and a copied session start as a fork of another session's conversation: their first CLI is
    launched with `--resume=<the parent's conversation>`, `--session-id=<its own sid>` and `--fork-session` (_options),
    loads the parent's transcript and writes its own, and keeps that argv after its first message. When the boot spares
    such a CLI (another kernel's tag, or none), a session stays down when its lastSid or its own sid is among the ids
    cli_writes_sids reads from the CLI's argv, whatever order the registry rows come in. A session also stays down when
    its own sid is the sid of the first lease in name order naming a spared CLI's pid and that lease's start time is the
    CLI's. A session also stays down when its lastSid is the lastSid of that lease's session, if that session's row has
    no forkOf.

    Each live thread here that is not under a live host has a queued reply, and each copy a cut turn, a queue, a
    question it had up or dead background tasks: without one the boot would not start the session even when nothing
    holds it. Every boot runs through the real
    _boot_reconcile (the Boot harness above), the registry rows handed over in the order named."""

    def _boot(self, regs, ps, tags, order, lease=None, starts=None):
        d = _root()
        for r in regs:
            r = dict(r)
            tail = r.pop("_tail", None)
            sb.write_reg(Path(d), r["sid"], dict({"cwd": "/tmp", "alive": True}, **r))
            if tail:
                sb.append_state(Path(d), r["sid"], tail)
        if lease:
            sb.write_lease(d, lease)
        tags = {p: (_tag(d) if t is OWN else t) for p, t in tags.items()}
        boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER] + list(ps), [], [], tags=tags,
                    starts=starts, sids=order)
        be = boot.go()
        # a reap that raised past its own handling is caught and logged by the boot, which then goes on without the
        # holds it had not yet made: an assertion here that a session is not held would pass on it, so none of these
        # boots may have logged that
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        return d, boot, be

    def _parent(self, tail="working"):
        return {"sid": WEB, "name": "web", "lastSid": WEB, "_tail": tail}

    LINEAGE = {"sid": WEB, "name": "web", "cut": "", "t": 1}     # forkedFrom, which SdkBackend.fork writes on each fork

    def _thread(self, first_message=True):
        r = {"sid": THR, "name": "web thread", "threadOf": WEB, "queue": ["a reply typed to the thread"],
             "forkedFrom": dict(self.LINEAGE)}
        # before its first message a fork keeps the lastSid it was made with (SdkBackend.fork), here web's, and its
        # fork flag unspent
        r.update({"lastSid": THR} if first_message else {"lastSid": WEB, "forkOf": WEB})
        return r

    @staticmethod
    def _first(order):
        return "parent" if order[0] == WEB else "fork"

    def _down(self, boot):
        return [m for m in boot.said if "stays down" in m]

    def _assert_thread_held_parent_resumed(self, d, boot):
        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
        self.assertEqual(_named(boot.started), ["web"],
                         "the parent resumes its cut turn, and the thread is not started beside the spared process")
        down = self._down(boot)
        self.assertEqual(len(down), 1, boot.said)
        self.assertIn("web thread", down[0], "the thread is the one held, not its parent")
        self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
        self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"], _states(d, WEB))
        self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"],
                         "the thread's reply stays in its queue")

    def test_a_threads_process_with_its_session_id_after_its_resume_holds_the_thread_not_its_parent_listed_ahead_of_it(self):
        d, boot, _ = self._boot([self._parent(), self._thread()], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                {CLI_F: _tag(_root())}, (WEB, THR))
        self._assert_thread_held_parent_resumed(d, boot)

    def test_a_threads_process_with_its_session_id_after_its_resume_holds_the_thread_not_its_parent_listed_after_it(self):
        d, boot, _ = self._boot([self._parent(), self._thread()], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                {CLI_F: _tag(_root())}, (THR, WEB))
        self._assert_thread_held_parent_resumed(d, boot)

    def test_a_fork_with_its_session_id_after_its_resume_spelled_with_its_values_apart_holds_the_thread_not_the_parent(self):
        # the same fork with every flag spelled apart from its value (`--resume <id> --session-id <id>`), the spelling an
        # SDK that passes them as two argv entries writes (_cli_carries_sid: both have been seen), in either order
        cli = ("  %d %d /x/claude --output-format stream-json --resume %s --session-id %s --fork-session "
               "--input-format stream-json" % (CLI_F, MANAGER, WEB, THR))
        for order in ((WEB, THR), (THR, WEB)):
            with self.subTest(first=self._first(order)):
                d, boot, _ = self._boot([self._parent(), self._thread()], [cli], {CLI_F: _tag(_root())}, order)
                self._assert_thread_held_parent_resumed(d, boot)

    def test_a_fork_whose_session_id_comes_ahead_of_its_resume_holds_the_thread_and_its_parent(self):
        # a launch that put the fork's --session-id ahead of its --resume reads like a plain resume of the parent with
        # that flag text in its prompt; the boot cannot tell which it is, so it holds both, the thread and the parent:
        # neither is started beside the process, whichever it writes
        shapes = {"--fork-session ahead of --resume": "--session-id=%s --fork-session --resume=%s" % (THR, WEB),
                  "--fork-session after --resume": "--session-id %s --resume %s --fork-session" % (THR, WEB)}
        for shape, argv in shapes.items():
            for order in ((WEB, THR), (THR, WEB)):
                with self.subTest(shape=shape, first=self._first(order)):
                    cli = ("  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                           % (CLI_F, MANAGER, argv))
                    d, boot, _ = self._boot([self._parent(), self._thread()], [cli], {CLI_F: _tag(_root())}, order)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual(_named(boot.started), [], "neither is started beside the process")
                    down = self._down(boot)
                    self.assertEqual(len(down), 2, boot.said)
                    self.assertEqual(len([m for m in down if "web thread" in m]), 1, down)
                    self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                    self.assertEqual(sb.last_state_value(Path(d), WEB), "working")
                    self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"])

    def test_with_no_session_id_after_a_resume_of_ours_every_session_a_session_id_ahead_of_it_names_stays_down(self):
        # the same launch order with an appended prompt between the fork's own --session-id and its --resume, and the
        # prompt naming a third session after a --session-id. Either --session-id may be the process's own, so, with no
        # --session-id after the --resume of ours, every one of ours ahead of it is held with the --resume's: the
        # thread, the parent and the session the prompt names all stay down
        cli = ("  %d %d /x/claude --session-id=%s --fork-session --output-format stream-json --append-system-prompt "
               "see --session-id %s for details --resume=%s --input-format stream-json"
               % (CLI_F, MANAGER, THR, SID, WEB))
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}      # its turn was cut too
        for order in ((WEB, THR, SID), (THR, WEB, SID), (SID, THR, WEB)):
            with self.subTest(first=_named(order)[0]):
                d, boot, _ = self._boot([self._parent(), self._thread(), api], [cli], {CLI_F: _tag(_root())}, order)
                self.assertEqual(boot.signaled(), [])
                self.assertEqual(_named(boot.started), [], "none of the three is started beside the process")
                down = self._down(boot)
                self.assertEqual(len(down), 3, boot.said)
                self.assertEqual(len([m for m in down if "web thread" in m]), 1, down)
                self.assertEqual(len([m for m in down if "web" in m and "web thread" not in m]), 1, down)
                self.assertEqual(len([m for m in down if "api" in m]), 1, down)
                self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                self.assertEqual(sb.read_reg(Path(d), SID).get("queue") or [], [])
                self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"])

    def test_a_threads_process_with_its_session_id_after_its_resume_holds_the_thread_before_its_first_message_not_its_parent(self):
        for order in ((WEB, THR), (THR, WEB)):
            with self.subTest(first=self._first(order)):
                d, boot, _ = self._boot([self._parent(), self._thread(first_message=False)],
                                        [_fork_cli(CLI_F, MANAGER, WEB, THR)], {CLI_F: _tag(_root())}, order)
                self._assert_thread_held_parent_resumed(d, boot)

    def test_a_forks_first_process_with_its_session_id_after_its_resume_holds_no_other_fork_made_from_the_same_conversation(self):
        # the thread and a second fork of web's, another thread or a copied session, both before their first message,
        # so both have web's conversation as their lastSid. The thread's own first process, with its --session-id and
        # --fork-session after its --resume (_fork_cli), survives (no tag): only the thread is held, web resumes its cut
        # turn and the second fork is started for its queued message, whichever of the three rows is listed first
        others = {"another thread": dict(self._thread(first_message=False), sid=TH2, name="thread two"),
                  "a copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB,
                                       "forkedFrom": dict(self.LINEAGE),
                                       "queue": ["a first message typed to the copy"]}}
        for kind, other in others.items():
            for order in ((WEB, THR, other["sid"]), (other["sid"], THR, WEB), (THR, other["sid"], WEB)):
                with self.subTest(second_fork=kind, first=_named(order)[0]):
                    d, boot, _ = self._boot([self._parent(), self._thread(first_message=False), other],
                                            [_fork_cli(CLI_F, MANAGER, WEB, THR)], {CLI_F: None}, order)
                    self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                    self.assertEqual(sorted(_named(boot.started)), sorted(["web", other["name"]]),
                                     "web resumes and the second fork is started")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web thread", down[0], "the thread whose process it is is the one held")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"], _states(d, WEB))
                    self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"])

    def test_a_thread_cleared_inside_its_spared_process_with_its_session_id_after_its_resume_holds_itself_not_its_parent(self):
        # a /clear inside the thread's CLI moved its lastSid on (the init's session id flips it) while the process
        # keeps its launch argv, so the argv names the parent's conversation and the thread's own sid, and neither is
        # the thread's lastSid. The process writes the thread's conversation: the thread's own sid holds it
        for order in ((WEB, THR), (THR, WEB)):
            with self.subTest(first=self._first(order)):
                cleared = dict(self._thread(), lastSid=THR2)
                d, boot, _ = self._boot([self._parent(), cleared], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                        {CLI_F: _tag(_root())}, order)
                self._assert_thread_held_parent_resumed(d, boot)

    def _cleared_web_process(self, flags):
        """web's surviving CLI, with `flags` on its command line."""
        return "  %d %d /x/claude --output-format stream-json %s --input-format stream-json" % (CLI_U, MANAGER, flags)

    def test_a_session_cleared_inside_its_spared_process_whose_command_line_names_its_own_sid_stays_down_beside_a_thread_made_before_the_clear(self):
        # web was cleared inside its CLI, which survives (another kernel's tag, or none) with web's own sid on its
        # command line, so web's lastSid has moved on. A thread made before the clear, still before its first message,
        # has web's sid as its lastSid, and the census admits the process by it. web is held by its own sid and the
        # thread by its lastSid: neither is started beside the process, in either order
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "working"}
        launches = {"a resume": "--resume=%s" % WEB, "a fresh launch": "--session-id=%s" % WEB}
        for launch, flags in launches.items():
            for tag in ("another kernel's", "none"):
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(launch=launch, tag=tag, first=self._first(order)):
                        d, boot, _ = self._boot([web, self._thread(first_message=False)],
                                                [self._cleared_web_process(flags)],
                                                {CLI_U: _tag(_root()) if tag == "another kernel's" else None}, order)
                        self.assertEqual(boot.signaled(), [])
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                         "the census admitted it and it was spared")
                        self.assertEqual(_named(boot.started), [], "neither is started beside the process")
                        down = self._down(boot)
                        self.assertEqual(len(down), 2, boot.said)
                        self.assertEqual(len([m for m in down if "web thread" in m]), 1, down)
                        self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                        self.assertFalse([r for r in _states(d, WEB) if "machineCut" in r], _states(d, WEB))
                        self.assertEqual(sb.last_state_value(Path(d), WEB), "working")
                        self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"])

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_session_cleared_inside_its_spared_process_whose_command_line_names_its_own_sid_stays_down_unattached_under_its_own_live_host(self):
        # web, cleared inside its surviving resume of web's sid (no tag), is under its own live host, and a thread made
        # before the clear, still before its first message, has web's sid as its lastSid: web is held and not attached,
        # and the thread is not started, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        lease = {"sid": WEB, "fsid": WEB2, "name": "web", "pid": me, "start": st, "version": "",
                 "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "waiting"}
        for order in ((WEB, THR), (THR, WEB)):
            with self.subTest(first=self._first(order)):
                d, boot, be = self._boot([web, self._thread(first_message=False)],
                                         [self._cleared_web_process("--resume=%s" % WEB)], {CLI_U: None}, order,
                                         lease=lease, starts={me: st})
                self.assertEqual(boot.signaled(), [])
                (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                self.assertEqual(summary.get("attached"), 0, "the held session's live host is not attached")
                self.assertEqual(_named(sorted(be._boot_attach_sids)), [])
                self.assertEqual(_named(boot.started), [], "no attach and no start")
                down = self._down(boot)
                self.assertEqual(len(down), 2, boot.said)
                self.assertEqual(len([m for m in down if "web thread" in m]), 1, down)

    def test_a_session_cleared_inside_its_leased_spared_process_after_an_earlier_clear_stays_down_alone_or_beside_a_copys_first_process_or_another_kernels_fork_of_its_current_conversation(self):
        # web was cleared, resumed on the conversation that clear gave it (C1), and cleared again inside that CLI, so
        # its lastSid is C2 while the CLI's command line names only C1, which is no live row's lastSid and no row's own
        # sid. The CLI survives with no tag and with web's lease on this state root, whose kernel holder is gone: the
        # census admits it by that lease and the tag check spares it. Beside it, a second spared process (no tag) names
        # C2 on its --resume: the first process of a copy of web made after the second clear, before or past the copy's
        # first message, or another kernel's fork of C2; or there is no second process. web, the copy and api each have
        # a cut turn, and nothing holds api. In each world and either order, web and the copy stay down and api is
        # started
        c1, c2 = "22222222-3333-4444-5555-6666eeee0930", "22222222-3333-4444-5555-6666ffff0930"
        lease = {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}
        web = {"sid": WEB, "name": "web", "lastSid": c2, "_tail": "working"}
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}
        copy_past = {"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "working"}
        copy_before = dict(copy_past, lastSid=c2, forkOf=WEB, forkedFrom=dict(self.LINEAGE))
        worlds = {   # the copy's row, the second process, the sessions that stay down
            "a copy's first process, before the copy's first message":
                (copy_before, _fork_cli(CLI_F, MANAGER, c2, CPY), ["web", "web copy"]),
            "a copy's first process, past the copy's first message":
                (copy_past, _fork_cli(CLI_F, MANAGER, c2, CPY), ["web", "web copy"]),
            "another kernel's fork of C2": (None, _fork_cli(CLI_F, MANAGER, c2, ELSE), ["web"]),
            "no second process": (None, None, ["web"])}
        for world, (copy, second, held) in worlds.items():
            regs = [web, api] + ([copy] if copy else [])
            others = tuple(r["sid"] for r in regs[1:])
            for order in ((WEB,) + others, others + (WEB,)):
                with self.subTest(world=world, first=_named(order)[0]):
                    ps = [self._cleared_web_process("--resume=%s" % c1)] + ([second] if second else [])
                    d, boot, _ = self._boot(regs, ps, {CLI_U: None, CLI_F: None}, order, lease=lease,
                                            starts={CLI_U: "7"})
                    self.assertEqual(boot.signaled(), [], "both processes are spared")
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                    self.assertEqual(_named(boot.started), ["api"])
                    self.assertEqual(sorted(m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                            for m in self._down(boot)), held)
                    self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                    self.assertFalse([r for r in _states(d, WEB) if "machineCut" in r], _states(d, WEB))
                    self.assertEqual(sb.last_state_value(Path(d), WEB), "working")

    def test_a_thread_or_copy_made_before_its_parents_first_clear_is_started_beside_its_parents_leased_spared_process_whose_command_line_names_a_later_conversation(self):
        # web was cleared, and its surviving CLI (no tag, web's lease on this state root, whose kernel holder is gone)
        # resumes the conversation that clear gave it (C1). Either web's lastSid is C1, or web was cleared again inside
        # that CLI and its lastSid is C2, alone or beside another kernel's fork of C2. A thread or a copy made from web
        # before the first clear, still before its first message, has web's sid as its lastSid and a queued message;
        # api has a cut turn. No process names web's sid. In each world and each order, web stays down and the fork and
        # api are started
        c1, c2 = "22222222-3333-4444-5555-6666e1e10930", "22222222-3333-4444-5555-6666f1f10930"
        lease = {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}
        forks = {"a thread": self._thread(first_message=False),
                 "a copy": {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB,
                            "forkedFrom": dict(self.LINEAGE), "queue": ["a first message typed to the copy"]}}
        worlds = {   # web's lastSid, the second process
            "cleared once": (c1, None),
            "cleared again inside the CLI": (c2, None),
            "cleared again inside the CLI, beside another kernel's fork of C2":
                (c2, _fork_cli(CLI_F, MANAGER, c2, ELSE))}
        for kind, fork in forks.items():
            for world, (last, second) in worlds.items():
                web = {"sid": WEB, "name": "web", "lastSid": last, "_tail": "working"}
                for order in ((WEB, fork["sid"], SID), (SID, fork["sid"], WEB), (fork["sid"], SID, WEB)):
                    with self.subTest(fork=kind, world=world, first=_named(order)[0]):
                        ps = [self._cleared_web_process("--resume=%s" % c1)] + ([second] if second else [])
                        d, boot, _ = self._boot([web, fork, api], ps, {CLI_U: None, CLI_F: None}, order, lease=lease,
                                                starts={CLI_U: "7"})
                        self.assertEqual(boot.signaled(), [], "every process is spared")
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                        self.assertEqual(sorted(_named(boot.started)), sorted([fork["name"], "api"]))
                        self.assertEqual([m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                          for m in self._down(boot)], ["web"])

    def test_a_thread_or_copy_made_after_its_parents_second_clear_stays_down_beside_its_parents_leased_spared_process_alone_or_beside_another_spared_process_naming_their_conversation(self):
        # web was cleared, resumed on the conversation that clear gave it (C1), and cleared again inside that CLI, so
        # its lastSid is C2 while the CLI's command line names only C1. The CLI survives with no tag and with web's
        # lease on this state root, whose kernel holder is gone. A thread or a copy made from web after the second
        # clear, still before its first message, has C2 as its lastSid, a queued message and no process of its own.
        # Beside them there is no second process, or a second spared process (no tag) names C2 on its --resume: the
        # first process of another copy of web's, before or past that copy's first message, or another kernel's fork of
        # C2. web's CLI may be writing C2, which the thread's or copy's launch copies. In each world and either order,
        # web, the thread or copy, and the other copy where there is one stay down, and api, with a cut turn, is started
        c1, c2 = "22222222-3333-4444-5555-6666eeee0930", "22222222-3333-4444-5555-6666ffff0930"
        lease = {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}
        web = {"sid": WEB, "name": "web", "lastSid": c2, "_tail": "working"}
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}
        news = {"a thread": dict(self._thread(first_message=False), lastSid=c2),
                "a copy": {"sid": CP2, "name": "web copy two", "lastSid": c2, "forkOf": WEB,
                           "forkedFrom": dict(self.LINEAGE), "queue": ["a first message typed to the copy"]}}
        copy_past = {"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "working"}
        copy_before = dict(copy_past, lastSid=c2, forkOf=WEB, forkedFrom=dict(self.LINEAGE))
        worlds = {   # the other copy's row, the second process
            "no second process": (None, None),
            "a copy's first process, before the copy's first message":
                (copy_before, _fork_cli(CLI_F, MANAGER, c2, CPY)),
            "a copy's first process, past the copy's first message": (copy_past, _fork_cli(CLI_F, MANAGER, c2, CPY)),
            "another kernel's fork of C2": (None, _fork_cli(CLI_F, MANAGER, c2, ELSE))}
        for kind, new in news.items():
            for world, (copy, second) in worlds.items():
                regs = [web, api, new] + ([copy] if copy else [])
                others = tuple(r["sid"] for r in regs[1:])
                for order in ((WEB,) + others, others + (WEB,)):
                    with self.subTest(new=kind, world=world, first=_named(order)[0]):
                        ps = [self._cleared_web_process("--resume=%s" % c1)] + ([second] if second else [])
                        d, boot, _ = self._boot(regs, ps, {CLI_U: None, CLI_F: None}, order, lease=lease,
                                                starts={CLI_U: "7"})
                        self.assertEqual(boot.signaled(), [], "every process is spared")
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                        self.assertEqual(_named(boot.started), ["api"])
                        self.assertEqual(sorted(m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                                for m in self._down(boot)),
                                         sorted(["web", new["name"]] + ([copy["name"]] if copy else [])))
                        self.assertEqual(sb.read_reg(Path(d), new["sid"]).get("queue"), new["queue"])
                        self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                        self.assertFalse([r for r in _states(d, WEB) if "machineCut" in r], _states(d, WEB))

    def test_a_thread_or_copy_made_after_its_parents_second_clear_stays_down_beside_its_closed_parents_leased_spared_process_and_another_spared_process_naming_their_conversation(self):
        # web was cleared, resumed on the conversation that clear gave it (C1), and cleared again inside that CLI, so
        # its lastSid is C2 while the CLI's command line names only C1. web's row is closed. The CLI survives with no
        # tag and with web's lease on this state root, whose kernel holder is gone. A thread or a copy made from web
        # after the second clear, still before its first message, has C2 as its lastSid, a queued message and no
        # process of its own. Beside them a second spared process (no tag) names C2 on its --resume: the first process
        # of another copy of web's, before or past that copy's first message, or another kernel's fork of C2. web's
        # CLI may be writing C2, which the thread's or copy's launch copies. In each world and every row order, the
        # thread or copy and the other copy where there is one stay down, and api, with a cut turn, is started
        c1, c2 = "22222222-3333-4444-5555-6666eeee0930", "22222222-3333-4444-5555-6666ffff0930"
        lease = {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}
        web = {"sid": WEB, "name": "web", "lastSid": c2, "_tail": "working", "alive": False}
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}
        news = {"a thread": dict(self._thread(first_message=False), lastSid=c2),
                "a copy": {"sid": CP2, "name": "web copy two", "lastSid": c2, "forkOf": WEB,
                           "forkedFrom": dict(self.LINEAGE), "queue": ["a first message typed to the copy"]}}
        copy_past = {"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "working"}
        copy_before = dict(copy_past, lastSid=c2, forkOf=WEB, forkedFrom=dict(self.LINEAGE))
        worlds = {   # the other copy's row, the second process
            "a copy's first process, before the copy's first message":
                (copy_before, _fork_cli(CLI_F, MANAGER, c2, CPY)),
            "a copy's first process, past the copy's first message": (copy_past, _fork_cli(CLI_F, MANAGER, c2, CPY)),
            "another kernel's fork of C2": (None, _fork_cli(CLI_F, MANAGER, c2, ELSE))}
        for kind, new in news.items():
            for world, (copy, second) in worlds.items():
                regs = [web, api, new] + ([copy] if copy else [])
                for order in itertools.permutations([r["sid"] for r in regs]):
                    with self.subTest(new=kind, world=world, order=_named(order)):
                        ps = [self._cleared_web_process("--resume=%s" % c1), second]
                        d, boot, _ = self._boot(regs, ps, {CLI_U: None, CLI_F: None}, order, lease=lease,
                                                starts={CLI_U: "7"})
                        self.assertEqual(boot.signaled(), [], "every process is spared")
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                        self.assertEqual(_named(boot.started), ["api"])
                        self.assertEqual(sorted(m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                                for m in self._down(boot)),
                                         sorted([new["name"]] + ([copy["name"]] if copy else [])))
                        self.assertEqual(sb.read_reg(Path(d), new["sid"]).get("queue"), new["queue"])

    def test_a_thread_or_copy_made_after_its_closed_parents_second_clear_is_started_beside_the_parents_process_whose_lease_holder_is_a_gone_host_and_a_copys_first_process_naming_their_conversation(self):
        # web was cleared, resumed on the conversation that clear gave it (C1), and cleared again inside that CLI, so
        # its lastSid is C2 while the CLI's command line names only C1. web's row is closed. The CLI survives with no
        # tag and with web's lease at its start time, whose holder is a session host that is gone: the boot files a
        # holder-gone row about it, does not signal it and does not name it on the left-alone line. A thread or a copy
        # made from web after the second clear, still before its first message, has C2 as its lastSid, a queued
        # message and no process of its own. Beside them is the first process of another copy of web's, made after the
        # second clear and still before that copy's first message, with no tag and no lease, naming C2 on its
        # --resume. api has a cut turn. In every row order, the thread or copy and api are started, the other copy
        # stays down, and nothing is signaled
        c1, c2 = "22222222-3333-4444-5555-6666eeee0930", "22222222-3333-4444-5555-6666ffff0930"
        lease = {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "host", "pid": P + 70, "start": "3"}, "t": time.time()}
        web = {"sid": WEB, "name": "web", "lastSid": c2, "_tail": "working", "alive": False}
        api = {"sid": SID, "name": "api", "lastSid": SID, "_tail": "working"}
        news = {"a thread": dict(self._thread(first_message=False), lastSid=c2),
                "a copy": {"sid": CP2, "name": "web copy two", "lastSid": c2, "forkOf": WEB,
                           "forkedFrom": dict(self.LINEAGE), "queue": ["a first message typed to the copy"]}}
        copy = {"sid": CPY, "name": "web copy", "lastSid": c2, "forkOf": WEB, "forkedFrom": dict(self.LINEAGE),
                "_tail": "working"}
        for kind, new in news.items():
            regs = [web, api, new, copy]
            for order in itertools.permutations([r["sid"] for r in regs]):
                with self.subTest(new=kind, order=_named(order)):
                    ps = [self._cleared_web_process("--resume=%s" % c1), _fork_cli(CLI_F, MANAGER, c2, CPY)]
                    d, boot, _ = self._boot(regs, ps, {CLI_U: None, CLI_F: None}, order, lease=lease,
                                            starts={CLI_U: "7"})
                    self.assertEqual(sorted(_named(boot.started)), sorted(["api", new["name"]]))
                    self.assertEqual([m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                      for m in self._down(boot)], ["web copy"])
                    self.assertEqual(boot.signaled(), [], "nothing is signaled")
                    left = [m for m in boot.said if "left alone" in m]
                    self.assertEqual(len(left), 1, boot.said)
                    self.assertNotIn("claude pid %d" % CLI_U, left[0])
                    self.assertEqual([r.get("cliPid") for r in _rows(d) if r.get("kind") == "lease.holder-gone"],
                                     [CLI_U])

    def test_a_copied_sessions_process_with_its_session_id_after_its_resume_holds_the_copy_not_the_session_it_was_copied_from(self):
        copy = {"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "working"}    # its turn was cut too
        d, boot, _ = self._boot([self._parent(), copy], [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                {CLI_F: None}, (WEB, CPY))                              # no tag: an older build's CLI
        self.assertEqual(boot.signaled(), [])
        self.assertEqual(_named(boot.started), ["web"], "the copy is not resumed beside the spared process")
        down = self._down(boot)
        self.assertEqual(len(down), 1, boot.said)
        self.assertIn("web copy", down[0], "the copy is the one held, not the session it was copied from")
        self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
        self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"])
        self.assertEqual(sb.read_reg(Path(d), CPY).get("queue") or [], [], "nothing of the resume reached the copy")
        self.assertFalse([r for r in _states(d, CPY) if "machineCut" in r], _states(d, CPY))
        self.assertEqual(sb.last_state_value(Path(d), CPY), "working")

    def test_a_copied_sessions_process_with_its_session_id_after_its_resume_holds_the_copy_before_its_first_message_not_its_parent(self):
        # a copied session before its first message: as for a thread, it keeps the lastSid it was made with
        # (SdkBackend.fork), here web's, and its fork flag unspent, and its fork CLI, spared (no tag: an older build's),
        # writes the copy's own sid. The own-sid half of the stay-down match holds the copy and the parent resumes, in
        # either order. A copied session resumes at boot for a cut turn or a queue, among other reasons, and each case
        # gives it one of the two
        reasons = {"a queued first message": {"queue": ["a first message typed to the copy"]},
                   "a cut turn": {"_tail": "working"}}
        for reason, extra in reasons.items():
            for order in ((WEB, CPY), (CPY, WEB)):
                with self.subTest(copy_has=reason, first=self._first(order)):
                    copy = dict({"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB}, **extra)
                    d, boot, _ = self._boot([self._parent(), copy], [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                            {CLI_F: None}, order)
                    self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                    self.assertEqual(_named(boot.started), ["web"],
                                     "the parent resumes its cut turn, and the copy is not started beside the spared "
                                     "process")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web copy", down[0], "the copy is the one held, not the session it was copied from")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"], _states(d, WEB))
                    self.assertEqual(sb.read_reg(Path(d), CPY).get("queue") or [], extra.get("queue", []),
                                     "nothing of the resume reached the copy, and its own message stays in its queue")
                    self.assertFalse([r for r in _states(d, CPY) if "machineCut" in r], _states(d, CPY))

    def test_a_copied_session_whose_only_reason_to_start_is_a_question_or_dead_tasks_stays_down_beside_its_process(self):
        # a copied session past its first message whose turn was not cut and whose queue is empty: the boot would start
        # it only to tell it of a question it had up when the old kernel died (pendingAsk) or of background tasks that
        # died with it (bgTasks). Its fork CLI survives (no tag: an older build's), so the copy stays down with that
        # left on its reg, and the parent resumes its cut turn, in either order
        reasons = {"a question it had up": {"pendingAsk": True},
                   "dead background tasks": {"bgTasks": [{"toolUseId": "toolu_01", "desc": "watch the build",
                                                          "type": "local_bash", "since": 100}]}}
        for reason, extra in reasons.items():
            for order in ((WEB, CPY), (CPY, WEB)):
                with self.subTest(copy_has=reason, first=self._first(order)):
                    copy = dict({"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "waiting",
                                 "forkedFrom": dict(self.LINEAGE)}, **extra)
                    d, boot, _ = self._boot([self._parent(), copy], [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                            {CLI_F: None}, order)
                    self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                    self.assertEqual(_named(boot.started), ["web"],
                                     "the parent resumes its cut turn, and the copy is not started beside the spared "
                                     "process")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web copy", down[0], "the copy is the one held, not the session it was copied from")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    reg = sb.read_reg(Path(d), CPY)
                    self.assertEqual(reg.get("queue") or [], [], "no notice reached the copy's queue")
                    for key, value in extra.items():
                        self.assertEqual(reg.get(key), value, "it stays on the copy's reg")
                    self.assertEqual(sb.last_state_value(Path(d), CPY), "waiting")

    TASKS = [{"toolUseId": "toolu_01", "desc": "watch the build", "type": "local_bash", "since": 100}]

    def test_a_copied_session_with_a_question_or_dead_tasks_and_a_cut_turn_or_a_queue_stays_down_beside_its_process(self):
        # a copied session past its first message that carries a question it had up (pendingAsk) or dead background
        # tasks (bgTasks) together with a cut turn or a queued message. Its fork CLI survives (another kernel's tag, or
        # none), so the copy stays down with none of that touched (no notice in its queue, no machine-cut stamp), and
        # the parent resumes its cut turn, in either order
        reasons = {"a question it had up": {"pendingAsk": True}, "dead background tasks": {"bgTasks": self.TASKS}}
        others = {"a cut turn": {"_tail": "working"},
                  "a queued message": {"_tail": "waiting", "queue": ["a message typed to the copy"]}}
        for reason, extra in reasons.items():
            for other, more in others.items():
                for tag in ("another kernel's", "none"):
                    for order in ((WEB, CPY), (CPY, WEB)):
                        with self.subTest(copy_has=reason, beside=other, tag=tag, first=self._first(order)):
                            copy = dict({"sid": CPY, "name": "web copy", "lastSid": CPY,
                                         "forkedFrom": dict(self.LINEAGE)}, **extra, **more)
                            d, boot, _ = self._boot([self._parent(), copy], [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                                    {CLI_F: _tag(_root()) if tag == "another kernel's" else None},
                                                    order)
                            self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                            self.assertEqual(_named(boot.started), ["web"],
                                             "the parent resumes its cut turn, and the copy is not started beside the "
                                             "spared process")
                            down = self._down(boot)
                            self.assertEqual(len(down), 1, boot.said)
                            self.assertIn("web copy", down[0], "the copy is the one held, not its parent")
                            self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1],
                                             [sb.BOOT_RESUME_NUDGE])
                            reg = sb.read_reg(Path(d), CPY)
                            self.assertEqual(reg.get("queue") or [], more.get("queue", []),
                                             "no notice reached the copy's queue, and its own message stays there")
                            for key, value in extra.items():
                                self.assertEqual(reg.get(key), value, "it stays on the copy's reg")
                            self.assertFalse([r for r in _states(d, CPY) if "machineCut" in r], _states(d, CPY))
                            self.assertEqual(sb.last_state_value(Path(d), CPY), more["_tail"])

    def test_a_thread_with_a_queued_reply_and_a_question_or_dead_tasks_stays_down_beside_its_process(self):
        # a thread past its first message with a queued reply that also carries a question it had up or dead
        # background tasks. Its fork CLI survives (another kernel's tag, or none), so the thread stays down with its
        # reply and the rest left on its reg, and the parent resumes its cut turn, in either order
        reasons = {"a question it had up": {"pendingAsk": True}, "dead background tasks": {"bgTasks": self.TASKS}}
        for reason, extra in reasons.items():
            for tag in ("another kernel's", "none"):
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(thread_has=reason, tag=tag, first=self._first(order)):
                        d, boot, _ = self._boot([self._parent(), dict(self._thread(), **extra)],
                                                [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                                {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order)
                        self._assert_thread_held_parent_resumed(d, boot)
                        reg = sb.read_reg(Path(d), THR)
                        for key, value in extra.items():
                            self.assertEqual(reg.get(key), value, "it stays on the thread's reg")

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_parent_under_its_own_live_host_is_attached_beside_its_threads_spared_fork_process_with_its_session_id_after_its_resume(self):
        me = os.getpid()
        st = sb.proc_start(me)
        lease = {"sid": WEB, "fsid": WEB, "name": "web", "pid": me, "start": st, "version": "",
                 "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
        d, boot, be = self._boot([self._parent(tail="waiting"), self._thread()], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                 {CLI_F: _tag(_root())}, (WEB, THR), lease=lease, starts={me: st})
        (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
        self.assertEqual(summary.get("attached"), 1, "the parent's live host is attached")
        self.assertEqual(_named(sorted(be._boot_attach_sids)), ["web"])
        self.assertEqual(_named(boot.started), ["web"], "the attach, and no start for the thread")
        down = self._down(boot)
        self.assertEqual(len(down), 1, boot.said)
        self.assertIn("web thread", down[0], "the thread is the one held, not its parent")

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_parent_under_its_own_live_host_is_attached_beside_a_copy_past_its_first_message_held_by_its_process_with_its_session_id_after_its_resume(self):
        # a copied session past its first message with a cut turn, whose fork CLI survives (another kernel's tag, or
        # none): the copy is held, and its parent, under its own live host, is attached, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        copy = {"sid": CPY, "name": "web copy", "lastSid": CPY, "forkedFrom": dict(self.LINEAGE), "_tail": "working"}
        for tag in ("another kernel's", "none"):
            for order in ((WEB, CPY), (CPY, WEB)):
                with self.subTest(tag=tag, first=self._first(order)):
                    lease = {"sid": WEB, "fsid": WEB, "name": "web", "pid": me, "start": st, "version": "",
                             "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
                    d, boot, be = self._boot([self._parent(tail="waiting"), copy],
                                             [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                             {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order,
                                             lease=lease, starts={me: st})
                    self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                    (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                    self.assertEqual(summary.get("attached"), 1, "the parent's live host is attached")
                    self.assertEqual(_named(sorted(be._boot_attach_sids)), ["web"])
                    self.assertEqual(_named(boot.started), ["web"], "the attach, and no start for the copy")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web copy", down[0], "the copy is the one held, not its parent")
                    self.assertFalse([r for r in _states(d, CPY) if "machineCut" in r], _states(d, CPY))
                    self.assertEqual(sb.last_state_value(Path(d), CPY), "working")

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_parent_under_its_own_live_host_is_attached_beside_a_thread_or_copy_before_its_first_message_held_by_its_process_with_its_session_id_after_its_resume(self):
        # a thread or copied session before its first message, whose fork CLI survives (another kernel's tag, or none):
        # the fork is held, and its parent, under its own live host, is attached, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        copy = {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB, "forkedFrom": dict(self.LINEAGE),
                "queue": ["a first message typed to the copy"]}
        for kind, fork in {"thread": self._thread(first_message=False), "copied session": copy}.items():
            for tag in ("another kernel's", "none"):
                for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                    with self.subTest(fork=kind, tag=tag, first=self._first(order)):
                        lease = {"sid": WEB, "fsid": WEB, "name": "web", "pid": me, "start": st, "version": "",
                                 "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
                        d, boot, be = self._boot([self._parent(tail="waiting"), fork],
                                                 [_fork_cli(CLI_F, MANAGER, WEB, fork["sid"])],
                                                 {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order,
                                                 lease=lease, starts={me: st})
                        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                        (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                        self.assertEqual(summary.get("attached"), 1, "the parent's live host is attached")
                        self.assertEqual(_named(sorted(be._boot_attach_sids)), ["web"])
                        self.assertEqual(_named(boot.started), ["web"], "the attach, and no start for the fork")
                        down = self._down(boot)
                        self.assertEqual(len(down), 1, boot.said)
                        self.assertIn(fork["name"], down[0], "the fork is the one held, not its parent")
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"],
                                         "the fork's message stays in its queue")

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_thread_with_no_reply_under_its_own_live_host_stays_down_unattached_beside_its_spared_process(self):
        # a thread with no queued reply, under its own live host, while its fork CLI (another kernel's tag, or none)
        # survives writing the thread's conversation. Past its first message the thread's lastSid is its own sid;
        # before it, web's, so only the thread's own sid holds it. The stay-down test runs ahead of the attach branch,
        # so the thread is held and not attached. The parent resumes its cut turn, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        threads = {"past its first message": dict(self._thread(), queue=[]),
                   "before its first message": dict(self._thread(first_message=False), queue=[])}
        for case, thread in threads.items():
            # the lease's fsid is web's conversation, which the thread's CLI resumed, until a heartbeat after the init
            # moves it on to the thread's
            lease = {"sid": THR, "fsid": thread["lastSid"], "name": "web thread", "pid": me, "start": st,
                     "version": "", "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
            for tag in ("another kernel's", "none"):
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(thread=case, tag=tag, first=self._first(order)):
                        d, boot, be = self._boot([self._parent(), thread], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                                 {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order,
                                                 lease=lease, starts={me: st})
                        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                        (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                        self.assertEqual(summary.get("attached"), 0, "the held thread's live host is not attached")
                        self.assertEqual(_named(sorted(be._boot_attach_sids)), [])
                        self.assertEqual(_named(boot.started), ["web"],
                                         "the parent resumes, and the thread is neither attached nor started")
                        down = self._down(boot)
                        self.assertEqual(len(down), 1, boot.said)
                        self.assertIn("web thread", down[0], "the thread is the one held, not its parent")
                        self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_copied_session_under_its_own_live_host_stays_down_unattached_beside_its_spared_process(self):
        # a copied session with a cut turn, under its own live host, while its fork CLI (another kernel's tag, or none)
        # survives writing the copy's conversation. Past its first message the copy's lastSid is its own sid; before
        # it, web's, so only the copy's own sid holds it. The stay-down test runs ahead of the attach branch, so the
        # copy is held and not attached. The parent resumes its cut turn, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        copy = {"sid": CPY, "name": "web copy", "forkedFrom": dict(self.LINEAGE), "_tail": "working"}
        copies = {"past its first message": dict(copy, lastSid=CPY),
                  "before its first message": dict(copy, lastSid=WEB, forkOf=WEB)}
        for case, cp in copies.items():
            # the lease's fsid is web's conversation, which the copy's CLI resumed, until a heartbeat after the init
            # moves it on to the copy's
            lease = {"sid": CPY, "fsid": cp["lastSid"], "name": "web copy", "pid": me, "start": st,
                     "version": "", "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
            for tag in ("another kernel's", "none"):
                for order in ((WEB, CPY), (CPY, WEB)):
                    with self.subTest(copy=case, tag=tag, first=self._first(order)):
                        d, boot, be = self._boot([self._parent(), cp], [_fork_cli(CLI_F, MANAGER, WEB, CPY)],
                                                 {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order,
                                                 lease=lease, starts={me: st})
                        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                        (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                        self.assertEqual(summary.get("attached"), 0, "the held copy's live host is not attached")
                        self.assertEqual(_named(sorted(be._boot_attach_sids)), [])
                        self.assertEqual(_named(boot.started), ["web"],
                                         "the parent resumes, and the copy is neither attached nor started")
                        down = self._down(boot)
                        self.assertEqual(len(down), 1, boot.said)
                        self.assertIn("web copy", down[0], "the copy is the one held, not its parent")
                        self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                        self.assertFalse([r for r in _states(d, CPY) if "machineCut" in r], _states(d, CPY))
                        self.assertEqual(sb.last_state_value(Path(d), CPY), "working")

    def test_an_own_orphan_of_a_thread_is_ended_and_its_row_names_the_thread(self):
        for first_message in (True, False):
            for order in ((WEB, THR), (THR, WEB)):
                with self.subTest(first_message=first_message, first=self._first(order)):
                    d, boot, _ = self._boot([self._parent(tail="waiting"), self._thread(first_message)],
                                            [_fork_cli(CLI_F, MANAGER, WEB, THR)], {CLI_F: OWN}, order)
                    self.assertEqual(boot.signaled(), [CLI_F], "this kernel's own orphan is ended")
                    self.assertEqual(self._down(boot), [], "nothing is held once the orphan is gone")
                    self.assertEqual(_named(boot.started), ["web thread"], "the thread is started for its reply")
                    reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                    self.assertEqual(len(reaped), 1, _rows(d))
                    row = reaped[0]
                    self.assertEqual([row.get("cliPid"), row.get("name")] + _named([row.get("sid"), row.get("fsid")]),
                                     [CLI_F, "web thread", "web thread", "web thread"])

    def test_the_census_row_for_a_threads_unleased_process_names_the_threads_conversation(self):
        # another kernel on this state root (hosts off there) runs the thread's CLI as its child, no lease. The census
        # admits the CLI by the parent's lastSid on its argv and files one row, whose fsid is the conversation the CLI
        # writes and whose session the boot finds by that id. A closed thread (its row not alive, its lastSid its own
        # sid) is named too: the boot hands the census every row's own sid, closed rows included, and finds a row's
        # session by own sid when no live row has the fsid as its lastSid
        closed = {"sid": THR, "name": "web thread", "threadOf": WEB, "lastSid": THR, "alive": False}
        threads = {"past its first message": self._thread(), "before its first message": self._thread(False),
                   "closed": closed}
        for case, thread in threads.items():
            for order in ((WEB, THR), (THR, WEB)):
                with self.subTest(thread=case, first=self._first(order)):
                    d, boot, _ = self._boot([self._parent(tail="waiting"), thread],
                                            ["  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                                             _fork_cli(CLI_F, KERNEL_K, WEB, THR)], {CLI_F: OWN}, order)
                    self.assertEqual(boot.signaled(), [], "a live kernel's child is kept")
                    rows = [r for r in _rows(d) if r.get("kind") == "lease.cli-without-lease"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                     [CLI_F, "web thread", "web thread"])

    def test_the_census_row_for_a_live_kernels_child_whose_lease_does_not_hold_names_the_thread(self):
        # another kernel on this state root runs the thread's fork CLI as its child, and the CLI holds a lease written
        # at the fork's launch (sid the thread's, fsid web's) that does not hold: its heartbeat is stale, or its kernel
        # holder is gone. The thread is before its first message or closed, so its own sid is no live row's lastSid.
        # The boot keeps the CLI and files one row about it, whose fsid is the thread's own sid
        closed = {"sid": THR, "name": "web thread", "threadOf": WEB, "lastSid": THR, "alive": False}
        threads = {"before its first message": self._thread(first_message=False), "closed": closed}
        now = time.time()
        holders = {"stale heartbeat": ({"kind": "kernel", "pid": KERNEL_K, "start": "5"}, now - 10 * sb.LEASE_TTL_S),
                   "kernel gone": ({"kind": "kernel", "pid": P + 94, "start": "5"}, now)}
        for case, (holder, beat) in holders.items():
            lease = {"sid": THR, "fsid": WEB, "name": "web thread", "pid": CLI_F, "start": "7", "version": "",
                     "holder": holder, "t": beat}
            for tcase, thread in threads.items():
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(lease=case, thread=tcase, first=self._first(order)):
                        d, boot, _ = self._boot([self._parent(tail="waiting"), thread],
                                                ["  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                                                 _fork_cli(CLI_F, KERNEL_K, WEB, THR)], {}, order, lease=lease,
                                                starts={CLI_F: "7", KERNEL_K: "5"})
                        self.assertEqual(boot.signaled(), [], "a live kernel's child is kept")
                        rows = [r for r in _rows(d) if r.get("cliPid") == CLI_F]
                        self.assertEqual([r.get("kind") for r in rows], ["lease.cli-without-lease"], _rows(d))
                        self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                         [CLI_F, "web thread", "web thread"])

    def test_the_census_rows_about_a_threads_process_whose_lease_does_not_hold_name_the_threads_conversation(self):
        # lease_census's rows about a leased CLI whose argv names a live row's lastSid and whose lease does not hold
        # (holder gone, stale heartbeat) take their fsid from the argv (cli_sid_of) ahead of the lease's: for the
        # thread's fork CLI, the thread's conversation, whichever row's lastSid comes first. The lease is the one
        # written at the fork's launch: its fsid is the conversation the CLI resumed (the lease write takes resume_sid
        # or sid), the parent's, until a heartbeat after the init moves it on, so a row that took the lease's fsid
        # first would name the parent's conversation. One row per failing lease check
        gone, live = P + 80, P + 81
        starts = {CLI_F: "7", live: "5"}
        now = time.time()
        cases = {"host gone": ("host", gone, now, "lease.holder-gone"),
                 "kernel gone": ("kernel", gone, now, "lease.holder-gone"),
                 "stale heartbeat": ("kernel", live, now - 10 * sb.LEASE_TTL_S, "lease.stale-heartbeat")}
        for case, (kind, hpid, beat, row_kind) in cases.items():
            lease = {"sid": THR, "fsid": WEB, "name": "web thread", "pid": CLI_F, "start": "7", "version": "",
                     "holder": {"kind": kind, "pid": hpid, "start": "5"}, "t": beat}
            for lastsids in ([WEB, THR], [THR, WEB]):
                with self.subTest(lease=case, first=self._first(lastsids)):
                    census = sb.lease_census([_fork_cli(CLI_F, MANAGER, WEB, THR)], lastsids, os.getpid(), [lease],
                                             now=now, start=starts.get)
                    rows = [r for r in census["problems"] if r.get("cliPid") == CLI_F]
                    self.assertEqual([r.get("kind") for r in rows], [row_kind], census["problems"])
                    self.assertEqual(_named([rows[0].get("sid"), rows[0].get("fsid")]), ["web thread", "web thread"])

    def test_the_holder_gone_row_about_a_threads_process_names_the_thread_before_its_first_message_or_closed(self):
        # the thread's fork CLI holds a lease written at its launch (sid the thread's, fsid web's) whose host is gone,
        # and the thread's own sid is no live row's lastSid: before its first message its lastSid is web's, and once
        # closed its row is not alive. The boot keeps the CLI and files one holder-gone row, whose fsid is the thread's
        # own sid, which the boot hands the census with every row's own sid
        closed = {"sid": THR, "name": "web thread", "threadOf": WEB, "lastSid": THR, "alive": False}
        threads = {"before its first message": self._thread(first_message=False), "closed": closed}
        lease = {"sid": THR, "fsid": WEB, "name": "web thread", "pid": CLI_F, "start": "7", "version": "",
                 "holder": {"kind": "host", "pid": P + 94, "start": "5"}, "t": time.time()}
        for case, thread in threads.items():
            for order in ((WEB, THR), (THR, WEB)):
                with self.subTest(thread=case, first=self._first(order)):
                    d, boot, _ = self._boot([self._parent(tail="waiting"), thread],
                                            [_fork_cli(CLI_F, MANAGER, WEB, THR)], {}, order, lease=lease,
                                            starts={CLI_F: "7"})
                    self.assertEqual(boot.signaled(), [], "a CLI whose host is gone is kept")
                    rows = [r for r in _rows(d) if r.get("kind") == "lease.holder-gone"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                     [CLI_F, "web thread", "web thread"])

    def test_the_row_about_a_threads_ended_process_whose_lease_does_not_hold_names_the_thread(self):
        # the thread's fork CLI carries this root's tag and holds a lease written at its launch (sid the thread's, fsid
        # web's) that does not hold: its kernel holder is gone, its heartbeat is stale, or it names the CLI's pid with
        # another start time. The thread is before its first message or closed. The boot ends the CLI and files one row
        # for the failing check, whose fsid is the thread's own sid
        closed = {"sid": THR, "name": "web thread", "threadOf": WEB, "lastSid": THR, "alive": False}
        threads = {"before its first message": self._thread(first_message=False), "closed": closed}
        live = P + 95
        now = time.time()
        leases = {"kernel gone": ({"kind": "kernel", "pid": P + 94, "start": "5"}, now, "7", "lease.holder-gone"),
                  "stale heartbeat": ({"kind": "kernel", "pid": live, "start": "5"}, now - 10 * sb.LEASE_TTL_S, "7",
                                      "lease.stale-heartbeat"),
                  "another start time": ({"kind": "kernel", "pid": live, "start": "5"}, now, "8",
                                         "lease.no-live-process")}
        for case, (holder, beat, start, kind) in leases.items():
            lease = {"sid": THR, "fsid": WEB, "name": "web thread", "pid": CLI_F, "start": start, "version": "",
                     "holder": holder, "t": beat}
            for tcase, thread in threads.items():
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(lease=case, thread=tcase, first=self._first(order)):
                        d, boot, _ = self._boot([self._parent(tail="waiting"), thread],
                                                [_fork_cli(CLI_F, MANAGER, WEB, THR)], {CLI_F: OWN}, order,
                                                lease=lease, starts={CLI_F: "7", live: "5"})
                        self.assertEqual(boot.signaled(), [CLI_F], "this kernel's own orphan is ended")
                        rows = [r for r in _rows(d) if r.get("kind") == kind]
                        self.assertEqual(len(rows), 1, _rows(d))
                        self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                         [CLI_F, "web thread", "web thread"])

    def test_a_closed_threads_process_with_its_session_id_after_its_resume_does_not_hold_its_parent_which_resumes(self):
        # a closed thread (its row not alive, its lastSid its own sid) whose fork CLI outlived the kernel. The CLI
        # writes the closed thread's conversation, so no live session is held and the parent resumes its cut turn.
        # With this kernel's tag the CLI is ended, and the reaped row names the closed thread, found by its own sid
        closed = {"sid": THR, "name": "web thread", "threadOf": WEB, "lastSid": THR, "alive": False}
        for spared in (True, False):
            for order in ((WEB, THR), (THR, WEB)):
                with self.subTest(spared=spared, first=self._first(order)):
                    d, boot, _ = self._boot([self._parent(), closed], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                            {CLI_F: _tag(_root()) if spared else OWN}, order)
                    self.assertEqual(boot.signaled(), [] if spared else [CLI_F])
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1 if spared else 0,
                                     "a spared process is named once on the left-alone line: the boot got past it")
                    self.assertEqual(self._down(boot), [], "no live session is held for the closed thread's process")
                    self.assertEqual(_named(boot.started), ["web"], "the parent resumes its cut turn")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"], _states(d, WEB))
                    reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                    self.assertEqual(len(reaped), 0 if spared else 1, _rows(d))
                    if not spared:
                        row = reaped[0]
                        self.assertEqual([row.get("cliPid"), row.get("name")]
                                         + _named([row.get("sid"), row.get("fsid")]),
                                         [CLI_F, "web thread", "web thread", "web thread"])

    def test_a_fork_another_kernel_made_with_its_session_id_and_fork_flag_after_its_resume_holds_none_of_ours(self):
        # a session of another kernel's, forked from web's conversation: its CLI, launched with its --session-id and
        # --fork-session after its --resume, outlived that kernel, loaded web's conversation and writes its own, an id
        # no row here has. It holds none of our sessions: web resumes its cut turn, and a thread or copied session of
        # web's before its first message, whose lastSid is web's conversation, is started for its queued message, in
        # either order; the process is still left alone and named
        copy = {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB, "forkedFrom": dict(self.LINEAGE),
                "queue": ["a first message typed to the copy"]}
        worlds = {"web alone": [], "a thread of web's": [self._thread(first_message=False)],
                  "a copied session of web's": [copy]}
        for world, forks in worlds.items():
            ids = tuple(f["sid"] for f in forks)
            for order in dict.fromkeys(((WEB,) + ids, ids + (WEB,))):
                with self.subTest(world=world, first=_named(order)[0]):
                    d, boot, _ = self._boot([self._parent()] + forks, [_fork_cli(CLI_F, MANAGER, WEB, ELSE)],
                                            {CLI_F: _tag(_root())}, order)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                     "the spared process is named once")
                    self.assertEqual(self._down(boot), [], boot.said)
                    self.assertEqual(sorted(_named(boot.started)), sorted(["web"] + [f["name"] for f in forks]),
                                     "the session the fork was made from resumes, and its forks are started")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"],
                                    _states(d, WEB))

    def test_a_thread_or_copy_past_its_first_message_is_not_held_by_a_resume_of_its_parents_conversation_whose_command_line_names_neither_of_its_ids(self):
        # a plain resume of web's conversation survives (another kernel's tag). A thread or copied session past its
        # first message has its own sid as its lastSid, which that process does not name, and carries web as its
        # forkedFrom: web is held and the fork is started (the thread for its reply, the copy for its cut turn), in
        # either order
        copy = {"sid": CPY, "name": "web copy", "lastSid": CPY, "_tail": "working", "forkedFrom": dict(self.LINEAGE)}
        for kind, fork in {"thread": self._thread(), "copied session": copy}.items():
            for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                with self.subTest(fork=kind, first=self._first(order)):
                    d, boot, _ = self._boot([self._parent(), fork], [_cli(CLI_U, MANAGER, sid=WEB)],
                                            {CLI_U: _tag(_root())}, order)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual(_named(boot.started), [fork["name"]])
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web", down[0])
                    self.assertNotIn(fork["name"], down[0], "the parent is the one held, not its fork")
                    self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                    self.assertEqual(sb.last_state_value(Path(d), WEB), "working")

    def test_a_held_parents_fork_before_its_first_message_is_started_beside_a_process_naming_neither_of_its_ids_unless_the_processs_lease_names_the_parent_whose_lastsid_the_fork_has(self):
        # web was cleared, so its lastSid has moved on from its sid. A thread or copied session before its first message
        # carries web's sid as its forkOf, and as its lastSid the conversation web had when the fork was made: web's sid
        # before the clear, web's moved-on conversation after it. A spared process (no tag) holds web: a plain resume of
        # web's moved-on conversation beside a fork made before the clear, or web's process from before the clear,
        # admitted by its lease (its kernel holder is gone), beside a fork made after it. The process names neither of
        # the fork's ids, its lastSid or its sid. In either order, the fork is started beside the plain resume, and held
        # beside web's leased process: web was cleared inside that process, which may be writing web's moved-on
        # conversation, the fork's lastSid, which the fork's launch copies
        def cli(flags):
            return ("  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                    % (CLI_U, MANAGER, flags))
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "working"}
        lease = {"sid": WEB, "fsid": WEB, "name": "web", "pid": CLI_U, "start": "7", "version": "",
                 "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}
        # each world: the fork's lastSid, the spared process, web's lease on it, whether the fork is started
        worlds = {"made before the clear": (WEB, cli("--resume %s" % WEB2), None, True),
                  "made after the clear": (WEB2, cli("--resume=%s" % WEB), lease, False)}
        for world, (fork_last, line, web_lease, started) in worlds.items():
            forks = {"thread": dict(self._thread(first_message=False), lastSid=fork_last),
                     "copied session": {"sid": CPY, "name": "web copy", "lastSid": fork_last, "forkOf": WEB,
                                        "forkedFrom": dict(self.LINEAGE),
                                        "queue": ["a first message typed to the copy"]}}
            for kind, fork in forks.items():
                for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                    with self.subTest(fork=kind, world=world, first=self._first(order)):
                        d, boot, _ = self._boot([web, fork], [line], {CLI_U: None}, order, lease=web_lease,
                                                starts={CLI_U: "7"})
                        self.assertEqual(boot.signaled(), [])
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                         "the census admitted the process and it was spared")
                        self.assertEqual(_named(boot.started), [fork["name"]] if started else [],
                                         "the fork is started beside the plain resume, and held beside web's process")
                        self.assertEqual(sorted(m.split("boot reconcile: ", 1)[1].split(" stays down", 1)[0]
                                                for m in self._down(boot)),
                                         ["web"] if started else sorted(["web", fork["name"]]))
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])
                        self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                        self.assertEqual(sb.last_state_value(Path(d), WEB), "working")

    def test_a_process_whose_ids_include_the_shared_conversation_holds_the_parent_and_a_fork_before_its_first_message(self):
        # a thread or copied session before its first message, made while web's lastSid was web's sid, still has that
        # lastSid, so a spared process (no tag) of each kind below, whose ids include that conversation, holds both, in
        # either order. web is a copy of api, whose row is closed
        def cli(flags):
            return ("  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                    % (CLI_U, MANAGER, flags))
        processes = {"a plain resume": cli("--resume %s" % WEB),
                     "a fresh launch": cli("--session-id=%s" % WEB),
                     "a fresh launch, its flag apart from its value": cli("--session-id %s" % WEB),
                     "a resume with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --resume=%s" % WEB),
                     "a fresh launch with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --session-id=%s" % WEB),
                     "web's own fork process": cli("--resume=%s --session-id=%s --fork-session" % (SID, WEB)),
                     "web's own fork process with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --resume=%s --session-id=%s --fork-session"
                             % (SID, WEB)),
                     "web's own fork process, its flags apart from their values":
                         cli("--resume %s --session-id %s --fork-session" % (SID, WEB)),
                     "web's fork process, its --session-id ahead of its --resume":
                         cli("--session-id=%s --fork-session --resume=%s" % (WEB, SID)),
                     "a resume of api with web's sid on a --session-id after it and no --fork-session":
                         cli("--resume=%s --session-id=%s" % (SID, WEB)),
                     "a plain resume with api's sid after a --session-id in its prompt":
                         cli("--append-system-prompt see --session-id %s for details --resume=%s" % (SID, WEB)),
                     "a resume of api with web's sid after a --session-id in its prompt":
                         cli("--append-system-prompt see --session-id %s for details --resume=%s" % (WEB, SID)),
                     "a resume with api's sid after a --resume in its prompt":
                         cli("--append-system-prompt see --resume %s for details --resume=%s" % (SID, WEB)),
                     "another kernel's fork, its --session-id and --fork-session ahead of its --resume":
                         cli("--session-id=%s --fork-session --resume=%s" % (ELSE, WEB)),
                     "another kernel's fork, its --session-id ahead of its --resume and --fork-session after it":
                         cli("--session-id %s --resume %s --fork-session" % (ELSE, WEB)),
                     "another kernel's fork, only its --fork-session ahead of its --resume":
                         cli("--fork-session --resume=%s --session-id=%s" % (WEB, ELSE)),
                     "a resume with both fork flags in the appended prompt ahead of it":
                         cli("--append-system-prompt use --fork-session with --session-id %s --resume=%s"
                             % (ELSE, WEB)),
                     "a resume with --fork-session and no --session-id": cli("--resume=%s --fork-session" % WEB),
                     "a resume with a --session-id none of ours and no --fork-session":
                         cli("--resume=%s --session-id=%s" % (WEB, ELSE)),
                     "a resume with a --session-id none of ours and --fork-sessions, which is not the fork flag":
                         cli("--resume=%s --session-id=%s --fork-sessions" % (WEB, ELSE)),
                     "a plain resume among other flags in claude-agent-sdk's order":
                         cli(_sdk_flags("--resume=%s" % WEB)),
                     "a fresh launch among other flags in claude-agent-sdk's order":
                         cli(_sdk_flags("--session-id=%s" % WEB)),
                     "a plain resume among other flags in claude-agent-sdk's order, with no appended prompt":
                         cli(_sdk_flags("--resume=%s" % WEB, prompt=_SDK_NO_PROMPT)),
                     "a fresh launch among other flags in claude-agent-sdk's order, with no appended prompt":
                         cli(_sdk_flags("--session-id=%s" % WEB, prompt=_SDK_NO_PROMPT)),
                     "a plain resume among other flags in claude-agent-sdk's order, with the thinking-summaries flags":
                         cli(_sdk_flags("--resume=%s" % WEB, thinking=_SDK_THINKING)),
                     "a fresh launch among other flags in claude-agent-sdk's order, with the thinking-summaries flags":
                         cli(_sdk_flags("--session-id=%s" % WEB, thinking=_SDK_THINKING))}
        forks = {"thread": self._thread(first_message=False),
                 "copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB,
                                    "queue": ["a first message typed to the copy"]}}
        api = {"sid": SID, "name": "api", "lastSid": SID, "alive": False}
        for process, line in processes.items():
            for kind, fork in forks.items():
                for order in ((WEB, fork["sid"], SID), (fork["sid"], WEB, SID)):
                    with self.subTest(process=process, fork=kind, first=self._first(order)):
                        d, boot, _ = self._boot([self._parent(), fork, api], [line], {CLI_U: None}, order)
                        self.assertEqual(boot.signaled(), [])
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                         "the census admitted the process and it was spared")
                        self.assertEqual(_named(boot.started), [],
                                         "neither is started beside the process on their conversation")
                        down = self._down(boot)
                        self.assertEqual(len(down), 2, boot.said)
                        self.assertEqual(len([m for m in down if fork["name"] in m]), 1, down)
                        self.assertEqual(len([m for m in down if "web" in m and fork["name"] not in m]), 1, down)
                        self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                        self.assertEqual(sb.last_state_value(Path(d), WEB), "working")
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])

    def test_after_the_parents_clear_a_forks_own_process_holds_it_and_a_plain_resume_or_fresh_launch_of_their_conversation_holds_both(self):
        # web was cleared, so its lastSid has moved on from its sid. A thread or copied session made after the clear,
        # still before its first message, has web's moved-on conversation as its lastSid (SdkBackend.fork copies the
        # parent's lastSid). A spared process (no tag) on that conversation, a plain resume or a fresh launch of it,
        # holds both. The fork's own first process (_fork_cli: it loads that conversation and writes the fork's sid)
        # holds only the fork, and web resumes its cut turn. Each in either order
        def cli(flags):
            return ("  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                    % (CLI_U, MANAGER, flags))
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "working"}
        forks = {"thread": dict(self._thread(first_message=False), lastSid=WEB2),
                 "copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB2, "forkOf": WEB,
                                    "queue": ["a first message typed to the copy"]}}
        for kind, fork in forks.items():
            processes = {"a plain resume": cli("--resume %s" % WEB2),
                         "a fresh launch": cli("--session-id=%s" % WEB2),
                         "the fork's own first process": _fork_cli(CLI_U, MANAGER, WEB2, fork["sid"])}
            for process, line in processes.items():
                own = process == "the fork's own first process"
                for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                    with self.subTest(fork=kind, process=process, first=self._first(order)):
                        d, boot, _ = self._boot([web, fork], [line], {CLI_U: None}, order)
                        self.assertEqual(boot.signaled(), [])
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                         "the census admitted the process and it was spared")
                        down = self._down(boot)
                        self.assertEqual(len([m for m in down if fork["name"] in m]), 1, down)
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])
                        web_queue = sb.read_reg(Path(d), WEB).get("queue") or []
                        if own:
                            self.assertEqual(_named(boot.started), ["web"],
                                             "web resumes its cut turn, and the fork is not started beside its process")
                            self.assertEqual(len(down), 1, boot.said)
                            self.assertEqual(web_queue[:1], [sb.BOOT_RESUME_NUDGE])
                            self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"],
                                            _states(d, WEB))
                        else:
                            self.assertEqual(_named(boot.started), [],
                                             "neither is started beside the process on their conversation")
                            self.assertEqual(len(down), 2, boot.said)
                            self.assertEqual(len([m for m in down if "web" in m and fork["name"] not in m]), 1, down)
                            self.assertEqual(web_queue, [])
                            self.assertEqual(sb.last_state_value(Path(d), WEB), "working")

    def test_after_the_parents_clear_a_process_whose_ids_include_the_shared_conversation_holds_the_parent_and_a_fork_before_its_first_message(self):
        # web was cleared, so its lastSid has moved on from its sid. A thread or copied session made after the clear,
        # still before its first message, has web's moved-on conversation as its lastSid (SdkBackend.fork copies the
        # parent's lastSid). A spared process (no tag) of each kind below, whose ids include that conversation, holds
        # both, in either order. api's row is closed
        def cli(flags):
            return ("  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                    % (CLI_U, MANAGER, flags))
        processes = {"a fork process of api's writing the conversation":
                         cli("--resume=%s --session-id=%s --fork-session" % (SID, WEB2)),
                     "a fork process of api's writing the conversation, with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --resume=%s --session-id=%s --fork-session"
                             % (SID, WEB2)),
                     "a fork process of api's writing the conversation, its flags apart from their values":
                         cli("--resume %s --session-id %s --fork-session" % (SID, WEB2)),
                     "a fork process of api's, its --session-id ahead of its --resume":
                         cli("--session-id=%s --fork-session --resume=%s" % (WEB2, SID)),
                     "a resume of api with the conversation's id on a --session-id after it and no --fork-session":
                         cli("--resume=%s --session-id=%s" % (SID, WEB2)),
                     "a resume of api with the conversation's id after a --session-id in its prompt":
                         cli("--append-system-prompt see --session-id %s for details --resume=%s" % (WEB2, SID)),
                     "a resume with api's sid after a --session-id in its prompt":
                         cli("--append-system-prompt see --session-id %s for details --resume=%s" % (SID, WEB2)),
                     "a resume with api's sid after a --resume in its prompt":
                         cli("--append-system-prompt see --resume %s for details --resume=%s" % (SID, WEB2)),
                     "another kernel's fork, its --session-id and --fork-session ahead of its --resume":
                         cli("--session-id=%s --fork-session --resume=%s" % (ELSE, WEB2)),
                     "another kernel's fork, its --session-id ahead of its --resume and --fork-session after it":
                         cli("--session-id %s --resume %s --fork-session" % (ELSE, WEB2)),
                     "another kernel's fork, only its --fork-session ahead of its --resume":
                         cli("--fork-session --resume=%s --session-id=%s" % (WEB2, ELSE)),
                     "a resume with both fork flags in the appended prompt ahead of it":
                         cli("--append-system-prompt use --fork-session with --session-id %s --resume=%s"
                             % (ELSE, WEB2)),
                     "a resume with --fork-session and no --session-id": cli("--resume=%s --fork-session" % WEB2),
                     "a resume with a --session-id none of ours and no --fork-session":
                         cli("--resume=%s --session-id=%s" % (WEB2, ELSE)),
                     "a resume with a --session-id none of ours and --fork-sessions, which is not the fork flag":
                         cli("--resume=%s --session-id=%s --fork-sessions" % (WEB2, ELSE)),
                     "a resume with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --resume=%s" % WEB2),
                     "a fresh launch with the appended prompt ahead of its flags":
                         cli("--append-system-prompt see the notes --session-id=%s" % WEB2),
                     "a fresh launch, its flag apart from its value": cli("--session-id %s" % WEB2),
                     "a plain resume among other flags in claude-agent-sdk's order":
                         cli(_sdk_flags("--resume=%s" % WEB2)),
                     "a fresh launch among other flags in claude-agent-sdk's order":
                         cli(_sdk_flags("--session-id=%s" % WEB2)),
                     "a plain resume among other flags in claude-agent-sdk's order, with no appended prompt":
                         cli(_sdk_flags("--resume=%s" % WEB2, prompt=_SDK_NO_PROMPT)),
                     "a fresh launch among other flags in claude-agent-sdk's order, with no appended prompt":
                         cli(_sdk_flags("--session-id=%s" % WEB2, prompt=_SDK_NO_PROMPT)),
                     "a plain resume among other flags in claude-agent-sdk's order, with the thinking-summaries flags":
                         cli(_sdk_flags("--resume=%s" % WEB2, thinking=_SDK_THINKING)),
                     "a fresh launch among other flags in claude-agent-sdk's order, with the thinking-summaries flags":
                         cli(_sdk_flags("--session-id=%s" % WEB2, thinking=_SDK_THINKING))}
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "working"}
        forks = {"thread": dict(self._thread(first_message=False), lastSid=WEB2),
                 "copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB2, "forkOf": WEB,
                                    "queue": ["a first message typed to the copy"]}}
        api = {"sid": SID, "name": "api", "lastSid": SID, "alive": False}
        for process, line in processes.items():
            for kind, fork in forks.items():
                for order in ((WEB, fork["sid"], SID), (fork["sid"], WEB, SID)):
                    with self.subTest(process=process, fork=kind, first=self._first(order)):
                        d, boot, _ = self._boot([web, fork, api], [line], {CLI_U: None}, order)
                        self.assertEqual(boot.signaled(), [])
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                         "the census admitted the process and it was spared")
                        self.assertEqual(_named(boot.started), [],
                                         "neither is started beside the process on their conversation")
                        down = self._down(boot)
                        self.assertEqual(len(down), 2, boot.said)
                        self.assertEqual(len([m for m in down if fork["name"] in m]), 1, down)
                        self.assertEqual(len([m for m in down if "web" in m and fork["name"] not in m]), 1, down)
                        self.assertEqual(sb.read_reg(Path(d), WEB).get("queue") or [], [])
                        self.assertEqual(sb.last_state_value(Path(d), WEB), "working")
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])

    def test_a_fork_made_before_the_parents_clear_is_held_by_its_own_first_process_with_its_session_id_after_its_resume_and_the_parent_resumes(self):
        # a thread or copied session made before web was cleared, still before its first message, has web's sid as its
        # lastSid, while web's lastSid has moved on. The fork's own first process (_fork_cli: it loads web's sid and
        # writes the fork's) survives with no tag: the fork is held, and web resumes its cut turn, in either order
        web = {"sid": WEB, "name": "web", "lastSid": WEB2, "_tail": "working"}
        forks = {"thread": self._thread(first_message=False),
                 "copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB,
                                    "forkedFrom": dict(self.LINEAGE), "queue": ["a first message typed to the copy"]}}
        for kind, fork in forks.items():
            for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                with self.subTest(fork=kind, first=self._first(order)):
                    d, boot, _ = self._boot([web, fork], [_fork_cli(CLI_F, MANAGER, WEB, fork["sid"])], {CLI_F: None},
                                            order)
                    self.assertEqual(boot.signaled(), [])
                    self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                     "the census admitted the process and it was spared")
                    self.assertEqual(_named(boot.started), ["web"], "web resumes, and the fork is not started")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn(fork["name"], down[0], "the fork is the one held, not web")
                    self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"], _states(d, WEB))

    def _forks_of(self, conv, past):
        """The thread and the copied session, each before its first message with `conv` as its lastSid, or past it."""
        thread = self._thread(first_message=past)
        copy = {"sid": CPY, "name": "web copy", "forkedFrom": dict(self.LINEAGE)}
        if past:
            copy.update({"lastSid": CPY, "_tail": "working"})
        else:
            thread["lastSid"] = conv
            copy.update({"lastSid": conv, "forkOf": WEB, "queue": ["a first message typed to the copy"]})
        return {"thread": thread, "copied session": copy}

    # a fork's own first process: _fork_cli's order, claude-agent-sdk's order (with or without an appended prompt, or
    # with the thinking-summaries flags), or each flag apart from its value
    _FORK_LAUNCHES = (("_fork_cli's order", _fork_cli), ("the SDK's order", _sdk_fork_cli),
                      ("the SDK's order with no appended prompt", _sdk_fork_cli_no_prompt),
                      ("the SDK's order with the thinking-summaries flags", _sdk_fork_cli_thinking),
                      ("flags apart", _apart_fork_cli))

    def test_a_forks_first_process_with_its_session_id_after_its_resume_as_the_sdk_launches_it_or_with_its_flags_apart_holds_only_the_fork_and_the_parent_resumes(self):
        # a thread's or copied session's own first process, spelled each way in _FORK_LAUNCHES, spared (another
        # kernel's tag, or none): before the fork's first message and past it, each with no /clear of web, made after
        # web's clear (the process resumes web's moved-on conversation) or made before it (the process resumes web's
        # sid). Only the fork is held, and web resumes its cut turn, in either order
        # each world: (web's lastSid, the conversation the fork's process resumes, past the fork's first message)
        worlds = {"before its first message, no clear": (WEB, WEB, False),
                  "before its first message, made after the clear": (WEB2, WEB2, False),
                  "before its first message, made before the clear": (WEB2, WEB, False),
                  "past its first message, no clear": (WEB, WEB, True),
                  "past its first message, made after the clear": (WEB2, WEB2, True),
                  "past its first message, made before the clear": (WEB2, WEB, True)}
        for world, (web_last, conv, past) in worlds.items():
            for kind, fork in self._forks_of(conv, past).items():
                for spelling, launch in self._FORK_LAUNCHES:
                    for tag in ("another kernel's", "none"):
                        for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                            with self.subTest(world=world, fork=kind, spelling=spelling, tag=tag,
                                              first=self._first(order)):
                                web = {"sid": WEB, "name": "web", "lastSid": web_last, "_tail": "working"}
                                d, boot, _ = self._boot([web, fork], [launch(CLI_F, MANAGER, conv, fork["sid"])],
                                                        {CLI_F: _tag(_root()) if tag == "another kernel's" else None},
                                                        order)
                                self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                                self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                                 "the census admitted the process and it was spared")
                                self.assertEqual(_named(boot.started), ["web"],
                                                 "web resumes, and the fork is not started beside its process")
                                down = self._down(boot)
                                self.assertEqual(len(down), 1, boot.said)
                                self.assertIn(fork["name"], down[0], "the fork is the one held, not web")
                                self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1],
                                                 [sb.BOOT_RESUME_NUDGE])
                                self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"],
                                                _states(d, WEB))
                                self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork.get("queue"))

    def _fork_lease(self, fork, conv):
        """A fork's own lease on its first process, as _lease_open writes one: the fork's sid, the conversation the
        process resumed, the process's pid and start time, and a kernel holder that is gone."""
        return {"sid": fork["sid"], "fsid": conv, "name": fork["name"], "pid": CLI_F, "start": "7", "version": "",
                "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()}

    def test_a_forks_first_process_carrying_the_forks_own_lease_before_its_first_message_holds_only_the_fork_and_the_parent_resumes(self):
        # a thread's or copied session's own first process, as the SDK launches it, survives with no tag before the
        # fork's first message, and carries the fork's own lease: with no /clear of web, made after web's clear (the
        # process resumes web's moved-on conversation) or made before it (the process resumes web's sid). The fork's
        # row has forkOf set and the conversation the process resumes as its lastSid. Only the fork is held, and web
        # resumes its cut turn, in either order
        # each world: (web's lastSid, the conversation the fork's process resumes)
        worlds = {"no clear": (WEB, WEB), "made after the clear": (WEB2, WEB2), "made before the clear": (WEB2, WEB)}
        for world, (web_last, conv) in worlds.items():
            for kind, fork in self._forks_of(conv, False).items():
                for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                    with self.subTest(world=world, fork=kind, first=self._first(order)):
                        web = {"sid": WEB, "name": "web", "lastSid": web_last, "_tail": "working"}
                        d, boot, _ = self._boot([web, fork], [_sdk_fork_cli(CLI_F, MANAGER, conv, fork["sid"])],
                                                {CLI_F: None}, order, lease=self._fork_lease(fork, conv),
                                                starts={CLI_F: "7"})
                        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1, boot.said)
                        self.assertEqual(_named(boot.started), ["web"],
                                         "web resumes, and the fork is not started beside its process")
                        down = self._down(boot)
                        self.assertEqual(len(down), 1, boot.said)
                        self.assertIn(fork["name"], down[0], "the fork is the one held, not web")
                        self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                        self.assertTrue([r for r in _states(d, WEB) if r.get("machineCut") == "restart"],
                                        _states(d, WEB))
                        self.assertEqual(sb.read_reg(Path(d), fork["sid"]).get("queue"), fork["queue"])

    def test_a_threads_first_process_carrying_the_threads_own_lease_before_its_first_message_holds_no_other_fork_made_from_the_same_conversation(self):
        # the thread, before its first message, and a second fork of web's, another thread or a copied session, also
        # before its first message, so both have web's conversation as their lastSid. The thread's own first process
        # (_fork_cli) survives with no tag and carries the thread's own lease. Only the thread is held: web resumes its
        # cut turn and the second fork is started for its queued message, whichever of the three rows is listed first
        thread = self._thread(first_message=False)
        others = {"another thread": dict(self._thread(first_message=False), sid=TH2, name="thread two"),
                  "a copied session": {"sid": CPY, "name": "web copy", "lastSid": WEB, "forkOf": WEB,
                                       "forkedFrom": dict(self.LINEAGE),
                                       "queue": ["a first message typed to the copy"]}}
        for kind, other in others.items():
            for order in ((WEB, THR, other["sid"]), (other["sid"], THR, WEB), (THR, other["sid"], WEB)):
                with self.subTest(second_fork=kind, first=_named(order)[0]):
                    d, boot, _ = self._boot([self._parent(), thread, other], [_fork_cli(CLI_F, MANAGER, WEB, THR)],
                                            {CLI_F: None}, order, lease=self._fork_lease(thread, WEB),
                                            starts={CLI_F: "7"})
                    self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                    self.assertEqual(sorted(_named(boot.started)), sorted(["web", other["name"]]),
                                     "web resumes and the second fork is started")
                    down = self._down(boot)
                    self.assertEqual(len(down), 1, boot.said)
                    self.assertIn("web thread", down[0], "the thread whose process it is is the one held")
                    self.assertEqual((sb.read_reg(Path(d), WEB).get("queue") or [])[:1], [sb.BOOT_RESUME_NUDGE])
                    self.assertEqual(sb.read_reg(Path(d), THR).get("queue"), ["a reply typed to the thread"])

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_parent_under_its_own_live_host_is_attached_beside_its_forks_first_process_with_its_session_id_after_its_resume_as_the_sdk_launches_it_or_with_its_flags_apart(self):
        # a thread or copied session whose own first process, spelled each way in _FORK_LAUNCHES, survives (another
        # kernel's tag, or none): before the fork's first message with no /clear of web, and past it with no clear,
        # made after web's clear (the process resumes web's moved-on conversation) or made before it (the process
        # resumes web's sid). The fork is held, and web, under its own live host, is attached, in either order
        me = os.getpid()
        st = sb.proc_start(me)
        # each world: (web's lastSid, the conversation the fork's process resumes, past the fork's first message)
        worlds = {"before its first message, no clear": (WEB, WEB, False),
                  "past its first message, no clear": (WEB, WEB, True),
                  "past its first message, made after the clear": (WEB2, WEB2, True),
                  "past its first message, made before the clear": (WEB2, WEB, True)}
        for world, (web_last, conv, past) in worlds.items():
            for kind, fork in self._forks_of(conv, past).items():
                for spelling, launch in self._FORK_LAUNCHES:
                    for tag in ("another kernel's", "none"):
                        for order in ((WEB, fork["sid"]), (fork["sid"], WEB)):
                            with self.subTest(world=world, fork=kind, spelling=spelling, tag=tag,
                                              first=self._first(order)):
                                web = {"sid": WEB, "name": "web", "lastSid": web_last, "_tail": "waiting"}
                                lease = {"sid": WEB, "fsid": web_last, "name": "web", "pid": me, "start": st,
                                         "version": "", "holder": {"kind": "host", "pid": me, "start": st},
                                         "t": time.time()}
                                d, boot, be = self._boot(
                                    [web, fork], [launch(CLI_F, MANAGER, conv, fork["sid"])],
                                    {CLI_F: _tag(_root()) if tag == "another kernel's" else None}, order,
                                    lease=lease, starts={me: st})
                                self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
                                (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
                                self.assertEqual(summary.get("attached"), 1, "the parent's live host is attached")
                                self.assertEqual(_named(sorted(be._boot_attach_sids)), ["web"])
                                self.assertEqual(_named(boot.started), ["web"], "the attach, and no start for the fork")
                                down = self._down(boot)
                                self.assertEqual(len(down), 1, boot.said)
                                self.assertIn(fork["name"], down[0], "the fork is the one held, not its parent")

    _EXTRA_FLAGS = ("--model opus", "--verbose", "--permission-mode default", "--max-turns 3",
                    "--mcp-config /x/mcp.json", "--include-partial-messages",
                    "--resume-session-at=0f0f0f0f-0000-4000-8000-000000000001")

    @classmethod
    def _generated_flags(cls, rng, C, fork_sid, template, prompt, spelling):
        """The flags of one command line: template `template` of the nine below, which name C or the fork's sid on a
        --resume or a --session-id; each flag spelled with '=', with a space or, for "mixed", either one chosen by
        `rng`; the flags shuffled by `rng` with up to three of _EXTRA_FLAGS among them; and `prompt`, (place, text),
        the appended prompt ahead of the flags or after them, or None."""
        def flag(name, value):
            return "%s%s%s" % (name, rng.choice(("=", " ")) if spelling == "mixed" else spelling, value)
        other = rng.choice((SID, WEB) if C != WEB else (SID,))      # another id of ours
        templates = (
            lambda: [flag("--resume", C)],                                             # a plain resume
            lambda: [flag("--session-id", C)],                                         # a fresh launch
            lambda: [flag("--resume", other), flag("--session-id", C), "--fork-session"],      # a fork writing C
            lambda: [flag("--resume", C), flag("--session-id", ELSE), "--fork-session"],       # another kernel's fork
            lambda: [flag("--resume", other), flag("--session-id", C)],
            lambda: [flag("--resume", C), flag("--session-id", ELSE)] + rng.choice(([], ["--fork-sessions"])),
            lambda: [flag("--resume", C), "--fork-session"],
            lambda: [flag("--resume", C), flag("--session-id", C)],
            lambda: [flag("--resume", C), flag("--session-id", fork_sid), "--fork-session"])   # the fork's own
        flags = templates[template]()
        rng.shuffle(flags)
        for x in rng.sample(cls._EXTRA_FLAGS, rng.randrange(4)):
            flags.insert(rng.randrange(len(flags) + 1), x)
        if prompt is not None:
            place, text = prompt
            flags = ["--append-system-prompt " + text] + flags if place == "ahead" else flags + [
                "--append-system-prompt " + text]
        return " ".join(flags)

    def test_over_generated_command_lines_a_parent_or_fork_before_its_first_message_stays_down_exactly_when_cli_writes_sids_returns_its_lastsid_or_sid(self):
        # web and a thread or copied session before its first message share one conversation C: web's sid with no
        # /clear, web's moved-on lastSid after its clear. api's row is closed. Each boot spares one process whose
        # command line is one of the nine templates of _generated_flags, with each appended prompt in `prompts` (none;
        # plain, after the flags or ahead of them; or ahead of them, naming an id after a --session-id or a --resume)
        # and each spelling, its flag order, other flags, tag (another kernel's, or none), fork and row order chosen
        # from a fixed seed. Both sessions have a reason to start (web a cut turn, the fork a queued message).
        # Each stays down exactly when its lastSid or its own sid is among the ids cli_writes_sids returns for that
        # command line, and is started otherwise
        api = {"sid": SID, "name": "api", "lastSid": SID, "alive": False}
        other_root = _root()
        self.addCleanup(shutil.rmtree, other_root, ignore_errors=True)
        for world, (C, seed) in {"no clear": (WEB, 41), "after the clear": (WEB2, 42)}.items():
            rng = random.Random(seed)
            web = {"sid": WEB, "name": "web", "lastSid": C, "_tail": "working"}
            held_c = n = 0
            prompts = [None, ("after", "see the notes"), ("ahead", "see the notes")] + [
                ("ahead", "see %s %s for details" % (flag, named)) for named in (SID, ELSE, C, WEB, "<fork>")
                for flag in ("--session-id", "--resume")]
            for template in range(9):
                for prompt in prompts:
                    for spelling in ("=", " ", "mixed"):
                        kind = rng.choice(("thread", "copied session"))
                        fork = self._forks_of(C, False)[kind]
                        text = prompt and (prompt[0], prompt[1].replace("<fork>", fork["sid"]))
                        flags = self._generated_flags(rng, C, fork["sid"], template, text, spelling)
                        tag = rng.choice(("another kernel's", "none"))
                        order = rng.choice(((WEB, fork["sid"], SID), (fork["sid"], WEB, SID)))
                        line = "  %d %d /x/claude --output-format stream-json %s --input-format stream-json" % (
                            CLI_U, MANAGER, flags)
                        ids = set(sb.cli_writes_sids(line, [C, fork["sid"], WEB, SID]))
                        held = [r["name"] for r in (web, fork) if r["lastSid"] in ids or r["sid"] in ids]
                        held_c += C in ids
                        n += 1
                        with self.subTest(world=world, template=template, fork=kind, tag=tag, first=self._first(order),
                                          flags=flags):
                            d, boot, _ = self._boot([web, fork, api], [line],
                                                    {CLI_U: _tag(other_root) if tag == "another kernel's" else None},
                                                    order)
                            self.addCleanup(shutil.rmtree, d, ignore_errors=True)
                            self.assertEqual(boot.signaled(), [])
                            self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                                             "the census admitted the process and it was spared")
                            self.assertEqual(sorted(_named(boot.started)),
                                             sorted(r["name"] for r in (web, fork) if r["name"] not in held),
                                             "the boot starts each session cli_writes_sids leaves free (%s)"
                                             % _named(sorted(ids)))
                            down = self._down(boot)
                            self.assertEqual(len(down), len(held), boot.said)
                            for name in held:
                                self.assertEqual(len([m for m in down if " %s stays down" % name in m]), 1, down)
            self.assertEqual(n, 9 * 13 * 3, world)
            self.assertGreater(held_c, n // 2, "%s: most generated lines hold the shared conversation" % world)


API2 = "11111111-2222-3333-4444-5555eeee0923"     # api's conversation after a /clear inside its CLI
CPY2 = "55555555-6666-4777-8888-111111110930"     # the copy's conversation after a /clear inside its CLI


class AHeldSessionStaysDown(unittest.TestCase):
    """Each boot here has one spared process (another kernel's tag, or none), and one session it holds is the subject:
    a session that is neither a thread nor a copy, or a thread or a copied session past its first message or before it,
    held by its lastSid, by its own sid, or by both. The subject's row carries each combination of a cut turn, a queued
    message, a question it had up and dead background tasks, from none of them to all four (a fork before its first
    message: a cut turn, a queued message, both or neither), and is listed first or last. The subject is not started,
    one stays-down line names it, and its row and state tail keep what they carried; under its own live host it is not
    attached either. Every boot runs through the real _boot_reconcile (the Boot harness above)."""

    TASKS = [{"toolUseId": "toolu_01", "desc": "watch the build", "type": "local_bash", "since": 100}]
    LINEAGE = {"sid": WEB, "name": "web", "cut": "", "t": 1}     # forkedFrom, which SdkBackend.fork writes on each fork
    # what the subject's row carries: (its state tail, its reg's fields)
    CARRIES = {"nothing": ("waiting", {}),
               "a cut turn": ("working", {}),
               "a queued message": ("waiting", {"queue": ["a message typed to it"]}),
               "a cut turn and a queued message": ("working", {"queue": ["a message typed to it"]}),
               "a question it had up": ("waiting", {"pendingAsk": True}),
               "dead background tasks": ("waiting", {"bgTasks": TASKS}),
               "a question it had up and dead background tasks": ("waiting", {"pendingAsk": True, "bgTasks": TASKS}),
               "a queued message, a question and dead tasks": ("waiting", {"queue": ["a message typed to it"],
                                                                           "pendingAsk": True, "bgTasks": TASKS}),
               "a cut turn and a question": ("working", {"pendingAsk": True}),
               "a cut turn and dead tasks": ("working", {"bgTasks": TASKS}),
               "a queued message and a question": ("waiting", {"queue": ["a message typed to it"], "pendingAsk": True}),
               "a queued message and dead tasks": ("waiting", {"queue": ["a message typed to it"], "bgTasks": TASKS}),
               "a cut turn, a question and dead tasks": ("working", {"pendingAsk": True, "bgTasks": TASKS}),
               "a cut turn, a queued message and a question": ("working", {"queue": ["a message typed to it"],
                                                                           "pendingAsk": True}),
               "a cut turn, a queued message and dead tasks": ("working", {"queue": ["a message typed to it"],
                                                                           "bgTasks": TASKS}),
               "a cut turn, a queued message, a question and dead tasks": ("working", {
                   "queue": ["a message typed to it"], "pendingAsk": True, "bgTasks": TASKS})}
    # a fork before its first message is given no question and no dead tasks: the kernel spends forkOf at the fork's
    # init, before a turn can write either
    BEFORE_FIRST_MESSAGE = ("nothing", "a cut turn", "a queued message", "a cut turn and a queued message")

    @staticmethod
    def _cli(flags):
        return "  %d %d /x/claude --output-format stream-json %s --input-format stream-json" % (CLI_U, MANAGER, flags)

    def _worlds(self):
        """{world: (the subject's row, the other rows, the spared process, what the subject's row may carry)}."""
        web = {"sid": WEB, "name": "web", "lastSid": WEB, "_tail": "waiting"}
        past, before = tuple(self.CARRIES), self.BEFORE_FIRST_MESSAGE

        def thread(**kw):
            return dict({"sid": THR, "name": "web thread", "threadOf": WEB, "forkedFrom": dict(self.LINEAGE)}, **kw)

        def copy(**kw):
            return dict({"sid": CPY, "name": "web copy", "forkedFrom": dict(self.LINEAGE)}, **kw)

        # web cleared inside its process, which resumes web's own sid; a thread made before the clear, still before its
        # first message, has web's sid as its lastSid, and the census admits the process by it
        thread_from_before = thread(lastSid=WEB, forkOf=WEB)
        return {
            "neither a thread nor a copy, by its lastSid and its own sid":
                ({"sid": SID, "name": "api", "lastSid": SID}, [], self._cli("--resume=%s" % SID), past),
            "neither a thread nor a copy, by its lastSid, a /clear having moved it on":
                ({"sid": SID, "name": "api", "lastSid": API2}, [], self._cli("--resume=%s" % API2), past),
            "neither a thread nor a copy, by its own sid, cleared inside the process":
                ({"sid": WEB, "name": "web", "lastSid": WEB2}, [thread_from_before], self._cli("--resume=%s" % WEB),
                 past),
            "a thread past its first message, by its lastSid and its own sid, its first process":
                (thread(lastSid=THR), [web], _fork_cli(CLI_U, MANAGER, WEB, THR), past),
            "a thread past its first message, by its lastSid, a resume of it after a /clear":
                (thread(lastSid=THR2), [web], self._cli("--resume=%s" % THR2), past),
            "a thread past its first message, by its own sid, cleared inside its first process":
                (thread(lastSid=THR2), [web], _fork_cli(CLI_U, MANAGER, WEB, THR), past),
            "a copy past its first message, by its lastSid and its own sid, its first process":
                (copy(lastSid=CPY), [web], _fork_cli(CLI_U, MANAGER, WEB, CPY), past),
            "a copy past its first message, by its lastSid, a resume of it after a /clear":
                (copy(lastSid=CPY2), [web], self._cli("--resume=%s" % CPY2), past),
            "a copy past its first message, by its own sid, cleared inside its first process":
                (copy(lastSid=CPY2), [web], _fork_cli(CLI_U, MANAGER, WEB, CPY), past),
            "a thread before its first message, by its own sid, its first process":
                (thread(lastSid=WEB, forkOf=WEB), [web], _fork_cli(CLI_U, MANAGER, WEB, THR), before),
            "a thread before its first message, by its lastSid, a resume of the conversation it shares with web":
                (thread(lastSid=WEB, forkOf=WEB), [web], self._cli("--resume=%s" % WEB), before),
            "a copy before its first message, by its own sid, its first process":
                (copy(lastSid=WEB, forkOf=WEB), [web], _fork_cli(CLI_U, MANAGER, WEB, CPY), before),
            "a copy before its first message, by its lastSid, a resume of the conversation it shares with web":
                (copy(lastSid=WEB, forkOf=WEB), [web], self._cli("--resume=%s" % WEB), before),
        }

    def _boot(self, regs, line, tag, order, lease=None, starts=None):
        d = _root()
        for r in regs:
            r = dict(r)
            tail = r.pop("_tail", None)
            sb.write_reg(Path(d), r["sid"], dict({"cwd": "/tmp", "alive": True}, **r))
            if tail:
                sb.append_state(Path(d), r["sid"], tail)
        if lease:
            sb.write_lease(d, lease)
        boot = Boot(d, ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, line], [], [], tags={CLI_U: tag},
                    starts=starts, sids=order)
        be = boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        return d, boot, be

    @staticmethod
    def _held(boot):
        """The session names the stays-down lines give, one per line."""
        return [m.split("boot reconcile: ", 1)[-1].split(" stays down: ", 1)[0] for m in boot.said
                if " stays down: " in m]

    def _each_boot(self, host):
        """Boot every world, with each thing its subject's row may carry, each tag and each order, under the subject's
        own live host when `host`, and run _assert_held on each boot inside its subTest."""
        me = os.getpid()
        st = sb.proc_start(me) if host else None
        for world, (subject, others, line, carries) in self._worlds().items():
            for what in carries:
                tail, fields = self.CARRIES[what]
                regs = [dict(subject, _tail=tail, **fields)] + others
                ids = tuple(r["sid"] for r in others)
                for tag in ("another kernel's", "none"):
                    for order in dict.fromkeys(((subject["sid"],) + ids, ids + (subject["sid"],))):
                        with self.subTest(world=world, row_has=what, tag=tag, first=_named(order)[0]):
                            lease = starts = None
                            if host:
                                lease = {"sid": subject["sid"], "fsid": subject["lastSid"], "name": subject["name"],
                                         "pid": me, "start": st, "version": "",
                                         "holder": {"kind": "host", "pid": me, "start": st}, "t": time.time()}
                                starts = {me: st}
                            d, boot, be = self._boot(regs, line, _tag(_root()) if tag == "another kernel's" else None,
                                                     order, lease=lease, starts=starts)
                            if host:
                                self.assertEqual(sb._ht().host_lease_state(sb.read_lease(d, subject["sid"]),
                                                                           time.time()), "attach",
                                                 "the subject's own host is live, so the attach branch would take it")
                            self._assert_held(d, boot, be, subject, tail, fields)

    def _assert_held(self, d, boot, be, subject, tail, fields):
        sid, name = subject["sid"], subject["name"]
        self.assertEqual(boot.signaled(), [], "the spared process is not signaled")
        self.assertEqual(len([m for m in boot.said if "left alone" in m]), 1,
                         "the census admitted the process and it was spared")
        self.assertNotIn(name, _named(boot.started), "not started beside the spared process")
        self.assertNotIn(name, _named(sorted(be._boot_attach_sids)), "not attached")
        (summary,) = [r for r in _rows(d) if r.get("kind") == "reconcile.boot"]
        self.assertEqual(summary.get("attached"), 0)
        self.assertEqual(self._held(boot).count(name), 1, boot.said)
        reg = sb.read_reg(Path(d), sid)
        self.assertEqual(reg.get("queue") or [], fields.get("queue", []), "no notice reached its queue")
        self.assertEqual((reg.get("pendingAsk"), reg.get("bgTasks")), (fields.get("pendingAsk"), fields.get("bgTasks")),
                         "what it carries stays on its reg")
        self.assertEqual(sb.last_state_value(Path(d), sid), tail)
        self.assertFalse([r for r in _states(d, sid) if "machineCut" in r], _states(d, sid))

    def test_a_held_thread_copy_or_other_session_stays_down_and_keeps_what_its_row_carries(self):
        self._each_boot(host=False)

    @unittest.skipUnless(LINUX, "the live host lease names this test's process, its start time read from /proc")
    def test_a_held_thread_copy_or_other_session_under_its_own_live_host_stays_down_unattached_and_keeps_what_its_row_carries(self):
        self._each_boot(host=True)


class TheBootsRowsNameTheirSession(unittest.TestCase):
    """The boot files each lease census row it keeps (it drops the rows about a spared process) under the row's lease's
    sid, else under the session whose own sid is the row's fsid, else under a session whose live lastSid it is (the
    boot's sid_of map), else under no session; and the orphan-reaped row about a process it ends under the session of
    the first lease in name order naming the process's pid when that lease's start time is the process's and that
    session has a row, else under the session whose own sid is that row's fsid, else under a session whose live lastSid
    it is, else under no session. The tests here run the real _boot_reconcile."""

    # Two tests share one world. web, a copy of api, was cleared inside its first CLI, which still runs
    # `--resume=<api's sid> --session-id=<web's sid> --fork-session`, so web's lastSid has moved on from its sid. A
    # thread made from web before the clear, still before its first message, keeps web's sid as its lastSid, and is
    # live or closed. Each row is about web's CLI, and its fsid is web's sid.
    THREAD = {"live": True, "closed": False}     # the thread's alive flag

    def _web_cleared_with_a_thread_from_before(self, d, thread_alive):
        moved = "22222222-3333-4444-5555-6666aaaa0930"
        sb.write_reg(Path(d), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": moved})
        sb.write_reg(Path(d), THR, {"sid": THR, "name": "web thread", "cwd": "/tmp", "alive": thread_alive,
                                    "threadOf": WEB, "lastSid": WEB, "forkOf": WEB})
        _reg(d, SID)

    def test_the_orphan_reaped_row_names_the_session_whose_sid_it_is_before_one_whose_live_lastsid_it_is(self):
        # web's CLI carries this root's tag, so the boot ends it. Its row names web, whose own sid the row's fsid is,
        # whether the thread, whose lastSid it is, is live or closed. In either order
        for thread, alive in self.THREAD.items():
            for order in ((WEB, THR, SID), (THR, WEB, SID)):
                with self.subTest(thread=thread, first=_named(order)[0]):
                    d = _root()
                    self._web_cleared_with_a_thread_from_before(d, alive)
                    ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _fork_cli(CLI_B, MANAGER, SID, WEB)]
                    boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
                    rows = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    r = rows[0]
                    self.assertEqual([r.get("cliPid"), r.get("name")] + _named([r.get("sid"), r.get("fsid")]),
                                     [CLI_B, "web", "web", "web"])

    def test_a_census_row_without_a_lease_names_the_session_whose_sid_it_is_before_one_whose_live_lastsid_it_is(self):
        # web's CLI is a child of another live kernel on this state root, with no lease. Its census row's session comes
        # from the boot's sid_of map: web, whose own sid the row's fsid is, whether the thread, whose lastSid it is, is
        # live or closed. In either order
        cli = P + 92
        for thread, alive in self.THREAD.items():
            for order in ((WEB, THR, SID), (THR, WEB, SID)):
                with self.subTest(thread=thread, first=_named(order)[0]):
                    d = _root()
                    self._web_cleared_with_a_thread_from_before(d, alive)
                    ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
                          "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                          _fork_cli(cli, KERNEL_K, SID, WEB)]
                    boot = Boot(d, ps, [], [], tags={}, sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [], "a live kernel's child is kept")
                    rows = [r for r in _rows(d) if r.get("kind") == "lease.cli-without-lease"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                     [cli, "web", "web"])

    def test_a_census_row_names_its_leases_session_before_the_session_the_lookup_finds(self):
        # web was cleared inside a CLI that resumes the conversation an earlier clear gave it (C1), and that CLI holds a
        # lease on this state root, with sid web, whose host is gone: the census keeps the CLI and files a holder-gone
        # row about it, whose fsid is C1. A thread made between the two clears, still before its first message, has C1
        # as its lastSid, and C1 is no row's own sid, so the boot's sid_of map gives the thread for C1. The row names
        # web, the lease's session. In either order
        cli = P + 93
        c1, c2 = "22222222-3333-4444-5555-6666cccc0930", "22222222-3333-4444-5555-6666dddd0930"
        who = {c1: "C1"}.get      # a name, not a uuid, in a failure's output
        for order in ((WEB, THR, SID), (THR, WEB, SID)):
            with self.subTest(first=_named(order)[0]):
                d = _root()
                sb.write_reg(Path(d), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": c2})
                sb.write_reg(Path(d), THR, {"sid": THR, "name": "web thread", "cwd": "/tmp", "alive": True,
                                            "threadOf": WEB, "lastSid": c1, "forkOf": WEB})
                _reg(d, SID)
                sb.write_lease(d, {"sid": WEB, "fsid": c1, "name": "web", "pid": cli, "start": "7", "version": "",
                                   "holder": {"kind": "host", "pid": P + 94, "start": "5"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(cli, MANAGER, sid=c1)]
                boot = Boot(d, ps, [], [], tags={}, starts={cli: "7"}, sids=order)
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [], "a CLI whose host is gone is kept")
                rows = [r for r in _rows(d) if r.get("kind") == "lease.holder-gone"]
                self.assertEqual(len(rows), 1, _rows(d))
                self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid")])
                                 + [who(rows[0].get("fsid"), rows[0].get("fsid"))], [cli, "web", "C1"])

    def test_the_orphan_reaped_row_about_a_leased_process_whose_command_line_names_none_of_our_ids_names_the_leases_session(self):
        # web was cleared, resumed on the conversation that clear gave it (C1), and cleared again inside that CLI, so
        # its lastSid is C2 while the CLI's command line names only C1, which is no live row's lastSid and no row's own
        # sid. The CLI holds web's lease on this state root, whose kernel holder is gone, and carries this root's tag:
        # the census admits it by that lease and the boot ends it. Its row names web, the lease's session, with an
        # empty fsid. In either order
        c1, c2 = "22222222-3333-4444-5555-6666eeee0930", "22222222-3333-4444-5555-6666ffff0930"
        for order in ((WEB, SID), (SID, WEB)):
            with self.subTest(first=_named(order)[0]):
                d = _root()
                sb.write_reg(Path(d), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": c2})
                _reg(d, SID)
                sb.write_lease(d, {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_B, "start": "7", "version": "",
                                   "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
                      "  %d %d /x/claude --output-format stream-json --resume=%s --input-format stream-json"
                      % (CLI_B, MANAGER, c1)]
                boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, starts={CLI_B: "7"}, sids=order)
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
                rows = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                self.assertEqual(len(rows), 1, _rows(d))
                r = rows[0]
                self.assertEqual([r.get("cliPid"), r.get("name"), _named([r.get("sid")])[0], r.get("fsid")],
                                 [CLI_B, "web", "web", ""])

    def test_the_orphan_reaped_row_about_a_leased_process_names_the_leases_session_before_a_session_whose_live_lastsid_its_command_line_names(self):
        # web was cleared inside a CLI that resumes the conversation an earlier clear gave it (C1). The CLI holds web's
        # lease on this state root, whose kernel holder is gone, and carries this root's tag, so the boot ends it. A
        # thread made between the two clears, still before its first message, has C1 as its lastSid, and C1 is no row's
        # own sid. The orphan-reaped row names web, the lease's session, with fsid C1, and the census row about the same
        # process names web too. In either order
        c1, c2 = "22222222-3333-4444-5555-6666c1c10930", "22222222-3333-4444-5555-6666d1d10930"
        who = {c1: "C1"}.get      # a name, not a uuid, in a failure's output
        for order in ((WEB, THR, SID), (THR, WEB, SID)):
            with self.subTest(first=_named(order)[0]):
                d = _root()
                sb.write_reg(Path(d), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": c2})
                sb.write_reg(Path(d), THR, {"sid": THR, "name": "web thread", "cwd": "/tmp", "alive": True,
                                            "threadOf": WEB, "lastSid": c1, "forkOf": WEB})
                _reg(d, SID)
                sb.write_lease(d, {"sid": WEB, "fsid": c1, "name": "web", "pid": CLI_B, "start": "7", "version": "",
                                   "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()})
                ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER, sid=c1)]
                boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, starts={CLI_B: "7"}, sids=order)
                boot.go()
                self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
                reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                self.assertEqual(len(reaped), 1, _rows(d))
                r = reaped[0]
                self.assertEqual([r.get("cliPid"), r.get("name"), _named([r.get("sid")])[0],
                                  who(r.get("fsid"), r.get("fsid"))], [CLI_B, "web", "web", "C1"])
                census = [r for r in _rows(d) if r.get("kind") == "lease.holder-gone"]
                self.assertEqual([[r.get("cliPid")] + _named([r.get("sid")]) for r in census], [[CLI_B, "web"]])

    def test_the_orphan_reaped_row_about_a_process_whose_lease_names_a_session_with_no_row_names_the_session_its_command_line_names(self):
        # this root's tagged CLI resumes api's conversation and holds a lease on this state root, whose kernel holder is
        # gone, for a session that has no row here. The boot ends it, and its row names api
        d = _root()
        _reg(d, SID)
        sb.write_lease(d, {"sid": ELSE, "fsid": SID, "name": "gone", "pid": CLI_B, "start": "7", "version": "",
                           "holder": {"kind": "kernel", "pid": P + 70, "start": "3"}, "t": time.time()})
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER)]
        boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, starts={CLI_B: "7"})
        boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
        reaped = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
        self.assertEqual(len(reaped), 1, _rows(d))
        r = reaped[0]
        self.assertEqual([r.get("cliPid"), r.get("name")] + _named([r.get("sid"), r.get("fsid")]),
                         [CLI_B, "api", "api", "api"])

    def test_rows_about_a_live_kernels_unleased_resumes_of_a_moved_on_conversation_naming_no_other_id_of_ours_name_a_session_whose_lastsid_it_is(self):
        # two CLIs of another live kernel on this state root, with no lease, resuming a session's current
        # conversation, which a /clear moved on from the session's sid. Each row's fsid is that conversation, and its
        # session is found by the session's lastSid: by own sids alone the rows would name no session
        moved = "11111111-2222-3333-4444-5555eeee0923"
        who = {SID: "api", moved: "api's current conversation"}.get   # names, not uuids, in a failure's output
        cli1, cli2 = P + 90, P + 91
        d = _root()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "api", "cwd": "/tmp", "alive": True, "lastSid": moved})
        sb.append_state(Path(d), SID, "waiting")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
              "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
              _cli(cli1, KERNEL_K, sid=moved), _cli(cli2, KERNEL_K, sid=moved)]
        boot = Boot(d, ps, [], [], tags={})
        boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        self.assertEqual(boot.signaled(), [], "a live kernel's children are kept")
        rows = [r for r in _rows(d) if r.get("kind") == "lease.cli-without-lease"]
        self.assertEqual(sorted(r.get("cliPid") for r in rows), [cli1, cli2], _rows(d))
        for r in rows:
            self.assertEqual([who(r.get("sid"), r.get("sid")), who(r.get("fsid"), r.get("fsid"))],
                             ["api", "api's current conversation"], r)

    def test_the_orphan_reaped_row_about_a_resume_of_a_moved_on_conversation_naming_no_other_id_of_ours_names_a_session_whose_lastsid_it_is(self):
        # an orphan with this root's tag resuming a session's current conversation, which a /clear moved on from the
        # session's sid (the command line of a session respawned after a /clear). The boot ends it and files one
        # orphan-reaped row, whose fsid is that conversation and whose session is found by the session's lastSid: by
        # own sids alone the row would name no session
        moved = "22222222-3333-4444-5555-6666ffff0923"
        who = {SID: "api", moved: "api's current conversation"}.get   # names, not uuids, in a failure's output
        d = _root()
        sb.write_reg(Path(d), SID, {"sid": SID, "name": "api", "cwd": "/tmp", "alive": True, "lastSid": moved})
        sb.append_state(Path(d), SID, "waiting")
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER, sid=moved)]
        boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)})
        boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
        rows = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
        self.assertEqual(len(rows), 1, _rows(d))
        r = rows[0]
        self.assertEqual([r.get("cliPid"), r.get("name"), who(r.get("sid"), r.get("sid")),
                          who(r.get("fsid"), r.get("fsid"))], [CLI_B, "api", "api", "api's current conversation"])

    def test_the_orphan_reaped_and_kernel_child_rows_name_a_processs_last_session_id_of_ours_else_its_resume(self):
        # web and a thread past its first message. A process whose only --session-id of ours, the thread's, comes ahead
        # of its --resume names the thread on both rows; another kernel's fork of web's conversation, whose --session-id
        # is none of ours, names web. The orphan-reaped row is about an orphan with this root's tag, which the boot
        # ends; the census row is about a child of another live kernel, with no lease. In either order
        shapes = {"a --session-id of ours ahead of the --resume": ("--session-id=%s --fork-session --resume=%s"
                                                                   % (THR, WEB), "web thread"),
                  "another kernel's fork": ("--resume=%s --session-id=%s --fork-session" % (WEB, ELSE), "web")}
        for shape, (flags, session) in shapes.items():
            for kind in ("reconcile.orphan-reaped", "lease.cli-without-lease"):
                for order in ((WEB, THR), (THR, WEB)):
                    with self.subTest(shape=shape, row=kind, first=_named(order)[0]):
                        d = _root()
                        _reg(d, WEB, name="web")
                        sb.write_reg(Path(d), THR, {"sid": THR, "name": "web thread", "cwd": "/tmp", "alive": True,
                                                    "threadOf": WEB, "lastSid": THR})
                        reaped = kind == "reconcile.orphan-reaped"
                        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
                              "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                              "  %d %d /x/claude --output-format stream-json %s --input-format stream-json"
                              % (CLI_F, MANAGER if reaped else KERNEL_K, flags)]
                        boot = Boot(d, ps, [], [], tags={CLI_F: _tag(d)} if reaped else {}, sids=order)
                        boot.go()
                        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                        self.assertEqual(boot.signaled(), [CLI_F] if reaped else [])
                        rows = [r for r in _rows(d) if r.get("kind") == kind]
                        self.assertEqual(len(rows), 1, _rows(d))
                        self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid"), rows[0].get("fsid")]),
                                         [CLI_F, session, session])

    # Two tests share one world. web was cleared, so its lastSid, W2, is no row's own sid. A thread or a copy made from
    # web after the clear, still before its first message, and then closed, also has W2 as its lastSid. Each row is
    # about a process resuming W2, and its fsid is W2.
    W2 = "22222222-3333-4444-5555-6666a2a20930"
    CLOSED_FORKS = {"a thread": {"sid": THR, "name": "web thread", "threadOf": WEB},
                    "a copy": {"sid": CPY, "name": "web copy"}}

    def _web_cleared_with_a_closed_fork_from_after(self, d, fork):
        sb.write_reg(Path(d), WEB, {"sid": WEB, "name": "web", "cwd": "/tmp", "alive": True, "lastSid": self.W2})
        sb.write_reg(Path(d), fork["sid"], dict(fork, cwd="/tmp", alive=False, lastSid=self.W2, forkOf=WEB,
                                                forkedFrom=dict(ASparedForkHoldsItsOwnConversation.LINEAGE)))
        _reg(d, SID)

    def test_the_orphan_reaped_row_about_a_resume_of_a_live_sessions_moved_on_conversation_names_that_session_and_not_a_closed_thread_or_copy_with_the_same_lastsid(self):
        # an orphan with this root's tag resumes W2. The boot ends it and files one orphan-reaped row, which names web.
        # In either order
        who = {self.W2: "W2"}.get      # a name, not a uuid, in a failure's output
        for kind, fork in self.CLOSED_FORKS.items():
            for order in ((WEB, fork["sid"], SID), (fork["sid"], WEB, SID)):
                with self.subTest(closed=kind, first=_named(order)[0]):
                    d = _root()
                    self._web_cleared_with_a_closed_fork_from_after(d, fork)
                    ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER, _cli(CLI_B, MANAGER, sid=self.W2)]
                    boot = Boot(d, ps, [], [], tags={CLI_B: _tag(d)}, sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [CLI_B], "this kernel's own orphan is ended")
                    rows = [r for r in _rows(d) if r.get("kind") == "reconcile.orphan-reaped"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    r = rows[0]
                    self.assertEqual([r.get("cliPid"), r.get("name")] + _named([r.get("sid")])
                                     + [who(r.get("fsid"), r.get("fsid"))], [CLI_B, "web", "web", "W2"])

    def test_a_census_row_about_a_live_kernels_unleased_resume_of_a_live_sessions_moved_on_conversation_names_that_session_and_not_a_closed_thread_or_copy_with_the_same_lastsid(self):
        # a child of another live kernel on this state root, with no lease, resumes W2. The boot keeps it and files one
        # census row about it, which names web. In either order
        cli = P + 92
        who = {self.W2: "W2"}.get      # a name, not a uuid, in a failure's output
        for kind, fork in self.CLOSED_FORKS.items():
            for order in ((WEB, fork["sid"], SID), (fork["sid"], WEB, SID)):
                with self.subTest(closed=kind, first=_named(order)[0]):
                    d = _root()
                    self._web_cleared_with_a_closed_fork_from_after(d, fork)
                    ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER,
                          "  %d 1 /usr/bin/python3 /x/romp/bin/romp-kernel" % KERNEL_K,
                          _cli(cli, KERNEL_K, sid=self.W2)]
                    boot = Boot(d, ps, [], [], tags={}, sids=order)
                    boot.go()
                    self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
                    self.assertEqual(boot.signaled(), [], "a live kernel's child is kept")
                    rows = [r for r in _rows(d) if r.get("kind") == "lease.cli-without-lease"]
                    self.assertEqual(len(rows), 1, _rows(d))
                    self.assertEqual([rows[0].get("cliPid")] + _named([rows[0].get("sid")])
                                     + [who(rows[0].get("fsid"), rows[0].get("fsid"))], [cli, "web", "W2"])


class TheBootsCostPerProcess(unittest.TestCase):
    """The boot builds its set of our ids once (_OurIds: every live row's lastSid and every row's own sid, closed rows
    included) and hands it to cli_writes_sids for each spared CLI and to cli_sid_of for each reaped one. Handed a list
    instead, each call builds the set again, so each such CLI costs the boot time in proportion to the registry, which
    keeps its closed rows. Counted in lines of the kernel module run under the real _boot_reconcile (sys.settrace),
    which do not depend on the machine's speed, what 39 more CLIs cost is the same with 40 closed rows as with 1,000,
    for untagged CLIs (spared) and for CLIs with this root's tag (reaped). The lease census's own cost per CLI is
    pinned in tests/test_session_events_ledger.py."""

    LIVE = ["%08x-1111-4222-8333-000000000930" % i for i in range(40)]

    def _lines(self, clis, closed, tagged):
        d = _root()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)     # a thousand rows: not left for the run's end
        for i, s in enumerate(self.LIVE):
            sb.write_reg(Path(d), s, {"sid": s, "name": "s%d" % i, "cwd": "/tmp", "alive": True, "lastSid": s})
            sb.append_state(Path(d), s, "idle")
        shut = ["%08x-5555-4666-8777-000000000930" % i for i in range(closed)]
        for i, s in enumerate(shut):
            sb.write_reg(Path(d), s, {"sid": s, "name": "c%d" % i, "cwd": "/tmp", "alive": False, "lastSid": s})
        prompt = "see the notes " * 150       # an appended prompt of about 2 KB, ahead of the flags
        pids = [P + 9000 + i for i in range(clis)]
        ps = ["  %d 1 /usr/lib/systemd/systemd --user" % MANAGER] + [
            "  %d %d /x/claude --output-format stream-json --append-system-prompt %s --resume=%s --input-format "
            "stream-json" % (pid, MANAGER, prompt, self.LIVE[i]) for i, pid in enumerate(pids)]
        boot = Boot(d, ps, [], [], tags={pid: _tag(d) for pid in pids} if tagged else {}, sids=self.LIVE + shut)
        count = [0]
        real = sb.SdkBackend._boot_reconcile
        kernel = real.__code__.co_filename

        def traced(be, regs):
            def tracer(frame, event, arg):
                # the kernel module's lines only, so code outside it that runs inside the window is not counted
                if frame.f_code.co_filename != kernel:
                    return None
                if event == "line":
                    count[0] += 1
                return tracer
            old = sys.gettrace()
            sys.settrace(tracer)
            try:
                return real(be, regs)
            finally:
                sys.settrace(old)
        with mock.patch.object(sb.SdkBackend, "_boot_reconcile", traced):
            boot.go()
        self.assertFalse([m for m in boot.said if "reap failed" in m], boot.said)
        self.assertEqual(boot.signaled(), pids if tagged else [], "every CLI was read: reaped, or spared")
        self.assertEqual(len([m for m in boot.said if "stays down" in m]), 0 if tagged else clis, boot.said)
        return count[0]

    def test_the_boots_cost_per_spared_or_reaped_cli_does_not_grow_with_closed_rows(self):
        for tagged in (False, True):
            with self.subTest(clis="reaped" if tagged else "spared"):
                more = {n: self._lines(40, n, tagged) - self._lines(1, n, tagged) for n in (40, 1000)}
                self.assertGreater(more[40], 0)
                self.assertEqual(more[1000], more[40], "the cost of each CLI grew with the closed rows")


class TheTag(unittest.TestCase):
    def test_the_tag_is_the_hash_of_the_resolved_state_directory(self):
        a, b = tempfile.mkdtemp(), tempfile.mkdtemp()
        self.assertEqual(sb.state_tag_of(a), _tag(a))
        self.assertRegex(sb.state_tag_of(a), r"\A[0-9a-f]{16}\Z")
        self.assertNotEqual(sb.state_tag_of(a), sb.state_tag_of(b), "two state roots are two kernels")
        link = os.path.join(tempfile.mkdtemp(), "link")
        os.symlink(a, link)
        self.assertEqual(sb.state_tag_of(link), sb.state_tag_of(a), "a symlink to the root is the same kernel")
        self.assertEqual(sb.state_tag_of(Path(a)), sb.state_tag_of(a))

    def test_a_unit_lines_tag_is_read_from_its_description(self):
        t = "0123456789abcdef"
        line = _scope_line("romp-session-11111111-4242-1.scope", "romp session %s romp-state=%s" % (SID, t))
        self.assertEqual(sb.unit_state_tag(line), t)
        self.assertIsNone(sb.unit_state_tag(_scope_line("romp-session-11111111-4242-1.scope", "romp session %s" % SID)))
        self.assertIsNone(sb.unit_state_tag("romp-session-11111111-4242-1.scope loaded active running romp-state=0123"),
                          "a malformed tag is no tag")
        self.assertIsNone(sb.unit_state_tag("romp-session-11111111-4242-1.scope loaded active running x romp-state=%sff" % t))
        self.assertIsNone(sb.unit_state_tag(""))
        self.assertEqual(sb.unit_state_tags([line, "", "run-x.scope loaded active running y"]),
                         {"romp-session-11111111-4242-1.scope": t, "run-x.scope": None})

    def test_both_scope_listings_ask_for_the_full_description(self):
        for argv in (sb.SCOPE_LIST_ARGV, sb.HOST_SCOPE_LIST_ARGV):
            self.assertIn("--full", argv, "a truncated description would drop the tag")
            self.assertTrue(argv[-1].endswith("*.scope"), "the glob stays last")

    @unittest.skipUnless(LINUX, "reads /proc/<pid>/environ")
    def test_a_processs_tag_is_read_from_its_environment(self):
        t = "fedcba9876543210"
        env = dict(os.environ, ROMP_STATE_TAG=t)
        tagged = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE, env=env)
        self.addCleanup(tagged.wait, timeout=10); self.addCleanup(tagged.stdin.close)
        env.pop("ROMP_STATE_TAG")
        bare = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE, env=env)
        self.addCleanup(bare.wait, timeout=10); self.addCleanup(bare.stdin.close)
        env["ROMP_STATE_TAG"] = "not-a-tag"
        bad = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE, env=env)
        self.addCleanup(bad.wait, timeout=10); self.addCleanup(bad.stdin.close)
        self.assertEqual(sb.proc_state_tag(tagged.pid), t)
        self.assertIsNone(sb.proc_state_tag(bare.pid))
        self.assertIsNone(sb.proc_state_tag(bad.pid), "a value of another shape is no tag")
        self.assertIsNone(sb.proc_state_tag(P + 5), "a pid no process wears has no tag")

    def test_without_procfs_the_tag_comes_from_ps_E(self):
        t = "fedcba9876543210"
        calls = []
        def run(argv, **kw):
            calls.append(list(argv))
            return mock.Mock(stdout="/x/claude --resume %s HOME=/h ROMP_STATE_TAG=%s PATH=/bin\n" % (SID, t), returncode=0)
        self.assertEqual(sb.proc_state_tag(4242, run=run, procfs=False), t)
        self.assertEqual(calls[0][0], "ps")
        self.assertIn("-E", calls[0])
        self.assertIsNone(sb.proc_state_tag(4242, run=lambda a, **k: mock.Mock(stdout="/x/claude a b\n"), procfs=False))
        def boom(argv, **kw):
            raise OSError("no ps")
        self.assertIsNone(sb.proc_state_tag(4242, run=boom, procfs=False))


class TheProducersTagWhatTheyStart(unittest.TestCase):
    def test_a_host_scope_carries_the_tag_in_its_description(self):
        d = _root()
        be = sb.SdkBackend(d, "/bin/true", lambda *a, **k: None)
        be.cli_scope = True
        spec = Path(d, "hosts", SID, "spawn.json")
        spec.parent.mkdir(parents=True)
        spec.write_text("{}")
        seen = []
        class _Popen:
            def __init__(self, argv, **kw):
                seen.append(list(argv))
        with mock.patch.object(sb.shutil, "which", lambda n: "/usr/bin/" + n), \
             mock.patch.object(sb.subprocess, "Popen", _Popen):
            be._spawn_host(type("S", (), {"sid": SID, "name": "api"})(), spec)
        self.assertEqual(seen[0][0], "systemd-run")
        self.assertIn("--description=romp session host %s romp-state=%s" % (SID, _tag(d)), seen[0])


if __name__ == "__main__":
    unittest.main()
