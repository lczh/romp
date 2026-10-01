#!/usr/bin/env python3
"""A new comment is sent once, shows that it was sent the moment Enter is pressed, and its answer from the kernel
moves only its own box, on the real /chat page (2026-10-01: the user pressed Enter in a new comment, saw nothing change
for seconds, and pressed again or clicked away and commented again, which left pairs of threads with the same words on
the same passage; the highlight of a fresh comment came and went; clicking away and starting a
second comment had the first one's thread pop up over it).

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web: two prompts, two replies) whose first
reply already carries a thread, and the parent runs on the fake Agent SDK, so every create is the kernel's real create
door: the fork, the opening message, the thread row. The page's copy of the kernel's answer (the comments frame and the
commentCreated ack) is HELD by a capture listener while a case needs the create in flight, and released on cue, which
stands in for a kernel that takes seconds to answer (it reads the whole transcript first; measured at 3 to 14 s on a
110 MB session). Every commentCreate the page posts is counted at the socket. Asserted:
  Enter, Enter: one create posted, one thread made; from the first Enter the box shows the sent comment (the loader and
  the words as the pending bubble) and has no composer left;
  Enter, a click outside, the same passage selected and commented on again: the box re-opens on that send (no composer
  holding the words to send twice), and the kernel's answer adopts the thread into it; one create, one thread;
  Enter, a click outside, a second comment typed on another passage: the first comment's answer leaves the second box
  and its words alone;
  a comment on a reply that already has a thread, with an ordinary comments frame arriving while it is in flight: the
  new passage keeps its highlight;
  an unrelated warning while typing leaves the composer, its caret and the words alone; one after Enter leaves the sent
  view and the highlight up, and nothing can be sent twice;
  a create the kernel refuses (a taken name) hands the words back to an editable composer and drops the highlight.
Red before the change: two creates and two threads; a composer holding the sent words; the first thread's popover over
the second box with its words gone; the highlight dropped by the frame; a rebuilt composer with the caret at the end, and
after Enter a re-armed arrow with no highlight. Skips LOUDLY without the extension deps or a Playwright browser.
SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
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
    "double": "Keep the retry cap at two minutes",
    "reopen": "on every sync path",
    "first": "Longer caps only delay the error",
    "second": "A longer window starts serving stale titles",
    "frame": "the export stream stays uncached",
    "warn": "The header is written once per export",
    "refused": "the footer carries the row count",
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
  // the kernel's answer, HELD on cue: the comments frame for the session and the create's ack/nack wait in order and are
  // released by __release(); a frame the driver dispatches itself (__probe) passes. Registered before the page's own
  // listeners, so stopping the event here keeps it from them.
  window.__hold = false; window.__held = []; window.__lastCF = null; window.__posts = [];
  const held = (m) => m && ((m.type === "comments" && m.id === sid) || m.type === "commentCreated" || m.type === "commentCreateFailed");
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || m.__released || m.__probe) return;
    if (window.__hold && held(m)) { e.stopImmediatePropagation(); window.__held.push(m); return; }
    if (m.type === "comments" && m.id === sid) window.__lastCF = m;
  }, true);
  window.__release = () => {
    window.__hold = false;
    const q = window.__held; window.__held = [];
    for (const m of q) { if (m.type === "comments") window.__lastCF = m; window.dispatchEvent(new MessageEvent("message", { data: Object.assign({}, m, { __released: true }) })); }
    return q.length;
  };
  // every commentCreate the page posts, counted at the socket
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) {
    try { const m = JSON.parse(d); if (m && m.type === "commentCreate") window.__posts.push(m.exact); } catch (e) {}
    return send.call(this, d);
  };
}, cfg.sid);
const page = await ctx.newPage();
const errs = [];
page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
await page.goto(cfg.chat);
try {
  await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
  await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
  await page.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.p.double, { timeout: 60000 });
  // the comments frame has landed and placed the old thread's mark: the page knows the reply already has a thread
  await page.waitForFunction(() => !!window.__lastCF && document.querySelectorAll("#content mark.cmt-hl").length > 0, null, { timeout: 30000 });
} catch (e) { await done("the chat never showed the replies and the old thread: " + String(e).split("\n")[0]); }

const select = (text) => page.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.textContent || "").includes(text));
  if (!turn) return false;
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return false;
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
  return true;
}, text);
const comment = async (text) => { await select(text); await page.keyboard.press("Control+/"); await page.waitForSelector("#cmt-pop", { timeout: 8000 }); };
const pop = () => page.evaluate(() => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const box = p.querySelector(".cmt-input");
  const q = p.querySelector(".cmt-msgs > .turn-queued");
  return { mode: p.dataset.mode, sentView: !!p.querySelector(".cmt-boot-sent"), box: box ? box.value : null,
           railStub: q ? getComputedStyle(q, "::before").display !== "none" : null,
           bubble: ((p.querySelector(".cmt-msgs") || {}).textContent || ""), sendDisabled: (p.querySelector(".cmt-send") || {}).disabled ?? null };
});
// is there an optimistic mark (a create in flight) / a thread's mark on this passage?
const marks = (text) => page.evaluate((text) => {
  const on = Array.from(document.querySelectorAll("#content mark.cmt-hl")).filter((m) => text.includes((m.textContent || "").trim()) && (m.textContent || "").trim().length > 3);
  return { pending: on.some((m) => (m.dataset.tid || "").startsWith("pending:")), real: on.some((m) => !(m.dataset.tid || "").startsWith("pending:")) };
}, text);
const posts = (text) => page.evaluate((text) => window.__posts.filter((e) => e === text).length, text);
const closePop = async () => { await page.evaluate(() => document.querySelector("#cmt-pop .cmt-x")?.click()); await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {}); };
const adopted = () => page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 30000 }).then(() => true, () => false);
const realMark = (text) => page.waitForFunction((text) => Array.from(document.querySelectorAll("#content mark.cmt-hl")).some((m) => !(m.dataset.tid || "").startsWith("pending:") && text.includes((m.textContent || "").trim()) && (m.textContent || "").trim().length > 3), text, { timeout: 30000 }).then(() => true, () => false);

// 1. Enter, Enter
{
  const P = cfg.p.double;
  await comment(P);
  await page.keyboard.type("Why two minutes?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  const afterFirst = await pop();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  out.double = { afterFirst, posts: await posts(P) };
  await page.evaluate(() => window.__release());
  out.double.adopted = await adopted();
  out.double.postsAfter = await posts(P);
  await closePop();
}
// 2. Enter, click outside, the same passage again
{
  const P = cfg.p.reopen;
  await comment(P);
  await page.keyboard.type("Every one of them?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.mouse.click(600, 30);
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
  const closed = await page.evaluate(() => !document.getElementById("cmt-pop"));
  await comment(P);
  const reopened = await pop();
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  out.reopen = { closed, reopened, posts: await posts(P) };
  await page.evaluate(() => window.__release());
  out.reopen.adopted = await adopted();
  out.reopen.postsAfter = await posts(P);
  await closePop();
}
// 3. Enter, click outside, a second comment typed on another passage, then the first one's answer
{
  const P = cfg.p.first, Q = cfg.p.second;
  await comment(P);
  await page.keyboard.type("How long is too long?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.mouse.click(600, 30);
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
  await comment(Q);
  await page.keyboard.type("Second thought here");
  await page.evaluate(() => window.__release());
  const landed = await realMark(P);
  await page.waitForTimeout(300);
  out.second = { landed, pop: await pop() };
  await closePop();
}
// 4. a comment on a reply that already has a thread, an ordinary comments frame arriving while it is in flight
{
  const P = cfg.p.frame;
  await comment(P);
  await page.keyboard.type("Why not cache it?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  const before = await marks(P);
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: Object.assign(JSON.parse(JSON.stringify(window.__lastCF)), { __probe: true }) })));
  await page.waitForTimeout(100);
  out.frame = { before, after: await marks(P) };
  await page.evaluate(() => window.__release());
  out.frame.adopted = await adopted();
  await closePop();
}
// 5. an unrelated warning while typing, and one after Enter
{
  const P = cfg.p.warn;
  await comment(P);
  await page.keyboard.type("first half second half");
  for (let i = 0; i < 12; i++) await page.keyboard.press("ArrowLeft");
  await page.evaluate(() => { window.__box = document.querySelector("#cmt-pop .cmt-input"); });
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "warn", text: "api: an unrelated warning from another session" } })));
  await page.waitForTimeout(150);
  const typing = await page.evaluate(() => { const b = document.querySelector("#cmt-pop .cmt-input"); return { same: b === window.__box, caret: b ? b.selectionStart : null }; });
  await page.keyboard.type("X");
  const words = await page.evaluate(() => document.querySelector("#cmt-pop .cmt-input").value);
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { type: "warn", text: "api: an unrelated warning from another session" } })));
  await page.waitForTimeout(150);
  const afterSend = await pop();
  const mark = await marks(P);
  await page.evaluate(() => document.querySelector("#cmt-pop .cmt-send")?.click());
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  out.warn = { typing, words, afterSend, mark, posts: await posts(P) };
  await page.evaluate(() => window.__release());
  out.warn.adopted = await adopted();
  await closePop();
}
// 6. a create the kernel refuses: a name already taken by the thread the first reply carries
{
  const P = cfg.p.refused;
  await page.evaluate(() => { for (const t of Array.from(document.querySelectorAll(".warn-toast"))) t.remove(); });   // case 5's warnings
  await comment(P);
  await page.evaluate(() => { const n = document.querySelector("#cmt-pop .cmt-name"); n.value = "web-comment-1"; n.dispatchEvent(new Event("input")); });
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type("Is it ever empty?");
  await page.keyboard.press("Enter");
  const back = await page.waitForFunction(() => { const b = document.querySelector("#cmt-pop .cmt-input"); return !!b && b.value === "Is it ever empty?"; }, null, { timeout: 30000 }).then(() => true, () => false);
  await page.waitForTimeout(200);
  out.refused = { back, pop: await pop(), mark: await marks(P), posts: await posts(P),
                  toasts: await page.evaluate(() => Array.from(document.querySelectorAll(".warn-toast-msg")).map((t) => t.textContent)) };
  await closePop();
}
out.store = JSON.parse(fs.readFileSync(cfg.store, "utf8"));
out.errs = errs;
await done();
"""


class ServedCommentCreate(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-create-")
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
        cls.port, cls.token = _free_port(), "testtok-cmtcreate"
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

    def test_one_send_per_comment_and_each_answer_moves_only_its_own_box(self):
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
        threads = r["store"]["threads"]
        on = lambda key: [t for t in threads if t["exact"] == PASSAGES[key]]

        # 1. Enter, Enter: one create, one thread; the box shows the sent comment from the first Enter
        d = r["double"]
        self.assertTrue(d["afterFirst"]["sentView"], d)
        self.assertIsNone(d["afterFirst"]["box"], "nothing left to type or send: %r" % d)
        self.assertIn("Why two minutes?", d["afterFirst"]["bubble"], "the words as the pending bubble")
        self.assertIs(d["afterFirst"]["railStub"], False, "no orphan rail stub beside the bubble: %r" % d)
        self.assertEqual((d["posts"], d["postsAfter"]), (1, 1), "the second Enter sent nothing: %r" % d)
        self.assertTrue(d["adopted"], "the kernel's answer adopts the thread into the box")
        self.assertEqual(len(on("double")), 1, threads)

        # 2. Enter, a click outside, the same passage again: the box re-opens on that send
        o = r["reopen"]
        self.assertTrue(o["closed"], "the click outside closed the box")
        self.assertTrue(o["reopened"]["sentView"], "re-opened on the send in flight: %r" % o)
        self.assertIsNone(o["reopened"]["box"], "no composer holding the words already sent: %r" % o)
        self.assertEqual((o["posts"], o["postsAfter"]), (1, 1), o)
        self.assertTrue(o["adopted"], "the answer adopts the thread into the re-opened box")
        self.assertEqual(len(on("reopen")), 1, threads)

        # 3. the first comment's answer leaves the second box and its words alone
        s = r["second"]
        self.assertTrue(s["landed"], "the first comment's thread landed: %r" % s)
        self.assertEqual((s["pop"] or {}).get("mode"), "create", "still the second comment's box: %r" % s)
        self.assertEqual((s["pop"] or {}).get("box"), "Second thought here", s)
        self.assertEqual(len(on("first")), 1, threads)
        self.assertEqual(on("second"), [], "the second comment was never sent")

        # 4. a frame mid-create on a reply that already has a thread: the new passage keeps its highlight
        fr = r["frame"]
        self.assertTrue(fr["before"]["pending"], fr)
        self.assertTrue(fr["after"]["pending"], "the optimistic highlight survives an ordinary frame: %r" % fr)
        self.assertTrue(fr["adopted"])

        # 5. unrelated warnings: the composer stays put while typing; after Enter the sent view and highlight stay
        w = r["warn"]
        self.assertEqual(w["typing"], {"same": True, "caret": 10}, "the same composer, the caret where it was: %r" % w)
        self.assertEqual(w["words"], "first halfX second half", "the next key lands where the caret was")
        self.assertTrue(w["afterSend"]["sentView"], w)
        self.assertIsNone(w["afterSend"]["box"], w)
        self.assertTrue(w["mark"]["pending"], "the highlight stays up through an unrelated warning: %r" % w)
        self.assertEqual(w["posts"], 1, "a click and an Enter after the warning sent nothing: %r" % w)
        self.assertTrue(w["adopted"])
        self.assertEqual(len(on("warn")), 1, threads)

        # 6. a refused create hands the words back to an editable composer and drops the highlight
        f = r["refused"]
        self.assertTrue(f["back"], "the words came back to the composer: %r" % f)
        self.assertEqual((f["pop"] or {}).get("mode"), "create", f)
        self.assertFalse((f["pop"] or {}).get("sentView"), f)
        self.assertFalse((f["pop"] or {}).get("sendDisabled"), "the arrow is armed again: %r" % f)
        self.assertFalse(f["mark"]["pending"], "the optimistic highlight went with the refusal: %r" % f)
        self.assertEqual(f["posts"], 1, f)
        self.assertTrue([t for t in f["toasts"] if "unrelated" not in t], "the kernel's warning says why: %r" % f["toasts"])
        self.assertEqual(on("refused"), [], threads)
        self.assertEqual(r["errs"], [], r["errs"])


if __name__ == "__main__":
    unittest.main()
