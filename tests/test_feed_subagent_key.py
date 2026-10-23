#!/usr/bin/env python3
"""The feed key reads only the subagents trees and agent files its session's derivation read (2026-10-23).

The per-session card memo (_feed_session_key, T368) carried a `subagents` component taken from every living session's
whole subagents tree on every build: one lstat per directory, the root and every workflows/wf_<id>/ directory under it,
whether or not that session's cards read anything there. A derivation reads a tree only for a session that is idle with
live agents (_session_awaiting -> _awaiting_live_rows -> _subagent_meta_map, and _awaiting_nest's transcript join over the
open agents' own files). On a machine whose sessions run many workflows the trees hold hundreds of directories, and each
lstat gives up the interpreter lock, so the pusher paid a lock hand-off per directory per build while the judge threads
were busy. Now the component is a dependency one: a derivation records what it read, and the next build's key re-takes
exactly that.

Pinned here, over the REAL build_feed on a three-session board (the notes-api demo world: web, api and tests, each idle
with a warm parse and a subagents tree of workflow directories):
  * an unchanged rebuild of a board where no session has live agents takes no lstat under any subagents tree, and serves
    every session from the memo (red on the base: one lstat per directory of every tree, every build);
  * a session idle with two live agents keys its own tree alone: an unchanged rebuild lstats its directories and no
    other session's; a sidecar landing in a nested workflow directory re-derives it under `subagents` and the agent
    nests under its launcher, the board equal to a from-scratch build;
  * a session idle with ONE live agent reads no tree at all (no agent can nest under itself, so _awaiting_nest skips
    the sidecar map; the rows are the same), and its rebuild takes no lstat;
  * an open agent's file is keyed by its launch ids: a background command's launch landing in the agent's file nests
    the command under the agent at the next build (red on the base, whose key never read the file), while the agent's
    other writes re-derive nothing;
  * the identities kept for a derivation are the ones its read was taken under: a sidecar landing after the read and
    before the key is stored is seen at the next build (the stat-then-read order);
  * an oracle: seeded sequences of agents starting and stopping, sidecars landing late, agent files growing with and
    without launches, commands starting and stopping and workflow directories appearing, inside and outside a pusher
    cycle's scope; after every step the memoized board equals a from-scratch build, byte for byte.

Synthetic fixtures only: private synthetic sids (the goal-store fixture rule), invented agent ids and text, a temp
directory."""
import collections
import json
import os
import random
import re
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
BIN = os.path.join(os.path.dirname(HERE), "bin")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
# Hermetic state BEFORE the load: the kernel resolves its state root at import time, and only pytest runs conftest's
# floor. The root minted here is outside conftest's belt, so session hosts are switched off in it too.
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
km = load_source("romp_kernel_feed_subagent_key", os.path.join(BIN, "romp-kernel"))
jd = km.jd

# This module's PRIVATE synthetic sids (never the shared placeholder: load_goals replays the per-sid override journal).
SIDS = ("6a4b3c2d-1e0f-4a98-8765-4321fedcb001", "6a4b3c2d-1e0f-4a98-8765-4321fedcb002",
        "6a4b3c2d-1e0f-4a98-8765-4321fedcb003")
WEB, SRV, TESTS = SIDS
NAME_OF = {WEB: "web", SRV: "api", TESTS: "tests"}
COLOR_OF = {WEB: "#1EA1EB", SRV: "#E67E22", TESTS: "#2ECC71"}
GOAL_OF = {WEB: "wire the notes-api web client", SRV: "add the notes-api list endpoint",
           TESTS: "cover the notes-api list endpoint"}
NOW = 1781100000
T0 = NOW - 3600
WORKFLOWS = 30                          # workflow directories per session's tree


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


def aid_of(n):
    return "a%016x" % n


def _dump(feed):
    """The payload's serialization with the declared clock fields stripped: the dedup's comparison."""
    return json.dumps({k: v for k, v in feed.items() if k not in km._DEDUP_VOLATILE}, sort_keys=True, default=str)


def _reset_memo():
    """The memo empty and its counters at zero, the card anchor walk's held aggregates with it: a from-scratch build."""
    with km._FEED_SEG_LOCK:
        km._FEED_SEG_HELD.clear()
    with km._feed_memo_lock:
        km._feed_memo.clear()
        st = km._FEED_MEMO_STATS
        for k in ("hit", "miss", "evict", "derived", "entries", "bytes", "failed", "coldLive", "coldFlip"):
            st[k] = 0
        for k in st["miss_by"]:
            st["miss_by"][k] = 0
        for k in st["row_by"]:
            st["row_by"][k] = 0
        km._FEED_DERIVE_FAILED.clear()


def _memo_snapshot():
    with km._feed_memo_lock:
        snap = (dict(km._feed_memo), json.loads(json.dumps(km._FEED_MEMO_STATS)), dict(km._FEED_DERIVE_FAILED))
    with km._FEED_SEG_LOCK:
        snap += (collections.OrderedDict(km._FEED_SEG_HELD), dict(km._FEED_SEG_STATS))
    return snap


def _memo_restore(snap):
    entries, stats, failed, held, seg = snap
    with km._FEED_SEG_LOCK:
        km._FEED_SEG_HELD.clear()
        km._FEED_SEG_HELD.update(held)
        km._FEED_SEG_STATS.clear()
        km._FEED_SEG_STATS.update(seg)
    with km._feed_memo_lock:
        km._feed_memo.clear()
        km._feed_memo.update(entries)
        km._FEED_MEMO_STATS.clear()
        km._FEED_MEMO_STATS.update(stats)
        km._FEED_DERIVE_FAILED.clear()
        km._FEED_DERIVE_FAILED.update(failed)


class _Calls:
    """Counts os.lstat, os.stat and os.scandir calls on paths under the given roots, by root, while open."""

    def __init__(self, roots):
        self.roots = {str(r).rstrip(os.sep) + os.sep: str(r) for r in roots}
        self.lstat = collections.Counter()
        self.other = collections.Counter()

    def _root(self, p):
        try:
            s = os.fsdecode(p) if not isinstance(p, int) else ""
        except TypeError:
            return None
        for pre, r in self.roots.items():
            if s == r or s.startswith(pre):
                return r
        return None

    def __enter__(self):
        real_lstat, real_stat, real_scandir = os.lstat, os.stat, os.scandir

        def lstat(p, *a, **k):
            r = self._root(p)
            if r is not None:
                self.lstat[r] += 1
            return real_lstat(p, *a, **k)

        def stat_(p, *a, **k):
            r = self._root(p)
            if r is not None:
                self.other[r] += 1
            return real_stat(p, *a, **k)

        def scandir(p=".", *a, **k):
            r = self._root(p)
            if r is not None:
                self.other[r] += 1
            return real_scandir(p, *a, **k)

        self._patches = [mock.patch.object(os, "lstat", lstat), mock.patch.object(os, "stat", stat_),
                         mock.patch.object(os, "scandir", scandir)]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False


class _World(unittest.TestCase):
    """Three idle sessions with warm parses, a goal each, and a subagents tree each: WORKFLOWS workflow directories
    holding one finished agent apiece, plus one flat finished agent. The racy window is closed (the walk memo's tests
    close it the same way), and every directory change is followed by a back-dated stamp unique to it, so a change is
    never hidden inside one clock tick."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        td = Path(self.td.name)
        (td / "session-hosts").write_text("off\n")
        proj = td / "projects"
        names = td / "names"
        names.mkdir()
        self.tpath, self.root = {}, {}
        self._stamp = time.time_ns() - 500 * 1_000_000_000   # the back-dated stamps, unique and increasing
        self._n = 0x100                                        # the next invented agent number
        for sid in SIDS:
            cdir = td / ("launch-" + NAME_OF[sid])
            cdir.mkdir()
            pdir = proj / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(str(cdir)))
            pdir.mkdir(parents=True)
            tp = pdir / (sid + ".jsonl")
            tp.write_text(self._transcript_body(sid))
            (names / sid).write_text("%s\t%s\t%s\n" % (NAME_OF[sid], cdir, COLOR_OF[sid]))
            self.tpath[sid] = tp
            self.root[sid] = Path(km._subagents_dir(tp))
            self._tree(sid)
        self.saved = (jd.STATE, jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD)
        self.saved_memo = _memo_snapshot()
        jd._rebind_state(td)
        jd.PROJECTS = proj
        km.NAMES = jd.NAMES
        km._GLOBAL_CLAUDE_MD = td / "no-global-claude.md"
        self._racy = mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0)
        self._racy.start()
        km._live_scope.names = None
        km._live_scope.subagent_trees = None
        for sid in SIDS:
            self._mint(sid, GOAL_OF[sid])
        self.live = {sid: self._row() for sid in SIDS}
        km._live_scope.snapshot = self.live      # _session_awaiting and _bg_live_norm read the row off _live_map()
        for sid in SIDS:
            km._parse(str(self.tpath[sid]), sid, NOW)        # warm: the feed reads the parse cache-only
        jd._discover_cache.clear()
        km.build_feed(NOW - 1, self.live)        # the first sight adopts every session into the persisted order
        _reset_memo()

    def tearDown(self):
        self._racy.stop()
        _memo_restore(self.saved_memo)
        jd._rebind_state(self.saved[0])
        jd.PROJECTS, km.NAMES, km._GLOBAL_CLAUDE_MD = self.saved[1:]
        km._live_scope.names = None
        km._live_scope.snapshot = None
        km._live_scope.subagent_trees = None
        jd._discover_cache.clear()
        self.td.cleanup()

    # ── the world's writers ──
    @staticmethod
    def _transcript_body(sid):
        return "".join(json.dumps(r) + "\n" for r in [
            {"type": "user", "timestamp": iso(T0), "uuid": "u1", "parentUuid": None, "promptSource": "typed",
             "message": {"role": "user", "content": "start on: " + GOAL_OF[sid]}},
            {"type": "assistant", "timestamp": iso(T0 + 40), "uuid": "a1", "parentUuid": "u1",
             "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}], "stop_reason": "end_turn"}},
        ])

    @staticmethod
    def _row():
        return {"state": "idle", "since": NOW - 100, "model": "", "effort": "", "context": None,
                "compactPct": None, "color": None}

    @staticmethod
    def _mint(sid, text):
        s = {"rompUuid": sid, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}
        jd.apply_plan(s, "s1", T0, [{"do": "mint", "why": "the request that opened the session", "text": text}], [])
        jd.rollup_status(s, session_closed=False)
        jd.save_goals(sid, s)

    def _bump(self, d):
        """Stamp the directory `d` with a back-dated time no other change used: its identity moves, and it is vouched."""
        self._stamp += 1_000_000_000
        os.utime(str(d), ns=(self._stamp, self._stamp))

    def _new_aid(self):
        self._n += 1
        return aid_of(self._n)

    def _agent(self, sid, where, aid=None, parent=None, meta=True, records=()):
        """An agent's file (and its sidecar unless meta is False) in the directory `where` of sid's tree."""
        aid = aid or self._new_aid()
        d = Path(where)
        if not d.exists():
            d.mkdir(parents=True)
            self._bump(d.parent)                        # a new workflow directory moves its parent
        (d / ("agent-%s.jsonl" % aid)).write_text("".join(json.dumps(r) + "\n" for r in records))
        if meta:
            self._sidecar(d, aid, parent, bump=False)
        self._bump(d)
        return aid

    def _sidecar(self, d, aid, parent=None, bump=True):
        m = {"agentType": "general-purpose", "description": "audit step %s" % aid[-3:], "spawnDepth": 1,
             "toolUseId": "toolu_" + aid[1:]}
        if parent:
            m["parentAgentId"] = parent
        (Path(d) / ("agent-%s.meta.json" % aid)).write_text(json.dumps(m))
        if bump:
            self._bump(d)

    def _tree(self, sid):
        root = self.root[sid]
        wfs = root / "workflows"
        wfs.mkdir(parents=True)
        self._agent(sid, root)
        for i in range(WORKFLOWS):
            self._agent(sid, wfs / ("wf_%016x" % (int(sid[-3:], 16) * 1000 + i)))
        self._bump(wfs)
        self._bump(root)

    def _dirs(self, sid):
        return [r for r, _ds, _fs in os.walk(str(self.root[sid]))]

    @staticmethod
    def _launch(tid, name="Bash"):
        inp = ({"run_in_background": True, "command": "uv run pytest tests/test_list.py -q", "description": "run the list tests"}
               if name == "Bash" else {"description": "check the list endpoint", "prompt": "read the handler"})
        return {"type": "assistant", "timestamp": iso(NOW - 20), "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": tid, "name": name, "input": inp}]}}

    @staticmethod
    def _noise(i):
        return {"type": "assistant", "timestamp": iso(NOW - 10), "message": {"role": "assistant", "content": [
            {"type": "text", "text": "step %d of the audit" % i}]}}

    def _append(self, path, rec):
        with open(path, "a") as fh:
            fh.write(json.dumps(rec) + "\n")

    def _live_agent(self, sid, aid, since=None):
        self.live[sid] = dict(self.live[sid], subagents=list(self.live[sid].get("subagents") or ()) + [
            {"type": "general-purpose", "since": since or NOW - 60, "agentId": aid}])

    def _command(self, sid, tid, since=None):
        self.live[sid] = dict(self.live[sid], bgTasks=list(self.live[sid].get("bgTasks") or ()) + [
            {"toolUseId": tid, "type": "local_bash", "desc": "run the list tests", "since": since or NOW - 30}])
        km._live_scope.snapshot = self.live

    # ── the reads ──
    def _build(self, scope=True):
        """One build at the fixed clock; inside a pusher cycle's subagents-tree scope when `scope`, as the pusher builds."""
        km._live_scope.snapshot = self.live
        km._live_scope.subagent_trees = {} if scope else None
        try:
            return km.build_feed(NOW, self.live)
        finally:
            km._live_scope.subagent_trees = None

    def _delta(self, fn):
        b = km._feed_memo_report()
        bm = dict(b["miss_by"])
        out = fn()
        a = km._feed_memo_report()
        d = {k: a[k] - b[k] for k in ("hit", "miss", "derived")}
        d["miss_by"] = {k: v - bm.get(k, 0) for k, v in a["miss_by"].items() if v - bm.get(k, 0)}
        return d, out

    def _scratch(self, scope=True):
        """The same board built with nothing memoized, the memo handed back after."""
        snap = _memo_snapshot()
        _reset_memo()
        try:
            return self._build(scope)
        finally:
            _memo_restore(snap)

    def _counted(self, scope=True):
        with _Calls(self.root.values()) as c:
            d, f = self._delta(lambda: self._build(scope))
        return c, d, f

    @staticmethod
    def _items(feed, sid):
        """The awaited rows on sid's cards, as [(id, [nested ids])]."""
        for c in feed["asks"]:
            if c.get("sid") == sid and isinstance(c.get("awaiting"), dict) and c["awaiting"].get("items"):
                return [(it["id"], [w["id"] for w in it.get("waits") or ()]) for it in c["awaiting"]["items"]]
        return None


class AnUnreadTreeIsNeverSampled(_World):
    def test_an_unchanged_rebuild_takes_no_lstat_under_any_tree_when_no_session_has_live_agents(self):
        """RED FIRST: on the base the key samples each living session's whole tree on every build, one lstat per
        directory (here 32 per session), whether or not the derivation read it."""
        self._build()                                   # cold: every session derived
        c, d, f = self._counted()
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self.assertEqual(sum(c.lstat.values()), 0,
                         "no session's derivation read a subagents tree, so no key samples one: %r" % dict(c.lstat))
        self.assertEqual(sum(c.other.values()), 0, dict(c.other))
        self.assertEqual(_dump(f), _dump(self._scratch()))

    def test_outside_a_cycle_scope_too(self):
        self._build(scope=False)
        c, d, _f = self._counted(scope=False)
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self.assertEqual(sum(c.lstat.values()), 0, dict(c.lstat))

    def test_a_tree_changing_under_a_session_that_reads_none_re_derives_nothing(self):
        self._build()
        aid = self._agent(WEB, self.root[WEB] / "workflows" / ("wf_%016x" % 0xfeed))   # a workflow agent lands and finishes
        self._bump(self.root[WEB] / "workflows")
        d, f = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self.assertEqual(_dump(f), _dump(self._scratch()))
        self.assertTrue(aid)


class ALiveAgentsSessionKeysItsOwnTree(_World):
    def _two_agents(self):
        a1 = self._agent(WEB, self.root[WEB])
        a2 = self._agent(WEB, self.root[WEB] / "workflows" / ("wf_%016x" % 0xabc), meta=False)
        self._live_agent(WEB, a1)
        self._live_agent(WEB, a2, since=NOW - 50)
        return a1, a2

    def test_an_unchanged_rebuild_samples_the_reading_sessions_tree_and_no_other(self):
        a1, a2 = self._two_agents()
        self._build()
        c, d, f = self._counted()
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self.assertEqual(set(c.lstat), {str(self.root[WEB])}, "only web's derivation read a tree")
        self.assertEqual(c.lstat[str(self.root[WEB])], len(self._dirs(WEB)), "one lstat per directory of web's tree")
        self.assertEqual(self._items(f, WEB), [(a1, []), (a2, [])], "two live agents, neither nested yet")
        self.assertEqual(_dump(f), _dump(self._scratch()))

    def test_a_late_sidecar_in_a_nested_directory_re_derives_the_session_under_subagents(self):
        a1, a2 = self._two_agents()
        self._build()
        self._build()
        self._sidecar(self.root[WEB] / "workflows" / ("wf_%016x" % 0xabc), a2, parent=a1)   # the CLI's sidecar lands late
        d, f = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (1, 2), d)
        self.assertEqual(d["miss_by"], {"subagents": 1}, d)
        self.assertEqual(self._items(f, WEB), [(a1, [a2])], "the agent nests under its launcher")
        self.assertEqual(_dump(f), _dump(self._scratch()))
        d, _f = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (0, 3), "and is served from then on: %r" % d)

    def test_a_cold_entry_that_read_a_tree_hits_on_the_next_unchanged_build(self):
        self._two_agents()
        d, _f = self._delta(self._build)
        self.assertEqual(d["derived"], 3, d)
        d, _f = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (0, 3),
                         "the key stored with the entry holds the derivation's own reads, so nothing re-derives: %r" % d)
        d, _f = self._delta(lambda: self._build(scope=False))
        self.assertEqual((d["derived"], d["hit"]), (0, 3), "outside a scope as well: %r" % d)

    def test_the_identities_kept_are_the_ones_the_read_was_taken_under(self):
        """A sidecar landing after the derivation read the map and before its key is stored (outside a cycle scope,
        where a later sample is a fresh one) is seen at the next build: the stored identities are the read's, so the
        next sample differs and the session derives again. Keyed on a sample taken after the derivation, the entry
        would hit on a board without the nesting for good."""
        a1, a2 = self._two_agents()
        real = km._feed_session_entry
        wf = self.root[WEB] / "workflows" / ("wf_%016x" % 0xabc)
        fired = []

        def entry(s, ctx):
            out = real(s, ctx)
            if s["sid"] == WEB and not fired:
                fired.append(1)
                self._sidecar(wf, a2, parent=a1)         # lands after the read, before the key is kept
            return out
        with mock.patch.object(km, "_feed_session_entry", side_effect=entry):
            f1 = self._build(scope=False)
        self.assertEqual(fired, [1])
        self.assertEqual(self._items(f1, WEB), [(a1, []), (a2, [])], "the derivation read the tree before the sidecar")
        d, f2 = self._delta(lambda: self._build(scope=False))
        self.assertEqual(d["miss_by"].get("subagents"), 1, d)
        self.assertEqual(self._items(f2, WEB), [(a1, [a2])])
        self.assertEqual(_dump(f2), _dump(self._scratch(scope=False)))

    def test_a_tree_moving_between_two_reads_of_one_derivation_is_seen_at_the_next_build(self):
        """Outside a cycle scope every read of a tree is a fresh sample, so one derivation can read it twice across a
        change. Here a background agent's launch is in the task set without its agent id, _awaiting_live_rows looks it up
        in the sidecar map and finds nothing, and the launch's sidecar lands before _awaiting_nest reads the map again
        for the two live agents. The key keeps the FIRST sample the derivation read, so the next build sees the tree
        moved and resolves the launch to its agent; keyed on the later sample it would serve the unresolved row for good."""
        self._two_agents()
        where = self.root[WEB] / "workflows" / ("wf_%016x" % 0xbcd)
        a3 = self._agent(WEB, where, meta=False)
        tid = "toolu_" + a3[1:]                          # the launch's id, the toolUseId its sidecar will carry
        self.live[WEB] = dict(self.live[WEB], bgTasks=[{"toolUseId": tid, "type": "local_agent",
                                                        "desc": "check the list endpoint", "since": NOW - 40}])
        real = km._awaiting_nest
        fired = []

        def nest(agents, commands, cmd_owner, path):
            if str(path) == str(self.tpath[WEB]) and not fired:
                fired.append(1)
                self._sidecar(where, a3)                 # between the two reads of the map
            return real(agents, commands, cmd_owner, path)
        with mock.patch.object(km, "_awaiting_nest", side_effect=nest):
            f1 = self._build(scope=False)
        self.assertEqual(fired, [1])

        def row(f):
            for c in f["asks"]:
                if c.get("sid") == WEB and isinstance(c.get("awaiting"), dict):
                    for it in c["awaiting"].get("items") or ():
                        if it["id"] == tid:
                            return it
            return None
        self.assertIsNotNone(row(f1), "the launch is an awaited row")
        self.assertNotIn("agentId", row(f1), "the first read found no sidecar for the launch")
        d, f2 = self._delta(lambda: self._build(scope=False))
        self.assertEqual(d["miss_by"].get("subagents"), 1, d)
        self.assertEqual(row(f2).get("agentId"), a3, "the next build resolves the launch to its agent")
        self.assertEqual(_dump(f2), _dump(self._scratch(scope=False)))


class OneLiveAgentReadsNoTree(_World):
    def test_a_lone_live_agent_reads_no_tree_and_its_rebuild_takes_no_lstat(self):
        """RED FIRST on the short-circuit: _awaiting_nest read the sidecar map for a lone live agent, though no agent can
        nest under itself; that read would key the session on its whole tree."""
        a1 = self._agent(WEB, self.root[WEB])
        self._live_agent(WEB, a1)
        f0 = self._build()
        self.assertEqual(self._items(f0, WEB), [(a1, [])])
        reads = json.loads(km._feed_memo[WEB][1]).get("reads") or {}
        self.assertNotIn("subagents", reads, "the derivation read nothing of the subagents tree")
        c, d, f = self._counted()
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self.assertEqual(sum(c.lstat.values()), 0, dict(c.lstat))
        self.assertEqual(_dump(f), _dump(self._scratch()))

    def test_a_lone_agent_whose_sidecar_names_a_parent_stays_top_level(self):
        """The skipped read cannot change the answer: a lone agent's sidecar naming a launcher that is not a live row
        nested nothing when the map was read, and nests nothing now."""
        a1 = self._agent(WEB, self.root[WEB], parent=aid_of(0xdead))
        self._live_agent(WEB, a1)
        f = self._build()
        self.assertEqual(self._items(f, WEB), [(a1, [])])
        self.assertEqual(_dump(f), _dump(self._scratch()))


class AnOpenAgentsFileIsKeyedByItsLaunches(_World):
    def test_a_launch_landing_in_the_agents_file_nests_the_command_at_the_next_build(self):
        """RED FIRST: on the base the key never read the agent's file, so the launch landing moved no component and the
        command stayed top-level on a served entry."""
        a1 = self._agent(WEB, self.root[WEB], records=[self._noise(0)])
        self._live_agent(WEB, a1)
        self._command(WEB, "toolu_cmd_0001")
        f1 = self._build()
        self.assertEqual(self._items(f1, WEB), [(a1, []), ("toolu_cmd_0001", [])], "nobody's file names the launch yet")
        apath = self.root[WEB] / ("agent-%s.jsonl" % a1)
        self._append(apath, self._noise(1))              # the agent's other writes move nothing
        d, _f = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (0, 3), d)
        self._append(apath, self._launch("toolu_cmd_0001"))
        d, f2 = self._delta(self._build)
        self.assertEqual((d["derived"], d["hit"]), (1, 2), d)
        self.assertEqual(d["miss_by"], {"subagents": 1}, d)
        self.assertEqual(self._items(f2, WEB), [(a1, ["toolu_cmd_0001"])], "the agent launched it: nested under it")
        self.assertEqual(_dump(f2), _dump(self._scratch()))


class TheMemoizedBoardEqualsAFromScratchBuild(_World):
    """The oracle: seeded sequences of changes to the agents, their files and sidecars, the commands and the trees; after
    each step the memoized board equals the board built with nothing memoized, byte for byte."""

    def _step(self, rng, state):
        sid = rng.choice(SIDS)
        st = state[sid]
        kind = rng.choice(("start", "start", "stop", "late", "launch", "noise", "cmd", "cmd_stop", "wf", "other"))
        if kind == "start":
            where = self.root[sid] if rng.random() < 0.4 else (self.root[sid] / "workflows" / ("wf_%016x" % rng.randrange(1 << 20)))
            parent = rng.choice(st["live"]) if st["live"] and rng.random() < 0.3 else None
            meta = rng.random() < 0.6
            aid = self._agent(sid, where, parent=parent, meta=meta, records=[self._noise(0)])
            st["dirs"][aid] = where
            if not meta:
                st["nometa"].append(aid)
            st["live"].append(aid)
            self._live_agent(sid, aid, since=NOW - 60 + len(st["live"]))
            if parent is None and st["live"][:-1] and rng.random() < 0.5:   # launched by another live agent's file
                self._append(self._file(sid, rng.choice(st["live"][:-1]), st), self._launch("toolu_" + aid[1:], name="Agent"))
        elif kind == "stop" and st["live"]:
            aid = rng.choice(st["live"])
            st["live"].remove(aid)
            self.live[sid] = dict(self.live[sid], subagents=[a for a in self.live[sid].get("subagents") or ()
                                                              if a["agentId"] != aid])
        elif kind == "late" and st["nometa"]:
            aid = st["nometa"].pop(0)
            parent = rng.choice([a for a in st["live"] if a != aid] or [None])
            self._sidecar(st["dirs"][aid], aid, parent=parent)
        elif kind == "launch" and st["live"]:
            tid = rng.choice(st["cmds"] or ["toolu_cmd_%04d" % rng.randrange(1000)])
            self._append(self._file(sid, rng.choice(st["live"]), st), self._launch(tid))
        elif kind == "noise" and st["live"]:
            self._append(self._file(sid, rng.choice(st["live"]), st), self._noise(rng.randrange(100)))
        elif kind == "cmd":
            tid = "toolu_cmd_%04d" % rng.randrange(1000)
            if tid not in st["cmds"]:
                st["cmds"].append(tid)
                self._command(sid, tid)
        elif kind == "cmd_stop" and st["cmds"]:
            tid = st["cmds"].pop(rng.randrange(len(st["cmds"])))
            self.live[sid] = dict(self.live[sid], bgTasks=[t for t in self.live[sid].get("bgTasks") or ()
                                                           if t["toolUseId"] != tid])
        elif kind == "wf":
            d = self.root[sid] / "workflows" / ("wf_%016x" % rng.randrange(1 << 20))
            d.mkdir(exist_ok=True)
            self._bump(d)
            self._bump(self.root[sid] / "workflows")
        elif kind == "other":                           # an agent that finished before anyone looked lands somewhere
            self._agent(sid, self.root[sid] / "workflows" / ("wf_%016x" % rng.randrange(1 << 20)),
                        parent=rng.choice(st["live"]) if st["live"] else None)
        return sid, kind

    def _file(self, sid, aid, st):
        return Path(st["dirs"][aid]) / ("agent-%s.jsonl" % aid)

    def _run(self, seed, scope):
        rng = random.Random(seed)
        state = {sid: {"live": [], "nometa": [], "cmds": [], "dirs": {}} for sid in SIDS}
        for sid in SIDS:
            self.live[sid] = self._row()                 # no agents or commands carried over from an earlier seed
        self._build(scope)
        derived = served = 0
        for i in range(24):
            sid, kind = self._step(rng, state)
            d, memo = self._delta(lambda: self._build(scope))
            derived += d["derived"]
            served += d["hit"]
            self.assertEqual(_dump(memo), _dump(self._scratch(scope)),
                             "seed %d step %d (%s on %s): the memoized board differs from a from-scratch build"
                             % (seed, i, kind, NAME_OF[sid]))
        self.assertGreater(served, derived, "the memo still serves most sessions (%d served, %d derived)" % (served, derived))

    def test_seeded_sequences_inside_a_cycle_scope(self):
        for seed in (1, 2, 3):
            with self.subTest(seed=seed):
                self._run(seed, scope=True)

    def test_seeded_sequences_outside_a_scope(self):
        for seed in (4, 5):
            with self.subTest(seed=seed):
                self._run(seed, scope=False)


if __name__ == "__main__":
    unittest.main()
