#!/usr/bin/env python3
"""What romp sent with a comment, and a comment on a passage from BEFORE a session's last context compaction, executed
on the real /chat page (the user 2026-09-30: a thread on such a passage instead of the refusal, and one rule for every
thread, that they can always see exactly what romp sent on their behalf).

The kernel's own create door makes the threads: a small script run with the lab's environment loads the kernel and
calls _comment_create on the lab's synthetic transcript, on a passage before the compaction boundary and on two after
it, with a stand-in for the SDK backend's fork (a hermetic kernel has no SDK to fork with; the stand-in records the cut
and the opening message and writes the registry row, and the script then writes each thread's transcript as the CLI
would: the copied history, the opening message, a reply). One of the two ordinary threads then loses its row's copy of
what was sent, which is how a thread created before this change looks. A hermetic kernel serves the page, and the
driver walks:

  the pre-compaction passage renders in the chat and wears its thread's highlight (the mark lands on the passage's own
  turn); every thread opens with the opening bubble showing the comment alone and, inside that turn, ONE note,
  COLLAPSED, whose line is the user's (the longer one, naming the turns around the passage, on the pre-compaction
  thread); a click on the note shows the words exactly as sent (the row's copy byte for byte; for the older thread,
  the split-off line and the quote read back from its stored opening message); a reload brings the notes back.

Asserted on the kernel's side too: the pre-compaction thread is a tip fork stamped with its passage's own time and its
opening message is the row's copy followed by the comment, labelled as the ask; the others are cut forks whose opening
message is the approved ordinary one. With COMMENT_SHOTS=<dir> the driver writes the popover of the ordinary thread and
of the pre-compaction thread, collapsed and expanded, in the dark theme and the Yatharth light one. Skips LOUDLY without
the extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo world: session web, host
TESTHOST, placeholder UUIDs, invented prose)."""
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
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

WEB = "aaaaaaaa-1111-2222-3333-444444444444"
PRE_ANCHOR, ORD_ANCHOR, OLD_ANCHOR = "pa2", "pa4", "pa5"
PRE_EXACT = "Use exponential backoff with a jitter of ten percent"
ORD_EXACT = "The dashboard reads the retry table directly"
OLD_EXACT = "Each endpoint's row keeps its last error text"
PRE_COMMENT = "Why ten percent and not twenty?"
ORD_COMMENT = "Does it refresh on its own?"
OLD_COMMENT = "Is the error text ever cleared?"
W, H = 1200, 900
_COMMENTS_TS = Path(ROOT, "ui", "webview", "comments.ts").read_text()
GIST = re.search(r'export const CMT_SENT_GIST = "([^"]+)";', _COMMENTS_TS).group(1)
GIST_PRIOR = re.search(r'export const CMT_SENT_GIST_PRIOR = "([^"]+)";', _COMMENTS_TS).group(1)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def user(t, uuid, parent, text, sid=WEB):
    return {"type": "user", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "promptSource": "typed",
            "sessionId": sid, "message": {"role": "user", "content": text}}


def agent(t, uuid, parent, text, sid=WEB):
    return {"type": "assistant", "timestamp": iso(t), "uuid": uuid, "parentUuid": parent, "sessionId": sid,
            "message": {"role": "assistant", "model": "claude-opus-5", "stop_reason": "end_turn",
                        "content": [{"type": "text", "text": text}]}}


def parent_records(t0):
    """The web session, compacted once: three closed turns before the boundary (the passage is the second answer),
    the CLI's boundary and summary, then a turn after it (the ordinary thread's passage)."""
    return [
        user(t0, "p1", None, "Set up the notes-api sync loop."),
        agent(t0 + 20, "pa1", "p1", "Done: the loop polls the server every thirty seconds and writes one row per endpoint."),
        user(t0 + 60, "p2", "pa1", "How should it back off when the server is down?"),
        agent(t0 + 80, PRE_ANCHOR, "p2", PRE_EXACT + ", so the clients never retry in lockstep."),
        user(t0 + 120, "p3", PRE_ANCHOR, "and the cap on the delay?"),
        agent(t0 + 140, "pa3", "p3", "Cap the delay at two minutes, so a recovered server is polled again within one cycle."),
        {"type": "system", "subtype": "compact_boundary", "timestamp": iso(t0 + 600), "uuid": "pb1", "parentUuid": None,
         "logicalParentUuid": "pa3", "sessionId": WEB, "content": "Conversation compacted",
         "compactMetadata": {"trigger": "manual", "preTokens": 51234}},
        dict(user(t0 + 601, "ps1", "pb1", "This session is being continued from a previous conversation. Summary: the "
                                          "sync loop backs off with jitter and caps the delay at two minutes."),
             isCompactSummary=True, isVisibleInTranscriptOnly=True),
        user(t0 + 700, "p4", "ps1", "now wire the dashboard"),
        agent(t0 + 720, ORD_ANCHOR, "p4", ORD_EXACT + ", one row per endpoint with its next allowed time."),
        user(t0 + 760, "p5", ORD_ANCHOR, "and when a request fails?"),
        agent(t0 + 780, OLD_ANCHOR, "p5", OLD_EXACT + ", so the operator sees why it is backing off."),
    ]


# Run with the lab's environment BEFORE the lab kernel starts: the kernel's own create door, with a stand-in for the
# SDK backend's fork. Prints CREATED:<json> — per comment, the create's verdict, the cut the fork was asked for and the
# opening message the thread was sent.
CREATE = r'''
import json, os, sys
sys.path.insert(0, os.environ["TESTS_DIR"])
from romp_load import load_source
BIN = os.environ["BIN_DIR"]
load_source("romp_event_model", os.path.join(BIN, "romp-event-model"))
load_source("romp_judge", os.path.join(BIN, "romp-judge"))
km = load_source("romp_kernel_priornote", os.path.join(BIN, "romp-kernel"))
cfg = json.load(open(sys.argv[1]))
FORKS, SENT = {}, {}


class ForkStandIn:
    """What SdkBackend.fork leaves for a thread before its CLI runs, minus the CLI: the registry row (threadOf the
    parent, the fork already landed so no forkOf). The transcript is written by the caller once the opener is known."""
    def fork(self, name, parent_sid, cut_uuid="", bg="", fg="", sid=None, thread_of="", model="", effort="", fast=""):
        FORKS[sid] = cut_uuid
        (km.jd.STATE / "sdk" / (sid + ".json")).write_text(json.dumps(
            {"sid": sid, "name": name, "cwd": cfg["cwd"], "lastSid": sid, "threadOf": thread_of, "alive": False,
             "mode": "auto", "effort": "high", "model": "claude-opus-5", "liveModel": "Opus 5"}))
        return sid

    def connect(self, sid):
        return True

    def send(self, sid, text):
        SENT[sid] = text
        return True

    def kill(self, sid):
        return True


be = ForkStandIn()
km.Sessions.backend_for = staticmethod(lambda sid: be)
km._sdk_ready = lambda: True
km._live_map = lambda: {}
km._push_soon = lambda: None
km._sessions = lambda now, window=None, forks=True: [
    {"sid": cfg["web"], "name": "web", "anchor": cfg["web"], "path": cfg["parent_path"], "mtime": now}]
out = {"threadFrame": km._THREAD_FRAME, "quoteHead": km._COMMENT_FRAME_HEAD, "askHead": km._COMMENT_ASK_HEAD}
for key, anchor, exact, comment in cfg["comments"]:
    err, tid = km._comment_create(cfg["web"], anchor, exact, comment, color="#e8b220")
    out[key] = {"err": err, "tid": tid, "cut": FORKS.get(tid), "opener": SENT.get(tid)}
print("CREATED:" + json.dumps(out))
'''

DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const page = await browser.newPage({ viewport: { width: cfg.W, height: cfg.H } });
const TIDS = [cfg.pre, cfg.ord, cfg.old];
// the highlights are a client-side join of the chat frame (the anchor turns) and the comments frame (the threads);
// gate on the comments frame carrying every thread, the kernel's event, never a wall clock (the promoted-popup lab)
await page.addInitScript(({ sid, tids }) => {
  window.__cmtFrames = 0;
  window.addEventListener("message", (e) => {
    const m = e.data; if (!m || !m.type) return;
    if (m.type === "comments" && m.id === sid && tids.every((t) => (m.threads || []).some((x) => x && x.tid === t))) window.__cmtFrames++;
  }, true);
}, { sid: cfg.web, tids: TIDS });

async function landed() {
  // the chat opens on the conversation since the compaction; the passage before it is older history the page loads when
  // the reader scrolls back to it, which is how the user reaches it to comment: scroll back until its turn is on the page
  const sel = `#content .turn[data-uuid="${cfg.preAnchor}"]`;
  await page.waitForSelector("#content .turn[data-uuid]", { state: "attached", timeout: 60000 });
  for (let i = 0; i < 12 && !(await page.$(sel)); i++) {   // loop-ok: bounded, each pass one scroll-back and the page's answer
    await page.mouse.move(cfg.W / 2, cfg.H / 3);
    await page.mouse.wheel(0, -4000);
    await page.waitForSelector(sel, { state: "attached", timeout: 5000 }).catch(() => {});
  }
  try { await page.waitForSelector(sel, { state: "attached", timeout: 30000 }); }
  catch (e) {   // name what the page held instead, so a miss says whether the passage's turn was windowed out or never came
    const held = await page.evaluate(() => Array.from(document.querySelectorAll("#content .turn")).map((t) => t.dataset.uuid || t.className));
    console.error("pre-compaction turn missing; the page held: " + JSON.stringify(held));
    throw e;
  }
  const n0 = await page.evaluate(() => window.__cmtFrames);
  const marks = async () => { for (const t of TIDS) if (!(await page.$(`mark.cmt-hl[data-tid="${t}"]`))) return false; return true; };
  if (!(await marks())) await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 60000 }).catch(() => {});
  for (const tid of TIDS) await page.waitForSelector(`mark.cmt-hl[data-tid="${tid}"]`, { state: "attached", timeout: 30000 });
}
async function open(tid) {
  if (await page.$("#cmt-pop") && !(await page.$(`#cmt-pop[data-tid="${tid}"]`))) {
    await page.click("#cmt-pop .cmt-x");
    await page.waitForSelector("#cmt-pop", { state: "detached", timeout: 5000 });
  }
  if (!(await page.$(`#cmt-pop[data-mode="thread"][data-tid="${tid}"]`))) {
    await page.locator(`mark.cmt-hl[data-tid="${tid}"]`).first().click();
    await page.waitForSelector(`#cmt-pop[data-mode="thread"][data-tid="${tid}"]`, { timeout: 15000 });
  }
  await page.waitForSelector("#cmt-pop .cmt-msgs .turn-user", { timeout: 60000 });   // the chat-parity render
  await page.waitForSelector("#cmt-pop .cmt-sent", { timeout: 30000 });
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
}
const state = () => page.evaluate(() => {
  const pop = document.getElementById("cmt-pop");
  const first = pop.querySelector(".cmt-msgs .turn-user");
  const bubble = first && first.querySelector(".user-bubble");
  const note = pop.querySelector(".cmt-sent");
  const body = note && note.querySelector(".notice-body");
  const vis = (n) => !!n && getComputedStyle(n).display !== "none" && n.getBoundingClientRect().height > 0;
  const g = note && note.querySelector(".notice-gist");
  const rg = document.createRange(); if (g) rg.selectNodeContents(g);
  return {
    tid: pop.dataset.tid,
    bubble: bubble ? bubble.innerText.trim() : null,
    notes: pop.querySelectorAll(".cmt-sent").length,
    noteInOpener: !!(first && note && first.contains(note)),
    noteAfterBubble: !!(bubble && note && (bubble.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING)),
    gist: g ? g.textContent : null,
    gistLines: g ? new Set(Array.from(rg.getClientRects()).map((x) => Math.round(x.top))).size : 0,
    firstItem: (() => { const f = pop.querySelector(".cmt-msgs > *"); return f ? String(f.className) : null; })(),
    open: note ? note.classList.contains("notice-open") : null,
    bodyShown: vis(body),
    bodyText: body ? body.querySelector(".cmt-sent-text").textContent : null,
    shownText: pop.innerText,
  };
});
// the review shots: the popover from its title through the reply under the opening message (the lab kernel has no SDK,
// so its own "backend isn't installed" error card follows below; that is this lab's, never the feature's)
const shot = async (name) => {
  if (!cfg.shots) return;
  const clip = await page.evaluate(() => {
    const pop = document.getElementById("cmt-pop").getBoundingClientRect();
    const opener = document.querySelector("#cmt-pop .cmt-msgs .turn-user");
    let end = opener;
    for (let n = opener && opener.nextElementSibling; n; n = n.nextElementSibling) { end = n; if (n.matches(".turn") && !n.matches(".turn-user")) break; }
    const bottom = end ? end.getBoundingClientRect().bottom : pop.bottom;
    return { x: pop.left, y: pop.top, width: pop.width, height: Math.min(pop.height, bottom - pop.top + 10) };
  });
  await page.screenshot({ path: cfg.shots + "/" + name + ".png", clip });
};
const toggle = async (want) => {
  await page.click("#cmt-pop .cmt-sent .notice-head");
  if (want) await page.waitForSelector("#cmt-pop .cmt-sent.notice-open", { timeout: 5000 });
  else await page.waitForFunction(() => !document.querySelector("#cmt-pop .cmt-sent.notice-open"), null, { timeout: 5000 });
  await page.mouse.move(2, 2);   // off the head, so its hover underline is not in the shot
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
};
const theme = async (light) => {
  await page.evaluate((on) => {   // what theme.ts applies for the "yatharth-light" setting, and the dark default's bare body
    document.body.classList.toggle("theme-light", on); document.body.classList.toggle("chat-theme-yatharth", on);
  }, light);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
};

const out = {};
await page.goto(cfg.chat);
await page.waitForSelector("#tabs .tab[data-id]", { timeout: 20000 });
await page.locator(`#tabs .tab[data-id="${cfg.web}"]`).first().click();
await landed();
out.preMark = await page.evaluate((tid) => {
  const m = document.querySelector(`mark.cmt-hl[data-tid="${tid}"]`);
  const turn = m && m.closest(".turn");
  return { text: m ? m.textContent : null, turn: turn ? turn.dataset.uuid : null };
}, cfg.pre);
// the shots are taken with the box maximized (its own button), so the pre-summary note fits whole when it is open
let maxed = false;   // the box's own toggle; its state is its size, so the driver keeps what it asked for
const maximize = async (on) => {
  if (!cfg.shots || maxed === on) return;
  maxed = on;
  const before = await page.$eval("#cmt-pop", (p) => p.offsetHeight);
  await page.click("#cmt-pop .cmt-max");
  await page.waitForFunction((h) => document.getElementById("cmt-pop").offsetHeight !== h, before, { timeout: 5000 }).catch(() => {});
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
};
for (const [key, tid] of [["pre", cfg.pre], ["ord", cfg.ord], ["old", cfg.old]]) {
  await open(tid);
  out[key] = { collapsed: await state() };
  if (key !== "old") { await maximize(true); await shot(`note-${key === "pre" ? "pre-summary" : "ordinary"}-dark-collapsed`); }
  await toggle(true);
  out[key].expanded = await state();
  if (key !== "old") {
    const name = key === "pre" ? "pre-summary" : "ordinary";
    await shot(`note-${name}-dark-expanded`);
    await theme(true);
    await shot(`note-${name}-yatharth-light-expanded`);
    await toggle(false);
    await shot(`note-${name}-yatharth-light-collapsed`);
    await theme(false);
    await maximize(false);
  } else await toggle(false);
  out[key].recollapsed = await state();
}
// a reload: the notes come back from what the kernel ships (the row, or the stored opening message), never this page's memory
await page.reload();
await landed();
out.afterReload = {};
for (const [key, tid] of [["pre", cfg.pre], ["old", cfg.old]]) { await open(tid); out.afterReload[key] = await state(); }
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedCommentSentNote(unittest.TestCase):
    maxDiff = None

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
        cls.lab = tempfile.mkdtemp(prefix="cmt-sent-")
        b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
        if b.returncode != 0:
            raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(EXT, "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "proj")
        for d in ("names", "sdk", "states", "comments"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        # this lab mints its own state root, outside the runner's floor: per-session hosts off (repo CLAUDE.md)
        Path(state, "session-hosts").write_text("off\n")
        Path(state, "names", WEB).write_text("web\t%s\t#9cd2ff\t#0c1a2e\n" % cwd)
        Path(state, "sdk", WEB + ".json").write_text(json.dumps(
            {"sid": WEB, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": WEB, "alive": True,
             "model": "claude-opus-5", "liveModel": "Opus 5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        t0 = int(time.time()) - 3600
        cls.recs = parent_records(t0)
        cls.t0 = t0
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        cls.parent_path = os.path.join(proj, WEB + ".jsonl")
        Path(cls.parent_path).write_text("".join(json.dumps(r) + "\n" for r in cls.recs))
        cls.port, cls.token = _free_port(), "testtok-cmtsent"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        # the two comments, through the kernel's own create door (CREATE), before the lab kernel reads the store
        ccfg = os.path.join(cls.lab, "create.json")
        Path(ccfg).write_text(json.dumps({"web": WEB, "cwd": cwd, "parent_path": cls.parent_path, "comments": [
            ["pre", PRE_ANCHOR, PRE_EXACT, PRE_COMMENT], ["ord", ORD_ANCHOR, ORD_EXACT, ORD_COMMENT],
            ["old", OLD_ANCHOR, OLD_EXACT, OLD_COMMENT]]}))
        script = os.path.join(cls.lab, "create.py")
        Path(script).write_text(CREATE)
        c = subprocess.run([sys.executable, script, ccfg], capture_output=True, text=True, timeout=300,
                           env=dict(env, TESTS_DIR=HERE, BIN_DIR=BIN, ROMP_KERNEL_NO_OPEN="1"))
        line = next((ln for ln in c.stdout.splitlines() if ln.startswith("CREATED:")), None)
        if line is None:
            raise AssertionError("the create script printed no result:\n" + c.stdout[-3000:] + c.stderr[-3000:])
        cls.created = json.loads(line[len("CREATED:"):])
        cls.store = Path(state, "comments", WEB + ".json")
        store = json.loads(cls.store.read_text())
        # the older thread: its row as a create from before this change wrote it, with no copy of what was sent
        for th in store.get("threads") or []:
            if th.get("tid") == (cls.created.get("old") or {}).get("tid"):
                cls.old_row_sent = th.pop("sent", None)
        cls.store.write_text(json.dumps(store))
        cls.rows = {th["tid"]: th for th in store.get("threads") or []}
        # each thread's transcript as the CLI leaves it: the copied history (all of it for the tip fork, up to the cut
        # for the cut fork), then the opening message the create sent and a reply
        for key, reply in (("pre", "Ten percent spreads the retries without stretching any one wait much."),
                           ("ord", "It refreshes on every poll, so within thirty seconds."),
                           ("old", "Only when the endpoint answers again.")):
            cr = cls.created.get(key) or {}
            tid, cut = cr.get("tid"), cr.get("cut")
            if not tid:
                continue
            row = cls.rows.get(tid) or {}
            upto = len(cls.recs) if not cut else next(i for i, r in enumerate(cls.recs) if r["uuid"] == cut) + 1
            hist = [dict(r, sessionId=tid) for r in cls.recs[:upto]]
            last = hist[-1]["uuid"]
            tc = int(row.get("createdT") or time.time())
            hist += [user(tc + 1, tid[:8] + "-u1", last, cr["opener"], sid=tid),
                     agent(tc + 6, tid[:8] + "-a1", tid[:8] + "-u1", reply, sid=tid)]
            Path(proj, tid + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in hist))
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(BIN, "romp-kernel")], stdout=open(cls.klog, "w"),
                                      stderr=subprocess.STDOUT, env=env)
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
            k.kill(); k.wait()
        shutil.rmtree(getattr(cls, "lab", ""), ignore_errors=True)

    def _drive(self):
        if getattr(type(self), "_result", None) is None:
            type(self)._result = self._run()
        return type(self)._result

    def _run(self):
        cfg = os.path.join(self.lab, "cfg.json")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "W": W, "H": H,
                       "web": WEB, "pre": self.created["pre"]["tid"], "ord": self.created["ord"]["tid"],
                       "old": self.created["old"]["tid"],
                       "preAnchor": PRE_ANCHOR, "shots": os.environ.get("COMMENT_SHOTS", "")}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.run(["node", driver], capture_output=True, text=True, timeout=420,
                           env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one (CI installs none)")
        klog = open(self.klog).read()[-2000:]
        self.assertEqual(p.returncode, 0, "driver failed:\n" + p.stdout[-4000:] + p.stderr[-4000:] + "\nkernel:\n" + klog)
        line = next((ln for ln in p.stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + p.stdout[-4000:])
        return json.loads(line[len("RESULT:"):])

    def test_the_create_door_takes_a_pre_compaction_passage_as_a_tip_fork_with_its_surroundings(self):
        c = self.created
        pre, ordn, old = c["pre"], c["ord"], c["old"]
        frame, head, ask = c["threadFrame"], c["quoteHead"], c["askHead"]
        self.assertIsNone(pre["err"], "no refusal for a passage from before the compaction: %r" % pre)
        self.assertEqual(pre["cut"], "", "a tip fork: the CLI cannot cut before the boundary")
        row = self.rows[pre["tid"]]
        self.assertEqual((row["anchorUuid"], row["cutUuid"], row.get("beforeSummary")), (PRE_ANCHOR, "", True))
        self.assertEqual(row["anchorT"], self.t0 + 80, "stamped with the passage's own time")
        sent = row["sent"]
        self.assertTrue(sent.startswith(frame + "\n\n" + head + "\n\n> " + PRE_EXACT + "\n\n"), sent[:300])
        self.assertIn("Right before it, I said:\n\n> How should it back off when the server is down?", sent)
        self.assertIn("Right after it, I said:\n\n> and the cap on the delay?", sent)
        self.assertIn("The full conversation is saved in " + os.path.abspath(self.parent_path)
                      + " (this passage is the entry with uuid " + PRE_ANCHOR + ") if you need more.\n\n" + ask, sent)
        self.assertEqual(pre["opener"], sent + "\n\n" + PRE_COMMENT, "the opener is the row's copy, then the comment")
        # the post-compaction passages: ordinary cut forks, the approved opening message, no ask label
        for cr, anchor, exact, comment in ((ordn, ORD_ANCHOR, ORD_EXACT, ORD_COMMENT), (old, OLD_ANCHOR, OLD_EXACT, OLD_COMMENT)):
            self.assertIsNone(cr["err"])
            self.assertEqual(cr["cut"], anchor)
            self.assertEqual(cr["opener"], frame + "\n\n" + head + "\n\n> " + exact + "\n\n" + comment)
        self.assertEqual(self.rows[ordn["tid"]]["sent"], frame + "\n\n" + head + "\n\n> " + ORD_EXACT)
        self.assertNotIn("sent", self.rows[old["tid"]], "the older thread's row keeps no copy, as before this change")

    def test_every_thread_shows_a_collapsed_note_that_opens_on_the_words_as_sent(self):
        r = self._drive()
        c = self.created
        frame, head = c["threadFrame"], c["quoteHead"]
        self.assertEqual(r["preMark"], {"text": PRE_EXACT, "turn": PRE_ANCHOR},
                         "the highlight lands on the pre-compaction passage's own turn")
        expect = {"pre": (PRE_COMMENT, GIST_PRIOR, self.rows[c["pre"]["tid"]]["sent"]),
                  "ord": (ORD_COMMENT, GIST, frame + "\n\n" + head + "\n\n> " + ORD_EXACT),
                  "old": (OLD_COMMENT, GIST, frame + "\n\n" + head + "\n\n> " + OLD_EXACT)}   # read back from the opener
        for key, (comment, gist, words) in expect.items():
            k = r[key]["collapsed"]
            with self.subTest(thread=key):
                self.assertEqual(k["bubble"], comment, "the opening bubble is the comment alone: %r" % k["bubble"])
                self.assertEqual(k["notes"], 1, k)
                self.assertTrue(k["noteInOpener"] and k["noteAfterBubble"], "the note sits under the opening message: %r" % k)
                self.assertEqual(k["gist"], gist)
                self.assertEqual(k["gistLines"], 1, "one line at the popover's default width")
                self.assertIn("turn-user", k["firstItem"], "the thread opens on its opening message, no copied head card above it")
                self.assertNotIn("System context", k["shownText"])
                self.assertFalse(k["open"], "collapsed by default")
                self.assertFalse(k["bodyShown"], "the words are one click deeper")
                for piece in ("I've split this off", "About this part of the conversation", "before our conversation was summarized",
                              "What I'm asking"):
                    self.assertNotIn(piece, k["shownText"], "nothing romp added shows while the note is collapsed")
                e = r[key]["expanded"]
                self.assertTrue(e["open"] and e["bodyShown"], e)
                self.assertEqual(e["bodyText"], words, "the words exactly as sent")
                self.assertFalse(r[key]["recollapsed"]["open"], "and it folds again")

    def test_a_reload_brings_the_notes_back(self):
        r = self._drive()
        a = r["afterReload"]
        self.assertEqual((a["pre"]["notes"], a["pre"]["gist"]), (1, GIST_PRIOR), "from the row, not the page's memory: %r" % a)
        self.assertEqual((a["old"]["notes"], a["old"]["gist"]), (1, GIST), "from the stored opening message: %r" % a)


if __name__ == "__main__":
    unittest.main()
