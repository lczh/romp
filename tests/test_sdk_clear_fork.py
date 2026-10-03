#!/usr/bin/env python3
"""An SDK session that /clear-forks must READ its new transcript (the user 2026-07-10).

`/clear` on an SDK session mints a new transcript fsid under the same romp sid; the SDK backend
records it as the registry's lastSid (from the CLI's own init message — the authoritative source).
discover() previously associated forks only by custom-title (a tmux-ism the SDK never writes), so
every surface stayed pinned to the dead anchor file: the chat showed pre-clear history forever and
the timeline drew the anchor's unsettled tail as an ever-growing work bar. Now the anchor entry's
PATH follows lastSid (the sid stays stable — goals/captions/chat key on it), the discover
fingerprint signs the lastSid VALUE (a registry rewrite alone must bust the cache), and the parse
wrappers put the anchor file among the candidates so a resume-style fork that back-links across
files keeps its history while a /clear fork (parentUuid null) starts fresh. Since 2026-10-03 the
anchor joins only when the parse can reach it (a back-linked head, a resume lineage through it, no
head to tell by yet, or a states log that cannot be read): a /clear's leaf parses alone, reading no
byte of the pre-clear file, with every turn, segment and verdict of the two-file parse.

All fixtures are SYNTHETIC (invented text, placeholder UUIDs, hostname TESTHOST).
"""
import json
import os
import shutil
import tempfile
import time
import unittest
from datetime import datetime, timezone
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
em = jd.em

SID = "11111111-2222-3333-4444-555555555555"
FORK = "66666666-7777-8888-9999-aaaaaaaaaaaa"
NAME = "TESTHOST-session"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uline(t, text, uuid, parent=None):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "promptSource": "typed", "message": {"role": "user", "content": text}}


def aline(t, text, uuid, parent=None):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}],
                        "stop_reason": "end_turn"}}


class SdkClearForkBase(unittest.TestCase):
    def setUp(self):
        self._saved = jd.STATE
        self._saved_proj = jd.PROJECTS
        self._td = tempfile.mkdtemp()
        jd._rebind_state(Path(self._td))
        jd.PROJECTS = Path(self._td) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.now = int(time.time())
        self.cdir = str(Path(self._td) / "work")
        self.proj = jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        jd.NAMES.mkdir(parents=True, exist_ok=True)
        jd.SDKDIR.mkdir(parents=True, exist_ok=True)
        (jd.NAMES / SID).write_text("%s\t%s" % (NAME, self.cdir))

    def tearDown(self):
        jd._rebind_state(self._saved)
        jd.PROJECTS = self._saved_proj
        shutil.rmtree(self._td, ignore_errors=True)

    def _write(self, stem, records):
        p = self.proj / (stem + ".jsonl")
        p.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return p

    def _write_reg(self, last_sid, bump=0):
        p = jd.SDKDIR / (SID + ".json")
        p.write_text(json.dumps({"sid": SID, "name": NAME, "lastSid": last_sid, "alive": True}))
        # same-second rewrites share an mtime on coarse filesystems — bump it so the memo re-reads
        os.utime(p, (self.now + bump, self.now + bump))

    def _anchor_records(self):
        t = self.now - 600
        return [uline(t, "pre-clear ask about the widget", "u1"),
                aline(t + 5, "pre-clear widget answer", "a1", parent="u1")]


class DiscoverFollowsLastSid(SdkClearForkBase):
    def test_a_diverged_lastSid_moves_the_anchor_entrys_path_to_the_fork(self):
        self._write(SID, self._anchor_records())
        self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9")])
        self._write_reg(FORK)
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(len(rows), 1, "the session keeps ONE entry under its stable romp sid")
        fsid, path, anchor, name = rows[0]
        self.assertEqual(Path(path).stem, FORK, "the entry reads the CURRENT (lastSid) transcript")
        self.assertEqual((anchor, name), (SID, NAME))

    def test_an_undiverged_registry_keeps_the_anchor_path(self):
        self._write(SID, self._anchor_records())
        self._write_reg(SID)                              # lastSid == sid → no fork
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(Path(rows[0][1]).stem, SID)

    def test_a_lastSid_with_no_transcript_yet_falls_back_to_the_anchor(self):
        # the registry write can land a beat before the CLI creates the new file — never go dark
        self._write(SID, self._anchor_records())
        self._write_reg(FORK)
        rows = [r for r in jd.discover(self.now) if r[0] == SID]
        self.assertEqual(Path(rows[0][1]).stem, SID)

    def test_a_registry_only_lastSid_flip_busts_the_discover_cache(self):
        # the fork file already exists (it bumped the dir mtime long ago); ONLY the registry then
        # flips lastSid — no new directory entry, so the VALUE in the fingerprint must carry it
        self._write(SID, self._anchor_records())
        self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9")])
        a = jd.discover(self.now)
        self.assertEqual(Path([r for r in a if r[0] == SID][0][1]).stem, SID)
        self._write_reg(FORK, bump=1)
        b = jd.discover(self.now)
        self.assertIsNot(b, a, "the lastSid flip re-fingerprints")
        self.assertEqual(Path([r for r in b if r[0] == SID][0][1]).stem, FORK)


class ForkParseCandidates(SdkClearForkBase):
    def _texts(self, session):
        # atoms carry the raw record (text nested in message content) — serialize for a plain contains-check
        return "\n".join(json.dumps(t.get("atoms", [])) for t in session["turns"])

    def test_a_clear_fork_parses_FRESH_pre_clear_history_drops(self):
        self._write(SID, self._anchor_records())
        leaf = self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9"),
                                  aline(self.now - 55, "post-clear reply", "a9", parent="u9")])
        session = jd.parsed_session(SID, [str(leaf)], self.now)
        texts = self._texts(session)
        self.assertIn("post-clear fresh ask", texts)
        self.assertNotIn("pre-clear", texts, "/clear cut the chain — the transcript reads brand-new")

    def test_a_resume_style_fork_that_backlinks_keeps_its_history(self):
        anchor = self._write(SID, self._anchor_records())
        # the fork's head links to a1 IN THE ANCHOR FILE — the walk must cross files via the
        # anchor-sibling candidate parsed_session adds on its own
        leaf = self._write(FORK, [uline(self.now - 60, "post-resume ask", "u9", parent="a1"),
                                  aline(self.now - 55, "post-resume reply", "a9", parent="u9")])
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf), str(anchor)],
                         "a head that links back keeps the anchor, in the order it always had")
        session = jd.parsed_session(SID, [str(leaf)], self.now)
        texts = self._texts(session)
        self.assertIn("post-resume ask", texts)
        self.assertIn("pre-clear ask", texts, "cross-file resume chain keeps pre-fork history")

    # ── the anchor only when the parse can reach it (2026-10-03) ──
    def _states(self, rows):
        jd.STATESDIR.mkdir(parents=True, exist_ok=True)
        p = jd.STATESDIR / (SID + ".jsonl")
        p.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return p

    def _closure(self, leaf, cands):
        links = em.resume_fork_links(em._load_states(str(jd.STATESDIR / (SID + ".jsonl"))))
        return em._lineage_closure(Path(leaf), cands, links)

    def test_a_clear_fork_leaves_the_anchor_out_of_the_candidates(self):
        self._write(SID, self._anchor_records())
        leaf = self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9"),
                                  aline(self.now - 55, "post-clear reply", "a9", parent="u9")])
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf)],
                         "a parentless head no resume lineage runs through: the anchor is unreachable")

    def test_a_clear_fork_parse_reads_no_byte_of_the_anchor(self):
        anchor = self._write(SID, self._anchor_records())
        leaf = self._write(FORK, [uline(self.now - 60, "post-clear fresh ask", "u9"),
                                  aline(self.now - 55, "post-clear reply", "a9", parent="u9")])
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.pop(str(anchor), None)
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.pop(str(anchor), None)
        session = jd.parsed_session(SID, [str(leaf)], self.now)
        mem = jd._chain_membership(SID, str(leaf), "")
        self.assertIn("post-clear fresh ask", self._texts(session))
        self.assertIn("u9", mem["kept"])
        with em._READ_BYTES_LOCK:
            self.assertEqual(em._READ_BYTES.get(str(anchor), 0), 0, "neither the parse nor the chain walk read the anchor")
        with em._JSONL_CACHE_LOCK:
            self.assertNotIn(str(anchor), em._JSONL_CACHE, "and no record of it is resident")

    def test_a_two_hop_resume_lineage_keeps_the_anchor_in_its_place(self):
        # anchor -> L1 -> L2 by recorded resume forks, every head fresh: the anchor IS the conversation's start, so it stays a
        # candidate right after the leaf and the closure appends L1 after it, exactly as before
        anchor = self._write(SID, self._anchor_records())
        l1, l2 = "66666666-7777-8888-9999-aaaaaaaaaab1", "66666666-7777-8888-9999-aaaaaaaaaab2"
        p1 = self._write(l1, [uline(self.now - 300, "first resumed ask", "r1u"),
                              aline(self.now - 295, "first resumed reply", "r1a", parent="r1u")])
        p2 = self._write(l2, [uline(self.now - 60, "second resumed ask", "r2u"),
                              aline(self.now - 55, "second resumed reply", "r2a", parent="r2u")])
        self._states([{"t": self.now - 310, "resumeFork": {"from": SID, "to": l1}},
                      {"t": self.now - 70, "resumeFork": {"from": l1, "to": l2}}])
        cands = jd._judge_candidates(SID, [str(p2)])
        self.assertEqual(cands, [str(p2), str(anchor)])
        self.assertEqual(self._closure(p2, cands), [str(p2), str(anchor), str(p1)], "the closure's order, as before")
        texts = self._texts(jd.parsed_session(SID, [str(p2)], self.now))
        for t in ("pre-clear ask about the widget", "first resumed ask", "second resumed ask"):
            self.assertIn(t, texts, "the stitched history holds every hop")

    def test_an_unreadable_states_log_keeps_the_anchor_and_is_never_memoized(self):
        # the two-hop lineage above with its states log unreadable: the reader answers [] for it as for an empty log, so the
        # lineage through the anchor cannot be seen and the anchor stays a candidate. A chmod moves neither the mtime nor the
        # size the links memo keys on, so once the log reads again a memo of the blind read would still be served (the anchor
        # dropped, the closure reordered) until the log next grew
        if os.geteuid() == 0:
            self.skipTest("root reads any file: the unreadable log cannot be staged")
        anchor = self._write(SID, self._anchor_records())
        l1, l2 = "66666666-7777-8888-9999-aaaaaaaaaad1", "66666666-7777-8888-9999-aaaaaaaaaad2"
        p1 = self._write(l1, [uline(self.now - 300, "first resumed ask", "d1u"),
                              aline(self.now - 295, "first resumed reply", "d1a", parent="d1u")])
        p2 = self._write(l2, [uline(self.now - 60, "second resumed ask", "d2u"),
                              aline(self.now - 55, "second resumed reply", "d2a", parent="d2u")])
        log = self._states([{"t": self.now - 310, "resumeFork": {"from": SID, "to": l1}},
                            {"t": self.now - 70, "resumeFork": {"from": l1, "to": l2}}])
        st0 = os.stat(log)
        log.chmod(0)
        try:
            with self.assertRaises(PermissionError, msg="premise: the log cannot be opened"):
                open(log, "rb").close()
            self.assertEqual(jd._judge_candidates(SID, [str(p2)]), [str(p2), str(anchor)],
                             "a lineage that cannot be read keeps the anchor")
        finally:
            log.chmod(0o644)
        st1 = os.stat(log)
        self.assertEqual((st1.st_mtime_ns, st1.st_size), (st0.st_mtime_ns, st0.st_size), "premise: the memo's key is unchanged")
        self.assertEqual(jd._resume_links(SID), {l1: SID, l2: l1}, "the readable log's links, not the blind read's")
        cands = jd._judge_candidates(SID, [str(p2)])
        self.assertEqual(cands, [str(p2), str(anchor)])
        self.assertEqual(self._closure(p2, cands), [str(p2), str(anchor), str(p1)], "the closure's order, as before")

    def test_a_resume_after_a_clear_leaves_the_anchor_out(self):
        # the anchor, a /clear to L1 (no lineage row: a fresh conversation), then a resume fork L1 -> L2: the conversation
        # starts at L1's parentless head
        self._write(SID, self._anchor_records())
        l1, l2 = "66666666-7777-8888-9999-aaaaaaaaaac1", "66666666-7777-8888-9999-aaaaaaaaaac2"
        p1 = self._write(l1, [uline(self.now - 300, "post-clear ask", "c1u"),
                              aline(self.now - 295, "post-clear reply", "c1a", parent="c1u")])
        p2 = self._write(l2, [uline(self.now - 60, "resumed after the clear", "c2u"),
                              aline(self.now - 55, "resumed reply", "c2a", parent="c2u")])
        self._states([{"t": self.now - 70, "resumeFork": {"from": l1, "to": l2}}])
        cands = jd._judge_candidates(SID, [str(p2)])
        self.assertEqual(cands, [str(p2)])
        self.assertEqual(self._closure(p2, cands), [str(p2), str(p1)])
        texts = self._texts(jd.parsed_session(SID, [str(p2)], self.now))
        self.assertIn("post-clear ask", texts, "the resume stitch still crosses into L1")
        self.assertNotIn("widget", texts)

    def _resume_after_a_clear(self, tag, l2_records):
        """The anchor, a /clear to L1 (a fresh head, no lineage row), then a resume fork L1 -> L2 whose file holds
        `l2_records`. Returns (anchor, L1, L2) paths."""
        anchor = self._write(SID, self._anchor_records())
        l1, l2 = "66666666-7777-8888-9999-aaaaaaaaa%s01" % tag, "66666666-7777-8888-9999-aaaaaaaaa%s02" % tag
        p1 = self._write(l1, [uline(self.now - 300, "post-clear ask", tag + "1u"),
                              aline(self.now - 295, "post-clear reply", tag + "1a", parent=tag + "1u")])
        p2 = self.proj / (l2 + ".jsonl")
        p2.write_text("".join(json.dumps(r) + "\n" for r in l2_records))
        self._states([{"t": self.now - 70, "resumeFork": {"from": l1, "to": l2}}])
        return anchor, p1, p2

    def test_a_resume_after_a_clear_whose_new_file_has_no_record_yet_keeps_the_anchor(self):
        # review round one: the floor under salvaged replies is the earliest head over the lineage, and with no head in L2
        # yet it cannot be told, so a landed pre-clear reply came back as an orphan in the chat until L2's head was written.
        # As for a /clear's leaf with no record yet, the anchor stays meanwhile, and leaves once the head is written
        anchor, p1, p2 = self._resume_after_a_clear("e", [{"type": "queue-operation", "operation": "enqueue",
                                                           "timestamp": iso(self.now)}])
        self.assertEqual(jd._judge_candidates(SID, [str(p2)]), [str(p2), str(anchor)])
        self.assertIsNone(jd.conversation_floor(SID, str(p2)), "premise: the floor cannot be told yet")
        with open(p2, "a") as fh:
            fh.write(json.dumps(uline(self.now - 60, "resumed after the clear", "e2u")) + "\n")
        self.assertEqual(jd._judge_candidates(SID, [str(p2)]), [str(p2)], "the head is written: the anchor is unreachable")

    def test_a_resume_after_a_clear_whose_new_files_head_stamp_does_not_parse_keeps_the_anchor(self):
        head = uline(self.now - 60, "resumed after the clear, a broken stamp", "f2u")
        head["timestamp"] = "not-a-timestamp"
        anchor, p1, p2 = self._resume_after_a_clear("f", [head, aline(self.now - 55, "resumed reply", "f2a", parent="f2u")])
        self.assertEqual(jd._judge_candidates(SID, [str(p2)]), [str(p2), str(anchor)])

    def test_a_seventeen_hop_resume_lineage_keeps_the_anchor(self):
        # the walk stops where the lineage closure stops, at sixteen hops: a lineage it cannot finish walking keeps the anchor,
        # here one that does run back to it (anchor -> F1 -> ... -> F17, every head fresh)
        anchor = self._write(SID, self._anchor_records())
        rows, prev, leaf = [], SID, None
        for i in range(1, 18):
            stem = "66666666-7777-8888-9999-aaaaaaaa%04d" % i
            leaf = self._write(stem, [uline(self.now - 3000 + 100 * i, "resumed ask %d" % i, "h%du" % i),
                                      aline(self.now - 2995 + 100 * i, "resumed reply %d" % i, "h%da" % i, parent="h%du" % i)])
            rows.append({"t": self.now - 3010 + 100 * i, "resumeFork": {"from": prev, "to": stem}})
            prev = stem
        self._states(rows)
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf), str(anchor)])

    def test_a_two_hop_resume_after_a_clear_keeps_the_leaf_alone(self):
        # the anchor, a /clear to L1, then resumes L1 -> L2 -> L3: the walk crosses both hops to L1's parentless head
        self._write(SID, self._anchor_records())
        l1, l2, l3 = ("66666666-7777-8888-9999-aaaaaaaaag%02d" % i for i in (1, 2, 3))
        p1 = self._write(l1, [uline(self.now - 500, "post-clear ask", "g1u"),
                              aline(self.now - 495, "post-clear reply", "g1a", parent="g1u")])
        p2 = self._write(l2, [uline(self.now - 300, "first resume", "g2u"),
                              aline(self.now - 295, "first resumed reply", "g2a", parent="g2u")])
        p3 = self._write(l3, [uline(self.now - 60, "second resume", "g3u"),
                              aline(self.now - 55, "second resumed reply", "g3a", parent="g3u")])
        self._states([{"t": self.now - 310, "resumeFork": {"from": l1, "to": l2}},
                      {"t": self.now - 70, "resumeFork": {"from": l2, "to": l3}}])
        cands = jd._judge_candidates(SID, [str(p3)])
        self.assertEqual(cands, [str(p3)])
        self.assertEqual(self._closure(p3, cands), [str(p3), str(p2), str(p1)])

    def test_a_resume_lineage_whose_middle_files_head_stamp_does_not_parse_keeps_the_anchor(self):
        # the floor is the earliest head over the whole closure, so a file resumed into on the way back whose first record's
        # stamp does not parse leaves it untold, as the leaf's own would: the anchor stays (review round one)
        anchor = self._write(SID, self._anchor_records())
        l1, l2, l3 = ("66666666-7777-8888-9999-aaaaaaaaam%02d" % i for i in (1, 2, 3))
        self._write(l1, [uline(self.now - 500, "post-clear ask", "m1u"),
                         aline(self.now - 495, "post-clear reply", "m1a", parent="m1u")])
        head = uline(self.now - 300, "first resume, a broken stamp", "m2u")
        head["timestamp"] = "not-a-timestamp"
        self._write(l2, [head, aline(self.now - 295, "first resumed reply", "m2a", parent="m2u")])
        p3 = self._write(l3, [uline(self.now - 60, "second resume", "m3u"),
                              aline(self.now - 55, "second resumed reply", "m3a", parent="m3u")])
        self._states([{"t": self.now - 310, "resumeFork": {"from": l1, "to": l2}},
                      {"t": self.now - 70, "resumeFork": {"from": l2, "to": l3}}])
        self.assertEqual(jd._judge_candidates(SID, [str(p3)]), [str(p3), str(anchor)])

    def test_a_leaf_with_no_record_yet_keeps_the_anchor(self):
        # the registry names the new file a beat before the CLI writes its first record: nothing to prove the head by
        anchor = self._write(SID, self._anchor_records())
        leaf = self.proj / (FORK + ".jsonl")
        leaf.write_text(json.dumps({"type": "queue-operation", "operation": "enqueue", "timestamp": iso(self.now)}) + "\n")
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf), str(anchor)])
        leaf.write_text("")
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf), str(anchor)], "an empty file too")

    def test_a_leaf_whose_head_stamp_does_not_parse_keeps_the_anchor(self):
        anchor = self._write(SID, self._anchor_records())
        head = uline(self.now - 60, "post-clear ask with a broken stamp", "u9")
        head["timestamp"] = "not-a-timestamp"
        leaf = self._write(FORK, [head, aline(self.now - 55, "reply", "a9", parent="u9")])
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf), str(anchor)])

    def test_a_clear_fork_parses_the_same_with_and_without_the_anchor(self):
        """The parity guard: for a /clear's leaf every turn id, segment id, atom and verdict the two-file parse gave is the
        one the leaf-alone parse gives. The anchor holds a rewound branch, a compaction and a parallel tool batch; the leaf
        has its own of each."""
        def batch(tag, t, parent):
            def call(n, cmd, par):
                return {"type": "assistant", "timestamp": iso(t), "uuid": "%sc%d" % (tag, n), "parentUuid": par,
                        "message": {"id": "msg_" + tag, "role": "assistant", "stop_reason": "tool_use",
                                    "content": [{"type": "tool_use", "id": "tu_%s%d" % (tag, n), "name": "Bash",
                                                 "input": {"command": cmd}}]}}

            def res(n, out):
                return {"type": "user", "timestamp": iso(t + 1 + n), "uuid": "%sr%d" % (tag, n), "parentUuid": "%sc%d" % (tag, n),
                        "sourceToolAssistantUUID": "%sc%d" % (tag, n),
                        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu_%s%d" % (tag, n),
                                                                 "content": out}]}}
            return [call(1, "ls notes_api", parent), call(2, "pwd", tag + "c1"), res(1, "notes.py"), res(2, "/w/notes-api"),
                    aline(t + 5, "Both checks ran.", tag + "done", parent=tag + "r2")]

        def body(tag, t0):
            recs = [uline(t0, "list the notes-api files", tag + "u1"),
                    aline(t0 + 5, "Listing them.", tag + "a1", parent=tag + "u1"),
                    uline(t0 + 10, "and the routes too", tag + "u2", tag + "a1"),          # rewound away below
                    aline(t0 + 15, "Routes listed.", tag + "a2", parent=tag + "u2"),
                    uline(t0 + 20, "no, the models instead", tag + "u3", tag + "a1"),      # the rewind: u3 re-parents at a1
                    aline(t0 + 25, "Models listed.", tag + "a3", parent=tag + "u3"),
                    {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t0 + 40), "uuid": tag + "cb",
                     "parentUuid": None, "logicalParentUuid": tag + "a3",
                     "compactMetadata": {"trigger": "manual", "preTokens": 9000}},
                    uline(t0 + 50, "run both checks at once", tag + "u4", tag + "cb")]
            return recs + batch(tag, t0 + 55, tag + "u4")
        anchor = self._write(SID, body("x", self.now - 3000))
        leaf = self._write(FORK, body("y", self.now - 600))
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        two = em.parse_session(str(leaf), rompuuid=SID, candidate_files=[str(leaf), str(anchor)],
                               states=str(jd.STATESDIR / (SID + ".jsonl")), postal_log=str(jd.MESSAGES), now=self.now,
                               sdk_human=False)
        two_m = em.chain_membership(str(leaf), candidate_files=[str(leaf), str(anchor)])
        one = jd.parsed_session(SID, [str(leaf)], self.now)
        one_m = jd._chain_membership(SID, str(leaf), "")

        def shape(tree):
            return ([t["id"] for t in tree["turns"]],
                    [s["id"] for t in tree["turns"] for s in em.segments(t)],
                    [a.get("uuid") for t in tree["turns"] for a in t["atoms"]])
        self.assertEqual(shape(one), shape(two), "turn ids, segment ids and atoms are the two-file parse's")
        self.assertIn("ydone", shape(one)[2], "premise: the leaf's batch reply is in the parse")
        self.assertIn("yr1", shape(one)[2], "premise: the batch's first result is kept beside the spine")
        leaf_uuids = {json.loads(ln)["uuid"] for ln in leaf.read_text().splitlines() if ln.strip()}
        for k in ("rewind", "kept"):
            self.assertEqual(set(one_m[k]) & leaf_uuids, set(two_m[k]) & leaf_uuids, k)
        self.assertIn("yu2", one_m["rewind"], "premise: the leaf's own rewind is found")
        self.assertEqual(jd._judge_candidates(SID, [str(leaf)]), [str(leaf)], "and the leaf-alone parse is the one compared")


class KernelParseMirrors(unittest.TestCase):
    """The kernel's _parse must mirror the anchor-sibling candidates (it has no jsdom-style harness —
    importing bin/romp-kernel runs the SDK boot reconcile against LIVE state — so this pins source)."""
    def test_kernel_parse_adds_the_anchor_sibling_candidate(self):
        src = (Path(BIN) / "romp-kernel").read_text()
        self.assertIn('anchor = os.path.join(os.path.dirname(path), sid + ".jsonl")', src)
        self.assertIn('if os.path.basename(path) != sid + ".jsonl" and os.path.exists(anchor):', src)
        self.assertIn("candidate_files=cands", src)

    def test_the_kernels_chain_checks_take_the_parses_own_candidates(self):
        # the rewind hold's kept set and its spent-flag check walk the graph the display parse walks (2026-10-03: a /clear's
        # leaf alone), and so does a fork's seeding, never a hand-built leaf-plus-anchor pair
        src = (Path(BIN) / "romp-kernel").read_text()
        self.assertEqual(src.count('cands = jd._judge_candidates(sid, [sess["path"]])'), 2)
        self.assertIn("cands = jd._judge_candidates(parent_sid, [path])", src)


if __name__ == "__main__":
    unittest.main()
