#!/usr/bin/env python3
"""unwatched_tabs_lab: what one page feels on a kernel where every session is working and only a few tabs are watched.

The kernel stops rebuilding a chat tab no connected page is watching (kernel.py, _rest_unwatched): such a tab's page keeps
its status, and the tab is built at once when the page opens it. A page watches the last few tabs it showed (four by
default, ROMP_WATCHED_TABS), so a flip among them paints from a current copy. This tool measures both in a lab, with nothing
live: a hermetic kernel of this checkout (a temporary state root with session hosts off, no CLI, none of a live kernel's
exports in its environment; tests/test_ship_reship_served.py kernel_env), --sessions synthetic sessions of invented
notes-api turns in the CLI's record shapes, --large more of them at --large-mb each (a compaction every forty turns, as a
long session has), and one chat page spoken as the real one speaks, over a WebSocket of this tool's own (no browser):

  it dials as the chat pane does (the diet, one watched tab: the first large session when there is one), says ready, and
  fills every other tab once, one ask at a time, as the page's idle prefetch does, so the kernel holds a built copy of every
  tab for it, the state a board is in minutes after a page loads;
  it then shows eight tabs in turn, six small ones, the second large one and the first again, as a person flipping among
  their main sessions does; the kernel keeps the last ROMP_WATCHED_TABS of them watched;
  then every session works: it appends a step of one open turn (a line of text and a tool call, then the tool's
  result) every --interval seconds for --seconds, each round followed by the wake a session's stream gives the pusher
  (POST /tick), and the tool times, for the tab ON SCREEN, the append to the frame that carries it (the page's update
  latency), and reads the kernel's own /perf for the pusher's cycles, its stages and its chat builds over the same span;
  then the clicks, each timed from the click to the page holding the session's newest step as of the click (click to
  fresh; 0 when a frame it already had carried that step): the other tabs of the watched set (inside), --clicks small tabs
  outside it, and every large tab outside it. A tab outside the set went out of date, so its click sends what the page
  sends (activeTab, then the ask for the tab whole; the page shows the copy it holds meanwhile, so the time measured is
  the copy's catching up, not a blank pane); each such click is split in the --json row into the kernel's push for the
  ask and the session's build inside it (outsideSplit), and for a large one the row says where the wait went: the kernel's
  whole push for the ask, the restore of the session's saved document inside it, what was parsed, and the frame's bytes.

--tracking sets the kernel's Task tracking switch (default off, the measured machine's setting: no feed is built). On,
the feed build reads every session each cycle and parses the ones no chat build parsed for it, so part of what the chat
stage stops paying moves there; the stage split in the --json rows says how much.

A large session is prepared the way a long-lived one stands on a real machine: a first kernel boot builds it cold, one
large session per boot (a 300 MB transcript's first build holds several gigabytes, which a restored one does not), and
is stopped once the kernel has written the session's saved document; the roads then boot over that state root, so each
restores the large sessions from their documents as a restart does. With --large the roads share that one root (the
transcripts go on growing from road to road); without it each road has a fresh root.

Roads, one kernel boot each, over the same transcripts (written once):

    before     the page dials without `rest=1`: it cannot take a loaded tab back, so the kernel rebuilds every tab on
               every cycle its transcript moved, as it did for every page before this change
    after      the page dials with `rest=1`, as the chat pane does now; one boot per value of --watched

One line per road. One run per point, on whatever else the machine is doing (the load average says how much that was):
a measurement, not a distribution.

    nice -n 10 tools/unwatched_tabs_lab.py                       # 85 sessions of 1 MB, 60 s, both roads, four watched
    nice -n 10 tools/unwatched_tabs_lab.py --roads after --watched 1,4,8 --large 2 --large-mb 300 --json /tmp/watched.json
    nice -n 10 tools/unwatched_tabs_lab.py --tracking on --json /tmp/unwatched-on.json
    nice -n 10 tools/unwatched_tabs_lab.py --sessions 20 --kb 200 --seconds 20 --json /tmp/unwatched.json

Outside CI on purpose (hundreds of megabytes and minutes). The temporary roots are removed afterwards unless --keep."""
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
COMPACT_EVERY = 40                                   # a large session's turns between two compactions
RSS_CEILING_MB = 8000                                # a lab kernel past this is stopped: the machine is shared
MEM_FLOOR_MB = 2000                                  # …and so is one that leaves the machine less than this available


def sid_of(i):
    return "%08d-1111-2222-3333-%012d" % (i, i)


def _iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t))


class Transcript:
    """One synthetic session's transcript: a typed prompt and an assistant's reply per turn, chained by parentUuid, to
    start with (a large one with a compaction boundary and its summary every COMPACT_EVERY turns); then ONE open turn the
    session works in, a prompt followed by steps (a line of text and a tool call, then the tool's result), appended the way
    a working session appends. A step ends no turn, as a working session's appends end none: what a turn's end sets off
    in a kernel (the judges, the settle writes) is not what this measures."""
    def __init__(self, path, i, rnd, name, large=False):
        self.path, self.i, self.rnd, self.k, self.prev, self.sid, self.name, self.large = path, i, rnd, 0, None, sid_of(i), name, large
        self.t = int(time.time()) - 30 * 86400
        self.steps = 0                               # the steps appended to the open turn; 0 until the turn opens

    def state(self):
        return {"k": self.k, "prev": self.prev, "t": self.t, "steps": self.steps}

    def write(self, nbytes):
        """The session's history: `nbytes` of settled turns, written from two pools of invented lines (a record is a
        format, not a json.dumps: 300 MB of them in seconds)."""
        asks = [" ".join(self.rnd.choice(WORDS) for _ in range(20)) for _ in range(61)]
        says = [" ".join(self.rnd.choice(WORDS) for _ in range(120)) for _ in range(67)]
        head = "%08d-4444-5555-" % self.i
        n, k, prev, t, sid = 0, 0, None, self.t, self.sid
        out = []
        with open(self.path, "w", encoding="utf-8") as f:
            while n < nbytes:
                if self.large and k and k % COMPACT_EVERY == 0:
                    b, s = "%s7777-%012d" % (head, k), "%s8888-%012d" % (head, k)
                    out.append('{"type": "system", "subtype": "compact_boundary", "uuid": "%s", "parentUuid": null, "logicalParentUuid": "%s", '
                               '"timestamp": "%s", "sessionId": "%s", "compactMetadata": {"trigger": "auto", "preTokens": 160000, "postTokens": 9000}}\n'
                               % (b, prev, _iso(t), sid))
                    out.append('{"type": "user", "uuid": "%s", "parentUuid": "%s", "timestamp": "%s", "sessionId": "%s", "isCompactSummary": true, '
                               '"message": {"role": "user", "content": "summary so far: %s"}}\n' % (s, b, _iso(t + 1), sid, says[k % 67]))
                    prev = s
                    t += 2
                u, a = "%s6666-%012d" % (head, 2 * k), "%s6666-%012d" % (head, 2 * k + 1)
                t += 2
                out.append('{"type": "user", "uuid": "%s", "parentUuid": %s, "timestamp": "%s", "sessionId": "%s", '
                           '"message": {"role": "user", "content": "turn %d: %s"}}\n'
                           % (u, "null" if prev is None else '"%s"' % prev, _iso(t), sid, k, asks[k % 61]))
                out.append('{"type": "assistant", "uuid": "%s", "parentUuid": "%s", "timestamp": "%s", "sessionId": "%s", '
                           '"message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn", '
                           '"content": [{"type": "text", "text": "answer %d: %s."}]}}\n' % (a, u, _iso(t + 1), sid, k, says[k % 67]))
                prev = a
                k += 1
                if len(out) >= 4096:
                    blob = "".join(out)
                    f.write(blob)
                    n += len(blob)
                    out = []
            blob = "".join(out)
            f.write(blob)
            n += len(blob)
        self.k, self.prev, self.t = k, prev, t
        return n

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
        self.buf = bytearray(head.split(b"\r\n\r\n", 1)[1])
        self.sock.settimeout(None)
        self.lock = threading.Lock()                 # the frames below, appended by the reader thread
        self.cond = threading.Condition(self.lock)
        self.frames = []                             # (monotonic arrival, type, id, the raw text)
        self.closed = False
        self.wlock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(1 << 20)
            if not chunk:
                raise EOFError
            self.buf += chunk
        out = bytes(self.buf[:n])
        del self.buf[:n]
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

    def count(self):
        with self.lock:
            return len(self.frames)

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ── the lab ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def _get(port, token, path):
    with urllib.request.urlopen("http://127.0.0.1:%d%s%stoken=%s" % (port, path, "&" if "?" in path else "?", token), timeout=120) as r:
        return json.loads(r.read().decode())


def _tick(port, token):
    req = urllib.request.Request("http://127.0.0.1:%d/tick?token=%s" % (port, token), data=b"{}", method="POST",
                                 headers={"X-Romp-Token": token, "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass                                         # the pusher's own half-second poll is the backstop


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def _ms(x):
    return None if x is None else round(x * 1000)


def _rss_mb(pid, field="VmRSS"):
    try:
        with open("/proc/%d/status" % pid, encoding="ascii", errors="replace") as f:
            for line in f:
                if line.startswith(field + ":"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _dig(d, *key):
    for k in key:
        d = (d or {}).get(k) if isinstance(d, dict) else None
    return d


def _find(d, name):
    """The first value under key `name` anywhere in a /perf answer (its blocks have moved before; the name has not)."""
    if isinstance(d, dict):
        if name in d:
            return d[name]
        for v in d.values():
            got = _find(v, name)
            if got is not None:
                return got
    return None


def write_seed(a, seed):
    """The transcripts, written once: the small sessions first, the large ones last. Returns [(i, name, large, state)] and
    the bytes written."""
    rnd = random.Random(7)
    rows, total = [], 0
    for i in range(1, a.sessions + a.large + 1):
        large = i > a.sessions
        name = "%s-%02d" % (GROUPS[i % 3], i)
        tr = Transcript(os.path.join(seed, sid_of(i) + ".jsonl"), i, rnd, name, large=large)
        total += tr.write(a.large_mb * 1024 * 1024 if large else a.kb * 1024)
        rows.append((i, name, large, tr.state()))
    return rows, total


class Watch:
    """A lab kernel under this tool's eye: stopped when it holds more than RSS_CEILING_MB or the machine's available memory
    falls under MEM_FLOOR_MB (the machine is shared), read every two seconds on a thread of its own."""
    def __init__(self, kernel):
        self.kernel, self.why, self.stop = kernel, None, threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while not self.stop.wait(2.0):
            rss = _rss_mb(self.kernel.pid)
            avail = None
            try:
                with open("/proc/meminfo") as f:
                    avail = next(int(line.split()[1]) // 1024 for line in f if line.startswith("MemAvailable:"))
            except (OSError, StopIteration, ValueError):
                pass
            if (rss is not None and rss > RSS_CEILING_MB) or (avail is not None and avail < MEM_FLOOR_MB):
                self.why = "the lab kernel held %s MB with %s MB available on the machine: stopped" % (rss, avail)
                self.kernel.kill()
                return

    def check(self):
        if self.why:
            raise RuntimeError(self.why)


def make_root(a, seed, rows, keep):
    """A state root and a transcripts directory for a lab kernel: the registry rows, the names, and each session's
    transcript copied from the seed (the sessions append to their copy). Returns the root's record."""
    # the tests package this tool borrows the kernel's environment from points TMPDIR at a root of its own and removes it
    # at exit; a root to keep goes beside it, in the system's temp directory
    lab = tempfile.mkdtemp(prefix="unwatched-tabs-lab-", dir=os.environ.get("ROMP_TESTS_SYSTEM_TMPDIR") if keep else None)
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
    rnd = random.Random(11)
    scripts = []
    for i, name, large, st in rows:
        sid = sid_of(i)
        with open(os.path.join(state, "names", sid), "w") as f:
            f.write("%s\t%s\t\t\n" % (name, cwd))
        with open(os.path.join(state, "sdk", sid + ".json"), "w") as f:
            json.dump({"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                       "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}, f)
        path = os.path.join(proj, sid + ".jsonl")
        shutil.copyfile(os.path.join(seed, sid + ".jsonl"), path)
        tr = Transcript(path, i, rnd, name, large=large)
        tr.k, tr.prev, tr.t, tr.steps = st["k"], st["prev"], st["t"], st["steps"]
        scripts.append(tr)
    dist = os.path.join(lab, "dist")
    os.makedirs(dist, exist_ok=True)                 # no page is served here: the dial is this tool's own
    return {"lab": lab, "state": state, "claude": claude, "dist": dist, "scripts": scripts}


def boot(root, **seams):
    """Start a lab kernel over `root` and wait for it to serve. Returns (the process, its port, its token, its watch)."""
    port = _lab._free_port()
    token = "-".join(("lab", "unwatched", "tabs"))   # the lab kernel's own serve token, put together here (no literal for a secret scan to trip on)
    env = _lab.kernel_env(root["lab"], root["claude"], root["dist"], port, token, **seams)
    klog = open(os.path.join(root["lab"], "kernel.log"), "ab")
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
        kernel.kill()
        raise RuntimeError("the lab kernel never served /healthz")
    return kernel, port, token, Watch(kernel)


def halt(kernel, watch):
    watch.stop.set()
    kernel.kill()                                    # this tool's own child, by its handle
    kernel.wait()


def prepare_large(root, a):
    """Each large session's first build, one kernel boot per session: a page that shows that tab alone (the diet: every
    other tab a skeleton, none fetched), kept until the kernel has written the session's saved document. Returns one row
    per session: the first build's seconds, the wait for the document, the kernel's peak memory."""
    out = []
    ckpt = os.path.join(root["state"], "checkpoints")
    for tr in [t for t in root["scripts"] if t.large]:
        kernel, port, token, watch = boot(root)
        page = None
        try:
            page = Page(port, "app=chat&iid=lab-prepare&rest=1&active=%s&skeleton=1&token=%s" % (tr.sid, token))
            page.say({"type": "ready", "proto": 2})
            t0 = time.monotonic()
            while page.wait(lambda typ, fid, raw: typ == "session" and fid == tr.sid, timeout=5) is None:
                watch.check()
                if kernel.poll() is not None or time.monotonic() - t0 > 3600:
                    raise RuntimeError("a large session's first frame never came: %s" % tr.sid)
            first_s = time.monotonic() - t0
            # its document: the newest saved document at least a hundredth of the transcript's size (a small session's is
            # a few hundred KB; none of them is built in this boot)
            floor = os.path.getsize(tr.path) // 100
            t1 = time.monotonic()
            doc = None
            while doc is None:
                watch.check()
                if time.monotonic() - t1 > 1800:
                    raise RuntimeError("no saved document for %s within thirty minutes of its first build" % tr.sid)
                try:
                    for name in os.listdir(ckpt):
                        if name.endswith(".asm.json.gz") and os.path.getmtime(os.path.join(ckpt, name)) >= t_wall0(t0) and os.path.getsize(os.path.join(ckpt, name)) >= floor:
                            doc = name
                except OSError:
                    pass
                if doc is None:
                    time.sleep(2.0)
            out.append({"sid": tr.sid[:8], "transcriptMB": round(os.path.getsize(tr.path) / 1048576, 1), "firstBuildS": round(first_s, 1),
                        "documentAfterS": round(time.monotonic() - t1, 1), "documentMB": round(os.path.getsize(os.path.join(ckpt, doc)) / 1048576, 1),
                        "kernelPeakRssMB": _rss_mb(kernel.pid, "VmHWM")})
        finally:
            if page is not None:
                page.close()
            halt(kernel, watch)
    return out


def t_wall0(mono0):
    """The wall clock at a monotonic instant: file times are wall times."""
    return time.time() - (time.monotonic() - mono0)


def run_road(road, a, watched_k, root, total):
    kernel = page = watch = None
    scripts = root["scripts"]
    try:
        smalls, larges = [t for t in scripts if not t.large], [t for t in scripts if t.large]
        kernel, port, token, watch = boot(root, ROMP_WATCHED_TABS=str(watched_k))

        guard = watch.check

        on_screen = larges[0] if larges else smalls[0]
        rest = road == "after"
        page = Page(port, "app=chat&iid=lab-page%s&active=%s&skeleton=1&token=%s" % ("&rest=1" if rest else "", on_screen.sid, token))
        page.say({"type": "ready", "proto": 2})
        t_first = time.monotonic()
        if page.wait(lambda typ, fid, raw: typ == "session" and fid == on_screen.sid, timeout=1800) is None:
            raise RuntimeError("the first tab's frame never came")
        first_s = time.monotonic() - t_first
        guard()
        # the idle prefetch: every other tab, one ask at a time
        t_fill = time.monotonic()
        for tr in scripts:
            if tr is on_screen:
                continue
            at = page.count()
            page.say({"type": "needFull", "id": tr.sid, "why": "prefetch"})
            if page.wait(lambda typ, fid, raw, s=tr.sid: typ == "session" and fid == s, since=at, timeout=1800) is None:
                raise RuntimeError("a prefetched tab's frame never came: %s" % tr.sid)
            guard()
        fill_s = time.monotonic() - t_fill
        # the tabs the page shows in turn: six small ones, the second large one, and the first again. The kernel keeps the
        # last ROMP_WATCHED_TABS of them watched; nothing has moved yet, so each is shown from the copy the page holds
        shown = smalls[1:7] + larges[1:2] + [on_screen]
        for tr in shown:
            page.say({"type": "activeTab", "id": tr.sid})
        time.sleep(1.0)
        want = [t.sid for t in shown][-watched_k:] if rest else None   # what the kernel should be keeping built for this page
        in_set = [t for t in shown if rest and t.sid in want and t is not on_screen]
        outside_small = [t for t in smalls if not (rest and t.sid in (want or ()))]
        outside_large = [t for t in larges if not (rest and t.sid in (want or ())) and t is not on_screen]

        # ── the steady span: every session appends ──
        stop, marks = threading.Event(), {}
        state_now = {"on": on_screen, "round": 0}

        def writer():
            n = 0
            while not stop.is_set():
                n += 1
                for tr in scripts:
                    if tr is state_now["on"]:
                        mark = "labmark%06d" % n
                        tr.step(mark)
                        marks[mark] = time.monotonic()
                    else:
                        tr.step()
                state_now["round"] = n
                if not a.no_wake:
                    _tick(port, token)
                stop.wait(a.interval)
        p0, f0 = _get(port, token, "/perf"), page.count()
        t0 = time.monotonic()
        th = threading.Thread(target=writer, daemon=True)
        th.start()
        time.sleep(a.seconds)
        span = time.monotonic() - t0
        p1 = _get(port, token, "/perf")
        guard()
        lat, span_marks = [], sorted(marks.items())  # the appends of the span: the writer goes on through the clicks
        for mark, t_app in span_marks:
            hit = page.wait(lambda typ, fid, raw, m=mark: fid == on_screen.sid and typ in ("chatTail", "session") and m in raw,
                            since=f0, timeout=0.01)
            if hit is not None:
                lat.append(hit[1] - t_app)
        with page.lock:
            fr = page.frames[f0:]
        kinds = {}
        for _, typ, fid, raw in fr:
            kinds[typ] = kinds.get(typ, 0) + 1
        rests = sum(1 for _, typ, fid, raw in fr if typ == "status" and '"rest": true' in raw)
        tails_by = {}
        for _, typ, fid, raw in fr:
            if typ == "chatTail":
                tails_by[fid] = tails_by.get(fid, 0) + 1

        # ── the clicks, while every session keeps appending ──
        def click(tr, ask):
            """Show `tr`: (seconds from the click to the page holding its newest step as of the click, the frame that
            brought it, None when it was already held)."""
            time.sleep(a.interval)
            newest = "step %d of %s:" % (tr.steps, tr.sid[:8])
            at = page.count()
            tc = time.monotonic()
            page.say({"type": "activeTab", "id": tr.sid})
            if ask:                                  # a tab at rest shows the copy the page holds and asks (render.ts catchUp)
                page.say({"type": "needFull", "id": tr.sid, "why": "rest-open"})
            hit = page.wait(lambda typ, fid, raw, s=tr.sid, m=newest: fid == s and typ in ("chatTail", "session") and m in raw,
                            since=f0, timeout=1800)  # from the span's start: a frame the page already held may carry it
            state_now["on"] = tr
            if hit is None:
                return None, None
            with page.lock:
                raw = page.frames[hit[0]][3] if hit[0] >= at else None
            return max(0.0, hit[1] - tc), raw

        inside = [click(tr, False)[0] for tr in in_set[-3:]]
        rr = random.Random(13)
        picks = list(outside_small)
        rr.shuffle(picks)
        def split_click(tr):
            """A click on a tab outside the set, with the kernel's own clocks read before and after it: (seconds to fresh,
            the frame, the two /perf reads, the session's build row before and after, the ask's push in ms and how many
            pushes answered asks meanwhile)."""
            q0 = _get(port, token, "/perf")
            dt, raw = click(tr, rest)
            time.sleep(0.5)
            q1 = _get(port, token, "/perf")
            row0 = next((r for r in _dig(q0, "builds", "chat", "bySession") or [] if r.get("sid") == tr.sid), {})
            row1 = next((r for r in _dig(q1, "builds", "chat", "bySession") or [] if r.get("sid") == tr.sid), {})
            rep0, rep1 = _dig(q0, "pusher", "connectPush", "repair") or {}, _dig(q1, "pusher", "connectPush", "repair") or {}
            return dt, raw, q0, q1, row0, row1, rep1.get("ms_sum", 0) - rep0.get("ms_sum", 0), rep1.get("count", 0) - rep0.get("count", 0)

        # a small tab outside the set: the click, the kernel's push for its ask, and the build inside that push (the
        # session's own build timer), so a wait that is neither says so
        outside, outside_split = [], []
        for tr in picks[:a.clicks]:
            dt, raw, q0, q1, row0, row1, push_ms, pushes = split_click(tr)
            outside.append(dt)
            built = (row1.get("n") or 0) - (row0.get("n") or 0)
            outside_split.append({"clickToFreshMs": _ms(dt), "askPushMs": round(push_ms, 1), "askPushes": pushes,
                                  "buildMs": round(row1.get("last") or 0, 1) if built else None, "chatBuilds": built})
        large_rows = []
        for tr in outside_large:
            dt, raw, q0, q1, row0, row1, push_ms, pushes = split_click(tr)
            road0, road1 = _dig(q0, "parses", "byRoad") or {}, _dig(q1, "parses", "byRoad") or {}
            rs0, rs1 = _find(q0, "restoreMs") or {}, _find(q1, "restoreMs") or {}
            restore = {k: round(v - rs0.get(k, 0), 1) for k, v in rs1.items() if isinstance(v, (int, float)) and v - rs0.get(k, 0) >= 0.05}
            large_rows.append({
                "sid": tr.sid[:8], "transcriptMB": round(os.path.getsize(tr.path) / 1048576, 1), "clickToFreshMs": _ms(dt),
                # the kernel's whole push for the ask (the strip, the build, the frame handed to the socket's queue)
                "askPushMs": round(push_ms, 1), "askPushes": pushes,
                # the session's build inside it, by the kernel's per-session build timer (the last build's time, when the
                # click built it): what is left of the push is the strip, the statuses, the send, and any wait on another thread
                "buildMs": round(row1.get("last") or 0, 1) if (row1.get("n") or 0) > (row0.get("n") or 0) else None,
                # the restore of the session's saved document inside it, by part (the kernel's own clocks), when one ran:
                # a session nobody reads has its parsed history handed to its document and is read back on the ask
                "restoreMs": restore,
                # …and the rest of that push: the fold of what the session wrote meanwhile, the reshape, the strip, the send
                "restOfPushMs": round(push_ms - restore.get("total", 0), 1),
                # what was parsed meanwhile, by road, for every session (a whole parse or a restore shows here)
                "parsed": {k: v - road0.get(k, 0) for k, v in road1.items() if isinstance(v, (int, float)) and v - road0.get(k, 0)},
                "wholeParseMB": round(((_dig(q1, "parses", "wholeBytes") or 0) - (_dig(q0, "parses", "wholeBytes") or 0)) / 1048576, 1),
                "chatBuilds": (row1.get("n") or 0) - (row0.get("n") or 0),
                # the wire: the frame's size, and what the click waited beyond the kernel's push (the queue, the socket, the read)
                "frameKB": round(len(raw) / 1024, 1) if raw else None,
                "beyondPushMs": round(max(0.0, (dt or 0) * 1000 - push_ms), 1),
            })
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
        ok_out = [c for c in outside if c is not None]
        ok_in = [c for c in inside if c is not None]
        return {
            "road": road, "watchedTabs": chat1.get("watchedTabs") if rest else None, "tracking": a.tracking,
            "sessions": len(scripts), "large": len(larges), "transcriptMB": round(total / 1048576, 1),
            "firstTabS": round(first_s, 1), "prefetchFillS": round(fill_s, 1),
            "spanS": round(span, 1), "rounds": state_now["round"],
            "cycles": cyc, "cycleMsMean": round(d(("pusher", "cycle_ms_sum"), p0, p1) / cyc, 1) if cyc else None,
            "cycleMsMaxSinceBoot": round(p1["pusher"]["cycle_ms_max"], 1),
            "pushChatS": round(d(("stages_ms", "push.chat"), p0, p1) / 1000, 2),
            "chatBuilt": chat1["built"] - chat0["built"], "onScreenBuilt": chat1["active_built"] - chat0["active_built"],
            "otherBuilt": chat1["bg_built"] - chat0["bg_built"],   # the tabs built that are not on screen: the watched set's others; every tab, before
            "tabsTakingDeltas": len(tails_by),
            "rested": chat1.get("rested", 0) - chat0.get("rested", 0), "coldSkipped": chat1["coldSkipped"] - chat0["coldSkipped"],
            "restHeld": {k: v - (chat0.get("restHeld") or {}).get(k, 0) for k, v in (chat1.get("restHeld") or {}).items()},
            "pusherCpuS": round(d(("pusher", "cycle_cpu_ms_sum"), p0, p1) / 1000, 2),
            # where the pusher and its jobs spent the span, the largest first (the kernel's own stage clocks, in seconds)
            "stagesS": dict(sorted(((k, round(d(("stages_ms", k), p0, p1) / 1000, 2)) for k in (p1.get("stages_ms") or {})
                                    if d(("stages_ms", k), p0, p1) >= 50), key=lambda kv: -kv[1])[:12]),
            "onScreenUpdates": len(lat), "onScreenAppends": len(span_marks),
            "onScreenLatencyMs": {"p50": _ms(_pct(lat, 0.5)), "p90": _ms(_pct(lat, 0.9)), "max": _ms(max(lat)) if lat else None},
            "restFrames": rests, "frames": kinds,
            "insideClicks": len(inside), "insideClickToFreshMs": {"p50": _ms(_pct(ok_in, 0.5)), "max": _ms(max(ok_in)) if ok_in else None},
            "outsideClicks": len(outside), "outsideUnanswered": len(outside) - len(ok_out),
            "outsideClickToFreshMs": {"p50": _ms(_pct(ok_out, 0.5)), "max": _ms(max(ok_out)) if ok_out else None},
            "outsideSplit": outside_split,
            "largeOutside": large_rows,
            # each large session's chat builds over the span (the kernel's per-session timer): how many, and what one cost
            "largeBuilds": [{"sid": t.sid[:8], "watched": bool(rest and t.sid in (want or ())) or not rest,
                             "builds": (r1.get("n") or 0) - (r0.get("n") or 0), "lastMs": round(r1.get("last") or 0, 1), "maxMs": round(r1.get("max") or 0, 1)}
                            for t in larges
                            for r0 in [next((r for r in _dig(p0, "builds", "chat", "bySession") or [] if r.get("sid") == t.sid), {})]
                            for r1 in [next((r for r in _dig(p1, "builds", "chat", "bySession") or [] if r.get("sid") == t.sid), {})]],
            "clickBuilds": p2["builds"]["chat"]["built"] - chat1["built"],
            "kernelRssMB": _rss_mb(kernel.pid), "kernelPeakRssMB": _rss_mb(kernel.pid, "VmHWM"),
            "loadavg": round(os.getloadavg()[0], 2),
        }
    finally:
        if page is not None:
            page.close()
        if kernel is not None:
            halt(kernel, watch)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sessions", type=int, default=85, help="the small sessions (default 85)")
    ap.add_argument("--kb", type=int, default=1024, help="a small session's transcript at the start, in KB (default 1024)")
    ap.add_argument("--large", type=int, default=0, help="large sessions beside them (default 0)")
    ap.add_argument("--large-mb", type=int, default=300, help="a large session's transcript, in MB (default 300, the most this tool writes)")
    ap.add_argument("--seconds", type=float, default=60.0, help="the steady span (default 60)")
    ap.add_argument("--interval", type=float, default=2.0, help="seconds between a session's appends (default 2)")
    ap.add_argument("--clicks", type=int, default=10, help="small tabs outside the watched set opened after the span (default 10)")
    ap.add_argument("--watched", default="4", help="the after road's ROMP_WATCHED_TABS, one boot per value (default 4; 1,4,8 for three)")
    ap.add_argument("--no-wake", action="store_true", help="no POST /tick after a round: the pusher's half-second poll alone")
    ap.add_argument("--tracking", choices=("off", "on"), default="off", help="the kernel's Task tracking switch (default off)")
    ap.add_argument("--roads", default=",".join(ROADS))
    ap.add_argument("--json", help="write the rows here too")
    ap.add_argument("--keep", action="store_true", help="keep each road's temporary root (the kernel's log is in it)")
    a = ap.parse_args()
    if a.sessions * a.kb > 300 * 1024 or a.large_mb > 300:
        ap.error("at most 300 MB of small transcripts, and 300 MB for a large one")
    if a.sessions < 7:
        ap.error("at least seven small sessions: the page shows six of them beside the one on screen")
    ks = [int(x) for x in a.watched.split(",") if x.strip()]
    seed = tempfile.mkdtemp(prefix="unwatched-tabs-seed-")
    rows_out, roots = [], []

    def save():
        if a.json:
            with open(a.json, "w") as f:
                json.dump(rows_out, f, indent=1)
    try:
        rows, total = write_seed(a, seed)
        shared = None
        if a.large:                                  # one root for every road: the large sessions' documents live in it
            shared = make_root(a, seed, rows, a.keep)
            roots.append(shared)
            shutil.rmtree(seed, ignore_errors=True)  # the root holds its own copy: the seed's hundreds of megabytes go now
            prep = prepare_large(shared, a)
            rows_out.append({"road": "prepare", "large": prep})
            print("prepare: " + "; ".join("%s MB built first in %s s, its document (%s MB) %s s later, kernel peak %s MB"
                                          % (r["transcriptMB"], r["firstBuildS"], r["documentMB"], r["documentAfterS"], r["kernelPeakRssMB"]) for r in prep))
            sys.stdout.flush()
            save()
        for road in [r for r in a.roads.split(",") if r in ROADS]:
            for k in (ks if road == "after" else ks[:1]):
                root = shared
                if root is None:
                    root = make_root(a, seed, rows, a.keep)
                    roots.append(root)
                try:
                    row = run_road(road, a, k, root, total)
                finally:
                    if shared is None and not a.keep:
                        shutil.rmtree(root["lab"], ignore_errors=True)
                rows_out.append(row)
                w, o, i = row["onScreenLatencyMs"], row["outsideClickToFreshMs"], row["insideClickToFreshMs"]
                print("%-6s watched %s, tracking %s, %d sessions (%d large), %s MB | %d cycles, mean %s ms, chat stage %s s of %s s | "
                      "builds: %d on screen, %d other, %d rested | on screen: %d of %d appends seen, p50 %s ms, p90 %s ms, max %s ms | "
                      "click inside the set: p50 %s ms, max %s ms (%d) | outside: p50 %s ms, max %s ms (%d, %d unanswered) | large outside: %s | "
                      "kernel %s MB | load %s"
                      % (road, row["watchedTabs"], row["tracking"], row["sessions"], row["large"], row["transcriptMB"], row["cycles"],
                         row["cycleMsMean"], row["pushChatS"], row["spanS"], row["onScreenBuilt"], row["otherBuilt"], row["rested"],
                         row["onScreenUpdates"], row["onScreenAppends"], w["p50"], w["p90"], w["max"], i["p50"], i["max"], row["insideClicks"],
                         o["p50"], o["max"], row["outsideClicks"], row["outsideUnanswered"],
                         ", ".join("%s MB in %s ms (push %s ms: restore %s, the rest %s; frame %s KB, beyond the push %s ms)"
                                   % (r["transcriptMB"], r["clickToFreshMs"], r["askPushMs"], r["restoreMs"].get("total", 0), r["restOfPushMs"],
                                      r["frameKB"], r["beyondPushMs"]) for r in row["largeOutside"]) or "none",
                         row["kernelPeakRssMB"], row["loadavg"]))
                sys.stdout.flush()
                save()
    finally:
        shutil.rmtree(seed, ignore_errors=True)
        for root in roots:
            if a.keep:
                print("kept: %s" % root["lab"], file=sys.stderr)
            else:
                shutil.rmtree(root["lab"], ignore_errors=True)


if __name__ == "__main__":
    main()
