// A short highlight's sentence, read by the REAL chat bundle (render.ts) in headless Chromium from the rendered message the
// selection was made in (the user 2026-10-14, who approved the wording): a session frame renders a reply through md(), a
// real Range selects a few words of it, and what the page posts is read off a recording acquireVsCodeApi: the message
// box's send (the quote chip seeded by selectionchange, composed by quoteReplyBody) and a comment's create (the key's box,
// its frame's `context`). A whole sentence selected goes out as it always did. Skips with a stated reason when no
// playwright browser is installed (CI installs none; md-sanitize-chat-fragment-browser.test.ts is the precedent).
// Synthetic prose only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { chatBody, ATTACH_TITLE_WEB } from "../../vscode-extension/src/page-skeleton";

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
const REPLY = ["The chart reads well now. We should revisit the **placement** of the legend before shipping the deck. "
  + "The colours are fine.", "", "- Move the legend below the chart, and keep the legend's font small.",
  "- Rerun the build."].join("\n");

async function inChat(t: any, body: (page: any, errors: string[]) => Promise<void>): Promise<void> {
  if (!pw) { t.skip("playwright is not installed under vscode-extension; the browser leg needs it (CI installs no browsers)"); return; }
  let browser: any;
  try { browser = await pw.chromium.launch(); }
  catch (e) { t.skip("no playwright browser on this machine; the browser leg needs one (CI installs none): " + String((e as Error).message).split("\n")[0]); return; }
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
    await page.evaluate(([sid, uuid, md]: [string, string, string]) => {
      window.postMessage({ type: "session", id: sid, name: "web", cwd: "/tmp/TESTHOST/notes-api", status: { state: "idle", sinceEpoch: null },
                           events: [{ kind: "assistant", md, uuid }] }, "*");
    }, [SID, UUID, REPLY] as [string, string, string]);
    await page.waitForSelector("#content .turn-assistant .md li", { state: "attached", timeout: 10000 });
    await body(page, errors);
  } finally {
    await browser.close();
  }
}

// select `words` (its `nth` occurrence in the rendered message's text, which may span elements) with a real Range, as a drag
// would leave it, then let selectionchange run
async function select(page: any, words: string, nth = 0): Promise<void> {
  const ok = await page.evaluate(([w, n]: [string, number]) => {
    const body = document.querySelector("#content .turn-assistant .md") as HTMLElement;
    const nodes: Text[] = [];
    const walk = document.createTreeWalker(body, NodeFilter.SHOW_TEXT);
    for (let x = walk.nextNode(); x; x = walk.nextNode()) nodes.push(x as Text);
    const all = nodes.map((x) => x.data).join("");
    let at = -1;
    for (let i = 0; i <= n; i++) at = all.indexOf(w, at + 1);
    if (at < 0) return false;
    const point = (off: number): [Text, number] => {
      for (const x of nodes) { if (off <= x.data.length) return [x, off]; off -= x.data.length; }
      return [nodes[nodes.length - 1], nodes[nodes.length - 1].data.length];
    };
    const r = document.createRange();
    r.setStart(...point(at));
    r.setEnd(...point(at + w.length));
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(r);
    return true;
  }, [words, nth] as [string, number]);
  assert.ok(ok, "premise: the reply shows " + JSON.stringify(words));
  // selectionchange seeded the quote chip: its label is the words
  await page.waitForFunction((w: string) => Array.from(document.querySelectorAll("#composer-chips .composer-chip-label"))
    .some((n) => n.textContent === w), words, { timeout: 5000 });
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
