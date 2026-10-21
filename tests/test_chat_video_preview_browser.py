#!/usr/bin/env python3
"""A video in the chat gets a picture's treatment (the user 2026-10-21): the path preview's compact box, a tap to open it
large in the lightbox, where it plays.

A hermetic kernel serves a synthetic notes-api chat (session web on TESTHOST, placeholder sids). A second local HTTP server
plays an outside video host, answering byte ranges as a real one does, so every clip loads in CI without the internet.
The clip itself is made at run time, never checked in: Playwright's own screen recorder films a two-second synthetic page
at 1920x1080 into a WebM (VP8), the one encoder every Playwright Chromium install carries. The lab's Chromium plays that
WebM under any name, so the outside host serves it as clip.mp4 and demo.mp4 as well, the names the chat keys on. One reply:
  <video controls width="100%" src="…/clip.mp4"> alone in its paragraph, the shape an agent posts;
  a paragraph naming the local file media/walkthrough.webm (romp's path preview, through the kernel's /file) and linking
  …/demo.mp4 (a plain link to a video file), so both previews render after it;
  a markdown picture, ![the signup form](…/form.png), the last stop in the arrows' sequence.
The driver, at a desktop width and at a phone width, in the dark theme and the light one, reads:
  the inline still: within 320px and the bubble, 569x320 for the 1920x1080 clip on the desktop although the markup says
  width="100%", rounded and framed, no native controls, a play badge, muted and paused, the zoom cursor;
  the tap or click: the lightbox opens on that clip, a <video controls> from its own address (no first-frame fragment),
  playing, with "Open original";
  Escape: the lightbox gone and the clip it played paused, detached and unloaded;
and on the desktop in the dark theme, the arrows: the clip, the local file, the linked clip and the picture in page order,
each step stopping the video it leaves; the local file's preview loading through /file and its range requests answered
206 (the browser's own media requests, and a closed `bytes=0-1` probe, Safari's first ask); the linked clip's preview.
A second session, api, posts a <video> with its caption on the next line (the review of #343, 2026-10-21): at both widths
in both themes the clip keeps the box, with the caption directly under it. A third session, tests, posts a reply of raw
<video>s (the review of #351, 2026-10-21), read the same way: written on one line, as a block with its open tag on a line of its own, and with its <source> on lines of its
own, each with a caption on the next line, the clip keeps the box with the caption under it; two blocks a blank line apart
each keep it, stacked; two clips on consecutive lines of a padded <p> stay inline and unframed.

CVP_DIST=<dir> serves another tree's UI bundle (the red run's before); CVP_SHOTS=<dir> writes the reply at both widths in
both themes and the open lightbox; CVP_DUMP=<path> writes the whole measurement. Skips LOUDLY without the extension deps
or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both, so a skip there is a failure).
Synthetic throughout: invented prose, run-time media, no recorded data.
"""
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

WEB = "aaaaaaaa-1111-2222-3333-5e1d0c1a9b77"
API = "cccccccc-1111-2222-3333-5e1d0c1a9b77"
TESTS = "dddddddd-1111-2222-3333-5e1d0c1a9b77"


def _png(w, h, rgb):
    """An opaque PNG, assembled at run time (no binary fixture in the repo)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# The clip: Playwright's recorder films a synthetic page (a blue field, a yellow block crossing it, a frame counter) at
# 1920x1080. Exit 3: no browser to film with.
RECORD = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const dir = process.env.OUT_DIR;
const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, recordVideo: { dir, size: { width: 1920, height: 1080 } } });
const page = await ctx.newPage();
await page.setContent('<body style="margin:0;background:#2a6fb0;font:bold 120px sans-serif;color:#fff">'
  + '<div id=b style="position:absolute;left:0;top:340px;width:400px;height:400px;background:#f0c040"></div><div id=n style="position:absolute;right:80px;top:60px">0</div></body>');
for (let i = 0; i < 16; i++) {
  await page.evaluate((i) => { document.getElementById("b").style.left = (i * 100) + "px"; document.getElementById("n").textContent = String(i); }, i);
  await page.waitForTimeout(120);
}
const v = page.video();
await ctx.close();
fs.renameSync(await v.path(), process.env.OUT_FILE);
await browser.close();
console.log("RECORDED " + fs.statSync(process.env.OUT_FILE).size);
"""

CLIP = {"bytes": b""}
HOSTED = {"/form.png": _png(640, 400, (84, 178, 4))}


class _Host(BaseHTTPRequestHandler):
    """The outside video host: the clip under two names and a picture, with byte ranges as a real host answers them."""
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/clip.mp4", "/demo.mp4"):
            body, ctype = CLIP["bytes"], "video/mp4"
        elif path in HOSTED:
            body, ctype = HOSTED[path], "image/png"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        m = re.match(r"^bytes=(\d+)-(\d*)$", self.headers.get("Range") or "")
        if m and ctype.startswith("video/"):
            a = int(m.group(1))
            b = min(int(m.group(2)) if m.group(2) else len(body) - 1, len(body) - 1)
            self.send_response(206)
            self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(body)))
            body = body[a:b + 1]
        else:
            self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass                                  # a <video> drops a request it no longer needs: nothing to report

    def log_message(self, *a):
        pass


def _reply(host):
    return ("The signup flow, recorded:\n\n"
            "<video controls width=\"100%%\" src=\"%(h)s/clip.mp4\"></video>\n\n"
            "The error state is in media/walkthrough.webm, and the hosted copy is at %(h)s/demo.mp4 for the review.\n\n"
            "![the signup form](%(h)s/form.png)\n") % {"h": host}


def _reply_captioned(host):
    # the review of #343 (2026-10-21): a <video> with its caption on the next line is one paragraph, and the clip keeps the
    # box with the caption under it, as a picture does
    return ("The checkout flow, recorded:\n\n"
            "<video controls src=\"%(h)s/demo.mp4?v=cap\"></video>\n"
            "*Clip 2: checkout, from the cart to the receipt.*\n") % {"h": host}


def _reply_raw(host):
    # the review of #351 (2026-10-21): a <video> an agent writes as a raw HTML block (its open tag alone on its line, or
    # its <source> on lines of their own, so loose at the message's root) with its caption on the next line keeps the box
    # with the caption under it, as the one-line form (a paragraph) always has; two such blocks a blank line apart each
    # keep it, stacked; two <video>s on consecutive lines of a padded <p> (the README layout) stay inline, unframed
    return ("The three flows, recorded:\n\n"
            "<video controls width=\"100%%\" src=\"%(h)s/demo.mp4?v=line\"></video>\n"
            "Clip 3: signup, written on one line.\n\n"
            "<video controls width=\"100%%\" src=\"%(h)s/demo.mp4?v=block\">\n</video>\n"
            "Clip 4: checkout, written as a block.\n\n"
            "<video controls width=\"100%%\">\n  <source src=\"%(h)s/demo.mp4?v=source\" type=\"video/mp4\">\n</video>\n"
            "Clip 5: the receipt, from its source.\n\n"
            "<video controls width=\"100%%\" src=\"%(h)s/demo.mp4?v=blankA\">\n</video>\n\n"
            "<video controls width=\"100%%\" src=\"%(h)s/demo.mp4?v=blankB\">\n</video>\n\n"
            "The two side by side:\n\n"
            "<p>\n<video width=\"45%%\" src=\"%(h)s/demo.mp4?v=rowL\"></video>\n"
            "<video width=\"45%%\" src=\"%(h)s/demo.mp4?v=rowR\"></video>\n</p>\n") % {"h": host}


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
const CLIP = cfg.host + "/clip.mp4";
const STILL = '.assistant video[src^="' + CLIP + '"]';            // the message's own <video>, wherever the page put it

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
  const errs = [], files = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  page.on("response", (r) => { const u = r.url(); if (/\/file\?path=/.test(u) && /walkthrough\.webm/.test(u)) files.push({ status: r.status(), range: (r.request().headers()["range"] || null) }); });
  await page.goto(cfg.chat);
  await page.waitForSelector(STILL, { timeout: 60000 });
  // every video's metadata (its size) in, the picture loaded
  await page.waitForFunction(() => Array.from(document.querySelectorAll(".assistant video")).every((v) => v.readyState >= 1), null, { timeout: 30000 }).catch(() => {});
  await page.waitForFunction(() => { const i = document.querySelector('img[alt="the signup form"]'); return !!(i && i.complete && i.naturalWidth > 0); }, null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(300);
  return { ctx, page, errs, files };
};

// the reply's bubble and its stills
const measure = (page, sel) => page.evaluate(({ sel, host }) => {
  const q = (v) => Math.round(v * 10) / 10;
  const r = (e) => { if (!e) return null; const b = e.getBoundingClientRect(); return { left: q(b.left), right: q(b.right), top: q(b.top), w: q(b.width), h: q(b.height) }; };
  const v = document.querySelector(sel);
  if (!v) return { missing: true, bodyClass: document.body.className };
  const body = v.closest(".assistant");
  body.scrollIntoView({ block: "start" });
  const wrap = v.parentElement;
  const cs = getComputedStyle(v), ws = getComputedStyle(wrap), badge = getComputedStyle(wrap, "::after");
  const fig = (q2) => { const e = body.querySelector(q2); if (!e) return null; return { rect: r(e), src: e.getAttribute("src"), ready: e.readyState, w: e.videoWidth, h: e.videoHeight, still: e.parentElement.className, data: e.parentElement.getAttribute("data-video"), inPreview: !!e.closest(".path-full") }; };
  const pf = body.querySelector(".path-thumbs");
  const para = Array.from(body.querySelectorAll("p")).find((p) => /error state/.test(p.textContent || ""));
  return {
    body: r(body), video: r(v), natural: [v.videoWidth, v.videoHeight], ready: v.readyState,
    attrs: { controls: v.controls, muted: v.muted, paused: v.paused, autoplay: v.autoplay, preload: v.getAttribute("preload"), src: v.getAttribute("src"), width: v.getAttribute("width") },
    style: { display: cs.display, radius: cs.borderRadius, border: [cs.borderTopWidth, cs.borderTopStyle].join(" "), maxH: cs.maxHeight },
    wrap: { cls: wrap.className, data: wrap.getAttribute("data-video"), cursor: ws.cursor, rect: r(wrap) },
    badge: { content: badge.content, w: badge.width, h: badge.height, bg: badge.backgroundImage.slice(0, 40), rect: null },
    local: fig('.path-full video[src*="walkthrough.webm"]'), linked: fig('.path-full video[src^="' + host + '/demo.mp4"]'),
    stripAfterPara: !!(pf && para && para.nextElementSibling === pf),
    page: { docScrollW: document.scrollingElement.scrollWidth, docClientW: document.scrollingElement.clientWidth },
    bodyClass: document.body.className,
  };
}, { sel, host: cfg.host });

const readLB = (page) => page.evaluate(() => {
  const wrap = document.getElementById("romp-lightbox");
  if (!wrap) return null;
  const v = wrap.querySelector("video"), img = wrap.querySelector("img.romp-lightbox-img");
  const cue = wrap.querySelector(".romp-lightbox-cue");
  const ctl = wrap.querySelector(".romp-lightbox-dl");
  const copy = wrap.querySelector(".romp-lightbox-copy");
  return { kind: v ? "video" : img ? "img" : null, src: v ? v.getAttribute("src") : img ? img.getAttribute("src") : null,
           controls: v ? v.controls : null, paused: v ? v.paused : null, muted: v ? v.muted : null, time: v ? v.currentTime : null,
           cls: (v || img || { className: "" }).className, cue: cue ? cue.textContent : null,
           base: (wrap.querySelector(".fileview-base") || {}).textContent,
           ctl: ctl ? { aria: ctl.getAttribute("aria-label"), href: ctl.getAttribute("href"), target: ctl.getAttribute("target"), download: ctl.getAttribute("download") } : null,
           copyShown: copy ? getComputedStyle(copy).display !== "none" : null };
});
// open: the lightbox up and, for a video, playing (the tap that opened it starts it) or, after an arrow step, loaded
const settle = async (page, playing) => {
  const up = await page.waitForSelector("#romp-lightbox", { timeout: 4000 }).then(() => true).catch(() => false);
  if (!up) return null;
  if (playing) await page.waitForFunction(() => { const v = document.querySelector("#romp-lightbox video"); return !!(v && !v.paused && v.currentTime > 0); }, null, { timeout: 8000 }).catch(() => {});
  else await page.waitForFunction(() => { const v = document.querySelector("#romp-lightbox video"), i = document.querySelector("#romp-lightbox img.romp-lightbox-img");
    return v ? v.readyState >= 1 : !!(i && i.complete && i.naturalWidth > 0); }, null, { timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(120);
  return readLB(page);
};
const hold = (page) => page.evaluate(() => { window.__held = document.querySelector("#romp-lightbox video"); return !!window.__held; });
const held = (page) => page.evaluate(() => { const v = window.__held; return v ? { paused: v.paused, connected: v.isConnected, src: v.getAttribute("src"), net: v.networkState } : null; });
const press = async (page, key) => { await page.keyboard.press(key); return settle(page, false); };
const lbGone = (page) => page.evaluate(() => !document.getElementById("romp-lightbox"));
const shot = async (page, name, clipSel) => {
  if (!cfg.shots) return;
  if (clipSel) {
    const c = await page.evaluate((s) => { const e = document.querySelector(s); if (!e) return null; const b = e.getBoundingClientRect();
      return { x: 0, y: Math.max(0, b.top - 12), width: window.innerWidth, height: Math.max(40, Math.min(window.innerHeight - Math.max(0, b.top - 12), b.height + 24)) }; }, clipSel);
    if (c) { await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip: c }); return; }
  }
  await page.screenshot({ path: cfg.shots + "/" + name + ".png" });
};

const out = { legs: {}, errs: {} };
for (const theme of ["dark", "light"]) for (const phone of [false, true]) {
  const key = (phone ? "phone" : "desktop") + theme[0].toUpperCase() + theme.slice(1);
  const leg = {};
  out.legs[key] = leg;
  let ctx = null;
  try {
    const o = await open(theme, phone);
    ctx = o.ctx;
    const { page, errs, files } = o;
    leg.m = await measure(page, STILL);
    await page.evaluate((s) => { const v = document.querySelector(s); if (v) v.closest(".assistant").scrollIntoView({ block: "start" }); }, STILL);
    await page.waitForTimeout(150);
    await shot(page, key + "-reply", ".turn-assistant:has(" + STILL + ")");
    // the tap (phone) or click (desktop) on the still opens the lightbox on the clip, playing
    await page.evaluate((s) => document.querySelector(s).scrollIntoView({ block: "center" }), STILL);
    if (phone) await page.tap(STILL); else await page.click(STILL);
    leg.open = await settle(page, true);
    await shot(page, key + "-lightbox");
    if (leg.open) {
      leg.held = await hold(page);
      if (!phone && theme === "dark") {
        // "Open original": a new tab, the opener severed, the lightbox still up
        await page.evaluate(() => { window.__opened = []; });
        await page.click("#romp-lightbox .romp-lightbox-dl", { timeout: 5000 });
        await page.waitForTimeout(150);
        leg.openOriginal = { opened: await page.evaluate(() => window.__opened), stillOpen: !(await lbGone(page)) };
        // the arrows, in page order: the clip, the local file, the linked clip, the picture; each step stops what it leaves
        leg.right1 = await press(page, "ArrowRight");
        leg.afterStep = await held(page);
        leg.right2 = await press(page, "ArrowRight");
        leg.right3 = await press(page, "ArrowRight");
        leg.right4 = await press(page, "ArrowRight");          // the end ENDS
        leg.left1 = await press(page, "ArrowLeft");
        await hold(page);
      }
      await page.keyboard.press("Escape");
      await page.waitForTimeout(200);
      leg.escapeGone = await lbGone(page);
      leg.afterEscape = await held(page);
    }
    if (!phone && theme === "dark") {
      // the local file's preview: through /file, its media requests answered with ranges; and the closed probe Safari opens with
      leg.files = files.slice();
      leg.probe = await page.evaluate(async () => {
        const v = document.querySelector('.path-full video[src*="walkthrough.webm"]');
        if (!v) return null;
        const u = v.getAttribute("src").split("#")[0];
        const r = await fetch(u, { headers: { Range: "bytes=0-1" } });
        const b = new Uint8Array(await r.arrayBuffer());
        return { url: u, status: r.status, range: r.headers.get("Content-Range"), accept: r.headers.get("Accept-Ranges"), type: r.headers.get("Content-Type"), len: b.length };
      });
      // the local file's still opens the lightbox on the FILE, and the linked clip's on its address
      await page.click('.path-full video[src*="walkthrough.webm"]');
      leg.localOpen = await settle(page, true);
      await page.keyboard.press("Escape"); await page.waitForTimeout(150);
      await page.click('.path-full video[src^="' + cfg.host + '/demo.mp4"]');
      leg.linkOpen = await settle(page, true);
      await page.keyboard.press("Escape"); await page.waitForTimeout(150);
    }
    out.errs[key] = errs;
  } catch (e) { leg.err = String(e && e.stack || e).slice(0, 600); }
  if (ctx) await ctx.close();
}
// the second session (api): a video with its caption on the next line, at both widths in both themes
out.caption = {};
const CAP = '.assistant video[src^="' + cfg.host + '/demo.mp4?v=cap"]';
for (const theme of ["dark", "light"]) for (const phone of [false, true]) {
  const key = (phone ? "phone" : "desktop") + theme[0].toUpperCase() + theme.slice(1);
  let ctx = null;
  try {
    ctx = await browser.newContext(phone
      ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
      : { viewport: { width: 1100, height: 800 } });
    await ctx.addInitScript(({ t }) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, { t: THEMES[theme] });
    const page = await ctx.newPage();
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
    await page.goto(cfg.chat + "#only=api");
    await page.waitForSelector(CAP, { timeout: 60000 });
    await page.waitForFunction((s) => { const v = document.querySelector(s); return !!(v && v.readyState >= 1); }, CAP, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(300);
    out.caption[key] = await page.evaluate((s) => {
      const v = document.querySelector(s), wrap = v.parentElement, cs = getComputedStyle(v);
      v.closest(".assistant").scrollIntoView({ block: "start" });
      const b = v.getBoundingClientRect();
      let cap = null;
      for (let n = wrap.nextSibling; n && !cap; n = n.nextSibling) {
        if (n.nodeName === "BR" || !(n.textContent || "").trim()) continue;
        const r = document.createRange(); r.selectNodeContents(n);
        const x = Array.from(r.getClientRects()).find((q) => q.width > 0);
        cap = x ? { left: x.left, top: x.top, text: (n.textContent || "").trim().slice(0, 40) } : { missing: true };
      }
      return { wrap: wrap.className, natural: [v.videoWidth, v.videoHeight], video: { left: b.left, top: b.top, bottom: b.bottom, h: b.height },
               style: { display: cs.display, radius: cs.borderRadius, border: [cs.borderTopWidth, cs.borderTopStyle].join(" ") }, cap,
               bodyClass: document.body.className };
    }, CAP);
    await shot(page, key + "-caption", ".turn-assistant:has(" + CAP + ")");
    out.errs["caption" + key] = errs;
  } catch (e) { out.caption[key] = { err: String(e && e.stack || e).slice(0, 600) }; }
  if (ctx) await ctx.close();
}
// the third session (tests): the raw <video>s (the review of #351), at both widths in both themes
out.raw = {};
const RAW = ["line", "block", "source", "blankA", "blankB", "rowL", "rowR"];
for (const theme of ["dark", "light"]) for (const phone of [false, true]) {
  const key = (phone ? "phone" : "desktop") + theme[0].toUpperCase() + theme.slice(1);
  let ctx = null;
  try {
    ctx = await browser.newContext(phone
      ? { viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true }
      : { viewport: { width: 1100, height: 800 } });
    await ctx.addInitScript(({ t }) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, { t: THEMES[theme] });
    const page = await ctx.newPage();
    const errs = [];
    page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
    await page.goto(cfg.chat + "#only=tests");
    await page.waitForSelector('.assistant video[src^="' + cfg.host + '/demo.mp4?v=line"]', { timeout: 60000 });
    // the raw reply (the review of #351): each still by the address its clip names, its caption the first thing shown after it
    await page.waitForFunction(({ h, names }) => names.every((n) => {
      const s = document.querySelector('.assistant [src^="' + h + '/demo.mp4?v=' + n + '"]'); const v = s && s.closest("video"); return !!(v && v.readyState >= 1); }),
      { h: cfg.host, names: RAW }, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(200);
    out.raw[key] = await page.evaluate(({ h, names }) => {
      const one = (n) => {
        const s = document.querySelector('.assistant [src^="' + h + '/demo.mp4?v=' + n + '"]'), v = s && s.closest("video");
        if (!v) return { missing: true };
        const wrap = v.parentElement, cs = getComputedStyle(v), b = v.getBoundingClientRect();
        let cap = null;
        for (let x = wrap.nextSibling; x && !cap; x = x.nextSibling) {
          if (x.nodeName === "BR" || !(x.textContent || "").trim()) continue;
          if (x.nodeType === 1 && (x.querySelector("video") || x.nodeName === "P")) break;   // the next clip or paragraph: no caption
          const r = document.createRange(); r.selectNodeContents(x);
          const q = Array.from(r.getClientRects()).find((y) => y.width > 0);
          cap = q ? { left: q.left, top: q.top, text: (x.textContent || "").trim().slice(0, 40) } : { missing: true };
        }
        return { wrap: wrap.className, wrapDisplay: getComputedStyle(wrap).display, natural: [v.videoWidth, v.videoHeight],
                 video: { left: b.left, right: b.right, top: b.top, bottom: b.bottom, h: b.height },
                 style: { display: cs.display, radius: cs.borderRadius, border: [cs.borderTopWidth, cs.borderTopStyle].join(" ") }, cap };
      };
      const body = document.querySelector('.assistant [src^="' + h + '/demo.mp4?v=line"]').closest(".assistant").getBoundingClientRect();
      const out = { body: { left: body.left, right: body.right } };
      for (const n of names) out[n] = one(n);
      return out;
    }, { h: cfg.host, names: RAW });
    for (const [n, name] of [["block", "raw-caption"], ["blankA", "raw-blank-line"], ["rowL", "raw-row"]]) {
      await page.evaluate(({ h, n }) => document.querySelector('.assistant [src^="' + h + '/demo.mp4?v=' + n + '"]').closest("video").scrollIntoView({ block: "start" }), { h: cfg.host, n });
      await page.waitForTimeout(120);
      await shot(page, key + "-" + name);
    }
    out.errs["raw" + key] = errs;
  } catch (e) { out.raw[key] = { err: String(e && e.stack || e).slice(0, 600) }; }
  if (ctx) await ctx.close();
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
console.log("RESULT: ok");
"""


class ServedChatVideoPreview(unittest.TestCase):
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
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one")
        cls.lab = tempfile.mkdtemp(prefix="chat-video-")
        # the clip, filmed now
        rec = os.path.join(cls.lab, "record.mjs")
        Path(rec).write_text(RECORD)
        clip = os.path.join(cls.lab, "clip.webm")
        p = subprocess.run(["node", rec], capture_output=True, text=True, timeout=120,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), OUT_DIR=os.path.join(cls.lab, "rec"), OUT_FILE=clip))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one")
        if p.returncode != 0 or not os.path.exists(clip):
            raise AssertionError("Playwright's recorder made no clip (its ffmpeg ships with `npx playwright install chromium`):\n"
                                 + p.stdout[-1500:] + p.stderr[-1500:])
        CLIP["bytes"] = Path(clip).read_bytes()
        before = os.environ.get("CVP_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        cls.host = ThreadingHTTPServer(("127.0.0.1", 0), _Host)
        threading.Thread(target=cls.host.serve_forever, daemon=True).start()
        cls.origin2 = "http://127.0.0.1:%d" % cls.host.server_address[1]
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        os.makedirs(os.path.join(cwd, "media"), exist_ok=True)
        shutil.copyfile(clip, os.path.join(cwd, "media", "walkthrough.webm"))
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
        # web first in the shared tab order, so a plain load still opens on web; the caption legs ask for api, the raw legs
        # for tests (#only=)
        Path(state, "session-order.json").write_text(json.dumps([WEB, API, TESTS]))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        recs = [user(t0, "u0", None, "record the notes-api signup flow so the review can watch it", WEB),
                agent(t0 + 40, "a0", "u0", _reply(cls.origin2), WEB)]
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        api = [user(t0 - 7200, "v0", None, "record the checkout flow too", API),
               agent(t0 - 7160, "b0", "v0", _reply_captioned(cls.origin2), API)]
        Path(proj, API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in api))
        tests = [user(t0 - 7000, "w0", None, "record the three flows as their own clips", TESTS),
                 agent(t0 - 6960, "c0", "w0", _reply_raw(cls.origin2), TESTS)]
        Path(proj, TESTS + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in tests))
        cls.port, cls.token = _free_port(), "testtok-chatvideo"
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
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "host": cls.origin2,
                       "out": out, "shots": os.environ.get("CVP_SHOTS", "")}, f)
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
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("CVP_DUMP"):
            Path(os.environ["CVP_DUMP"]).write_text(json.dumps(result, indent=1) + "\n")
        return result

    def _leg(self, key):
        leg = self._run()["legs"][key]
        self.assertNotIn("err", leg, leg.get("err"))
        return leg

    def test_a_posted_video_is_a_still_in_the_picture_box_at_either_width_in_either_theme(self):
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            m = self._leg(key)["m"]
            table = "\n  %s: %s" % (key, json.dumps(m))
            self.assertNotIn("missing", m, "the posted <video> rendered" + table)
            self.assertEqual(m["natural"], [1920, 1080], "the clip's metadata loaded from the lab's outside host" + table)
            self.assertLessEqual(m["video"]["h"], 320.5, "within the path preview's 320px" + table)
            self.assertGreaterEqual(m["video"]["left"], m["body"]["left"] - 0.5, "inside the bubble" + table)
            self.assertLessEqual(m["video"]["right"], m["body"]["right"] + 0.5, "inside the bubble" + table)
            self.assertAlmostEqual(m["video"]["w"] / m["video"]["h"], 1920 / 1080, delta=0.03, msg="its ratio kept" + table)
            if key.startswith("desktop"):
                # the markup says width="100%": the still's own CSS auto wins, so the clip is the picture box's size, not the bubble's
                self.assertEqual(m["attrs"]["width"], "100%", "the posted markup's width attribute is still on the element" + table)
                self.assertAlmostEqual(m["video"]["w"], 569, delta=2, msg="569 wide, not bubble-wide" + table)
                self.assertAlmostEqual(m["video"]["h"], 320, delta=1.5, msg="320 tall" + table)
                self.assertLess(m["video"]["w"], m["body"]["w"] - 50, "visibly narrower than the bubble" + table)
            else:
                self.assertLessEqual(m["video"]["w"], m["body"]["w"] + 0.5, "on the phone the bubble bounds it" + table)
            self.assertEqual(m["style"]["display"], "block", table)
            self.assertEqual(m["style"]["radius"], "8px", "rounded like the path preview" + table)
            self.assertTrue(m["style"]["border"].startswith("1px solid"), "framed like the path preview" + table)
            self.assertFalse(m["attrs"]["controls"], "no native controls inline: they would fight the tap" + table)
            self.assertTrue(m["attrs"]["muted"], "muted" + table)
            self.assertTrue(m["attrs"]["paused"], "never playing by itself" + table)
            self.assertFalse(m["attrs"]["autoplay"], table)
            self.assertEqual(m["attrs"]["preload"], "metadata", "the header and the first frame, never the whole file" + table)
            self.assertTrue(m["attrs"]["src"].endswith("/clip.mp4#t=0.001"), "the first-frame fragment iOS needs" + table)
            self.assertIn("md-video-still", m["wrap"]["cls"].split(), table)
            self.assertIn("md-solo", m["wrap"]["cls"].split(), "alone in its paragraph: the box as a block" + table)
            self.assertEqual(m["wrap"]["cursor"], "zoom-in", "the zoom cursor, as a picture's" + table)
            self.assertNotIn(m["badge"]["content"], ("none", "normal"), "a play badge over the frame" + table)
            self.assertEqual((m["badge"]["w"], m["badge"]["h"]), ("44px", "44px"), table)
            self.assertLessEqual(m["page"]["docScrollW"], m["page"]["docClientW"], "the page never scrolls sideways" + table)
        light = self._leg("phoneLight")["m"]["bodyClass"].split()
        self.assertIn("theme-light", light, "the light legs ran in the light theme")
        self.assertIn("chat-theme-yatharth", light)

    def test_a_video_with_its_caption_on_the_next_line_keeps_the_box_with_the_caption_under_it(self):
        # the review of #343 (2026-10-21): the solo rule read the caption as text beside the clip, so the still went
        # inline and unframed with the caption at its bottom edge; the caption's line break leaves it alone on its line
        res = self._run()
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            m = res["caption"][key]
            table = "\n  %s: %s" % (key, json.dumps(m))
            self.assertNotIn("err", m, table)
            self.assertEqual(m["natural"], [1920, 1080], "the clip's metadata loaded" + table)
            self.assertIn("md-solo", m["wrap"].split(), "a caption on the next line leaves the clip alone on its line" + table)
            self.assertEqual(m["style"]["display"], "block", table)
            self.assertEqual(m["style"]["radius"], "8px", "rounded like the path preview" + table)
            self.assertTrue(m["style"]["border"].startswith("1px solid"), "framed like the path preview" + table)
            self.assertLessEqual(m["video"]["h"], 320.5, table)
            c = m["cap"]
            self.assertTrue(c and c.get("text", "").startswith("Clip 2"), "the caption rendered" + table)
            self.assertGreaterEqual(c["top"], m["video"]["bottom"] - 0.5, "the caption sits UNDER the clip" + table)
            self.assertLess(c["top"] - m["video"]["bottom"], 12, "…directly under it" + table)
            self.assertAlmostEqual(c["left"], m["video"]["left"], delta=1.5, msg="…at the clip's left edge" + table)
        light = res["caption"]["phoneLight"]["bodyClass"].split()
        self.assertIn("theme-light", light)
        self.assertIn("chat-theme-yatharth", light)

    def _raw(self, key):
        m = self._run()["raw"][key]
        self.assertNotIn("err", m, m.get("err"))
        return m

    def test_a_raw_video_block_with_its_caption_on_the_next_line_keeps_the_box_with_the_caption_under_it(self):
        # the review of #351 (2026-10-21): #351 read every newline of the author's HTML as a space, so a <video> written as
        # a raw HTML block (its open tag, or its <source>, on a line of its own: loose at the message's root) went inline
        # and unframed with its caption beside the clip's bottom edge. A newline there ends the clip's line unless another
        # picture or video opens the next; the one-line <video …></video>, a paragraph, keeps the box as it always has
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            r = self._raw(key)
            for name, text in (("line", "Clip 3"), ("block", "Clip 4"), ("source", "Clip 5")):
                m = r[name]
                table = "\n  %s %s: %s" % (key, name, json.dumps(m))
                self.assertNotIn("missing", m, "the clip rendered" + table)
                self.assertEqual(m["natural"], [1920, 1080], "the clip's metadata loaded" + table)
                self.assertIn("md-solo", m["wrap"].split(), "alone on its source line" + table)
                self.assertEqual(m["wrapDisplay"], "block", table)
                self.assertEqual(m["style"]["radius"], "8px", "rounded like the path preview" + table)
                self.assertTrue(m["style"]["border"].startswith("1px solid"), "framed like the path preview" + table)
                self.assertLessEqual(m["video"]["h"], 320.5, table)
                self.assertGreaterEqual(m["video"]["left"], r["body"]["left"] - 0.5, "inside the bubble" + table)
                self.assertLessEqual(m["video"]["right"], r["body"]["right"] + 0.5, "inside the bubble" + table)
                c = m["cap"]
                self.assertTrue(c and c.get("text", "").startswith(text), "the caption rendered" + table)
                self.assertGreaterEqual(c["top"], m["video"]["bottom"] - 0.5, "the caption sits UNDER the clip, not beside it" + table)
                self.assertLess(c["top"] - m["video"]["bottom"], 12, "…directly under it" + table)
                self.assertAlmostEqual(c["left"], m["video"]["left"], delta=1.5, msg="…at the clip's left edge" + table)

    def test_two_raw_video_blocks_a_blank_line_apart_each_keep_the_box_stacked(self):
        # the review of #351 (2026-10-21): two <video> blocks a blank line apart (which ends a raw HTML block in markdown's
        # source) went inline and unframed. Each stands alone: boxed, the second under the first
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            r = self._raw(key)
            a, b = r["blankA"], r["blankB"]
            table = "\n  %s: %s" % (key, json.dumps({"blankA": a, "blankB": b}))
            for name, m in (("first", a), ("second", b)):
                self.assertNotIn("missing", m, "the %s clip rendered%s" % (name, table))
                self.assertEqual(m["natural"], [1920, 1080], "the %s clip's metadata loaded%s" % (name, table))
                self.assertIn("md-solo", m["wrap"].split(), "the %s clip stands alone%s" % (name, table))
                self.assertEqual(m["wrapDisplay"], "block", table)
                self.assertTrue(m["style"]["border"].startswith("1px solid"), "…framed" + table)
            self.assertGreaterEqual(b["video"]["top"], a["video"]["bottom"] - 0.5, "the second sits UNDER the first" + table)
            self.assertAlmostEqual(b["video"]["left"], a["video"]["left"], delta=1.5, msg="…at the same left edge" + table)

    def test_two_videos_on_consecutive_lines_of_a_padded_p_stay_inline_unframed(self):
        # #351's fix, kept: in the author's HTML a newline between two clips is a space, so the README layout
        # `<p>\n<video>\n<video>\n</p>` lays them out inline, unframed (a still is the picture box's size whatever its width
        # attribute says, so two 16:9 stills at 320px tall wrap in any bubble narrower than both)
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            r = self._raw(key)
            for name in ("rowL", "rowR"):
                m = r[name]
                table = "\n  %s %s: %s" % (key, name, json.dumps(m))
                self.assertNotIn("missing", m, "the clip rendered" + table)
                self.assertEqual(m["natural"], [1920, 1080], "the clip's metadata loaded" + table)
                self.assertIn("md-video-still", m["wrap"].split(), table)
                self.assertNotIn("md-solo", m["wrap"].split(), "it shares its line with the other clip" + table)
                self.assertEqual(m["wrapDisplay"], "inline-block", "…so it stays inline" + table)
                self.assertTrue(m["style"]["border"].startswith("0px"), "…unframed" + table)
                self.assertLessEqual(m["video"]["right"], r["body"]["right"] + 0.5, "inside the bubble" + table)

    def test_a_tap_opens_the_lightbox_playing_the_clip_with_its_controls(self):
        clip = self.origin2 + "/clip.mp4"
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            lb = self._leg(key)["open"]
            self.assertIsNotNone(lb, key + ": a tap on the still opened the lightbox")
            self.assertEqual(lb["kind"], "video", key)
            self.assertEqual(lb["src"], clip, key + ": the clip from its own address, from the start (no first-frame fragment)")
            self.assertTrue(lb["controls"], key + ": played with its controls")
            self.assertFalse(lb["paused"], key + ": the tap that opened it started it: " + json.dumps(lb))
            self.assertGreater(lb["time"], 0, key + ": and it is playing")
            self.assertIn("romp-lightbox-img", lb["cls"].split(), key + ": in the picture's capped box")
            self.assertEqual(lb["ctl"]["aria"], "Open original", key + ": a web address's control opens the original")
            self.assertEqual(lb["ctl"]["href"], clip, key)
            self.assertFalse(lb["copyShown"], key + ": a video has no picture to copy")
        o = self._leg("desktopDark")["openOriginal"]
        self.assertEqual(o["opened"], [[clip, "_blank", "noopener,noreferrer"]], "the original opened in its own tab, the opener severed")
        self.assertTrue(o["stillOpen"])

    def test_escape_closes_the_lightbox_and_playback_stops(self):
        for key in ("desktopDark", "desktopLight", "phoneDark", "phoneLight"):
            leg = self._leg(key)
            self.assertTrue(leg["held"], key)
            self.assertTrue(leg["escapeGone"], key + ": Escape closes it")
            h = leg["afterEscape"]
            self.assertTrue(h["paused"], key + ": the clip it played is paused: " + json.dumps(h))
            self.assertFalse(h["connected"], key + ": and gone from the page")
            self.assertIsNone(h["src"], key + ": its source dropped, so its download ends too")

    def test_the_arrows_step_between_videos_and_the_picture_in_page_order(self):
        leg = self._leg("desktopDark")
        self.assertEqual(leg["open"]["cue"], "1/4", "the clip, the local file, the linked clip, the picture")
        r1, r2, r3, r4, l1 = leg["right1"], leg["right2"], leg["right3"], leg["right4"], leg["left1"]
        self.assertEqual(r1["kind"], "video", json.dumps(r1))
        self.assertIn("/file?path=", r1["src"], "→ the local file, through the kernel")
        self.assertIn("walkthrough.webm", r1["src"])
        self.assertEqual((r1["cue"], r1["ctl"]["aria"], r1["ctl"]["download"]), ("2/4", "Download", "walkthrough.webm"), "a file downloads under its name")
        self.assertTrue(r1["paused"], "a step onto a video leaves it paused: browsing is not watching")
        a = leg["afterStep"]
        self.assertTrue(a["paused"] and not a["connected"], "stepping away stopped the clip that was playing: " + json.dumps(a))
        self.assertEqual((r2["kind"], r2["src"], r2["cue"]), ("video", self.origin2 + "/demo.mp4", "3/4"), "→ the linked clip")
        self.assertEqual((r3["kind"], r3["src"], r3["cue"]), ("img", self.origin2 + "/form.png", "4/4"), "→ the picture after them")
        self.assertTrue(r3["copyShown"], "a picture's copy control is back")
        self.assertEqual(r4["src"], r3["src"], "the end ENDS: no wrap")
        self.assertEqual((l1["kind"], l1["src"]), ("video", self.origin2 + "/demo.mp4"), "← back to the linked clip")

    def test_a_local_video_path_previews_through_file_with_ranges(self):
        leg = self._leg("desktopDark")
        m = leg["m"]
        loc = m["local"]
        self.assertIsNotNone(loc, "the mentioned media/walkthrough.webm rendered a preview: " + json.dumps(m))
        self.assertTrue(loc["inPreview"], "in the path preview's strip")
        self.assertTrue(loc["src"].startswith("/file?path=media%2Fwalkthrough.webm&sid="), loc["src"])
        self.assertTrue(loc["src"].endswith("#t=0.001"))
        self.assertGreaterEqual(loc["ready"], 1, "its metadata loaded through /file")
        self.assertEqual([loc["w"], loc["h"]], [1920, 1080])
        self.assertLessEqual(loc["rect"]["h"], 320.5)
        self.assertTrue(m["stripAfterPara"], "the previews render after the paragraph that names them")
        files = leg["files"]
        self.assertTrue(files, "the browser fetched the clip through /file")
        self.assertIn(206, [f["status"] for f in files], "the browser's media requests were answered with a range: " + json.dumps(files))
        p = leg["probe"]
        self.assertEqual((p["status"], p["range"], p["accept"], p["type"], p["len"]),
                         (206, "bytes 0-1/%d" % len(CLIP["bytes"]), "bytes", "video/webm", 2),
                         "Safari's opening probe, a closed range, gets exactly its two bytes: " + json.dumps(p))
        lo = leg["localOpen"]
        self.assertEqual(lo["kind"], "video")
        self.assertIn("walkthrough.webm", lo["src"])
        self.assertNotIn("#", lo["src"], "the lightbox plays the file from the start")
        self.assertFalse(lo["paused"], "and the click started it")

    def test_a_plain_video_link_gets_the_preview_below_its_paragraph(self):
        leg = self._leg("desktopDark")
        ln = leg["m"]["linked"]
        self.assertIsNotNone(ln, "the link to demo.mp4 rendered a still: " + json.dumps(leg["m"]))
        self.assertTrue(ln["inPreview"])
        self.assertEqual(ln["data"], self.origin2 + "/demo.mp4")
        self.assertEqual([ln["w"], ln["h"]], [1920, 1080], "its metadata loaded")
        self.assertLessEqual(ln["rect"]["h"], 320.5)
        lo = leg["linkOpen"]
        self.assertEqual((lo["kind"], lo["src"]), ("video", self.origin2 + "/demo.mp4"))
        self.assertFalse(lo["paused"])
        self.assertEqual(lo["ctl"]["aria"], "Open original")

    def test_no_page_errors(self):
        r = self._run()
        for k, errs in r["errs"].items():
            self.assertEqual(errs, [], k)


if __name__ == "__main__":
    unittest.main()
