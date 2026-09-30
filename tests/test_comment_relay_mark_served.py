#!/usr/bin/env python3
"""A relayed comment thread keeps its highlight, and a resolved one stays visible, on the real /chat page (the user
2026-09-30, who pressed Relay on a thread and watched its comment stop showing on the transcript).

Relay sends a thread's discussion back into the session and leaves the thread open for more talk (T145), so the
thread is still an open conversation. The page used to paint it as RESOLVED: the mark took the resolved wash, a white
wash at 8% that the light theme's cream page swallows whole, and the rail tick dropped to 30%. A resolved
thread's mark vanished in that theme the same way, and a thread in the relay's transient `merging` status lost its rail
tick outright.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web: three prompts, three replies) with
three threads, one per reply: tid-1 open (the one the driver relays), tid-2 resolved (a legacy closed thread), tid-3
open (the reference an open thread looks like). The parent session runs on the fake Agent SDK, so the relay's handoff
is really delivered and the kernel really settles the thread. Asserted in the browser, in the dark theme and in
the light theme (the settings key `yatharth-light`, the product's own light theme: `theme-light` AND
`chat-theme-yatharth` on the body, never a class toggled by hand):
  the store says `merged` after the Relay click, with the anchor untouched;
  the relayed thread's mark carries no `resolved` class, paints the same wash an open thread's mark does, and keeps
  its relayed tooltip; its rail tick has the open tick's full strength;
  a thread in `merging` (a comments frame dispatched on the page, the status the kernel holds while the handoff is
  delivered) keeps its mark and its rail tick;
  the resolved thread's mark stays dimmer than an open one but VISIBLE (its painted wash differs from the page by a
  perceptible OKLab distance), and its tick too, in both themes.
Each read is a PIXEL read of the painted screen, the mark's text made transparent for it, against the same pixel with
the wash (or the tick) taken away. With COMMENT_SHOTS=<dir> (and SHOT_TAG, default "after") the driver writes the
relayed and resolved marks in both themes. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC
fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
import json
import math
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
FAKE_SDK = os.path.join(HERE, "fixtures", "fake_agent_sdk")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-5e1a75e1a75e"
T_RELAY = "bbbbbbbb-1111-2222-3333-5e1a75e1a75e"   # the thread the driver relays
T_DONE = "cccccccc-1111-2222-3333-5e1a75e1a75e"    # a resolved thread
T_OPEN = "dddddddd-1111-2222-3333-5e1a75e1a75e"    # an open thread: what open looks like

P_RELAY = "Cache the notes-api list responses for thirty seconds, keyed by the query string."
P_DONE = "Drop the legacy export endpoint once the new stream has run clean for a week."
P_OPEN = "Keep the retry cap at two minutes on every sync path."


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text, sid):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "sessionId": sid, "message": {"role": "user", "content": text}}


def agent(t, uuid, parent, text, sid):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": sid,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def _oklab(rgb):
    def lin(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(x) for x in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def delta(a, b):
    """The OKLab distance between two painted pixels, x100: about 2 is where two flat colours start to read as two."""
    return 100 * math.dist(_oklab(a), _oklab(b))


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
if (cfg.shots) fs.mkdirSync(cfg.shots, { recursive: true });
// the theme the PRODUCT's way: the settings store's key, read by theme.ts applyTheme at boot
const THEMES = { dark: "classic", light: "yatharth-light" };

const openChat = async (theme) => {
  const ctx = await browser.newContext({ viewport: { width: 760, height: 900 }, deviceScaleFactor: 2 });
  await ctx.addInitScript(({ t, sid }) => {
    try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {}
    window.__cmtFrames = 0; window.__statuses = []; window.__lastFrame = null;
    window.addEventListener("message", (e) => {
      const m = e.data; if (!m || m.type !== "comments" || m.id !== sid || m.__probe) return;   // the driver's own frame is not the kernel's
      window.__cmtFrames++; window.__lastFrame = m;
      window.__statuses.push((m.threads || []).map((th) => th.tid + ":" + th.status).join(","));
    }, true);
  }, { t: THEMES[theme], sid: cfg.sid });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  page.on("console", (m) => { if (m.type() === "error") errs.push("console: " + m.text().slice(0, 300)); });
  await page.goto(cfg.chat);
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  await page.waitForSelector("#content .turn", { timeout: 60000 });
  // the marks are a join of the chat frame (the turns) and the comments frame (the threads), in either order: when the
  // turns landed after the comments frame was processed, the next comments frame is the event that places the marks
  const all = (tids) => tids.every((t) => document.querySelector(`mark.cmt-hl[data-tid="${t}"]`) && document.querySelector(`.cmt-tick[data-tid="${t}"]`));
  if (!(await page.evaluate(all, cfg.tids))) {
    const n0 = await page.evaluate(() => window.__cmtFrames);
    await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 30000 }).catch(() => {});
  }
  await page.waitForFunction(all, cfg.tids, { timeout: 20000 }).catch(async () => {
    const seen = await page.evaluate(() => ({ frames: window.__cmtFrames, statuses: window.__statuses, marks: document.querySelectorAll("mark.cmt-hl").length, ticks: document.querySelectorAll(".cmt-tick").length,
      turns: Array.from(document.querySelectorAll("#content .turn")).map((t) => (t.dataset.uuid || "?") + ":" + t.className.split(" ").slice(0, 3).join(".")) }));
    console.error("marks-miss: " + JSON.stringify(seen) + "\nerrors: " + JSON.stringify(errs)); process.exit(4);
  });
  await page.mouse.move(380, 890);   // off every mark: a hovered mark wears the hover tier
  await page.waitForTimeout(500);   // the rail's rAF pass
  return { ctx, page };
};

// one painted pixel (the top-left device pixel of a 1x1 CSS clip), decoded through a canvas in the page
const pixelAt = async (page, x, y) => {
  const b64 = (await page.screenshot({ clip: { x: Math.floor(x), y: Math.floor(y), width: 1, height: 1 } })).toString("base64");
  return page.evaluate(async (b64) => {
    const img = new Image(); img.src = "data:image/png;base64," + b64; await img.decode();
    const c = document.createElement("canvas"); c.width = img.width; c.height = img.height;
    const x = c.getContext("2d"); x.drawImage(img, 0, 0);
    const d = x.getImageData(0, 0, 1, 1).data; return [d[0], d[1], d[2]];
  }, b64);
};

// a thread's mark and tick as the page paints them: classes, computed style, and the painted pixel with and without
const look = async (page, tid) => {
  const info = await page.evaluate((tid) => {
    const m = document.querySelector(`mark.cmt-hl[data-tid="${tid}"]`);
    const k = document.querySelector(`.cmt-tick[data-tid="${tid}"]`);
    const out = { mark: null, tick: null };
    if (m) {
      const r = Array.from(m.getClientRects()).find((q) => q.width > 8) || m.getBoundingClientRect();
      out.mark = { cls: m.className, bg: getComputedStyle(m).backgroundColor, title: m.title, x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    if (k) {
      const r = k.getBoundingClientRect();
      out.tick = { cls: k.className, opacity: getComputedStyle(k).opacity, bg: getComputedStyle(k).backgroundColor, x: r.left + r.width / 2, y: r.top + r.height / 2 };
    }
    return out;
  }, tid);
  const sel = `mark.cmt-hl[data-tid="${tid}"]`;
  if (info.mark) {
    await page.evaluate((sel) => document.querySelectorAll(sel).forEach((m) => m.style.setProperty("color", "transparent", "important")), sel);
    info.mark.on = await pixelAt(page, info.mark.x, info.mark.y);
    await page.evaluate((sel) => document.querySelectorAll(sel).forEach((m) => m.style.setProperty("background", "transparent", "important")), sel);
    info.mark.off = await pixelAt(page, info.mark.x, info.mark.y);
    await page.evaluate((sel) => document.querySelectorAll(sel).forEach((m) => { m.style.removeProperty("color"); m.style.removeProperty("background"); }), sel);
  }
  if (info.tick) {
    const ts = `.cmt-tick[data-tid="${tid}"]`;
    info.tick.on = await pixelAt(page, info.tick.x, info.tick.y);
    await page.evaluate((ts) => { document.querySelector(ts).style.visibility = "hidden"; }, ts);
    info.tick.off = await pixelAt(page, info.tick.x, info.tick.y);
    await page.evaluate((ts) => { const k = document.querySelector(ts); if (k) k.style.visibility = ""; }, ts);
  }
  return info;
};
const looks = async (page) => {
  const out = { body: await page.evaluate(() => document.body.className) };
  for (const tid of cfg.tids) out[tid] = await look(page, tid);
  return out;
};
// a screenshot of one thread's turn, the rail beside it included
const shotTurn = async (page, tid, name) => {
  if (!cfg.shots) return;
  const r = await page.evaluate((tid) => {
    const m = document.querySelector(`mark.cmt-hl[data-tid="${tid}"]`); const t = m && m.closest(".turn");
    if (!t) return null;   // no mark to frame: the assertions name that, the shot is skipped
    const q = t.getBoundingClientRect(); return { y: Math.max(0, q.top - 24), h: q.height + 48 };
  }, tid);
  if (!r) return;
  await page.screenshot({ path: cfg.shots + "/" + name + "-" + cfg.tag + ".png", clip: { x: 0, y: r.y, width: 760, height: Math.min(r.h, 900 - r.y) } });
};

const out = {};
// ── dark: the threads as seeded, a MERGING frame, then the real Relay ──
{
  const { ctx, page } = await openChat("dark");
  out.darkBefore = await looks(page);
  // the kernel holds `merging` while it delivers the handoff (its CAS latch): the same frame with tid-1 in that status,
  // dispatched the way the page's own shim dispatches a kernel frame
  await page.evaluate((tid) => {
    const f = JSON.parse(JSON.stringify(window.__lastFrame));
    f.__probe = true;
    for (const th of f.threads) if (th.tid === tid) th.status = "merging";
    window.dispatchEvent(new MessageEvent("message", { data: f }));
  }, cfg.relay);
  await page.waitForTimeout(500);
  out.merging = await look(page, cfg.relay);
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: Object.assign(JSON.parse(JSON.stringify(window.__lastFrame)), { __probe: true }) })));   // back to the kernel's truth
  await page.waitForTimeout(300);
  // Relay, the user's way: open the thread from its mark, press the button
  await page.click(`mark.cmt-hl[data-tid="${cfg.relay}"]`);
  await page.waitForSelector('#cmt-pop [data-act="cmtmerge"]', { timeout: 10000 });
  await page.click('#cmt-pop [data-act="cmtmerge"]');
  const settled = await page.waitForFunction((tid) => (window.__statuses[window.__statuses.length - 1] || "").includes(tid + ":merged"), cfg.relay, { timeout: 30000 }).then(() => true).catch(() => false);
  out.settled = settled;
  out.statuses = await page.evaluate(() => window.__statuses);
  out.popTitle = await page.evaluate(() => { const t = document.querySelector("#cmt-pop .cmt-title"); return t ? t.textContent : null; });
  out.store = JSON.parse(fs.readFileSync(cfg.store, "utf8"));
  await page.keyboard.press("Escape");
  await page.evaluate(() => { const x = document.querySelector("#cmt-pop .cmt-x"); if (x) x.click(); });
  await page.mouse.move(380, 890);
  await page.waitForTimeout(600);
  out.darkAfter = await looks(page);
  await shotTurn(page, cfg.relay, "dark-relayed");
  await shotTurn(page, cfg.done, "dark-resolved");
  if (cfg.shots) await page.screenshot({ path: cfg.shots + "/dark-page-" + cfg.tag + ".png" });
  await ctx.close();
}
// ── the light theme: the relayed thread (now merged in the store) and the resolved one ──
{
  const { ctx, page } = await openChat("light");
  out.light = await looks(page);
  await shotTurn(page, cfg.relay, "yatharth-light-relayed");
  await shotTurn(page, cfg.done, "yatharth-light-resolved");
  if (cfg.shots) await page.screenshot({ path: cfg.shots + "/yatharth-light-page-" + cfg.tag + ".png" });
  await ctx.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentRelayMark(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-relay-mark-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        parent = [user(t0, "u1", None, "how long should the notes-api cache its list responses?", WEB),
                  agent(t0 + 5, "a1", "u1", P_RELAY + " A longer window starts serving stale titles after an edit.", WEB),
                  user(t0 + 60, "u2", "a1", "and the old export endpoint?", WEB),
                  agent(t0 + 65, "a2", "u2", P_DONE + " Nothing else reads it.", WEB),
                  user(t0 + 120, "u3", "a2", "what about retries?", WEB),
                  agent(t0 + 125, "a3", "u3", P_OPEN + " Longer caps only delay the error the user will see anyway.", WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        # three threads, each a fork cut at its anchor (the copied history, then one exchange), its reply SEEN (lastSeenT
        # past it: no unread cue in the way of the read); no names/ entry (a thread is never on the board)
        rows = []
        now = int(time.time())
        for i, (tsid, anchor, exact, status, ask, reply) in enumerate([
                (T_RELAY, "a1", P_RELAY, "open", "is thirty seconds enough?", "Thirty seconds covers the burst of list calls a page load makes."),
                (T_DONE, "a2", P_DONE, "resolved", "who still calls it?", "No client has called it in the last month of logs."),
                (T_OPEN, "a3", P_OPEN, "open", "why two minutes?", "Two minutes is past the longest outage the sync has ridden out.")]):
            t = t0 + 300 + 60 * i
            upto = [r for r in parent if r["uuid"] in ("u1", "a1", "u2", "a2", "u3", "a3")[:2 * (i + 1)]]
            thread = [dict(r, sessionId=tsid) for r in upto]
            thread += [user(t, "c%d" % i, anchor, ask, tsid), agent(t + 20, "r%d" % i, "c%d" % i, reply, tsid)]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            rows.append({"tid": "tid-%d" % (i + 1), "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor,
                         "cutUuid": anchor, "exact": exact, "status": status, "createdT": t, "lastSeenT": now})
        cls.store = os.path.join(state, "comments", WEB + ".json")
        Path(cls.store).write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-relaymark"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=FAKE_SDK,   # the parent runs on the fake Agent SDK: the relay's handoff is really delivered
                              ROMP_FAKE_SDK_REPLY="Noted: the list cache holds for thirty seconds.")
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

    def test_a_relayed_thread_keeps_its_highlight_and_a_resolved_one_stays_visible(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "tids": ["tid-1", "tid-2", "tid-3"], "relay": "tid-1", "done": "tid-2", "store": self.store,
                       "shots": os.environ.get("COMMENT_SHOTS", ""), "tag": os.environ.get("SHOT_TAG", "after")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            def _dec(b):
                return "" if b is None else (b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b)
            self.fail("driver ran past its 420 s cap:\n%s%s\nkernel:\n%s" % (_dec(e.stdout)[-3000:], _dec(e.stderr)[-3000:], open(self.klog).read()[-2000:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-2000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])

        # the kernel's side: the Relay settled the thread to `merged` (still talkable), its anchor untouched
        self.assertTrue(r["settled"], "the page saw the relayed thread settle: %r\nkernel:\n%s" % (r["statuses"], open(self.klog).read()[-2000:]))
        row = next(t for t in r["store"]["threads"] if t["tid"] == "tid-1")
        self.assertEqual(row["status"], "merged", row)
        self.assertEqual((row["anchorUuid"], row["exact"]), ("a1", P_RELAY), "the relay never moves the anchor: %r" % row)
        self.assertGreater(row.get("relayedT") or 0, 0, row)
        self.assertIn("relayed to the session", r["popTitle"] or "", r["popTitle"])

        def wash(lk):
            return delta(lk["mark"]["on"], lk["mark"]["off"])

        def tick(lk):
            return delta(lk["tick"]["on"], lk["tick"]["off"])

        # the transient `merging` status keeps the mark and the rail tick, in the open look
        mg = r["merging"]
        self.assertIsNotNone(mg["mark"], "a merging thread keeps its mark: %r" % mg)
        self.assertIsNotNone(mg["tick"], "a merging thread keeps its rail tick: %r" % mg)
        self.assertNotIn("resolved", mg["mark"]["cls"].split(), mg)
        self.assertNotIn("resolved", mg["tick"]["cls"].split(), mg)

        for label, m, theme in (("dark, after the relay", r["darkAfter"], "dark"), ("light theme", r["light"], "light")):
            classes = m["body"].split()
            if theme == "light":
                self.assertIn("theme-light", classes, "the product's light theme: %r" % m["body"])
                self.assertIn("chat-theme-yatharth", classes, "the light theme carries both classes: %r" % m["body"])
            else:
                self.assertNotIn("theme-light", classes, m["body"])
            relayed, done, open_ = m["tid-1"], m["tid-2"], m["tid-3"]
            for tid, lk in (("tid-1", relayed), ("tid-2", done), ("tid-3", open_)):
                self.assertIsNotNone(lk["mark"], "%s: %s has its mark: %r" % (label, tid, lk))
                self.assertIsNotNone(lk["tick"], "%s: %s has its rail tick: %r" % (label, tid, lk))
            # RELAYED renders as OPEN: no resolved class, the open wash, the open tick, its own tooltip
            self.assertNotIn("resolved", relayed["mark"]["cls"].split(), "%s: a relayed thread is not resolved: %r" % (label, relayed))
            self.assertNotIn("resolved", relayed["tick"]["cls"].split(), "%s: nor is its tick: %r" % (label, relayed))
            self.assertEqual(relayed["mark"]["bg"], open_["mark"]["bg"], "%s: the relayed mark wears the open wash: %r vs %r" % (label, relayed, open_))
            self.assertLess(abs(wash(relayed) - wash(open_)), 1.0, "%s: …as painted: %r vs %r" % (label, relayed["mark"], open_["mark"]))
            self.assertGreaterEqual(wash(relayed), 3.0, "%s: the relayed mark is VISIBLE on the page: %.1f %r" % (label, wash(relayed), relayed["mark"]))
            self.assertIn("relayed", relayed["mark"]["title"], "%s: its tooltip still says it was relayed: %r" % (label, relayed["mark"]["title"]))
            self.assertEqual(relayed["tick"]["opacity"], open_["tick"]["opacity"], "%s: the relayed tick at the open tick's strength: %r vs %r" % (label, relayed["tick"], open_["tick"]))
            self.assertLess(abs(tick(relayed) - tick(open_)), 2.0, "%s: …as painted: %r vs %r" % (label, relayed["tick"], open_["tick"]))
            # RESOLVED stays dimmed but visible: its wash and its tick read against the page, and less than an open one's
            self.assertIn("resolved", done["mark"]["cls"].split(), "%s: %r" % (label, done))
            self.assertGreaterEqual(wash(done), 2.5, "%s: the resolved mark is still VISIBLE: %.1f %r" % (label, wash(done), done["mark"]))
            self.assertLess(wash(done), wash(open_), "%s: …and dimmer than an open one: %.1f vs %.1f" % (label, wash(done), wash(open_)))
            self.assertGreaterEqual(tick(done), 8.0, "%s: the resolved tick is findable: %.1f %r" % (label, tick(done), done["tick"]))
            self.assertLess(tick(done), tick(open_), "%s: …and dimmer than an open one: %.1f vs %.1f" % (label, tick(done), tick(open_)))
        # the dark read before the relay: the seeded resolved thread was visible there too (the same floors)
        db = r["darkBefore"]
        self.assertGreaterEqual(wash(db["tid-2"]), 2.5, "dark, as seeded: the resolved mark: %r" % db["tid-2"])


if __name__ == "__main__":
    unittest.main()
