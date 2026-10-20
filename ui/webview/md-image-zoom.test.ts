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

test("the predicate: a marked picture, outside any link or summary, showing a source, loaded at least ZOOM_MIN_H px tall", () => {
  assert.match(PREVIEW, /const ZOOM_MIN_H = 32;/, "a badge is 20px tall, an emoji or an icon 16 to 24: glyphs in a line of text");
  const at = PREVIEW.indexOf("export function mdImgZoomable(");
  const code = ts(PREVIEW.slice(at, PREVIEW.indexOf("\n}\n", at) + 2).replace(/^export /, ""));
  const zoomable = (new Function("ZOOM_MIN_H", "opensInLightbox", code + "\nreturn mdImgZoomable;") as
    (n: number, ok: (u: string) => boolean) => (img: unknown) => boolean)(32, opensInLightbox);
  const img = (o: { cls?: string[]; link?: boolean; summary?: boolean; complete?: boolean; h?: number; src?: string; currentSrc?: string }) => ({
    classList: { contains: (c: string) => (o.cls || ["md-img"]).includes(c) },
    closest: (s: string) => (s.split(/\s*,\s*/).some((one) => (one === "a" && o.link) || (one === "summary" && o.summary)) ? {} : null),
    complete: o.complete ?? true, naturalHeight: o.h ?? 450,
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
  assert.match(body, /if \(img\.naturalHeight < ZOOM_MIN_H\) mdImgTiny\.add\(img\.src\); else mdImgTiny\.delete\(img\.src\);/, "a glyph is remembered, so the sequence skips it too");
  assert.match(body, /img\.classList\.toggle\("md-img-zoom", mdImgZoomable\(img\)\);/, "the cursor class follows the same predicate the click reads");
  assert.match(body, /document\.addEventListener\("click", \(e\) => \{[\s\S]*?\}, true\);/, "capture: a fold or a bubble around the picture never toggles under it");
  assert.match(body, /e\.preventDefault\(\); e\.stopPropagation\(\);\s*\n\s*openLightbox\(\{ url: img\.currentSrc \|\| img\.src, alt: img\.alt \}, img\);/,
    "the URL the picture SHOWS (a srcset's candidate, else its src; 2026-10-20 review: a srcset alone opened an empty lightbox), with the element the chat reads its transcript off");
  assert.equal((RENDER.match(/installMdImgZoom\(\);/g) || []).length, 1, "render.ts installs it once, beside the heal");
  assert.match(PREVIEW, /\.filter\(\(e\) => "url" in e \? !mdImgFailed\.has\(e\.url\) && !mdImgTiny\.has\(e\.url\) : previewKind\(e\.path\) === "img"\);/,
    "the sequence skips a URL that failed or loaded glyph-sized: no click opens one");
  assert.match(PREVIEW, /img\.onclick = \(ev\) => \{ ev\.stopPropagation\(\); openLightbox\(\{ path, sid, pin \}, img\); \};/, "a path preview opens the same lightbox, with its element");
});

test("the lightbox's own anchors and the comment popover: a data: download is the browser's, and the popover outlives the lightbox", () => {
  assert.match(RENDER, /if \(a\.hasAttribute\("download"\) && a\.closest\("#romp-lightbox"\)\) return;/,
    "the chat's link opener leaves the lightbox's download to the browser (a tab cannot open a data: URL)");
  assert.match(RENDER, /closest\?\.\("\.meta-menu, #fork-prompt, #romp-lightbox"\)\) return;/,
    "a press inside the lightbox a thread's picture opened does not close the thread");
});

test("styles: media capped at the body at zero specificity, a table and a <pre> scroll inside it, the zoom cursor", () => {
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
  assert.match(CSS, /^\.md-img-zoom \{ cursor: zoom-in; \}$/m, "the path preview's own cursor");
});
