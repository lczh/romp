#!/usr/bin/env python3
"""unwatched_tabs_lab: what one page feels on a kernel where every session is working and only one tab is on screen.

The kernel stops rebuilding a chat tab no connected page is watching (kernel.py, _rest_unwatched): such a tab's page keeps
its status, and the tab is built at once when the page opens it. This tool measures the difference in a lab, with nothing
live: a hermetic kernel of this checkout (a temporary state root with session hosts off, no CLI, none of a live kernel's
exports in its environment; tests/test_ship_reship_served.py kernel_env), --sessions synthetic sessions of invented
notes-api turns in the CLI's record shapes, and one chat page spoken as the real one speaks, over a WebSocket of this
tool's own (no browser):

  it dials as the chat pane does (the diet, one watched tab), says ready, and fills every other tab once, one ask at a
  time, as the page's idle prefetch does, so the kernel holds a built copy of every tab for it, the state a board is in
  minutes after a page loads;
  then every session works: it appends a step of one open turn (a line of text and a tool call, then the tool's
  result) every --interval seconds for --seconds, each round followed by the wake a session's stream gives the pusher
  (POST /tick), and the tool times, for the WATCHED tab, the append to the frame that carries it (the page's update
  latency), and reads the kernel's own /perf for the pusher's cycles, its stages and its chat builds over the same span;
  then it opens --clicks other tabs, one at a time, as a click does (activeTab, and the ask a tab that went out of date
  sends), and times each from the click to the page holding the session's newest step as of the click (click to
  fresh; 0 when a frame it already had carried that step).

--tracking sets the kernel's Task tracking switch (default off, the measured machine's setting: no feed is built). On,
the feed build reads every session each cycle and parses the ones no chat build parsed for it, so part of what the chat
stage stops paying moves there; the stage split in the --json rows says how much.

Two roads, one kernel boot each, over the same transcripts:

    before   the page dials without `rest=1`: it cannot take a loaded tab back, so the kernel rebuilds every tab on
             every cycle its transcript moved, as it did for every page before this change
    after    the page dials with `rest=1`, as the chat pane does now

One line per road. One run per point, on whatever else the machine is doing (the load average says how much that was):
a measurement, not a distribution.

    nice -n 10 tools/unwatched_tabs_lab.py                       # 85 sessions of 1 MB, 60 s, both roads, tracking off
    nice -n 10 tools/unwatched_tabs_lab.py --tracking on --json /tmp/unwatched-on.json
    nice -n 10 tools/unwatched_tabs_lab.py --sessions 20 --kb 200 --seconds 20 --json /tmp/unwatched.json

Outside CI on purpose (a hundred megabytes and minutes). The temporary root is removed afterwards unless --keep."""
import argparse
import base64
import hashlib
import json
import os
import random
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

TOOL = os.path.realpath(__file__)
REPO = os.path.dirname(os.path.dirname(TOOL))
sys.path[:0] = [REPO, os.path.join(REPO, "tests")]
import test_ship_reship_served as _lab  # noqa: E402  the lab kernel's environment: named, never a copy of the runner's

ROADS = ("before", "after")
WORDS = ("notes", "route", "pages", "cursor", "tags", "filter", "owner", "search", "index", "rebuilt", "retry", "cap",
         "backoff", "review", "branch", "merge", "green", "fixture", "suite", "schema")
GROUPS = ("web", "api", "tests")


def sid_of(i):
    return "%08d-1111-2222-3333-%012d" % (i, i)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class Transcript:
    """One synthetic session's transcript: a typed prompt and an assistant's reply per turn, chained by parentUuid, to
    start with; then ONE open turn the session works in, a prompt followed by steps (a line of text and a tool call,
    then the tool's result), appended the way a working session appends. A step ends no turn, as a working session's
    appends end none: what a turn's end sets off in a kernel (the judges, the settle writes) is not what this measures."""
    def __init__(self, path, i, rnd):
        self.path, self.i, self.rnd, self.k, self.prev, self.sid = path, i, rnd, 0, None, sid_of(i)
        self.t = int(time.time()) - 6 * 3600
        self.steps = 0                               # the steps appended to the open turn; 0 until the turn opens

    def step(self, mark=""):
        """Append one step of the open turn (the prompt that opens it first). Returns the words that name the step."""
        k, self.k = self.k, self.k + 1
        ids = ["%08d-4444-5555-6666-%012d" % (self.i, 2 * k), "%08d-4444-5555-6666-%012d" % (self.i, 2 * k + 1)]
        self.t += 2
        recs = []
        if not self.steps:
            recs.append({"type": "user", "uuid": ids[0] + "-p", "parentUuid": self.prev, "timestamp": _iso(self.t),
                         "sessionId": self.sid, "promptSource": "typed",
                         "message": {"role": "user", "content": "work through the notes api, step by step"}})
            self.prev = ids[0] + "-p"
        self.steps += 1
        name = "step %d of %s" % (self.steps, self.sid[:8])
        tid = "toolu_%08d_%d" % (self.i, k)
        say = " ".join(self.rnd.choice(WORDS) for _ in range(40))
        recs.append({"type": "assistant", "uuid": ids[0], "parentUuid": self.prev, "timestamp": _iso(self.t), "sessionId": self.sid,
                     "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "tool_use", "content": [
                         {"type": "text", "text": "%s%s: %s." % (mark + " " if mark else "", name, say)},
                         {"type": "tool_use", "id": tid, "name": "Bash", "input": {"command": "npm test", "description": "run the notes-api tests"}}]}})
        recs.append({"type": "user", "uuid": ids[1], "parentUuid": ids[0], "timestamp": _iso(self.t + 1), "sessionId": self.sid,
                     "message": {"role": "user", "content": [
                         {"type": "tool_result", "tool_use_id": tid, "content": " ".join(self.rnd.choice(WORDS) for _ in range(80))}]}})
        self.prev = ids[1]
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("".join(json.dumps(r) + "\n" for r in recs))
        return name

    def _turn(self, mark=""):
        k, self.k = self.k, self.k + 1
        u, a = "%08d-4444-5555-6666-%012d" % (self.i, 2 * k), "%08d-4444-5555-6666-%012d" % (self.i, 2 * k + 1)
        self.t += 2
        ask = " ".join(self.rnd.choice(WORDS) for _ in range(20))
        say = " ".join(self.rnd.choice(WORDS) for _ in range(120))
        recs = [{"type": "user", "uuid": u, "parentUuid": self.prev, "timestamp": _iso(self.t), "sessionId": self.sid,
                 "message": {"role": "user", "content": "turn %d: %s" % (k, ask)}},
                {"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": _iso(self.t + 1), "sessionId": self.sid,
                 "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                             "content": [{"type": "text", "text": "%sanswer %d: %s." % (mark + " " if mark else "", k, say)}]}}]
        self.prev = a
        return "".join(json.dumps(r) + "\n" for r in recs)

    def write(self, nbytes):
        n = 0
        with open(self.path, "w", encoding="utf-8") as f:
            while n < nbytes:
                s = self._turn()
                f.write(s)
                n += len(s)
        return n



# ── the page: a WebSocket client of the standard library, enough for text frames in both directions ─────────────────
class Page:
    def __init__(self, port, query):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET /ws?%s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (query, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("the kernel closed the dial: %r" % head[:200])
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise RuntimeError("the dial was refused: %r" % head[:200])
        want = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
        if want not in head:
            raise RuntimeError("the handshake's accept key does not match")
        self.buf = head.split(b"\r\n\r\n", 1)[1]
        self.sock.settimeout(None)
        self.lock = threading.Lock()                 # the frames below, appended by the reader thread
        self.cond = threading.Condition(self.lock)
        self.frames = []                             # (monotonic arrival, type, id, the raw text)
        self.closed = False
        self.wlock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 16)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _read(self):
        text = b""
        try:
            while True:
                b0, b1 = self._need(2)
                n = b1 & 0x7F
                if n == 126:
                    n = struct.unpack(">H", self._need(2))[0]
                elif n == 127:
                    n = struct.unpack(">Q", self._need(8))[0]
                payload = self._need(n)              # a server frame is never masked
                op = b0 & 0x0F
                if op == 0x9:
                    self._send(0xA, payload)
                    continue
                if op == 0x8:
                    break
                if op in (0x0, 0x1):
                    text += payload
                    if b0 & 0x80:
                        raw, text = text.decode("utf-8", "replace"), b""
                        t = time.monotonic()
                        m = re.match(r'\{"type": ?"(\w+)"', raw)
                        i = re.search(r'"id": ?"([^"]+)"', raw[:400])
                        with self.cond:
                            self.frames.append((t, m.group(1) if m else "", i.group(1) if i else "", raw))
                            self.cond.notify_all()
        except (EOFError, OSError):
            pass
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    def _send(self, op, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x80 | op]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536
                                     else bytes([0x80 | 127]) + struct.pack(">Q", n))
        with self.wlock:
            self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def say(self, msg):
        self._send(0x1, json.dumps(msg).encode())

    def wait(self, pred, since=0, timeout=120.0):
        """The first frame at or after index `since` that `pred(type, id, raw)` holds for: (its index, its arrival), or
        None when the timeout passes or the socket ends."""
        end = time.monotonic() + timeout
        i = since
        with self.cond:
            while True:
                while i < len(self.frames):
                    t, typ, fid, raw = self.frames[i]
                    if pred(typ, fid, raw):
                        return i, t
                    i += 1
                left = end - time.monotonic()
                if left <= 0 or self.closed:
                    return None
                self.cond.wait(min(left, 1.0))

    def count(self, since=0):
        with self.lock:
            return len(self.frames)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ── the lab ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _get(port, token, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s%stoken=%s" % (port, path, "&" if "?" in path else "?", token), timeout=30) as r:
        return json.loads(r.read().decode())


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def _tick(port, token):
    req = urllib.request.Request("http://127.0.0.1:%d/tick?token=%s" % (port, token), data=b"{}", method="POST",
                                 headers={"X-Romp-Token": token, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass                                         # the pusher's own half-second poll is the backstop


def run_road(road, a):
    # the tests package this tool borrows the kernel's environment from points TMPDIR at a root of its own and removes it
    # at exit; a root to keep goes beside it, in the system's temp directory
    lab = tempfile.mkdtemp(prefix="unwatched-tabs-lab-", dir=os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") if a.keep else None)
    kernel = page = None
    try:
        state, cwd, claude = os.path.join(lab, "xdg", "romp"), os.path.join(lab, "proj"), os.path.join(lab, "claude")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        with open(os.path.join(state, "session-hosts"), "w") as f:
            f.write("off\n")
        if a.tracking == "off":
            with open(os.path.join(state, "task-tracking.json"), "w") as f:
                json.dump({"enabled": False}, f)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        rnd = random.Random(7)
        scripts, total = [], 0
        for i in range(1, a.sessions + 1):
            sid, name = sid_of(i), "%s-%02d" % (GROUPS[i % 3], i)
            with open(os.path.join(state, "names", sid), "w") as f:
                f.write("%s\t%s\t\t\n" % (name, cwd))
            with open(os.path.join(state, "sdk", sid + ".json"), "w") as f:
                json.dump({"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                           "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}, f)
            tr = Transcript(os.path.join(proj, sid + ".jsonl"), i, rnd)
            total += tr.write(a.kb * 1024)
            scripts.append(tr)
        port = _lab._free_port()
        token = "-".join(("lab", "unwatched", "tabs"))   # the lab kernel's own serve token, put together here (no literal for a secret scan to trip on)
        dist = os.path.join(lab, "dist")
        os.makedirs(dist, exist_ok=True)             # no page is served here: the dial is this tool's own
        env = _lab.kernel_env(lab, claude, dist, port, token)
        klog = open(os.path.join(lab, "kernel.log"), "ab")
        kernel = subprocess.Popen([os.path.join(REPO, "bin", "romp-kernel")], stdout=klog, stderr=subprocess.STDOUT, env=env)
        for _ in range(240):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
                break
            except Exception:
                if kernel.poll() is not None:
                    raise RuntimeError("the lab kernel exited at boot: see %s" % klog.name)
                time.sleep(0.5)
        else:
            raise RuntimeError("the lab kernel never served /healthz")
        watched = scripts[0]
        by_sid = {t.sid: t for t in scripts}
        page = Page(port, "app=chat&iid=lab-page&active=%s&skeleton=1%s&token=%s" % (watched.sid, "&rest=1" if road == "after" else "", token))
        page.say({"type": "ready", "proto": 2})
        got = page.wait(lambda typ, fid, raw: typ == "session" and fid == watched.sid, timeout=600)
        if got is None:
            raise RuntimeError("the watched tab's first frame never came")
        # the idle prefetch: every other tab, one ask at a time
        t_fill = time.monotonic()
        for tr in scripts[1:]:
            at = page.count()
            page.say({"type": "needFull", "id": tr.sid, "why": "prefetch"})
            if page.wait(lambda typ, fid, raw, s=tr.sid: typ == "session" and fid == s, since=at, timeout=600) is None:
                raise RuntimeError("a prefetched tab's frame never came: %s" % tr.sid)
        fill_s = time.monotonic() - t_fill

        # ── the steady span: every session appends, one tab is watched ──
        stop, marks = threading.Event(), {}

        def writer():
            n = 0
            while not stop.is_set():
                n += 1
                for tr in scripts:
                    if tr is state_now["watched"]:
                        mark = "labmark%06d" % n
                        tr.step(mark)
                        marks[mark] = time.monotonic()
                    else:
                        tr.step()
                state_now["round"] = n
                if not a.no_wake:
                    _tick(port, token)
                stop.wait(a.interval)
        state_now = {"watched": watched, "round": 0}
        p0, f0 = _get(port, token, "/perf"), page.count()
        t0 = time.monotonic()
        th = threading.Thread(target=writer, daemon=True)
        th.start()
        time.sleep(a.seconds)
        span = time.monotonic() - t0
        p1 = _get(port, token, "/perf")
        lat, span_marks = [], sorted(marks.items())  # the appends of the span: the writer goes on through the clicks
        for mark, t_app in span_marks:
            hit = page.wait(lambda typ, fid, raw, m=mark: fid == watched.sid and typ in ("chatTail", "session") and m in raw,
                            since=f0, timeout=0.01)
            if hit is not None:
                lat.append(hit[1] - t_app)
        with page.lock:
            fr = page.frames[f0:]
        kinds = {}
        for _, typ, fid, raw in fr:
            kinds[typ] = kinds.get(typ, 0) + 1
        rests = sum(1 for _, typ, fid, raw in fr if typ == "status" and '"rest": true' in raw)
        statuses_for = len({fid for _, typ, fid, raw in fr if typ == "status"})

        # ── the clicks: open other tabs while every session keeps appending ──
        clicks = []
        order = [t for t in scripts[1:]]
        random.Random(11).shuffle(order)
        for tr in order[:a.clicks]:
            time.sleep(a.interval)
            newest = "step %d of %s:" % (tr.steps, tr.sid[:8])   # the tab's newest step as the click lands
            tc = time.monotonic()
            page.say({"type": "activeTab", "id": tr.sid})
            if road == "after":                      # a tab that went out of date shows the loader and asks (render.ts showActive)
                page.say({"type": "needFull", "id": tr.sid, "why": "skeleton-click"})
            hit = page.wait(lambda typ, fid, raw, s=tr.sid, m=newest: fid == s and typ in ("chatTail", "session") and m in raw,
                            since=f0, timeout=600)   # from the span's start: a frame the page already held may carry it
            clicks.append(max(0.0, hit[1] - tc) if hit is not None else None)
            state_now["watched"] = tr
        stop.set()
        th.join(timeout=10)
        p2 = _get(port, token, "/perf")

        def d(key, a0, a1):
            x0, x1 = a0, a1
            for k in key:
                x0, x1 = (x0 or {}).get(k), (x1 or {}).get(k)
            return (x1 or 0) - (x0 or 0)
        cyc = d(("pusher", "cycles"), p0, p1)
        chat0, chat1 = p0["builds"]["chat"], p1["builds"]["chat"]
        ok_clicks = [c for c in clicks if c is not None]
        return {
            "road": road, "tracking": a.tracking, "sessions": a.sessions, "transcriptMB": round(total / 1048576, 1),
            "prefetchFillS": round(fill_s, 1),
            "spanS": round(span, 1), "rounds": state_now["round"],
            "cycles": cyc, "cycleMsMean": round(d(("pusher", "cycle_ms_sum"), p0, p1) / cyc, 1) if cyc else None,
            "cycleMsMaxSinceBoot": round(p1["pusher"]["cycle_ms_max"], 1),
            "pushChatS": round(d(("stages_ms", "push.chat"), p0, p1) / 1000, 2),
            "chatBuilt": chat1["built"] - chat0["built"], "watchedBuilt": chat1["active_built"] - chat0["active_built"],
            "unwatchedBuilt": chat1["bg_built"] - chat0["bg_built"],
            "rested": chat1.get("rested", 0) - chat0.get("rested", 0), "coldSkipped": chat1["coldSkipped"] - chat0["coldSkipped"],
            "restHeld": {k: v - (chat0.get("restHeld") or {}).get(k, 0) for k, v in (chat1.get("restHeld") or {}).items()},
            "pusherCpuS": round(d(("pusher", "cycle_cpu_ms_sum"), p0, p1) / 1000, 2),
            # where the pusher and its jobs spent the span, the largest first (the kernel's own stage clocks, in seconds)
            "stagesS": dict(sorted(((k, round(d(("stages_ms", k), p0, p1) / 1000, 2)) for k in (p1.get("stages_ms") or {})
                                    if d(("stages_ms", k), p0, p1) >= 50), key=lambda kv: -kv[1])[:12]),
            "watchedUpdates": len(lat), "watchedAppends": len(span_marks),
            "watchedLatencyMs": {"p50": round(_pct(lat, 0.5) * 1000) if lat else None, "p90": round(_pct(lat, 0.9) * 1000) if lat else None,
                                 "max": round(max(lat) * 1000) if lat else None},
            "restFrames": rests, "tabsWithStatusFrames": statuses_for, "frames": kinds,
            "clicks": len(clicks), "clicksUnanswered": len(clicks) - len(ok_clicks),
            "clickToFreshMs": {"p50": round(_pct(ok_clicks, 0.5) * 1000) if ok_clicks else None,
                               "max": round(max(ok_clicks) * 1000) if ok_clicks else None},
            "clickBuilds": p2["builds"]["chat"]["built"] - chat1["built"],
            "loadavg": round(os.getloadavg()[0], 2),
        }
    finally:
        if page is not None:
            page.close()
        if kernel is not None:
            kernel.kill()                            # this tool's own child, by its handle
            kernel.wait()
        if a.keep:
            print("kept: %s" % lab, file=sys.stderr)
        else:
            shutil.rmtree(lab, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", type=int, default=85)
    ap.add_argument("--kb", type=int, default=1024, help="each transcript's size at the start, in KB (default 1024)")
    ap.add_argument("--seconds", type=float, default=60.0, help="the steady span (default 60)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between a session's appends (default 2)")
    ap.add_argument("--clicks", type=int, default=10, help="tabs opened after the steady span (default 10)")
    ap.add_argument("--no-wake", action="store_true", help="no POST /tick after a round: the pusher's half-second poll alone")
    ap.add_argument("--tracking", choices=("off", "on"), default="off", help="the kernel's Task tracking switch (default off)")
    ap.add_argument("--roads", default=",".join(ROADS))
    ap.add_argument("--json", help="write the rows here too")
    ap.add_argument("--keep", action="store_true", help="keep each road's temporary root (the kernel's log is in it)")
    a = ap.parse_args()
    if a.sessions * a.kb > 300 * 1024:
        ap.error("at most 300 MB of transcripts in one run")
    rows = []
    for road in [r for r in a.roads.split(",") if r in ROADS]:
        row = run_road(road, a)
        rows.append(row)
        w, c = row["watchedLatencyMs"], row["clickToFreshMs"]
        print("%-6s tracking %s, %d sessions, %s MB | %d cycles, mean %s ms, chat stage %s s of %s s | builds: %d watched, %d unwatched, %d rested | "
              "watched tab: %d of %d appends seen, p50 %s ms, p90 %s ms, max %s ms | click to fresh: p50 %s ms, max %s ms (%d clicks, "
              "%d unanswered, %d builds) | load %s"
              % (road, row["tracking"], row["sessions"], row["transcriptMB"], row["cycles"], row["cycleMsMean"], row["pushChatS"], row["spanS"],
                 row["watchedBuilt"], row["unwatchedBuilt"], row["rested"], row["watchedUpdates"], row["watchedAppends"],
                 w["p50"], w["p90"], w["max"], c["p50"], c["max"], row["clicks"], row["clicksUnanswered"], row["clickBuilds"],
                 row["loadavg"]))
        sys.stdout.flush()
    if a.json:
        with open(a.json, "w") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
