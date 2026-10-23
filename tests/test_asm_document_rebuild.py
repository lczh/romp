#!/usr/bin/env python3
"""Every large session keeps a current assembly document, across a version bump too (2026-10-23).

A whole parse that finds a leaf's document refused (a version bump outdates every document at the deploy boot) or missing,
on a leaf over the rebuild floor, notes it; the kernel's document writer then rebuilds it off the hot path, largest first
and one at a time, on the document refresh's road (a private whole parse that takes no key lock, then the writer with its
proofs and its atomic replace), and the next boot restores from it. Before this a document was written only from a whole
entry the settle or the converge pass still held, so after a version bump ten to sixteen sessions parsed whole at every
boot, and the converge pass, the only writer an idle session has, deferred any leaf whose estimate was over its whole cycle
budget at every cycle, forever, holding up the leaves after it.

Also pinned: a chat build outside the pusher's cycle (a page connect's push, a history reply) takes the tree's cut once the
tree has gained a document, rather than a floor of 0 the pusher recorded over the whole parse before it.

Synthetic only: invented notes-api text, placeholder uuids, private synthetic sids, a temp state root with session hosts
off, and no CLI (ROMP_CLAUDE_BIN is /bin/false)."""
import contextlib
import gzip
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source   # above every state preamble (tests/test_state_isolation_order.py)

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp(prefix="romp-asm-rebuild-")
os.environ.pop("ROMP_STATE_DIR", None)                    # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(os.environ["XDG_STATE_HOME"], "romp"), exist_ok=True)
Path(os.environ["XDG_STATE_HOME"], "romp", "session-hosts").write_text("off\n")   # no per-session host (CLAUDE.md, 2026-09-11)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
km = load_source("romp_kernel_asm_rebuild", os.path.join(BIN, "romp-kernel"))
jd, em = km.jd, km.em

SID = "5b5b5b5b-1111-4222-8333-555555555555"             # private synthetic sids (no goal store is minted here)
SID2 = "5b5b5b5b-2222-4222-8333-555555555555"
NOW = 1781200000
FLOOR = 192 * 1024                                        # the rebuild floor these tests run at; the transcripts are sized over it
WORDS = ("notes", "route", "pages", "cursor", "tags", "filter", "owner", "search", "index", "rebuilt", "retry", "cap",
         "backoff", "review", "branch", "merge", "green", "fixture", "suite", "schema")


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def transcript(turns, tag="n", t0=NOW - 86400, compact_every=50):
    """`turns` settled turns of invented notes-api text (about 1.1 KB each), a compaction every `compact_every`."""
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "%s_b%d" % (tag, k), "%s_s%d" % (tag, k)
            recs.append({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
                         "timestamp": _iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
            recs.append({"type": "user", "uuid": s, "parentUuid": b, "timestamp": _iso(t + 1), "isCompactSummary": True,
                         "message": {"role": "user", "content": "summary so far: " + " ".join(WORDS[(k + j) % len(WORDS)] for j in range(60))}})
            parent = s; t += 2
        u, a = "%s_u%d" % (tag, k), "%s_a%d" % (tag, k)
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
                     "message": {"role": "user", "content": " ".join(WORDS[(k + j) % len(WORDS)] for j in range(25)) + " %d" % k}})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(t + 20), "cwd": "/w/notes-api",
                     "message": {"role": "assistant", "content": [{"type": "text", "text": " ".join(WORDS[(k * 3 + j) % len(WORDS)]
                                                                                                   for j in range(140))}],
                                 "stop_reason": "end_turn"}})
        parent = a; t += 60
    return recs


def _join_rebuilds(timeout=60):
    for th in [t for t in threading.enumerate() if t.name == "asm-rebuild"]:
        th.join(timeout=timeout)


class Harness(unittest.TestCase):
    def setUp(self):
        saved_bin = os.environ.get("ROMP_CLAUDE_BIN")           # never a real CLI (conftest's floor; set here for a direct run too)
        os.environ["ROMP_CLAUDE_BIN"] = "/bin/false"
        self.addCleanup(lambda: os.environ.__setitem__("ROMP_CLAUDE_BIN", saved_bin) if saved_bin is not None
                        else os.environ.pop("ROMP_CLAUDE_BIN", None))
        self.root = Path(tempfile.mkdtemp(prefix="romp-asm-rebuild-k-")).resolve()
        (self.root / "romp").mkdir()
        (self.root / "romp" / "session-hosts").write_text("off\n")
        self._prior_state, self._prior_ckpt = jd.STATE, em._CKPT_DIR_FN
        jd._rebind_state(self.root / "romp")
        for d in ("states", "goals", "sdk", "checkpoints"):
            (jd.STATE / d).mkdir(parents=True, exist_ok=True)
        self.proj = self.root / "claude" / "projects" / "-w-notes-api"
        self.proj.mkdir(parents=True)
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.rows = []
        for name, val in (("_sessions", lambda now, **kw: list(self.rows)), ("_live_map", lambda: {}),
                          ("_display_sdk_human", lambda sid: False)):
            saved = getattr(km, name); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
        for name, val in (("ASM_REBUILD", True), ("ASM_CONVERGE", True), ("CKPT_CONVERGE_MS", 5000.0),
                          ("CKPT_CONVERGE_BYTES", em._CKPT_CYCLE_CAP_DEFAULT)):
            saved = getattr(km, name, None); setattr(km, name, val); self.addCleanup(setattr, km, name, saved)
        saved = getattr(em, "_ASM_REBUILD_MIN", None); em._ASM_REBUILD_MIN = FLOOR
        self.addCleanup(setattr, em, "_ASM_REBUILD_MIN", saved)
        self.fresh()

    def tearDown(self):
        _join_rebuilds()
        with em._ASM_LOCK:
            em._ASM_REFRESH["inflight"] = None
        km._live_scope.chat_floor0 = None
        jd._rebind_state(self._prior_state)
        em.set_checkpoint_dir(self._prior_ckpt)
        shutil.rmtree(self.root, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side: every reader, assembly, parse-store and chat entry gone, the rebuild table too."""
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")   # (also forgets the rebuild table, as a new process has none)
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear(); em._JSONL_CACHE_BYTES[0] = 0
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
            em._ASM_CKPT_REFUSED.clear()
        em._LAZY_FILES.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for c in list(em._FOLD_REG.values()):
            c.clear()
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        km._parse_mode.clear()
        with km._chat_fold_lock:
            km._chat_fold.clear()
        km._built_chat.clear(); km._prev_chat_events.clear(); km._push_first.clear()
        km._RENDER_FLOOR.clear(); getattr(km, "_RENDER_FLOOR_CUT", {}).clear()
        km._ASM_CONVERGE_DONE.clear(); km._ASM_CONVERGE_BLIP.clear(); km._ASM_CONVERGE_NOENTRY.clear(); km._ASM_CONVERGE_FLAG.clear()
        km._live_scope.chat_floor0 = None
        em._ASM_CKPT_STATS.update(written=0, restored=0, fallbacks={}, skipped={})
        em._ASM_CKPT_STATS["converge"] = {"writes": 0, "bytes": 0, "deferred": 0, "candidates": 0, "skipped": {}}
        with em._ASM_CKPT_LOCK:
            rs = getattr(em, "_ASM_REBUILD_STATS", {})
            for k in list(rs):
                rs[k] = 0
            getattr(em, "_ASM_REBUILD_DRAIN", {}).update(written=0, declined=0, failed=0, bytes=0, ms=0.0, running=0)
        for k in list(em._ASM_STATS):
            em._ASM_STATS[k] = 0

    def write(self, recs, leaf=None):
        leaf = leaf or self.leaf
        Path(leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))
        return leaf

    def boot_parse(self, sid=SID, leaf=None):
        """The boot's parse of the session (the judges' and the display's share it): the modes it took."""
        modes = []
        jd.parsed_session(sid, [leaf or self.leaf], NOW, asm_mode_out=modes)
        return modes

    def doc_file(self, leaf=None):
        return em._asm_ckpt_file(leaf or self.leaf)

    def doc(self, leaf=None):
        return json.loads(gzip.decompress(self.doc_file(leaf).read_bytes()))

    def evict_read(self, leaf=None):
        """The record cache's count cap takes the boot's whole read of the leaf (the reader's LRU evicts by count whatever an
        assembly entry holds: on the machine that met this, 1,644 count evictions in the kernel's first 27 minutes)."""
        with em._JSONL_CACHE_LOCK:
            em._cache_pop_locked(leaf or self.leaf)
        self.assertFalse(em.entry_whole_resident(leaf or self.leaf))

    def writer_cycle(self, now=NOW + 600):
        """One cycle of the kernel's document writer (the settle writes handed over, the checkpoint cycle, the converge pass,
        then the next rebuild, if one is due)."""
        km._document_write_phase(now)

    def restores_with_no_whole_parse(self, leaf=None):
        """The next boot: the parse restores from the document and reads only the tail, never the whole leaf."""
        leaf = leaf or self.leaf
        self.fresh()
        modes = self.boot_parse(leaf=leaf, sid=SID if leaf == self.leaf else SID2)
        self.assertEqual(modes, ["restore"], "restored from the document: %s" % em.asm_checkpoint_stats()["fallbacks"])
        self.assertFalse([k for k in em._ASM_STATS if k.startswith("full") and em._ASM_STATS[k]], "no whole parse: %s" % em._ASM_STATS)
        self.assertLess(em.read_bytes_report().get(leaf, 0), os.path.getsize(leaf) // 2, "the tail, not the whole leaf")


class TheRebuild(Harness):
    def _version_bumped(self):
        """A large session's document from the previous version, as the deploy boot of a version bump finds it."""
        self.write(transcript(260))
        self.assertGreater(os.path.getsize(self.leaf), FLOOR, "the premise: the leaf is over the rebuild floor")
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, False, tree=km._parse(self.leaf, SID, NOW)), em.asm_checkpoint_stats())
        d = self.doc(); d["av"] = em._ASM_CKPT_V - 1
        self.doc_file().write_bytes(gzip.compress(json.dumps(d).encode("utf-8")))
        self.fresh()                                               # the deploy boot
        self.assertEqual(self.boot_parse(), ["full"])
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("version"), 1, "refused for its version")
        self.assertFalse(self.doc_file().exists(), "and unlinked")

    def test_a_version_bumped_document_is_rebuilt_after_boot_and_the_next_boot_restores_from_it(self):
        self._version_bumped()
        self.evict_read()                                          # the boot's read gone: the converge pass has nothing to write from
        km._begin_checkpoint_cycle(); km._converge_checkpoints(NOW + 600)
        self.assertFalse(self.doc_file().exists(), "the converge pass alone writes nothing (the gap this closes)")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.writer_cycle()
            _join_rebuilds()
            self.writer_cycle(NOW + 601)                           # the drain is empty: its summary line
        self.assertTrue(self.doc_file().exists(), "rebuilt off the hot path: %s" % em.asm_checkpoint_stats().get("rebuild"))
        self.assertEqual(self.doc()["av"], em._ASM_CKPT_V, "at this version")
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["noted"], rb["claimed"], rb["written"], rb["pending"]), (1, 1, 1, 0), rb)
        out = err.getvalue()
        self.assertEqual(out.count("rebuilt the document of %s (refused;" % self.leaf), 1, out)
        self.assertEqual(out.count("assembly checkpoint: rebuilt 1 document ("), 1, "one summary line for the drain: %s" % out)
        self.assertFalse(em.entry_whole_resident(self.leaf), "the rebuild's own read of the leaf was let go once written")
        self.restores_with_no_whole_parse()

    def test_a_missing_document_is_rebuilt_the_same_way(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.assertEqual(self.boot_parse(), ["full"])
        self.assertEqual(em._ASM_STATS.get("full:noDocument"), 1)
        self.evict_read()
        self.writer_cycle()
        _join_rebuilds()
        self.assertTrue(self.doc_file().exists(), "%s" % em.asm_checkpoint_stats().get("rebuild"))
        self.assertEqual(em.asm_checkpoint_stats()["rebuild"]["written"], 1)
        self.restores_with_no_whole_parse()

    def test_a_page_connect_during_the_rebuild_is_not_blocked(self):
        """The rebuild's whole parse holds no lock a reader of the leaf takes: a page connect's chat build of the same session
        (a targeted push: the parse, the render) completes while the rebuild is held mid-parse, and the writer's cycle that
        started it returned at once. The rebuild thread runs at the lowest CPU priority."""
        self.write(transcript(260))
        self.rows = [{"sid": SID, "name": "api", "path": self.leaf, "mtime": NOW, "anchor": SID}]
        self.boot_parse()
        self.evict_read()
        started, release, seen = threading.Event(), threading.Event(), {}
        real = em._asm_whole_entry

        def held(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                seen["nice"] = os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) if sys.platform.startswith("linux") else None
                started.set()
                release.wait(60)
            return real(*a, **k)
        try:
            with mock.patch.object(em, "_asm_whole_entry", held):
                self.writer_cycle()
                self.assertTrue(started.wait(30), "the writer's cycle started the rebuild and returned")
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()   # the connect's build parses the leaf, not a stored tree
                done, box = threading.Event(), {}

                def connect():
                    box["m"] = km.build_session(SID, NOW + 1, {})
                    done.set()
                threading.Thread(target=connect, name="connect-probe", daemon=True).start()
                self.assertTrue(done.wait(60), "the page connect's build finished while the rebuild was held")
                self.assertTrue(box["m"] and box["m"].get("events"), "and built the session")
                self.assertTrue(any(t.name == "asm-rebuild" and t.is_alive() for t in threading.enumerate()), "the rebuild still held")
                self.assertFalse(km._asm_rebuild_maybe(), "one at a time: no second rebuild while one runs")
        finally:
            release.set()
            _join_rebuilds()
        if seen.get("nice") is not None:
            self.assertEqual(seen["nice"], 19, "the rebuild thread runs at the lowest priority")
        self.assertTrue(self.doc_file().exists())

    def test_largest_first_one_at_a_time(self):
        small_leaf = str(self.proj / (SID2 + ".jsonl"))
        self.write(transcript(320, tag="big"))
        self.write(transcript(220, tag="small"), leaf=small_leaf)
        self.rows = [{"sid": SID, "path": self.leaf}, {"sid": SID2, "path": small_leaf}]
        self.boot_parse(SID2, small_leaf); self.boot_parse(SID, self.leaf)   # the smaller one parsed (and noted) first
        self.evict_read(small_leaf); self.evict_read(self.leaf)
        order, real = [], jd.asm_refresh

        def recording(fsid, leaf, now, **kw):
            order.append(fsid)
            return real(fsid, leaf, now, **kw)
        with mock.patch.object(jd, "asm_refresh", recording):
            for k in range(4):
                self.writer_cycle(NOW + 600 + k)
                _join_rebuilds()
        self.assertEqual(order, [SID, SID2], "the larger leaf first, then the next")
        self.assertTrue(self.doc_file().exists() and self.doc_file(small_leaf).exists())

    def test_tiny_and_gone_sessions_are_not_rebuilt(self):
        tiny = str(self.proj / (SID2 + ".jsonl"))
        self.write(transcript(12, tag="tiny"), leaf=tiny)
        self.assertLess(os.path.getsize(tiny), FLOOR)
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}, {"sid": SID2, "path": tiny}]
        self.boot_parse(SID2, tiny); self.boot_parse()
        self.assertEqual([p[0] for p in em.asm_rebuild_pending()], [self.leaf], "the leaf under the floor is never noted")
        self.evict_read()
        os.unlink(self.leaf)                                       # the session's transcript is gone
        self.writer_cycle()
        _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["gone"], rb["pending"]), (0, 1, 0), rb)

    def test_a_leaf_the_boots_read_still_holds_is_left_to_the_converge_pass(self):
        """No second parse where the converge pass can write from the read in hand: the rebuild waits, and the pass writes it."""
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.assertTrue(em.entry_whole_resident(self.leaf))
        with mock.patch.object(km, "_converge_checkpoints", lambda now: 0):
            self.assertFalse(km._asm_rebuild_maybe(), "left to the converge pass")
        self.writer_cycle()
        _join_rebuilds()
        self.assertTrue(self.doc_file().exists())
        av = em.asm_checkpoint_stats()["converge"]
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((av["writes"], rb["claimed"], rb["moot"]), (1, 0, 1), (av, rb))

    def test_a_rebuild_that_raises_before_its_parse_releases_the_claim_and_the_drain_still_ends(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()
        err = io.StringIO()
        with mock.patch.object(jd, "asm_refresh", side_effect=RuntimeError("synthetic")), contextlib.redirect_stderr(err):
            self.writer_cycle(); _join_rebuilds()
            self.writer_cycle(NOW + 601)
        self.assertFalse(em.asm_refresh_running(), "the claim was released")
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["failed"], rb["pending"]), (1, 1, 0), rb)
        self.assertIn("assembly checkpoint: rebuilt 0 documents (", err.getvalue())
        self.assertIn("; 1 failed", err.getvalue())

    def test_a_declined_rebuild_is_not_tried_again_until_the_leaf_moves(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()
        saved = em._ASM_FIRST_DOC_MIN; em._ASM_FIRST_DOC_MIN = 1 << 40   # the writer's young floor declines every first document
        self.addCleanup(setattr, em, "_ASM_FIRST_DOC_MIN", saved)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.writer_cycle(); _join_rebuilds()
        self.assertFalse(self.doc_file().exists())
        self.assertIn("was not rebuilt (declined: young)", err.getvalue())
        self.assertFalse(em.asm_rebuild_note(self.leaf, SID, "noDocument"), "not noted again at the same stat")
        with open(self.leaf, "a") as f:
            f.write(json.dumps(transcript(1, tag="more", t0=NOW)[0]) + "\n")
        self.assertTrue(em.asm_rebuild_note(self.leaf, SID, "noDocument"), "noted again once the leaf moves")


class TheConvergePassHandsOverALeafItCanNeverFit(Harness):
    """Question two of the diagnosis: an idle session never settles, so the converge pass is its one writer, and the pass took
    a sixty-fourth of the leaf's size from a cycle budget of 8 MB: a leaf over 512 MB fit no cycle, was deferred at every
    cycle, and the deferral stopped the step, so the leaves after it in the cache's order waited as well."""

    def test_an_over_cap_leaf_goes_to_the_rebuild_and_the_next_leaf_is_written_in_the_same_cycle(self):
        small_leaf = str(self.proj / (SID2 + ".jsonl"))
        self.write(transcript(320, tag="big"))
        self.write(transcript(220, tag="small"), leaf=small_leaf)
        self.rows = [{"sid": SID, "path": self.leaf}, {"sid": SID2, "path": small_leaf}]
        self.boot_parse(SID, self.leaf); self.boot_parse(SID2, small_leaf)   # the big one first in the cache's order
        est = getattr(km, "_converge_assembly_est", lambda n: max(4096, n // 64))   # the pass's estimate: a sixty-fourth of the leaf
        big_est, small_est = est(os.path.getsize(self.leaf)), est(os.path.getsize(small_leaf))
        km.CKPT_CONVERGE_BYTES = (big_est + small_est) // 2        # the big leaf's estimate is over the whole cycle's budget
        self.assertGreater(big_est, km.CKPT_CONVERGE_BYTES); self.assertLess(small_est, km.CKPT_CONVERGE_BYTES)
        km._begin_checkpoint_cycle(); km._converge_checkpoints(NOW + 600)
        av = em.asm_checkpoint_stats()["converge"]
        self.assertTrue(self.doc_file(small_leaf).exists(), "the leaf after it was written in the same cycle: %s" % av)
        self.assertEqual((av["deferred"], av["skipped"].get("overCap")), (0, 1), av)
        self.assertFalse(self.doc_file().exists())
        self.assertEqual([p[:2] for p in em.asm_rebuild_pending()], [(self.leaf, SID)], "the rebuild's (the parse noted it too)")
        self.writer_cycle(NOW + 601)
        _join_rebuilds()
        self.assertTrue(self.doc_file().exists(), "%s" % em.asm_checkpoint_stats().get("rebuild"))
        self.restores_with_no_whole_parse()


class FollowerFloor(Harness):
    """Question one of the diagnosis: the pusher records the floor its last build used, and every build outside its cycle (a
    page connect's targeted push, a history reply) followed it. Built over the boot's whole parse, that floor was 0; once the
    document was written and the entry re-seated on it, the tree's cut moved up, but the record stood (the pusher's cached
    build did not move), so the connect's build rendered and hydrated every turn from 0."""

    def _pusher(self, now):
        km._live_scope.chat_floor0 = False                     # the pusher's cycle, no index client connected
        try:
            return km.build_session(SID, now, {})
        finally:
            km._live_scope.chat_floor0 = None

    def _whole_then_documented(self):
        recs = transcript(160)
        self.write(recs)
        self.rows = [{"sid": SID, "name": "api", "path": self.leaf, "mtime": NOW, "anchor": SID}]
        m0 = self._pusher(NOW)
        self.assertEqual((m0["floor"], km._RENDER_FLOOR[SID]), (0, 0), "the whole parse's build: floor 0, recorded")
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, False, tree=km._parse(self.leaf, SID, NOW)), em.asm_checkpoint_stats())
        more = transcript(2, tag="more", t0=NOW - 600)
        more[0]["parentUuid"] = recs[-1]["uuid"]
        self.write(recs + more)                                  # the session moves on: the next parse re-seats on the document
        tree = km._parse(self.leaf, SID, NOW + 1)
        cut = km._asm_cut_turn(tree)
        self.assertGreater(cut, 0, "the tree has a cut now: %s" % em._ASM_STATS)
        return cut

    def test_a_connect_build_after_the_document_exists_starts_from_the_cut(self):
        cut = self._whole_then_documented()
        km._built_chat[SID] = ("stale-sig", {"events": []}, None, None)   # the pusher's cached build over the whole parse
        m = km.build_session(SID, NOW + 2, {})                   # a page connect's targeted push: no pusher scope
        self.assertEqual(m["floor"], cut, "from the cut, not turn 0")
        self.assertNotIn(SID, km._built_chat, "the pusher's cached build over the older tree is dropped")
        self.assertIn(SID, km._push_first, "and the session named to its next cycle")
        m2 = self._pusher(NOW + 3)
        self.assertEqual(m2["floor"], cut, "the pusher rebuilds at the same cut: the two lists line up")
        self.assertEqual([e.get("uuid") for e in m2["events"]], [e.get("uuid") for e in m["events"]])

    def test_a_floor_held_at_zero_for_an_index_client_is_still_followed(self):
        cut = self._whole_then_documented()
        km._live_scope.chat_floor0 = True                        # an index client is connected: the pusher holds every tab at 0
        try:
            km.build_session(SID, NOW + 2, {})
        finally:
            km._live_scope.chat_floor0 = None
        self.assertGreater(cut, 0)
        m = km.build_session(SID, NOW + 3, {})
        self.assertEqual(m["floor"], 0, "followed: the index client needs the whole list")


if __name__ == "__main__":
    unittest.main()
