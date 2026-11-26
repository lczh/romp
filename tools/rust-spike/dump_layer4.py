#!/usr/bin/env python3
"""Python's own answers for layer 4 of the Rust spike's port (rust/notes/s2-design.md section 2, "Store helpers";
rust/SPIKE.md step S4.4), for the Rust side to match with zero differences.

It loads the real kernel the way dump_layer1.py does (an empty state root, the bench guards) and, per alive session of
a dump, calls the real store helpers on the inputs the per-card build hands them: the session's goal store from
snapshot.json, the recorded _heal_session_tops and _peer_answered answers (the tuple the live helper returns), the live
row, the recorded _api_error answer, and layer 3's seg_uuid and seg_trig maps (kernel._feed_seg_walk with nothing held).
The body's own `children` build is taken from kernel._feed_session_entry's source (its _hidden and children
statements, compiled as a function), so the oracle builds it with the kernel's lines. Per session it records the
children, _agent_open_set, _parked_rows and _focus_open_top over lastNode; per node in store order jd._top_of,
_focus_open_top, _goal_awaiting_stamp_full (with the stamp's peer identities and awaiting rows), _newest_block_ev_t over
the node's blocked subtree, _open_leaves, _all_outstanding_delegated, _handoff_peer_identities and _awaiting_peer_items
over its handoff leaves, _handoff_card_fields, jd._block_ev_t, _block_since, _done_since, review_boundary,
_session_started_face, _node_log_rows, _node_anchor_uuids (its _node_anchor_last carried across sessions, cleared
first), jd.junk_quote and _landing_inputs (every state and column the body passes); and _login_refusal_label. Every
sid the dump names gets _name_of, _name_color and _peer_identity. `romp-spike layer4 DUMP` computes the same.

    python3 tools/rust-spike/dump_layer4.py DUMP_DIR [DUMP_DIR ...]   # writes DUMP_DIR/layer4.json
    python3 tools/rust-spike/dump_layer4.py --edge-cases OUT.json     # the synthetic cases (romp-spike layer4 OUT.json)
    python3 tools/rust-spike/dump_layer4.py --fuzz N [--seed S] [--plant NAME] [--bin ROMP_SPIKE] [--out FILE] [--lines]

A dump also gets a build check: one replay build of the dump (dump_world's harness) with the real helpers wrapped, and
every call the per-card body makes is compared with the oracle's answer for the same session, node and arguments (the
children it passes included). And it checks that the replay's peer facts past the registry (kernel._remotes, the postal
log's names) are empty, as the port's default `PeerWorld` is.

The worlds miss most branches (no awaiting stamp, no parked row, no agentTask, no born step, no heal, no review
advance, no cold anchor, no auth error, no registry miss), so the edge cases build them, and `--fuzz N` draws random
worlds and direct calls, answered here and compared by `romp-spike layer4`; any difference or refusal fails it. A case
named "REFUSE ..." is one the Rust loader must refuse. The edge set reports the lines of the 29 functions it ran (in
the fixture's `lineCoverage`), and so does `--fuzz` with `--lines`. Stdlib only.
"""
import argparse
import ast
import hashlib
import importlib.util
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
SCHEMA = "romp-spike-layer4/1"
EDGE_SCHEMA = "romp-spike-layer4-edge/1"
OUT_NAME = "layer4.json"
ABSENT_SID = "absent-sid"                     # no registry or store names it (layer4.rs ABSENT_SID)
LANDING_STATES = ("completed", "blocked", None)
LANDING_COLUMNS = ("", "working", "completed", "needs_input")
PLANTS = ("max-last", "int-round", "uuid-strict", "table-off", "parked-sorted")
MAX_NODE_DEPTH = 500                          # snapshot.rs MAX_NODE_DEPTH
# the functions layer 4 ports (module, name), the lines each run reaches are reported
FUNCS = [("kernel", n) for n in (
    "_agent_open_set", "_parked_rows", "_focus_open_top", "_goal_awaiting_stamp_full", "_peer_stamp_superseded",
    "_stamp_written_at", "_open_leaves", "_all_outstanding_delegated", "_handoff_card_fields", "_title_shaped",
    "_handoff_peer_identities", "_peer_identity", "_awaiting_item", "_awaiting_peer_items", "_newest_block_ev_t",
    "_session_started_face", "_node_log_rows", "_node_anchor_uuids", "_landing_inputs", "_login_refusal_label",
    "_name_color", "_name_of", "_names_parts")] + [("judge", n) for n in (
        "_top_of", "_block_ev_t", "_block_since", "_done_since", "review_boundary", "junk_quote")]


def load_l3():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer3", os.path.join(HERE, "dump_layer3.py"))
    l3 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l3)
    return l3


def raised(e):
    return {"raises": type(e).__name__, "arg": str(e)}


def plain(v):
    return json.loads(json.dumps(v))


# ───────────────────────────── the body's children, from the kernel's own source ─────────────────────────────
def body_children_fn(km):
    """kernel._feed_session_entry's `_hidden = ...`, `children = {}` and the `for nid, nd in nodes.items()` loop after
    it, compiled as `_l4_body_children(nodes, healed) -> (children, _hidden)` in the kernel's globals."""
    code = km._feed_session_entry.__code__
    src = open(code.co_filename, encoding="utf-8").read()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
              and n.name == "_feed_session_entry" and n.lineno == code.co_firstlineno)
    body = fn.body
    hidden = next(s for s in body if isinstance(s, ast.Assign) and len(s.targets) == 1
                  and isinstance(s.targets[0], ast.Name) and s.targets[0].id == "_hidden")
    i = next(i for i, s in enumerate(body) if isinstance(s, ast.Assign) and len(s.targets) == 1
             and isinstance(s.targets[0], ast.Name) and s.targets[0].id == "children"
             and isinstance(s.value, ast.Dict) and not s.value.keys)
    loop = body[i + 1]
    if not (isinstance(loop, ast.For) and ast.unparse(loop.iter) == "nodes.items()"
            and ast.unparse(loop.target) in ("(nid, nd)", "nid, nd")):
        raise SystemExit("dump_layer4: kernel._feed_session_entry's children loop moved; update body_children_fn")
    new = ast.parse("def _l4_body_children(nodes, healed):\n    pass\n").body[0]
    new.body = [hidden, body[i], loop, ast.parse("return children, _hidden").body[0]]
    mod = ast.Module(body=[new], type_ignores=[])
    ast.fix_missing_locations(mod)
    ns = {}
    exec(compile(mod, code.co_filename, "exec"), km.__dict__, ns)   # noqa: S102 (the kernel's own lines)
    return ns["_l4_body_children"], [hidden.lineno, loop.end_lineno]


def walk_loops(nodes, children, top):
    """Whether _goal_awaiting_stamp_full's stack walk from `top` (and the body's _subtree) meets a node twice: there
    Python never returns, so the oracle does not call it and records {"loops": true}."""
    stack, seen = [top], set()
    while stack:
        x = stack.pop()
        if not nodes.get(x):
            continue
        if x in seen:
            return True
        seen.add(x)
        stack.extend(children.get(x, []))
    return False


def subtree(children, root):
    """The body's _subtree(root), without its memo."""
    stack, acc = [root], []
    while stack:
        x = stack.pop()
        acc.append(x)
        stack.extend(children.get(x, []))
    return acc


# ───────────────────────────── one world ─────────────────────────────
class World:
    """Installs a world's names registry and peer facts in the kernel (as the replay scopes them) for its answers."""

    def __init__(self, km, names, remotes, postal):
        self.km, self.names, self.remotes, self.postal = km, names, remotes, postal

    def __enter__(self):
        km = self.km
        self.saved = (getattr(km._live_scope, "names", None), dict(km._remotes), km._postal_peer_names)
        km._live_scope.names = {k: list(v) for k, v in self.names.items() if v is not None}
        km._remotes.clear()
        km._remotes.update(json.loads(json.dumps(self.remotes)))
        postal = dict(self.postal)
        km._postal_peer_names = lambda: postal
        km._node_anchor_last.clear()
        return self

    def __exit__(self, *exc):
        km = self.km
        km._live_scope.names, remotes, km._postal_peer_names = self.saved
        km._remotes.clear()
        km._remotes.update(remotes)
        km._node_anchor_last.clear()
        return False


def session_answer(km, bc, s):
    """One session's answer, keys in the order romp-spike writes them (layer4.rs session_json). `s`: {store, healed,
    peerAnswered (absent: never recorded), liveRow, apiError (absent or a value), segUuid, segTrig}."""
    jd = km.jd
    store = s.get("store")
    if store is None:
        store = {"nodes": {}, "status": {}}       # the body's stand-in for a store it could not read
    nodes, status = store.get("nodes", {}), store.get("status", {})
    healed = s.get("healed") or {}
    answered = tuple(s["peerAnswered"]) if "peerAnswered" in s else None
    seg_uuid, seg_trig = dict(s.get("segUuid") or []), dict(s.get("segTrig") or [])
    children, hidden = bc(nodes, healed)
    ans = {"children": [[k, v] for k, v in children.items()], "hidden": sorted(hidden),
           "agentOpen": sorted(km._agent_open_set(nodes, children)),
           "parked": [[k, v] for k, v in km._parked_rows(nodes, children).items()],
           "focusTop": km._focus_open_top(nodes, store.get("lastNode"), status)}
    per = []
    for nid, nd in nodes.items():
        r = {"topOf": jd._top_of(nodes, nid), "focusTop": km._focus_open_top(nodes, nid, status)}
        loops = walk_loops(nodes, children, nid)
        if answered is not None:
            if loops:
                r["stamp"] = {"loops": True}
            else:
                sf = km._goal_awaiting_stamp_full(nodes, nid, children, answered_at=answered)
                peers = (sorted((km._peer_identity(p) for p in sf[3]), key=lambda d_: d_["name"])
                         if sf and sf[2] == "peer" and sf[3] else None)       # the body's _stamp_peers
                r["stamp"] = list(sf) if sf else None
                r["stampPeers"] = peers
                r["stampItems"] = km._awaiting_peer_items(peers)
        r["newestBlockEvT"] = {"loops": True} if loops else km._newest_block_ev_t(
            nodes, [x for x in subtree(children, nid) if nodes[x].get("blocked")])
        r["openLeaves"] = km._open_leaves(nodes, nid)
        r["allDelegated"] = km._all_outstanding_delegated(nodes, nid)
        hn = [x for x in r["openLeaves"] if isinstance(nodes[x].get("handoff"), dict)]
        r["delegPeers"] = km._handoff_peer_identities(nodes, hn)
        r["delegItems"] = km._awaiting_peer_items(r["delegPeers"])
        r["handoffCard"] = list(km._handoff_card_fields(nodes, nid))
        r["blockEvT"] = jd._block_ev_t(nd)
        r["blockSince"] = jd._block_since(nd)
        r["doneSince"] = jd._done_since(nd)
        r["reviewBoundary"] = jd.review_boundary(nd)
        r["startedFace"] = km._session_started_face(nodes, nid, healed)
        r["logRows"] = km._node_log_rows(nd, seg_uuid)
        r["anchors"] = list(km._node_anchor_uuids(nd, seg_trig, seg_uuid))
        r["junk"] = jd.junk_quote(nd.get("quote"))
        r["landing"] = [list(km._landing_inputs(st, col, nd)) for st in LANDING_STATES for col in LANDING_COLUMNS]
        per.append([nid, r])
    ans["nodes"] = per
    try:
        ans["loginLabel"] = km._login_refusal_label(s.get("liveRow") or {}, s.get("apiError"))
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        ans["loginLabel"] = raised(e)
    return ans


def names_answer(km, queries):
    return [[q, km._name_of(q or ""), km._name_color(q or ""), km._peer_identity(q)] for q in queries]


def world_answer(km, bc, w):
    with World(km, w.get("names") or {}, w.get("remotes") or {}, w.get("postalNames") or {}):
        sessions = [session_answer(km, bc, s) for s in w["sessions"]]
        return {"sessions": sessions, "names": names_answer(km, w.get("queries") or [])}


def new_cov():
    return {"sessions": 0, "nodes": 0, "stamps": 0, "stampLoops": 0, "stampPeers": 0, "parked": 0, "agentOpen": 0,
            "hidden": 0, "openLeaves": 0, "delegated": 0, "badges": 0, "badgeFromLabel": 0, "blockTimes": 0,
            "blockUnknown": 0, "reviewAdvanced": 0, "faces": 0, "logRows": 0, "logAnchors": 0, "anchorsWarm": 0,
            "junk": 0, "loginLabels": 0, "loginRaises": 0, "registryMisses": 0, "remoteNames": 0, "composites": 0,
            "focusTops": 0}


def count(cov, ans, w):
    for sa in ans["sessions"]:
        cov["sessions"] += 1
        cov["parked"] += len(sa["parked"])
        cov["agentOpen"] += len(sa["agentOpen"])
        cov["hidden"] += len(sa["hidden"])
        cov["focusTops"] += sa["focusTop"] is not None
        lab = sa["loginLabel"]
        cov["loginLabels"] += isinstance(lab, str) and lab != ""
        cov["loginRaises"] += isinstance(lab, dict)
        for _nid, r in sa["nodes"]:
            cov["nodes"] += 1
            st = r.get("stamp")
            cov["stamps"] += isinstance(st, list)
            cov["stampLoops"] += isinstance(st, dict)
            cov["stampPeers"] += bool(r.get("stampPeers"))
            cov["openLeaves"] += len(r["openLeaves"])
            cov["delegated"] += r["allDelegated"]
            badge = r["handoffCard"][0]
            cov["badges"] += badge is not None
            cov["badgeFromLabel"] += bool(badge and badge["peer"] and badge["peerHost"] and badge["color"] is None)
            cov["blockTimes"] += r["blockEvT"] is not None
            cov["blockUnknown"] += r["newestBlockEvT"] is None
            cov["reviewAdvanced"] += r["reviewBoundary"] is not None
            cov["faces"] += r["startedFace"] is not None
            cov["logRows"] += len(r["logRows"] or [])
            cov["logAnchors"] += sum(1 for x in r["logRows"] or [] if x["anchorUuid"] is not None)
            cov["anchorsWarm"] += r["anchors"][1] is not None
            cov["junk"] += r["junk"]
    for q, name, _c, ident in ans["names"]:
        cov["registryMisses"] += name is None
        cov["remoteNames"] += bool(ident["host"]) and ident["color"] is None and ":" not in ident["sid"]
        cov["composites"] += bool(ident["host"]) and name is None and ":" in ident["sid"]


# ───────────────────────────── the line tracer ─────────────────────────────
class Lines:
    """sys.settrace line events inside FUNCS (and their nested closures) and the compiled body-children function."""

    def __init__(self, km, extra=()):
        mods = {"kernel": km, "judge": km.jd}
        self.codes, self.labels = {}, []
        for m, n in FUNCS:
            self._add("%s.%s" % ("jd" if m == "judge" else "kernel", n), getattr(mods[m], n).__code__)
        for label, code in extra:
            self._add(label, code)
        self.hit = {label: set() for label in self.labels}

    def _add(self, label, code):
        self.labels.append(label)
        stack = [code]
        while stack:
            c = stack.pop()
            self.codes[id(c)] = (label, c)
            stack.extend(k for k in c.co_consts if hasattr(k, "co_lines"))

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
            missed = sorted(exe[label] - self.hit[label])
            total += len(exe[label])
            ran += len(exe[label] & self.hit[label])
            out[label] = missed
        return {"lines": total, "ran": ran, "missed": {k: v for k, v in out.items() if v}}


# ───────────────────────────── a dump ─────────────────────────────
def dump_queries(snap):
    """Every sid-shaped string the dump names, first occurrence kept (layer4.rs dump_queries)."""
    out, seen = [], set()

    def add(x):
        if isinstance(x, str) and x not in seen:
            seen.add(x)
            out.append(x)
    for r in snap["alive"]:
        add(r["sid"])
    for psid in snap["peers"]:
        add(psid)
    stores = [s.get("store") for s in snap["sessions"].values()] + [p.get("store") for p in snap["peers"].values()]
    for st in stores:
        for nd in ((st or {}).get("nodes") or {}).values():
            if isinstance(nd.get("handoff"), dict):
                add(nd["handoff"].get("peer"))
            if isinstance(nd.get("origin"), dict):
                add(nd["origin"].get("peer"))
            for p in nd.get("awaitingPeers") or []:
                add(p)
    add(ABSENT_SID)
    add("")
    return out


def dump_world_of(km, snap, trees):
    """The layer-4 world of a dump: per alive session its inputs, with layer 3's maps from the real walk."""
    sessions = []
    for r in snap["alive"]:
        sid = r["sid"]
        S = snap["sessions"][sid]
        rec = S["recorded"]
        tree = trees.get(sid)
        km._FEED_SEG_HELD.clear()
        w = km._feed_seg_walk(sid, None, tree["turns"] if tree else None, S["store"])
        s = {"sid": sid, "store": S["store"], "healed": rec.get("healSessionTops") or {},
             "liveRow": snap["liveMap"].get(sid), "segUuid": list(w.seg_uuid.items()),
             "segTrig": list(w.seg_trig.items())}
        if "peerAnswered" in rec:
            s["peerAnswered"] = rec["peerAnswered"]
        if "apiError" in rec:
            s["apiError"] = rec["apiError"]
        sessions.append(s)
    names = {}
    for psid, p in snap["peers"].items():
        if p["names"] is not None:
            names[psid] = p["names"]
    for sid, S in snap["sessions"].items():
        if S["names"] is not None:
            names[sid] = S["names"]
    return {"sessions": sessions, "names": names, "remotes": {}, "postalNames": {}, "queries": dump_queries(snap)}


# the helpers the build check wraps: name -> (module key, how the oracle's answer is found)
WRAPPED = ("_agent_open_set", "_parked_rows", "_focus_open_top", "_goal_awaiting_stamp_full", "_open_leaves",
           "_all_outstanding_delegated", "_handoff_card_fields", "_node_anchor_uuids", "_node_log_rows",
           "_session_started_face", "_landing_inputs", "_login_refusal_label", "_name_of", "_name_color",
           "_peer_identity", "jd.review_boundary", "jd._done_since", "jd._block_since")


def build_check(dw, km, bc, snap, trees, answer, world):
    """One replay build with the helpers wrapped: every call inside the per-card body is compared with the oracle's
    answer for that session and node (and, for _agent_open_set, the children it was handed). Returns counts."""
    by_sid = {s["sid"]: (s, a) for s, a in zip(world["sessions"], answer["sessions"])}
    name_rows = {q: (n, c, i) for q, n, c, i in answer["names"]}
    res = {"calls": 0, "compared": 0, "mismatches": [], "unmatched": 0, "byHelper": {}}
    cur = {"sid": None}
    saved = []

    def node_row(sid, nid):
        rows = dict(by_sid[sid][1]["nodes"])
        return rows.get(nid)

    def check(name, got, want):
        res["compared"] += 1
        res["byHelper"][name] = res["byHelper"].get(name, 0) + 1
        if plain(got) != plain(want):
            res["mismatches"].append("%s in %s: build %r, oracle %r" % (name, cur["sid"], plain(got), plain(want)))

    def compare(name, a, k, r):
        sid = cur["sid"]
        if sid is None or sid not in by_sid:
            res["unmatched"] += 1
            return
        s, sa = by_sid[sid]
        if name == "_agent_open_set":
            nodes, children = a
            got = [[x, v] for x, v in children.items()]
            if "children" not in cur.setdefault("seen", {}).setdefault(sid, {}):
                cur["seen"][sid]["children"] = True
                check("children", got, sa["children"])    # the body's first call hands it the children it built
            if plain(got) == plain(sa["children"]):
                check(name, sorted(r), sa["agentOpen"])
            else:
                res["unmatched"] += 1                  # the heal un-nest's second call, over other children
        elif name == "_parked_rows":
            check(name, [[x, v] for x, v in r.items()], sa["parked"])
        elif name == "_focus_open_top":
            focus = a[1]
            store = s["store"] or {}
            if focus == store.get("lastNode"):
                check(name, r, sa["focusTop"])
            else:
                row = node_row(sid, focus)
                if row is None:
                    res["unmatched"] += 1
                else:
                    check(name, r, row["focusTop"])
        elif name in ("_goal_awaiting_stamp_full", "_open_leaves", "_all_outstanding_delegated",
                      "_handoff_card_fields", "_session_started_face"):
            nid = a[1]
            row = node_row(sid, nid)
            key = {"_goal_awaiting_stamp_full": "stamp", "_open_leaves": "openLeaves",
                   "_all_outstanding_delegated": "allDelegated", "_handoff_card_fields": "handoffCard",
                   "_session_started_face": "startedFace"}[name]
            if row is None or key not in row:
                res["unmatched"] += 1
            else:
                check(name, list(r) if isinstance(r, tuple) else r, row[key])
        elif name in ("_node_anchor_uuids", "_node_log_rows", "jd.review_boundary", "jd._done_since",
                      "jd._block_since"):
            nd = a[0]
            row = node_row(sid, nd.get("id"))
            key = {"_node_anchor_uuids": "anchors", "_node_log_rows": "logRows", "jd.review_boundary":
                   "reviewBoundary", "jd._done_since": "doneSince", "jd._block_since": "blockSince"}[name]
            if row is None:
                res["unmatched"] += 1
            else:
                check(name, list(r) if isinstance(r, tuple) else r, row[key])
        elif name == "_landing_inputs":
            state, column, nd = a
            row = node_row(sid, nd.get("id"))
            if row is None or state not in LANDING_STATES or column not in LANDING_COLUMNS:
                res["unmatched"] += 1
            else:
                i = LANDING_STATES.index(state) * len(LANDING_COLUMNS) + LANDING_COLUMNS.index(column)
                check(name, list(r), row["landing"][i])
        elif name == "_login_refusal_label":
            check(name, r, sa["loginLabel"])
        elif name in ("_name_of", "_name_color", "_peer_identity"):
            q = a[0]
            if q not in name_rows:
                res["unmatched"] += 1
            else:
                check(name, r, name_rows[q][("_name_of", "_name_color", "_peer_identity").index(name)])

    def wrap(obj, attr, label):
        real = getattr(obj, attr)

        def w(*a, **k):
            if not cur.get("depth", 0) or cur.get("inner"):
                return real(*a, **k)              # outside the body, or a wrapped helper's own call: not the body's
            cur["inner"] = True
            try:
                r = real(*a, **k)
            finally:
                cur["inner"] = False
            res["calls"] += 1
            compare(label, a, k, r)
            return r
        saved.append((obj, attr, real))
        setattr(obj, attr, w)
        return real

    def run():
        for name in WRAPPED:
            if name.startswith("jd."):
                wrap(km.jd, name[3:], name)
            else:
                wrap(km, name, name)
        real_entry = km._feed_session_entry

        def entry(s_, ctx):
            cur["sid"], cur["depth"] = s_["sid"], cur.get("depth", 0) + 1
            try:
                return real_entry(s_, ctx)
            finally:
                cur["depth"] -= 1
                cur["sid"] = None
        saved.append((km, "_feed_session_entry", real_entry))
        km._feed_session_entry = entry
        km._node_anchor_last.clear()
        try:
            dw.replay_build(km, snap, trees)
        finally:
            for obj, attr, real in reversed(saved):
                setattr(obj, attr, real)
            km._node_anchor_last.clear()
    run()
    return res


def dump_one(dw, km, bc, out):
    snap, trees = dw.load_dump(out)
    if km._remotes or km._postal_peer_names():
        raise SystemExit("dump_layer4: the replay's peer facts are not empty (remotes %r)" % (km._remotes,))
    world = dump_world_of(km, snap, trees)
    ans = world_answer(km, bc, world)
    cov = new_cov()
    count(cov, ans, world)
    check = build_check(dw, km, bc, snap, trees, ans, world)
    doc = {"schema": SCHEMA, "world": snap["world"]["name"], "python": platform.python_version(),
           "coverage": cov, "buildCheck": {k: (v[:10] if k == "mismatches" else v) for k, v in check.items()},
           "buildMismatches": len(check["mismatches"]), "queries": world["queries"],
           "sessions": [{"sid": s["sid"], "answer": a} for s, a in zip(world["sessions"], ans["sessions"])],
           "names": ans["names"]}
    path = os.path.join(out, OUT_NAME)
    with open(path, "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return cov, check, os.path.getsize(path)


# ───────────────────────────── the edge cases ─────────────────────────────
U1 = "11111111-2222-3333-4444-0000000000a1"       # in the registry, with a color
U2 = "11111111-2222-3333-4444-0000000000a2"       # in the registry, no color
U3 = "11111111-2222-3333-4444-0000000000a3"       # not in the registry; owned by host h1, which names it
U4 = "11111111-2222-3333-4444-0000000000a4"       # not in the registry; owned by h1, unnamed there; in the postal log
U5 = "11111111-2222-3333-4444-0000000000a5"       # not in the registry; owned by h2 (whose row's host field is h1)
U6 = "11111111-2222-3333-4444-0000000000a6"       # known nowhere
NAMES = {U1: ["alpha", "/w/a", "#4a9eff"], U2: ["beta", "/w/b"], "s-red": ["red", "/w/r", "red"],
         "s-empty": ["", "/w/e", "#000000"], "s-short": ["short"], "h9:bob": ["bob-local", "/w", "#123456"],
         "s-null": None, "g-é": ["été", "/w", "#abcdef"]}
REMOTES = {"h1": {"host": "h1", "sids": [U3, U4, U3 + "\n"], "names": {U3: "gamma", U3 + "\n": "gamma-nl"}},
           "h2": {"host": "h1", "sids": [U5], "names": {U5: "never-read"}},
           "h3": {"host": None, "sids": None, "names": None}}
POSTAL = {U4: "h7:delta", U6 + "x": "h8:", "11111111-2222-3333-4444-0000000000b1": "h8:",
          "11111111-2222-3333-4444-0000000000b2": ":eps", "11111111-2222-3333-4444-0000000000b3": "",
          "11111111-2222-3333-4444-0000000000b4": "noColon"}
ID_QUERIES = [U1, U2, U3, U4, U5, U6, "peer:" + U1, "peer:" + U6, "peer:", "peer:peer:x", U3 + "\n", U6 + "\n",
              U6 + "\n\n", U6 + " ", U6.upper(), "h9:bob", "h9:" + U6, "h9:" + U6 + "\n", "h9:", ":tail", "a:b:c",
              "s-red", "s-empty", "s-short", "s-null", "g-é", "été-long-name-\U0001F600", "x" * 20, "",
              None, "11111111-2222-3333-4444-0000000000b1", "11111111-2222-3333-4444-0000000000b2",
              "11111111-2222-3333-4444-0000000000b3", "11111111-2222-3333-4444-0000000000b4", ABSENT_SID]


def nd(nid, parent=None, **kw):
    d = {"id": nid, "parentId": parent}
    d.update(kw)
    return d


def store(*nodes, last=None, status=None, **kw):
    st = {"nodes": {n.pop("_key", n["id"]): n for n in nodes}, "status": status or {}}
    if last is not None:
        st["lastNode"] = last
    st.update(kw)
    return st


ABSENT = object()


def sess(st, healed=None, answered=(0, {}), live=None, aerr=ABSENT, seg_uuid=(), seg_trig=()):
    s = {"store": st, "healed": healed or {}, "liveRow": live, "segUuid": [list(x) for x in seg_uuid],
         "segTrig": [list(x) for x in seg_trig]}
    if answered is not ABSENT:
        s["peerAnswered"] = list(answered)
    if aerr is not ABSENT:
        s["apiError"] = aerr
    return s


def world(*sessions, names=None, remotes=None, postal=None, queries=(), reg=False):
    """A world; the shared registry and peer facts (NAMES, REMOTES, POSTAL) only with `reg` (the cases that name
    peers), else empty unless given."""
    return {"sessions": list(sessions), "names": (NAMES if reg else {}) if names is None else names,
            "remotes": (REMOTES if reg else {}) if remotes is None else remotes,
            "postalNames": (POSTAL if reg else {}) if postal is None else postal, "queries": list(queries)}


def row(kind, ev_t=ABSENT, at=ABSENT, **kw):
    r = {"kind": kind}
    if ev_t is not ABSENT:
        r["ev_t"] = ev_t
    if at is not ABSENT:
        r["at"] = at
    r.update(kw)
    return r


def chain(n, prefix="c"):
    return [nd("%s%d" % (prefix, i), None if i == 0 else "%s%d" % (prefix, i - 1)) for i in range(n)]


def edge_cases():
    """(name, kind, payload)."""
    c = []

    def W(name, *sessions, **kw):
        c.append((name, "world", {"world": world(*sessions, **kw)}))

    def one(name, *nodes, **kw):
        sk = {k: kw.pop(k) for k in ("healed", "answered", "live", "aerr", "seg_uuid", "seg_trig") if k in kw}
        wk = {k: kw.pop(k) for k in ("reg",) if k in kw}
        W(name, sess(store(*nodes, **kw), **sk), **wk)

    # names and the identity ladder
    W("identity ladder over every query shape", queries=ID_QUERIES, reg=True)
    W("no peer facts: the ladder ends at the stubs", names={}, remotes={}, postal={}, queries=ID_QUERIES)
    W("no session, no store", sess(None, answered=ABSENT), queries=[U1], reg=True)

    # the body's children: heal, hidden, a born orphan
    one("heal nests a top; a hidden top is dropped; a born orphan is a root; a plain orphan keeps its key",
        nd("a"), nd("b", "gone", born={"why": "started", "parentText": "the ask"}), nd("c", "gone"), nd("d"),
        nd("e", "a"), nd("f"), nd("g", "", born={}),
        healed={"d": ["a", {"why": "healed", "kind": "session"}], "f": [None, {"hidden": True, "why": "skill"}],
                "e": ["", {"why": "heal with an empty parent"}], "zz": ["a", {"hidden": 1}]})
    one("a born orphan healed under a missing parent is a root",
        nd("a", "gone", born={"why": "w"}), healed={"a": ["gone2", {"why": "h"}]})
    one("a hidden flag that is falsy keeps the node", nd("a"), nd("b"),
        healed={"a": [None, {"hidden": 0}], "b": [None, {"hidden": ""}]})

    # _agent_open_set and _open_leaves
    one("an open to-do under a flat-done umbrella pierces the done marker",
        nd("t", nodeComplete=True), nd("x", "t", nodeComplete=True, agentTask={"status": "open"}),
        nd("y", "t", nodeComplete=True), nd("z", "x", nodeComplete=True), nd("w", "t", cleared=True,
                                                                              agentTask={"status": "open"}))
    one("agentTask shapes: falsy, done, a number status, nested open",
        nd("t"), nd("a", "t", agentTask=None), nd("b", "t", agentTask={}), nd("c", "t", agentTask=False),
        nd("d", "t", agentTask={"status": "done"}), nd("e", "t", agentTask={"status": 5}),
        nd("f", "t", nodeComplete=True), nd("g", "f", nodeComplete=True), nd("h", "g", agentTask={"status": "open"}),
        nd("i", "t", agentTask=[]), nd("j", "t", agentTask=""))
    one("open leaves in document order; a flat top is its own leaf; a done top has none",
        nd("t"), nd("b", "t"), nd("a", "t"), nd("a1", "a"), nd("a2", "a", nodeComplete=True),
        nd("c", "t", cleared=True),
        nd("flat"), nd("done", nodeComplete=True), nd("dk", "done"))
    one("every open leaf a handoff: delegated; one own leaf: not",
        nd("t"), nd("h1", "t", handoff={"peer": U1}), nd("h2", "t", handoff={"peer": U3}), nd("h3", "t", handoff={},
                                                                                             nodeComplete=True),
        nd("u"), nd("u1", "u", handoff={"peer": U2}), nd("u2", "u"), nd("v", handoff=None), nd("w", handoff={}),
        reg=True)
    one("a parentId cycle: the stamp walk loops, the cycle-safe readers answer",
        nd("a", "b", awaitingWhy="w", awaitingAt=5), nd("b", "a", blocked=True, log=[row("block", 3)]),
        nd("c", "a"), nd("r"), nd("s", "s"), last="c", status={"r": "working"})
    one("a node whose parent is missing is not a loop", nd("o", "missing", awaitingWhy="w", awaitingAt=1),
        nd("p", "o", blocked=True))

    # _parked_rows
    one("a younger sibling's handoff leapfrogs an older quiet row",
        nd("top"), nd("z", "top", t=1), nd("h", "top", t=2), nd("hk", "h", handoff={"peer": U1}, t=0),
        nd("a", "top", t=0.5), nd("b", "top", t=2.0), nd("c", "top"), nd("d", "top", t=0),
        nd("e", "top", t=1, log=[row("note", synth=True)]), nd("f", "top", t=1, log=[row("note", synth=False)]),
        nd("g", "top", t=1, blocked=True), nd("i", "top", t=1, nodeComplete=True), nd("j", "top", t=1, cleared=True),
        nd("k", "top", t=1, handoff={"peer": U2}), nd("l", "top", t=1), nd("l1", "l", log=[row("x")]))
    one("roots leapfrogged too, two younger handoffs count two, children map order",
        nd("q", "p2", t=5), nd("r1", t=1), nd("r2", t=3, handoff={"peer": U1}), nd("r3", t=4, handoff={"peer": U2}),
        nd("p2"), nd("q2", "p2", t=9, handoff={"peer": U1}), nd("r0", t=1.0))
    one("a sibling inside a parentId cycle: the subtree test meets a node twice and goes on",
        nd("h", "c1", t=3, handoff={"peer": U1}), nd("c1", "c2", t=1), nd("c2", "c1", t=1), nd("s", "c1", t=2))
    one("equal times do not leapfrog; a cycle under a sibling is safe",
        nd("p"), nd("a", "p", t=5), nd("b", "p", t=5.0, handoff={"peer": U1}), nd("c", "p", t=6), nd("c1", "c2"),
        nd("c2", "c1"), nd("d", "p", t=7), nd("d1", "d", handoff={"peer": U2}), nd("d2", "d1"), nd("d3", "d2"))

    # _focus_open_top and jd._top_of
    for st_name, st in (("working", "working"), ("completed", "completed"), ("cleared", "cleared"),
                        ("blocked", "blocked"), ("absent", None)):
        one("focus under a root whose status is %s" % st_name, nd("r"), nd("k", "r"), nd("k2", "k"), last="k2",
            status={} if st is None else {"r": st})
    one("focus on a missing node, an orphan, a cycle, an empty key", nd("o", "gone"), nd("a", "b"), nd("b", "a"),
        nd("", None, _key="", text="the empty key"), nd("e", ""), last="o")
    one("focus absent", nd("r"), status={"r": "working"})
    one("focus null", nd("r"), last=None, lastNode=None)
    one("focus is the root", nd("r"), last="r")

    # _goal_awaiting_stamp_full, _peer_stamp_superseded, _stamp_written_at
    one("no stamp", nd("t"), nd("a", "t"))
    one("stamps tie on the anchor and break on the strings",
        nd("t"), nd("a", "t", awaitingWhy="waiting on b", awaitingAt=5, awaitingKind="job"),
        nd("b", "t", awaitingWhy="waiting on a", awaitingAt=5.0, awaitingKind="peer", awaitingPeers=[U1]),
        nd("c", "t", awaitingWhy="waiting on b", awaitingAt=5, awaitingKind="agents"))
    one("a full tie keeps the first the stack meets (the tag rides): c1, then b, then a",
        nd("t"), nd("a", "t", awaitingWhy="w", awaitingAt=5.0), nd("b", "t", awaitingWhy="w", awaitingAt=5),
        nd("c", "t"), nd("c1", "c", awaitingWhy="w", awaitingAt=5))
    one("kind None against kind \"\" and peers lists compare",
        nd("t"), nd("a", "t", awaitingWhy="w", awaitingAt=5, awaitingKind=None, awaitingPeers=["b"]),
        nd("b", "t", awaitingWhy="w", awaitingAt=5, awaitingKind="", awaitingPeers=["a", "z"]),
        nd("c", "t", awaitingWhy="w", awaitingAt=5, awaitingPeers=["a"]),
        nd("d", "t", awaitingWhy="w", awaitingAt=5, awaitingPeers=[]))
    one("rolledUp, an empty why and a falsy anchor",
        nd("t"), nd("a", "t", awaitingWhy="rolled", awaitingAt=9, rolledUp=True),
        nd("b", "t", awaitingWhy="", awaitingAt=9), nd("c", "t", awaitingWhy="no anchor", awaitingAt=0),
        nd("d", "t", awaitingWhy="null anchor", awaitingAt=None), nd("e", "t", awaitingWhy="absent anchor"),
        nd("f", "t", awaitingWhy="rolled falsy", awaitingAt=-1, rolledUp=0))
    per = {U1: 50, U2: 70.5, "peer:h1:x": 80}
    one("a peer answer supersedes a peer and a kindless stamp written before it",
        nd("t"), nd("a", "t", awaitingWhy="peer wait", awaitingAt=40, awaitingKind="peer"),
        nd("b", "t", awaitingWhy="kindless", awaitingAt=41),
        nd("c", "t", awaitingWhy="job stands", awaitingAt=30, awaitingKind="job"),
        nd("d", "t", awaitingWhy="pair p1", awaitingAt=40, awaitingKind="peer", awaitingPeers=[U1]),
        nd("e", "t", awaitingWhy="pair p3", awaitingAt=40, awaitingKind="peer", awaitingPeers=[U6]),
        nd("f", "t", awaitingWhy="pair both", awaitingAt=60, awaitingKind="peer", awaitingPeers=[U1, U2]),
        nd("g", "t", awaitingWhy="written late", awaitingAt=40, log=[row("awaiting", at=65)]),
        nd("h", "t", awaitingWhy="lift ignored", awaitingAt=40, log=[row("awaiting", at=99, lift=True),
                                                                   row("awaiting", 41, at=0)]),
        nd("i", "t", awaitingWhy="ev_t used", awaitingAt=10, log=[row("awaiting", 61), row("block", at=200)]),
        answered=(60, per))
    one("the answered time ties the write time: not superseded",
        nd("t"), nd("a", "t", awaitingWhy="tie", awaitingAt=60), nd("b", "t", awaitingWhy="tie float",
                                                                   awaitingAt=59, log=[row("awaiting", at=60.0)]),
        answered=(60, {}))
    one("no answer: nothing superseded", nd("t", awaitingWhy="w", awaitingAt=3), answered=(0, {}))
    one("the stamp's peers name identities (sorted by name) and awaiting rows",
        nd("t"), nd("a", "t", awaitingWhy="w", awaitingAt=5, awaitingKind="peer",
                     awaitingPeers=[U3, "peer:" + U1, U6, "h9:bob", U2, ""]), reg=True)
    one("an unrecorded peer answer: no stamp asked", nd("t", awaitingWhy="w", awaitingAt=5), answered=ABSENT)
    one("a deep stamp under a healed child", nd("t"), nd("u"), nd("u1", "u", awaitingWhy="deep", awaitingAt=7),
        healed={"u": ["t", {"why": "healed"}]})

    # block and done times
    one("block rows: ev_t, its at fallback, falsy times skipped, ties keep the first",
        nd("a", log=[row("block", 0, at=7), row("block", None, at=7.0), row("block", 0, at=0), row("done", 99),
                     row("block")]),
        nd("b", log=[row("block", 5), row("block", at=5.0)]), nd("c", log=[row("block", 5.0), row("block", 5)]),
        nd("d", t=3, mt=0), nd("e", t=0, mt=0), nd("f", t=4.5), nd("g", mt=8, t=2, log=[row("unblock", 9)]),
        nd("h", mt=8, log=[row("block", 0, at=0), row("block", None, at=-0.0), row("block", at=None)]))
    one("done rows: a timeless one counts as 0, not mt; ties; no rows fall to mt or t",
        nd("a", mt=9, t=1, log=[row("done")]), nd("b", mt=9, log=[row("done", 0, at=4), row("done", None, at=4.0)]),
        nd("c", mt=0, t=6.5), nd("d"), nd("e", mt=3, log=[row("done", -2)]))
    one("newest block over a subtree: one unknown makes it None; none blocked is 0",
        nd("t", blocked=True, log=[row("block", 5)]), nd("a", "t", blocked=True, log=[row("block", 7.0)]),
        nd("b", "t", blocked=True, log=[row("block", at=7)]), nd("u", blocked=True, log=[row("block", 1)]),
        nd("u1", "u", blocked=True), nd("v"), nd("v1", "v", blocked=False, log=[row("block", 3)]))

    # review_boundary
    for name, n in (
            ("advanced past an older deltaSince", nd("a", deltaSince=5, distilledMt=10, summary="s",
                                                     log=[row("reopen", 11)])),
            ("advanced with no deltaSince", nd("a", distilledMt=10, summary="s", log=[row("reopen", 10.5)])),
            ("advanced over a null deltaSince", nd("a", deltaSince=None, distilledMt=10.0, summary="s",
                                                   log=[row("reopen", 11)])),
            ("not past an equal deltaSince (tags differ)", nd("a", deltaSince=10.0, distilledMt=10, summary="s",
                                                               log=[row("reopen", 11)])),
            ("a blank summary", nd("a", deltaSince=5, distilledMt=10, summary=" \x1c　", log=[row("reopen", 11)])),
            ("no summary", nd("a", deltaSince=5, distilledMt=10, log=[row("reopen", 11)])),
            ("a reopen at the watermark is not after it", nd("a", deltaSince=5, distilledMt=10, summary="s",
                                                              log=[row("reopen", 10.0), row("reopen", at=50)])),
            ("a zero watermark", nd("a", deltaSince=5, distilledMt=0, summary="s", log=[row("reopen", 11)])),
            ("a null watermark keeps a float deltaSince", nd("a", deltaSince=5.5, distilledMt=None)),
            ("a reopen without ev_t", nd("a", distilledMt=-3, summary="s", log=[row("reopen", at=9)])),
            ("only deltaSince", nd("a", deltaSince=0))):
        one("review boundary: " + name, n)

    # _session_started_face
    one("started faces: own born, healed born, parent text fallbacks",
        nd("p", text="the parent ask"), nd("a", "p", born={"why": "own", "parentText": "named"}),
        nd("b", "p", born={"why": None, "parentText": ""}), nd("c", "p", born={"parentText": None}),
        nd("d", "p"), nd("e", "gone", born={"why": "orphan"}), nd("f", "p", born={}),
        nd("g", "q", born={"why": "empty parent text"}), nd("q", text=""), nd("h", "", born={"why": "x"}),
        nd("i", "p", born=None), nd("j", "p"),
        healed={"d": [None, {"why": "healed", "parentText": "from heal"}], "i": ["p", {"why": "heal of a null born"}],
                "j": ["p", {}], "e": ["p", {"why": "never read: own born wins"}]})

    # _node_log_rows
    seg = "sx:100:abcd1234"
    one("log rows: the newest eight, their anchors by seg key, nulls and absents",
        nd("a", log=[row("block", i, at=i * 10, src="judge", why="w%d" % i, seg=seg if i % 2 else "sx:99:abcd1234")
                     for i in range(11)]),
        nd("b", log=[row("done"), {"kind": None, "src": None, "why": None, "seg": None, "at": None, "ev_t": None},
                     row("x", seg=""), row("y", seg="nokey"), row("z", seg="sx:1:missing"), {}]),
        nd("c", log=[]), nd("d"),
        seg_uuid=[("sx:abcd1234", "w-1"), ("nokey", "w-nokey"), ("sx:missing", None)])

    # _node_anchor_uuids
    st_ = "sa:1:h1", "sa:2:h2", "sa:3:h3"
    seg_u = [("sa:h1", "w1"), ("sa:h2", "w2"), ("sa:h4", ""), ("sa:h5", None)]
    seg_t = [("sa:h1", "p1"), ("sa:h2", ""), ("sa:h3", "p3")]
    one("anchors: stored prompt, derived prompt, junk quote, warm and cold work, summaryAnchor",
        nd("a", trail=[st_[0], st_[1]], promptUuid="stored"), nd("b", trail=[st_[0]], promptUuid=""),
        nd("c", trail=[st_[1], st_[2]]), nd("d", trail=[st_[2]], summaryAnchor="sa-d"),
        nd("e", trail=[], summaryAnchor=None), nd("f", trail=["sa:4:h4"], quote="retry"),
        nd("g", trail=[st_[0]], quote="Continue."), nd("h", trail=[st_[2]], quote="/compact", promptUuid="pp"),
        nd("i", trail=["sa:5:h5"], summaryAnchor="sa-i"), nd("", None, _key="k-empty", trail=[st_[0]]),
        nd("j", trail=["", st_[0]]),
        seg_uuid=seg_u, seg_trig=seg_t)
    cold = {"seg_uuid": [], "seg_trig": []}
    W("anchors across sessions: a warm resolve serves a later cold lookup of the same id",
      sess(store(nd("g1", trail=[st_[0]], quote="go"), nd("g2", trail=[st_[1]], promptUuid="pq"),
                 nd("g3", trail=[st_[0]])), seg_uuid=seg_u, seg_trig=seg_t),
      sess(store(nd("g1", trail=["sb:9:zz"], summaryAnchor="never"), nd("g2", trail=[], promptUuid=""),
                 nd("g3", trail=["sb:9:zz"], promptUuid="own", quote="ok")), **cold),
      sess(store(nd("g2", trail=[st_[2]]), nd("g3", trail=[st_[1]])), seg_uuid=[("sa:h3", "w3-new")],
           seg_trig=[("sa:h2", "p2-new")]),
      sess(store(nd("g2", trail=[]), nd("g3", trail=[])), **cold))
    W("anchors: an empty work anchor is warm (`is not None`), remembered and served to a later cold lookup",
      sess(store(nd("g4", trail=["sa:4:h4"])), seg_uuid=seg_u, seg_trig=seg_t),
      sess(store(nd("g4", trail=[], summaryAnchor="sa-g4")), **cold))
    W("anchors: the same id twice in one store (two keys) resolves in store order",
      sess(store(nd("dup", _key="k1", trail=[st_[0]]), nd("dup", _key="k2", trail=["zz:1:none"]),
                 nd("dup", _key="k3", trail=[st_[1]], promptUuid="x")), seg_uuid=seg_u, seg_trig=seg_t))

    # jd.junk_quote
    for q in ("retry", "  Keep   GOING!! ", "Okay…", "okay…!?.", "oK", "oK.", "OK　", "/compact",
              "/compact now", "/", " / x", "continue from where you left off", "please\ncontinue", "", None, "okk",
              "y", "yes please", "ok\x1c", "İ", "K", "go ahead...", "…"):
        one("junk quote %r" % (q,), nd("a", quote=q))

    # _handoff_card_fields
    for name, n in (
            ("registry peer, label present (the registry wins)",
             nd("h", text="↪ delegated to s003: update the client", handoff={"peer": U1})),
            ("registry miss: the label names host and peer",
             nd("h", text="↪ delegated to far:gamma: the work", handoff={"peer": U6})),
            ("registry miss: a label without a host keeps the identity's host",
             nd("h", text="↪ delegated to plain: the work", handoff={"peer": U3})),
            ("registry miss: a label whose name tail is empty",
             nd("h", text="↪ delegated to host:: the work", handoff={"peer": U6})),
            ("the work is (work): why, then summary, then the de-arrowed text",
             nd("h", text="↪ delegated to x: (work)", handoff={"peer": U6}, why="  the why  ", summary="sum")),
            ("an empty work and a blank why: the summary",
             nd("h", text="↪ delegated to x: ", handoff={"peer": U6}, why=" \x1c", summary=" the summary. More here")),
            ("no label: the de-arrowed text",
             nd("h", text="  ↪ handed over: something", handoff={"peer": U6})),
            ("no label and nothing else: the text kept as is",
             nd("h", text="↪ ", handoff={"peer": U6})),
            ("no text at all", nd("h", handoff={"peer": U6})),
            ("a long why is title-shaped",
             nd("h", text="↪ delegated to x: (work)", handoff={"peer": U6},
                why="First line of the why,\nwhich runs on. " + "word " * 40)),
            ("a peer:-prefixed peer that resolves", nd("h", text="t", handoff={"peer": "peer:" + U2})),
            ("a peer of just peer:", nd("h", text="↪ delegated to : x", handoff={"peer": "peer:"})),
            ("a composite peer", nd("h", text="↪ delegated to h9:bob: work", handoff={"peer": "h9:bob"})),
            ("an empty peer: no badge", nd("h", text="↪ delegated to x: y", handoff={"peer": ""})),
            ("a null peer: no badge", nd("h", text="t", handoff={"peer": None})),
            ("an empty handoff: no badge", nd("h", text="t", handoff={})),
            ("a null handoff: no badge", nd("h", text="t", handoff=None)),
            ("separators before the arrow, a label over two lines",
             nd("h", text="\x1c　↪\x1fdelegated to a\nb: c\nd\n", handoff={"peer": U6})),
            ("a remote-named peer and its label", nd("h", text="↪ delegated to x:y: z", handoff={"peer": U3})),
            ("registry miss: the first \": \" ends the label's name, the work keeps the rest",
             nd("h", text="↪ delegated to far:gamma: step one: step two", handoff={"peer": U6}))):
        one("handoff card: " + name, n, reg=True)
    one("handoff peers: duplicates once, sorted by name, equal names in first-seen order",
        nd("t"), nd("a", "t", handoff={"peer": U6}), nd("b", "t", handoff={"peer": U1}),
        nd("c", "t", handoff={"peer": U6}),
        nd("d", "t", handoff={"peer": "z:" + U6}), nd("e", "t", handoff={"peer": "y:" + U6}),
        nd("f", "t", handoff={"peer": None}), nd("g", "t", handoff={"peer": "s-empty"}), reg=True)

    # _login_refusal_label
    good = {"authLogin": "L1", "authLoginLive": "L1", "authLabel": "Work login"}
    for name, live, aerr in (
            ("an auth error on the carried login", good, {"authErr": True}),
            ("a truthy authErr of another type", good, {"authErr": "401"}),
            ("an empty label falls to login", dict(good, authLabel=""), {"authErr": 1}),
            ("a null label falls to login", dict(good, authLabel=None), {"authErr": 1}),
            ("the CLI used another login", dict(good, authLoginLive="L2"), {"authErr": True}),
            ("no login carried", dict(good, authLogin=""), {"authErr": True}),
            ("no live row", None, {"authErr": True}),
            ("not an auth error", good, {"authErr": False}),
            ("an empty error", good, {}),
            ("a null error", good, None),
            ("an absent error", good, ABSENT),
            ("a string error raises", good, "rate limited"),
            ("a list error raises", good, [1]),
            ("an int error raises", good, 7),
            ("a float error raises", good, 0.5),
            ("true raises", good, True),
            ("falsy non-dicts do not raise", good, [])):
        W("login label: " + name, sess(store(nd("a")), live=live, aerr=aerr))

    # direct calls
    for kind, iid, label, since, kw in (
            ("peer", "peer:x", "x", None, {}), ("agents", "a1", "  label \x1c", 1781100000.75, {"agentId": "ag1"}),
            ("commands", None, None, -0.5, {"detail": "pred"}), ("watches", "", "", 0, {"stoppable": True}),
            ("timer", 7, 8, 2.5, {"agentId": "", "detail": "", "stoppable": False}),
            ("peer", "i", "l", -2.7, {}), ("peer", "i", "l", 1e20, {}), ("peer", "i", "l", -1e20, {}),
            ("peer", "i", "l", 4611686018427387904, {}), ("peer", "i", "l", -0.0, {}), ("peer", "i", "l", 0.0, {}),
            ("peer", "i", "l", 9.223372036854775807e18, {}), ("peer", "i", "l", 0.9999999999999999, {}),
            ("task", "i", "l", 1, {}), ("", "i", "l", 1, {}), ("Peer", "i", "l", 1, {})):
        p = {"itemKind": kind, "iid": iid if not isinstance(iid, int) else None, "label": label if not
             isinstance(label, int) else None, "since": since}
        p.update(kw)
        c.append(("awaiting item %s since %r" % (kind or "''", since), "item", p))
    for text, cap in (("e.g. this one. And more", 120), ("Short. Then more", 120), ("a\n\x1cb  c", 120),
                      ("a\r b  c", 120), ("x" * 118 + " yz", 120), ("x" * 120, 120), ("x" * 121, 120), (None, 120),
                      ("", 120), ("Twelve chars! then", 120), ("Twelve char!\nthen", 120), ("Thirteen ch!. x", 120),
                      ("ends with a stop.", 120), ("A stop then　space? yes", 120), ("A stop then\x1cx. y", 120),
                      ("é" * 130, 120), ("ab   cd  " * 30, 13), ("one two three", 1), ("x\ny", 2),
                      ("\U0001F600" * 5, 4), ("long words then. " * 3, 10), ("x" * 117 + " \x1c" + "y" * 5, 120),
                      ("x" * 118 + "\x1f" + "y" * 5, 120), ("x" * 118 + "\u3000" + "y" * 5, 120)):
        c.append(("title %r cap %d" % ((text or "")[:24], cap), "title", {"text": text, "cap": cap}))
    for answered, tup, n in (((60, {U1: 50}), True, nd("x", awaitingWhy="w", awaitingAt=40, awaitingPeers=[U1])),
                             (61.5, False, nd("x", awaitingWhy="w", awaitingAt=40, awaitingPeers=[U1])),
                             (None, False, nd("x", awaitingWhy="w", awaitingAt=40)),
                             (0, False, nd("x", awaitingWhy="w", awaitingAt=40)),
                             ((70, {U1: 75, U2: 75.0}), True, nd("x", awaitingAt=40, awaitingPeers=[U1, U2])),
                             ((70, {U1: 75.0, U2: 75}), True, nd("x", awaitingAt=40, awaitingPeers=[U1, U2])),
                             ((70, {}), True, nd("x", awaitingAt=40, awaitingPeers=[])),
                             ((70, {}), True, nd("x", awaitingAt=40, awaitingKind="timer")),
                             ((70, {}), True, nd("x", awaitingAt=40, awaitingKind="")),
                             (45.0, False, nd("x", awaitingAt=40, log=[row("awaiting", 50), row("awaiting", at=44)]))):
        c.append(("superseded %r" % (answered,), "superseded", {"node": n, "answered": list(answered) if tup else
                                                                answered, "tuple": tup}))
    sd = {"t": nd("t"), "a": nd("a", "t", awaitingWhy="w", awaitingAt=5), "b": nd("b", "a", awaitingWhy="w2",
                                                                                  awaitingAt=5.0)}
    for top, answered, tup in (("t", None, False), ("t", (9, {}), True), ("a", 6, False), ("missing", 0, False)):
        c.append(("stamp without children from %s" % top, "stampDefault",
                  {"nodes": sd, "top": top, "answered": list(answered) if tup else answered, "tuple": tup}))
    cyc = {"a": nd("a", "b", awaitingWhy="w", awaitingAt=1), "b": nd("b", "a")}
    c.append(("stamp without children in a cycle", "stampDefault", {"nodes": cyc, "top": "a", "answered": 0,
                                                                    "tuple": False}))
    for state in ("completed", "blocked", None, "working"):
        for column in ("", "working", "completed", "needs_input", "awaiting"):
            c.append(("landing %s %r" % (state, column), "landing",
                      {"state": state, "column": column, "node": nd("a", summary="", blockSummary="the brief")}))
    c.append(("landing with no lines", "landing", {"state": "completed", "column": "", "node": nd("a")}))

    # the loader's refusals
    one("REFUSE an agentTask that is a truthy string (Python raises AttributeError)", nd("a", agentTask="open"))
    one("REFUSE a born that is a string (Python ignores it)", nd("a", born="x"))
    one("REFUSE awaitingPeers that is a string (Python iterates its characters)",
        nd("a", awaitingWhy="w", awaitingAt=5, awaitingPeers="ab"))
    one("REFUSE an awaitingWhy that is a number", nd("a", awaitingWhy=5, awaitingAt=5))
    one("REFUSE a parent chain of 1,000 (Python raises RecursionError)", *chain(1000))
    # one past the limit (a chain of 501, a healed forest of 501, a cycle of 501) is refused too, where Python still
    # answers: pinned by the Rust unit tests (snapshot.rs, layer4.rs), kept out of this fixture for its size
    W("REFUSE an authLogin that is a number", sess(store(nd("a")), live={"authLogin": 5}, aerr={"authErr": True}))
    W("REFUSE a names entry holding null", names={U1: ["a", None]}, queries=[U1])
    W("REFUSE a heal answer of three parts", sess(store(nd("a")), healed={"a": [None, {}, 1]}))
    W("REFUSE a peer answer whose per-peer time is null",
      sess(store(nd("a", awaitingWhy="w", awaitingAt=5, awaitingPeers=[U1])), answered=(9, {U1: None})))
    c.append(("REFUSE a title cap of 0 (Python slices from the end)", "title", {"text": "abc", "cap": 0}))
    # the accepted side of the limits
    one("a parent chain at the limit", *chain(MAX_NODE_DEPTH))
    W("a healed forest at the limit",
      sess(store(*[nd("h%d" % i) for i in range(MAX_NODE_DEPTH)]),
           healed={"h%d" % i: ["h%d" % (i - 1), {"why": "heal"}] for i in range(1, MAX_NODE_DEPTH)}))
    return c


def answer_case(km, bc, name, kind, payload):
    case = {"name": name, "kind": kind}
    case.update(plain(payload))
    try:
        if kind == "world":
            case["expect"] = world_answer(km, bc, case["world"])
        elif kind == "item":
            kw = {k2: case[k1] for k1, k2 in (("agentId", "agent_id"), ("detail", "detail"),
                                              ("stoppable", "stoppable")) if k1 in case}
            case["expect"] = km._awaiting_item(case["itemKind"], case["iid"], case["label"], case["since"], **kw)
        elif kind == "title":
            case["expect"] = km._title_shaped(case["text"], case["cap"])
        elif kind == "junk":
            case["expect"] = km.jd.junk_quote(case["q"])
        elif kind == "superseded":
            ans = tuple(case["answered"]) if case["tuple"] else case["answered"]
            case["expect"] = km._peer_stamp_superseded(case["node"], ans)
        elif kind == "stampDefault":
            ans = tuple(case["answered"]) if case["tuple"] else case["answered"]
            if walk_loops(case["nodes"], _parents(case["nodes"]), case["top"]):
                case["expect"] = {"loops": True}
            else:
                sf = km._goal_awaiting_stamp_full(case["nodes"], case["top"], answered_at=ans)
                case["expect"] = list(sf) if sf else None
        elif kind == "landing":
            case["expect"] = list(km._landing_inputs(case["state"], case["column"], case["node"]))
        else:
            raise ValueError(kind)
    except Exception as e:                       # noqa: BLE001 (the raise is the answer recorded)
        case["expect"] = raised(e)
    return case


def _parents(nodes):
    children = {}
    for nid, n in nodes.items():
        children.setdefault(n.get("parentId"), []).append(nid)
    return children


BIG_EXPECT = 32768                            # an answer longer than this (the depth-limit cases) is kept as its digest


def shrink(cases):
    """Keep the fixture small: a REFUSE case's answer is only whether Python raised (the port never compares it), and
    an answer longer than BIG_EXPECT bytes is kept as the SHA-1 of json.dumps(answer), which is what romp-json writes
    for the port's answer. Returns how many REFUSE cases Python answers."""
    answers = 0
    for c in cases:
        e = c["expect"]
        if c["name"].startswith("REFUSE "):
            if not (isinstance(e, dict) and "raises" in e):
                c["expect"] = {"answers": True}
                answers += 1
            continue
        text = json.dumps(e, allow_nan=False)
        if len(text) > BIG_EXPECT:
            del c["expect"]
            c["expectSha1"] = hashlib.sha1(text.encode("ascii")).hexdigest()
            c["expectBytes"] = len(text)
    return answers


def answer_cases(km, bc, specs, lines=None):
    cases, cov = [], new_cov()
    for name, kind, payload in specs:
        if lines is not None:
            case = lines.run(lambda: answer_case(km, bc, name, kind, payload))
        else:
            case = answer_case(km, bc, name, kind, payload)
        if kind == "world" and not name.startswith("REFUSE ") and "raises" not in case["expect"]:
            count(cov, case["expect"], case["world"])
        cases.append(case)
    return cases, cov


def load_kernel(tmp):
    l3 = load_l3()
    l2 = l3.load_l2()
    l1 = l2.load_l1()
    dw = l1.load_dw()
    root, km, rec = l1.load_kernel(dw, tmp)
    sys.setrecursionlimit(1000)                  # Python's default: the depth at which the recursive helpers raise
    return dw, km, rec, root


def edge_main(out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer4-")
    try:
        _dw, km, _rec, _root = load_kernel(tmp)
        bc, span = body_children_fn(km)
        lines = Lines(km, [("kernel._feed_session_entry:children", bc.__code__)])
        cases, cov = answer_cases(km, bc, edge_cases(), lines)
        n_answers_refused = shrink(cases)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer4.py --edge-cases", "bodyChildrenLines": span,
               "coverage": cov, "lineCoverage": lines.report(), "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
    kinds = {}
    for c in cases:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    n_raise = sum(1 for c in cases if isinstance(c.get("expect"), dict) and "raises" in c["expect"])
    n_refuse = sum(1 for c in cases if c["name"].startswith("REFUSE "))
    n_digest = sum(1 for c in cases if "expectSha1" in c)
    lc = doc["lineCoverage"]
    print("edge cases: %d (%s), %d where Python raises, %d the loader must refuse (Python answers %d of them), %d "
          "answers kept as digests; lines %d of %d run, missed %s; coverage %s -> %s (%.2f MB)" % (
              len(cases), ", ".join("%d %s" % (n, k) for k, n in sorted(kinds.items())), n_raise, n_refuse,
              n_answers_refused, n_digest, lc["ran"], lc["lines"], json.dumps(lc["missed"]),
              json.dumps(cov, sort_keys=True), out, os.path.getsize(out) / 1e6))
    return 0


# ───────────────────────────── the randomized check (--fuzz) ─────────────────────────────
# Random worlds of one to three sessions over a shared registry and peer facts, and direct calls. Node ids repeat
# across sessions (bare g1, g2: the anchor table serves across them) and sometimes differ from their keys; parents
# form chains, orphans and two- or three-node cycles; times are ints and floats with ties in both tags, zeros, -0.0
# and absents; log rows of every kind with ev_t/at present, zero, null or absent, synth and lift flags; stamps with
# tied anchors and strings, peer kinds and peer lists, answered by tuples aimed at their peers; handoffs whose peers
# take every identity shape (registry, remote, postal, composite, "peer:", a uuid with a trailing newline) and texts
# around the label; quotes from the junk list, mangled; heals, hidden tops and born steps; live rows and api errors
# of every shape. Seeded: a failure replays.
FUZZ_WS = " \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\x85\xa0   　"
FUZZ_CHARS = ("abcdefghijklmnopqrstuvwxyz   ABCXYZ0123456789.,:;-_/()!?'\"éï→—…日"
              "\U0001F600​Kİ")
FUZZ_PEERS = (U1, U2, U3, U4, U5, U6, "peer:" + U1, "peer:" + U3, U3 + "\n", U6 + "\n", "h9:bob", "h9:" + U6,
              "h9:", ":t", "a:b:c", "s-red", "s-empty", "s-short", "g-é", "", "peer:", "x" * 12,
              "11111111-2222-3333-4444-0000000000b1", "11111111-2222-3333-4444-0000000000b2",
              "11111111-2222-3333-4444-0000000000b4", U6.upper())
FUZZ_JUNK = list(("retry", "try again", "continue", "keep going", "go", "go ahead", "ok", "okay", "k", "yes", "y",
                  "yeah", "sure", "proceed", "please continue", "carry on", "resume", "keep at it",
                  "continue from where you left off", "/compact", "/model x", "/", "oK", "K", "okk",
                  "fix the bug", "", None))
FUZZ_TIMES = (0, 0, 1, 5, 5, 5.0, 7, 7.5, 10, -0.0, -3, 1781100000, 1781100000.5, None, "absent")
FUZZ_KINDS = ("block", "block", "done", "done", "awaiting", "awaiting", "reopen", "settle", "unblock", None, "absent")
FUZZ_SEG_KEYS = ("fz:k1", "fz:k2", "fz:k3", "fz:k4", "fz:k5")


def fz_text(rng, n=None):
    if n is None:
        n = rng.choice((0, rng.randint(1, 8), rng.randint(5, 40), rng.randint(100, 140)))
    s = "".join(rng.choice(FUZZ_CHARS) for _ in range(n))
    r = rng.random()
    if r < 0.15:
        s = rng.choice(FUZZ_WS) + s + rng.choice(FUZZ_WS)
    elif r < 0.25:
        s = s.replace(" ", rng.choice(FUZZ_WS))
    elif r < 0.32:
        s = s[:13] + rng.choice(".!?") + rng.choice(FUZZ_WS + "x") + s[13:]
    return s


def fz_time(rng):
    t = rng.choice(FUZZ_TIMES)
    return t


def put(d, key, v):
    if not (isinstance(v, str) and v == "absent"):
        d[key] = v


def fz_row(rng, segs):
    r = {}
    put(r, "kind", rng.choice(FUZZ_KINDS))
    put(r, "ev_t", fz_time(rng))
    put(r, "at", fz_time(rng))
    if rng.random() < 0.2:
        r["synth"] = rng.choice((True, False, 1, 0, "", None))
    if rng.random() < 0.15:
        r["lift"] = rng.choice((True, False, 1, 0, None))
    if rng.random() < 0.4:
        r["seg"] = rng.choice(segs + ["", None, "fz:9:nokey"])
    for k in ("src", "why"):
        if rng.random() < 0.5:
            r[k] = rng.choice(("judge", "planner", None, "", "wé"))
    return r


def fz_handoff_text(rng):
    r = rng.random()
    work = rng.choice(("the work", "(work)", "", " ", "a: b", "line\nbreak"))
    if r < 0.5:
        who = rng.choice(("s003", "far:gamma", "host::", "plain", "a\nb", ":x", "h:", "x:y:z", ""))
        return rng.choice(("", " ", "\x1c", "　")) + "↪" + rng.choice(("", " ", "\x1f")) + "delegated to " + who + \
            ": " + work
    if r < 0.7:
        return "↪ " + fz_text(rng)
    return fz_text(rng)


def fz_store(rng, prefix, segs):
    n = rng.choice((0, 1, 2, 3, 4, 5, 6, 8, 10))
    ids = ["%s%d" % (prefix, i) for i in range(n)]
    nodes, last_stamp = {}, None
    for i, key in enumerate(ids):
        d = {"id": key if rng.random() < 0.9 else rng.choice(ids + ["", "g1"])}
        r = rng.random()
        if r < 0.3 or i == 0:
            d["parentId"] = None if rng.random() < 0.8 else "absent"
        elif r < 0.85:
            d["parentId"] = rng.choice(ids[:i] if rng.random() < 0.8 else ids)
        elif r < 0.95:
            d["parentId"] = rng.choice(("gone", ""))
        else:
            d["parentId"] = "absent"
        if d.get("parentId") == "absent":
            del d["parentId"]
        for k, p in (("nodeComplete", 0.3), ("blocked", 0.25), ("cleared", 0.1)):
            if rng.random() < 0.8:
                d[k] = rng.random() < p
        put(d, "t", rng.choice((0, 1, 2, 2.0, 3, 5, 5.0, -0.0, 1781100000.5, "absent")))
        put(d, "mt", rng.choice((0, 2, 4, 4.0, 9, "absent", "absent")))
        if rng.random() < 0.6:
            d["log"] = [fz_row(rng, segs) for _ in range(rng.choice((0, 1, 2, 3, 9)))]
        if rng.random() < 0.3:
            if last_stamp and rng.random() < 0.35:     # the same stamp again, its anchor's tag flipped: a full tie
                d.update(json.loads(json.dumps(last_stamp)))
                at = d.get("awaitingAt")
                if isinstance(at, (int, float)) and not isinstance(at, bool):
                    d["awaitingAt"] = float(at) if isinstance(at, int) else (int(at) if at.is_integer() else at)
            else:
                d["awaitingWhy"] = rng.choice(("w", "w", "v", "waiting on a peer", ""))
                put(d, "awaitingAt", rng.choice((0, 5, 5, 5.0, 6, 40, 60, None, "absent")))
                put(d, "awaitingKind", rng.choice((None, "peer", "peer", "job", "", "agents", "absent")))
                if rng.random() < 0.6:
                    d["awaitingPeers"] = rng.sample(FUZZ_PEERS[:8], rng.choice((0, 1, 1, 2)))
                last_stamp = {k: d[k] for k in ("awaitingWhy", "awaitingAt", "awaitingKind", "awaitingPeers") if k in d}
            if rng.random() < 0.1:
                d["rolledUp"] = rng.choice((True, 1, 0, None))
        if rng.random() < 0.15:
            d["agentTask"] = rng.choice(({"status": "open"}, {"status": "open"}, {"status": "done"}, {}, None, False,
                                         {"status": None}))
        if rng.random() < 0.3:
            d["handoff"] = rng.choice(({"peer": rng.choice(FUZZ_PEERS)}, {"peer": rng.choice(FUZZ_PEERS)}, {},
                                       None, {"peer": None}))
            d["text"] = fz_handoff_text(rng)
        elif rng.random() < 0.7:
            d["text"] = fz_text(rng)
        for k in ("why", "summary", "blockSummary"):
            if rng.random() < 0.3:
                d[k] = rng.choice((fz_text(rng), "", None, "line one\nline two. more"))
        if rng.random() < 0.2:
            d["born"] = rng.choice(({"why": "born", "parentText": "the ask"}, {"why": None}, {}, None,
                                    {"parentText": ""}))
        if rng.random() < 0.6:
            d["trail"] = rng.sample(segs, rng.choice((0, 1, 1, 2)))
        if rng.random() < 0.4:
            d["promptUuid"] = rng.choice(("pu", "", None))
        if rng.random() < 0.5:
            q = rng.choice(FUZZ_JUNK)
            if isinstance(q, str) and rng.random() < 0.4:
                q = rng.choice(("", " ", "　")) + q.upper() + rng.choice(("", ".", "!?", "…", " "))
            d["quote"] = q
        if rng.random() < 0.2:
            d["summaryAnchor"] = rng.choice(("sa", None))
        if rng.random() < 0.25:
            put(d, "deltaSince", rng.choice((None, 5, 10, 10.0, 0, "absent")))
            put(d, "distilledMt", rng.choice((None, 10, 10.0, 0, 7.5, "absent")))
            d.setdefault("log", []).append({"kind": "reopen", "ev_t": rng.choice((9, 10, 10.0, 11, None, 0))})
            if rng.random() < 0.5:
                d["summary"] = rng.choice(("s", " ", ""))
        nodes[key] = d
    if ids and rng.random() < 0.15:                 # a cycle among the last nodes
        k = rng.choice((2, 3))
        cyc = ids[-k:]
        for j, x in enumerate(cyc):
            nodes[x]["parentId"] = cyc[j - 1]
    st = {"nodes": nodes}
    if rng.random() < 0.8:
        st["status"] = {x: rng.choice(("working", "completed", "cleared", "blocked")) for x in ids
                        if rng.random() < 0.5}
    if rng.random() < 0.7:
        st["lastNode"] = rng.choice(ids + [None, "gone"]) if ids else None
    return st


def fz_session(rng, prefix):
    segs = ["fz:%d:%s" % (rng.randint(1, 3), k.split(":")[1]) for k in FUZZ_SEG_KEYS]
    st = fz_store(rng, prefix, segs) if rng.random() < 0.95 else None
    ids = list((st or {}).get("nodes") or {})
    healed = {}
    for x in ids:
        if rng.random() < 0.12:
            healed[x] = [rng.choice(ids + [None, "", "gone"]), rng.choice((
                {"why": "healed", "parentText": "host text"}, {"why": "skill", "hidden": True}, {}, {"hidden": 0}))]
    if healed:   # a heal may not make a cycle reachable from a root: drop the ones whose new parent leads back
        for x in list(healed):
            p, seen = healed[x][0], {x}
            while p and p not in seen:
                seen.add(p)
                hp = healed.get(p)
                p = hp[0] if hp and hp[0] else ((st["nodes"].get(p) or {}).get("parentId"))
            if p in seen:
                del healed[x]
    s = {"store": st, "healed": healed,
         "liveRow": rng.choice((None, {}, {"authLogin": "L1", "authLoginLive": "L1", "authLabel": rng.choice(("", "Lab",
                                                                                                             None))},
                                {"authLogin": "L1", "authLoginLive": "L2"}, {"authLogin": "", "authLoginLive": ""})),
         "segUuid": [[k, rng.choice(("w1", "w2", "", None))] for k in FUZZ_SEG_KEYS if rng.random() < 0.6],
         "segTrig": [[k, rng.choice(("p1", "p2", "", None))] for k in FUZZ_SEG_KEYS if rng.random() < 0.6]}
    if rng.random() < 0.9:
        per = {p: rng.choice((0, 5, 50, 50.0, 70.5)) for p in FUZZ_PEERS[:8] if rng.random() < 0.3}
        s["peerAnswered"] = [rng.choice((0, 0, 5, 45, 60, 60.0)), per]
    r = rng.random()
    if r < 0.6:
        s["apiError"] = rng.choice((None, {}, {"authErr": True}, {"authErr": 0}, {"authErr": "x"}, "boom", [1], 3, 0.5,
                                    True, [], ""))
    return s


def fz_world(rng):
    sessions = []
    for i in range(rng.choice((1, 1, 2, 3))):
        prefix = rng.choice(("g", "g", "s%d:g" % i))
        sessions.append(fz_session(rng, prefix))
    queries = rng.sample(FUZZ_PEERS, rng.choice((0, 2, 4)))
    return {"sessions": sessions, "names": NAMES, "remotes": REMOTES, "postalNames": POSTAL, "queries": queries}


def fz_direct(rng):
    r = rng.random()
    if r < 0.3:
        p = {"itemKind": rng.choice(("peer", "agents", "commands", "watches", "timer", "task")),
             "iid": rng.choice(("i", "", None, "peer:x")), "label": rng.choice((fz_text(rng, 6), "", None)),
             "since": rng.choice((None, 0, -0.0, 1, 2.5, -2.5, 0.999, -0.999, 1e20, -1e19, 1781100000.75,
                                  4611686018427387904, 9.3e18))}
        for k, v in (("agentId", ("a", "", None)), ("detail", ("d", "", None)), ("stoppable", (True, False))):
            if rng.random() < 0.4:
                p[k] = rng.choice(v)
        return "item", p
    if r < 0.6:
        return "title", {"text": rng.choice((fz_text(rng), None, "x" * rng.randint(115, 125) + " y",
                                             fz_text(rng, 20) + "\n" + fz_text(rng, 20),
                                             "x" * rng.randint(116, 120) + rng.choice(FUZZ_WS) + "y" * 5)),
                         "cap": rng.choice((120, 120, 1, 2, 13, 40))}
    if r < 0.8:
        q = rng.choice(FUZZ_JUNK)
        if isinstance(q, str) and rng.random() < 0.5:
            q = rng.choice(("", " ", "\x1c", "\n")) + rng.choice((q, q.upper(), q.title())) + rng.choice(
                ("", ".", "…", "!?", " ", "　"))
        return "junk", {"q": q}
    if r < 0.85:
        st = fz_store(rng, rng.choice(("g", "t")), ["fz:1:k1", "fz:2:k2"])
        ids = list(st["nodes"])
        tup = rng.random() < 0.5
        return "stampDefault", {"nodes": st["nodes"], "top": rng.choice(ids + ["missing"]) if ids else "missing",
                                "answered": [rng.choice((0, 5, 45, 60)), {p: rng.choice((0, 50, 70.5)) for p in
                                                                         FUZZ_PEERS[:6] if rng.random() < 0.4}]
                                if tup else rng.choice((None, 0, 5, 45.5)), "tuple": tup}
    if r < 0.92:
        n = {"id": "x", "awaitingAt": rng.choice((0, 40, 40.0, None)), "awaitingWhy": "w"}
        put(n, "awaitingKind", rng.choice((None, "peer", "job", "", "absent")))
        if rng.random() < 0.6:
            n["awaitingPeers"] = rng.sample(FUZZ_PEERS[:6], rng.choice((0, 1, 2)))
        if rng.random() < 0.5:
            n["log"] = [fz_row(rng, ["fz:1:k1"]) for _ in range(rng.randint(0, 3))]
        if rng.random() < 0.5:
            return "superseded", {"node": n, "answered": [rng.choice((0, 41, 60, 60.0)), {p: rng.choice((
                0, 39, 50, 50.0)) for p in FUZZ_PEERS[:6] if rng.random() < 0.4}], "tuple": True}
        return "superseded", {"node": n, "answered": rng.choice((None, 0, 41, 60.5, -1)), "tuple": False}
    return "landing", {"state": rng.choice(("completed", "blocked", None, "x")),
                       "column": rng.choice(("", "working", "completed", "needs_input", "awaiting")),
                       "node": {"id": "a", "summary": rng.choice(("s", "", None)),
                                "blockSummary": rng.choice(("b", "", None))}}


def fuzz_specs(rng, n, seed):
    specs = []
    for i in range(n):
        name = "fuzz seed %d case %d" % (seed, i)
        if rng.random() < 0.2:
            kind, p = fz_direct(rng)
            specs.append((name, kind, p))
        else:
            specs.append((name, "world", {"world": fz_world(rng)}))
    return specs


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


# romp-spike's edge-set summary line (main.rs, layer4_edge)
RUST_SUMMARY = re.compile(r"^layer4 edge set(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"values\), (\d+) refused at load \(every REFUSE case\), (\d+) differences in (\d+) cases ")


def fuzz_main(n, seed, plant, binary, out, with_lines):
    """Make `n` cases from `seed`, answer them in Python, and compare with `romp-spike layer4` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer4-fuzz-")
    try:
        t0 = time.monotonic()
        _dw, km, _rec, _root = load_kernel(tmp)
        bc, _span = body_children_fn(km)
        specs = fuzz_specs(random.Random(seed), n, seed)
        lines = Lines(km, [("kernel._feed_session_entry:children", bc.__code__)]) if with_lines else None
        cases, cov = answer_cases(km, bc, specs, lines)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer4.py --fuzz %d --seed %d" % (n, seed), "coverage": cov,
               "cases": cases}
        path = out or os.path.join(tmp, "layer4_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        kinds = {}
        for c in cases:
            kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
        nraise = sum(1 for c in cases if isinstance(c["expect"], dict) and "raises" in c["expect"])
        print("fuzz seed %d: %d cases (%s) made and answered by Python in %.1f s, %.1f MB; Python raised on %d" % (
            seed, n, ", ".join("%d %s" % (v, k) for k, v in sorted(kinds.items())), py_s,
            os.path.getsize(path) / 1e6, nraise))
        print("  python: coverage %s" % json.dumps(cov, sort_keys=True))
        if lines is not None:
            lc = lines.report()
            print("  python: lines %d of %d run, missed %s" % (lc["ran"], lc["lines"], json.dumps(lc["missed"])))
        p = subprocess.run([binary, "layer4", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        lines_out = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, lines_out) if m]
        for line in lines_out:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer4: romp-spike layer4 exited %d without its summary line: %s" % (
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


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random cases with romp-spike layer4")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=PLANTS, help="--fuzz against romp-spike layer4 --plant NAME: the check passes "
                    "when the plant is caught")
    ap.add_argument("--bin", help="a built romp-spike for --fuzz (default: cargo build --release)")
    ap.add_argument("--out", help="keep the --fuzz cases in this file (default: a temporary file, removed)")
    ap.add_argument("--lines", action="store_true", help="--fuzz: also report the lines of the 29 functions it ran")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if a.fuzz is not None:
        if a.fuzz < 1:
            ap.error("--fuzz N needs N >= 1")
        return fuzz_main(a.fuzz, a.seed, a.plant, rust_binary(a.bin), a.out, a.lines)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT, or --fuzz N")
    first = os.path.realpath(a.dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        dw, km, rec, root = load_kernel(work)
        bc, _span = body_children_fn(km)
        for d in a.dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer4: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            cov, check, size = dump_one(dw, km, bc, d)
            print("%s: %s; build check %d calls, %d compared, %d unmatched, %d mismatches %s -> %s (%.1f MB)" % (
                os.path.basename(d), json.dumps(cov, sort_keys=True), check["calls"], check["compared"],
                check["unmatched"], len(check["mismatches"]), json.dumps(check["byHelper"], sort_keys=True),
                os.path.join(d, OUT_NAME), size / 1e6))
            for m in check["mismatches"][:10]:
                print("  build mismatch: " + m)
            if check["mismatches"]:
                return 1
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer4: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
