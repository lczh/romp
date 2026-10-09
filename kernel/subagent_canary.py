#!/usr/bin/env python3
"""Subagents at standard speed's version canary: probe the recipe on the Claude Code this kernel launches, in isolation.

Run by the kernel (SdkBackend._run_canary_subprocess) with its own interpreter, once per Claude Code version, before the
recipe rides any session's launch on that version. Isolation from every live session:
  * its own romp state root (a temp XDG_STATE_HOME) with session hosts off, so the backend it builds shares no registry,
    no lease, no host and no ledger with the kernel's, and its CLI is a child that dies with this process;
  * a fresh session id (uuid4) in a scratch directory: its transcripts land in a project directory of their own, which
    this script deletes with the state root and the directory when it is done, whatever the outcome;
  * no MCP servers of romp's (no peer messaging) and no kernel at all: only the backend's launch path, the same one
    sessions use, so the probe tests what a session would run. Its CLI does load the user's Claude Code settings, the
    login and romp's own postal and wake hooks among them; the kernel runs it with ROMP_SUMMARIZING set, as the judges'
    nested calls are, so those hooks stand down and nothing reaches the live mail service (sdk_backend.subagent_canary_env);
  * each temp directory carries the probe's pid (SUBAGENT_CANARY_PID_FILE) from its first instant, so a probe killed
    before its cleanup is swept at the next probe's start or the kernel's boot, and a running one never is.
What it shares with the machine is the Claude login, used the way any session uses it: the kernel hands it its state
directory (--kernel-state), and the probe's state takes the machine default billing from it, a stored login's record
included (sdk_backend.subagent_canary_billing), so its launch bills what an unpicked session's does (the machine's own
credential store, a stored login's token command run at the launch, or the token the kernel was given, passed in its
environment). The kernel also hands it where it imports the Agent SDK from (--sdk-path), which this script puts on its
own path before anything loads, as kernel._ensure_sdk_on_path did for the kernel. One fast Opus 5.5 turn whose main loop
starts one subagent: a few cents.

Prints one JSON line, the verdict (sdk_backend.subagent_canary_verdict), and exits 0; any failure to run is an
"inconclusive" verdict, never a pass, and a session that never started says so with the backend's own reason."""
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


def _connected(sess) -> bool:
    ev = getattr(sess, "_connected", None)   # set at the CLI's handshake, cleared at its teardown
    return bool(ev is not None and ev.is_set())


def _start_failure(be, sid) -> str:
    """Why the probe's session never started, in the backend's own words: the launch error it recorded for the session
    (a missing SDK, a credential that would not resolve, the CLI's refusal and its stderr line), else its newest problem."""
    rec = be.launch_error(sid) or {}
    if rec.get("text"):
        return str(rec["text"])
    probs = be.problems()
    return str(probs[-1].get("text") or "") if probs else ""


def probe_turn(sb, be, state, sid, timeout=TURN_TIMEOUT_S, tick=1.0) -> dict:
    """The probe's one turn on a backend built over its own state root: the switch on, the prompt sent, the turn awaited
    (or the session's start failure, or a question no one will answer), and the verdict read from the session's speed
    check. Apart from main() so the tests drive it with a stand-in backend."""
    be._update_reg(sid, model=sb.SUBAGENT_STANDARD_MODEL, fast=True, effort="low")
    if not be.set_subagent_standard(sid, "on"):
        return {"verdict": "inconclusive", "why": "the switch was refused: %s" % be.last_subagent_standard_refusal}
    be.send(sid, sb.SUBAGENT_CANARY_PROMPT, user=True)
    turns = Path(state, "turns.jsonl")
    finished, started, row, s, t0 = False, False, {}, None, time.time()
    while time.time() - t0 < timeout:
        s = be.sessions.get(sid) or s
        started = started or _connected(s)
        if turns.exists():
            rows = [json.loads(x) for x in turns.read_text().splitlines() if x.strip()]
            mine = [r for r in rows if r.get("sid") == sid]
            if mine:
                finished, row = True, mine[-1]
                break
        if be._pending_ask.get(sid) is not None:
            break   # a question no one will answer: the turn cannot finish
        if be.launch_error(sid):
            break   # its CLI did not start, and nothing here starts it again
        time.sleep(tick)
    time.sleep(tick)   # the settle's registry write lands right after the row
    s = be.sessions.get(sid) or s
    launched = bool(getattr(s, "_sub_std_launched", False))
    started = started or _connected(s) or launched or finished
    reg = sb.read_reg(Path(state), sid) or {}
    verdict = sb.subagent_canary_verdict(reg.get("subagentSpeed"), launched=launched, finished=finished,
                                         fast=str(getattr(s, "fast", "") or ""),
                                         fast_reason=str(getattr(s, "fast_reason", "") or ""),
                                         started=started, start_failure="" if started else _start_failure(be, sid),
                                         errored=row.get("isError") is True)
    if isinstance(row.get("usd"), (int, float)):
        verdict["usd"] = row["usd"]
    return verdict


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", required=True)
    ap.add_argument("--kernel-dir", required=True)
    ap.add_argument("--kernel-state", default="")   # the kernel's state directory: the machine default billing
    ap.add_argument("--sdk-path", default="")       # the sys.path entry the kernel imports the Agent SDK from
    a = ap.parse_args(argv)
    if a.sdk_path and a.sdk_path not in sys.path:
        sys.path.insert(0, a.sdk_path)   # this process only: PYTHONPATH would ride into the CLI and its hooks
    root = tempfile.mkdtemp(prefix="romp-subagent-canary-")
    Path(root, "canary.pid").write_text(str(os.getpid()))          # SUBAGENT_CANARY_PID_FILE: kept from the sweep while alive
    work = tempfile.mkdtemp(prefix="romp-subagent-canary-work-")
    Path(work, "canary.pid").write_text(str(os.getpid()))
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
        if a.kernel_state:
            sb.subagent_canary_billing(a.kernel_state, state)
        be = sb.SdkBackend(state, a.claude, lambda *x, **k: None, log=lambda m, **k: None)
        sid = be.spawn("canary", work, sid=str(uuid.uuid4()))
        verdict = probe_turn(sb, be, state, sid)
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
