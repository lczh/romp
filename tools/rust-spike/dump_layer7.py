#!/usr/bin/env python3
"""Python's own answers for layer 7 of the Rust spike's port (rust/notes/s2-design.md section 2, "Loop, fold and
cross-session pass"; rust/SPIKE.md step S4.7), for the Rust side to match byte for byte.

Layer 7's answer is the whole feed frame build_feed returns. The S2 dump already holds Python's real feed for both
worlds (`oracle/feed.canon.json`, sorted keys, the gate; `oracle/feed.wire.json`, insertion order), and
`romp-spike feed DUMP` compares with those. This tool makes the inputs the worlds do not reach and answers them with the
real kernel.

A case is a small world: a snapshot in the S2 dump's schema (dump_world.py) and its trees, plus what layer 7 reads that
the S2 dumper never writes (neither world has any): `board.postalLog` (the postal log's records), `board.mailNew` (the
maildir files that stand, `[recipient, message id]`) and `board.notices` (each notice file's lines). Python answers it
with one replay build_feed over it, the real fold and every pass after the loop included: dump_world's Replay harness,
dump_layer6's stubs (the live backend's recorded `current_ask`, the derivation's complaint), the postal log served to
the real fold through em.serve_records, the maildir files and notice files written into the replay's empty state root
for the real readers (the notice memo cleared first), and the held-mail backfill stubbed (a writer that posts and
retires held-mail notice rows from the hold directory; its effect is in the rows a case gives). _node_anchor_last and
the build memos are cleared first, so a case starts as a fresh process does. The clock is the case's `world.now`. The
port reads `mailNew` as exact (recipient, id) pairs where Python asks the filesystem, so the harness answers no world
whose `mailNew` this filesystem contradicts for a parked candidate (its file exists() here and is not listed, or the
reverse: APFS ignores letter case, so a send `M1` finds a file `m1`), as it answers none whose notice files collide;
the loader refuses the ids a path lookup reads as another path (kernel._safe_id).

The frame keys build_feed passes through (`views`, `judgeLimit`, `stateUnknown`, `sessions`, ...) come from the same
build: a case's `frameKeys` are the feed's keys minus `servingFoldedIds` and `absentSids` (the port decides those two and
writes them right after `now`), and its `frame` every key but the ones the per-card build makes. The harness checks that
this assembly gives Python's own wire bytes back, so the port is told nothing about the two conditional keys. The
expected answer is the feed (decoded from Python's wire string) with the SHA-1 of Python's canonical and wire strings.

    python3 tools/rust-spike/dump_layer7.py DUMP_DIR [DUMP_DIR ...]   # the harness against the dump's own feed
    python3 tools/rust-spike/dump_layer7.py --edge-cases OUT.json     # the synthetic cases (romp-spike feed OUT.json)
    python3 tools/rust-spike/dump_layer7.py --fuzz N [--seed S] [--plant NAME] [--bin ROMP_SPIKE] [--out FILE] [--lines]

The dump mode runs this harness over a dump's snapshot and trees, splices the dump's own frame keys in as the S2 replay
does, and requires the canonical and wire bytes to equal oracle/feed.canon.json and feed.wire.json (the harness's own
external check). A case named "REFUSE ..." is one the Rust loader must refuse. The edge set reports the lines of the
ported functions it ran (`lineCoverage`), and carries Python's cm.age_rgb and cm.ramp over a table of ages and points
(`tints`, `ramps`), which `romp-spike feed` and `tests/layer7_edge.rs` check point by point. Stdlib only.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from unittest import mock

sys.dont_write_bytecode = True

HERE = os.path.dirname(os.path.realpath(__file__))
EDGE_SCHEMA = "romp-spike-layer7-edge/1"
NOW = 1781100000
PLANTS = ("bell-falsy-skip", "serving-first-row", "who-owner", "parked-unsorted", "notice-cap-held", "absent-last")
CONDITIONAL = ("servingFoldedIds", "absentSids")
# the functions layer 7 ports whose lines are reported (nested closures and comprehensions count toward them)
FUNCS = (("kernel", "build_feed"), ("kernel", "_feed_fold_session"), ("kernel", "_feed_fold_tints"),
         ("kernel", "_feed_fold_bell"), ("kernel", "_feed_fold_stamp"), ("kernel", "_feed_fold_card"),
         ("kernel", "_notify_card_effective"), ("kernel", "_push_explicit_bell"), ("kernel", "_parked_handoffs"),
         ("kernel", "_parked_fold_step"), ("kernel", "_notice_cards"), ("kernel", "_notice_rows"),
         ("kernel", "_notice_projection"), ("kernel", "_notice_item_id"), ("kernel", "_notice_action_kind"),
         ("kernel", "_held_mail_row"), ("colormap", "stops_for"), ("colormap", "ramp"), ("colormap", "age_rgb"))
COLORMAP_NAMES = ("aurora", "hawaii", "viridis", "magma", "inferno", "plasma", "cividis")


def load_l6():
    spec = importlib.util.spec_from_file_location("romp_spike_dump_layer6", os.path.join(HERE, "dump_layer6.py"))
    l6 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(l6)
    return l6


def load_kernel(tmp):
    l6 = load_l6()
    l5, _l4, dw, km, rec, root = l6.load_kernel(tmp)
    return l6, l5, dw, km, rec, root


def sha1(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class Lines:
    """sys.settrace line events inside FUNCS (their nested closures and comprehensions count toward them)."""

    def __init__(self, km):
        mods = {"kernel": km, "colormap": km.cm}
        self.codes, self.labels = {}, []
        for mod, n in FUNCS:
            label = "%s.%s" % (mod, n)
            self.labels.append(label)
            stack = [getattr(mods[mod], n).__code__]
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


# ───────────────────────────── the harness ─────────────────────────────
class Unfaithful(Exception):
    """The world's mailNew does not say what this filesystem says: a parked candidate's maildir file exists() here and
    is not listed, or is listed and does not exist here (APFS ignores letter case, so `M1` finds a file `m1`; a path
    lookup also drops `.` components and trailing slashes, which the loader refuses with kernel._safe_id). The port
    reads mailNew as exact pairs, so such a world has no answer to compare."""


def parked_unfaithful(km, board, alive, postal):
    """The parked candidates whose maildir test on this filesystem disagrees with their mailNew membership, each
    (mid, to_id, exists here, listed): the real fold (_parked_fold_step over the postal log's dict rows, as fold_records
    steps them), then the stat _parked_handoffs makes for a candidate whose recipient is not alive."""
    state = {}
    try:
        for o in board.get("postalLog") or []:
            if isinstance(o, dict):
                state = km._parked_fold_step(state, o)
    except Exception:                                  # noqa: BLE001 (the build meets the same raise in its own fold)
        return []
    listed = {(to, mid) for to, mid in board.get("mailNew") or []}
    out = []
    for mid, c in state.items():
        to = c["toId"]
        if not (isinstance(mid, str) and isinstance(to, str)) or to in alive:
            continue
        try:
            there = (postal / "mail" / to / "new" / mid).exists()
        except (OSError, ValueError):                  # Python's build reads an OSError as no file; NUL raises there
            there = False
        if there != ((to, mid) in listed):
            out.append((mid, to, there, (to, mid) in listed))
    return out


def answer_feed(l6, dw, km, snap, trees, lines=None, faithful=True):
    """One replay build_feed over a world, the real fold and passes included: (feed, None), or (None, the raise) when
    build_feed itself raised. With `faithful`, a world whose mailNew this filesystem contradicts raises Unfaithful
    (only a REFUSE case, which the loader refuses on other grounds, is answered as Python reads it)."""
    jd = km.jd
    board, now = snap["board"], snap["world"]["now"]
    notices_dir, postal = km._notice_dir(), jd.STATE / "postal"
    for d in (notices_dir, postal):
        shutil.rmtree(d, ignore_errors=True)
    files = board.get("notices") or {}
    if files:
        os.makedirs(notices_dir)
        for sid, rows in files.items():
            with open(os.path.join(notices_dir, sid + ".jsonl"), "w", encoding="utf-8", newline="") as f:
                f.write("".join(json.dumps(r) + "\n" for r in rows))
        listed = sorted(os.listdir(notices_dir))
        if listed != sorted(sid + ".jsonl" for sid in files):
            raise SystemExit("dump_layer7: the notice files collide on this filesystem: %r" % listed)
    for to, mid in board.get("mailNew") or []:
        p = postal / "mail" / to / "new" / mid
        os.makedirs(p.parent, exist_ok=True)
        p.write_text("")
    bad = parked_unfaithful(km, board, {r["sid"] for r in snap["alive"]}, postal) if faithful else []
    if bad:
        for d in (notices_dir, postal):
            shutil.rmtree(d, ignore_errors=True)
        raise Unfaithful("dump_layer7: mailNew disagrees with this filesystem's maildir test for parked candidates "
                         "(mid, to_id, exists here, listed): %r" % (bad,))
    rp = dw.Replay(km, snap, trees)
    dw.clear_build_memos(km)
    km._node_anchor_last.clear()
    km._NOTICE_MEMO.clear()
    saved = []

    def patch(obj, name, fn):
        saved.append((obj, name, obj.__dict__[name] if isinstance(obj, type) else getattr(obj, name)))
        setattr(obj, name, fn)

    rp.install()
    stub = l6._AskStub(rp, snap)
    patch(km.Sessions, "backend_for", staticmethod(lambda sid: stub))
    patch(km, "_feed_derive_complain", lambda *a, **k: None)
    patch(km, "_held_mail_backfill", lambda: 0)
    feed = raised = None
    try:
        with mock.patch("time.time", return_value=float(now)), \
                km.em.serve_records({jd.MESSAGES: list(board.get("postalLog") or [])}):
            km._STARTED = float(now)
            fn = lambda: km.build_feed(now, km._live_map())
            try:
                feed = lines.run(fn) if lines is not None else fn()
            except Exception as e:                     # noqa: BLE001 (the answer: build_feed itself raised)
                raised = {"raises": type(e).__name__, "arg": str(e)}
    finally:
        for obj, name, v in reversed(saved):
            setattr(obj, name, v)
        rp.uninstall()
        for d in (notices_dir, postal):
            shutil.rmtree(d, ignore_errors=True)
    return feed, raised


def assemble(dw, snap, feed):
    """The port's frame rule: the snapshot's frame keys in order, `now` the world's clock, servingFoldedIds and
    absentSids right after it when the build made them, the per-card build's keys from the feed, the rest passed
    through from the snapshot's frame."""
    out = {}
    for k in snap["frameKeys"]:
        if k in CONDITIONAL:
            continue
        if k == "now":
            out[k] = snap["world"]["now"]
            for c in CONDITIONAL:
                if feed.get(c):
                    out[c] = feed[c]
        elif k in dw.SLICE_KEYS:
            out[k] = feed.get(k)
        else:
            out[k] = snap["frame"][k]
    return out


def dump_one(l6, dw, km, d):
    snap, trees = dw.load_dump(d)
    lines = Lines(km)
    feed, raised = answer_feed(l6, dw, km, snap, trees, lines)
    doc = {"schema": "romp-spike-layer7/1", "world": snap["world"]["name"], "python": platform.python_version(),
           "raised": raised, "lineCoverage": lines.report()}
    if feed is not None:
        full = assemble(dw, snap, feed)
        with open(os.path.join(d, "oracle", "feed.canon.json"), encoding="utf-8") as f:
            canon = f.read()
        with open(os.path.join(d, "oracle", "feed.wire.json"), encoding="utf-8") as f:
            wire = f.read()
        doc.update(canonEqual=dw.canon(full) == canon, wireEqual=dw.wire(full) == wire, cards=len(feed["asks"]),
                   firstDiff=dw.first_diff(json.loads(dw.wire(full)), json.loads(wire)))
    doc["harnessAgrees"] = bool(doc.get("canonEqual") and doc.get("wireEqual"))
    with open(os.path.join(d, "layer7.json"), "w", encoding="utf-8", newline="") as f:
        json.dump(doc, f, allow_nan=False)
    return doc


def main_dumps(dumps):
    first = os.path.realpath(dumps[0])
    work = os.path.join(first, "work")
    made = not os.path.isdir(work)
    os.makedirs(work, exist_ok=True)
    try:
        l6, _l5, dw, km, rec, root = load_kernel(work)
        rc = 0
        for d in dumps:
            d = os.path.realpath(d)
            if not os.path.isfile(os.path.join(d, "snapshot.json")) or not os.path.exists(os.path.join(d, dw.MARKER)):
                print("dump_layer7: %s is not a dump_world.py output directory" % d, file=sys.stderr)
                return 2
            doc = dump_one(l6, dw, km, d)
            lc = doc["lineCoverage"]
            print("%s: the harness's feed %s oracle/feed.canon.json and feed.wire.json (%s cards)%s; layer-7 lines run "
                  "%d of %d" % (os.path.basename(d), "equals" if doc["harnessAgrees"] else "DIFFERS from",
                                doc.get("cards"), "" if doc["harnessAgrees"] else " %s %s" % (
                                    doc.get("raised"), doc.get("firstDiff")), lc["ran"], lc["lines"]))
            rc = rc or (0 if doc["harnessAgrees"] else 1)
        if rec["spawns"] or rec["refused_writes"] or rec["thread_starts"]:
            print("dump_layer7: the guards saw spawns %d, refused writes %d, thread starts %d" % (
                len(rec["spawns"]), len(rec["refused_writes"]), rec["thread_starts"]), file=sys.stderr)
            return 1
        return rc
    finally:
        shutil.rmtree(work if made else root, ignore_errors=True)


def answer_cases(l6, dw, km, specs, lines=None):
    """Each spec (name, snapshot, trees[, post]) answered: the frame keys filled in from Python's own build, the
    assembly checked against Python's wire bytes, and the expected feed with its SHA-1s, or build_feed's own raise (an
    admitted world reaches one: a null card in an entry's asks, which the fold reads with `.get`, outside the
    per-session guard). `post` edits the snapshot after the answer (a REFUSE case that breaks the frame itself)."""
    cases = []
    for spec in specs:
        name, snap, trees = spec[:3]
        try:
            feed, raised = answer_feed(l6, dw, km, snap, trees, lines, faithful=not name.startswith("REFUSE "))
        except Exception as e:                         # noqa: BLE001 (only a refused world may break the harness)
            if not name.startswith("REFUSE "):
                raise
            feed, raised = None, {"raises": "harness", "arg": "%s: %s" % (type(e).__name__, e)}
        if feed is not None:
            snap["frameKeys"] = [k for k in feed if k not in CONDITIONAL]
            snap["frame"] = {k: dw.plain(v) for k, v in feed.items() if k not in dw.FRAME_SKIP}
            if dw.wire(assemble(dw, snap, feed)) != dw.wire(feed):
                raise SystemExit("dump_layer7: %s: the frame assembly does not give Python's wire bytes back" % name)
            expect = {"feed": dw.plain(feed), "canonSha1": sha1(dw.canon(feed)), "wireSha1": sha1(dw.wire(feed))}
        else:
            # a build that raises makes no frame: the case carries the minimal frame keys the loader requires, never
            # assembled (the port must raise the same before any frame exists)
            snap["frameKeys"] = ["type", "asks", "now", "working", "awaiting", "bgServices"]
            snap["frame"] = {"type": "feed"}
            expect = raised
        if len(spec) > 3:
            spec[3](snap)
        cases.append({"name": name, "snapshot": snap, "trees": {k: v for k, v in trees.items()}, "expect": expect})
    return cases


def summarize(cases):
    out = {"cards": 0, "absent": 0, "folded": 0, "parked": 0, "notices": 0, "served": 0}
    for cs in cases:
        f = cs["expect"].get("feed") if isinstance(cs["expect"], dict) else None
        if not f or cs["name"].startswith("REFUSE "):
            continue
        out["cards"] += len(f["asks"])
        out["absent"] += len(f.get("absentSids") or [])
        out["folded"] += len(f.get("servingFoldedIds") or [])
        out["parked"] += sum(1 for c in f["asks"] if str(c.get("itemId", "")).startswith("parked:"))
        out["notices"] += sum(1 for c in f["asks"] if str(c.get("itemId", "")).startswith("notice:"))
    return out


# ───────────────────────────── the worlds: builders ─────────────────────────────
def session_tree(km, l6, s=0, open_=False, t0=100):
    """Two turns: a closed one with a long reply, a second one with tools, open or closed. Returns (tree, k1, k2)."""
    L = l6
    tr = L.tree(L.turn([L.u("u1", t0, "fix the cache", s=s), L.asst("a1", t0 + 1, L.SUB1, s=s), L.tool("a2", t0 + 2, s=s),
                        L.asst("a3", t0 + 3, "Short note.", s=s)]),
                L.turn([L.u("u2", t0 + 100, "now the index", s=s), L.tool("b1", t0 + 101, s=s),
                        L.asst("b2", t0 + 102, L.SUB2, s=s)], ended=not open_))
    k1, k2 = L.segids(km, tr)
    return tr, k1, k2


def with_now(w, now):
    w[0]["world"]["now"] = now
    return w


def with_cmap(w, name):
    w[0]["world"]["colormap"] = name
    return w


def notice(op="post", key="k1", rev=1, t=None, **kw):
    r = {"op": op, "key": key}
    if rev is not None:
        r["rev"] = rev
    if t is not None:
        r["t"] = t
    r.update(kw)
    return r


def sent(mid, to, frm, t=None, park=True, **kw):
    r = {"ev": "sent", "id": mid, "to_id": to, "from_id": frm, "body": "the handoff %s" % (mid,)}
    if park is not None:
        r["park"] = park
    if t is not None:
        r["t"] = t
    r.update(kw)
    return r


PEER = "ffffffff-0000-0000-0000-000000000009"     # dump_layer6.PEER: a session the registry knows, not alive
DEAD = "dddddddd-0000-0000-0000-000000000001"     # a session that is not alive (parked mail waits for it)
DEAD2 = "dddddddd-0000-0000-0000-000000000002"
UNI = "ünïcödé-dead-session-0000"                 # a dead sid whose first 8 code points are not 8 bytes


def edge_cases(km, l6):
    """(name, snapshot, trees[, post])."""
    L = l6
    S, W, N, ST, REC = L.S, L.W, L.N, L.ST, L.REC
    SIDS, PEER = L.SIDS, L.PEER
    c = []

    def add(name, w, post=None):
        c.append((name, w[0], w[1]) + ((post,) if post else ()))

    trees = [session_tree(km, l6, s) for s in range(4)]
    opens = [session_tree(km, l6, s, open_=True) for s in range(4)]

    def g(s, nid, **kw):
        kw.setdefault("trail", [trees[s][1]])
        return N(nid, **kw)

    def hand(nid, peer, **kw):
        kw.setdefault("handoff", {"peer": peer, "msgId": "m1"})
        return N(nid, **kw)

    def idle_s(i, *nodes, **kw):
        return S(i, tr=trees[i][0], st=ST(*nodes, **kw.pop("store_kw", {})), **kw)

    def open_s(i, *nodes, **kw):
        return S(i, tr=opens[i][0], st=ST(*nodes, **kw.pop("store_kw", {})), **kw)

    T = NOW - 5000
    # ── the tint: every colormap, the clamp's edges, the clock's tag
    for name in COLORMAP_NAMES:
        add("tint: colormap %s, a card and its child row at two ages" % name, with_cmap(W(idle_s(
            0, g(0, "g1", t=NOW - 3000), g(0, "c1", parentId="g1", t=NOW - 90000, nodeComplete=True)),
            board={"notifyCards": {}}), name))
    for name, why in ((None, "null: the default"), ("", "empty: the default"), ("VIRIDIS", "upper case: lowercased"),
                      ("Hawaii", "title case: lowercased"), ("nope", "unknown: the default (aurora, not hawaii)"),
                      ("hawaii ", "a trailing space: unknown (not stripped)")):
        add("tint: colormap %r, %s" % (name, why), with_cmap(W(idle_s(0, g(0, "g1", t=NOW - 3000))), name))
    edges = (("age 0", NOW), ("119 s", NOW - 119), ("120 s (FADE_LO)", NOW - 120), ("121 s", NOW - 121),
             ("345600 s (FADE_HI)", NOW - 345600), ("345601 s", NOW - 345601), ("a future time", NOW + 50),
             ("a fractional age", NOW - 1000.25), ("a negative time", -5))
    add("tint: ages at the clamp's edges, one top card each", W(idle_s(
        0, *[g(0, "g%d" % i, t=t, mt=t) for i, (_n, t) in enumerate(edges)])))
    add("tint: a float clock (1781100000.5): every age a float, the placeholder dated by the float",
        with_now(W(idle_s(0, g(0, "g1", t=NOW - 3000), g(0, "c1", parentId="g1", t=NOW - 30)),
                   S(1, tr=L.tree(), st=ST(), live={"state": "permission", "since": NOW - 30})), NOW + 0.5))
    add("tint: an integral float clock (1781100000.0) prints as a float", with_now(W(
        S(0, tr=L.tree(), st=ST(), live={"state": "permission", "since": NOW - 30}),
        idle_s(1, g(1, "g1", t=NOW - 3000))), float(NOW)))
    add("tint: a small int clock near the trees' own times (now 400)", with_now(W(
        idle_s(0, g(0, "g1"), g(0, "c1", parentId="g1", t=150), g(0, "c2", parentId="g1", t=300.5)),
        open_s(1, g(1, "g1", t=380))), 400))
    for age in (28675.670495616596, 10594.720899223195):     # aurora: 50.5 reads 50; 168.5 and 188.5 read 168, 188
        add("tint: an age whose channel lands on an exact half rounds it to even (the clock %r, the card at 0)" % age,
            with_now(W(idle_s(0, g(0, "g1", t=0))), age))
    add("tint: an int clock minus a float time is float arithmetic, the time not truncated (a card and its row at "
        "NOW - 121.5)", W(idle_s(0, g(0, "g1", t=NOW - 121.5), g(0, "c1", parentId="g1", t=NOW - 121.5))))
    add("tint: an age where ramp's float order shows: a + (b - a) * fr, not a * (1 - fr) + b * fr (the clock "
        "146335.799933855, the card at 0)", with_now(W(idle_s(0, g(0, "g1", t=0))), 146335.799933855))
    add("tint: a float clock past i64 (1e19) with no parked send is admitted: every age a float, the darkest stop",
        with_now(W(idle_s(0, g(0, "g1", t=NOW - 3000), g(0, "c1", parentId="g1", t=NOW - 30))), 1e19))
    big = 2 ** 60 + 1
    add("tint: a clock past 2**53: the age is int arithmetic, exact, then rounded (1000 s, where floats read 1024)",
        with_now(W(idle_s(0, g(0, "g1", t=big - 1000), g(0, "c1", parentId="g1", t=big - 1001, nodeComplete=True))), big))
    add("tint: rows tinted from their own subtree's time (last), the card from its own",
        W(idle_s(0, g(0, "g1", t=NOW - 300000), g(0, "c1", parentId="g1", t=NOW - 600),
                 g(0, "d1", parentId="c1", t=NOW - 60), g(0, "c2", parentId="g1", t=NOW - 200000))))
    live_perm = {"state": "permission", "since": NOW - 30}
    add("placeholder: a blocked placeholder with no turns: t := the clock, the brightest tint",
        W(S(0, tr=L.tree(), st=ST(), live=live_perm)))
    pt = L.tree(L.turn([L.u("u1", NOW - 700, "may I run the migration")], ended=False))
    add("placeholder: a blocked placeholder dated by its held segment keeps its time",
        W(S(0, tr=pt, st=ST(), live=live_perm)))
    add("placeholder: the provisional card keeps its segment's time",
        W(S(0, tr=L.tree(L.turn([L.u("u1", NOW - 2000, "write the docs")], ended=False)),
            st=ST(N("g1", nodeComplete=True), status={"g1": "completed"}))))
    awt = L.tree(L.turn([L.u("u1", NOW - 900, "go"), L.asst("a1", NOW - 899, "done")]))
    (w1,) = L.segids(km, awt)
    aw = {"why": "waiting on the build", "kind": "commands", "since": 99, "count": 2}
    add("placeholder: the awaiting placeholder dated by the last turn",
        W(S(0, tr=awt, st=ST(N("g1", nodeComplete=True), status={"g1": "completed"}, placements={w1 + "#p": "g1"}),
            rec=REC(sessionAwaiting={"idle=true": aw}, awaitingTaskDescs=["npm test"]))))

    # ── the bells
    two = lambda board=None, flags0=None, flags1=None, peers=None: W(
        idle_s(0, g(0, "g1", t=NOW - 100), flags=flags0), idle_s(1, g(1, "g2", t=NOW - 200), flags=flags1),
        board=board, peers=peers)
    add("bell: nothing set: notify null, notifyRaw null", two())
    add("bell: the master on: every card notify true, notifyRaw null", two(board={"notifyCards": {"*": True}}))
    add("bell: the master truthy but not a bool (1)", two(board={"notifyCards": {"*": 1}}))
    add("bell: the master off (false and \"\")", two(board={"notifyCards": {"*": ""}}))
    add("bell: the turn-finished switch alone arms nothing", two(board={"notifyCards": {"*turns": True}}))
    add("bell: a card override true, the master off", two(board={"notifyCards": {"g1": True}}))
    add("bell: a card override false, the master on: an explicit mute (never the master)",
        two(board={"notifyCards": {"*": True, "g1": False}}))
    add("bell: a card override 0 and \"\": falsy, not None: mutes", two(board={"notifyCards": {"*": True, "g1": 0, "g2": ""}}))
    add("bell: a card override false beats the session's flag true",
        two(board={"notifyCards": {"g1": False}}, flags0={"notify": True}))
    add("bell: card overrides 1 and \"yes\", the master off: truthy, so armed and an explicit true",
        two(board={"notifyCards": {"g1": 1, "g2": "yes"}}))
    add("bell: a card whose itemId is \"\" muted, the master on: notify reads the override, notifyRaw skips it "
        "(Python's `if item_id:`)", W(idle_s(0, g(0, "", t=NOW - 100)), board={"notifyCards": {"": False, "*": True}}))
    add("bell: a card whose itemId is \"\" armed, its session's flag false: notifyRaw is the flag, not the override",
        W(idle_s(0, g(0, "", t=NOW - 100), flags={"notify": False}), board={"notifyCards": {"": True}}))
    add("bell: a card override null is no override (the session's flag decides)",
        two(board={"notifyCards": {"g1": None, "*": True}}, flags0={"notify": False}))
    for v in (True, False, None, 0, "yes"):
        add("bell: the session flag notify %r, the master on" % (v,),
            two(board={"notifyCards": {"*": True}}, flags0={"notify": v, "hideFromFeed": 0}))
    add("bell: the session flag true, the master off", two(flags1={"notify": True}))
    w = two(board={"notifyCards": {"*": True}})
    w[0]["peers"][SIDS[0]] = {"names": None, "flags": {"notify": False}, "store": None}
    add("bell: the session's flags are null and a peer row's flags for the same sid mute it", w)
    w = two(board={"notifyCards": {"*": True}}, flags0={"x": 1})
    w[0]["peers"][SIDS[0]] = {"names": None, "flags": {"notify": False}, "store": None}
    add("bell: the session's own flags (no notify) win over a peer row's", w)

    # ── the session fold
    add("fold: a raising session is absent; the others fold around it",
        W(idle_s(0, g(0, "g1")), idle_s(1, g(1, "g1"), rec=REC(drop=("interrupting",))), idle_s(2, g(2, "g1"))))
    add("fold: two raising sessions, absentSids in alive order",
        W(idle_s(0, g(0, "g1"), rec=REC(drop=("sessionRetrying",))), idle_s(1, g(1, "g1")),
          idle_s(2, g(2, "g1"), rec=REC(drop=("apiError",)))))
    add("fold: a null card in an entry (the awaiting placeholder of a session that is not live) raises out of the fold",
        W(idle_s(0, g(0, "g1")), S(1, tr=awt, st=ST(N("g1", nodeComplete=True), status={"g1": "completed"},
                                                   placements={L.segids(km, awt)[0] + "#p": "g1"}),
                                  live=None, rec=REC(sessionAwaiting={"idle=true": aw}))))
    add("fold: a cold session (no tree, the warm wanted) folds its cards; heal counts and the warm are log lines only",
        W(S(0, tr=None, st=ST(N("g1", t=NOW - 100)), rec=REC(warmWanted=True)),
          idle_s(1, g(1, "g1"), g(1, "g2", t=110), rec=REC(healSessionTops={
              "g2": ["g1", {"why": "started it", "parentText": "the ask"}]})),
          idle_s(2, g(2, "g1"), g(2, "g3", t=110), rec=REC(healSessionTops={"g3": [None, {"hidden": True}]}))))
    add("fold: a hidden session folds nothing and is not absent",
        W(idle_s(0, g(0, "g1"), flags={"hideFromFeed": True}), open_s(1, g(1, "g1"))))
    add("fold: working names from two open sessions, awaiting from an awaiting one, in alive order",
        W(open_s(0, g(0, "g1")), idle_s(1, g(1, "g1", awaitingWhy="waiting on the build", awaitingAt=160,
                                               awaitingKind="commands")),
          open_s(2, g(2, "g1")), S(3, tr=awt, st=ST(N("g1", nodeComplete=True), status={"g1": "completed"},
                                                   placements={L.segids(km, awt)[0] + "#p": "g1"}),
                                  rec=REC(sessionAwaiting={"idle=true": aw}, awaitingTaskDescs=["npm test"]))))
    tasks = [{"tid": "tu-a2-0", "desc": "npm test", "t": 170}]
    svc = lambda i, descs, name: idle_s(i, g(i, "g1"), name=name, rec=REC(bgLive=tasks, bgOwnerTops={},
                                                                           bgServiceDescs=descs))
    add("fold: bgServices by session name, a later session of the same name replacing the value in place",
        W(svc(0, ["dev server"], "dup"), svc(1, ["watcher"], "other"), svc(2, ["api", "db"], "dup")))
    add("fold: bgServices of one session", W(svc(0, ["dev server"], "s001"), idle_s(1, g(1, "g1"))))

    # ── the serving join (a candidate in one session, its tracker row a handoff row in another's card)
    srv = lambda tracker: {"goalId": tracker, "msgId": "m1"}
    sender = lambda i, *extra, **kw: idle_s(i, g(i, "g1", promptUuid="u1", t=NOW - 400),
                                            hand("h1", PEER, parentId="g1", t=NOW - 300), *extra, **kw)
    worker = lambda i, tracker="h1", **kw: idle_s(i, g(i, "w1", serving=srv(tracker), t=NOW - 200),
                                                  g(i, "w2", parentId="w1", t=NOW - 150), **kw)
    add("serving: a working candidate folds its rows under its rendered tracker row", W(sender(0), worker(1)))
    add("serving: the candidate's session comes before its tracker's", W(worker(0), sender(1)))
    add("serving: no rendered tracker row: the candidate's own card at the end, its bells from the bell pass",
        W(worker(0, tracker="h9"), idle_s(1, g(1, "g1")), board={"notifyCards": {"w1": True}}))
    add("serving: a tracker id two cards render: the last handoff row wins",
        W(sender(0), sender(1), worker(2)))
    add("serving: a tracker id that only an ask row carries does not fold",
        W(idle_s(0, g(0, "h1", promptUuid="u1")), worker(1)))
    add("serving: two candidates into one tracker row, and one into another row of the same card",
        W(idle_s(0, g(0, "g1", promptUuid="u1", t=NOW - 400), hand("h1", PEER, parentId="g1", t=NOW - 300),
                 hand("h2", PEER, parentId="g1", t=NOW - 350)),
          worker(1), idle_s(2, g(2, "x1", serving=srv("h1"), t=NOW - 100)), idle_s(3, g(3, "y1", serving=srv("h2")))))
    add("serving: a completed candidate folds too; a needs-you one stays an entry card",
        W(sender(0), idle_s(1, g(1, "w1", serving=srv("h1"), nodeComplete=True), store_kw={"status": {"w1": "completed"}}),
          idle_s(2, N("b1", trail=[trees[2][1]], serving=srv("h1"), blocked=True, blockCheckT=250,
                      log=[{"kind": "block", "src": "planner", "ev_t": 140}]), store_kw={"status": {"b1": "blocked"}})))
    add("serving: a numeric tracker never folds", W(sender(0), idle_s(1, g(1, "w1", serving=srv(5)))))
    add("serving: the joined rows' handoff rows take the whoWorking stamp",
        W(sender(0), idle_s(1, g(1, "w1", serving=srv("h1"), promptUuid="u1"),
                            hand("wh", SIDS[2], parentId="w1")), open_s(2, g(2, "g1"))))
    add("serving: a second candidate aimed at a joined row of the first keeps its own card (the tracker rows are "
        "indexed once, before the join)",
        W(sender(0), idle_s(1, g(1, "w1", serving=srv("h1"), promptUuid="u1"), hand("wh", PEER, parentId="w1")),
          idle_s(2, g(2, "x1", serving=srv("wh"), t=NOW - 100))))
    add("serving: a later candidate aimed at a kept candidate's handoff row keeps its own card",
        W(idle_s(0, g(0, "w1", serving=srv("h9"), promptUuid="u1"), hand("wh", PEER, parentId="w1")),
          idle_s(1, g(1, "x1", serving=srv("wh"), t=NOW - 100))))
    add("serving: a tracker row whose children already list ids gets the candidate's first row appended",
        W(idle_s(0, g(0, "g1", promptUuid="u1", t=NOW - 400), hand("h1", PEER, parentId="g1", t=NOW - 300),
                 g(0, "k1", parentId="h1", t=NOW - 250)), worker(1)))

    # ── whoWorking: a handoff row's dot is its recipient's
    deleg = lambda i, peer, **kw: idle_s(i, g(i, "g1", promptUuid="u1"), hand("h1", peer, parentId="g1"), **kw)
    add("who: the recipient works: the row's dot lights", W(deleg(0, SIDS[1]), open_s(1, g(1, "g1"))))
    add("who: the recipient is alive and idle", W(deleg(0, SIDS[1]), idle_s(1, g(1, "g1"))))
    add("who: the recipient is not alive", W(deleg(0, PEER)))
    add("who: the recipient's derivation raised (absent): dark",
        W(deleg(0, SIDS[1]), open_s(1, g(1, "g1"), rec=REC(drop=("interrupting",)))))
    add("who: the recipient is hidden (its null entry adds no name)",
        W(deleg(0, SIDS[1]), open_s(1, g(1, "g1"), flags={"hideFromFeed": True})))
    add("who: the owner works and the recipient does not", W(
        open_s(0, g(0, "g1", promptUuid="u1"), hand("h1", SIDS[1], parentId="g1")), idle_s(1, g(1, "g1"))))
    w = W(deleg(0, SIDS[1]), open_s(1, g(1, "g1")))
    w[0]["alive"][1]["name"] = ""                    # S() reads a falsy name as its default, so the row is set here
    add("who: a working session named \"\" adds no working name: its ask rows keep their own bit, a handoff to it stays dark",
        w)
    add("who: a handoff to the working owner itself", W(open_s(0, g(0, "g1", promptUuid="u1"),
                                                            hand("h1", SIDS[0], parentId="g1"))))

    # ── parked handoffs (the postal log's fold, the maildir, the alive set)
    reg = {DEAD: {"names": ["dead-one", "/TESTDIR/work/d", "#123456"], "flags": None, "store": None},
           DEAD2: {"names": ["", "/x", "nocolor"], "flags": None, "store": None},
           PEER: {"names": ["peer9", "/TESTDIR/work/p", "#ff8800"], "flags": None, "store": None}}

    def pk(log, mail, *sess, board=None, now=None, peers=None):
        b = {"postalLog": log, "mailNew": mail}
        b.update(board or {})
        w = W(*(sess or (idle_s(0, g(0, "g1")),)), board=b, peers=dict(reg, **(peers or {})))
        return with_now(w, now) if now is not None else w
    add("parked: a parked send to a dead session whose mail stands is a needs-you card",
        pk([sent("m1", DEAD, SIDS[0], t=NOW - 600)], [[DEAD, "m1"]]))
    add("parked: the recipient is alive: delivered, no card",
        pk([sent("m1", SIDS[1], SIDS[0], t=NOW - 600)], [[SIDS[1], "m1"]], idle_s(0, g(0, "g1")), idle_s(1, g(1, "g1"))))
    add("parked: the mail was consumed (no new/ file): no card", pk([sent("m1", DEAD, SIDS[0])], []))
    add("parked: the file stands under another recipient's box: no card", pk([sent("m1", DEAD, SIDS[0])], [[DEAD2, "m1"]]))
    add("parked: recalled and bounced: no card; recalled then sent again: back, last in order",
        pk([sent("m1", DEAD, PEER, t=100), sent("m2", DEAD, PEER, t=100), sent("m3", DEAD2, PEER, t=100),
            {"ev": "recall", "id": "m1"}, {"ev": "bounced", "id": "m3"}, {"ev": "recall", "id": "m2"},
            sent("m2", DEAD, PEER, t=100), sent("m4", DEAD, PEER, t=100)],
           [[DEAD, "m1"], [DEAD, "m2"], [DEAD2, "m3"], [DEAD, "m4"]]))
    add("parked: not parked (false, 0, absent), no to_id, no from_id, no id, another event: no card",
        pk([sent("a1", DEAD, PEER, park=False), sent("a2", DEAD, PEER, park=0), sent("a3", DEAD, PEER, park=None),
            sent("a4", "", PEER), sent("a5", DEAD, None), sent("", DEAD, PEER), {"ev": "exec", "id": "a7"},
            sent("a8", DEAD, PEER, ev="unexec")],
           [[DEAD, x] for x in ("a1", "a2", "a3", "a4", "a5", "a7", "a8")]))
    add("parked: times: absent and 0 read the clock, a float truncates, true is 1, a list is no time, negative",
        pk([sent("t1", DEAD, PEER), sent("t2", DEAD, PEER, t=0), sent("t3", DEAD, PEER, t=NOW - 700.9),
            sent("t4", DEAD, PEER, t=True), sent("t5", DEAD, PEER, t=[1]), sent("t6", DEAD, PEER, t=-30),
            sent("t7", DEAD, PEER, t=NOW - 700)],
           [[DEAD, "t%d" % i] for i in range(1, 8)]))
    add("parked: sorted by time, ties in the log's order",
        pk([sent("z1", DEAD, PEER, t=300), sent("z2", DEAD2, PEER, t=100), sent("z3", DEAD, PEER, t=300),
            sent("z4", DEAD, PEER, t=200), sent("z5", DEAD2, PEER, t=100)],
           [[DEAD, "z1"], [DEAD2, "z2"], [DEAD, "z3"], [DEAD, "z4"], [DEAD2, "z5"]]))
    add("parked: names from the registry, else the sid's first 8 code points (an empty name, a non-ASCII sender, an "
        "unknown recipient)",
        pk([sent("n1", DEAD, SIDS[0], t=5), sent("n2", DEAD2, UNI, t=6),
            sent("n3", "zz-unknown-recipient", "zz-unknown-sender", t=7)],
           [[DEAD, "n1"], [DEAD2, "n2"], ["zz-unknown-recipient", "n3"]]))
    add("parked: a 128-character id (kernel._safe_id's cap) is admitted and shows",
        pk([sent("m" * 128, DEAD, PEER, t=NOW - 60)], [[DEAD, "m" * 128]]))
    add("parked: ids a path lookup misreads pass the loader on rows the fold does not park (not parked, recalled, "
        "bounced, another event) and as a sender",
        pk([sent(".", DEAD, PEER, park=False), sent("m1/", DEAD + "/", PEER, park=0), {"ev": "recall", "id": "../x"},
            {"ev": "bounced", "id": "M1"}, {"ev": "exec", "id": ".", "to_id": UNI}, sent("ok2", UNI, PEER, park=None),
            sent("ok", DEAD, UNI + "/.", t=5)], [[DEAD, "ok"]]))
    add("parked and notice: a recipient and a notice file's owner alive but absent (its derivation raised) count as "
        "alive: no parked card, the notice live",
        pk([sent("a1", SIDS[1], PEER, t=NOW - 300)], [[SIDS[1], "a1"]], idle_s(0, g(0, "g1")),
           idle_s(1, g(1, "g1"), rec=REC(drop=("interrupting",))),
           board={"notices": {SIDS[1]: [notice(t=NOW - 50, title="for the absent one")]}}))
    add("parked: a cleared parked id is skipped",
        pk([sent("c1", DEAD, PEER, t=5), sent("c2", DEAD, PEER, t=6)], [[DEAD, "c1"], [DEAD, "c2"]],
           board={"cleared": ["parked:c1"]}))
    add("parked: the same id sent twice: the second's facts in the first's place",
        pk([sent("r1", DEAD, PEER, t=50), sent("r2", DEAD, PEER, t=50), sent("r1", DEAD2, SIDS[0], t=50)],
           [[DEAD, "r1"], [DEAD2, "r1"], [DEAD, "r2"]]))
    add("parked: rows that are not dicts are skipped",
        pk([5, "x", [1], None, sent("d1", DEAD, PEER, t=9)], [[DEAD, "d1"]]))
    add("parked: a float clock: the missing time is int(now), the tint's age a float",
        pk([sent("f1", DEAD, PEER), sent("f2", DEAD, PEER, t=NOW - 10)], [[DEAD, "f1"], [DEAD, "f2"]],
           now=NOW + 0.75))
    add("parked: bells: a card override, the recipient's own flag",
        pk([sent("b1", DEAD, PEER, t=5), sent("b2", DEAD, PEER, t=6), sent("b3", DEAD2, PEER, t=7)],
           [[DEAD, "b1"], [DEAD, "b2"], [DEAD2, "b3"]], board={"notifyCards": {"parked:b1": False, "*": True}},
           peers={DEAD2: {"names": ["", "/x", "nocolor"], "flags": {"notify": False}, "store": None}}))

    # ── notice cards (the notice files, the projection, the cap)
    def nt(files, *sess, board=None, now=None, peers=None):
        b = {"notices": files}
        b.update(board or {})
        w = W(*(sess or (idle_s(0, g(0, "g1")),)), board=b, peers=dict(reg, **(peers or {})))
        return with_now(w, now) if now is not None else w
    add("notice: an owner-less card: Notes, no color, not live", nt({"notes": [notice(t=NOW - 50, title="Heads up")]}))
    add("notice: a session's card: its registry name and color, live while it is alive",
        nt({SIDS[0]: [notice(t=NOW - 50, title="build done")], DEAD: [notice(t=NOW - 60, title="for the dead one")]}))
    add("notice: a file of an unknown, dead sid: its first 8 code points, not live",
        nt({"zz-unknown-file-sid": [notice(t=NOW - 50)], "ab": [notice(t=NOW - 40)]}))
    add("notice: revisions: a higher rev wins, an equal rev posted later wins, a lower one later is ignored",
        nt({"notes": [notice(key="a", rev=1, title="a1"), notice(key="a", rev=2, title="a2"),
                      notice(key="a", rev=1, title="a1 again"), notice(key="b", rev=3, title="b3"),
                      notice(key="b", rev=3, title="b3 later"), notice(key="c", rev=None, title="c0"),
                      notice(key="c", rev=0, title="c0 later")]}))
    add("notice: an expire row retires exactly its (key, rev)",
        nt({"notes": [notice(key="a", rev=2), notice("expire", key="a", rev=2), notice(key="b", rev=2),
                      notice("expire", key="b", rev=1), notice(key="c", rev=None), notice("expire", key="c", rev=0)]}))
    add("notice: an acted row spends exactly its (key, rev): no actions, acted true",
        nt({"notes": [notice(key="a", rev=1, actions=[{"kind": "send", "label": "Go", "body": {"text": "go"}}],
                             dismissOnAction=True),
                      notice("acted", key="a", rev=1), notice(key="b", rev=2, actions=[{"kind": "send"}]),
                      notice("acted", key="b", rev=1), notice(key="c", rev=1, acted=True)]}))
    add("notice: expiry: past and at the clock gone (>=), later, 0 and null kept, true is 1 (gone)",
        nt({"notes": [notice(key="e1", expiresAt=NOW - 1), notice(key="e2", expiresAt=NOW),
                      notice(key="e3", expiresAt=NOW + 1), notice(key="e4", expiresAt=0),
                      notice(key="e5", expiresAt=None), notice(key="e6", expiresAt=True),
                      notice(key="e7", expiresAt=NOW + 0.5)]}))
    add("notice: expiry against a float clock (1781100000.5 >= 1781100000)",
        nt({"notes": [notice(key="e1", expiresAt=NOW), notice(key="e2", expiresAt=NOW + 1)]}, now=NOW + 0.5))
    add("notice: a cleared notice id is skipped (the rev in the id)",
        nt({"notes": [notice(key="a", rev=2), notice(key="b", rev=1)], SIDS[0]: [notice(key="a", rev=2)]},
           board={"cleared": ["notice:notes:a:2", "notice:notes:b:2"]}))
    add("notice: sorted by (t, key) in a file, by (t, itemId) across files",
        nt({"notes": [notice(key="b", t=50), notice(key="a", t=50), notice(key="c", t=40), notice(key="d")],
            "ab": [notice(key="z", t=50), notice(key="a", t=40.9)], "zz": [notice(key="a", t=50)]}))
    many = [notice(key="k%02d" % i, t=1000 + i) for i in range(53)]
    held = [notice(key="m%d" % i, t=900 + i, producer="postal",
                   actions=[{"kind": "quarantine", "body": {"mid": "m%d" % i, "verdict": "allow"}}]) for i in range(3)]
    add("notice: the cap keeps the newest 50 keys; held-mail cards hold no slot",
        nt({SIDS[0]: many + held}))
    add("notice: held mail: a mismatched mid, another kind, no actions outside the owner-less home all count",
        nt({SIDS[0]: [notice(key="k%02d" % i, t=1000 + i) for i in range(49)] + [
            notice(key="m1", t=900, producer="postal", actions=[{"kind": "quarantine", "body": {"mid": "m9"}}]),
            notice(key="m2", t=901, producer="postal", actions=[{"kind": "send", "body": {"mid": "m2"}}]),
            notice(key="m3", t=902, producer="postal"),
            notice(key="m4", t=903, producer="postal", sid="notes"),
            notice(key="m5", t=904, producer="postal", actions=[{"route": "/quarantine", "body": {"mid": "m5"}}])]}))
    add("notice: held mail in the owner-less home: a row with no actions and the home's sid holds no slot",
        nt({"notes": [notice(key="k%02d" % i, t=1000 + i) for i in range(51)] + [
            notice(key="h1", t=5, producer="postal", sid="notes"), notice(key="h2", t=6, producer="postal",
                                                                          actions=[])]}))
    add("notice: board and category: feed's column is the category, another board keeps the needs-you column",
        nt({"notes": [notice(key="a", board="feed", category="needs_input"), notice(key="b", board="ops", category="x"),
                      notice(key="c", category="completed", needsYou=True), notice(key="d", needsYou=True),
                      notice(key="e", board="", category=""), notice(key="f", board="ops", needsYou=1),
                      notice(key="g", category=5)]}))
    add("notice: actions: kind kept and stripped, a route read as its kind, neither is \"\", a kind replaced in place",
        nt({"notes": [notice(key="a", actions=[
            {"label": "Go", "kind": " send\x1c", "body": {"text": "x"}}, {"route": "/send", "label": "Old"},
            {"route": " /send ", "x": 1}, {"label": "none"}, {"kind": "", "route": "/send"}, {"route": "/other"},
            {"kind": "quarantine", "body": {}}, {}])]}))
    add("notice: absent title, producer, body, attachment and expiry; dismissOnAction by truthiness",
        nt({"notes": [notice(key="a"), notice(key="b", title="", producer="", body="", attachment=None,
                                              dismissOnAction="yes"),
                      notice(key="c", title=["a list title"], body={"rich": True}, attachment={"path": "/x"},
                             dismissOnAction=0, expiresAt=0)]}))
    add("notice: lines that are not rows: not a dict, no op, a falsy key, a falsy op",
        nt({"notes": [5, "x", [1], None, {"key": "a"}, {"op": "post", "key": ""}, {"op": "post", "key": None},
                      {"op": "", "key": "b"}, {"op": 0, "key": "c"}, notice(key="ok")]}))
    add("notice: other ops are ignored; an op that is not a string matches none",
        nt({"notes": [notice(key="a"), notice("note", key="a", rev=5), notice(op=7, key="a", rev=9),
                      notice("expire ", key="a", rev=1)]}))
    add("notice: rev and t as floats and bools: int() truncates, true is 1",
        nt({"notes": [notice(key="a", rev=2.7, t=40.9), notice(key="b", rev=True, t=True),
                      notice(key="b", rev=1.5, title="b rev 1.5 ties rev true"), notice(key="c", rev=-1.5, t=-2.5),
                      notice("expire", key="c", rev=-1)]}))
    add("notice: bells: an override on a notice id, the owner-less home's own flags, the master",
        nt({"notes": [notice(key="a", t=5), notice(key="b", t=6)], SIDS[0]: [notice(key="c", t=7)]},
           board={"notifyCards": {"notice:notes:a:1": True, "*": True}},
           peers={"notes": {"names": None, "flags": {"notify": False}, "store": None}}))
    add("notice: a non-ASCII title and body pass through", nt({"notes": [notice(title="Ünïcödé ✓", body="naïve → ok")]}))

    # ── the cleared set and the frame
    add("cleared: a cleared set over session cards, a parked id and a notice id (clearedForeign passes through)",
        nt({"notes": [notice(key="a"), notice(key="b")]}, idle_s(0, g(0, "g1"), g(0, "g2")),
           board={"cleared": ["g2", "notice:notes:a:1", "parked:p1", SIDS[3] + ":g7"],
                  "postalLog": [sent("p1", DEAD, PEER, t=5), sent("p2", DEAD, PEER, t=6)],
                  "mailNew": [[DEAD, "p1"], [DEAD, "p2"]]}))
    add("frame: servingFoldedIds and absentSids both made: right after now, in that order",
        W(sender(0), worker(1), idle_s(2, g(2, "g1"), rec=REC(drop=("interrupting",)))))
    add("frame: every producer at once: session cards, a kept candidate, parked and notice cards, all belled",
        nt({"notes": [notice(key="a", t=NOW - 10)]}, worker(0, tracker="nowhere"), open_s(1, g(1, "g1")),
           board={"postalLog": [sent("p1", DEAD, PEER, t=NOW - 20)], "mailNew": [[DEAD, "p1"]],
                  "notifyCards": {"*": True, "parked:p1": False}}))
    return c


def refuse_cases(km, l6):
    """Worlds the Rust loader refuses (layer7.rs validate): Python answers or raises on them."""
    L = l6
    S, W, N, ST = L.S, L.W, L.N, L.ST
    SIDS, PEER = L.SIDS, L.PEER
    tr, k1, _k2 = session_tree(km, l6, 0)
    c = []

    def add(name, w, post=None):
        c.append(("REFUSE " + name, w[0], w[1]) + ((post,) if post else ()))

    one = lambda board=None, **kw: W(S(0, tr=tr, st=ST(N("g1", trail=[k1], **kw))), board=board)
    w = one()
    w[0]["alive"][0]["name"] = None
    add("an alive name that is null", w)
    w = one()
    del w[0]["world"]["colormap"]
    add("a world without a colormap key", w)
    add("a bells store that is a list", one(board={"notifyCards": ["*"]}))
    add("a serving goalId that is a list", one(serving={"goalId": ["h1"]}))
    for name, row in (("a parked send whose id is a number", sent(5, DEAD, PEER)),
                      ("a parked send whose to_id is a number", sent("m1", 7, PEER)),
                      ("a parked send whose from_id is a list", sent("m1", DEAD, ["x"])),
                      ("a parked send whose time is a string", sent("m1", DEAD, PEER, t="1781099000")),
                      ("a parked send whose time is past i64", sent("m1", DEAD, PEER, t=1e20)),
                      ("a parked send whose body is a dict", sent("m1", DEAD, PEER, body={"x": 1})),
                      ("a recall whose id is a list", {"ev": "recall", "id": ["m1"]}),
                      # kernel._safe_id's rule on a parked send's id and to_id (Python looks the mail up by path)
                      ("a parked send whose id is '.' (the path is the recipient's new/ directory itself)",
                       sent(".", DEAD, PEER, t=5)),
                      ("a parked send whose id ends in a slash (pathlib drops it: m1/ finds m1)",
                       sent("m1/", DEAD, PEER, t=5)),
                      ("a parked send whose to_id ends in a slash", sent("m1", DEAD + "/", PEER, t=5)),
                      ("a parked send whose id starts with a dot", sent(".m1", DEAD, PEER, t=5)),
                      ("a parked send whose id holds a backslash", sent("m\\1", DEAD, PEER, t=5)),
                      ("a parked send whose id holds NUL (pathlib's exists() reads it as no file)",
                       sent("m\x001", DEAD, PEER, t=5)),
                      ("a parked send whose id is 129 characters (past kernel._safe_id's cap)",
                       sent("m" * 129, DEAD, PEER, t=5)),
                      ("a parked send whose id holds a space (outside [A-Za-z0-9._-])", sent("m 1", DEAD, PEER, t=5)),
                      ("a parked send whose id starts with a hyphen", sent("-m1", DEAD, PEER, t=5)),
                      ("a parked send whose to_id is not ASCII (APFS also folds Unicode normalization)",
                       sent("m1", UNI, PEER, t=5))):
        add(name, one(board={"postalLog": [row], "mailNew": [[DEAD, "m1"]]}))
    for now in (1e19, -1e19):
        add("a float clock whose int() is past i64 (%r) while a parked send has no time" % now,
            with_now(one(board={"postalLog": [sent("m1", DEAD, PEER)], "mailNew": [[DEAD, "m1"]]}), now))
    for name, rows in (("a notice post whose key is a number", [notice(key=5)]),
                       ("a notice expire whose key is a list", [notice("expire", key=["a"])]),
                       ("a notice rev that is a string", [notice(rev="2")]),
                       ("a notice time that is a string", [notice(t="40")]),
                       ("a notice time that truncates past i64", [notice(t=1e19)]),
                       ("a notice expiry that is a string", [notice(expiresAt="soon")]),
                       ("notice actions that are a dict", [notice(actions={"kind": "send"})]),
                       ("a notice action that is a string", [notice(actions=["send"])]),
                       ("a notice action kind that is a number", [notice(actions=[{"kind": 5}])]),
                       ("a notice action route that is a list", [notice(actions=[{"route": ["/send"]}])]),
                       ("a notice action body that is a string", [notice(actions=[{"kind": "send", "body": "x"}])])):
        add(name, one(board={"notices": {"notes": rows}}))

    def drop_now(snap):
        snap["frameKeys"] = [k for k in snap["frameKeys"] if k != "now"]
    add("frame keys without now", one(), drop_now)

    def drop_pass(snap):
        snap["frame"].pop("selfHost", None)
    add("a frame key the frame lacks", one(), drop_pass)
    return c


def tints(cm):
    """Python's cm.age_rgb over (colormap, age) and cm.ramp over (v, colormap): the clamp's edges, ages on and halfway
    between the log scale's stops, exact halves of a channel (round half to even), and seeded sweeps."""
    rng = random.Random(7)
    names = (None, "") + COLORMAP_NAMES + ("VIRIDIS", "nope")
    ages = [0, -5, 1, 119, 120, 120.0000001, 121, 345599, 345600, 345601, 1e12, -1e300, 1e300, 60.5, 3600, 86400]
    ages += [round(math.exp(rng.uniform(math.log(100), math.log(400000))), rng.choice((0, 1, 3, 9))) for _ in range(300)]
    # ages whose log position falls on a stop or between two (x = v * (n - 1) near an integer or a half)
    lo, hi = math.log(cm.FADE_LO), math.log(cm.FADE_HI)
    for n in (8, 9, 10):
        for k in range(2 * (n - 1) + 1):
            v = k / (2 * (n - 1))
            ages.append(math.exp(lo + (1.0 - v) * (hi - lo)))
    out = [[name, a, list(cm.age_rgb(a, name))] for name in names for a in ages]
    pts = [k / 16 for k in range(-2, 19)] + [k / 18 for k in range(19)] + [k / 14 for k in range(15)]
    pts += [rng.random() for _ in range(100)] + [0.0625, 0.3125, -0.0, 1.5, -0.5]
    ramps = [[v, name, list(cm.ramp(v, cm.stops_for(name)))] for name in names for v in pts]
    return out, ramps


def edge_main(out):
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer7-")
    try:
        l6, _l5, dw, km, _rec, _root = load_kernel(tmp)
        lines = Lines(km)
        cases = answer_cases(l6, dw, km, edge_cases(km, l6), lines)
        cases += answer_cases(l6, dw, km, refuse_cases(km, l6))
        t_rows, r_rows = tints(km.cm)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer7.py --edge-cases", "lineCoverage": lines.report(),
               "tints": t_rows, "ramps": r_rows, "cases": cases}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
    n_refuse = sum(1 for cs in cases if cs["name"].startswith("REFUSE "))
    sm = summarize(cases)
    lc = doc["lineCoverage"]
    print("edge cases: %d worlds (%d the loader must refuse), %d cards (%d parked, %d notices), %d absent, %d serving "
          "folded; %d tint and %d ramp points; lines %d of %d run, missed %s -> %s (%.2f MB)" % (
              len(cases), n_refuse, sm["cards"], sm["parked"], sm["notices"], sm["absent"], sm["folded"], len(t_rows),
              len(r_rows), lc["ran"], lc["lines"], json.dumps(lc["missed"]), out, os.path.getsize(out) / 1e6))
    return 0


# ───────────────────────────── --fuzz ─────────────────────────────
# Random worlds from dump_layer6's generator (layer 5's random trees and stores, layer 6's node, recorded and board
# decorations), then what layer 7 reads: the clock (the fixed NOW most of the time, an integral float, a fractional
# one, and clocks near the trees' own times so the tints spread), the colormap (every name, null, upper case, unknown),
# handoff peers aimed at the world's alive sessions (so the whoWorking stamp meets working, idle, hidden and raising
# recipients), serving trackers aimed at the world's node keys (so the join hits, misses and meets repeated ids), the
# bells (the master, the turn switch, card overrides of every falsy and truthy shape on the world's item ids, session
# notify flags), a postal log of parked sends, recalls, bounces, re-sends and noise with a maildir that stands for some,
# notice files (owner-less, a session's, a dead sid's) with revisions, expires, acted rows, expiries around the clock,
# boards, categories, actions and held-mail rows, now and then past the cap, and cleared ids for parked and notice
# cards. Seeded: a failure replays.
FZ_NOW = (NOW,) * 10 + (float(NOW), NOW + 0.5, 300, 1000, 5000, 200000, 1781000500, 1781000000.25)
FZ_CMAP = (None, None, None, "aurora", "") + COLORMAP_NAMES + ("VIRIDIS", "Plasma", "nope")
FZ_TRUTHY = (True, True, False, False, 0, 1, "", "x", None, [], {"a": 1})


def fz_postal(rng, sids, now):
    pool_to = [DEAD, DEAD2, "eeeeeeee-dead-0001"] + sids     # safe ids (kernel._safe_id): the loader refuses others
    pool_from = [DEAD, PEER, "zz-sender", "", UNI] + sids
    mids = ["m%d" % i for i in range(6)]
    log, mail = [], []
    for _ in range(rng.choice((0, 0, 1, 2, 3, 5, 8))):
        r = rng.random()
        mid = rng.choice(mids + [""] * 1)
        if r < 0.65:
            row = {"ev": "sent", "id": mid, "to_id": rng.choice(pool_to + [""]), "from_id": rng.choice(pool_from)}
            p = rng.random()
            if p < 0.8:
                row["park"] = rng.choice((True, True, True, 1, False, 0, None))
            t = rng.random()
            if t < 0.6:
                row["t"] = rng.choice((now - 600, now - 600, 100, 150.9, 0, None, True, [1], -30, int(now) - 5))
                if isinstance(row["t"], float) and abs(row["t"]) >= 9e18:
                    row["t"] = 5
            if rng.random() < 0.8:
                row["body"] = rng.choice(("hand-off " + mid, "", "Ünï " * rng.randint(1, 100)))
            log.append(row)
        elif r < 0.85:
            log.append({"ev": rng.choice(("recall", "bounced")), "id": mid})
        elif r < 0.95:
            log.append({"ev": rng.choice(("exec", "unexec", "read", None)), "id": mid,
                        "to_id": rng.choice(pool_to + [UNI])})
        else:
            log.append(rng.choice((5, "x", [1], None)))
    if rng.random() < 0.15:
        # a re-send around another parked send of the same time: the fold keeps the first send's place, and the sort by
        # time keeps the fold's order on the tie
        a, b, to = rng.sample(mids, 2) + [rng.choice((DEAD, DEAD2, "eeeeeeee-dead-0001"))]
        t = rng.choice((now - 600, 100))
        log += [{"ev": "sent", "id": a, "to_id": to, "from_id": PEER, "park": True, "t": t},
                {"ev": "sent", "id": b, "to_id": to, "from_id": PEER, "park": True, "t": t},
                {"ev": "sent", "id": a, "to_id": to, "from_id": rng.choice(pool_from[:3]), "park": True, "t": t}]
    for row in log:
        if isinstance(row, dict) and row.get("to_id") and row.get("id") and rng.random() < 0.7:
            mail.append([row["to_id"], row["id"]])
    if rng.random() < 0.2:
        mail.append([rng.choice(pool_to), rng.choice(mids)])
    return log, mail


def fz_notices(rng, sids, now):
    files = {}
    owners = ["notes", "notes"] + sids + ["zz-file-owner", DEAD]
    for _ in range(rng.choice((0, 0, 0, 1, 1, 2))):
        sid = rng.choice(owners)
        rows = files.setdefault(sid, [])
        big = rng.random() < 0.02                     # past the cap: every key posted once, then the usual rows
        nkeys = 52 + rng.randint(0, 3) if big else rng.randint(1, 5)
        keys = ["k%d" % i for i in range(nkeys)]
        if big:
            rows.extend({"op": "post", "key": k, "t": now - 3000 + i} for i, k in enumerate(keys))
        for _ in range(rng.randint(0, 6) if big else nkeys + rng.randint(0, 6)):
            r = rng.random()
            k = rng.choice(keys)
            if r < 0.7:
                row = {"op": "post", "key": k}
                for f, vals, p in (("rev", (0, 1, 1, 2, 3, 2.5, True, None), 0.8),
                                   ("t", (now - 50, now - 5000, 100, 40.9, 0, True, now - 50), 0.85),
                                   ("title", ("A notice", "", "Ünïcödé", ["t"]), 0.7),
                                   ("producer", ("postal", "build", "", None), 0.5),
                                   ("body", ("text", "", {"rich": 1}), 0.4),
                                   ("attachment", (None, {"path": "/x", "kind": "image"}), 0.2),
                                   ("expiresAt", (0, None, now - 1, now, now + 100, True, now + 0.5), 0.25),
                                   ("needsYou", FZ_TRUTHY, 0.4), ("board", ("feed", "ops", "", None), 0.3),
                                   ("category", ("needs_input", "completed", "", "x", 5), 0.3),
                                   ("dismissOnAction", FZ_TRUTHY, 0.2), ("acted", FZ_TRUTHY, 0.05),
                                   ("sid", ("notes", sid, None), 0.1)):
                    if rng.random() < p:
                        row[f] = rng.choice(vals)
                if isinstance(row.get("t"), float) and abs(row["t"]) >= 9e18:
                    row["t"] = 5
                if rng.random() < 0.4:
                    acts = []
                    for _ in range(rng.choice((0, 1, 1, 2))):
                        a = {}
                        if rng.random() < 0.7:
                            a["kind"] = rng.choice(("send", "quarantine", " send ", "", None, "quarantine\x1c"))
                        if rng.random() < 0.3:
                            a["route"] = rng.choice(("/send", " /send", "/x", ""))
                        if rng.random() < 0.7:
                            a["body"] = rng.choice(({"mid": k}, {"mid": "other"}, {}, None, {"text": "go"}))
                        if rng.random() < 0.5:
                            a["label"] = "Do it"
                        acts.append(a)
                    row["actions"] = acts
                rows.append(row)
            elif r < 0.85:
                rows.append({"op": rng.choice(("expire", "acted")), "key": k, "rev": rng.choice((0, 1, 2, 3, None))})
            elif r < 0.95:
                rows.append(rng.choice(({"op": "note", "key": k}, {"key": k}, {"op": "post", "key": ""}, {"op": "post"})))
            else:
                rows.append(rng.choice((5, "x", [1], None)))
    return files


def fz_world7(rng, km, l6, l5, l3, l2, serial):
    snap, trees = l6.fz_world(rng, km, l5, l3, l2, serial)
    sids = [r["sid"] for r in snap["alive"]]
    snap["world"]["now"] = now = rng.choice(FZ_NOW)
    snap["world"]["colormap"] = rng.choice(FZ_CMAP)
    nodes = []
    for sid in sids:
        st = snap["sessions"][sid]["store"]
        for nid, nd in ((st or {}).get("nodes") or {}).items():
            nodes.append((sid, nid, nd))
    keys = [nid for _s, nid, _n in nodes]
    hkeys = [nid for _s, nid, nd in nodes if isinstance(nd.get("handoff"), dict)]
    for _sid, _nid, nd in nodes:
        if isinstance(nd.get("handoff"), dict) and rng.random() < 0.5:
            nd["handoff"]["peer"] = rng.choice(sids)
        if keys and rng.random() < 0.15:
            pool = hkeys * 4 + keys + ["", PEER + ":g1", 5, "nowhere"]
            nd["serving"] = {"goalId": rng.choice(pool)}
            if rng.random() < 0.5:
                nd["parentId"] = None                # a top, so its card can be a candidate
    stores = [(sid, snap["sessions"][sid]["store"]) for sid in sids if snap["sessions"][sid]["store"]]
    if stores and rng.random() < 0.5:
        # a tracker row and its candidate: a handoff child under some top of one store, a top of another (or the same)
        # serving it; whether the join meets depends on what layer 6 renders (a hidden, cleared or delegation-only top
        # renders no row, a needs-you candidate stays an entry card)
        hsid, hst = rng.choice(stores)
        tops = [k for k, nd in hst["nodes"].items() if not nd.get("parentId")]
        if tops:
            hk = "hx%d" % rng.randint(0, 2)
            top = rng.choice(tops)
            hst["nodes"][hk] = {"id": hk, "text": "↪ delegated to peer9: the work", "t": rng.choice((150, now - 100)),
                                "parentId": top, "handoff": {"peer": rng.choice(sids + [PEER]), "msgId": "m1"}}
            if rng.random() < 0.6:
                hst["status"][top] = "working"
                hst["nodes"][top].pop("cleared", None)
            if len(stores) > 1 and rng.random() < 0.6:     # the same tracker id rendered in a second store: the last row wins
                osid, ost = rng.choice([x for x in stores if x[0] != hsid])
                otops = [k for k, nd in ost["nodes"].items() if not nd.get("parentId")]
                if otops:
                    otop = rng.choice(otops)
                    ost["nodes"][hk] = dict(hst["nodes"][hk], parentId=otop)
                    if rng.random() < 0.6:
                        ost["status"][otop] = "working"
                        ost["nodes"][otop].pop("cleared", None)
            csid, cst = rng.choice(stores)
            ctops = [k for k, nd in cst["nodes"].items() if not nd.get("parentId") and k != hk]
            for k in rng.sample(ctops, min(len(ctops), rng.choice((1, 1, 2)))):
                cst["nodes"][k]["serving"] = {"goalId": hk}
                if rng.random() < 0.6:
                    cst["status"][k] = rng.choice(("working", "completed"))
    board = snap["board"]
    board["postalLog"], board["mailNew"] = fz_postal(rng, sids, now)
    board["notices"] = fz_notices(rng, sids, now)
    items = keys + ["%s:%s" % (k, s) for k in ("provisional", "blocked", "awaiting") for s in sids]
    items += ["parked:" + r["id"] for r in board["postalLog"] if isinstance(r, dict) and isinstance(r.get("id"), str)]
    items += ["notice:%s:k%d:%d" % (s, i, v) for s in board["notices"] for i in range(3) for v in (0, 1, 2)]
    cards = {}
    if rng.random() < 0.35:
        cards["*"] = rng.choice((True, True, False, 1, 0, "x", None))
    if rng.random() < 0.05:
        cards["*turns"] = True
    for _ in range(rng.choice((0, 0, 0, 1, 2, 4))):
        if items:
            cards[rng.choice(items)] = rng.choice((True, False, False, 0, 1, "", None))
    board["notifyCards"] = cards
    for sid in sids:
        S = snap["sessions"][sid]
        if rng.random() < 0.25 and (S["flags"] is None or isinstance(S["flags"], dict)):
            f = dict(S["flags"] or {})
            f["notify"] = rng.choice((True, False, None, 0, 1, "yes"))
            S["flags"] = f
    rows = snap["alive"]
    if len(rows) > 1 and rng.random() < 0.2:           # two sessions of one name: bgServices keeps the first one's place
        rows[-1]["name"] = rows[0]["name"]
    for sid in sids:                                   # service chips, so bgServices has several names to order
        rec = snap["sessions"][sid]["recorded"]
        if rng.random() < 0.3 and "bgLive" in rec and "bgOwnerTops" in rec:
            rec["bgLive"] = [{"tid": "t1", "desc": "npm test", "t": 170}]
            rec["bgOwnerTops"] = {}
            rec["bgServiceDescs"] = rng.choice((["dev server"], ["api", "db"]))
    for r in rows:
        if rng.random() < 0.04:                        # a falsy name: a working session adds no working name
            r["name"] = ""
    if rng.random() < 0.1 and "notes" in board["notices"]:
        snap["peers"]["notes"] = {"names": None, "flags": {"notify": rng.choice((True, False))}, "store": None}
    extra = [x for x in items if x.startswith(("parked:", "notice:"))]
    if extra and rng.random() < 0.3:
        board["cleared"] = sorted(set(board["cleared"]) | set(rng.sample(extra, min(len(extra), rng.randint(1, 3)))))
    for psid in (DEAD, DEAD2):
        if rng.random() < 0.5:
            snap["peers"][psid] = {"names": rng.choice((["dead-one", "/x", "#123456"], ["", "/x", "nocolor"], None)),
                                   "flags": rng.choice((None, {"notify": True}, {"notify": False})), "store": None}
    return snap, trees


def fuzz_specs(rng, km, l6, l5, n, seed):
    l3 = l5.load_l4().load_l3()
    l2 = l3.load_l2()
    return [("fuzz seed %d world %d" % (seed, i),) + fz_world7(rng, km, l6, l5, l3, l2, i) for i in range(n)]


RUST_SUMMARY = re.compile(r"^feed edge set(?: \[planted: [a-z-]+\])?: (\d+) cases, (\d+) loaded and compared \((\d+) "
                          r"sessions, (\d+) cards, (\d+) tree rows, (\d+) absent, (\d+) serving folded, (\d+) parked, "
                          r"(\d+) notices, (\d+) values\), (\d+) refused at load \(every REFUSE case\), (\d+) differences "
                          r"in (\d+) cases ")


def fuzz_main(n, seed, plant, binary, out, with_lines):
    """Make `n` worlds from `seed`, answer them in Python, and compare with `romp-spike feed` (under `plant`, if
    given). 0 when there is no difference (with a plant: when the plant is caught), 1 otherwise, 2 on an error."""
    tmp = tempfile.mkdtemp(prefix="romp-spike-layer7-fuzz-")
    try:
        t0 = time.monotonic()
        l6, l5, dw, km, _rec, _root = load_kernel(tmp)
        specs = fuzz_specs(random.Random(seed), km, l6, l5, n, seed)
        lines = Lines(km) if with_lines else None
        cases = answer_cases(l6, dw, km, specs, lines)
        doc = {"schema": EDGE_SCHEMA, "python": platform.python_version(),
               "generatedBy": "tools/rust-spike/dump_layer7.py --fuzz %d --seed %d" % (n, seed), "cases": cases}
        path = out or os.path.join(tmp, "layer7_fuzz.json")
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(doc, ensure_ascii=True, allow_nan=False) + "\n")
        py_s = time.monotonic() - t0
        sm = summarize(cases)
        print("fuzz seed %d: %d worlds, %d cards (%d parked, %d notices), %d absent sessions, %d serving folded, made "
              "and answered by Python in %.1f s, %.1f MB" % (seed, n, sm["cards"], sm["parked"], sm["notices"],
                                                            sm["absent"], sm["folded"], py_s,
                                                            os.path.getsize(path) / 1e6))
        if lines is not None:
            lc = lines.report()
            print("  python: lines %d of %d run, missed %s" % (lc["ran"], lc["lines"], json.dumps(lc["missed"])))
        p = subprocess.run([binary, "feed", path] + (["--plant", plant] if plant else []), capture_output=True,
                           text=True)
        out_lines = p.stdout.splitlines()
        summary = [m for m in map(RUST_SUMMARY.match, out_lines) if m]
        for line in out_lines:
            print("  rust: " + line)
        if p.returncode not in (0, 1) or len(summary) != 1:
            print("dump_layer7: romp-spike feed exited %d without its summary line: %s" % (
                p.returncode, p.stderr.strip()[-2000:]), file=sys.stderr)
            return 2
        g = list(map(int, summary[0].groups()))
        ncases, loaded, values, refused, diffs, differing = g[0], g[1], g[9], g[10], g[11], g[12]
        if ncases != n or (plant is None and (loaded != n or refused != 0 or diffs != 0 or p.returncode != 0)):
            print("fuzz seed %d: FAILED, %d differences over %d worlds (%d loaded, %d refused at load)" % (
                seed, diffs, ncases, loaded, refused))
            return 1
        if plant is None:
            print("fuzz seed %d: OK, %d worlds, %d values compared, 0 differences" % (seed, n, values))
            return 0
        print("fuzz seed %d, planted %s: %s, %d differences in %d of %d worlds" % (
            seed, plant, "caught" if diffs else "NOT CAUGHT", diffs, differing, n))
        return 0 if diffs else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def rust_binary(given):
    if given:
        return given
    cargo = shutil.which("cargo") or os.path.expanduser("~/.cargo/bin/cargo")
    rust = os.path.join(os.path.dirname(os.path.dirname(HERE)), "rust")
    subprocess.run([cargo, "build", "--release", "--offline", "-q", "-p", "romp-spike"], cwd=rust, check=True)
    return os.path.join(rust, "target", "release", "romp-spike")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dumps", nargs="*", help="dump directories (dump_world.py --out)")
    ap.add_argument("--edge-cases", metavar="OUT", help="write the synthetic edge cases to OUT")
    ap.add_argument("--fuzz", type=int, metavar="N", help="compare N random worlds with romp-spike feed")
    ap.add_argument("--seed", type=int, default=1, help="the --fuzz seed (default 1)")
    ap.add_argument("--plant", choices=PLANTS, help="--fuzz against romp-spike feed --plant NAME: the check passes "
                    "when the plant is caught")
    ap.add_argument("--bin", help="a built romp-spike for --fuzz (default: cargo build --release)")
    ap.add_argument("--out", help="keep the --fuzz cases in this file (default: a temporary file, removed)")
    ap.add_argument("--lines", action="store_true", help="--fuzz: also report the layer-7 lines run")
    a = ap.parse_args(argv)
    if a.edge_cases:
        return edge_main(a.edge_cases)
    if a.fuzz is not None:
        if a.fuzz < 1:
            ap.error("--fuzz N needs N >= 1")
        return fuzz_main(a.fuzz, a.seed, a.plant, rust_binary(a.bin), a.out, a.lines)
    if not a.dumps:
        ap.error("name at least one dump directory, or --edge-cases OUT, or --fuzz N")
    return main_dumps(a.dumps)


if __name__ == "__main__":
    sys.exit(main())
