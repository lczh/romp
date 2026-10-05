#!/usr/bin/env python3
"""A restored assembly entry keeps its document current: once the tail past the document's cut reaches the churn bound's
share, the settle refreshes the document from a whole parse on a thread of its own, and the next boot restores a short tail.

Before this, a restored entry's cut moved only through a whole parse that replaced the entry, and the fold's gate held only
the atoms-only form and a re-seated entry to the share (a turns-section restore leaves `prefix` empty), so a session restored
at every boot kept the document it first got. Measured in place on the machine that holds it (2026-10-01): a 133 MB session
restored a 36 MB tail five days after its document was written, and the first chat build after the boot rendered all 545
turns of it. The refresh leaves the entry in hand alone (no key lock, nothing installed), so no reader of the leaf waits on
it. Synthetic records only: invented notes-api text, placeholder uuids."""
import gzip
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source   # above every state preamble (tests/test_state_isolation_order.py)

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_asm_checkpoint as T   # noqa: E402  the harness, the builders and the shared event-model module
import test_event_model_golden as G   # noqa: E402

em, NOW, SID = T.em, T.NOW, T.SID
WORDS = "the notes route pages by cursor and the tags route filters by owner before the search index is rebuilt".split()


def _turns(tag, n, t0, parent):
    """`n` settled turns with a few hundred bytes of invented text each, chained onto `parent`."""
    out = []
    for k in range(n):
        u, a = "u_%s_%d" % (tag, k), "a_%s_%d" % (tag, k)
        text = " ".join(WORDS[(k + j) % len(WORDS)] for j in range(60))
        out.append(G.uline(t0 + 20 * k, "%s step %d: %s" % (tag, k, text[:120]), u, parent))
        out.append(G.aline(t0 + 20 * k + 5, "%s step %d done: %s" % (tag, k, text), a, u, stop="end_turn"))
        parent = a
    return out


def _base():
    """Forty settled turns: the part the first document covers (its cut sits before the last settled turn, so the two turns
    past it are well under an eighth of the bytes before it)."""
    return _turns("base", 40, NOW - 7200, None)


def _more(recs, n=10):
    """`n` more settled turns after `recs`: the tail grows past an eighth of the pre-cut bytes."""
    return _turns("more", n, T.Harness.after(recs, 60), T._last_uuid(recs))


def _leaf_cut(path):
    doc = T._doc(path)
    return int(doc["files"][Path(path).stem]["cut"][0])


class RefreshOfARestoredEntry(T.Harness):
    def _restored_past_the_share(self):
        base = _base()
        path = self.write("refresh", base)
        self.parse(path)
        self.assertTrue(self.doc(path), "the first document is written from the whole parse")
        cut0 = _leaf_cut(path)
        recs = base + _more(base)
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in recs))
        self.fresh()                                      # a kernel restart: the next parse restores from the document
        modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["restore"])
        tail = os.path.getsize(path) - cut0
        self.assertGreaterEqual(tail * em._ASM_TAIL_SHARE, cut0, "the tail past the cut has reached the share (the premise)")
        return path, recs, cut0

    def _entry(self, path):
        with em._ASM_LOCK:
            return em._ASM_CACHE.get((os.path.realpath(path), SID, False))

    def test_the_writer_alone_never_moves_a_restored_entrys_cut(self):
        """The gap, pinned: the settle's writer skips a restored entry, so without the refresh the cut stays where the first
        document put it for as long as the session is restored at every boot."""
        path, _recs, cut0 = self._restored_past_the_share()
        reasons = []
        self.assertFalse(em.asm_checkpoint_write(path, SID, tree=self._trees.get(path), reason_out=reasons))
        self.assertEqual(reasons, ["restored"])
        self.assertEqual(_leaf_cut(path), cut0)

    def test_the_refresh_moves_the_cut_and_leaves_the_entry_in_hand_alone(self):
        path, recs, cut0 = self._restored_past_the_share()
        live = self._entry(path)
        self.assertTrue(live is not None and live.get("preTurns"), "a restored entry with a turns section")
        self.assertTrue(em.asm_refresh_claim(path, SID), "due: the tail reached the share")
        self.assertFalse(em.asm_refresh_claim(path, SID), "one refresh at a time")
        wrote = em.asm_refresh_document(path, SID, candidate_files=[path], states=self.states, postal_log=self.sent, now=NOW)
        self.assertTrue(wrote, "the refresh wrote a document: %s" % em.asm_checkpoint_stats()["skipped"])
        cut1 = _leaf_cut(path)
        self.assertGreater(cut1, cut0, "the cut moved up to the turn before the last settled one")
        self.assertIs(self._entry(path), live, "the entry in hand is the same object: nothing was installed over it")
        self.assertTrue(live.get("preTurns"), "and it is still the restored entry its readers fold")
        self.assertFalse(em.asm_refresh_claim(path, SID), "not due again until the leaf grows past the refresh's point by the share")
        old_tail = os.path.getsize(path) - cut0
        cold = self.cold(path)
        tree, modes, _n_lazy = self.restored(path)
        self.assertEqual(modes, ["restore"], "the next boot restores from the refreshed document")
        self.assertEqual(tree, cold, "and its tree, hydrated, equals the cold parse")
        self.assertLess(self.read_before, old_tail, "the next boot reads less than the tail the stale document left (the cost bound)")
        self.assertLessEqual(self.read_before, os.path.getsize(path) - cut1 + 4096,
                             "it reads the tail past the new cut and its guard bytes, nothing more")
        self.assertEqual(em.asm_checkpoint_stats()["refresh"]["written"], 1)

    def test_no_refresh_under_the_share(self):
        base = _base()
        path = self.write("under", base)
        self.parse(path)
        self.assertTrue(self.doc(path))
        more = _more(base, n=1)                           # one turn: well under an eighth of the pre-cut bytes
        Path(path).write_text("".join(json.dumps(r) + "\n" for r in base + more))
        self.fresh()
        self.parse(path)
        self.assertFalse(em.asm_refresh_claim(path, SID), "under the share the standing document stands")

    def test_a_whole_entry_is_never_claimed(self):
        """A whole entry writes at its own settle (asm_checkpoint_write): the refresh is for restored entries only."""
        base = _base()
        path = self.write("whole", base + _more(base))
        self.parse(path)
        self.assertFalse(em.asm_refresh_claim(path, SID))


class DeadWritersTempFiles(T.Harness):
    def test_the_boot_sweep_removes_a_dead_writers_temp_and_keeps_a_live_ones(self):
        """The refresh runs on a daemon thread, so an exit can land mid-write: its temp file (never renamed into place) is
        removed by the next boot's sweep, while a temp a live process is still writing stays."""
        import subprocess
        p = subprocess.Popen([sys.executable, "-c", "pass"]); p.wait()
        dead = p.pid                                       # a pid that has exited (reaped above)
        self.ck.mkdir(parents=True, exist_ok=True)
        names = {"dead_doc": "0123456789abcdef0123.asm.json.gz.%d.7f3a.tmp" % dead,
                 "dead_meta": "0123456789abcdef0123.asm.json.gz.meta.%d.tmp" % dead,
                 "dead_mark": "0123456789abcdef0123.asm.json.gz.meta.mark.%d.7f3a.tmp" % dead,
                 "mine": "0123456789abcdef0123.asm.json.gz.%d.7f3a.tmp" % os.getpid(),
                 "parent": "0123456789abcdef0123.asm.json.gz.meta.%d.tmp" % os.getppid()}
        for n in names.values():
            (self.ck / n).write_bytes(b"partial")
        em.checkpoint_sweep()
        left = {k for k, n in names.items() if (self.ck / n).exists()}
        self.assertEqual(left, {"mine", "parent"}, "only the dead writer's temps are removed")


# ── the settle write (the jobs pass's) starts the refresh on a thread of its own (the kernel side) ──
_STATE_ROOT = tempfile.mkdtemp(prefix="romp-asm-refresh-")
os.environ["XDG_STATE_HOME"] = _STATE_ROOT                # hermetic state BEFORE the load (the kernel resolves its root at import)
os.environ.pop("ROMP_STATE_DIR", None)
os.makedirs(os.path.join(_STATE_ROOT, "romp"), exist_ok=True)
Path(_STATE_ROOT, "romp", "session-hosts").write_text("off\n")   # this test's own root: no per-session host (CLAUDE.md, 2026-09-11)
km = load_source("romp_kernel_asm_refresh", os.path.join(os.path.dirname(HERE), "bin", "romp-kernel"))
SETTLE_SID = "a5a5a5a5-1111-4222-8333-444444444444"     # a private synthetic sid (no goal store is minted here)


class TheSettleStartsTheRefresh(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="romp-asm-refresh-k-")).resolve()
        (self.root / "romp").mkdir()
        (self.root / "romp" / "session-hosts").write_text("off\n")
        self._prior_state, self._prior_ckpt = km.jd.STATE, em._CKPT_DIR_FN
        km.jd._rebind_state(self.root / "romp")
        em.set_checkpoint_dir(lambda: km.jd.STATE / "checkpoints")   # the judges' wiring (a harness test before this one unset it)
        proj = self.root / "claude" / "projects" / "-w-notes-api"
        proj.mkdir(parents=True)
        self.leaf = str(proj / (SETTLE_SID + ".jsonl"))
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()

    def tearDown(self):
        km.jd._rebind_state(self._prior_state)
        em.set_checkpoint_dir(self._prior_ckpt)
        shutil.rmtree(self.root, ignore_errors=True)

    def _parse(self):
        return km.jd.parsed_session(SETTLE_SID, [self.leaf], NOW)

    def test_a_settle_over_a_restored_entry_past_the_share_refreshes_its_document(self):
        base = _base()
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in base))
        tree = self._parse()
        human = bool(km.jd._sdk_owned(SETTLE_SID))
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SETTLE_SID, human, tree=tree), "the first document")
        doc_path = em._asm_ckpt_file(self.leaf)
        cut0 = int(json.loads(gzip.decompress(doc_path.read_bytes()))["files"][SETTLE_SID]["cut"][0])
        recs = base + _more(base)
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in recs))
        with em._JSONL_CACHE_LOCK:                        # a kernel restart's in-memory side
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        modes = []
        tree = km.jd.parsed_session(SETTLE_SID, [self.leaf], NOW + 1, asm_mode_out=modes)
        self.assertEqual(modes, ["restore"], "the boot restores the stale document")
        keys = iter([1, 2])                               # the first sight records; the turn end after it is the settle
        km._CKPT_SETTLE_SEEN.clear(); km._CKPT_PERIODIC_SEEN.clear()
        with mock.patch.object(km, "_sessions", lambda now: [{"sid": SETTLE_SID, "path": self.leaf}]), \
             mock.patch.object(km, "_turn_end_key", lambda s, reg=None: next(keys)), \
             mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
             mock.patch.object(km, "_heal_cold_folds", lambda leaf: None), \
             mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
             mock.patch.object(km, "_display_sdk_human", lambda s: human), \
             mock.patch.object(km, "_stored_tree", lambda leaf, sid: tree):
            km._persist_checkpoints(NOW + 2)
            km._persist_checkpoints(NOW + 3)
            for th in [t for t in threading.enumerate() if t.name == "asm-refresh"]:
                th.join(timeout=60)
        cut1 = int(json.loads(gzip.decompress(doc_path.read_bytes()))["files"][SETTLE_SID]["cut"][0])
        self.assertGreater(cut1, cut0, "the settle refreshed the document: its cut moved past the stale one")
        with em._ASM_LOCK:
            entry = em._ASM_CACHE.get((os.path.realpath(self.leaf), SETTLE_SID, human))
        self.assertTrue(entry is not None and entry.get("preTurns"), "the restored entry the readers fold was left in place")

    def test_the_settle_returns_while_the_refresh_runs(self):
        """The jobs pass's settle write is not held by the refresh: the settle claims it and returns while the whole parse and the
        write are still running on their own thread (stood in for here by a refresh that waits to be released)."""
        base = _base()
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in base))
        tree = self._parse()
        human = bool(km.jd._sdk_owned(SETTLE_SID))
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SETTLE_SID, human, tree=tree))
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in base + _more(base)))
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        tree = km.jd.parsed_session(SETTLE_SID, [self.leaf], NOW + 1)
        started, release = threading.Event(), threading.Event()

        def held_refresh(fsid, leaf, now, sdk_human=None, states=None):
            started.set()
            release.wait(30)
            em.asm_refresh_release(leaf, fsid, bool(sdk_human))
            return False
        keys = iter([1, 2])
        km._CKPT_SETTLE_SEEN.clear(); km._CKPT_PERIODIC_SEEN.clear()
        try:
            with mock.patch.object(km, "_sessions", lambda now: [{"sid": SETTLE_SID, "path": self.leaf}]), \
                 mock.patch.object(km, "_turn_end_key", lambda s, reg=None: next(keys)), \
                 mock.patch.object(km, "_prime_leaf_folds", lambda leaf: True), \
                 mock.patch.object(km, "_heal_cold_folds", lambda leaf: None), \
                 mock.patch.object(km.em, "checkpoint_dirty", lambda: []), \
                 mock.patch.object(km, "_display_sdk_human", lambda s: human), \
                 mock.patch.object(km, "_stored_tree", lambda leaf, sid: tree), \
                 mock.patch.object(km.jd, "asm_refresh", held_refresh):
                km._persist_checkpoints(NOW + 2)
                km._persist_checkpoints(NOW + 3)            # the settle: it returns here, the refresh still held
                self.assertTrue(started.wait(10), "the settle started the refresh")
                running = [t for t in threading.enumerate() if t.name == "asm-refresh" and t.is_alive()]
                self.assertTrue(running, "and returned while the refresh was still running on its own thread")
                self.assertFalse(em.asm_refresh_claim(self.leaf, SETTLE_SID, human), "one refresh at a time")
        finally:
            release.set()
            for th in [t for t in threading.enumerate() if t.name == "asm-refresh"]:
                th.join(timeout=30)


if __name__ == "__main__":
    unittest.main()
