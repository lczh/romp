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
X_PROMPT_4 = "Add an archive flag to notes."
X_QUESTION = "Should archived notes still show up in search? I'm hiding them for now; tell me if they should stay."
X_REPLY_4 = "Notes can be archived now, and archived notes are hidden from the list and from search. Say if search should keep them."
X_PROMPT_5 = "Clean up the old export script."
X_SUMMARY = "Removed `scripts/export.sh` and its npm task; nothing referenced them, and `/notes/export` covers what it did."
X_PROMPT_6 = "Show me how the search query looks now."
X_CODE_NOTE = ("Here is the query as it stands:\n\n```sql\nselect n.* from notes n\njoin note_tags t on t.note_id = n.id\n"
               "where lower(n.title) = lower(?)\n  and n.archived = 0\norder by n.id\nlimit ?\n```\n\nRunning it on the sample "
               "data to confirm the plan uses the index.")
X_PROMPT_7 = "Run the migration and the e2e tests."
X_STATE_NOTES = ["Running the migration first.",
                 "The column is there from an earlier run, so I'm fixing the two routes instead and linting.",
                 "Starting the e2e suite; it takes a few minutes."]
X_REPLY_6 = "The query joins the tags, matches the title without case and skips archived notes; its plan uses the `lower(title)` index."
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

    def tool(self, name, inp, out, thinks=(), tokens=0, mark=(), error=False, pending=False):
        self.tc += 1
        tid = "toolu_%s_%02d" % (self.prefix[:8], self.tc)
        self.message(list(thinks) + [{"type": "tool_use", "id": tid, "name": name, "input": inp}], tokens, mark=mark)
        if pending:                                   # no result yet: the call is still running
            return
        res = {"type": "tool_result", "tool_use_id": tid, "content": out}
        if error:
            res["is_error"] = True
        self.add({"type": "user", "message": {"role": "user", "content": [res]},
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
    # a question to the person between tools, then more tools and notes
    tr.user(X_PROMPT_4)
    tr.note_tool("Adding an `archived` column with a migration.", "Edit", {"file_path": cwd + "/db/migrations/007.sql",
                                                                           "old_string": "", "new_string": "alter table notes add archived"}, "edited")
    tr.text_tool(X_QUESTION, "Edit", {"file_path": src, "old_string": "where", "new_string": "where archived = 0 and"}, "edited")
    tr.note_tool("Updating the list route to skip archived notes too.", "Edit", {"file_path": cwd + "/src/routes/notes.ts",
                                                                                 "old_string": "where", "new_string": "where archived = 0 and"}, "edited")
    tr.note_tool("Running the tests.", "Bash", {"command": "npm test", "description": "Ran the tests"}, "all passing")
    tr.reply(X_REPLY_4)
    # the turn's summary, then a cleanup tool after it
    tr.user(X_PROMPT_5)
    tr.note_tool("The export script is unused since the API grew `/notes/export`.", "Bash",
                 {"command": "grep -rn export.sh .", "description": "Searched for the script"}, "no matches")
    tr.text_tool(X_SUMMARY, "Bash", {"command": "rm -rf tmp/export-cache", "description": "Removed the export cache"}, "")
    tr.tool("Bash", {"command": "git status --short", "description": "Checked the tree"}, "")
    # a long note with a code block before two tools
    tr.user(X_PROMPT_6)
    tr.note_tool(X_CODE_NOTE, "Bash", {"command": "sqlite3 notes.db < q.sql", "description": "Ran the query"}, "3 rows")
    tr.tool("Bash", {"command": "sqlite3 notes.db 'explain query plan ...'", "description": "Read the query plan"}, "USING INDEX")
    tr.reply(X_REPLY_6)
    # the tools' states: a failed command after a note, a line of three calls with one failure, a command still running
    tr.user(X_PROMPT_7)
    tr.tool("Bash", {"command": "npm run migrate", "description": "Ran the migration"},
            "Error: column archived already exists\n    at migrate (db/migrate.js:12)", [think(X_STATE_NOTES[0])], 300, mark=(0,), error=True)
    tr.tool("Edit", {"file_path": cwd + "/src/routes/notes.ts", "old_string": "a", "new_string": "b"}, "edited",
            [think(X_STATE_NOTES[1])], 300, mark=(0,))
    tr.tool("Edit", {"file_path": cwd + "/src/routes/search.ts", "old_string": "zz", "new_string": "b"},
            "String to replace not found in file.", error=True)
    tr.tool("Bash", {"command": "npm run lint", "description": "Ran the linter"}, "clean")
    tr.tool("Bash", {"command": "npm run e2e", "description": "Ran the e2e tests"}, "", [think(X_STATE_NOTES[2])], 300, mark=(0,), pending=True)
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
  await page.mouse.move(700, (h || 1000) - 24);   // over the composer, off the tab strip: a tab's tooltip names the machine's login, never a shot's
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
           attached: c.includes("toolgroup-attached") || c.includes("tool-attached"), err: c.includes("tool-err") || !!t.querySelector(":scope > .dot.err"),
           dotShown: (() => { const d = t.querySelector(":scope > .dot"); return !!d && getComputedStyle(d).visibility === "visible"; })(),
           working: !!t.querySelector(":scope > .dot.working"), tool: words(t.querySelector(".tool-head")), line: words(t.querySelector(".toolgroup-line")),
           file: words(t.querySelector(".tool-head .tool-file, .tool-head a")), foot: foot ? words(foot) : "", capLines: cap ? Math.round(cap.getBoundingClientRect().height / capLh) : 0,
           capClamp: cap ? getComputedStyle(cap).webkitLineClamp : "" };
}));
const shot = async (page, dir, name, prompt, hover) => {
  if (!dir) return;
  fs.mkdirSync(dir, { recursive: true });
  await page.evaluate((p) => { const t = Array.from(document.querySelectorAll("#content .turn-user")).find((n) => (n.textContent || "").includes(p)); if (t) t.scrollIntoView({ block: "start" }); }, prompt);
  if (hover) await page.locator(hover).first().hover(); else await page.mouse.move(700, page.viewportSize().height - 24);   // the composer: never the tab strip
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
// THE MIXED TURNS (tests, compact): A, E (the default) and C2, dark and light, the realistic turns and the hard cases (a question
// mid-turn, a summary before cleanup tools, a long note with code) as two shots each; C2 with its first run opened by its
// caption; C for its rows alone. E, the default, is probed further, and shot again into realistic4: the tools' states (a failed
// command, a line with one failure, a command still running and its timer), a fork spot revealed by a hover, a line opened
const dir3 = cfg.shots3 || (cfg.shots && cfg.shots + "/realistic3");
const dir4 = cfg.shots4 || (cfg.shots && cfg.shots + "/realistic4");
const lineAfter = (p, text) => p.locator("#content .turn-assistant", { hasText: text }).first()
  .locator("xpath=following-sibling::div[contains(concat(' ', normalize-space(@class), ' '), ' turn ')][1]");
for (const look of cfg.before ? [""] : ["A", "E", "C2", "C"]) {
  for (const [theme, tname] of [["yatharth", "dark"], ["yatharth-light", "light"]]) {
    if (look === "C" && tname === "light") continue;
    const p = await open(theme, true, look, cfg.tests, cfg.xReplyStart, 1500);
    if (look !== "C") {
      await shot(p, dir3, "mixed-" + (look || "before") + "-" + tname, cfg.xPrompt);
      await shot(p, dir3, "cases-" + (look || "before") + "-" + tname, cfg.xPrompt4);
    }
    if (tname === "dark" && look) out["mixed" + look] = await rows(p);
    if (look === "C2" && tname === "dark") {
      // the caption of the first run opens it (its ellipsis invites the click)
      await p.locator("#content .toolgroup-caption:visible").first().click();
      await p.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
      await painted(p);
      out.mixedC2Open = await rows(p);
    }
    if (look === "E") {
      await shot(p, dir4, "mixed-E-" + tname, cfg.xPrompt);
      await shot(p, dir4, "onecall-E-" + tname, cfg.xPrompt4);
      await shot(p, dir4, "states-E-" + tname, cfg.xPrompt7);
      if (tname === "dark") {
        // the running command's time ticks without a rebuild
        const tick = () => p.evaluate(() => { const ts = Array.from(document.querySelectorAll("#content .tool-attached .tool-elapsed, #content .toolgroup-attached .tool-elapsed")).filter((n) => n.getClientRects().length > 0); return ts.length ? ts[ts.length - 1].textContent : null; });
        out.timer0 = await tick();
        await p.waitForTimeout(2200);
        out.timer1 = await tick();
        // a hover on the summary reveals the fork button riding its line
        await lineAfter(p, cfg.summary.slice(0, 30)).scrollIntoViewIfNeeded();
        await p.locator("#content .turn-assistant", { hasText: cfg.summary.slice(0, 30) }).first().hover();
        await painted(p);
        out.forkHover = await p.evaluate((t) => {
          const prose = Array.from(document.querySelectorAll("#content .turn-assistant")).find((n) => (n.textContent || "").includes(t));
          const line = prose && prose.nextElementSibling;
          const b = line && line.querySelector(".fork-spot .msg-fork");
          return { onLine: !!b, inProse: !!(prose && prose.querySelector(".fork-spot")), opacity: b ? parseFloat(getComputedStyle(b).opacity) : null };
        }, cfg.summary.slice(0, 30));
      }
      // the summary's line opened: its calls in place, the fork spot below them, shown by a hover on the line's last call
      await lineAfter(p, cfg.summary.slice(0, 30)).locator(".toolgroup-line").click();
      await p.waitForFunction(() => !!document.querySelector("#content .tg-child"), null, { timeout: 10000 });
      await painted(p);
      if (tname === "dark") {
        out.mixedEOpen = await rows(p);
        out.openSpot = await p.evaluate((t) => {
          const prose = Array.from(document.querySelectorAll("#content .turn-assistant")).find((n) => (n.textContent || "").includes(t));
          const head = prose && prose.nextElementSibling;
          let last = head; for (let n = head && head.nextElementSibling; n && n.classList.contains("tg-child"); n = n.nextElementSibling) last = n;
          return { inHead: !!(head && head.querySelector(".fork-spot")), inLast: !!(last && last !== head && last.querySelector(".fork-spot")),
                   lastIsCall: !!(last && last.classList.contains("tg-last")) };
        }, cfg.summary.slice(0, 30));
      }
      await shot(p, dir4, "opened-E-" + tname, cfg.xPrompt5, "#content .tg-last");
    }
    await p.close();
  }
}
out.errors = errors;
fs.writeFileSync(cfg.resultFile, JSON.stringify(out));   // a file, not stdout: the result outgrows one pipe write
fs.writeSync(1, "RESULT:ok\n");
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
                       "tests": SID_TESTS, "xPrompt": X_PROMPT_1, "xReplyStart": X_REPLY_6.replace("`", "")[:30], "xPrompt4": X_PROMPT_4,
                       "shots3": os.environ.get("NARRATION_SHOTS_REALISTIC3", ""), "question": X_QUESTION,
                       "shots4": os.environ.get("NARRATION_SHOTS_REALISTIC4", ""), "summary": X_SUMMARY.replace("`", ""),
                       "xPrompt5": X_PROMPT_5, "xPrompt7": X_PROMPT_7,
                       "resultFile": os.path.join(self.lab, "result.json"),
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
        with open(os.path.join(self.lab, "result.json")) as f:
            return json.load(f)

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
        allrows = [x for x in r["mixedC2"] if x["kind"] != "other"]
        cut = next(k for k, x in enumerate(allrows) if x["kind"] == "user" and X_PROMPT_4[:20] in x["text"])
        rows = allrows[:cut]
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
        # a turn's last prose is its reply wherever it sits: the summary before a cleanup tool stays a row, never a caption
        summary = [x for x in allrows if x["kind"] == "assistant" and x["text"].startswith(shown(X_SUMMARY)[:30])]
        self.assertEqual(len(summary), 1, "the summary before the cleanup tool is its own row: %r" % allrows[cut:])
        # a caption cut from a note with a code block holds to two lines too (the height cap)
        code = next(x for x in allrows if x["kind"] == "toolgroup" and x["caption"].startswith("Here is the query"))
        self.assertLessEqual(code["capLines"], 2, "a code block in a caption is cut at two lines: %r" % code)
        # opened by a click on its CAPTION: every tool and note in order, the long note whole
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

    def test_look_e_keeps_every_prose_row_whole_and_folds_the_tools_after_each_into_its_trailing_line(self):
        r = self.result()
        if self.before:
            self.skipTest("a before-the-change checkout: screenshots only, the assertions describe the change")
        self.assertEqual(r["errors"], [], "no page errors")
        key = lambda t: re.sub(r"[`*]", "", t).replace("\n", " ")[:25]
        recs, _marks = _realistic2("/tmp/notes-api", 0)
        said = [key(b.get("text") or b.get("thinking") or "") for rec in recs if rec["type"] == "assistant"
                for b in rec["message"]["content"] if (b.get("type") == "text" and b.get("text")) or (b.get("type") == "thinking" and b.get("thinking"))]
        rows = [x for x in r["mixedE"] if x["kind"] != "other"]
        shown = [key(x["text"]) for x in rows if x["kind"] == "assistant"]
        self.assertEqual(shown, said, "every thing the session said is a row of its own, in order: nothing folded")
        self.assertFalse(any(x["caption"] or x["capLines"] for x in rows), "no caption and no clamp in E")
        # four note/tool pairs: four notes and four tool lines under them, then the reply
        i1 = next(k for k, x in enumerate(rows) if x["kind"] == "user" and X_PROMPT_1[:20] in x["text"])
        self.assertEqual([("prose" if x["kind"] == "assistant" else "line" if x["attached"] else x["kind"]) for x in rows[i1 + 1:i1 + 10]],
                         ["prose", "line"] * 4 + ["prose"], "turn one: %r" % rows[i1:i1 + 10])
        self.assertEqual(rows[i1 + 9]["text"][:30], X_REPLY_1.replace("`", "")[:30], "…the last of them the reply")
        # the question mid-turn is a row; ONE call after it is that call's own row, tucked under it: its label and file link
        q = next(k for k, x in enumerate(rows) if x["kind"] == "assistant" and x["text"].startswith(X_QUESTION[:30]))
        one = rows[q + 1]
        self.assertTrue(one["attached"] and one["kind"] == "tool", "the call after the question is its row: %r" % one)
        self.assertTrue(one["tool"].startswith("Edited") and "routes/search.ts" in one["file"], "its own label and link: %r" % one)
        # the summary before the cleanup tools: a row, the two calls its line
        sm = next(k for k, x in enumerate(rows) if x["kind"] == "assistant" and key(x["text"]) == key(X_SUMMARY))
        self.assertTrue(rows[sm + 1]["attached"] and rows[sm + 1]["line"].startswith("▸ Ran 2 commands"), "the cleanup: %r" % rows[sm + 1])
        # the stretch before a message sent mid-turn keeps its "worked" line, under the tool line it ends on
        for mid in (X_MID_2, X_MID_3):
            m = next(k for k, x in enumerate(rows) if x["kind"] == "user" and mid in x["text"])
            self.assertTrue(rows[m - 1]["attached"] and rows[m - 1]["foot"], "before %r: %r" % (mid, rows[m - 1]))
        # a fork spot below a response sits under its tool line, never between the response and its line, and never twice
        for k in range(len(rows) - 1):
            if rows[k]["kind"] == "assistant" and rows[k + 1]["attached"]:
                self.assertFalse(rows[k]["fork"], "no fork row between a response and its tool line: %r" % rows[k])
        self.assertTrue(rows[sm + 1]["fork"], "the summary's spot rides its line")
        # …and a hover on the response shows it, as on every other spot
        fh = r["forkHover"]
        self.assertEqual((fh["onLine"], fh["inProse"]), (True, False), "one spot, on the line: %r" % fh)
        self.assertGreater(fh["opacity"], 0.5, "the hover reveals the button on the line: %r" % fh)
        # the line opened: its calls in place under its head, and the spot below the last of them, not in the head
        self.assertEqual(r["openSpot"], {"inHead": False, "inLast": True, "lastIsCall": True}, "the open line's spot: %r" % r["openSpot"])
        op = [x for x in r["mixedEOpen"] if x["kind"] != "other"]
        s2 = next(k for k, x in enumerate(op) if x["kind"] == "assistant" and key(x["text"]) == key(X_SUMMARY))
        self.assertTrue(op[s2 + 1]["attached"] and not op[s2 + 1]["head"], "the open line's head: %r" % op[s2 + 1])
        self.assertEqual([(x["kind"], x["child"]) for x in op[s2 + 2:s2 + 4]], [("tool", True)] * 2, "its calls, in place")
        # the tools' states, as A's rows say them
        i7 = next(k for k, x in enumerate(rows) if x["kind"] == "user" and X_PROMPT_7[:20] in x["text"])
        failed, line, running = rows[i7 + 2], rows[i7 + 4], rows[i7 + 6]
        self.assertTrue(failed["attached"] and failed["err"] and failed["dotShown"], "a failed call: its row, red, its dot shown: %r" % failed)
        self.assertTrue(failed["tool"].startswith("Ran the migration") and "error" in failed["tool"], "…labelled, and saying error: %r" % failed)
        self.assertTrue(line["attached"] and "1 failed" in line["line"] and line["err"] and line["dotShown"],
                        "a line of three calls with one failure says so, red, its dot shown: %r" % line)
        self.assertTrue(running["attached"] and running["working"] and running["dotShown"] and "running" in running["tool"],
                        "a call still running: the working dot and running…: %r" % running)
        self.assertRegex(r["timer0"] or "", r"\d", "its elapsed time shows")
        self.assertNotEqual(r["timer0"], r["timer1"], "…and ticks: %r -> %r" % (r["timer0"], r["timer1"]))
        # the turn still runs, so its tip spot waits for the turn to end: no spot under its newest note
        self.assertFalse(any(x["fork"] for x in rows[i7:]), "no tip spot while the turn runs: %r" % rows[i7:])

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
