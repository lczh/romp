#!/usr/bin/env python3
"""Python's own answers for layer 5 of the Rust spike's port (rust/notes/s2-design.md section 2, "Landing, delegation
and provisional"; rust/SPIKE.md step S4.5), for the Rust side to match with zero differences.

It loads the real kernel the way dump_layer4.py does (an empty state root, the bench guards) and, per alive session of
a dump, calls the real functions on what the per-card build hands them: kernel._provisional_card(s, name, color, fsid,
live, now, store) with the session's tree served as its parse, its recorded captions as `_captions(fsid)` and the
board's cleared set as `_cleared_ids_display()`; per segment of every turn (kernel._segs_seam) jd._seg_human,
_seg_command, _seg_command_worked, _seg_slash_shaped, _seg_followup and _seg_system, kernel._seg_prompt,
_split_reminders, the comment strip _provisional_card runs inline, _seg_placed, _live_replanned, _live_cleared_under,
_seg_caption and _text_atoms; per atom jd._human_prompt_record; and per node in store order _card_gone,
_dictated_prompt_uuid over its promptUuid, _pure_delegation_top (one memo per session, as the body's), and over the
body's subtree of the node (its own children build, dump_layer4.body_children_fn) _summary_outrun and every
_summary_text_anchor call: each trail key of the subtree, each of the node's lines (summary, blockSummary), stub_ok
False and True, with layer 3's seg_turn and seg_best from the real _feed_seg_walk. `romp-spike layer5 DUMP` computes
the same over the typed structs.

    python3 tools/rust-spike/dump_layer5.py DUMP_DIR [DUMP_DIR ...]   # writes DUMP_DIR/layer5.json
    python3 tools/rust-spike/dump_layer5.py --edge-cases OUT.json     # the synthetic cases (romp-spike layer5 OUT.json)
    python3 tools/rust-spike/dump_layer5.py --fuzz N [--seed S] [--plant NAME] [--bin ROMP_SPIKE] [--out FILE] [--lines]
    python3 tools/rust-spike/dump_layer5.py --tables OUT.rs           # romp-views' pyre_tables.rs, from Python's re

A dump also gets two self-checks. The memo check calls every _summary_text_anchor again with a memo key, twice (a miss
then a hit of _SUMMARY_ANCHOR_MEMO, cleared first), and both must answer as the call without one did; faults are
counted (_SUMMARY_ANCHOR_STATS). The build check runs one replay build of the dump (dump_world's harness) with
_provisional_card, _pure_delegation_top, _summary_text_anchor and _summary_outrun wrapped, and compares every call the
per-card body makes with the oracle's answer for the same session, node, key, line and stub_ok.

The worlds miss most branches (no reminder, no command, no follow-up, no attachment, no placement drift, no caption,
no cleared card, no latch, no quote found only case-insensitively), so the edge cases build them, and `--fuzz N` draws
random sessions and direct calls, answered here and compared by `romp-spike layer5`; any difference or refusal fails
it. A case named "REFUSE ..." is one the Rust loader must refuse. The edge set reports the lines of the ported
functions it ran (`lineCoverage`), and so does `--fuzz` with `--lines`. The edge set also carries Python's own regex
tables (`\\d`, `\\w`, the case tables), which `tests/layer5_edge.rs` checks the port's against on every code point.
Stdlib only.
"""
import argparse
import copy
import importlib.util
import inspect
import json
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
SCHEMA = "romp-spike-layer5/1"
EDGE_SCHEMA = "romp-spike-layer5-edge/1"
OUT_NAME = "layer5.json"
NOW = 1781100000
PLANTS = ("lowercase", "em-text", "uuid-exclude", "outrun-float", "prompt-bytes", "idle-tail")
# _provisional_card's inline comment strip: the oracle runs this same expression, and refuses a kernel whose
# _provisional_card no longer holds it
CLEAN_LINE = 're.sub(r"<!--.*?-->", "", _split_reminders(_seg_prompt(held))[0], flags=re.S).strip()'
# the functions layer 5 ports (module, name): the lines each run reaches are reported
FUNCS = [("kernel", n) for n in (
    "_summary_text_anchor", "_text_atoms", "_opening_sentence", "_summary_outrun", "_dictated_prompt_uuid",
    "_pure_delegation_top", "_provisional_card", "_seg_placed", "_live_replanned", "_live_cleared_under", "_card_gone",
    "_seg_caption", "_seg_prompt", "_split_reminders")] + [("judge", n) for n in (
        "_locate_quote", "_human_prompt_record", "_seg_human", "_seg_command", "_seg_command_worked",
        "_seg_slash_shaped", "_seg_followup", "_seg_system")] + [("event_model", "postal_pairs")]


class NotRecorded(Exception):
    """_captions(fsid) for a session whose captions the dump did not record (Python never read them): the port must
    fail the same way (layer5.rs NOT_RECORDED)."""


def load_l4():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer4", os.path.join(HERE, "dump_layer4.py"))
    l4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l4)
    return l4


def raised(e):
    return {"raises": type(e).__name__, "arg": str(e)}


def plain(v):
    return json.loads(json.dumps(v))


def clean(km, text):
    """_provisional_card's prompt line (CLEAN_LINE) over `text`."""
    return re.sub(r"<!--.*?-->", "", km._split_reminders(text)[0], flags=re.S).strip()


def check_clean_line(km):
    if CLEAN_LINE not in inspect.getsource(km._provisional_card):
        raise SystemExit("dump_layer5: _provisional_card's comment strip moved; update CLEAN_LINE and layer5.rs")


class Patched:
    """The per-session readers _provisional_card and _dictated_prompt_uuid reach, served from the session's inputs:
    the parse (_parse, _parse_cached), the captions (_captions; NotRecorded when none were recorded) and the cleared
    set (_cleared_ids_display)."""

    def __init__(self, km, tree, captions, cleared):
        self.km, self.tree, self.captions, self.cleared = km, tree, captions, set(cleared or ())

    def __enter__(self):
        km = self.km
        self.saved = [(n, getattr(km, n)) for n in ("_parse", "_parse_cached", "_captions", "_cleared_ids_display")]
        tree, caps, cleared = self.tree, self.captions, self.cleared

        def captions(fsid, *a, **k):
            if caps is None:
                raise NotRecorded("_captions(%s)" % fsid)
            return copy.deepcopy(caps)
        km._parse = lambda path, sid, now: tree
        km._parse_cached = lambda path: tree
        km._captions = captions
        km._cleared_ids_display = lambda: set(cleared)
        return self

    def __exit__(self, *exc):
        for n, v in self.saved:
            setattr(self.km, n, v)
        return False


def seg_rows(km, turn, store, caps, sid):
    """Per segment of kernel._segs_seam(turn, store), its row (layer5.rs seg_row), or the raise."""
    jd = km.jd
    placements = (store or {}).get("placements") or {}
    nodes = (store or {}).get("nodes") or {}
    try:
        segs = km._segs_seam(turn, store)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        return raised(e)
    index_of = {id(a): i for i, a in enumerate(turn.get("atoms") or [])}
    rows = []
    for g in segs:
        prompt = km._seg_prompt(g)
        if caps is None:
            cap = raised(NotRecorded("_captions(%s)" % sid))
        else:
            cap = km._seg_caption(caps, g["id"])
        rows.append([g["id"], jd._seg_human(g), bool(g.get("seam")), jd._seg_system(g), jd._seg_command(g),
                     jd._seg_command_worked(g), jd._seg_slash_shaped(g), jd._seg_followup(g), prompt,
                     list(km._split_reminders(prompt)), clean(km, prompt), km._seg_placed(placements, g["id"]),
                     km._live_replanned(placements, g["id"]), km._live_cleared_under(placements, nodes, g["id"]),
                     cap, [index_of[id(a)] for a in km._text_atoms(g["atoms"])]])
    return rows


def node_lines(nd):
    out = []
    for line in (nd.get("summary"), nd.get("blockSummary")):
        if line and line not in out:
            out.append(line)
    return out


def sub_keys(km, nodes, sub):
    out = []
    for x in sub:
        for s in nodes[x].get("trail") or []:
            k = km._seg_key(s)
            if k not in out:
                out.append(k)
    return out


def session_answer(km, l4, bc, s, landing_calls=None):
    """One session's answer, keys in the order romp-spike writes them (layer5.rs session_json). `s`: {sid, path, name,
    color, live, tree, store, captions (null: never recorded), cleared, healed}. Each _summary_text_anchor call is
    appended to `landing_calls` as (nid, key, line, stub_ok, turn_seg, answer) for the memo check."""
    jd = km.jd
    sid, path, tree, store, caps = s["sid"], s.get("path"), s.get("tree"), s.get("store"), s.get("captions")
    with Patched(km, tree, caps, s.get("cleared")):
        ans = {}
        try:
            ans["provisional"] = km._provisional_card({"sid": sid, "name": s.get("name"), "path": path},
                                                      s.get("name"), s.get("color"), sid, s["live"], NOW, store)
        except Exception as e:                   # noqa: BLE001 (the raise is the answer recorded)
            ans["provisional"] = raised(e)
        turns = tree["turns"] if tree else []
        ans["segs"] = [seg_rows(km, t, store, caps, sid) for t in turns]
        ans["prompts"] = [[jd._human_prompt_record(a, sid) for a in t.get("atoms") or []] for t in turns]
        nodes = (store or {}).get("nodes") or {}
        children, _hidden = bc(nodes, s.get("healed") or {})
        km._FEED_SEG_HELD.clear()
        w = km._feed_seg_walk(sid, None, turns if tree else None, store)
        memo = {}
        f0 = km._SUMMARY_ANCHOR_STATS["fault"]
        per = []
        for nid, nd in nodes.items():
            row = {"cardGone": km._card_gone(nodes, nid),
                   "dictated": km._dictated_prompt_uuid(sid, path, nd.get("promptUuid"), memo),
                   "pure": km._pure_delegation_top(nodes, nid, sid=sid, path=path, memo=memo)}
            if l4.walk_loops(nodes, children, nid):
                row["outrun"] = row["landing"] = {"loops": True}
            else:
                sub = l4.subtree(children, nid)
                row["outrun"] = km._summary_outrun(nd, [nodes[x].get("trail") for x in sub], w.seg_best)
                rows = []
                for line in node_lines(nd):
                    for k in sub_keys(km, nodes, sub):
                        ts = w.seg_turn.get(k)
                        for stub in (False, True):
                            u, q = km._summary_text_anchor(ts, line, stub_ok=stub)
                            rows.append([k, line, stub, [u, q]])
                            if landing_calls is not None:
                                landing_calls.append((nid, k, line, stub, ts, [u, q]))
                row["landing"] = rows
            per.append([nid, row])
        ans["nodes"] = per
        ans["faults"] = km._SUMMARY_ANCHOR_STATS["fault"] - f0
        return ans


def memo_check(km, sid, calls):
    """Each landing call again with a memo key, twice: (calls, agreeing, hits, misses, faults)."""
    km._SUMMARY_ANCHOR_MEMO.clear()
    st = km._SUMMARY_ANCHOR_STATS
    h0, m0, f0 = st["hit"], st["miss"], st["fault"]
    agree = 0
    for nid, k, line, stub, ts, want in calls:
        got = [list(km._summary_text_anchor(ts, line, memo_key=(sid, nid, k), stub_ok=stub)) for _ in range(2)]
        agree += got[0] == want and got[1] == want
    km._SUMMARY_ANCHOR_MEMO.clear()
    return len(calls), agree, st["hit"] - h0, st["miss"] - m0, st["fault"] - f0


# ───────────────────────────── direct calls ─────────────────────────────
def call_answer(km, fn, args):
    jd, em = km.jd, km.em
    if fn == "opening_sentence":
        return km._opening_sentence(args[0])
    if fn == "locate_quote":
        return list(jd._locate_quote(args[0], args[1]))
    if fn == "split_reminders":
        return list(km._split_reminders(args[0]))
    if fn == "postal_pairs":
        return [list(p) for p in em.postal_pairs(args[0])]
    if fn == "bare_slash":
        return bool(jd.BARE_SLASH_RE.match(args[0]))
    if fn == "followup_re":
        m = jd.FOLLOWUP_RE.search(args[0])
        return m.group(1) if m else None
    if fn == "comment_strip":
        return re.sub(r"<!--.*?-->", "", args[0], flags=re.S)
    if fn == "human_prompt_record":
        return jd._human_prompt_record(args[0], "s")
    if fn == "summary_outrun":
        return km._summary_outrun(args[0], args[1], {k: tuple(v) for k, v in args[2]})
    if fn == "card_gone":
        return km._card_gone(args[0], args[1])
    if fn == "seg_placed":
        return km._seg_placed(args[0], args[1])
    if fn == "live_replanned":
        return km._live_replanned(args[0], args[1])
    if fn == "live_cleared_under":
        return km._live_cleared_under(args[0], args[1], args[2])
    if fn == "seg_caption":
        return km._seg_caption(args[0], args[1])
    if fn == "text_anchor":
        turn = args[0]
        atoms = turn.get("atoms") or []
        return list(km._summary_text_anchor((turn, [atoms[i] for i in args[1]]), args[2], stub_ok=args[3]))
    raise ValueError(fn)


def answer_case(km, l4, bc, name, kind, payload):
    case = {"name": name, "kind": kind}
    case.update(plain(payload))
    try:
        if kind == "session":
            case["expect"] = session_answer(km, l4, bc, case["session"])
        elif kind == "call":
            case["expect"] = call_answer(km, case["fn"], case["args"])
        else:
            raise ValueError(kind)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        case["expect"] = raised(e)
    return case


def new_cov():
    return {"sessions": 0, "cards": 0, "cardNone": 0, "cardRaises": 0, "cardResuming": 0, "cardPast": 0,
            "cardCaption": 0, "turns": 0, "raisingTurns": 0, "segments": 0, "human": 0, "seam": 0, "system": 0,
            "command": 0, "worked": 0, "slash": 0, "followup": 0, "reminders": 0, "placed": 0, "replanned": 0,
            "liveCleared": 0, "captions": 0, "textAtoms": 0, "records": 0, "nodes": 0, "cardGone": 0,
            "dictatedTrue": 0, "dictatedFalse": 0, "pure": 0, "outrun": 0, "loops": 0, "landingCalls": 0,
            "landedQuote": 0, "landedPlain": 0, "faults": 0, "calls": 0}


def count(cov, ans):
    """What one session answer reached (the header's and the fuzz's `coverage`)."""
    cov["sessions"] += 1
    p = ans["provisional"]
    if p is None:
        cov["cardNone"] += 1
    elif "raises" in p:
        cov["cardRaises"] += 1
    else:
        cov["cards"] += 1
        cov["cardResuming"] += p["text"] == "Resuming work after a restart"
        cov["cardPast"] += p["text"].startswith("Working past")
        cov["cardCaption"] += p["text"].startswith(("Working: ", "Analyzing: "))
    for rows in ans["segs"]:
        cov["turns"] += 1
        if isinstance(rows, dict):
            cov["raisingTurns"] += 1
            continue
        for r in rows:
            cov["segments"] += 1
            for i, k in ((1, "human"), (2, "seam"), (3, "system"), (4, "command"), (5, "worked"), (6, "slash"),
                         (11, "placed"), (12, "replanned"), (13, "liveCleared")):
                cov[k] += bool(r[i])
            cov["followup"] += r[7] is not None
            cov["reminders"] += len(r[9][1])
            cov["captions"] += isinstance(r[14], str) and r[14] != ""
            cov["textAtoms"] += len(r[15])
    cov["records"] += sum(1 for t in ans["prompts"] for x in t if x is not None)
    for _nid, row in ans["nodes"]:
        cov["nodes"] += 1
        cov["cardGone"] += row["cardGone"]
        cov["dictatedTrue"] += row["dictated"] is True
        cov["dictatedFalse"] += row["dictated"] is False
        cov["pure"] += row["pure"]
        if isinstance(row["outrun"], dict):
            cov["loops"] += 1
            continue
        cov["outrun"] += row["outrun"]
        for _k, _line, _stub, (u, q) in row["landing"]:
            cov["landingCalls"] += 1
            cov["landedQuote"] += q is not None
            cov["landedPlain"] += u is not None and q is None
    cov["faults"] += ans["faults"]


class Lines:
    """sys.settrace line events inside FUNCS (dump_layer4.Lines over this layer's list)."""

    def __init__(self, km):
        mods = {"kernel": km, "judge": km.jd, "event_model": km.em}
        self.codes, self.labels = {}, []
        for m, n in FUNCS:
            label = "%s.%s" % ({"judge": "jd", "event_model": "em"}.get(m, m), n)
            self.labels.append(label)
            stack = [getattr(mods[m], n).__code__]
            while stack:
                c = stack.pop()
                self.codes[id(c)] = (label, c)
                stack.extend(k for k in c.co_consts if hasattr(k, "co_lines"))
        self.hit = {label: set() for label in self.labels}

    def _global(self, frame, event, arg):
        got = self.codes.get(id(frame.f_code))
        if got is None:
            return None
        hit = self.hit[got[0]]

        def local(fr, ev, a):
            if ev == "line":
                hit.add(fr.f_lineno)
            return local
        return local

    def run(self, fn):
        sys.settrace(self._global)
        try:
            return fn()
        finally:
            sys.settrace(None)

    def report(self):
        exe = {label: set() for label in self.labels}
        for label, c in self.codes.values():
            own = {ln for _s, _e, ln in c.co_lines() if ln is not None}
            own.discard(c.co_firstlineno)
            exe[label] |= own
        out, total, ran = {}, 0, 0
        for label in self.labels:
            miss = sorted(exe[label] - self.hit[label])
            out[label] = miss
            total += len(exe[label])
            ran += len(exe[label]) - len(miss)
        return {"lines": total, "ran": ran, "missed": {k: v for k, v in out.items() if v}}


def answer_cases(km, l4, bc, specs, lines=None):
    cases, cov = [], new_cov()
    for name, kind, payload in specs:
        if lines is not None:
            case = lines.run(lambda: answer_case(km, l4, bc, name, kind, payload))
        else:
            case = answer_case(km, l4, bc, name, kind, payload)
        if name.startswith("REFUSE ") or (isinstance(case["expect"], dict) and "raises" in case["expect"]
                                          and kind == "session"):
            pass
        elif kind == "session":
            count(cov, case["expect"])
        else:
            cov["calls"] += 1
        cases.append(case)
    return cases, cov


def load_kernel(tmp):
    l4 = load_l4()
    dw, km, rec, root = l4.load_kernel(tmp)
    check_clean_line(km)
    bc, _span = l4.body_children_fn(km)
    return l4, dw, km, rec, root, bc


# ───────────────────────────── a dump ─────────────────────────────
def dump_inputs(km, snap, trees):
    out = []
    for r in snap["alive"]:
        sid = r["sid"]
        S = snap["sessions"][sid]
        out.append({"sid": sid, "path": "$ROOT/" + r["transcript"], "name": r["name"], "color": km._name_color(sid),
                    "live": sid in snap["liveMap"], "tree": trees.get(sid), "store": S["store"],
                    "captions": S["captions"], "cleared": snap["board"]["cleared"],
                    "healed": S["recorded"].get("healSessionTops") or {}})
    return out


def names_of(snap):
    names = {}
    for psid, p in snap["peers"].items():
        if p["names"] is not None:
            names[psid] = p["names"]
    for sid, S in snap["sessions"].items():
        if S["names"] is not None:
            names[sid] = S["names"]
    return names


WRAPPED = ("_provisional_card", "_pure_delegation_top", "_summary_text_anchor", "_summary_outrun")


def build_check(dw, km, snap, trees, answers):
    """One replay build with the four layer-5 entries wrapped: every call the per-card body makes is compared with the
    oracle's answer for its session and node (and key, line and stub_ok for the landing)."""
    res = {"calls": 0, "compared": 0, "mismatches": [], "unmatched": 0, "byHelper": {}}
    cur = {"sid": None, "depth": 0, "inner": False}
    saved = []

    def rows_of(sid):
        return dict(answers[sid]["nodes"])

    def check(name, got, want):
        res["compared"] += 1
        res["byHelper"][name] = res["byHelper"].get(name, 0) + 1
        if plain(got) != plain(want):
            res["mismatches"].append("%s in %s: build %r, oracle %r" % (name, cur["sid"], plain(got), plain(want)))

    def compare(name, a, k, r):
        sid = cur["sid"]
        if sid not in answers:
            res["unmatched"] += 1
            return
        if name == "_provisional_card":
            check(name, r, answers[sid]["provisional"])
        elif name == "_pure_delegation_top":
            row = rows_of(sid).get(a[1])
            if row is None:
                res["unmatched"] += 1
            else:
                check(name, r, row["pure"])
        elif name == "_summary_outrun":
            nid = a[0].get("id")
            row = rows_of(sid).get(nid)
            if row is None or isinstance(row["outrun"], dict):
                res["unmatched"] += 1
            else:
                check(name, r, row["outrun"])
        elif name == "_summary_text_anchor":
            mk = k.get("memo_key")
            line, stub = a[1], bool(k.get("stub_ok", False))
            row = rows_of(sid).get(mk[1]) if mk else None
            hit = None
            if row is not None and isinstance(row["landing"], list):
                hit = next((x[3] for x in row["landing"] if x[0] == mk[2] and x[1] == line and x[2] == stub), None)
            if hit is None:
                res["unmatched"] += 1
            else:
                check(name, list(r), hit)

    def wrap(name):
        real = getattr(km, name)

        def w(*a, **k):
            if not cur["depth"] or cur["inner"]:
                return real(*a, **k)
            cur["inner"] = True
            try:
                r = real(*a, **k)
            finally:
                cur["inner"] = False
            res["calls"] += 1
            compare(name, a, k, r)
            return r
        saved.append((name, real))
        setattr(km, name, w)

    for name in WRAPPED:
        wrap(name)
    real_entry = km._feed_session_entry

    def entry(s_, ctx):
        cur["sid"], cur["depth"] = s_["sid"], cur["depth"] + 1
        try:
            return real_entry(s_, ctx)
        finally:
            cur["depth"] -= 1
            cur["sid"] = None
    saved.append(("_feed_session_entry", real_entry))
    km._feed_session_entry = entry
    try:
        dw.replay_build(km, snap, trees)
    finally:
        for name, real in reversed(saved):
            setattr(km, name, real)
    return res


def dump_one(dw, km, l4, bc, out):
    snap, trees = dw.load_dump(out)
    with l4.World(km, names_of(snap), {}, {}):
        inputs = dump_inputs(km, snap, trees)
        sessions, cov = [], new_cov()
        memo = {"calls": 0, "agree": 0, "hits": 0, "misses": 0, "faults": 0}
        answers = {}
        for s in inputs:
            calls = []
            ans = session_answer(km, l4, bc, s, calls)
            count(cov, ans)
            with Patched(km, s["tree"], s["captions"], s["cleared"]):
                n, agree, hits, misses, faults = memo_check(km, s["sid"], calls)
            for key, v in (("calls", n), ("agree", agree), ("hits", hits), ("misses", misses), ("faults", faults)):
                memo[key] += v
            answers[s["sid"]] = ans
            sessions.append({"sid": s["sid"], "answer": ans})
    check = build_check(dw, km, snap, trees, answers)
    doc = {"schema": SCHEMA, "world": snap["world"]["name"], "python": platform.python_version(),
           "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "memoCheck": memo,
           "buildCheck": {k: (v[:10] if k == "mismatches" else v) for k, v in check.items()},
           "buildMismatches": len(check["mismatches"]), "sessions": sessions}
    path = os.path.join(out, OUT_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return cov, memo, check, os.path.getsize(path)


# ───────────────────────────── the edge cases ─────────────────────────────
SID = "eeeeeeee-0000-0000-0000-000000000005"
SETTLE = "No response requested."
COLOR = {"bg": "#4a9eff", "fg": "#ffffff"}
LONG_S = "ſ"          # ſ: matches s under re.I through re._casefix
KELVIN = "K"          # K: lowercases to k
DOTLESS = "ı"         # ı: matches i through re._casefix


def u(uuid, t, text="please fix the cache", author="human", **kw):
    a = {"type": "user", "uuid": uuid, "t": t, "session_id": SID, "author": author,
         "message": {"role": "user", "content": [{"type": "text", "text": text}] if text is not None else []}}
    a.update(kw)
    return a


def us(uuid, t, text, author="human", **kw):
    """A user atom whose content is a bare string."""
    a = {"type": "user", "uuid": uuid, "t": t, "session_id": SID, "author": author,
         "message": {"role": "user", "content": text}}
    a.update(kw)
    return a


def asst(uuid, t, text=None, tools=0, **kw):
    content = ([{"type": "text", "text": text}] if text is not None else []) + [
        {"type": "tool_use", "id": "tu-%s-%d" % (uuid, i), "name": "Bash", "input": {}} for i in range(tools)]
    a = {"type": "assistant", "uuid": uuid, "t": t, "session_id": SID,
         "message": {"role": "assistant", "content": content, "stop_reason": "tool_use" if tools else "end_turn"}}
    a.update(kw)
    return a


def tool(uuid, t, **kw):
    return asst(uuid, t, tools=1, **kw)


def tx(a, value):
    """`a` with its own `text` field (jd._human_prompt_record's `str(a.get("text") or "")`)."""
    a["text"] = value
    return a


def idle(t, end):
    return {"type": "idle", "uuid": None, "t": t, "end": end, "session_id": SID}


def att(uuid, t, text=None, content=None, **kw):
    a = {"type": "attachment", "uuid": uuid, "t": t, "session_id": SID}
    if text is not None:
        a["text"] = text
    if content is not None:
        a["message"] = {"role": "user", "content": content}
    a.update(kw)
    return a


def cmd(uuid, t, command="/model", **kw):
    a = {"type": "system", "uuid": uuid, "t": t, "session_id": SID, "command": command}
    a.update(kw)
    return a


def turn(atoms, ended=True, trigger="first", t=None, end=None, **kw):
    if trigger == "first":
        trigger = {"uuid": atoms[0]["uuid"]} if atoms else None
    tr = {"atoms": atoms, "t": atoms[0]["t"] if t is None and atoms else (t if t is not None else 100),
          "end": end if end is not None else ((atoms[-1]["t"] + 5) if atoms else 105), "ended": ended,
          "id": "edge-turn", "trigger": trigger}
    tr.update(kw)
    return tr


def tree(*turns):
    return {"turns": list(turns)}


def nd(nid, **kw):
    d = {"id": nid}
    d.update(kw)
    return d


def store(*nodes, placements=None, **kw):
    st = {"rompUuid": SID, "nodes": {n["id"]: n for n in nodes}}
    if placements is not None:
        st["placements"] = placements
    st.update(kw)
    return st


def sess(tr, st=None, captions=None, cleared=(), live=True, path="p/" + SID, healed=None, name="s005",
         color=COLOR):
    return {"sid": SID, "path": path, "name": name, "color": color, "live": live, "tree": tr, "store": st,
            "captions": {} if captions is None else captions, "cleared": list(cleared), "healed": healed or {}}


def edge_cases(km):
    """(name, kind, payload). kind "session": {"session": {...}}; kind "call": {"fn": name, "args": [...]}."""
    em = km.em

    def seg_ids(tr, store_=None):
        return [g["id"] for g in km._segs_seam(tr, store_)]

    def drift(seg_id, dt=7):
        a, t, h = seg_id.split(":")
        return "%s:%d:%s" % (a, int(t) + dt, h)

    c = []

    def add(name, s):
        c.append((name, "session", {"session": s}))

    def call(name, fn, *args):
        c.append((name, "call", {"fn": fn, "args": list(args)}))

    open_turn = turn([u("u1", 100), tool("a1", 105)], ended=False)
    # ── _provisional_card: the gates
    add("card: an open turn's fresh human prompt", sess(tree(open_turn)))
    add("card: not live", sess(tree(open_turn), live=False))
    add("card: no tree", sess(None))
    add("card: no turns", sess(tree()))
    add("card: a last turn with no atoms (one empty segment, no trigger atom)",
        sess(tree(turn([u("u1", 100)]), turn([], ended=False, trigger=None, t=200, end=200))))
    add("card: an ended turn is Analyzing and judging",
        sess(tree(turn([u("u1", 100), asst("a1", 101, "on it")]))))
    add("card: an idle span mid-turn closes it though the turn did not end",
        sess(tree(turn([u("u1", 100), idle(101, 105), tool("a1", 106)], ended=False))))
    add("card: an idle tail closes it", sess(tree(turn([u("u1", 100), idle(101, 105)], ended=False))))
    add("card: ended absent reads open", sess(tree({k: v for k, v in open_turn.items() if k != "ended"})))
    add("card: a peer's trigger", sess(tree(turn([u("u1", 100, author={"peer": "s9", "mid": "m"})], ended=False))))
    add("card: an sdk trigger", sess(tree(turn([u("u1", 100, author="sdk")], ended=False))))
    add("card: the interrupt record", sess(tree(turn([u("u1", 100, "[Request interrupted by user for tool use]"),
                                                      tool("a1", 101)], ended=False))))
    add("card: the interrupt record as a bare string",
        sess(tree(turn([us("u1", 100, " [Request interrupted by user]"), tool("a1", 101)], ended=False))))
    sysnote = "<!-- romp-system --> Restarted: continue the work"
    add("card: a romp-system notice from romp is Resuming",
        sess(tree(turn([u("u1", 100, sysnote, author="romp"), tool("a1", 101)], ended=False))))
    add("card: a romp-system notice with a caption is still Resuming",
        sess(tree(turn([u("u1", 100, sysnote, author="romp")], ended=False)),
             captions={"%s:100:%s#p" % (SID, em._text_hash8(u("u1", 100, sysnote))): {"caption": "gist"}}))
    add("card: a system notice reads the captions first: unrecorded ones fail",
        sess(tree(turn([u("u1", 100, sysnote, author="romp")], ended=False)), captions=None) | {"captions": None})
    add("card: unrecorded captions fail on a plain prompt too", sess(tree(open_turn)) | {"captions": None})
    add("card: unrecorded captions are never read when the gate closes first",
        sess(tree(turn([u("u1", 100, author="sdk")], ended=False))) | {"captions": None})
    add("card: a bare command", sess(tree(turn([u("u1", 100, "/usage", command="/usage"),
                                               asst("o1", 101, "usage: ...", command=True)], ended=False))))
    add("card: a command that put the model to work",
        sess(tree(turn([u("u1", 100, "/jld do the thing", command="/jld"), tool("a1", 101)], ended=False))))
    add("card: a command with a falsy flag is not a command",
        sess(tree(turn([u("u1", 100, "/jld x", command=""), asst("o1", 101, "x", command=0)], ended=False))))
    add("card: slash-shaped human text", sess(tree(turn([u("u1", 100, "/compact")], ended=False))))
    add("card: a slash command with arguments", sess(tree(turn([u("u1", 100, "/model opus 4")], ended=False))))
    add("card: a path is not slash-shaped", sess(tree(turn([u("u1", 100, "/Users/x/y.py fails")], ended=False))))
    add("card: a combining mark is not \\w, so not slash-shaped",
        sess(tree(turn([u("u1", 100, "/kaा")], ended=False))))
    add("card: a superscript is \\w, so slash-shaped", sess(tree(turn([u("u1", 100, "/x²")], ended=False))))
    add("card: slash then a second line is not slash-shaped",
        sess(tree(turn([u("u1", 100, "/model opus\nthen fix it")], ended=False))))
    add("card: a slash command with a command flag is a command, not slash-shaped",
        sess(tree(turn([u("u1", 100, "/compact", command="/compact"), tool("a1", 101)], ended=False))))
    # follow-ups
    fu = "please continue <!-- romp-goal-id: %s:g1 -->" % SID
    g1 = nd(SID + ":g1", parentId=None)
    g1c = nd(SID + ":g1", parentId=None, cleared=True)
    add("card: a follow-up whose target stands", sess(tree(turn([u("u1", 100, fu)], ended=False)), store(g1)))
    add("card: a follow-up whose target was cleared, open turn",
        sess(tree(turn([u("u1", 100, fu)], ended=False)), store(g1c)))
    add("card: a follow-up whose target's parent was cleared",
        sess(tree(turn([u("u1", 100, fu)], ended=False)),
             store(nd("g0", cleared=True), nd(SID + ":g1", parentId="g0"))))
    add("card: a follow-up whose target was cleared, turn ended", sess(tree(turn([u("u1", 100, fu)])), store(g1c)))
    add("card: a follow-up whose absent target is in the cleared log",
        sess(tree(turn([u("u1", 100, fu)], ended=False)), store(), cleared=[SID + ":g1"]))
    add("card: a follow-up whose absent target has no clear evidence",
        sess(tree(turn([u("u1", 100, fu)], ended=False)), store()))
    fu_turn = turn([u("u1", 100, fu)], ended=False)
    fid = seg_ids(fu_turn)[-1]
    add("card: a follow-up already live-replanned",
        sess(tree(fu_turn), store(g1c, placements={fid + "#live": None})))
    add("card: a follow-up already live-replanned at a drifted time",
        sess(tree(fu_turn), store(g1c, placements={drift(fid) + "#live": SID + ":g2"})))
    add("card: a follow-up target in a cycle is not gone",
        sess(tree(fu_turn), store(nd(SID + ":g1", parentId="c2"), nd("c2", parentId=SID + ":g1"))))
    add("card: a follow-up marker without its comment form",
        sess(tree(turn([u("u1", 100, "romp-goal-id: %s:g1" % SID)], ended=False)), store(g1)))
    # placements
    pid = seg_ids(open_turn)[-1]
    for ph in ("", "#p", "#d", "#live"):
        add("card: placed exactly as %r" % (ph or "bare"), sess(tree(open_turn), store(g1, placements={pid + ph: g1["id"]})))
    add("card: placed at a drifted time", sess(tree(open_turn), store(g1, placements={drift(pid) + "#p": g1["id"]})))
    add("card: a retired placement still counts", sess(tree(open_turn), store(placements={pid + "#p": None})))
    add("card: placed, but every target cleared mid-work: resurrected",
        sess(tree(open_turn), store(g1c, placements={pid + "#p": g1["id"], drift(pid): g1["id"]})))
    add("card: placed on a missing node mid-work: resurrected",
        sess(tree(open_turn), store(placements={pid + "#p": "gone"})))
    add("card: placed, cleared, but one target stands",
        sess(tree(open_turn), store(g1c, nd("g2"), placements={pid + "#p": g1["id"], pid: "g2"})))
    add("card: placed, cleared, one retired", sess(tree(open_turn), store(g1c, placements={pid + "#p": g1["id"],
                                                                                         pid + "#d": None})))
    add("card: placed and cleared, but the live re-plan ran",
        sess(tree(open_turn), store(g1c, placements={pid + "#p": g1["id"], pid + "#live": None})))
    add("card: placed and cleared, turn ended: no resurrect",
        sess(tree(turn([u("u1", 100), tool("a1", 105)])), store(g1c, placements={pid + "#p": g1["id"]})))
    add("card: a placement of another segment", sess(tree(open_turn), store(g1, placements={pid[:-1] + "x": "g"})))
    add("card: null placements", sess(tree(open_turn), store(g1, placements=None) | {"placements": None}))
    # the prompt
    add("card: a prompt that is only a comment", sess(tree(turn([u("u1", 100, "<!-- note -->  ")], ended=False))))
    add("card: a prompt that is only a reminder",
        sess(tree(turn([u("u1", 100, "<system-reminder>ctx</system-reminder>")], ended=False))))
    add("card: a reminder peeled, the prompt kept",
        sess(tree(turn([u("u1", 100, "<system-reminder>ctx</system-reminder>\n\nfix the bug please")],
                       ended=False))))
    add("card: an unclosed comment stays in the prompt",
        sess(tree(turn([u("u1", 100, "fix <!-- this")], ended=False))))
    add("card: a comment across lines is removed", sess(tree(turn([u("u1", 100, "fix <!-- a\nb --> it")],
                                                                  ended=False))))
    add("card: <!--> is not a comment by itself", sess(tree(turn([u("u1", 100, "a <!--> b --> c")], ended=False))))
    long_p = ("Spécial tâche → " * 12) + "end"
    add("card: a long non-ASCII prompt is cut at 140 code points", sess(tree(turn([u("u1", 100, long_p)],
                                                                                 ended=False))))
    add("card: the cut lands on whitespace, then stripped",
        sess(tree(turn([u("u1", 100, "x" * 139 + "   tail")], ended=False))))
    add("card: a bare-string prompt is kept unstripped by _seg_prompt",
        sess(tree(turn([us("u1", 100, "  spaced prompt  ")], ended=False))))
    add("card: a float time rides into t and _ageT", sess(tree(turn([u("u1", 100.5), tool("a1", 101)],
                                                                    ended=False))))
    add("card: the last of two inputs is the held segment",
        sess(tree(turn([u("u1", 100, "first ask"), tool("a1", 101), u("u2", 110, "second ask")], ended=False))))
    add("card: the last turn's first atom has no session_id: segmentation raises",
        sess(tree(turn([{k: v for k, v in u("u1", 100).items() if k != "session_id"}], ended=False))))
    add("card: a null name and color pass through", sess(tree(open_turn), name=None, color=None))
    seam_turn = turn([u("u1", 100, "do it"), asst("a1", 101, "on it"), tool("a2", 110), asst("a3", 120, "done")],
                     ended=False)
    skey = km._seg_key(seg_ids(seam_turn)[0])
    seam_st = lambda text="Fix the cache", **kw: store(g1, seams=[{"t": 105, "top": g1["id"], "text": text,
                                                                     "segs": [skey], **kw}])
    tail_id = seg_ids(seam_turn, seam_st())[-1]
    # captions
    cap_id = pid + "#p"
    for label, row in (("a string", {"caption": "fixing the cache"}), ("empty", {"caption": ""}),
                       ("null", {"caption": None}), ("absent", {"grain": "message"}), ("an empty row", {}),
                       ("falsy list", {"caption": []})):
        add("card: caption %s" % label, sess(tree(open_turn), captions={cap_id: row}))
    for label, v in (("a number", 5), ("true", True), ("a list", ["x"]), ("a dict", {"a": 1}), ("a float", 2.5)):
        add("card: caption %s raises TypeError on pre + g" % label, sess(tree(open_turn), captions={cap_id: {
            "caption": v}}))
    add("card: a seam tail's caption a number raises too", sess(tree(seam_turn), seam_st(),
                                                               captions={tail_id + "#p": {"caption": 7}}))
    add("card: a drifted caption is found by key",
        sess(tree(open_turn), captions={drift(pid, 3) + "#p": {"caption": "drifted gist"}}))
    add("card: the first drifted caption in the rows' order wins",
        sess(tree(open_turn), captions={drift(pid, 9) + "#p": {"caption": "first"},
                                        drift(pid, 2) + "#p": {"caption": "second"}}))
    add("card: an exact empty row stops the drift scan",
        sess(tree(open_turn), captions={drift(pid, 3) + "#p": {"caption": "drifted"}, cap_id: {}}))
    add("card: a bare segment caption is not a message caption",
        sess(tree(open_turn), captions={pid: {"caption": "work gist"}}))
    add("card: a caption on an ended turn is Analyzing",
        sess(tree(turn([u("u1", 100), tool("a1", 105)])), captions={cap_id: {"caption": "gist"}}))
    # seam tails
    add("card: a seam tail names the goal it followed", sess(tree(seam_turn), seam_st()))
    add("card: a seam tail with a null text", sess(tree(seam_turn), seam_st(text=None)))
    add("card: a seam tail with an empty text", sess(tree(seam_turn), seam_st(text="")))
    add("card: a seam tail with no text key", sess(tree(seam_turn), store(g1, seams=[{"t": 105, "top": g1["id"],
                                                                                    "segs": [skey]}])))
    add("card: a seam tail's long text is cut at 100 code points",
        sess(tree(seam_turn), seam_st(text="étape " * 30)))
    add("card: a seam tail with a caption", sess(tree(seam_turn), seam_st(),
                                               captions={tail_id + "#p": {"caption": "after work"}}))
    add("card: a seam tail placed", sess(tree(seam_turn), store(g1, seams=seam_st()["seams"],
                                                              placements={tail_id: g1["id"]})))
    # trigger lookups
    add("card: no trigger: the first atom without a uuid is the trigger (a human system notice)",
        sess(tree(turn([asst("a1", 100, "x"), att(None, 101, author="human", content=[
            {"type": "text", "text": "<!-- romp-system --> resumed"}])], ended=False, trigger=None))))
    add("card: no trigger: the first uuid-less atom is human, atoms[0] is not",
        sess(tree(turn([asst("a1", 100, "x"), att(None, 101, "hello there", author="human")], ended=False,
                       trigger=None))))
    add("card: a trigger of \"\" finds the atom whose uuid is \"\"",
        sess(tree(turn([tool("a1", 100), u("", 101, author="romp"), asst("a2", 102, "y")], ended=False,
                       trigger={"uuid": ""}))))
    add("card: a trigger of \"\" is no trigger to _seg_prompt: the first user atom's text, not the \"\" atom's",
        sess(tree(turn([u("x0", 100, "the leading user text", author=None), u("", 101, "the ask", author="romp")],
                       ended=False))))
    add("card: a trigger naming an assistant atom (no input)",
        sess(tree(turn([asst("a1", 100, "x"), u("u9", 101, "words", author=None)], ended=False,
                       trigger={"uuid": "a1"}))))
    add("card: a trigger naming nothing: atoms[0]",
        sess(tree(turn([u("u0", 100, "please do", author=None), tool("a1", 101)], ended=False,
                       trigger={"uuid": "zz"}))))

    # ── the summary landing, per node
    s1 = "Fixed the cache invalidation bug in the loader."
    t_land = turn([u("u1", 100, "fix the cache"), tool("a1", 101),
                   asst("a2", 102, "First I looked. " + s1 + " Tests pass."),
                   asst("a3", 103, "Later: " + s1 + " Done."), asst("a4", 104, "ok")])
    lid = seg_ids(t_land)
    add("landing: the newest text atom carrying the opening sentence",
        sess(tree(t_land), store(nd("g1", summary=s1 + " More detail.", trail=lid))))
    add("landing: list markers and paragraphs before the sentence",
        sess(tree(t_land), store(nd("g1", summary="\n \n1. " + s1 + "\nsecond line", trail=lid),
                                 nd("g2", blockSummary="- " + s1, trail=lid),
                                 nd("g3", summary="١) " + s1, trail=lid))))
    add("landing: a line with no stop past 12 code points takes the paragraph's first line",
        sess(tree(t_land), store(nd("g1", summary="Fixed it. no stop after", trail=lid))))
    t_ci = turn([u("u1", 100), asst("a1", 101, "We fixed the MO" + LONG_S + "T important cache bug this time."),
                 asst("a2", 102, "Fixed the missing cache key, and the \u03a3\u039f\u03a6\u039f\u03a3 label.")])
    add("landing: matched only case-insensitively, through the long s, the dotless i and the final sigma",
        sess(tree(t_ci), store(nd("g1", summary="We fixed the most important cache bug this time.", trail=seg_ids(t_ci)),
                               nd("g2", summary="Fixed the m" + DOTLESS + "ssing cache key, and the \u03c3\u03bf\u03c6"
                                                "\u03bf\u03c2 label.", trail=seg_ids(t_ci)))))
    t_fold = turn([u("u1", 100), asst("a1", 101, "We fixed the MO" + LONG_S + "T important bug.\n\nLater text"),
                   asst("a2", 102, "We added the missing "+ KELVIN + "ey check and the M" + "İ" + "SS case.")])
    fid2 = seg_ids(t_fold)
    add("landing: sre's fold (long s, Kelvin, I-dot) and whitespace collapse",
        sess(tree(t_fold), store(nd("g1", summary="We fixed the most   important bug. Then more.", trail=fid2),
                                 nd("g2", summary="We added the missing key check and the miss case. x", trail=fid2))))
    t_bare = turn([u("u1", 100), asst("a1", 101, "short"), asst("a2", 102, None)])
    t_bare["atoms"][2]["message"]["content"] = s1 + " (a bare string)"
    add("landing: a bare-string atom is a text atom but jd._atom_text reads it as \"\"",
        sess(tree(t_bare), store(nd("g1", summary=s1, trail=seg_ids(t_bare)))))
    t_two = turn([u("u1", 100, "first"), asst("d1", 101, "short reply"), u("u2", 110, "second"),
                  asst("d1", 111, "Another segment says: " + s1)])
    tid = seg_ids(t_two)
    add("landing: the turn's other text atoms are excluded by identity, not uuid (a repeated uuid)",
        sess(tree(t_two), store(nd("g1", summary=s1, trail=[tid[0]]))))
    add("landing: found only in another segment of the turn", sess(tree(t_two), store(nd("g1", summary=s1,
                                                                                          trail=[tid[0]]))))
    t_both = turn([u("u1", 100, "first"), asst("e1", 101, "Here: " + s1), u("u2", 110, "second"),
                   asst("e2", 111, "Again: " + s1)])
    add("landing: the segment's own atoms are searched before the turn's",
        sess(tree(t_both), store(nd("g1", summary=s1, trail=[seg_ids(t_both)[0]]))))
    sub_text = "Recap: " + "the cache layer now invalidates on write and the loader retries once. " * 2
    t_sub = turn([u("u1", 100, "x"), asst("b1", 101, sub_text), asst("b2", 102, "brief note"),
                  u("u2", 110, "y"), asst("b3", 111, sub_text + " Again."), asst("b4", 112, "tiny")])
    sid2 = seg_ids(t_sub)
    add("landing: no quote found: the segment's newest substantive atom, else the turn's, else the stub",
        sess(tree(t_sub), store(nd("g1", summary="Unrelated words here entirely.", trail=[sid2[0]]),
                                nd("g2", summary="Unrelated words here entirely.", trail=[sid2[1]]))))
    t_two_sub = turn([u("u1", 100, "x"), asst("s1", 101, sub_text), asst("s2", 102, sub_text + " Twice."),
                      asst("s3", 103, "brief")])
    add("landing: two substantive atoms in the segment: the newer one",
        sess(tree(t_two_sub), store(nd("g1", summary="Unrelated words here entirely.", trail=seg_ids(t_two_sub)))))
    t_stub = turn([u("u1", 100, "x"), asst("c1", 101, "tiny one"), asst("c2", 102, "tiny two"),
                   asst("c3", 103, SETTLE), asst("c4", 104, "api", isApiError=True), asst("", 105, "empty uuid")])
    add("landing: only stubs: None without stub_ok, the last text atom with it (settle, API error, \"\" skipped)",
        sess(tree(t_stub), store(nd("g1", summary="Nothing matches.", trail=seg_ids(t_stub)))))
    t_syn = turn([u("u1", 100, "x"), asst("e1", 101, s1, message={"role": "assistant", "model": "<synthetic>",
                                                                   "content": [{"type": "text", "text": s1}]})])
    add("landing: a synthetic reply is not a text atom", sess(tree(t_syn), store(nd("g1", summary=s1,
                                                                                    trail=seg_ids(t_syn)))))
    long_ws = turn([u("u1", 100), asst("f1", 101, "Alpha" + " " * 320 + "beta gamma.")])
    add("landing: a located span longer than 300 code points is cut",
        sess(tree(long_ws), store(nd("g1", summary="Alpha beta gamma. And then", trail=seg_ids(long_ws)))))
    add("landing: a trail key not in the walk, a blank line, no line",
        sess(tree(t_land), store(nd("g1", summary="   ", blockSummary=s1, trail=["%s:1:nokey" % SID] + lid),
                                 nd("g2", trail=lid), nd("g3", summary=s1, blockSummary=s1, trail=lid))))
    add("landing: the subtree's trails, in subtree order",
        sess(tree(t_land, t_two), store(nd("g1", summary=s1, trail=[tid[1]]), nd("g2", parentId="g1", trail=lid),
                                        nd("g3", parentId="g1", trail=[tid[0], lid[0]]))))
    add("landing: a node in a parent cycle loops",
        sess(tree(t_land), store(nd("g1", summary=s1, trail=lid, parentId="g2"), nd("g2", parentId="g1"))))
    raise_turn = turn([{k: v for k, v in u("u9", 300).items() if k != "session_id"}])
    add("landing: a walk stopped by a raise: the raising turn's keys are not in seg_turn",
        sess(tree(t_land, raise_turn), store(nd("g1", summary=s1, trail=lid + ["%s:300:x" % SID]))))
    add("landing: the healed children decide the subtree",
        sess(tree(t_land, t_two), store(nd("g1", summary=s1, trail=[tid[0]]), nd("g2", parentId=None, trail=lid)),
             healed={"g2": ["g1", {"why": "healed"}]}))

    # ── _summary_outrun, per node
    t_out = turn([u("u1", 100), asst("o1", 101, s1)], t=100)
    oid = seg_ids(t_out)
    add("outrun: coverage stamps against the trail's segment times and the follow-up",
        sess(tree(t_out), store(nd("g1", distilledMt=99, trail=oid), nd("g2", distilledMt=100, trail=oid),
                                nd("g3", distilledMt=99.9, trail=oid), nd("g4", briefedMt=50, distilledMt=None,
                                                                          followupAt=60, trail=oid),
                                nd("g5", distilledMt=200, followupAt=200, trail=oid),
                                nd("g6", distilledMt=200, followupAt=200.5, trail=oid),
                                nd("g7", distilledMt=0.9, followupAt=5, trail=oid),
                                nd("g8", distilledMt=-5.5, trail=oid), nd("g9", trail=oid, followupAt=1e20),
                                nd("g10", distilledMt=1e20, followupAt=1e20 + 4096.0, trail=oid))))

    # ── _pure_delegation_top and _dictated_prompt_uuid, per node
    hand = {"peer": "s9", "goalId": "s9:g1"}
    t_d = turn([u("h1", 100, "please ask the peers"), asst("m1", 101, "asking"),
                u("h1", 102, "[Request interrupted by user]"), u("i1", 103, "[Request interrupted by user]"),
                att("at1", 104, "queued words"), att("at2", 105, "mail <!-- romp-msg-id: m9 -->"),
                att("at3", 106, "nudge <!-- romp-injected -->"), att("at4", 107, " \x1c "),
                att("at5", 108, content=[{"type": "text", "text": "from content"}], text="ignored"),
                att("at6", 109, "<!-- romp-msg-kind: x<!--romp-msg-id:y -->"),
                us("hs", 110, "  dictated as a string  "), tx(u("ht", 111, None), "from the text field"),
                u("hn", 112, None)])
    deleg = lambda top, *more, **kw: store(nd("t", **top), nd("k1", parentId="t", handoff=hand),
                                           nd("k2", parentId="t", handoff=hand), *more, **kw)
    add("delegation: all handoff leaves, no prompt: pure", sess(tree(t_d), deleg({})))
    add("delegation: a dictated human prompt exempts the top", sess(tree(t_d), deleg({"promptUuid": "h1"})))
    add("delegation: the first atom under a uuid decides (a human one, then an interrupt under the same uuid)",
        sess(tree(t_d), deleg({"promptUuid": "h1"})))
    add("delegation: an assistant anchor is not dictated: pure", sess(tree(t_d), deleg({"promptUuid": "m1"})))
    add("delegation: an interrupt anchor is not dictated: pure", sess(tree(t_d), deleg({"promptUuid": "i1"})))
    add("delegation: an unresolvable anchor fails open", sess(tree(t_d), deleg({"promptUuid": "nope"})))
    add("delegation: no path fails open", sess(tree(t_d), deleg({"promptUuid": "m1"}), path=None))
    add("delegation: no parse fails open", sess(None, deleg({"promptUuid": "m1"})))
    for anchor in ("at1", "at2", "at3", "at4", "at5", "at6", "hs", "ht", "hn"):
        add("delegation: an anchor on %s" % anchor, sess(tree(t_d), deleg({"promptUuid": anchor})))
    for latch in ("human", "absent", "scheduled", "machine", "other", "", 5, None, ["human"]):
        add("delegation: latch %r on a machine anchor" % (latch,), sess(tree(t_d), deleg({"promptUuid": "m1",
                                                                                          "askAnchor": latch})))
        add("delegation: latch %r on a human anchor" % (latch,), sess(tree(t_d), deleg({"promptUuid": "h1",
                                                                                        "askAnchor": latch})))
    add("delegation: an origin dict skips the latch and the anchor",
        sess(tree(t_d), deleg({"promptUuid": "h1", "askAnchor": "human", "origin": {"peer": "s8"}})))
    add("delegation: a userAsk dict exempts", sess(tree(t_d), deleg({"userAsk": {"text": "x"}})))
    add("delegation: a userAsk that is not a dict does not", sess(tree(t_d), deleg({"userAsk": "x"})))
    add("delegation: an empty promptUuid is no anchor", sess(tree(t_d), deleg({"promptUuid": ""})))
    add("delegation: a top that is itself a handoff",
        sess(tree(t_d), store(nd("t", handoff=hand, promptUuid="h1", userAsk={"a": 1}))))
    add("delegation: one own-work leaf", sess(tree(t_d), deleg({}, nd("k3", parentId="t"))))
    add("delegation: a nested own-work leaf under a handoff",
        sess(tree(t_d), deleg({}, nd("k3", parentId="k1"))))
    add("delegation: a cycle below the top", sess(tree(t_d), deleg({}, nd("c1", parentId="c2", handoff=hand),
                                                                  nd("c2", parentId="c1", handoff=hand))))
    add("delegation: own parents, not the heal, build the walk",
        sess(tree(t_d), deleg({}, nd("k3")), healed={"k3": ["t", {"why": "healed"}]}))
    add("delegation: a leaf only, no handoff: not pure", sess(tree(t_d), store(nd("t"))))
    add("delegation: card gone over cleared ancestors and a missing parent",
        sess(tree(t_d), store(nd("a", cleared=True), nd("b", parentId="a"), nd("c", parentId="zz"),
                              nd("d", parentId="d"))))

    # ── direct calls
    for line in (None, "", "   ", "Short.", "Exactly twelve. Then", "Twelve chars!x more text. end",
                 "e.g. the fix is in. Next", "Version 1.2.3 shipped today. Next", "Done? Next sentence here.",
                 "Done!　and more words here. x", "Is it done\u001c? Next", "a\n\nb", "a\n \t\nb", "a\n \nb",
                 "a\n\u0085\nb", "line one\nline two", "\n\n\n  ", "1.Item without space", "1. ", "12) twelve items",
                 "- a dash item", "* a star item", "-no space", "１. fullwidth digit", "². superscript",
                 "١٢. arabic digits", "  2.   spaced  marker  ", "x" * 250, "é" * 199 + " y",
                 "word " * 60, "First paragraph.\n\nSecond.", "  \n\n  Lead blank paragraphs. Yes", "End with dot.",
                 "No terminal punctuation at all here", "Stop at end?", "a.b.c.d.e.f.g.h. done", "Ünïcödé text "
                 "here. Then", "\u00e9" * 8 + ". rest. end", "1.\n \nFixed the thing. More",
                 "This is the first part!\u00a0Then more.", "This is the first part?\u3000Then more.",
                 "This is the first part.\u001cThen more.", "This is the first part.\u0085Then more."):
        call("opening %r" % (line[:30] if line else line,), "opening_sentence", line)
    for text, q in (("hello world", "world"), ("café world", "world"), ("x", None), (None, "x"), ("", "x"),
                    ("x", ""), ("x", "  \n "), ("Hello   World", "hello world"), ("Hello\nWorld", " hello  world "),
                    ("HELLO WORLD", "hello world"), ("hello", "hello world"), ("ma" + LONG_S + "t", "MAST"),
                    ("mast", "MA" + LONG_S + "T"), ("the " + KELVIN + "ey", "KEY"), ("the key", KELVIN + "EY"),
                    ("straße", "STRAẞE"), ("straẞe", "straße"), ("İnfo", "info"),
                    ("info", "İNFO"), ("f" + DOTLESS + "x", "FIX"), ("σοφος", "ΣΟΦΟΣ"),
                    ("µs", "ΜS"), ("axb", "a.b"), ("a.b", "a.b"), ("(x)", "(X)"), ("a+b", "A+B"),
                    ("one two one two", "TWO one"), ("a　b", "a b"), ("a\u001cb", "a b"), ("a​b", "a b"),
                    ("tab\there", "TAB here"), ("x y z", "y  z"), ("ab", "b"), ("日本語のテキスト", "テキスト"),
                    ("Hello world, then hello world", "hello world"), ("HELLO there; hello  there", "hello there"),
                    ("x y", "x\u3000y"), ("one two", "one\u001ctwo"), ("one two", "ONE\u0085TWO"),
                    ("\u01c6ungla", "\u01c5UNGLA"), ("x\u01c4y", "X\u01c5Y")):
        call("locate %r in %r" % (q, text), "locate_quote", text, q)
    for text in ("plain", "  spaced  ", "", "<system-reminder>only</system-reminder>",
                 "a<system-reminder>r</system-reminder>b", "a <system-reminder>r</system-reminder> b",
                 "a\n<system-reminder>r</system-reminder>\nb", "a\n\n<system-reminder>r</system-reminder>b",
                 "a<system-reminder>r</system-reminder>\n\n\nb", "x<task-notification>t</task-notification>y",
                 "<task-notification>t</task-notification><system-reminder>r</system-reminder>z",
                 "a<system-reminder>unclosed", "a<system-reminder> </system-reminder>b",
                 "a<system-reminder>r1</system-reminder>m<system-reminder>r2</system-reminder>n",
                 "</system-reminder>a<system-reminder>r</system-reminder>", "a\u001c<system-reminder>r</system-reminder>　b",
                 "<system-reminder>a<task-notification>b</task-notification>c</system-reminder>d"):
        call("split %r" % text[:40], "split_reminders", text)
    for text in ("", "plain", "<!-- romp-msg-id: m1 -->", "<!--romp-msg-id:m1-->", "<!-- romp-msg-id: m1 --> <!-- "
                 "romp-msg-kind: question -->", "<!-- romp-msg-kind: delegate --><!-- romp-msg-id: m2 -->",
                 "<!-- romp-msg-id: a --><!-- romp-msg-id: b --><!-- romp-msg-kind: coordinate -->",
                 "<!-- romp-msg-id: a --><!-- romp-msg-kind: bogus --><!-- romp-msg-kind: question -->",
                 "<!-- romp-msg-kind: x<!--romp-msg-id:y -->", "<!-- romp-msg-id: a b -->", "<!-- romp-msg-id: -->",
                 "<!-- romp-msg-id: m1--->", "<!-- romp-msg-id:　m1　-->", "<!-- romp-msg-ID: m1 -->",
                 "<!-- romp-msg-id: a<!-- romp-msg-id: b -->", "x<!--<!-- romp-msg-id: z -->",
                 "<!-- romp-msg-id: a-->b -->"):
        call("postal %r" % text[:40], "postal_pairs", text)
    for text in ("/compact", "/model opus", "/a", "/1", "/", "", "/Users/x/y.py", "/a:b-c_d", "/a\tb", "/a b\nc",
                 "/a b\n", "/a\n", "/ab\n\n", "/é", "/aé", "/aा", "/x²", "/xⅠ", " /x", "/x ",
                 "/x y", "/x.y", "/xͅ"):
        call("bare slash %r" % text, "bare_slash", text)
    for text in ("<!-- romp-goal-id: g1 -->", "<!--romp-goal-id:g1-->", "<!-- romp-goal-id: g1--->",
                 "<!-- romp-goal-id: a>b -->", "<!-- romp-goal-id: a>b --> <!-- romp-goal-id: c -->",
                 "romp-goal-id: g1", "<!-- romp-goal-id:  -->", "<!-- romp-goal-id: s:g1 --> <!-- romp-goal-id: s:g2 -->",
                 "<!-- romp-goal-id:　gé　-->", "<!--  ROMP-goal-id: x -->", "<!-- romp-goal-id: a\nb -->"):
        call("followup %r" % text[:40], "followup_re", text)
    for text in ("", "plain", "a<!-- x -->b", "a<!--x\ny-->b", "<!-->", "<!---->x", "<!--->x", "a<!-- b",
                 "a<!-- b -->c<!-- d", "<!--<!-- x -->-->", "--> <!-- x -->"):
        call("comment strip %r" % text, "comment_strip", text)
    for a in t_d["atoms"] + [u("x1", 1, author=None), asst("x2", 2, "hi"), us("x3", 3, "", author="human"),
                             att("x4", 4), att("x5", 5, text=""), tx(u("x6", 6, None), 0)]:
        call("record %s" % a["uuid"], "human_prompt_record", a)
    best = [["s:h", [None, False, 150]], ["s:f", ["u", True, 150.5]], ["s:z", [None, False, 0.0]]]
    for label, node, trails in (
            ("no coverage", nd("g"), [["s:1:h"]]), ("int cover below", nd("g", distilledMt=149), [["s:1:h"]]),
            ("int cover equal", nd("g", distilledMt=150), [["s:1:h"]]),
            ("truncated float cover", nd("g", distilledMt=150.9), [["s:1:f"]]),
            ("briefed beats distilled", nd("g", distilledMt=100, briefedMt=160), [["s:1:h"]]),
            ("followup after", nd("g", distilledMt=100, followupAt=100.5), []),
            ("followup null", nd("g", distilledMt=100, followupAt=None), [None, []]),
            ("zero seg time", nd("g", distilledMt=1), [["s:9:z"]]), ("key absent", nd("g", distilledMt=1), [["s:9:q"]]),
            ("negative cover", nd("g", distilledMt=-5.5, briefedMt=-1), [["s:1:h"]]),
            ("big cover", nd("g", distilledMt=1e20), [["s:1:h"]]),
            ("big followup", nd("g", briefedMt=7, followupAt=1e300), [])):
        call("outrun %s" % label, "summary_outrun", node, trails, best)
    nodes_g = {"a": nd("a", cleared=True), "b": nd("b", parentId="a"), "c": nd("c", parentId="zz"),
               "d": nd("d", parentId="e"), "e": nd("e", parentId="d"), "f": nd("f", cleared=False, parentId=None)}
    for nid in ("a", "b", "c", "d", "f", "missing"):
        call("card gone %s" % nid, "card_gone", nodes_g, nid)
    P = {"s:5:h": "g1", "s:6:k#p": None, "s:7:m#d": "g2", "s:8:n#live": "g3", "plain": "g4", "a:b": "g5",
         "s:1:2:q#p": "g6"}
    for seg_id in ("s:5:h", "s:9:h", "s:9:k", "s:9:m", "s:9:n", "s:5:z", "plain", "a:b", "s:3:2:q", "x:y:z:q"):
        call("placed %s" % seg_id, "seg_placed", P, seg_id)
        call("replanned %s" % seg_id, "live_replanned", P, seg_id)
        call("live cleared %s" % seg_id, "live_cleared_under", P, nodes_g, seg_id)
    caps = {"s:1:h": {"caption": "work"}, "s:2:h#p": {"caption": "msg"}, "s:3:j#p": {"caption": None},
            "s:4:k#p": {}, "s:5:m#p": {"caption": "first"}, "s:6:m#p": {"caption": "second"}, "s:7:n#p": {"caption": 0},
            "q#p": {"caption": "short"}}
    for seg_id in ("s:9:h", "s:2:h", "s:9:j", "s:9:k", "s:9:m", "s:9:n", "s:9:zz", "q", "s:9:q"):
        call("caption %s" % seg_id, "seg_caption", caps, seg_id)
    call("text anchor: identity, not uuid (the same uuid twice, the other copy found)", "text_anchor",
         turn([u("u1", 100), asst("d1", 101, "short"), asst("d1", 102, s1)]), [0, 1], s1, False)
    call("text anchor: the segment holds both copies", "text_anchor",
         turn([u("u1", 100), asst("d1", 101, "short"), asst("d1", 102, s1)]), [0, 1, 2], s1, False)
    call("text anchor: an empty segment searches the turn", "text_anchor",
         turn([u("u1", 100), asst("d1", 101, s1)]), [], s1, True)
    call("text anchor: no line, stub", "text_anchor", turn([u("u1", 100), asst("d1", 101, "x")]), [1], None, True)

    # ── refused at load where Python answers or raises
    add("REFUSE an atom text that is a list (Python reads its repr)",
        sess(tree(turn([att("a1", 100, ["x"])], ended=False, trigger=None))))
    add("REFUSE an atom text that is a number (Python reads str(5))",
        sess(tree(turn([u("u1", 100), att("a1", 101, 5)]))))
    add("REFUSE a placement value that is a number", sess(tree(open_turn), store(placements={pid: 5})))
    add("REFUSE a caption row that is a string (Python raises AttributeError)",
        sess(tree(open_turn), captions={cap_id: "gist"}))
    add("REFUSE a followupAt that is a string (Python raises TypeError)",
        sess(tree(t_out), store(nd("g1", distilledMt=5, followupAt="7", trail=oid))))
    add("REFUSE a briefedMt that is a numeric string (Python's int() reads it)",
        sess(tree(t_out), store(nd("g1", briefedMt="500", trail=oid))))
    add("REFUSE a followupAt that is a bool (Python compares True as 1)",
        sess(tree(t_out), store(nd("g1", distilledMt=1, followupAt=True, trail=oid))))
    add("REFUSE null nodes (the body raises on them)", sess(tree(open_turn), {"rompUuid": SID, "nodes": None}))
    add("REFUSE captions that are a list", sess(tree(open_turn), captions=[]))
    call("REFUSE a human record whose text is a dict", "human_prompt_record",
         tx(u("u1", 1, None), {"a": "<!-- romp-msg-id: m -->"}))
    call("REFUSE an outrun distilledMt past i64", "summary_outrun", nd("g", distilledMt=10 ** 20), [], best)
    return c


def python_tables():
    """Python's own regex tables, as the port's are written (layer5_edge.rs checks every code point against them)."""
    import _sre
    import re._casefix as casefix
    import unicodedata
    n = sys.maxunicode + 1
    digit, word = re.compile(r"\d"), re.compile(r"\w")

    def ranges(pred):
        out = []
        for ch in range(n):
            if 0xD800 <= ch <= 0xDFFF or not pred(ch):
                continue
            if out and out[-1][1] == ch - 1:
                out[-1][1] = ch
            else:
                out.append([ch, ch])
        return out
    lower = [[ch, _sre.unicode_tolower(ch)] for ch in range(n) if _sre.unicode_tolower(ch) != ch]
    return {"unicode": unicodedata.unidata_version,
            "decimal": ranges(lambda ch: bool(digit.match(chr(ch)))),
            "word": ranges(lambda ch: bool(word.match(chr(ch)))),
            "cased": ranges(lambda ch: bool(_sre.unicode_iscased(ch))),
            "lower": lower,
            "extraCases": sorted([k, list(v)] for k, v in casefix._EXTRA_CASES.items())}


def edge_main(out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer5-")
    try:
        l4, _dw, km, _rec, _root, bc = load_kernel(tmp)
        lines = Lines(km)
        cases, cov = answer_cases(km, l4, bc, edge_cases(km), lines)
        for cs in cases:
            if cs["name"].startswith("REFUSE ") and not (isinstance(cs["expect"], dict) and "raises" in cs["expect"]):
                cs["expect"] = {"answers": True}
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer5.py --edge-cases",
               "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "lineCoverage": lines.report(),
               "tables": python_tables(), "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
    kinds = {}
    for cs in cases:
        kinds[cs["kind"]] = kinds.get(cs["kind"], 0) + 1
    n_raise = sum(1 for cs in cases if isinstance(cs["expect"], dict) and "raises" in cs["expect"])
    n_refuse = sum(1 for cs in cases if cs["name"].startswith("REFUSE "))
    n_ans = sum(1 for cs in cases if cs["expect"] == {"answers": True})
    lc = doc["lineCoverage"]
    print("edge cases: %d (%s), %d where Python raises, %d the loader must refuse (Python answers %d of them); lines "
          "%d of %d run, missed %s; coverage %s -> %s (%.2f MB)" % (
              len(cases), ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items())), n_raise, n_refuse, n_ans,
              lc["ran"], lc["lines"], json.dumps(lc["missed"]), json.dumps(cov, sort_keys=True), out,
              os.path.getsize(out) / 1e6))
    return 0


# ───────────────────────────── the randomized check (--fuzz) ─────────────────────────────
# Random sessions: layer 3's random trees (dump_layer3.fuzz_session: atoms of every kind, uuids that repeat or are ""
# or null or absent, non-ASCII and Python-only whitespace, int and float times with ties, every trigger shape, command
# atoms, idle tails, attachments), decorated with what layer 5 reads: prompts carrying follow-up, system, nudge and
# postal markers, slash commands, reminders and comments, interrupts, bare-string and text-field prompts; assistant
# prose built from a pool of sentences; stores whose nodes' summaries are opening sentences taken from the tree's own
# prose and mangled (case, whitespace, the long s, the Kelvin sign, the dotless i, list markers, paragraphs), whose
# trails name the tree's segments (some at drifted times), with coverage stamps around the segments' times, handoffs,
# latches, userAsk and origin shapes; placements and captions keyed on the segments under every phase and drift, with
# retired, missing and cleared targets; cleared sets; heals. One case in five is a direct call. Seeded: a failure
# replays.
FZ_WS = " \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0 　"
FZ_SENTENCES = ("Fixed the cache invalidation bug in the loader.", "The tests pass now!", "Is the retry needed?",
                "Most of the work is done.", "We added the missing key check.", "Version 1.2 shipped. Next steps.",
                "e.g. the index is rebuilt on write.", "Ünïcödé prose → handled — fine.", "Short.",
                "Recap: the session store now keeps its index across appends and reads only the new bytes.",
                "日本語の説明文です。", "Done", "the cache, the loader, and the index all agree on the key now",
                "The \u01c6ungla digraph \u01c4 is handled.")
FZ_MARKS = ("<!-- romp-goal-id: %s -->", "<!-- romp-system -->", "<!-- romp-injected -->", "<!-- romp-msg-id: m1 -->",
            "<!-- romp-msg-kind: question -->", "<!-- note -->", "<!--", "-->", "<system-reminder>ctx</system-reminder>",
            "<task-notification>t</task-notification>", "<system-reminder>open", "[Request interrupted by user]",
            "/compact", "/model opus", "/Users/a/b.py", "/xा")


def fz_sentence(rng):
    s = rng.choice(FZ_SENTENCES)
    r = rng.random()
    if r < 0.15:
        s = s.upper()
    elif r < 0.25:
        s = s.replace("s", LONG_S).replace("k", KELVIN)
    elif r < 0.32:
        s = s.replace("i", DOTLESS)
    elif r < 0.42:
        s = s.replace(" ", rng.choice(FZ_WS) * rng.randint(1, 3))
    elif r < 0.47:
        s = s.replace("\u01c6", "\u01c5").replace("\u01c4", "\u01c5")
    return s


def fz_prompt(rng, ids):
    parts = []
    for _ in range(rng.choice((1, 1, 2, 3))):
        r = rng.random()
        if r < 0.45:
            m = rng.choice(FZ_MARKS)
            parts.append(m % rng.choice(ids + ["gone", "s:g9"]) if "%s" in m else m)
        else:
            parts.append(rng.choice(("please fix it", "fix the cache", "  ", "", "Ünïcödé ask " * rng.randint(1, 14),
                                     "x" * rng.choice((10, 139, 141)), fz_sentence(rng))))
    sep = rng.choice(("", " ", "\n", "\n\n", rng.choice(FZ_WS)))
    return sep.join(parts)


def fz_decorate(rng, tr, ids):
    for t in tr["turns"]:
        for a in t["atoms"]:
            m = a.get("message")
            if a.get("type") == "user" and isinstance(m, dict) and rng.random() < 0.6:
                r = rng.random()
                if r < 0.15:
                    m["content"] = fz_prompt(rng, ids)
                elif r < 0.22:
                    m["content"] = []
                    a["text"] = rng.choice((fz_prompt(rng, ids), "", None))
                else:
                    m["content"] = [{"type": "text", "text": fz_prompt(rng, ids)}]
            elif a.get("type") == "assistant" and isinstance(m, dict) and rng.random() < 0.6:
                text = " ".join(fz_sentence(rng) for _ in range(rng.choice((1, 2, 3, 6))))
                if rng.random() < 0.1:
                    m["content"] = text                  # a bare string: a text atom jd._atom_text reads as ""
                else:
                    m["content"] = [{"type": "text", "text": text}]
            elif a.get("type") == "attachment" and rng.random() < 0.7:
                a["text"] = rng.choice((fz_prompt(rng, ids), "", None))
            if rng.random() < 0.04:
                a["command"] = rng.choice(("/usage", True, "", 0, None))
            if a.get("type") == "user" and rng.random() < 0.3:
                a["author"] = rng.choice(("human", "human", "romp", "sdk", None))


def fz_opening(rng, tr):
    texts = [b.get("text") for t in tr["turns"] for a in t["atoms"] if a.get("type") == "assistant"
             for b in ((a.get("message") or {}).get("content") or [] if isinstance(a.get("message"), dict) else [])
             if isinstance(b, dict) and isinstance(b.get("text"), str)]
    if texts and rng.random() < 0.75:
        src = rng.choice(texts)
        words = src.split()
        k = rng.randrange(len(words)) if words else 0
        s = " ".join(words[k:k + rng.randint(3, 14)])
    else:
        s = fz_sentence(rng)
    r = rng.random()
    if r < 0.15:
        s = s.swapcase()
    elif r < 0.25:
        s = s.replace("s", LONG_S)
    elif r < 0.3:
        s = s.replace("K", KELVIN).replace("k", KELVIN)
    elif r < 0.4:
        s = s.replace(" ", rng.choice(FZ_WS) * rng.randint(1, 2))
    lead = rng.choice(("", "", "1. ", "- ", "* ", "١) ", "². ", "\n\n", " \n \n", "Intro.\n\n"))
    tail = rng.choice(("", " More words after.", "\nsecond line", "\n\nNext paragraph.", "?"))
    return lead + s + tail


def fz_store(rng, km, tr, l3, l2):
    turns = (tr or {}).get("turns") or []
    seams = None
    if turns and rng.random() < 0.4:             # seams aimed at the turns' segments (dump_layer3.fuzz_store)
        seams = (l3.fuzz_store(rng, km, l2, tr) or {}).get("seams")
    seg_ids = []
    for t in turns:
        try:
            seg_ids += [g["id"] for g in km._segs_seam(t, {"seams": seams})]
        except Exception:                        # noqa: BLE001 (a raising turn has no segments to aim at)
            pass
    times = [int(s.split(":")[-2]) for s in seg_ids if s.count(":") >= 2 and s.split(":")[-2].lstrip("-").isdigit()]
    times = [t for t in times if abs(t) < 2 ** 62]   # a "%d" of a huge float time is past i64, which the loader refuses

    def drift(s):
        p = s.split(":")
        if len(p) >= 3 and p[-2].lstrip("-").isdigit():
            p[-2] = str(int(p[-2]) + rng.choice((1, -3, 60)))
        return ":".join(p)

    def pick_seg():
        if seg_ids and rng.random() < 0.85:
            s = rng.choice(seg_ids)
            return drift(s) if rng.random() < 0.15 else s
        return rng.choice(("%s:1:nokey" % SID, "plain", "a:b"))

    n = rng.choice((0, 1, 2, 3, 4, 5, 6))
    ids = ["g%d" % i for i in range(n)]
    hand = {"peer": "s9", "goalId": "s9:g1"}
    uuids = [a.get("uuid") for t in turns for a in t["atoms"] if isinstance(a.get("uuid"), str)]
    nodes = {}
    for i, nid in enumerate(ids):
        d = {"id": nid}
        r = rng.random()
        if r < 0.4 or i == 0:
            d["parentId"] = None
        elif r < 0.9:
            d["parentId"] = rng.choice(ids[:i])
        else:
            d["parentId"] = rng.choice(ids + ["zz"])
        if rng.random() < 0.3:
            d["handoff"] = dict(hand)
        if rng.random() < 0.15:
            d["cleared"] = rng.choice((True, False))
        if rng.random() < 0.6:
            d["trail"] = [pick_seg() for _ in range(rng.choice((1, 1, 2, 3)))]
        for key in ("summary", "blockSummary"):
            if rng.random() < 0.55:
                d[key] = fz_opening(rng, tr) if tr else fz_sentence(rng)
            elif rng.random() < 0.1:
                d[key] = rng.choice(("", None, "   "))
        if rng.random() < 0.5:
            d["promptUuid"] = rng.choice(uuids + ["", "nope"]) if uuids else rng.choice(("", "nope"))
        if rng.random() < 0.3:
            d["askAnchor"] = rng.choice(("human", "absent", "scheduled", "machine", "other", "", None, 0))
        if rng.random() < 0.08:
            d["userAsk"] = rng.choice(({"text": "x"}, "x", None, {}))
        if rng.random() < 0.08:
            d["origin"] = {"peer": "s8"}
        base = rng.choice(times) if times else 1781000000
        for key in ("distilledMt", "briefedMt", "followupAt"):
            if rng.random() < 0.35:
                d[key] = rng.choice((0, None, base, base - 1, base + 1, base + 0.5, base - 0.5, float(base), -5.5,
                                     1e20, 0.5))
        nodes[nid] = d
    st = {"rompUuid": SID, "nodes": nodes}
    if seams is not None:
        st["seams"] = seams
    if rng.random() < 0.7:
        pl = {}
        for _ in range(rng.choice((0, 1, 2, 3, 5))):
            pl[pick_seg() + rng.choice(("", "#p", "#d", "#live"))] = rng.choice(ids + [None, "gone"]) if ids else \
                rng.choice((None, "gone"))
        st["placements"] = pl
    elif rng.random() < 0.3:
        st["placements"] = None
    return st, seg_ids, ids


def fz_session(rng, km, l3, l2, serial):
    tr = l3.fuzz_session(rng, km, l2, serial)
    ids0 = ["g%d" % i for i in range(4)]
    if tr:
        fz_decorate(rng, tr, ids0)
        if tr["turns"] and rng.random() < 0.5:
            tr["turns"][-1]["ended"] = rng.random() < 0.3
    st, seg_ids, ids = fz_store(rng, km, tr, l3, l2) if rng.random() < 0.9 else (None, [], [])
    caps = None
    if rng.random() < 0.85:
        caps = {}
        for _ in range(rng.choice((0, 1, 2, 3))):
            if seg_ids and rng.random() < 0.8:
                s = rng.choice(seg_ids)
                if rng.random() < 0.3:
                    p = s.split(":")
                    if len(p) >= 3 and p[-2].lstrip("-").isdigit():
                        p[-2] = str(int(p[-2]) + 5)
                    s = ":".join(p)
            else:
                s = "other:1:x"
            caps[s + rng.choice(("#p", "#p", ""))] = rng.choice(({"caption": "a gist"}, {"caption": ""},
                                                                {"caption": None}, {}, {"caption": "Ünï gist"},
                                                                {"caption": rng.choice((3, [1], True, 0, []))}
                                                                if rng.random() < 0.1 else {"caption": "a gist"}))
    healed = {}
    if ids and rng.random() < 0.1:
        x = rng.choice(ids)
        if (st["nodes"][x].get("parentId") or None) is None:
            healed[x] = [rng.choice([i for i in ids if i != x] + [None]), {"why": "healed"}]
    cleared = rng.sample(ids + ["gone", "g9"], rng.choice((0, 0, 1, 2))) if rng.random() < 0.5 else []
    if tr and tr["turns"] and st and rng.random() < 0.15:  # a follow-up to a card cleared out from under it
        target = rng.choice(ids + ["gone"])
        if target in st["nodes"] and rng.random() < 0.7:
            st["nodes"][target]["cleared"] = True
        elif rng.random() < 0.7:
            cleared.append(target)
        lt = tr["turns"][-1]
        t0 = lt["atoms"][-1].get("t") if lt["atoms"] else lt.get("t")
        t0 = t0 + 1 if isinstance(t0, (int, float)) and abs(t0) < 1e15 else 1781000000
        lt["atoms"].append(u("fu-%d" % serial, t0, "please continue <!-- romp-goal-id: %s -->" % target))
        lt["ended"] = rng.random() < 0.4
        if rng.random() < 0.2:
            lt["atoms"].append(idle(t0 + 1, t0 + 2))
    return {"sid": SID, "path": rng.choice(("p/" + SID, "p/" + SID, None)), "name": "s005", "color": COLOR,
            "live": rng.random() < 0.9, "tree": tr, "store": st, "captions": caps, "cleared": cleared,
            "healed": healed}


def fz_text(rng, n=None):
    chars = "abcXYZ019 .!?:-_/*)(\n\t 　\u001c" + LONG_S + KELVIN + DOTLESS + "İé→日ा²١"
    if n is None:
        n = rng.choice((0, rng.randint(1, 10), rng.randint(10, 60), rng.randint(190, 320)))
    return "".join(rng.choice(chars) for _ in range(n))


def fz_direct(rng):
    r = rng.random()
    if r < 0.18:
        return "opening_sentence", [rng.choice((None, fz_text(rng), fz_opening(rng, {"turns": []}),
                                                rng.choice(FZ_WS) + fz_text(rng)))]
    if r < 0.38:
        text = " ".join(fz_sentence(rng) for _ in range(rng.randint(1, 4)))
        words = text.split()
        k = rng.randrange(len(words))
        q = " ".join(words[k:k + rng.randint(1, 6)])
        q = rng.choice((q, q.swapcase(), q.replace("s", LONG_S), q.replace("i", DOTLESS), q.replace(" ", "  "),
                        fz_text(rng, 4), "", None))
        if rng.random() < 0.2:
            text = text.replace(" ", rng.choice(FZ_WS) * 2)
        return "locate_quote", [rng.choice((text, None, "")), q]
    if r < 0.48:
        return "split_reminders", [fz_prompt(rng, ["g1"])]
    if r < 0.58:
        return "postal_pairs", [rng.choice(("", " ")).join(rng.choice((
            "<!-- romp-msg-id: m%d -->" % rng.randint(1, 3), "<!-- romp-msg-kind: %s -->" % rng.choice(
                ("question", "delegate", "coordinate", "bogus")), "<!--romp-msg-id:x", "-->", "<!-- romp-msg-kind: x",
            fz_text(rng, 3))) for _ in range(rng.randint(1, 5)))]
    if r < 0.66:
        return "bare_slash", [rng.choice(("/", "/", "", " ")) + fz_text(rng, rng.randint(0, 8)) +
                              rng.choice(("", "", "\n", " x\n", "\t\n", "\n\n"))]
    if r < 0.72:
        return "followup_re", [rng.choice(("", "x ")) + "<!--" + rng.choice(("", " ", "　")) + "romp-goal-id:" +
                               fz_text(rng, rng.randint(0, 6)) + rng.choice(("-->", " -->", "--->", ""))]
    if r < 0.78:
        return "comment_strip", ["".join(rng.choice(("<!--", "-->", "x", " ", "\n", "-", ">")) for _ in
                                         range(rng.randint(0, 10)))]
    if r < 0.88:
        a = rng.choice((u("x", 1, fz_prompt(rng, ["g1"])), us("x", 1, fz_prompt(rng, ["g1"])),
                        att("x", 1, rng.choice((fz_prompt(rng, ["g1"]), "", None))),
                        att("x", 1, content=[{"type": "text", "text": fz_prompt(rng, ["g1"])}]),
                        tx(u("x", 1, None), rng.choice((fz_prompt(rng, ["g1"]), "", None)))))
        if rng.random() < 0.3:
            a["author"] = rng.choice(("human", None, "romp"))
        return "human_prompt_record", [a]
    node = {"id": "g"}
    for key in ("distilledMt", "briefedMt", "followupAt"):
        if rng.random() < 0.6:
            node[key] = rng.choice((0, None, 100, 100.5, 99.9, 150, -2.5, 1e20, 0.5))
    best = [["s:h", [None, False, rng.choice((0, 100, 100.5, 150, 0.0, 99))]]]
    return "summary_outrun", [node, [rng.choice((["s:1:h"], ["s:2:z"], None, []))], best]


def fuzz_specs(rng, km, n, seed):
    l4 = load_l4()
    l3 = l4.load_l3()
    l2 = l3.load_l2()
    specs = []
    for i in range(n):
        name = "fuzz seed %d case %d" % (seed, i)
        if rng.random() < 0.2:
            fn, args = fz_direct(rng)
            specs.append((name, "call", {"fn": fn, "args": args}))
        else:
            specs.append((name, "session", {"session": fz_session(rng, km, l3, l2, i)}))
    return specs


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


# romp-spike's edge-set summary line (main.rs, layer5_edge)
RUST_SUMMARY = re.compile(r"^layer5 edge set(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"values\), (\d+) refused at load \(every REFUSE case\), (\d+) differences in (\d+) cases ")


def fuzz_main(n, seed, plant, binary, out, with_lines):
    """Make `n` cases from `seed`, answer them in Python, and compare with `romp-spike layer5` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer5-fuzz-")
    try:
        t0 = time.monotonic()
        l4, _dw, km, _rec, _root, bc = load_kernel(tmp)
        specs = fuzz_specs(random.Random(seed), km, n, seed)
        lines = Lines(km) if with_lines else None
        cases, cov = answer_cases(km, l4, bc, specs, lines)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer5.py --fuzz %d --seed %d" % (n, seed),
               "constants": {"CITE_MIN_CHARS": km.jd.CITE_MIN_CHARS}, "coverage": cov, "cases": cases}
        path = out or os.path.join(tmp, "layer5_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        kinds = {}
        for cs in cases:
            kinds[cs["kind"]] = kinds.get(cs["kind"], 0) + 1
        nraise = sum(1 for cs in cases if isinstance(cs["expect"], dict) and "raises" in cs["expect"])
        print("fuzz seed %d: %d cases (%s) made and answered by Python in %.1f s, %.1f MB; Python raised on %d" % (
            seed, n, ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items())), py_s,
            os.path.getsize(path) / 1e6, nraise))
        print("  python: coverage %s" % json.dumps(cov, sort_keys=True))
        if lines is not None:
            lc = lines.report()
            print("  python: lines %d of %d run, missed %s" % (lc["ran"], lc["lines"], json.dumps(lc["missed"])))
        p = subprocess.run([binary, "layer5", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        out_lines = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, out_lines) if m]
        for line in out_lines:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer5: romp-spike layer5 exited %d without its summary line: %s" % (
                p.returncode, p.stderr.strip()[-2000:]), file=sys.stderr)
            return 2
        ncases, loaded, values, refused, diffs, differing = map(int, summary[0].groups())
        if ncases != n or (plant is None and (loaded != n or refused != 0 or diffs != 0 or p.returncode != 0)):
            print("fuzz seed %d: FAILED, %d differences over %d cases (%d loaded, %d refused at load)" % (
                seed, diffs, ncases, loaded, refused))
            return 1
        if plant is None:
            print("fuzz seed %d: OK, %d cases, %d values compared, 0 differences" % (seed, n, values))
            return 0
        print("fuzz seed %d, planted %s: %s, %d differences in %d of %d cases" % (
            seed, plant, "caught" if diffs else "NOT CAUGHT", diffs, differing, n))
        return 0 if diffs else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────── --tables ─────────────────────────────
def tables_source():
    """The Rust source of romp-views' pyre_tables.rs: Python's own `\\d`, `\\w`, case and ignore-case tables."""
    import _sre
    import re._casefix as casefix
    import unicodedata
    n = sys.maxunicode + 1
    t = python_tables()

    def lower_runs():
        out = []                                  # [first, last, step, delta]
        for ch in range(n):
            lo = _sre.unicode_tolower(ch)
            if lo == ch:
                continue
            d = lo - ch
            if out:
                f, last, st, dd = out[-1]
                if dd == d and ((st == 0 and ch - last in (1, 2)) or (st and ch - last == st)):
                    out[-1] = [f, ch, st or ch - last, dd]
                    continue
            out.append([ch, ch, 0, d])
        return out

    def pack(items, fmt, indent="    ", width=120):
        rows, cur = [], indent
        for it in items:
            s = fmt(it) + ", "
            if len(cur) + len(s.rstrip()) > width:
                rows.append(cur.rstrip())
                cur = indent
            cur += s
        if cur.strip():
            rows.append(cur.rstrip())
        return "\n".join(rows)

    rng = lambda r: "(0x%X, 0x%X)" % (r[0], r[1])
    extra = sorted(casefix._EXTRA_CASES.items())
    head = ("// Generated by `python3 tools/rust-spike/dump_layer5.py --tables` from Python %s (Unicode %s): do not edit.\n"
            "// Regenerate with the same command when Python's Unicode version moves; `layer5_edge.rs` checks every code\n"
            "// point against the tables Python writes into the layer-5 fixture.\n\n"
            % (platform.python_version(), unicodedata.unidata_version))
    return head + "\n".join([
        "/// The Unicode version these tables come from (Python's `unicodedata.unidata_version`).",
        "pub const UNICODE_VERSION: &str = \"%s\";\n" % unicodedata.unidata_version,
        "/// What `\\d` matches in a Python str regex (`Py_UNICODE_ISDECIMAL`, category Nd): inclusive ranges.",
        "pub const DECIMAL: &[(u32, u32)] = &[\n%s\n];\n" % pack(t["decimal"], rng),
        "/// What `\\w` matches in a Python str regex (`Py_UNICODE_ISALNUM` or `_`): inclusive ranges.",
        "pub const WORD: &[(u32, u32)] = &[\n%s\n];\n" % pack(t["word"], rng),
        "/// `_sre.unicode_iscased`: a code point whose simple lowercase or uppercase mapping is another code point.",
        "pub const CASED: &[(u32, u32)] = &[\n%s\n];\n" % pack(t["cased"], rng),
        "/// `_sre.unicode_tolower` (the simple lowercase mapping) as runs `(first, last, step, delta)`: every code point",
        "/// `first + k * step` up to `last` lowers to itself plus `delta` (step 0: `first` alone). Others lower to themselves.",
        "pub const LOWER: &[(u32, u32, u32, i32)] = &[\n%s\n];\n" % pack(
            lower_runs(), lambda r: "(0x%X, 0x%X, %d, %d)" % tuple(r)),
        "/// `re._casefix._EXTRA_CASES`: a lowercase code point to the others an ignore-case literal also accepts.",
        "pub const EXTRA_CASES: &[(u32, &[u32])] = &[\n%s\n];\n" % pack(
            extra, lambda kv: "(0x%X, &[%s])" % (kv[0], ", ".join("0x%X" % v for v in kv[1]))),
    ])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random cases with romp-spike layer5")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=PLANTS, help="--fuzz against romp-spike layer5 --plant NAME: the check passes "
                    "when the plant is caught")
    ap.add_argument("--bin", help="a built romp-spike for --fuzz (default: cargo build --release)")
    ap.add_argument("--out", help="keep the --fuzz cases in this file (default: a temporary file, removed)")
    ap.add_argument("--lines", action="store_true", help="--fuzz: also report the lines of the ported functions run")
    ap.add_argument("--tables", metavar="OUT", help="write romp-views' pyre_tables.rs (Python's regex tables) to OUT")
    a = ap.parse_args(argv)
    if a.tables:
        with open(a.tables, "w", encoding="utf-8", newline="") as f:
            f.write(tables_source())
        print("tables -> %s" % a.tables)
        return 0
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if a.fuzz is not None:
        if a.fuzz < 1:
            ap.error("--fuzz N needs N >= 1")
        return fuzz_main(a.fuzz, a.seed, a.plant, rust_binary(a.bin), a.out, a.lines)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT, or --fuzz N, or --tables OUT")
    first = os.path.realpath(a.dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        l4, dw, km, rec, root, bc = load_kernel(work)
        for d in a.dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer5: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            cov, memo, check, size = dump_one(dw, km, l4, bc, d)
            print("%s: %s; memo check %s; build check %d calls, %d compared, %d unmatched, %d mismatches %s -> %s "
                  "(%.1f MB)" % (os.path.basename(d), json.dumps(cov, sort_keys=True), json.dumps(memo, sort_keys=True),
                                 check["calls"], check["compared"], check["unmatched"], len(check["mismatches"]),
                                 json.dumps(check["byHelper"], sort_keys=True), os.path.join(d, OUT_NAME), size / 1e6))
            for m in check["mismatches"][:10]:
                print("  build mismatch: " + m)
            if check["mismatches"] or memo["agree"] != memo["calls"] or memo["faults"]:
                return 1
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer5: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
