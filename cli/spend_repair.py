#!/usr/bin/env python3
"""romp spend-repair: recompute a day's spend from the turn ledger's re-attach staircase (T354).

The fault it repairs: a session under a per-session host keeps its CLI process across a kernel restart, and the
CLI's total_cost_usd is cumulative per process; the kernel seeded its watermark at zero for what it took for a fresh
process, so the first result after each restart recorded the process lifetime as one turn. On the turn ledger this
reads as a STAIRCASE per session: rows whose dollars are the cumulative total (monotone across the day's restarts),
between ordinary rows.

The arithmetic (the manager's rule, 2026-09-11): a session's first result after a kernel restart is cumulative; its
true cost is the cumulative less the previous cumulative less the ordinary rows between the two; the day's first
cumulative row, with no previous cumulative, counts as a typical turn (the median of the session's ordinary rows that
day, else the day's median across sessions, else nothing). A candidate whose dollars fall BELOW the previous cumulative
is a fresh process (its total started over), not a staircase row: it stands as recorded and starts a new chain.

Sources: turns.jsonl (with its rotated predecessor), restart-cuts.jsonl and restart-audit.jsonl for the restart
instants, spend.json for the buckets. Pure functions over rows for the tests; the command prints before and after per
hour and per session and changes nothing unless --apply. Tokens are left as recorded (the same staircase exists in the
token watermarks; this pass repairs dollars only, and says so).

`--restored-cost-state` runs a second rule (6) over the whole turn ledger: first results that recorded a cost-state record
a resumed CLI restored and the kernel's seed missed (the section above restored_plan has the fault and the evidence).
"""
import argparse
import json
import os
import statistics
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RESTART_ACTIONS = ("manager-sigterm", "remote-restart", "main-converge", "p2p-update", "self-update")
TYPICAL_MULTIPLE = 3.0     # a first-after-restart row with no previous cumulative counts as cumulative only when it
MIN_STAIRCASE_USD = 20.0   # is this many times the session's typical turn and at least this much: a modest first
#                            turn after a restart is a turn, not the lifetime


def state_dir() -> Path:
    return Path(os.environ.get("ROMP_STATE_DIR")
                or Path(os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local/state")) / "romp")


def read_jsonl(path: Path, bad: list = None) -> list:
    """Every parseable object row of `path`, its rotated predecessor (<name>.1) first when present. A line that does
    not parse (a torn append) is counted into `bad` when a list is given, so the report can say how many were skipped."""
    out = []
    for p in (path.with_name(path.name + ".1"), path):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for ln in text.splitlines():
            if not ln.strip():
                continue
            try:
                o = json.loads(ln)
            except Exception:
                if bad is not None:
                    bad.append(p.name)
                continue
            if isinstance(o, dict):
                out.append(o)
    return out


def local_day(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%d")


def local_hour(t) -> str:
    return datetime.fromtimestamp(float(t)).strftime("%Y-%m-%dT%H")


REPAIR_JOURNAL = "spend-repair.jsonl"   # beside the ledger: each --apply's row deltas; the mark that their buckets were folded
#                                          lives INSIDE spend.json (repairJournal.folded), written in the same atomic replace
REPAIR_RULE = 4                          # stamped on every row this rule corrects (repairRule); a standing correction is kept
REPAIR_RULE_LIFETIME = 5                 # stamped on a row the lifetime road corrects (a lifetime billed once more after an
#                                          attach-unknown row) and carried in the journal's deltas, so a row is auditable per rule
#                                          only when this rule wrote it and it reads as a plausible typical turn


def restart_instants(cuts: list, audit: list = None) -> list:
    """The moments a new kernel took over, sorted, deduplicated to the second: every restart-cuts BOOT row's
    `firstServe` (the epoch the new kernel began serving; the row's `t` when it has none). Nothing else is an
    instant. The audit row is the REQUEST, and on the live ledgers most unanswered requests are PARKED ones (a quiet
    deploy, no restart followed): taken for instants they made a session's next ordinary turn a first result, reset
    the chain's baseline to that small figure and the next real re-bill was corrected to almost the whole lifetime
    (the round-three review's HIGH); a request a boot answers adds nothing the boot does not; a restart with no boot
    row wrote no first serve and re-billed nothing. The cut row is written by the DYING kernel at the start of its exit, before its drain, so
    results land for seconds after it and are the old kernel's. The boot row's own
    `t` is the SETTLE, which lagged the first serve by three minutes on 2026-09-11 at 20:19Z while the re-billed
    first results landed from 20:19:44Z. Every row at or before the first-serve second is the old kernel's."""
    out = set()
    for r in cuts:
        if not isinstance(r.get("t"), (int, float)):
            continue
        if "firstServe" in r or "settleS" in r or "bootSettled" in r:
            fs = r.get("firstServe")
            out.add(int(fs) if isinstance(fs, (int, float)) and fs > 0 else int(r["t"]))
    return sorted(out)
def _typical(usds: list) -> float:
    return float(statistics.median(usds)) if usds else 0.0


def _kernel_usd(r: dict) -> float:
    """The figure the kernel wrote for a row: usdRecorded where a run corrected it, else usd."""
    v = r.get("usdRecorded")
    return float(v) if isinstance(v, (int, float)) else float(r["usd"])


def journal_pending(state: Path, spend: dict, bad: list = None, name: str = REPAIR_JOURNAL) -> list:
    """Row deltas an earlier --apply wrote to turns.jsonl whose bucket write never completed: every `rows` entry of
    the repair journal whose stamp is not among the refs spend.json itself carries (repairJournal.folded, written in
    the same atomic replace as the fold, so a death between the two writes, or a restore of spend.json from a copy,
    leaves exactly the unfolded entries pending and never re-folds a folded one: the round-four MEDIUM). A torn journal
    line is counted into `bad` (the round-four low). Oldest first."""
    folded = set()
    rj = spend.get("repairJournal") if isinstance(spend, dict) and isinstance(spend.get("repairJournal"), dict) else {}
    for x in rj.get("folded") or []:
        folded.add(x)
    rows = []
    for o in read_jsonl(state / name, bad):
        if o.get("phase") == "rows" and isinstance(o.get("deltas"), list):
            rows.append(o)
        elif o.get("phase") == "buckets" and o.get("ref") is not None:
            folded.add(o.get("ref"))          # the earlier rule's mark, written to the journal after the fold: honoured, so a
            #                                   ledger those runs folded is not folded again by this one
    return [o for o in rows if o.get("t") not in folded]


def mark_folded(spend: dict, refs: list) -> dict:
    """The fold's completion recorded inside the ledger. Unbounded (a few hundred small numbers a day): a cap dropped
    the oldest ref while the journal kept its rows entry, and that entry read as pending again, a double debit."""
    rj = spend.setdefault("repairJournal", {}) if isinstance(spend.get("repairJournal"), dict) else spend.__setitem__("repairJournal", {}) or spend["repairJournal"]
    have = list(rj.get("folded") or [])
    for r in refs:
        if r not in have:
            have.append(r)
    rj["folded"] = have
    return spend


def journal_append(state: Path, entry: dict, name: str = REPAIR_JOURNAL) -> None:
    with open(state / name, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + chr(10))


def journal_compact(state: Path, spend: dict, name: str = REPAIR_JOURNAL) -> int:
    """The journal rewritten to the entries still pending against `spend` (round six of the review: never trimmed, it
    was re-read whole on every run, and an entry folded long ago stayed a candidate for a ledger that lost its refs).
    Returns the entries dropped. Atomic replace; a torn line is kept (counted elsewhere, --apply refuses on it)."""
    path = state / name
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    pending = {o.get("t") for o in journal_pending(state, spend, name=name)}
    keep, dropped = [], 0
    for ln in lines:
        if not ln.strip():
            continue
        try:
            o = json.loads(ln)
        except Exception:
            keep.append(ln); continue
        if o.get("phase") == "rows" and o.get("t") in pending:
            keep.append(ln)
        else:
            dropped += 1                                  # folded entries and their marks (both are in the ledger's refs)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(l + chr(10) for l in keep), encoding="utf-8")
    os.replace(tmp, path)
    return dropped


def fold_deltas(spend: dict, deltas: list, day: str, turns: list) -> tuple:
    """The journal's deltas folded into the buckets, the same fold as a plan's rows. Each entry names the figure its
    row held before that run (`corrected - delta`) and the figure it wrote (`corrected`); the fold is the row's PRESENT
    figure less the former, on `turns` as they stood before this run's own rewrite, so a row edited or re-judged since
    the entry was written still brings its buckets to what the rows say (the plan's own delta folds on top). A row the
    ledger no longer holds is skipped. Returns (spend, rows folded against a present figure other than the recorded one,
    rows skipped)."""
    held = {(str(r.get("sid") or ""), int(r["t"])): round(float(r["usd"]), 6) for r in turns
            if isinstance(r.get("t"), (int, float)) and isinstance(r.get("usd"), (int, float))}
    take, moved, skipped = [], 0, 0
    for d in deltas:
        key = (str(d.get("sid") or ""), int(d["t"]))
        present = held.get(key)
        if present is None:
            skipped += 1
            continue
        corrected = d.get("corrected")
        if isinstance(corrected, (int, float)):
            before = round(float(corrected) - float(d["delta"]), 6)
            if present != round(float(corrected), 6):
                moved += 1
            take.append(dict(d, current=0.0, corrected=round(present - before, 6), name=d.get("name") or ""))
        else:
            take.append(dict(d, current=0.0, corrected=float(d["delta"]), name=d.get("name") or ""))
    p = {"day": day, "rows": take}
    out = apply_to_spend(spend, p)
    return out, moved, skipped, list(p.get("notes") or [])   # the fold's notes ride out: a clamp here is said too (round five)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def parse_spend(text: str) -> dict:
    """The ledger, or {} for no file; a file that does not parse (or is not an object) raises ValueError, and the
    caller refuses to run (M3 of the review)."""
    if not text.strip():
        return {}
    v = json.loads(text)
    if not isinstance(v, dict):
        raise ValueError("not a JSON object")
    return v


def registry_maps(state: Path):
    """From the registry (sdk/<sid>.json): {thread sid: owner sid} for comment threads (threadOf, the session the
    kernel billed the thread's turns to) and the set of sids whose CLI bills an API key (apiKeyAuth, the persisted
    init truth). Both are the registry's CURRENT word; a session that changed auth mid-day is read as it is now."""
    owners, keyed = {}, set()
    for f in sorted((state / "sdk").glob("*.json")) if (state / "sdk").is_dir() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(r, dict):
            continue
        sid = str(r.get("sid") or f.stem)
        if r.get("threadOf"):
            owners[sid] = str(r["threadOf"])
        if r.get("apiKeyAuth"):
            keyed.add(sid)
    return owners, keyed


def parse_since(text: str):
    """--since: an ISO instant (2026-09-11T14:09:56Z, or a local date-time without a zone) or epoch seconds; None when empty."""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    t = text.strip()
    if t.endswith("Z"):
        return datetime.strptime(t[:-1], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).timestamp()
        except ValueError:
            continue
    raise ValueError(text)


def plan(turns: list, restarts: list, day: str, since=None, owners=None, keyed=None) -> dict:
    """The corrections for `day`: {"rows": [{sid, name, t, hour, recorded, corrected, reason}], "hours": {hour: {before,
    after}}, "days": {day: {before, after}}, "bySid": {sid: {name, before, after}}}, computed from the turn rows alone
    (the buckets' before/after are the sums over the day's rows, so a bucket the rows do not explain is left alone).
    A row is a staircase candidate when a restart instant lies after the session's previous row and at or before it
    (or, for the session's first row of the day, at or before it that day). `since` (epoch seconds) is the instant the
    per-session hosts came on: before it every restart killed the CLI, so a first result after a restart is a fresh
    process's own turn and never a step (a row corrected by an earlier run without the bound is restored). `owners`
    maps a comment thread's sid to the session it bills (the registry's threadOf: the kernel folds a thread's turns
    into its owner's bySid); `keyed` names the sids whose turns bill an API key (the registry's apiKeyAuth), whose
    corrections reach the buckets' `key` split too; a row whose session neither map knows is folded on its own sid
    and its keyed split is left as recorded."""
    owners, keyed = owners or {}, keyed or set()
    rows = [r for r in turns if isinstance(r.get("t"), (int, float)) and isinstance(r.get("usd"), (int, float))
            and local_day(r["t"]) == day]
    rows.sort(key=lambda r: (str(r.get("sid") or ""), float(r["t"])))
    by_sid = {}
    for r in rows:
        by_sid.setdefault(str(r.get("sid") or ""), []).append(r)
    day_start = datetime.strptime(day, "%Y-%m-%d").timestamp()
    corrections, all_ordinary, notes = [], [], []
    # first pass per session: which rows are staircase steps, which are ordinary. A row already repaired (usdRecorded
    # keeps the figure the kernel wrote) is judged AGAIN on that figure, so a run over repaired rows finds nothing new
    # when the judgement stands and restores the row when it does not (a rule tightened after the first run brings a
    # zeroed turn back); a row written by a kernel that carries the CLI's cumulative (T354's fix) is never a step.
    marked, rs_by_sid, step_ids_by_sid, lifetimes = {}, {}, {}, {}
    for sid, rs in by_sid.items():
        # the session's typical turn: the median of its rows that are NOT a first result after a restart (those are a
        # cumulative or a fresh process's first turn, both atypical); the same figure decides the threshold and the
        # correction, and it does not move when a first row is corrected or restored, so a second run agrees with the first
        firsts, pt = set(), day_start
        for r in rs:
            if any(pt <= x < float(r["t"]) for x in restarts):   # a row AT the first-serve second is the old kernel's; the
                #                                                       first row strictly after the instant is the first
                firsts.add(id(r))
            pt = float(r["t"])
        # the provisional typical turn for the first-cumulative threshold: rows following no restart, on the KERNEL's
        # figure (usdRecorded where a run corrected the row), so it reads the same on every run; the correction's
        # typical is taken below over the rows that are not steps, once they are known (low a of the review)
        typical = _typical([_kernel_usd(r) for r in rs if id(r) not in firsts]) or 0.0
        prev_t, prev_cum, steps, restores = day_start, None, [], []
        between = []
        unknown_armed, lifetime = False, []
        for r in rs:
            t, usd = float(r["t"]), float(r["usd"])
            restarted = id(r) in firsts
            step = False
            if isinstance(r.get("cumulativeUsd"), (int, float)) or r.get("spendBaseline"):
                # written by a kernel that carries the CLI's cumulative on the row, or names a first result's baseline
                # (the fix): right, never a step, with ONE rule of its own (the fix's first boot, 2026-09-11 22:38Z): a
                # row whose KERNEL figure equals its cumulative, in a session whose attach-unknown row precedes it, is
                # the lifetime billed once more (the replayed first result left the watermark at zero), and its true
                # cost is the kernel's own arithmetic: the cumulative less the PREVIOUS same-session row's cumulative
                # (a replay row with usd 0 and a rising cumulative counts as that previous row: the unknown window can
                # replay several, and the first replayed cumulative is the wrong baseline; the 1473 review's third
                # round). The guard is the kernel's reset comparison: the cumulative ABOVE the previous row's. The first
                # paid turn after a mid-life /clear is written with its dollars equal to its cumulative BY DESIGN (the
                # watermark is zeroed on the /clear), a counter reset that the first cut of this rule zeroed in silence
                # (the review's HIGH); it stands, said in a note, never a clamp. The chain disarms on the row it judged
                # (corrected or not), on a counter reset and on a fresh or seeded baseline row; a corrected row is judged
                # again on its recorded figure every run (idempotent) and restored when the rule no longer believes it
                cum = float(r["cumulativeUsd"]) if isinstance(r.get("cumulativeUsd"), (int, float)) else None
                base = r.get("spendBaseline")
                rec_k = _kernel_usd(r)
                repaired = isinstance(r.get("usdRecorded"), (int, float))
                candidate = cum is not None and not base and abs(rec_k - cum) < 1e-6 and rec_k > 0
                who = "%s %s" % (str(r.get("name") or sid[:8]), datetime.fromtimestamp(t).strftime("%H:%M:%S"))
                if candidate and unknown_armed:
                    if prev_cum is not None and cum > prev_cum + 1e-6:
                        remainder = cum - prev_cum
                        if abs(remainder - usd) > 1e-6:
                            lifetime.append((r, usd, remainder, prev_cum, None))
                    else:
                        why = ("its cumulative %.4f is at or below the previous row's %.4f: a counter reset (a /clear's first paid "
                               "turn is written with its dollars equal to its cumulative), the turn stands" % (cum, prev_cum)
                               if prev_cum is not None else "no earlier row of the session carries a cumulative to stand it on")
                        notes.append("%s: the lifetime rule stood down, %s" % (who, why))
                        if repaired and abs(usd - rec_k) > 1e-9:
                            lifetime.append((r, usd, rec_k, prev_cum, why))   # a restore
                    unknown_armed = False
                elif candidate and repaired and abs(usd - rec_k) > 1e-9:
                    # a correction whose attach-unknown row no longer arms a chain before it: restored
                    lifetime.append((r, usd, rec_k, None, "no attach-unknown row arms a chain before it"))
                elif cum is not None and base == "attach-unknown":
                    unknown_armed = True
                elif base in ("fresh", "seeded") or (cum is not None and prev_cum is not None and cum < prev_cum - 1e-6):
                    unknown_armed = False                             # a new or seeded process, or a counter reset: no lifetime follows
                if cum is not None:
                    prev_cum = cum; between = []
                prev_t = t
                continue
            repaired = isinstance(r.get("usdRecorded"), (int, float))
            rec = float(r["usdRecorded"]) if repaired else usd
            if since is not None and t < since:
                # before the hosts came on: a fresh process each restart, the turn stands (restored, when an earlier
                # run without the bound took it). No baseline carries over: the first restart after the hosts' start
                # kills the plain child, so the first row after it is a fresh process, judged as the day's first
                if repaired and abs(rec - usd) > 1e-9:
                    restores.append((r, rec, usd, "since", []))
                prev_t = t
                continue
            if restarted:
                if prev_cum is not None:
                    # the surviving process's lifetime again: at or above the previous cumulative PLUS every turn recorded
                    # between, since a process's total grows by at least what its own rows recorded. A figure below that
                    # cannot be this process's cumulative: it is a fresh process's first turn. (The first run's rule was
                    # `>= prev_cum` alone and zeroed 49 small genuine turns on 2026-09-11; they carry usdRecorded and
                    # come back through here to be restored.)
                    step = rec >= prev_cum + sum(between) - 1e-6
                else:
                    step = rec >= max(MIN_STAIRCASE_USD, TYPICAL_MULTIPLE * typical) if typical else rec >= MIN_STAIRCASE_USD
            elif steps and prev_cum is not None and rec >= max(prev_cum + sum(between) - 1e-6, MIN_STAIRCASE_USD):
                # no restart instant on record between this row and the last (a crash leaves no audit row), yet the
                # row bears the staircase's signature on a chain this session has already shown: at or above the
                # previous cumulative plus every turn between, and no small figure. Taken as a step, said in the
                # reason; without an established chain the signature alone is not believed
                step = "no-instant"
            if step:
                steps.append((r, rec, usd, prev_cum, list(between), step == "no-instant"))
                prev_cum = rec
                between = []
            elif restarted or repaired:
                # a FRESH process's first result (below the previous cumulative plus the rows between, or a modest first
                # turn): the turn stands as the kernel recorded it, and its total IS the new process's cumulative, so the
                # chain continues from it; a repaired row that is no step is restored to the kernel's figure
                if repaired and abs(rec - usd) > 1e-9:
                    restores.append((r, rec, usd, prev_cum, list(between)))
                if restarted:
                    prev_cum = rec
                    between = []
                else:
                    between.append(rec)        # a repaired row that follows no restart (an earlier run's instants were
                    #                            wrong): an ordinary turn, restored above, counted between
            else:
                between.append(usd)
            prev_t = t
        if steps and steps[0][3] is None and not (len(steps) > 1 and steps[1][3] == steps[0][1]):
            # the day's first cumulative row is believed only when a staircase DESCENDS from it: the session's next
            # step must stand on it (its previous cumulative is this row's figure, so it is at or above it plus the
            # turns between). Alone, or followed only by a chain that started afresh (a CLI that died mid-day), a big
            # first result after a restart is as likely an honest long first turn, and it stands (restored, when an
            # earlier run took it) (M2 of the review)
            r, rec, usd, _, _, _ = steps.pop(0)
            if isinstance(r.get("usdRecorded"), (int, float)) and abs(rec - usd) > 1e-9:
                restores.append((r, rec, usd, None, []))
        step_ids = {id(x[0]) for x in steps}
        ordinary = [_kernel_usd(r) for r in rs if id(r) not in step_ids]   # every row that is not a step, the kernel's figure
        marked[sid] = (steps, restores, _typical(ordinary) or 0.0)
        lifetimes[sid] = lifetime
        rs_by_sid[sid], step_ids_by_sid[sid] = rs, step_ids
        all_ordinary.extend(ordinary)
    day_typical = _typical(all_ordinary)

    def entry(r, sid, rec, cur, corrected, reason, **extra):
        return {"sid": sid, "owner": owners.get(sid, sid), "keyed": sid in keyed,
                "name": str(r.get("name") or sid[:8]), "t": int(r["t"]), "hour": local_hour(r["t"]),
                "recorded": round(rec, 6), "current": round(cur, 6), "corrected": round(corrected, 6), "reason": reason, **extra}

    for sid, (steps, restores, typical) in marked.items():
        typical = typical or day_typical
        for r, rec, cur, prev_cum, between, no_instant in steps:
            if prev_cum is None:
                before = [_kernel_usd(x) for x in rs_by_sid[sid] if id(x) not in step_ids_by_sid[sid] and float(x["t"]) < float(r["t"])]
                typical_before = _typical(before) or typical
                if isinstance(r.get("usdRecorded"), (int, float)) and r.get("repairRule") == REPAIR_RULE \
                        and cur < max(MIN_STAIRCASE_USD, TYPICAL_MULTIPLE * (typical_before or typical)):
                    continue                   # a standing correction THIS rule wrote, and a plausible typical turn: kept, so the
                    #                            day's later rows do not rewrite it (low C). One an earlier rule wrote (a phantom
                    #                            baseline from a parked request left 497 standing as 'the turn') is judged again
                    #                            (the round-four HIGH)
                corrected, reason = typical_before, "the day's first cumulative row: a typical turn (median %.4f of the session's earlier rows)" % typical_before
            else:
                corrected = rec - prev_cum - sum(between)      # >= 0 by the bound that made this a step, to float noise
                if corrected < 0:
                    corrected = 0.0
                reason = "cumulative %.4f less the previous cumulative %.4f less %d row(s) between (%.4f)%s" % (
                    rec, prev_cum, len(between), sum(between), "; no restart instant on record, the staircase's signature alone" if no_instant else "")
            if abs(corrected - cur) < 1e-6:
                continue                       # already right: a run over repaired rows
            corrections.append(entry(r, sid, rec, cur, corrected, reason))
        for r, cur, corrected, pcum, why in lifetimes.get(sid, []):
            rec_k = _kernel_usd(r)
            if why is None:
                corrections.append(entry(r, sid, rec_k, cur, corrected,
                                         "the lifetime billed once more after the attach-unknown row (cumulative %.4f less the previous row's cumulative %.4f)"
                                         % (rec_k, pcum), rule=REPAIR_RULE_LIFETIME))
            else:
                corrections.append(entry(r, sid, rec_k, cur, rec_k, "restored: %.4f is no lifetime billed once more: %s" % (rec_k, why), restore=True))
        for r, rec, cur, prev_cum, between in restores:
            if prev_cum == "since":
                reason = "restored: %.4f precedes the hosts' start (%s), a fresh process's turn" % (
                    rec, datetime.fromtimestamp(since).strftime("%Y-%m-%d %H:%M:%S"))
            elif prev_cum is None:
                reason = "restored: %.4f is a lone first result after a restart with no staircase following it, a turn" % rec
            else:
                reason = "restored: %.4f is below the previous cumulative %.4f plus %d row(s) between (%.4f), a turn of a fresh process" % (
                    rec, prev_cum, len(between), sum(between))
            corrections.append(entry(r, sid, rec, cur, rec, reason, restore=True))
    hours, sids = {}, {}
    for r in rows:
        h, sid = local_hour(r["t"]), str(r.get("sid") or "")
        owner = owners.get(sid, sid)                    # a thread's turns count under the session it bills
        hours.setdefault(h, {"before": 0.0, "after": 0.0})
        sids.setdefault(owner, {"name": str(r.get("name") or sid[:8]) if owner == sid else owner[:8], "before": 0.0, "after": 0.0})
        if owner == sid:
            sids[owner]["name"] = str(r.get("name") or sid[:8])
        hours[h]["before"] += float(r["usd"]); hours[h]["after"] += float(r["usd"])
        sids[owner]["before"] += float(r["usd"]); sids[owner]["after"] += float(r["usd"])
    for c in corrections:
        d = c["corrected"] - c["current"]
        hours[c["hour"]]["after"] += d
        sids[c["owner"]]["after"] += d
    for m in list(hours.values()) + list(sids.values()):
        m["before"], m["after"] = round(m["before"], 6), round(m["after"], 6)
    before = round(sum(h["before"] for h in hours.values()), 6)
    after = round(sum(h["after"] for h in hours.values()), 6)
    return {"day": day, "since": since, "rows": sorted(corrections, key=lambda c: c["t"]), "hours": dict(sorted(hours.items())),
            "stoodDown": notes,                        # the lifetime rule's refusals, said (never a clamp)
            "unkeyedRows": sum(1 for c in corrections if not c["keyed"]),
            "days": {day: {"before": before, "after": after}}, "bySid": sids, "restarts": len([x for x in restarts if local_day(x) == day])}


def apply_to_spend(spend: dict, p: dict) -> dict:
    """spend.json with the plan's deltas folded into the day's hour buckets, its day bucket and their bySid maps
    (dollars only; turns and tokens stay). A row's delta reaches bySid under the session it BILLS (a comment
    thread's owner, the registry's threadOf, as the kernel folded it) and the `key` split only when that session
    bills an API key (the registry's apiKeyAuth; turn rows carry no keyed flag of their own), else the split is left as
    recorded and the plan's notes say for how many rows. A bucket the ledger lacks is left alone, said in the notes."""
    out = json.loads(json.dumps(spend or {}))
    hours = out.setdefault("hours", {}) if isinstance(out.get("hours"), dict) else out.__setitem__("hours", {}) or out["hours"]
    days = out.setdefault("days", {}) if isinstance(out.get("days"), dict) else out.__setitem__("days", {}) or out["days"]

    clamped = []

    def add(d, delta, where):
        v = float(d.get("usd") or 0) + delta
        if v < -1e-6:
            clamped.append("%s would go %.4f below zero; held at zero" % (where, -v))   # said, never silent (round four)
            v = 0.0
        d["usd"] = round(v, 6)

    def fold(bucket, owner, delta, keyed, where):
        if not isinstance(bucket, dict):
            return False
        add(bucket, delta, where)
        k = bucket.get("key")
        if keyed and isinstance(k, dict) and isinstance(k.get("usd"), (int, float)):
            add(k, delta, where + " key")
        by = bucket.get("bySid")
        if isinstance(by, dict) and isinstance(by.get(owner), dict):
            add(by[owner], delta, where + " bySid " + owner[:8])
            sk = by[owner].get("key")
            if keyed and isinstance(sk, dict) and isinstance(sk.get("usd"), (int, float)):
                add(sk, delta, where + " bySid " + owner[:8] + " key")
        elif isinstance(by, dict):
            return "no bySid entry for %s" % owner[:8]
        return True

    notes = []
    for c in p["rows"]:
        delta = c["corrected"] - c["current"]      # against the row as it stands now (a repaired row's current figure)
        for where, bucket in (("hour %s" % c["hour"], hours.get(c["hour"])), ("day %s" % p["day"], days.get(p["day"]))):
            got = fold(bucket, c["owner"], delta, c["keyed"], where)
            if got is False:
                notes.append("no bucket for %s (%s)" % (where, c["name"]))
            elif got is not True:
                notes.append("%s: %s" % (where, got))
    if p.get("unkeyedRows"):
        notes.append("%d corrected row(s) belong to sessions the registry does not mark as API-key billed: their buckets' "
                     "key split is left as recorded" % p["unkeyedRows"])
    p["notes"] = sorted(set(notes)) + clamped
    return out


def apply_to_turns(path: Path, p: dict, write: bool = True) -> list:
    """turns.jsonl (and its predecessor) rewritten with each corrected row's usd, the kernel's figure kept as
    usdRecorded (a row corrected twice keeps the original); a restored row gets the kernel's figure back and loses its
    repair marks. Returns the plan rows actually rewritten (matched by sid, second and the figure the row held), so the
    caller folds the buckets for those and no other. The file handling is _rewrite_turns'."""
    want = {(c["sid"], c["t"], c["current"]): c for c in p["rows"]}

    def stamp(o):
        key = (str(o.get("sid") or ""), int(o["t"]) if isinstance(o.get("t"), (int, float)) else None,
               round(float(o["usd"]), 6) if isinstance(o.get("usd"), (int, float)) else None)
        c = want.pop(key, None)
        if c is not None:
            if c.get("restore"):
                o["usd"] = c["corrected"]
                o.pop("usdRecorded", None); o.pop("repairedT", None); o.pop("repairRule", None)
            else:
                o.setdefault("usdRecorded", o["usd"])
                o["usd"] = c["corrected"]
                o["repairedT"] = int(time.time())
                o["repairRule"] = int(c.get("rule") or REPAIR_RULE)   # the rule that wrote it, auditable per row
        return c

    return _rewrite_turns(path, stamp, write)


def _rewrite_turns(path: Path, stamp, write: bool = True) -> list:
    """turns.jsonl (and its predecessor) rewritten through `stamp`, which edits a row in place and returns the plan entry
    it answers (None for a row it leaves alone); returns those entries. The kernel appends to this file while it runs
    (one open-append-close per result): the file is read again just before the replace and every line past the count
    first read rides along, and read once more after it. Atomic per file. `write` False is a dry match: the caller
    journals exactly the rows the rewrite will touch, first."""
    done = []
    for f in (path.with_name(path.name + ".1"), path):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        out = []
        n_read = len(lines)
        hit = False
        for ln in lines:
            try:
                o = json.loads(ln)
            except Exception:
                out.append(ln); continue
            c = stamp(o) if isinstance(o, dict) else None
            if c is not None:
                done.append(c)
                hit = True
            out.append(json.dumps(o))
        if not hit or not write:
            continue                                  # nothing of the plan in this file, or a dry match
        try:
            now_lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            now_lines = lines
        if len(now_lines) > n_read and now_lines[:n_read] == lines:
            out.extend(now_lines[n_read:])
        tmp = f.with_name(f.name + ".repair.tmp")
        tmp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
        os.replace(tmp, f)
        try:
            after = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            after = out
        if len(after) < len(out):
            f.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    return done


def report(p: dict) -> str:
    n_restore = sum(1 for c in p["rows"] if c.get("restore"))
    lines = ["spend repair for %s: %d restart(s) that day, %d cumulative row(s) found%s" % (
        p["day"], p["restarts"], len(p["rows"]) - n_restore, ", %d earlier correction(s) to restore" % n_restore if n_restore else "")]
    if p.get("since") is not None:
        lines.append("rows before %s (the hosts' start, --since) are fresh processes' turns, never steps"
                     % datetime.fromtimestamp(p["since"]).strftime("%Y-%m-%d %H:%M:%S"))
    else:
        lines.append("no --since: every first result after a restart is judged, the hosts' start unknown (a plain child's "
                     "long first turn can pass the bound by chance; pass the instant the hosts came on)")
    lines.append("")
    lines.append("per session (dollars before -> after):")
    for sid, m in sorted(p["bySid"].items(), key=lambda kv: -kv[1]["before"]):
        if any(c["sid"] == sid for c in p["rows"]):
            lines.append("  %-24s %10.2f -> %10.2f" % (m["name"][:24], m["before"], m["after"]))
    lines.append("")
    lines.append("per hour (dollars before -> after):")
    for h, m in p["hours"].items():
        mark = "" if abs(m["after"] - m["before"]) < 1e-6 else "   *"
        lines.append("  %s %10.2f -> %10.2f%s" % (h, m["before"], m["after"], mark))
    d = p["days"][p["day"]]
    lines.append("")
    lines.append("the day: %.2f -> %.2f" % (d["before"], d["after"]))
    lines.append("")
    lines.append("rows (session, time, now -> corrected, why):")
    for c in p["rows"]:
        lines.append("  %-16s %s %10.2f -> %8.2f  %s" % (c["name"][:16], datetime.fromtimestamp(c["t"]).strftime("%H:%M:%S"),
                                                        c["current"], c["corrected"], c["reason"]))
    if p.get("notes") or p.get("stoodDown"):
        lines.append("")
        lines.extend("note: " + n for n in list(p.get("stoodDown") or []) + list(p.get("notes") or []))
    return "\n".join(lines)


# ── Rule 6: a first result that recorded a restored cost-state record (the resume seed's old 8 MB bound) ──────────────
#
# The fault (fixed by #162, 2026-10-08): a CLI that resumes a conversation, a fork-session resume included, restores the
# last `cost-state` record of the transcript it loads, so its first result's total_cost_usd is that record's total plus
# the turn, and its modelUsage map the record's token counts plus the turn's. The kernel seeds its watermarks from the
# same record, but read only the transcript's last 8 MB; where the record lay further back the seed stood at zero and
# the restored history was recorded as the turn: a `spendBaseline: "fresh"` row whose dollars and token columns carry
# the record's totals on top of the turn's own.
#
# The evidence that a row is one, read from the authoritative records on both sides: the transcript the CLI loaded (the
# session's own conversation or, for a session born as a fork such as a comment thread, its parent's; the split copies
# in the state's repair backups too) holds a cost-state record R; R's token sums do not exceed the row's; and the row's
# dollars less the turn's own API calls (every assistant record's `usage` in the session's transcripts and its
# subagents', within the turn, priced at Claude Code's own per-call rates) equal R's totalCostUSD. The correction is the
# kernel's own arithmetic with the seed it should have read: the row less R, dollars and the four token columns.

REPAIR_RULE_RESTORED = 6
RESTORED_JOURNAL = "spend-repair-restored.jsonl"   # this rule's own journal: its deltas carry tokens and a login, which
#                                                    the staircase rule's recovery fold does not know how to fold
MATCH_ABS, MATCH_REL = 0.25, 0.01   # the match's tolerance: a quarter plus 1% of the turn's own calls. The CLI bills a few
#                                     side calls it writes to no transcript (a session title from a small model: live, one
#                                     such row sat 3.5 cents above its calls plus the record, 31,749 input and 692 output
#                                     tokens the transcript does not hold), and a record is dollars apart from the next
MIN_RESTORED = 1.0                  # a record below this is no history a correction could tell from those side calls
WINDOW_PAD = 5.0                    # seconds either side of the turn a call's record may be stamped
TOKS = ("tokIn", "tokOut", "tokCacheR", "tokCacheW")
_MU_KEYS = ("inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")

# Claude Code's per-call prices in $ per million tokens (input, output, cache write 5 m, cache write 1 h, cache read),
# from its model catalog as of 2.1.28x; a call whose usage says speed "fast" takes the model's fast rate where it has
# one, usage.inference_geo "us" multiplies by 1.1, a web search request is a cent. A model not listed leaves the row
# unverified, said in the report, never priced by a guess.
_T_3_15, _T_5_25 = (3, 15, 3.75, 6, 0.3), (5, 25, 6.25, 10, 0.5)
CLI_PRICES = {
    "claude-3-5-haiku": (0.8, 4, 1, 1.6, 0.08), "claude-haiku-4-5": (1, 5, 1.25, 2, 0.1),
    "claude-3-5-sonnet": _T_3_15, "claude-3-7-sonnet": _T_3_15, "claude-sonnet-4-0": _T_3_15,
    "claude-sonnet-4-5": _T_3_15, "claude-sonnet-4-6": _T_3_15, "claude-sonnet-5": (2, 10, 2.5, 4, 0.2),
    "claude-opus-4-0": (15, 75, 18.75, 30, 1.5), "claude-opus-4-1": (15, 75, 18.75, 30, 1.5),
    "claude-opus-4-5": _T_5_25, "claude-opus-4-6": _T_5_25, "claude-opus-4-7": _T_5_25, "claude-opus-4-8": _T_5_25,
    "claude-opus-5": _T_5_25, "claude-opus-5-5": (4, 20, 5, 8, 0.2),
    "claude-fable-5": (10, 50, 12.5, 20, 1), "claude-fable-5-1": (10, 50, 12.5, 20, 0.25),
    "claude-mythos-5": (10, 50, 12.5, 20, 1), "claude-mythos-5-1": (10, 50, 12.5, 20, 0.25),
}
CLI_FAST_PRICES = {"claude-opus-5-5": (8, 40, 10, 16, 0.4), "claude-opus-5": (10, 50, 12.5, 20, 1),
                   "claude-opus-4-8": (10, 50, 12.5, 20, 1), "claude-opus-4-7": (30, 150, 37.5, 60, 3),
                   "claude-opus-4-6": (30, 150, 37.5, 60, 3)}
_PRICE_IDS = sorted(CLI_PRICES, key=len, reverse=True)   # the longest id first: claude-opus-5-5 before claude-opus-5


def call_cost(model, usage):
    """One API call's cost as Claude Code computes it, or None for a model it has no price for here."""
    m = str(model or "").replace("[1m]", "").replace("[2m]", "")
    cid = next((c for c in _PRICE_IDS if m == c or m.startswith(c + "-")), None)
    if cid is None or not isinstance(usage, dict):
        return None
    i, o, w5, w1, r = CLI_FAST_PRICES[cid] if usage.get("speed") == "fast" and cid in CLI_FAST_PRICES else CLI_PRICES[cid]
    n = lambda k: float(usage.get(k) or 0)
    w = n("cache_creation_input_tokens")
    w1h = min(float(((usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens")) or 0), w)
    usd = (n("input_tokens") * i + n("output_tokens") * o + n("cache_read_input_tokens") * r + w1h * w1 + (w - w1h) * w5) / 1e6
    if usage.get("inference_geo") == "us":
        usd *= 1.1
    return usd + float(((usage.get("server_tool_use") or {}).get("web_search_requests")) or 0) * 0.01


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def registry_all(state: Path) -> dict:
    """{sid: registry row} from sdk/<sid>.json."""
    out = {}
    for f in sorted((state / "sdk").glob("*.json")) if (state / "sdk").is_dir() else []:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):
            out[str(r.get("sid") or f.stem)] = r
    return out


def conversation_ids(state: Path, sid: str, regs: dict) -> set:
    """Every conversation id a session has written to: its own sid, the registry's lastSid and the resume lineage the
    kernel records in states/<sid>.jsonl (resumeFork from/to)."""
    ids = {sid} if sid else set()
    r = regs.get(sid) or {}
    if r.get("lastSid"):
        ids.add(str(r["lastSid"]))
    try:
        with open(state / "states" / (sid + ".jsonl"), "rb") as f:
            for ln in f:
                if b"resumeFork" not in ln:
                    continue
                try:
                    rf = json.loads(ln).get("resumeFork") or {}
                except ValueError:
                    continue
                ids.update(str(x) for x in (rf.get("from"), rf.get("to")) if x)
    except OSError:
        pass
    return ids


def transcript_files(claude: Path, state: Path, fsid: str, subagents: bool = True) -> list:
    """A conversation's transcript files: <claude>/projects/<any>/<fsid>.jsonl and, with `subagents`, its subagents'
    files, plus the copies a repair kept under the state's repair-backup-* directories (a split transcript's pieces)."""
    out = []
    for f in sorted((claude / "projects").glob("*/%s.jsonl" % fsid)):
        out.append(f)
        if subagents:
            out.extend(sorted((f.parent / fsid / "subagents").glob("*.jsonl")))
    for bk in sorted(state.glob("repair-backup-*")):
        for p in sorted(bk.glob("projects/*/%s*" % fsid)):
            if p.is_file():
                out.append(p)
            elif p.is_dir() and subagents:
                out.extend(sorted(x for x in p.rglob("*") if x.is_file() and ".jsonl" in x.name))
    return out


def cost_state_record(line: bytes):
    """A transcript line's cost-state record as {"total", "tok": [in, out, cache read, cache write]}, or None."""
    try:
        o = json.loads(line)
    except ValueError:
        return None
    if not isinstance(o, dict) or o.get("type") != "cost-state":
        return None
    total = o.get("totalCostUSD")
    if not isinstance(total, (int, float)) or not (0 <= total < float("inf")):
        return None
    tok = [0, 0, 0, 0]
    for m in (o.get("modelUsage") or {}).values() if isinstance(o.get("modelUsage"), dict) else []:
        if isinstance(m, dict):
            for i, k in enumerate(_MU_KEYS):
                v = m.get(k)
                tok[i] += int(v) if isinstance(v, (int, float)) else 0
    return {"total": float(total), "tok": tok}


_RECORDS, _CALLS = {}, {}


def transcript_records(path: Path) -> list:
    """Every cost-state record in one file, oldest first; read once per run."""
    key = str(path)
    if key not in _RECORDS:
        out = []
        try:
            with open(path, "rb") as f:
                for ln in f:
                    if b'"cost-state"' in ln:
                        rec = cost_state_record(ln)
                        if rec is not None:
                            out.append(rec)
        except OSError:
            pass
        _RECORDS[key] = out
    return _RECORDS[key]


def transcript_calls(path: Path) -> dict:
    """{message id: (epoch, cost or None, model, [4 tokens])} for one file's API calls; read once per run. A call streamed
    as several records is one id, and the record with the fullest usage stands."""
    key = str(path)
    if key in _CALLS:
        return _CALLS[key]
    calls = {}
    try:
        with open(path, "rb") as f:
            for ln in f:
                if b'"usage"' not in ln or b'"assistant"' not in ln:
                    continue
                try:
                    d = json.loads(ln)
                except ValueError:
                    continue
                m = d.get("message") if isinstance(d, dict) and d.get("type") == "assistant" else None
                u = m.get("usage") if isinstance(m, dict) else None
                if not isinstance(u, dict) or not m.get("id") or m.get("model") == "<synthetic>":
                    continue
                try:
                    ts = datetime.fromisoformat(str(d.get("timestamp") or "").replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                tok = [int(u.get(k) or 0) for k in _USAGE_KEYS]
                prev = calls.get(m["id"])
                if prev is None or sum(tok) > sum(prev[3]):
                    calls[m["id"]] = (min(ts, prev[0]) if prev else ts, call_cost(m.get("model"), u), str(m.get("model") or ""), tok)
                elif ts < prev[0]:
                    calls[m["id"]] = (ts,) + prev[1:]
    except OSError:
        pass
    _CALLS[key] = calls
    return calls


def restored_plan(turns: list, state: Path, claude: Path, regs: dict = None) -> dict:
    """The rows rule 6 corrects: {"rows": [...], "unverified": [...]} (see the section note). A row this rule corrected
    already (repairRule 6) is passed over, so a second run finds nothing."""
    regs = registry_all(state) if regs is None else regs
    rows, unverified = [], []
    conv = {}

    def ids(sid):
        if sid not in conv:
            conv[sid] = conversation_ids(state, sid, regs)
        return conv[sid]

    for r in turns:
        if r.get("spendBaseline") != "fresh" or r.get("repairRule") == REPAIR_RULE_RESTORED:
            continue
        usd = r.get("usd")
        if not isinstance(usd, (int, float)) or usd <= 0 or not isinstance(r.get("t"), (int, float)):
            continue
        sid = str(r.get("sid") or "")
        reg = regs.get(sid) or {}
        ff = reg.get("forkedFrom") if isinstance(reg.get("forkedFrom"), dict) else {}
        parent = str(ff.get("sid") or reg.get("threadOf") or "")   # a session born as a fork loaded its parent's conversation
        own = ids(sid)
        loaded = own | (ids(parent) if parent else set())
        rtok = [int(r.get(k) or 0) for k in TOKS]
        cands = []
        for fsid in sorted(loaded):
            for f in transcript_files(claude, state, fsid, subagents=False):
                for rec in transcript_records(f):
                    if MIN_RESTORED <= rec["total"] <= usd + MATCH_ABS and all(a >= b for a, b in zip(rtok, rec["tok"])):
                        cands.append(dict(rec, source="its own transcript" if fsid in own else "its parent's transcript"))
        if not cands:
            continue
        t1 = float(r.get("resultT") or r["t"])
        t0 = float(r["fedT"]) if isinstance(r.get("fedT"), (int, float)) else t1 - float(r.get("durationMs") or 0) / 1000.0
        calls = {}
        for fsid in sorted(own):
            for f in transcript_files(claude, state, fsid):
                for mid, c in transcript_calls(f).items():
                    if t0 - WINDOW_PAD <= c[0] <= t1 + WINDOW_PAD and (mid not in calls or sum(c[3]) > sum(calls[mid][3])):
                        calls[mid] = c
        name = str(r.get("name") or sid[:8])
        base = {"sid": sid, "owner": str(reg.get("threadOf") or sid), "parent": parent, "name": name, "t": int(r["t"]),
                "day": local_day(r["t"]), "hour": local_hour(r["t"]), "recorded": round(float(usd), 6),
                "launchedLogin": str(reg.get("launchedLogin") or "")}
        unpriced = sorted({c[2] for c in calls.values() if c[1] is None})
        if unpriced:
            unverified.append(dict(base, why="its calls include a model with no price here (%s)" % ", ".join(unpriced)))
            continue
        own_usd = sum(c[1] for c in calls.values())
        gap = float(usd) - own_usd
        best = min(cands, key=lambda c: abs(gap - c["total"]))
        if abs(gap - best["total"]) <= MATCH_ABS + MATCH_REL * own_usd:
            rows.append(dict(base, corrected=max(0.0, round(float(usd) - best["total"], 6)), dusd=round(best["total"], 6),
                             dtok=list(best["tok"]), tokRecorded=rtok, tokCorrected=[a - b for a, b in zip(rtok, best["tok"])],
                             ownCalls=round(own_usd, 6), calls=len(calls), source=best["source"]))
        elif gap > max(1.0, MATCH_ABS + MATCH_REL * own_usd):
            unverified.append(dict(base, why="%.2f above its own %d call(s) (%.2f), and no record in the transcripts it loaded "
                                             "holds that figure (nearest %.2f)" % (gap, len(calls), own_usd, best["total"])))
    rows.sort(key=lambda c: c["t"])
    return {"rows": rows, "unverified": unverified}


def _usd_in(b, field, key):
    e = (b.get(field) or {}).get(key) if isinstance(b, dict) and isinstance(b.get(field), dict) else None
    return float(e.get("usd") or 0) if isinstance(e, dict) else 0.0


def decide_billing(spend: dict, rows: list) -> list:
    """Each row's billed session and stored login, read off the buckets the kernel folded it into (a turn row names
    neither), in time order, each row's dollars taken out of a copy before the next is judged. The day bucket decides,
    with the hour's too where the ledger still keeps it (it keeps 800 hours, fewer than its days).
    Billed session: the kernel billed a comment thread's turns to its owner (threadOf, as the thread stood then; a
    thread promoted since bills itself now), so the candidates are the registry's threadOf, the session it was forked
    from and the row's own; the one whose bySid share holds the row, the registry's current word when several do.
    Login: a login is the row's when its byLogin share holds the row and the remainder (the machine's own login and the
    key, never a byLogin row) cannot; the remainder alone, when it alone can; between several, the registry's launch
    login when it is one of them; else none, said."""
    sim = json.loads(json.dumps(spend or {}))
    for c in rows:
        bs = [b for b in ((sim.get("days") or {}).get(c["day"]), (sim.get("hours") or {}).get(c["hour"])) if isinstance(b, dict)]
        need = c["dusd"] - 1e-6
        if not bs or not isinstance((sim.get("days") or {}).get(c["day"]), dict):
            c["login"], c["loginWhy"] = "", "no bucket for its day"
            sim, _ = fold_restored(sim, [c])
            continue
        owners = []
        for o in (c["owner"], c.get("parent") or "", c["sid"]):
            if o and o not in owners:
                owners.append(o)
        fit = [o for o in owners if all(_usd_in(b, "bySid", o) >= need for b in bs)]
        if fit and c["owner"] not in fit:
            c["owner"] = fit[0]
        logins = set.intersection(*[set(b.get("byLogin") or {}) if isinstance(b.get("byLogin"), dict) else set() for b in bs])
        fits = sorted(L for L in logins if all(_usd_in(b, "byLogin", L) >= need for b in bs))
        rest = all(float(b.get("usd") or 0) - sum(_usd_in(b, "byLogin", L) for L in (b.get("byLogin") or {})) >= need for b in bs)
        if len(fits) == 1 and not rest:
            c["login"], c["loginWhy"] = fits[0], "the only login whose share holds it"
        elif not fits:
            c["login"], c["loginWhy"] = "", ("no byLogin split in its buckets" if not logins
                                             else "the machine's own login or the key" if rest else "no share of the buckets holds it")
        elif c.get("launchedLogin") in fits:
            c["login"], c["loginWhy"] = c["launchedLogin"], "several shares hold it; the registry's launch login"
        else:
            c["login"], c["loginWhy"] = "", "several shares hold it and the registry names none of them: byLogin left as recorded"
        sim, _ = fold_restored(sim, [c])
    return rows


def fold_restored(spend: dict, rows: list) -> tuple:
    """spend.json less each row's restored record: the hour and day buckets' usd and four token columns, the bySid entry of
    the session the row bills and the byLogin entry of its login (usd and tok); the `key` split and turn counts stay.
    Returns (spend, notes); a figure that would go below zero is held at zero and said."""
    out = json.loads(json.dumps(spend or {}))
    notes = []
    gone = 0

    def sub(d, k, v, where, integer=False):
        x = float(d.get(k) or 0) - v
        if x < -1e-6:
            notes.append("%s %s would go %s below zero; held at zero" % (where, k, ("%d" % -x) if integer else ("%.4f" % -x)))
            x = 0.0
        d[k] = int(round(x)) if integer else round(x, 6)

    for c in rows:
        tok = sum(c["dtok"])
        for where, b in (("hour %s" % c["hour"], (out.get("hours") or {}).get(c["hour"])),
                         ("day %s" % c["day"], (out.get("days") or {}).get(c["day"]))):
            if not isinstance(b, dict):
                if where.startswith("hour"):
                    gone += 1                          # the ledger keeps 800 hours, fewer than its days: said once, below
                else:
                    notes.append("no bucket for %s (%s)" % (where, c["name"]))
                continue
            sub(b, "usd", c["dusd"], where)
            for k, v in zip(TOKS, c["dtok"]):
                sub(b, k, v, where, integer=True)
            by = b.get("bySid")
            if isinstance(by, dict) and isinstance(by.get(c["owner"]), dict):
                sub(by[c["owner"]], "usd", c["dusd"], where + " bySid " + c["owner"][:8])
                sub(by[c["owner"]], "tok", tok, where + " bySid " + c["owner"][:8], integer=True)
            elif isinstance(by, dict):
                notes.append("%s: no bySid entry for %s" % (where, c["owner"][:8]))
            bl = b.get("byLogin")
            if c.get("login"):
                if isinstance(bl, dict) and isinstance(bl.get(c["login"]), dict):
                    sub(bl[c["login"]], "usd", c["dusd"], where + " byLogin " + c["login"])
                    sub(bl[c["login"]], "tok", tok, where + " byLogin " + c["login"], integer=True)
                else:
                    notes.append("%s: no byLogin entry for %s" % (where, c["login"]))
    if gone:
        notes.append("%d row(s) fall in hours the ledger no longer keeps (it keeps 800 hours, fewer than its days): their "
                     "day buckets are corrected" % gone)
    return out, notes


def apply_restored_to_turns(path: Path, rows: list, write: bool = True) -> list:
    """turns.jsonl with each planned row corrected: usd and the four token columns to the corrected figures, the kernel's
    kept as usdRecorded and tokRecorded, the record taken out as restoredCostUsd, stamped repairRule 6. A row is matched
    by sid, second and the figure it holds, and only while it is an uncorrected fresh-baseline row."""
    want = {(c["sid"], c["t"], c["recorded"]): c for c in rows}

    def stamp(o):
        if o.get("spendBaseline") != "fresh" or o.get("repairRule") == REPAIR_RULE_RESTORED:
            return None
        key = (str(o.get("sid") or ""), int(o["t"]) if isinstance(o.get("t"), (int, float)) else None,
               round(float(o["usd"]), 6) if isinstance(o.get("usd"), (int, float)) else None)
        c = want.pop(key, None)
        if c is not None:
            o["usdRecorded"] = o["usd"]
            o["tokRecorded"] = [int(o.get(k) or 0) for k in TOKS]
            o["usd"] = c["corrected"]
            for k, v in zip(TOKS, c["tokCorrected"]):
                o[k] = int(v)
            o["restoredCostUsd"] = c["dusd"]
            o["repairedT"] = int(time.time())
            o["repairRule"] = REPAIR_RULE_RESTORED
        return c

    return _rewrite_turns(path, stamp, write)


def restored_recovery(state: Path, spend: dict, turns: list, skip_ref=None) -> tuple:
    """The deltas an earlier --apply journaled whose bucket fold never completed, folded now for the rows it did rewrite
    (a row stamped repairRule 6 at the journaled figure); a row still uncorrected folds nothing, and the plan finds it
    again. Returns (spend, refs folded, deltas folded, notes)."""
    held = {(str(r.get("sid") or ""), int(r["t"])): r for r in turns if isinstance(r.get("t"), (int, float))}
    refs, n, notes = [], 0, []
    for o in journal_pending(state, spend, name=RESTORED_JOURNAL):
        if o.get("t") == skip_ref:
            continue
        take = []
        for d in o["deltas"]:
            r = held.get((str(d.get("sid") or ""), int(d["t"])))
            if r is not None and r.get("repairRule") == REPAIR_RULE_RESTORED and isinstance(r.get("usd"), (int, float)) \
                    and abs(float(r["usd"]) - float(d["corrected"])) < 1e-6:
                take.append(d)
        spend, got = fold_restored(spend, take)
        notes += got
        n += len(take)
        refs.append(o.get("t"))
    return spend, refs, n, notes


def restored_report(p: dict, before: dict, after: dict, notes: list) -> str:
    rows = p["rows"]
    lines = ["spend repair, rule 6: %d row(s) recorded a restored cost-state record that the resume's seed missed" % len(rows)]
    days = {}
    for c in rows:
        d = days.setdefault(c["day"], [0, 0.0, [0, 0, 0, 0]])
        d[0] += 1; d[1] += c["dusd"]
        d[2] = [a + b for a, b in zip(d[2], c["dtok"])]
    if days:
        lines += ["", "per day, subtracted (dollars; tokens in, out, cache read, cache write):"]
        for day, (n, usd, tok) in sorted(days.items()):
            lines.append("  %s  %3d row(s)  -%12.2f   in -%d  out -%d  cache read -%d  cache write -%d" % (day, n, usd, *tok))
    months = sorted({d[:7] for d in list((before.get("days") or {})) if any(c["day"][:7] == d[:7] for c in rows)})
    if months:
        lines += ["", "per month, the ledger's day buckets (dollars before -> after):"]
        for m in months:
            b = sum(float(v.get("usd") or 0) for k, v in (before.get("days") or {}).items() if k[:7] == m and isinstance(v, dict))
            a = sum(float(v.get("usd") or 0) for k, v in (after.get("days") or {}).items() if k[:7] == m and isinstance(v, dict))
            lines.append("  %s  %12.2f -> %12.2f" % (m, b, a))
    if rows:
        lines += ["", "rows (session, time, recorded -> corrected, the record restored, billed to, login):"]
        for c in rows:
            lines.append("  %-24s %s %10.2f -> %8.2f  $%.2f in %s; its own %d call(s) $%.2f; billed to %s; login %s (%s)" % (
                c["name"][:24], datetime.fromtimestamp(c["t"]).strftime("%Y-%m-%d %H:%M:%S"), c["recorded"], c["corrected"],
                c["dusd"], c["source"], c["calls"], c["ownCalls"], c["owner"][:8], c.get("login") or "-", c.get("loginWhy") or ""))
    if p["unverified"]:
        lines += ["", "fresh-baseline rows a record could explain but the evidence does not confirm (left as recorded):"]
        for c in p["unverified"]:
            lines.append("  %-24s %s %10.2f  %s" % (c["name"][:24], datetime.fromtimestamp(c["t"]).strftime("%Y-%m-%d %H:%M:%S"),
                                                   c["recorded"], c["why"]))
    if notes:
        lines.append("")
        lines.extend("note: " + n for n in notes)
    return "\n".join(lines)


def restored_main(a, state: Path) -> int:
    """`romp spend-repair --restored-cost-state [--apply]`: rule 6 over every row of the turn ledger."""
    claude = Path(a.claude_dir) if a.claude_dir else claude_dir()
    bad = []
    turns = read_jsonl(state / "turns.jsonl", bad)
    spend_text = read_text(state / "spend.json")
    try:
        spend = parse_spend(spend_text)
    except ValueError as e:
        sys.stderr.write("romp spend-repair: %s does not parse (%s); refusing to run on it. Nothing written.\n" % (state / "spend.json", e))
        return 2
    jbad = []
    pending = journal_pending(state, spend, jbad, name=RESTORED_JOURNAL)
    if jbad:
        sys.stderr.write("romp spend-repair: %d unparseable line(s) in %s (a torn write); --apply is refused until the journal "
                         "is whole\n" % (len(jbad), state / RESTORED_JOURNAL))
        if a.apply:
            return 2
    p = restored_plan(turns, state, claude)
    decide_billing(spend, p["rows"])
    preview, notes = fold_restored(spend, p["rows"])
    if bad:
        sys.stdout.write("%d unparseable line(s) in %s skipped (a torn append)\n" % (len(bad), ", ".join(sorted(set(bad)))))
    if a.json:
        sys.stdout.write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(restored_report(p, spend, preview, notes) + "\n")
    if pending:
        sys.stdout.write("%d journaled run(s) did not finish their bucket write: --apply folds them first\n" % len(pending))
    if not a.apply:
        sys.stdout.write("\ndry run: nothing written (pass --apply to write spend.json and turns.jsonl)\n")
        return 0
    if not p["rows"] and not pending:
        sys.stdout.write("\nnothing to apply\n")
        return 0
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if not a.no_backup:
        for name in ("spend.json", "turns.jsonl", "turns.jsonl.1"):
            src = state / name
            if src.exists():
                shutil.copy2(src, src.with_name("%s.bak-%s" % (name, stamp)))
        sys.stdout.write("\nbackups: spend.json.bak-%s, turns.jsonl.bak-%s (beside the files)\n" % (stamp, stamp))
    # the order the staircase rule keeps: the deltas of the rows the rewrite will find are journaled first, the rows are
    # rewritten, then spend.json is read again and the deltas folded on what is there, the completion marked inside it
    planned = apply_restored_to_turns(state / "turns.jsonl", p["rows"], write=False)
    stamp_t = time.time()
    keep = ("sid", "owner", "name", "t", "day", "hour", "recorded", "corrected", "dusd", "dtok", "login")   # owner and
    #                                                                                     login as decided, so a recovery folds the same
    if planned:
        journal_append(state, {"t": stamp_t, "phase": "rows", "rule": REPAIR_RULE_RESTORED,
                               "deltas": [{k: c.get(k) for k in keep} for c in planned]}, name=RESTORED_JOURNAL)
    done = apply_restored_to_turns(state / "turns.jsonl", p["rows"])
    sp = state / "spend.json"
    fresh_text = read_text(sp)
    try:
        base = parse_spend(fresh_text)
    except ValueError as e:
        sys.stderr.write("romp spend-repair: spend.json stopped parsing between the plan and the write (%s); the rows were "
                         "rewritten, the buckets were not: their deltas are journaled and fold on the next run\n" % e)
        return 2
    if fresh_text != spend_text:
        sys.stdout.write("spend.json moved since the plan's read (a result folded meanwhile): the fold is made on the file as it stands\n")
    # the earlier runs' deltas are judged on the rows as the plan read them, BEFORE this run's rewrite: a row this run
    # stamped answers this run's entry, and judged on the rewritten file it would answer an earlier entry too (twice)
    base, refs, n_rec, rec_notes = restored_recovery(state, base, turns, skip_ref=stamp_t)
    if refs:
        sys.stdout.write("%d delta(s) from %d earlier run(s) whose bucket write did not complete were folded now\n" % (n_rec, len(refs)))
    new_spend, fold_notes = fold_restored(base, done)
    for n in rec_notes + fold_notes:
        if n not in notes:
            sys.stdout.write("note: %s\n" % n)
    refs = refs + ([stamp_t] if planned else [])
    mark_folded(new_spend, refs)
    tmp = sp.with_name("spend.json.repair.tmp")
    tmp.write_text(json.dumps(new_spend), encoding="utf-8")
    os.replace(tmp, sp)
    for r in refs:
        journal_append(state, {"t": time.time(), "phase": "buckets", "ref": r}, name=RESTORED_JOURNAL)
    journal_compact(state, new_spend, name=RESTORED_JOURNAL)
    sys.stdout.write("\napplied: %d turn row(s) corrected (usdRecorded and tokRecorded keep the kernel's figures), then "
                     "spend.json rewritten for those\n" % len(done))
    if len(done) < len(p["rows"]):
        sys.stdout.write("%d planned row(s) were not in turns.jsonl as planned (the ledger moved): left as they are, judged "
                         "afresh next run\n" % (len(p["rows"]) - len(done)))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="romp spend-repair",
                                 description="recompute a day's spend from the turn ledger's re-attach staircase (T354); a dry run unless --apply")
    ap.add_argument("--day", help="the local date to repair (default: today)")
    ap.add_argument("--apply", action="store_true", help="write the corrected spend.json and turns.jsonl (default: print only)")
    ap.add_argument("--state", help="a state directory other than this machine's")
    ap.add_argument("--since", help="the instant the per-session hosts came on (ISO, Z or local; or epoch seconds): a first result "
                    "after a restart before it is a fresh process's turn, never a step; default: the whole day")
    ap.add_argument("--json", action="store_true", help="the plan as JSON")
    ap.add_argument("--no-backup", action="store_true", help="--apply without the spend.json.bak-<stamp> and turns.jsonl.bak-<stamp> copies")
    ap.add_argument("--restored-cost-state", action="store_true",
                    help="rule 6 over the whole turn ledger instead of a day's staircase: first results that recorded a "
                    "restored cost-state record the resume's seed missed (fixed by #162), checked against the transcripts")
    ap.add_argument("--claude-dir", help="with --restored-cost-state: the Claude Code config directory whose projects/ "
                    "holds the transcripts (default: $CLAUDE_CONFIG_DIR, else ~/.claude)")
    a = ap.parse_args(argv)
    state = Path(a.state) if a.state else state_dir()
    if a.restored_cost_state:
        return restored_main(a, state)
    day = a.day or local_day(time.time())
    try:
        datetime.strptime(day, "%Y-%m-%d")
    except ValueError:
        sys.stderr.write("romp spend-repair: bad date %r (YYYY-MM-DD)\n" % day)
        return 2
    try:
        since = parse_since(a.since or "")
    except ValueError:
        sys.stderr.write("romp spend-repair: bad --since %r (ISO instant or epoch seconds)\n" % a.since)
        return 2
    bad = []
    turns = read_jsonl(state / "turns.jsonl", bad)
    restarts = restart_instants(read_jsonl(state / "restart-cuts.jsonl"))
    owners, keyed = registry_maps(state)
    spend_text = read_text(state / "spend.json")
    try:
        spend = parse_spend(spend_text)
    except ValueError as e:
        # a ledger that does not parse is refused, never overwritten (M3 of the review: an empty ledger written over
        # 90 days of buckets with exit 0 was the alternative)
        sys.stderr.write("romp spend-repair: %s does not parse (%s); refusing to run on it. Nothing written.\n" % (state / "spend.json", e))
        return 2
    p = plan(turns, restarts, day, since, owners, keyed)
    if bad:
        sys.stdout.write("%d unparseable line(s) in %s skipped (a torn append)\n" % (len(bad), ", ".join(sorted(set(bad)))))
    new_spend = apply_to_spend(spend, p)      # computed either way, for the notes; written only with --apply
    if a.json:
        sys.stdout.write(json.dumps(p, indent=1, sort_keys=True) + "\n")
    else:
        sys.stdout.write(report(p) + "\n")
    jbad = []
    pending = journal_pending(state, spend, jbad)
    if jbad:
        sys.stderr.write("romp spend-repair: %d unparseable line(s) in %s (a torn write); a pending fold may hide in them, so "
                         "--apply is refused until the journal is whole\n" % (len(jbad), state / REPAIR_JOURNAL))
        if a.apply:
            return 2
    if pending:
        sys.stdout.write("%d delta(s) from %d earlier run(s) are journaled with their bucket write incomplete: --apply folds them first\n"
                         % (sum(len(o["deltas"]) for o in pending), len(pending)))
        # the preview (round six of the review): the recovery fold run in memory on the ledger as read, its bucket
        # moves and its notes, so the operator sees the figures an --apply would produce, clamps included
        preview = json.loads(json.dumps(spend))
        for o in pending:
            preview, _m, _s, notes = fold_deltas(preview, o["deltas"], str(o.get("day") or day), turns)
            for n in notes:
                sys.stdout.write("  recovery fold would say: %s\n" % n)
        for h in sorted(set(k for o in pending for d_ in o["deltas"] for k in [d_.get("hour")]) - {None}):
            before = float(((spend.get("hours") or {}).get(h) or {}).get("usd") or 0)
            after_ = float(((preview.get("hours") or {}).get(h) or {}).get("usd") or 0)
            sys.stdout.write("  recovery fold would move hour %s: %.2f -> %.2f\n" % (h, before, after_))
        # and the plan's own fold on that recovered copy (round seven of the review): the report's hour table comes from
        # the turn rows and the plan's fold above ran on the ledger as read, so a clamp that arises only on the recovered
        # figures showed nowhere before an --apply landed it
        p_on = dict(p, rows=list(p["rows"]))
        planned_on = apply_to_spend(preview, p_on)
        for n in p_on.get("notes") or []:
            if "below zero" in n:
                sys.stdout.write("  plan fold on the recovered ledger would say: %s\n" % n)
        for h in sorted({c["hour"] for c in p["rows"]}):
            b_ = float(((preview.get("hours") or {}).get(h) or {}).get("usd") or 0)
            a_ = float(((planned_on.get("hours") or {}).get(h) or {}).get("usd") or 0)
            if abs(a_ - b_) > 1e-9:
                sys.stdout.write("  plan fold on the recovered ledger would move hour %s: %.2f -> %.2f\n" % (h, b_, a_))
    if not a.apply:
        sys.stdout.write("\ndry run: nothing written (pass --apply to write spend.json and turns.jsonl)\n")
        return 0
    if not p["rows"] and not pending:
        sys.stdout.write("\nnothing to apply\n")
        return 0
    sp = state / "spend.json"
    # the kernel may be running and folding results as this runs (the same race bin/romp-spend-rebuild states): the
    # ledger is read again right before the write and the fold recomputed on what is there now, so a turn folded
    # since the plan's read is kept; the window left is the replace itself. The copies beside the files are the
    # undo: spend.json.bak-<stamp> and turns.jsonl.bak-<stamp>.
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    if not a.no_backup:
        for name in ("spend.json", "turns.jsonl"):
            src = state / name
            if src.exists():
                shutil.copy2(src, src.with_name("%s.bak-%s" % (name, stamp)))
        sys.stdout.write("\nbackups: spend.json.bak-%s, turns.jsonl.bak-%s (beside the files)\n" % (stamp, stamp))
    # turns.jsonl FIRST, then the buckets for the rows actually rewritten (low c of the review): a planned row the
    # file no longer holds as planned (the ledger moved) is left alone in both places, said below, and the next run
    # judges it afresh; before this the buckets moved for every planned row and a row the rewrite missed had its
    # delta folded again on the next run. The deltas are journaled between the two writes (low D): a failure there
    # leaves rows corrected and buckets unfolded, and the next run folds the journaled deltas first
    # the rows the rewrite will find as planned are journaled BEFORE the rewrite (round five of the review): a death
    # between the rows replace and a journal append left rows corrected, buckets unfolded and nothing for a later run to
    # fold; an entry ahead of a rewrite that never happens is harmless (the recovery folds present less former: zero)
    planned = apply_to_turns(state / "turns.jsonl", p, write=False)
    stamp_t = time.time()
    deltas = [{"sid": c["sid"], "t": c["t"], "hour": c["hour"], "owner": c["owner"], "keyed": c["keyed"], "name": c["name"],
               "delta": round(c["corrected"] - c["current"], 6), "corrected": round(c["corrected"], 6),
               "rule": (0 if c.get("restore") else int(c.get("rule") or REPAIR_RULE)), "reason": c.get("reason", "")} for c in planned]
    if deltas:
        journal_append(state, {"t": stamp_t, "phase": "rows", "day": day, "deltas": deltas})
    done = apply_to_turns(state / "turns.jsonl", p)
    missed = len(p["rows"]) - len(done)
    fresh_text = read_text(sp)
    try:
        base = parse_spend(fresh_text)
    except ValueError as e:
        sys.stderr.write("romp spend-repair: spend.json stopped parsing between the plan and the write (%s); the rows were "
                         "rewritten, the buckets were not: their deltas are journaled and fold on the next run once it parses\n" % e)
        return 2
    if fresh_text != spend_text:
        sys.stdout.write("spend.json moved since the plan's read (a result folded meanwhile): the fold was recomputed on the file as it stands\n")
    pending = [o for o in journal_pending(state, base, []) if o.get("t") != stamp_t]   # against the ledger as it stands now,
    #                                                                                    this run's own entry excluded (folded below)
    if pending:
        n_folded = n_moved = n_skipped = 0
        for o in pending:
            base, moved, skipped, notes = fold_deltas(base, o["deltas"], str(o.get("day") or day), turns)   # the rows as the plan read them
            n_folded += len(o["deltas"]) - skipped; n_moved += moved; n_skipped += skipped
            for n in notes:
                sys.stdout.write("note (recovery fold): %s\n" % n)         # a clamp inside the recovery is said too (round five)
        sys.stdout.write("%d delta(s) from %d earlier run(s) whose bucket write did not complete were folded now%s%s\n"
                         % (n_folded, len(pending),
                            "; %d against the row's present figure, which moved since" % n_moved if n_moved else "",
                            "; %d skipped, their rows are gone" % n_skipped if n_skipped else ""))
    p2 = dict(p, rows=done)
    said = set(p.get("notes") or [])                  # the report's own note lines, printed above
    new_spend = apply_to_spend(base, p2)
    for n in p2.get("notes") or []:                   # the fold ran on the ledger as it stands, after any recovery fold and on
        if "below zero" in n and n not in said:       # the rows found: a clamp that is a NEW line is said (round six: a text gate
            sys.stdout.write("note: %s\n" % n)       # on spend.json missed a base the recovery fold had moved in memory)
    refs = [o["t"] for o in pending] + ([stamp_t] if deltas else [])
    mark_folded(new_spend, refs)                      # the completion rides the same write
    tmp = sp.with_name("spend.json.repair.tmp")
    tmp.write_text(json.dumps(new_spend), encoding="utf-8")
    os.replace(tmp, sp)
    for r in refs:
        journal_append(state, {"t": time.time(), "phase": "buckets", "ref": r})   # the belt: a ledger rebuilt without its refs still finds the mark here
    dropped = journal_compact(state, new_spend)       # folded entries and their marks leave the journal; only pending ones stay
    n_restore = sum(1 for c in done if c.get("restore"))
    sys.stdout.write("\napplied: %d turn row(s) corrected (usdRecorded keeps the old figure)%s, then spend.json rewritten for those\n"
                     % (len(done) - n_restore, ", %d restored to the kernel's figure" % n_restore if n_restore else ""))
    if missed:
        sys.stdout.write("%d planned row(s) were not in turns.jsonl as planned (the ledger moved): left as they are in both files, judged afresh next run\n" % missed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
