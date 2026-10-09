#!/usr/bin/env python3
"""The message box's behaviours in the file viewer and the comment threads, on the real /chat page (the user 2026-10-07:
words dictated over a file viewer landed in the chat's message box, and a comment thread had no select-quote-stage).

A hermetic kernel serves a synthetic chat (the notes-api demo world: session web, host TESTHOST; its files on disk; the
second reply carries a comment thread whose conversation has two exchanges). One driver run; each test reads its part:
  the file viewer (the modal): a drag over two code rows makes a quote chip in its bar; then, with nothing editable
  focused, dictation (CDP's Input.insertText, the text-insertion path the operating system's dictation takes: the page
  sees one `textInput` event at the body), a real Ctrl+V paste and typed keys each land in the viewer's bar and never in
  the chat's message box, and the chip is still there; a file TAB's bar takes dictation the same way;
  the comment thread (the sessions on the fake Agent SDK, so the thread's conversation is served): opened from its highlight with the caret outside it, a stray keystroke and a dictated word go into
  its reply box; a drag over a passage of the thread's own messages makes a quote chip in the reply box (and none in the
  chat's message box); words typed then (no click into the box) and Ctrl+Enter stage one item in the popover's staged
  strip; a second passage, words, Ctrl+Enter: two; Enter on the empty box sends ONE commentReply to the thread carrying
  both quotes, each under the conversation's lead-in, and both notes, in order; the strip empties.
With COMPOSER_PARITY_SHOTS=<dir> the driver writes the popover holding a staged item and a quote chip with words typed
against it, in the dark theme and the Yatharth light one. Skips LOUDLY without the extension deps or a Playwright browser.
SYNTHETIC fixtures only (placeholder ids, invented prose, host TESTHOST)."""
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

WEB = "aaaaaaaa-1111-2222-3333-5a7e0c000000"
THREAD = "bbbbbbbb-1111-2222-3333-5a7e0c000001"
APP_PY = "def notes():\n    return [\"a\", \"b\"]\n\n\nif __name__ == \"__main__\":\n    print(notes())\n"
GUIDE = "# Notes API guide\n\nThe web session keeps the notes-api tidy.\n\n## Fold rules\n\nOne rule per line.\n"
A1 = "Start with docs/guide.md, then the handler in src/app.py."
A2 = ("Cache the notes-api list responses for thirty seconds, keyed by the query string. The export stream stays "
      "uncached.")
PASSAGE = "Cache the notes-api list responses for thirty seconds"   # the thread's highlight in the second reply
P1 = "Thirty seconds covers the burst of list calls a page load makes."
P2 = "Each export is read once, so caching it would only hold memory."
N1 = "Why a page load?"
N2 = "Measure it first."
LEAD = "Replying to this part of the conversation:"
FAKE_REPLY = "Both are measured on the staging box."   # the fake Agent SDK's reply to the thread's sent run
# ONE fixed synthetic instant stamps every fixture row and starts the page's clock, in a page told it is in UTC: midday,
# so the rows (an hour back at most) and the page's today are one day at any hour on any runner. The rows used to be
# stamped from the wall clock, an hour back: in the first hour after midnight they fell on the page's yesterday, a day
# divider opened the thread's messages, they overflowed the popover's scroll box, and the drag over the second passage
# captured a prefix of it (red twice in CI between 00:00 and 01:00 UTC on 2026-10-09).
NOW = 1768478400   # 2026-01-15T12:00:00Z


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
const out = { errors: [], replies: [], sends: [] };
const T = 20000;
const finish = async (why) => { if (why) out.died = why; fs.writeFileSync(cfg.out, JSON.stringify(out)); fs.writeSync(1, "RESULT:" + cfg.out + "\n"); await browser.close(); process.exit(0); };
const wait = (page, fn, arg, why, ms) => page.waitForFunction(fn, arg, { timeout: ms || T }).catch(async (e) => { await finish(why + " (" + String(e).split("\n")[0] + ")"); });
const THEMES = { dark: "yatharth", light: "yatharth-light" };   // the settings store's keys, read by theme.ts at boot
async function open(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, deviceScaleFactor: 2, timezoneId: "UTC" });
  await ctx.clock.install({ time: cfg.nowMs });   // the fixture's NOW, flowing from there: its rows are an hour old, today
  await ctx.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(cfg.chat).origin });
  if (theme) await ctx.addInitScript((t) => { try { if (!localStorage.getItem("romp:settings")) localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, THEMES[theme]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(String(e && e.stack || e).slice(0, 500)));
  // what the kernel is sent: the page's own socket frames (a reply to the thread, a message to the session)
  page.on("websocket", (ws) => ws.on("framesent", (f) => { try { const m = JSON.parse(f.payload);
    if (m && m.type === "commentReply") out.replies.push({ id: m.id, tid: m.tid, text: m.text, theme: theme || "" });
    if (m && m.type === "sendMessage") out.sends.push({ id: m.id, text: m.text }); } catch (e) {} }));
  await page.goto(cfg.chat);
  await wait(page, () => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-file)").length >= 1, null, "the strip never showed the session");
  await page.click('#tabs .tab[data-id="' + cfg.web + '"]');
  await wait(page, () => !!document.querySelector('#content .file-uri-link[data-path="src/app.py"]'), null, "the transcript never linked src/app.py");
  return page;
}
// a real drag from the start of one element's box to the end of another's (the mouse's own selection)
const drag = async (page, fromSel, toSel) => {
  const a = await (await page.$(fromSel)).boundingBox(), b = await (await page.$(toSel || fromSel)).boundingBox();
  await page.mouse.move(a.x + 1, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 1, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
};
// a plain click on text that takes no focus: the caret leaves any box, as after reading
const clickText = async (page, sel) => { const b = await (await page.$(sel)).boundingBox(); await page.mouse.click(b.x + 2, b.y + b.height / 2); };
const where = (page) => page.evaluate(() => {
  const a = document.activeElement;
  return { tag: a ? a.tagName : null, cls: a ? String(a.className || a.id || "") : "", typing: !!a && (a.tagName === "TEXTAREA" || a.tagName === "INPUT" || a.isContentEditable),
           composer: (document.getElementById("composer-input") || {}).value || "" };
});
const bar = (page, scope) => page.evaluate((s) => {
  const root = document.querySelector(s + " .fileview-compose");
  if (!root) return null;
  return { chips: Array.from(root.querySelectorAll(".fv-compose-chips .composer-chip-label")).map((c) => c.textContent),
           value: (root.querySelector(".fv-compose-input") || {}).value || "", focused: document.activeElement === root.querySelector(".fv-compose-input"),
           composer: (document.getElementById("composer-input") || {}).value || "" };
}, scope);
const pop = (page) => page.evaluate(() => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const box = p.querySelector(".cmt-input"), strip = p.querySelector(".cmt-staged");
  return { mode: p.dataset.mode, value: box ? box.value : null, focused: document.activeElement === box,
           chips: Array.from(p.querySelectorAll(".cmt-chips .composer-chip-label")).map((c) => c.textContent),
           staged: strip && strip.style.display !== "none" ? Array.from(strip.querySelectorAll(".staged-chip")).map((c) => c.textContent) : [],
           label: strip ? ((strip.querySelector(".staged-lbl") || {}).textContent || "") : "",
           composer: (document.getElementById("composer-input") || {}).value || "",
           composerChips: document.querySelectorAll("#composer-chips .composer-chip").length };
});
const openThread = async (page) => {
  await wait(page, (tid) => !!document.querySelector('mark.cmt-hl[data-tid="' + tid + '"]'), cfg.tid, "the thread's highlight never landed", 60000);
  await page.click('mark.cmt-hl[data-tid="' + cfg.tid + '"]');
  await wait(page, () => { const p = document.getElementById("cmt-pop"); return !!p && p.dataset.mode === "thread"; }, null, "the thread never opened");
  await wait(page, (u) => !!document.querySelector('#cmt-pop .cmt-msgs .turn[data-uuid="' + u + '"] .md p'), cfg.r2, "the thread's messages never rendered", 60000);
};
const msgP = (u) => '#cmt-pop .cmt-msgs .turn[data-uuid="' + u + '"] .md p';

async function run() {
const page = await open(null);

// ── the file viewer, the modal ──
await page.click('#content .file-uri-link[data-path="src/app.py"]:visible');
await wait(page, () => !!document.querySelector("#romp-fileview .fileview-compose") && !document.querySelector("#romp-fileview .fileview-load"), null, "the viewer never showed its bar");
await drag(page, "#romp-fileview code.hljs .fv-cl:nth-child(1) .fv-ct", "#romp-fileview code.hljs .fv-cl:nth-child(2) .fv-ct");
await wait(page, () => document.querySelectorAll("#romp-fileview .fv-compose-chips .composer-chip").length === 1, null, "the drag never made a chip");
out.viewerFocusBefore = await where(page);
// dictation: the operating system's insertion, with nothing editable focused (the page sees a textInput at the body)
const cdp = await page.context().newCDPSession(page);
await page.evaluate(() => { window.__ti = []; window.addEventListener("textInput", (e) => window.__ti.push((e.target && e.target.tagName) || ""), true); });
await cdp.send("Input.insertText", { text: "dictated words" });
await page.waitForTimeout(150);
out.viewerDictated = await bar(page, "#romp-fileview");
out.viewerTextInputAt = await page.evaluate(() => window.__ti.slice());
// a paste: a real Ctrl+V of the clipboard, the caret back outside the bar first
await clickText(page, "#romp-fileview code.hljs .fv-cl:nth-child(6) .fv-ct");
out.viewerFocusBeforePaste = await where(page);
await page.evaluate(() => navigator.clipboard.writeText(" and pasted"));
await page.keyboard.press("Control+V");
await page.waitForTimeout(150);
out.viewerPasted = await bar(page, "#romp-fileview");
// typed keys, the caret outside again
await clickText(page, "#romp-fileview code.hljs .fv-cl:nth-child(6) .fv-ct");
out.viewerFocusBeforeKeys = await where(page);
await page.keyboard.type(", typed");   // a printable first key: Space from the bare area stays a scroll key
await page.waitForTimeout(100);
out.viewerTyped = await bar(page, "#romp-fileview");
await page.click("#romp-fileview .fileview-close");
await wait(page, () => !document.getElementById("romp-fileview"), null, "the viewer never closed");

// ── the file viewer as a FILE TAB ──
await page.click('#content .file-uri-link[data-path="docs/guide.md"]:visible');
await wait(page, () => !!document.querySelector("#romp-fileview .fileview-compose") && !document.querySelector("#romp-fileview .fileview-load"), null, "the guide never opened");
await page.click("#romp-fileview .fileview-popout");
await wait(page, () => document.body.classList.contains("filetab-mode") && !!document.querySelector("#filetab-host .fileview-compose") && !!document.querySelector("#filetab-host .fileview-md h2 + p"), null, "the file tab never showed its bar");
await clickText(page, "#filetab-host .fileview-md h2 + p");
out.tabFocusBefore = await where(page);
await cdp.send("Input.insertText", { text: "tab words" });
await page.waitForTimeout(150);
out.tabDictated = await bar(page, "#filetab-host");
await page.click('#tabs .tab[data-id="' + cfg.web + '"]');
await wait(page, () => !document.body.classList.contains("filetab-mode"), null, "the session never came back");

// ── the comment thread ──
await openThread(page);
out.threadOpened = { ...(await pop(page)), where: await where(page) };
await page.keyboard.type("x");                                    // a stray keystroke, the caret outside the popover
await page.waitForTimeout(100);
out.threadStray = await pop(page);
await clickText(page, msgP(cfg.r1));                              // the caret out again, no selection left
await cdp.send("Input.insertText", { text: "y" });                // a dictated word
await page.waitForTimeout(100);
out.threadDictated = await pop(page);
await page.focus("#cmt-pop .cmt-input");
await page.keyboard.press("Control+A");
await page.keyboard.press("Backspace");
// select → type → Ctrl+Enter, twice
await drag(page, msgP(cfg.r1));
await wait(page, () => document.querySelectorAll("#cmt-pop .cmt-chips .composer-chip").length === 1, null, "the thread's selection never made a chip");
out.threadChip = { ...(await pop(page)), where: await where(page) };
await page.keyboard.type(cfg.n1);
out.threadNote1 = await pop(page);
await page.keyboard.press("Control+Enter");
await wait(page, () => { const s = document.querySelector("#cmt-pop .cmt-staged"); return !!s && s.querySelectorAll(".staged-chip").length === 1; }, null, "Ctrl+Enter never staged in the popover");
out.threadStaged1 = await pop(page);
await drag(page, msgP(cfg.r2));
await wait(page, () => document.querySelectorAll("#cmt-pop .cmt-chips .composer-chip").length === 1, null, "the second selection never made a chip");
await page.keyboard.type(cfg.n2);
await page.keyboard.press("Control+Enter");
await wait(page, () => { const s = document.querySelector("#cmt-pop .cmt-staged"); return !!s && s.querySelectorAll(".staged-chip").length === 2; }, null, "the second Ctrl+Enter never staged");
out.threadStaged2 = await pop(page);
out.repliesBeforeSend = out.replies.length;
await page.keyboard.press("Enter");                               // the empty box with a stack: send the stack
await wait(page, () => { const s = document.querySelector("#cmt-pop .cmt-staged"); return !!s && s.style.display === "none"; }, null, "Enter never released the stack");
await page.waitForTimeout(400);
out.threadSent = await pop(page);

// the screenshots: the popover with a staged item and a chip with words typed against it, dark and Yatharth light
if (cfg.shots) {
  fs.mkdirSync(cfg.shots, { recursive: true });
  for (const theme of ["dark", "light"]) {
    const p = await open(theme);
    await openThread(p);
    await drag(p, msgP(cfg.r1));
    await wait(p, () => document.querySelectorAll("#cmt-pop .cmt-chips .composer-chip").length === 1, null, "shots: no chip");
    await p.keyboard.type(cfg.n1);
    await p.keyboard.press("Control+Enter");
    await drag(p, msgP(cfg.r2));
    await wait(p, () => document.querySelectorAll("#cmt-pop .cmt-chips .composer-chip").length === 1, null, "shots: no second chip");
    await p.keyboard.type(cfg.n2);
    await p.waitForTimeout(250);
    await (await p.$("#cmt-pop")).screenshot({ path: cfg.shots + "/comment-stage-" + theme + ".png" });
    await p.context().close();
  }
}
}
await run().then(() => finish(null), (e) => finish("an action failed: " + String(e && e.message || e).split("\n")[0]));
"""


class ComposerParityServed(unittest.TestCase):
    maxDiff = None
    _run = None   # the one driver run every test reads: (result, failure)

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
        cls.lab = tempfile.mkdtemp(prefix="composer-parity-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)   # tests/dist_copy: skips the bundler's staging files
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        for d in ("docs", "src"):
            os.makedirs(os.path.join(cwd, d), exist_ok=True)
        Path(cwd, "src", "app.py").write_text(APP_PY)
        Path(cwd, "docs", "guide.md").write_text(GUIDE)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = NOW - 3600
        parent = [user(t0, "u1", None, "where does the web side of notes-api start?", WEB),
                  agent(t0 + 5, "a1", "u1", A1, WEB),
                  user(t0 + 60, "u2", "a1", "how long should the list responses be cached?", WEB),
                  agent(t0 + 65, "a2", "u2", A2, WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        # the thread on the second reply: a fork cut at a2 with two exchanges, its replies seen
        t = t0 + 300
        thread = [dict(r, sessionId=THREAD) for r in parent]
        thread += [user(t, "c1", "a2", "Is thirty seconds enough?", THREAD), agent(t + 20, "r1", "c1", P1, THREAD),
                   user(t + 60, "c2", "r1", "And the export stream?", THREAD), agent(t + 80, "r2", "c2", P2, THREAD)]
        Path(proj, THREAD + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
        Path(state, "sdk", THREAD + ".json").write_text(json.dumps(
            {"sid": THREAD, "name": "web-comment-1", "cwd": cwd, "lastSid": THREAD, "threadOf": WEB, "forkAt": "a2",
             "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))   # no forkOf: the fork has happened
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": [
            {"tid": THREAD, "sid": THREAD, "name": "web-comment-1", "anchorUuid": "a2", "cutUuid": "a2", "exact": PASSAGE,
             "status": "open", "createdT": t, "lastSeenT": NOW, "color": "#e8b220"}]}))
        cls.port, cls.token = _free_port(), "testtok-parity"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", ROMP_CLI_SCOPE="0",
                              PYTHONPATH=FAKE_SDK,   # the sessions run on the fake Agent SDK, so the thread's conversation is served
                              ROMP_FAKE_SDK_REPLY=FAKE_REPLY)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"),
                                      stderr=subprocess.STDOUT, env=env)
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

    def _result(self):
        cls = type(self)
        if cls._run is None:
            cls._run = cls._drive()
        r, why = cls._run
        if why == "skip":
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if why:
            self.fail(why)
        return r

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "web": WEB, "tid": THREAD,
                       "r1": "r1", "r2": "r2", "n1": N1, "n2": N2, "out": out, "nowMs": NOW * 1000,
                       "shots": os.environ.get("COMPOSER_PARITY_SHOTS") or ""}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            return None, "skip"
        klog = open(cls.klog).read()[-1500:]
        if p.returncode != 0:
            return None, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + klog
        result = json.loads(Path(out).read_text())
        if result.get("died"):
            return None, "driver stopped: %s\nso far: %s\nkernel:\n%s" % (result["died"], json.dumps(result)[-3000:], klog)
        return result, None

    # ── the file viewer ──
    def test_dictation_paste_and_keys_over_the_viewer_land_in_its_bar_not_the_message_box(self):
        r = self._result()
        self.assertFalse(r["viewerFocusBefore"]["typing"], "the selection leaves nothing editable focused: %r" % r["viewerFocusBefore"])
        self.assertEqual(r["viewerTextInputAt"], ["BODY"], "dictation reaches the page as one textInput at the body")
        d = r["viewerDictated"]
        self.assertEqual(d["value"], "dictated words", d)
        self.assertTrue(d["focused"], "the caret followed the words into the bar")
        self.assertEqual(d["composer"], "", "nothing landed in the chat's message box")
        self.assertFalse(r["viewerFocusBeforePaste"]["typing"], r["viewerFocusBeforePaste"])
        self.assertEqual(r["viewerPasted"]["value"], "dictated words and pasted", r["viewerPasted"])
        self.assertFalse(r["viewerFocusBeforeKeys"]["typing"], r["viewerFocusBeforeKeys"])
        t = r["viewerTyped"]
        self.assertEqual(t["value"], "dictated words and pasted, typed", t)
        self.assertEqual(t["composer"], "")
        self.assertEqual(len(t["chips"]), 1, "the quote chip the selection made is still there")
        self.assertTrue(t["chips"][0].startswith("src/app.py:1-2 — def notes():"), t["chips"])

    def test_dictation_over_a_file_tab_lands_in_its_bar(self):
        r = self._result()
        self.assertFalse(r["tabFocusBefore"]["typing"], r["tabFocusBefore"])
        self.assertEqual(r["tabDictated"]["value"], "tab words", r["tabDictated"])
        self.assertEqual(r["tabDictated"]["composer"], "")

    # ── the comment thread ──
    def test_a_stray_keystroke_and_a_dictated_word_go_into_the_open_threads_reply_box(self):
        r = self._result()
        o = r["threadOpened"]
        self.assertEqual(o["mode"], "thread")
        self.assertFalse(o["focused"], "opened from its highlight, the caret is outside the popover: %r" % o)
        self.assertEqual(r["threadStray"]["value"], "x", r["threadStray"])
        self.assertTrue(r["threadStray"]["focused"])
        self.assertEqual(r["threadDictated"]["value"], "xy", r["threadDictated"])
        self.assertEqual(r["threadDictated"]["composer"], "", "never the chat's message box")

    def test_a_selection_in_the_threads_messages_seeds_a_chip_in_its_reply_box_only(self):
        r = self._result()
        c = r["threadChip"]
        self.assertEqual(c["chips"], [P1], c)
        self.assertEqual(c["composerChips"], 0, "the chat's message box got no chip")
        self.assertEqual(r["threadNote1"]["value"], N1, "words typed after the selection, with no click, went into the reply box")
        self.assertEqual(r["threadNote1"]["chips"], [P1], "and the chip survived the caret moving in")

    def test_ctrl_enter_stages_in_the_popover_one_then_two(self):
        r = self._result()
        s1 = r["threadStaged1"]
        self.assertEqual(len(s1["staged"]), 1, s1)
        self.assertIn(P1, s1["staged"][0])
        self.assertIn(N1, s1["staged"][0])
        self.assertEqual((s1["value"], s1["chips"]), ("", []), "the box and its chips moved onto the staged item")
        self.assertTrue(s1["focused"], "the caret stays in the box for the next passage")
        self.assertTrue(s1["label"].startswith("1 staged"), s1["label"])
        s2 = r["threadStaged2"]
        self.assertEqual(len(s2["staged"]), 2, s2)
        self.assertIn(P2, s2["staged"][1])
        self.assertIn(N2, s2["staged"][1])
        self.assertEqual(r["repliesBeforeSend"], 0, "nothing sent while staging")

    def test_enter_sends_one_reply_carrying_both_quotes_and_notes(self):
        r = self._result()
        main = [x for x in r["replies"] if not x["theme"]]
        self.assertEqual(main, [{"id": WEB, "tid": THREAD, "theme": "", "text":
            LEAD + "\n> " + P1 + "\n\n" + N1 + "\n\n" + LEAD + "\n> " + P2 + "\n\n" + N2}],
            "one commentReply to the thread, through its parent, with the run in stage order")
        self.assertEqual(r["sends"], [], "nothing went through the chat's send")
        self.assertEqual(r["threadSent"]["staged"], [], "the stack released")

    def test_no_page_error(self):
        self.assertEqual(self._result()["errors"], [])


if __name__ == "__main__":
    unittest.main()
