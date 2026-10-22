// A markdown picture opens the lightbox, and nothing a message carries widens its bubble (2026-10-20, the user reading
// on a phone: a picture ran off the right edge, and only the pictures romp drew from a file path zoomed). Source pins over
// preview.ts, render.ts and styles.css; the served lab (tests/test_md_image_lightbox_browser.py) clicks, steps and
// measures the real page at a desktop and a phone width in both themes, and the predicate is RUN below on stand-ins.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { opensInLightbox } from "./md-images";

const requireCjs = createRequire(__filename);
const UI = path.resolve(process.cwd(), "..", "ui", "webview");
const PREVIEW = fs.readFileSync(path.join(UI, "preview.ts"), "utf8");
const RENDER = fs.readFileSync(path.join(UI, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(UI, "styles.css"), "utf8");
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;
// the solo rule's source, RUN by the tests below: its tag lists, newlineEndsLine and mdSolo, one stretch of preview.ts
const SOLO_CODE = (() => {
  const at = PREVIEW.indexOf("const BLOCK_TAGS = "), fnAt = PREVIEW.indexOf("export function mdSolo(", at);
  return ts(PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", fnAt) + 2).replace(/^export /m, ""));
})();

test("the mark: every picture md() and userMd() render wears .md-img, stamped by the chat's post-pass and no other renderer", () => {
  const at = PREVIEW.indexOf("export function mdImgPostPass(root: ParentNode): void {");
  const body = PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at));
  assert.match(body, /img\.classList\.add\("md-img"\);/);
  assert.doesNotMatch(body, /if \(!mdImgFailed\.size\) return;/, "the mark is stamped whether or not any URL failed");
  assert.equal((RENDER.match(/mdImgPostPass\(clean\);/g) || []).length, 2, "md() and userMd() run it; the hover card's previewMdClean and the file viewer do not");
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("function previewMdClean("), RENDER.indexOf("function showFilePreview(")), /mdImgPostPass/);
});

// A fake of the sanitized DOM the post-pass walks: elements with a tag, attributes, classes, a parent and children (text
// nodes included), their siblings and first and last child, enough for mdSolo and the picture checks. Built by
// `el(tag, attrs, ...children)`; a string child is text. READS counts every child and sibling the walks read (a child read
// by index, a copy of the children included), the work the 1,000-picture pin below holds to a few steps a picture.
type FNode = { nodeType: number; textContent?: string; tagName?: string; parentElement?: FNode | null; childNodes?: FNode[] } & Record<string, unknown>;
let READS = 0;
function el(tag: string, attrs: Record<string, string> = {}, ...kids: (FNode | string)[]): FNode {
  const cls = new Set<string>(), at = new Map(Object.entries(attrs));
  const list: FNode[] = [];
  const childNodes = new Proxy(list, { get: (t, k, r) => { if (typeof k === "string" && /^\d+$/.test(k)) READS++; return Reflect.get(t, k, r); } });
  const node: FNode = { nodeType: 1, tagName: tag.toUpperCase(), parentElement: null, childNodes, cls, dataset: {} as Record<string, string>,
    get firstChild() { READS++; return list[0] || null; }, get lastChild() { READS++; return list[list.length - 1] || null; },
    get src() { return at.get("src") || ""; },
    getAttribute: (a: string) => (at.has(a) ? at.get(a) : null), hasAttribute: (a: string) => at.has(a), removeAttribute: (a: string) => { at.delete(a); },
    classList: { add: (c: string) => cls.add(c), remove: (c: string) => cls.delete(c), contains: (c: string) => cls.has(c) },
    closest: (q: string) => { for (let n: FNode | null | undefined = node; n; n = n.parentElement) if (q.split(/\s*,\s*/).includes((n.tagName || "").toLowerCase())) return n; return null; },
    querySelectorAll: (q: string) => { const out: FNode[] = []; const walk = (n: FNode) => { for (const k of n.childNodes || []) { if (k.nodeType === 1) { if ((k.tagName || "").toLowerCase() === q) out.push(k); walk(k); } } }; walk(node); return out; } };
  for (const k of kids) {
    const kid: FNode = typeof k === "string" ? { nodeType: 3, textContent: k } : k;
    if (kid.nodeType === 1) kid.parentElement = node;
    const i = list.push(kid) - 1;
    Object.defineProperty(kid, "nextSibling", { configurable: true, get: () => { READS++; return list[i + 1] || null; } });
    Object.defineProperty(kid, "previousSibling", { configurable: true, get: () => { READS++; return list[i - 1] || null; } });
  }
  return node;
}

test("the post-pass, RUN: a URL that loaded picture-sized wears the zoom from its next render, the box only alone in its paragraph", () => {
  // the transcript rebuilds a streaming turn's pictures on every push; a box that waited for each fresh element's load
  // would step the turn's height (inline to block) on every push, so the remembered verdict is stamped at render. The
  // block and the frame go only to a picture that stands alone in its paragraph (2026-10-21, the review of the shared
  // box), and the height judged is the one it will SHOW at (its attributes through the remembered natural size)
  const fn = (name: string) => { const at = PREVIEW.indexOf(name); return ts(PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at) + 2).replace(/^export /, "")); };
  const line = (start: string) => { const at = PREVIEW.indexOf(start); return ts(PREVIEW.slice(at, PREVIEW.indexOf("\n", PREVIEW.indexOf("};", at)) + 1)); };
  const code = line("const attrPx = ") + SOLO_CODE + fn("function parkMdImg(") + fn("export function mdImgPostPass(")
    + fn("export function shownHeightOf(");
  let videos = 0;
  const run = (new Function("mdImgFailed", "mdImgDims", "opensInLightbox", "ZOOM_MIN_H", "mdVideoPostPass", code + "\nreturn mdImgPostPass;") as
    (f: Set<string>, d: Map<string, { w: number; h: number }>, ok: (u: string) => boolean, min: number, v: () => void) => (root: unknown) => void);
  const H = "https://img.example.test/";
  const img = (src: string, attrs: Record<string, string> = {}) => el("img", { src, ...attrs });
  const big = img(H + "trace.png"), fresh = img(H + "new.png"), badge = img(H + "badge.svg"), linked = img(H + "trace.png"),
    folded = img(H + "trace.png"), gone = img(H + "gone.png"), page = img("javascript:alert(1)"), midline = img(H + "trace.png"),
    squashed = img(H + "trace.png", { height: "20" }), narrow = img(H + "wide.png", { width: "100" }), pair1 = img(H + "trace.png"), pair2 = img(H + "trace.png");
  const root = el("body", {},
    el("p", {}, big), el("p", {}, fresh), el("p", {}, "Build ", badge, " is green"), el("p", {}, el("a", { href: H }, linked)),
    el("details", {}, el("summary", {}, folded)), el("p", {}, gone), el("p", {}, page),
    el("p", {}, "The trace ", midline, " shows the slow request."), el("p", {}, squashed), el("p", {}, narrow),
    el("p", {}, "\n", pair1, " ", pair2, "\n"));
  const all = [big, fresh, badge, linked, folded, gone, page, midline, squashed, narrow, pair1, pair2];
  const dims = new Map([[H + "trace.png", { w: 400, h: 1600 }], [H + "wide.png", { w: 1800, h: 450 }], [H + "badge.svg", { w: 88, h: 20 }], [H + "gone.png", { w: 400, h: 400 }],
    ["javascript:alert(1)", { w: 400, h: 400 }]]);
  run(new Set([H + "gone.png"]), dims, opensInLightbox, 32, () => { videos++; })(root);
  const has = (i: FNode, c: string) => (i.cls as Set<string>).has(c);
  for (const i of all) assert.ok(has(i, "md-img"), "every picture wears the mark");
  assert.equal(videos, 1, "and the videos' pass runs on the same root");
  assert.ok(has(big, "md-img-zoom") && has(big, "md-solo"), "a URL that loaded picture-sized, alone in its paragraph: the zoom and the box at once");
  assert.ok(has(midline, "md-img-zoom") && !has(midline, "md-solo"), "the same picture in the middle of a sentence: it zooms, but stays on its line (no box)");
  assert.ok(!has(pair1, "md-solo") && !has(pair2, "md-solo"), "two pictures side by side: neither stands alone");
  assert.ok(!has(squashed, "md-img-zoom"), "an <img height=\"20\"> of a large picture shows 20px tall: a glyph, no zoom");
  assert.ok(!has(narrow, "md-img-zoom") && has(narrow, "md-solo"), "an 1800x450 picture given width=\"100\" shows 25px tall: no zoom (alone, but no box without it)");
  assert.ok(!has(fresh, "md-img-zoom"), "a URL never loaded: the load decides");
  assert.ok(!has(badge, "md-img-zoom"), "a URL whose natural size is glyph-sized is no picture");
  assert.ok(!has(linked, "md-img-zoom"), "a linked picture is the link's: no box, no zoom cursor");
  assert.ok(!has(folded, "md-img-zoom"), "a summary's picture is the summary's");
  assert.ok(has(gone, "md-img-failed") && !has(gone, "md-img-zoom") && gone.src === "", "a parked URL is a caption, not a picture");
  assert.ok(!has(page, "md-img-zoom"), "a scheme the lightbox never opens is no picture to box");
  // the park clears the box when a boxed picture's URL fails after it was stamped
  const late = img(H + "late.png"); (late.classList as { add: (c: string) => void }).add("md-img"); (late.classList as { add: (c: string) => void }).add("md-img-zoom");
  (new Function("mdImgFailed", fn("function parkMdImg(") + "\nreturn parkMdImg;")(new Set()) as (i: unknown, u: string) => void)(late, H + "late.png");
  assert.ok(has(late, "md-img-failed") && !has(late, "md-img-zoom"), "a picture parked after it loaded loses the box");
});

test("the solo rule, RUN: a picture or a video stands alone when nothing else is on its LINE, so a caption on the next line keeps the box", () => {
  // the review of #343 (2026-10-21): an agent's `![plot](url)` with `*Figure 1: …*` on the next line is ONE paragraph,
  // joined by a soft break (assistant markdown, breaks:false: a "\n" in a text node) or a <br> (the user's, breaks:true),
  // and the picture lost its box, inline with the caption beside its bottom edge. A line ends at either break, and, in a
  // parent that holds blocks, at a block
  const solo = new Function(SOLO_CODE + "\nreturn mdSolo;")() as (e: unknown) => boolean;
  const H = "https://img.example.test/";
  const pic = (n: string) => el("img", { src: H + n + ".png" });
  const cap = () => el("em", {}, "Figure 1: requests per hour across the run.");
  const br = () => el("br");
  const check = (want: boolean, what: string, build: (a: FNode, b: FNode) => FNode) => {
    const a = pic("a"), b = pic("b");
    build(a, b);
    assert.equal(solo(a), want, what);
  };
  // stands alone
  check(true, "a picture alone in its paragraph", (a) => el("p", {}, a));
  check(true, "…with whitespace and newlines around it", (a) => el("p", {}, "\n  ", a, "\n"));
  check(true, "a picture, a soft break, a caption (assistant markdown)", (a) => el("p", {}, a, "\n", cap()));
  check(true, "…the caption as plain text after the break", (a) => el("p", {}, a, "\nFigure 1: requests per hour."));
  check(true, "a picture, a <br>, a caption (the user's markdown, breaks:true)", (a) => el("p", {}, a, br(), "\n", cap()));
  check(true, "…with no newline after the <br>", (a) => el("p", {}, a, br(), cap()));
  check(true, "a lead-in line, then the picture on a line of its own", (a) => el("p", {}, "The trace, top to bottom:\n", a));
  check(true, "…after a <br>", (a) => el("p", {}, "The trace, top to bottom:", br(), a));
  check(true, "a comment beside it shows nothing", (a) => el("p", {}, { nodeType: 8, textContent: "x" }, a));
  check(true, "a list item's lead-in line, then the picture", (a) => el("li", {}, "The plot:\n", a));
  check(true, "a block parent: the picture between blocks", (a) => el("div", {}, el("p", {}, "before"), a, el("ul", {}, el("li", {}, "after"))));
  // two pictures, each on its own line, each stands alone; side by side, neither does
  for (const [what, mk] of [["a soft break", () => "\n"], ["a <br>", br]] as [string, () => FNode | string][]) {
    const a = pic("a"), b = pic("b");
    el("p", {}, a, mk(), b, mk(), cap());
    assert.ok(solo(a) && solo(b), "two pictures separated by " + what + ", a caption under the second: each stands alone");
  }
  { const a = pic("a"), b = pic("b"); el("p", {}, "\n", a, " ", b, "\n"); assert.ok(!solo(a) && !solo(b), "two pictures side by side: neither stands alone"); }
  // inline: anything shown on its own line
  check(false, "text before it on its line", (a) => el("p", {}, "The trace ", a));
  check(false, "…even with a caption after a break", (a) => el("p", {}, "The trace ", a, "\n", cap()));
  check(false, "…a lead-in line earlier does not help", (a) => el("p", {}, "Results:\nThe trace ", a));
  check(false, "text after it on its line: the picture is a word in that sentence (\"![ok](u) means passing\")", (a) => el("p", {}, a, " is the slowest request."));
  check(false, "…its line's text wrapping onto the next", (a) => el("p", {}, a, " is the slowest\nrequest of the run."));
  check(false, "an element after it on its line", (a) => el("p", {}, a, " ", el("code", {}, "GET /notes")));
  check(false, "the middle of a sentence", (a) => el("p", {}, "The trace ", a, " shows the slow request."));
  check(false, "in a list item, text before it on its line", (a) => el("li", {}, "See ", a));
  check(false, "no parent: nothing to stand in", () => el("p"));
  // the same rule for a video
  const v = el("video", { src: H + "clip.mp4" });
  el("p", {}, v, "\n", el("em", {}, "Clip 1: the signup flow."));
  assert.equal(solo(v), true, "a video with its caption on the next line stands alone");
  const w = el("video", { src: H + "clip.mp4" });
  el("p", {}, "Watch ", w, " first.");
  assert.equal(solo(w), false, "a video in a sentence does not");
});

test("where a line ends, RUN: a newline only where the author's line breaks, a bold picture in its sentence, and only the <br> that ends the picture's line marked", () => {
  // the review of #349 (2026-10-21) reproduced three leftovers of the solo rule: a README's two half-width pictures in raw
  // HTML stacked as framed blocks (every newline counted, the HTML source's own included), a bold picture mid-sentence
  // was framed (its parent <strong> held nothing else), and a hard break inside a caption was hidden as the picture's
  const solo = new Function(SOLO_CODE + "\nreturn mdSolo;")() as (e: unknown) => boolean;
  const H = "https://img.example.test/";
  const pic = (n: string) => el("img", { src: H + n + ".png" });
  const marked = (n: FNode) => (n.cls as Set<string>).has("md-solo-end");
  const half = (n: string) => el("img", { src: H + n + ".png", width: "45%" });
  const pair = (what: string, want: boolean, build: (a: FNode, b: FNode) => FNode, mk = pic) => {
    const a = mk("a"), b = mk("b");
    build(a, b);
    assert.deepEqual([solo(a), solo(b)], [want, want], what);
  };
  // the author's own HTML lays out by HTML's rules: a newline between its tags is a space, so the README's half-width
  // pictures sit side by side (a row the author tiled: the test below)
  pair("the README layout, its <p> opened and closed on lines of their own", false, (a, b) => el("p", {}, "\n", a, "\n", b, "\n"), half);
  pair("…indented under an align", false, (a, b) => el("p", { align: "center" }, "\n  ", a, "\n  ", b, "\n"), half);
  pair("…opened on a line of its own only", false, (a, b) => el("p", {}, "\n", a, "\n", b), half);
  pair("…closed on a line of its own only", false, (a, b) => el("p", {}, a, "\n", b, "\n"), half);
  pair("…a raw list item, padded the same way", false, (a, b) => el("li", {}, "\n", a, "\n", b, "\n"), half);
  pair("…in a <div>", false, (a, b) => el("div", { align: "center" }, a, "\n", b), half);
  pair("…a raw HTML block of two <img> lines, at the message's root", false, (a, b) => el("body", {}, a, "\n", b), half);
  pair("…in a table cell", false, (a, b) => el("td", {}, a, "\n", b), half);
  // …and the same layouts with no widths stack, each picture on a line of its own (the review of #353, the test below)
  pair("the README layout with no widths", true, (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  pair("…a raw HTML block of two <img> lines with no widths", true, (a, b) => el("body", {}, a, "\n", b));
  // …but a newline there still ends a picture's line when words, not another picture, open the next one (the review of
  // #351: the test below)
  { const a = pic("a"); el("body", {}, el("p", {}, "before"), "\n", a, "\nFigure 1: requests per hour.", el("p", {}, "after"));
    assert.equal(solo(a), true, "a raw picture with words on the next source line: its caption, under it"); }
  // markdown's own lines still end at its soft breaks (#349's case): marked writes paragraphs, list items and headings flush
  pair("markdown's ![a](u)\\n![b](u): each on a line of its own", true, (a, b) => el("p", {}, a, "\n", b));
  pair("…in a tight list item", true, (a, b) => el("li", {}, a, "\n", b));
  { const a = pic("a"); el("li", {}, a, "\nThe plot of the run.", el("ul", {}, el("li", {}, "a sub-point")), "\n");
    assert.equal(solo(a), true, "a tight list item ends on a newline after its nested list: a block's, no padding"); }
  { const a = pic("a"); el("h2", {}, "Results\n", a); assert.equal(solo(a), true, "a heading written on two lines (setext)"); }
  { const a = pic("a"); el("pre", {}, "line one\n", a, "\nline three"); assert.equal(solo(a), true, "a <pre>, where the page breaks at every newline"); }
  // what shows nothing, and blocks the author's HTML ends a line with
  { const a = pic("a"); el("p", {}, "\n", el("picture", {}, "\n  ", el("source", { srcset: H + "dark.png" }), "\n  ", a, "\n"), "\n");
    assert.equal(solo(a), true, "a <picture>'s sources show nothing: its picture stands alone"); }
  { const a = pic("a"); el("figure", {}, "\n", a, "\n", el("figcaption", {}, "Figure 1: requests per hour."), "\n");
    assert.equal(solo(a), true, "a <figcaption> is a block under its picture"); }
  { const a = pic("a"); el("details", {}, el("summary", {}, "The plot"), "\n", a, "\n");
    assert.equal(solo(a), true, "…and so is a <summary> above it"); }
  // an inline wrapper is part of the line it sits on: the walk climbs out of it (`The **![x](u)** result`)
  const one = (what: string, want: boolean, build: (a: FNode) => FNode) => { const a = pic("a"); build(a); assert.equal(solo(a), want, what); };
  one("a bold picture in the middle of a sentence", false, (a) => el("p", {}, "The ", el("strong", {}, a), " result is the slowest."));
  one("…bold and italic", false, (a) => el("p", {}, "The ", el("em", {}, el("strong", {}, a)), " result"));
  one("…words after it only", false, (a) => el("p", {}, el("strong", {}, a), " is the slowest."));
  one("…words before it inside the bold", false, (a) => el("p", {}, el("strong", {}, "Note: ", a)));
  one("a linked picture ending a sentence", false, (a) => el("p", {}, "The board: ", el("a", { href: H }, a)));
  one("a struck-through picture between words", false, (a) => el("p", {}, "Was ", el("del", {}, a), " before"));
  one("a bold picture alone, its caption on the next line", true, (a) => el("p", {}, el("strong", {}, a), "\n", el("em", {}, "Figure 1: requests per hour.")));
  one("…bold and italic", true, (a) => el("p", {}, el("em", {}, el("strong", {}, a)), "\nFigure 1: requests per hour."));
  one("…an italic around the picture and its caption", true, (a) => el("p", {}, el("em", {}, a, "\nFigure 1: requests per hour.")));
  // the <br> that ends the picture's line, and only it, is marked: a hard break inside the caption (two spaces, a
  // backslash or a raw <br>, all one <br>) is the caption's own line break
  { const a = pic("a"), inCap = el("br");
    el("p", {}, a, "\nFigure 1: requests per hour.", inCap, "\nSource: the nightly load run.");
    assert.ok(solo(a) && !marked(inCap), "agent markdown: the soft break ends the picture's line; the caption's own break is not the picture's"); }
  { const a = pic("a"), end = el("br"), inCap = el("br");
    el("p", {}, a, end, "\nFigure 1: requests per hour.", inCap, "\nSource: the nightly load run.");
    assert.ok(solo(a) && marked(end) && !marked(inCap), "the user's markdown: the first <br> ends the picture's line, the second is the caption's"); }
  { const a = pic("a"), end = el("br"); el("p", {}, el("em", {}, el("strong", {}, a)), end, "Figure 1: requests per hour.");
    assert.ok(solo(a) && marked(end), "a bold picture's line ends at the <br> after its wrapper"); }
  { const a = pic("a"), b = pic("b"), endA = el("br"), endB = el("br"); el("p", {}, a, endA, b, endB, "Figure 1: both panels.");
    assert.ok(solo(a) && solo(b) && marked(endA) && marked(endB), "two pictures on lines of their own: each marks its own line's end"); }
  { const a = pic("a"), lead = el("br"); el("p", {}, "The trace:", lead, a);
    assert.ok(solo(a) && !marked(lead), "the <br> before a picture ends the lead-in's line, not the picture's"); }
  { const a = pic("a"), after = el("br"); el("p", {}, "The trace ", a, after, "Figure 1: requests per hour.");
    assert.ok(!solo(a) && !marked(after), "a picture that is a word in a sentence marks nothing"); }
  { const v = el("video", { src: H + "clip.mp4" }), end = el("br"); el("p", {}, v, end, el("em", {}, "Clip 1: the signup flow."));
    assert.ok(solo(v) && marked(end), "a video's line end is marked the same way"); }
  // the review of #355 (2026-10-22): agent markdown's `![a](u)\n<br>\nCaption` left a blank line between the box and its
  // caption, since the soft break had ended the picture's line and the <br> on the next went unmarked. With only white
  // space between the picture and that <br>, it is the picture's line end too
  { const a = pic("a"), end = el("br"); el("p", {}, a, "\n", end, "\nFigure 1: requests per hour.");
    assert.ok(solo(a) && marked(end), "agent markdown, a <br> alone on the line after the picture: marked"); }
  { const a = pic("a"), end = el("br"); el("p", {}, a, "\n", end, "Figure 1: requests per hour.");
    assert.ok(solo(a) && marked(end), "…the caption right after that <br>"); }
  { const a = pic("a"), end = el("br"); el("p", {}, el("strong", {}, a), "\n", end, "\n", el("em", {}, "Figure 1: requests per hour."));
    assert.ok(solo(a) && marked(end), "…a bold picture's"); }
  { const a = pic("a"), b = pic("b"), endA = el("br"), endB = el("br"); el("p", {}, a, "\n", endA, "\n", b, "\n", endB, "\nFigure 1: both panels.");
    assert.ok(solo(a) && solo(b) && marked(endA) && marked(endB), "…two pictures, each with its <br> on the line after"); }
  { const a = pic("a"), inCap = el("br"); el("p", {}, a, "\nFigure 1: requests per hour.", inCap, "\nSource: the nightly load run.");
    assert.ok(solo(a) && !marked(inCap), "words before the <br> still make it the caption's own break (the review of #349)"); }
  { const a = pic("a"), lead = el("br"); el("p", {}, "The trace:", lead, "\n", a, "\nFigure 1: requests per hour.");
    assert.ok(solo(a) && !marked(lead), "a <br> on the line BEFORE the picture is the lead-in's"); }
  { const a = pic("a"), after = el("br"); el("pre", {}, a, "\n", after, "\nline three");
    assert.ok(solo(a) && !marked(after), "a <pre>'s newline is a line of its own: the <br> after it is the pre's"); }
  { const a = pic("a"), after = el("br"); el("p", {}, a, "\n", el("code", {}, "GET /notes"), after, "\nSource: the nightly load run.");
    assert.ok(solo(a) && !marked(after), "something shown before the <br> on the next line: the <br> is that line's"); }
});

test("1,000 raw pictures, RUN: every walk costs a few steps a picture, never the whole message", () => {
  // the review of #355 (2026-10-22): one message of 1,000 raw <img> lines took 2.28 s a render at #355 (0.48 s at live),
  // since every picture copied its parent's children to find itself (Array.from, indexOf) and rowOf walked its whole row.
  // The walks step from sibling to sibling and rowTiles stops at the first picture without a percentage width or once the
  // row passes the line. READS counts the children and siblings read: about 8,000 a picture at #355, 4 to 8 now (72 when
  // every share is 0%, the walk running until the spaces alone pass the line). Benchmark, mdSolo over the 1,000 in
  // Chromium on a parsed document (median of five): live 490 ms, #355 2,300 ms, this walk 2.2 ms (4.5 ms at 45% each)
  const solo = new Function(SOLO_CODE + "\nreturn mdSolo;")() as (e: unknown) => boolean;
  const H = "https://img.example.test/";
  for (const [what, width, cap] of [["no widths", undefined, 20], ["every one at 45%", "45%", 20], ["every one at 0%: shares that never fill the line", "0%", 100]] as [string, string | undefined, number][]) {
    const ms = Array.from({ length: 1000 }, (_, i) => el("img", { src: H + i + ".png", ...(width === undefined ? {} : { width }) }));
    el("body", {}, el("p", {}, "The runs:"), "\n", ...ms.flatMap((m) => [m, "\n"]), el("p", {}, "That is all."));
    READS = 0;
    const got = ms.map((m) => solo(m));
    assert.deepEqual(got, ms.map(() => true), what + ": each on its own line, boxed");
    assert.ok(READS < cap * ms.length, what + ": " + READS + " reads for " + ms.length + " pictures, at most " + cap + " a picture");
  }
  { // and a row that does tile still reads only its own pictures
    const ms = Array.from({ length: 1000 }, (_, i) => el("img", { src: H + i + ".png", width: "45%" }));
    el("body", {}, ...ms.flatMap((m, i) => [m, i % 2 ? "\n\n" : "\n"]));
    READS = 0;
    assert.deepEqual(ms.map((m) => solo(m)), ms.map(() => false), "500 pairs at 45%, a blank line after each: every pair side by side");
    assert.ok(READS < 20 * ms.length, "…" + READS + " reads");
  }
});

test("an author's raw HTML, RUN: a picture or a video alone on its source line ends its line there, another one on the next line included", () => {
  // the review of #351 (2026-10-21), through the real marked, DOMPurify and mdSolo: #351 read every newline in the author's
  // HTML as a space, so an agent's raw `<img src="…">` with `Figure 1: …` on the next line (a raw HTML block, loose at the
  // message's root) lost its box and the caption sat beside its bottom edge; so did a <video> block; two raw <img> lines a
  // blank line apart touched, unframed; and a padded `<p align="center">` put a picture's <em> caption beside it. A newline
  // there ends the line. #353 kept pictures and videos on consecutive lines as one row, and the review of #353 (2026-10-22)
  // found that row wrapping into an unframed stack: they end their lines too, and only a row the author tiled with
  // percentage widths stays one (the test below)
  const solo = new Function(SOLO_CODE + "\nreturn mdSolo;")() as (e: unknown) => boolean;
  const H = "https://img.example.test/";
  const marked = (n: FNode) => (n.cls as Set<string>).has("md-solo-end");
  const kinds: [string, (n: string) => FNode][] = [
    ["a picture", (n) => el("img", { src: H + n + ".png", width: "300" })],
    ["a video", (n) => el("video", { controls: "", width: "100%" }, "\n  ", el("source", { src: H + n + ".mp4", type: "video/mp4" }), "\n")],
    ["a video's still", (n) => el("span", { class: "md-video-still" }, el("video", { src: H + n + ".mp4" }))],
  ];
  for (const [kind, mk] of kinds) {
    const media = (n: string): [FNode, FNode] => { const m = mk(n); return [m, m.tagName === "SPAN" ? m.childNodes![0] : m]; };
    const one = (what: string, want: boolean, build: (a: FNode) => FNode) => { const [a, at] = media("a"); build(a); assert.equal(solo(at), want, kind + ": " + what); };
    const pair = (what: string, want: boolean, build: (a: FNode, b: FNode) => FNode) => {
      const [a, at] = media("a"), [b, bt] = media("b");
      build(a, b);
      assert.deepEqual([solo(at), solo(bt)], [want, want], kind + ": " + what);
    };
    // 1. alone on its source line, a caption on the next: it ends its line, the caption goes under it
    one("a raw block after a paragraph, its caption on the next line", true,
      (a) => el("body", {}, el("p", {}, "Here is the plot:"), "\n", a, "\nFigure 1: requests per hour.\n\n", el("p", {}, "After."), "\n"));
    one("…opening the message", true, (a) => el("body", {}, a, "\nFigure 1: requests per hour."));
    one("…its caption an <em>", true, (a) => el("body", {}, a, "\n", el("em", {}, "Figure 1: requests per hour.")));
    one("…its caption an <em> on an indented line", true, (a) => el("body", {}, a, "\n  ", el("em", {}, "Figure 1: requests per hour."), "\n"));
    pair("two captioned in one raw block: each line ends at its newline", true,
      (a, b) => el("body", {}, a, "\nFigure 1: the first run.\n", b, "\nFigure 2: the second run."));
    one("a padded <p align=\"center\"> with an <em> caption under it", true,
      (a) => el("p", { align: "center" }, "\n  ", a, "\n  ", el("em", {}, "Figure 1: the run."), "\n"));
    one("…in a <div>", true, (a) => el("div", {}, "\n", a, "\nFigure 1: the run.\n"));
    one("…in a quote", true, (a) => el("blockquote", {}, "\n", a, "\nFigure 1: the run.\n"));
    one("…in a cell", true, (a) => el("td", {}, a, "\nFigure 1: the run."));
    one("…a lead-in line above it in the same raw block", true, (a) => el("body", {}, el("strong", {}, "The run:"), "\n", a, "\n"));
    // 2. a blank line between two (markdown ends a raw HTML block there): each ends its line, stacked and boxed
    pair("two raw lines a blank line apart, at the message's root", true, (a, b) => el("body", {}, a, "\n\n", b));
    pair("…between paragraphs", true, (a, b) => el("body", {}, el("p", {}, "Lead."), "\n", a, "\n\n", b, "\n\n", el("p", {}, "After."), "\n"));
    pair("…a blank line holding spaces and a tab", true, (a, b) => el("body", {}, a, "\n  \t\n", b));
    pair("…inside a padded <p>", true, (a, b) => el("p", {}, "\n", a, "\n\n", b, "\n"));
    pair("…inside a <div>", true, (a, b) => el("div", { align: "center" }, "\n", a, "\n\n", b, "\n"));
    // 3. on consecutive lines of HTML with no percentage widths (the review of #353, 2026-10-22: #351 laid these out as a
    // row, which wraps into a stack with no frame when they are too wide to share a line): each ends its line, boxed
    pair("the README layout, <p>\\n<img>\\n<img>\\n</p>", true, (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
    pair("…indented under an align", true, (a, b) => el("p", { align: "center" }, "\n  ", a, "\n  ", b, "\n"));
    pair("…in a <div>", true, (a, b) => el("div", { align: "center" }, "\n", a, "\n", b, "\n"));
    pair("…two raw lines at the message's root", true, (a, b) => el("body", {}, a, "\n", b));
    pair("…in a cell", true, (a, b) => el("td", {}, a, "\n", b));
    pair("…an &nbsp; line between them", true, (a, b) => el("p", { align: "center" }, "\n  ", a, "\n  \u00a0 \n  ", b, "\n"));
    pair("…a comment between them", true, (a, b) => el("p", {}, "\n", a, "\n", { nodeType: 8, textContent: "x" }, "\n", b, "\n"));
    pair("…the second in a link", true, (a, b) => el("p", {}, "\n", a, "\n", el("a", { href: H }, b), "\n"));
    pair("…the second bold", true, (a, b) => el("p", {}, "\n", a, "\n", el("strong", {}, b), "\n"));
    pair("…a caption line after them", true, (a, b) => el("p", {}, "\n", a, "\n", b, "\n", el("em", {}, "Figure 1: both runs."), "\n"));
    // and a <br> on the next line ends it as it always did, marked under the box
    { const [a, at] = media("a"), end = el("br");
      el("p", { align: "center" }, "\n  ", a, "\n  ", end, "\n  ", el("em", {}, "Figure 1: the run."), "\n");
      assert.ok(solo(at) && marked(end), kind + ": a padded <p> with a <br> on the line after: alone, the <br> its line's end"); }
    // words on its own line keep it inline, in the author's HTML as in markdown
    one("words after it on its source line", false, (a) => el("body", {}, a, " is the slowest request.\n"));
    one("words before it on its source line", false, (a) => el("p", {}, "\nThe trace ", a, "\n"));
  }
  // a picture and a video on consecutive lines stack too
  { const a = el("img", { src: H + "a.png" }), v = el("video", { src: H + "b.mp4" });
    el("p", {}, "\n", a, "\n", v, "\n");
    assert.deepEqual([solo(a), solo(v)], [true, true], "a picture and a video on consecutive lines of HTML: each on its own line"); }
});

test("a row the author tiled, RUN: pictures on consecutive lines of HTML stay side by side only when every one gives a percentage width and, with the spaces between them, they fit a phone's line", () => {
  // the review of #353 (2026-10-22): #351 and #353 laid every run of raw pictures and videos on consecutive lines out as
  // HTML would, one row, so three pictures too wide to share a line wrapped into a stack with no frame, a caption on the
  // next line beside the last one's bottom edge, and raw <video>s on consecutive lines lost their frame. The user's
  // request that chat media look and size like the path preview wins: they stack, each boxed, unless the markup itself
  // says the row fits (the README's `<img width="45%">` pair), read off the markup and never off a layout
  const solo = new Function(SOLO_CODE + "\nreturn mdSolo;")() as (e: unknown) => boolean;
  const H = "https://img.example.test/";
  const img = (n: string, width?: string, more: Record<string, string> = {}) => el("img", { src: H + n + ".png", ...(width === undefined ? {} : { width }), ...more });
  const vid = (n: string, width?: string) => el("video", { controls: "", ...(width === undefined ? {} : { width }) }, "\n  ", el("source", { src: H + n + ".mp4", type: "video/mp4" }), "\n");
  const each = (what: string, want: boolean, ms: FNode[], build: (...ms: FNode[]) => FNode) => {
    build(...ms);
    assert.deepEqual(ms.map((m) => solo(m)), ms.map(() => want), what);
  };
  // the cases the review named
  { const a = img("a"), b = img("b"), c = img("c");
    el("body", {}, el("p", {}, "The three runs:"), "\n", a, "\n", b, "\n", c, "\nFigure 1: three runs, one under another.\n");
    assert.deepEqual([solo(a), solo(b), solo(c)], [true, true, true], "three raw <img> lines, no widths, a caption line after: each on its own line, the caption under the last"); }
  each("two raw <video>s on consecutive lines: each on its own line", true, [vid("a", "100%"), vid("b", "100%")], (a, b) => el("body", {}, a, "\n", b, "\n"));
  each("…with no widths", true, [vid("a"), vid("b")], (a, b) => el("body", {}, a, "\n", b));
  each("…as stills", true, [vid("a"), vid("b")].map((v) => el("span", { class: "md-video-still" }, v)).map((s) => s.childNodes![0]),
    (a, b) => el("p", {}, "\n", a.parentElement!, "\n", b.parentElement!, "\n"));
  each("the README row, <p>\\n<img width=\"45%\">\\n<img width=\"45%\">\\n</p>: side by side", false, [img("a", "45%"), img("b", "45%")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("two at 60%: 120 does not fit, each on its own line", true, [img("a", "60%"), img("b", "60%")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("one at 45% and one with no width: each on its own line", true, [img("a", "45%"), img("b")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("…one at 45% and one 300 pixels wide", true, [img("a", "45%"), img("b", "300")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("a picture and a video on consecutive lines: each on its own line", true, [img("a"), vid("b")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("…both at 45%: a video never tiles, the box sizes its still whatever its width says", true, [img("a", "45%"), vid("b", "45%")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  each("two videos at 45%", true, [vid("a", "45%"), vid("b", "45%")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n"));
  // the sum, every picture of the row counted, the ones sharing a source line included, and 3 for every space shown
  // between them (TILE_GAP: a space of the light theme's monospace prose is 2.74% of the phone's 286px bubble)
  each("three at 30%: 90 and two spaces, side by side", false, [img("a", "30%"), img("b", "30%"), img("c", "30%")], (a, b, c) => el("p", {}, "\n", a, "\n", b, "\n", c, "\n"));
  each("two at 48.5%: with the space between, exactly 100, side by side", false, [img("a", "48.5%"), img("b", "48.50%")], (a, b) => el("body", {}, a, "\n", b));
  each("…the white space before the first and after the last is no space between them", false, [img("a", "48.5%"), img("b", "48.5%")],
    (a, b) => el("p", { align: "center" }, "\n  ", a, "\n  ", b, "\n"));
  each("50% and 50.5%: over 100, each on its own line", true, [img("a", "50%"), img("b", "50.5%")], (a, b) => el("body", {}, a, "\n", b));
  // the review of #355 (2026-10-22): rows whose shares add up to 100 at most still wrapped, into a stack with no frame,
  // once the spaces between their pictures pushed them over. Each case below was laid out in Chromium on the real
  // stylesheet at the phone's 286px bubble in both themes (tests/test_md_image_lightbox_browser.py holds the served ones)
  const row = (ws: string[], gap = "\n") => { const ms = ws.map((w, i) => img("p" + i, w)); el("p", {}, "\n", ...ms.flatMap((m, i) => (i ? [gap, m] : [m])), "\n"); return ms.map((m) => solo(m)); };
  for (const [ws, fits, what] of [
    [["50%", "50%"], false, "wraps at every width"], [["25%", "25%", "25%", "25%"], false, "wraps at every width"],
    [["49.5%", "49.5%"], false, "wraps on a phone"], [["49%", "49%"], false, "wraps on a phone in the light theme"],
    [["33%", "33%", "33%"], false, "wraps on a phone"], [["32%", "32%", "32%"], false, "wraps on a phone in the light theme"],
    [["24%", "24%", "24%", "24%"], false, "wraps on a phone"], [["23%", "23%", "23%", "23%"], false, "wraps on a phone in the light theme"],
    [["45%", "45%"], true, "fits"], [["48%", "48%"], true, "fits"], [["30%", "30%", "30%"], true, "fits"], [["31%", "31%", "31%"], true, "fits"],
    [["22%", "22%", "22%", "22%"], true, "fits"],
  ] as [string[], boolean, string][]) {
    assert.deepEqual(row(ws), ws.map(() => !fits), ws.join(" + ") + " on consecutive lines " + what + (fits ? ": side by side" : ": each on its own line, boxed"));
  }
  // a README's spacer line: `&nbsp; &nbsp; &nbsp;` between the two is seven spaces, a run on each side of each &nbsp;
  const SPACER = "\n\u00a0 \u00a0 \u00a0\n";
  assert.deepEqual(row(["45%", "45%"], SPACER), [true, true], "the README pair at 45% with an &nbsp; spacer line (90 and seven spaces) wraps on a phone: each on its own line, boxed");
  assert.deepEqual(row(["48%", "48%"], SPACER), [true, true], "…at 48%");
  assert.deepEqual(row(["38%", "38%"], SPACER), [false, false], "…at 38% it fits (76 and seven spaces): side by side");
  assert.deepEqual(row(["45%", "45%"], "\n\u00a0\n"), [false, false], "a single &nbsp; line between two at 45% (three spaces) fits: side by side");
  assert.deepEqual(row(["47%", "47%"], "\n\u00a0\n"), [true, true], "…between two at 47% it does not");
  assert.deepEqual(row(["42%", "42%"], "\n\u2003\u2003\u2003\n"), [true, true],
    "an em space counts two spaces (13px in the dark theme's Inter): three between two at 42% wrap on a phone, each on its own line");
  { const a = img("a", "45%"), b = img("b", "45%");
    el("p", {}, "\n", el("a", { href: H }, "\u00a0\u00a0", a), "\n", el("a", { href: H }, b, "\u00a0\u00a0"), "\n");
    assert.deepEqual([solo(a), solo(b)], [true, true], "the &nbsp; inside a wrapper on the row counts too, before the first picture and after the last included"); }
  { const a = img("a", "45%"), b = img("b", "45%"), c = img("c", "45%");
    el("p", {}, "\n", a, " ", b, "\n", c, "\n");
    assert.deepEqual([solo(a), solo(b), solo(c)], [false, false, true], "two at 45% on one line and a third on the next: 135, so the third is on a line of its own"); }
  { const a = img("a", "45%"), b = img("b", "45%"), c = img("c");
    el("p", {}, "\n", a, "\n", b, "\n", c, "\n");
    assert.deepEqual([solo(a), solo(b), solo(c)], [true, true, true], "a third with no width on the next line: the row is not tiled, each on its own line"); }
  // what a percentage width is: HTML's reading of the attribute, and only the attribute
  each("a width with leading spaces is still a percentage", false, [img("a", " 45%"), img("b", "45%")], (a, b) => el("body", {}, a, "\n", b));
  each("\"45 %\" is 45 pixels to HTML, not a share", true, [img("a", "45 %"), img("b", "45 %")], (a, b) => el("body", {}, a, "\n", b));
  each("a width in a style is none (the sanitizer keeps a style's colours only)", true,
    [img("a", undefined, { style: "width: 45%" }), img("b", undefined, { style: "width: 45%" })], (a, b) => el("body", {}, a, "\n", b));
  // a row runs across what shows nothing and through the wrappers its pictures sit in, and stops where mdSolo's line does
  const inPicture = (m: FNode): FNode => (el("picture", {}, "\n", el("source", { srcset: H + "d.png" }), "\n", m, "\n"), m);
  each("a <picture> at 45% beside a picture at 45%", false, [img("a", "45%"), inPicture(img("b", "45%"))],
    (a, b) => el("p", {}, "\n", a, "\n", b.parentElement!, "\n"));
  each("…a <picture> whose picture gives no width", true, [img("a", "45%"), inPicture(img("b"))],
    (a, b) => el("p", {}, "\n", a, "\n", b.parentElement!, "\n"));
  each("linked pictures at 45%: side by side", false, [img("a", "45%"), img("b", "45%")],
    (a, b) => el("p", {}, "\n", el("a", { href: H }, a), "\n", el("a", { href: H }, b), "\n"));
  each("…an &nbsp; line between them", false, [img("a", "45%"), img("b", "45%")], (a, b) => el("p", { align: "center" }, "\n  ", a, "\n  \u00a0 \n  ", b, "\n"));
  each("…a comment between them", false, [img("a", "45%"), img("b", "45%")], (a, b) => el("p", {}, "\n", a, "\n", { nodeType: 8, textContent: "x" }, "\n", b, "\n"));
  each("…a caption line after the row", false, [img("a", "45%"), img("b", "45%")], (a, b) => el("p", {}, "\n", a, "\n", b, "\n", el("em", {}, "Figure 1: both runs."), "\n"));
  { const a = img("a", "45%"), b = img("b", "45%"), c = img("c", "45%"), d = img("d", "45%");
    el("p", {}, "\n", a, "\n", b, "\nFigure 1: the first pair.\n", c, "\n", d, "\n");
    assert.deepEqual([solo(a), solo(b), solo(c), solo(d)], [false, false, false, false], "a caption line between two rows parts them: two rows of 90"); }
  { const a = img("a", "45%"), b = img("b", "45%"), c = img("c", "45%");
    el("body", {}, a, "\n", b, "\n\n", c, "\n");
    assert.deepEqual([solo(a), solo(b), solo(c)], [false, false, true], "a blank line parts a row: the pair tiles, the third stands alone"); }
  { const a = img("a", "45%");
    el("p", {}, "\n", a, "\n", el("a", { href: H }, img("b", "45%"), " the board"), "\n");
    assert.equal(solo(a), true, "a link holding a picture AND words is no picture: the line ends"); }
  // where marked wrote the line, its newline still ends it, percentages or not
  each("a flush <p> (markdown's own lines): each on its own line", true, [img("a", "45%"), img("b", "45%")], (a, b) => el("p", {}, a, "\n", b));
});

test("the predicate: a marked picture, outside any link or summary, showing a source, SHOWN at least ZOOM_MIN_H px tall", () => {
  assert.match(PREVIEW, /const ZOOM_MIN_H = 32;/, "a badge is 20px tall, an emoji or an icon 16 to 24: glyphs in a line of text");
  const at = PREVIEW.indexOf("export function mdImgZoomable(");
  const code = ts(PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at) + 2).replace(/^export /, ""));
  const zoomable = (new Function("ZOOM_MIN_H", "opensInLightbox", code + "\nreturn mdImgZoomable;") as
    (n: number, ok: (u: string) => boolean) => (img: unknown) => boolean)(32, opensInLightbox);
  // h: the file's natural height; shown: the height the page lays it out at (img.height), the natural one unless given
  const img = (o: { cls?: string[]; link?: boolean; summary?: boolean; complete?: boolean; h?: number; shown?: number; src?: string; currentSrc?: string }) => ({
    classList: { contains: (c: string) => (o.cls || ["md-img"]).includes(c) },
    closest: (s: string) => (s.split(/\s*,\s*/).some((one) => (one === "a" && o.link) || (one === "summary" && o.summary)) ? {} : null),
    complete: o.complete ?? true, naturalHeight: o.h ?? 450, height: o.shown ?? o.h ?? 450,
    src: o.src ?? "https://img.example.test/wide.png", currentSrc: o.currentSrc ?? o.src ?? "https://img.example.test/wide.png" });
  assert.equal(zoomable(img({})), true, "a markdown picture zooms");
  assert.equal(zoomable(img({ link: true })), false, "a picture inside a link is the link's");
  assert.equal(zoomable(img({ summary: true })), false, "a picture inside a <summary> is the summary's: its click opens the details (2026-10-20 review)");
  assert.equal(zoomable(img({ src: "", currentSrc: "https://img.example.test/a.png" })), true, "a srcset alone: it shows its candidate, and zooms on it");
  assert.equal(zoomable(img({ src: "", currentSrc: "" })), false, "no source at all: nothing a lightbox could show (2026-10-20 review)");
  // the click opens on what the picture shows, and a srcset candidate past the first is one the sanitizer never read: the
  // click keeps to the schemes the arrows keep (opensInLightbox), whatever the browser picked (2026-10-20 review)
  assert.equal(zoomable(img({ currentSrc: "javascript:alert(1)" })), false, "a javascript: candidate never opens");
  assert.equal(zoomable(img({ currentSrc: "data:text/html,<p>page</p>" })), false, "nor a data: page");
  assert.equal(zoomable(img({ src: "javascript:alert(1)", currentSrc: "" })), false, "nor a src the browser shows as it is");
  assert.equal(zoomable(img({ currentSrc: "data:image/png;base64,AAAA" })), true, "a data: picture does");
  assert.equal(zoomable(img({ h: 20 })), false, "a 20px badge does not");
  assert.equal(zoomable(img({ h: 32 })), true, "32px and up does");
  // the height it SHOWS at decides, not its file's (2026-10-21, the review of the shared box)
  assert.equal(zoomable(img({ h: 1200, shown: 20 })), false, "an <img height=\"20\"> of a large picture is a glyph in its line");
  assert.equal(zoomable(img({ h: 20, shown: 64 })), true, "a small file shown large (an author's height) is a picture");
  assert.equal(zoomable(img({ complete: false, h: 0 })), false, "nothing loaded yet: nothing to show");
  assert.equal(zoomable(img({ h: 0 })), false, "a parked picture (no src) shows its alt text, not a picture");
  assert.equal(zoomable(img({ cls: ["path-full-img"] })), false, "romp's own figures keep their own click (previewFull)");
  assert.equal(zoomable(img({ cls: ["user-img"] })), false, "…and so do a user's attachments");
});

test("the hook: ONE pair of document listeners, installed once, web only; load marks the cursor class and remembers glyphs, click opens by URL", () => {
  const at = PREVIEW.indexOf("export function installMdImgZoom(): void {");
  const body = PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at));
  assert.match(body, /if \(mdImgZoomOn \|\| !canPreview\(\)\) return;/, "ensure-once, and the web dashboard only, as every preview");
  assert.match(body, /document\.addEventListener\("load", \(e\) => \{[\s\S]*?\}, true\);/, "load does not bubble: the capture phase sees it");
  assert.match(body, /if \(img\.height < ZOOM_MIN_H\) mdImgTiny\.add\(img\.src\); else mdImgTiny\.delete\(img\.src\);/, "a glyph (by the height it shows at) is remembered, so the sequence skips it too");
  assert.match(body, /img\.classList\.toggle\("md-img-zoom", mdImgZoomable\(img\)\);/, "the cursor class follows the same predicate the click reads");
  assert.match(body, /mdImgDims\.set\(img\.src, \{ w: img\.naturalWidth, h: img\.naturalHeight \}\);/,
    "a URL's natural size is remembered, so the post-pass stamps the zoom on its next render (shownHeightOf reads the attributes against it)");
  // a message's video still opens the lightbox from the same capture listener, by the address it carries; a path's or a
  // link's still (inside .path-full) keeps its own click with the file's source (2026-10-21)
  assert.match(body, /if \(still && !still\.closest\("\.path-full"\) && still\.dataset\.video && opensInLightbox\(still\.dataset\.video, "video"\)\) \{[\s\S]*?openLightbox\(\{ url: still\.dataset\.video, video: true \}, v\);/,
    "re-checked at the click against the video rule, whatever put the address there");
  assert.match(body, /document\.addEventListener\("loadedmetadata", \(e\) => \{[\s\S]*?videoDims\.set\(url, \{ w: v\.videoWidth, h: v\.videoHeight \}\);[\s\S]*?\}, true\);/,
    "a clip's size is remembered the moment its metadata lands, so its next render is laid out at that size");
  assert.match(body, /document\.addEventListener\("click", \(e\) => \{[\s\S]*?\}, true\);/, "capture: a fold or a bubble around the picture never toggles under it");
  assert.match(body, /e\.preventDefault\(\); e\.stopPropagation\(\);\s*\n\s*openLightbox\(\{ url: img\.currentSrc \|\| img\.src, alt: img\.alt \}, img\);/,
    "the URL the picture SHOWS (a srcset's candidate, else its src; 2026-10-20 review: a srcset alone opened an empty lightbox), with the element the chat reads its transcript off");
  assert.equal((RENDER.match(/installMdImgZoom\(\);/g) || []).length, 1, "render.ts installs it once, beside the heal");
  assert.match(PREVIEW, /\.filter\(\(e\) => "url" in e \? !mdImgFailed\.has\(e\.url\) && !mdImgTiny\.has\(e\.url\) : previewKind\(e\.path\) === "img" \|\| previewKind\(e\.path\) === "video"\);/,
    "the sequence skips a URL that failed or loaded glyph-sized: no click opens one; a file is a picture or a video");
  assert.match(PREVIEW, /img\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openLightbox\(\{ path, sid, pin \}, img\); \};/, "a path preview opens the same lightbox, with its element");
});

test("the lightbox's own anchors and the comment popover: a data: download is the browser's, and the popover outlives the lightbox", () => {
  assert.match(RENDER, /if \(a\.hasAttribute\("download"\) && a\.closest\("#romp-lightbox"\)\) return;/,
    "the chat's link opener leaves the lightbox's download to the browser (a tab cannot open a data: URL)");
  assert.match(RENDER, /closest\?\.\("\.meta-menu, #fork-prompt, #romp-lightbox"\)\) return;/,
    "a press inside the lightbox a thread's picture opened does not close the thread");
});

test("styles: media capped at the body at zero specificity, a table and a <pre> scroll inside it", () => {
  assert.match(CSS, /^:where\(\.md, \.cmt-msg\.agent\) :where\(img, video, iframe\) \{ max-width: 100%; \}$/m);
  assert.match(CSS, /^:where\(\.md, \.cmt-msg\.agent\) :where\(img, video\):where\(:not\(\[height\]\), \[width\]\) \{ height: auto; \}$/m,
    "the ratio kept; a height given with no width stays as written; a frame (no ratio of its own) keeps its height");
  assert.match(CSS, /^\.md table \{ border-collapse: collapse; margin: 0\.6em 0; display: block; width: max-content; max-width: 100%; overflow-x: auto; overflow-wrap: normal; \}$/m,
    "a table scrolls inside its own box, whole words kept (the file viewer's treatment)");
  assert.match(CSS, /^\.cmt-msg\.agent table \{ display: block; width: max-content; max-width: 100%; overflow-x: auto; overflow-wrap: normal; \}$/m);
  assert.match(CSS, /^\.cmt-msg\.agent pre \{ overflow-x: auto; \}$/m);
  const pre = (CSS.match(/^\.md pre \{[^}]*\}/m) || [""])[0];
  assert.ok(pre, "the chat's <pre> rule");
  assert.match(pre, /overflow-x: auto;/, "a <pre> the message writes in HTML scrolls instead of being cut off");
  assert.doesNotMatch(pre, /overflow-x: hidden/);
  assert.match(CSS, /\.md pre code \{[^}]*white-space: pre-wrap;/, "a fence still WRAPS, so it never scrolls sideways");
});

test("ONE picture box: a markdown picture, a video's still and the path preview share every declaration, each written once", () => {
  // the user 2026-10-21, who wanted both kinds of picture, then videos, sized and dressed like the path preview: the classes
  // sit in the same selector lists, so none can drift from another. The size caps every markdown picture and every still
  // (.md-img, .md-video; a badge far under both caps); the box and the frame dress one that zooms AND stands alone in its
  // paragraph (.md-solo; the review of this box, 2026-10-21: a picture mid-sentence split the sentence around a block);
  // the zoom cursor marks everything a click opens, inline or not, as the path preview
  assert.match(CSS, /^\.path-full-img, \.md-img, \.md-video \{ max-width: 100%; max-height: 320px; object-fit: contain; \}$/m,
    "the size: the bubble's width, 320px tall, the ratio kept");
  assert.match(CSS, /^\.path-full-img, \.md-img-zoom\.md-solo, \.md-solo > \.md-video \{ display: block; border-radius: 8px;\n  border: 1px solid rgba\(255, 255, 255, 0\.14\); background: rgba\(0, 0, 0, 0\.25\); \}$/m,
    "the box: block, rounded, framed, for a picture or a still alone in its paragraph");
  assert.match(CSS, /^\.path-full-img, \.md-img-zoom, \.md-video-still \{ cursor: zoom-in; \}$/m, "the zoom cursor, inline or boxed");
  assert.match(CSS, /^\.path-full-img:hover, \.md-img-zoom\.md-solo:hover, \.md-solo:hover > \.md-video \{ border-color: var\(--accent\); \}$/m, "the hover");
  // and no other rule styles any of them on its own, so there is no second copy to drift; the still's own anatomy (its
  // auto size against an author's width="100%", its wrapper's shrink-to-fit) names no number the shared rules carry
  const rules = [...CSS.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((m) => ({ sel: m[1].trim(), body: m[2] }));
  const names = /\.(?:path-full-img|md-img|md-img-zoom|md-video|md-solo)(?![-\w])/;
  assert.deepEqual(rules.filter((r) => names.test(r.sel)).map((r) => r.sel),
    [".path-full-img, .md-img, .md-video", ".path-full-img, .md-img-zoom.md-solo, .md-solo > .md-video",
     ".path-full-img, .md-img-zoom, .md-video-still", ".path-full-img:hover, .md-img-zoom.md-solo:hover, .md-solo:hover > .md-video",
     ".md-video", ".md-video-still.md-solo", ":is(.md-img-zoom, .md-video-still).md-solo + br.md-solo-end",
     ":has(> :is(.md-img-zoom, .md-video-still).md-solo:last-child) + br.md-solo-end,\n:has(> :last-child > :is(.md-img-zoom, .md-video-still).md-solo:last-child) + br.md-solo-end"],
    "the four shared rules, the still's two anatomy rules and the boxed forms' line break are the only ones naming any of them");
  // a box ends its own line: the <br> that ends that line (a caption on the user's next line) adds no blank line; only the
  // boxed forms (the review of #343). Only the <br> mdSolo marked: a bare `+ br` passes over text, and hid the hard break
  // INSIDE a caption, running its two lines together (the review of #349)
  assert.match(CSS, /^:is\(\.md-img-zoom, \.md-video-still\)\.md-solo \+ br\.md-solo-end \{ display: none; \}$/m, "the box's own line end");
  assert.match(CSS, /^:has\(> :is\(\.md-img-zoom, \.md-video-still\)\.md-solo:last-child\) \+ br\.md-solo-end,\n:has\(> :last-child > :is\(\.md-img-zoom, \.md-video-still\)\.md-solo:last-child\) \+ br\.md-solo-end \{ display: none; \}$/m,
    "…after the bold or italic around a box, whose last element it is (its own rule: a browser without :has() drops only it)");
  for (const r of rules) assert.doesNotMatch(r.sel, /\+\s*br(?!\.md-solo-end)/, "no rule hides a <br> mdSolo did not mark: " + r.sel);
  assert.match(CSS, /^\.md-video \{ display: block; width: auto; height: auto; \}$/m,
    "an author rule's auto beats the width=\"100%\" an agent writes on a <video>: the clip is the picture box's size, never the bubble's");
  for (const r of rules.filter((x) => names.test(x.sel) && !/^\.path-full-img/.test(x.sel)))
    assert.doesNotMatch(r.body, /320px|border-radius|border:|max-height/, r.sel + " repeats nothing the shared rules own");
});
