#!/usr/bin/env python3
"""The stored logins' "Same account as…" picker on the served dashboard (the review of #78, 2026-10-01). Three defects, each
read here in a browser over a hermetic kernel:
  1. The picker should list this machine and every connected kernel, but it read the kernel list only when the stored logins
     rendered, and settings asked for the kernels (/tunnels) and the logins (/logins) side by side. When /logins answered
     first, a first open listed only this machine and a later open the previous open's machines: a newly connected kernel
     missing, a disconnected one still offered. /logins is still read beside /tunnels, and the machine's own login it
     names (the signed-in line's organisation and kind) is said at once; the rows are drawn once the /tunnels read settles,
     with the kernel list when it arrives and with this machine alone when it fails, so this lab reads each picker once the
     kernel list has arrived. The waiting drawing draws the newest /logins answer to have arrived, less a login removed
     since: drawing its own, it undid on screen a link made on those rows while /tunnels was on its way, when that open's
     own answer had arrived first. Opens overlap, and their reads can answer out of order. A drawing's pickers list the
     kernels of the newest settings fill whose /tunnels read has settled, not the last list to arrive: an older open's
     /tunnels answering after a newer one's listed the older kernels in the newer open's drawing and in a drawing after a
     link. Only the newest open draws, unless its /logins read fails; then an older open draws, once the failed open's
     /tunnels read has settled, with the kernels that read listed. While a link or an unlink is on its way, a fill's
     drawing is skipped.
  2. The picker linked on its change event, and a focused, closed select fires change on an arrow key or a typed letter
     (measured in the Linux builds of Chromium, Firefox and WebKit), so a keyboard user looking through the options linked
     the login at once. A pick now shows a Link button beside the picker, and only Link sends. The pick and Link outlive a
     redraw of the rows while the machine is still listed: a synchronized switch's change re-runs the settings fill for 21 s,
     and each run's drawing put the picker back at its placeholder before Link was pressed. A reopen of settings shows them
     again too. Picking the placeholder, pressing Link, the machine leaving the list or the login being linked elsewhere drops
     the pick.
  3. The Account row's text span never shrank below its content, so the widest stored-login row's text set every row's
     width: a linked row with a long name (it carries the linked email, organisation and kind) or a long same-account note
     pushed every row's Remove past the settings card's edge at desktop width, and at phone width a row's label and date
     beside the picker could do the same. Measured here at 1200x800 and 390x844; below about 360 px a row showing Link still
     runs past the card.

The kernel is real: its own home and Claude config dir hold a synthetic signed-in account (dev@example.com, Example Org,
subscriptionType team, read as enterprise), and two stored logins are added through its POST /logins door (work and
spare; their token commands are never run). The driver answers the settings frame's /tunnels, with stand-in connected
kernels (api-box, then web-box); it can hold that answer until it releases it, answer it 500, or, while
armed with a list, hold every /tunnels asked meanwhile and answer each, once released, with that list. The settings
frame's GET /logins reaches the real kernel; the driver can answer one 500, or hold one until it releases it. The link
and unlink POSTs reach the real kernel, which reads this machine's sign-in itself and refuses a link to a stand-in kernel
(none is connected to it); the driver can delay one on its way, hold it until it releases it (every one, or one naming a
given login), hold its answer once the kernel has taken it, or answer it 500 with a plain-text body. The last six scenes'
pages count, in each frame, the /logins answers the page has read (a wrapped Response.json), so the driver can wait until
the page has read an answer. The driver, at 1200x800:
  - opens settings on General with /tunnels held and waits for a /logins answer to arrive meanwhile, then reads the panel:
    the signed-in line, the Account row's description and how many stored-login rows there are. It releases /tunnels and
    reads the work row's picker (api-box connected) and how many /logins reads the open sent;
  - closes, swaps the stand-in kernels (api-box gone, web-box up), reopens with /tunnels held, reads the same three once a
    /logins answer has arrived, releases it, and reads the picker and the read count again;
  - focuses the picker and presses ArrowDown, ArrowUp and ArrowDown, reading the pick and Link after each, and the POSTs to
    /logins and the kernel's record after the three; Tabs to Link and presses Enter with that POST delayed, reads the row
    while it is in flight and presses Enter again, then reads the row and the record;
  - measures every stored-login row's last control (Remove, by its class) against the card's inner edge, and the card's
    side scroll.
Then a 390x844 phone page opens settings and measures the rows again, the linked one and the one with the picker, and once
more with a machine picked, Link showing, reading Link's computed style beside Remove's. With spare unlinked through the
kernel's door if a step above linked it, a fresh 1200x800 page opens settings, reads spare's picker (web-box connected)
and closes it, then reopens with /tunnels answered 500, counting the 500s served, and once the rows are drawn again (or
after 6 s) reads them and spare's picker. Then a fresh 1200x800 page opens settings and closes it, reopens with
/tunnels held until a /logins answer has arrived, links spare on the rows still drawn from the first open (the POST
answered while /tunnels is held), then releases /tunnels and, once the rows are drawn again (or after 4 s), reads spare's
row and the kernel's record. With spare unlinked again, a fresh page opens settings, toggles Compact suggest (a
synchronized switch), picks this machine in spare's row, reads spare's row after each of the next two drawings the re-run
fill makes, then presses Link and reads the POSTs and the record. With spare unlinked, a fresh page opens settings and
closes it; reopens with its /tunnels held, armed with api-box, until its /logins answer has arrived; closes; opens again,
that open's /tunnels answered at once with web-box, and reads spare's picker; then releases the held request and, once
the machine selector lists api-box (fill() fills it from the same answer), reads the picker again. With spare unlinked, a
fresh page opens settings, and spare's row is read after each of these, each but the last followed by closing and reopening
settings until the row is drawn again: this machine picked; the placeholder picked; web-box picked, with web-box then gone
from the kernel list; this machine picked, then spare linked through the kernel's door (the row read linked) and unlinked
again; and last, api-box connected again, api-box picked and Link pressed with the POST held: a reopen with its /tunnels
held until its /logins answer has arrived, then answered with web-box, and spare's row read once the machine selector lists
web-box; then the POST released, the refusal read, and spare's row read after a reopen with api-box connected. Then three
fresh pages each open settings with no kernel connected and close it, reopen with /tunnels held, armed with api-box,
until a /logins answer has arrived, and close; then open again with web-box connected: (a) that open's GET /logins
answered 500 and its /tunnels at once; the first open's /tunnels is released and, once the machine selector lists
api-box, spare's picker is read in the rows drawn then; (b) the same, that open's /tunnels held until the first open's
has landed; (c) that open's GET /logins held until the first open's /tunnels has landed, then answered by the kernel, and
spare's picker read; then work is unlinked through the kernel's door, spare linked with its picker and Link, and work's
picker read in the rows drawn after the link.

Then scenes on a link or an unlink pressed while a reopen's rows wait on /tunnels, each a fresh page with work and spare
unlinked. Unless its line says otherwise, a scene opens settings with api-box connected, picks a machine in spare's row and
closes, then reopens with web-box connected and /tunnels held until its /logins answer has arrived, so spare's row is the
first open's, with the pick and Link. A release of /tunnels is read once the machine selector lists web-box.
  - api-box picked, Link pressed with the POST held, /tunnels released, then the POST released: spare's row read after
    each, and work's picker after the refusal;
  - this machine picked, the same: spare's row read after each, work's picker once spare reads linked, then a reopen;
  - spare linked through the kernel's door before the first open and unlinked before the reopen, so its row shows Unlink:
    Unlink pressed with the POST held, /tunnels released, then the POST released, spare's row read after each;
  - api-box picked and Link pressed, the refusal read while /tunnels is held, then /tunnels released and spare's row and
    picker read once the rows are drawn again;
  - this machine picked and Link pressed with the POST held; the next GET /logins held, the POST released, and once that
    GET (the link's re-read) is held, /tunnels released; spare's row read, then the GET released and the row read again;
  - on a fresh page without the reopen, this machine picked and Link pressed with the POST answered 500, then a reopen;
  - the mouse pressed on spare's Link, /tunnels released, and once the row is drawn again the element under the pointer read
    and the button let go, then Link pressed once: the POSTs and the kernel's record read after each;
  - in the order of (b) of the overlapping opens above, with this machine picked in the first open and Link pressed, its POST
    held, in the older open: spare's row read once both /tunnels answers have reached the page, then the POST released;
  - the same, the POST answered 500 once the older open's /tunnels has reached the page and while the newer open's is held;
    spare's row and picker read once the newer open's has reached the page;
  - api-box picked in work's row too, Link pressed in both rows, both POSTs held; work's let go, which the kernel refuses,
    while /tunnels is held; /tunnels released and spare's row read, then spare's let go and its refusal read.

Then five scenes, each a fresh page with work and spare unlinked: it opens settings with api-box connected, and reopens with
web-box connected and /tunnels held until the page has read the reopen's /logins answer, then released.
  - this machine picked in spare's row and Link pressed before the reopen, the POST held on its way; after the release the
    POST let go, its re-read answered 500, and once the page has read both, spare's row, work's picker and the kernel's
    record read;
  - the same with the POST taken by the kernel and its answer held;
  - on the reopen's rows before the release, Link pressed on work (api-box), its POST held on its way, and on spare (this
    machine), taken by the kernel with its answer held; after the release work's link let go, which the kernel refuses; then
    spare's answer let go, its re-read answered 500, and once the page has read both, both rows and the kernel's record read;
  - Remove pressed on work before the release, its re-read held; work's and spare's rows read after the Remove and after the
    release, then the re-read answered 500 and both rows read once the page has read it;
  - this machine picked in spare's row and Link pressed before the reopen, the POST held on its way; after the release
    Remove pressed on work, its re-read held, and work's and spare's rows read; the link let go, its re-read answered 500,
    and both rows read once the page has read the link's answer and the 500; then the Remove's re-read answered 500 and both
    rows read again.
The last two take work out of the kernel, so they run last, and the second adds work back first.

LINK_PICKER_DUMP=<path> writes the whole measurement there. Skips LOUDLY without the extension deps, a Playwright browser,
a working esbuild or a kernel that serves (CI turns any skip in a served module into a failure). Synthetic throughout:
dev@example.com, Example Org, TESTHOST, api-box, web-box."""
import json
import os
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment: a list of names, never a copy of the runner's

EMAIL, ORG = "dev@example.com", "Example Org"
ACCOUNT = {"oauthAccount": {"accountUuid": "11111111-2222-3333-4444-555555555555", "emailAddress": EMAIL, "organizationName": ORG}}


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
const out = { errors: [] };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const kernelLogins = async () => (await (await fetch(cfg.base + "/logins?token=" + cfg.token)).json()).logins
  .map((r) => ({ id: r.id, label: r.label, linked: r.linked ? r.linked.host : null }));
// tunnels: the stand-in connected kernels; hold: the settings frame's /tunnels waits until the driver clears it (held counts the
// answers waiting); holdOne: a list, and every settings /tunnels asked while it is set waits until releaseOne and is answered
// with it (heldOne counts those waiting, oneAnswered is set once one is answered). The driver clears holdOne once the open it
// holds has asked, so a later open's /tunnels is answered as usual; the settings page's own federation poll (every 4 s, from the
// same frame) asked meanwhile is held with it and answered alike. fail: the settings /tunnels is answered 500 (served500 counts
// them); tunnelsAnswered: the settings /tunnels answered. failGet: the next settings GET /logins is answered 500; holdGet: the
// next one waits until releaseGet (getHeld counts it) and is then answered 500 if failHeld is set, else by the kernel;
// failedGets counts those 500s. postDelay: a link or unlink POST waits that long on its way to the kernel, and holdPost: until the
// driver clears it; failPost: it is then answered 500 with a plain-text body. holdIds: a POST naming one of these logins waits
// on its way too; holdAnswerIds: one naming one of these reaches the kernel and its answer waits until the driver clears the id
// (answerHeld counts those waiting). gets: the settings frame's GET /logins sent; answeredWhileHeld: those answered while a
// /tunnels was held
const st = { tunnels: cfg.tunnels1, hold: false, held: 0, holdOne: null, heldOne: 0, releaseOne: false, oneAnswered: false, fail: false,
  served500: 0, tunnelsAnswered: 0, failGet: false, holdGet: false, releaseGet: false, failHeld: false, getHeld: 0, failedGets: 0,
  postDelay: 0, holdPost: false, failPost: false, holdIds: [], holdAnswerIds: [], answerHeld: 0, gets: 0, answeredWhileHeld: 0, posts: [] };
const door = async (body) => (await fetch(cfg.base + "/logins", { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.token },
  body: JSON.stringify(body) })).json();
const fromSettings = (req) => { try { return req.frame().url().includes("/settings"); } catch (e) { return false; } };
const isLogins = (req, method) => { try { return new URL(req.url()).pathname === "/logins" && req.method() === method; } catch (e) { return false; } };

// counted: each frame counts the /logins answers whose json() has settled (window.__loginsRead) and fires a lab-logins-read event
// on its window at each, so the driver can wait until the page has read an answer; whatever that answer sets off without waiting on
// a request or a timer has run once the count is seen
async function context(viewport, mobile, counted) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1, isMobile: !!mobile, hasTouch: !!mobile });
  if (counted) await ctx.addInitScript(() => {
    const json = Response.prototype.json;
    Response.prototype.json = function () {
      const u = this.url || "";
      return json.call(this).finally(() => {
        if (!/\/logins(\?|$)/.test(u)) return;
        window.__loginsRead = (window.__loginsRead || 0) + 1;
        window.dispatchEvent(new Event("lab-logins-read"));
      });
    };
  });
  await ctx.route(/\/tunnels(\?|$)/, async (route) => {
    if (!fromSettings(route.request())) return route.continue();
    if (st.fail) {
      st.served500++;
      await route.fulfill({ status: 500, contentType: "text/plain", body: "the stand-in kernel list failed" });
      st.tunnelsAnswered++;
      return;
    }
    if (st.holdOne) {
      const list = st.holdOne;
      st.held++; st.heldOne++;
      const t0 = Date.now();
      while (!st.releaseOne && Date.now() - t0 < 15000) await sleep(20);   // loop-ok: bounded at 15 s; the driver releases it
      st.held--; st.heldOne--;
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tunnels: list }) });
      st.tunnelsAnswered++;
      st.oneAnswered = true;
      return;
    }
    if (st.hold) {
      st.held++;
      const t0 = Date.now();
      while (st.hold && Date.now() - t0 < 15000) await sleep(20);   // loop-ok: bounded at 15 s; the driver releases it
      st.held--;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ tunnels: st.tunnels }) });
    st.tunnelsAnswered++;
  });
  await ctx.route(/\/logins(\?|$)/, async (route) => {
    const req = route.request();
    if (req.method() === "GET" && fromSettings(req) && (st.failGet || st.holdGet)) {
      let fail = st.failGet;
      st.failGet = false;
      if (st.holdGet) {
        st.holdGet = false; st.getHeld++;
        const t0 = Date.now();
        while (!st.releaseGet && Date.now() - t0 < 15000) await sleep(20);   // loop-ok: bounded at 15 s; the driver releases it
        st.getHeld--;
        fail = st.failHeld;
      }
      if (fail) {
        st.failedGets++;
        return route.fulfill({ status: 500, contentType: "text/plain", body: "the stand-in /logins read failed" });
      }
      return route.continue();
    }
    if (req.method() === "POST") {
      if (st.postDelay) await sleep(st.postDelay);
      const t0 = Date.now(), pb = req.postData() || "";
      const names = (ids) => ids.some((id) => pb.includes(id));
      while ((st.holdPost || names(st.holdIds)) && Date.now() - t0 < 15000) await sleep(20);   // loop-ok: bounded at 15 s; the driver releases it
      if (st.failPost) return route.fulfill({ status: 500, contentType: "text/plain", body: "the stand-in POST failed" });
      if (names(st.holdAnswerIds)) {
        const answer = await route.fetch();
        st.answerHeld++;
        const t1 = Date.now();
        while (names(st.holdAnswerIds) && Date.now() - t1 < 15000) await sleep(20);   // loop-ok: bounded at 15 s; the driver releases it
        st.answerHeld--;
        return route.fulfill({ response: answer });
      }
    }
    return route.continue();
  });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => out.errors.push((mobile ? "phone: " : "") + String((e && e.message) || e).slice(0, 300)));
  page.on("request", (r) => {
    if (isLogins(r, "GET") && fromSettings(r)) st.gets++;
    if (isLogins(r, "POST")) { try { st.posts.push(JSON.parse(r.postData() || "null")); } catch (e) { st.posts.push(null); } }
  });
  page.on("requestfinished", (r) => { if (isLogins(r, "GET") && fromSettings(r) && st.held > 0) st.answeredWhileHeld++; });
  await page.goto(cfg.url);
  await page.waitForFunction(() => typeof window.__rompOpenSettings === "function", null, { timeout: 20000 });
  return { ctx, page };
}
// open settings on General and wait for the panel; openSettings also waits for the two stored-login rows
async function openPanel(page) {
  await page.evaluate(() => window.__rompOpenSettings("general"));
  let setF = null;
  for (let i = 0; i < 100 && !setF; i++) { setF = page.frames().find((f) => f.url().includes("/settings")) || null; if (!setF) await page.waitForTimeout(100); }   // loop-ok: 100 tries
  if (!setF) throw new Error("the settings frame never loaded");
  await setF.waitForSelector("#rsettings:not([hidden])", { timeout: 15000 });
  return setF;
}
async function openSettings(page) {
  const setF = await openPanel(page);
  await setF.waitForFunction(() => document.querySelectorAll("#rs-logins .rs-login-row").length === 2, null, { timeout: 15000 });
  return setF;
}
async function closeSettings(page, setF) {
  await page.evaluate(() => window.__rompOpenSettings());   // the opener toggles: an open panel closes
  await setF.waitForSelector("#rsettings", { state: "hidden", timeout: 10000 });
}
// an open with the settings frame's /tunnels held: what the panel shows once a /logins answer has arrived meanwhile (the
// signed-in line takes the organisation from it), then the release and the picker with the stand-in kernels
async function heldOpen(page, kernelHost) {
  const gets0 = st.gets, answered0 = st.answeredWhileHeld;
  st.hold = true;
  const setF = await openPanel(page);
  for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
  await setF.waitForFunction((org) => ((document.getElementById("rs-login-who") || {}).textContent || "").includes(org), cfg.org, { timeout: 2000 }).catch(() => {});
  const held = await setF.evaluate(() => ({ who: (document.getElementById("rs-login-who") || {}).textContent || "",
    desc: (document.getElementById("rs-login-acct") || {}).textContent || "",
    rows: document.querySelectorAll("#rs-logins .rs-login-row").length }));
  held.heldNow = st.held; held.answeredWhileHeld = st.answeredWhileHeld - answered0;
  st.hold = false;
  await waitOption(setF, kernelHost);
  await sleep(500);   // a second read the settled /tunnels set off would go out at once
  return { setF, held, gets: st.gets - gets0 };
}
// the row of a stored login by its label (the name starts with it), read in the frame
const ROW = (label) => "#rs-logins .rs-login-row:has(.rs-login-name[title^='" + label + "'])";
// the picker's machines by option value ('' is the placeholder, TESTHOST this machine), never by their words
const options = (setF, label) => setF.evaluate((sel) => {
  const s = document.querySelector(sel + " select.rs-login-link");
  return s ? Array.from(s.options).map((o) => o.value) : null;
}, ROW(label));
const waitOption = (setF, host) => setF.waitForFunction((h) => Array.from(document.querySelectorAll("#rs-logins select.rs-login-link option"))
  .some((o) => o.value === h), host, { timeout: 4000 }).catch(() => {});
const rowState = (setF, label) => setF.evaluate((sel) => {
  const r = document.querySelector(sel);
  if (!r) return null;
  const go = r.querySelector(".rs-login-go"), s = r.querySelector("select.rs-login-link"), n = r.querySelector(".rs-note");
  const ul = r.querySelector(".rs-login-unlink");
  return { note: (n || {}).textContent || "", bad: !!n && n.classList.contains("rs-login-bad"), unlink: !!ul,
    ul: ul ? { text: ul.textContent, disabled: ul.disabled } : null,
    picker: !!s, value: s ? s.value : null, disabled: s ? s.disabled : null,
    link: go ? { shown: go.getBoundingClientRect().width > 0 && getComputedStyle(go).display !== "none" } : null };
}, ROW(label));
const active = (setF) => setF.evaluate(() => { const a = document.activeElement; return a ? { tag: a.tagName, cls: a.className, text: (a.textContent || "").slice(0, 80) } : null; });
// every stored-login row against the card: how far its last control reaches past the card's inner right edge, and whether the
// card scrolls sideways. A row's Remove is known by its class: the row's buttons all wear rs-login-rm, and Link and Unlink add
// one of their own
const measure = (setF) => setF.evaluate(() => {
  const isRemove = (b) => !!b && b.classList.contains("rs-login-rm") && !b.classList.contains("rs-login-go") && !b.classList.contains("rs-login-unlink");
  const card = document.querySelector("#rsettings .rs-card"), cr = card.getBoundingClientRect(), cs = getComputedStyle(card);
  const inner = cr.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth);
  return { cardWidth: Math.round(cr.width), sideScroll: card.scrollWidth - card.clientWidth,
    rows: Array.from(document.querySelectorAll("#rs-logins .rs-login-row")).map((r) => {
      const last = r.lastElementChild, name = r.querySelector(".rs-login-name");
      return { name: name.textContent, lastIsRemove: isRemove(last), lastClass: last.className, linked: !!r.querySelector(".rs-login-unlink"), picker: !!r.querySelector("select"),
        pastCard: Math.round((last.getBoundingClientRect().right - inner) * 10) / 10, nameWidth: Math.round(name.getBoundingClientRect().width) };
    }) };
});
// Link's computed style beside its row's Remove's: the same button, so the row reads as one line of quiet controls
const dress = (setF, label) => setF.evaluate((sel) => {
  const isRemove = (b) => !!b && b.classList.contains("rs-login-rm") && !b.classList.contains("rs-login-go") && !b.classList.contains("rs-login-unlink");
  const r = document.querySelector(sel);
  const look = (b) => { if (!b) return null; const c = getComputedStyle(b);
    return { background: c.backgroundColor, color: c.color, border: [c.borderTopWidth, c.borderTopStyle, c.borderTopColor].join(" "),
      radius: c.borderTopLeftRadius, padding: [c.paddingTop, c.paddingRight, c.paddingBottom, c.paddingLeft].join(" "),
      font: [c.fontSize, c.fontFamily, c.fontWeight].join(" "), height: Math.round(b.getBoundingClientRect().height) }; };
  return r ? { link: look(r.querySelector(".rs-login-go")), remove: look(Array.from(r.querySelectorAll("button")).find(isRemove)) } : null;
}, ROW(label));
// the machine selector lists a kernel: fill() fills it from each /tunnels answer it reads, and the federation poll never does.
// Keyed on the frame's DOM mutations (a MutationObserver), not polled on animation frames as waitForFunction is by default
const scopeLists = (setF, host) => setF.evaluate((h) => new Promise((resolve) => {
  const lists = () => Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt")).some((o) => (o.textContent || "").trim() === h);
  if (lists()) return resolve(true);
  const done = (v) => { mo.disconnect(); clearTimeout(t); resolve(v); };
  const mo = new MutationObserver(() => { if (lists()) done(true); });
  const t = setTimeout(() => done(false), 4000);
  mo.observe(document, { childList: true, subtree: true, characterData: true });
}), host).catch(() => false);
const waitRow = (setF, label, sel) => setF.waitForFunction(([r, s]) => !!document.querySelector(r + " " + s), [ROW(label), sel], { timeout: 8000 })
  .then(() => true, () => false);
const keptConnected = (setF) => setF.evaluate(() => !!window.__keptRow && window.__keptRow.isConnected);
// Scenes 11 to 17, each on a fresh page. keptReopen: work and spare unlinked (spare then linked through the kernel's door when
// `linked`), settings opened with api-box connected and `pick` picked in spare's row (on a tree with Link), closed (spare unlinked
// through the door when `linked`), and reopened with web-box connected and /tunnels held until its /logins answer has arrived;
// spare's row, still the first open's, is kept in window.__keptRow
async function keptReopen(rec, pick, linked) {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  const spare = (await kernelLogins()).find((r) => r.label === "spare");
  if (linked) await door({ link: { login: spare.id, sameAs: "local" } });
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  if (linked) await waitRow(setF, "spare", ".rs-login-unlink");
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  if (pick && rec.hasLink) await setF.selectOption(ROW("spare") + " select.rs-login-link", pick);
  await closeSettings(page, setF);
  if (linked) await door({ unlink: spare.id });
  st.tunnels = cfg.tunnels2;
  const answered0 = st.answeredWhileHeld;
  st.hold = true;
  setF = await openPanel(page);
  for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
  rec.heldNow = st.held; rec.answeredWhileHeld = st.answeredWhileHeld - answered0;
  await setF.evaluate((sel) => { window.__keptRow = document.querySelector(sel); }, ROW("spare"));
  rec.kept = await rowState(setF, "spare");
  return { ctx, page, setF };
}
// Link on spare's row (on a tree without Link, the pick itself, which links on change), or Unlink; returns once the POST is sent
async function press(setF, rec, pick, unlink) {
  const p0 = st.posts.length;
  if (unlink) await setF.click(ROW("spare") + " .rs-login-unlink", { timeout: 4000 });
  else if (rec.hasLink) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
  else await setF.selectOption(ROW("spare") + " select.rs-login-link", pick);
  for (let i = 0; i < 100 && st.posts.length === p0; i++) await sleep(20);   // loop-ok: bounded at 2 s
  return p0;
}
// /tunnels released: true once its answer (web-box) has reached the machine selector, and so the rows' drawing has been decided
const release = (setF) => { st.hold = false; return scopeLists(setF, cfg.tunnels2[0].host); };
// close and reopen settings: true once spare's row is drawn anew
async function reopenDraws(page, setF) {
  await setF.evaluate((sel) => { window.__reRow = document.querySelector(sel); }, ROW("spare"));
  await closeSettings(page, setF);
  setF = await openPanel(page);
  const drawn = await setF.waitForFunction((sel) => !!window.__reRow && !window.__reRow.isConnected && !!document.querySelector(sel), ROW("spare"),
    { timeout: 6000 }).then(() => true, () => false);
  return { setF, drawn };
}
async function scene(name, body) {
  const rec = {};
  out[name] = rec;
  let ctx = null;
  try {
    ctx = await body(rec);
  } catch (e) {
    rec.error = String((e && e.stack) || e).slice(0, 1500);
  }
  st.hold = false; st.holdPost = false; st.failPost = false; st.holdGet = false; st.releaseGet = true; st.failGet = false; st.failHeld = false;
  st.holdOne = null; st.releaseOne = true; st.holdIds = []; st.holdAnswerIds = []; st.tunnels = cfg.tunnels2;
  if (ctx) await ctx.close();
}
// 11. a link the kernel refuses (api-box), on its way while /tunnels is released
const inflightRefused = () => scene("inflightRefused", async (rec) => {
  const { ctx, page, setF } = await keptReopen(rec, cfg.tunnels1[0].host, false);
  st.holdPost = true;
  const p0 = await press(setF, rec, cfg.tunnels1[0].host, false);
  rec.posts = st.posts.slice(p0);
  rec.sent = await rowState(setF, "spare");
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.holdPost = false;
  rec.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  rec.afterAnswer = await rowState(setF, "spare");
  rec.keptAfterAnswer = await keptConnected(setF);
  rec.work = await options(setF, "work");
  rec.kernel = await kernelLogins();
  return ctx;
});
// 12. a link the kernel takes (this machine), on its way while /tunnels is released; then a reopen
const inflightOk = () => scene("inflightOk", async (rec) => {
  let { ctx, page, setF } = await keptReopen(rec, "TESTHOST", false);
  st.holdPost = true;
  const p0 = await press(setF, rec, "TESTHOST", false);
  rec.posts = st.posts.slice(p0);
  rec.sent = await rowState(setF, "spare");
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.holdPost = false;
  rec.linked = await waitRow(setF, "spare", ".rs-login-unlink");
  rec.afterAnswer = await rowState(setF, "spare");
  rec.work = await options(setF, "work");
  rec.kernel = await kernelLogins();
  ({ setF, drawn: rec.reopenDrawn } = await reopenDraws(page, setF));
  return ctx;
});
// 13. an unlink the kernel refuses (spare is no longer linked), on its way while /tunnels is released
const inflightUnlink = () => scene("inflightUnlink", async (rec) => {
  const { ctx, page, setF } = await keptReopen(rec, null, true);
  st.holdPost = true;
  const p0 = await press(setF, rec, null, true);
  rec.posts = st.posts.slice(p0);
  rec.sent = await rowState(setF, "spare");
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.holdPost = false;
  rec.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  rec.afterAnswer = await rowState(setF, "spare");
  rec.kernel = await kernelLogins();
  return ctx;
});
// 14. a refusal answered while /tunnels is still held, then /tunnels released
const refusedFirst = () => scene("refusedFirst", async (rec) => {
  const { ctx, page, setF } = await keptReopen(rec, cfg.tunnels1[0].host, false);
  const p0 = await press(setF, rec, cfg.tunnels1[0].host, false);
  rec.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  rec.heldAtRefusal = st.held;
  rec.posts = st.posts.slice(p0);
  rec.refusedRow = await rowState(setF, "spare");
  rec.released = await release(setF);
  rec.drawn = await setF.waitForFunction((sel) => !!window.__keptRow && !window.__keptRow.isConnected && !!document.querySelector(sel), ROW("spare"),
    { timeout: 6000 }).then(() => true, () => false);
  rec.after = await rowState(setF, "spare");
  rec.spare = await options(setF, "spare");
  rec.work = await options(setF, "work");
  return ctx;
});
// 15. the ok answered while its re-read's GET /logins is held, /tunnels released in that window
const okWindow = () => scene("okWindow", async (rec) => {
  const { ctx, page, setF } = await keptReopen(rec, "TESTHOST", false);
  st.holdPost = true;
  const p0 = await press(setF, rec, "TESTHOST", false);
  rec.posts = st.posts.slice(p0);
  st.releaseGet = false; st.failHeld = false; st.holdGet = true;
  st.holdPost = false;
  for (let i = 0; i < 200 && st.getHeld === 0; i++) await sleep(20);   // loop-ok: bounded at 4 s
  rec.reReadHeld = st.getHeld;
  rec.kernelAtOk = await kernelLogins();
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.releaseGet = true;
  rec.linked = await waitRow(setF, "spare", ".rs-login-unlink");
  rec.afterReRead = await rowState(setF, "spare");
  return ctx;
});
// 16. a link answered 500 with a plain-text body, then a reopen
const noAnswer = () => scene("noAnswer", async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  st.tunnels = cfg.tunnels1;
  let { ctx, page } = await context({ width: 1200, height: 800 }, false);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  if (rec.hasLink) await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
  st.failPost = true;
  const p0 = await press(setF, rec, "TESTHOST", false);
  rec.posts = st.posts.slice(p0);
  rec.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  st.failPost = false;
  rec.row = await rowState(setF, "spare");
  ({ setF, drawn: rec.reopenDrawn } = await reopenDraws(page, setF));
  rec.after = await rowState(setF, "spare");
  rec.kernel = await kernelLogins();
  return ctx;
});
// 17. the mouse pressed on spare's Link while /tunnels is held, /tunnels released, and once the rows are drawn again the element
// under the pointer read and the button let go; then Link once
const pressAcross = () => scene("pressAcross", async (rec) => {
  const { ctx, page, setF } = await keptReopen(rec, "TESTHOST", false);
  rec.kernelBefore = await kernelLogins();
  const p0 = st.posts.length;
  const go = await setF.$(ROW("spare") + " .rs-login-go");
  const box = await go.boundingBox(), at = await go.evaluate((b) => { const q = b.getBoundingClientRect(); return [q.left, q.top]; });
  const x = box.x + box.width / 2, y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  st.hold = false;
  rec.redrawn = await setF.waitForFunction((sel) => !!window.__keptRow && !window.__keptRow.isConnected && !!document.querySelector(sel), ROW("spare"),
    { timeout: 6000 }).then(() => true, () => false);
  // the pointer in the frame's own coordinates: what is under it, and whether that is the redrawn spare row's Link
  rec.underAtUp = await setF.evaluate(([fx, fy, sel]) => {
    const e = document.elementFromPoint(fx, fy);
    return { cls: e ? e.className : null, sparesLink: !!e && e === document.querySelector(sel + " .rs-login-go") };
  }, [x - (box.x - at[0]), y - (box.y - at[1]), ROW("spare")]);
  await page.mouse.up();
  rec.afterUp = await rowState(setF, "spare");
  rec.postsAfterUp = st.posts.slice(p0);
  rec.kernelAfterUp = await kernelLogins();
  if (await setF.$(ROW("spare") + " .rs-login-go:not([hidden])")) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
  rec.linked = await waitRow(setF, "spare", ".rs-login-unlink");
  rec.posts = st.posts.slice(p0);
  rec.kernel = await kernelLogins();
  return ctx;
});
// 18, 19. a link on its way while an older open waits on a newer one whose /logins read fails, in scene 9b's order: the older
// open's /tunnels lands first, then the newer open's. waitingReopen: up to the older open's landing
async function waitingReopen(rec) {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  st.tunnels = [];
  const { ctx, page } = await context({ width: 1200, height: 800 }, false);
  let setF = await openSettings(page);
  await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
  await closeSettings(page, setF);
  const answered0 = st.answeredWhileHeld;
  st.releaseOne = false; st.oneAnswered = false; st.holdOne = cfg.tunnels1;
  setF = await openPanel(page);
  for (let i = 0; i < 150 && !(st.heldOne > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
  rec.aHeld = st.heldOne; rec.aAnsweredWhileHeld = st.answeredWhileHeld - answered0;
  st.holdOne = null;
  await setF.evaluate((sel) => { window.__keptRow = document.querySelector(sel); }, ROW("spare"));
  st.holdPost = true;
  const p0 = await press(setF, { hasLink: true }, "TESTHOST", false);
  rec.posts = st.posts.slice(p0);
  await closeSettings(page, setF);
  st.tunnels = cfg.tunnels2;
  const f0 = st.failedGets;
  st.failGet = true; st.hold = true;
  setF = await openPanel(page);
  for (let i = 0; i < 200 && !(st.failedGets > f0 && st.held - st.heldOne > 0); i++) await sleep(20);   // loop-ok: bounded at 4 s
  rec.bFailed = st.failedGets - f0; rec.bHeld = st.held - st.heldOne;
  st.releaseOne = true;
  for (let i = 0; i < 150 && !st.oneAnswered; i++) await sleep(20);   // loop-ok: bounded at 3 s
  rec.aListed = await scopeLists(setF, cfg.tunnels1[0].host);
  return { ctx, setF };
}
// 18. the newer open's /tunnels released, then the link answered
const waitingSkip = () => scene("waitingSkip", async (rec) => {
  const { ctx, setF } = await waitingReopen(rec);
  rec.bListed = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.holdPost = false;
  rec.linked = await waitRow(setF, "spare", ".rs-login-unlink");
  return ctx;
});
// 19. the link answered 500 while the newer open's /tunnels is held, then that /tunnels released
const waitingRefused = () => scene("waitingRefused", async (rec) => {
  const { ctx, setF } = await waitingReopen(rec);
  st.failPost = true; st.holdPost = false;
  rec.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  rec.keptAtRefusal = await keptConnected(setF);
  rec.heldAtRefusal = st.held - st.heldOne;
  st.failPost = false;
  rec.bListed = await release(setF);
  rec.drawn = await setF.waitForFunction((sel) => !!window.__keptRow && !window.__keptRow.isConnected && !!document.querySelector(sel), ROW("spare"),
    { timeout: 6000 }).then(() => true, () => false);
  rec.spare = await options(setF, "spare");
  rec.kernel = await kernelLogins();
  return ctx;
});
// 20. api-box picked in work's row and in spare's; on the reopen's rows, its /tunnels held, Link pressed in both, both POSTs held
// on their way; work's let go, which the kernel refuses; /tunnels released; then spare's let go, which the kernel refuses
const twoInflight = () => scene("twoInflight", async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  const ids = {};
  for (const r of await kernelLogins()) ids[r.label] = r.id;
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false, true);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  if (rec.hasLink) for (const label of ["work", "spare"]) await setF.selectOption(ROW(label) + " select.rs-login-link", cfg.tunnels1[0].host);
  setF = await countedReopen(rec, page, setF);
  await setF.evaluate((sel) => { window.__keptRow = document.querySelector(sel); }, ROW("spare"));
  st.holdIds = [ids.work, ids.spare];
  const p0 = st.posts.length;
  for (const label of ["work", "spare"]) {
    if (rec.hasLink) await setF.click(ROW(label) + " .rs-login-go", { timeout: 4000 });
    else await setF.selectOption(ROW(label) + " select.rs-login-link", cfg.tunnels1[0].host);
  }
  for (let i = 0; i < 100 && st.posts.length < p0 + 2; i++) await sleep(20);   // loop-ok: bounded at 2 s
  rec.posts = st.posts.slice(p0);
  st.holdIds = [ids.spare];
  rec.workRefused = await waitRow(setF, "work", ".rs-note.rs-login-bad");
  rec.heldAtWorkRefusal = st.held;
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  st.holdIds = [];
  rec.spareRefused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
  rec.kernel = await kernelLogins();
  return ctx;
});
// Scenes 20 to 25, each on a fresh page whose /logins answers are counted (context's `counted`). loginsRead: the settings frame's
// count; readTo: true once it reaches n, keyed on the frame's lab-logins-read event, not polled on animation frames
const loginsRead = (setF) => setF.evaluate(() => window.__loginsRead || 0);
const readTo = (setF, n) => setF.evaluate((k) => new Promise((resolve) => {
  const reached = () => (window.__loginsRead || 0) >= k;
  if (reached()) return resolve(true);
  const done = (v) => { window.removeEventListener("lab-logins-read", on); clearTimeout(t); resolve(v); };
  const on = () => { if (reached()) done(true); };
  const t = setTimeout(() => done(false), 8000);
  window.addEventListener("lab-logins-read", on);
}), n).catch(() => false);
// settings closed and reopened with web-box connected, its /tunnels held until the page has read its /logins answer
async function countedReopen(rec, page, setF) {
  await closeSettings(page, setF);
  st.tunnels = cfg.tunnels2;
  const answered0 = st.answeredWhileHeld, n0 = await loginsRead(setF);
  st.hold = true;
  setF = await openPanel(page);
  for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
  rec.heldNow = st.held; rec.answeredWhileHeld = st.answeredWhileHeld - answered0;
  rec.reopenRead = await readTo(setF, n0 + 1);
  return setF;
}
// a held link let go (letGo) with the next settings GET /logins, its re-read, answered 500; returns once the page has read the
// link's answer and the 500
async function failedReReadOf(rec, setF, letGo) {
  const f0 = st.failedGets, n0 = await loginsRead(setF);
  st.failGet = true;
  letGo();
  rec.answered = await readTo(setF, n0 + 2);
  rec.reReadFailed = st.failedGets - f0;
  st.failGet = false;
}
// 21, 22. this machine picked in spare's row and Link pressed, the POST held on its way to the kernel (Before) or taken by the
// kernel with its answer held (After); the reopen; /tunnels released; then the link answered, its re-read answered 500
const failedReRead = (when) => scene("failedReRead" + when, async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  const spare = (await kernelLogins()).find((r) => r.label === "spare");
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false, true);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  if (rec.hasLink) await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
  if (when === "Before") st.holdPost = true; else st.holdAnswerIds = [spare.id];
  const p0 = await press(setF, rec, "TESTHOST", false);
  rec.posts = st.posts.slice(p0);
  if (when === "After") {
    for (let i = 0; i < 200 && st.answerHeld === 0; i++) await sleep(20);   // loop-ok: bounded at 4 s
    rec.answerHeld = st.answerHeld;
    rec.kernelHeld = await kernelLogins();
  }
  await setF.evaluate((sel) => { window.__keptRow = document.querySelector(sel); }, ROW("spare"));
  setF = await countedReopen(rec, page, setF);
  rec.released = await release(setF);
  rec.afterRelease = await rowState(setF, "spare");
  rec.keptAfterRelease = await keptConnected(setF);
  await failedReReadOf(rec, setF, () => { st.holdPost = false; st.holdAnswerIds = []; });
  rec.after = await rowState(setF, "spare");
  rec.keptAfter = await keptConnected(setF);
  rec.work = await options(setF, "work");
  rec.kernel = await kernelLogins();
  return ctx;
});
// 23. on the reopen's rows, its /tunnels held: Link on work (api-box), held on its way, then on spare (this machine), taken by the
// kernel with its answer held; /tunnels released; work's link let go, which the kernel refuses; then spare's link answered, its
// re-read answered 500
const refusalThenFailedReRead = () => scene("refusalThenFailedReRead", async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  const ids = {};
  for (const r of await kernelLogins()) ids[r.label] = r.id;
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false, true);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  setF = await countedReopen(rec, page, setF);
  const kept = () => setF.evaluate(() => [window.__keptWork, window.__keptRow].map((r) => !!r && r.isConnected));
  await setF.evaluate(([w, s]) => { window.__keptWork = document.querySelector(w); window.__keptRow = document.querySelector(s); }, [ROW("work"), ROW("spare")]);
  st.holdIds = [ids.work]; st.holdAnswerIds = [ids.spare];
  const p0 = st.posts.length;
  for (const [label, host] of [["work", cfg.tunnels1[0].host], ["spare", "TESTHOST"]]) {
    await setF.selectOption(ROW(label) + " select.rs-login-link", host);
    if (rec.hasLink) await setF.click(ROW(label) + " .rs-login-go", { timeout: 4000 });
  }
  for (let i = 0; i < 100 && st.posts.length < p0 + 2; i++) await sleep(20);   // loop-ok: bounded at 2 s
  rec.posts = st.posts.slice(p0);
  for (let i = 0; i < 200 && st.answerHeld === 0; i++) await sleep(20);   // loop-ok: bounded at 4 s
  rec.answerHeld = st.answerHeld;
  rec.released = await release(setF);
  rec.keptAfterRelease = await kept();
  st.holdIds = [];
  rec.refused = await waitRow(setF, "work", ".rs-note.rs-login-bad");
  rec.refusal = await rowState(setF, "work");
  await failedReReadOf(rec, setF, () => { st.holdAnswerIds = []; });
  rec.work = await rowState(setF, "work");
  rec.spare = await rowState(setF, "spare");
  rec.keptAfter = await kept();
  rec.kernel = await kernelLogins();
  return ctx;
});
// 24, 25. Remove pressed on work, its re-read held and then answered 500, while a drawing of an answer read before the Remove is
// due: a reopen's once its /tunnels is released (24), and the one after a link's ok whose re-read fails (25). Each takes work out
// of the kernel, so they run last, and 25 adds work back first through the kernel's door. workShown, spareShown: that login's row
// is drawn
const workShown = (setF) => setF.evaluate((sel) => !!document.querySelector(sel), ROW("work"));
const spareShown = (setF) => setF.evaluate((sel) => !!document.querySelector(sel), ROW("spare"));
// Remove pressed on work, its re-read held; spare's row, as drawn before the Remove, is kept in window.__keptRow
async function removeWork(rec, setF) {
  await setF.evaluate((sel) => { window.__keptRow = document.querySelector(sel); }, ROW("spare"));
  st.releaseGet = false; st.failHeld = true; st.holdGet = true;
  await setF.click(ROW("work") + " button.rs-login-rm:not(.rs-login-go):not(.rs-login-unlink)", { timeout: 4000 });
  for (let i = 0; i < 200 && st.getHeld === 0; i++) await sleep(20);   // loop-ok: bounded at 4 s
  rec.rmReReadHeld = st.getHeld;
  for (let i = 0; i < 100 && (await kernelLogins()).some((r) => r.label === "work"); i++) await sleep(50);   // loop-ok: bounded at 5 s
  rec.kernelAfterRemove = await kernelLogins();
  rec.workAtRemove = await workShown(setF);
  rec.spareAtRemove = await spareShown(setF);
  rec.keptAtRemove = await keptConnected(setF);
}
// the Remove's re-read let go (answered 500); returns once the page has read it
async function failRmReRead(rec, setF) {
  const n0 = await loginsRead(setF), f0 = st.failedGets;
  st.releaseGet = true;
  rec.rmReReadRead = await readTo(setF, n0 + 1);
  rec.rmReReadFailed = st.failedGets - f0;
  rec.workAfterRmReRead = await workShown(setF);
  rec.spareAfterRmReRead = await spareShown(setF);
}
const removeAtRelease = () => scene("removeAtRelease", async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false, true);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  setF = await countedReopen(rec, page, setF);
  await removeWork(rec, setF);
  rec.released = await release(setF);
  rec.workAfterRelease = await workShown(setF);
  rec.spareAfterRelease = await spareShown(setF);
  rec.keptAfterRelease = await keptConnected(setF);
  rec.spareRowAfterRelease = await rowState(setF, "spare");
  rec.spareOptionsAfterRelease = await options(setF, "spare");
  await failRmReRead(rec, setF);
  rec.kernel = await kernelLogins();
  return ctx;
});
const removeThenFailedReRead = () => scene("removeThenFailedReRead", async (rec) => {
  for (const r of await kernelLogins()) if (r.linked) await door({ unlink: r.id });
  if (!(await kernelLogins()).some((r) => r.label === "work")) await door({ add: { label: "work", tokenCmd: "cat ~/.romp-lab-work-token" } });
  const spare = (await kernelLogins()).find((r) => r.label === "spare");
  st.tunnels = cfg.tunnels1;
  const { ctx, page } = await context({ width: 1200, height: 800 }, false, true);
  let setF = await openSettings(page);
  await waitOption(setF, cfg.tunnels1[0].host);
  rec.hasLink = !!(await setF.$(ROW("spare") + " .rs-login-go"));
  if (rec.hasLink) await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
  st.holdIds = [spare.id];
  const p0 = await press(setF, rec, "TESTHOST", false);
  setF = await countedReopen(rec, page, setF);
  rec.released = await release(setF);
  await removeWork(rec, setF);
  await failedReReadOf(rec, setF, () => { st.holdIds = []; });
  rec.workAfterFailedReRead = await workShown(setF);
  rec.spareAfterFailedReRead = await spareShown(setF);
  rec.keptAfterFailedReRead = await keptConnected(setF);
  rec.spareRowAfterFailedReRead = await rowState(setF, "spare");
  rec.spareOptionsAfterFailedReRead = await options(setF, "spare");
  rec.posts = st.posts.slice(p0);
  await failRmReRead(rec, setF);
  rec.kernel = await kernelLogins();
  return ctx;
});
try {
  {
    const { ctx, page } = await context({ width: 1200, height: 800 }, false);
    // 1. the first open, api-box connected, /tunnels held until a /logins answer has arrived
    let o = await heldOpen(page, cfg.tunnels1[0].host);
    let setF = o.setF;
    out.held1 = o.held; out.gets1 = o.gets;
    out.open1 = await options(setF, "work");
    out.linkAtRest = (await rowState(setF, "work")).link;
    // …and a later open, api-box gone and web-box connected
    await closeSettings(page, setF);
    st.tunnels = cfg.tunnels2;
    o = await heldOpen(page, cfg.tunnels2[0].host);
    setF = o.setF;
    out.held2 = o.held; out.gets2 = o.gets;
    out.open2 = await options(setF, "work");
    // 2. the keyboard: the picker focused and ArrowDown, which a closed select answers with change (Chromium on Linux)
    const before = st.posts.length;
    await setF.focus(ROW("work") + " select.rs-login-link");
    out.focused = await active(setF);
    await page.keyboard.press("ArrowDown");
    await sleep(1000);   // a POST the arrow set off goes out at once; a second is ample
    out.afterArrow = await rowState(setF, "work");
    // …ArrowUp, back to the placeholder: no pick, so no Link
    await page.keyboard.press("ArrowUp");
    await sleep(300);
    out.afterArrowUp = await rowState(setF, "work");
    await page.keyboard.press("ArrowDown");
    await sleep(300);
    out.afterArrowAgain = await rowState(setF, "work");
    out.arrowPosts = st.posts.slice(before);
    out.kernelAfterArrow = await kernelLogins();
    // …then Tab to Link and Enter, the POST delayed on its way: the row while it is in flight, and Enter once more
    await page.keyboard.press("Tab");
    out.tabbed = await active(setF);
    const before2 = st.posts.length;
    if (out.tabbed && /\brs-login-go\b/.test(out.tabbed.cls)) {
      st.postDelay = 1500;
      await page.keyboard.press("Enter");
      await sleep(200);
      out.inFlight = await rowState(setF, "work");
      out.inFlightFocus = await active(setF);
      await page.keyboard.press("Enter");
      await setF.waitForFunction((sel) => { const r = document.querySelector(sel); return !!r && !!r.querySelector(".rs-login-unlink"); }, ROW("work"), { timeout: 10000 }).catch(() => {});
      await sleep(500);   // a second POST the second Enter set off waits out the same delay
      st.postDelay = 0;
    }
    out.enterPosts = st.posts.slice(before2);
    out.linkedRow = await rowState(setF, "work");
    out.kernelLinked = await kernelLogins();
    // 3. the rows against the card at desktop width, work linked (by the kernel's own door when the steps above did not)
    const work = out.kernelLinked.find((r) => r.label === "work");
    if (!work.linked) {
      await fetch(cfg.base + "/logins", { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.token },
        body: JSON.stringify({ link: { login: work.id, sameAs: "local" } }) });
      await closeSettings(page, setF);
      setF = await openSettings(page);
    }
    await setF.waitForFunction((sel) => { const r = document.querySelector(sel); return !!r && !!r.querySelector(".rs-login-unlink"); }, ROW("work"), { timeout: 10000 });
    out.desktop = await measure(setF);
    await ctx.close();
  }
  {
    // the phone: 390 px wide, the linked row and the row with the picker
    const { ctx, page } = await context({ width: 390, height: 844 }, true);
    const setF = await openSettings(page);
    await setF.waitForFunction(() => !!document.querySelector("#rs-logins .rs-login-unlink") && !!document.querySelector("#rs-logins select.rs-login-link"), null, { timeout: 10000 });
    out.phone = await measure(setF);
    // …and with a machine picked in the row with the picker, so Link shows beside it and takes its room too
    await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
    out.phonePickedLink = (await rowState(setF, "spare")).link;
    out.phonePicked = await measure(setF);
    out.phonePickedDress = await dress(setF, "spare");
    await ctx.close();
  }
  {
    // 4. a page whose first open listed web-box, reopened with its settings /tunnels read failing: the stored logins still show,
    // the picker listing this machine alone. The first open's rows are marked, so the rows read are the failed open's own
    // drawing. spare is unlinked first through the kernel's own door if the steps above linked it (a tree that links on the
    // pick has), so its picker is there to read
    const spare = (await kernelLogins()).find((r) => r.label === "spare");
    if (spare.linked) await fetch(cfg.base + "/logins", { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.token },
      body: JSON.stringify({ unlink: spare.id }) });
    const { ctx, page } = await context({ width: 1200, height: 800 }, false);
    const s0 = await openSettings(page);
    await waitOption(s0, st.tunnels[0].host);
    const prior = await options(s0, "spare");
    await closeSettings(page, s0);
    await s0.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row").forEach((r) => r.setAttribute("data-old", "1")));
    st.fail = true;
    const served0 = st.served500;
    const setF = await openPanel(page);
    await setF.waitForFunction(() => document.querySelectorAll("#rs-logins .rs-login-row").length === 2
      && !document.querySelector("#rs-logins .rs-login-row[data-old]"), null, { timeout: 6000 }).catch(() => {});
    out.failed = { rows: await setF.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row").length),
      fresh: await setF.evaluate(() => !document.querySelector("#rs-logins .rs-login-row[data-old]")), prior,
      spare: await options(setF, "spare"), served500: st.served500 - served0 };
    st.fail = false;
    await ctx.close();
  }
  {
    // 5. a link made on the rows while a later open's /tunnels is held. The open's own /logins answer arrived before the link and
    // is older than the link's re-read, which draws the row linked at once; the drawing that waits on /tunnels must not put the
    // older answer back over it. spare is unlinked here: no step above pressed Link on it (where a pick links at once, the
    // failed-list scene unlinked it). work is linked
    const stale = {};
    out.stale = stale;
    let ctx = null;
    try {
      let page;
      ({ ctx, page } = await context({ width: 1200, height: 800 }, false));
      let setF = await openSettings(page);
      await waitOption(setF, st.tunnels[0].host);
      await closeSettings(page, setF);
      const answered0 = st.answeredWhileHeld;
      st.hold = true;
      setF = await openPanel(page);
      for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
      stale.heldNow = st.held; stale.answeredWhileHeld = st.answeredWhileHeld - answered0;
      stale.rows = await setF.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row").length);
      stale.before = await rowState(setF, "spare");
      await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
      // Link sends the pick; a tree with no Link (one that links on the pick itself) has sent it already
      if (await setF.$(ROW("spare") + " .rs-login-go")) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
      await setF.waitForFunction((sel) => { const r = document.querySelector(sel); return !!r && !!r.querySelector(".rs-login-unlink"); }, ROW("spare"), { timeout: 8000 }).catch(() => {});
      stale.stillHeld = st.held;
      stale.linked = await rowState(setF, "spare");
      // the row as drawn now: a drawing that waited on /tunnels replaces it once /tunnels answers (a tree that draws on the
      // /logins answer alone draws none then)
      await setF.evaluate((sel) => { window.__staleRow = document.querySelector(sel); }, ROW("spare"));
      st.hold = false;
      stale.redrawn = await setF.waitForFunction(() => !!window.__staleRow && !window.__staleRow.isConnected, null, { timeout: 4000 }).then(() => true, () => false);
      stale.after = await rowState(setF, "spare");
      stale.kernel = await kernelLogins();
    } catch (e) {
      stale.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.hold = false;
    if (ctx) await ctx.close();
  }
  {
    // 6. a pick made just after a synchronized switch changes: fill() re-runs 0.7 s later and on to 21 s, and the drawing each run
    // makes must keep the picked machine and Link. The row is read after the first two of those drawings (0.7 s and 2.5 s after
    // the switch), then Link on the redrawn row sends that pick. spare is unlinked first (scene 5 linked it)
    const keep = {};
    out.keep = keep;
    let ctx = null;
    try {
      const spare = (await kernelLogins()).find((r) => r.label === "spare");
      if (spare.linked) await fetch(cfg.base + "/logins", { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.token },
        body: JSON.stringify({ unlink: spare.id }) });
      let page;
      ({ ctx, page } = await context({ width: 1200, height: 800 }, false));
      const setF = await openSettings(page);
      await waitOption(setF, st.tunnels[0].host);
      await sleep(300);
      const gets0 = st.gets, posts0 = st.posts.length;
      keep.switchedTo = await setF.evaluate(() => { const c = document.getElementById("rs-suggestcompact"); c.click(); return c.checked; });
      await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
      keep.picked = await rowState(setF, "spare");
      // the row as drawn now: the re-run fill's drawing replaces it
      await setF.evaluate((sel) => { window.__pickRow = document.querySelector(sel); }, ROW("spare"));
      keep.redrawn = await setF.waitForFunction(() => !!window.__pickRow && !window.__pickRow.isConnected, null, { timeout: 6000 }).then(() => true, () => false);
      await sleep(200);
      keep.gets = st.gets - gets0;
      keep.after = await rowState(setF, "spare");
      // …and once more after the next re-run's drawing
      await setF.evaluate((sel) => { window.__pickRow2 = document.querySelector(sel); }, ROW("spare"));
      keep.redrawn2 = await setF.waitForFunction(() => !!window.__pickRow2 && !window.__pickRow2.isConnected, null, { timeout: 6000 }).then(() => true, () => false);
      await sleep(200);
      keep.after2 = await rowState(setF, "spare");
      keep.postsBeforeLink = st.posts.slice(posts0);
      if (await setF.$(ROW("spare") + " .rs-login-go:not([hidden])")) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
      for (let i = 0; i < 100 && !(await kernelLogins()).find((r) => r.label === "spare").linked; i++) await sleep(50);   // loop-ok: 5 s
      keep.posts = st.posts.slice(posts0);
      keep.kernel = await kernelLogins();
      await setF.evaluate(() => document.getElementById("rs-suggestcompact").click());   // the switch back as it was
      await sleep(300);
    } catch (e) {
      keep.error = String((e && e.stack) || e).slice(0, 1500);
    }
    if (ctx) await ctx.close();
  }
  {
    // 7. two opens overlapping, the first one's /tunnels answering after the second's. The first open's /tunnels is held, armed
    // with api-box, the kernel connected when it asked, and disarmed once its /logins answer has arrived; it closes, and a second
    // open's /tunnels answers at once with web-box. The first one's answer, released last, must leave the pickers the second open
    // drew. spare is unlinked first (scene 6 linked it)
    const over = {};
    out.overlap = over;
    let ctx = null;
    try {
      const spare = (await kernelLogins()).find((r) => r.label === "spare");
      if (spare.linked) await fetch(cfg.base + "/logins", { method: "POST", headers: { "Content-Type": "application/json", "X-Romp-Token": cfg.token },
        body: JSON.stringify({ unlink: spare.id }) });
      st.tunnels = cfg.tunnels2;
      let page;
      ({ ctx, page } = await context({ width: 1200, height: 800 }, false));
      let setF = await openSettings(page);
      await waitOption(setF, cfg.tunnels2[0].host);
      await closeSettings(page, setF);
      const answered0 = st.answeredWhileHeld;
      st.releaseOne = false; st.oneAnswered = false; st.holdOne = cfg.tunnels1;
      setF = await openPanel(page);
      for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
      over.aHeld = st.heldOne; over.aAnsweredWhileHeld = st.answeredWhileHeld - answered0;
      st.holdOne = null;   // disarmed: the second open's /tunnels is answered at once
      await closeSettings(page, setF);
      // the rows on screen are marked, so the second open's own drawing is known by rows without the mark
      await setF.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row").forEach((r) => r.setAttribute("data-old", "1")));
      const t0 = st.tunnelsAnswered;
      setF = await openPanel(page);
      over.bDrawn = await setF.waitForFunction(() => document.querySelectorAll("#rs-logins .rs-login-row").length === 2
        && !document.querySelector("#rs-logins .rs-login-row[data-old]"), null, { timeout: 8000 }).then(() => true, () => false);
      over.bAnswered = st.tunnelsAnswered - t0;
      over.aStillHeld = st.heldOne;
      over.b = await options(setF, "spare");
      // the second open's rows are kept to compare: a drawing after the first open's late answer would replace them
      await setF.evaluate(() => { window.__bRows = Array.from(document.querySelectorAll("#rs-logins .rs-login-row")); });
      st.releaseOne = true;
      for (let i = 0; i < 150 && !st.oneAnswered; i++) await sleep(20);   // loop-ok: bounded at 3 s
      over.aReleased = st.oneAnswered;
      // the first open's kernel list has reached the page once the machine selector, which fill() fills from the same answer just
      // before the rows' drawing would go out, lists api-box
      over.aListed = await setF.waitForFunction(() => Array.from(document.querySelectorAll("#rs-scope-list .rs-scope-opt"))
        .some((o) => (o.textContent || "").trim() === "api-box"), null, { timeout: 4000 }).then(() => true, () => false);
      await sleep(500);   // a drawing that answer set off has gone out by now
      over.after = await options(setF, "spare");
      over.bRowsKept = await setF.evaluate(() => (window.__bRows || []).filter((r) => r.isConnected).length);
    } catch (e) {
      over.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.holdOne = null; st.releaseOne = true;
    if (ctx) await ctx.close();
  }
  {
    // 8. the kept pick's edges, each read once spare's row is drawn again by a reopen of settings (closing settings keeps a pick
    // not yet sent): the reopen shows the pick and Link again; picking the placeholder drops the pick; so does its machine
    // leaving the kernel list; a pick on a login that is then linked elsewhere and unlinked does not come back. Last, Link on a
    // pick the kernel refuses (api-box), with the POST held: a reopen meanwhile draws nothing, and after the refusal a reopen
    // shows no pick and no Link. spare is unlinked first if a scene above left it linked
    const edges = {};
    out.edges = edges;
    let ctx = null;
    try {
      const spare = (await kernelLogins()).find((r) => r.label === "spare");
      if (spare.linked) await door({ unlink: spare.id });
      st.tunnels = cfg.tunnels2;
      let page, setF;
      ({ ctx, page } = await context({ width: 1200, height: 800 }, false));
      setF = await openSettings(page);
      await waitOption(setF, cfg.tunnels2[0].host);
      const pick = (host) => setF.selectOption(ROW("spare") + " select.rs-login-link", host);
      // close and reopen settings: true once spare's row is drawn anew, with its picker (or its Unlink when want is "unlink")
      const reopen = async (want) => {
        await setF.evaluate((sel) => { window.__edgeRow = document.querySelector(sel); }, ROW("spare"));
        await closeSettings(page, setF);
        setF = await openPanel(page);
        return setF.waitForFunction(([sel, w]) => {
          if (!window.__edgeRow || window.__edgeRow.isConnected) return false;
          const r = document.querySelector(sel);
          return !!r && !!r.querySelector(w === "unlink" ? ".rs-login-unlink" : "select.rs-login-link");
        }, [ROW("spare"), want || "picker"], { timeout: 6000 }).then(() => true, () => false);
      };
      await pick("TESTHOST");
      edges.reopened = { redrawn: await reopen(), row: await rowState(setF, "spare") };
      await pick("");
      edges.unpicked = { redrawn: await reopen(), row: await rowState(setF, "spare") };
      await pick(cfg.tunnels2[0].host);
      st.tunnels = [];
      edges.gone = { redrawn: await reopen(), row: await rowState(setF, "spare"), options: await options(setF, "spare") };
      st.tunnels = cfg.tunnels2;
      await pick("TESTHOST");
      await door({ link: { login: spare.id, sameAs: "local" } });
      edges.linkedElsewhere = { redrawn: await reopen("unlink"), row: await rowState(setF, "spare") };
      await door({ unlink: spare.id });
      edges.relisted = { redrawn: await reopen(), row: await rowState(setF, "spare") };
      // …and Link pressed on api-box with its POST held on the way to the kernel. The reopen meanwhile holds /tunnels until its
      // /logins answer has arrived and is then answered with web-box: once the machine selector lists web-box its drawing has
      // been decided
      st.tunnels = cfg.tunnels1;
      edges.listed = await reopen();
      await pick(cfg.tunnels1[0].host);
      const posts0 = st.posts.length;
      st.holdPost = true;
      if (await setF.$(ROW("spare") + " .rs-login-go")) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
      for (let i = 0; i < 100 && st.posts.length === posts0; i++) await sleep(20);   // loop-ok: bounded at 2 s
      await setF.evaluate((sel) => { window.__edgeRow = document.querySelector(sel); }, ROW("spare"));
      await closeSettings(page, setF);
      st.tunnels = cfg.tunnels2;
      const answered0 = st.answeredWhileHeld;
      st.hold = true;
      setF = await openPanel(page);
      for (let i = 0; i < 150 && !(st.held > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
      const answered = st.answeredWhileHeld - answered0;
      st.hold = false;
      edges.inFlight = { answered, listed: await scopeLists(setF, cfg.tunnels2[0].host), row: await rowState(setF, "spare"),
        kept: await setF.evaluate(() => !!window.__edgeRow && window.__edgeRow.isConnected), posts: st.posts.slice(posts0) };
      st.holdPost = false;
      edges.refused = await waitRow(setF, "spare", ".rs-note.rs-login-bad");
      st.tunnels = cfg.tunnels1;
      edges.afterRefusal = { redrawn: await reopen(), row: await rowState(setF, "spare"), options: await options(setF, "spare") };
      edges.posts = st.posts.slice(posts0);
      edges.kernel = await kernelLogins();
    } catch (e) {
      edges.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.holdPost = false; st.hold = false; st.tunnels = cfg.tunnels2;
    if (ctx) await ctx.close();
  }
  // Scenes 9 and 10: an older open overlapping a newer one, each on a fresh page. olderOpen opens settings with no kernel
  // connected and closes it, reopens with the /tunnels asked meanwhile held (armed with api-box) until its /logins answer has
  // arrived, and closes, the rows on screen marked so a later drawing is known by rows without the mark; web-box is then
  // connected for the newer open. spare is unlinked first, so its row has the picker to read
  async function olderOpen(rec) {
    const spare = (await kernelLogins()).find((r) => r.label === "spare");
    if (spare.linked) await door({ unlink: spare.id });
    st.tunnels = [];
    const { ctx, page } = await context({ width: 1200, height: 800 }, false);
    let setF = await openSettings(page);
    await closeSettings(page, setF);
    const answered0 = st.answeredWhileHeld;
    st.releaseOne = false; st.oneAnswered = false; st.holdOne = cfg.tunnels1;
    setF = await openPanel(page);
    for (let i = 0; i < 150 && !(st.heldOne > 0 && st.answeredWhileHeld > answered0); i++) await sleep(20);   // loop-ok: bounded at 3 s
    rec.aHeld = st.heldOne; rec.aAnsweredWhileHeld = st.answeredWhileHeld - answered0;
    st.holdOne = null;   // disarmed: the newer open's /tunnels is answered as usual
    await closeSettings(page, setF);
    await setF.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row").forEach((r) => r.setAttribute("data-old", "1")));
    st.tunnels = cfg.tunnels2;
    return { ctx, page, setF };
  }
  const freshRows = (setF) => setF.waitForFunction(() => document.querySelectorAll("#rs-logins .rs-login-row").length === 2
    && !document.querySelector("#rs-logins .rs-login-row[data-old]"), null, { timeout: 4000 }).then(() => true, () => false);
  const freshCount = (setF) => setF.evaluate(() => document.querySelectorAll("#rs-logins .rs-login-row:not([data-old])").length);
  // the older open's /tunnels released: answered with api-box, which then reaches the machine selector
  async function releaseOlder(setF, rec) {
    st.releaseOne = true;
    for (let i = 0; i < 150 && !st.oneAnswered; i++) await sleep(20);   // loop-ok: bounded at 3 s
    rec.aReleased = st.oneAnswered;
    rec.aListed = await scopeLists(setF, cfg.tunnels1[0].host);
    await sleep(300);   // its drawing, or its wait on the newer open, has been decided by now
  }
  {
    // 9a. the newer open's GET /logins is answered 500 and its /tunnels at once; then the older open's /tunnels is released. The
    // older open draws, its pickers listing the kernels the newer open read (web-box), not the older list that arrived last
    const nf = {};
    out.newerFails = nf;
    let ctx = null;
    try {
      let page, setF;
      ({ ctx, page, setF } = await olderOpen(nf));
      const f0 = st.failedGets;
      st.failGet = true;
      setF = await openPanel(page);
      for (let i = 0; i < 200 && st.failedGets === f0; i++) await sleep(20);   // loop-ok: bounded at 4 s
      nf.bFailed = st.failedGets - f0;
      nf.bListed = await scopeLists(setF, cfg.tunnels2[0].host);   // the newer open's /tunnels has landed
      await sleep(300);   // its failed read has been seen by now
      nf.freshBefore = await freshCount(setF);
      await releaseOlder(setF, nf);
      nf.drawn = await freshRows(setF);
      nf.after = await options(setF, "spare");
    } catch (e) {
      nf.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.failGet = false; st.holdOne = null; st.releaseOne = true;
    if (ctx) await ctx.close();
  }
  {
    // 9b. the newer open's GET /logins is answered 500 while its /tunnels is held until the older open's has been released and
    // has landed: the older open draws nothing until the newer open's /tunnels settles, then draws, listing web-box
    const nf = {};
    out.newerFailsHeld = nf;
    let ctx = null;
    try {
      let page, setF;
      ({ ctx, page, setF } = await olderOpen(nf));
      const f0 = st.failedGets;
      st.failGet = true; st.hold = true;
      setF = await openPanel(page);
      for (let i = 0; i < 200 && !(st.failedGets > f0 && st.held - st.heldOne > 0); i++) await sleep(20);   // loop-ok: bounded at 4 s
      nf.bFailed = st.failedGets - f0; nf.bHeld = st.held - st.heldOne;
      await sleep(300);   // its failed read has been seen by now
      await releaseOlder(setF, nf);
      nf.freshBefore = await freshCount(setF);
      st.hold = false;
      nf.bListed = await scopeLists(setF, cfg.tunnels2[0].host);
      nf.drawn = await freshRows(setF);
      nf.after = await options(setF, "spare");
    } catch (e) {
      nf.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.failGet = false; st.hold = false; st.holdOne = null; st.releaseOne = true;
    if (ctx) await ctx.close();
  }
  {
    // 10. the newer open's GET /logins is held until the older open's /tunnels, released after the newer one's, has landed: the
    // newer open's drawing lists the kernels it read (web-box), not the older list that arrived last (api-box). Then work is
    // unlinked through the kernel's door and spare linked with its picker and Link: the drawing after the link, which does not
    // wait on a kernel list, lists web-box too
    const late = {};
    out.lateList = late;
    let ctx = null;
    try {
      let page, setF;
      ({ ctx, page, setF } = await olderOpen(late));
      st.releaseGet = false; st.failHeld = false; st.holdGet = true;
      setF = await openPanel(page);
      for (let i = 0; i < 200 && st.getHeld === 0; i++) await sleep(20);   // loop-ok: bounded at 4 s
      late.bGetHeld = st.getHeld;
      late.bListed = await scopeLists(setF, cfg.tunnels2[0].host);   // the newer open's /tunnels has landed
      await releaseOlder(setF, late);
      late.freshBefore = await freshCount(setF);
      st.releaseGet = true;
      late.drawn = await freshRows(setF);
      late.after = await options(setF, "spare");
      const work = (await kernelLogins()).find((r) => r.label === "work");
      if (work.linked) await door({ unlink: work.id });
      await setF.selectOption(ROW("spare") + " select.rs-login-link", "TESTHOST");
      if (await setF.$(ROW("spare") + " .rs-login-go")) await setF.click(ROW("spare") + " .rs-login-go", { timeout: 4000 });
      await setF.waitForFunction((sel) => { const r = document.querySelector(sel); return !!r && !!r.querySelector(".rs-login-unlink"); }, ROW("spare"), { timeout: 8000 }).catch(() => {});
      late.linked = await rowState(setF, "spare");
      late.workAfterLink = await options(setF, "work");
    } catch (e) {
      late.error = String((e && e.stack) || e).slice(0, 1500);
    }
    st.holdGet = false; st.releaseGet = true; st.holdOne = null; st.releaseOne = true;
    if (ctx) await ctx.close();
  }
  await inflightRefused();
  await inflightOk();
  await inflightUnlink();
  await refusedFirst();
  await okWindow();
  await noAnswer();
  await pressAcross();
  await waitingSkip();
  await waitingRefused();
  await twoInflight();
  await failedReRead("Before");
  await failedReRead("After");
  await refusalThenFailedReRead();
  await removeAtRelease();
  await removeThenFailedReRead();
} catch (e) {
  out.error = String((e && e.stack) || e).slice(0, 1500);
}
await browser.close();
fs.writeFileSync(cfg.out, JSON.stringify(out));
"""


class ServedLoginLinkPicker(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here): the served guard needs them")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one")
        cls.lab = tempfile.mkdtemp(prefix="login-link-picker-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        os.makedirs(state, exist_ok=True)
        # a lab root writes its own session-hosts off (the conftest rule): hosts are on by default
        Path(state, "session-hosts").write_text("off\n")
        # the machine's sign-in: the account file in the kernel's own home, and again in its Claude config dir (where the CLI
        # keeps it when CLAUDE_CONFIG_DIR is set), and the subscription the kind word is read from
        claude = os.path.join(cls.lab, "claude")
        home = os.path.join(cls.lab, "home")
        for d in (claude, home):
            os.makedirs(d, exist_ok=True)
            Path(d, ".claude.json").write_text(json.dumps(ACCOUNT))
        Path(claude, ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"subscriptionType": "team"}}))
        cls.port, cls.token = _free_port(), "testtok-login-link-picker"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST", HOME=home)
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
        cls.ids = {}
        for label in ("work", "spare"):   # through the kernel's own door; the token commands are never run here
            ans = cls._post({"add": {"label": label, "tokenCmd": "cat ~/.romp-lab-%s-token" % label}})
            if not ans.get("ok"):
                raise AssertionError("the kernel refused the stored login %r: %r" % (label, ans))
            cls.ids[label] = ans["id"]

    @classmethod
    def _post(cls, body):
        req = urllib.request.Request("http://127.0.0.1:%d/logins" % cls.port, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "X-Romp-Token": cls.token})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())

    @classmethod
    def tearDownClass(cls):
        k = getattr(cls, "kernel", None)
        if k:
            k.terminate()
            try:
                k.wait(timeout=10)
            except subprocess.TimeoutExpired:
                k.kill(); k.wait()
            time.sleep(0.5)
        lab = getattr(cls, "lab", "")
        if lab:
            shutil.rmtree(lab, ignore_errors=True)
            time.sleep(0.3)
            shutil.rmtree(lab, ignore_errors=True)

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
        base = "http://127.0.0.1:%d" % cls.port
        with open(cfg, "w") as f:
            json.dump({"url": base + "/?token=" + cls.token, "base": base, "token": cls.token, "out": out, "org": ORG,
                       "tunnels1": [{"host": "api-box", "status": "up", "settings": {}}],
                       "tunnels2": [{"host": "web-box", "status": "up", "settings": {}}]}, f)
        driver = os.path.join(cls.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=240,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box: the served guard needs one")
        if p.returncode != 0 or not os.path.exists(out):
            raise AssertionError("driver failed:\n" + p.stdout[-3000:] + p.stderr[-3000:] + "\nkernel:\n" + open(cls.klog).read()[-1500:])
        result = json.loads(Path(out).read_text())
        if os.environ.get("LINK_PICKER_DUMP"):
            Path(os.environ["LINK_PICKER_DUMP"]).write_text(json.dumps(result, indent=1, ensure_ascii=False))
        if result.get("error"):
            raise AssertionError("the driver threw: " + result["error"] + "\n  read so far: " + json.dumps(result)[:2500]
                                 + "\nkernel:\n" + open(cls.klog).read()[-1200:])
        return result

    def test_a_first_open_draws_the_rows_once_the_kernels_are_known(self):
        r = self._run()
        held = r["held1"]
        self.assertGreaterEqual(held["heldNow"], 1, "the panel was read while /tunnels was still held: %r" % held)
        self.assertGreaterEqual(held["answeredWhileHeld"], 1, "a /logins answer arrived while /tunnels was held (the read goes out "
                                "beside /tunnels), so no row means the rows waited: %r" % held)
        self.assertEqual(held["rows"], 0, "no stored-login row is drawn before the kernel list arrives: %r" % held)
        self.assertEqual(r["open1"], ["", "TESTHOST", "api-box"], "once it has, the first open lists this machine and the connected kernel")

    def test_the_signed_in_line_does_not_wait_for_the_kernels(self):
        held = self._run()["held1"]
        self.assertGreaterEqual(held["heldNow"], 1, "the panel was read while /tunnels was still held: %r" % held)
        self.assertIn(ORG, held["who"], "with /tunnels held, the signed-in line already names the organisation the /logins answer "
                      "carries (the /version read names the email alone): %r" % held)

    def test_the_description_is_said_on_the_first_opens_answer(self):
        # the first open is what this measures: the second open has the same stored logins, and the description outlives an open,
        # so its equality below is a consistency read and could not tell a sentence said on its answer from one left standing
        r = self._run()
        one, two = r["held1"], r["held2"]
        for held in (one, two):
            self.assertGreaterEqual(held["answeredWhileHeld"], 1, "read once a /logins answer had arrived, /tunnels still held: %r" % held)
        self.assertIn("For the other logins below", one["desc"], "the first open says the stored logins' sentence on its /logins "
                      "answer, before /tunnels answers: %r" % one)
        self.assertEqual(one["desc"], two["desc"], "the same stored logins at both opens, so the same description: %r" % [one, two])

    def test_each_open_reads_the_stored_logins_once(self):
        r = self._run()
        self.assertEqual((r["gets1"], r["gets2"]), (1, 1), "one GET /logins per open (the read is not repeated once /tunnels settles)")

    def test_a_later_open_lists_the_kernels_connected_then(self):
        r = self._run()
        held = r["held2"]
        self.assertGreaterEqual(held["heldNow"], 1, "the later open was read while /tunnels was still held: %r" % held)
        self.assertGreaterEqual(held["answeredWhileHeld"], 1, "its /logins answer arrived while /tunnels was held, the order in which "
                                "rows drawn on the answer listed the previous open's kernels: %r" % held)
        self.assertEqual(r["open2"], ["", "TESTHOST", "web-box"],
                         "once the kernel list has arrived, a later open lists the kernels connected now: web-box in, api-box out")

    def test_a_failed_kernel_list_still_draws_the_stored_logins(self):
        f = self._run()["failed"]
        self.assertGreaterEqual(f["served500"], 1, "the settings frame's /tunnels was answered 500 (an empty kernel list draws the same "
                                "rows and picker): %r" % f)
        self.assertEqual(f["prior"], ["", "TESTHOST", "web-box"], "the page's first open listed web-box: %r" % f)
        self.assertTrue(f["fresh"], "the rows read are the failed open's own drawing: %r" % f)
        self.assertEqual(f["rows"], 2, "a /tunnels answered 500 still shows both stored logins: %r" % f)
        self.assertEqual(f["spare"], ["", "TESTHOST"], "…the picker listing this machine alone: %r" % f)

    def test_an_arrow_key_on_the_picker_links_nothing(self):
        r = self._run()
        self.assertEqual(r["focused"]["tag"], "SELECT", r["focused"])
        self.assertEqual(r["arrowPosts"], [], "ArrowDown, ArrowUp and ArrowDown on the closed picker send no link")
        work = [x for x in r["kernelAfterArrow"] if x["label"] == "work"]
        self.assertEqual(work, [{"id": self.ids["work"], "label": "work", "linked": None}], "…and the kernel recorded none")
        self.assertEqual(r["afterArrow"]["value"], "TESTHOST", "the arrow moved the pick to the first machine: %r" % r["afterArrow"])
        self.assertEqual(r["afterArrow"]["link"], {"shown": True}, "the pick offers Link beside the picker: %r" % r["afterArrow"])
        self.assertEqual(r["linkAtRest"], {"shown": False}, "with no pick there is no Link to press")
        self.assertEqual(r["afterArrowUp"]["value"], "", "ArrowUp moved the pick back to the placeholder: %r" % r["afterArrowUp"])
        self.assertEqual(r["afterArrowUp"]["link"], {"shown": False}, "…and Link went with the pick: %r" % r["afterArrowUp"])
        self.assertEqual(r["afterArrowAgain"]["link"], {"shown": True}, "a pick again brings it back: %r" % r["afterArrowAgain"])

    def test_link_sends_the_pick_from_the_keyboard(self):
        r = self._run()
        self.assertIn("rs-login-go", r["tabbed"]["cls"], "Tab from the picker reaches Link: %r" % r["tabbed"])
        self.assertEqual(r["inFlight"]["link"]["shown"], False, "Link goes once pressed, while the link is on its way: %r" % r["inFlight"])
        self.assertTrue(r["inFlight"]["disabled"], "…and the picker is disabled meanwhile: %r" % r["inFlight"])
        self.assertEqual(r["enterPosts"], [{"link": {"login": self.ids["work"], "sameAs": "TESTHOST"}}],
                         "Enter on Link sends the pick once; Enter again while it is on its way sends nothing")
        self.assertTrue(r["linkedRow"]["unlink"], "the row reads linked, with Unlink: %r" % r["linkedRow"])
        linked = {x["label"]: x["linked"] for x in r["kernelLinked"]}
        self.assertEqual(linked, {"work": "TESTHOST", "spare": None}, "the kernel recorded the one link, to this machine's sign-in")

    def _inside_the_card(self, width):
        m = self._run()[width]
        kinds = sorted((x["linked"], x["picker"]) for x in m["rows"])
        self.assertEqual(kinds, [(False, True), (True, False)], "%s: one linked row and one with the picker: %r" % (width, m))
        for row in m["rows"]:
            self.assertTrue(row["lastIsRemove"], "%s: the row ends in its Remove: %r" % (width, row))
            self.assertLessEqual(row["pastCard"], 0.5, "%s: Remove ends inside the card (%s px past its edge): %r" % (width, row["pastCard"], m))
        self.assertLessEqual(m["sideScroll"], 0, "%s: the card does not scroll sideways: %r" % (width, m))

    def test_at_desktop_width_the_rows_stay_inside_the_card(self):
        self._inside_the_card("desktop")

    def test_at_phone_width_the_rows_stay_inside_the_card(self):
        self._inside_the_card("phone")

    def test_at_390px_a_pick_with_link_beside_it_stays_inside_the_card(self):
        # 390x844 only: below about 360 px a row showing Link still runs past the card
        self.assertEqual(self._run()["phonePickedLink"], {"shown": True}, "the pick shows Link")
        self._inside_the_card("phonePicked")

    def test_link_is_styled_like_remove(self):
        d = self._run()["phonePickedDress"]
        self.assertTrue(d and d["link"] and d["remove"], "Link showing beside the row's Remove: %r" % d)
        self.assertEqual(d["link"], d["remove"], "Link is the row's own button, as Remove is, not the browser's default one")

    def test_a_link_made_while_the_kernels_are_read_stays_drawn(self):
        s = self._run()["stale"]
        self.assertNotIn("error", s, s)
        self.assertGreaterEqual(s["heldNow"], 1, "the later open was read while /tunnels was held: %r" % s)
        self.assertGreaterEqual(s["answeredWhileHeld"], 1, "its /logins answer, older than the link, arrived meanwhile: %r" % s)
        self.assertEqual((s["before"] or {}).get("picker"), True, "spare's row, drawn by the first open, offered the picker: %r" % s)
        self.assertGreaterEqual(s["stillHeld"], 1, "the link was answered and drawn with /tunnels still held: %r" % s)
        self.assertTrue(s["redrawn"], "the drawing that waited on /tunnels came after the link, replacing the row it drew: %r" % s)
        self.assertTrue((s["linked"] or {}).get("unlink"), "the link's own re-read drew the row linked: %r" % s)
        spare = [x["linked"] for x in s["kernel"] if x["label"] == "spare"]
        self.assertEqual(spare, ["TESTHOST"], "the kernel holds the link: %r" % s)
        self.assertTrue((s["after"] or {}).get("unlink"), "once /tunnels answered and the rows were drawn again, the row still reads "
                        "linked, as the kernel holds it, not as the open's older /logins answer had it: %r" % s)

    def test_a_pick_outlives_the_redraws_after_a_synchronized_switch(self):
        k = self._run()["keep"]
        self.assertNotIn("error", k, k)
        self.assertEqual((k["picked"] or {}).get("value"), "TESTHOST", "the pick was made: %r" % k)
        self.assertTrue(k["redrawn"], "the fill the switch set off drew the rows again after the pick (%r GET /logins): %r" % (k.get("gets"), k))
        self.assertEqual((k["after"] or {}).get("value"), "TESTHOST", "the redrawn row keeps the picked machine: %r" % k)
        self.assertEqual((k["after"] or {}).get("link"), {"shown": True}, "…and Link beside it: %r" % k)
        self.assertTrue(k["redrawn2"], "the next re-run drew the rows again: %r" % k)
        self.assertEqual((k["after2"] or {}).get("value"), "TESTHOST", "the pick outlives that drawing too: %r" % k)
        self.assertEqual((k["after2"] or {}).get("link"), {"shown": True}, "…with Link: %r" % k)
        self.assertEqual(k["postsBeforeLink"], [], "the pick sent nothing on its own: %r" % k)
        self.assertEqual(k["posts"], [{"link": {"login": self.ids["spare"], "sameAs": "TESTHOST"}}],
                         "Link on the redrawn row sends that pick, once: %r" % k)
        spare = [x["linked"] for x in k["kernel"] if x["label"] == "spare"]
        self.assertEqual(spare, ["TESTHOST"], "…and the kernel holds the link: %r" % k)

    def test_an_older_open_answering_late_leaves_the_pickers_alone(self):
        o = self._run()["overlap"]
        self.assertNotIn("error", o, o)
        self.assertGreaterEqual(o["aHeld"], 1, "the first open's /tunnels was held: %r" % o)
        self.assertGreaterEqual(o["aAnsweredWhileHeld"], 1, "its /logins answer arrived meanwhile, so only the kernel list kept its "
                                "drawing waiting: %r" % o)
        self.assertTrue(o["bDrawn"], "the second open drew rows of its own: %r" % o)
        self.assertGreaterEqual(o["bAnswered"], 1, "…once its /tunnels answered: %r" % o)
        self.assertGreaterEqual(o["aStillHeld"], 1, "…with the first open's still held: %r" % o)
        self.assertEqual(o["b"], ["", "TESTHOST", "web-box"], "the second open lists the kernel connected then: %r" % o)
        self.assertTrue(o["aReleased"], "the first open's /tunnels was answered after it, with api-box: %r" % o)
        self.assertTrue(o["aListed"], "…and that list reached the page: the machine selector lists api-box: %r" % o)
        self.assertEqual(o["after"], ["", "TESTHOST", "web-box"], "the pickers still list what the newer open read, not the "
                         "older open's api-box: %r" % o)
        self.assertEqual(o["bRowsKept"], 2, "the older open drew nothing: the rows the newer open drew are still the ones shown: %r" % o)

    def _edges(self):
        e = self._run()["edges"]
        self.assertNotIn("error", e, e)
        return e

    def test_a_pick_outlives_closing_and_reopening_settings(self):
        e = self._edges()["reopened"]
        self.assertTrue(e["redrawn"], "the reopen drew spare's row again: %r" % e)
        self.assertEqual((e["row"]["value"], e["row"]["link"]), ("TESTHOST", {"shown": True}),
                         "the pick not yet sent is back after the reopen, with Link: %r" % e)

    def test_the_placeholder_drops_a_kept_pick(self):
        e = self._edges()["unpicked"]
        self.assertTrue(e["redrawn"], "the reopen drew spare's row again: %r" % e)
        self.assertEqual((e["row"]["value"], e["row"]["link"]), ("", {"shown": False}),
                         "a pick taken back with the placeholder stays taken back: no machine and no Link: %r" % e)

    def test_a_pick_on_a_machine_gone_from_the_list_is_dropped(self):
        e = self._edges()["gone"]
        self.assertTrue(e["redrawn"], "the reopen drew spare's row again: %r" % e)
        self.assertEqual(e["options"], ["", "TESTHOST"], "web-box, the picked machine, is no longer listed: %r" % e)
        self.assertEqual((e["row"]["value"], e["row"]["link"]), ("", {"shown": False}),
                         "the row shows the placeholder and no Link, not Link beside a machine it cannot send: %r" % e)

    def test_a_pick_does_not_return_after_its_login_is_linked_elsewhere_and_unlinked(self):
        e = self._edges()
        self.assertTrue(e["linkedElsewhere"]["redrawn"] and e["linkedElsewhere"]["row"]["unlink"],
                        "linked through the kernel's door, the redrawn row reads linked: %r" % e["linkedElsewhere"])
        r = e["relisted"]
        self.assertTrue(r["redrawn"], "unlinked again, the row was drawn with its picker: %r" % r)
        self.assertEqual((r["row"]["value"], r["row"]["link"]), ("", {"shown": False}),
                         "the pick made before the link elsewhere does not come back: %r" % r)

    def test_a_reopen_draws_nothing_while_a_link_is_held_and_after_its_refusal_shows_no_pick_or_link(self):
        e = self._edges()
        self.assertTrue(e["listed"], "a reopen with api-box connected drew spare's row with its picker: %r" % e)
        f = e["inFlight"]
        link = {"link": {"login": self.ids["spare"], "sameAs": "api-box"}}
        self.assertEqual(f["posts"], [link], "Link sent the pick, and the POST was held on its way: %r" % f)
        self.assertGreaterEqual(f["answered"], 1, "the reopen's /logins answer arrived while its /tunnels was held: %r" % f)
        self.assertTrue(f["listed"], "…and its /tunnels answer reached the page: %r" % f)
        self.assertTrue(f["kept"], "the reopen drew nothing: spare's row is still the one Link was pressed on: %r" % f)
        self.assertEqual(f["row"]["note"], "Linking…", "…and it still says Linking…: %r" % f)
        self.assertTrue(e["refused"], "the kernel refused the link: %r" % e)
        a = e["afterRefusal"]
        self.assertTrue(a["redrawn"], "a reopen after the refusal drew spare's row again: %r" % a)
        self.assertIn("api-box", a["options"], "with api-box listed: %r" % a)
        self.assertEqual((a["row"]["value"], a["row"]["link"]), ("", {"shown": False}),
                         "the redrawn row shows no pick and no Link: %r" % a)
        self.assertEqual(e["posts"], [link], "one link POST in all: %r" % e)
        spare = [x["linked"] for x in e["kernel"] if x["label"] == "spare"]
        self.assertEqual(spare, [None], "…and the kernel holds no link: %r" % e)

    def _overlap(self, key):
        o = self._run()[key]
        self.assertNotIn("error", o, o)
        self.assertGreaterEqual(o["aHeld"], 1, "the older open's /tunnels was held: %r" % o)
        self.assertGreaterEqual(o["aAnsweredWhileHeld"], 1, "its /logins answer arrived meanwhile, so only the kernel list kept its "
                                "drawing waiting: %r" % o)
        self.assertTrue(o["aReleased"] and o["aListed"], "its /tunnels was answered later, with api-box, and reached the page (the "
                        "machine selector lists api-box): %r" % o)
        return o

    def test_an_older_open_draws_when_the_newer_opens_read_fails(self):
        o = self._overlap("newerFails")
        self.assertGreaterEqual(o["bFailed"], 1, "the newer open's GET /logins was answered 500: %r" % o)
        self.assertTrue(o["bListed"], "…and its /tunnels, web-box, landed before the older open's: %r" % o)
        self.assertEqual(o["freshBefore"], 0, "nothing was drawn before the older open's /tunnels answered: %r" % o)
        self.assertTrue(o["drawn"], "the older open drew the rows once its /tunnels answered, the newer open's read having failed: %r" % o)
        self.assertEqual(o["after"], ["", "TESTHOST", "web-box"], "…listing the kernels the newer open read, not the older list "
                         "that arrived last: %r" % o)

    def test_an_older_open_waits_for_the_failed_opens_kernels_before_it_draws(self):
        o = self._overlap("newerFailsHeld")
        self.assertGreaterEqual(o["bFailed"], 1, "the newer open's GET /logins was answered 500: %r" % o)
        self.assertGreaterEqual(o["bHeld"], 1, "…with its /tunnels held: %r" % o)
        self.assertEqual(o["freshBefore"], 0, "the older open drew nothing while the newer open's /tunnels was held: %r" % o)
        self.assertTrue(o["bListed"] and o["drawn"], "once that /tunnels landed, the rows were drawn: %r" % o)
        self.assertEqual(o["after"], ["", "TESTHOST", "web-box"], "…listing the kernels it read: %r" % o)

    def test_the_newest_open_lists_the_kernels_it_read_not_the_last_to_arrive(self):
        o = self._overlap("lateList")
        self.assertGreaterEqual(o["bGetHeld"], 1, "the newer open's GET /logins was held: %r" % o)
        self.assertTrue(o["bListed"], "…and its /tunnels, web-box, landed before the older open's: %r" % o)
        self.assertEqual(o["freshBefore"], 0, "the older open drew nothing, a newer open being on its way: %r" % o)
        self.assertTrue(o["drawn"], "the newer open drew the rows once its /logins answered: %r" % o)
        self.assertEqual(o["after"], ["", "TESTHOST", "web-box"], "…listing the kernels it read, not api-box, which arrived last: %r" % o)

    def test_a_drawing_after_a_link_lists_the_newest_settled_opens_kernels(self):
        o = self._overlap("lateList")
        self.assertTrue((o.get("linked") or {}).get("unlink"), "spare was linked with its picker and Link: %r" % o)
        self.assertEqual(o["workAfterLink"], ["", "TESTHOST", "web-box"], "the drawing after the link, which waits on no kernel list, "
                         "lists the kernels of the newest open whose /tunnels read has settled, not api-box, which arrived last: %r" % o)

    def _kept(self, key):
        s = self._run()[key]
        self.assertNotIn("error", s, s)
        self.assertGreaterEqual(s["heldNow"], 1, "the reopen's /tunnels was held: %r" % s)
        self.assertGreaterEqual(s["answeredWhileHeld"], 1, "…and its /logins answer arrived meanwhile: %r" % s)
        return s

    def _link(self, host):
        return {"link": {"login": self.ids["spare"], "sameAs": host}}

    def _spare(self, s):
        return [x["linked"] for x in s["kernel"] if x["label"] == "spare"]

    def test_a_refused_link_keeps_linking_across_the_kernel_lists_arrival_then_shows_the_refusal(self):
        s = self._kept("inflightRefused")
        self.assertEqual(s["posts"], [self._link("api-box")], "Link sent the kept pick and the POST was held: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page: %r" % s)
        a = s["afterRelease"]
        self.assertEqual((a["note"], a["disabled"], a["link"]), ("Linking…", True, {"shown": False}),
                         "after it the row still says Linking…, with the picker disabled and no Link: %r" % s)
        self.assertTrue(s["refused"], "the kernel's refusal is shown in the row: %r" % s)
        b = s["afterAnswer"]
        self.assertEqual((b["bad"], b["value"], b["disabled"]), (True, "", False),
                         "…with the picker back at its placeholder: %r" % s)
        self.assertTrue(s["keptAfterAnswer"], "the rows are still the ones drawn before the reopen: %r" % s)
        self.assertEqual(s["work"], ["", "TESTHOST", "api-box"],
                         "…so work's picker lists the first open's kernels, not web-box, which the reopen read: %r" % s)
        self.assertEqual(self._spare(s), [None], "the kernel holds no link: %r" % s)

    def test_a_taken_link_keeps_linking_across_the_kernel_lists_arrival_and_a_reopen_then_draws(self):
        s = self._kept("inflightOk")
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the kept pick and the POST was held: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page: %r" % s)
        a = s["afterRelease"]
        self.assertEqual((a["note"], a["disabled"], a["link"]), ("Linking…", True, {"shown": False}),
                         "after it the row still says Linking…: %r" % s)
        self.assertTrue(s["linked"] and s["afterAnswer"]["unlink"], "then the row reads linked: %r" % s)
        self.assertEqual(s["work"], ["", "TESTHOST", "web-box"], "…and work's picker lists the reopen's kernels: %r" % s)
        self.assertEqual(self._spare(s), ["TESTHOST"], "the kernel holds the link: %r" % s)
        self.assertTrue(s["reopenDrawn"], "a reopen after it draws the rows: %r" % s)

    def test_a_refused_unlink_keeps_unlinking_across_the_kernel_lists_arrival_then_shows_the_refusal(self):
        s = self._kept("inflightUnlink")
        self.assertTrue(s["kept"]["unlink"], "spare's kept row shows Unlink: %r" % s)
        self.assertEqual(s["posts"], [{"unlink": self.ids["spare"]}], "Unlink was sent and the POST held: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page: %r" % s)
        self.assertEqual(s["afterRelease"]["ul"], {"text": "Unlinking…", "disabled": True},
                         "after it the row still says Unlinking…: %r" % s)
        self.assertTrue(s["refused"], "the kernel's refusal (spare is not linked) is shown in the row: %r" % s)
        self.assertEqual(s["afterAnswer"]["ul"], {"text": "Unlink", "disabled": False}, "…with Unlink back: %r" % s)

    def test_a_refusal_shown_before_the_kernel_list_arrives_is_drawn_over_when_it_does(self):
        s = self._kept("refusedFirst")
        self.assertEqual(s["posts"], [self._link("api-box")], "Link sent the kept pick: %r" % s)
        self.assertTrue(s["refused"] and s["refusedRow"]["bad"], "the refusal is shown: %r" % s)
        self.assertGreaterEqual(s["heldAtRefusal"], 1, "…while the reopen's /tunnels was still held: %r" % s)
        self.assertTrue(s["released"] and s["drawn"], "its answer, web-box, drew the rows again: %r" % s)
        self.assertFalse(s["after"]["bad"], "the redrawn row no longer shows the refusal: %r" % s)
        self.assertEqual((s["spare"], s["work"]), (["", "TESTHOST", "web-box"],) * 2, "the pickers list the reopen's kernels: %r" % s)

    def test_after_an_ok_the_row_keeps_linking_until_its_re_read_is_drawn(self):
        s = self._kept("okWindow")
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the kept pick: %r" % s)
        self.assertGreaterEqual(s["reReadHeld"], 1, "its ok reached the page and the re-read's GET /logins is held: %r" % s)
        self.assertEqual(self._spare({"kernel": s["kernelAtOk"]}), ["TESTHOST"], "the kernel holds the link: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page in that window: %r" % s)
        self.assertEqual((s["afterRelease"]["note"], s["keptAfterRelease"]), ("Linking…", True),
                         "the row still says Linking…: %r" % s)
        self.assertTrue(s["linked"] and s["afterReRead"]["unlink"], "once the re-read is answered the row reads linked: %r" % s)

    def test_a_link_with_no_answer_shows_the_refusal_and_a_reopen_then_draws(self):
        s = self._run()["noAnswer"]
        self.assertNotIn("error", s, s)
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the pick, answered 500 with a plain-text body: %r" % s)
        self.assertTrue(s["refused"], "the row shows a refusal: %r" % s)
        self.assertEqual(s["row"]["note"], "the kernel did not answer; try again", "…the one for no answer: %r" % s)
        self.assertTrue(s["reopenDrawn"], "a reopen after it draws the rows: %r" % s)
        self.assertEqual(self._spare(s), [None], "the kernel holds no link: %r" % s)

    def test_a_link_press_across_a_redraw_sends_nothing_and_the_redrawn_link_sends_one(self):
        s = self._kept("pressAcross")
        self.assertEqual((s["kept"]["value"], s["kept"]["link"]), ("TESTHOST", {"shown": True}),
                         "spare's kept row shows the pick and Link: %r" % s)
        self.assertTrue(s["redrawn"], "the mouse went down on Link, and the rows were drawn again before it went up: %r" % s)
        self.assertTrue(s["underAtUp"]["sparesLink"], "…with the redrawn row's Link under the pointer when it went up: %r" % s)
        self.assertEqual(s["postsAfterUp"], [], "the press sent nothing: %r" % s)
        self.assertEqual(s["kernelAfterUp"], s["kernelBefore"], "…and the kernel's logins are unchanged: %r" % s)
        self.assertEqual((s["afterUp"]["value"], s["afterUp"]["link"]), ("TESTHOST", {"shown": True}),
                         "the redrawn row shows the pick and Link: %r" % s)
        self.assertTrue(s["linked"], "one press of that Link links spare: %r" % s)
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "…with one link POST in all: %r" % s)
        self.assertEqual(self._spare(s), ["TESTHOST"], "the kernel holds the link: %r" % s)

    def _waiting(self, key):
        s = self._run()[key]
        self.assertNotIn("error", s, s)
        self.assertGreaterEqual(s["aHeld"], 1, "the older open's /tunnels was held: %r" % s)
        self.assertGreaterEqual(s["aAnsweredWhileHeld"], 1, "…and its /logins answer arrived meanwhile: %r" % s)
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the pick and the POST was held: %r" % s)
        self.assertGreaterEqual(s["bFailed"], 1, "the newer open's GET /logins was answered 500: %r" % s)
        self.assertGreaterEqual(s["bHeld"], 1, "…with its /tunnels held: %r" % s)
        self.assertTrue(s["aListed"], "the older open's /tunnels reached the page: %r" % s)
        return s

    def test_a_link_on_its_way_keeps_linking_when_an_older_open_waiting_on_a_failed_read_would_draw(self):
        s = self._waiting("waitingSkip")
        self.assertTrue(s["bListed"], "…then the newer open's: %r" % s)
        self.assertEqual((s["afterRelease"]["note"], s["keptAfterRelease"]), ("Linking…", True),
                         "the row Link was pressed on is still drawn and still says Linking…: %r" % s)
        self.assertTrue(s["linked"], "once the POST is answered the row reads linked: %r" % s)

    def test_a_link_with_no_answer_while_an_older_open_waits_on_a_failed_open_lets_it_draw_with_the_failed_opens_kernels(self):
        s = self._waiting("waitingRefused")
        self.assertTrue(s["refused"] and s["keptAtRefusal"], "then the no-answer note shows in the row it was pressed on: %r" % s)
        self.assertGreaterEqual(s["heldAtRefusal"], 1, "…while the newer open's /tunnels was held: %r" % s)
        self.assertTrue(s["bListed"], "the newer open's /tunnels then reached the page: %r" % s)
        self.assertTrue(s["drawn"], "and the older open's waiting drawing drew the rows: %r" % s)
        self.assertEqual(s["spare"], ["", "TESTHOST", "web-box"], "…listing the newer open's kernels: %r" % s)
        self.assertEqual(self._spare(s), [None], "the kernel holds no link: %r" % s)

    def test_after_one_of_two_links_on_their_way_is_refused_the_other_keeps_linking_across_the_kernel_lists_arrival(self):
        s = self._kept("twoInflight")
        self.assertTrue(s["reopenRead"], "…until the page had read it: %r" % s)
        self.assertEqual(s["posts"], [{"link": {"login": self.ids["work"], "sameAs": "api-box"}}, self._link("api-box")],
                         "Link sent work's pick, then spare's, both held: %r" % s)
        self.assertTrue(s["workRefused"], "the kernel refused work's link, and its row shows the refusal: %r" % s)
        self.assertGreaterEqual(s["heldAtWorkRefusal"], 1, "…while the reopen's /tunnels was held: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, then reached the page: %r" % s)
        self.assertEqual((s["afterRelease"]["note"], s["keptAfterRelease"]), ("Linking…", True),
                         "spare's row, its link still on its way, is still drawn and still says Linking…: %r" % s)
        self.assertTrue(s["spareRefused"], "then spare's refusal shows in its row: %r" % s)
        self.assertEqual({x["label"]: x["linked"] for x in s["kernel"]}, {"work": None, "spare": None}, "the kernel holds no link: %r" % s)

    def _failed_re_read(self, key, drawn, why):
        s = self._run()[key]
        self.assertNotIn("error", s, s)
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the pick: %r" % s)
        if "answerHeld" in s:
            self.assertGreaterEqual(s["answerHeld"], 1, "the kernel took the link and its answer was held: %r" % s)
            self.assertEqual(self._spare({"kernel": s["kernelHeld"]}), ["TESTHOST"], "…before the reopen: %r" % s)
        self.assertGreaterEqual(s["heldNow"], 1, "the reopen's /tunnels was held: %r" % s)
        self.assertTrue(s["reopenRead"], "…until the page had read its /logins answer: %r" % s)
        self.assertTrue(s["released"], "its /tunnels answer, web-box, then reached the page: %r" % s)
        self.assertEqual((s["afterRelease"]["note"], s["keptAfterRelease"]), ("Linking…", True),
                         "the reopen drew nothing while the link was on its way: %r" % s)
        self.assertTrue(s["answered"], "the page read the link's ok and its re-read's answer: %r" % s)
        self.assertGreaterEqual(s["reReadFailed"], 1, "…the re-read answered 500: %r" % s)
        self.assertEqual(self._spare(s), ["TESTHOST"], "the kernel holds the link: %r" % s)
        a = s["after"]
        self.assertEqual({k: a[k] for k in drawn}, drawn, why + ": %r" % s)
        self.assertEqual(s["work"], ["", "TESTHOST", "web-box"], "the pickers list the reopen's kernels: %r" % s)
        self.assertFalse(s["keptAfter"], "…in rows drawn again: %r" % s)

    def test_a_failed_re_read_draws_the_skipped_reopen_from_its_answer_read_before_the_link_was_taken(self):
        self._failed_re_read("failedReReadBefore", {"unlink": False, "picker": True, "disabled": False, "value": ""},
                             "spare is drawn as the reopen's answer had it, read before the kernel took the link: unlinked, its "
                             "picker enabled at the placeholder")

    def test_a_failed_re_read_draws_the_skipped_reopen_from_its_answer_read_after_the_link_was_taken(self):
        self._failed_re_read("failedReReadAfter", {"unlink": True}, "spare is drawn as the reopen's answer had it: linked, with Unlink")

    def test_a_refusal_shown_after_a_skipped_drawing_stays_when_another_links_re_read_fails(self):
        s = self._run()["refusalThenFailedReRead"]
        self.assertNotIn("error", s, s)
        self.assertEqual(s["posts"], [{"link": {"login": self.ids["work"], "sameAs": "api-box"}}, self._link("TESTHOST")],
                         "Link sent work's pick, then spare's: %r" % s)
        self.assertGreaterEqual(s["heldNow"], 1, "the reopen's /tunnels was held: %r" % s)
        self.assertTrue(s["reopenRead"], "…until the page had read its /logins answer: %r" % s)
        self.assertGreaterEqual(s["answerHeld"], 1, "the kernel took spare's link and its answer was held: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page: %r" % s)
        self.assertEqual(s["keptAfterRelease"], [True, True], "the reopen drew nothing while the links were on their way: %r" % s)
        self.assertTrue(s["refused"], "the kernel refused work's link, and its row shows the refusal: %r" % s)
        self.assertTrue(s["answered"], "the page read spare's ok and its re-read's answer: %r" % s)
        self.assertGreaterEqual(s["reReadFailed"], 1, "…the re-read answered 500: %r" % s)
        linked = {x["label"]: x["linked"] for x in s["kernel"]}
        self.assertEqual(linked, {"work": None, "spare": "TESTHOST"}, "the kernel holds spare's link alone: %r" % s)
        self.assertEqual((s["work"]["bad"], s["work"]["note"]), (True, s["refusal"]["note"]), "work's row still shows the refusal: %r" % s)
        self.assertEqual(s["keptAfter"], [True, True], "…in the rows the links were pressed on: %r" % s)

    def _removed(self, key):
        s = self._run()[key]
        self.assertNotIn("error", s, s)
        self.assertGreaterEqual(s["heldNow"], 1, "the reopen's /tunnels was held: %r" % s)
        self.assertTrue(s["reopenRead"], "…until the page had read its /logins answer: %r" % s)
        self.assertFalse(s["workAtRemove"], "Remove took work's row away: %r" % s)
        self.assertTrue(s["spareAtRemove"], "…and left spare's drawn: %r" % s)
        self.assertTrue(s["keptAtRemove"], "…as drawn before the Remove: %r" % s)
        self.assertGreaterEqual(s["rmReReadHeld"], 1, "…and the Remove's re-read was held: %r" % s)
        self.assertEqual([x["label"] for x in s["kernelAfterRemove"]], ["spare"], "the kernel took the Remove: %r" % s)
        self.assertTrue(s["released"], "the reopen's /tunnels answer, web-box, reached the page: %r" % s)
        return s

    def _removed_stays_gone(self, s):
        self.assertTrue(s["rmReReadRead"], "the page read the Remove's re-read: %r" % s)
        self.assertGreaterEqual(s["rmReReadFailed"], 1, "…answered 500: %r" % s)
        self.assertFalse(s["workAfterRmReRead"], "work's row is still not drawn: %r" % s)
        self.assertTrue(s["spareAfterRmReRead"], "…and spare's is: %r" % s)

    def test_a_login_removed_while_the_kernel_list_is_read_stays_gone_when_the_list_arrives(self):
        s = self._removed("removeAtRelease")
        self.assertFalse(s["workAfterRelease"], "work's row is not drawn back once /tunnels has answered: %r" % s)
        self.assertTrue(s["spareAfterRelease"], "…and spare's is drawn: %r" % s)
        self.assertFalse(s["keptAfterRelease"], "…in rows drawn again: %r" % s)
        self.assertEqual(s["spareOptionsAfterRelease"], ["", "TESTHOST", "web-box"], "…its picker listing the reopen's kernels: %r" % s)
        self._removed_stays_gone(s)

    def test_a_login_removed_while_a_link_is_on_its_way_stays_gone_when_the_links_re_read_fails(self):
        s = self._removed("removeThenFailedReRead")
        self.assertEqual(s["posts"], [self._link("TESTHOST")], "Link sent the pick: %r" % s)
        self.assertTrue(s["answered"], "the page read the link's ok and its re-read's answer: %r" % s)
        self.assertGreaterEqual(s["reReadFailed"], 1, "…the re-read answered 500: %r" % s)
        self.assertFalse(s["workAfterFailedReRead"], "work's row is not drawn back once the link's re-read has failed: %r" % s)
        self.assertTrue(s["spareAfterFailedReRead"], "…and spare's is drawn: %r" % s)
        self.assertFalse(s["keptAfterFailedReRead"], "…in rows drawn again: %r" % s)
        r = s["spareRowAfterFailedReRead"]
        self.assertNotEqual(r["note"], "Linking…", "…spare's row not saying Linking…: %r" % s)
        self.assertEqual((r["picker"], r["disabled"]), (True, False), "…its picker enabled: %r" % s)
        self.assertEqual(s["spareOptionsAfterFailedReRead"], ["", "TESTHOST", "web-box"], "…and listing the reopen's kernels: %r" % s)
        self._removed_stays_gone(s)

    def test_no_page_errors(self):
        self.assertEqual(self._run()["errors"], [])


if __name__ == "__main__":
    unittest.main()
