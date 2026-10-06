#!/usr/bin/env python3
"""Golden-equivalence replay for the assembly cache (kernel/event_model.py `_assemble`).

The contract under test: parse_session THROUGH the assembly cache (folding appends) produces a
tree byte-identical to a from-scratch full parse, at EVERY prefix of every transcript. Two
independent module instances make that comparison honest — `emi` keeps its assembly cache across
steps (the live folding path) while `emr` has its cache cleared before every parse (always the
full path, which the golden suite already pins). Each scenario is replayed record by record and
the two trees are deep-compared at each step, so any divergence names the exact record that
caused it. The gate tests then trip every fast-path gate on purpose: equivalence must hold BY
DEMOTION (the fold refuses, the full path serves), pinned via the stats counters — and the one
clean-append case must actually FOLD, so the fast path can never silently rot into full-parsing
everything while the equivalence assertions stay green.

All scenarios are SYNTHETIC (placeholder UUIDs, invented text — per CLAUDE.md); the golden test
module's builders are reused so the replay corpus and the golden corpus cannot drift apart.

Run:    python3 tests/test_event_model_assembly.py
"""
import json
import os
import tempfile
import unittest
from romp_load import load_source
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "bin")

# Hermetic state BEFORE the loads (the modules resolve their state root at import time; only
# pytest runs conftest's floor). The golden module is loaded FIRST for its scenario builders —
# it re-points XDG_STATE_HOME to its own tempdir, which our loads then inherit: still hermetic.
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)
G = load_source("em_golden_scenarios",
                     os.path.join(HERE, "test_event_model_golden.py"))
emi = load_source("em_incremental", os.path.join(SCRIPTS, "romp-event-model"))
emr = load_source("em_reference", os.path.join(SCRIPTS, "romp-event-model"))

SID, NOW, T0 = G.SID, G.NOW, G.T0
iso = G.iso


def _text_of(blocks):
    return " ".join(b.get("text", "") for b in blocks if isinstance(b, dict))


def _tree(x):
    return json.loads(json.dumps(x, sort_keys=True))


class Replay(unittest.TestCase):
    """Record-by-record replay: incremental == full at every step."""
    maxDiff = None

    def _step(self, path, kw, label):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()   # the reference NEVER folds — every call is a full parse
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc), "incremental diverged from full at %s" % label)

    def replay(self, records, states=None, postal=None, min_folds=None, exact_folds=None):
        """Write `records` one at a time, comparing trees at every prefix."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            kw = {"candidate_files": [str(path)], "states": states,
                  "postal_log": list(postal or []), "now": NOW}
            folds0 = emi._ASM_STATS["fold"]
            path.write_text("")
            for i, r in enumerate(records):
                with open(path, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                self._step(path, kw, "record %d/%d" % (i + 1, len(records)))
            if min_folds is not None:
                self.assertGreaterEqual(emi._ASM_STATS["fold"] - folds0, min_folds,
                                        "the fast path never engaged — every step full-parsed")
            if exact_folds is not None:
                self.assertEqual(emi._ASM_STATS["fold"] - folds0, exact_folds,
                                 "fold count drifted — a record kind silently demotes to full")

    def test_all_single_file_golden_scenarios_replay_equivalently(self):
        for name, (records_fn, sent) in G.SINGLE_FILE.items():
            with self.subTest(scenario=name):
                states = G.IDLE_STATES if name == "idle_atom" else None
                self.replay(records_fn(), states=states, postal=sent)

    def test_plain_streaming_replay_actually_folds(self):
        # the red-first pin for the whole feature: a clean append-only stream must take the
        # fast path on EVERY step after the first (equivalence alone would pass trivially if
        # steps demoted to full — an exact count catches a single record kind rotting)
        recs = G.scenario_author_kinds()
        self.replay(recs, postal=G.SENT_LOG, exact_folds=len(recs) - 1)

    def test_resume_lineage_replays_equivalently_across_files(self):
        # file A is static (identity-stable non-leaf), file B streams; the states rows carry
        # the recorded fork so the stitched walk crosses files on both paths
        with tempfile.TemporaryDirectory() as td:
            pa = Path(td) / (G.FSID_A + ".jsonl")
            pb = Path(td) / (G.FSID_B + ".jsonl")
            pa.write_text("\n".join(json.dumps(r) for r in G.scenario_resume_lineage_fileA()) + "\n")
            rows = [{"t": T0 + 500, "resumeFork": {"to": G.FSID_B, "from": G.FSID_A}}]
            kw = {"candidate_files": [str(pa), str(pb)], "states": rows,
                  "postal_log": [], "now": NOW}
            pb.write_text("")
            folds0 = emi._ASM_STATS["fold"]
            for i, r in enumerate(G.scenario_resume_lineage_fileB()):
                with open(pb, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                inc = emi.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                emr._ASM_CACHE.clear()
                ref = emr.parse_session(str(pb), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                self.assertEqual(_tree(ref), _tree(inc), "diverged at fileB record %d" % (i + 1))
            self.assertGreater(emi._ASM_STATS["fold"] - folds0, 0,
                               "multi-file sessions silently demote to permanent full parses")


class GarbledTimestamps(unittest.TestCase):
    """A malformed record must never eclipse the rest of the session (T210): a kept record whose
    timestamp does not parse used to reach segment_turns' (t, _seq) sort as t=None and TypeError
    the WHOLE parse — one corrupt line took down the session view for chat and judges alike. The
    fix fails toward SHOWING: ingest borrows the last good stamp seen in file order (the record's
    real neighbor), so the atom stays visible at a truthful adjacent time, counted and noted
    loudly. Skipping it instead would silently drop a possibly-real ask — the one fatal error."""
    maxDiff = None

    def _parse_both(self, path, kw):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc))
        return inc

    def _write(self, path, records):
        with open(path, "a") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def test_garbled_timestamp_never_takes_the_session_down(self):
        # the dispatch's two-record repro: second record's stamp is garbage — parse_session used
        # to TypeError in segment_turns' sort and the whole session view went down with it
        bad = G.aline(T0 + 10, "the reply the user must still see", "a1", "u1")
        bad["timestamp"] = "not-a-timestamp"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("a1", [a.get("uuid") for a in atoms],
                          "the garbled-stamp record was dropped — it must stay visible")
            a1 = next(a for a in atoms if a.get("uuid") == "a1")
            self.assertEqual(a1["t"], T0, "the borrow is the last good stamp in file order")

    def test_garbled_first_record_still_shows(self):
        bad = G.uline(T0, "opening ask", "u1")
        bad["timestamp"] = "garbage"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [bad, G.aline(T0 + 10, "answer", "a1", "u1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("u1", [a.get("uuid") for a in atoms])
            u1 = next(a for a in atoms if a.get("uuid") == "u1")
            self.assertEqual(u1["t"], 0, "no good stamp yet -> epoch zero, deterministically")

    def test_garbled_assistant_append_demotes_and_survives(self):
        # a garbled user/assistant stamp in the DELTA trips the existing ts gate (fold refuses,
        # full parse serves) — and the full parse must now survive it
        bad = G.aline(T0 + 20, "late reply", "a2", "a1")
        bad["timestamp"] = "###"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")])
            self._parse_both(path, kw)
            g0 = emi._ASM_STATS.get("g:ts", 0)
            self._write(path, [bad])
            ses = self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS.get("g:ts", 0), g0 + 1,
                             "a garbled delta stamp folds only the full emit can place — demote")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertIn("a2", [a.get("uuid") for a in atoms])

    def test_garbled_system_record_folds_equivalently(self):
        # a system record (no ts gate) with a garbled stamp FOLDS — the ingest repair must give
        # fold and full parse the same borrowed time, or they diverge
        refusal = {"type": "system", "subtype": "model_refusal_fallback", "uuid": "s1",
                   "parentUuid": "a1", "timestamp": "junk", "content": "swapped models",
                   "originalModel": "m-one", "fallbackModel": "m-two"}
        tail = G.aline(T0 + 30, "from the fallback model", "a2", "s1")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")])
            self._parse_both(path, kw)
            f0 = emi._ASM_STATS["fold"]
            self._write(path, [refusal, tail])
            ses = self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["fold"], f0 + 1,
                             "the garbled system record must fold, not silently demote")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            s1 = next(a for a in atoms if a.get("uuid") == "s1")
            self.assertEqual(s1["t"], T0 + 10, "borrowed from its file neighbor, both paths")

    def test_garbled_tail_never_zeroes_the_watermark(self):
        # review-reproduced divergence (T210): the prepass watermark read the RAW stamp of the
        # chronologically-last record — a garbled tail (which borrows the max stamp and sorts
        # last) zeroed max_ppt, disarmed the g:ts gate, and a timestamp-regressed append FOLDED
        # into carried state a fresh full parse disagrees with. The watermark must read ts_of.
        bad = G.aline(T0 + 20, "tail with a rotten stamp", "aX", "a1")
        bad["timestamp"] = "rotten"
        regressed = G.uline(T0 + 5, "arrives stamped BEFORE the fold watermark", "u2", "aX")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "hi", "u1"), G.aline(T0 + 10, "ok", "a1", "u1"), bad])
            self._parse_both(path, kw)
            g0 = emi._ASM_STATS.get("g:ts", 0)
            self._write(path, [regressed])
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS.get("g:ts", 0), g0 + 1,
                             "a regressed append after a garbled tail must DEMOTE — folding it "
                             "diverges (the watermark was zeroed by the raw-stamp read)")

    def test_garbled_spliced_prompt_still_shows(self):
        # a queued_command attachment is the WITNESS for a prompt the user actually typed
        # mid-turn: dropping it on a garbled stamp is the exact silent loss this repo calls
        # fatal — it must ride the repaired stamp like every other record (T210 review)
        att = G.attline(T0 + 15, "the mid-turn ask that must not vanish", "q1", "a1")
        att["timestamp"] = "mangled"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._write(path, [G.uline(T0, "start", "u1"),
                               G.aline(T0 + 10, "working", "a1", "u1", tools=("Bash",), stop="tool_use"),
                               att,
                               G.trline(T0 + 20, "tu_a1_0", "u2", "q1"),
                               G.aline(T0 + 30, "done", "a2", "u2")])
            ses = self._parse_both(path, kw)
            texts = [(_text_of(a["message"]["content"]) if a.get("message") else "")
                     for t in ses["turns"] for a in t["atoms"]]
            self.assertTrue(any("must not vanish" in x for x in texts),
                            "the spliced prompt was dropped on its garbled stamp")
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            spliced = next(a for a in atoms if a.get("absorbed"))
            self.assertEqual(spliced["t"], T0 + 10, "borrowed from its file neighbor")

    def test_garbled_cut_record_still_trims_orphans(self):
        # the pending-cut orphan filter read the cut record's RAW stamp: garbled -> None ->
        # filter skipped -> the deleted prompt's salvaged reply stood alone in the chat (the
        # half-worked-delete shape). The filter must read the repaired stamp (T210 review).
        cut = G.aline(T0 + 10, "the reply being rolled back", "a1", "u1")
        cut["timestamp"] = "shredded"
        rows = [{"t": T0 + 30, "orphanReply": {"uuid": "zz9", "text": "landed after the cut",
                                               "ts": iso(T0 + 30)}}]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), cut,
                               G.uline(T0 + 20, "next", "u2", "a1")])
            kw = {"candidate_files": [str(path)], "states": rows, "postal_log": [], "now": NOW,
                  "leaf_override": "a1"}
            ses = self._parse_both(path, kw)
            atoms = [a for t in ses["turns"] for a in t["atoms"]]
            self.assertNotIn("zz9", [a.get("uuid") for a in atoms],
                             "an orphan landing AFTER the cut must be trimmed — the garbled cut "
                             "stamp disarmed the filter")

    def test_ts_repair_counts_distinct_records_not_parses(self):
        # a stable corrupt line must not inflate the counter on every full parse — it counts
        # CORRUPTION, not parse volume (T210 review)
        bad = G.aline(T0 + 10, "reply", "a1", "u1")
        bad["timestamp"] = "xxx"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emr._ASM_CACHE.clear()
            emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            c1 = emr._ASM_STATS.get("ts-repair", 0)
            emr._ASM_CACHE.clear()
            emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emr._ASM_STATS.get("ts-repair", 0), c1,
                             "the second full parse re-ingested the same corrupt line and "
                             "counted it again")

    def test_note_cap_rearms_instead_of_going_silent(self):
        import io, contextlib
        bad = G.uline(T0, "ask", "u1")
        bad["timestamp"] = "bad"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [bad, G.aline(T0 + 10, "answer", "a1", "u1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emr._TS_REPAIR_NOTED.clear()
            emr._TS_REPAIR_NOTED.update("stem%d" % i for i in range(64))   # cap reached
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertIn("timestamp", err.getvalue(),
                          "a full cap silenced every NEW file forever — it must re-arm")
            emr._TS_REPAIR_NOTED.clear()

    def test_ts_repair_is_counted_and_noted_loudly(self):
        import io, contextlib
        bad = G.aline(T0 + 10, "reply", "a1", "u1")
        bad["timestamp"] = "zzz"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("")
            self._write(path, [G.uline(T0, "hello", "u1"), bad, G.uline(T0 + 20, "more", "u2", "a1")])
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            c0 = emr._ASM_STATS.get("ts-repair", 0)
            emr._TS_REPAIR_NOTED.clear()
            emr._TS_REPAIRED_SEEN.clear()   # distinct-record counting — this file's uuids again
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertGreater(emr._ASM_STATS.get("ts-repair", 0), c0, "repairs ride the stats")
            self.assertIn("timestamp", err.getvalue(), "the repair is loud, never silent")
            err2 = io.StringIO()
            with contextlib.redirect_stderr(err2):
                emr._ASM_CACHE.clear()
                emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(err2.getvalue(), "", "the note fires once per file, not per parse")


class GateDemotions(unittest.TestCase):
    """Each fast-path gate, tripped on purpose: equivalence must hold BY DEMOTION."""
    maxDiff = None

    def _parse_both(self, path, kw):
        inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        emr._ASM_CACHE.clear()
        ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
        self.assertEqual(_tree(ref), _tree(inc))
        return inc

    def _stream_then(self, base_records, appended, expect_full=True):
        """Stream a clean base (folding), then append `appended` in ONE step and assert that
        step full-parsed (a gate demotion) — or folded, for the near-miss twin. Returns the
        road counters that step moved."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            path.write_text("")
            for r in base_records:
                with open(path, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                self._parse_both(path, kw)
            fulls0, folds0 = emi._ASM_STATS["full"], emi._ASM_STATS["fold"]
            before = dict(emi._ASM_STATS)
            with open(path, "a") as fh:
                for r in appended:
                    fh.write(json.dumps(r) + "\n")
            self._parse_both(path, kw)
            moved = {k: emi._ASM_STATS.get(k, 0) - before.get(k, 0) for k in set(before) | set(emi._ASM_STATS)
                     if emi._ASM_STATS.get(k, 0) != before.get(k, 0)}
            if expect_full:
                self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1,
                                 "the gate should have demoted this append to a full parse: %s" % moved)
            else:
                self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1,
                                 "this append is the fast path's own case and must fold: %s" % moved)
            return moved

    @staticmethod
    def _promptid_arms(moved):
        """The promptid gate's counters one step moved: every demotion, the two arms, the seed's part, the deltas the narrowed arm
        let through that demoted after all, and the folds it saved (fold:attachedPass, counted once the fold serves)."""
        arms = {k: moved.get("g:promptid" + k, 0) for k in ("", ":boundary", ":wrapper", ":boundarySeed", ":passedDemote")}
        arms["fold:attachedPass"] = moved.get("fold:attachedPass", 0)
        return arms

    def _base(self):
        return [G.uline(T0, "first ask", "u1"),
                G.aline(T0 + 10, "first reply", "a1", "u1")]

    def test_clean_append_folds(self):
        self._stream_then(self._base(),
                          [G.uline(T0 + 60, "second ask", "u2", "a1"),
                           G.aline(T0 + 70, "second reply", "a2", "u2")], expect_full=False)

    def test_rewritten_uuid_demotes(self):
        # a verbatim re-write (same uuid) rebinds last-write-wins index state — never folded
        self._stream_then(self._base(), [G.aline(T0 + 10, "first reply", "a1", "u1")])

    def test_resurrected_dangling_target_demotes(self):
        # u2's parent x9 exists nowhere (broken chain, kept); a record LANDING as x9 later must
        # full-parse — a fold would keep the broken/repaired shape while a fresh build rebinds
        base = self._base() + [G.uline(T0 + 30, "on a broken chain", "u2", "x9")]
        self._stream_then(base, [G.aline(T0 + 40, "the missing link lands", "x9", "a1")])

    def test_compact_boundary_demotes(self):
        self._stream_then(self._base(),
                          [G.compact_line(T0 + 100, "cb1", logical_parent="a1")])

    def test_compact_summary_demotes(self):
        self._stream_then(self._base(),
                          [G.compact_summary_line(T0 + 100, "cs1", parent="a1")])

    def test_repeated_promptid_across_appends_demotes(self):
        w1 = G.uline(T0 + 60, "<command-name>/usage</command-name>\n"
                              "<command-message>usage</command-message>\n"
                              "<command-args></command-args>", "w1", "a1")
        w1["promptId"] = "p77"
        so = G.uline(T0 + 61, "<local-command-stdout>tokens: plenty</local-command-stdout>",
                     "so1", "w1")
        so["promptId"] = "p77"
        moved = self._stream_then(self._base() + [w1], [so])
        self.assertEqual(self._promptid_arms(moved),
                         {"": 1, ":boundary": 0, ":wrapper": 1, ":boundarySeed": 0, ":passedDemote": 0, "fold:attachedPass": 0},
                         "the wrapper arm demoted, and its counter says so: %s" % moved)

    def test_repeated_promptid_on_plain_records_folds(self):
        # the ROUTINE shape: every record of a turn wears its prompt's id, so tool results
        # repeat it on nearly every real append — those must fold (unshaped, this gate
        # demoted 1863/1869 bursts on a live replay); only wrapper-family shapes and
        # adoptable-boundary episode pids can re-classify old atoms
        opener = G.uline(T0 + 60, "run the checks", "u2", "a1")
        opener["promptId"] = "p42"
        work = G.aline(T0 + 70, "running", "a2", "u2", tools=("Bash",), stop="tool_use")
        tr = G.trline(T0 + 80, "tu_a2_0", "u3", "a2")
        tr["promptId"] = "p42"
        done = G.aline(T0 + 90, "all green", "a3", "u3")
        moved = self._stream_then(self._base() + [opener, work], [tr, done], expect_full=False)
        self.assertEqual(self._promptid_arms(moved)["fold:attachedPass"], 0,
                         "a routine repeated prompt id is not the narrowed arm's pass: %s" % moved)

    def test_tool_results_after_an_auto_compaction_in_the_same_prompt_fold(self):
        """An automatic compaction in the middle of a long turn (2026-10-03): the CLI writes the boundary and its summary
        ATTACHED (the conversation chains through them); here the summary wears the prompt id of the turn it compacted in (the
        shape the narrowed gate assumes the CLI can write), and every later tool result of the turn wears the same id. The
        boundary arm demoted each such append to a whole parse until the next prompt. Adoption skips an attached pair, so
        nothing such a record does can re-seat it: the append folds, and the tree equals the whole parse's at every step. The
        append holds two such tool results, and the fold it saved counts once (fold:attachedPass)."""
        opener = dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9")
        work = G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use")
        res1 = dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2", content="migration applied"), promptId="p9")
        cb = G.compact_line(T0 + 90, "cb1", logical_parent="r2", trigger="auto")
        cs = dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1"), promptId="p9")
        more = G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")
        res2 = dict(G.trline(T0 + 110, "tu_a3_0", "r3", "a3", content="index rebuilt: 3 shards"), promptId="p9")
        check = G.aline(T0 + 115, "querying the rebuilt index", "a3q", "r3", tools=("Bash",), stop="tool_use")
        res3 = dict(G.trline(T0 + 118, "tu_a3q_0", "r3q", "a3q", content="3 hits"), promptId="p9")
        done = G.aline(T0 + 120, "the index is migrated", "a4", "r3q")
        moved = self._stream_then(self._base() + [opener, work, res1, cb, cs, more], [res2, check, res3, done], expect_full=False)
        self.assertEqual(self._promptid_arms(moved),
                         {"": 0, ":boundary": 0, ":wrapper": 0, ":boundarySeed": 0, ":passedDemote": 0, "fold:attachedPass": 1},
                         "the narrowed arm let the attached pair's prompt id through, and says so: %s" % moved)

    def test_a_passed_record_the_stamp_gate_then_demotes_counts_no_saved_fold(self):
        """The pass counter counts folds the narrowed arm saved, so it moves only once the fold serves (review of 2026-10-03,
        round one). A live manual /compact parsed while its boundary and summary are the file's last two records reads as
        attached, so the arm lets the episode's next record through, the raw twin stamped at the command's issue time; the
        stamp gate then demotes it to a whole parse, as before the narrowing, and no pass is counted."""
        side, _so = G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a1")   # boundary, summary (pc), raw twin, wrapper, stdout
        moved = self._stream_then(self._base() + side[:2], side[2:3])             # the pair is the leaf; then the raw twin
        self.assertEqual((moved.get("g:ts", 0), moved.get("fold:attachedPass", 0)), (1, 0),
                         "the stamp gate demotes the passed record, and no saved fold is counted: %s" % moved)
        self.assertEqual({k: n for k, n in moved.items() if k.startswith("g:promptid")}, {},
                         "the promptid gate files nothing for a record it let through: %s" % moved)

    def test_a_record_wearing_a_detached_manual_compacts_pid_still_demotes(self):
        """The boundary arm's own case, kept: a LIVE manual /compact writes its boundary and summary as a DETACHED pair, the
        summary wearing the /compact's prompt id, and until the stdout lands adoption splices the pair after the episode's last
        landed record (here the wrapper). A later record of the same episode moves that splice, and with it the boundary's
        parent and time, without changing which records are kept, so the fold's kept check cannot see it: the arm demotes, and
        the tree equals the whole parse's."""
        side, _so = G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a1")   # boundary, summary (pc), raw twin, wrapper, stdout
        late = dict(G.uline(T0 + 125, "compacting the notes-api conversation", "pl", "cwc", ps=None), promptId="pc", isMeta=True)
        moved = self._stream_then(self._base() + side[:4], [late])                # the stdout has not landed: the wrapper is the leaf
        self.assertEqual(self._promptid_arms(moved),
                         {"": 1, ":boundary": 1, ":wrapper": 0, ":boundarySeed": 0, ":passedDemote": 0, "fold:attachedPass": 0},
                         "the boundary arm demoted, and its counter says so: %s" % moved)
        self.assertEqual(self._boundary_reasons(moved), {"adopted": 1},
                         "the demotion is counted under the reason the entry could not vouch for the id: %s" % moved)

    # The episode rule (2026-10-05). The shapes below hold a compaction pair that is not attached, with a summary wearing the
    # turn's prompt id p9, and no record wearing p9 names /compact. Adoption splices a detached pair only behind a /compact
    # episode, so no record wearing p9 can move one: the boundary arm demoted each of these appends, and they fold now, the tree
    # equal to the whole parse's at every step.

    @staticmethod
    def _boundary_reasons(moved):
        """The boundary arm's demotions one step moved, by reason (g:promptid:boundary:<reason>)."""
        pre = "g:promptid:boundary:"
        return {k[len(pre):]: n for k, n in moved.items() if k.startswith(pre)}

    @staticmethod
    def _pass_shapes(moved):
        """The folds the episode rule saved one step moved, by the pair's shape (fold:attachedPass:<shape>)."""
        pre = "fold:attachedPass:"
        return {k[len(pre):]: n for k, n in moved.items() if k.startswith(pre)}

    def _compacted_turn(self):
        """A turn compacted automatically in its middle: the opener u2 and its first call, the boundary anchored on the first
        result, the summary wearing the turn's prompt id p9, and the next call and its result chained on the summary."""
        return self._base() + [
            dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9"),
            G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
            dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2", content="migration applied"), promptId="p9"),
            G.compact_line(T0 + 90, "cb1", logical_parent="r2", trigger="auto"),
            dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1"), promptId="p9"),
            G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use"),
            dict(G.trline(T0 + 110, "tu_a3_0", "r3", "a3", content="index rebuilt: 3 shards"), promptId="p9")]

    @staticmethod
    def _spur():
        """The eclipse spur (the golden eclipsed-branch scenario, T209): a rate-limit storm's buffered api_error records,
        stamped when they happened and flushed at the next enqueue, chained off the turn's opener u2; here the enqueue is a
        message queued in the middle of the turn, and the turn's next call hangs on it. The compaction pair is off the path."""
        err = {"message": "429 rate_limit_error (synthetic)"}
        return [G.sysline(T0 + 61, "api_error", "e1", "u2", level="error", error=err),
                G.sysline(T0 + 75, "api_error", "e2", "e1", level="error", error=err),
                G.attline(T0 + 115, "and log the shard counts too", "q1", "e2"),
                G.aline(T0 + 120, "querying the rebuilt index", "a4", "q1", tools=("Bash",), stop="tool_use")]

    @staticmethod
    def _golden_eclipse():
        """The golden eclipse exactly, off the compacted turn's opener u2: the storm's two api_error records, the stop hook and
        the next typed prompt hung on them, and its reply. Written after the compaction, it keeps the branch under the opener,
        the pair included, so the pair is off the path but still kept."""
        err = {"message": "429 rate_limit_error (synthetic)"}
        return [G.sysline(T0 + 61, "api_error", "e1", "u2", level="error", error=err),
                G.sysline(T0 + 75, "api_error", "e2", "e1", level="error", error=err),
                G.sysline(T0 + 92, "stop_hook_summary", "sh1", "e2", hookCount=1),
                G.uline(T0 + 120, "thanks - now page the search results", "u5", "sh1"),
                G.aline(T0 + 130, "paging them", "a5", "u5")]

    def _stream_steps(self, base_records, steps):
        """Stream a clean base record by record, then append each of `steps` in one step, the trees compared with the whole
        parse's at every parse; returns the road counters each step moved."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            path.write_text("")
            for r in base_records:
                with open(path, "a") as fh:
                    fh.write(json.dumps(r) + "\n")
                self._parse_both(path, kw)
            out = []
            for step in steps:
                before = dict(emi._ASM_STATS)
                with open(path, "a") as fh:
                    for r in step:
                        fh.write(json.dumps(r) + "\n")
                self._parse_both(path, kw)
                out.append({k: emi._ASM_STATS.get(k, 0) - before.get(k, 0) for k in set(before) | set(emi._ASM_STATS)
                            if emi._ASM_STATS.get(k, 0) != before.get(k, 0)})
            return out

    def test_tool_results_on_a_spur_off_an_auto_compactions_pair_fold(self):
        """Off the path (`offpath`): after the spur, every later tool result of the turn wears p9 and the pair is no ancestor of
        the leaf. The append folds, and the fold names the shape it saved."""
        res = dict(G.trline(T0 + 130, "tu_a4_0", "r4", "a4", content="3 hits"), promptId="p9")
        done = G.aline(T0 + 140, "the index is migrated", "a5", "r4")
        moved = self._stream_then(self._compacted_turn() + self._spur(), [res, done], expect_full=False)
        self.assertEqual(self._promptid_arms(moved),
                         {"": 0, ":boundary": 0, ":wrapper": 0, ":boundarySeed": 0, ":passedDemote": 0, "fold:attachedPass": 1},
                         "the episode rule let the record through: %s" % moved)
        self.assertEqual(self._pass_shapes(moved), {"offpath": 1}, "the saved fold names the pair's shape: %s" % moved)

    def test_a_record_wearing_a_summary_pid_hung_off_a_tool_result_folds(self):
        """A summary hung off a record that is no compact_boundary (`parent`), here the turn's first result: it is no
        boundary's summary, so adoption never reads its id. A later result wearing p9 folds."""
        recs = self._base() + [
            dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9"),
            G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
            dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2"), promptId="p9"),
            dict(G.compact_summary_line(T0 + 91, "cs1", parent="r2"), promptId="p9"),
            G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")]
        res = dict(G.trline(T0 + 110, "tu_a3_0", "r3", "a3", content="index rebuilt"), promptId="p9")
        moved = self._stream_then(recs, [res, G.aline(T0 + 120, "the index is migrated", "a4", "r3")], expect_full=False)
        self.assertEqual((self._promptid_arms(moved)[""], self._pass_shapes(moved)), (0, {"parent": 1}),
                         "the episode rule let the record through: %s" % moved)

    def test_a_record_wearing_a_replaced_summarys_pid_folds(self):
        """A summary a later record replaced by reusing its uuid (`replaced`; last write wins): the boundary's child is a plain
        note now, and nothing names a /compact. A later result wearing p9 folds."""
        recs = self._compacted_turn()[:-2] + [
            G.uline(T0 + 95, "a note written again under the summary's uuid", "cs1", "cb1", ps=None),
            G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")]
        res = dict(G.trline(T0 + 110, "tu_a3_0", "r3", "a3", content="index rebuilt"), promptId="p9")
        moved = self._stream_then(recs, [res, G.aline(T0 + 120, "the index is migrated", "a4", "r3")], expect_full=False)
        self.assertEqual((self._promptid_arms(moved)[""], self._pass_shapes(moved)), (0, {"replaced": 1}),
                         "the episode rule let the record through: %s" % moved)

    def test_a_record_that_names_compact_itself_on_an_off_path_pair_demotes(self):
        """The episode rule's per-record half: a record wearing p9 whose text names /compact (here quoted mid-text, so the wrapper
        arm, which reads a leading tag, passes it) would make p9's episode a /compact one, behind which adoption can splice the
        pair. It demotes under the boundary arm, and the tree equals the whole parse's."""
        quoted = dict(G.uline(T0 + 130, "the notes say <command-name>/compact</command-name> ran here", "u5", "a4", ps=None),
                      promptId="p9", isMeta=True)
        moved = self._stream_then(self._compacted_turn() + self._spur(), [quoted])
        self.assertEqual((self._promptid_arms(moved)[":boundary"], self._boundary_reasons(moved)), (1, {"offpath": 1}),
                         "the record that names /compact is refused: %s" % moved)

    def test_an_off_path_pair_whose_id_a_compact_episode_wears_still_demotes(self):
        """The episode rule's graph half: a live manual /compact's pair, adopted once its stdout landed, then rewound away (the
        next prompt re-parents at the pre-compaction leaf, and the episode leaves the path with the pair). Its episode names
        /compact, so a record wearing its id still demotes, counted `offpath`."""
        side, _so = G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a1")
        rewound = G.uline(T0 + 130, "never mind, back to the search page size", "u3", "a1")
        late = dict(G.uline(T0 + 140, "compacting the notes-api conversation", "pl", "u3", ps=None), promptId="pc", isMeta=True)
        moved = self._stream_then(self._base() + side + [rewound], [late])
        self.assertEqual((self._promptid_arms(moved)[":boundary"], self._boundary_reasons(moved)), (1, {"offpath": 1}),
                         "a /compact episode wears the id: the arm demotes: %s" % moved)

    def test_a_record_that_names_compact_beside_an_eclipsed_pair_demotes(self):
        """Why the per-record half is needed: the pair is off the path but still KEPT. The spur here is the golden eclipse
        exactly (the stop hook and the next typed prompt hang on it), written right after the compaction, so the eclipse keeps
        the branch under the opener, the pair included. A record wearing p9 that names /compact, then a reply under it, makes
        p9's episode a /compact one ending on the path: adoption then splices the pair behind that record, which moves the
        boundary's parent and time while every record stays kept, so the fold's kept check cannot see it. The arm demotes, and
        the tree equals the whole parse's at every step."""
        recs = self._compacted_turn()[:-2] + self._golden_eclipse()
        quoted = dict(G.uline(T0 + 140, "the notes say <command-name>/compact</command-name> ran here", "u6", "a5", ps=None),
                      promptId="p9", isMeta=True)
        moved = self._stream_then(recs, [quoted, G.aline(T0 + 150, "noted", "a6", "u6")])
        self.assertEqual((self._promptid_arms(moved)[":boundary"], self._boundary_reasons(moved), moved.get("g:kept", 0)),
                         (1, {"offpath": 1}, 0), "the record that names /compact is refused at the arm: %s" % moved)

    def test_a_record_on_hand_that_names_compact_keeps_the_arm_refusing_beside_an_eclipsed_pair(self):
        """The episode rule's scan of the records on hand, where only the served tree tells (review of 2026-10-05, round one).
        x9, a meta record wearing p9 that quotes the /compact tag, hangs off the turn's first result before the golden eclipse,
        which leaves it off the path and keeps it, with the pair. p9's episode is a /compact one ending on x9, off the path, so
        adoption splices nothing. The append lands l9, a meta record wearing p9 that names nothing, on the path, and a reply
        under it: the episode now ends on l9, and the cold parse splices the pair behind it while every record stays kept,
        which the fold's kept check cannot see. Only the scan finds x9, so the arm refuses the append (`offpath`) and it parses
        whole. A scan that skipped meta records, or read none, folded it and served the card as a turn of its own ahead of u5,
        where the cold parse puts it after a5, opening the turn a6 joins. The tree equals the whole parse's at every step."""
        x9 = dict(G.uline(T0 + 93, "the notes say <command-name>/compact</command-name> ran here", "x9", "r2", ps=None),
                  promptId="p9", isMeta=True)
        l9 = dict(G.uline(T0 + 140, "a note the harness kept for the turn", "l9", "a5", ps=None), promptId="p9", isMeta=True)
        moved = self._stream_then(self._compacted_turn()[:-2] + [x9] + self._golden_eclipse(),
                                  [l9, G.aline(T0 + 150, "noted", "a6", "l9")])
        self.assertEqual((self._promptid_arms(moved)[":boundary"], self._boundary_reasons(moved), moved.get("g:kept", 0)),
                         (1, {"offpath": 1}, 0), "the record on hand that names /compact keeps the arm refusing: %s" % moved)

    def test_a_record_outside_every_episode_that_names_compact_leaves_the_episode_rule_vouching(self):
        """Which records make up a /compact episode is one filter, adoption's and the gate's (_compact_episode_pid, review of
        2026-10-05, round two): a user record wearing the id, a compaction summary aside. Each case puts p9 on a record the
        filter leaves out, its text opening on the /compact tag: the summary cs1, or the turn's assistant call a2, which is
        no user record. The golden eclipse keeps the pair off the path; the append lands l9, a meta record wearing p9 that
        names nothing, and a reply under it. No record in p9's episode names /compact, so adoption splices nothing, the
        episode rule vouches (`offpath`), and the append folds, the tree equal to the whole parse's at every step. A filter
        widened for adoption alone, to summaries or to records of any type, makes the episode a /compact one ending on l9: the
        cold parse splices the pair behind l9 while the gate still vouches, and the fold serves a tree without that turn."""
        turn = self._compacted_turn()[:-2]
        self.assertEqual([r["uuid"] for r in turn], ["u1", "a1", "u2", "a2", "r2", "cb1", "cs1"], "the premise: the turn's records")
        cs1 = dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1",
                                          text="<command-name>/compact</command-name>\nsummary of the conversation so far"),
                   promptId="p9")
        a2 = dict(G.aline(T0 + 70, "<command-name>/compact</command-name>\nrunning the migration", "a2", "u2", tools=("Bash",),
                          stop="tool_use"), promptId="p9")
        l9 = dict(G.uline(T0 + 140, "a note the harness kept for the turn", "l9", "a5", ps=None), promptId="p9", isMeta=True)
        for case, recs in (("summary", turn[:6] + [cs1]), ("assistant", turn[:3] + [a2] + turn[4:])):
            with self.subTest(case=case):
                moved = self._stream_then(recs + self._golden_eclipse(), [l9, G.aline(T0 + 150, "noted", "a6", "l9")],
                                          expect_full=False)
                self.assertEqual((self._promptid_arms(moved)[":boundary"], self._pass_shapes(moved)), (0, {"offpath": 1}),
                                 "the episode rule vouched for the append: %s" % moved)

    def test_a_record_whose_summary_flag_is_truthy_but_not_true_leaves_the_episode_rule_vouching(self):
        """The per-record half reads a delta record's summary flag as the shared filter does, by truth (_compact_episode_pid,
        review of 2026-10-05, round two). The record is the eclipse case's u6 above, a meta record wearing p9 that quotes the
        /compact tag mid-text, with isCompactSummary set to 1, truthy but not True, and a reply under it. Adoption leaves a user
        record whose flag is truthy out of every episode, so p9's episode is still not a /compact one and the cold parse
        splices nothing: the episode rule vouches (`offpath`), the append folds, and the tree equals the whole parse's at every
        step. A per-record half that read the flag by "is True", as it did before the shared filter, refused the record at the
        arm and parsed the append whole, though adoption never puts that record in an episode."""
        recs = self._compacted_turn()[:-2] + self._golden_eclipse()
        flagged = dict(G.uline(T0 + 140, "the notes say <command-name>/compact</command-name> ran here", "u6", "a5", ps=None),
                       promptId="p9", isMeta=True, isCompactSummary=1)
        moved = self._stream_then(recs, [flagged, G.aline(T0 + 150, "noted", "a6", "u6")], expect_full=False)
        self.assertEqual((self._promptid_arms(moved), self._pass_shapes(moved), moved.get("full", 0)),
                         ({"": 0, ":boundary": 0, ":wrapper": 0, ":boundarySeed": 0, ":passedDemote": 0, "fold:attachedPass": 1},
                          {"offpath": 1}, 0), "the episode rule vouched for the record flagged 1: %s" % moved)

    def test_a_fold_the_attached_rule_saves_after_one_the_episode_rule_saved_names_no_shape(self):
        """The shape tags speak for their own delta (review of 2026-10-05, round one): the gate sets them for every delta that
        reaches the descent, so a fold the attached rule saves after one the episode rule saved counts no shape. The turn's
        result past the spur folds on the episode rule (`offpath`); the next turn compacts automatically, attached, its
        summary wearing that turn's id p10, and its next result, wearing p10, folds on the attached rule: one saved fold, no
        shape."""
        nxt = [dict(G.uline(T0 + 200, "now warm the notes-api ranking cache", "u6", "a5"), promptId="p10"),
               G.aline(T0 + 210, "warming the cache", "a6", "u6", tools=("Bash",), stop="tool_use"),
               dict(G.trline(T0 + 220, "tu_a6_0", "r6", "a6", content="cache warmed"), promptId="p10"),
               G.compact_line(T0 + 230, "cb2", logical_parent="r6", trigger="auto"),
               dict(G.compact_summary_line(T0 + 231, "cs2", parent="cb2"), promptId="p10"),
               G.aline(T0 + 240, "checking the warmed cache", "a7", "cs2", tools=("Bash",), stop="tool_use")]
        moved = self._stream_steps(self._compacted_turn() + self._spur(), [
            [dict(G.trline(T0 + 130, "tu_a4_0", "r4", "a4", content="3 hits"), promptId="p9"),
             G.aline(T0 + 140, "the index is migrated", "a5", "r4")]] + [[r] for r in nxt] + [
            [dict(G.trline(T0 + 250, "tu_a7_0", "r7", "a7", content="cache hit rate 0.97"), promptId="p10"),
             G.aline(T0 + 260, "the cache is warm", "a8", "r7")]])
        self.assertEqual((moved[0].get("fold", 0), self._pass_shapes(moved[0])), (1, {"offpath": 1}),
                         "the premise: the episode rule saved the first fold: %s" % moved[0])
        self.assertEqual((moved[-1].get("fold", 0), moved[-1].get("fold:attachedPass", 0), self._pass_shapes(moved[-1])), (1, 1, {}),
                         "the attached rule's fold names no shape: %s" % moved[-1])

    def test_skill_link_to_new_invocation_demotes(self):
        # the payload record references tu_skill_9 BEFORE any such invocation exists; when the
        # invocation appends, a full parse reclassifies the OLD record — the gate must fire
        payload = G.uline(T0 + 30, "skill instructions payload", "u2", "a1")
        payload["sourceToolUseID"] = "tu_skill_9"
        payload["isMeta"] = True
        inv = G.aline(T0 + 40, "invoking", "a2", "u2", tools=("Skill",))
        inv["message"]["content"][-1]["id"] = "tu_skill_9"
        self._stream_then(self._base() + [payload], [inv])

    def test_timestamp_regression_demotes(self):
        self._stream_then(self._base(),
                          [G.uline(T0 - 50, "typed earlier, landed later", "u2", "a1")])

    def test_unparseable_timestamp_demotes(self):
        # isMeta so the record emits no atom either way: a kept EMITTING record with a garbage
        # timestamp crashes segment_turns on main too (pre-existing, outside this diff) — the
        # gate's job is only that a ts-less record never poisons the chronological carry
        bad = G.uline(T0 + 60, "harness caveat echo", "u2", "a1")
        bad["timestamp"] = "not-a-time"
        bad["isMeta"] = True
        self._stream_then(self._base(), [bad])

    def test_interior_reparent_demotes(self):
        # the new leaf chains to u1 (an interior record), not the old leaf a1 — a rewind shape
        self._stream_then(self._base(), [G.uline(T0 + 60, "edited resend", "u2", "u1")])

    def test_api_error_spur_reparent_demotes(self):
        # the T209 geometry mid-stream: the CLI flushes buffered api_error records parented at
        # the turn's OPENER, and the next prompt chains onto that spur — leaf descent fails
        spur = [{"type": "system", "subtype": "api_error", "timestamp": iso(T0 + 60),
                 "uuid": "e1", "parentUuid": "u1"},
                G.uline(T0 + 70, "next ask", "u2", "e1")]
        self._stream_then(self._base() + [G.aline(T0 + 20, "real reply", "a2", "a1")], spur)

    def test_pending_cut_bypasses_the_cache(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = self._base() + [G.uline(T0 + 60, "second ask", "u2", "a1"),
                                   G.aline(T0 + 70, "second reply", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [],
                  "now": NOW, "leaf_override": "a1"}
            byp0 = emi._ASM_STATS["bypass"]
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["bypass"], byp0 + 1)

    def test_resume_link_change_demotes(self):
        # same transcript, but a resumeFork row lands between parses -> the links gate refuses
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in self._base()) + "\n")
            kw = {"candidate_files": [str(path)], "states": [], "postal_log": [], "now": NOW}
            self._parse_both(path, kw)
            fulls0 = emi._ASM_STATS["full"]
            kw["states"] = [{"t": T0 + 90, "resumeFork":
                             {"to": "cccccccc-0000-0000-0000-000000000000", "from": SID}}]
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)

    def test_truncated_rewrite_demotes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = self._base() + [G.uline(T0 + 60, "second ask", "u2", "a1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            self._parse_both(path, kw)
            fulls0 = emi._ASM_STATS["full"]
            path.write_text("\n".join(json.dumps(r) for r in recs[:2]) + "\n")   # shrink = rewrite
            self._parse_both(path, kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)


class PromptIdAdoptable(unittest.TestCase):
    """The narrowed boundary arm's rules (_asm_pid_refusal, 2026-10-03 and 2026-10-05), asked of real adapters: a record wearing a
    compaction summary's prompt id may fold when every summary wearing it hangs off a boundary on the active path that adoption
    did not splice (the attached rule), or, for a pair that is not attached (off the path, its summary hung off no boundary, or
    replaced by a later copy of its uuid), when no record wearing the id names /compact (the episode rule: adoption splices a
    detached pair only behind a /compact episode). Each other shape demotes, named by its reason: an adopted pair (`adopted`, a
    live manual /compact, whose splice a later episode record moves), a pid with no summary on record (`nosummary`), a pid a
    restored adapter's seed names (`seed`: its document's gates name the summaries before its cut, whose pairs the restored
    adapter never read), a pair that is not attached when a record on hand wearing its id names /compact (under the pair's
    shape), and one whose id a restored adapter's seed lists (`seedEpisode`: its document's compact_pids, or the prompt ids of
    a document written before that field)."""

    # Texts the gate reads as naming /compact, and texts it does not (_asm_names_compact): the tag anywhere in a user record's
    # text blocks, a bare name read with its slash, a later tag read too; a tool result's output is no text block, another
    # command's name is no /compact, and a command's message tag alone names nothing.
    NAMES_COMPACT = ["<command-name>/compact</command-name>",
                     "<command-message>compact</command-message>\n<command-name>compact</command-name>",
                     "quoted mid-text <command-name> /compact </command-name>",
                     "<command-name>/usage</command-name> <command-name>/compact</command-name>"]
    NAMES_NO_COMPACT = ["/compact", "<command-name>/compactor</command-name>", "<command-name>/usage</command-name>",
                        "<command-message>compact</command-message>",
                        [{"type": "tool_result", "tool_use_id": "t1", "content": "<command-name>/compact</command-name>"}]]

    def _adapter(self, records):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("".join(json.dumps(r) + "\n" for r in records))
            return emi.FileAdapter([str(path)], str(path))

    def _refusal(self, ad, pid):
        """_asm_pid_refusal's (why, how), checked against the reasons the gate counts (_ASM_PID_REFUSALS, review of 2026-10-05,
        round one): a refusal's reason is listed, and so is the shape the episode rule vouched for, which the gate counts as the
        reason when the record itself names /compact. Every shape below asks through here, so a reason one of them returns
        that the list lacks, renamed or new, fails."""
        why, how = emi._asm_pid_refusal(ad, pid)
        self.assertIn(why if why is not None else how, emi._ASM_PID_REFUSALS + (("attached",) if why is None else ()),
                      "the reason or shape is one the gate counts")
        return why, how

    def test_each_pair_shape_is_vouched_for_or_refused_under_its_reason(self):
        base = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1")]
        attached = self._adapter(base + [
            dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9"),
            G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
            dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2"), promptId="p9"),
            G.compact_line(T0 + 90, "cb1", logical_parent="r2", trigger="auto"),
            dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1"), promptId="p9"),
            G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")])
        self.assertEqual(attached.summary_pids, {"p9": ["cs1"]}, "the ingest records the summary under its prompt id")
        self.assertEqual(self._refusal(attached, "p9"), (None, "attached"), "an automatic compaction's attached pair")
        self.assertEqual(self._refusal(attached, "p-unknown"), ("nosummary", None), "a pid with no summary on record")
        attached.seed = {"boundary_pids": {"p9"}}     # as a restored adapter whose document names the pid
        self.assertEqual(self._refusal(attached, "p9"), ("seed", None),
                         "a pid the restore seed names demotes, whatever the tail's summaries say: a pre-cut summary may wear it")
        side, _so = G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a1")
        adopted = self._adapter(base + side[:4])      # the wrapper is the leaf: the pair is spliced after it
        self.assertIn("cbc", adopted._adopted, "the premise: the live manual compact's pair is adopted")
        self.assertTrue(emi._asm_on_path(adopted, "cbc"), "the premise: the splice puts it on the path")
        self.assertEqual(self._refusal(adopted, "pc"), ("adopted", None),
                         "an adopted pair's pid demotes: a later episode record moves it")
        hidden = self._adapter(base + side[:2] + [G.uline(T0 + 130, "a plain next ask", "u3", "a1")])
        self.assertNotIn("cbc", hidden._adopted, "the premise: no episode adopts the pair")
        self.assertFalse(emi._asm_on_path(hidden, "cbc"), "the premise: the detached pair is off the path")
        self.assertEqual(self._refusal(hidden, "pc"), (None, "offpath"),
                         "a detached pair with no /compact episode on record: the episode rule vouches (the gate still refuses "
                         "a record that names /compact itself)")
        # as a restored adapter: its document's writer had read records wearing the id (the turn's own), and compact_pids
        # lists the ids that a record before the cut naming /compact wears (2026-10-05)
        hidden.seed = {"boundary_pids": set(), "prompt_ids": {"pc"}, "compact_pids": set()}
        self.assertEqual(self._refusal(hidden, "pc"), (None, "offpath"),
                         "no record before the cut wearing the id names /compact: the episode rule vouches, whatever prompt ids "
                         "the document's writer had read")
        hidden.seed = {"boundary_pids": set(), "prompt_ids": {"pc"}, "compact_pids": {"pc"}}
        self.assertEqual(emi._asm_pid_compact_episode(hidden, "pc"), "seed", "the seed's list is what said so")
        self.assertEqual(self._refusal(hidden, "pc"), ("seedEpisode", None),
                         "a record before the cut wearing the id names /compact, and the restored adapter cannot read it: the "
                         "pair demotes, counted apart from its shape")
        hidden.seed = {"boundary_pids": set(), "prompt_ids": {"pc"}}           # a document written before compact_pids
        self.assertEqual(self._refusal(hidden, "pc"), ("seedEpisode", None),
                         "a document without compact_pids is read by its prompt ids, every id its writer had read: it demotes")
        hidden.seed = {"boundary_pids": set(), "prompt_ids": {"p-other"}}
        self.assertEqual(self._refusal(hidden, "pc"), (None, "offpath"), "and vouches for an id they do not hold")
        rewound = self._adapter(base + side + [G.uline(T0 + 130, "a plain next ask", "u3", "a1")])
        self.assertNotIn("cbc", rewound._adopted, "the premise: the /compact was rewound away with its episode")
        self.assertFalse(emi._asm_on_path(rewound, "cbc"), "the premise: the pair is off the path")
        self.assertEqual(self._refusal(rewound, "pc"), ("offpath", None),
                         "a detached pair whose id a /compact episode wears: an episode record could adopt it")
        rewound.seed = {"boundary_pids": set(), "prompt_ids": {"pc"}, "compact_pids": {"pc"}}
        self.assertEqual((emi._asm_pid_compact_episode(rewound, "pc"), self._refusal(rewound, "pc")),
                         ("episode", ("offpath", None)), "a /compact episode on hand is counted under the pair's shape, "
                         "whatever the seed lists")

    def test_one_prompt_id_on_an_attached_pair_and_an_adopted_pair_demotes(self):
        """Every summary wearing the id is asked, not the first or the last (review of 2026-10-03, round one): one prompt id worn
        by an attached automatic pair and by a live manual /compact's adopted pair demotes, whichever pair comes first in the
        file. Asked of the attached pair alone, a record wearing the id would fold, and the fold would serve the adopted
        boundary with the parent and time a later episode record moved away from."""
        base = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1")]

        def auto(t, anchor, nxt):
            """An automatic compaction anchored on `anchor`, attached (the reply `nxt` chains on its summary)."""
            return [G.compact_line(t, "cba", logical_parent=anchor, trigger="auto"),
                    dict(G.compact_summary_line(t + 1, "csa", parent="cba"), promptId="pc"),
                    G.aline(t + 5, "the notes-api search index is migrated", nxt, "csa", stop="end_turn")]
        run = [G.aline(T0 + 20, "running the migration", "a2", "a1", tools=("Bash",), stop="tool_use"),
               G.trline(T0 + 30, "tu_a2_0", "r2", "a2", content="migration applied")]
        first = base + run + auto(T0 + 40, "r2", "a3") + G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a3")[0][:4]
        side, so = G.manual_compact_lines(T0 + 20, T0 + 40, "c", parent="a1")
        last = base + side + [G.uline(T0 + 60, "and the search cursor?", "u3", so),
                              G.aline(T0 + 70, "paging the cursor", "a4", "u3", tools=("Bash",), stop="tool_use"),
                              G.trline(T0 + 80, "tu_a4_0", "r4", "a4", content="cursor pages")] + auto(T0 + 90, "r4", "a5")
        for order, records, want in (("attached first", first, ["csa", "csc"]), ("adopted first", last, ["csc", "csa"])):
            with self.subTest(order=order):
                ad = self._adapter(records)
                self.assertEqual(ad.summary_pids, {"pc": want}, "the premise: both summaries wear the id, in file order")
                self.assertIn("cbc", ad._adopted, "the premise: the manual compact's pair is adopted")
                self.assertNotIn("cba", ad._adopted, "the premise: the automatic pair is not")
                self.assertTrue(emi._asm_on_path(ad, "cba"), "the premise: the automatic pair is attached")
                self.assertEqual(self._refusal(ad, "pc"), ("adopted", None), "named by its reason (%s)" % order)

    @staticmethod
    def _quoting_compact(t, uuid, parent, pid):
        """A record wearing `pid` whose text names /compact: what makes the id's episode a /compact one."""
        return dict(G.uline(t, "<command-name>/compact</command-name>\n<command-message>compact</command-message>", uuid, parent,
                            ps=None), promptId=pid)

    def test_a_summary_hung_on_a_user_record_is_vouched_for_by_its_episode(self):
        """A summary whose parent is not a compact_boundary, here the turn's tool result on the path, is no attached pair
        (`parent`, review of 2026-10-03, round one). It is no boundary's summary either, so adoption never reads its id: with no
        /compact episode wearing the id the episode rule vouches for it (2026-10-05), and with one it demotes."""
        recs = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1"),
                dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9"),
                G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
                dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2"), promptId="p9"),
                dict(G.compact_summary_line(T0 + 91, "cs1", parent="r2"), promptId="p9"),
                G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")]
        ad = self._adapter(recs)
        self.assertEqual((ad.summary_pids, ad.parent_of.get("cs1"), ad.by_uuid["r2"].get("type")), ({"p9": ["cs1"]}, "r2", "user"),
                         "the premise: the summary hangs off a user record")
        self.assertTrue(emi._asm_on_path(ad, "r2"), "the premise: that record is on the path")
        self.assertEqual(self._refusal(ad, "p9"), (None, "parent"), "no /compact episode wears the id: vouched for")
        ad = self._adapter(recs + [self._quoting_compact(T0 + 110, "w9", "a3", "p9")])
        self.assertEqual(self._refusal(ad, "p9"), ("parent", None), "a /compact episode wears the id: it demotes")

    def test_a_summary_replaced_by_a_later_copy_of_its_uuid_is_vouched_for_by_its_episode(self):
        """A later record reusing the summary's uuid replaces it (last write wins): the uuid the adapter recorded under the id is
        no summary any more (`replaced`, review of 2026-10-03, round one), even with that copy hung on the attached boundary.
        Adoption reads no summary there, and a record wearing the id can only reach it through a /compact episode: with none
        on record the episode rule vouches for the id (2026-10-05), and with one it demotes."""
        recs = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1"),
                dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "a1"), promptId="p9"),
                G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
                dict(G.trline(T0 + 80, "tu_a2_0", "r2", "a2"), promptId="p9"),
                G.compact_line(T0 + 90, "cb1", logical_parent="r2", trigger="auto"),
                dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1"), promptId="p9"),
                G.uline(T0 + 100, "a note written again under the summary's uuid", "cs1", "cb1")]
        ad = self._adapter(recs)
        self.assertEqual(ad.summary_pids, {"p9": ["cs1"]}, "the premise: the ingest recorded the summary under its id")
        self.assertIsNot(ad.by_uuid["cs1"].get("isCompactSummary"), True, "the premise: the later copy replaced the summary")
        self.assertEqual(ad.parent_of.get("cs1"), "cb1", "the premise: the copy hangs on the boundary")
        self.assertTrue(emi._asm_on_path(ad, "cb1") and "cb1" not in ad._adopted, "the premise: the boundary is attached")
        self.assertEqual(self._refusal(ad, "p9"), (None, "replaced"), "no /compact episode wears the id: vouched for")
        ad = self._adapter(recs + [self._quoting_compact(T0 + 110, "w9", "cs1", "p9")])
        self.assertEqual(self._refusal(ad, "p9"), ("replaced", None), "a /compact episode wears the id: it demotes")

    def test_the_episode_reads_compact_as_adoption_does(self):
        """What makes an episode a /compact one to the gate (_asm_names_compact), read with adoption's reader
        (_names_compact) and wider, every tag where adoption reads the first, so it says yes at least whenever adoption would:
        the texts the class lists."""
        def rec(content):
            return {"type": "user", "uuid": "x", "message": {"role": "user", "content": content}}
        self.assertEqual([emi._asm_names_compact(rec(c)) for c in self.NAMES_COMPACT], [True] * len(self.NAMES_COMPACT))
        self.assertEqual([emi._asm_names_compact(rec(c)) for c in self.NAMES_NO_COMPACT], [False] * len(self.NAMES_NO_COMPACT))

    def test_adoption_never_reads_compact_where_the_gate_does_not(self):
        """The gate is safe only while it reads /compact wherever adoption does (review of 2026-10-05, round one), so this runs
        adoption itself. Per listed text, an adapter holds a live manual /compact's detached pair, its summary wearing pc, and
        an on-path record wearing pc with that text: adoption splices the pair behind the record only when it reads the text as
        a /compact episode, and then the gate's reading of the record must say yes. A widening of adoption's reading alone
        (another tag, another source, another name) that reaches one of the listed texts adopts on a text where the gate reads
        no /compact, and this fails; the command-message-only text is the widening the review planted, and a widening no
        listed text reaches passes until a text for it is added. Adoption reads the first tag only, so the text whose first
        tag is another command's adopts nothing."""
        base = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1")]
        side, _so = G.manual_compact_lines(T0 + 60, T0 + 120, "c", parent="a1")    # side[:2]: the detached pair, its summary wearing pc
        adopted = []
        for c in self.NAMES_COMPACT + self.NAMES_NO_COMPACT:
            rec = dict(G.uline(T0 + 130, c, "w9", "a1", ps=None), promptId="pc")
            ad = self._adapter(base + side[:2] + [rec])
            adopted.append("cbc" in ad._adopted)
            with self.subTest(text=c):
                if "cbc" in ad._adopted:
                    self.assertTrue(emi._asm_names_compact(rec), "adoption read /compact in this record, so the gate must too")
        self.assertEqual(adopted[:len(self.NAMES_COMPACT)], [True, True, True, False],
                         "the premise: adoption splices the pair behind each text whose first tag names /compact")

    def test_the_documents_gates_name_the_summaries_before_its_cut(self):
        """What the seed check reads (_asm_doc_boundary_pids, 2026-10-03): a document's gates name the prompt ids of the summaries
        before its cut. A summary past the cut is read again from the tail by every restore, so its id stays out of the seed
        and the restored entry checks its pair as a whole entry does."""
        records = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1"),
                   G.compact_line(T0 + 20, "cb0", logical_parent="a1", trigger="auto"),
                   dict(G.compact_summary_line(T0 + 21, "cs0", parent="cb0"), promptId="p1"),
                   dict(G.uline(T0 + 60, "migrate the notes-api search index", "u2", "cs0"), promptId="p9"),
                   G.aline(T0 + 70, "running the migration", "a2", "u2", tools=("Bash",), stop="tool_use"),
                   G.compact_line(T0 + 90, "cb1", logical_parent="a2", trigger="auto"),
                   dict(G.compact_summary_line(T0 + 91, "cs1", parent="cb1"), promptId="p9"),
                   G.aline(T0 + 100, "checking the rebuilt index", "a3", "cs1", tools=("Bash",), stop="tool_use")]
        ad = self._adapter(records)
        recs = [(ad.seq_of[r["uuid"]], r) for r in records]
        self.assertEqual(ad.boundary_pids, {"p1", "p9"}, "the premise: the whole adapter holds both summaries' ids")
        self.assertEqual(emi._asm_doc_boundary_pids(ad, recs, ad.seq_of["cb1"]), {"p1"},
                         "a cut before the second compaction names the first summary's id alone")
        self.assertEqual(emi._asm_doc_boundary_pids(ad, recs, ad.seq_of["a3"]), {"p1", "p9"},
                         "a cut past the second summary names both")

    def test_the_documents_gates_name_the_ids_a_pre_cut_compact_wears(self):
        """What the episode rule's seed check reads (_asm_doc_compact_pids, 2026-10-05): the prompt ids that a user record before
        the cut naming /compact wears, a rewound manual /compact's included, and never the ids the turn's own records wear, which
        the gates' prompt_ids holds."""
        side, _so = G.manual_compact_lines(T0 + 20, T0 + 30, "c", parent="a1")
        records = [G.uline(T0, "first ask", "u1"), G.aline(T0 + 10, "first reply", "a1", "u1")] + side[2:] + [
            G.uline(T0 + 40, "never mind, back to the search page size", "u2", "a1"),
            G.aline(T0 + 50, "paging it", "a2", "u2", stop="end_turn"),
            dict(G.uline(T0 + 60, "migrate the notes-api search index", "u3", "a2"), promptId="p9"),
            G.aline(T0 + 70, "running the migration", "a3", "u3", tools=("Bash",), stop="tool_use"),
            dict(G.trline(T0 + 80, "tu_a3_0", "r3", "a3"), promptId="p9"),
            dict(G.uline(T0 + 90, "the notes say <command-name>/compact</command-name> ran", "w9", "r3", ps=None), promptId="pd",
                 isMeta=True)]
        ad = self._adapter(records)
        recs = [(ad.seq_of[r["uuid"]], r) for r in records]
        self.assertTrue({"pc", "p9", "pd"} <= ad.prompt_ids, "the premise: the writer read every id")
        self.assertEqual(emi._asm_doc_compact_pids(ad, recs, ad.seq_of["u3"]), {"pc"},
                         "a cut before the turn names the rewound /compact's id alone, not the turn's")
        self.assertEqual(emi._asm_doc_compact_pids(ad, recs, ad.seq_of["w9"] + 1), {"pc", "pd"},
                         "a cut past a record that names /compact names its id too")


class PostalHeal(unittest.TestCase):
    """A marker that missed the index at emit time re-authors once the log catches up — on the
    SERVE path (no transcript change), matching what a full parse with the same rows returns."""
    maxDiff = None

    def test_delivery_record_heals_on_serve(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.postal_line(T0 + 100, "ASK: bump the alpha", "u2", "a1"),
                    G.aline(T0 + 110, "ack peer", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertIsNone(trig["author"]["peer"])          # the miss, cached
            kw["postal_log"] = list(G.SENT_LOG)                # the log catches up; file unchanged
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["peer"], G.PEER)   # healed in place, no re-parse

    def test_absorbed_splice_heals_too(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            spliced = "ASK: bump the alpha\n<!-- romp-msg-id: %s -->" % G.MID
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 20, "working", "a1", "u1", tools=("Read",), stop="tool_use"),
                    G.attline(T0 + 40, spliced, "att1", "a1"),
                    G.aline(T0 + 90, "folded it in", "a2", "att1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            # att1's file-order predecessor is stamped before its send, so each emit of it clamps and
            # counts landedT-clamp: the parse's, and the heal's on the serve road once the log resolves
            # the sender (a heal that raises falls back to a full parse, with the same tree)
            keys = ("serve", "fallback", "landedT-clamp")
            moved = lambda s0: {k: emi._ASM_STATS.get(k, 0) - s0.get(k, 0) for k in keys}
            s0 = dict(emi._ASM_STATS)
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(moved(s0)["landedT-clamp"], 1,
                             "premise: the parse clamps the splice and counts it")
            kw["postal_log"] = list(G.SENT_LOG)
            s0 = dict(emi._ASM_STATS)
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(moved(s0), {"serve": 1, "fallback": 0, "landedT-clamp": 1})
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            ab = [a for t in inc["turns"] for a in t["atoms"] if a.get("absorbed")][0]
            self.assertEqual(ab["author"]["peer"], G.PEER)


class ServedTreesAreCallerOwned(unittest.TestCase):
    def test_mutating_a_served_tree_never_reaches_the_next_serve(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in [
                G.uline(T0, "opener", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")]) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            one = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            for t in one["turns"]:
                for a in t["atoms"]:
                    a["type"] = "vandalized"
                    a.pop("message", None)
            two = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            kinds = [a["type"] for t in two["turns"] for a in t["atoms"]]
            self.assertEqual(kinds, ["user", "assistant"])

    def test_eviction_is_one_at_a_time_never_a_clear(self):
        old_max = emi._ASM_CACHE_MAX
        emi._ASM_CACHE_MAX = 2
        try:
            emi._ASM_CACHE.clear()
            with tempfile.TemporaryDirectory() as td:
                paths = []
                for i in range(3):
                    p = Path(td) / ("%08d-1111-2222-3333-444444444444.jsonl" % i)
                    p.write_text(json.dumps(G.uline(T0, "ask %d" % i, "u1")) + "\n")
                    paths.append(p)
                kw = {"states": None, "postal_log": [], "now": NOW}
                for p in paths:
                    emi.parse_session(str(p), rompuuid=p.stem, candidate_files=[str(p)], **kw)
                self.assertEqual(len(emi._ASM_CACHE), 2)
                held = [k[0] for k in emi._ASM_CACHE]
                self.assertNotIn(os.path.realpath(str(paths[0])), held)
                self.assertIn(os.path.realpath(str(paths[2])), held)
        finally:
            emi._ASM_CACHE_MAX = old_max
            emi._ASM_CACHE.clear()



class FallbackPath(unittest.TestCase):
    """The correctness backstop the design leans on: a fold-machinery exception serves a plain
    full parse (equivalent output), warns loudly, pops the poisoned entry, and RECOVERS — the
    next parse rebuilds and the one after folds again."""
    maxDiff = None

    def test_a_fold_exception_serves_a_full_parse_loudly_and_recovers(self):
        import contextlib
        import io
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text(json.dumps(G.uline(T0, "opener", "u1")) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            with open(path, "a") as fh:
                fh.write(json.dumps(G.aline(T0 + 10, "ok", "a1", "u1")) + "\n")
            old_gates = emi._asm_gates
            emi._asm_gates = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("synthetic fold bug"))
            emi._ASM_WARNED[0] = False
            try:
                fb0 = emi._ASM_STATS["fallback"]
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                emr._ASM_CACHE.clear()
                ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
                self.assertEqual(_tree(ref), _tree(inc))
                self.assertEqual(emi._ASM_STATS["fallback"], fb0 + 1)
                self.assertTrue(emi._ASM_WARNED[0])
                self.assertIn("assembly fold failed", err.getvalue())
                self.assertNotIn(os.path.realpath(str(path)),
                                 [k[0] for k in emi._ASM_CACHE])   # the poisoned entry is gone
            finally:
                emi._asm_gates = old_gates
            fulls0 = emi._ASM_STATS["full"]
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emi._ASM_STATS["full"], fulls0 + 1)      # rebuilds…
            with open(path, "a") as fh:
                fh.write(json.dumps(G.uline(T0 + 60, "next ask", "u2", "a1")) + "\n")
            folds0 = emi._ASM_STATS["fold"]
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1)      # …and folds again
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))


class HealThenFold(unittest.TestCase):
    def test_postal_and_transcript_catch_up_in_the_same_visit(self):
        # the heal runs before the fold in one _assemble visit: the log resolves an old miss
        # while new records append — both effects must land, matching a full parse exactly
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.postal_line(T0 + 100, "ASK: bump the alpha", "u2", "a1")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None, "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            with open(path, "a") as fh:
                fh.write(json.dumps(G.aline(T0 + 110, "ack peer", "a2", "u2")) + "\n")
            kw["postal_log"] = list(G.SENT_LOG)
            folds0 = emi._ASM_STATS["fold"]
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            self.assertEqual(emi._ASM_STATS["fold"], folds0 + 1)   # healed on the FOLD path
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["peer"], G.PEER)

    def test_wrong_but_resolved_earlier_marker_still_heals(self):
        # the review's major catch: a QUOTED earlier marker resolves first, so the author has
        # peer set but names the wrong message — the heal must keep revisiting until the
        # TRAILING marker resolves, exactly as a fresh full parse would decide it
        mid2 = "1700000000.222_333.TESTHOST"
        body = ("fwd of my earlier note\n<!-- romp-msg-id: %s -->\n"
                "and the real ask\n<!-- romp-msg-id: %s -->" % (G.MID, mid2))
        row2 = {"t": T0 + 195, "ev": "sent", "id": mid2, "from": "feeddesign",
                "from_id": "77777777-6666-5555-4444-333333333333", "to_id": SID,
                "body": "ASK: the real one"}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            recs = [G.uline(T0, "opener", "u1"),
                    G.aline(T0 + 10, "ok", "a1", "u1"),
                    G.uline(T0 + 200, body, "u2", "a1", ps=None),
                    G.aline(T0 + 210, "ack", "a2", "u2")]
            path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
            kw = {"candidate_files": [str(path)], "states": None,
                  "postal_log": list(G.SENT_LOG), "now": NOW}   # only the QUOTED marker resolves
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["mid"], G.MID)         # provisional: quoted marker won
            kw["postal_log"] = list(G.SENT_LOG) + [row2]           # the trailing row lands
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            trig = [a for t in inc["turns"] for a in t["atoms"] if a.get("uuid") == "u2"][0]
            self.assertEqual(trig["author"]["mid"], mid2)          # re-decided, not latched


class StatesOnlyChange(unittest.TestCase):
    def test_idle_rows_growing_under_a_static_transcript_serve_equivalently(self):
        # idle/orphan overlays are synthesized OUTSIDE the cache on every call — a states-only
        # change must flow through the serve path without any transcript re-emit
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / (SID + ".jsonl")
            path.write_text("\n".join(json.dumps(r) for r in [
                G.uline(T0, "opener", "u1"), G.aline(T0 + 10, "ok", "a1", "u1")]) + "\n")
            kw = {"candidate_files": [str(path)], "states": [], "postal_log": [], "now": NOW}
            emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            serves0 = emi._ASM_STATS["serve"]
            kw["states"] = list(G.IDLE_STATES)
            inc = emi.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            emr._ASM_CACHE.clear()
            ref = emr.parse_session(str(path), rompuuid=SID, name="impl", dir="/TESTDIR", **kw)
            self.assertEqual(_tree(ref), _tree(inc))
            self.assertEqual(emi._ASM_STATS["serve"], serves0 + 1)
            self.assertTrue(any(a["type"] == "idle"
                                for t in inc["turns"] for a in t["atoms"]))
if __name__ == "__main__":
    unittest.main(verbosity=2)
