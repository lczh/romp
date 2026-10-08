#!/usr/bin/env python3
"""The lab's ENVIRONMENT phase (`lab.sh --env-only`): what the lab kernel and the CLIs it starts were handed.

A lab started from inside a live romp session used to inherit that session's identity (its ROMP_SID, the Claude Code
session id the postal tools read, the live kernel's port, the user bus), and the kernel hands its whole environment to
every CLI it starts, so the lab's sessions ran as the live one (2026-10-08). lab.sh now starts from an allowlist; this
phase is the instrument that shows it held, on the real stack:

1. the kernel's own environment (Linux: /proc/<pid>/environ, the environment it was exec'd with) carries no name
   outside the lab's allowlist and the names lab.sh set itself (LAB_ENV_ALLOWED);
2. a session started through the kernel's own door (POST /new) spawns the probe CLI (env-probe-cli.py, the lab's
   ROMP_CLAUDE_BIN in this mode), which writes down its environment: the session's identity there is the one this lab
   minted, never the caller's.

Everything is written under LAB_DIR/probe (kernel-env.json, new.json, cli-<pid>.json) so a test can read the raw
evidence itself rather than trust this script's verdict. stdlib only; no browser, no model, no spend.
"""
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

LAB = Path(os.environ["LAB_DIR"])
PORT = os.environ["PORT"]
TOKEN = os.environ["TOKEN"]
KPID = os.environ["KPID"]
PROJECT = os.environ["PROJECT_DIR"]
PROBE = LAB / "probe"
ALLOWED = set(os.environ.get("LAB_ENV_ALLOWED", "").split())
BASH_OWN = {"PWD", "OLDPWD", "SHLVL", "_"}      # bash sets these on every exec; they carry nothing of the caller's
NAME = "envprobe"
CLI_WAIT_S = 30


PROBLEMS = []


def fail(msg):
    """Note a divergence and keep going, so one run shows every leg's evidence; the phase exits non-zero at the end."""
    PROBLEMS.append(msg)
    print("env phase: FAIL: " + msg)


def post(path, body):
    req = urllib.request.Request("http://127.0.0.1:%s%s" % (PORT, path), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "X-Romp-Token": TOKEN}, method="POST")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


PROBE.mkdir(parents=True, exist_ok=True)

# 1. the kernel's environment, as it was exec'd
environ = Path("/proc", KPID, "environ")
if environ.exists():
    kenv = dict(kv.split("=", 1) if "=" in kv else (kv, "")
                for kv in environ.read_bytes().decode("utf-8", "replace").split("\0") if kv)
    (PROBE / "kernel-env.json").write_text(json.dumps(kenv))
    stray = sorted(n for n in kenv if n not in ALLOWED and n not in BASH_OWN and not n.startswith("LC_"))
    if stray:
        fail("the lab kernel's environment carries names outside the lab's allowlist: %s" % " ".join(stray))
    else:
        print("env phase: the kernel's environment is the allowlist and the lab's own names (%d names)" % len(kenv))
else:
    kenv = None
    print("env phase: no /proc here, so the kernel's environment is not read; the CLI's below stands for it")

# 2. a session through the kernel's own door: its CLI is the probe
ans = post("/new", {"name": NAME, "dir": PROJECT, "backend": "sdk"})
(PROBE / "new.json").write_text(json.dumps(ans))
if not ans.get("ok"):
    fail("the kernel would not start a session: %s" % (ans.get("error") or ans))
    sys.exit(1)
sid = str(ans.get("id") or "")
deadline = time.monotonic() + CLI_WAIT_S
mine = []
while time.monotonic() < deadline:
    mine = []
    for p in sorted(PROBE.glob("cli-*.json")):
        env = json.loads(p.read_text()).get("env") or {}
        if env.get("ROMP_SID") == sid:
            mine.append((p.name, env))
    if mine:
        break
    time.sleep(0.25)
if not mine:
    fail("no CLI carrying the new session's id (%s) started within %ds" % (sid, CLI_WAIT_S))
    sys.exit(1)
for fname, env in mine:
    if env.get("ROMP_SESSION_NAME") != NAME:
        fail("%s: ROMP_SESSION_NAME is %r, not the lab session's %r" % (fname, env.get("ROMP_SESSION_NAME"), NAME))
    added = sorted(n for n in env if kenv is not None and n not in kenv)
    print("env phase: CLI %s runs as the lab's session %s; beyond the kernel's environment it carries only %s"
          % (fname, sid[:8], " ".join(added) if added else "nothing"))
if PROBLEMS:
    sys.exit(1)
print("env phase: OK")
