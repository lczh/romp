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
  URL the kernel serves (a path the browser resolved against the page never got one), and is measured once it shows on
  the pinned address the kernel latches when the file exists (measured earlier, the CI flake the review of #454 found);
  a picture the page showed before the kernel pinned its file keeps showing, on no frame empty, until the pinned bytes have
  loaded (the driver holds that ask to measure under it);
  a src naming a file that is no picture (`.env`, a `.json`) stays as written and the kernel's file route is never asked;
  an incoming mail's picture keeps its src (the sender's path is no file in the reader's folder), a sent mail's loads
  from the writer's own;
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
from test_comment_relay_mark_served import user, agent, iso as _iso, _free_port   # noqa: E402  the same synthetic records

WEB = "aaaaaaaa-1111-2222-3333-4d1a6e1b0c66"
API = "cccccccc-1111-2222-3333-4d1a6e1b0c66"
TESTS = "dddddddd-1111-2222-3333-4d1a6e1b0c66"   # a peer that mails web; it exists only in the hub's message log
LOCAL_ALTS = ("A-tmp", "C-home", "D-tilde", "E-rel")
REMOTE_ALTS = ("R-abs", "R-rel")
WATCH_ALTS = ("K-env", "K-json")                  # pictures whose src names a file that is no picture
MAIL_ALTS = ("P-mail", "P-out")                   # a picture in mail web received, and in mail web sent
BAR = "#" * 44


def _mail_banner(frm, kind, mid, body, t):
    """The delivered banner the postal service injects (bin/romp-postal-service format_push), as a user message's text."""
    return "\n".join([BAR, "## \U0001F4EC from %s \u00b7 %s" % (frm, time.strftime("%H:%M", time.localtime(t))), BAR, body,
                      "<!-- romp-msg-id: %s -->" % mid, "<!-- romp-msg-kind: %s -->" % kind, BAR,
                      "(to reply, only if substantive: romp mail send --kind delegate|coordinate|question %s \"...\")" % frm])


def _mail_records(t, parent):
    """web's mail: one question from tests whose picture names `figs/mail-p.png` (a path in TESTS' folder, which the reader's
    folder also holds), then web's own reply with a picture from its folder; the transcript records and the log's rows."""
    inbody = "Is this the p95 split you meant for the README?\n\n![P-mail](figs/mail-p.png)\n"
    outbody = "Close: here is the split from my run, for the README.\n\n![P-out](figs/mail-out.png)\n"
    log = [{"t": t, "ev": "sent", "id": "m-in1", "from": "tests", "from_id": TESTS, "to_id": WEB, "body": inbody, "kind": "question", "from_host": ""},
           {"t": t + 20, "ev": "sent", "id": "m-out1", "from": "web", "from_id": WEB, "to_id": TESTS, "body": outbody, "kind": "coordinate", "from_host": ""}]
    tu = "tu_mail_out1"
    recs = [{"type": "user", "timestamp": _iso(t + 1), "uuid": "m0", "parentUuid": parent, "promptSource": "sdk", "sessionId": WEB,
             "message": {"role": "user", "content": _mail_banner("tests", "question", "m-in1", inbody, t)}},
            {"type": "assistant", "timestamp": _iso(t + 20), "uuid": "a1", "parentUuid": "m0", "sessionId": WEB,
             "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "tool_use",
                         "content": [{"type": "tool_use", "id": tu, "name": "mcp__romp-postal-service__send_message",
                                      "input": {"to": "tests", "body": outbody, "kind": "coordinate"}}]}},
            {"type": "user", "timestamp": _iso(t + 21), "uuid": "a1r", "parentUuid": "a1", "sessionId": WEB,
             "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu, "content": "Delivered to 'tests'.", "is_error": False}]}}]
    return log, recs


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

const open = async (compact, hash, routes) => {
  const ctx = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  await ctx.addInitScript(({ c }) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: "classic", compact: c })); } catch (e) {} }, { c: compact });
  const page = await ctx.newPage();
  const errs = [], files = [], asked = [];
  page.on("pageerror", (e) => errs.push(String(e && e.stack || e).slice(0, 600)));
  page.on("response", (r) => {
    const u = new URL(r.url());
    if (u.pathname === "/file" || /^\/remote\/[^/]+\/file$/.test(u.pathname) || /\.png$/.test(u.pathname))
      files.push({ status: r.status(), route: u.pathname, path: u.searchParams.get("path"), sid: u.searchParams.get("sid"), pin: !!u.searchParams.get("pin") });
  });
  // every request naming a file that is no picture (K-env, K-json), whether the page's own address or the kernel's file route
  page.on("request", (q) => {
    const u = new URL(q.url());
    if (cfg.watch.some((w) => u.pathname.endsWith(w) || (u.searchParams.get("path") || "").endsWith(w))) asked.push({ route: u.pathname, path: u.searchParams.get("path") });
  });
  if (routes) await routes(page);
  await page.goto(cfg.chat + (hash || ""));
  return { ctx, page, errs, files, asked };
};
const loaded = (page, alts, ms) => page.waitForFunction((as) => as.every((a) => {
  const i = document.querySelector('img[alt="' + a + '"]');
  return !!(i && i.complete && i.naturalWidth > 0 && !i.classList.contains("md-img-failed"));
}), alts, { timeout: ms }).catch(() => {});
// …on its PINNED address: the kernel latches a pin on its first build after the file exists (kernel.py _path_links), and the
// repaint that carries it changes the picture's src; a picture loaded before that is not yet the one the message keeps
const loadedPinned = (page, alt, ms) => page.waitForFunction((a) => {
  const i = document.querySelector('img[alt="' + a + '"]');
  return !!(i && /[?&]pin=/.test(i.getAttribute("src") || "") && i.complete && i.naturalWidth > 0 && !i.classList.contains("md-img-failed"));
}, alt, { timeout: ms }).catch(() => {});
const measure = (page, alts) => page.evaluate((as) => {
  const style = (e) => { const cs = getComputedStyle(e); return { display: cs.display, radius: cs.borderRadius,
    margin: [cs.marginTop, cs.marginRight, cs.marginBottom, cs.marginLeft].join(" "), cursor: cs.cursor,
    border: [cs.borderTopWidth, cs.borderTopStyle, cs.borderTopColor].join(" "), background: cs.backgroundColor }; };
  const one = (e) => e ? { src: e.getAttribute("src"), parked: e.dataset.mdSrc || null, cls: e.className, complete: e.complete,
    nw: e.naturalWidth, nh: e.naturalHeight, h: e.getBoundingClientRect().height, w: e.getBoundingClientRect().width,
    file: e.dataset.mdPath || null, sid: e.dataset.mdSid || null, pin: e.dataset.mdPin || null, standIn: e.dataset.mdPinned || null,
    style: style(e) } : null;
  const pics = {};
  for (const a of as) pics[a] = one(document.querySelector('img[alt="' + a + '"]'));
  return { pics, path: one(document.querySelector(".path-full-img")) };
}, alts);

// ── the hub's own session, web: every form of disk path, in the full chat and the compact one ──
// G-pin's file is not on disk when the page draws it, so the kernel has no pin for it; the page's unpinned ask is answered here
// with the file's bytes, the window in which the page shows a file the kernel's next build has not pinned yet (the late
// picture's probe can land in it). Its PINNED ask is held until the driver has measured the picture under it.
let pinAsked = null, releasePin = () => {};
const holdPin = async (page) => {
  let seen;
  pinAsked = new Promise((r) => { seen = r; });
  const gate = new Promise((r) => { releasePin = r; });
  await page.route((u) => u.pathname === "/file" && u.searchParams.get("path") === cfg.standinTok, async (route) => {
    if (!new URL(route.request().url()).searchParams.get("pin"))
      return route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from(cfg.standinPng, "base64") });
    seen();
    await gate;
    return route.continue();
  });
};
const within = (p, ms) => Promise.race([p.then(() => true), new Promise((r) => setTimeout(() => r(false), ms))]);
for (const compact of [false, true]) {
  const key = compact ? "compact" : "full";
  try {
    const { ctx, page, errs, files, asked } = await open(compact, "", compact ? null : holdPin);
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
      await loadedPinned(page, "F-late", 30000);
      leg.late = (await measure(page, ["F-late"])).pics["F-late"];
      // the picture the page showed before the kernel pinned it: its height is read on every frame from here until the
      // pinned bytes have landed, and the picture is measured while the pinned ask is held
      await loaded(page, ["G-pin"], 20000);
      leg.standinFirst = (await measure(page, ["G-pin"])).pics["G-pin"];
      await page.evaluate(() => {
        window.__standinH = [];
        const tick = () => {
          const i = document.querySelector('img[alt="G-pin"]');
          window.__standinH.push(i ? i.getBoundingClientRect().height : -1);
          if (!window.__standinStop) requestAnimationFrame(tick);
        };
        requestAnimationFrame(tick);
      });
      fs.writeFileSync(cfg.standinFile, Buffer.from(cfg.standinPng, "base64"));
      leg.standinAsked = await within(pinAsked, 30000);
      await page.waitForFunction(() => { const i = document.querySelector('img[alt="G-pin"]'); return !!(i && i.dataset.mdPin); }, null, { timeout: 10000 }).catch(() => {});
      await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));   // two painted frames under the held ask
      leg.standinHeld = (await measure(page, ["G-pin"])).pics["G-pin"];
      releasePin();
      await loadedPinned(page, "G-pin", 20000);
      leg.standinLanded = (await measure(page, ["G-pin"])).pics["G-pin"];
      leg.standinFrames = await page.evaluate(() => {
        window.__standinStop = true;
        const hs = window.__standinH || [];
        return { n: hs.length, zero: hs.filter((h) => !(h > 0)).length, min: hs.length ? Math.min(...hs) : null };
      });
    }
    leg.watched = await measure(page, cfg.watchAlts);
    leg.mail = await measure(page, cfg.mailAlts);
    leg.asked = asked;
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


def _kernel(lab, name, port, token, dist, sid=None, sess_name=None, cwd=None, records=None, log=None, **seams):
    """Boot one hermetic kernel: its own state root and dist, with one session whose transcript is `records`, and `log`
    as its postal message log (the clean bodies its mail cards read)."""
    state = os.path.join(lab, name, "xdg", "romp")
    claude = os.path.join(lab, name, "claude")
    for d in ("names", "sdk", "states", "comments", "timeline"):
        os.makedirs(os.path.join(state, d), exist_ok=True)
    if log:
        Path(state, "timeline", "messages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in log))
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
                 os.path.join(cls.cwd, "docs", "figure.png"): _png(600, 400, (84, 178, 4)),
                 # files that are no picture, which a rewrite would have fetched from the session's disk (the kernel's
                 # /file answers a text file with its text)
                 os.path.join(cls.cwd, ".env"): b"NOTES_API_MODE=demo\n",
                 os.path.join(cls.home, "figs", "creds.json"): b'{"mode": "demo"}\n',
                 # the reader's own copies of the mail's paths: an incoming mail's relative path resolved against the reader
                 # showed this one, the reader's, for a file the sender meant in its own folder
                 os.path.join(cls.cwd, "figs", "mail-p.png"): _png(600, 400, (230, 40, 40)),
                 os.path.join(cls.cwd, "figs", "mail-out.png"): _png(600, 400, (40, 200, 120))}
        for p, data in files.items():
            os.makedirs(os.path.dirname(p), exist_ok=True)
            Path(p).write_bytes(data)
        cls.late_file = os.path.join(cls.cwd, "figs", "late-f.png")   # written by the driver once the page has drawn its picture
        cls.standin_file = os.path.join(cls.cwd, "figs", "standin-g.png")   # …and this one once the page shows it (holdPin)
        cls.expect = {"A-tmp": cls.tmpfig, "C-home": os.path.join(cls.home, "figs", "hit-rate-c.png"),
                      "D-tilde": "~/figs/p95-d.png", "E-rel": "docs/split-e.png"}
        reply = ("Here are the notes-api plots.\n\n"
                 "The latency run:\n\n![A-tmp](%s)\n\n"
                 "The cache hit rate:\n\n![C-home](%s)\n\n"
                 "The p95 per region:\n\n![D-tilde](~/figs/p95-d.png)\n\n"
                 "The split by route:\n\n![E-rel](docs/split-e.png)\n\n"
                 "The rerun, still writing:\n\n![F-late](figs/late-f.png)\n\n"
                 "The cache sweep, from the run the page saw first:\n\n![G-pin](figs/standin-g.png)\n\n"
                 "The settings it ran with:\n\n![K-env](.env)\n\n![K-json](~/figs/creds.json)\n\n"
                 "The raw counts:\n\ndocs/figure.png\n") % (cls.expect["A-tmp"], cls.expect["C-home"])
        # the mail first and the reply last: the late pictures sit in the transcript's tail, which every build re-reads (a
        # message sealed above the tail stopped retrying its second late path once its first had resolved, in this lab)
        mail_log, mail_recs = _mail_records(t0 + 2, "u0")
        web = [user(t0, "u0", None, "plot the notes-api latency, hit rate and p95", WEB)] + mail_recs + [agent(t0 + 30, "a0", "a1r", reply, WEB)]
        cls.hport, cls.htoken = _free_port(), "testtok-mdip-hub"
        hp, cls.hlog = _kernel(cls.lab, "hub", cls.hport, cls.htoken, dist, sid=WEB, sess_name="web", cwd=cls.cwd, records=web,
                               log=mail_log, HOME=cls.home)
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
                       "latePng": base64.b64encode(_png(600, 400, (220, 200, 60))).decode(),
                       "standinTok": "figs/standin-g.png", "standinFile": cls.standin_file,
                       "standinPng": base64.b64encode(_png(600, 400, (60, 200, 220))).decode(),
                       "watch": [".env", "creds.json"], "watchAlts": list(WATCH_ALTS), "mailAlts": list(MAIL_ALTS)}, f)
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

    @staticmethod
    def _written(m):
        """The address a picture the chat left alone points at, as written: its src attribute, or once its load failed and
        the error handler parked it as its caption, the parked address (absolute) read back to the page-relative path."""
        if m.get("src"):
            return m["src"]
        return urlsplit(m.get("parked") or "").path.lstrip("/")

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
            self.assertFalse([f for f in leg["files"] if f["route"] != "/file" and f["route"].endswith(".png") and f["route"] != "/figs/mail-p.png"],
                             "no picture is asked of the dashboard's own origin by its path (but the incoming mail's, which keeps its "
                             "src as written): %s" % json.dumps(leg["files"]))
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
        # Measured once the picture shows on its PINNED address (the driver's loadedPinned): the kernel latches the pin on
        # its first build after the file exists, and the repaint carrying it changes the src. Measured as soon as the probe
        # had landed the unpinned bytes, this read the picture inside that repaint 1 run in 4 to 7 under CI's load (the
        # review of #454), before the stand-in below kept it showing through the swap.
        leg = self._leg("full")
        self.assertIsNotNone(leg.get("lateParked"), "the picture of the missing file rendered")
        self.assertIn("md-img-failed", (leg["lateParked"]["cls"] or "").split(), "parked as its caption while the file is missing: %r" % leg["lateParked"])
        late = leg.get("late")
        table = "\n  late: %s" % json.dumps(late)
        self.assertTrue(late and late["complete"] and late["nw"] > 0 and "md-img-failed" not in late["cls"].split(),
                        "once the file exists the picture lands, through the bounded retry for a URL the kernel serves" + table)
        route, path, sid, pin = self._query(late["src"])
        self.assertEqual((route, sid), ("/file", WEB), table)
        self.assertEqual(path, "figs/late-f.png", table)
        self.assertTrue(pin and late["pin"] == pin, "…on the address with the mention-time pin the kernel latched once the file existed" + table)
        self.assertGreater(late["h"], 0, "…and it shows" + table)

    def test_a_picture_shown_before_its_pin_keeps_showing_until_the_pinned_bytes_have_loaded(self):
        # The page loaded G-pin unpinned (the driver answered that ask: the file was not on disk, so the kernel had no pin),
        # then the file appeared and the kernel's next build pinned it. The repaint that carries the pin drew a fresh <img>
        # on the pinned address, zero-height until those bytes arrived: the picture blinked out, and loaded twice. The
        # driver holds the pinned ask, so the picture is read under it, and its height is read on every frame throughout.
        leg = self._leg("full")
        first, held, landed, frames = leg.get("standinFirst"), leg.get("standinHeld"), leg.get("standinLanded"), leg.get("standinFrames")
        table = "\n  first: %s\n  held: %s\n  landed: %s\n  frames: %s" % tuple(json.dumps(x) for x in (first, held, landed, frames))
        self.assertTrue(first and first["complete"] and first["nw"] > 0 and not self._query(first["src"])[3],
                        "the picture showed first on its unpinned address" + table)
        self.assertTrue(leg.get("standinAsked"), "the kernel pinned the file once it existed, and the page asked for the pinned bytes" + table)
        self.assertTrue(held and held["pin"], "the repaint carrying the pin is on the page" + table)
        self.assertTrue(held["complete"] and held["nw"] > 0 and held["h"] > 0,
                        "while the pinned bytes are on their way, the picture that already showed stays on screen" + table)
        self.assertEqual(self._query(held["src"])[:3], ("/file", "figs/standin-g.png", WEB), "…the same file, unpinned" + table)
        self.assertIsNone(self._query(held["src"])[3], table)
        self.assertTrue(landed and self._query(landed["src"])[3] == landed["pin"] and landed["complete"] and landed["nw"] > 0
                        and landed["h"] > 0 and not landed["standIn"], "then the pinned bytes take its place" + table)
        self.assertTrue(frames and frames["n"] > 0 and frames["zero"] == 0, "and no frame drew it empty, from first sight to the pinned bytes" + table)

    def test_a_src_naming_a_file_that_is_no_picture_stays_as_written_and_the_kernel_is_never_asked_for_it(self):
        # `![x](.env)` and `![x](~/figs/creds.json)` were rewritten to the kernel's /file like any path, and /file answers a
        # text file with its text (the review of #454). Both files exist in the lab, so a rewrite would fetch them.
        for key in ("full", "compact"):
            leg = self._leg(key)
            table = "\n  %s: %s\n  asked: %s" % (key, json.dumps(leg["watched"]["pics"]), json.dumps(leg["asked"]))
            for alt, src in (("K-env", ".env"), ("K-json", "~/figs/creds.json")):
                m = leg["watched"]["pics"][alt]
                self.assertIsNotNone(m, "the picture rendered" + table)
                self.assertEqual((self._written(m), m["file"]), (src, None), "its src as the message wrote it, no file on it" + table)
            self.assertFalse([a for a in leg["asked"] if a["route"] == "/file" or a["route"].endswith("/file")],
                             "the kernel's file route is never asked for either" + table)

    def test_an_incoming_mails_picture_keeps_its_src_and_a_sent_ones_loads_from_the_writers_folder(self):
        # A received mail was written by the PEER: its relative path names a file in the peer's folder, and resolved against
        # the reader it showed the reader's own copy (here a red one). The card carries no sender session to resolve
        # against, so the picture keeps its src as written; the mail the reader SENT resolves against the reader, its writer.
        leg = self._leg("full")
        pics = leg["mail"]["pics"]
        table = "\n  mail: %s\n  files: %s" % (json.dumps(pics), json.dumps(leg["files"]))
        got = pics["P-mail"]
        self.assertIsNotNone(got, "the incoming mail's picture rendered" + table)
        self.assertEqual((self._written(got), got["file"]), ("figs/mail-p.png", None), "its src as the sender wrote it" + table)
        self.assertFalse([f for f in leg["files"] if f["path"] == "figs/mail-p.png"], "the reader's file route is never asked for it" + table)
        sent = pics["P-out"]
        self.assertIsNotNone(sent, "the sent mail's picture rendered" + table)
        self.assertEqual(self._query(sent["src"])[:3], ("/file", "figs/mail-out.png", WEB), "the writer's own file, through its kernel" + table)
        self.assertEqual((sent["file"], sent["sid"]), ("figs/mail-out.png", WEB), table)

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
