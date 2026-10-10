#!/usr/bin/env python3
"""Lab benchmark for the connect push a page gets after load (Handler._push_one -> _push([client], connect=True)), on a
SYNTHETIC board: invented prompts and replies, placeholder ids, the notes-api demo world, no real session data (CLAUDE.md).

Not a test (pytest collects test_*.py only). It writes N sessions with M-record transcripts into a temp state root and a
temp Claude directory, imports the kernel in-process through tools/perf-bench.py's loader and side-effect guards (no
spawn, no network, no notification; writes land in a shadow), and runs the connect push the way the kernel's socket lane
runs it: Handler._push_one on a thread of its own, where no pusher-cycle scope is open. Rows:

  cold      a fresh chat page on a cold kernel: --scenario full, a page that declared no diet (every tab built and sent
            whole); --scenario diet, a skeleton=1 page (the active tab built, a status per other tab)
  prefetch  (diet) the page's idle prefetch: one needFull per skeleton tab, in the kernel's order, one at a time
  warm      a second page that declared no diet, on the now-warm kernel
  needfull  one needFull on the warm kernel, through the socket's dispatch (Handler._dispatch_ws)

    python3 tests/bench_connect_push.py --sessions 38 --records 3000 --big 24000 --scenario full
    python3 tests/bench_connect_push.py --sessions 38 --records 3000 --big 24000 --scenario diet --repo ../romp-base
    python3 tests/bench_connect_push.py --sessions 30 --records 3000 --profile /tmp/connect.prof

Each row prints the lane thread's CPU seconds (wall time on a shared box moves with every other process) and what the push
did: liveness reads (Sessions.live), discovery walks (jd.discover and its fingerprint), tab signatures and builds. --repo
benches another checkout's kernel with this checkout's harness. With --profile the cold push runs under cProfile and the
top functions print by cumulative time.

Measured 2026-10-10 on the 38-session board above (one 24,000-record transcript), before -> after this change:
  a page that declared no diet, cold   21.1 s, 381 liveness reads, 82 walks -> 17.0 s, 1 read, 2 walks
  a skeleton page, cold                 2.0 s, 122 reads                     ->  0.7 s, 1 read
  its prefetch, 37 needFulls           42.1 s (1.14 s each), 777 signatures, 2,257 reads -> 15.7 s (0.43 s each), 74, 37
"""
import argparse
import cProfile
import importlib.util
import json
import os
import pstats
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.realpath(__file__))
REPO = os.path.dirname(HERE)


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")


class Gen:
    """Eight records a turn: a prompt, three tool rounds (thinking + text + tool_use, then its result), a closing reply."""

    def __init__(self, sid, cwd, t0):
        self.sid, self.cwd, self.n, self.parent, self.t = sid, cwd, 0, None, t0

    def rec(self, typ, message, **extra):
        self.t += 3
        self.n += 1
        u = "aaaaaaaa-%04d-0000-0000-%012d" % (int(self.sid[-4:]), self.n)
        r = {"type": typ, "timestamp": iso(self.t), "uuid": u, "parentUuid": self.parent, "sessionId": self.sid,
             "cwd": self.cwd, "version": "2.1.0", "gitBranch": "main", "message": message}
        r.update(extra)
        self.parent = u
        return r

    def turn(self, i):
        out = [self.rec("user", {"role": "user", "content": "step %d: tighten the search index and rerun the suite" % i},
                        promptSource="typed")]
        for k in range(3):
            tid = "toolu_%06d_%d" % (i, k)
            name = ("Edit", "Bash", "Read")[k]
            inp = ({"file_path": "/TESTDIR/notes-api/search.py", "old_string": "limit = %d\n" % k,
                    "new_string": "limit = %d\n" % (k + 1)} if name == "Edit" else
                   {"command": "uv run pytest -q tests/test_search.py::test_%d" % i} if name == "Bash" else
                   {"file_path": "/TESTDIR/notes-api/tests/test_search.py"})
            out.append(self.rec("assistant", {"role": "assistant", "model": "claude-sonnet-4",
                                              "content": [{"type": "thinking", "thinking": "Considering step %d round %d." % (i, k), "signature": ""},
                                                          {"type": "text", "text": "Round %d: adjusting the search limit." % k},
                                                          {"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                              "stop_reason": "tool_use"}))
            out.append(self.rec("user", {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                                     "content": "ok: %d lines changed\n" % (k + 1) * 8}]},
                                toolUseResult={"filePath": inp.get("file_path", ""), "stdout": "ok"}))
        out.append(self.rec("assistant", {"role": "assistant", "model": "claude-sonnet-4",
                                          "content": [{"type": "text", "text": "Step %d done: the index is tighter and the suite passes." % i}],
                                          "stop_reason": "end_turn"}))
        return out


def write_world(root, n_sessions, n_records, big_records=0):
    """The synthetic board: state root (names/, sdk/, states/, session-hosts off) and a Claude directory with one
    transcript per session, spread over three launch directories of the notes-api world."""
    state = os.path.join(root, "state", "romp")
    claude = os.path.join(root, "claude")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    with open(os.path.join(state, "session-hosts"), "w") as f:
        f.write("off")
    now = int(time.time())
    cwds = [os.path.join(root, "work", n) for n in ("notes-api", "notes-web", "notes-tests")]
    for c in cwds:
        os.makedirs(c, exist_ok=True)
    sids = []
    for i in range(n_sessions):
        sid = "11111111-2222-3333-4444-%012d" % (i + 1)
        cwd = cwds[i % len(cwds)]
        name = "s%03d" % (i + 1)
        proj = os.path.join(claude, "projects", "".join(ch if ch.isalnum() else "-" for ch in os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        g = Gen(sid, cwd, now - 2 * 86400 + i * 60)
        recs = big_records if (big_records and i == 0) else n_records
        with open(os.path.join(proj, sid + ".jsonl"), "w") as f:
            for t in range(max(1, recs // 8)):
                for r in g.turn(t):
                    f.write(json.dumps(r) + "\n")
        with open(os.path.join(state, "names", sid), "w") as f:
            f.write("%s\t%s\t#4a9eff\n" % (name, cwd))
        with open(os.path.join(state, "sdk", sid + ".json"), "w") as f:
            json.dump({"sid": sid, "alive": True, "name": name, "cwd": cwd, "model": "claude-sonnet-4"}, f)
        with open(os.path.join(state, "states", sid + ".jsonl"), "w") as f:
            f.write(json.dumps({"t": now - 600, "state": "waiting"}) + "\n")
        sids.append(sid)
    return state, claude, sids


def load_bench_tools():
    spec = importlib.util.spec_from_file_location("romp_perf_bench_tools", os.path.join(REPO, "tools", "perf-bench.py"))
    pb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pb)
    return pb


def on_lane(fn):
    """Run fn on a fresh thread (no pusher scope, as the socket lane), return its CPU seconds on that thread (wall time on a
    shared box moves with every other process; the thread's own CPU does not)."""
    box = {}

    def run():
        t0 = time.thread_time()
        try:
            fn()
        except BaseException as e:      # noqa: BLE001 - reported, the bench goes on
            box["err"] = e
        box["s"] = time.thread_time() - t0
    t = threading.Thread(target=run, name="ws-lane")
    t.start()
    t.join()
    if "err" in box:
        raise box["err"]
    return box["s"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", type=int, default=30)
    ap.add_argument("--records", type=int, default=3000)
    ap.add_argument("--big", type=int, default=0, help="records in the first session's transcript (one large tab)")
    ap.add_argument("--repo", default=REPO)
    ap.add_argument("--profile", help="cProfile the cold push into this file and print its top functions")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--scenario", choices=("full", "diet"), default="full",
                    help="full: a page that declared no diet (every tab built cold); diet: a skeleton=1 page, then its prefetch")
    args = ap.parse_args(argv)
    root = tempfile.mkdtemp(prefix="romp-connect-lab-")
    try:
        state, claude, sids = write_world(root, args.sessions, args.records, args.big)
        private = os.path.join(root, "private")
        os.makedirs(private)
        pb = load_bench_tools()
        pb.prepare_env(state, claude, private)
        rec = pb.new_recorder()
        shadow = pb.StateShadow(state, os.path.join(private, "shadow"), rec["refused_writes"])
        km, sbmod = pb.load_kernel(os.path.realpath(args.repo), shadow)
        be = pb.make_backend(sbmod, state, False, False)
        km._sdk_backend = be
        if hasattr(km, "_codex_backend"):
            km._codex_backend = False
        pb.install_guards(km, sbmod, shadow, rec, no_git=True)
        km._BOOT_MARKS.setdefault("firstServe", time.time())

        # what each row did: liveness reads (Sessions.live, the read a scope serves once), discovery walks (jd.discover and
        # its fingerprint), tab signatures and builds
        counts = {}
        for mod, name, label in ((km.jd, "discover", "discover"), (km.jd, "_discover_fingerprint", "fingerprint"),
                                 (km.Sessions, "live", "liveness"), (km, "build_session", "builds"),
                                 (km, "_chat_build_sig", "signatures")):
            real = getattr(mod, name, None)
            if real is None:
                continue

            def counted(*a, _real=real, _label=label, **k):
                counts[_label] = counts.get(_label, 0) + 1
                return _real(*a, **k)
            setattr(mod, name, staticmethod(counted) if mod is km.Sessions else counted)

        handler = km.Handler.__new__(km.Handler)     # the socket's dispatch, for the needFull arm as the lane runs it

        def fresh(active, diet=False):
            c = pb.fake_client(km, "chat", active)
            c.update({"proto": 2, "handshake": True})
            if diet:                                 # the skeleton=1 dial's arming, as Handler._ws leaves it past the ready arm
                c.update({"reconnect": True, "dietSkeleton": True})
            return c

        def row(label, secs, c, n=1):
            print("%-12s %8.2f s cpu%s   frames=%-5d bytes=%-10d %s" % (
                label, secs, ("  (%d pushes, %.3f s each)" % (n, secs / n)) if n > 1 else "", c["frames"],
                sum(c["bytes"].values()), " ".join("%s=%d" % kv for kv in sorted(counts.items()))), flush=True)
            counts.clear()

        def connect(c):
            return on_lane(lambda: km.Handler._push_one(handler, c))

        def need_full(c, sid):
            return on_lane(lambda: handler._dispatch_ws({"type": "needFull", "id": sid, "why": "prefetch"}, c))

        print("board: %d sessions x %d records%s, %s" % (args.sessions, args.records,
                                                         (" (first: %d)" % args.big) if args.big else "", args.scenario), flush=True)
        active = sids[-1]
        c = fresh(active, diet=args.scenario == "diet")
        km._clients[:] = [c]
        if args.profile:
            pr = cProfile.Profile()

            def prof():
                pr.enable()
                try:
                    km.Handler._push_one(handler, c)
                finally:
                    pr.disable()
            secs = on_lane(prof)
            pr.dump_stats(args.profile)
        else:
            secs = connect(c)
        row("cold", secs, c)
        if args.scenario == "diet":
            # the page's idle prefetch: one needFull per skeleton tab, one at a time, each answered before the next
            held = list((c.get("skeletonOrder") or []))
            t = 0.0
            for sid in held:
                t += need_full(c, sid)
            row("prefetch", t, c, n=max(1, len(held)))
        c2 = fresh(active)
        km._clients[:] = [c2]
        row("warm", connect(c2), c2)
        row("needfull", need_full(c2, sids[0]), c2)
        if rec["refused_writes"] or rec["spawns"]:
            print("guards: refused writes %d, spawns %d" % (len(rec["refused_writes"]), len(rec["spawns"])))
        if args.profile:
            st = pstats.Stats(args.profile)
            st.sort_stats("cumulative").print_stats(45)
            st.sort_stats("tottime").print_stats(25)
    finally:
        if not args.keep:
            shutil.rmtree(root, ignore_errors=True)
        else:
            print("kept", root)


if __name__ == "__main__":
    main()
