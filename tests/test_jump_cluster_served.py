#!/usr/bin/env python3
"""The chat's jump cluster on the real /chat page (the user 2026-09-24, paraphrased: after new content arrives they lose
their place and want to get back to their last message and read everything since, and reach comments without hunting on
the rail). One narrow column in the pane's bottom-left corner, three capsules, each its up chevron, an icon and its down
chevron: the threads with an unread reply (and how many there are; it replaced the "replies unread" chips), every
comment thread, and the reader's own messages.

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges, more than one wire tail
(250 events), so the page opens holding exchanges 25..149 with the older ones on the server, and renders only the last 80
units of those (the rest in a spacer). Five comment threads: on reply 3 (history the page has not loaded; unread), 25
(loaded, outside the rendered window; read), 90 (resolved), 140 (unread) and 145 (unread). A second session, api, is two
lines long. Asserted in the browser, each move by the deep-link flash landing on the expected turn:
  the column's layout (order, labels, icons, 24px wide, 204px tall, the chip's slot held below, left-aligned with it);
  from the bottom, previous lands on the last message of the reader's own, previous again on the one before, next back;
  previous comment walks 145, 140, 90 and 25 (the last two outside the rendered window); from reply 25, previous message
  lands on prompt 25 and previous again on prompt 24, which the page did not hold (asked for through the page's own gap
  loader, landed once it arrived); previous unread lands reply 3, fetched by the landing road; next unread (by key, then
  by click) walks 140 and 145, where none is left below; previous unread by key lands 140; neither marks a thread read;
  the count is every unread thread in the session, wherever it sits (the user 2026-10-15, who watched it go 5, 4, 5 as
  threads scrolled in and out of view with nothing read): a landing that brings one into view, a scroll that takes it
  out and brings it back, none of them moves the number; opening a thread takes one off and a reply landing on it puts
  it back; the keys for messages and comments do what the buttons do; the api tab,
  whose transcript fits, shows no cluster; on a 390x844 phone all three capsules show, 34px wide with 32x40 chevrons, and
  the column clears the message box. The go-to-bottom chip is the column's foot (the user 2026-09-25, paraphrased: the
  same width as the column, in its look): shown, it measures the column's width (24 on desktop, 34 on the phone), 30 and 42
  tall (the phone's 40 inside the hairline, the arrows' touch size), left-aligned, one capsule gap below the column, and
  the page computes the same surface, hairline, shadow and radius for it as for a capsule, in the dark theme and the
  light, with the arrows' chevron at their size, stroke and resting glow; scrolled up on the phone, column and chip
  together sit between the pane's top and the message box. A question waiting at the bottom (the user 2026-10-18,
  paraphrased: the corner's pills had no room only then): its picker card keeps the thread's left gutter, so the column
  covers neither the card nor a line of its text, on the desktop and the phone, in either theme; and with a long unbroken
  path in the question, the question wraps inside the card rather than running past its right edge.
A second lab (ServedHostOfflineFoot, two kernels): a session on a checked-in TESTHOST whose link drops shows the
host-offline foot under its last turn with no other event, and the foot keeps the thread's left gutter, so neither the
column nor the go-to-bottom chip covers it, at the bottom and a few lines up, on the desktop and the phone, in either theme.
As the foot comes up, a reader at the bottom stays there with the whole foot in view, a reader scrolled up is not moved, and
a transcript that fit its pane is not yanked as the foot overflows it, the go-to-bottom chip coming up for it at once.
With JUMP_SHOTS=<dir> the driver writes the cluster in the dark theme, the light theme and the phone layout (a coarse
pointer), each also a screen up with the go-to-bottom chip showing (*-up-corner.png), the corner with the question's
card up (ask-*.png, phone-ask-*.png; ask-long-*.png with the long path), and the foot's corner a few lines up
(foot-*.png, phone-foot-*.png). With JUMP_SHOTS_ONLY=1 it writes
the shots after load and stops, for a before shot of an older build. Skips LOUDLY without the extension deps or a
Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose,
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

WEB = "aaaaaaaa-1111-2222-3333-888888888888"
API = "aaaaaaaa-1111-2222-3333-999999999999"
N = 150
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
THREADS = [("tid-a", 3, "unread"), ("tid-b", 25, "read"), ("tid-d", 90, "resolved"), ("tid-c", 140, "unread"), ("tid-e", 145, "unread")]
THREAD_SID = "dddddddd-1111-2222-3333-%012d"
CHIP_H, PHONE_CHIP_H, GAP = 30, 42, 6   # the go-to-bottom chip's height on each layout, and the gap between capsules


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def exact(k):
    return "The notes-api export for step %d streams rows through a bounded buffer." % k


def reply_body(k):
    return exact(k) + "\n\nStep %d also keeps the header written once, and the retry cap stays at two minutes." % k


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const U = (k) => cfg.userUuid.replace("%012d", String(k).padStart(12, "0"));
const R = (k) => cfg.replyUuid.replace("%012d", String(k).padStart(12, "0"));
const out = { steps: {} };
const ctx = await browser.newContext({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
const page = await ctx.newPage();
await page.addInitScript(({ sid, n }) => {
  window.__cmtFrames = 0; window.__lastCmt = null;
  window.addEventListener("message", (e) => { const m = e.data; if (m && m.type === "comments" && m.id === sid && (m.threads || []).length >= n) window.__cmtFrames++;
    if (m && m.type === "comments" && m.id === sid && !m.__synth) window.__lastCmt = m; }, true);
  // a reply landing on a thread: the newest real frame again, that thread's reply unread (what the kernel's frame says
  // when one lands), then the count as the page shows it at once, before any later frame of the kernel's can move it
  window.__replyLands = (tid) => {
    if (!window.__lastCmt) return null;
    const f = JSON.parse(JSON.stringify(window.__lastCmt));
    for (const t of f.threads || []) if (t.tid === tid) t.unread = true;
    window.dispatchEvent(new MessageEvent("message", { data: Object.assign(f, { __synth: true }) }));
    const c = document.querySelector("#jump-cluster .jc-count");
    return c.hidden ? "" : c.textContent.trim();
  };
}, { sid: cfg.sid, n: cfg.threads });
const settle = async (sel) => {
  await page.waitForSelector("#tabs .tab, #tabs [data-sid]", { timeout: 20000 });
  await page.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
  const ticks = () => page.evaluate((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.minTicks);
  if (!(await ticks())) { const n0 = await page.evaluate(() => window.__cmtFrames); await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 45000 }).catch(() => {}); }
  await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.minTicks, { timeout: 30000 }).catch(() => {});
  if (sel) await page.waitForSelector(sel, { timeout: 30000 });
  await page.waitForTimeout(500);
};
const shoot = async (p, name) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await p.mouse.move(500, 200);
  await p.waitForTimeout(200);
  await p.screenshot({ path: cfg.shots + "/" + name + ".png" });
  const c = await p.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { l: r.left, b: r.bottom }; });
  await p.screenshot({ path: cfg.shots + "/" + name + "-corner.png", clip: { x: Math.max(0, c.l), y: Math.max(0, c.b - 300), width: 260, height: 300 } });
};
// the go-to-bottom chip shows only off the bottom: a screen up puts it beside the column, and its own click brings the view
// back down (the follow re-entry, so a run that shot this carries on from the bottom as one that did not)
const scrollUp = async (p) => {
  await p.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = Math.max(0, c.scrollTop - 600); });
  await p.waitForSelector("#jump-bottom:not([hidden])", { timeout: 10000 }).catch(() => {});
  await p.waitForTimeout(400);
};
const backDown = async (p) => {
  await p.click("#jump-bottom").catch(() => {});
  await p.waitForSelector("#jump-bottom", { state: "hidden", timeout: 10000 }).catch(() => {});
  await p.waitForTimeout(300);
};
// the corner with the column and the chip both in frame, in each theme (the view already scrolled up)
const upCorner = async (p, prefix, themes) => {
  if (!cfg.shots) return;
  fs.mkdirSync(cfg.shots, { recursive: true });
  await p.mouse.move(300, 60);
  for (const th of themes) {
    await p.evaluate((light) => document.body.classList.toggle("theme-light", light), th === "light");
    await p.waitForTimeout(300);
    const clip = await p.evaluate(() => {
      const a = document.getElementById("jump-cluster").getBoundingClientRect(), b = document.getElementById("jump-bottom").getBoundingClientRect();
      const top = Math.max(0, Math.min(a.top, b.top) - 28);
      return { x: 0, y: top, width: 240, height: Math.max(a.bottom, b.bottom) + 24 - top };
    });
    await p.screenshot({ path: cfg.shots + "/" + prefix + th + "-up-corner.png", clip });
  }
  await p.evaluate(() => document.body.classList.remove("theme-light"));
  await p.waitForTimeout(200);
};
const openWeb = async (p) => {   // the web tab is the long one; the strip may open on either (and the phone folds the strip away)
  await p.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
  await p.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
};
// A QUESTION WAITING ON THE READER (the user 2026-10-18, paraphrased: the corner's pills had no room only when a question was
// waiting). The picker card is the transcript's tail, the row beside the column, and it sits beside the threads rather than
// inside one. At the bottom of the transcript the lab dispatches the kernel's own askLive frame (the shape the SDK backend
// pushes), measures the column against the card and each line of its text in the dark theme and the light, shoots the corner,
// and clears the question. The kernel's own question frames for the session are held back, so none clears the lab's mid-run.
const ASK = { kind: "single", header: "Release", question: "Ship the fix to the notes-api exporter now, or hold it for the next release?",
  options: [{ n: 1, label: "Ship it (Recommended)", desc: "Merge on green and release behind the flag today.", selected: true },
            { n: 2, label: "Hold for now", selected: false }, { n: 3, label: "Type something.", selected: false }],
  cursor: 1, cursorFound: true, multiSelect: false, sig: "lab-ask-1" };
const holdAsks = (p) => p.addInitScript((sid) => {
  window.addEventListener("message", (e) => { const m = e.data;
    if (m && (m.type === "askLive" || m.type === "askLiveClear") && m.id === sid && !m.__lab) e.stopImmediatePropagation(); }, true);
}, cfg.sid);
const lab = (p, m) => p.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: { ...m, __lab: true } })); }, m);
const ASKGEO = () => {
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  const box = document.getElementById("jump-cluster"), card = document.querySelector("#live-ask .ask-card");
  const th = Array.from(document.querySelectorAll("#content .thread")).find((e) => e.getClientRects().length > 0);
  return { shown: !box.hidden && getComputedStyle(box).display !== "none", column: R(box), card: card ? R(card) : null,
           gutter: th ? th.getBoundingClientRect().left + parseFloat(getComputedStyle(th).paddingLeft) : null,
           texts: card ? Array.from(card.querySelectorAll(".ask-qtext, .ask-optlabel, .ask-optdesc, .ask-custom-plus, .ask-custom-hint-text"))
                           .map((e) => ({ cls: e.className, text: e.textContent.slice(0, 24), ...R(e) })) : [] };
};
// A LONG UNBROKEN TOKEN in the question (2026-10-18, the review of #306): a path or URL ran past the card's right border and was
// clipped at the pane's edge, worse once the card took the gutter. The same card with an invented path in its question: the text
// as laid out (a range over the question's contents) stays inside the card, and the question's box has nothing to scroll.
const ASK_LONG = { ...ASK, sig: "lab-ask-2",
  question: "Overwrite notes-api/services/exporter/fixtures/regenerated_after_the_nightly_sweep/export_rows_through_a_bounded_buffer_with_the_header_written_once_and_the_retry_cap_at_two_minutes_step_149.csv with the regenerated fixture?" };
const LONGGEO = () => {
  const card = document.querySelector("#live-ask .ask-card"), q = card && card.querySelector(".ask-qtext");
  if (!q) return null;
  const range = document.createRange(); range.selectNodeContents(q);
  const R = (r) => ({ l: r.left, t: r.top, r: r.right, b: r.bottom });
  return { card: R(card.getBoundingClientRect()), text: R(range.getBoundingClientRect()), lines: range.getClientRects().length,
           scrollW: q.scrollWidth, clientW: q.clientWidth, has: q.textContent.includes("step_149.csv") };
};
const askStep = async (p, prefix) => {
  await backDown(p);
  await lab(p, { type: "askLive", id: cfg.sid, ask: ASK });
  const up = await p.waitForSelector("#live-ask .ask-card", { timeout: 10000 }).then(() => true).catch(() => false);
  await p.waitForTimeout(500);   // the reveal's scroll and the column's placement settle
  const res = { up };
  await p.mouse.move(300, 60);
  for (const th of ["dark", "light"]) {   // the default dark, and the light theme as the gear applies it (theme.ts: both classes)
    await p.evaluate((light) => { document.body.classList.toggle("theme-light", light); document.body.classList.toggle("chat-theme-yatharth", light); }, th === "light");
    await p.waitForTimeout(300);
    await p.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });   // the theme's own strip can move the tail
    await p.waitForTimeout(300);
    res[th] = await p.evaluate(ASKGEO);
    const g = res[th];
    if (cfg.shots && g.card) {
      fs.mkdirSync(cfg.shots, { recursive: true });
      const c = await p.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { w: r.width, b: r.bottom }; });
      const top = Math.max(0, Math.min(g.column.t, g.card.t) - 40);
      await p.screenshot({ path: cfg.shots + "/" + prefix + "ask-" + th + ".png", clip: { x: 0, y: top, width: Math.min(c.w, 640), height: c.b - top } });
    }
  }
  await lab(p, { type: "askLive", id: cfg.sid, ask: ASK_LONG });
  await p.waitForFunction(() => { const q = document.querySelector("#live-ask .ask-qtext"); return !!q && q.textContent.includes("step_149"); }, null, { timeout: 10000 }).catch(() => {});
  res.long = {};
  for (const th of ["dark", "light"]) {
    await p.evaluate((light) => { document.body.classList.toggle("theme-light", light); document.body.classList.toggle("chat-theme-yatharth", light); }, th === "light");
    await p.waitForTimeout(300);
    await p.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });
    await p.waitForTimeout(300);
    const g = res.long[th] = await p.evaluate(LONGGEO);
    if (cfg.shots && g) {
      fs.mkdirSync(cfg.shots, { recursive: true });
      const c = await p.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { w: r.width, b: r.bottom }; });
      const top = Math.max(0, g.card.t - 40);
      await p.screenshot({ path: cfg.shots + "/" + prefix + "ask-long-" + th + ".png", clip: { x: 0, y: top, width: c.w, height: c.b - top } });
    }
  }
  await p.evaluate(() => { document.body.classList.remove("theme-light", "chat-theme-yatharth"); });
  await lab(p, { type: "askLiveClear", id: cfg.sid });
  await p.waitForSelector("#live-ask .ask-card", { state: "detached", timeout: 10000 }).catch(() => {});
  return res;
};
await holdAsks(page);
await page.goto(cfg.chat);
await openWeb(page);
await settle(cfg.shotsOnly ? null : "#jump-cluster:not([hidden])");
if (cfg.shotsOnly) {
  await shoot(page, "dark");
  await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
  await shoot(page, "light");
  await page.evaluate(() => document.body.classList.remove("theme-light"));
  await scrollUp(page); await upCorner(page, "", ["dark", "light"]);
  await askStep(page, "");
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  const pp = await phone.newPage(); await holdAsks(pp); await pp.goto(cfg.chat); await openWeb(pp);
  await pp.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 }); await pp.waitForTimeout(4000);
  if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone.png" });
  await scrollUp(pp); await upCorner(pp, "phone-", ["dark", "light"]);
  if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone-up.png" });
  await askStep(pp, "phone-");
  fs.writeSync(1, "RESULT:" + JSON.stringify({ shotsOnly: true }) + "\n"); await browser.close(); process.exit(0);
}
// every deep-link flash as it lands: the uuid of the turn wearing it (the flash is a 1.6s class; a late wait can miss it)
await page.evaluate(() => {
  window.__flashed = [];
  new MutationObserver((recs) => { for (const r of recs) {
    const el = r.target;
    if (r.attributeName === "class" && el.classList && el.classList.contains("anchor-flash")) { const t = el.closest(".turn"); window.__flashed.push(t ? t.dataset.uuid : null); }
  } }).observe(document.getElementById("content"), { subtree: true, attributes: true, attributeFilter: ["class"] });
});
// the column as the page lays it out: each capsule's box, each control's state, the count, the chip below
const LAYOUT = () => {
  const box = document.getElementById("jump-cluster");
  const b = (m) => { const e = box.querySelector(`[data-move="${m}"]`); return e ? { disabled: !!e.disabled, aria: e.getAttribute("aria-label"), w: e.offsetWidth, h: e.offsetHeight } : null; };
  const g = (cls) => { const e = box.querySelector(cls); const r = e.getBoundingClientRect(); return { shown: !e.hidden && e.offsetParent !== null, l: r.left, t: r.top, w: r.width, h: r.height, label: e.getAttribute("aria-label"),
                        icon: !!e.querySelector(".jc-icon svg"), order: Array.from(e.children).map((k) => k.className.split(" ")[0]) }; };
  const r = box.getBoundingClientRect(), c = document.getElementById("content").getBoundingClientRect();
  const jb = document.getElementById("jump-bottom"), jr = jb.getBoundingClientRect();
  const cnt = box.querySelector(".jc-count"), cr = cnt.getBoundingClientRect();
  const comp = document.getElementById("composer"), kr = comp ? comp.getBoundingClientRect() : null;
  return { hidden: box.hidden, prevMine: b("prevMine"), nextMine: b("nextMine"), prevComment: b("prevComment"), nextComment: b("nextComment"),
           prevUnread: b("prevUnread"), nextUnread: b("nextUnread"),
           unread: g(".jc-unread"), cmt: g(".jc-cmt"), mine: g(".jc-mine"),
           count: { shown: !cnt.hidden, text: cnt.textContent.trim(), l: cr.left, t: cr.top, r: cr.right, pointer: getComputedStyle(cnt).pointerEvents },
           rect: { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }, content: { l: c.left, t: c.top, b: c.bottom },
           composerTop: kr ? kr.top : null, jump: { hidden: jb.hidden, l: jr.left, t: jr.top, r: jr.right, b: jr.bottom, w: jr.width, h: jr.height },
           chips: !!document.getElementById("reply-chips") };
};
// the go-to-bottom chip's dress beside a capsule's, as the page computes them in the current theme (the chip shown, the
// pointer elsewhere): the surface, the hairline, the shadow, the radius, and the chevron's size, stroke and resting glow
const DRESS = () => {
  const box = (e) => { const s = getComputedStyle(e); return { bg: s.backgroundColor, bw: s.borderTopWidth, bs: s.borderTopStyle, bc: s.borderTopColor, shadow: s.boxShadow, radius: s.borderTopLeftRadius }; };
  const glyph = (e) => { const r = e.getBoundingClientRect(), pl = e.querySelector("polyline");
    return { w: r.width, h: r.height, stroke: e.getAttribute("stroke-width"), vb: e.getAttribute("viewBox"), points: pl ? pl.getAttribute("points") : null, opacity: getComputedStyle(e).opacity }; };
  const jb = document.getElementById("jump-bottom"), g = document.querySelector("#jump-cluster .jc-group:not([hidden])");
  const down = document.querySelector('#jump-cluster .jc-group:not([hidden]) .jc-btn[data-move^="next"]:not(:disabled) svg');   // a live down arrow (a disabled one rests at 0.2)
  return { jump: box(jb), group: box(g), jumpGlyph: glyph(jb.querySelector("svg")), arrowGlyph: down ? glyph(down) : null };
};
const state = () => page.evaluate(LAYOUT);
const move = async (name, how, want, ms = 15000) => {
  await page.evaluate(() => { window.__flashed = []; });
  if (how === "click") await page.click(`#jump-cluster [data-move="${name}"]`);
  else await page.keyboard.press(how);
  let ok = true;
  try { await page.waitForFunction((u) => window.__flashed.includes(u), want, { timeout: ms }); } catch (e) { ok = false; }
  await page.waitForTimeout(350);   // the landing's settle
  const got = await page.evaluate((u) => { const c = document.getElementById("content").getBoundingClientRect(); const t = document.querySelector(`.turn[data-uuid="${u}"]`);
    const r = t ? t.getBoundingClientRect() : null; return { flashed: window.__flashed.slice(), inView: !!r && r.bottom > c.top && r.top < c.bottom, top: r ? Math.round(r.top - c.top) : null }; }, want);
  out.steps[name + ":" + how + ":" + want.slice(-4)] = { ok, want, ...got };
  return ok;
};
out.start = await state();
await shoot(page, "dark");
await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
await shoot(page, "light");
await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
if (cfg.shots) { await scrollUp(page); await upCorner(page, "", ["dark", "light"]); await backDown(page); }
out.order = [];
const readState = (tid) => page.evaluate((t) => { const m = document.querySelector(`mark.cmt-hl[data-tid="${t}"]`); return { unread: m ? m.classList.contains("unread") : null, pop: !!document.getElementById("cmt-pop") }; }, tid);
const seq = [
  // your own messages, from the bottom
  ["prevMine", "click", U(149)], ["prevMine", "click", U(148)], ["nextMine", "click", U(149)],
  // every comment: 145, 140, 90 and 25 (the last two outside the rendered window)
  ["prevComment", "click", R(145)], ["prevComment", "click", R(140)], ["prevComment", "click", R(90)], ["prevComment", "click", R(25)],
  // prompt 25, then prompt 24, which the page does not hold: loaded, then landed
  ["prevMine", "click", U(25)], ["prevMine", "click", U(24), 40000],
  // the unread replies: reply 3 is in history the page has not loaded (outside the loaded window), then down and back up
  ["prevUnread", "click", R(3), 40000], ["nextUnread", "Control+Alt+PageDown", R(140)], ["nextUnread", "click", R(145)],
];
for (const [m, how, want, ms] of seq) { out.order.push(m + ">" + want.slice(-4)); if (!(await move(m, how, want, ms))) break; }
if (Object.values(out.steps).every((s) => s.ok)) {
  out.atLast = await state();   // on reply 145: no unread thread is below it
  out.read145 = await readState("tid-e");
  await page.mouse.move(500, 300);
  await page.waitForTimeout(200);   // the glyphs' 120ms glow back to rest
  out.dress = { dark: await page.evaluate(DRESS) };
  await page.evaluate(() => document.body.classList.add("theme-light")); await page.waitForTimeout(300);
  out.dress.light = await page.evaluate(DRESS);
  await page.evaluate(() => document.body.classList.remove("theme-light")); await page.waitForTimeout(200);
  await move("prevUnread", "Control+Alt+PageUp", R(140));
  out.read140 = await readState("tid-c");
  await move("prevMine", "Control+Alt+ArrowUp", U(140));
  await move("prevComment", "Control+Alt+Shift+ArrowUp", R(90));
  // the count holds still while only the view moves (the user 2026-10-15): the next unread lands reply 140 in view, a
  // screen up takes it out of view, a screen down brings it back
  const count = () => page.evaluate(() => { const c = document.querySelector("#jump-cluster .jc-count"); return c.hidden ? "" : c.textContent.trim(); });
  const inView = (u) => page.evaluate((u) => { const c = document.getElementById("content").getBoundingClientRect(), t = document.querySelector(`.turn[data-uuid="${u}"]`);
    const r = t ? t.getBoundingClientRect() : null; return !!r && r.bottom > c.top && r.top < c.bottom; }, u);
  // the reader's own gesture, the wheel over the transcript: a landing re-aligns its target while the transcript settles
  // until the reader's own input takes over, so a bare scrollTop write would be put back
  const scrollBy = async (dy) => {
    const c = await page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
    await page.mouse.move(c.x, c.y); await page.mouse.wheel(0, dy); await page.waitForTimeout(500);
  };
  out.counts = { onLast: out.atLast.count.text };
  await move("nextUnread", "click", R(140));
  out.counts.landed = await count(); out.counts.landedInView = await inView(R(140));
  const screen = await page.evaluate(() => document.getElementById("content").clientHeight + 120);
  await scrollBy(-screen);
  out.counts.away = await count(); out.counts.awayInView = await inView(R(140));
  await scrollBy(screen);
  out.counts.back = await count(); out.counts.backInView = await inView(R(140));
  // opening the thread reads it: one fewer; closed, a reply landing on it makes it unread again: one more
  await page.click('mark.cmt-hl[data-tid="tid-c"]');
  await page.waitForSelector("#cmt-pop", { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(300);
  out.counts.opened = await count();
  await page.click('#cmt-pop [data-act="cmtclose"]').catch(() => {});
  await page.waitForSelector("#cmt-pop", { state: "detached", timeout: 10000 }).catch(() => {});
  out.counts.closed = await count();
  // the reply lands on the kernel's own frame that counts the read (seenSeq, 2026-10-15): the page holds a read until a frame
  // does, so a frame from before it is no reply after it
  await page.waitForFunction(() => ((window.__lastCmt && window.__lastCmt.threads) || []).some((t) => t.tid === "tid-c" && t.seenSeq >= 1),
                             null, { timeout: 15000 }).catch(() => {});
  out.counts.reply = await page.evaluate(() => window.__replyLands("tid-c"));
  out.counts.replyInView = await inView(R(140));
}
// a question waiting at the bottom: the column covers none of its card
out.ask = await askStep(page, "");
// the api tab: a transcript that fits shows no cluster
await page.evaluate((sid) => { const t = document.querySelector(`#tabs .tab[data-id="${sid}"]`); if (t) t.click(); }, cfg.api);
await page.waitForTimeout(1500);
out.api = await page.evaluate(() => ({ hidden: document.getElementById("jump-cluster").hidden, fits: (() => { const c = document.getElementById("content"); return c.scrollHeight <= c.clientHeight + 2; })() }));
// the phone layout: a coarse pointer, all three capsules showing (three replies wait unread at the bottom)
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const pp = await phone.newPage();
await holdAsks(pp);
await pp.goto(cfg.chat);
await openWeb(pp);
await pp.waitForSelector("#jump-cluster:not([hidden]) .jc-unread:not([hidden])", { timeout: 60000 }).catch(() => {});
await pp.waitForTimeout(1500);
out.phone = await pp.evaluate(LAYOUT);
out.phone.coarse = await pp.evaluate(() => matchMedia("(pointer: coarse)").matches);
if (cfg.shots) { await pp.screenshot({ path: cfg.shots + "/phone.png" }); const c = await pp.evaluate(() => document.getElementById("content").getBoundingClientRect().bottom);
  await pp.screenshot({ path: cfg.shots + "/phone-corner.png", clip: { x: 0, y: Math.max(0, c - 440), width: 200, height: 440 + 120 } }); }
// a screen up on the phone: the chip shows under the column, and the pair must still fit between the pane's top and the message box
await scrollUp(pp);
out.phoneUp = await pp.evaluate(LAYOUT);
out.phoneDress = { dark: await pp.evaluate(DRESS) };
await pp.evaluate(() => document.body.classList.add("theme-light")); await pp.waitForTimeout(300);
out.phoneDress.light = await pp.evaluate(DRESS);
await pp.evaluate(() => document.body.classList.remove("theme-light")); await pp.waitForTimeout(200);
await upCorner(pp, "phone-", ["dark", "light"]);
if (cfg.shots) await pp.screenshot({ path: cfg.shots + "/phone-up.png" });
// the phone, back at the bottom, with a question waiting: the wider column covers none of its card either
out.phoneAsk = await askStep(pp, "phone-");
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedJumpCluster(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="jump-cluster-")
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
        for sid, name, colour in ((WEB, "web", "#9cd2ff\t#0c1a2e"), (API, "api", "#1EA1EB\t#ffffff")):
            Path(state, "names", sid).write_text("%s\t%s\t%s\n" % (name, cwd, colour))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
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
        short = [{"type": "user", "timestamp": iso(t0), "uuid": "a-u1", "parentUuid": None, "promptSource": "typed", "sessionId": API,
                  "message": {"role": "user", "content": "notes-api: is the api healthy?"}},
                 {"type": "assistant", "timestamp": iso(t0 + 5), "uuid": "a-a1", "parentUuid": "a-u1", "sessionId": API,
                  "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn", "content": [{"type": "text", "text": "Yes, all green."}]}}]
        Path(proj, API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in short))
        rows = []
        for i, (tid, k, st) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 1)
            anchor = REPLY_UUID % k
            t = t0 + 600 * N + 60 * i
            hist = [dict(r, sessionId=tsid) for r in recs[:2 * k + 2]]
            hist += [{"type": "user", "timestamp": iso(t), "uuid": "c%d" % i, "parentUuid": anchor, "promptSource": "typed",
                      "sessionId": tsid, "message": {"role": "user", "content": "why a bounded buffer at step %d?" % k}},
                     {"type": "assistant", "timestamp": iso(t + 20), "uuid": "r%d" % i, "parentUuid": "c%d" % i, "sessionId": tsid,
                      "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                  "content": [{"type": "text", "text": "So a slow client never holds the whole export in memory."}]}}]
            Path(proj, tsid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
            Path(state, "sdk", tsid + ".json").write_text(json.dumps(
                {"sid": tsid, "name": "web-comment-%d" % (i + 1), "cwd": cwd, "lastSid": tsid, "threadOf": WEB, "forkAt": anchor,
                 "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
            row = {"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                   "exact": exact(k), "status": "resolved" if st == "resolved" else "open", "createdT": t}
            if st != "unread":
                row["lastSeenT"] = int(time.time()) + 86400
            rows.append(row)
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-jumpcluster"
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

    _result = None

    def _drive(self):
        """The driver runs once for the class; each test reads its own part of the one result."""
        if ServedJumpCluster._result is None:
            ServedJumpCluster._result = self._run_driver()
        return ServedJumpCluster._result

    def _run_driver(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "sid": WEB, "api": API,
                       "threads": len(THREADS), "minTicks": 3, "userUuid": USER_UUID, "replyUuid": REPLY_UUID,
                       "shots": os.environ.get("JUMP_SHOTS", ""), "shotsOnly": os.environ.get("JUMP_SHOTS_ONLY") == "1"}, f)
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

    def test_each_control_lands_its_turn_including_history_the_page_did_not_hold(self):
        r = self._drive()
        if r.get("shotsOnly"):
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        s0 = r["start"]
        # at the bottom of a long transcript: one narrow column of three capsules above the chip's slot, the old chips gone
        self.assertFalse(s0["hidden"], s0)
        self.assertFalse(s0["chips"], "the reply chips are gone: %r" % s0)
        for g, label in (("unread", "Unread replies"), ("cmt", "Comments"), ("mine", "Your messages")):
            self.assertTrue(s0[g]["shown"], "%s capsule shows: %r" % (g, s0[g]))
            self.assertEqual(s0[g]["label"], label, "each capsule names what it walks: %r" % s0[g])
            self.assertTrue(s0[g]["icon"], "an icon between its chevrons: %r" % s0[g])
            self.assertEqual(s0[g]["order"], ["jc-btn", "jc-icon", "jc-btn"], "up, icon, down: %r" % s0[g])
            self.assertEqual((s0[g]["l"], s0[g]["w"]), (14, 24), "left-aligned at the chip's 14px, 24px wide: %r" % s0[g])
            self.assertEqual(s0[g]["h"], 64, "a capsule: two 22px chevrons, an 18px icon row, the hairline: %r" % s0[g])
        self.assertLess(s0["unread"]["t"], s0["cmt"]["t"], "unread on top")
        self.assertLess(s0["cmt"]["t"], s0["mine"]["t"], "comments above your messages")
        self.assertEqual(s0["cmt"]["t"] - (s0["unread"]["t"] + s0["unread"]["h"]), 6, "a small gap between capsules")
        self.assertEqual((s0["rect"]["w"], s0["rect"]["h"]), (24, 204), "the column: 24 wide, three capsules 204 tall: %r" % s0["rect"])
        self.assertTrue(s0["jump"]["hidden"], "at the bottom the go-to-bottom chip is down: %r" % s0)
        self.assertLess(abs(s0["rect"]["b"] - (s0["content"]["b"] - 8 - CHIP_H - GAP)), 1, "the chip's slot held below, though the chip is down: %r" % s0)
        # the controls: at the bottom nothing of yours, no comment and no unread reply is below
        for m, dis in (("prevMine", False), ("nextMine", True), ("prevComment", False), ("nextComment", True), ("prevUnread", False), ("nextUnread", True)):
            self.assertEqual(s0[m]["disabled"], dis, "%s at the bottom: %r" % (m, s0[m]))
        self.assertEqual(s0["prevUnread"]["aria"], "Previous unread reply (Ctrl+Alt+PageUp)", "the tip names the key: %r" % s0["prevUnread"])
        self.assertEqual(s0["nextUnread"]["aria"], "Next unread reply (Ctrl+Alt+PageDown)", s0["nextUnread"])
        # the count: the three unread replies, a badge over the empty margin, never in a finger's way
        self.assertEqual((s0["count"]["shown"], s0["count"]["text"]), (True, "3"), s0["count"])
        self.assertLess(s0["count"]["l"], s0["unread"]["l"], "on the capsule's upper-left corner, over the margin: %r" % s0["count"])
        self.assertLessEqual(s0["count"]["r"], s0["unread"]["l"] + s0["unread"]["w"], "the column is not widened: %r" % s0["count"])
        self.assertEqual(s0["count"]["pointer"], "none", s0["count"])
        # every move landed its turn (the deep-link flash on the expected uuid), in order
        for name, st in r["steps"].items():
            self.assertTrue(st["ok"], "%s did not land %s: %r (order %r)" % (name, st["want"], st, r["order"]))
            self.assertTrue(st["inView"], "%s: the landed turn is in view: %r" % (name, st))
        self.assertEqual(len(r["steps"]), 16, "all sixteen moves ran: %r" % list(r["steps"]))
        # on reply 145, the last unread thread: none left below, and the chip is up while the column did not move for it
        s1 = r["atLast"]
        self.assertTrue(s1["nextUnread"]["disabled"], "no unread reply is left below: %r" % s1["nextUnread"])
        self.assertFalse(s1["prevUnread"]["disabled"], s1)
        self.assertFalse(s1["jump"]["hidden"], s1)
        self.assertEqual((s1["rect"]["b"], s1["rect"]["l"]), (s0["rect"]["b"], s0["rect"]["l"]), "the column never moves as the chip comes and goes: %r vs %r" % (s1["rect"], s0["rect"]))
        self.assertLess(abs(s1["jump"]["t"] - s1["rect"]["b"] - GAP), 0.5, "the chip one capsule gap below the column: %r" % s1)
        self.assertEqual(s1["jump"]["l"], s1["rect"]["l"], "left-aligned with the chip: %r" % s1)
        # the chip is the column's foot: its width exactly, a short capsule a touch taller than wide
        self.assertEqual((s1["jump"]["w"], s1["jump"]["h"]), (s1["rect"]["w"], CHIP_H), "the chip: the column's width, %dpx tall: %r" % (CHIP_H, s1))
        self.assertEqual(s1["jump"]["w"], 24, s1)
        # one dress in either theme: the page computes the chip's surface, hairline, shadow and radius as a capsule's, and
        # its chevron is the arrows' own, at their size, stroke and resting glow
        for theme in ("dark", "light"):
            self._same_dress(r["dress"][theme], 14, "0.55", theme)
        self.assertNotEqual(r["dress"]["dark"]["jump"]["bg"], r["dress"]["light"]["jump"]["bg"], "the themes differ, so the match means something: %r" % r["dress"])
        # the unread arrows land a thread without marking it read (opening the thread does)
        self.assertEqual(r["read145"], {"unread": True, "pop": False}, "landing is not reading: %r" % r["read145"])
        self.assertEqual(r["read140"], {"unread": True, "pop": False}, r["read140"])
        # the count is every unread thread, wherever it sits: landing one into view, scrolling it out and back, never
        # moves it (it read 2, 3, 2 here when it counted the threads off screen); only a thread's own change does
        k = r["counts"]
        self.assertEqual(k["onLast"], "3", "on reply 145, in view, it still counts: %r" % k)
        self.assertTrue(k["landedInView"] and not k["awayInView"] and k["backInView"], "the scroll took reply 140 out of view and back: %r" % k)
        self.assertEqual((k["landed"], k["away"], k["back"]), ("3", "3", "3"), "scrolling alone never changes the count: %r" % k)
        self.assertEqual(k["opened"], "2", "opening a thread reads it: one fewer: %r" % k)
        self.assertEqual(k["closed"], "2", "closing it changes nothing: %r" % k)
        self.assertTrue(k["replyInView"], k)
        self.assertEqual(k["reply"], "3", "a reply landing on it, in view, puts it back: %r" % k)
        # the api tab: its transcript fits, so no cluster
        self.assertTrue(r["api"]["fits"], r["api"])
        self.assertTrue(r["api"]["hidden"], "no cluster over a transcript that fits: %r" % r["api"])
        # the phone (390x844, a coarse pointer): all three capsules show, the strip stays thin, the touch size comes from
        # height, and the column clears the message box
        ph = r["phone"]
        self.assertTrue(ph["coarse"], ph)
        self.assertFalse(ph["hidden"], ph)
        for g in ("unread", "cmt", "mine"):
            self.assertTrue(ph[g]["shown"], "%s capsule on the phone: %r" % (g, ph[g]))
            self.assertEqual(ph[g]["w"], 34, "the phone column: 34px: %r" % ph[g])
        for m in ("prevUnread", "nextUnread", "prevComment", "nextComment", "prevMine", "nextMine"):
            self.assertEqual((ph[m]["w"], ph[m]["h"]), (32, 40), "each chevron 32x40 on the phone: %s %r" % (m, ph[m]))
        self.assertEqual(ph["rect"]["h"], 330, "the phone column: three 106px capsules and two gaps: %r" % ph["rect"])
        self.assertGreaterEqual(ph["rect"]["t"], ph["content"]["t"], "inside the pane: %r" % ph)
        self.assertIsNotNone(ph["composerTop"], ph)
        self.assertLess(ph["rect"]["b"] + 8 + PHONE_CHIP_H + GAP, ph["composerTop"] + 1, "the column and the chip's slot clear the message box: %r" % ph)
        self.assertLessEqual(ph["rect"]["b"], ph["content"]["b"] - 8 - PHONE_CHIP_H - GAP + 1, ph)
        # a screen up on the phone: the chip shows, the column's width, its touch size from height, one gap below the column,
        # and column and chip together sit inside the pane, clear of the message box
        pu = r["phoneUp"]
        self.assertFalse(pu["jump"]["hidden"], "scrolled up, the chip shows: %r" % pu)
        self.assertFalse(pu["hidden"], pu)
        self.assertEqual((pu["jump"]["w"], pu["jump"]["h"]), (pu["rect"]["w"], PHONE_CHIP_H), "the chip on the phone: the column's width, %dpx tall: %r" % (PHONE_CHIP_H, pu))
        self.assertEqual(pu["jump"]["w"], 34, pu)
        self.assertGreaterEqual(pu["jump"]["h"] - 2, 40, "at least 40px of touch height inside the hairline, as the arrows: %r" % pu["jump"])
        self.assertEqual(pu["jump"]["l"], pu["rect"]["l"], "left-aligned with the column: %r" % pu)
        self.assertLess(abs(pu["jump"]["t"] - pu["rect"]["b"] - GAP), 0.5, "one capsule gap below the column: %r" % pu)
        self.assertEqual((pu["rect"]["b"], pu["rect"]["h"]), (ph["rect"]["b"], ph["rect"]["h"]), "the column did not move as the chip came up: %r vs %r" % (pu["rect"], ph["rect"]))
        self.assertGreaterEqual(pu["rect"]["t"], pu["content"]["t"], "the column's top inside the pane: %r" % pu)
        self.assertLessEqual(pu["jump"]["b"], pu["composerTop"], "the chip clears the message box: %r" % pu)
        self.assertLessEqual(pu["jump"]["b"], pu["content"]["b"], "…and the pane's bottom edge: %r" % pu)
        for theme in ("dark", "light"):
            self._same_dress(r["phoneDress"][theme], 16, "0.8", "phone " + theme)
        print("COLUMN desktop %sx%s chip %sx%s; phone %sx%s chip %sx%s, top clearance %.0f, message-box clearance %.0f" % (
            s0["rect"]["w"], s0["rect"]["h"], s1["jump"]["w"], s1["jump"]["h"], ph["rect"]["w"], ph["rect"]["h"], pu["jump"]["w"], pu["jump"]["h"],
            pu["rect"]["t"] - pu["content"]["t"], pu["composerTop"] - pu["jump"]["b"]))

    def test_the_column_covers_none_of_a_waiting_question(self):
        """A question waiting on the reader (the user 2026-10-18, paraphrased: the corner's pills had no room only when a
        question was waiting): the picker card is the transcript's tail, beside the column in the corner, and keeps the
        thread's left gutter as the turns do, so the column covers neither the card nor a line of its text, on the desktop and
        on the phone, in the dark theme and the light. It ran to the pane's left edge and lost its first letters under the
        column."""
        r = self._drive()
        if r.get("shotsOnly"):
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        for where, a in (("desktop", r["ask"]), ("phone", r["phoneAsk"])):
            self.assertTrue(a["up"], "%s: the question's card is up: %r" % (where, a))
            for theme in ("dark", "light"):
                g, at = a[theme], "%s %s" % (where, theme)
                col, card = g["column"], g["card"]
                self.assertTrue(g["shown"], "%s: the column shows, so the check means something: %r" % (at, g))
                self.assertIsNotNone(card, "%s: the card is on the page: %r" % (at, g))
                self.assertTrue(card["t"] < col["b"] and card["b"] > col["t"],
                                "%s: the card sits beside the column, so the check means something: %r" % (at, g))

                def covers(b):
                    return b["l"] < col["r"] and b["r"] > col["l"] and b["t"] < col["b"] and b["b"] > col["t"]
                self.assertFalse(covers(card), "%s: the column covers none of the card: column %r, card %r" % (at, col, card))
                self.assertGreaterEqual(len(g["texts"]), 5, "%s: the question, both options, a description, the add-your-own hint: %r" % (at, g["texts"]))
                for t in g["texts"]:
                    self.assertFalse(covers(t), "%s: the column covers %s %r: column %r, text %r" % (at, t["cls"], t["text"], col, t))
                self.assertIsNotNone(g["gutter"], g)
                self.assertLess(abs(card["l"] - g["gutter"]), 0.5,
                                "%s: the card starts at the thread's gutter, where the turns' column does: %r" % (at, g))

    def test_a_long_path_in_a_waiting_question_wraps_inside_its_card(self):
        """A long unbroken token in a waiting question (2026-10-18, the review of #306: a path or URL ran past the card's right
        border and was clipped at the pane's edge): the question wraps inside the card, on the desktop and on the phone, in the
        dark theme and the light. Before .ask-qtext wrapped anywhere, the invented path's line ran past the card on both."""
        r = self._drive()
        if r.get("shotsOnly"):
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        for where, a in (("desktop", r["ask"]), ("phone", r["phoneAsk"])):
            for theme in ("dark", "light"):
                g, at = a["long"][theme], "%s %s" % (where, theme)
                self.assertIsNotNone(g, "%s: the card with the long question is up: %r" % (at, a))
                self.assertTrue(g["has"], "%s: it is the long question: %r" % (at, g))
                self.assertGreater(g["lines"], 2, "%s: the question wraps over several lines: %r" % (at, g))
                self.assertLessEqual(g["text"]["r"], g["card"]["r"], "%s: no line of the question runs past the card's right edge: %r" % (at, g))
                self.assertLessEqual(g["scrollW"], g["clientW"], "%s: the question's box has nothing hidden to its right: %r" % (at, g))

    def _same_dress(self, d, glyph_px, rest, where):
        """The go-to-bottom chip and a capsule, as the page computed them: one surface, hairline, shadow and radius, and the
        chip's chevron the arrows' own at their size, stroke and resting glow."""
        for k in ("bg", "bw", "bs", "bc", "shadow", "radius"):
            self.assertEqual(d["jump"][k], d["group"][k], "%s: the chip's %s is the capsule's: %r" % (where, k, d))
        self.assertEqual(d["jump"]["bw"], "1px", "%s: a hairline: %r" % (where, d["jump"]))
        jg, ag = d["jumpGlyph"], d["arrowGlyph"]
        self.assertIsNotNone(ag, "%s: a live down arrow to compare with: %r" % (where, d))
        self.assertEqual((jg["w"], jg["h"]), (glyph_px, glyph_px), "%s: the chip's chevron at the arrows' %dpx: %r" % (where, glyph_px, d))
        for k in ("w", "h", "stroke", "vb", "points", "opacity"):
            self.assertEqual(jg[k], ag[k], "%s: the chip's chevron %s is a down arrow's: %r" % (where, k, d))
        self.assertEqual(jg["opacity"], rest, "%s: resting, as the arrows rest: %r" % (where, jg))


# THE HOST-OFFLINE FOOT (2026-10-18). A session on a linked machine whose link drops keeps its transcript on screen, and the
# transcript says where it ends and why: a slim notice under the last turn (render.ts syncHostOfflineFoot), a sibling of the
# threads rather than a row inside one. It sits in the go-to-bottom chip's slot at the bottom, and a reader a few lines up has
# the chip beside it; the foot keeps the thread's left gutter, so neither the chip nor the column above it covers its first
# letters. The lab is two hermetic kernels: a hub, and a TESTHOST checked in to it (the mobile handshake, no ssh) holding a
# session long enough to overflow both layouts, and a short one that fits. The pages read the remote sessions while their host
# answers; then the lab's own TESTHOST kernel is killed by its pid, the hub's health pass reads the host down, and each page's
# next poll publishes the drop (romp-hosts). Nothing else happens on any page, so the foot coming up at all is the drop's own
# repaint (before it the foot waited for the next frame or tab switch, and a dropped host sends no frame), and where the
# reader stands after it is the page's own write as the foot comes up (the review of #308).
FOOT_SID = "aaaaaaaa-1111-2222-3333-777777777777"   # "web" on TESTHOST
FOOT_SHORT_SID = "aaaaaaaa-1111-2222-3333-666666666666"   # "api" on TESTHOST: two exchanges, a transcript that fits the pane
FOOT_HOST = "TESTHOST"
FOOT_REMOTE = FOOT_HOST + ":" + FOOT_SID
FOOT_SHORT_REMOTE = FOOT_HOST + ":" + FOOT_SHORT_SID
FOOT_N = 40

FOOT_DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = {};
const open = async (opts, id = cfg.remote) => {   // a remote session's tab on the hub's page (the long one with the column up)
  const ctx = await browser.newContext(opts);
  const p = await ctx.newPage();
  await p.goto(cfg.chat);
  await p.waitForSelector(`#tabs .tab[data-id="${id}"]`, { state: "attached", timeout: 60000 });
  await p.evaluate((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`).click(), id);
  await p.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
  if (id === cfg.remote) await p.waitForSelector("#jump-cluster:not([hidden])", { timeout: 30000 }).catch(() => {});
  return p;
};
const GEO = () => {
  const R = (e) => { const r = e.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom }; };
  const on = (e) => !!e && !e.hidden && getComputedStyle(e).display !== "none";
  const box = document.getElementById("jump-cluster"), chip = document.getElementById("jump-bottom");
  const foot = document.getElementById("host-offline-foot"), card = foot && foot.querySelector(".notice");
  const th = Array.from(document.querySelectorAll("#content .thread")).find((e) => e.getClientRects().length > 0);
  const pills = [];
  if (on(box)) pills.push({ what: "column", ...R(box) });
  if (on(chip)) pills.push({ what: "chip", ...R(chip) });
  return { pills, card: card ? R(card) : null, text: foot ? foot.textContent : "",
           gutter: th ? th.getBoundingClientRect().left + parseFloat(getComputedStyle(th).paddingLeft) : null,
           texts: card ? Array.from(card.querySelectorAll(".notice-glyph, .notice-src, .notice-gist, .host-dial-swirl"))
                           .map((e) => ({ cls: e.className, text: (e.textContent || "").slice(0, 24), ...R(e) })) : [] };
};
const toBottom = (p) => p.evaluate(() => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight; });
// at the bottom (the foot in the chip's empty slot), then a few lines up (the chip up beside it), in the dark theme and the light
const footStep = async (p, prefix) => {
  const res = {};
  await p.mouse.move(300, 60);
  for (const th of ["dark", "light"]) {
    await p.evaluate((light) => { document.body.classList.toggle("theme-light", light); document.body.classList.toggle("chat-theme-yatharth", light); }, th === "light");
    await p.waitForTimeout(300);
    await toBottom(p);
    await p.waitForSelector("#jump-bottom", { state: "hidden", timeout: 5000 }).catch(() => {});
    await p.waitForTimeout(300);
    const bottom = await p.evaluate(GEO);
    await p.evaluate((d) => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight - c.clientHeight - d; }, cfg.up);
    await p.waitForSelector("#jump-bottom:not([hidden])", { timeout: 5000 }).catch(() => {});
    await p.waitForTimeout(300);
    const up = await p.evaluate(GEO);
    res[th] = { bottom, up };
    if (cfg.shots && up.card) {
      fs.mkdirSync(cfg.shots, { recursive: true });
      const c = await p.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { w: r.width, b: r.bottom }; });
      const top = Math.max(0, Math.min(...up.pills.map((x) => x.t), up.card.t) - 60);
      await p.screenshot({ path: cfg.shots + "/" + prefix + "foot-" + th + ".png", clip: { x: 0, y: top, width: Math.min(c.w, 640), height: c.b - top } });
    }
  }
  await p.evaluate(() => { document.body.classList.remove("theme-light", "chat-theme-yatharth"); });
  await toBottom(p);
  return res;
};
// Where the reader stands as the foot comes up (the review of #308): the drop adds the foot at the transcript's end with no
// append, so the page's own write is all that keeps a reader who follows the tail at the bottom, and all that re-reads the chip.
const PLACE = () => {
  const c = document.getElementById("content"), foot = document.getElementById("host-offline-foot"), chip = document.getElementById("jump-bottom");
  const top = c.getBoundingClientRect().top + c.clientTop, r = foot ? foot.getBoundingClientRect() : null;
  return { top: c.scrollTop, sh: c.scrollHeight, ch: c.clientHeight, dist: c.scrollHeight - c.scrollTop - c.clientHeight,
           pane: { t: top, b: top + c.clientHeight }, foot: r ? { t: r.top, b: r.bottom, h: r.height } : null, chip: !!chip && !chip.hidden };
};
const settled = (p) => p.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 300)))));
// the short transcript's pane sized to hold it with a little room to spare (cfg.slack px), so it fits until the foot comes up: its
// height read where it overflows a short pane (scrollHeight is the pane's own height while nothing overflows), then the pane grown
const fit = async (p) => {
  await p.setViewportSize({ width: 1000, height: 320 });
  await settled(p);
  const m = await p.evaluate(() => { const c = document.getElementById("content"); return { sh: c.scrollHeight, ch: c.clientHeight, vh: window.innerHeight }; });
  await p.setViewportSize({ width: 1000, height: Math.ceil(m.vh - m.ch + m.sh + cfg.slack) });
  await settled(p);
};
const desk = await open({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });
const phone = await open({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
const away = await open({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 });   // a reader scrolled well up
const fits = await open({ viewport: { width: 1000, height: 700 }, deviceScaleFactor: 2 }, cfg.shortRemote);   // a transcript that fits
for (const p of [desk, phone, away]) await toBottom(p);
await away.evaluate((d) => { const c = document.getElementById("content"); c.scrollTop = c.scrollHeight - c.clientHeight - d; }, cfg.away);
await fit(fits);
const legs = { desk, phone, away, fits };
for (const p of Object.values(legs)) await settled(p);
out.before = {};
for (const [k, p] of Object.entries(legs)) out.before[k] = await p.evaluate(PLACE);
out.footWhileUp = await desk.evaluate(() => !!document.getElementById("host-offline-foot"));
// the link drops: the lab's TESTHOST kernel goes away (its own pid, nothing matched by name), the hub reads the host down
process.kill(cfg.remotePid, "SIGKILL");
for (const p of Object.values(legs)) await p.waitForSelector("#host-offline-foot", { state: "attached", timeout: 60000 }).catch(() => {});
out.after = {};
for (const [k, p] of Object.entries(legs)) { await settled(p); out.after[k] = await p.evaluate(PLACE); }
for (const p of [away, fits]) await p.context().close();
out.desk = await footStep(desk, "");
out.phone = await footStep(phone, "phone-");
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


def _foot_kernel(lab, name, port, token, sessions):
    """One hermetic kernel of the foot's lab under `lab`/`name`, serving `sessions` ((sid, name, exchanges) each)."""
    root = os.path.join(lab, name)
    state = os.path.join(root, "xdg", "romp")
    claude = os.path.join(root, "claude")
    cwd = os.path.join(root, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    fakebin = os.path.join(root, "fakebin")   # the machine's user manager is not the lab's
    os.makedirs(fakebin)
    for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
        Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
        os.chmod(os.path.join(fakebin, tool), 0o755)
    os.makedirs(cwd, exist_ok=True)
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    t0 = int(time.time()) - 86400
    for sid, sname, n in sessions:
        Path(state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (sname, cwd))
        Path(state, "sdk", sid + ".json").write_text(json.dumps(
            {"sid": sid, "name": sname, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        recs, prev = [], None
        for k in range(n):
            u, a = "%s-u%03d" % (sid[:8], k), "%s-a%03d" % (sid[:8], k)
            recs.append({"type": "user", "timestamp": iso(t0 + 600 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": sid, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 600 * k + 30), "uuid": a, "parentUuid": u, "sessionId": sid,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k)}]}})
            prev = a
        Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
    env = _lab.kernel_env(root, claude, os.path.join(lab, "dist"), port, token, ROMP_HOST_NAME=name.upper())
    env["PATH"] = fakebin + os.pathsep + env.get("PATH", os.environ.get("PATH", ""))
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill()
    proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


class ServedHostOfflineFoot(unittest.TestCase):
    """The host-offline foot beside the jump cluster: the foot keeps the thread's left gutter, so neither the column nor the
    go-to-bottom chip under it covers the notice or a word of it, at the bottom and a few lines up, on the desktop and the
    phone, in the dark theme and the light. It started at the pane's 24px, under the 14..38px chip (14..48 on the phone).
    And where the reader stands as it comes up (the review of #308): two more desktop pages read as the host drops, one
    scrolled well up the long session, one on a two-exchange session (api) whose pane is sized to hold it with less room than
    the foot needs. Before the fix a reader at the bottom was left the foot's height above it, most of the foot under the
    pane's edge, and the chip stayed down."""
    maxDiff = None
    UP = 16   # how far above the bottom the reader sits for the second measure: off the true bottom, so the chip is up
    AWAY = 300   # how far above the bottom the scrolled-up reader sits as the host drops: well past the foot's height
    SLACK = 12   # the room under the short transcript before the drop: less than the foot, so the foot overflows the pane

    @classmethod
    def setUpClass(cls):
        cls.procs = []
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
        cls.lab = tempfile.mkdtemp(prefix="jump-foot-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.rport, cls.rtoken = _free_port(), "testtok-foot-remote"
        cls.hport, cls.htoken = _free_port(), "testtok-foot-hub"
        cls.remote, cls.rlog = _foot_kernel(cls.lab, "testhost", cls.rport, cls.rtoken, [(FOOT_SID, "web", FOOT_N), (FOOT_SHORT_SID, "api", 2)])
        cls.procs.append(cls.remote)
        hub, cls.hlog = _foot_kernel(cls.lab, "hub", cls.hport, cls.htoken, [])
        cls.procs.append(hub)
        body = json.dumps({"host": FOOT_HOST, "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if not json.loads(resp.read().decode()).get("ok"):
                raise unittest.SkipTest("the hub refused the check-in")
        rows = []
        for _ in range(60):
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r:
                    rows = json.loads(r.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            if any(t.get("host") == FOOT_HOST and t.get("status") == "up" and t.get("hasToken") for t in rows):
                break
            time.sleep(0.5)
        else:
            raise unittest.SkipTest("the hub never reported the checked-in TESTHOST up: %r" % (rows,))

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill()
                p.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        """The driver's result, run once for the class: the drop it makes is the lab's one, and every test reads its pages."""
        cls = type(self)
        if "_result" not in cls.__dict__:
            try:
                cls._result = self._run_driver()
            except BaseException as e:   # a skip or a failure is every test's, not the first one's alone
                cls._result = e
        if isinstance(cls._result, BaseException):
            raise cls._result
        return cls._result

    def _run_driver(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.hport, self.htoken), "remote": FOOT_REMOTE,
                       "shortRemote": FOOT_SHORT_REMOTE, "remotePid": self.remote.pid, "up": self.UP, "away": self.AWAY,
                       "slack": self.SLACK, "shots": os.environ.get("JUMP_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(FOOT_DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nhub:\n" + open(self.hlog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    def test_the_pills_cover_none_of_the_host_offline_foot(self):
        r = self._drive()
        if os.environ.get("JUMP_SHOTS_ONLY") == "1":
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        self.assertFalse(r["footWhileUp"], "no foot while the host answers: %r" % r)
        for where in ("desk", "phone"):
            for theme in ("dark", "light"):
                for pos in ("bottom", "up"):
                    g, at = r[where][theme][pos], "%s %s %s" % (where, theme, pos)
                    self.assertIsNotNone(g["card"], "%s: the foot is on the page: %r" % (at, g))
                    self.assertIn("TESTHOST is disconnected", g["text"], "%s: it is the host-offline foot: %r" % (at, g))
                    self.assertTrue(any(x["what"] == "column" for x in g["pills"]), "%s: the column shows, so the check means something: %r" % (at, g))
                    if pos == "up":
                        self.assertTrue(any(x["what"] == "chip" for x in g["pills"]), "%s: off the bottom the chip is up: %r" % (at, g))
                        card = g["card"]
                        self.assertTrue(any(card["t"] < x["b"] and card["b"] > x["t"] for x in g["pills"]),
                                        "%s: the foot sits beside a pill, so the check means something: %r" % (at, g))

                    def covers(b, x):
                        return b["l"] < x["r"] and b["r"] > x["l"] and b["t"] < x["b"] and b["b"] > x["t"]
                    self.assertGreaterEqual(len(g["texts"]), 3, "%s: the glyph, the source label, the sentence: %r" % (at, g["texts"]))
                    for x in g["pills"]:
                        self.assertFalse(covers(g["card"], x), "%s: the %s covers none of the foot: %r, foot %r" % (at, x["what"], x, g["card"]))
                        for t in g["texts"]:
                            self.assertFalse(covers(t, x), "%s: the %s covers %s %r: %r, text %r" % (at, x["what"], t["cls"], t["text"], x, t))
                    self.assertIsNotNone(g["gutter"], g)
                    self.assertLess(abs(g["card"]["l"] - g["gutter"]), 0.5,
                                    "%s: the foot starts at the thread's gutter, where the turns' column does: %r" % (at, g))

    def test_the_foot_coming_up_keeps_the_reader_where_they_were_following(self):
        """The drop adds the foot outside the append (the review of #308): a reader at the bottom stays there with the whole foot in
        view, on the desktop and the phone; a reader scrolled well up is not moved a pixel; a transcript that fit the pane is not
        yanked when the foot overflows it (the append's slack rule), and the go-to-bottom chip comes up for it at once."""
        r = self._drive()
        if os.environ.get("JUMP_SHOTS_ONLY") == "1":
            self.skipTest("optional: JUMP_SHOTS_ONLY wrote the screenshots and ran no assertions")
        b, a = r["before"], r["after"]
        for where in ("desk", "phone"):
            pre, post = b[where], a[where]
            self.assertLessEqual(pre["dist"], 1, "%s: the reader sat at the bottom before the drop: %r" % (where, pre))
            self.assertIsNone(pre["foot"], "%s: no foot while the host answered: %r" % (where, pre))
            self.assertIsNotNone(post["foot"], "%s: the drop brought the foot up: %r" % (where, post))
            self.assertLessEqual(post["dist"], 1, "%s: the reader is still at the bottom, the foot under the last turn followed: %r" % (where, post))
            self.assertGreaterEqual(post["foot"]["t"], post["pane"]["t"] - 0.5, "%s: the foot's top is in view: %r" % (where, post))
            self.assertLessEqual(post["foot"]["b"], post["pane"]["b"] + 0.5, "%s: the whole foot is in view, none of it under the pane's edge: %r" % (where, post))
            self.assertFalse(post["chip"], "%s: at the bottom, no go-to-bottom chip: %r" % (where, post))
        pre, post = b["away"], a["away"]
        self.assertGreater(pre["dist"], self.AWAY - 2, "the scrolled-up reader sat well above the bottom: %r" % pre)
        self.assertTrue(pre["chip"], "scrolled up, the chip was up: %r" % pre)
        self.assertIsNotNone(post["foot"], "the drop brought the foot up: %r" % post)
        self.assertEqual(post["top"], pre["top"], "a reader scrolled up is not moved: %r -> %r" % (pre, post))
        self.assertTrue(post["chip"], "…and keeps the chip: %r" % post)
        pre, post = b["fits"], a["fits"]
        self.assertLessEqual(pre["sh"], pre["ch"] + 2, "the short transcript fit its pane before the drop: %r" % pre)
        self.assertFalse(pre["chip"], "nothing to go to the bottom of: %r" % pre)
        self.assertIsNotNone(post["foot"], "the drop brought the foot up: %r" % post)
        self.assertGreater(post["sh"], post["ch"] + 2, "the foot overflows the pane, so the check means something: %r" % post)
        self.assertEqual(post["top"], pre["top"], "a transcript that fit is not yanked as the foot first overflows it: %r -> %r" % (pre, post))
        self.assertTrue(post["chip"], "the foot below the pane's edge, the chip is up at once, with no scroll to re-read it: %r" % post)


if __name__ == "__main__":
    unittest.main()
