#!/usr/bin/env python3
"""Two reads the jobs pass repeated on every pass for an answer its inputs had already settled (2026-10-25).

The reminder walk looks at a session waiting on its own background agents on every pass: that verdict ("awaiting-dispatch")
names no file event that would end it, so the look's memo never serves it. Each of those looks asked _session_awaiting, which
assembles every awaited row (the subagents sidecar map, which samples the session's whole subagents tree, one lstat per
directory, and each live agent's own file for the nesting) only for the look to test whether any row is there. The live row
settles that alone when its hook set names a live agent or its task set holds a launch the judge has not placed:
_awaiting_settled_by_row answers those, and the look asks _session_awaiting for everything else, as before.

The awaiting lift scanned every alive session's dispatches on every pass (a copy of every task the transcript ever launched)
to fingerprint inputs that, for a store with no stamp to rule on, cannot change its answer: _lift_decisions returns nothing
before it reads them. While the store, its override journal and the goals archive stand as they were when a read found
nothing to rule on (_lift_empty, keyed on their identities, stat-ed before the read), the session is settled before the scan.

Each class is an ORACLE over a scripted sequence: two sessions with mirrored worlds go through the same steps, one on the new
road and one with the settling turned off (main's road, exactly), and every step's outcome must be the same on both; the
saving is asserted on the steps the settling covers, so a kernel without it fails there. SYNTHETIC fixtures only."""
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
# Hermetic state BEFORE the loads — they resolve their state root at import time, and only
# pytest runs conftest's floor (a bare unittest or script run otherwise writes REAL state).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
km = load_source("romp_kernel_settledreads", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NEW = "11111111-2222-3333-4444-0000000e5e01"     # private synthetic sids: the new road
OLD = "11111111-2222-3333-4444-0000000e5e02"     # ...and main's road, the oracle
BORN, LAUNCH, LAUNCH2, STAMP, BACK, BACK2, NOW = 100, 200, 250, 300, 400, 450, 1000


def _iso(t):
    import datetime
    return datetime.datetime.fromtimestamp(t, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _launch(tid, t):
    return {"type": "user", "timestamp": _iso(t), "uuid": "u" + tid, "parentUuid": None,
            "toolUseResult": {"status": "async_launched", "description": "a dispatched check of the notes-api build"},
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": "launched"}]}}


def _append(path, row):
    with open(path, "a") as f:
        f.write(json.dumps(row) + "\n")


def _notification(tid, t):
    body = ("<task-notification>\n<task-id>%s</task-id>\n<tool-use-id>%s</tool-use-id>\n<status>completed</status>\n"
            "<summary>the check finished</summary>\n</task-notification>" % (tid, tid))
    return {"type": "user", "timestamp": _iso(t), "uuid": "n" + tid, "parentUuid": None,
            "message": {"role": "user", "content": body}}


class _World(unittest.TestCase):
    """A private state root (session hosts off) holding the two sessions' transcripts and subagents trees."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, True)
        (self.root / "session-hosts").write_text("off\n")            # a test that mints its own state root pins session hosts off
        saved = jd.STATE
        jd._rebind_state(self.root)
        self.addCleanup(jd._rebind_state, saved)
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        self.proj = self.root / "projects" / "notes-api"
        self.proj.mkdir(parents=True)
        self.path = {sid: str(self.proj / (sid + ".jsonl")) for sid in (NEW, OLD)}
        for sid in (NEW, OLD):
            Path(self.path[sid]).write_text("")
            sub = self.proj / sid / "subagents"
            wf = sub / "workflows" / "wf_0000000000000001"
            wf.mkdir(parents=True)
            for i, home in ((1, sub), (2, sub), (3, wf)):           # each agent's sidecar, one under a workflow directory
                (home / ("agent-a%016x.meta.json" % i)).write_text(json.dumps(
                    {"agentType": "general-purpose", "description": "check %d" % i, "spawnDepth": 1, "toolUseId": "toolu_%d" % i}))
                (home / ("agent-a%016x.jsonl" % i)).write_text(json.dumps(
                    {"type": "user", "uuid": "x%d" % i, "timestamp": _iso(LAUNCH), "message": {"role": "user", "content": "go"}}) + "\n")

    def mirrored(self, fn):
        for sid in (NEW, OLD):
            fn(sid)


class LookAwaitingGate(_World):
    """The look's awaiting gate on the new road against main's, step by step, over a session whose background work starts,
    is placed, ends, and is replaced by a watch and an overlay row."""

    def setUp(self):
        super().setUp()
        self.placed = set()
        stubs = dict(_session_flag=lambda sid, flag: False, _compacting_now=lambda sid, **k: False, _api_error=lambda path: None,
                     _backend_rewind_pending=lambda sid: False, _session_working=lambda turns: False,
                     _interrupt_suppresses_nudge=lambda turns, sid="", **k: False, _backend_queued=lambda sid: False,
                     _last_state=lambda sid: ("", 0), _closer_settled=lambda *a: False, _pending_ops={},
                     _bg_placed_tops=lambda sid, path, tids, store=None: {t: sid + ":g1" for t in tids if t in self.placed})
        p = mock.patch.multiple(km, **stubs)
        p.start()
        self.addCleanup(p.stop)
        turn = {"id": "t-1", "t": BORN, "end": BORN + 60, "ended": True, "atoms": [{"role": "user", "text": "check the build"}]}
        p = mock.patch.object(jd, "parsed_session", lambda sid, paths, now=None, **k: {"turns": [dict(turn)]})
        p.start()
        self.addCleanup(p.stop)
        real = km._awaiting_settled_by_row
        p = mock.patch.object(km, "_awaiting_settled_by_row",           # main's road for the oracle session: never settled
                              lambda sid, path, row: False if sid == OLD else real(sid, path, row))
        p.start()
        self.addCleanup(p.stop)
        self.reads = []
        for name in ("_subagent_meta_map", "_agent_launch_read"):        # the reads the assembly makes and the settling skips
            fn = getattr(km, name)
            p = mock.patch.object(km, name, (lambda fn, name: lambda path, *a, **k: (self.reads.append((name, str(path))),
                                                                                    fn(path, *a, **k))[1])(fn, name))
            p.start()
            self.addCleanup(p.stop)
        saved_watches = list(km._watches)
        saved_horizon = dict(km._NUDGE_HORIZON.__dict__)               # the raw look leaves its collector's marks on this thread
        self.addCleanup(lambda: (km._NUDGE_HORIZON.__dict__.clear(), km._NUDGE_HORIZON.__dict__.update(saved_horizon)))
        self.addCleanup(lambda: (km._watches.clear(), km._watches.extend(saved_watches)))
        self.addCleanup(setattr, km._live_scope, "snapshot", None)

    def _look(self, live):
        """Each session's verdict from the raw look (the parse gate's memo is the walk's, not this gate's), the reads that
        look made, _session_awaiting's own answer and the live row's settling, all with the pass's live map served as the
        pass serves it."""
        out = {}
        for sid in (NEW, OLD):
            del self.reads[:]
            km._live_scope.snapshot = live
            try:
                s = {"sid": sid, "path": self.path[sid], "name": sid[-4:], "mtime": 0}
                v = km._auto_nudge_session.__wrapped__(s, NOW, live, {}, {}, alive_ids={NEW, OLD}, wake_only=False,
                                                       cleared=set(), reminders=False)
                reads = len([r for r in self.reads if r[1].startswith(str(self.proj))])
                oracle = km._session_awaiting(sid, self.path[sid], True)
                settled = _real_settled(sid, self.path[sid], live.get(sid))
            finally:
                km._live_scope.snapshot = None
            out[sid] = (v, reads, bool(oracle), settled)
        return out

    def test_the_verdicts_match_mains_at_every_step_and_the_settled_steps_read_no_tree(self):
        agents = lambda sid: [{"type": "general-purpose", "since": LAUNCH, "agentId": "a%016x" % i} for i in (1, 2)]
        steps = [   # (name, the step's world for a session, the expected verdict, whether the row settles it)
            ("idle", lambda sid: {"state": "waiting", "subagents": [], "bgTasks": []}, "closer-unsettled", False),
            ("a workflow launched, not placed",
             lambda sid: {"state": "waiting", "subagents": [], "bgTasks": [
                 {"toolUseId": "toolu_w", "desc": "Running review", "since": LAUNCH, "type": "local_workflow", "taskId": "wf-1"}]},
             "awaiting-dispatch", True),
            ("two background agents",
             lambda sid: {"state": "waiting", "subagents": agents(sid), "bgTasks": [
                 {"toolUseId": "toolu_%d" % i, "desc": "Running check", "since": LAUNCH, "type": "local_agent",
                  "taskId": "a%016x" % i} for i in (1, 2)]},
             "awaiting-dispatch", True),
            ("the workflow placed by the judge",
             lambda sid: (self.placed.add("toolu_w"), {"state": "waiting", "subagents": [], "bgTasks": [
                 {"toolUseId": "toolu_w", "desc": "Running review", "since": LAUNCH, "type": "local_workflow", "taskId": "wf-1"}]})[1],
             "closer-unsettled", False),
            ("hook entries that are not rows", lambda sid: {"state": "waiting", "subagents": ["a1", 7], "bgTasks": []},
             "closer-unsettled", False),
            ("a hook row with no agent id", lambda sid: {"state": "waiting", "subagents": [{"type": "general-purpose"}], "bgTasks": []},
             "awaiting-dispatch", True),
            ("a monitor past its recorded deadline",
             lambda sid: ((jd.STATE / "sdk").mkdir(exist_ok=True),
                          (jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps(
                              {"bgLedger": [{"toolUseId": "toolu_m", "deadlineEpoch": time.time() - 3600}]})),
                          {"state": "waiting", "subagents": [], "bgTasks": [
                              {"toolUseId": "toolu_m", "desc": "watch the build", "since": LAUNCH, "type": "local_bash"}]})[2],
             "closer-unsettled", False),
            ("an armed watch", lambda sid: (km._watches.append({"id": "w-" + sid[-4:], "cmd": "make check", "sid": sid, "at": LAUNCH}),
                                            {"state": "waiting", "subagents": [], "bgTasks": []})[1],
             "awaiting-dispatch", False),
            ("an awaiting overlay row",
             lambda sid: (km._watches.clear(), (jd.STATE / "states").mkdir(exist_ok=True),
                          (jd.STATE / "states" / (sid + ".jsonl")).write_text(json.dumps(
                              {"t": BACK, "awaiting": True, "why": "waiting on the release build"}) + "\n"),
                          {"state": "waiting", "subagents": [], "bgTasks": []})[3],
             "awaiting-dispatch", False),
            ("the overlay's clearing row",
             lambda sid: (_append(jd.STATE / "states" / (sid + ".jsonl"), {"t": BACK2, "awaiting": False}),
                          {"state": "waiting", "subagents": [], "bgTasks": []})[1],
             "closer-unsettled", False),
        ]
        for name, world, want, settled in steps:
            live = {sid: world(sid) for sid in (NEW, OLD)}
            got = self._look(live)
            (v_new, reads_new, oracle_new, settled_new), (v_old, reads_old, oracle_old, _s) = got[NEW], got[OLD]
            self.assertEqual(v_new, v_old, "%s: the new road's verdict is main's" % name)
            self.assertEqual(v_new, want, name)
            self.assertEqual(v_new == "awaiting-dispatch", oracle_new, "%s: the verdict is _session_awaiting's own answer" % name)
            self.assertEqual(oracle_new, oracle_old, name)
            self.assertEqual(settled_new, settled, name)
            if settled_new:
                self.assertTrue(oracle_new, "%s: settled only where _session_awaiting answers awaiting" % name)
            if settled:
                self.assertEqual(reads_new, 0, "%s: the live row settled it, so the look read no sidecar map or agent file" % name)

    def test_mains_road_reads_the_tree_for_the_steps_the_row_settles(self):
        """The saving is real: main's road assembles the rows (the sidecar map for a workflow's task, which carries no agent
        id, and the nesting's reads for two agents) where the new road reads nothing."""
        wf = {sid: {"state": "waiting", "subagents": [], "bgTasks": [
            {"toolUseId": "toolu_w", "desc": "Running review", "since": LAUNCH, "type": "local_workflow", "taskId": "wf-1"}]}
            for sid in (NEW, OLD)}
        got = self._look(wf)
        self.assertEqual((got[NEW][0], got[OLD][0]), ("awaiting-dispatch", "awaiting-dispatch"))
        self.assertEqual(got[NEW][1], 0)
        self.assertGreater(got[OLD][1], 0, "main's road read the sidecar map for the workflow's task")


class LiftEmptyStore(_World):
    """The lift on the new road against main's, step by step, over a store that is absent, then unstamped while its session's
    dispatch returns, then stamped, lifted, restored from the journal and rolled up."""

    def setUp(self):
        super().setUp()
        stubs = dict(_alive_sessions=lambda now, live_map: [{"sid": sid, "path": self.path[sid]} for sid in (NEW, OLD)],
                     _mark_views_dirty=lambda *a, **k: None)
        p = mock.patch.multiple(km, **stubs)
        p.start()
        self.addCleanup(p.stop)
        real = km._lift_store_ident
        p = mock.patch.object(km, "_lift_store_ident", lambda sid: None if sid == OLD else real(sid))   # main's road: never settled
        p.start()
        self.addCleanup(p.stop)
        self.scans = []
        scan = km._bg_scan_all_cached
        p = mock.patch.object(km, "_bg_scan_all_cached", lambda path: (self.scans.append(str(path)), scan(path))[1])
        p.start()
        self.addCleanup(p.stop)

        def clear():
            km._lift_seen.clear(); km._lift_empty.clear(); km._bgall_cache.clear(); km._bgtasks_cache.clear()
            km._SESSION_STAMP_CACHE.clear(); km._task_seg_cache.clear(); km._BG_TOPS_CACHE.clear(); km._PLACEMENT_IDX.clear()
            jd._shared_clear()
        clear()
        self.addCleanup(clear)
        self.records = {sid: [] for sid in (NEW, OLD)}

    def _transcript(self, sid, *recs):
        self.records[sid].extend(recs)
        with open(self.path[sid], "w") as f:
            for r in self.records[sid]:
                f.write(json.dumps(r) + "\n")

    def _node(self, sid, n, **kw):
        nd = {"id": "%s:g%d" % (sid, n), "text": "ship the notes-api fix %d" % n, "parentId": None, "nodeComplete": False,
              "blocked": False, "cleared": False, "trail": [], "t": BORN, "mt": BORN, "log": []}
        nd.update(kw)
        return nd

    def _stamp(self, sid, n, why="waiting on the dispatched checks", kind=None, **kw):
        log = [{"ev_t": STAMP, "src": "closer", "kind": "awaiting", "why": why, "at": STAMP}]
        extra = {"awaitingKind": kind} if kind else {}
        return self._node(sid, n, awaitingWhy=why, awaitingAt=STAMP, log=log, **dict(extra, **kw))

    def _write_store(self, sid, nodes, publish=False):
        store = {"rompUuid": sid, "seq": len(nodes), "placements": {}, "status": {}, "nodes": {nd["id"]: nd for nd in nodes}}
        if publish:
            jd.save_goals(sid, store)                                  # a rename publish, as the closer's
        else:
            (jd.GOALDIR / (sid + ".json")).write_text(json.dumps(store))

    def _view(self, sid):
        """The store as the lift left it, the session's own id taken off every node key: the stamps and every awaiting row
        (its lift mark, evidence time and horizon; never its wall-clock `at`)."""
        p = jd.GOALDIR / (sid + ".json")
        if not p.exists():
            return None
        nodes = json.loads(p.read_text()).get("nodes") or {}
        return {k.split(":", 1)[1]: (nd.get("awaitingWhy"), bool(nd.get("rolledUp")),
                                     [(e.get("ev_t"), bool(e.get("lift")), e.get("end_ev")) for e in nd.get("log") or []
                                      if e.get("kind") == "awaiting"])
                for k, nd in nodes.items()}

    def _tick(self):
        del self.scans[:]
        e0 = km._lift_gate_stats["empty"]
        km._lift_spent_awaiting(NOW, {sid: {"state": "waiting"} for sid in (NEW, OLD)})
        return {sid: sum(1 for p in self.scans if p == self.path[sid]) for sid in (NEW, OLD)}, km._lift_gate_stats["empty"] - e0

    def _publish(self, sid, *nodes):
        """A publish through the writer (load, add, save_goals: a rename), as the closer's."""
        store = jd.load_goals(sid)
        for nd in nodes:
            store["nodes"][nd["id"]] = nd
        jd.save_goals(sid, store)

    def test_the_stores_match_mains_at_every_step_and_an_empty_store_takes_no_scan(self):
        steps = [   # (name, the step's change for a session, whether the new road settles it before the scan)
            ("a dispatch out, no store yet", lambda sid: self._transcript(sid, _launch("t1", LAUNCH)), False),
            ("the absent store, unchanged", lambda sid: None, True),
            ("an unstamped goal written", lambda sid: self._write_store(sid, [self._node(sid, 1)]), False),
            ("a quiet pass", lambda sid: None, True),
            # the archive is one of the view's three files, so its move unsettles the session; main's fingerprint does not
            # carry it and rules again only when another input moves, so the new road takes main's road until that ruling
            ("the goals archive rewritten",
             lambda sid: (jd.GOALARCHDIR.mkdir(parents=True, exist_ok=True),
                          (jd.GOALARCHDIR / (sid + ".json")).write_text(json.dumps({"nodes": {}}))), False),
            ("a quiet pass after it: main's road until its next ruling", lambda sid: None, False),
            ("the dispatch returns: main rules, and the store is settled again",
             lambda sid: self._transcript(sid, _notification("t1", BACK)), False),
            ("a quiet pass after that ruling", lambda sid: None, True),
            ("a stamped goal published, its second dispatch out",
             lambda sid: (self._transcript(sid, _launch("t2", LAUNCH2)), self._publish(sid, self._stamp(sid, 2))), False),
            ("a quiet pass over the stamp", lambda sid: None, False),
            ("the second dispatch returns: the lift", lambda sid: self._transcript(sid, _notification("t2", BACK2)), False),
            ("after the lift's own publish", lambda sid: None, False),
            ("settled again", lambda sid: None, True),
            ("a rolled-up stamp published: its lift",
             lambda sid: self._publish(sid, self._stamp(sid, 4, why="a wait the roll-down froze", nodeComplete=True, rolledUp=True)),
             False),
            ("after the rolled-up lift", lambda sid: None, False),
            ("settled after it", lambda sid: None, True),
            ("a stamp restored through the journal",
             lambda sid: jd.append_restore(sid, {"%s:g3" % sid: self._node(
                 sid, 3, t=500, mt=500, awaitingWhy="waiting on the web session's answer", awaitingAt=600, awaitingKind="peer",
                 log=[{"ev_t": 600, "src": "closer", "kind": "awaiting", "why": "waiting on the web session's answer", "at": 600}])},
                 {}, BACK2 + 10), False),
            ("a block row on the journal", lambda sid: jd.append_block(sid, "%s:g1" % sid, "nudge", "no answer", BACK2 + 20), False),
            ("a quiet pass over the restored stamp", lambda sid: None, False),
        ]
        seen, lifted_at = set(), []
        for name, change, settled in steps:
            self.mirrored(change)
            scans, empty = self._tick()
            self.assertEqual(self._view(NEW), self._view(OLD), "%s: the new road's store is main's" % name)
            self.assertGreater(scans[OLD], 0, "%s: main's road scans every pass" % name)
            if settled:
                self.assertEqual((scans[NEW], empty), (0, 1), "%s: an empty store, unmoved, settles the session before the scan"
                                 % name)
            else:
                self.assertGreater(scans[NEW], 0, "%s: a store that moved, or one with a stamp, takes main's road" % name)
            lifts = {(k, row[0]) for k, v in (self._view(NEW) or {}).items() for row in v[2] if row[1]}
            if lifts - seen:
                lifted_at.append(name)
            seen |= lifts
        self.assertEqual(lifted_at, ["the second dispatch returns: the lift", "a rolled-up stamp published: its lift"],
                         "the sequence lifted the dispatch stamp and the rolled-up one, on both roads alike")
        for sid in (NEW, OLD):
            self.assertTrue(jd.load_goals(sid)["nodes"]["%s:g3" % sid].get("awaitingWhy"), "the restored stamp stands on both roads")

    def test_an_identity_that_cannot_be_read_is_never_settled(self):
        """A stat that fails, or something other than a file at one of the three paths, gives no identity: nothing is served."""
        self.assertEqual(_real_ident(NEW), (None, None, None), "three absent files are an identity")
        with mock.patch.object(km.os, "stat", side_effect=PermissionError("synthetic")):
            self.assertIsNone(_real_ident(NEW))
        (jd.GOALDIR / (NEW + ".json")).mkdir()
        self.assertIsNone(_real_ident(NEW), "a directory in the store's place")

    def test_a_session_that_left_the_alive_set_leaves_the_memo(self):
        self._write_store(NEW, [self._node(NEW, 1)])
        self._tick()
        self.assertIn(NEW, km._lift_empty)
        with mock.patch.object(km, "_alive_sessions", lambda now, live_map: []):
            km._lift_spent_awaiting(NOW, {})
        self.assertNotIn(NEW, km._lift_empty)


_real_ident = km._lift_store_ident
_real_settled = km._awaiting_settled_by_row


if __name__ == "__main__":
    unittest.main()
