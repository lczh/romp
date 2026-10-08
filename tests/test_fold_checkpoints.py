#!/usr/bin/env python3
"""T323 stage 3 (2026-09-11): the folds' checkpoints. One small JSON file per folded JSONL file records the reader's
prefix witness (offset past the last complete line, the guard bytes before it, the record count) and the state of every
fold_records fold whose cursor stood at that count; a fresh process verifies the guard on disk, reads only the tail
past the offset and resumes each fold from its recorded state. Pinned here: the codec round-trips every fold state
shape; fold-equals-full holds from the third start state (restore, then fold the tail) for the generic fold and for
every kernel fold that carries a checkpoint name; a whole reader touching the file first still lets the folds resume;
every fallback (rewrite under the guard, shrink, version, path, corrupt) reads whole, is counted per reason and equals
a cold fold; the bytes the reader pulls after a restore are the tail plus the guard; a fold behind the witness is left
out and cold-folds; the boot sweep removes checkpoints of vanished files; the kernel writes at a session's settle or
states-log move and never otherwise; /perf carries the counters. Hermetic: synthetic records under a temp root."""
import contextlib
import errno
import io
import json
import os
import tempfile
import threading
import time
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_t323s3", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-000000000301"
TS0 = 1_800_000_000


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _write(path, recs):
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")


def _append(path, *recs, torn=None):
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
        if torn is not None:
            f.write(json.dumps(torn))                     # a final record without its newline
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 2))        # a new mtime even on a coarse-clock filesystem


def _user(text, uuid, parent, t, **kw):
    r = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "promptSource": "typed",
         "message": {"role": "user", "content": text}, "cwd": "/w/notes-api", "version": "2.1.0", "gitBranch": "web"}
    r.update(kw)
    return r


def _assistant(blocks, uuid, parent, t):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "cwd": "/w/notes-api",
            "version": "2.1.0", "gitBranch": "web", "message": {"role": "assistant", "content": blocks, "stop_reason": "end_turn"}}


def _tool_result(tid, uuid, parent, t, text="ok"):
    return {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t),
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def _bg_launch(tid, desc, uuid, parent, t):
    return _assistant([{"type": "tool_use", "id": tid, "name": "Bash",
                        "input": {"command": "sleep 1", "run_in_background": True, "description": desc}}], uuid, parent, t)


def _bg_note(tid, status, uuid, parent, t):
    note = ("<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>%s</tool-use-id>\n<status>%s</status>\n"
            "<summary>%s</summary>\n</task-notification>" % (tid, status, status))
    return _user(note, uuid, parent, t)


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_state = jd.STATE
        self.td = Path(tempfile.mkdtemp())
        jd._rebind_state(self.td / "state")
        (jd.STATE / "states").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "timeline").mkdir(parents=True, exist_ok=True)
        self.fresh_process()
        em._CKPT_STATS["refolds"].clear()                              # reset, never injected: the /perf key pin must see the real counter
        em._CKPT_STATS.update(restored=0, writes=0, swept=0, skippedFolds=0, fallbacks={}, restoredFolds={}, droppedRestores=0, oversizeFolds={},
                              coldFolds={}, coldWrites={})
        if "converge" in em._CKPT_STATS:                          # reset, never injected: the /perf key pin tests the production default
            em._CKPT_STATS["converge"] = {k: 0 for k in em._CKPT_STATS["converge"]}

    def tearDown(self):
        jd._rebind_state(self.saved_state)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

    def fresh_process(self):
        """What a kernel restart does to the in-memory side: every reader entry, fold cursor, pending restore and
        byte counter gone; the checkpoint files on disk stay."""
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")   # the setter clears the pending restores and the seqs
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for c in list(em._FOLD_REG.values()):
            c.clear()
        with em._CKPT_LOCK:
            em._COLD_FOLDS.clear()                                # a process-wide set: a new process starts with none
            em._COLD_REASONS.clear(); em._COLD_OVER_KB.clear()   # ...and no reason or KB inherited from the previous boot (T359)
        km._CKPT_SETTLE_SEEN.clear()

    def ckpt_files(self):
        d = jd.STATE / "checkpoints"
        return sorted(d.glob("*.json")) if d.is_dir() else []

    def doc(self, path):
        return json.loads(em._ckpt_file(path).read_text())


class Codec(Base):
    def test_every_fold_state_shape_round_trips(self):
        shapes = [
            {"tasks": {"toolu_1": {"status": "running", "desc": "x"}}, "order": ["toolu_1"], "dispatch": {}, "all": False, "done": {"toolu_0", "toolu_9"}},
            {"steps": [{"tool": "Bash", "desc": "ls", "ts": "2026-09-10T00:00:00.000Z"}], "calls": 1, "since": None, "last": "t"},
            {"launched": {"toolu_a": 1.5}, "settled": {"toolu_a"}},
            {"sent": {"m1": {"ev": "sent", "id": "m1"}}, "execd": {"m1": (1.0, None)}, "ended": {"m2"}},
            (12.5, "restart"), (None, False), ({"awaiting": True, "t": 3}, True),
            {"recoveries": [{"t": 1, "retries": 2}], "gaveups": [], "orphans": [], "efforts": [], "gestures": []},
            ([("hello", "2026-09-10T00:00:00Z"), ("", None)], 7),
            [(1, "working"), (2, "idle")],
            {1: "int keyed", (2, 3): "tuple keyed"},
            {"~set": "a plain key that looks like a tag"},
            set(), (), [], {}, None, True, 0, 1.25, "s",
        ]
        for st in shapes:
            enc = em._ckpt_encode(st)
            json.dumps(enc)                                       # JSON-able
            self.assertEqual(em._ckpt_decode(json.loads(json.dumps(enc))), st, repr(st))
            self.assertIs(type(em._ckpt_decode(enc)), type(st), repr(st))

    def test_an_unencodable_state_raises_type_error(self):
        with self.assertRaises(TypeError):
            em._ckpt_encode({"x": object()})


class GenericFold(Base):
    """em.fold_records with a checkpoint name over a plain JSONL file of {"n": i} records."""

    def setUp(self):
        super().setUp()
        self.p = str(self.td / "t.jsonl")
        self.cache = {}
        self.kinds = []

    @staticmethod
    def _step(st, o):
        return st + [o["n"]]

    def fold(self, name="t", cache=None):
        return em.fold_records(cache if cache is not None else self.cache, self.p, list, self._step, on=self.kinds.append, ckpt=name)

    def cold(self):
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None                                 # a plain whole fold, no checkpoint in play
        try:
            return em.fold_records({}, self.p, list, self._step)
        finally:
            em._CKPT_DIR_FN = saved

    def test_write_restore_fold_the_tail_equals_a_cold_fold_and_reads_only_the_tail(self):
        _write(self.p, [{"n": i} for i in range(3)])
        self.assertEqual(self.fold(), [0, 1, 2])
        self.assertEqual(self.kinds, ["refold"])
        self.assertIn(self.p, em.checkpoint_dirty())
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertNotIn(self.p, em.checkpoint_dirty(), "a write clears the dirty mark")
        d = self.doc(self.p)
        size0 = os.stat(self.p).st_size
        self.assertEqual((d["v"], d["count"], d["offset"], d["size"], d["seq"]), (1, 3, size0, size0, 1))
        self.assertEqual(d["folds"]["t"]["count"], 3)
        self.assertEqual(bytes.fromhex(d["guard"]), open(self.p, "rb").read()[-64:])
        self.fresh_process()
        _append(self.p, {"n": 3}, {"n": 4})
        self.assertEqual(self.fold(), [0, 1, 2, 3, 4], "restored state plus the tail equals a cold fold of the file")
        self.assertEqual(self.kinds[-1], "restore")
        with em._JSONL_CACHE_LOCK:
            ent = em._JSONL_CACHE[self.p]
        self.assertEqual((ent[5], len(ent[4])), (3, 2), "a TAIL entry: base 3, two records held")
        read = em.read_bytes_report()[self.p]
        size1 = os.stat(self.p).st_size
        self.assertEqual(read, (size1 - size0) + min(64, size0) + min(64, size1),
                         "the tail, the guard check before it and the guard captured after it: nothing of the prefix")
        self.assertEqual(em.checkpoint_stats()["restored"], 1)
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        cp = em._ckpt_file(self.p)
        self.assertEqual(em.read_bytes_report()[str(cp)], cp.stat().st_size, "the checkpoint document's own read is counted")
        self.assertEqual(em.checkpoint_stats()["documentBytes"], cp.stat().st_size)
        self.assertEqual(self.fold(), [0, 1, 2, 3, 4]); self.assertEqual(self.kinds[-1], "hit")
        self.assertEqual(self.cache[self.p][0], 5, "the cursor's count is first, as every reader of it knew")
        self.assertEqual(self.cold(), [0, 1, 2, 3, 4])

    def test_an_unchanged_file_restores_with_no_tail_and_a_torn_tail_folds_provisionally(self):
        _write(self.p, [{"n": 0}, {"n": 1}])
        self.fold(); em.checkpoint_write(self.p)
        self.fresh_process()
        self.assertEqual(self.fold(), [0, 1]); self.assertEqual(self.kinds[-1], "restore")
        self.assertEqual(em.read_bytes_report()[self.p], 2 * min(64, os.stat(self.p).st_size), "the guard checked, then captured again: no content")
        _append(self.p, torn={"n": 2})
        self.assertEqual(self.fold(), [0, 1, 2], "the newline-less record is folded provisionally")
        self.assertEqual(self.cache[self.p][0], 2, "and not into the cursor")

    def test_a_whole_reader_first_then_the_fold_still_restores(self):
        _write(self.p, [{"n": 0}, {"n": 1}, {"n": 2}])
        self.fold(); em.checkpoint_write(self.p)
        size0 = os.stat(self.p).st_size
        self.fresh_process()
        _append(self.p, {"n": 3})
        recs = em._read_jsonl_incremental(self.p)                 # a whole reader (the parse) touches the file first
        self.assertEqual(len(recs), 4)
        self.assertEqual(self.fold(), [0, 1, 2, 3])
        self.assertEqual(self.kinds[-1], "restore", "the fold state came from the checkpoint after a guard check on disk")
        self.assertEqual(em.checkpoint_stats()["restored"], 1)
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        self.assertEqual(em.read_bytes_report()[self.p], os.stat(self.p).st_size + min(64, os.stat(self.p).st_size) + min(64, size0),
                         "the whole file and its guard capture (the whole reader's) plus the restore's guard check")

    def test_a_rewrite_under_the_guard_falls_back_loudly_and_equals_cold(self):
        _write(self.p, [{"n": i} for i in range(4)])
        self.fold(); em.checkpoint_write(self.p)
        self.fresh_process()
        _write(self.p, [{"n": 100 + i} for i in range(4)] + [{"n": 7}])   # same shape, other content, longer
        self.assertEqual(self.fold(), [100, 101, 102, 103, 7])
        self.assertEqual(self.kinds[-1], "refold")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {"guard": 1})
        self.assertFalse(em._ckpt_file(self.p).exists(), "the bad checkpoint is removed")
        self.assertEqual(self.fold(), self.cold())

    def test_a_shrunk_file_a_wrong_version_a_wrong_path_and_a_corrupt_document_each_fall_back(self):
        for reason, spoil in (
            ("shrunk", lambda: _write(self.p, [{"n": 0}])),
            ("version", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), v=99)))),
            ("path", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), path="/elsewhere/t.jsonl")))),
            ("corrupt", lambda: em._ckpt_file(self.p).write_text("{not json")),
            ("corrupt", lambda: em._ckpt_file(self.p).write_text(json.dumps(dict(self.doc(self.p), offset="x")))),
            ("rewrite", lambda: (_write(self.p, [{"n": 5}, {"n": 6}, {"n": 7}]), os.utime(self.p, (TS0, TS0)))),
        ):
            with self.subTest(reason=reason):
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process()
                em._CKPT_STATS["fallbacks"] = {}
                self.fold(); em.checkpoint_write(self.p)
                self.fresh_process()
                spoil()
                got = self.fold()
                self.assertEqual(em.checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, self.cold(), reason)
                self.assertEqual(self.kinds[-1], "refold")

    def test_a_fold_behind_the_witness_is_written_with_its_own_count_and_restores_warm(self):
        """T359 (romp_perf's boot finding, 2026-09-12): a fold whose cursor lagged the entry's record count (the kernel's
        background-task view is stepped by builds, so at a settle or exit write it stands behind the leaf) was left out of
        the document silently, and the next boot read the leaf whole for it, every boot. The writer records such a fold at
        ITS count with its state; the restore takes a count inside the entry's held records and steps the tail from there
        (the append path every live fold takes), so the fold is warm and the read is the tail's."""
        _write(self.p, [{"n": 0}, {"n": 1}])
        other = {}
        self.fold(); self.fold("u", other)
        _append(self.p, {"n": 2})
        self.fold()                                               # "t" stands at 3, "u" still at 2
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual(sorted(d["folds"]), ["t", "u"], "the lagging fold is recorded beside the one at the witness")
        self.assertEqual((d["folds"]["t"]["count"], d["folds"]["u"]["count"], d["count"]), (3, 2, 2),
                         "each at its own count; the document's cut is the lowest, so the next tail read holds what the lagging fold needs")
        self.assertIn("state", d["folds"]["u"])
        self.assertEqual(d["lastUuid"], None); self.assertEqual(bytes.fromhex(d["guard"]), open(self.p, "rb").read()[max(0, d["offset"] - 64):d["offset"]])
        self.fresh_process()
        _append(self.p, {"n": 3})
        self.assertEqual(self.fold(), [0, 1, 2, 3]); self.assertEqual(self.kinds[-1], "restore")
        size = os.path.getsize(self.p)
        self.assertEqual(em.fold_records({}, self.p, list, self._step, on=self.kinds.append, ckpt="u"), [0, 1, 2, 3])
        self.assertEqual(self.kinds[-1], "restore", "the lagging fold restores from its own count and steps the two records since")
        with em._JSONL_CACHE_LOCK:
            self.assertEqual(em._JSONL_CACHE[self.p][5], 2, "the entry is a tail from the cut: nothing was read whole")
        self.assertEqual(em.read_bytes_report().get(self.p, 0), (size - d["offset"]) + min(64, d["offset"]) + min(64, size),
                         "the tail from the cut, the guard check before it and the guard captured after it: nothing of the prefix")
        self.assertEqual(em.checkpoint_stats()["restoredFolds"], {"t": 1, "u": 1})
        self.assertEqual(self.fold(), [0, 1, 2, 3]); self.assertEqual(self.kinds[-1], "hit", "the restored fold still stands")

    def test_a_lagging_cursor_outside_the_entrys_records_is_not_written_and_a_document_count_ahead_of_the_entry_is_refused(self):
        """The bound on the incremental tail: a lagging fold is written only when its count sits inside the entry's held
        records (the tail the reader holds), and a restore takes a fold's count only up to the document's own; anything
        else is the whole refold it always was."""
        _write(self.p, [{"n": i} for i in range(5)])
        self.fold()                                               # "t" at 5 over a whole entry
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p); d["folds"]["t"]["count"] = 99      # a document claiming a count past its own and the entry's
        em._ckpt_file(self.p).write_text(json.dumps(d))
        self.fresh_process()
        _append(self.p, {"n": 5})
        self.assertEqual(self.fold(), list(range(6)))
        self.assertEqual(self.kinds[-1], "refold", "a count the entry cannot hold is refused: the whole file, as before")
        d = self.doc(self.p); d["folds"]["t"]["count"] = 2; d["count"] = 6   # a lagging count BELOW the tail entry's base
        em._ckpt_file(self.p).write_text(json.dumps(d))
        self.fresh_process(); self.cache.clear()
        _append(self.p, {"n": 6})
        self.assertEqual(self.fold(), list(range(7)))
        self.assertEqual(self.kinds[-1], "refold", "a count below the records the entry holds is refused too")

    def test_an_evicted_reader_entry_never_lets_a_rewritten_file_pass_as_an_append(self):
        """Review find (2026-09-11): a from-zero read after an eviction (the reader cache runs at its cap on a busy box) or
        a failure pop restarted the generation at 0 while the fold's cursor still held (count, 0, state): a file rewritten
        in place with as many records then read as a hit, one with more as an append of the NEW file's tail onto the OLD
        state, and the checkpoint written afterwards carried the wrong state under a clean witness. Every from-zero read
        takes a fresh process-wide generation now."""
        _write(self.p, [{"n": i} for i in range(3)])
        self.assertEqual(self.fold(), [0, 1, 2])
        for rewrite in ([{"n": 7}, {"n": 8}, {"n": 9}], [{"n": 4}, {"n": 5}, {"n": 6}, {"n": 3}]):   # same size, then longer
            with self.subTest(records=len(rewrite)):
                with em._JSONL_CACHE_LOCK:
                    em._JSONL_CACHE.pop(self.p, None)                 # evicted (or popped by a stat failure) under the fold
                _write(self.p, rewrite)
                self.assertEqual(self.fold(), [r["n"] for r in rewrite], "the rewritten file is folded from zero, never onto the old state")
                self.assertEqual(self.kinds[-1], "refold")
                self.assertTrue(em.checkpoint_write(self.p))
                self.assertEqual(self.fold(), self.cold())
                self.fresh_process()
                self.assertEqual(self.fold(), [r["n"] for r in rewrite], "and the checkpoint written after it is right")
                self.assertEqual(self.kinds[-1], "restore")

    def test_a_whole_reader_first_then_a_rewritten_file_falls_back_like_the_tail_path(self):
        """Review find (2026-09-11): the whole-reader-first restore checked the guard alone; the tail path also compared
        the document's size and mtime. Both verdicts come from one helper now: the same rewrite gets the same reason
        whichever reader came first, so /perf's fallback count is a witness."""
        for reason, rewrite in (("guard", [{"n": 100 + i} for i in range(4)] + [{"n": 7}]),          # longer, other prefix
                                ("rewrite", ([{"n": 5}, {"n": 6}, {"n": 7}], (TS0, TS0)))):         # same size, other mtime
            with self.subTest(reason=reason):
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process(); em._CKPT_STATS["fallbacks"] = {}
                self.fold(); em.checkpoint_write(self.p)
                self.fresh_process()
                if isinstance(rewrite, tuple):
                    _write(self.p, rewrite[0]); os.utime(self.p, rewrite[1])
                else:
                    _write(self.p, rewrite)
                recs = em._read_jsonl_incremental(self.p)          # the whole reader comes first
                self.assertEqual(len(recs), len(rewrite[0] if isinstance(rewrite, tuple) else rewrite))
                got = self.fold()
                self.assertEqual(em.checkpoint_stats()["fallbacks"], {reason: 1}, reason)
                self.assertEqual(got, self.cold()); self.assertEqual(self.kinds[-1], "refold")
                self.assertFalse(em._ckpt_file(self.p).exists(), "the checkpoint that did not verify is gone")

    def test_two_threads_meeting_a_checkpointed_files_first_read_cost_one_read_and_lose_no_restore(self):
        """Review find (2026-09-11): the reader held its lock for the lookup and the store only, so a fold's tail restore
        (generation N, pending fold states under N) and a concurrent whole read (generation N+1, stored last) left the
        pending states orphaned: every other fold of the file refolded over a whole read, silently. Reads that pull bytes
        are serialized per path now and re-check the cache under the lock: the second thread waits and hits."""
        _write(self.p, [{"n": i} for i in range(200)])
        other = {}
        self.fold(); self.fold("u", other); em.checkpoint_write(self.p)
        size = os.stat(self.p).st_size
        for trial in range(8):
            with self.subTest(trial=trial):
                self.fresh_process(); other.clear()
                em._CKPT_STATS.update(restored=0, restoredFolds={}, droppedRestores=0, fallbacks={})
                gate = threading.Barrier(2)
                results, errors = {}, []

                def fold_first():
                    try:
                        gate.wait(5); results["fold"] = self.fold()
                    except Exception as e:                                # noqa: BLE001
                        errors.append(e)

                def whole_first():
                    try:
                        gate.wait(5); results["whole"] = len(em._read_jsonl_incremental(self.p))
                    except Exception as e:                                # noqa: BLE001
                        errors.append(e)
                ts = [threading.Thread(target=fold_first), threading.Thread(target=whole_first)]
                for th in ts:
                    th.start()
                for th in ts:
                    th.join(10)
                self.assertEqual(errors, [])
                self.assertEqual((results["fold"], results["whole"]), (list(range(200)), 200))
                self.assertEqual(em.fold_records(other, self.p, list, self._step, on=self.kinds.append, ckpt="u"), list(range(200)))
                self.assertEqual(self.kinds[-1], "restore", "the second fold of the file restores too: no orphaned pending states")
                st = em.checkpoint_stats()
                self.assertEqual((st["droppedRestores"], st["fallbacks"]), (0, {}))
                self.assertEqual(sorted(st["restoredFolds"]), ["t", "u"])
                read = em.read_bytes_report()[self.p]
                self.assertLessEqual(read, size + 4 * 64, "the file's content was read once, whichever thread went first (%d bytes for a %d-byte file)" % (read, size))

    def test_a_fold_whose_state_is_the_size_of_its_file_keeps_its_cursor_and_restarts_cold_over_the_tail(self):
        """Review finds (2026-09-11): a checkpoint carried every fold's state, and a state that grows with its file (the postal
        fold's map of every sent row) made the document a second copy of the file, read at every boot. A fold's encoded
        state past the cap is left out, counted per name; its CURSOR stays, so the next process starts that fold cold at the
        cut and steps the tail only (counted under coldFolds, said once) instead of reading the file whole at every restart;
        the bounded folds beside it restore whole."""
        saved_cap = em._CKPT_FOLD_CAP                                  # the cap is sized to the machine now (8 MiB); this test's
        em._CKPT_FOLD_CAP = 64 * 1024                                  # oversize state is 170 KB, so pin the cap it was written for
        self.addCleanup(setattr, em, "_CKPT_FOLD_CAP", saved_cap)
        _write(self.p, [{"n": i, "pad": "x" * 400} for i in range(400)])           # ~170 KB of records
        big = {}
        self.fold()                                                               # "t": a list of 400 ints, small
        em.fold_records(big, self.p, list, lambda st, o: st + [o], ckpt="big")    # "big": every record whole, over the cap
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual(sorted(d["folds"]), ["big", "t"], "the oversize fold's cursor is in the document")
        self.assertEqual((d["folds"]["big"]["count"], "state" in d["folds"]["big"]), (400, False), "without its state")
        self.assertGreater(d["folds"]["big"].get("over", 0), 64, "and with the reason: its encoded size in KB, over the cap")
        self.assertLess(em._ckpt_file(self.p).stat().st_size, em._CKPT_FOLD_CAP, "and the document stays small")
        self.assertEqual(em.checkpoint_stats()["oversizeFolds"], {"big": 1})
        self.fresh_process(); big.clear()
        self.assertEqual(self.fold(), list(range(400))); self.assertEqual(self.kinds[-1], "restore")
        size = os.path.getsize(self.p)
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual(got, []); self.assertEqual(self.kinds[-1], "cold", "the oversize fold starts cold at the cut: an empty tail")
        self.assertEqual(em.checkpoint_stats()["coldFolds"], {"big": 1})
        self.assertLess(em.read_bytes_report().get(self.p, 0), size / 2, "the file was not read whole")
        _append(self.p, {"n": 400})
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual(got, [{"n": 400}], "and steps the records appended since"); self.assertEqual(self.kinds[-1], "append")
        # review find (2026-09-11, third round): the tail-only state is small now; a write must not record it as complete
        self.assertEqual(self.fold(), list(range(401)))               # the bounded fold steps to the witness too
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual(self.doc(self.p)["folds"]["big"], {"count": 401, "over": d["folds"]["big"]["over"]},
                         "a fold that began cold stays a cursor without state, its reason (over the cap, the KB) carried (T359)")
        self.assertEqual(em.checkpoint_stats()["coldWrites"], {"big": 1})
        self.assertIn("state", self.doc(self.p)["folds"]["t"], "the bounded fold beside it is written whole")
        self.fresh_process(); big.clear()
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual((got, self.kinds[-1]), ([], "cold"), "the third process restores it cold again, not as a complete state")
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("big", 0), 0)
        self.assertEqual(em.cold_fold_reasons(self.p), {"big": "over"}, "...and knows why: over the cap, which no settle heals")
        big.clear(); em._TRAILING_CACHE.clear()
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        em._ckpt_file(self.p).unlink()                                # no document to restore from: the fold reads the file whole
        got = em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual((len(got), self.kinds[-1]), (401, "refold"), "a whole refold completes the state")
        with em._CKPT_LOCK:
            self.assertNotIn((self.p, "big"), em._COLD_FOLDS, "and the fold is no longer cold")
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        self.assertEqual(sorted(self.doc(self.p)["folds"]), ["big"], "the state is oversize again: written as a cursor for that reason")
        self.assertEqual((self.doc(self.p)["folds"]["big"]["count"], "over" in self.doc(self.p)["folds"]["big"]), (401, True))
        self.assertEqual(em.checkpoint_stats()["coldWrites"], {"big": 1}, "not as a cold write")
        self.fresh_process(); big.clear()
        em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
        self.assertEqual(em.cold_fold_reasons(self.p), {"big": "over"}, "an over-the-cap cursor restores cold for that reason, and no settle heals it")

    def test_the_boot_line_names_the_real_reason_a_fold_restarts_cold(self):
        """The line blamed the size cap whatever the reason (romp_perf, 2026-09-12: 68 lines about an 8 MB cap at a boot whose
        oversizeFolds was empty). Over the cap: the state's KB against the cap. A cursor without a state and without a reason
        (a tail-only state, or an older kernel's cursor-only entry): says so, and that a settle heals it."""
        import io
        _write(self.p, [{"n": i, "pad": "x" * 400} for i in range(400)])
        self.fold()
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        d["folds"]["t"] = {"count": 400, "over": 170}                 # an oversize cursor, as the writer marks it
        em._ckpt_file(self.p).write_text(json.dumps(d))
        self.fresh_process(); em._SAID.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.fold()
        self.assertEqual(self.kinds[-1], "cold")
        self.assertIn("its state was 170 KB, over the %d KB cap" % (em._CKPT_FOLD_CAP // 1024), err.getvalue())
        d["folds"]["t"] = {"count": 400}                               # an older kernel's cursor-only entry: no reason recorded
        em._ckpt_file(self.p).write_text(json.dumps(d))
        self.fresh_process(); em._SAID.clear(); self.cache.clear()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.fold()
        self.assertEqual(self.kinds[-1], "cold")
        self.assertIn("carries its cursor without a state", err.getvalue())
        self.assertIn("one whole refold heals it", err.getvalue())
        self.assertNotIn("KB cap", err.getvalue(), "the cap is not blamed for a state that never met it")

    @staticmethod
    def _noop_init():
        return []

    def test_nothing_is_written_when_no_fold_stands_at_the_witness_unless_forced(self):
        _write(self.p, [{"n": 0}])
        em._read_jsonl_incremental(self.p)                        # an entry with no fold on it
        self.assertFalse(em.checkpoint_write(self.p))
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        self.assertEqual(self.doc(self.p)["folds"], {})

    def test_checkpoints_off_fold_as_before_and_write_nothing(self):
        em.set_checkpoint_dir(None)
        _write(self.p, [{"n": 0}])
        self.assertEqual(self.fold(), [0])
        self.assertFalse(em.checkpoint_write(self.p))
        self.assertEqual(self.ckpt_files(), [])

    def test_the_boot_sweep_removes_checkpoints_of_files_that_are_gone(self):
        q = str(self.td / "q.jsonl")
        _write(self.p, [{"n": 0}]); _write(q, [{"n": 1}])
        self.fold(); em.fold_records({}, q, list, self._step, ckpt="q")
        em.checkpoint_write_dirty()
        self.assertEqual(len(self.ckpt_files()), 2)
        os.unlink(q)
        (jd.STATE / "checkpoints" / "junk.json").write_text("{")
        self.assertEqual(em.checkpoint_sweep(), 2)
        self.assertEqual([f.name for f in self.ckpt_files()], [em._ckpt_file(self.p).name])
        self.assertEqual(em.checkpoint_stats()["swept"], 2)

    def test_the_seq_counts_writes_per_file_and_survives_a_restore(self):
        _write(self.p, [{"n": 0}])
        self.fold(); em.checkpoint_write(self.p)
        _append(self.p, {"n": 1}); self.fold(); em.checkpoint_write(self.p)
        self.assertEqual(self.doc(self.p)["seq"], 2)
        self.fresh_process()
        self.fold(); _append(self.p, {"n": 2}); self.fold(); em.checkpoint_write(self.p)
        self.assertEqual(self.doc(self.p)["seq"], 3)


class KernelFolds(Base):
    """Every kernel fold that carries a checkpoint name: the answer after (checkpoint at a prefix, a fresh process,
    the tail appended, the fold) equals the answer of a cold fold over the whole file."""

    def setUp(self):
        super().setUp()
        self.proj = self.td / "proj"; self.proj.mkdir()
        self.leaf = str(self.proj / (SID + ".jsonl"))
        sub = self.proj / SID / "subagents"; sub.mkdir(parents=True)
        self.agent = str(sub / "agent-aaaa.jsonl")
        self.states = str(jd.STATE / "states" / (SID + ".jsonl"))
        self.postal = str(jd.STATE / "timeline" / "messages.jsonl")
        self.queue_leaf = str(self.proj / "queue.jsonl")
        self.nudge = str(jd.STATE / "nudge-events.jsonl")
        self.files = (self.leaf, self.agent, self.states, self.postal, self.queue_leaf, self.nudge)

    def leaf_recs(self, tail):
        head = [_user("wire the fixtures", "u1", None, TS0, permissionMode="acceptEdits"),
                _bg_launch("toolu_bg1", "run the suite", "a1", "u1", TS0 + 10),
                _assistant([{"type": "tool_use", "id": "toolu_ag1", "name": "Agent", "input": {"prompt": "check the width"}}], "a2", "a1", TS0 + 20),
                _assistant([{"type": "tool_use", "id": "toolu_ed1", "name": "Edit", "input": {"file_path": "/w/notes-api/web/app.ts"}}], "a3", "a2", TS0 + 30)]
        rest = [_bg_note("toolu_bg1", "completed", "u2", "a3", TS0 + 40),
                _tool_result("toolu_ag1", "u3", "u2", TS0 + 50, text="done"),
                _bg_launch("toolu_bg2", "lint", "a4", "u3", TS0 + 60),
                dict(_user("and the cap?", "u4", "a4", TS0 + 70), gitBranch="api"),
                dict(_assistant([{"type": "tool_use", "id": "toolu_ed2", "name": "Write", "input": {"file_path": "/w/notes-api/api/cap.py"}}], "a5", "u4", TS0 + 80), gitBranch="api")]
        return head + rest if tail else head

    def agent_recs(self, tail):
        head = [_user("check the width", "s1", None, TS0 + 21),
                _assistant([{"type": "tool_use", "id": "toolu_s1", "name": "Bash", "input": {"command": "ls web", "description": "list"}}], "s2", "s1", TS0 + 22)]
        rest = [_assistant([{"type": "tool_use", "id": "toolu_s2", "name": "Read", "input": {"file_path": "/w/notes-api/web/app.ts"}}], "s3", "s2", TS0 + 23),
                _assistant([{"type": "tool_use", "id": "toolu_s3", "name": "Agent", "input": {"prompt": "deeper"}}], "s4", "s3", TS0 + 24)]
        return head + rest if tail else head

    def states_recs(self, tail):
        head = [{"t": TS0, "state": "working"}, {"t": TS0 + 5, "awaiting": True, "state": "waiting"},
                {"t": TS0 + 6, "machineCut": "restart", "state": "idle"},
                {"t": TS0 + 7, "retriesRecovered": True, "retries": 2},
                {"t": TS0 + 7.5, "spendCut": {"rate": "1,240", "ceiling": "1,000"}}]
        rest = [{"t": TS0 + 8, "state": "working"}, {"t": TS0 + 8.25, "spendCut": {"rate": "2,050", "ceiling": "1,000"}},
                {"t": TS0 + 8.5, "state": "idle", "by": "stop"},
                {"t": TS0 + 9, "state": "retrying"}, {"t": TS0 + 9.5, "state": "retrying"},
                {"t": TS0 + 10, "machineCut": "deploy"}, {"t": TS0 + 11, "retriesGaveUp": True, "retries": 5, "errorKind": "overloaded"}]
        return head + rest if tail else head

    def postal_recs(self, tail):
        head = [{"ev": "sent", "id": "m1", "from_id": SID, "to_id": "22222222-2222-4333-8444-000000000302", "body": "ping", "t": TS0, "kind": "coordinate"},
                {"ev": "exec", "id": "m1", "t": TS0 + 1, "dmid": None}]
        rest = [{"ev": "sent", "id": "m2", "from_id": SID, "to_id": SID, "body": "note", "t": TS0 + 2},
                {"ev": "recall", "id": "m2", "t": TS0 + 3}, {"ev": "unexec", "id": "m1", "t": TS0 + 4},
                {"ev": "sent", "id": "m3", "from_id": SID, "to_id": "22222222-2222-4333-8444-000000000302", "body": "handoff",
                 "t": TS0 + 5, "park": True}]   # a parked send: the feed's parked-handoff fold (2026-09-18)
        return head + rest if tail else head

    def queue_recs(self, tail):
        head = [{"type": "queue-operation", "operation": "enqueue", "content": "first", "timestamp": _iso(TS0)},
                {"type": "queue-operation", "operation": "enqueue", "content": "second", "timestamp": _iso(TS0 + 1)}]
        rest = [{"type": "queue-operation", "operation": "remove", "content": "first", "timestamp": _iso(TS0 + 2)},
                {"type": "queue-operation", "operation": "enqueue", "content": "third", "timestamp": _iso(TS0 + 3)}]
        return head + rest if tail else head

    def nudge_recs(self, tail):
        head = [{"gid": SID + ":g1", "t": TS0 + 1}, {"gid": SID + ":g1", "t": TS0 + 2}]
        rest = [{"gid": SID + ":g2", "t": TS0 + 3}, {"gid": SID + ":g1", "t": TS0 + 4}]
        return head + rest if tail else head

    def recs_of(self, p, tail):
        return {self.leaf: self.leaf_recs, self.agent: self.agent_recs, self.states: self.states_recs,
                self.postal: self.postal_recs, self.queue_leaf: self.queue_recs, self.nudge: self.nudge_recs}[p](tail)

    def answers(self):
        return {
            "sessionMeta": dict(km._session_meta(self.leaf)),
            "bgRunning": km._bg_scan_cached(self.leaf),
            "bgAll": km._bg_scan_all_cached(self.leaf),
            "agentLaunches": {k: (dict(v) if isinstance(v, dict) else sorted(v)) for k, v in km._agent_launch_state(self.leaf).items()},
            "agentGist": km._agent_steps(self.agent),
            "agentLaunchIds": sorted(km._agent_launch_ids(self.agent)),
            "statesOverlay": km._states_awaiting_overlay(SID),
            "stateIntervals": km._state_intervals(SID, "working", TS0 + 200),
            "statesNotes": km._states_notes(SID),
            "machineCut": km._last_machine_cut(SID),
            "spendCuts": km._spend_cuts(SID),
            "lastState": km._last_state(SID),
            "lastNaturalState": km._last_natural_state(SID),
            "retryingSince": km._fold_records(km._retrying_since_cache, jd.STATE / "states" / (SID + ".jsonl"), lambda: None,
                                              km._retrying_since_step, ckpt="retryingSince"),
            "queueLedger": km._pending_ledger(self.queue_leaf),
            "wakeTail2": km._undelivered_wake_tail(self.queue_leaf),
            "nudgeTimes": km._nudge_times(),
            "bgJudge": [dict(t) for t in jd._bg_unresolved(self.leaf, now=TS0 + 100)],
            "postalLog": {k: (dict(v) if isinstance(v, dict) else sorted(v)) for k, v in
                          km._fold_records(km._postal_log_cache, self.postal, km._postal_log_fresh, km._postal_log_step, ckpt="postalLog").items()},
            "parkedHandoffs": dict(km._fold_records(km._parked_fold_cache, self.postal, km._parked_fold_fresh, km._parked_fold_step,
                                                    ckpt="parkedHandoffs")),
        }

    def write_all(self, tail):
        for p in self.files:
            _write(p, self.recs_of(p, tail))

    def test_every_named_fold_resumes_from_its_checkpoint_and_equals_a_cold_fold(self):
        self.write_all(tail=False)
        self.answers()                                            # every fold at the prefix
        names = set()
        for p in self.files:
            self.assertTrue(em.checkpoint_write(p), p)
            names |= set(self.doc(p)["folds"])
        ALL_NAMES = {"sessionMeta", "bgRunning", "bgAll", "bgJudge", "agentLaunches", "agentGist", "agentLaunchIds", "statesOverlay",
                     "stateIntervals", "statesNotes", "machineCut", "queueLedger", "wakeTail2", "postalLog", "lastState",
                     "lastNaturalState", "retryingSince", "nudgeTimes", "parkedHandoffs", "spendCuts"}
        self.assertEqual(names, ALL_NAMES, "every kernel fold this test drives left its state in the checkpoint")
        ksrc = open(os.path.join(BIN, "romp-kernel")).read(); jsrc = open(os.path.join(BIN, "romp-judge")).read()
        import re as _re
        named = set(_re.findall(r'ckpt="([A-Za-z0-9]+)"', ksrc)) | set(_re.findall(r'name_fold_cache\([^,]+, "([A-Za-z]+)"\)', jsrc))
        self.assertEqual(named, ALL_NAMES, "every checkpoint name the kernel and the judge give a fold is driven here")
        self.fresh_process()
        for p in self.files:
            full, head = self.recs_of(p, True), self.recs_of(p, False)
            _append(p, *full[len(head):])
        warm = self.answers()
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})
        self.assertEqual(em.checkpoint_stats()["restored"], 6, "one restore per file")
        self.assertEqual(set(em.checkpoint_stats()["restoredFolds"]), ALL_NAMES, "every fold restored from its recorded state")
        for p in self.files:
            with em._JSONL_CACHE_LOCK:
                self.assertGreater(em._JSONL_CACHE[p][5], 0, "a tail entry: %s" % p)
        self.fresh_process(); em.set_checkpoint_dir(None)       # the cold answer: a fresh process with no checkpoints at all
        cold = self.answers()
        self.assertEqual(warm, cold)
        self.assertEqual(cold["sessionMeta"]["lastEditPath"], "/w/notes-api/api/cap.py", "the fixtures drive the folds")
        self.assertEqual(cold["sessionMeta"]["gitBranch"], "api")
        self.assertEqual([t["id"] for t in cold["bgRunning"]], ["toolu_bg2"])
        self.assertEqual(cold["agentLaunches"]["settled"], ["toolu_ag1"])
        self.assertEqual(len(cold["agentGist"]["steps"]), 3)
        self.assertEqual(cold["machineCut"], (float(TS0 + 10), "deploy"))
        self.assertEqual(cold["spendCuts"], [{"t": TS0 + 7.5, "rate": "1,240", "ceiling": "1,000"},
                                             {"t": TS0 + 8.25, "rate": "2,050", "ceiling": "1,000"}], "the guard's cut records, head and tail")
        self.assertEqual(len(cold["stateIntervals"]), 2, "two working stretches in the states rows")
        self.assertEqual(cold["lastState"], ("retrying", TS0 + 9.5)); self.assertEqual(cold["lastNaturalState"], ("retrying", TS0 + 9.5))
        self.assertEqual(cold["retryingSince"], TS0 + 9, "the current retrying stretch dates from its first row")
        _append(self.states, {"t": TS0 + 12, "state": "waiting", "by": "stop"})       # a Stop press: romp's row, not the session's
        self.assertEqual(km._last_state(SID), ("waiting", TS0 + 12))
        self.assertEqual(km._last_natural_state(SID), ("retrying", TS0 + 9.5), "the session's own newest state skips rows with `by`")
        self.assertEqual(cold["queueLedger"], ["second", "third"])
        self.assertEqual([e["text"] for e in cold["wakeTail2"][0]], ["second", "third"])
        self.assertEqual(cold["nudgeTimes"], {SID + ":g1": [TS0 + 1, TS0 + 2, TS0 + 4], SID + ":g2": [TS0 + 3]})
        self.assertEqual([t["id"] for t in cold["bgJudge"]], ["toolu_bg2"], "the judge's settled gate reads the same pairing")
        self.assertEqual(cold["postalLog"]["ended"], ["m2"])
        self.assertNotIn("m1", cold["postalLog"]["execd"])
        self.assertEqual(sorted(cold["parkedHandoffs"]), ["m3"], "the one park:true send, folded from the tail alone after the restore")

    def test_a_wake_tail_checkpoint_written_under_the_old_rules_is_never_restored(self):
        """The wake tail's rules changed on 2026-10-08 (romp's content-less sends kept out, meta deliveries read, a content-less
        remove taking nothing), and a document holding a tail folded under the old ones carries their phantoms: its fold's
        checkpoint name moved, so the first fold after the deploy reads the transcript whole instead of restoring them."""
        recs = [{"type": "queue-operation", "operation": "enqueue", "timestamp": _iso(TS0 + i)} for i in range(3)]   # romp's sends
        recs.append({"type": "queue-operation", "operation": "enqueue", "content": "kept", "timestamp": _iso(TS0 + 3)})
        _write(self.queue_leaf, recs)

        def old_rules(state, o):                                         # the shape the old fold kept: one entry per enqueue
            tail, n = state
            if o.get("type") == "queue-operation" and o.get("operation") == "enqueue":
                tail.append({"pos": n, "ts": str(o.get("timestamp") or ""), "text": o.get("content") or "", "wrapper": False})
            return (tail, n + 1)
        old = em.fold_records({}, self.queue_leaf, lambda: ([], 0), old_rules, ckpt="wakeTail")
        self.assertEqual(len(old[0]), 4, "the old document's tail: three phantoms and the real entry")
        self.assertTrue(em.checkpoint_write(self.queue_leaf))
        self.assertIn("wakeTail", self.doc(self.queue_leaf)["folds"])
        self.fresh_process()
        _append(self.queue_leaf, {"type": "queue-operation", "operation": "enqueue", "content": "later", "timestamp": _iso(TS0 + 4)})
        tail, mark = km._undelivered_wake_tail(self.queue_leaf)
        self.assertEqual([e["text"] for e in tail], ["kept", "later"], "the phantoms were not restored")
        self.assertNotIn("wakeTail", em.checkpoint_stats()["restoredFolds"], "nothing restored under the old name")

    def test_the_kernel_writes_at_a_settle_or_a_states_log_move_and_not_otherwise(self):
        self.write_all(tail=False)
        self.answers()
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        try:
            self.assertGreater(len(em.checkpoint_dirty()), 0)
            self.assertEqual(km._persist_checkpoints(TS0 - 1), 0, "the first sight of a session records its evidence and writes nothing: "
                             "the evidence predates this kernel life, and a boot must not prime every session at once (2026-09-11)")
            turn_end[0] = TS0 + 50                                 # a settle after the first sight: new evidence
            km._persist_checkpoints(TS0)
            self.assertEqual(sorted(set(em.checkpoint_dirty()) & {self.leaf, self.agent, self.states, self.postal}), [])
            self.assertIn(self.queue_leaf, em.checkpoint_dirty(), "a file of no session waits for exit")
            _append(self.leaf, _user("more", "u9", "a3", TS0 + 100))
            km._session_meta(self.leaf)
            self.assertIn(self.leaf, em.checkpoint_dirty())
            self.assertEqual(km._persist_checkpoints(TS0 + 1), 0, "no settle, no states move: nothing written")
            turn_end[0] = TS0 + 100                                # the Stop hook stamped the settle
            em._ASM_CKPT_STATS["skipped"] = {}
            self.assertEqual(km._persist_checkpoints(TS0 + 2), 1)
            self.assertGreaterEqual(em.asm_checkpoint_stats()["skipped"].get("noEntry", 0) + em.asm_checkpoint_stats()["skipped"].get("noCut", 0), 1,
                                    "the settle write asked for the leaf's assembly document too (a counted skip here: no parse, no cut)")
            self.assertNotIn(self.leaf, em.checkpoint_dirty())
            _append(self.agent, _assistant([{"type": "tool_use", "id": "toolu_s9", "name": "Bash", "input": {"command": "true"}}], "s9", "s2", TS0 + 101))
            km._agent_steps(self.agent)
            self.assertEqual(km._persist_checkpoints(TS0 + 3), 0)
            _append(self.states, {"t": TS0 + 102, "state": "working"})   # a states row is an event, with no settle
            self.assertEqual(km._persist_checkpoints(TS0 + 4), 1, "the agent file rides the states-log move")
            self.assertEqual(em.checkpoint_write_dirty(), 2, "exit writes what is left (the queue ledger's file and the nudge log)")
            self.assertEqual(em.checkpoint_dirty(), [])
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn

    def test_the_settle_write_leaves_a_cursor_for_every_leaf_fold_whether_or_not_it_ran(self):
        """A fold no caller happened to run before the write (a kernel stopped before a judges' pass reached its
        background-task pairing) would have no cursor for the next process, whose first run of it would read the leaf
        whole: the settle write and the exit drain bring every leaf fold current first (_prime_leaf_folds)."""
        self.write_all(tail=False)
        self.fresh_process()
        km._session_meta(self.leaf)                               # the one fold a build happened to run
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        try:
            self.assertEqual(km._persist_checkpoints(TS0 - 1), 0, "first sight: recorded, nothing written")
            turn_end[0] = TS0
            self.assertGreaterEqual(km._persist_checkpoints(TS0), 1)
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn
        self.assertEqual(sorted(self.doc(self.leaf)["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "queueLedger", "sessionMeta", "wakeTail2"],
                         "the leaf's document holds every leaf fold, the judges' pairing included")
        self.fresh_process()
        km._session_meta(self.leaf)                               # a restored TAIL entry: priming would read the file whole
        self.assertFalse(em.entry_whole_resident(self.leaf))
        size = os.path.getsize(self.leaf)
        self.assertTrue(km._prime_leaf_folds(self.leaf), "over a tail entry the folds holding a cursor at it are primed (an append)")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and the leaf was not read whole")
        self.assertFalse(km._prime_leaf_folds(str(self.leaf) + ".absent"), "no entry: nothing read")
        self.fresh_process(); km._session_meta(self.leaf)
        self.assertEqual(sorted(k for k in em._FOLD_REG if em._FOLD_REG[k].get(self.leaf)), ["sessionMeta"])
        self.assertTrue(km._prime_leaf_folds(self.leaf))
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "the four folds with no cursor here were left alone")
        self.assertEqual(sorted(k for k in em._FOLD_REG if em._FOLD_REG[k].get(self.leaf)), ["sessionMeta"])

    def test_a_fold_lagging_behind_a_restored_tail_entry_is_primed_at_the_settle_and_the_next_boot_restores_it(self):
        """Review find (2026-09-11, third round): after the first boot every restored leaf is a tail entry; a fold whose
        cursor merely lagged the leaf (the judges' pairing steps only in their passes) dropped out of the settle write, and
        the next boot's first pass read the leaf whole. Over a tail entry the primer steps every fold holding a cursor at
        that entry, so the document carries all five and the next boot restores each."""
        self.write_all(tail=False)
        self.answers()
        for p in self.files:
            em.checkpoint_write(p)
        self.fresh_process()
        for fn in (km._session_meta, km._bg_scan_cached, km._bg_scan_all_cached, km.jd._bg_scan, km._agent_launch_state):
            fn(self.leaf)                                             # five cursors restored at the tail entry
        self.assertFalse(em.entry_whole_resident(self.leaf))
        _append(self.leaf, _user("a new prompt", "u_lag", "a3", TS0 + 500))
        km._session_meta(self.leaf)                                   # the one fold a build stepped; the other four lag
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [TS0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        size = os.path.getsize(self.leaf)
        try:
            self.assertEqual(km._persist_checkpoints(TS0 + 499), 0, "first sight: recorded, nothing written")
            turn_end[0] = TS0 + 500                                   # the settle after the new prompt
            self.assertGreaterEqual(km._persist_checkpoints(TS0 + 501), 1)
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"], "all five at the new count")
        self.assertEqual({f["count"] for f in d["folds"].values()}, {d["count"]})
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "no whole read at the settle")
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0)
        km.jd._bg_scan(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0), before + 1, "the next boot restores the judges' pairing")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and reads no leaf whole")
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("_prime_leaf_folds(_s[\"path\"])", src, "the exit drain primes every session's leaf before its write")
        drain = src[src.index("def _drain_and_exit("):]; drain = drain[:drain.index("\ndef ", 1)]
        self.assertNotIn("_heal_cold_folds", drain, "...and never heals there: a whole read per leaf would blow the exit's budget (T359)")

    def test_a_tail_only_fold_is_healed_at_the_settle_by_one_whole_refold_and_the_next_boot_restores_it_warm(self):
        """T359: the devbox's leaves carried the kernel's background-task view as a cursor without a state (written under the
        64 KB cap, then perpetuated: a fold that began cold is written cursor-only, so every boot since restarted it cold over
        the tail and answered from the tail alone). The settle's primer now refolds such a fold whole once (the leaf read, once,
        at a settle, never at boot), the write carries its state, and the next boot restores it warm."""
        self.write_all(tail=False)
        self.answers()
        for p in self.files:
            em.checkpoint_write(p)
        d = self.doc(self.leaf)
        d["folds"]["bgAll"] = {"count": d["folds"]["bgAll"]["count"]}        # the document an older kernel left: cursor only
        em._ckpt_file(self.leaf).write_text(json.dumps(d))
        self.fresh_process()
        km._session_meta(self.leaf)                                         # a restored tail entry
        size = os.path.getsize(self.leaf)
        km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.checkpoint_stats()["coldFolds"], {"bgAll": 1}, "the boot restores it cold over the tail (cheap)")
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "no whole read at boot")
        rows = [{"sid": SID, "path": self.leaf}]
        saved_sessions, saved_turn = km._sessions, km._turn_end_key
        turn_end = [TS0]
        km._sessions = lambda now: rows
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        try:
            self.assertEqual(km._persist_checkpoints(TS0 + 499), 0, "first sight: recorded, nothing written")
            turn_end[0] = TS0 + 500
            self.assertGreaterEqual(km._persist_checkpoints(TS0 + 501), 1, "the settle writes")
        finally:
            km._sessions, km._turn_end_key = saved_sessions, saved_turn
        self.assertGreaterEqual(em.read_bytes_report().get(self.leaf, 0), size, "the settle's primer read the leaf whole ONCE for the heal")
        self.assertEqual(em.cold_fold_reasons(self.leaf), {}, "the fold is complete again")
        self.assertIn("state", self.doc(self.leaf)["folds"]["bgAll"], "and written whole")
        self.assertEqual(km._bg_scan_all_cached(self.leaf), self.answers()["bgAll"], "the healed view equals a whole fold's")
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgAll", 0); cold_before = em.checkpoint_stats()["coldFolds"].get("bgAll", 0)
        km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgAll", 0), before + 1, "the next boot restores it warm")
        self.assertEqual(em.checkpoint_stats()["coldFolds"].get("bgAll", 0), cold_before, "and not cold")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and reads no leaf whole")

    def _converge_world(self, strip, extra=(), quiescent=False):
        """Documents for every file, then the leaf's document with `strip`'s folds removed or reduced to bare cursors (what an
        older kernel, or a kernel where the fold never ran, left behind), then a fresh process. `extra` names generic folds
        run over the leaf beside the five leaf folds (the transcript's wake-tail and queue-ledger folds in production).
        `quiescent` ages the leaf before its document is written, so the document records the old mtime and the leaf stands
        unchanged past the reader's quiescence window (an idle session)."""
        self.write_all(tail=False)
        saved_q = em._DROP_AFTER_QUIESCENT_S
        if quiescent:
            old = time.time() - 600
            os.utime(self.leaf, (old, old))
            em._DROP_AFTER_QUIESCENT_S = 1e9                       # the world's own folds and writes must not meet the drop
        try:
            self.answers()
            for name in extra:
                em.fold_records({}, self.leaf, list, lambda st, o: st + [1], ckpt=name)
            for p in self.files:
                em.checkpoint_write(p)
        finally:
            em._DROP_AFTER_QUIESCENT_S = saved_q
        d = self.doc(self.leaf)
        for name, how in strip.items():
            if how == "missing":
                d["folds"].pop(name, None)
            else:
                d["folds"][name] = {"count": d["folds"][name]["count"]}
        em._ckpt_file(self.leaf).write_text(json.dumps(d))
        self.fresh_process()
        rows = [{"sid": SID, "path": self.leaf}]
        self._saved_sessions = km._sessions
        km._sessions = lambda now, **kw: rows
        self.addCleanup(setattr, km, "_sessions", self._saved_sessions)

    def test_converge_writes_the_folds_a_boot_ran_whole_and_the_next_boot_restores_them(self):
        """T360: a fold missing from a document (it never ran in the writing process) is read whole at the next boot, and that
        cost was paid at EVERY boot because an idle session never settles, so its document was never rewritten (the devbox:
        the judges' pairing missing from 39 of 60 documents, 2.4 GB per boot). The converge pass, on the pusher's cycle, writes
        a dirty document that lacks a state this process now holds; over the whole entry the boot's read left, every leaf
        fold is primed first, so one write carries all five and the next boot restores them."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"})
        size = os.path.getsize(self.leaf)
        jd._bg_scan(self.leaf)                                        # the judges' first pass: no document entry, a whole refold
        self.assertGreaterEqual(em.read_bytes_report().get(self.leaf, 0), size, "the boot paid the whole read")
        self.assertEqual(em.checkpoint_converge_candidates(), [self.leaf], "a dirty document lacking a fold this process holds")
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        self.assertEqual(sorted(self.doc(self.leaf)["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "queueLedger", "sessionMeta", "wakeTail2"],
                         "the write carries every leaf fold: the four others primed over the whole entry, and the ledger and wake folds (T377)")
        self.assertTrue(all("state" in f for f in self.doc(self.leaf)["folds"].values()))
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["writes"], cv["primed"]), (1, 1)); self.assertGreater(cv["bytes"], 0)
        self.assertEqual(em.checkpoint_converge_candidates(), [], "nothing left to converge")
        self.assertEqual(km._converge_checkpoints(TS0 + 601), 0, "idempotent: the second pass writes nothing")
        self.assertEqual(em.checkpoint_stats()["converge"]["writes"], 1)
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0)
        jd._bg_scan(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0), before + 1, "the next boot restores the pairing warm")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and reads no leaf whole")

    def test_converge_leaves_a_document_that_carries_every_fold_alone(self):
        """Idempotence on a live session: its leaf is dirty every turn, but its document carries every fold that ran, so the
        converge pass never rewrites it (the settle does); no steady stream of writes."""
        self._converge_world({})
        km._session_meta(self.leaf)                                   # a restore: not dirty
        _append(self.leaf, _user("another prompt", "u_more", "a3", TS0 + 700))
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)   # appends: dirty, the document's folds all present
        self.assertIn(self.leaf, em.checkpoint_dirty())
        self.assertEqual(em.checkpoint_converge_candidates(), [], "a document carrying every fold that ran is not a candidate")
        self.assertEqual(km._converge_checkpoints(TS0 + 701), 0)
        self.assertEqual(em.checkpoint_stats()["converge"]["writes"], 0)
        self.assertIn(self.leaf, em.checkpoint_dirty(), "...and stays dirty for the settle")

    def test_converge_heals_an_idle_sessions_legacy_bare_cursor_once(self):
        """T360 (2): the legacy bare cursors of idle sessions (18 on the devbox), which no settle reaches: the converge pass heals
        the fold with one whole refold under its budget and writes the state; the next boot restores it warm."""
        self._converge_world({"bgAll": "bare"})
        size = os.path.getsize(self.leaf)
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "the boot itself read the tail only")
        self.assertEqual(em.checkpoint_converge_candidates(), [self.leaf])
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual(cv["heals"], 1); self.assertGreaterEqual(cv["healBytes"], size, "the heal's whole read is counted against the budget")
        self.assertEqual(cv["unhealed"], 0, "the fold the heal reran is not also counted unhealed: %s" % cv)
        self.assertIn("state", self.doc(self.leaf)["folds"]["bgAll"]); self.assertEqual(em.cold_fold_reasons(self.leaf), {})
        self.assertEqual(km._converge_checkpoints(TS0 + 601), 0)
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgAll", 0)
        km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgAll", 0), before + 1)
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2)

    def test_a_heal_the_settle_write_lands_before_the_passs_own_heal_is_not_counted_unhealed(self):
        """The review of 2026-10-05: the converge pass (the pusher's) listed the leaf's cold folds, healed, and counted as
        unhealed every listed fold its own heal did not rerun. The settle write's heal runs on the jobs thread since that
        change and could land between the two: the pass's heal then found nothing cold, and the fold, complete again, read
        as one unhealed. A stub lands the jobs thread's heal just before the pass's own, deterministically (the real heal on
        a thread of its own, joined, then the pass's own). The pass reads no cold list before its heal now: it counts what
        its own heal's drop took and could not rerun (`_heal_cold_folds(p, mine)`), here nothing, so none unhealed, no heal
        of the pass's own, and the fold complete and written."""
        self._converge_world({"bgAll": "bare"})
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        real, landed = km._heal_cold_folds, []

        def after_the_settles_heal(leaf, dropped_out=None):
            th = threading.Thread(target=lambda: landed.append(real(leaf)), name="jobs-probe")
            th.start(); th.join(30)
            self.assertFalse(th.is_alive(), "the jobs thread's heal finished")
            return real(leaf) if dropped_out is None else real(leaf, dropped_out)   # the pass's own: nothing is cold
        with mock.patch.object(km, "_heal_cold_folds", after_the_settles_heal):
            self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        self.assertEqual(landed, [["bgAll"]], "the jobs thread's heal reran the fold")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["unhealed"], cv["heals"]), (0, 0), "none unhealed, no heal of the pass's own: %s" % cv)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {})
        self.assertIn("state", self.doc(self.leaf)["folds"]["bgAll"], "the fold complete, and written")

    def test_a_heal_still_rerunning_on_the_jobs_thread_is_not_counted_unhealed(self):
        """The same race caught mid-rerun: the settle write's heal on the jobs thread has dropped the fold's cursor and cold
        mark and is still reading the leaf whole when the converge pass counts, so neither a cold mark nor a cursor is there
        to see. The pass counts only what its own drop took. A stub holds the jobs thread's rerun at its start (an event, no
        clock) until the pass has counted and written."""
        self._converge_world({"bgAll": "bare"})
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        real_fold, real_heal = km._bg_scan_all_cached, km._heal_cold_folds
        started, release, box = threading.Event(), threading.Event(), {}

        def held_fold(leaf, *a, **k):
            if threading.current_thread().name == "jobs-probe" and not started.is_set():
                started.set(); release.wait(30)                  # the jobs thread's rerun, held at its start
            return real_fold(leaf, *a, **k)

        def jobs_heal_in_flight(leaf, dropped_out=None):
            th = box["th"] = threading.Thread(target=lambda: box.setdefault("landed", real_heal(leaf)), name="jobs-probe")
            th.start()
            self.assertTrue(started.wait(30), "the jobs thread's heal dropped the cursor and began its rerun")
            return real_heal(leaf) if dropped_out is None else real_heal(leaf, dropped_out)   # the pass's own: nothing cold
        try:
            with mock.patch.object(km, "_heal_cold_folds", jobs_heal_in_flight), mock.patch.object(km, "_bg_scan_all_cached", held_fold):
                km._converge_checkpoints(TS0 + 600)
        finally:
            release.set()
            if "th" in box:
                box["th"].join(30)
        self.assertIn("th", box, "the pass healed the leaf")
        self.assertFalse(box["th"].is_alive(), "the jobs thread's heal finished")
        self.assertEqual(box.get("landed"), ["bgAll"], "and reran the fold")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["unhealed"], cv["heals"]), (0, 0), "none unhealed, no heal of the pass's own: %s" % cv)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {})

    def test_converge_is_bounded_per_cycle_in_bytes_and_defers_the_rest(self):
        """The budget: a second leaf (a copy) with the same gap; with a one-byte budget the pass writes the first candidate and
        defers the second (counted), the next pass takes it. The knobs: CKPT_CONVERGE_MS and CKPT_CONVERGE_BYTES."""
        self._converge_world({"bgJudge": "missing"})
        leaf2 = str(self.proj / ("bbbbbbbb-2222-3333-4444-555555555555.jsonl"))
        import shutil; shutil.copy(self.leaf, leaf2)
        rows = [{"sid": SID, "path": self.leaf}, {"sid": "bbbbbbbb-2222-3333-4444-555555555555", "path": leaf2}]
        km._sessions = lambda now, **kw: rows
        jd._bg_scan(self.leaf); jd._bg_scan(leaf2)                    # both read whole, both dirty, both lacking the pairing
        self.assertEqual(sorted(em.checkpoint_converge_candidates()), sorted([self.leaf, leaf2]))
        saved = km.CKPT_CONVERGE_BYTES; km.CKPT_CONVERGE_BYTES = 1
        self.addCleanup(setattr, km, "CKPT_CONVERGE_BYTES", saved)
        km._begin_checkpoint_cycle()                                  # the cycle's start hands the pass its budget (T362)
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1, "the first always writes; the budget stops the second")
        self.assertEqual(em.checkpoint_stats()["converge"]["deferred"], 1)
        self.assertEqual(len(em.checkpoint_converge_candidates()), 1)
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 601), 1, "the next pass takes it")
        self.assertEqual(em.checkpoint_converge_candidates(), [])

    def test_converge_budget_holds_when_the_first_candidate_writes_nothing(self):
        """Review, medium 1: the budget was gated on documents WRITTEN, so a first candidate that wrote nothing (a non-leaf whose
        only cold cursor was dropped, a failed write) let the pass heal every remaining leaf whole. Gated on candidates
        processed now, and the empty write is counted."""
        self._converge_world({"bgJudge": "missing"})
        a = str(self.td / "aaa.jsonl")                                # sorts before the leaves: the first candidate
        _write(a, [{"n": 0}, {"n": 1}])
        em.fold_records({}, a, list, lambda st, o: st + [o["n"]], ckpt="gen")
        em.checkpoint_write(a, force=True)
        d = self.doc(a); d["folds"]["gen"] = {"count": d["folds"]["gen"]["count"]}; em._ckpt_file(a).write_text(json.dumps(d))
        leaf2 = str(self.proj / ("bbbbbbbb-2222-3333-4444-555555555555.jsonl"))
        import shutil; shutil.copy(self.leaf, leaf2)
        self.fresh_process()
        km._sessions = lambda now, **kw: [{"sid": SID, "path": self.leaf}, {"sid": "bbbbbbbb-2222-3333-4444-555555555555", "path": leaf2}]
        em.fold_records({}, a, list, lambda st, o: st + [o["n"]], ckpt="gen")   # cold: the first candidate, writing nothing
        jd._bg_scan(self.leaf); jd._bg_scan(leaf2)
        self.assertEqual(em.checkpoint_converge_candidates(), [a, self.leaf, leaf2])
        saved = (km.CKPT_CONVERGE_MS, km.CKPT_CONVERGE_BYTES); km.CKPT_CONVERGE_MS, km.CKPT_CONVERGE_BYTES = 1e-6, 1   # (0 turns the pass off: T361)
        self.addCleanup(lambda: setattr(km, "CKPT_CONVERGE_MS", saved[0])); self.addCleanup(lambda: setattr(km, "CKPT_CONVERGE_BYTES", saved[1]))
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0, "the empty first candidate, then the budget")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["deferred"], cv["heals"], cv["failed"], cv["unhealed"]), (2, 0, 1, 1), "nothing healed past the budget: %s" % cv)

    def test_converge_keeps_the_states_of_folds_this_process_never_ran(self):
        """Review, medium 2: a converge write rebuilt the document from this process's cursors, stripping the states of folds it
        never ran (the transcript's wake-tail and queue-ledger folds beside the five leaf folds), and the next boot read the
        leaf whole for them. Every write merges the on-disk document's states for such folds (verified by its guard)."""
        self._converge_world({"bgJudge": "missing"}, extra=("extraA", "extraB"))
        self.assertEqual(len(self.doc(self.leaf)["folds"]), 6, "five leaf folds less the stripped one, plus two extra")
        jd._bg_scan(self.leaf)
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "extraA", "extraB", "queueLedger", "sessionMeta", "wakeTail2"], "nine: the two carried, and the ledger and wake folds primed (T377)")
        self.assertTrue(all("state" in f for f in d["folds"].values()))
        self.fresh_process()
        self.assertEqual(em.fold_records({}, self.leaf, list, lambda st, o: st + [1], on=self.kinds_sink(), ckpt="extraA"), [1] * 4)
        self.assertEqual(self._kinds[-1], "restore", "the next boot restores the carried fold")

    def kinds_sink(self):
        self._kinds = []
        return self._kinds.append

    def test_converge_drops_an_unhealable_cold_fold_once_and_the_next_boot_completes_it(self):
        """Review, medium 3: a cold fold the heal cannot rerun (not one of the leaf's five) kept its path a candidate for the
        kernel's life, one write every cycle. Its cursor and cold mark are dropped (counted unhealed), the write leaves it out,
        the pass writes once, and the next boot reads the leaf whole for that fold and writes its state."""
        self._converge_world({}, extra=("extraA",))
        d = self.doc(self.leaf); d["folds"]["extraA"] = {"count": d["folds"]["extraA"]["count"]}; em._ckpt_file(self.leaf).write_text(json.dumps(d))
        self.fresh_process()
        km._session_meta(self.leaf)
        em.fold_records({}, self.leaf, list, lambda st, o: st + [1], on=self.kinds_sink(), ckpt="extraA")
        self.assertEqual(self._kinds[-1], "cold"); self.assertEqual(em.cold_fold_reasons(self.leaf), {"extraA": "cold"})
        self.assertEqual(em.checkpoint_converge_candidates(), [self.leaf])
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        self.assertEqual(em.checkpoint_stats()["converge"]["unhealed"], 1)
        self.assertNotIn("extraA", self.doc(self.leaf)["folds"]); self.assertEqual(em.cold_fold_reasons(self.leaf), {})
        self.assertEqual(em.checkpoint_converge_candidates(), []); self.assertEqual(km._converge_checkpoints(TS0 + 601), 0, "once")
        self.fresh_process()
        em.fold_records({}, self.leaf, list, lambda st, o: st + [1], on=self.kinds_sink(), ckpt="extraA")
        self.assertEqual(self._kinds[-1], "refold", "the next boot reads the leaf whole for it, once")
        self.assertEqual(km._converge_checkpoints(TS0 + 700), 1)
        self.assertIn("state", self.doc(self.leaf)["folds"]["extraA"], "...and the pass writes its state")

    def test_a_path_the_settle_wrote_is_not_written_again_by_the_pass_in_the_same_cycle(self):
        """Review, low 4: a settling session's states log whose fold began cold (a legacy bare cursor) while the log's other folds
        hold cursors was written twice in one cycle: the settle write (dropping the cold cursor), then the pass again for the
        cold mark that survived the drop (seq 2 to 3, identical documents). The drop clears the mark and the pass skips the
        paths the settle just wrote."""
        self._converge_world({})
        d = self.doc(self.states); d["folds"]["lastState"] = {"count": d["folds"]["lastState"]["count"]}   # the log's legacy cursor
        em._ckpt_file(self.states).write_text(json.dumps(d))
        self.fresh_process()
        km._states_notes(SID); km._last_state(SID)                 # one fold restored at the witness, one cold
        self.assertEqual(em.cold_fold_reasons(self.states), {"lastState": "cold"})
        with open(self.states, "a") as f:                            # the settle's evidence: a states-log row, and a fold over it
            f.write(json.dumps({"t": TS0 + 480, "state": "idle"}) + "\n")
        km._states_notes(SID)                                        # ...stepped, so the log is dirty for the settle write
        self.assertIn(self.states, em.checkpoint_dirty())
        saved_turn = km._turn_end_key; turn_end = [TS0]
        km._turn_end_key = lambda sid, reg=None: turn_end[0]
        self.addCleanup(setattr, km, "_turn_end_key", saved_turn)
        self.assertEqual(km._persist_checkpoints(TS0 + 499), 0, "first sight")
        turn_end[0] = TS0 + 500
        self.assertGreaterEqual(km._persist_checkpoints(TS0 + 501), 1, "the settle writes the session's files")
        seq = self.doc(self.states)["seq"]
        self.assertNotIn("lastState", self.doc(self.states)["folds"], "the cold cursor dropped: left out, to refold whole at its next run")
        self.assertEqual(km._converge_checkpoints(TS0 + 501), 0, "the pass writes nothing for it this cycle")
        self.assertEqual(self.doc(self.states)["seq"], seq, "one write per path per cycle")

    def test_a_fold_far_behind_the_entry_is_left_out_so_it_cannot_drag_the_cut(self):
        """Review: a carried (or lagging) fold at a low count moved the document's cut back to it, and every later boot read from
        that count to the end and held those records resident (a 2000-record leaf carrying one fold at count 1: the next boot
        read the file whole where it had read 128 bytes). Both the writer's own lagging cursors and the carry stop at 64 records
        or an eighth of the entry behind; further behind the fold is left out and refolds whole once when it next runs."""
        _write(self.leaf, [_user("p%d" % i, "u%d" % i, None, TS0 + i) for i in range(2000)])
        early = {}
        em.fold_records(early, self.leaf, list, lambda st, o: st + [1], ckpt="early")   # runs over 1 record...
        early[self.leaf] = (1, early[self.leaf][1], [1])                                 # ...and stopped at count 1 (a skipped fold)
        self.fold_leaf = lambda: em.fold_records({}, self.leaf, list, lambda st, o: st + [1], ckpt="whole")
        self.fold_leaf()
        self.assertTrue(em.checkpoint_write(self.leaf, force=True))
        d = self.doc(self.leaf)
        self.assertEqual((d["count"], sorted(d["folds"])), (2000, ["whole"]), "the fold 1999 behind is left out, the cut stays at the witness")
        d["folds"]["early"] = {"count": 1, "state": em._ckpt_encode([1])}                  # the same fold carried from an older document
        em._ckpt_file(self.leaf).write_text(json.dumps(d))
        self.fresh_process()
        self.fold_leaf()                                                                    # a restore; the write must not carry count 1
        self.assertTrue(em.checkpoint_write(self.leaf, force=True))
        d = self.doc(self.leaf)
        self.assertEqual((d["count"], sorted(d["folds"])), (2000, ["whole"]), "the carry stops at the bound too")
        self.fresh_process()
        size = os.path.getsize(self.leaf)
        self.fold_leaf()
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), 512, "the next boot reads the tail only: %d of %d bytes" % (em.read_bytes_report().get(self.leaf, 0), size))
        near = {}
        em.fold_records(near, self.leaf, list, lambda st, o: st + [1], ckpt="near")
        near[self.leaf] = (1990, near[self.leaf][1], [1] * 1990)                           # ten behind: inside the bound
        self.assertTrue(em.checkpoint_write(self.leaf, force=True))
        self.assertEqual((self.doc(self.leaf)["count"], sorted(self.doc(self.leaf)["folds"])), (1990, ["near", "whole"]), "a small lag is written at its count")

    def test_a_fold_stopped_beyond_the_bound_on_a_dirty_leaf_does_not_keep_the_pass_writing(self):
        """Round three: a fold stopped more than the bound behind on a path dirty without a settle (the leaf mid-turn) was left out
        by the writer and refused by the carry, its shape read None, and the pass rewrote the identical document every dirty
        cycle. The candidate check knows the bound: such a fold is no candidate (it refolds once when it runs)."""
        self._converge_world({"bgJudge": "missing"})
        _write(self.leaf, [json.loads(l) for l in open(self.leaf)] + [_user("p%d" % i, "ux%d" % i, None, TS0 + 1000 + i) for i in range(400)])
        stopped = {}
        em.fold_records(stopped, self.leaf, list, lambda st, o: st + [1], ckpt="stopped")   # a sixth fold, run over the whole leaf...
        stopped[self.leaf] = (2, stopped[self.leaf][1], [1, 1])                              # ...and stopped at count 2 (skipped since)
        jd._bg_scan(self.leaf)                                                                 # the missing pairing: a real candidate
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1, "one write, for the pairing")
        self.assertNotIn("stopped", self.doc(self.leaf)["folds"])
        seq = self.doc(self.leaf)["seq"]
        for k in range(3):                                                                     # three dirty cycles: an append and a fold each
            _write(self.leaf, [json.loads(l) for l in open(self.leaf)] + [_user("q%d" % k, "uq%d" % k, None, TS0 + 2000 + k)])
            km._session_meta(self.leaf)
            self.assertIn(self.leaf, em.checkpoint_dirty())
            self.assertEqual(em.checkpoint_converge_candidates(), [], "a fold beyond the bound is no candidate")
            self.assertEqual(km._converge_checkpoints(TS0 + 601 + k), 0)
        self.assertEqual(self.doc(self.leaf)["seq"], seq, "no write over three dirty cycles")

    def test_a_fallback_forgets_the_documents_shape(self):
        """Review, low 4: _ckpt_fallback removed the document but left the pass believing it still carried every state."""
        self._converge_world({})
        self.assertNotIn(self.leaf, em._CKPT_DOC_FOLDS, "a fresh process knows no shapes until it consults a document")
        km._session_meta(self.leaf)                                       # the restore consults the document: its shape is known
        self.assertTrue(em._CKPT_DOC_FOLDS.get(self.leaf))
        em._ckpt_fallback(self.leaf, "guard", "test")
        self.assertNotIn(self.leaf, em._CKPT_DOC_FOLDS)
        self.assertEqual(em.checkpoint_stats()["fallbacks"].get("guard"), 1)

    def test_the_carry_refuses_a_same_size_edit_under_another_mtime(self):
        """Review, low 3: the carry checked the guard bytes alone, so an in-place edit outside them (a same-size rewrite under
        another mtime) was laundered into a fresh document the restore then trusted. The carry asks the restore's verdict first.
        The gate is driven directly: the reader's entry is a plain whole read that consulted no document (so no restore fallback
        removed it first), a cursor is planted at that entry, and the write's carry meets the document over the edited file. In
        production the restore usually falls back before a write can carry, so this path is defensive."""
        self._converge_world({}, extra=("extraA",))
        recs = [json.loads(l) for l in open(self.leaf)]
        recs[1] = dict(recs[1], text="X" * len(recs[1].get("text", "")))   # a same-size edit inside the prefix, outside the guard
        _write(self.leaf, recs); os.utime(self.leaf, ns=(os.stat(self.leaf).st_atime_ns, os.stat(self.leaf).st_mtime_ns + 10 ** 9))
        self.fresh_process()
        ent = em._read_jsonl_entry(self.leaf)                              # a whole read, no document consulted
        self.assertEqual(ent[5], 0)
        em._FOLD_REG["planted"] = {self.leaf: (ent[5] + len(ent[4]), ent[6], [1])}   # a cursor at that entry
        self.addCleanup(em._FOLD_REG.pop, "planted", None)
        self.assertTrue(os.path.exists(em._ckpt_file(self.leaf)), "the older document is still on disk when the write runs")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {}, "no restore refused it first: the carry's own gate decides")
        self.assertTrue(em.checkpoint_write(self.leaf, force=True))
        self.assertEqual(sorted(self.doc(self.leaf)["folds"]), ["planted"], "the write carries nothing from a document the verdict calls a rewrite")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})

    def test_converge_refuses_a_quiescent_leaf_and_reads_nothing_over_three_cycles(self):
        """T361, the live loop: an idle leaf (unchanged past the reader's quiescence window) with a legacy bare cursor. The
        reader drops such a file's whole entry right after the agent-launch fold steps it, so the pass's heal read the file whole
        every cycle and its write found no entry and failed, forever (about 300 MB per cycle on the devbox). The pass refuses a
        quiescent leaf outright, counts it, and skips it until the file changes: zero reads, zero writes, three cycles."""
        self._converge_world({"bgAll": "bare"}, quiescent=True)      # unchanged for ten minutes
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)  # the boot: the tail, and bgAll cold
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        size = os.path.getsize(self.leaf); read0 = em.read_bytes_report().get(self.leaf, 0)
        self.assertLess(read0, size / 2)
        self.assertEqual(em.checkpoint_converge_candidates(), [self.leaf])
        for k in range(3):
            self.assertEqual(km._converge_checkpoints(TS0 + 600 + k), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["quiescent"], cv["skipped"], cv["heals"], cv["writes"], cv["failed"]), (1, 1, 0, 0, 0),
                         "refused once, the hold counted once for the file's state, not per cycle: %s" % cv)
        self.assertEqual(em.read_bytes_report().get(self.leaf, 0), read0, "the pass read nothing of the leaf")
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"}, "left for the boot's cold refold or the next settle")
        saved = km._stat_key_ns
        km._stat_key_ns = lambda p: self.fail("the held entry's stamp answers: no stat while the reader holds the file")
        try:
            self.assertTrue(km._converge_skipped(self.leaf), "stat-free while the reader's entry stands")
        finally:
            km._stat_key_ns = saved
        _append(self.leaf, _user("a new prompt", "u_new", "a3", TS0 + 700))   # the file changes
        self.assertTrue(km._converge_skipped(self.leaf), "held until the reader sees the change: no fold has looked, nothing new to write")
        km._session_meta(self.leaf)                                       # a fold re-reads it: the entry's stamp moved
        self.assertFalse(km._converge_skipped(self.leaf), "the pass may look again")

    def test_converge_primes_a_quiescent_leaf_whose_entry_is_whole_resident_and_the_drop_writes_it(self):
        """T362 follow-up (the first live boot after the merge: dropWrites 0, the pairing still missing from 37 of 60 documents).
        The only fold that drops quiescent leaves is the agent-launch fold, which the builders call for sessions with agents and
        the pass never reached for a quiescent leaf (the T361 refusal), so after the boot's whole read nothing ran a drop over an
        idle leaf and its document stayed stale with the read resident. The refusal is narrowed to a leaf whose entry is NOT
        whole-resident: with the boot's read in hand the heal and the prime step records in memory, the prime's last fold is
        the launch fold, and its drop writes the document from that read and pops the entry; the pass counts it."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing", "bgAll": "bare"}, quiescent=True)
        size = os.path.getsize(self.leaf)
        km._bg_scan_all_cached(self.leaf)                             # the boot: the background view restores cold over the tail
        jd._bg_scan(self.leaf)                                        # the judges' first pass: the whole read, resident
        self.assertTrue(em.entry_whole_resident(self.leaf)); self.assertEqual(em.cold_fold_reasons(self.leaf), {"bgAll": "cold"})
        read0 = em.read_bytes_report().get(self.leaf, 0); dropped0 = em.record_cache_stats()["dropped"]
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0, "the pass wrote nothing itself")
        self.assertGreater(em._CKPT_CYCLE["spent"], 0, "the drop's write charged the cycle's take")
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "queueLedger", "sessionMeta", "wakeTail2"])
        self.assertTrue(all("state" in f for f in d["folds"].values()), "the healed view and the primed folds, all complete")
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "and the entry left memory")
        self.assertEqual(em.checkpoint_converge_candidates(), [], "nothing left to converge")
        for k in (1, 2):                                              # the guard (the T361 loop was a pass acting every cycle):
            km._begin_checkpoint_cycle()                              #  two more cycles do nothing for this leaf
            self.assertEqual(km._converge_checkpoints(TS0 + 600 + k), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["passes"], cv["heals"], cv["viaDrop"], cv["dropWrites"], cv["quiescent"], cv["failed"], cv["writes"]),
                         (1, 1, 1, 1, 0, 0, 0), "three cycles: one heal, one drop write, then nothing: %s" % cv)
        self.assertEqual(em.record_cache_stats()["dropped"], dropped0 + 1, "one pop")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0) - read0, 256, "no read of records: the write's 64-byte guard check alone")
        self.assertIn("viaDrop", km._PERF_STATS.snapshot()["checkpoints"]["converge"], "the story is on /perf")
        self.fresh_process()
        jd._bg_scan(self.leaf)
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "the next boot reads the tail")

    def test_converge_over_a_resident_quiescent_leaf_whose_launch_fold_restores_writes_once(self):
        """The other live shape: the document carries the launch state and lacks the pairing. The prime's launch fold restores
        at the witness, the drop writes without popping (nothing stepped), and the pass, asking the rule, writes nothing more."""
        self._converge_world({"bgJudge": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf)
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["viaDrop"], cv["dropWrites"], cv["writes"], cv["quiescent"]), (1, 1, 0, 0), "%s" % cv)
        d = self.doc(self.leaf); self.assertIn("state", d["folds"]["bgJudge"])
        self.assertTrue(em.entry_whole_resident(self.leaf), "nothing stepped: the entry stays")
        km._begin_checkpoint_cycle(); self.assertEqual(km._converge_checkpoints(TS0 + 601), 0)
        self.assertEqual((em.checkpoint_stats()["converge"]["passes"], self.doc(self.leaf)["seq"]), (1, d["seq"]), "one pass, one write")

    def test_with_the_drop_write_off_the_pass_refuses_a_resident_quiescent_leaf_and_keeps_its_read(self):
        """Follow-up review, low 1: under a zero byte budget the prime popped the boot's read without writing and viaDrop counted a
        leaf where the drop wrote nothing. With the drop write off the exception does not apply: refused, the entry kept."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); seq = self.doc(self.leaf)["seq"]
        saved = km.CKPT_CONVERGE_BYTES; km.CKPT_CONVERGE_BYTES = 0
        self.addCleanup(setattr, km, "CKPT_CONVERGE_BYTES", saved)
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["quiescent"], cv["viaDrop"], cv["dropWrites"], cv["heals"]), (1, 0, 0, 0), "%s" % cv)
        self.assertTrue(em.entry_whole_resident(self.leaf), "the boot's read is kept, not discarded with the document stale")
        self.assertEqual(self.doc(self.leaf)["seq"], seq)

    def test_via_drop_counts_only_a_write_that_happened(self):
        """Follow-up review, low 1: with the drop's write failing, the pop still left nothing to write, and viaDrop counted it. It
        counts from a write that happened for that path; a failed one is the pass's failure, skipped until the file changes."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); seq = self.doc(self.leaf)["seq"]
        saved = em.checkpoint_write; em.checkpoint_write = lambda path, force=False: False
        self.addCleanup(setattr, em, "checkpoint_write", saved)
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["viaDrop"], cv["dropWrites"], cv["failed"]), (0, 0, 1), "%s" % cv)
        self.assertTrue(km._converge_skipped(self.leaf)); self.assertEqual(self.doc(self.leaf)["seq"], seq)

    def test_a_cold_launch_fold_is_healed_and_the_other_folds_still_primed_before_the_drop(self):
        """Follow-up review, low 2: when the launch fold was itself the cold one, the heal's rerun of it dropped the entry mid-heal,
        the prime never ran, and the folds this process never called stayed out of the document. The drop is held while the pass
        heals and primes, then paid once: one write with every leaf fold, one pop."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "bare", "bgAll": "missing", "sessionMeta": "missing"}, quiescent=True)
        km._agent_launch_state(self.leaf)                             # the boot: the launch fold restores cold over the tail
        jd._bg_scan(self.leaf)                                        # the judges' first pass: the whole read, resident
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"agentLaunches": "cold"}); self.assertTrue(em.entry_whole_resident(self.leaf))
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "queueLedger", "sessionMeta", "wakeTail2"], "every leaf fold, primed before the drop")
        self.assertTrue(all("state" in f for f in d["folds"].values()), "the healed launch state complete, the rest primed")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["heals"], cv["viaDrop"], cv["dropWrites"], cv["writes"]), (1, 1, 1, 0), "%s" % cv)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "one pop, after the prime")
        self.assertEqual(em.checkpoint_converge_candidates(), [])

    def test_an_unhealable_cold_fold_is_counted_on_the_via_drop_path_too(self):
        """Follow-up review, low 3: the viaDrop continue preceded the unhealed counter, so a cold fold the pass could not rerun
        (not one of the leaf's five) was dropped uncounted."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing", "extraCold": "bare"}, extra=("extraCold",), quiescent=True)
        em.fold_records({}, self.leaf, list, lambda st, o: st + [1], ckpt="extraCold")   # the boot: the generic fold restores cold
        jd._bg_scan(self.leaf)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"extraCold": "cold"})
        km._begin_checkpoint_cycle()
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["unhealed"], cv["viaDrop"], cv["dropWrites"]), (1, 1, 1), "%s" % cv)
        self.assertNotIn("extraCold", self.doc(self.leaf)["folds"], "left out: its next run reads the file whole once")

    def test_a_raise_inside_the_drop_hold_leaves_nothing_held_on_the_thread(self):
        """A hold left set by a raise would hold every later drop on the pusher thread, unpaid, for the kernel's life."""
        with self.assertRaises(RuntimeError):
            with em.hold_quiescent_drops():
                em._DROP_HOLD.held["x"] = True
                raise RuntimeError("a fold raised")
        self.assertIsNone(em._DROP_HOLD.held); self.assertEqual(em.pay_held_drops(), {})

    def test_the_settle_primes_the_ledger_and_wake_folds_over_a_whole_resident_entry_so_the_next_boot_reads_a_tail(self):
        """T377, reader two: the queue-ledger fold runs at a session's echo settle and the wake-tail fold on its wake, neither
        among the five folds the settle primes, so a live leaf whose document lacked them (13 of 24 live documents on the
        devbox) was refolded WHOLE at every boot's first call: about 0.7 GB per boot. Red first: the whole read is counted per
        fold under checkpoints.refolds; the settle primes both folds over a whole-resident entry only, once per read (three
        settles, one prime), the write carries them, and the next boot's call reads the tail."""
        self._converge_world({"bgJudge": "missing"})                 # every document; the leaf's lacks the pairing and, as every
        size = os.path.getsize(self.leaf)                            #  world's does, the ledger and wake folds (they never ran)
        self.assertNotIn("queueLedger", self.doc(self.leaf)["folds"])
        km._session_meta(self.leaf)                                  # the boot: the tail
        read0 = em.read_bytes_report().get(self.leaf, 0)
        with em._CKPT_LOCK:
            em._CKPT_STATS["refolds"] = {}                           # the world's own setup refolds are not this test's
        km._pending_ledger(self.leaf)                                # the ledger fold's first call: nothing to restore, a whole refold
        got = em.read_bytes_report().get(self.leaf, 0) - read0
        self.assertGreaterEqual(got, size, "read whole, as on the base")
        rf = em.checkpoint_stats()["refolds"]
        self.assertEqual(rf.get("queueLedger", {}).get("count"), 1, "%s" % rf); self.assertGreaterEqual(rf["queueLedger"]["bytes"], size)
        self.fresh_process()                                         # a boot whose settle finds the read in hand
        jd._bg_scan(self.leaf)                                       # the judges' whole read (the pairing missing): whole-resident
        calls = []
        real_l, real_w = km._pending_ledger, km._undelivered_wake_tail
        km._pending_ledger = lambda p: (calls.append("ledger"), real_l(p))[1]
        km._undelivered_wake_tail = lambda p: (calls.append("wake"), real_w(p))[1]
        self.addCleanup(setattr, km, "_pending_ledger", real_l); self.addCleanup(setattr, km, "_undelivered_wake_tail", real_w)
        for k in range(3):                                           # three settles: both advanced each time (a stat over the whole
            km._prime_leaf_folds(self.leaf); em.checkpoint_write(self.leaf)   #  entry), no read, one document that carries them
        self.assertEqual(sorted(calls), ["ledger"] * 3 + ["wake"] * 3, "advanced at every settle like the five: %s" % calls)
        d = self.doc(self.leaf)
        self.assertIn("state", d["folds"].get("queueLedger", {}), "%s" % sorted(d["folds"])); self.assertIn("state", d["folds"].get("wakeTail2", {}))
        self.assertEqual(em.checkpoint_stats()["refolds"].get("queueLedger"), rf["queueLedger"], "the prime over records in hand is no refold read")
        self.fresh_process()
        km._session_meta(self.leaf)                                  # the next boot: the tail
        read1 = em.read_bytes_report().get(self.leaf, 0)
        km._pending_ledger(self.leaf); km._undelivered_wake_tail(self.leaf)
        self.assertLess(em.read_bytes_report().get(self.leaf, 0) - read1, size / 2, "restored: a tail read, not the whole leaf")
        self.assertEqual(em.checkpoint_stats()["refolds"].get("queueLedger"), rf["queueLedger"], "no refold at the next boot: the ledger's count stands")

    def test_converge_skips_a_path_whose_write_failed_until_its_file_changes(self):
        """T361 (b): a failed write must not repeat every cycle."""
        self._converge_world({"bgJudge": "missing"})
        jd._bg_scan(self.leaf)
        saved = em.checkpoint_write; em.checkpoint_write = lambda path, force=False: False
        try:
            self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        finally:
            em.checkpoint_write = saved
        self.assertEqual(em.checkpoint_stats()["converge"]["failed"], 1)
        self.assertEqual(km._converge_checkpoints(TS0 + 601), 0, "skipped while the file stands")
        self.assertEqual(em.checkpoint_stats()["converge"]["skipped"], 1)
        _append(self.leaf, _user("a new prompt", "u_new", "a3", TS0 + 700)); km._session_meta(self.leaf)
        self.assertEqual(km._converge_checkpoints(TS0 + 702), 1, "the file changed: written")

    def test_converge_heal_bytes_reach_the_budget(self):
        """T361 (c): the heal's whole read is attributed under the reader's own key, so the byte budget sees it."""
        self._converge_world({"bgAll": "bare"})
        km._session_meta(self.leaf); km._bg_scan_all_cached(self.leaf)   # a tail entry, bgAll cold; the leaf is fresh (not quiescent)
        size = os.path.getsize(self.leaf)
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 1)
        cv = em.checkpoint_stats()["converge"]
        self.assertGreaterEqual(cv["healBytes"], size, "the heal's whole read counted: %s" % cv)

    def test_converge_off_at_zero_milliseconds(self):
        """T361 (d): ROMP_CKPT_CONVERGE_MS=0 turns the pass off outright, candidates or not."""
        self._converge_world({"bgJudge": "missing"})
        jd._bg_scan(self.leaf)
        self.assertEqual(em.checkpoint_converge_candidates(), [self.leaf])
        saved = km.CKPT_CONVERGE_MS; km.CKPT_CONVERGE_MS = 0.0
        self.addCleanup(setattr, km, "CKPT_CONVERGE_MS", saved)
        self.assertEqual(km._converge_checkpoints(TS0 + 600), 0)
        self.assertEqual(em.checkpoint_stats()["converge"]["passes"], 0, "no pass counted: off")

    def _lock_free_write(self):
        """checkpoint_write wrapped to assert neither the reader's lock nor the checkpoint lock is held by the caller (T362: the
        drop must call the write outside _JSONL_CACHE_LOCK, no inversion with the reader's own lock on another thread)."""
        real = em.checkpoint_write
        seen = []
        def wrapped(path, force=False):
            for name in ("_JSONL_CACHE_LOCK", "_CKPT_LOCK"):
                lock = getattr(em, name)
                got = lock.acquire(blocking=False)
                seen.append((name, got))
                if got:
                    lock.release()
            return real(path, force)
        em.checkpoint_write = wrapped
        self.addCleanup(setattr, em, "checkpoint_write", real)
        return seen

    def test_the_quiescence_drop_writes_the_document_from_the_boots_own_read(self):
        """T362: an idle leaf (unchanged past the reader's quiescence window) whose document lacks the judges' pairing. The boot's
        first pass reads it whole; the next quiescent-drop fold over it (the leaf's agent-launch state) writes the document
        BEFORE popping the entry, so every leaf fold is recorded from a read that already happened, no heal on the cycle. The
        entry is gone after; the next boot restores the pairing warm and reads the tail only. The document lacks the launch
        state too (the live shape: both missing from most idle documents), so the fold refolds and the drop pops."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        size = os.path.getsize(self.leaf)
        jd._bg_scan(self.leaf)                                        # the boot's read: whole, the path dirty
        self.assertGreaterEqual(em.read_bytes_report().get(self.leaf, 0), size)
        seen = self._lock_free_write()
        km._agent_launch_state(self.leaf)                             # a drop_after="quiescent" fold over the idle leaf
        self.assertTrue(seen and all(got for _, got in seen), "the drop calls the write holding neither lock: %s" % seen)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "the entry left memory after the write")
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"], "written from the read that already happened")
        self.assertTrue(all("state" in f for f in d["folds"].values()))
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["dropWrites"], cv["dropDeferred"]), (1, 0))
        self.assertEqual(em.checkpoint_converge_candidates(), [], "the pass finds nothing to do")
        read1 = em.read_bytes_report().get(self.leaf, 0)
        km._agent_launch_state(self.leaf)                             # a later drop over the unchanged whole document
        self.assertEqual(self.doc(self.leaf)["seq"], d["seq"], "idempotent: never rewritten at a later drop")
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 1)
        self.assertLess(em.read_bytes_report().get(self.leaf, 0) - read1, size / 2, "and no whole read for it")
        self.fresh_process()
        before = em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0)
        jd._bg_scan(self.leaf)
        self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("bgJudge", 0), before + 1, "the next boot restores the pairing warm")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0), size / 2, "and reads the tail only")

    def test_a_fold_restored_at_the_witness_writes_the_document_and_keeps_the_entry(self):
        """The document carries the launch state but lacks the pairing: the launch fold restores at the witness and steps nothing,
        the path the fold returns from before the pop (the entry stays, as it always has there). The write still runs, from
        the whole entry the judges' pass left, so the pairing converges for this shape too."""
        self._converge_world({"bgJudge": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf)
        seen = self._lock_free_write()
        km._agent_launch_state(self.leaf)
        self.assertTrue(seen and all(got for _, got in seen), "the write is called holding neither lock: %s" % seen)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(self.leaf), "nothing stepped: the entry stays")
        d = self.doc(self.leaf)
        self.assertIn("state", d["folds"].get("bgJudge", {}), "the pairing written from the read that already happened")
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 1)
        km._agent_launch_state(self.leaf)
        self.assertEqual(self.doc(self.leaf)["seq"], d["seq"], "a hit at the witness over a whole document: no write")
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 1)

    def test_the_drop_writes_once_more_after_an_append_and_a_new_quiescence(self):
        self._converge_world({"bgJudge": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); km._agent_launch_state(self.leaf)
        seq = self.doc(self.leaf)["seq"]
        _append(self.leaf, _user("a new prompt", "u_new", "a3", TS0 + 700))
        km._session_meta(self.leaf); km._agent_launch_state(self.leaf)   # fresh again: no drop, no write
        self.assertEqual(self.doc(self.leaf)["seq"], seq, "a changing file is not dropped, so not written here")
        old = time.time() - 600; os.utime(self.leaf, (old, old))       # quiescent once more, its folds stepped since the write
        km._session_meta(self.leaf); km._agent_launch_state(self.leaf)
        self.assertEqual(self.doc(self.leaf)["seq"], seq + 1, "one more write at the next drop")
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 2)

    def test_the_agent_files_gist_drop_writes_its_document_too(self):
        self._converge_world({}, quiescent=True)
        old = time.time() - 600; os.utime(self.agent, (old, old))
        d = self.doc(self.agent); d["folds"].pop("agentGist", None); d["mtime"] = os.stat(self.agent).st_mtime   # the idle file's
        em._ckpt_file(self.agent).write_text(json.dumps(d))                                                     #  document, as written
        self.fresh_process()
        km._agent_steps(self.agent)                                   # a whole read (no document entry), then the drop writes
        self.assertIn("state", self.doc(self.agent)["folds"].get("agentGist", {}))
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 1)

    def test_a_drop_over_the_cycles_byte_budget_is_deferred_and_paid_by_the_next_cycles_start(self):
        """The budget is shared with the converge pass: over it, the write and the drop are deferred (the entry stays, the read
        is not lost), counted. Round one, low 2: the population this serves (a returned subagent's transcript) is never folded
        again, so the drop owed is paid at the next cycle's START with the room that cycle has, oldest first, no fold needed;
        an owed path whose file is gone is forgotten."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        old = time.time() - 600; os.utime(self.agent, (old, old))
        d = self.doc(self.agent); d["folds"].pop("agentGist", None); d["mtime"] = os.stat(self.agent).st_mtime
        em._ckpt_file(self.agent).write_text(json.dumps(d))
        jd._bg_scan(self.leaf)
        saved = km.CKPT_CONVERGE_BYTES; km.CKPT_CONVERGE_BYTES = 1
        self.addCleanup(setattr, km, "CKPT_CONVERGE_BYTES", saved)
        km._begin_checkpoint_cycle()                                  # the cycle begins with a one-byte budget
        km._agent_launch_state(self.leaf); km._agent_steps(self.agent)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(self.leaf), "deferred: the entry is held")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["dropWrites"], cv["dropDeferred"]), (0, 2))
        self.assertEqual(list(em._DROP_OWED), [self.leaf, self.agent], "owed, oldest first")
        km._begin_checkpoint_cycle()                                  # still a one-byte budget: held again, counted again
        self.assertEqual(em.checkpoint_stats()["converge"]["dropDeferred"], 4)
        os.unlink(self.agent)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNotNone(em._JSONL_CACHE.get(self.agent), "the deleted file's entry is still held")
        km.CKPT_CONVERGE_BYTES = saved
        km._begin_checkpoint_cycle()                                  # the next cycle's start pays what is owed: no fold over the file
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["dropWrites"], cv["dropDeferred"]), (1, 4))
        self.assertIn("bgJudge", self.doc(self.leaf)["folds"])
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "written, then popped")
            self.assertIsNone(em._JSONL_CACHE.get(self.agent), "the deleted file's entry released by the payment (round two, low 2)")
        self.assertEqual(list(em._DROP_OWED), [], "paid, and the deleted file's path forgotten")
        import inspect
        src = inspect.getsource(km._document_write_phase)
        self.assertLess(src.index("_begin_checkpoint_cycle()"), src.index("_converge_checkpoints("),
                        "the cycle's budget begins in the document writer's cycle, before the pass that shares it")
        self.assertNotIn("_begin_checkpoint_cycle", inspect.getsource(km._pusher_cycle_jobs), "the pusher's cycle writes no document")
        self.assertNotIn("checkpoint_cycle_begin", inspect.getsource(km._converge_checkpoints), "the pass shares the cycle, it does not begin it")

    # ── the document writer (2026-10-08): a drop another thread meets is the jobs thread's to write ─────────────────────────
    def _writer(self):
        """The document writer's hold, on this thread (the kernel's jobs loop holds it for its life). The base had none, where
        every thread wrote its own drops: the stand-in there is a no-op, so a test below reds on what was written, not on a
        missing name."""
        return getattr(em, "document_writer", contextlib.nullcontext)()

    def _off_the_writer(self, fn):
        """Run `fn` on another thread (the pusher's build, a request handler) and wait for it."""
        out, err = [], []

        def run():
            try:
                out.append(fn())
            except BaseException as e:                         # carried to the test's thread
                err.append(e)
        th = threading.Thread(target=run, name="pusher-probe")
        th.start(); th.join(30)
        self.assertFalse(th.is_alive())
        self.assertEqual(err, [])
        return out[0] if out else None

    def test_a_drop_met_off_the_document_writer_is_handed_to_it_and_done_at_its_write_phase(self):
        """The idle leaf of the drop write above, its document lacking the pairing and the launch state, read whole by the
        boot's judges' pass. The leaf's quiescent-drop fold then runs on ANOTHER thread, a build on the pusher, while this thread
        holds the document writer: no document is written there (a fold document is seconds of encoding for a long leaf, and
        every frame waited on it) and the entry stays, handed over; this thread's write phase writes the document from that
        same read, with no read of its own, and pops the entry, as the drop would have. At the base the fold's own thread
        wrote it."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf)                                        # the boot's whole read: the path dirty, the entry whole
        seq = self.doc(self.leaf)["seq"]
        with self._writer():
            self._off_the_writer(lambda: km._agent_launch_state(self.leaf))
            self.assertEqual(self.doc(self.leaf)["seq"], seq, "no document written off the writer: %s" % sorted(self.doc(self.leaf)["folds"]))
            with em._JSONL_CACHE_LOCK:
                self.assertIsNotNone(em._JSONL_CACHE.get(self.leaf), "the entry stays for the writer to write from")
            cv = em.checkpoint_stats()["converge"]
            self.assertEqual((cv["dropHandedOff"], cv["dropWrites"], cv["dropDeferred"]), (1, 0, 0), "handed over, counted: %s" % cv)
            read0 = em.read_bytes_report().get(self.leaf, 0)
            km._begin_checkpoint_cycle()                              # the writer's write phase opens with the cycle
        d = self.doc(self.leaf)
        self.assertEqual(sorted(d["folds"]), ["agentLaunches", "bgAll", "bgJudge", "bgRunning", "sessionMeta"], "written from the read in hand")
        self.assertTrue(all("state" in f for f in d["folds"].values()))
        self.assertLess(em.read_bytes_report().get(self.leaf, 0) - read0, 512, "no read of records of the writer's own: a guard's bytes")
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "then the entry left memory, as the drop would have let it")
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 1)
        self.assertEqual(em._DROP_HANDOFF, {}, "nothing left handed")

    def test_a_release_off_the_writer_is_done_at_its_write_phase_with_no_wait_for_the_files_quiet(self):
        """A release (a dead lane's leaf, a leaf a /clear retired) is the event that the file's use ended, so it writes and drops
        with no two-minute wait. Met off the writer it is handed over like a quiet drop and done as the release would have
        done it: the leaf here is fresh, and the writer's write phase still writes its document and pops its entry. A hand-off
        paid through the quiet drop's road would have held it for the file's quiet. At the base the release's own thread wrote."""
        self._converge_world({"bgJudge": "missing"})                  # fresh: no quiet drop would take it
        jd._bg_scan(self.leaf)
        seq = self.doc(self.leaf)["seq"]
        with self._writer():
            self.assertEqual(self._off_the_writer(lambda: em.release_records([self.leaf])), 0, "nothing dropped off the writer")
            self.assertEqual(self.doc(self.leaf)["seq"], seq, "and nothing written there")
            km._begin_checkpoint_cycle()
        self.assertIn("state", self.doc(self.leaf)["folds"]["bgJudge"], "written at the write phase")
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "and released, though the file is fresh")
        self.assertEqual(list(em._DROP_OWED), [], "never owed to the file's quiet")

    def test_past_the_stop_no_thread_writes_at_a_drop_and_the_writer_pays_nothing(self):
        """The kernel's exit stand-down reaches the event model (set_document_stop): once it is set no thread starts a document
        write at a drop, the writer's own included, and the writer's write phase pays nothing, so a write never begins beside
        the exit's own checkpoint writes. What was handed stays handed (the process is leaving; the next kernel reads the file
        afresh)."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf)
        seq = self.doc(self.leaf)["seq"]
        leaving = [False]
        prev = em._DOC_STOP[0]
        em.set_document_stop(lambda: leaving[0])
        self.addCleanup(em.set_document_stop, prev)
        with self._writer():
            self._off_the_writer(lambda: km._agent_launch_state(self.leaf))   # handed over while the kernel runs
            leaving[0] = True
            km._agent_launch_state(self.leaf)                         # and met again on the writer itself, past the stop
            km._begin_checkpoint_cycle()
            self.assertEqual(self.doc(self.leaf)["seq"], seq, "no document written past the stop")
            self.assertIn(self.leaf, em._DROP_HANDOFF, "the hand-off stands")
            leaving[0] = False                                        # (a test's undo: a kernel never comes back)
            km._begin_checkpoint_cycle()
        self.assertIn("state", self.doc(self.leaf)["folds"]["bgJudge"])

    def test_one_handed_drop_that_raises_is_its_own_failure_and_the_rest_of_the_hand_offs_are_done(self):
        """The review of PR 155: the writer's turn emptied both hand-off tables first and paid the drops with no try of their
        own, so ONE drop whose write raised lost every drop after it and every rewrite the parses had handed over (a lost drop
        came back only through its file's dirty mark, a lost rewrite only at the next boot's proof and whole parse). Each
        hand-off's failure is now its own: the leaf's write raising here (its carried document unreadable, say) is said once,
        and the agent file's drop handed after it is still written and popped, and the rewrite handed after both is still
        done. The same failure at the next turn is not said again (one episode); after a turn where it pays, a new failure is
        said again. At the base the raise left the agent's document unwritten and the rewrite undone."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        old = time.time() - 600; os.utime(self.agent, (old, old))
        d = self.doc(self.agent); d["folds"].pop("agentGist", None); d["mtime"] = os.stat(self.agent).st_mtime
        em._ckpt_file(self.agent).write_text(json.dumps(d))
        jd._bg_scan(self.leaf)
        real_write = em.checkpoint_write
        broken = [True]

        def write(path, *a, **k):
            if str(path) == self.leaf and broken[0]:
                raise ValueError("the carried document could not be decoded")
            return real_write(path, *a, **k)
        rewrites = []
        real_rewrite = em._asm_write_after_refusal
        em._asm_write_after_refusal = lambda *a, **k: rewrites.append(a[0])
        self.addCleanup(setattr, em, "_asm_write_after_refusal", real_rewrite)
        err = io.StringIO()
        with self._writer(), mock.patch.object(em, "checkpoint_write", write), mock.patch.object(sys, "stderr", err):
            self._off_the_writer(lambda: km._agent_launch_state(self.leaf))   # handed first: the leaf, whose write raises
            self._off_the_writer(lambda: km._agent_steps(self.agent))         # then the agent file
            with em._ASM_CKPT_LOCK:                                           # and a parse's rewrite after a refused document
                em._ASM_REFUSAL_HANDOFF[os.path.realpath(self.leaf)] = (self.leaf, SID, False, None, "unproven", 0, None)
            self.assertEqual(list(em._DROP_HANDOFF), [self.leaf, self.agent])
            km._begin_checkpoint_cycle()
            self.assertIn("state", self.doc(self.agent)["folds"].get("agentGist", {}), "the agent's drop, handed after it, written")
            with em._JSONL_CACHE_LOCK:
                self.assertIsNone(em._JSONL_CACHE.get(self.agent), "and popped")
                self.assertIsNotNone(em._JSONL_CACHE.get(self.leaf), "the leaf's entry stays: its drop raised before any pop")
            self.assertEqual(rewrites, [self.leaf], "the rewrite handed after them done")
            said = [ln for ln in err.getvalue().splitlines() if self.leaf in ln and "decoded" in ln]
            self.assertEqual(len(said), 1, "the raise said once, naming the file and the fault: %r" % err.getvalue())
            self.assertIn(self.leaf, em.checkpoint_dirty(), "its dirty mark stands, for the converge pass's own road")
            self._off_the_writer(lambda: km._agent_launch_state(self.leaf))   # handed again, raising again: the same episode
            km._begin_checkpoint_cycle()
            self.assertEqual(len([ln for ln in err.getvalue().splitlines() if self.leaf in ln]), 1, "not said again: %r" % err.getvalue())
            broken[0] = False
            self._off_the_writer(lambda: km._agent_launch_state(self.leaf))   # it pays: the episode ends
            km._begin_checkpoint_cycle()
            self.assertIn("state", self.doc(self.leaf)["folds"].get("bgJudge", {}))
        self.assertEqual(em._DROP_HANDOFF, {}, "nothing left handed")

    def test_the_owed_tables_overflow_pops_the_oldest_owed_entry(self):
        """Round two, low 1: over the bound the table was cleared, leaving those entries neither paid nor popped. The oldest owed
        drop is paid by its pop alone instead."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        old = time.time() - 600; os.utime(self.agent, (old, old))
        d = self.doc(self.agent); d["folds"].pop("agentGist", None); d["mtime"] = os.stat(self.agent).st_mtime
        em._ckpt_file(self.agent).write_text(json.dumps(d))
        jd._bg_scan(self.leaf)
        saved = (km.CKPT_CONVERGE_BYTES, em._DROP_OWED_MAX); km.CKPT_CONVERGE_BYTES = 1; em._DROP_OWED_MAX = 1
        self.addCleanup(setattr, km, "CKPT_CONVERGE_BYTES", saved[0]); self.addCleanup(setattr, em, "_DROP_OWED_MAX", saved[1])
        km._begin_checkpoint_cycle()
        km._agent_launch_state(self.leaf)                             # owed: the leaf
        km._agent_steps(self.agent)                                   # owed: the agent file; the table overflows, the leaf is popped
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "the oldest owed entry released")
            self.assertIsNotNone(em._JSONL_CACHE.get(self.agent), "the newest still owed and held")
        self.assertEqual(list(em._DROP_OWED), [self.agent])
        self.assertEqual(em.checkpoint_stats()["converge"]["dropWrites"], 0, "unwritten: the budget was gone")

    def test_the_knobs_reach_the_drop_write_before_the_first_cycle(self):
        """Round two, low 3: before the first pusher cycle the drop write was on whatever the knobs said. The event model reads
        the knobs, and the kernel's constants are those reads."""
        self.assertEqual(km.CKPT_CONVERGE_MS, em._CKPT_CONVERGE_MS_DEFAULT)
        saved = em._CKPT_CONVERGE_MS_DEFAULT; em._CKPT_CONVERGE_MS_DEFAULT = 0.0
        self.addCleanup(setattr, em, "_CKPT_CONVERGE_MS_DEFAULT", saved)
        self.fresh_process()                                          # a process that has begun no cycle
        self.assertFalse(em.checkpoint_drop_writes_on(), "the pass off: the drop write off before any cycle")
        em._CKPT_CONVERGE_MS_DEFAULT = saved; self.fresh_process()
        self.assertTrue(em.checkpoint_drop_writes_on())

    def test_the_cycle_budget_stands_before_the_first_cycle_begins(self):
        """Round one, low 3: the boot's first cycle charged no budget (no cap until the pass, near the cycle's end, began one), so
        the drops this change exists for were uncapped where the volume is. The default cap stands from import and after a
        fresh process, and the kernel's knob is that default."""
        self.assertEqual(em._CKPT_CYCLE_CAP_DEFAULT, km.CKPT_CONVERGE_BYTES)
        self.fresh_process()
        self.assertTrue(em.checkpoint_cycle_take(1)); self.assertFalse(em.checkpoint_cycle_take(em._CKPT_CYCLE_CAP_DEFAULT))

    def test_the_pass_off_switch_covers_the_drop_write(self):
        """Round one, low 4: ROMP_CKPT_CONVERGE_MS=0 stopped the pass and not the drop write. Off, the drop pops as before T362:
        no write, no deferral, no held entry."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); seq = self.doc(self.leaf)["seq"]
        saved = km.CKPT_CONVERGE_MS; km.CKPT_CONVERGE_MS = 0.0
        self.addCleanup(setattr, km, "CKPT_CONVERGE_MS", saved)
        km._begin_checkpoint_cycle(); km._agent_launch_state(self.leaf)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "popped, not held")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["dropWrites"], cv["dropDeferred"], self.doc(self.leaf)["seq"]), (0, 0, seq))

    def test_a_zero_byte_budget_pops_without_holding(self):
        """Round one, low 4: ROMP_CKPT_CONVERGE_MB=0 deferred every drop and HELD the entries, worse than a no-op."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); seq = self.doc(self.leaf)["seq"]
        saved = km.CKPT_CONVERGE_BYTES; km.CKPT_CONVERGE_BYTES = 0
        self.addCleanup(setattr, km, "CKPT_CONVERGE_BYTES", saved)
        km._begin_checkpoint_cycle(); km._agent_launch_state(self.leaf)
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf), "popped, not held")
        cv = em.checkpoint_stats()["converge"]
        self.assertEqual((cv["dropWrites"], cv["dropDeferred"], self.doc(self.leaf)["seq"]), (0, 0, seq))

    def test_a_stateless_document_cursor_never_replaces_a_held_state_after_the_drop(self):
        """Round one, medium: after the drop popped the entry, the next fold's stale-generation restore took the document's cursor
        even without a state (an over-cap, cold or bare legacy write), so a complete state this process held was replaced by a
        tail-only one that answered empty, forever (the pass refuses a quiescent leaf, an idle session never settles for the
        heal; 9 of 481 live documents carry such a cursor). A stateless document cursor against a held state is refused: the
        fold reads whole and answers what it held, as before."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf)
        held = km._agent_launch_state(self.leaf)                      # the refold's answer; the drop writes and pops
        self.assertTrue(held["launched"], "the fixture's leaf holds a launch")
        d = self.doc(self.leaf); d["folds"]["agentLaunches"] = {"count": d["folds"]["agentLaunches"]["count"]}   # a bare legacy cursor
        em._ckpt_file(self.leaf).write_text(json.dumps(d))
        with em._JSONL_CACHE_LOCK:
            self.assertIsNone(em._JSONL_CACHE.get(self.leaf))
        read0 = em.read_bytes_report().get(self.leaf, 0); size = os.path.getsize(self.leaf)
        again = km._agent_launch_state(self.leaf)
        self.assertEqual(again["launched"], held["launched"], "the held launch, not an empty tail-only state")
        self.assertGreaterEqual(em.read_bytes_report().get(self.leaf, 0) - read0, size, "read whole, as the base did")
        self.assertEqual(km._agent_launch_state(self.leaf)["launched"], held["launched"], "and it stays")

    def test_the_post_drop_fold_answers_from_the_document_over_a_tail_read(self):
        """Round one, low 5: the fold after a drop takes its cursor from the DOCUMENT (a state planted there is the answer) over a
        tail read, and writes nothing."""
        self._converge_world({"bgJudge": "missing", "agentLaunches": "missing"}, quiescent=True)
        jd._bg_scan(self.leaf); km._agent_launch_state(self.leaf)   # written, popped
        d = self.doc(self.leaf)
        d["folds"]["agentLaunches"]["state"] = em._ckpt_encode({"launched": {"planted": {"t": 1}}, "settled": set()})
        em._ckpt_file(self.leaf).write_text(json.dumps(d))
        read0 = em.read_bytes_report().get(self.leaf, 0); size = os.path.getsize(self.leaf)
        self.assertEqual(km._agent_launch_state(self.leaf)["launched"], {"planted": {"t": 1}}, "the document's cursor is the fold's answer")
        self.assertLess(em.read_bytes_report().get(self.leaf, 0) - read0, size / 2, "over a tail read")
        self.assertEqual((em.checkpoint_stats()["converge"]["dropWrites"], self.doc(self.leaf)["seq"]), (1, d["seq"]), "and no rewrite")

    def test_a_cold_fold_at_the_drop_is_not_rewritten_every_fold(self):
        """Round one, low 1: the rule stayed true forever for a fold cold for want of a state, so a quiescent file whose document
        held the dropping fold's cursor without a state was rewritten at every fold (seq 2, 3, 4). A write that cannot improve
        the document (the cold fold is written cold again) is not needed; the fold is left for the heal."""
        self._converge_world({"agentLaunches": "bare"}, quiescent=True)
        km._session_meta(self.leaf)                                   # the boot: the tail
        seq = self.doc(self.leaf)["seq"]
        for k in range(3):
            km._begin_checkpoint_cycle(); km._agent_launch_state(self.leaf); km._converge_checkpoints(TS0 + 600 + k)
        self.assertEqual(em.cold_fold_reasons(self.leaf), {"agentLaunches": "cold"}, "restored cold at the cut, left for the heal")
        self.assertEqual((self.doc(self.leaf)["seq"], em.checkpoint_stats()["converge"]["dropWrites"]), (seq, 0),
                         "nothing to improve on the document: no write")

    def test_one_rule_for_the_writer_the_carry_the_candidates_and_the_drop(self):
        import inspect
        self.assertIn("_cursor_recordable(", inspect.getsource(em.checkpoint_write))
        self.assertIn("_count_recordable(", inspect.getsource(em._carry_forward_states))
        self.assertIn("_path_needs_write(", inspect.getsource(em.checkpoint_converge_candidates))
        self.assertIn("_path_needs_write(", inspect.getsource(em._write_then_drop))
        self.assertIn("_cursor_recordable(", inspect.getsource(em._path_needs_write))
        for caller in (em._drop_quiescent_entry, em.release_records):   # the quiet drop and the release at the end of a file's use
            self.assertIn("_write_then_drop(", inspect.getsource(caller), "%s takes the drop's one write half" % caller.__name__)
        src = inspect.getsource(em._write_then_drop)
        self.assertLess(src.index("checkpoint_write("), src.index("with _JSONL_CACHE_LOCK"), "the write before the reader's lock")
        self.assertIn("checkpoint_cycle_take(", src); self.assertNotIn("checkpoint_cycle_room(", src)   # the room check and the charge are one step

    def test_perf_carries_the_checkpoint_counters_and_the_kernel_wires_the_three_events(self):
        snap = km._PERF_STATS.snapshot()
        self.assertIn("converge", em._CKPT_STATS, "the production default carries the converge counters (the fixture injects nothing)")
        self.assertEqual(sorted(snap["checkpoints"]), ["coldFolds", "coldWrites", "converge", "dirty", "docConsults", "docMemo", "documentBytes", "droppedRestores", "fallbacks", "oversizeFolds",
                                                        "readByPath", "readBytes", "readPaths", "refolds", "restored", "restoredFolds", "rewoundMemo", "skippedFolds", "swept", "writes"])
        src = open(os.path.join(BIN, "romp-kernel")).read()
        self.assertIn("em.checkpoint_write_dirty(paths=_ckpt_order, budget_s=max(0.0, _write_end - time.monotonic()))", src,
                      "exit writes the dirty checkpoints in _drain_and_exit, bounded (2026-09-11: unbounded, it met the manager's SIGKILL; "
                      "2026-10-05: by its cap and the exit's pre-drain line, the leaves it primed first)")
        self.assertIn("_persist_checkpoints(now)", src)
        self.assertIn("em.checkpoint_sweep()", src)



class ReviewProbes(Base):
    """T359 review (2026-09-12): the cut's guard, the over-cap reason through a cold write, the heal of any file's fold.
    GenericFold's helpers over a plain JSONL file, without inheriting its tests."""
    _step = staticmethod(GenericFold._step)
    _noop_init = staticmethod(GenericFold._noop_init)
    fold = GenericFold.fold

    def setUp(self):
        super().setUp()
        self.p = str(self.td / "t.jsonl")
        self.cache = {}
        self.kinds = []
    def test_a_leaf_rewritten_whole_between_the_read_and_the_write_gets_no_cut_document(self):
        """Medium 1: the cut path read fresh guard bytes at write time; a leaf rewritten whole (and larger) between the entry's
        read and the write paired the entry's counts and states with the new file's bytes, and the next boot restored every
        fold onto a prefix that no longer existed. The read is gated on the entry's stat: a changed file writes the witness
        form, which the rewrite then fails to verify, as the merge-base did."""
        _write(self.p, [{"n": i} for i in range(12)])
        lo = {}
        em.fold_records(lo, self.p, list, self._step, ckpt="lo")          # lo at 12
        _append(self.p, {"n": 12}, {"n": 13})
        self.fold()                                                        # "t" at 14, lo lags at 12
        os.utime(self.p, (0, 0)); time.sleep(0.01)
        _write(self.p, [{"n": 1000 + i, "pad": "y" * 8} for i in range(20)])   # rewritten whole, larger, another mtime
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual(d["count"], 14, "no cut move: the witness form")
        self.assertEqual(sorted(d["folds"]), ["t"], "the lagging fold is left out of this write")
        self.fresh_process()
        got = em.fold_records({}, self.p, list, self._step, on=self.kinds.append, ckpt="t")
        self.assertEqual(got, [1000 + i for i in range(20)], "the next process folds the file as it is")
        self.assertEqual(self.kinds[-1], "refold"); self.assertTrue(em.checkpoint_stats()["fallbacks"], "the document was refused")

    def test_an_append_between_the_read_and_the_write_still_carries_the_lagging_fold(self):
        """Fold review: a stat gate on the cut's guard refused a plain APPEND between the entry's read and the write (a states log,
        the postal log, a peer advancing a leaf), so the lagging fold fell out and the next boot refolded it whole. The entry's
        own witness guard is verified instead: an append leaves that prefix intact, so the cut move proceeds."""
        _write(self.p, [{"n": 0}, {"n": 1}])
        lo = {}
        em.fold_records(lo, self.p, list, self._step, ckpt="lo")          # lo at 2
        _append(self.p, {"n": 2})
        self.fold()                                                        # "t" at 3; lo lags at 2
        _append(self.p, {"n": 3})                                          # an append the entry has not read yet
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual((sorted(d["folds"]), d["count"], d["folds"]["lo"]["count"]), (["lo", "t"], 2, 2), "the cut moved: the lagging fold is carried")
        self.fresh_process()
        got = em.fold_records({}, self.p, list, self._step, on=self.kinds.append, ckpt="lo")
        self.assertEqual((got, self.kinds[-1]), ([0, 1, 2, 3], "restore"), "the next process restores it warm over the tail")
        self.assertEqual(em.checkpoint_stats()["fallbacks"], {})

    def test_a_rewrite_keeping_size_and_mtime_between_the_read_and_the_write_gets_no_cut_document(self):
        """Fold review: a rewrite that keeps the size AND the modification time (a copy preserving times, a coarse timestamp) passed
        a stat gate and paired the entry's counts with the new file's bytes. The entry's witness guard catches it: the bytes
        before its offset changed, so the write is the witness form, which the next process refuses."""
        _write(self.p, [{"n": i} for i in range(12)])
        lo = {}
        em.fold_records(lo, self.p, list, self._step, ckpt="lo")          # lo at 12
        _append(self.p, {"n": 12}, {"n": 13})
        self.fold()                                                        # "t" at 14, lo lags at 12
        st = os.stat(self.p)
        _write(self.p, [{"n": 9 - i} for i in range(10)] + [{"n": 23 - i} for i in range(10, 14)])   # the same byte length, other content
        os.utime(self.p, ns=(st.st_atime_ns, st.st_mtime_ns))              # ...and the same modification time
        self.assertEqual((os.stat(self.p).st_size, os.stat(self.p).st_mtime_ns), (st.st_size, st.st_mtime_ns))
        self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p)
        self.assertEqual((d["count"], sorted(d["folds"])), (14, ["t"]), "the witness form, the lagging fold left out")
        self.assertIn(self.p, em.checkpoint_dirty(), "...and the path stays dirty, so the next write (a settle, the exit drain) tries it again")
        self.fresh_process()
        got = em.fold_records({}, self.p, list, self._step, on=self.kinds.append, ckpt="t")
        self.assertEqual((self.kinds[-1], len(got)), ("refold", 14), "the next process refuses the document and folds the file as it is")
        self.assertTrue(em.checkpoint_stats()["fallbacks"])

    def test_an_over_cap_fold_stays_over_through_a_cold_write_across_three_boots(self):
        """Medium 2: a fold that restored cold because its state was over the cap was written {count, cold} at the next write,
        so the following boot promised a heal and refolded it WHOLE (the unbounded read), then wrote it over again: alternating
        boots. The reason and its KB carry through the cold write."""
        saved_cap = em._CKPT_FOLD_CAP; em._CKPT_FOLD_CAP = 64 * 1024
        self.addCleanup(setattr, em, "_CKPT_FOLD_CAP", saved_cap)
        _write(self.p, [{"n": i, "pad": "x" * 400} for i in range(400)])
        big = {}
        em.fold_records(big, self.p, list, lambda st, o: st + [o], ckpt="big")
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        kb = self.doc(self.p)["folds"]["big"]["over"]; self.assertGreater(kb, 64)
        size = os.path.getsize(self.p)
        for boot in (2, 3):
            self.fresh_process(); big.clear()
            em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
            self.assertEqual(self.kinds[-1], "cold", "boot %d: cold" % boot)
            self.assertEqual(em.cold_fold_reasons(self.p), {"big": "over"}, "boot %d: for the cap's reason" % boot)
            self.assertEqual(em.drop_cold_cursors(self.p), [], "boot %d: nothing to heal: over stays over" % boot)
            _append(self.p, {"n": 400 + boot})
            em.fold_records(big, self.p, self.__class__._noop_init, lambda st, o: st + [o], on=self.kinds.append, ckpt="big")
            self.assertTrue(em.checkpoint_write(self.p, force=True))
            self.assertEqual(self.doc(self.p)["folds"]["big"], {"count": 400 + boot - 1, "over": kb}, "boot %d: the cold write carries the reason and its KB" % boot)
            self.assertLess(em.read_bytes_report().get(self.p, 0), size / 2, "boot %d: no whole read" % boot)

    def test_any_files_tail_only_fold_heals_by_a_cursor_drop_before_the_write(self):
        """Low 3: the heal walked the five leaf folds only; another file's fold with a cursor-only document said the line every
        boot and never healed. em.drop_cold_cursors drops such cursors for any file: the write leaves them out and the next run
        of the fold reads the file whole once, complete again."""
        _write(self.p, [{"n": i} for i in range(6)])
        self.fold(); self.assertTrue(em.checkpoint_write(self.p))
        d = self.doc(self.p); d["folds"]["t"] = {"count": 6}                # an older kernel's cursor-only entry
        em._ckpt_file(self.p).write_text(json.dumps(d))
        self.fresh_process()
        self.assertEqual(self.fold(), []); self.assertEqual(self.kinds[-1], "cold")
        self.assertEqual(em.drop_cold_cursors(self.p), ["t"])
        self.assertTrue(em.checkpoint_write(self.p, force=True))
        self.assertNotIn("t", self.doc(self.p)["folds"], "left out of the write")
        self.assertEqual(self.fold(), list(range(6))); self.assertEqual(self.kinds[-1], "refold", "the next run reads the file whole once")
        self.assertEqual(em.cold_fold_reasons(self.p), {}, "complete again")
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertIn("state", self.doc(self.p)["folds"]["t"], "and written whole")


class CarryReadsOncePerChange(Base):
    """A write's carry (the document's states for folds this process never ran, _carry_forward_states) loaded the whole document
    back at EVERY write, since the write dropped from the memo the very document it had just written: a live kernel's document
    reads were mostly a write re-reading what the same process wrote one write earlier, and for a live leaf, whose every fold
    the settle runs, only to learn there was nothing to carry. The carry now skips the read when this process already knows
    the document has nothing to give it (_DOC_INDEX: what each document can be carried from, keyed by the file's inode, mtime
    and size), and a written document that carried an entry only the disk had (a state or an over-the-cap cursor) stays in the
    memo for the next write. Every document written keeps its bytes. GenericFold's step over a plain JSONL file of {"n": i}
    records, plus a padding fold whose state is a string and, in one test, a fold whose state passes the size cap."""
    _step = staticmethod(GenericFold._step)
    cold = GenericFold.cold

    def setUp(self):
        super().setUp()
        self.p = str(self.td / "t.jsonl")
        self.caches = {}
        _write(self.p, [{"n": i} for i in range(3)])

    def fold(self, name, path=None):
        return em.fold_records(self.caches.setdefault(name, {}), path or self.p, list, self._step, ckpt=name)

    def fold_pad(self, path=None):
        return em.fold_records(self.caches.setdefault("pad", {}), path or self.p, str, lambda st, o: st + "x" * 64, ckpt="pad")

    def cp(self, path=None):
        return em._ckpt_file(path or self.p)

    def doc_reads(self, path=None):
        return em.read_bytes_report().get(str(self.cp(path)), 0)

    def consults(self):
        return em.checkpoint_stats()["docConsults"]

    def memo(self, path=None):
        with em._CKPT_LOCK:
            hit = em._DOC_MEMO.get(path or self.p)
        return None if hit is None else hit[1]

    def states(self, path=None):
        return {n: (f["count"], em._ckpt_decode(f["state"])) for n, f in self.doc(path or self.p)["folds"].items() if "state" in f}

    def carrying_process(self, path=None):
        """Process one folds t and u and writes; process two (a fresh process) folds only t over an append, restoring from the
        document (its one read), and writes, carrying u. Returns the first document's size; self.n_boot is the load count at
        process two's start (the counter is the module's, never reset)."""
        p = path or self.p
        self.fold("t", p); self.fold("u", p)
        self.assertTrue(em.checkpoint_write(p))
        size = self.cp(p).stat().st_size
        self.fresh_process()
        self.n_boot = self.consults()
        _append(p, {"n": 3})
        self.assertEqual(self.fold("t", p), [0, 1, 2, 3])
        self.assertTrue(em.checkpoint_write(p))
        self.assertEqual(self.states(p)["u"], (3, [0, 1, 2]), "the write carries the fold this process never ran")
        return size

    def test_a_second_write_reads_no_document_when_every_fold_it_holds_is_current(self):
        self.fold("t"); self.fold("u")
        self.assertTrue(em.checkpoint_write(self.p))                     # the first write: no document yet
        _append(self.p, {"n": 3})
        self.fold("t"); self.fold("u")
        reads, n0 = self.doc_reads(), self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual((self.doc_reads() - reads, self.consults() - n0), (0, 0),
                         "every fold the document holds is one this write holds: nothing to carry, no document byte read")
        self.assertEqual(self.states(), {"t": (4, [0, 1, 2, 3]), "u": (4, [0, 1, 2, 3])})
        self.assertEqual(self.states()["t"][1], self.cold(), "the document holds what a cold fold gives")
        self.assertIsNone(self.memo(), "a document of this process's own states is not kept in memory")

    def test_a_fold_this_process_never_ran_is_carried_from_memory_at_every_write(self):
        size = self.carrying_process()
        self.assertEqual((self.doc_reads(), self.consults() - self.n_boot), (size, 1),
                         "the restore read the document once and the first write's carry shared that read")
        for k in range(2):                                               # two more appends, folds and writes
            _append(self.p, {"n": 4 + k})
            self.assertEqual(self.fold("t"), list(range(5 + k)))
            self.assertTrue(em.checkpoint_write(self.p))
            self.assertEqual(self.states(), {"t": (5 + k, list(range(5 + k))), "u": (3, [0, 1, 2])},
                             "write %d carries u with process one's count and state" % (k + 2))
            self.assertEqual(self.memo(), json.loads(self.cp().read_text()), "the document kept in memory is the one on disk")
        self.assertEqual((self.doc_reads(), self.consults() - self.n_boot), (size, 1), "one document read and one load in the whole sequence")

    def test_a_needed_carry_the_memory_no_longer_holds_reads_the_document_and_carries(self):
        q = str(self.td / "q.jsonl")
        _write(q, [{"n": i} for i in range(3)])
        self.fold("t", q); self.fold("u", q); self.assertTrue(em.checkpoint_write(q))   # process one's other file
        self.carrying_process()                                          # (its fresh process forgets q's cursors too)
        saved = em._DOC_MEMO_CAP; em._DOC_MEMO_CAP = 1                   # room for one document: the newest
        self.addCleanup(setattr, em, "_DOC_MEMO_CAP", saved)
        _append(q, {"n": 3}); self.fold("t", q)                          # q's restore loads q's document: p's leaves the memory
        self.assertIsNone(self.memo())
        _append(self.p, {"n": 4}); self.fold("t")
        size, reads, n0 = self.cp().stat().st_size, self.doc_reads(), self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual((self.doc_reads() - reads, self.consults() - n0), (size, 1), "the carry needs u, so it reads the document")
        self.assertEqual(self.states()["u"], (3, [0, 1, 2]), "and carries it")

    def rewrite_as_another_writer(self, add, in_place):
        """Rewrite the document as another writer would, adding the folds `add` at the file's own size (the padding fold's
        string shortened, trailing spaces after): by a tmp file and a rename (a new inode) under the old modification time,
        or in place (the same inode) under a new one."""
        cp = self.cp(); st = cp.stat()
        d = json.loads(cp.read_text()); d["folds"].update(add)
        over = len(json.dumps(d, separators=(",", ":"))) - st.st_size
        if over > 0:
            self.assertGreater(len(d["folds"]["pad"]["state"]), over)
            d["folds"]["pad"]["state"] = d["folds"]["pad"]["state"][:-over]
        text = json.dumps(d, separators=(",", ":"))
        text += " " * (st.st_size - len(text))
        if in_place:
            cp.write_text(text)
            os.utime(cp, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        else:
            tmp = cp.with_name(cp.name + ".other.tmp")
            tmp.write_text(text)
            os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(tmp, cp)
        st2 = cp.stat()
        self.assertEqual(st2.st_size, st.st_size)
        if in_place:
            self.assertEqual(st2.st_ino, st.st_ino); self.assertNotEqual(st2.st_mtime_ns, st.st_mtime_ns)
        else:
            self.assertNotEqual(st2.st_ino, st.st_ino); self.assertEqual(st2.st_mtime_ns, st.st_mtime_ns)

    def test_a_document_another_writer_replaced_is_read_again_at_the_same_size(self):
        """Over a document of this process's own states (the index's case): a rename at the same size and modification time,
        and an in-place rewrite at the same size under a new time, each read and carried at the next write."""
        for in_place in (False, True):
            with self.subTest(in_place=in_place):
                self.p = str(self.td / ("own-%d.jsonl" % in_place))
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process()
                self.fold("t"); self.fold_pad()
                self.assertTrue(em.checkpoint_write(self.p))
                self.rewrite_as_another_writer({"v": {"count": 3, "state": em._ckpt_encode([7])}}, in_place)
                _append(self.p, {"n": 3}); self.fold("t"); self.fold_pad()
                n0 = self.consults()
                self.assertTrue(em.checkpoint_write(self.p))
                self.assertEqual(self.consults() - n0, 1, "the other writer's document is read")
                self.assertEqual(self.states().get("v"), (3, [7]), "and its fold carried")

    def test_a_document_another_writer_replaced_under_a_kept_one_is_read_again_at_the_same_size(self):
        """The same over a document a carrying write kept in memory (the memo's case)."""
        for in_place in (False, True):
            with self.subTest(in_place=in_place):
                self.p = str(self.td / ("kept-%d.jsonl" % in_place))
                _write(self.p, [{"n": i} for i in range(3)])
                self.fresh_process()
                self.fold("t"); self.fold("u"); self.fold_pad()
                self.assertTrue(em.checkpoint_write(self.p))
                self.fresh_process()
                _append(self.p, {"n": 3}); self.fold("t"); self.fold_pad()
                self.assertTrue(em.checkpoint_write(self.p))
                self.assertIsNotNone(self.memo(), "the carrying write kept its document")
                self.rewrite_as_another_writer({"v": {"count": 3, "state": em._ckpt_encode([7])}}, in_place)
                _append(self.p, {"n": 4}); self.fold("t"); self.fold_pad()
                n0 = self.consults()
                self.assertTrue(em.checkpoint_write(self.p))
                self.assertEqual(self.consults() - n0, 1, "the other writer's document is read, not the kept one served")
                self.assertEqual((self.states().get("u"), self.states().get("v")), ((3, [0, 1, 2]), (3, [7])), "both carried")

    def test_a_retirement_after_a_carrying_write_leaves_the_fold_and_the_memory(self):
        self.carrying_process()
        em._retire_fold(self.p, "u")
        _append(self.p, {"n": 4}); self.fold("t")
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertNotIn("u", self.doc(self.p)["folds"], "the retired fold is left out after the carry")
        self.assertIsNone(self.memo(), "nothing the write carried survived the retirement: the document is not kept")
        _append(self.p, {"n": 5}); self.fold("t")
        reads, n0 = self.doc_reads(), self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual((self.doc_reads() - reads, self.consults() - n0), (0, 0), "and the next write reads nothing")
        self.assertEqual(self.states(), {"t": (6, list(range(6)))})

    def test_a_document_spoiled_between_writes_still_falls_back(self):
        self.fold("t"); self.assertTrue(em.checkpoint_write(self.p))
        self.cp().write_text("{not json")
        _append(self.p, {"n": 3}); self.fold("t")
        n0 = self.consults()
        self.assertTrue(em.checkpoint_write(self.p), "the write succeeds over it")
        self.assertEqual((em.checkpoint_stats()["fallbacks"], self.consults() - n0), ({"corrupt": 1}, 1), "read, refused and counted")
        self.assertEqual(self.states(), {"t": (4, [0, 1, 2, 3])})
        with em._CKPT_LOCK:
            self.assertEqual(em._DOC_INDEX[self.p][0], em._doc_sig(self.cp().stat()), "the index describes the new document")

    def test_an_over_the_cap_cursor_of_a_fold_this_process_never_ran_is_carried(self):
        """The index lists an over-the-cap cursor (no state, only its count) as carryable, as the carry does: left out, the
        restore's load would tell the index the document has nothing to give, the write would drop the cursor, and the next
        boot would fold that fold over the whole file again."""
        saved = em._CKPT_FOLD_CAP; em._CKPT_FOLD_CAP = 64
        self.addCleanup(setattr, em, "_CKPT_FOLD_CAP", saved)
        self.fold("t"); em.fold_records(self.caches.setdefault("big", {}), self.p, list, lambda st, o: st + ["y" * 100], ckpt="big")
        self.assertTrue(em.checkpoint_write(self.p))
        big = self.doc(self.p)["folds"]["big"]
        self.assertEqual((big.get("count"), "state" in big, bool(big.get("over"))), (3, False, True), "the premise: an over-the-cap cursor")
        self.fresh_process()
        for k in range(2):
            _append(self.p, {"n": 3 + k})
            self.assertEqual(self.fold("t"), list(range(4 + k)))
            self.assertTrue(em.checkpoint_write(self.p))
            self.assertEqual(self.doc(self.p)["folds"].get("big"), big, "write %d carries the over-the-cap cursor" % (k + 1))

    def test_a_write_after_a_restore_whose_kept_document_is_gone_reads_nothing_when_it_holds_every_fold(self):
        """The restore's validated load fills the index, so the write after it reads nothing even when the memo has let the
        document go (as its byte cap does when many documents restore at boot)."""
        self.fold("t"); self.fold("u"); self.assertTrue(em.checkpoint_write(self.p))
        self.fresh_process()
        with em._CKPT_LOCK:
            self.assertNotIn(self.p, em._DOC_INDEX, "the premise: the rebind forgot what the first write told the index")
        _append(self.p, {"n": 3})
        self.assertEqual((self.fold("t"), self.fold("u")), ([0, 1, 2, 3], [0, 1, 2, 3]))
        with em._CKPT_LOCK:
            em._doc_memo_drop(self.p)                    # as the byte cap evicts it when many documents restore at boot
        reads, n0 = self.doc_reads(), self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual((self.doc_reads() - reads, self.consults() - n0), (0, 0), "the restore's load filled the index: no read")

    def test_a_fold_this_process_never_ran_too_far_behind_to_carry_costs_no_read(self):
        """An entry at a count the write cannot record (here past the lag bound) is never carried; the index applies the
        carry's count rule too, so learning that costs no read."""
        self.fold("t"); self.fold("u"); self.assertTrue(em.checkpoint_write(self.p))
        self.fresh_process()
        with em._CKPT_LOCK:
            self.assertNotIn(self.p, em._DOC_INDEX, "the premise: the rebind forgot what the first write told the index")
        _append(self.p, *[{"n": 3 + i} for i in range(200)])
        self.assertEqual(self.fold("t"), list(range(203)))
        with em._CKPT_LOCK:
            em._doc_memo_drop(self.p)
        reads, n0 = self.doc_reads(), self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual(sorted(self.doc(self.p)["folds"]), ["t"], "u stands too far behind to carry, as the carry has always left it")
        self.assertEqual((self.doc_reads() - reads, self.consults() - n0), (0, 0), "and learning that costs no read")

    def test_an_entry_whose_count_is_not_an_int_is_one_no_write_carries(self):
        """A count json reads as float infinity (`Infinity`, or `1e999`) is one int() refuses with OverflowError, not
        ValueError; "3" and 3.0 are counts int() accepts but that are not ints, and the carry takes an entry as it stands, so
        the cut met them as they were (min() of "3" and an int, an entry's offsets indexed by 3.0). The index and the carry take
        only an int count: such an entry is one nothing carries, so the load that fills the index (a retirement's consult in
        a fresh process, the restore of another entry) and the write return, and the write leaves that entry out. Before, an
        infinite count raised at that load, on the first read of the transcript in each process, and at the carry at every
        write; "3" and 3.0 raised at every write before its rename, so the document was never rewritten. No writer produces
        such a count; it takes a hand edit."""
        for i, bad in enumerate((float("inf"), "3", 3.0)):
            with self.subTest(count=bad):
                self.p = str(self.td / ("count-%d.jsonl" % i))
                _write(self.p, [{"n": k} for k in range(3)])
                self.fresh_process()
                em._CKPT_STATS["restoredFolds"] = {}
                self.fold("t"); self.fold("u"); self.fold("v"); self.assertTrue(em.checkpoint_write(self.p))
                cp = self.cp(); d = json.loads(cp.read_text()); d["folds"]["u"]["count"] = bad
                cp.write_text(json.dumps(d))
                self.assertEqual(json.loads(cp.read_text())["folds"]["u"]["count"], bad, "the premise: json writes and reads it")
                self.fresh_process()
                n0 = self.consults()
                em.rewound_memo_forget(self.p)           # a retirement's consult before any load in the process: it asks the disk
                self.assertEqual(self.consults() - n0, 1, "the consult loaded the document")
                self.assertNotIn(self.p, em._RETIRED_FOLDS, "and found nothing to retire")
                self.fresh_process()
                _append(self.p, {"n": 3})
                self.assertEqual(self.fold("t"), [0, 1, 2, 3])
                self.assertEqual(em.checkpoint_stats()["restoredFolds"].get("t"), 1, "the restore of another entry")
                with em._CKPT_LOCK:
                    indexed = em._DOC_INDEX[self.p][1].get("u", "absent")   # the restore's load filled the index; the write refills it
                self.assertTrue(em.checkpoint_write(self.p), "the write proceeds")
                self.assertEqual(self.states(), {"t": (4, [0, 1, 2, 3]), "v": (3, [0, 1, 2])}, "carrying v")
                self.assertNotIn("u", self.doc(self.p)["folds"], "and leaving out the entry with no int count")
                self.assertIsNone(indexed, "the index listed that entry with no count, as the carry reads it")

    def test_a_document_whose_count_seq_or_mtime_does_not_read_falls_back_at_the_restore(self):
        """The validated load reads the document's top-level seq and mtime as the restore does, and a number json reads as
        infinity (OverflowError) is a corrupt document there like any other bad shape: counted under fallbacks, removed, and
        the file read whole, as a bad offset always was. So is a fold's own count at infinity at that fold's restore. Before,
        each raised out of the restore, the top-level count also out of a retirement's consult, and nothing counted or
        removed the document, so every process raised again. No writer produces such a document; it takes a hand edit."""
        def spoil(d, field):
            if field == "fold":
                d["folds"]["u"]["count"] = float("inf")
            else:
                d[field] = "x" if field == "mtime" else float("inf")
        for field in ("count", "seq", "mtime", "fold"):
            with self.subTest(field=field):
                self.p = str(self.td / ("top-%s.jsonl" % field))
                _write(self.p, [{"n": k} for k in range(3)])
                self.fresh_process()
                em._CKPT_STATS["fallbacks"] = {}
                self.fold("t"); self.fold("u"); self.assertTrue(em.checkpoint_write(self.p))
                cp = self.cp(); d = json.loads(cp.read_text()); spoil(d, field)
                cp.write_text(json.dumps(d))
                self.fresh_process()
                _append(self.p, {"n": 3})
                with contextlib.redirect_stderr(io.StringIO()):
                    got = (self.fold("t"), self.fold("u"))
                self.assertEqual(got, ([0, 1, 2, 3], [0, 1, 2, 3]), "both folds answer, from the file read whole where needed")
                self.assertEqual(em.checkpoint_stats()["fallbacks"], {"corrupt": 1}, "counted corrupt once")
                self.assertFalse(cp.exists(), "and removed, so the next process does not meet it again")

    def test_a_document_whose_stat_fails_reads_as_none_and_is_said_once(self):
        """A stat of the document that fails for a reason other than a missing file (here a checkpoints directory without its
        search bit) reads as no document at the restore and at the write's carry, as a missing one does, and never raises.
        Unlike a missing one it is said once on stderr, naming the document and the error: the file is read whole and the
        write fails, and nothing else would say why. No fallback is counted, and the document is not removed. The restore's
        stat (the shared read's) says it here; the write's own, the carry's, is pinned by the next test."""
        d, cp = jd.STATE / "checkpoints", self.cp()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.fold("t"); self.assertTrue(em.checkpoint_write(self.p))   # the first fold and write: no document yet
        self.assertEqual([ln for ln in err.getvalue().splitlines() if str(cp) in ln], [],
                         "a missing document is the quiet common case: nothing names it")
        self.assertEqual(cp.parent, d)
        before, mode = cp.read_bytes(), d.stat().st_mode
        os.chmod(d, 0o600)                               # readable and writable, not searchable: a stat of any file in it fails
        self.addCleanup(os.chmod, d, mode)
        if os.access(d, os.X_OK):
            self.skipTest("this account searches a directory whatever its mode (root)")
        self.fresh_process()
        _append(self.p, {"n": 3})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.fold("t"), [0, 1, 2, 3], "the restore reads the file whole")
        said = [ln for ln in err.getvalue().splitlines() if str(cp) in ln]
        self.assertEqual(len(said), 1, "the restore's read says it: %r" % err.getvalue())
        self.assertIn(os.strerror(errno.EACCES), said[0])
        with contextlib.redirect_stderr(err):
            self.assertFalse(em.checkpoint_write(self.p), "the carry reads no document, and the write's own tmp file fails")
        said = [ln for ln in err.getvalue().splitlines() if str(cp) in ln]
        self.assertEqual(len(said), 1, "said once across the restore and the write: %r" % err.getvalue())
        self.assertEqual((em.checkpoint_stats()["fallbacks"], em.checkpoint_stats()["restoredFolds"]), ({}, {}),
                         "no fallback counted, nothing restored")
        os.chmod(d, mode)
        self.assertEqual(cp.read_bytes(), before, "and the document stays as it was")

    def test_a_write_whose_document_stat_fails_says_so_once(self):
        """The write's carry stats the document on its own path: a running kernel whose checkpoints directory fails after
        boot reaches only that stat (its folds hold their cursors, so no restore reads the document), and its writes then
        fail with that line the one word on why."""
        d, cp = jd.STATE / "checkpoints", self.cp()
        self.fold("t"); self.assertTrue(em.checkpoint_write(self.p))
        _append(self.p, {"n": 3}); self.assertEqual(self.fold("t"), [0, 1, 2, 3])   # the cursor advances; nothing reads the document
        mode = d.stat().st_mode
        os.chmod(d, 0o600)
        self.addCleanup(os.chmod, d, mode)
        if os.access(d, os.X_OK):
            self.skipTest("this account searches a directory whatever its mode (root)")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertFalse(em.checkpoint_write(self.p), "the carry reads no document, and the write's own tmp file fails")
        said = [ln for ln in err.getvalue().splitlines() if str(cp) in ln]
        self.assertEqual(len(said), 1, "the write's carry says it: %r" % err.getvalue())
        self.assertIn(os.strerror(errno.EACCES), said[0])

    def test_the_exit_drains_budget_counts_the_paths_it_attempted(self):
        """checkpoint_write_dirty's budget (the exit drain's, inside the manager's grace) is keyed on the paths attempted, as
        the converge pass's is. Under a lasting fault every write fails (here a checkpoints directory without its search bit),
        and a budget that tripped only after a written path let the drain attempt every dirty path, each after its encode.
        At a zero budget it attempts the first, and every path stays dirty for the next writer."""
        paths = sorted(str(self.td / ("drain-%d.jsonl" % i)) for i in range(3))
        for q in paths:
            _write(q, [{"n": k} for k in range(3)])
            self.fold("t", q)
        self.assertEqual(em.checkpoint_dirty(), paths, "the premise: three dirty paths")
        d = jd.STATE / "checkpoints"
        d.mkdir(parents=True, exist_ok=True)
        mode = d.stat().st_mode
        os.chmod(d, 0o600)
        self.addCleanup(os.chmod, d, mode)
        if os.access(d, os.X_OK):
            self.skipTest("this account searches a directory whatever its mode (root)")
        tried, real = [], em.checkpoint_write

        def write(p, force=False):
            tried.append(p)
            return real(p, force)
        with mock.patch.object(em, "checkpoint_write", write), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(em.checkpoint_write_dirty(budget_s=0), 0, "no write succeeds")
        self.assertEqual(tried, paths[:1], "one path attempted, then the budget ends the drain")
        self.assertEqual(em.checkpoint_dirty(), paths, "and every path stays dirty")

    def test_another_writers_rename_right_after_this_writes_is_read_at_the_next_write(self):
        """The write takes its index entry's stat from its own tmp file before the rename (a rename keeps the inode, the
        modification time and the size), never from the document's path after it: another writer's rename landing between
        this write's rename and such a stat would file that writer's document under this write's entry, and the next carry
        would drop that writer's entries. Here another writer renames in a copy with one more entry right after this
        write's rename."""
        cp, real, landed = self.cp(), os.replace, []

        def replace(src, dst):
            real(src, dst)
            if str(dst) == str(cp) and not landed:       # this write's rename; the other writer's lands right after it
                d = json.loads(cp.read_text()); d["folds"]["v"] = {"count": d["count"], "state": em._ckpt_encode([7])}
                tmp = cp.with_name(cp.name + ".other.tmp"); tmp.write_text(json.dumps(d, separators=(",", ":")))
                real(tmp, cp); landed.append(1)
        self.fold("t")
        with mock.patch.object(em.os, "replace", replace):
            self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual((landed, sorted(self.doc(self.p)["folds"])), ([1], ["t", "v"]), "the premise: the other writer's document stands")
        _append(self.p, {"n": 3}); self.fold("t")
        n0 = self.consults()
        self.assertTrue(em.checkpoint_write(self.p))
        self.assertEqual(self.consults() - n0, 1, "the next write's carry loads the other writer's document")
        self.assertEqual(self.states().get("v"), (3, [7]), "and carries its entry")

    def test_the_index_holds_at_most_its_bound_and_the_oldest_path_reads_once_after(self):
        """_DOC_INDEX is bounded in paths (_DOC_INDEX_MAX): over it the path least recently written or loaded is let go, and
        that path's next carry reads its document once, as a carry with no index entry does. Here the bound is 2 and three
        files are written."""
        saved = em._DOC_INDEX_MAX; em._DOC_INDEX_MAX = 2
        self.addCleanup(setattr, em, "_DOC_INDEX_MAX", saved)
        paths = [str(self.td / ("bound-%d.jsonl" % i)) for i in range(3)]
        for q in paths:
            _write(q, [{"n": i} for i in range(3)])
            self.fold("t", q); self.assertTrue(em.checkpoint_write(q))
        with em._CKPT_LOCK:
            self.assertEqual(list(em._DOC_INDEX), paths[1:], "over the bound the oldest path goes")
        _append(paths[0], {"n": 3}); self.assertEqual(self.fold("t", paths[0]), [0, 1, 2, 3])
        size, reads, n0 = self.cp(paths[0]).stat().st_size, self.doc_reads(paths[0]), self.consults()
        self.assertTrue(em.checkpoint_write(paths[0]))
        self.assertEqual((self.doc_reads(paths[0]) - reads, self.consults() - n0), (size, 1), "its next carry reads its document once")
        with em._CKPT_LOCK:
            self.assertEqual(list(em._DOC_INDEX), [paths[2], paths[0]], "and the carry's load files it again as the newest")

    def test_a_write_files_its_path_as_the_newest_in_the_index(self):
        """A write moves its path to the newest end of the index, so the bound lets go of the path least recently written or
        loaded, not the first one ever filed: without the move, a path written every cycle would go by its first filing's
        age, and its next carry would read its document again. At a bound of 2: p0 and p1 written, p0 again after an append
        (its carry reads nothing, so the write alone files it), then p2. p1 goes."""
        saved = em._DOC_INDEX_MAX; em._DOC_INDEX_MAX = 2
        self.addCleanup(setattr, em, "_DOC_INDEX_MAX", saved)
        paths = [str(self.td / ("newest-%d.jsonl" % i)) for i in range(3)]
        for q in paths:
            _write(q, [{"n": k} for k in range(3)])
        for q in paths[:2]:
            self.fold("t", q); self.assertTrue(em.checkpoint_write(q))
        _append(paths[0], {"n": 3}); self.assertEqual(self.fold("t", paths[0]), [0, 1, 2, 3])
        n0 = self.consults()
        self.assertTrue(em.checkpoint_write(paths[0]))
        self.assertEqual(self.consults() - n0, 0, "the premise: p0's carry loaded nothing")
        self.fold("t", paths[2]); self.assertTrue(em.checkpoint_write(paths[2]))
        with em._CKPT_LOCK:
            self.assertEqual(list(em._DOC_INDEX), [paths[0], paths[2]], "p1, the least recently written, goes; p0 stays")


class CursorTableBound(Base):
    """fold_records' cursor dicts past 256 keys (2026-09-17). A fold dict is keyed per FILE, and the chat build's agent-gist dict
    holds one cursor per agent transcript the board shows, finished ones included (a live board: 315), so its stepping folds (a
    running agent's append, a new agent's first fold) reached the bound at every build. The bound CLEARED the dict, and a
    finished file's cursor gone while its restored tail entry still stood had nothing to restore from (a document's restore is
    taken once per pending record): the next fold read the file whole, the quiescent drop popped the entry and rewrote the
    document, the fold after restored again, and so on, one whole read per build per finished file (live: 2,369 whole refolds
    and 1.24 GB read in an hour). At the bound the fold now drops only the cursors that cannot serve a hit (no reader entry for
    the path, or one of another generation) and keeps every live one, so the table is bounded by the reader's entries."""

    def setUp(self):
        super().setUp()
        self.d = self.td / "agents"; self.d.mkdir()
        em.checkpoint_cycle_begin(64 * 1024 * 1024)   # a cycle budget of the test's own, so the drops write (review 2026-09-17): it
        #                                               began with the env-derived default, and the class's ~258 drop writes take 8 KB
        #                                               of the budget each, so ROMP_CKPT_CONVERGE_MB exported at 1 deferred the drops
        #                                               from about the 128th file on, the entries standing, and turned exactly this
        #                                               class red; the module's other budget tests make a drop or two and never notice
        self.cache = {}
        self.kinds = []

    @staticmethod
    def _step(st, o):
        return st + [o["n"]]

    def _finished(self, name):
        p = str(self.d / name)
        _write(p, [{"n": i} for i in range(3)])
        old = time.time() - 600; os.utime(p, (old, old))       # unchanged past the quiescence window: a subagent that returned
        return p

    def _fold(self, p, ckpt="synthGist"):
        with em._READ_BYTES_LOCK:
            b0 = em._READ_BYTES.get(p, 0)
        got = em.fold_records(self.cache, p, list, self._step, on=self.kinds.append, ckpt=ckpt, drop_after="quiescent")
        with em._READ_BYTES_LOCK:
            b1 = em._READ_BYTES.get(p, 0)
        return got, self.kinds[-1], b1 - b0

    def _rotation(self, prefix, ckpt="synthGist"):
        """257 finished files, each folded twice: a refold (the drop pops the entry and writes the document), then a restore
        over a tail entry that now stands. Every cursor is live: at its standing entry's generation."""
        ps = [self._finished("%s-%03d.jsonl" % (prefix, i)) for i in range(257)]
        for p in ps:
            self.assertEqual(self._fold(p, ckpt)[1], "refold", "the first fold reads whole")
            with em._JSONL_CACHE_LOCK:
                self.assertIsNone(em._JSONL_CACHE.get(p), "the quiescent drop popped the entry")
            self.assertIn("state", self.doc(p)["folds"][ckpt], "and wrote the document")
        for p in ps:
            self.assertEqual(self._fold(p, ckpt)[1], "restore", "the second fold restores over a tail entry")
            with em._JSONL_CACHE_LOCK:
                ent = em._JSONL_CACHE.get(p)
            self.assertIsNotNone(ent, "which stands after the fold")
            self.assertEqual((ent[5], len(ent[4]), self.cache[p][1]), (3, 0, ent[6]), "base 3, nothing held, the cursor at its gen")
        self.assertEqual(len(self.cache), 257)
        return ps

    def test_a_live_cursor_survives_the_bound_and_its_finished_file_stays_a_hit(self):
        ps = self._rotation("f")
        new = self._finished("new.jsonl")
        self.assertEqual(self._fold(new)[1], "refold")            # a stepping fold with the dict at 257: the bound trips
        refolds = {k: dict(v) for k, v in em._CKPT_STATS["refolds"].items()}
        seq = self.doc(ps[0])["seq"]
        got, kind, nread = self._fold(ps[0])
        self.assertEqual((kind, nread, got), ("hit", 0, [0, 1, 2]), "the finished file costs a stat: its cursor survived the bound")
        self.assertEqual(em._CKPT_STATS["refolds"], refolds, "no whole read counted against the fold")
        self.assertEqual(self.doc(ps[0])["seq"], seq, "and its document is not rewritten")
        self.assertEqual(len(self.cache), 258, "every cursor at a standing entry's generation is kept")

    def test_dead_cursors_leave_the_table_at_the_bound(self):
        """A cursor whose reader entry left memory, with no document to restore from, serves nothing: the next fold of its file
        reads whole whatever the table holds. Those are what the bound drops, so the table stays bounded."""
        saved = em._CKPT_DIR_FN; em._CKPT_DIR_FN = None          # no checkpoints in play: the drop pops and writes nothing
        self.addCleanup(setattr, em, "_CKPT_DIR_FN", saved)
        ps = [self._finished("d-%03d.jsonl" % i) for i in range(257)]
        for p in ps:
            self.assertEqual(self._fold(p, "synthDead")[1], "refold")
        with em._JSONL_CACHE_LOCK:
            self.assertTrue(all(em._JSONL_CACHE.get(p) is None for p in ps), "every entry popped by the drop")
        self.assertEqual(self.ckpt_files(), [], "and no document written")
        self.assertEqual(len(self.cache), 257, "under the bound nothing is swept")
        new = self._finished("new.jsonl")
        self.assertEqual(self._fold(new, "synthDead")[1], "refold")
        self.assertEqual(set(self.cache), {new}, "at the bound every dead cursor left; the stepping fold's own stands")
        got, kind, nread = self._fold(ps[0], "synthDead")
        self.assertEqual((kind, got), ("refold", [0, 1, 2]), "a dead cursor served nothing: its file reads whole regardless")
        self.assertGreater(nread, 0)

    def test_the_bound_sweeps_the_dead_cursors_and_keeps_the_live_ones(self):
        """The two together: entries popped from under half the cursors (an owed drop paid by its pop alone, the real event that
        kills a cursor) make those dead; the sweep takes exactly those, and the finished files behind the rest stay hits."""
        ps = self._rotation("m")
        for p in ps[1::2]:
            em._pop_owed_entry(p)
        new = self._finished("new.jsonl")
        self.assertEqual(self._fold(new)[1], "refold")
        self.assertEqual(set(self.cache), set(ps[0::2]) | {new}, "the dead cursors left, the live ones and the stepping fold's stand")
        got, kind, nread = self._fold(ps[2])
        self.assertEqual((kind, nread, got), ("hit", 0, [0, 1, 2]), "a live cursor's file: a stat")
        got, kind, nread = self._fold(ps[1])
        self.assertEqual((kind, got), ("restore", [0, 1, 2]), "a dead cursor's file restores over a fresh tail entry, as it would have "
                                                              "with the stale cursor in the table (its gen was another entry's)")
        self.assertGreater(nread, 0, "the guard read, nothing of the prefix")

    def test_a_cursor_under_an_entry_of_another_generation_leaves_at_the_bound(self):
        """The sweep's second arm (review 2026-09-17: the three tests above kill every cursor by popping its entry, so a sweep
        that asked only whether the entry is gone passed them all). Production reaches this arm through the two fold dicts that
        share every agent file (the gist's and the launch ids'): one dict's quiescent drop pops the file's entry, the sibling
        dict's next fold rebuilds it from the document at a NEW generation and restores at the witness, which pops nothing and
        leaves the entry standing, and the first dict's cursor stands under it at the old generation, unable to serve a hit
        (every from-zero read is a new gen). At the bound that cursor leaves, and the file's next fold in the first dict restores
        from its document rather than answering from the stale cursor."""
        ps = self._rotation("g")
        other, kinds = {}, []                                  # the sibling fold dict over the same files, as the launch-ids dict is
        stale, live = ps[1::2], ps[0::2]
        for p in stale:
            em.fold_records(other, p, list, self._step, on=kinds.append, ckpt="synthOther", drop_after="quiescent")
            self.assertEqual(kinds[-1], "refold", "the sibling's first fold has no restore of its own: whole, and its drop pops")
            with em._JSONL_CACHE_LOCK:
                self.assertIsNone(em._JSONL_CACHE.get(p))
            em.fold_records(other, p, list, self._step, on=kinds.append, ckpt="synthOther", drop_after="quiescent")
            self.assertEqual(kinds[-1], "restore", "its second fold restores over a tail entry rebuilt from the document")
            with em._JSONL_CACHE_LOCK:
                ent = em._JSONL_CACHE.get(p)
            self.assertIsNotNone(ent, "which stands: a restore at the witness pops nothing")
            self.assertEqual(other[p][1], ent[6], "the sibling's cursor is at the new generation")
            self.assertNotEqual(self.cache[p][1], ent[6], "this dict's cursor is at the old one, under a standing entry")
        self.assertEqual(len(self.cache), 257, "under the bound the stale cursors stand")
        new = self._finished("new.jsonl")
        self.assertEqual(self._fold(new)[1], "refold")            # the stepping fold that trips the bound
        self.assertEqual(set(self.cache), set(live) | {new}, "at the bound every cursor under an entry of another generation left")
        got, kind, nread = self._fold(stale[0])
        self.assertEqual((kind, nread, got), ("restore", 0, [0, 1, 2]), "its file restores from the document over the standing entry")
        with em._JSONL_CACHE_LOCK:
            self.assertEqual(self.cache[stale[0]][1], em._JSONL_CACHE[stale[0]][6], "and its cursor is at the entry's generation again")
        got, kind, nread = self._fold(live[0])
        self.assertEqual((kind, nread, got), ("hit", 0, [0, 1, 2]), "a neighbour at its entry's generation stayed: a stat")

if __name__ == "__main__":
    unittest.main()
