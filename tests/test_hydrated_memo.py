#!/usr/bin/env python3
"""The hydrated-body memo keeps only what a fill reads, weighs it in real memory, and stays under a smaller cap
(2026-10-01, the kernel memory trims).

A lazy atom's body is read back from its transcript on demand (em.hydrate), and a memo keyed by the record's uuid keeps
what was read, so the next fresh copy of the atom is filled without the disk. Before this change the memo kept the WHOLE
record (its metadata, and a Read call's second copy of the file's text, which no atom carries), weighed it by the line's
length on disk, and capped it at max(1 GiB, MemTotal/32) of those line bytes. A decoded record weighs something else:
about 2.85 times its line across the live transcripts, under one times for escaped non-ASCII text, several times for a
body of many small blocks.

Pinned here: the memory the memo holds matches the bytes it counts (measured with tracemalloc) for non-ASCII text as
the CLI writes it, for the synthetic extreme of the same text escaped, and for bodies of many small blocks; small
records carry their entry's own overhead in their weight; a kept tool result (an Edit's original file, a question's
answers) and a long queued prompt count in it too; the size walk counts each object once and skips the shared
singletons, exactly, and runs outside the memo's lock; an atom filled from the memo, by a miss or by a hit, shares its
body rather than copying it, so while such an atom lives an eviction frees only the entry's own share, and once it is
gone the eviction has freed what was counted; a Read result's duplicate text is not kept, nor an attachment's fields
beside its prompt; the counter stays the sum of its entries' weights when a peer memoized the same record first, and the
replaced record moves to the young end; a hit moves its entry there too, so the cap evicts the least recently used; the
memo's serves and evictions are counted, a body a peer put while this call waited on the file's read stripe among the
serves, an atom whose body a peer had already written not; the default cap is max(256 MiB, MemTotal/48) of real
memory. And the guards that keep every reader as it was: each atom kind fills from a memo hit exactly as it fills from
its whole record, a hit reads nothing, a consumed tool result reaches every atom of its record whichever atom read it, a
malformed attachment fails the fill as the whole record does, and the by-offset record read the comment cut uses still
returns the whole record after a hydration, takes no read stripe, and fails loudly once its file has moved.

SYNTHETIC fixtures only: invented notes-api text, placeholder uuids."""
import gc
import json
import os
import shutil
import sys
import tempfile
import threading
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


def write(path, recs, escaped=False):
    """The records as the CLI writes them, one JSON line each, non-ASCII text as raw UTF-8 (JSON.stringify escapes none
    of it); returns each record's (byte offset, byte length). escaped=True writes that text as \\u escapes instead, as
    Python's json does by default: a synthetic extreme, not the CLI's format."""
    offs, off = [], 0
    with open(path, "wb") as fh:
        for r in recs:
            b = json.dumps(r, ensure_ascii=escaped).encode("utf-8")
            fh.write(b + b"\n")
            offs.append((off, len(b)))
            off += len(b) + 1
    return offs


def edit_results(n=40, lines=300):
    """n Edit results: the line the atom shows, and a top-level tool result of a consumed shape (a structured patch) that
    carries the whole original file, a few hundred lines, so the memo keeps it."""
    old, new = "    return store.page(cursor, limit)", "    return store.page(cursor, min(limit, 100))"
    recs = []
    for i in range(n):
        src = "".join("    # line %d of notes_%d.py\n%s\n" % (k, i, old) for k in range(lines // 2))
        patch = [{"oldStart": 120, "oldLines": 6, "newStart": 120, "newLines": 6,
                  "lines": ["    # line %d of notes_%d.py" % (k, i) for k in range(60, 64)] + ["-" + old, "+" + new]}]
        tur = {"filePath": "/notes-api/src/notes_%d.py" % i, "oldString": old, "newString": new, "originalFile": src,
               "structuredPatch": patch, "userModified": False, "replaceAll": False}
        recs.append(user(uid("edit", i), None, i, [{"type": "tool_result", "tool_use_id": "toolu_%04d" % i,
                                                    "content": "The file /notes-api/src/notes_%d.py has been updated." % i}],
                         tur=tur))
    return recs


def answer_results(n=200):
    """n answers to the agent's questions: the line the atom shows, and a top-level tool result of a consumed shape (the
    questions with their options, and the answers), so the memo keeps it."""
    recs = []
    for i in range(n):
        qs = [{"question": "Which store should the notes route page from (%d.%d)?" % (i, q), "header": "Store %d" % q,
               "multiSelect": False,
               "options": [{"label": "store %d" % o, "description": "page from store %d by a cursor of %d rows" % (o, 50 * o)}
                           for o in range(4)]}
              for q in range(3)]
        answers = {x["question"]: "store %d" % q for q, x in enumerate(qs)}
        text = "User has answered your questions: " + ", ".join('"%s"="%s"' % kv for kv in answers.items())
        recs.append(user(uid("ans", i), None, i, [{"type": "tool_result", "tool_use_id": "toolu_%04d" % i, "content": text}],
                         tur={"questions": qs, "answers": answers}))
    return recs


def lazy_atom(rec, path, at, kind, own_uuid=False, **lz):
    """A pre-cut atom as a restore builds it: identity scalars, the record's location under `lazy`, a sentinel body.
    own_uuid gives the atom a uuid string object of its own, as an atom built from a document row has, so the memo's key
    outlives the atom only in the memo."""
    u = (rec["uuid"] + " ")[:-1] if own_uuid else rec["uuid"]
    return {"type": KIND_TYPE[kind], "uuid": u, "session_id": SID, "t": 0, "fsid": Path(path).stem,
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

    def measure(self, recs, kind="a", escaped=False, tur=False):
        """The memory figures for `recs` hydrated through lazy atoms of `kind` (with `tur`, kind u atoms whose marker
        carries the record's tool result, so they share it with the memo as they share the message), a dict of:
        counted     the bytes the memo counts for them; lines, their line bytes on disk;
        alive       what the misses allocated, the memo's entries and the first atoms' bodies together;
        hits        what filling a second set of fresh atoms from the memo allocated: they share its bodies;
        held        what the memo holds once both sets have dropped their bodies;
        freedAlive  what an eviction frees while a third set, filled from the memo, still holds its bodies;
        freed       what that eviction has freed once the third set drops its bodies too. That includes what those
                    atoms owned and the memo never did: each fill's own message dict around the shared content, and for
                    a body whose content is a string (a typed prompt) the block list the fill builds around it. On the
                    shapes measured here those parts are small beside the body; on a short prompt they are not, which is
                    why the small-record weight is measured with the atoms deleted whole instead.
        The atoms' own parts that predate the measurement (the lazy markers, the sentinel bodies) are kept alive, so only
        what the hydration allocated moves the numbers. The eviction is the reader's own: a cap of one byte and one more
        read."""
        tiny = assistant(uid("tiny", 0), None, 0, [{"type": "text", "text": "noted"}])
        path = self.td / (SID + ".jsonl")
        offs = write(path, list(recs) + [tiny], escaped=escaped)
        lz = {"tur": True} if tur else {}
        sets = [[lazy_atom(r, path, at, kind, **lz) for r, at in zip(recs, offs)] for _ in range(3)]
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

    def check(self, recs, kind="a", escaped=False, tur=False):
        m = self.measure(recs, kind, escaped, tur)
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

    def test_non_ascii_text_counts_its_decoded_size_not_its_line(self):
        """Non-ASCII text as the CLI writes it, raw UTF-8, is three bytes a character on disk for a dash or an ellipsis and
        two decoded: the line overstates it."""
        text = "—…" * 2500
        recs = [assistant(uid("utf", i), None, i, [{"type": "text", "text": "%d %s" % (i, text)}]) for i in range(120)]
        m = self.check(recs)
        self.assertLess(m["counted"], 0.9 * m["lines"], "decoded, the bodies weigh under their lines")

    def test_escaped_text_counts_its_decoded_size_not_its_line(self):
        """The synthetic extreme of the same text, written as \\u escapes (Python's json default, not the CLI's format):
        six bytes a character on disk and two decoded, so the line overstates it about three times."""
        text = "—…" * 2500
        recs = [assistant(uid("esc", i), None, i, [{"type": "text", "text": "%d %s" % (i, text)}]) for i in range(120)]
        m = self.check(recs, escaped=True)
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

    def test_an_edit_results_original_file_counts_in_its_weight(self):
        """An Edit's tool result is a shape atoms carry, its structured patch beside the whole original file, and the
        memo keeps it: the weight counts it, or the memo would hold many times what it counts."""
        self.check(edit_results(), kind="u", tur=True)

    def test_a_questions_answers_count_in_their_weight(self):
        """A question's tool result, its options and the answers chosen, is kept and weighed the same way."""
        self.check(answer_results(), kind="u", tur=True)

    def test_an_attachment_is_kept_as_its_prompt_alone(self):
        """A queued command's attachment carries its type, its mode and whatever else the CLI writes beside the prompt;
        the fill reads the prompt, and the memo keeps nothing else of it. A long prompt weighs at least its text: the
        floor beside the ceiling, so a weight that left the attachment out shows."""
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
        long = _meta(uid("att", 1), None, 1, "attachment")
        prompt = "also run the api tests, then " + "page the notes route by cursor and check the retry budget. " * 100
        long["attachment"] = {"type": "queued_command", "prompt": prompt, "commandMode": "prompt", "extra": "x" * 20000}
        at = write(path, [long])[0]
        self.assertEqual(em.hydrate([lazy_atom(long, path, at, "b")], SID, by="test"), 1)
        weight = em._HYDRATED[long["uuid"]][1]
        self.assertGreaterEqual(weight, sys.getsizeof(prompt), "a long prompt weighs at least its text")
        self.assertLess(weight, sys.getsizeof(prompt) + 2000, "…and not the rest of the attachment")

    def test_small_records_carry_their_entry_overhead_in_their_weight(self):
        """On a small record the entry's own parts (its tuple, its weight, its uuid key and its slot in the memo's table:
        _HYDRATED_ENTRY_BYTES) are a large share of the weight, about a quarter of a typed prompt's. With the atoms
        deleted whole, so the memo alone holds the uuid keys, what the memo holds is within 0.85 to 1.1 of what it
        counts. The constant is an estimate that overcounts here (a freshly grown table has no deleted slots, and a tuple
        from the interpreter's free list allocates nothing); the upper bound is the one that matters, since an
        undercount lets the memo hold more than its cap."""
        shapes = {
            "prompt": ([user(uid("sp", i), None, i, "please close out the retry ticket %d and update the notes" % i)
                        for i in range(1500)], "u"),
            "tool-call": ([assistant(uid("st", i), None, i,
                                     [{"type": "text", "text": "Running the retry tests."},
                                      {"type": "tool_use", "id": "toolu_%06d" % i, "name": "Bash",
                                       "input": {"command": "pytest -q tests/test_api.py -k retry_%d" % i}}])
                           for i in range(1500)], "a"),
        }
        for name, (recs, kind) in shapes.items():
            with self.subTest(shape=name):
                self.fresh()
                path = self.td / ("%s-%s.jsonl" % (SID, name))
                offs = write(path, recs)
                was = tracemalloc.is_tracing()
                if not was:
                    tracemalloc.start()
                try:
                    m0 = traced()
                    atoms = [lazy_atom(r, path, at, kind, own_uuid=True) for r, at in zip(recs, offs)]
                    self.assertEqual(em.hydrate(atoms, SID, by="test"), len(recs))
                    counted = em._HYDRATED_BYTES[0]
                    del atoms
                    held = traced() - m0
                finally:
                    if not was:
                        tracemalloc.stop()
                said = "%s: counted %d, held %d, per entry %.0f against %.0f" % (name, counted, held, counted / len(recs),
                                                                                  held / len(recs))
                self.assertEqual(len(em._HYDRATED), len(recs), said)
                self.assertTrue(0.85 <= held / counted <= 1.1, "the weight carries the entry's own parts: " + said)

    def test_the_size_walk_counts_each_object_once_and_skips_the_shared_singletons(self):
        """The weight's walk, exactly: every dict, list, key and value by sys.getsizeof; a key string two dicts share (the
        decoder shares a record's keys) and a container met twice, each once; None, the booleans, the small ints and the
        empty and one-character Latin-1 strings not at all, being the interpreter's shared objects; a one-character
        string outside Latin-1 is no such singleton and counts."""
        key = "".join(["tool", "_use_id"])                     # one key object, in both dicts
        text = "".join(["the retry ", "budget is three"])
        first = {key: "toolu_0001", "count": 1000}
        second = {key: "toolu_0002"}
        blocks = [first, second, second, None, True, False, 7, -5, 256, "", "a", "é", "—"]
        value = {"blocks": blocks, "text": text}
        size = sys.getsizeof
        want = (size(value) + size("blocks") + size("text") + size(text) + size(blocks)
                + size(first) + size(second) + size(key) + size("toolu_0001") + size("toolu_0002")
                + size("count") + size(1000) + size("—"))
        self.assertEqual(em._decoded_size(value), want)
        self.assertEqual(em._decoded_size(None), 0)
        self.assertEqual(em._decoded_size({}), size({}))

    def test_the_size_walk_runs_outside_the_memo_lock_and_inside_the_read_stripe(self):
        """The walk can cost several times the decode, so it never runs under the memo's lock, which every hydrate and
        every /perf read takes; it runs under the file's read stripe, where the put lands."""
        recs = [assistant(uid("walk", i), None, i, [{"type": "text", "text": "note %d on the retry budget" % i}])
                for i in range(3)]
        path = self.td / (SID + ".jsonl")
        offs = write(path, recs)
        atoms = [lazy_atom(r, path, at, "a") for r, at in zip(recs, offs)]
        stripe, real, seen = em._read_stripe(str(path)), em._decoded_size, []

        def watched(obj):
            seen.append((em._ASM_CKPT_LOCK.locked(), stripe._is_owned()))
            return real(obj)
        with mock.patch.object(em, "_decoded_size", watched):
            self.assertEqual(em.hydrate(atoms, SID, by="test"), 3)
        self.assertEqual(seen, [(False, True)] * 3, "(memo lock held, read stripe held) at each of the three walks")


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

    def test_a_put_over_a_peers_entry_moves_the_record_to_the_young_end(self):
        """The same race with another record put after the peer's: this thread's put replaces the peer's entry and the
        record ends the memo's order, the young end, rather than keeping the peer's older place."""
        rec = assistant(uid("young", 0), None, 0, [{"type": "text", "text": "the retry budget is three"}])
        other = uid("young", 1)
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        atom = lazy_atom(rec, path, at, "a")

        class PeerPutThenAnother(dict):
            misses = 0

            def get(self, k, default=None):
                hit = super().get(k, default)
                if hit is None and k == rec["uuid"]:
                    PeerPutThenAnother.misses += 1
                    if PeerPutThenAnother.misses == 2:
                        self[k] = ({"message": rec["message"]}, 5000)
                        self[other] = ({"message": {"role": "user", "content": "noted"}}, 300)
                        em._HYDRATED_BYTES[0] += 5300
                return hit

        memo = PeerPutThenAnother()
        with mock.patch.object(em, "_HYDRATED", memo):
            em.hydrate([atom], SID, by="test")
            self.assertEqual(list(memo), [other, rec["uuid"]], "the replaced record ends the memo's order")
        self.assertEqual(PeerPutThenAnother.misses, 2, "the peer's puts landed between the check and the put")

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

    def test_a_hit_moves_its_entry_to_the_young_end_so_the_cap_evicts_the_least_recently_used(self):
        """Under a cap of two equal entries, a hit on a fresh copy of the first and then a third record's put push out
        the second: the hit made the first the most recently used."""
        recs = [assistant(uid("lru", i), None, i, [{"type": "text", "text": "note %d on the retry budget" % i}])
                for i in range(3)]
        path = self.td / (SID + ".jsonl")
        offs = write(path, recs)
        first, third = recs[0]["uuid"], recs[2]["uuid"]

        def atom(i):
            return lazy_atom(recs[i], path, offs[i], "a")
        self.assertEqual(em.hydrate([atom(0), atom(1)], SID, by="test"), 2)
        weights = {w for _, w in em._HYDRATED.values()}
        self.assertEqual(len(weights), 1, "two entries of equal weight")
        with mock.patch.object(em, "_HYDRATED_CAP", 2 * weights.pop()):
            st0 = em.asm_checkpoint_stats()
            self.assertEqual(em.hydrate([atom(0)], SID, by="test"), 1)
            self.assertEqual(em.asm_checkpoint_stats()["hydratedHits"] - st0["hydratedHits"], 1, "a hit on the first")
            self.assertEqual(em.hydrate([atom(2)], SID, by="test"), 1)
            self.assertEqual(list(em._HYDRATED), [first, third], "the second, least recently used, was pushed out")

    def test_a_body_a_peer_put_while_this_call_waited_counts_as_a_serve(self):
        """Two hydrations of the same atoms (the judges' unit text and a chat build) both miss at hydrate's first check;
        the second then waits on the file's read stripe, and by the time it holds it the first has put the body. That
        re-check is a fill served without a read, a hit. A memo hit for an atom whose body a peer had already written
        when this call looked is not counted, on either check: the peer's own read or hit counted that body. That covers
        a body in place with its marker pop still in flight, completed from the memo, and an atom the peer finished
        whole between hydrate's filter and its first check, which this call does not touch."""
        rec = assistant(uid("wait", 0), None, 0, [{"type": "text", "text": "the retry budget is three"}])
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        entry = ({"message": rec["message"]}, 5000)

        class PeerPutMeanwhile(dict):
            """A memo that misses the record at this call's first check and holds it at the next: a peer's put lands
            between the two, and with `fills` set the peer's fill of that atom's body lands too, its marker pop still in
            flight."""
            gets = 0
            fills = None

            def get(self, k, default=None):
                if k == rec["uuid"]:
                    PeerPutMeanwhile.gets += 1
                    if PeerPutMeanwhile.gets == 2:
                        self[k] = entry
                        em._HYDRATED_BYTES[0] += entry[1]
                        if PeerPutMeanwhile.fills is not None:
                            PeerPutMeanwhile.fills["message"] = em._norm_message(rec["message"])
                return super().get(k, default)

        class PeerFinishes(dict):
            """A memo holding the entry, whose first get for the record is the instant a peer's fill finishes the atom:
            its body written and its marker popped, after hydrate's filter."""
            atom = None

            def get(self, k, default=None):
                a = PeerFinishes.atom
                if k == rec["uuid"] and a is not None and "lazy" in a:
                    a["message"] = em._norm_message(rec["message"])
                    a.pop("lazy")
                return super().get(k, default)

        def run(atom, memo):
            st0 = em.asm_checkpoint_stats()
            with mock.patch.object(em, "_HYDRATED", memo):
                self.assertEqual(em.hydrate([atom], SID, by="test"), 1)
            st = em.asm_checkpoint_stats()
            return {k: st[k] - st0[k] for k in ("hydratedAtoms", "hydratedHits", "hydratedEvicted")}

        def meanwhile(atom, fills=False):
            PeerPutMeanwhile.gets = 0
            PeerPutMeanwhile.fills = atom if fills else None
            got = run(atom, PeerPutMeanwhile())
            self.assertEqual(PeerPutMeanwhile.gets, 2, "the peer's put landed between the first check and the second")
            return got

        waited = lazy_atom(rec, path, at, "a")
        self.assertEqual(meanwhile(waited), {"hydratedAtoms": 0, "hydratedHits": 1, "hydratedEvicted": 0},
                         "the re-check under the read stripe: one fill served, no read")
        self.assertEqual(waited["message"], em._norm_message(rec["message"]))
        self.assertNotIn("lazy", waited)
        raced = lazy_atom(rec, path, at, "a")
        self.assertEqual(meanwhile(raced, fills=True), {"hydratedAtoms": 0, "hydratedHits": 0, "hydratedEvicted": 0},
                         "the peer's put and its fill both landed while this call waited: completed, not counted")
        self.assertEqual(raced["message"], em._norm_message(rec["message"]))
        self.assertNotIn("lazy", raced)
        finished = lazy_atom(rec, path, at, "a")
        finished["message"] = em._norm_message(rec["message"])     # a peer's fill wrote the body; its marker pop is in flight
        self.assertEqual(meanwhile(finished), {"hydratedAtoms": 0, "hydratedHits": 0, "hydratedEvicted": 0},
                         "an atom a peer finished, met at the in-place branch's second look: completed from the memo, "
                         "not counted")
        self.assertNotIn("lazy", finished)
        in_place = lazy_atom(rec, path, at, "a")
        in_place["message"] = em._norm_message(rec["message"])     # the same, met at the first check of a warm memo
        self.assertEqual(run(in_place, dict({rec["uuid"]: entry})),
                         {"hydratedAtoms": 0, "hydratedHits": 0, "hydratedEvicted": 0},
                         "an atom a peer finished, met at the first check: completed from the memo, not counted")
        self.assertNotIn("lazy", in_place)
        whole = lazy_atom(rec, path, at, "a")
        PeerFinishes.atom = whole
        self.assertEqual(run(whole, PeerFinishes({rec["uuid"]: entry})),
                         {"hydratedAtoms": 0, "hydratedHits": 0, "hydratedEvicted": 0},
                         "an atom a peer finished whole after the filter: nothing for this call to fill, not counted")
        self.assertEqual(whole["message"], em._norm_message(rec["message"]))
        self.assertNotIn("lazy", whole)

    def test_the_default_cap_is_a_quarter_gib_of_real_memory_or_a_48th_of_the_machine(self):
        if os.environ.get("ROMP_HYDRATED_CAP_MB"):
            self.skipTest("ROMP_HYDRATED_CAP_MB sets the cap outright in this environment")
        self.assertEqual(em._HYDRATED_CAP, max(256 * MIB, em._machine_memory_bytes() // 48))


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

    def test_a_consumed_tool_result_reaches_every_atom_of_its_record_whichever_atom_read_it(self):
        """One entry serves every atom of its record, so a tool result of a consumed shape (a question's answers) is
        kept whatever the marker of the atom whose read filled the memo says. Read first through an atom whose marker
        does not carry it, the record then fills an atom whose marker does (lazy tur) from a hit, tool result and all, as
        the whole record would."""
        tur = {"answers": {"Which store?": "the first"}, "questions": [{"question": "Which store?"}]}
        rec = user(uid("tur", 0), None, 0, [{"type": "tool_result", "tool_use_id": "toolu_0009", "content": "answered"}],
                   tur=tur)
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        plain = lazy_atom(rec, path, at, "u")
        self.assertEqual(em.hydrate([plain], SID, by="test"), 1)
        self.assertNotIn("toolUseResult", plain, "this atom's marker does not carry it")
        want = lazy_atom(rec, path, at, "u", tur=True)
        em._hydrate_one(want, json.loads(json.dumps(rec)))
        carried = lazy_atom(rec, path, at, "u", tur=True)
        read0 = em._ASM_CKPT_STATS["hydratedBytes"]
        self.assertEqual(em.hydrate([carried], SID, by="test"), 1)
        self.assertEqual(em._ASM_CKPT_STATS["hydratedBytes"], read0, "filled from the memo: no bytes read")
        self.assertEqual(carried.get("toolUseResult"), tur, "the hit carries the record's tool result")
        self.assertEqual(bodies(carried), bodies(want), "a hit fills as the whole record does")

    def test_an_attachment_that_is_not_a_dict_fails_the_fill_as_the_whole_record_does(self):
        """A malformed attachment (a string, a list) is kept as it is, so a miss and a hit fail the fill exactly as the
        fill from the whole record does, rather than quietly filling an empty message."""
        for i, bad in enumerate(("queued", ["also run the api tests"])):
            with self.subTest(attachment=bad):
                rec = _meta(uid("badatt", i), None, i, "attachment")
                rec["attachment"] = bad
                path = self.td / ("%s-%d.jsonl" % (SID, i))
                at = write(path, [rec])[0]
                with self.assertRaises(Exception) as ref:
                    em._hydrate_one(lazy_atom(rec, path, at, "b"), json.loads(json.dumps(rec)))
                miss, hit = lazy_atom(rec, path, at, "b"), lazy_atom(rec, path, at, "b")
                with self.assertRaises(type(ref.exception)) as got:
                    em.hydrate([miss], SID, by="test")
                self.assertEqual(str(got.exception), str(ref.exception), "a miss fails as the whole record does")
                self.assertIn(rec["uuid"], em._HYDRATED, "the read was memoized before the fill")
                read0 = em._ASM_CKPT_STATS["hydratedBytes"]
                with self.assertRaises(type(ref.exception)) as got:
                    em.hydrate([hit], SID, by="test")
                self.assertEqual(str(got.exception), str(ref.exception), "a hit fails as the whole record does")
                self.assertEqual(em._ASM_CKPT_STATS["hydratedBytes"], read0, "the hit read nothing")
                self.assertIn("lazy", miss)
                self.assertIn("lazy", hit)

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

    def test_the_record_read_by_offset_fails_loudly_once_its_file_has_moved(self):
        """A transcript rewritten at the same size since its records were hydrated, or cut short: the by-offset read
        raises the lookup's miss error, which the comment cut catches before it reads the transcript, rather than
        answering from a copy of a file that is gone."""
        rec = user(uid("moved", 0), None, 0, "where does the retry budget live?")
        other = user(uid("moved", 1), None, 0, "where does the retry budget live?")
        path = self.td / (SID + ".jsonl")
        at = write(path, [rec])[0]
        em.hydrate([lazy_atom(rec, path, at, "u")], SID, by="test")
        self.assertIn(rec["uuid"], em._HYDRATED)
        size = os.path.getsize(path)
        write(path, [other])
        self.assertEqual(os.path.getsize(path), size, "rewritten at the same size, another record at the offset")
        with self.assertRaises(em.GraphMiss):
            em._read_record_at(str(path), at, rec["uuid"])
        write(path, [rec])
        os.truncate(path, at[1] // 2)
        with self.assertRaises(em.GraphMiss):
            em._read_record_at(str(path), at, rec["uuid"])

    def test_the_record_read_by_offset_does_not_wait_on_the_files_read_stripe(self):
        """The by-offset read fills no cache, so it takes no read stripe: a comment cut reading a pre-cut record answers
        while a peer holds the stripe for the same path (a hydrate group, a record-cache read), rather than waiting
        for it inside its entry's key lock."""
        rec = user(uid("stripe", 0), None, 0, "where does the retry budget live?")
        path = str(self.td / (SID + ".jsonl"))
        at = write(path, [rec])[0]
        held, release, done, out = threading.Event(), threading.Event(), threading.Event(), {}

        def peer():
            with em._read_stripe(path):
                held.set()
                release.wait(30)

        def reader():
            out["rec"] = em._read_record_at(path, at, rec["uuid"])
            done.set()
        holder = threading.Thread(target=peer, daemon=True)
        holder.start()
        try:
            self.assertTrue(held.wait(10), "the peer holds the stripe")
            threading.Thread(target=reader, daemon=True).start()
            self.assertTrue(done.wait(5), "the read answered while the peer held the file's read stripe")
        finally:
            release.set()
            holder.join(10)
        self.assertTrue(done.wait(10))
        self.assertEqual(out["rec"], rec)


if __name__ == "__main__":
    unittest.main()
