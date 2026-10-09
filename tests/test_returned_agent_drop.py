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
    a dirty path while the drop write is off, or a parse holding the very records list; a parse built before the file's
    last append is a whole assembly entry, which the assembly pin keeps until the parse is released; over the cycle's
    budget the drop waits unowed, and under the converge hold it is paid without its pop; a cursor of another generation
    that the document does not carry does not hold it;
  * the kernel's agent-file folds take the new policy and the parent-leaf fold does not;
  * closing the subagent viewer pops the background-task cursor its render left on an agent file with a task notification
    and a nested background launch, and retires it from a document written while the viewer was open, so the agent still
    drops when it returns: a close through closeSubagent or the socket going away (which releases the viewer's parse too),
    a close that lands while the pusher renders the viewer (a render that raises included) or whose socket goes away then,
    the last of two clients' closes (the first keeps the cursor), a close in a new process before any render (the state a
    viewer open at the exit drain left, read from the document on disk) and a close inside a document write, before any
    document exists; a close with nothing to retire leaves the path clean;
  * the spend guard prices agent files from a read of its own, then runs the dropping folds where they step records the
    reader holds (never starting a steps state for a workflow agent, never folding while a viewer's parse holds the
    records, stepping the viewer's background-task state a restart left behind), prices a dropped file from its memo without entering it
    again, prices a resumed agent from its memo and the tail without a whole read (a response split across the cut counted
    once, whichever side logged the larger usage), reads the file whole when the memo ends short of the tail or starts
    after the window, prices a file a fresh process restored as a tail (one whole read after the boot, none later, the
    drop after two quiet minutes), prices a restored tail that covers the window without a whole read and folds it out of
    memory, counts that tail's document corrupt once, with no raise, when a fold's count in it is no int (infinity, NaN, a
    string, null) or missing, and lets the tail go with it, so the file is read whole once and dropped, for a workflow agent
    and for a card's subagent whose chat is no longer built, whether the launch-ids count or the viewer's background-task
    count was bad (a tail of the generation the gate looked at, a longer one an append put in its place included; a whole
    entry or one of another generation another read put there stays at that pass, and what such a tail does after it,
    without its saved state, is described at the gate in kernel/event_model.py and not pinned here; and a whole entry a
    viewer's parse read, its steps count bad, stays through the fallback and leaves at a later quiet drop, its document
    written), re-scans its memo when a tail entry becomes the whole file, and pays no whole read for a half-written
    record;
    a raise in the drop's witness check or the guard's folds (its launch-ids fold included) is counted and said with the
    file and the frame, at the 1st, 10th, 100th and 1000th raise, and a guard fold that raises at every pass does not keep
    the folds after it from dropping a returned agent;
  * a lab-style run of a synthetic session (agents with cards, workflow agents without) on an advancing clock, the cards'
    folds run in lockstep, in the kernel's own pattern, or stopping before the agent returns: the entries holding records
    and their bytes return to the live set as agents return, the workflow agents' entries stop climbing, and the guard's
    window equals a reference sum at every cycle; agents that fall silent for over two minutes mid-run are dropped and
    resumed with no whole read; without the guard, a card cursor left behind keeps its agent resident.
SYNTHETIC fixtures only: placeholder ids, the notes-api demo names, transcripts written here."""
import inspect
import io
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
        k0 = em.record_cache_stats().get("pinnedKept", 0)
        self.fold(cache, p)                                       # quiet: the document is written, the entry stays
        self.assertIs(self.entry(p), ent, "a pop would free nothing the parse holds")
        r0 = self.read_of(p)
        for _ in range(3):
            parse()                                               # the viewer rebuilt (its running flag flipped, say)
            self.fold(cache, p)
            self.assertIs(self.entry(p), ent)
        self.assertEqual(self.read_of(p), r0, "no rebuild read the file whole again")
        self.assertEqual(em.record_cache_stats().get("pinnedKept", 0) - k0, 0,
                         "the witness's identity test held each quiet drop, never reaching the assembly pin (round six)")
        with em._ASM_LOCK:
            gone = list(em._ASM_CACHE.values())
            em._ASM_CACHE.clear()
        for e in gone:
            em._asm_release(e)
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "the parse gone, the next quiet fold drops")
        self.assertEqual(em.record_cache_stats().get("pinnedKept", 0) - k0, 0, "and no pass counted a pin")

    def test_a_parse_built_before_the_last_append_is_a_whole_entry_the_pin_keeps_until_it_is_released(self):
        """The viewer's parse of a returned agent's file, built before the agent's last records were read, holds an older
        records list at the same generation. The witness's identity test passes it, and the assembly pin keeps the entry
        (a whole assembly entry whose next parse would be whole again) until the parse is released, as the viewer's close
        releases it (round six of the fold's review)."""
        p = self.sub / "agent-a9600000000000016.jsonl"
        write(p, [rec(T0 - 30 + i, i) for i in range(20)], T0 - 20)
        cache = {}
        self.assertEqual(len(self.fold(cache, p)), 20)            # the card's fold reads the twenty while the file is fresh
        em.parse_session(str(p), rompuuid=SID, name="api", dir="/w/notes-api", candidate_files=[str(p)], states=None,
                         postal_log=[], now=T0)                   # the viewer's parse: a whole assembly entry over them
        append(p, [rec(T0 - 9 + i, 20 + i) for i in range(3)], T0 - 7)   # the agent writes on
        kinds = []
        self.assertEqual(len(self.fold(cache, p, kinds=kinds)), 23)
        self.assertEqual(kinds[-1], "append", "three more read while the file is fresh")
        ent = self.entry(p)
        with em._ASM_LOCK:
            srcs = [getattr(e.get("ad"), "_src", {}).get(str(p)) for e in em._ASM_CACHE.values()]
        srcs = [s for s in srcs if s is not None]
        self.assertEqual([len(s) for s in srcs], [20], "the premise: one parse, over the twenty records it read")
        self.assertIsNot(srcs[0], ent[4], "an older list than the reader's, so the witness's identity test passes it")
        self.clock.t = T0 + 300                                   # the agent returned
        st0 = dict(em.record_cache_stats())
        self.fold(cache, p)                                       # a quiet pass: the witness approves, the pin keeps
        st1 = em.record_cache_stats()
        self.assertIs(self.entry(p), ent, "the whole assembly entry folds over this generation: the pin keeps the records")
        self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (1, 0),
                         "counted as kept for the pin, not as a drop")
        self.assertEqual(em.release_assembly(str(p), rompuuid=SID), 1, "the viewer's close releases the parse")
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "the parse released, the next quiet pass drops")
        st2 = em.record_cache_stats()
        self.assertEqual((st2.get("pinnedKept", 0) - st1.get("pinnedKept", 0), st2["dropped"] - st1["dropped"]), (0, 1))

    def test_a_raise_in_the_witness_check_holds_the_drop_and_is_counted_and_said(self):
        p, cache, ent = self.returned("agent-a9800000000000008.jsonl")
        em._SWALLOWED.clear()
        err = io.StringIO()
        with mock.patch.object(em, "_asm_holds_records", side_effect=RuntimeError("synthetic witness fault")), \
             mock.patch("sys.stderr", new=err):
            self.fold(cache, p)
            self.fold(cache, p)
        self.assertIs(self.entry(p), ent, "the raise holds the drop: memory, never a wrong answer")
        self.assertEqual(em.record_cache_stats()["swallowed"], {"dropWitness:RuntimeError": 2}, "counted on /perf")
        self.assertEqual(err.getvalue().count("dropWitness raised RuntimeError"), 1, "said once, at the first raise")
        self.assertIn(" on %s: synthetic witness fault" % p, err.getvalue(), "the line names the file")
        self.assertIn("in _document_at_witness", err.getvalue(), "and the frame that raised")
        self.fold(cache, p)
        self.assertIsNone(self.entry(p), "the fault gone, the next quiet fold drops")

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
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "the steps fold's own quiet fold let the records go")
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
    background launch), and no card reader steps that cursor. The spend guard, where it passes the file (a live session, a
    ceiling above 0, the file in the window, no parse keeping the records), steps it where it stands at the entry's
    generation or where the file's document restores it after a restart, and a close that lands inside the guard's step can
    leave the cursor that step stores, which the guard then keeps current. Where the guard does not pass the file, once the
    viewer closes and the agent writes on, a cursor behind the entry would hold the returned agent's quiet drop for good.
    Closing the viewer pops it, and retires it from a document written while the viewer was open, or left by a viewer open
    at the exit drain."""

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

    @staticmethod
    def user(t, uuid, content, **extra):
        return dict({"type": "user", "timestamp": iso(t), "uuid": uuid, "sessionId": SID,
                     "message": {"role": "user", "content": content}}, **extra)

    def agent_records(self, t):
        """A prompt, a background shell command, a nested background agent and its launch ack, the command's finished
        notification, then five tool calls: t-1 .. t+14."""
        launch = rec(t + 1, 1, launch=True)
        nested = rec(t + 2, 2)
        nested["message"]["content"] = [{"type": "tool_use", "id": "toolu_nested_1", "name": "Agent", "input": {
            "description": "lint the api", "prompt": "Run the linter over api/.", "subagent_type": "general-purpose",
            "run_in_background": True}}]
        ack = self.user(t + 3, "k-%d" % t, [{"type": "tool_result", "tool_use_id": "toolu_nested_1",
                                             "content": "Async agent launched. Agent ID: a9b00000000000002."}],
                        toolUseResult={"isAsync": True, "status": "async_launched", "agentId": "a9b00000000000002",
                                       "description": "lint the api"})
        note = self.user(t + 9, "n-%d" % t, (
            "<task-notification>\n<task-id>b1</task-id>\n<tool-use-id>%s</tool-use-id>\n"
            "<output-file>/w/notes-api/.tasks/b1.output</output-file>\n<status>completed</status>\n"
            "<summary>Background command \"make test\" completed</summary>\n</task-notification>"
            % launch["message"]["content"][0]["id"]))
        prompt = self.user(t - 1, "p-%d" % t, "Run the notes-api tests in the background and report.")
        return chain([prompt, rec(t, 0), launch, nested, ack, note] + [rec(t + 10 + i, 10 + i) for i in range(5)])

    def test_a_closed_viewer_lets_go_of_the_background_task_cursor_its_render_left(self):
        ask = self.user(T0 - 201, "w-1", "check the api tests with a subagent")
        reply = dict(rec(T0 - 200, 0), parentUuid="w-1")
        write(self.leaf, [ask, reply], int(time.time()))         # the parent transcript (discovery reads its real mtime)
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

    def test_a_viewer_closed_while_the_pusher_renders_it_lets_go_of_the_cursor_that_render_left(self):
        """The close runs on the handler thread and the pusher's re-push renders on its own: a close that lands during the
        render pops nothing (the render has not folded the file yet), so the pusher, finding the viewer gone after its
        render, pops the cursor that render left and sends nothing."""
        ask = self.user(T0 - 201, "w-1", "check the api tests with a subagent")
        reply = dict(rec(T0 - 200, 0), parentUuid="w-1")
        write(self.leaf, [ask, reply], int(time.time()))         # the parent transcript (discovery reads its real mtime)
        ap = str(self.sub / ("agent-%s.jsonl" % self.AID))
        recs = self.agent_records(T0 - 60)
        write(ap, recs, T0 - 46)
        km._agent_launch_ids(ap)                                  # the card's fold while the agent runs
        client = {"app": "chat", "wid": "w1", "alive": True, "sent": {}, "send": lambda s: None,
                  "subagents": {(SID, self.AID): True}}          # the viewer is open on this client
        real, left, sent = km.build_subagent, [], []

        def closing_build(sid, agent_id, now, live_map=None):
            km.Handler._dispatch_ws(object.__new__(km.Handler), {"type": "closeSubagent", "id": sid, "agentId": agent_id}, client)
            fr = real(sid, agent_id, now, live_map)
            left.append(ap in km._bgall_cache)
            return fr

        with mock.patch.object(km, "build_subagent", closing_build), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, **kw: sent.append(key)):
            km._push_subagents([client], int(time.time()), {})
        self.assertEqual(left, [True], "the push rendered the viewer once, after the close, and the render left its cursor")
        self.assertNotIn(ap, km._bgall_cache, "the pusher let go of the cursor its render left for the closed viewer")
        self.assertEqual(sent, [], "a viewer closed during its render is not sent the frame")
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)   # the agent writes on
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records while the file is fresh
        self.clock.t = T0 + 300                                   # the agent returned
        km._agent_launch_ids(ap)                                  # a quiet fold
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "no cursor left by the closed viewer's render holds the returned agent's drop")

    def test_a_viewer_closed_while_a_render_that_raises_runs_lets_go_of_what_that_render_left(self):
        """As above, but the render raises after it folded the file's background tasks and parsed the file (logged as a
        failed frame). The pusher's re-check after a render runs on that path too: the cursor and the parse go, nothing is
        sent, and the returned agent drops (round six of the fold's review)."""
        ap, recs = self.scene()
        client = self.client()
        real, left, sent = km.build_subagent, [], []

        def closing_raising_build(sid, agent_id, now, live_map=None):
            self.close(client)                                    # the close lands while the render runs
            real(sid, agent_id, now, live_map)
            left.append((ap in km._bgall_cache, bool(self.asm_entries(ap))))
            raise RuntimeError("synthetic render fault")

        err = io.StringIO()
        with mock.patch.object(km, "build_subagent", closing_raising_build), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, **kw: sent.append(key)), \
                mock.patch("sys.stderr", new=err):
            km._push_subagents([client], int(time.time()), {})
        self.assertEqual(left, [(True, True)], "the premise: the render left its cursor and its parse before it raised")
        self.assertIn("subagent frame failed", err.getvalue(), "the raise is said, as before")
        self.assertNotIn(ap, km._bgall_cache, "the pusher let go of the cursor the raising render left for the closed viewer")
        self.assertEqual(self.asm_entries(ap), [], "and of the parse it rebuilt, which would pin the records")
        self.assertEqual(sent, [], "nothing is sent")
        self.returns_and_drops(ap, recs, "nothing the raising render left holds the returned agent's drop")

    def test_a_render_that_raises_for_an_open_viewer_sends_nothing_and_the_push_goes_on(self):
        """A render that raises while its viewer stays open: the frame was never built, so nothing is sent for that viewer
        and the push goes on to the next one (round seven of the fold's review: without the guard on the send, the push
        raised on the unbuilt frame and the cycle's catch skipped the feed, bars and timeline frames for every client)."""
        ap, recs = self.scene()
        first, second = self.client("w1"), self.client("w2")
        self.register(first, second)
        real, calls, sent = km.build_subagent, [], []

        def raising_once(sid, agent_id, now, live_map=None):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("synthetic render fault")
            return real(sid, agent_id, now, live_map)

        err = io.StringIO()
        with mock.patch.object(km, "build_subagent", raising_once), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, **kw: sent.append((c["wid"], key))), \
                mock.patch("sys.stderr", new=err):
            km._push_subagents([first, second], int(time.time()), {})   # returns: the raise is caught, never sent
        self.assertIn("subagent frame failed", err.getvalue(), "the raise is said")
        self.assertEqual([w for w, _key in sent], ["w2"], "nothing for the raising viewer; the other client's viewer is sent")
        self.assertIn((SID, self.AID), first["subagents"], "the open viewer stays registered")

    def test_a_good_render_then_a_raising_one_on_one_client_sends_only_the_good_viewer_its_own_frame(self):
        """One client with two viewers open, the second viewer's render raising after the first built: only the first
        viewer is sent, under its own key and with its own subagent's frame. A built flag reset once per push or once
        per client, or a frame bound before the loop and sent whenever it is set, sends the first frame again under the
        second viewer's key, filling the client's dedup slot for that viewer (round eight of the fold's review)."""
        self.scene()
        other = "a9b00000000000003"                               # a second subagent with its own file
        write(str(self.sub / ("agent-%s.jsonl" % other)), self.agent_records(T0 - 120), T0 - 106)
        client = self.client()
        client["subagents"][(SID, other)] = True                  # opened after the first, so rendered second
        real, sent = km.build_subagent, []

        def raising_for_other(sid, agent_id, now, live_map=None):
            if agent_id == other:
                raise RuntimeError("synthetic render fault")
            return real(sid, agent_id, now, live_map)

        err = io.StringIO()
        with mock.patch.object(km, "build_subagent", raising_for_other), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, **kw: sent.append((key, msg))), \
                mock.patch("sys.stderr", new=err):
            km._push_subagents([client], int(time.time()), {})
        self.assertIn("subagent frame failed", err.getvalue(), "the second render's raise is said")
        self.assertEqual([key for key, _msg in sent], [("subagent", SID, self.AID)], "only the good viewer is sent")
        self.assertEqual(sent[0][1].get("agentId"), self.AID, "with its own subagent's frame")
        self.assertIn((SID, other), client["subagents"], "the raising viewer stays registered")

    def asm_entries(self, ap):
        """The assembly entries over the agent file `ap` for the test's session (the viewer's parse)."""
        rp = os.path.realpath(ap)
        with em._ASM_LOCK:
            return [k for k in em._ASM_CACHE if k[0] == rp and k[1] == SID]

    def scene(self):
        """The parent transcript and the agent file (agent_records at T0 - 60, written at T0 - 46), with the card's fold run
        while the agent runs: (agent path, records)."""
        ask = self.user(T0 - 201, "w-1", "check the api tests with a subagent")
        reply = dict(rec(T0 - 200, 0), parentUuid="w-1")
        write(self.leaf, [ask, reply], int(time.time()))         # the parent transcript (discovery reads its real mtime)
        ap = str(self.sub / ("agent-%s.jsonl" % self.AID))
        recs = self.agent_records(T0 - 60)
        write(ap, recs, T0 - 46)
        km._agent_launch_ids(ap)                                  # the card's fold while the agent runs
        return ap, recs

    def client(self, wid="w1", viewer=True):
        c = {"app": "chat", "wid": wid, "alive": True, "sent": {}, "send": lambda s: None}
        if viewer:
            c["subagents"] = {(SID, self.AID): True}             # the viewer is open on this client
        return c

    def register(self, *clients):
        saved = list(km._clients)
        km._clients.extend(clients)
        self.addCleanup(lambda: (km._clients.clear(), km._clients.extend(saved)))

    def close(self, client):
        km.Handler._dispatch_ws(object.__new__(km.Handler), {"type": "closeSubagent", "id": SID, "agentId": self.AID}, client)

    def returns_and_drops(self, ap, recs, why):
        """The agent writes on while its file is fresh, the card's fold steps the new records, the agent returns, and a quiet
        fold must drop it."""
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)
        km._agent_launch_ids(ap)
        self.clock.t = T0 + 300
        km._agent_launch_ids(ap)
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], why)

    def test_a_viewer_closed_after_a_write_recorded_its_cursor_retires_it_from_the_document(self):
        """A document written while the viewer is open (here by a quiet pass of the card's fold; the guard's pass and the
        checkpoint write do the same) records the background-task state at the cursor's count. Popped from memory alone,
        that state came back from disk at the next write, the cut stayed at its count, and every quiet drop was refused
        until the file outgrew the lag bound (64 records here). The close retires the fold from the document too."""
        ap, recs = self.scene()
        km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it
        self.assertIn(ap, km._bgall_cache)
        self.clock.t = T0 + 300                                   # a quiet spell over two minutes, the viewer still open
        km._agent_launch_ids(ap)                                  # a quiet pass: its drop writes the document
        self.assertEqual(self.doc(ap)["folds"]["bgAll"]["count"], len(recs), "the write recorded the open viewer's cursor")
        append(ap, chain([rec(T0 + 301 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 + 304)   # the agent writes on
        self.clock.t = T0 + 305
        self.close(self.client())
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records while the file is fresh
        self.clock.t = T0 + 700                                   # the agent returned
        km._agent_launch_ids(ap)                                  # a quiet fold
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "the state the closed viewer left in the document no longer holds the drop")
        self.assertNotIn("bgAll", self.doc(ap)["folds"], "the close retired the fold from the document")

    def test_a_restarted_kernel_steps_the_state_a_viewer_open_at_the_exit_drain_left(self):
        """With the viewer open at the exit drain, the document carries the background-task state behind the file's end (the
        cut at its count), and the next process retires it only if that viewer, loaded before the restart, is closed (the
        restart test of the close below); here it is not. The guard's pass steps that state over the records the reader
        holds, before the card's folds, so the returned agent drops in that pass."""
        ap, recs = self.scene()
        km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)   # the agent writes on
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records
        self.assertTrue(em.checkpoint_write(ap))                  # the exit drain, the viewer still open
        d = self.doc(ap)
        self.assertEqual((d["count"], d["folds"]["bgAll"]["count"], d["folds"]["agentLaunchIds"]["count"]),
                         (len(recs), len(recs), len(recs) + 3), "the cut sits at the viewer's cursor, behind the file's end")
        self.fresh_process()                                      # a restart; the viewer is not closed in the new process
        for c in (km._parse_cache, km._SUBAGENT_FRAMES):
            c.clear()
        self.clock.t = T0 + 300                                   # the agent returned
        km._spend_window_usd(self.leaf, T0 + 300, 600, PRICES)    # the guard's pass
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "the guard stepped the viewer's state before the card's folds, and the agent dropped")
        self.assertEqual(self.doc(ap)["folds"]["bgAll"]["count"], len(recs) + 3, "the document's cut follows the file's end")

    def test_a_viewer_whose_socket_went_away_during_the_push_render_lets_go_of_that_renders_cursor(self):
        """The socket's finally runs on the handler thread: it marks the client dead, takes it off the client list and closes
        its viewers (the cursor, then the parse), which frees nothing when it lands before the render folds and parses the
        file, and it leaves the client's viewer map as it was. The pusher, finding the client dead after its render, lets go
        of the cursor and the parse that render left and sends nothing."""
        ap, recs = self.scene()
        client = self.client()
        self.register(client)
        real, after_close, sent = km.build_subagent, [], []

        def dying_build(sid, agent_id, now, live_map=None):
            client["alive"] = False                               # the socket's finally, in its order
            with km._clients_lock:
                km._clients.remove(client)
            for sv in list((client.get("subagents") or {}).keys()):
                km._subagent_viewer_closed(*sv)
                km._release_subagent_view(*sv)
            after_close.append(ap in km._bgall_cache)
            return real(sid, agent_id, now, live_map)

        with mock.patch.object(km, "build_subagent", dying_build), \
                mock.patch.object(km, "_send_client", lambda c, key, msg, pre=None, **kw: sent.append(key)):
            km._push_subagents([client], int(time.time()), {})
        self.assertEqual(after_close, [False], "the socket's close ran before the render folded the file")
        self.assertIn((SID, self.AID), client["subagents"], "the finally leaves the viewer map as it was")
        self.assertNotIn(ap, km._bgall_cache, "the pusher let go of the cursor its render left for the dead client")
        self.assertEqual(self.asm_entries(ap), [], "and of the parse that render rebuilt")
        self.assertEqual(sent, [], "a dead client is not sent the frame")
        self.returns_and_drops(ap, recs, "no cursor left by the dead client's render holds the returned agent's drop")

    def dial(self, frames, at_close=None):
        """The real handshake and read loop of one chat socket whose peer sends `frames` (as text) and then closes; `at_close`
        runs as the close frame is read. Returns the client the socket registered."""
        class FakeSelf:
            headers = {"Sec-WebSocket-Key": "dGhlIHNhbXBsZSBub25jZQ=="}
            path = "/ws?app=chat&delta=1&iid=page-v&wid=w1"
            rfile = io.BytesIO(); wfile = io.BytesIO()
            connection = type("FakeSock", (), {"sendall": lambda self, b: None, "shutdown": lambda self, how: None})()
            close_connection = False
            _dispatch_ws = km.Handler._dispatch_ws            # bound here, or the open frame's AttributeError is logged and swallowed
            def send_response(self, *a): pass
            def send_header(self, *a): pass
            def end_headers(self): pass
        pending = [(0x1, json.dumps(f).encode("utf-8"), True) for f in frames]

        def recv(rfile):
            if pending:
                return pending.pop(0)
            # the socket's ops run on its lane, beside this read loop (kernel.py _ws_arm_lane, 2026-10-09): the page closes once
            # what it sent has been served, so the close is read after the lane has dispatched the frames before it
            lane = got[0].get("lane") if got else None
            end = time.time() + 30
            while lane is not None and lane["pending"] and time.time() < end:
                time.sleep(0.005)
            if at_close is not None:
                at_close()
            return (0x8, b"", True)
        got, real_reg = [], km._register_ws_client
        saved = list(km._clients)
        try:
            with mock.patch.object(km, "_register_ws_client", lambda c: (got.append(c), real_reg(c))), \
                    mock.patch.object(km, "_ws_recv", recv), mock.patch("sys.stderr", new=io.StringIO()):
                km.Handler._ws(FakeSelf())
        finally:
            km._clients.clear()
            km._clients.extend(saved)
        self.assertEqual(len(got), 1)
        return got[0]

    def test_a_socket_that_goes_away_closes_the_viewer_it_opened(self):
        """The viewer opens through the socket's own dispatch (openSubagent: the render folds the file's background tasks),
        and the socket's teardown closes it; a control socket with no viewer open leaves the cursor alone."""
        ap, recs = self.scene()
        seen = []
        c = self.dial([{"type": "openSubagent", "id": SID, "agentId": self.AID}], at_close=lambda: seen.append(ap in km._bgall_cache))
        self.assertIn((SID, self.AID), c.get("subagents") or {}, "the open registered the viewer on the socket's client")
        self.assertEqual(seen, [True], "the open's render left its cursor")
        self.assertNotIn(ap, km._bgall_cache, "the socket going away closed the viewer it had open")
        km.build_subagent(SID, self.AID, int(time.time()), {})   # a viewer rendered for another socket
        self.assertIn(ap, km._bgall_cache)
        self.dial([])                                             # a control socket with no viewer goes away
        self.assertIn(ap, km._bgall_cache, "a socket with no viewer open leaves the cursor alone")

    def test_a_socket_that_goes_away_releases_the_parse_of_the_viewer_it_opened(self):
        """The same socket, opened and closed through its own dispatch and teardown: the teardown releases the viewer's
        parse as well as its cursor, so a returned agent's records are not left under the assembly pin (a closed tab, a
        reload, a dropped connection; round six of the fold's review)."""
        ap, recs = self.scene()
        seen = []
        c = self.dial([{"type": "openSubagent", "id": SID, "agentId": self.AID}], at_close=lambda: seen.append(bool(self.asm_entries(ap))))
        self.assertIn((SID, self.AID), c.get("subagents") or {}, "the open registered the viewer on the socket's client")
        self.assertEqual(seen, [True], "the premise: the open's render parsed the agent's file")
        self.assertEqual(self.asm_entries(ap), [], "the socket going away released the viewer's parse")
        self.returns_and_drops(ap, recs, "no parse the gone socket's viewer built holds the returned agent's drop")

    def test_a_viewer_another_client_has_open_keeps_its_cursor_until_the_last_close(self):
        ap, recs = self.scene()
        one, two = self.client("w1"), self.client("w2")
        self.register(one, two)
        km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer, open on both clients, renders
        self.assertIn(ap, km._bgall_cache)
        self.close(one)
        self.assertIn(ap, km._bgall_cache, "the other client still has the viewer open: its cursor stays")
        self.close(two)
        self.assertNotIn(ap, km._bgall_cache, "the last close pops it")
        self.returns_and_drops(ap, recs, "after the last close the returned agent drops")

    def test_a_close_in_a_new_process_before_any_render_retires_the_state_a_viewer_open_at_the_exit_drain_left(self):
        """A viewer loaded before a restart and closed after it: the page sends the close although the new process never
        rendered the viewer, so no cursor is in memory and no fold of the new process has loaded the file's document. The
        close reads the document's folds from disk and retires the background-task state, so the card's quiet folds drop the
        returned agent with no guard pass (a guard pass would step that state itself, the restart test above)."""
        ap, recs = self.scene()
        km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)   # the agent writes on
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records
        self.assertTrue(em.checkpoint_write(ap))                  # the exit drain, the viewer still open
        d = self.doc(ap)
        self.assertEqual((d["count"], d["folds"]["bgAll"]["count"]), (len(recs), len(recs)), "the cut sits at the viewer's cursor")
        self.fresh_process()                                      # a restart
        for c in (km._parse_cache, km._SUBAGENT_FRAMES):
            c.clear()
        self.assertNotIn(ap, km._bgall_cache, "no cursor in the new process")
        with em._CKPT_LOCK:
            self.assertNotIn(ap, em._CKPT_DOC_FOLDS, "and no fold of it has loaded the document")
        self.clock.t = T0 + 300                                   # the agent returned
        self.close(self.client(viewer=False))                     # the page closes the viewer it loaded before the restart
        for i in range(2):
            km._agent_launch_ids(ap)                              # the card's quiet folds; no guard pass
            ent = self.entry(ap)
            self.assertTrue(ent is None or not ent[4], "quiet fold %d: the close retired the state and the agent dropped" % (i + 1))
        self.assertNotIn("bgAll", self.doc(ap)["folds"], "the document no longer carries the closed viewer's state")

    def test_a_close_with_nothing_to_retire_leaves_a_clean_path_clean(self):
        """A returned agent whose quiet fold wrote its document and dropped it, and whose viewer never rendered: the close
        finds no cursor to pop and no background-task state in the document, so it retires nothing and dirties nothing."""
        ap, recs = self.scene()
        self.clock.t = T0 + 300                                   # the agent returned
        km._agent_launch_ids(ap)                                  # a quiet fold: the document written, the entry dropped
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4])
        self.assertNotIn("bgAll", self.doc(ap)["folds"])
        with em._CKPT_LOCK:
            self.assertEqual((ap in em._FOLD_DIRTY, ap in em._RETIRED_FOLDS), (False, False), "clean before the close")
        self.close(self.client(viewer=False))
        with em._CKPT_LOCK:
            self.assertNotIn(ap, em._FOLD_DIRTY, "the close left the path clean")
            self.assertNotIn(ap, em._RETIRED_FOLDS, "and owes it no retirement")

    def test_a_close_that_lands_inside_a_write_before_any_document_keeps_the_fold_out_of_it(self):
        """The close runs on the handler thread and a document write on the pusher's. A close that lands after the write took
        its cursors and before it carries the disk's states finds no document to consult, so only the cursor it popped says
        there is something to retire, and the write must still leave the fold out."""
        ap, recs = self.scene()
        km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it
        append(ap, chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"]), T0 - 38)   # the agent writes on
        km._agent_launch_ids(ap)                                  # the card's fold steps the new records
        self.assertFalse(em._ckpt_file(ap).exists(), "no document yet")
        real, popped = em._carry_forward_states, []

        def closing_carry(*a, **kw):
            self.close(self.client(viewer=False))                 # the viewer closes while the write runs
            popped.append(ap not in km._bgall_cache)
            return real(*a, **kw)
        with mock.patch.object(em, "_carry_forward_states", closing_carry):
            self.assertTrue(em.checkpoint_write(ap))
        self.assertEqual(popped, [True], "the close landed inside the write and popped the cursor")
        self.assertNotIn("bgAll", self.doc(ap)["folds"], "the write left out the fold the close retired")
        self.clock.t = T0 + 300                                   # the agent returned
        km._agent_launch_ids(ap)                                  # a quiet fold
        ent = self.entry(ap)
        self.assertTrue(ent is None or not ent[4], "the closed viewer's cursor does not hold the returned agent's drop")

    def guard_over_a_raising_reader(self, bg):
        """A returned agent whose card's chat stopped being built before it returned (the steps cursor stands where the chat
        last folded, behind the file's end) meets the guard's first passes over the quiet file with one of the guard's
        readers raising at every pass: with `bg`, the open viewer's background-task step (em._bg_step, which the fold looks
        up at call time, patched to raise as the launch-ids variant patches its own step, so the test does not rest on a
        product step's crash: review round four); otherwise the launch-ids step. The folds after the raising one still
        run, so the agent drops at the first pass, the rate stands and each raise is counted. Returns stderr's text."""
        ap, recs = self.scene()
        km._agent_steps(ap)                                       # the card's steps fold, while its chat was still built
        if bg:
            km.build_subagent(SID, self.AID, int(time.time()), {})   # the viewer opens on it and stays open
            self.assertIn(ap, km._bgall_cache)
            # the viewer's parse released, as the assembly cache's own bound would: while it stands, the assembly pin (#87)
            # keeps the records it folds over, by design; the background-task cursor the render left stays
            em.release_assembly(ap, rompuuid=SID)
            self.assertIn(ap, km._bgall_cache)
        more = chain([rec(T0 - 40 + i, 20 + i) for i in range(3)], recs[-1]["uuid"])
        append(ap, more, T0 - 38)                                 # the agent writes on; no card fold runs from here
        want = sum(usd(r) for r in recs + more if r["type"] == "assistant") + usd(rec(T0 - 200, 0))   # and the parent's reply
        site = "spendGuardFold:RuntimeError"
        fault = mock.patch.object(em, "_bg_step", side_effect=RuntimeError("synthetic step fault")) if bg \
            else mock.patch.object(km, "_launch_ids_step", side_effect=RuntimeError("synthetic step fault"))
        em._SWALLOWED.clear()
        err = io.StringIO()
        with fault, mock.patch("sys.stderr", new=err):
            for n, t in enumerate((T0 + 300, T0 + 305), 1):       # the agent returned: the guard's passes over the quiet file
                self.clock.t = t
                self.assertAlmostEqual(km._spend_window_usd(self.leaf, t, 600, PRICES), want, places=9,
                                       msg="pass %d: the guard's rate stands" % n)
                ent = self.entry(ap)
                self.assertTrue(ent is None or not ent[4], "pass %d: the folds after the raising one ran, and the agent dropped" % n)
                self.assertEqual(em.record_cache_stats()["swallowed"], {site: 1},
                                 "pass %d: the first pass's raise counted; over the file let go no fold runs again" % n)
        self.assertEqual(err.getvalue().count("spendGuardFold raised"), 1, "said once, at the first raise")
        self.assertIn(" on %s: " % ap, err.getvalue(), "the line names the file")
        em._SWALLOWED.clear()
        return err.getvalue()

    def test_a_background_task_step_that_raises_every_pass_does_not_keep_a_returned_agent(self):
        self.assertIn("in _bg_scan_all_cached", self.guard_over_a_raising_reader(bg=True), "the background-task step raised")

    def test_a_launch_ids_step_that_raises_every_pass_does_not_keep_a_returned_agent(self):
        self.assertIn("in _agent_launch_ids_fold", self.guard_over_a_raising_reader(bg=False), "the launch-ids step raised")


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

    def test_a_raise_in_the_guards_agent_fold_keeps_its_total_and_is_counted_and_said(self):
        wf = str(self.sub / "workflows" / "wf_1" / "agent-a9800000000000009.jsonl")
        recs = [rec(T0 - 100 + 5 * i, i) for i in range(10)]
        write(wf, recs, T0 - 55)
        km.em._SWALLOWED.clear()
        err = io.StringIO()
        with mock.patch.object(km.em, "fold_steps_in_hand", side_effect=RuntimeError("synthetic fold fault")), \
             mock.patch("sys.stderr", new=err):
            self.assertAlmostEqual(self.window(T0), sum(map(usd, recs)), places=9, msg="the guard's rate stands")
            self.assertAlmostEqual(self.window(T0 + 120), sum(map(usd, recs)), places=9)
        self.assertEqual(km.em.record_cache_stats()["swallowed"], {"spendGuardFold:RuntimeError": 6},
                         "counted on /perf: each of the three folds asks in a try of its own, at each of the two passes")
        self.assertEqual(err.getvalue().count("spendGuardFold raised RuntimeError"), 1, "said once, at the first raise")
        self.assertIn(" on %s: synthetic fold fault" % wf, err.getvalue(), "the line names the file")
        self.assertIn("in _spend_fold_agent", err.getvalue(), "and the frame that raised")
        self.assertTrue(em.entry_whole_resident(wf), "no fold ran: the records stay, as on main")

    def test_a_raise_in_the_guards_launch_ids_fold_is_counted_and_said(self):
        """For a cardless subagent the launch-ids fold is the guard's only reader. Its kernel reader answers set() on any
        raise, so through that reader a raise never reached the guard's handler: no count and no line while the records
        stayed. The guard folds through the raising reader the other one wraps."""
        wf = str(self.sub / "workflows" / "wf_1" / "agent-a9800000000000010.jsonl")
        recs = [rec(T0 - 100 + 5 * i, i) for i in range(10)]
        write(wf, recs, T0 - 55)
        km.em._SWALLOWED.clear()
        err = io.StringIO()
        with mock.patch.object(km, "_launch_ids_step", side_effect=RuntimeError("synthetic step fault")), \
             mock.patch("sys.stderr", new=err):
            self.assertAlmostEqual(self.window(T0), sum(map(usd, recs)), places=9, msg="the guard's rate stands")
            self.assertAlmostEqual(self.window(T0 + 120), sum(map(usd, recs)), places=9)
            self.assertEqual(km._agent_launch_ids(wf), set(), "the card's reader still answers set() on a raise")
        self.assertEqual(km.em.record_cache_stats()["swallowed"], {"spendGuardFold:RuntimeError": 2},
                         "the guard's two raises counted on /perf, the card reader's not")
        self.assertEqual(err.getvalue().count("spendGuardFold raised RuntimeError"), 1, "said once, at the first raise")
        self.assertIn("in _agent_launch_ids_fold", err.getvalue(), "the traceback runs through the guard's raising reader")
        self.assertTrue(em.entry_whole_resident(wf), "the fold never finished: the records stay")
        self.assertAlmostEqual(self.window(T0 + 125), sum(map(usd, recs)), places=9)
        self.assertIsNone(self.entry(wf), "the fault gone, the guard's next look drops the returned agent")

    def test_the_raise_line_is_said_at_the_first_tenth_hundredth_and_thousandth_raise(self):
        err = io.StringIO()
        em._SWALLOWED.clear()
        with mock.patch("sys.stderr", new=err):
            for _ in range(1000):
                try:
                    raise RuntimeError("synthetic fault")
                except RuntimeError as e:
                    em.note_swallowed("synthSite", e, "/w/notes-api/agent-a9800000000000011.jsonl")
        said = [int(ln.split("(")[1].split()[0]) for ln in err.getvalue().splitlines() if ln.startswith("record cache: synthSite")]
        self.assertEqual(said, [1, 10, 100, 1000])
        self.assertEqual(em.record_cache_stats()["swallowed"], {"synthSite:RuntimeError": 1000})
        em._SWALLOWED.clear()

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

    def test_a_half_written_record_at_the_guards_read_costs_no_whole_read(self):
        ap = str(self.sub / "agent-a9f00000000000001.jsonl")
        early = [bare(rec(T0 - 400 + 5 * i, i)) for i in range(10)]   # no message ids: a record counted twice would show
        late = [bare(rec(T0 + 5 + i, 100 + i)) for i in range(4)]
        lines = [json.dumps(r) + "\n" for r in early + late]
        Path(ap).parent.mkdir(parents=True, exist_ok=True)

        def put(text, mtime):
            with open(ap, "a") as fh:
                fh.write(text)
            os.utime(ap, (mtime, mtime))
        put("".join(lines[:10]) + lines[10][:40], T0 - 355)       # the memo's first scan meets a half-written record
        self.assertAlmostEqual(self.window(T0), in_window(early, T0), places=9)
        self.assertIsNone(self.entry(ap), "quiet: dropped as it was folded")
        whole0 = self.guard_whole_reads()
        put(lines[10][40:] + lines[11] + lines[12][:40], T0 + 8)   # the agent resumes; the resume's read meets another half
        self.assertAlmostEqual(self.window(T0 + 10), in_window(early + late[:2], T0 + 10), places=9)
        self.assertEqual(self.entry(ap)[5], 10, "the tail past the drop's cut")
        put(lines[12][40:] + lines[13], T0 + 13)
        self.assertAlmostEqual(self.window(T0 + 15), in_window(early + late, T0 + 15), places=9)
        self.assertEqual(self.guard_whole_reads(), whole0, "the memo ends where its records end, not past a half-written line")

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
        self.split_response(larger_first=False)

    def test_a_response_whose_larger_usage_was_logged_before_the_drop_counts_once_at_it(self):
        self.split_response(larger_first=True)

    def split_response(self, larger_first):
        """One response logged as two records with one message id, one before a drop and one in the tail after it, the
        larger usage on the side `larger_first` names: priced once at that usage, from the memo and the tail."""
        ap = str(self.sub / "agent-a9d00000000000001.jsonl")
        early = [rec(T0 - 400 + 5 * i, i) for i in range(10)]
        early[-1]["message"]["id"] = "msg-split"                 # a response's first content block, before the drop
        split = rec(T0 + 5, 100)
        split["message"]["id"] = "msg-split"                     # its later block, past the cut
        (early[-1] if larger_first else split)["message"]["usage"]["output_tokens"] = 3000
        write(ap, early, T0 - 355)
        self.assertAlmostEqual(self.window(T0), sum(map(usd, early)), places=9)
        self.assertIsNone(self.entry(ap))
        whole0 = self.guard_whole_reads()
        late = [split, rec(T0 + 6, 101)]
        append(ap, late, T0 + 8)
        kept = early + late[1:] if larger_first else early[:-1] + late
        self.assertAlmostEqual(self.window(T0 + 10), sum(map(usd, kept)), places=9,
                               msg="one response, one row, at its largest usage, as a scan of the whole file counts it")
        self.assertEqual(self.guard_whole_reads(), whole0, "priced from the tail, never through a whole read")

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

    NO_COUNT = object()           # corrupt_tail's edit that deletes the fold's count instead of replacing it
    CORRUPT_DROPPED = {"swallowed": {}, "fallbacks": {"corrupt": 1}, "first": (True, True, False), "held": 0, "dropped": 1,
                       "wholeAfter": 1, "raiseLines": 0, "fallbackLines": 1, "docCount": 100}

    def corrupt_tail(self, n, folds, bad, count):
        """The restored-tail test above in a session of its own (`n` names it), over a corrupt document. The previous process
        ran the folds `folds` over the agent file's first five records and wrote its document, each fold's state at count 5
        (with the card's steps fold among them the file is a card's subagent, at the top of the subagents directory;
        otherwise a workflow agent); the agent then wrote 95 more records; and while the kernel was down the count of fold
        `bad` was edited to `count` (NO_COUNT: the key deleted), which no writer stores, so only a hand edit or another tool
        does. No card fold runs in the new process: the card's chat is no longer built. The guard passes the file every five
        seconds from T0 to T0 + 900 s, its total checked against the reference at every pass. Returns what the tests
        compare: raises swallowed, fallbacks counted, `first` (after the first pass: the reader's entry gone, the file read
        short of its size, the document on disk), the records held and the drops at the end, the whole reads of the file
        after the first pass (the bytes read since, over its size), the raise and fallback lines said, and the launch-ids
        count in the document at the end (None with no document)."""
        self.fresh_process()
        sid = "11111111-2222-4333-8444-0000000001%02d" % n
        leaf = str(self.proj / (sid + ".jsonl"))
        write(leaf, [rec(T0 - 5000, 0)], T0 - 5000)              # the leaf: nothing inside the window
        sub = self.proj / sid / "subagents"
        name = "agent-a9a000000000001%02d.jsonl" % n
        ap = str(sub / name if "agentGist" in folds else sub / "workflows" / "wf_1" / name)
        pairs = [(rec(T0 - 1000 + 10 * i, i), T0 - 1000 + 10 * i) for i in range(100)]   # T0-1000 .. T0-10
        write(ap, [r for r, _t in pairs[:5]], T0 - 960)
        self.clock.t = T0 - 955
        run = {"bgAll": km._bg_scan_all_cached, "agentLaunchIds": km._agent_launch_ids_fold, "agentGist": km._agent_gist_fold}
        for f in folds:                                           # the previous process's folds while the agent ran
            run[f](ap)
        self.assertTrue(em.checkpoint_write(ap))                  # its document
        append(ap, [r for r, _t in pairs[5:]], T0 - 10)
        cp = em._ckpt_file(ap)
        d = json.loads(cp.read_text())
        self.assertEqual({f: ("state" in d["folds"][f], d["folds"][f]["count"]) for f in folds}, dict.fromkeys(folds, (True, 5)),
                         "the document carries each fold's state at count 5")
        if count is self.NO_COUNT:
            del d["folds"][bad]["count"]
        else:
            d["folds"][bad]["count"] = count                      # a hand edit while the kernel was down
        cp.write_text(json.dumps(d))
        self.fresh_process()                                      # the restart
        em._SWALLOWED.clear()
        fb0 = dict(em.checkpoint_stats()["fallbacks"])
        size, err = os.path.getsize(ap), io.StringIO()
        with mock.patch("sys.stderr", new=err):
            for t in range(T0, T0 + 901, 5):
                self.clock.t = t
                want = sum(usd(r) for r, rt in pairs if rt >= t - 600)
                self.assertAlmostEqual(km._spend_window_usd(leaf, t, 600, PRICES), want, places=9,
                                       msg="the window at +%d s" % (t - T0))
                if t == T0:
                    r1 = self.read_of(ap)
                    first = (self.entry(ap) is None, r1 < size, cp.exists())
        ent, again = self.entry(ap), self.read_of(ap) - r1
        self.assertLessEqual(again, size + 4 * em._JSONL_TAIL_GUARD, "at most one whole read after the first pass")
        self.assertIn("fold %s: " % bad, err.getvalue(), "the fallback names the fold whose count it refused")
        fb = {k: v - fb0.get(k, 0) for k, v in em.checkpoint_stats()["fallbacks"].items() if v != fb0.get(k, 0)}
        return {"swallowed": em.record_cache_stats()["swallowed"], "fallbacks": fb, "first": first,
                "held": len(ent[4]) if ent else 0, "dropped": em.record_cache_stats()["dropped"], "wholeAfter": again // size,
                "raiseLines": err.getvalue().count("spendGuardFold raised"),
                "fallbackLines": err.getvalue().count("checkpoint fallback (corrupt)"),
                "docCount": (self.doc(ap)["folds"].get("agentLaunchIds") or {}).get("count") if cp.exists() else None}

    def test_a_restored_tail_whose_launch_ids_count_is_no_int_is_counted_corrupt_once_and_then_dropped(self):
        """The restored-tail test above, for a workflow agent, with the document's launch-ids count edited to a value int()
        refuses: Infinity (OverflowError), NaN or a string (ValueError), null (TypeError), or no count at all (KeyError). The
        restore counts such a document corrupt and removes it. The guard's gate caught all but OverflowError and answered
        that the fold did not step the records in hand, leaving the document on disk with nothing counted, and on Infinity
        it raised at every pass while the file stayed in the window (review round ten). Each is now counted corrupt once,
        at the first pass, and no pass raises. The fallback removes every fold's state with the document, so the gate lets
        go of the tail restored from it too: the guard's next read takes the file whole, and its launch-ids fold drops the
        file once the agent has been quiet for two minutes, writing a good document (review round eleven: until then the
        tail stayed with no state left for a fold to step). The guard's total holds at every pass."""
        for i, (label, count) in enumerate((("Infinity", float("inf")), ("NaN", float("nan")), ("a string", "five"),
                                            ("null", None), ("no count", self.NO_COUNT))):
            with self.subTest(count=label):
                self.assertEqual(self.corrupt_tail(10 + i, ("agentLaunchIds",), "agentLaunchIds", count), self.CORRUPT_DROPPED)

    def test_a_cards_restored_tail_whose_launch_ids_count_reads_as_infinity_is_dropped_after_the_fallback(self):
        """A card's subagent whose chat is no longer built, restored as a tail whose document carries the card's steps state
        and a launch-ids count edited to Infinity. The fallback at the launch-ids fold's gate removed the steps state with
        the document, so the steps fold, asked next, found a tail with no cursor and no state at that pass and every later
        one, and the tail stayed, where before review round ten the agent was dropped (review round eleven). The gate now
        lets the tail go with the document, and the guard drops the file after one whole read."""
        self.assertEqual(self.corrupt_tail(20, ("agentLaunchIds", "agentGist"), "agentLaunchIds", float("inf")),
                         self.CORRUPT_DROPPED)

    def test_a_cards_restored_tail_whose_background_task_count_reads_as_infinity_is_dropped_after_the_fallback(self):
        """The same card's subagent with valid launch-ids and steps states and the subagent viewer's background-task state
        (a viewer open at the exit drain leaves one) whose count reads as Infinity. That gate is asked first, so its
        fallback took both later folds' states (review round eleven); the guard now drops the file after one whole read."""
        self.assertEqual(self.corrupt_tail(21, ("bgAll", "agentLaunchIds", "agentGist"), "bgAll", float("inf")),
                         self.CORRUPT_DROPPED)

    def test_a_cardless_restored_tail_whose_background_task_count_is_infinity_or_missing_is_dropped_after_the_fallback(self):
        """A workflow agent with a valid launch-ids state and a background-task state whose count reads as Infinity, or has
        no count. Before review round ten the agent was dropped in both, after a raise at every pass until then for
        Infinity, with none for the missing count, which the gate answered as not in hand; at review round ten's head the
        background-task fallback took the launch-ids state and the tail stayed (review round eleven). The guard now drops
        the file after one whole read."""
        for i, (label, count) in enumerate((("Infinity", float("inf")), ("no count", self.NO_COUNT))):
            with self.subTest(count=label):
                self.assertEqual(self.corrupt_tail(30 + i, ("bgAll", "agentLaunchIds"), "bgAll", count), self.CORRUPT_DROPPED)

    def spared(self, n, put):
        """The restored-tail test above for a workflow agent in a session of its own (`n` names it), its document's
        launch-ids count edited to Infinity while the kernel was down. Inside the corrupt fallback, between the gate's look
        and its eviction and before the fallback removes the document, `put(wf, pairs)` has another read store an entry of
        its own for the file (a record it appends joins `pairs`). The guard passes the file once, at T0, its total checked.
        Returns the session's leaf, the file, the records, the restored tail the gate looked at, the entry `put` stored, and
        the corrupt fallbacks counted before the pass."""
        self.fresh_process()
        sid = "11111111-2222-4333-8444-0000000001%02d" % n
        leaf = str(self.proj / (sid + ".jsonl"))
        write(leaf, [rec(T0 - 5000, 0)], T0 - 5000)              # the leaf: nothing inside the window
        wf = str(self.proj / sid / "subagents" / "workflows" / "wf_1" / ("agent-a9a000000000001%02d.jsonl" % n))
        pairs = [(rec(T0 - 1000 + 10 * i, i), T0 - 1000 + 10 * i) for i in range(100)]   # T0-1000 .. T0-10
        write(wf, [r for r, _t in pairs[:5]], T0 - 960)
        self.clock.t = T0 - 955
        km._agent_launch_ids_fold(wf)                             # the previous process's fold while the agent ran
        self.assertTrue(em.checkpoint_write(wf))
        append(wf, [r for r, _t in pairs[5:]], T0 - 10)
        cp = em._ckpt_file(wf)
        d = json.loads(cp.read_text())
        d["folds"]["agentLaunchIds"]["count"] = float("inf")      # a hand edit while the kernel was down
        cp.write_text(json.dumps(d))
        self.fresh_process()                                      # the restart
        real, seen = em._ckpt_fallback, []

        def fallback(*a, **k):
            if not seen:                                          # once, should a read `put` makes reach the fallback again
                seen.append(self.entry(wf))                       # the restored tail the gate looked at, still in place
                seen.append(put(wf, pairs))
            return real(*a, **k)

        fb0 = em.checkpoint_stats()["fallbacks"].get("corrupt", 0)
        want = sum(usd(r) for r, rt in pairs if rt >= T0 - 600)   # priced before the guard's folds run
        self.clock.t = T0
        with mock.patch.object(em, "_ckpt_fallback", side_effect=fallback) as fb, mock.patch("sys.stderr", new=io.StringIO()):
            self.assertAlmostEqual(km._spend_window_usd(leaf, T0, 600, PRICES), want, places=9)
        self.assertEqual(fb.call_count, 1, "the gate took the corrupt fallback once")
        looked, mine = seen
        self.assertEqual((looked[5], len(looked[4])), (5, 95), "the premise: the gate looked at the restored tail")
        return leaf, wf, pairs, looked, mine, fb0

    def test_the_gates_eviction_spares_a_whole_entry_or_another_generation_another_read_put_in_its_place(self):
        """The gate lets go of a tail of the generation it looked at, never of a whole entry nor of one of another generation
        (review round twelve; at review round eleven, of the very entry it looked at alone). Between the gate's look and its
        eviction a whole reader upgrades the tail in place (the whole file, the same generation), or the count cap evicts
        the tail and a reader that takes one restores it again from the document, at a generation of its own: the entry
        that read stored stays."""
        def whole(wf, pairs):
            return em._read_jsonl_entry(wf)                       # a whole reader: the tail upgraded in place

        def regen(wf, pairs):
            with em._JSONL_CACHE_LOCK:
                em._cache_pop_locked(wf)                          # the count cap evicts the tail
            return em._read_jsonl_entry(wf, tail_ok=True)         # and a reader that takes a tail restores it again

        for i, (label, put) in enumerate((("a whole entry", whole), ("another generation", regen))):
            with self.subTest(put=label):
                _leaf, wf, _pairs, looked, mine, _fb0 = self.spared(40 + i, put)
                if put is whole:
                    self.assertEqual((mine[5], len(mine[4]), mine[6]), (0, 100, looked[6]),
                                     "the premise: the whole file at the generation the gate looked at")
                else:
                    self.assertEqual((mine[5], len(mine[4])), (5, 95), "the premise: the restored tail again")
                    self.assertNotEqual(mine[6], looked[6], "at a generation of its own")
                self.assertIs(self.entry(wf), mine, "the entry another read put in place stays")

    def test_the_gates_eviction_lets_go_of_a_same_generation_tail_an_append_put_in_its_place(self):
        """Between the gate's look and its eviction the agent writes one more record, and a reader that takes a tail (the
        assembly refresh, say) appends it over the restored tail: a longer tail of the generation the gate looked at, with
        no state left for a fold to step once the fallback removes the document. The gate lets it go as it lets that tail go
        (review round twelve; at 66a48e3f9, which spared every entry but the one it looked at, it stayed with its 96
        records, nothing dropped and no document). The guard's next read takes the file whole, and its launch-ids fold
        drops it once the agent has been quiet for two minutes, writing a good document; the total holds at every pass."""
        def appended(wf, pairs):
            r = rec(T0 - 3, 100)
            append(wf, [r], T0 - 3)
            pairs.append((r, T0 - 3))
            return em._read_jsonl_entry(wf, tail_ok=True)

        leaf, wf, pairs, looked, mine, fb0 = self.spared(42, appended)
        self.assertEqual((mine[5], len(mine[4]), mine[6]), (5, 96, looked[6]), "the premise: a longer tail, the same generation")
        self.assertIsNot(mine, looked)
        self.assertIsNone(self.entry(wf), "it goes with the corrupt document")
        size, r1, err = os.path.getsize(wf), self.read_of(wf), io.StringIO()
        with mock.patch("sys.stderr", new=err):
            for t in range(T0 + 5, T0 + 901, 5):
                self.clock.t = t
                self.assertAlmostEqual(km._spend_window_usd(leaf, t, 600, PRICES), sum(usd(r) for r, rt in pairs if rt >= t - 600),
                                       places=9, msg="the window at +%d s" % (t - T0))
        ent = self.entry(wf)
        self.assertTrue(ent is None or not ent[4], "the records left memory")
        self.assertEqual(em.record_cache_stats()["dropped"], 1, "dropped once")
        self.assertEqual(self.doc(wf)["folds"]["agentLaunchIds"]["count"], 101, "by the launch-ids fold, which wrote a good document")
        self.assertLessEqual(self.read_of(wf) - r1, size + 4 * em._JSONL_TAIL_GUARD, "after one whole read")
        self.assertEqual(em.checkpoint_stats()["fallbacks"].get("corrupt", 0) - fb0, 1, "counted corrupt once")
        self.assertNotIn("checkpoint fallback", err.getvalue(), "no later pass took a fallback")

    def test_a_whole_entry_a_viewers_parse_read_outlasts_the_gates_fallback_and_leaves_at_a_later_quiet_drop(self):
        """A card's subagent whose chat is no longer built, after a restart, its document's steps count edited to Infinity,
        its viewer open: the viewer's parse read the file whole before the agent's last record (review round twelve). The
        guard's launch-ids fold restores over that whole entry, and the steps gate, asked next, reads the bad count and
        takes the corrupt fallback. At 66a48e3f9 the gate popped the whole entry there, past the assembly pin, freeing
        nothing the parse held, and the viewer's rebuild read the file whole. The gate lets go of a tail only, so the entry
        stays and the quiet drop decides it as before: the pin keeps it while the parse stands, its document written; the
        viewer's rebuild reads nothing, as at 147100aa0; and once the viewer's close releases the parse, the next quiet pass
        drops it."""
        ap = str(self.sub / "agent-a9a00000000000150.jsonl")
        pairs = [(rec(T0 - 1000 + 10 * i, i), T0 - 1000 + 10 * i) for i in range(100)]   # T0-1000 .. T0-10
        write(ap, [r for r, _t in pairs[:5]], T0 - 960)
        self.clock.t = T0 - 955
        km._agent_launch_ids_fold(ap)                             # the previous process: the card's two folds while the agent ran
        km._agent_gist_fold(ap)
        self.assertTrue(em.checkpoint_write(ap))                  # its document
        append(ap, [r for r, _t in pairs[5:]], T0 - 20)
        cp = em._ckpt_file(ap)
        d = json.loads(cp.read_text())
        self.assertEqual({f: ("state" in d["folds"][f], d["folds"][f]["count"]) for f in ("agentLaunchIds", "agentGist")},
                         {"agentLaunchIds": (True, 5), "agentGist": (True, 5)}, "the document carries both folds' states at 5")
        d["folds"]["agentGist"]["count"] = float("inf")           # a hand edit while the kernel was down
        cp.write_text(json.dumps(d))
        self.fresh_process()                                      # the restart; no card fold runs: the chat is not built
        self.clock.t = T0

        def parse():                                              # the subagent viewer's parse of the agent's own file
            em.parse_session(ap, rompuuid=SID, name="api", dir="/w/notes-api", candidate_files=[ap], states=None,
                             postal_log=[], now=T0)
        parse()
        self.assertEqual((self.entry(ap)[5], len(self.entry(ap)[4])), (0, 100), "the premise: the parse read the file whole")
        last = rec(T0 - 5, 100)
        append(ap, [last], T0 - 5)                                # the agent's last record, after the parse
        pairs.append((last, T0 - 5))

        def want(t):
            return sum(usd(r) for r, rt in pairs if rt >= t - 600)
        st0, fb0, err = em.record_cache_stats(), em.checkpoint_stats()["fallbacks"].get("corrupt", 0), io.StringIO()
        with mock.patch("sys.stderr", new=err):
            self.assertAlmostEqual(self.window(T0), want(T0), places=9)
            ent = self.entry(ap)
            self.assertEqual(em.checkpoint_stats()["fallbacks"].get("corrupt", 0) - fb0, 1, "the steps gate counted the document corrupt")
            self.assertIn("fold agentGist: ", err.getvalue(), "the fallback names the fold whose count it refused")
            self.assertFalse(cp.exists(), "and removed the document")
            self.assertIsNotNone(ent, "the whole entry the viewer's parse read outlasts the gate's fallback")
            self.assertEqual((ent[5], len(ent[4])), (0, 101))
            st1 = em.record_cache_stats()
            self.assertEqual((st1.get("pinnedKept", 0) - st0.get("pinnedKept", 0), st1["dropped"] - st0["dropped"]), (0, 0),
                             "nothing dropped or pinned yet: the agent wrote five seconds ago")
            self.assertAlmostEqual(self.window(T0 + 200), want(T0 + 200), places=9)   # the agent quiet for 205 s
            st2 = em.record_cache_stats()
            self.assertIs(self.entry(ap), ent, "the parse built before the last append is a whole assembly entry: the pin keeps it")
            self.assertEqual((st2.get("pinnedKept", 0) - st1.get("pinnedKept", 0), st2["dropped"] - st1["dropped"]), (1, 0),
                             "counted as kept for the pin, not as a drop")
            self.assertEqual(self.doc(ap)["folds"]["agentLaunchIds"]["count"], 101, "the quiet drop wrote a good document first")
            r0 = self.read_of(ap)
            parse()                                               # the viewer rebuilds
            self.assertEqual(self.read_of(ap), r0, "and reads nothing, as at 147100aa0, which kept the entry too")
            self.assertEqual(em.release_assembly(ap, rompuuid=SID), 1, "the viewer's close releases the parse")
            self.assertAlmostEqual(self.window(T0 + 205), want(T0 + 205), places=9)
        st3 = em.record_cache_stats()
        self.assertIsNone(self.entry(ap), "the parse released, the next quiet pass drops the entry")
        self.assertEqual((st3.get("pinnedKept", 0) - st2.get("pinnedKept", 0), st3["dropped"] - st2["dropped"]), (0, 1))
        self.assertEqual(self.doc(ap)["folds"]["agentLaunchIds"]["count"], 101, "its document stands")
        self.assertEqual(em.checkpoint_stats()["fallbacks"].get("corrupt", 0) - fb0, 1, "counted corrupt once")
        self.assertEqual(self.guard_whole_reads(), {"zero<-_spend_file_rows": 1},
                         "the guard's one whole read is the session leaf's first, at the first pass; it never read the agent file whole")

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
