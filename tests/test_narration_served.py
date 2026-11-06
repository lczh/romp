#!/usr/bin/env python3
"""Progress notes read as the agent's prose on the real /chat page of a hermetic kernel (2026-11-06).

On Claude Opus 5.5 a note the model writes between tool calls comes back as a `thinking` block, and the chat drew it as a
collapsed Thought row, so a reply to a message sent mid-turn never reached the person. The CLI lists those blocks on the
frames it streams; the kernel files the list (kernel/narration.py) and the chat build draws a listed block with words as an
assistant message flagged `narration`. Here, on the served page, session `web`, under look A (plain prose):

- a progress note before the first tool and the note answering the mid-turn message are prose (`.turn-assistant`, the
  `turn-narration` hook), in order, between the tools, in compact mode and in full mode;
- the reasoning summary of the same turn stays a Thought row (compact) or the clamped thinking row (full);
- an older turn's note with no mark (history from before the store) stays a Thought row;
- LIVE: the turn's second half lands in the transcript while the page is open; the note answering the mid-turn message
  arrives as prose through the push, without a reload; after a reload the page draws the same.

And session `api`, the realistic Opus 5.5 shape (a note before nearly every tool), under look C, the default (compact mode
folds the notes between tools into the tool run): a turn of four tool/note pairs is ONE run whose caption is its latest note,
and in the turn with a message delivered mid-turn the note answering it stands on its own while the four tool/note pairs
after it fold; an expanded run shows every tool and note in order.

The lab writes the store as the kernel's own writer would have (its marks are filed as frames arrive, so a mark can stand
before its record lands, which is how the live half is staged). The look is the page's localStorage "romp:narration-look".
With NARRATION_SHOTS=<dir> the driver writes screenshots: `web` in compact and full mode under looks A and B, dark and
light, and the realistic `api` turns in compact mode under looks A, B and C, dark and light, in <dir>/realistic/;
NARRATION_BEFORE_ROOT=<checkout> serves another checkout's kernel and built bundle for the before shots and skips the
assertions. Skips LOUDLY without the extension deps or a Playwright browser; the extension CI job requires it. SYNTHETIC
fixtures only: the notes-api demo world, sessions `web` and `api`, invented text, placeholder ids, host TESTHOST."""
import json
import os
import re
import shutil
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
EXT = os.path.join(ROOT, "vscode-extension")
sys.path.insert(0, HERE)
import test_ship_reship_served as _lab   # noqa: E402  the lab kernel's environment (the module, not its classes)

SID = "aaaaaaaa-5555-2222-3333-444444444444"
MODEL = "claude-opus-5-5"
SIG = "c3ludGhldGljLXNpZ25hdHVyZQ=="   # a synthetic signature: every block is signed, as the API's are
HISTORY_NOTE = "Looking at the router file to list the routes before answering."
REASONING = "The list handler builds one query with no limit. A cursor on the note id stays stable when notes are added, an offset does not."
NOTE_1 = "The list handler returns every row. I'll read it and its test, then add a cursor on the note id with a default page size."
MIDTURN = "keep the default page size at 50, not 20"
NOTE_2 = "Got it: the default page size is 50, not 20. I'm changing it and updating the one test that pinned 20."
REPLY = "Pagination is in. `GET /notes` takes `limit` (default 50) and `cursor`, and the notes tests pass."
REPLY_SHOWN = REPLY.replace("`", "")            # the reply as the page draws it (inline code is markup)
SID_API = "aaaaaaaa-5555-2222-3333-555555555555"
R_PROMPT_1 = "Add rate limiting to POST /notes."
R_NOTES_1 = ["The create handler has no middleware yet. I'll check how the app wires its other limits.",
             "There's a token bucket in src/limits.ts that the search route already uses; I'll reuse it.",
             "Wiring the bucket into POST /notes at 30 requests a minute per key.",
             "Running the notes tests to make sure nothing else trips the limit."]
R_REPLY_1 = "POST /notes is rate limited at 30 requests a minute per API key, with the token bucket the search route already had. The notes tests pass."
R_PROMPT_2 = "Add a test that hits the limit."
R_NOTES_2A = ["I'll add a test that sends 31 creates and checks the last one is refused.",
              "The test file already builds a client with a fixed key, so the new test can reuse it."]
R_MIDTURN = "use 429, not 403"
R_ANSWER = "Got it: the limit answers 429 Too Many Requests, not 403. I'm changing the middleware and the test's expectation."
R_NOTES_2B = ["The middleware and the test both say 429 now; running the suite.",
              "One search test reused the same key and hit the limit. Reading it.",
              "Giving the search test a key of its own.",
              "Running the suite again."]
SID_TESTS = "aaaaaaaa-5555-2222-3333-666666666666"
X_PROMPT_1 = "Make the notes search ignore case."
X_LONG = ("The change touches two places:\n\n- the query now wraps both sides in `lower()`;\n- the index on `title` no longer "
          "helps, so I added an expression index on `lower(title)`.\n\nRunning the search tests next, then the whole suite to "
          "be sure the index migration applies cleanly.")
X_REPLY_1 = ("Search ignores case now: `GET /notes/search?q=Notes` finds `notes` and `NOTES`. An index on `lower(title)` keeps "
             "the lookup fast, and a test covers mixed-case queries. All tests pass.")
X_PROMPT_2 = "Add a search by tag too."
X_TEXT_2 = "Writing the join and a tag filter on the search route."
X_MID_2 = "only exact tag matches, please"
X_ANSWER_2 = "Understood: tags match exactly, no partial matches."
X_NOTES_2 = ["Exact matching needs no `lower()` on tags; running the tests.",
             "One tag test expected a partial match; updating it to the exact rule."]
X_REPLY_2 = "Tag search is in: `?tag=work` returns notes tagged exactly `work`. All tests pass."
X_PROMPT_3 = "Paginate the search results."
X_MID_3 = "default limit 25"
X_ANSWER_3 = "Got it: the search page defaults to 25 results. Updating the default and its test."
X_REPLY_3 = "Search results page with `cursor` and `limit` (default 25). The search tests pass."
R_REPLY_2 = ("The limit test is in: the 31st create in a minute gets 429 Too Many Requests with a Retry-After header. I switched "
             "the middleware from 403 to 429 as you asked, and gave the search test its own key. All tests pass.")


def _free_port():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Transcript:
    """One session's records, one content block per record as Claude Code writes it, and the uuids whose block the CLI
    listed as a note (the store's rows)."""

    def __init__(self, sid, prefix, base):
        self.sid, self.prefix, self.recs, self.marks = sid, prefix, [], []
        self.prev, self.n, self.t, self.tc = None, 0, base, 0

    def add(self, rec):
        self.n += 1
        rec.update(sessionId=self.sid, parentUuid=self.prev, uuid="%s-%012d" % (self.prefix, self.n),
                   timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(self.t)))
        self.recs.append(rec)
        self.prev = rec["uuid"]
        self.t += 3
        return rec["uuid"]

    def user(self, text):
        self.add({"type": "user", "message": {"role": "user", "content": text}})

    def midturn(self, text):                         # a message delivered mid-turn: the CLI's queued_command splice
        self.add({"type": "attachment", "isSidechain": False, "attachment": {"type": "queued_command", "prompt": text}})

    def message(self, blocks, think_tokens=0, stop="tool_use", mark=()):
        mid = "msg_%s_%06d" % (self.prefix[:8], self.n + 1)
        usage = {"input_tokens": 10, "output_tokens": 200, "output_tokens_details": {"thinking_tokens": think_tokens}} if think_tokens \
            else {"input_tokens": 10, "output_tokens": 50}
        for i, b in enumerate(blocks):
            u = self.add({"type": "assistant", "message": {"id": mid, "role": "assistant", "model": MODEL, "stop_reason": stop,
                                                            "content": [b], "usage": usage}})
            if i in mark:
                self.marks.append(u)

    def tool(self, name, inp, out, thinks=(), tokens=0, mark=()):
        self.tc += 1
        tid = "toolu_%s_%02d" % (self.prefix[:8], self.tc)
        self.message(list(thinks) + [{"type": "tool_use", "id": tid, "name": name, "input": inp}], tokens, mark=mark)
        self.add({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out}]},
                  "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})

    def note_tool(self, note, name, inp, out="ok"):   # the Opus 5.5 shape: a note, then the tool it introduces
        self.tool(name, inp, out, [think(note)], 300, mark=(0,))

    def reply(self, text):
        self.message([{"type": "text", "text": text}], stop="end_turn")

    def text_tool(self, text, name, inp, out="ok"):   # a short note the API returns as a TEXT block, then its tool
        self.tc += 1
        tid = "toolu_%s_%02d" % (self.prefix[:8], self.tc)
        self.message([{"type": "text", "text": text}, {"type": "tool_use", "id": tid, "name": name, "input": inp}])
        self.add({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid, "content": out}]},
                  "toolUseResult": {"stdout": out, "stderr": "", "interrupted": False, "isImage": False}})


def think(text=""):
    return {"type": "thinking", "thinking": text, "signature": SIG}


def _records(cwd, base):
    """Session `web`: (first part, live part, the uuids to mark). An older turn whose note carries no mark; then the turn
    under test: a reasoning summary and a note before the first tool, a second tool, the mid-turn message, then (the live
    part) the note answering it, two tools and the reply."""
    tr = _Transcript(SID, "bbbbbbbb-5555-2222-3333", base)
    src = cwd + "/src/routes/notes.ts"
    tr.user("Which routes does the notes service have?")
    tr.tool("Bash", {"command": "grep -n router src/routes/notes.ts", "description": "Listed the routes"}, "3 routes",
            [think(HISTORY_NOTE)], 600)
    tr.reply("Three: list, get and create.")
    tr.user("Add cursor pagination to GET /notes.")
    tr.tool("Read", {"file_path": src}, "export function listNotes(db) { return db.all('select * from notes'); }",
            [think(REASONING), think(NOTE_1)], 1840, mark=(1,))
    tr.tool("Read", {"file_path": cwd + "/tests/notes.test.ts"}, "test('lists notes', ...)", [think()], 420)
    tr.midturn(MIDTURN)
    first = len(tr.recs)
    tr.tool("Edit", {"file_path": src, "old_string": "limit = 20", "new_string": "limit = 50"}, "edited", [think(NOTE_2)], 960, mark=(0,))
    tr.tool("Bash", {"command": "npm test -- notes", "description": "Ran the notes tests"}, "3 passing", [think()], 300)
    tr.reply(REPLY)
    return tr.recs[:first], tr.recs[first:], tr.marks


def _realistic(cwd, base):
    """Session `api`, the realistic Opus 5.5 shape: a turn of four note/tool pairs and its answer; then a turn with two pairs,
    a message delivered mid-turn, the note answering it, four tool/note pairs, and the answer."""
    tr = _Transcript(SID_API, "cccccccc-5555-2222-3333", base)
    tr.user(R_PROMPT_1)
    n1 = R_NOTES_1
    tr.note_tool(n1[0], "Read", {"file_path": cwd + "/src/app.ts"})
    tr.note_tool(n1[1], "Read", {"file_path": cwd + "/src/limits.ts"})
    tr.note_tool(n1[2], "Edit", {"file_path": cwd + "/src/routes/notes.ts", "old_string": "router.post('/notes', create)",
                                 "new_string": "router.post('/notes', bucket(30), create)"}, "edited")
    tr.note_tool(n1[3], "Bash", {"command": "npm test -- notes", "description": "Ran the notes tests"}, "12 passing")
    tr.reply(R_REPLY_1)
    tr.user(R_PROMPT_2)
    tr.note_tool(R_NOTES_2A[0], "Read", {"file_path": cwd + "/tests/notes.test.ts"})
    tr.note_tool(R_NOTES_2A[1], "Edit", {"file_path": cwd + "/tests/notes.test.ts", "old_string": "});",
                                         "new_string": "test('refuses the 31st create', ...);\n});"}, "edited")
    tr.midturn(R_MIDTURN)
    tr.note_tool(R_ANSWER, "Edit", {"file_path": cwd + "/src/limits.ts", "old_string": "403", "new_string": "429"}, "edited")
    tr.tool("Edit", {"file_path": cwd + "/tests/notes.test.ts", "old_string": "403", "new_string": "429"}, "edited")
    tr.note_tool(R_NOTES_2B[0], "Bash", {"command": "npm test", "description": "Ran the tests"}, "1 failing")
    tr.note_tool(R_NOTES_2B[1], "Read", {"file_path": cwd + "/tests/search.test.ts"})
    tr.note_tool(R_NOTES_2B[2], "Edit", {"file_path": cwd + "/tests/search.test.ts", "old_string": "key: 'k1'",
                                         "new_string": "key: 'k2'"}, "edited")
    tr.note_tool(R_NOTES_2B[3], "Bash", {"command": "npm test", "description": "Ran the tests again"}, "all passing")
    tr.reply(R_REPLY_2)
    return tr.recs, tr.marks


def _realistic2(cwd, base):
    """Session `tests`, the mix Opus 5.5 writes: notes as thinking blocks and as short TEXT blocks between tools, a long note
    with a list, a turn with no message mid-turn, one whose mid-turn answer is a text block and one whose answer is a note;
    the segments before the mid-turn messages end on a tool, inside a run, so their "worked" line rides the run."""
    tr = _Transcript(SID_TESTS, "dddddddd-5555-2222-3333", base)
    src, tst = cwd + "/src/routes/search.ts", cwd + "/tests/search.test.ts"
    tr.user(X_PROMPT_1)
    tr.text_tool("Looking at how search builds its WHERE clause.", "Read", {"file_path": src})
    tr.note_tool("Search compares the raw title with `=`, so `Notes` and `notes` miss each other. I'll lower both sides.",
                 "Edit", {"file_path": src, "old_string": "title = ?", "new_string": "lower(title) = lower(?)"}, "edited")
    tr.text_tool("Adding a test for mixed-case queries.", "Edit", {"file_path": tst, "old_string": "});",
                                                                   "new_string": "test('ignores case', ...);\n});"}, "edited")
    tr.note_tool(X_LONG, "Bash", {"command": "npm test -- search", "description": "Ran the search tests"}, "9 passing")
    tr.reply(X_REPLY_1)
    tr.user(X_PROMPT_2)
    tr.note_tool("Tags live in their own table; I'll join it.", "Read", {"file_path": cwd + "/src/db/schema.ts"})
    tr.text_tool(X_TEXT_2, "Edit", {"file_path": src, "old_string": "where", "new_string": "join note_tags where"}, "edited")
    tr.midturn(X_MID_2)
    tr.text_tool(X_ANSWER_2, "Edit", {"file_path": src, "old_string": "like ?", "new_string": "= ?"}, "edited")
    tr.note_tool(X_NOTES_2[0], "Bash", {"command": "npm test", "description": "Ran the tests"}, "1 failing")
    tr.note_tool(X_NOTES_2[1], "Edit", {"file_path": tst, "old_string": "'wor'", "new_string": "'work'"}, "edited")
    tr.reply(X_REPLY_2)
    tr.user(X_PROMPT_3)
    tr.note_tool("Reusing the cursor from GET /notes.", "Read", {"file_path": cwd + "/src/routes/notes.ts"})
    tr.text_tool("Adding `cursor` and `limit` to search.", "Edit", {"file_path": src, "old_string": "all(", "new_string": "page("}, "edited")
    tr.midturn(X_MID_3)
    tr.note_tool(X_ANSWER_3, "Edit", {"file_path": src, "old_string": "limit = 20", "new_string": "limit = 25"}, "edited")
    tr.text_tool("Running the search tests.", "Bash", {"command": "npm test -- search", "description": "Ran the search tests"}, "10 passing")
    tr.reply(X_REPLY_3)
    return tr.recs, tr.marks


DRIVER = r"""
import { createRequire } from "node:module";
import fs from "node:fs";
const require = createRequire(process.env.EXT_PKG);
const { chromium } = require("playwright");
const cfg = JSON.parse(fs.readFileSync(process.env.CFG, "utf8"));
let browser;
try { browser = await chromium.launch(); }
catch (e) { console.error("browser-launch-failed: " + e); process.exit(3); }
const painted = (page) => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => setTimeout(r, 60)))));
const errors = [];
const showing = (t) => Array.from(document.querySelectorAll("#content .turn")).some((n) => n.getClientRects().length > 0 && (n.textContent || "").includes(t));
// one page: the theme, compact or full, the look (A, B, C: localStorage romp:narration-look), the session's tab, and a text it must show
const open = async (theme, compact, look, sid, wait, h) => {
  const page = await browser.newPage({ viewport: { width: 1200, height: h || 1000 }, deviceScaleFactor: 1 });
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
  await page.addInitScript(([th, c, lk]) => { try { localStorage.setItem("romp:settings", JSON.stringify({ theme: th, compact: c })); if (lk) localStorage.setItem("romp:narration-look", lk); } catch (e) {} }, [theme, compact, look]);
  await page.goto(cfg.chat);
  await page.waitForSelector('#tabs .tab[data-id="' + sid + '"]', { state: "attached", timeout: 30000 });
  // the session's tab: a real click on the strip's visible element
  const active = await page.evaluate((id) => !!document.querySelector('#tabs .tab.active[data-id="' + id + '"]'), sid);
  if (!active) await page.locator('#tabs .tab[data-id="' + sid + '"]:visible').first().click();
  try { await page.waitForFunction(showing, wait, { timeout: 30000 }); }
  catch (e) {
    const st = await page.evaluate(() => ({ tabs: Array.from(document.querySelectorAll("#tabs .tab[data-id]")).map((t) => t.getAttribute("data-id") + (t.classList.contains("active") ? "*" : "")),
      shown: Array.from(document.querySelectorAll("#content .turn")).filter((n) => n.getClientRects().length > 0).map((n) => (n.textContent || "").slice(0, 40)) }));
    throw new Error("open(" + sid + "): " + JSON.stringify(wait) + " never showed: " + JSON.stringify(st));
  }
  await page.addStyleTag({ content: "#content .turn-apierror { display: none !important; }" });   // the lab kernel runs no SDK backend: its notice is not this test's
  await painted(page);
  return page;
};
// the agent's side of the chat, in order: what each row is and the words it shows
const rows = (page) => page.evaluate(() => Array.from(document.querySelectorAll("#content .turn")).filter((t) => t.getClientRects().length > 0).map((t) => {
  const c = t.className;
  const kind = c.includes("turn-thought") ? "thought" : c.includes("turn-thinking") ? "thinking" : c.includes("turn-assistant") ? "assistant"
             : c.includes("turn-user") ? "user" : c.includes("turn-toolgroup") ? "toolgroup" : c.includes("turn-tool") ? "tool" : "other";
  const cap = t.querySelector(".toolgroup-caption");
  const body = kind === "toolgroup" ? null : (t.querySelector(".assistant.md") || t.querySelector(".thought-gist") || t.querySelector(".thinking") || t.querySelector(".user-bubble"));
  const words = (n) => n ? (n.innerText || n.textContent || "").replace(/\s+/g, " ").trim() : "";
  const foot = t.querySelector(":scope > .turn-elapsed");
  const capLh = cap ? parseFloat(getComputedStyle(cap).lineHeight) || 18 : 0;
  return { kind, narration: c.includes("turn-narration"), opaque: !!t.querySelector(".thinking.encrypted"), fork: !!t.querySelector(".fork-spot"),
           child: c.includes("tg-child"), text: words(body), caption: words(cap), head: words(t.querySelector(".toolgroup-head")),
           foot: foot ? words(foot) : "", capLines: cap ? Math.round(cap.getBoundingClientRect().height / capLh) : 0,
           capClamp: cap ? getComputedStyle(cap).webkitLineClamp : "" };
}));
const shot = async (page, dir, name, prompt) => {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.evaluate((p) => { const t = Array.from(document.querySelectorAll("#content .turn-user")).find((n) => (n.textContent || "").includes(p)); if (t) t.scrollIntoView({ block: "start" }); }, prompt);
  await painted(page);
  await page.screenshot({ path: dir + "/" + name + ".png" });
};
const out = {};
// LIVE (web, look A): the page holds the first half; the second half lands while it is open and arrives through the push
const live = await open("yatharth", true, "A", cfg.web, cfg.midturn);
out.beforeLand = await rows(live);
fs.writeFileSync(cfg.landFlag, "go");
await live.waitForFunction(showing, cfg.replyStart, { timeout: 30000 });
await painted(live);
out.live = await rows(live);
await live.close();
// AFTER RELOAD (web): look A in both modes, and the shots of A and B
for (const [compact, mname] of [[true, "compact"], [false, "full"]]) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    for (const look of cfg.before ? [""] : ["A", "B"]) {
      const p = await open(theme, compact, look, cfg.web, cfg.replyStart);
      if (tname === "dark" && look !== "B") out[mname] = await rows(p);
      await shot(p, cfg.shots, "narration-" + mname + "-" + tname + (look === "B" ? "-optB" : ""), cfg.prompt);
      await p.close();
    }
  }
}
// THE REALISTIC TURNS (api, compact): A, B and C, dark and light; C's rows, and its runs opened
for (const look of cfg.before ? [""] : ["A", "B", "C"]) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    const p = await open(theme, true, look, cfg.api, cfg.rReplyStart, 1300);
    await shot(p, cfg.shots && cfg.shots + "/realistic", "realistic-" + (look || "before") + "-" + tname, cfg.rPrompt);
    if (look === "C" && tname === "dark") {
      out.realC = await rows(p);
      const lines = p.locator("#content .turn-toolgroup .toolgroup-line:visible");
      const n = await lines.count();
      await lines.nth(n - 1).click();                    // the last run: the four tool/note pairs after the answer
      await p.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
      await painted(p);
      out.realCOpen = await rows(p);
      await shot(p, cfg.shots && cfg.shots + "/realistic", "realistic-C-dark-opened", cfg.rPrompt2);
    }
    await p.close();
  }
}
// THE MIXED TURNS (tests, compact): A, C and C2, dark and light, and C2 with its first run opened
const dir2 = cfg.shots2 || (cfg.shots && cfg.shots + "/realistic2");
for (const look of cfg.before ? [""] : ["A", "C", "C2"]) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    const p = await open(theme, true, look, cfg.tests, cfg.xReplyStart, 1500);
    await shot(p, dir2, "mixed-" + (look || "before") + "-" + tname, cfg.xPrompt);
    if (tname === "dark" && look) out["mixed" + look] = await rows(p);
    if (look === "C2" && tname === "dark") {
      await p.locator("#content .turn-toolgroup .toolgroup-line:visible").first().click();   // the first run: the long note inside
      await p.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
      await painted(p);
      out.mixedC2Open = await rows(p);
      await shot(p, dir2, "mixed-C2-dark-opened", cfg.xPrompt);
    }
    await p.close();
  }
}
out.errors = errors;
fs.writeSync(1, "RESULT:" + JSON.stringify(out) + "\n");
await browser.close();
process.exit(0);
"""


class ServedNarration(unittest.TestCase):
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
            raise unittest.SkipTest("extension deps absent (npm ci not run here) — the served guard needs them; CI's extension job has them and requires this file to run")
        probe = subprocess.run(["node", "-e", "const p=require(process.argv[1]);process.stdout.write(p.chromium.executablePath())",
                                os.path.join(EXT, "node_modules", "playwright")], capture_output=True, text=True)
        if probe.returncode != 0 or not os.path.exists(probe.stdout.strip()):
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        cls.lab = tempfile.mkdtemp(prefix="narration-served-")
        cls.before = os.environ.get("NARRATION_BEFORE_ROOT", "")
        root = cls.before or ROOT
        if not cls.before:
            b = subprocess.run(["node", "esbuild.js"], cwd=EXT, capture_output=True, text=True)
            if b.returncode != 0:
                raise unittest.SkipTest("esbuild failed here: " + (b.stderr or b.stdout)[-200:])
        dist = os.path.join(cls.lab, "dist")
        copy_dist(os.path.join(root, "vscode-extension", "dist"), dist)
        state = os.path.join(cls.lab, "xdg", "romp")
        claude = os.path.join(cls.lab, "claude")
        cwd = os.path.join(cls.lab, "notes-api")
        for d in ("names", "sdk", "states", "narration"):
            os.makedirs(os.path.join(state, d), exist_ok=True)
        os.makedirs(cwd, exist_ok=True)
        Path(state, "session-hosts").write_text("off\n")   # this lab mints its own state root: no per-session host (root CLAUDE.md)
        Path(state, "names", SID).write_text("web\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID + ".json").write_text(json.dumps(
            {"sid": SID, "name": "web", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID, "alive": True,
             "model": MODEL, "liveModel": "Opus 5.5"}))
        Path(state, "usage.json").write_text(json.dumps({"five_hour": {"pct": 10}, "seven_day": {"pct": 10}}))
        proj = os.path.join(claude, "projects", re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd)))
        os.makedirs(proj, exist_ok=True)
        cls.first, cls.rest, cls.marks = _records(cwd, int(time.time()) - 3600)
        cls.tpath = os.path.join(proj, SID + ".jsonl")
        Path(cls.tpath).write_text("".join(json.dumps(r) + "\n" for r in cls.first))
        api_recs, api_marks = _realistic(cwd, int(time.time()) - 1800)
        Path(proj, SID_API + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in api_recs))
        Path(state, "names", SID_API).write_text("api\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID_API + ".json").write_text(json.dumps(
            {"sid": SID_API, "name": "api", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID_API, "alive": True,
             "model": MODEL, "liveModel": "Opus 5.5"}))
        # the store as the kernel's writer leaves it: one row per frame that listed a note, filed as the frame arrived (so
        # the live half's mark stands before its record lands)
        x_recs, x_marks = _realistic2(cwd, int(time.time()) - 900)
        Path(proj, SID_TESTS + ".jsonl").write_text("".join(json.dumps(r) + "\n" for r in x_recs))
        Path(state, "names", SID_TESTS).write_text("tests\t%s\t\t\n" % cwd)
        Path(state, "sdk", SID_TESTS + ".json").write_text(json.dumps(
            {"sid": SID_TESTS, "name": "tests", "cwd": cwd, "mode": "auto", "effort": "high", "lastSid": SID_TESTS, "alive": True,
             "model": MODEL, "liveModel": "Opus 5.5"}))
        for sid, marks in ((SID, cls.marks), (SID_API, api_marks), (SID_TESTS, x_marks)):
            Path(state, "narration", sid + ".jsonl").write_text("".join(json.dumps({"u": u, "i": [0], "k": [0]}) + "\n" for u in marks))
        cls.port, cls.token = _free_port(), "testtok-narration"
        env = _lab.kernel_env(cls.lab, claude, dist, cls.port, cls.token, ROMP_HOST_NAME="TESTHOST")
        cls.klog = os.path.join(cls.lab, "kernel.log")
        cls.kernel = subprocess.Popen([os.path.join(root, "bin", "romp-kernel")], stdout=open(cls.klog, "w"), stderr=subprocess.STDOUT, env=env)
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

    def _drive(self):
        cfg = os.path.join(self.lab, "cfg.json")
        flag = os.path.join(self.lab, "land.flag")
        with open(cfg, "w") as f:
            json.dump({"chat": "http://127.0.0.1:%d/chat?token=%s" % (self.port, self.token), "midturn": MIDTURN,
                       "web": SID, "api": SID_API, "prompt": "Add cursor pagination", "replyStart": "Pagination is in.",
                       "rPrompt": R_PROMPT_1, "rPrompt2": R_PROMPT_2, "rReplyStart": R_REPLY_2[:40], "landFlag": flag,
                       "tests": SID_TESTS, "xPrompt": X_PROMPT_1, "xReplyStart": X_REPLY_3.replace("`", "")[:30],
                       "shots2": os.environ.get("NARRATION_SHOTS_REALISTIC2", ""),
                       "shots": os.environ.get("NARRATION_SHOTS", ""), "before": bool(self.before)}, f)
        driver = os.path.join(self.lab, "driver.mjs")
        with open(driver, "w") as f:
            f.write(DRIVER)
        p = subprocess.Popen(["node", driver], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env=dict(os.environ, EXT_PKG=os.path.join(EXT, "package.json"), CFG=cfg))
        # the live half lands once the driver holds the first: the driver drops the flag, the transcript grows
        deadline = time.time() + 120
        while not os.path.exists(flag) and p.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        if os.path.exists(flag):
            with open(self.tpath, "a") as f:
                f.write("".join(json.dumps(r) + "\n" for r in self.rest))
        stdout, stderr = p.communicate(timeout=300)
        if p.returncode == 3:
            raise unittest.SkipTest("no playwright browser on this box — the served guard needs one; CI's extension job installs Chromium and requires this file to run")
        self.assertEqual(p.returncode, 0, "driver failed:\n" + stdout[-3000:] + stderr[-3000:] + "\nkernel:\n" + open(self.klog).read()[-1500:])
        line = next((ln for ln in stdout.splitlines() if ln.startswith("RESULT:")), None)
        self.assertIsNotNone(line, "driver printed no result:\n" + stdout[-3000:] + stderr[-2000:])
        return json.loads(line[len("RESULT:"):])

    def _agent_rows(self, rows, after_prompt):
        """The rows from the turn under test's prompt on, tools and the tool runs collapsed to one kind."""
        i = next(k for k, r in enumerate(rows) if r["kind"] == "user" and after_prompt in r["text"])
        return [(r["kind"] if r["kind"] not in ("toolgroup",) else "tool", r["narration"], r["text"]) for r in rows[i + 1:]
                if r["kind"] != "other" and not r.get("opaque")]   # a textless block's token line (full mode) is not under test

    @classmethod
    def _result(cls, test):
        if getattr(cls, "_r", None) is None:          # one drive serves both tests (the live half lands once)
            try:
                cls._r = ("ok", test._drive())
            except BaseException as e:                # a skip or a failure is the same for the second test
                cls._r = ("raise", e)
        if cls._r[0] == "raise":
            raise cls._r[1]
        return cls._r[1]

    def result(self):
        return type(self)._result(self)

    def test_progress_notes_read_as_prose_live_and_after_reload_in_compact_and_full_mode(self):
        r = self.result()
        if self.before:
            self.skipTest("a before-the-change checkout: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        note = lambda t: ("assistant", True, t)
        # LIVE, compact: before the second half lands, the first note is prose and the reasoning a Thought row
        pre = self._agent_rows(r["beforeLand"], "Add cursor pagination")
        self.assertIn(note(NOTE_1), pre, "the first note is prose before anything lands: %r" % pre)
        self.assertEqual([x for x in pre if x[0] in ("thought", "assistant")][:2], [("thought", False, REASONING), note(NOTE_1)],
                         "the reasoning summary stays a Thought row, and the note follows it as prose: %r" % pre)
        live = self._agent_rows(r["live"], "Add cursor pagination")
        self.assertIn(note(NOTE_2), live, "the answer to the mid-turn message arrives as prose through the push: %r" % live)
        mid = next(k for k, x in enumerate(live) if x[0] == "user" and MIDTURN in x[2])
        self.assertEqual(live[mid + 1], note(NOTE_2), "the answer sits right after the mid-turn message: %r" % live[mid:])
        self.assertEqual(live[-1], ("assistant", False, REPLY_SHOWN), "the reply is a reply, not a note")
        # AFTER RELOAD: compact, and full, where the reasoning is the clamped thinking row
        compact = self._agent_rows(r["compact"], "Add cursor pagination")
        self.assertEqual([x for x in compact if x[0] != "tool"],
                         [("thought", False, REASONING), note(NOTE_1), ("user", False, MIDTURN), note(NOTE_2),
                          ("assistant", False, REPLY_SHOWN)], "compact after reload: %r" % compact)
        full = self._agent_rows(r["full"], "Add cursor pagination")
        self.assertEqual([x for x in full if x[0] in ("thinking", "assistant", "user") and x[2]],
                         [("thinking", False, REASONING), note(NOTE_1), ("user", False, MIDTURN), note(NOTE_2),
                          ("assistant", False, REPLY_SHOWN)], "full after reload: %r" % full)
        # a note is the agent speaking mid-turn, not a response a fork cuts below: no fork row under it
        notes = [x for x in r["compact"] + r["full"] if x["narration"]]
        self.assertEqual(len(notes), 4)
        self.assertFalse(any(x["fork"] for x in notes), "no fork row under a note: %r" % notes)
        # history with no mark keeps its Thought row
        hist = [x for x in r["compact"] if x["kind"] in ("thought", "assistant") and HISTORY_NOTE[:30] in x["text"]]
        self.assertEqual([(x["kind"], x["narration"]) for x in hist], [("thought", False)], "unmarked history: %r" % hist)

    def test_mixed_notes_c2_folds_text_and_thinking_notes_clamps_captions_and_keeps_answers_and_footers(self):
        r = self.result()
        if self.before:
            self.skipTest("a before-the-change checkout: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        shown = lambda t: t.replace("`", "")
        rows = [x for x in r["mixedC2"] if x["kind"] != "other"]
        shape = [(x["kind"], x["narration"], (x["caption"][:30] if x["kind"] == "toolgroup" else x["text"][:30])) for x in rows]
        self.assertEqual(shape, [
            ("user", False, X_PROMPT_1[:30]),
            ("toolgroup", False, "The change touches two places:"),   # text and thinking notes fold; the long note is the caption
            ("assistant", False, shown(X_REPLY_1)[:30]),               # the final message after the last tool stays a reply
            ("user", False, X_PROMPT_2[:30]),
            ("toolgroup", False, X_TEXT_2[:30]),                      # a TEXT note is the caption
            ("user", False, X_MID_2[:30]),
            ("assistant", False, X_ANSWER_2[:30]),                    # the answer as a TEXT block stands alone
            ("toolgroup", False, shown(X_NOTES_2[1])[:30]),
            ("assistant", False, shown(X_REPLY_2)[:30]),
            ("user", False, X_PROMPT_3[:30]),
            ("toolgroup", False, shown("Adding `cursor` and `limit` to search.")[:30]),
            ("user", False, X_MID_3[:30]),
            ("assistant", True, X_ANSWER_3[:30]),                     # the answer as a NOTE stands alone too
            ("toolgroup", False, "Running the search tests."[:30]),
            ("assistant", False, shown(X_REPLY_3)[:30]),
        ], "C2, compact: %r" % shape)
        long_cap = rows[1]
        self.assertEqual(long_cap["capClamp"], "2", "the caption is a two-line clamp, not a fade")
        self.assertLessEqual(long_cap["capLines"], 2, "…and shows two lines at most: %r" % long_cap)
        answer = rows[6]
        self.assertEqual(answer["capLines"], 0)
        # the segment before each mid-turn message ends on a tool inside a folded run: its "worked" line rides the run
        self.assertTrue(rows[4]["foot"], "the run before the first mid-turn message carries the segment's footer: %r" % rows[4])
        self.assertTrue(rows[10]["foot"], "…and the one before the second: %r" % rows[10])
        # opened: every tool and note in order, the long note whole
        opened = [x for x in r["mixedC2Open"] if x["child"]]
        self.assertEqual(["note" if x["kind"] == "assistant" else x["kind"] for x in opened],
                         ["note", "tool", "note", "tool", "note", "tool", "note", "tool"], "opened: %r" % opened)
        self.assertIn("then the whole suite", opened[6]["text"], "the long note opens whole")
        # C (thinking notes only): a text answer to a mid-turn message disarms the rule; no later note is pulled out
        c = [x for x in r["mixedC"] if x["kind"] != "other"]
        i = next(k for k, x in enumerate(c) if x["kind"] == "user" and X_MID_2 in x["text"])
        self.assertEqual([(x["kind"], x["narration"]) for x in c[i + 1:i + 4]],
                         [("assistant", False), ("toolgroup", False), ("assistant", False)],
                         "C: the text answer, then ONE run holding both later notes, then the reply: %r" % c[i:i + 5])

    def test_look_c_folds_the_notes_between_tools_into_the_run_and_keeps_the_answer_to_a_midturn_message_out(self):
        r = self.result()
        if self.before:
            self.skipTest("a before-the-change checkout: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        rows = [x for x in r["realC"] if x["kind"] != "other"]
        shape = [(x["kind"], x["narration"], x["caption"] if x["kind"] == "toolgroup" else x["text"]) for x in rows]
        self.assertEqual(shape, [
            ("user", False, R_PROMPT_1),
            ("toolgroup", False, R_NOTES_1[3]),            # four note/tool pairs: ONE run, its caption the latest note
            ("assistant", False, R_REPLY_1),
            ("user", False, R_PROMPT_2),
            ("toolgroup", False, R_NOTES_2A[1]),           # the two pairs before the message
            ("user", False, R_MIDTURN),
            ("assistant", True, R_ANSWER),                 # the answer to the message, on its own as prose
            ("toolgroup", False, R_NOTES_2B[3]),           # the pairs after it fold again
            ("assistant", False, R_REPLY_2),
        ], "look C, compact: %r" % shape)
        self.assertTrue(rows[1]["head"].startswith("Read 2 files"), "the run's head still counts its tools: %r" % rows[1])
        # the last run opened: every tool and note in order, each note as prose
        opened = [x for x in r["realCOpen"] if x["child"]]
        kinds = ["note" if x["narration"] else x["kind"] for x in opened]
        self.assertEqual(kinds, ["tool", "tool", "note", "tool", "note", "tool", "note", "tool", "note", "tool"], "opened: %r" % opened)
        self.assertEqual([x["text"] for x in opened if x["narration"]], R_NOTES_2B)
        head = next(x for x in r["realCOpen"] if x["kind"] == "toolgroup" and not x["head"])
        self.assertEqual(head["caption"], "", "an open run shows its notes in place, not the caption")


if __name__ == "__main__":
    unittest.main()
