#!/usr/bin/env python3
"""The chat signature's per-cycle work (2026-10-23): a quiet cycle no longer re-reads, tab by tab, what the cycle has
already read, and a tree that has not moved costs a tab one check.

Every chat tab's signature re-evaluated its cached build's dependency record on every pusher cycle, and the taskout
component was one stat per recorded path: the session's subagents directories (one per directory of its tree), each
Agent card's file, every sibling session's subagents root when an agent's file was missing from its own tree (the same
list for every tab of the project) and the task outputs. The feed key validates each of those trees in the same cycle,
and every tab of a project re-stat'd the same sibling roots. The directory-derived rows (the git branch, the GitHub
repository, the work trees, the CLAUDE.md chain), the repo listing behind the path-link pre-check and the fork table
were read again by each tab of the same directory.

Pinned here:
(1) THE ORACLE. Six tabs in two projects, with Agent cards whose files sit at the top of the tree, in a nested workflow
    directory, in a sibling session's tree or nowhere, a task output, an unresolved path mention and goal stores, driven
    through the real push over a seeded run of changes (agent files growing, workflow directories appearing and going,
    sidecars landing, a missing file landing in a sibling's tree, outputs growing, transcripts and stores moving,
    directories touched in place, quiet cycles). At every tab of every push the signature equals the one taken with no
    push memo and one stat per recorded path (the evaluation before this change), so each build or serve decision is
    the same, and every served payload equals what building the tab at that moment returns.
(2) THE WORK. A quiet push's signatures stat no subagents directory (each tree is read through the cycle's one sample,
    which the feed key reads too), stat each sibling root once however many tabs recorded it, and read the directory
    rows, the repo listing and the fork table once per directory or per push; a quiet tab whose trees held takes no
    identity of their directories beyond the sample's own validation.
(3) THE PLAN'S GUARD. Keys read from a sample are vouched on later cycles only when that sample is exactly the walk
    memo's entry: a re-walk by another reader between the sample and the signature is not adopted, so the change it
    saw is not hidden from the next cycle.
Synthetic fixtures only: private placeholder ids, the notes-api demo world, invented text, temporary directories."""
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

# Hermetic state BEFORE the load (the tests/test_subagent_tree_memo.py preamble), session hosts off in the minted root.
_ROOT = tempfile.mkdtemp()
os.environ["XDG_STATE_HOME"] = _ROOT
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.makedirs(os.path.join(_ROOT, "romp"), exist_ok=True)
with open(os.path.join(_ROOT, "romp", "session-hosts"), "w") as _fh:
    _fh.write("off")
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "test-token-DO-NOT-USE")
km = load_source("romp_kernel_chat_sig_cycle_work", os.path.join(BIN, "romp-kernel"))
jd = km.jd

NOW = int(time.time())
T0 = NOW - 3600
SIDS = ["99999999-aaaa-bbbb-cccc-%012x" % (0xd0 + i) for i in range(6)]   # private: these tests write goal stores
TAB_NAMES = ["web", "api", "tests", "docs", "deploy", "lint"]
PROJ_OF = [0, 0, 0, 0, 1, 1]          # four tabs work in notes-api, two in notes-web
SIBLINGS = 10                         # other sessions' directories in the notes-api project, every second one with a tree
WF_EXTRA = 5                          # empty workflow directories per tree, beside the one holding an agent


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _urec(sid, t, uuid, content, parent, **extra):
    r = {"type": "user", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": sid,
         "version": "2.1.0", "message": {"role": "user", "content": content}}
    r.update(extra)
    return r


def _arec(sid, t, uuid, blocks, parent, stop="end_turn"):
    return {"type": "assistant", "uuid": uuid, "parentUuid": parent, "timestamp": _iso(t), "sessionId": sid,
            "version": "2.1.0", "message": {"role": "assistant", "model": "claude-opus-5", "content": blocks,
                                            "stop_reason": stop}}


def _tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def _tool_result(tid, content):
    return {"type": "tool_result", "tool_use_id": tid, "content": content, "is_error": False}


def _agent_rows(sid, aid, t, n, start=0):
    """An agent's own file: a prompt, then `n` tool calls (numbered from `start`), sidechain records."""
    rows = [] if start else [dict(_urec(sid, t, "%s-u0" % aid, "Do the delegated work.", None), isSidechain=True, agentId=aid)]
    prev = "%s-u0" % aid if not start else "%s-r%d" % (aid, start - 1)
    for k in range(start, start + n):
        a, r = "%s-a%d" % (aid, k), "%s-r%d" % (aid, k)
        rows.append(dict(_arec(sid, t + 2 * k + 1, a, [_tool_use("%s-tu%d" % (aid, k), "Read",
                                                                  {"file_path": "notes/item%d.md" % k})], prev, "tool_use"),
                         isSidechain=True, agentId=aid))
        rows.append(dict(_urec(sid, t + 2 * k + 2, r, [_tool_result("%s-tu%d" % (aid, k), "line %d" % k)], a),
                         isSidechain=True, agentId=aid))
        prev = r
    return rows


def _write(path, rows, mode="w"):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, mode) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class _World(unittest.TestCase):
    """The six-tab world and the real push over it, with the feed and timeline sections stubbed (the feed stub reads each
    tab's tree through the feed key's own reader, as the real feed key does every cycle)."""

    PUSH_STUBS = ("_live_map", "_live_names", "_chat_tab_sessions", "_cached_feed", "_cached_timeline",
                  "build_timeline", "_fleet_view_sig", "_comments_frame", "_retry_parked_creates", "_sdk",
                  "NAMES", "WORKING_DIR", "_GLOBAL_CLAUDE_MD", "build_session", "_chat_build_sig")

    def setUp(self):
        self.td = Path(tempfile.mkdtemp())
        self.saved = {nm: getattr(km, nm) for nm in self.PUSH_STUBS}
        self.saved_jd = (jd.STATE, jd.PROJECTS)
        self.saved_env = os.environ.get("CLAUDE_CONFIG_DIR")
        self.saved_misc = (dict(km._built_chat), dict(km._prev_chat_events), dict(km._prev_chat_ledger),
                           list(km._last_tab_order), km._judge_gen[0],
                           [set(km._thread_fold_keep[0]), set(km._thread_fold_keep[1])])
        state = self.td / "state"
        state.mkdir()
        (state / "session-hosts").write_text("off")
        jd._rebind_state(state)
        jd.PROJECTS = self.td / "projects"
        jd._discover_cache["fp"] = None
        jd._discover_cache["result"] = None
        jd.NAMES.mkdir(parents=True)
        km.NAMES = jd.NAMES
        km.WORKING_DIR = state / "working"
        km._GLOBAL_CLAUDE_MD = self.td / "no-global-claude.md"
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.td / "claude")
        self.live_map = {}                                    # dormant tabs: no live rows
        km._live_map = lambda: self.live_map
        km._sdk = lambda: None
        km._live_names = lambda tm: {}
        km._chat_tab_sessions = lambda now, live_map: [{k: t[k] for k in ("sid", "name", "path", "anchor")} for t in self.tabs]
        km._cached_feed = self._feed
        km._cached_timeline = lambda now, live_map, sig, connect=False: {"turns": {}, "judging": [], "messages": [], "now": now}
        km.build_timeline = lambda now, live_map, **kw: {"lanes": [], "now": now}
        km._fleet_view_sig = lambda now, live_map: {"probe": 1}
        km._comments_frame = lambda sid, live_map: None
        km._retry_parked_creates = lambda: None
        self._racy = mock.patch.object(km, "_SUBAGENT_DIR_RACY_NS", 0)   # the world stamps every change itself (_stamp)
        self._racy.start()
        self._clear()
        self.real_build, self.real_sig = self.saved["build_session"], self.saved["_chat_build_sig"]
        km.build_session = lambda sid, now, live_map=None, **kw: self.real_build(sid, NOW, live_map, **kw)
        km._chat_build_sig = lambda sess, tm=None, now=None, live_map=None, deps=None: \
            self.real_sig(sess, tm, NOW, live_map=live_map, deps=deps)
        self.chat = {"app": "chat", "alive": True, "sent": {}, "active": SIDS[0], "send": lambda s: None}
        self.tl = {"app": "timeline", "alive": True, "sent": {}, "send": lambda s: None}
        self.tick = 0
        self._build()

    def tearDown(self):
        self._racy.stop()
        for nm, v in self.saved.items():
            setattr(km, nm, v)
        jd._rebind_state(self.saved_jd[0])
        jd.PROJECTS = self.saved_jd[1]
        jd._discover_cache.clear()
        if self.saved_env is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = self.saved_env
        bc, pe, pl, lo, jg, keep = self.saved_misc
        self._clear()
        km._built_chat.update(bc)
        km._prev_chat_events.update(pe)
        km._prev_chat_ledger.update(pl)
        km._last_tab_order[:] = lo
        km._judge_gen[0] = jg
        km._thread_fold_keep[0], km._thread_fold_keep[1] = keep
        km._chat_dep_scope.deps = None
        for k in ("chat_shared", "subagent_trees", "chat_push_owned"):
            setattr(km._live_scope, k, None)
        shutil.rmtree(self.td, ignore_errors=True)

    def _clear(self):
        for d in (km._built_chat, km._prev_chat_events, km._prev_chat_ledger, km._chat_fold, km._SUBAGENT_TREES,
                  km._SUBAGENT_META_CACHE, km._SUBAGENT_FILE_CACHE, km._AGENT_GIST_CACHE, km._AGENT_LAUNCH_CACHE,
                  km._AGENT_TREE_NAMES, km._AGENT_PROJECT_KIDS, km._task_out_cache, km._bgtasks_cache, km._bgall_cache):
            d.clear()
        for k in [k for k in km._PATH_LINK_CACHE if k[0] in SIDS]:
            km._PATH_LINK_CACHE.pop(k, None)
        for sid in SIDS:
            km._node_anchor_rev.pop(sid, None)
        # The per-thread scopes a push or a cycle opens (the names snapshot, the tree samples, the shared signature
        # components): every test runs on this one thread, and _chat_push_scopes_open adopts a slot that is already
        # set, so a snapshot an earlier test left would serve that test's directories here.
        km._chat_push_scopes_close()
        for k in list(vars(km._live_scope)):
            setattr(km._live_scope, k, None)
        # The names snapshot's per-entry memo is keyed on the sid and checked by (mtime_ns, size, inode): this module
        # stamps fixed mtimes, its directories' names have one length, and a removed world frees its inodes for the
        # next, so an earlier test's entry would pass that check and serve its directory.
        km._names_entry_memo.clear()

    # ── the world ────────────────────────────────────────────────────────────────────────────────────
    def _stamp(self, *paths):
        """A change's own stamp: every path it touched gets the next second, so no two changes share an mtime (the
        racy window is closed for these tests; every directory and file a change touches moves by this)."""
        self.tick += 1
        t = NOW - 7200 + self.tick
        for p in paths:
            os.utime(p, (t, t))

    def _build(self):
        self.cwds = [self.td / "notes-api", self.td / "notes-web"]
        self.projs = []
        for c in self.cwds:
            c.mkdir()
            p = jd._proj_dir(os.path.realpath(str(c)))
            p.mkdir(parents=True)
            self.projs.append(p)
        self.outs = self.td / "outs"
        self.outs.mkdir()
        self.tabs = []
        for i, sid in enumerate(SIDS):
            cwd = os.path.realpath(str(self.cwds[PROJ_OF[i]]))
            (jd.NAMES / sid).write_text("%s\t%s" % (TAB_NAMES[i], cwd))
            proj = self.projs[PROJ_OF[i]]
            sub = proj / sid / "subagents"
            wf = sub / "workflows" / ("wf_%016x" % (0xb000 + i))
            wf.mkdir(parents=True)
            for k in range(WF_EXTRA):
                (sub / "workflows" / ("wf_%016x" % (0xc000 + 100 * i + k))).mkdir()
            a_top, a_wf, a_gone = ("a%016x" % (0x1000 + 16 * i + n) for n in (1, 2, 3))
            tus = ["toolu_%s_%d" % (sid[-4:], n) for n in range(4)]
            for aid, d, tu in ((a_top, sub, tus[0]), (a_wf, wf, tus[1])):
                (d / ("agent-%s.meta.json" % aid)).write_text(json.dumps(
                    {"agentType": "general-purpose", "description": "review the notes", "spawnDepth": 1, "toolUseId": tu}))
                _write(d / ("agent-%s.jsonl" % aid), _agent_rows(sid, aid, T0 + 10, 3))
            out = self.outs / ("%s.output" % sid)
            out.write_text("collecting\n")
            notif = ("<task-notification>\n<task-id>b%s</task-id>\n<tool-use-id>%s</tool-use-id>\n<output-file>%s</output-file>\n"
                     "<status>completed</status>\n<summary>Background command \"make check\" completed</summary>\n"
                     "</task-notification>" % (sid[-6:], tus[3], out))
            rows = [_urec(sid, T0, "u1", "start the notes-api spike; the plan is in notes/report.md", None),
                    _arec(sid, T0 + 2, "a1", [{"type": "text", "text": "Sending reviewers."},
                                              _tool_use(tus[0], "Agent", {"description": "review the notes", "prompt": "Review.",
                                                                          "subagent_type": "general-purpose"})], "u1", "tool_use"),
                    _urec(sid, T0 + 30, "r1", [_tool_result(tus[0], "Reviewed.")], "a1",
                          toolUseResult={"agentId": a_top, "content": [{"type": "text", "text": "Reviewed."}]}),
                    _arec(sid, T0 + 32, "a2", [_tool_use(tus[1], "Agent", {"description": "tidy the readme", "prompt": "Tidy.",
                                                                           "subagent_type": "general-purpose"})], "r1", "tool_use"),
                    _urec(sid, T0 + 60, "r2", [_tool_result(tus[1], "Tidied.")], "a2",
                          toolUseResult={"agentId": a_wf, "content": [{"type": "text", "text": "Tidied."}]}),
                    _arec(sid, T0 + 62, "a3", [_tool_use(tus[2], "Agent", {"description": "check the api tests", "prompt": "Check.",
                                                                           "subagent_type": "general-purpose"})], "r2", "tool_use"),
                    _urec(sid, T0 + 90, "r3", [_tool_result(tus[2], "Checked.")], "a3",
                          toolUseResult={"agentId": a_gone, "content": [{"type": "text", "text": "Checked."}]}),
                    _arec(sid, T0 + 92, "a4", [_tool_use(tus[3], "Bash", {"command": "make check", "run_in_background": True,
                                                                          "description": "run the checks"})], "r3", "tool_use"),
                    _urec(sid, T0 + 93, "r4", [_tool_result(tus[3], "Command running in background with ID: b%s. Output is "
                                                                    "being written to: %s" % (sid[-6:], out))], "a4"),
                    _urec(sid, T0 + 200, "n5", notif, "r4"),
                    _arec(sid, T0 + 202, "a5", [{"type": "text", "text": "Checks are done."}], "n5")]
            tpath = proj / (sid + ".jsonl")
            _write(tpath, rows)
            self.tabs.append({"sid": sid, "name": TAB_NAMES[i], "path": str(tpath), "anchor": sid, "sub": sub, "wf": wf,
                              "agents": (a_top, a_wf, a_gone), "out": out, "proj": proj, "last": "a5", "turns": 0})
        self.sibs = []
        for k in range(SIBLINGS):
            d = self.projs[0] / ("99999999-aaaa-bbbb-dddd-%012x" % k)
            d.mkdir()
            if k % 2 == 0:
                (d / "subagents").mkdir()
            self.sibs.append(d)
        # tab 0's third agent lives in a sibling's tree (a /clear fork moved it): found there, the siblings before it noted
        t0 = self.tabs[0]
        _write(self.sibs[4] / "subagents" / ("agent-%s.jsonl" % t0["agents"][2]), _agent_rows(SIDS[0], t0["agents"][2], T0 + 70, 2))
        every = [str(p) for p in [self.td] + sorted(Path(self.td).rglob("*"))]
        self._stamp(*every)

    def tree_dirs(self, tab):
        return [str(tab["sub"])] + sorted(str(p) for p in tab["sub"].rglob("*") if p.is_dir())

    # ── the changes ──────────────────────────────────────────────────────────────────────────────────
    def grow_agent(self, i):
        t = self.tabs[i]
        f = t["sub"] / ("agent-%s.jsonl" % t["agents"][0])
        n = sum(1 for _ in open(f)) // 2
        _write(f, _agent_rows(t["sid"], t["agents"][0], T0 + 10, 1, start=n), mode="a")
        self._stamp(f)

    def new_workflow(self, i):
        t = self.tabs[i]
        self.tick += 1
        d = t["sub"] / "workflows" / ("wf_%016x" % (0xe000 + self.tick))
        d.mkdir()
        aid = "a%016x" % (0x9000 + self.tick)
        (d / ("agent-%s.meta.json" % aid)).write_text(json.dumps(
            {"agentType": "Workflow", "description": "one more step", "spawnDepth": 1, "toolUseId": "toolu_wf_%d" % self.tick}))
        self._stamp(d, d.parent)

    def drop_workflow(self, i):
        t = self.tabs[i]
        kids = sorted(p for p in (t["sub"] / "workflows").iterdir() if p.name.startswith("wf_") and p != t["wf"])
        if kids:
            shutil.rmtree(kids[0])
            self._stamp(kids[0].parent)

    def land_sidecar(self, i):
        t = self.tabs[i]
        self.tick += 1
        (t["wf"] / ("agent-a%016x.meta.json" % (0x8000 + self.tick))).write_text(json.dumps(
            {"agentType": "Workflow", "description": "a nested step", "spawnDepth": 2, "toolUseId": "toolu_n_%d" % self.tick}))
        self._stamp(t["wf"])

    def touch_dir(self, i):
        self._stamp(self.tabs[i]["wf"])                          # moved in place: no entry changes, a new mtime

    def land_missing(self, i):
        """Tab i's missing agent's file lands in a sibling's tree: its card fills, and every tab of the project that noted
        that sibling root sees the root move."""
        t = self.tabs[i]
        sib = self.sibs[6 if i % 2 else 8] / "subagents"
        f = sib / ("agent-%s.jsonl" % t["agents"][2])
        if not f.exists():
            _write(f, _agent_rows(t["sid"], t["agents"][2], T0 + 70, 2))
            self._stamp(f, sib)

    def land_root_sidecar(self, i):
        """A sidecar lands at the top of tab i's tree: the root moves, which every other tab of the project keyed as a
        sibling root its missing agent's lookup read."""
        t = self.tabs[i]
        self.tick += 1
        (t["sub"] / ("agent-a%016x.meta.json" % (0x7000 + self.tick))).write_text(json.dumps(
            {"agentType": "general-purpose", "description": "a late step", "spawnDepth": 1, "toolUseId": "toolu_r_%d" % self.tick}))
        self._stamp(t["sub"])

    def grow_output(self, i):
        out = self.tabs[i]["out"]
        with open(out, "a") as fh:
            fh.write("step %d ok\n" % self.tick)
        self._stamp(out)

    def append_turn(self, i):
        t = self.tabs[i]
        t["turns"] += 1
        u, a = "u-x%d" % t["turns"], "a-x%d" % t["turns"]
        _write(t["path"], [_urec(t["sid"], T0 + 300 + 10 * t["turns"], u, "and the next step", t["last"]),
                           _arec(t["sid"], T0 + 302 + 10 * t["turns"], a, [{"type": "text", "text": "Next step done."}], u)],
               mode="a")
        t["last"] = a
        self._stamp(t["path"])

    def write_store(self, i):
        jd.GOALDIR.mkdir(parents=True, exist_ok=True)
        p = jd.GOALDIR / (SIDS[i] + ".json")
        p.write_text(json.dumps({"rompUuid": SIDS[i], "nodes": {}, "status": {}, "rev": self.tick}))
        self._stamp(p)

    def new_sibling(self, _i):
        self.tick += 1
        d = self.projs[0] / ("99999999-aaaa-bbbb-eeee-%012x" % self.tick)
        (d / "subagents").mkdir(parents=True)
        self._stamp(d / "subagents", d, self.projs[0])

    # land_missing is not drawn at random: a lookup served from the agent-file memo (_subagent_file's hit path) reports
    # nothing to the running build, so a tab rebuilt after its first build keys no sibling root, and a file landing then
    # waits for the tab's next rebuild on any key, the old evaluation's decision and this one's alike. The run lands it
    # once, on a tab whose first build's record (a cold lookup, every sibling root keyed) still stands.
    CHANGES = ("grow_agent", "new_workflow", "drop_workflow", "land_sidecar", "land_root_sidecar", "touch_dir",
               "grow_output", "append_turn", "write_store", "new_sibling")

    # ── the push and the references ─────────────────────────────────────────────────────────────────
    def _feed(self, now, live_map, sig, connect=False):
        """The feed section's stub: every tab's tree read through the feed key's reader, as the real feed key reads it
        each cycle (_subagent_dirs_ident), so the cycle's samples are shared the way they are live."""
        for t in self.tabs:
            km._subagent_dirs_ident(t["sid"], str(t["sub"]))
        return {"working": [], "awaiting": [], "now": now}

    def push(self):
        km._push([self.chat, self.tl])

    def settle(self):
        """Push until a push serves every tab from the cache (the first builds them; a later one takes each tree's keys
        from its sample); returns the cached signatures."""
        last = None
        for _ in range(6):
            self.push()
            sigs = {t["sid"]: km._built_chat[t["sid"]][0] for t in self.tabs}
            if sigs == last:
                return sigs
            last = sigs
        self.fail("the world never settled")

    def treed(self):
        """The tabs whose cached record keys directories of their own tree."""
        return [t for t in self.tabs if km._built_chat[t["sid"]][3].get("trees")]

    def fresh(self, sid):
        """What building `sid` NOW returns, the build the push would run on a miss (its fold prefix as it stands, whose own
        gates decide what a sealed turn re-reads), with nothing it leaves behind kept: the dependency record and the fold
        entry are put back as they were."""
        with km._chat_fold_lock:
            kept = km._chat_fold.get(sid)
        try:
            return self.real_build(sid, NOW, self.live_map)
        finally:
            km._chat_dep_scope.deps = None
            with km._chat_fold_lock:
                km._chat_fold.pop(sid, None)
                if kept is not None:
                    km._chat_fold[sid] = kept


def _reference_taskout(deps):
    """The taskout evaluation before 2026-10-23: one stat per recorded path."""
    return tuple((of, km._chat_stat_key(of)) for of, _k in deps["task_outs"])


class Oracle(_World):
    def _checked_sig(self, record):
        real = self.real_sig

        def sig(sess, tm=None, now=None, live_map=None, deps=None):
            new = real(sess, tm, NOW, live_map=live_map, deps=deps)
            if deps is None and new is not None:
                kept = (km._live_scope.chat_shared, km._live_scope.subagent_trees)
                km._live_scope.chat_shared = km._live_scope.subagent_trees = None   # no push memo, no cycle sample
                try:
                    with mock.patch.object(km, "_chat_taskout_now", _reference_taskout):
                        ref = real(sess, tm, NOW, live_map=live_map, deps=None)
                finally:
                    km._live_scope.chat_shared, km._live_scope.subagent_trees = kept
                hit = km._built_chat.get(sess["sid"])
                record.append((sess["sid"], new == ref, km._chat_sig_miss(ref, new),
                               hit is not None and hit[0] == new, hit is not None and hit[0] == ref))
            return new
        return sig

    def test_every_decision_and_every_served_payload_match_the_uncached_path(self):
        rec = []
        km._chat_build_sig = self._checked_sig(rec)
        rng = random.Random(20261023)
        plan = [("quiet", 0), ("land_missing", 1), ("quiet", 0)]
        plan += [(c, rng.randrange(len(self.tabs))) for c in self.CHANGES] + [("quiet", 0)]
        plan += [(rng.choice(self.CHANGES + ("quiet", "quiet")), rng.randrange(len(self.tabs))) for _ in range(40)]
        served = built = 0
        for step, (change, tab) in enumerate(plan):
            if change != "quiet":
                getattr(self, change)(tab)
            rec.clear()
            self.push()
            self.assertEqual(len(rec), len(self.tabs), "every tab's signature was taken (step %d, %s)" % (step, change))
            for sid, same, moved, hit_new, hit_ref in rec:
                self.assertTrue(same, "step %d (%s), tab %s: the signature differs from the uncached one in %r"
                                % (step, change, sid[-2:], moved))
                self.assertEqual(hit_new, hit_ref, "the serve decision is the uncached one's")
                served += hit_new
                built += not hit_new
            for t in self.tabs:
                ent = km._built_chat.get(t["sid"])
                self.assertIsNotNone(ent, "tab %s is cached after the push" % t["name"])
                want = self.fresh(t["sid"])
                for k in ("events", "ledger", "bgTasks", "status", "branches", "cwd", "gitBranch", "githubRepo", "workTree"):
                    self.assertEqual(ent[1].get(k), want.get(k), "step %d (%s): tab %s's served %s is a fresh build's"
                                     % (step, change, t["name"], k))
        self.assertGreater(served, 3 * len(self.tabs), "quiet pushes served tabs from the cache")
        self.assertGreater(built, 2 * len(self.tabs), "the changes rebuilt tabs")

    def test_the_world_records_what_the_signature_reevaluates(self):
        """The fixture is what the oracle needs it to be: a built tab's record holds its tree's directories under the
        tree's root, its agents' files, the sibling roots its missing agent's lookup read, and its task output."""
        self.push()
        t = self.tabs[1]
        rec = km._built_chat[t["sid"]][3]
        paths = {of for of, _k in rec["task_outs"]}
        self.assertEqual(rec["trees"], (str(t["sub"]),))
        self.assertLessEqual(set(self.tree_dirs(t)), paths, "every directory of the tree")
        self.assertIn(str(t["sub"] / ("agent-%s.jsonl" % t["agents"][0])), paths)
        self.assertIn(str(t["wf"] / ("agent-%s.jsonl" % t["agents"][1])), paths)
        self.assertLessEqual({str(s / "subagents") for s in self.sibs}, paths, "the missing agent's lookup read every sibling")
        self.assertIn(str(t["out"]), paths, "the task output")


class _ForkBackend:
    """An SDK backend that owns no tab and carries a fork table, counting its reads; any other read a signature's helper
    makes of it answers nothing."""

    def __init__(self):
        self.reads = 0

    def owns(self, sid):
        return False

    def pending_cut(self, sid):
        return ""

    def fork_children(self):
        self.reads += 1
        return {}

    def __getattr__(self, name):
        return lambda *a, **k: None


class QuietPushWork(_World):
    """What a push where nothing moved costs the signatures, counted inside the pre-build signatures alone (the post-build
    one and the build itself are outside): the os module's own stat and lstat, by path and by the tab being keyed."""

    def _counted_push(self, watch=()):
        """One push; returns {"stat": [(tab sid, path)], "lstat": [...], name: [args] for each km function in `watch`}."""
        calls = {"stat": [], "lstat": []}
        reals = {k: getattr(os, k) for k in ("stat", "lstat")}
        cur = [None]

        def wrap(kind):
            def f(p, *a, **k):
                if cur[0] is not None and not isinstance(p, int):
                    calls[kind].append((cur[0], os.fspath(p)))
                return reals[kind](p, *a, **k)
            return f
        for name in watch:
            calls[name] = []
        kept = {name: getattr(km, name) for name in watch}

        def spy(name):
            def f(*a, **k):
                if cur[0] is not None:
                    calls[name].append(a)
                return kept[name](*a, **k)
            return f
        for name in watch:
            setattr(km, name, spy(name))
        real_sig = km._chat_build_sig

        def sig(sess, tm=None, now=None, live_map=None, deps=None):
            cur[0] = sess["sid"] if deps is None else None
            try:
                return real_sig(sess, tm, now, live_map=live_map, deps=deps)
            finally:
                cur[0] = None
        km._chat_build_sig = sig
        try:
            with mock.patch.object(os, "stat", wrap("stat")), mock.patch.object(os, "lstat", wrap("lstat")):
                self.push()
        finally:
            km._chat_build_sig = real_sig
            for name, v in kept.items():
                setattr(km, name, v)
        return calls

    @mock.patch.object(km, "_TREE_WATCH_ON", False)   # the lstat validation's costs: the watch certificate's road is
    #                                                     tests/test_feed_subagents_cycle_work.py's (the root's lstat alone)
    def test_a_quiet_push_stats_no_tree_directory_for_its_own_tab_and_each_subagents_root_once(self):
        before = self.settle()
        self.push()                                           # a quiet push after the one that took the keys
        calls = self._counted_push()
        self.assertEqual({t["sid"]: km._built_chat[t["sid"]][0] for t in self.tabs}, before, "nothing moved: every tab served")
        roots = {str(t["sub"]) for t in self.tabs} | {str(s / "subagents") for s in self.sibs}
        inner = {d for t in self.tabs for d in self.tree_dirs(t)} - roots
        own = {t["sid"]: set(self.tree_dirs(t)) for t in self.tabs}
        self.assertEqual([p for _s, p in calls["stat"] if p in inner], [],
                         "no signature stats a directory below a subagents root: each is read through its tree's sample")
        self.assertEqual([(s, p) for s, p in calls["stat"] if p in own[s]], [],
                         "no tab stats a directory of its own tree, its root included")
        treed = self.treed()
        self.assertGreaterEqual(len(treed), 4, "the fixture: most tabs' records key their tree")
        lst = sorted(p for _s, p in calls["lstat"] if p in inner or p in {str(t["sub"]) for t in self.tabs})
        self.assertEqual(lst, sorted(d for t in treed for d in self.tree_dirs(t)),
                         "each tree a record keys is validated once in the push, every directory one lstat")
        for r in sorted(roots):
            n = sum(1 for _s, p in calls["stat"] if p == r)
            self.assertLessEqual(n, 1, "a subagents root that several tabs recorded is stat'd at most once a push: %s" % r)
        for s in self.sibs:
            n = sum(1 for _s, p in calls["stat"] if p == str(s / "subagents"))
            self.assertEqual(n, 1, "a departed sibling's root, recorded by four tabs, is stat'd once: %s" % s.name)

    def test_the_directory_rows_the_repo_listing_and_the_fork_table_are_read_once_per_directory_or_push(self):
        """Every tab's signature taken once inside one push's scopes (_chat_push_scopes_open, as _push opens them), with
        both directories reading as repositories and an SDK backend carrying a fork table."""
        self.settle()
        cwds = {os.path.realpath(str(c)) for c in self.cwds}
        counts = {"_git_branch": [], "_claudemd_key": [], "_repo_index_served": []}
        reals = {k: getattr(km, k) for k in list(counts) + ["_tree_of", "_sdk"]}
        km._git_branch = lambda cwd: counts["_git_branch"].append(cwd) or reals["_git_branch"](cwd)
        km._claudemd_key = lambda cwd: counts["_claudemd_key"].append(cwd) or reals["_claudemd_key"](cwd)
        km._repo_index_served = lambda cwd: counts["_repo_index_served"].append(cwd) or (None, 7)
        km._tree_of = lambda d: (d, "main") if d in cwds else reals["_tree_of"](d)
        be = _ForkBackend()
        km._sdk = lambda: be
        km._chat_push_scopes_open()
        try:
            for t in self.tabs:
                km._chat_build_sig({k: t[k] for k in ("sid", "name", "path", "anchor")}, None, NOW, live_map=self.live_map)
        finally:
            km._chat_push_scopes_close()
            for k, v in reals.items():
                setattr(km, k, v)
        self.assertEqual(sorted(counts["_git_branch"]), sorted(cwds), "the branch: once per directory, not per tab")
        self.assertEqual(sorted(counts["_claudemd_key"]), sorted(cwds), "the CLAUDE.md chain: once per directory")
        self.assertEqual(sorted(counts["_repo_index_served"]), sorted(cwds),
                         "the served repo listing: once per directory, for six tabs with an unresolved mention")
        self.assertEqual(be.reads, 1, "the fork table: once per push")

    @mock.patch.object(km, "_TREE_WATCH_ON", False)   # the lstat validation's costs: the watch certificate's road is
    #                                                     tests/test_feed_subagents_cycle_work.py's (the root's lstat alone)
    def test_a_quiet_tab_takes_no_identity_of_its_directories_beyond_the_samples_validation(self):
        self.settle()
        self.push()
        inodes = {os.lstat(d).st_ino for t in self.treed() for d in self.tree_dirs(t)}
        self.assertTrue(inodes, "the fixture: records that key their trees")
        seen = []
        real = km._stat_ident
        cur = [False]

        def ident(st):
            if cur[0] and st is not None and st.st_ino in inodes:
                seen.append(st.st_ino)
            return real(st)
        real_sig = km._chat_build_sig

        def sig(sess, tm=None, now=None, live_map=None, deps=None):
            cur[0] = deps is None
            try:
                return real_sig(sess, tm, now, live_map=live_map, deps=deps)
            finally:
                cur[0] = False
        km._stat_ident, km._chat_build_sig = ident, sig
        try:
            self.push()
        finally:
            km._stat_ident, km._chat_build_sig = real, real_sig
        self.assertEqual(sorted(seen), sorted(inodes),
                         "one identity per directory per push, the validation's: a quiet tab checks its plan and reads no key")


class PlanGuard(_World):
    def test_a_rewalk_between_the_sample_and_the_signature_is_not_adopted(self):
        self.settle()
        self.push()
        t = self.tabs[2]
        root = str(t["sub"])
        rec = km._built_chat[t["sid"]][3]
        at = rec["at_build"][0]
        self.assertEqual(rec["trees"], (root,), "the fixture: the record keys the tree")
        self.assertIsNotNone(rec.get("tout_plan"), "...and a quiet push vouched it")
        km._live_scope.subagent_trees = {}
        km._live_scope.chat_shared = {}
        try:
            km._subagent_tree(root)                           # the cycle's sample, taken first
            self.land_sidecar(2)                              # a directory moves after it...
            km._live_scope.subagent_trees, cyc = None, km._live_scope.subagent_trees
            km._subagent_tree(root)                           # ...and another reader, outside the cycle, re-walks
            km._live_scope.subagent_trees = cyc
            self.assertEqual(km._chat_taskout_now(rec), at, "this cycle reads its own sample: the change waits a cycle")
        finally:
            km._live_scope.subagent_trees = km._live_scope.chat_shared = None
        km._live_scope.subagent_trees, km._live_scope.chat_shared = {}, {}
        try:
            now = km._chat_taskout_now(rec)                   # the next cycle
        finally:
            km._live_scope.subagent_trees = km._live_scope.chat_shared = None
        self.assertEqual(now, _reference_taskout(rec), "the next cycle sees the moved directory, as one stat per path does")
        self.assertNotEqual(now, at)

    def test_a_tree_vouched_after_it_moved_is_never_served_as_the_build_saw_it(self):
        """A record whose tree moved and was not rebuilt (its build failed, or moved again mid-build, and the entry
        stands): the tree settles, its plan is taken with keys that differ from the build's, and every later cycle
        evaluates to the moved keys, never to the record's at-build tuple."""
        self.settle()
        self.push()
        t = self.tabs[3]
        rec = km._built_chat[t["sid"]][3]
        at = rec["at_build"][0]
        self.assertEqual(rec["trees"], (str(t["sub"]),), "the fixture: the record keys the tree")
        self.touch_dir(3)
        for cycle in range(3):
            km._live_scope.subagent_trees, km._live_scope.chat_shared = {}, {}
            try:
                now = km._chat_taskout_now(rec)
            finally:
                km._live_scope.subagent_trees = km._live_scope.chat_shared = None
            self.assertEqual(now, _reference_taskout(rec))
            self.assertNotEqual(now, at, "cycle %d: the moved directory's key, not the build's" % cycle)
        self.assertIsNotNone(rec.get("tout_plan"), "the settled tree was vouched")


if __name__ == "__main__":
    unittest.main()
