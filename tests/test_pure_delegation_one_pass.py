#!/usr/bin/env python3
"""The pure-delegation test makes one pass over a session's goal map per derivation, not one per card (2026-10-22).

The feed asks _pure_delegation_top about every top of a session it derives, and the session stamp read asks about
every open top whose outstanding work is all handoffs. Each call built its own children index, a pass over every
node in the store, and each unlatched top with a prompt anchor read the cached parse again and walked every atom in
it. A session with many cards paid its store's size times its number of tops on every derivation. Now the caller
hands every call of one derivation the same memo dict, which holds the index and the parse's records by uuid, and
every answer is the one a call without the memo gives.

Pinned four ways:
  * an oracle over a seeded matrix of node shapes and parse temperatures: the function with a shared memo and
    without one answers exactly what the body before this change (copied below as _ref_pure) answers, the first
    record under a duplicated uuid included;
  * the memo's guards: an index built for one nodes map is never used for another, and the prompt records are kept
    per transcript path;
  * the real build_feed and the real stamp read: the frame and the stamp view equal the ones the pre-change body
    gives, with the parse warm and cold, and the expected tops shown and suppressed;
  * the counts: in one feed derivation and in one stamp read, _pure_delegation_top makes at most ONE pass over the
    goal map and at most ONE cached-parse read, however many tops it is asked about.
SYNTHETIC fixtures only: private invented sids, hostname TESTHOST, the notes-api demo's web session."""
import collections
import contextlib
import io
import json
import os
import random
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_pd_one_pass", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = 1781300000
T0 = NOW - 7200
SID = "c4d5e6f7-1111-4222-8333-0000000000b1"     # private synthetic sids (never the shared placeholder)
PEER = "c4d5e6f7-1111-4222-8333-0000000000b2"
WK1 = "c4d5e6f7-1111-4222-8333-0000000000b3"
WK2 = "c4d5e6f7-1111-4222-8333-0000000000b4"
PATH = "/nonexistent/notes-api/web.jsonl"        # the unit tests' transcript path: _parse_cached is stubbed there


# ── the body before this change, verbatim but for the names: the oracle every answer is held to ──────────────────
def _ref_dictated(sid, path, pu):
    if not (pu and path):
        return None
    ps = km._parse_cached(str(path))
    if not ps:
        return None
    for turn in ps.get("turns") or []:
        for a in turn.get("atoms") or []:
            if a.get("uuid") == pu:
                return jd._human_prompt_record(a, str(sid or "")) is not None
    return None


def _ref_pure(nodes, top_id, sid=None, path=None, memo=None):   # memo accepted and ignored: a stand-in at the call sites
    root = nodes.get(top_id) or {}
    if not isinstance(root.get("handoff"), dict):
        if isinstance(root.get("userAsk"), dict):
            return False
        pu = root.get("promptUuid")
        if pu and not isinstance(root.get("origin"), dict):
            latch = root.get("askAnchor")
            if latch in ("human", "absent", "scheduled"):
                return False
            if latch != "machine" and _ref_dictated(sid, path, pu) is not False:
                return False
    children = {}
    for nid, nd in nodes.items():
        children.setdefault(nd.get("parentId"), []).append(nid)
    leaves, stack, seen = [], [top_id], set()
    while stack:
        x = stack.pop()
        if x in seen or x not in nodes:
            continue
        seen.add(x)
        kids = children.get(x, [])
        if kids:
            stack.extend(kids)
        else:
            leaves.append(x)
    return bool(leaves) and all(isinstance(nodes.get(nid, {}).get("handoff"), dict) for nid in leaves)


# ── unit fixtures: parse atoms and a seeded node matrix ─────────────────────────────────────────────────────────
def _human(u, text="please add paging to the notes-api list endpoint"):
    return {"uuid": u, "type": "user", "author": "human", "message": {"role": "user", "content": text}}


def _machine(u):
    return {"uuid": u, "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "wrapping up the notes-api sweep"}]}}


def _mail(u):
    return {"uuid": u, "type": "user", "author": {"peer": PEER, "kind": "delegate"},
            "message": {"role": "user", "content": "DELEGATE: the notes-api client half"}}


WARM = {"turns": [
    {"atoms": [_human("h0"), _machine("m0"), _mail("p0")]},
    {"atoms": [_human("h1"), _machine("m1"), {"type": "assistant", "message": {"role": "assistant", "content": []}}]},
    {},                                                       # a turn with no atoms key
    {"atoms": [_human("dupHM"), _machine("dupMH")]},          # each uuid's FIRST record decides it
    {"atoms": [_machine("dupHM"), _human("dupMH"), _human("h2"), _mail("p1")]},
]}
PARSES = {"cold": None, "empty": {"turns": []}, "warm": WARM}
ANCHORS = (None, "", "h0", "h1", "h2", "m0", "m1", "p0", "p1", "dupHM", "dupMH", "gone-1")
LATCHES = (None, None, None, "human", "absent", "scheduled", "machine", "machine", "other")


def _matrix(seed=7, tops=90):
    """A seeded store: tops of every exemption shape over subtrees of handoff and own-work leaves, a deterministic
    grid of every anchor under every latch, and the odd shapes a store can hold (a node under a missing parent, a
    self-parented node, a two-node cycle)."""
    r = random.Random(seed)
    nodes = {}

    def node(nid, parent, **kw):
        d = {"id": nid, "parentId": parent, "text": "notes-api step %s" % nid}
        d.update(kw)
        nodes[nid] = d

    def subtree(parent, depth):
        for k in range(r.choice((0, 1, 2, 3)) if depth else 0):
            kid = "%s.%d" % (parent, k)
            node(kid, parent, **({"handoff": {"peer": WK1, "msgId": "1781290000.%06d_1.TESTHOST" % len(nodes)}}
                                 if r.random() < 0.7 else {}))
            subtree(kid, depth - 1)

    for i in range(tops):
        kw = {}
        pu = r.choice(ANCHORS)
        if pu is not None:
            kw["promptUuid"] = pu
        latch = r.choice(LATCHES)
        if latch is not None:
            kw["askAnchor"] = latch
        if r.random() < 0.2:
            kw["origin"] = {"peer": PEER, "goalId": PEER + ":g9", "msgId": "1781290000.000001_1.TESTHOST"}
        if r.random() < 0.1:
            kw["userAsk"] = {"text": "add paging", "sid": PEER}
        if r.random() < 0.1:
            kw["handoff"] = {"peer": WK2, "msgId": "1781290000.%06d_2.TESTHOST" % i}
        node("t%d" % i, None, **kw)
        subtree("t%d" % i, r.choice((0, 1, 2, 3)))
    # …and the whole grid once, deterministically: every anchor under every latch, with and without a courier origin,
    # over one handoff leaf, so every branch of the exemption is reached whatever the seed draws
    for i, (pu, latch, origin) in enumerate((pu, latch, origin) for pu in ANCHORS for latch in (None, "human", "absent", "scheduled", "machine", "other")
                                            for origin in (False, True)):
        kw = {k: v for k, v in (("promptUuid", pu), ("askAnchor", latch)) if v is not None}
        if origin:
            kw["origin"] = {"peer": PEER, "goalId": PEER + ":g9", "msgId": "1781290000.000002_1.TESTHOST"}
        node("grid%d" % i, None, **kw)
        node("grid%d.h" % i, "grid%d" % i, handoff={"peer": WK1, "msgId": "1781290000.%06d_3.TESTHOST" % i})
    node("orphan", "missing-parent", handoff={"peer": WK1, "msgId": "x"})
    node("self", "self")
    node("cyc-a", "cyc-b")
    node("cyc-b", "cyc-a", handoff={"peer": WK1, "msgId": "y"})
    return nodes


class _Stub:
    """km._parse_cached stubbed to a per-path parse, counting reads."""

    def __init__(self, by_path):
        self.by_path, self.reads = by_path, 0

    def __call__(self, p):
        self.reads += 1
        return self.by_path.get(str(p))


class MatchesTheBodyBeforeTheChange(unittest.TestCase):

    def test_every_node_every_parse_with_and_without_a_shared_memo(self):
        nodes = _matrix()
        ids = list(nodes) + ["no-such-node"]
        answers = {}
        for label, ps in PARSES.items():
            for path in (PATH, None):
                with mock.patch.object(km, "_parse_cached", _Stub({PATH: ps})):
                    memo = {}
                    for nid in ids:
                        want = _ref_pure(nodes, nid, sid=SID, path=path)
                        self.assertIs(km._pure_delegation_top(nodes, nid, sid=SID, path=path, memo=memo), want,
                                      "%s / path %s / %s, shared memo" % (label, path, nid))
                        self.assertIs(km._pure_delegation_top(nodes, nid, sid=SID, path=path), want,
                                      "%s / path %s / %s, no memo" % (label, path, nid))
                        answers[(label, path, nid)] = want
        # the matrix is not vacuous: both answers occur, and the parse decides some tops
        self.assertGreater(sum(answers.values()), 10)
        self.assertGreater(len(answers) - sum(answers.values()), 10)
        moved = [nid for nid in ids if answers[("warm", PATH, nid)] != answers[("cold", PATH, nid)]]
        self.assertTrue(moved, "no top's answer depends on the parse: the anchor read is never exercised")

    def test_the_first_record_under_a_uuid_decides_it(self):
        def fanned(pu):
            return {"t": {"id": "t", "parentId": None, "promptUuid": pu},
                    "a": {"id": "a", "parentId": "t", "handoff": {"peer": WK1, "msgId": "1"}}}
        with mock.patch.object(km, "_parse_cached", _Stub({PATH: WARM})):
            memo = {}
            self.assertFalse(km._pure_delegation_top(fanned("dupHM"), "t", sid=SID, path=PATH, memo=memo),
                             "dupHM's first record is the human prompt: the ask keeps its card")
            self.assertTrue(km._pure_delegation_top(fanned("dupMH"), "t", sid=SID, path=PATH, memo=memo),
                            "dupMH's first record is the agent's own: coordination, suppressed")


class TheMemosGuards(unittest.TestCase):

    def test_an_index_is_used_only_for_the_map_it_was_built_from(self):
        h = {"peer": WK1, "msgId": "1781290000.000001_1.TESTHOST"}
        first = {"t": {"id": "t", "parentId": None}, "a": {"id": "a", "parentId": "t", "handoff": h}}
        second = dict(first, w={"id": "w", "parentId": "t"})       # the same top, now with an own-work leaf
        memo = {}
        self.assertTrue(km._pure_delegation_top(first, "t", memo=memo))
        self.assertFalse(km._pure_delegation_top(second, "t", memo=memo),
                         "the second map's own-work leaf is not in the first map's index")
        self.assertTrue(km._pure_delegation_top(first, "t", memo=memo))

    def test_prompt_records_are_kept_per_transcript_path(self):
        other = "/nonexistent/notes-api/api.jsonl"
        nodes = {"t": {"id": "t", "parentId": None, "promptUuid": "u1"},
                 "a": {"id": "a", "parentId": "t", "handoff": {"peer": WK1, "msgId": "1"}}}
        stub = _Stub({PATH: {"turns": [{"atoms": [_human("u1")]}]},
                      other: {"turns": [{"atoms": [_machine("u1")]}]}})
        with mock.patch.object(km, "_parse_cached", stub):
            memo = {}
            self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path=PATH, memo=memo))
            self.assertTrue(km._pure_delegation_top(nodes, "t", sid=SID, path=other, memo=memo),
                            "the second path's record is the agent's own, not the first path's prompt")
            self.assertFalse(km._pure_delegation_top(nodes, "t", sid=SID, path=PATH, memo=memo))
        self.assertEqual(stub.reads, 2, "one read per path, then lookups")


# ── the real feed: one session with many tops of every shape ────────────────────────────────────────────────────
def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


TURNS = 10      # hu<i> the user's typed prompts, as<i> the agent's replies


def _node(nid, text, parent=None, t=T0, **kw):
    d = {"id": nid, "text": text, "parentId": parent, "nodeComplete": False, "blocked": False, "cleared": False,
         "trail": [], "t": t, "mt": t, "log": []}
    d.update(kw)
    return d


def _board():
    """(nodes, status, groups): the session's store, every top named by the group whose rule decides it. The
    machine-latched tops are minted before every prompt-anchored top, so the read-side nesting (T319, which homes a
    machine-rooted top under the host current at its mint) leaves them roots, as the loop under test sees them."""
    nodes, status, groups = {}, {}, collections.defaultdict(list)
    seq = [0]

    def top(group, t, n_handoffs=2, own=0, done=False, **kw):
        seq[0] += 1
        nid = "%s:g%d" % (SID, seq[0])
        nodes[nid] = _node(nid, "notes-api %s %d" % (group, seq[0]), t=t, nodeComplete=done, **kw)
        for k in range(n_handoffs):
            seq[0] += 1
            hid = "%s:g%d" % (SID, seq[0])
            nodes[hid] = _node(hid, "delegated to api: part %d" % k, parent=nid, t=t + 1 + k, nodeComplete=done,
                               handoff={"peer": WK1 if k % 2 == 0 else WK2,
                                        "msgId": "1781290000.%06d_1.TESTHOST" % seq[0], "quiet": True})
        for k in range(own):
            seq[0] += 1
            oid = "%s:g%d" % (SID, seq[0])
            nodes[oid] = _node(oid, "own step %d" % k, parent=nid, t=t + 5 + k, nodeComplete=done)
        if done:
            status[nid] = "completed"
        groups[group].append(nid)

    t = T0 - 3600                                   # before every host
    for i in range(6):
        top("machine-latched", t + 10 * i, promptUuid="as%d" % i, askAnchor="machine")
    for i in range(2):
        top("completed", t + 100 + 10 * i, done=True, promptUuid="as%d" % (6 + i), askAnchor="machine")
    for i in range(2):
        top("tracker-top", t + 200 + 10 * i, n_handoffs=0,
            handoff={"peer": WK1, "msgId": "1781290001.%06d_1.TESTHOST" % i, "quiet": True})
    for i in range(2):
        top("courier", t + 300 + 10 * i, promptUuid="mail-anchor-%d" % i,
            origin={"peer": PEER, "goalId": PEER + ":g9", "msgId": "1781290002.%06d_1.TESTHOST" % i})
    t = T0 + 600
    for i in range(4):
        top("unlatched-machine", t + 10 * i, promptUuid="as%d" % i)
    for i in range(3):
        top("unlatched-human", t + 100 + 10 * i, promptUuid="hu%d" % i)
    for i in range(2):
        top("unlatched-absent", t + 200 + 10 * i, promptUuid="gone-%d" % i)
    for i in range(4):
        top("own-work", t + 300 + 10 * i, n_handoffs=1, own=1)
    top("courier-userask", t + 400, promptUuid="mail-anchor-9", userAsk={"text": "add paging", "sid": PEER},
        origin={"peer": PEER, "goalId": PEER + ":g8", "msgId": "1781290003.000001_1.TESTHOST"})
    return nodes, status, groups


SUPPRESSED_ALWAYS = ("machine-latched", "completed", "tracker-top", "courier")
SHOWN_ALWAYS = ("unlatched-human", "unlatched-absent", "own-work", "courier-userask")


def _dump(feed):
    return json.dumps({k: v for k, v in feed.items() if k not in km._DEDUP_VOLATILE}, sort_keys=True, default=str)


class _FeedWorld(unittest.TestCase):

    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.tpath = pdir / (SID + ".jsonl")
        recs, prev = [], None
        for i in range(TURNS):
            recs.append(uline(T0 + 60 * i, "notes-api request %d: add paging" % i, "hu%d" % i, prev))
            recs.append(aline(T0 + 60 * i + 30, "Done with request %d." % i, "as%d" % i, "hu%d" % i))
            prev = "as%d" % i
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        names = td / "names"; names.mkdir()
        (names / SID).write_text("web\t%s\t#abcdef\n" % str(cdir))
        self.saved = (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map)
        jd.NAMES, jd.PROJECTS = names, proj
        jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR = td / "captions", td / "archive", td / "goals"
        jd.STATE = td
        (td / "session-hosts").write_text("off\n")   # a state root of its own: no per-session host
        km.NAMES = names
        km._live_map = lambda: {SID: {"state": "idle", "since": NOW - 10, "model": "", "effort": "",
                                      "context": None, "compactPct": None, "color": None}}
        jd.GOALDIR.mkdir(parents=True)
        self.nodes, status, self.groups = _board()
        (jd.GOALDIR / (SID + ".json")).write_text(json.dumps(
            {"rompUuid": SID, "seq": len(self.nodes), "lastNode": None, "nodes": self.nodes,
             "placements": {}, "status": status}))
        self._reset()

    def tearDown(self):
        (jd.NAMES, jd.PROJECTS, jd.CAPDIR, jd.ARCHDIR, jd.GOALDIR, jd.STATE, km.NAMES, km._live_map) = self.saved
        self._reset()
        self.td.cleanup()

    @staticmethod
    def _reset():
        """Every per-process memo a derivation reads empty, so each build derives the session from scratch."""
        km._bgall_cache.clear()
        km._feed_memo.clear()
        km._SESSION_STAMP_CACHE.clear()
        held = getattr(km, "_FEED_SEG_HELD", None)
        if held is not None:
            with km._FEED_SEG_LOCK:
                held.clear()

    def _build(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            return km.build_feed(NOW)

    def _shown(self, feed):
        return {a.get("itemId") for a in feed["asks"] if a.get("sid") == SID and a.get("itemId") in self.nodes}

    def _group_ids(self, names):
        return {nid for g in names for nid in self.groups[g]}


class FeedFrameMatchesThePerCallBody(_FeedWorld):

    def _pair(self):
        self._reset()
        shipped = self._build()
        self._reset()
        with mock.patch.object(km, "_pure_delegation_top", _ref_pure):
            before = self._build()
        return shipped, before

    def test_warm_parse(self):
        km._parse(str(self.tpath), SID, NOW)            # the anchors resolve against the cached parse
        self._build()                                    # the session order adopted before the compared builds
        shipped, before = self._pair()
        self.assertEqual(_dump(shipped), _dump(before))
        shown = self._shown(shipped)
        self.assertTrue(self._group_ids(SHOWN_ALWAYS) <= shown, sorted(self._group_ids(SHOWN_ALWAYS) - shown))
        self.assertFalse(self._group_ids(SUPPRESSED_ALWAYS + ("unlatched-machine",)) & shown,
                         "a warm parse proves the unlatched machine anchors: those tops are coordination too")

    def test_cold_parse(self):
        self._build()
        shipped, before = self._pair()
        self.assertEqual(_dump(shipped), _dump(before))
        shown = self._shown(shipped)
        self.assertTrue(self._group_ids(SHOWN_ALWAYS + ("unlatched-machine",)) <= shown,
                        "a cold parse cannot resolve an unlatched anchor: those tops fail open to their cards")
        self.assertFalse(self._group_ids(SUPPRESSED_ALWAYS) & shown)

    def test_the_stamp_read_matches_the_per_call_body(self):
        km._parse(str(self.tpath), SID, NOW)
        self._reset()
        shipped = km._session_stamp_read(SID)
        self._reset()
        with mock.patch.object(km, "_pure_delegation_top", _ref_pure):
            before = km._session_stamp_read(SID)
        self.assertEqual(shipped, before)
        self.assertTrue(shipped[2], "the own-delegated tops name their peers (the view is not empty)")


class OnePassPerDerivation(_FeedWorld):
    """Counted with the profiler hook, so nothing in the code under test is replaced: a pass is a call of
    nodes.items() made by _pure_delegation_top, a parse read a call of _parse_cached made by _dictated_prompt_uuid,
    each filed under the function that asked _pure_delegation_top."""

    def _counted(self, fn):
        calls, passes, reads = collections.Counter(), collections.Counter(), collections.Counter()

        def hook(frame, event, arg):
            code = frame.f_code.co_name
            if event == "c_call" and code == "_pure_delegation_top" and getattr(arg, "__name__", "") == "items":
                passes[frame.f_back.f_code.co_name] += 1
            elif event == "call" and code == "_pure_delegation_top":
                calls[frame.f_back.f_code.co_name] += 1
            elif event == "call" and code == "_parse_cached" and frame.f_back is not None \
                    and frame.f_back.f_code.co_name == "_dictated_prompt_uuid":
                asker = frame.f_back.f_back                       # _pure_delegation_top
                reads[asker.f_back.f_code.co_name if asker is not None and asker.f_back is not None else "?"] += 1
        sys.setprofile(hook)
        try:
            fn()
        finally:
            sys.setprofile(None)
        return calls, passes, reads

    def test_one_feed_derivation(self):
        km._parse(str(self.tpath), SID, NOW)
        self._build()
        self._reset()
        calls, passes, reads = self._counted(self._build)
        asked = calls["_feed_session_entry"]
        self.assertGreaterEqual(asked, 20, "the derivation asks about every top: %s" % dict(calls))
        self.assertEqual(passes["_feed_session_entry"], 1,
                         "%d tops asked, %d passes over the goal map" % (asked, passes["_feed_session_entry"]))
        self.assertEqual(reads["_feed_session_entry"], 1,
                         "%d tops asked, %d cached-parse reads" % (asked, reads["_feed_session_entry"]))

    def test_one_stamp_read(self):
        km._parse(str(self.tpath), SID, NOW)
        self._reset()
        calls, passes, reads = self._counted(lambda: km._session_stamp_read(SID))
        asked = calls["_session_stamp_read"]
        self.assertGreaterEqual(asked, 15, "the read asks about every open all-delegated top: %s" % dict(calls))
        self.assertEqual(passes["_session_stamp_read"], 1,
                         "%d tops asked, %d passes over the goal map" % (asked, passes["_session_stamp_read"]))
        self.assertEqual(reads["_session_stamp_read"], 1,
                         "%d tops asked, %d cached-parse reads" % (asked, reads["_session_stamp_read"]))


if __name__ == "__main__":
    unittest.main()
