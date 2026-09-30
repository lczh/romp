// Comment in the message box's quote-chip row (the user 2026-09-30: on a phone the floating Comment pill under a selection
// kept landing under the system's own selection menu, which a page can neither query nor avoid). A transcript selection
// seeds a quote chip at the bottom of the screen, and the row now carries Comment before Stage on every device; its press
// runs the one comment door the menu, the key and the palette use (commentOnSelection), with the anchor the CHIP captured,
// since on a phone the tap that reaches the button dismisses the selection first.
//
// EXECUTED here, lifted out of render.ts and run over a fake DOM (the staged-list-cap.test.ts idiom): the row's paint
// (renderComposerChipsInner), the gate (chipCommentTarget) and the press (commentOnChip) through the real commentAim and
// commentOnSelection. Pinned at the source: the delegation, the pill's removal, and the touch sheet. The served half
// (a phone and a desktop, both themes, the tap areas measured) is tests/test_comment_chip_served.py.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);
const WEBVIEW = path.resolve(process.cwd(), "..", "ui", "webview");
const RENDER = fs.readFileSync(path.join(WEBVIEW, "render.ts"), "utf8");
const CSS = fs.readFileSync(path.join(WEBVIEW, "styles.css"), "utf8");

const lift = (name: string): string => {
  const i = RENDER.indexOf(`\nfunction ${name}(`);
  assert.ok(i > 0, name + " is in render.ts");
  let depth = 0, j = RENDER.indexOf("{\n", i);
  for (; j < RENDER.length; j++) { if (RENDER[j] === "{") depth++; else if (RENDER[j] === "}") { depth--; if (!depth) break; } }
  return RENDER.slice(i, j + 1);
};

/** Enough of Element for the row's paint: children, class name, dataset, style, attributes, listeners. */
class FakeEl {
  tag: string; className: string; children: FakeEl[] = [];
  dataset: Record<string, string> = {}; attrs: Record<string, string> = {}; listeners: Record<string, Function[]> = {};
  textContent = ""; title = ""; type = "";
  style: Record<string, string> = {};
  constructor(tag: string, cls = "") { this.tag = tag; this.className = cls; }
  appendChild(c: FakeEl): FakeEl { this.children.push(c); return c; }
  replaceChildren(): void { this.children = []; }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  getBoundingClientRect() { return { left: 300, top: 760, bottom: 777 }; }
}

type Cite = { itemId?: string; title: string; quote?: string; uuid?: string | null; src?: string };
type World = {
  cites?: Cite[];
  provisional?: boolean;                                  // the tab is still being created
  live?: boolean;                                         // liveSession answers (false: a skeleton this page has not loaded)
  edit?: boolean;                                         // an edit pill holds the row
  passage?: { left: number; bottom: number } | null;      // where the passage is on screen (null: not rendered)
  activeId?: string;
};

// synthetic ids, private to this file
const SID = "6d2f8a4b-1c3e-4f5a-8b7c-9d0e1f2a3b4c";
const U1 = "7e3a9b5c-2d4f-4a6b-9c8d-0e1f2a3b4c5d", U2 = "8f4b0c6d-3e5a-4b7c-8d9e-1f2a3b4c5d6e";
const PASSAGE = "The retry cap stays at two minutes";
const OLDER = "streams rows through a bounded buffer";
const quote = (text: string, uuid: string | null, src?: string): Cite => ({ title: text, quote: text, uuid, src });

function chat(w: World) {
  const strip = new FakeEl("div");
  const opened: Array<[string, string, string, number, number]> = [];
  const toasts: string[] = [];
  const ts = ["renderComposerChipsInner", "chipCommentTarget", "commentOnChip", "commentAim", "commentOnSelection"].map(lift).join("\n");
  const src = requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020" }).code;   // the types go, the logic runs as shipped
  const sid = w.activeId ?? SID;
  const env = {
    activeId: sid,
    document: { getElementById: (id: string) => (id === "composer-chips" ? strip : null) },
    el: (tag: string, cls?: string) => new FakeEl(tag, cls),
    composerEdits: new Map(w.edit ? [[sid, { uuid: U1, orig: "an earlier message" }]] : []),
    composerCitations: new Map(w.cites ? [[sid, w.cites]] : []),
    isProvisionalId: () => !!w.provisional,
    liveSession: (id: string) => (w.live === false ? undefined : { id }),
    transcriptSelection: () => null,                    // the selection has collapsed: the tap on the button dismissed it
    window: { getSelection: () => ({ rangeCount: 0 }) },
    views: new Map(),
    passageBox: () => (w.passage === undefined ? { left: 80, bottom: 193 } : w.passage),
    openCommentComposer: (s: string, u: string, exact: string, x: number, y: number) => { opened.push([s, u, exact, x, y]); },
    ephemeralWarnToast: (msg: string) => { toasts.push(msg); },
    noop: () => {},
  };
  const api = new Function("env",
    "const { document, el, composerEdits, composerCitations, isProvisionalId, liveSession, transcriptSelection, window, views, passageBox, openCommentComposer, ephemeralWarnToast } = env;\n"
    + "const closeCitePreview = env.noop, cancelComposerEdit = env.noop, openCitePreview = env.noop, removeCitation = env.noop, fireStage = env.noop;\n"
    + "let activeId = env.activeId;\n" + src
    + "\nreturn { renderComposerChipsInner, chipCommentTarget, commentOnChip };")(env) as {
    renderComposerChipsInner: (id: string | null) => void;
    chipCommentTarget: (id: string | null) => { sid: string; uuid: string; text: string } | "none" | "session";
    commentOnChip: (btn: FakeEl) => void;
  };
  api.renderComposerChipsInner(sid);
  const row = strip.children.map((c) => c.className.split(" ").includes("composer-chip") ? "chip" : c.className + ":" + c.textContent);
  const button = strip.children.find((c) => c.className === "composer-comment-btn") || null;
  return { ...api, strip, row, button, opened, toasts };
}

// ── when it shows ────────────────────────────────────────────────────────────────────────────────
test("a quote chip from a transcript turn: the row reads chip, Comment, Stage, and Comment is a delegated button", () => {
  const c = chat({ cites: [quote(PASSAGE, U1)] });
  assert.deepEqual(c.row, ["chip", "composer-comment-btn:Comment", "composer-stage-btn:Stage"], "Comment first, as in the right-click menu");
  assert.ok(c.button);
  assert.equal(c.button!.tag, "button");
  assert.equal(c.button!.type, "button");
  assert.equal(c.button!.dataset.act, "chip-comment", "its press is the strip's delegate, never a listener on a node the row rebuilds");
  assert.deepEqual(c.button!.listeners, {});
  assert.equal(c.button!.title, "comment on the quoted passage");
});

test("no Comment when no chip holds a passage of a turn, or the session cannot take a comment yet", () => {
  for (const [why, w] of [
    ["an editor highlight (a file's lines, no turn)", { cites: [quote("const a = 1;", null, "src/a.ts:12-12")] }],
    ["a card's follow-up (no passage)", { cites: [{ itemId: SID + ":g1", title: "the export goal" }] }],
    ["a quote with no turn", { cites: [quote(PASSAGE, null)] }],
    ["a tab still being created", { cites: [quote(PASSAGE, U1)], provisional: true }],
    ["a skeleton this page has not loaded", { cites: [quote(PASSAGE, U1)], live: false }],
  ] as Array<[string, World]>) {
    const c = chat(w);
    assert.equal(c.button, null, why + ": no Comment");
    assert.equal(c.row[c.row.length - 1], "composer-stage-btn:Stage", why + ": Stage is still there");
  }
  const edit = chat({ cites: [quote(PASSAGE, U1)], edit: true });
  assert.equal(edit.button, null, "an edit pill holds the row alone");
  const empty = chat({});
  assert.deepEqual([empty.row, empty.strip.style.display], [[], "none"], "no chips: no row at all");
});

test("stacked chips: one Comment, on the NEWEST chip that holds a passage of a turn", () => {
  const c = chat({ cites: [quote(OLDER, U2), quote(PASSAGE, U1), quote("const a = 1;", null, "src/a.ts:12-12")] });
  assert.deepEqual(c.row, ["chip", "chip", "chip", "composer-comment-btn:Comment", "composer-stage-btn:Stage"]);
  assert.deepEqual(c.chipCommentTarget(SID), { sid: SID, uuid: U1, text: PASSAGE }, "the editor highlight after it holds no turn");
  assert.equal(c.button!.title, "comment on the last quoted passage", "the tooltip says which");
});

// ── what it opens ────────────────────────────────────────────────────────────────────────────────
test("the press opens the box through commentOnSelection with the chip's captured sid, turn and text, under the passage", () => {
  const c = chat({ cites: [quote(OLDER, U2), quote(PASSAGE, U1)], passage: { left: 80, bottom: 193 } });
  c.commentOnChip(c.button!);
  assert.deepEqual(c.opened, [[SID, U1, PASSAGE, 80, 199]], "the live selection is gone; the chip's anchor is what opens");
  assert.deepEqual(c.toasts, []);
});

test("a passage not rendered on screen opens the box at the button, as the menu opens it at the pointer", () => {
  const c = chat({ cites: [quote(PASSAGE, U1)], passage: null });
  c.commentOnChip(c.button!);
  assert.deepEqual(c.opened, [[SID, U1, PASSAGE, 300, 760]]);
});

test("a press after the session stopped being ready, or the chip went, says so the key's way and opens nothing", () => {
  const c = chat({ cites: [quote(PASSAGE, U1)] });   // the button, painted while the session was ready…
  const gone = chat({ cites: [quote(PASSAGE, U1)], live: false });
  gone.commentOnChip(c.button!);                     // …pressed after it stopped being
  assert.deepEqual([gone.opened, gone.toasts], [[], ["This session isn't ready for comments yet."]]);
  const none = chat({});
  none.commentOnChip(c.button!);
  assert.deepEqual([none.opened, none.toasts], [[], ["Select some text in the chat to comment on it."]]);
});

// ── the wiring (render.ts) ───────────────────────────────────────────────────────────────────────
test("render.ts: the press is delegated once on the strip and runs the one comment door", () => {
  assert.match(RENDER, /\{\n\s*\/\/[^\n]*\n\s*const strip = document\.getElementById\("composer-chips"\);\n\s*if \(strip\) delegate\(strip, \{ "chip-comment": \(b\) => commentOnChip\(b\) \}\);\n\}/);
  const press = lift("commentOnChip");
  assert.match(press, /commentOnSelection\(\{ \.\.\.t, x: p \? p\.left : b\.left, y: p \? p\.bottom \+ 6 : b\.top \}\);/);
  assert.doesNotMatch(press, /openCommentComposer|getSelection/, "no door of its own, and never the live selection");
  const paint = lift("renderComposerChipsInner");
  assert.ok(paint.indexOf('el("button", "composer-comment-btn")') < paint.indexOf('el("button", "composer-stage-btn")'), "Comment before Stage");
});

test("the floating pill is gone: no module, no element, no rule, no touch wiring", () => {
  assert.equal(fs.existsSync(path.join(WEBVIEW, "comment-pill.ts")), false);
  assert.doesNotMatch(RENDER, /comment-pill|CommentPill|commentPill|cmtPill|cmt-pill|placeCommentPill/);
  assert.doesNotMatch(CSS, /cmt-pill/);
});

// ── the sheet ────────────────────────────────────────────────────────────────────────────────────
test("the sheet: Comment wears Stage's dress, and the chip cedes both buttons' room while Comment is there", () => {
  assert.match(CSS, /\.composer-comment-btn, \.composer-stage-btn \{ background: transparent;/);
  assert.match(CSS, /\.composer-comment-btn:hover, \.composer-stage-btn:hover \{/);
  assert.match(CSS, /\.composer-comment-btn:active, \.composer-stage-btn:active \{ transform: scale\(0\.96\); \}/);
  assert.match(CSS, /#composer-chips:has\(\.composer-comment-btn\) \.composer-chip \{ max-width: calc\(100% - 140px\); \}/);
});

test("the sheet: on a coarse pointer every control in the row and the staged strip owns a 44px tap area, and a fine pointer keeps today's sizes", () => {
  const at = CSS.indexOf("@media (pointer: coarse) {\n  #composer-chips {");
  assert.ok(at > 0, "the touch block");
  const block = CSS.slice(at, CSS.indexOf("\n}\n", at));
  assert.match(block, /#composer-chips \{ row-gap: 22px; column-gap: 8px; padding-top: 11px; padding-bottom: 11px; \}/,
    "lines 44px apart (a 22.8px chip and a 22px gap), and room for the areas above the first line and below the last");
  assert.match(block, /\.staged-head \.staged-caret::before, \.staged-head \.staged-go::before \{\n\s*content: ""; position: absolute; left: 50%; top: 50%; width: max\(100% \+ 2px, 44px\); height: 44px;/);
  assert.match(block, /#composer-chips \.composer-chip-x::before \{ content: ""; position: absolute; right: -5px; top: 50%; width: 44px; height: 44px;/,
    "the chip's x takes the chip's last 44px and never reaches past its edge");
  assert.match(block, /\.staged-head \{ min-height: 44px; \}/);
  assert.match(block, /\.staged-chip \{ position: relative; min-height: 44px; justify-content: center; padding-right: 44px; \}/);
  assert.match(block, /\.staged-chip \.composer-chip-x \{ position: absolute; top: -1px; right: -1px; bottom: -1px; width: 44px;/);
  // outside the touch block the controls keep their rules as they were
  const rest = CSS.slice(0, at) + CSS.slice(CSS.indexOf("\n}\n", at));
  const names = ["composer-comment-btn", "composer-stage-btn", "composer-chip-x", "staged-go", "staged-caret", "staged-chip", "staged-head", "#composer-chips"];
  for (const m of rest.matchAll(/([^{}]*)\{([^{}]*)\}/g)) {
    if (names.some((n) => m[1].includes(n))) assert.doesNotMatch(m[2], /44px/, "a fine pointer keeps today's sizes: " + m[1].trim());
  }
});
