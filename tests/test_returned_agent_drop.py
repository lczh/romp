#!/usr/bin/env python3
"""A finished subagent's records leave memory on a quiet fold too (the kernel memory work, 2026-10-01).

The record cache holds a file's decoded records, about three times the file's size. A fold with drop_after="quiescent"
let a file's records go only when it STEPPED records over a file unchanged for two minutes. A subagent the kernel follows
live writes its last record while the file is still fresh, so every later fold finds nothing new and its whole entry
stayed for the kernel's life. The spend guard read every agent file in its window through the reader with no fold at
all, so a workflow agent (no card, no fold) stayed resident too. Pinned here:
  * drop_after="returned" (a subagent's own transcript): a fold that finds nothing new pops a quiescent entry that holds
    records once the file's document stands at the entry's witness; the next fold restores from the document over a
    zero-byte tail, never a whole read; the restored empty tail stays, so later folds are stat-cost hits; a sibling
    cursor behind the entry holds the pop; with checkpoints off nothing pops; "quiescent" (the leaf's policy) is unchanged;
  * the kernel's agent-file folds take the new policy and the parent-leaf fold does not;
  * the spend guard routes its agent reads through the dropping fold, prices a dropped file from its memo without entering
    it again, keeps a resumed agent's earlier rows in the window, and prices a file a fresh process restored as a tail;
  * a lab-style run of a synthetic session (agents with cards, workflow agents without) on an advancing clock: the entries
    holding records and their bytes return to the live set as agents return, the workflow agents' entries stop climbing,
    and the guard's window equals a reference sum at every cycle.
SYNTHETIC fixtures only: placeholder ids, the notes-api demo names, transcripts written here."""
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
        em._RECORD_CACHE_STATS["dropped"] = 0
        em._RECORD_CACHE_STATS["droppedBytes"] = 0

    def entry(self, p):
        with em._JSONL_CACHE_LOCK:
            return em._JSONL_CACHE.get(str(p))

    def read_of(self, p):
        with em._READ_BYTES_LOCK:
            return em._READ_BYTES.get(str(p), 0)

    def doc(self, p):
        return json.loads(em._ckpt_file(str(p)).read_text())


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
        kinds = []
        self.assertEqual(len(self.fold(b, p, name="synthB", kinds=kinds)), 20)
        self.assertEqual(kinds[-1], "append")
        self.assertIsNone(self.entry(p), "b stepped the rest, and its drop takes the entry")


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
        early = [rec(T0 - 400 + 5 * i, i) for i in range(10)]    # T0-400 .. T0-355
        write(ap, early, T0 - 355)
        self.assertAlmostEqual(self.window(T0), sum(map(usd, early)), places=9)
        self.assertIsNone(self.entry(ap), "quiet for six minutes at the guard's first look: dropped as it was folded")
        late = [rec(T0 + 5 + i, 100 + i) for i in range(3)]
        append(ap, late, T0 + 8)                                  # the agent resumes
        self.assertAlmostEqual(self.window(T0 + 10), sum(map(usd, early + late)), places=9,
                               msg="the earlier rows stay in the window: the resumed file is read whole once")

    def test_a_fresh_process_prices_an_agent_file_it_restored_as_a_tail(self):
        ap = str(self.sub / "agent-a8888888888888888.jsonl")
        recs = [rec(T0 - 300 + 5 * i, i) for i in range(10)]
        write(ap, recs, T0 - 255)
        km._agent_steps(ap)                                       # a card's fold over a returned agent: the drop writes the document
        self.assertTrue(em._ckpt_file(ap).exists())
        self.fresh_process()                                      # a restart: the memo and the entry gone, the document stands
        self.assertAlmostEqual(self.window(T0), sum(map(usd, recs)), places=9,
                               msg="the restored tail holds none of the window's records: the guard reads the file whole")


class Lab(Base):
    """A synthetic session on an advancing clock, five seconds a cycle. Twenty-four agents start a minute apart, half of
    them Agent cards (their card's two folds run every cycle while their turn is open, until five minutes after they
    return) and half workflow agents (no card: the spend guard is their only reader); each writes a record a cycle for 90 s.
    Every cycle runs the spend guard over the session's tree, as the pusher does."""

    CYCLE = 5

    def snapshot(self, t, agents):
        with em._JSONL_CACHE_LOCK:
            ents = {p: e for p, e in em._JSONL_CACHE.items() if p != self.leaf}
        wf = {a["path"] for a in agents if a["kind"] == "workflow"}
        return {"t": int(t - T0), "spawned": sum(1 for a in agents if a["start"] <= t),
                "writing": sum(1 for a in agents if a["start"] <= t < a["end"]),
                "rcEntries": len(ents), "withRecords": sum(1 for e in ents.values() if e[4]),
                "bytes": sum(em._entry_weight(e) for e in ents.values()),
                "workflowEntries": sum(1 for p in ents if p in wf), "dropped": em._RECORD_CACHE_STATS["dropped"]}

    def test_finished_agents_leave_the_record_cache(self):
        agents = []
        for i in range(24):
            kind = "task" if i % 2 == 0 else "workflow"
            aid = "a%016x" % (0xd0000 + i)
            p = (self.sub / ("agent-%s.jsonl" % aid) if kind == "task"
                 else self.sub / "workflows" / ("wf_%d" % i) / ("agent-%s.jsonl" % aid))
            agents.append({"path": str(p), "kind": kind, "start": T0 + 60 * i, "end": T0 + 60 * i + 90, "recs": []})
        t_end = agents[-1]["end"] + 900
        series, t = [], T0
        while t <= t_end:
            self.clock.t = t
            em.checkpoint_cycle_begin(64 * 1024 * 1024)          # the pusher begins each cycle's checkpoint budget
            for a in agents:
                if a["start"] <= t < a["end"]:
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
            for a in agents:
                if a["kind"] == "task" and a["recs"] and t <= a["end"] + 300:
                    km._agent_steps(a["path"])
                    km._agent_launch_ids(a["path"])
            got = km._spend_window_usd(self.leaf, t, 600, PRICES)
            want = sum(usd(r) for a in agents for r, rt in a["recs"] if rt >= t - 600)
            self.assertAlmostEqual(got, want, places=9, msg="the guard's window at +%d s" % (t - T0))
            if (t - T0) % 300 == 0 or t == t_end:
                series.append(self.snapshot(t, agents))
            t += self.CYCLE
        print("LAB " + json.dumps(series, separators=(",", ":")))
        final = series[-1]
        sizes = {a["path"]: os.path.getsize(a["path"]) for a in agents}
        print("LAB final=%s agentBytesOnDisk=%d" % (json.dumps(final), sum(sizes.values())))
        for s in series:                                          # at most the agents written to in the last two minutes hold records
            tt = s["t"] + T0
            recent = sum(1 for a in agents if a["recs"] and tt - a["recs"][-1][1] < 120 + self.CYCLE)
            self.assertLessEqual(s["withRecords"], recent, "at +%d s: %s" % (s["t"], s))
        self.assertEqual((final["withRecords"], final["bytes"]), (0, 0), "every returned agent's records left memory: %s" % final)
        self.assertEqual(final["workflowEntries"], 0, "the agents only the guard reads leave no entry at all")
        self.assertGreaterEqual(final["dropped"], len(agents))
        for a in agents:
            self.assertLess(self.read_of(a["path"]), 2 * sizes[a["path"]], "no agent file read whole twice: %s" % a["path"])


if __name__ == "__main__":
    unittest.main()
