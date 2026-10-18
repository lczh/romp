#!/usr/bin/env python3
"""The tab strip keeps its scroll position through updates (the user 2026-10-17: with many sessions the strip scrolls, and
whenever anything in it changed, the scroll jumped back to the top).

Two causes, both in render.ts renderTabs, which rebuilds the strip whenever its signature changes (every kernel push that
touches a tab): the rebuild handed tab-mode focus back to the active tab with a plain focus(), which scrolls the focused
node into view, so a reader who had picked a tab near the top (focus stays on it) and then wheeled down was thrown back to
that tab on the next push; and the rebuild kept the raw scrollTop, so sessions added or removed above the view slid what the
reader was looking at by a row or more. The rebuild now restores focus without scrolling, and it anchors on the first item
visible in the strip, so that item stays where it was. A frame that drops sessions dismisses them one by one, a rebuild each,
so the anchor is the strip the reader SAW, held through that frame's rebuilds (anchoring each unpainted in-between strip on
its own first item let the view drift a row per rebuild). Only a deliberate gesture scrolls the strip (Escape from the
message box into tab mode brings the active tab into view, as it always did).

The served lab drives the real /chat page of a hub kernel holding 150 synthetic sessions (two tag groups of 20, then the
untagged sessions on their own rows), with a SECOND hermetic kernel checked in as host TESTHOST (the mobile handshake, no
ssh). The reader clicks a tab on the strip's first row, then wheels the strip down into the untagged sessions. Every update
below comes from outside this page and must leave the first visible item where it was:

  1. a status change: a session frame through the shim's own door (window.__rompFed.inbound, where every kernel frame
     enters) with that session working;
  2. new sessions above the view: the hub's strip frame with twelve new sessions at its front;
  3. sessions removed above the view: the hub's strip frame without those twelve and without four sessions above the view;
  4. a linked machine's update: a new session written into TESTHOST's state, which its kernel lists and the relay carries;
  5. with the focus in the message box rather than the strip, new sessions above the view again (the anchor alone).

The idle prefetch fills the strip's placeholder tabs throughout, a stream of real kernel pushes, each one a rebuild. Then
Escape from the message box brings the active tab back into view. Red before the fix: the first update throws the strip
back to the clicked tab (1 to 4), and the inserted sessions push the first visible item down (5).

STRIP_SCROLL_DUMP=<path> writes the driver's whole measurement. Skips LOUDLY without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both).
SYNTHETIC fixtures only: placeholder sids, host TESTHOST, the notes-api demo names.
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
import urllib.request
from pathlib import Path

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

N_HUB = 150
GROUP = 20   # the first two blocks of sessions are tagged web and api; the rest are untagged
KINDS = ("web", "api", "tests", "docs", "deploy", "auth")
HUB = [("%s-%03d" % (KINDS[k % len(KINDS)], k), "cccccccc-1111-2222-3333-%012d" % k) for k in range(N_HUB)]
REMOTE = [("web", "dddddddd-1111-2222-3333-000000000001"), ("api", "dddddddd-1111-2222-3333-000000000002"),
          ("tests", "dddddddd-1111-2222-3333-000000000003")]
REMOTE_NEW = ("docs", "dddddddd-1111-2222-3333-000000000004")
PALETTE = [("#9cd2ff", "#0c1a2e"), ("#1EA1EB", "#ffffff"), ("#54B204", "#ffffff"), ("#c98cff", "#1a0c2e"),
           ("#e5a50a", "#1a1200"), ("#4EC9B0", "#00201a")]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _session_files(state, proj, cwd, name, sid, k):
    """One idle synthetic session: its name line, its SDK record and a finished turn."""
    bg, fg = PALETTE[k % len(PALETTE)]
    Path(state, "names", sid).write_text("%s\t%s\t%s\t%s\n" % (name, cwd, bg, fg))
    Path(state, "sdk", sid + ".json").write_text(json.dumps(
        {"sid": sid, "name": name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
         "model": "claude-fable-5-1", "liveModel": "Fable 5.1"}))
    u, a = "11111111-2222-3333-4444-%012d" % (1000 + k), "11111111-2222-3333-4444-%012d" % (5000 + k)
    recs = [{"type": "user", "uuid": u, "parentUuid": None, "timestamp": "2026-09-07T10:%02d:00.000Z" % (k % 60), "sessionId": sid,
             "message": {"role": "user", "content": "notes-api: check the %s service" % name}},
            {"type": "assistant", "uuid": a, "parentUuid": u, "timestamp": "2026-09-07T10:%02d:05.000Z" % (k % 60), "sessionId": sid,
             "message": {"role": "assistant", "model": "claude-fable-5-1", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "The %s service is tidy." % name}]}}]
    Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


def _kernel(lab, name, port, token, sessions, tags=None):
    """Boot one hermetic kernel with its own state root over the lab's dist, holding `sessions`."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    cwd = os.path.join(lab, name, "proj")
    for d in ("names", "sdk", "states"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab that mints its own state root pins hosts off (CLAUDE.md)
    os.makedirs(cwd, exist_ok=True)
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    for k, (sname, sid) in enumerate(sessions):
        _session_files(state, proj, cwd, sname, sid, k)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    if tags:
        Path(state, "timeline-views.json").write_text(json.dumps({"tags": tags, "tagOrder": [t["name"] for t in tags]}))
    env = _lab.kernel_env(os.path.join(lab, name), claude, os.path.join(lab, "dist"), port, token,
                          ROMP_HOST_NAME=name.upper())
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, {"state": state, "proj": proj, "cwd": cwd}
        except Exception:
            time.sleep(0.5)
    proc.kill()
    proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: 1100, height: 700 } });
page.on("pageerror", (e) => console.error("pageerror: " + String(e).slice(0, 300)));
// the local kernel's frames enter the page through window.__rompFed.inbound (the shim's deliver): the lab keeps its last
// strip frame and the last session frame per id, to hand modified copies back through that door
await page.addInitScript(() => {
  let fed;
  window.__cap = { order: {}, session: {} };
  Object.defineProperty(window, "__rompFed", { configurable: true, get() { return fed; }, set(v) {
    fed = v;
    if (v && typeof v.inbound === "function") {
      const inb = v.inbound;
      v.inbound = (h, m) => {
        if (m && m.type === "tabOrder") window.__cap.order[h] = m;
        if (m && m.type === "session" && h === "" && typeof m.id === "string") window.__cap.session[m.id] = m;
        return inb(h, m);
      };
    }
  } });
});
const out = {};
const fail = async (why) => {
  const st = await page.evaluate(() => ({ tabs: document.querySelectorAll("#tabs .tab[data-id]").length,
    remote: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).filter((t) => t.dataset.id.startsWith("TESTHOST")).length,
    st: document.getElementById("tabbar")?.scrollTop, caps: Object.keys(window.__cap.order), scrolls: (window.__scrolls || []).slice(0, 40) })).catch(() => null);
  console.error(why + ": " + JSON.stringify(st)); process.exit(1);
};
await page.goto(cfg.chat);
try {
  await page.waitForFunction((n) => {
    const ids = Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.dataset.id);
    return ids.filter((id) => !id.startsWith("TESTHOST")).length >= n && ids.filter((id) => id.startsWith("TESTHOST")).length >= 3
      && window.__cap.order[""];
  }, cfg.nHub, { timeout: 60000 });
} catch (e) { await fail("the strip never showed both machines' sessions"); }
// what the reader sees: the strip's scroll, and the first item whose top edge is in view, by its key and its offset from
// the top of the view (a tab by its id and the group its copy sits in, a group header by its name)
await page.evaluate(() => {
  window.__key = (e) => e.classList.contains("tab-group-head") ? "h:" + (e.dataset.group || "")
    : (e.classList.contains("tab") && e.dataset.id) ? "t:" + e.dataset.id + "|" + (e.dataset.copy ?? "") : null;
  window.__look = () => {
    const sc = document.getElementById("tabbar"), bar = document.getElementById("tabs");
    const top = sc.getBoundingClientRect().top + sc.clientTop, bottom = top + sc.clientHeight;
    let first = null;
    for (const c of bar.children) {
      const k = window.__key(c); if (!k) continue;
      const r = c.getBoundingClientRect();
      if (!r.height || r.top < top - 0.5) continue;
      if (r.top < bottom) first = { key: k, off: Math.round((r.top - top) * 10) / 10 };
      break;
    }
    const a = document.activeElement;
    const act = document.querySelector("#tabs .tab.active");
    const ar = act ? act.getBoundingClientRect() : null;
    return { st: sc.scrollTop, max: sc.scrollHeight - sc.clientHeight, first,
             active: act ? act.dataset.id : null, activeInView: !!ar && ar.top >= top - 0.5 && ar.bottom <= bottom + 0.5,
             focus: a && a.closest && a.closest("#tabs") ? (a.dataset.id || a.className) : (a ? a.id || a.tagName : null) };
  };
  // where a keyed item sits now, relative to the top of the view (null when it left the strip)
  window.__offOf = (key) => {
    const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top + sc.clientTop;
    for (const c of document.getElementById("tabs").children) if (window.__key(c) === key) return Math.round((c.getBoundingClientRect().top - top) * 10) / 10;
    return null;
  };
  window.__frames = (n) => new Promise((res) => { const step = (k) => (k ? requestAnimationFrame(() => step(k - 1)) : res()); step(n); });
});
// 1. the reader picks a tab on the strip's first row (focus stays on it: tab mode), then wheels the strip down
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
out.picked = pick.id;
const box = await page.evaluate(() => { const r = document.getElementById("tabbar").getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; });
await page.mouse.move(box.x, box.y);
const max = await page.evaluate(() => { const sc = document.getElementById("tabbar"); return sc.scrollHeight - sc.clientHeight; });
// every offset the strip passes through from here on, for the record (a throw-back shows as a return toward 0)
await page.evaluate(() => { window.__scrolls = []; const sc = document.getElementById("tabbar");
  sc.addEventListener("scroll", () => { if (window.__scrolls.length < 400) window.__scrolls.push(Math.round(sc.scrollTop)); }, { passive: true }); });
await page.mouse.wheel(0, Math.round(max * 0.6));
// the wheel's scroll settled: it moved, then held one offset across two polls
try {
  await page.waitForFunction(() => { const v = document.getElementById("tabbar").scrollTop, was = window.__lastSt; window.__lastSt = v; return window.__scrolls.length > 0 && v === was; },
                             null, { timeout: 10000, polling: 150 });
} catch (e) { await fail("the wheel never scrolled the strip"); }
await page.evaluate(() => window.__frames(2));
out.max = max;
out.wheeled = await page.evaluate(() => window.__look());
// one update: `deliver` puts it on the way, `landed` says it is on the strip; then two frames for the strip's paint
const step = async (name, deliver, landed, arg) => {
  const before = await page.evaluate(() => window.__look());
  await deliver();
  let ok = true;
  try { await page.waitForFunction(landed, arg, { timeout: 45000, polling: 50 }); } catch (e) { ok = false; }
  await page.evaluate(() => window.__frames(2));
  const after = await page.evaluate(() => window.__look());
  out[name] = { before, after, landed: ok, offNow: before.first ? await page.evaluate((k) => window.__offOf(k), before.first.key) : null };
};
const hub = await page.evaluate(() => JSON.parse(JSON.stringify(window.__cap.order[""])));
// 2. a status change: a loaded session at or below the view, working (the idle prefetch loads the strip's sessions a few at a
// time, so the lab waits for one of those to have arrived; every arrival meanwhile is a rebuild the reader's place rides through)
let working = null;
try {
  working = await (await page.waitForFunction(() => {
    const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top;
    const t = Array.from(document.querySelectorAll("#tabs .tab[data-id]:not(.tab-placeholder)")).find((x) =>
      !x.dataset.id.startsWith("TESTHOST") && x.getBoundingClientRect().top >= top && window.__cap.session[x.dataset.id]);
    return t ? t.dataset.id : null;
  }, null, { timeout: 60000, polling: 250 })).jsonValue();
} catch (e) { /* reported below */ }
if (!working) await fail("no loaded session at or below the view");
out.workingId = working;
await step("status", () => page.evaluate((id) => {
  const f = JSON.parse(JSON.stringify(window.__cap.session[id]));
  f.status = Object.assign({}, f.status, { state: "working" });
  window.__rompFed.inbound("", f);
}, working), (id) => !!document.querySelector(`#tabs .tab.tab-working[data-id="${id}"]`), working);
// 3. twelve new sessions at the front of the hub's strip frame: they land at the head of the untagged rows, above the view
const added = Array.from({ length: 12 }, (_, i) => "eeeeeeee-1111-2222-3333-" + String(i).padStart(12, "0"));
const withAdded = (f, ids, tag) => {
  const g = JSON.parse(JSON.stringify(f));
  g.order = ids.concat(g.order);
  g.tabs = ids.map((id, i) => ({ id, name: tag + "-" + String(i).padStart(2, "0"), color: { bg: "#54B204", fg: "#ffffff" } })).concat(g.tabs || []);
  if (Array.isArray(g.live)) g.live = ids.concat(g.live);
  return g;
};
await step("added", () => page.evaluate((f) => window.__rompFed.inbound("", f), withAdded(hub, added, "tests-new")),
  (ids) => ids.every((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`)), added);
out.addedAbove = await page.evaluate(([ids, key]) => {
  const kids = Array.from(document.getElementById("tabs").children), at = kids.findIndex((c) => window.__key(c) === key);
  return at >= 0 && ids.every((id) => { const i = kids.findIndex((c) => c.dataset && c.dataset.id === id); return i >= 0 && i < at; });
}, [added, out.added.before.first && out.added.before.first.key]);
// 4. the hub's frame without those twelve and without four sessions above the view
const gone = await page.evaluate((key) => {
  const kids = Array.from(document.getElementById("tabs").children), at = kids.findIndex((c) => window.__key(c) === key);
  const sc = document.getElementById("tabbar"), top = sc.getBoundingClientRect().top;
  return kids.slice(0, at).filter((c) => c.classList.contains("tab") && c.dataset.id && !c.dataset.id.startsWith("TESTHOST")
    && !c.dataset.id.startsWith("eeeeeeee") && c.dataset.copy === "" && c.getBoundingClientRect().bottom <= top).slice(-4).map((c) => c.dataset.id);
}, out.added.after.first && out.added.after.first.key);
out.goneIds = gone;
await step("removed", () => page.evaluate(([f, drop]) => {
  const g = JSON.parse(JSON.stringify(f));
  g.order = g.order.filter((id) => !drop.includes(id));
  g.tabs = (g.tabs || []).filter((t) => !drop.includes(t.id));
  if (Array.isArray(g.live)) g.live = g.live.filter((id) => !drop.includes(id));
  window.__rompFed.inbound("", g);
}, [hub, gone]), (ids) => ids.every((id) => !document.querySelector(`#tabs .tab[data-id="${id}"]`)), added.concat(gone));
// 5. a linked machine's update: a new session in TESTHOST's own state, listed by its kernel and carried by the relay
await step("linked", () => { fs.writeFileSync(cfg.remoteNew.flag, "1"); return page.evaluate(() => 0); },
  (id) => !!document.querySelector(`#tabs .tab[data-id="TESTHOST:${id}"]`), cfg.remoteNew.sid);
// 6. the focus in the message box (a click there), and new sessions above the view again: the anchor alone
await page.click("#composer-input");
try { await page.waitForFunction(() => document.activeElement?.id === "composer-input", null, { timeout: 5000 }); }
catch (e) { await fail("the message box never took the focus"); }
const added2 = Array.from({ length: 12 }, (_, i) => "ffffffff-1111-2222-3333-" + String(i).padStart(12, "0"));
const hubNow = await page.evaluate(() => JSON.parse(JSON.stringify(window.__cap.order[""])));
await step("addedUnfocused", () => page.evaluate((f) => window.__rompFed.inbound("", f), withAdded(hubNow, added2, "docs-new")),
  (ids) => ids.every((id) => document.querySelector(`#tabs .tab[data-id="${id}"]`)), added2);
// 7. a deliberate gesture still scrolls the strip: Escape from the message box focuses the active tab, bringing it into view
await page.keyboard.press("Escape");
try { await page.waitForFunction((id) => document.activeElement?.dataset?.id === id, pick.id, { timeout: 5000 }); } catch (e) { /* read below */ }
await page.evaluate(() => window.__frames(2));
out.escape = await page.evaluate(() => window.__look());
out.scrolls = await page.evaluate(() => window.__scrolls.slice(0, 60));
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedStripScrollKeep(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="strip-scroll-keep-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        copy_dist(os.path.join(EXT, "dist"), os.path.join(cls.lab, "dist"))
        cls.procs = []
        tags = [{"id": "t-web", "name": "web", "color": "#1EA1EB", "members": [sid for (_n, sid) in HUB[:GROUP]]},
                {"id": "t-api", "name": "api", "color": "#c98cff", "members": [sid for (_n, sid) in HUB[GROUP:2 * GROUP]]}]
        cls.rport, cls.rtoken = _free_port(), "testtok-scrollkeep-remote"
        cls.hport, cls.htoken = _free_port(), "testtok-scrollkeep-hub"
        rp, cls.remote = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, REMOTE)
        cls.procs.append(rp)
        hp, cls.hub = _kernel(cls.lab, "hub", cls.hport, cls.htoken, HUB, tags)
        cls.procs.append(hp)
        # the check-in handshake a mobile machine makes through its reverse forward: the hub records the peer like an
        # attached remote and probes it on the port given, here the remote's own
        body = json.dumps({"host": "TESTHOST", "kernelPort": cls.rport, "busPort": _free_port(), "token": cls.rtoken}).encode()
        req = urllib.request.Request("http://127.0.0.1:%d/checkin?token=%s" % (cls.hport, cls.htoken), data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            ans = json.loads(resp.read().decode())
        if not ans.get("ok"):
            raise unittest.SkipTest("the hub refused the check-in: %r" % ans)
        rows = []
        for _ in range(60):   # the hub's supervisor probes the peer and reports it up; the browser dials only then
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/tunnels?token=%s" % (cls.hport, cls.htoken), timeout=3) as r2:
                    rows = json.loads(r2.read().decode()).get("tunnels") or []
            except Exception:
                rows = []
            row = next((t for t in rows if t.get("host") == "TESTHOST"), None)
            if row and row.get("status") == "up" and row.get("hasToken"):
                break
            time.sleep(0.5)
        else:
            raise unittest.SkipTest("the hub never reported the checked-in peer up: %r" % (rows,))
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
        flag = os.path.join(cls.lab, "remote-new.flag")
        cfg = os.path.join(cls.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.hport, cls.htoken), "nHub": N_HUB,
                       "remoteNew": {"sid": REMOTE_NEW[1], "flag": flag}}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        proc = subprocess.Popen(["node", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        # the linked machine's new session is written HERE, by the lab, when the driver raises its flag: the remote
        # kernel's own state, which the driver does not know the path of
        deadline = time.time() + 400
        while proc.poll() is None and time.time() < deadline:   # loop-ok: bounded by the deadline and the driver's exit
            if os.path.exists(flag) and not os.path.exists(flag + ".done"):
                _session_files(cls.remote["state"], cls.remote["proj"], cls.remote["cwd"], REMOTE_NEW[0], REMOTE_NEW[1], len(REMOTE))
                Path(flag + ".done").write_text("1")
            time.sleep(0.1)
        if proc.poll() is None:
            proc.kill()
        stdout, stderr = proc.communicate()
        if proc.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT:")), None)
        if proc.returncode != 0 or line is None:
            raise AssertionError("driver failed (%r):\n%s\n%s" % (proc.returncode, stdout[-3000:], stderr[-3000:]))
        if os.environ.get("STRIP_SCROLL_DUMP"):
            Path(os.environ["STRIP_SCROLL_DUMP"]).write_text(line[len("RESULT:"):] + "\n")
        return json.loads(line[len("RESULT:"):])

    def _held(self, name, focused=True):
        """The update landed, and the first item in view before it is still on the strip, at the same height in the view
        (the items beside it on its row may change: the rows re-wrap around what was added or removed above)."""
        r = self.result[name]
        b, a = r["before"], r["after"]
        d = "\n  %s: %s" % (name, json.dumps(r))
        self.assertTrue(r["landed"], "the update reached the strip" + d)
        self.assertIsNotNone(b["first"], "an item in view before the update" + d)
        self.assertIsNotNone(r["offNow"], "the item in view is still on the strip" + d)
        self.assertAlmostEqual(r["offNow"], b["first"]["off"], delta=1, msg="the item in view stayed where it was" + d)
        self.assertFalse(a["activeInView"], "the strip was not thrown back to the active tab" + d)
        if focused:
            self.assertEqual(a["focus"], self.result["picked"], "tab mode survives the rebuild: the active tab keeps the focus" + d)
        return r

    def test_the_reader_wheeled_away_from_the_active_tab(self):
        w = self.result["wheeled"]
        d = "\n  %s\n  offsets the strip passed through: %s" % (json.dumps(w), self.result.get("scrolls"))
        self.assertGreater(w["st"], 0, "the strip scrolls and the wheel moved it" + d)
        self.assertLess(w["st"], w["max"], "…short of its bottom" + d)
        self.assertFalse(w["activeInView"], "the picked tab is above the view" + d)
        self.assertEqual(w["focus"], self.result["picked"], "…and holds the focus" + d)

    def test_a_status_change_leaves_the_strip_where_it_was(self):
        r = self._held("status")
        self.assertAlmostEqual(r["after"]["st"], r["before"]["st"], delta=1, msg="nothing moved above the view: the scroll is unchanged\n  " + json.dumps(r))

    def test_sessions_added_above_the_view_keep_the_first_visible_item_in_place(self):
        r = self._held("added")
        self.assertTrue(self.result["addedAbove"], "the new sessions sit above the item in view\n  " + json.dumps(r))
        self.assertGreater(r["after"]["st"], r["before"]["st"] + 1, "the strip scrolled by what was added above\n  " + json.dumps(r))

    def test_sessions_removed_above_the_view_keep_the_first_visible_item_in_place(self):
        self.assertEqual(len(self.result["goneIds"]), 4, "four sessions above the view to remove: %r" % self.result["goneIds"])
        r = self._held("removed")
        self.assertLess(r["after"]["st"], r["before"]["st"] - 1, "the strip scrolled by what left above\n  " + json.dumps(r))

    def test_a_linked_machines_update_leaves_the_strip_where_it_was(self):
        r = self._held("linked")
        self.assertAlmostEqual(r["after"]["st"], r["before"]["st"], delta=1, msg="the new session lands below: the scroll is unchanged\n  " + json.dumps(r))

    def test_the_anchor_holds_with_the_focus_outside_the_strip(self):
        r = self._held("addedUnfocused", focused=False)
        self.assertEqual(r["after"]["focus"], "composer-input", "the message box kept the focus\n  " + json.dumps(r))

    def test_escape_into_tab_mode_still_brings_the_active_tab_into_view(self):
        e = self.result["escape"]
        d = "\n  " + json.dumps(e)
        self.assertEqual(e["focus"], self.result["picked"], "Escape focused the active tab" + d)
        self.assertTrue(e["activeInView"], "…and the strip scrolled it into view" + d)


if __name__ == "__main__":
    unittest.main()
