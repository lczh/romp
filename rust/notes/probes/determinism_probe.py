#!/usr/bin/env python3
"""Scratch probe for the S2 design (not part of the repo). Builds a synthetic world in a temp dir, freezes the
clock, mints goal stores with REAL trail segment ids, warms parses, builds the feed twice, records which functions
ran inside the per-card derivation, and writes the canonical feed + per-session entries for comparison."""
import hashlib, importlib.util, json, os, sys, tempfile, time
from collections import Counter
from unittest import mock

REPO = os.path.expanduser("~/romp-romp_fix")
NOW = 1781100000
NSESS = int(os.environ.get("PROBE_SESSIONS", "3"))
NREC = int(os.environ.get("PROBE_RECORDS", "80"))
BIG = int(os.environ.get("PROBE_BIG", "0"))
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

root = tempfile.mkdtemp(prefix="s2w-", dir="/tmp")
root = os.path.realpath(root)
for k in list(os.environ):
    if k.startswith(("ROMP_", "ANTHROPIC_", "OP_", "CLAUDE_")):
        os.environ.pop(k)
os.environ.update({"HOME": os.path.join(root, "home"), "TZ": "UTC", "ROMP_HOST_NAME": "spike-host",
                   "ROMP_UPDATE_CHECK": "off", "GIT_CEILING_DIRECTORIES": root})
os.makedirs(os.environ["HOME"], exist_ok=True)
time.tzset()

spec = importlib.util.spec_from_file_location("bcp", os.path.join(REPO, "tests", "bench_connect_push.py"))
bcp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bcp)
pb = bcp.load_bench_tools()
with mock.patch.object(bcp.time, "time", return_value=float(NOW)):
    state, claude, sids = bcp.write_world(root, NSESS, NREC, BIG)
# leave the LAST session mid-turn: a prompt and a tool_use with no result
last = sids[-1]
tp_last = None
for dp, dn, fn in os.walk(os.path.join(claude, "projects")):
    for f in fn:
        if f == last + ".jsonl":
            tp_last = os.path.join(dp, f)
with open(tp_last) as f:
    lines = [json.loads(x) for x in f]
parent = lines[-1]["uuid"]
t_last = NOW - 300
def iso(t):
    return bcp.iso(t)
extra = [
    {"type": "user", "timestamp": iso(t_last), "uuid": "bbbbbbbb-0000-0000-0000-000000000001", "parentUuid": parent,
     "sessionId": last, "cwd": lines[-1]["cwd"], "version": "2.1.0", "gitBranch": "main", "promptSource": "typed",
     "message": {"role": "user", "content": "now add pagination to the list endpoint and cover it with a test"}},
    {"type": "assistant", "timestamp": iso(t_last + 5), "uuid": "bbbbbbbb-0000-0000-0000-000000000002",
     "parentUuid": "bbbbbbbb-0000-0000-0000-000000000001", "sessionId": last, "cwd": lines[-1]["cwd"],
     "version": "2.1.0", "gitBranch": "main",
     "message": {"role": "assistant", "model": "claude-sonnet-4", "stop_reason": "tool_use",
                 "content": [{"type": "text", "text": "Reading the endpoint first."},
                             {"type": "tool_use", "id": "toolu_open_1", "name": "Read",
                              "input": {"file_path": "/TESTDIR/notes-api/api.py"}}]}},
]
with open(tp_last, "a") as f:
    for r in extra:
        f.write(json.dumps(r) + "\n")

private = os.path.join(root, "private"); os.makedirs(private)
pb.prepare_env(state, claude, private)
os.environ["ROMP_HOST_NAME"] = "spike-host"
km, sbmod = pb.load_kernel(REPO)
jd, em = km.jd, km.em
assert str(jd.STATE).startswith(root) or os.path.realpath(str(jd.STATE)).startswith(root), jd.STATE
assert os.path.realpath(str(jd.PROJECTS)).startswith(root), jd.PROJECTS
km._sdk_backend = pb.make_backend(sbmod, state, False, False)
km._codex_backend = False
rec = pb.new_recorder()
shadow = pb.StateShadow(state, state, rec["refused_writes"])
pb.install_guards(km, sbmod, shadow, rec, no_git=True)
km._GLOBAL_CLAUDE_MD = os.path.join(root, "missing")

live = {}
for i, sid in enumerate(sids):
    live[sid] = {"state": "working" if sid == last else "idle", "since": NOW - 100, "model": "", "effort": "",
                 "context": None, "compactPct": None, "color": None}
live = json.loads(json.dumps(live))
km._live_map = lambda: live
km._live_scope.snapshot = live
km._live_scope.paths = {}
km._live_scope.sessions = {}
km._live_scope.names = km._names_snapshot()

freeze = mock.patch("time.time", return_value=float(NOW))
freeze.start()
km._STARTED = float(NOW)
alive = km._alive_sessions(NOW, live)
print("alive", [(s["name"], s["mtime"]) for s in alive], file=sys.stderr)
# mint goal stores with REAL segment ids from the warm parse
for s in alive:
    sid = s["sid"]
    ps = km._parse(s["path"], sid, NOW)
    turns = ps["turns"]
    segs = []
    for t in turns:
        for sg in em.segments(t):
            segs.append(sg)
    store = {"rompUuid": sid, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}
    if len(segs) < 3:
        continue
    s0, s1, s2 = segs[0], segs[1], segs[-1]
    jd.apply_plan(store, s0["id"], s0["t"], [{"do": "mint", "why": "the opening request", "text": "tighten the notes-api search index"}], [],
                  prompt_uuid=s0.get("trigger"), quote="step 0: tighten the search index")
    jd.apply_plan(store, s1["id"], s1["t"], [{"do": "sub", "under": 1, "why": "a step", "text": "rerun the search suite"}],
                  [{"id": sid + ":g1"}])
    jd.apply_plan(store, s1["id"] + "#d", s1["t"], [{"do": "done", "goal": 1, "why": "suite passes"}], [{"id": sid + ":g2"}])
    jd.apply_plan(store, segs[2]["id"], segs[2]["t"], [{"do": "mint", "why": "a second ask", "text": "decide the index limit"}], [])
    jd.apply_plan(store, segs[2]["id"] + "#b", segs[2]["t"], [{"do": "block", "goal": 1, "why": "needs the user's call on the limit"}],
                  [{"id": sid + ":g3"}])
    if sid != last:
        jd.apply_plan(store, s2["id"], s2["t"], [{"do": "mint", "why": "a third ask", "text": "ship the index change"}], [])
        jd.apply_plan(store, s2["id"] + "#x", s2["t"], [{"do": "done", "goal": 1, "why": "shipped"}], [{"id": sid + ":g4"}])
    jd.rollup_status(store, session_closed=(sid != last), now=NOW - 60)
    jd.save_goals(sid, store)
# pin mtimes, distinct per file, relative to NOW
i = 0
for dp, dn, fn in os.walk(root):
    for f in sorted(fn):
        p = os.path.join(dp, f)
        i += 1
        ns = (NOW - 3600 + i) * 10**9
        os.utime(p, ns=(ns, ns))
# clear every parse/in-memory cache that might hold pre-utime identities, then warm
km._live_scope.names = km._names_snapshot()
jd._discover_cache.clear()
alive = km._alive_sessions(NOW, live)
for s in alive:
    km._parse(s["path"], s["sid"], NOW)
km.build_feed(NOW, live)          # adoption build
km._feed_memo_forget(set())
km._FEED_FOLD.clear()

# record calls inside the derivation
calls = Counter()
kfiles = {os.path.realpath(km.__file__): "kernel", os.path.realpath(jd.__file__): "judge",
          os.path.realpath(em.__file__): "event_model"}
depth = [0]
real_entry = km._feed_session_entry
def prof(frame, event, arg):
    if event == "call":
        fn = os.path.realpath(frame.f_code.co_filename)
        m = kfiles.get(fn)
        if m:
            calls[(m, frame.f_code.co_name, frame.f_code.co_firstlineno)] += 1
def wrapped(s, ctx):
    sys.setprofile(prof)
    try:
        return real_entry(s, ctx)
    finally:
        sys.setprofile(None)
km._feed_session_entry = wrapped
key_calls = Counter()
real_key = km._feed_session_key
def prof2(frame, event, arg):
    if event == "call":
        fn = os.path.realpath(frame.f_code.co_filename)
        m = kfiles.get(fn)
        if m:
            key_calls[(m, frame.f_code.co_name, frame.f_code.co_firstlineno)] += 1
def wrapped_key(s, tm, ctx, prev):
    sys.setprofile(prof2)
    try:
        return real_key(s, tm, ctx, prev)
    finally:
        sys.setprofile(None)
km._feed_session_key = wrapped_key
feed = km.build_feed(NOW, live)
km._feed_session_entry = real_entry
km._feed_session_key = real_key
entries = {sid: km._feed_memo[sid][1] for sid in km._feed_memo}
freeze.stop()

def raise_default(o):
    raise TypeError("unencodable %r" % type(o))
canon = json.dumps(feed, sort_keys=True, default=raise_default, allow_nan=False)
ins = json.dumps(feed, default=raise_default, allow_nan=False)
with open(os.path.join(OUT, "feed.canon.json"), "w") as f:
    f.write(canon)
with open(os.path.join(OUT, "feed.ins.json"), "w") as f:
    f.write(ins)
with open(os.path.join(OUT, "entries.json"), "w") as f:
    json.dump(entries, f, sort_keys=True)
with open(os.path.join(OUT, "calls.json"), "w") as f:
    json.dump([[m, n, l, c] for (m, n, l), c in sorted(calls.items())], f)
with open(os.path.join(OUT, "keycalls.json"), "w") as f:
    json.dump([[m, n, l, c] for (m, n, l), c in sorted(key_calls.items())], f)
summary = {
    "root": root, "asks": len(feed["asks"]), "absent": feed.get("absentSids"),
    "sdkNotices": len(feed.get("sdkNotices") or []), "syncNotices": len(feed.get("syncNotices") or []),
    "selfHost": feed.get("selfHost"), "canon_sha": hashlib.sha256(canon.encode()).hexdigest()[:16],
    "ins_sha": hashlib.sha256(ins.encode()).hexdigest()[:16], "bytes": len(canon),
    "spawns": len(rec["spawns"]), "refused": rec["refused_writes"], "threads": rec["thread_starts"],
    "warm_calls": len(rec["warm_calls"]),
    "cards": [(c["itemId"].split(":")[-1], c["column"], c.get("sessState"), bool(c.get("anchorUuid") or any(r.get("anchorUuid") for r in c.get("tree") or [])),
               c.get("summaryAnchorUuid") is not None, c.get("judging"), bool(c.get("working"))) for c in feed["asks"]],
    "working": feed["working"], "awaiting": feed["awaiting"],
}
print(json.dumps(summary, indent=1))

# ---- parse tree inspection (appended) ----
freeze.start()
lazy_n = atoms_n = 0
pts = {}
for s in alive:
    ps = km._parse_cached(s["path"])
    for t in ps["turns"]:
        for a in t["atoms"]:
            atoms_n += 1
            if a.get("lazy") is not None:
                lazy_n += 1
    try:
        pt = em.plain_tree(ps)
        js = json.dumps(pt, sort_keys=True, default=raise_default)
        pts[s["sid"]] = (len(js), list(ps.keys()), len(ps["turns"]))
    except Exception as e:
        pts[s["sid"]] = repr(e)[:200]
freeze.stop()
print(json.dumps({"atoms": atoms_n, "lazy": lazy_n, "trees": pts, "ckpt": str(em._CKPT_DIR_FN() if getattr(em, "_CKPT_DIR_FN", None) else None)}, indent=1))
if os.environ.get("PROBE_KEEP_TREE"):
    s = alive[0]
    with open(os.path.join(OUT, "tree0.json"), "w") as f:
        json.dump(em.plain_tree(km._parse_cached(s["path"])), f, sort_keys=True, indent=1, default=raise_default)
