#!/usr/bin/env python3
"""A judge pass over a large goal store and a long session walks each of them once, not once per item (2026-10-23).

On a session whose goal store held thousands of nodes and whose transcript was long and restored from its assembly
document, the judges spent most of their time repeating walks whose inputs had not changed:
  * rollup_status's pre-pass lifted the open children of each completed delegation tracking node with its own pass over
    the whole node map, so every rollup (the planner's, the closer's, every writer's) cost the store's size times its
    number of completed delegations. _lift_completed_handoffs gives the same result from one pass.
  * the planner pass walked every atom of the parse to look for task calls (em.plan_atoms, for a session with no task
    store), to collect every uuid (_note_checked) and to index the prompt anchors (_latch_prompt_msg_ids), and it read
    each segment's atoms for its work check (plan_units). Over a restored tree each of those reads built or touched
    pre-cut atoms, though a restored turn already carries what each walk wants: its tool calls (`tools`), its atoms'
    uuids (`uuids`) and each segment's work verdict (`w`), which the kernel's own walkers read.

Pinned four ways:
  * oracles: the one-pass lift equals the per-node calls (the old pre-pass, copied below as _ref_prepass) over hundreds
    of seeded stores with chains, cycles, self-parents and dangling parents, and the full rollup writes the same store
    as one running the old pre-pass, through several passes over stores of thousands of nodes with writes between the
    passes; each planner walk equals the old whole walk (copied below) over every golden scenario with a compaction
    appended, whole and restored, and over a long synthetic session with task calls, workless asks and mail;
  * the whole pass: the real _plan_session, run three times over a restored long session and a store with delegations
    and anchors the parse does not hold, writes the same store as with the old walks patched in, and touches few
    pre-cut atoms where the old walks touched each one several times;
  * counts: a rollup's passes over the node map do not grow with the number of delegations; the task search, the uuid
    set and the anchor index build no pre-cut atom they do not return; the uuid set is read only for a candidate;
    plan_units reads no restored segment's atoms for its work check;
  * the order rule the one-pass lift keeps, spelled out on a two-tracker chain.
Synthetic only: invented session ids, the notes-api demo's wording, the golden transcript builders."""
import contextlib
import copy
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source  # noqa: F401  the direct run's floor (tests/romp_load.py)

HERE = os.path.dirname(os.path.realpath(__file__))
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, kernel_module, compacting_variant   # noqa: E402

PSID = "8c1f3a20-4b6d-4e19-a7c2-36d5e0f19b84"    # the planner session (a private synthetic sid: this module saves stores)
RSID = "3e7b9d10-5c2a-4f81-b6e4-91a0d2c7f356"    # the rollup stores
PEER = "c4d2e6f8-1a3b-4c5d-8e9f-0a1b2c3d4e5f"     # the peer the delegations went to
T0 = 1_781_000_000
TASK_NAMES = ("TaskCreate", "TaskUpdate")


def _jd():
    return kernel_module().jd


# ── the old code, verbatim (the reference every oracle compares with) ──
def _ref_prepass(store):
    """rollup_status's pre-pass before 2026-10-23, verbatim but for the move count it adds up and the 2026-10-30 rule on
    which completions lift (_delegation_completed): one _lift_handoff_children call, a pass over every node, per
    completed tracking node."""
    jd = _jd()
    nodes = store["nodes"]
    moved = 0
    for _hid in list(nodes):
        _hn = nodes[_hid]
        if isinstance(_hn.get("handoff"), dict) and _hn.get("nodeComplete") and not _hn.get("rolledUp") \
                and jd._delegation_completed(_hn):
            moved += jd._lift_handoff_children(store, _hid)
    return moved


def _ref_plan_atoms(session):
    """em.plan_atoms before 2026-10-23, verbatim: every atom of every turn is read."""
    out, ids = [], set()
    for turn in session.get("turns", []):
        for a in turn["atoms"]:
            if a.get("type") != "assistant":
                continue
            lz = a.get("lazy")
            if lz is not None:
                calls = [(i, nm) for i, nm in (lz.get("tu") or [])]
            else:
                calls = [(b.get("id"), b.get("name")) for b in em._content(a.get("message"))
                         if isinstance(b, dict) and b.get("type") == "tool_use"]
            mine = [i for i, nm in calls if nm in ("TaskCreate", "TaskUpdate")]
            if mine:
                out.append(a); ids.update(mine)
    if not ids:
        return out
    for turn in session.get("turns", []):
        for a in turn["atoms"]:
            if a.get("type") != "user":
                continue
            lz = a.get("lazy")
            if lz is not None:
                got = lz.get("tr") or []
            else:
                got = [b.get("tool_use_id") for b in em._content(a.get("message")) if isinstance(b, dict) and b.get("type") == "tool_result"]
            if ids.intersection(got):
                out.append(a)
    return out


def _ref_atom_uuids(session):
    """The uuid set _plan_session handed _note_checked before 2026-10-23, verbatim."""
    return {a.get("uuid") for turn in session.get("turns") or [] for a in turn.get("atoms") or [] if a.get("uuid")}


def _ref_by_uuid(session):
    """The anchor index _latch_prompt_msg_ids built before 2026-10-23, verbatim."""
    return {a["uuid"]: a for turn in session.get("turns") or [] for a in turn.get("atoms") or [] if a.get("uuid")}


def _ref_latch_prompt_msg_ids(session, store):
    """_latch_prompt_msg_ids before 2026-10-23, verbatim."""
    jd = _jd()
    cands = [nd for nd in store.get("nodes", {}).values()
             if isinstance(nd, dict) and nd.get("parentId") is None and nd.get("promptUuid") and "promptMsgId" not in nd]
    if not cands:
        return 0
    by_uuid = {a["uuid"]: a for turn in session.get("turns") or [] for a in turn.get("atoms") or [] if a.get("uuid")}
    sid = str(store.get("rompUuid") or "")
    n = 0
    for nd in cands:
        a = by_uuid.get(nd["promptUuid"])
        if a is None:
            continue
        dels = [m for m, k in em.postal_pairs(jd._atom_text(a)) if k == "delegate" and jd._delegate_sender(m, sid)]
        nd["promptMsgId"] = str(dels[-1]) if dels else ""
        n += 1
    return n


def _ref_seg_work(seg):
    """plan_units' work check before 2026-10-23: the segment's atoms, always read."""
    return _jd()._has_asst_work(seg["atoms"])


# ── counting, with nothing under test replaced ──
class _Calls:
    """Counts the calls into one code object through a profile hook on this thread; `test` (optional) is asked about
    each call's frame and counts it under `hits` when it says yes."""

    def __init__(self, code, test=None):
        self.code, self.test, self.n, self.hits = code, test, 0, 0

    def __enter__(self):
        def hook(frame, event, arg):
            if event == "call" and frame.f_code is self.code:
                self.n += 1
                if self.test is not None and self.test(frame):
                    self.hits += 1
        self._prev = sys.getprofile()
        sys.setprofile(hook)
        return self

    def __exit__(self, *exc):
        sys.setprofile(self._prev)


class _CountingNodes(dict):
    """A node map that counts the passes over it: iteration, keys, values and items."""
    passes = 0

    def __iter__(self):
        self.passes += 1
        return dict.__iter__(self)

    def keys(self):
        self.passes += 1
        return dict.keys(self)

    def values(self):
        self.passes += 1
        return dict.values(self)

    def items(self):
        self.passes += 1
        return dict.items(self)


# ── synthetic stores ──
def _node(jd, nid, text, parent, t, **kw):
    return jd.GuardedNode(dict({"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False,
                                "cleared": False, "trail": [], "t": t, "mt": t, "log": []}, **kw))


def _wild_nodes(rng, n):
    """Plain nodes in a random insertion order for the pre-pass alone: random parents (a dangling id, self-parents and
    cycles included), trackers complete or open, rolled-up, cleared, and a few handoff values that are not dicts."""
    ids = ["%s:g%d" % (RSID, i) for i in range(1, n + 1)]
    order = ids[:]
    rng.shuffle(order)
    nodes = {}
    for nid in order:
        r = rng.random()
        parent = (None if r < 0.2 else "%s:g%d" % (RSID, n + 7) if r < 0.25 else nid if r < 0.28 else rng.choice(ids))
        nd = {"id": nid, "parentId": parent, "nodeComplete": rng.random() < 0.45, "cleared": rng.random() < 0.12}
        h = rng.random()
        if h < 0.35:
            nd["handoff"] = {"peer": PEER, "msgId": "m-api-%s" % nid.rsplit("g", 1)[1]}
        elif h < 0.38:
            nd["handoff"] = "legacy"
        if rng.random() < 0.1:
            nd["rolledUp"] = True
        nodes[nid] = nd
    return nodes


def _forest_store(jd, seed, n, sid=RSID):
    """A goal store of `n` nodes in a random insertion order with acyclic parents: about 30 percent tracking nodes, most
    of them completed by the courier; done, blocked and cleared nodes, every flag from a diary row."""
    rng = random.Random(seed)
    specs, made = [], []
    for i in range(1, n + 1):
        nid = "%s:g%d" % (sid, i)
        parent = None if (not made or rng.random() < 0.3) else rng.choice(made[-300:])
        specs.append((nid, parent, rng.random() < 0.3))
        made.append(nid)
    order = list(range(n))
    rng.shuffle(order)
    store = {"rompUuid": sid, "seq": n, "nodes": {}, "placements": {}, "status": {}}
    nodes = store["nodes"]
    for k in order:
        nid, parent, tracker = specs[k]
        text = ("↪ delegated to api: notes-api part %d" % k) if tracker else ("notes-api: step %d" % k)
        nodes[nid] = _node(jd, nid, text, parent, T0 + k, **({"handoff": {"peer": PEER, "msgId": "m-api-%d" % k}} if tracker else {}))
    for k in order:
        nid, _parent, tracker = specs[k]
        nd, r, ev = nodes[nid], rng.random(), T0 + 20_000 + k
        if tracker and r < 0.6:
            jd.record_verdict(store, nd, "courier", "done", ev)
        elif r < 0.22:
            jd.record_verdict(store, nd, "closer", "done", ev, why="shipped")
        elif r < 0.30:
            jd.record_verdict(store, nd, "closer", "block", ev, why="needs the user's word")
        elif r < 0.36:
            jd.record_verdict(store, nd, "user", "clear", ev)
    store["lastNode"] = specs[rng.randrange(n)][0]
    return store


def _ancestors(nodes, nid):
    seen, p = set(), nodes[nid].get("parentId")
    while p is not None and p in nodes and p not in seen:
        seen.add(p)
        p = nodes[p].get("parentId")
    return seen


def _mutate(jd, store, rng, k):
    """Another writer between two rollups: a few delegations complete, a few open nodes come back under a completed
    tracker (the save-rebase race republishing a stale parent), a few steps are ruled done, blocked or reopened."""
    nodes = store["nodes"]
    ids = sorted(nodes)
    ev = T0 + 50_000 + 100 * k
    tracker = lambda i: isinstance(nodes[i].get("handoff"), dict)   # noqa: E731
    open_trackers = [i for i in ids if tracker(i) and not nodes[i].get("nodeComplete") and not nodes[i].get("cleared")]
    for i in rng.sample(open_trackers, min(8, len(open_trackers))):
        jd.record_verdict(store, nodes[i], "courier", "done", ev)
    done_trackers = [i for i in ids if tracker(i) and nodes[i].get("nodeComplete") and not nodes[i].get("rolledUp")]
    movable = [i for i in ids if not tracker(i) and not nodes[i].get("nodeComplete") and not nodes[i].get("cleared")]
    for i in rng.sample(movable, min(12, len(movable))):
        if done_trackers:
            t = rng.choice(done_trackers)
            if t != i and i not in _ancestors(nodes, t):
                nodes[i]["parentId"] = t
    for i in rng.sample(ids, 8):
        r = rng.random()
        if r < 0.4:
            jd.record_verdict(store, nodes[i], "closer", "done", ev, why="shipped")
        elif r < 0.7:
            jd.record_verdict(store, nodes[i], "closer", "block", ev, why="needs the user's word")
        else:
            jd.record_verdict(store, nodes[i], "user", "reopen", ev)


def _canon(store):
    """A store as JSON compares it: transient keys dropped, and each diary row's arrival stamp (wall clock)."""
    d = json.loads(json.dumps({k: v for k, v in store.items() if not str(k).startswith("_")}, sort_keys=True, default=str))
    for nd in (d.get("nodes") or {}).values():
        for e in nd.get("log") or []:
            e.pop("at", None)
    return json.dumps(d, sort_keys=True)


# ── synthetic sessions ──
def _long_records(tag, turns, tasks_every=0, workless_every=0, mail_every=0):
    """A long notes-api session: `turns` turns of ten atoms each (an ask, four tool round trips, a closing reply), then
    a compaction and two tail turns, so a restore holds all of those turns before the cut. Every `tasks_every`-th turn
    also declares a step (TaskCreate, then TaskUpdate, each with its result); every `workless_every`-th ask gets no reply
    (the next ask follows it); every `mail_every`-th ask is a delegate mail."""
    t = NOW - 20 * 86400
    recs, last = [], [None]

    def add(r):
        recs.append(r)
        last[0] = r["uuid"]
    for k in range(turns):
        if mail_every and k % mail_every == mail_every - 1:
            text = ("Please add retry handling to the notes-api sync job, part %d\n<!-- romp-msg-id: m-%s-%d -->\n"
                    "<!-- romp-msg-kind: delegate -->" % (k, tag, k))
        else:
            text = "notes-api: tighten the sync step, part %d" % k
        add(G.uline(t, text, "u_%s_%d" % (tag, k), last[0]))
        t += 5
        if workless_every and k % workless_every == workless_every - 1:
            continue
        if tasks_every and k % tasks_every == tasks_every - 1:
            for j, name in enumerate(TASK_NAMES):
                a = "a_%s_%d_t%d" % (tag, k, j)
                add(G.aline(t, "", a, last[0], tools=(name,), stop="tool_use"))
                add(G.trline(t + 1, "tu_%s_0" % a, "r_%s_%d_t%d" % (tag, k, j), a,
                             content=("Task #%d created successfully" % (k + 1)) if j == 0 else "Updated task"))
                t += 2
        for j in range(4):
            a = "a_%s_%d_%d" % (tag, k, j)
            add(G.aline(t, "reading the sync code" if j == 0 else "", a, last[0], tools=("Read",), stop="tool_use"))
            add(G.trline(t + 1, "tu_%s_0" % a, "r_%s_%d_%d" % (tag, k, j), a, content="ok"))
            t += 2
        add(G.aline(t, "the sync step, part %d, is tightened" % k, "a_%s_%d_end" % (tag, k), last[0], stop="end_turn"))
        t += 30
    b, sm = "b_%s" % tag, "s_%s" % tag
    recs += [G.compact_line(t, b, last[0]), G.compact_summary_line(t + 1, sm, b),
             G.uline(t + 10, "after the compaction, what remains?", "u_%s_x1" % tag, sm),
             G.aline(t + 20, "the retry budget remains", "a_%s_x1" % tag, "u_%s_x1" % tag, stop="end_turn"),
             G.uline(t + 30, "then close it out", "u_%s_x2" % tag, "a_%s_x1" % tag),
             G.aline(t + 40, "closing it", "a_%s_x2" % tag, "u_%s_x2" % tag, stop="end_turn")]
    return recs


class _Trees(Harness):
    """The harness's parse under a chosen session id, and the restore the kernel does at a boot: a whole parse, the
    document written from it, then a fresh process's parse."""

    def parse_as(self, path, sid, modes=None):
        tree = em.parse_session(path, rompuuid=sid, name="impl", dir="/TESTDIR", candidate_files=[path],
                                states=self.states, postal_log=self.sent, now=NOW, asm_mode_out=modes)
        self._trees[path] = tree
        return tree

    def write_as(self, name, records, sid):
        d = self.td / name
        d.mkdir(exist_ok=True)
        p = d / (sid + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in records))
        self.states, self.sent = None, []
        return str(p)

    def restore_as(self, path, sid):
        self.fresh()
        self.parse_as(path, sid)
        self.assertTrue(em.asm_checkpoint_write(path, sid, tree=self._trees.get(path)), em.asm_checkpoint_stats())
        self.fresh()
        modes = []
        tree = self.parse_as(path, sid, modes)
        return tree, modes

    def fixture_trees(self):
        """(name, shape, tree): every golden scenario with a compaction appended, whole and restored, then the long
        session with task calls, workless asks and mail, restored."""
        out = []
        for name in sorted(G.SINGLE_FILE):
            records, sent = G.SINGLE_FILE[name]
            path = self.write("w-" + name, compacting_variant(records(), "w"),
                              states=G.IDLE_STATES if name == "idle_atom" else None, sent=sent)
            self.fresh()
            whole = self.parse(path)
            written = self.doc(path)
            out.append((name, "whole", whole))
            if written:
                self.fresh()
                out.append((name, "restored", self.parse(path)))
        path = self.write_as("long", _long_records("lg", 40, tasks_every=5, workless_every=9, mail_every=7), SID)
        tree, modes = self.restore_as(path, SID)
        self.assertEqual(modes, ["restore"], "the long session restores from its document")
        out.append(("long", "restored", tree))
        return out


def _pre_turns(tree):
    return [t for t in tree["turns"] if t.get("pre")]


class OnePassLift(unittest.TestCase):
    def test_the_one_pass_lift_equals_the_per_node_calls_over_seeded_stores(self):
        jd = _jd()
        moves = multi = 0
        for seed in range(400):
            rng = random.Random(seed)
            nodes = _wild_nodes(rng, rng.choice((6, 20, 60, 150)))
            before = {k: v.get("parentId") for k, v in nodes.items()}
            a, b = {"nodes": copy.deepcopy(nodes)}, {"nodes": copy.deepcopy(nodes)}
            ma = _ref_prepass(a)
            mb = jd._lift_completed_handoffs(b)
            self.assertEqual(b, a, "seed %d: the same parents, flags untouched" % seed)
            self.assertEqual(list(b["nodes"]), list(a["nodes"]), "seed %d: the node order kept" % seed)
            self.assertEqual(mb, ma, "seed %d: the same number of moves" % seed)
            moves += ma
            multi += ma > sum(1 for k, v in a["nodes"].items() if v.get("parentId") != before[k])
        self.assertGreater(moves, 1000, "the stores exercised the lift")
        self.assertGreater(multi, 20, "and chains where one node moved twice in one pre-pass")

    def test_a_later_tracker_takes_the_lifted_children_on_and_an_earlier_one_does_not(self):
        """The order rule the per-node calls had: X sits under the completed tracker T1, itself under the completed
        tracker T2, under the open top U. Node order T1 then T2: X moves to T2, and T2's own turn moves it on to U. Node
        order T2 then T1: T2's turn is over when X arrives, so X stays under T2 until the next rollup lifts it."""
        jd = _jd()
        shape = {"U": {"parentId": None, "nodeComplete": False},
                 "T1": {"parentId": "T2", "nodeComplete": True, "handoff": {"peer": PEER, "msgId": "m-api-1"}},
                 "T2": {"parentId": "U", "nodeComplete": True, "handoff": {"peer": PEER, "msgId": "m-api-2"}},
                 "X": {"parentId": "T1", "nodeComplete": False}}

        def run(order, fn):
            store = {"nodes": {k: dict(shape[k]) for k in order}}
            moved = fn(store)
            return store["nodes"]["X"]["parentId"], moved
        for fn in (jd._lift_completed_handoffs, _ref_prepass):
            self.assertEqual(run(["U", "T1", "T2", "X"], fn), ("U", 2))
            self.assertEqual(run(["U", "T2", "T1", "X"], fn), ("T2", 1))

    def test_a_rollups_passes_over_the_node_map_do_not_grow_with_the_number_of_delegations(self):
        jd = _jd()

        def delegations(tracked, total):
            nodes = _CountingNodes()
            store = {"rompUuid": RSID, "seq": 0, "nodes": nodes, "placements": {}, "status": {}}
            k, lifted = [0], {}

            def mk(parent, text, **kw):
                k[0] += 1
                nid = "%s:g%d" % (RSID, k[0])
                nodes[nid] = _node(jd, nid, text, parent, T0 + k[0], **kw)
                return nid
            for i in range(tracked):
                top = mk(None, "notes-api: ship the sync rework, part %d" % i)
                h = mk(top, "↪ delegated to api: part %d" % i, handoff={"peer": PEER, "msgId": "m-api-%d" % i})
                lifted[mk(h, "add the retry test for part %d" % i)] = top     # open: lifted beside the tracker
                d = mk(h, "send the spec for part %d" % i)
                jd.record_verdict(store, nodes[h], "courier", "done", T0 + 10_000 + i)
                jd.record_verdict(store, nodes[d], "closer", "done", T0 + 10_000 + i, why="sent")
            while k[0] < total:
                mk(None, "notes-api: tidy the web pane, item %d" % k[0])
            store["seq"] = k[0]
            store["lastNode"] = "%s:g%d" % (RSID, k[0])
            return store, lifted
        (small, s_lifted), (big, b_lifted) = delegations(10, 1200), delegations(160, 1200)
        for st, lifted in ((small, s_lifted), (big, b_lifted)):
            jd.rollup_status(st, False, now=NOW)                    # the first rollup lifts and settles
            self.assertEqual({c: st["nodes"][c]["parentId"] for c in lifted}, lifted, "every open child moved up")
            st["nodes"].passes = 0
            jd.rollup_status(st, False, now=NOW)                    # a steady rollup: what every later writer pays
        self.assertLessEqual(big["nodes"].passes, small["nodes"].passes + 2,
                             "160 delegations cost the passes 10 do (%d against %d)" % (big["nodes"].passes, small["nodes"].passes))
        self.assertLess(big["nodes"].passes, 60, "a fixed handful of passes: %d" % big["nodes"].passes)


class RollupOracle(unittest.TestCase):
    def test_the_rollup_writes_the_same_store_as_the_per_node_prepass_through_several_passes(self):
        """Two stores of 2,400 nodes, five passes each, writes between them; the same seed builds both copies."""
        jd = _jd()
        moves = []

        def ref(store):
            moves.append(_ref_prepass(store))
            return moves[-1]
        for seed in (1, 2):
            a, b = _forest_store(jd, seed, 2400), _forest_store(jd, seed, 2400)
            self.assertEqual(_canon(a), _canon(b), "the two copies start equal")
            ra, rb = random.Random(seed * 7), random.Random(seed * 7)
            for p in range(5):
                if p:
                    _mutate(jd, a, ra, p)
                    _mutate(jd, b, rb, p)
                closed = p % 2 == 1
                jd.rollup_status(a, closed, now=NOW + p)
                with mock.patch.object(jd, "_lift_completed_handoffs", ref):
                    jd.rollup_status(b, closed, now=NOW + p)
                self.assertEqual(_canon(a), _canon(b), "seed %d, pass %d: the same store" % (seed, p))
        self.assertGreater(sum(moves), 200, "the passes lifted children: %r" % moves)
        self.assertGreater(sum(1 for m in moves[1:] if m), 3, "and later passes lifted the republished ones again")


class PlannerWalks(_Trees):
    def test_plan_atoms_and_the_declared_plan_equal_the_whole_walk(self):
        task_turns = compared = 0
        for name, shape, tree in self.fixture_trees():
            with self.subTest(scenario=name, shape=shape):
                ref = _ref_plan_atoms(tree)
                got = em.plan_atoms(tree)
                self.assertEqual([a.get("uuid") for a in got], [a.get("uuid") for a in ref])
                self.assertTrue(all(x is y for x, y in zip(got, ref)), "the same atom objects")
                with mock.patch.object(em, "plan_atoms", _ref_plan_atoms):
                    want = em.declared_plan(tree)
                self.assertEqual(em.declared_plan(tree), want)
                task_turns += sum(1 for t in _pre_turns(tree) if any(n in TASK_NAMES for _i, n in t.get("tools") or ()))
                compared += len(ref)
        self.assertGreater(task_turns, 3, "restored turns with task calls were compared")
        self.assertGreater(compared, 10)

    def test_the_uuid_set_and_the_anchor_index_equal_the_whole_walk(self):
        jd = _jd()
        pre = 0
        for name, shape, tree in self.fixture_trees():
            with self.subTest(scenario=name, shape=shape):
                self.assertEqual(jd._parse_atom_uuids(tree), _ref_atom_uuids(tree))
                ref = _ref_by_uuid(tree)
                got = jd._parse_atoms_by_uuid(tree, set(ref) | {"u-not-in-this-parse"})
                self.assertEqual(sorted(got), sorted(ref))
                self.assertTrue(all(got[u] is ref[u] for u in ref), "the same atom objects")
                some = set(sorted(ref)[::3])
                got = jd._parse_atoms_by_uuid(tree, some)
                self.assertEqual(sorted(got), sorted(some))
                self.assertTrue(all(got[u] is ref[u] for u in some))
                pre += len(_pre_turns(tree))
        self.assertGreater(pre, 40, "restored turns were compared")

    def test_a_repeated_uuid_resolves_to_its_last_atom_across_restored_and_plain_turns(self):
        jd = _jd()
        a1, a2, a3, b1 = ({"uuid": "u1", "n": 1}, {"uuid": "u1", "n": 2}, {"uuid": "u2", "n": 3}, {"uuid": "u1", "n": 4})
        a4, c1 = {"uuid": "u2", "n": 5}, {"uuid": "u1", "n": 6}
        session = {"turns": [{"pre": True, "uuids": ["u1", "u1", None], "atoms": [a1, a2, {"uuid": None}]},
                             {"atoms": [a3, b1]},
                             {"pre": True, "uuids": ["u2", "u1"], "atoms": [a4, c1]}]}
        got = jd._parse_atoms_by_uuid(session, {"u1", "u2"})
        self.assertEqual(got, _ref_by_uuid(session))
        self.assertIs(got["u1"], c1)
        self.assertIs(got["u2"], a4)
        self.assertIs(jd._parse_atoms_by_uuid({"turns": session["turns"][:1]}, {"u1"})["u1"], a2, "the later of two in one turn")
        self.assertEqual(jd._parse_atom_uuids(session), {"u1", "u2"})
        odd = {"turns": [{"pre": True, "uuids": ["u9"], "atoms": [{"uuid": "u1"}, {"uuid": "u2"}]}]}   # lengths disagree:
        self.assertEqual(jd._parse_atom_uuids(odd), {"u1", "u2"}, "the atoms are read")                 # never trusted
        self.assertEqual(jd._parse_atoms_by_uuid(odd, {"u2"}), {"u2": {"uuid": "u2"}})

    def test_the_prompt_mail_latch_stamps_what_the_whole_walk_stamped(self):
        jd = _jd()
        stamped = 0
        for name, shape, tree in self.fixture_trees():
            with self.subTest(scenario=name, shape=shape):
                uuids = sorted(_ref_atom_uuids(tree))
                if not uuids:
                    continue
                nodes = {}
                for i, u in enumerate(uuids[::2] + ["u-gone-1", "u-gone-2"]):
                    nid = "%s:g%d" % (RSID, i + 1)
                    nodes[nid] = {"id": nid, "parentId": None, "promptUuid": u, "text": "notes-api ask %d" % i}
                nodes["%s:g900" % RSID] = {"id": "%s:g900" % RSID, "parentId": "%s:g1" % RSID, "promptUuid": uuids[0]}
                nodes["%s:g901" % RSID] = {"id": "%s:g901" % RSID, "parentId": None, "promptUuid": uuids[-1], "promptMsgId": "m-x"}
                a = {"rompUuid": SID, "nodes": copy.deepcopy(nodes)}
                b = {"rompUuid": SID, "nodes": copy.deepcopy(nodes)}
                self.assertEqual(jd._latch_prompt_msg_ids(tree, a), _ref_latch_prompt_msg_ids(tree, b))
                self.assertEqual(a, b)
                stamped += sum(1 for nd in a["nodes"].values() if "promptMsgId" in nd)
        self.assertGreater(stamped, 100)

    def test_plan_units_and_the_stored_work_verdict_equal_the_atom_scan(self):
        jd = _jd()
        stored = workless = 0
        for name, shape, tree in self.fixture_trees():
            with self.subTest(scenario=name, shape=shape):
                for seg in (sg for t in tree["turns"] for sg in em.segments(t)):
                    self.assertEqual(jd._seg_work(seg), jd._has_asst_work(seg["atoms"]), seg["id"])
                    stored += "w" in seg
                    workless += "w" in seg and not seg["w"]
                store = {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {}, "placementsV": jd.PLACEMENTS_V}
                with mock.patch.object(jd, "_seg_work", _ref_seg_work):
                    ref = jd.plan_units(tree, store, lazy_text=True)
                self.assertEqual(jd.plan_units(tree, store, lazy_text=True), ref)
                placed = dict(store, placements={jd._unit_key(u[0], u[1]): None for u in ref})
                with mock.patch.object(jd, "_seg_work", _ref_seg_work):
                    ref = jd.plan_units(tree, placed, lazy_text=True)
                self.assertEqual(jd.plan_units(tree, placed, lazy_text=True), ref)
        self.assertGreater(stored, 40, "restored segments with a stored verdict")
        self.assertGreater(workless, 0, "a restored workless segment among them")


class PlannerWalkCounts(_Trees):
    def _plain(self):
        path = self.write_as("plain", _long_records("pl", 40), SID)
        tree, modes = self.restore_as(path, SID)
        self.assertEqual(modes, ["restore"])
        n = sum(len(t["atoms"]) for t in _pre_turns(tree))
        self.assertGreater(n, 300)
        return tree

    def test_the_task_search_builds_no_pre_cut_atom_of_a_session_without_task_calls(self):
        tree = self._plain()
        with _Calls(em.LazyAtoms._at.__code__) as c:
            self.assertEqual(em.plan_atoms(tree), [])
        self.assertEqual(c.n, 0, "no pre-cut atom built or touched")

    def test_the_uuid_set_and_the_anchor_index_build_only_what_they_return(self):
        jd = _jd()
        tree = self._plain()
        pre_prompt = _pre_turns(tree)[7]["uuids"][0]
        with _Calls(em.LazyAtoms._at.__code__) as c:
            uuids = jd._parse_atom_uuids(tree)
        self.assertEqual(c.n, 0)
        self.assertIn(pre_prompt, uuids)
        with _Calls(em.LazyAtoms._at.__code__) as c:
            got = jd._parse_atoms_by_uuid(tree, {pre_prompt, "u-gone"})
        self.assertEqual((sorted(got), c.n), ([pre_prompt], 1), "the one atom asked for")
        with _Calls(em.LazyAtoms._at.__code__) as c:
            self.assertEqual(jd._parse_atoms_by_uuid(tree, {"u-gone"}), {})
        self.assertEqual(c.n, 0)
        store = {"rompUuid": SID, "nodes": {"%s:g1" % RSID: {"id": "%s:g1" % RSID, "parentId": None, "promptUuid": pre_prompt},
                                            "%s:g2" % RSID: {"id": "%s:g2" % RSID, "parentId": None, "promptUuid": "u-gone"}}}
        with _Calls(em.LazyAtoms._at.__code__) as c:
            self.assertEqual(jd._latch_prompt_msg_ids(tree, store), 1)
        self.assertEqual(c.n, 1, "the latch builds the one anchor it stamps")
        self.assertEqual(store["nodes"]["%s:g1" % RSID].get("promptMsgId"), "")
        self.assertNotIn("promptMsgId", store["nodes"]["%s:g2" % RSID], "an anchor the parse lacks waits")

    def test_note_checked_reads_the_uuid_set_only_for_a_candidate(self):
        jd = _jd()
        saved = set(jd._CHECKED)
        self.addCleanup(lambda: (jd._CHECKED.clear(), jd._CHECKED.update(saved)))
        jd._CHECKED.clear()

        def boom():
            raise AssertionError("the uuid set was read with no candidate")
        self.assertEqual(jd._note_checked({"nodes": {"g1": {"parentId": "g0", "promptUuid": "u1"}}}, boom), 0)
        reads = []
        store = {"nodes": {"g1": {"parentId": None, "promptUuid": "u1", "askAnchor": "human"},
                           "g2": {"parentId": None, "promptUuid": "u2", "askAnchor": "human"}}}
        self.assertEqual(jd._note_checked(store, lambda: (reads.append(1), {"u1", "a1"})[1]), 1)
        self.assertEqual((reads, jd._CHECKED), ([1], {"u1"}), "read once, for the pass")

    def test_plan_units_reads_no_restored_segments_atoms_for_its_work_check(self):
        jd = _jd()
        tree = self._plain()
        store = {"rompUuid": SID, "seq": 0, "nodes": {}, "placements": {}, "status": {}, "placementsV": jd.PLACEMENTS_V}
        store["placements"] = {jd._unit_key(u[0], u[1]): None for u in jd.plan_units(tree, store, lazy_text=True)}
        pre_segs = sum(len(t["segs"]) for t in _pre_turns(tree))
        self.assertGreater(pre_segs, 30)
        with _Calls(jd._has_asst_work.__code__, test=lambda f: isinstance(f.f_locals.get("atoms"), em.LazyAtoms)) as c:
            jd.plan_units(tree, store, lazy_text=True)
        self.assertEqual(c.hits, 0, "no restored segment's atoms read for the work check (%d calls in all)" % c.n)


class WholePlannerPass(_Trees):
    """The real _plan_session, three passes over a restored long session (no task store, so the declared-plan fold runs)
    and a store with delegations and prompt anchors, some the parse does not hold; another writer moves the store
    between passes. Run once with the old walks patched in and once as shipped."""

    def _seed(self, jd, store, tree):
        forest = _forest_store(jd, 5, 500, sid=PSID)
        store["nodes"].update(forest["nodes"])
        n = len(store["nodes"])
        prompts = [t["trigger"]["uuid"] for t in tree["turns"] if t.get("trigger")]
        for i, u in enumerate(prompts + ["u-gone-%d" % j for j in range(12)]):
            n += 1
            nid = "%s:g%d" % (PSID, n)
            gone = u.startswith("u-gone")
            store["nodes"][nid] = _node(jd, nid, "notes-api: the ask from prompt %d" % i, None, T0 + 30_000 + i, promptUuid=u,
                                        askAnchor="absent" if gone else "human", **({} if gone else {"promptMsgId": ""}))
            if i % 3 == 0 and not gone:
                jd.record_verdict(store, store["nodes"][nid], "closer", "done", T0 + 40_000 + i, why="shipped")
        store["seq"] = n
        store["lastNode"] = "%s:g%d" % (PSID, n - 13)
        store["placementsV"] = jd.PLACEMENTS_V
        store["placements"] = {jd._unit_key(u[0], u[1]): None for u in jd.plan_units(tree, store, lazy_text=True)}

    def _between(self, jd, p):
        st = jd.load_goals(PSID)
        ids = sorted(st["nodes"])
        trackers = [i for i in ids if isinstance(st["nodes"][i].get("handoff"), dict)]
        open_t = [i for i in trackers if not st["nodes"][i].get("nodeComplete") and not st["nodes"][i].get("cleared")]
        done_t = [i for i in trackers if st["nodes"][i].get("nodeComplete") and not st["nodes"][i].get("rolledUp")]
        if open_t:
            jd.record_verdict(st, st["nodes"][open_t[p % len(open_t)]], "courier", "done", T0 + 60_000 + p)
        loose = [i for i in ids if i not in trackers and st["nodes"][i].get("parentId") is not None
                 and not st["nodes"][i].get("nodeComplete") and not st["nodes"][i].get("cleared")]
        if loose and done_t:
            i, t = loose[(5 * p) % len(loose)], done_t[(3 * p) % len(done_t)]
            if t != i and i not in _ancestors(st["nodes"], t):
                st["nodes"][i]["parentId"] = t                  # the save-rebase race: a stale parent republished
        jd.save_goals(PSID, st)

    def _run(self, jd, tree, path, old):
        root = Path(tempfile.mkdtemp())
        (root / "state").mkdir()
        saved_state, saved_owner, saved_checked = jd.STATE, jd._SDK_OWNER_FN, set(jd._CHECKED)
        asked = []
        patches = [mock.patch.object(jd, "parsed_session", lambda *a, **kw: tree),
                   mock.patch.object(jd, "opener_llm", lambda *a, **kw: (asked.append(a), "")[1]),
                   mock.patch.object(em, "task_store_dir", lambda fsid: root / "no-task-store" / str(fsid)),
                   mock.patch("time.time", return_value=float(NOW + 3600))]
        if old:
            patches += [mock.patch.object(jd, "_lift_completed_handoffs", _ref_prepass),
                        mock.patch.object(jd, "_parse_atom_uuids", _ref_atom_uuids),
                        mock.patch.object(jd, "_parse_atoms_by_uuid", lambda session, want: _ref_by_uuid(session)),
                        mock.patch.object(em, "plan_atoms", _ref_plan_atoms),
                        mock.patch.object(jd, "_seg_work", _ref_seg_work)]
        stores, touched = [], None
        jd._rebind_state(root / "state")
        jd._SDK_OWNER_FN = None
        try:
            with contextlib.ExitStack() as stack:
                for p_ in patches:
                    stack.enter_context(p_)
                jd._CHECKED.clear()
                store = jd.load_goals(PSID)
                self._seed(jd, store, tree)
                jd.save_goals(PSID, store)
                for p in range(3):
                    if p == 2:
                        with _Calls(em.LazyAtoms._at.__code__) as c:
                            jd._plan_session(PSID, path, NOW)
                        touched = c.n
                    else:
                        jd._plan_session(PSID, path, NOW)
                    stores.append(_canon(jd.load_goals(PSID)))
                    self._between(jd, p)
        finally:
            jd._rebind_state(saved_state)
            jd._SDK_OWNER_FN = saved_owner
            jd._CHECKED.clear()
            jd._CHECKED.update(saved_checked)
        self.assertEqual(asked, [], "a steady pass asks the model nothing")
        return stores, touched

    def test_steady_passes_write_the_same_store_and_touch_few_pre_cut_atoms(self):
        jd = _jd()
        path = self.write_as("whole", _long_records("wp", 90, workless_every=13, mail_every=7), PSID)
        tree, modes = self.restore_as(path, PSID)
        self.assertEqual(modes, ["restore"])
        pre_atoms = sum(len(t["atoms"]) for t in _pre_turns(tree))
        self.assertGreater(pre_atoms, 700)
        old_stores, old_touched = self._run(jd, tree, path, old=True)
        new_stores, new_touched = self._run(jd, tree, path, old=False)
        for p, (o, n) in enumerate(zip(old_stores, new_stores)):
            self.assertEqual(n, o, "pass %d: the store the old walks wrote" % p)
        self.assertGreater(old_touched, 2 * pre_atoms, "the old walks touched each pre-cut atom several times (%d)" % old_touched)
        self.assertLess(new_touched, pre_atoms / 3, "about one touch per restored segment: %d for %d pre-cut atoms" % (new_touched, pre_atoms))


if __name__ == "__main__":
    unittest.main()
