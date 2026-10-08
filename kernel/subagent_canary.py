#!/usr/bin/env python3
"""Subagents at standard speed's version canary: probe the recipe on the Claude Code this kernel launches, in isolation.

Run by the kernel (SdkBackend._run_canary_subprocess) with its own interpreter, once per Claude Code version, before the
recipe rides any session's launch on that version. Isolation from every live session:
  * its own romp state root (a temp XDG_STATE_HOME) with session hosts off, so the backend it builds shares no registry,
    no lease, no host and no ledger with the kernel's, and its CLI is a child that dies with this process;
  * a fresh session id (uuid4) in a scratch directory: its transcripts land in a project directory of their own, which
    this script deletes with the state root and the directory when it is done, whatever the outcome;
  * no MCP servers (no peer messaging), no hooks of the kernel's, no kernel at all: only the backend's launch path, the
    same one sessions use, so the probe tests what a session would run.
What it shares with the machine is the Claude login, used the way any session uses it (the machine's own credential
store, or the token the kernel was given, passed in its environment). One fast Opus 5.5 turn whose main loop starts one
subagent: a few cents.

Prints one JSON line, the verdict (sdk_backend.subagent_canary_verdict), and exits 0; any failure to run is an
"inconclusive" verdict, never a pass."""
import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

TURN_TIMEOUT_S = 300


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", required=True)
    ap.add_argument("--kernel-dir", required=True)
    a = ap.parse_args(argv)
    root = tempfile.mkdtemp(prefix="romp-subagent-canary-")
    work = tempfile.mkdtemp(prefix="romp-subagent-canary-work-")
    os.environ["XDG_STATE_HOME"] = root
    os.environ.pop("ROMP_STATE_DIR", None)
    state = os.path.join(root, "romp")
    os.makedirs(state, exist_ok=True)
    Path(state, "session-hosts").write_text("off")   # a kernel child: it dies with this process
    verdict = {"verdict": "inconclusive", "why": "the probe did not run"}
    be = sb = sid = None
    try:
        spec = importlib.util.spec_from_file_location("romp_sdk_backend_canary",
                                                      os.path.join(a.kernel_dir, "sdk_backend.py"))
        sb = importlib.util.module_from_spec(spec)
        sys.modules["romp_sdk_backend_canary"] = sb
        spec.loader.exec_module(sb)
        be = sb.SdkBackend(state, a.claude, lambda *x, **k: None, log=lambda m, **k: None)
        sid = be.spawn("canary", work, sid=str(uuid.uuid4()))
        be._update_reg(sid, model=sb.SUBAGENT_STANDARD_MODEL, fast=True, effort="low")
        if not be.set_subagent_standard(sid, "on"):
            verdict = {"verdict": "inconclusive", "why": "the switch was refused: %s" % be.last_subagent_standard_refusal}
            return 0
        be.send(sid, sb.SUBAGENT_CANARY_PROMPT, user=True)
        turns = Path(state, "turns.jsonl")
        finished, row, t0 = False, {}, time.time()
        while time.time() - t0 < TURN_TIMEOUT_S:
            if turns.exists():
                rows = [json.loads(x) for x in turns.read_text().splitlines() if x.strip()]
                mine = [r for r in rows if r.get("sid") == sid]
                if mine:
                    finished, row = True, mine[-1]
                    break
            if be._pending_ask.get(sid) is not None:
                break   # a question no one will answer: the turn cannot finish
            time.sleep(1)
        time.sleep(1)   # the settle's registry write lands right after the row
        s = be.sessions.get(sid)
        reg = sb.read_reg(Path(state), sid) or {}
        verdict = sb.subagent_canary_verdict(reg.get("subagentSpeed"), launched=bool(getattr(s, "_sub_std_launched", False)),
                                             finished=finished, fast=str(getattr(s, "fast", "") or ""),
                                             fast_reason=str(getattr(s, "fast_reason", "") or ""))
        if isinstance(row.get("usd"), (int, float)):
            verdict["usd"] = row["usd"]
    except Exception as e:
        verdict = {"verdict": "inconclusive", "why": "the probe failed: %s" % e}
    finally:
        try:
            if be is not None and sid is not None:
                be.kill(sid)
                time.sleep(1)
        except Exception:
            pass
        try:   # the probe's transcripts: the project directory its scratch directory maps to, and only that one
            if sb is not None and sid is not None:
                proj = Path(sb.transcript_path(work, sid)).parent
                if proj.name == re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(work)):
                    shutil.rmtree(proj, ignore_errors=True)
        except Exception:
            pass
        shutil.rmtree(work, ignore_errors=True)
        shutil.rmtree(root, ignore_errors=True)
        print(json.dumps(verdict), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
