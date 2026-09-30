#!/usr/bin/env python3
"""A key and a palette command for commenting on selected chat text, on the real dashboard (the user 2026-09-30, who
wanted a keyboard shortcut for adding a comment, then wanted it two keys rather than three; until then the only way in
was right-click, Comment).

A hermetic kernel serves the shell page over a synthetic chat (the notes-api demo world, session web: one prompt, one
reply). Asserted in the browser, in the shell's first chat column:
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
  on a Mac, ⌘/ opens it, and so does a German Mac's ⌘⇧7; the menu, the palette row and the dialog say ⌘/; and a ⌘⌥M the
  reader binds in place of it, which reaches the page as key "µ" (code KeyM) because Option composes a character, still
  opens it.
Red before the change: Ctrl+/ and ⌘/ do nothing, and the hints name Ctrl+Alt+M.
With COMMENT_KEY_SHOTS=<dir> the driver writes the box the key opened, the menu with its key, the palette row and the
toast. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (placeholder UUIDs,
invented prose, host TESTHOST)."""
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
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
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
const closePop = async (fr) => { await fr.click("#cmt-pop .cmt-x").catch(() => {}); await fr.waitForFunction(() => !document.getElementById("cmt-pop"), null, { timeout: 5000 }).catch(() => {}); };
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

// 6. a Mac: navigator.platform says so. ⌘/ pressed for real, then a German Mac's ⌘⇧7 (key "/", code Digit7)
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
out.macDeSel = await select(mfr, cfg.second);
out.macDeKey = await dispatchKey(mfr, { key: "/", code: "Digit7", metaKey: true, shiftKey: true });
out.macDeOpened = await waitPop(mfr);
out.macDePop = await pop(mfr);
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
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
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
                       "passage": PASSAGE, "second": SECOND, "third": THIRD, "noneToast": NONE_TOAST,
                       "shots": os.environ.get("COMMENT_KEY_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
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
        # 6. a Mac: ⌘/, and a German Mac's ⌘⇧7; every hint says ⌘/
        self.assertTrue(r["macOpened"], "⌘/ on a Mac opened no comment box: %r" % r)
        self._anchored(r["macPop"], r["macSel"], "a Mac's ⌘/")
        self.assertEqual(r["macDeKey"]["platform"], "MacIntel", r["macDeKey"])
        self.assertTrue(r["macDeKey"]["prevented"], "a German Mac's ⌘/ was taken: %r" % r["macDeKey"])
        self.assertTrue(r["macDeOpened"], "a German Mac's ⌘/ (⌘⇧7) opened no comment box: %r" % r)
        self._anchored(r["macDePop"], r["macDeSel"], "a German Mac's ⌘/")
        self.assertEqual({m["label"]: m["sub"] for m in r["macMenu"]}.get("Comment"), "⌘/", r["macMenu"])
        self.assertEqual(r["macPalRow"], {"title": "Comment on selection", "kbd": "⌘/"}, r["macPalRow"])
        self.assertEqual(r["macDialogChip"], "⌘/", r["macDialogChip"])
        # 7. a Mac with ⌘⌥M bound in its place: spelled as µ with code KeyM, it still matches
        self.assertTrue(r["boundKey"]["prevented"], "the bound ⌘⌥M was taken on a Mac: %r" % r["boundKey"])
        self.assertTrue(r["boundOpened"], "a bound ⌘⌥M on a Mac opened no comment box: %r" % r)
        self._anchored(r["boundPop"], r["boundSel"], "a Mac's bound ⌘⌥M")


if __name__ == "__main__":
    unittest.main()
