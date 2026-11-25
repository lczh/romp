#!/usr/bin/env python3
"""Scratch probe v2 (not part of the repo): a richer synthetic world, line coverage of the per-card derivation,
and a replay check (non-ported helpers answered from recorded values must give the same bytes)."""
import ast, hashlib, importlib.util, json, os, sys, tempfile, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from unittest import mock

REPO = os.path.expanduser("~/romp-romp_fix")
NOW = 1781100000
T_MINT = NOW - 1800                      # the judges' verdict arrival clock (log rows' `at`)
NREC = int(os.environ.get("PROBE_RECORDS", "80"))
BIG = int(os.environ.get("PROBE_BIG", "0"))
CHAT_TURNS = int(os.environ.get("PROBE_CHAT_TURNS", "10"))
OUT = sys.argv[1]
os.makedirs(OUT, exist_ok=True)

root = os.path.realpath(tempfile.mkdtemp(prefix="s2w-", dir="/tmp"))
for k in list(os.environ):
    if k.startswith(("ROMP_", "ANTHROPIC_", "OP_", "CLAUDE_")):
        os.environ.pop(k)
os.environ.update({"HOME": os.path.join(root, "home"), "TZ": "UTC", "GIT_CEILING_DIRECTORIES": root})
os.makedirs(os.environ["HOME"], exist_ok=True)
time.tzset()

spec = importlib.util.spec_from_file_location("bcp", os.path.join(REPO, "tests", "bench_connect_push.py"))
bcp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bcp)
pb = bcp.load_bench_tools()
with mock.patch.object(bcp.time, "time", return_value=float(NOW)):
    state, claude, sids = bcp.write_world(root, 4, NREC, BIG)
S1, S2, S3, S4 = sids

def tpath(sid):
    for dp, dn, fn in os.walk(os.path.join(claude, "projects")):
        if sid + ".jsonl" in fn:
            return os.path.join(dp, sid + ".jsonl")

# s001's transcript: bench_chatbuild's Gen (substantive ~95-char wrap-ups), extracted by ast (importing it runs the bench)
tree = ast.parse(open(os.path.join(REPO, "tests", "bench_chatbuild.py")).read())
keep = [n for n in tree.body if (isinstance(n, ast.ClassDef) and n.name == "Gen") or (isinstance(n, ast.FunctionDef) and n.name == "iso")]
ns = {"datetime": datetime, "timezone": timezone, "SID": S1, "T0": NOW - 2 * 86400}
exec(compile(ast.Module(body=keep, type_ignores=[]), "bench_chatbuild_gen", "exec"), ns)
class ChatGen(ns["Gen"]):
    def uid(self):
        self.n += 1
        return "cccccccc-0001-0000-0000-%012d" % self.n
g = ChatGen()
with open(tpath(S1), "w") as f:
    for i in range(CHAT_TURNS):
        for r in g.turn(i):
            r["cwd"] = os.path.join(root, "work", "notes-api")
            f.write(json.dumps(r) + "\n")

def open_turn(sid, tag, text):
    p = tpath(sid)
    with open(p) as f:
        last = json.loads(f.readlines()[-1])
    t = NOW - 300
    u1, u2 = "%s-0000-0000-0000-000000000001" % tag, "%s-0000-0000-0000-000000000002" % tag
    recs = [
        {"type": "user", "timestamp": bcp.iso(t), "uuid": u1, "parentUuid": last["uuid"], "sessionId": sid,
         "cwd": last["cwd"], "version": "2.1.0", "gitBranch": "main", "promptSource": "typed",
         "message": {"role": "user", "content": text}},
        {"type": "assistant", "timestamp": bcp.iso(t + 5), "uuid": u2, "parentUuid": u1, "sessionId": sid,
         "cwd": last["cwd"], "version": "2.1.0", "gitBranch": "main",
         "message": {"role": "assistant", "model": "claude-sonnet-4", "stop_reason": "tool_use",
                     "content": [{"type": "text", "text": "Reading the endpoint first."},
                                 {"type": "tool_use", "id": "toolu_open_" + tag[:4], "name": "Read",
                                  "input": {"file_path": "/TESTDIR/notes-api/api.py"}}]}}]
    with open(p, "a") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
open_turn(S3, "dddddddd", "now add pagination to the list endpoint and cover it with a test")
open_turn(S4, "eeeeeeee", "write the release notes for the search change")

private = os.path.join(root, "private"); os.makedirs(private)
pb.prepare_env(state, claude, private)
os.environ["ROMP_HOST_NAME"] = "spike-host"
km, sbmod = pb.load_kernel(REPO)
jd, em = km.jd, km.em
assert os.path.realpath(str(jd.STATE)).startswith(root) and os.path.realpath(str(jd.PROJECTS)).startswith(root)
km._sdk_backend = pb.make_backend(sbmod, state, False, False)
km._codex_backend = False
rec = pb.new_recorder()
shadow = pb.StateShadow(state, state, rec["refused_writes"])
pb.install_guards(km, sbmod, shadow, rec, no_git=True)
km._GLOBAL_CLAUDE_MD = os.path.join(root, "missing")

def row(st):
    return {"state": st, "since": NOW - 100, "model": "", "effort": "", "context": None, "compactPct": None, "color": None}
live = json.loads(json.dumps({S1: row("idle"), S2: row("permission"), S3: row("working"), S4: row("working")}))
km._live_map = lambda: live
km._live_scope.snapshot = live
km._live_scope.paths = {}
km._live_scope.sessions = {}
km._live_scope.names = km._names_snapshot()

freeze = mock.patch("time.time", return_value=float(T_MINT))
freeze.start()
km._STARTED = float(NOW)

def segs_of(sid):
    ps = km._parse(tpath(sid), sid, NOW)
    return [sg for t in ps["turns"] for sg in em.segments(t)]

def new_store(sid):
    return {"rompUuid": sid, "seq": 0, "placementsV": jd.PLACEMENTS_V, "nodes": {}, "placements": {}, "status": {}}

def plan(store, seg, ops, menu=(), key=None, **kw):
    jd.apply_plan(store, seg["id"], seg["t"], ops, [{"id": m} for m in menu], place_key=key, **kw)

# ---- s001: completed top with a summary, a blocked top with a brief (needs you), a delegation-awaiting top ----
sg = segs_of(S1)
st = new_store(S1)
g = lambda n: "%s:g%d" % (S1, n)
plan(st, sg[0], [{"do": "mint", "why": "opening ask", "text": "tighten the notes-api search index"}], prompt_uuid=sg[0]["trigger"],
     quote="step 0: tighten the search index")
plan(st, sg[1], [{"do": "sub", "under": 1, "why": "a step", "text": "raise the search limit"}], [g(1)])
plan(st, sg[2], [{"do": "done", "goal": 1, "why": "limit raised"}], [g(2)], key=sg[2]["id"] + "#d")
plan(st, sg[3], [{"do": "done", "goal": 1, "why": "index tightened"}], [g(1)], key=sg[3]["id"] + "#d")
plan(st, sg[4], [{"do": "mint", "why": "second ask", "text": "split the notes-api router"}])               # g3
plan(st, sg[5], [{"do": "mint", "why": "third ask", "text": "coordinate the client and test changes"}], prompt_uuid=sg[5]["trigger"])   # g4
plan(st, sg[5], [{"do": "sub", "under": 1, "why": "delegated", "text": "↪ delegated to s003: update the client"}], [g(4)], key=sg[5]["id"] + "#s1")  # g5
plan(st, sg[5], [{"do": "sub", "under": 1, "why": "delegated", "text": "↪ delegated to s002: add the tests"}], [g(4)], key=sg[5]["id"] + "#s2")    # g6
st["nodes"][g(5)]["handoff"] = {"peer": S3, "msgId": "m-0001", "goalId": None}
st["nodes"][g(6)]["handoff"] = {"peer": S2, "msgId": "m-0002", "goalId": None}
plan(st, sg[-1], [{"do": "block", "goal": 1, "why": "needs the user's call on the router split"}], [g(3)])
st["nodes"][g(1)]["summary"] = "The search index is tighter: the limit was raised and the suite passes."
st["nodes"][g(3)]["blockSummary"] = "Step 9 done: the index is tighter and tests pass. Decide whether to split the router."
jd.rollup_status(st, session_closed=True, now=T_MINT)
jd.save_goals(S1, st)

# ---- s002: a goal planted by s001 (origin badge), a working goal; live row on a permission prompt ----
sg = segs_of(S2)
st = new_store(S2)
plan(st, sg[0], [{"do": "mint", "why": "planted by a peer", "text": "add the notes-api list tests"}])
st["nodes"]["%s:g1" % S2]["origin"] = {"peer": S1, "goalId": g(6), "peerName": "s001", "msgId": "m-0002"}
plan(st, sg[2], [{"do": "mint", "why": "own ask", "text": "fix the flaky search test"}])
jd.rollup_status(st, session_closed=True, now=T_MINT)
jd.save_goals(S2, st)

# ---- s003: open turn, working goal with a done sub, a failed nudge on record ----
sg = segs_of(S3)
st = new_store(S3)
plan(st, sg[0], [{"do": "mint", "why": "ask", "text": "update the notes-api client"}])
plan(st, sg[1], [{"do": "sub", "under": 1, "why": "step", "text": "regenerate the client types"}], ["%s:g1" % S3])
plan(st, sg[2], [{"do": "done", "goal": 1, "why": "types regenerated"}], ["%s:g2" % S3], key=sg[2]["id"] + "#d")
jd.rollup_status(st, session_closed=False, now=T_MINT)
jd.save_goals(S3, st)
# s004: no goal store (the provisional placeholder)

with open(os.path.join(state, "auto-nudge.json"), "w") as f:
    json.dump({"enabled": True, "nudged": {"%s:g1" % S3: {"count": 2, "failed": True, "failedAt": NOW - 600}}}, f)
with open(os.path.join(state, "nudge-events.jsonl"), "w") as f:
    for t in (NOW - 1500, NOW - 900):
        f.write(json.dumps({"gid": "%s:g1" % S3, "t": t, "verdict": "fired"}) + "\n")
freeze.stop()

i = 0
for dp, dn, fn in os.walk(root):
    for fname in sorted(fn):
        i += 1
        ns_ = (NOW - 3600 + i) * 10**9
        os.utime(os.path.join(dp, fname), ns=(ns_, ns_))

freeze = mock.patch("time.time", return_value=float(NOW))
freeze.start()
with jd._active_lock:
    jd._active["probe-run"] = {"fsid": S2, "kind": "planner", "t": NOW - 20}
km._live_scope.names = km._names_snapshot()
jd._discover_cache.clear()
alive = km._alive_sessions(NOW, live)
for s in alive:
    km._parse(s["path"], s["sid"], NOW)
km.build_feed(NOW, live)
km._feed_memo_forget(set()); km._FEED_FOLD.clear()

# ---- line coverage of everything the derivation executes ----
kfiles = {os.path.realpath(km.__file__): "kernel", os.path.realpath(jd.__file__): "judge",
          os.path.realpath(em.__file__): "event_model", os.path.realpath(km.cm.__file__): "colormap"}
lines = defaultdict(set)          # (mod, func, firstline) -> executed lines
def tracer(frame, event, arg):
    m = kfiles.get(os.path.realpath(frame.f_code.co_filename))
    if not m:
        return None
    key = (m, frame.f_code.co_name, frame.f_code.co_firstlineno)
    def lt(fr, ev, a):
        if ev == "line":
            lines[key].add(fr.f_lineno)
        return lt
    lines[key]
    return lt
real_entry = km._feed_session_entry
def traced_entry(s, ctx):
    sys.settrace(tracer)
    try:
        return real_entry(s, ctx)
    finally:
        sys.settrace(None)
# record the boundary helpers' answers (the "recorded facts" for replay)
RECORD = ["_closer_pending", "_interrupting", "_session_retrying", "_session_awaiting", "_bg_live_norm", "_api_error",
          "_interrupted_by", "_warm_wanted", "_peer_answered", "_heal_session_tops", "_cap_switch_offer", "_awaiting_task_descs"]
recorded = defaultdict(list)
reals = {n: getattr(km, n) for n in RECORD}
def mk(n):
    def f(*a, **k):
        r = reals[n](*a, **k)
        recorded[n].append(json.loads(json.dumps(r, default=list)))
        return r
    return f
for n in RECORD:
    setattr(km, n, mk(n))
feed = km.build_feed(NOW, live)
for n in RECORD:
    setattr(km, n, reals[n])
entries = {sid: km._feed_memo[sid][1] for sid in km._feed_memo}

def raise_default(o):
    raise TypeError("unencodable %r" % type(o))
canon = json.dumps(feed, sort_keys=True, default=raise_default, allow_nan=False)
ins = json.dumps(feed, default=raise_default, allow_nan=False)

# ---- replay: answer the recorded helpers from the recording, in call order, and rebuild from an empty memo ----
km._feed_memo_forget(set()); km._FEED_FOLD.clear()
queues = {n: list(v) for n, v in recorded.items()}
def rp(n):
    def f(*a, **k):
        return queues[n].pop(0)
    return f
for n in RECORD:
    setattr(km, n, rp(n))
km._SUMMARY_ANCHOR_MEMO.clear() if hasattr(km, "_SUMMARY_ANCHOR_MEMO") else None
km._feed_session_entry = traced_entry
feed2 = km.build_feed(NOW, live)
km._feed_session_entry = real_entry
for n in RECORD:
    setattr(km, n, reals[n])
canon2 = json.dumps(feed2, sort_keys=True, default=raise_default, allow_nan=False)
freeze.stop()

for name, data in (("feed.canon.json", canon), ("feed.ins.json", ins)):
    with open(os.path.join(OUT, name), "w") as f:
        f.write(data)
with open(os.path.join(OUT, "entries.json"), "w") as f:
    json.dump(entries, f, sort_keys=True)
with open(os.path.join(OUT, "recorded.json"), "w") as f:
    json.dump(recorded, f, sort_keys=True)
cov = [[m, fn, l, len(v)] for (m, fn, l), v in sorted(lines.items())]
with open(os.path.join(OUT, "coverage.json"), "w") as f:
    json.dump(cov, f)
ent_lines = sorted(lines[("kernel", "_feed_session_entry", 55894)])
with open(os.path.join(OUT, "entry_lines.json"), "w") as f:
    json.dump(ent_lines, f)
def card_brief(c):
    return {"id": c["itemId"].split(":", 1)[-1][-6:] if ":" in c["itemId"] else c["itemId"], "sid": c["sid"][-1], "col": c["column"],
            "sess": c.get("sessState"), "blocked": (c.get("blocked") or {}).get("state"), "await": (c.get("awaiting") or {}).get("kind"),
            "judging": c.get("judging"), "rejudging": c.get("rejudging"), "working": c.get("working"), "origin": bool(c.get("origin")),
            "handoffRows": sum(1 for r in c.get("tree") or [] if r.get("kind") == "handoff"),
            "whoWorking": [r.get("whoWorking") for r in c.get("tree") or [] if r.get("kind") == "handoff"],
            "anchor": bool(c["tree"] and c["tree"][0].get("anchorUuid")) if c.get("tree") else None,
            "prompt": bool(c["tree"] and c["tree"][0].get("promptAnchorUuid")) if c.get("tree") else None,
            "sa": c.get("summaryAnchorUuid") is not None, "saq": c.get("summaryAnchorQuote") is not None,
            "nudged": bool(c.get("nudged")), "nudgeFailed": c.get("nudgeFailed"), "text": c["text"][:40]}
print(json.dumps({"root": root, "asks": len(feed["asks"]), "absent": feed.get("absentSids"),
                  "notices": [len(feed.get(k) or []) for k in ("sdkNotices", "syncNotices", "clearNotices")],
                  "canon_sha": hashlib.sha256(canon.encode()).hexdigest()[:16],
                  "ins_sha": hashlib.sha256(ins.encode()).hexdigest()[:16], "bytes": len(canon),
                  "replay_equal": canon == canon2, "spawns": len(rec["spawns"]), "refused": rec["refused_writes"],
                  "threads": rec["thread_starts"], "working": feed["working"], "awaiting": feed["awaiting"],
                  "recorded_calls": {k: len(v) for k, v in recorded.items()},
                  "cards": [card_brief(c) for c in feed["asks"]]}, indent=1))

# ---- timing (appended): memo-cold, parse-warm builds, and memo-warm builds ----
import statistics
freeze.start()
def clear_memos():
    km._feed_memo_forget(set()); km._FEED_FOLD.clear(); km._SUMMARY_ANCHOR_MEMO.clear()
cold, warm, derive = [], [], []
real_entry = km._feed_session_entry
acc = [0.0]
def timed_entry(s, ctx):
    t0 = time.process_time()
    try:
        return real_entry(s, ctx)
    finally:
        acc[0] += time.process_time() - t0
km._feed_session_entry = timed_entry
for _ in range(7):
    clear_memos(); acc[0] = 0.0
    t0 = time.process_time(); km.build_feed(NOW, live); cold.append(time.process_time() - t0); derive.append(acc[0])
    t0 = time.process_time(); km.build_feed(NOW, live); warm.append(time.process_time() - t0)
km._feed_session_entry = real_entry
freeze.stop()
print(json.dumps({"build_memo_cold_ms": round(statistics.median(cold) * 1000, 2),
                  "derive_only_ms": round(statistics.median(derive) * 1000, 2),
                  "build_memo_warm_ms": round(statistics.median(warm) * 1000, 2)}))

# ---- full replay (appended): trees and stores JSON round-tripped, recorded helpers answered by sid ----
freeze.start()
paths = {s["path"]: s["sid"] for s in alive}
trees = {s["sid"]: json.loads(json.dumps(em.plain_tree(km._parse_cached(s["path"])), default=raise_default)) for s in alive}
stores = {}
for s in alive:
    stv, _k = km._feed_goals_keyed(s["sid"])
    stores[s["sid"]] = json.loads(json.dumps(stv, default=raise_default)) if stv is not None else None
peer_stores = {}
for psid in sids:
    pst, pf = jd.load_goals_shared_or_fault(psid)
    peer_stores[psid] = json.loads(json.dumps(pst, default=raise_default)) if pst is not None else None
by_sid = defaultdict(dict)
order_args = {"_closer_pending": 0, "_interrupting": 0, "_session_retrying": 0, "_session_awaiting": 0, "_bg_live_norm": 0,
              "_api_error": "path", "_interrupted_by": 1, "_peer_answered": 0, "_heal_session_tops": "path",
              "_awaiting_task_descs": 0, "_warm_wanted": "s", "_cap_switch_offer": 0}
def sid_of(n, a):
    w = order_args[n]
    if w == "path":
        return paths[str(a[0])]
    if w == "s":
        return a[0]["sid"]
    return a[w]
def rec2(n):
    def f(*a, **k):
        r = reals[n](*a, **k)
        key = sid_of(n, a) + ("|idle=%s" % a[2] if n == "_session_awaiting" else "")
        by_sid[n][key] = json.loads(json.dumps(r, default=list))
        return r
    return f
for n in RECORD:
    setattr(km, n, rec2(n))
clear_memos(); km.build_feed(NOW, live)
for n in RECORD:
    setattr(km, n, reals[n])
def rp2(n):
    def f(*a, **k):
        key = sid_of(n, a) + ("|idle=%s" % a[2] if n == "_session_awaiting" else "")
        return by_sid[n][key]
    return f
saved = {n: getattr(km, n) for n in ("_parse_cached", "_parse", "_feed_goals_keyed")}
saved_peer = jd.load_goals_shared_or_fault
for n in RECORD:
    setattr(km, n, rp2(n))
km._parse_cached = lambda path: trees.get(paths.get(str(path)))
km._parse = lambda path, sid, now: trees[paths[str(path)]]
km._feed_goals_keyed = lambda sid: (stores[sid], None)
jd.load_goals_shared_or_fault = lambda psid: (peer_stores.get(psid) or {"nodes": {}, "status": {}}, None)
clear_memos()
feed3 = km.build_feed(NOW, live)
for n in RECORD:
    setattr(km, n, reals[n])
for n, v in saved.items():
    setattr(km, n, v)
jd.load_goals_shared_or_fault = saved_peer
canon3 = json.dumps(feed3, sort_keys=True, default=raise_default, allow_nan=False)
freeze.stop()
print(json.dumps({"full_replay_equal": canon3 == canon, "recorded_by_sid": {n: sorted(v) for n, v in by_sid.items()}}))
if canon3 != canon:
    a, b = json.loads(canon), json.loads(canon3)
    for i, (x, y) in enumerate(zip(a["asks"], b["asks"])):
        if x != y:
            print("diff card", i, [k for k in set(x) | set(y) if x.get(k) != y.get(k)])
