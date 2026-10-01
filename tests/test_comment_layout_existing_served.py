#!/usr/bin/env python3
"""A sent comment's box takes the thread's layout the moment Enter is pressed, and a Comment on a passage that already has
a thread opens that thread, on the real /chat page (the user 2026-10-01, who approved both: the box no longer jumps seconds
after the gesture, when the kernel answers, and one passage never silently grows a second thread).

The same hermetic lab as test_comment_create_served.py (the notes-api demo world, session web on the fake Agent SDK, the
first reply already carrying a thread on its opening words), with the page's copy of the kernel's answer HELD on cue.
Asserted:
  the box at the selection turns into the thread's layout at Enter (70% wide, 60% tall, right-aligned) before the kernel
  answers, and the thread the answer brings keeps exactly that rect;
  a remembered size (the user's own pull, kept per page) is the size the sent box and its thread take;
  a box the user maximized keeps its size and place through the send and the thread;
  a Comment on a passage that overlaps the existing thread's (same reply, overlapping text) opens that thread, with "New
  thread instead", which opens the comment box on exactly the selection, and sending it makes the second thread;
  a passage of the same reply that does not overlap opens the comment box; a click on the thread's highlight opens the
  thread without the offer;
  a Comment on a passage overlapping a send still in flight re-opens that send's box.
Red before the change: the small box at the selection until the answer, then the jump; a fresh comment box on the passage
that has a thread; a fresh box on the passage whose send is in flight. Skips LOUDLY without the extension deps or a
Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
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
FAKE_SDK = os.path.join(HERE, "fixtures", "fake_agent_sdk")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-c0c0a1e1c0c0"
T_OLD = "bbbbbbbb-1111-2222-3333-c0c0a1e1c0c0"     # the thread the first reply already carries
A1 = ("Cache the notes-api list responses for thirty seconds, keyed by the query string. A longer window starts serving "
      "stale titles after an edit, and the export stream stays uncached.")
A2 = ("Keep the retry cap at two minutes on every sync path. Longer caps only delay the error the user will see anyway. "
      "The header is written once per export, and the footer carries the row count.")
P_OLD = "Cache the notes-api list responses"
PASSAGES = {
    "overlap": "notes-api list responses for thirty seconds",   # overlaps the existing thread's P_OLD
    "apart": "stale titles after an edit",                       # the same reply, no overlap
    "layout": "Keep the retry cap at two minutes",
    "remembered": "Longer caps only delay the error",
    "maximized": "The header is written once per export",
    "inflight": "the footer carries the row count",
    "inflightInside": "carries the row",                         # inside the in-flight passage
}


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


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = {};
const done = async (why) => { if (why) out.died = why; fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n"); await browser.close(); process.exit(0); };
const ctx = await browser.newContext({ viewport: { width: 1200, height: 860 } });
await ctx.addInitScript((sid) => {
  window.__hold = false; window.__held = [];
  const held = (m) => m && ((m.type === "comments" && m.id === sid) || m.type === "commentCreated" || m.type === "commentCreateFailed");
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || m.__released) return;
    if (window.__hold && held(m)) { e.stopImmediatePropagation(); window.__held.push(m); }
  }, true);
  window.__release = () => {
    window.__hold = false;
    const q = window.__held; window.__held = [];
    for (const m of q) window.dispatchEvent(new MessageEvent("message", { data: Object.assign({}, m, { __released: true }) }));
  };
}, cfg.sid);
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
await page.goto(cfg.chat);
try {
  await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
  await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
  await page.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.p.layout, { timeout: 60000 });
  await page.waitForFunction(() => document.querySelectorAll("#content mark.cmt-hl").length > 0, null, { timeout: 30000 });
} catch (e) { await done("the chat never showed the replies and the old thread: " + String(e).split("\n")[0]); }
out.vw = await page.evaluate(() => [window.innerWidth, window.innerHeight]);

// select `text` in the newest turn holding it, across text nodes (a passage can run into or out of a highlight's <mark>)
const select = (text) => page.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.textContent || "").includes(text));
  if (!turn) return false;
  const nodes = [];
  const walker = document.createTreeWalker(turn.querySelector(".md") || turn, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
  const all = nodes.map((n) => n.data).join("");
  const at = all.indexOf(text);
  if (at < 0) return false;
  const point = (off) => { let acc = 0; for (const n of nodes) { if (off <= acc + n.data.length) return [n, off - acc]; acc += n.data.length; } return null; };
  const a = point(at), b = point(at + text.length);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(a[0], a[1]); r.setEnd(b[0], b[1]); sel.addRange(r);
  return sel.toString() === text;
}, text);
const comment = async (text) => { await select(text); await page.keyboard.press("Control+/"); await page.waitForSelector("#cmt-pop", { timeout: 8000 }); await page.waitForTimeout(100); };
const pop = () => page.evaluate(() => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const r = p.getBoundingClientRect();
  return { mode: p.dataset.mode, tid: p.dataset.tid, status: p.dataset.status, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
           quote: ((p.querySelector(".cmt-quote") || {}).textContent || "").trim(), offer: ((p.querySelector(".cmt-newthread") || {}).textContent || "") };
});
const closePop = async () => { await page.evaluate(() => document.querySelector("#cmt-pop .cmt-x")?.click()); await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {}); };
const adopted = () => page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 30000 }).then(() => true, () => false);
const send = async (words) => {   // type, hold the answer, Enter: the box as the send leaves it, then the thread the answer brings
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type(words);
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  const sent = await pop();
  await page.evaluate(() => window.__release());
  const ok = await adopted();
  await page.waitForTimeout(150);
  return { sent, adopted: ok, thread: await pop() };
};

// a passage of the reply that already carries a thread, overlapping it: the thread opens, offering a new one
{
  await comment(cfg.p.overlap);
  const opened = await pop();
  await page.click("#cmt-pop .cmt-newthread").catch((e) => { out.offerClick = String(e).split("\n")[0]; });
  await page.waitForTimeout(150);
  const fresh = await pop();
  await page.focus("#cmt-pop .cmt-input").catch(() => {});
  await page.keyboard.type("Is thirty seconds per query string?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 30000 }).catch(() => {});
  out.overlap = { opened, fresh };
  await closePop();
}
// a passage of the same reply that does not overlap: the comment box
{
  await comment(cfg.p.apart);
  out.apart = await pop();
  await closePop();
}
// a click on the thread's own highlight: the thread, with no offer
{
  await page.evaluate((tid) => document.querySelector(`#content mark.cmt-hl[data-tid="${tid}"]`)?.click(), "tid-old");
  await page.waitForSelector("#cmt-pop", { timeout: 8000 }).catch(() => {});
  out.markClick = await pop();
  await closePop();
}
// the thread layout at Enter, nothing remembered
{
  await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
  await comment(cfg.p.layout);
  const composer = await pop();
  out.layout = { composer, ...(await send("Why two minutes?")) };
  await closePop();
}
// a remembered size: the sent box and its thread take it
{
  await page.evaluate(() => localStorage.setItem("romp:cmtPopSize", JSON.stringify({ w: 0.5, h: 0.5 })));
  await comment(cfg.p.remembered);
  out.remembered = await send("How long is too long?");
  await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
  await closePop();
}
// a box the user maximized: size and place kept through the send and the thread
{
  await comment(cfg.p.maximized);
  await page.click("#cmt-pop .cmt-max");
  await page.waitForTimeout(150);
  const max = await pop();
  out.maximized = { max, ...(await send("Is it written twice anywhere?")) };
  await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
  await closePop();
}
// a Comment overlapping a send still in flight re-opens that send's box
{
  await comment(cfg.p.inflight);
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type("Is it ever empty?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.mouse.click(600, 30);
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
  await comment(cfg.p.inflightInside);
  out.inflight = await page.evaluate(() => { const p = document.getElementById("cmt-pop"); return p ? { mode: p.dataset.mode, status: p.dataset.status } : null; });
  await page.evaluate(() => window.__release());
  await adopted();
  await closePop();
}
out.store = JSON.parse(fs.readFileSync(cfg.store, "utf8"));
out.errs = errs;
await done();
"""


class ServedCommentLayoutAndExisting(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-layout-existing-")
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
                  agent(t0 + 5, "a1", "u1", A1, WEB),
                  user(t0 + 60, "u2", "a1", "and retries?", WEB),
                  agent(t0 + 65, "a2", "u2", A2, WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        # the thread the first reply already carries: a fork cut at a1 with one exchange, its reply SEEN; no names/ entry
        t = t0 + 300
        thread = [dict(r, sessionId=T_OLD) for r in parent[:2]]
        thread += [user(t, "c1", "a1", "is thirty seconds enough?", T_OLD),
                   agent(t + 20, "r1", "c1", "Thirty seconds covers the burst of list calls a page load makes.", T_OLD)]
        Path(proj, T_OLD + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
        Path(state, "sdk", T_OLD + ".json").write_text(json.dumps(
            {"sid": T_OLD, "name": "web-comment-1", "cwd": cwd, "lastSid": T_OLD, "threadOf": WEB, "forkAt": "a1",
             "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
        cls.store = os.path.join(state, "comments", WEB + ".json")
        Path(cls.store).write_text(json.dumps({"threads": [
            {"tid": "tid-old", "sid": T_OLD, "name": "web-comment-1", "anchorUuid": "a1", "cutUuid": "a1", "exact": P_OLD,
             "status": "open", "createdT": t, "lastSeenT": int(time.time())}]}))
        cls.port, cls.token = _free_port(), "testtok-cmtlayout"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=FAKE_SDK,   # the parent runs on the fake Agent SDK: every create forks for real
                              ROMP_FAKE_SDK_REPLY="Two minutes is past the longest outage the sync has ridden out.")
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

    def test_the_send_takes_the_thread_layout_and_a_passage_with_a_thread_opens_it(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB,
                       "p": PASSAGES, "store": self.store}, f)
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
        self.assertNotIn("died", r, r.get("died"))
        vw, vh = r["vw"]
        near = lambda a, b, tol=2: abs(a - b) <= tol

        # a Comment on a passage overlapping the existing thread opens it, offering a new one
        o = r["overlap"]
        self.assertEqual((o["opened"] or {}).get("mode"), "thread", "the existing thread opened: %r" % o)
        self.assertEqual(o["opened"]["tid"], "tid-old", o)
        self.assertEqual(o["opened"]["offer"], "New thread instead", o)
        self.assertNotIn("offerClick", r, r.get("offerClick"))
        self.assertEqual((o["fresh"] or {}).get("mode"), "create", "the offer opens the comment box: %r" % o)
        self.assertEqual(o["fresh"]["quote"], PASSAGES["overlap"], "on exactly the selection")
        on = lambda text: [t for t in r["store"]["threads"] if t["exact"] == text]
        self.assertEqual(len(on(PASSAGES["overlap"])), 1, "the new thread was made on the selection: %r" % r["store"])
        self.assertEqual(len(on(P_OLD)), 1, "the existing thread is untouched")

        # apart in the same reply: the comment box; the highlight's own click: the thread without the offer
        self.assertEqual((r["apart"] or {}).get("mode"), "create", r["apart"])
        self.assertEqual((r["markClick"] or {}).get("tid"), "tid-old", r["markClick"])
        self.assertEqual(r["markClick"]["offer"], "", "no offer when nobody asked for a new comment")

        # the thread layout from the Enter, and the thread keeps the rect
        lay = r["layout"]
        cl, ct, cw, ch = lay["composer"]["rect"]
        sl, st, sw, sh = lay["sent"]["rect"]
        self.assertEqual(lay["sent"]["status"], "sent", lay)
        self.assertTrue(near(sw, round(vw * 0.7)) and near(sh, round(vh * 0.6)), "70%% x 60%% at the Enter: %r (window %r)" % (lay, r["vw"]))
        self.assertTrue(near(sl + sw, vw - 8), "right-aligned: %r" % lay)
        self.assertLess(cw * ch, sw * sh, "the comment box itself was the small one at the selection: %r" % lay)
        self.assertTrue(lay["adopted"], lay)
        self.assertEqual(lay["thread"]["mode"], "thread")
        self.assertTrue(all(near(a, b, 1) for a, b in zip(lay["thread"]["rect"], lay["sent"]["rect"])),
                        "no jump when the thread arrives: %r" % lay)

        # a remembered size is the sent box's and the thread's
        rem = r["remembered"]
        self.assertTrue(near(rem["sent"]["rect"][2], round(vw * 0.5)) and near(rem["sent"]["rect"][3], round(vh * 0.5)), rem)
        self.assertTrue(all(near(a, b, 1) for a, b in zip(rem["thread"]["rect"], rem["sent"]["rect"])), rem)

        # a maximized box keeps its size and place
        mx = r["maximized"]
        self.assertTrue(all(near(a, b, 1) for a, b in zip(mx["sent"]["rect"], mx["max"]["rect"])), "maximized stays maximized at the Enter: %r" % mx)
        self.assertTrue(all(near(a, b, 1) for a, b in zip(mx["thread"]["rect"], mx["max"]["rect"])), "…and in the thread: %r" % mx)

        # overlapping a send in flight re-opens its box
        self.assertEqual(r["inflight"], {"mode": "create", "status": "sent"}, r["inflight"])
        self.assertEqual(len(on(PASSAGES["inflight"])), 1)
        self.assertEqual(r["errs"], [], r["errs"])


if __name__ == "__main__":
    unittest.main()
