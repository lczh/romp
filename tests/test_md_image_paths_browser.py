#!/usr/bin/env python3
"""A picture a message embeds as markdown with a FILE PATH for its source (`![plot](/tmp/run/loss.png)`) renders, the
way the same path on a line of its own already did (2026-11-12). Before the fix marked wrote the path into the <img> as
written, the browser asked the dashboard's own origin for `/tmp/run/loss.png`, the kernel answered 404, and the
markdown-image error handler parked the picture as its alt text for the page life (a `/tmp/...` address is no route
the kernel serves, so it never got a retry). Every form failed: absolute under /tmp, absolute under $HOME, `~/...`, and
relative to the session's folder; and through federation, where the hub's origin knows nothing of a remote session's
disk. The chat now loads such a picture through the kernel's /file route for the session the message belongs to
(md-images.ts mdImgPathPass), a remote session's through the hub's /remote/<host>/file relay.

A hub kernel (host HUB, its HOME a directory of the lab's) serves a synthetic notes-api session, web, whose reply embeds
one picture per form, one whose file does not exist yet, and a plain path line whose figure is the reference. A second
kernel checked in as host TESTHOST owns the session api, whose reply embeds pictures that exist on ITS disk only. The
driver, in the full and the compact chat:
  every picture loads (a 200 from /file naming the file and the owning session), wears the path preview's box (display,
  radius, margins, frame, cursor) and opens the lightbox on the file, its path in the bar;
  the picture whose file appears after the page has drawn it heals through the bounded retry the error handler gives a
  URL the kernel serves (a path the browser resolved against the page never got one);
  the remote session's pictures load through the hub's relay with the remote kernel's bare sid.

MDIP_DIST=<dir> serves another tree's UI bundle (the red run's before); MDIP_SHOTS=<dir> writes each leg's reply.
Skips LOUDLY without the extension deps or a Playwright browser (CI sets ROMP_SERVED_TESTS_REQUIRE=1 and installs both,
so a skip there is a failure). Synthetic throughout: invented prose, run-time PNGs, placeholder sids.
"""
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zlib
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tests.dist_copy import copy_dist

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)
from test_comment_relay_mark_served import user, agent, _free_port   # noqa: E402  the same synthetic records

WEB = "aaaaaaaa-1111-2222-3333-4d1a6e1b0c66"
API = "cccccccc-1111-2222-3333-4d1a6e1b0c66"
LOCAL_ALTS = ("A-tmp", "C-home", "D-tilde", "E-rel")
REMOTE_ALTS = ("R-abs", "R-rel")


def _png(w, h, rgb):
    """An opaque PNG, assembled at run time (no binary fixture in the repo)."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
if (cfg.shots) fs.mkdirSync(cfg.shots, { recursive: true });
const out = { legs: {}, errs: {} };

const open = async (compact, hash) => {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  await ctx.addInitScript(({ c }) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: "classic", compact: c })); } catch (e) {} }, { c: compact });
  const page = await ctx.newPage();
  const errs = [], files = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  page.on("response", (r) => {
    const u = new URL(r.url());
    if (u.pathname === "/file" || /^\/remote\/[^/]+\/file$/.test(u.pathname) || /\.png$/.test(u.pathname))
      files.push({ status: r.status(), route: u.pathname, path: u.searchParams.get("path"), sid: u.searchParams.get("sid"), pin: !!u.searchParams.get("pin") });
  });
  await page.goto(cfg.chat + (hash || ""));
  return { ctx, page, errs, files };
};
const loaded = (page, alts, ms) => page.waitForFunction((as) => as.every((a) => {
  const i = document.querySelector('img[alt="' + a + '"]');
  return !!(i && i.complete && i.naturalWidth > 0 && !i.classList.contains("md-img-failed"));
}), alts, { timeout: ms }).catch(() => {});
const measure = (page, alts) => page.evaluate((as) => {
  const style = (e) => { const cs = getComputedStyle(e); return { display: cs.display, radius: cs.borderRadius,
    margin: [cs.marginTop, cs.marginRight, cs.marginBottom, cs.marginLeft].join(" "), cursor: cs.cursor,
    border: [cs.borderTopWidth, cs.borderTopStyle, cs.borderTopColor].join(" "), background: cs.backgroundColor }; };
  const one = (e) => e ? { src: e.getAttribute("src"), parked: e.dataset.mdSrc || null, cls: e.className, complete: e.complete,
    nw: e.naturalWidth, nh: e.naturalHeight, h: e.getBoundingClientRect().height, w: e.getBoundingClientRect().width,
    file: e.dataset.mdPath || null, style: style(e) } : null;
  const pics = {};
  for (const a of as) pics[a] = one(document.querySelector('img[alt="' + a + '"]'));
  return { pics, path: one(document.querySelector(".path-full-img")) };
}, alts);

// ── the hub's own session, web: every form of disk path, in the full chat and the compact one ──
for (const compact of [false, true]) {
  const key = compact ? "compact" : "full";
  try {
    const { ctx, page, errs, files } = await open(compact, "");
    await page.waitForSelector('img[alt="A-tmp"]', { timeout: 60000 });
    await loaded(page, cfg.localAlts, 20000);
    await page.waitForFunction(() => { const i = document.querySelector(".path-full-img"); return !!(i && i.complete && i.naturalWidth > 0); }, null, { timeout: 20000 }).catch(() => {});
    await page.waitForTimeout(250);
    const leg = await measure(page, cfg.localAlts);
    // the picture whose file appears only now: parked as its caption first, then the retry the kernel's route earns
    if (!compact) {
      await page.waitForSelector('img[alt="F-late"].md-img-failed', { timeout: 15000 }).catch(() => {});
      leg.lateParked = await page.evaluate(() => { const i = document.querySelector('img[alt="F-late"]'); return i ? { cls: i.className, parked: i.dataset.mdSrc || null } : null; });
      fs.mkdirSync(cfg.lateDir, { recursive: true });
      fs.writeFileSync(cfg.lateFile, Buffer.from(cfg.latePng, "base64"));
      await loaded(page, ["F-late"], 30000);
      leg.late = (await measure(page, ["F-late"])).pics["F-late"];
    }
    // the lightbox, opened from a picture: the FILE, its path in the bar
    const first = page.locator('img[alt="A-tmp"]');
    leg.lightbox = null;
    if (await first.evaluate((i) => i.classList.contains("md-img-zoom")).catch(() => false)) {
      await first.click();
      await page.waitForSelector("#romp-lightbox .romp-lightbox-img", { timeout: 5000 }).catch(() => {});
      leg.lightbox = await page.evaluate(() => {
        const w = document.getElementById("romp-lightbox");
        if (!w) return null;
        const name = w.querySelector(".romp-lightbox-name"), img = w.querySelector(".romp-lightbox-img");
        return { title: name ? name.title : null, src: img ? img.getAttribute("src") : null, text: w.innerText.slice(0, 300) };
      });
      await page.keyboard.press("Escape");
    }
    if (cfg.shots) {
      await page.evaluate(() => { const i = document.querySelector('img[alt="A-tmp"]'); if (i) i.closest(".assistant").scrollIntoView({ block: "start" }); });
      await page.mouse.move(600, 880);
      await page.screenshot({ path: cfg.shots + "/md-image-paths-" + key + ".png" });
    }
    leg.files = files;
    out.legs[key] = leg;
    out.errs[key] = errs;
    await ctx.close();
  } catch (e) { out.legs[key + "Err"] = String(e && e.stack || e).slice(0, 1500); }
}

// ── the remote session, api, through the hub: its pictures are files on TESTHOST's disk ──
try {
  const { ctx, page, errs, files } = await open(false, "");
  // the remote host's tab appears once the hub reports the check-in up and the relay socket is open
  const remoteTab = page.locator("#tabs .tab", { hasText: "TESTHOST" }).first();
  await remoteTab.waitFor({ timeout: 45000 });
  await remoteTab.click();
  await page.waitForSelector('img[alt="R-abs"]', { timeout: 30000 });
  await loaded(page, cfg.remoteAlts, 20000);
  await page.waitForTimeout(250);
  const leg = await measure(page, cfg.remoteAlts);
  leg.active = await page.evaluate(() => { const t = document.querySelector("#tabs .tab.active[data-id]"); return t ? t.dataset.id : null; });
  if (cfg.shots) {
    await page.evaluate(() => { const i = document.querySelector('img[alt="R-abs"]'); if (i) i.closest(".assistant").scrollIntoView({ block: "start" }); });
    await page.mouse.move(600, 880);
    await page.screenshot({ path: cfg.shots + "/md-image-paths-remote.png" });
  }
  leg.files = files;
  out.legs.remote = leg;
  out.errs.remote = errs;
  await ctx.close();
} catch (e) { out.legs.remoteErr = String(e && e.stack || e).slice(0, 1500); }

fs.writeFileSync(cfg.out, JSON.stringify(out));
await browser.close();
process.exit(0);
"""


def _kernel(lab, name, port, token, dist, sid=None, sess_name=None, cwd=None, records=None, **seams):
    """Boot one hermetic kernel: its own state root and dist, with one session whose transcript is `records`."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    for d in ("names", "sdk", "states", "comments"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    Path(state, "session-hosts").write_text("off\n")   # a lab root of its own: no per-session host process (CLAUDE.md, 2026-09-11)
    Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
    Path(state, "names", sid).write_text("%s\t%s\t#9cd2ff\t#0c1a2e\n" % (sess_name, cwd))
    Path(state, "sdk", sid + ".json").write_text(json.dumps(
        {"sid": sid, "name": sess_name, "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": sid, "alive": True,
         "model": "claude-opus-5", "liveModel": "Opus 5"}))
    Path(state, "session-order.json").write_text(json.dumps([sid]))
    proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
    os.makedirs(proj, exist_ok=True)
    Path(proj, sid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    env = _lab.kernel_env(os.path.join(lab, name), claude, dist, port, token, ROMP_HOST_NAME=name.upper(), **seams)
    log = os.path.join(lab, name + "-kernel.log")
    proc = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(log, "w"), stderr=subprocess.STDOUT, env=env)
    for _ in range(120):
        try:
            urllib.request.urlopen("http://127.0.0.1:%d/healthz" % port, timeout=1)
            return proc, log
        except Exception:
            time.sleep(0.5)
    proc.kill()
    proc.wait()
    raise unittest.SkipTest("hermetic kernel %s never served /healthz here" % name)


class ServedMarkdownImagePaths(unittest.TestCase):
    maxDiff = None
    result = None

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
        cls.lab = tempfile.mkdtemp(prefix="md-image-paths-")
        cls.procs = []
        before = os.environ.get("MDIP_DIST", "")
        if before:
            src = before
        else:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
            src = os.path.join(EXT, "dist")
        dist = os.path.join(cls.lab, "dist")
        copy_dist(src, dist)
        t0 = int(time.time()) - 900
        # the hub: its session's folder, a HOME of the lab's own (so `~` and $HOME name files the lab wrote), a folder under
        # the system's temp directory for the absolute form
        hub = os.path.join(cls.lab, "hub")
        cls.home = os.path.join(hub, "home")
        cls.cwd = os.path.join(hub, "notes-api")
        cls.tmpfig = os.path.join(hub, "figs", "latency-a.png")
        files = {cls.tmpfig: _png(600, 400, (40, 120, 200)),
                 os.path.join(cls.home, "figs", "hit-rate-c.png"): _png(600, 400, (200, 90, 40)),
                 os.path.join(cls.home, "figs", "p95-d.png"): _png(400, 600, (60, 160, 90)),
                 os.path.join(cls.cwd, "docs", "split-e.png"): _png(600, 400, (150, 60, 170)),
                 os.path.join(cls.cwd, "docs", "figure.png"): _png(600, 400, (84, 178, 4))}
        for p, data in files.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Path(p).write_bytes(data)
        cls.late_file = os.path.join(cls.cwd, "figs", "late-f.png")   # written by the driver once the page has drawn its picture
        cls.expect = {"A-tmp": cls.tmpfig, "C-home": os.path.join(cls.home, "figs", "hit-rate-c.png"),
                      "D-tilde": "~/figs/p95-d.png", "E-rel": "docs/split-e.png"}
        reply = ("Here are the notes-api plots.\n\n"
                 "The latency run:\n\n![A-tmp](%s)\n\n"
                 "The cache hit rate:\n\n![C-home](%s)\n\n"
                 "The p95 per region:\n\n![D-tilde](~/figs/p95-d.png)\n\n"
                 "The split by route:\n\n![E-rel](docs/split-e.png)\n\n"
                 "The rerun, still writing:\n\n![F-late](figs/late-f.png)\n\n"
                 "The raw counts:\n\ndocs/figure.png\n") % (cls.expect["A-tmp"], cls.expect["C-home"])
        web = [user(t0, "u0", None, "plot the notes-api latency, hit rate and p95", WEB), agent(t0 + 5, "a0", "u0", reply, WEB)]
        cls.hport, cls.htoken = _free_port(), "testtok-mdip-hub"
        hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken, dist, sid=WEB, sess_name="web", cwd=cls.cwd, records=web,
                               HOME=cls.home)
        cls.procs.append(hp)
        # the remote machine: its session's pictures exist on its disk only, one absolute and one relative to its folder
        rem = os.path.join(cls.lab, "testhost")
        rcwd = os.path.join(rem, "proj")
        cls.rabs = os.path.join(rem, "plots", "remote-abs.png")
        for p, data in {cls.rabs: _png(600, 400, (200, 160, 40)), os.path.join(rcwd, "plots", "remote-rel.png"): _png(600, 400, (40, 160, 200))}.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Path(p).write_bytes(data)
        cls.rexpect = {"R-abs": cls.rabs, "R-rel": "plots/remote-rel.png"}
        api = [user(t0, "v0", None, "show me the notes-api retry plots", API),
               agent(t0 + 5, "b0", "v0", "The retry plots.\n\n![R-abs](%s)\n\n![R-rel](plots/remote-rel.png)\n" % cls.rabs, API)]
        cls.rport, cls.rtoken = _free_port(), "testtok-mdip-remote"
        rp, cls.rlog = _kernel(cls.lab, "testhost", cls.rport, cls.rtoken, dist, sid=API, sess_name="api", cwd=rcwd, records=api)
        cls.procs.append(rp)
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
        import base64
        cfg = os.path.join(cls.lab, "cfg.json")
        out = os.path.join(cls.lab, "result.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (cls.hport, cls.htoken), "out": out,
                       "shots": os.environ.get("MDIP_SHOTS", ""), "localAlts": list(LOCAL_ALTS), "remoteAlts": list(REMOTE_ALTS),
                       "lateDir": os.path.dirname(cls.late_file), "lateFile": cls.late_file,
                       "latePng": base64.b64encode(_png(600, 400, (220, 200, 60))).decode()}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        try:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=360,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        except subprocess.TimeoutExpired as e:
            raise AssertionError("driver ran past its 360 s cap:\n%s\nhub:\n%s" % (str(e.stdout)[-2000:], open(cls.hlog).read()[-2000:]))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        if p.returncode != 0 or not os.path.exists(out):
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nhub:\n" + open(cls.hlog).read()[-1500:])
        return json.loads(Path(out).read_text())

    def _leg(self, key):
        r = self._run()
        self.assertNotIn(key + "Err", r["legs"], r["legs"].get(key + "Err"))
        return r["legs"][key]

    @staticmethod
    def _query(src):
        u = urlsplit(src or "")
        q = parse_qs(u.query)
        return u.path, (q.get("path") or [None])[0], (q.get("sid") or [None])[0], (q.get("pin") or [None])[0]

    def test_every_form_of_disk_path_loads_from_the_kernels_file_route_in_the_full_and_the_compact_chat(self):
        for key in ("full", "compact"):
            leg = self._leg(key)
            for alt in LOCAL_ALTS:
                m = leg["pics"][alt]
                table = "\n  %s %s: %s" % (key, alt, json.dumps(m))
                self.assertIsNotNone(m, "the picture rendered" + table)
                route, path, sid, _pin = self._query(m["src"])
                self.assertEqual((route, path, sid), ("/file", self.expect[alt], WEB),
                                 "the picture loads its file through the hub's /file for its own session (the kernel expands ~ "
                                 "and resolves a relative path against the session's folder)" + table)
                self.assertTrue(m["complete"] and m["nw"] > 0 and "md-img-failed" not in m["cls"].split(), "…and it loaded" + table)
                ok = [f for f in leg["files"] if f["route"] == "/file" and f["path"] == self.expect[alt] and f["sid"] == WEB]
                self.assertTrue(ok and all(f["status"] == 200 for f in ok), "a 200 from /file for it, nothing else%s\n  files: %s"
                                % (table, json.dumps(leg["files"])))
            self.assertFalse([f for f in leg["files"] if f["route"] != "/file" and f["route"].endswith(".png")],
                             "no picture is asked of the dashboard's own origin by its path: %s" % json.dumps(leg["files"]))
            self.assertIsNotNone(self._query(leg["pics"]["A-tmp"]["src"])[3],
                                 "a path the kernel verified carries the message's mention-time pin, as a path line's figure does")

    def test_each_wears_the_path_previews_box_and_opens_the_lightbox_on_its_file(self):
        for key in ("full", "compact"):
            leg = self._leg(key)
            ref = leg["path"]
            self.assertIsNotNone(ref, "the plain path line's figure rendered: the reference box (%s)" % key)
            for alt in LOCAL_ALTS:
                m = leg["pics"][alt]
                table = "\n  %s %s: %s\n  path preview: %s" % (key, alt, json.dumps(m), json.dumps(ref))
                cls = m["cls"].split()
                self.assertTrue({"md-img", "md-solo", "md-img-zoom"} <= set(cls), "a picture alone on its line, zoomable" + table)
                self.assertEqual(m["style"], ref["style"], "the path preview's box: display, radius, margins, frame, background, cursor" + table)
                self.assertLessEqual(m["h"], 320.5, "within the path preview's 320px" + table)
            lb = leg["lightbox"]
            self.assertIsNotNone(lb, "a click on the picture opened the lightbox (%s)" % key)
            self.assertEqual(lb["title"], self.expect["A-tmp"], "the lightbox names the FILE in its bar, as for a path's figure: %r" % lb)
            self.assertEqual(self._query(lb["src"])[:3], ("/file", self.expect["A-tmp"], WEB), "…and shows it from the kernel: %r" % lb)

    def test_a_picture_whose_file_appears_later_heals_through_the_retry_a_kernel_route_earns(self):
        leg = self._leg("full")
        self.assertIsNotNone(leg.get("lateParked"), "the picture of the missing file rendered")
        self.assertIn("md-img-failed", (leg["lateParked"]["cls"] or "").split(), "parked as its caption while the file is missing: %r" % leg["lateParked"])
        late = leg.get("late")
        table = "\n  late: %s" % json.dumps(late)
        self.assertTrue(late and late["complete"] and late["nw"] > 0 and "md-img-failed" not in late["cls"].split(),
                        "once the file exists the picture lands, through the bounded retry for a URL the kernel serves" + table)
        route, path, sid, _pin = self._query(late["src"])
        self.assertEqual((route, sid), ("/file", WEB), table)
        self.assertEqual(path, "figs/late-f.png", table)

    def test_a_remote_sessions_pictures_load_from_its_own_machine_through_the_hubs_relay(self):
        leg = self._leg("remote")
        self.assertEqual(leg["active"], "TESTHOST:" + API, "the remote session's tab is the one on screen, under its host prefix")
        for alt in REMOTE_ALTS:
            m = leg["pics"][alt]
            table = "\n  remote %s: %s\n  files: %s" % (alt, json.dumps(m), json.dumps(leg["files"]))
            self.assertIsNotNone(m, "the remote session's picture rendered" + table)
            route, path, sid, _pin = self._query(m["src"])
            self.assertEqual((route, path, sid), ("/remote/TESTHOST/file", self.rexpect[alt], API),
                             "the OWNING machine's file, through the hub's relay, with the bare sid that kernel knows" + table)
            self.assertTrue(m["complete"] and m["nw"] > 0 and "md-img-failed" not in m["cls"].split(), "…and it loaded" + table)
            ok = [f for f in leg["files"] if f["route"] == "/remote/TESTHOST/file" and f["path"] == self.rexpect[alt]]
            self.assertTrue(ok and all(f["status"] == 200 for f in ok), "a 200 through the relay" + table)

    def test_no_page_errors(self):
        r = self._run()
        for key, errs in r["errs"].items():
            self.assertEqual(errs, [], "page errors in the %s leg" % key)


if __name__ == "__main__":
    unittest.main()
