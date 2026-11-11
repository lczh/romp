#!/usr/bin/env python3
"""Which models Claude Code runs fast mode on, learned from the CLI itself (2026-11-11).

Fast mode began as an Opus-only research preview, and every consumer of the fact — the judges' per-tier gate
(judge.fast_capable), the Always fast switch (sdk_backend.fast_capable), the chat's fast badge and the comment
dialog's fast chip — carried the rule "the id names Opus". The CLI moved: 2.1.321 runs fast mode on Fable 5.2 and
Anthropic retired it for Opus 4.8, so a hard-coded family rule offered a dead toggle on one model and hid a live one
on another. The CLI is the authoritative source for what it serves, and it says so at every connect: the initialize
response (the SDK's get_server_info) carries `models`, a list of option dicts {value (an alias like "default",
"opus", "fable" or a version id), resolvedModel (the version id), displayName, supportsFastMode (true, or absent
when not supported), …}, beside the session's own fast_mode_state. This module keeps what the CLI said in ONE table,
durable under the state dir (STATE/model-fast.json: {id: {"fast": bool, "t": epoch, "cli": version}}), and answers
"does the CLI run fast mode on this model?" for every consumer from it — bool when the CLI has said, None when it
has not, so each consumer keeps its own documented seed (today's Opus rule) until the first report. Keyed by BOTH an
option's alias and its resolved id, so "opus" and "claude-opus-5-5" each answer from their own entry.

Loaded by the judge module (for the kernel, the judges and the gear) and by the SDK backend (the writer, and the
Always fast switch's reader) through load_source, the cli_prices idiom: the backend does not import the judges, and
the judges do not import the backend. Event-based on both sides: written when a connect-time initialize answer or a
per-turn init arrives (never a timer), write-on-change only; read fresh per call behind an mtime cache."""
import json
import os
import re
import threading
import time
from pathlib import Path

MODEL_FAST_FILE = "model-fast.json"

_LOCK = threading.Lock()        # one read-modify-write at a time: sessions connect on threads of their own
_CACHE = {}                     # str(path) -> ((mtime_ns, size), table): a stat per read, a parse per change

# a first-party version id, with an optional dated snapshot tail: claude-opus-4-5, claude-fable-5-2-20261101
_VERSION_ID_RE = re.compile(r"^claude-([a-z]+)-(\d+)(?:-(\d+))?(?:-(\d{8}))?$")
# the CLI's display spelling, as the badge wears it: "Opus 5", "Fable 5.2"
_PRETTY_RE = re.compile(r"^([a-z]+) (\d+)(?:\.(\d+))?$")
# a Claude Code version as the CLI reports it (claude_code_version: "2.1.321"), the x.y.z it opens with
_CLI_VERSION_RE = re.compile(r"^\s*(\d+)\.(\d+)\.(\d+)")


def _path(state_dir):
    return Path(state_dir) / MODEL_FAST_FILE


def read_table(state_dir) -> dict:
    """{id: {"fast": bool, "t": int, "cli": str}} as the file holds it; {} when it is absent or unreadable (a fresh
    install, a hand edit) — the consumers then fall to their seed. Cached on the file's stat, so a write by the
    backend's thread is read by the kernel's next call."""
    if not state_dir:
        return {}
    p = _path(state_dir)
    try:
        st = os.stat(p)
    except OSError:
        return {}
    key = (st.st_mtime_ns, st.st_size)
    c = _CACHE.get(str(p))
    if c and c[0] == key:
        return c[1]
    try:
        d = json.loads(p.read_text())
    except (OSError, ValueError):
        d = {}
    table = {str(k): v for k, v in d.items() if isinstance(v, dict) and isinstance(v.get("fast"), bool)} \
        if isinstance(d, dict) else {}
    _CACHE[str(p)] = (key, table)
    return table


def keys_for(model) -> list:
    """The table keys a spelling of `model` is looked up under, most exact first: the id as given (lower-cased,
    stripped); the id without a trailing [1m]-style context tag or a Vertex @date; a provider-prefixed id by its
    first-party part (us.anthropic.claude-opus-4-6-… → claude-opus-4-6); a dated snapshot by its dateless alias
    (claude-opus-4-5-20251101 → claude-opus-4-5); a pretty name by its id ("Fable 5.2" → claude-fable-5-2). A
    family alias ("opus", "fable") and "default" are keys of their own. NEVER a version id's family: the alias
    names the family's current head, which may run fast where an older pinned version does not."""
    s = str(model or "").strip().lower()
    if not s:
        return []
    out = [s]
    bare = re.sub(r"[\[@].*$", "", s).strip()           # claude-opus-4-6[1m], claude-opus-4-6@20260101
    if bare and bare not in out:
        out.append(bare)
    i = bare.find("claude-")
    if i > 0:                                            # us.anthropic.claude-opus-4-6-v1:0 → the first-party part
        fp = bare[i:]
        m = re.match(r"^(claude-[a-z]+-\d+(?:-\d+)?(?:-\d{8})?)", fp)
        if m and m.group(1) not in out:
            out.append(m.group(1))
    for cand in list(out):
        m = _VERSION_ID_RE.match(cand)
        if m and m.group(4):                             # a dated snapshot: the dateless alias follows the version
            dateless = "claude-%s-%s" % (m.group(1), m.group(2)) + ("-%s" % m.group(3) if m.group(3) else "")
            if dateless not in out:
                out.append(dateless)
    m = _PRETTY_RE.match(bare)
    if m:                                                # "Fable 5.2" → claude-fable-5-2
        vid = "claude-%s-%s" % (m.group(1), m.group(2)) + ("-%s" % m.group(3) if m.group(3) else "")
        if vid not in out:
            out.append(vid)
    return out


def fast_known(model, state_dir):
    """What the CLI has said about `model`: True when a report claimed fast mode for it, False when a report listed it
    without the claim (or a flagged connect on it never ran fast), None when no report named it — the caller's seed
    applies then. The first key of keys_for with an entry answers."""
    table = read_table(state_dir)
    if not table:
        return None
    for k in keys_for(model):
        rec = table.get(k)
        if rec is not None:
            return bool(rec["fast"])
    return None


def support_from_payload(d) -> dict:
    """{id: bool} from a CLI payload's `models` options (the initialize response; harmless on a payload without
    them): each option's alias (`value`) AND its resolved version id (`resolvedModel`) map to whether the option
    claims supportsFastMode — true → True; absent, None or false → False (the CLI did not claim it). Both keys
    carry the same answer: the CLI resolved the alias to that id at this report. {} when NO option carries the
    field at all: the CLI emits it only when true (its options builder, 2.1.322), so a list without it on any option
    is a CLI that predates the field as readily as an account none of whose models runs fast, and the two cannot be
    told apart — such a list teaches nothing and the consumers' seed stands, rather than every id learned as "no"
    from a silence (the rule _adopt_fast_state keeps for the sibling fields: an absent field leaves the last truth
    standing; the review of 2026-11-11, where a hosted session's older binary would have written Opus off)."""
    out = {}
    models = d.get("models") if isinstance(d, dict) else None
    if not isinstance(models, list):
        return out
    opts = [o for o in models if isinstance(o, dict)]
    if not any("supportsFastMode" in o for o in opts):
        return out
    for o in opts:
        fast = o.get("supportsFastMode") is True
        for k in (o.get("value"), o.get("resolvedModel")):
            k = str(k or "").strip().lower()
            if k:
                out[k] = fast
    return out


def version_tuple(cli):
    """(major, minor, patch) from a Claude Code version as the CLI reports it ("2.1.321"); None for "" or a spelling
    that opens with no x.y.z (a row no CLI stamped, a hand edit) — unknown, which note() never reads as newer."""
    m = _CLI_VERSION_RE.match(str(cli or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def note(state_dir, support: dict, cli: str = "") -> bool:
    """Record what a CLI said: `support` is {id: bool}, `cli` the Claude Code version that said it ("" when the
    payload named none). THE NEWEST CLI'S WORD STANDS (the review of 2026-11-11): sessions on one kernel run
    different Claude Code versions — a session host keeps its binary across `claude update` — and the 2.1.320 →
    2.1.321 line is exactly where Fable 5.2 gained fast mode and Opus 4.8 lost it, so last-writer-wins would have
    flipped every answer at every connect of either, a models frame and a rebuilt badge each time with no new
    information. Per id: a row stamped by a NEWER version than the writer's is left as it is, answer and stamp,
    however the writer disagrees; a row stamped by the same or an older version, or by none, takes a differing
    answer with the writer's stamp; a row the writer merely CONFIRMS keeps its answer and advances its stamp to the
    writer's when that is newer (so the confirmation counts against older binaries later), untouched otherwise; a new
    id is written. A writer whose version is unknown never overrides a stamped row's answer — it cannot show it is
    newer — but writes new ids and flips unstamped rows, and its own rows yield to anyone. Returns whether any ANSWER
    changed, so the caller can tell the pickers; a stamp advance alone rewrites the file (the chat-build signature
    reads its identity and rebuilds the statuses once, with the same answers) but is no change. Atomic (tmp +
    replace) under one lock: sessions connect on threads of their own."""
    if not state_dir or not support:
        return False
    p = _path(state_dir)
    wv = version_tuple(cli)
    with _LOCK:
        try:
            d = json.loads(p.read_text())
            d = d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            d = {}
        changed = restamped = False
        now = int(time.time())
        for k, fast in support.items():
            k = str(k or "").strip().lower()
            if not k:
                continue
            fast = bool(fast)
            cur = d.get(k)
            if isinstance(cur, dict) and isinstance(cur.get("fast"), bool):
                cv = version_tuple(cur.get("cli"))
                if cur["fast"] is fast:                                    # confirmed: the newer stamp, else as it is
                    if wv is not None and (cv is None or wv > cv):
                        d[k] = {"fast": fast, "t": now, "cli": str(cli or "")}
                        restamped = True
                    continue
                if cv is not None and (wv is None or wv < cv):
                    continue                                               # a newer CLI said otherwise: its word stands
            d[k] = {"fast": fast, "t": now, "cli": str(cli or "")}
            changed = True
        if not (changed or restamped):
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path("%s.%d.%x.tmp" % (p, os.getpid(), threading.get_ident()))
        tmp.write_text(json.dumps(d, sort_keys=True))
        os.replace(tmp, p)
    return changed


def rev(state_dir):
    """The table's revision for a view signature: the file's mtime, 0 when there is none. A write moves it, so a
    cached build that read the old answers rebuilds on the event, never on a clock."""
    try:
        return os.stat(_path(state_dir)).st_mtime_ns
    except (OSError, TypeError):
        return 0
