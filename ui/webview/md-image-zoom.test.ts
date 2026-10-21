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

test("the mark: every picture md() and userMd() render wears .md-img, stamped by the chat's post-pass and no other renderer", () => {
  const at = PREVIEW.indexOf("export function mdImgPostPass(root: ParentNode): void {");
  const body = PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at));
  assert.match(body, /img\.classList\.add\("md-img"\);/);
  assert.doesNotMatch(body, /if \(!mdImgFailed\.size\) return;/, "the mark is stamped whether or not any URL failed");
  assert.equal((RENDER.match(/mdImgPostPass\(clean\);/g) || []).length, 2, "md() and userMd() run it; the hover card's previewMdClean and the file viewer do not");
  assert.doesNotMatch(RENDER.slice(RENDER.indexOf("function previewMdClean("), RENDER.indexOf("function showFilePreview(")), /mdImgPostPass/);
});

// A fake of the sanitized DOM the post-pass walks: elements with a tag, attributes, classes, a parent and children (text
// nodes included), enough for mdSolo and the picture checks. Built by `el(tag, attrs, ...children)`; a string child is text.
type FNode = { nodeType: number; textContent?: string; tagName?: string; parentElement?: FNode | null; childNodes?: FNode[] } & Record<string, unknown>;
function el(tag: string, attrs: Record<string, string> = {}, ...kids: (FNode | string)[]): FNode {
  const cls = new Set<string>(), at = new Map(Object.entries(attrs));
  const node: FNode = { nodeType: 1, tagName: tag.toUpperCase(), parentElement: null, childNodes: [], cls, dataset: {} as Record<string, string>,
    get src() { return at.get("src") || ""; },
    getAttribute: (a: string) => (at.has(a) ? at.get(a) : null), hasAttribute: (a: string) => at.has(a), removeAttribute: (a: string) => { at.delete(a); },
    classList: { add: (c: string) => cls.add(c), remove: (c: string) => cls.delete(c), contains: (c: string) => cls.has(c) },
    closest: (q: string) => { for (let n: FNode | null | undefined = node; n; n = n.parentElement) if (q.split(/\s*,\s*/).includes((n.tagName || "").toLowerCase())) return n; return null; },
    querySelectorAll: (q: string) => { const out: FNode[] = []; const walk = (n: FNode) => { for (const k of n.childNodes || []) { if (k.nodeType === 1) { if ((k.tagName || "").toLowerCase() === q) out.push(k); walk(k); } } }; walk(node); return out; } };
  for (const k of kids) {
    const kid: FNode = typeof k === "string" ? { nodeType: 3, textContent: k } : k;
    if (kid.nodeType === 1) kid.parentElement = node;
    node.childNodes!.push(kid);
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
  const code = line("const attrPx = ") + ts(PREVIEW.slice(PREVIEW.indexOf("const BLOCK_TAGS = "), PREVIEW.indexOf("\n", PREVIEW.indexOf("const BLOCK_TAGS = ")) + 1)) + fn("function parkMdImg(") + fn("export function mdImgPostPass(")
    + fn("export function shownHeightOf(") + fn("export function mdSolo(");
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
     ".md-video", ".md-video-still.md-solo"],
    "the four shared rules, and the still's two anatomy rules, are the only ones naming any of them");
  assert.match(CSS, /^\.md-video \{ display: block; width: auto; height: auto; \}$/m,
    "an author rule's auto beats the width=\"100%\" an agent writes on a <video>: the clip is the picture box's size, never the bubble's");
  for (const r of rules.filter((x) => names.test(x.sel) && !/^\.path-full-img/.test(x.sel)))
    assert.doesNotMatch(r.body, /320px|border-radius|border:|max-height/, r.sel + " repeats nothing the shared rules own");
});
