#!/usr/bin/env python3
"""The romp wordmark's "o" paints in the same frame as its letters (the user 2026-10-01, who saw R, m and p appear
the moment romp loaded and the spinning o land a beat later, and guessed it was an image).

It was: <img class=rl-o src=/media/romp-swirl-o.svg>, a request of its own after the HTML, and /media is served
no-cache, so even a cached copy waited on a revalidation round trip while the letters painted from the markup.
The swirl now rides inside the page: the kernel's loader CSS carries it as a data URI once per page (the boot
splash, each pane's loader, the spend panel's and the restart flow's copies of the same markup), the /timeline page
declares it for the view's own loader, and esbuild bakes the same URI into the webview bundles (the TS side is
pinned by ui/webview/media-base.test.ts and ui/timeline-loader-swirl.test.ts).

The first class reads the pages off the real handler on a loopback server. The second drives one of them in a
real browser with every request for the swirl file held unanswered, the way a slow link holds it, and reads what
painted; it skips LOUDLY without the extension's node deps or a browser (CI installs none), the way
tests/test_api_health_browser.py does. Synthetic only: no session data is involved."""
import http.client
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
import urllib.parse
from http.server import ThreadingHTTPServer
from romp_load import load_source

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BIN = os.path.join(ROOT, "bin")
EXT = os.path.join(ROOT, "vscode-extension")
# Hermetic state BEFORE the load: it resolves its state root at import time, and only pytest runs conftest's
# floor (a bare unittest or script run otherwise writes REAL state, and a kernel module that can reach a live
# manager port restarts the live kernel).
os.environ["XDG_STATE_HOME"] = tempfile.mkdtemp()
os.environ.pop("ROMP_STATE_DIR", None)  # a live kernel's export outranks the XDG floor
os.environ["ROMP_KERNEL_NO_OPEN"] = "1"
os.environ.setdefault("ROMP_SERVE_TOKEN", "testtok")
os.environ["ROMP_MANAGER_PORT"] = "1"             # a dead port, never an inherited live one
km = load_source("romp_kernel_wordmark_first_paint", os.path.join(BIN, "romp-kernel"))

PREFIX = "data:image/svg+xml,"
SWIRL = os.path.join(EXT, "media", "romp-swirl-o.svg")
# every page that carries the kernel's loader markup: the shell (boot splash) and the panes with _pane_spin
# (/fleet is the outline pane's existing route)
LOADER_PAGES = ("/", "/chat", "/feed", "/fleet", "/artifacts")


def folded(svg):
    """The SVG as the URI carries it: whitespace runs folded to one space (the bytes /media serves otherwise)."""
    return re.sub(r"\s+", " ", re.sub(r">\s+<", "><", svg)).strip()


def swirl_svg():
    with open(SWIRL, encoding="utf-8") as f:
        return folded(f.read())


def decode(uri):
    assert uri.startswith(PREFIX), uri[:60]
    return urllib.parse.unquote(uri[len(PREFIX):])


class _Server:
    def __enter__(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), km.Handler)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("GET", path, headers={"X-Romp-Token": km.TOKEN})
            r = conn.getresponse()
            return r.status, r.read().decode("utf-8")
        finally:
            conn.close()


class ServedPagesCarryTheO(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = {}
        with _Server() as s:
            for path in LOADER_PAGES + ("/timeline",):
                status, body = s.get(path)
                assert status == 200, (path, status, body[:300])
                cls.pages[path] = body

    def test_no_loader_page_asks_the_network_for_the_o(self):
        for path in LOADER_PAGES:
            self.assertNotIn("romp-swirl-o.svg", self.pages[path], "%s: nothing on the page fetches the o" % path)

    def test_the_wordmark_markup_is_r_the_swirl_m_p_with_no_image_to_load(self):
        for path in LOADER_PAGES:
            html = self.pages[path]
            word = re.search(r"<div class=rl-word>(.*?)</div>", html)
            self.assertIsNotNone(word, "%s carries the loader wordmark" % path)
            self.assertEqual(word.group(1),
                             "<span style='color:#1EA1EB'>R</span><span class=rl-o role=img aria-label=o></span>"
                             "<span style='color:#54B204'>m</span><span style='color:#4EA8A9'>p</span>", path)

    def test_the_page_s_own_css_paints_the_swirl_from_a_data_uri_of_the_media_file(self):
        rule = re.compile(r'span\.rl-o\{display:inline-block;background:url\("([^"]*)"\) center/contain no-repeat\}')
        for path in LOADER_PAGES:
            html = self.pages[path]
            m = rule.search(html)
            self.assertIsNotNone(m, "%s: the rule that paints the o rides in the page's own style" % path)
            self.assertEqual(decode(m.group(1)), swirl_svg(), "%s: the same picture /media serves" % path)
            self.assertEqual(html.count(m.group(1)), 1, "%s: one copy per page, however often the markup recurs" % path)
            # the rule's geometry is untouched, so the swirl still sits in the o's slot and spins
            self.assertIn(".rl-o{width:.65em;height:.65em;vertical-align:middle;margin:0 -.0335em;"
                          "animation:rl-spin 7s linear infinite}", html)

    def test_the_landing_s_spliced_copies_of_the_markup_reuse_the_one_rule(self):
        html = self.pages["/"]
        # the spend panel's loader and the restart flow's rebuilt splash are the same markup, JSON-spliced
        spliced = json.dumps(km._loader_inner())
        self.assertGreaterEqual(html.count(spliced), 2)
        self.assertNotIn(PREFIX, spliced, "the markup itself carries no picture, so the copies cost no bytes")

    def test_the_timeline_page_declares_the_swirl_before_the_view_that_reads_it(self):
        html = self.pages["/timeline"]
        m = re.search(r"var __ROMP_SWIRL_O__=(\"[^\"]*\");var module=", html)
        self.assertIsNotNone(m, "the page declares the inline swirl ahead of the view module")
        self.assertEqual(decode(json.loads(m.group(1))), swirl_svg())
        self.assertLess(m.start(), html.index("function swirlOUrl()"))
        self.assertIn("o.src = swirlOUrl();", html, "the view's loader takes its o from it")


DRIVER = r"""
import { createRequire } from "node:module";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const base = process.env.BASE;
const R = { held: [] };
for (const [name, path, sel] of [["landing", "/?token=" + process.env.TOKEN, "#romp-boot"], ["chat", "/chat?token=" + process.env.TOKEN, "#pane-spin"]]) {
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on("pageerror", () => {});
  await page.addInitScript(() => { window.WebSocket = function () { this.readyState = 0; this.send = () => {}; this.close = () => {}; }; });
  // the page under test and its fonts only; the swirl file is held unanswered, so anything waiting on it never paints
  await page.route("**/*", (r) => {
    const u = new URL(r.request().url());
    if (u.pathname.endsWith("/romp-swirl-o.svg")) { R.held.push(name + " " + u.pathname); return; }
    if (u.pathname === new URL(base + path).pathname || /^\/media\/.*\.(ttf|woff2)$/.test(u.pathname)) return r.continue();
    return r.abort();
  });
  await page.goto(base + path, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => document.fonts.load("120px RompAnta"));
  R[name] = await page.evaluate(async (sel) => {
    document.getAnimations().forEach((a) => { a.pause(); a.currentTime = 0; });   // measure the unrotated box
    const word = document.querySelector(sel + " .rl-word");
    const [r, o, m] = word.children;
    const cs = getComputedStyle(o);
    const bg = cs.backgroundImage;
    const uri = (bg.match(/^url\("(.*)"\)$/) || [])[1] || "";
    let natural = 0;
    try { const img = new Image(); img.src = uri; await img.decode(); natural = img.naturalWidth; } catch (e) {}
    // the advance a real Anta "o" takes in the same line, which the swirl's box is sized to
    const probe = document.createElement("span"); probe.textContent = "o"; word.appendChild(probe);
    const oAdvance = probe.getBoundingClientRect().width; probe.remove();
    const fs = parseFloat(getComputedStyle(word).fontSize);
    const rr = r.getBoundingClientRect(), ro = o.getBoundingClientRect(), rm = m.getBoundingClientRect();
    return { tag: o.tagName, display: cs.display, anim: cs.animationName, bgIsData: bg.startsWith('url("data:image/svg+xml,'),
             natural, fontReady: document.fonts.check("120px RompAnta"), fs, oW: ro.width, oH: ro.height,
             gapRtoM: rm.left - rr.right, oAdvance };
  }, sel);
  await page.close();
}
console.log("RESULT:" + JSON.stringify(R));
await browser.close();
process.exit(0);
"""


class PaintsWithTheSwirlFileHeld(unittest.TestCase):
    """One browser run; each test reads one facet of what painted while nothing answered for the swirl file."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isdir(os.path.join(EXT, "node_modules", "playwright")):
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the first-paint check needs a browser")
        lab = tempfile.mkdtemp(prefix="wordmark-first-paint-")
        cls.addClassCleanup(shutil.rmtree, lab, ignore_errors=True)
        driver = os.path.join(lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        with _Server() as s:
            p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=180,
                               env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"),
                                        BASE="http://127.0.0.1:%d" % s.port, TOKEN=km.TOKEN))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this machine: the first-paint check needs one (CI installs none)")
        if p.returncode != 0:
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        if line is None:
            raise AssertionError("driver printed no result:\n" + p.stdout[-3000:] + p.stderr[-3000:])
        cls.R = json.loads(line[len("RESULT:"):])

    def test_no_page_asked_for_the_swirl_file(self):
        self.assertEqual(self.R["held"], [])

    def test_the_o_paints_from_the_page_and_still_spins(self):
        for name in ("landing", "chat"):
            r = self.R[name]
            self.assertEqual((r["tag"], r["display"], r["anim"]), ("SPAN", "inline-block", "rl-spin"), name)
            self.assertTrue(r["bgIsData"], "%s: the o's picture is a data URI" % name)
            self.assertGreater(r["natural"], 0, "%s: and it decodes as an image" % name)

    def test_the_swirl_takes_exactly_the_slot_a_real_o_would(self):
        for name in ("landing", "chat"):
            r = self.R[name]
            self.assertTrue(r["fontReady"], "%s: measured in Anta, the face the geometry is cut for" % name)
            self.assertAlmostEqual(r["oW"], 0.65 * r["fs"], delta=0.5, msg="%s: the .65em box" % name)
            self.assertAlmostEqual(r["oH"], 0.65 * r["fs"], delta=0.5, msg="%s: the .65em box" % name)
            # the -.0335em side margins make the swirl's advance Anta's 'o': m lands where "Romp" in type puts it
            self.assertAlmostEqual(r["gapRtoM"], r["oAdvance"], delta=0.75, msg="%s: R..m spacing %r" % (name, r))


if __name__ == "__main__":
    unittest.main()
