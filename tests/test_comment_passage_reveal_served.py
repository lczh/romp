#!/usr/bin/env python3
"""Three follow-ups to the landing on a comment thread's passage (the review of 2026-10-15). The tick on the scroll rail lands
the thread's passage, flashes it and opens the thread beside it; three shapes of passage went wrong on the real /chat page:
  a passage on the LAST reply's final line, clicked with the reader scrolled far up: the box was placed above the passage,
  then a fork control joined the reply and the landing's settle scrolled the passage up behind the box;
  a passage folded in a closed <details> of its reply: the view went where the hidden passage would be and nothing visible
  flashed; and one that cannot be shown (a hidden block inside the fold) must land the fold's heading and say so in the box;
  a passage split over several elements (a bold phrase): only its first piece flashed.
And the review of that fix: the box followed every scroll of the view, so with the session still writing it climbed a line
at a time as the view followed the new text, shrank at the window's top and then jumped below the passage, while the
reader was hovering or typing in it; and a passage running from one closed fold into the next opened only the first.
The box now moves only when the LANDING re-lands its passage into it (the late fork control above), the least that clears
it, and never while the pointer is over it or it holds the focus (it steps off once they leave); and every fold any piece
of the passage sits in opens. And a landing on a passage pauses follow mode (the user 2026-10-15): with the session still
writing, the passage and its box stay where the reader is looking, uncovered, and scrolling back to the bottom resumes it.
A paused view holds the passage still in every frame while the session writes, back from another tab too, where only a
view that was following resumes following (the user 2026-10-17).

A hermetic kernel serves a synthetic chat (the notes-api demo world, session web): 150 exchanges of fourteen-paragraph
replies, so the page renders only the tail of what it holds. Four threads: reply 149's final paragraph, a passage in a closed
fold of reply 120, one with a bold phrase in reply 110, one in a hidden block inside a fold of reply 100, and one across
two closed folds of reply 90; then replies stream in under the last one by appending to the transcript. Skips LOUDLY
without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs, invented prose, host
TESTHOST)."""
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

WEB = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
API = "aaaaaaaa-1111-2222-3333-cccccccccccc"   # a second session, for a tab to go to and come back from
N = 150
PARAS, AT = 14, 6
USER_UUID = "11111111-2222-3333-4444-%012d"
REPLY_UUID = "22222222-3333-4444-5555-%012d"
THREAD_SID = "eeeeeeee-1111-2222-3333-%012d"
# (tid, reply index, how the passage sits in its reply)
THREADS = [("tid-end", 149, "end"), ("tid-fold", 120, "fold"), ("tid-split", 110, "split"), ("tid-hidden", 100, "hidden"), ("tid-two", 90, "two")]
STREAM_UUID = "33333333-4444-5555-6666-%012d"   # the replies that stream in after the last one
FOLDED = "Couldn't unfold this passage in its message."


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


def two_exact(k):
    """A passage that starts in one closed fold and runs into the next, the second's heading between."""
    return "The bounded buffer at step %d holds two hundred rows Writer notes before the writer flushes to the file." % k


def para(j, k):
    return ("Paragraph %d of step %d walks the export: the reader pulls a page of notes, the encoder turns each into a row, "
            "and the writer appends it to the file the client downloads, so nothing waits on the whole set." % (j, k))


def reply_body(k, how):
    out = []
    for j in range(PARAS):
        if how == "end" and j == PARAS - 1:
            out.append(exact(k))
        elif how == "fold" and j == AT:
            out.append('<details class="fx-fold"><summary>Export notes</summary><p>%s</p></details>' % exact(k))
        elif how == "hidden" and j == AT:
            out.append('<details class="fx-hidden"><summary>Archived notes</summary><div hidden><p>%s</p></div></details>' % exact(k))
        elif how == "two" and j == AT:
            out.append('<details class="fx-two-a"><summary>Buffer notes</summary><p>The bounded buffer at step %d holds two hundred rows</p></details>' % k)
        elif how == "two" and j == AT + 1:
            out.append('<details class="fx-two-b"><summary>Writer notes</summary>\n<p>before the writer flushes to the file.</p></details>')
        elif how == "split" and j == AT:
            out.append("The bounded buffer at step %d holds **two hundred** rows before the writer flushes." % k)
        else:
            out.append(para(j, k))
    return "\n\n".join(out)


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
await page.goto(cfg.chat);
await page.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
await page.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
await page.waitForFunction((n) => document.querySelectorAll("#cmt-rail .cmt-tick").length >= n, cfg.threads, { timeout: 45000 }).catch(() => {});
await page.waitForTimeout(800);
out.ticks = await page.evaluate(() => Array.from(document.querySelectorAll("#cmt-rail .cmt-tick")).map((t) => t.dataset.tid));
// what happens on screen, in order: a flash as it starts (on a piece of a thread's highlight, by its index among the thread's
// pieces, or on another element, by its tag) and the thread's box as it opens; the flash class lasts 1.6s, so it is caught
// as it is added
await page.evaluate(() => {
  window.__seq = []; window.__pieces = {};
  const note = (s) => { if (window.__seq[window.__seq.length - 1] !== s) window.__seq.push(s); };
  new MutationObserver((recs) => { for (const r of recs) {
    if (r.type === "attributes") {
      const el = r.target;
      if (!el.classList || !el.classList.contains("anchor-flash") || !el.closest("#content")) continue;
      if (el.tagName === "MARK") {
        const tid = el.dataset.tid;
        const i = Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`)).indexOf(el);
        (window.__pieces[tid] = window.__pieces[tid] || []).includes(i) || window.__pieces[tid].push(i);
        note("flash:mark:" + tid);
      } else note("flash:" + el.tagName.toLowerCase());
    } else for (const n of Array.from(r.addedNodes)) if (n.id === "cmt-pop") note("pop:" + n.dataset.tid);
  } }).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ["class"] });
});
// a thread's passage on screen (every piece of its highlight as one box), the heading of the fold it is in, the open box, and
// what the box says
const measure = (tid, fold) => page.evaluate(({ tid, fold }) => {
  const c = document.getElementById("content").getBoundingClientRect();
  let u = null;
  const marks = Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`));
  for (const m of marks) for (const q of Array.from(m.getClientRects()))
    u = u ? { l: Math.min(u.l, q.left), t: Math.min(u.t, q.top), r: Math.max(u.r, q.right), b: Math.max(u.b, q.bottom) } : { l: q.left, t: q.top, r: q.right, b: q.bottom };
  const d = fold ? document.querySelector(`#content details.${fold}`) : null;
  const s = d ? d.querySelector("summary").getBoundingClientRect() : null;
  const pop = document.getElementById("cmt-pop"), p = pop ? pop.getBoundingClientRect() : null;
  const lost = pop ? pop.querySelector(".cmt-lost") : null;
  // shown: drawn, not only laid out (Chromium lays out a closed <details>' content, so a folded passage has a box)
  return { content: { t: c.top, b: c.bottom }, mark: u, inView: !!u && u.b > c.top && u.t < c.bottom, pieces: marks.length,
           shown: marks.length > 0 && marks.filter((m) => m.textContent.trim()).every((m) => m.checkVisibility()),
           flashed: (window.__pieces[tid] || []).slice().sort(), fold: d ? { open: d.open, head: { l: s.left, t: s.top, r: s.right, b: s.bottom } } : null,
           pop: p ? { l: p.left, t: p.top, r: p.right, b: p.bottom } : null, popTid: pop ? pop.dataset.tid : null,
           lost: lost ? lost.textContent : null, vh: innerHeight, seq: window.__seq.slice() };
}, { tid, fold });
const fresh = async () => {
  await page.evaluate(() => { document.getElementById("cmt-pop")?.querySelector(".cmt-x")?.click(); window.__seq = []; window.__pieces = {}; });
  await page.waitForTimeout(300);
};
const opened = async (tid, fold) => {
  await page.waitForSelector(`#cmt-pop[data-tid="${tid}"]`, { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1600);   // past the landing's settle, and the late fork control under the last reply
  return measure(tid, fold);
};

// 1. the last reply's final line, with the reader scrolled far up (the tail outside the rendered window): the landing renders
// the tail and cannot scroll the passage higher, so the box goes above it; a fork control then joins the reply, and the
// settle scrolls the passage up into where the box was
await page.mouse.move(150, 400);
for (let i = 0; i < 40; i++) { await page.mouse.wheel(0, -3000); await page.waitForTimeout(100); }   // loop-ok: forty wheel turns, a reader scrolling far up
await page.waitForTimeout(800);
out.endBefore = await page.evaluate((u) => ({ tailRendered: !!document.querySelector(`#content .turn[data-uuid="${u}"]`) }), R(149));
await page.evaluate(() => { window.__seq = []; window.__pieces = {}; });
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-end"]`);
out.end = await opened("tid-end");

// 2. a passage folded in a closed <details> of its reply: the fold opens, the passage lands at the top and flashes
await fresh();
out.foldBefore = await measure("tid-fold", "fx-fold");
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-fold"]`);
out.fold = await opened("tid-fold", "fx-fold");

// 3. a passage split over three pieces by a bold phrase: every piece flashes
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-split"]`);
out.split = await opened("tid-split");

// 4. a passage its reply keeps hidden inside a fold (a `hidden` block the landing does not open): the fold's heading lands,
// and the box says so
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-hidden"]`);
out.hidden = await opened("tid-hidden", "fx-hidden");

// 5. a passage that starts in one closed fold and runs into the next: both folds open, every piece shows
await fresh();
await page.click(`#cmt-rail .cmt-tick[data-tid="tid-two"]`);
out.two = await opened("tid-two", "fx-two-a");
out.twoB = await page.evaluate(() => { const d = document.querySelector("#content details.fx-two-b");
  return d ? { open: d.open, pieces: d.querySelectorAll('mark.cmt-hl[data-tid="tid-two"]').length } : null; });

// 6-8: the box holds still while the reader reads. The box and the passage, the view's scroll, as numbers
const boxNow = () => page.evaluate(() => { const p = document.getElementById("cmt-pop"); if (!p) return null; const r = p.getBoundingClientRect(); return { t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left), r: Math.round(r.right) }; });
const markNow = (tid) => page.evaluate((tid) => { let u = null;
  for (const m of Array.from(document.querySelectorAll(`#content mark.cmt-hl[data-tid="${tid}"]`))) for (const q of Array.from(m.getClientRects()))
    u = u ? { t: Math.min(u.t, q.top), b: Math.max(u.b, q.bottom), l: Math.min(u.l, q.left), r: Math.max(u.r, q.right) } : { t: q.top, b: q.bottom, l: q.left, r: q.right };
  return u && { t: Math.round(u.t), b: Math.round(u.b), l: Math.round(u.l), r: Math.round(u.r) }; }, tid);
const scrollNow = () => page.evaluate(() => document.getElementById("content").scrollTop);
const landEnd = async () => {
  await fresh();
  await page.click(`#cmt-rail .cmt-tick[data-tid="tid-end"]`);
  await page.waitForSelector('#cmt-pop[data-tid="tid-end"]', { timeout: 15000 }).catch(() => {});
};

// 6, 7: the box holds still while the reader is in it. The message box under the transcript grows (lines put into it by the
// page, the focus left where the scenario put it): the view re-pins the bottom and raises the passage into the thread's box
const growComposer = (lines) => page.evaluate((lines) => {
  const t = document.getElementById("composer-input"); if (!t) return null;
  t.value = lines ? Array.from({ length: lines }, (_, i) => "line " + i + " about the rows").join("\n") : "";
  t.dispatchEvent(new Event("input", { bubbles: true }));
  return t.offsetHeight;
}, lines);
const inBox = () => page.evaluate(() => { const p = document.getElementById("cmt-pop"); return { focused: !!document.activeElement?.closest("#cmt-pop"), hovered: !!p && p.matches(":hover") }; });

// 6. the pointer over the box: the passage rises into it, and the box stays where it is until the pointer leaves
await landEnd();
await page.waitForTimeout(1500);   // the landing settled: only the message box moves the passage from here
await page.mouse.move(640, 250);   // inside the box, which the landing puts above the passage (asserted: hovered)
out.hover0 = { box: await boxNow(), mark: await markNow("tid-end"), ...(await inBox()) };
await growComposer(5);
await page.waitForTimeout(500);
out.hover1 = { box: await boxNow(), mark: await markNow("tid-end") };
await page.mouse.move(150, 650);   // the pointer leaves the box: the step-off it owed is paid now
await page.waitForTimeout(200);
out.hover2 = { box: await boxNow(), mark: await markNow("tid-end") };
await growComposer(0);
await page.waitForTimeout(300);

// 7. typing in the box, the pointer elsewhere: the same, until the focus leaves it
await landEnd();
await page.waitForTimeout(1500);
await page.mouse.move(150, 650);
await page.evaluate(() => document.querySelector("#cmt-pop .cmt-input")?.focus());
await page.keyboard.type("why");
out.type0 = { box: await boxNow(), mark: await markNow("tid-end"), ...(await inBox()) };
await growComposer(5);
await page.waitForTimeout(500);
out.type1 = { box: await boxNow(), mark: await markNow("tid-end") };
await page.evaluate(() => { document.querySelector("#cmt-pop .cmt-input")?.blur(); });
await page.waitForTimeout(200);   // the focus left the box: the step-off it owed is paid now
out.type2 = { box: await boxNow(), mark: await markNow("tid-end") };
await page.evaluate(() => { const i = document.querySelector("#cmt-pop .cmt-input"); if (i) { i.value = ""; i.dispatchEvent(new Event("input", { bubbles: true })); } });
await growComposer(0);
await page.waitForTimeout(300);

// 11. typing in the message box after a landing on the last reply: the box below grows, the view re-pins the bottom and the
// passage rises into the thread's box above it; the thread's box steps off it
await landEnd();
await page.mouse.move(150, 650);
await page.waitForTimeout(1500);   // the landing settled
out.compose0 = { box: await boxNow(), mark: await markNow("tid-end"), h: await page.evaluate(() => document.getElementById("composer-input")?.offsetHeight ?? null) };
await page.evaluate(() => document.getElementById("composer-input")?.focus());
await page.keyboard.type("a note on the export");
for (let i = 0; i < 4; i++) { await page.keyboard.press("Shift+Enter"); await page.keyboard.type("and one more line about the rows"); }   // loop-ok: four more lines typed
await page.waitForTimeout(600);
out.compose1 = { box: await boxNow(), mark: await markNow("tid-end"), h: await page.evaluate(() => document.getElementById("composer-input")?.offsetHeight ?? null) };
await page.evaluate(() => { const t = document.getElementById("composer-input"); if (t) { t.value = ""; t.dispatchEvent(new Event("input", { bubbles: true })); t.blur(); } });
await page.waitForTimeout(400);

// the session writing: a reply appended to its transcript, waited on until it reaches the page (`pg`)
const atBottomNow = (pg = page) => pg.evaluate(() => { const c = document.getElementById("content"); return c.scrollHeight - c.scrollTop - c.clientHeight <= 2; });
const chipNow = (pg = page) => pg.evaluate(() => { const b = document.getElementById("jump-bottom"); return !!b && !b.hidden; });
let prev = R(149), nStream = 0;
const streamOne = async (pg = page, wait = true, paras = 1) => {
  const u = cfg.streamUuid.replace("%012d", String(nStream++).padStart(12, "0"));
  fs.appendFileSync(cfg.transcript, JSON.stringify({ type: "assistant", uuid: u, parentUuid: prev, timestamp: new Date().toISOString(), sessionId: cfg.sid,
    message: { role: "assistant", model: "claude-opus-5", content: [{ type: "text", text: Array.from({ length: paras }, (_, j) => "Streaming note " + nStream + "." + j + ": the writer flushes the next page of rows to the download, and the reader keeps pulling.").join("\n\n") }] } }) + "\n");
  prev = u;
  if (wait) await pg.waitForSelector(`#content .turn[data-uuid="${u}"]`, { state: "attached", timeout: 10000 }).catch(() => {});
  return u;
};
const contentRect = () => page.evaluate(() => { const r = document.getElementById("content").getBoundingClientRect(); return { t: Math.round(r.top), b: Math.round(r.bottom) }; });

// 15. the review of #279: paused at the bottom, the tail shrinks 80 px under the reader (a stand-in element leaves the end of the
// transcript; the room under the paused view keeps the passage still), then the reader wheels UP 40 px. That is a reader off the
// bottom: still paused, the chip showing, and the next reply moves nothing. Then they wheel down to the bottom: following resumes
// and the next reply is followed
await page.evaluate(() => {
  const th = Array.from(document.querySelectorAll("#content > .thread")).find((t) => t.offsetHeight > 0);
  const d = document.createElement("div"); d.id = "fx-tail"; d.style.height = "80px"; th?.appendChild(d);
});
await landEnd();
await page.mouse.move(150, 650);
await page.waitForTimeout(1500);   // the landing settled
out.shrink0 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), st: await scrollNow() };
await page.evaluate(() => document.getElementById("fx-tail")?.remove());
await page.waitForTimeout(400);
out.shrink1 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), st: await scrollNow() };
await page.mouse.move(150, 400);
await page.mouse.wheel(0, -40);
await page.waitForTimeout(1000);   // past the scroll's end
out.shrink2 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow(), st: await scrollNow() };
await streamOne();
await page.waitForTimeout(400);
out.shrink3 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow() };
for (let i = 0; i < 4; i++) { await page.mouse.wheel(0, 600); await page.waitForTimeout(150); }   // loop-ok: a reader wheeling down to the end
await page.waitForTimeout(600);
out.shrink4 = { bottom: await atBottomNow(), chip: await chipNow() };
await streamOne();
await page.waitForTimeout(400);
out.shrink5 = { bottom: await atBottomNow(), chip: await chipNow() };

// 16. the review of #280: paused 50 px above the end (a landing at the bottom, then the reader wheels up 50 px), and 300 px leave
// the transcript ABOVE the reader (a stand-in element in an earlier reply). The browser's anchoring keeps the passage still; the
// room under the view moves up with the text, so the reader can scroll no further than the 50 px they had (never into 300 px of
// blank space), and wheeling down to the end resumes following with no jump as the room is let go
const roomNow = () => page.evaluate(() => { const c = document.getElementById("content"); return Math.round(c.scrollHeight - c.scrollTop - c.clientHeight); });
await page.evaluate(() => {
  const th = Array.from(document.querySelectorAll("#content > .thread")).find((t) => t.offsetHeight > 0);
  const d = document.createElement("div"); d.id = "fx-above"; d.style.height = "300px"; th?.querySelector(".turn")?.appendChild(d);
});
await landEnd();
await page.mouse.move(150, 650);
await page.waitForTimeout(1500);   // the landing settled
await page.mouse.move(150, 400);
await page.mouse.wheel(0, -50);
await page.waitForTimeout(1000);   // past the scroll's end
out.above0 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), room: await roomNow(), st: await scrollNow(),
               above: await page.evaluate(() => { const d = document.getElementById("fx-above"), c = document.getElementById("content");
                 return !!d && d.getBoundingClientRect().bottom < c.getBoundingClientRect().top; }) };
await page.evaluate(() => document.getElementById("fx-above")?.remove());
await page.waitForTimeout(400);
out.above1 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), room: await roomNow(), chip: await chipNow() };
// the blank stretch under the transcript's last line: the view's bottom edge less the bottom of the thread's last drawn element
const blankNow = () => page.evaluate(() => {
  const c = document.getElementById("content");
  const th = Array.from(c.querySelectorAll(":scope > .thread")).find((t) => t.offsetHeight > 0);
  const last = th ? Array.from(th.children).reverse().find((k) => k.getBoundingClientRect().height > 0) : null;
  return last ? Math.round(c.getBoundingClientRect().top + c.clientTop + c.clientHeight - last.getBoundingClientRect().bottom) : null;
});
out.above1.blank = await blankNow();
await page.mouse.wheel(0, 200);    // more than the 50 px to the end, less than the 300 px that left
await page.waitForTimeout(800);   // at the end: following resumes, and the room goes
out.above2 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow(), blank: await blankNow() };
await streamOne();
await page.waitForTimeout(400);
out.above3 = { bottom: await atBottomNow(), chip: await chipNow() };

// 14. the session writes while the reader is on another tab, after a landing at the bottom: back on the tab, the passage is
// where they left it, follow mode still paused, the chip showing the text below; the next reply does not move it either
await landEnd();
await page.waitForTimeout(1500);   // the landing settled
out.away0 = { mark: await markNow("tid-end"), bottom: await atBottomNow() };
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.otherSid);
await page.waitForTimeout(800);
await streamOne(page, false, 8);   // eight paragraphs written while the reader is away: the page holds it, the hidden view draws it on return
await page.waitForTimeout(1500);
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.sid);
await page.waitForSelector(`#content .turn[data-uuid="${prev}"]`, { state: "attached", timeout: 10000 }).catch(() => {});   // drawn before or after the return
await page.waitForTimeout(800);
out.away1 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow(), content: await contentRect(), arrived: await page.evaluate((u) => !!document.querySelector(`#content .turn[data-uuid="${u}"]`), prev) };
await streamOne();
await page.waitForTimeout(400);
out.away2 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow() };
await page.click("#jump-bottom").catch(() => {});   // back to following, for what comes next
await page.waitForTimeout(600);

// 10. a paused landing at the bottom, the thread closed, another tab and back with nothing written meanwhile: the passage is
// where the reader left it and the pause stands. Then the session writes, a reply a second for ten seconds: the passage holds
// still on screen through every frame of it (the view itself never moves, not only the box), and the chip shows the text below
await landEnd();
await page.waitForTimeout(1500);   // the landing settled
await fresh();                    // the thread closed
await page.mouse.move(150, 650);
out.held0 = { mark: await markNow("tid-end"), bottom: await atBottomNow() };
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.otherSid);
await page.waitForTimeout(800);
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.sid);
await page.waitForTimeout(800);
out.held1 = { mark: await markNow("tid-end"), bottom: await atBottomNow(), chip: await chipNow() };
// the passage's top in every frame from here on: a move of the view lasting one frame is caught, not only one a sample lands on
await page.evaluate((tid) => {
  window.__tops = []; window.__topsOn = true; window.__frames = 0;
  const frame = () => {
    window.__frames++;
    const m = document.querySelector(`#content mark.cmt-hl[data-tid="${tid}"]`);
    const t = m ? Math.round(m.getBoundingClientRect().top * 10) / 10 : null;
    if (!window.__tops.length || window.__tops[window.__tops.length - 1].t !== t) window.__tops.push({ ms: Math.round(performance.now()), t });
    if (window.__topsOn) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}, "tid-end");
out.held = [];
for (let i = 0; i < 10; i++) {   // loop-ok: ten replies a second apart, each waited on reaching the page
  const u = await streamOne();
  await page.waitForTimeout(1000);
  out.held.push({ arrived: await page.evaluate((u) => !!document.querySelector(`#content .turn[data-uuid="${u}"]`), u), bottom: await atBottomNow(), chip: await chipNow() });
}
out.heldTops = await page.evaluate(() => { window.__topsOn = false; return { frames: window.__frames, tops: window.__tops }; });
await page.click("#jump-bottom").catch(() => {});   // back to following, the reader's own way, for what comes next
await page.waitForTimeout(600);

// 10b. a view following at the bottom, another tab and back: at the bottom, following exactly, reply after reply (a view at the
// bottom that silently stopped following hid the next text below, the review of #251)
out.follow0 = { bottom: await atBottomNow(), chip: await chipNow() };
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.otherSid);
await page.waitForTimeout(800);
await page.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`)?.click(), cfg.sid);
await page.waitForTimeout(800);
out.tab0 = { bottom: await atBottomNow(), chip: await chipNow() };
out.tab = [];
for (let i = 0; i < 3; i++) {   // loop-ok: three replies, each waited on its own turn reaching the page
  await streamOne();
  await page.waitForTimeout(400);
  out.tab.push({ bottom: await atBottomNow(), chip: await chipNow() });
}

// 8. the session goes on writing: replies stream in under the last one from the moment the box opens. The landing paused
// follow mode, so the view keeps its place: the passage stays clear of the box throughout and, once the landing settled, the
// passage and the box hold still; the go-to-bottom chip shows the text below
await landEnd();
const t0 = Date.now();
await page.mouse.move(150, 650);
out.stream = [];
for (let i = 0; i < 8; i++) {   // loop-ok: eight replies, each waited on its own turn reaching the page
  const u = await streamOne();
  await page.waitForTimeout(150);
  out.stream.push({ t: Date.now() - t0, box: await boxNow(), mark: await markNow("tid-end"), st: await scrollNow(), chip: await chipNow(),
                    arrived: await page.evaluate((u) => !!document.querySelector(`#content .turn[data-uuid="${u}"]`), u) });
}

// 9. the reader scrolls back to the bottom with the wheel: following resumes, the next reply is followed
await page.mouse.move(150, 400);
for (let i = 0; i < 12; i++) { await page.mouse.wheel(0, 600); await page.waitForTimeout(100); }   // loop-ok: a reader wheeling down to the end
await page.waitForTimeout(300);
out.resume0 = { st: await scrollNow(), bottom: await atBottomNow(), chip: await chipNow() };
await streamOne();
await page.waitForTimeout(400);
out.resume1 = { st: await scrollNow(), bottom: await atBottomNow(), chip: await chipNow() };

// 12. a phone: a landing through the jump arrows pauses follow mode, a reply arrives below, and the reader flicks back to the
// bottom. The flick's momentum reaches the bottom well after the finger left (a smooth scroll stands in for it, started by the
// touch); following resumes all the same, and the next reply is followed
const phone = await (await browser.newContext({ viewport: { width: 390, height: 780 }, isMobile: true, hasTouch: true })).newPage();
await phone.goto(cfg.chat);
await phone.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
await phone.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
await phone.waitForSelector("#content .turn-user", { state: "attached", timeout: 60000 });
await phone.waitForSelector('#jump-cluster [data-move="prevComment"]', { state: "visible", timeout: 30000 }).catch(() => {});
await phone.waitForTimeout(800);
await phone.evaluate(() => document.querySelector('#jump-cluster [data-move="prevComment"]')?.click());   // the last thread's passage, tid-end
await phone.waitForTimeout(1500);
await streamOne(phone);
await phone.waitForTimeout(400);
out.flick0 = { bottom: await atBottomNow(phone), chip: await chipNow(phone) };
out.flick = await phone.evaluate(() => new Promise((done) => {
  const c = document.getElementById("content");
  const t = new Touch({ identifier: 1, target: c, clientX: 200, clientY: 500 });
  const t0 = performance.now();
  let bottomAt = null;
  const onScroll = () => { if (bottomAt == null && c.scrollHeight - c.scrollTop - c.clientHeight <= 2) bottomAt = performance.now() - t0; };
  c.addEventListener("scroll", onScroll, { passive: true });
  c.dispatchEvent(new TouchEvent("touchstart", { touches: [t], targetTouches: [t], changedTouches: [t], bubbles: true }));
  c.dispatchEvent(new TouchEvent("touchmove", { touches: [t], targetTouches: [t], changedTouches: [t], bubbles: true }));
  c.dispatchEvent(new TouchEvent("touchend", { touches: [], targetTouches: [], changedTouches: [t], bubbles: true }));
  c.scrollTo({ top: c.scrollHeight, behavior: "smooth" });
  const finish = () => { c.removeEventListener("scroll", onScroll); done({ bottomAt, distance: null }); };
  c.addEventListener("scrollend", finish, { once: true });
  setTimeout(finish, 4000);
}));
await phone.waitForTimeout(300);
out.flick1 = { bottom: await atBottomNow(phone) };
await streamOne(phone);
await phone.waitForTimeout(400);
out.flick2 = { bottom: await atBottomNow(phone), chip: await chipNow(phone) };
// 13. the keys back to the bottom after a landing: End, PageDown and Space are the browser's own smooth scrolls, whose scrollend
// can come before the last scroll event at the bottom. Each run: a landing (the passage has replies below it now), a reply that
// lands below unfollowed, the keys to the bottom, then the next reply must be followed
const keyBack = async (key) => {
  await fresh();
  await page.click(`#cmt-rail .cmt-tick[data-tid="tid-end"]`);
  await page.waitForSelector('#cmt-pop[data-tid="tid-end"]', { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(1500);   // the landing settled
  await streamOne();
  await page.waitForTimeout(300);
  const before = { bottom: await atBottomNow(), chip: await chipNow() };
  await page.mouse.click(150, 300);   // into the transcript, so the keys scroll it (the press outside closes the thread's box)
  for (let i = 0; i < (key === "End" ? 1 : 10) && !(await atBottomNow()); i++) {   // loop-ok: a key at a time until the bottom, each waited past its smooth scroll
    await page.keyboard.press(key);
    await page.waitForTimeout(500);
  }
  const reached = await atBottomNow();
  await streamOne();
  await page.waitForTimeout(400);
  return { key, before, reached, after: { bottom: await atBottomNow(), chip: await chipNow() } };
};
out.keys = [];
for (const key of ["End", "End", "End", "End", "PageDown", "Space"]) out.keys.push(await keyBack(key));   // loop-ok: six runs, each waited on its own landing and replies

fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentPassageReveal(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="comment-passage-reveal-")
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
        Path(state, "names", API).write_text("api\t%s\t#f0b27a\t#2e1a0c\n" % cwd)
        Path(state, "sdk", API + ".json").write_text(json.dumps(
            {"sid": API, "name": "api", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": API, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        how = {k: h for _, k, h in THREADS}
        t0 = int(time.time()) - 3 * 86400
        recs, prev = [], None
        for k in range(N):
            u, a = USER_UUID % k, REPLY_UUID % k
            recs.append({"type": "user", "timestamp": iso(t0 + 600 * k), "uuid": u, "parentUuid": prev, "promptSource": "typed",
                         "sessionId": WEB, "message": {"role": "user", "content": "notes-api: step %d, how does the export look?" % k}})
            recs.append({"type": "assistant", "timestamp": iso(t0 + 600 * k + 30), "uuid": a, "parentUuid": u, "sessionId": WEB,
                         "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                     "content": [{"type": "text", "text": reply_body(k, how.get(k))}]}})
            prev = a
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(proj, API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in (
            {"type": "user", "timestamp": iso(t0), "uuid": "44444444-5555-6666-7777-000000000001", "parentUuid": None, "promptSource": "typed",
             "sessionId": API, "message": {"role": "user", "content": "notes-api: which routes need auth?"}},
            {"type": "assistant", "timestamp": iso(t0 + 30), "uuid": "44444444-5555-6666-7777-000000000002", "parentUuid": "44444444-5555-6666-7777-000000000001",
             "sessionId": API, "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                           "content": [{"type": "text", "text": "Every route under /notes, and the export."}]}})))
        rows = []
        cls.transcript = os.path.join(proj, WEB + ".jsonl")
        for i, (tid, k, how_) in enumerate(THREADS):
            tsid = THREAD_SID % (i + 11)
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
            rows.append({"tid": tid, "sid": tsid, "name": "web-comment-%d" % (i + 1), "anchorUuid": anchor, "cutUuid": anchor,
                         "exact": two_exact(k) if how_ == "two" else exact(k), "status": "open", "createdT": t, "lastSeenT": int(time.time()) + 86400})
        Path(state, "comments", WEB + ".json").write_text(json.dumps({"threads": rows}))
        cls.port, cls.token = _free_port(), "testtok-passagereveal"
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
                       "threads": len(THREADS), "replyUuid": REPLY_UUID, "streamUuid": STREAM_UUID, "transcript": self.transcript, "otherSid": API}, f)
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

    def _clear(self, a, p):
        return not (p["l"] < a["r"] and p["r"] > a["l"] and p["t"] < a["b"] and p["b"] > a["t"])

    def _beside(self, m, tid, where):
        """The landing flashed the passage, then opened the thread on it; the box covers none of the passage, sits in the
        window, and says nothing more."""
        self.assertEqual(m["popTid"], tid, "%s: the thread's box is open: %r" % (where, m))
        self.assertTrue(m["inView"] and m["shown"], "%s: the passage is on screen, drawn: %r" % (where, m))
        seq = m["seq"]
        self.assertIn("flash:mark:" + tid, seq, "%s: the passage itself flashed: %r" % (where, seq))
        if ("pop:" + tid) in seq:
            self.assertLess(seq.index("flash:mark:" + tid), seq.index("pop:" + tid), "%s: the landing first, then the box: %r" % (where, seq))
        self.assertTrue(self._clear(m["mark"], m["pop"]), "%s: the box does not cover the passage: passage %r, box %r" % (where, m["mark"], m["pop"]))
        self.assertGreaterEqual(m["pop"]["t"], 0, "%s: the box is in the window: %r" % (where, m["pop"]))
        self.assertLessEqual(m["pop"]["b"], m["vh"], "%s: the box is in the window: %r" % (where, m["pop"]))
        self.assertIsNone(m["lost"], "%s: a passage found says nothing more: %r" % (where, m))

    def test_passages_land_whole_and_the_box_steps_off_only_for_the_landing(self):
        r = self._drive()
        self.assertEqual(sorted(r["ticks"]), sorted(t[0] for t in THREADS), "a tick for each thread: %r" % r["ticks"])
        # 1. the last reply's final line, from far up: the box stays clear of the passage after the late fork control
        self.assertFalse(r["endBefore"]["tailRendered"], "the reader was far up, the tail outside the rendered window: %r" % r["endBefore"])
        self._beside(r["end"], "tid-end", "the end of the chat")
        # 2. a passage in a closed fold: the fold opens, the passage is at the top of the pane and flashes, the box beside it
        fb = r["foldBefore"]
        self.assertIsNotNone(fb["fold"], "the reply folds the passage in a <details>: %r" % fb)
        self.assertFalse(fb["fold"]["open"], "the fold starts closed: %r" % fb)
        f = r["fold"]
        self.assertTrue(f["fold"]["open"], "the landing opened the fold: %r" % f)
        self._beside(f, "tid-fold", "a passage in a closed fold")
        self.assertLess(abs(f["mark"]["t"] - f["content"]["t"]), 3, "the passage is at the top of the pane: %r" % f)
        # 3. a passage in three pieces: every piece flashes, not only the first
        sp = r["split"]
        self.assertGreaterEqual(sp["pieces"], 3, "the bold phrase splits the highlight: %r" % sp)
        self.assertEqual(sp["flashed"], list(range(sp["pieces"])), "every piece of the highlight flashed: %r" % sp)
        self._beside(sp, "tid-split", "a passage in pieces")
        # 4. a passage its reply keeps hidden: the fold's heading lands at the top and flashes, and the box says so, clear of it
        h = r["hidden"]
        self.assertEqual(h["popTid"], "tid-hidden", h)
        self.assertEqual(h["lost"], FOLDED, "the box says the passage could not be shown: %r" % h)
        self.assertFalse(h["shown"], "the passage itself stays hidden: %r" % h)
        head = h["fold"]["head"]
        self.assertLess(abs(head["t"] - h["content"]["t"]), 3, "the fold's heading is at the top of the pane: %r" % h)
        self.assertIn("flash:summary", h["seq"], "the heading flashed: %r" % h["seq"])
        self.assertTrue(self._clear(head, h["pop"]), "the box does not cover the heading: %r" % h)
        # 5. a passage from one closed fold into the next: both open, every piece drawn
        self.assertIsNotNone(r["twoB"], "the reply has the second fold: %r" % r)
        self.assertGreaterEqual(r["twoB"]["pieces"], 2, "the passage runs on into the second fold (its heading and its text): %r" % r["twoB"])
        self.assertTrue(r["two"]["fold"]["open"], "the first fold opened: %r" % r["two"])
        self.assertTrue(r["twoB"]["open"], "the second fold opened too, not only the first piece's: %r" % r["twoB"])
        self._beside(r["two"], "tid-two", "a passage across two folds")
        # 6, 7. the passage moves into the box while the pointer is over it, or while the reader types in it: the box holds still,
        # and steps off the passage once the pointer or the focus leaves it
        for name, why in (("hover", "the pointer over the box"), ("type", "typing in the box")):
            a, b, c = r[name + "0"], r[name + "1"], r[name + "2"]
            if name == "type":
                self.assertTrue(a["focused"], "the box holds the focus: %r" % a)
            else:
                self.assertTrue(a["hovered"], "the pointer is over the box: %r" % a)
            self.assertIsNotNone(a["box"], "%s: the box is open: %r" % (why, a))
            self.assertLessEqual(b["mark"]["t"], a["mark"]["t"] - 30, "%s: the growing message box raised the passage (a move the box would follow): %r -> %r" % (why, a, b))
            self.assertFalse(self._clear(b["mark"], b["box"]), "%s: the risen passage is under the still box: %r" % (why, b))
            self.assertEqual(b["box"], a["box"], "%s: the box did not move: %r -> %r" % (why, a["box"], b["box"]))
            self.assertTrue(self._clear(c["mark"], c["box"]), "%s, then left: the box stepped off the passage: %r" % (why, c))
        # 8. a session still writing, from the moment the box opened: the passage is never under the box, and once the landing
        # settled the passage and the box hold still (follow mode paused) while the go-to-bottom chip shows the text below
        st = r["stream"]
        self.assertTrue(all(x["arrived"] for x in st), "the streamed replies reached the page: %r" % st)
        for x in st:
            self.assertTrue(self._clear(x["mark"], x["box"]), "the passage stays uncovered as text streams in: %r" % x)
        settled = [x for x in st if x["t"] > 1700]
        self.assertGreaterEqual(len(settled), 2, "replies streamed in after the landing settled: %r" % st)
        for x in settled:
            self.assertEqual((x["mark"], x["box"], x["st"]), (settled[0]["mark"], settled[0]["box"], settled[0]["st"]),
                             "the passage, the box and the view hold still as text streams in: %r" % settled)
        self.assertTrue(st[-1]["chip"], "the go-to-bottom chip shows the new text below: %r" % st[-1])
        # 9. scrolling back to the bottom resumes following
        self.assertTrue(r["resume0"]["bottom"], "the reader wheeled back to the bottom: %r" % r["resume0"])
        self.assertTrue(r["resume1"]["bottom"], "the next reply was followed, the view at the bottom: %r" % r["resume1"])
        self.assertGreater(r["resume1"]["st"], r["resume0"]["st"], "the view moved down with it: %r" % r)
        self.assertFalse(r["resume1"]["chip"], "no chip at the bottom: %r" % r["resume1"])
        # 15. a scroll up after the tail shrank under a paused view is a reader off the bottom: still paused (the review of #279,
        # where the room under the view was lowered to meet the reader, the scroll's end read them as at the bottom, following
        # resumed and the view jumped 40 px); a scroll down to the bottom resumes following
        s0, s1, s2, s3, s4, s5 = (r["shrink%d" % i] for i in range(6))
        self.assertTrue(s0["bottom"], "the landing on the last reply put the view at the bottom: %r" % s0)
        self.assertLess(abs(s1["mark"]["t"] - s0["mark"]["t"]), 3, "the 80 px shrink under the paused view moved nothing: %r -> %r" % (s0, s1))
        self.assertFalse(s2["bottom"], "40 px up from the bottom is off the bottom: %r" % s2)
        self.assertTrue(s2["chip"], "…with the chip showing the way back: %r" % s2)
        self.assertLess(abs((s2["mark"]["t"] - s1["mark"]["t"]) - (s1["st"] - s2["st"])), 3, "the view moved by the reader's scroll alone: %r -> %r" % (s1, s2))
        self.assertLess(s1["st"] - s2["st"], 60, "…the wheel's 40 px, no jump on top: %r -> %r" % (s1, s2))
        self.assertLess(abs(s3["mark"]["t"] - s2["mark"]["t"]), 3, "still paused: the next reply moves nothing: %r -> %r" % (s2, s3))
        self.assertTrue(s3["chip"] and not s3["bottom"], s3)
        self.assertTrue(s4["bottom"] and not s4["chip"], "the reader wheeled down to the bottom: %r" % s4)
        self.assertTrue(s5["bottom"] and not s5["chip"], "following resumed: the next reply was followed: %r" % s5)
        # 16. 300 px leave the transcript above a reader paused 50 px above the end: the passage holds still and the room under
        # the view moves up with the text, so the reader can still scroll only the 50 px to the end, where following resumes with
        # no jump (the review of #280: the room stayed where it was, 300 px of blank space below the last line, and the view
        # jumped by it as following resumed)
        b0, b1, b2, b3 = (r["above%d" % i] for i in range(4))
        self.assertTrue(b0["above"], "the stand-in element is above the view: %r" % b0)
        self.assertFalse(b0["bottom"], "50 px up from the end is off the bottom: %r" % b0)
        self.assertLess(abs(b0["room"] - 50), 15, "…by the wheel's 50 px: %r" % b0)
        self.assertLess(abs(b1["mark"]["t"] - b0["mark"]["t"]), 3, "the shrink above moved nothing on screen: %r -> %r" % (b0, b1))
        self.assertLess(abs(b1["room"] - b0["room"]), 5, "the reader can scroll as far as before, no further: %r -> %r" % (b0, b1))
        self.assertTrue(b1["chip"] and not b1["bottom"], "still paused: %r" % b1)
        self.assertTrue(b2["bottom"] and not b2["chip"], "the reader wheeled down to the end: %r" % b2)
        self.assertLess(b2["blank"], 30, "a wheel past the end shows no blank stretch under the last line: %r" % b2)
        self.assertLess(abs((b0["mark"]["t"] - b2["mark"]["t"]) - b0["room"]), 5,
                        "the passage rose by the 50 px to the end, and stayed there as following resumed: %r -> %r" % (b1, b2))
        self.assertTrue(b3["bottom"] and not b3["chip"], "following resumed: the next reply was followed: %r" % b3)
        # 14. text written while the reader was away from a held tab: back on it, the passage where they left it, still paused
        a0, a1, a2 = r["away0"], r["away1"], r["away2"]
        self.assertTrue(a0["bottom"], "the landing on the last reply put the view at the bottom: %r" % a0)
        self.assertTrue(a1["arrived"], "the reply written while away is in the transcript: %r" % a1)
        self.assertTrue(a1["mark"] and a1["content"]["t"] <= a1["mark"]["t"] and a1["mark"]["b"] <= a1["content"]["b"], "the passage is in view: %r" % a1)
        self.assertLess(abs(a1["mark"]["t"] - a0["mark"]["t"]), 4, "…where the reader left it: %r -> %r" % (a0, a1))
        self.assertFalse(a1["bottom"], "not carried to the new bottom: %r" % a1)
        self.assertTrue(a1["chip"], "the chip shows the text below: %r" % a1)
        self.assertEqual(a2["mark"], a1["mark"], "follow mode stays paused: the next reply moves nothing: %r -> %r" % (a1, a2))
        self.assertTrue(a2["chip"] and not a2["bottom"], a2)
        # 10. a paused landing at the bottom, another tab and back: the passage where the reader left it, the pause standing; the
        # session then writes for ten seconds and the passage holds still in every frame, the chip showing the text below
        h0, h1, hs, tops = r["held0"], r["held1"], r["held"], r["heldTops"]["tops"]
        self.assertTrue(h0["bottom"], "the landing on the passage's reply put the view at the bottom: %r" % h0)
        self.assertLess(abs(h1["mark"]["t"] - h0["mark"]["t"]), 4, "back on the tab, the passage is where the reader left it: %r -> %r" % (h0, h1))
        self.assertTrue(all(x["arrived"] for x in hs), "the replies reached the page: %r" % hs)
        for x in hs:
            self.assertTrue(x["chip"] and not x["bottom"], "the pause stands, the chip shows the text below: %r" % hs)
        self.assertGreater(r["heldTops"]["frames"], 100, "the passage was read in every frame of the ten seconds: %r" % r["heldTops"])
        moved = [x for x in tops if x["t"] is None or abs(x["t"] - h1["mark"]["t"]) > 2]
        self.assertEqual(moved, [], "the passage holds still on screen in every frame while the session writes: from %r, %r" % (h1["mark"]["t"], tops))
        # 10b. a view following at the bottom, another tab and back: at the bottom and following, exactly, reply after reply
        self.assertTrue(r["follow0"]["bottom"] and not r["follow0"]["chip"], "the go-to-bottom chip put the view back to following: %r" % r["follow0"])
        self.assertTrue(r["tab0"]["bottom"] and not r["tab0"]["chip"], "back on the tab, the view is at the bottom: %r" % r["tab0"])
        for x in r["tab"]:
            self.assertTrue(x["bottom"] and not x["chip"], "the view follows each reply: %r" % r["tab"])
        # 11. the message box grows under a landing on the last reply: the passage stays clear of the thread's box
        c0, c1 = r["compose0"], r["compose1"]
        self.assertGreater(c1["h"], c0["h"] + 40, "the message box grew: %r -> %r" % (c0["h"], c1["h"]))
        self.assertTrue(self._clear(c0["mark"], c0["box"]), "the landing left the passage clear: %r" % c0)
        self.assertLess(c1["mark"]["t"], c0["mark"]["t"] - 30, "the view re-pinned the bottom, raising the passage: %r -> %r" % (c0, c1))
        self.assertTrue(self._clear(c1["mark"], c1["box"]), "the thread's box stepped off the risen passage: %r" % c1)
        # 13. End, PageDown and Space back to the bottom after a landing resume following, run after run
        for k in r["keys"]:
            self.assertFalse(k["before"]["bottom"], "the landing paused follow mode, the reply landed below: %r" % k)
            self.assertTrue(k["reached"], "%s took the view to the bottom: %r" % (k["key"], k))
            self.assertTrue(k["after"]["bottom"], "after %s to the bottom the next reply is followed: %r" % (k["key"], r["keys"]))
        # 12. a phone flick back to the bottom whose momentum outlasts the input window resumes following
        self.assertFalse(r["flick0"]["bottom"], "the landing paused follow mode, so the reply landed below: %r" % r["flick0"])
        self.assertTrue(r["flick0"]["chip"], "…with the chip showing it: %r" % r["flick0"])
        self.assertIsNotNone(r["flick"]["bottomAt"], "the flick reached the bottom: %r" % r["flick"])
        self.assertGreater(r["flick"]["bottomAt"], 150, "…after the input window, the momentum's own time: %r" % r["flick"])
        self.assertTrue(r["flick1"]["bottom"], r)
        self.assertTrue(r["flick2"]["bottom"], "the next reply was followed: following resumed: %r" % r["flick2"])
        self.assertFalse(r["flick2"]["chip"], r["flick2"])

if __name__ == "__main__":
    unittest.main()
