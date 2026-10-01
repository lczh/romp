#!/usr/bin/env python3
"""A salvaged reply that carries a transcript record's uuid must not keep a leaf from ever getting its assembly document.

The SDK backend salvages a reply that streamed but that the transcript never kept as an assistant record: an orphanReply
marker in states/<sid>.jsonl, which the parse turns into an assistant atom (synthesize_orphans) under the marker's uuid. A
local command's streamed output (a /cost, say) is such a reply, and the transcript records it under the SAME uuid as a
system/local_command row, which the emit skips. The writer's turns section looked every atom up by uuid, found a transcript
record with no atom row of its own, and refused the whole document as `turnRows` ("the tree and the entry disagree").
That refusal is not tied to a cut, so it repeated at every settle and every converge pass for the session's life: a
203 MB live leaf carried no document at all, and every kernel boot read it whole (about 10 s of a single core, minutes
under the boot's contention; measured in place on the machine that holds it, 2026-10-01). A salvaged reply is a
synthesized atom whatever uuid it carries; it gets a synthesized row like an idle span, and the restored tree equals the
cold parse. Synthetic records only: invented notes-api text, placeholder uuids."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_asm_checkpoint as T   # noqa: E402  the harness, the builders and the shared event-model module
import test_event_model_golden as G   # noqa: E402

em, NOW, SID = T.em, T.NOW, T.SID


def _records():
    """Three settled turns, the middle one a /cost command whose output the transcript keeps as a local_command row under
    `lc2`, then a compaction and settled turns after it, so the command turn is well before the writer's cut."""
    t = NOW - 7200
    recs = [G.uline(t, "list the open notes-api routes", "u1", None),
            G.aline(t + 5, "three routes are open: notes, tags and search", "a1", "u1", stop="end_turn"),
            {"type": "user", "timestamp": G.iso(t + 60), "uuid": "cmd2", "parentUuid": "a1",
             "message": {"role": "user", "content": "<command-name>/cost</command-name>"}},
            G.sysline(t + 61, "local_command", "lc2", "cmd2", content="<local-command-stdout>Total cost: $0.42</local-command-stdout>"),
            G.uline(t + 120, "add pagination to the search route", "u3", "lc2"),
            G.aline(t + 130, "search now pages by cursor", "a3", "u3", stop="end_turn")]
    recs = T.compacting_variant(recs, "c")
    return recs + T._turns_after(recs, "after", 3)


STATES = [{"t": NOW - 7200 + 61, "orphanReply": {"uuid": "lc2", "text": "Total cost: $0.42"}}]   # the salvaged /cost output


class SalvagedReplyUnderARecordUuid(T.Harness):
    def setUp(self):
        super().setUp()
        self.path = self.write("salvaged-reply", _records(), states=STATES)

    def test_the_salvaged_reply_is_in_the_parse_under_the_records_uuid(self):
        """The precondition, pinned: the parse carries the salvaged reply as an assistant atom whose uuid is the local_command
        record's (the collision the writer has to survive), in a turn before the compaction."""
        tree = self.parse(self.path)
        hits = [(i, a) for i, turn in enumerate(tree["turns"]) for a in turn["atoms"] if a.get("uuid") == "lc2"]
        self.assertEqual(len(hits), 1, "one atom under lc2: %s" % hits)
        self.assertEqual(hits[0][1].get("type"), "assistant")
        self.assertTrue(hits[0][1].get("orphaned"), "the atom is the salvaged reply, not a record of its own")

    def test_the_document_is_written_and_restores_to_the_cold_parse(self):
        cold = self.cold(self.path)
        self.fresh()
        self.parse(self.path)
        em._ASM_CKPT_STATS["skipped"] = {}
        reasons = []
        wrote = em.asm_checkpoint_write(self.path, SID, tree=self._trees.get(self.path), reason_out=reasons)
        self.assertTrue(wrote, "the document is written; refused for %s (skipped %s)" % (reasons, em.asm_checkpoint_stats()["skipped"]))
        self.assertNotIn("turnRows", em.asm_checkpoint_stats()["skipped"])
        again = []
        self.assertFalse(em.asm_checkpoint_write(self.path, SID, tree=self._trees.get(self.path), reason_out=again))
        self.assertEqual(again, ["written"], "the next settle skips at once: before, every settle and converge pass rebuilt the "
                                             "whole document on the pusher and refused it again")
        doc = T._doc(self.path)
        self.assertTrue(doc.get("turns"), "the turns section is written (the restore builds the pre-cut turns from it)")
        syn = [json.loads(r) for r in doc["atoms"] if json.loads(r).get("syn")]
        self.assertTrue(any((r.get("s") or {}).get("uuid") == "lc2" and (r.get("s") or {}).get("orphaned") for r in syn),
                        "the salvaged reply rides as a synthesized row: %s" % syn)
        tree, modes, n_lazy = self.restored(self.path)
        self.assertEqual(modes, ["restore"], "the next process restores from the document")
        self.assertGreater(n_lazy, 0, "the pre-cut atoms come lazily from the document")
        self.assertEqual(tree, cold, "the restored tree, hydrated, equals the cold parse")
        cut = int(doc["files"][os.path.basename(self.path)[:-len(".jsonl")]]["cut"][0])
        self.assertLessEqual(self.read_before, os.path.getsize(self.path) - cut + 4096,
                             "the restore read the tail past the cut and its guard bytes, never the whole leaf")


def _twin_records():
    """The other collision: the CLI kept the streamed reply as an EMPTY assistant record (a thinking block with no text) under
    the marker's uuid, so the record has an atom and a row of its own and the salvaged reply shares its uuid."""
    t = NOW - 7200
    empty = G.aline(t + 70, "", "a2", "u2", thinking="  ")
    recs = [G.uline(t, "list the open notes-api routes", "u1", None),
            G.aline(t + 5, "three routes are open: notes, tags and search", "a1", "u1", stop="end_turn"),
            G.uline(t + 60, "which one is slowest?", "u2", "a1"), empty,
            G.uline(t + 120, "add pagination to the search route", "u3", "a2"),
            G.aline(t + 130, "search now pages by cursor", "a3", "u3", stop="end_turn")]
    recs = T.compacting_variant(recs, "c")
    return recs + T._turns_after(recs, "after", 3)


TWIN_STATES = [{"t": NOW - 7200 + 70, "orphanReply": {"uuid": "a2", "text": "search is the slowest: it scans every note"}}]


class SalvagedReplyBesideItsEmptyTwin(T.Harness):
    def test_both_atoms_keep_their_own_rows_and_restore_to_the_cold_parse(self):
        path = self.write("salvaged-twin", _twin_records(), states=TWIN_STATES)
        tree = self.parse(path)
        under = [a for turn in tree["turns"] for a in turn["atoms"] if a.get("uuid") == "a2"]
        self.assertEqual(sorted(bool(a.get("orphaned")) for a in under), [False, True],
                         "the precondition: the empty record's atom and the salvaged reply share the uuid: %s" % under)
        cold = self.cold(path)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "written: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertTrue(T._doc(path).get("turns"), "with its turns section (the twin no longer takes the record's row, which "
                                                   "failed the coverage check and dropped the section)")
        restored, modes, _n = self.restored(path)
        self.assertEqual(modes, ["restore"])
        self.assertEqual(restored, cold, "the restored tree, hydrated, equals the cold parse")


if __name__ == "__main__":
    unittest.main()
