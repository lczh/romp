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
import { createRequire } from "node:module";
import { quoteContext, codeLineContext, QUOTE_CONTEXT_CAP, QUOTE_WORDS_MAX } from "./quote-context";
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
const requireCjs = createRequire(path.join(process.cwd(), "package.json"));

test("render.ts reads what goes out from the selection's own range in its message, for the message box and a thread's box", () => {
  const fn = RENDER.split("function selectionQuoteView(")[1].split("\n}")[0];
  assert.match(fn, /closest\("\.md"\)/, "the rendered message the range starts in");
  assert.match(fn, /body\.contains\(r\.endContainer\)/, "and ends in");
  assert.match(fn, /closest\("pre"\)[\s\S]*codeBlockLines\(pre, r\)[\s\S]*codeLineContext\(/, "a code block: its rows, a code line");
  assert.match(fn, /closest\("code"\)[\s\S]*codeLineContext\(code\.textContent/, "inline code: its own text");
  assert.match(fn, /blockQuoteText\(scope, r\)[\s\S]*quoteContext\(t\.text, t\.start, t\.end, words\)/, "prose: its block, as it reads");
  assert.match(RENDER, /const QUOTE_SKIP = "button, select, input, textarea, svg, \.code-copy";/, "never a control's text");
  const seeder = RENDER.split("function seedTranscriptQuote(")[1].split("\n}")[0];
  assert.match(seeder, /mkQuoteCitation\(quote, uuid, undefined, liveQuoteView\(quote\)\)/);
  const thread = RENDER.split("function seedThreadQuote(")[1].split("\n}")[0];
  assert.match(thread, /mkQuoteCitation\(quote, uuid, undefined, liveQuoteView\(quote\)\)/);
  // the chip keeps the 4,000-character quote cap for the lines it shows too
  assert.match(RENDER, /view\?\.shown \? \{ shown: view\.shown\.slice\(0, QUOTE_CAP\) \}/);
});

test("a comment carries what the gesture read off the selection, or what its chip kept", () => {
  const aim = RENDER.split("function commentAim(")[1].split("\n}")[0];
  assert.match(aim, /const view = selectionQuoteView\(r, q\.text\);/);
  assert.match(RENDER, /function chipCommentTarget\(id: string \| null\): \{ sid: string; uuid: string; text: string; view\?: QuoteView \} \| "none" \| "session" \{/);
  assert.match(RENDER, /openCommentComposer\(aim\.sid, aim\.uuid, aim\.text, aim\.x, aim\.y, aim\.at, false, aim\.view\);/);
  const open = RENDER.split("function openCommentComposer(")[1].split("\n}")[0];
  assert.match(open, /context: view\?\.ctx \|\| null, shown: view\?\.shown \|\| null/);
  assert.doesNotMatch(open, /quoteContext\(prose/, "never the turn's joined prose, whose code rows and soft line breaks read wrong");
});

// ── review of #229 (2026-10-14): code, the cap, abbreviations, the joins, the chip's identity ───────────────────────────

test("a word in a line of code: that line, never a sentence and never bold", () => {
  const line = "    x = refill(cache)";
  assert.equal(codeLineContext(line, ...at(line, "refill"), "refill"), "x = refill(cache)");
  const twin = "cache = refill(cache)";
  assert.equal(codeLineContext(twin, ...at(twin, "cache", 5)), "cache = refill(cache)", "no ** in code: it is an operator");
  assert.equal(codeLineContext(line, ...at(line, "x = refill(cache)")), null, "the whole line: the words say it all");
  assert.equal(codeLineContext("a\nb", 0, 1), null, "one line only");
  const long = "result = " + Array.from({ length: 80 }, (_, i) => "arg" + i).join(", ") + ", refill(cache), " + Array.from({ length: 80 }, (_, i) => "tail" + i).join(", ");
  const c = codeLineContext(long, ...at(long, "refill"))!;
  assert.ok(c.length <= QUOTE_CONTEXT_CAP + 2 && c.includes("refill(cache)") && c.startsWith("\u2026") && c.endsWith("\u2026"), c);
});

test("only a few words are named: a long selection, with or without a full stop, goes out as before", () => {
  const p = "word ".repeat(3000) + "end";                   // no full stop anywhere
  assert.equal(quoteContext(p, 0, 14850), null, "a 14,850-character selection has no sentence context");
  assert.equal(quoteContext(p, 0, QUOTE_WORDS_MAX + 5), null, "nor does one just over the few-words bound");
  assert.ok(quoteContext(p, 100, 120), "a few words of it do, cut to the window");
  assert.ok(quoteContext(p, 100, 120)!.length <= QUOTE_CONTEXT_CAP + 6, "the window, two cuts and a twin's bold marks");
  // the lead-in never repeats a long quote, whatever a cite carries
  const big = { quote: "x".repeat(500), ctx: "\u2026" + "x".repeat(500) + " y\u2026" };
  assert.equal(quoteLeadIn(big), "Replying to this part of the conversation:");
  assert.equal(quoteReplyBody([big], ""), "Replying to this part of the conversation:\n> " + big.quote);
});

test("a code passage over several lines is quoted as its lines, line breaks and all, under the usual lead-in", () => {
  const cite = { quote: "def refill(cache):    return cache.load()", shown: "def refill(cache):\n    return cache.load()" };
  assert.equal(quoteReplyBody([cite], "Why load?"),
    "Replying to this part of the conversation:\n> def refill(cache):\n>     return cache.load()\n\nWhy load?");
  assert.equal(quoteReplyBody([{ ...cite, src: "src/a.py:1-2" }], ""), "Replying to this highlighted code (src/a.py:1-2):\n> " + cite.quote,
    "an editor quote shows what it selected");
});

test("abbreviations, initials and a trailing-off ellipsis do not end a sentence", () => {
  for (const [p, w] of [
    ["Ask Dr. Shaw about the placement of the legend first.", "placement"],
    ["Ship it to the U.S. office after the placement review.", "placement"],
    ["Fix the legend, the axes, etc. and then the placement too.", "placement"],
    ["We tried \u2026 then the placement moved again.", "placement"],
    ["We tried... then the placement moved again.", "placement"],
    ["The fix from J. Smith covers the placement as well.", "placement"],
  ] as Array<[string, string]>) assert.equal(quoteContext(p, ...at(p, w)), p, p);
  const two = "Fix the axes etc. Then the placement goes last.";
  assert.equal(quoteContext(two, ...at(two, "placement")), "\u2026Then the placement goes last.", "etc. before a capital ends one");
});

test("the joins: a ? or ! keeps its mark and a spaced cut; an ellipsis is never doubled", () => {
  const q = "Should the placement change? I think so.";
  assert.equal(quoteContext(q, ...at(q, "placement")), "Should the placement change? \u2026");
  const e = "First line. Then the placement drifts\u2026 Later we fix it.";
  assert.equal(quoteContext(e, ...at(e, "placement")), "\u2026Then the placement drifts\u2026");
  const d = "First line. ...and the placement drifts. Done.";
  assert.equal(quoteContext(d, ...at(d, "placement")), "...and the placement drifts\u2026", "an opening ellipsis takes no second one");
  for (const t of [q, e, d]) assert.ok(!/\u2026\u2026|\.\.\.\u2026|\u2026\.\.\.|\?\u2026/.test(quoteContext(t, ...at(t, "placement"))!), t);
});

test("a chip is its words and what goes out for them: the same word from two sentences is two chips", () => {
  const fn = RENDER.slice(RENDER.indexOf("function citeKey("), RENDER.indexOf("\n}\n", RENDER.indexOf("function citeKey(")) + 3);
  const citeKey = new Function(requireCjs("esbuild").transformSync(fn, { loader: "ts" }).code + "\nreturn citeKey;")() as (c: object) => string;
  const a = { title: "placement", quote: "placement", ctx: "\u2026We should revisit the placement of the legend\u2026" };
  const b = { title: "placement", quote: "placement", ctx: "Keep the placement as it is." };
  assert.notEqual(citeKey(a), citeKey(b));
  assert.equal(citeKey(a), citeKey({ ...a }));
  assert.notEqual(citeKey({ title: "x", quote: "x" }), citeKey({ title: "x", quote: "x", shown: "x" }));
  // a refused un-send puts back the chip the box held and takes away the one it armed (render.ts's own lines, run)
  const block = RENDER.split("if (stash.armedCites.length || stash.armedFiles.length) {")[1].split("const beforeF")[0];
  const run = new Function("stash", "composerCitations", "m", "citeKey", block + "\nreturn composerCitations.get(m.id);");
  const held = new Map([["s", [b]]]);   // the box after the un-send: the rescinded message's chip in place of the user's
  const out = run({ cites: [a], armedCites: [citeKey(b)] }, held, { id: "s" }, citeKey) as Array<{ ctx: string }>;
  assert.deepEqual(out.map((c) => c.ctx), [a.ctx], "the user's own chip is back, the armed one gone");
  assert.match(RENDER, /for \(const c of composerCitations\.get\(sidQ\) \|\| \[\]\) armedCites\.push\(citeKey\(c\)\);/, "the un-send arms its chips by the same key");
});
