#!/usr/bin/env python3
"""The hydrated-body memo keeps only what a fill reads, weighs it in real memory, and stays under a smaller cap
(2026-10-01, the kernel memory trims).

A lazy atom's body is read back from its transcript on demand (em.hydrate), and a memo keyed by the record's uuid keeps
what was read, so the next fresh copy of the atom is filled without the disk. Before this change the memo kept the WHOLE
record (its metadata, and a Read call's second copy of the file's text, which no atom carries), weighed it by the line's
length on disk, and capped it at max(1 GiB, MemTotal/32) of those line bytes. A decoded record weighs something else:
about 2.85 times its line across the live transcripts, under one times for escaped non-ASCII text, several times for a
body of many small blocks.

Pinned here: the memory the memo holds matches the bytes it counts (measured with tracemalloc) at both of those
extremes; an atom filled from the memo, by a miss or by a hit, shares its body rather than copying it, so while such an
atom lives an eviction frees only the entry's own share, and once it is gone the eviction has freed what was counted; a
Read result's duplicate text is not kept, nor an attachment's fields beside its prompt; the counter stays the sum of its
entries' weights when a peer memoized the same record first; the memo's serves and evictions are counted; the default
cap is max(256 MiB, MemTotal/64) of real memory. And the guards that keep every reader as it was: each atom kind fills
from a memo hit exactly as it fills from its whole record, a hit reads nothing, and the by-offset record read the
comment cut uses still returns the whole record after a hydration.

SYNTHETIC fixtures only: invented notes-api text, placeholder uuids."""
import gc
import json
import os
import shutil
import tempfile
import tracemalloc
import unittest
import zlib
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs conftest's floor
# (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))

SID = "7e5d2c1b-0a9f-4e8d-b7c6-a5f4e3d2c1b0"           # a private synthetic session id
MIB = 1024 * 1024
BODY_FIELDS = ("message", "toolUseResult", "skillMd")
KIND_TYPE = {"a": "assistant", "k": "assistant", "o": "assistant", "u": "user", "c": "user", "b": "user"}


def uid(tag, i):
    """A placeholder uuid, stable across runs: the tag's crc32, then the index."""
    return "%08x-1111-2222-3333-%012x" % (zlib.crc32(tag.encode()), i)


def _meta(u, parent, i, typ):
    return {"parentUuid": parent, "isSidechain": False, "userType": "external", "cwd": "/notes-api", "sessionId": SID,
            "version": "2.1.0", "gitBranch": "main", "type": typ, "uuid": u,
            "timestamp": "2026-10-01T12:%02d:%02d.000Z" % (i // 60 % 60, i % 60)}


def assistant(u, parent, i, content):
    r = _meta(u, parent, i, "assistant")
    r["requestId"] = "req_%s" % u.replace("-", "")
    r["message"] = {"id": "msg_%s" % u.replace("-", ""), "type": "message", "role": "assistant", "model": "claude-test",
                    "content": content, "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": 3, "output_tokens": 912, "cache_read_input_tokens": 40960,
                              "cache_creation_input_tokens": 1024}}
    return r


def user(u, parent, i, content, tur=None):
    r = _meta(u, parent, i, "user")
    r["message"] = {"role": "user", "content": content}
    if tur is not None:
        r["toolUseResult"] = tur
    return r


def write(path, recs):
    """The records as the CLI writes them, one JSON line each; returns each record's (byte offset, byte length)."""
    offs, off = [], 0
    with open(path, "wb") as fh:
        for r in recs:
            b = json.dumps(r).encode("utf-8")
            fh.write(b + b"\n")
            offs.append((off, len(b)))
            off += len(b) + 1
    return offs


def lazy_atom(rec, path, at, kind, **lz):
    """A pre-cut atom as a restore builds it: identity scalars, the record's location under `lazy`, a sentinel body."""
    return {"type": KIND_TYPE[kind], "uuid": rec["uuid"], "session_id": SID, "t": 0, "fsid": Path(path).stem,
            "parentUuid": rec.get("parentUuid"), "lazy": dict({"k": kind, "at": tuple(at)}, **lz),
            "message": em._LazyBody(rec["uuid"], str(path))}


def bodies(atom):
    return {f: atom[f] for f in BODY_FIELDS + ("lazy",) if f in atom}


def traced():
    gc.collect()
    return tracemalloc.get_traced_memory()[0]


class Base(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()
        self.fresh()

    def tearDown(self):
        self.fresh()
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear()
            em._HYDRATED_BYTES[0] = 0


class RealUnits(Base):
    """The counted bytes are the memory the memo holds. The atoms filled from it share its bodies, so an eviction gives
    that memory back only once no such atom holds them: while one does, the eviction frees the entry's own share alone,
    and /perf's hydrated bytes overstate what a cap frees by the bodies live atoms hold."""

    def measure(self, recs, kind="a"):
        """The memory figures for `recs` hydrated through lazy atoms of `kind`, a dict of:
        counted     the bytes the memo counts for them; lines, their line bytes on disk;
        alive       what the misses allocated, the memo's entries and the first atoms' bodies together;
        hits        what filling a second set of fresh atoms from the memo allocated: they share its bodies;
        held        what the memo holds once both sets have dropped their bodies;
        freedAlive  what an eviction frees while a third set, filled from the memo, still holds its bodies;
        freed       what that eviction has freed once the third set drops its bodies too.
        The atoms' own parts that predate the measurement (the lazy markers, the sentinel bodies) are kept alive, so only
        what the hydration allocated moves the numbers. The eviction is the reader's own: a cap of one byte and one more
        read."""
        tiny = assistant(uid("tiny", 0), None, 0, [{"type": "text", "text": "noted"}])
        path = self.td / (SID + ".jsonl")
        offs = write(path, list(recs) + [tiny])
        sets = [[lazy_atom(r, path, at, kind) for r, at in zip(recs, offs)] for _ in range(3)]
        last = lazy_atom(tiny, path, offs[-1], "a")
        keep = [(a["lazy"], a["message"]) for s in sets + [[last]] for a in s]
        lines = sum(ln for _, ln in offs[:-1])

        def drop(atoms):
            for a in atoms:
                for f in BODY_FIELDS:
                    a.pop(f, None)

        was = tracemalloc.is_tracing()
        if not was:
            tracemalloc.start()
        try:
            m0 = traced()
            b0 = em._HYDRATED_BYTES[0]
            read0 = em._ASM_CKPT_STATS["hydratedAtoms"]
            self.assertEqual(em.hydrate(sets[0], SID, by="test"), len(recs))
            counted = em._HYDRATED_BYTES[0] - b0
            alive = traced() - m0
            self.assertEqual(em.hydrate(sets[1], SID, by="test"), len(recs))
            self.assertEqual(em._ASM_CKPT_STATS["hydratedAtoms"] - read0, len(recs), "the second set is filled from the memo")
            hits = traced() - m0 - alive
            drop(sets[0])
            drop(sets[1])
            held = traced() - m0
            self.assertEqual(em.hydrate(sets[2], SID, by="test"), len(recs))
            m1 = traced()
            with mock.patch.object(em, "_HYDRATED_CAP", 1):
                em.hydrate([last], SID, by="test")
            drop([last])
            self.assertEqual(len(em._HYDRATED), 0, "a cap of one byte evicts every entry")
            freed_alive = m1 - traced()
            drop(sets[2])
            freed = m1 - traced()
        finally:
            if not was:
                tracemalloc.stop()
        del keep
        return {"counted": counted, "lines": lines, "alive": alive, "hits": hits, "held": held, "freedAlive": freed_alive,
                "freed": freed}

    def check(self, recs, kind="a"):
        m = self.measure(recs, kind)
        said = ", ".join("%s %d" % kv for kv in m.items())
        counted = m["counted"]
        self.assertGreater(counted, 0, said)
        self.assertTrue(0.8 <= m["held"] / counted <= 1.25, "the memo holds what it counts: " + said)
        self.assertLessEqual(m["alive"] / counted, 1.3, "an atom filled by a miss shares the memo's body, never a second copy: "
                             + said)
        self.assertLessEqual(m["hits"] / counted, 0.3, "an atom filled by a hit shares the memo's body, never a second copy: "
                             + said)
        self.assertLessEqual(m["freedAlive"] / counted, 0.2, "while atoms filled from the memo hold its bodies, an eviction "
                             "frees the entries' own share, the bodies staying with the atoms: " + said)
        self.assertTrue(0.8 <= m["freed"] / counted <= 1.25, "once no atom holds them, the eviction has freed what it "
                        "counted: " + said)
        return m

    def test_escaped_text_counts_its_decoded_size_not_its_line(self):
        """Non-ASCII text written escaped is six bytes a character on disk and two decoded: the line overstates it."""
        text = "—…" * 2500
        recs = [assistant(uid("esc", i), None, i, [{"type": "text", "text": "%d %s" % (i, text)}]) for i in range(120)]
        m = self.check(recs)
        self.assertLess(m["counted"], 0.6 * m["lines"], "decoded, the bodies weigh well under their lines")

    def test_many_small_blocks_count_their_decoded_size_not_their_line(self):
        """A body of many small blocks is a few dozen bytes a block on disk and a dict and a string each decoded: the line
        understates it several times over."""
        recs = [assistant(uid("blk", i), None, i, [{"type": "text", "text": "ok %d.%d" % (i, j)} for j in range(300)])
                for i in range(60)]
        m = self.check(recs)
        self.assertGreater(m["counted"], 2 * m["lines"], "decoded, the bodies weigh several times their lines")

    def test_a_read_results_copy_of_the_file_is_not_kept(self):
        """A Read call's result record carries the file's text twice: in the tool result the atom shows, and in a top-level
        toolUseResult of a shape no consumer reads (the emit never carries it). The memo keeps the first alone."""
        recs = []
        for i in range(80):
            text = "".join("line %d of app_%d.py: return handler(request)\n" % (n, i) for n in range(180))
            tur = {"type": "text", "file": {"filePath": "/notes-api/src/app_%d.py" % i, "content": text,
                                            "numLines": 180, "startLine": 1, "totalLines": 180}}
            recs.append(user(uid("read", i), None, i,
                             [{"type": "tool_result", "tool_use_id": "toolu_%04d" % i, "content": text}], tur=tur))
        m = self.check(recs, kind="u")
        said = ", ".join("%s %d" % kv for kv in m.items())
        self.assertLess(m["counted"], 0.65 * m["lines"], "the duplicate file text is not counted: " + said)
        self.assertLess(m["held"], 0.65 * m["lines"], "…and not held: " + said)

    def test_an_attachment_is_kept_as_its_prompt_alone(self):
        """A queued command's attachment carries its type, its mode and whatever else the CLI writes beside the prompt;
        the fill reads the prompt, and the memo keeps nothing else of it."""
        rec = _meta(uid("att", 0), None, 0, "attachment")
        rec["attachment"] = {"type": "queued_command", "prompt": "also run the api tests", "commandMode": "prompt",
                             "extra": "x" * 20000}
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        atom = lazy_atom(rec, path, at, "b")
        self.assertEqual(em.hydrate([atom], SID, by="test"), 1)
        body, weight = em._HYDRATED[rec["uuid"]]
        self.assertEqual(body, {"attachment": {"prompt": "also run the api tests"}})
        self.assertLess(weight, 2000, "weighed without the rest of the attachment")
        self.assertEqual(atom["message"], {"role": "user", "content": [{"type": "text", "text": "also run the api tests"}]})


class CounterAndCap(Base):
    def test_the_counter_stays_the_sum_of_its_entries_when_a_peer_memoized_the_record_first(self):
        """Two threads that both missed the same record (copies of it in two files of a lineage) both read it; the second
        put replaces the first entry, so its weight leaves the counter and the entry moves to the young end."""
        rec = assistant(uid("peer", 0), None, 0, [{"type": "text", "text": "the retry budget is three"}])
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        atom = lazy_atom(rec, path, at, "a")

        class PeerPut(dict):
            """A memo whose second miss for the record (the per-file check, after the first loop missed too) is the
            instant a peer's put of the same record lands, under the lock the caller holds."""
            misses = 0

            def get(self, k, default=None):
                hit = super().get(k, default)
                if hit is None and k == rec["uuid"]:
                    PeerPut.misses += 1
                    if PeerPut.misses == 2:
                        self[k] = ({"message": rec["message"]}, 5000)
                        em._HYDRATED_BYTES[0] += 5000
                return hit

        memo = PeerPut()
        with mock.patch.object(em, "_HYDRATED", memo):
            em.hydrate([atom], SID, by="test")
            self.assertEqual(list(memo), [rec["uuid"]])
            self.assertEqual(em._HYDRATED_BYTES[0], sum(w for _, w in memo.values()),
                             "the counter is the sum of the entries' weights, the replaced one's taken out")
        self.assertEqual(PeerPut.misses, 2, "the peer's put landed between the check and the put")

    def test_the_memo_counts_its_serves_and_its_evictions(self):
        """With the gauge in real bytes it no longer equals hydratedBytes (line bytes read) while nothing is evicted, so
        asmCheckpoint carries the two numbers that tell a memo that fits from one too small: hydratedHits, the fills it
        served without a read, and hydratedEvicted, the entries its cap pushed out (then reads of records already read)."""
        recs = [assistant(uid("cnt", i), None, i, [{"type": "text", "text": "note %d on the retry budget" % i}])
                for i in range(7)]
        path = self.td / (SID + ".jsonl")
        offs = write(path, recs)

        def atoms(n):
            return [lazy_atom(r, path, at, "a") for r, at in list(zip(recs, offs))[:n]]

        def delta(st0):
            st = em.asm_checkpoint_stats()
            return {k: st[k] - st0[k] for k in ("hydratedAtoms", "hydratedHits", "hydratedEvicted")}

        st0 = em.asm_checkpoint_stats()
        self.assertEqual(em.hydrate(atoms(6), SID, by="test"), 6)
        self.assertEqual(delta(st0), {"hydratedAtoms": 6, "hydratedHits": 0, "hydratedEvicted": 0}, "six reads")
        st0 = em.asm_checkpoint_stats()
        self.assertEqual(em.hydrate(atoms(6), SID, by="test"), 6)
        self.assertEqual(delta(st0), {"hydratedAtoms": 0, "hydratedHits": 6, "hydratedEvicted": 0},
                         "fresh copies of the same atoms: six fills served, no read")
        with mock.patch.object(em, "_HYDRATED_CAP", 1):
            st0 = em.asm_checkpoint_stats()
            self.assertEqual(em.hydrate(atoms(7)[6:], SID, by="test"), 1)
            self.assertEqual(delta(st0), {"hydratedAtoms": 1, "hydratedHits": 0, "hydratedEvicted": 7},
                             "a seventh record under a one-byte cap: the six and itself pushed out")
            st0 = em.asm_checkpoint_stats()
            self.assertEqual(em.hydrate(atoms(6), SID, by="test"), 6)
            self.assertEqual(delta(st0), {"hydratedAtoms": 6, "hydratedHits": 0, "hydratedEvicted": 6},
                             "the six again: read again, each pushed out as it lands")

    def test_the_default_cap_is_a_quarter_gib_of_real_memory_or_a_64th_of_the_machine(self):
        if os.environ.get("ROMP_HYDRATED_CAP_MB"):
            self.skipTest("ROMP_HYDRATED_CAP_MB sets the cap outright in this environment")
        self.assertEqual(em._HYDRATED_CAP, max(256 * MIB, em._machine_memory_bytes() // 64))


class ReadersKeepTheirBehavior(Base):
    """Guards, green before the change and after: every reader of the memo gets what it got from the whole record."""

    def kinds(self):
        """One record per atom kind, with the lazy marker a restore gives it."""
        skill = "Base directory for this skill: /notes-api/.claude/skills/review\n\n# Review\n" + "check the handler. " * 1100
        rows = [
            ("a", assistant(uid("k", 1), None, 1, [{"type": "text", "text": "the cap and the retry budget remain"},
                                                     {"type": "tool_use", "id": "toolu_0001", "name": "Bash",
                                                      "input": {"command": "pytest -q tests/test_api.py"}}]), {}),
            ("u", user(uid("k", 2), uid("k", 1), 2,
                       [{"type": "tool_result", "tool_use_id": "toolu_0002", "content": "answered"}],
                       tur={"answers": {"Which store?": "the first"}, "questions": [{"question": "Which store?"}]}),
             {"tur": True}),
            ("u", user(uid("k", 3), uid("k", 2), 3,
                       [{"type": "tool_result", "tool_use_id": "toolu_0003", "content": "def handler(): pass\n"}],
                       tur={"type": "text", "file": {"filePath": "/notes-api/src/app.py", "content": "def handler(): pass\n"}}),
             {}),
            ("u", user(uid("k", 4), uid("k", 3), 4, "please close out the retry ticket"), {}),
            ("c", user(uid("k", 5), uid("k", 4), 5, "<command-name>/review</command-name>"), {"disp": "/review"}),
            ("o", user(uid("k", 6), uid("k", 5), 6, "<local-command-stdout>\x1b[1mall 12 checks passed\x1b[0m"
                                                    "</local-command-stdout>"), {}),
            ("k", user(uid("k", 7), uid("k", 6), 7, [{"type": "text", "text": skill}]), {}),
        ]
        att = _meta(uid("k", 8), uid("k", 7), 8, "attachment")
        att["attachment"] = {"type": "queued_command", "prompt": "also run the api tests", "commandMode": "prompt",
                             "extra": "x" * 400}
        rows.append(("b", att, {}))
        return rows

    def test_every_kind_fills_from_a_memo_hit_as_from_its_whole_record(self):
        rows = self.kinds()
        path = self.td / (SID + ".jsonl")
        offs = write(path, [r for _, r, _ in rows])
        for (kind, rec, lz), at in zip(rows, offs):
            want = lazy_atom(rec, path, at, kind, **lz)
            em._hydrate_one(want, json.loads(json.dumps(rec)))     # the fill from the whole record: the reference
            miss = lazy_atom(rec, path, at, kind, **lz)
            hit = lazy_atom(rec, path, at, kind, **lz)
            self.assertEqual(em.hydrate([miss], SID, by="test"), 1)
            self.assertIn(rec["uuid"], em._HYDRATED, "the read is memoized (%s)" % kind)
            read0 = em._ASM_CKPT_STATS["hydratedBytes"]
            self.assertEqual(em.hydrate([hit], SID, by="test"), 1)
            self.assertEqual(em._ASM_CKPT_STATS["hydratedBytes"], read0, "a hit reads nothing (%s)" % kind)
            self.assertEqual(bodies(miss), bodies(want), "a miss fills as the whole record does (%s)" % kind)
            self.assertEqual(bodies(hit), bodies(want), "a hit fills as the whole record does (%s)" % kind)
            self.assertNotIn("lazy", hit)

    def test_the_record_read_by_offset_is_the_whole_record_after_a_hydration(self):
        """The comment cut reads a pre-cut record back by offset and decides on its type, time and provenance, which the
        memo's entry need not hold: the read after a hydration is the whole record, as before."""
        rec = user(uid("cut", 0), None, 0, "where does the retry budget live?")
        rec["origin"] = {"kind": "human"}
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        atom = lazy_atom(rec, path, at, "u")
        em.hydrate([atom], SID, by="test")
        self.assertIn(rec["uuid"], em._HYDRATED)
        self.assertEqual(em._read_record_at(str(path), at, rec["uuid"]), rec)


if __name__ == "__main__":
    unittest.main()
