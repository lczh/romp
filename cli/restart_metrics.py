#!/usr/bin/env python3
"""romp-restart-metrics: what kernel restarts do to the sessions, measured. `romp restart-metrics`.

Stage 0 of the restart-surviving sessions program (part of #1317): before any restart behaviour changes,
the monitors exist, a baseline is recorded, and the same reader keeps running after the change. This is
the READER: it reads the state directory's ledgers and the running kernel's routes, never a kernel module
(loading one against live state runs the boot reconcile as a second actor), and it changes nothing.

Sources, and what each stamp is (every stamp is an EVENT's time, never the clock at read time):
  restart-cuts.jsonl     one row per kernel exit (cutTurns: the sessions whose in-flight turn the drain
                         interrupted; stopped, unjoined, reaped; reason), written at the exit's start since
                         2026-10-05 with `expectsDone`, and then a done row (exitDone, cutT) after the drain that
                         fold_exit_done joins to it; and one bootSettled row per boot (firstServe, reconcileDone,
                         settleS, outageS = firstServe minus the previous cut's t)
  restart-audit.jsonl    the requests and notes around a restart; the manager's `quiet-window` row is the
                         parked deploy's wait from parked to restart (waitedS) and whether the fifteen-minute
                         backstop fired (backstop)
  session-events.jsonl   what went wrong with a session's process (kernel/sdk_backend.py problem_row): an
                         orphaned CLI ended at boot, a leftover scope stopped, two CLIs serving one
                         session (keyed by fsid in rows before the sid key, by sid since; either counts)
                         or a boot that could not read every CLI's session, a crash heal or loop, a session the drain left closing, the lease
                         work's `lease.*` kinds; plus reconcile.boot, the boot sweep's summary (resumed =
                         continuation notices queued)
  turns.jsonl            one row per settled turn: fedT (the feed pop), firstOutT (the first streamed work
                         atom), resultT (the ResultMessage), the CLI's own durationMs / apiMs, the spend
                         fold's usd and tokens, resumeNotice (a turn redoing cut work)
  states/<sid>.jsonl     the state log: working (written at the feed pop) to waiting (the ResultMessage),
                         one-second resolution, the latency the baseline has for turns before turns.jsonl
                         existed; machineCut rows count romp's own cuts by cause
  spend.json             hour and day buckets per session (no per-turn rows: redo cost cannot come from it,
                         and this says so); the day's total dollars, for the redo ratio
  live (skipped with --no-live): each romp-session-* scope's memory.current and cpu.stat on Linux (ps on
                         macOS), the kernel's GET /version and GET /perf (its own CPU and the pusher's
                         idle-cycle counter), a `ps` scan for two CLIs holding one SESSION now (the session
                         is the ROMP_SID the kernel set on the process, never the conversation id on its
                         argv, which a comment thread shares with its parent), and the comment threads
                         running per parent session (the registry's threadOf)

Output: --json, the whole document (schema 1), or the one-screen text summary per window. Windows are
days or weeks (--window), the week anchored on --anchor (default: the day of the first restart in range),
in the machine's local time unless --tz names a zone. Missing sources are said, never silently zero.
"""
import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = 1
SCOPE_RE = re.compile(r"romp-session-([0-9a-fA-F]{1,8})-(\d+)-\d+\.scope\Z")
SDK_CLI_MARK = "--input-format stream-json"
SID_ENV = "ROMP_SID"      # the session id the kernel sets on every session CLI it launches (sdk_backend _options)
PROBLEM_KINDS = ("reconcile.orphan-reaped", "reconcile.scope-stopped", "reconcile.duplicate-cli", "reconcile.duplicate-unchecked",
                 "crash.heal", "crash.loop", "drain.unjoined")
QUIET_JOIN_S = 180        # a cut row within this many seconds after a quiet-window row is that window's restart


# ── where things are ────────────────────────────────────────────────────────────────────────────────────
def state_dir() -> Path:
    return Path(os.environ.get("ROMP_STATE_DIR")
                or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")


DEFAULT_LABEL = "this machine"   # the document's name for itself; never the hostname (a personal identifier
#                                  the user's rules keep out of anything they read) unless the caller passes one


def _read_jsonl(path: Path) -> tuple[list[dict], dict]:
    """(rows, source note): every parseable object row, in file order, the rotated predecessor
    (<name>.1, kernel/sdk_backend.py LEDGER_ROTATE_BYTES) first when one exists; the note says what was
    read."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], {"path": path.name, "present": False, "rows": 0}
    except OSError as e:
        return [], {"path": path.name, "present": True, "rows": 0, "error": str(e)}
    prev = path.with_name(path.name + ".1")
    if prev.exists():
        try:
            text = prev.read_text(encoding="utf-8") + "\n" + text
        except OSError:
            pass
    rows, bad = [], 0
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except Exception:
            bad += 1
            continue
        if isinstance(r, dict):
            rows.append(r)
    note = {"path": path.name, "present": True, "rows": len(rows)}
    if bad:
        note["unparseable"] = bad
    return rows, note


# ── stats and windows ───────────────────────────────────────────────────────────────────────────────────
def stats(values) -> dict:
    """n, mean, p50, p90, max over the numbers given (nearest-rank percentiles); {} when empty."""
    xs = sorted(float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not xs:
        return {"n": 0}
    def pct(p):
        k = max(0, min(len(xs) - 1, int(round(p / 100.0 * (len(xs) - 1)))))
        return xs[k]
    return {"n": len(xs), "mean": round(sum(xs) / len(xs), 3), "p50": round(pct(50), 3),
            "p90": round(pct(90), 3), "max": round(xs[-1], 3)}


def _zone(tz: str | None):
    if not tz:
        return None
    from zoneinfo import ZoneInfo
    return ZoneInfo(tz)


def local_date(t, tz=None) -> str:
    return datetime.fromtimestamp(float(t), tz=_zone(tz)).strftime("%Y-%m-%d")


def day_start(date_str: str, tz=None) -> float:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    d = d.replace(tzinfo=_zone(tz)) if tz else d
    return d.timestamp()


def _date(s: str):
    from datetime import date
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def bucket_key(t, kind: str, anchor: str, tz=None) -> str:
    """The window a stamp falls in: the day's date, or for weeks the anchor date plus a whole number of
    weeks ("week of YYYY-MM-DD"); days before the anchor bucket backwards the same way. Weeks are counted
    in LOCAL DATES, not multiples of 604800 seconds, so a daylight-saving change inside a week moves no
    boundary off local midnight (review find, 2026-09-10)."""
    if kind == "day":
        return local_date(t, tz)
    days = (_date(local_date(t, tz)) - _date(anchor)).days
    n = days // 7
    return "week of " + (_date(anchor) + timedelta(days=7 * n)).isoformat()


def bucket_bounds(key: str, kind: str, tz=None) -> tuple[float, float]:
    """[start, end) of a bucket in epoch seconds: local midnight of its first day to local midnight of the
    day after its last (a week's end is the SEVENTH local midnight, whatever the clocks did in between)."""
    date = key.replace("week of ", "")
    s = day_start(date, tz)
    e = day_start((_date(date) + timedelta(days=7 if kind == "week" else 1)).isoformat(), tz)
    return s, e


# ── the ledgers, parsed ─────────────────────────────────────────────────────────────────────────────────
DONE_FIELDS = ("ckptWritten", "ckptLeft", "primed", "primeSkipped", "asmSkipped", "deadlineS", "elapsedS")


def fold_exit_done(rows: list) -> list:
    """restart-cuts.jsonl with each exit's DONE row folded into its cut row (the kernel's exit path, 2026-10-05). The
    kernel writes the cut row at the exit's START (what it knew before the drain, `expectsDone`) and a done row after
    the drain (`exitDone`, `cutT` = the cut row's t, the drain's stopped / unjoined / reaped, `drainCutTurns` when the
    turns cut differ from the row's prediction, drainError / reasonError, `finalReason` when the late verdict differs,
    `phases` and the checkpoint counts). Two append-only rows, because the ledger is shared by every kernel under the
    root and a rewrite could drop a sibling's append. A done row joins the newest cut row before it with the same pid and
    t == cutT; its fields override the cut row's, the phases' ckptS and drainS land where older cut rows carried them,
    and the record gets exitDone True. A cut row with expectsDone and no done row is exitDone False (the exit was killed
    after its row: the forced kill, visible where the old order lost the row), and an older row is exitDone None. Done
    rows never stand as rows of their own (one whose cut row is missing is dropped); every other row passes unchanged.
    Pure; the input is not modified."""
    out, open_cuts = [], {}
    for r in rows:
        if not isinstance(r, dict):
            out.append(r)
            continue
        if r.get("exitDone") is True and "cutTurns" not in r:
            waiting = open_cuts.get((r.get("pid"), r.get("cutT"))) or []
            if not waiting:
                continue
            c = out[waiting.pop()]
            c["exitDone"] = True
            for k in ("stopped", "unjoined", "reaped"):
                if k in r:
                    c[k] = r[k]
            if isinstance(r.get("drainCutTurns"), list):
                c["cutTurns"] = list(r["drainCutTurns"])
            for k in ("drainError", "reasonError"):
                if r.get(k):
                    c[k] = r[k]
            if "finalReason" in r:
                c["reason"] = r["finalReason"]
            ph = r.get("phases") if isinstance(r.get("phases"), dict) else {}
            for k in ("ckptS", "drainS"):
                if k in ph:
                    c[k] = ph[k]
            if ph:
                c["phases"] = dict(ph)
            for k in DONE_FIELDS:
                if k in r:
                    c[k] = r[k]
            c["doneT"] = r.get("t")
            continue
        if "cutTurns" in r:
            c = dict(r)
            c["exitDone"] = False if r.get("expectsDone") else None
            if r.get("expectsDone"):
                open_cuts.setdefault((r.get("pid"), r.get("t")), []).append(len(out))
            out.append(c)
            continue
        out.append(r)
    return out


def parse_restarts(rows: list[dict]) -> list[dict]:
    """One record per kernel exit from restart-cuts.jsonl (rows carrying cutTurns, each with its done row folded in
    first: fold_exit_done), each joined with the
    boot that followed it: the bootSettled row whose prevCutT names it, else the first boot row after it.
    A boot the ledger shows with no preceding cut (a crash, a SIGKILL: no drain ran) becomes a record of its
    own with `noCutRow` so the outage is still counted where the boot row measured it. `exitDone` is True for an exit
    that wrote its done row, False for one killed after its cut row, None for a row from before the done row existed."""
    rows = fold_exit_done(rows)
    cuts = sorted([r for r in rows if isinstance(r.get("t"), (int, float)) and "cutTurns" in r], key=lambda r: r["t"])
    boots = [r for r in rows if isinstance(r.get("t"), (int, float)) and r.get("bootSettled")]
    used = set()
    out = []
    for n, c in enumerate(cuts):
        next_t = cuts[n + 1]["t"] if n + 1 < len(cuts) else float("inf")
        boot = None
        for i, b in enumerate(boots):
            if i not in used and b.get("prevCutT") == c["t"]:
                boot = b
                used.add(i)
                break
        if boot is None:
            # a boot row with no prevCutT (the kernel writes one only when a cut precedes it; older rows
            # have none) joins the cut only when it sits BEFORE the next cut: a cut with no boot row of
            # its own (the ledger predates the boot rows, or the boot never settled) must not take a
            # later restart's boot, which cascaded through the whole ledger (found on the live file)
            for i, b in enumerate(boots):
                if i not in used and b.get("prevCutT") is None and c["t"] <= b["t"] < next_t:
                    boot = b
                    used.add(i)
                    break
        names = [x.get("name") or str(x.get("sid") or "")[:8] for x in (c.get("cutTurns") or []) if isinstance(x, dict)]
        rec = {"t": int(c["t"]), "pid": c.get("pid"), "reason": str(c.get("reason") or ""),
               "cutTurns": len(c.get("cutTurns") or []), "cutSessions": names,
               "stopped": int(c.get("stopped") or 0), "unjoined": int(c.get("unjoined") or 0),
               "reaped": int(c.get("reaped") or 0), "watchesArmed": int(c.get("watchesArmed") or 0),
               "auditT": c.get("auditT"), "drainError": c.get("drainError"), "reasonError": c.get("reasonError"),
               "rssKb": c.get("rssKb"), "cpuS": c.get("cpuS"),   # the kernel's own size and CPU at its exit
               "exitDone": c.get("exitDone")}                    # True: the exit completed; False: killed after its cut row
        if boot is not None:
            rec["boot"] = {"t": int(boot["t"]), "firstServe": boot.get("firstServe"),
                           "reconcileDone": boot.get("reconcileDone"), "settleS": boot.get("settleS"),
                           "rssKb": boot.get("rssKb"), "cpuS": boot.get("cpuS"),
                           "outageS": boot.get("outageS") if boot.get("outageS") is not None
                           else (round(float(boot["firstServe"]) - c["t"], 2) if isinstance(boot.get("firstServe"), (int, float)) else None)}
        out.append(rec)
    for i, b in enumerate(boots):
        if i in used:
            continue
        out.append({"t": int(b["t"]), "pid": b.get("pid"), "reason": "", "cutTurns": 0, "cutSessions": [],
                    "stopped": 0, "unjoined": 0, "reaped": 0, "watchesArmed": 0, "noCutRow": True,
                    "boot": {"t": int(b["t"]), "firstServe": b.get("firstServe"), "reconcileDone": b.get("reconcileDone"),
                             "settleS": b.get("settleS"), "outageS": b.get("outageS"),
                             "rssKb": b.get("rssKb"), "cpuS": b.get("cpuS")}})
    out.sort(key=lambda r: r["t"])
    return out


def kernel_series(restarts: list[dict]) -> list[dict]:
    """The kernel process's own resident size and CPU, sampled at the two events the restart ledger records
    (T304): each exit (the cut row, kind "exit": the process at the end of its life) and each settled boot
    (kind "boot": the process just born). Rows older than the sample carry none and are skipped. Time order."""
    out = []
    for r in restarts:
        if isinstance(r.get("rssKb"), (int, float)) and not r.get("noCutRow"):
            out.append({"t": r["t"], "kind": "exit", "pid": r.get("pid"), "rssMb": round(r["rssKb"] / 1024.0, 1),
                        "cpuS": r.get("cpuS")})
        b = r.get("boot") or {}
        if isinstance(b.get("rssKb"), (int, float)):
            out.append({"t": b["t"], "kind": "boot", "pid": None, "rssMb": round(b["rssKb"] / 1024.0, 1), "cpuS": b.get("cpuS")})
    out.sort(key=lambda x: (x["t"], x["kind"] == "boot"))
    return out


def parse_quiet_windows(audit_rows: list[dict], restarts: list[dict]) -> list[dict]:
    """The manager's quiet-window rows, each joined to the first restart within QUIET_JOIN_S after it. `lastCodex`
    rides beside `lastInflight` (2026-09-23, the review of this lane): the manager's row counts the park's Codex turns
    apart from its Claude ones, and a row from an older manager has none (None, never a guessed 0)."""
    out = []
    for r in audit_rows:
        if r.get("action") != "quiet-window" or not isinstance(r.get("t"), (int, float)):
            continue
        q = {"t": int(r["t"]), "since": r.get("since"), "waitedS": r.get("waitedS"), "reason": str(r.get("reason") or ""),
             "backstop": bool(r.get("backstop")), "coalesced": r.get("coalesced"), "mode": r.get("mode"),
             "lastInflight": r.get("lastInflight"), "lastCodex": r.get("lastCodex"),
             "drainRefusedCount": r.get("drainRefusedCount"), "drainArmedCount": r.get("drainArmedCount")}
        hit = next((x for x in restarts if q["t"] <= x["t"] <= q["t"] + QUIET_JOIN_S), None)
        q["restartT"] = hit["t"] if hit else None
        q["cutTurns"] = hit["cutTurns"] if hit else None
        out.append(q)
    return out


def audit_counts(audit_rows: list[dict]) -> dict:
    """How many rows of each action the audit ledger holds, plus manager-sigterm triggers."""
    by, trig = {}, {}
    for r in audit_rows:
        a = str(r.get("action") or "(none)")
        by[a] = by.get(a, 0) + 1
        if a == "manager-sigterm":
            t = str(r.get("trigger") or r.get("reason") or "?")
            trig[t] = trig.get(t, 0) + 1
    return {"byAction": by, "sigtermTriggers": trig}


def parse_events(rows: list[dict]) -> list[dict]:
    return [r for r in rows if isinstance(r.get("t"), (int, float)) and isinstance(r.get("kind"), str)]


def parse_turns(rows: list[dict]) -> list[dict]:
    """turns.jsonl rows with the derived seconds: feedToResultS, feedToFirstOutS (when both stamps exist and
    the row says the turn was fed: a kernel before the 2026-09-10 writer fix stamped a turn the CLI opened
    itself, fedTexts 0, with the previous fed turn's fedT and firstOutT, which read as hours of feed-to-result
    and a duplicated first output; such a row is a turn and measures no feed latency). The gate is the count 0
    alone: a row without a fedTexts key is read by its stamps, and a bool is not a count."""
    out = []
    for r in rows:
        if not isinstance(r.get("t"), (int, float)):
            continue
        rec = dict(r)
        n = r.get("fedTexts")
        if isinstance(n, int) and not isinstance(n, bool) and n == 0:
            out.append(rec)          # nothing fed: the stamps, if any, are another turn's
            continue
        fed, res, fo = r.get("fedT"), r.get("resultT"), r.get("firstOutT")
        if isinstance(fed, (int, float)) and fed > 0 and isinstance(res, (int, float)) and res >= fed:
            rec["feedToResultS"] = round(float(res) - float(fed), 3)
            if isinstance(fo, (int, float)) and fo >= fed:
                rec["feedToFirstOutS"] = round(float(fo) - float(fed), 3)
        out.append(rec)
    return out


def state_log_turns(lines: list[str]) -> tuple[list[dict], dict]:
    """(turns, machine cuts) from one states/<sid>.jsonl: each `working` row followed by a `waiting` row
    (not romp's own `by`-marked settle, an interrupt) is a turn with feedToResultS at one-second
    resolution; the feed pop and the ResultMessage are what those two writes ARE. machineCut rows count
    romp's cuts by cause. Pure."""
    turns, cuts = [], {}
    open_t = None
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if not isinstance(r, dict):
            continue
        if "machineCut" in r:
            c = str(r["machineCut"])
            cuts[c] = cuts.get(c, 0) + 1
            open_t = None      # the cut turn never gets its result: the resumed turn's `waiting` is not its end
            #                    (review find, 2026-09-10: the pair spanned the outage and the redo)
            continue
        st = r.get("state")
        t = r.get("t")
        if not isinstance(t, (int, float)) or not st:
            continue
        if st == "working":
            if open_t is None:
                open_t = float(t)
        elif st == "waiting":
            if open_t is not None and not r.get("by"):
                turns.append({"t": int(t), "fedT": int(open_t), "feedToResultS": round(float(t) - open_t, 3)})
            open_t = None
        elif st in ("idle",):
            open_t = None
    return turns, cuts


def spend_by_day(spend: dict) -> dict:
    """{date: usd} from spend.json's day buckets."""
    out = {}
    days = spend.get("days") if isinstance(spend, dict) else None
    for k, v in (days or {}).items():
        if isinstance(v, dict) and isinstance(v.get("usd"), (int, float)):
            out[str(k)] = float(v["usd"])
    return out


# ── the buckets ─────────────────────────────────────────────────────────────────────────────────────────
def build_buckets(restarts, quiet, events, turns, statelog_turns, machine_cuts, spend_days, kind, anchor,
                  tz=None, since=None, until=None) -> list[dict]:
    """One dict per window with every metric the figures read. Rows outside [since, until) are dropped."""
    def inside(t):
        return (since is None or t >= since) and (until is None or t < until)
    B = {}
    def get(t):
        k = bucket_key(t, kind, anchor, tz)
        if k not in B:
            s, e = bucket_bounds(k, kind, tz)
            B[k] = {"key": k, "start": int(s), "end": int(e), "restarts": 0, "cutTurns": 0, "cleanRestarts": 0,
                    "restartsWithoutCutRow": 0, "cutSessions": {}, "reasons": {}, "_outage": [], "_settle": [],
                    "drainUnjoinedCount": 0, "drainReapedCount": 0,
                    "quietWindows": 0, "backstopFires": 0, "_quietWait": [],
                    "events": {}, "orphansReaped": 0, "scopesStopped": 0, "duplicateClis": 0, "duplicateUnchecked": 0, "crashHeals": 0,
                    "crashLoops": 0, "drainLeftClosing": 0, "leaseProblems": 0, "boots": 0, "resumedTurns": 0,
                    "attachedAtBoot": 0, "attachedLater": 0,
                    "redo": {"turns": 0, "usd": 0.0, "tokens": 0}, "_l_res": [], "_l_first": [], "_l_api": [],
                    "_l_dur": [], "turns": 0, "_sl": [], "stateLogTurns": 0, "machineCuts": {}, "spendUsd": None,
                    "_k_rss": [], "_k_cpu": []}
        return B[k]
    for r in restarts:
        if not inside(r["t"]):
            continue
        b = get(r["t"])
        b["restarts"] += 1
        b["cutTurns"] += r["cutTurns"]
        if r.get("noCutRow"):
            b["restartsWithoutCutRow"] += 1
        elif r["cutTurns"] == 0:
            b["cleanRestarts"] += 1
        for n in r["cutSessions"]:
            b["cutSessions"][n] = b["cutSessions"].get(n, 0) + 1
        why = (r["reason"].split(":", 1)[0] or "(none)") if r["reason"] else ("(no cut row)" if r.get("noCutRow") else "(none)")
        b["reasons"][why] = b["reasons"].get(why, 0) + 1
        b["drainUnjoinedCount"] += r["unjoined"]
        b["drainReapedCount"] += r["reaped"]
        bt = r.get("boot") or {}
        if isinstance(bt.get("outageS"), (int, float)):
            b["_outage"].append(bt["outageS"])
        if isinstance(bt.get("settleS"), (int, float)):
            b["_settle"].append(bt["settleS"])
        if isinstance(r.get("rssKb"), (int, float)) and not r.get("noCutRow"):
            b["_k_rss"].append(r["rssKb"] / 1024.0)
        if isinstance(r.get("cpuS"), (int, float)) and not r.get("noCutRow"):
            b["_k_cpu"].append(r["cpuS"])
    for q in quiet:
        if not inside(q["t"]):
            continue
        b = get(q["t"])
        b["quietWindows"] += 1
        b["backstopFires"] += 1 if q["backstop"] else 0
        if isinstance(q.get("waitedS"), (int, float)):
            b["_quietWait"].append(q["waitedS"])
    for e in events:
        if not inside(e["t"]):
            continue
        b = get(e["t"])
        k = e["kind"]
        b["events"][k] = b["events"].get(k, 0) + 1
        if k == "reconcile.boot":
            b["boots"] += 1
            b["resumedTurns"] += int(e.get("resumed") or 0)
        elif k == "reconcile.orphan-reaped":
            b["orphansReaped"] += 1
        elif k == "reconcile.scope-stopped":
            b["scopesStopped"] += 1
        elif k == "reconcile.duplicate-cli" or k == "lease.duplicate-cli":
            b["duplicateClis"] += 1
        elif k == "reconcile.duplicate-unchecked":        # a boot that could not read every CLI's session (ROMP_SID),
            b["duplicateUnchecked"] += 1                  #  so its duplicate count is a floor
        elif k == "crash.heal":
            b["crashHeals"] += 1
        elif k == "crash.loop":
            b["crashLoops"] += 1
        elif k == "drain.unjoined":
            b["drainLeftClosing"] += 1
        elif k == "host.attached":                        # a session host re-attached (T315): at the kernel's boot (the
            if e.get("boot") is True:                    #  sessions a restart kept running under their hosts), or later
                b["attachedAtBoot"] += 1                 #  (a send after the boot found a host to attach to)
            else:
                b["attachedLater"] += 1
        if k.startswith("lease."):
            b["leaseProblems"] += 1
    for tr in turns:
        if not inside(tr["t"]):
            continue
        b = get(tr["t"])
        b["turns"] += 1
        if "feedToResultS" in tr:
            b["_l_res"].append(tr["feedToResultS"])
        if "feedToFirstOutS" in tr:
            b["_l_first"].append(tr["feedToFirstOutS"])
        if isinstance(tr.get("apiMs"), (int, float)):
            b["_l_api"].append(tr["apiMs"] / 1000.0)
        if isinstance(tr.get("durationMs"), (int, float)):
            b["_l_dur"].append(tr["durationMs"] / 1000.0)
        if tr.get("resumeNotice"):
            b["redo"]["turns"] += 1
            b["redo"]["usd"] += float(tr.get("usd") or 0)
            b["redo"]["tokens"] += sum(int(tr.get(k) or 0) for k in ("tokIn", "tokOut", "tokCacheR", "tokCacheW"))
    for tr in statelog_turns:
        if not inside(tr["t"]):
            continue
        b = get(tr["t"])
        b["stateLogTurns"] += 1
        b["_sl"].append(tr["feedToResultS"])
    for (t, cause) in machine_cuts:
        if not inside(t):
            continue
        b = get(t)
        b["machineCuts"][cause] = b["machineCuts"].get(cause, 0) + 1
    for date, usd in spend_days.items():
        try:
            t = day_start(date, tz) + 3600
        except ValueError:
            continue
        if not inside(t):
            continue
        b = get(t)
        b["spendUsd"] = round((b["spendUsd"] or 0.0) + usd, 4)
    out = []
    for k in sorted(B):
        b = B[k]
        b["samples"] = {"feedToResultS": _cap(b["_l_res"]), "feedToFirstOutS": _cap(b["_l_first"]),
                        "stateLogS": _cap(b["_sl"])}
        b["outageS"] = stats(b.pop("_outage"))
        b["settleS"] = stats(b.pop("_settle"))
        b["quietWaitS"] = stats(b.pop("_quietWait"))
        b["latency"] = {"feedToResultS": stats(b.pop("_l_res")), "feedToFirstOutS": stats(b.pop("_l_first")),
                        "apiS": stats(b.pop("_l_api")), "durationS": stats(b.pop("_l_dur"))}
        b["stateLogLatencyS"] = stats(b.pop("_sl"))
        b["kernelAtExit"] = {"rssMb": stats(b.pop("_k_rss")), "cpuS": stats(b.pop("_k_cpu"))}
        b["redo"]["usd"] = round(b["redo"]["usd"], 4)
        b["measuredRestarts"] = b["restarts"] - b["restartsWithoutCutRow"]   # a boot with no cut row cut an UNKNOWN
        #                                                                        number of turns, not zero (review find)
        b["cutTurnsPerRestart"] = round(b["cutTurns"] / b["measuredRestarts"], 2) if b["measuredRestarts"] else None
        out.append(b)
    return out


# ── live reads (never a kernel module; files and HTTP only) ─────────────────────────────────────────────
def _regs(state: Path, errors: list | None = None) -> dict:
    """sid -> {name, lastSid, alive, threadOf} from sdk/*.json (read, never written). threadOf is the parent
    session's sid on a comment thread's row (the kernel's own mark: backend.fork(thread_of=...), cleared when
    the thread is promoted to a full session); a deliberate fork has none, since it is a session of its own.
    A row that does not parse, or no rows at all, is said in `errors`: the sessions it would name cannot be
    identified, and nothing below guesses them."""
    out, bad = {}, 0
    for p in glob.glob(str(state / "sdk" / "*.json")):
        try:
            d = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            bad += 1
            continue
        if isinstance(d, dict):
            sid = str(d.get("sid") or Path(p).stem)
            out[sid] = {"name": str(d.get("name") or sid[:8]), "lastSid": str(d.get("lastSid") or ""),
                        "alive": bool(d.get("alive")), "threadOf": str(d.get("threadOf") or "")}
        else:
            bad += 1
    if errors is not None:
        if bad:
            errors.append("%d registry file(s) under sdk/ did not parse: their sessions are not identified" % bad)
        if not out:
            errors.append("no registry rows under sdk/: no process can be tied to a session, so neither "
                          "duplicates nor comment threads are counted")
    return out


def _read_int(path: str):
    try:
        return int(open(path).read().split()[0])
    except Exception:
        return None


def cgroup_scope_dirs(root="/sys/fs/cgroup") -> list[str]:
    hits = glob.glob(os.path.join(root, "user.slice", "user-*.slice", "user@*.service", "*", "romp-session-*.scope"))
    if not hits:
        hits = glob.glob(os.path.join(root, "**", "romp-session-*.scope"), recursive=True)
    return sorted(hits)


def scope_stats(scope_dir: str) -> dict:
    """memory.current (bytes), memory.peak when the kernel offers it, cpu.stat usage_usec, and the pid count
    of one romp-session-* scope's cgroup. Read-only."""
    unit = os.path.basename(scope_dir)
    m = SCOPE_RE.match(unit)
    out = {"scope": unit, "sid8": m.group(1).lower() if m else "", "cliPid": int(m.group(2)) if m else None}
    out["memBytes"] = _read_int(os.path.join(scope_dir, "memory.current"))
    out["memPeakBytes"] = _read_int(os.path.join(scope_dir, "memory.peak"))
    cpu = None
    try:
        for ln in open(os.path.join(scope_dir, "cpu.stat")):
            if ln.startswith("usage_usec"):
                cpu = int(ln.split()[1]) / 1e6
                break
    except Exception:
        pass
    out["cpuS"] = round(cpu, 3) if cpu is not None else None
    try:
        out["procs"] = len([x for x in open(os.path.join(scope_dir, "cgroup.procs")).read().split() if x])
    except Exception:
        out["procs"] = None
    return out


def ps_lines() -> list[str]:
    try:
        return subprocess.run(["ps", "-axwwo", "pid=,ppid=,rss=,pcpu=,command="], capture_output=True,
                              text=True, timeout=10).stdout.splitlines()
    except Exception:
        return []


def parse_ps(lines: list[str]) -> dict[int, dict]:
    procs = {}
    for ln in lines:
        parts = ln.strip().split(None, 4)
        if len(parts) < 5 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            rss = int(float(parts[2]))
            pcpu = float(parts[3])
        except ValueError:
            continue
        procs[int(parts[0])] = {"ppid": int(parts[1]), "rssKb": rss, "pcpu": pcpu, "cmd": parts[4]}
    return procs


ARGV_ID_RE = re.compile(r"--(?:resume|session-id)[= ](\S+)")


def argv_ids(cmd: str) -> set[str]:
    """The conversation ids a CLI's argv names after --resume / --session-id, either spelling (the kernel's
    cli_sid_of match, read as a set so a registry of thousands of rows costs one lookup per id)."""
    return set(ARGV_ID_RE.findall(cmd))


def proc_session_id(pid: int, run=None, procfs=None) -> str | None:
    """The session id the kernel set on a process at launch: ROMP_SID in its environment as exec'd. _options
    sets it on every session CLI to that session's own sid, so a comment thread's CLI carries the thread's
    sid while its argv still names the PARENT's conversation (--resume <parent> --fork-session: the fork
    loads the parent's transcript and writes its own). /proc/<pid>/environ where there is a procfs (readable
    for this user's own processes), else `ps -E` (macOS prints the environment after the command), the read
    the kernel's proc_state_tag makes. None when the process is gone or its environment is unreadable or
    carries no ROMP_SID. `run` and `procfs` are the test seams."""
    procfs = os.path.isdir("/proc") if procfs is None else procfs
    key = SID_ENV + "="
    if procfs:
        try:
            with open("/proc/%d/environ" % int(pid), "rb") as f:
                data = f.read()
        except (OSError, ValueError):
            return None
        for kv in data.split(b"\0"):
            if kv.startswith(key.encode()):
                return kv[len(key):].decode("ascii", "replace") or None
        return None
    run = run or subprocess.run
    try:
        out = run(["ps", "-E", "-ww", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True,
                  timeout=5).stdout
    except Exception:
        return None
    vals = [tok[len(key):] for tok in (out or "").split() if tok.startswith(key)]
    return (vals[-1] or None) if vals else None    # the environment follows the argv


def cli_sessions(procs: dict[int, dict], regs: dict, sid_of=None) -> tuple[dict[str, list[int]], list[int]]:
    """({sid: [pids]}, [unidentified pids]) over the SDK-driven CLIs on this state directory's conversations
    (the stream-json mark plus a --resume / --session-id naming one of the registry's conversation ids or
    sids, the kernel's boot-reap match). Each is filed under the session its ROMP_SID names (proc_session_id,
    `sid_of` the seam), never under the conversation id on its argv: a parent and every comment thread forked
    from it all carry the parent's id there, and they are different sessions. A CLI of ours whose ROMP_SID
    cannot be read, or names no registry row, is UNIDENTIFIED and listed as such, never guessed from its
    argv. Pids ascending."""
    sid_of = sid_of or proc_session_id
    ours = {r["lastSid"] for r in regs.values() if r.get("lastSid")} | set(regs)
    by_sid: dict[str, list[int]] = {}
    unknown: list[int] = []
    for pid in sorted(procs):
        cmd = procs[pid]["cmd"]
        if SDK_CLI_MARK not in cmd or not (argv_ids(cmd) & ours):
            continue
        sid = sid_of(pid)
        if sid and sid in regs:
            by_sid.setdefault(sid, []).append(pid)
        else:
            unknown.append(pid)
    return by_sid, unknown


def duplicate_clis(procs: dict[int, dict], regs: dict, sid_of=None) -> dict[str, list[int]]:
    """Sessions more than one SDK-driven CLI serves right now, sid -> pids (cli_sessions' grouping: by the
    session, so a parent and its comment threads are never each other's duplicates)."""
    by_sid, _ = cli_sessions(procs, regs, sid_of)
    return {s: p for s, p in by_sid.items() if len(p) > 1}


def ps_session_trees(procs: dict[int, dict], regs: dict, sid_of=None) -> list[dict]:
    """Per-session memory and CPU from a ps listing (macOS, or Linux without cgroup access): each SDK CLI
    cli_sessions ties to a session, with its descendants' rss summed. An unidentified CLI has no row here;
    live_snapshot lists it by pid."""
    kids = {}
    for pid, p in procs.items():
        kids.setdefault(p["ppid"], []).append(pid)
    by_sid, _ = cli_sessions(procs, regs, sid_of)
    out = []
    for sid, pids in by_sid.items():
        for pid in pids:
            stack, tree = [pid], []
            while stack:
                x = stack.pop()
                tree.append(x)
                stack.extend(kids.get(x, []))
            out.append({"sid": sid, "name": regs[sid]["name"], "cliPid": pid, "procs": len(tree),
                        "memBytes": sum(procs[x]["rssKb"] for x in tree) * 1024,
                        "cpuPct": round(sum(procs[x]["pcpu"] for x in tree), 2)})
    return out


def thread_groups(sessions: list[dict], regs: dict) -> list[dict]:
    """The comment threads among the live session rows, per parent session: a row whose registry row carries
    threadOf (the kernel's mark for a comment thread) is that parent's thread. Each thread is its own session
    with its own process, counted and sized here so its memory is visible without reading as a leak. Largest
    first. A row with no registry row is not a thread by this reading: nothing says it is."""
    groups: dict[str, dict] = {}
    for s in sessions:
        r = regs.get(s.get("sid") or "")
        parent = (r or {}).get("threadOf")
        if not parent:
            continue
        g = groups.setdefault(parent, {"parentSid": parent,
                                       "parentName": (regs.get(parent) or {}).get("name") or parent[:8],
                                       "threads": 0, "sids": [], "memBytes": None})
        if s["sid"] not in g["sids"]:
            g["sids"].append(s["sid"])
            g["threads"] += 1
        if isinstance(s.get("memBytes"), int):
            g["memBytes"] = (g["memBytes"] or 0) + s["memBytes"]
    return sorted(groups.values(), key=lambda g: (-(g["memBytes"] or 0), g["parentName"]))


def _kernel_port(state: Path) -> int:
    env = os.environ.get("ROMP_KERNEL_PORT")
    if env and env.isdigit():
        return int(env)
    try:
        return int((state / "serve-port").read_text().strip())
    except Exception:
        return 29855


def _kernel_token(state: Path) -> str:
    return os.environ.get("ROMP_SERVE_TOKEN") or (state / "serve-token").read_text().strip() if (state / "serve-token").exists() else (os.environ.get("ROMP_SERVE_TOKEN") or "")


def kernel_live(state: Path) -> dict:
    """GET /version (auth-exempt) and GET /perf (token): the kernel's pid, uptime, CPU seconds, resident size
    and the pusher's idle-cycle share. Unreachable or refused is SAID in `error`, never a silent zero."""
    port = _kernel_port(state)
    base = "http://127.0.0.1:%d" % port
    out = {"port": port}
    try:
        with urllib.request.urlopen(base + "/version", timeout=2) as r:
            v = json.loads(r.read().decode())
        out.update({"pid": v.get("pid"), "started": v.get("started"), "uptimeS": v.get("uptime_s"),
                    "kernelSha": v.get("kernel_sha"), "bootId": v.get("boot")})
    except Exception as e:
        out["error"] = "kernel not reachable on :%d (%s)" % (port, e.__class__.__name__)
        return out
    try:
        tok = _kernel_token(state)
        req = urllib.request.Request(base + "/perf", headers={"X-Romp-Token": tok} if tok else {})
        with urllib.request.urlopen(req, timeout=3) as r:
            pf = json.loads(r.read().decode())
        proc, pusher = pf.get("process") or {}, pf.get("pusher") or {}
        cyc = pusher.get("cycles") or 0
        out["perf"] = {"cpuS": proc.get("cpu_s"), "rssKb": proc.get("rss_kb"), "threads": proc.get("threads"),
                       "cycles": cyc, "idleCycles": pusher.get("idle_cycles"),
                       "idleShare": round(float(pusher.get("idle_cycles") or 0) / cyc, 3) if cyc else None,
                       "cycleMsP50": pusher.get("cycle_ms_p50"), "cycleMsP90": pusher.get("cycle_ms_p90")}
    except Exception as e:
        out["perfError"] = "GET /perf failed (%s)" % e.__class__.__name__
    return out


def live_snapshot(state: Path, sid_of=None) -> dict:
    """The live block. Every process is tied to its SESSION: a scope by the sid prefix in its unit name
    (bin/romp-cli-scope names it from ROMP_SID, the session's own sid, not its conversation id), a CLI by the
    ROMP_SID in its environment (cli_sessions). `sid_of` is the environment reader's seam; each pid is read
    once however many views ask (on macOS every read is a `ps` run)."""
    out = {"platform": platform.system(), "t": int(time.time()), "sessions": [], "errors": []}
    reader, seen = sid_of or proc_session_id, {}
    def cached(pid):
        if pid not in seen:
            seen[pid] = reader(pid)
        return seen[pid]
    regs = _regs(state, out["errors"])
    by8: dict[str, list] = {}
    for sid, r in regs.items():
        by8.setdefault(sid[:8].lower(), []).append((sid, r))
    dirs = cgroup_scope_dirs()
    procs = parse_ps(ps_lines())
    if dirs:
        out["source"] = "cgroup"
        for d in dirs:
            s = scope_stats(d)
            hits = by8.get(s["sid8"]) or []
            sid, r = hits[0] if len(hits) == 1 else (None, None)   # two sids sharing a prefix: named by neither
            s["sid"], s["name"] = sid, (r["name"] if r else s["sid8"])
            out["sessions"].append(s)
    else:
        out["source"] = "ps"
        out["sessions"] = ps_session_trees(procs, regs, cached)
    by_sid, unknown = cli_sessions(procs, regs, cached)
    out["duplicateClis"] = [{"sid": s, "name": regs[s]["name"], "pids": p} for s, p in by_sid.items() if len(p) > 1]
    out["unidentifiedClis"] = unknown
    out["threads"] = thread_groups(out["sessions"], regs)
    out["kernel"] = kernel_live(state)
    mem = [s["memBytes"] for s in out["sessions"] if isinstance(s.get("memBytes"), int)]
    out["memTotalBytes"] = sum(mem) if mem else None
    out["sessionsCounted"] = len(out["sessions"])
    out["unmatchedScopes"] = sum(1 for s in out["sessions"] if not s.get("sid"))
    return out


# ── the document and its summary ────────────────────────────────────────────────────────────────────────
def collect(state: Path, kind="day", anchor=None, tz=None, since=None, until=None, live=True,
            label=DEFAULT_LABEL) -> dict:
    cuts, n_cuts = _read_jsonl(state / "restart-cuts.jsonl")
    audit, n_audit = _read_jsonl(state / "restart-audit.jsonl")
    ev, n_ev = _read_jsonl(state / "session-events.jsonl")
    tu, n_tu = _read_jsonl(state / "turns.jsonl")
    restarts = parse_restarts(cuts)
    quiet = parse_quiet_windows(audit, restarts)
    events = parse_events(ev)
    turns = parse_turns(tu)
    sl_turns, machine_cuts, n_states = [], [], 0
    for p in sorted(glob.glob(str(state / "states" / "*.jsonl"))):
        try:
            lines = Path(p).read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        n_states += 1
        t_list, cuts_by = state_log_turns(lines)
        sl_turns.extend(t_list)
        # machineCut rows carry their own float t; count them where they happened
        for ln in lines:
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if isinstance(r, dict) and "machineCut" in r and isinstance(r.get("t"), (int, float)):
                machine_cuts.append((float(r["t"]), str(r["machineCut"])))
    spend, spend_note = {}, {"path": "spend.json", "present": False}
    try:
        spend = json.loads((state / "spend.json").read_text(encoding="utf-8"))
        spend_note = {"path": "spend.json", "present": True, "days": len((spend or {}).get("days") or {})}
    except FileNotFoundError:
        pass
    except Exception as e:
        spend_note = {"path": "spend.json", "present": True, "error": str(e)}
    stamps = [r["t"] for r in restarts] + [e["t"] for e in events]
    if anchor is None:
        first = min([t for t in stamps if since is None or t >= since] or [time.time()])
        anchor = local_date(first, tz)
    buckets = build_buckets(restarts, quiet, events, turns, sl_turns, machine_cuts, spend_by_day(spend),
                            kind, anchor, tz, since, until)
    doc = {"schema": SCHEMA, "generatedAt": int(time.time()), "label": str(label or DEFAULT_LABEL),
           "window": {"kind": kind, "anchor": anchor, "tz": tz or "local"},
           "range": {"since": since, "until": until},
           "sources": {"restartCuts": n_cuts, "restartAudit": n_audit, "sessionEvents": n_ev, "turns": n_tu,
                       "stateLogs": n_states, "spend": spend_note},
           "audit": audit_counts(audit),
           "restarts": restarts, "quietWindows": quiet, "kernelSeries": kernel_series(restarts),
           "events": {"byKind": _count(e["kind"] for e in events), "recent": events[-50:]},
           "buckets": buckets,
           "notes": ["Redo cost (redo.usd, redo.tokens) is read from turns.jsonl, whose rows begin when this "
                     "reader's kernel change landed; the spend ledger (spend.json) has hour and day buckets per "
                     "session and no per-turn rows, so it cannot attribute a turn's cost.",
                     "Turn latency: turns.jsonl stamps are event stamps at millisecond resolution (feed pop, "
                     "first work atom, ResultMessage), present only for turns this kernel fed (a turn the CLI "
                     "opened by itself has no feed and is not a latency sample); stateLogLatencyS is the same "
                     "feed-to-result interval from the state log at one-second resolution, available for turns "
                     "before turns.jsonl, and a pair broken by a machine cut is not a turn."]}
    doc["live"] = live_snapshot(state) if live else {"skipped": True}
    return doc


SAMPLE_CAP = 4000     # latency samples kept per bucket for the distribution figure (evenly strided past this)


def _cap(xs, cap=SAMPLE_CAP):
    xs = [round(float(x), 3) for x in xs]
    if len(xs) <= cap:
        return xs
    step = len(xs) / float(cap)
    return [xs[int(i * step)] for i in range(cap)]


def _count(it):
    c = {}
    for k in it:
        c[k] = c.get(k, 0) + 1
    return c


def _fmt_s(x):
    if not isinstance(x, (int, float)):
        return "-"
    return ("%.1f s" % x) if x < 100 else ("%d s" % round(x))


def _st(s: dict, unit=_fmt_s) -> str:
    if not s or not s.get("n"):
        return "none"
    return "n=%d p50 %s p90 %s max %s" % (s["n"], unit(s["p50"]), unit(s["p90"]), unit(s["max"]))


def _mb(b):
    return "-" if not isinstance(b, (int, float)) else ("%.0f MB" % (b / 1048576.0))


def _size(b):
    """MB below a gigabyte, GB with one decimal from there (many threads' memory reads as 9.8 GB, not 10035 MB)."""
    if not isinstance(b, (int, float)):
        return "memory unknown"
    return _mb(b) if b < 1 << 30 else "%.1f GB" % (b / float(1 << 30))


def summary(doc: dict) -> str:
    """The one-screen text per window."""
    w = doc["window"]
    src = doc["sources"]
    lines = ["restart metrics: %s, %s windows, anchor %s, dates in %s time"
             % (doc.get("label") or DEFAULT_LABEL, w["kind"], w["anchor"], w["tz"])]
    missing = [k for k in ("restartCuts", "restartAudit", "sessionEvents", "turns") if not (src.get(k) or {}).get("present")]
    if missing:
        lines.append("  MISSING ledgers (nothing measured from them): " + ", ".join((src[k] or {}).get("path", k) for k in missing))
    if not doc["buckets"]:
        lines.append("  no rows in range")
    for b in doc["buckets"]:
        lines.append("")
        tz = None if w.get("tz") in (None, "local") else w["tz"]
        lines.append("%s  (%s to %s)" % (b["key"], local_date(b["start"], tz), local_date(b["end"] - 1, tz)))
        per = (" (%s per measured restart)" % b["cutTurnsPerRestart"]) if b["cutTurnsPerRestart"] is not None else ""
        lines.append("  restarts %d · turns cut %d%s · clean restarts %d · boots with no cut row %d"
                     % (b["restarts"], b["cutTurns"], per, b["cleanRestarts"], b["restartsWithoutCutRow"]))
        if b["reasons"]:
            lines.append("  reasons: " + ", ".join("%s %d" % (k, v) for k, v in sorted(b["reasons"].items(), key=lambda kv: -kv[1])))
        lines.append("  outage to first serve %s · reconcile settle %s" % (_st(b["outageS"]), _st(b["settleS"])))
        lines.append("  quiet windows %d · wait %s · backstop fired %d" % (b["quietWindows"], _st(b["quietWaitS"]), b["backstopFires"]))
        unchecked = (" (%d boot(s) could not check every CLI)" % b["duplicateUnchecked"]) if b.get("duplicateUnchecked") else ""
        lines.append("  boot sweeps %d · orphans reaped %d · scopes stopped %d · duplicate CLIs %d%s · crash heals %d · "
                     "crash loops %d · drain left closing %d (cut rows: unjoined %d, reaped %d) · lease problems %d"
                     % (b["boots"], b["orphansReaped"], b["scopesStopped"], b["duplicateClis"], unchecked, b["crashHeals"],
                        b["crashLoops"], b["drainLeftClosing"], b["drainUnjoinedCount"], b["drainReapedCount"], b["leaseProblems"]))
        lines.append("  hosts attached: at boot %d · later %d" % (b["attachedAtBoot"], b["attachedLater"]))
        red = b["redo"]
        spend = (" of $%.2f that day" % b["spendUsd"]) if isinstance(b.get("spendUsd"), (int, float)) and w["kind"] == "day" else \
                (" of $%.2f in the window" % b["spendUsd"]) if isinstance(b.get("spendUsd"), (int, float)) else ""
        lines.append("  continuation notices %d · redo turns %d · redo cost $%.2f%s · redo tokens %s"
                     % (b["resumedTurns"], red["turns"], red["usd"], spend, "{:,}".format(red["tokens"])))
        lat = b["latency"]
        lines.append("  turn latency: feed to result %s · feed to first output %s · api %s"
                     % (_st(lat["feedToResultS"]), _st(lat["feedToFirstOutS"]), _st(lat["apiS"])))
        lines.append("  state-log latency (1 s resolution) %s · machine cuts %s"
                     % (_st(b["stateLogLatencyS"]), ", ".join("%s %d" % kv for kv in sorted(b["machineCuts"].items())) or "none"))
        k = b["kernelAtExit"]
        lines.append("  kernel process at its exits: resident %s · cpu %s"
                     % (_st(k["rssMb"], lambda x: "%.0f MB" % x), _st(k["cpuS"])))
    live = doc.get("live") or {}
    lines.append("")
    if live.get("skipped"):
        lines.append("live: skipped")
    else:
        sess = live.get("sessions") or []
        threads = live.get("threads") or []
        n_thr = sum(g["threads"] for g in threads)
        top = sorted([s for s in sess if isinstance(s.get("memBytes"), int)], key=lambda s: -s["memBytes"])[:3]
        lines.append("live (%s): %d session scopes%s · memory total %s · top: %s"
                     % (live.get("source", "?"), len(sess),
                        (" (%d of them comment threads)" % n_thr) if n_thr else "", _mb(live.get("memTotalBytes")),
                        ", ".join("%s %s" % (s.get("name"), _mb(s["memBytes"])) for s in top) or "-"))
        if threads:
            lines.append("comment threads running, each its own session forked from the one named (not duplicates): "
                         + " · ".join("%s: %d thread%s, %s" % (g["parentName"], g["threads"], "" if g["threads"] == 1 else "s",
                                                               _size(g["memBytes"])) for g in threads))
        if live.get("unmatchedScopes"):
            lines.append("%d scope(s) match no registry row: not identified, so not counted as a thread or a duplicate"
                         % live["unmatchedScopes"])
        for e in live.get("errors") or []:
            lines.append("live: " + e)
        k = live.get("kernel") or {}
        if k.get("error"):
            lines.append("kernel: " + k["error"])
        else:
            pf = k.get("perf") or {}
            idle = ("%.0f%%" % (100 * pf["idleShare"])) if isinstance(pf.get("idleShare"), float) else "-"
            lines.append("kernel: pid %s · up %s · cpu %s · rss %s · pusher idle cycles %s%s"
                         % (k.get("pid"), _fmt_s(k.get("uptimeS")), _fmt_s(pf.get("cpuS")),
                            _mb((pf.get("rssKb") or 0) * 1024) if pf.get("rssKb") else "-", idle,
                            (" · " + k["perfError"]) if k.get("perfError") else ""))
        if live.get("duplicateClis"):
            lines.append("DUPLICATE CLIs NOW (more than one process for one session): "
                         + "; ".join("%s pids %s" % (d["name"], d["pids"]) for d in live["duplicateClis"]))
        if live.get("unidentifiedClis"):
            lines.append("NOT CHECKED for duplicates: %d CLI(s) on this machine's conversations whose session id "
                         "(ROMP_SID) could not be read or names no registry row, pids %s"
                         % (len(live["unidentifiedClis"]), live["unidentifiedClis"]))
    lines.append("")
    for n in doc.get("notes") or []:
        lines.append("note: " + n)
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="romp restart-metrics", description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the whole document as JSON")
    ap.add_argument("--window", choices=("day", "week"), default="day")
    ap.add_argument("--anchor", help="YYYY-MM-DD the weeks start from (default: the first restart's day)")
    ap.add_argument("--since", help="YYYY-MM-DD, inclusive")
    ap.add_argument("--until", help="YYYY-MM-DD, exclusive")
    ap.add_argument("--tz", help="a zone name for the day boundaries (default: the machine's local time)")
    ap.add_argument("--no-live", action="store_true", help="skip the live reads (scopes, ps, the kernel's routes)")
    ap.add_argument("--state", help="a state directory other than this machine's")
    ap.add_argument("--label", default=DEFAULT_LABEL,
                    help="what the document calls this machine (default: '%s'; never the hostname unless you say so)" % DEFAULT_LABEL)
    a = ap.parse_args(argv)
    state = Path(a.state) if a.state else state_dir()
    try:
        since = day_start(a.since, a.tz) if a.since else None
        until = day_start(a.until, a.tz) if a.until else None
        if a.anchor:
            day_start(a.anchor, a.tz)
    except ValueError as e:
        sys.stderr.write("romp restart-metrics: bad date: %s\n" % e)
        return 2
    doc = collect(state, kind=a.window, anchor=a.anchor, tz=a.tz, since=since, until=until, live=not a.no_live,
                  label=a.label)
    if a.json:
        sys.stdout.write(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(summary(doc) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
