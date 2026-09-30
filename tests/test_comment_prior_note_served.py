#!/usr/bin/env python3
"""A comment on a passage from BEFORE a session's last context compaction, executed on the real /chat page (the user
2026-09-30, who wanted a thread there instead of the refusal, and wanted to see what romp sent on their behalf).

The kernel's own create door makes the threads: a small script run with the lab's environment loads the kernel and
calls _comment_create on the lab's synthetic transcript, once on a passage before the compaction boundary and once on
one after it, with a stand-in for the SDK backend's fork (a hermetic kernel has no SDK to fork with; the stand-in
records the cut and the opening message and writes the registry row, and the script then writes each thread's
transcript as the CLI would: the copied history, the opening message, a reply). A hermetic kernel then serves the
page, and the driver walks:

  the pre-compaction passage renders in the chat and wears its thread's highlight (the mark lands on the passage's own
  turn); its thread opens with the opening bubble showing the comment alone and, inside that turn, ONE note whose one
  line says the thread was also sent the turns around the passage and where the conversation is saved, COLLAPSED;
  a click on the note shows the words exactly as sent (the row's priorContext, byte for byte); a reload brings the
  note back (from the row, not the page's memory); the thread on the post-compaction passage has no note.

Asserted on the kernel's side too: the pre-compaction thread is a tip fork stamped with its passage's own time and
its opening message carries the stored block between the quote and the comment; the other is a cut fork with no
block. With COMMENT_SHOTS=<dir> the driver writes the popover collapsed and expanded, in the dark theme and the light
one. Skips LOUDLY without the extension deps or a Playwright browser. SYNTHETIC fixtures only (the notes-api demo
world: session web, host TESTHOST, placeholder UUIDs, invented prose)."""
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
PRE_ANCHOR, ORD_ANCHOR = "pa2", "pa4"
PRE_EXACT = "Use exponential backoff with a jitter of ten percent"
ORD_EXACT = "The dashboard reads the retry table directly"
PRE_COMMENT = "Why ten percent and not twenty?"
ORD_COMMENT = "Does it refresh on its own?"
W, H = 1200, 900
GIST = re.search(r'export const CMT_PRIOR_GIST =\s*"([^"]+)";',
                 Path(ROOT, "ui", "webview", "comments.ts").read_text()).group(1)


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
out = {}
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
// the highlights are a client-side join of the chat frame (the anchor turns) and the comments frame (the threads);
// gate on the comments frame carrying both threads, the kernel's event, never a wall clock (the promoted-popup lab)
await page.addInitScript(({ sid, tids }) => {
  window.__cmtFrames = 0;
  window.addEventListener("message", (e) => {
    const m = e.data; if (!m || !m.type) return;
    if (m.type === "comments" && m.id === sid && tids.every((t) => (m.threads || []).some((x) => x && x.tid === t))) window.__cmtFrames++;
  }, true);
}, { sid: cfg.web, tids: [cfg.pre, cfg.ord] });

async function landed() {
  await page.waitForSelector(`#content .turn[data-uuid="${cfg.preAnchor}"]`, { state: "attached", timeout: 60000 });
  const n0 = await page.evaluate(() => window.__cmtFrames);
  const marks = async () => (await page.$(`mark.cmt-hl[data-tid="${cfg.pre}"]`)) && (await page.$(`mark.cmt-hl[data-tid="${cfg.ord}"]`));
  if (!(await marks())) await page.waitForFunction((n) => window.__cmtFrames > n, n0, { timeout: 60000 }).catch(() => {});
  for (const tid of [cfg.pre, cfg.ord]) await page.waitForSelector(`mark.cmt-hl[data-tid="${tid}"]`, { state: "attached", timeout: 30000 });
}
async function open(tid) {
  if (!(await page.$(`#cmt-pop[data-mode="thread"][data-tid="${tid}"]`))) {
    await page.locator(`mark.cmt-hl[data-tid="${tid}"]`).first().click();
    await page.waitForSelector(`#cmt-pop[data-mode="thread"][data-tid="${tid}"]`, { timeout: 15000 });
  }
  await page.waitForSelector("#cmt-pop .cmt-msgs .turn-user", { timeout: 60000 });   // the chat-parity render
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
}
const state = () => page.evaluate(() => {
  const pop = document.getElementById("cmt-pop");
  const first = pop.querySelector(".cmt-msgs .turn-user");
  const bubble = first && first.querySelector(".user-bubble");
  const note = pop.querySelector(".cmt-prior");
  const body = note && note.querySelector(".notice-body");
  const vis = (n) => !!n && getComputedStyle(n).display !== "none" && n.getBoundingClientRect().height > 0;
  return {
    tid: pop.dataset.tid,
    bubble: bubble ? bubble.innerText.trim() : null,
    notes: pop.querySelectorAll(".cmt-prior").length,
    noteInOpener: !!(first && note && first.contains(note)),
    noteAfterBubble: !!(bubble && note && (bubble.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING)),
    gist: note ? note.querySelector(".notice-gist").textContent : null,
    gistLines: note ? (() => { const g = note.querySelector(".notice-gist"); const rg = document.createRange(); rg.selectNodeContents(g);
                               return new Set(Array.from(rg.getClientRects()).map((x) => Math.round(x.top))).size; })() : 0,
    firstItem: (() => { const f = pop.querySelector(".cmt-msgs > *"); return f ? String(f.className) : null; })(),
    open: note ? note.classList.contains("notice-open") : null,
    bodyShown: vis(body),
    bodyText: body ? body.querySelector(".cmt-prior-text").textContent : null,
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
await open(cfg.pre);
await page.waitForSelector("#cmt-pop .cmt-prior", { timeout: 30000 });
out.collapsed = await state();
await shot("precompact-note-dark-collapsed");
await page.click("#cmt-pop .cmt-prior .notice-head");
await page.waitForSelector("#cmt-pop .cmt-prior.notice-open", { timeout: 5000 });
out.expanded = await state();
await shot("precompact-note-dark-expanded");
await page.evaluate(() => document.body.classList.add("theme-light"));
await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
await shot("precompact-note-light-expanded");
await page.click("#cmt-pop .cmt-prior .notice-head");
await page.waitForFunction(() => !document.querySelector("#cmt-pop .cmt-prior.notice-open"), null, { timeout: 5000 });
out.recollapsed = await state();
await shot("precompact-note-light-collapsed");
await page.evaluate(() => document.body.classList.remove("theme-light"));
// a reload: the note comes back from the thread row the kernel ships, never from this page's memory
await page.reload();
await landed();
await open(cfg.pre);
await page.waitForSelector("#cmt-pop .cmt-prior", { timeout: 30000 });
out.afterReload = await state();
await page.click("#cmt-pop .cmt-x");
await page.waitForSelector("#cmt-pop", { state: "detached", timeout: 5000 });
await open(cfg.ord);
out.ordinary = await state();
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedPreCompactionCommentNote(unittest.TestCase):
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
        cls.lab = tempfile.mkdtemp(prefix="cmt-prior-")
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
        cls.port, cls.token = _free_port(), "testtok-cmtprior"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        # the two comments, through the kernel's own create door (CREATE), before the lab kernel reads the store
        ccfg = os.path.join(cls.lab, "create.json")
        Path(ccfg).write_text(json.dumps({"web": WEB, "cwd": cwd, "parent_path": cls.parent_path, "comments": [
            ["pre", PRE_ANCHOR, PRE_EXACT, PRE_COMMENT], ["ord", ORD_ANCHOR, ORD_EXACT, ORD_COMMENT]]}))
        script = os.path.join(cls.lab, "create.py")
        Path(script).write_text(CREATE)
        c = subprocess.run([sys.executable, script, ccfg], capture_output=True, text=True, timeout=300,
                           env=dict(env, TESTS_DIR=HERE, BIN_DIR=BIN, ROMP_KERNEL_NO_OPEN="1"))
        line = next((ln for ln in c.stdout.splitlines() if ln.startswith("CREATED:")), None)
        if line is None:
            raise AssertionError("the create script printed no result:\n" + c.stdout[-3000:] + c.stderr[-3000:])
        cls.created = json.loads(line[len("CREATED:"):])
        store = json.loads(Path(state, "comments", WEB + ".json").read_text())
        cls.rows = {th["tid"]: th for th in store.get("threads") or []}
        # each thread's transcript as the CLI leaves it: the copied history (all of it for the tip fork, up to the cut
        # for the cut fork), then the opening message the create sent and a reply
        for key, reply in (("pre", "Ten percent spreads the retries without stretching any one wait much."),
                           ("ord", "It refreshes on every poll, so within thirty seconds.")):
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
        pre, ordn = self.created["pre"], self.created["ord"]
        self.assertIsNone(pre["err"], "no refusal for a passage from before the compaction: %r" % pre)
        self.assertEqual(pre["cut"], "", "a tip fork: the CLI cannot cut before the boundary")
        row = self.rows[pre["tid"]]
        self.assertEqual((row["anchorUuid"], row["cutUuid"]), (PRE_ANCHOR, ""))
        self.assertEqual(row["anchorT"], self.t0 + 80, "stamped with the passage's own time")
        prior = row["priorContext"]
        self.assertIn("Just before it, I said:\n\n> How should it back off when the server is down?", prior)
        self.assertIn("Just after it, I said:\n\n> and the cap on the delay?", prior)
        self.assertIn(os.path.abspath(self.parent_path) + ", and this passage is the entry with uuid " + PRE_ANCHOR + ".", prior)
        self.assertIn("\n\n> " + PRE_EXACT + "\n\n" + prior + "\n\n" + PRE_COMMENT, pre["opener"],
                      "the opener carries the stored block, verbatim, between the quote and the comment")
        # the post-compaction passage is the ordinary thread it always was
        self.assertIsNone(ordn["err"])
        self.assertEqual(ordn["cut"], ORD_ANCHOR)
        self.assertNotIn("priorContext", self.rows[ordn["tid"]])

    def test_the_popover_shows_a_collapsed_note_that_opens_on_the_words_as_sent(self):
        r = self._drive()
        self.assertEqual(r["preMark"], {"text": PRE_EXACT, "turn": PRE_ANCHOR},
                         "the highlight lands on the pre-compaction passage's own turn")
        c = r["collapsed"]
        self.assertEqual(c["bubble"], PRE_COMMENT, "the opening bubble is the comment alone: %r" % c["bubble"])
        self.assertEqual(c["notes"], 1, c)
        self.assertTrue(c["noteInOpener"] and c["noteAfterBubble"], "the note sits under the opening message: %r" % c)
        self.assertEqual(c["gist"], GIST)
        self.assertEqual(c["gistLines"], 1, "one line at the popover's default width")
        self.assertIn("turn-user", c["firstItem"], "the thread opens on its opening message, no copied head card above it")
        self.assertNotIn("System context", c["shownText"])
        self.assertFalse(c["open"], "collapsed by default")
        self.assertFalse(c["bodyShown"], "the words are one click deeper")
        self.assertNotIn("before our conversation was summarized", c["shownText"], "nothing of the block shows collapsed")
        self.assertNotIn("About this part of the conversation", c["shownText"])
        self.assertNotIn("I've split this off", c["shownText"])
        e = r["expanded"]
        self.assertTrue(e["open"] and e["bodyShown"], e)
        self.assertEqual(e["bodyText"], self.rows[self.created["pre"]["tid"]]["priorContext"],
                         "the words exactly as sent, from the row")
        self.assertFalse(r["recollapsed"]["open"], "and it folds again")

    def test_a_reload_brings_the_note_back_and_an_ordinary_thread_has_none(self):
        r = self._drive()
        a = r["afterReload"]
        self.assertEqual((a["notes"], a["gist"]), (1, GIST), "from the row, not the page's memory: %r" % a)
        o = r["ordinary"]
        self.assertEqual(o["notes"], 0, "a thread on a post-compaction passage carries no note: %r" % o)
        self.assertEqual(o["bubble"], ORD_COMMENT)


if __name__ == "__main__":
    unittest.main()
