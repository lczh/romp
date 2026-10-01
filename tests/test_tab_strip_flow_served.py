#!/usr/bin/env python3
"""Tag groups FLOW onto the strip's rows as units (the user 2026-10-01). Folded groups already shared a row (the
user 2026-09-16), but an open group always opened a row of its own, so two folded groups and one open group of
three tabs took three rows (the folded pair, the open group, the untagged sessions) where two hold them.

Now a group's unit (its header alone when folded; its header and the tabs it shows when open) stays on the row
while it fits the rest of it, opens the next row whole when it does not (never its header at the end of one row
and its tabs on the next), and wraps inside itself only when it is wider than a whole row. The untagged sessions
keep a row of their own at the end, where a dragged tab goes to lose its tags. render.ts flowStripRows measures
the painted strip and asks tab-groups.ts flowGroups (executed in ui/webview/tab-groups.test.ts) where each group
goes; the gutter between two groups on one row stands wherever either shows tabs.

The served guard drives the real /chat page of a hermetic kernel in the notes-api demo world: five tags in tag
order, web (folded by a header click), archived (folded by default), tests (open, three tabs), infra (open, two)
and ops (folded by a click), plus two untagged sessions. The user's own shape is the strip's first three groups.
At a typical width (1100px) the open tests group shares the first row with the two folded headers, behind a
gutter, and the untagged sessions sit one row below the last group; at a narrow width (560px) the tests group
does not fit beside the folded pair and opens the second row whole. At both widths, in the classic dark theme and
in Yatharth light (`theme: "yatharth-light"` in the settings store, as the user sets it), every group is one run
on one row, a group that opens a row would not have fitted after the one before it (the packing is tight), and
the trail starts at the left edge below every group. ↓ from a tab on the first row selects the nearest tab by
column on the next row, and ↑ comes back: the strip's row navigation follows the rows the flow made.

STRIP_FLOW_SHOTS=<dir> writes the strip at both widths in both themes. Skips LOUDLY without the extension deps or
a Playwright browser (CI installs none); the CI-safe pins ride ui/webview/tab-groups.test.ts. Synthetic fixtures
only (the notes-api demo world, placeholder sids).
"""
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
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

# name → (sid, tag or None); the tag order below puts the user's shape first: two folded groups, then an open group of three
SESSIONS = [
    ("web-frontend", "bbbbbbbb-1111-2222-3333-000000000001", "web"),
    ("web-gateway", "bbbbbbbb-1111-2222-3333-000000000002", "web"),
    ("old-notes", "bbbbbbbb-1111-2222-3333-000000000003", "archived"),
    ("old-search", "bbbbbbbb-1111-2222-3333-000000000004", "archived"),
    ("tests-unit", "bbbbbbbb-1111-2222-3333-000000000005", "tests"),
    ("tests-e2e", "bbbbbbbb-1111-2222-3333-000000000006", "tests"),
    ("tests-load", "bbbbbbbb-1111-2222-3333-000000000007", "tests"),
    ("infra-ci", "bbbbbbbb-1111-2222-3333-000000000008", "infra"),
    ("infra-deploy", "bbbbbbbb-1111-2222-3333-000000000009", "infra"),
    ("ops-pager", "bbbbbbbb-1111-2222-3333-000000000010", "ops"),
    ("api-docs", "bbbbbbbb-1111-2222-3333-000000000011", None),
    ("scratch", "bbbbbbbb-1111-2222-3333-000000000012", None),
]
TAGS = [("t-web", "web", "#1EA1EB"), ("t-archived", "archived", "#4EA8A9"), ("t-tests", "tests", "#c98cff"),
        ("t-infra", "infra", "#54B204"), ("t-ops", "ops", "#e5a50a")]
FOLD_BY_CLICK = ["web", "ops"]   # archived folds by default (tab-groups.ts DEFAULT_COLLAPSED)
# identity colours (bg, fg), so the pictures show the strip as it is worn: the session names in their colours
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e"),
           ("#e5a50a", "#1a1200"), ("#4EC9B0", "#00201a")]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: cfg.widths[0], height: 520 }, deviceScaleFactor: 2 });
const fail = async (why) => {
  const st = await page.evaluate(() => ({ heads: Array.from(document.querySelectorAll("#tabs .tab-group-head")).map((h) => [h.dataset.group, h.dataset.folded]),
    tabs: document.querySelectorAll("#tabs .tab[data-id]").length, html: (document.getElementById("tabs")?.innerHTML || "").slice(0, 600) })).catch(() => null);
  console.error(why + ": " + JSON.stringify(st)); process.exit(1);
};
await page.goto(cfg.chat);
try { await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab-group-head").length === n, cfg.groups, { timeout: 30000 }); }
catch (e) { await fail("the five headers never rendered"); }
// fold by a header click, reading the header first: a click on the header of the section the pane shows, holding the open
// session, is the way back to the transcript and folds nothing (render.ts makeGroupHead), so that one is pressed and read again
const fold = async (name) => {
  const sel = `#tabs .tab-group-head[data-group="${name}"]`;
  for (let i = 0; i < 3; i++) {   // loop-ok: at most a way-back press and the fold, each waited on
    const st = await page.evaluate((s) => { const h = document.querySelector(s); return h ? { act: h.dataset.act, folded: h.dataset.folded } : null; }, sel);
    if (!st) await fail("no header " + name);
    if (st.folded === "1") return;
    await page.click(sel);
    await page.waitForFunction(([s, was]) => { const h = document.querySelector(s); return !!h && (h.dataset.folded === "1" || h.dataset.act !== was); }, [sel, st.act], { timeout: 10000 }).catch(() => {});
  }
  await fail("the " + name + " group never folded");
};
for (const g of cfg.foldByClick) await fold(g);
// every open member's tab on the strip, then a pick of an untagged session so no header wears the shown-section box
try { await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-placeholder)").length === n, cfg.shown, { timeout: 30000 }); }
catch (e) { await fail("the open members' tabs never settled"); }
await page.click(`#tabs .tab[data-id="${cfg.pick}"] .tab-label`);
await page.waitForFunction((id) => { const a = document.querySelector("#tabs .tab.active"); return !!a && a.dataset.id === id && !document.querySelector("#tabs .tab-group-head.snap-shown"); }, cfg.pick, { timeout: 10000 })
  .catch(() => fail("the untagged pick never took"));
await page.waitForTimeout(400);
const survey = () => page.evaluate(() => {
  const bar = document.getElementById("tabs"), b = bar.getBoundingClientRect(), cs = getComputedStyle(bar);
  const r1 = (v) => Math.round(v * 10) / 10;
  const items = Array.from(bar.children).map((e) => { const r = e.getBoundingClientRect();
    return { cls: e.className, group: e.dataset.group || null, id: e.dataset.id || null, copy: e.dataset.copy === undefined ? null : e.dataset.copy,
             folded: e.dataset.folded || null, top: r1(r.top - b.top), left: r1(r.left - b.left), w: r1(r.width), h: r1(r.height),
             bg: e.classList.contains("tab-group-gutter") ? getComputedStyle(e).backgroundImage : null }; });
  return { items, rowW: bar.clientWidth - parseFloat(cs.paddingLeft || "0") - parseFloat(cs.paddingRight || "0"), gap: parseFloat(cs.columnGap) || 0,
           gutter: parseFloat(cs.getPropertyValue("--strip-gutter")) || 0, theme: document.body.className,
           active: document.querySelector("#tabs .tab.active")?.dataset.id || null, focused: document.activeElement?.dataset?.id || null };
});
const shot = async (tag) => {
  if (!cfg.shots) return;
  await page.mouse.move(5, 500);   // off the strip: no hover tip in the picture
  await page.waitForTimeout(250);
  const r = await page.evaluate(() => { const e = document.getElementById("tabbar").getBoundingClientRect(); return { x: e.left, y: e.top, width: e.width, height: e.height }; });
  await page.screenshot({ path: `${cfg.shots}/strip-${tag}.png`, clip: { x: 0, y: 0, width: r.x + r.width, height: Math.ceil(r.y + r.height + 6) } });
};
const out = {};
for (const theme of ["classic", "yatharth-light"]) {
  if (theme !== "classic") {
    await page.evaluate((t) => { let s = {}; try { s = JSON.parse(localStorage.getItem("romp:settings") || "{}") || {}; } catch (e) {}
                                 s.theme = t; localStorage.setItem("romp:settings", JSON.stringify(s)); }, theme);
    await page.reload();
    try { await page.waitForFunction((n) => document.querySelectorAll("#tabs .tab[data-id]:not(.tab-placeholder)").length === n && document.body.classList.contains("theme-light"), cfg.shown, { timeout: 30000 }); }
    catch (e) { await fail("the light page never settled"); }
    await page.waitForTimeout(400);
  }
  for (const w of cfg.widths) {
    await page.setViewportSize({ width: w, height: 520 });
    await page.waitForTimeout(400);   // the strip's observer re-flows on the resize
    out[theme + "@" + w] = await survey();
    await shot(`${w}-${theme === "classic" ? "dark" : "yatharth-light"}`);
  }
}
// ↓ and ↑ at the typical width (the light page is up): from a tab of the open group on the first row to the next row and back
await page.setViewportSize({ width: cfg.widths[0], height: 520 });
await page.waitForTimeout(400);
const nav = { before: await survey() };
await page.click(`#tabs .tab[data-id="${cfg.navFrom}"] .tab-label`);
await page.waitForFunction((id) => document.querySelector("#tabs .tab.active")?.dataset.id === id, cfg.navFrom, { timeout: 10000 }).catch(() => fail("the nav start never activated"));
await page.focus(`#tabs .tab[data-id="${cfg.navFrom}"]`);
nav.start = await survey();
await page.keyboard.press("ArrowDown");
await page.waitForFunction((id) => document.querySelector("#tabs .tab.active")?.dataset.id !== id, cfg.navFrom, { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(300);
nav.down = await survey();
await page.keyboard.press("ArrowUp");
await page.waitForFunction((id) => document.querySelector("#tabs .tab.active")?.dataset.id !== id, nav.down.active, { timeout: 5000 }).catch(() => {});
await page.waitForTimeout(300);
nav.up = await survey();
out.nav = nav;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedTagGroupsFlow(unittest.TestCase):
    maxDiff = None
    WIDE, NARROW = 1100, 560

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them")
        cls.lab = tempfile.mkdtemp(prefix="stripflow-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        cls.state = os.path.join(cls.lab, "xdg", "romp")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states"):
            os.makedirs(os.path.join(cls.state, d), exist_ok=True)
        Path(cls.state, "session-hosts").write_text("off\n")   # a lab that mints its own state root pins hosts off (CLAUDE.md)
        os.makedirs(cwd, exist_ok=True)
        claude = os.path.join(cls.lab, "claude")
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        for k, (name, sid, _tag) in enumerate(SESSIONS):
            bg, fg = PALETTE[k % len(PALETTE)]
            Path(cls.state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
            Path(cls.state, "sdk", sid + ".json").write_text(json.dumps(
                {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high",
                 "lastSid": sid, "alive": True, "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
            u, a = "11111111-2222-3333-4444-%012d" % (100 + k), "11111111-2222-3333-4444-%012d" % (200 + k)
            # a finished turn apiece, so every tab rests idle as most of a real strip does
            recs = [{"type": "user", "uuid": u, "parentUuid": None, "timestamp": "2026-09-07T10:%02d:00.000Z" % k, "sessionId": sid,
                     "message": {"role": "user", "content": "notes-api: check the %s service" % name}},
                    {"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": "2026-09-07T10:%02d:05.000Z" % k, "sessionId": sid,
                     "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                                 "content": [{"type": "text", "text": "The %s service is tidy." % name}]}}]
            Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))
        Path(cls.state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 100}, "seven_day": {"pct": 10}}))
        tags = [{"id": tid, "name": tname, "color": color, "members": [sid for (_n, sid, t) in SESSIONS if t == tname]}
                for (tid, tname, color) in TAGS]
        Path(cls.state, "timeline-views.json").write_text(json.dumps({"tags": tags, "tagOrder": [t[1] for t in TAGS]}))
        cls.port = _free_port()
        cls.token = "testtok-stripflow"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token)
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")],
                                      stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
        import urllib.request
        for _ in range(120):
            try:
                urllib.request.urlopen("http://127.0.0.1:%d/healthz" % cls.port, timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        else:
            cls.kernel.kill()
            raise unittest.SkipTest("hermetic kernel never served /healthz here")
        cls.result = cls._drive()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "kernel", None):
            cls.kernel.kill()
            cls.kernel.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    @classmethod
    def _drive(cls):
        shots = os.environ.get("STRIP_FLOW_SHOTS", "")
        if shots:
            os.makedirs(shots, exist_ok=True)
        sid = {n: s for (n, s, _t) in SESSIONS}
        folded = set(FOLD_BY_CLICK) | {"archived"}
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.port, cls.token), "widths": [cls.WIDE, cls.NARROW],
                       "groups": len(TAGS), "foldByClick": FOLD_BY_CLICK,
                       "shown": len([1 for (_n, _s, t) in SESSIONS if t not in folded]),
                       "pick": sid["scratch"], "navFrom": sid["tests-e2e"], "shots": shots}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:])
        return json.loads(line[len("RESULT:"):])

    # ── reading a survey ──
    @staticmethod
    def _units(s):
        """The strip cut into units in order: [(group, [header item, its tabs…])], then ("trail", [its tabs]); the
        elements between (row breaks, gutters, hairlines, the right end) are left out."""
        units = []
        for it in s["items"]:
            cls = it["cls"].split()
            if "tab-group-head" in cls:
                units.append((it["group"], [it]))
            elif "tab-group-sep" in cls:
                units.append(("trail", []))
            elif "tab" in cls and it["id"] and units:
                units[-1][1].append(it)
        return units

    @staticmethod
    def _left(s):
        return min(it["left"] for it in s["items"] if it["w"] > 0 and ("tab" in it["cls"].split() or "tab-group-head" in it["cls"].split()))

    def _detail(self, s):
        return "\n  rowW %r gap %r gutter %r\n  " % (s["rowW"], s["gap"], s["gutter"]) + "\n  ".join(
            "%s %s@%s,%s+%s" % (i["cls"], i["group"] or i["id"] or "", i["left"], i["top"], i["w"]) for i in s["items"])

    def _check_flow(self, s, label):
        """The invariants at any width: every group one run (one row unless wider than a row), a group that opens a row
        would not have fitted after the group before it, a group sharing a row sits after the one before on its last
        row with a gutter between them wherever either shows tabs, and the trail starts at the left edge below them."""
        d = self._detail(s)
        units = self._units(s)
        self.assertEqual([g for g, _ in units], [t[1] for t in TAGS] + ["trail"], "%s: the groups in tag order, then the trail%s" % (label, d))
        left, rowW, gap, gutter = self._left(s), s["rowW"], s["gap"], s["gutter"]
        self.assertEqual(gutter, 13, "%s: the gutter's width as the flow reads it%s" % (label, d))
        items = s["items"]
        breaks = [i for i in items if "tab-group-break" in i["cls"].split()]
        prev = None
        for g, its in units[:-1]:
            head = its[0]
            width = sum(i["w"] for i in its) + gap * (len(its) - 1)
            tops = {i["top"] for i in its}
            if width <= rowW:
                self.assertEqual(len(tops), 1, "%s: the %s group is one run on one row%s" % (label, g, d))
            if prev is None:
                self.assertEqual(head["left"], left, "%s: the first header opens the strip%s" % (label, d))
            else:
                pg, pits = prev
                last = pits[-1]
                open_side = len(its) > 1 or len(pits) > 1
                at = items.index(head)
                lead = items[at - 1]
                if head["left"] == left:
                    # opened a row: it would not have fitted after the group before (the packing is tight), and a break says so
                    need = (last["left"] + last["w"]) + (gutter + gap if open_side else 0) + gap + width
                    self.assertGreater(need, rowW - 1, "%s: the %s group opened a row it would have fitted on after %s%s" % (label, g, pg, d))
                    self.assertIn("tab-group-break", lead["cls"].split(), "%s: a row break stands ahead of %s%s" % (label, g, d))
                    self.assertGreater(head["top"], last["top"], "%s: %s sits on a row below %s's last%s" % (label, g, pg, d))
                else:
                    self.assertEqual(head["top"], last["top"], "%s: %s shares the row of %s's last item%s" % (label, g, pg, d))
                    self.assertGreater(head["left"], last["left"], "%s: …after it%s" % (label, d))
                    if open_side:
                        self.assertIn("tab-group-gutter", lead["cls"].split(), "%s: a gutter between %s and %s, one of them showing tabs%s" % (label, pg, g, d))
                        self.assertAlmostEqual(lead["w"], gutter, delta=0.6, msg="%s: the gutter is its width%s" % (label, d))
                        self.assertIn("linear-gradient", lead["bg"] or "", "%s: the gutter draws its hairline%s" % (label, d))
                    else:
                        self.assertEqual(lead, last, "%s: two bare folded headers sit flush, nothing between%s" % (label, d))
            prev = (g, its)
        trail = units[-1][1]
        self.assertTrue(trail, "%s: the untagged sessions have their tabs%s" % (label, d))
        self.assertEqual(trail[0]["left"], left, "%s: the trail opens its own row at the left edge%s" % (label, d))
        self.assertGreater(trail[0]["top"], max(i["top"] for _g, its in units[:-1] for i in its), "%s: below every group%s" % (label, d))
        self.assertEqual(len({t["top"] for t in trail}), 1, "%s: the two untagged tabs on that one row%s" % (label, d))
        self.assertIn("tab-group-sep", breaks[-1]["cls"].split(), "%s: the last break is the trail's boundary%s" % (label, d))
        return units

    def _by_group(self, units):
        return {g: its for g, its in units}

    # ── the cases ──
    def test_the_users_shape_shares_one_row_at_a_typical_width(self):
        for theme in ("classic", "yatharth-light"):
            s = self.result["%s@%d" % (theme, self.WIDE)]
            units = self._check_flow(s, "%s %d" % (theme, self.WIDE))
            g = self._by_group(units)
            d = self._detail(s)
            self.assertEqual([g["web"][0]["folded"], g["archived"][0]["folded"], g["tests"][0]["folded"]], ["1", "1", "0"], "two folded groups, then the open one" + d)
            self.assertEqual(len(g["tests"]), 4, "the open tests group shows its three tabs" + d)
            row1 = g["web"][0]["top"]
            self.assertEqual({i["top"] for i in g["archived"] + g["tests"]}, {row1},
                             "%s: the open group of three shares row 1 with the two folded headers%s" % (theme, d))
            lead = s["items"][s["items"].index(g["tests"][0]) - 1]
            self.assertIn("tab-group-gutter", lead["cls"].split(), "a gutter leads the open group in mid-row" + d)
            if theme == "yatharth-light":
                self.assertIn("theme-light", s["theme"])
                self.assertIn("chat-theme-yatharth", s["theme"])

    def test_an_open_group_that_does_not_fit_moves_to_the_next_row_whole(self):
        for theme in ("classic", "yatharth-light"):
            s = self.result["%s@%d" % (theme, self.NARROW)]
            units = self._check_flow(s, "%s %d" % (theme, self.NARROW))
            g = self._by_group(units)
            d = self._detail(s)
            left = self._left(s)
            self.assertEqual(g["web"][0]["top"], g["archived"][0]["top"], "the folded pair still shares row 1" + d)
            tests = g["tests"]
            self.assertEqual(tests[0]["left"], left, "%s: the tests header opens row 2 at the left edge%s" % (theme, d))
            self.assertGreater(tests[0]["top"], g["archived"][0]["top"], "…below the folded pair" + d)
            self.assertEqual({i["top"] for i in tests}, {tests[0]["top"]}, "%s: its three tabs went with it%s" % (theme, d))

    def test_down_and_up_follow_the_flowed_rows(self):
        nav = self.result["nav"]
        start, down, up = nav["start"], nav["down"], nav["up"]
        d = self._detail(start)
        sid = {n: s for (n, s, _t) in SESSIONS}
        self.assertEqual(start["active"], sid["tests-e2e"], "the walk starts on a tab of the open group" + d)
        tabs = [i for i in start["items"] if i["id"] and "tab" in i["cls"].split()]
        me = next(t for t in tabs if t["id"] == sid["tests-e2e"])
        heads = {i["group"]: i for i in start["items"] if i["group"]}
        self.assertEqual(me["top"], heads["web"]["top"], "…on the first row, beside the folded headers" + d)
        below = sorted({t["top"] for t in tabs if t["top"] > me["top"]})
        self.assertTrue(below, "a row of tabs below" + d)
        cx = me["left"] + me["w"] / 2
        want = min((t for t in tabs if t["top"] == below[0]), key=lambda t: abs(t["left"] + t["w"] / 2 - cx))
        self.assertEqual(down["active"], want["id"], "↓ selects the nearest tab by column on the next row the flow made" + self._detail(down))
        self.assertEqual(down["focused"], want["id"], "…and the focus follows it")
        back = [t for t in tabs if t["top"] == me["top"]]
        wx = want["left"] + want["w"] / 2
        want_up = min(back, key=lambda t: abs(t["left"] + t["w"] / 2 - wx))
        self.assertEqual(up["active"], want_up["id"], "↑ returns to the first row's nearest tab" + self._detail(up))


if __name__ == "__main__":
    unittest.main()
