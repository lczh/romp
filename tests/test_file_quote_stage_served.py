#!/usr/bin/env python3
"""Quote a file, stage it, send it (the user 2026-09-30, who wanted the chat's select-and-stage flow in the file viewer).

The viewer's message bar, driven on the real /chat page of a hermetic kernel (the lab world test_file_tabs_served.py
builds: the notes-api sessions web, api and tests, their files on disk, the path links in their transcripts):
  1. web's transcript opens src/app.py in the viewer (the modal): the bar at its foot says "to web";
  2. a mouse drag over the code view's first two rows makes a quote chip there, labelled src/app.py:1-2;
  3. typed words and Ctrl+Enter stage it: the chat's own staged strip holds it, on web's stack;
  4. docs/guide.md, popped out as a FILE TAB, has the bar too; a drag over a sentence of the RENDERED document quotes it
     without lines; typed words and Enter send: one message to web, the staged file quote first and this one after it,
     each with its lead-in, and the kernel's socket receives exactly that body.
FILE_QUOTE_SHOTS=<dir> writes quote-*.png (the bar with a chip, the chat's staged strip), dark and Yatharth light. Skips
LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, HERE)
import test_file_tabs_served as ft   # noqa: E402  the lab world and its kernel (setUpClass/tearDownClass are that class's)

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const out = { errors: [], sent: [] };
const T = 20000;
const finish = async (why) => { if (why) out.died = why; fs.writeFileSync(cfg.out, JSON.stringify(out)); fs.writeSync(1, "RESULT:" + cfg.out + "\n"); await browser.close(); process.exit(0); };
const wait = (page, fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await finish(why + " (" + String(e).split("\n")[0] + ")"); });
const THEMES = { dark: "yatharth", light: "yatharth-light", classic: "classic" };
async function open(theme) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
  await ctx.addInitScript((t) => { try { if (!localStorage.getItem("romp:settings")) localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, THEMES[theme]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(String(e && e.stack || e).slice(0, 500)));
  // what the kernel is sent: the page's own socket frames
  page.on("websocket", (ws) => ws.on("framesent", (f) => { try { const m = JSON.parse(f.payload); if (m && m.type === "sendMessage") out.sent.push({ id: m.id, text: m.text }); } catch (e) {} }));
  await page.goto(cfg.chat);
  await wait(page, (n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-file)").length >= n, cfg.count, "the strip never showed every session");
  await wait(page, () => typeof window.__rompPublishViewFiles === "function", null, "the page never heard the kernel's store");
  return page;
}
const openLink = async (page, sid, p) => {
  await page.click('#tabs .tab[data-id="' + sid + '"]');
  await wait(page, (x) => !!document.querySelector('#content .file-uri-link[data-path="' + x + '"]'), p, "the transcript never linked " + p);
  await page.click('#content .file-uri-link[data-path="' + p + '"]:visible');   // the shown transcript's (a prebuilt hidden view links it too)
  await wait(page, () => !!document.querySelector("#romp-fileview .fileview-compose") && !document.querySelector("#romp-fileview .fileview-load"), null, "the viewer never showed its bar");
};
// a real drag from the start of one element's text to the end of another's (the mouse's own selection)
const drag = async (page, fromSel, toSel) => {
  const a = await (await page.$(fromSel)).boundingBox(), b = await (await page.$(toSel)).boundingBox();
  await page.mouse.move(a.x + 1, a.y + a.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 1, b.y + b.height / 2, { steps: 8 });
  await page.mouse.up();
};
const bar = (page, scope) => page.evaluate((s) => {
  const root = document.querySelector(s + " .fileview-compose");
  if (!root) return null;
  return { to: (root.querySelector(".fv-compose-to") || {}).textContent || "", chips: Array.from(root.querySelectorAll(".fv-compose-chips .composer-chip-label")).map((c) => c.textContent),
           note: (root.querySelector(".fv-compose-note") || {}).textContent || "", value: (root.querySelector(".fv-compose-input") || {}).value || "", hidden: root.hidden };
}, scope);
const strip = (page) => page.evaluate(() => { const s = document.getElementById("composer-staged");
  return { shown: !!s && s.style.display !== "none", sid: s ? s.dataset.sid || null : null, items: s ? Array.from(s.querySelectorAll(".staged-chip")).map((c) => c.textContent) : [] }; });
async function run() {
const page = await open("classic");

// 1–3: the modal, a chip from two code rows, staged
await openLink(page, cfg.web, "src/app.py");
out.modalBar = await bar(page, "#romp-fileview");
await drag(page, "#romp-fileview code.hljs .fv-cl:nth-child(1) .fv-ct", "#romp-fileview code.hljs .fv-cl:nth-child(2) .fv-ct");
await wait(page, () => document.querySelectorAll("#romp-fileview .fv-compose-chips .composer-chip").length === 1, null, "the drag never made a chip");
out.chipped = await bar(page, "#romp-fileview");
if (cfg.shots) { fs.mkdirSync(cfg.shots, { recursive: true }); }
await page.click("#romp-fileview .fv-compose-input");
await page.keyboard.type("Why a list here?");
if (cfg.shots) { const b = await (await page.$("#romp-fileview .fileview")).boundingBox(); await page.screenshot({ path: cfg.shots + "/quote-bar-classic.png", clip: { x: b.x, y: b.y + b.height - 160, width: b.width, height: 160 } }); }
await page.keyboard.press("Control+Enter");
await wait(page, () => { const s = document.getElementById("composer-staged"); return !!s && s.querySelectorAll(".staged-chip").length === 1; }, null, "Ctrl+Enter never staged onto the chat's strip");
out.staged = { bar: await bar(page, "#romp-fileview"), strip: await strip(page) };
await page.click("#romp-fileview .fileview-close");
await wait(page, () => !document.getElementById("romp-fileview"), null, "the viewer never closed");
out.afterClose = await strip(page);
if (cfg.shots) await (await page.$("#footer")).screenshot({ path: cfg.shots + "/quote-staged-strip-classic.png" });

// 4: a file TAB of the rendered guide; a sentence of the rendered text, no lines; Enter sends the lot
await openLink(page, cfg.web, "docs/guide.md");
await page.click("#romp-fileview .fileview-popout");
await wait(page, () => document.body.classList.contains("filetab-mode") && !!document.querySelector("#filetab-host .fileview-compose") && !!document.querySelector("#filetab-host .fileview-md p"), null, "the file tab never showed its bar");
out.tabBar = await bar(page, "#filetab-host");
await drag(page, "#filetab-host .fileview-md h2 + p", "#filetab-host .fileview-md h2 + p");
await wait(page, () => document.querySelectorAll("#filetab-host .fv-compose-chips .composer-chip").length === 1, null, "the rendered drag never made a chip");
out.tabChip = await bar(page, "#filetab-host");
await page.click("#filetab-host .fv-compose-input");
await page.keyboard.type("Is that a hard rule?");
await page.keyboard.press("Enter");
await wait(page, () => /Sent to/.test((document.querySelector("#filetab-host .fv-compose-note") || {}).textContent || ""), null, "Enter never sent");
await page.waitForTimeout(300);
out.afterSend = { bar: await bar(page, "#filetab-host"), strip: await strip(page) };

// the screenshots in the two themes the change is reviewed in: the bar with a chip, and the chat's strip holding it
if (cfg.shots) {
  for (const theme of ["dark", "light"]) {
    const p = await open(theme);
    await openLink(p, cfg.api, "src/app.py");
    await drag(p, "#romp-fileview code.hljs .fv-cl:nth-child(1) .fv-ct", "#romp-fileview code.hljs .fv-cl:nth-child(2) .fv-ct");
    await wait(p, () => document.querySelectorAll("#romp-fileview .fv-compose-chips .composer-chip").length === 1, null, "shots: no chip");
    await p.click("#romp-fileview .fv-compose-input");
    await p.keyboard.type("Why a list here?");
    const b = await (await p.$("#romp-fileview .fileview")).boundingBox();
    await p.screenshot({ path: cfg.shots + "/quote-bar-" + theme + ".png", clip: { x: b.x, y: b.y + b.height - 160, width: b.width, height: 160 } });
    await p.keyboard.press("Control+Enter");
    await wait(p, () => { const s = document.getElementById("composer-staged"); return !!s && s.querySelectorAll(".staged-chip").length >= 1; }, null, "shots: not staged");
    await p.click("#romp-fileview .fileview-close");
    await p.waitForTimeout(300);
    await (await p.$("#footer")).screenshot({ path: cfg.shots + "/quote-staged-strip-" + theme + ".png" });
    await p.context().close();
  }
}
}
await run().then(() => finish(null), (e) => finish("an action failed: " + String(e && e.message || e).split("\n")[0]));
"""


class FileQuoteStageServed(unittest.TestCase):
    maxDiff = None
    result = None

    # the lab world and its kernel are test_file_tabs_served.py's, borrowed whole (its boot, its teardown)
    _boot = classmethod(ft.FileTabsServed._boot.__func__)
    setUpClass = classmethod(ft.FileTabsServed.setUpClass.__func__)
    tearDownClass = classmethod(ft.FileTabsServed.tearDownClass.__func__)

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
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(ft.NAMES), "out": out,
                       "web": ft.SIDS["web"], "api": ft.SIDS["api"], "shots": os.environ.get("FILE_QUOTE_SHOTS") or ""}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(ft.EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        result = json.loads(Path(out).read_text())
        if result.get("died"):
            raise AssertionError("driver stopped: %s\nso far: %s\nkernel:\n%s" % (result["died"], json.dumps(result)[-3000:], open(cls.klog).read()[-1500:]))
        return result

    def test_the_viewer_has_a_bar_that_names_its_session(self):
        r = self._run()
        self.assertEqual(r["modalBar"]["to"], "to web")
        self.assertEqual(r["tabBar"]["to"], "to web", "a file tab's view has it too")

    def test_a_drag_over_two_code_rows_makes_a_chip_cited_to_the_file_and_its_lines(self):
        chips = self._run()["chipped"]["chips"]
        self.assertEqual(len(chips), 1)
        self.assertTrue(chips[0].startswith("src/app.py:1-2 — def notes():"), chips[0])

    def test_ctrl_enter_stages_it_on_the_sessions_own_stack_which_the_chats_strip_shows(self):
        r = self._run()
        self.assertEqual(r["staged"]["bar"]["chips"], [], "the chip moved onto the staged item")
        self.assertEqual(r["staged"]["bar"]["value"], "", "the box cleared")
        self.assertTrue(r["staged"]["bar"]["note"].startswith("1 staged"), r["staged"]["bar"]["note"])
        s = r["afterClose"]
        self.assertTrue(s["shown"] and s["sid"] == ft.SIDS["web"], s)
        self.assertEqual(len(s["items"]), 1)
        self.assertIn("Why a list here?", s["items"][0])
        self.assertIn("def notes():", s["items"][0], "the quote rides the staged item")

    def test_a_rendered_documents_passage_quotes_without_lines(self):
        chips = self._run()["tabChip"]["chips"]
        self.assertEqual(len(chips), 1)
        self.assertTrue(chips[0].startswith("docs/guide.md — One rule per line."), chips[0])

    def test_enter_sends_the_staged_file_quote_and_this_one_as_one_message_with_their_lead_ins(self):
        r = self._run()
        self.assertEqual(r["sent"], [{"id": ft.SIDS["web"], "text":
            "Replying to this part of src/app.py (lines 1–2):\n> def notes():\n>     return [\"a\", \"b\"]\n\nWhy a list here?\n\n"
            "Replying to this part of docs/guide.md:\n> One rule per line.\n\nIs that a hard rule?"}],
            "the kernel's socket received exactly that body, to web")
        self.assertFalse(r["afterSend"]["strip"]["items"], "the stack released")
        self.assertEqual(r["afterSend"]["bar"]["note"], "Sent to web.")

    def test_no_page_error(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
