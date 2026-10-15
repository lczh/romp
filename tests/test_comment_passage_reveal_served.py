#!/usr/bin/env python3
"""Three follow-ups to the landing on a comment thread's passage (the review of 2026-10-15). The tick on the scroll rail lands
the thread's passage, flashes it and opens the thread beside it; three shapes of passage went wrong on the real /chat page:
  a passage on the LAST reply's final line, clicked with the reader scrolled far up: the box was placed above the passage,
  then a fork control joined the reply and the landing's settle scrolled the passage up behind the box;
  a passage folded in a closed <details> of its reply: the view went where the hidden passage would be and nothing visible
  flashed; and one that cannot be shown (a hidden block inside the fold) must land the fold's heading and say so in the box;
  a passage split over several elements (a bold phrase): only its first piece flashed.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges of fourteen-paragraph
replies, so the page renders only the tail of what it holds. Four threads: reply 149's final paragraph, a passage in a closed
fold of reply 120, one with a bold phrase in reply 110, and one in a hidden block inside a fold of reply 100. Skips LOUDLY
without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host
TESTHOST)."""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
N = 150
PARAS, AT = 14, 6
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
THREAD_SID = "eeeeeeee-1111-2222-3333-%012d"
# (tid, reply index, how the passage sits in its reply)
THREADS = [("tid-end", 149, "end"), ("tid-fold", 120, "fold"), ("tid-split", 110, "split"), ("tid-hidden", 100, "hidden")]
FOLDED = "Couldn't unfold this passage in its message."


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def exact(k):
    return "The bounded buffer at step %d holds two hundred rows before the writer flushes." % k


def para(j, k):
    return ("Paragraph %d of step %d walks the export: the reader pulls a page of notes, the encoder turns each into a row, "
            "and the writer appends it to the file the client downloads, so nothing waits on the whole set." % (j, k))


def reply_body(k, how):
    out = []
    for j in range(PARAS):
        if how == "end" and j == PARAS - 1:
            out.append(exact(k))
        elif how == "fold" and j == AT:
            out.append('<details class="fx-fold"><summary>Export notes</summary><p>%s</p></details>' % exact(k))
        elif how == "hidden" and j == AT:
            out.append('<details class="fx-hidden"><summary>Archived notes</summary><div hidden><p>%s</p></div></details>' % exact(k))
        elif how == "split" and j == AT:
            out.append("The bounded buffer at step %d holds **two hundred** rows before the writer flushes." % k)
        else:
            out.append(para(j, k))
    return "\n\n".join(out)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const R = (k) => cfg.replyUuid.replace("%012d", String(k).padStart(12, "0"));
const out = {};
const ctx = await browser.newContext({ viewport: { width: 1000, height: 700 } });
const page = await ctx.newPage();
await page.goto(cfg.chat);
await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
await page.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.threads, { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(800);
out.ticks = await page.evaluate(() => Array.from(document.querySelectorAll("#cmt-rail .cmt-tick")).map((t) => t.dataset.tid));
// what happens on screen, in order: a flash as it starts (on a piece of a thread's highlight, by its index among the thread's
// pieces, or on another element, by its tag) and the thread's box as it opens; the flash class lasts 1.6s, so it is caught
// as it is added
await page.evaluate(() => {
  window.__seq = []; window.__pieces = {};
  const note = (s) => { if (window.__seq[window.__seq.length - 1] !== s) window.__seq.push(s); };
  new MutationObserver((recs) => { for (const r of recs) {
    if (r.type === "attributes") {
      const el = r.target;
      if (!el.classList || !el.classList.contains("anchor-flash") || !el.closest("#content")) continue;
      if (el.tagName === "MARK") {
        const tid = el.dataset.tid;
        const i = Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`)).indexOf(el);
        (window.__pieces[tid] = window.__pieces[tid] || []).includes(i) || window.__pieces[tid].push(i);
        note("flash:mark:" + tid);
      } else note("flash:" + el.tagName.toLowerCase());
    } else for (const n of Array.from(r.addedNodes)) if (n.id === "cmt-pop") note("pop:" + n.dataset.tid);
  } }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
});
// a thread's passage on screen (every piece of its highlight as one box), the heading of the fold it is in, the open box, and
// what the box says
const measure = (tid, fold) => page.evaluate(({ tid, fold }) => {
  const c = document.getElementById("content").getBoundingClientRect();
  let u = null;
  const marks = Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`));
  for (const m of marks) for (const q of Array.from(m.getClientRects()))
    u = u ? { l: Math.min(u.l, q.left), t: Math.min(u.t, q.top), r: Math.max(u.r, q.right), b: Math.max(u.b, q.bottom) } : { l: q.left, t: q.top, r: q.right, b: q.bottom };
  const d = fold ? document.querySelector(`#content details.${fold}`) : null;
  const s = d ? d.querySelector("summary").getBoundingClientRect() : null;
  const pop = document.getElementById("cmt-pop"), p = pop ? pop.getBoundingClientRect() : null;
  const lost = pop ? pop.querySelector(".cmt-lost") : null;
  // shown: drawn, not only laid out (Chromium lays out a closed <details>' content, so a folded passage has a box)
  return { content: { t: c.top, b: c.bottom }, mark: u, inView: !!u && u.b > c.top && u.t < c.bottom, pieces: marks.length,
           shown: marks.length > 0 && marks.every((m) => m.checkVisibility()),
           flashed: (window.__pieces[tid] || []).slice().sort(), fold: d ? { open: d.open, head: { l: s.left, t: s.top, r: s.right, b: s.bottom } } : null,
           pop: p ? { l: p.left, t: p.top, r: p.right, b: p.bottom } : null, popTid: pop ? pop.dataset.tid : null,
           lost: lost ? lost.textContent : null, vh: innerHeight, seq: window.__seq.slice() };
}, { tid, fold });
const fresh = async () => {
  await page.evaluate(() => { document.getElementById("cmt-pop")?.querySelector(".cmt-x")?.click(); window.__seq = []; window.__pieces = {}; });
  await page.waitForTimeout(300);
};
const opened = async (tid, fold) => {
  await page.waitForSelector(`#cmt-pop[data-tid="${tid}"]`, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1600);   // past the landing's settle, and the late fork control under the last reply
  return measure(tid, fold);
};

// 1. the last reply's final line, with the reader scrolled far up (the tail outside the rendered window): the landing renders
// the tail and cannot scroll the passage higher, so the box goes above it; a fork control then joins the reply, and the
// settle scrolls the passage up into where the box was
await page.mouse.move(150, 400);
for (let i = 0; i < 40; i++) { await page.mouse.wheel(0, -3000); await page.waitForTimeout(100); }   // loop-ok: forty wheel turns, a reader scrolling far up
await page.waitForTimeout(800);
out.endBefore = await page.evaluate((u) => ({ tailRendered: !!document.querySelector(`#content .turn[data-uuid="${u}"]`) }), R(149));
await page.evaluate(() => { window.__seq = []; window.__pieces = {}; });
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-end"]`);
out.end = await opened("tid-end");

// 2. a passage folded in a closed <details> of its reply: the fold opens, the passage lands at the top and flashes
await fresh();
out.foldBefore = await measure("tid-fold", "fx-fold");
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-fold"]`);
out.fold = await opened("tid-fold", "fx-fold");

// 3. a passage split over three pieces by a bold phrase: every piece flashes
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-split"]`);
out.split = await opened("tid-split");

// 4. a passage its reply keeps hidden inside a fold (a `hidden` block the landing does not open): the fold's heading lands,
// and the box says so
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-hidden"]`);
out.hidden = await opened("tid-hidden", "fx-hidden");
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentPassageReveal(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def _boot(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        cls.lab = tempfile.mkdtemp(prefix="comment-passage-reveal-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        fakebin = os.path.join(cls.lab, "fakebin")   # the machine's user manager is not the lab's
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        how = {k: h for _, k, h in THREADS}
        t0 = int(time.time()) - 3 * 86400
        recs, prev = [], None
        for k in range(N):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 600 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 600 * k + 30), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k, how.get(k))}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        rows = []
        for i, (tid, k, _how) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 11)
            anchor = REPLY_UUID % k
            t = t0 + 600 * N + 60 * i
            hist = [dict(r, sessionId=tsid) for r in recs[:2 * k + 2]]
            hist += [{"type": "user", "timestamp": iso(t), "uuid": "c%d" % i, "parentUuid": anchor, "promptSource": "typed",
                      "sessionId": tsid, "message": {"role": "user", "content": "why two hundred rows at step %d?" % k}},
                     {"type": "assistant", "timestamp": iso(t + 20), "uuid": "r%d" % i, "parentUuid": "c%d" % i, "sessionId": tsid,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "So a slow client never holds the whole export in memory."}]}}]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            rows.append({"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                         "exact": exact(k), "status": "open", "createdT": t, "lastSeenT": int(time.time()) + 86400})
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-passagereveal"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        env["PATH"] = fakebin + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise unittest.SkipTest("hermetic kernel never served /healthz here")

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.kill()
            k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "threads": len(THREADS), "replyUuid": REPLY_UUID}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=480,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def _clear(self, a, p):
        return not (p["l"] < a["r"] and p["r"] > a["l"] and p["t"] < a["b"] and p["b"] > a["t"])

    def _beside(self, m, tid, where):
        """The landing flashed the passage, then opened the thread on it; the box covers none of the passage, sits in the
        window, and says nothing more."""
        self.assertEqual(m["popTid"], tid, "%s: the thread's box is open: %r" % (where, m))
        self.assertTrue(m["inView"] and m["shown"], "%s: the passage is on screen, drawn: %r" % (where, m))
        seq = m["seq"]
        self.assertIn("flash:mark:" + tid, seq, "%s: the passage itself flashed: %r" % (where, seq))
        if ("pop:" + tid) in seq:
            self.assertLess(seq.index("flash:mark:" + tid), seq.index("pop:" + tid), "%s: the landing first, then the box: %r" % (where, seq))
        self.assertTrue(self._clear(m["mark"], m["pop"]), "%s: the box does not cover the passage: passage %r, box %r" % (where, m["mark"], m["pop"]))
        self.assertGreaterEqual(m["pop"]["t"], 0, "%s: the box is in the window: %r" % (where, m["pop"]))
        self.assertLessEqual(m["pop"]["b"], m["vh"], "%s: the box is in the window: %r" % (where, m["pop"]))
        self.assertIsNone(m["lost"], "%s: a passage found says nothing more: %r" % (where, m))

    def test_folded_split_and_end_of_chat_passages_land_whole_and_uncovered(self):
        r = self._drive()
        self.assertEqual(sorted(r["ticks"]), sorted(t[0] for t in THREADS), "a tick for each thread: %r" % r["ticks"])
        # 1. the last reply's final line, from far up: the box stays clear of the passage after the late fork control
        self.assertFalse(r["endBefore"]["tailRendered"], "the reader was far up, the tail outside the rendered window: %r" % r["endBefore"])
        self._beside(r["end"], "tid-end", "the end of the chat")
        # 2. a passage in a closed fold: the fold opens, the passage is at the top of the pane and flashes, the box beside it
        fb = r["foldBefore"]
        self.assertIsNotNone(fb["fold"], "the reply folds the passage in a <details>: %r" % fb)
        self.assertFalse(fb["fold"]["open"], "the fold starts closed: %r" % fb)
        f = r["fold"]
        self.assertTrue(f["fold"]["open"], "the landing opened the fold: %r" % f)
        self._beside(f, "tid-fold", "a passage in a closed fold")
        self.assertLess(abs(f["mark"]["t"] - f["content"]["t"]), 3, "the passage is at the top of the pane: %r" % f)
        # 3. a passage in three pieces: every piece flashes, not only the first
        sp = r["split"]
        self.assertGreaterEqual(sp["pieces"], 3, "the bold phrase splits the highlight: %r" % sp)
        self.assertEqual(sp["flashed"], list(range(sp["pieces"])), "every piece of the highlight flashed: %r" % sp)
        self._beside(sp, "tid-split", "a passage in pieces")
        # 4. a passage its reply keeps hidden: the fold's heading lands at the top and flashes, and the box says so, clear of it
        h = r["hidden"]
        self.assertEqual(h["popTid"], "tid-hidden", h)
        self.assertEqual(h["lost"], FOLDED, "the box says the passage could not be shown: %r" % h)
        self.assertFalse(h["shown"], "the passage itself stays hidden: %r" % h)
        head = h["fold"]["head"]
        self.assertLess(abs(head["t"] - h["content"]["t"]), 3, "the fold's heading is at the top of the pane: %r" % h)
        self.assertIn("flash:summary", h["seq"], "the heading flashed: %r" % h["seq"])
        self.assertTrue(self._clear(head, h["pop"]), "the box does not cover the heading: %r" % h)

if __name__ == "__main__":
    unittest.main()
