#!/usr/bin/env python3
"""End the lab's OWN session hosts and their CLIs: `end-hosts.py <lab state root>` (lab.sh's cleanup, 2026-10-08).

A host outlives the kernel that spawned it by design (it carries a session across a kernel restart), and it starts in
a session of its own, so killing the lab kernel leaves a `--session-hosts` lab's hosts running: an idle one for its
unattached grace (900 s by default), and a CLI mid-turn finishing that turn, spending, after the lab directory is gone.

What is ended is read from the given state root ALONE: each host's identity.json (hosts/<sid>/, written at the host's
start) and each host-held lease (leases/<sid>.json: the host and the CLI it runs). A process is signalled only while
its start time still matches the one recorded there (sdk_backend.proc_start, the identity every lease reader uses), so
a pid since reused by anything else is never touched. A host leads its own process group (spawned with a new
session), and its CLI runs in that group, so the group gets SIGTERM, then SIGKILL for whatever still matches after a
short wait. Prints one line saying what it ended. stdlib plus the backend module, loaded only when there is a candidate.
"""
import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
GRACE_S = 5.0


def candidates(state):
    """{pid: (start, what)} from the state root's own files; nothing else is ever a candidate."""
    out = {}

    def add(pid, start, what):
        if isinstance(pid, int) and pid > 1 and start:
            out.setdefault(pid, (str(start), what))

    for p in sorted((state / "hosts").glob("*/identity.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            add(d.get("pid"), d.get("start"), "host")
    for p in sorted((state / "leases").glob("*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        holder = d.get("holder") if isinstance(d, dict) else None
        if not isinstance(holder, dict) or holder.get("kind") != "host":
            continue                                  # a kernel-held lease: its CLI is the (already ended) kernel's child
        add(holder.get("pid"), holder.get("start"), "host")
        add(d.get("pid"), d.get("start"), "cli")
    return out


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: end-hosts.py <lab state root>\n")
        return 2
    state = Path(argv[1])
    found = candidates(state)
    if not found:
        print("lab hosts: none")
        return 0
    sys.path.insert(0, str(ROOT / "kernel"))
    from loadsource import load_source
    sb = load_source("romp_sdk_backend", ROOT / "kernel" / "sdk_backend.py")
    me, my_group = os.getpid(), os.getpgid(0)

    def still(pid, start):
        """The recorded process, still running: its start time matches, and it is not a zombie awaiting its reaper.

        Two reads, so a reaper can land between them: a process the first read finds is gone by the second, or caught
        mid-reap (state X). Both have ended; read as running, they made a process that ended on SIGTERM count as
        needing SIGKILL."""
        if sb.proc_start(pid) != start:
            return False
        try:
            with open("/proc/%d/stat" % pid) as f:
                return f.read().rsplit(")", 1)[1].split()[0] not in ("Z", "X")
        except OSError:
            return not os.path.isdir("/proc")         # with procfs, reaped since the first read; macOS: start decides

    def signal_one(pid, what, sig):
        try:
            if what == "host" and os.getpgid(pid) == pid and pid != my_group:
                os.killpg(pid, sig)                   # the host's own group: the host, its CLI and what the CLI runs in it
            else:
                os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    live = {pid: v for pid, v in found.items() if pid != me and still(pid, v[0])}
    for pid, (_start, what) in live.items():
        signal_one(pid, what, signal.SIGTERM)
    deadline = time.monotonic() + GRACE_S
    while time.monotonic() < deadline and any(still(pid, v[0]) for pid, v in live.items()):
        time.sleep(0.1)
    stubborn = {pid: v for pid, v in live.items() if still(pid, v[0])}
    for pid, (_start, what) in stubborn.items():
        signal_one(pid, what, signal.SIGKILL)
    hosts = sum(1 for v in live.values() if v[1] == "host")
    print("lab hosts: ended %d host(s) and %d CLI(s)%s" % (hosts, len(live) - hosts,
                                                         " (%d needed SIGKILL)" % len(stubborn) if stubborn else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
