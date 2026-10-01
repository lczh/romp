#!/usr/bin/env python3
"""A whole assembly entry re-seats on the document written from it (the 2026-09-24 investigation of the kernel's whole
re-parses). An entry built by a whole parse (a boot with no document, a compaction, a demotion with none standing) kept
every record and atom for the life of the process: the settle and the converge pass wrote the leaf's assembly document FROM
it and left it whole, so every later fold re-ran the graph passes over every record and copied every atom, and every serve
segmented the whole history again (on synthetic transcripts of 150 MB, a fold an order of magnitude dearer than one over a
restored entry), and the chat's render floor stayed at turn 0. Pinned here, by the parse's own road counters and the
entry's size on a synthetic episode: once the document stands, the next parse takes the restore road from it, the tree is
the cold parse's, and the folds after it walk the tail alone. And the rules that keep the re-seat from costing more than it
saves, or from serving less than the whole entry did: the re-seated entry carries the churn bound (its cut still advances
once the tail reaches the share), also through a restore after a descent; an entry whose document's tail already meets
the share is not re-seated; a re-seat the restore refuses, or an entry whose leaf's document a restore just refused, stays
whole, and the writer's churn hold keeps its document as it is until the share; and an entry whose postal author still
waits on the log is not re-seated. Synthetic transcripts only: invented text in the notes-api demo world, private
placeholder ids."""
import gzip
import json
import os
import random
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
# Hermetic state BEFORE the load: the event model resolves its state root at import time, and only pytest runs conftest's
# floor (a bare unittest or script run would otherwise write REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))

SID = "5ea70924-2222-4333-8444-00000000a5e1"          # this module's own placeholder session id
NOW = 1781200000
PEER = "5ea70924-2222-4333-8444-00000000a5e2"         # a peer session's placeholder id (the postal sender)
MSG = "1781200000.222_444.TESTHOST"                   # a synthetic postal message id
WORDS = ("notes", "search", "index", "query", "cursor", "page", "cache", "retry", "fixture", "route", "schema", "review")


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _words(rnd, n):
    return " ".join(rnd.choice(WORDS) for _ in range(n))


def episode(t0, turns=240, compact_every=80, tool_every=6, seed=24):
    """A long synthetic session: typed prompts and prose replies, a Bash call and its result every `tool_every` turns, and
    an automatic compaction (the boundary and its summary) every `compact_every` turns."""
    rnd = random.Random(seed)
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "b%d" % k, "s%d" % k
            recs.append({"type": "system", "subtype": "compact_boundary", "uuid": b, "parentUuid": None, "logicalParentUuid": parent,
                         "timestamp": iso(t), "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}})
            recs.append({"type": "user", "uuid": s, "parentUuid": b, "timestamp": iso(t + 1), "isCompactSummary": True,
                         "message": {"role": "user", "content": "summary so far: " + _words(rnd, 80)}})
            parent = s; t += 2
        u = "u%d" % k
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
                     "message": {"role": "user", "content": "%s %d" % (_words(rnd, 24), k)}})
        parent = u
        if k % tool_every == 0:
            c, r = "c%d" % k, "r%d" % k
            recs.append({"type": "assistant", "uuid": c, "parentUuid": parent, "timestamp": iso(t + 5), "cwd": "/w/notes-api",
                         "message": {"id": "msg_c%d" % k, "role": "assistant", "stop_reason": "tool_use",
                                     "content": [{"type": "tool_use", "id": "toolu_%d" % k, "name": "Bash",
                                                  "input": {"command": "uv run pytest -q tests/test_search.py"}}]}})
            out = "%d passed\n%s" % (10 + k % 7, _words(rnd, 60))
            recs.append({"type": "user", "uuid": r, "parentUuid": c, "sourceToolAssistantUUID": c, "timestamp": iso(t + 9),
                         "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False},
                         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_%d" % k, "content": out}]}})
            parent = r
        a = "a%d" % k
        recs.append({"type": "assistant", "uuid": a, "parentUuid": parent, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
                     "message": {"id": "msg_a%d" % k, "role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": _words(rnd, 90)}]}})
        parent = a; t += 60
    return recs


def tool_round(t, k, parent, width=400):
    """One call and its result inside a turn that stays open (the call's stop reason is tool_use, no reply follows yet)."""
    c, r = "oc%d" % k, "or%d" % k
    out = "%d passed\n%s" % (k, _words(random.Random(k), width))
    return [{"type": "assistant", "uuid": c, "parentUuid": parent, "timestamp": iso(t), "cwd": "/w/notes-api",
             "message": {"id": "msg_oc%d" % k, "role": "assistant", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": "toolu_o%d" % k, "name": "Bash",
                                      "input": {"command": "uv run pytest -q tests/test_index_%d.py" % k}}]}},
            {"type": "user", "uuid": r, "parentUuid": c, "sourceToolAssistantUUID": c, "timestamp": iso(t + 4),
             "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False},
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_o%d" % k, "content": out}]}}]


def spur(t, opener):
    """An api_error spur as the CLI writes one when a request fails: the error record parented at the turn's opener (where
    the request started), and the next prompt chained onto the error record. The new leaf does not chain back to the old
    one through the delta, and nothing in it hangs off a tool call, so the fold's gate demotes the entry for descent."""
    return [{"type": "system", "subtype": "api_error", "uuid": "de1", "parentUuid": opener, "level": "error", "timestamp": iso(t),
             "error": {"type": "overloaded_error"}},
            {"type": "user", "uuid": "du1", "parentUuid": "de1", "timestamp": iso(t + 10), "promptSource": "typed", "cwd": "/w/notes-api",
             "message": {"role": "user", "content": "and the export endpoint of the notes-api?"}}]


def rewind(t, before):
    """A rewound prompt: the last prompt asked again, parented at the record before it (`before`), so the last exchange
    leaves the active branch and the new leaf does not chain back to the old one (a descent)."""
    return [{"type": "user", "uuid": "du1", "parentUuid": before, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
             "message": {"role": "user", "content": "actually, page the search index by cursor instead"}}]


def turn(t, k, parent):
    """One more settled turn chained onto `parent`: a typed prompt and its reply."""
    u, a = "xu%d" % k, "xa%d" % k
    return [{"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t), "promptSource": "typed", "cwd": "/w/notes-api",
             "message": {"role": "user", "content": "follow-up %d on the notes-api search" % k}},
            {"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t + 20), "cwd": "/w/notes-api",
             "message": {"id": "msg_x%d" % k, "role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "follow-up %d is done: the cursor pages the index" % k}]}}]


def _strip(tree):
    """A tree as JSON compares it (the stage 4a harness's rule): a restored tree's pre-cut turns built into plain turns, the
    lazy markers dropped once hydrated, the cut turn left out (a restored tree's own fact)."""
    t = json.loads(json.dumps(em.plain_tree(tree), default=lambda o: "<unserializable>"))
    t.pop("cutTurn", None)
    for tr in t["turns"]:
        for a in tr["atoms"]:
            a.pop("lazy", None)
    return t


class Episode(unittest.TestCase):
    def setUp(self):
        self.td = Path(tempfile.mkdtemp()).resolve()      # the real path: the reader's key and the assembly cache's realpath key agree
        self.ck = self.td / "checkpoints"
        em.set_checkpoint_dir(lambda: self.ck)
        self.leaf = str(self.td / (SID + ".jsonl"))
        self.recs = episode(NOW - 86400)
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in self.recs))
        self.k = 0
        self.postal = []                                   # the postal log's rows, as the parse reads them
        self.fresh()

    def tearDown(self):
        em.set_checkpoint_dir(None)
        self.fresh()
        shutil.rmtree(self.td, ignore_errors=True)

    def fresh(self):
        """A kernel restart's in-memory side: every reader entry, assembly entry, hydrated body and memoized document gone."""
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
        with em._ASM_LOCK:
            em._ASM_CACHE.clear()
        em._TRAILING_CACHE.clear()
        with em._ASM_CKPT_LOCK:
            em._HYDRATED.clear(); em._HYDRATED_BYTES[0] = 0
            em._ASM_DOC_MEMO.clear(); em._ASM_DOC_MEMO_BYTES[0] = 0
        em._LAZY_FILES.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()

    def append(self, recs):
        self.recs += recs
        with open(self.leaf, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))

    def begin(self, recs):
        """Start the leaf over with `recs` (before any parse of it)."""
        self.recs = list(recs)
        Path(self.leaf).write_text("".join(json.dumps(r) + "\n" for r in self.recs))

    def last_uuid(self):
        return next(r["uuid"] for r in reversed(self.recs) if r.get("uuid"))

    def last_t(self):
        return max(em.parse_z(r["timestamp"]) for r in self.recs if r.get("timestamp"))

    def append_turn(self):
        """A settled turn on the leaf, stamped past every record so far (the fold's stamp gate holds)."""
        self.k += 1
        self.append(turn(self.last_t() + 60, self.k, self.last_uuid()))

    def doc_cut(self):
        """The leaf's cut offset in the standing document: where the pre-cut bytes end."""
        d = json.loads(gzip.decompress(em._asm_ckpt_file(self.leaf).read_bytes()))
        return int(d["files"][Path(self.leaf).stem]["cut"][0])

    def parse(self):
        mode = []
        out = em.parse_session(self.leaf, rompuuid=SID, name="web", dir="/w/notes-api", candidate_files=[self.leaf],
                               states=None, postal_log=list(self.postal), now=NOW, asm_mode_out=mode)
        return out, (mode[-1] if mode else None)

    def settle(self, tree):
        """The settle's write: the leaf's document from its assembly entry, with the tree the parse store holds."""
        return em.asm_checkpoint_write(self.leaf, SID, tree=tree)

    def doc_bytes(self):
        return em._asm_ckpt_file(self.leaf).read_bytes()

    def grow_to_the_share(self, base=1000):
        """Append settled turns until the tail past the standing document's cut reaches the churn bound's share; returns the
        cut it was measured from."""
        cut0 = self.doc_cut()
        grow = []
        for k in range(4000):                                   # loop-ok: bounded; the share is reached long before
            if (os.path.getsize(self.leaf) + sum(len(json.dumps(r)) + 1 for r in grow) - cut0) * em._ASM_TAIL_SHARE >= cut0:
                break
            grow += turn(self.last_t() + 60 * (len(grow) + 1), base + k, grow[-1]["uuid"] if grow else self.last_uuid())
        self.append(grow)
        return cut0

    @staticmethod
    def skipped():
        return dict(em.asm_checkpoint_stats()["skipped"])

    def entry(self):
        with em._ASM_LOCK:
            return em._ASM_CACHE.get((os.path.realpath(self.leaf), SID, False))

    def cold(self):
        """The reference: a whole parse from zero in a fresh process with no document, hydrated and stripped."""
        self.fresh()
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None
        try:
            out, _ = self.parse()
        finally:
            em._CKPT_DIR_FN = saved
        return _strip(out)

    def assertColdEqual(self, tree):
        em.hydrate(tree, SID)
        got = _strip(tree)
        want = self.cold()
        self.assertEqual(len(got["turns"]), len(want["turns"]), "as many turns as the cold parse")
        self.assertEqual([t["id"] for t in got["turns"]], [t["id"] for t in want["turns"]], "the cold parse's turn ids, in order")
        self.assertEqual(got, want, "and the cold parse's tree, atom for atom and body for body")

    @staticmethod
    def stats():
        return dict(em._ASM_STATS)

    @staticmethod
    def moved(a, b):
        return {k: b.get(k, 0) - a.get(k, 0) for k in set(a) | set(b) if b.get(k, 0) != a.get(k, 0)}


class WholeEntryReseats(Episode):
    """The defect itself: the settle writes the document from a whole entry, and the next parse still folds the whole entry."""

    def test_after_the_settle_writes_the_document_the_next_parse_restores_from_it(self):
        out, mode = self.parse()
        self.assertEqual(mode, "full", "no document yet: a whole parse")
        self.assertTrue(self.settle(out), "the settle wrote the document: %s" % em.asm_checkpoint_stats()["skipped"])
        self.append_turn()
        s0 = self.stats()
        out2, mode2 = self.parse()
        d = self.moved(s0, self.stats())
        self.assertEqual(mode2, "restore", "once its document stands the entry takes the restore road from it; on main the whole "
                         "entry keeps folding: road %s, cutTurn %r, %d turns, counters moved %s"
                         % (mode2, out2.get("cutTurn"), len(out2["turns"]), d))
        self.assertEqual((d.get("g:reseat"), d.get("restore:afterDemote"), d.get("full", 0)), (1, 1, 0), "counted as a re-seat: %s" % d)
        self.assertGreater(out2.get("cutTurn", 0), 0, "the render floor moved from turn 0 to the cut")
        self.assertColdEqual(out2)

    def test_the_folds_after_the_reseat_walk_the_tail_alone(self):
        out, _ = self.parse()
        whole = self.entry()
        n_recs, n_atoms = len(whole["ad"].by_uuid), len(whole["atoms"])
        self.assertTrue(self.settle(out))
        walked, modes = [], []
        for _k in range(3):
            self.append_turn()
            out, m = self.parse()
            modes.append(m)
            e = self.entry()
            walked.append((len(e["ad"].by_uuid), len(e["atoms"])))
        for i, (recs_n, atoms_n) in enumerate(walked):
            self.assertLess(recs_n * 10, n_recs, "parse %d after the write walked %d records and copied %d atoms of the whole "
                            "entry's %d and %d (roads %s): every fold re-derived the whole history" % (i + 1, recs_n, atoms_n, n_recs, n_atoms, modes))
            self.assertLess(atoms_n * 10, n_atoms, "parse %d copied %d atoms of %d" % (i + 1, atoms_n, n_atoms))
        self.assertEqual(modes, ["restore", "fold", "fold"], "one re-seat, then folds over the restored entry")
        self.assertColdEqual(out)


class ReseatKeepsTheChurnBound(Episode):
    """The whole entry carried the churn bound in the writer (a standing document is rewritten once the tail past its cut
    reaches an eighth of the pre-cut bytes); a re-seated entry carries it in the fold's gate, so its leaf's cut still
    advances, and an entry whose document's tail already meets the share is not re-seated at all."""

    def test_a_reseated_entry_whose_tail_reaches_the_share_is_parsed_whole_and_its_cut_advances(self):
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        cut0 = self.doc_cut()
        self.append_turn()
        _, m1 = self.parse()
        self.assertEqual(m1, "restore", "re-seated on the document")
        grow = []
        for k in range(4000):                                   # loop-ok: bounded; the share is reached long before
            if (os.path.getsize(self.leaf) + sum(len(json.dumps(r)) + 1 for r in grow) - cut0) * em._ASM_TAIL_SHARE >= cut0:
                break
            grow += turn(self.last_t() + 60 * (len(grow) + 1), 1000 + k, grow[-1]["uuid"] if grow else self.last_uuid())
        self.append(grow)
        s0 = self.stats()
        out, m2 = self.parse()
        d = self.moved(s0, self.stats())
        self.assertEqual((m2, d.get("g:tailShare")), ("full", 1), "the tail past the cut reached the share: the re-seated entry "
                         "is demoted to the whole parse, as the whole entry's writer rewrote at the share (%s)" % d)
        self.assertTrue(self.settle(out), "the settle after the whole parse writes: %s" % em.asm_checkpoint_stats()["skipped"])
        self.assertGreater(self.doc_cut(), cut0 + (os.path.getsize(self.leaf) - cut0) // 2, "the cut advanced past the grown tail")
        self.append_turn()
        out, m3 = self.parse()
        self.assertEqual(m3, "restore", "and the next parse re-seats on the new document")
        self.assertColdEqual(out)

    def test_a_document_whose_tail_already_meets_the_share_leaves_the_entry_whole(self):
        """A turn that stays open (a long run of tool calls) keeps the cut before the last settled turn while the tail grows;
        re-seated there, the restored entry would demote at its first fold and the whole parse would write the same cut again
        at every settle. The entry stays whole and folds, as before the re-seat."""
        recs = episode(NOW - 86400, turns=40, compact_every=1000)
        t = max(em.parse_z(r["timestamp"]) for r in recs) + 60
        opener = {"type": "user", "uuid": "ou", "parentUuid": recs[-1]["uuid"], "timestamp": iso(t), "promptSource": "typed",
                  "cwd": "/w/notes-api", "message": {"role": "user", "content": "run the whole index suite, file by file"}}
        recs.append(opener)
        for k in range(6):
            recs += tool_round(t + 10 * (k + 1), k, recs[-1]["uuid"])
        self.begin(recs)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.assertGreaterEqual((os.path.getsize(self.leaf) - self.doc_cut()) * em._ASM_TAIL_SHARE, self.doc_cut(),
                                "the fixture's premise: the open turn's tail already meets the share")
        s0 = self.stats()
        modes = []
        for k in range(6, 10):
            self.append(tool_round(self.last_t() + 10, k, self.last_uuid()))
            out, m = self.parse()
            modes.append(m)
            self.settle(out)                                    # the settle writes at every states row of the open turn
        d = self.moved(s0, self.stats())
        self.assertEqual(modes, ["fold"] * 4, "no re-seat, no whole parse per call of the open turn: %s" % d)
        self.assertEqual((d.get("full", 0), d.get("g:reseat", 0)), (0, 0), "%s" % d)
        self.assertColdEqual(out)


def _refused_with_its_document(key, leaf_path, *a, **k):
    """A restore that refuses the leaf's document the way a document that does not verify is refused: counted, said once,
    and removed from disk."""
    em._asm_ckpt_note(leaf_path, "identity")
    return None


class ARefusedReseatStaysWhole(Episode):
    """A re-seat whose restore does not serve must not become a loop (a document written at every settle, refused at the next
    miss, and the history parsed whole again): the entry the whole parse builds after the refusal stays whole and folds.
    Such an entry is where the writer's churn hold still governs (a whole entry with a standing document rewrites it only
    once the tail past its cut reaches the share or a compaction lands past it), so both tests here pin the hold too: every
    settle after the entry's first write leaves the document as it is (`written`), where a rewrite would cost a whole
    document build per settled turn (review of 2026-09-24, medium 2)."""

    def test_a_reseat_the_restore_refuses_is_not_tried_again_for_that_entry(self):
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        with mock.patch.object(em, "_asm_restore", _refused_with_its_document):
            s0, k0 = self.stats(), self.skipped()
            modes, wrote, doc = [], [], None
            for _k in range(4):
                self.append_turn()
                out, m = self.parse()
                modes.append(m)
                wrote.append(self.settle(out))                  # the settle runs after every turn
                if doc is None:
                    doc = self.doc_bytes()
            d, k = self.moved(s0, self.stats()), self.moved(k0, self.skipped())
        self.assertEqual(modes, ["full", "fold", "fold", "fold"], "one refused re-seat, one whole parse, then folds: %s" % d)
        self.assertEqual(d.get("g:reseat"), 1, "the re-seat is tried once: %s" % d)
        self.assertEqual((wrote, k), ([True, False, False, False], {"written": 3}), "the whole parse's entry writes its document "
                         "once; the churn hold keeps it while the tail is under the share (settles %s, skipped %s)" % (wrote, k))
        self.assertEqual(self.doc_bytes(), doc, "the document is the one written after the whole parse")
        self.assertColdEqual(out)

    def test_after_a_restore_refused_a_standing_document_the_whole_entry_is_not_reseated(self):
        """A boot whose restore the tail's shape refuses (the chain proof; the document stands) parses whole and rewrites the
        document at once (the refusal's own write); the entry that parse built is not re-seated on it, since the same tail
        refuses it again."""
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.fresh()                                            # a restart: the next parse offers the document to the restore
        with mock.patch.object(em, "_tail_chains_onto_the_document", lambda *a, **k: False):
            s0 = self.stats()
            out, m0 = self.parse()
            doc, k0 = self.doc_bytes(), self.skipped()          # the refusal's own write, from the whole parse
            modes, wrote = [], []
            for _k in range(3):
                self.append_turn()
                out, m = self.parse()
                modes.append(m)
                wrote.append(self.settle(out))
            d, k = self.moved(s0, self.stats()), self.moved(k0, self.skipped())
        self.assertEqual((m0, d.get("restore:chainRefused")), ("full", 1), "the boot's restore refused, the whole parse served: %s" % d)
        self.assertEqual(modes, ["fold"] * 3, "the whole entry folds, never re-seated on a document its tail refuses: %s" % d)
        self.assertEqual(d.get("g:reseat", 0), 0, "%s" % d)
        self.assertEqual((wrote, k), ([False] * 3, {"written": 3}), "the churn hold keeps the standing document while the tail "
                         "is under the share (settles %s, skipped %s)" % (wrote, k))
        self.assertEqual(self.doc_bytes(), doc, "the document is the refusal's own write")
        self.assertColdEqual(out)


class ReseatSurvivesADescent(Episode):
    """An api_error spur off the last turn's opener with a prompt chained onto it, and a rewound prompt in the tail, each
    demote the entry for descent (the new leaf does not chain back to the old one through the delta), and a descent takes
    the restore road again. The churn bound has to ride that restore: a restore without it leaves a turns-section entry
    with no churn gate (`prefix` is empty), the writer skips it as restored, and its cut never moves again in the process
    (review of 2026-09-24, medium 1). Each shape in both places: after the re-seat, where the restored entry carries the
    re-seat's flag, and as the first delta after the write, which demotes the marked whole entry before any re-seat. Neither
    shape hangs off a tool call, so each stays a descent whatever the gate makes of a parallel tool batch's results."""

    def opener(self):
        """The last turn's typed prompt."""
        return next(r for r in reversed(self.recs) if r.get("type") == "user" and r.get("promptSource") == "typed")

    def descend(self, shape):
        """Append the descent of `shape` and parse (the premise: a descent demotion served by the restore), then the reply to
        the prompt it ends on, which folds."""
        t = self.last_t() + 60
        recs = spur(t, self.opener()["uuid"]) if shape == "spur" else rewind(t, self.opener()["parentUuid"])
        s0 = self.stats()
        self.append(recs)
        _, m = self.parse()
        d = self.moved(s0, self.stats())
        self.assertEqual((m, d.get("g:descent", 0), d.get("restore:afterDemote", 0)), ("restore", 1, 1), "the fixture's premise: "
                         "the %s demotes the entry for descent and the restore serves it (%s)" % (shape, d))
        self.append([{"type": "assistant", "uuid": "da1", "parentUuid": "du1", "timestamp": iso(self.last_t() + 20), "cwd": "/w/notes-api",
                      "message": {"id": "msg_da1", "role": "assistant", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "the export endpoint pages the notes by cursor"}]}}])
        _, m = self.parse()
        self.assertEqual(m, "fold", "the reply chains onto the new leaf")

    def grown_is_parsed_whole_and_the_cut_advances(self):
        cut0 = self.grow_to_the_share()
        s0 = self.stats()
        out, m = self.parse()
        d = self.moved(s0, self.stats())
        self.assertEqual((m, d.get("g:tailShare")), ("full", 1), "the tail past the cut reached the share after a restore for "
                         "descent: the entry is demoted to the whole parse (%s)" % d)
        self.assertTrue(self.settle(out), "the settle after the whole parse writes: %s" % self.skipped())
        self.assertGreater(self.doc_cut(), cut0 + (os.path.getsize(self.leaf) - cut0) // 2, "the cut advanced past the grown tail")
        self.append_turn()
        out, m = self.parse()
        self.assertEqual(m, "restore", "and the next parse re-seats on the new document")
        self.assertColdEqual(out)

    def after_the_reseat(self, shape):
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.append_turn()
        _, m = self.parse()
        self.assertEqual(m, "restore", "re-seated on the document")
        self.descend(shape)
        self.grown_is_parsed_whole_and_the_cut_advances()

    def as_the_first_delta_after_the_write(self, shape):
        """The settle writes from the whole entry and marks it; the descent demotes that marked entry at its next parse,
        before the gates could pass a delta and re-seat it, and the restore after the descent re-seats the entry."""
        out, m = self.parse()
        self.assertEqual(m, "full")
        self.assertTrue(self.settle(out), "the settle writes: %s" % self.skipped())
        self.descend(shape)
        self.grown_is_parsed_whole_and_the_cut_advances()

    def test_a_spur_after_the_reseat_keeps_the_churn_bound(self):
        self.after_the_reseat("spur")

    def test_a_rewind_after_the_reseat_keeps_the_churn_bound(self):
        self.after_the_reseat("rewind")

    def test_a_spur_as_the_first_delta_after_the_write_keeps_the_churn_bound(self):
        self.as_the_first_delta_after_the_write("spur")

    def test_a_rewind_as_the_first_delta_after_the_write_keeps_the_churn_bound(self):
        self.as_the_first_delta_after_the_write("rewind")


class AProvisionalPostalAuthorIsNotFrozen(Episode):
    """A peer's message whose postal id the log does not hold yet gets a provisional author, and the whole entry re-authors it
    when the log catches up (the assembly's heal). The document carries the provisional author and no heal state, so an
    entry re-seated on it would serve the provisional author for the rest of the process where a cold parse names the peer
    (review of 2026-09-24, low 1): the writer does not mark such an entry, and it stays whole and heals."""

    def test_an_entry_with_an_unresolved_postal_marker_stays_whole_and_heals(self):
        recs = episode(NOW - 86400)
        i = next(j for j, r in enumerate(recs) if r.get("uuid") == "u30")
        recs[i] = dict(recs[i], message={"role": "user", "content": "please raise the search page size to 50\n<!-- romp-msg-id: %s -->" % MSG})
        recs[i].pop("promptSource", None)
        sent = em.parse_z(recs[i]["timestamp"]) + 1
        self.begin(recs)
        out, _ = self.parse()
        self.assertIn("u30", self.entry()["st"]["postal_miss_rec"], "the fixture's premise: the marker waits on the log")
        self.assertTrue(self.settle(out))
        s0 = self.stats()
        self.append_turn()
        _, m = self.parse()
        self.assertEqual(m, "fold", "no re-seat while an author waits on the log: %s" % self.moved(s0, self.stats()))
        self.postal = [{"t": sent, "ev": "sent", "id": MSG, "from": "api", "from_id": PEER, "to_id": SID,
                        "body": "ASK: raise the search page size"}]
        self.append_turn()
        out, m = self.parse()
        self.assertEqual(m, "fold")
        self.assertEqual(self.moved(s0, self.stats()).get("g:reseat", 0), 0)
        self.assertColdEqual(out)


class TheConvergePassReseatsToo(Episode):
    """The converge pass writes an idle session's document through the same writer as the settle; the entry it wrote from is
    re-seated the same way (review of 2026-09-24, low 3)."""

    def test_a_document_the_converge_pass_writes_reseats_the_entry(self):
        out, _ = self.parse()
        why = []
        self.assertTrue(em.asm_checkpoint_write(self.leaf, SID, tree=out, reason_out=why, who="converge pass"), why)
        self.append_turn()
        s0 = self.stats()
        out, m = self.parse()
        d = self.moved(s0, self.stats())
        self.assertEqual((m, d.get("g:reseat"), d.get("restore:afterDemote")), ("restore", 1, 1), "re-seated on the converge "
                         "pass's document: %s" % d)
        self.assertColdEqual(out)


def _aged(*paths):
    """Stamp files an hour old, as a file whose writer finished long ago reads (the quiescence drop's test)."""
    old = time.time() - 3600
    for p in paths:
        os.utime(p, (old, old))


def _quiet_drop(path):
    """The reader's drop as a quiet fold makes it: a fold that steps the file's records, drop_after="quiescent" (the kernel's
    agent-launch fold over a parent leaf, the judges' walk over a dead episode's file). The drop's document write is off for
    it (a cycle cap of 0), so the cycle's byte budget cannot defer the drop and the test reads the drop itself."""
    saved = dict(em._CKPT_CYCLE)
    em._CKPT_CYCLE["cap"] = 0
    try:
        em.fold_records({}, str(path), lambda: 0, lambda s, r: s + 1, drop_after="quiescent")
    finally:
        em._CKPT_CYCLE.update(saved)


def _reader_entry(path):
    with em._JSONL_CACHE_LOCK:
        return em._JSONL_CACHE.get(str(path))


def _chat(prefix, t0, n, parent=None):
    """n settled turns, uuids under `prefix` (unique across this module's sessions: the hydrated memo is keyed by uuid)."""
    recs = []
    for k in range(n):
        u, a = "%su%d" % (prefix, k), "%sa%d" % (prefix, k)
        recs.append({"type": "user", "uuid": u, "parentUuid": parent, "timestamp": iso(t0 + 60 * k), "promptSource": "typed",
                     "cwd": "/w/notes-api", "message": {"role": "user", "content": "%s: page the notes index, step %d" % (prefix, k)}})
        recs.append({"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": iso(t0 + 60 * k + 20), "cwd": "/w/notes-api",
                     "message": {"id": "msg_%s%d" % (prefix, k), "role": "assistant", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "%s: step %d of the notes index is paged" % (prefix, k)}]}})
        parent = a
    return recs


class AQuietDropKeepsWhatTheAssemblyEntryHolds(Episode):
    """Release on the events that end a file's use (2026-10-01), the record side. The reader drops a quiet file on a fold (the
    quiescence drop, a deferred drop paid later), and when a WHOLE assembly entry folds over that very entry (its generation)
    the drop freed nothing, since the adapter holds the same records list, made recordCache.bytes fall while the memory
    stayed, and retired the generation: the entry's next visit demoted (g:rewrite) and, with no document a restore would take,
    parsed whole, while a whole reader in between decoded a second copy. Such a reader entry is now kept as it stands, at the
    same generation, when the whole entry's next visit would parse whole: its leaf has no document a restore would take, or a
    restore here refused it. A RESTORED entry, or a whole one whose leaf has a document a restore would take, does not hold
    a drop back: its next visit restores from the document, and the restore that replaces it frees what it held (round one
    of the review: keeping or trimming a reader entry under a restored entry held a whole reader's second decoding of the
    tail next to the restore's own until the entry next demoted, and counted bytes as freed that the adapter still held)."""

    def test_a_quiet_drop_under_a_whole_entry_keeps_the_generation_and_the_next_parse_folds(self):
        _aged(self.leaf)
        out, mode = self.parse()
        self.assertEqual(mode, "full")
        ent0 = _reader_entry(self.leaf)
        self.assertEqual(ent0[5], 0, "the whole parse read the leaf from record zero")
        st0 = dict(em.record_cache_stats())
        _quiet_drop(self.leaf)
        ent1 = _reader_entry(self.leaf)
        self.assertIsNotNone(ent1, "the drop would free nothing the whole entry does not hold: the reader keeps the entry")
        self.assertEqual((ent1[6], ent1[5], len(ent1[4])), (ent0[6], 0, len(ent0[4])), "the same generation, whole")
        self.assertIs(ent1[4], self.entry()["ad"]._src[self.leaf], "the very records list the assembly entry's adapter holds: "
                      "one copy, which the drop could not have freed")
        st1 = em.record_cache_stats()
        self.assertEqual(st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), 1, "counted as kept for the assembly entry")
        self.assertEqual(st1["dropped"] - st0["dropped"], 0, "and not as a drop")
        self.assertEqual(st1["bytes"], st0["bytes"], "the counted bytes stay with the memory that stays")
        self.append_turn()
        s0 = self.stats()
        out, mode = self.parse()
        self.assertEqual(mode, "fold", "the generation stands, so the appended turn folds; at the base the drop retired it and the "
                         "parse went %s: %s" % (mode, self.moved(s0, self.stats())))
        self.assertColdEqual(out)

    def test_a_whole_entry_kept_whole_after_a_refused_re_seat_holds_the_drop_back(self):
        """A re-seat whose restore did not serve leaves the whole entry the next parse builds kept whole (keepWhole), and its
        settle writes a fresh document from it. Popped, its next visit would offer that document to the restore that refused
        it and parse whole again, so it pins although a document stands and no refusal mark does."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        with mock.patch.object(em, "_asm_restore", _refused_with_its_document):
            self.append_turn()
            _aged(self.leaf)
            out, mode = self.parse()
            self.assertEqual(mode, "full", "the re-seat's restore refused: a whole parse")
            self.assertTrue(self.settle(out), "its settle writes the document again")
            e = self.entry()
            self.assertEqual((bool(e.get("keepWhole")), em.asm_document_stands(self.leaf), em._asm_refusal_stands(self.leaf)),
                             (True, True, False), "the premise: kept whole, a document standing, no refusal mark")
            ent0 = _reader_entry(self.leaf)
            st0 = dict(em.record_cache_stats())
            _quiet_drop(self.leaf)
            self.assertIs(_reader_entry(self.leaf), ent0, "kept as it stands: the drop would only retire the generation")
            st1 = em.record_cache_stats()
            self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (1, 0))
            self.append_turn()
            s0 = self.stats()
            out, mode = self.parse()
            self.assertEqual(mode, "fold", "the appended turn folds: %s" % self.moved(s0, self.stats()))
        self.assertColdEqual(out)

    def test_a_whole_entry_whose_leaf_document_carries_a_refusal_mark_holds_the_drop_back(self):
        """A refusal mark at the leaf's current stat (the chain proof refused the standing document for the tail's shape) sends
        every restore of that document to a whole parse while the leaf stands: a whole entry over it pins, even one its settle
        marked for re-seat, so the whole parse its next visit pays reads the kept records rather than the file again."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.assertTrue(em._asm_mark_refused(self.leaf, "shape", rompuuid=SID))
        ent0 = _reader_entry(self.leaf)
        k0 = em.record_cache_stats().get("pinnedKept", 0)
        _quiet_drop(self.leaf)
        self.assertIs(_reader_entry(self.leaf), ent0, "kept: a restore would refuse the document and parse whole")
        self.assertEqual(em.record_cache_stats().get("pinnedKept", 0) - k0, 1)

    def test_a_whole_entry_whose_own_document_would_restore_does_not_hold_the_drop_back(self):
        """A whole entry that wrote its document but is not marked for re-seat (the tail past that cut already meets the churn
        bound's share) does not pin either: its next visit restores from that document, and the restore replaces the entry and
        what it held. This is the converge pass's shape (it writes an idle leaf's document from the whole entry and pops after
        both writes, test_asm_checkpoint), so an idle session's whole entry leaves memory at its next visit."""
        recs = episode(NOW - 86400, turns=40, compact_every=1000)
        t = max(em.parse_z(r["timestamp"]) for r in recs) + 60
        recs.append({"type": "user", "uuid": "ou", "parentUuid": recs[-1]["uuid"], "timestamp": iso(t), "promptSource": "typed",
                     "cwd": "/w/notes-api", "message": {"role": "user", "content": "run the whole index suite, file by file"}})
        for k in range(6):
            recs += tool_round(t + 10 * (k + 1), k, recs[-1]["uuid"])
        self.begin(recs)
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        e = self.entry()
        self.assertEqual((bool(e.get("docWritten")), bool(e.get("reseat")), bool(e.get("keepWhole"))), (True, False, False),
                         "the premise: a document written, the entry not marked for re-seat")
        st0 = dict(em.record_cache_stats())
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "popped")
        st1 = em.record_cache_stats()
        self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (0, 1))
        out, mode = self.parse()
        self.assertEqual(mode, "restore", "the next visit restores from the document the entry wrote")
        self.assertColdEqual(out)

    def test_a_lineage_file_with_a_document_of_its_own_is_pinned_by_a_leaf_entry_that_has_none(self):
        """After a /clear the anchor (the old leaf) carries the document written in its days as the leaf, while the new leaf
        has none yet: a drop of the anchor would demote the new leaf's whole entry (g:nonleaf) to a restore of the NEW leaf's
        document, which does not stand, and so to a whole parse. The leaf's entry pins the anchor: the document asked about
        is the entry's leaf's, not the dropped file's."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))                             # the anchor's own document, from its days as the leaf
        em.release_assembly(self.leaf, rompuuid=SID)                  # the /clear's flip released its entry
        new_leaf = str(self.td / "5ea70924-2222-4333-8444-00000000a5e9.jsonl")
        Path(new_leaf).write_text("".join(json.dumps(r) + "\n" for r in _chat("cl", NOW - 600, 3)))   # a fresh root

        def parse_new():
            mode = []
            em.parse_session(new_leaf, rompuuid=SID, name="web", dir="/w/notes-api", candidate_files=[new_leaf, self.leaf],
                             states=None, postal_log=[], now=NOW, asm_mode_out=mode)
            return mode[-1] if mode else None
        self.assertEqual(parse_new(), "full")
        self.assertTrue(em.asm_document_stands(self.leaf) and not em.asm_document_stands(new_leaf),
                        "the premise: the anchor has a document, the new leaf none")
        ent0 = _reader_entry(self.leaf)
        k0 = em.record_cache_stats().get("pinnedKept", 0)
        _quiet_drop(self.leaf)
        self.assertIs(_reader_entry(self.leaf), ent0, "the anchor is kept for the new leaf's entry")
        self.assertEqual(em.record_cache_stats().get("pinnedKept", 0) - k0, 1)
        with open(new_leaf, "a") as f:
            f.write("".join(json.dumps(r) + "\n" for r in _chat("cm", NOW - 300, 1, parent="cla2")))
        self.assertEqual(parse_new(), "fold", "the anchor's generation stands: the new leaf's turn folds")

    def test_a_whole_entry_marked_to_re_seat_on_its_document_does_not_hold_the_drop_back(self):
        """The settle's document marks the whole entry for re-seat: its next visit restores from that document whatever the drop
        does, so the drop pops as before."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.assertTrue(self.entry().get("reseat"), "the premise: marked for re-seat")
        d0 = em.record_cache_stats()["dropped"]
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "popped")
        self.assertEqual(em.record_cache_stats()["dropped"] - d0, 1)
        out, mode = self.parse()
        self.assertEqual(mode, "restore", "the next visit re-seats on the document")
        self.assertColdEqual(out)

    def test_a_quiet_drop_under_a_restored_entry_pops_the_reader_entry_and_the_next_parse_restores(self):
        """A restored entry whose leaf a whole reader upgraded (a judge's walk): the drop pops the reader entry, as it always
        did, and the entry's next visit restores from its document, a single decoding of the tail again."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out), em.asm_checkpoint_stats()["skipped"])
        self.fresh()
        out, mode = self.parse()
        self.assertEqual(mode, "restore")
        gen, base, _count = self.entry()["recs"][self.leaf]
        self.assertGreater(base, 0, "the restore read the leaf from the document's cut")
        whole = em._read_jsonl_entry(self.leaf, tail_ok=False)        # a whole reader (a judge's walk) upgrades the entry
        self.assertEqual((whole[6], whole[5]), (gen, 0), "the upgrade keeps the generation and holds every record")
        st0 = dict(em.record_cache_stats())
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "a restored entry does not hold the drop back: popped")
        st1 = em.record_cache_stats()
        self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (0, 1))
        s0 = self.stats()
        out, mode = self.parse()
        self.assertEqual(mode, "restore", "the next visit restores: %s" % self.moved(s0, self.stats()))
        self.assertColdEqual(out)

    def test_a_restored_entry_that_folded_over_the_upgrade_does_not_hold_the_drop_back_either(self):
        """The same after a fold over the upgraded reader entry, which leaves the restored adapter holding the reader's WHOLE
        list (_src) under its tail row: a keep or a trim of the reader entry would free nothing there, so the drop pops, and
        the restore at the next visit replaces the entry and what it held."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.fresh()
        _, mode = self.parse()
        self.assertEqual(mode, "restore")
        em._read_jsonl_entry(self.leaf, tail_ok=False)               # the whole reader's upgrade
        self.append_turn()
        _aged(self.leaf)
        _, mode = self.parse()
        self.assertEqual(mode, "fold")
        e = self.entry()
        gen, base, count = e["recs"][self.leaf]
        self.assertGreater(base, 0)
        self.assertEqual(len(e["ad"]._src[self.leaf]), count, "the premise: the adapter holds the whole upgraded list")
        st0 = dict(em.record_cache_stats())
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "popped")
        st1 = em.record_cache_stats()
        self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (0, 1))
        out, mode = self.parse()
        self.assertEqual(mode, "restore")
        self.assertColdEqual(out)

    def test_a_restored_entry_over_a_whole_reader_entry_does_not_hold_the_drop_back(self):
        """A restore that met a whole reader entry first reads over it (its row's base 0): popped as well, and restored again."""
        _aged(self.leaf)
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.fresh()
        em._read_jsonl_entry(self.leaf, tail_ok=False)               # a whole reader first
        _, mode = self.parse()
        self.assertEqual(mode, "restore")
        self.assertEqual(self.entry()["recs"][self.leaf][1], 0, "the premise: the restore read over the whole reader entry")
        d0 = em.record_cache_stats()["dropped"]
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "popped")
        self.assertEqual(em.record_cache_stats()["dropped"] - d0, 1)
        out, mode = self.parse()
        self.assertEqual(mode, "restore")
        self.assertColdEqual(out)

    def test_an_owed_drop_paid_by_its_pop_alone_keeps_a_pinned_entry_too(self):
        """An owed drop past the owed table's bound is paid by the pop alone (_pop_owed_entry): a reader entry a whole assembly
        entry pins stays there too, and a file nothing pins is popped."""
        other = self.td / "frozen.jsonl"
        other.write_text("".join(json.dumps(r) + "\n" for r in _chat("ow", NOW - 7200, 3)))
        _aged(other, self.leaf)
        self.parse()                                                  # a whole entry, no document: it pins the leaf
        em._read_jsonl_entry(str(other), tail_ok=False)
        ent0 = _reader_entry(self.leaf)
        saved = (dict(em._CKPT_CYCLE), em._DROP_OWED_MAX, dict(em._DROP_OWED))
        em._CKPT_CYCLE.update(cap=1, spent=1)                         # the drop write on, the cycle's budget spent: the drop is owed
        em._DROP_OWED_MAX = 0                                         # and the owed table past its bound at once: paid by the pop
        em._DROP_OWED.clear()
        st0 = dict(em.record_cache_stats())
        try:
            with mock.patch.object(em, "_path_needs_write", lambda *a, **k: True):
                for p in (self.leaf, str(other)):
                    em.fold_records({}, p, lambda: 0, lambda s, r: s + 1, drop_after="quiescent")
        finally:
            em._CKPT_CYCLE.clear(); em._CKPT_CYCLE.update(saved[0])
            em._DROP_OWED_MAX = saved[1]
            em._DROP_OWED.clear(); em._DROP_OWED.update(saved[2])
        st1 = em.record_cache_stats()
        self.assertIs(_reader_entry(self.leaf), ent0, "the pinned leaf stays, at its generation")
        self.assertIsNone(_reader_entry(other), "the file nothing pins is popped")
        self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (1, 1))

    def test_a_quiet_drop_of_a_file_no_assembly_entry_folds_over_still_drops_it(self):
        other = self.td / "frozen.jsonl"
        other.write_text("".join(json.dumps(r) + "\n" for r in _chat("fz", NOW - 7200, 3)))
        _aged(other, self.leaf)
        self.parse()
        em._read_jsonl_entry(str(other), tail_ok=False)
        d0 = em.record_cache_stats()["dropped"]
        _quiet_drop(other)
        self.assertIsNone(_reader_entry(other), "nothing pins it: dropped as before")
        self.assertEqual(em.record_cache_stats()["dropped"] - d0, 1)
        gen = self.entry()["recs"][self.leaf][0]
        em._read_jsonl_entry(self.leaf, tail_ok=False)
        with em._JSONL_CACHE_LOCK:                                    # an entry at another generation does not pin it either
            em._JSONL_CACHE[self.leaf] = em._JSONL_CACHE[self.leaf][:6] + (gen + 10 ** 6,) + em._JSONL_CACHE[self.leaf][7:]
        _quiet_drop(self.leaf)
        self.assertIsNone(_reader_entry(self.leaf), "an assembly entry at another generation keeps nothing")


class ReleaseOnTheEndOfUse(Episode):
    """The assembly side: em.release_assembly drops a leaf's assembly entries (one session's, when named), gives the lazy index
    back and lets the hydrated bodies of the entry's pre-cut records go, leaving the record cache as it stands; and
    em.release_records drops the files outside a live lineage that no assembly entry folds over."""

    def test_a_release_drops_the_entry_and_its_hydrated_bodies_and_keeps_the_records_and_other_sessions(self):
        out, _ = self.parse()
        self.assertTrue(self.settle(out))
        self.fresh()
        out, mode = self.parse()
        self.assertEqual(mode, "restore")
        em.hydrate(out, SID)                                          # the consumers read the pre-cut bodies: the memo holds them
        e = self.entry()
        ix = e.get("index")
        pre = {r[0] for r in ix.records} if ix is not None else {a.get("uuid") for a in e.get("prefix") or [] if a.get("uuid")}
        with em._ASM_CKPT_LOCK:
            held = set(em._HYDRATED) & pre
            em._HYDRATED["peer-body-uuid"] = ({"uuid": "peer-body-uuid"}, 10); em._HYDRATED_BYTES[0] += 10
        self.assertTrue(held, "the restored entry's bodies were memoized")
        saved = em._CKPT_DIR_FN
        em._CKPT_DIR_FN = None                                        # a whole parse: no restore under the other session's id
        try:                                                          #  (it would refuse this session's document)
            em.parse_session(self.leaf, rompuuid=PEER, name="api", dir="/w/notes-api", candidate_files=[self.leaf], states=None,
                             postal_log=[], now=NOW)                  # the same file parsed for another session (a fork child)
        finally:
            em._CKPT_DIR_FN = saved
        rec0 = _reader_entry(self.leaf)
        r0 = em._ASM_STATS.get("released", 0)
        self.assertEqual(em.release_assembly(self.leaf, rompuuid=SID), 1)
        self.assertIsNone(self.entry(), "this session's entry is gone")
        with em._ASM_LOCK:
            self.assertIn((os.path.realpath(self.leaf), PEER, False), em._ASM_CACHE, "another session's entry of the same file stays")
        self.assertIs(_reader_entry(self.leaf), rec0, "the record cache is not the release's to touch")
        with em._ASM_CKPT_LOCK:
            self.assertEqual(len(set(em._HYDRATED) & pre), 0, "the entry's hydrated bodies went with it (of %d held)" % len(held))
            self.assertIn("peer-body-uuid", em._HYDRATED, "bodies of other records stay")
            self.assertEqual(em._HYDRATED_BYTES[0], sum(v[1] for v in em._HYDRATED.values()), "the memo's byte count follows")
        self.assertEqual(em._ASM_STATS.get("released", 0) - r0, 1, "counted")
        out, mode = self.parse()
        self.assertEqual(mode, "restore", "the session parsed again restores from its document")
        _, mode = self.parse()
        self.assertEqual(mode, "serve", "and serves from the entry after")
        self.assertColdEqual(out)

    def test_release_records_drops_only_what_no_lineage_keeps_and_no_entry_folds_over(self):
        frozen, kept = self.td / "frozen.jsonl", self.td / "kept.jsonl"
        frozen.write_text("".join(json.dumps(r) + "\n" for r in _chat("fz", NOW - 7200, 3)))
        kept.write_text("".join(json.dumps(r) + "\n" for r in _chat("kp", NOW - 7200, 3)))
        self.parse()
        for p in (frozen, kept):
            em._read_jsonl_entry(str(p), tail_ok=False)
        leaf0, kept0 = _reader_entry(self.leaf), _reader_entry(kept)
        d0 = em.record_cache_stats()["dropped"]
        self.assertEqual(em.release_records([self.leaf, str(frozen), str(kept)], keep={str(kept)}), 1)
        self.assertIsNone(_reader_entry(frozen), "outside every lineage and pinned by nothing: dropped")
        self.assertIs(_reader_entry(kept), kept0, "a live lineage's file stays")
        self.assertIs(_reader_entry(self.leaf), leaf0, "a file an assembly entry folds over stays")
        self.assertEqual(em.record_cache_stats()["dropped"] - d0, 1)
        em.release_assembly(self.leaf)
        self.assertEqual(em.release_records([self.leaf]), 1, "once its entry is released, the leaf goes too")
        self.assertIsNone(_reader_entry(self.leaf))


class ReleasesKeepTheJudgesVerdicts(Episode):
    """Verdict parity: the judges' facts (chain membership, the per-file rewinds, the parsed tree) over a synthetic corpus are
    the same after every release this change adds: a /clear's flip, the end of each session's use, a quiet drop over each
    leaf, every record entry dropped, and the judges' own walk with its drop. The corpus: a long session with compactions, a
    rewind and its document (restored); a session that cleared, its anchor carrying a rewind; a session resumed from another
    file."""

    def test_every_release_leaves_the_judges_facts_as_they_were(self):
        last = self.last_uuid()
        last_prompt = next(r for r in reversed(self.recs) if r.get("type") == "user" and r.get("promptSource") == "typed")
        self.append(rewind(self.last_t() + 60, last_prompt["parentUuid"]) + [
            {"type": "assistant", "uuid": "da1", "parentUuid": "du1", "timestamp": iso(self.last_t() + 80), "cwd": "/w/notes-api",
             "message": {"id": "msg_da1", "role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "cursor paging it is"}]}}])
        self.assertNotEqual(self.last_uuid(), last)
        P, R = "5ea70924-2222-4333-8444-00000000a5f1", "5ea70924-2222-4333-8444-00000000a5f2"
        p_anchor, p_leaf = str(self.td / (P + ".jsonl")), str(self.td / "5ea70924-2222-4333-8444-00000000a5f3.jsonl")
        r_anchor, r_leaf = str(self.td / (R + ".jsonl")), str(self.td / "5ea70924-2222-4333-8444-00000000a5f4.jsonl")
        pre = _chat("pc", NOW - 9000, 3)
        pre += [{"type": "user", "uuid": "pcu9", "parentUuid": "pca0", "timestamp": iso(NOW - 8000), "promptSource": "typed",
                 "cwd": "/w/notes-api", "message": {"role": "user", "content": "pc: no, page by cursor from step 0"}}]
        Path(p_anchor).write_text("".join(json.dumps(r) + "\n" for r in pre))
        Path(p_leaf).write_text("".join(json.dumps(r) + "\n" for r in _chat("pn", NOW - 7000, 3)))   # the /clear: a fresh root
        Path(r_anchor).write_text("".join(json.dumps(r) + "\n" for r in _chat("ra", NOW - 9000, 2)))
        Path(r_leaf).write_text("".join(json.dumps(r) + "\n" for r in _chat("rb", NOW - 6000, 2, parent="raa1")))   # resumed
        _aged(self.leaf, p_anchor, p_leaf, r_anchor, r_leaf)
        sessions = {"long": (self.leaf, [self.leaf], SID), "cleared": (p_leaf, [p_leaf, p_anchor], P),
                    "resumed": (r_leaf, [r_leaf, r_anchor], R)}
        out, _ = self.parse()
        self.assertTrue(self.settle(out), em.asm_checkpoint_stats()["skipped"])
        self.fresh()

        def facts():
            got, roads = {}, {}
            for name, (leaf, cands, rid) in sessions.items():
                mode = []
                tree = em.parse_session(leaf, rompuuid=rid, name=name, dir="/w/notes-api", candidate_files=cands, states=None,
                                        postal_log=[], now=NOW, asm_mode_out=mode)
                em.hydrate(tree, rid)
                roads[name] = mode[-1] if mode else None
                got[name] = {"chain": {k: sorted(v) for k, v in em.chain_membership(leaf, cands, rompuuid=rid).items()},
                             "leafRewound": sorted(em.file_rewound(leaf, rompuuid=rid)),
                             "otherRewound": {Path(fp).name: sorted(em.rewound_uuids(fp, drop=False)) for fp in cands if fp != leaf},
                             "tree": _strip(tree)}
            return got, roads

        em.parse_session(p_anchor, rompuuid=P, name="cleared", dir="/w/notes-api", candidate_files=[p_anchor], states=None,
                         postal_log=[], now=NOW)                      # the anchor was the cleared session's leaf before its /clear
        before, roads0 = facts()
        self.assertEqual(roads0["long"], "restore")
        self.assertTrue(before["long"]["leafRewound"] and before["cleared"]["otherRewound"][Path(p_anchor).name],
                        "the corpus carries rewinds for the walks to find: %s" % {k: v["leafRewound"] for k, v in before.items()})
        em.release_assembly(p_anchor, rompuuid=P)                     # the /clear's flip
        for leaf, _cands, _rid in sessions.values():
            _quiet_drop(leaf)                                         # a quiet fold over each leaf: kept under a whole entry,
        #                                                               popped under the restored one
        for leaf, _cands, rid in sessions.values():
            em.release_assembly(leaf, rompuuid=rid)                   # the end of each session's use
        files = sorted({f for _l, cands, _r in sessions.values() for f in cands})
        em.release_records(files)                                     # nothing live: every record entry goes
        for _l, cands, _r in sessions.values():
            for fp in cands[1:]:
                em.rewound_uuids(fp, drop=True)                       # the judges' walk over a frozen file, with its drop
        with em._JSONL_CACHE_LOCK:
            self.assertFalse(set(em._JSONL_CACHE) & set(files) - {p_anchor, r_anchor},
                             "the leaves left the record cache: the facts below are rebuilt from the files")
        after, roads1 = facts()
        self.assertEqual(roads1, {"long": "restore", "cleared": "full", "resumed": "full"},
                         "each session pays one parse after the release: the restore where a document stands, else whole")
        for name in sessions:
            self.assertEqual(after[name], before[name], "%s: the judges' facts are unchanged by the releases" % name)
        _, roads2 = facts()
        self.assertEqual(roads2, {"long": "serve", "cleared": "serve", "resumed": "serve"}, "and served after")


if __name__ == "__main__":
    unittest.main()
