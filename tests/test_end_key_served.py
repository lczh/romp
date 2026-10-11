#!/usr/bin/env python3
"""End and Ctrl+End in the chat page, executed in a real browser on test_file_tabs_served.py's lab (its kernel, sessions and
driver helpers), with docs/guide.md made long and, added to web's transcript, a reply with an audio clip, a menu, a listbox and a task
list, then a Bash call with a long output and a long reply, then a reply with another audio clip. One driver run, on the page's own keys:
  1. a click on a line of the reply, then End, then Ctrl+End, each from the top of the transcript;
  2. Shift+End from a word selected on a line, then Alt+End and Meta+End from a caret on one;
  3. End with the selection's anchor in an element of the reply with role="menu", then in one with role="listbox";
  4. a press on the strip's empty part, the selection's anchor set on a line of the reply, then End;
  5. End with focus on a checkbox of a question card (the kernel's askLive frame, dispatched by the lab), the anchor on a line of the
     reply;
  6. the Bash call's output opened and a click on its text, then End with that box at its top, and End with it at its end;
  7. End with focus on the link in the first reply's menu, then on the one in its listbox;
  8. a press on the first reply's task-list checkbox, focus then on the page body and no selection, then End;
  9. the first reply's audio with its clip loaded, then End: focused, then with focus on its time scrubber, on its volume slider, and
     on its volume slider with the clip ended;
 10. the transcript at its bottom, End from the last reply's audio play control, reached by Tab and by a click;
 11. a right-click on a word selected on a line of the reply, then End in the selection menu it opens; then End in the comment box its
     Comment row opens, with text typed and the caret at its start;
 12. End after a click in the file viewer opened from the transcript; End with focus on the viewer's Copy path button and the anchor
     on a line of the reply; End after a click in the file tab the viewer pops out to;
 13. the file tab still up, the selection's anchor set on a line of the hidden transcript, then End.
A listener the lab adds on the window after the page's own records whether the page took each End (defaultPrevented). Skips
LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo world)."""
import json
import math
import os
import re
import struct
import subprocess
import sys
import time
import unittest
import wave
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_file_tabs_served as F   # noqa: E402  the lab: its boot, its sessions and its driver helpers

LONG_GUIDE = "# Notes API guide\n\n" + "".join("- rule %d of the notes-api guide, one per line\n" % i for i in range(3000))
OUT = "".join("out line %d: the notes table row %d was read and checked\n" % (i, i) for i in range(1, 201))
REPLY = ("Here is the plan for the notes api, step by step.\n\n"
         '<div role="menu">The note menu: open, rename or delete a note.</div>\n\n'
         '<div role="listbox">The note list: notes one, two and three.</div>\n\n'
         + "".join("- Plan line %d: the handler reads the note by id and returns it.\n" % i for i in range(1, 121)))
EARLY = ("Clips and lists for the notes api.\n\n"
         '<a href="https://example.test/pre-clip">pre clip</a>\n\n<audio id="lab-clip" controls src="https://media.test/clip.wav"></audio>\n\n'
         '<div role="menu">Note actions: <a href="https://example.test/open">open the note</a></div>\n\n'
         '<div role="listbox">Note picks: <a href="https://example.test/pick">pick the note</a></div>\n\n'
         "- [ ] an open task for the notes api\n")
LAST = ('One more clip of the notes api.\n\n<a href="https://example.test/pre-last">pre last</a>\n\n'
        '<audio id="lab-last-clip" controls src="https://media.test/clip.wav"></audio>')
ASK = {"kind": "multi", "header": "Tables", "question": "Which tables should the notes-api keep?",
       "options": [{"n": 1, "label": "notes", "checked": False, "selected": True},
                   {"n": 2, "label": "tags", "checked": False, "selected": False}],
       "cursor": 1, "cursorFound": True, "multiSelect": True, "sig": "lab-end-1"}

RUN = r"""
async function run() {
// the page, the lab owning web's question: the kernel's own askLive frames for it are held back, and the answers the card
// sends are swallowed at the socket
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
await ctx.addInitScript(() => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: "classic" })); } catch (e) {} });
await ctx.addInitScript((sid) => {
  window.addEventListener("message", (e) => { const m = e.data; if (m && (m.type === "askLive" || m.type === "askLiveClear") && m.id === sid && !m.__lab) e.stopImmediatePropagation(); }, true);
  const KEEP = ["addCustomAsk", "askText", "answerAsk", "toggleAsk", "submitAsk", "cancelAsk"];
  const send = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { const m = JSON.parse(d); if (m && KEEP.includes(m.type)) return; } catch (e) {} return send.call(this, d); };
}, cfg.web);
// the target of the last press on the page
await ctx.addInitScript(() => { window.__press = null; window.addEventListener("pointerdown", (e) => { window.__press = e.target; }, true); });
// the clip the replies' audio plays, served at media.test with byte ranges
const clip = fs.readFileSync(cfg.clip);
await ctx.route("https://media.test/**", (route) => {
  const range = route.request().headers()["range"], m = range && /bytes=(\d+)-(\d*)/.exec(range);
  if (!m) return route.fulfill({ status: 200, body: clip, headers: { "content-type": "audio/wav", "accept-ranges": "bytes" } });
  const a = +m[1], b = m[2] ? Math.min(+m[2], clip.length - 1) : clip.length - 1;
  return route.fulfill({ status: 206, body: clip.subarray(a, b + 1), headers: { "content-type": "audio/wav", "accept-ranges": "bytes", "content-range": "bytes " + a + "-" + b + "/" + clip.length } });
});
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(String(e && e.stack || e).slice(0, 500)));
await page.goto(cfg.chat);
await wait(page, (n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-file)").length >= n, cfg.count, "the strip never showed every session");
await page.click('#tabs .tab[data-id="' + cfg.web + '"]');
await wait(page, () => Array.from(document.querySelectorAll('#content [role="menu"], #content [role="listbox"]')).filter((e) => e.offsetParent !== null).length === 4
  && Array.from(document.querySelectorAll("#content li")).some((e) => e.offsetParent !== null && /Plan line 120:/.test(e.textContent)), null, "web's reply never showed in full");
// after the page's own listeners: whether each End reached the window taken (defaultPrevented); and the transcript's scroll events
await page.evaluate(() => { window.__ends = []; window.addEventListener("keydown", (e) => { if (e.key === "End") window.__ends.push(e.defaultPrevented); });
  window.__scrolls = 0; document.getElementById("content").addEventListener("scroll", () => { window.__scrolls++; }, { passive: true });
  window.__vis = (sel) => Array.from(document.querySelectorAll(sel)).find((e) => e.offsetParent !== null) || null; });
const settle = async () => { await page.waitForTimeout(600); await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))))); };
// where focus, the selection's anchor and the last press sit, the transcript's scroll and the file scrollers' and the tool fold's
const where = () => page.evaluate(() => {
  const c = document.getElementById("content"), s = document.getSelection(), a = s && s.anchorNode, ae = a && (a.nodeType === 1 ? a : a.parentElement), f = document.activeElement;
  const body = (sel) => { const b = document.querySelector(sel); return b ? { st: Math.round(b.scrollTop), max: b.scrollHeight - b.clientHeight } : null; };
  const inWhat = (e) => !e ? null : e.closest("#romp-fileview") ? "fileview" : e.closest("#filetab-host") ? "filetab" : c.contains(e) ? "content" : "other";
  const fold = Array.from(document.querySelectorAll("#content .tool-io-fold")).find((e) => e.offsetParent !== null);
  const p = window.__press, pe = p && (p.nodeType === 1 ? p : p.parentElement);
  const rows = Array.from(c.querySelectorAll(".turn[data-uuid]")).filter((e) => e.offsetParent !== null), last = rows[rows.length - 1], lb = last && last.getBoundingClientRect(), cr = c.getBoundingClientRect();
  return { bodyFocused: f === document.body, focusInContent: !!f && f !== document.body && c.contains(f), focusTag: f ? f.tagName + (f.type ? ":" + f.type : "") : null,
           focusRole: f && f !== document.body && f.closest("[role]") ? f.closest("[role]").getAttribute("role") : null,
           focusIn: f && f !== document.body ? inWhat(f) : null, pressIn: inWhat(pe), pressTag: pe ? pe.tagName + (pe.type ? ":" + pe.type : "") : null,
           lastOnScreen: !!lb && lb.bottom > cr.top + 1 && lb.top < cr.bottom - 1, chipShown: !!document.getElementById("jump-bottom") && !document.getElementById("jump-bottom").hidden,
           anchorIn: inWhat(ae),
           anchorRole: ae && ae.closest("[role]") ? ae.closest("[role]").getAttribute("role") : null, collapsed: !s || s.isCollapsed, selected: s ? s.toString().length : 0,
           fileMode: document.body.classList.contains("filetab-mode"), contentShown: c.clientHeight > 0,
           top: Math.round(c.scrollTop), dist: Math.round(c.scrollHeight - c.scrollTop - c.clientHeight), ch: c.clientHeight,
           viewer: body("#romp-fileview .fileview-body"), tab: body("#filetab-host .fileview-body"),
           fold: fold ? { st: Math.round(fold.scrollTop), max: fold.scrollHeight - fold.clientHeight } : null, anchorInFold: !!fold && !!ae && fold.contains(ae) }; });
const press = async (keys) => { const n = await page.evaluate(() => window.__ends.length); await page.keyboard.press(keys); await settle();
  return { taken: await page.evaluate((k) => window.__ends.slice(k), n), after: await where() }; };
const toTop = () => page.evaluate(() => { document.getElementById("content").scrollTop = 0; });
const line = (n) => page.locator("#content li:visible", { hasText: "Plan line " + n + ":" });
// a click that is to leave a caret starts from no selection (a click inside a selection can leave none)
const unselect = () => page.evaluate(() => document.getSelection().removeAllRanges());
// the selection's anchor set on a line of web's reply
const anchorOn = (n) => page.evaluate(([sid, k]) => { const li = Array.from(document.querySelectorAll('#content .thread[data-session="' + sid + '"] li')).find((e) => e.textContent.startsWith("Plan line " + k + ":"));
  document.getSelection().collapse(li.firstChild, 0); }, [cfg.web, n]);

// 1. the transcript: End, then Ctrl+End, after a click on a line
await line(3).click();
await toTop(); await settle();
out.end = Object.assign({ before: await where() }, await press("End"));
await toTop(); await settle();
out.ctrlEnd = Object.assign({ before: await where() }, await press("Control+End"));

// 2. the modifiers: Shift+End from a word selected on a line, then Alt+End and Meta+End from a caret on one
await toTop(); await settle();
await line(3).dblclick({ position: { x: 20, y: 4 } });
out.shiftEnd = Object.assign({ before: await where() }, await press("Shift+End"));
for (const k of ["Alt", "Meta"]) {
  await toTop(); await settle();
  await unselect(); await line(3).click({ position: { x: 20, y: 4 } });
  out[k.toLowerCase() + "End"] = Object.assign({ before: await where() }, await press(k + "+End"));
}

// 3. the anchor in the reply's menu, then in its listbox
for (const role of ["menu", "listbox"]) {
  await toTop(); await settle();
  await unselect(); await page.locator('#content [role="' + role + '"]:visible', { hasText: role === "menu" ? "The note menu" : "The note list" }).click();
  out[role] = Object.assign({ before: await where() }, await press("End"));
}

// 4. a press on the strip's empty part (outside the transcript; it takes no focus), then the selection's anchor set on a line of the
//    reply
await toTop(); await settle();
await unselect();
const spot = await page.evaluate(() => { const b = document.getElementById("tabbar").getBoundingClientRect();
  const right = Math.max(b.left, ...Array.from(document.querySelectorAll("#tabs > *")).map((e) => e.getBoundingClientRect()).filter((r) => r.top < b.top + 30).map((r) => r.right));
  return { x: Math.round((right + b.right) / 2), y: Math.round(b.top + Math.min(14, b.height / 2)) }; });
await page.mouse.click(spot.x, spot.y);
await anchorOn(3); await settle();
out.pressOutside = Object.assign({ before: await where() }, await press("End"));

// 5. focus on a question card's checkbox, the selection's anchor on a line of the reply
await page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: { ...m, __lab: true } })); }, { type: "askLive", id: cfg.web, ask: cfg.ask });
await wait(page, () => !!document.querySelector("#live-ask .ask-check input"), null, "the question card never showed its checkboxes");
await toTop(); await settle();
await unselect(); await line(3).click({ position: { x: 20, y: 4 } });
await page.focus("#live-ask .ask-check input");
await toTop(); await settle();
out.checkbox = Object.assign({ before: await where() }, await press("End"));
await page.evaluate((m) => { window.dispatchEvent(new MessageEvent("message", { data: { ...m, __lab: true } })); }, { type: "askLiveClear", id: cfg.web });

// 6. the Bash call's output opened (.tool-io-fold, a box that scrolls on its own) and a click on its text: End with the box at its top,
//    then End with it at its end
await page.locator("#content .tool-fold-toggle", { hasText: /^200 lines$/ }).click();
await wait(page, () => { const f = Array.from(document.querySelectorAll("#content .tool-io-fold")).find((e) => e.offsetParent !== null); return !!f && f.scrollHeight > f.clientHeight + 100; }, null, "the tool output never opened as a scroll box");
const centerFold = (end) => page.evaluate((e) => { const f = Array.from(document.querySelectorAll("#content .tool-io-fold")).find((x) => x.offsetParent !== null);
  f.scrollIntoView({ block: "center" }); f.scrollTop = e ? f.scrollHeight : 0; }, end);
await centerFold(false); await settle();
await unselect();
const fb = await page.locator("#content .tool-io-fold:visible").boundingBox();
await page.mouse.click(fb.x + 120, fb.y + fb.height / 2); await settle();
out.foldTop = Object.assign({ before: await where() }, await press("End"));
await centerFold(true); await settle();
out.foldEnd = Object.assign({ before: await where() }, await press("End"));

// 7. focus on the link in the first reply's menu, then on the one in its listbox, each from the top of the transcript
for (const role of ["menu", "listbox"]) {
  await toTop(); await settle();
  await unselect(); await page.evaluate((r) => window.__vis('#content [role="' + r + '"] a').focus({ preventScroll: true }), role); await settle();
  out[role + "Link"] = Object.assign({ before: await where() }, await press("End"));
}

// 8. a press on the first reply's task-list checkbox (it takes no focus), brought to the middle of the pane; then focus returned to the
//    page body and the selection emptied
await toTop(); await settle();
await unselect(); await page.evaluate(() => window.__vis("#content .md li input").scrollIntoView({ block: "center" })); await settle();
const tb = await page.evaluate(() => { const b = window.__vis("#content .md li input").getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
await page.mouse.click(tb.x, tb.y); await settle();
await page.evaluate(() => { const f = document.activeElement; if (f && f !== document.body && f.blur) f.blur(); document.getSelection().removeAllRanges(); });
out.taskPress = Object.assign({ before: await where() }, await press("End"));

// 9. the first reply's audio, brought to the middle of the pane, its clip loaded at half volume and at 0 s: End with the audio focused;
//    then with focus on its time scrubber, then on its volume slider, each reached by Tab from the link before it; then on its volume
//    slider again with the clip ended. Each reads the clip, the focused node in the accessibility tree and the transcript's scroll events
const clipState = (sel) => page.evaluate((s) => { const m = window.__vis(s), c = document.getElementById("content");
  return { t: m.currentTime, d: m.duration, ended: m.ended, volume: m.volume, paused: m.paused, readyState: m.readyState, focused: document.activeElement === m, inContent: c.contains(m), scrolls: window.__scrolls }; }, sel);
const stageClip = async (sel, ended) => {
  await page.evaluate((s) => window.__vis(s).scrollIntoView({ block: "center" }), sel); await settle();
  await wait(page, (s) => window.__vis(s).readyState >= 1, sel, "the clip never loaded");
  await page.evaluate(([s, e]) => { const m = window.__vis(s); m.volume = 0.5; m.currentTime = e ? m.duration : 0; }, [sel, ended]);
  if (ended) await wait(page, (s) => window.__vis(s).ended, sel, "the clip never ended");
  await settle();
};
const tabFrom = async (href, n) => { await page.evaluate((h) => window.__vis('#content a[href="' + h + '"]').focus({ preventScroll: true }), href); for (let i = 0; i < n; i++) await page.keyboard.press("Tab"); await settle(); };
const axFocused = async () => { const s = await ctx.newCDPSession(page);
  try { return (await s.send("Accessibility.getFullAXTree")).nodes.filter((n) => n.role && n.role.value !== "RootWebArea" && (n.properties || []).some((q) => q.name === "focused" && q.value && q.value.value === true)).map((n) => (n.role && n.role.value) + ":" + (n.name && n.name.value)); }
  finally { await s.detach().catch(() => {}); } };
const clipPress = async (sel) => { await page.evaluate(() => { window.__scrolls = 0; }); const before = Object.assign(await where(), { clip: await clipState(sel), ax: await axFocused() });
  const r = await press("End"); return Object.assign({ before, clip: await clipState(sel) }, r); };
const CLIP = '[id="user-content-lab-clip"]', PRE = "https://example.test/pre-clip";
await unselect(); await stageClip(CLIP, false);
await page.evaluate((s) => window.__vis(s).focus({ preventScroll: true }), CLIP); await settle();
out.clip = await clipPress(CLIP);
await stageClip(CLIP, false); await tabFrom(PRE, 3);
out.clipScrubber = await clipPress(CLIP);
await stageClip(CLIP, false); await tabFrom(PRE, 4);
out.clipVolume = await clipPress(CLIP);
await stageClip(CLIP, true); await tabFrom(PRE, 4);
out.clipVolumeEnded = await clipPress(CLIP);

// 10. the last reply's audio, the transcript at its bottom: End with focus on the play control reached by Tab from the link before it;
//     then after a click on the play control, which starts the clip
const LASTCLIP = '[id="user-content-lab-last-clip"]';
await unselect(); await page.evaluate(() => { document.getElementById("content").scrollTop = 1e9; }); await settle();
await wait(page, (s) => window.__vis(s).readyState >= 1, LASTCLIP, "the last clip never loaded");
await tabFrom("https://example.test/pre-last", 2);
out.lastTab = await clipPress(LASTCLIP);
const lc = await page.evaluate((s) => { const b = window.__vis(s).getBoundingClientRect(); return { x: b.left, y: b.top }; }, LASTCLIP);
await page.mouse.click(lc.x + 20, lc.y + 27); await settle();
out.lastClick = await clipPress(LASTCLIP);
await page.evaluate((s) => window.__vis(s).pause(), LASTCLIP);

// 11. a word selected on a line of the reply, brought to the middle of the pane, and a right-click on the selection: End in the
//     selection menu it opens; then its Comment row, text typed in the comment box and the caret put at its start, then End. The word is
//     selected again and the right-click repeated, up to three times, until the menu opens (out.selMenuTries)
await toTop(); await settle();
await page.evaluate(() => Array.from(document.querySelectorAll("#content li")).find((e) => e.offsetParent !== null && e.textContent.startsWith("Plan line 5:")).scrollIntoView({ block: "center" }));
await settle();
for (out.selMenuTries = 1; out.selMenuTries <= 3; out.selMenuTries++) {
  await unselect(); await line(5).dblclick({ position: { x: 20, y: 4 } }); await settle();
  const sr = await page.evaluate(() => { const s = document.getSelection(); if (!s || !s.rangeCount || !s.toString().trim()) return null; const b = s.getRangeAt(0).getBoundingClientRect(); return { x: b.left + b.width / 2, y: b.top + b.height / 2 }; });
  if (!sr) continue;
  await page.mouse.click(sr.x, sr.y, { button: "right" });
  if (await page.waitForFunction(() => !!document.querySelector(".ctx-menu"), null, { timeout: 5000 }).then(() => true, () => false)) break;
}
await wait(page, () => !!document.querySelector(".ctx-menu"), null, "the selection menu never opened");
const menuState = () => page.evaluate(() => { const m = document.querySelector(".ctx-menu"), f = document.activeElement, rows = m ? Array.from(m.querySelectorAll('[role="menuitem"]')) : [];
  return { open: !!m, onBody: !!m && m.parentElement === document.body, focusInMenu: !!m && m.contains(f), focusRow: rows.indexOf(f), rows: rows.map((r) => r.textContent.trim()) }; });
out.selMenu = { before: Object.assign(await where(), { menu: await menuState() }) };
Object.assign(out.selMenu, await press("End"));
out.selMenu.menu = await menuState();
await page.locator('.ctx-menu [role="menuitem"]', { hasText: "Comment" }).click();
await wait(page, () => !!document.querySelector("#cmt-pop .cmt-input"), null, "the comment box never opened");
await page.focus("#cmt-pop .cmt-input"); await page.keyboard.type("a note on this line"); await page.keyboard.press("Home"); await settle();
const boxState = () => page.evaluate(() => { const b = document.querySelector("#cmt-pop .cmt-input"), p = document.getElementById("cmt-pop");
  return { focused: document.activeElement === b, caret: b ? b.selectionStart : null, len: b ? b.value.length : null, onBody: !!p && p.parentElement === document.body, inContent: !!p && document.getElementById("content").contains(p) }; });
out.commentBox = { before: Object.assign(await where(), { box: await boxState() }) };
Object.assign(out.commentBox, await press("End"));
out.commentBox.box = await boxState();
await page.keyboard.press("Escape"); await page.keyboard.press("Escape"); await settle();

// 12. the file viewer opened from the transcript: End after a click in it; then, the selection's anchor set on a line of the reply,
//     End with focus on the viewer's Copy path button; then the file tab it pops out to
await openLink(page, cfg.web, "docs/guide.md");
await wait(page, () => { const b = document.querySelector("#romp-fileview .fileview-body"); return !!b && b.scrollHeight - b.clientHeight > 10000; }, null, "the viewer never showed the long guide");
await page.locator("#romp-fileview .fileview-body").click();
out.viewer = Object.assign({ before: await where() }, await press("End"));
await anchorOn(3); await page.focus('#romp-fileview button[aria-label="Copy path"]'); await settle();
out.viewerButton = Object.assign({ before: await where() }, await press("End"));
await popOut(page);
await wait(page, () => { const b = document.querySelector("#filetab-host .fileview-body"); return !!b && b.scrollHeight - b.clientHeight > 10000; }, null, "the file tab never showed the long guide");
await page.evaluate(() => { document.querySelector("#filetab-host .fileview-body").scrollTop = 0; });
await page.locator("#filetab-host .fileview-body").click();
out.tab = Object.assign({ before: await where() }, await press("End"));

// 13. the file tab still up, the selection's anchor set on a line of the hidden transcript, then End
await page.evaluate((sid) => { const li = Array.from(document.querySelectorAll('#content .thread[data-session="' + sid + '"] li')).find((e) => /Plan line 3:/.test(e.textContent));
  document.getSelection().collapse(li.firstChild, 0); }, cfg.web);
await settle();
out.hidden = Object.assign({ before: await where() }, await press("End"));
}
await run().then(() => finish(null), (e) => finish("an action failed: " + String(e && e.message || e).split("\n")[0]));
"""
DRIVER = F.DRIVER[:F.DRIVER.index("async function run() {")] + RUN


class EndKeyServed(unittest.TestCase):
    maxDiff = None
    result = None

    # the lab's kernel and sessions are test_file_tabs_served.py's (its boot, its teardown)
    _boot = classmethod(F.FileTabsServed._boot.__func__)
    tearDownClass = classmethod(F.FileTabsServed.tearDownClass.__func__)

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
            cwd = os.path.join(cls.lab, "notes-api")
            Path(cwd, "docs", "guide.md").write_text(LONG_GUIDE)
            proj = os.path.join(cls.lab, "claude", "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
            sid, t = F.SIDS["web"], int(time.time()) - 600
            recs = [{"type": "user", "timestamp": F.iso(t - 4), "uuid": "u1c", "parentUuid": "a1", "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "can you share the clips and lists for the notes api?"}},
                    {"type": "assistant", "timestamp": F.iso(t - 2), "uuid": "a1c", "parentUuid": "u1c", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": EARLY}]}},
                    {"type": "user", "timestamp": F.iso(t), "uuid": "u2", "parentUuid": "a1c", "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "what is the plan for the notes api?"}},
                    {"type": "assistant", "timestamp": F.iso(t + 2), "uuid": "a2", "parentUuid": "u2", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                                 "content": [{"type": "tool_use", "id": "tu9", "name": "Bash", "input": {"command": "python3 rows.py"}}]}},
                    {"type": "user", "timestamp": F.iso(t + 3), "uuid": "u3", "parentUuid": "a2", "sessionId": sid,
                     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu9", "content": OUT}]}},
                    {"type": "assistant", "timestamp": F.iso(t + 5), "uuid": "a3", "parentUuid": "u3", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": REPLY}]}},
                    {"type": "user", "timestamp": F.iso(t + 7), "uuid": "u4", "parentUuid": "a3", "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "and the last clip?"}},
                    {"type": "assistant", "timestamp": F.iso(t + 9), "uuid": "a4", "parentUuid": "u4", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": LAST}]}}]
            with open(os.path.join(proj, sid + ".jsonl"), "a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in recs))
            cls.clip = os.path.join(cls.lab, "clip.wav")   # three seconds of a tone
            with wave.open(cls.clip, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 8000))) for i in range(3 * 8000)))
        except BaseException:
            cls.tearDownClass()
            raise

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
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(F.NAMES), "out": out,
                       "web": F.SIDS["web"], "ask": ASK, "clip": cls.clip}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(F.EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served lab needs one")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        result = json.loads(Path(out).read_text())
        print("END-KEY:", json.dumps(result), file=sys.stderr)
        if result.get("died"):
            raise AssertionError("driver stopped: %s\nso far: %s\nkernel:\n%s" % (result["died"], json.dumps(result)[-3000:], open(cls.klog).read()[-1500:]))
        return result

    def _left_to_the_browser(self, step):
        self.assertEqual(step["taken"], [False], "the page left the key to the browser: %r" % step)

    def test_end_and_ctrl_end_after_a_click_in_the_transcript_are_taken_by_the_page_and_go_to_the_bottom(self):
        r = self._run()
        for k in ("end", "ctrlEnd"):
            b = r[k]["before"]
            self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["top"] == 0 and b["dist"] > b["ch"],
                            "%s: focus on the page body, the anchor in the transcript, more than a screen from the bottom: %r" % (k, b))
        self.assertEqual({k: (r[k]["taken"], r[k]["after"]["dist"] <= 2) for k in ("end", "ctrlEnd")}, {"end": ([True], True), "ctrlEnd": ([True], True)},
                         "the page took each key, and the transcript is at the bottom: %r" % {k: r[k] for k in ("end", "ctrlEnd")})

    def test_end_with_shift_alt_or_meta_held_is_left_to_the_browser(self):
        r = self._run()
        b = r["shiftEnd"]["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["selected"] > 0, "shiftEnd: a word selected in the transcript: %r" % b)
        for k in ("altEnd", "metaEnd"):
            b = r[k]["before"]
            self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["collapsed"], "%s: a caret in the transcript: %r" % (k, b))
        keys = ("shiftEnd", "altEnd", "metaEnd")
        self.assertEqual({k: r[k]["taken"] for k in keys}, {k: [False] for k in keys}, "the page left each key to the browser: %r" % {k: r[k] for k in keys})
        self.assertGreater(r["shiftEnd"]["after"]["selected"], r["shiftEnd"]["before"]["selected"], "Shift+End extended the selection: %r" % r["shiftEnd"])

    def test_end_with_the_anchor_in_a_menu_or_a_listbox_in_the_transcript_is_taken_by_the_page_and_goes_to_the_bottom(self):
        r = self._run()
        for role in ("menu", "listbox"):
            b = r[role]["before"]
            self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["anchorRole"] == role and b["dist"] > b["ch"],
                            "%s: the anchor in it, in the transcript, more than a screen from the bottom: %r" % (role, b))
        self.assertEqual({k: (r[k]["taken"], r[k]["after"]["dist"] <= 2) for k in ("menu", "listbox")}, {"menu": ([True], True), "listbox": ([True], True)},
                         "the page took each End, and the transcript is at the bottom: %r" % {k: r[k] for k in ("menu", "listbox")})

    def test_end_with_focus_on_a_link_in_a_menu_or_a_listbox_in_the_transcript_is_taken_by_the_page_and_goes_to_the_bottom(self):
        r = self._run()
        for role in ("menu", "listbox"):
            b = r[role + "Link"]["before"]
            self.assertTrue(b["focusInContent"] and b["focusTag"] == "A" and b["focusRole"] == role and b["dist"] > b["ch"],
                            "%s: focus on a link in it, in the transcript, more than a screen from the bottom: %r" % (role, b))
        self.assertEqual({k: (r[k + "Link"]["taken"], r[k + "Link"]["after"]["dist"] <= 2) for k in ("menu", "listbox")}, {"menu": ([True], True), "listbox": ([True], True)},
                         "the page took each End, and the transcript is at the bottom: %r" % {k: r[k + "Link"] for k in ("menu", "listbox")})

    def test_end_after_a_press_on_a_task_checkbox_in_the_transcript_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["taskPress"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] is None and b["pressIn"] == "content" and b["pressTag"] == "INPUT:checkbox" and b["dist"] > b["ch"],
                        "focus on the page body, no anchor, the last press on a task-list checkbox in the transcript, more than a screen from the bottom: %r" % b)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_with_the_anchor_in_the_transcript_and_the_last_press_outside_it_goes_to_the_bottom(self):
        s = self._run()["pressOutside"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "content" and b["anchorRole"] is None and b["pressIn"] == "other" and b["dist"] > b["ch"],
                        "focus on the page body, the anchor on a line of the transcript, the last press outside it, more than a screen from the bottom: %r" % b)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_on_a_checkbox_in_the_transcript_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["checkbox"]
        b = s["before"]
        self.assertTrue(b["focusInContent"] and b["focusTag"] == "INPUT:checkbox" and b["dist"] > b["ch"],
                        "focus on the question card's checkbox, more than a screen from the bottom: %r" % b)
        self.assertTrue(b["anchorIn"] == "content" and b["anchorRole"] is None, "…the anchor on a line of the transcript: %r" % b)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def _clip_premise(self, s, ax):
        c, b = s["before"]["clip"], s["before"]
        self.assertTrue(c["focused"] and c["inContent"] and c["readyState"] >= 1 and c["d"] == 3 and b["ax"] == ax,
                        "focus on a reply's audio in the transcript with its clip loaded, the focused node %r: %r" % (ax, b))

    def test_end_on_a_focused_audio_with_its_clip_loaded_moves_the_playhead_and_is_left_to_the_browser(self):
        s = self._run()["clip"]
        self._clip_premise(s, ["Audio:"])
        self.assertTrue(s["before"]["clip"]["t"] == 0 and not s["before"]["clip"]["ended"] and s["before"]["dist"] > s["before"]["ch"],
                        "…at 0 s, not ended, more than a screen from the bottom: %r" % s["before"])
        self._left_to_the_browser(s)
        self.assertEqual((s["clip"]["t"], s["after"]["dist"] > s["after"]["ch"]), (3, True), "the playhead went to the end and the transcript stayed: %r" % s)

    def test_end_on_an_audio_time_scrubber_moves_the_playhead_and_does_not_reach_the_page(self):
        s = self._run()["clipScrubber"]
        self._clip_premise(s, ["slider:audio time scrubber"])
        self.assertEqual(s["before"]["clip"]["t"], 0, "…at 0 s: %r" % s["before"])
        self.assertEqual(s["taken"], [], "End never reached the page's window: %r" % s)
        self.assertEqual((s["clip"]["t"], s["clip"]["scrolls"], s["after"]["top"]), (3, 0, s["before"]["top"]),
                         "the playhead went to the end; the transcript did not scroll: %r" % s)

    def test_end_on_an_audio_volume_slider_raises_the_volume_and_does_not_reach_the_page(self):
        r = self._run()
        for k, ended in (("clipVolume", False), ("clipVolumeEnded", True)):
            s = r[k]
            self._clip_premise(s, ["slider:volume"])
            self.assertEqual((s["before"]["clip"]["volume"], s["before"]["clip"]["ended"]), (0.5, ended), "%s: at half volume, ended %s: %r" % (k, ended, s["before"]))
            self.assertEqual(s["taken"], [], "%s: End never reached the page's window: %r" % (k, s))
            self.assertEqual((s["clip"]["volume"], s["clip"]["scrolls"], s["after"]["top"]), (1, 0, s["before"]["top"]),
                             "%s: the volume went to full; the transcript did not scroll: %r" % (k, s))

    def test_end_on_an_audio_play_control_at_the_bottom_of_the_transcript_leaves_it_there(self):
        r = self._run()
        for k, ax in (("lastTab", ["button:play"]), ("lastClick", ["button:pause"])):
            s, b = r[k], r[k]["before"]
            self.assertTrue(b["clip"]["focused"] and b["clip"]["inContent"] and b["ax"] == ax and b["dist"] <= 2 and b["lastOnScreen"],
                            "%s: focus on the play control of the last reply's audio, the focused node %r, the transcript at its bottom: %r" % (k, ax, b))
            a = s["after"]
            self.assertTrue(a["dist"] <= 2 and a["top"] >= b["top"] and a["lastOnScreen"], "%s: after End the transcript is still at its bottom: %r" % (k, s))

    def test_end_in_the_selection_menu_opened_in_the_transcript_moves_to_its_last_row(self):
        s = self._run()["selMenu"]
        b, m0, m1 = s["before"], s["before"]["menu"], s["menu"]
        self.assertTrue(m0["open"] and m0["onBody"] and m0["focusInMenu"] and b["anchorIn"] == "content" and len(m0["rows"]) > 1,
                        "the selection menu open on the page body with focus in it, the anchor in the transcript: %r" % b)
        self.assertEqual(s["taken"], [], "End never reached the page's window: %r" % s)
        self.assertEqual((m1["focusRow"], s["after"]["top"]), (len(m0["rows"]) - 1, b["top"]), "focus went to the menu's last row; the transcript stayed: %r" % s)

    def test_end_in_a_comment_box_on_a_selection_in_the_transcript_moves_its_caret(self):
        s = self._run()["commentBox"]
        x0, x1 = s["before"]["box"], s["box"]
        self.assertTrue(x0["focused"] and x0["onBody"] and not x0["inContent"] and x0["caret"] == 0 and x0["len"] > 0,
                        "focus in the comment box on the page body, its caret at the start of its text: %r" % s["before"])
        self._left_to_the_browser(s)
        self.assertEqual(x1["caret"], x0["len"], "the caret went to the end of the text: %r" % s)

    def test_end_in_an_opened_tool_output_box_scrolls_the_box(self):
        s = self._run()["foldTop"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorInFold"] and b["fold"]["max"] > 0 and b["fold"]["st"] == 0,
                        "focus on the page body, the anchor in the tool output box, the box at its top: %r" % b)
        self._left_to_the_browser(s)
        a = s["after"]
        self.assertTrue(a["fold"]["st"] >= a["fold"]["max"] - 2 and abs(a["top"] - b["top"]) <= 1, "the box scrolled to its end, the transcript stayed: %r" % s)

    def test_end_in_a_tool_output_box_at_its_end_is_taken_by_the_page_and_goes_to_the_bottom(self):
        s = self._run()["foldEnd"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorInFold"] and b["fold"]["max"] > 0 and b["fold"]["st"] >= b["fold"]["max"] - 2 and b["dist"] > b["ch"],
                        "focus on the page body, the anchor in the tool output box, the box at its end, more than a screen from the bottom: %r" % b)
        self.assertEqual((s["taken"], s["after"]["dist"] <= 2), ([True], True), "the page took End, and the transcript is at the bottom: %r" % s)

    def test_end_in_the_file_viewer_scrolls_the_file(self):
        s = self._run()["viewer"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "fileview" and b["contentShown"] and b["viewer"]["st"] == 0,
                        "focus on the page body, the anchor in the viewer over the shown transcript, the file at its top: %r" % b)
        self._left_to_the_browser(s)
        self.assertGreaterEqual(s["after"]["viewer"]["st"], s["after"]["viewer"]["max"] - 2, "the file scrolled to its end: %r" % s["after"]["viewer"])

    def test_end_with_focus_on_a_control_outside_the_transcript_and_the_anchor_in_it_is_left_to_the_browser(self):
        s = self._run()["viewerButton"]
        b = s["before"]
        self.assertTrue(b["focusTag"] == "BUTTON:button" and b["focusIn"] == "fileview" and b["anchorIn"] == "content" and b["anchorRole"] is None and b["contentShown"],
                        "focus on the viewer's Copy path button, the anchor on a line of the shown transcript: %r" % b)
        self._left_to_the_browser(s)

    def test_end_in_a_file_tab_scrolls_the_file(self):
        s = self._run()["tab"]
        b = s["before"]
        self.assertTrue(b["bodyFocused"] and b["anchorIn"] == "filetab" and b["fileMode"] and b["tab"]["st"] == 0,
                        "focus on the page body, the anchor in the file tab, the file at its top: %r" % b)
        self._left_to_the_browser(s)
        self.assertGreaterEqual(s["after"]["tab"]["st"], s["after"]["tab"]["max"] - 2, "the file scrolled to its end: %r" % s["after"]["tab"])

    def test_end_in_a_file_tab_with_the_anchor_in_the_hidden_transcript_is_left_to_the_browser(self):
        s = self._run()["hidden"]
        b = s["before"]
        self.assertTrue(b["fileMode"] and not b["contentShown"] and b["anchorIn"] == "content",
                        "the file tab up, the transcript hidden, the anchor still in it: %r" % b)
        self._left_to_the_browser(s)

    def test_no_page_error(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
