#!/usr/bin/env python3
"""The tag menu on the real page (the user 2026-10-01, who wanted to right-click a tag and get a menu to rename it,
delete it and so on).

This lab drives the /chat page of a hermetic kernel with real right-clicks and keys, and a SECOND client, another
browser context on the same kernel, watching. The fixture is the notes-api demo world: `qa` (tests, docs), `infra`
(web, api, auth) and `release` (deploy, cache), dragged into the order qa, infra, release, with infra and release
folded (the kernel's view-folds.json, the folds every device shares), and search in no tag. Then:
  - right-click infra's strip header: the menu names the tag (its chip), offers Rename…, the palette's swatches,
    Open group (the header renders folded), Show overview (the section at a glance, which a header's click no longer
    opens since 2026-10-02), Show only this tag, Configure tags… and Delete tag… ("3 sessions lose it");
  - Rename… turns the header's chip into a field holding the name; "platform" and Enter rename the tag: the kernel's
    store keeps its id, its three sessions and its place in the order (qa, platform, release), both pages draw a folded
    platform header counting three and no infra header, the page posted ONE targeted rename (no order write: the kernel
    carries it, `tagNameCarry`), and the shared folds name platform, not infra;
  - Delete tag… on release asks first ("2 sessions lose this tag"); confirmed, the tag is gone from the store and the
    order, its sessions are in no tag and drawn on the ungrouped row by both pages, release's fold is gone from the shared
    folds, and the page posted ONE targeted delete;
  - Open group on platform opens it on both pages (the folds are shared), its three sessions drawn under it;
  - from a chip of the tab menu's Tags flyout the same menu opens, without the fold row, and its Rename… asks in the
    prompt (the chip has no header to edit in); Escape leaves everything as it was.
The light theme (the `theme` key of romp:settings, as the user sets it) opens the menu and the inline field on a fresh
pair and cancels with Escape. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only
(notes-api names, host TESTHOST, placeholder sids). TAG_MENU_SHOTS=<dir> writes the menu, the inline rename, the prompt
and the delete's question, per theme."""
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

NAMES = ["web", "api", "auth", "cache", "tests", "docs", "deploy", "search"]
SIDS = {n: "%s-1111-2222-3333-444444444444" % (chr(ord("a") + i) * 8) for i, n in enumerate(NAMES)}
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e")]
TAGS = [{"id": "tag-qa", "name": "qa", "color": "#c98cff", "members": [SIDS[n] for n in ("tests", "docs")]},
        {"id": "tag-infra", "name": "infra", "color": "#4EC9B0", "members": [SIDS[n] for n in ("web", "api", "auth")]},
        {"id": "tag-release", "name": "release", "color": "#e5a50a", "members": [SIDS[n] for n in ("deploy", "cache")]}]
ORDER = ["qa", "infra", "release"]
FOLDS = {"collapsed": ["infra", "release"], "expanded": [], "pinned": []}
VISIBLE = 3   # tabs on the strip at load: qa's two and search (infra and release are folded)


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
const VIEW = { width: 1300, height: 640 };
const tapSends = () => {   // every frame the page SENDS its kernel, so the writes a gesture posts are read off the wire
  window.__sent = [];
  const orig = WebSocket.prototype.send;
  WebSocket.prototype.send = function (d) { try { if (typeof d === "string") window.__sent.push(JSON.parse(d)); } catch (e) {} return orig.call(this, d); };
};
const open = async (theme) => {
  const ctx = await browser.newContext({ viewport: VIEW });
  await ctx.addInitScript(tapSends);
  await ctx.addInitScript((t) => { try { let s = {}; try { s = JSON.parse(localStorage.getItem("romp:settings") || "{}") || {}; } catch (e) {}
    s.theme = t; localStorage.setItem("romp:settings", JSON.stringify(s)); } catch (e) {} }, theme);
  const page = await ctx.newPage();
  await page.goto(cfg.chat);
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.visible, { timeout: 30000 });
  await page.waitForSelector('#tabs .tab-group-head[data-group="qa"]', { timeout: 30000 });   // qa is never renamed here
  await page.waitForTimeout(800);
  return { ctx, page };
};
const readJSON = (p) => { try { return JSON.parse(fs.readFileSync(p, "utf8")); } catch (e) { return null; } };
const memberSid = (m) => (m && typeof m === "object" ? String(m.sid || "") : String(m == null ? "" : m));
const tagsOf = (sid) => { const v = readJSON(cfg.views); return v ? (v.tags || []).filter((t) => (t.members || []).some((m) => memberSid(m).endsWith(sid))).map((t) => t.name).sort() : null; };
const until = async (page, fn, n = 80) => { for (let i = 0; i < n; i++) { if (fn()) return true; await page.waitForTimeout(100); } return fn(); };
const strip = (page) => page.evaluate(() => ({
  theme: document.body.classList.contains("theme-light") ? "light" : "dark",
  heads: Array.from(document.querySelectorAll("#tabs .tab-group-head[data-group]")).map((h) => ({ group: h.dataset.group, folded: h.classList.contains("collapsed"),
    count: (h.querySelector(".tab-group-count") || {}).textContent || "", chip: (h.querySelector(".tab-group-chip") || {}).textContent || "" })),
  tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => ({ id: t.dataset.id, copy: t.dataset.copy === undefined ? null : t.dataset.copy })),
}));
const menu = (page) => page.evaluate(() => {
  const m = document.querySelector(".ctx-menu.ctx-tagmenu");
  if (!m) return null;
  return { chip: (m.querySelector(".ctx-tagmenu-head") || {}).textContent || "", swatches: m.querySelectorAll(".ctx-swatch").length,
           sel: m.querySelectorAll(".ctx-swatch.sel").length, z: getComputedStyle(m).zIndex,
           rows: Array.from(m.querySelectorAll(".ctx-item")).map((r) => ({ act: ((r.className.match(/ctx-tag-(\w+)/) || [])[1]) || "",
             label: (r.querySelector(".ctx-item-label") || r).textContent.trim(), sub: ((r.querySelector(".ctx-item-sub") || {}).textContent || "").trim(),
             danger: r.classList.contains("ctx-item-danger") })) };
});
// the strip and whatever floats over it (the menu, a dialog): the region a reader sees
const shot = async (page, name, sel) => {
  if (!cfg.shots) return;
  const box = await page.evaluate((s) => { const r = [document.getElementById("tabbar"), ...(s ? Array.from(document.querySelectorAll(s)) : [])]
    .filter(Boolean).map((e) => e.getBoundingClientRect());
    return { b: Math.max(...r.map((x) => x.bottom)), r: Math.max(...r.map((x) => x.right)) }; }, sel || "");
  await page.screenshot({ path: cfg.shots + "/" + name + ".png",
    clip: { x: 0, y: 0, width: Math.min(VIEW.width, Math.max(760, Math.round(box.r + 24))), height: Math.min(VIEW.height, Math.round(box.b + 24)) } });
};
const rightClickHead = async (page, g) => {
  const h = await page.$(`#tabs .tab-group-head[data-group="${g}"]`);
  if (!h) return false;
  await h.click({ button: "right" });
  await page.waitForSelector(".ctx-menu.ctx-tagmenu", { timeout: 5000 });
  return true;
};
const writes = (page) => page.evaluate(() => window.__sent.filter((m) => m && (m.type === "tagEdit" || m.type === "setTimelineViews" || m.type === "editTag")));
// the tab menu's Tags flyout on a session's tab, then a right-click on the row of the tag `g` the session holds
const chipMenu = async (page, sid, g) => {
  const tab = await page.$(`#tabs .tab[data-id="${sid}"]`);
  if (!tab) return "no tab";
  await tab.click({ button: "right" });
  await page.waitForSelector(".ctx-menu .ctx-item-tags", { timeout: 5000 });
  const item = await page.$(".ctx-menu .ctx-item-tags");
  const ib = await item.boundingBox();
  await page.mouse.move(ib.x + ib.width / 2, ib.y + ib.height / 2);
  try { await page.waitForSelector(".ctx-sub-tags", { timeout: 1500 }); } catch (e) { await item.click(); await page.waitForSelector(".ctx-sub-tags", { timeout: 5000 }); }
  const row = await page.evaluateHandle((n) => Array.from(document.querySelectorAll(".ctx-sub-tags > .ctx-item")).find((r) => (r.querySelector(".ctx-tag-chip") || {}).textContent === n) || null, g);
  const el = row.asElement();
  if (!el) return "no row";
  await el.click({ button: "right" });
  await page.waitForSelector(".ctx-menu.ctx-tagmenu", { timeout: 5000 });
  return "";
};
const out = {};

// ── DARK: the menu, the inline rename, the delete, the fold row, the chip's prompt ──
{
  const a = await open("yatharth"), b = await open("yatharth");
  const A = a.page, B = b.page;
  const d = out.dark = { theme: (await strip(A)).theme };
  d.opened = await rightClickHead(A, "infra");
  d.menu = await menu(A);
  await shot(A, "tag-menu-dark", ".ctx-menu.ctx-tagmenu");
  await A.evaluate(() => { window.__sent = []; });
  await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-rename");
  await A.waitForSelector("#tabs .tab-group-head .tag-rename", { timeout: 5000 });
  d.field = await A.evaluate(() => { const i = document.querySelector("#tabs .tab-group-head .tag-rename");
    return { value: i.value, focused: document.activeElement === i, inHead: i.closest(".tab-group-head").dataset.group, menuGone: !document.querySelector(".ctx-menu") }; });
  await A.keyboard.press("Control+A");
  await A.keyboard.type("platform");
  await A.mouse.move(VIEW.width - 20, VIEW.height - 20);   // off the strip: no hover tooltip over the shot
  await shot(A, "tag-rename-dark");
  await A.keyboard.press("Enter");
  d.renamed = await until(A, () => { const v = readJSON(cfg.views); return !!v && (v.tags || []).some((t) => t.id === "tag-infra" && t.name === "platform"); });
  const v1 = readJSON(cfg.views) || {};
  d.store = { tag: (v1.tags || []).find((t) => t.id === "tag-infra") || null, order: v1.tagOrder || null };
  const headIs = (p, g, folded, count) => p.waitForFunction(([g, f, c]) => { const h = Array.from(document.querySelectorAll("#tabs .tab-group-head[data-group]"));
    return h.some((x) => x.dataset.group === g && x.classList.contains("collapsed") === f && (x.querySelector(".tab-group-count") || {}).textContent === c)
      && !h.some((x) => x.dataset.group === "infra"); }, [g, folded, count], { timeout: 15000 }).then(() => true).catch(() => false);
  d.pageShows = await headIs(A, "platform", true, "3");
  d.watcherShows = await headIs(B, "platform", true, "3");
  d.focusOnHead = await A.evaluate(() => (document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.group : null) || null);
  d.renameWrites = await writes(A);
  d.foldsAfterRename = await until(A, () => { const f = readJSON(cfg.folds); return !!f && (f.collapsed || []).includes("platform") && !(f.collapsed || []).includes("infra"); })
    ? readJSON(cfg.folds) : { stale: readJSON(cfg.folds) };
  d.headsA = (await strip(A)).heads; d.headsB = (await strip(B)).heads;

  // Delete tag… on release
  await A.evaluate(() => { window.__sent = []; });
  d.delOpened = await rightClickHead(A, "release");
  d.delMenu = await menu(A);
  await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-delete");
  await A.waitForSelector("#confirm .confirm-title", { timeout: 5000 });
  d.confirm = await A.evaluate(() => ({ title: document.querySelector("#confirm .confirm-title").textContent, detail: document.querySelector("#confirm .confirm-detail").textContent,
    buttons: Array.from(document.querySelectorAll("#confirm .confirm-btn")).map((x) => ({ label: x.textContent, danger: x.classList.contains("danger") })) }));
  await shot(A, "tag-delete-confirm-dark", "#confirm .confirm-box");
  await A.click("#confirm .confirm-btn.danger");
  d.deleted = await until(A, () => { const v = readJSON(cfg.views); return !!v && !(v.tags || []).some((t) => t.name === "release"); });
  const v2 = readJSON(cfg.views) || {};
  d.afterDelete = { order: v2.tagOrder || null, deploy: tagsOf(cfg.sids.deploy), cache: tagsOf(cfg.sids.cache), names: (v2.tags || []).map((t) => t.name).sort() };
  const trailed = (p) => p.waitForFunction((ids) => !document.querySelector('#tabs .tab-group-head[data-group="release"]')
    && ids.every((id) => Array.from(document.querySelectorAll(`#tabs .tab[data-id="${id}"]`)).map((t) => t.dataset.copy).join("|") === ""),
    [cfg.sids.deploy, cfg.sids.cache], { timeout: 15000 }).then(() => true).catch(() => false);
  d.pageUntagged = await trailed(A);
  d.watcherUntagged = await trailed(B);
  d.deleteWrites = await writes(A);
  d.foldsAfterDelete = await until(A, () => { const f = readJSON(cfg.folds); return !!f && !(f.collapsed || []).includes("release"); }) ? readJSON(cfg.folds) : { stale: readJSON(cfg.folds) };

  // Open group on platform: the fold row, shared with the second client
  d.foldOpened = await rightClickHead(A, "platform");
  d.foldMenu = await menu(A);
  await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-open");
  const under = (p) => p.waitForFunction((ids) => ids.every((id) => Array.from(document.querySelectorAll(`#tabs .tab[data-id="${id}"]`)).some((t) => t.dataset.copy === "platform")),
    [cfg.sids.web, cfg.sids.api, cfg.sids.auth], { timeout: 15000 }).then(() => true).catch(() => false);
  d.pageOpen = await under(A);
  d.watcherOpen = await under(B);

  // a chip of the Tags flyout: the same menu, no fold row; Rename… asks in the prompt
  d.chipLeg = await chipMenu(A, cfg.sids.tests, "qa");
  d.chipMenu = await menu(A);
  if (!d.chipLeg) {
    await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-rename");
    await A.waitForSelector("#tag-rename-prompt input", { timeout: 5000 });
    d.prompt = await A.evaluate(() => { const p = document.getElementById("tag-rename-prompt"), i = p.querySelector("input");
      return { title: p.querySelector(".confirm-title").textContent, value: i.value, focused: document.activeElement === i,
               buttons: Array.from(p.querySelectorAll(".confirm-btn")).map((x) => x.textContent) }; });
    await A.keyboard.press("Control+A"); await A.keyboard.type("platform"); await A.keyboard.press("Enter");   // a name another tag has: refused in place
    d.promptClash = await A.evaluate(() => { const p = document.getElementById("tag-rename-prompt"); return p ? (p.querySelector(".tag-rename-hint") || {}).textContent : null; });
    await A.keyboard.press("Control+A"); await A.keyboard.type("qa");
    await shot(A, "tag-rename-prompt-dark", "#tag-rename-prompt .confirm-box");
    await A.keyboard.press("Escape");
    d.promptGone = await A.evaluate(() => !document.getElementById("tag-rename-prompt"));
    d.qaStill = (readJSON(cfg.views).tags || []).some((t) => t.id === "tag-qa" && t.name === "qa");
  }
  await a.ctx.close(); await b.ctx.close();
}

// ── LIGHT: the menu and the inline field on a fresh pair, cancelled ──
{
  const a = await open("yatharth-light");
  const A = a.page;
  const l = out.light = { theme: (await strip(A)).theme };
  l.opened = await rightClickHead(A, "qa");
  l.menu = await menu(A);
  await shot(A, "tag-menu-light", ".ctx-menu.ctx-tagmenu");
  await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-rename");
  await A.waitForSelector("#tabs .tab-group-head .tag-rename", { timeout: 5000 });
  await A.mouse.move(VIEW.width - 20, VIEW.height - 20);
  await shot(A, "tag-rename-light");
  await A.keyboard.press("Escape");
  l.cancelled = await A.evaluate(() => !document.querySelector("#tabs .tag-rename") && !!document.querySelector('#tabs .tab-group-head[data-group="qa"]'));
  l.chipLeg = await chipMenu(A, cfg.sids.docs, "qa");
  if (!l.chipLeg) {
    await A.click(".ctx-menu.ctx-tagmenu .ctx-tag-rename");
    await A.waitForSelector("#tag-rename-prompt input", { timeout: 5000 });
    await shot(A, "tag-rename-prompt-light", "#tag-rename-prompt .confirm-box");
    await A.keyboard.press("Escape");
  }
  l.qaStill = (readJSON(cfg.views).tags || []).some((t) => t.id === "tag-qa" && t.name === "qa");
  await a.ctx.close();
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
fs.writeSync(1, "RESULT:" + cfg.out + "\n");
await browser.close();
process.exit(0);
"""


class ServedTagMenu(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tag-menu-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # a state root of this lab's own: no per-session hosts
        os.makedirs(cwd, exist_ok=True)
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
                     "message": {"role": "user", "content": "what does the %s session do in notes-api?" % name}},
                    {"type": "assistant", "timestamp": iso(t0 + i + 5), "uuid": "a1", "parentUuid": "u1", "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "It keeps the %s side of the notes-api tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
            stamp = t0 + 900 - len(NAMES) + i
            os.utime(os.path.join(proj, sid + ".jsonl"), (stamp, stamp))
        Path(state, "timeline-views.json").write_text(json.dumps({"active": "all", "tags": TAGS, "tagOrder": ORDER}))
        Path(state, "view-folds.json").write_text(json.dumps(FOLDS))
        cls.state = state
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-tagmenu"
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
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _run(cls):
        """One driver run for every check (the gestures build on each other); its result, or its failure, shared."""
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
        shots = os.environ.get("TAG_MENU_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "visible": VISIBLE, "out": out,
                       "views": os.path.join(cls.state, "timeline-views.json"), "folds": os.path.join(cls.state, "view-folds.json"),
                       "sids": SIDS, "shots": shots}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        if not any(ln.startswith("RESULT:") for ln in p.stdout.splitlines()) or not os.path.exists(out):
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(Path(out).read_text())

    def _dark(self):
        d = self._run()["dark"]
        return d, "\n  %s" % json.dumps(d, indent=1)[:5000]

    def test_the_header_menu_names_the_tag_and_offers_its_actions(self):
        d, detail = self._dark()
        self.assertEqual(d["theme"], "dark", detail)
        self.assertTrue(d["opened"] and d["menu"], "a right-click on the strip's group header opens the tag menu" + detail)
        m = d["menu"]
        self.assertEqual(m["chip"], "infra", "the menu names the tag it acts on, with its chip" + detail)
        self.assertEqual([(r["act"], r["label"]) for r in m["rows"]],
                         [("rename", "Rename…"), ("open", "Open group"), ("overview", "Show overview"), ("only", "Show only this tag"),
                          ("configure", "Configure tags…"), ("delete", "Delete tag…")], detail)
        dele = m["rows"][-1]
        self.assertEqual((dele["danger"], dele["sub"]), (True, "3 sessions lose it; the sessions stay"), detail)
        self.assertGreater(m["swatches"], 0, "the palette's swatches, the tab menu's colour row" + detail)

    def test_rename_inline_keeps_members_order_and_fold_and_reaches_the_second_client(self):
        d, detail = self._dark()
        self.assertEqual(d["field"], {"value": "infra", "focused": True, "inHead": "infra", "menuGone": True},
                         "Rename… edits the name in the header itself, the field focused and holding the name" + detail)
        self.assertTrue(d["renamed"], "the kernel's store took the rename" + detail)
        tag = d["store"]["tag"]
        self.assertEqual(sorted(m["sid"] if isinstance(m, dict) else m for m in tag["members"]),
                         sorted(SIDS[n] for n in ("web", "api", "auth")), "the same tag keeps its sessions" + detail)
        self.assertEqual(d["store"]["order"], ["qa", "platform", "release"], "and its place in the order" + detail)
        self.assertTrue(d["pageShows"], "the page draws the platform header, folded as infra was, counting three, and no infra header" + detail)
        self.assertTrue(d["watcherShows"], "…and so does the second client" + detail)
        self.assertEqual(d["focusOnHead"], "platform", "Enter hands the focus back to the header under its new name" + detail)
        edits = [w for w in d["renameWrites"] if w.get("type") == "tagEdit"]
        self.assertEqual([w["edit"] for w in edits], [{"op": "rename", "tid": "tag-infra", "newName": "platform"}],
                         "ONE targeted rename by the tag's id" + detail)
        self.assertEqual([w for w in d["renameWrites"] if w.get("type") != "tagEdit"], [],
                         "no order write: the kernel carries the order in the rename (tagNameCarry)" + detail)
        self.assertNotIn("stale", d["foldsAfterRename"], "the shared folds name the new name, not the old" + detail)

    def test_delete_asks_then_untags_its_sessions_and_drops_the_group_everywhere(self):
        d, detail = self._dark()
        self.assertEqual(d["delMenu"]["chip"], "release", detail)
        self.assertEqual(d["confirm"]["title"], "Delete the tag “release”?", detail)
        self.assertEqual(d["confirm"]["detail"], "2 sessions lose this tag. The sessions stay, with any other tags they have.", detail)
        self.assertEqual(d["confirm"]["buttons"], [{"label": "Delete tag", "danger": True}, {"label": "Cancel", "danger": False}], detail)
        self.assertTrue(d["deleted"], "the kernel's store dropped the tag" + detail)
        self.assertEqual(d["afterDelete"], {"order": ["qa", "platform"], "deploy": [], "cache": [], "names": ["platform", "qa"]},
                         "gone from the order; its sessions in no tag; the other tags stand" + detail)
        self.assertTrue(d["pageUntagged"], "the page drops the group and draws its sessions on the ungrouped row" + detail)
        self.assertTrue(d["watcherUntagged"], "…and so does the second client" + detail)
        edits = [w["edit"] for w in d["deleteWrites"] if w.get("type") == "tagEdit"]
        self.assertEqual(edits, [{"op": "delete", "tid": "tag-release"}], "ONE targeted delete" + detail)
        self.assertNotIn("stale", d["foldsAfterDelete"], "the deleted group's fold goes with it" + detail)
        self.assertIn("platform", d["foldsAfterDelete"].get("collapsed", []), "the other folds stand" + detail)

    def test_the_fold_row_opens_the_group_on_every_client(self):
        d, detail = self._dark()
        self.assertIn(("open", "Open group"), [(r["act"], r["label"]) for r in d["foldMenu"]["rows"]], detail)
        self.assertTrue(d["pageOpen"], "Open group draws the renamed group's three sessions under it" + detail)
        self.assertTrue(d["watcherOpen"], "…on the second client too: the folds are shared" + detail)

    def test_a_flyout_chip_opens_the_same_menu_and_its_rename_asks_in_the_prompt(self):
        d, detail = self._dark()
        self.assertEqual(d["chipLeg"], "", detail)
        self.assertEqual(d["chipMenu"]["chip"], "qa", detail)
        self.assertEqual([r["act"] for r in d["chipMenu"]["rows"]], ["rename", "only", "configure", "delete"],
                         "a chip has no fold and no overview to offer" + detail)
        self.assertEqual(d["prompt"], {"title": "Rename the tag “qa”", "value": "qa", "focused": True, "buttons": ["Cancel", "Rename"]}, detail)
        self.assertEqual(d["promptClash"], "a tag named “platform” already exists", "a taken name is refused in place, with the reason" + detail)
        self.assertTrue(d["promptGone"] and d["qaStill"], "Escape closes it and changes nothing" + detail)

    def test_the_light_theme_opens_the_same_menu_and_field(self):
        l = self._run()["light"]
        detail = "\n  %s" % json.dumps(l, indent=1)[:3000]
        self.assertEqual(l["theme"], "light", detail)
        self.assertTrue(l["opened"], detail)
        self.assertEqual([r["act"] for r in l["menu"]["rows"]], ["rename", "fold", "overview", "only", "configure", "delete"],
                         "qa renders open: Fold group" + detail)
        self.assertTrue(l["cancelled"], "Escape puts the chip back" + detail)
        self.assertEqual(l["chipLeg"], "", detail)
        self.assertTrue(l["qaStill"], "nothing was renamed" + detail)


if __name__ == "__main__":
    unittest.main()
