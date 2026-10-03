#!/usr/bin/env python3
"""A compaction appended past the assembly document's cut was parsed whole (the reparse investigation of 2026-09-24). The fold's
gates demote a delta holding a compact_boundary (`boundary`) or its isCompactSummary record (`summary`), rightly: the fold only
carries the emit state forward, and a compaction changes how records around it parse. But neither reason was among the
demotions that fall to the restore road (_ASM_RESTORE_AFTER_DEMOTE), so the entry went to a whole parse of the transcript
although the standing document still covers everything before its cut, and a restore parses the tail from the cut, compaction
included, exactly as every boot over a document already does. Measured on synthetic transcripts on the cluster, that whole
parse cost 0.6 to 1.7 s per compaction at 150 to 184 MB, the settle after it rewrote the document for another 2.3 to 2.7 s,
and the next parse re-seated the new whole entry on the document for 0.3 to 0.4 s.

Pinned here, each against a cold whole parse turn by turn: a compaction past the cut takes the restore road (`g:boundary` then
`restore:afterDemote`, no `full`), from an entry restored at boot and from the whole entry the settle wrote the document from; a
summary written after its boundary restores again at `g:summary`; the live manual /compact shape restores; the entry after
the compaction is the restored shape, and the next append folds onto it; and a compaction anchored before the cut still
refuses at the chain proof and parses whole.

Two limits on that road, from the review of the same day. A compaction masks nothing later in its delta: a record past it that
the fold refuses for its own reason (a stamp before the records folded, a Skill call a pre-cut payload names, a uuid a pre-cut
record named as its parent, a command-wrapper record wearing a pre-cut prompt id) keeps its whole parse, since the restore
proves only that the tail chains onto the document and would serve a tree a cold parse does not build, at every later boot
too. And a compaction restores only while the tail past the document's cut is under the churn bound's share of the pre-cut
bytes; once it has reached the share the compaction parses whole (`restore:pastShare`, or `g:tailShare` on an entry the fold's
churn gate already holds to the share, a re-seated one) and the settle writes a document with a later cut, so the tail every
restore reads stays bounded. A compaction on an entry with no document to measure parses whole as it always did and is not
counted past the share. And a compaction parses whole on a whole entry whose postal author still waits on the log, as the
writer declines to re-seat it, and on any entry whose document was written while an author waited (`postalWait`), whether it
wrote that document and has healed since or was restored from it: the document carries the provisional author and no heal
state. A document with no `postalWait` at all, written before the writer recorded the wait, counts as one written while an
author waited. The writer keeps that record on the entry as soon as it replaces the document, so a sidecar write that fails
after the replace leaves no stale record.
And past an automatic compaction in the middle of a turn whose summary wears the turn's prompt id, the turn's later tool
results, which wear it too, fold onto the restored entry, whether it was restored from the standing document or from one a
settle wrote after the compaction, while a prompt id only the document's seed names still parses whole (2026-10-03).
A delta the narrowed gate let through that a compaction or the descent then refuses parses whole, as before the narrowing,
never on the restore road (round one of that review: a result rewinding onto the pre-compaction leaf restored a turn the cold
parse does not build), and a turn compacted twice still demotes on the seed after a restart.
The re-seat steps aside for such a delta that passed on a parallel batch's link, so the kept check rules: a result of a
parallel call an automatic compaction was anchored on parses whole, on an entry marked for its re-seat and on one without
the mark, and a turn's later compaction alone in its append restores, its own summary marking nothing (round two).
Synthetic transcripts only: invented notes-api text, placeholder uuids."""
import json
import os
import random
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from romp_load import load_source  # noqa: F401  the direct run's floor lands at this import (tests/romp_load.py)

# Hermetic state BEFORE the loads: they resolve their state root at import time, and only pytest runs conftest's floor (a bare
# unittest or script run otherwise writes REAL state). The harness below loads the event model under the same floor.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
from test_asm_checkpoint import em, G, SID, NOW, Harness, _strip, _doc, _write_doc   # noqa: E402  the stage 4a harness and the golden builders

WORDS = ("search", "index", "notes", "query", "ranking", "cursor", "page", "cache", "schema", "migration", "endpoint", "fixture")


def episode(t0, turns=240, compact_every=80, skill_at=None, orphan_at=None, wrapper_at=None):
    """A long synthetic session: `turns` prompt-and-reply turns, with an auto compaction (the boundary anchored on the last
    record, its summary the next prompt's parent) every `compact_every` turns. `skill_at` chains a skill payload (a meta user
    record naming the Skill call `tu_skill_<k>` by sourceToolUseID) after turn k; `orphan_at` adds, after turn k, a prompt and
    reply off the chain whose parent `ghost-<k>` no record holds; `wrapper_at` chains, after turn k, a local command's wrapper
    record wearing the prompt id `pp_usage_<k>`. None of them draws from the random stream."""
    rnd = random.Random(924)
    recs, parent, t = [], None, t0
    for k in range(turns):
        if k and k % compact_every == 0:
            b, s = "b%d" % k, "s%d" % k
            recs += [G.compact_line(t, b, parent, trigger="auto"),
                     G.compact_summary_line(t + 1, s, b, text="summary so far: " + " ".join(rnd.choice(WORDS) for _ in range(80)))]
            parent = s
            t += 2
        u, a = "u%d" % k, "a%d" % k
        recs += [G.uline(t, " ".join(rnd.choice(WORDS) for _ in range(30)) + " %d" % k, u, parent),
                 G.aline(t + 20, " ".join(rnd.choice(WORDS) for _ in range(200)), a, u, stop="end_turn")]
        parent = a
        t += 60
        if k == skill_at:
            recs.append(dict(G.uline(t, "skill instructions: the notes-api search conventions", "sp%d" % k, parent),
                             sourceToolUseID="tu_skill_%d" % k, isMeta=True))
            parent = "sp%d" % k
            t += 5
        if k == orphan_at:
            recs += [G.uline(t, "an ask whose parent no transcript holds", "uo%d" % k, "ghost-%d" % k),
                     G.aline(t + 10, "a reply under it", "ao%d" % k, "uo%d" % k, stop="end_turn")]
            t += 30
        if k == wrapper_at:
            recs.append(dict(G.uline(t, "<command-name>/usage</command-name>\n<command-message>usage</command-message>\n"
                                        "<command-args></command-args>", "uw%d" % k, parent), promptId="pp_usage_%d" % k))
            parent = "uw%d" % k
            t += 5
    return recs


def filler(t, tag, parent, nbytes):
    """Settled turns chained onto `parent`, a minute apart from `t`, whose lines add up to at least `nbytes`: the tail past a
    document's cut grown in one write."""
    out, size, k = [], 0, 0
    while size < nbytes:
        u, a = "gu_%s_%d" % (tag, k), "ga_%s_%d" % (tag, k)
        pair = [G.uline(t + 60 * k, "index the next batch of notes, step %d" % k, u, parent),
                G.aline(t + 60 * k + 20, "indexed another batch of notes for the search endpoint. " * 20 + str(k), a, u, stop="end_turn")]
        out += pair
        size += sum(len(json.dumps(r)) + 1 for r in pair)
        parent, k = a, k + 1
    return out


def doc_cut(path):
    """The leaf's standing document as the bound reads it: the pre-cut bytes over the lineage and the leaf's cut offset."""
    d = _doc(path)
    pre = sum(int(f["size"]) if f.get("skip") else int((f.get("cut") or [0])[0]) for f in d["files"].values())
    return pre, int(((d["files"].get(Path(path).stem) or {}).get("cut") or [0])[0])


def compaction(t, tag, anchor):
    """An auto compaction as the CLI writes it: the boundary anchored (logicalParentUuid) on `anchor`, its summary its child.
    Returns the two records and the summary's uuid (the next prompt's parent)."""
    b, s = "cb_%s" % tag, "cs_%s" % tag
    return [G.compact_line(t, b, anchor, trigger="auto"),
            G.compact_summary_line(t + 1, s, b, text="summary so far: the notes-api search is wired (%s)" % tag)], s


def turn(t, tag, parent):
    u, a = "xu_%s" % tag, "xa_%s" % tag
    return [G.uline(t, "follow-up %s on the notes-api search" % tag, u, parent),
            G.aline(t + 20, "done with %s" % tag, a, u, stop="end_turn")]


def postal_episode():
    """The long session with turn 30's prompt a peer's message: the postal marker names a message id the log does not hold
    until `row` is handed to the parse, so the author stays provisional until then. Returns the records and the log's row."""
    recs = episode(NOW - 86400)
    i = next(j for j, r in enumerate(recs) if r.get("uuid") == "u30")
    recs[i] = dict(recs[i], message={"role": "user", "content": "please raise the notes-api search page size to 50\n"
                                                                "<!-- romp-msg-id: %s -->" % G.MID})
    recs[i].pop("promptSource", None)
    row = {"t": em.parse_z(recs[i]["timestamp"]) + 1, "ev": "sent", "id": G.MID, "from": "api", "from_id": G.PEER, "to_id": SID,
           "body": "ASK: raise the search page size"}
    return recs, row


def author_of(tree, uuid="u30"):
    """The served author of the atom `uuid` in a stripped tree."""
    return next((a.get("author") for t in tree["turns"] for a in t["atoms"] if a.get("uuid") == uuid), None)


class CompactionRestore(Harness):
    def _documented(self, name, **shape):
        """A long session parsed whole, its document written from that whole entry (the settle's write); the entry stands."""
        recs = episode(NOW - 86400, **shape)
        path = self.write(name, recs)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
        return path, recs

    def _restart(self, path):
        """A restart over the document: the next parse restores from it."""
        self.fresh()
        modes = []
        self.parse(path, modes)
        self.assertEqual(modes, ["restore"], "the restart restores from the document: %s" % em.asm_checkpoint_stats())

    @staticmethod
    def _append(path, recs):
        with open(path, "a") as fh:
            fh.write("".join(json.dumps(r) + "\n" for r in recs))

    @staticmethod
    def _entry(path):
        """The leaf's assembly entry as the cache holds it (None once a cold parse or a restart cleared the cache)."""
        with em._ASM_LOCK:
            return em._ASM_CACHE.get((os.path.realpath(path), SID, False))

    def _parse_counted(self, path):
        """One parse: its tree, its roads, and the road counters it moved (restore:afterDemote, g:<reason>, full:<why>, ...)."""
        before = dict(em._ASM_STATS)
        modes = []
        tree = self.parse(path, modes)
        after = dict(em._ASM_STATS)
        moved = {k: after.get(k, 0) - before.get(k, 0) for k in set(before) | set(after) if after.get(k, 0) != before.get(k, 0)}
        return tree, modes, moved

    def _served(self, tree):
        """The served tree as a comparison reads it: hydrated (a restored tree's pre-cut bodies), stripped, its cut noted first."""
        cut = tree.get("cutTurn", 0)
        em.hydrate(tree, SID)
        return _strip(tree), cut

    def _snapshot(self, path, tag):
        """The leaf's bytes now, under the same file name in a directory of their own: the cold parse's reference for this step,
        taken at the end (a cold parse clears the caches the steps after it read)."""
        d = self.td / ("ref-" + tag)
        d.mkdir()
        ref = d / Path(path).name
        shutil.copyfile(path, ref)
        return str(ref)

    def assertSameTree(self, got, want, what):
        """Two stripped trees, compared by length first and then turn by turn, naming the first turn that differs (a whole-tree
        assertEqual over hundreds of turns prints a diff nobody reads), then the session's own fields."""
        self.assertEqual(len(got["turns"]), len(want["turns"]), "%s: the turn count" % what)
        first = next((i for i, (a, b) in enumerate(zip(got["turns"], want["turns"])) if a != b), None)
        if first is not None:
            g, w = got["turns"][first], want["turns"][first]
            self.fail("%s: turn %d differs from the cold parse: served %s %r, cold %s %r" % (
                what, first, g.get("id"), [a.get("uuid") for a in g["atoms"]], w.get("id"), [a.get("uuid") for a in w["atoms"]]))
        self.assertEqual({k: v for k, v in got.items() if k != "turns"}, {k: v for k, v in want.items() if k != "turns"},
                         "%s: the session's fields" % what)

    def assertRestored(self, modes, moved, reason, what):
        self.assertEqual(moved.get("full", 0), 0, "%s: no whole parse for a compaction past the cut: road %s, counters moved %s"
                         % (what, modes, moved))
        self.assertEqual((modes, moved.get("g:" + reason, 0), moved.get("restore:afterDemote", 0)), (["restore"], 1, 1),
                         "%s: the gates demote at %s and the restore road serves: counters moved %s" % (what, reason, moved))

    def test_a_compaction_past_the_cut_restores_from_the_standing_document_and_the_next_append_folds(self):
        path, recs = self._documented("restored")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c1", recs[-1]["uuid"])
        more = pair + turn(self.after(recs, 150), "c1", s)
        self._append(path, more)
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        ref = self._snapshot(path, "compaction")
        nxt = turn(self.after(recs + more, 60), "next", more[-1]["uuid"])
        self._append(path, nxt)
        tree2, modes2, moved2 = self._parse_counted(path)
        got2, cut2 = self._served(tree2)
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertSameTree(got2, self.cold(path), "after the next append")
        self.assertRestored(modes, moved, "boundary", "the compaction")
        self.assertGreater(cut, 0, "the restored shape: the pre-cut turns from the document, the render floor at the cut")
        self.assertEqual((modes2, moved2.get("full", 0)), (["fold"], 0), "the next append folds: %s" % moved2)
        self.assertGreater(cut2, 0, "onto the restored entry, not a whole one: every later fold re-derives the tail only")

    def test_a_compaction_on_the_whole_entry_the_document_was_written_from_restores_too(self):
        path, recs = self._documented("whole")                  # no restart: the entry the settle wrote from stands, whole
        pair, s = compaction(self.after(recs, 120), "c2", recs[-1]["uuid"])
        self._append(path, pair + turn(self.after(recs, 150), "c2", s))
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        reseated = (self._entry(path) or {}).get("reseated")
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        skipped = dict(em.asm_checkpoint_stats()["skipped"])
        self.assertSameTree(got, self.cold(path), "after the compaction")
        self.assertRestored(modes, moved, "boundary", "the compaction")
        self.assertGreater(cut, 0, "the restored shape")
        self.assertEqual((wrote, skipped), (False, {"restored": 1}),
                         "the document stands: a restored entry writes nothing, so a compaction under the share leaves the cut")
        self.assertIs(reseated, True, "the writer marked the whole entry for its re-seat, and the compaction's restore carries the "
                      "mark, so the fold's churn gate holds the restored entry to the share")

    def test_a_summary_written_after_its_boundary_restores_at_each_step(self):
        """The two records can reach the parse in separate writes: the boundary demotes at `boundary`, the summary alone at
        `summary`, and each restores; the turn after them folds."""
        path, recs = self._documented("split")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c3", recs[-1]["uuid"])
        steps = [("boundary", pair[:1]), ("summary", pair[1:]), (None, turn(self.after(recs, 150), "c3", s))]
        served = []
        for reason, grp in steps:
            self._append(path, grp)
            tree, modes, moved = self._parse_counted(path)
            got, cut = self._served(tree)
            served.append((reason, modes, moved, got, cut, self._snapshot(path, "split-%d" % len(served))))
        for reason, modes, moved, got, cut, ref in served:
            self.assertSameTree(got, self.cold(ref), "after the %s step" % (reason or "turn"))
            if reason is None:
                self.assertEqual((modes, moved.get("full", 0)), (["fold"], 0), "the turn after the pair folds: %s" % moved)
            else:
                self.assertRestored(modes, moved, reason, "the %s alone" % reason)
            self.assertGreater(cut, 0, "the restored shape after the %s step" % (reason or "turn"))

    def test_a_live_manual_compact_past_the_cut_restores(self):
        """The live manual /compact shape (tests/test_event_model_golden.manual_compact_lines): the boundary and summary land as a
        detached pair, then the command wrappers and the stdout; the conversation chains through the wrappers."""
        path, recs = self._documented("manual")
        self._restart(path)
        t = self.after(recs, 120)
        side, so = G.manual_compact_lines(t, t + 10, "m1", parent=recs[-1]["uuid"])
        self._append(path, side + turn(t + 40, "m1", so))
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after the manual compact")
        self.assertRestored(modes, moved, "boundary", "the manual compact")
        self.assertGreater(cut, 0, "the restored shape")

    def test_tool_results_after_an_auto_compaction_in_the_same_prompt_fold_onto_the_restored_entry(self):
        """The road a long turn takes through an automatic compaction (2026-10-03). The compaction's delta demotes and the restore
        road serves it from the standing document (`g:boundary`, `restore:afterDemote`); every later tool result of the same turn
        wears the turn's prompt id, and here the summary wears it too (the shape the narrowed gate assumes the CLI can write).
        The gate's boundary arm demoted each of those appends to a whole parse until the next prompt. The pair is attached (the
        conversation chains through the boundary and its summary), so no such record can re-seat it: the appends fold onto the
        restored entry (`fold:attachedPass`), and each served tree equals a cold parse. From an entry a restart restored,
        and from the whole entry the document was written from (re-seated on it by the turn's first parse)."""
        ran = []
        for entry in ("restored", "whole"):
            with self.subTest(entry=entry):
                name = "pid-" + entry
                path, recs = self._documented(name)
                if entry == "restored":
                    self._restart(path)
                t = self.after(recs, 60)
                opener = dict(G.uline(t, "rebuild the notes-api search index", "pz_u", recs[-1]["uuid"]), promptId="pz")
                call1 = G.aline(t + 10, "rebuilding the index", "pz_a1", "pz_u", tools=("Bash",), stop="tool_use")
                res1 = dict(G.trline(t + 20, "tu_pz_a1_0", "pz_r1", "pz_a1", content="rebuilt shard 1 of 12"), promptId="pz")
                self._append(path, [opener, call1, res1])
                self._parse_counted(path)                                    # the turn opens: a fold, or the whole entry's re-seat
                pair, s = compaction(t + 30, "pz", "pz_r1")
                pair[1]["promptId"] = "pz"                                   # the summary wears the prompt id of the turn it compacted in
                call2 = G.aline(t + 40, "checking the rebuilt index", "pz_a2", s, tools=("Bash",), stop="tool_use")
                self._append(path, pair + [call2])
                tree1, modes1, moved1 = self._parse_counted(path)
                got1, _cut1 = self._served(tree1)
                ref1 = self._snapshot(path, name + "-compaction")
                res2 = dict(G.trline(t + 50, "tu_pz_a2_0", "pz_r2", "pz_a2", content="12 shards, 0 errors"), promptId="pz")
                self._append(path, [res2])
                tree2, modes2, moved2 = self._parse_counted(path)
                got2, cut2 = self._served(tree2)
                ref2 = self._snapshot(path, name + "-result")
                call3 = G.aline(t + 60, "one more query against it", "pz_a3", "pz_r2", tools=("Bash",), stop="tool_use")
                res3 = dict(G.trline(t + 70, "tu_pz_a3_0", "pz_r3", "pz_a3", content="the search answers from the new index"),
                            promptId="pz")
                done = G.aline(t + 80, "the index is rebuilt and answering", "pz_a4", "pz_r3", stop="end_turn")
                self._append(path, [call3, res3, done])
                tree3, modes3, moved3 = self._parse_counted(path)
                got3, cut3 = self._served(tree3)
                self.assertSameTree(got1, self.cold(ref1), "after the compaction")
                self.assertSameTree(got2, self.cold(ref2), "after the first tool result past it")
                self.assertSameTree(got3, self.cold(path), "after the turn's end")
                self.assertRestored(modes1, moved1, "boundary", "the compaction")
                for what, modes, moved, cut in (("the tool result past the compaction", modes2, moved2, cut2),
                                                ("the turn's end", modes3, moved3, cut3)):
                    self.assertEqual((modes, moved.get("full", 0), moved.get("g:promptid", 0), moved.get("fold:attachedPass", 0)),
                                     (["fold"], 0, 0, 1), "%s folds onto the restored entry: counters moved %s" % (what, moved))
                    self.assertGreater(cut, 0, "%s: still the restored shape, the render floor at the cut" % what)
                ran.append(entry)
        self.assertEqual(ran, ["restored", "whole"])

    def test_tool_results_fold_on_an_entry_restored_from_a_document_written_after_the_compaction(self):
        """The same turn when a settle rewrites the document after its compaction (2026-10-03). Here the compaction lands with
        the tail past the churn bound's share, so it parses whole, and the settle writes a document whose cut is before the
        compaction, its summary in the tail. The entry then comes back from that document, by the re-seat at the next parse or
        by a restart. The document's gates named every summary's prompt id at the write, so that entry's seed carried the
        turn's id, and every later tool result of the turn demoted to a whole parse. The gates name only the summaries before
        the cut, the restore reads this one from the tail, and the tool results fold onto the restored entry
        (`fold:attachedPass`); each served tree equals a cold parse."""
        ran = []
        for road in ("reseat", "restart"):
            with self.subTest(road=road):
                name = "pid-rewrite-" + road
                path, recs = self._documented(name)
                pre0, cut0 = doc_cut(path)
                grow = filler(self.after(recs, 60), road, recs[-1]["uuid"],
                              pre0 // em._ASM_TAIL_SHARE - (os.path.getsize(path) - cut0) + 1)
                t = self.after(recs + grow, 60)
                opener = dict(G.uline(t, "rebuild the notes-api search index", "pw_u", grow[-1]["uuid"]), promptId="pw")
                call1 = G.aline(t + 10, "rebuilding the index", "pw_a1", "pw_u", tools=("Bash",), stop="tool_use")
                res1 = dict(G.trline(t + 20, "tu_pw_a1_0", "pw_r1", "pw_a1", content="rebuilt shard 1 of 12"), promptId="pw")
                self._append(path, grow + [opener, call1, res1])
                self._parse_counted(path)                                    # the growth and the turn's opening: the re-seat
                pair, s = compaction(t + 30, "pw", "pw_r1")
                pair[1]["promptId"] = "pw"                                   # the summary wears the prompt id of the turn it compacted in
                call2 = G.aline(t + 40, "checking the rebuilt index", "pw_a2", s, tools=("Bash",), stop="tool_use")
                self._append(path, pair + [call2])
                tail = os.path.getsize(path) - cut0
                tree1, modes1, moved1 = self._parse_counted(path)
                got1, _cut1 = self._served(tree1)
                ref1 = self._snapshot(path, name + "-compaction")
                em._ASM_CKPT_STATS["skipped"] = {}
                wrote = self.doc(path)                                       # the settle after the compaction's whole parse
                skipped = dict(em.asm_checkpoint_stats()["skipped"])
                d = _doc(path)
                _pre1, cut1 = doc_cut(path)
                if road == "restart":
                    self._restart(path)
                res2 = dict(G.trline(t + 50, "tu_pw_a2_0", "pw_r2", "pw_a2", content="12 shards, 0 errors"), promptId="pw")
                self._append(path, [res2])
                tree2, modes2, moved2 = self._parse_counted(path)
                got2, cut2 = self._served(tree2)
                ref2 = self._snapshot(path, name + "-result")
                call3 = G.aline(t + 60, "one more query against it", "pw_a3", "pw_r2", tools=("Bash",), stop="tool_use")
                res3 = dict(G.trline(t + 70, "tu_pw_a3_0", "pw_r3", "pw_a3", content="the search answers from the new index"),
                            promptId="pw")
                done = G.aline(t + 80, "the index is rebuilt and answering", "pw_a4", "pw_r3", stop="end_turn")
                self._append(path, [call3, res3, done])
                tree3, modes3, moved3 = self._parse_counted(path)
                got3, cut3 = self._served(tree3)
                self.assertSameTree(got1, self.cold(ref1), "after the compaction")
                self.assertSameTree(got2, self.cold(ref2), "after the first tool result past it")
                self.assertSameTree(got3, self.cold(path), "after the turn's end")
                self.assertGreaterEqual(tail * em._ASM_TAIL_SHARE, pre0, "the premise: the compaction lands with the tail at the share")
                self.assertEqual((modes1, moved1.get("full", 0)), (["full"], 1), "the premise: the compaction parses whole: %s" % moved1)
                self.assertTrue(wrote, "the premise: the settle rewrites the document: %s" % skipped)
                self.assertGreater(cut1, cut0, "the premise: the rewritten document's cut advanced")
                self.assertNotIn(s, {row[0] for row in d["records"]}, "the premise: the summary is past the rewritten document's cut")
                folds = [("the tool result past the compaction", modes2, moved2, cut2), ("the turn's end", modes3, moved3, cut3)]
                if road == "reseat":
                    counts = (moved2.get("full", 0), moved2.get("g:reseat", 0), moved2.get("restore:afterDemote", 0),
                              moved2.get("fold:attachedPass", 0), moved2.get("g:promptid", 0))
                    self.assertEqual((modes2, counts), (["restore"], (0, 1, 1, 0, 0)),
                                     "the next parse re-seats the whole entry on the rewritten document, counted as a "
                                     "re-seat alone: no attached pass, no prompt-id demotion (review round four): %s" % moved2)
                    folds = folds[1:]
                for what, modes, moved, cut in folds:
                    self.assertEqual((modes, moved.get("full", 0), moved.get("g:promptid", 0), moved.get("fold:attachedPass", 0)),
                                     (["fold"], 0, 0, 1), "%s folds onto the entry restored from the rewritten document: counters "
                                     "moved %s" % (what, moved))
                    self.assertGreater(cut, 0, "%s: still the restored shape, the render floor at the cut" % what)
                self.assertNotIn("pw", d["gates"]["boundary_pids"], "the rewritten document's gates name the summaries before its cut")
                ran.append(road)
        self.assertEqual(ran, ["reseat", "restart"])

    def test_a_prompt_id_only_the_documents_seed_names_still_demotes(self):
        """The narrowed arm's other side: a restored entry holds the tail's records alone, and the document's gates name the
        prompt ids of the summaries before its cut, for which the entry cannot see whether the boundary is attached. A tail
        record wearing such an id demotes as before (`g:promptid:boundary`, and `boundarySeed` for the seed's part) and parses
        whole; the served tree equals a cold parse."""
        recs = episode(NOW - 86400)
        i = next(j for j, r in enumerate(recs) if r.get("uuid") == "s160")
        recs[i] = dict(recs[i], promptId="pp")                      # turn 160's compaction summary, before the document's cut
        path = self.write("seed-pid", recs)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
        d = _doc(path)
        self.assertIn("s160", {row[0] for row in d["records"]}, "the premise: the summary is a pre-cut record")
        self.assertIn("pp", d["gates"]["boundary_pids"], "the premise: the document's gates name its prompt id")
        self._restart(path)
        t = self.after(recs, 60)
        ask = dict(G.uline(t, "and the notes-api search cursor?", "xu_pp", recs[-1]["uuid"]), promptId="pp")
        self._append(path, [ask, G.aline(t + 20, "the cursor pages the index", "xa_pp", "xu_pp", stop="end_turn")])
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after a record wearing the pre-cut summary's prompt id")
        self.assertEqual((modes, moved.get("g:promptid", 0), moved.get("g:promptid:boundary", 0), moved.get("g:promptid:boundarySeed", 0),
                          moved.get("fold:attachedPass", 0), moved.get("full:demoted", 0)), (["full"], 1, 1, 1, 0, 1),
                         "the restored entry cannot vouch for the pre-cut summary: the boundary arm demotes, a whole parse: %s" % moved)

    def test_a_record_wearing_the_summarys_id_that_rewinds_onto_the_pre_compaction_leaf_parses_whole(self):
        """A delta the narrowed arm lets through and the descent then refuses parses whole, as before the narrowing (review of
        2026-10-03, round one). The document is written after an automatic compaction in the middle of a turn, so its cut is the
        compaction's turn and its pre-cut spine ends at the first tool result, the pre-compaction leaf; the summary wears the
        turn's prompt id. The model's second call then answers, its result wearing that id and chained onto the first result,
        under the pair. The gate lets it through (the pair is attached), the descent refuses it (the leaf rewinds), and the
        descent's restore road read the tail onto the document's tip and served one turn more than the cold parse: the second
        result as a turn of its own. The settle writes nothing for a restored entry, so the same document served that tree at
        the next record and at every boot. Now the record parses whole (`g:promptid:passedDemote`), the settle writes a
        document from that whole entry, and each served tree equals a cold parse: at the record, at the next record, and at
        the restart. From the whole entry the document was written from, and from an entry a restart restored."""
        ran = []
        for road in ("whole", "restart"):
            with self.subTest(road=road):
                name = "pid-rewind-" + road
                recs = episode(NOW - 86400)
                t = self.after(recs, 60)
                opener = dict(G.uline(t, "rebuild the notes-api search index", "pv_u", recs[-1]["uuid"]), promptId="pv")
                call1 = G.aline(t + 10, "rebuilding two shards at once", "pv_a1", "pv_u", tools=("Bash", "Bash"), stop="tool_use")
                res1 = dict(G.trline(t + 20, "tu_pv_a1_0", "pv_r1", "pv_a1", content="rebuilt shard 1 of 12"), promptId="pv")
                pair, s = compaction(t + 30, "pv", "pv_r1")
                pair[1]["promptId"] = "pv"                                   # the summary wears the prompt id of the turn it compacted in
                call2 = G.aline(t + 40, "checking the rebuilt index", "pv_a2", s, tools=("Bash",), stop="tool_use")
                path = self.write(name, recs + [opener, call1, res1] + pair + [call2])
                self.fresh()
                self.parse(path)
                self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
                d = _doc(path)
                self.assertEqual((d["records"][d["spine"][-1]][0], s in {row[0] for row in d["records"]}), ("pv_r1", False),
                                 "the premise: the cut is the compaction's turn, the pre-cut spine's tip the pre-compaction leaf")
                if road == "restart":
                    self._restart(path)
                late = dict(G.trline(t + 50, "tu_pv_a1_1", "pv_r1b", "pv_r1", content="rebuilt shard 2 of 12"), promptId="pv")
                self._append(path, [late])
                tree1, modes1, moved1 = self._parse_counted(path)
                got1, _cut1 = self._served(tree1)
                ref1 = self._snapshot(path, name + "-late")
                self.doc(path)                                               # the settle after the step
                self._append(path, [G.aline(t + 60, "both shards are rebuilt", "pv_a3", "pv_r1b", stop="end_turn")])
                tree2, modes2, moved2 = self._parse_counted(path)
                got2, _cut2 = self._served(tree2)
                ref2 = self._snapshot(path, name + "-reply")
                self.doc(path)
                self.fresh()                                                 # the restart
                boot_modes = []
                boot, _cut = self._served(self.parse(path, boot_modes))
                self.assertSameTree(got1, self.cold(ref1), "the result wearing the summary's prompt id, chained onto the pre-compaction leaf")
                self.assertSameTree(got2, self.cold(ref2), "the next record")
                self.assertSameTree(boot, self.cold(path), "the restart (road %s)" % boot_modes)
                self.assertEqual((modes1, moved1.get("g:promptid", 0), moved1.get("g:promptid:passedDemote", 0), moved1.get("g:descent", 0),
                                  moved1.get("restore:afterDemote", 0), moved1.get("fold:attachedPass", 0)), (["full"], 1, 1, 0, 0, 0),
                                 "the descent refuses the passed delta, which parses whole under promptid: counters moved %s" % moved1)
                ran.append(road)
        self.assertEqual(ran, ["whole", "restart"])

    def test_a_compaction_in_a_delta_the_arm_let_through_parses_whole(self):
        """The compaction's filing obeys the same rule (review of 2026-10-03, round one): one append holding a tool result that
        wears the attached summary's prompt id and the turn's second automatic compaction parses whole under promptid
        (`g:promptid:passedDemote`): the result marks the delta. Without the result, the compaction alone in its append takes
        the restore road, since its own summary does not mark the delta (the next test). The served tree equals a cold
        parse."""
        path, recs = self._documented("pid-second")
        self._restart(path)
        t = self.after(recs, 60)
        opener = dict(G.uline(t, "rebuild the notes-api search index", "py_u", recs[-1]["uuid"]), promptId="py")
        call1 = G.aline(t + 10, "rebuilding the index", "py_a1", "py_u", tools=("Bash",), stop="tool_use")
        res1 = dict(G.trline(t + 20, "tu_py_a1_0", "py_r1", "py_a1", content="rebuilt shard 1 of 12"), promptId="py")
        self._append(path, [opener, call1, res1])
        self._parse_counted(path)                                            # the turn opens: a fold onto the restored entry
        pair1, s1 = compaction(t + 30, "py1", "py_r1")
        pair1[1]["promptId"] = "py"                                          # the summary wears the prompt id of the turn
        call2 = G.aline(t + 40, "checking the rebuilt index", "py_a2", s1, tools=("Bash",), stop="tool_use")
        self._append(path, pair1 + [call2])
        _tree1, modes1, moved1 = self._parse_counted(path)                  # the first compaction: the restore road
        res2 = dict(G.trline(t + 50, "tu_py_a2_0", "py_r2", "py_a2", content="12 shards, 0 errors"), promptId="py")
        pair2, s2 = compaction(t + 60, "py2", "py_r2")
        pair2[1]["promptId"] = "py"
        self._append(path, [res2] + pair2 + [G.aline(t + 70, "one more query against it", "py_a3", s2, tools=("Bash",), stop="tool_use")])
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after the append holding the result and the second compaction")
        self.assertRestored(modes1, moved1, "boundary", "the premise: the first compaction")
        self.assertEqual((modes, moved.get("g:promptid", 0), moved.get("g:promptid:passedDemote", 0), moved.get("g:boundary", 0),
                          moved.get("restore:afterDemote", 0)), (["full"], 1, 1, 0, 0),
                         "the compaction in a passed delta parses whole under promptid: counters moved %s" % moved)

    def test_a_turns_later_compaction_alone_in_its_append_restores(self):
        """A compaction summary does not mark its own delta (review of 2026-10-03, round two). The turn's second automatic
        compaction lands alone in its append (the boundary, the summary and the model's next call), its summary wearing the
        turn's prompt id, which the first summary already put on record, so the narrowed arm lets the summary through. When
        the summary marked its own delta, the compaction parsed whole under `g:promptid:passedDemote`; it takes the restore
        road (`g:boundary`, `restore:afterDemote`), as every compaction does, and the served tree equals a cold parse."""
        path, recs = self._documented("pid-second-alone")
        self._restart(path)
        t = self.after(recs, 60)
        opener = dict(G.uline(t, "rebuild the notes-api search index", "px_u", recs[-1]["uuid"]), promptId="px")
        call1 = G.aline(t + 10, "rebuilding the index", "px_a1", "px_u", tools=("Bash",), stop="tool_use")
        res1 = dict(G.trline(t + 20, "tu_px_a1_0", "px_r1", "px_a1", content="rebuilt shard 1 of 12"), promptId="px")
        self._append(path, [opener, call1, res1])
        self._parse_counted(path)                                            # the turn opens: a fold onto the restored entry
        pair1, s1 = compaction(t + 30, "px1", "px_r1")
        pair1[1]["promptId"] = "px"                                          # the summary wears the prompt id of the turn
        call2 = G.aline(t + 40, "checking the rebuilt index", "px_a2", s1, tools=("Bash",), stop="tool_use")
        self._append(path, pair1 + [call2])
        _tree1, modes1, moved1 = self._parse_counted(path)                  # the first compaction: the restore road
        self._append(path, [dict(G.trline(t + 50, "tu_px_a2_0", "px_r2", "px_a2", content="12 shards, 0 errors"), promptId="px")])
        _tree2, modes2, moved2 = self._parse_counted(path)                  # a result wearing the id: the fold the arm saves
        pair2, s2 = compaction(t + 60, "px2", "px_r2")
        pair2[1]["promptId"] = "px"
        self._append(path, pair2 + [G.aline(t + 70, "one more query against it", "px_a3", s2, tools=("Bash",), stop="tool_use")])
        tree, modes, moved = self._parse_counted(path)
        got, cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after the second compaction alone in its append")
        self.assertRestored(modes1, moved1, "boundary", "the premise: the first compaction")
        self.assertEqual((modes2, moved2.get("fold:attachedPass", 0)), (["fold"], 1),
                         "the premise: a result wearing the id folds on the arm's pass: %s" % moved2)
        self.assertEqual((moved.get("g:promptid", 0), moved.get("g:promptid:passedDemote", 0)), (0, 0),
                         "the summary wearing the id does not mark its own delta: counters moved %s" % moved)
        self.assertRestored(modes, moved, "boundary", "the second compaction alone in its append")
        self.assertGreater(cut, 0, "the restored shape")

    def _parallel_call_compaction(self, name):
        """The shape of the review's second round, parsed whole and documented: a long session, then a turn whose model message
        makes two tool calls in one record, and an automatic compaction anchored on that record before either result lands,
        its summary wearing the turn's prompt id. The settle writes the document from that whole entry, cut at the
        compaction's turn, so its pre-cut spine ends at the call record, and marks the entry for its re-seat. Returns the
        path and a time past every record."""
        recs = episode(NOW - 86400)
        t = self.after(recs, 60)
        opener = dict(G.uline(t, "rebuild the notes-api search index", "pk_u", recs[-1]["uuid"]), promptId="pk")
        calls = G.aline(t + 10, "rebuilding two shards at once", "pk_a1", "pk_u", tools=("Bash", "Bash"), stop="tool_use")
        pair, s = compaction(t + 20, "pk", "pk_a1")
        pair[1]["promptId"] = "pk"                                           # the summary wears the prompt id of the turn it compacted in
        path = self.write(name, recs + [opener, calls] + pair)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
        d = _doc(path)
        self.assertEqual((d["records"][d["spine"][-1]][0], s in {row[0] for row in d["records"]}), ("pk_a1", False),
                         "the premise: the cut is the compaction's turn, the pre-cut spine's tip the call record")
        self.assertIs((self._entry(path) or {}).get("reseat"), True, "the premise: the writer marked the whole entry for its re-seat")
        return path, t + 30

    def test_a_result_of_a_parallel_call_a_compaction_was_anchored_on_is_not_re_seated(self):
        """The re-seat steps aside for a delta the narrowed arm let through that passed on a parallel batch's link (review of
        2026-10-03, round two). A result of the first call lands, wearing the turn's prompt id and parented at the call record:
        the arm lets it through (the pair is attached), and the gates pass it on the batch link. The settle had marked the
        whole entry for its re-seat when it wrote the document, so the delta was re-seated onto the restore road, whose chain
        proof accepted the result as a child of the document's tip: the restore served one turn more than the cold parse (the
        result as a turn of its own), and the settle, which writes nothing for a restored entry, left that tree for the next
        record and every boot. Now the fold's kept check rules (the result moves the leaf off the pair), the delta parses
        whole under `g:promptid:passedDemote`, and the settle writes a document from that whole entry. A parse with nothing
        new then re-seats on it: the thread bits that result's delta left speak for no other parse. Each served tree equals a
        cold parse: at the result, at the re-seat, at the call's second result, and at the restart."""
        path, t = self._parallel_call_compaction("pid-parallel-reseat")
        self._append(path, [dict(G.trline(t, "tu_pk_a1_0", "pk_r0", "pk_a1", content="rebuilt shard 1 of 12"), promptId="pk")])
        tree1, modes1, moved1 = self._parse_counted(path)
        got1, _cut1 = self._served(tree1)
        ref1 = self._snapshot(path, "parallel-reseat-result")
        wrote = self.doc(path)                                               # the settle after the step
        marked = (self._entry(path) or {}).get("reseat")
        tree2, modes2, moved2 = self._parse_counted(path)                   # nothing new: the re-seat on the rewritten document
        got2, _cut2 = self._served(tree2)
        self._append(path, [dict(G.trline(t + 10, "tu_pk_a1_1", "pk_r1", "pk_a1", content="rebuilt shard 2 of 12"), promptId="pk")])
        tree3, modes3, moved3 = self._parse_counted(path)
        got3, _cut3 = self._served(tree3)
        ref3 = self._snapshot(path, "parallel-reseat-second")
        self.doc(path)
        self.fresh()                                                         # the restart
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertSameTree(got1, self.cold(ref1), "the result of the parallel call, parented at the call record")
        self.assertSameTree(got2, self.cold(ref1), "the parse with nothing new after the settle")
        self.assertSameTree(got3, self.cold(ref3), "the call's second result (road %s)" % modes3)
        self.assertSameTree(boot, self.cold(path), "the restart (road %s)" % boot_modes)
        self.assertEqual((modes1, moved1.get("g:promptid", 0), moved1.get("g:promptid:passedDemote", 0), moved1.get("g:reseat", 0),
                          moved1.get("g:kept", 0), moved1.get("restore", 0), moved1.get("fold:attachedPass", 0)),
                         (["full"], 1, 1, 0, 0, 0, 0),
                         "the re-seat steps aside and the kept check's rejection parses whole: counters moved %s" % moved1)
        self.assertEqual((wrote, marked), (True, True), "the premise: the settle writes a document from the whole entry and marks it")
        self.assertEqual((modes2, moved2.get("g:reseat", 0)), (["restore"], 1),
                         "with nothing new, the marked entry re-seats on the rewritten document: counters moved %s" % moved2)

    def test_a_result_of_a_parallel_call_a_compaction_was_anchored_on_parses_whole_at_the_kept_check(self):
        """The kept check's own branch for a passed batch delta (review of 2026-10-03, round two): the same shape over a whole
        entry whose document stands without the re-seat mark (the writer leaves it off when the tail is at the churn share or
        a postal author waits on the log; cleared here by hand). The result folds, the kept check rejects it, and the
        rejection parses whole under `g:promptid:passedDemote` instead of filing `kept` and taking the restore road a batch's
        rejection takes, where the restore served the result as a turn of its own at the parse and at every boot. Each served
        tree equals a cold parse, at the parse and at the restart."""
        path, t = self._parallel_call_compaction("pid-parallel-kept")
        self._entry(path)["reseat"] = False
        self._append(path, [dict(G.trline(t, "tu_pk_a1_0", "pk_r0", "pk_a1", content="rebuilt shard 1 of 12"), promptId="pk")])
        tree1, modes1, moved1 = self._parse_counted(path)
        got1, _cut1 = self._served(tree1)
        ref1 = self._snapshot(path, "parallel-kept-result")
        self.doc(path)                                                       # the settle after the step
        self.fresh()                                                         # the restart
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertSameTree(got1, self.cold(ref1), "the result of the parallel call, parented at the call record")
        self.assertSameTree(boot, self.cold(path), "the restart (road %s)" % boot_modes)
        self.assertEqual((modes1, moved1.get("g:promptid", 0), moved1.get("g:promptid:passedDemote", 0), moved1.get("g:kept", 0),
                          moved1.get("g:reseat", 0), moved1.get("restore", 0), moved1.get("fold:attachedPass", 0)),
                         (["full"], 1, 1, 0, 0, 0, 0),
                         "the kept check rejects the passed batch delta, which parses whole under promptid: counters moved %s" % moved1)

    def test_a_turn_compacted_twice_demotes_on_the_seed_after_a_restart(self):
        """The limit the narrowed arm keeps (review of 2026-10-03, round one): a turn whose two automatic compactions both wear
        its prompt id. A document written after the second has the first summary before its cut, so its gates name the id,
        and an entry a restart restored from it cannot see that pair. A tool result wearing the id then demotes on the seed
        alone (`g:promptid:boundarySeed`), though the tail's own summary wearing it hangs off an attached boundary, and parses
        whole; the served tree equals a cold parse."""
        recs = episode(NOW - 86400)
        t = self.after(recs, 60)
        opener = dict(G.uline(t, "rebuild the notes-api search index", "pq_u", recs[-1]["uuid"]), promptId="pq")
        call1 = G.aline(t + 10, "rebuilding shard 1", "pq_a1", "pq_u", tools=("Bash",), stop="tool_use")
        res1 = dict(G.trline(t + 20, "tu_pq_a1_0", "pq_r1", "pq_a1", content="rebuilt shard 1 of 12"), promptId="pq")
        pair1, s1 = compaction(t + 30, "pq1", "pq_r1")
        call2 = G.aline(t + 40, "rebuilding shard 2", "pq_a2", s1, tools=("Bash",), stop="tool_use")
        res2 = dict(G.trline(t + 50, "tu_pq_a2_0", "pq_r2", "pq_a2", content="rebuilt shard 2 of 12"), promptId="pq")
        pair2, s2 = compaction(t + 60, "pq2", "pq_r2")
        for p in (pair1, pair2):
            p[1]["promptId"] = "pq"                                          # both summaries wear the prompt id of the turn
        call3 = G.aline(t + 70, "rebuilding shard 3", "pq_a3", s2, tools=("Bash",), stop="tool_use")
        path = self.write("pid-twice", recs + [opener, call1, res1] + pair1 + [call2, res2] + pair2 + [call3])
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the document is written: %s" % em.asm_checkpoint_stats()["skipped"])
        d = _doc(path)
        rows = {row[0] for row in d["records"]}
        self.assertEqual((s1 in rows, s2 in rows), (True, False), "the premise: the first summary is before the cut, the second past it")
        self.assertIn("pq", d["gates"]["boundary_pids"], "the premise: the document's gates name the turn's id")
        self._restart(path)
        self._append(path, [dict(G.trline(t + 80, "tu_pq_a3_0", "pq_r3", "pq_a3", content="rebuilt shard 3 of 12"), promptId="pq")])
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after a tool result wearing the turn's id")
        self.assertEqual((modes, moved.get("g:promptid", 0), moved.get("g:promptid:boundary", 0), moved.get("g:promptid:boundarySeed", 0),
                          moved.get("fold:attachedPass", 0), moved.get("full:demoted", 0)), (["full"], 1, 1, 1, 0, 1),
                         "the seed names the id, so the boundary arm demotes and the parse is whole: counters moved %s" % moved)

    def test_a_compaction_anchored_before_the_cut_is_refused_by_the_chain_proof_and_parsed_whole(self):
        """The guard the restore road brings with it: a compaction anchored on a pre-cut record (a rewind, then a compaction)
        invalidates the document's pre-cut verdicts, so the chain proof refuses it and the parse is whole, as at boot."""
        path, recs = self._documented("interior")
        self._restart(path)
        pair, s = compaction(self.after(recs, 120), "c5", "a10")
        self._append(path, pair + turn(self.after(recs, 150), "c5", s))
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertSameTree(got, self.cold(path), "after a compaction anchored before the cut")
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("restore:chainRefused", 0), moved.get("full:demoted", 0)),
                         (["full"], 1, 1, 1), "the restore is tried and the chain proof refuses it: counters moved %s" % moved)

    def _tip_time(self, path, recs):
        """The stamp of the standing document's pre-cut spine tip."""
        d = _doc(path)
        tip = d["records"][d["spine"][-1]][0]
        return next(em.parse_z(r["timestamp"]) for r in recs if r.get("uuid") == tip)

    def _past_the_compaction(self, reason, path, recs, s, t):
        """Records a fold refuses for `reason`, chained onto the compaction's summary `s`, from `t` on."""
        if reason == "ts":        # typed 30 s before the pre-cut tip, written after the compaction: a cold parse sorts it into
            #                       the pre-cut turns
            return [G.uline(self._tip_time(path, recs) - 30, "typed earlier, written after the compaction", "xu_ts", s),
                    G.aline(t + 20, "answered after the compaction", "xa_ts", "xu_ts", stop="end_turn")]
        if reason == "skill-link":   # the Skill call the pre-cut payload after turn 150 names
            call = G.aline(t + 10, "loading the notes skill", "xa_sk", "xu_sk", tools=("Skill",), stop="tool_use")
            call["message"]["content"][-1]["id"] = "tu_skill_150"
            return [G.uline(t, "use the notes skill", "xu_sk", s), call,
                    G.aline(t + 20, "used the notes skill", "xa_sk2", "xa_sk", stop="end_turn")]
        if reason == "uuid-known":   # the record the pre-cut orphan after turn 150 named as its parent
            return [G.uline(t, "the ask the orphaned one answered", "ghost-150", s),
                    G.aline(t + 20, "answered", "xa_gh", "ghost-150", stop="end_turn")]
        if reason == "promptid":     # the local command's output, wearing the prompt id of the pre-cut wrapper after turn 150: a
            #                          command-wrapper record under a folded prompt id can re-classify the records already read
            return [dict(G.uline(t, "<local-command-stdout>tokens: plenty left</local-command-stdout>", "xo_pi", s),
                         promptId="pp_usage_150")] + turn(t + 30, "pi", "xo_pi")
        raise AssertionError(reason)

    def test_a_compaction_masks_no_later_record_that_parses_whole_for_its_own_reason(self):
        """The gates read a delta to its end before a compaction's demotion is filed: a record past the compaction that the
        fold refuses for its own reason (a stamp before the records folded, a Skill call a pre-cut payload names, a uuid a
        pre-cut record named as its parent, a command-wrapper record wearing a pre-cut prompt id) demotes for that reason and
        parses whole, from the restored entry and from the whole one. The restore would have proved only that the tail chains
        onto the document, and it served a tree a cold parse does not build; the settle after the whole parse rewrites the
        document, so the next boot equals cold as well (review of 2026-09-24: under the first cut of the rule these restored,
        and every later boot served the same tree)."""
        shapes = {"ts": {}, "skill-link": {"skill_at": 150}, "uuid-known": {"orphan_at": 150}, "promptid": {"wrapper_at": 150}}
        ran = []
        for reason, shape in shapes.items():
            for entry in ("restored", "whole"):
                with self.subTest(reason=reason, entry=entry):
                    name = "mask-%s-%s" % (reason, entry)
                    path, recs = self._documented(name, **shape)
                    if entry == "restored":
                        self._restart(path)
                    pair, s = compaction(self.after(recs, 120), "mk", recs[-1]["uuid"])
                    self._append(path, pair + self._past_the_compaction(reason, path, recs, s, self.after(recs, 130)))
                    tree, modes, moved = self._parse_counted(path)
                    got, _cut = self._served(tree)
                    ref = self._snapshot(path, name)
                    self.doc(path)                                         # the settle after the step
                    self.fresh()
                    boot_modes = []
                    boot, _cut = self._served(self.parse(path, boot_modes))
                    self.assertSameTree(got, self.cold(ref), "live, a compaction then a record refused for %s" % reason)
                    self.assertSameTree(boot, self.cold(path), "at the next boot (road %s)" % boot_modes)
                    self.assertEqual((modes, moved.get("g:" + reason, 0), moved.get("g:boundary", 0), moved.get("restore:afterDemote", 0)),
                                     (["full"], 1, 0, 0), "the delta demotes for %s and parses whole: counters moved %s" % (reason, moved))
                    ran.append((reason, entry))
        self.assertEqual(len(ran), 8, "every shape ran on both entries: %s" % ran)

    def test_a_compaction_once_the_tail_has_reached_the_share_parses_whole_and_the_settle_advances_the_cut(self):
        """The churn bound holds on the restore road: a compaction restores only while the leaf's bytes past the standing
        document's cut, times the share, are under the pre-cut bytes. Once the tail has reached the share the compaction parses
        whole, the settle writes a document whose cut is past the compaction, and the next boot restores from it. Without this
        the cut of the entry a boot restores never moved again: the fold's own share gate reads only an atoms-only restore and a
        re-seated entry, a boot's restore through the lazy index is neither, and a restored entry writes nothing, so the tail
        every restore reads grew for the session's life (review of 2026-09-24: 34 MB past the cut after four compactions at 150
        to 184 MB, and the boot's restore doubled). On that boot-restored entry the share test is this change's own
        (`restore:pastShare` beside `g:boundary`). The whole entry the document was written from is re-seated on it by the
        growth's parse (the writer marked it), so the fold's churn gate already holds it: at the share the gate demotes it
        before the compaction is read (`g:tailShare`), and the compaction parses whole under that reason."""
        # per entry: the growth's road with its g:reseat count, then the compaction's road and counters
        # (g:boundary, g:tailShare, restore:pastShare, restore:afterDemote, full:demoted)
        want = {"restored": ((["fold"], 0), (["full"], 1, 0, 1, 0, 1)),
                "whole": ((["restore"], 1), (["full"], 0, 1, 0, 0, 1))}
        ran = []
        for entry in ("restored", "whole"):
            with self.subTest(entry=entry):
                name = "share-" + entry
                path, recs = self._documented(name)
                if entry == "restored":
                    self._restart(path)
                pre0, cut0 = doc_cut(path)
                grow = filler(self.after(recs, 60), entry, recs[-1]["uuid"],
                              pre0 // em._ASM_TAIL_SHARE - (os.path.getsize(path) - cut0) + 1)
                self._append(path, grow)
                _t, gmodes, gmoved = self._parse_counted(path)              # the growth: a fold, or the whole entry's re-seat;
                #                                                             nothing here settles
                pair, s = compaction(self.after(recs + grow, 120), "sh", grow[-1]["uuid"])
                self._append(path, pair + turn(self.after(recs + grow, 150), "sh", s))
                tail = os.path.getsize(path) - cut0
                tree, modes, moved = self._parse_counted(path)
                got, _cut = self._served(tree)
                ref = self._snapshot(path, name)
                em._ASM_CKPT_STATS["skipped"] = {}
                wrote = self.doc(path)
                skipped = dict(em.asm_checkpoint_stats()["skipped"])
                pre1, cut1 = doc_cut(path)
                self.fresh()
                boot_modes = []
                boot, _cut = self._served(self.parse(path, boot_modes))
                growth, counts = want[entry]
                self.assertEqual((gmodes, gmoved.get("g:reseat", 0)), growth, "the growth's road: counters moved %s" % gmoved)
                self.assertGreaterEqual(tail * em._ASM_TAIL_SHARE, pre0, "the compaction lands with the tail at the share")
                self.assertSameTree(got, self.cold(ref), "after the compaction")
                self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("g:tailShare", 0), moved.get("restore:pastShare", 0),
                                  moved.get("restore:afterDemote", 0), moved.get("full:demoted", 0)), counts,
                                 "the compaction parses whole once the tail has reached the share: counters moved %s" % moved)
                self.assertTrue(wrote, "the settle writes a new document: %s" % skipped)
                self.assertGreater(cut1, cut0, "the document's cut advanced")
                self.assertLess((os.path.getsize(path) - cut1) * em._ASM_TAIL_SHARE, pre1, "the tail past the new cut is under the share")
                self.assertEqual(boot_modes, ["restore"], "the next boot restores from the new document")
                self.assertSameTree(boot, self.cold(path), "at the next boot")
                ran.append(entry)
        self.assertEqual(ran, ["restored", "whole"])

    def test_a_compaction_on_an_entry_with_no_document_parses_whole_and_is_not_counted_past_the_share(self):
        """`restore:pastShare` names a compaction whose tail had reached the share of a document it could have restored from.
        A whole entry that wrote no document (here a session under the first document's floor, so the settle declines) has
        nothing to measure: its compaction parses whole as it always did, counted under `g:boundary` and `full:demoted` only.
        Counted past the share, every compaction of every young session would have filled the counter in /perf with cases
        that never had a restore to decline (verification of the review's fold, 2026-09-24)."""
        saved = em._ASM_FIRST_DOC_MIN
        self.addCleanup(setattr, em, "_ASM_FIRST_DOC_MIN", saved)
        em._ASM_FIRST_DOC_MIN = 10 ** 9                      # the floor holds the session's first document back
        recs = episode(NOW - 86400)
        path = self.write("nodoc", recs)
        self.fresh()
        self.parse(path)
        em._ASM_CKPT_STATS["skipped"] = {}
        wrote = self.doc(path)
        skipped = dict(em.asm_checkpoint_stats()["skipped"])
        pair, s = compaction(self.after(recs, 120), "nd", recs[-1]["uuid"])
        self._append(path, pair + turn(self.after(recs, 150), "nd", s))
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        self.assertFalse(wrote, "the floor declines the first document: %s" % skipped)
        cp = em._asm_ckpt_file(path)
        self.assertTrue(cp is None or not cp.exists(), "no document stands for the leaf")
        self.assertSameTree(got, self.cold(path), "after the compaction")
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:pastShare", 0),
                          moved.get("restore:afterDemote", 0)), (["full"], 1, 1, 0, 0),
                         "a compaction with no document to measure parses whole, not counted past the share: %s" % moved)

    def _postal_compaction(self, name, restart, predates=False):
        """The postal episode parsed whole and its document written while the author waits on the log (with `predates`, the
        document as a writer before the wait record wrote it; and, with `restart`, a restart that restores the entry from that
        document); then a compaction under the share; the settle; the log catching up and a turn; the settle; and a restart.
        Returns what each step saw."""
        recs, row = postal_episode()
        path = self.write(name, recs)
        self.fresh()
        self.parse(path)
        out = {"waiting": set(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or ())}
        out["wrote"] = self.doc(path)
        out["docWait"] = _doc(path).get("postalWait")
        if predates:
            d = _doc(path)
            d.pop("postalWait", None)                                # the document as a writer before the record wrote it
            _write_doc(path, d)
        if restart:
            self._restart(path)
        out["entryWait"] = (self._entry(path) or {}).get("docPostalWait")
        pair, s = compaction(self.after(recs, 120), name, recs[-1]["uuid"])
        more = pair + turn(self.after(recs, 150), name, s)
        self._append(path, more)
        pre, cut_off = doc_cut(path)
        out["under"] = (os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE < pre
        _t, out["modes"], out["moved"] = self._parse_counted(path)
        self.doc(path)                                               # the settle after the compaction
        self.sent = [row]                                            # the log catches up
        self._append(path, turn(self.after(recs + more, 60), name + "-2", more[-1]["uuid"]))
        tree2, out["modes2"], out["moved2"] = self._parse_counted(path)
        out["got2"], _cut = self._served(tree2)
        self.doc(path)                                               # the settle after the heal
        self.fresh()
        boot_modes = []
        out["boot"], _cut = self._served(self.parse(path, boot_modes))
        out["bootModes"] = boot_modes
        out["cold"] = self.cold(path)
        return out

    def assertCompactionParsedWholeAndHealed(self, out, what):
        self.assertIn("u30", out["waiting"], "the fixture's premise: the marker waits on the log")
        self.assertTrue(out["wrote"], "the fixture's premise: the document is written while the author waits")
        self.assertTrue(out["under"], "the fixture's premise: the compaction lands with the tail under the share")
        moved = out["moved"]
        self.assertEqual((out["modes"], moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0),
                          moved.get("restore:pastShare", 0)), (["full"], 1, 1, 0, 0),
                         "%s: the compaction parses whole: counters moved %s" % (what, moved))
        self.assertEqual(out["modes2"], ["fold"], "%s: the whole entry folds the next turn: counters moved %s" % (what, out["moved2"]))
        self.assertEqual((author_of(out["got2"]) or {}).get("peer"), G.PEER,
                         "%s: the log caught up, and the served author is the peer's (%r)" % (what, author_of(out["got2"])))
        self.assertSameTree(out["got2"], out["cold"], "%s: after the log caught up" % what)
        self.assertEqual(out["bootModes"], ["restore"], "%s: the next restart restores" % what)
        self.assertSameTree(out["boot"], out["cold"], "%s: at the next restart" % what)

    def test_a_compaction_on_a_whole_entry_whose_postal_author_waits_on_the_log_parses_whole_and_heals(self):
        """A peer's message whose postal id the log does not hold yet gets a provisional author, and the whole entry re-authors
        it once the log catches up (the assembly's heal). The document carries the provisional author and no heal state, so the
        writer does not mark such an entry for its re-seat; restored from that document at a compaction, the entry served the
        provisional author after the log caught up, where a cold parse names the peer, until its next whole parse (found once
        main re-seated whole entries, 2026-09-24). The compaction parses whole instead, counted as it always was, and the parse
        after the log catches up folds, heals, and serves the cold parse's tree, as does the next restart."""
        self.assertCompactionParsedWholeAndHealed(self._postal_compaction("postal-whole", restart=False), "a whole entry")

    def test_a_compaction_on_an_entry_restored_from_a_document_written_while_a_postal_author_waited_parses_whole_and_heals(self):
        """The same author, frozen in the document, reaches an entry a restart restores from it: that entry has no heal state
        for the pre-cut part, so it serves the provisional author, and on main the compaction's whole parse was what healed it.
        Restored from the document at the compaction instead, the entry kept the provisional author after the log caught up and
        at the next restart, where a cold parse names the peer (a probe, 2026-09-24). The writer records its postal test in the
        document (`postalWait`), the restore carries it, and the compaction parses whole."""
        out = self._postal_compaction("postal-restored", restart=True)
        self.assertEqual((out["docWait"], out["entryWait"]), (True, True),
                         "the document records the wait and the restored entry carries it")
        self.assertCompactionParsedWholeAndHealed(out, "an entry restored at a restart")

    def test_a_compaction_on_a_whole_entry_healed_after_its_document_was_written_parses_whole(self):
        """The whole entry heals its provisional author once the log catches up, but the document it wrote while the author
        waited still carries that author, and the settle after the heal leaves the document standing (its tail is under the
        share). Restored from it at a compaction, the entry served the provisional author live and at the next restart, where
        a cold parse names the peer. The writer keeps the document's `postalWait` on the entry it wrote from, so the
        compaction parses whole, as every compaction does on main."""
        recs, row = postal_episode()
        path = self.write("postal-healed", recs)
        self.fresh()
        self.parse(path)
        self.assertIn("u30", ((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or (), "the premise: the marker waits")
        self.assertTrue(self.doc(path), "the premise: the document is written while the author waits")
        self.sent = [row]                                            # the log catches up
        t1 = turn(self.after(recs, 60), "healed-1", recs[-1]["uuid"])
        self._append(path, t1)
        _t, modes1, _m = self._parse_counted(path)                   # the whole entry folds and heals
        self.assertEqual(modes1, ["fold"], "the premise: the whole entry folds the turn")
        self.assertFalse(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec"), "the premise: the entry healed")
        em._ASM_CKPT_STATS["skipped"] = {}
        self.assertFalse(self.doc(path), "the premise: the settle after the heal writes nothing")
        self.assertEqual(em.asm_checkpoint_stats()["skipped"], {"written": 1}, "the premise: the document stands")
        allr = recs + t1
        pair, s = compaction(self.after(allr, 120), "healed", t1[-1]["uuid"])
        self._append(path, pair + turn(self.after(allr, 150), "healed-2", s))
        pre, cut_off = doc_cut(path)
        self.assertLess((os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE, pre, "the premise: under the share")
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        ref = self._snapshot(path, "postal-healed")
        self.doc(path)                                               # the settle after the compaction
        self.fresh()
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0)),
                         (["full"], 1, 1, 0), "the compaction parses whole: counters moved %s" % moved)
        self.assertEqual((author_of(got) or {}).get("peer"), G.PEER, "the served author is the peer's (%r)" % author_of(got))
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertEqual(boot_modes, ["restore"], "the next restart restores")
        self.assertSameTree(boot, self.cold(path), "at the next restart")

    def test_a_compaction_on_an_entry_restored_from_a_document_that_predates_the_wait_record_parses_whole_and_heals(self):
        """A document written before the writer recorded the wait carries no `postalWait`, even when an author waited as it
        was written, and it is still this version's document. Read as no wait, a compaction on an entry a restart restored
        from it restored again and kept the provisional author after the log caught up, live and at the next restart, where
        a cold parse names the peer (a probe, 2026-09-25). The restore reads the key's absence as a wait, so the compaction
        parses whole once, as on main, and the settle after it writes a document that records the wait either way."""
        self.assertCompactionParsedWholeAndHealed(self._postal_compaction("postal-predates", restart=True, predates=True),
                                                  "an entry restored from a document that predates the record")

    def test_a_compaction_after_a_rewrite_whose_sidecar_write_failed_parses_whole(self):
        """The writer replaces the document before it writes the sidecar, and a sidecar write that fails is the counted `write`
        skip with the new document already standing. Here an entry kept whole (a compaction anchored before the cut) rewrites its
        document while a peer's marker before the new cut waits on the log, the sidecar write fails, the log catches up and a fold
        heals the entry with no settle after it, and a compaction lands under the share. With the document's record of the wait
        kept on the entry only after the sidecar, the entry still said no wait, and the compaction restored the new document's
        provisional author, live and at the next restart, where a cold parse names the peer (a probe, 2026-09-25). The record is
        kept as soon as the document is replaced, so the compaction parses whole."""
        recs = episode(NOW - 86400)
        path = self.write("postal-sidecar", recs)
        self.fresh()
        self.parse(path)
        self.assertTrue(self.doc(path), "the premise: the first document is written")
        allr = list(recs)
        pair, s = compaction(self.after(allr, 120), "sck", "a10")      # anchored before the cut: the chain proof refuses it
        more = pair + turn(self.after(allr, 150), "sck", s)
        self._append(path, more)
        allr += more
        self.parse(path)
        self.assertTrue((self._entry(path) or {}).get("keepWhole"), "the premise: the entry is kept whole")
        t = self.after(allr, 60)
        more = [G.uline(t, "please raise the notes-api search page size to 50\n<!-- romp-msg-id: %s -->" % G.MID, "um_sc",
                        allr[-1]["uuid"], ps=None),
                G.aline(t + 20, "raised the notes-api search page size", "am_sc", "um_sc", stop="end_turn")]
        more += turn(self.after(allr + more, 60), "sc0", more[-1]["uuid"])
        more += turn(self.after(allr + more, 60), "sc1", more[-1]["uuid"])
        self._append(path, more)
        allr += more
        self.parse(path)
        self.assertIn("um_sc", ((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec") or (), "the premise: the marker waits")
        orig, reasons = Path.write_text, []
        def failing(p, *a, **k):
            if ".meta." in p.name:
                raise OSError("the sidecar write fails")
            return orig(p, *a, **k)
        Path.write_text = failing
        try:
            wrote = em.asm_checkpoint_write(path, SID, tree=self._trees.get(path), reason_out=reasons)
        finally:
            Path.write_text = orig
        self.assertEqual((wrote, reasons), (False, ["write"]), "the premise: the sidecar write fails")
        self.assertIs(_doc(path).get("postalWait"), True, "the premise: the rewritten document stands and records the wait")
        self.sent = [{"t": t + 1, "ev": "sent", "id": G.MID, "from": "api", "from_id": G.PEER, "to_id": SID,
                      "body": "ASK: raise the search page size"}]    # the log catches up
        nxt = turn(self.after(allr, 60), "sch", allr[-1]["uuid"])
        self._append(path, nxt)
        allr += nxt
        _t, modes1, _m = self._parse_counted(path)                   # a fold heals the entry; no settle before the compaction
        self.assertEqual(modes1, ["fold"], "the premise: the kept-whole entry folds the turn")
        self.assertFalse(((self._entry(path) or {}).get("st") or {}).get("postal_miss_rec"), "the premise: the entry healed")
        pair, s = compaction(self.after(allr, 120), "scc", allr[-1]["uuid"])
        self._append(path, pair + turn(self.after(allr, 150), "scc", s))
        pre, cut_off = doc_cut(path)
        self.assertLess((os.path.getsize(path) - cut_off) * em._ASM_TAIL_SHARE, pre, "the premise: under the share")
        tree, modes, moved = self._parse_counted(path)
        got, _cut = self._served(tree)
        ref = self._snapshot(path, "postal-sidecar")
        self.doc(path)                                               # the settle after the compaction
        self.fresh()
        boot_modes = []
        boot, _cut = self._served(self.parse(path, boot_modes))
        self.assertEqual((modes, moved.get("g:boundary", 0), moved.get("full:demoted", 0), moved.get("restore:afterDemote", 0)),
                         (["full"], 1, 1, 0), "the compaction parses whole: counters moved %s" % moved)
        self.assertEqual((author_of(got, "um_sc") or {}).get("peer"), G.PEER, "the served author is the peer's (%r)" % author_of(got, "um_sc"))
        self.assertSameTree(got, self.cold(ref), "after the compaction")
        self.assertEqual(boot_modes, ["restore"], "the next restart restores")
        self.assertSameTree(boot, self.cold(path), "at the next restart")


if __name__ == "__main__":
    unittest.main()
