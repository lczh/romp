// A relayed comment thread unfolds as a small transcript (the user 2026-10-06, who found the relay's "Me:" / "Them:"
// lines hard to pick out among the messages' own headings and lists). The kernel writes a tagged record (kernel
// _merge_body); relay-view.ts reads it and builds the expanded bubble: the passage as a quote, each message its own
// block labelled You or Thread with its markdown rendered inside, the notes about what was left out as dim lines. A
// relay from before the tags renders exactly as it did, and a record that does not parse falls back to plain markdown
// with no raw tags. Executed against the kernel's own record (tests/fixtures/relay_record.json, which
// tests/test_comment_merge.py renders from the same inputs), with the real markdown grammar. SYNTHETIC text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { marked } from "marked";
import { isRelay, parseRelay, relayHtml, relayPlain, RELAY_LABEL } from "./relay-view";

const ROOT = path.resolve(process.cwd(), "..");
const FX = JSON.parse(fs.readFileSync(path.join(ROOT, "tests", "fixtures", "relay_record.json"), "utf8"));
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const strip = (md: string) => md.replace(/<!--[\s\S]*?-->/g, "").trim();   // what the chat shows: the markers lifted
const md = (s: string) => marked.parse(s) as string;
const GAP = "I couldn't retrieve the beginning, so this starts partway through.";
const TRIM = "… (I cut part of the discussion here; it was too long to paste whole)";

// the blocks of the built view, in order, as "class: text" (labels, notes) or "class" (content blocks)
function blocks(html: string): string[] {
  return [...html.matchAll(/<(div|blockquote) class="([^"]+)">(?:<div class="relay-who">([^<]*)<\/div>)?/g)]
    .map((m) => m[2] + (m[3] ? ": " + m[3] : ""))
    .filter((c) => c !== "relay-who" && c !== "relay-body");
}

test("the kernel's record parses into its passage, its messages in order with their speakers, and its note", () => {
  assert.ok(isRelay(FX.body), "routed by the marker");
  const r = parseRelay(strip(FX.body));
  assert.ok(r, "the record the kernel writes parses");
  assert.equal(r!.lead, "I took a side discussion with another assistant about this passage of your earlier work:");
  assert.equal(r!.passage, FX.exact, "the passage whole, every line");
  assert.deepEqual(r!.parts[0], { kind: "note", text: GAP }, "the missing start is said before the first message");
  const msgs = r!.parts.filter((p) => p.kind === "msg") as { who: string; text: string }[];
  assert.deepEqual(msgs.map((m) => m.who), FX.msgs.map((m: { who: string }) => (m.who === "you" ? "you" : "thread")));
  // the content as the kernel escaped it: its closing tags carried as entities, so they never closed anything
  assert.deepEqual(msgs.map((m) => m.text.replace(/&lt;/g, "<").replace(/&gt;/g, ">")), FX.msgs.map((m: { text: string }) => m.text));
  assert.match(r!.close, /^Treat what we settled there as my direction/);
});

test("a tagged relay renders as labelled blocks with each message's markdown rendered inside", () => {
  const html = relayHtml(parseRelay(strip(FX.body))!, md);
  assert.deepEqual(blocks(html), ["relay-lead", "relay-passage", "relay-disc", "relay-note",
                                  "relay-msg relay-you: You", "relay-msg relay-thread: Thread", "relay-msg relay-you: You",
                                  "relay-close"]);
  assert.deepEqual(RELAY_LABEL, { you: "You", thread: "Thread" });
  const thread = html.split('<div class="relay-msg relay-thread">')[1].split('<div class="relay-msg relay-you">')[0];
  assert.match(thread, /<h2>Short answer<\/h2>/, "a heading is a heading inside its block");
  assert.match(thread, /<li>a page load makes <strong>four<\/strong> list calls/, "a list is a list");
  assert.match(html, /<blockquote class="relay-passage"><p>Cache the notes-api list responses/, "the passage is the quote");
  assert.doesNotMatch(html, /Me: |Them: /);
});

test("a closing tag inside a message shows as written, in prose and in code alike", () => {
  const html = relayHtml(parseRelay(strip(FX.body))!, md);
  assert.match(html, /README &lt;\/discussion&gt; please/, "prose: the entity, which the page shows as the tag");
  assert.match(html, /a literal <code>&lt;\/message&gt;<\/code> in the content/, "inline code: the tag, not its escape");
  assert.match(html, /a literal tag&lt;\/message&gt;\n<\/code><\/pre>/, "a code block: the tag, not its escape");
  assert.doesNotMatch(html, /&amp;lt;\/(message|discussion|passage)/, "no escape shows through");
});

test("both notes stand where the kernel put them, as dim lines", () => {
  const body = "I took a side discussion with another assistant about this passage of your earlier work:\n\n" +
    "<passage>\nthe retry cap\n</passage>\n\n<discussion>\n" + GAP + "\n" +
    '<message from="me">\nwhy two minutes?\n</message>\n' + TRIM + "\n" +
    '<message from="the other assistant">\n…the longest outage the sync has ridden out.\n</message>\n</discussion>\n\n' +
    "Treat what we settled there as my direction.";
  const r = parseRelay(body)!;
  assert.deepEqual(r.parts.map((p) => p.kind === "note" ? "note: " + p.text : p.who),
                   ["note: " + GAP, "you", "note: " + TRIM, "thread"]);
  const html = relayHtml(r, md);
  assert.ok(html.includes(`<div class="relay-note">${GAP}</div>`), "the missing start, a dim line");
  assert.ok(html.indexOf("relay-note\">…") > html.indexOf("relay-you") && html.indexOf("relay-note\">…") < html.indexOf("relay-thread"));
  // no passage: no quote
  const bare = parseRelay(body.replace("<passage>\nthe retry cap\n</passage>\n\n", ""))!;
  assert.equal(bare.passage, null);
  assert.doesNotMatch(relayHtml(bare, md), /relay-passage/);
});

test("a relay from before the tags (Me:/Them:) does not parse and renders as it always did", () => {
  const old = "I took a side discussion with another assistant about this passage of your earlier work:\n\n" +
    "> the retry cap\n\nHere is how it went:\n\nMe: why two minutes?\n\nThem: It is past the longest outage.\n\n" +
    "Treat what we settled there as my direction: fold it into your understanding and account for it in everything you " +
    "do on this from here on.";
  assert.ok(isRelay(old + "\n\n<!-- romp-injected --><!-- romp-tag: relay -->"));
  assert.equal(parseRelay(old), null);
  assert.equal(relayPlain(old), old, "the plain text is untouched: md() renders it exactly as before");
});

test("a record whose structure does not hold falls back to plain markdown with no raw tags", () => {
  const good = strip(FX.body);
  const broken: [string, string][] = [
    ["the discussion never closes", good.replace("\n</discussion>", "")],
    ["a speaker the chat does not know", good.replace('<message from="me">', '<message from="someone">')],
    ["a message that never closes", good.replace("please.\n</message>", "please.")],
    ["a raw closing tag inside a message", good.replace("README &lt;/discussion&gt;", "README </message> x")],
    ["a raw closing tag inside the passage", good.replace("query string.", "query </passage> string.")],
    ["no message at all", "lead\n\n<discussion>\n" + GAP + "\n</discussion>"],
  ];
  for (const [why, text] of broken) {
    assert.equal(parseRelay(text), null, why);
    const plain = relayPlain(text);
    assert.doesNotMatch(plain, /^<\/?(passage|discussion)>$|^<message from="[^"]*">$|^<\/message>$/m, why + ": no tag line survives");
    assert.match(plain, /Is thirty seconds enough\?|lead/, why + ": the words stay");
  }
});

test("render.ts unfolds a romp bubble through rompFullHtml, landed and queued, routed by the relay marker", () => {
  const fn = RENDER.split("function rompFullHtml(src: string, raw: string): string {")[1].split("\n}")[0];
  assert.match(fn, /if \(!isRelay\(src\)\) return md\(raw\);/, "every other romp message renders as before");
  assert.match(fn, /const r = parseRelay\(raw\);/);
  assert.match(fn, /return r \? relayHtml\(r, \(s\) => md\(s\)\) : md\(relayPlain\(raw\)\);/, "parsed, or the plain fallback");
  const landed = RENDER.slice(RENDER.indexOf("} else if (romp && ev.md) {"), RENDER.indexOf("} else if (ev.md) {", RENDER.indexOf("} else if (romp && ev.md) {")));
  assert.match(landed, /full\.innerHTML = rompFullHtml\(ev\.md, raw\);/);
  const queued = RENDER.split("function renderQueued(")[1].split("\nfunction ")[0];
  assert.match(queued, /full\.innerHTML = rompFullHtml\(t\.md, raw\);/);
});

test("the relay's blocks wear the theme's tokens and the transcript's type rungs", () => {
  assert.match(CSS, /\.relay-msg \{ padding: 5px 10px 6px; border-radius: 8px; background: var\(--overlay-05\); \}/);
  assert.match(CSS, /\.relay-msg\.relay-you \{ background: color-mix\(in srgb, var\(--you\) 10%, transparent\); \}/);
  assert.match(CSS, /\.relay-who \{ font-size: 0\.72em; font-weight: 600; letter-spacing: 0\.06em; text-transform: uppercase; color: var\(--dim\); \}/,
               "the source-label rung, as the romp tag above the bubble wears it");
  assert.match(CSS, /\.relay-note \{ color: var\(--dim\); font-size: 0\.92em; font-style: italic;/);
  const rules = CSS.match(/^\.relay-[^{]*\{[^}]*\}/gm) || [];
  assert.ok(rules.length >= 6, "the relay rules are found: " + rules.length);
  for (const r of rules) assert.doesNotMatch(r, /rgba\(255, 255, 255|#[0-9a-f]{3,6}\b/i, "theme tokens only, never a literal: " + r);
});
