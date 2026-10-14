// A short highlight's sentence, read by the REAL chat bundle (render.ts) in headless Chromium from the rendered message the
// selection was made in (the user 2026-10-14, who approved the wording): a session frame renders a reply through md(), a
// real Range selects a few words of it, and what the page posts is read off a recording acquireVsCodeApi: the message
// box's send (the quote chip seeded by selectionchange, composed by quoteReplyBody) and a comment's create (the key's box,
// its frame's `context`). A whole sentence selected goes out as it always did. Skips with a stated reason when no
// playwright browser is installed, and fails with it where CI requires the leg (browser-leg.ts). Synthetic prose only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { chatBody, ATTACH_TITLE_WEB } from "../../vscode-extension/src/page-skeleton";
import { capQuote } from "./quote-context";
import { launchLeg } from "./browser-leg";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");
const STYLES = fs.readFileSync(path.join(UI, "styles.css"), "utf8").replace(/^@import [^\n]*\n/m, "");
const BUILD = { bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
  nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent" };

// the chat page with a fake acquireVsCodeApi that keeps every post, so the test reads exactly what the page sent
const chatHtml = () => `<!DOCTYPE html><html lang=en><head><meta charset=utf-8><style>${STYLES}</style></head><body>
${chatBody(ATTACH_TITLE_WEB)}
<script>window.__posted=[];window.acquireVsCodeApi=function(){return{postMessage:function(m){window.__posted.push(m)},getState:function(){return null},setState:function(){}}};</script>
<script src=/dist/render.js></script></body></html>`;

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

const SID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee";
const UUID = "11111111-2222-4333-8444-555555555555";
const LONG = Array.from({ length: 1000 }, (_, i) => "w" + i).join(" ");   // a paragraph with no full stop, ~5,900 characters
const REPLY = ["The chart reads well now. We should revisit the **placement** of the legend before shipping the deck. "
  + "The colours are fine.", "", "- Move the legend below the chart, and keep the legend's font small.",
  "- Rerun the build.", "",
  "The axes look fine. We should revisit the", "spacing of the labels. Then rerun it.", "",   // a source line break mid-sentence
  "Call `refill(cache)` before the load.", "",
  "Set `max_retries` to 5 before the next run.", "",
  "```python", "def refill(cache):", "    return cache.load()", "x = refill(cache)", "```", "",
  "<details><summary>More detail</summary>Text inside the closed fold.</details>", "",
  "The paragraph after the fold.", "", LONG, "", "The reply closes here."].join("\n");
// a tool row and a user turn between two replies: a selection across them meets the tool row's label, its collapsed input
// and output, and the user turn's chrome
const UUID_TOOL = "11111111-2222-4333-8444-888888888888", UUID_USER = "11111111-2222-4333-8444-777777777777";
const UUID_NEXT = "11111111-2222-4333-8444-666666666666";
const AROUND = [
  { kind: "tool", name: "Bash", desc: "List the files", input: "ls -la", output: "total 0", isError: false, uuid: UUID_TOOL },
  { kind: "user", md: "Thanks, carry on.", uuid: UUID_USER },
  { kind: "assistant", md: "The next reply starts here. It has two sentences.", uuid: UUID_NEXT },
];

async function inChat(t: any, body: (page: any, errors: string[]) => Promise<void>, events?: object[]): Promise<void> {
  const browser = await launchLeg(t, pw, "chromium");
  if (!browser) return;
  const errors: string[] = [];
  try {
    const renderJs = requireCjs("esbuild").buildSync({ ...BUILD, entryPoints: [path.join(UI, "render.ts")] }).outputFiles[0].text;
    const page = await browser.newPage({ viewport: { width: 1000, height: 700 } });
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      if (u.hostname !== "romp.test") return route.fulfill({ status: 404, body: "" });
      if (u.pathname === "/chat") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: chatHtml() });
      if (u.pathname === "/dist/render.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: renderJs });
      return route.fulfill({ status: 404, body: "" });
    });
    await page.goto("http://romp.test/chat");
    await page.evaluate(([sid, evs]: [string, object[]]) => {
      window.postMessage({ type: "session", id: sid, name: "web", cwd: "/tmp/TESTHOST/notes-api", status: { state: "idle", sinceEpoch: null },
                           events: evs }, "*");
    }, [SID, events || [{ kind: "assistant", md: REPLY, uuid: UUID }]] as [string, object[]]);
    await page.waitForSelector("#content .turn-assistant .md " + (events ? "p" : "li"), { state: "attached", timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

// the [start, end) offsets of `words` (its `nth` occurrence), or of `words` through the end of `to`, in the text of `scope`,
// as a Range (which may span elements and turns); null when the text is not there
const RANGE_OF = `(spec) => {
  const root = document.querySelector(spec.scope);
  if (!root) return null;
  const nodes = [];
  const walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let x = walk.nextNode(); x; x = walk.nextNode()) nodes.push(x);
  const all = nodes.map((x) => x.data).join("");
  let at = -1;
  for (let i = 0; i <= spec.nth; i++) { at = all.indexOf(spec.words, at + 1); if (at < 0) return null; }
  let end = at + spec.words.length;
  if (spec.to) { const t = all.indexOf(spec.to, at); if (t < 0) return null; end = t + spec.to.length; }
  const point = (off) => {
    for (const x of nodes) { if (off <= x.data.length) return [x, off]; off -= x.data.length; }
    return [nodes[nodes.length - 1], nodes[nodes.length - 1].data.length];
  };
  const r = document.createRange();
  r.setStart(...point(at));
  r.setEnd(...point(end));
  return r;
}`;
type Pick = { to?: string; scope?: string; label?: string };

// select `words` (its `nth` occurrence in the rendered message's text, which may span elements; through `to` when given)
// with a real Range, as a drag would leave it, then let selectionchange run
async function select(page: any, words: string, nth = 0, pick: Pick = {}): Promise<void> {
  const ok = await page.evaluate(([fn, spec]: [string, object]) => {
    const r = (0, eval)(fn)(spec) as Range | null;
    if (!r) return false;
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(r);
    return true;
  }, [RANGE_OF, { words, nth, to: pick.to, scope: pick.scope || "#content .turn-assistant .md" }] as [string, object]);
  assert.ok(ok, "premise: the reply shows " + JSON.stringify(words));
  await chipShows(page, pick.label ?? words);
}

// the selection a real double-click (2) or triple-click (3) on the middle of `words` makes
async function clickSelect(page: any, words: string, clicks: 2 | 3, label: string, nth = 0): Promise<void> {
  const at = await page.evaluate(([fn, spec]: [string, object]) => {
    const r = (0, eval)(fn)(spec) as Range | null;
    if (!r) return null;
    (r.startContainer.parentElement as HTMLElement).scrollIntoView({ block: "center" });
    const b = Array.from(r.getClientRects()).find((x) => x.width > 0)!;
    return { x: b.left + b.width / 2, y: b.top + b.height / 2 };
  }, [RANGE_OF, { words, nth, scope: "#content .turn-assistant .md" }] as [string, object]);
  assert.ok(at, "premise: the reply shows " + JSON.stringify(words));
  await page.mouse.click(at.x, at.y, { clickCount: clicks });
  await chipShows(page, label);
}

// selectionchange seeded the quote chip: its label is the passage's first 140 characters
async function chipShows(page: any, label: string): Promise<void> {
  await page.waitForFunction((w: string) => Array.from(document.querySelectorAll("#composer-chips .composer-chip-label"))
    .some((n) => n.textContent === w), label.replace(/\s+/g, " ").trim().slice(0, 140), { timeout: 5000 });
}

// a comment on the live selection, through the comment key's box: the create frame the page posted
async function commentWith(page: any, text: string): Promise<any> {
  await page.evaluate(() => { (window as any).__posted.length = 0; });
  await page.keyboard.press("Control+/");
  await page.waitForSelector("#cmt-pop .cmt-input", { timeout: 5000 });
  await page.focus("#cmt-pop .cmt-input");
  await page.keyboard.type(text);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => (window as any).__posted.some((m: any) => m && m.type === "commentCreate"), null, { timeout: 5000 });
  const create = await page.evaluate(() => (window as any).__posted.find((m: any) => m && m.type === "commentCreate"));
  await page.keyboard.press("Escape");
  return create;
}

async function sendWith(page: any, text: string): Promise<string> {
  await page.evaluate(() => { (window as any).__posted.length = 0; });
  await page.focus("#composer-input");
  await page.keyboard.type(text);
  await page.keyboard.press("Enter");
  await page.waitForFunction(() => (window as any).__posted.some((m: any) => m && m.type === "sendMessage"), null, { timeout: 5000 });
  return page.evaluate(() => (window as any).__posted.find((m: any) => m && m.type === "sendMessage").text);
}

test("the message box: a few words go out named, with their sentence; a twin is marked; a whole sentence as before", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await select(page, "placement");
    assert.equal(await sendWith(page, "Where would it go instead?"),
      "Replying to \"placement\" in this part of the conversation:\n"
      + "> …We should revisit the placement of the legend before shipping the deck…\n\n"
      + "Where would it go instead?", "the word sits in <strong>: the sentence is read across the elements");
    await select(page, "legend", 2);
    assert.equal(await sendWith(page, "Which one?"),
      "Replying to \"legend\" in this part of the conversation:\n"
      + "> Move the legend below the chart, and keep the **legend**'s font small.\n\nWhich one?", "a list item is its own line");
    await select(page, "We should revisit the placement of the legend before shipping the deck.");
    assert.equal(await sendWith(page, "Agreed."),
      "Replying to this part of the conversation:\n> We should revisit the placement of the legend before shipping the deck.\n\nAgreed.");
    assert.deepEqual(errors, [], "no page errors");
  });
});

// ── review of #229 (2026-10-14): code is not prose, the cap holds, a source line break is a space ─────────────────────────
test("code: a word in a code block names its line and never the Copy button; a word in inline code its sentence; several lines keep their breaks", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await select(page, "refill", 2);                    // x = refill(cache), the block's third line
    assert.equal(await sendWith(page, "Why here?"),
      "Replying to \"refill\" in this part of the conversation:\n> x = refill(cache)\n\nWhy here?");
    await select(page, "refill", 0);                    // the inline code span in the prose: the prose's sentence (review of #231)
    assert.equal(await sendWith(page, "And here?"),
      "Replying to \"refill\" in this part of the conversation:\n> Call refill(cache) before the load.\n\nAnd here?");
    await select(page, "def refill(cache):    return cache.load()");   // two rows: the rendered rows join with no break
    assert.equal(await sendWith(page, "Why load?"),
      "Replying to this part of the conversation:\n> def refill(cache):\n>     return cache.load()\n\nWhy load?");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a long selection with no full stop goes out as before, under the 4,000-character cap and never named", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    const words = LONG.slice(0, LONG.indexOf(" w900"));
    await select(page, words);
    const body = await sendWith(page, "Thoughts?");
    // cut at the cap, and the cut says so (review of #231): a quote cut with no mark reads as the whole passage
    assert.equal(body, "Replying to this part of the conversation:\n> " + words.slice(0, 3999).trimEnd() + "\u2026\n\nThoughts?");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a source line break inside a paragraph is a space: the sentence is whole, its cut before it kept", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await select(page, "spacing");
    assert.equal(await sendWith(page, "How much?"),
      "Replying to \"spacing\" in this part of the conversation:\n> \u2026We should revisit the spacing of the labels\u2026\n\nHow much?");
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a comment on a word in a code block names its line: the review's refill example", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await select(page, "refill", 2);
    await page.evaluate(() => { (window as any).__posted.length = 0; });
    await page.keyboard.press("Control+/");
    await page.waitForSelector("#cmt-pop .cmt-input", { timeout: 5000 });
    await page.focus("#cmt-pop .cmt-input");
    await page.keyboard.type("Why call it here?");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => (window as any).__posted.some((m: any) => m && m.type === "commentCreate"), null, { timeout: 5000 });
    const create = await page.evaluate(() => (window as any).__posted.find((m: any) => m && m.type === "commentCreate"));
    assert.deepEqual([create.exact, create.context, create.shown], ["refill", "x = refill(cache)", undefined]);
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a comment on a few words carries their sentence in its create frame", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await select(page, "colours");
    await page.evaluate(() => { (window as any).__posted.length = 0; });
    await page.keyboard.press("Control+/");
    await page.waitForSelector("#cmt-pop .cmt-input", { timeout: 5000 });
    await page.focus("#cmt-pop .cmt-input");
    await page.keyboard.type("Which palette?");
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => (window as any).__posted.some((m: any) => m && m.type === "commentCreate"), null, { timeout: 5000 });
    const create = await page.evaluate(() => (window as any).__posted.find((m: any) => m && m.type === "commentCreate"));
    assert.deepEqual([create.uuid, create.exact, create.text, create.context], [UUID, "colours", "Which palette?", "…The colours are fine."]);
    assert.deepEqual(errors, [], "no page errors");
  });
});

// ── review of #231 (2026-10-14): inline code is prose, the quote is the content's text, the walk is bounded, comments are
// capped ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
test("an inline code identifier double-clicked in a sentence is named over that sentence, in the message box and a comment", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    await clickSelect(page, "max_retries", 2, "max_retries");
    assert.equal(await sendWith(page, "Why five?"),
      "Replying to \"max_retries\" in this part of the conversation:\n> Set max_retries to 5 before the next run.\n\nWhy five?",
      "the identifier alone said nothing about where it was");
    await clickSelect(page, "max_retries", 2, "max_retries");
    const create = await commentWith(page, "Why five?");
    assert.deepEqual([create.exact, create.context, create.shown], ["max_retries", "Set max_retries to 5 before the next run.", undefined]);
    assert.deepEqual(errors, [], "no page errors");
  });
});

test("a quote is the content's text: a code block's Copy button, a closed fold's contents and a tool row's labels stay out", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    // a triple-click on a code block's last line takes the Copy button beside it into the range
    await clickSelect(page, "x = refill(cache)", 3, "x = refill(cache)");
    assert.equal(await sendWith(page, "Why here?"), "Replying to this part of the conversation:\n> x = refill(cache)\n\nWhy here?");
    // a few words dragged on into the button: the words, without it (the range's own text, which a comment's highlight is
    // found by, holds the button's, so they are not named over their line)
    await select(page, "refill(cache)", 2, { to: "Copy", label: "refill(cache)" });
    assert.equal(await sendWith(page, "And here?"), "Replying to this part of the conversation:\n> refill(cache)\n\nAnd here?");
    // from a code block through a closed fold: its summary shows, its contents do not
    await select(page, "x = refill(cache)", 0, { to: "The paragraph after the fold.", label: "x = refill(cache) More detail The paragraph after the fold." });
    assert.equal(await sendWith(page, "And this?"),
      "Replying to this part of the conversation:\n> x = refill(cache)\n> \n> More detail\n> \n> The paragraph after the fold.\n\nAnd this?");
    // across a collapsed tool row and a user's turn into the next reply
    const label = "The reply closes here. Thanks, carry on. The next reply starts";
    await select(page, "The reply closes here.", 0, { to: "The next reply starts", scope: "#content", label });
    const sent = await sendWith(page, "Go on.");
    assert.equal(sent, "Replying to this part of the conversation:\n> The reply closes here.\n> \n> Thanks, carry on.\n> \n> The next reply starts\n\nGo on.");
    // a comment on it: the passage it anchors on is the range's own text (the highlight is found by it), what its opening
    // message quotes is the content's
    await select(page, "The reply closes here.", 0, { to: "The next reply starts", scope: "#content", label });
    const create = await commentWith(page, "Why?");
    assert.match(create.exact, /List the files/, "premise: the anchor is the range's own text, labels and all");
    assert.equal(create.shown, "The reply closes here.\n\nThanks, carry on.\n\nThe next reply starts");
    assert.deepEqual(errors, [], "no page errors");
  }, [{ kind: "assistant", md: REPLY, uuid: UUID }, ...AROUND]);
});

test("a comment on a passage longer than the cap: what it quotes is cut there with the cut marked, what it anchors on is whole", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    // from a code block (its Copy button in the range) through a closed fold into the long paragraph
    const head = "x = refill(cache)\n\nMore detail\n\nThe paragraph after the fold.\n\n";
    const words = LONG.slice(0, LONG.indexOf(" w900") + 5);
    await select(page, "x = refill(cache)", 0, { to: " w900", label: head + words });
    const create = await commentWith(page, "Thoughts?");
    assert.equal(create.shown, capQuote(head + words), "the opening message quotes it within the cap");
    assert.ok(create.shown.length <= 4000 && create.shown.endsWith("\u2026"), "premise: cut, and marked");
    assert.ok(create.exact.length > 4000 && create.exact.includes("Copy"), "the passage it anchors on is the range's own text, whole");
    assert.deepEqual(errors, [], "no page errors");
  });
});

// the work a selectionchange does over a code block of 4,000 lines, counted by the DOM calls the page makes while it runs
// (a perf pin by work, never by the clock, which a shared runner does not keep): a few words, two lines near the end, and
// the whole block. Before review of #231 every update walked every text node of the block (about 100 ms a drag step).
test("a selection in a code block of 4,000 lines costs work bounded by the selection and the cap, not by the block", { timeout: 120000 }, async (t) => {
  const BIG = ["Before the block.", "", "```python",
    ...Array.from({ length: 4000 }, (_, i) => `value_${i} = compute(alpha_${i}, beta, gamma) + other(${i})`), "```"].join("\n");
  await inChat(t, async (page, errors) => {
    await page.waitForSelector("#content .md pre .ct", { state: "attached", timeout: 20000 });
    const work = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll("#content .md pre .ct"));
      const text = (e: Element) => document.createTreeWalker(e, NodeFilter.SHOW_TEXT).nextNode() as Text;
      let n = 0;
      const count = <T extends object>(proto: T, names: string[]) => {
        for (const k of names) {
          const d = Object.getOwnPropertyDescriptor(proto, k)!;
          if (d.get) Object.defineProperty(proto, k, { ...d, get(this: unknown) { n++; return d.get!.call(this); } });
          else Object.defineProperty(proto, k, { ...d, value: function (this: unknown, ...a: unknown[]) { n++; return (d.value as Function).apply(this, a); } });
        }
      };
      count(TreeWalker.prototype, ["nextNode", "previousNode"]);
      count(Range.prototype, ["intersectsNode", "comparePoint", "toString", "selectNodeContents"]);
      count(Node.prototype, ["textContent", "firstChild", "nextSibling", "parentNode", "childNodes"]);
      count(Element.prototype, ["matches", "closest", "querySelectorAll"]);
      count(window, ["getComputedStyle"]);
      const cost = (a: Node, ao: number, b: Node, bo: number) => {
        const r = document.createRange();
        r.setStart(a, ao);
        r.setEnd(b, bo);
        const sel = window.getSelection()!;
        sel.removeAllRanges();
        sel.addRange(r);
        n = 0;
        document.dispatchEvent(new Event("selectionchange"));
        return n;
      };
      return {
        word: cost(text(rows[3500]), 0, text(rows[3500]), 9),
        twoLines: cost(text(rows[3998]), 0, text(rows[3999]), 9),
        whole: cost(text(rows[0]), 0, text(rows[3999]), 9),
      };
    });
    t.diagnostic("work per selectionchange: " + JSON.stringify(work));
    // bounds with room: measured 32, 39 and 1,572 calls after review of #231, and 4,017, 28,018 and 28,018 before it (a
    // walk of the block)
    assert.ok(work.word < 400, "a word: " + JSON.stringify(work));
    assert.ok(work.twoLines < 400, "two lines: " + JSON.stringify(work));
    assert.ok(work.whole < 4000, "the whole block, bounded by the cap: " + JSON.stringify(work));
    assert.deepEqual(errors, [], "no page errors");
  }, [{ kind: "assistant", md: BIG, uuid: UUID }]);
});


// ── review of #234 (2026-10-14): a tool group's line and a diff's gutters are chrome too ─────────────────────────────────
// two tool calls fold into one group line ("▸ Ran a command, edited a file +1 -1"), whose caret and totals went out as
// "> ▸ +1 -1"; an opened edit's rows went out as "1010a = 1", the line numbers glued to the code (a <pre>'s rows are not
// style-read, so the gutters' user-select: none was never seen)
const U = (n: number) => "11111111-2222-4333-8444-" + String(n).padStart(12, "0");
const GROUPED = [
  { kind: "assistant", md: "The first reply ends here.", uuid: U(1) },
  { kind: "tool", name: "Bash", desc: "List the files", input: "ls -la", output: "total 0", isError: false, uuid: U(2) },
  { kind: "tool", name: "Edit", file: "notes.py", input: "edit", uuid: U(3),
    diffRows: [{ oldNo: 10, newNo: 10, sign: " ", text: "a = 1" }, { oldNo: 11, newNo: null, sign: "-", text: "b = 2" },
               { oldNo: null, newNo: 11, sign: "+", text: "b = 3" }] },
  { kind: "assistant", md: "The second reply starts here. It goes on.", uuid: U(4) },
];

test("a tool group's caret and totals and a diff's line numbers stay out of a quote; a diff row keeps its sign", { timeout: 90000 }, async (t) => {
  await inChat(t, async (page, errors) => {
    const across = { to: "The second reply starts", scope: "#content", label: "The first reply ends here. The second reply starts" };
    await select(page, "The first reply ends here.", 0, across);
    assert.equal(await sendWith(page, "Go on."),
      "Replying to this part of the conversation:\n> The first reply ends here.\n> \n> The second reply starts\n\nGo on.");
    await select(page, "The first reply ends here.", 0, across);
    const create = await commentWith(page, "Why?");
    assert.match(create.exact, /\u25b8 Ran a command, edited a file \+1 -1/, "premise: the range's own text holds the group's line");
    assert.equal(create.shown, "The first reply ends here.\n\nThe second reply starts");
    // the group opened and the edit's diff with it
    await page.locator("#content .toolgroup-line").first().click();
    await page.locator("#content .tool-fold-toggle", { hasText: "+1 -1" }).click();
    await page.waitForSelector("#content .diff-fold .diff-row", { state: "visible", timeout: 5000 });
    await select(page, "a = 1", 0, { to: "b = 3", scope: "#content", label: "a = 1 -b = 2 +b = 3" });
    assert.equal(await sendWith(page, "Why three?"),
      "Replying to this part of the conversation:\n> a = 1\n> -b = 2\n> +b = 3\n\nWhy three?");
    await select(page, "The first reply ends here.", 0, { ...across, label: "The first reply ends here. a = 1 -b = 2 +b = 3 The second reply starts" });
    assert.equal(await sendWith(page, "And all of it?"),
      "Replying to this part of the conversation:\n> The first reply ends here.\n> \n> a = 1\n> -b = 2\n> +b = 3\n> \n"
      + "> The second reply starts\n\nAnd all of it?", "the open group's caret, the rows' heads and the closed fold stay out");
    assert.deepEqual(errors, [], "no page errors");
  }, GROUPED);
});
