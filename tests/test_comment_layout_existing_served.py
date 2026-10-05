#!/usr/bin/env python3
"""A sent comment's box takes the thread's layout the moment Enter is pressed, and a Comment on a passage that already has
a thread opens that thread, on the real /chat page (the user 2026-10-01, who approved both: the box no longer jumps seconds
after the gesture, when the kernel answers, and one passage never silently grows a second thread).

The same hermetic lab as test_comment_create_served.py (the notes-api demo world, session web on the fake Agent SDK, the
first reply already carrying a thread on its opening words), plus a third reply carrying a thread on a sentence whose
phrases repeat, answered three minutes after its prompt so its rail stamp shows a label, with the page's copy of the
kernel's answer HELD on cue, and the page's copy of a drop's saved path held on a cue of its own. One driver run per test
process; each test reads its part. Asserted:
  the box at the selection turns into the thread's layout at Enter (70% wide, 60% tall, right-aligned, top 120) before the
  kernel answers, and the thread the answer brings keeps exactly that rect;
  a remembered size (the user's own pull, kept per page) is the size the sent box and its thread take;
  a box the user maximized keeps its size and place through the send and the thread;
  a Comment on a passage that overlaps the existing thread's (same reply, overlapping text), with no comment box open,
  opens that thread at the thread default, with "New thread instead", which opens the comment box on exactly the
  selection, and sending it makes the second thread;
  a passage of the same reply that does not overlap opens the comment box; a click on the thread's highlight opens the
  thread at the thread default, without the offer;
  a Comment on a passage overlapping a send whose thread has not arrived yet, the only send on that passage, re-opens
  that send's box at the thread default, even after a maximize made an earlier box's spot the user's;
  the thread a Comment opens has the caret in its reply box, on the key, the selection menu, the quote chip and the posted
  run, and the words typed next land there and not in the chat's message box; a later rebuild of a box the key's Comment
  opened that way (a status change on a thread that has messages) leaves the caret in the chat's message box, where the
  person moved it; on the key, a Comment while that thread's box is already open (from its highlight, the caret outside
  it) refreshes the box the highlight opened in place and puts the caret in it;
  a file whose upload finishes while the comment box is open and editable lands in that box; a file that lands while the
  sent view waits reaches the chat's attachment strip and is still there after the thread arrives; on the tab shown, a
  chat message held by "Wait for the upload" sends with its file when that upload lands while a sent comment waits, and
  so does one whose "still uploading" dialog is still open, the dialog closing itself;
  in the third reply, with its thread the only thread or send on it, "the cache" selected at its second copy, inside the
  thread's highlight, opens that thread though its first copy is outside, on the key, the selection menu and the posted
  run, and "drains the queue" selected at its second copy, after the highlight, opens the thread its first copy lies in,
  on the key; on the key, with no send waiting for its thread, once a newer thread holds the first copy of "the cache",
  its second copy, selected inside the older thread's highlight and no other, still opens the thread it was selected in;
  on the key, "the worker" selected at its second copy, inside a send whose thread has not arrived yet, with every other
  send on that reply acked, re-opens that send's box though its first copy lies in the thread's passage;
  on the key, with no send waiting for its thread, a selection that starts in a reply's last words and runs into the next
  turn opens the only thread it meets in that reply, an open one, and a selection that starts on a reply's rail stamp and
  runs into a thread's passage opens the only thread it meets, an open one.
Skips LOUDLY without the extension deps or a Playwright browser.
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
T_TWO = "cccccccc-1111-2222-3333-c0c0a1e1c0c0"     # the thread the third reply already carries
A1 = ("Cache the notes-api list responses for thirty seconds, keyed by the query string. A longer window starts serving "
      "stale titles after an edit, and the export stream stays uncached.")
A2 = ("Keep the retry cap at two minutes on every sync path. Longer caps only delay the error the user will see anyway. "
      "The header is written once per export, and the footer carries the row count.")
A3 = ("The importer warms the cache before the first request. Each export then refills the cache from the database, "
      "and the worker drains the queue. After a restart the worker drains the queue again.")
P_OLD = "Cache the notes-api list responses"
P_TWO = "Each export then refills the cache from the database, and the worker drains the queue"   # its phrases repeat
PASSAGES = {
    "overlap": "notes-api list responses for thirty seconds",   # overlaps the existing thread's P_OLD
    "apart": "stale titles after an edit",                       # the same reply, no overlap
    "layout": "Keep the retry cap at two minutes",
    "remembered": "Longer caps only delay the error",
    "maximized": "The header is written once per export",
    "inflight": "the footer carries the row count",
    "inflightInside": "carries the row",                         # inside the in-flight passage
    "dropped": "the export stream stays uncached",               # a file lands while this send waits
    "repeatInside": "the cache",       # its second occurrence is inside P_TWO's highlight, its first is not
    "repeatOutside": "drains the queue",   # its second occurrence is after P_TWO's highlight, its first is inside it
    "inflightRepeat": "After a restart the worker drains the queue again",   # a send held in flight on A3's last sentence
    "inflightRepeatInside": "the worker",   # its second occurrence is inside that send's passage, its first inside P_TWO's
    "acrossFrom": "row count.",   # the last words of "inflight" (A2's end), where a selection into the next turn starts
    "gutterTo": "request. Each export",   # where a selection from the third reply's rail stamp ends: in P_TWO's first words
    "heldSend": "keyed by the query string",   # a comment sent while a chat message waits for its upload
    "openAgain": "Cache the",   # P_OLD's first words, before the overlap case's thread: it meets P_OLD's thread alone
    "gateSend": "A longer window starts serving",   # a comment sent before a chat message opens its upload dialog
}
DROP_NAME = "cmt-sent-drop.txt"
OPEN_DROP_NAME = "cmt-open-drop.txt"   # dropped, and its upload finishing, while the comment box is open and editable
HELD_NAME = "chat-held-upload.txt"   # the chat's own upload, which a held chat message waits for
HELD_MSG = "Which rows does this file hold?"
GATE_NAME = "chat-gate-upload.txt"   # the chat's own upload, still uploading when its message's dialog opens
GATE_MSG = "What does this file change?"
FAKE_REPLY = "Two minutes is past the longest outage the sync has ridden out."   # the fake Agent SDK's reply to a message


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
  window.__hold = false; window.__held = []; window.__lastCmt = null;
  window.__holdDrops = false; window.__heldDrops = [];   // the kernel's saved-path answers to a drop, held on their own cue
  const held = (m) => m && ((m.type === "comments" && m.id === sid) || m.type === "commentCreated" || m.type === "commentCreateFailed");
  window.addEventListener("message", (e) => {
    const m = e.data;
    if (m && m.type === "comments" && m.id === sid && !m.__synth) window.__lastCmt = m;   // the newest real frame (__frameWith)
    if (!m || m.__released) return;
    if (window.__hold && held(m)) { e.stopImmediatePropagation(); window.__held.push(m); }
    else if (window.__holdDrops && m.type === "droppedPath") { e.stopImmediatePropagation(); window.__heldDrops.push(m); }
  }, true);
  window.__release = () => {
    window.__hold = false;
    const q = window.__held; window.__held = [];
    for (const m of q) window.dispatchEvent(new MessageEvent("message", { data: Object.assign({}, m, { __released: true }) }));
  };
  window.__releaseDrops = () => {
    window.__holdDrops = false;
    const q = window.__heldDrops; window.__heldDrops = [];
    for (const m of q) window.dispatchEvent(new MessageEvent("message", { data: Object.assign({}, m, { __released: true }) }));
  };
  // a full rebuild of the open thread's box that nobody asked for: the newest real frame again, one thread's status changed
  window.__frameWith = (tid, status) => {
    if (!window.__lastCmt) return false;
    const f = JSON.parse(JSON.stringify(window.__lastCmt));
    for (const t of f.threads || []) if (t.tid === tid) t.status = status;
    window.dispatchEvent(new MessageEvent("message", { data: Object.assign(f, { __synth: true, __released: true }) }));
    return true;
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
  await page.waitForFunction(() => document.querySelectorAll("#content mark.cmt-hl").length > 1, null, { timeout: 30000 });
} catch (e) { await done("the chat never showed the replies and the two threads: " + String(e).split("\n")[0]); }
out.vw = await page.evaluate(() => [window.innerWidth, window.innerHeight]);

// select the `nth` occurrence of `text` in the newest turn holding it, across text nodes (a passage can run into or out of a
// highlight's <mark>)
const select = (text, nth = 0) => page.evaluate(([text, nth]) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.textContent || "").includes(text));
  if (!turn) return false;
  const nodes = [];
  const walker = document.createTreeWalker(turn.querySelector(".md") || turn, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
  const all = nodes.map((n) => n.data).join("");
  let at = -1;
  for (let i = 0; i <= nth; i++) { at = all.indexOf(text, at + 1); if (at < 0) return false; }
  const point = (off) => { let acc = 0; for (const n of nodes) { if (off <= acc + n.data.length) return [n, off - acc]; acc += n.data.length; } return null; };
  const a = point(at), b = point(at + text.length);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(a[0], a[1]); r.setEnd(b[0], b[1]); sel.addRange(r);
  return sel.toString() === text;
}, [text, nth]);
// open the comment box on a selection by one of the Comment roads: the key, the selection menu, the quote chip's Comment, or
// the posted run: a run message in the palette's form, posted by the lab (the aim the chat hands the palette as it opens,
// posted back; a key run from the shell posts no aim and reads the live selection, as the key road does)
const road = async (how, text, nth = 0) => {
  await page.evaluate(() => document.activeElement?.blur?.());   // as a selection made with the mouse leaves it: nothing focused
  await select(text, nth);
  if (how === "key") await page.keyboard.press("Control+/");
  else if (how === "menu") {
    await page.evaluate(() => {
      const r = window.getSelection().getRangeAt(0), b = r.getBoundingClientRect();
      const at = r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement;
      at.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: b.left + 4, clientY: b.top + 4 }));
    });
    await page.locator(".ctx-item", { hasText: /^Comment/ }).first().click({ timeout: 5000 });
  } else if (how === "chip") {
    await page.waitForFunction((head) => Array.from(document.querySelectorAll("#composer-chips .composer-chip-label")).some((l) => (l.textContent || "").includes(head)),
                               text.slice(0, 9), { timeout: 5000 }).catch(() => {});
    await page.click('#composer-chips [data-act="chip-comment"]', { timeout: 5000 });
  } else if (how === "posted") {
    await page.evaluate(() => window.dispatchEvent(new MessageEvent("message", { data: { romp: "commentSelection", held: window.__rompCommentAim() } })));
  }
  await page.waitForSelector("#cmt-pop", { timeout: 8000 });
  await page.waitForTimeout(100);
};
const comment = (text, nth = 0) => road("key", text, nth);
const pop = () => page.evaluate(() => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const r = p.getBoundingClientRect();
  return { mode: p.dataset.mode, tid: p.dataset.tid, status: p.dataset.status, rect: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
           quote: ((p.querySelector(".cmt-quote") || {}).textContent || "").trim(), offer: ((p.querySelector(".cmt-newthread") || {}).textContent || "") };
});
// where the caret is: the open box's reply input, the chat's message box, or elsewhere
const caret = () => page.evaluate(() => {
  const ae = document.activeElement;
  if (ae && ae === document.querySelector("#cmt-pop .cmt-input")) return "box";
  if (ae && ae === document.getElementById("composer-input")) return "chat";
  return !ae || ae === document.body ? "body" : (ae.id || ae.className || ae.tagName);
});
// type `words` wherever the caret is, say where they landed, then take them back out so the later scenes start clean
const typed = async (words) => {
  await page.keyboard.type(words);
  return page.evaluate((w) => {
    const where = { box: false, chat: false, seen: {} };
    for (const [k, t] of [["box", document.querySelector("#cmt-pop .cmt-input")], ["chat", document.getElementById("composer-input")]]) {
      if (t) where.seen[k] = t.value.slice(0, 120);
      if (!t || !t.value.includes(w)) continue;
      where[k] = true;
      t.value = t.value.replace(w, "");
      t.dispatchEvent(new Event("input"));
    }
    return where;
  }, words);
};
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
// the threads whose highlights the live selection meets in the reply it starts in, and the sends on the page whose thread has
// not arrived yet (their highlight is the optimistic mark, data-tid "pending:…")
const meets = () => page.evaluate(() => {
  const sel = window.getSelection();
  if (!sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  const turn = (r.startContainer.nodeType === 1 ? r.startContainer : r.startContainer.parentElement).closest(".turn");
  const tids = turn ? Array.from(turn.querySelectorAll(".md mark.cmt-hl")).filter((m) => r.intersectsNode(m)).map((m) => m.dataset.tid) : [];
  return { tids: Array.from(new Set(tids)), pending: document.querySelectorAll('#content mark.cmt-hl[data-tid^="pending:"]').length };
});

// the thread a Comment opens takes the caret, and the words typed next, on every road (the key road is the overlap case
// below): the menu, the chip, the posted run. Before the overlap case sends its second thread, so the passage still
// has the one thread
out.roads = {};
for (const how of ["menu", "chip", "posted"]) {
  try {
    await road(how, cfg.p.overlap);
    const got = { ...(await pop()), caret: await caret() };
    out.roads[how] = { ...got, typed: await typed("Where is it read?") };
  } catch (e) { out.roads[how] = { error: String(e).split("\n")[0] }; }
  await closePop();
}
// …and a rebuild of that box that nobody asked for (a status change on a thread with messages) leaves the caret where the
// person moved it
{
  await comment(cfg.p.overlap);
  const opened = await caret();
  await page.focus("#composer-input");
  const flipped = await page.evaluate(() => window.__frameWith("tid-old", "merging"));
  await page.waitForTimeout(150);
  out.rebuilt = { opened, flipped, status: ((await pop()) || {}).status, caret: await caret() };
  await page.evaluate(() => window.__frameWith("tid-old", "open"));
  await page.waitForTimeout(150);
  await closePop();
}
// a passage of the reply that already carries a thread, overlapping it: the thread opens, with the caret, offering a new one
{
  await comment(cfg.p.overlap);
  const opened = await pop();
  const caretAt = await caret();
  const words = await typed("Where is it read?");
  await page.click("#cmt-pop .cmt-newthread").catch((e) => { out.offerClick = String(e).split("\n")[0]; });
  await page.waitForTimeout(150);
  const fresh = await pop();
  await page.focus("#cmt-pop .cmt-input").catch(() => {});
  await page.keyboard.type("Is thirty seconds per query string?");
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => document.getElementById("cmt-pop")?.dataset.mode === "thread", null, { timeout: 30000 }).catch(() => {});
  out.overlap = { opened, caret: caretAt, typed: words, fresh };
  await closePop();
}
// a passage of the same reply that does not overlap: the comment box
{
  await comment(cfg.p.apart);
  out.apart = await pop();
  await closePop();
}
// a click on the thread's own highlight: the thread, with no offer. Then, with that box still open and the caret outside it,
// a Comment on a passage that meets only that thread: the box is the one already open, refreshed in place (a refresh that
// returns before renderCommentPopover's focus line, and before the full build's "New thread instead", so the box shows no
// offer; not asserted here), and the caret still lands in its reply box
{
  await page.evaluate((tid) => document.querySelector(`#content mark.cmt-hl[data-tid="${tid}"]`)?.click(), "tid-old");
  await page.waitForSelector("#cmt-pop", { timeout: 8000 }).catch(() => {});
  out.markClick = await pop();
  // a mark on the open box's element: a refresh in place keeps the element, a full rebuild makes a new one
  await page.evaluate(() => { const p = document.getElementById("cmt-pop"); if (p) p.dataset.labSame = "1"; });
  try {
    await comment(cfg.p.openAgain);
    const same = await page.evaluate(() => document.getElementById("cmt-pop")?.dataset.labSame === "1");
    out.openAgain = { same, ...(await pop()), caret: await caret() };
  } catch (e) { out.openAgain = { error: String(e).split("\n")[0] }; }
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
// a Comment overlapping a send whose thread has not arrived yet, the only send on that passage, re-opens that send's box.
// Armed first, whatever ran before: a maximize makes the box's spot the user's, so only the Comment gesture's own reset of
// it puts the in-flight box at the thread default
{
  await comment(cfg.p.apart);
  await page.click("#cmt-pop .cmt-max");
  await page.waitForTimeout(150);
  out.inflightArmed = ((await pop()) || {}).rect || null;
  await closePop();
  await page.evaluate(() => localStorage.removeItem("romp:cmtPopSize"));
  await comment(cfg.p.inflight);
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type("Is it ever empty?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.mouse.click(600, 30);
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
  await comment(cfg.p.inflightInside);
  out.inflight = await pop();
  await page.evaluate(() => window.__release());
  await adopted();
  await closePop();
}
// a file dropped on the page, its upload finishing while the comment box is still open and editable: its saved path lands in
// that box. The path is then taken back out, so the scene below opens the box on the same passage empty
{
  out.droppedOpen = {};
  try {
    await comment(cfg.p.dropped);
    out.droppedOpen.box = await page.evaluate(() => {
      const p = document.getElementById("cmt-pop"), t = p && p.querySelector(".cmt-input");
      return { mode: p ? p.dataset.mode : null, readOnly: t ? t.readOnly : null };
    });
    await page.evaluate((name) => {
      const dt = new DataTransfer();
      dt.items.add(new File(["a synthetic attachment\n"], name, { type: "text/plain" }));
      document.body.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, cfg.openDropName);
    await page.waitForFunction(() => !document.querySelector("#composer-files .composer-file-pending"), null, { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(150);
    out.droppedOpen.landed = await page.evaluate((name) => ({
      chat: Array.from(document.querySelectorAll("#composer-files .composer-file")).filter((b) => (b.title || "").includes(name)).length,
      pending: document.querySelectorAll("#composer-files .composer-file-pending").length,
      box: ((document.querySelector("#cmt-pop .cmt-input") || {}).value || "").includes(name),
    }), cfg.openDropName);
    await page.evaluate(() => { const t = document.querySelector("#cmt-pop .cmt-input"); if (t) { t.value = ""; t.dispatchEvent(new Event("input")); } });
  } catch (e) { out.droppedOpen.error = String(e).split("\n")[0]; }
  await closePop();
  await page.evaluate((name) => Array.from(document.querySelectorAll("#composer-files .composer-file")).find((b) => (b.title || "").includes(name))?.querySelector(".composer-file-x")?.click(), cfg.openDropName);
}
// a file that lands while the sent view waits: a page drop after Enter, its saved path answering before the thread does
{
  await comment(cfg.p.dropped);
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type("Does the export read this file?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.waitForTimeout(150);
  const sent = await pop();
  await page.evaluate((name) => {
    const dt = new DataTransfer();
    dt.items.add(new File(["a synthetic attachment\n"], name, { type: "text/plain" }));
    document.body.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
  }, cfg.dropName);
  const where = () => page.evaluate((name) => ({
    chat: Array.from(document.querySelectorAll("#composer-files .composer-file:not(.composer-file-pending)")).filter((b) => (b.title || "").includes(name)).length,
    pending: document.querySelectorAll("#composer-files .composer-file-pending").length,
    box: ((document.querySelector("#cmt-pop .cmt-input") || {}).value || "").includes(name),
  }), cfg.dropName);
  // the saved path's answer: the in-flight chip retires (into the box or the chat's strip, whichever took it)
  await page.waitForFunction(() => !document.querySelector("#composer-files .composer-file-pending"), null, { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(150);
  const during = await where();
  await page.evaluate(() => window.__release());
  const ok = await adopted();
  await page.waitForTimeout(150);
  out.dropped = { sent, during, adopted: ok, after: await where(), thread: await pop() };
  await closePop();
  await page.evaluate((name) => Array.from(document.querySelectorAll("#composer-files .composer-file")).find((b) => (b.title || "").includes(name))?.querySelector(".composer-file-x")?.click(), cfg.dropName);
}
// a selection that starts on the third reply's rail stamp (the time label drawn before its prose, selectable text) and ends in
// the first words of the thread's passage on that reply: the selected text begins with the stamp's words, which the prose
// does not hold
{
  await page.evaluate(() => document.activeElement?.blur?.());
  out.gutter = await page.evaluate((text) => {
    const turn = Array.from(document.querySelectorAll("#content .turn")).reverse().find((n) => (n.querySelector(".md")?.textContent || "").includes(text));
    if (!turn) return { why: "no reply holds " + text };
    const marker = turn.querySelector(".time-marker");
    const mnode = marker && Array.from(marker.childNodes).find((n) => n.nodeType === 3 && n.data);
    const nodes = [], w = document.createTreeWalker(turn.querySelector(".md"), NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) nodes.push(n);
    const at = nodes.map((n) => n.data).join("").indexOf(text) + text.length;
    let acc = 0, to = null;
    for (const n of nodes) { if (at <= acc + n.data.length) { to = [n, at - acc]; break; } acc += n.data.length; }
    if (!mnode || !to) return { why: "no stamp text to start from, or no point to end at", marker: marker ? marker.textContent : null };
    const r = document.createRange(); r.setStart(mnode, 0); r.setEnd(to[0], to[1]);
    const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
    const got = window.getSelection().getRangeAt(0);
    return { marker: marker.textContent, startsInStamp: got.startContainer === mnode && got.startOffset === 0, selected: sel.toString() };
  }, cfg.p.gutterTo);
  out.gutterMeets = await meets();
  try {
    await page.keyboard.press("Control+/");
    await page.waitForSelector("#cmt-pop", { timeout: 8000 });
    await page.waitForTimeout(100);
    out.gutterPop = await pop();
  } catch (e) { out.gutterPop = { error: String(e).split("\n")[0] }; }
  await closePop();
}
// in the third reply, with its thread the only thread or send on it: "the cache" at its second copy, inside the thread's
// highlight, opens the thread on the key, the menu and the posted run, and "drains the queue" at its second copy, after the
// highlight, with its first copy inside it, opens it on the key
{
  await comment(cfg.p.repeatInside, 1);
  out.repeatInside = { ...(await pop()), caret: await caret() };
  await closePop();
  try {
    await road("menu", cfg.p.repeatInside, 1);
    out.repeatInsideMenu = await pop();
  } catch (e) { out.repeatInsideMenu = { error: String(e).split("\n")[0] }; }
  await closePop();
  try {
    await road("posted", cfg.p.repeatInside, 1);
    out.repeatInsidePosted = await pop();
  } catch (e) { out.repeatInsidePosted = { error: String(e).split("\n")[0] }; }
  await closePop();
  await comment(cfg.p.repeatOutside, 1);
  out.repeatOutside = await pop();
  await closePop();
}
// …and with a NEWER thread on the phrase's first occurrence, on the key: a comment there makes one (selected outside every
// highlight, it opens the comment box), then the phrase selected again inside the older thread's highlight opens the older
// thread, the one it was selected in, not the newer one its first occurrence lies in. After the cases above, which need the
// first occurrence outside every highlight
{
  out.tie = {};
  try {
    await comment(cfg.p.repeatInside, 0);
    out.tie.composer = await pop();
    await page.focus("#cmt-pop .cmt-input");
    await page.keyboard.type("Is the warm-up timed?");
    await page.keyboard.press("Enter");
    out.tie.made = await adopted() ? ((await pop()) || {}).tid : null;
    await closePop();
    out.tie.marks = await page.evaluate((tid) => {
      const m = document.querySelector(`#content mark.cmt-hl[data-tid="${tid}"]`);
      return { text: m ? m.textContent : null, insideTwo: !!(m && m.closest('mark.cmt-hl[data-tid="tid-two"]')) };
    }, out.tie.made);
    // what that selection meets, read before the key's Comment makes the same selection again
    await select(cfg.p.repeatInside, 1);
    out.tie.meets = await meets();
    await comment(cfg.p.repeatInside, 1);
    out.tie.opened = await pop();
  } catch (e) { out.tie.error = String(e).split("\n")[0]; }
  await closePop();
}
// …and selected inside a send whose thread has not arrived yet, with every other send on that reply acked (the tie's send
// above, its box adopting its thread): the second Comment, from the key, re-opens that send, though the
// phrase's first occurrence lies in the other thread's passage. After the cases above, since this send makes a thread on the
// sentence repeatOutside selects in
{
  await comment(cfg.p.inflightRepeat);
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type("Does the second drain start empty?");
  await page.evaluate(() => { window.__hold = true; });
  await page.keyboard.press("Enter");
  await page.mouse.click(600, 30);
  await page.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
  try {
    await select(cfg.p.inflightRepeatInside, 1);   // what that selection meets, read before the key's Comment makes it again
    out.inflightRepeatMeets = await meets();
    await comment(cfg.p.inflightRepeatInside, 1);
    out.inflightRepeat = await pop();
  } catch (e) { out.inflightRepeat = { error: String(e).split("\n")[0] }; }
  await page.evaluate(() => window.__release());
  await adopted();
  await closePop();
}
// a selection that starts on the last words of a reply, inside the thread the in-flight case made there, and ends in the next
// turn: the text it anchors with runs past the reply, and the part the reply's prose holds is too short for the text rule to
// find
{
  await page.evaluate(() => document.activeElement?.blur?.());
  out.acrossSelected = await page.evaluate((text) => {
    const turns = Array.from(document.querySelectorAll("#content .turn"));
    const i = turns.findIndex((n) => (n.querySelector(".md")?.textContent || "").includes(text));
    if (i < 0) return "no reply holds " + text;
    const texts = (root) => { const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), got = []; for (let n = w.nextNode(); n; n = w.nextNode()) got.push(n); return got; };
    const nodes = texts(turns[i].querySelector(".md"));
    const at = nodes.map((n) => n.data).join("").lastIndexOf(text);
    let acc = 0, from = null;
    for (const n of nodes) { if (at < acc + n.data.length) { from = [n, at - acc]; break; } acc += n.data.length; }
    const to = turns.slice(i + 1).map((t) => texts(t).find((n) => n.data.trim())).find(Boolean);
    if (!from || !to) return "no point to select from or to";
    const sel = window.getSelection(); sel.removeAllRanges();
    const r = document.createRange(); r.setStart(from[0], from[1]); r.setEnd(to, Math.min(3, to.data.length)); sel.addRange(r);
    const got = sel.toString();
    return got.startsWith(text) && got.trim().length > text.length ? "" : "selected " + JSON.stringify(got.slice(0, 80));
  }, cfg.p.acrossFrom);
  out.acrossMeets = await meets();
  try {
    await page.keyboard.press("Control+/");
    await page.waitForSelector("#cmt-pop", { timeout: 8000 });
    await page.waitForTimeout(100);
    out.across = await pop();
  } catch (e) { out.across = { error: String(e).split("\n")[0] }; }
  await closePop();
}
// a chat message held for its upload ("Wait for the upload"), whose saved path answers while a sent comment waits for its
// thread: the held message sends with the file, and the path stays out of the comment's read-only box
{
  out.heldSend = {};
  try {
    await page.evaluate(() => { document.activeElement?.blur?.(); window.__holdDrops = true; });
    await page.evaluate((name) => {
      const dt = new DataTransfer();
      dt.items.add(new File(["a synthetic attachment\n"], name, { type: "text/plain" }));
      document.body.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, cfg.heldName);
    await page.waitForSelector("#composer-files .composer-file-pending", { timeout: 10000 });
    // the citations an earlier scene's selection left on the chat's message go, so the message is the words and the file
    out.heldSend.quotesRemoved = await page.evaluate(() => {
      let n = 0;
      for (let b; n < 20 && (b = document.querySelector('#composer-chips .composer-chip-x[aria-label="Remove citation"]')); n++) b.click();
      return n;
    });
    await page.fill("#composer-input", cfg.heldMsg);
    await page.click("#composer-send");
    await page.locator(".confirm-btn", { hasText: "Wait for the upload" }).click({ timeout: 5000 });
    out.heldSend.armed = await page.inputValue("#composer-input");
    await comment(cfg.p.heldSend);
    await page.focus("#cmt-pop .cmt-input");
    await page.keyboard.type("Is the key case-sensitive?");
    await page.evaluate(() => { window.__hold = true; });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(150);
    out.heldSend.sent = await pop();
    out.heldSend.waiting = await page.evaluate((msg) => ({
      heldDrops: window.__heldDrops.length, input: document.getElementById("composer-input").value,
      posted: Array.from(document.querySelectorAll("#content .turn")).some((t) => (t.textContent || "").includes(msg)) }), cfg.heldMsg);
    await page.evaluate(() => window.__releaseDrops());
    await page.waitForFunction(() => !document.querySelector("#composer-files .composer-file-pending"), null, { timeout: 30000 }).catch(() => {});
    await page.waitForFunction((msg) => Array.from(document.querySelectorAll("#content .turn")).some((t) => (t.textContent || "").includes(msg)),
                               cfg.heldMsg, { timeout: 10000 }).catch(() => {});
    out.heldSend.during = await page.evaluate(([name, msg]) => {
      const turn = Array.from(document.querySelectorAll("#content .turn")).find((t) => (t.textContent || "").includes(msg));
      return { box: ((document.querySelector("#cmt-pop .cmt-input") || {}).value || "").includes(name),
               status: (document.getElementById("cmt-pop") || { dataset: {} }).dataset.status || null,
               input: document.getElementById("composer-input").value, posted: !!turn, postedFile: !!turn && turn.outerHTML.includes(name),
               postedText: turn ? (turn.textContent || "").slice(0, 300) : null };
    }, [cfg.heldName, cfg.heldMsg]);
    await page.evaluate(() => window.__release());
    out.heldSend.adopted = await adopted();
  } catch (e) { out.heldSend.error = String(e).split("\n")[0]; }
  await page.evaluate(() => { window.__releaseDrops(); window.__release(); });
  await closePop();
}
// a chat message whose "still uploading" dialog is still open when its upload's saved path answers, while a sent comment waits
// for its thread: the dialog closes itself, the message sends with the file, and the path stays out of the comment's
// read-only box. After the held-send scene, once that message's reply is in
{
  out.gateSend = {};
  try {
    await page.waitForFunction((reply) => Array.from(document.querySelectorAll("#content .turn")).some((t) => (t.textContent || "").includes(reply)),
                               cfg.reply, { timeout: 10000 }).catch(() => {});
    await comment(cfg.p.gateSend);
    await page.focus("#cmt-pop .cmt-input");
    await page.keyboard.type("Which edit starts the stale window?");
    await page.evaluate(() => { window.__hold = true; window.__holdDrops = true; });
    await page.keyboard.press("Enter");
    await page.waitForTimeout(150);
    out.gateSend.sent = await pop();
    await page.evaluate((name) => {
      const dt = new DataTransfer();
      dt.items.add(new File(["a synthetic attachment\n"], name, { type: "text/plain" }));
      document.body.dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true, cancelable: true }));
    }, cfg.gateName);
    await page.waitForSelector("#composer-files .composer-file-pending", { timeout: 10000 });
    // the citation the comment's selection left on the chat's message goes (a click with no mousedown, which would close the
    // sent view), so the message is the words and the file
    await page.evaluate(() => {
      for (let n = 0, b; n < 20 && (b = document.querySelector('#composer-chips .composer-chip-x[aria-label="Remove citation"]')); n++) b.click();
    });
    await page.fill("#composer-input", cfg.gateMsg);
    await page.keyboard.press("Enter");   // the message box's own Enter: a press on the send button would close the sent view first
    await page.waitForSelector("#confirm .confirm-btn", { timeout: 5000 });
    out.gateSend.waiting = await page.evaluate((msg) => ({
      dialog: !!document.getElementById("confirm"), status: (document.getElementById("cmt-pop") || { dataset: {} }).dataset.status || null,
      heldDrops: window.__heldDrops.length, input: document.getElementById("composer-input").value,
      posted: Array.from(document.querySelectorAll("#content .turn")).some((t) => (t.textContent || "").includes(msg)) }), cfg.gateMsg);
    await page.evaluate(() => window.__releaseDrops());
    await page.waitForFunction(() => !document.querySelector("#composer-files .composer-file-pending"), null, { timeout: 30000 }).catch(() => {});
    await page.waitForFunction((msg) => Array.from(document.querySelectorAll("#content .turn")).some((t) => (t.textContent || "").includes(msg)),
                               cfg.gateMsg, { timeout: 10000 }).catch(() => {});
    out.gateSend.during = await page.evaluate(([name, msg]) => {
      const turn = Array.from(document.querySelectorAll("#content .turn")).find((t) => (t.textContent || "").includes(msg));
      return { dialog: !!document.getElementById("confirm"), box: ((document.querySelector("#cmt-pop .cmt-input") || {}).value || "").includes(name),
               status: (document.getElementById("cmt-pop") || { dataset: {} }).dataset.status || null,
               input: document.getElementById("composer-input").value, posted: !!turn, postedFile: !!turn && turn.outerHTML.includes(name) };
    }, [cfg.gateName, cfg.gateMsg]);
    await page.evaluate(() => window.__release());
    out.gateSend.adopted = await adopted();
  } catch (e) { out.gateSend.error = String(e).split("\n")[0]; }
  await page.evaluate(() => { window.__releaseDrops(); window.__release(); });
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
                  agent(t0 + 65, "a2", "u2", A2, WEB),
                  user(t0 + 120, "u3", "a2", "and the export worker?", WEB),
                  agent(t0 + 305, "a3", "u3", A3, WEB)]   # three minutes after its prompt: its rail stamp shows a label
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
        # …and the one the third reply carries, on the sentence whose phrases repeat: cut at a3, the same shape
        two = [dict(r, sessionId=T_TWO) for r in parent]
        two += [user(t + 30, "c2", "a3", "does the refill block exports?", T_TWO),
                agent(t + 50, "r2", "c2", "No: the refill runs after the export has finished writing.", T_TWO)]
        Path(proj, T_TWO + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in two))
        Path(state, "sdk", T_TWO + ".json").write_text(json.dumps(
            {"sid": T_TWO, "name": "web-comment-2", "cwd": cwd, "lastSid": T_TWO, "threadOf": WEB, "forkAt": "a3",
             "alive": False, "mode": "auto", "effort": "high", "model": "claude-opus-5"}))
        cls.store = os.path.join(state, "comments", WEB + ".json")
        Path(cls.store).write_text(json.dumps({"threads": [
            {"tid": "tid-old", "sid": T_OLD, "name": "web-comment-1", "anchorUuid": "a1", "cutUuid": "a1", "exact": P_OLD,
             "status": "open", "createdT": t, "lastSeenT": int(time.time())},
            {"tid": "tid-two", "sid": T_TWO, "name": "web-comment-2", "anchorUuid": "a3", "cutUuid": "a3", "exact": P_TWO,
             "status": "open", "createdT": t + 30, "lastSeenT": int(time.time())}]}))
        cls.port, cls.token = _free_port(), "testtok-cmtlayout"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST",
                              PYTHONPATH=FAKE_SDK,   # the parent runs on the fake Agent SDK: every create forks for real
                              ROMP_FAKE_SDK_REPLY=FAKE_REPLY)
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

    _run = None   # the one driver run every test in this process reads: (result, failure), filled by the first test that asks

    def _result(self):
        cls = type(self)
        if cls._run is None:
            cls._run = cls._drive()
        r, why = cls._run
        if why == "skip":
            raise unittest.SkipTest("the driver could not launch a playwright browser here: the served guard needs one")
        if why:
            self.fail(why)
        return r

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "sid": WEB,
                       "p": PASSAGES, "store": cls.store, "dropName": DROP_NAME, "openDropName": OPEN_DROP_NAME,
                       "heldName": HELD_NAME, "heldMsg": HELD_MSG, "gateName": GATE_NAME, "gateMsg": GATE_MSG,
                       "reply": FAKE_REPLY}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        klog = lambda: open(cls.klog).read()[-2000:]
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            def _dec(b):
                return "" if b is None else (b.decode("utf-8", "replace") if isinstance(b, (bytes, bytearray)) else b)
            return None, "driver ran past its 420 s cap:\n%s%s\nkernel:\n%s" % (_dec(e.stdout)[-3000:], _dec(e.stderr)[-3000:], klog())
        if p.returncode == 3:
            return None, "skip"
        if p.returncode != 0:
            return None, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + klog()
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            return None, "driver printed no result:\n" + p.stdout[-3000:]
        r = json.loads(line[len("RESULT:"):])
        if "died" in r:
            return None, r["died"]
        print("RESULT:" + json.dumps(r))   # the whole record, printed once per test process
        return r, None

    def at_thread_default(self, r, rect, what):
        """A box at the thread layout's default: 70% x 60% of the window, its right edge 8 px in from the window's, top 120."""
        vw, vh = r["vw"]
        near = lambda a, b, tol=2: abs(a - b) <= tol
        self.assertIsNotNone(rect, what)
        left, top, w, h = rect
        self.assertTrue(near(w, round(vw * 0.7)) and near(h, round(vh * 0.6)) and near(left + w, vw - 8) and near(top, 120),
                        "%s: at the thread default, not %r (window %r)" % (what, rect, r["vw"]))

    def assert_third_reply_layout(self):
        """Where the third reply's repeated phrases sit, which the tests that select a phrase's second copy rely on. Such a
        scene tells the live range's rule from the first copy's only while one of the two copies (the one it selects, the
        phrase's first) lies inside the passage it is measured against and the other outside; an edit to A3 or P_TWO that
        put both on one side would let the scene pass whichever rule decided."""
        def nth(text, n):
            at = -1
            for _ in range(n + 1):
                at = A3.find(text, at + 1)
                self.assertGreaterEqual(at, 0, "the third reply holds %r at least %d times" % (text, n + 1))
            return at, at + len(text)
        inside = lambda r, p: p[0] <= r[0] and r[1] <= p[1]
        self.assertEqual(A3.count(P_TWO), 1, "the thread's passage is in the third reply once")
        two = nth(P_TWO, 0)
        cache, drains, worker = PASSAGES["repeatInside"], PASSAGES["repeatOutside"], PASSAGES["inflightRepeatInside"]
        self.assertLessEqual(nth(cache, 0)[1], two[0], "the first %r is before the thread's passage" % cache)
        self.assertTrue(inside(nth(cache, 1), two), "the second %r is inside the thread's passage" % cache)
        self.assertTrue(inside(nth(drains, 0), two), "the first %r is inside the thread's passage" % drains)
        self.assertGreaterEqual(nth(drains, 1)[0], two[1], "the second %r is after the thread's passage" % drains)
        send = nth(PASSAGES["inflightRepeat"], 0)
        self.assertGreaterEqual(send[0], two[1], "the send held in flight is on a sentence after the thread's passage")
        self.assertTrue(inside(nth(worker, 0), two), "the first %r is inside the thread's passage" % worker)
        self.assertTrue(inside(nth(worker, 1), send), "the second %r is inside the send's passage" % worker)

    def test_the_send_takes_the_thread_layout_and_a_passage_with_a_thread_opens_it(self):
        r = self._result()
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
        self.assertEqual(((r["inflight"] or {}).get("mode"), (r["inflight"] or {}).get("status")), ("create", "sent"), r["inflight"])
        self.assertEqual(len(on(PASSAGES["inflight"])), 1)
        self.assertEqual(r["errs"], [], r["errs"])

    def test_a_comment_gestures_selection_point_does_not_place_a_box_in_the_thread_layout(self):
        """A Comment gesture's selection point does not place a box in the thread layout: the redirected thread, the
        highlight click, the sent box (its top included), the thread the send brought, and the in-flight box re-opened after
        a maximize made the spot the user's all open at the thread default."""
        r = self._result()
        vw, vh = r["vw"]
        self.at_thread_default(r, (r["overlap"]["opened"] or {}).get("rect"), "the thread a Comment opened instead of a new one")
        self.at_thread_default(r, (r["markClick"] or {}).get("rect"), "the thread its highlight's click opened")
        self.at_thread_default(r, (r["layout"]["sent"] or {}).get("rect"), "the sent box")
        self.at_thread_default(r, (r["layout"]["thread"] or {}).get("rect"), "the thread the send brought")
        # the maximize that arms the in-flight case ran: the box is at the window's cap (the .cmt-pop CSS caps, 94% x 90%,
        # never nearer than 8 px to an edge: comment-pop-size.ts cmtPopCapPx; maximized is within 4 px of it on both axes,
        # isCmtPopMax). The box a missing maximize leaves, the comment box at its content size, is not
        armed = r["inflightArmed"]
        cap_w, cap_h = min(round(vw * 0.94), vw - 16), min(round(vh * 0.9), vh - 16)
        self.assertTrue(armed and abs(armed[2] - cap_w) <= 4 and abs(armed[3] - cap_h) <= 4,
                        "the maximize that arms the in-flight case ran: the box at the cap %r, not %r (window %r)"
                        % ([cap_w, cap_h], armed, r["vw"]))
        self.at_thread_default(r, (r["inflight"] or {}).get("rect"), "the in-flight send's box, re-opened by a second Comment")

    def test_the_thread_a_comment_opens_takes_the_caret_on_every_road(self):
        """The thread a Comment opens has the caret in its reply box, on the key, the menu, the quote chip and the posted run,
        and the words typed next land there. A later rebuild of a box the key's Comment opened that way (a status change on
        a thread with messages) leaves the caret in the chat's message box, where the person moved it. On the key, a
        Comment while that thread's box is already open, the caret outside it, refreshes the box in place and puts the
        caret in it."""
        r = self._result()
        o = r["overlap"]
        self.assertEqual((o["opened"] or {}).get("tid"), "tid-old", o)
        self.assertEqual(o["caret"], "box", "the key: the caret in the thread's reply box: %r" % o)
        self.assertEqual((o["typed"]["box"], o["typed"]["chat"]), (True, False), "the words typed next land in the thread, never the chat's message box: %r" % o)
        for how in ("menu", "chip", "posted"):
            got = r["roads"][how]
            typed = got.get("typed") or {}
            self.assertEqual((got.get("tid"), bool(got.get("offer")), got.get("caret"), typed.get("box"), typed.get("chat")),
                             ("tid-old", True, "box", True, False), "%s: the caret and the typed words in the thread: %r" % (how, got))
        oa = r["openAgain"]
        self.assertNotIn("error", oa, oa)
        self.assertTrue(oa.get("same"), "the precondition: the Comment refreshed the open box in place, not a new one: %r" % oa)
        self.assertEqual((oa.get("tid"), oa.get("caret")), ("tid-old", "box"),
                         "a Comment while the thread's box is already open, the box refreshed in place: the caret in it: %r" % oa)
        rb = r["rebuilt"]
        self.assertEqual((rb["opened"], rb["flipped"], rb["status"]), ("box", True, "merging"), "the box opened with the caret and was rebuilt: %r" % rb)
        self.assertEqual(rb["caret"], "chat", "the rebuild left the caret in the chat's message box, where the person moved it: %r" % rb)

    def test_a_file_that_lands_while_the_sent_view_waits_reaches_the_chat_and_stays(self):
        """A file that lands while the sent view waits goes to the chat's attachment strip, and is still there after the
        thread arrives."""
        r = self._result()
        d = r["dropped"]
        self.assertEqual((d["sent"] or {}).get("status"), "sent", d)
        self.assertEqual(d["during"], {"chat": 1, "pending": 0, "box": False}, "the saved path in the chat's strip, not the read-only box: %r" % d)
        self.assertTrue(d["adopted"], d)
        self.assertEqual(d["after"], {"chat": 1, "pending": 0, "box": False}, "…and still there once the thread arrived: %r" % d)

    def test_a_file_whose_upload_finishes_while_the_comment_box_is_open_and_editable_lands_in_that_box(self):
        """A file dropped on the page whose upload finishes while the comment box is open and editable lands in that box,
        not in the chat's attachment strip."""
        r = self._result()
        d = r["droppedOpen"]
        self.assertNotIn("error", d, d)
        self.assertEqual(d["box"], {"mode": "create", "readOnly": False}, "the comment box was open and editable: %r" % d)
        self.assertEqual(d["landed"], {"chat": 0, "pending": 0, "box": True}, "the saved path in the box, not the chat's strip: %r" % d)

    def test_a_chat_message_held_for_its_upload_on_the_tab_shown_sends_when_the_upload_lands_while_a_sent_comment_waits(self):
        """On the tab shown, a chat message held by "Wait for the upload" sends, with the file, when the upload's saved path
        answers while a sent comment waits for its thread; the path does not go into the comment's read-only box."""
        r = self._result()
        h = r["heldSend"]
        self.assertNotIn("error", h, h)
        self.assertEqual(h["armed"], HELD_MSG, "the message was held on the upload: %r" % h)
        self.assertEqual((h["sent"] or {}).get("status"), "sent", "the comment was in its sent view: %r" % h)
        self.assertEqual(h["waiting"], {"heldDrops": 1, "input": HELD_MSG, "posted": False},
                         "while the comment waited in its sent view, the saved path was still held and the message still waited: %r" % h)
        d = h["during"]
        self.assertEqual((d["box"], d["status"], d["input"], d["posted"], d["postedFile"]), (False, "sent", "", True, True),
                         "the held message sent with the file while the comment waited: %r" % h)
        self.assertTrue(h["adopted"], h)

    def test_an_open_still_uploading_dialog_on_the_tab_shown_closes_and_its_message_sends_when_the_upload_lands_while_a_sent_comment_waits(self):
        """On the tab shown, a chat message whose "still uploading" dialog is still open when the upload's saved path
        answers, while a sent comment waits for its thread: the dialog closes itself, the message sends with the file, and
        the path does not go into the comment's read-only box."""
        r = self._result()
        g = r["gateSend"]
        self.assertNotIn("error", g, g)
        self.assertEqual((g["sent"] or {}).get("status"), "sent", "the comment was in its sent view: %r" % g)
        self.assertEqual(g["waiting"], {"dialog": True, "status": "sent", "heldDrops": 1, "input": GATE_MSG, "posted": False},
                         "while the comment waited in its sent view, the dialog was open, the saved path still held and the message "
                         "unsent: %r" % g)
        d = g["during"]
        self.assertEqual((d["dialog"], d["box"], d["status"], d["input"], d["posted"], d["postedFile"]), (False, False, "sent", "", True, True),
                         "the dialog closed and the message sent with the file while the comment waited: %r" % g)
        self.assertTrue(g["adopted"], g)

    def test_with_one_open_thread_and_no_other_thread_or_send_on_the_third_reply_the_cache_inside_it_and_drains_the_queue_after_it_open_it_on_the_key(self):
        """With its open thread the only thread or send on the third reply: "the cache" selected at its second copy, inside
        the thread's highlight, opens that thread on the key, the menu and the posted run, though its first copy is
        outside; "drains the queue" selected at its second copy, after the highlight, opens that thread on the key too,
        its first copy lying inside it."""
        self.assert_third_reply_layout()
        r = self._result()
        # each of these scenes closes the box before its Comment, so the box is built fresh, and a fresh box's offer of a new
        # thread is what tells a Comment's redirect from a highlight click
        want = ("thread", "tid-two", "open", True)
        for k in ("repeatInside", "repeatInsideMenu", "repeatInsidePosted"):
            got = r[k] or {}
            self.assertEqual((got.get("mode"), got.get("tid"), got.get("status"), bool(got.get("offer"))), want,
                             "%s: selected inside the highlight: %r" % (k, got))
        got = r["repeatOutside"] or {}
        self.assertEqual((got.get("mode"), got.get("tid"), got.get("status"), bool(got.get("offer"))), want,
                         "selected outside, first occurrence inside: %r" % got)

    def test_with_no_send_waiting_for_its_thread_a_phrase_selected_inside_the_only_thread_it_meets_an_open_one_opens_it_over_a_newer_thread_on_its_first_occurrence_on_the_key(self):
        """With no send on the page waiting for its thread, two threads match one selection: the open thread whose highlight
        the phrase was selected inside, the only highlight the selection meets, and a newer one on the phrase's first
        occurrence. On the key, the one it was selected inside opens."""
        self.assert_third_reply_layout()
        r = self._result()
        tie = r["tie"] or {}
        self.assertNotIn("error", tie, tie)
        self.assertEqual(((tie.get("composer") or {}).get("mode"), bool((tie.get("composer") or {}).get("offer"))), ("create", False),
                         "the first occurrence, outside every highlight, opened the comment box: %r" % tie)
        made = [t for t in r["store"]["threads"] if t.get("tid") == tie.get("made")]
        two = [t for t in r["store"]["threads"] if t.get("tid") == "tid-two"]
        self.assertTrue(made and two and made[0]["exact"] == PASSAGES["repeatInside"] and made[0]["anchorUuid"] == "a3"
                        and made[0]["createdT"] > two[0]["createdT"], "a newer thread was made on the phrase: %r %r" % (tie, r["store"]))
        self.assertEqual(tie.get("marks"), {"text": PASSAGES["repeatInside"], "insideTwo": False},
                         "its highlight is drawn on the first occurrence, outside the older thread's: %r" % tie)
        self.assertEqual(tie.get("meets"), {"tids": ["tid-two"], "pending": 0},
                         "the selection meets the older thread's highlight alone, and no send is waiting for its thread: %r" % tie)
        got = tie.get("opened") or {}
        self.assertEqual((got.get("mode"), got.get("tid"), got.get("status"), bool(got.get("offer"))), ("thread", "tid-two", "open", True),
                         "the thread the phrase was selected inside: %r" % got)

    def test_a_repeated_phrase_selected_inside_a_send_waiting_for_its_thread_with_every_other_send_on_the_reply_acked_reopens_that_sends_box_on_the_key(self):
        """On the key, the check for a send still in flight reads where the phrase was selected too: a second Comment on a
        phrase selected inside the passage of a send whose thread has not arrived yet (its highlight still the optimistic
        mark), with every other send on that reply acked, re-opens that send's box, though the phrase's first occurrence
        lies in another thread's passage. The reply's earlier send, the tie scene's, had its ack, its box adopting its
        thread."""
        self.assert_third_reply_layout()
        r = self._result()
        self.assertTrue((r["tie"] or {}).get("made"),
                        "the reply's earlier send, the tie scene's, had its ack before this send: its box adopted its thread: %r"
                        % r["tie"])
        m = r["inflightRepeatMeets"] or {}
        tids = m.get("tids") or []
        self.assertTrue(len(tids) == 1 and tids[0].startswith("pending:"),
                        "the selection meets one highlight, the optimistic mark of a send waiting for its thread: %r" % m)
        got = r["inflightRepeat"] or {}
        self.assertEqual((got.get("mode"), got.get("status"), got.get("quote")), ("create", "sent", PASSAGES["inflightRepeat"]),
                         "the send's own box, re-opened: %r" % got)
        self.assertEqual(len([t for t in r["store"]["threads"] if t["exact"] == PASSAGES["inflightRepeat"]]), 1,
                         "one thread from the one send: %r" % r["store"])

    def test_with_no_send_waiting_for_its_thread_a_selection_from_a_replys_last_words_into_the_next_turn_opens_the_only_thread_it_meets_an_open_one_on_the_key(self):
        """A selection may start in a reply and end in the next turn. Its place in the reply is then cut at the end of the
        reply's prose (selectionAt), and that range meets the thread on the reply's last words. The selected text alone
        cannot: the part of it that the reply's prose holds is two words, too short for the text rule to locate
        (findAnchorRange). The selection meets no highlight in the reply but that open thread's, and no send on the page is
        waiting for its thread."""
        r = self._result()
        self.assertEqual(r["acrossSelected"], "", "the selection ran from the reply's last words into the next turn")
        got = r["across"] or {}
        self.assertEqual((got.get("mode"), got.get("status"), got.get("quote"), bool(got.get("offer"))), ("thread", "open", PASSAGES["inflight"], True),
                         "the thread on the reply's last words: %r" % got)
        self.assertEqual(r["acrossMeets"], {"tids": [got.get("tid")], "pending": 0},
                         "the selection meets one thread's highlight in the reply, and no send is waiting for its thread: %r %r"
                         % (r["acrossMeets"], got))

    def test_with_no_send_waiting_for_its_thread_a_selection_from_a_replys_rail_stamp_into_a_threads_passage_opens_the_only_thread_it_meets_an_open_one_on_the_key(self):
        """A selection may start on a reply's rail stamp, the time label drawn in the turn before its prose. Its range in the
        reply then begins where the prose begins (selectionAt moves a start before the prose to the prose's first
        character), and that range meets the thread whose first words it runs into. The selected text alone cannot: it
        begins with the stamp's words, which the prose does not hold, so the text rule finds nothing. The selection meets
        no highlight in the reply but that open thread's, and no send on the page is waiting for its thread."""
        r = self._result()
        g = r["gutter"] or {}
        self.assertTrue(g.get("marker") and g.get("startsInStamp"), "the stamp shows a label and the selection starts in its text: %r" % g)
        self.assertTrue((g.get("selected") or "").endswith(PASSAGES["gutterTo"]), "…and ends in the thread's first words: %r" % g)
        got = r["gutterPop"] or {}
        self.assertEqual((got.get("mode"), got.get("tid"), got.get("status"), bool(got.get("offer"))), ("thread", "tid-two", "open", True),
                         "the thread the selection runs into: %r" % got)
        self.assertEqual(r["gutterMeets"], {"tids": ["tid-two"], "pending": 0},
                         "the selection meets one thread's highlight in the reply, and no send is waiting for its thread: %r"
                         % r["gutterMeets"])


if __name__ == "__main__":
    unittest.main()
