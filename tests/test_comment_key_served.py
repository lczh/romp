#!/usr/bin/env python3
"""A key and a palette command for commenting on selected chat text, on the real dashboard (the user 2026-09-30, who
wanted a keyboard shortcut for adding a comment, then wanted it two keys rather than three; until then the only way in
was right-click, Comment).

A hermetic kernel serves the shell page over a synthetic chat (the notes-api demo world, session web: one prompt, one
reply). Asserted in the browser:
  a passage selected in the reply and Ctrl+/ pressed opens the comment box in its create mode, on that turn, quoting
  exactly that passage, with its corner just under the selection (the pointer a right-click would have supplied), and
  the box takes the caret;
  the same key with the caret in the message box and the selection still in the transcript opens it too, and the message
  box stays empty with no slash menu (a plain "/" typed there does open the menu, the control that makes this mean
  something);
  a German layout's Ctrl+/ (key "/" typed as Shift+7, code Digit7) opens it from the message box; the key a US keyboard
  has "/" on, which types "-" there (Ctrl+-), does not;
  with nothing selected the key opens nothing and a toast says to select some text;
  the command palette lists "Comment on selection" with its key, and a run from the palette comments on the passage
  that was selected when the palette opened, even though the chat's selection was gone by the time it ran;
  the right-click menu's Comment and the shortcuts dialog name the key, Ctrl+/;
  on a Mac, ⌘/ opens it; ⌘⇧/, which a Mac's browser reports as key "/" with Shift (with ⌘ held the key reads as if
  Shift were up; read in w3c-keyname's source and the engines', not run on a Mac, and the Mac here is a
  navigator.platform override on Linux with the keydown built by script), does not, and neither does a German Mac's
  ⌘⇧7, which arrives as key "7" for the same reason; the menu,
  the palette row and the dialog say ⌘/; and a ⌘⌥M the reader binds in place of it, which reaches the page as key "µ"
  (code KeyM) because Option composes a character, still opens it;
  with file editing on and a .py file open in the file viewer's editor, in the Files pane (with a passage selected in
  the chat), in the chat's own viewer, on a Mac in the chat's viewer, and in a file tab the chat viewer's pop-out opened,
  Ctrl+/ (⌘/) is the editor's toggle comment: the line under the cursor is commented, no toast or comment box appears,
  and the focus stays in the editor; in the editor's search field (Ctrl+F) Ctrl+/ does nothing: the field keeps its
  text, its selection and the focus, the line is unchanged, and no toast or comment box appears;
  with Open the log saved on Ctrl+Shift+/ (the dialog's spelling of a German Ctrl+Shift+7 before #39), Ctrl+/ opens the
  log, and the menu's Comment and the palette row name no key while the dialog's row says it yields;
  with Open the log saved on Ctrl+/ and Token usage on Ctrl+Shift+/, which now share Ctrl+/, the dialog marks the comment
  row and the log's row as yielding to Token usage, the command the key runs, and Ctrl+/ recorded for Remote kernels
  names Token usage and, on Enter, unbinds both saved keys, so Ctrl+/ then opens Remote kernels;
  with Open the log saved on Ctrl+M, Navigate back's default, the recorder asked for Ctrl+M on Remote kernels names
  Open the log and Navigate back, and its Enter unbinds the log's key and Navigate back's default, so Ctrl+M pressed
  from the transcript opens Remote kernels; the same with Ctrl+Alt+↑ and Go to your previous message; and that key
  recorded again for Open the log, which holds it, names the chat command alone, and its Enter unbinds that command's
  default and keeps the log's key, so the key pressed from the transcript opens the log;
  with the comment key rebound to Alt+/ and Navigate back saved on Alt+Shift+/ (both read as Alt+/ now), Alt+/ comments
  and the menu names it, in the shell and on the chat page on its own, since the nav handler reads no chord without
  Ctrl or Meta;
  with a key saved on Ctrl+/ under an id no command has (a command an upgrade removed), Ctrl+/ still comments and the
  menu names it;
  with Open the log saved on Shift+/ (the dialog's spelling of a German Shift+7 before #39), a "/" typed from the
  transcript on a US keyboard reaches the message box and the log stays shut, and a German Shift+7 there (a keydown built
  by script) opens the log;
  on the chat page opened on its own (no shell around it, so no dispatcher), the same saved Ctrl+Shift+/ leaves Ctrl+/
  the comment key, and the menu names it; with Navigate back saved on Ctrl+Shift+/ instead, the chat's own nav handler
  takes Ctrl+/ there: no comment box, no notice, and the menu names no key.
Red before #39: Ctrl+/ and ⌘/ do not comment, the hints name Ctrl+Alt+M, and the saved-binding, recorder and dialog
cases fail as well, except the Shift+/ and Alt+/ cases and the key recorded again for the command that holds it, which
keep behaviour from before #39.
Red with #39 merged, before the fixes the review of #39 asked for: in the file viewer's editor the line stays as it
was and the chat's notice takes the key, in the editor's search field too, or from the Files pane a comment box does,
with the focus moved to the chat; ⌘⇧/ opens a comment box; with Open the log saved on Ctrl+Shift+/, Ctrl+/ opens a
comment box instead, the menu and the palette name Ctrl+/, and the dialog marks nothing; the two saved keys are still
two chords, so Ctrl+/ opens the log, and the recorder names Comment on selection, whose default yields there, and its
Enter unbinds that default and not the saved keys; with Open the log saved on Ctrl+M or Ctrl+Alt+↑, the recorder names
only Navigate back or Go to your previous message, and its Enter leaves the log's key in place; with Navigate back
saved on Ctrl+Shift+/, the chat page on its own opens a comment box and its menu names Ctrl+/; and a German Shift+7
does not open a log saved on Shift+/.
With COMMENT_KEY_SHOTS=<dir> the driver writes the box the key opened, the menu with its key, the palette row, the toast,
each editor after the key and its search field, and the chat page on its own. LAB_ENGINE=firefox or webkit runs the
driver in that Playwright engine (CI runs Chromium). Skips LOUDLY without the extension deps or a Playwright browser.
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
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-abcabcabcabc"
USER_UUID = "11111111-2222-3333-4444-000000000001"
REPLY_UUID = "22222222-3333-4444-5555-000000000001"
REPLY = ("The notes-api export streams rows through a bounded buffer, so a slow client never holds the whole export "
         "in memory. The retry cap stays at two minutes, and the header is written once.")
PASSAGE = "streams rows through a bounded buffer"
SECOND = "The retry cap stays at two minutes"
THIRD = "the header is written once"
NONE_TOAST = "Select some text in the chat to comment on it."
ENGINE = os.environ.get("LAB_ENGINE") or "chromium"   # the Playwright engine the driver launches (CI: Chromium)
CODE_LINE = "def export_rows(buf):"   # line one of the file the editor cases open, and the line they comment


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const engines = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await engines[cfg.engine].launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = {};
const done = async (why) => { if (why) out.died = why; fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n"); await browser.close(); process.exit(0); };
const shot = async (p, name, clip) => { if (!cfg.shots) return; fs.mkdirSync(cfg.shots, { recursive: true }); await p.screenshot({ path: cfg.shots + "/" + name + ".png", ...(clip ? { clip } : {}) }); };

// the shell, its first chat column on the web session with the reply rendered
const boot = async (p) => {
  await p.goto(cfg.url);
  const fr = await (await p.waitForSelector("#f-chat", { timeout: 30000 })).contentFrame();
  try {
    await fr.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 30000 });
    await fr.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
    await fr.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.passage, { timeout: 30000 });
    await p.waitForFunction(() => !document.getElementById("romp-boot"), null, { timeout: 20000 }).catch(() => {});
  } catch (e) { await done("the chat never showed the reply: " + String(e).split("\n")[0]); }
  await p.waitForTimeout(600);
  return fr;
};
// the reply's text node holding `text`: select exactly that, and report the selection's box (the frame's own viewport) and
// the turn it sits in
const select = (fr, text) => fr.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).find((n) => (n.textContent || "").includes(text));
  if (!turn) return null;
  const walker = document.createTreeWalker(turn, NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node && !(node.textContent || "").includes(text)) node = walker.nextNode();
  if (!node) return null;
  const at = node.textContent.indexOf(text);
  const sel = window.getSelection(); sel.removeAllRanges();
  const r = document.createRange(); r.setStart(node, at); r.setEnd(node, at + text.length); sel.addRange(r);
  const b = r.getBoundingClientRect();
  return { text: sel.toString(), uuid: turn.getAttribute("data-uuid"), left: b.left, top: b.top, bottom: b.bottom, right: b.right,
           active: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null };
}, text);
const pop = (fr) => fr.evaluate(() => {
  const p = document.getElementById("cmt-pop");
  if (!p) return null;
  const r = p.getBoundingClientRect();
  const a = document.activeElement;
  return { mode: p.dataset.mode, tid: p.dataset.tid, quote: ((p.querySelector(".cmt-quote") || {}).textContent || "").trim(),
           left: r.left, top: r.top, w: r.width, h: r.height, vw: window.innerWidth, vh: window.innerHeight,
           caret: !!(a && a.closest && a.closest("#cmt-pop .cmt-input")) };
});
const waitPop = (fr) => fr.waitForFunction(() => !!document.getElementById("cmt-pop"), null, { timeout: 8000 }).then(() => true, () => false);
// the comment box closed by its own close button; with no box open it returns at once, since a click on a missing button
// waits out Playwright's 30 s action timeout (the negative cases call it with no box)
const closePop = async (fr) => {
  if (!(await fr.evaluate(() => !!document.getElementById("cmt-pop")))) return;
  await fr.click("#cmt-pop .cmt-x", { timeout: 5000 }).catch(() => {});
  await fr.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {});
};
const toasts = (fr) => fr.evaluate(() => Array.from(document.querySelectorAll(".warn-toast-msg")).map((t) => t.textContent));
const clearToasts = (fr) => fr.evaluate(() => { for (const t of Array.from(document.querySelectorAll(".warn-toast"))) t.remove(); });
// the page rect of an element inside the chat column, for the page's own screenshot clip
const pageRect = (p, sel, pad) => p.evaluate(([sel, pad]) => {
  const f = document.getElementById("f-chat"), fr = f.getBoundingClientRect(), e = f.contentDocument.querySelector(sel);
  if (!e) return null;
  const r = e.getBoundingClientRect();
  const x = Math.max(0, fr.left + r.left - pad), y = Math.max(0, fr.top + r.top - pad);
  return { x, y, width: Math.min(window.innerWidth - x, r.width + 2 * pad), height: Math.min(window.innerHeight - y, r.height + 2 * pad) };
}, [sel, pad]);
// the chat document takes the focus: a click on the prompt's own text (it collapses any selection; each case selects after)
const focusChat = async (fr) => { await fr.click("#content .turn-user", { position: { x: 12, y: 8 } }).catch(async () => { await fr.evaluate(() => window.focus()); }); };

// The file viewer's code editor: open the lab's .py file in `vf`, the frame `home` names (the chat's own, or the Files
// pane's), with `tab` pop it out into a file tab of the chat strip, switch to edit mode (file editing is on in the lab's
// state root), put the cursor on line one, press `key`, and read what happened: the line (commented by the editor, or
// not), the chat's toasts and comment box, whether the focus is still in the editor, and which pane frame the shell's
// focus is on. The viewer over the pane is #romp-fileview; a file tab's view sits in #filetab-host.
const lineOne = (vf, root) => vf.evaluate((root) => { const l = document.querySelector(root + " .cm-content .cm-line"); return l ? l.textContent : null; }, root);
const editorPress = async (p, cf, vf, key, home, label, tab = false) => {
  const r = {};
  // the shell posts the open, as its relay of a file link does; a pane still booting has no listener yet, so ask again
  for (let i = 0; i < 12 && !r.viewer; i++) {
    await p.evaluate(([id, path, sid]) => document.getElementById(id).contentWindow.postMessage({ romp: "viewFile", path, sid }, "*"), [home, cfg.file, cfg.sid]);
    r.viewer = await vf.waitForSelector("#romp-fileview", { timeout: 1500 }).then(() => true, () => false);
  }
  if (tab) {
    // the viewer's pop-out: the viewer closes and the file opens as a tab, shown in the pane in the transcript's place
    r.popOut = await vf.waitForSelector("#romp-fileview .fileview-popout", { timeout: 20000 }).then(() => true, () => false);
    if (!r.popOut) return r;
    await vf.click("#romp-fileview .fileview-popout");
    r.tab = await vf.waitForFunction(() => !document.getElementById("romp-fileview") && document.body.classList.contains("filetab-mode") && !!document.querySelector("#filetab-host .fileview-inline"), null, { timeout: 20000 }).then(() => true, () => false);
    if (!r.tab) return r;
  }
  const root = tab ? "#filetab-host" : "#romp-fileview";
  r.edit = await vf.waitForFunction((root) => { const b = document.querySelector(root + ' button[aria-label="Edit"]'); return !!b && !b.hidden; }, root, { timeout: 20000 }).then(() => true, () => false);
  if (!r.edit) return r;
  await vf.click(root + ' button[aria-label="Edit"]');
  r.editor = await vf.waitForSelector(root + " .cm-editor .cm-line", { timeout: 20000 }).then(() => true, () => false);
  if (!r.editor) return r;
  r.before = await lineOne(vf, root);
  await clearToasts(cf);
  await vf.click(root + " .cm-content .cm-line >> nth=0", { position: { x: 30, y: 5 } });
  r.focusBefore = await vf.evaluate(() => !!(document.activeElement && document.activeElement.closest && document.activeElement.closest(".cm-editor")));
  await p.keyboard.press(key);
  await vf.waitForFunction(([root, was]) => { const l = document.querySelector(root + " .cm-content .cm-line"); return !!l && l.textContent !== was; }, [root, r.before], { timeout: 3000 }).catch(() => {});
  await p.waitForTimeout(600);   // a toast or a box the key opened has had the time to draw
  r.after = await lineOne(vf, root);
  r.toasts = await toasts(cf);
  r.pop = await pop(cf);
  r.focusAfter = await vf.evaluate(() => !!(document.activeElement && document.activeElement.closest && document.activeElement.closest(".cm-editor")));
  r.shellFocus = await p.evaluate(() => document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null);
  await shot(p, label);
  return r;
};

const ctx = await browser.newContext({ viewport: { width: 1300, height: 850 } });
const page = await ctx.newPage();
const fr = await boot(page);

const composer = (fr) => fr.evaluate(() => ({ value: document.getElementById("composer-input").value, slash: !!document.getElementById("slash-pop") }));
// a keydown the page's keyboard cannot type (another layout's, a Mac's composed glyph), dispatched where the focus is
// (`active` is where it was dispatched: a box the key opens takes the focus before the dispatch returns)
const dispatchKey = (fr, init) => fr.evaluate((init) => {
  const at = document.activeElement || document.body;
  const ev = new KeyboardEvent("keydown", { ...init, bubbles: true, cancelable: true });
  at.dispatchEvent(ev);
  return { prevented: ev.defaultPrevented, platform: navigator.platform, active: at.id || at.tagName };
}, init);
// the key's row in the shortcuts dialog (the shell's), read and closed again
const dialogChip = async (p) => {
  await p.evaluate(() => window.__rompKeysOpen());
  await p.waitForSelector('#rkeys-back .rkeys-row[data-cmd="chat.commentSelection"]', { timeout: 8000 }).catch(() => {});
  const chip = await p.evaluate(() => { const r = document.querySelector('#rkeys-back .rkeys-row[data-cmd="chat.commentSelection"]'); return r ? ((r.querySelector(".rkeys-chip") || {}).textContent || "") : null; });
  await p.keyboard.press("Escape");
  return chip;
};
// the bindings store, set from the shell's document and announced the way the dialog's own save announces it (the shell's
// dispatcher rebuilds its chord map on that event; the chat reads the store on every press)
const setKeys = (p, keys) => p.evaluate((keys) => {
  if (keys) localStorage.setItem("romp:keys", JSON.stringify(keys)); else localStorage.removeItem("romp:keys");
  window.dispatchEvent(new Event("romp:keys"));
}, keys);
// which of the shell's panels a key opened: the log, Token usage, Remote kernels; and closing them again
const panels = (p) => p.evaluate(() => {
  const shown = (id) => { const b = document.getElementById(id); return !!b && !b.hidden; };
  const u = document.getElementById("ru-back");
  return { log: shown("rerr-back"), usage: !!u && u.classList.contains("on"), net: shown("rnet-back") };
});
const closePanels = (p) => p.evaluate(() => { for (const f of ["__rompCloseErrs", "__rompUsageClose", "__rompCloseNet"]) { try { if (typeof window[f] === "function") window[f](); } catch (e) {} } });
// the shell's own document takes the focus (a key pressed then is the shell dispatcher's alone)
const focusShell = (p) => p.evaluate(() => { const a = document.activeElement; if (a && a !== document.body && a.blur) a.blur(); document.body.focus(); });
// the dialog's rows for `ids`: what each shows after its title (the chip, and a yield when there is one), then closed
const dialogRows = async (p, ids) => {
  await p.evaluate(() => window.__rompKeysOpen());
  await p.waitForSelector('#rkeys-back .rkeys-row[data-cmd="chat.commentSelection"]', { timeout: 8000 }).catch(() => {});
  const rows = await p.evaluate((ids) => Object.fromEntries(ids.map((id) => {
    const r = document.querySelector(`#rkeys-back .rkeys-row[data-cmd="${id}"]`);
    return [id, r ? Array.from(r.children).filter((c) => c.tagName !== "BUTTON").slice(1).map((c) => c.textContent).join(" | ") : null];
  })), ids);
  await p.keyboard.press("Escape");
  await p.waitForFunction(() => { const b = document.getElementById("rkeys-back"); return !b || b.hidden; }, null, { timeout: 5000 }).catch(() => {});
  return rows;
};
// the recorder: Change on `id`'s row, `key` pressed (the row then names the command holding it), Enter, and the store after
const record = async (p, id, key) => {
  await p.evaluate(() => window.__rompKeysOpen());
  await p.waitForSelector(`#rkeys-back .rkeys-row[data-cmd="${id}"]`, { timeout: 8000 });
  await p.evaluate((id) => Array.from(document.querySelector(`#rkeys-back .rkeys-row[data-cmd="${id}"]`).querySelectorAll("button")).find((b) => b.textContent === "Change").click(), id);
  await p.keyboard.press(key);
  const prompt = await p.evaluate((id) => { const h = document.querySelector(`#rkeys-back .rkeys-row[data-cmd="${id}"] .rkeys-conflict`); return h ? h.textContent : null; }, id);
  await p.keyboard.press("Enter");
  await p.waitForTimeout(300);
  const stored = await p.evaluate(() => JSON.parse(localStorage.getItem("romp:keys") || "{}"));
  for (let i = 0; i < 2; i++) {
    if (await p.evaluate(() => { const b = document.getElementById("rkeys-back"); return !b || b.hidden; })) break;
    await p.keyboard.press("Escape");
    await p.waitForTimeout(150);
  }
  return { prompt, stored };
};
const menuItems = (fr, text) => fr.evaluate((text) => {
  const turn = Array.from(document.querySelectorAll("#content .turn")).find((n) => (n.textContent || "").includes(text));
  const sel = window.getSelection(), r = sel.getRangeAt(0).getBoundingClientRect();
  turn.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, clientX: r.right - 4, clientY: r.bottom - 4, button: 2 }));
  return Array.from(document.querySelectorAll(".ctx-menu .ctx-item")).map((i) => ({ label: (i.querySelector(".ctx-item-label") || {}).textContent || "", sub: (i.querySelector(".ctx-item-sub") || {}).textContent || null }));
}, text);
const paletteRow = async (p, openKey) => {
  await p.keyboard.press(openKey);
  await p.waitForFunction(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, { timeout: 8000 }).catch(() => {});
  await p.keyboard.type("Comment on selection");
  await p.waitForFunction(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Comment on selection/.test(r.textContent || ""); }, null, { timeout: 8000 }).catch(() => {});
  return p.evaluate(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return r ? { title: (r.querySelector(".rpal-title") || {}).textContent || "", kbd: (r.querySelector(".rpal-kbd") || {}).textContent || "" } : null; });
};

// 1. the key, with the focus in the transcript
await focusChat(fr);
out.keySel = await select(fr, cfg.passage);
await page.keyboard.press("Control+/");
out.keyOpened = await waitPop(fr);
out.keyPop = await pop(fr);
await shot(page, "popover-by-key");
const pr = await pageRect(page, "#cmt-pop", 60);
if (pr) await shot(page, "popover-by-key-zoom", pr);
await closePop(fr);

// 2. the key with the caret in the message box, the selection still in the transcript: it comments, and the box gets no
// "/", so its slash menu stays shut
await fr.click("#composer-input");
out.boxSel = await select(fr, cfg.second);
await page.keyboard.press("Control+/");
out.boxOpened = await waitPop(fr);
out.boxPop = await pop(fr);
out.boxAfter = await composer(fr);
await closePop(fr);
// the control: a plain "/" typed into the empty box does open the slash menu
await fr.click("#composer-input");
await page.keyboard.type("/");
await fr.waitForSelector("#slash-pop", { state: "attached", timeout: 8000 }).catch(() => {});
out.slashControl = await composer(fr);
await fr.fill("#composer-input", "");
await fr.waitForFunction(() => !document.getElementById("slash-pop"), null, { timeout: 5000 }).catch(() => {});
// …and it stays open when the box was left and taken back just before, the close its leave scheduled still due (CI
// 2026-09-30: the leave to the comment box above, then the click back and the "/", and the late close shut the menu). A
// timer the page sets right after the leave, with the close's delay, runs after the close, so its mark says the close had
// its turn; the menu is read then
await fr.evaluate(() => { const ta = document.getElementById("composer-input"); ta.blur(); window.__leaveDue = false; setTimeout(() => { window.__leaveDue = true; }, 120); ta.focus(); });
await page.keyboard.type("/");
await fr.waitForFunction(() => window.__leaveDue === true, null, { timeout: 5000 }).catch(() => {});
out.slashAfterReturn = await composer(fr);
await fr.fill("#composer-input", "");
await fr.waitForFunction(() => !document.getElementById("slash-pop"), null, { timeout: 5000 }).catch(() => {});

// 2b. a German layout, where "/" is Shift+7: its Ctrl+/ from the message box comments too; its Ctrl+- (the key a US keyboard
// has "/" on) does not
await fr.click("#composer-input");
out.deSel = await select(fr, cfg.passage);
out.deKey = await dispatchKey(fr, { key: "/", code: "Digit7", ctrlKey: true, shiftKey: true });
out.deOpened = await waitPop(fr);
out.dePop = await pop(fr);
out.deAfter = await composer(fr);
await closePop(fr);
await fr.click("#composer-input");
out.dashSel = await select(fr, cfg.passage);
out.dashKey = await dispatchKey(fr, { key: "-", code: "Slash", ctrlKey: true });
await fr.waitForTimeout(400);
out.dashPop = await pop(fr);
await closePop(fr);

// 3. nothing selected: a toast, no box
await clearToasts(fr);
await focusChat(fr);
await fr.evaluate(() => window.getSelection().removeAllRanges());
await page.keyboard.press("Control+/");
await fr.waitForFunction((m) => Array.from(document.querySelectorAll(".warn-toast-msg")).some((t) => t.textContent === m), cfg.noneToast, { timeout: 5000 }).catch(() => {});
out.noneToasts = await toasts(fr);
out.nonePop = await pop(fr);
await shot(page, "no-selection-toast");
await clearToasts(fr);

// 4. the palette: opened over a selection, which the chat then loses; the run comments on the one the palette opened over
await focusChat(fr);
out.palSel = await select(fr, cfg.third);
await page.keyboard.press("Control+P");
await page.waitForFunction(() => { const b = document.getElementById("rpal-back"); return !!b && !b.hidden; }, null, { timeout: 8000 }).catch(() => {});
await fr.evaluate(() => window.getSelection().removeAllRanges());
out.palLiveSelAfter = await fr.evaluate(() => window.getSelection().toString());
await page.keyboard.type("Comment on selection");
await page.waitForFunction(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return !!r && /Comment on selection/.test(r.textContent || ""); }, null, { timeout: 8000 }).catch(() => {});
out.palRow = await page.evaluate(() => { const r = document.querySelector("#rpal-list .rpal-row.active"); return r ? { title: (r.querySelector(".rpal-title") || {}).textContent || "", kbd: (r.querySelector(".rpal-kbd") || {}).textContent || "" } : null; });
await shot(page, "palette-row");
await page.keyboard.press("Enter");
out.palOpened = await waitPop(fr);
out.palPop = await pop(fr);
await closePop(fr);

// 5. the right-click menu's Comment names the key
await focusChat(fr);
out.menuSel = await select(fr, cfg.passage);
out.menu = await menuItems(fr, cfg.passage);
await shot(page, "menu-key-hint");
const mr = await pageRect(page, ".ctx-menu", 40);
if (mr) await shot(page, "menu-key-hint-zoom", mr);
await page.keyboard.press("Escape");
out.dialogChip = await dialogChip(page);

// 11. Open the log saved on Ctrl+Shift+/, the dialog's spelling of a German Ctrl+Shift+7 before #39: it reads as
// Ctrl+/, so Ctrl+/ opens the log, and the comment command's hints stop naming a key that runs something else
await setKeys(page, { "log.open": "Ctrl+Shift+/" });
await focusChat(fr);
out.savedSel = await select(fr, cfg.passage);
await page.keyboard.press("Control+/");
await page.waitForFunction(() => { const b = document.getElementById("rerr-back"); return !!b && !b.hidden; }, null, { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(300);
out.savedPanels = await panels(page);
out.savedPop = await pop(fr);
out.savedToasts = await toasts(fr);
await closePop(fr);
await closePanels(page);
await focusChat(fr);
await select(fr, cfg.passage);
out.savedMenu = await menuItems(fr, cfg.passage);
await page.keyboard.press("Escape");
out.savedPalRow = await paletteRow(page, "Control+P");
await page.keyboard.press("Escape");
out.savedDialog = await dialogRows(page, ["chat.commentSelection", "log.open"]);

// 12. Open the log saved on Ctrl+/ and Token usage on Ctrl+Shift+/: both read as Ctrl+/ now, and the dispatcher gives it to
// Token usage, registered later. The dialog names that command; the recorder, asked for Ctrl+/ on Remote kernels, names it
// too, and its Enter unbinds both saved keys, so Ctrl+/ then opens Remote kernels. The stored bindings after Enter are
// what catch an Enter that unbinds only the named command: Remote kernels registers after Open the log, so its Ctrl+/
// would open it even with the log's binding left in place
await setKeys(page, { "log.open": "Ctrl+/", "usage.open": "Ctrl+Shift+/" });
out.bothDialog = await dialogRows(page, ["chat.commentSelection", "log.open", "usage.open"]);
await focusShell(page);
await page.keyboard.press("Control+/");
await page.waitForTimeout(600);
out.bothKey = await panels(page);
await closePanels(page);
out.bothRecord = await record(page, "net.open", "Control+/");
await focusShell(page);
await page.keyboard.press("Control+/");
await page.waitForTimeout(600);
out.bothAfter = await panels(page);
await closePanels(page);

// 16. Open the log saved on Ctrl+M, Navigate back's default, and then on Ctrl+Alt+↑, Go to your previous message's: the
// recorder, asked for that key on Remote kernels, names both commands, and its Enter unbinds the log's key and the chat
// command's default, whose own handler would otherwise go on taking the key from the chat; so the key pressed from the
// transcript opens Remote kernels. Asked for the same key again on Open the log, which holds it, the recorder names the
// chat command, and its Enter unbinds that default and keeps the log's key, so the key from the transcript opens the log
out.ownRecord = {};
out.ownAfter = {};
out.againRecord = {};
out.againAfter = {};
for (const [chord, press] of [["Ctrl+M", "Control+m"], ["Ctrl+Alt+ArrowUp", "Control+Alt+ArrowUp"]]) {
  await setKeys(page, { "log.open": chord });
  out.ownRecord[chord] = await record(page, "net.open", press);
  await focusChat(fr);
  await page.keyboard.press(press);
  await page.waitForFunction(() => { const b = document.getElementById("rnet-back"); return !!b && !b.hidden; }, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  out.ownAfter[chord] = await panels(page);
  await closePanels(page);
  await setKeys(page, { "log.open": chord });
  out.againRecord[chord] = await record(page, "log.open", press);
  await focusChat(fr);
  await page.keyboard.press(press);
  await page.waitForFunction(() => { const b = document.getElementById("rerr-back"); return !!b && !b.hidden; }, null, { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(300);
  out.againAfter[chord] = await panels(page);
  await closePanels(page);
}

// 17. the comment key rebound to Alt+/ and Navigate back saved on Alt+Shift+/, both from before #39, which now read as one
// chord: the nav trail's handler reads no chord without Ctrl or Meta, so Alt+/ comments and the menu names it, in the shell
// here and on the chat page on its own below
await setKeys(page, { "chat.commentSelection": "Alt+/", "chat.navBack": "Alt+Shift+/" });
await focusChat(fr);
out.altSel = await select(fr, cfg.passage);
await page.keyboard.press("Alt+/");
out.altOpened = await waitPop(fr);
out.altPop = await pop(fr);
await closePop(fr);
await focusChat(fr);
await select(fr, cfg.passage);
out.altMenu = await menuItems(fr, cfg.passage);
await page.keyboard.press("Escape");
await setKeys(page, null);

// 14. a key saved on Ctrl+/ under an id no command has (a command an upgrade removed): it runs nothing, so Ctrl+/ still
// comments, and the menu still names it
await setKeys(page, { "retired.command": "Ctrl+/" });
await focusChat(fr);
out.retiredSel = await select(fr, cfg.passage);
await page.keyboard.press("Control+/");
out.retiredOpened = await waitPop(fr);
out.retiredPop = await pop(fr);
out.retiredPanels = await panels(page);
await closePop(fr);
await focusChat(fr);
await select(fr, cfg.passage);
out.retiredMenu = await menuItems(fr, cfg.passage);
await page.keyboard.press("Escape");

// 15. Open the log saved on Shift+/ (the dialog's spelling of a German Shift+7 before #39): a "/" typed from the
// transcript on a US keyboard is typing, so it reaches the message box and the log stays shut; a German Shift+7 from the
// transcript opens the log, as it did before #39
await setKeys(page, { "log.open": "Shift+/" });
await focusChat(fr);
await page.keyboard.press("/");
await page.waitForTimeout(500);
out.slashPanels = await panels(page);
out.slashBox = await composer(fr);
await closePanels(page);
await fr.fill("#composer-input", "");
await fr.waitForFunction(() => !document.getElementById("slash-pop"), null, { timeout: 5000 }).catch(() => {});
await focusChat(fr);
out.slashDeKey = await dispatchKey(fr, { key: "/", code: "Digit7", shiftKey: true });
await page.waitForFunction(() => { const b = document.getElementById("rerr-back"); return !!b && !b.hidden; }, null, { timeout: 5000 }).catch(() => {});
out.slashDePanels = await panels(page);
await closePanels(page);
await setKeys(page, null);

// 6. a Mac: navigator.platform says so. ⌘/ pressed for real; then ⌘⇧/ as a Mac's browser reports it (key "/" with Shift:
// with ⌘ held the key reads as if Shift were up) and a German Mac's ⌘⇧7 (key "7" for the same reason): neither is ⌘/
const mctx = await browser.newContext({ viewport: { width: 1300, height: 850 } });
await mctx.addInitScript(() => { Object.defineProperty(Navigator.prototype, "platform", { get: () => "MacIntel", configurable: true }); });
const mpage = await mctx.newPage();
const mfr = await boot(mpage);
await focusChat(mfr);
out.macSel = await select(mfr, cfg.passage);
await mpage.keyboard.press("Meta+/");
out.macOpened = await waitPop(mfr);
out.macPop = await pop(mfr);
await closePop(mfr);
await focusChat(mfr);
out.macHelpSel = await select(mfr, cfg.second);
out.macHelpKey = await dispatchKey(mfr, { key: "/", code: "Slash", metaKey: true, shiftKey: true });
out.macDeKey = await dispatchKey(mfr, { key: "7", code: "Digit7", metaKey: true, shiftKey: true });
await mfr.waitForTimeout(400);
out.macHelpPop = await pop(mfr);
await closePop(mfr);
// the hints say ⌘/ there: the menu's sub-line, the palette's chip, the dialog's
await focusChat(mfr);
await select(mfr, cfg.passage);
out.macMenu = await menuItems(mfr, cfg.passage);
await mpage.keyboard.press("Escape");
out.macPalRow = await paletteRow(mpage, "Meta+P");
await mpage.keyboard.press("Escape");
out.macDialogChip = await dialogChip(mpage);

// 7. a Mac whose reader bound ⌘⌥M instead: it reaches the page as key "µ" with code KeyM (Option composes a character), and
// the chord still matches its binding
await mfr.evaluate(() => localStorage.setItem("romp:keys", JSON.stringify({ "chat.commentSelection": "Mod+Alt+M" })));
await focusChat(mfr);
out.boundSel = await select(mfr, cfg.passage);
out.boundKey = await dispatchKey(mfr, { key: "µ", code: "KeyM", altKey: true, metaKey: true });
out.boundOpened = await waitPop(mfr);
out.boundPop = await pop(mfr);
await closePop(mfr);

// 8. the file viewer's code editor on a Mac, in the chat's own viewer, the binding reset to the default: ⌘/ is the
// editor's toggle comment there
await mfr.evaluate(() => localStorage.removeItem("romp:keys"));
out.edMac = await editorPress(mpage, mfr, mfr, "Meta+/", "f-chat", "editor-mac-chat");
await mctx.close();

// 9. the code editor in the Files pane, a passage selected in the chat: Ctrl+/ comments the editor's line, and neither the
// chat's notice nor a comment box on that passage takes the key, nor the focus
const ectx = await browser.newContext({ viewport: { width: 1500, height: 900 } });
await ectx.addInitScript(() => { try { if (localStorage.getItem("romp:settings") === null) localStorage.setItem("romp:settings", JSON.stringify({ showFilesControl: true })); } catch (e) {} });
const epage = await ectx.newPage();
const efr = await boot(epage);
await epage.evaluate(() => window.__rompPaneToggle("files", true));
const ff = await (await epage.waitForSelector("#f-files", { timeout: 15000 })).contentFrame();
await ff.waitForLoadState("load").catch(() => {});
await ff.waitForFunction(() => document.readyState === "complete", null, { timeout: 15000 }).catch(() => {});
out.edFilesSel = await select(efr, cfg.passage);
out.edFiles = await editorPress(epage, efr, ff, "Control+/", "f-files", "editor-files");
// 10. the code editor in the chat's own viewer
out.edChat = await editorPress(epage, efr, efr, "Control+/", "f-chat", "editor-chat");
// 19. the editor's search field there: Ctrl+F opens it, and Ctrl+/ typed in it does nothing: the line stays as the editor
// left it, the field keeps its text, its selection and the focus, and no notice or comment box appears
await clearToasts(efr);
await epage.keyboard.press("Control+f");
out.edSearch = { open: await efr.waitForFunction(() => { const a = document.activeElement; return !!(a && a.closest && a.closest(".cm-search")); }, null, { timeout: 5000 }).then(() => true, () => false) };
if (out.edSearch.open) {
  const field = () => efr.evaluate(() => { const a = document.activeElement; return { value: a.value, start: a.selectionStart, end: a.selectionEnd,
    inSearch: !!(a.closest && a.closest(".cm-search")), line: (document.querySelector("#romp-fileview .cm-content .cm-line") || {}).textContent || null }; });
  await epage.keyboard.type("rows");
  out.edSearch.before = await field();
  await epage.keyboard.press("Control+/");
  await epage.waitForTimeout(600);
  out.edSearch.after = await field();
  out.edSearch.toasts = await toasts(efr);
  out.edSearch.pop = await pop(efr);
  await shot(epage, "editor-search");
}
await ectx.close();

// 18. the code editor in a file tab: the chat viewer's pop-out keeps the file open as a tab of the chat strip, and Ctrl+/ in
// its editor is the editor's toggle comment too
const tctx = await browser.newContext({ viewport: { width: 1300, height: 850 } });
const tpage = await tctx.newPage();
const tfr = await boot(tpage);
out.edTab = await editorPress(tpage, tfr, tfr, "Control+/", "f-chat", "editor-tab", true);
await tctx.close();

// 13. the chat page opened on its own, with no shell around it and so no dispatcher, and Open the log saved on
// Ctrl+Shift+/: nothing here would run the log, so Ctrl+/ stays the comment key, and the menu names it
const actx = await browser.newContext({ viewport: { width: 1300, height: 850 } });
const apage = await actx.newPage();
await apage.goto(cfg.chat, { waitUntil: "domcontentloaded", timeout: 60000 });
try {
  await apage.waitForSelector(`#tabs .tab[data-id="${cfg.sid}"]`, { state: "attached", timeout: 45000 });
  await apage.evaluate((sid) => document.querySelector(`#tabs .tab[data-id="${sid}"]`).click(), cfg.sid);
  await apage.waitForFunction((t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => (n.textContent || "").includes(t)), cfg.passage, { timeout: 30000 });
} catch (e) { await done("the chat page on its own never showed the reply: " + String(e).split("\n")[0]); }
await apage.waitForTimeout(600);
const afr = apage.mainFrame();
out.aloneShell = await apage.evaluate(() => !!document.getElementById("f-chat") || window.parent !== window);
await apage.evaluate(() => localStorage.setItem("romp:keys", JSON.stringify({ "log.open": "Ctrl+Shift+/" })));
await focusChat(afr);
out.aloneSel = await select(afr, cfg.passage);
await apage.keyboard.press("Control+/");
out.aloneOpened = await waitPop(afr);
out.alonePop = await pop(afr);
out.aloneToasts = await toasts(afr);
await shot(apage, "chat-page-alone");
await closePop(afr);
await focusChat(afr);
await select(afr, cfg.passage);
out.aloneMenu = await menuItems(afr, cfg.passage);
await apage.keyboard.press("Escape");
// …and with Navigate back saved on Ctrl+Shift+/, the chat's own nav handler takes Ctrl+/ on this page too: no comment
// box, no notice, and the menu names no key (a window listener added after the page's own reads that the key was taken)
await apage.evaluate(() => {
  localStorage.setItem("romp:keys", JSON.stringify({ "chat.navBack": "Ctrl+Shift+/" }));
  window.__keySeen = null;
  window.addEventListener("keydown", (e) => { if (e.key === "/") window.__keySeen = { prevented: e.defaultPrevented }; }, true);
});
await focusChat(afr);
out.aloneNavSel = await select(afr, cfg.passage);
await clearToasts(afr);
await apage.keyboard.press("Control+/");
await afr.waitForTimeout(600);
out.aloneNavPop = await pop(afr);
out.aloneNavToasts = await toasts(afr);
out.aloneNavSeen = await apage.evaluate(() => window.__keySeen);
await closePop(afr);
await focusChat(afr);
await select(afr, cfg.passage);
out.aloneNavMenu = await menuItems(afr, cfg.passage);
await apage.keyboard.press("Escape");
// 17, on the chat page on its own: the comment key on Alt+/ beside a Navigate back saved on Alt+Shift+/ comments here too,
// and the menu names it
await apage.evaluate(() => localStorage.setItem("romp:keys", JSON.stringify({ "chat.commentSelection": "Alt+/", "chat.navBack": "Alt+Shift+/" })));
await focusChat(afr);
out.aloneAltSel = await select(afr, cfg.passage);
await apage.keyboard.press("Alt+/");
out.aloneAltOpened = await waitPop(afr);
out.aloneAltPop = await pop(afr);
await closePop(afr);
await focusChat(afr);
await select(afr, cfg.passage);
out.aloneAltMenu = await menuItems(afr, cfg.passage);
await apage.keyboard.press("Escape");
await actx.close();
await done();
"""


class ServedCommentKey(unittest.TestCase):
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
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p[process.argv[2]].executablePath())",
                                os.path.join(EXT, "node_modules", "playwright"), ENGINE], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright %s browser on this box: the served guard needs one" % ENGINE)
        cls.lab = tempfile.mkdtemp(prefix="comment-key-")
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
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        # file editing on (the opt-in the kernel reads before the viewer's Edit, kernel.py _file_editing_on) and a small
        # Python file in the session's directory, for the editor cases
        Path(state, "file-editing.json").write_text(json.dumps({"enabled": True, "gt": 1}))
        Path(cwd, "export.py").write_text(CODE_LINE + "\n    return list(buf)\n")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        recs = [
            {"type": "user", "timestamp": iso(t0), "uuid": USER_UUID, "parentUuid": None, "promptSource": "typed", "sessionId": WEB,
             "message": {"role": "user", "content": "notes-api: how does the export handle a slow client?"}},
            {"type": "assistant", "timestamp": iso(t0 + 20), "uuid": REPLY_UUID, "parentUuid": USER_UUID, "sessionId": WEB,
             "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": REPLY}]}},
        ]
        Path(proj, WEB + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        cls.port, cls.token = _free_port(), "testtok-commentkey"
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
            json.dump({"url": "http://127.0.0.1:%d/?token=%s" % (self.port, self.token), "sid": WEB,
                       "chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token),
                       "passage": PASSAGE, "second": SECOND, "third": THIRD, "noneToast": NONE_TOAST,
                       "file": os.path.join(self.lab, "proj", "export.py"), "engine": ENGINE,
                       "shots": os.environ.get("COMMENT_KEY_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright %s browser on this box: the served guard needs one" % ENGINE)
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-3000:])
        r = json.loads(line[len("RESULT:"):])
        self.assertNotIn("died", r, r)
        return r

    def _anchored(self, pop, sel, where):
        """The box opened in its create mode on the selection's turn, quoting exactly the selection, its corner just under
        the selection's box (clamped inside the pane, as any placement is), with the caret in it."""
        self.assertIsNotNone(pop, "%s: no comment box opened" % where)
        self.assertEqual(pop["mode"], "create", "%s: %r" % (where, pop))
        self.assertEqual(pop["tid"], REPLY_UUID, "%s: anchored on the reply's turn: %r" % (where, pop))
        self.assertEqual(pop["quote"], sel["text"], "%s: it quotes exactly the selection: %r vs %r" % (where, pop, sel))
        want_x = max(8, min(sel["left"], pop["vw"] - pop["w"] - 8))
        want_y = max(8, min(sel["bottom"] + 6, pop["vh"] - pop["h"] - 8))
        self.assertLess(abs(pop["left"] - want_x), 1.5, "%s: the box's left at the selection's: %r vs %r" % (where, pop, sel))
        self.assertLess(abs(pop["top"] - want_y), 1.5, "%s: the box just under the selection: %r vs %r" % (where, pop, sel))
        self.assertTrue(pop["caret"], "%s: the box takes the caret, as the menu's Comment does: %r" % (where, pop))

    def _dialog(self, rows):
        """The dialog's rows as dialogRows read them ("<chip>", or "<chip> | <yield mark>"), each as its chip and the title
        its yield mark quotes, None with no mark. The mark's other words are the dialog's copy, which tab-cycle.test.ts pins
        as source text; a mark that quotes no title reads as itself."""
        if not isinstance(rows, dict):
            return rows
        out = {}
        for cid, shown in rows.items():
            if shown is None:
                out[cid] = None
                continue
            chip, _, mark = shown.partition(" | ")
            m = re.search("\u201c(.*)\u201d", mark)
            out[cid] = (chip, m.group(1) if m else (mark or None))
        return out

    def test_the_key_and_the_palette_open_what_the_menu_opens(self):
        r = self._drive()
        # 1. the key with the focus in the transcript
        self.assertEqual((r["keySel"] or {}).get("text"), PASSAGE, r["keySel"])
        self.assertTrue(r["keyOpened"], "Ctrl+/ over a selected passage opened no comment box: %r" % r)
        self._anchored(r["keyPop"], r["keySel"], "the key")
        # 2. from the message box, the selection still in the transcript; the box types no "/" and opens no slash menu
        self.assertEqual(r["boxSel"]["active"], "composer-input", "the caret sat in the message box as the key was pressed: %r" % r["boxSel"])
        self.assertTrue(r["boxOpened"], "the key from the message box opened nothing: %r" % r)
        self._anchored(r["boxPop"], r["boxSel"], "from the message box")
        self.assertEqual(r["boxAfter"], {"value": "", "slash": False}, "Ctrl+/ put a \"/\" in the message box or opened its slash menu: %r" % r["boxAfter"])
        self.assertEqual(r["slashControl"], {"value": "/", "slash": True}, "the control: a typed \"/\" opens the slash menu: %r" % r["slashControl"])
        self.assertEqual(r["slashAfterReturn"], {"value": "/", "slash": True}, "the box left and taken back, a \"/\" typed while the leave's close was due: the menu stays open: %r" % r["slashAfterReturn"])
        # 2b. a German layout: Ctrl+Shift+7 spells "/" and comments; Ctrl+- on the US slash key's position does not
        self.assertEqual(r["deKey"]["active"], "composer-input", r["deKey"])
        self.assertTrue(r["deKey"]["prevented"], "a German Ctrl+/ was taken: %r" % r["deKey"])
        self.assertTrue(r["deOpened"], "a German Ctrl+/ (Ctrl+Shift+7) opened no comment box: %r" % r)
        self._anchored(r["dePop"], r["deSel"], "a German Ctrl+/")
        self.assertEqual(r["deAfter"], {"value": "", "slash": False}, r["deAfter"])
        self.assertEqual(r["dashSel"]["text"], PASSAGE, r["dashSel"])
        self.assertFalse(r["dashKey"]["prevented"], "Ctrl+- stays the browser's: %r" % r["dashKey"])
        self.assertIsNone(r["dashPop"], "Ctrl+- (a German keyboard's key where a US one has \"/\") must not comment: %r" % r["dashPop"])
        # 3. nothing selected: a toast, and nothing opens
        self.assertIsNone(r["nonePop"], "nothing selected must open nothing: %r" % r["nonePop"])
        self.assertIn(NONE_TOAST, r["noneToasts"], r["noneToasts"])
        # 4. the palette: listed with its key; its run comments on the passage selected as it opened, which the chat lost since
        self.assertEqual(r["palRow"], {"title": "Comment on selection", "kbd": "Ctrl+/"}, r["palRow"])
        self.assertEqual(r["palLiveSelAfter"], "", "the chat's own selection was gone before the run: %r" % r["palLiveSelAfter"])
        self.assertTrue(r["palOpened"], "the palette's run opened no comment box: %r" % r)
        self._anchored(r["palPop"], r["palSel"], "the palette")
        # 5. the right-click menu names the key under Comment, and only there; the shortcuts dialog shows it on its row
        menu = {m["label"]: m["sub"] for m in r["menu"]}
        self.assertEqual(menu.get("Comment"), "Ctrl+/", "the menu's Comment names the key: %r" % r["menu"])
        self.assertIsNone(menu.get("Quote"), r["menu"])
        self.assertEqual(r["dialogChip"], "Ctrl+/", "the shortcuts dialog's row: %r" % r["dialogChip"])
        # 6. a Mac: ⌘/ comments; ⌘⇧/ and a German Mac's ⌘⇧7 do not; every hint says ⌘/
        self.assertTrue(r["macOpened"], "⌘/ on a Mac opened no comment box: %r" % r)
        self._anchored(r["macPop"], r["macSel"], "a Mac's ⌘/")
        self.assertEqual(r["macHelpKey"]["platform"], "MacIntel", r["macHelpKey"])
        self.assertEqual(r["macHelpSel"]["text"], SECOND, r["macHelpSel"])
        self.assertFalse(r["macHelpKey"]["prevented"], "⌘⇧/ (key \"/\" with Shift and ⌘) was taken as ⌘/: %r" % r["macHelpKey"])
        self.assertFalse(r["macDeKey"]["prevented"], "a German Mac's ⌘⇧7 (key \"7\") was taken: %r" % r["macDeKey"])
        self.assertIsNone(r["macHelpPop"], "⌘⇧/ or a German Mac's ⌘⇧7 opened a comment box: %r" % r["macHelpPop"])
        self.assertEqual({m["label"]: m["sub"] for m in r["macMenu"]}.get("Comment"), "⌘/", r["macMenu"])
        self.assertEqual(r["macPalRow"], {"title": "Comment on selection", "kbd": "⌘/"}, r["macPalRow"])
        self.assertEqual(r["macDialogChip"], "⌘/", r["macDialogChip"])
        # 7. a Mac with ⌘⌥M bound in its place: spelled as µ with code KeyM, it still matches
        self.assertTrue(r["boundKey"]["prevented"], "the bound ⌘⌥M was taken on a Mac: %r" % r["boundKey"])
        self.assertTrue(r["boundOpened"], "a bound ⌘⌥M on a Mac opened no comment box: %r" % r)
        self._anchored(r["boundPop"], r["boundSel"], "a Mac's bound ⌘⌥M")
        # 8 to 10 and 18. the file viewer's code editor keeps its own toggle comment: the Files pane (with a passage selected
        # in the chat), the chat's own viewer, the chat's viewer on a Mac, and a file tab's editor
        self.assertEqual((r["edFilesSel"] or {}).get("text"), PASSAGE, r["edFilesSel"])
        self.assertTrue(r["edTab"].get("popOut") and r["edTab"].get("tab"), "the chat viewer's pop-out never made a file tab: %r" % r["edTab"])
        for name, where, home in (("edFiles", "the Files pane's editor, Ctrl+/", "f-files"),
                                  ("edChat", "the chat viewer's editor, Ctrl+/", "f-chat"),
                                  ("edMac", "the chat viewer's editor on a Mac, ⌘/", "f-chat"),
                                  ("edTab", "a file tab's editor, Ctrl+/", "f-chat")):
            e = r[name]
            self.assertTrue(e.get("edit") and e.get("editor"), "%s: the viewer never reached its editor: %r" % (where, e))
            self.assertEqual(e["before"], CODE_LINE, "%s: %r" % (where, e))
            self.assertTrue(e["focusBefore"], "%s: the cursor was not in the editor when the key was pressed: %r" % (where, e))
            self.assertEqual(e["after"], "# " + CODE_LINE, "%s: the editor did not comment its line: %r" % (where, e))
            self.assertEqual(e["toasts"], [], "%s: the chat's notice took the key: %r" % (where, e))
            self.assertIsNone(e["pop"], "%s: a comment box took the key: %r" % (where, e))
            self.assertTrue(e["focusAfter"], "%s: the focus left the editor: %r" % (where, e))
            self.assertEqual(e["shellFocus"], home, "%s: the shell moved the focus to another pane: %r" % (where, e))
        # 19. the editor's search field: Ctrl+/ there leaves the field, the line and the focus as they were, and opens nothing
        s = r["edSearch"]
        self.assertTrue(s["open"], "Ctrl+F in the chat viewer's editor never opened its search field: %r" % s)
        self.assertEqual(s["before"], {"value": "rows", "start": 4, "end": 4, "inSearch": True, "line": "# " + CODE_LINE}, s)
        self.assertEqual(s["after"], s["before"], "Ctrl+/ in the editor's search field changed the field, its focus or the line: %r" % s)
        self.assertEqual(s["toasts"], [], "the chat's notice took the key in the editor's search field: %r" % s)
        self.assertIsNone(s["pop"], "a comment box took the key in the editor's search field: %r" % s)
        # 11. Open the log saved on Ctrl+Shift+/: Ctrl+/ opens it, and no hint names Ctrl+/ as the comment key
        self.assertEqual((r["savedSel"] or {}).get("text"), PASSAGE, r["savedSel"])
        self.assertEqual(r["savedPanels"], {"log": True, "usage": False, "net": False},
                         "Ctrl+/ with Open the log saved on Ctrl+Shift+/ did not open the log: %r" % r)
        self.assertIsNone(r["savedPop"], "the comment key took Ctrl+/ from the saved binding: %r" % r["savedPop"])
        self.assertEqual(r["savedToasts"], [], r["savedToasts"])
        saved_menu = {m["label"]: m["sub"] for m in r["savedMenu"]}
        self.assertIn("Comment", saved_menu, r["savedMenu"])
        self.assertIsNone(saved_menu["Comment"], "the menu's Comment names a key that opens the log: %r" % r["savedMenu"])
        self.assertEqual(r["savedPalRow"], {"title": "Comment on selection", "kbd": ""},
                         "the palette row names a key that opens the log: %r" % r["savedPalRow"])
        self.assertEqual(self._dialog(r["savedDialog"]), {"chat.commentSelection": ("Ctrl+/", "Open the log"), "log.open": ("Ctrl+/", None)},
                         r["savedDialog"])
        # 12. two saved keys that now share Ctrl+/: the dialog and the recorder name the command the key runs, and the
        # recorder's Enter frees the chord from both, so the fresh binding runs
        self.assertEqual(r["bothKey"], {"log": False, "usage": True, "net": False}, "Ctrl+/ did not run Token usage, the later registered of the two: %r" % r["bothKey"])
        self.assertEqual(self._dialog(r["bothDialog"]), {"chat.commentSelection": ("Ctrl+/", "Token usage"),
                                                         "log.open": ("Ctrl+/", "Token usage"),
                                                         "usage.open": ("Ctrl+/", None)},
                         "the dialog names a command the key does not run, or leaves the dead row unmarked: %r" % r["bothDialog"])
        self.assertIn("\u201cToken usage\u201d", r["bothRecord"]["prompt"] or "", "the recorder did not name Token usage, the command holding Ctrl+/: %r" % r["bothRecord"])
        self.assertEqual(r["bothRecord"]["stored"], {"log.open": "", "usage.open": "", "net.open": "Ctrl+/"}, r["bothRecord"])
        self.assertEqual(r["bothAfter"], {"log": False, "usage": False, "net": True},
                         "after the reassign Ctrl+/ must open Remote kernels: %r" % r["bothAfter"])
        # 16. a key saved for another command on a chat command's default, recorded for a third: the recorder names both and
        # unbinds both, so the key opens Remote kernels from the transcript
        for chord, shown, own, title in (("Ctrl+M", "Ctrl+M", "chat.navBack", "Navigate back in the chat"),
                                         ("Ctrl+Alt+ArrowUp", "Ctrl+Alt+\u2191", "chat.prevMine", "Go to your previous message")):
            rec = r["ownRecord"][chord]
            for named in ("Open the log", title):
                self.assertIn("\u201c%s\u201d" % named, rec["prompt"] or "", "the recorder left out a command its Enter unbinds: %r" % rec)
            self.assertEqual(rec["stored"], {"log.open": "", own: "", "net.open": chord}, "Enter left the saved key or %s's default bound: %r" % (own, rec))
            self.assertEqual(r["ownAfter"][chord], {"log": False, "usage": False, "net": True},
                             "%s from the transcript must open Remote kernels, not run %s: %r" % (chord, own, r["ownAfter"][chord]))
            # …and recorded again for Open the log, which holds it: the recorder names the chat command alone, its Enter
            # unbinds that default and keeps the log's key, and the key from the transcript opens the log
            again = r["againRecord"][chord]
            self.assertTrue((again["prompt"] or "").startswith(shown + " "),
                            "recorded again for the command that holds it, the recorder showed no row for %s: %r" % (shown, again))
            self.assertEqual(re.findall("\u201c([^\u201d]*)\u201d", again["prompt"] or ""), [title],
                             "recorded again for the command that holds it, the recorder did not name %s alone: %r" % (own, again))
            self.assertEqual(again["stored"], {"log.open": chord, own: ""}, "Enter left %s's default on %s: %r" % (own, chord, again))
            self.assertEqual(r["againAfter"][chord], {"log": True, "usage": False, "net": False},
                             "%s from the transcript must open the log, not run %s: %r" % (chord, own, r["againAfter"][chord]))
        # 17. the comment key on Alt+/ beside a Navigate back saved on Alt+Shift+/: it comments and the menu names it, in the
        # shell and on the chat page on its own
        for pre, where in (("alt", "in the shell"), ("aloneAlt", "on the chat page on its own")):
            self.assertEqual((r[pre + "Sel"] or {}).get("text"), PASSAGE, r[pre + "Sel"])
            self.assertTrue(r[pre + "Opened"], "Alt+/ %s opened no comment box: %r" % (where, r))
            self._anchored(r[pre + "Pop"], r[pre + "Sel"], "Alt+/ " + where)
            self.assertEqual({m["label"]: m["sub"] for m in r[pre + "Menu"]}.get("Comment"), "Alt+/",
                             "the menu %s names no key that comments: %r" % (where, r[pre + "Menu"]))
        # 13. the chat page on its own: the saved Ctrl+Shift+/ takes nothing there, so Ctrl+/ comments and the menu says so
        self.assertFalse(r["aloneShell"], "the chat page had a shell around it: %r" % r["aloneShell"])
        self.assertTrue(r["aloneOpened"], "Ctrl+/ on the chat page on its own opened no comment box: %r" % r)
        self._anchored(r["alonePop"], r["aloneSel"], "the chat page on its own")
        self.assertEqual(r["aloneToasts"], [], r["aloneToasts"])
        self.assertEqual({m["label"]: m["sub"] for m in r["aloneMenu"]}.get("Comment"), "Ctrl+/", r["aloneMenu"])
        # …and with Navigate back saved there, the chat's nav handler takes the key: one command, no comment, no key named
        self.assertEqual((r["aloneNavSel"] or {}).get("text"), PASSAGE, r["aloneNavSel"])
        self.assertIsNone(r["aloneNavPop"], "Ctrl+/ with Navigate back saved on Ctrl+Shift+/ also opened a comment box: %r" % r["aloneNavPop"])
        self.assertEqual(r["aloneNavToasts"], [], r["aloneNavToasts"])
        self.assertEqual(r["aloneNavSeen"], {"prevented": True}, "nothing took the key (its default was not prevented): %r" % r["aloneNavSeen"])
        alone_nav_menu = {m["label"]: m["sub"] for m in r["aloneNavMenu"]}
        self.assertIn("Comment", alone_nav_menu, r["aloneNavMenu"])
        self.assertIsNone(alone_nav_menu["Comment"], "the menu names a key that goes back: %r" % r["aloneNavMenu"])
        # 14. a key saved under an id no command has takes nothing: Ctrl+/ comments, and the menu names it
        self.assertTrue(r["retiredOpened"], "Ctrl+/ with a key saved under an unknown id opened no comment box: %r" % r)
        self._anchored(r["retiredPop"], r["retiredSel"], "Ctrl+/ beside a key saved under an unknown id")
        self.assertEqual(r["retiredPanels"], {"log": False, "usage": False, "net": False}, r["retiredPanels"])
        self.assertEqual({m["label"]: m["sub"] for m in r["retiredMenu"]}.get("Comment"), "Ctrl+/",
                         "the menu hides a key that still comments: %r" % r["retiredMenu"])
        # 15. Open the log saved on Shift+/: a US keyboard's "/" from the transcript types into the message box, and a German
        # Shift+7 opens the log
        self.assertEqual(r["slashPanels"], {"log": False, "usage": False, "net": False},
                         "a bare \"/\" opened the log saved on Shift+/: %r" % r["slashPanels"])
        self.assertEqual(r["slashBox"]["value"], "/", "the \"/\" typed from the transcript never reached the message box: %r" % r["slashBox"])
        self.assertTrue(r["slashDeKey"]["prevented"], "a German Shift+7 from the transcript was not taken: %r" % r["slashDeKey"])
        self.assertEqual(r["slashDePanels"], {"log": True, "usage": False, "net": False},
                         "a German Shift+7 did not open the log saved on Shift+/: %r" % r["slashDePanels"])


if __name__ == "__main__":
    unittest.main()
