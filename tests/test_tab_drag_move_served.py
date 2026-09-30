#!/usr/bin/env python3
"""Dragging a tab into another tag group MOVES the session there (the user 2026-09-30, who dragged a tab into a
different group expecting it to take that group's tag and lose all its others, and found it had only gained the
new one). Held Option (Alt elsewhere) at the release is the desktop's copy-drag and keeps the old ADD.

This lab drives the real /chat page of a hermetic kernel with REAL mouse drags (page.mouse down, a run of moves, up
over the target) and a SECOND client, another browser context on the same kernel, watching. The fixture is the
notes-api demo world: three tags, `infra` and `release` holding the same four sessions (so each has a copy in both
groups), `qa` holding two, two sessions in no tag. Each case drags one session's `infra` copy into the `qa` group's
row:
  - plain: the session ends tagged exactly [qa] in the KERNEL'S OWN views store, both pages draw its one copy under
    qa and nowhere else, and the page posted ONE tagEdit, a `setTags` naming qa alone (the atomic write: a second
    device never reads a half-moved session). Mid-gesture, qa's header wears the accent ring and the line under the
    tab names both tags coming off; no "+" badge.
  - Alt held: the session ends [infra, qa, release], both pages draw a copy under each, and the page posted ONE
    `addMember`. Mid-gesture, the ring and the "+" badge on the dragged tab; no leave line.
Both cases run under the dark theme and the light one (the `theme` key of the romp:settings store, set the way the
user sets it), each on its own session, and the second client's qa row reads the same order as the first's (the
shared arrangement the kernel keeps, the viewOrder frame) with the same folds (none).

Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (notes-api names, host
TESTHOST, placeholder sids). TAG_MOVE_SHOTS=<dir> writes the drop indicator's screenshots, mid-gesture, plain and with
the modifier, per theme."""
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
BOTH = ["web", "api", "auth", "cache"]   # in infra AND release: a copy under each
QA = ["tests", "docs"]
TAGS = [{"id": "tag-infra", "name": "infra", "color": "#4EC9B0", "members": [SIDS[n] for n in BOTH]},
        {"id": "tag-qa", "name": "qa", "color": "#c98cff", "members": [SIDS[n] for n in QA]},
        {"id": "tag-release", "name": "release", "color": "#e5a50a", "members": [SIDS[n] for n in BOTH]}]
# the cases: (label, theme, session dragged, Alt held)
CASES = [("dark, plain", "yatharth", "web", False), ("dark, alt", "yatharth", "api", True),
         ("light, plain", "yatharth-light", "auth", False), ("light, alt", "yatharth-light", "cache", True)]
TAB_COUNT = len(BOTH) * 2 + len(QA) + (len(NAMES) - len(BOTH) - len(QA))


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
const VIEW = { width: 1400, height: 640 };
// every frame the page SENDS to its kernel, kept per page: the tag write a drop posts is asserted from the wire itself
const tapSends = () => {
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
  await page.waitForSelector("#tabs .tab[data-id]", { timeout: 30000 });
  await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]").length >= n, cfg.count, { timeout: 30000 });
  await page.waitForTimeout(800);
  return { ctx, page };
};
const memberSid = (m) => (m && typeof m === "object" ? String(m.sid || "") : String(m == null ? "" : m));
const store = () => { try { return JSON.parse(fs.readFileSync(cfg.views, "utf8")); } catch (e) { return null; } };
const tagsFor = (sid) => { const v = store(); return v ? (v.tags || []).filter((t) => (t.members || []).some((m) => memberSid(m).endsWith(sid))).map((t) => t.name).sort() : null; };
// what a page's strip draws: every tab copy with its group ("" = the ungrouped row), in strip order, and the headers' folds
const strip = (page) => page.evaluate(() => {
  const b = document.getElementById("tabs").getBoundingClientRect();
  const tabs = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => { const r = t.getBoundingClientRect();
    return { id: t.dataset.id, copy: t.dataset.copy === undefined ? null : t.dataset.copy, name: (t.querySelector(".tab-label") || t).textContent.trim(),
             left: r.left, top: r.top, w: r.width, h: r.height }; });
  const heads = Array.from(document.querySelectorAll("#tabs .tab-group-head")).map((h) => ({ group: h.dataset.group, folded: h.classList.contains("collapsed") }));
  return { tabs, heads, theme: document.body.classList.contains("theme-light") ? "light" : "dark", bar: { top: b.top, bottom: b.bottom } };
});
const copiesOf = (S, sid) => S.tabs.filter((t) => t.id === sid).map((t) => t.copy).sort();
const rowOf = (S, group) => S.tabs.filter((t) => t.copy === group).map((t) => t.name);
const settleAt = async (page, x, y) => { await page.mouse.move(x, y); for (let i = 0; i < 3; i++) { await page.mouse.move(x, y); await page.waitForTimeout(25); } };
const out = { cases: [] };
let pair = null, theme = null;
for (const [label, th, who, alt] of cfg.cases) {
  if (th !== theme) {   // each theme on a fresh pair of clients: the dragging page and the second one watching
    if (pair) { await pair.a.ctx.close(); await pair.b.ctx.close(); }
    pair = { a: await open(th), b: await open(th) };
    theme = th;
  }
  const { page } = pair.a, watcher = pair.b.page;
  const sid = cfg.sids[who];
  const pre = await strip(page);
  const src = pre.tabs.find((t) => t.id === sid && t.copy === "infra");
  const qa = pre.tabs.filter((t) => t.copy === "qa");
  const tgt = qa[qa.length - 1];
  const c = { label, theme: pre.theme, who, alt, before: tagsFor(sid) };
  out.cases.push(c);
  if (!src || !tgt) { c.skipped = "no such tab: " + JSON.stringify(pre.tabs.map((t) => t.name + "/" + t.copy)); continue; }
  await page.evaluate(() => { window.__sent = []; });
  const sx = src.left + src.w / 2, sy = src.top + src.h / 2;
  const tx = tgt.left + tgt.w * 0.75, ty = tgt.top + tgt.h / 2;   // the right part of qa's last member: the slot after it
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move(sx + 4, sy + 1, { steps: 2 });   // the drag starts on the first moves after the press
  if (alt) await page.keyboard.down("Alt");              // pressed once the drag is under way, the way a hand adds it
  await page.mouse.move(tx, ty, { steps: 16 });
  await settleAt(page, sx, sy);
  await settleAt(page, tx, ty);
  for (let i = 0; i < 3; i++) await page.mouse.move(tx, ty);
  // the hand holds still past the strip's slide (flipTabs eases a moved tab over 0.12 s) with no tick after it: the
  // cue must already be where the tab came to rest, not where the last tick caught it mid-slide
  await page.waitForTimeout(300);
  // the drop indicator as the hand sees it: the ringed header, the leave line's words, the + badge on the dragged tab
  c.cue = await page.evaluate(() => {
    const vis = (el) => !!el && el.style.display !== "none" && getComputedStyle(el).display !== "none";
    const line = document.querySelector(".tab-drag-cue"), plus = document.querySelector(".tab-drag-add");
    const dragged = document.querySelector("#tabs .tab.dragging");
    const pr = vis(plus) ? plus.getBoundingClientRect() : null, dr = dragged ? dragged.getBoundingClientRect() : null;
    return { ring: Array.from(document.querySelectorAll("#tabs .tab-group-head.join-target")).map((h) => h.dataset.group),
             leave: vis(line) ? line.textContent.replace(/\s+/g, " ").trim() : "",
             plus: pr ? { text: plus.textContent, w: Math.round(pr.width), h: Math.round(pr.height),
                          onCorner: !!dr && Math.abs(pr.left + pr.width / 2 - dr.right) <= pr.width && Math.abs(pr.top + pr.height / 2 - dr.top) <= pr.height,
                          badge: { left: Math.round(pr.left), top: Math.round(pr.top) },
                          tab: dr ? { right: Math.round(dr.right), top: Math.round(dr.top), transform: getComputedStyle(dragged).transform } : null,
                          bg: getComputedStyle(plus).backgroundColor } : null,
             lineUnder: vis(line) && dr ? Math.abs(line.getBoundingClientRect().top - (dr.bottom + 4)) <= 2 : null };
  });
  if (cfg.shots) {
    const S = await strip(page);
    await page.screenshot({ path: cfg.shots + "/tag-move-" + (S.theme) + "-" + (alt ? "alt" : "plain") + ".png",
                            clip: { x: 0, y: 0, width: 900, height: Math.min(VIEW.height, Math.round(S.bar.bottom + 48)) } });
  }
  await page.mouse.up();
  if (alt) await page.keyboard.up("Alt");
  // the store answers first (a tag write lands before its ack); then both strips, each bounded and recorded
  c.wrote = false;
  for (let i = 0; i < 60; i++) { if (JSON.stringify(tagsFor(sid)) !== JSON.stringify(c.before)) { c.wrote = true; break; } await page.waitForTimeout(100); }
  c.after = tagsFor(sid);
  const want = JSON.stringify(c.after);
  const shows = (p) => p.waitForFunction(([s, w]) => JSON.stringify(Array.from(document.querySelectorAll(`#tabs .tab[data-id="${s}"]`)).map((t) => t.dataset.copy).sort()) === w,
    [sid, want], { timeout: 15000 }).then(() => true).catch(() => false);
  c.pageShows = await shows(page);
  c.watcherShows = await shows(watcher);
  await page.mouse.move(700, 600);
  await page.waitForTimeout(400);
  // …and the second client's qa row in the first one's order (the shared arrangement), bounded like the rest
  const rowA = rowOf(await strip(page), "qa");
  c.rowsAgree = await watcher.waitForFunction((w) => JSON.stringify(Array.from(document.querySelectorAll('#tabs .tab[data-copy="qa"]')).map((t) => (t.querySelector(".tab-label") || t).textContent.trim())) === w,
    JSON.stringify(rowA), { timeout: 15000 }).then(() => true).catch(() => false);
  const A = await strip(page), B = await strip(watcher);
  Object.assign(c, { copiesA: copiesOf(A, sid), copiesB: copiesOf(B, sid), rowA, rowB: rowOf(B, "qa"), headsA: A.heads, headsB: B.heads,
                     tagEdits: (await page.evaluate(() => window.__sent)).filter((m) => m && (m.type === "tagEdit" || m.type === "setTimelineViews" || m.type === "editTag")) });
}
fs.writeFileSync(cfg.out, JSON.stringify(out));
fs.writeSync(1, "RESULT:" + cfg.out + "\n");
await browser.close();
process.exit(0);
"""


class ServedTabDragMove(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="tab-move-")
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
        Path(state, "timeline-views.json").write_text(json.dumps({"active": "all", "tags": TAGS}))
        cls.state = state
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        cls.port, cls.token = _free_port(), "testtok-tabmove"
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
        """One driver run for every case (the drags are sequential); its result, or its failure, shared by the tests."""
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
        shots = os.environ.get("TAG_MOVE_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "count": TAB_COUNT, "out": out,
                       "views": os.path.join(cls.state, "timeline-views.json"), "sids": SIDS, "cases": CASES, "shots": shots}, f)
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

    def _case(self, label):
        c = next((c for c in self._run()["cases"] if c["label"] == label), None)
        self.assertIsNotNone(c, "no such case %r" % label)
        if c.get("skipped"):
            self.fail("%s: %s" % (label, c["skipped"]))
        return c

    def _moved(self, label, theme):
        c = self._case(label)
        detail = "\n  %s" % json.dumps(c, indent=1)[:4000]
        self.assertEqual(c["theme"], theme, "the page wears the theme the settings store names" + detail)
        self.assertEqual(c["before"], ["infra", "release"], "the dragged session carried two tags" + detail)
        self.assertTrue(c["wrote"], "the kernel's store answered the drop" + detail)
        self.assertEqual(c["after"], ["qa"], "it holds exactly the group it was dropped into, in the kernel's own store" + detail)
        self.assertEqual(c["copiesA"], ["qa"], "the dragging page draws its one copy under qa, and none under infra or release" + detail)
        self.assertTrue(c["watcherShows"], "the second client converged on the move" + detail)
        self.assertEqual(c["copiesB"], ["qa"], "…and draws it the same" + detail)
        tag_edits = [m for m in c["tagEdits"] if m.get("type") == "tagEdit"]
        self.assertEqual([m["edit"] for m in tag_edits], [{"op": "setTags", "tids": ["tag-qa"], "sids": [SIDS[c["who"]]]}],
                         "the page posted ONE tagEdit that sets the full tag list, never a remove-each-then-add" + detail)
        self.assertEqual([m for m in c["tagEdits"] if m.get("type") != "tagEdit"], [], "and no whole-blob or remote write" + detail)
        self.assertEqual(c["cue"]["ring"], ["qa"], "the group about to take it wore the accent ring before the release" + detail)
        self.assertEqual(c["cue"]["leave"], "out of infra, release", "the line under the tab named both tags coming off" + detail)
        self.assertIsNone(c["cue"]["plus"], "no + badge without the modifier" + detail)
        self.assertTrue(c["cue"]["lineUnder"], "the line sits under the dragged tab where it came to rest" + detail)
        self._agree(c, detail)

    def _added(self, label, theme):
        c = self._case(label)
        detail = "\n  %s" % json.dumps(c, indent=1)[:4000]
        self.assertEqual(c["theme"], theme, "the page wears the theme the settings store names" + detail)
        self.assertEqual(c["before"], ["infra", "release"], "the dragged session carried two tags" + detail)
        self.assertTrue(c["wrote"], "the kernel's store answered the drop" + detail)
        self.assertEqual(c["after"], ["infra", "qa", "release"], "with Alt held the drop ADDS qa and keeps the rest" + detail)
        self.assertEqual(c["copiesA"], ["infra", "qa", "release"], "the dragging page draws a copy under each" + detail)
        self.assertTrue(c["watcherShows"], "the second client converged on the add" + detail)
        self.assertEqual(c["copiesB"], ["infra", "qa", "release"], "…and draws it the same" + detail)
        tag_edits = [m for m in c["tagEdits"] if m.get("type") == "tagEdit"]
        self.assertEqual([m["edit"] for m in tag_edits], [{"op": "addMember", "tid": "tag-qa", "sids": [SIDS[c["who"]]]}],
                         "one addMember, the old join" + detail)
        self.assertEqual(c["cue"]["ring"], ["qa"], "the ring on the group it joins" + detail)
        self.assertEqual(c["cue"]["leave"], "", "nothing comes off, so the leave line stays down" + detail)
        plus = c["cue"]["plus"]
        self.assertIsNotNone(plus, "the + badge is up while Alt is held" + detail)
        self.assertEqual(plus["text"], "+", detail)
        self.assertTrue(plus["onCorner"], "…on the dragged tab's top-right corner" + detail)
        self.assertLessEqual(max(plus["w"], plus["h"]), 16, "…and small" + detail)
        self._agree(c, detail)

    def _agree(self, c, detail):
        self.assertTrue(c["rowsAgree"], "the second client's qa row converged on the first's order" + detail)
        self.assertEqual(c["rowB"], c["rowA"], "the shared arrangement: the same order on both clients" + detail)
        self.assertEqual(c["rowA"][-1], c["who"], "dropped on the right part of qa's last member, it lands last in qa" + detail)
        self.assertEqual(c["headsB"], c["headsA"], "the same groups, folded the same, on both clients" + detail)
        self.assertFalse(any(h["folded"] for h in c["headsA"]), "a move folds nothing" + detail)

    def test_dark_a_drop_into_another_group_moves_the_session_there(self):
        self._moved("dark, plain", "dark")

    def test_dark_alt_held_adds_the_groups_tag_and_keeps_the_rest(self):
        self._added("dark, alt", "dark")

    def test_light_a_drop_into_another_group_moves_the_session_there(self):
        self._moved("light, plain", "light")

    def test_light_alt_held_adds_the_groups_tag_and_keeps_the_rest(self):
        self._added("light, alt", "light")


if __name__ == "__main__":
    unittest.main()
