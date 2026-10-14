// A short highlight names its words and quotes the sentence they sit in (the user 2026-10-14, who approved the wording):
//   Replying to "placement" in this part of the conversation:
//   > …We should revisit the placement of the legend before shipping the deck…
// EXECUTED: the sentence finder (quote-context.ts quoteContext) over synthetic rendered prose, and the outgoing body
// (staged-messages.ts quoteReplyBody, the one function the live send, the staged release and the chip's preview share)
// and its inverse (queued-rescind.ts splitQuoteReplyBody) on cites that carry the context. The page's wiring (the
// selection's range read into the message's text and offsets) is pinned on render.ts below. Synthetic prose only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { quoteContext, QUOTE_CONTEXT_CAP } from "./quote-context";
import { quoteLeadIn, quoteReplyBody, stagedRunBody, stagedPosts } from "./staged-messages";
import { splitQuoteReplyBody } from "./queued-rescind";

const P = "The chart reads well now. We should revisit the placement of the legend before shipping the deck. The colours are fine.";
const at = (prose: string, words: string, from = 0): [number, number] => {
  const i = prose.indexOf(words, from);
  assert.ok(i >= 0, "premise: the words are in the prose: " + words);
  return [i, i + words.length];
};
const CTX = "…We should revisit the placement of the legend before shipping the deck…";

test("a word inside a sentence: the sentence it sits in, cut where the line goes on", () => {
  assert.equal(quoteContext(P, ...at(P, "placement"), "placement"), CTX);
  // the first sentence on its line: no cut before it; the last: none after, and its period stays
  assert.equal(quoteContext(P, ...at(P, "reads well")), "The chart reads well now…");
  assert.equal(quoteContext(P, ...at(P, "colours")), "…The colours are fine.");
  // alone on its line (a paragraph, a list item): no cut on either side
  const line = "Intro paragraph.\nMove the legend below the plot area\nOutro.";
  assert.equal(quoteContext(line, ...at(line, "legend")), "Move the legend below the plot area");
});

test("the words twice in what is quoted: the highlighted one is in bold, any case", () => {
  const p = "Move the legend below the chart, and keep the legend's font small.";
  assert.equal(quoteContext(p, ...at(p, "legend", 20)), "Move the legend below the chart, and keep the **legend**'s font small.");
  assert.equal(quoteContext(p, ...at(p, "legend")), "Move the **legend** below the chart, and keep the legend's font small.");
  const q = "Legend first, then the legend colours.";
  assert.equal(quoteContext(q, ...at(q, "legend")), "Legend first, then the **legend** colours.", "a capitalised twin counts");
  assert.equal(quoteContext(P, ...at(P, "legend")), "…We should revisit the placement of the legend before shipping the deck…",
    "once is plain");
});

test("a whole sentence or more, or across a line: null, and the quote goes out as it always did", () => {
  const s = "We should revisit the placement of the legend before shipping the deck.";
  assert.equal(quoteContext(P, ...at(P, s)), null, "the sentence with its stop");
  assert.equal(quoteContext(P, ...at(P, s.slice(0, -1))), null, "the sentence without its stop");
  assert.equal(quoteContext(P, ...at(P, "deck. The colours")), null, "into the next sentence");
  assert.equal(quoteContext(P, 0, P.length), null, "the whole message");
  const two = "First line here\nsecond line here";
  assert.equal(quoteContext(two, ...at(two, "here\nsecond")), null, "over a line break");
  const head = "Placement\nThe rest.";
  assert.equal(quoteContext(head, ...at(head, "Placement")), null, "a heading highlighted whole");
});

test("the words must be the quote the selection gave; offsets out of range are nothing", () => {
  assert.equal(quoteContext(P, ...at(P, "placement"), "legend"), null, "a quote that is not what sits there");
  assert.equal(quoteContext(P, ...at(P, "placement"), "  placement \n"), CTX, "edge whitespace is not a difference");
  assert.equal(quoteContext(P, 5, 5), null);
  assert.equal(quoteContext(P, -1, 4), null);
  assert.equal(quoteContext(P, 0, P.length + 1), null);
  assert.equal(quoteContext(P, 1.5, 4), null);
});

test("an abbreviation's stop does not end the sentence; a stop inside a word or number never does", () => {
  const p = "Pick a short unit, e.g. seconds, for the timeout in config.py before 2.5 ships.";
  assert.equal(quoteContext(p, ...at(p, "seconds")), p);
  assert.equal(quoteContext(p, ...at(p, "config.py")), p);
});

test("a long sentence is cut to a window around the words, at word boundaries", () => {
  const filler = Array.from({ length: 60 }, (_, i) => "word" + i).join(" ");
  const p = "Opening. " + filler + " then the placement of the legend " + filler + " ends here. Closing.";
  const c = quoteContext(p, ...at(p, "placement"))!;
  assert.ok(c.startsWith("…word") && c.endsWith("…"), c);
  assert.ok(c.includes(" then the placement of the legend "), c);
  assert.ok(c.length <= QUOTE_CONTEXT_CAP + 2, "about the cap: " + c.length);
  assert.match(c, /^\u2026word\d+ /, "cut at a word's start");
  assert.match(c, / word\d+\u2026$/, "and after a word's end");
});

// ── the outgoing body ─────────────────────────────────────────────────────────────────────────────────────────────────

const SHORT = { quote: "placement", ctx: CTX };
const WHOLE = { quote: "We should revisit the placement of the legend before shipping the deck." };

test("a short highlight's section names the words and quotes the sentence: the approved wording", () => {
  assert.equal(quoteLeadIn(SHORT), "Replying to \"placement\" in this part of the conversation:");
  assert.equal(quoteReplyBody([SHORT], "Where would it go instead?"),
    "Replying to \"placement\" in this part of the conversation:\n"
    + "> …We should revisit the placement of the legend before shipping the deck…\n\n"
    + "Where would it go instead?");
  // the words' own whitespace is one line in the lead-in
  assert.equal(quoteLeadIn({ quote: "the\n placement", ctx: "x the placement y" }), "Replying to \"the placement\" in this part of the conversation:");
});

test("a full-sentence highlight, and the file and editor flavours, are unchanged", () => {
  assert.equal(quoteReplyBody([WHOLE], "ok"),
    "Replying to this part of the conversation:\n> We should revisit the placement of the legend before shipping the deck.\n\nok");
  assert.equal(quoteLeadIn({ quote: "notes", src: "src/app.py:1-2", ctx: "def notes():" }), "Replying to this highlighted code (src/app.py:1-2):");
  assert.equal(quoteReplyBody([{ quote: "notes", kind: "file", path: "src/app.py", lines: [1, 1], src: "src/app.py:1", ctx: "def notes():" }], ""),
    "Replying to this part of src/app.py (line 1):\n> notes", "a file passage quotes what was selected");
});

test("staged runs compose several quotes, short and whole, in stage order", () => {
  const items = [{ text: "Where would it go instead?", cites: [SHORT] }, { text: "Agreed.", cites: [WHOLE] }];
  const body = stagedRunBody(items, { text: "Thanks." });
  assert.equal(body, quoteReplyBody([SHORT], "Where would it go instead?") + "\n\n" + quoteReplyBody([WHOLE], "Agreed.") + "\n\nThanks.");
  assert.ok(body.startsWith("Replying to \"placement\" in this part of the conversation:\n> …We should"), body);
  assert.deepEqual(stagedPosts(items, { text: "Thanks." }).map((p) => p.text), [body], "one message for the run");
  assert.equal(quoteReplyBody([SHORT, WHOLE], "both"),
    quoteReplyBody([SHORT], "") + "\n\n" + quoteReplyBody([WHOLE], "") + "\n\nboth", "stacked chips on one message");
});

test("a rescinded message gives the short quote back as its chip, words and sentence, byte for byte", () => {
  const body = quoteReplyBody([SHORT, WHOLE], "Where would it go instead?");
  const back = splitQuoteReplyBody(body);
  assert.deepEqual(back, { cites: [{ quote: "placement", ctx: CTX }, { quote: WHOLE.quote }], text: "Where would it go instead?" });
  assert.equal(quoteReplyBody(back.cites, back.text), body);
});

test("the lead-in speaks as the person: no word only the tracking system knows", () => {
  const lead = quoteLeadIn(SHORT).toLowerCase();
  for (const w of ["romp", "card", "board", "goal", "cleared", "dismissal", "status check", "nudge"])
    assert.ok(!lead.includes(w), w);
});

// ── the page's wiring, pinned ─────────────────────────────────────────────────────────────────────────────────────────

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");

test("render.ts reads the context from the selection's own range in its message, for the message box and a thread's box", () => {
  const fn = RENDER.split("function rangeQuoteContext(")[1].split("\n}")[0];
  assert.match(fn, /closest\("\.md"\)/, "the rendered message the range starts in");
  assert.match(fn, /body\.contains\(r\.endContainer\)/, "and ends in");
  assert.match(fn, /return quoteContext\(prose, start, start \+ r\.toString\(\)\.length, words\);/);
  const seeder = RENDER.split("function seedTranscriptQuote(")[1].split("\n}")[0];
  assert.match(seeder, /mkQuoteCitation\(quote, uuid, undefined, liveQuoteContext\(quote\)\)/);
  const thread = RENDER.split("function seedThreadQuote(")[1].split("\n}")[0];
  assert.match(thread, /mkQuoteCitation\(quote, uuid, undefined, liveQuoteContext\(quote\)\)/);
  assert.match(RENDER, /function mkQuoteCitation\(quote: string, uuid: string \| null, src\?: string, ctx\?: string \| null\): Citation \{/);
});

test("a comment carries the context from the page: the selection's offsets in the message, else the chip's", () => {
  const open = RENDER.split("function openCommentComposer(")[1].split("\n}")[0];
  assert.match(open, /const context = at \? \(prose \? quoteContext\(prose, at\.start, at\.end, exact\) : null\) : \(ctx \|\| null\);/);
  assert.match(RENDER, /function chipCommentTarget\(id: string \| null\): \{ sid: string; uuid: string; text: string; ctx\?: string \} \| "none" \| "session" \{/);
  assert.match(RENDER, /openCommentComposer\(aim\.sid, aim\.uuid, aim\.text, aim\.x, aim\.y, aim\.at, false, aim\.ctx\);/);
});
