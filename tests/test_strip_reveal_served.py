#!/usr/bin/env python3
"""A deliberate navigation brings its tab into view, and an open tab menu survives the strip holding its place (the review
of PR #278, which made the tab strip keep the reader's place through its rebuilds: render.ts keepStripScroll, and a focus
restore that no longer scrolls).

Two regressions that change left behind, both in render.ts:

  * A jump to a session whose tab is out of the strip's view (a card in the feed, a row of the Sessions pane, a deep link, a
    notification, the keyboard) left the new tab off-screen: the rebuild's plain focus() used to scroll it into view as a side
    effect, with the focus in the strip only, and the quiet restore took that away. Every deliberate navigation now brings its
    tab into view itself (revealActiveTab), wherever the focus is; a repaint still never scrolls the strip.
  * The rebuild's anchoring scroll (sessions added above the view) raised a scroll event, and the menus' shared builder
    (ctx-menu.ts) closes on any scroll, so an open tab menu vanished whenever sessions were added above. The anchoring scroll
    is marked as the page's own (scrollQuietly) and dismisses nothing, the rebuild anchors on the menu's tab, and the card
    follows its tab when the rows re-flow (shiftContextMenu). A reader's own wheel still closes the menu.

The served lab drives the real /chat page of a hermetic kernel holding 150 synthetic sessions (two tag groups of 20, then the
untagged sessions on their own rows; test_strip_scroll_keep_served.py's world):

  1. the reader clicks a tab on the first row (tab mode), and the Sessions pane opens a session near the strip's end: the
     pane's own message (openSession) over this page's socket, answered by the kernel's focus frame;
  2. with the focus in the message box, a feed card is clicked for a session on the strip's first row, now above the view:
     the feed's echo (a same-origin page writes romp:focus-echo) and its showOnTimeline message, as the card's title sends;
  3. with the strip scrolled, the reader right-clicks an untagged tab, and the kernel's strip frame lands with twelve new
     sessions under the web tag, whose section is above the view; then the reader wheels the strip.

Red before the fix: the jumped-to tab stays off-screen (1 and 2), and the menu is gone after the update (3).

STRIP_REVEAL_DUMP=<path> writes the driver's whole measurement. Skips LOUDLY without the extension deps or a Playwright browser
(CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both). SYNTHETIC fixtures only: placeholder sids, the notes-api demo names.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
EXT = os.path.join(os.path.dirname(HERE), "vscode-extension")
sys.path.insert(0, HERE)
import test_strip_scroll_keep_served as _keep   # noqa: E402  the 150-session world and its hermetic kernel (the module, not its class)

FAR = _keep.HUB[_keep.N_HUB - 3][1]   # an untagged session near the strip's end
TOP = _keep.HUB[1][1]                 # a session on the strip's first row (the web group)
TOP_REPLY = "11111111-2222-3333-4444-%012d" % 5001   # its finished turn's reply (_keep._session_files)
FAR2 = _keep.HUB[_keep.N_HUB - 6][1]  # another untagged session near the strip's end, for the one-pane window

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const context = await browser.newContext({ viewport: { width: 1100, height: 700 } });
const page = await context.newPage();
page.on("pageerror", (e) => console.error("pageerror: " + String(e).slice(0, 300)));
// the kernel's strip frame enters through window.__rompFed.inbound (the shim's door for every frame): kept, to hand a copy
// with sessions added back through that door
const capInit = () => {
  let fed;
  window.__cap = { order: null, seen: [] };
  Object.defineProperty(window, "__rompFed", { configurable: true, get() { return fed; }, set(v) {
    fed = v;
    if (v && typeof v.inbound === "function") {
      const inb = v.inbound;
      v.inbound = (h, m) => {
        if (m && m.type === "tabOrder" && h === "") window.__cap.order = m;
        if (m && m.type === "caps" && h === "") window.__cap.caps = true;   // the kernel answered the page's ready: the connect burst is in
        if (m && /^(focus|confirmRevive|err)$/.test(m.type)) window.__cap.seen.push(m.type + ":" + (m.id || ""));   // the jumps' answers, for a failure's report
        return inb(h, m);
      };
    }
  } });
};
await page.addInitScript(capInit);
const out = {};
const fail = async (why) => {
  const st = await page.evaluate(() => ({ tabs: document.querySelectorAll("#tabs .tab[data-id]").length,
    st: document.getElementById("tabbar")?.scrollTop, active: document.querySelector("#tabs .tab.active")?.dataset.id, seen: window.__cap.seen })).catch(() => null);
  console.error(why + ": " + JSON.stringify(st) + " " + JSON.stringify(out).slice(0, 2000)); process.exit(1);
};
await page.goto(cfg.chat);
try {
  // the jumps below are answered on this page's socket in wire order, behind the connect burst: wait for the kernel's caps frame,
  // which follows it
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n && window.__cap.order && window.__cap.caps, cfg.nHub, { timeout: 120000 });
} catch (e) { await fail("the strip never showed the sessions"); }
await page.evaluate(() => {
  // where a tab is: the strip's scroll, whether the tab (its first copy) is wholly in the strip's view, which tab is active and
  // what holds the focus
  window.__look = (id) => {
    const sc = document.getElementById("tabbar");
    const top = sc.getBoundingClientRect().top + sc.clientTop, bottom = top + sc.clientHeight;
    const t = document.querySelector(`#tabs .tab[data-id="${id}"]`), r = t ? t.getBoundingClientRect() : null;
    const a = document.activeElement;
    return { st: sc.scrollTop, max: sc.scrollHeight - sc.clientHeight, present: !!t,
             inView: !!r && r.height > 0 && r.top >= top - 0.5 && r.bottom <= bottom + 0.5,
             active: document.querySelector("#tabs .tab.active")?.dataset.id || null,
             focus: a && a.closest && a.closest("#tabs") ? (a.dataset.id || a.className) : (a ? a.id || a.tagName : null) };
  };
  window.__frames = (n) => new Promise((res) => { const step = (k) => (k ? requestAnimationFrame(() => step(k - 1)) : res()); step(n); });
});
const landOn = async (id, why) => {
  try { await page.waitForFunction((x) => document.querySelector("#tabs .tab.active")?.dataset.id === x, id, { timeout: 45000 }); }
  catch (e) { await fail(why); }
  await page.evaluate(() => window.__frames(3));
};

// 1. tab mode: a click on a first-row tab, then the Sessions pane opens a session near the strip's end
const pick = await page.evaluate(() => {
  const sc = document.getElementById("tabbar").getBoundingClientRect();
  const t = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).find((x) => { const r = x.getBoundingClientRect(); return r.height && r.top >= sc.top && r.bottom <= sc.top + 40; });
  if (!t) return null;
  const r = (t.querySelector(".tab-label") || t).getBoundingClientRect();
  return { id: t.dataset.id, x: r.left + 4, y: r.top + r.height / 2 };
});
if (!pick) await fail("no tab on the first row");
await page.mouse.click(pick.x, pick.y);   // coordinates, not a locator: the prefetch rebuilds the strip under a locator's stability wait
try { await page.waitForFunction((id) => document.querySelector("#tabs .tab.active")?.dataset.id === id && document.activeElement?.closest?.("#tabs"), pick.id, { timeout: 10000 }); }
catch (e) { await fail("the picked tab never took the focus"); }
await page.mouse.move(5, 650);   // off the strip: no hover tip over it
out.sessionsBefore = await page.evaluate((id) => window.__look(id), cfg.far);
await page.evaluate((id) => window.__rompFed.outbound({ type: "openSession", id }), cfg.far);   // the Sessions pane row's message
await landOn(cfg.far, "the Sessions pane's open never activated the session");
out.sessionsAfter = await page.evaluate((id) => window.__look(id), cfg.far);

// 2. the focus in the message box, and a feed card's title clicked for a session on the first row (above the view now)
await page.click("#composer-input");
try { await page.waitForFunction(() => document.activeElement?.id === "composer-input", null, { timeout: 5000 }); }
catch (e) { await fail("the message box never took the focus"); }
// the reader scrolled the strip to its end: the session's tab, on the first row, is above the view
await page.evaluate(() => { const sc = document.getElementById("tabbar"); sc.scrollTop = sc.scrollHeight; });
await page.evaluate(() => window.__frames(3));
out.feedBefore = await page.evaluate((id) => window.__look(id), cfg.top);
const feed = await context.newPage();   // the feed's own document: same origin, so its echo reaches the chat as a storage event
await feed.goto(cfg.origin + "/healthz");
await feed.evaluate((sid) => localStorage.setItem("romp:focus-echo", JSON.stringify({ sid, t: Date.now() })), cfg.top);
await page.evaluate(([sid, uuid]) => window.__rompFed.outbound({ type: "showOnTimeline", sid, anchor: "work", anchorUuid: uuid }), [cfg.top, cfg.topReply]);
await landOn(cfg.top, "the feed's jump never activated the session");
// the echo activates at once; the kernel's own focus frame follows (behind whatever this socket is still delivering), and the
// scenario below must not run into it
try { await page.waitForFunction((id) => window.__cap.seen.includes("focus:" + id), cfg.top, { timeout: 45000 }); }
catch (e) { await fail("the kernel never answered the feed's showOnTimeline"); }
await page.evaluate(() => window.__frames(3));
out.feedAfter = await page.evaluate((id) => window.__look(id), cfg.top);
await feed.close();

// 3. the strip scrolled, a right-click on an untagged tab in the middle of the view, then the kernel's strip frame with twelve
// new sessions under the web tag (above the view: that section is the strip's first rows)
await page.evaluate(() => { const sc = document.getElementById("tabbar"); sc.scrollTop = Math.round((sc.scrollHeight - sc.clientHeight) * 0.6); });
await page.evaluate(() => window.__frames(3));
const target = await page.evaluate(() => {
  const sc = document.getElementById("tabbar"), b = sc.getBoundingClientRect();
  const top = b.top + sc.clientTop, bottom = top + sc.clientHeight;
  const inView = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).filter((t) => {
    const r = t.getBoundingClientRect();
    return t.dataset.copy === "" && r.height && r.top >= top + 30 && r.bottom <= bottom - 2;
  });
  const t = inView[Math.floor(inView.length / 2)];
  if (!t) return null;
  const r = (t.querySelector(".tab-label") || t).getBoundingClientRect();
  return { id: t.dataset.id, x: Math.round(r.left + 6), y: Math.round(r.top + r.height / 2) };
});
if (!target) await fail("no untagged tab in the middle of the view");
out.menuTab = target.id;
await page.mouse.click(target.x, target.y, { button: "right" });
try { await page.waitForFunction(() => !!document.querySelector("body > .ctx-menu"), null, { timeout: 5000 }); }
catch (e) { await fail("the tab menu never opened"); }
await page.evaluate(() => {
  // the card's place relative to its tab (the copy in the untagged rows, by id), and the strip's scroll
  window.__menuAt = (id) => {
    const m = document.querySelector("body > .ctx-menu"), t = document.querySelector(`#tabs .tab[data-id="${id}"][data-copy=""]`);
    const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top + sc.clientTop;
    const mr = m ? m.getBoundingClientRect() : null, tr = t ? t.getBoundingClientRect() : null;
    return { open: !!m, st: sc.scrollTop, tabOff: tr ? Math.round((tr.top - top) * 10) / 10 : null,
             dx: mr && tr ? Math.round((mr.left - tr.left) * 10) / 10 : null, dy: mr && tr ? Math.round((mr.top - tr.top) * 10) / 10 : null };
  };
});
out.menuBefore = await page.evaluate((id) => window.__menuAt(id), target.id);
const added = Array.from({ length: 12 }, (_, i) => "eeeeeeee-1111-2222-3333-" + String(i).padStart(12, "0"));
await page.evaluate((ids) => {
  const g = JSON.parse(JSON.stringify(window.__cap.order));
  g.order = ids.concat(g.order);
  g.tabs = ids.map((id, i) => ({ id, name: "tests-new-" + String(i).padStart(2, "0"), color: { bg: "#54B204", fg: "#ffffff" } })).concat(g.tabs || []);
  if (Array.isArray(g.live)) g.live = ids.concat(g.live);
  // tagged web: a session the viewer never arranged goes after every arranged one, so untagged it would land at the strip's
  // end; under the web tag it lands in that section, on the strip's first rows
  const web = ((g.views || {}).tags || []).find((t) => t.name === "web");
  if (web) web.members = (web.members || []).concat(ids);
  window.__rompFed.inbound("", g);
}, added);
try { await page.waitForFunction((ids) => ids.every((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`)), added, { timeout: 20000 }); }
catch (e) { await fail("the added sessions never reached the strip"); }
await page.evaluate(() => window.__frames(3));   // the anchoring scroll's event fires at the next frame
out.menuAfter = await page.evaluate((id) => window.__menuAt(id), target.id);
out.addedAbove = await page.evaluate(([ids, id]) => {   // the new sessions sit above the menu's tab, and above the view
  const kids = Array.from(document.getElementById("tabs").children), at = kids.findIndex((c) => c.dataset && c.dataset.id === id && c.dataset.copy === "");
  const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top + sc.clientTop;
  return at >= 0 && ids.every((x) => { const i = kids.findIndex((c) => c.dataset && c.dataset.id === x); return i >= 0 && i < at && kids[i].getBoundingClientRect().bottom <= top; });
}, [added, target.id]);
// the reader's own wheel over the strip (beside the card, not on it) still closes the menu
const spot = await page.evaluate(() => {
  const b = document.getElementById("tabbar").getBoundingClientRect(), m = document.querySelector("body > .ctx-menu")?.getBoundingClientRect();
  const x = m && m.left < b.left + b.width / 2 ? b.right - 40 : b.left + 40;
  return { x: Math.round(x), y: Math.round(b.top + b.height / 2) };
});
if (out.menuAfter.open) {
  await page.mouse.move(spot.x, spot.y);
  await page.mouse.wheel(0, -120);
  out.wheelClosed = await page.waitForFunction(() => !document.querySelector("body > .ctx-menu"), null, { timeout: 5000 }).then(() => true, () => false);
}

// 4. the strip at its top (where nothing anchors, so what lands above shows), a right-click on a tab in view, then forty sessions
// land above it in a new tag section placed first (the review of #282: the tab was carried out of the strip and its card past the
// window's bottom)
await page.keyboard.press("Escape");
await page.evaluate(() => { document.getElementById("tabbar").scrollTop = 0; });
await page.evaluate(() => window.__frames(3));
const topTarget = await page.evaluate(() => {
  const sc = document.getElementById("tabbar"), b = sc.getBoundingClientRect();
  const top = b.top + sc.clientTop, bottom = top + sc.clientHeight;
  const inView = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).filter((t) => {
    const r = t.getBoundingClientRect();
    return r.height && r.top >= top + 2 && r.bottom <= bottom - 2;
  });
  const t = inView[Math.floor(inView.length / 2)];
  if (!t) return null;
  const r = (t.querySelector(".tab-label") || t).getBoundingClientRect();
  return { id: t.dataset.id, copy: t.dataset.copy ?? "", x: Math.round(r.left + 6), y: Math.round(r.top + r.height / 2) };
});
if (!topTarget) await fail("no tab in view at the strip's top");
await page.mouse.click(topTarget.x, topTarget.y, { button: "right" });
try { await page.waitForFunction(() => !!document.querySelector("body > .ctx-menu"), null, { timeout: 5000 }); }
catch (e) { await fail("the tab menu never opened at the strip's top"); }
await page.evaluate(() => {
  window.__menuAtCopy = (id, copy) => {
    const m = document.querySelector("body > .ctx-menu"), t = document.querySelector(`#tabs .tab[data-id="${id}"][data-copy="${copy}"]`);
    const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top + sc.clientTop;
    const mr = m ? m.getBoundingClientRect() : null, tr = t ? t.getBoundingClientRect() : null;
    return { open: !!m, st: sc.scrollTop, tabOff: tr ? Math.round((tr.top - top) * 10) / 10 : null,
             inView: !!tr && tr.top >= top - 0.5 && tr.bottom <= top + sc.clientHeight + 0.5,
             dx: mr && tr ? Math.round((mr.left - tr.left) * 10) / 10 : null, dy: mr && tr ? Math.round((mr.top - tr.top) * 10) / 10 : null,
             cardTop: mr ? Math.round(mr.top) : null, cardBottom: mr ? Math.round(mr.bottom) : null, winH: window.innerHeight };
  };
});
out.topMenuBefore = await page.evaluate(([id, copy]) => window.__menuAtCopy(id, copy), [topTarget.id, topTarget.copy]);
const forty = Array.from({ length: 40 }, (_, i) => "ffffffff-1111-2222-3333-" + String(i).padStart(12, "0"));
await page.evaluate((ids) => {
  const g = JSON.parse(JSON.stringify(window.__cap.order));
  g.order = ids.concat(g.order);
  g.tabs = ids.map((id, i) => ({ id, name: "tests-top-" + String(i).padStart(2, "0"), color: { bg: "#54B204", fg: "#ffffff" } })).concat(g.tabs || []);
  if (Array.isArray(g.live)) g.live = ids.concat(g.live);
  // a tag of their own, first in the tag order: its section is the strip's first rows, above every tab on screen
  const views = g.views || (g.views = {});
  views.tags = [{ id: "t-tests", name: "tests", color: "#54B204", members: ids }].concat(views.tags || []);
  views.tagOrder = ["tests"].concat((views.tagOrder || []).filter((n) => n !== "tests"));
  window.__rompFed.inbound("", g);
}, forty);
try { await page.waitForFunction((ids) => ids.every((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`)), forty, { timeout: 20000 }); }
catch (e) { await fail("the forty sessions never reached the strip"); }
await page.evaluate(() => window.__frames(3));
out.topMenuAfter = await page.evaluate(([id, copy]) => window.__menuAtCopy(id, copy), [topTarget.id, topTarget.copy]);
out.topAddedAbove = await page.evaluate(([ids, id, copy]) => {
  const kids = Array.from(document.getElementById("tabs").children), at = kids.findIndex((c) => c.dataset && c.dataset.id === id && (c.dataset.copy ?? "") === copy);
  return at >= 0 && ids.every((x) => { const i = kids.findIndex((c) => c.dataset && c.dataset.id === x); return i >= 0 && i < at; });
}, [forty, topTarget.id, topTarget.copy]);
await page.keyboard.press("Escape");

// 5. a window one pane wide (800 px, the shell's one-pane layout with its bottom tabs), the reader on the Feed tab: a jump the
// kernel answers with its focus frame (the Sessions pane's openSession here; a card on another host's feed and a notification tap
// arrive the same way) activates the session while the chat pane is hidden, and the pane comes forward a task later. Its tab must
// be in the strip's view once the chat shows (the review of #282: the reveal was skipped while hidden and never retried)
const ctx2 = await browser.newContext({ viewport: { width: 800, height: 700 } });
await ctx2.addInitScript(capInit);
const shell = await ctx2.newPage();
shell.on("pageerror", (e) => console.error("shell pageerror: " + String(e).slice(0, 300)));
await shell.goto(cfg.shell);
const chatFrame = async () => { const h = await shell.$("#f-chat"); return h ? h.contentFrame() : null; };
let cf = null;
try {
  await shell.waitForSelector("#f-chat", { timeout: 20000 });
  cf = await chatFrame();
  await cf.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n && window.__cap && window.__cap.order && window.__cap.caps, cfg.nHub, { timeout: 120000 });
} catch (e) { await fail("the shell's chat pane never showed the sessions: " + e); }
out.narrowMobile = await shell.evaluate(() => !!(window.__rompMobileOn && window.__rompMobileOn()));
await cf.evaluate(() => {
  window.__look = (id) => {
    const sc = document.getElementById("tabbar");
    const top = sc.getBoundingClientRect().top + sc.clientTop, bottom = top + sc.clientHeight;
    const t = document.querySelector(`#tabs .tab[data-id="${id}"]`), r = t ? t.getBoundingClientRect() : null;
    return { st: sc.scrollTop, boxH: sc.clientHeight, present: !!t, inView: !!r && r.height > 0 && r.top >= top - 0.5 && r.bottom <= bottom + 0.5,
             active: document.querySelector("#tabs .tab.active")?.dataset.id || null };
  };
  window.__frames = (n) => new Promise((res) => { const step = (k) => (k ? requestAnimationFrame(() => step(k - 1)) : res()); step(n); });
  document.getElementById("tabbar").scrollTop = 0;
});
await cf.evaluate(() => window.__frames(3));
out.narrowBefore = await cf.evaluate((id) => window.__look(id), cfg.far2);
await shell.click("#mtabs button[data-pane=feed]");
try { await shell.waitForFunction(() => document.body.getAttribute("data-tab") === "feed", null, { timeout: 5000 }); }
catch (e) { await fail("the Feed tab never showed"); }
out.narrowHidden = await cf.evaluate((id) => window.__look(id), cfg.far2);   // the chat pane hidden: its strip has no box on screen
await cf.evaluate((id) => window.__rompFed.outbound({ type: "openSession", id }), cfg.far2);
try { await cf.waitForFunction((x) => document.querySelector("#tabs .tab.active")?.dataset.id === x && window.__cap.seen.includes("focus:" + x), cfg.far2, { timeout: 45000 }); }
catch (e) { await fail("the jump never activated the session in the hidden chat pane"); }
try { await shell.waitForFunction(() => document.body.getAttribute("data-tab") === "chat", null, { timeout: 10000 }); }
catch (e) { await fail("the jump never brought the chat pane forward"); }
await cf.evaluate(() => window.__frames(3));
out.narrowAfter = await cf.evaluate((id) => window.__look(id), cfg.far2);
await ctx2.close();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedStripReveal(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._boot()
        except BaseException:          # a skip OR a failure: never leave a kernel or a lab behind
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
        cls.lab = tempfile.mkdtemp(prefix="strip-reveal-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.procs = []
        tags = [{"id": "t-web", "name": "web", "color": "#1EA1EB", "members": [sid for (_n, sid) in _keep.HUB[:_keep.GROUP]]},
                {"id": "t-api", "name": "api", "color": "#c98cff", "members": [sid for (_n, sid) in _keep.HUB[_keep.GROUP:2 * _keep.GROUP]]}]
        cls.port, cls.token = _keep._free_port(), "testtok-strip-reveal"
        proc, _st = _keep._kernel(cls.lab, "hub", cls.port, cls.token, _keep.HUB, tags)
        cls.procs.append(proc)
        cls.result = cls._drive()

    @classmethod
    def tearDownClass(cls):
        for p in getattr(cls, "procs", []):
            try:
                p.kill()
                p.wait()
            except Exception:
                pass
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _drive(cls):
        cfg = os.path.join(cls.lab, "cfg.json")
        origin = "http://127.0.0.1:%d" % cls.port
        with open(cfg, "w") as f:
            json.dump({"chat": "%s/chat?token=%s" % (origin, cls.token), "shell": "%s/?token=%s" % (origin, cls.token),
                       "origin": origin, "nHub": _keep.N_HUB, "far": FAR, "far2": FAR2, "top": TOP, "topReply": TOP_REPLY}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            proc = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                                  env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            raise AssertionError("driver timed out:\n%s\n%s" % ((e.stdout or "")[-3000:], (e.stderr or "")[-3000:]))
        if proc.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if proc.returncode != 0 or line is None:
            raise AssertionError("driver failed (%r):\n%s\n%s" % (proc.returncode, proc.stdout[-3000:], proc.stderr[-3000:]))
        if os.environ.get("STRIP_REVEAL_DUMP"):
            Path(os.environ["STRIP_REVEAL_DUMP"]).write_text(line[len("RESULT:"):] + "\n")
        return json.loads(line[len("RESULT:"):])

    def test_a_sessions_pane_jump_to_an_off_screen_tab_brings_it_into_view(self):
        b, a = self.result["sessionsBefore"], self.result["sessionsAfter"]
        d = "\n  before %s\n  after %s" % (json.dumps(b), json.dumps(a))
        self.assertTrue(b["present"] and not b["inView"], "the session's tab is on the strip, below the view" + d)
        self.assertEqual(a["active"], FAR, "the jump activated the session" + d)
        self.assertEqual(a["focus"], FAR, "tab mode follows the jump: the new tab holds the focus" + d)
        self.assertTrue(a["inView"], "…and the strip scrolled it into view" + d)

    def test_a_feed_jump_brings_its_tab_into_view_with_the_focus_in_the_message_box(self):
        b, a = self.result["feedBefore"], self.result["feedAfter"]
        d = "\n  before %s\n  after %s" % (json.dumps(b), json.dumps(a))
        self.assertTrue(b["present"] and not b["inView"], "the session's tab is on the strip, above the view" + d)
        self.assertEqual(a["active"], TOP, "the jump activated the session" + d)
        self.assertTrue(a["inView"], "the strip scrolled its tab into view" + d)

    def test_an_open_tab_menu_survives_sessions_added_above_and_stays_on_its_tab(self):
        b, a = self.result["menuBefore"], self.result["menuAfter"]
        d = "\n  before %s\n  after %s" % (json.dumps(b), json.dumps(a))
        self.assertTrue(b["open"] and b["dx"] is not None, "the tab menu opened on its tab" + d)
        self.assertTrue(self.result["addedAbove"], "the new sessions landed above the view" + d)
        self.assertGreater(a["st"], b["st"] + 1, "the strip held its place by scrolling past what was added above" + d)
        self.assertTrue(a["open"], "the menu is still open after the update" + d)
        self.assertAlmostEqual(a["tabOff"], b["tabOff"], delta=1, msg="its tab stayed where it was in the view" + d)
        self.assertAlmostEqual(a["dx"], b["dx"], delta=1, msg="the card is where it was beside its tab" + d)
        self.assertAlmostEqual(a["dy"], b["dy"], delta=1, msg="…at the same height" + d)

    def test_an_open_tab_menu_at_the_strips_top_keeps_its_tab_in_view_and_stays_inside_the_window(self):
        b, a = self.result["topMenuBefore"], self.result["topMenuAfter"]
        d = "\n  before %s\n  after %s" % (json.dumps(b), json.dumps(a))
        self.assertTrue(b["open"] and b["st"] == 0 and b["inView"], "the menu opened on a tab in view, the strip at its top" + d)
        self.assertTrue(self.result["topAddedAbove"], "the forty sessions landed above the menu's tab" + d)
        self.assertTrue(a["open"], "the menu is still open after the update" + d)
        self.assertTrue(a["inView"], "its tab is still in the strip's view" + d)
        self.assertAlmostEqual(a["tabOff"], b["tabOff"], delta=1, msg="…where it was: the strip held it under the card" + d)
        self.assertAlmostEqual(a["dx"], b["dx"], delta=1, msg="the card is where it was beside its tab" + d)
        self.assertAlmostEqual(a["dy"], b["dy"], delta=1, msg="…at the same height" + d)
        self.assertLessEqual(a["cardBottom"], a["winH"], "the card is inside the window" + d)

    def test_a_jump_while_the_chat_pane_is_hidden_reveals_its_tab_when_the_pane_shows(self):
        r = self.result
        b, h, a = r["narrowBefore"], r["narrowHidden"], r["narrowAfter"]
        d = "\n  before %s\n  hidden %s\n  after %s" % (json.dumps(b), json.dumps(h), json.dumps(a))
        self.assertTrue(r["narrowMobile"], "800 px is the shell's one-pane layout" + d)
        self.assertTrue(b["present"] and not b["inView"], "the session's tab is on the strip, below the view" + d)
        self.assertEqual(h["boxH"], 0, "on the Feed tab the chat pane is hidden: its strip has no box on screen" + d)
        self.assertEqual(a["active"], FAR2, "the jump activated the session" + d)
        self.assertGreater(a["boxH"], 0, "the chat pane came forward" + d)
        self.assertTrue(a["inView"], "…with the session's tab in the strip's view" + d)

    def test_the_readers_own_wheel_still_closes_the_menu(self):
        if not self.result["menuAfter"]["open"]:
            self.skipTest("the menu did not survive the update (the test above reports it)")
        self.assertTrue(self.result.get("wheelClosed"), "a wheel over the strip closes the menu\n  " + json.dumps(self.result))


if __name__ == "__main__":
    unittest.main()
