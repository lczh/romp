#!/usr/bin/env python3
"""A comment thread's way back to its passage on the real /chat page (the user 2026-10-15, who clicked a thread's tick on
the scroll rail and got the thread's box with no way to reach the passage it was about). The tick used to land the anchor
TURN'S top and open the thread's box at its fixed spot over the middle of the pane, so in a long message the passage sat
below the fold or under the box. Every entry point that opens a thread now lands the PASSAGE first (aligned at the top of
the pane, its highlight flashed) and then opens the thread beside it, never over it.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges, each reply fourteen
paragraphs long, so the page holds exchanges 25..149 and renders only the last 80 units of those (the rest in a spacer).
Four threads, each on the seventh paragraph of its reply: reply 30 (held by the page, outside the rendered window), 140
(unread: its turn's rail line is tinted and opens it), 149 (the last reply, on screen at load: the landing cannot scroll it
higher, the box moves instead) and 100, whose quoted words are not in its reply. Asserted in the browser:
  the tick on a passage already on screen flashes the passage, then opens the thread, and the box does not cover it;
  the tick on a passage the page has not rendered renders it, puts it at the top of the pane, flashes it, then opens the
  thread beside it;
  with the reader scrolled away, the quoted passage at the top of the open thread takes them back to it, the same way;
  the tinted rail line of an unread thread's turn lands its passage and opens the thread beside it;
  a thread whose words are not in its message lands the message and says so in the box;
  the jump cluster's comment row lands the same passage the same way, and opens nothing.
Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose,
host TESTHOST)."""
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

WEB = "aaaaaaaa-1111-2222-3333-aaaaaaaaaaaa"
N = 150
PARAS, AT = 14, 6
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
THREAD_SID = "eeeeeeee-1111-2222-3333-%012d"
# (tid, reply index, read or unread, whether its quoted words are in the reply)
THREADS = [("tid-far", 30, "read", True), ("tid-rail", 140, "unread", True), ("tid-last", 149, "read", True), ("tid-drift", 100, "read", False)]
WORDS_LOST = "Couldn't find this passage's words in its message."


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


def reply_body(k):
    return "\n\n".join(exact(k) if j == AT else
                       "Paragraph %d of step %d walks the export: the reader pulls a page of notes, the encoder turns each into a row, "
                       "and the writer appends it to the file the client downloads, so nothing waits on the whole set." % (j, k)
                       for j in range(PARAS))


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
await page.addInitScript(({ sid, n }) => {
  window.__cmtFrames = 0;
  window.addEventListener("message", (e) => { const m = e.data; if (m && m.type === "comments" && m.id === sid && (m.threads || []).length >= n) window.__cmtFrames++; }, true);
}, { sid: cfg.sid, n: cfg.threads });
await page.goto(cfg.chat);
await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
await page.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.threads, { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(800);
out.ticks = await page.evaluate(() => Array.from(document.querySelectorAll("#cmt-rail .cmt-tick")).map((t) => t.dataset.tid));
// the order of what happens on screen: a flash as it starts (on a thread's highlight, or on a turn) and the thread's box as it
// opens; the flash class lasts 1.6s, so it is caught as it is added
await page.evaluate(() => {
  window.__seq = [];
  const note = (s) => { if (window.__seq[window.__seq.length - 1] !== s) window.__seq.push(s); };
  new MutationObserver((recs) => { for (const r of recs) {
    if (r.type === "attributes") {
      const el = r.target;
      if (!el.classList || !el.classList.contains("anchor-flash") || !el.closest("#content")) continue;
      note(el.tagName === "MARK" ? "flash:mark:" + el.dataset.tid : "flash:turn:" + ((el.closest(".turn") || {}).dataset || {}).uuid);
    } else for (const n of Array.from(r.addedNodes)) if (n.id === "cmt-pop") note("pop:" + n.dataset.tid);
  } }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
});
// a thread's passage on screen (every fragment of its highlight as one box), the open box, and what the box says
const measure = (tid) => page.evaluate((tid) => {
  const c = document.getElementById("content").getBoundingClientRect();
  let u = null;
  for (const m of Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`))) for (const q of Array.from(m.getClientRects()))
    u = u ? { l: Math.min(u.l, q.left), t: Math.min(u.t, q.top), r: Math.max(u.r, q.right), b: Math.max(u.b, q.bottom) } : { l: q.left, t: q.top, r: q.right, b: q.bottom };
  const pop = document.getElementById("cmt-pop"), p = pop ? pop.getBoundingClientRect() : null;
  const lost = pop ? pop.querySelector(".cmt-lost") : null;
  return { content: { t: c.top, b: c.bottom }, mark: u, inView: !!u && u.b > c.top && u.t < c.bottom,
           pop: p ? { l: p.left, t: p.top, r: p.right, b: p.bottom } : null, popTid: pop ? pop.dataset.tid : null,
           lost: lost ? lost.textContent : null, vh: innerHeight, seq: window.__seq.slice() };
}, tid);
const turnTop = (uuid) => page.evaluate((u) => { const t = document.querySelector(`#content .turn[data-uuid="${u}"]`), c = document.getElementById("content").getBoundingClientRect();
  return t ? Math.round(t.getBoundingClientRect().top - c.top) : null; }, uuid);
const fresh = async () => {
  await page.evaluate(() => { document.getElementById("cmt-pop")?.querySelector(".cmt-x")?.click(); window.__seq = []; });
  await page.waitForTimeout(300);
};
const opened = async (tid) => {
  await page.waitForSelector(`#cmt-pop[data-tid="${tid}"]`, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);   // the landing's settle
  return measure(tid);
};

// 1. the last reply's passage, on screen at load (the view sits at the bottom, which the landing cannot scroll past)
out.lastBefore = await measure("tid-last");
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-last"]`);
out.last = await opened("tid-last");

// 2. a passage the page holds but has not rendered (the rendered window covers the tail)
await fresh();
out.farBefore = await measure("tid-far");
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-far"]`);
out.far = await opened("tid-far");

// 3. the reader scrolls away with the thread open (their own wheel, over the pane beside the box); its quoted passage takes
// them back
await page.mouse.move(150, 400);
for (let i = 0; i < 6; i++) { await page.mouse.wheel(0, 1500); await page.waitForTimeout(150); }   // loop-ok: six wheel turns, a reader scrolling down
await page.waitForTimeout(800);
out.quoteBefore = await measure("tid-far");
out.quoteLink = await page.evaluate(() => { const q = document.querySelector('#cmt-pop .cmt-quote[data-passage]'); return q ? { title: q.title, cursor: getComputedStyle(q).cursor } : null; });
await page.evaluate(() => { window.__seq = []; });
if (out.quoteLink) {
  await page.click('#cmt-pop .cmt-quote[data-passage]');
  await page.waitForFunction(() => window.__seq.includes("flash:mark:tid-far"), null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);
}
out.quote = await measure("tid-far");

// 4. an unread thread's tinted rail line: back at the bottom (the go-to-bottom chip), the turn brought on screen, a click on
// its line below the turn's dot
await fresh();
await page.click("#jump-bottom").catch(() => {});
await page.waitForSelector("#jump-bottom", { state: "hidden", timeout: 10000 }).catch(() => {});
await page.waitForTimeout(1500);   // past the landing's settle window, so the move below is not re-landed
await page.evaluate((u) => { const t = document.querySelector(`#content .turn[data-uuid="${u}"]`); if (t) t.scrollIntoView({ block: "start" }); }, R(140));
await page.waitForTimeout(800);
out.railBefore = await page.evaluate((u) => { const t = document.querySelector(`#content .turn[data-uuid="${u}"]`);
  return { tinted: !!t && t.classList.contains("cmt-rail-unread"), unreadMark: !!t && !!t.querySelector("mark.cmt-hl.unread"), hit: !!t && !!t.querySelector(":scope > .rail-hit") }; }, R(140));
await page.evaluate(() => { window.__seq = []; });
await page.click(`#content .turn[data-uuid="${R(140)}"] > .rail-hit`, { position: { x: 4, y: 80 } }).catch((e) => { out.railErr = String(e); });
out.rail = await opened("tid-rail");

// 5. a thread whose quoted words are not in its message: the message, and the box says so
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-drift"]`);
out.drift = await opened("tid-drift");
out.driftTurnTop = await turnTop(R(100));

// 6. the jump cluster's comment row takes the same landing (and opens nothing): from the bottom, the last reply's thread, then
// the one on reply 140, its passage at the top
await fresh();
await page.click("#jump-bottom").catch(() => {});
await page.waitForSelector("#jump-bottom", { state: "hidden", timeout: 10000 }).catch(() => {});
await page.waitForTimeout(1500);
for (const want of ["tid-last", "tid-rail"]) {   // loop-ok: two presses, each waited on its own flash
  await page.evaluate(() => { window.__seq = []; });
  await page.click('#jump-cluster [data-move="prevComment"]').catch((e) => { out.clusterErr = String(e); });
  await page.waitForFunction((t) => window.__seq.includes("flash:mark:" + t), want, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(600);
}
out.cluster = await measure("tid-rail");
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentTickPassage(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-tick-passage-")
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
        t0 = int(time.time()) - 3 * 86400
        recs, prev = [], None
        for k in range(N):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 600 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 600 * k + 30), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k)}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        rows = []
        for i, (tid, k, st, present) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 1)
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
            words = exact(k) if present else "The cache at step %d keeps every row in memory until the export ends." % k
            row = {"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                   "exact": words, "status": "open", "createdT": t}
            if st != "unread":
                row["lastSeenT"] = int(time.time()) + 86400
            rows.append(row)
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-tickspassage"
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

    def _beside(self, m, tid, where):
        """The landing flashed the passage, then opened the thread on it; the box covers none of the passage and sits in
        the window."""
        self.assertEqual(m["popTid"], tid, "%s: the thread's box is open: %r" % (where, m))
        self.assertTrue(m["inView"], "%s: the passage is on screen: %r" % (where, m))
        seq = m["seq"]
        self.assertIn("flash:mark:" + tid, seq, "%s: the passage itself flashed, not only its turn: %r" % (where, seq))
        if ("pop:" + tid) in seq:
            self.assertLess(seq.index("flash:mark:" + tid), seq.index("pop:" + tid), "%s: the landing first, then the box: %r" % (where, seq))
        a, p = m["mark"], m["pop"]
        covers = p["l"] < a["r"] and p["r"] > a["l"] and p["t"] < a["b"] and p["b"] > a["t"]
        self.assertFalse(covers, "%s: the box does not cover the passage: passage %r, box %r" % (where, a, p))
        self.assertGreaterEqual(p["t"], 0, "%s: the box is in the window: %r" % (where, p))
        self.assertLessEqual(p["b"], m["vh"], "%s: the box is in the window: %r" % (where, p))
        self.assertIsNone(m["lost"], "%s: a passage found says nothing more: %r" % (where, m))

    def _at_top(self, m, where):
        self.assertLess(abs(m["mark"]["t"] - m["content"]["t"]), 3, "%s: the passage is at the top of the pane, not its message's top: %r" % (where, m))

    def test_each_entry_point_lands_the_passage_then_opens_the_thread_beside_it(self):
        r = self._drive()
        self.assertEqual(sorted(r["ticks"]), sorted(t[0] for t in THREADS), "a tick for each thread: %r" % r["ticks"])
        # 1. on screen at load: the landing cannot scroll the last reply higher, so the box moves off the passage instead
        self.assertTrue(r["lastBefore"]["inView"], "the last reply's passage is on screen at load: %r" % r["lastBefore"])
        self._beside(r["last"], "tid-last", "a passage on screen")
        # 2. a passage the page holds but has not rendered: rendered, at the top of the pane, flashed, then the thread beside it
        self.assertIsNone(r["farBefore"]["mark"], "the passage was outside the rendered window: %r" % r["farBefore"])
        self._beside(r["far"], "tid-far", "a passage outside the rendered window")
        self._at_top(r["far"], "a passage outside the rendered window")
        # 3. the quoted passage at the top of the open thread takes the reader back to it
        self.assertIsNotNone(r["quoteLink"], "the open thread's quote is a link back to its passage")
        self.assertEqual(r["quoteLink"]["cursor"], "pointer", r["quoteLink"])
        self.assertFalse(r["quoteBefore"]["inView"], "the reader had scrolled away from the passage: %r" % r["quoteBefore"])
        self._beside(r["quote"], "tid-far", "the open thread's quote")
        self._at_top(r["quote"], "the open thread's quote")
        # 4. an unread thread's tinted rail line lands its passage and opens the thread beside it
        self.assertEqual(r["railBefore"], {"tinted": True, "unreadMark": True, "hit": True}, "the unread thread tints its turn's line: %r" % r["railBefore"])
        self.assertNotIn("railErr", r, r.get("railErr"))
        self._beside(r["rail"], "tid-rail", "the tinted rail line")
        self._at_top(r["rail"], "the tinted rail line")
        # 5. words not in the message: the message lands, and the box says the passage's words were not found
        d = r["drift"]
        self.assertEqual(d["popTid"], "tid-drift", d)
        self.assertEqual(d["lost"], WORDS_LOST, "the box says so rather than opening as if all were well: %r" % d)
        self.assertIsNotNone(r["driftTurnTop"], "the message is rendered: %r" % r)
        self.assertLess(abs(r["driftTurnTop"]), 3, "the message is at the top of the pane: %r" % r["driftTurnTop"])
        # 6. the jump cluster's comment row lands the same passage, at the top of the pane, and opens nothing
        c = r["cluster"]
        self.assertNotIn("clusterErr", r, r.get("clusterErr"))
        self.assertIn("flash:mark:tid-rail", c["seq"], "the cluster flashes the passage: %r" % c["seq"])
        self._at_top(c, "the jump cluster")
        self.assertIsNone(c["popTid"], "the cluster lands a thread without opening it: %r" % c)


if __name__ == "__main__":
    unittest.main()
