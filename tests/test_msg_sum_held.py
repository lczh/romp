#!/usr/bin/env python3
"""The message-caption scan holds each closed turn's message ids (2026-10-05).

THE PROBLEM. A peer message's caption on the timeline and in the chat is the caption of the segment that received it:
the kernel joins each message id to its segment and the segment to its row in captions/<sid>.jsonl. The scan that
builds that map (_msg_sum_scan_session) re-segmented every turn of a session and read every atom's ids whenever the
transcript, the captions file or the goal store moved, so a working session paid for its whole history on every
append, and every caption a judge wrote paid it again. Now each closed turn's (segment id, message ids) pairs are held
between scans and the captions are joined fresh: an append walks the open turn alone, a turn boundary the turn that
closed and the one that opened, a caption row only the open turn.

Pinned here: the walk counts (the spy on _segs_seam, the scan's one segmentation per turn; the first test is the
red-first case, 31 turns walked on every append before this change); the map equal to the one-pass loop it replaced,
written out below as the oracle; a departed session's records dropped with its submap; a walk that raises storing
nothing; the byte bound shedding the least recently scanned session first; the hold key and the seams' fingerprint;
the held records holding strings and tuples only; the /perf block. tests/test_build_road_equivalence.py drives the
road of history events through warm and cold builds.

From the review that came after the merge (2026-10-06): a rewrite that leaves fewer closed turns than the entry held
counts under partial; an entry whose seams moved is dropped at the comparison, before its walk, so a walk that then
raises leaves no dead entry to shed valid ones; a fault outside the walk counts under faulted and is said on stderr
once per episode; an entry over the bound by itself counts under overBound, not evict; the leaf's head is stat'd
before the parse (a leaf replaced right after the parse returns); the join's order (an id in two turns takes the later
turn's caption); the reference names every key of the block.

Synthetic data only: placeholder uuids, the web, api and tests names, invented notes-api text, a private state root
with session hosts off."""
import contextlib
import io
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
km = load_source("romp_kernel_msg_sum_held", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID_WEB = "b0ad0000-1005-4aaa-8bbb-00000000b001"   # private synthetic sids (CLAUDE.md, goal-store fixtures)
SID_API = "b0ad0000-1005-4aaa-8bbb-00000000b002"
SID_TESTS = "b0ad0000-1005-4aaa-8bbb-00000000b003"
NAMES = {SID_WEB: "web", SID_API: "api", SID_TESTS: "tests"}   # a session is discovered only once its leaf exists
REAL_SEGS_SEAM = km._segs_seam


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "message": {"role": "user", "content": text}}


def peer(t, uuid, parent, text, mid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": "%s\n<!-- romp-msg-id: %s -->" % (text, mid)}}


def reply(t, uuid, parent, text):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"id": "msg_" + uuid, "role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


def tool_call(t, uuid, parent, tid):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"id": "msg_" + uuid, "role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "make test"}}]}}


def tool_out(t, uuid, parent, tid, content):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}


def one_pass(sid, path, now):
    """The scan as it stood before the hold (2026-10-05), the oracle: every turn segmented, a captioned segment's ids
    each given its caption, in turn and segment order."""
    caps = km._captions(sid)
    if not caps:
        return {}
    sub = {}
    store = jd.load_goals_shared(sid)
    for turn in km._parse(path, sid, now)["turns"]:
        for seg in REAL_SEGS_SEAM(turn, store):
            cap = km._seg_work_caption(caps, seg["id"])
            if not cap:
                continue
            for mid in km._seg_mids(seg):
                sub[mid] = cap
    return sub


class _Backend:
    def pending_cut(self, sid):
        return ""

    def owns(self, sid):
        return False


class MsgScanHold(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.td, True)
        self.saved = (jd.STATE, km._sessions, km._sdk, km._codex, km._segs_seam, dict(km._msg_sum_cache))
        jd._rebind_state(self.td / "state")
        for d in ("states", "captions", "goals"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        (jd.STATE / "session-hosts").write_text("off\n")              # this state root is the test's own: no host spawns
        km._sdk = lambda: _Backend()
        km._codex = lambda: None
        km._msg_sum_cache.clear()
        self.walked, self.counting = 0, False

        def spy(turn, store):
            if self.counting:
                self.walked += 1
            return REAL_SEGS_SEAM(turn, store)
        km._segs_seam = spy
        proj = self.td / "projects" / "-w-notes-api"
        proj.mkdir(parents=True)
        self.leaf = {sid: str(proj / (sid + ".jsonl")) for sid in NAMES}
        self.live = list(NAMES)
        self.open_t = {}                                              # each session's open-turn time
        self.clock = int(time.time()) - 5000
        self.t = int(time.time()) - 5 * 3600
        km._sessions = lambda now: [{"sid": sid, "name": NAMES[sid], "path": self.leaf[sid], "mtime": self.clock}
                                    for sid in self.live if os.path.exists(self.leaf[sid])]

    def tearDown(self):
        (state, km._sessions, km._sdk, km._codex, km._segs_seam, msc) = self.saved
        km._msg_sum_cache.clear()
        km._msg_sum_cache.update(msc)
        jd._rebind_state(state)

    def stamp(self, path):
        self.clock += 1
        os.utime(path, (self.clock, self.clock))

    def append(self, sid, recs):
        with open(self.leaf[sid], "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        self.last = recs[-1]["uuid"]
        self.stamp(self.leaf[sid])

    def session(self, sid, closed):
        """`closed` closed turns, odd ones api's questions (id m-pNN), then an open turn: a typed ask and a tool call."""
        parent = None
        for i in range(closed):
            u, a, t = "%s-u%02d" % (NAMES[sid], i), "%s-a%02d" % (NAMES[sid], i), self.t + 120 * i
            ask = (peer(t, u, parent, "api asks: is page %d indexed?" % i, "m-%s%02d" % (NAMES[sid], i)) if i % 2
                   else user(t, u, parent, "page %d of the notes-api index" % i))
            self.append(sid, [ask, reply(t + 20, a, u, "Paged %d." % i)])
            parent = a
        t = self.t + 120 * closed
        self.append(sid, [user(t, "%s-u%02d" % (NAMES[sid], closed), parent, "ship it"),
                          tool_call(t + 10, "%s-c%02d" % (NAMES[sid], closed), "%s-u%02d" % (NAMES[sid], closed), "toolu_1")])
        self.open_t[sid] = t + 10
        self.caption_closed(sid)

    def segs(self, sid):
        store = jd.load_goals_shared(sid)
        return [(i, s) for i, turn in enumerate(km._parse(self.leaf[sid], sid, time.time())["turns"])
                for s in REAL_SEGS_SEAM(turn, store)]

    def caption_closed(self, sid):
        segs = self.segs(sid)
        last = max(i for i, _s in segs)
        rows = [{"id": s["id"], "grain": "segment", "t": s["t"], "caption": "%s: work at %d" % (NAMES[sid], s["t"])}
                for i, s in segs if i < last]
        self.caption(sid, rows)

    def caption(self, sid, rows):
        p = jd.CAPDIR / (sid + ".jsonl")
        with open(p, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in rows))
        self.stamp(p)

    def build(self):
        self.walked, self.counting = 0, True
        try:
            return dict(km._msg_summaries())
        finally:
            self.counting = False

    def held(self, sid=SID_WEB):
        return (km._msg_sum_cache.get("held") or {}).get(sid)

    def test_an_append_to_a_31_turn_session_walks_its_open_turn_alone(self):
        """The red-first case: before the hold every append re-segmented all 31 turns."""
        self.session(SID_WEB, 30)
        first = self.build()
        self.assertEqual(self.walked, 31, "the first scan walks every turn")
        self.assertIn("m-web01", first, "the premise: the map holds a captioned peer message")
        self.append(SID_WEB, [tool_out(self.open_t[SID_WEB] + 10, "web-r30", "web-c30", "toolu_1", "tests: 41 passed")])
        self.assertEqual(self.build(), first)
        self.assertEqual(self.walked, 1, "an append to the open turn walks the open turn alone (31 before the hold)")

    def test_a_turn_boundary_walks_the_turn_that_closed_and_the_one_that_opened(self):
        self.session(SID_WEB, 30)
        self.build()
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [reply(t + 20, "web-a30", "web-c30", "Shipped."),
                              peer(t + 300, "web-u31", "web-a30", "api asks: and the cursor?", "m-web31")])
        self.build()
        self.assertEqual(self.walked, 2)
        self.assertEqual(km._msg_sum_scan_session(SID_WEB, self.leaf[SID_WEB], time.time()),
                         one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))

    def test_a_caption_row_alone_walks_the_open_turn_and_is_joined_fresh(self):
        self.session(SID_WEB, 30)
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [reply(t + 20, "web-a30", "web-c30", "Shipped."),
                              peer(t + 300, "web-u31", "web-a30", "api asks: and the cursor?", "m-web31")])
        self.build()
        seg = next(s for _i, s in self.segs(SID_WEB) if "m-web31" in km._seg_mids(s))
        old = next(s for _i, s in self.segs(SID_WEB) if "m-web01" in km._seg_mids(s))
        self.caption(SID_WEB, [{"id": seg["id"], "grain": "segment", "t": seg["t"], "caption": "web: weighing the cursor"},
                               {"id": old["id"], "grain": "segment", "t": old["t"], "caption": "web: paged it, told api"}])
        got = self.build()
        self.assertEqual(self.walked, 1, "a caption row walks no closed turn")
        self.assertEqual(got.get("m-web31"), "web: weighing the cursor", "the open segment's new caption is joined")
        self.assertEqual(got.get("m-web01"), "web: paged it, told api", "an old segment's overwritten caption is joined")
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()), "the one-pass loop's map")

    def test_the_map_equals_the_one_pass_loop_with_the_hold_warm(self):
        self.session(SID_WEB, 12)
        self.session(SID_API, 5)
        for k in range(4):
            self.build()
            self.append(SID_WEB, [tool_out(self.open_t[SID_WEB] + 10 * (k + 1), "web-r%d" % k, "web-c12" if k == 0 else "web-r%d" % (k - 1),
                                           "toolu_1", "inbox: <!-- romp-msg-id: m-x%d -->" % k)])
            for sid in (SID_WEB, SID_API):
                self.assertEqual(km._msg_sum_scan_session(sid, self.leaf[sid], time.time()),
                                 one_pass(sid, self.leaf[sid], time.time()), "%s after %d appends" % (NAMES[sid], k + 1))

    def test_a_departed_session_takes_its_held_records_with_it(self):
        self.session(SID_WEB, 4)
        self.session(SID_API, 4)
        self.build()
        self.assertIsNotNone(self.held(SID_API), "the premise: api's closed turns are held")
        self.live = [SID_WEB]
        self.build()
        self.assertIsNone(self.held(SID_API), "api left discovery: its held records go with its submap")
        self.assertNotIn(SID_API, km._msg_sum_cache["per"])
        self.assertIsNotNone(self.held(SID_WEB))

    def test_a_walk_that_raises_stores_nothing_and_is_counted(self):
        self.session(SID_WEB, 8)
        first = self.build()
        held, s0 = self.held(), km._msg_scan_report()
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [reply(t + 20, "web-a08", "web-c08", "Shipped."),
                              peer(t + 300, "web-u09", "web-a08", "api asks: and the cursor?", "m-web09")])

        def raising(turn, store):
            raise em.LazyBodyRead("a synthetic body read fault")
        with mock.patch.object(km, "_segs_seam", raising):
            self.assertEqual(self.build(), first, "the last-known map is served")
        self.assertIs(self.held(), held, "the walk that raised stored nothing")
        rep = km._msg_scan_report()
        self.assertEqual(rep["raised"], s0["raised"] + 1)
        self.assertEqual(rep["faulted"], s0["faulted"] + 1, "a walk's raise is a faulted scan too")
        self.assertEqual(rep["scans"], s0["scans"], "a scan whose walk raised did not finish: scans does not move")
        self.append(SID_WEB, [tool_call(t + 310, "web-c09", "web-u09", "toolu_2")])
        got = self.build()
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))
        self.assertEqual(self.walked, 2, "the held prefix still stood: the two turns past it walked")

    def test_the_byte_bound_sheds_the_least_recently_scanned_session_first(self):
        self.session(SID_WEB, 6)
        self.session(SID_API, 6)
        self.build()
        sizes = {sid: self.held(sid)[4] for sid in (SID_WEB, SID_API)}
        self.assertTrue(all(n > 0 for n in sizes.values()), sizes)
        bound = max(sizes.values()) + min(sizes.values()) - 1          # room for one entry, never two
        evict = km._msg_scan_report()["evict"]
        with mock.patch.object(km, "MSG_SCAN_HELD_BYTES", bound):
            self.append(SID_API, [tool_out(self.open_t[SID_API] + 10, "api-r06", "api-c06", "toolu_1", "ok")])
            self.build()                                               # api scanned last: web, the colder, is shed
            rep = km._msg_scan_report()
        self.assertIsNone(self.held(SID_WEB))
        self.assertIsNotNone(self.held(SID_API))
        self.assertEqual(rep["evict"], evict + 1)
        self.assertLessEqual(rep["bytes"], bound)
        self.assertEqual(rep["bound"], bound)
        with mock.patch.object(km, "MSG_SCAN_HELD_BYTES", 1):          # an entry over the bound alone is not held either
            self.append(SID_API, [tool_out(self.open_t[SID_API] + 20, "api-r07", "api-r06", "toolu_1", "ok")])
            self.build()
        self.assertIsNone(self.held(SID_API))

    def test_the_bound_is_a_fraction_of_memory_with_an_override(self):
        with mock.patch.dict(os.environ, {"ROMP_MSG_SCAN_HELD_BYTES": "12345"}):
            self.assertEqual(km._msg_scan_held_bound(), 12345)
        for bad in ("", "0", "-5", "lots"):
            with mock.patch.dict(os.environ, {"ROMP_MSG_SCAN_HELD_BYTES": bad}):
                self.assertEqual(km._msg_scan_held_bound(), km._mem_total_bytes() // 256, bad)

    def test_the_hold_key_extends_the_prefix_key_with_the_trigger_and_the_newest_uuid(self):
        turn = {"id": SID_WEB + ":100:abcd1234", "ended": True, "end": 160, "trigger": {"uuid": "u1"},
                "atoms": [{"uuid": "u1", "t": 100}, {"uuid": "a1", "t": 120}, {"type": "idle", "uuid": None, "t": 130}]}
        key = km._turn_hold_key(turn)
        self.assertEqual(key[:4], km._turn_prefix_key(turn))
        self.assertEqual(key[4:], ("u1", "a1"), "the trigger's uuid and the newest uuid-bearing atom's (an idle span has none)")
        self.assertNotEqual(km._turn_hold_key(dict(turn, trigger={"uuid": "u2"})), key)
        self.assertNotEqual(km._turn_hold_key(dict(turn, atoms=turn["atoms"][:1] + [{"uuid": "a2", "t": 120}] + turn["atoms"][2:])), key)
        self.assertEqual(km._turn_hold_key(dict(turn, trigger=None))[4], None)

        class Unbuilt(list):
            """A restored turn's atoms: building one is what the key must not do."""
            def __getitem__(self, i):
                raise AssertionError("an atom was built")

            def __iter__(self):
                raise AssertionError("an atom was built")

            def __reversed__(self):
                raise AssertionError("an atom was built")
        pre = {"id": turn["id"], "ended": True, "end": 160, "trigger": {"uuid": "u1"}, "pre": True,
               "uuids": ["u1", "a1", None], "atoms": Unbuilt([None, None, None])}
        self.assertEqual(km._turn_hold_key(pre), key, "a restored turn's key, from its uuids, equals the whole parse's")

    def test_the_seams_fingerprint_compares_by_value_what_apply_seams_reads(self):
        seam = {"segs": [SID_WEB + ":abcd1234"], "t": 130, "top": "g1", "text": "Back off the retries"}
        store = {"nodes": {"g1": {"text": "x"}}, "status": {}, "seams": [seam]}
        fp = km._seams_fp(store)
        self.assertEqual(km._seams_fp(json.loads(json.dumps(store))), fp, "another object, the same seams")
        self.assertEqual(km._seams_fp(jd._freeze_store(json.loads(json.dumps(store)))), fp, "the shared frozen view")
        self.assertEqual(km._seams_fp(dict(store, nodes={}, status={"g1": "done"}, rev=9)), fp, "nodes and status are not read")
        self.assertEqual(km._seams_fp(dict(store, seams=[seam, {"t": 5}, "junk", {"segs": [], "t": 7}])), fp,
                         "a seam apply_seams skips (not a dict, no segs) is skipped")
        for k, v in (("segs", [SID_WEB + ":ffff0000"]), ("t", 131), ("top", "g2"), ("text", "Retry later")):
            self.assertNotEqual(km._seams_fp(dict(store, seams=[dict(seam, **{k: v})])), fp, k)
        self.assertNotEqual(km._seams_fp(dict(store, seams=[dict(seam, segs={SID_WEB + ":abcd1234": 1})])), fp,
                            "a dict of keys is told from a list of them")
        self.assertEqual(km._seams_fp(None), ())
        self.assertEqual(km._seams_fp({"nodes": {}}), ())

    def test_the_held_records_are_strings_and_tuples(self):
        self.session(SID_WEB, 6)
        self.build()
        head, fp, keys, recs, size = self.held()
        self.assertEqual(head, (self.leaf[SID_WEB], os.stat(self.leaf[SID_WEB]).st_ino))
        self.assertEqual(len(keys), 6)
        self.assertEqual(len(recs), 6)
        self.assertTrue(any(recs), "the premise: some closed turn carries ids")
        for r in recs:
            self.assertIsInstance(r, tuple)
            for seg_id, mids in r:
                self.assertIsInstance(seg_id, str)
                self.assertIsInstance(mids, tuple)
                self.assertTrue(all(isinstance(m, str) for m in mids))
        self.assertGreater(size, 0)

    def test_the_perf_block_reports_the_hold(self):
        self.session(SID_WEB, 6)
        s0 = km._msg_scan_report()
        self.build()
        self.append(SID_WEB, [tool_out(self.open_t[SID_WEB] + 10, "web-r06", "web-c06", "toolu_1", "ok")])
        self.build()
        snap = km._PERF_STATS.snapshot()["memos"]["msgScan"]
        self.assertEqual(set(snap), {"scans", "heldTurns", "walkedTurns", "partial", "reset", "raised", "faulted", "evict",
                                     "overBound", "entries", "bytes", "bound"})
        self.assertEqual(snap["scans"] - s0["scans"], 2)
        self.assertEqual(snap["heldTurns"] - s0["heldTurns"], 6, "the second scan served the six closed turns")
        self.assertEqual(snap["walkedTurns"] - s0["walkedTurns"], 7 + 1)
        self.assertEqual(snap["entries"], 1)
        self.assertEqual(snap["bytes"], self.held()[4])
        self.assertEqual(snap["bound"], km.MSG_SCAN_HELD_BYTES)
        json.dumps(snap)

    def test_a_raise_leaves_no_key_behind_so_the_next_scan_retries(self):
        self.session(SID_WEB, 8)
        self.build()
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [reply(t + 20, "web-a08", "web-c08", "Shipped."),
                              peer(t + 300, "web-u09", "web-a08", "api asks: and the cursor?", "m-web09")])
        seg = next(s for _i, s in self.segs(SID_WEB) if "m-web09" in km._seg_mids(s))
        self.caption(SID_WEB, [{"id": seg["id"], "grain": "segment", "t": seg["t"], "caption": "web: weighing the cursor"}])

        def raising(turn, store):
            raise em.LazyBodyRead("a synthetic body read fault")
        with mock.patch.object(km, "_segs_seam", raising):
            self.assertNotIn("m-web09", self.build(), "the faulted scan serves the last-known map")
        self.assertEqual(self.build().get("m-web09"), "web: weighing the cursor",
                         "with nothing written since, the next scan retries instead of serving the faulted one's map")

    def test_an_entry_over_the_bound_alone_sheds_no_other_session(self):
        self.session(SID_WEB, 2)
        self.session(SID_API, 12)
        self.build()
        small, big = self.held(SID_WEB)[4], self.held(SID_API)[4]
        self.assertLess(small, big)
        s0 = km._msg_scan_report()
        with mock.patch.object(km, "MSG_SCAN_HELD_BYTES", big - 1):
            self.append(SID_API, [tool_out(self.open_t[SID_API] + 10, "api-r12", "api-c12", "toolu_1", "ok")])
            self.build()
            self.append(SID_API, [tool_out(self.open_t[SID_API] + 20, "api-r13", "api-r12", "toolu_1", "ok")])
            self.build()
        rep = km._msg_scan_report()
        self.assertIsNone(self.held(SID_API), "api's entry alone is over the bound: not held")
        self.assertIsNotNone(self.held(SID_WEB), "and web's, which fits, is not shed for it")
        self.assertEqual(rep["overBound"] - s0["overBound"], 2, "each scan of the over-bound session counts under overBound")
        self.assertEqual(rep["evict"], s0["evict"], "nothing was shed: evict stays for entries the bound shed (it read "
                                                    "+1 per scan of the over-bound session before)")

    def test_a_rewrite_that_drops_closed_turns_counts_partial(self):
        """A held entry cut short is a hold not served whole: the leaf rewritten in place down to six closed turns where
        the entry held twelve, every one of the six standing. partial stayed 0 here before, since the prefix that matched
        reached the end of the shorter list (the review after the merge)."""
        self.session(SID_WEB, 12)
        self.build()
        self.assertEqual(len(self.held()[2]), 12, "the premise: twelve closed turns held")
        s0 = km._msg_scan_report()
        recs = [json.loads(ln) for ln in Path(self.leaf[SID_WEB]).read_text().splitlines()]
        with open(self.leaf[SID_WEB], "w") as f:                       # in place: the same inode, so the same head
            f.write("".join(json.dumps(r) + "\n" for r in recs[:14]))  # turns 0 to 6, the last of them now the open one
        self.stamp(self.leaf[SID_WEB])
        got = self.build()
        rep = km._msg_scan_report()
        self.assertEqual(self.walked, 1, "the six closed turns stood: the last turn alone walked")
        self.assertEqual(rep["partial"] - s0["partial"], 1, "a hold cut short counts under partial")
        self.assertEqual(rep["reset"] - s0["reset"], 0, "the same leaf and seams: nothing discarded whole")
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))
        self.assertIn("m-web05", got)
        self.assertNotIn("m-web07", got, "the premise: the dropped turns' ids left the map")
        self.assertEqual(len(self.held()[2]), 6)
        self.append(SID_WEB, [user(self.t + 120 * 7, "web-u07b", "web-a06", "page 7 again")])
        self.build()
        self.assertEqual(km._msg_scan_report()["partial"] - s0["partial"], 1, "a turn boundary after it is no partial")

    def test_a_cut_released_that_restores_whole_turns_counts_nothing(self):
        """A rewind cut armed at an old reply, then released. The cut world's closed turns are the full world's first six
        (the turn the cut ends in is its open turn), so the entry the cut stored is a prefix of the restored keys: the
        release serves it whole and walks the restored turns, as after an append, and neither partial nor reset moves.
        The build road's release counts under partial only because its cut world leaves out a late record inside a held
        turn (the review of the follow-up)."""
        cut = {}
        self.addCleanup(jd.set_pending_cut_provider, jd._PENDING_CUT_FN)
        jd.set_pending_cut_provider(lambda sid: cut.get(sid, ""))     # the parse's cut
        backend = _Backend()
        backend.pending_cut = lambda sid: cut.get(sid, "")            # the scan key's cut (tearDown puts km._sdk back)
        km._sdk = lambda: backend
        self.session(SID_WEB, 12)
        self.build()
        s0 = km._msg_scan_report()
        cut[SID_WEB] = "web-a06"                                      # the cut world ends in turn 6, its open turn
        got = self.build()
        s1 = km._msg_scan_report()
        self.assertEqual(len(self.held()[2]), 6, "the premise: the cut world's six closed turns held")
        self.assertEqual(s1["partial"] - s0["partial"], 1, "the premise: the cut is a hold cut short")
        self.assertNotIn("m-web07", got, "the premise: the cut world drops turn 7's id")
        cut[SID_WEB] = ""
        got = self.build()
        rep = km._msg_scan_report()
        self.assertEqual(rep["partial"] - s1["partial"], 0, "a release that restores whole turns counts nothing")
        self.assertEqual(rep["reset"] - s0["reset"], 0, "the same leaf and seams throughout: nothing discarded whole")
        self.assertEqual(rep["heldTurns"] - s1["heldTurns"], 6, "the cut's six closed turns served from the hold")
        self.assertEqual(self.walked, 7, "the restored turns walked, the open one with them")
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))
        self.assertIn("m-web07", got)
        self.assertEqual(len(self.held()[2]), 12)

    def test_an_entry_the_seams_moved_is_dropped_before_its_walk(self):
        """An entry under other seams can never be served. It is dropped at the comparison, under the lock and before the
        walk, so a walk that then raises leaves nothing of it in the table to count toward the bound and shed a valid
        entry. Before, the dead entry was moved to the newest end first and outlasted valid ones: with the bound one
        byte short of all three entries, api's next store shed tests and kept web (the review after the merge)."""
        for sid in (SID_WEB, SID_API, SID_TESTS):
            self.session(sid, 6)
        self.build()                                                   # web, api, tests: web the least recently scanned
        sizes = {sid: self.held(sid)[4] for sid in (SID_WEB, SID_API, SID_TESTS)}
        seg = next(s for _i, s in self.segs(SID_WEB) if "m-web01" in km._seg_mids(s))
        p = jd.GOALDIR / (SID_WEB + ".json")
        p.write_text(json.dumps({"nodes": {}, "status": {}, "rev": 2,
                                 "seams": [{"segs": [jd._seg_key(seg["id"])], "t": seg["t"] + 5, "top": "g1",
                                            "text": "Back off the retries"}]}))
        self.stamp(p)
        s0 = km._msg_scan_report()

        def raising(turn, store):
            raise em.LazyBodyRead("a synthetic body read fault")
        with mock.patch.object(km, "_segs_seam", raising):
            with self.assertRaises(em.LazyBodyRead):
                km._msg_sum_scan_session(SID_WEB, self.leaf[SID_WEB], time.time())
        rep = km._msg_scan_report()
        self.assertIsNone(self.held(SID_WEB), "web's entry, under other seams, was dropped before its walk raised")
        self.assertEqual(rep["reset"] - s0["reset"], 1, "counted when it was discarded, though the walk then raised")
        self.assertEqual(rep["raised"] - s0["raised"], 1)
        with mock.patch.object(km, "MSG_SCAN_HELD_BYTES", sum(sizes.values()) - 1):   # one byte short of all three
            self.append(SID_API, [tool_out(self.open_t[SID_API] + 10, "api-r06", "api-c06", "toolu_1", "ok")])
            km._msg_sum_scan_session(SID_API, self.leaf[SID_API], time.time())
            after = km._msg_scan_report()
        self.assertIsNotNone(self.held(SID_TESTS), "tests, valid, is not shed for a dead entry")
        self.assertIsNotNone(self.held(SID_API))
        self.assertEqual(after["evict"], rep["evict"], "the two valid entries fit: nothing shed")

    def test_a_fault_outside_the_walk_is_counted_and_said_once_per_episode(self):
        """A scan that faults before its walk (the captions read, the parse, the store read, a seams field the
        fingerprint cannot read) serves the last-known map and is scanned again on every build. Each such scan counts
        under faulted (raised counts the walk's raises alone, scans the finished scans), and stderr says it once per
        episode, until a clean scan of the session ends the episode. Before, these faults were counted and said nowhere,
        so /perf read healthy while the session's captions stayed at the last-known map (the review after the merge)."""
        self.session(SID_WEB, 4)
        first = self.build()
        self.assertIn("m-web01", first, "the premise: a captioned peer message")
        store = jd.GOALDIR / (SID_WEB + ".json")

        @contextlib.contextmanager
        def seams_unreadable():
            store.write_text(json.dumps({"nodes": {}, "status": {}, "seams": 5, "rev": 2}))
            self.stamp(store)
            try:
                yield
            finally:
                store.write_text(json.dumps({"nodes": {}, "status": {}, "seams": [], "rev": 3}))
                self.stamp(store)
        faults = (("the captions read", lambda: mock.patch.object(km, "_captions", side_effect=OSError("a synthetic captions fault"))),
                  ("the parse", lambda: mock.patch.object(km, "_parse", side_effect=OSError("a synthetic parse fault"))),
                  ("the store read", lambda: mock.patch.object(jd, "load_goals_shared", side_effect=OSError("a synthetic store fault"))),
                  ("the seams' fingerprint", seams_unreadable))
        parent = "web-c04"
        for k, (what, fault) in enumerate(faults):
            s0 = km._msg_scan_report()
            self.append(SID_WEB, [tool_out(self.open_t[SID_WEB] + 10 * (k + 1), "web-r%d" % k, parent, "toolu_1", "ok")])
            parent = "web-r%d" % k
            err = io.StringIO()
            with fault(), mock.patch("sys.stderr", err):
                self.assertEqual(self.build(), first, "%s: the last-known map is served" % what)
                self.assertEqual(self.build(), first, "%s: scanned again, and faulted again" % what)
            rep = km._msg_scan_report()
            self.assertEqual(rep["faulted"] - s0["faulted"], 2, "%s: each faulted scan is counted" % what)
            self.assertEqual((rep["raised"], rep["scans"]), (s0["raised"], s0["scans"]), "%s: no walk raised or finished" % what)
            said = [ln for ln in err.getvalue().splitlines() if ln.startswith("[msg-scan]")]
            self.assertEqual(len(said), 1, "%s: said once for the episode: %r" % (what, said))
            self.assertIn("sid=%s" % SID_WEB[:8], said[0])
            err = io.StringIO()
            with mock.patch("sys.stderr", err):
                got = self.build()
            self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()), "%s: the clean scan's map" % what)
            self.assertEqual(km._msg_scan_report()["faulted"], rep["faulted"], "%s: a clean scan is no fault" % what)
            self.assertNotIn("[msg-scan]", err.getvalue(), "%s: a clean scan says nothing (and ends the episode)" % what)

    def test_the_head_is_taken_before_the_parse_reads_the_leaf(self):
        """The held entry's head is stat'd before the parse reads the leaf: a leaf replaced right after the parse
        returns leaves the old head beside the records read, and the next scan, under the new inode, walks whole. With
        the head stat'd after the parse, the new head would vouch for the old records and the next scan would serve the
        old message id until a closed turn's key moved (the review after the merge: nothing pinned the order). The id
        changed is a tool output's inside a middle turn, never a prompt's: a turn's id hashes its prompt's text, so an id
        changed in a prompt moves that turn's key and the scan walks it under either order."""
        self.session(SID_WEB, 4)
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [tool_out(t + 10, "web-r04", "web-c04", "toolu_1", "inbox: <!-- romp-msg-id: m-in04 -->"),
                              reply(t + 20, "web-a04", "web-r04", "Read the inbox."),
                              user(self.t + 600, "web-u05", "web-a04", "page 5 of the notes-api index"),
                              reply(self.t + 620, "web-a05", "web-u05", "Paged 5."),
                              user(self.t + 720, "web-u06", "web-a05", "page 6 of the notes-api index"),
                              reply(self.t + 740, "web-a06", "web-u06", "Paged 6."),
                              user(self.t + 840, "web-u07", "web-a06", "ship it"),
                              tool_call(self.t + 850, "web-c07", "web-u07", "toolu_2")])
        self.caption_closed(SID_WEB)                                    # turns 4 to 6, now closed, get their captions
        self.assertIn("m-in04", self.build(), "the premise: a middle turn's tool output carries a captioned id")
        self.append(SID_WEB, [tool_out(self.t + 860, "web-r07", "web-c07", "toolu_2", "ok")])
        real, swapped = km._parse, []

        def parse_then_replace(path, sid, now):
            got = real(path, sid, now)
            if not swapped and str(path) == self.leaf[SID_WEB]:
                body = Path(path).read_text().replace("m-in04", "m-xn04")   # one middle record's id, the same length
                with open(str(path) + ".new", "w") as f:
                    f.write(body)
                os.replace(str(path) + ".new", path)                        # a new file at the path: a new inode
                self.stamp(path)
                swapped.append(1)
            return got
        with mock.patch.object(km, "_parse", parse_then_replace):
            self.build()
        self.assertTrue(swapped, "the premise: the leaf was replaced inside the scan, after its parse")
        got = self.build()
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()), "the warm map is the cold one")
        self.assertNotIn("m-in04", got, "the replaced leaf's records are walked: the old id is gone")
        self.assertIn("m-xn04", got, "and the new one carries its segment's caption")

    def test_an_id_in_two_turns_takes_the_later_turns_caption(self):
        """The join assigns in turn and segment order, as the one-pass loop did, so an id named in a peer prompt and
        again in a later captioned turn carries the later turn's caption, cold and warm (nothing pinned the order: the
        road and the tests above name each id once)."""
        self.session(SID_WEB, 6)
        t = self.open_t[SID_WEB]
        self.append(SID_WEB, [tool_out(t + 10, "web-r06", "web-c06", "toolu_1", "inbox: <!-- romp-msg-id: m-web01 -->"),
                              reply(t + 20, "web-a06", "web-r06", "Answered api again."),
                              user(t + 300, "web-u07", "web-a06", "next page")])
        self.caption_closed(SID_WEB)                                    # turn 6, now closed, gets its caption
        segs = [(i, s) for i, s in self.segs(SID_WEB) if "m-web01" in km._seg_mids(s)]
        self.assertEqual([i for i, _s in segs], [1, 6], "the premise: the id is in turns 1 and 6")
        later, earlier = "web: work at %d" % segs[1][1]["t"], "web: work at %d" % segs[0][1]["t"]
        self.assertNotEqual(later, earlier)
        got = self.build()
        self.assertEqual(got.get("m-web01"), later, "the later turn's caption, as the one-pass loop gave it")
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))
        self.append(SID_WEB, [tool_call(t + 310, "web-c07", "web-u07", "toolu_2")])
        got = self.build()
        self.assertEqual(self.walked, 1, "the premise: the closed turns were served from the hold")
        self.assertEqual(got.get("m-web01"), later, "warm too")
        self.assertEqual(got, one_pass(SID_WEB, self.leaf[SID_WEB], time.time()))

    def test_the_reference_names_every_key_of_the_block(self):
        doc = (Path(HERE).parent / "docs" / "reference.md").read_text(encoding="utf-8")
        at = doc.index("`msgScan` is the message-caption scan's hold")
        passage = doc[at:doc.index("`judgingBand` is", at)]
        for k in km._msg_scan_report():
            self.assertIn("`%s`" % k, passage, k)


if __name__ == "__main__":
    unittest.main()
