#!/usr/bin/env python3
"""The build road: after every step of a synthetic history, a warm build equals a cold one (2026-10-05).

THE QUESTION. The kernel's builds keep results between builds so a quiet session costs a lookup, and since 2026-10-05
the message-caption scan (_msg_sum_scan_session) keeps each closed turn's (segment id, message ids) pairs too and walks
only the turns that moved. A held result is safe only while every event that can change it moves its key. Reading the
code argues that; this module checks it. One synthetic road drives a session through the events that change its
history, and after every step the warm build (every memo as the previous step left it) is compared with a cold one over
the same inputs, built with the builders' own memos cleared: for the map, the message-caption scan's (_msg_sum_cache,
its held turns included) and the captions mapping (_CAPTION_ROWS_MEMO); for the lanes, the lane prefix memo and the
captions mapping. The parse and the record cache under it, which hold the inputs as read, stay as the warm build left
them. The cold build's memos are put back afterwards, so each step builds on the warm chain and never on what the cold
build left. This change compares the message-caption map (_msg_summaries, the map the timeline's connectors and the
chat's incoming cards read); a later change that holds the board's or the Sessions pane's results adds its builder to
the comparison.

The Sessions pane's lanes joined on 2026-10-06, when the lane prefix (the held bars of a lane's closed turns) began to
survive caption writes and store republishes. Each session's lane is derived as the bars build derives a live lane
(_lane_segments with the leaf's head, the prefix memo as the last step left it) and again from a cleared prefix memo;
the bars, segment ends, last activity, compaction markers and whole prompts must agree, and so must the bars as the
bars frame encodes them: the warm side through the wire cache warm from the previous step's frame, where a held bar
edited in place would ship its old bytes, the cold side with no cache. The road's restore and demote steps are where
the prefix held from whole-parse turns meets restored ones, under hold keys that do not tell the two apart.

THE ROAD (the notes-api demo: web works, api is its peer). Appends to the open turn; the open turn closing with its
state row before and after the record; state rows inside a closed turn's idle span with the turn's end standing (an idle
notice that splits the span, then a row that moves the first span's end while the turn's atom count stands too, which
no hold key sees); captions for an old segment, the open one, a drifted id and an overwrite; the
goal store republished with the same seams; a seam splitting an old segment, then removed; a peer delivery whose sender
the postal log learns later; the leaf rewritten in place with one turn's prompt and then another's reply re-recorded
under new uuids, and replaced by a new file at the same path, each carrying a different message id under turn
identities that otherwise stand (what the hold key's two uuids and its file header are there for: without them the
held pairs would be served for the new records); auto and manual compaction; two turns with identical prompt text; a
store fault and its recovery; the captions file rewritten; a document written and a restart that restores from it; a
walk that raises partway through, over the restored turns; a demote; a late record whose time sorts into a closed turn;
a rewind cut and its release; a past-prompt edit; a resume into a new file; a /clear. A dropped echo's flag is
not a step here: echoes live in the live-merged session, which the message-caption scan never reads; it joins the road
with the first builder compared here that reads the merged session.

Two tests drive the same road. One compares warm with cold at every step and checks the steps changed what they claim
(so the equality is not a comparison of two empty maps). The other counts the turns the warm build walked at each step
(the spy on _segs_seam): one or two where only the open turn moved, every turn where the leaf or the seams moved; and,
for web's lane, one where only the open turn, the captions or the store's nodes moved (red before 2026-10-06, when a
caption row or a republish re-walked the whole lane), every turn where the leaf, the seams or a held turn's hold key
moved (a state row that moves only the end of a held turn's first idle span moves no key: one).

Synthetic data only: placeholder uuids, the web and api names, invented notes-api text, a private state root with
session hosts off."""
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
km = load_source("romp_kernel_build_road", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID_WEB = "b0ad0000-1005-4aaa-8bbb-00000000a001"   # private synthetic sids (CLAUDE.md, goal-store fixtures)
SID_API = "b0ad0000-1005-4aaa-8bbb-00000000a002"
RESUME_FILE = "b0ad0000-1005-4aaa-8bbb-00000000a003"   # the CLI session ids a resume and a /clear write their files under
CLEAR_FILE = "b0ad0000-1005-4aaa-8bbb-00000000a004"
NAMES = {SID_WEB: "web", SID_API: "api"}
REAL_SEGS_SEAM = km._segs_seam                     # the kernel's own segmentation, for the test's helpers (never counted)
ROAD_WIRE = ("roadBars", "turns")                  # the warm side's wire cache: a collection key of the road's own


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text):
    """A prompt the person typed."""
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def peer(t, uuid, parent, text, mid):
    """A delivered peer message: its marker makes the sender its author, and its id the segment's."""
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": "%s\n<!-- romp-msg-id: %s -->" % (text, mid)}}


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


def web_turn(i, t, parent):
    """Turn i of web's history and its last uuid. i % 3 == 0: a typed ask, answered. i % 3 == 1: api's question (its id
    m-pNN) with an inbox check whose result carries a second id (m-iNN). i % 3 == 2: a typed ask whose work runs on past a
    check whose second result carries an id (m-qNN): the segment a seam can split between the two checks (t + 30)."""
    u, a = "w-u%02d" % i, "w-a%02d" % i
    if i % 3 == 0:
        return [user(t, u, parent, "step %d: page the notes-api search results" % i),
                reply(t + 20, a, u, "Paged step %d." % i)], a
    if i % 3 == 1:
        c, r, tid = "w-c%02d" % i, "w-r%02d" % i, "toolu_w%02d" % i
        return [peer(t, u, parent, "api asks: is the index rebuilt for step %d?" % i, "m-p%02d" % i),
                tool_call(t + 10, c, u, tid),
                tool_out(t + 20, r, c, tid, "inbox: one message <!-- romp-msg-id: m-i%02d -->" % i),
                reply(t + 30, a, r, "Rebuilt for step %d; told api." % i)], a
    c, r, c2, r2 = "w-c%02d" % i, "w-r%02d" % i, "w-d%02d" % i, "w-e%02d" % i
    tid, tid2 = "toolu_w%02d" % i, "toolu_x%02d" % i
    return [user(t, u, parent, "check the retry budget for step %d" % i),
            tool_call(t + 10, c, u, tid),
            tool_out(t + 20, r, c, tid, "budget: 4096"),
            tool_call(t + 40, c2, r, tid2),
            tool_out(t + 50, r2, c2, tid2, "mail: <!-- romp-msg-id: m-q%02d -->" % i),
            reply(t + 60, a, r2, "Budget checked for step %d." % i)], a


class _Backend:
    """The two backend reads the scan's key and the parse take: the pending rewind cut and the owner bit."""

    def __init__(self):
        self.cut = {}

    def pending_cut(self, sid):
        return self.cut.get(sid, "")

    def owns(self, sid):
        return False


class RoadWorld(unittest.TestCase):
    """The two sessions under a private state root, every input the message-caption scan reads written by the test:
    the transcripts, the states logs, the captions, the goal stores and the postal log. No test of its own."""

    TURNS = 12                                     # web's closed turns at rest, its open turn after them

    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()   # the parse is handed the real path (the reader's and the assembly's keys)
        self.addCleanup(shutil.rmtree, self.td, True)
        self.saved = (jd.STATE, em._CKPT_DIR_FN, km._sessions, km._sdk, km._codex, km._segs_seam, jd._PENDING_CUT_FN,
                      dict(km._msg_sum_cache), dict(em._CKPT_CYCLE))
        jd._rebind_state(self.td / "state")
        for d in ("states", "checkpoints", "captions", "goals", "timeline"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")              # this state root is the test's own: no host spawns
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        em.checkpoint_cycle_begin(1 << 40)                            # a document write is never deferred by an earlier test's spend
        self.backend = _Backend()
        km._sdk = lambda: self.backend
        km._codex = lambda: None
        jd.set_pending_cut_provider(self.backend.pending_cut)
        km._msg_sum_cache.clear()
        self.saved_prefix = dict(km._lane_prefix_memo)              # the lane prefix memo: the road's own, put back after
        km._lane_prefix_memo.clear()
        km._delta_entry_memo.pop(ROAD_WIRE, None)
        self.walked, self.counting = 0, False
        self.lane_walked, self.lane_counting = 0, False

        def spy(turn, store):
            if self.counting:
                self.walked += 1
            if self.lane_counting:
                self.lane_walked += 1
            return REAL_SEGS_SEAM(turn, store)
        km._segs_seam = spy
        self.proj = self.td / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.leaf = {sid: str(self.proj / (sid + ".jsonl")) for sid in NAMES}
        self.recs = {sid: [] for sid in NAMES}                        # the records of each session's current leaf
        self.caps = {sid: [] for sid in NAMES}                        # the caption rows written, in order
        self.clock = int(time.time()) - 5000                          # every write's mtime, a second apart: no two share a stamp
        self.t0 = int(time.time()) - 6 * 3600
        km._sessions = lambda now: [{"sid": sid, "name": NAMES[sid], "path": self.leaf[sid],
                                     "mtime": os.path.getmtime(self.leaf[sid]) if os.path.exists(self.leaf[sid]) else 0}
                                    for sid in NAMES]
        self.fresh()

    def tearDown(self):
        self.fresh()
        (state, ckpt, km._sessions, km._sdk, km._codex, km._segs_seam, cut_fn, msc, cyc) = self.saved
        km._lane_prefix_memo.clear()
        km._lane_prefix_memo.update(self.saved_prefix)
        km._delta_entry_memo.pop(ROAD_WIRE, None)
        jd.set_pending_cut_provider(cut_fn)
        km._msg_sum_cache.clear()
        km._msg_sum_cache.update(msc)
        em.set_checkpoint_dir(ckpt)
        em._CKPT_CYCLE.clear()
        em._CKPT_CYCLE.update(cyc)
        jd._rebind_state(state)

    # ── the world's writes ──

    def fresh(self):
        """A kernel restart's in-memory side of the parse: no reader entry, assembly entry, stored parse or read memo.
        The scan's own memos are left as they stand (a test clears them when it means to)."""
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

    def rewrite(self, sid, recs, replace=False):
        """The leaf's records written over whole: in place (the same inode, as a write over the open file leaves it) or,
        with `replace`, as a new file moved over the path (a new inode, as an atomic save leaves it)."""
        body = "".join(json.dumps(r) + "\n" for r in recs)
        p = self.leaf[sid]
        if replace:
            with open(p + ".new", "w") as f:
                f.write(body)
            os.replace(p + ".new", p)
        else:
            with open(p, "w") as f:
                f.write(body)
        self.recs[sid] = list(recs)
        self.stamp(p)

    def state(self, sid, t, state):
        p = jd.STATE / "states" / (sid + ".jsonl")
        with open(p, "a") as f:
            f.write(json.dumps({"t": t, "state": state}) + "\n")
        self.stamp(p)

    def store(self, sid, seams=(), rev=1):
        p = jd.GOALDIR / (sid + ".json")
        p.write_text(json.dumps({"nodes": {}, "status": {}, "seams": list(seams), "rev": rev}))
        self.stamp(p)

    def caption_rows(self, sid, rows, rewrite=False):
        p = jd.CAPDIR / (sid + ".jsonl")
        if rewrite:
            self.caps[sid] = []
        with open(p, "w" if rewrite else "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        self.caps[sid] += rows
        self.stamp(p)

    def postal(self, mid, frm, to, t):
        p = jd.MESSAGES
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a") as f:
            f.write(json.dumps({"ev": "sent", "id": mid, "from_id": frm, "to_id": to, "t": t}) + "\n")
        self.stamp(p)

    # ── what the scan sees ──

    def tree(self, sid):
        return km._parse(self.leaf[sid], sid, time.time())

    def segs(self, sid):
        """[(turn index, segment)] of the session's current parse, seam-aware, as the scan segments it."""
        store = jd.load_goals_shared(sid)
        return [(i, s) for i, turn in enumerate(self.tree(sid)["turns"]) for s in REAL_SEGS_SEAM(turn, store)]

    def seg_of(self, sid, mid):
        """The segment whose atoms carry message id `mid` (the scan's own read of it)."""
        return next(s for _i, s in self.segs(sid) if mid in km._seg_mids(s))

    def caption_segments(self, sid, skip=()):
        """A work caption for every uncaptioned segment of the session's closed turns but those whose ids carry a
        message id in `skip`, as the captioner writes them: one row per segment id, grain segment, at its start."""
        have = {r["id"] for r in self.caps[sid]}
        turns = self.tree(sid)["turns"]
        rows = []
        for i, s in self.segs(sid):
            if i == len(turns) - 1 or s["id"] in have or set(km._seg_mids(s)) & set(skip):
                continue
            rows.append(cap_row(s, "%s: work at %d" % (NAMES[sid], s["t"])))
        if rows:
            self.caption_rows(sid, rows)

    def history(self):
        """Web's TURNS closed turns and an open one, api's two closed turns (one a question to web) and an open one, a
        states log per session, a work caption for every closed segment but web's turns 4 and 10 (captioned later on the
        road), and web's goal store with no seams."""
        parent = None
        for i in range(self.TURNS):
            t = self.t0 + 300 * i
            recs, parent = web_turn(i, t, parent)
            self.append(SID_WEB, recs)
            self.state(SID_WEB, t, "working")
            self.state(SID_WEB, t + 65, "waiting")
        t = self.t0 + 300 * self.TURNS
        self.append(SID_WEB, [user(t, "w-u%02d" % self.TURNS, parent, "ship step %d" % self.TURNS),
                              tool_call(t + 10, "w-c%02d" % self.TURNS, "w-u%02d" % self.TURNS, "toolu_w%02d" % self.TURNS)])
        self.state(SID_WEB, t, "working")
        self.web_t = t + 10
        ta = self.t0 + 100
        self.append(SID_API, [user(ta, "p-u00", None, "draft the notes-api schema"), reply(ta + 20, "p-a00", "p-u00", "Drafted."),
                              peer(ta + 300, "p-u01", "p-a00", "web says: the index is rebuilt", "m-w01"),
                              reply(ta + 320, "p-a01", "p-u01", "Thanks, noted."),
                              user(ta + 600, "p-u02", "p-a01", "route the cursor"),
                              tool_call(ta + 610, "p-c02", "p-u02", "toolu_p02")])
        self.state(SID_API, ta, "working")
        self.store(SID_WEB, rev=1)
        self.caption_segments(SID_WEB, skip=("m-p04", "m-p10"))
        self.caption_segments(SID_API)

    def last(self, sid):
        return next(r["uuid"] for r in reversed(self.recs[sid]) if r.get("uuid"))

    def turns(self, sid=SID_WEB):
        return len(self.tree(sid)["turns"])

    def tick(self, dt=10):
        self.web_t += dt
        return self.web_t

    # ── the comparison ──

    def builds(self):
        """(warm, cold): the message-caption map as the warm memos give it, then over the same inputs with the scan's
        memo (_msg_sum_cache) and the captions mapping (_CAPTION_ROWS_MEMO) cleared, so the cold side reads every
        captions file into a mapping of its own. The warm memos are put back after the cold build, so the next step
        builds on the warm chain."""
        self.walked, self.counting = 0, True
        try:
            warm = dict(km._msg_summaries())
        finally:
            self.counting = False
        saved, saved_caps = dict(km._msg_sum_cache), dict(km._CAPTION_ROWS_MEMO)
        km._msg_sum_cache.clear()
        km._CAPTION_ROWS_MEMO.clear()
        try:
            cold = dict(km._msg_summaries())
        finally:
            km._msg_sum_cache.clear()
            km._msg_sum_cache.update(saved)
            km._CAPTION_ROWS_MEMO.clear()
            km._CAPTION_ROWS_MEMO.update(saved_caps)
        return warm, cold

    def lane(self, sid):
        """One session's lane as the Sessions pane's bars build derives a live lane (_lane_memo's derivation): the leaf's
        head stat'd before the parse, the parse, the store's shared view, the captions mapping, the prefix memo as it
        stands. (bars, segment ends, last activity, compaction markers, whole prompts)."""
        head = km._msg_scan_head(self.leaf[sid])
        tree = self.tree(sid)
        caps = km._captions(sid)
        prompts = {}
        v = km._lane_segments(sid, tree, jd.load_goals_shared(sid), caps, True, None, prompts, head=head)
        return v[0], v[1], v[2], v[3], prompts

    def lane_builds(self):
        """(warm, cold, warm bytes, cold bytes) for the lanes: each session's lane derived with the prefix memo and the
        captions mapping as the last step left them (web's walks counted), then over the same inputs with both cleared
        (_lane_prefix_memo and _CAPTION_ROWS_MEMO, the warm memos put back after), and each side's bars split as the
        bars frame splits them, the warm side through the road's wire cache."""
        warm = {}
        for sid in NAMES:
            self.lane_walked, self.lane_counting = (0, True) if sid == SID_WEB else (self.lane_walked, False)
            try:
                warm[sid] = self.lane(sid)
            finally:
                self.lane_counting = False
        saved, saved_caps = dict(km._lane_prefix_memo), dict(km._CAPTION_ROWS_MEMO)
        km._lane_prefix_memo.clear()
        km._CAPTION_ROWS_MEMO.clear()
        try:
            cold = {sid: self.lane(sid) for sid in NAMES}
        finally:
            km._lane_prefix_memo.clear()
            km._lane_prefix_memo.update(saved)
            km._CAPTION_ROWS_MEMO.clear()
            km._CAPTION_ROWS_MEMO.update(saved_caps)

        def wire(lanes, key):
            ents, order = km._delta_split("dictlist:id", {sid: lanes[sid][0] for sid in NAMES}, memo_key=key)
            return order, {k: v[1] for k, v in ents.items()}
        return warm, cold, wire(warm, ROAD_WIRE), wire(cold, None)


def cap_row(seg, text, t=None, seg_id=None):
    return {"id": seg_id or seg["id"], "grain": "segment", "t": seg["t"] if t is None else t, "caption": text}


def drifted(seg_id, dt):
    """The id a judge's parse gives the same segment when its start drifts by dt seconds (the middle field)."""
    parts = seg_id.split(":")
    parts[-2] = str(int(parts[-2]) + dt)
    return ":".join(parts)


class BuildRoad(RoadWorld):
    """The road, step by step. `mode` "equal" asserts warm == cold after each step and what the step changed; "walks"
    asserts the turns the warm build walked."""

    def step(self, name, walks=None, whole=False, expect=None, lane=None):
        """One step's comparison. `lane` is web's lane walk where this module asserts one: a count (at most that many of
        its turns walked: the prefix held) or "whole" (every turn: the prefix was dropped or diverged)."""
        warm, cold = self.builds()
        lw, lc, ww, wc = self.lane_builds()
        if self.mode == "equal":
            self.assertEqual(warm, cold, "%s: the warm message-caption map is the cold one" % name)
            for mid, cap in (expect or {}).items():
                if cap is None:
                    self.assertNotIn(mid, warm, "%s: %s has no caption" % (name, mid))
                else:
                    self.assertEqual(warm.get(mid), cap, "%s: %s's caption" % (name, mid))
            for sid in NAMES:
                self.assertEqual(json.dumps(lw[sid][0]), json.dumps(lc[sid][0]), "%s: %s's warm bars are the cold ones" % (name, NAMES[sid]))
                self.assertEqual(lw[sid][1:], lc[sid][1:], "%s: %s's segment ends, last activity, markers and prompts" % (name, NAMES[sid]))
            self.assertEqual(ww, wc, "%s: the bars frame's bytes through the warm wire cache are a cold encoding's" % name)
            self.assertTrue(lw[SID_WEB][0], "%s: web's lane has bars" % name)
        else:
            n = len(self.tree(SID_WEB)["turns"])
            if whole:
                self.assertEqual(self.walked, n, "%s: every turn of web's %d walked once" % (name, n))
            elif walks is not None:
                self.assertLessEqual(self.walked, walks, "%s: at most %d of web's %d turns walked" % (name, walks, n))
            if lane == "whole":
                self.assertEqual(self.lane_walked, n, "%s: every turn of web's lane walked once" % name)
            elif lane is not None:
                self.assertLessEqual(self.lane_walked, lane, "%s: at most %d of web's %d turns walked for its lane" % (name, lane, n))
        return warm

    def cap_of(self, sid, mid):
        """The caption the scan should join to `mid`: its segment's exact row, the last written."""
        seg = self.seg_of(sid, mid)
        return next((r["caption"] for r in reversed(self.caps[sid]) if r["id"] == seg["id"]), None)

    def road(self):
        self.history()
        w = self.step("the history at rest", expect={"m-p01": self.cap_of(SID_WEB, "m-p01"), "m-i01": self.cap_of(SID_WEB, "m-i01"),
                                                     "m-q02": self.cap_of(SID_WEB, "m-q02"), "m-w01": self.cap_of(SID_API, "m-w01"),
                                                     "m-p04": None, "m-p10": None})
        if self.mode == "equal":
            self.assertGreaterEqual(len(w), 9, "the road's map is not empty: %r" % sorted(w))

        # the open turn grows, then closes and another opens
        n = self.TURNS
        self.append(SID_WEB, [tool_out(self.tick(), "w-r%02d" % n, "w-c%02d" % n, "toolu_w%02d" % n, "tests: 41 passed")])
        self.step("an append to the open turn", walks=1, lane=1)
        self.state(SID_WEB, self.web_t + 40, "waiting")
        self.step("the open turn's state row before its last record", walks=1, lane=1)
        self.append(SID_WEB, [reply(self.tick(20), "w-a%02d" % n, "w-r%02d" % n, "Shipped step %d." % n)])
        self.step("the open turn's last record after its state row", walks=1, lane=1)
        t = self.tick(300)
        self.append(SID_WEB, [peer(t, "w-u%02d" % (n + 1), "w-a%02d" % n, "api asks: page by cursor next?", "m-p13")])
        self.step("a new turn opens before its state row", walks=2)
        self.state(SID_WEB, t, "working")
        self.step("the new turn's state row after its record", walks=2)

        # state rows inside a closed turn's idle span, the turn's end standing (the post-merge review of the lane prefix's
        # inputs by value, 2026-10-06): the idle notice after a Stop splits the span in two, one more atom, which the hold
        # key sees; then a row inside the first span moves that span's end while the turn's end and atom count stand,
        # which no hold key sees, and the held bars must still be a cold derivation's
        t6 = self.t0 + 300 * 6                         # web's turn 6: a typed ask, its Stop at t6 + 65, turn 7 at t6 + 300
        self.state(SID_WEB, t6 + 125, "idle")
        self.step("an idle notice inside a closed turn's idle span, the turn's end standing",
                  walks=self.turns() - 6, lane="whole")
        self.state(SID_WEB, t6 + 95, "working")
        self.step("a state row moving a closed turn's first idle end, its end and atom count standing", walks=1, lane=1)

        # the judges write: captions, a republish, a seam and its removal
        s4, s10, s13 = self.seg_of(SID_WEB, "m-p04"), self.seg_of(SID_WEB, "m-p10"), self.seg_of(SID_WEB, "m-p13")
        s1 = self.seg_of(SID_WEB, "m-p01")
        self.caption_rows(SID_WEB, [cap_row(s4, "web: rebuilt the index for step 4"),
                                    cap_row(s13, "web: weighing cursor paging"),
                                    cap_row(s10, "web: rebuilt it for step 10", t=s10["t"] + 3, seg_id=drifted(s10["id"], 3)),
                                    cap_row(s1, "web: rebuilt the index, told api")])
        self.step("captions for an old segment, the open one, a drifted id and an overwrite", walks=1, lane=1,
                  expect={"m-p04": "web: rebuilt the index for step 4", "m-i04": "web: rebuilt the index for step 4",
                          "m-p13": "web: weighing cursor paging", "m-p10": "web: rebuilt it for step 10",
                          "m-p01": "web: rebuilt the index, told api"})
        self.store(SID_WEB, rev=2)
        self.step("the goal store republished with the same seams", walks=1, lane=1)
        s2 = self.seg_of(SID_WEB, "m-q02")
        seam = {"segs": [jd._seg_key(s2["id"])], "t": s2["t"] + 30, "top": "w-g1", "text": "Back off the retries"}
        self.store(SID_WEB, seams=[seam], rev=3)
        self.step("a seam splits an old segment", whole=True, lane="whole", expect={"m-q02": None})
        tail = self.seg_of(SID_WEB, "m-q02")
        if self.mode == "equal":
            self.assertNotEqual(tail["id"], s2["id"], "the premise: the seam moved m-q02 into the tail segment")
        self.caption_rows(SID_WEB, [cap_row(tail, "web: kept checking past the settle")])
        self.step("the seam's tail captioned", walks=1, lane=1, expect={"m-q02": "web: kept checking past the settle"})
        self.store(SID_WEB, seams=[], rev=4)
        self.step("the seam removed", whole=True, lane="whole", expect={"m-q02": self.cap_of(SID_WEB, "m-q02")})
        self.postal("m-p13", SID_API, SID_WEB, self.web_t)
        self.step("a peer delivery whose sender the postal log learns later", walks=0, lane=1)

        # the leaf rewritten under turn identities that stand: the uuids and the file header tell the records apart
        recs = [dict(r) for r in self.recs[SID_WEB]]
        for r in recs:
            if r.get("uuid") == "w-u04":
                r["uuid"] = "w-x04"                    # the prompt re-recorded: same text, same time, a new uuid
            if r.get("parentUuid") == "w-u04":
                r["parentUuid"] = "w-x04"
            if r.get("uuid") == "w-r04":
                r["message"] = json.loads(json.dumps(r["message"]).replace("m-i04", "m-j04"))
        self.rewrite(SID_WEB, recs)
        self.step("the leaf rewritten in place, one turn's prompt under a new uuid", walks=self.turns() - 4, lane="whole",
                  expect={"m-j04": "web: rebuilt the index for step 4", "m-i04": None})
        recs = [dict(r) for r in self.recs[SID_WEB]]
        for r in recs:
            if r.get("uuid") == "w-a07":
                r["uuid"] = "w-y07"                    # the reply re-recorded: same text, same time, a new uuid
                r["message"] = dict(r["message"], id="msg_w-y07")
            if r.get("parentUuid") == "w-a07":
                r["parentUuid"] = "w-y07"
            if r.get("uuid") == "w-r07":
                r["message"] = json.loads(json.dumps(r["message"]).replace("m-i07", "m-k07"))
        self.rewrite(SID_WEB, recs)
        self.step("the leaf rewritten in place, one turn's reply under a new uuid", walks=self.turns() - 7, lane="whole",
                  expect={"m-k07": self.cap_of(SID_WEB, "m-k07"), "m-i07": None})
        ino = os.stat(self.leaf[SID_WEB]).st_ino
        recs = [dict(r) for r in self.recs[SID_WEB]]
        for r in recs:
            if r.get("uuid") == "w-r01":
                r["message"] = json.loads(json.dumps(r["message"]).replace("m-i01", "m-z01"))
        self.rewrite(SID_WEB, recs, replace=True)
        if self.mode == "equal":
            self.assertNotEqual(os.stat(self.leaf[SID_WEB]).st_ino, ino, "the premise: a new file under the path")
        self.step("the leaf replaced by a new file, every turn identity standing", whole=True, lane="whole",
                  expect={"m-z01": "web: rebuilt the index, told api", "m-i01": None})

        # compactions and repeated prompt text
        t = self.tick(30)
        self.append(SID_WEB, [reply(t, "w-a%02d" % (n + 1), "w-u%02d" % (n + 1), "Cursor paging it is.")])
        self.state(SID_WEB, t + 5, "waiting")
        recs, s = compaction(self.tick(300), "1", self.last(SID_WEB), "auto")
        t = self.tick(10)
        self.append(SID_WEB, recs + [user(t, "w-u%02d" % (n + 2), s, "after the compaction: what remains of the search?"),
                                     reply(t + 20, "w-a%02d" % (n + 2), "w-u%02d" % (n + 2), "The cursor and the cap remain.")])
        self.state(SID_WEB, t, "working")
        self.step("an auto compaction", walks=4)
        recs, s = compaction(self.tick(300), "2", self.last(SID_WEB), "manual")
        t = self.tick(10)
        self.append(SID_WEB, recs + [user(t, "w-u%02d" % (n + 3), s, "after the second compaction: close out the cap")])
        self.step("a manual compaction", walks=4)
        self.append(SID_WEB, [reply(self.tick(20), "w-a%02d" % (n + 3), "w-u%02d" % (n + 3), "Closed out the cap.")])
        for k, mid in ((4, "m-c01"), (5, "m-c02")):
            t = self.tick(300)
            u, c, r, a = "w-u%02d" % (n + k), "w-c%02d" % (n + k), "w-r%02d" % (n + k), "w-a%02d" % (n + k)
            self.append(SID_WEB, [user(t, u, self.last(SID_WEB), "continue"), tool_call(t + 10, c, u, "toolu_c%d" % k),
                                  tool_out(t + 20, r, c, "toolu_c%d" % k, "inbox: <!-- romp-msg-id: %s -->" % mid),
                                  reply(t + 30, a, r, "Continued.")])
        first = self.seg_of(SID_WEB, "m-c01")
        self.caption_rows(SID_WEB, [cap_row(first, "web: continued the cap work")])
        self.step("two turns with identical prompt text, the first captioned", walks=3,
                  expect={"m-c01": "web: continued the cap work", "m-c02": "web: continued the cap work"})
        self.caption_rows(SID_WEB, [cap_row(self.seg_of(SID_WEB, "m-c02"), "web: continued it again")])
        self.step("the second captioned under its own id", walks=1, lane=1,
                  expect={"m-c01": "web: continued the cap work", "m-c02": "web: continued it again"})

        # a store fault and its recovery: the walk raises, nothing is stored, the last-known map is served
        before, held = dict(km._msg_summaries()), (km._msg_sum_cache.get("held") or {}).get(SID_WEB)
        t = self.tick(300)
        self.append(SID_WEB, [user(t, "w-u%02d" % (n + 7), self.last(SID_WEB), "page the archive")])
        with mock.patch.object(jd, "load_goals_shared", side_effect=OSError("a synthetic store fault")):
            faulted = dict(km._msg_summaries())
        if self.mode == "equal":
            self.assertEqual(faulted, before, "the store fault: the last-known map is served")
            self.assertIs((km._msg_sum_cache.get("held") or {}).get(SID_WEB), held, "the store fault: nothing was stored")
        self.append(SID_WEB, [tool_call(self.tick(), "w-c%02d" % (n + 7), "w-u%02d" % (n + 7), "toolu_w%02d" % (n + 7))])
        self.step("the store readable again", walks=2)

        # the captions file rewritten: a row dropped, one changed
        rows = [dict(r) for r in self.caps[SID_WEB] if r["id"] != s4["id"]]
        for r in rows:
            if r["id"] == s13["id"]:
                r["caption"] = "web: paged by cursor"
        self.caption_rows(SID_WEB, rows, rewrite=True)
        self.step("the captions file rewritten", walks=1, lane=1,
                  expect={"m-p04": None, "m-i04": None, "m-j04": None, "m-p13": "web: paged by cursor"})

        # a document, a restart that restores from it, a walk that raises over the restored turns, a demote
        leaf = self.leaf[SID_WEB]
        self.assertTrue(em.asm_checkpoint_write(leaf, SID_WEB, sdk_human=bool(km._display_sdk_human(SID_WEB)), tree=self.tree(SID_WEB)),
                        "the document is written: %r" % em.asm_checkpoint_stats().get("skipped"))
        self.fresh()
        self.append(SID_WEB, [tool_out(self.tick(), "w-r%02d" % (n + 7), "w-c%02d" % (n + 7), "toolu_w%02d" % (n + 7), "archive: 3 pages")])
        self.step("a restart that restores from the document", walks=2)
        self.assertEqual(km._parse_mode.get(leaf), "restore", "the premise: the parse took the restore road")
        held = (km._msg_sum_cache.get("held") or {}).get(SID_WEB)
        before = dict(km._msg_summaries())
        self.store(SID_WEB, seams=[seam], rev=5)       # the seams move: every turn is walked, the third one raises
        calls = []

        def raising(turn, store):
            calls.append(1)
            if len(calls) == 3:
                raise em.LazyBodyRead("a synthetic body read fault, partway through the walk")
            return REAL_SEGS_SEAM(turn, store)
        with mock.patch.object(km, "_segs_seam", raising):
            faulted = dict(km._msg_summaries())
        if self.mode == "equal":
            self.assertEqual(len(calls), 3, "the premise: the walk raised at its third turn")
            self.assertEqual(faulted, before, "a walk that raised: the last-known map is served")
            self.assertIs((km._msg_sum_cache.get("held") or {}).get(SID_WEB), held, "a walk that raised stored nothing")
        self.append(SID_WEB, [reply(self.tick(), "w-a%02d" % (n + 7), "w-r%02d" % (n + 7), "Archive paged.")])
        self.step("the walk again with nothing raising, over the restored turns", whole=True, lane="whole",
                  expect={"m-q02": "web: kept checking past the settle"})
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(leaf, None)
        em._read_jsonl_entry(leaf, tail_ok=False)     # a from-zero read: a fresh generation, the next parse demotes
        t = self.tick(300)
        self.append(SID_WEB, [user(t, "w-u%02d" % (n + 8), self.last(SID_WEB), "and the cap?")])
        self.step("a demote of the restored entry", walks=2)

        # a late record, after the document (a document is written from a tree whose turns hold its pre-cut records in
        # order; a record stamped into a pre-cut turn is the restore road's own case, not this step's): whichever road the
        # parse takes, the scan reads the tree it serves
        late_t = self.t0 + 300 * 5 + 25                # inside turn 5, closed hours ago
        self.append(SID_WEB, [reply(late_t, "w-l05", self.last(SID_WEB), "noted <!-- romp-msg-id: m-l05 -->")])
        self.step("a late record whose time sorts into a closed turn", expect={"m-l05": self.cap_of(SID_WEB, "m-l05")})

        # a rewind cut and its release; a past-prompt edit
        self.backend.cut[SID_WEB] = "w-a08"            # the cut world ends at turn 8 (and leaves out the late record)
        self.step("a rewind cut armed at an old reply", expect={"m-p13": None, "m-p01": "web: rebuilt the index, told api"})
        self.backend.cut[SID_WEB] = ""
        self.step("the cut released", expect={"m-p13": "web: paged by cursor"})
        t = self.tick(300)
        self.append(SID_WEB, [user(t, "w-v03", "w-a03", "edit: page by offset instead"), reply(t + 20, "w-v03a", "w-v03", "Paging by offset.")])
        self.step("a past prompt edited: a new branch from turn 3", expect={"m-p13": None, "m-p01": "web: rebuilt the index, told api"})

        # a resume into a new file, then a /clear
        res = str(self.proj / (RESUME_FILE + ".jsonl"))
        t = self.tick(300)
        with open(res, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in [
                peer(t, "w-res1", self.last(SID_WEB), "api asks: resumed, where were we?", "m-r01"),
                reply(t + 20, "w-res2", "w-res1", "On the offset paging."),
                user(t + 300, "w-res3", "w-res2", "then the cursor")]))
        self.stamp(res)
        self.leaf[SID_WEB], self.recs[SID_WEB] = res, []
        self.caption_segments(SID_WEB)
        self.step("a resume into a new file", whole=True, lane="whole", expect={"m-r01": self.cap_of(SID_WEB, "m-r01"),
                                                                 "m-p01": "web: rebuilt the index, told api"})
        clr = str(self.proj / (CLEAR_FILE + ".jsonl"))
        t = self.tick(300)
        with open(clr, "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in [
                peer(t, "w-clr1", None, "api asks: fresh start on the schema?", "m-f01"),
                reply(t + 20, "w-clr2", "w-clr1", "Starting fresh."),
                user(t + 300, "w-clr3", "w-clr2", "then the schema's indexes")]))
        self.stamp(clr)
        self.leaf[SID_WEB], self.recs[SID_WEB] = clr, []
        self.caption_segments(SID_WEB)
        self.step("a /clear", whole=True, lane="whole", expect={"m-f01": self.cap_of(SID_WEB, "m-f01"), "m-p01": None, "m-r01": None})

    def test_after_every_step_the_warm_map_equals_a_cold_one(self):
        self.mode = "equal"
        self.road()

    def test_where_only_the_open_turn_moved_the_closed_turns_are_not_walked_again(self):
        self.mode = "walks"
        self.road()


if __name__ == "__main__":
    unittest.main()
