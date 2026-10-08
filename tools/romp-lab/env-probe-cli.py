#!/usr/bin/env python3
# The PROBE CLI for the lab's environment phase (`lab.sh --env-only`): the lab kernel resolves claude through
# ROMP_CLAUDE_BIN, so pointing it here makes every CLI the kernel starts write down what it was started with, then exit.
# It speaks no protocol: a session the kernel connects to it fails to start, which is all the phase needs (the spawn is
# the event, its environment the evidence). `-v` / `--version` answer like the real CLI's version check and record
# nothing. SYNTHETIC ONLY: it reads no credential and writes only under ROMP_LAB_PROBE_DIR, the lab's temp root.
import json
import os
import sys

if any(a in ("-v", "--version") for a in sys.argv[1:]):
    print("2.1.999 (Claude Code)")
    sys.exit(0)
out = os.environ.get("ROMP_LAB_PROBE_DIR")
if out:
    os.makedirs(out, exist_ok=True)
    tmp = os.path.join(out, ".cli-%d.json.tmp" % os.getpid())
    with open(tmp, "w") as f:
        json.dump({"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ)}, f)
    os.replace(tmp, os.path.join(out, "cli-%d.json" % os.getpid()))   # whole or absent: the driver polls for it
sys.exit(3)
