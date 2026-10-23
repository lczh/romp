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

And the review of PR #367 (2026-10-23): a rebuild starts, and carries on, only while the pusher sleeps between its cycles and
no page parses whole or connects (nice 19 did not keep it off the pages, which wait on the interpreter, not on a core); a leaf
is noted only once the page's own whole parse is done, so no rebuild parses it beside that parse, and a rebuild whose leaf
gained a document while it worked writes nothing over it; two writers of one leaf never share a temp name; a rebuild that
raises is tried once more, and a second raise is said loudly.

And the review of PR #370: every cycle's end opens the gate, busy or idle (an idle cycle's alone gave a busy host's rebuilds
about 45 s an hour); a rebuild waiting at its gate lends the shared claim to a settle's refresh, and takes it back before it
goes on; and a document a refusal removes under an unchanged leaf is written again by the converge pass, which had set the
leaf aside for as long as the leaf stood unchanged.

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
import time
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


def _rebuild_alive():
    return any(t.name == "asm-rebuild" and t.is_alive() for t in threading.enumerate())


def _gate_edge(asleep):
    """The pusher's cycle edge for the background gate: False as a cycle begins, True as a cycle ends and the pusher sleeps
    (getattr: the base had no gate, and its rebuild started from the document writer's cycle whatever the pusher was doing)."""
    gate = getattr(em, "asm_background_idle", None)
    if gate is not None:
        gate(asleep)


def _cycle_end():
    """The end of a pusher cycle: the gate opens, and the next rebuild starts if one is due (_asm_rebuild_tick). A busy cycle's
    end is the real cycle's (_pusher_cycle, its jobs stubbed busy), since #370's tick took the idle bit."""
    tick = getattr(km, "_asm_rebuild_tick", None)                  # the base started it in the writer's cycle
    if tick is None:
        return None
    try:
        return tick()
    except TypeError:
        return tick(True)                                          # #370's signature: an idle cycle's end, the one that opened it


def _until(pred, timeout=30.0):
    """Wait for `pred` to hold, by polling: the rebuild's thread reaching its chunk point is an event this test can only observe."""
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return bool(pred())


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
        _gate_edge(True)                                          # no pusher runs here: the gate opens as a cycle ends
        self.fresh()

    def tearDown(self):
        _gate_edge(True)                                          # a rebuild a test held at a chunk point finishes
        _join_rebuilds()
        for th in [t for t in threading.enumerate() if t.name == "asm-refresh"]:
            th.join(timeout=60)
        with em._ASM_LOCK:
            em._ASM_REFRESH["inflight"] = None
            if "paused" in em._ASM_REFRESH:
                em._ASM_REFRESH["paused"] = None
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
        """One cycle of the kernel's document writer (the settle writes handed over, the checkpoint cycle, the converge pass),
        then the end of a pusher cycle, which starts the next rebuild, if one is due (_asm_rebuild_tick)."""
        km._document_write_phase(now)
        _gate_edge(True)                                           # (#370's: the end of an idle cycle)
        _cycle_end()

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
            self.writer_cycle(NOW + 601); _join_rebuilds()        # the one retry a raise gets (the review of PR #367)
            self.writer_cycle(NOW + 602)
        self.assertFalse(em.asm_refresh_running(), "the claim was released")
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["retry"], rb["failed"], rb["pending"]), (2, 1, 1, 0), rb)
        self.assertIn("assembly checkpoint: rebuilt 0 documents (", err.getvalue())
        self.assertIn("; 1 failed", err.getvalue())
        self.assertEqual(err.getvalue().count("FAILED TWICE"), 1, err.getvalue())

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


class APageParseAndTheRebuild(Harness):
    """The review of PR #367, issue one. The leaf was noted BEFORE the page's whole parse ran, so the converge pass did not own it
    yet (no whole entry), and any document writer cycle during the parse, about every 2.5 s, started a rebuild that parsed the
    same file beside the page; and a rebuild asked whether a document stood only before its own parse, so one the converge pass
    wrote meanwhile, from the page's parse, was written over a second time."""

    def test_no_rebuild_parses_beside_a_page_parse_and_the_converge_pass_writes_the_document_once(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        entered, release, lock = threading.Event(), threading.Event(), threading.Lock()
        parses, beside, inflight = [], [], [0]
        real = em._asm_whole_entry

        def whole(*a, **k):                                        # every whole parse of the leaf, by thread
            name = threading.current_thread().name
            with lock:
                inflight[0] += 1
                parses.append(name)
                if inflight[0] > 1:
                    beside.append(name)
            try:
                if name == "page-parse":
                    entered.set()
                    release.wait(60)                               # the page's parse, held in flight
                return real(*a, **k)
            finally:
                with lock:
                    inflight[0] -= 1
        with mock.patch.object(em, "_asm_whole_entry", whole):
            page = threading.Thread(target=self.boot_parse, name="page-parse", daemon=True)
            page.start()
            try:
                self.assertTrue(entered.wait(30), "the page's whole parse is in flight")
                for k in range(3):                                 # writer cycles, each followed by a pusher cycle's end
                    self.writer_cycle(NOW + 600 + k)
                self.assertFalse(_rebuild_alive(), "no rebuild runs beside the page's parse")
                self.assertEqual(em.asm_checkpoint_stats()["rebuild"]["claimed"], 0, em.asm_checkpoint_stats()["rebuild"])
            finally:
                release.set()
                page.join(60)
                _join_rebuilds()
            for k in range(3):                                     # the parse is done: the converge pass writes from it
                self.writer_cycle(NOW + 610 + k)
                _join_rebuilds()
        self.assertEqual(beside, [], "no two whole parses of the leaf at once")
        self.assertEqual(parses, ["page-parse"], "one whole parse: the page's")
        self.assertTrue(self.doc_file().exists())
        st, rb = em.asm_checkpoint_stats(), em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((st["written"], st["converge"]["writes"], rb["claimed"], rb["written"]), (1, 1, 0, 0), (st["converge"], rb))
        self.restores_with_no_whole_parse()

    def test_a_rebuild_whose_leaf_gained_a_document_while_it_parsed_writes_nothing_over_it(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()                                          # noted; the converge pass has nothing to write from
        parsed, release = threading.Event(), threading.Event()
        real = em.parse_session

        def parse(*a, **k):
            out = real(*a, **k)
            if threading.current_thread().name == "asm-rebuild":
                parsed.set()
                release.wait(60)                                   # the rebuild's parse is done; its write is next
            return out
        with mock.patch.object(em, "parse_session", parse):
            try:
                self.writer_cycle()
                self.assertTrue(parsed.wait(60), "the rebuild started and parsed")
                with em._ASM_LOCK:                                 # a page opens the session: its whole parse ...
                    em._ASM_CACHE.clear()
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                self.assertIn("full", self.boot_parse())
                km._begin_checkpoint_cycle(); km._converge_checkpoints(NOW + 605)   # ... and the converge pass writes from it
                self.assertTrue(self.doc_file().exists())
                standing = self.doc_file().read_bytes()
                written = em.asm_checkpoint_stats()["written"]
            finally:
                release.set()
                _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["written"], rb["moot"]), (1, 0, 1), rb)
        self.assertEqual(em.asm_checkpoint_stats()["written"], written, "no second write")
        self.assertEqual(self.doc_file().read_bytes(), standing, "the converge pass's document stands, not written over")
        self.restores_with_no_whole_parse()


_QUIET_CYCLE = dict(_live_build=lambda: ({}, 0, 0.0), _gc_freeze_tick=lambda idle, first: None,
                    _boot_health_first_cycle=lambda *a: None, _boot_health_row_backstop=lambda *a: None,
                    _first_cycle_sampler_start=lambda *a: None, _first_cycle_sampler_stop=lambda *a: None)


class TheRebuildRunsWhileThePusherSleeps(Harness):
    """The review of PR #367, issue two: nice 19 did not protect the pages. The rebuild competes for the interpreter, not for a
    core (in a lab a page's 40 MB whole parse took 2.1 times as long beside a rebuild, with full collections of 0.65 to 1.04 s).
    It runs while the pusher sleeps between its cycles, and waits at its chunk points whenever a cycle runs, a page parses whole
    or a page connects. The review of PR #370: the gate opened at an IDLE cycle's end alone, and on a host of 65 sessions, a
    tenth of whose cycles are idle, that left the rebuild about 45 s an hour; the pusher sleeps after a busy cycle as much as
    after an idle one, so every cycle's end opens it now."""

    def _noted(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()                                          # noted; nothing for the converge pass to write from

    def _counting_yield(self, passed):
        """em._asm_bg_yield, counting every chunk point the rebuild's thread passes (a wait's end included)."""
        real = em._asm_bg_yield

        def counted():
            waited = real()
            if threading.current_thread().name == "asm-rebuild":
                passed[0] += 1
            return waited
        return counted

    def test_a_busy_cycles_end_starts_the_rebuild(self):
        """The real cycle, its jobs stubbed busy: the gate is shut while the cycle runs, and its end starts the rebuild."""
        self._noted()
        seen = []

        def jobs(now, live_map, any_client):
            seen.append(em.asm_background_open())
            km._PERF_STATS.wake()                                  # the cycle changed something: not idle
        with mock.patch.multiple(km, **_QUIET_CYCLE), mock.patch.object(km, "_pusher_cycle_jobs", jobs):
            km._pusher_cycle()
            _join_rebuilds()
        self.assertEqual(seen, [False], "the gate is shut while the cycle runs")
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["written"]), (1, 1), "the busy cycle's end started it: %s" % rb)
        self.assertTrue(self.doc_file().exists())

    def test_under_a_pusher_that_is_never_idle_the_rebuild_goes_on_between_cycles_and_finishes(self):
        """No idle cycle at all, as on a busy host: the rebuild starts at the first cycle's end, passes no chunk point while a
        cycle runs, goes on in the sleeps between them, and finishes."""
        self._noted()
        passed, seen, during = [0], [], []

        def jobs(now, live_map, any_client):
            km._PERF_STATS.wake()                                  # every cycle busy
            seen.append(em.asm_background_open())
            time.sleep(0.05)                                       # a chunk point passed as the gate shut has counted by now
            c0 = passed[0]
            time.sleep(0.03)                                       # the cycle's work: no chunk point passes in it
            during.append(passed[0] - c0)
        end = time.monotonic() + 60
        with mock.patch.multiple(km, **_QUIET_CYCLE), mock.patch.object(km, "_pusher_cycle_jobs", jobs), \
                mock.patch.object(em, "_asm_bg_yield", self._counting_yield(passed)):
            while time.monotonic() < end:
                km._pusher_cycle()
                if self.doc_file().exists() and not _rebuild_alive():
                    break
                time.sleep(0.01)                                   # the pusher's sleep until its next wake
            _gate_edge(True)
            _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertTrue(self.doc_file().exists(), "the rebuild finished with no idle cycle: %s" % rb)
        self.assertEqual((rb["claimed"], rb["written"]), (1, 1), rb)
        self.assertFalse(any(seen), "the gate was shut in every cycle")
        self.assertEqual(sum(during), 0, "no chunk point passed while a cycle ran: %s" % during)
        self.assertGreaterEqual(rb["yields"], 1, "a cycle held it, and it went on in a later sleep: %s" % rb)
        self.assertGreaterEqual(len(seen), 2, "over more than one cycle")
        self.assertGreater(passed[0], 0)

    def test_a_page_parse_in_flight_holds_the_rebuild_through_the_pushers_sleeps(self):
        self._noted()
        passed, during = [0], []

        def jobs(now, live_map, any_client):
            km._PERF_STATS.wake()
        with mock.patch.multiple(km, **_QUIET_CYCLE), mock.patch.object(km, "_pusher_cycle_jobs", jobs), \
                mock.patch.object(em, "_asm_bg_yield", self._counting_yield(passed)):
            with em.asm_foreground():                              # a page's whole parse, in flight across cycles and sleeps
                km._pusher_cycle()                                 # its end starts no rebuild: the gate is shut
                self.assertFalse(_rebuild_alive())
                self.assertEqual(em.asm_checkpoint_stats()["rebuild"]["claimed"], 0)
            km._pusher_cycle()                                     # the parse is done: this cycle's end starts it
            self.assertEqual(em.asm_checkpoint_stats()["rebuild"]["claimed"], 1)
            with em.asm_foreground():                              # another page parses while the rebuild runs
                time.sleep(0.05)                                   # (a chunk point passed as it began has counted by now)
                c0 = passed[0]
                for _ in range(3):
                    km._pusher_cycle()
                    time.sleep(0.02)                               # the pusher sleeps: the page's parse still holds the gate
                during.append(passed[0] - c0)
                self.assertFalse(self.doc_file().exists())
            km._pusher_cycle()
            _join_rebuilds()
        self.assertEqual(during, [0], "no chunk point passed while the page parsed")
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["written"], rb["waiting"]), (1, 1, 0), rb)
        self.assertTrue(self.doc_file().exists())

    def test_a_running_rebuild_waits_while_a_cycle_runs_or_a_page_parses_and_carries_on_at_the_next_cycles_end(self):
        self._noted()
        started, go = threading.Event(), threading.Event()
        real = em._asm_whole_entry

        def held(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                started.set()
                go.wait(60)
            return real(*a, **k)

        def waiting():
            return em.asm_checkpoint_stats()["rebuild"].get("waiting") == 1
        with mock.patch.object(em, "_asm_whole_entry", held):
            try:
                self.writer_cycle()
                self.assertTrue(started.wait(30), "started at the cycle's end")
                _gate_edge(False)                                  # the next pusher cycle begins
                go.set()
                self.assertTrue(_until(waiting), "held at its next chunk point (after the read)")
                with em.asm_foreground():                          # a page parses whole: the cycle's end does not open it
                    _cycle_end()
                    self.assertTrue(waiting())
                    self.assertFalse(self.doc_file().exists())
                _join_rebuilds()                                   # the page's parse is done and the pusher sleeps
            finally:
                go.set()
                _gate_edge(True)
                _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["written"], rb["waiting"]), (1, 1, 0), rb)
        self.assertGreaterEqual(rb["yields"], 1, rb)
        self.assertTrue(self.doc_file().exists())

    def test_a_page_connects_push_holds_the_gate_shut(self):
        seen = []

        @contextlib.contextmanager
        def scope():
            yield None
        with mock.patch.object(km, "_push", lambda *a, **k: seen.append(em.asm_background_open())), \
                mock.patch.object(km, "_connect_push_scope", scope):
            km.Handler._push_one(None, {"app": "board"})
        self.assertEqual(seen, [False], "shut while the connect's push runs")
        self.assertTrue(em.asm_background_open(), "and open again after it")


def _waiting():
    return em.asm_checkpoint_stats()["rebuild"].get("waiting") == 1


def _join_refreshes(timeout=60):
    for th in [t for t in threading.enumerate() if t.name == "asm-refresh"]:
        th.join(timeout=timeout)


class ARefreshWhileARebuildWaits(Harness):
    """The review of PR #370, issue two: a waiting rebuild held the shared claim through every wait, so asm_refresh_claim refused
    every settle's refresh, of every session, for as long as the rebuild took (hours on a busy host), worse than before the
    rebuild existed. A waiting rebuild lends the claim out now: the settle's refresh, cheap and targeted, runs, and the rebuild
    takes the claim back before it goes on, so one whole parse still runs at a time."""

    def setUp(self):
        super().setUp()
        self.other = str(self.proj / (SID2 + ".jsonl"))

    def _leaf_cut(self, leaf):
        return int(self.doc(leaf)["files"][Path(leaf).stem]["cut"][0])

    def _restored_and_noted(self):
        """Session two restored from its document with a refresh due at its settle (its leaf grew past the churn bound's share
        since), and session one, larger, with no document: noted for a rebuild, the boot's read of it gone."""
        recs = transcript(200, tag="r")
        self.write(recs, leaf=self.other)
        self.rows = [{"sid": SID2, "path": self.other}]
        self.boot_parse(SID2, self.other)
        self.assertTrue(em.asm_checkpoint_write(self.other, SID2, False, tree=km._parse(self.other, SID2, NOW)),
                        em.asm_checkpoint_stats())
        more = transcript(60, tag="r2", t0=NOW - 3600, compact_every=10 ** 6)
        more[0]["parentUuid"] = recs[-1]["uuid"]
        self.write(recs + more, leaf=self.other)
        self.cut0 = self._leaf_cut(self.other)
        self.fresh()                                               # a restart
        self.write(transcript(320, tag="big"))
        self.rows = [{"sid": SID, "path": self.leaf}, {"sid": SID2, "path": self.other}]
        self.assertEqual(self.boot_parse(SID2, self.other), ["restore"])
        self.boot_parse()
        self.evict_read()
        self.assertEqual([p[0] for p in em.asm_rebuild_pending()], [self.leaf])
        self.rf0 = dict(em.asm_checkpoint_stats()["refresh"])     # the refresh's counts run for the module's life

    def refreshes(self):
        rf = em.asm_checkpoint_stats()["refresh"]
        return {k: rf[k] - self.rf0.get(k, 0) for k in ("claimed", "written", "refused", "failed")}

    def test_a_settles_refresh_runs_while_a_rebuild_waits_and_the_rebuild_then_finishes(self):
        self._restored_and_noted()
        started, go = threading.Event(), threading.Event()
        real = em._asm_whole_entry

        def held(*a, **k):
            if threading.current_thread().name == "asm-rebuild" and not started.is_set():
                started.set()
                go.wait(60)
            return real(*a, **k)
        with mock.patch.object(em, "_asm_whole_entry", held):
            try:
                self.writer_cycle()
                self.assertTrue(started.wait(30), "the rebuild started")
                _gate_edge(False)                                  # a pusher cycle begins: the rebuild waits at its next chunk point
                go.set()
                self.assertTrue(_until(_waiting), "the rebuild waits at the gate")
                self.assertTrue(km._asm_refresh_maybe(self.other, SID2), "the settle's refresh is not refused")
                _join_refreshes()
                self.assertGreater(self._leaf_cut(self.other), self.cut0, "and it wrote session two's document")
                self.assertTrue(_waiting(), "the rebuild still waits for the pusher's sleep")
                self.assertTrue(em.asm_refresh_running(), "a waiting rebuild still counts: no second rebuild starts")
                self.assertFalse(self.doc_file().exists())
            finally:
                go.set()
                _gate_edge(True)
                _join_rebuilds()
        rb, rf = em.asm_checkpoint_stats()["rebuild"], self.refreshes()
        self.assertEqual((rf["claimed"], rf["written"]), (1, 1), rf)
        self.assertEqual((rb["claimed"], rb["written"], rb["waiting"]), (1, 1, 0), rb)
        self.assertTrue(self.doc_file().exists(), "the rebuild took its claim back and finished")
        self.assertFalse(em.asm_refresh_running(), "and released it")
        self.restores_with_no_whole_parse()

    def test_a_rebuild_whose_gate_opens_while_a_refresh_runs_goes_on_once_the_refresh_is_done(self):
        """One whole parse at a time: the gate opens while the refresh still parses, and the rebuild waits on for the claim."""
        self._restored_and_noted()
        b_started, b_go, r_started, r_go = threading.Event(), threading.Event(), threading.Event(), threading.Event()
        refreshing, overlap = [False], []
        real, real_yield = em._asm_whole_entry, em._asm_bg_yield

        def held(*a, **k):
            name = threading.current_thread().name
            if name == "asm-rebuild" and not b_started.is_set():
                b_started.set()
                b_go.wait(60)
            if name != "asm-refresh":
                return real(*a, **k)
            refreshing[0] = True
            r_started.set()
            r_go.wait(60)
            try:
                return real(*a, **k)
            finally:
                refreshing[0] = False

        def counted():
            waited = real_yield()
            if threading.current_thread().name == "asm-rebuild" and refreshing[0]:
                overlap.append(waited)                             # a chunk point passed while the refresh parsed
            return waited
        with mock.patch.object(em, "_asm_whole_entry", held), mock.patch.object(em, "_asm_bg_yield", counted):
            try:
                self.writer_cycle()
                self.assertTrue(b_started.wait(30))
                _gate_edge(False)
                b_go.set()
                self.assertTrue(_until(_waiting), "the rebuild waits at the gate")
                self.assertTrue(km._asm_refresh_maybe(self.other, SID2), "the settle's refresh is not refused")
                self.assertTrue(r_started.wait(30), "the refresh parses")
                _gate_edge(True)                                   # the cycle ends: the gate opens while the refresh still runs
                time.sleep(0.2)
                self.assertTrue(_waiting(), "the rebuild waits on: the refresh holds the claim")
                self.assertFalse(self.doc_file().exists())
                r_go.set()
                _join_refreshes()
                _join_rebuilds()
            finally:
                b_go.set(); r_go.set()
                _gate_edge(True)
                _join_refreshes()
                _join_rebuilds()
        self.assertEqual(overlap, [], "no chunk point of the rebuild passed while the refresh parsed")
        rb, rf = em.asm_checkpoint_stats()["rebuild"], self.refreshes()
        self.assertEqual((rf["written"], rb["written"], rb["lent"]), (1, 1, 1), (rf, rb))
        self.assertGreater(self._leaf_cut(self.other), self.cut0)
        self.assertTrue(self.doc_file().exists())
        self.assertFalse(em.asm_refresh_running())

    def test_a_rebuild_waiting_in_its_writer_stands_down_when_a_document_appears(self):
        """Taking its claim back, the rebuild asks again whether its leaf has a document: one another writer put there while it
        waited ends the write at that chunk point (`moot`), with the identity proof and the encode unspent."""
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()
        in_writer, proofs = threading.Event(), []
        real_write, real_ident = em.asm_checkpoint_write, em._pre_tree_identity

        def write(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                _gate_edge(False)                                  # a cycle begins as the rebuild's writer starts
                in_writer.set()
            return real_write(*a, **k)

        def ident(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                proofs.append(1)
            return real_ident(*a, **k)
        with mock.patch.object(em, "asm_checkpoint_write", write), mock.patch.object(em, "_pre_tree_identity", ident):
            try:
                self.writer_cycle()
                self.assertTrue(in_writer.wait(60), "the rebuild parsed and its writer began")
                self.assertTrue(_until(_waiting), "and waits at the writer's first chunk point")
                with em._ASM_LOCK:                                 # a page opens the session: its whole parse ...
                    em._ASM_CACHE.clear()
                jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
                self.assertIn("full", self.boot_parse())
                km._begin_checkpoint_cycle(); km._converge_checkpoints(NOW + 605)   # ... and the converge pass writes from it
                self.assertTrue(self.doc_file().exists())
                standing = self.doc_file().read_bytes()
            finally:
                _gate_edge(True)
                _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["written"], rb["moot"]), (1, 0, 1), rb)
        self.assertEqual(proofs, [], "the identity proof never ran: the write stood down at its chunk point")
        self.assertEqual(self.doc_file().read_bytes(), standing, "the converge pass's document stands")


class ADocumentRefusedUnderAnUnchangedLeaf(Harness):
    """The review of PR #370, issue three (older than the rebuild; #370 made it certain): the converge pass sets a leaf aside
    once a document stands there, keyed on the leaf's stat alone; a refusal then unlinks the document while the leaf stays as it
    was, and the rebuild leaves the leaf to that pass (its whole entry resident, its estimate in budget), so neither wrote it
    until the leaf changed or the kernel restarted, and every boot until then parsed it whole."""

    def test_a_refused_then_unlinked_document_on_an_unchanged_leaf_is_written_again(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.assertEqual(self.boot_parse(), ["full"])
        self.writer_cycle()                                        # the converge pass writes it from the boot's read ...
        _join_rebuilds()
        self.assertTrue(self.doc_file().exists())
        self.writer_cycle(NOW + 601)                               # ... and a later cycle finds it standing
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["writes"], 1)
        st0 = os.stat(self.leaf)
        d = self.doc(); d["av"] = em._ASM_CKPT_V - 1              # the next restore refuses it (a version refusal)
        self.doc_file().write_bytes(gzip.compress(json.dumps(d).encode("utf-8")))
        with em._ASM_LOCK:                                         # a page opens the session: its parse restores, refuses and
            em._ASM_CACHE.clear()                                  #  unlinks the document, and parses whole
        jd._PARSE_CACHE.clear(); jd._CHAIN_MEMO.clear()
        self.assertEqual(self.boot_parse(), ["full"])
        self.assertEqual(em.asm_checkpoint_stats()["fallbacks"].get("version"), 1)
        self.assertFalse(self.doc_file().exists(), "refused and unlinked")
        st1 = os.stat(self.leaf)
        self.assertEqual((st1.st_size, st1.st_mtime_ns), (st0.st_size, st0.st_mtime_ns), "the leaf unchanged")
        for k in range(3):
            self.writer_cycle(NOW + 610 + k)
            _join_rebuilds()
        self.assertTrue(self.doc_file().exists(), "written again: %s %s" % (em.asm_checkpoint_stats()["converge"],
                                                                          em.asm_checkpoint_stats()["rebuild"]))
        self.assertEqual(self.doc()["av"], em._ASM_CKPT_V)
        self.assertEqual(em.asm_checkpoint_stats()["converge"]["writes"], 2, "by the converge pass, from the page's read")
        self.restores_with_no_whole_parse()


class TwoWritersOfOneLeaf(Harness):
    """The review of PR #367: the sidecar's temp name was the process's alone, so two writers of one leaf (the rebuild's thread
    and the converge pass) staged the sidecar under the same name; the second's replace moved the first's file away, and the
    first's own replace then found nothing and dropped its write."""

    def test_two_writers_of_one_leaf_never_share_a_temp_name(self):
        self.write(transcript(260))
        entries = []
        for _ in range(2):                                         # two private whole parses: neither writer takes a key lock
            priv = []
            tree = em.parse_session(self.leaf, rompuuid=SID, candidate_files=[self.leaf], now=NOW, private_entry_out=priv)
            entries.append((tree, priv[0]))
        names, a_staged, b_done, out = {}, threading.Event(), threading.Event(), {}
        real_replace = os.replace

        def replace(src, dst, *a, **k):
            who = threading.current_thread().name
            if who.startswith("writer-"):
                names.setdefault(who, []).append(os.path.basename(str(src)))
                if who == "writer-a" and str(dst).endswith(".meta"):
                    a_staged.set()                                 # A's sidecar staged under its temp name: B writes the leaf now
                    b_done.wait(60)
            return real_replace(src, dst, *a, **k)

        def write(tag, tree, entry):
            out[tag] = em.asm_checkpoint_write(self.leaf, SID, False, tree=tree, from_entry=entry, who="rebuild")
        with mock.patch.object(os, "replace", replace):
            a = threading.Thread(target=write, args=("a",) + entries[0], name="writer-a", daemon=True)
            a.start()
            try:
                self.assertTrue(a_staged.wait(60))
                b = threading.Thread(target=write, args=("b",) + entries[1], name="writer-b", daemon=True)
                b.start(); b.join(60)
            finally:
                b_done.set()
                a.join(60)
        self.assertEqual(out, {"a": True, "b": True}, "both writes landed: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertTrue(any(".meta." in n for n in names["writer-a"]) and any(".meta." in n for n in names["writer-b"]))
        self.assertEqual(set(names["writer-a"]) & set(names["writer-b"]), set(), names)
        self.assertEqual(list(self.doc_file().parent.glob("*.tmp")), [], "no temp file left behind")
        self.assertTrue(em._TMP_PID_RE.search(names["writer-a"][-1]), "in the shape the boot sweep reads back")


class ARebuildThatRaises(Harness):
    """The review of PR #367: a rebuild that raised was memoized at once, so an idle session was not tried again until it
    changed or the kernel restarted. Now a raise is tried once more at the next cycle's end, as a decline of the moment is,
    and a second raise is final for the leaf's stat and said loudly, with its traceback."""

    def test_a_raise_inside_the_parse_is_tried_once_more_and_a_second_is_said_loudly(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()
        real = em._asm_whole_entry

        def boom(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                raise RuntimeError("synthetic parse fault")
            return real(*a, **k)
        err = io.StringIO()
        with mock.patch.object(em, "_asm_whole_entry", boom), contextlib.redirect_stderr(err):
            self.writer_cycle(); _join_rebuilds()
            rb = em.asm_checkpoint_stats()["rebuild"]
            self.assertEqual((rb["claimed"], rb["retry"], rb["failed"], rb["pending"]), (1, 1, 0, 1), rb)
            self.assertIn("raised (RuntimeError('synthetic parse fault')); it is tried once more", err.getvalue())
            self.writer_cycle(NOW + 601); _join_rebuilds()         # the retry, at the next cycle's end
            self.writer_cycle(NOW + 602); _join_rebuilds()         # and nothing after it
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["retry"], rb["failed"], rb["pending"]), (2, 1, 1, 0), rb)
        out = err.getvalue()
        self.assertEqual(out.count("FAILED TWICE"), 1, out)
        self.assertIn("Traceback", out.split("FAILED TWICE", 1)[1], "the second raise's traceback")
        self.assertFalse(em.asm_rebuild_note(self.leaf, SID, "noDocument"), "final at the leaf's stat")

    def test_a_raise_then_a_success_writes_the_document(self):
        self.write(transcript(260))
        self.rows = [{"sid": SID, "path": self.leaf}]
        self.boot_parse()
        self.evict_read()
        real, calls = em._asm_whole_entry, []

        def once(*a, **k):
            if threading.current_thread().name == "asm-rebuild":
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("synthetic one-off fault")
            return real(*a, **k)
        with mock.patch.object(em, "_asm_whole_entry", once), contextlib.redirect_stderr(io.StringIO()):
            self.writer_cycle(); _join_rebuilds()
            self.writer_cycle(NOW + 601); _join_rebuilds()
        rb = em.asm_checkpoint_stats()["rebuild"]
        self.assertEqual((rb["claimed"], rb["retry"], rb["written"], rb["failed"]), (2, 1, 1, 0), rb)
        self.assertTrue(self.doc_file().exists())


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
