#!/usr/bin/env python3
"""Pictures a message embeds as markdown (`![alt](url)`) stay inside their bubble and open the chat's lightbox (the user
2026-10-20, reading on a phone: pictures ran off the right edge, and only the ones romp drew from a file path zoomed).

A hermetic kernel serves a synthetic notes-api chat (session web on TESTHOST, placeholder sids). A second local HTTP
server plays an outside image host, so every picture loads in CI without the internet; two more are data: URLs. The chat:
  a1  a markdown picture 1800px wide from the outside host, a mention of docs/figure.png (romp's own path preview), a
      sixteen-column table, a raw <pre> line 600 characters long and a fenced block with a long line;
  a2  a 20px-tall badge, a picture inside a link, and a 240px data: picture;
  an Agent launch whose subagent transcript embeds a picture and mentions docs/figure.png;
  an open comment thread whose reply embeds two pictures.
The driver, at a desktop width and at a phone width, in the dark theme and the light one, reads:
  every wide thing's box inside the reply's bubble, the table and the raw <pre> scrolling inside it, the fence wrapping,
  and nothing scrolling the page sideways;
  a click on the markdown picture opening the lightbox on THAT picture, its sequence holding the path preview too in the
  order the chat shows them (arrows both ways, and from the path preview's side), the outside picture's control an
  "Open original" link to a new tab, the data: picture's a download, the pinch stage's double-tap zoom on a URL picture;
  the linked picture following its link and the badge staying put (neither wears the zoom cursor);
  the subagent viewer's picture stepping to its own path preview, and the comment popover's picture opening the
  lightbox with the popover still there when the lightbox closes.

MDLB_DIST=<dir> serves another tree's UI bundle (the red run's before); MDLB_SHOTS=<dir> writes the reply at both widths
in both themes and the open lightbox; MDLB_DUMP=<path> writes the whole measurement. Skips LOUDLY without the extension
deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure).
Synthetic throughout: invented prose, run-time PNGs, no recorded data.
"""
import base64
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)
from test_comment_relay_mark_served import user, agent, iso, _free_port   # noqa: E402  the same synthetic records

WEB = "aaaaaaaa-1111-2222-3333-4d1a6e1b0c55"
THREAD = "bbbbbbbb-1111-2222-3333-4d1a6e1b0c55"
AGENT_ID = "a0123456789abcdef"                             # the shape the kernel's agent-id pattern admits
PASSAGE = "Cache the notes-api list responses for thirty seconds, keyed by the query string."


def _png(w, h, rgb):
    """An opaque PNG, assembled at run time (no binary fixture in the repo)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def _data_url(png):
    return "data:image/png;base64," + base64.b64encode(png).decode()


HOSTED = {"/wide.png": _png(1800, 450, (40, 120, 200)), "/card.png": _png(300, 200, (200, 90, 40)),
          "/hit.png": _png(280, 180, (60, 160, 90))}


class _Host(BaseHTTPRequestHandler):
    """The outside image host: three pictures and a page, nothing else."""
    def do_GET(self):
        path = self.path.split("?")[0]
        if path in HOSTED:
            body, ctype = HOSTED[path], "image/png"
        elif path == "/page":
            body, ctype = b"<!doctype html><title>notes-api dashboard</title><p>dashboard</p>", "text/html"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _reply_a1(host):
    cols = ["region", "p50", "p75", "p90", "p95", "p99", "p99.9", "max", "rps", "errors", "retries", "timeouts",
            "cache hits", "cache misses", "queue depth", "cpu"]
    rows = [["us-east-1", "12ms", "15ms", "21ms", "28ms", "44ms", "90ms", "212ms", "1840", "0", "3", "0", "91%", "9%", "4", "61%"],
            ["eu-west-1", "14ms", "18ms", "25ms", "31ms", "52ms", "110ms", "260ms", "1210", "1", "5", "0", "88%", "12%", "6", "57%"]]
    table = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n" + "".join("| " + " | ".join(r) + " |\n" for r in rows)
    line = "  ".join("GET /notes?limit=50&page=%d 200 %dms" % (k, 10 + k) for k in range(16))
    return ("The load test finished. Throughput over the run:\n\n"
            "![throughput over the run](%s/wide.png)\n\n"
            "The latency split per region is in docs/figure.png.\n\n%s\n"
            "<pre>%s</pre>\n\n"
            "```text\n%s\n```\n") % (host, table, line, line)


def _reply_a2(host, badge, swatch):
    return ("Build ![build passing](%s) is green. The dashboard card links to the live board: "
            "[![dashboard card](%s/card.png)](%s/page). The colour key: ![colour key](%s)") % (badge, host, host, swatch)


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
const WIDE = 'img[alt="throughput over the run"]';

const open = async (theme, phone) => {
  const ctx = await browser.newContext(phone
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
    : { viewport: { width: 1100, height: 800 } });
  await ctx.addInitScript(({ t }) => {
    try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {}
    window.__opened = [];
    window.open = (u, target, feats) => { window.__opened.push([String(u), String(target || ""), String(feats || "")]); return null; };
  }, { t: THEMES[theme] });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  await page.goto(cfg.chat);
  await page.waitForSelector(WIDE, { timeout: 60000 });
  await page.waitForFunction(() => Array.from(document.querySelectorAll("#content img")).filter((i) => i.closest(".md")).every((i) => i.complete),
    null, { timeout: 30000 }).catch(() => {});
  await page.waitForFunction((sel) => { const i = document.querySelector(sel); return !!(i && i.complete && i.naturalWidth > 0); }, WIDE, { timeout: 30000 });
  await page.waitForFunction(() => { const i = document.querySelector(".path-full-img"); return !!(i && i.complete && i.naturalWidth > 0); }, null, { timeout: 30000 });
  await page.waitForTimeout(250);
  return { ctx, page, errs };
};

// the reply's bubble and everything wide inside it, plus the page's own sideways overflow
const measure = (page) => page.evaluate((sel) => {
  const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); const q = (v) => Math.round(v * 10) / 10; return { left: q(b.left), right: q(b.right), top: q(b.top), w: q(b.width), h: q(b.height) }; };
  const img = document.querySelector(sel);
  const body = img.closest(".assistant");
  body.scrollIntoView({ block: "start" });
  const box = (e) => e ? { rect: r(e), scrollW: e.scrollWidth, clientW: e.clientWidth, overflowX: getComputedStyle(e).overflowX, display: getComputedStyle(e).display } : null;
  const pres = Array.from(body.querySelectorAll("pre"));
  const content = document.getElementById("content");
  const se = document.scrollingElement;
  const cur = (q) => { const e = document.querySelector(q); return e ? getComputedStyle(e).cursor : null; };
  return {
    body: r(body), img: r(img), imgNatural: img.naturalWidth,
    table: box(body.querySelector("table")),
    rawPre: box(pres.find((p) => !p.querySelector("code"))), fence: box(pres.find((p) => !!p.querySelector("code"))),
    page: { docScrollW: se.scrollWidth, docClientW: se.clientWidth, contentScrollW: content.scrollWidth, contentClientW: content.clientWidth },
    cursor: { wide: cur(sel), badge: cur('img[alt="build passing"]'), card: cur('img[alt="dashboard card"]'), swatch: cur('img[alt="colour key"]'), path: cur(".path-full-img") },
    bodyClass: document.body.className,
  };
}, WIDE);

const readLB = (page) => page.evaluate(() => {
  const wrap = document.getElementById("romp-lightbox");
  if (!wrap) return null;
  const img = wrap.querySelector(".romp-lightbox-img");
  const cue = wrap.querySelector(".romp-lightbox-cue");
  const ctl = wrap.querySelector(".romp-lightbox-dl");
  return { src: img ? img.src : null, natural: img ? img.naturalWidth : 0, complete: img ? img.complete : false, alt: img ? img.alt : null,
           transform: img ? img.style.transform : "", cue: cue ? cue.textContent : null,
           dir: (wrap.querySelector(".fileview-dir") || {}).textContent, base: (wrap.querySelector(".fileview-base") || {}).textContent,
           name: (wrap.querySelector(".fileview-name") || {}).title,
           ctl: ctl ? { tag: ctl.tagName.toLowerCase(), aria: ctl.getAttribute("aria-label"), title: ctl.title, href: ctl.getAttribute("href"),
                        target: ctl.getAttribute("target"), rel: ctl.getAttribute("rel"), download: ctl.getAttribute("download"),
                        svg: ctl.innerHTML.includes("<svg") } : null };
});
const settle = async (page) => {
  const up = await page.waitForSelector("#romp-lightbox", { timeout: 2500 }).then(() => true).catch(() => false);
  if (!up) return null;
  await page.waitForFunction(() => { const i = document.querySelector("#romp-lightbox .romp-lightbox-img"); return !!(i && i.complete && i.naturalWidth > 0); }, null, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(120);
  return readLB(page);
};
const press = async (page, key) => { await page.keyboard.press(key); return settle(page); };
const lbGone = (page) => page.evaluate(() => !document.getElementById("romp-lightbox"));
const shot = async (page, name, clipSel) => {
  if (!cfg.shots) return;
  if (clipSel) {
    const c = await page.evaluate((s) => { const e = document.querySelector(s); const b = e.getBoundingClientRect();
      return { x: 0, y: Math.max(0, b.top - 12), width: window.innerWidth, height: Math.min(window.innerHeight - Math.max(0, b.top - 12), b.height + 24) }; }, clipSel);
    await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip: c });
  } else await page.screenshot({ path: cfg.shots + "/" + name + ".png" });
};

const out = { widths: {}, errs: {} };

// ── desktop, dark: the widths, then the lightbox's whole contract ──
{
  const { ctx, page, errs } = await open("dark", false);
  out.widths.desktopDark = await measure(page);
  await page.waitForTimeout(100);
  await shot(page, "desktop-dark-reply", ".turn-assistant:has(" + WIDE + ")");
  // the markdown picture opens the lightbox on itself (each leg below is caught on its own, so a page where the lightbox
  // never opens still reports every measurement it can: the red run's before)
  const leg = async (name, fn) => { try { await fn(); } catch (e) { out[name + "Err"] = String(e).slice(0, 400); await page.keyboard.press("Escape").catch(() => {}); } };
  await leg("wide", async () => {
    await page.click(WIDE);
    out.wideOpen = await settle(page);
    await shot(page, "desktop-dark-lightbox");
    if (!out.wideOpen) return;
    out.stepRight1 = await press(page, "ArrowRight");
    out.stepRight2 = await press(page, "ArrowRight");
    out.stepRight3 = await press(page, "ArrowRight");     // the end ENDS: no wrap
    out.stepLeft1 = await press(page, "ArrowLeft");
    out.stepLeft2 = await press(page, "ArrowLeft");
    // "Open original": a new tab, the opener severed, the lightbox still up
    await page.evaluate(() => { window.__opened = []; });
    await page.click("#romp-lightbox .romp-lightbox-dl", { timeout: 5000 });
    await page.waitForTimeout(150);
    out.openOriginal = { opened: await page.evaluate(() => window.__opened), stillOpen: !(await lbGone(page)) };
    // the pinch stage is the URL picture's too: a double-tap toggles the zoom around the tap
    const b = await page.evaluate(() => { const r = document.querySelector("#romp-lightbox .romp-lightbox-img").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await page.mouse.dblclick(b.x, b.y);
    await page.waitForTimeout(120);
    out.zoomed = await readLB(page);
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
    out.escapeGone = await lbGone(page);
  });
  // the path preview's own click sees the same sequence
  await leg("path", async () => {
    await page.click(".turn-assistant:has(" + WIDE + ") .path-full-img");
    out.pathOpen = await settle(page);
    out.pathLeft = await press(page, "ArrowLeft");
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  // the data: picture: a download, never a tab (a browser refuses to open a data: URL as a page)
  await leg("swatch", async () => {
    await page.click('img[alt="colour key"]');
    out.swatchOpen = await settle(page);
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  // the linked picture follows its link; the badge stays put
  await leg("linked", async () => {
    await page.evaluate(() => { window.__opened = []; });
    await page.click('img[alt="dashboard card"]');
    await page.waitForTimeout(250);
    out.linked = { lightbox: !(await lbGone(page)), opened: await page.evaluate(() => window.__opened) };
    await page.keyboard.press("Escape"); await page.waitForTimeout(80);
    await page.click('img[alt="build passing"]');
    await page.waitForTimeout(250);
    out.badge = { lightbox: !(await lbGone(page)) };
    await page.keyboard.press("Escape"); await page.waitForTimeout(80);
  });

  // ── the subagent viewer: its own transcript is the sequence, its files resolve in the parent's folder ──
  await page.evaluate(({ sid, agent }) => {
    const b = document.createElement("span"); b.dataset.act = "openSubagent"; b.dataset.agent = agent; b.dataset.sid = sid;
    document.body.appendChild(b); b.click(); b.remove();
  }, { sid: cfg.sid, agent: cfg.agentId });
  const SUB = '.thread[data-session*="/agent/"] img[alt="cache hit rate"]';
  try {
    await page.waitForFunction((s) => { const i = document.querySelector(s); return !!(i && i.complete && i.naturalWidth > 0 && i.getBoundingClientRect().width > 0); }, SUB, { timeout: 30000 });
    await page.waitForTimeout(250);
    await page.click(SUB);
    out.subOpen = await settle(page);
    out.subRight = await press(page, "ArrowRight");
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  } catch (e) { out.subErr = String(e).slice(0, 400); }
  // back to the parent chat (the peek closes on the activation)
  await page.evaluate((sid) => {
    const back = document.querySelector('[data-act="subParent"][data-sid="' + sid + '"]') || document.querySelector('#tabs [data-sid="' + sid + '"]');
    if (back) back.click();
  }, cfg.sid);
  await page.waitForTimeout(400);

  // ── the comment popover: the same renderer, the same lightbox, and the popover outlives it ──
  try {
    await page.waitForSelector('mark.cmt-hl[data-tid="' + cfg.tid + '"]', { timeout: 30000 });
    await page.click('mark.cmt-hl[data-tid="' + cfg.tid + '"]');
    const POP = '#cmt-pop img[alt="burst chart"]';
    await page.waitForFunction((s) => { const i = document.querySelector(s); return !!(i && i.complete && i.naturalWidth > 0); }, POP, { timeout: 30000 });
    await page.waitForTimeout(250);
    out.popImg = await page.evaluate((s) => { const i = document.querySelector(s); const m = i.closest(".md, .cmt-msg"); const a = i.getBoundingClientRect(), b = m.getBoundingClientRect();
      return { img: { left: a.left, right: a.right }, body: { left: b.left, right: b.right }, cursor: getComputedStyle(i).cursor }; }, POP);
    await page.click(POP);
    out.popOpen = await settle(page);
    out.popRight = await press(page, "ArrowRight");
    await page.mouse.click(4, 4);                       // the backdrop: the lightbox closes, the popover stays
    await page.waitForTimeout(200);
    out.popAfterBackdrop = { lightbox: !(await lbGone(page)), pop: await page.evaluate(() => !!document.getElementById("cmt-pop")) };
    await page.click(POP);
    await settle(page);
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    out.popAfterEscape = { lightbox: !(await lbGone(page)), pop: await page.evaluate(() => !!document.getElementById("cmt-pop")) };
  } catch (e) { out.popErr = String(e).slice(0, 400); }
  out.errs.desktopDark = errs;
  await ctx.close();
}

// ── the phone, in both themes: the widths, and a tap that opens the lightbox ──
for (const theme of ["dark", "light"]) {
  const { ctx, page, errs } = await open(theme, true);
  const key = "phone" + theme[0].toUpperCase() + theme.slice(1);
  out.widths[key] = await measure(page);
  await page.waitForTimeout(100);
  await shot(page, "phone-" + theme + "-reply", ".turn-assistant:has(" + WIDE + ")");
  await page.evaluate((s) => document.querySelector(s).closest(".assistant").querySelector("table").scrollIntoView({ block: "center" }), WIDE);
  await page.waitForTimeout(100);
  await shot(page, "phone-" + theme + "-table");
  await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: "center" }), WIDE);
  await page.tap(WIDE);
  out[key + "Tap"] = await settle(page);
  await shot(page, "phone-" + theme + "-lightbox");
  out.errs[key] = errs;
  await ctx.close();
}

fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedMarkdownImageLightbox(unittest.TestCase):
    maxDiff = None
    result = None

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
        cls.lab = tempfile.mkdtemp(prefix="md-image-lightbox-")
        before = os.environ.get("MDLB_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        # the outside image host
        cls.host = ThreadingHTTPServer(("127.0.0.1", 0), _Host)
        threading.Thread(target=cls.host.serve_forever, daemon=True).start()
        cls.origin2 = "http://127.0.0.1:%d" % cls.host.server_address[1]
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        os.makedirs(os.path.join(cwd, "docs"), exist_ok=True)
        Path(cwd, "docs", "figure.png").write_bytes(_png(320, 200, (84, 178, 4)))
        cls.badge = _data_url(_png(88, 20, (60, 160, 60)))
        cls.swatch = _data_url(_png(240, 140, (150, 60, 170)))
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        h = cls.origin2
        agent_launch = {"type": "assistant", "timestamp": iso(t0 + 121), "uuid": "a3", "parentUuid": "u3", "sessionId": WEB,
                        "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use", "content": [
                            {"type": "tool_use", "id": "toolu_agent1", "name": "Agent", "input": {"prompt": "plot the cache hit rate"}}]}}
        agent_result = {"type": "user", "timestamp": iso(t0 + 180), "uuid": "u4", "parentUuid": "a3", "sessionId": WEB,
                        "toolUseResult": {"agentId": AGENT_ID, "status": "completed"},
                        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_agent1", "content": "plotted"}]}}
        parent = [user(t0, "u0", None, "how long should the notes-api cache its list responses?", WEB),
                  agent(t0 + 5, "a0", "u0", PASSAGE + " A longer window starts serving stale titles after an edit.", WEB),
                  user(t0 + 30, "u1", "a0", "how did the notes-api load test go?", WEB),
                  agent(t0 + 35, "a1", "u1", _reply_a1(h), WEB),
                  user(t0 + 60, "u2", "a1", "and the build badge?", WEB),
                  agent(t0 + 65, "a2", "u2", _reply_a2(h, cls.badge, cls.swatch), WEB),
                  user(t0 + 120, "u3", "a2", "check the cache hit rate in a subagent", WEB),
                  agent_launch, agent_result,
                  agent(t0 + 185, "a4", "u4", "The subagent plotted the hit rate.", WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in parent))
        sub = Path(proj, WEB, "subagents")
        sub.mkdir(parents=True, exist_ok=True)
        sub_recs = [{"type": "user", "timestamp": iso(t0 + 122), "uuid": "s0", "parentUuid": None,
                     "message": {"role": "user", "content": "plot the cache hit rate"}},
                    {"type": "assistant", "timestamp": iso(t0 + 170), "uuid": "r0", "parentUuid": "s0",
                     "message": {"role": "assistant", "stop_reason": "end_turn", "content": [{"type": "text", "text":
                         "The hit rate over the run:\n\n![cache hit rate](%s/hit.png)\n\nThe raw counts are in docs/figure.png." % h}]}}]
        agent_file = sub / ("agent-%s.jsonl" % AGENT_ID)
        agent_file.write_text("".join(json.dumps(r) + "\n" for r in sub_recs))
        os.utime(agent_file, (t0 + 171, t0 + 171))
        # the comment thread: a fork cut at the passage's reply, then one exchange whose reply embeds a picture
        t = t0 + 300
        thread = [dict(r, sessionId=THREAD) for r in parent[:2]]
        thread += [user(t, "c1", "a0", "Is thirty seconds enough for the list page's burst?", THREAD),
                   agent(t + 20, "r1", "c1", "The burst fits inside it:\n\n![burst chart](%s/card.png?v=thread)\n\n"
                         "and the curve after it settles:\n\n![settled curve](%s/hit.png?v=thread)" % (h, h), THREAD)]
        Path(proj, THREAD + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in thread))
        Path(state, "sdk", THREAD + ".json").write_text(json.dumps(
            {"sid": THREAD, "name": "web-comment-1", "cwd": cwd, "lastSid": THREAD, "threadOf": WEB, "forkAt": "a0",
             "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": [
            {"tid": "tid-1", "sid": THREAD, "name": "web-comment-1", "anchorUuid": "a0", "cutUuid": "a0", "exact": PASSAGE,
             "status": "open", "createdT": t, "lastSeenT": int(time.time())}]}))
        cls.port, cls.token = _free_port(), "testtok-mdimglb"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
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
        host = getattr(cls, "host", None)
        if host:
            host.shutdown()
            host.server_close()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _run(cls):
        if cls.result is not None:
            if isinstance(cls.result, BaseException):
                raise cls.result
            return cls.result
        try:
            cls.result = cls._drive()
        except BaseException as e:
            cls.result = e
            raise
        return cls.result

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "sid": WEB, "tid": "tid-1",
                       "agentId": AGENT_ID, "out": out, "shots": os.environ.get("MDLB_SHOTS", "")}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            def _dec(b):
                return "" if b is None else (b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b)
            raise AssertionError("driver ran past its 420 s cap:\n%s%s\nkernel:\n%s"
                                 % (_dec(e.stdout)[-3000:], _dec(e.stderr)[-3000:], open(cls.klog).read()[-2000:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("MDLB_DUMP"):
            Path(os.environ["MDLB_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def _inside(self, inner, outer, what, table):
        self.assertGreaterEqual(inner["left"], outer["left"] - 0.5, what + " starts inside the bubble" + table)
        self.assertLessEqual(inner["right"], outer["right"] + 0.5, what + " ends inside the bubble" + table)

    def test_nothing_in_the_reply_widens_its_bubble_or_the_page_at_either_width_in_either_theme(self):
        r = self._run()
        for key in ("desktopDark", "phoneDark", "phoneLight"):
            m = r["widths"][key]
            table = "\n  %s: %s" % (key, json.dumps(m))
            self.assertEqual(m["imgNatural"], 1800, "the picture loaded from the lab's outside host" + table)
            self._inside(m["img"], m["body"], "the 1800px markdown picture", table)
            self.assertLess(m["img"]["w"], 1800, "…capped, not cropped: the picture is scaled to the bubble" + table)
            self.assertAlmostEqual(m["img"]["h"] / m["img"]["w"], 450 / 1800, delta=0.01, msg="…keeping its ratio (height: auto)" + table)
            for name in ("table", "rawPre", "fence"):
                self.assertIsNotNone(m[name], name + " rendered" + table)
                self._inside(m[name]["rect"], m["body"], name, table)
            self.assertIn(m["table"]["overflowX"], ("auto", "scroll"), "the table scrolls inside the bubble past its width" + table)
            if key.startswith("phone"):
                self.assertGreater(m["table"]["scrollW"], m["table"]["clientW"], "on the phone the sixteen-column table is wider than the bubble, and scrolls" + table)
            self.assertGreater(m["rawPre"]["scrollW"], m["rawPre"]["clientW"], "the raw <pre> line is wider than the bubble…" + table)
            self.assertIn(m["rawPre"]["overflowX"], ("auto", "scroll"), "…and scrolls inside it rather than being cut off" + table)
            self.assertLessEqual(m["fence"]["scrollW"], m["fence"]["clientW"] + 1, "a fence WRAPS its long line (the chat's rule for code), so it never overflows" + table)
            self.assertLessEqual(m["page"]["docScrollW"], m["page"]["docClientW"], "the page never scrolls sideways" + table)
            self.assertLessEqual(m["page"]["contentScrollW"], m["page"]["contentClientW"], "nothing overflows the transcript to be cut off at its edge" + table)
        self.assertIn("theme-light", r["widths"]["phoneLight"]["bodyClass"].split(), "the light leg ran in the light theme")
        self.assertIn("chat-theme-yatharth", r["widths"]["phoneLight"]["bodyClass"].split())

    def test_a_markdown_picture_opens_the_lightbox_on_itself_with_an_open_original_link(self):
        r = self._run()
        lb = r["wideOpen"]
        self.assertIsNotNone(lb, "a click on the markdown picture opened the lightbox")
        self.assertEqual(lb["src"], self.origin2 + "/wide.png", "the lightbox shows THAT picture, from its own URL")
        self.assertEqual(lb["natural"], 1800)
        self.assertEqual((lb["dir"], lb["base"]), ("127.0.0.1:%d/" % self.host.server_address[1], "wide.png"),
                         "the title names the picture the way a path does: the place dimmed, the file name whole")
        c = lb["ctl"]
        self.assertEqual((c["tag"], c["aria"], c["href"], c["target"], c["rel"], c["download"], c["svg"]),
                         ("a", "Open original", self.origin2 + "/wide.png", "_blank", "noopener noreferrer", None, True),
                         "a URL picture's control opens the original in a new tab instead of downloading: " + json.dumps(c))
        o = r["openOriginal"]
        self.assertEqual(o["opened"], [[self.origin2 + "/wide.png", "_blank", "noopener,noreferrer"]], "the original opened in its own tab, the opener severed")
        self.assertTrue(o["stillOpen"], "…and the lightbox stayed up")
        self.assertTrue(r["escapeGone"], "Escape closes it")

    def test_the_arrows_visit_the_path_preview_and_both_markdown_pictures_in_the_chats_order(self):
        r = self._run()
        port = self.host.server_address[1]
        self.assertEqual(r["wideOpen"]["cue"], "1/3", "the sequence: the wide picture, the path preview, the colour key (the linked card and the badge are not stops)")
        s1, s2, s3 = r["stepRight1"], r["stepRight2"], r["stepRight3"]
        self.assertIn("/file?path=", s1["src"], "→ steps from the markdown picture to the path preview below it: " + json.dumps(s1))
        self.assertIn("figure.png", s1["src"])
        self.assertGreater(s1["natural"], 0, "the path preview's bytes loaded")
        self.assertEqual(s1["cue"], "2/3")
        self.assertEqual((s1["ctl"]["aria"], s1["ctl"]["download"], s1["ctl"]["target"]), ("Download", "figure.png", None),
                         "a path's control is the download again, its new-tab dress gone: " + json.dumps(s1["ctl"]))
        self.assertTrue(s2["src"].startswith("data:image/png;base64,"), "→ again: the colour key, a data: picture")
        self.assertEqual(s2["cue"], "3/3")
        self.assertEqual((s2["ctl"]["aria"], s2["ctl"]["target"]), ("Download", None), "a data: picture downloads (no browser opens one as a page)")
        self.assertTrue(s2["ctl"]["download"], "…under a file name")
        self.assertEqual(s3["src"], s2["src"], "the end ENDS: no wrap")
        self.assertIn("figure.png", r["stepLeft1"]["src"], "← walks back")
        self.assertEqual(r["stepLeft2"]["src"], "http://127.0.0.1:%d/wide.png" % port)
        self.assertEqual(r["pathOpen"]["cue"], "2/3", "the path preview's own click sees the same sequence")
        self.assertEqual(r["pathLeft"]["src"], "http://127.0.0.1:%d/wide.png" % port, "…and steps back to the markdown picture")
        self.assertEqual(r["swatchOpen"]["cue"], "3/3", "the data: picture opens on itself")

    def test_the_pinch_stage_serves_a_url_picture(self):
        z = self._run()["zoomed"]
        self.assertIsNotNone(z)
        self.assertIn("scale(2.5)", z["transform"], "a double-tap zooms the URL picture: the one lightbox, the one gesture wiring")

    def test_a_linked_picture_follows_its_link_and_a_badge_stays_put(self):
        r = self._run()
        self.assertFalse(r["linked"]["lightbox"], "a picture inside a link keeps the link's behaviour: no lightbox")
        self.assertEqual([o[0] for o in r["linked"]["opened"]], [self.origin2 + "/page"], "the link opened its page")
        self.assertFalse(r["badge"]["lightbox"], "a 20px badge is a glyph in the text, not a picture to zoom")
        cur = r["widths"]["desktopDark"]["cursor"]
        self.assertEqual(cur["wide"], "zoom-in", "the markdown picture wears the zoom cursor, as the path preview does: " + json.dumps(cur))
        self.assertEqual(cur["swatch"], "zoom-in")
        self.assertEqual(cur["path"], "zoom-in")
        self.assertNotEqual(cur["badge"], "zoom-in", "the badge does not")
        self.assertNotEqual(cur["card"], "zoom-in", "the linked picture wears its link's cursor")

    def test_the_subagent_viewer_steps_through_its_own_pictures(self):
        r = self._run()
        self.assertNotIn("subErr", r, r.get("subErr"))
        self.assertEqual(r["subOpen"]["src"], self.origin2 + "/hit.png")
        self.assertEqual(r["subOpen"]["cue"], "1/2", "the viewer's own transcript is the sequence: its picture and its path preview")
        self.assertIn("figure.png", r["subRight"]["src"])
        self.assertGreater(r["subRight"]["natural"], 0, "the path resolves in the parent's folder, as the viewer's preview does")

    def test_the_comment_popover_opens_the_lightbox_and_outlives_it(self):
        r = self._run()
        self.assertNotIn("popErr", r, r.get("popErr"))
        p = r["popImg"]
        self.assertLessEqual(p["img"]["right"], p["body"]["right"] + 0.5, "the popover's picture fits its message: " + json.dumps(p))
        self.assertEqual(p["cursor"], "zoom-in")
        self.assertEqual(r["popOpen"]["src"], self.origin2 + "/card.png?v=thread")
        self.assertEqual(r["popOpen"]["cue"], "1/2", "the thread's own messages are the sequence (the parent chat holds neither picture)")
        self.assertEqual(r["popRight"]["src"], self.origin2 + "/hit.png?v=thread")
        self.assertEqual(r["popAfterBackdrop"], {"lightbox": False, "pop": True}, "a backdrop tap closes the lightbox and leaves the popover open")
        self.assertEqual(r["popAfterEscape"], {"lightbox": False, "pop": True}, "Escape closes the lightbox alone")

    def test_a_tap_on_the_phone_opens_the_lightbox_in_both_themes(self):
        r = self._run()
        for key in ("phoneDarkTap", "phoneLightTap"):
            self.assertIsNotNone(r[key], key)
            self.assertEqual(r[key]["src"], self.origin2 + "/wide.png", key)
            self.assertGreater(r[key]["natural"], 0, key)

    def test_no_page_errors(self):
        r = self._run()
        for k, errs in r["errs"].items():
            self.assertEqual(errs, [], k)


if __name__ == "__main__":
    unittest.main()
