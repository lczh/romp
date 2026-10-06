#!/usr/bin/env python3
"""The timeline's DEAD-LANE memo (2026-09-08): a lane whose session is dead is re-derived only when an input moves.

The full timeline build ran every pusher cycle (the fleet signature's 5 s bucket turns over faster than a 6 s
cycle) and re-parsed every lane's transcript and goals each time, dead lanes included. Now a dead lane's
parse-derived parts (bars, compactions, the work end, its judging marks) are served from a memo keyed on
every file they read and the host's recorded suspensions, its parse is dropped from _parse_cache once cached
(the resident-memory lever), and the served frame is byte-identical to a rebuilt one: the judging marks are
derived once at horizon zero, stamped with the value the horizon test compares, and filtered per build on
exactly that. The bars encoder reuses the strings of entry objects it already encoded.

Synthetic transcript, states and captions under a temp root; a placeholder sid; the lane is dead because the
liveness snapshot is empty.

DerivationSplit, below the dead-lane classes, covers the judging derivation's split from the horizon assembly
(_derive_judging_marks and _judging_assemble against a private copy of the one-pass form as the oracle); it sits
in this module because the dead-lane memo's stamped marks are that split's other caller. The classes on
LaneMemoBase cover the LIVE-lane memo, which shares the lane loop and the /perf block with the dead-lane memo: a
fresh state root per test through jd._rebind_state, one live lane, and private synthetic sids because those
classes mint goal stores."""
import ast
import inspect
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone
from unittest import mock
from romp_load import load_source
from fs_clock import move_ctime   # noqa: E402  the shared test helper, on the path the line above put there
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_lane_memo", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-3333-4444-555555555555"
NOW = 1_800_000_000
T0 = NOW - 3600


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _rec(kind, t, uuid, parent, text):
    if kind == "user":
        return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
                "message": {"role": "user", "content": text}}
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}


class DeadLaneMemo(unittest.TestCase):
    def setUp(self):
        km._downtime[:] = []
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        cdir = td / "launchdir"; cdir.mkdir()
        proj = td / "projects"
        pdir = proj / km.jd.re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
        pdir.mkdir(parents=True)
        self.recs = [_rec("user", T0, "u1", None, "run the long benchmark"),
                     _rec("assistant", T0 + 10, "a1", "u1", "Launched it.")]
        self.tpath = pdir / (SID + ".jsonl")
        self._write(self.recs)
        names = td / "names"; names.mkdir()
        (names / SID).write_text("testsess\t%s\t#abcdef\n" % str(cdir))
        self.saved = (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._live_map)
        km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR = names, proj, td / "goals", td / "captions"
        km.jd.STATE = td
        km.NAMES = names
        km._live_map = lambda: {}                 # NOBODY is live: the lane is a dead one within the window
        (td / "states").mkdir(); (td / "captions").mkdir(); (td / "goals").mkdir()
        (td / "goals" / (SID + ".json")).write_text(json.dumps({"nodes": {}, "status": {}}))   # the judging marks need a store
        self.caps = td / "captions" / (SID + ".jsonl")
        km._parse_cache.pop(str(self.tpath), None)
        km._dead_lane_memo.clear()
        km._delta_entry_memo.clear()
        saved_dir = km.em._CKPT_DIR_FN              # the release tests write documents: a checkpoint directory of the test's
        km.em.set_checkpoint_dir(lambda: td / "checkpoints")   # own, set before any budget opens (setting it resets the budget),
        self.addCleanup(km.em.set_checkpoint_dir, saved_dir)   # since an earlier module may have turned judge.py's off (round
        #                                                        one of the fold's review)

    def tearDown(self):
        (km.jd.NAMES, km.jd.PROJECTS, km.jd.GOALDIR, km.jd.CAPDIR, km.jd.STATE, km.NAMES, km._live_map) = self.saved
        km._dead_lane_memo.clear()
        km._downtime[:] = []
        self.td.cleanup()

    def _write(self, recs):
        self.tpath.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        os.utime(self.tpath, (NOW - 30, NOW - 30))    # recently touched: a lane within the 12 h window

    def _build(self, now=NOW):
        return km.build_timeline(now, {}, with_bars=True)

    def _lane(self, tl):
        return next(s for s in tl["sessions"] if s["id"] == SID)

    def test_the_second_build_serves_the_lane_without_a_parse_and_drops_the_parse(self):
        parses = []
        real = km._parse
        km._parse = lambda path, sid, now: (parses.append(path), real(path, sid, now))[1]
        try:
            tl1 = self._build()
            self.assertEqual(parses, [str(self.tpath)], "the first build parses the dead lane once")
            self.assertNotIn(str(self.tpath), km._parse_cache, "and drops the parse once the lane is cached")
            self.assertIn(SID, km._dead_lane_memo)
            tl2 = self._build()
            self.assertEqual(len(parses), 1, "the second build serves the lane: no parse")
            self.assertEqual(tl1["turns"][SID], tl2["turns"][SID])
            self.assertEqual(self._lane(tl1), self._lane(tl2), "a served lane is the rebuilt lane, byte for byte")
            self.assertEqual([m for m in tl1["judging"] if m["sid"] == SID], [m for m in tl2["judging"] if m["sid"] == SID])
            self.assertFalse(any("_h" in m for m in tl2["judging"]), "the horizon stamp never reaches the wire")
        finally:
            km._parse = real

    def test_a_moved_transcript_re_derives_the_lane(self):
        self._build()
        recs = self.recs + [_rec("user", T0 + 600, "u2", "a1", "and the cap?"),
                            _rec("assistant", T0 + 610, "a2", "u2", "Two minutes.")]
        self._write(recs)
        os.utime(self.tpath, (NOW - 20, NOW - 20))
        tl = self._build()
        self.assertEqual(len(tl["turns"][SID]), 2, "the new turn is drawn")
        self.assertEqual(km._dead_lane_memo[SID][1]["bars"], tl["turns"][SID])

    def test_the_memo_keeps_stamped_marks_and_the_wire_never_sees_the_stamp(self):
        cap_t = NOW - km.TL_HORIZON + 30
        self.caps.write_text(json.dumps({"id": "u1", "t": cap_t, "caption": "launched the benchmark",
                                         "grain": "segment"}) + "\n")
        tl = self._build(NOW)
        marks = km._dead_lane_memo[SID][1]["marks"]
        self.assertEqual([(m["judge"], m["_h"]) for m in marks], [("captioner", cap_t)], "derived once, stamped")
        self.assertFalse(any("_h" in m for m in tl["judging"]), "the stamp never reaches the wire")

    def test_every_keyed_input_re_derives_the_lane_when_it_moves(self):
        """The key is every file the parse-derived parts read, not the transcript alone (the review of the
        first batch found a key pinned on one component): touching each one changes the memo's key and the
        lane is derived again."""
        td = Path(self.td.name)
        self._build()
        key0 = km._dead_lane_memo[SID][0]
        inputs = [td / "states" / (SID + ".jsonl"), td / "goals" / (SID + ".json"),
                  td / "overrides" / (SID + ".jsonl"), td / "captions" / (SID + ".jsonl"),
                  td / "archive" / (SID + ".json"), td / "session-flags.json"]
        seen = {key0}
        for i, p in enumerate(inputs):
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.name == SID + ".json" and p.parent.name == "goals":
                p.write_text(json.dumps({"nodes": {}, "status": {}, "touched": i}))
            elif p.suffix == ".json":
                p.write_text(json.dumps({"touched": i}))
            else:
                p.write_text("")
            os.utime(p, (NOW - 10 + i, NOW - 10 + i))
            self._build()
            key = km._dead_lane_memo[SID][0]
            self.assertNotIn(key, seen, "%s moved but the key did not" % p.name)
            seen.add(key)
        self._build()
        self.assertEqual(km._dead_lane_memo[SID][0], key, "nothing moved: the key stands")

    def test_a_lane_whose_goals_store_cannot_be_read_is_never_cached(self):
        """A goals FAULT is a store that cannot be read (an OSError; malformed bytes are healed by the loader):
        the lane renders without goal-derived data, complains, and is derived again on every build rather
        than served from a memo that would silence the fault."""
        store = Path(self.td.name) / "goals" / (SID + ".json")
        store.chmod(0)
        try:
            self._build()
            self.assertNotIn(SID, km._dead_lane_memo, "a faulted store stays loud on every build, never served stale")
        finally:
            store.chmod(0o600)
        self._build()
        self.assertIn(SID, km._dead_lane_memo, "readable again: cached like any other dead lane")

    def test_a_live_lane_is_not_memoized(self):
        km._live_map = lambda: {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "",
                                           "context": None, "compactPct": None, "color": None, "mode": ""}}
        km.build_timeline(NOW, km._live_map(), with_bars=True)
        self.assertNotIn(SID, km._dead_lane_memo)

    def test_a_chmod_alone_moves_the_transcripts_stat_key(self):
        """A chmod, chown or rename moves a file's ctime while its mtime, size and inode stand, so the stat
        key carries st_ctime_ns as its fourth member: the repair of a read the memo cached as the empty lane is
        visible to the key."""
        p = str(self.tpath)
        k0 = km._stat_key(p)
        move_ctime(p)
        k1 = km._stat_key(p)
        self.assertNotEqual(k0, k1, "ctime is in the key: a chmod or a rename moves it")
        self.assertEqual(k0[:3], k1[:3], "mtime, size and inode stood")
        self.assertIsNone(km._stat_key(p + ".absent"))

    def test_a_failed_parse_is_served_once_cached_and_re_attempted_when_the_transcripts_stat_moves(self):
        """A transcript that cannot be read parses as the empty lane (the read layer returns no records on an
        OSError, silently) and a parse that raises leaves the same empty lane plus one stderr line; either is
        cached like any other lane (a dead transcript has no writer, so the result would repeat). The lane is
        derived again only when a keyed file moves, and a chmod or chown that repairs the read moves neither
        mtime, size nor inode: the ctime in the key is what makes the repair visible. The vehicle here is a
        raising stub, the path with an observable complaint; the chmod only moves the ctime."""
        parses, failing = [], [True]
        real = km._parse

        def parse(path, sid, now):
            parses.append(path)
            if failing[0]:
                raise OSError("unreadable")
            return real(path, sid, now)
        km._parse = parse
        km._BARS_COMPLAINED.pop((SID, "parse"), None)   # the complaint latch: one line per distinct cause
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl1 = self._build()
                tl2 = self._build()
            self.assertEqual(len(parses), 1, "parsed once: the failed parse is cached as the empty lane")
            self.assertEqual(tl1["turns"][SID], [])
            self.assertEqual(tl2["turns"][SID], [], "served as the empty lane it drew")
            self.assertEqual(err.getvalue().count("timeline bars:"), 1, "one stderr line, none when served")
            self.assertIn(SID, km._dead_lane_memo)
            failing[0] = False
            move_ctime(self.tpath)
            tl3 = self._build()
            self.assertEqual(len(parses), 2, "the transcript's stat moved: the parse is attempted again")
            self.assertEqual(len(tl3["turns"][SID]), 1, "readable again: the bar is drawn")
            self._build()
            self.assertEqual(len(parses), 2, "and the repaired lane is served like any other")
        finally:
            km._parse = real

    def test_a_suspension_recorded_after_the_lane_was_cached_re_derives_it(self):
        """_awake_spans excises every recorded suspension from each segment's span, reading the in-memory list
        (its jsonl mirror is appended best-effort, so the list, not the file, is the input), and the list
        grows at run time when the producer's tick detects a sleep, on a thread other than the build's. The
        key carries the list: a nap inside a cached segment splits its bar on the very next build; a
        different nap of the same count is a different key; a nap outside every segment re-derives to equal
        bars; the same list rebound is served."""
        parses = []
        real = km._parse
        km._parse = lambda path, sid, now: (parses.append(path), real(path, sid, now))[1]
        try:
            self._build()
            key0 = km._dead_lane_memo[SID][0]
            self.assertEqual(len(parses), 1)
            km._downtime[:] = [(T0 + 3, T0 + 7)]            # a nap inside the one segment, an atom on each side
            tl = self._build()
            self.assertEqual(len(tl["turns"][SID]), 2, "the bar is cut at the nap on the very next build")
            self.assertEqual(len(parses), 2, "derived again, not served")
            key1 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key1, key0, "the suspensions are in the key")
            km._downtime[:] = [(T0 + 2, T0 + 8)]            # a different nap, the same count
            tl2 = self._build()
            self.assertNotEqual(tl2["turns"][SID], tl["turns"][SID], "a different nap cuts the bar elsewhere")
            key2 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key2, key1, "a nap of the same count is a different key")
            km._downtime.append((NOW - 20000, NOW - 19000))  # a sleep outside every segment: equal bars, a new key
            tl3 = self._build()
            key3 = km._dead_lane_memo[SID][0]
            self.assertNotEqual(key3, key2)
            self.assertEqual(tl3["turns"][SID], tl2["turns"][SID], "a nap outside every segment: the same bars")
            self.assertEqual(len(parses), 4)
            km._downtime[:] = list(km._downtime)            # the same content, rebound
            self._build()
            self.assertEqual(km._dead_lane_memo[SID][0], key3, "the same suspensions: the same key")
            self.assertEqual(len(parses), 4, "served")
        finally:
            km._parse = real

    def _open_budget(self):
        """The pusher cycle's checkpoint budget opened wide for the test, so the release's document write (the quiet drop's
        write half) is never deferred by what earlier tests in this process spent, and set back after."""
        saved = dict(km.em._CKPT_CYCLE)
        km.em.checkpoint_cycle_begin(1 << 40)
        self.addCleanup(lambda: (km.em._CKPT_CYCLE.clear(), km.em._CKPT_CYCLE.update(saved)))

    def _three_turns(self):
        recs = [_rec("user", T0, "u1", None, "run the long benchmark"), _rec("assistant", T0 + 10, "a1", "u1", "Launched it."),
                _rec("user", T0 + 600, "u2", "a1", "and the cap?"), _rec("assistant", T0 + 610, "a2", "u2", "Raised to 4096."),
                _rec("user", T0 + 1200, "u3", "a2", "ship it"), _rec("assistant", T0 + 1210, "a3", "u3", "Shipped.")]
        self._write(recs)
        getattr(km, "_dead_lane_released", {}).clear()
        self._open_budget()
        return str(self.tpath)

    def _asm_keys(self, path):
        with km.em._ASM_LOCK:
            return [k for k in km.em._ASM_CACHE if k[0] == os.path.realpath(path) and k[1] == SID]

    def test_a_dead_lane_whose_document_stands_releases_its_assembly_entry_and_records_once(self):
        """Release on the events that end a file's use (2026-10-01): the lever dropped a dead lane's parse and left the
        session's assembly entry (an adapter over every record, its atoms) and the leaf's record entry resident. When the
        leaf's assembly document stands (so any later parse restores from it, never parses whole), the lever releases the
        assembly entry and drops the records no live lane reads, once per transcript version: a miss after it (a keyed input
        moved) derives the lane again through one restore and keeps what the restore rebuilt."""
        p = self._three_turns()
        with km.em._ASM_CKPT_LOCK:                                    # an earlier module's hydrated body under one of these
            km.em._HYDRATED.clear(); km.em._HYDRATED_BYTES[0] = 0      #  synthetic uuids must not serve this lane's atoms
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=bool(km._display_sdk_human(SID)), tree=tree),
                        km.em.asm_checkpoint_stats()["skipped"])
        km._parse_cache.pop(p, None)
        self.assertTrue(self._asm_keys(p), "the session's assembly entry stands before the lane is built")
        tl1 = self._build()
        self.assertEqual(self._asm_keys(p), [], "the dead lane's assembly entry is released at the lever")
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(p, km.em._JSONL_CACHE, "no live lane reads the leaf: its records are dropped")
        tl2 = self._build()
        self.assertEqual(tl2["turns"][SID], tl1["turns"][SID], "served from the lane memo")
        arch = Path(self.td.name) / "archive" / (SID + ".json")
        arch.parent.mkdir(parents=True, exist_ok=True)
        arch.write_text(json.dumps({"touched": 1}))                   # a keyed input moves: the lane is derived again
        tl3 = self._build()
        self.assertEqual(km._parse_mode.get(p), "restore", "the parse after the release restores from the document")
        self.assertTrue(self._asm_keys(p), "one release per transcript version: what the restore rebuilt stays")
        self.assertEqual(tl3["turns"][SID], tl1["turns"][SID], "and the lane it derives is the same")

    def test_a_dead_lane_with_no_document_keeps_its_assembly_entry(self):
        """With no document a parse after the release would be a whole parse: the lever leaves the entry as before."""
        p = self._three_turns()
        km._parse(p, SID, NOW)
        km._parse_cache.pop(p, None)
        self.assertFalse(km.em.asm_document_stands(p))
        self._build()
        self.assertTrue(self._asm_keys(p), "kept: nothing to restore from")
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(p, km.em._JSONL_CACHE)

    def test_a_dead_lane_whose_document_a_restore_would_refuse_keeps_its_assembly_entry(self):
        """A document under a standing refusal mark (the chain proof refused it for the tail's shape at this very stat), or one
        a restore in this process refused (the session's entry kept whole for it), would not be restored from: a parse after
        a release would be a whole parse, so the lever leaves the entry and the records as before."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        self.assertTrue(km.em._asm_mark_refused(p, "shape", rompuuid=SID, sdk_human=human))
        self._build()                                                 # the lane's parse is the store's (a hit): the entry as it stands
        self.assertTrue(self._asm_keys(p), "kept: the document stands, but a refusal mark stands over it")
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(p, km.em._JSONL_CACHE)

    def test_a_dead_lane_whose_entry_a_restore_refused_keeps_it(self):
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        for k in self._asm_keys(p):
            with km.em._ASM_LOCK:
                km.em._ASM_CACHE[k]["keepWhole"] = True                # as the whole parse after a refused restore marks it
        self._build()
        self.assertTrue(self._asm_keys(p), "kept: a restore here already refused the document")
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(p, km.em._JSONL_CACHE)

    def test_the_dead_lanes_record_release_keeps_every_file_a_live_lane_reads(self):
        """The record half of the release drops the dead lane's leaf and anchor only outside every live lane's leaf and anchor:
        a fork child that is live while its registry still names the dead parent's file reads that very file."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        child = "11111111-2222-3333-4444-5555555555c1"
        alive = [{"sid": SID, "path": p}, {"sid": child, "path": p}]
        km._release_dead_lane(SID, p, ("v1",), alive, {child: {"state": "waiting"}})
        self.assertEqual(self._asm_keys(p), [], "the dead session's own assembly entry goes")
        with km.em._ASM_LOCK:
            self.assertFalse([k for k in km.em._ASM_CACHE if k[0] == os.path.realpath(p)], "no entry pins the file")
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(p, km.em._JSONL_CACHE, "a live lane reads the file: its records stay")
        km._release_dead_lane(SID, p, ("v2",), alive, {})              # the child ended too (a new transcript version)
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(p, km.em._JSONL_CACHE, "no live lane reads it any more: dropped")

    def test_after_a_dead_lane_release_the_next_named_fold_over_the_leaf_restores_from_its_document(self):
        """The release drops the leaf's record entry the way the quiet fold's drop does, its fold document written first when
        it lacks what this process holds (round one of the review: a pop with no write left the kernel's named folds over the
        leaf with cursors at a generation that no longer existed, so the next one read the whole file again and put the whole
        entry back). The next named fold restores its cursor from the document and steps nothing, reading the guard bytes."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        km._parse_cache.pop(p, None)
        name = "deadLaneNamedFold"
        self.addCleanup(lambda: km.em._FOLD_REG.pop(name, None))
        cache = {}
        s1 = km.em.fold_records(cache, p, lambda: 0, lambda s, r: s + 1, ckpt=name)   # a named fold over the leaf, as the kernel's
        self.assertEqual(s1, 6)
        self._build()                                                 # dead: the lane is cached and released
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(p, km.em._JSONL_CACHE, "the premise: the release dropped the leaf's records")
        with km.em._READ_BYTES_LOCK:
            b0 = km.em._READ_BYTES.get(p, 0)
        kinds = []
        s2 = km.em.fold_records(cache, p, lambda: 0, lambda s, r: s + 1, on=kinds.append, ckpt=name)
        with km.em._READ_BYTES_LOCK:
            read = km.em._READ_BYTES.get(p, 0) - b0
        self.assertEqual(kinds, ["restore"], "the next fold restores from the document the release wrote, not a whole refold")
        self.assertEqual(s2, s1, "to the same state")
        self.assertLessEqual(read, 4 * km.em._JSONL_TAIL_GUARD, "reading the guard bytes, not the %d-byte file" % os.path.getsize(p))

    def _second_file(self, head_parent):
        """The session's second transcript beside its first (self.tpath, the anchor), three turns whose head names
        `head_parent`: the anchor's last uuid for a leaf that links back into it, None for the fresh head a /clear writes."""
        leaf = str(self.tpath.with_name("11111111-2222-3333-4444-5555555555c2.jsonl"))
        Path(leaf).write_text("\n".join(json.dumps(r) for r in [
            _rec("user", T0 + 1800, "cu1", head_parent, "page the notes-api index"),
            _rec("assistant", T0 + 1810, "ca1", "cu1", "Paged."),
            _rec("user", T0 + 2400, "cu2", "ca1", "and by cursor?"), _rec("assistant", T0 + 2410, "ca2", "cu2", "By cursor too."),
            _rec("user", T0 + 3000, "cu3", "ca2", "ship it"), _rec("assistant", T0 + 3010, "ca3", "cu3", "Shipped.")]) + "\n")
        return leaf

    def _documented_parse(self, leaf):
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(leaf, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(leaf, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])

    def test_a_dead_lane_over_a_back_linked_leaf_drops_its_leaf_and_its_anchor(self):
        """A dead lane whose leaf is a file of its own, its head linking back into the session's first file (a resume-style
        fork): that first file is the anchor, the leaf's second candidate, so the parse holds both files' records and the
        release drops both record entries (round one of an earlier review: every dead-lane test had a leaf that was its own
        anchor, so a release that left the anchor passed them all). A /clear's leaf parses alone since 2026-10-03, so a
        back-linked leaf is the case whose anchor a parse reaches, and the one this release must drop."""
        self._three_turns()
        anchor = str(self.tpath)
        leaf = self._second_file("a3")                                # the anchor's last uuid
        self.assertEqual(km.jd._judge_candidates(SID, [leaf]), [leaf, anchor], "the premise: the head links back, so the "
                                                                              "anchor is a candidate")
        self._documented_parse(leaf)
        with km.em._JSONL_CACHE_LOCK:
            self.assertTrue(leaf in km.em._JSONL_CACHE and anchor in km.em._JSONL_CACHE, "the premise: the parse read both files")
        km._parse_cache.pop(leaf, None)
        km._release_dead_lane(SID, leaf, ("v1",), [{"sid": SID, "path": leaf}], {})
        self.assertEqual(self._asm_keys(leaf), [], "the session's entry of its leaf is released")
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(leaf, km.em._JSONL_CACHE, "the leaf's records go")
            self.assertNotIn(anchor, km.em._JSONL_CACHE, "and so do its anchor's: no live lane reads it")

    def test_a_dead_lane_after_a_clear_never_holds_its_anchor(self):
        """After a /clear the dead lane's leaf heads fresh, and its parse reads the leaf alone (2026-10-03): the session's first
        file never enters the reader, before the release or after it."""
        self._three_turns()
        anchor = str(self.tpath)
        leaf = self._second_file(None)
        self.assertEqual(km.jd._judge_candidates(SID, [leaf]), [leaf], "the premise: a fresh head, the leaf alone")
        self._documented_parse(leaf)
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(leaf, km.em._JSONL_CACHE, "the premise: the parse read the leaf")
            self.assertNotIn(anchor, km.em._JSONL_CACHE, "the parse never read the session's first file")
        km._parse_cache.pop(leaf, None)
        km._release_dead_lane(SID, leaf, ("v1",), [{"sid": SID, "path": leaf}], {})
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(leaf, km.em._JSONL_CACHE, "the leaf's records go")
            self.assertNotIn(anchor, km.em._JSONL_CACHE, "and the first file is not held")

    def test_a_release_that_raises_never_drops_the_frame(self):
        """The dead lane's release does file I/O (a stat, a sidecar read, a document write) inside the timeline build's lane
        loop, whose other steps each report through a complaint of their own: a raise there is noted once per file and
        outcome, and the frame is built with the lane in it (round one of the review: the raise left the build). Here the
        document check raises, before either release and before the version is recorded: nothing was released, the lane's
        next miss asks again, and the line claims no wait for the transcript (round three of the fold's review)."""
        p = self._three_turns()
        f0 = km.em._ASM_STATS.get("releaseFailed", 0)
        err = io.StringIO()
        with mock.patch.object(km.em, "asm_document_restores", side_effect=OSError("permission denied")) as chk, \
                redirect_stderr(err):
            tl = self._build()
            self.assertTrue(tl["turns"][SID], "the lane is drawn")
            self.assertIn(SID, km._dead_lane_memo, "and cached as any dead lane is")
            self.assertEqual(km.em._ASM_STATS.get("releaseFailed", 0) - f0, 1, "the failure is counted")
            self.assertNotIn(SID, km._dead_lane_released, "no version recorded before the document check answered")
            km._release_dead_lane(SID, p, km._dead_lane_memo[SID][0][0], [{"sid": SID, "path": p}], {})   # the lane's next miss
            self.assertEqual(chk.call_count, 2, "at the build's own version the next miss asks again (round four of the review)")
            self.assertEqual(km.em._ASM_STATS.get("releaseFailed", 0) - f0, 2, "and its failure is counted too")
        self.assertIn("dead lane", err.getvalue())
        self.assertIn("for session %s" % SID, err.getvalue(), "naming the session (round two of the fold's review)")
        self.assertIn("(the release stopped where it raised and what ran before it stays released; said once per file and "
                      "outcome)", err.getvalue(), "the default outcome alone: %r" % err.getvalue())
        with km.em._JSONL_CACHE_LOCK:
            self.assertIn(p, km.em._JSONL_CACHE, "nothing was released")

    def test_a_release_that_raises_after_the_version_is_recorded_is_not_retried_until_the_transcript_moves(self):
        """The dead lane records the transcript's version before either release runs, so a release that raises is not tried
        again at the same version, and its line says so; what ran before the raise stays released. A raise at the document
        check comes before the version is recorded and says no such thing (the test above), since the lane's next miss asks
        again (round three of the fold's review: the line said the memory was left as it stood, false once the assembly
        release had popped the entry)."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        self.assertTrue(self._asm_keys(p), "the premise: the session's assembly entry stands")
        alive = [{"sid": SID, "path": p}]
        f0 = km.em._ASM_STATS.get("releaseFailed", 0)
        err = io.StringIO()
        with mock.patch.object(km.em, "release_records", side_effect=OSError("permission denied")) as rel, redirect_stderr(err):
            km._release_dead_lane(SID, p, ("v1",), alive, {})
            km._release_dead_lane(SID, p, ("v1",), alive, {})       # the same transcript version: not tried again
            self.assertEqual(rel.call_count, 1, "tried once at this version")
            km._release_dead_lane(SID, p, ("v2",), alive, {})       # the transcript moved: tried again
            self.assertEqual(rel.call_count, 2, "and again once the transcript moved")
        self.assertEqual(self._asm_keys(p), [], "the assembly release ran before the record release raised, and stays released")
        self.assertEqual(km.em._ASM_STATS.get("releaseFailed", 0) - f0, 2, "each failure counted")
        line = next(ln for ln in err.getvalue().splitlines() if " at dead lane failed" in ln)
        self.assertIn("(the release stopped where it raised and what ran before it stays released; not retried until the "
                      "transcript moves; said once per file and outcome)", line)
        self.assertIn("for session %s" % SID, line)

    def test_an_assembly_release_that_raises_stops_the_lane_and_is_not_retried_at_its_version(self):
        """The version is recorded before either release, and the assembly release runs first: one that raises stops the
        lane's release there, so the record release never runs, the same transcript version does not try again, and the
        line says so (round four of the fold's review: only the record release had been made to raise here, so recording
        the version after the assembly release passed)."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        alive = [{"sid": SID, "path": p}]
        err = io.StringIO()
        with mock.patch.object(km.em, "release_assembly", side_effect=OSError("permission denied")) as asm, \
                mock.patch.object(km.em, "release_records") as rel, redirect_stderr(err):
            km._release_dead_lane(SID, p, ("v1",), alive, {})
            km._release_dead_lane(SID, p, ("v1",), alive, {})       # the same transcript version: not tried again
        self.assertEqual(asm.call_count, 1, "the assembly release was tried once at this version")
        self.assertEqual(rel.call_count, 0, "and the record release never ran: the release stopped where it raised")
        line = next(ln for ln in err.getvalue().splitlines() if " at dead lane failed" in ln)
        self.assertIn("; not retried until the transcript moves", line)

    def test_the_dead_lanes_two_outcomes_for_one_leaf_are_each_said(self):
        """A raise at the document check and a raise after the version is recorded say different things for the same leaf,
        so each is said once: the memo keys on the outcome as well as the event and the path (round four of the fold's
        review: keyed on the event and the path alone, the second outcome went unsaid)."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        alive = [{"sid": SID, "path": p}]
        err = io.StringIO()
        with redirect_stderr(err):
            with mock.patch.object(km.em, "asm_document_restores", side_effect=OSError("permission denied")):
                km._release_dead_lane(SID, p, ("v1",), alive, {})   # refused before the version is recorded
            with mock.patch.object(km.em, "release_records", side_effect=OSError("permission denied")):
                km._release_dead_lane(SID, p, ("v1",), alive, {})   # the check clears; the record release raises
        lines = [ln for ln in err.getvalue().splitlines() if " at dead lane failed" in ln]
        self.assertEqual(len(lines), 2, "both outcomes said: %r" % err.getvalue())
        self.assertNotIn("not retried", lines[0], "the document check's line claims no wait")
        self.assertIn("; not retried until the transcript moves", lines[1], "the record release's says it waits")

    def test_the_dead_lanes_two_outcomes_are_each_said_in_the_reverse_order_too(self):
        """The record release raising at v1, after the version is recorded, then the document check raising at a moved
        version: the first line carries the wait and the second does not, since the v2 check raised before v2 was
        recorded and the lane's next miss asks again (round five of the fold's review: a presence check on the
        recorded version, or a memo keyed on the event and the path that deduplicated only the default outcome, passed
        the first order alone)."""
        p = self._three_turns()
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        alive = [{"sid": SID, "path": p}]
        f0 = km.em._ASM_STATS.get("releaseFailed", 0)
        err = io.StringIO()
        with redirect_stderr(err):
            with mock.patch.object(km.em, "release_records", side_effect=OSError("permission denied")):
                km._release_dead_lane(SID, p, ("v1",), alive, {})   # recorded first; the record release raises
            self.assertEqual(km._dead_lane_released.get(SID), ("v1",), "the premise: v1 was recorded before the raise")
            with mock.patch.object(km.em, "asm_document_restores", side_effect=OSError("permission denied")) as chk:
                km._release_dead_lane(SID, p, ("v2",), alive, {})   # moved: the check raises before v2 is recorded
            self.assertEqual(chk.call_count, 1, "the check ran once at v2")
        self.assertEqual(km.em._ASM_STATS.get("releaseFailed", 0) - f0, 2, "both failures counted")
        lines = [ln for ln in err.getvalue().splitlines() if " at dead lane failed" in ln]
        self.assertEqual(len(lines), 2, "both outcomes said: %r" % err.getvalue())
        self.assertIn("; not retried until the transcript moves", lines[0], "the record release's line says it waits")
        self.assertNotIn("not retried", lines[1], "the v2 check's line claims no wait")

    def _many_turns(self, n):
        recs, parent = [], None
        for k in range(n):
            u, a = "u%d" % k, "a%d" % k
            recs += [_rec("user", T0 + 60 * k, u, parent, "page %d of the notes-api index" % k),
                     _rec("assistant", T0 + 60 * k + 10, a, u, "Paged %d." % k)]
            parent = a
        self._write(recs)
        getattr(km, "_dead_lane_released", {}).clear()
        self._open_budget()
        return str(self.tpath), recs

    def test_a_revived_session_restores_once_after_the_release_and_then_folds(self):
        """The release's own regression risk: a session revived after its dead lane let go of its entry and records pays one
        restore (the document's pre-cut rows and the tail from its cut), never a whole parse, for its lane and its chat's
        parse together, and folds its next turn over the restored entry."""
        p, recs = self._many_turns(48)
        human = bool(km._display_sdk_human(SID))
        tree = km._parse(p, SID, NOW)
        self.assertTrue(km.em.asm_checkpoint_write(p, SID, sdk_human=human, tree=tree), km.em.asm_checkpoint_stats()["skipped"])
        km._parse_cache.pop(p, None)
        self._build()                                                 # dead: the lane is cached and the session released
        self.assertEqual(self._asm_keys(p), [], "released at the lever")
        with km.em._JSONL_CACHE_LOCK:
            self.assertNotIn(p, km.em._JSONL_CACHE)
        live = {SID: {"state": "waiting", "since": NOW - 100, "model": "", "effort": "", "context": None, "compactPct": None,
                      "color": None, "mode": ""}}
        km._live_map = lambda: live
        s0 = dict(km.em._ASM_STATS)
        km.build_timeline(NOW, live, with_bars=True)                  # revived: its lane parses
        km._parse(p, SID, NOW)                                        # and its chat build's parse, through the same store
        roads = {k: km.em._ASM_STATS.get(k, 0) - s0.get(k, 0) for k in ("restore", "full", "fold")}
        self.assertEqual(roads, {"restore": 1, "full": 0, "fold": 0}, "one restore, no whole parse")
        self.assertTrue(self._asm_keys(p), "the restored entry stands for the live session")
        recs = recs + [_rec("user", T0 + 60 * 48, "u48", "a47", "and the last page?"),
                       _rec("assistant", T0 + 60 * 48 + 10, "a48", "u48", "Paged the last one.")]
        self._write(recs)
        s1 = dict(km.em._ASM_STATS)
        km.build_timeline(NOW, live, with_bars=True)
        roads = {k: km.em._ASM_STATS.get(k, 0) - s1.get(k, 0) for k in ("restore", "full", "fold")}
        self.assertEqual(roads, {"restore": 0, "full": 0, "fold": 1}, "the next turn folds over the restored entry")
        self.assertEqual(len(km._parse(p, SID, NOW)["turns"]), 49)


class HorizonFilterIsExact(unittest.TestCase):
    """The cached marks filtered on their stamped compare value are the marks a fresh derivation at that
    horizon would append, mark for mark: the diary and distiller marks are compared on their EVIDENCE time
    but emitted at the segment's work END, so a filter on the emitted time would admit a mark the fresh
    derivation drops (evidence before the horizon, work end after it). Pure functions, synthetic inputs."""

    def _inputs(self):
        h = 1_700_000_000
        caps = {"c%d" % i: {"id": "c%d" % i, "t": h - 100 + i * 50, "caption": "cap %d" % i, "grain": "segment"} for i in range(6)}
        goals = {"nodes": {
            "g1": {"t": h - 40, "mt": h + 10, "text": "old mint, done after the horizon",
                   "log": [{"src": "closer", "kind": "done", "ev_t": h - 5, "why": "evidence just before the horizon"}]},
            "g2": {"t": h + 20, "mt": h + 30, "text": "new mint", "distilledMt": h - 20, "briefedMt": h + 40},
        }}
        seg_ends = {h - 5: h + 300, h - 20: h + 400}      # completion marks land at the work END, after the horizon
        return h, caps, goals, seg_ends

    def test_filtered_stamped_marks_equal_a_fresh_derivation(self):
        h, caps, goals, seg_ends = self._inputs()
        for t0 in (h - 1000, h - 10, h, h + 15, h + 35, h + 1000):
            fresh = []
            km._derive_judging("s", caps, goals, t0, fresh, seg_ends)
            stamped = []
            km._derive_judging("s", caps, goals, 0, stamped, seg_ends, stamp=True)
            self.assertEqual(km._dead_lane_marks(stamped, t0), fresh, "horizon %+d" % (t0 - h))
        fresh = []
        km._derive_judging("s", caps, goals, h, fresh, seg_ends)
        self.assertFalse(any(m["judge"] == "closer" for m in fresh), "evidence before the horizon: dropped")
        self.assertTrue(any(m["judge"] == "distiller" and m["kind"] == "brief" for m in fresh))

    def test_the_stamp_is_private(self):
        h, caps, goals, seg_ends = self._inputs()
        stamped = []
        km._derive_judging("s", caps, goals, 0, stamped, seg_ends, stamp=True)
        self.assertTrue(stamped and all("_h" in m for m in stamped))
        self.assertFalse(any("_h" in m for m in km._dead_lane_marks(stamped, 0)))


class EntryEncodeMemo(unittest.TestCase):
    def test_an_entry_object_seen_last_split_is_not_encoded_again(self):
        km._delta_entry_memo.clear()
        sep = km._DELTA_SEP
        b1, b2 = {"id": "b1", "start": 1, "end": 2}, {"id": "b2", "start": 3, "end": 4}
        ents1, _ = km._delta_split("dictlist:id", {"S": [b1, b2]}, memo_key=("bars", "turns"))
        b3 = {"id": "b3", "start": 5, "end": 6}
        ents2, _ = km._delta_split("dictlist:id", {"S": [b1, b3]}, memo_key=("bars", "turns"))
        self.assertIs(ents2["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1], "the same object: the same string, not re-encoded")
        self.assertEqual(json.loads(ents2["S" + sep + "b3"][1]), b3)
        self.assertNotIn(id(b2), km._delta_entry_memo[("bars", "turns")], "the memo is rebuilt from THIS split: no growth")
        b1b = dict(b1)                                   # equal content, a NEW object: encoded afresh (identity, never equality)
        ents3, _ = km._delta_split("dictlist:id", {"S": [b1b]}, memo_key=("bars", "turns"))
        self.assertEqual(ents3["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1])
        self.assertIsNot(ents3["S" + sep + "b1"][1], ents1["S" + sep + "b1"][1])

    def test_the_wire_fill_hands_the_collection_key_down(self):
        import inspect
        self.assertIn("_delta_split(kind, value, memo_key=(ftype, name))", inspect.getsource(km._delta_parts))

    def test_a_memo_hit_hands_back_the_previous_split_pair_and_a_miss_mints_one_pair(self):
        """A split's (object, json) pair is a tuple that holds a dict, which the collector tracks for life, and a split's
        pairs live until the next build: long enough to reach the oldest generation, whose collection walks every tracked
        object the kernel holds (2026-09-16: two fresh pairs per bar per build, the memo saving the encode and not the
        tuples). So a hit hands back the LAST split's own pair, and a miss mints one pair for the memo and the entries both."""
        km._delta_entry_memo.clear()
        sep = km._DELTA_SEP
        b1, b2 = {"id": "b1", "start": 1, "end": 2}, {"id": "b2", "start": 3, "end": 4}
        k1 = "S" + sep + "b1"
        ents1, _ = km._delta_split("dictlist:id", {"S": [b1, b2]}, memo_key=("bars", "turns"))
        memo1 = km._delta_entry_memo[("bars", "turns")]
        self.assertIs(memo1[id(b1)], ents1[k1], "a miss: ONE pair, the memo's and the entries' the same tuple")
        self.assertIs(memo1[id(b2)], ents1["S" + sep + "b2"])
        ents2, _ = km._delta_split("dictlist:id", {"S": [b1, b2]}, memo_key=("bars", "turns"))
        self.assertIs(ents2[k1], ents1[k1], "a hit: the previous split's pair itself, no new tuple")
        self.assertIs(km._delta_entry_memo[("bars", "turns")][id(b1)], ents2[k1], "and the rebuilt memo holds that same pair")
        self.assertEqual(ents2[k1], (b1, json.dumps(b1)), "the pair is the same value as ever: the object and its string")
        b1b = dict(b1)                                   # equal content, a NEW object: a miss, one fresh pair
        ents3, _ = km._delta_split("dictlist:id", {"S": [b1b]}, memo_key=("bars", "turns"))
        self.assertIsNot(ents3[k1], ents1[k1])
        self.assertIs(km._delta_entry_memo[("bars", "turns")][id(b1b)], ents3[k1])


# ── the judging derivation split (_derive_judging_marks + _judging_assemble): what the one-pass form emitted ──
LIVE_SID = "44444444-5555-6666-7777-888888888801"      # private synthetic sids: the classes below mint goal stores, and a
LIVE_SID2 = "44444444-5555-6666-7777-888888888802"     # store minted under the shared placeholder sid can be re-flagged by
LIVE_PARENT = "44444444-5555-6666-7777-888888888803"   # another module's journaled overrides (load_goals replays them)
LIVE_CHILD = "44444444-5555-6666-7777-888888888804"
LIVE_SID3 = "44444444-5555-6666-7777-888888888805"


def _one_pass_judging(sid, caps, goals, t0, out, seg_ends=None):
    """_derive_judging as it stood in one pass, before the derivation was split from the horizon assembly: the
    equality oracle for DerivationSplit (the horizon compared inline, the caption cap taken inline)."""
    endt = (lambda tt: seg_ends.get(tt, tt)) if seg_ends else (lambda tt: tt)
    caps_in = sorted((c for c in caps.values() if c.get("t") and c["t"] >= t0), key=lambda c: c["t"])
    for c in caps_in[-km.JUDGE_CAP_LIMIT:]:
        out.append({"judge": "captioner", "sid": sid, "t": c["t"],
                    "kind": c.get("grain", "segment"), "text": c.get("caption", "")})
    for n in goals.get("nodes", {}).values():
        t = n.get("t")
        if not t:
            continue
        text = n.get("text", "")
        mt = n.get("mt") or t
        go = n.get("groupOp")
        if isinstance(go, dict) and (go.get("t") or 0) >= t0:
            out.append({"judge": "grouper", "sid": sid, "t": go["t"],
                        "kind": go.get("kind") or "group", "text": text})
        if n.get("origin"):
            if t >= t0:
                out.append({"judge": "courier", "sid": sid, "t": t, "kind": "plant", "text": text})
        elif n.get("umbrella"):
            if mt >= t0:
                out.append({"judge": "grouper", "sid": sid, "t": mt, "kind": "group", "text": text})
        elif t >= t0:
            out.append({"judge": "planner", "sid": sid, "t": t,
                        "kind": ("mint" if not n.get("parentId") else "sub"), "text": text})
        for _e in (n.get("log") or []):
            if _e.get("synth") or (_e.get("ev_t") or 0) < t0:
                continue
            if _e.get("src") in ("planner", "closer") and _e.get("kind") in ("done", "block"):
                out.append({"judge": _e["src"] if _e["src"] == "planner" else "closer", "sid": sid,
                            "t": endt(_e["ev_t"]),
                            "kind": ("done" if _e["src"] == "planner" else "close") if _e["kind"] == "done" else "block",
                            "text": _e.get("why") or text})
        if n.get("distilledMt") and n["distilledMt"] >= t0:
            out.append({"judge": "distiller", "sid": sid, "t": endt(n["distilledMt"]), "kind": "distill",
                        "text": n.get("summary") or text})
        if n.get("briefedMt") and n["briefedMt"] >= t0:
            out.append({"judge": "distiller", "sid": sid, "t": endt(n["briefedMt"]), "kind": "brief",
                        "text": n.get("blockSummary") or text})
    try:
        arch = json.loads((km.jd.STATE / "archive" / (sid + ".json")).read_text(errors="replace"))
        if arch.get("t") and arch["t"] >= t0:
            out.append({"judge": "archiver", "sid": sid, "t": arch["t"], "kind": "index",
                        "text": arch.get("headline", "")})
    except (OSError, ValueError):
        pass


class DerivationSplit(unittest.TestCase):
    """_derive_judging_marks derives every mark with no horizon and no clock; _judging_assemble applies the horizon
    and the caption cap per build. Together they emit what the one-pass form emitted, mark for mark, at every
    horizon, and the wrapper _derive_judging still answers its callers."""

    def setUp(self):
        self._saved_state = km.jd.STATE
        self._td = tempfile.mkdtemp()
        km.jd._rebind_state(Path(self._td))
        self.now = 1_781_100_000

    def tearDown(self):
        km.jd._rebind_state(self._saved_state)
        shutil.rmtree(self._td, ignore_errors=True)

    def fixture(self, timeless=False):
        now = self.now
        caps = {"c%d" % i: {"id": "c%d" % i, "grain": "segment" if i % 2 else "turn", "t": now - 9000 + i * 100,
                            "caption": "c%d" % i} for i in range(km.JUDGE_CAP_LIMIT + 10)}
        caps["tie"] = {"id": "tie", "grain": "segment", "t": now - 9000 + 100, "caption": "tie"}   # an equal t: stable order
        caps["none"] = {"id": "none", "grain": "segment", "caption": "no t"}
        nodes = {
            "g1": {"id": "g1", "parentId": None, "t": now - 8000, "mt": now - 7000, "text": "Top",
                   "log": [{"src": "planner", "kind": "done", "ev_t": now - 7000, "why": "shipped"},
                           {"src": "closer", "kind": "done", "ev_t": now - 6500},
                           {"src": "closer", "kind": "block", "ev_t": now - 6000, "why": "waiting"},
                           {"src": "planner", "kind": "done", "ev_t": now - 5000, "synth": True},
                           {"src": "user", "kind": "done", "ev_t": now - 4000}],
                   "distilledMt": now - 7000, "summary": "the takeaway", "briefedMt": now - 6000, "blockSummary": "the brief"},
            "g2": {"id": "g2", "parentId": "g1", "t": now - 7500, "text": "Step"},
            "g3": {"id": "g3", "parentId": None, "t": now - 3000, "text": "Planted", "origin": {"peer": "x"}},
            "g4": {"id": "g4", "parentId": None, "t": now - 2000, "mt": now - 1500, "text": "Umbrella", "umbrella": True},
            "g5": {"id": "g5", "parentId": None, "t": now - 1000, "text": "Merged", "groupOp": {"t": now - 900, "kind": "merge"}},
            "g6": {"id": "g6", "parentId": None, "text": "no t"},
        }
        if timeless:
            nodes["g7"] = {"id": "g7", "parentId": None, "t": now - 500, "text": "timeless op", "groupOp": {"kind": "retitle"},
                           "log": [{"src": "planner", "kind": "done"}]}
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (LIVE_SID + ".json")).write_text(json.dumps({"t": now - 300, "headline": "h"}))
        seg_ends = {now - 7000: now - 6900, now - 6000: now - 5900}
        return caps, {"nodes": nodes}, seg_ends

    def test_equal_to_the_one_pass_form_at_every_horizon(self):
        caps, goals, seg_ends = self.fixture()
        # now-6950 and now-5950 fall inside a completion's evidence-to-work-end window (seg_ends moves now-7000 to
        # now-6900 and now-6000 to now-5900): a filter on the plotted time instead of the evidence time shows there
        for t0 in (0, self.now - 100000, self.now - 6950, self.now - 6500, self.now - 6000, self.now - 5950,
                   self.now - 1000, self.now - 250, self.now + 1):
            for se in (seg_ends, None):
                want, got = [], []
                _one_pass_judging(LIVE_SID, caps, goals, t0, want, se)
                km._derive_judging(LIVE_SID, caps, goals, t0, got, se)
                self.assertEqual(json.dumps(got), json.dumps(want), "t0=%r seg_ends=%r" % (t0, se))
                cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, se)
                out = []
                km._judging_assemble(cap_marks, other, t0, out)
                self.assertEqual(json.dumps(out), json.dumps(want))
        self.assertEqual(len([m for m in want if m["judge"] == "captioner"]), 0, "the last horizon dropped every caption")

    def test_a_timeless_group_op_or_diary_row_is_dropped_as_the_one_pass_form_dropped_it(self):
        caps, goals, seg_ends = self.fixture(timeless=True)
        for t0 in (self.now - 100000, self.now - 250):
            want, got = [], []
            _one_pass_judging(LIVE_SID, caps, goals, t0, want, seg_ends)
            km._derive_judging(LIVE_SID, caps, goals, t0, got, seg_ends)
            self.assertEqual(json.dumps(got), json.dumps(want))
            self.assertFalse(any(m["kind"] == "retitle" for m in got))

    def test_the_marks_are_derived_once_and_filtered_per_horizon(self):
        caps, goals, seg_ends = self.fixture()
        cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        self.assertEqual(len(cap_marks), km.JUDGE_CAP_LIMIT + 11, "every timed caption, no cap yet")
        self.assertEqual([m["t"] for m in cap_marks], sorted(m["t"] for m in cap_marks))
        fts = [ft for ft, m in other]
        self.assertIn(self.now - 7000, fts, "a completion's filter time is the diary's ev_t")
        done = next(m for ft, m in other if m["judge"] == "planner" and m["kind"] == "done")
        self.assertEqual(done["t"], self.now - 6900, "while its plotted t is the segment's work end")
        out = []
        km._judging_assemble(cap_marks, other, self.now - 6950, out)      # a horizon between the two
        kinds = {(m["judge"], m["kind"]) for m in out}
        self.assertNotIn(("planner", "done"), kinds, "filtered on the evidence time (before the horizon), not the plotted end")
        self.assertNotIn(("distiller", "distill"), kinds, "the distiller's mark likewise (distilledMt before the horizon)")
        self.assertIn(("closer", "close"), kinds, "a completion whose evidence is after the horizon stays")

    def test_the_stamped_assembly_copies_the_shared_marks(self):
        # the pairs are a memo's objects, shared into every unstamped build: the stamp lands on copies only
        caps, goals, seg_ends = self.fixture()
        cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        stamped = []
        km._judging_assemble(cap_marks, other, 0, stamped, stamp=True)
        self.assertTrue(stamped and all("_h" in m for m in stamped))
        self.assertFalse(any("_h" in m for m in cap_marks) or any("_h" in m for ft, m in other), "the memo's marks stay clean")
        self.assertFalse(any(any(s is m for m in cap_marks) or any(s is m for ft, m in other) for s in stamped))
        for t0 in (self.now - 6500, self.now - 250):
            fresh = []
            km._judging_assemble(cap_marks, other, t0, fresh)
            self.assertEqual(km._dead_lane_marks(stamped, t0), fresh, "filtered on the stamp: the fresh assembly at %d" % t0)

    def test_the_derivation_reads_no_clock(self):
        class NoClock:
            def time(self):
                raise AssertionError("the derivation read the clock")

            def __getattr__(self, name):
                return getattr(time, name)
        caps, goals, seg_ends = self.fixture()
        real = km.time
        km.time = NoClock()
        try:
            cap_marks, other = km._derive_judging_marks(LIVE_SID, caps, goals, seg_ends)
        finally:
            km.time = real
        self.assertTrue(cap_marks and other)
        tree = ast.parse(inspect.getsource(km._derive_judging_marks))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"now", "time", "TL_HORIZON", "JUDGE_CAP_LIMIT"}, repr(names))
        self.assertIn("JUDGE_CAP_LIMIT", inspect.getsource(km._judging_assemble), "the cap is read at assembly")


# ── the LIVE-lane memo: a live lane's segment part is derived once and served while its inputs stand ──
def _live_row(since):
    return {"state": "waiting", "since": since, "model": "", "effort": "", "context": None,
            "compactPct": None, "color": "#336699", "mode": ""}


def _zero(stats):
    for k in list(stats):
        stats[k] = 0


class LaneMemoBase(unittest.TestCase):
    """A fresh state root per test (jd._rebind_state, so each test's shared store view is its own and its
    override journal dies with the root), the memos emptied, one LIVE lane with one finished turn. The band
    carries this build's own artifact marks (_run_judging stubbed to hand them through), read from the frame's
    compact per-lane judging map (j = judge, kd = kind, x = text)."""

    def setUp(self):
        self._saved_state, self._saved_proj, self._saved_names = km.jd.STATE, km.jd.PROJECTS, km.NAMES
        self._td = tempfile.mkdtemp()
        km.jd._rebind_state(Path(self._td))
        km.jd.PROJECTS = Path(self._td) / "projects"
        km.NAMES = km.jd.NAMES
        km.jd._discover_cache.clear(); km.jd._PARSE_CACHE.clear(); km.jd._CHAIN_MEMO.clear()
        km._parse_cache.clear(); km._BARS_COMPLAINED.clear()
        km._dead_lane_memo.clear(); km._delta_entry_memo.clear()
        km._downtime[:] = []
        self._saved = {nm: getattr(km, nm) for nm in (
            "_sdk", "_run_judging", "TL_HORIZON", "JUDGE_CAP_LIMIT", "_LANES_MEMO_MAX", "_lane_segments", "_segs_seam",
            "_derive_judging_marks", "_judging_assemble", "_captions", "_parse", "_merge_live_atoms", "_bind_message_execs")}
        self._saved_backend = km.Sessions.backend_for
        self._saved_file_key = km.jd._file_key
        km._lanes_memo.clear(); _zero(km._lanes_stats)
        km._lane_prefix_memo.clear()                 # the prefix memo (2026-09-12) holds turn keys that repeat across tests
        km._sdk = lambda: None
        km._run_judging = lambda t0, alive, semantic: [dict(m) for m in semantic]
        self.now = int(time.time())
        self.t0 = self.now - 600
        self.cdir = str(Path(self._td) / "work")
        self.proj = km.jd._proj_dir(self.cdir)
        self.proj.mkdir(parents=True, exist_ok=True)
        km.jd.NAMES.mkdir(parents=True, exist_ok=True)
        self.recs = [_rec("user", self.t0, "u1", None, "make the retry loop back off"),
                     _rec("assistant", self.t0 + 20, "a1", "u1", "Added exponential backoff.")]
        self.add_lane(LIVE_SID, "web", self.recs)

    def tearDown(self):
        for nm, v in self._saved.items():
            setattr(km, nm, v)
        km.Sessions.backend_for = self._saved_backend
        km.jd._file_key = self._saved_file_key
        km.jd._SHARED_OFF[0] = False
        km._lanes_memo.clear(); _zero(km._lanes_stats)
        km._lane_prefix_memo.clear()                 # the prefix memo (2026-09-12) holds turn keys that repeat across tests
        km._dead_lane_memo.clear(); km._downtime[:] = []
        km.jd._rebind_state(self._saved_state)
        km.jd.PROJECTS = self._saved_proj
        km.NAMES = self._saved_names
        shutil.rmtree(self._td, ignore_errors=True)

    # fixtures
    def tpath(self, sid=LIVE_SID):
        return self.proj / (sid + ".jsonl")

    def add_lane(self, sid, name, recs):
        (km.jd.NAMES / sid).write_text("%s\t%s\n" % (name, self.cdir))
        self.tpath(sid).write_text("\n".join(json.dumps(r) for r in recs) + "\n")
        km.jd._discover_cache.clear()

    def append_records(self, recs, sid=LIVE_SID):
        with self.tpath(sid).open("a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def seg_id(self, sid=LIVE_SID):
        p = str(self.tpath(sid))
        session = km.em.parse_session(p, rompuuid=sid, candidate_files=[p], now=self.now)
        return km.em.segments(session["turns"][0])[0]["id"]

    def mint_store(self, sid=LIVE_SID, text="Back off the retries", t=None):
        """A store with one minted top, published by save_goals: a FrozenStore from load_goals_shared after."""
        s = km.jd.load_goals(sid)
        km.jd.apply_plan(s, "s1", t or self.t0, [{"do": "mint", "why": "x", "text": text}], [])
        km.jd.rollup_status(s, session_closed=False)
        km.jd.save_goals(sid, s)
        return s

    def write_archive(self, t, headline, sid=LIVE_SID):
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (sid + ".json")).write_text(json.dumps({"t": t, "headline": headline}))

    def live_map(self, *sids):
        return {s: _live_row(self.now - 100) for s in sids}

    def build(self, live_map=None, with_bars=True, live_only=False, now=None):
        return km.build_timeline(self.now if now is None else now, self.live_map(LIVE_SID) if live_map is None else live_map,
                                 with_bars=with_bars, live_only=live_only)

    def stats(self):
        return km._lanes_memo_report()

    def outcomes(self):
        st = self.stats()
        return {k: st[k] for k in ("hit", "miss", "live_tail", "complain_skip", "unshared_skip")}

    def dead(self):
        st = self.stats()
        return {k: st[k] for k in ("dead_serve", "dead_miss", "dead_failed_serve")}

    def lane(self, tl, sid=LIVE_SID):
        return next(l for l in tl["sessions"] if l["id"] == sid)

    def marks(self, tl, sid=LIVE_SID, judge=None):
        return [m for m in tl["judging"].get(sid, []) if judge is None or m["j"] == judge]

    def spy_segments(self):
        calls, real = [], km._lane_segments

        def spy(*a, **k):
            calls.append(a[0])
            return real(*a, **k)
        km._lane_segments = spy
        return calls


class HitsAndEquality(LaneMemoBase):
    def test_an_unchanged_live_lane_is_served_and_equals_the_uncached_loop(self):
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        self.mint_store()
        self.write_archive(self.now - 50, "retry loop")
        seams, real_seams = [], km._segs_seam
        km._segs_seam = lambda turn, store: (seams.append(1), real_seams(turn, store))[1]
        tl1 = self.build()
        self.assertEqual(len(seams), 1, "the first build walks the lane's turns")
        tl2 = self.build()
        self.assertEqual(len(seams), 1, "the second build walks nothing: a lookup served the lane")
        self.assertEqual(self.outcomes(), {"hit": 1, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertIs(tl2["turns"][LIVE_SID], tl1["turns"][LIVE_SID], "the same bars list rides into turns[sid]")
        self.assertTrue(all(a is b for a, b in zip(tl1["turns"][LIVE_SID], tl2["turns"][LIVE_SID])), "and the same bar dicts")
        # what the memo served is what the uncached loop returns now, on the same objects
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals = km.jd.load_goals_shared(LIVE_SID)
        caps = km._captions(LIVE_SID)
        bars, seg_ends, last_t, compactions, cap_marks, other_marks, nsegs, complained = km._lane_segments(
            LIVE_SID, session, goals, caps, True, None)
        self.assertEqual(json.dumps(tl2["turns"][LIVE_SID]), json.dumps(bars))
        self.assertEqual(set(bars[0]), {"id", "start", "end", "p", "w", "r", "q", "c"}, "the compact wire bar, defaults omitted")
        self.assertEqual(json.dumps(tl2["sessions"]), json.dumps(tl1["sessions"]), "the badge row, per build, agrees")
        self.assertEqual(json.dumps(tl2["judging"]), json.dumps(tl1["judging"]))
        want = []
        km._derive_judging(LIVE_SID, caps, goals, self.now - km.TL_HORIZON, want, seg_ends)
        self.assertEqual([(m["j"], m["x"]) for m in self.marks(tl2)], [(m["judge"], m["text"]) for m in want],
                         "the marks on a hit are the derivation's")
        self.assertEqual({m["j"] for m in self.marks(tl2)}, {"captioner", "planner", "archiver"})
        self.assertEqual(self.lane(tl2)["since"], self.now - 100, "a live lane's since is the liveness snapshot's")
        st = self.stats()
        self.assertEqual((st["segs_hit"], st["segs_miss"], st["entries"], nsegs), (1, 1, 1, 1))

    def test_a_hit_lane_still_runs_the_parse_the_merge_and_the_captions_read(self):
        counts = {"parse": 0, "merge": 0, "caps": 0}
        real_parse, real_merge, real_caps = self._saved["_parse"], self._saved["_merge_live_atoms"], self._saved["_captions"]
        km._parse = lambda *a, **k: (counts.__setitem__("parse", counts["parse"] + 1), real_parse(*a, **k))[1]
        km._merge_live_atoms = lambda *a, **k: (counts.__setitem__("merge", counts["merge"] + 1), real_merge(*a, **k))[1]
        km._captions = lambda *a, **k: (counts.__setitem__("caps", counts["caps"] + 1), real_caps(*a, **k))[1]
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(counts, {"parse": 2, "merge": 2, "caps": 2},
                         "the chip reads the merged parse and the captions are the key's input: a hit skips only the loop")

    def test_the_held_bars_survive_the_postal_join_unchanged(self):
        tl = self.build()
        before = json.dumps(tl["turns"][LIVE_SID])
        km._bind_message_execs([{"id": "m1", "toId": LIVE_SID, "from": "api", "sent": self.t0 - 5, "pending": True}],
                               tl["turns"], {})
        self.assertEqual(json.dumps(tl["turns"][LIVE_SID]), before, "_bind_message_execs writes to the messages only")
        tl2 = self.build()
        self.assertIs(tl2["turns"][LIVE_SID], tl["turns"][LIVE_SID])
        self.assertEqual(json.dumps(tl2["turns"][LIVE_SID]), before)

    def test_the_badge_row_is_per_build_on_a_hit(self):
        tl1 = self.build()
        self.assertFalse(self.lane(tl1)["postalServiceOff"])
        km._set_session_flag(LIVE_SID, "postalServiceOff", True)
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "a flag is a badge-row input, not a segment input")
        self.assertTrue(self.lane(tl2)["postalServiceOff"], "and the row carries it at once")

    def test_a_hit_hands_the_binder_the_whole_prompts(self):
        # T278b: a bar carries the first line of its prompt, capped, and the binder's sender heuristic reads the WHOLE
        # text through the builder's prompts map, so a delivery that names its sender on a later line binds. The memo
        # serves the map beside the bars; without it every served build would bind on the first line alone.
        self.add_lane(LIVE_SID, "web", [_rec("user", self.t0, "u1", None, "make the retry loop back off\nfrom api: the cap is two minutes"),
                                         _rec("assistant", self.t0 + 20, "a1", "u1", "Added exponential backoff.")])
        handed, real = [], self._saved["_bind_message_execs"]
        km._bind_message_execs = lambda messages, turns, prompts=None: (handed.append(prompts), real(messages, turns, prompts))[1]
        self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        bar = tl2["turns"][LIVE_SID][0]
        self.assertEqual(bar["q"], "make the retry loop back off", "the wire carries the first line")
        self.assertEqual(handed[1].get(bar["id"]), "make the retry loop back off\nfrom api: the cap is two minutes",
                         "the served build hands the binder the whole prompt")
        self.assertEqual(handed[1], handed[0])
        msg = {"id": "m1", "toId": LIVE_SID, "from": "api", "fromOrig": "api", "sent": self.t0 - 5, "pending": True}
        real([msg], tl2["turns"], handed[1])
        self.assertEqual((msg["exec"], msg["pending"]), (bar["start"], False), "the sender named on the second line binds")
        bare = {"id": "m2", "toId": LIVE_SID, "from": "api", "fromOrig": "api", "sent": self.t0 - 5, "pending": True}
        real([bare], tl2["turns"], {})
        self.assertTrue(bare["pending"], "the first line alone does not name the sender")

    def test_the_judging_band_reader_leaves_the_held_marks_unchanged(self):
        # the held marks ride into `semantic`, which _run_judging reads for its gloss text and never writes to: the
        # real reader runs over a hit here (the usage log under this root is empty, so the band it builds is bare)
        self.mint_store()
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        self.build()
        before = json.dumps(km._lanes_memo[LIVE_SID][4][4:6])
        self.assertIn("Added backoff", before)
        km._run_judging = self._saved["_run_judging"]
        tl = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(json.dumps(km._lanes_memo[LIVE_SID][4][4:6]), before, "the reader wrote nothing into the held marks")
        self.assertEqual(self.marks(tl), [], "the band is the usage log's, empty under this root")

    def test_two_threads_deriving_one_lane_leave_one_exact_entry(self):
        # the derivation runs unlocked; the lock covers get, put, evict and the counters, so two builds that miss
        # together both store an exact entry and the last wins. The parse cache is warmed first so both threads
        # derive from ONE parse object: two concurrent misses in _parse each store their own, and the entry that
        # survives can hold the object _parse_cache did not, which the next build drops and derives once more. With
        # one parse object the next build's serve is this memo's property, not the parse cache's race.
        km._parse(str(self.tpath()), LIVE_SID, self.now)
        gate = threading.Barrier(2)
        frames, errors = [], []

        def run():
            try:
                gate.wait(5)
                frames.append(self.build())
            except Exception as e:      # a raise in a thread would otherwise be silent
                errors.append(e)
        ts = [threading.Thread(target=run) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(30)
        self.assertEqual(errors, [])
        self.assertEqual(len(frames), 2)
        self.assertEqual(self.stats()["entries"], 1)
        self.assertEqual(json.dumps(frames[0]["turns"]), json.dumps(frames[1]["turns"]))
        st = self.outcomes()
        self.assertEqual(st["hit"] + st["miss"], 2, "both missed, or the second found the first's entry: either is exact")
        tl = self.build()
        self.assertEqual(self.outcomes()["hit"], st["hit"] + 1, "the next build is served from the surviving entry")
        self.assertEqual(json.dumps(tl["turns"]), json.dumps(frames[0]["turns"]))


class EveryInputBusts(LaneMemoBase):
    """One test per input in the memo's key: change only that input, observe a miss and the new content."""

    def test_a_transcript_append_moves_the_parse_object(self):
        tl1 = self.build()
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(tl1["turns"][LIVE_SID]), 1)
        self.assertEqual(len(tl2["turns"][LIVE_SID]), 2, "the appended turn is a second bar")
        self.assertEqual(self.stats()["entries"], 1, "the new entry replaced the old")

    def test_a_states_write_moves_the_parse_object(self):
        # the states log is read into the parse (idle atoms) and sits in _parse's key
        self.build()
        (km.jd.STATE / "states").mkdir(exist_ok=True)
        (km.jd.STATE / "states" / (LIVE_SID + ".jsonl")).write_text(json.dumps({"t": self.t0 + 30, "state": "waiting"}) + "\n")
        self.build()
        self.assertEqual(self.outcomes()["miss"], 2)

    def test_a_store_publish_is_a_new_frozen_store(self):
        self.mint_store(text="First goal")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="planner")], ["First goal"])
        s = km.jd.load_goals(LIVE_SID)
        km.jd.apply_plan(s, "s2", self.t0 + 100, [{"do": "mint", "why": "x", "text": "Second goal"}], [])
        km.jd.rollup_status(s, session_closed=False)
        km.jd.save_goals(LIVE_SID, s)
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(sorted(m["x"] for m in self.marks(tl2, judge="planner")), ["First goal", "Second goal"])

    def test_a_journal_append_is_a_new_store_version(self):
        s = self.mint_store()
        nid = next(iter(s["nodes"]))
        self.build()
        km.jd.append_block(LIVE_SID, nid, "nudge", "no answer to the status ask", self.now)
        self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 2, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})

    def test_a_captions_append_moves_the_captions_key(self):
        seg = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "first summary")
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "first summary")
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "second summary")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(tl2["turns"][LIVE_SID][0]["c"], "second summary")
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["second summary"])

    def test_a_caption_landing_between_the_stat_and_the_read_misses_on_the_next_build(self):
        # The captions key is the file's stat taken BEFORE _captions reads it. A row that lands between the two is
        # read by this build and held under the old key, so the next build's stat misses and shows it; a stat taken
        # after the read would hold the old rows under the new key and serve the stale caption until another input moved.
        seg = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg, "segment", self.t0, "first summary")
        real, landed = self._saved["_captions"], []

        def late_row(fsid):
            caps = real(fsid)
            if not landed:
                landed.append(1)
                km.jd.append_caption(fsid, seg, "segment", self.t0, "landed after the read")
            return caps
        km._captions = late_row
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "first summary", "this build read the file before the row landed")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "the file's stat moved: the lane misses")
        self.assertEqual(tl2["turns"][LIVE_SID][0]["c"], "landed after the read")
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["landed after the read"])
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "and holds again once the key and the rows agree")

    def test_an_archive_rewrite_moves_the_archive_key(self):
        self.write_archive(self.now - 50, "retry loop")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="archiver")], ["retry loop"])
        self.write_archive(self.now - 40, "retry loop with a two minute cap")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual([(m["t"], m["x"]) for m in self.marks(tl2, judge="archiver")],
                         [(self.now - 40, "retry loop with a two minute cap")])

    def test_an_archive_appearing_after_a_build_misses(self):
        tl1 = self.build()
        self.assertEqual(self.marks(tl1, judge="archiver"), [])
        self.write_archive(self.now - 50, "retry loop")
        tl2 = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(self.marks(tl2, judge="archiver")), 1)

    def test_a_host_suspension_moves_the_downtime_tuple(self):
        self.build()
        km._downtime.append((self.now - 20000, self.now - 19000))        # a sleep outside every segment: the same bars,
        self.build()                                                     # a different key
        self.assertEqual(self.outcomes()["miss"], 2)
        km._downtime[:] = [(self.now - 20000, self.now - 19000)]         # the same content rebound: the same key
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        km._downtime[:] = [(self.now - 20000, self.now - 18000)]         # a slice-assign moves it too
        self.build()
        self.assertEqual(self.outcomes()["miss"], 3)
        self.assertEqual(self.stats()["entries"], 1)

    def test_a_suspension_inside_the_segment_splits_the_bar_on_the_next_build(self):
        self.build()
        km._downtime[:] = [(self.t0 + 5, self.t0 + 10)]
        tl = self.build()
        self.assertEqual(self.outcomes()["miss"], 2)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 2, "the bar is cut at the nap on the very next build")
        self.assertTrue(tl["turns"][LIVE_SID][1].get("t"), "the second piece is a continuation: no new prompt dot")

    def test_liveness_is_in_the_key_and_a_dead_lane_leaves_this_memo(self):
        self.build(live_map=self.live_map(LIVE_SID))
        tl = self.build(live_map={})                                         # the process ended: a dead lane in the window
        self.assertFalse(self.lane(tl)["live"])
        # a dead lane is the dead-lane memo's: derived once by _lane_segments directly (dead_miss) and handed to it,
        # whose populate drops the entry this memo held from the lane's live days (it keys on the parse the
        # dead-lane memo lets go of)
        self.assertEqual(self.outcomes()["miss"], 1, "one miss, the live build's")
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 0})
        self.assertIn(LIVE_SID, km._dead_lane_memo, "the dead build handed the lane to the dead-lane memo")
        self.assertNotIn(LIVE_SID, km._lanes_memo, "and this memo dropped the entry from the lane's live days")
        self.assertFalse(any(p.endswith("/" + LIVE_SID + ".jsonl") for p in km._parse_cache), "so the parse can go")
        served = km._VIEW_STATS.get("laneServe", 0)
        self.build(live_map={})
        self.assertEqual(km._VIEW_STATS.get("laneServe", 0), served + 1, "a dead-lane serve still counts under views")
        self.assertEqual(self.dead()["dead_serve"], 1, "and on this block beside it")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})

    def test_an_open_turn_reads_open_only_on_a_live_lane(self):
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "keep going"),
                             {"type": "assistant", "timestamp": _iso(self.t0 + 110), "uuid": "a2", "parentUuid": "u2",
                              "message": {"role": "assistant", "stop_reason": None,
                                          "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}]}}])
        tl_live = self.build(live_map=self.live_map(LIVE_SID))
        tl_dead = self.build(live_map={})
        self.assertEqual(self.outcomes()["miss"], 1, "the live build's; the dead build is derived outside this memo")
        self.assertEqual(self.dead()["dead_miss"], 1)
        self.assertTrue(tl_live["turns"][LIVE_SID][-1].get("u"), "the live turn's last piece is open")
        self.assertNotIn("u", tl_dead["turns"][LIVE_SID][-1], "a dead lane never draws an open bar")

    def fork_fixture(self):
        """A parent lane and a child forked from it at fork_t: the child's transcript carries the parent's history
        verbatim, and the backend reports the fork. Returns fork_t."""
        fork_t = self.now - 300
        shared = [_rec("user", self.now - 600, "u1", None, "how should the retry loop back off?"),
                  _rec("assistant", self.now - 580, "a1", "u1", "Use exponential backoff."),
                  # a compaction inside the copied history, and a turn whose work ends AT the fork time: the
                  # parent's while its lane is in the build (the clip is `<=`), the child's own once it leaves
                  {"type": "system", "subtype": "compact_boundary", "uuid": "cb1", "parentUuid": None,
                   "logicalParentUuid": "a1", "timestamp": _iso(self.now - 500), "compactMetadata": {"trigger": "auto"}},
                  _rec("user", fork_t - 20, "u2", "cb1", "and the cap?"),
                  _rec("assistant", fork_t, "a2", "u2", "Two minutes.")]
        own = [_rec("user", fork_t + 60, "u9", "a2", "and inside the fork?"),
               _rec("assistant", fork_t + 80, "a9", "u9", "Cap the delay at two minutes.")]
        self.add_lane(LIVE_PARENT, "parent", shared)
        self.add_lane(LIVE_CHILD, "child", shared + own)
        kids = {LIVE_PARENT: [{"sid": LIVE_CHILD, "name": "child", "cut": "a2", "t": fork_t}]}

        class FakeBe:
            def fork_children(self):
                return kids

            def owns(self, sid):
                return False

            def pending_cut(self, sid):
                return ""

            def __getattr__(self, name):
                return lambda *a, **k: None
        km._sdk = lambda: FakeBe()
        return fork_t

    def test_a_parent_lane_leaving_the_build_moves_a_live_childs_branch_clip(self):
        fork_t = self.fork_fixture()
        tl1 = self.build(live_map=self.live_map(LIVE_SID, LIVE_PARENT, LIVE_CHILD))
        self.assertIn(fork_t, [b["end"] for b in tl1["turns"][LIVE_PARENT]], "the parent draws the turn that ends at the fork time")
        self.assertEqual([c["t"] for c in self.lane(tl1, LIVE_PARENT)["compactions"]], [self.now - 500], "and its compaction marker")
        self.assertTrue(all(b["start"] > fork_t for b in tl1["turns"][LIVE_CHILD]),
                        "clipped while the parent is a lane, the turn ending AT the fork time included")
        self.assertEqual(self.lane(tl1, LIVE_CHILD)["compactions"], [], "the copied boundary is the parent's marker")
        self.assertEqual(self.outcomes()["miss"], 3)
        self.build(live_map=self.live_map(LIVE_SID, LIVE_PARENT, LIVE_CHILD))
        self.assertEqual(self.outcomes()["hit"], 3)
        (km.jd.NAMES / LIVE_PARENT).unlink()                             # the parent leaves the build: no name, no row
        km.jd._discover_cache.clear()
        tl3 = self.build(live_map=self.live_map(LIVE_SID, LIVE_CHILD))
        self.assertEqual(self.outcomes()["miss"], 4, "the child's clip moved: one more miss")
        self.assertEqual(self.outcomes()["hit"], 4, "the other lane still hits")
        self.assertTrue(any(b["start"] < fork_t for b in tl3["turns"][LIVE_CHILD]), "the whole story, the parent gone")
        self.assertEqual([c["t"] for c in self.lane(tl3, LIVE_CHILD)["compactions"]], [self.now - 500],
                         "the copied boundary is the child's marker now")
        self.assertEqual(self.stats()["evict"], 1, "the parent's entry left with its lane")
        self.assertEqual(self.stats()["entries"], 2)


class NotHeld(LaneMemoBase):
    def test_a_live_tail_is_derived_and_not_held(self):
        fresh = {"type": "assistant", "uuid": "live-1", "t": self.t0 + 40, "session_id": LIVE_SID, "fsid": None,
                 "parentUuid": "a1", "message": {"role": "assistant", "stop_reason": None,
                                                 "content": [{"type": "text", "text": "Streaming a reply."}]}}

        class FakeBE:
            def live_atoms(self, sid):
                return [dict(fresh)]

            def prune_live(self, *a, **k):
                pass
        km.Sessions.backend_for = lambda sid: FakeBE()
        tl1 = self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 2, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0, "a merged tail is a new object every build: nothing to hold")
        self.assertEqual(tl1["turns"][LIVE_SID][0]["end"], self.t0 + 40, "the live atom extends the bar")
        self.assertIsNot(tl2["turns"][LIVE_SID], tl1["turns"][LIVE_SID])

    def test_a_failed_parse_is_derived_says_so_once_and_is_not_held(self):
        km._parse = lambda path, sid, now: (_ for _ in ()).throw(OSError("unreadable"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(tl["turns"][LIVE_SID], [])
        self.assertEqual(err.getvalue().count("parse failed"), 1)

    def test_a_private_store_with_content_is_not_held(self):
        self.mint_store()
        km.jd._SHARED_OFF[0] = True                                      # the shared cache is off: load_goals' private store
        try:
            tl = self.build(); self.build()
            self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 2})
            self.assertEqual(self.stats()["entries"], 0)
            self.assertEqual(len(self.marks(tl, judge="planner")), 1, "derived all the same")
        finally:
            km.jd._SHARED_OFF[0] = False

    def test_a_lane_without_a_store_hits_under_the_empty_rule(self):
        self.assertFalse((km.jd.GOALDIR / (LIVE_SID + ".json")).exists())
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "no seams, no nodes: the store's identity does not matter")
        self.mint_store()
        self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "a published store is a new input")

    def test_a_faulted_store_is_derived_and_not_held(self):
        """A store that cannot be read (goals None: build_timeline complained) shares no key with the lane that has
        no store: _lane_segments derives NO marks for None, while the empty store still yields the captioner's and
        the archiver's. Both keyed as "empty" once, so a lane whose unreadable store was then removed was served the
        faulted build's empty marks until another input moved. A faulted store is derived and not held (the goals
        stage complained: complain_skip), and the lane without a store derives on the next build."""
        self.write_archive(self.now - 50, "retry loop")
        store = km.jd.GOALDIR / (LIVE_SID + ".json")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"nodes": {}, "status": {}}))
        store.chmod(0)                                 # unreadable: load_goals_shared raises, the boundary answers None
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl1 = self.build()
        finally:
            store.chmod(0o600)
        self.assertIn("goals failed", err.getvalue())
        self.assertEqual(self.marks(tl1), [], "a faulted store: the lane's marks are missing, loudly")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        store.unlink()                                 # no store now: load_goals' fresh private store, the empty key
        tl2 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="archiver")], ["retry loop"],
                         "derived, not served the faulted build's marks: the archiver's mark is this lane's")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})

    def test_a_held_lane_whose_store_faults_is_derived(self):
        """The reverse transition: a lane held with no store (the archiver's mark in its entry) whose store then
        appears unreadable is derived, as a fresh derivation of a faulted store is (no marks), and not served the
        entry's marks; the entry stands, and serves again once the store reads as absent."""
        self.write_archive(self.now - 50, "retry loop")
        tl1 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl1, judge="archiver")], ["retry loop"])
        self.assertEqual(self.stats()["entries"], 1)
        store = km.jd.GOALDIR / (LIVE_SID + ".json")
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text(json.dumps({"nodes": {}, "status": {}}))
        store.chmod(0)
        err = io.StringIO()
        try:
            with redirect_stderr(err):
                tl2 = self.build()
        finally:
            store.chmod(0o600)
        self.assertIn("goals failed", err.getvalue())
        self.assertEqual(self.marks(tl2), [], "the store faulted: derived without marks, never the held entry's")
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 1, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 1, "a skip stores nothing and drops nothing")
        store.unlink()
        tl3 = self.build()
        self.assertEqual([m["x"] for m in self.marks(tl3, judge="archiver")], ["retry loop"],
                         "no store again: the standing entry serves, and it equals a fresh derivation")
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(json.dumps(tl3["judging"][LIVE_SID]), json.dumps(tl1["judging"][LIVE_SID]),
                         "the served frame is the first build's derivation, on the same inputs")
        self.assertEqual(json.dumps(tl3["turns"][LIVE_SID]), json.dumps(tl1["turns"][LIVE_SID]))

    def test_a_lane_without_captions_hits_under_the_empty_rule(self):
        self.assertFalse((km.jd.CAPDIR / (LIVE_SID + ".jsonl")).exists())
        self.build(); self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual(self.stats()["entries"], 1)

    def test_a_complaining_seams_stage_is_not_held(self):
        km._segs_seam = lambda turn, store: (_ for _ in ()).throw(ValueError("malformed seam"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 2, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the seams fell back to the plain segments; the bar still draws")
        self.assertIn("seams failed", err.getvalue())

    def test_a_seams_field_that_is_a_truthy_scalar_costs_the_seams_not_the_frame(self):
        """RED FIRST (the post-merge review of the lane prefix's inputs by value, 2026-10-06): the prefix fingerprints a
        shared store's seams by value, outside the turn loop's seams guard, so a store whose seams field is a truthy
        scalar where a list belongs (1, true, 1.5) raised TypeError out of the whole build, and no lane got a bars frame.
        Such seams now cost the lane its prefix: it derives whole, the seams guard complains for it alone, and the frame
        ships with its bars, as it did before the seams were keyed by value."""
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        p = km.jd.GOALDIR / (LIVE_SID + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        for i, bad in enumerate((1, True, 1.5)):
            p.write_text(json.dumps({"nodes": {}, "status": {}, "seams": bad}))
            os.utime(p, (self.now - 300 + i, self.now - 300 + i))
            store = km.jd.load_goals_shared(LIVE_SID)
            self.assertIsInstance(store, km.jd.FrozenStore, "(the premise) %r: a shared store" % bad)
            self.assertEqual(store.get("seams"), bad, "(the premise) %r: the store carries the scalar" % bad)
            err = io.StringIO()
            with redirect_stderr(err):
                tl = self.build()
            self.assertEqual(len(tl["turns"][LIVE_SID]), 2, "%r: the frame ships with the lane's bars" % bad)
            self.assertIn("seams failed", err.getvalue(), "%r: the seams guard complains for the lane" % bad)
            self.assertNotIn(LIVE_SID, km._lane_prefix_memo, "%r: no prefix is held under seams nobody can read" % bad)
        self.assertEqual(self.outcomes()["complain_skip"], 3, "each build derived the lane and held nothing")

    def test_a_complaining_marks_stage_is_not_held(self):
        km._derive_judging_marks = lambda *a, **k: (_ for _ in ()).throw(KeyError("t"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build(); self.build()
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertEqual(self.stats()["entries"], 0)
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1)
        self.assertEqual(self.marks(tl), [], "this lane lost its marks; the frame shipped")
        self.assertIn("judging-marks failed", err.getvalue())

    def test_a_caption_row_with_a_string_time_costs_the_marks_not_the_frame(self):
        # the horizon comparisons run at assembly, outside the lane's guard; a time they cannot compare fails the
        # marks stage at derivation (not held, the lane says so) instead of raising out of build_timeline every build
        seg = self.seg_id()
        km.jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.CAPDIR / (LIVE_SID + ".jsonl")).write_text(
            json.dumps({"id": seg, "grain": "segment", "t": "not a number", "caption": "typed time"}) + "\n")
        err = io.StringIO()
        with redirect_stderr(err):
            tl1 = self.build()
            tl2 = self.build()
        for tl in (tl1, tl2):
            self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the lane is drawn")
            self.assertEqual(tl["turns"][LIVE_SID][0]["c"], "typed time", "the caption itself still serves the bar")
            self.assertEqual(self.marks(tl), [], "the lane is mark-less")
        self.assertEqual(self.outcomes()["complain_skip"], 2, "not held: the miss and the following build both derive")
        self.assertEqual(self.stats()["entries"], 0)
        self.assertIn("judging-marks failed", err.getvalue())

    def test_an_archive_with_a_string_time_costs_the_marks_not_the_frame(self):
        km.jd.ARCHDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.ARCHDIR / (LIVE_SID + ".json")).write_text(json.dumps({"t": "soon", "headline": "h"}))
        err = io.StringIO()
        with redirect_stderr(err):
            tl1 = self.build()
            tl2 = self.build()
        for tl in (tl1, tl2):
            self.assertEqual(len(tl["turns"][LIVE_SID]), 1)
            self.assertEqual(self.marks(tl), [])
        self.assertEqual(self.outcomes()["complain_skip"], 2)
        self.assertIn("judging-marks failed", err.getvalue())

    def test_the_assembly_is_guarded_per_lane_at_its_call_site(self):
        km._judging_assemble = lambda *a, **k: (_ for _ in ()).throw(TypeError("unorderable"))
        err = io.StringIO()
        with redirect_stderr(err):
            tl = self.build()
        self.assertEqual(len(tl["turns"][LIVE_SID]), 1, "the frame ships with the lane's bars")
        self.assertEqual(self.marks(tl), [])
        self.assertIn("judging-marks failed", err.getvalue())

    def test_captions_read_with_no_file_to_stat_are_derived_and_not_held(self):
        # the captions key is the file's stat taken before the read; rows read when that stat found no file (the
        # file appeared between the two) have no key this build can hold them under, so the lane is derived and not
        # held, and the next build, whose stat sees the file, holds it
        seg, real, landed = self.seg_id(), self._saved["_captions"], []

        def appears_before_the_read(fsid):
            if not landed:
                landed.append(1)
                km.jd.append_caption(fsid, seg, "segment", self.t0, "landed between the stat and the read")
            return real(fsid)
        km._captions = appears_before_the_read
        tl1 = self.build()
        self.assertEqual(tl1["turns"][LIVE_SID][0]["c"], "landed between the stat and the read", "this build read the row")
        self.assertEqual((self.outcomes()["miss"], self.stats()["entries"]), (1, 0), "and had no key to hold it under")
        self.build()
        self.assertEqual((self.outcomes()["miss"], self.stats()["entries"]), (2, 1), "the next build's stat sees the file: held")
        self.build()
        self.assertEqual(self.outcomes()["hit"], 1)

    def test_a_failed_archive_stat_stores_nothing(self):
        real = self._saved_file_key
        arch = str(km.jd.STATE / "archive" / (LIVE_SID + ".json"))
        km.jd._file_key = lambda p: object() if p == arch else real(p)
        self.build(); self.build()
        self.assertEqual(self.outcomes()["miss"], 2, "a sentinel matches nothing")
        self.assertEqual(self.stats()["entries"], 0, "and nothing is stored under it")

    def test_the_skeleton_build_bypasses_the_memo(self):
        self.build(with_bars=False)
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 0)
        self.build(with_bars=True)
        self.assertEqual(self.outcomes()["miss"], 1)
        self.build(with_bars=False)
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 1, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.stats()["entries"], 1, "a skeleton neither reads nor evicts")


class ClockAtAssembly(LaneMemoBase):
    def test_a_caption_row_without_a_time_is_dropped_on_a_hit_as_on_a_miss(self):
        seg = self.seg_id()
        km.jd.CAPDIR.mkdir(parents=True, exist_ok=True)
        (km.jd.CAPDIR / (LIVE_SID + ".jsonl")).write_text(
            json.dumps({"id": seg + "#p", "grain": "prompt", "caption": "no time on this row"}) + "\n"
            + json.dumps({"id": seg, "grain": "segment", "t": self.t0, "caption": "timed"}) + "\n")
        tl1 = self.build()
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        for tl in (tl1, tl2):
            self.assertEqual([m["x"] for m in self.marks(tl, judge="captioner")], ["timed"])
            self.assertEqual(tl["turns"][LIVE_SID][0]["m"], "no time on this row", "the caption itself still serves the dot")

    def test_the_horizon_applies_per_build_on_a_hit(self):
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.now - 3000, "an older caption")
        tl1 = self.build()
        self.assertEqual(len(self.marks(tl1, judge="captioner")), 1)
        km.TL_HORIZON = 1000                                             # the horizon moves past the mark
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1, "no re-derivation: the horizon is applied at assembly")
        self.assertEqual(self.marks(tl2, judge="captioner"), [])

    def test_the_caption_cap_applies_per_build_on_a_hit(self):
        seg = self.seg_id()
        for i in range(3):
            km.jd.append_caption(LIVE_SID, seg + ("" if i == 0 else "#p%d" % i), "segment", self.t0 + i, "caption %d" % i)
        tl1 = self.build()
        self.assertEqual(len(self.marks(tl1, judge="captioner")), 3)
        km.JUDGE_CAP_LIMIT = 1
        tl2 = self.build()
        self.assertEqual(self.outcomes()["hit"], 1)
        self.assertEqual([m["x"] for m in self.marks(tl2, judge="captioner")], ["caption 2"], "the newest survives the cap")

    def test_the_segment_derivation_reads_no_clock(self):
        class NoClock:
            def time(self):
                raise AssertionError("the derivation read the clock")

            def __getattr__(self, name):
                return getattr(time, name)
        self.mint_store()
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals, caps = km.jd.load_goals_shared(LIVE_SID), km._captions(LIVE_SID)
        real = km.time
        km.time = NoClock()
        try:
            value = km._lane_segments(LIVE_SID, session, goals, caps, True, None)
        finally:
            km.time = real
        self.assertEqual((len(value[0]), value[6], value[7]), (1, 1, False))
        self.assertTrue(value[4] and value[5], "the marks came out unfiltered")
        tree = ast.parse(inspect.getsource(km._lane_segments))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {"now", "time", "TL_HORIZON", "JUDGE_CAP_LIMIT"}, repr(names))


class Eviction(LaneMemoBase):
    def test_entries_leave_with_their_lanes_and_a_live_only_build_evicts_nothing(self):
        self.add_lane(LIVE_SID2, "api", self.recs)
        self.build(live_map=self.live_map(LIVE_SID, LIVE_SID2))
        self.assertEqual(self.stats()["entries"], 2)
        self.build(live_map=self.live_map(LIVE_SID), live_only=True)            # LIVE_SID2 is not in a live-only build
        self.assertEqual(self.stats()["entries"], 2, "a live-only build reads a subset of the lanes and must not evict")
        self.assertEqual(self.stats()["evict"], 0)
        (km.jd.NAMES / LIVE_SID2).unlink()
        km.jd._discover_cache.clear()
        self.build(live_map=self.live_map(LIVE_SID))
        self.assertEqual(self.stats()["entries"], 1)
        self.assertEqual(self.stats()["evict"], 1)
        self.assertEqual(set(km._lanes_memo), {LIVE_SID})

    def test_the_memo_is_bounded_least_recently_served_first(self):
        self.add_lane(LIVE_SID2, "api", self.recs)
        km._LANES_MEMO_MAX = 2
        self.build(live_map=self.live_map(LIVE_SID, LIVE_SID2))                 # both held
        self.assertEqual(set(km._lanes_memo), {LIVE_SID, LIVE_SID2})
        first = next(iter(km._lanes_memo))                               # the FIRST inserted, whichever lane the build drew first
        self.build(live_map=self.live_map(first), live_only=True)                # served: the most recently served, though inserted first
        self.assertEqual(self.outcomes()["hit"], 1)
        self.add_lane(LIVE_SID3, "tests", self.recs)
        self.build(live_map=self.live_map(LIVE_SID3), live_only=True)
        self.assertEqual(set(km._lanes_memo), {first, LIVE_SID3},
                         "the least recently SERVED went, not the first inserted; the served lane stayed")
        self.assertEqual(self.stats()["evict"], 1)
        self.assertEqual(self.stats()["entries"], 2)

    def test_an_entry_whose_parse_is_no_longer_the_builds_is_dropped_when_seen(self):
        self.build()
        self.assertEqual(self.stats()["entries"], 1)
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay")])   # a new parse object
        km._parse = lambda path, sid, now: (_ for _ in ()).throw(OSError("unreadable"))   # and the parse fails
        with redirect_stderr(io.StringIO()):
            self.build()
        self.assertEqual(self.stats()["entries"], 0, "the old entry held a parse the build no longer has")


class DeadLanesOnTheSameBlock(LaneMemoBase):
    """A dead lane bypasses the live-lane memo: derived by _lane_segments directly on a miss and cached in the
    dead-lane memo; its outcomes ride the same /perf block so one block carries every lane."""

    def dead_build(self):
        return self.build(live_map={})

    def test_a_dead_miss_leaves_this_memo_and_the_parse_cache_empty_and_is_not_a_miss_here(self):
        calls = self.spy_segments()
        tl = self.dead_build()
        self.assertEqual(calls, [LIVE_SID], "derived directly")
        self.assertFalse(self.lane(tl)["live"])
        self.assertEqual(self.outcomes(), {"hit": 0, "miss": 0, "live_tail": 0, "complain_skip": 0, "unshared_skip": 0})
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 0})
        self.assertNotIn(LIVE_SID, km._lanes_memo)
        self.assertIn(LIVE_SID, km._dead_lane_memo)
        self.assertFalse(km._dead_lane_memo[LIVE_SID][1]["failed"])
        self.assertFalse(any(p.endswith("/" + LIVE_SID + ".jsonl") for p in km._parse_cache))
        self.dead_build()
        self.assertEqual(calls, [LIVE_SID], "served from the dead-lane memo")
        self.assertEqual(self.dead()["dead_serve"], 1)

    def test_a_failed_dead_parse_is_served_as_failed_until_the_stat_moves(self):
        parses, failing, real = [], [True], self._saved["_parse"]

        def parse(path, sid, now):
            parses.append(path)
            if failing[0]:
                raise OSError("unreadable")
            return real(path, sid, now)
        km._parse = parse
        with redirect_stderr(io.StringIO()):
            tl1 = self.dead_build()
            tl2 = self.dead_build()
        self.assertEqual(len(parses), 1, "parsed once: the failed parse is cached as the empty lane")
        self.assertEqual((tl1["turns"][LIVE_SID], tl2["turns"][LIVE_SID]), ([], []))
        self.assertTrue(km._dead_lane_memo[LIVE_SID][1]["failed"])
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 1, "dead_failed_serve": 1})
        failing[0] = False
        move_ctime(self.tpath())
        tl3 = self.dead_build()
        self.assertEqual(len(parses), 2, "the transcript's stat moved: parsed again")
        self.assertEqual(len(tl3["turns"][LIVE_SID]), 1)
        self.dead_build()
        self.assertEqual(self.dead(), {"dead_serve": 1, "dead_miss": 2, "dead_failed_serve": 1})
        self.assertEqual(self.outcomes()["complain_skip"], 0, "a dead lane's failure is not this memo's outcome")

    def test_a_complaining_dead_lane_is_derived_every_build_and_not_cached(self):
        km._segs_seam = lambda turn, store: (_ for _ in ()).throw(ValueError("malformed seam"))
        with redirect_stderr(io.StringIO()):
            self.dead_build(); self.dead_build()
        self.assertNotIn(LIVE_SID, km._dead_lane_memo)
        self.assertEqual(self.dead(), {"dead_serve": 0, "dead_miss": 2, "dead_failed_serve": 0})


class PerfWiring(LaneMemoBase):
    def test_the_memo_reports_under_perf_memos_lanes(self):
        self.build(); self.build()
        blk = km._PERF_STATS.snapshot()["memos"]["lanes"]
        self.assertEqual(set(blk), {"hit", "miss", "live_tail", "complain_skip", "unshared_skip", "evict", "entries",
                                    "segs_hit", "segs_miss", "prefix_hit", "prefix_segs",
                                    "prefix_partial", "prefix_reset", "prefix_recap", "prefix_recap_new",   # the prefix's inputs by value (2026-10-06)
                                    "dead_serve", "dead_miss", "dead_failed_serve"})
        self.assertEqual((blk["hit"], blk["miss"], blk["entries"], blk["segs_hit"], blk["segs_miss"]), (1, 1, 1, 1, 1))
        self.assertEqual((blk["dead_serve"], blk["dead_miss"], blk["dead_failed_serve"]), (0, 0, 0), "live lanes only so far")
        self.build(live_map={}); self.build(live_map={})
        blk = km._PERF_STATS.snapshot()["memos"]["lanes"]
        self.assertEqual((blk["dead_serve"], blk["dead_miss"], blk["dead_failed_serve"]), (1, 1, 0), "the dead lanes ride the same block")
        self.assertEqual((blk["hit"], blk["miss"]), (1, 1), "and leave the live counters alone")

    def test_the_reference_names_every_key_of_the_block(self):
        doc = (Path(HERE).parent / "docs" / "reference.md").read_text(encoding="utf-8")
        at = doc.index("`lanes` is the timeline's")
        passage = doc[at:doc.index("`msgScan` is", at)]
        for k in km._lanes_memo_report():
            self.assertIn("`%s`" % k, passage, k)

    def test_the_reports_docstring_names_every_key_of_the_block(self):
        """RED FIRST: the report's docstring lists the block's counters for a reader of the code, and it lacked the six
        prefix counters (the two from 2026-09-12 and the four from 2026-10-06) until the post-merge review named them."""
        words = set(re.findall(r"[a-z_]+", km._lanes_memo_report.__doc__))
        for k in km._lanes_memo_report():
            self.assertIn(k, words, k)


def _boundary(t, uuid, parent):
    """A compact_boundary record as Claude Code writes one (parentUuid null, the anchor under logicalParentUuid). It
    parses into a turn of its own, so a lane with one holds a closed turn made of nothing but the marker."""
    return {"type": "system", "subtype": "compact_boundary", "uuid": uuid, "parentUuid": None, "logicalParentUuid": parent,
            "timestamp": _iso(t), "compactMetadata": {"trigger": "auto"}}


class _RowIndex:
    """A stand-in LazyIndex whose rows ARE the atoms: build hands the row back, so a slot's first read is a build and
    every later one a resident read, the shape a restored lane's closed turns have (em._pre_turns_of)."""

    def build(self, row):
        return row

    def uuid_of(self, row):
        return row.get("uuid")


class _CountingAtoms(km.em.LazyAtoms):
    """LazyAtoms that counts every slot read, per list (n) and in all (reads): _at is the one road for indexing, slicing,
    iteration and membership, and on a restored lane each such read is a lock round trip or a row decode."""
    reads = 0

    def __init__(self, index, rows):
        super().__init__(index, rows)
        self.n = 0

    def _at(self, i):
        self.n += 1
        type(self).reads += 1
        return super()._at(i)


def _tool_use(t, uuid, parent, tid):
    """An assistant record that calls a tool (real work: what a seam's tail must hold for apply_seams to split)."""
    return {"type": "assistant", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "assistant", "stop_reason": "tool_use",
                        "content": [{"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "make test"}}]}}


def _tool_result(t, uuid, parent, tid):
    return {"type": "user", "timestamp": _iso(t), "uuid": uuid, "parentUuid": parent,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "ok"}]}}


class PrefixMemo(LaneMemoBase):
    """The lane PREFIX memo (2026-09-12): a live lane whose transcript moved re-derived every turn of its history each
    build (1.19 million segments re-walked in 25 minutes on the devbox, a third of the pusher's time). The closed
    turns before the last one are held per lane and reused; only the tail is derived, and the bars equal a whole
    derivation's.

    Since 2026-10-06 the prefix's inputs are what the bars read, by value: the store's seams (_seams_fp), not the
    store object; the captions not at all, the held bars re-captioned instead (only those an appended row can reach,
    through the entry's bar index, or every one after a rewrite), a changed bar a new object in the builder's key
    order and every other bar the same object; the leaf's path and inode (the head) and each turn's uuids
    (_turn_hold_key) in the key. Red first: the caption-row, open-segment, rewrite, republish and re-recorded-uuid
    tests fail on the kernel before that change (the prefix was not served, or was served with another record's
    ids); the seam test and the unshared-store test pin rules that held before and hold still.

    The post-merge review of that change (2026-10-06) found roads no test reached, each giving the right bars and each
    left green by a mutant that broke it: five captions-version roads (a prefix held with no captions meeting its first
    row, an unknown version on both sides, an append whose reader entry went, a mapping the reader memo moved past, a
    row landing during the derivation), the host's suspensions as a prefix input, and the leaf's head (a file renamed
    over the leaf between builds, and during the parse). Each has a test below, red under the mutant its docstring
    names; each compares the bars with a whole derivation's."""

    def _prefix_counts(self):
        st = self.stats()
        return (st["prefix_hit"], st["prefix_segs"])

    def recap(self):
        st = self.stats()
        return (st["prefix_recap"], st["prefix_recap_new"])

    def head(self):
        """The live lane's leaf as build_timeline stats it before the parse: the prefix's head."""
        return km._msg_scan_head(str(self.tpath()))

    def derive(self, session, goals, caps, head):
        """_lane_segments over the live lane as _lane_memo calls it; `head` is the prefix's leaf (None holds nothing)."""
        return km._lane_segments(LIVE_SID, session, goals, caps, True, None, head=head)

    def whole(self):
        """A whole derivation of the lane as it stands: no prefix read or held, over the current parse, store and captions."""
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        return km._lane_segments(LIVE_SID, session, km.jd.load_goals_shared(LIVE_SID), km._captions(LIVE_SID), True, None)

    def seg_ids(self):
        """Every segment id of the lane's current parse, in turn order (one segment per turn in these fixtures)."""
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        return [s["id"] for t in session["turns"] for s in km.em.segments(t)]

    def three_turns(self, parent="a1"):
        """Two more exchanges after the first, built after each append: by the third build the first two turns are held
        (the third build was served the first). Returns the third build's frame."""
        self.build()
        self.append_records([_rec("user", self.t0 + 100, "u2", parent, "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        self.build()
        self.append_records([_rec("user", self.t0 + 200, "u3", "a2", "and log each retry"),
                             _rec("assistant", self.t0 + 220, "a3", "u3", "Logged with the delay.")])
        return self.build()

    def write_store(self, seams, rev):
        """The lane's goal store written whole, as a publish leaves it: no nodes, these seams, a new version."""
        p = km.jd.GOALDIR / (LIVE_SID + ".json")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"nodes": {}, "status": {}, "seams": list(seams), "rev": rev}))
        os.utime(p, (self.now - 300 + rev, self.now - 300 + rev))

    def test_a_third_turn_reuses_the_held_prefix_and_equals_a_whole_derivation(self):
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "Added backoff")
        self.build()                                                  # one turn: nothing before the tail, no prefix held
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        self.build()                                                  # two turns: the first turn is held as the prefix
        self.assertEqual(self._prefix_counts(), (0, 0), "nothing to reuse yet: the held prefix was empty at the first build")
        self.append_records([_rec("user", self.t0 + 200, "u3", "a2", "and log each retry"),
                             _rec("assistant", self.t0 + 220, "a3", "u3", "Logged with the delay.")])
        tl3 = self.build()                                            # three turns: the first turn's bars come from the prefix
        self.assertEqual(self._prefix_counts(), (1, 1), "one derivation reused the prefix, one segment came from it")
        self.assertEqual(len(tl3["turns"][LIVE_SID]), 3)
        # what the prefix served equals a whole derivation on the same objects
        km._lane_prefix_memo.clear()
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals = km.jd.load_goals_shared(LIVE_SID)
        caps = km._captions(LIVE_SID)
        bars, seg_ends, last_t, compactions, cap_marks, other_marks, nsegs, complained = km._lane_segments(
            LIVE_SID, session, goals, caps, True, None)
        self.assertEqual(json.dumps(tl3["turns"][LIVE_SID]), json.dumps(bars), "the prefix path and the whole derivation agree")
        self.assertEqual(nsegs, 3)
        self.assertEqual(self.stats()["segs_miss"], 1 + 2 + 2, "the derivations walked 1, then 2, then only the 2 turns after the prefix")

    def test_a_caption_row_re_captions_the_held_bar_it_names_and_the_prefix_still_serves(self):
        """RED FIRST (2026-10-06): a caption row for the working session was a new input to the prefix (the captions
        file's stat), so the build that drew it re-walked every turn of the lane, and the judges write one every few
        seconds while a session works. The prefix now serves, and only the held bar the appended row names is looked up
        again (the bar index): the bars and their encoded bytes equal a whole derivation's, the re-captioned bar is a
        new object, and the other held bar is the same object, never edited, so the wire cache's string for it stands.
        A later row for the second held bar, whose turn closed after the index was first built, reaches it too."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        tl3 = self.three_turns()
        self.assertEqual(self._prefix_counts()[0], 1)
        held = list(tl3["turns"][LIVE_SID])
        self.assertEqual(held[0].get("c"), "first summary")
        snap = [json.dumps(b) for b in held]
        km._delta_split("dictlist:id", tl3["turns"], memo_key=("bars", "turns"))   # the wire cache, warm from this frame
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "Added backoff")   # the first turn's caption changes
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "Added backoff", "the first bar carries the new caption")
        self.assertEqual(self._prefix_counts()[0], 2, "the prefix served the build that drew the caption row")
        self.assertEqual(self.recap(), (1, 1), "one held bar looked up again, and rebuilt")
        self.assertIsNot(bars[0], held[0], "the re-captioned bar is a new object")
        self.assertIs(bars[1], held[1], "the other held bar is the same object")
        self.assertEqual([json.dumps(b) for b in held], snap, "no held bar was edited in place")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]), "the bars equal a whole derivation's")
        warm, worder = km._delta_split("dictlist:id", tl4["turns"], memo_key=("bars", "turns"))
        cold, corder = km._delta_split("dictlist:id", tl4["turns"])
        self.assertEqual(worder, corder)
        self.assertEqual({k: v[1] for k, v in warm.items()}, {k: v[1] for k, v in cold.items()},
                         "the frame's bytes through the warm wire cache equal a cold encoding")
        self.assertEqual([m["x"] for m in self.marks(tl4, judge="captioner")], ["Added backoff"],
                         "the judging marks are derived on every build, a prefix hit's too")
        seg1 = self.seg_ids()[1]
        km.jd.append_caption(LIVE_SID, seg1, "segment", self.t0 + 100, "Capped the delay")
        tl5 = self.build()
        self.assertEqual(tl5["turns"][LIVE_SID][1].get("c"), "Capped the delay", "the second held bar, indexed when its turn closed")
        self.assertEqual((self._prefix_counts()[0], self.recap()), (3, (2, 2)))
        self.assertIs(tl5["turns"][LIVE_SID][0], bars[0], "and the first bar, unchanged this time, is the same object")
        self.assertEqual(json.dumps(tl5["turns"][LIVE_SID]), json.dumps(self.whole()[0]))

    def test_a_caption_row_for_the_open_segment_looks_up_no_held_bar(self):
        """RED FIRST: the captioner rewrites the OPEN segment's caption as it grows (the build-cost lab's churn writes
        exactly that row). It shares no held bar's segment key, so the bar index finds none: the build looks up the
        open segment's captions alone, where the whole walk looked up every segment's (three here), and the held bars
        are the same objects."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        tl3 = self.three_turns()
        tail = self.seg_ids()[-1]
        calls, real = [], km._seg_work_caption
        self.addCleanup(setattr, km, "_seg_work_caption", real)
        km._seg_work_caption = lambda caps, seg_id: (calls.append(seg_id), real(caps, seg_id))[1]
        km.jd.append_caption(LIVE_SID, tail, "segment", self.t0 + 200, "Logging each retry")
        tl4 = self.build()
        km._seg_work_caption = real
        self.assertEqual(calls, [tail], "only the open segment's caption was looked up")
        self.assertEqual(self.recap(), (0, 0), "no held bar was looked up again")
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[-1].get("c"), "Logging each retry")
        self.assertTrue(all(a is b for a, b in zip(bars[:2], tl3["turns"][LIVE_SID][:2])), "the held bars are the same objects")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_a_message_caption_row_for_a_held_bar_re_captions_its_dot(self):
        """RED FIRST: a '#p' row is a held bar's MESSAGE caption (its dot, `m`). It reaches the bar through the bar index
        under the row's key less its '#p', exact and drifted: the prefix serves, that bar alone is rebuilt, the other held
        bar is the same object, and the bars equal a whole derivation's."""
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "first summary")
        tl3 = self.three_turns()
        held = list(tl3["turns"][LIVE_SID])
        self.assertNotIn("m", held[0], "the premise: no message caption yet")
        seg0, seg1 = self.seg_ids()[:2]
        km.jd.append_caption(LIVE_SID, seg0 + "#p", "prompt", self.t0, "Back off the retries")
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("m"), "Back off the retries", "the first held bar carries its new message caption")
        self.assertEqual(self._prefix_counts()[0], 2, "the prefix served the build that drew the row")
        self.assertEqual(self.recap(), (1, 1), "that held bar alone was looked up again, and rebuilt")
        self.assertIsNot(bars[0], held[0])
        self.assertIs(bars[1], held[1], "the other held bar is the same object")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))
        parts = seg1.split(":")
        parts[-2] = str(int(parts[-2]) + 3)                  # the judge's parse placed the segment's start 3 s later
        km.jd.append_caption(LIVE_SID, ":".join(parts) + "#p", "prompt", self.t0 + 103, "Cap the delay")
        tl5 = self.build()
        bars5 = tl5["turns"][LIVE_SID]
        self.assertEqual(bars5[1].get("m"), "Cap the delay", "a drifted '#p' row reaches its held bar too")
        self.assertEqual((self._prefix_counts()[0], self.recap()), (3, (2, 2)))
        self.assertIs(bars5[0], bars[0])
        self.assertEqual(json.dumps(bars5), json.dumps(self.whole()[0]))

    def test_seeded_caption_rows_rewrites_republishes_and_turns_keep_the_bars_whole(self):
        """Seeded random caption rows (exact, drifted, '#p', the open segment's, another grain, rows the fold ignores),
        captions rewrites, store republishes and new turns: after each, the lane's bars and their bytes through the warm
        wire cache equal a whole derivation's and a cold encoding, and the prefix served and re-captioned along the way."""
        import random
        rnd = random.Random(1)
        km.jd.append_caption(LIVE_SID, self.seg_id(), "segment", self.t0, "first summary")
        self.build()
        state = {"n": 1, "last": "a1", "rev": 0}
        texts = ["continue", "go on", "and cap the delay"]

        def add_turn():
            i = state["n"]
            t = self.t0 + 100 + 25 * i
            u, a = "u%d" % (i + 1), "a%d" % (i + 1)
            self.append_records([_rec("user", t, u, state["last"], rnd.choice(texts)),
                                 _rec("assistant", t + 10, a, u, "Done %d." % i)])
            state["n"] += 1
            state["last"] = a
        for _ in range(4):
            add_turn()
            self.build()
        tl = self.build()
        km._delta_split("dictlist:id", tl["turns"], memo_key=("fuzz", "turns"))
        cap_path = km.jd.CAPDIR / (LIVE_SID + ".jsonl")
        st0 = dict(self.stats())
        for step in range(36):
            ids = self.seg_ids()
            pick = rnd.choice(ids[:-1])
            parts = pick.split(":")
            parts[-2] = str(int(parts[-2]) + rnd.choice([-4, 3, 7]))
            drift = ":".join(parts)
            k = rnd.randrange(10)
            if k == 0:
                km.jd.append_caption(LIVE_SID, pick, "segment", self.t0, "w%d" % step)
            elif k == 1:
                km.jd.append_caption(LIVE_SID, pick + "#p", "prompt", self.t0, "p%d" % step)
            elif k == 2:
                km.jd.append_caption(LIVE_SID, drift, "segment", int(parts[-2]), "dw%d" % step)
            elif k == 3:
                km.jd.append_caption(LIVE_SID, drift + "#p", "prompt", int(parts[-2]), "dp%d" % step)
            elif k == 4:
                km.jd.append_caption(LIVE_SID, ids[-1], "segment", self.t0, "o%d" % step)
            elif k == 5:
                km.jd.append_caption(LIVE_SID, pick, "turn", self.t0, "t%d" % step)
            elif k == 6:
                with cap_path.open("a") as f:
                    f.write(json.dumps({"grain": "segment", "caption": "no id"}) + "\n")
            elif k == 7:
                rows = [json.loads(x) for x in cap_path.read_text().splitlines() if x.strip()]
                keep = [r for r in rows if rnd.random() < 0.6]
                cap_path.write_text("".join(json.dumps(r) + "\n" for r in keep))
            elif k == 8:
                state["rev"] += 1
                self.write_store([], state["rev"])
            else:
                add_turn()
            tl = self.build()
            bars = tl["turns"][LIVE_SID]
            label = "step %d kind %d" % (step, k)
            self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]), label)
            warm, wo = km._delta_split("dictlist:id", tl["turns"], memo_key=("fuzz", "turns"))
            cold, co = km._delta_split("dictlist:id", tl["turns"])
            self.assertEqual(wo, co, label)
            self.assertEqual({x: v[1] for x, v in warm.items()}, {x: v[1] for x, v in cold.items()}, label)
        st = self.stats()
        self.assertGreater(st["prefix_hit"] - st0.get("prefix_hit", 0), 0, "the walk served the held prefix")
        self.assertGreater(st.get("prefix_recap_new", 0) - st0.get("prefix_recap_new", 0), 0, "and rebuilt re-captioned bars")

    def test_a_rewritten_captions_file_re_captions_every_held_bar(self):
        """RED FIRST: a rewrite of the captions file (a new generation of the reader's entry: rows dropped or replaced)
        is not an append, so no appended rows say which bars it reaches: every held bar is looked up again, the prefix
        still serves, a bar whose row went loses its caption, and the bars equal a whole derivation's."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        tl3 = self.three_turns()
        self.assertEqual(tl3["turns"][LIVE_SID][0].get("c"), "first summary")
        seg1 = self.seg_ids()[1]
        path = km.jd.CAPDIR / (LIVE_SID + ".jsonl")
        before = path.stat().st_size
        path.write_text(json.dumps({"id": seg1, "grain": "segment", "t": self.t0 + 100, "caption": "Capped"}) + "\n")
        os.utime(path, (self.now - 5, self.now - 5))
        self.assertLess(path.stat().st_size, before, "the premise: the file shrank, which the reader reads from zero")
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertNotIn("c", bars[0], "the first bar's row went")
        self.assertEqual(bars[1].get("c"), "Capped")
        self.assertEqual(self._prefix_counts()[0], 2, "the prefix served the rewrite's build")
        self.assertEqual(self.recap(), (2, 2), "both held bars looked up again, both changed")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_a_republished_store_with_the_same_seams_serves_the_prefix(self):
        """RED FIRST: a store republish (a planner's write: a node minted, no seam) made a new shared store object, and
        the prefix was keyed on the object, so that build re-walked every turn. The bars read the store only through
        its seams, which stand, so the prefix serves and the held bars are the same objects; the planner's new node is a
        mark on the same build (the marks are derived on every build, outside the prefix)."""
        self.mint_store(text="First goal")
        tl3 = self.three_turns()
        self.assertEqual(self._prefix_counts()[0], 1)
        s = km.jd.load_goals(LIVE_SID)
        km.jd.apply_plan(s, "s2", self.t0 + 100, [{"do": "mint", "why": "x", "text": "Second goal"}], [])
        km.jd.rollup_status(s, session_closed=False)
        km.jd.save_goals(LIVE_SID, s)
        tl4 = self.build()
        self.assertEqual(sorted(m["x"] for m in self.marks(tl4, judge="planner")), ["First goal", "Second goal"],
                         "the premise: a republished store, read by this build")
        self.assertEqual(self._prefix_counts()[0], 2, "the same seams: the prefix served")
        self.assertEqual(self.stats()["prefix_reset"], 0)
        self.assertTrue(all(a is b for a, b in zip(tl4["turns"][LIVE_SID][:2], tl3["turns"][LIVE_SID][:2])))
        self.assertEqual(json.dumps(tl4["turns"][LIVE_SID]), json.dumps(self.whole()[0]))

    def test_a_seam_that_splits_a_held_segment_re_derives_the_lane_and_equals_a_whole_derivation(self):
        """A seam (a goal settled while its segment kept working) splits a held segment into two bars. The seams are the
        prefix's input by value, so the held bars are dropped (prefix_reset), the lane re-derives, and the bars equal a
        whole derivation's with the split; the seam removed, the same again with one bar."""
        self.add_lane(LIVE_SID, "web", [
            _rec("user", self.t0, "u1", None, "make the retry loop back off"),
            _tool_use(self.t0 + 10, "c1", "u1", "toolu_1"), _tool_result(self.t0 + 20, "r1", "c1", "toolu_1"),
            _tool_use(self.t0 + 40, "c2", "r1", "toolu_2"), _tool_result(self.t0 + 50, "r2", "c2", "toolu_2"),
            _rec("assistant", self.t0 + 60, "a1", "r2", "Added exponential backoff.")])
        self.write_store([], 1)
        tl3 = self.three_turns()
        n = len(tl3["turns"][LIVE_SID])
        seg0 = self.seg_ids()[0]
        self.write_store([{"segs": [km.jd._seg_key(seg0)], "t": self.t0 + 30, "top": "g1", "text": "Back off the retries"}], 2)
        tl4 = self.build()
        self.assertEqual(len(tl4["turns"][LIVE_SID]), n + 1, "the premise: the seam split the first segment")
        self.assertEqual(self.stats()["prefix_reset"], 1, "the seams moved: the held bars were dropped")
        self.assertEqual(json.dumps(tl4["turns"][LIVE_SID]), json.dumps(self.whole()[0]))
        self.write_store([], 3)
        tl5 = self.build()
        self.assertEqual(len(tl5["turns"][LIVE_SID]), n)
        self.assertEqual(self.stats()["prefix_reset"], 2)
        self.assertEqual(json.dumps(tl5["turns"][LIVE_SID]), json.dumps(self.whole()[0]))

    def test_records_re_recorded_under_new_uuids_are_not_served_the_held_bars(self):
        """RED FIRST: a turn's identity in the prefix key named its session, start second and prompt text, so a leaf
        rewritten with the same times and text under new uuids (a file rewritten in place, a fixture reusing its times)
        was served the held bars, whose prompt and work ids named records the transcript no longer holds. The hold key
        carries the trigger's and the newest atom's uuids: the first held turn diverges (prefix_partial), the lane
        re-derives, and the bars name the new records, equal to a whole derivation's."""
        tl3 = self.three_turns()
        self.assertEqual(tl3["turns"][LIVE_SID][0].get("p"), "u1")
        ino = os.stat(self.tpath()).st_ino
        self.tpath().write_text("".join(json.dumps(r) + "\n" for r in [
            _rec("user", self.t0, "x1", None, "make the retry loop back off"),
            _rec("assistant", self.t0 + 20, "y1", "x1", "Added exponential backoff."),
            _rec("user", self.t0 + 100, "u2", "y1", "and cap the delay"),
            _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes."),
            _rec("user", self.t0 + 200, "u3", "a2", "and log each retry"),
            _rec("assistant", self.t0 + 220, "a3", "u3", "Logged with the delay.")]))
        os.utime(self.tpath(), (self.now - 5, self.now - 5))
        self.assertEqual(os.stat(self.tpath()).st_ino, ino, "the premise: rewritten in place, the same leaf")
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual((bars[0].get("p"), bars[0].get("w")), ("x1", "y1"), "the bar names the new records")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))
        self.assertEqual((self.stats()["prefix_partial"], self._prefix_counts()[0]), (1, 1), "diverged: not served")

    def test_another_leaf_under_the_same_session_is_a_new_prefix(self):
        """A resume into another file, or a file replaced at the same path, is another leaf: the prefix is headed by the
        leaf's path and inode, so the held bars are dropped (prefix_reset) even where every turn key stands; a caller
        with no head (a dead lane, a direct derivation) neither reads nor holds a prefix."""
        self.three_turns()
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals = km.jd.load_goals_shared(LIVE_SID)
        km._lane_prefix_memo.clear(); _zero(km._lanes_stats)
        here = self.head()
        self.assertIsNotNone(here)
        self.derive(session, goals, {}, here)
        self.derive(session, goals, {}, here)
        self.assertEqual((self._prefix_counts()[0], self.stats()["prefix_reset"]), (1, 0), "the same leaf: served")
        other = (here[0] + ".resumed", here[1] + 1)
        v = self.derive(session, goals, {}, other)
        self.assertEqual((self._prefix_counts()[0], self.stats()["prefix_reset"]), (1, 1), "another leaf: dropped, not served")
        self.assertEqual(json.dumps(v[0]), json.dumps(self.derive(session, goals, {}, None)[0]))
        self.assertEqual(self.stats()["prefix_reset"], 1, "no head: nothing read")
        self.assertEqual(km._lane_prefix_memo[LIVE_SID][0][0], other, "and nothing held over the last leaf's entry")

    def test_an_unshared_mutable_store_gets_no_prefix(self):
        """A private store with content (the shared view off) could change under a held entry: no prefix is read or
        held for it, whatever its seams (the rule before the seams were keyed by value, kept)."""
        self.mint_store()
        km.jd._SHARED_OFF[0] = True
        try:
            tl3 = self.three_turns()
            self.assertEqual(self._prefix_counts()[0], 0)
            self.assertNotIn(LIVE_SID, km._lane_prefix_memo)
            self.assertEqual(len(tl3["turns"][LIVE_SID]), 3)
        finally:
            km.jd._SHARED_OFF[0] = False

    def test_the_re_captioned_bar_keeps_the_builders_key_order(self):
        """A held bar whose caption changed is rebuilt as a new dict, and its bytes equal the builder's only if its keys
        come in the builder's order. _BAR_ORDER is read here off the loop itself (its `bar = {...}` literal, then every
        `bar[...] =` in source order), and a rebuilt bar keeps it with every key set, an empty caption omitted."""
        tree = ast.parse(inspect.getsource(km._lane_segments))
        order = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
                continue
            tgt = node.targets[0]
            if isinstance(tgt, ast.Name) and tgt.id == "bar" and isinstance(node.value, ast.Dict):
                order += [(node.lineno, i, k.value) for i, k in enumerate(node.value.keys)]
            elif (isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name) and tgt.value.id == "bar"
                  and isinstance(tgt.slice, ast.Constant)):
                order.append((node.lineno, 0, tgt.slice.value))
        self.assertEqual(tuple(k for _l, _i, k in sorted(order)), km._BAR_ORDER, "the loop's key order")
        full = {k: (1 if k in ("start", "end") else "v-" + k) for k in reversed(km._BAR_ORDER)}
        out = km._bar_recaptioned(full, "new work", "new gist")
        self.assertIsNot(out, full)
        self.assertEqual(list(out), list(km._BAR_ORDER))
        self.assertEqual((out["c"], out["m"], out["q"]), ("new work", "new gist", "v-q"))
        bare = km._bar_recaptioned(full, "", "")
        self.assertEqual(list(bare), [k for k in km._BAR_ORDER if k not in ("c", "m")],
                         "an empty caption is omitted, as the builder omits it")
        self.assertEqual(full["c"], "v-c", "the bar it was rebuilt from is untouched")

    # ── the roads the post-merge review named (2026-10-06): each red under the mutant its docstring names ──
    def cap_path(self):
        return km.jd.CAPDIR / (LIVE_SID + ".jsonl")

    def test_a_prefix_held_with_no_captions_meets_its_first_row(self):
        """The usual order, since a lane's turns close before the captioner writes their first rows. A prefix held while
        the lane had no captions file is held under the "empty" version, which names no file and no records, so the first
        row is no append to it: every held bar is looked up again, and the one the row names carries its caption. The
        same after the file is truncated to no rows and then appended to. Red under the mutant in which
        _caption_rows_since answers no rows from the "empty" version to a tuple (the closed turns would lack their first
        captions until a later row reached their bars, a rewrite, or a reset)."""
        self.assertFalse(self.cap_path().exists(), "(the premise) no captions file")
        self.three_turns()
        self.assertEqual(km._lane_prefix_memo[LIVE_SID][9], "empty", "(the premise) the prefix is held under the empty version")
        seg0 = self.seg_ids()[0]
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "first summary", "the first row reaches its held bar")
        self.assertEqual((self._prefix_counts()[0], self.recap()), (2, (2, 1)),
                         "the prefix served; both held bars were looked up again, the one the row names rebuilt")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))
        self.cap_path().write_text("")                               # the file truncated to no rows: empty again
        os.utime(self.cap_path(), (self.now - 5, self.now - 5))
        tl5 = self.build()
        self.assertNotIn("c", tl5["turns"][LIVE_SID][0], "the truncation took the caption")
        self.assertEqual(km._lane_prefix_memo[LIVE_SID][9], "empty", "(the premise) held under the empty version again")
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "second summary")
        tl6 = self.build()
        bars = tl6["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "second summary", "the append after the truncation reaches its held bar")
        self.assertEqual(self._prefix_counts()[0], 4)
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_an_unknown_version_on_both_sides_re_captions_every_held_bar(self):
        """A mapping _captions answered without holding it (the reader's entry gone after the read) has no version
        (None), and a prefix held under None can meet a derivation under None again: in the kernel two uncached reads in
        a row, the rarest of these roads. Two unknowns are not one version, so every held bar is looked up again and a
        changed row reaches its bar. The reader's entry for the captions is dropped after every read here. Red under the
        mutant in which _lane_segments re-captions only when the two versions differ (the held bar would keep its older
        caption)."""
        real_read, cap = km.em._read_jsonl_incremental, str(self.cap_path())

        def read_not_kept(path, *a, **k):
            out = real_read(path, *a, **k)
            if str(path) == cap:
                with km.em._JSONL_CACHE_LOCK:
                    km.em._cache_pop_locked(cap)
            return out
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        with mock.patch.object(km.em, "_read_jsonl_incremental", read_not_kept):
            self.three_turns()
            self.assertIsNone(km._lane_prefix_memo[LIVE_SID][9], "(the premise) the prefix is held under no version")
            km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "Added backoff")
            tl4 = self.build()
            self.assertIsNone(km._lane_prefix_memo[LIVE_SID][9], "(the premise) and derived under none again")
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "Added backoff", "the changed row reaches its held bar")
        self.assertEqual(self._prefix_counts()[0], 2, "the prefix served")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_an_append_whose_reader_entry_went_before_the_derivation_re_captions_every_held_bar(self):
        """An append's version names the reader's entry that holds the appended records, and the derivation reads them
        from that entry. An entry evicted between the captions read and the derivation holds none of them, so every held
        bar is looked up again, not none of them. Red under the mutant in which _caption_rows_since answers no rows when
        the reader's entry is gone (the held bar would keep its older caption)."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        self.three_turns()
        held_ver = km._lane_prefix_memo[LIVE_SID][9]
        self.assertIsInstance(held_ver, tuple, "(the premise) the prefix is held under a version of the file")
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "Added backoff")
        real, read = km._captions, []

        def evicted_after_the_read(fsid):
            caps = real(fsid)
            if fsid == LIVE_SID and not read:
                read.append(km._caption_rows_version(fsid, caps))
                with km.em._JSONL_CACHE_LOCK:
                    km.em._cache_pop_locked(str(self.cap_path()))
            return caps
        km._captions = evicted_after_the_read
        tl4 = self.build()
        km._captions = real
        self.assertEqual((read[0][:3], read[0][3]), (held_ver[:3], held_ver[3] + 1), "(the premise) an append to the held version")
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "Added backoff", "the appended row reaches its held bar")
        self.assertEqual((self._prefix_counts()[0], self.recap()), (2, (2, 1)),
                         "the prefix served; both held bars were looked up again, the one the row names rebuilt")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_a_derivation_over_a_mapping_the_reader_memo_moved_past_then_one_over_the_newer_mapping(self):
        """A build that read its captions before an append, while another thread's read after the append moved the
        reader memo on, derives over a mapping the memo no longer holds. The bars it holds were captioned from the older
        rows, so they are held under no version, never the memo's newer one: the next derivation, over the newer mapping,
        looks them up again, and the held bar the append changed carries the new caption. Asserted on the caption, not on
        the version helper's answer. Red under the mutant in which _caption_rows_version answers the memo's version for
        a mapping it no longer holds."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        self.three_turns()
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        goals = km.jd.load_goals_shared(LIVE_SID)
        older = km._captions(LIVE_SID)
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "Added backoff")
        newer = []
        t = threading.Thread(target=lambda: newer.append(km._captions(LIVE_SID)))
        t.start()
        t.join(30)
        self.assertIsNot(newer[0], older, "(the premise) the other thread read the newer mapping")
        self.assertIs(km._CAPTION_ROWS_MEMO[str(self.cap_path())][3], newer[0], "(the premise) and the reader memo holds it")
        v1 = self.derive(session, goals, older, self.head())
        self.assertEqual(v1[0][0].get("c"), "first summary", "the older rows caption the derivation over them")
        self.assertEqual(self._prefix_counts()[0], 2, "the prefix served it")
        v2 = self.derive(session, goals, km._captions(LIVE_SID), self.head())
        self.assertEqual(v2[0][0].get("c"), "Added backoff", "the derivation over the newer mapping carries the new caption")
        self.assertEqual(json.dumps(v2[0]), json.dumps(self.whole()[0]))

    def test_a_row_landing_during_a_derivation_the_prefix_serves_reaches_its_bar_on_the_next_build(self):
        """The prefix records the version its bars were captioned under, the one the derivation read, never the file's
        newer one. A row appended while a build the prefix serves is deriving (here from a hook on the work-caption
        lookup, at the open segment's) is not in that build's bars; the next build's captions are an append to the
        recorded version, so the held bar the row names is looked up again and carries it. The build needs a whole-lane
        memo miss first (a captions row for the open segment), or the lane memo serves it and no derivation runs. Red
        under the mutant in which the prefix records the version of a captions read taken when it stores."""
        seg0 = self.seg_id()
        km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "first summary")
        self.three_turns()
        tail = self.seg_ids()[-1]
        km.jd.append_caption(LIVE_SID, tail, "segment", self.t0 + 200, "Logging each retry")
        real, landed = km._seg_work_caption, []
        self.addCleanup(setattr, km, "_seg_work_caption", real)

        def row_lands(caps, seg_id):
            if not landed:
                landed.append(seg_id)
                km.jd.append_caption(LIVE_SID, seg0, "segment", self.t0, "Added backoff")
            return real(caps, seg_id)
        km._seg_work_caption = row_lands
        tl4 = self.build()
        km._seg_work_caption = real
        self.assertEqual((landed, self._prefix_counts()[0]), ([tail], 2),
                         "(the premise) the row landed during the open segment's lookup, in a build the prefix served")
        self.assertEqual(tl4["turns"][LIVE_SID][0].get("c"), "first summary", "that build read the rows before it landed")
        tl5 = self.build()
        bars = tl5["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("c"), "Added backoff", "the next build looks the held bar up again")
        self.assertEqual(self._prefix_counts()[0], 3)
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_a_host_sleep_inside_a_held_segment_drops_the_prefix_and_cuts_the_bar(self):
        """The host's recorded suspensions are a prefix input: the bars excise every sleep inside a segment, so a held bar
        drawn before a sleep was recorded would span it. A three-turn lane, so the prefix holds two turns; a sleep
        inside the first held segment drops the held bars (prefix_reset) and the bar is cut at the sleep, equal to a
        whole derivation's. Red under the mutant in which _lane_prefix_inputs keys an empty tuple for the suspensions (the
        kept bar would span the sleep until the leaf, the seams or a kept turn moved)."""
        tl3 = self.three_turns()
        self.assertEqual((self._prefix_counts()[0], len(tl3["turns"][LIVE_SID])), (1, 3), "(the premise) two turns held")
        km._downtime[:] = [(self.t0 + 5, self.t0 + 10)]
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(self.stats()["prefix_reset"], 1, "the suspensions moved: the held bars were dropped")
        self.assertEqual(len(bars), 4, "the first bar is cut at the sleep")
        self.assertTrue(bars[1].get("t"), "the second piece is a continuation: no new prompt dot")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def tool_lane(self):
        """The lane's first turn with a tool call between its prompt and its reply: the call's record is neither the
        turn's trigger nor its newest, so re-recording it under a new uuid moves no turn key, and it is the bar's work
        anchor (`w`)."""
        self.add_lane(LIVE_SID, "web", [
            _rec("user", self.t0, "u1", None, "make the retry loop back off"),
            _tool_use(self.t0 + 10, "c1", "u1", "toolu_1"), _tool_result(self.t0 + 15, "r1", "c1", "toolu_1"),
            _rec("assistant", self.t0 + 20, "a1", "r1", "Added exponential backoff.")])

    def rename_over(self):
        """The leaf's records, the tool call re-recorded under a new uuid, written to a new file renamed over the leaf:
        a new inode at the same path, every turn key standing."""
        body = self.tpath().read_text()
        new = body.replace('"uuid": "c1"', '"uuid": "c1x"').replace('"parentUuid": "c1"', '"parentUuid": "c1x"')
        self.assertEqual(new.count('"c1x"'), 2, "(the premise) the call and its result's parent re-recorded")
        tmp = Path(str(self.tpath()) + ".new")
        tmp.write_text(new)
        os.replace(str(tmp), str(self.tpath()))

    def test_a_new_file_renamed_over_the_leaf_is_not_served_the_old_files_bars(self):
        """A file renamed over the leaf whose one changed record (the tool call, under a new uuid) moves no turn key is
        another file at the same path: the prefix is headed by the leaf's path and inode, which build_timeline stats, so
        the held bars are dropped (prefix_reset) and the bar names the new record, equal to a whole derivation's. Red
        under the mutant in which build_timeline's head carries a constant in place of the inode (the old file's bar
        would be served)."""
        self.tool_lane()
        tl3 = self.three_turns()
        self.assertEqual(tl3["turns"][LIVE_SID][0].get("w"), "c1", "(the premise) the call is the bar's work anchor")
        keys = [km._turn_hold_key(t) for t in km._parse(str(self.tpath()), LIVE_SID, self.now)["turns"]]
        ino = os.stat(self.tpath()).st_ino
        self.rename_over()
        self.assertNotEqual(os.stat(self.tpath()).st_ino, ino, "(the premise) a new file at the path")
        self.assertEqual([km._turn_hold_key(t) for t in km._parse(str(self.tpath()), LIVE_SID, self.now)["turns"]], keys,
                         "(the premise) every turn key stands")
        tl4 = self.build()
        bars = tl4["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("w"), "c1x", "the bar names the new file's record")
        self.assertEqual((self.stats()["prefix_reset"], self._prefix_counts()[0]), (1, 1), "the held bars were dropped, not served")
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    def test_a_file_renamed_over_the_leaf_during_the_parse_is_dropped_on_the_next_build(self):
        """build_timeline stats the leaf BEFORE the parse reads it. A file renamed over the leaf after the parse returns
        leaves this build's bars, the old file's, under the old file's head, so the next build, whose head is the new
        file's, drops them and names the new record. The build needs a whole-lane memo miss (a captions row for the open
        segment), or the lane memo serves it and no derivation runs. Red under the mutant in which build_timeline stats
        the leaf after the parse (the new file's head would hold the old file's bars, and the next build would serve
        them)."""
        self.tool_lane()
        self.three_turns()
        km.jd.append_caption(LIVE_SID, self.seg_ids()[-1], "segment", self.t0 + 200, "Logging each retry")
        real, renamed = km._parse, []

        def parse_then_rename(path, sid, now):
            out = real(path, sid, now)
            if sid == LIVE_SID and not renamed:
                renamed.append(1)
                self.rename_over()
            return out
        km._parse = parse_then_rename
        tl4 = self.build()
        km._parse = real
        self.assertEqual(renamed, [1], "(the premise) the file was renamed over the leaf after the build's parse returned")
        self.assertEqual(tl4["turns"][LIVE_SID][0].get("w"), "c1", "that build parsed the old file")
        tl5 = self.build()
        bars = tl5["turns"][LIVE_SID]
        self.assertEqual(bars[0].get("w"), "c1x", "the next build drops the held bars and names the new record")
        self.assertEqual(self.stats()["prefix_reset"], 1)
        self.assertEqual(json.dumps(bars), json.dumps(self.whole()[0]))

    # ── the closed turns' compaction markers ride the prefix (2026-09-16) ──
    def _lazy_closed_turns(self, session):
        """Every closed turn's atoms as fresh, UNBUILT counting LazyAtoms over the same rows, with the uuids beside them
        as a restored turn carries them (em._pre_turns_of: `pre` and `uuids`, no stored segments), so the hold key reads
        none of the atoms and stands (id, ended, atom count, end, the trigger's and the newest atom's uuids). The wrapped
        lists come back so a test can pin their slots, and the read count starts at 0."""
        out = []
        for turn in session["turns"][:-1]:
            rows = list(turn["atoms"])
            turn["atoms"] = _CountingAtoms(_RowIndex(), rows)
            turn["pre"], turn["uuids"] = True, [a.get("uuid") for a in rows]
            out.append(turn["atoms"])
        _CountingAtoms.reads = 0
        return out

    @staticmethod
    def _slots(lazies):
        return [list.__getitem__(la, i) is km.em._UNMAT for la in lazies for i in range(len(la))]

    def _boundary_lane(self):
        """Four turns: the first reply, a compaction marker alone in a turn of its own, two more exchanges. Parsed, with
        a store minted so the goals object is the kernel's shape (a FrozenStore); the from-scratch markers beside."""
        self.append_records([_boundary(self.t0 + 50, "cb1", "a1"),
                             _rec("user", self.t0 + 100, "u2", "cb1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes."),
                             _rec("user", self.t0 + 200, "u3", "a2", "and log each retry"),
                             _rec("assistant", self.t0 + 220, "a3", "u3", "Logged with the delay.")])
        self.mint_store()
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        self.assertEqual(len(session["turns"]), 4)
        want = [{"t": a["t"]} for t in session["turns"] for a in t["atoms"]
                if a.get("type") == "system" and a.get("subtype") == "compact_boundary"]
        self.assertEqual(want, [{"t": self.t0 + 50}], "the fixture yields one real marker, inside a closed turn")
        return session, km.jd.load_goals_shared(LIVE_SID), want

    def test_a_prefix_hit_reads_no_atom_of_the_closed_turns_and_its_compactions_equal_a_whole_derivation(self):
        """The markers were a comprehension over every atom of every turn, run AFTER the prefix reuse, so a hit still read
        the whole history for them. With the closed turns as unbuilt LazyAtoms (the kernel's shape for a restored lane),
        a prefix-hit derivation reads none of their slots, leaves every one unbuilt, and serves the whole derivation's
        markers. Both calls name the leaf's head: a prefix is held under a head only."""
        session, goals, want = self._boundary_lane()
        self._lazy_closed_turns(session)
        whole = self.derive(session, goals, {}, self.head())
        self.assertGreater(_CountingAtoms.reads, 0, "the whole derivation reads the closed turns")
        self.assertEqual(whole[3], want)
        lazies = self._lazy_closed_turns(session)                  # fresh unbuilt slots over the same rows
        hit = self.derive(session, goals, {}, self.head())
        self.assertEqual(self._prefix_counts()[0], 1, "the second derivation was served the prefix")
        self.assertEqual(_CountingAtoms.reads, 0, "a prefix hit reads no atom of the closed turns")
        self.assertTrue(all(self._slots(lazies)), "and leaves every closed slot unbuilt: on a restored lane the read IS the cost")
        self.assertEqual(hit[3], whole[3], "the markers a hit serves are the whole derivation's")
        self.assertEqual(json.dumps(hit[0]), json.dumps(whole[0]))
        self.assertEqual((hit[1], hit[2], hit[7]), (whole[1], whole[2], whole[7]))

    def test_a_boundary_in_the_tail_turn_lands_without_re_walking_the_closed_turns(self):
        """The kernel road, three transcript moves after the prefix first served. A compaction appended after the third
        exchange is a tail turn of its own: the build that draws it derives the exchange that just closed and the new
        tail, reads no atom of the two turns the prefix holds, and puts the marker on the wire. Two exchanges later the
        marker's own turn is held too: its atoms are never read again and the marker still rides the lane, equal to a
        whole derivation's. Every closed turn is wrapped per derivation; the per-turn read counts tell held from derived."""
        self.build()
        self.append_records([_rec("user", self.t0 + 100, "u2", "a1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes.")])
        self.build()
        self.append_records([_rec("user", self.t0 + 200, "u3", "a2", "and log each retry"),
                             _rec("assistant", self.t0 + 220, "a3", "u3", "Logged with the delay.")])
        tl3 = self.build()
        self.assertEqual((self._prefix_counts()[0], self.lane(tl3)["compactions"]), (1, []))
        real, seen = km._lane_segments, []

        def wrapped(sid, session, *a, **k):                        # the closed turns as unbuilt LazyAtoms for this derivation only
            plain = [t["atoms"] for t in session["turns"][:-1]]
            lazies = self._lazy_closed_turns(session)
            try:
                return real(sid, session, *a, **k)
            finally:
                seen.append(([la.n for la in lazies], [all(self._slots([la])) for la in lazies]))
                for t, atoms in zip(session["turns"], plain):
                    t["atoms"] = atoms
                    t.pop("pre", None)
                    t.pop("uuids", None)
        km._lane_segments = wrapped
        try:
            self.append_records([_boundary(self.t0 + 300, "cb1", "a3")])
            tl4 = self.build()                                     # closed: the three exchanges; held: the first two
            self.assertEqual(self.lane(tl4)["compactions"], [{"t": self.t0 + 300}], "the tail's marker is on the wire")
            self.append_records([_rec("user", self.t0 + 400, "u4", "cb1", "and retry on timeouts"),
                                 _rec("assistant", self.t0 + 420, "a4", "u4", "Timeouts retry too.")])
            tl5 = self.build()                                     # closed: three exchanges and the marker's turn; held: the exchanges
            self.append_records([_rec("user", self.t0 + 500, "u5", "a4", "and count the retries"),
                                 _rec("assistant", self.t0 + 520, "a5", "u5", "Counted per call.")])
            tl6 = self.build()                                     # the marker's turn is held now
        finally:
            km._lane_segments = real
        self.assertEqual(self._prefix_counts()[0], 4, "every build since the third was served the prefix")
        self.assertEqual(len(seen), 3, "one derivation per build")
        reads, unbuilt = zip(*seen)
        self.assertEqual([r[:2] for r in reads], [[0, 0]] * 3, "the first two exchanges, held since the third build, are never read again")
        self.assertGreater(reads[0][2], 0, "the exchange that closed at the fourth build is derived once")
        self.assertEqual(reads[1][:3], [0, 0, 0]); self.assertGreater(reads[1][3], 0, "then held; the marker's turn is derived at the fifth")
        self.assertEqual(reads[2], [0, 0, 0, 0, reads[2][4]], "at the sixth the marker's turn is held: its atoms are not read")
        self.assertGreater(reads[2][4], 0)
        self.assertEqual(list(unbuilt), [[True, True, False], [True, True, True, False], [True, True, True, True, False]],
                         "a held turn's slots stay unbuilt; the one that just closed is built")
        self.assertEqual([self.lane(tl)["compactions"] for tl in (tl5, tl6)], [[{"t": self.t0 + 300}]] * 2,
                         "the marker rides the lane after its turn is held")
        km._lane_prefix_memo.clear()
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        whole = km._lane_segments(LIVE_SID, session, km.jd.load_goals_shared(LIVE_SID), km._captions(LIVE_SID), True, None)
        self.assertEqual(self.lane(tl6)["compactions"], whole[3])
        self.assertEqual(json.dumps(tl6["turns"][LIVE_SID]), json.dumps(whole[0]))

    def test_a_prefix_miss_still_walks_every_closed_turn_and_the_compactions_equal(self):
        """The two ways a prefix is not served fall back as before: a changed input (another leaf) and a partial prefix (a
        closed turn gone from the middle) each re-derive the whole lane, every closed slot built and the markers gathered
        from scratch, and each is counted (prefix_reset, prefix_partial). The partial case's fixture drops the marker's
        own turn, so a prefix that served its held markers blindly would put a marker on a lane that has none."""
        session, goals, want = self._boundary_lane()
        here = self.head()
        other = (here[0] + ".other", here[1] + 1)
        self._lazy_closed_turns(session)
        self.derive(session, goals, {}, here)
        lazies = self._lazy_closed_turns(session)
        miss = self.derive(session, goals, {}, other)              # another leaf: a new input
        self.assertEqual((self._prefix_counts()[0], self.stats()["prefix_reset"]), (0, 1), "a changed input is not served the held prefix")
        self.assertFalse(any(self._slots(lazies)), "the whole lane re-derived: every closed slot built")
        self.assertEqual(miss[3], want)
        short = dict(session, turns=[session["turns"][0], session["turns"][2], session["turns"][3]])   # the marker's turn gone
        lazies = self._lazy_closed_turns(short)
        part = self.derive(short, goals, {}, other)
        self.assertEqual((self._prefix_counts()[0], self.stats()["prefix_partial"]), (0, 1), "a partial prefix is not served either")
        self.assertFalse(any(self._slots(lazies)))
        self.assertEqual(part[3], [], "no marker on a lane whose marker turn is gone")
        km._lane_prefix_memo.clear()
        whole = self.derive(short, goals, {}, other)
        self.assertEqual(json.dumps(part[0]), json.dumps(whole[0]))
        self.assertEqual(part[3], whole[3])

    def test_a_partial_prefix_reads_each_standing_turn_once_not_twice(self):
        """RED FIRST: the partial-prefix branch (a closed turn changed in the middle) carried a leftover loop that walked every
        atom of the k standing turns into a set nobody read, right before the whole re-derivation read those turns again;
        on a restored lane each of those reads is a lock round trip or a row decode. A standing turn's atoms are now read
        exactly as often as a from-scratch derivation reads them, and no more (2026-09-16)."""
        session, goals, want = self._boundary_lane()
        short = dict(session, turns=[session["turns"][0], session["turns"][2], session["turns"][3]])   # the marker's turn gone
        here = self.head()
        km._lane_prefix_memo.clear()
        fresh = self._lazy_closed_turns(short)                          # a from-scratch derivation's read count per standing turn
        self.derive(short, goals, {}, here)
        scratch = [la.n for la in fresh]
        self.assertTrue(all(n > 0 for n in scratch), "the derivation reads every closed turn")
        km._lane_prefix_memo.clear()
        self._lazy_closed_turns(session)
        self.derive(session, goals, {}, here)                           # the four-turn prefix is held
        lazies = self._lazy_closed_turns(short)                         # turn 0 stands, turn 1 differs: the partial branch
        part = self.derive(short, goals, {}, here)
        self.assertEqual(self._prefix_counts()[0], 0, "a partial prefix is not served")
        self.assertEqual([la.n for la in lazies], scratch, "the standing turn is read as a from-scratch derivation reads it, not once more by a dead loop")
        self.assertEqual(part[3], [], "no marker on a lane whose marker turn is gone")

    def test_a_boundary_inside_an_echo_turn_lands_in_turn_order_on_both_roads(self):
        """The per-turn gather runs before the echo skip, pinned by setting the echo flag BY HAND on a parsed turn that holds a
        boundary (the kernel's own echo turns hold only echo atoms, so this shape models no lane the kernel meets): such a
        turn draws no bar, and the gather's placement is what keeps its marker in turn order on both roads."""
        self.append_records([_boundary(self.t0 + 50, "cb1", "a1"),
                             _rec("user", self.t0 + 100, "u2", "cb1", "and cap the delay"),
                             _rec("assistant", self.t0 + 120, "a2", "u2", "Capped at two minutes."),
                             _boundary(self.t0 + 250, "cb2", "a2"),
                             _rec("user", self.t0 + 300, "u3", "cb2", "and log each retry"),
                             _rec("assistant", self.t0 + 320, "a3", "u3", "Logged with the delay.")])
        session = km._parse(str(self.tpath()), LIVE_SID, self.now)
        self.assertEqual(len(session["turns"]), 5)
        session["turns"][1]["echoTurn"] = True                     # the first marker's turn, flagged as a stale echo's
        goals = km.jd.load_goals_shared(LIVE_SID)
        whole = self.derive(session, goals, {}, self.head())
        hit = self.derive(session, goals, {}, self.head())
        self.assertEqual(self._prefix_counts()[0], 1)
        self.assertEqual(whole[3], [{"t": self.t0 + 50}, {"t": self.t0 + 250}], "both markers, in turn order")
        self.assertEqual(hit[3], whole[3])
        self.assertNotIn(self.t0 + 50, [b["start"] for b in whole[0]], "the echo turn draws no bar")
        self.assertEqual(json.dumps(hit[0]), json.dumps(whole[0]))


if __name__ == "__main__":
    unittest.main()
