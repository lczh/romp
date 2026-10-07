#!/usr/bin/env python3
"""A relayed comment thread unfolds in the chat as a small transcript, on the real /chat page (the user 2026-10-06, who
found the relay's "Me:" / "Them:" lines hard to pick out among the messages' own headings and lists).

The kernel hands a thread's discussion back to its session as a TAGGED record (kernel _merge_body): the passage in
<passage>, one <message from="me"> or <message from="the other assistant"> per message inside <discussion>. The chat shows
the relay as a grey romp bubble folded to its one-line gist, and unfolded, through ui/webview/relay-view.ts, as the passage
quoted and each message its own block labelled You or Thread with its markdown rendered inside.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web) with one open thread on a passage of
the agent's reply, its discussion two exchanges of formatted text (a heading, lists, a code block). The parent session
runs on the fake Agent SDK, so the Relay's handoff is really delivered, written to the parent's transcript, and landed
by the kernel's real parse. The driver presses Relay the user's way, then in the dark theme and in the light theme
(the settings key `yatharth-light`) reads the landed bubble:
  folded, its gist still says it is a relay (the record's own lead line);
  unfolded, the passage is a quote, then the four messages in order as You / Thread / You / Thread blocks, each with
  its markdown rendered (the heading, the list, the code block), with no tag of the record shown anywhere;
  the blocks are painted: the You block in the theme's voice colour, a Thread block on the theme's wash.
With RELAY_SHOTS=<dir> the driver writes the unfolded bubble in both themes. Skips LOUDLY without the extension deps or
a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host TESTHOST)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
FAKE_SDK = os.path.join(HERE, "fixtures", "fake_agent_sdk")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)
from test_comment_relay_mark_served import user, agent, _free_port   # noqa: E402  the same synthetic records

WEB = "aaaaaaaa-1111-2222-3333-7e1a77e1a77e"
THREAD = "bbbbbbbb-1111-2222-3333-7e1a77e1a77e"
PASSAGE = "Cache the notes-api list responses for thirty seconds, keyed by the query string."
ASK = "Is thirty seconds enough? The list page fires a burst of calls on load."
REPLY = ("## Why thirty seconds\n\nA page load fires **four** list calls inside two seconds, so thirty seconds absorbs the "
         "burst with room to spare.\n\n- an edit invalidates the key, so staleness is bounded by the edit path\n"
         "- the key is the normalized query string\n\n```python\nLIST_CACHE_TTL = 30  # seconds\n```")
ASK2 = "And if two edits land inside one window?"
REPLY2 = ("The second edit invalidates again. The window only matters for reads that **no edit touched**:\n\n"
          "1. edit, then invalidate\n2. next read misses and refills")


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
const THEMES = { dark: "classic", light: "yatharth-light" };   // the settings store's key, read by theme.ts at boot

const openChat = async (theme) => {
  const ctx = await browser.newContext({ viewport: { width: 860, height: 1000 }, deviceScaleFactor: 2 });
  await ctx.addInitScript(({ t, sid }) => {
    try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {}
    window.__statuses = [];
    window.addEventListener("message", (e) => {
      const m = e.data; if (!m || m.type !== "comments" || m.id !== sid) return;
      window.__statuses.push((m.threads || []).map((th) => th.tid + ":" + th.status).join(","));
    }, true);
  }, { t: THEMES[theme], sid: cfg.sid });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  await page.goto(cfg.chat);
  await page.waitForSelector("#content .turn", { timeout: 60000 });
  return { ctx, page, errs };
};

// the landed relay: the grey romp bubble of a user-row turn (never the queued copy) whose text is the record's lead
const RELAY_SEL = ".turn.turn-user.romp > .romp-bubble";
const findRelay = (sel) => Array.from(document.querySelectorAll(sel)).find((b) => (b.textContent || "").includes("I took a side discussion"));

const readRelay = async (page, name) => {
  await page.waitForFunction((sel) => {
    const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
    return !!b;
  }, RELAY_SEL, { timeout: 60000 });
  const folded = await page.evaluate((sel) => {
    const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
    b.scrollIntoView({ block: "start" });
    const g = b.querySelector(".nudge-gist");
    return { expanded: b.classList.contains("expanded"), gist: g ? g.textContent : null, gistShown: g ? getComputedStyle(g).display !== "none" : false };
  }, RELAY_SEL);
  if (!folded.expanded) {
    const box = await page.evaluate((sel) => {
      const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
      const r = b.querySelector(".nudge-gist").getBoundingClientRect(); return { x: r.left + 20, y: r.top + r.height / 2 };
    }, RELAY_SEL);
    await page.mouse.click(box.x, box.y);
    await page.waitForFunction((sel) => {
      const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
      return b && b.classList.contains("expanded");
    }, RELAY_SEL, { timeout: 10000 });
  }
  await page.mouse.move(5, 5);
  await page.waitForTimeout(300);
  const open = await page.evaluate((sel) => {
    const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
    b.scrollIntoView({ block: "start" });
    const full = b.querySelector(".nudge-full");
    const cs = (el, p) => el ? getComputedStyle(el)[p] : null;
    const you = getComputedStyle(document.body).getPropertyValue("--you").trim();
    const probe = document.createElement("span"); probe.style.color = you; document.body.appendChild(probe);
    const youRgb = getComputedStyle(probe).color; probe.remove();
    return {
      shown: cs(full, "display"),
      kids: Array.from(full.children).map((c) => c.tagName.toLowerCase() + "." + c.className),
      passage: (full.querySelector("blockquote.relay-passage") || {}).textContent || null,
      msgs: Array.from(full.querySelectorAll(".relay-msg")).map((m) => ({
        cls: m.className, who: (m.querySelector(".relay-who") || {}).textContent, text: (m.querySelector(".relay-body") || {}).innerText,
        h2: !!m.querySelector(".relay-body h2"), ul: !!m.querySelector(".relay-body ul"), ol: !!m.querySelector(".relay-body ol"),
        pre: !!m.querySelector(".relay-body pre"), strong: !!m.querySelector(".relay-body strong"),
        bg: cs(m, "backgroundColor"), whoColor: cs(m.querySelector(".relay-who"), "color") })),
      notes: Array.from(full.querySelectorAll(".relay-note")).map((n) => n.textContent),
      text: full.innerText,
      youRgb,
      bodyClass: document.body.className,
    };
  }, RELAY_SEL);
  if (cfg.shots) {
    const r = await page.evaluate((sel) => {
      const b = Array.from(document.querySelectorAll(sel)).find((x) => (x.textContent || "").includes("I took a side discussion"));
      const t = b.closest(".turn").getBoundingClientRect(); return { y: Math.max(0, t.top - 12), h: t.height + 24 };
    }, RELAY_SEL);
    await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip: { x: 0, y: r.y, width: 860, height: Math.min(r.h, 1000 - r.y) } });
    await page.screenshot({ path: cfg.shots + "/" + name + "-page.png" });
  }
  return { folded, open };
};

const out = {};
{
  const { ctx, page, errs } = await openChat("dark");
  await page.waitForSelector(`mark.cmt-hl[data-tid="${cfg.tid}"]`, { timeout: 30000 });
  await page.click(`mark.cmt-hl[data-tid="${cfg.tid}"]`);
  await page.waitForSelector('#cmt-pop [data-act="cmtmerge"]', { timeout: 10000 });
  await page.click('#cmt-pop [data-act="cmtmerge"]');
  out.settled = await page.waitForFunction((tid) => (window.__statuses[window.__statuses.length - 1] || "").includes(tid + ":merged"), cfg.tid, { timeout: 30000 }).then(() => true).catch(() => false);
  out.statuses = await page.evaluate(() => window.__statuses);
  await page.keyboard.press("Escape");
  await page.evaluate(() => { const x = document.querySelector("#cmt-pop .cmt-x"); if (x) x.click(); });
  try { out.dark = await readRelay(page, "relay-dark"); }
  catch (e) { out.darkErr = String(e).slice(0, 400); out.turns = await page.evaluate(() => Array.from(document.querySelectorAll("#content .turn")).map((t) => t.className + " | " + (t.textContent || "").slice(0, 80))); }
  out.darkErrs = errs;
  await ctx.close();
}
{
  const { ctx, page, errs } = await openChat("light");
  try { out.light = await readRelay(page, "relay-yatharth-light"); }
  catch (e) { out.lightErr = String(e).slice(0, 400); }
  out.lightErrs = errs;
  await ctx.close();
}
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedRelayView(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-relay-view-")
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
        os.makedirs(cwd, exist_ok=True)
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        parent = [user(t0, "u1", None, "how long should the notes-api cache its list responses?", WEB),
                  agent(t0 + 5, "a1", "u1", PASSAGE + " A longer window starts serving stale titles after an edit.", WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        # the thread: a fork cut at the passage's reply (the copied history), then its two exchanges, all seen
        t = t0 + 300
        thread = [dict(r, sessionId=THREAD) for r in parent]
        thread += [user(t, "c1", "a1", ASK, THREAD), agent(t + 20, "r1", "c1", REPLY, THREAD),
                   user(t + 60, "c2", "r1", ASK2, THREAD), agent(t + 80, "r2", "c2", REPLY2, THREAD)]
        Path(proj, THREAD + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
        Path(state, "sdk", THREAD + ".json").write_text(json.dumps(
            {"sid": THREAD, "name": "web-comment-1", "cwd": cwd, "lastSid": THREAD, "threadOf": WEB, "forkAt": "a1",
             "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": [
            {"tid": "tid-1", "sid": THREAD, "name": "web-comment-1", "anchorUuid": "a1", "cutUuid": "a1", "exact": PASSAGE,
             "status": "open", "createdT": t, "lastSeenT": int(time.time())}]}))
        cls.port, cls.token = _free_port(), "testtok-relayview"
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

    def test_a_relay_unfolds_as_labelled_blocks_in_both_themes(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB, "tid": "tid-1",
                       "shots": os.environ.get("RELAY_SHOTS", "")}, f)
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
        self.assertTrue(r["settled"], "the relay settled the thread: %r\nkernel:\n%s" % (r["statuses"], open(self.klog).read()[-2000:]))

        for theme in ("dark", "light"):
            self.assertNotIn(theme + "Err", r, "%s: the relay bubble landed and unfolded: %r\nturns: %r\nkernel:\n%s"
                             % (theme, r.get(theme + "Err"), r.get("turns"), open(self.klog).read()[-2000:]))
            self.assertEqual(r[theme + "Errs"], [], "%s: no page error" % theme)
            folded, open_ = r[theme]["folded"], r[theme]["open"]
            if theme == "dark":
                # folded to its one line, which still says what it is: the record's own lead
                self.assertFalse(folded["expanded"], folded)
                self.assertTrue(folded["gistShown"], folded)
                self.assertTrue((folded["gist"] or "").lstrip("▸ ").startswith("I took a side discussion with another assistant"),
                                folded)
            else:
                classes = open_["bodyClass"].split()
                self.assertIn("theme-light", classes, open_["bodyClass"])
                self.assertIn("chat-theme-yatharth", classes, open_["bodyClass"])
            self.assertEqual(open_["shown"], "block", "%s: the unfolded text shows" % theme)
            # the small transcript: the lead, the passage quoted, the discussion, the closing ask
            self.assertEqual(open_["kids"], ["div.relay-lead", "blockquote.relay-passage", "div.relay-disc", "div.relay-close"],
                             theme)
            self.assertEqual((open_["passage"] or "").strip(), PASSAGE, theme)
            self.assertEqual([m["who"] for m in open_["msgs"]], ["You", "Thread", "You", "Thread"], theme)
            self.assertEqual([m["cls"] for m in open_["msgs"]],
                             ["relay-msg relay-you", "relay-msg relay-thread"] * 2, theme)
            self.assertEqual(open_["notes"], [], "%s: nothing was left out, so no note" % theme)
            ask, reply, ask2, reply2 = open_["msgs"]
            self.assertEqual(ask["text"].strip(), ASK, theme)
            self.assertTrue(reply["h2"] and reply["ul"] and reply["pre"] and reply["strong"],
                            "%s: the reply's heading, list, code and bold render inside its block: %r" % (theme, reply))
            self.assertIn("LIST_CACHE_TTL = 30", reply["text"], theme)
            self.assertTrue(reply2["ol"], "%s: the second reply's numbered list: %r" % (theme, reply2))
            self.assertEqual(ask2["text"].strip(), ASK2, theme)
            self.assertNotRegex(open_["text"], r"</?(message|discussion|passage)\b|Me: |Them: ",
                                "%s: no tag of the record, and no old prefix, is shown" % theme)
            # painted: the You block in the theme's voice colour, a Thread block on the theme's wash
            self.assertEqual(ask["whoColor"], open_["youRgb"], "%s: the You label in the voice colour" % theme)
            self.assertNotEqual(reply["whoColor"], open_["youRgb"], "%s: the Thread label in the dim ink" % theme)
            for m in (ask, reply):
                self.assertNotIn(m["bg"], ("rgba(0, 0, 0, 0)", "transparent"), "%s: each block is painted: %r" % (theme, m))
            self.assertNotEqual(ask["bg"], reply["bg"], "%s: You and Thread blocks read apart" % theme)


if __name__ == "__main__":
    unittest.main()
