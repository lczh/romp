#!/usr/bin/env python3
"""A finished subagent's records leave memory on a quiet fold too (the kernel memory work, 2026-10-01).

The record cache holds a file's decoded records, about three times the file's size. A fold with drop_after="quiescent"
let a file's records go only when it STEPPED records over a file unchanged for two minutes. A subagent the kernel follows
live writes its last record while the file is still fresh, so every later fold finds nothing new and its whole entry
stayed for the kernel's life. The spend guard read every agent file in its window through the reader with no fold at
all, so a workflow agent (no card, no fold) stayed resident too. Pinned here:
  * drop_after="returned" (a subagent's own transcript): a fold that finds nothing new pops a quiescent entry that holds
    records once the file's document stands at the entry's witness; the next fold restores from the document over a
    zero-byte tail, never a whole read; the restored empty tail stays, so later folds are stat-cost hits; "quiescent" (the
    leaf's policy) is unchanged. Nothing pops, and nothing is re-read after, when the document cannot stand for the entry:
    with checkpoints off, with a sibling cursor behind the entry, a fold recorded without its state, a cut behind the entry,
    a dirty path while the drop write is off, or a parse holding the very records list; over the cycle's budget the drop
    waits unowed, and under the converge hold it is paid without its pop; a cursor of another generation that the document
    does not carry does not hold it;
  * the kernel's agent-file folds take the new policy and the parent-leaf fold does not;
  * closing the subagent viewer pops the background-task cursor its render left on an agent file with a task notification,
    so the agent still drops when it returns;
  * the spend guard routes its agent reads through the dropping folds (never starting a steps state for a workflow agent,
    never folding while a viewer's parse holds the records), prices a dropped file from its memo without entering it
    again, prices a resumed agent from its memo and the tail without a whole read (a response split across the cut counted
    once), reads the file whole when the memo ends short of the tail or starts after the window, prices a file a fresh
    process restored as a tail, prices a restored tail that covers the window without a whole read and folds it out of
    memory, and re-scans its memo when a tail entry becomes the whole file;
  * a lab-style run of a synthetic session (agents with cards, workflow agents without) on an advancing clock, the cards'
    folds run in lockstep, in the kernel's own pattern, or stopping before the agent returns: the entries holding records
    and their bytes return to the live set as agents return, the workflow agents' entries stop climbing, and the guard's
    window equals a reference sum at every cycle; agents that fall silent for over two minutes mid-run are dropped and
    resumed with no whole read; without the guard, a card cursor left behind keeps its agent resident.
SYNTHETIC fixtures only: placeholder ids, the notes-api demo names, transcripts written here."""
import inspect
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source   # first: it brings in the tests package's temp-root hooks before the preamble mints one

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()   # hermetic state before any romp code loads
os.environ.pop("ROMP_STATE_DIR", None)
em = load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
jd = load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_returned_drop", os.path.join(BIN, "romp-kernel"))

SID = "11111111-2222-4333-8444-0000000000d4"
T0 = 1_781_200_000                # the simulated clock's start (the event model's quiescence gate reads it)
PRICES = {"claude-opus-4-8": {"in": 5e-6, "out": 25e-6, "cache_w": 6.25e-6, "cache_r": 0.5e-6}}
PAD = "x" * 1200                  # a tool input of about a kilobyte, so a record weighs like a real one


class Clock:
    """The event model's `time` with a settable wall clock: its quiescence gate reads time.time(), so a file ages without
    being touched (touching it would read as a rewrite). Everything else is the real module."""

    def __init__(self, t):
        self.t = float(t)

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def rec(t, n, launch=False):
    """One assistant record of an agent's transcript at time t: a tool call (the card's folds read it), usage (the spend
    guard prices it) and a message id of its own."""
    t = int(t)
    name, inp = (("Bash", {"command": "make test", "run_in_background": True, "description": "tests %d" % n}) if launch
                 else ("Read", {"file_path": "/w/notes-api/api/notes.py", "description": PAD}))
    return {"type": "assistant", "timestamp": iso(t), "uuid": "u-%d-%d" % (t, n), "sessionId": SID,
            "message": {"id": "msg-%d-%d" % (t, n), "role": "assistant", "model": "claude-opus-4-8",
                        "usage": {"input_tokens": 100, "output_tokens": 1000,
                                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                        "content": [{"type": "tool_use", "id": "toolu_%d_%d" % (t, n), "name": name, "input": inp}]}}


def usd(r):
    u, p = r["message"]["usage"], PRICES["claude-opus-4-8"]
    return u["input_tokens"] * p["in"] + u["output_tokens"] * p["out"]


def in_window(recs, t):
    """The reference: what the records written at or after t - 600 cost, each record once."""
    return sum(usd(r) for r in recs if km._msg_epoch(r) >= t - 600)


def bare(r):
    """The record without its message id (a Codex transcript's records carry none, so each counts on its own)."""
    r["message"].pop("id", None)
    return r


def chain(recs, parent=None):
    """The records as one conversation: each record's parent is the one before it (a null parent reads as a fork)."""
    for r in recs:
        r["parentUuid"], r["isSidechain"] = parent, True
        parent = r["uuid"]
    return recs


def write(path, recs, mtime):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))


def append(path, recs, mtime):
    with open(path, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    os.utime(path, (mtime, mtime))


class Base(unittest.TestCase):
    def setUp(self):
        self.saved_state = jd.STATE
        self.td = Path(tempfile.mkdtemp())
        root = self.td / "state"
        jd._rebind_state(root)
        root.mkdir(parents=True, exist_ok=True)
        (root / "session-hosts").write_text("off")      # a state root of the test's own runs no session host
        self.fresh_process()
        em.checkpoint_cycle_begin(64 * 1024 * 1024)      # the cycle budget the drop's document write charges
        self.clock = Clock(T0)
        p = mock.patch.object(em, "time", self.clock)
        p.start()
        self.addCleanup(p.stop)
        self.proj = self.td / "proj"
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.sub = self.proj / SID / "subagents"

    def tearDown(self):
        self.fresh_process()
        jd._rebind_state(self.saved_state)
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")

    def fresh_process(self):
        """What a kernel restart does to the memory side: entries, cursors, pending restores, memos and counters gone; the
        documents on disk stay."""
        em.set_checkpoint_dir(lambda: jd.STATE / "checkpoints")
        with em._JSONL_CACHE_LOCK:
            em._JSONL_CACHE.clear()
            em._JSONL_CACHE_BYTES[0] = 0
        em._TRAILING_CACHE.clear()
        with em._READ_BYTES_LOCK:
            em._READ_BYTES.clear()
        for c in list(em._FOLD_REG.values()):
            c.clear()
        with em._CKPT_LOCK:
            em._COLD_FOLDS.clear(); em._COLD_REASONS.clear(); em._COLD_OVER_KB.clear()
        km._SPEND_ROWS_CACHE.clear()
        km._SPEND_TREE_CACHE.clear()
        with em._ASM_LOCK:
            gone = list(em._ASM_CACHE.values())
            em._ASM_CACHE.clear()
        for e in gone:
            em._asm_release(e)
        em._RECORD_CACHE_STATS["dropped"] = 0
        em._RECORD_CACHE_STATS["droppedBytes"] = 0
        em._RECORD_CACHE_STATS["wholeReads"] = {}
        em._RECORD_CACHE_STATS["wholeReadsByStage"] = {}

    def entry(self, p):
        with em._JSONL_CACHE_LOCK:
            return em._JSONL_CACHE.get(str(p))

    def read_of(self, p):
        with em._READ_BYTES_LOCK:
            return em._READ_BYTES.get(str(p), 0)

    def doc(self, p):
        return json.loads(em._ckpt_file(str(p)).read_text())

    def guard_whole_reads(self):
        """The whole reads the spend guard's own row scan made (a tail upgraded to the whole file, or a file read from zero)."""
        return {k: v["count"] for k, v in em.record_cache_stats()["wholeReads"].items() if k.endswith("<-_spend_file_rows")}


class ReturnedPolicy(Base):
    """fold_records(drop_after="returned") in the event model alone."""

    def fold(self, cache, p, policy="returned", name="synthGist", kinds=None):
        return em.fold_records(cache, str(p), list, lambda st, o: st + [o["message"]["id"]],
                               on=kinds.append if kinds is not None else None, ckpt=name, drop_after=policy)

    def test_a_returned_file_leaves_memory_at_its_first_quiet_fold_once_its_document_stands(self):
        p = self.sub / "agent-a1111111111111111.jsonl"
        recs = [rec(T0 - 30 + i, i) for i in range(20)]
        write(p, recs, T0 - 11)                                   # the agent wrote its last record 11 s ago: fresh
        want = [r["message"]["id"] for r in recs]
        cache, kinds = {}, []
        self.assertEqual(self.fold(cache, p, kinds=kinds), want)
        self.assertEqual(kinds[-1], "refold")
        self.assertEqual(self.fold(cache, p, kinds=kinds), want)
        self.assertEqual(kinds[-1], "hit")
        self.assertIsNotNone(self.entry(p), "a fresh file keeps its records")
        size, read0 = os.path.getsize(p), self.read_of(p)
        self.clock.t = T0 + 300                                   # five minutes on, the file untouched: the agent returned
        self.assertEqual(self.fold(cache, p, kinds=kinds), want)
        self.assertEqual(kinds[-1], "hit", "nothing new to step")
        self.assertIsNone(self.entry(p), "the quiet fold let the records go")
        st = em.record_cache_stats()
        self.assertEqual((st["dropped"], st["droppedBytes"]), (1, size))
        d = self.doc(p)
        self.assertEqual((d["count"], d["folds"]["synthGist"]["count"]), (20, 20), "the drop wrote the document at the witness first")
        self.assertIn("state", d["folds"]["synthGist"])
        self.assertEqual(self.fold(cache, p, kinds=kinds), want, "the next fold answers the same")
        self.assertEqual(kinds[-1], "restore", "from the document")
        self.assertLessEqual(self.read_of(p) - read0, 2 * em._JSONL_TAIL_GUARD, "over a zero-byte tail: the guard bytes, never the file")
        ent = self.entry(p)
        self.assertEqual((ent[5], len(ent[4]), em._entry_weight(ent)), (20, 0, 0), "an empty witness tail that weighs nothing")
        r1 = self.read_of(p)
        for _ in range(3):
            self.assertEqual(self.fold(cache, p, kinds=kinds), want)
            self.assertEqual(kinds[-1], "hit", "the empty tail stays: the folds after it cost a stat, not a restore each")
        self.assertIs(self.entry(p), ent)
        self.assertEqual(self.read_of(p), r1)
        self.assertEqual(em.record_cache_stats()["dropped"], 1)

    def test_the_quiescent_policy_keeps_its_entry_on_a_quiet_fold_as_before(self):
        p = self.proj / "leaf-like.jsonl"
        write(p, [rec(T0 - 30 + i, i) for i in range(10)], T0 - 11)
        cache = {}
        self.fold(cache, p, policy="quiescent")
        self.clock.t = T0 + 300
        self.fold(cache, p, policy="quiescent")
        self.assertIsNotNone(self.entry(p), "the leaf's policy: a quiet fold leaves a live session's file warm")
        self.assertEqual(em.record_cache_stats()["dropped"], 0)

    def test_with_checkpoints_off_a_quiet_fold_never_pops(self):
        em.set_checkpoint_dir(None)
        p = self.sub / "agent-a2222222222222222.jsonl"
        write(p, [rec(T0 - 30 + i, i) for i in range(10)], T0 - 11)
        cache = {}
        self.fold(cache, p)
        self.clock.t = T0 + 300
        self.fold(cache, p)
        self.assertIsNotNone(self.entry(p), "no document to restore from: a pop would make every later fold a whole read")
        ent = self.entry(p)
        self.assert_stands(cache, p, ent, "no document to restore from")
        self.assertEqual(em.record_cache_stats()["dropped"], 0)

    def test_a_sibling_cursor_behind_the_entry_holds_the_pop_until_it_steps(self):
        p = self.sub / "agent-a3333333333333333.jsonl"
        write(p, [rec(T0 - 40 + i, i) for i in range(12)], T0 - 20)
        a, b = {}, {}
        self.fold(b, p, name="synthB")                            # b stands at 12
        append(p, [rec(T0 - 19 + i, 12 + i) for i in range(8)], T0 - 11)
        self.fold(a, p, name="synthA")                            # a stands at 20, over the same entry (an append)
        self.clock.t = T0 + 300
        self.fold(a, p, name="synthA")                            # quiet, with b at the entry's generation 8 records behind
        self.assertIsNotNone(self.entry(p), "a pop now would leave b a whole read to resume from")
        ent = self.entry(p)
        self.assert_stands(a, p, ent, "b would read whole after a pop", name="synthA")
        kinds = []
        self.assertEqual(len(self.fold(b, p, name="synthB", kinds=kinds)), 20)
        self.assertEqual(kinds[-1], "append")
        self.assertIsNone(self.entry(p), "b stepped the rest, and its drop takes the entry")

    def returned(self, name, n=20):
        """An agent file of `n` records, last written 11 s before T0, folded once while fresh; the clock then moves five
        minutes on, so the next fold is a quiet one over a returned file. Returns (path, cursor dict, the entry)."""
        p = self.sub / name
        write(p, [rec(T0 - 30 + i, i) for i in range(n)], T0 - 11)
        cache = {}
        self.fold(cache, p)
        self.clock.t = T0 + 300
        return p, cache, self.entry(p)

    def assert_stands(self, cache, p, ent, why, name="synthGist"):
        """Three more quiet folds leave the very entry standing and read nothing of the file."""
        r0 = self.read_of(p)
        for _ in range(3):
            self.fold(cache, p, name=name)
            self.assertIs(self.entry(p), ent, why)
        self.assertEqual(self.read_of(p), r0, "nothing re-read: %s" % why)

    def test_a_fold_recorded_without_its_state_holds_the_quiet_drop(self):
        cap = mock.patch.object(em, "_CKPT_FOLD_CAP", 64)       # a state over the cap is written as a cursor without its state
        cap.start()
        self.addCleanup(cap.stop)
        p, cache, ent = self.returned("agent-a9100000000000001.jsonl")
        self.fold(cache, p)                                       # quiet: the drop writes the document first
        f = self.doc(p)["folds"]["synthGist"]
        self.assertEqual((f["count"], "state" in f, f.get("over")), (20, False, 1), "over the cap: the cursor and its size only")
        self.assertIs(self.entry(p), ent, "a pop would leave the next fold no state to resume from")
        self.assert_stands(cache, p, ent, "every later quiet fold would read the file whole after a pop")

    def test_a_document_whose_cut_sits_behind_the_entry_holds_the_quiet_drop(self):
        p = self.sub / "agent-a9200000000000002.jsonl"
        write(p, [rec(T0 - 40 + i, i) for i in range(12)], T0 - 20)
        a, b = {}, {}
        self.fold(b, p, name="synthB")                            # b stands at 12
        self.assertTrue(em.checkpoint_write(str(p)))              # b's document
        b.clear()                                                 # b's cursor gone (a fold that stopped); its state stays on disk
        append(p, [rec(T0 - 19 + i, 12 + i) for i in range(8)], T0 - 11)
        self.fold(a, p, name="synthA")                            # a stands at 20
        ent = self.entry(p)
        self.clock.t = T0 + 300
        self.fold(a, p, name="synthA")                            # quiet: the drop writes the document first, carrying b's state
        d = self.doc(p)
        self.assertEqual((d["count"], d["folds"]["synthA"]["count"], d["folds"]["synthB"]["count"]), (12, 20, 12),
                         "the cut sits at the carried b's count, behind the entry's twenty")
        self.assertIs(self.entry(p), ent, "a pop would leave a tail from the cut")
        self.assert_stands(a, p, ent, "every later quiet fold would re-read the tail from the cut after a pop", name="synthA")

    def test_a_dirty_path_holds_the_quiet_drop_while_the_drop_write_is_off(self):
        p, cache, ent = self.returned("agent-a9300000000000003.jsonl", n=10)
        self.clock.t = T0
        self.assertTrue(em.checkpoint_write(str(p)))              # a document at the witness
        cache.clear()
        self.fold(cache, p)                                       # a fold over the same records since: the path is dirty again
        self.assertIn(str(p), em.checkpoint_dirty())
        self.clock.t = T0 + 300
        em.checkpoint_cycle_begin(0)                              # the drop write off (ROMP_CKPT_CONVERGE_MS=0)
        self.assert_stands(cache, p, ent, "a fold moved since the document was written: the document is not its word")
        em.checkpoint_cycle_begin(64 * 1024 * 1024)
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "with the write on, the document is rewritten first and the drop taken")

    def test_a_quiet_drop_over_the_cycle_budget_waits_unowed_for_the_next_quiet_fold(self):
        p, cache, ent = self.returned("agent-a9400000000000004.jsonl")
        em.checkpoint_cycle_begin(1)                              # a cycle with no room for the document write
        self.fold(cache, p)
        self.assertIs(self.entry(p), ent, "over the budget the write and the drop wait")
        self.assertFalse(em._ckpt_file(str(p)).exists())
        self.assertNotIn(str(p), em._DROP_OWED, "a quiet drop is never owed: the next quiet fold decides it afresh")
        em.checkpoint_cycle_begin(64 * 1024 * 1024)
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "the next quiet fold with room writes the document and drops")

    def test_under_the_converge_hold_a_quiet_drop_is_paid_without_its_pop(self):
        p, cache, ent = self.returned("agent-a9500000000000005.jsonl")
        with em.hold_quiescent_drops():
            self.fold(cache, p)
            self.assertIs(self.entry(p), ent, "held: nothing written or popped inside the hold")
        self.assertEqual(em.pay_held_drops(), {str(p): True}, "the hold pays the document write")
        self.assertIs(self.entry(p), ent, "and not the quiet pop: the next quiet fold takes it")
        self.fold(cache, p)
        self.assertIsNone(self.entry(p))

    def test_a_parse_holding_the_very_records_keeps_them_on_a_quiet_fold(self):
        p, cache, ent = self.returned("agent-a9600000000000006.jsonl")

        def parse():                                              # the subagent viewer's parse of the agent's own file
            em.parse_session(str(p), rompuuid=SID, name="api", dir="/w/notes-api", candidate_files=[str(p)], states=None,
                             postal_log=[], now=T0)
        parse()
        with em._ASM_LOCK:
            held = [e for e in em._ASM_CACHE.values() if getattr(e.get("ad"), "_src", {}).get(str(p)) is ent[4]]
        self.assertEqual(len(held), 1, "the parse's assembly entry holds the reader's very list")
        self.fold(cache, p)                                       # quiet: the document is written, the entry stays
        self.assertIs(self.entry(p), ent, "a pop would free nothing the parse holds")
        r0 = self.read_of(p)
        for _ in range(3):
            parse()                                               # the viewer rebuilt (its running flag flipped, say)
            self.fold(cache, p)
            self.assertIs(self.entry(p), ent)
        self.assertEqual(self.read_of(p), r0, "no rebuild read the file whole again")
        with em._ASM_LOCK:
            gone = list(em._ASM_CACHE.values())
            em._ASM_CACHE.clear()
        for e in gone:
            em._asm_release(e)
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "the parse gone, the next quiet fold drops")

    def test_a_cursor_of_another_generation_the_document_does_not_carry_leaves_the_quiet_drop_alone(self):
        p = self.sub / "agent-a9700000000000007.jsonl"
        write(p, [rec(T0 - 200 + i, i) for i in range(4)], T0 - 11)
        a, b = {}, {}
        self.fold(b, p, name="synthB")                            # b folds early and stops, at 4
        append(p, [rec(T0 - 100 + i, 4 + i) for i in range(96)], T0 - 5)
        self.clock.t = T0 + 300
        self.fold(a, p, name="synthA")                            # a's first fold steps all 100 over the quiet file: the drop
        self.assertIsNone(self.entry(p))
        self.assertNotIn("synthB", self.doc(p)["folds"], "b lags too far to be written: the document never carries it")
        append(p, [rec(T0 + 301 + i, 100 + i) for i in range(8)], T0 + 308)   # the agent resumes
        self.clock.t = T0 + 310
        kinds = []
        self.assertEqual(len(self.fold(a, p, name="synthA", kinds=kinds)), 108)
        self.assertEqual(kinds[-1], "restore", "a restored from the document over the new tail")
        ent = self.entry(p)
        self.assertEqual((ent[5], len(ent[4])), (100, 8))
        old = b[str(p)][1]
        self.assertNotEqual(old, ent[6], "b's cursor sits at the generation before the drop")
        self.clock.t = T0 + 600                                   # quiet again
        self.fold(a, p, name="synthA")
        self.assertIsNone(self.entry(p), "a cursor of another generation does not hold the quiet drop")
        self.assertEqual(b[str(p)][1], old, "b never ran: its cursor still sits at the old generation")
        self.assertEqual(len(self.fold(b, p, name="synthB", kinds=kinds)), 108, "b's next fold reads the file once and answers whole")
        self.assertEqual(kinds[-1], "refold")


class KernelFolds(Base):
    def test_the_agent_card_folds_drop_a_returned_agent_and_the_leaf_fold_keeps_an_idle_leaf(self):
        ap = str(self.sub / "agent-a4444444444444444.jsonl")
        write(ap, [rec(T0 - 40 + i, i, launch=(i == 3)) for i in range(15)], T0 - 11)
        write(self.leaf, [rec(T0 - 60 + i, i) for i in range(6)], T0 - 11)
        steps0, ids0 = km._agent_steps(ap), km._agent_launch_ids(ap)
        self.assertEqual((steps0["calls"], len(ids0)), (15, 1), "the fixture drives both folds")
        km._agent_launch_state(self.leaf)
        self.assertIsNotNone(self.entry(ap))
        self.clock.t = T0 + 300                                   # both files untouched for five minutes
        self.assertEqual(km._agent_steps(ap), steps0)
        self.assertEqual(km._agent_launch_ids(ap), ids0)
        km._agent_launch_state(self.leaf)
        ent = self.entry(ap)
        self.assertTrue(ent is None or (not ent[4] and em._entry_weight(ent) == 0),
                        "the returned agent's records left memory (at most an empty witness tail stands)")
        self.assertEqual(em.record_cache_stats()["dropped"], 1)
        leaf = self.entry(self.leaf)
        self.assertIsNotNone(leaf, "the idle parent leaf keeps its entry: a live session's file is never dropped on a quiet fold")
        self.assertEqual((leaf[5], len(leaf[4])), (0, 6))


class ViewerClose(Base):
    """The subagent viewer's render folds the background tasks of an agent file that carries a task notification (or a nested
    background launch), and no other reader steps that cursor: once the viewer closes and the agent writes on, a cursor
    behind the entry would hold the returned agent's quiet drop for good. Closing the viewer pops it."""

    AID = "a9b00000000000001"

    def setUp(self):
        super().setUp()
        self.saved_projects = jd.PROJECTS
        jd.PROJECTS = Path(os.path.realpath(str(self.td))) / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        cwd = os.path.realpath(str(self.td / "notes-api"))
        os.makedirs(cwd, exist_ok=True)
        self.proj = jd._proj_dir(cwd)
        self.proj.mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names").mkdir(parents=True, exist_ok=True)
        (jd.STATE / "names" / SID).write_text("web\t" + cwd)
        self.leaf = str(self.proj / (SID + ".jsonl"))
        self.sub = self.proj / SID / "subagents"
        for c in (km._parse_cache, km._SUBAGENT_FILE_CACHE, km._SUBAGENT_META_CACHE, km._SUBAGENT_FRAMES):
            c.clear()
        p = mock.patch.object(km, "_live_map", lambda: {})
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        super().tearDown()
        jd.PROJECTS = self.saved_projects
        jd._discover_cache.clear()

    def agent_records(self, t):
        """A prompt, a background shell command, its finished notification, then five tool calls: t-1 .. t+14."""
        launch = rec(t + 1, 1, launch=True)
        note = {"type": "user", "timestamp": iso(t + 9), "uuid": "n-%d" % t, "sessionId": SID,
                "message": {"role": "user", "content": (
                    "<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>%s</tool-use-id>\n"
                    "<output-file>/w/notes-api/.tasks/b1.output</output-file>\n<status>completed</status>\n"
                    "<summary>Background command \"make test\" completed</summary>\n</task-notification>"
                    % launch["message"]["content"][0]["id"])}}
        prompt = {"type": "user", "timestamp": iso(t - 1), "uuid": "p-%d" % t, "sessionId": SID,
                  "message": {"role": "user", "content": "Run the notes-api tests in the background and report."}}
        return chain([prompt, rec(t, 0), launch, note] + [rec(t + 10 + i, 10 + i) for i in range(5)])

    def test_a_closed_viewer_lets_go_of_the_background_task_cursor_its_render_left(self):
        write(self.leaf, [rec(T0 - 200, 0)], int(time.time()))   # the parent transcript (discovery reads its real mtime)
        ap = str(self.sub / ("agent-%s.jsonl" % self.AID))
        recs = self.agent_records(T0 - 60)
        write(ap, recs, T0 - 46)
        self.assertEqual(str(km._subagent_file(self.leaf, self.AID)), ap)
        km._agent_launch_ids(ap)                                  # the card's fold while the agent runs
        fr = km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it
        self.assertNotIn("error", fr)
        self.assertIn(ap, km._bgall_cache, "the notification's join folded the agent file's background tasks")
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)   # the agent writes on
        client = {"app": "chat", "wid": "w1", "alive": True, "sent": {}, "send": lambda s: None}
        km.Handler._dispatch_ws(object.__new__(km.Handler), {"type": "closeSubagent", "id": SID, "agentId": self.AID}, client)
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records while the file is fresh
        self.clock.t = T0 + 300                                   # the agent returned
        km._agent_launch_ids(ap)                                  # a quiet fold
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "the closed viewer's cursor no longer holds the returned agent's drop")
        self.assertNotIn(ap, km._bgall_cache)
        self.assertIn("_subagent_viewer_closed(*_sv)", inspect.getsource(km.Handler._ws), "a socket that goes away closes its viewers")


class SpendGuard(Base):
    def setUp(self):
        super().setUp()
        write(self.leaf, [rec(T0 - 5000, 0)], T0 - 5000)        # the leaf: nothing inside the window

    def window(self, t):
        self.clock.t = t
        return km._spend_window_usd(self.leaf, t, 600, PRICES)

    def test_a_workflow_agent_only_the_guard_reads_leaves_memory_and_its_window_holds(self):
        wf = str(self.sub / "workflows" / "wf_1" / "agent-a5555555555555555.jsonl")
        recs = [rec(T0 - 100 + 5 * i, i) for i in range(10)]     # T0-100 .. T0-55
        write(wf, recs, T0 - 55)
        want = sum(map(usd, recs))
        self.assertAlmostEqual(self.window(T0), want, places=9)
        self.assertTrue(em.entry_whole_resident(wf), "fresh: the guard's read keeps it")
        self.assertAlmostEqual(self.window(T0 + 120), want, places=9, msg="175 s quiet, still inside the window")
        self.assertIsNone(self.entry(wf), "returned: the guard's own read let its records go")
        self.assertIn("state", self.doc(wf)["folds"]["agentLaunchIds"], "through the dropping fold, which wrote its document")
        self.assertNotIn(wf, km._AGENT_GIST_CACHE, "the guard never starts a steps state for a workflow agent")
        self.assertNotIn("agentGist", self.doc(wf)["folds"], "nor writes one")
        r0 = self.read_of(wf)
        self.assertAlmostEqual(self.window(T0 + 130), want, places=9, msg="priced from the memo")
        self.assertIsNone(self.entry(wf), "and not entered again while it stays in the window")
        self.assertEqual(self.read_of(wf), r0, "nothing read")
        self.assertEqual(self.window(T0 + 600), 0.0, "aged out of the window")

    def test_the_row_memo_serves_a_tail_restore_of_the_same_bytes(self):
        ap = str(self.sub / "agent-a6666666666666666.jsonl")
        recs = [rec(T0 - 100 + 5 * i, i) for i in range(10)]
        write(ap, recs, T0 - 55)
        want = sum(map(usd, recs))
        self.assertAlmostEqual(sum(c for _t, c in km._spend_file_rows(ap, T0 - 600, PRICES, None)), want, places=9)
        em.fold_records({}, ap, list, lambda st, o: st, ckpt="synthSpend")   # a cursor, so the document has a fold to carry
        self.assertTrue(em.checkpoint_write(ap))
        em._pop_owed_entry(ap)                                     # the drop: the records leave memory
        ent = em._read_jsonl_entry(ap, tail_ok=True)              # the next reader's entry: a witness tail of the same bytes
        self.assertEqual((ent[5], len(ent[4])), (10, 0))
        self.assertAlmostEqual(sum(c for _t, c in km._spend_file_rows(ap, T0 - 590, PRICES, None)), want, places=9,
                               msg="the memo saw every record of these very bytes; the tail holds none of them")

    def test_a_dropped_agent_that_resumes_keeps_its_earlier_rows_in_the_window(self):
        ap = str(self.sub / "agent-a7777777777777777.jsonl")
        early = [bare(rec(T0 - 400 + 5 * i, i)) for i in range(10)]   # T0-400 .. T0-355, no message ids: nothing dedupes a
        write(ap, early, T0 - 355)                                     #  record counted twice
        self.assertAlmostEqual(self.window(T0), in_window(early, T0), places=9)
        self.assertIsNone(self.entry(ap), "quiet for six minutes at the guard's first look: dropped as it was folded")
        whole0 = self.guard_whole_reads()                         # the first look read the leaf and the agent from zero
        size0, r0 = os.path.getsize(ap), self.read_of(ap)
        late = [bare(rec(T0 + 5 + i, 100 + i)) for i in range(3)]
        append(ap, late[:2], T0 + 8)                              # the agent resumes (a live agent's long silence ends the same way)
        self.assertAlmostEqual(self.window(T0 + 10), in_window(early + late[:2], T0 + 10), places=9,
                               msg="the earlier rows stay in the window: the memo's rows beside the tail's")
        self.assertEqual(self.entry(ap)[5], 10, "the reader holds the tail past the drop's cut")
        append(ap, late[2:], T0 + 13)                             # and writes on: the memo now ends inside the tail
        self.assertAlmostEqual(self.window(T0 + 15), in_window(early + late, T0 + 15), places=9,
                               msg="only the record past the memo's end is priced: none counted twice")
        self.assertEqual(self.entry(ap)[5], 10, "still the tail")
        self.assertEqual(self.guard_whole_reads(), whole0, "the resume was priced from the tail, never read whole")
        self.assertLessEqual(self.read_of(ap) - r0, os.path.getsize(ap) - size0 + 4 * em._JSONL_TAIL_GUARD,
                             "the appended bytes and the guard bytes of two reads")
        self.assertAlmostEqual(self.window(T0 + 200), in_window(early + late, T0 + 200), places=9, msg="quiet again")
        self.assertIsNone(self.entry(ap), "quiet again: dropped again")
        r1 = self.read_of(ap)
        self.assertAlmostEqual(self.window(T0 + 210), in_window(early + late, T0 + 210), places=9, msg="priced from the memo")
        self.assertEqual(self.read_of(ap), r1, "and not entered again")
        self.assertEqual(self.guard_whole_reads(), whole0)

    def resumed(self, name, early, late, mid=()):
        """An agent file whose `early` records the guard priced at T0 while fresh (its memo from the whole file); `mid` appended
        at T0 + 5, after that look; a card's launch-ids fold over the quiet file drops it at T0 + 300; the agent then writes
        `late` at T0 + 308. Returns the path."""
        ap = str(self.sub / name)
        write(ap, early, T0 - 11)
        self.window(T0)
        self.assertIsNotNone(self.entry(ap), "fresh at the guard's look: kept")
        if mid:
            append(ap, list(mid), T0 + 5)
        self.clock.t = T0 + 300
        km._agent_launch_ids(ap)
        self.assertIsNone(self.entry(ap), "the quiet card fold dropped it")
        append(ap, late, T0 + 308)
        return ap

    def test_a_memo_that_ends_before_the_tail_begins_reads_the_file_whole(self):
        early = [rec(T0 - 60 + 5 * i, i) for i in range(10)]     # T0-60 .. T0-15
        mid = [rec(T0 + 1 + i, 10 + i) for i in range(5)]
        late = [rec(T0 + 305 + i, 20 + i) for i in range(3)]
        self.resumed("agent-a9c00000000000001.jsonl", early, late, mid=mid)
        self.assertAlmostEqual(self.window(T0 + 310), sum(map(usd, early + mid + late)), places=9,
                               msg="the memo ends where the middle records begin, not where the tail does: the whole file")
        self.assertEqual(self.guard_whole_reads().get("upgrade<-_spend_file_rows"), 1)

    def test_a_window_reaching_past_the_memos_floor_reads_the_file_whole(self):
        ap = self.resumed("agent-a9c00000000000002.jsonl", [rec(T0 - 900 + 30 * i, i) for i in range(30)], [rec(T0 + 305, 50)])
        recs = [json.loads(ln) for ln in open(ap)]
        since = T0 + 310 - 1100                                   # further back than the memo's floor (T0 - 660)
        self.clock.t = T0 + 310
        self.assertAlmostEqual(sum(c for _t, c in km._spend_file_rows(ap, since, PRICES, None, agent=True)),
                               sum(usd(r) for r in recs if km._msg_epoch(r) >= since), places=9,
                               msg="rows older than the memo's floor are in this window: the whole file")
        self.assertEqual(self.guard_whole_reads().get("upgrade<-_spend_file_rows"), 1)

    def test_a_response_logged_on_both_sides_of_a_drop_counts_once_at_its_largest_usage(self):
        ap = str(self.sub / "agent-a9d00000000000001.jsonl")
        early = [rec(T0 - 400 + 5 * i, i) for i in range(10)]
        early[-1]["message"]["id"] = "msg-split"                 # a response's first content block, before the drop
        write(ap, early, T0 - 355)
        self.assertAlmostEqual(self.window(T0), sum(map(usd, early)), places=9)
        self.assertIsNone(self.entry(ap))
        whole0 = self.guard_whole_reads()
        split = rec(T0 + 5, 100)
        split["message"]["id"] = "msg-split"                     # its later block, past the cut, with the larger usage
        split["message"]["usage"]["output_tokens"] = 3000
        late = [split, rec(T0 + 6, 101)]
        append(ap, late, T0 + 8)
        self.assertAlmostEqual(self.window(T0 + 10), sum(map(usd, early[:-1] + late)), places=9,
                               msg="one response, one row, at its largest usage, as a scan of the whole file counts it")
        self.assertEqual(self.guard_whole_reads(), whole0, "priced from the tail")

    def test_the_guard_leaves_an_agent_file_a_viewers_parse_holds_alone_until_the_parse_is_gone(self):
        ap = str(self.sub / "agent-a9e00000000000001.jsonl")
        recs = [rec(T0 - 300 + 5 * i, i) for i in range(20)]
        write(ap, recs, T0 - 205)                                 # returned 205 s ago, first seen by this process (a restart)

        def parse():                                              # the subagent viewer's parse of the agent's own file
            em.parse_session(ap, rompuuid=SID, name="api", dir="/w/notes-api", candidate_files=[ap], states=None,
                             postal_log=[], now=T0)
        parse()
        ent = self.entry(ap)
        with em._ASM_LOCK:
            held = [e for e in em._ASM_CACHE.values() if getattr(e.get("ad"), "_src", {}).get(ap) is ent[4]]
        self.assertEqual(len(held), 1, "the parse holds the reader's very list")
        self.assertAlmostEqual(self.window(T0), sum(map(usd, recs)), places=9)
        self.assertIs(self.entry(ap), ent, "the guard's fold would drop records the parse still holds, freeing nothing")
        self.assertNotIn(ap, km._AGENT_LAUNCH_IDS_CACHE, "no fold ran")
        r0 = self.read_of(ap)
        parse()                                                   # the viewer rebuilds
        self.assertEqual(self.read_of(ap), r0, "and reads nothing")
        with em._ASM_LOCK:
            gone = list(em._ASM_CACHE.values())
            em._ASM_CACHE.clear()
        for e in gone:
            em._asm_release(e)
        self.assertAlmostEqual(self.window(T0 + 5), sum(map(usd, recs)), places=9)
        self.assertIsNone(self.entry(ap), "the parse gone, the guard's next look folds the file and drops it")

    def test_a_fresh_process_prices_an_agent_file_it_restored_as_a_tail(self):
        ap = str(self.sub / "agent-a8888888888888888.jsonl")
        recs = [rec(T0 - 100 + 5 * i, i) for i in range(10)]     # T0-100 .. T0-55
        write(ap, recs, T0 - 55)
        self.clock.t = T0 - 50
        km._agent_steps(ap)                                       # the card's fold while the agent ran
        self.assertTrue(em.checkpoint_write(ap))                  # the exit drain's document, at the file's end
        self.fresh_process()                                      # a restart: the memo and the entry gone, the document stands
        want = sum(map(usd, recs))
        self.assertAlmostEqual(self.window(T0), want, places=9,
                               msg="the restored tail holds none of the window's records: the guard reads the file whole")
        self.assertEqual(self.guard_whole_reads().get("upgrade<-_spend_file_rows"), 1, "once, after the boot")
        ent = self.entry(ap)
        self.assertEqual((ent[5], len(ent[4])), (0, 10), "written to 55 s ago: held whole until it has been quiet for two minutes")
        for t in (T0 + 30, T0 + 60):                              # 85 and 115 s quiet
            self.assertAlmostEqual(self.window(t), want, places=9)
            self.assertEqual(len(self.entry(ap)[4]), 10, "still held at +%d s" % (t - T0))
        self.assertAlmostEqual(self.window(T0 + 70), want, places=9)
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "125 s quiet: the guard's fold let the records go")
        r0 = self.read_of(ap)
        self.assertAlmostEqual(self.window(T0 + 80), want, places=9, msg="priced from the memo")
        self.assertEqual(self.read_of(ap), r0, "and not entered again")
        self.assertEqual(self.guard_whole_reads().get("upgrade<-_spend_file_rows"), 1, "no whole read after the first")

    def test_a_restored_tail_that_covers_the_window_is_priced_from_it_and_folded_out_of_memory(self):
        wf = str(self.sub / "workflows" / "wf_1" / "agent-a9a00000000000001.jsonl")
        pairs = [(rec(T0 - 1000 + 10 * i, i), T0 - 1000 + 10 * i) for i in range(100)]   # T0-1000 .. T0-10
        write(wf, [r for r, _t in pairs[:5]], T0 - 960)
        self.clock.t = T0 - 955
        km._agent_launch_ids(wf)                                  # the previous process: the guard's fold while the agent ran
        self.assertTrue(em.checkpoint_write(wf))                  # the converge pass's write at that first fold (count 5)
        append(wf, [r for r, _t in pairs[5:]], T0 - 10)
        self.fresh_process()                                      # a restart whose exit drain did not refresh this document
        size = os.path.getsize(wf)
        for t in range(T0, T0 + 901, 5):
            want = sum(usd(r) for r, rt in pairs if rt >= t - 600)
            self.assertAlmostEqual(self.window(t), want, places=9, msg="the window at +%d s" % (t - T0))
            if t == T0:
                ent = self.entry(wf)
                self.assertEqual((ent[5], len(ent[4])), (5, 95), "priced from the restored tail: it reaches past the window's floor")
                self.assertLess(self.read_of(wf), size, "and not read whole")
        ent = self.entry(wf)
        self.assertTrue(ent is None or not ent[4], "the tail's records left memory through the guard's fold: %r" % ((ent or ())[5:6],))
        self.assertGreaterEqual(em.record_cache_stats()["dropped"], 1)
        self.assertLess(self.read_of(wf), size, "the file was never read whole")

    def test_the_row_memo_rescans_when_its_tail_entry_becomes_the_whole_file(self):
        f = str(self.proj / "11111111-2222-4333-8444-0000000000e1.jsonl")
        recs = [rec(T0 - 300 + 5 * i, i) for i in range(10)]     # T0-300 .. T0-255: every one inside the window
        write(f, recs[:5], T0 - 280)
        em.fold_records({}, f, list, lambda st, o: st, ckpt="synthSpend")   # a cursor, so the document has a fold to carry
        self.assertTrue(em.checkpoint_write(f))                   # the document's cut at record 5
        append(f, recs[5:], T0 - 255)
        self.fresh_process()                                      # a restart: the reader restores the records past the cut
        since = T0 - 600
        self.assertAlmostEqual(sum(c for _t, c in km._spend_file_rows(f, since, PRICES, None)), sum(map(usd, recs[5:])), places=9,
                               msg="the memo is built from the tail the reader holds")
        self.assertEqual(self.entry(f)[5], 5)
        em._read_jsonl_entry(f)                                   # a whole reader upgrades the entry: the same mtime and size
        self.assertEqual(self.entry(f)[5], 0)
        self.assertAlmostEqual(sum(c for _t, c in km._spend_file_rows(f, since, PRICES, None)), sum(map(usd, recs)), places=9,
                               msg="a base that went down re-scans: the records before the cut join the window")


class Lab(Base):
    """A synthetic session on an advancing clock, five seconds a cycle. Twenty-four agents start a minute apart and each
    writes a record a cycle for 90 s; half are Agent cards, half workflow agents (no card: the spend guard is their only
    reader). The guard runs every cycle over the session's tree, as the pusher does, and its total must equal a reference sum
    at every cycle. How each card's two folds run is the variable (`cards`):
      lockstep   both, every cycle, until five minutes after the agent returns;
      kernel     the kernel's own pattern: the steps fold over every card while any card agent writes (the parent's chat
                 rebuild), the launch-ids fold only while the agent runs and not over its last two records (the awaiting
                 box's attribution, which reads live agents only);
      tabclosed  the steps fold only in the agent's first 30 s (the chat tab closed, or the laptop asleep, before the agent
                 returned), the launch-ids fold while the agent runs.
    With `silences` each agent writes in three bursts of 45 s with two silences of 155 s between them (a long tool call, a
    long response): a live agent quiet for over two minutes counts as returned and is dropped, and its next record resumes
    it."""

    CYCLE = 5

    def snapshot(self, t, agents):
        """The record cache's agent entries at `t`, and the bound on those holding records: the agents written to in the two
        minutes (and a cycle) before `t`, counted from the records written by then. `outside` counts the agents holding
        records that are not among them (a finished agent held while a recent one was let go passes the count, not this)."""
        with em._JSONL_CACHE_LOCK:
            ents = {p: e for p, e in em._JSONL_CACHE.items() if p != self.leaf}
        wf = {a["path"] for a in agents if a["kind"] == "workflow"}
        card = {a["path"] for a in agents if a["kind"] == "task"}
        recent = {a["path"] for a in agents if a["recs"] and t - a["recs"][-1][1] < 120 + self.CYCLE}
        return {"t": int(t - T0), "spawned": sum(1 for a in agents if a["start"] <= t),
                "writing": sum(1 for a in agents if self.writes(a, t)),
                "rcEntries": len(ents), "withRecords": sum(1 for e in ents.values() if e[4]),
                "cardWithRecords": sum(1 for p, e in ents.items() if p in card and e[4]),
                "bytes": sum(em._entry_weight(e) for e in ents.values()),
                "workflowEntries": sum(1 for p in ents if p in wf), "dropped": em._RECORD_CACHE_STATS["dropped"],
                "bound": len(recent), "outside": len({p for p, e in ents.items() if e[4]} - recent)}

    def test_finished_agents_leave_the_record_cache(self):
        self.assert_drained(*self.run_lab("lockstep"))

    def test_the_guard_catches_up_the_card_cursors_the_kernels_own_folds_leave_behind(self):
        self.assert_drained(*self.run_lab("kernel"))

    def test_the_guard_catches_up_a_card_whose_chat_stopped_being_built_before_it_returned(self):
        self.assert_drained(*self.run_lab("tabclosed"))

    def test_a_live_agents_long_silences_drop_it_and_each_resume_is_priced_from_the_tail(self):
        agents, series = self.run_lab("kernel", silences=True)
        self.assert_drained(agents, series)
        self.assertGreaterEqual(series[-1]["dropped"], 3 * len(agents), "each agent left memory in both silences and at its end")
        self.assertEqual(self.guard_whole_reads().get("upgrade<-_spend_file_rows"), None,
                         "no resume read its file whole for the guard: the memo's rows and the tail's priced it")
        for a in agents:
            self.assertLess(self.read_of(a["path"]), 1.25 * os.path.getsize(a["path"]), "each file read about once: %s" % a["path"])

    def test_without_the_guard_a_card_cursor_left_behind_keeps_its_agent_resident(self):
        # The dependency the change names: a card's launch-ids fold stops before its agent's last records, and with the guard
        # off (a ceiling of 0, or a session no longer alive) nothing catches it up, so every card agent stays as on main. A
        # stopped cursor that no longer holds the drop would change this, and that is the owner's call.
        agents, series = self.run_lab("kernel", guard=False)
        final = series[-1]
        self.assertEqual((final["cardWithRecords"], final["workflowEntries"], final["dropped"]), (12, 0, 0), final)

    def assert_drained(self, agents, series):
        final = series[-1]
        for s in series:                                          # only the agents written to in the two minutes before hold records
            self.assertEqual(s["outside"], 0, "at +%d s an agent not written to in the two minutes and a cycle before holds records: %s"
                             % (s["t"], s))
            self.assertLessEqual(s["withRecords"], s["bound"], "at +%d s: %s" % (s["t"], s))
        self.assertEqual((final["withRecords"], final["bytes"]), (0, 0), "every returned agent's records left memory: %s" % final)
        self.assertEqual(final["workflowEntries"], 0, "the agents only the guard reads leave no entry at all")
        self.assertGreaterEqual(final["dropped"], len(agents))
        for a in agents:
            self.assertLess(self.read_of(a["path"]), 2 * os.path.getsize(a["path"]), "no agent file read whole twice: %s" % a["path"])

    @staticmethod
    def writes(a, t):
        return any(s <= t < e for s, e in a["bursts"])

    def run_lab(self, cards, guard=True, silences=False):
        agents = []
        for i in range(24):
            kind = "task" if i % 2 == 0 else "workflow"
            aid = "a%016x" % (0xd0000 + i)
            p = (self.sub / ("agent-%s.jsonl" % aid) if kind == "task"
                 else self.sub / "workflows" / ("wf_%d" % i) / ("agent-%s.jsonl" % aid))
            s = T0 + 60 * i
            bursts = [(s, s + 45), (s + 195, s + 240), (s + 390, s + 435)] if silences else [(s, s + 90)]
            agents.append({"path": str(p), "kind": kind, "start": s, "end": bursts[-1][1], "bursts": bursts, "recs": []})
        t_end = agents[-1]["end"] + 900
        series, t = [], T0
        while t <= t_end:
            self.clock.t = t
            em.checkpoint_cycle_begin(64 * 1024 * 1024)          # the pusher begins each cycle's checkpoint budget
            card_writing = False
            for a in agents:
                if self.writes(a, t):
                    r = (rec(t, len(a["recs"])), t)
                    if a["recs"]:
                        append(a["path"], [r[0]], t)
                    else:
                        write(a["path"], [r[0]], t)
                        d = Path(a["path"]).parent                # a new file moves its directory (and a new directory its
                        while True:                               #  parent): stamped on the simulated clock, so the guard's
                            os.utime(d, (t, t))                   #  tree memo sees each one on a coarse-clock filesystem too
                            if d == self.sub:
                                break
                            d = d.parent
                    a["recs"].append(r)
                    card_writing = card_writing or a["kind"] == "task"
            for a in agents:
                if a["kind"] != "task" or not a["recs"]:
                    continue
                if cards == "lockstep":
                    steps = ids = t <= a["end"] + 300
                elif cards == "kernel":
                    steps, ids = card_writing, a["start"] <= t < a["end"] - 2 * self.CYCLE
                else:
                    steps, ids = t < a["start"] + 30, a["start"] <= t < a["end"]
                if steps:
                    km._agent_steps(a["path"])
                if ids:
                    km._agent_launch_ids(a["path"])
            if guard:
                got = km._spend_window_usd(self.leaf, t, 600, PRICES)
                want = sum(usd(r) for a in agents for r, rt in a["recs"] if rt >= t - 600)
                self.assertAlmostEqual(got, want, places=9, msg="the guard's window at +%d s" % (t - T0))
            if (t - T0) % 300 == 0 or t == t_end:
                series.append(self.snapshot(t, agents))
            t += self.CYCLE
        tag = "%s%s%s" % (cards, "" if guard else " guardOff", " silences" if silences else "")
        print("LAB %s %s" % (tag, json.dumps(series, separators=(",", ":"))))
        print("LAB %s final=%s agentBytesOnDisk=%d maxReadRatio=%.3f guardWholeReads=%s" % (
            tag, json.dumps(series[-1]), sum(os.path.getsize(a["path"]) for a in agents),
            max(self.read_of(a["path"]) / float(os.path.getsize(a["path"])) for a in agents),
            json.dumps(self.guard_whole_reads(), sort_keys=True)))
        return agents, series


if __name__ == "__main__":
    unittest.main()
