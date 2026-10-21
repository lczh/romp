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
A second session, api, holds the edge cases a review of the first cut reproduced (2026-10-20), read on a desktop at a
device pixel ratio of 2: a paragraph that wraps a picture onto the line after a path's mention (the picture is the stop
before the figure), a picture with a 2x srcset (it opens on the candidate it shows, at its place in the arrows), a
picture drawn from a srcset alone (it opens on it, its "Open original" the picture, never the dashboard), a src whose
query is written with numeric character references (a stop the click finds, no phantom beside it), and a picture in a
<details>'s <summary> (its click opens the details; no zoom, no stop).
A third session, tests, holds one reply that puts the two kinds of picture side by side (2026-10-21, the user wanting a
markdown picture sized and dressed like the path preview): a mention of docs/figure.png, a trace 400x1600 and a heat map
1800x1200 from the outside host, a 20px badge in a sentence and a picture inside a link. Read at a desktop width and a
phone width in both themes: the two pictures within 320px and the bubble, their ratio kept, and their computed display,
radius, margins, frame, background and cursor those of the path preview beside them; the badge inline at its natural
size, and neither it nor the linked picture wearing the zoom cursor. The same session holds the solo rule's cases (the
review of #343, 2026-10-21): a picture with its caption on the next line, in the reply and in the user's own message (a
soft break and a <br>), keeps the box with the caption directly under it; two pictures on lines of their own each keep
it; a picture with words after it, or before it, on its own line stays inline. And the review of #349's (2026-10-21): a
caption of two lines joined by a hard break (two spaces, a backslash, a <br>) keeps both lines; two half-width pictures
in a raw HTML <p> sit side by side, unframed; a bold picture in the middle of a sentence stays in it. And the review of
#351's (2026-10-21): a raw <img> alone on its line with a caption on the next (at the message's root, and in a padded
<p align="center"> with an <em> caption) keeps the box with the caption under it; two raw <img> lines a blank line apart
each keep the box, stacked; two raw <img> lines at the root with no blank line between sit side by side, as in a <p>.

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
API = "cccccccc-1111-2222-3333-4d1a6e1b0c55"
TESTS = "dddddddd-1111-2222-3333-4d1a6e1b0c55"
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
          "/hit.png": _png(280, 180, (60, 160, 90)), "/tall.png": _png(400, 1600, (200, 100, 40)),
          "/big.png": _png(1800, 1200, (40, 120, 200))}


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


def _reply_b0(host):
    return ("The p95 split per region is in docs/figure.png and\n"
            "![soft wrap](%(h)s/hit.png?v=soft) shows the hourly curve.\n\n"
            "The dense card <img src=\"%(h)s/card.png?v=dense\" srcset=\"%(h)s/card.png?v=dense2 2x\" alt=\"dense card\"> "
            "and one drawn from a srcset alone <img srcset=\"%(h)s/card.png?v=srcset 1x\" alt=\"srcset only\">\n\n"
            "Its query written with references <img src=\"%(h)s/card.png?v=num&#38;w=2&#x26;h=3\" alt=\"numeric ref\">\n\n"
            "<details><summary><img src=\"%(h)s/hit.png?v=summary\" alt=\"fold picture\"> Raw counts</summary>\n\n"
            "The raw counts per region.\n\n</details>\n") % {"h": host}


def _reply_c0(host, badge):
    # the last two lines are the review of the shared box (2026-10-21): a picture in the middle of a sentence stays on its
    # line, and an <img height="20"> of a large picture, alone on its line, shows 20px tall, so it is a glyph, not a box
    return ("The latency split per region is in docs/figure.png.\n\n"
            "The trace of one slow request, top to bottom:\n\n"
            "![request trace](%(h)s/tall.png)\n\n"
            "The heat map of the whole run:\n\n"
            "![run heat map](%(h)s/big.png)\n\n"
            "Deploy ![deploy passing](%(b)s) is green, and the thumbnail links to the board: "
            "[![board thumbnail](%(h)s/big.png?v=linked)](%(h)s/page)\n\n"
            "Trace ![inline trace](%(h)s/tall.png?v=mid) here.\n\n"
            "<img height=\"20\" src=\"%(h)s/big.png?v=h20\" alt=\"squashed heat map\">\n\n"
            # the review of #343 (2026-10-21): a picture with its caption on the next line is ONE paragraph, and it keeps the
            # box with the caption under it; two pictures on lines of their own each keep it; a picture with words on its
            # own line, after it or before it, stays inline
            "![captioned heat map](%(h)s/big.png?v=cap)\n"
            "*Figure 1: requests per hour across the run.*\n\n"
            "![first panel](%(h)s/tall.png?v=p1)\n"
            "![second panel](%(h)s/big.png?v=p2)\n\n"
            "![leading trace](%(h)s/tall.png?v=lead) is the slowest request.\n\n"
            "The slowest request: ![trailing trace](%(h)s/tall.png?v=tail)\n\n"
            # the review of #349 (2026-10-21): a caption of two lines joined by a hard break (two spaces, a backslash or a
            # <br>: one <br> each) keeps its break; a raw HTML <p> of two half-width pictures, the README layout, keeps them
            # side by side; a bold picture in the middle of a sentence stays in it
            "![spaces caption](%(h)s/big.png?v=cap2s)\n"
            "Figure 3: requests per hour.  \n"
            "Source: the nightly load run.\n\n"
            "![backslash caption](%(h)s/big.png?v=cap2b)\n"
            "Figure 4: requests per hour.\\\n"
            "Source: the nightly load run.\n\n"
            "![br caption](%(h)s/big.png?v=cap2r)\n"
            "Figure 5: requests per hour.<br>\n"
            "Source: the nightly load run.\n\n"
            "<p>\n<img width=\"45%%\" src=\"%(h)s/big.png?v=sideL\" alt=\"side left\">\n"
            "<img width=\"45%%\" src=\"%(h)s/big.png?v=sideR\" alt=\"side right\">\n</p>\n\n"
            "The **![bold trace](%(h)s/tall.png?v=bold)** result is the slowest request.\n\n"
            # the review of #351 (2026-10-21): an agent's raw <img> alone on its line with a caption on the next (a raw HTML
            # block, loose at the message's root) keeps the box with the caption under it, and so does one in a padded
            # <p align="center"> with an <em> caption on the line under it; two raw <img> lines a blank line apart each keep
            # the box, stacked; two raw <img> lines with no blank line between, at the root, are a row as in a <p>
            "<img src=\"%(h)s/big.png?v=raw\" width=\"300\" alt=\"raw captioned\">\n"
            "Figure 6: requests per hour, from the raw picture.\n\n"
            "<p align=\"center\">\n  <img src=\"%(h)s/big.png?v=centre\" width=\"300\" alt=\"centred captioned\">\n"
            "  <em>Figure 7: the same run, centred.</em>\n</p>\n\n"
            "<img src=\"%(h)s/big.png?v=rawA\" width=\"240\" alt=\"raw first\">\n\n"
            "<img src=\"%(h)s/big.png?v=rawB\" width=\"240\" alt=\"raw second\">\n\n"
            "The two runs side by side:\n\n"
            "<img width=\"45%%\" src=\"%(h)s/big.png?v=rootL\" alt=\"root left\">\n"
            "<img width=\"45%%\" src=\"%(h)s/big.png?v=rootR\" alt=\"root right\">\n\n"
            "That is the whole run.\n") % {"h": host, "b": badge}


# the user's own message (breaks:true, so its line break is a <br>): a picture with a caption on the next line
USER_CAPTIONED = "here is the one from yesterday's run:\n![user heat map](%s/big.png?v=user)\nFigure 2: the same run, per region."


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

// o.dpr: a desktop at that device pixel ratio; o.hash: the page's #only= filter; o.ready: the picture that says the chat is up
const open = async (theme, phone, o = {}) => {
  const ctx = await browser.newContext(phone
    ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
    : { viewport: { width: 1100, height: 800 }, deviceScaleFactor: o.dpr || 1 });
  await ctx.addInitScript(({ t }) => {
    try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {}
    window.__opened = [];
    window.open = (u, target, feats) => { window.__opened.push([String(u), String(target || ""), String(feats || "")]); return null; };
  }, { t: THEMES[theme] });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  const ready = o.ready || WIDE;
  await page.goto(cfg.chat + (o.hash || ""));
  await page.waitForSelector(ready, { timeout: 60000 });
  await page.waitForFunction(() => Array.from(document.querySelectorAll("#content img")).filter((i) => i.closest(".md")).every((i) => i.complete),
    null, { timeout: 30000 }).catch(() => {});
  await page.waitForFunction((sel) => { const i = document.querySelector(sel); return !!(i && i.complete && i.naturalWidth > 0); }, ready, { timeout: 30000 });
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

// ── the second session (api) on a dense screen: the review's edge cases ──
{
  const SOFT = 'img[alt="soft wrap"]';
  const { ctx, page, errs } = await open("dark", false, { dpr: 2, hash: "#only=api", ready: SOFT });
  const leg = async (name, fn) => { try { await fn(); } catch (e) { out[name + "Err"] = String(e).slice(0, 400); await page.keyboard.press("Escape").catch(() => {}); } };
  out.api = {};
  out.api.cursor = await page.evaluate(() => { const cur = (q) => { const e = document.querySelector(q); return e ? getComputedStyle(e).cursor : null; };
    return { soft: cur('img[alt="soft wrap"]'), dense: cur('img[alt="dense card"]'), srcset: cur('img[alt="srcset only"]'), num: cur('img[alt="numeric ref"]'), fold: cur('img[alt="fold picture"]') }; });
  await leg("apiSoft", async () => {
    await page.click(SOFT);
    out.api.softOpen = await settle(page);
    out.api.softRight = await press(page, "ArrowRight");
    out.api.softRight2 = await press(page, "ArrowRight");
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  await leg("apiPath", async () => {
    await page.click(".turn-assistant:has(" + SOFT + ") .path-full-img");
    out.api.pathOpen = await settle(page);
    out.api.pathLeft = await press(page, "ArrowLeft");
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  await leg("apiDense", async () => {
    await page.click('img[alt="dense card"]');
    out.api.denseOpen = await settle(page);
    out.api.denseShown = await page.evaluate(() => document.querySelector('img[alt="dense card"]').currentSrc);
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  await leg("apiSrcset", async () => {
    await page.click('img[alt="srcset only"]');
    out.api.srcsetOpen = await settle(page);
    await page.evaluate(() => { window.__opened = []; });
    await page.click("#romp-lightbox .romp-lightbox-dl", { timeout: 5000 });
    await page.waitForTimeout(150);
    out.api.srcsetOriginal = await page.evaluate(() => window.__opened);
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  await leg("apiNum", async () => {
    await page.click('img[alt="numeric ref"]');
    out.api.numOpen = await settle(page);
    out.api.numShown = await page.evaluate(() => document.querySelector('img[alt="numeric ref"]').src);
    out.api.numLeft = await press(page, "ArrowLeft");
    await page.keyboard.press("Escape"); await page.waitForTimeout(120);
  });
  await leg("apiFold", async () => {
    const shut = await page.evaluate(() => !document.querySelector('img[alt="fold picture"]').closest("details").open);
    await page.click('img[alt="fold picture"]');
    await page.waitForTimeout(250);
    out.api.fold = { shutBefore: shut, lightbox: !(await lbGone(page)),
      open: await page.evaluate(() => document.querySelector('img[alt="fold picture"]').closest("details").open) };
    await page.keyboard.press("Escape"); await page.waitForTimeout(80);
  });
  out.errs.apiDense = errs;
  await ctx.close();
}

// ── the third session (tests): a markdown picture in the path preview's box, at both widths in both themes ──
out.box = {};
const TALL = 'img[alt="request trace"]';
for (const theme of ["dark", "light"]) for (const phone of [false, true]) {
  const key = (phone ? "phone" : "desktop") + theme[0].toUpperCase() + theme.slice(1);
  try {
    const { ctx, page, errs } = await open(theme, phone, { hash: "#only=tests", ready: TALL });
    await page.waitForFunction(() => ['img[alt="run heat map"]', 'img[alt="board thumbnail"]', 'img[alt="deploy passing"]', 'img[alt="inline trace"]', 'img[alt="squashed heat map"]',
      'img[alt="captioned heat map"]', 'img[alt="first panel"]', 'img[alt="second panel"]', 'img[alt="leading trace"]', 'img[alt="trailing trace"]', 'img[alt="user heat map"]',
      'img[alt="spaces caption"]', 'img[alt="backslash caption"]', 'img[alt="br caption"]', 'img[alt="side left"]', 'img[alt="side right"]', 'img[alt="bold trace"]',
      'img[alt="raw captioned"]', 'img[alt="centred captioned"]', 'img[alt="raw first"]', 'img[alt="raw second"]', 'img[alt="root left"]', 'img[alt="root right"]']
      .every((s) => { const i = document.querySelector(s); return !!(i && i.complete && i.naturalWidth > 0); }), null, { timeout: 30000 });
    await page.waitForTimeout(150);
    out.box[key] = await page.evaluate((sel) => {
      const tall = document.querySelector(sel);
      const body = tall.closest(".assistant");
      body.scrollIntoView({ block: "start" });
      const one = (e) => {
        if (!e) return null;
        const b = e.getBoundingClientRect(), cs = getComputedStyle(e);
        const bw = (side) => parseFloat(cs["border" + side + "Width"]) || 0;
        return { left: b.left, right: b.right, w: b.width, h: b.height, cw: b.width - bw("Left") - bw("Right"), ch: b.height - bw("Top") - bw("Bottom"),
                 nw: e.naturalWidth, nh: e.naturalHeight, inLink: !!e.closest("a"),
                 style: { display: cs.display, radius: cs.borderRadius, margin: [cs.marginTop, cs.marginRight, cs.marginBottom, cs.marginLeft].join(" "),
                          cursor: cs.cursor, objectFit: cs.objectFit, border: [cs.borderTopWidth, cs.borderTopStyle, cs.borderTopColor].join(" "),
                          background: cs.backgroundColor } };
      };
      const b = body.getBoundingClientRect();
      // the text on either side of a picture written inside a sentence: the last box of the text before it and the first of
      // the text after it, so the driver can tell whether all three share one line
      const line = (e) => {
        const box = (n, last) => { if (!n) return null; const r = document.createRange(); r.selectNodeContents(n); const rs = r.getClientRects(); const x = rs.length ? rs[last ? rs.length - 1 : 0] : null; return x && { left: x.left, right: x.right, top: x.top, bottom: x.bottom }; };
        const i = e.getBoundingClientRect();
        return { before: box(e.previousSibling, true), after: box(e.nextSibling, false), img: { left: i.left, right: i.right, top: i.top, bottom: i.bottom } };
      };
      const mid = body.querySelector('img[alt="inline trace"]');
      // a picture's caption: the first box of the first thing shown after it past any <br> (its line's words, or the
      // caption's under it), so the driver can tell beside from below
      const caption = (e) => {
        for (let n = e && e.nextSibling; n; n = n.nextSibling) {
          if (n.nodeName === "BR" || !(n.textContent || "").trim()) continue;
          const r = document.createRange(); r.selectNodeContents(n);
          const x = Array.from(r.getClientRects()).find((q) => q.width > 0);
          return x ? { left: x.left, right: x.right, top: x.top, bottom: x.bottom, text: (n.textContent || "").trim().slice(0, 40) } : null;
        }
        return null;
      };
      const pic = (alt, root) => { const e = (root || body).querySelector('img[alt="' + alt + '"]'); const m = one(e); if (m) { m.top = e.getBoundingClientRect().top; m.bottom = e.getBoundingClientRect().bottom; m.cls = e.className; m.next = caption(e); } return m; };
      const lead = body.querySelector('img[alt="leading trace"]'), trail = body.querySelector('img[alt="trailing trace"]');
      // a caption of two lines: the first box of each of its text nodes (the review of #349), so the driver can tell one
      // line from two
      const twoLines = (alt) => {
        const e = body.querySelector('img[alt="' + alt + '"]'), m = pic(alt);
        if (!e || !m) return null;
        const texts = [];
        for (let n = e.nextSibling; n; n = n.nextSibling) {
          if (n.nodeType !== 3 || !(n.textContent || "").trim()) continue;
          const r = document.createRange(); r.selectNodeContents(n);
          const x = Array.from(r.getClientRects()).find((q) => q.width > 0);
          texts.push(x ? { left: x.left, top: x.top, bottom: x.bottom, text: (n.textContent || "").trim().slice(0, 40) } : { text: (n.textContent || "").trim().slice(0, 40) });
        }
        m.lines = texts;
        m.brs = Array.from(e.parentElement.querySelectorAll("br")).map((b) => ({ cls: b.className, display: getComputedStyle(b).display }));
        return m;
      };
      const bold = body.querySelector('img[alt="bold trace"]');
      return { body: { left: b.left, right: b.right, w: b.width }, path: one(body.querySelector(".path-full-img")), tall: one(tall),
               large: one(body.querySelector('img[alt="run heat map"]')), badge: one(body.querySelector('img[alt="deploy passing"]')),
               linked: one(body.querySelector('img[alt="board thumbnail"]')), midline: one(mid), midlineLine: mid ? line(mid) : null,
               squashed: one(body.querySelector('img[alt="squashed heat map"]')),
               captioned: pic("captioned heat map"), panel1: pic("first panel"), panel2: pic("second panel"),
               leading: pic("leading trace"), leadingLine: lead ? line(lead) : null, trailing: pic("trailing trace"), trailingLine: trail ? line(trail) : null,
               userPic: pic("user heat map", document),
               spaces: twoLines("spaces caption"), backslash: twoLines("backslash caption"), brCap: twoLines("br caption"),
               sideL: pic("side left"), sideR: pic("side right"),
               bold: pic("bold trace"), boldLine: bold ? line(bold.parentElement) : null,
               rawCap: pic("raw captioned"), centreCap: pic("centred captioned"), rawA: pic("raw first"), rawB: pic("raw second"),
               rootL: pic("root left"), rootR: pic("root right"),
               bodyClass: document.body.className };
    }, TALL);
    await shot(page, "box-" + key + "-top");
    await page.evaluate(() => document.querySelector('img[alt="run heat map"]').scrollIntoView({ block: "start" }));
    await page.waitForTimeout(100);
    await shot(page, "box-" + key + "-bottom");
    for (const [alt, name] of [["captioned heat map", "caption"], ["first panel", "panels"], ["leading trace", "lines"], ["user heat map", "user-caption"],
                               ["spaces caption", "two-line-caption"], ["side left", "side-by-side"], ["bold trace", "bold"],
                               ["raw captioned", "raw-caption"], ["centred captioned", "raw-centred-caption"], ["raw first", "raw-blank-line"],
                               ["root left", "raw-row"]]) {
      await page.evaluate((a) => document.querySelector('img[alt="' + a + '"]').scrollIntoView({ block: "start" }), alt);
      await page.waitForTimeout(100);
      await shot(page, "box-" + key + "-" + name);
    }
    out.errs["box" + key] = errs;
    await ctx.close();
  } catch (e) { out.box[key + "Err"] = String(e).slice(0, 400); }
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
        Path(state, "names", API).write_text("api\t%s\t#c8f0b4\t#132a10\n" % cwd)
        Path(state, "sdk", API + ".json").write_text(json.dumps(
            {"sid": API, "name": "api", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": API, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "names", TESTS).write_text("tests\t%s\t#ffd59c\t#2e1f0c\n" % cwd)
        Path(state, "sdk", TESTS + ".json").write_text(json.dumps(
            {"sid": TESTS, "name": "tests", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": TESTS, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        # web first in the shared tab order (a tab drag's file), so a plain load still opens on web; the api and tests legs
        # ask for their session through the page's #only= filter
        Path(state, "session-order.json").write_text(json.dumps([WEB, API, TESTS]))
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
        api = [user(t0 - 7200, "v0", None, "where does the p95 split per region live?", API),
               agent(t0 - 7195, "b0", "v0", _reply_b0(h), API)]
        Path(proj, API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in api))
        tests = [user(t0 - 3600, "w0", None, "show me the slow request's trace next to the latency split", TESTS),
                 agent(t0 - 3595, "c0", "w0", _reply_c0(h, cls.badge), TESTS),
                 user(t0 - 3500, "w1", "c0", USER_CAPTIONED % h, TESTS),
                 agent(t0 - 3495, "c1", "w1", "That one matches the heat map above.", TESTS)]
        Path(proj, TESTS + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in tests))
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

    def test_a_wrapped_paragraph_steps_to_its_picture_before_the_figure_after_it(self):
        r = self._run()
        a = r["api"]
        self.assertNotIn("apiSoftErr", r, r.get("apiSoftErr"))
        self.assertNotIn("apiPathErr", r, r.get("apiPathErr"))
        self.assertEqual(a["softOpen"]["cue"], "1/4", "the api chat's stops: the wrapped picture, the figure, the dense card, the "
                         "referenced one (the srcset-only picture names no src; the summary's is its fold's): " + json.dumps(a["softOpen"]))
        self.assertIn("figure.png", a["softRight"]["src"], "→ from the picture on the wrapped line to the figure the page shows after the paragraph")
        self.assertEqual(a["softRight"]["cue"], "2/4")
        self.assertEqual(a["softRight2"]["src"], self.origin2 + "/card.png?v=dense", "→ again: the next paragraph's picture")
        self.assertEqual(a["pathOpen"]["cue"], "2/4", "the figure's own click sees the same order")
        self.assertEqual(a["pathLeft"]["src"], self.origin2 + "/hit.png?v=soft", "…and steps back to the picture above it")

    def test_a_picture_opens_on_the_srcset_candidate_it_shows(self):
        r = self._run()
        a = r["api"]
        self.assertNotIn("apiDenseErr", r, r.get("apiDenseErr"))
        self.assertEqual(a["denseShown"], self.origin2 + "/card.png?v=dense2", "the dense screen shows the 2x candidate")
        self.assertEqual(a["denseOpen"]["src"], self.origin2 + "/card.png?v=dense2", "the lightbox opens on what the page showed")
        self.assertEqual(a["denseOpen"]["cue"], "3/4", "…at its place in the arrows, which name it by its src")
        self.assertNotIn("apiSrcsetErr", r, r.get("apiSrcsetErr"))
        s = a["srcsetOpen"]
        self.assertIsNotNone(s, "a picture drawn from a srcset alone opens the lightbox")
        self.assertEqual(s["src"], self.origin2 + "/card.png?v=srcset", "on the picture it shows, never an empty source: " + json.dumps(s))
        self.assertGreater(s["natural"], 0)
        self.assertEqual((s["ctl"]["aria"], s["ctl"]["href"]), ("Open original", self.origin2 + "/card.png?v=srcset"),
                         "its Open original is the picture, never the dashboard's own page")
        self.assertEqual(a["srcsetOriginal"], [[self.origin2 + "/card.png?v=srcset", "_blank", "noopener,noreferrer"]])
        self.assertEqual(a["cursor"]["srcset"], "zoom-in", json.dumps(a["cursor"]))

    def test_a_src_written_with_numeric_references_is_the_stop_its_click_finds(self):
        r = self._run()
        a = r["api"]
        self.assertNotIn("apiNumErr", r, r.get("apiNumErr"))
        self.assertEqual(a["numShown"], self.origin2 + "/card.png?v=num&w=2&h=3", "the page's <img> decoded the references")
        self.assertEqual(a["numOpen"]["src"], a["numShown"])
        self.assertEqual(a["numOpen"]["cue"], "4/4", "the sequence names it as the page does: the click finds it, and no phantom follows")
        self.assertEqual(a["numLeft"]["src"], self.origin2 + "/card.png?v=dense", "← steps to the picture before it")

    def test_a_picture_in_a_summary_opens_its_details(self):
        r = self._run()
        a = r["api"]
        self.assertNotIn("apiFoldErr", r, r.get("apiFoldErr"))
        self.assertEqual(a["fold"], {"shutBefore": True, "lightbox": False, "open": True},
                         "the summary's picture keeps the summary's click: the details open, no lightbox")
        self.assertNotEqual(a["cursor"]["fold"], "zoom-in", "…and it wears no zoom cursor")
        self.assertEqual(a["cursor"]["soft"], "zoom-in")

    def test_a_markdown_picture_wears_the_path_previews_box_at_either_width_in_either_theme(self):
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            table = "\n  %s: %s" % (key, json.dumps(b))
            self.assertIsNotNone(b["path"], "the path preview rendered beside the markdown pictures" + table)
            self.assertEqual(b["path"]["style"]["cursor"], "zoom-in", table)
            for name, (nw, nh) in (("tall", (400, 1600)), ("large", (1800, 1200))):
                m = b[name]
                self.assertEqual((m["nw"], m["nh"]), (nw, nh), name + " loaded from the lab's outside host" + table)
                self.assertLessEqual(m["h"], 320.5, "the %s picture stays within the path preview's 320px%s" % (name, table))
                self._inside(m, b["body"], "the %s picture" % name, table)
                self.assertAlmostEqual(m["cw"] / m["ch"], nw / nh, delta=0.02 * nw / nh, msg="…keeping its ratio" + table)
                self.assertEqual(m["style"], b["path"]["style"],
                                 "the %s picture wears the path preview's box: display, radius, margins, frame, background, cursor%s" % (name, table))
            self.assertGreaterEqual(b["tall"]["h"], 319.5, "the tall picture is bounded by the 320px cap itself" + table)
            badge = b["badge"]
            self.assertEqual((badge["w"], badge["h"]), (88, 20), "the badge keeps its natural size" + table)
            self.assertEqual(badge["style"]["display"], "inline", "…in its line of text, not a block of its own" + table)
            self.assertNotEqual(badge["style"]["cursor"], "zoom-in", "…and does not say it zooms" + table)
            self.assertTrue(badge["style"]["border"].startswith("0px"), "…with no frame" + table)
            linked = b["linked"]
            self.assertTrue(linked["inLink"], table)
            self.assertNotEqual(linked["style"]["cursor"], "zoom-in", "the linked picture wears its link's cursor" + table)
            self.assertLessEqual(linked["h"], 320.5, "…at the same 320px cap" + table)
            self._inside(linked, b["body"], "the linked picture", table)
        self.assertIn("theme-light", r["box"]["phoneLight"]["bodyClass"].split(), "the light legs ran in the light theme")
        self.assertIn("chat-theme-yatharth", r["box"]["desktopLight"]["bodyClass"].split())

    def test_a_picture_inside_a_sentence_stays_on_its_line_and_a_squashed_one_is_a_glyph(self):
        # the review of the shared box (2026-10-21): the box's block layout split a sentence around a mid-sentence picture,
        # and a large picture written with height="20" became a 20px framed blob on a line of its own (the zoom judged its
        # file's height, not the one it shows at)
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            table = "\n  %s: %s" % (key, json.dumps({k: b[k] for k in ("midline", "midlineLine", "squashed", "body")}))
            m, ln = b["midline"], b["midlineLine"]
            self.assertEqual((m["nw"], m["nh"]), (400, 1600), "the mid-sentence picture loaded" + table)
            self.assertEqual(m["style"]["display"], "inline", "a picture inside a sentence stays inline" + table)
            self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + table)
            self.assertEqual(m["style"]["cursor"], "zoom-in", "…and still says it zooms" + table)
            self.assertLessEqual(m["h"], 320.5, "…within the same 320px cap" + table)
            self.assertIsNotNone(ln["before"], table)
            self.assertIsNotNone(ln["after"], table)
            self.assertLessEqual(ln["before"]["right"], ln["img"]["left"] + 1, "the words before it sit to its left" + table)
            self.assertGreaterEqual(ln["after"]["left"], ln["img"]["right"] - 1, "the words after it sit to its right" + table)
            self.assertAlmostEqual(ln["before"]["bottom"], ln["after"]["bottom"], delta=1, msg="…on the same line: the sentence is not split" + table)
            q = b["squashed"]
            self.assertEqual((q["nw"], q["nh"]), (1800, 1200), "the squashed picture is the large one" + table)
            self.assertAlmostEqual(q["h"], 20, delta=0.5, msg="shown at its height attribute" + table)
            self.assertEqual(q["style"]["display"], "inline", "a glyph, not a block on a line of its own" + table)
            self.assertTrue(q["style"]["border"].startswith("0px"), "…with no frame" + table)
            self.assertNotEqual(q["style"]["cursor"], "zoom-in", "…and no zoom" + table)

    def test_a_picture_with_its_caption_on_the_next_line_keeps_the_box_with_the_caption_under_it(self):
        # the review of #343 (2026-10-21): `![plot](url)` with `*Figure 1: …*` on the next line, the common agent shape, is
        # one paragraph, and the solo rule read the caption as text beside the picture: inline, unframed, the caption at its
        # bottom edge to its right (x=560 on the desktop). Assistant markdown joins the two with a soft break, the user's
        # with a <br>; both keep the box, and the caption sits under the picture at the paragraph's left edge
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            for name, text in (("captioned", "Figure 1"), ("userPic", "Figure 2")):
                m = b[name]
                table = "\n  %s %s: %s" % (key, name, json.dumps(m))
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the captioned picture loaded" + table)
                self.assertIn("md-solo", m["cls"].split(), "a caption on the next line leaves the picture alone on its line" + table)
                self.assertEqual(m["style"], b["path"]["style"], "…so it wears the path preview's box: block, rounded, framed" + table)
                self.assertLessEqual(m["h"], 320.5, "…within 320px" + table)
                c = m["next"]
                self.assertIsNotNone(c, "the caption rendered" + table)
                self.assertTrue(c["text"].startswith(text), table)
                self.assertGreaterEqual(c["top"], m["bottom"] - 0.5, "the caption sits UNDER the picture, not beside it" + table)
                self.assertLess(c["top"] - m["bottom"], 12, "…directly under it: no blank line between" + table)
                self.assertAlmostEqual(c["left"], m["left"], delta=1.5, msg="…starting at the picture's left edge" + table)

    def test_two_pictures_on_lines_of_their_own_each_keep_the_box_and_words_on_a_pictures_line_keep_it_inline(self):
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            table = "\n  %s: %s" % (key, json.dumps({k: b[k] for k in ("panel1", "panel2", "leading", "leadingLine", "trailing", "trailingLine")}))
            p1, p2 = b["panel1"], b["panel2"]
            for name, m in (("first", p1), ("second", p2)):
                self.assertIn("md-solo", m["cls"].split(), "the %s of two pictures on lines of their own stands alone%s" % (name, table))
                self.assertEqual(m["style"], b["path"]["style"], "…and wears the box" + table)
            self.assertGreaterEqual(p2["top"], p1["bottom"] - 0.5, "the second sits under the first" + table)
            for name, ln, side in (("leading", b["leadingLine"], "after"), ("trailing", b["trailingLine"], "before")):
                m = b[name]
                self.assertEqual((m["nw"], m["nh"]), (400, 1600), "the %s picture loaded%s" % (name, table))
                self.assertNotIn("md-solo", m["cls"].split(), "words on its own line: the %s picture is a word in a sentence%s" % (name, table))
                self.assertEqual(m["style"]["display"], "inline", "…so it stays inline" + table)
                self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + table)
                self.assertEqual(m["style"]["cursor"], "zoom-in", "…and still says it zooms" + table)
                self.assertIsNotNone(ln[side], table)
                if side == "after":
                    self.assertGreaterEqual(ln["after"]["left"], ln["img"]["right"] - 1, "the words after it sit to its right" + table)
                    self.assertLess(ln["after"]["top"], ln["img"]["bottom"], "…on its line" + table)
                else:
                    self.assertLessEqual(ln["before"]["right"], ln["img"]["left"] + 1, "the words before it sit to its left" + table)
                    self.assertLess(ln["before"]["top"], ln["img"]["bottom"], "…on its line" + table)

    def test_a_hard_break_inside_a_caption_keeps_the_captions_two_lines(self):
        # the review of #349 (2026-10-21): the rule that hid the <br> after a boxed picture was `+ br`, which passes over
        # text, so the hard break INSIDE an agent's two-line caption was hidden as the picture's and the caption read
        # "...per hour.Source: ...". Only the <br> that ends the picture's own line goes now, and here that line ends at
        # the soft break, so the caption's break stays: two spaces, a backslash and a <br> alike
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            for name, fig in (("spaces", "Figure 3"), ("backslash", "Figure 4"), ("brCap", "Figure 5")):
                m = b[name]
                table = "\n  %s %s: %s" % (key, name, json.dumps(m))
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the captioned picture loaded" + table)
                self.assertIn("md-solo", m["cls"].split(), "alone on its line" + table)
                self.assertEqual(m["style"], b["path"]["style"], "…so boxed" + table)
                self.assertEqual([x["text"][:8] for x in m["lines"]], [fig, "Source: "], "the caption's two lines rendered" + table)
                first, second = m["lines"]
                self.assertGreaterEqual(first["top"], m["bottom"] - 0.5, "the caption sits under the picture" + table)
                self.assertLess(first["top"] - m["bottom"], 12, "…directly under it" + table)
                self.assertGreaterEqual(second["top"], first["bottom"] - 1, "its second line starts BELOW its first: the break holds" + table)
                self.assertAlmostEqual(second["left"], first["left"], delta=1.5, msg="…at the same left edge" + table)
                self.assertEqual(m["brs"], [{"cls": "", "display": "inline"}], "the caption's own <br>, unmarked and shown" + table)

    def test_raw_html_pictures_side_by_side_stay_in_one_row_and_markdowns_own_lines_still_stack(self):
        # the review of #349 (2026-10-21): every newline counted as a line end, the HTML source's own included, so the
        # README layout `<p>\n<img width="45%">\n<img width="45%">\n</p>` stacked two framed blocks. In the author's
        # HTML a newline between tags is a space: one row, unframed. Markdown's own `![a](u)\n![b](u)` still stacks
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            left, right = b["sideL"], b["sideR"]
            table = "\n  %s: %s" % (key, json.dumps({k: b[k] for k in ("sideL", "sideR", "panel1", "panel2", "body")}))
            for name, m in (("left", left), ("right", right)):
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the %s picture loaded%s" % (name, table))
                self.assertNotIn("md-solo", m["cls"].split(), "the %s picture shares its line%s" % (name, table))
                self.assertEqual(m["style"]["display"], "inline", "…so it stays inline" + table)
                self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + table)
                self.assertEqual(m["style"]["cursor"], "zoom-in", "…and still zooms" + table)
                self._inside(m, b["body"], "the %s picture" % name, table)
            self.assertAlmostEqual(left["top"], right["top"], delta=1, msg="one row: the two tops level" + table)
            self.assertGreaterEqual(right["left"], left["right"] - 1, "…the right picture beside the left one" + table)
            self.assertAlmostEqual(left["w"], b["body"]["w"] * 0.45, delta=2, msg="…each at the author's 45%" + table)
            p1, p2 = b["panel1"], b["panel2"]
            self.assertTrue("md-solo" in p1["cls"].split() and "md-solo" in p2["cls"].split(), table)
            self.assertGreaterEqual(p2["top"], p1["bottom"] - 0.5, "markdown's two lines still stack" + table)
            # the review of #351 (2026-10-21): two raw <img> lines at the message's root, no blank line between, are the same
            # row: the newline between two pictures of the author's HTML is a space
            rl, rr = b["rootL"], b["rootR"]
            rtable = "\n  %s: %s" % (key, json.dumps({"rootL": rl, "rootR": rr, "body": b["body"]}))
            for m in (rl, rr):
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the root row's picture loaded" + rtable)
                self.assertNotIn("md-solo", m["cls"].split(), "it shares its line with the other" + rtable)
                self.assertEqual(m["style"]["display"], "inline", "…inline" + rtable)
                self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + rtable)
            self.assertAlmostEqual(rl["top"], rr["top"], delta=1, msg="the root row: the two tops level" + rtable)
            self.assertGreaterEqual(rr["left"], rl["right"] - 1, "…side by side" + rtable)

    def test_a_raw_picture_alone_on_its_line_keeps_the_box_with_its_caption_under_it(self):
        # the review of #351 (2026-10-21): #351 read every newline of the author's HTML as a space, so an agent's raw
        # `<img src="…">` with `Figure 6: …` on the next line (a raw HTML block, loose at the message's root) went inline,
        # unframed, the caption beside its bottom edge, and a padded `<p align="center">` put a picture's <em> caption beside
        # it. A newline there ends the picture's line unless another picture opens the next: the box, the caption under it
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            for name, text in (("rawCap", "Figure 6"), ("centreCap", "Figure 7")):
                m = b[name]
                table = "\n  %s %s: %s" % (key, name, json.dumps(m))
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the raw picture loaded" + table)
                self.assertIn("md-solo", m["cls"].split(), "alone on its source line" + table)
                self.assertEqual(m["style"], b["path"]["style"], "…so it wears the path preview's box: block, rounded, framed" + table)
                self.assertLessEqual(m["h"], 320.5, "…within 320px" + table)
                self._inside(m, b["body"], "the raw picture", table)
                c = m["next"]
                self.assertIsNotNone(c, "the caption rendered" + table)
                self.assertTrue(c["text"].startswith(text), table)
                self.assertGreaterEqual(c["top"], m["bottom"] - 0.5, "the caption sits UNDER the picture, not beside it" + table)
                self.assertLess(c["top"] - m["bottom"], 12, "…directly under it" + table)
            c, m = b["rawCap"]["next"], b["rawCap"]
            self.assertAlmostEqual(c["left"], m["left"], delta=1.5, msg="…starting at the picture's left edge" + "\n  %s: %s" % (key, json.dumps(m)))

    def test_two_raw_pictures_a_blank_line_apart_each_keep_the_box_stacked(self):
        # the review of #351 (2026-10-21): two raw <img> lines with a blank line between them (which ends a raw HTML block
        # in markdown's source) went inline, unframed and touching. Each stands alone: boxed, the second under the first
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            a, c = b["rawA"], b["rawB"]
            table = "\n  %s: %s" % (key, json.dumps({"rawA": a, "rawB": c, "body": b["body"]}))
            for name, m in (("first", a), ("second", c)):
                self.assertEqual((m["nw"], m["nh"]), (1800, 1200), "the %s picture loaded%s" % (name, table))
                self.assertIn("md-solo", m["cls"].split(), "the %s picture stands alone%s" % (name, table))
                self.assertEqual(m["style"], b["path"]["style"], "…boxed like the path preview" + table)
                self._inside(m, b["body"], "the %s picture" % name, table)
            self.assertGreaterEqual(c["top"], a["bottom"] - 0.5, "the second sits UNDER the first: stacked" + table)
            self.assertAlmostEqual(c["left"], a["left"], delta=1.5, msg="…at the same left edge" + table)

    def test_a_bold_picture_in_a_sentence_stays_on_its_line(self):
        # the review of #349 (2026-10-21): `The **![x](u)** result` framed the picture as a block and split the sentence,
        # since its parent <strong> held nothing else. The walk climbs out of the bold: the sentence is the picture's line
        r = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            self.assertNotIn(key + "Err", r["box"], r["box"].get(key + "Err"))
            b = r["box"][key]
            m, ln = b["bold"], b["boldLine"]
            table = "\n  %s: %s" % (key, json.dumps({"bold": m, "line": ln}))
            self.assertEqual((m["nw"], m["nh"]), (400, 1600), "the bold picture loaded" + table)
            self.assertNotIn("md-solo", m["cls"].split(), "a word in its sentence" + table)
            self.assertEqual(m["style"]["display"], "inline", "…inline" + table)
            self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + table)
            self.assertEqual(m["style"]["cursor"], "zoom-in", "…and still zooms" + table)
            self.assertIsNotNone(ln["before"], table)
            self.assertIsNotNone(ln["after"], table)
            self.assertLessEqual(ln["before"]["right"], ln["img"]["left"] + 1, "the words before it sit to its left" + table)
            self.assertGreaterEqual(ln["after"]["left"], ln["img"]["right"] - 1, "the words after it sit to its right" + table)
            self.assertAlmostEqual(ln["before"]["bottom"], ln["after"]["bottom"], delta=1, msg="…on the same line" + table)

    def test_no_page_errors(self):
        r = self._run()
        for k, errs in r["errs"].items():
            self.assertEqual(errs, [], k)


if __name__ == "__main__":
    unittest.main()
