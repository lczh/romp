#!/usr/bin/env python3
"""The card anchor walk holds its closed turns (2026-10-06).

THE PROBLEM. A card on the board can be clicked through to the place in the chat it is about: the work it did, the
prompt that asked for it, the prose that sums it up. The kernel finds those places by walking every segment of the
session's history and building maps from a segment's key to its anchors (_feed_session_entry), and the re-check that
moves a blocked card when the user replies needs the session's latest plain prompt, a second walk over every turn. Both
ran on every derivation of a session's cards, so a working session paid for its whole history on every append. Now the
closed turns' part is held between derivations as one aggregate, and a derivation folds only the turns that closed
since and the open one (_feed_seg_walk, _feed_seg_plain_t).

Pinned here against the one-pass loop it replaced, written out below as the oracle. After every step of a road (appends
to the open turn, a turn boundary, a republish with the same seams, a seam that splits an old segment and its removal,
a rewind cut and its release, a compaction, a document restore with the hold kept and with it cleared, a demote, a late
record in a closed turn, a resume into a new file, the leaf replaced under turn keys that stand, a /clear) the five maps
(seg_uuid, seg_trig, seg_best, cite_uuids, and seg_turn's lazy answers, the same turn object and the same atoms) and the
plain-prompt time equal the oracle's, and the walk folded only the turns the step moved. Also: a fold that raises keeps
its partial maps (the oracle's own try, met at the same call) and stores nothing; a plain-prompt read that raises fails
and stores nothing, with the anchor maps whole; one sid at two paths is never served the other path's anchors, the
test-isolation hazard (the turn keys name no file); a lazy lookup whose segmentation raises answers None and counts the
landing's fault; the byte bound sheds the least recently used session first and an aggregate over it alone is not held;
a departed session's aggregate leaves with it; the held aggregate holds strings, numbers and tuples only.
tests/test_feed_session_memo.py drives the same kind of road through build_feed and compares whole boards.

The review after the merge (2026-10-06) added: a cut inside the open turn (a rolled-back mid-turn prompt) is served the
closed turns from the hold and nothing of the removed segment, and after a cold walk and a reuse walk the held maps are
the one-pass walk over the closed turns alone (the two copies the walk makes); an entry the walk cannot use leaves the
table in that walk even when the walk then fails, counted once; a walk that did not finish counts no turns; GET /perf's
report moves by one walk, one folded turn and thirty held turns on a reuse; an aggregate over the bound by itself counts
under overBound, not segEvict; a reused entry is the newest, so a colder session is shed first; and past the bound a
build keeps the entries it uses and holds no newcomer, so two of three sessions walk only their open turn, and a
session whose grown aggregate the bound turns away keeps the entry its walk was served from.

Synthetic data only: placeholder uuids, the notes-api demo's web, api and tests, invented text, a private state root
with session hosts off."""
import collections
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
Path(os.environ["XDG_STATE_HOME"], "romp").mkdir(parents=True, exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off\n")   # this root is the module's own: no host spawns
km = load_source("romp_kernel_feed_seg_held", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID_WEB = "c4d00000-1006-4aaa-8bbb-00000000c001"   # private synthetic sids (CLAUDE.md, goal-store fixtures)
SID_API = "c4d00000-1006-4aaa-8bbb-00000000c002"
RESUME_FILE = "c4d00000-1006-4aaa-8bbb-00000000c003"   # the CLI session ids a resume and a /clear write their files under
CLEAR_FILE = "c4d00000-1006-4aaa-8bbb-00000000c004"
SID_TESTS = "c4d00000-1006-4aaa-8bbb-00000000c005"      # the third session the byte bound's tests need
REAL_SEGS_SEAM = km._segs_seam
PROSE = "the notes-api search now pages twenty results at a time and hands back a cursor for the next page"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text):
    """A prompt the person typed."""
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def reply(t, uuid, parent, text, stop="end_turn"):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"id": "msg_" + uuid, "role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": stop}}


def tool_call(t, uuid, parent, tid):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"id": "msg_" + uuid, "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "make test"}}]}}


def tool_out(t, uuid, parent, tid, content):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}


def compaction(t, tag, logical_parent, trigger):
    """A compaction as the CLI attaches it: the boundary (no parent, its logical parent the last record) and its summary."""
    b, s = "w-b%s" % tag, "w-s%s" % tag
    return [{"type": "system", "subtype": "compact_boundary", "timestamp": iso(t), "uuid": b, "parentUuid": None,
             "logicalParentUuid": logical_parent, "isMeta": False,
             "compactMetadata": {"trigger": trigger, "preTokens": 160000, "postTokens": 9000}},
            {"type": "user", "timestamp": iso(t + 1), "uuid": s, "parentUuid": b, "isCompactSummary": True,
             "isVisibleInTranscriptOnly": True,
             "message": {"role": "user", "content": "summary so far: the notes-api search is paged and indexed"}}], s


def turn_records(i, t, parent, p, sid):
    """Turn i of a session's history and its last uuid, `p` the uuid prefix. The kind by i % 6: 0 a typed ask answered at
    length (a citable reply); 1 a typed ask whose work runs on past a check (the segment a seam can split at t + 30); 2 a
    typed reply to one card (the romp-goal-id marker: no plain prompt); 3 a typed ask with a second typed prompt mid-turn
    (two segments); 4 a bare "continue" (every such segment shares one key across turns: the later turn wins); 5 a
    "continue" with a second "continue" mid-turn (two segments of one turn sharing a key: the later segment wins)."""
    u, a, c, r, c2, r2, m = ("%s-%s%02d" % (p, k, i) for k in ("u", "a", "c", "r", "d", "e", "m"))
    tid, tid2 = "toolu_%s%02d" % (p, i), "toolu_%sx%02d" % (p, i)
    kind = i % 6
    if kind == 0:
        return [user(t, u, parent, "step %d: page the notes-api search" % i),
                reply(t + 20, a, u, "Step %d: %s." % (i, PROSE))], a
    if kind == 1:
        return [user(t, u, parent, "step %d: check the retry budget" % i), tool_call(t + 10, c, u, tid),
                tool_out(t + 20, r, c, tid, "budget: 4096"), tool_call(t + 40, c2, r, tid2),
                tool_out(t + 50, r2, c2, tid2, "budget kept"), reply(t + 60, a, r2, "Step %d: %s." % (i, PROSE))], a
    if kind == 2:
        return [user(t, u, parent, "answer for step %d <!-- romp-goal-id: %s:g2 -->" % (i, sid)),
                reply(t + 20, a, u, "Noted.")], a
    if kind == 3:
        return [user(t, u, parent, "step %d: index the notes" % i), tool_call(t + 10, c, u, tid),
                tool_out(t + 20, r, c, tid, "indexed"), user(t + 30, m, r, "also check the cursor for step %d" % i),
                reply(t + 40, a, m, "Step %d: %s." % (i, PROSE))], a
    if kind == 4:
        return [user(t, u, parent, "continue"), reply(t + 20, a, u, "Continued.")], a
    return [user(t, u, parent, "continue"), tool_call(t + 10, c, u, tid), tool_out(t + 20, r, c, tid, "next page"),
            user(t + 30, m, r, "continue"), reply(t + 40, a, m, "Continued again.")], a


def one_pass(turns, store):
    """The walk as it stood before the hold (2026-10-06), the oracle: _feed_session_entry's one-pass loop over every
    turn, its try included, and the plain-prompt time over every turn."""
    seg_uuid, seg_trig, seg_best, cite_uuids = {}, {}, {}, set()
    seg_turn = {}
    try:
        for turn in turns:
            for seg in km._segs_seam(turn, store):
                seg_turn[km._seg_key(seg["id"])] = (turn, seg["atoms"])
                w, r = km._seg_anchors(seg["atoms"])
                seg_uuid[km._seg_key(seg["id"])] = r or km._seg_jump(seg["atoms"])
                seg_trig[km._seg_key(seg["id"])] = jd._prompt_anchor_uuid(seg)
                _lu, _lsub = km._seg_last_text(seg["atoms"])
                seg_best[km._seg_key(seg["id"])] = (_lu, _lsub, seg.get("t", 0))
                pcs_ = em.turn_scalar(turn, "pcs")
                if pcs_ is not None:
                    cite_uuids.update(u_ for u_, n_ in pcs_.items() if n_ >= jd.CITE_MIN_CHARS)
                    continue
                for _a in seg["atoms"]:
                    if _a.get("uuid") and km._atom_prose_chars(_a) >= jd.CITE_MIN_CHARS:
                        cite_uuids.add(_a["uuid"])
    except Exception:
        pass
    return seg_uuid, seg_trig, seg_best, cite_uuids, seg_turn


def sig(atoms):
    """What a (turn, atoms) answer is made of, atom by atom."""
    return [(a.get("uuid"), a.get("type"), a.get("t")) for a in atoms]


class _Backend:
    """The two backend reads the parse takes: the pending rewind cut and the owner bit."""

    def __init__(self):
        self.cut = {}

    def pending_cut(self, sid):
        return self.cut.get(sid, "")

    def owns(self, sid):
        return False


class HoldWorld(unittest.TestCase):
    """Two sessions under a private state root, their transcripts written by the test, walked by the kernel's
    _feed_seg_walk over the kernel's own parse. No test of its own."""

    TURNS = 30                                     # each history's closed turns, its open turn after them

    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()   # the parse is handed the real path (the reader's and the assembly's keys)
        self.addCleanup(shutil.rmtree, self.td, True)
        with km._FEED_SEG_LOCK:
            held = (collections.OrderedDict(km._FEED_SEG_HELD), dict(km._FEED_SEG_STATS))
            km._FEED_SEG_HELD.clear()
        self.saved = (jd.STATE, em._CKPT_DIR_FN, km._sdk, km._codex, km._segs_seam, jd._PENDING_CUT_FN,
                      dict(em._CKPT_CYCLE), held)
        jd._rebind_state(self.td / "state")
        for d in ("states", "checkpoints", "captions", "goals"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")              # this state root is the test's own: no host spawns
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        em.checkpoint_cycle_begin(1 << 40)                            # a document write is never deferred by an earlier test's spend
        self.backend = _Backend()
        km._sdk = lambda: self.backend
        km._codex = lambda: None
        jd.set_pending_cut_provider(self.backend.pending_cut)
        self.walked, self.counting = set(), False

        def spy(turn, store):
            if self.counting:
                self.walked.add(id(turn))
            return REAL_SEGS_SEAM(turn, store)
        km._segs_seam = spy
        self.proj = self.td / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.leaf = {sid: str(self.proj / (sid + ".jsonl")) for sid in (SID_WEB, SID_API, SID_TESTS)}
        self.recs = {sid: [] for sid in self.leaf}
        self.clock = int(time.time()) - 5000                          # every write's mtime, a second apart
        self.t0 = int(time.time()) - 6 * 3600
        self.store = {"nodes": {}, "status": {}, "seams": []}         # the store the walk segments under (its seams)
        self.next_t = {}
        self.fresh()

    def tearDown(self):
        self.fresh()
        (state, ckpt, km._sdk, km._codex, km._segs_seam, cut_fn, cyc, held) = self.saved
        jd.set_pending_cut_provider(cut_fn)
        em.set_checkpoint_dir(ckpt)
        em._CKPT_CYCLE.clear()
        em._CKPT_CYCLE.update(cyc)
        with km._FEED_SEG_LOCK:
            km._FEED_SEG_HELD.clear()
            km._FEED_SEG_HELD.update(held[0])
            km._FEED_SEG_STATS.clear()
            km._FEED_SEG_STATS.update(held[1])
        jd._rebind_state(state)

    # ── the world's writes ──

    def fresh(self):
        """A kernel restart's in-memory side of the parse: no reader entry, assembly entry, stored parse or read memo.
        The walk's held aggregates are left as they stand (a test clears them when it means to)."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear()
            em._HYDRATED_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with em._MAT_LOCK:
            em._MAT_LRU.clear()
        memo = getattr(em, "_ASM_DOC_MEMO", None)
        if memo is not None:
            with em._ASM_CKPT_LOCK:
                memo.clear()
                getattr(em, "_ASM_DOC_MEMO_BYTES", [0])[0] = 0
        jd.parse_cache_clear()
        km._parse_mode.clear()

    def stamp(self, path):
        self.clock += 1
        os.utime(path, (self.clock, self.clock))

    def append(self, sid, recs):
        with open(self.leaf[sid], "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        self.recs[sid] += recs
        self.stamp(self.leaf[sid])

    def write_leaf(self, path, recs, replace=False):
        body = "".join(json.dumps(r) + "\n" for r in recs)
        if replace:
            with open(path + ".new", "w") as f:
                f.write(body)
            os.replace(path + ".new", path)
        else:
            with open(path, "w") as f:
                f.write(body)
        self.stamp(path)

    def history(self, sid=SID_WEB, p="w"):
        """TURNS closed turns of the six kinds and an open one (a typed ask whose tool call is running)."""
        parent = None
        for i in range(self.TURNS):
            recs, parent = turn_records(i, self.t0 + 300 * i, parent, p, sid)
            self.append(sid, recs)
        t = self.t0 + 300 * self.TURNS
        n = self.TURNS
        self.append(sid, [user(t, "%s-u%02d" % (p, n), parent, "ship step %d" % n),
                          tool_call(t + 10, "%s-c%02d" % (p, n), "%s-u%02d" % (p, n), "toolu_%s%02d" % (p, n))])
        self.next_t[sid] = t + 20

    def tick(self, sid=SID_WEB, dt=10):
        self.next_t[sid] += dt
        return self.next_t[sid]

    def last(self, sid=SID_WEB):
        return next(r["uuid"] for r in reversed(self.recs[sid]) if r.get("uuid"))

    def open_new_turn(self, sid, k, text):
        """The open turn's reply lands (end_turn) and a new typed ask opens the next turn with a running tool call."""
        a, u, c = "w-ya%02d" % k, "w-yu%02d" % k, "w-yc%02d" % k
        self.append(sid, [reply(self.tick(sid), a, self.last(sid), "Shipped: %s." % PROSE)])
        t = self.tick(sid, 200)
        self.append(sid, [user(t, u, a, text), tool_call(t + 10, c, u, "toolu_y%02d" % k)])
        self.next_t[sid] = t + 10

    # ── the walk and its oracle ──

    def tree(self, sid=SID_WEB):
        return km._parse(self.leaf[sid], sid, time.time())

    def head(self, sid=SID_WEB):
        p = self.leaf[sid]
        return (p, os.stat(p).st_ino)

    def walk(self, sid=SID_WEB, store=None):
        """(the walk, its plain-prompt time, the turns it folded, the parse's turns) over the session's current parse."""
        turns = self.tree(sid)["turns"]
        store = self.store if store is None else store
        self.walked, self.counting = set(), True
        try:
            w = km._feed_seg_walk(sid, self.head(sid), turns, store)
        finally:
            self.counting = False
        return w, km._feed_seg_plain_t(w), len(self.walked), turns

    def assert_maps(self, label, w, plain_t, turns, store=None):
        ref = one_pass(turns, self.store if store is None else store)
        seg_uuid, seg_trig, seg_best, cite, seg_turn = ref
        self.assertEqual(w.seg_uuid, seg_uuid, "%s: the work anchors" % label)
        self.assertEqual(w.seg_trig, seg_trig, "%s: the prompt anchors" % label)
        self.assertEqual(w.seg_best, seg_best, "%s: the latest prose and its time" % label)
        self.assertEqual(w.cite_uuids, cite, "%s: the citable uuids" % label)
        for k, (turn, atoms) in seg_turn.items():
            self.assertIn(k, w.seg_turn, "%s: seg_turn holds %s" % (label, k))
            got = w.seg_turn.get(k)
            self.assertIsNotNone(got, "%s: seg_turn answers %s" % (label, k))
            self.assertIs(got[0], turn, "%s: %s answers the same turn object" % (label, k))
            self.assertEqual(sig(got[1]), sig(atoms), "%s: %s answers the segment the dict held" % (label, k))
        self.assertEqual(set(w.seg_turn._pos), set(seg_turn), "%s: seg_turn holds exactly the dict's keys" % label)
        self.assertNotIn("no-such-key", w.seg_turn, "%s: a key the dict never held" % label)
        self.assertIsNone(w.seg_turn.get("no-such-key"))
        self.assertEqual(w.seg_turn.get("no-such-key", "dflt"), "dflt")
        self.assertEqual(plain_t, km._last_plain_user_turn_t(turns), "%s: the plain-prompt time" % label)
        return ref

    def check(self, label, sid=SID_WEB, store=None, folded=None, at_most=None, whole=False):
        """Walk, compare with the oracle, and pin the turns folded: exactly `folded`, at most `at_most`, or every turn."""
        w, plain_t, n, turns = self.walk(sid, store)
        self.assert_maps(label, w, plain_t, turns, store)
        if whole:
            self.assertEqual(n, len(turns), "%s: every one of the %d turns folded" % (label, len(turns)))
        if folded is not None:
            self.assertEqual(n, folded, "%s: %d of the %d turns folded" % (label, folded, len(turns)))
        if at_most is not None:
            self.assertLessEqual(n, at_most, "%s: at most %d of the %d turns folded" % (label, at_most, len(turns)))
        return w, plain_t, turns

    def stats(self):
        with km._FEED_SEG_LOCK:
            return dict(km._FEED_SEG_STATS)

    def new_build(self):
        """A board build begins, as build_feed begins one (tests/test_feed_session_memo.py pins that it does, once per
        build). A kernel without the build number (the base commit this module's red-first run pins against) has nothing
        to begin, so there every walk is one build and a test runs red on its own assertion rather than erroring here."""
        begin = getattr(km, "_feed_seg_build_begin", None)
        if begin is not None:
            begin()

    def held_is_the_closed_turns(self, label, sid=SID_WEB):
        """The held entry's maps equal the one-pass walk over the session's closed turns alone: no open turn's segment is
        ever held, and no derivation folded into a held map."""
        turns = self.tree(sid)["turns"]
        seg_uuid, seg_trig, seg_best, cite, seg_turn = one_pass(turns[:-1], self.store)
        with km._FEED_SEG_LOCK:
            maps = km._FEED_SEG_HELD[sid][3]
        self.assertEqual(maps[0], seg_uuid, "%s: the held work anchors are the closed turns'" % label)
        self.assertEqual(maps[1], seg_trig, "%s: the held prompt anchors are the closed turns'" % label)
        self.assertEqual(maps[2], seg_best, "%s: the held latest prose is the closed turns'" % label)
        self.assertEqual(maps[3], cite, "%s: the held citable uuids are the closed turns'" % label)
        self.assertEqual(set(maps[4]), set(seg_turn), "%s: the held positions name the closed turns' keys" % label)
        for k, (ti, _si) in maps[4].items():
            self.assertEqual(turns[ti]["id"], seg_turn[k][0]["id"], "%s: %s's position names its turn" % (label, k))


class TheRoad(HoldWorld):
    def test_after_every_step_the_maps_equal_the_one_pass_walk_and_only_the_moved_turns_fold(self):
        self.history()
        n = self.TURNS
        w, t_rest, turns = self.check("the history, cold", whole=True)
        self.assertGreaterEqual(len(w.seg_uuid), n // 2, "the premise: the maps are not empty")
        self.assertTrue(w.cite_uuids, "the premise: the history holds citable prose")
        self.assertIn(SID_WEB, km._FEED_SEG_HELD, "the closed turns are held")
        r0 = km._feed_memo_report()                     # read as GET /perf reads them (builds.feed.memo), not the table
        self.check("the same parse again", folded=1)
        r1 = km._feed_memo_report()
        self.assertEqual({k: r1[k] - r0[k] for k in ("segWalks", "segWalkTurns", "segHeld")},
                         {"segWalks": 1, "segWalkTurns": 1, "segHeld": n},
                         "a reuse of the same parse: one walk, the open turn folded, every closed turn served from the hold")

        # the open turn grows, then closes and another opens
        self.append(SID_WEB, [tool_out(self.tick(), "w-r%02d" % n, "w-c%02d" % n, "toolu_w%02d" % n, "tests: 41 passed")])
        self.check("an append to the open turn", folded=1)
        self.open_new_turn(SID_WEB, 1, "next: page the archive")
        self.check("the open turn closes and another opens", folded=2)

        # the store: a republish with the same seams, a seam that splits an old segment, the seam removed
        self.store = {"nodes": {"x": {"text": "moved"}}, "status": {"x": "working"}, "seams": []}
        self.check("a republish with the same seams", folded=1)
        before = dict(w.seg_uuid)
        s13 = next(s for s in REAL_SEGS_SEAM(self.tree()["turns"][13], {}) if s["trigger"] == "w-u13")
        seam = {"segs": [jd._seg_key(s13["id"])], "t": s13["t"] + 30, "top": SID_WEB + ":g4", "text": "Paged the results"}
        self.store = {"nodes": {}, "status": {}, "seams": [seam]}
        s0 = self.stats()
        w, _t, turns = self.check("a seam splits an old segment", whole=True)
        self.assertEqual(self.stats()["segReset"] - s0["segReset"], 1, "the seams moved: the aggregate is discarded whole")
        key13 = jd._seg_key(s13["id"])
        self.assertEqual(before[key13], "w-a13", "the premise: before the seam the segment's work anchor is its reply")
        self.assertEqual(w.seg_uuid[key13], "w-c13", "the premise: the seam moved the reply into the tail")
        self.store = {"nodes": {}, "status": {}, "seams": []}
        self.check("the seam removed", whole=True)

        # a rewind cut and its release
        self.backend.cut[SID_WEB] = "w-a20"            # the cut world ends at turn 20
        s0 = self.stats()
        w, t_cut, turns = self.check("a rewind cut at an old reply", whole=True)
        self.assertEqual(len(turns), 21, "the premise: the cut world ends at turn 20")
        self.assertEqual(self.stats()["segPartial"] - s0["segPartial"], 1, "the held keys diverged inside the aggregate")
        self.assertLess(t_cut, t_rest, "the premise: the rewind took the latest plain prompts")
        self.backend.cut[SID_WEB] = ""
        turns = self.tree()["turns"]
        self.check("the cut released", folded=len(turns) - 20)   # the aggregate the cut world stored covers turns 0..19

        # a compaction: the open turn closes, the boundary and its summary land, a new ask opens
        self.append(SID_WEB, [reply(self.tick(), "w-ya90", self.last(), "Closed out the archive.")])
        recs, s = compaction(self.tick(dt=300), "1", self.last(), "auto")
        t = self.tick(dt=10)
        self.append(SID_WEB, recs + [user(t, "w-yu91", s, "after the compaction: what remains of the search?"),
                                     tool_call(t + 10, "w-yc91", "w-yu91", "toolu_y91")])
        self.next_t[SID_WEB] = t + 10
        self.check("an auto compaction", at_most=4)

        # a document, a restart that restores from it, the walk with the hold kept and with it cleared, a demote
        leaf = self.leaf[SID_WEB]
        self.assertTrue(em.asm_checkpoint_write(leaf, SID_WEB, sdk_human=bool(km._display_sdk_human(SID_WEB)), tree=self.tree()),
                        "the document is written: %r" % em.asm_checkpoint_stats().get("skipped"))
        self.fresh()
        self.append(SID_WEB, [tool_out(self.tick(), "w-yr91", "w-yc91", "toolu_y91", "archive: 3 pages")])
        turns = self.tree()["turns"]
        self.assertEqual(km._parse_mode.get(leaf), "restore", "the premise: the parse took the restore road")
        self.assertTrue(any(t.get("pre") for t in turns), "the premise: restored pre-cut turns")
        s0 = self.stats()
        self.check("the restored turns under the whole road's aggregate", at_most=2)
        self.assertGreater(self.stats()["segHeld"] - s0["segHeld"], n, "the restored turns' keys are the whole road's")
        with km._FEED_SEG_LOCK:
            km._FEED_SEG_HELD.clear()                  # a kernel restart: the table is empty
        self.check("the restored turns walked cold", whole=True)
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(leaf, None)
        em._read_jsonl_entry(leaf, tail_ok=False)     # a from-zero read: a fresh generation, the next parse demotes
        self.append(SID_WEB, [reply(self.tick(), "w-ya92", "w-yr91", "Archive paged.", stop="tool_use")])
        self.tree()
        self.check("a demote of the restored entry (the parse's road: %s)" % km._parse_mode.get(leaf), at_most=2)

        # a late record whose time sorts into a closed turn: whichever turn the parse files it in, the maps are the oracle's
        self.append(SID_WEB, [reply(self.t0 + 300 * 5 + 25, "w-l05", self.last(), "Noted: %s." % PROSE)])
        turns = self.tree()["turns"]
        into_closed = any(a.get("uuid") == "w-l05" for x in turns[:-1] for a in x["atoms"])
        self.check("a late record whose time sorts into a closed turn", whole=into_closed)

        # a resume into a new file (the same records and a new turn): only the leaf's head tells it apart
        res = str(self.proj / (RESUME_FILE + ".jsonl"))
        t = self.tick(dt=300)
        recs = list(self.recs[SID_WEB]) + [user(t, "w-res1", self.last(), "resumed: where were we?")]
        self.write_leaf(res, recs)
        held_keys = km._FEED_SEG_HELD[SID_WEB][2]
        self.leaf[SID_WEB], self.recs[SID_WEB] = res, recs
        turns = self.tree()["turns"]
        self.assertEqual(tuple(km._turn_hold_key(x) for x in turns[:len(held_keys)]), held_keys,
                         "the premise: the resumed file's closed turns carry the held keys")
        s0 = self.stats()
        self.check("a resume into a new file", whole=True)
        self.assertEqual(self.stats()["segReset"] - s0["segReset"], 1, "another leaf: the aggregate is discarded whole")

        # the leaf replaced at its path, one tool call re-recorded under a new uuid: every turn key stands
        held_keys = km._FEED_SEG_HELD[SID_WEB][2]
        w_prev, _t, _n, _turns = self.walk()
        recs = [dict(r) for r in self.recs[SID_WEB]]
        for r in recs:
            if r.get("uuid") == "w-c09":
                r["uuid"] = "w-x09"                    # the first segment's work anchor, inside the turn: not a key's uuid
            if r.get("parentUuid") == "w-c09":
                r["parentUuid"] = "w-x09"
        ino = os.stat(self.leaf[SID_WEB]).st_ino
        self.write_leaf(self.leaf[SID_WEB], recs, replace=True)
        self.recs[SID_WEB] = recs
        self.assertNotEqual(os.stat(self.leaf[SID_WEB]).st_ino, ino, "the premise: a new file under the path")
        turns = self.tree()["turns"]
        self.assertEqual(tuple(km._turn_hold_key(x) for x in turns[:len(held_keys)]), held_keys,
                         "the premise: every held turn key stands")
        w, _t, turns = self.check("the leaf replaced, every turn key standing", whole=True)
        k9 = next(k for k, v in w.seg_uuid.items() if v == "w-x09")
        self.assertEqual(w_prev.seg_uuid[k9], "w-c09", "the premise: the replaced file moved a work anchor")

        # a /clear: a new leaf with a history of its own
        clr = str(self.proj / (CLEAR_FILE + ".jsonl"))
        t = self.tick(dt=300)
        recs = [user(t, "w-clr1", None, "fresh start on the schema"), reply(t + 20, "w-clr2", "w-clr1", "Starting: %s." % PROSE),
                user(t + 300, "w-clr3", "w-clr2", "then the schema's indexes"), tool_call(t + 310, "w-clr4", "w-clr3", "toolu_clr")]
        self.write_leaf(clr, recs)
        self.leaf[SID_WEB], self.recs[SID_WEB] = clr, recs
        self.check("a /clear", whole=True)


class Failures(HoldWorld):
    def test_a_fold_that_raises_keeps_its_partial_maps_and_stores_nothing(self):
        self.history()
        turns = self.tree()["turns"]
        whole = one_pass(turns, self.store)
        calls = []
        real = km._seg_anchors

        def raising(atoms):
            calls.append(1)
            if len(calls) == 15:
                raise em.LazyBodyRead("a synthetic body read fault, partway through the walk")
            return real(atoms)
        s0 = self.stats()
        with mock.patch.object(km, "_seg_anchors", raising):
            w = km._feed_seg_walk(SID_WEB, self.head(), turns, self.store)
            plain_t = km._feed_seg_plain_t(w)
            self.assertGreaterEqual(len(calls), 15, "the premise: the fold met the fault")
            del calls[:]
            self.assert_maps("the walk that raised, against the oracle meeting the same fault", w, plain_t, turns)
        self.assertLess(len(w.seg_uuid), len(whole[0]), "the premise: the maps stopped short")
        self.assertNotIn(SID_WEB, km._FEED_SEG_HELD, "a walk that raised stored nothing")
        s1 = self.stats()
        self.assertEqual(s1["segRaised"] - s0["segRaised"], 1)
        self.assertEqual({k: s1[k] - s0[k] for k in ("segWalks", "segHeld", "segWalkTurns")},
                         {"segWalks": 0, "segHeld": 0, "segWalkTurns": 0},
                         "a walk that did not finish counts no turns (the review after the merge)")
        self.check("the next walk, nothing raising", whole=True)
        self.assertIn(SID_WEB, km._FEED_SEG_HELD)
        self.check("and the one after it", folded=1)

    def test_a_plain_prompt_read_that_raises_fails_and_stores_nothing_while_the_anchor_maps_stay_whole(self):
        self.history()
        turns = self.tree()["turns"]
        s0 = self.stats()
        with mock.patch.object(km, "_atom_user_text", side_effect=em.LazyBodyRead("a synthetic body read fault")):
            w = km._feed_seg_walk(SID_WEB, self.head(), turns, self.store)
            with self.assertRaises(em.LazyBodyRead):
                km._feed_seg_plain_t(w)
        self.assertNotIn(SID_WEB, km._FEED_SEG_HELD, "a plain-prompt read that raised stored nothing")
        self.assertEqual(w.seg_uuid, one_pass(turns, self.store)[0], "the two failures stay apart: the anchor maps are whole")
        s1 = self.stats()
        self.assertEqual(s1["segRaised"] - s0["segRaised"], 1)
        self.assertEqual({k: s1[k] - s0[k] for k in ("segWalks", "segHeld", "segWalkTurns")},
                         {"segWalks": 0, "segHeld": 0, "segWalkTurns": 0},
                         "a walk whose plain-prompt read raised did not finish: it counts no turns")
        self.check("the next walk", whole=True)

    def _failing_walks(self, label, store):
        """Two walks over the current parse whose anchor fold raises at its first segment: (stats before, stats after)."""
        turns = self.tree()["turns"]
        s0 = self.stats()
        with mock.patch.object(km, "_seg_anchors", side_effect=em.LazyBodyRead("a synthetic body read fault")):
            for i in (1, 2):
                w = km._feed_seg_walk(SID_WEB, self.head(), turns, store)
                km._feed_seg_plain_t(w)
                self.assertFalse(w.ok, "the premise: %s, walk %d raised" % (label, i))
                self.assertNotIn(SID_WEB, km._FEED_SEG_HELD, "%s: walk %d left no entry it could not use" % (label, i))
        return s0, self.stats()

    def test_an_entry_the_walk_cannot_use_leaves_the_table_even_when_the_walk_then_fails(self):
        """The review after the merge: the entry was compared outside the lock and only dropped by a later successful
        store, so while the walk kept failing an entry for other seams, or one whose keys diverged, stayed in the table
        (newest in its order, ahead of colder sessions' valid entries) and was counted again by every derivation. Now the
        walk that finds it deletes it, and it is counted once."""
        self.history()
        self.check("cold", whole=True)
        s13 = next(s for s in REAL_SEGS_SEAM(self.tree()["turns"][13], {}) if s["trigger"] == "w-u13")
        seam = {"segs": [jd._seg_key(s13["id"])], "t": s13["t"] + 30, "top": SID_WEB + ":g4", "text": "Paged the results"}
        s0, s1 = self._failing_walks("other seams", {"nodes": {}, "status": {}, "seams": [seam]})
        self.assertEqual((s1["segReset"] - s0["segReset"], s1["segRaised"] - s0["segRaised"]), (1, 2),
                         "discarded whole once, by the walk that found it; both walks raised")
        self.check("the walk after, nothing raising", whole=True)
        self.assertIn(SID_WEB, km._FEED_SEG_HELD)
        self.backend.cut[SID_WEB] = "w-a20"            # a rewind: the held keys diverge inside the entry
        s0, s1 = self._failing_walks("a rewind cut", self.store)
        self.assertEqual((s1["segPartial"] - s0["segPartial"], s1["segRaised"] - s0["segRaised"]), (1, 2),
                         "the diverged entry is dropped once, by the walk that found it")
        self.check("the cut world walked, nothing raising", whole=True)

    def test_seams_that_are_a_truthy_scalar_hold_nothing_and_raise_nothing_out_of_the_walk(self):
        """A goal store whose seams field is a truthy scalar where a list belongs (1, True, 1.5). The one-pass loop met
        it inside its try, so it cost only the anchors; the held walk read the seams' fingerprint before the fold's try,
        so the raise left the derivation and, on a cold kernel, the session's cards left the board. Now the walk holds
        nothing for such a store, drops the entry held under good seams, and its maps are the oracle's (empty: the
        oracle meets the seams at its first segment)."""
        self.history()
        self.check("cold, the seams a list", whole=True)
        self.assertIn(SID_WEB, km._FEED_SEG_HELD, "the premise: web's closed turns are held")
        turns = self.tree()["turns"]
        for bad in (1, True, 1.5):
            store = {"nodes": {}, "status": {}, "seams": bad}
            s0 = self.stats()
            w = km._feed_seg_walk(SID_WEB, self.head(), turns, store)
            plain_t = km._feed_seg_plain_t(w)
            self.assertFalse(w.ok, "%r: the fold met the seams inside its guard" % (bad,))
            self.assertNotIn(SID_WEB, km._FEED_SEG_HELD, "%r: nothing is held under seams the walk cannot read" % (bad,))
            self.assert_maps("seams %r" % (bad,), w, plain_t, turns, store)
            s1 = self.stats()
            self.assertEqual(s1["segRaised"] - s0["segRaised"], 1, "%r: counted as a walk that stored nothing" % (bad,))
            self.assertEqual(s1["segWalks"] - s0["segWalks"], 0, "%r: a walk that did not finish" % (bad,))
        self.check("the seams a list again: every turn", whole=True)
        self.assertIn(SID_WEB, km._FEED_SEG_HELD, "held again under seams it can read")

    def test_a_lazy_lookup_whose_segmentation_raises_answers_none_and_counts_the_landing_fault(self):
        self.history()
        self.check("cold", whole=True)
        w, _t, _n, turns = self.walk()
        k = next(iter(w.seg_uuid))
        f0 = km._SUMMARY_ANCHOR_STATS["fault"]
        with mock.patch.object(km, "_segs_seam", side_effect=em.LazyBodyRead("a synthetic body read fault")):
            self.assertIsNone(w.seg_turn.get(k), "a turn that cannot be segmented answers the default")
        self.assertEqual(km._SUMMARY_ANCHOR_STATS["fault"] - f0, 1, "...counted as the landing tier's fault (served, not memoized)")
        self.assertIsNotNone(w.seg_turn.get(k), "the next lookup segments again")


class TheCutInsideTheOpenTurn(HoldWorld):
    def test_a_cut_inside_the_open_turn_is_served_the_closed_turns_and_nothing_of_the_removed_segment(self):
        """The two copies the walk makes (the review after the merge): it copies the held maps before folding into them,
        and copies the closed turns' maps before folding the open turn, so the held aggregate is the closed turns alone.
        Every rewind the road drives cuts at a closed turn's reply, where the held keys diverge and every turn walks from
        fresh maps, so neither copy was pinned. A bare rollback that deletes a mid-turn prompt cuts the parse at the
        prompt's parent, inside the open turn, with every closed turn standing: without the second copy the cold walk
        holds the open turn's second segment, without the first a reuse walk folds it into the held maps, and either way
        the cut's walk is served an anchor at work the rollback removed."""
        self.history()
        n = self.TURNS
        r, m, c2 = "w-r%02d" % n, "w-m%02d" % n, "w-n%02d" % n
        t = self.tick()
        self.append(SID_WEB, [tool_out(t, r, "w-c%02d" % n, "toolu_w%02d" % n, "tests: 41 passed"),
                              user(t + 10, m, r, "also page the archive while the tests run"),
                              tool_call(t + 20, c2, m, "toolu_wn%02d" % n)])
        self.next_t[SID_WEB] = t + 20
        w, _t, turns = self.check("cold, the open turn in two segments", whole=True)
        segs = REAL_SEGS_SEAM(turns[-1], self.store)
        self.assertEqual(len(segs), 2, "the premise: the typed prompt opened a second segment in the open turn")
        k2 = km._seg_key(segs[1]["id"])
        self.assertIn(k2, w.seg_uuid, "the premise: the second segment has a work anchor")
        self.held_is_the_closed_turns("after the cold walk")
        self.check("a reuse of the same parse", folded=1)
        self.held_is_the_closed_turns("after a reuse walk")
        self.backend.cut[SID_WEB] = r                   # the mid-turn prompt rolled back: the parse ends at its parent
        w, _t, turns = self.check("the cut inside the open turn", folded=1)
        self.assertEqual(len(turns), n + 1, "the premise: every closed turn stands")
        self.assertEqual(len(REAL_SEGS_SEAM(turns[-1], self.store)), 1, "the premise: the cut took the second segment")
        self.assertNotIn(k2, w.seg_uuid, "no anchor of the removed segment")
        self.held_is_the_closed_turns("after the cut")


class Isolation(HoldWorld):
    def test_one_sid_at_two_paths_is_never_served_the_other_paths_anchors(self):
        """The test-isolation hazard (the critique's F2): a turn key names no file, so two worlds that reuse one sid, one
        clock and one text would share keys. World B is world A's records with one tool call re-recorded under a new uuid
        (inside its turn, so every key stands) at another path: only the leaf's head tells them apart."""
        self.history()
        w_a, _t, turns_a = self.check("world A", whole=True)
        keys_a = tuple(km._turn_hold_key(x) for x in turns_a[:-1])
        recs = [dict(r) for r in self.recs[SID_WEB]]
        for r in recs:
            if r.get("uuid") == "w-c09":
                r["uuid"] = "w-x09"
            if r.get("parentUuid") == "w-c09":
                r["parentUuid"] = "w-x09"
        other = self.td / "projects-b" / "-w-notes-api"
        other.mkdir(parents=True)
        self.leaf[SID_WEB] = str(other / (SID_WEB + ".jsonl"))
        self.write_leaf(self.leaf[SID_WEB], recs)
        self.recs[SID_WEB] = recs
        turns_b = self.tree()["turns"]
        self.assertEqual(tuple(km._turn_hold_key(x) for x in turns_b[:-1]), keys_a, "the premise: the two worlds' keys match")
        w_b, _t, _turns = self.check("world B, the same sid at another path", whole=True)
        self.assertNotEqual(w_a.seg_uuid, w_b.seg_uuid, "the premise: the two worlds' anchors differ")


class BoundAndDepartures(HoldWorld):
    def histories(self):
        for sid, p in ((SID_WEB, "w"), (SID_API, "p"), (SID_TESTS, "t")):
            self.history(sid, p)

    def test_the_byte_bound_sheds_the_least_recently_used_session_and_the_report_says_so(self):
        self.history(SID_WEB, "w")
        self.history(SID_API, "p")
        self.new_build()
        self.check("web", whole=True)
        size = km._FEED_SEG_HELD[SID_WEB][5]
        head, fp, keys, maps, _plain, _size = km._FEED_SEG_HELD[SID_WEB][:6]
        self.assertEqual(size, km._feed_seg_entry_bytes(keys, maps), "the entry carries its estimate")
        self.new_build()                                # api's store is the next build's: web is not used in it
        with mock.patch.object(km, "FEED_SEG_HELD_BYTES", size):
            e0 = self.stats()["segEvict"]
            self.check("api", sid=SID_API, whole=True)
            self.assertEqual(list(km._FEED_SEG_HELD), [SID_API], "the least recently used session went first")
            self.assertEqual(self.stats()["segEvict"] - e0, 1)
            rep = km._feed_memo_report()
            self.assertEqual(rep["segEntries"], 1)
            self.assertEqual(rep["segBytes"], km._FEED_SEG_HELD[SID_API][5])
            self.assertLessEqual(rep["segBytes"], size)
            self.assertEqual(rep["segBound"], size)
        with mock.patch.object(km, "FEED_SEG_HELD_BYTES", 64):
            s0 = self.stats()
            self.check("web over the bound alone", whole=True)
            self.assertNotIn(SID_WEB, km._FEED_SEG_HELD, "an aggregate over the bound alone is not held")
            self.assertIn(SID_API, km._FEED_SEG_HELD, "...and sheds no other session")
            s1 = self.stats()
            self.assertEqual((s1.get("overBound", 0) - s0.get("overBound", 0), s1["segEvict"] - s0["segEvict"]), (1, 0),
                             "an aggregate that was never held is counted under overBound, not as an eviction "
                             "(the review after the merge)")
            self.assertEqual(km._feed_memo_report()["overBound"], s1["overBound"], "...and GET /perf reports it")
            self.check("web again: nothing held, so every turn", whole=True)

    def test_a_reused_entry_is_the_newest_so_the_bound_sheds_a_colder_session_first(self):
        """The mark that makes a reused entry the newest (the review after the merge): web is stored, then api, then web
        is reused; a third session's store past a bound that fits two sheds api and keeps web. A third is needed, since a
        store re-inserts its own session as the newest."""
        self.histories()
        self.new_build()
        self.check("build 1: web", whole=True)
        self.check("build 1: api", sid=SID_API, whole=True)
        self.assertEqual(list(km._FEED_SEG_HELD), [SID_WEB, SID_API], "the premise: stored in that order")
        size = km._FEED_SEG_HELD[SID_WEB][5]
        self.assertEqual(km._FEED_SEG_HELD[SID_API][5], size, "the premise: the histories weigh the same")
        self.new_build()
        self.check("build 2: web reused after api was stored", folded=1)
        self.assertEqual(list(km._FEED_SEG_HELD), [SID_API, SID_WEB], "a reused entry is the newest")
        self.new_build()
        with mock.patch.object(km, "FEED_SEG_HELD_BYTES", 2 * size):   # fits two of the three
            e0 = self.stats()["segEvict"]
            self.check("build 3: tests", sid=SID_TESTS, whole=True)
            self.assertEqual(list(km._FEED_SEG_HELD), [SID_WEB, SID_TESTS], "api, the colder, was shed; web kept")
            self.assertEqual(self.stats()["segEvict"] - e0, 1)

    def test_past_the_bound_a_build_keeps_the_entries_it_uses_and_holds_no_newcomer(self):
        """The shed past the bound (the review after the merge): it took the least recently used entry, which every build
        derives first, so once the working sessions' aggregates outgrew the bound each store shed the entry the build's
        next derivation needed and every session walked every turn on every build. Now an entry carries the number of the
        last build that used it, a store sheds only entries this build did not use, and when none is left the newcomer is
        not held. Three sessions, a bound that fits two, two builds: in the second, two sessions walk only their open
        turn."""
        self.histories()
        self.new_build()
        self.check("build 1: web", whole=True)
        size = km._FEED_SEG_HELD[SID_WEB][5]
        with mock.patch.object(km, "FEED_SEG_HELD_BYTES", 2 * size):   # fits two of the three
            self.check("build 1: api", sid=SID_API, whole=True)
            self.assertEqual(km._FEED_SEG_HELD[SID_API][5], size, "the premise: the histories weigh the same")
            e0 = self.stats()["segEvict"]
            self.check("build 1: tests", sid=SID_TESTS, whole=True)
            self.assertEqual(list(km._FEED_SEG_HELD), [SID_WEB, SID_API],
                             "build 1: the two entries the build used stay, and the newcomer is not held")
            self.assertEqual(self.stats()["segEvict"] - e0, 1, "the newcomer the bound did not admit is counted")
            self.new_build()
            e0 = self.stats()["segEvict"]
            self.check("build 2: web", folded=1)
            self.check("build 2: api", sid=SID_API, folded=1)
            self.check("build 2: tests", sid=SID_TESTS, whole=True)
            self.assertEqual(list(km._FEED_SEG_HELD), [SID_WEB, SID_API], "build 2: the same two entries stand")
            self.assertEqual(self.stats()["segEvict"] - e0, 1)

    def test_a_grown_aggregate_the_bound_does_not_admit_leaves_the_entry_its_walk_was_served_from(self):
        """Past the bound with every other entry used in this build, a session whose aggregate grew by a closed turn is
        not admitted; the entry its walk was served from still stands, so the next build folds the turns since it."""
        self.history(SID_WEB, "w")
        self.history(SID_API, "p")
        self.new_build()
        self.check("build 1: web", whole=True)
        self.check("build 1: api", sid=SID_API, whole=True)
        size = km._FEED_SEG_HELD[SID_WEB][5]
        self.assertEqual(km._FEED_SEG_HELD[SID_API][5], size, "the premise: the histories weigh the same")
        with mock.patch.object(km, "FEED_SEG_HELD_BYTES", 2 * size):   # fits the two as they stand, not a grown one
            self.new_build()
            self.check("build 2: web", folded=1)
            n = self.TURNS
            self.append(SID_API, [tool_out(self.tick(SID_API), "p-r%02d" % n, "p-c%02d" % n, "toolu_p%02d" % n,
                                           "tests: 41 passed")])
            self.open_new_turn(SID_API, 1, "next: page the archive")
            e0 = self.stats()["segEvict"]
            self.check("build 2: api, a turn closed", sid=SID_API, folded=2)
            self.assertEqual(self.stats()["segEvict"] - e0, 1, "the premise: the grown aggregate was not admitted")
            self.assertIn(SID_WEB, km._FEED_SEG_HELD, "the premise: web's entry, used in this build, stands")
            self.assertIn(SID_API, km._FEED_SEG_HELD, "the entry api's walk was served from still stands")
            self.assertEqual(len(km._FEED_SEG_HELD[SID_API][2]), n, "...the one held before the turn closed")
            self.new_build()
            self.check("build 3: web", folded=1)
            self.check("build 3: api", sid=SID_API, folded=2)

    def test_the_bound_is_a_fraction_of_the_machines_memory_unless_the_environment_names_bytes(self):
        with mock.patch.dict(os.environ, {"ROMP_FEED_SEG_HELD_BYTES": "4096"}):
            self.assertEqual(km._feed_seg_held_bound(), 4096)
        for raw in ("lots", "0", "-5"):
            with mock.patch.dict(os.environ, {"ROMP_FEED_SEG_HELD_BYTES": raw}):
                self.assertEqual(km._feed_seg_held_bound(), km._mem_total_bytes() // 128, raw)
        env = {k: v for k, v in os.environ.items() if k != "ROMP_FEED_SEG_HELD_BYTES"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(km._feed_seg_held_bound(), km._mem_total_bytes() // 128)
        self.assertGreater(km.FEED_SEG_HELD_BYTES, 0)

    def test_a_departed_sessions_aggregate_leaves_with_it(self):
        self.history(SID_WEB, "w")
        self.history(SID_API, "p")
        self.check("web", whole=True)
        self.check("api", sid=SID_API, whole=True)
        self.assertEqual(set(km._FEED_SEG_HELD), {SID_WEB, SID_API})
        e0 = self.stats()["segEvict"]
        km._feed_memo_forget({SID_API})              # build_feed's alive set after web departed
        self.assertEqual(set(km._FEED_SEG_HELD), {SID_API})
        self.assertEqual(self.stats()["segEvict"] - e0, 1, "a departure is counted as the card memo counts its own")
        self.assertEqual(km._feed_memo_report()["segEntries"], 1)

    def test_the_held_aggregate_holds_strings_numbers_and_tuples_only(self):
        self.history()
        self.check("cold", whole=True)
        head, fp, keys, maps, plain, size = km._FEED_SEG_HELD[SID_WEB][:6]
        rest = km._FEED_SEG_HELD[SID_WEB][6:]            # the build number that last used it (2026-10-06)
        scalar = (str, int, float, bool, type(None))

        def flat(v):
            return all(flat(x) for x in v) if isinstance(v, tuple) else isinstance(v, scalar)
        self.assertTrue(flat(head) and flat(fp) and flat(keys) and flat(plain) and flat(rest),
                        "head, seams, keys, plain time, build number")
        for i in (0, 1, 2, 4):
            self.assertIs(type(maps[i]), dict)
            for k, v in maps[i].items():
                self.assertIsInstance(k, str)
                self.assertTrue(flat(v), "map %d holds %r under %s" % (i, v, k))
        self.assertIs(type(maps[3]), set)
        self.assertTrue(all(isinstance(u, str) for u in maps[3]))
        self.assertEqual(head, self.head(), "the leaf's path and inode")
        self.assertEqual(len(keys), self.TURNS, "one key per closed turn")


if __name__ == "__main__":
    unittest.main()
