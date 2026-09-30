#!/usr/bin/env python3
"""A file opened from the viewer as a TAB of the chat strip (the user 2026-09-30, who liked the file viewer and wanted a way
to keep several files open as tabs of their own, beside the sessions, to flip between them and the sessions).

This lab drives one hermetic kernel's real /chat page (the viewer, the strip and the store are the product's own; nothing
is stubbed) from three browser contexts, separate localStorage each, so they are three devices and not three tabs sharing
a store: the desktop the gestures happen on, a second desktop, and a phone (a coarse pointer at 390 px, the kernel's own
media rule). In one driver run:
  1. the desktop opens web, clicks a file path in its transcript, and presses the viewer's pop-out: the modal closes and a
     file tab sits right after web, inside web's tag group, showing the file (the pane in file mode: no message box);
  2. back to web (the transcript and the box return) and back to the file: the file is fetched again;
  3. the same path opened again (link, pop-out) activates the same tab, never a second one;
  4. the keyboard walks the file tab like a tab: → from it lands on the session after it, ← comes back to it;
  5. the second desktop and the phone, without a reload, list the file tab (the kernel's copy, pushed): the strip and the
     phone's session picker;
  6. the ✕ closes it on every device; reopened, a reload brings the pane back up on it, and a middle-click closes it.
The kernel's store (view-files.json) is read after each step. FILE_TABS_SHOTS=<dir> also writes the screenshots the
change was reviewed by, dark and Yatharth light. Skips LOUDLY without the extension deps or a Playwright browser. The lab
kernel never reaches the machine's user manager (a shadowed systemctl and systemd-run). SYNTHETIC fixtures only (the
notes-api demo world: web, api, tests; host TESTHOST; placeholder sids)."""
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

NAMES = ["web", "api", "tests"]
SIDS = {n: "%s-5555-4666-8777-000000000000" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
NAME_OF = {v: k for k, v in SIDS.items()}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e")]
TAGS = [{"id": "tag-infra", "name": "infra", "color": "#4EC9B0", "members": [SIDS["web"], SIDS["api"]]}]
GUIDE = "# Notes API guide\n\nThe web session keeps the notes-api tidy.\n\n## Fold rules\n\nOne rule per line.\n"
APP_PY = "def notes():\n    return [\"a\", \"b\"]\n\n\nif __name__ == \"__main__\":\n    print(notes())\n"
TEST_PY = "from src.app import notes\n\n\ndef test_notes():\n    assert notes() == [\"a\", \"b\"]\n"
REPLIES = {"web": "Start with docs/guide.md, then the handler in src/app.py.",
           "api": "The api side reads src/app.py too.",
           "tests": "The suite lives in tests/test_app.py."}


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
const out = { steps: [], errors: [] };
const T = 20000;
const finish = async (why) => { if (why) out.died = why; fs.writeFileSync(cfg.out, JSON.stringify(out)); fs.writeSync(1, "RESULT:" + cfg.out + "\n"); await browser.close(); process.exit(0); };
const wait = (page, fn, arg, why) => page.waitForFunction(fn, arg, { timeout: T }).catch(async (e) => { await finish(why + " (" + String(e).split("\n")[0] + ")"); });
const store = () => { try { return JSON.parse(fs.readFileSync(cfg.filesFile, "utf8")); } catch (e) { return null; } };
const THEMES = { dark: "yatharth", light: "yatharth-light", classic: "classic" };
async function open(theme, phone) {
  const ctx = await browser.newContext(phone ? { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true, deviceScaleFactor: 3 }
                                             : { viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 });
  await ctx.addInitScript((t) => { try { if (!localStorage.getItem("romp:settings")) localStorage.setItem("romp:settings", JSON.stringify({ theme: t })); } catch (e) {} }, THEMES[theme]);
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push(String(e && e.stack || e).slice(0, 500)));
  page.__files = 0;
  page.on("request", (r) => { if (/\/file\?path=/.test(r.url()) && !/download=1/.test(r.url())) page.__files++; });
  await page.goto(cfg.chat);
  await wait(page, (n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-file)").length >= n, cfg.count, "the strip never showed every session");
  await wait(page, () => typeof window.__rompPublishViewFiles === "function", null, "a page never heard the kernel's file tabs");
  return page;
}
// the strip as the user reads it: headers, sessions by name, file tabs by their label, the selected one marked
const readStrip = (page) => page.evaluate((names) => Array.from(document.getElementById("tabs").children).map((el) => {
  if (el.matches(".tab-group-head[data-group]")) return "g:" + el.dataset.group;
  if (el.matches(".tab-group-sep, .tab-group-break.tab-group-sep")) return "sep";
  if (el.matches(".tab.tab-file[data-id]")) return "f:" + (el.querySelector(".tab-label") || {}).textContent.replace("×", "") + (el.classList.contains("active") ? "(active)" : "");
  if (el.matches(".tab[data-id]:not(.tab-away)")) return "t:" + (names[el.dataset.id] || el.dataset.id) + (el.classList.contains("active") ? "(active)" : "");
  return null; }).filter(Boolean), cfg.names);
const pane = (page) => page.evaluate(() => ({
  fileMode: document.body.classList.contains("filetab-mode"),
  modal: !!document.getElementById("romp-fileview"),
  fileText: ((document.querySelector("#filetab-host .fileview-inline .fileview-body") || {}).textContent || "").slice(0, 400),
  hostShown: (() => { const h = document.getElementById("filetab-host"); return !!h && getComputedStyle(h).display !== "none" && h.getBoundingClientRect().height > 100; })(),
  footerShown: (() => { const f = document.getElementById("footer"); return !!f && getComputedStyle(f).display !== "none"; })(),
  contentShown: (() => { const c = document.getElementById("content"); return !!c && getComputedStyle(c).display !== "none"; })(),
  tabTitle: ((document.querySelector("#tabs .tab-file") || {}).title || ""),
  fileTabs: Array.from(document.querySelectorAll("#tabs .tab-file[data-id]")).map((t) => t.dataset.id),
  barName: ((document.querySelector("#filetab-host .fileview-name") || {}).title || ""),
  reload: !!document.querySelector("#filetab-host .fileview-reload"),
  popout: !!document.querySelector("#filetab-host .fileview-popout"),
}));
const openLink = async (page, sid, path) => {
  await page.click('#tabs .tab[data-id="' + sid + '"]');
  await wait(page, (p) => !!document.querySelector('#content .file-uri-link[data-path="' + p + '"]'), path, "the transcript never linked " + path);
  await page.click('#content .file-uri-link[data-path="' + path + '"]');
  await wait(page, () => !!document.querySelector("#romp-fileview .fileview-popout"), null, "the viewer never offered its pop-out");
};
const popOut = async (page) => {
  await page.click("#romp-fileview .fileview-popout");
  await wait(page, () => !document.getElementById("romp-fileview") && document.body.classList.contains("filetab-mode"), null, "the pop-out never made a tab");
  await wait(page, () => /\S/.test((document.querySelector("#filetab-host .fileview-body") || {}).textContent || "") && !document.querySelector("#filetab-host .fileview-load"), null, "the tab never showed the file");
};
async function run() {
const page = await open("classic", false);

// 1. the pop-out, from a path in web's transcript
await openLink(page, cfg.web, "docs/guide.md");
out.modal = await page.evaluate(() => { const b = document.querySelector("#romp-fileview .fileview-popout"); return { title: b.title, label: b.getAttribute("aria-label"), inFileGroup: !!b.closest(".fileview-group-file") }; });
await popOut(page);
out.opened = { strip: await readStrip(page), pane: await pane(page), store: store() };

// 2. to the session and back: the transcript returns, and the file is fetched again
await page.click('#tabs .tab[data-id="' + cfg.web + '"]');
await wait(page, () => !document.body.classList.contains("filetab-mode"), null, "the session never took the pane back");
out.backToWeb = { strip: await readStrip(page), pane: await pane(page) };
const before = page.__files;
await page.click("#tabs .tab-file");
await wait(page, () => document.body.classList.contains("filetab-mode") && /Notes API guide/.test((document.querySelector("#filetab-host .fileview-body") || {}).textContent || ""), null, "the file tab never came back");
out.refetch = { requests: page.__files - before, pane: await pane(page) };

// 3. the same path again: the same tab
await openLink(page, cfg.web, "docs/guide.md");
await popOut(page);
out.again = { strip: await readStrip(page), pane: await pane(page), store: store() };

// 4. the keyboard: → from the file tab to the session after it, ← back (the window's bare arrows)
await page.evaluate(() => { document.activeElement && document.activeElement.blur && document.activeElement.blur(); });
await page.keyboard.press("ArrowRight");
await wait(page, () => !document.body.classList.contains("filetab-mode"), null, "→ never left the file tab");
out.right = await readStrip(page);
await page.keyboard.press("ArrowLeft");
await wait(page, () => document.body.classList.contains("filetab-mode"), null, "← never came back to the file tab");
out.left = await readStrip(page);

// a second file, from tests: beside tests, in the untagged trail
await openLink(page, cfg.tests, "tests/test_app.py");
await popOut(page);
out.second = { strip: await readStrip(page), store: store() };

// 5. the other devices, without a reload
const other = await open("classic", false);
await wait(other, () => document.querySelectorAll("#tabs .tab-file").length === 2, null, "the second desktop never listed the file tabs");
await other.evaluate(() => { window.__rompFileMark = 1; });
out.other = { strip: await readStrip(other) };
const phone = await open("classic", true);
await wait(phone, () => document.querySelectorAll("#tabs .tab-file").length === 2, null, "the phone never listed the file tabs");
await phone.tap("#mcur");
await wait(phone, () => !!document.querySelector("#mlist.open") && document.querySelectorAll("#mlist .mrow").length >= 5, null, "the phone picker never listed the file tabs");
out.phone = await phone.evaluate(() => Array.from(document.getElementById("mlist").children).map((r) => (r.matches(".mhead") ? "g:" + r.dataset.group : r.matches(".msep") ? "sep" : "r:" + r.querySelector(".nm").textContent + (r.querySelector(".mclose") ? "|" + r.querySelector(".mclose").title : ""))));
// a tap on the file tab's row opens it on the phone
await phone.tap('#mlist .mrow[data-id="' + out.second.store.tabs[1].id + '"]');
await wait(phone, () => document.body.classList.contains("filetab-mode"), null, "the phone never opened the file tab");
out.phoneOpen = { pane: await pane(phone), current: await phone.evaluate(() => (document.querySelector("#mcur .nm") || {}).textContent || "") };

// 6. the ✕ on the open tab (test_app.py, the one the pane shows) closes it everywhere and gives the pane back
const guideId = out.opened.store.tabs[0].id, testId = out.second.store.tabs[1].id;
await page.hover('#tabs .tab-file[data-id="' + testId + '"]');
await page.click('#tabs .tab-file[data-id="' + testId + '"] .tab-close');
await wait(page, (id) => !document.querySelector('#tabs .tab-file[data-id="' + id + '"]'), testId, "the ✕ never closed the tab");
await wait(other, (id) => !document.querySelector('#tabs .tab-file[data-id="' + id + '"]'), testId, "the second desktop never dropped the closed tab");
out.closed = { strip: await readStrip(page), pane: await pane(page), other: await readStrip(other), otherFresh: await other.evaluate(() => window.__rompFileMark === 1), store: store() };
// the other file tab, shown, then a reload: the pane comes back up on it
await page.click('#tabs .tab-file[data-id="' + guideId + '"]');
await wait(page, () => document.body.classList.contains("filetab-mode"), null, "the guide's tab never showed");
await page.reload();
await wait(page, () => document.body.classList.contains("filetab-mode") && /Notes API guide/.test((document.querySelector("#filetab-host .fileview-body") || {}).textContent || ""), null, "the reload never brought the file tab back up");
out.reloaded = { strip: await readStrip(page), pane: await pane(page) };
// …and a middle click closes it
await page.click('#tabs .tab-file[data-id="' + guideId + '"]', { button: "middle" });
await wait(page, (id) => !document.querySelector('#tabs .tab-file[data-id="' + id + '"]'), guideId, "a middle click never closed the tab");
out.middle = { strip: await readStrip(page), pane: await pane(page), store: store() };

// 7. from a pane with no strip of its own: the Files pane of the shell. A path clicked in the chat opens there (the open
// pane takes it); its viewer's pop-out goes up to the shell, which hands it to the chat, and the tab opens beside api
const shellCtx = await browser.newContext({ viewport: { width: 1600, height: 900 } });
await shellCtx.addInitScript(() => { if (self !== top) return; localStorage.setItem("romp:settings", JSON.stringify({ theme: "classic", showFilesControl: true })); });
const shell = await shellCtx.newPage();
shell.on("pageerror", (e) => out.errors.push("shell: " + String(e && e.stack || e).slice(0, 500)));
await shell.goto(cfg.shell);
await wait(shell, () => !!document.getElementById("f-chat"), null, "the shell never mounted the chat pane");
await shell.evaluate(() => { window.__rompPaneToggle && window.__rompPaneToggle("files", true); });
const frameOf = (part) => shell.frames().find((f) => f.url().includes(part));
await wait(shell, () => { const f = document.getElementById("f-files"); try { return !!f && !!f.contentDocument && f.contentDocument.readyState === "complete" && !!f.contentDocument.getElementById("files-empty"); } catch (e) { return false; } }, null, "the Files pane never loaded");
const chatF = frameOf("/chat");
await wait(chatF, (n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-file)").length >= n, cfg.count, "the shell's chat never showed every session");
await chatF.click('#tabs .tab[data-id="' + cfg.api + '"]');
await wait(chatF, () => !!document.querySelector('#content .file-uri-link[data-path="src/app.py"]'), null, "the shell's chat never linked src/app.py");
await chatF.click('#content .file-uri-link[data-path="src/app.py"]');
const filesF = frameOf("/files");
await wait(filesF, () => !!document.querySelector("#romp-fileview .fileview-popout"), null, "the Files pane's viewer never offered its pop-out");
out.filesPane = { chatModal: await chatF.evaluate(() => !!document.getElementById("romp-fileview")) };
await filesF.click("#romp-fileview .fileview-popout");
await wait(chatF, () => document.body.classList.contains("filetab-mode") && /def notes/.test((document.querySelector("#filetab-host .fileview-body") || {}).textContent || ""), null, "the shell never handed the pop-out to the chat");
out.filesPane.strip = await readStrip(chatF);
out.filesPane.viewerClosed = await filesF.evaluate(() => !document.getElementById("romp-fileview"));
out.filesPane.store = store();
await shellCtx.close();

// the screenshots the change was reviewed by (FILE_TABS_SHOTS): two file tabs beside sessions, one inside a tag group
if (cfg.shots) {
  fs.mkdirSync(cfg.shots, { recursive: true });
  await openLink(page, cfg.web, "docs/guide.md");
  await popOut(page);
  await openLink(page, cfg.api, "src/app.py");
  await popOut(page);
  await openLink(page, cfg.tests, "tests/test_app.py");
  await popOut(page);
  for (const theme of ["dark", "light", "classic"]) {
    const p = await open(theme, false);
    await wait(p, () => document.querySelectorAll("#tabs .tab-file").length === 3, null, "shots: the file tabs never showed");
    await p.click('#tabs .tab[data-id="' + cfg.web + '"]');
    await p.mouse.move(900, 600);
    await p.waitForTimeout(500);
    const bar = await p.$("#tabbar");
    await bar.screenshot({ path: cfg.shots + "/strip-" + theme + ".png" });
    await openLink(p, cfg.web, "docs/guide.md");
    await p.waitForTimeout(300);
    const vb = await (await p.$("#romp-fileview .fileview-bar")).boundingBox();
    await p.hover("#romp-fileview .fileview-popout");
    await p.waitForTimeout(700);
    await p.screenshot({ path: cfg.shots + "/popout-button-" + theme + ".png", clip: { x: Math.max(0, vb.x + vb.width - 520), y: Math.max(0, vb.y - 4), width: 520, height: vb.height + 40 } });
    await popOut(p);
    await p.mouse.move(640, 500);
    await p.waitForTimeout(300);
    await p.screenshot({ path: cfg.shots + "/file-tab-view-" + theme + ".png" });
    const ph = await open(theme, true);
    await wait(ph, () => document.querySelectorAll("#tabs .tab-file").length === 3, null, "shots: the phone never listed the file tabs");
    await ph.tap("#mcur");
    await wait(ph, () => !!document.querySelector("#mlist.open"), null, "shots: the phone picker never opened");
    await ph.waitForTimeout(300);
    await ph.screenshot({ path: cfg.shots + "/phone-picker-" + theme + ".png" });
    await p.context().close(); await ph.context().close();
  }
}
}
await run().then(() => finish(null), (e) => finish("an action failed: " + String(e && e.message || e).split("\n")[0]));
"""


class FileTabsServed(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="file-tabs-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
        fakebin = os.path.join(cls.lab, "fakebin")
        os.makedirs(fakebin)
        for tool, body in (("systemctl", "exit 0\n"), ("systemd-run", "echo 'no user manager in this lab' >&2\nexit 1\n")):
            Path(fakebin, tool).write_text("#!/bin/sh\n" + body)
            os.chmod(os.path.join(fakebin, tool), 0o755)
        for d in ("docs", "src", "tests"):
            os.makedirs(os.path.join(cwd, d), exist_ok=True)
        Path(cwd, "docs", "guide.md").write_text(GUIDE)
        Path(cwd, "src", "app.py").write_text(APP_PY)
        Path(cwd, "tests", "test_app.py").write_text(TEST_PY)
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        t0 = int(time.time()) - 900
        for i, name in enumerate(NAMES):
            sid = SIDS[name]
            bg, fg = PALETTE[i % len(PALETTE)]
            Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            Path(state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
                 "model": "claude-opus-5", "liveModel": "Opus 5"}))
            recs = [{"type": "user", "timestamp": iso(t0 + i), "uuid": "u1", "parentUuid": None, "promptSource": "typed", "sessionId": sid,
                     "message": {"role": "user", "content": "where does the %s side of notes-api start?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": REPLIES[name]}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            os.utime(Path(proj, sid + ".jsonl"), (t0 + i, t0 + i))
        Path(state, "timeline-views.json").write_text(json.dumps({"active": "all", "tags": TAGS, "tagOrder": ["infra"]}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.state = state
        cls.port, cls.token = _free_port(), "testtok-filetabs"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", ROMP_CLI_SCOPE="0")
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
            k.kill(); k.wait()   # the exact process this class started, and nothing else
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
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": len(NAMES), "out": out,
                       "web": SIDS["web"], "api": SIDS["api"], "tests": SIDS["tests"], "names": NAME_OF,
                       "filesFile": os.path.join(cls.state, "view-files.json"),
                       "shell": "http://127.0.0.1:%d/?token=%s" % (cls.port, cls.token), "shots": os.environ.get("FILE_TABS_SHOTS") or ""}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        result = json.loads(Path(out).read_text())
        if result.get("died"):
            raise AssertionError("driver stopped: %s\nso far: %s\nkernel:\n%s" % (result["died"], json.dumps(result)[-3000:], open(cls.klog).read()[-1500:]))
        return result

    # ── 1: the pop-out ──
    def test_the_viewer_offers_the_pop_out_in_its_file_group(self):
        m = self._run()["modal"]
        self.assertEqual((m["title"], m["label"], m["inFileGroup"]), ("Open as a tab", "Open as a tab", True))

    def test_the_pop_out_puts_a_file_tab_right_after_its_session_inside_its_group_showing_the_file(self):
        r = self._run()["opened"]
        strip = r["strip"]
        self.assertEqual(strip[strip.index("t:web") + 1], "f:guide.md(active)", "right after web, and the one selected: %s" % strip)
        self.assertLess(strip.index("t:web"), strip.index("sep"), "…inside web's tag group (infra), before the untagged trail: %s" % strip)
        self.assertEqual(strip[0], "g:infra")
        self.assertTrue(r["pane"]["fileMode"] and r["pane"]["hostShown"] and not r["pane"]["modal"], r["pane"])
        self.assertIn("Notes API guide", r["pane"]["fileText"], "the file, rendered, in the pane")
        self.assertFalse(r["pane"]["footerShown"], "no message box: there is no session to send to")
        self.assertFalse(r["pane"]["contentShown"], "the transcript is not on screen under the file")
        self.assertIn("docs/guide.md", r["pane"]["tabTitle"], "the tooltip names the path")
        self.assertIn("Opened from web", r["pane"]["tabTitle"], "…and the session it came from")
        self.assertTrue(r["pane"]["reload"] and not r["pane"]["popout"], "the tab's bar: Reload, and no pop-out of its own")
        tabs = r["store"]["tabs"]
        self.assertEqual([(t["path"], t["sid"], t["anchor"]) for t in tabs], [("docs/guide.md", SIDS["web"], SIDS["web"])], "the kernel keeps it")

    # ── 2: the session and back ──
    def test_the_session_takes_the_pane_back_and_the_file_is_fetched_again_on_return(self):
        r = self._run()
        strip = r["backToWeb"]["strip"]
        self.assertEqual(strip[strip.index("t:web(active)") + 1], "f:guide.md", strip)
        self.assertTrue(r["backToWeb"]["pane"]["contentShown"] and r["backToWeb"]["pane"]["footerShown"], "the transcript and the box are back")
        self.assertGreaterEqual(r["refetch"]["requests"], 1, "the file was read again when its tab came back")

    # ── 3: the same path again ──
    def test_the_same_path_opened_again_is_the_same_tab(self):
        r = self._run()["again"]
        self.assertEqual([t for t in r["strip"] if t.startswith("f:")], ["f:guide.md(active)"], r["strip"])
        self.assertEqual(len(r["store"]["tabs"]), 1)

    # ── 4: the keyboard ──
    def test_the_arrows_walk_the_file_tab_like_a_tab(self):
        r = self._run()
        strip = r["again"]["strip"]
        at = strip.index("f:guide.md(active)")
        after = next(t for t in strip[at + 1:] if t.startswith("t:"))   # the session the strip shows next, whatever the kernel's order
        self.assertIn(after + "(active)", r["right"], "→ from the file tab: the session after it on the strip, %s" % r["right"])
        self.assertIn("f:guide.md(active)", r["left"], "← back onto the file tab")

    def test_a_second_file_sits_beside_its_own_session(self):
        r = self._run()["second"]
        strip = r["strip"]
        self.assertEqual(strip[strip.index("t:tests"):][:2], ["t:tests", "f:test_app.py(active)"], strip)
        self.assertEqual(len(r["store"]["tabs"]), 2)

    # ── 5: every device ──
    def test_another_device_and_the_phone_list_the_file_tabs(self):
        r = self._run()
        self.assertEqual([t for t in r["other"]["strip"] if t.startswith("f:")], ["f:guide.md", "f:test_app.py"], r["other"]["strip"])
        rows = r["phone"]
        self.assertIn("r:guide.md|Close tab", rows, "the phone's picker lists the file tab, closable as a tab: %s" % rows)
        self.assertIn("r:test_app.py|Close tab", rows)
        self.assertEqual(rows.index("r:guide.md|Close tab"), rows.index("r:web|End session") + 1, "in the strip's order: right after web")
        self.assertTrue(r["phoneOpen"]["pane"]["fileMode"], "a tap on its row opens it")
        self.assertEqual(r["phoneOpen"]["current"].strip(), "test_app.py", "the picker's chip names the file tab being shown")

    # ── 6: close, reload, middle click ──
    def test_the_close_reaches_every_device_and_a_reload_restores_the_open_tab(self):
        r = self._run()
        self.assertNotIn("f:test_app.py", " ".join(r["closed"]["strip"]))
        self.assertNotIn("f:test_app.py", " ".join(r["closed"]["other"]))
        self.assertTrue(r["closed"]["otherFresh"], "the other desktop followed without a reload")
        self.assertEqual([t["path"] for t in r["closed"]["store"]["tabs"]], ["docs/guide.md"])
        self.assertFalse(r["closed"]["pane"]["fileMode"], "the pane gave the file up and the transcript came back")
        self.assertTrue(r["closed"]["pane"]["contentShown"] and r["closed"]["pane"]["footerShown"])
        self.assertIn("f:guide.md(active)", r["reloaded"]["strip"], "after the reload the pane is back on the file tab")
        self.assertTrue(r["reloaded"]["pane"]["fileMode"] and "Notes API guide" in r["reloaded"]["pane"]["fileText"])
        self.assertNotIn("f:guide.md", " ".join(r["middle"]["strip"]), "a middle click closed it")
        self.assertFalse(r["middle"]["pane"]["fileMode"])

    # ── 7: a pane with no strip ──
    def test_the_files_panes_pop_out_opens_the_tab_in_the_chat(self):
        r = self._run()["filesPane"]
        self.assertFalse(r["chatModal"], "the open Files pane took the click: no modal over the chat")
        strip = r["strip"]
        self.assertEqual(strip[strip.index("t:api") + 1], "f:app.py(active)", "beside api, the session the file came from: %s" % strip)
        self.assertTrue(r["viewerClosed"], "the Files pane's viewer closed on the pop-out")
        self.assertEqual([(t["path"], t["sid"]) for t in r["store"]["tabs"]], [("src/app.py", SIDS["api"])])

    def test_no_page_error(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
