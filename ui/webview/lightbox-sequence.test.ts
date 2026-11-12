// The chat's lightbox sequence (render.ts lightboxScope + chatImagesFor), RUN: a message's path previews and its markdown
// pictures in the order the chat shows them, and the transcript a clicked picture sits in as the scope (2026-10-20, the
// user on a phone: a picture a message embedded as markdown never opened the lightbox). render.ts has import-time DOM
// side effects, so the two functions are LIFTED (esbuild's ts loader, the lightbox-bar.test.ts precedent) and run over
// synthetic events: the notes-api demo world, placeholder ids.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { Marked } from "marked";
import { mdLayout, opensInLightbox, mdImgFile } from "./md-images";
import { keyOf } from "./chat-window";
import { chatMdExtensions } from "./chat-md";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const ts = (code: string): string => requireCjs("esbuild").transformSync(code, { loader: "ts" }).code;
const marked = new Marked({ gfm: true, breaks: false }, ...chatMdExtensions);

type Ev = Record<string, unknown>;
type Entry = { path?: string; sid?: string | null; pin?: string; url?: string; alt?: string };
type Fake = { closest: (sel: string) => unknown };
const WEB = "aaaaaaaa-1111-2222-3333-444444444444";
const SUB = WEB + "/agent/a0123456789abcdef";

// `lexed` counts the lexer's runs (the chat's marked singleton's lexer, as chatImagesFor calls it)
function lift(sessions: Map<string, { events: Ev[] }>, thread: { sid: string; th: { events: Ev[] } } | null = null, lexed = { n: 0 }) {
  const a = RENDER.indexOf("function lightboxScope(");
  const z = RENDER.indexOf("\n}\n", RENDER.indexOf("function chatImagesFor("));
  assert.ok(a > 0 && z > a, "lightboxScope/chatImagesFor: anchors not found; re-anchor");
  const code = ts(RENDER.slice(a, z + 2));
  const prelude = `
    const previewKind = (p) => /\\.(png|jpe?g|gif|webp|bmp|svg)$/i.test(p) ? "img" : /\\.pdf$/i.test(p) ? "pdf" : null;
    const subParts = (id) => { const m = /^(.+)\\/agent\\/([^/]+)$/.exec(id); return m ? { parentId: m[1], agentId: m[2] } : null; };
    const openCommentThread = () => H.thread;
    const sessions = H.sessions;
    const document = { baseURI: "https://dash.example.test/chat" };
  `;
  const H = { sessions, thread };
  const counting = { lexer: (src: string) => { lexed.n++; return marked.lexer(src); } };
  return (new Function("H", "marked", "mdLayout", "keyOf", "opensInLightbox", "mdImgFile", prelude + code + "\nreturn chatImagesFor;") as
    (h: unknown, m: unknown, f: unknown, k: unknown, o: unknown, mf: unknown) => (sid: string | null, from?: Fake | null) => Entry[])(H, counting, mdLayout, keyOf, opensInLightbox, mdImgFile);
}
// a clicked element, as far as the scope reads it: which ancestor it sits under
function inside(sel: Record<string, unknown>): Fake {
  return { closest: (s: string) => (s in sel ? sel[s] : null) };
}
const show = (es: Entry[]) => es.map((e) => e.url ? "url:" + e.url.replace("https://img.example.test/", "") : "path:" + e.path + (e.pin ? "@" + e.pin : ""));

test("within a message: a markdown picture where it is written, a path's figure after the paragraph that names it", () => {
  const ev: Ev = { kind: "assistant", uuid: "a1",
    md: "The run:\n\n![throughput](https://img.example.test/wide.png)\n\nThe split is in docs/figure.png and ![inline](https://img.example.test/inline.png) beside it.\n\n![after](https://img.example.test/after.png)",
    pathLinks: { "docs/figure.png": "docs/figure.png" }, pathPins: { "docs/figure.png": "v1" } };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(show(nav), ["url:wide.png", "url:inline.png", "path:docs/figure.png@v1", "url:after.png"],
    "the inline picture on the mentioning line renders inside its paragraph, the figure after it");
  assert.equal(nav[2].sid, WEB, "the file resolves in the session's folder");
});

test("a paragraph that wraps onto a second line: the picture written there shows ahead of the figure, as the page has it", () => {
  // the 2026-10-20 review's case: the end of the mentioning LINE put the figure ahead of a picture the paragraph shows first
  const ev: Ev = { kind: "assistant", uuid: "a1",
    md: "The p95 split per region is in docs/figure.png and\n![soft](https://img.example.test/soft.png) shows the hourly curve.\n\n![next](https://img.example.test/next.png)",
    pathLinks: { "docs/figure.png": "docs/figure.png" } };
  assert.deepEqual(show(lift(new Map([[WEB, { events: [ev] }]]))(WEB)), ["url:soft.png", "path:docs/figure.png", "url:next.png"]);
});

test("lists, table rows and one-line paragraphs keep their order: a figure after its item, its row, its line", () => {
  const ev: Ev = { kind: "assistant", uuid: "a1", md: [
    "- first docs/a.png",
    "- second ![b](https://img.example.test/b.png)",
    "",
    "| k | v |",
    "|---|---|",
    "| docs/c.png | x |",
    "| e | ![e](https://img.example.test/e.png) |",
    "",
    "One line names docs/f.png and ![g](https://img.example.test/g.png) beside it.",
    "",
    "![h](https://img.example.test/h.png)",
  ].join("\n"), pathLinks: { "docs/a.png": "docs/a.png", "docs/c.png": "docs/c.png", "docs/f.png": "docs/f.png" } };
  assert.deepEqual(show(lift(new Map([[WEB, { events: [ev] }]]))(WEB)),
    ["path:docs/a.png", "url:b.png", "path:docs/c.png", "url:e.png", "url:g.png", "path:docs/f.png", "url:h.png"]);
});

test("a list item's figure goes at the item's end, after what is nested in it; a quote's paragraph is one block", () => {
  const ev: Ev = { kind: "assistant", uuid: "a1", md: [
    "- outer names docs/a.png",
    "  - inner ![b](https://img.example.test/b.png)",
    "- next ![c](https://img.example.test/c.png)",
    "",
    "> quoted docs/d.png and",
    "> ![e](https://img.example.test/e.png) here",
  ].join("\n"), pathLinks: { "docs/a.png": "docs/a.png", "docs/d.png": "docs/d.png" } };
  assert.deepEqual(show(lift(new Map([[WEB, { events: [ev] }]]))(WEB)),
    ["url:b.png", "path:docs/a.png", "url:c.png", "url:e.png", "path:docs/d.png"]);
});

test("figures after the same block go in linkifyFileUris's order: the spaced paths, then the others by where they are mentioned", () => {
  const ev: Ev = { kind: "assistant", uuid: "a1", md: "Both docs/y.png and `my plot.png` and docs/z.png, with\n![c](https://img.example.test/c.png).",
    spacePaths: ["my plot.png"], pathLinks: { "docs/z.png": "docs/z.png", "docs/y.png": "docs/y.png" } };   // z resolved first: key order is not mention order
  assert.deepEqual(show(lift(new Map([[WEB, { events: [ev] }]]))(WEB)), ["url:c.png", "path:my plot.png", "path:docs/y.png", "path:docs/z.png"]);
});

test("each message is read once: more opens lex nothing again, a re-sent transcript neither, an edit re-reads that message alone", () => {
  const evs: Ev[] = Array.from({ length: 6 }, (_, i) => ({ kind: "assistant", uuid: "a" + i,
    md: `Run ${i}:\n\n![r${i}](https://img.example.test/r${i}.png)\n\nThe notes are in docs/f${i}.png`, pathLinks: { [`docs/f${i}.png`]: `docs/f${i}.png` } }));
  evs.push({ kind: "assistant", uuid: "p0", md: "no picture in this one, docs/f0.png", pathLinks: { "docs/f0.png": "docs/f0.png" } });
  const s = { events: evs };
  const lexed = { n: 0 };
  const sub = { events: [{ kind: "assistant", uuid: "s0", md: "![s](https://img.example.test/sub.png)" }] as Ev[] };
  const nav = lift(new Map([[WEB, s], [SUB, sub]]), null, lexed);
  const fresh = () => lift(new Map([[WEB, { events: s.events }]]))(WEB);   // an uncached read of the same events
  const first = nav(WEB);
  assert.equal(lexed.n, 6, "the first open reads each message that names a picture, once");
  assert.deepEqual(first, fresh());
  for (let k = 0; k < 5; k++) assert.deepEqual(nav(WEB), first);
  const fromSub = inside({ ".thread[data-session]": { dataset: { session: SUB } } });
  nav(null, fromSub); nav(null, fromSub);
  assert.equal(lexed.n, 7, "five more opens read nothing again; the subagent viewer's transcript is read once, on its own");
  nav(WEB);
  assert.equal(lexed.n, 7, "…and does not cost the session's");
  s.events = evs.map((e) => ({ ...e }));                // a full frame: the same messages as new objects
  assert.deepEqual(nav(WEB), first);
  assert.equal(lexed.n, 7, "a re-sent transcript's unchanged messages are not read again");
  s.events = s.events.slice();
  s.events[2] = { ...s.events[2], md: s.events[2].md + "\n\n![more](https://img.example.test/more.png)" };   // an edit, or a reply streaming in
  s.events.push({ kind: "assistant", uuid: "a9", md: "![late](https://img.example.test/late.png)" });     // and one new message
  const edited = nav(WEB);
  assert.equal(lexed.n, 9, "the edited message and the new one are read, and nothing else");
  assert.deepEqual(edited, fresh(), "the result is what an uncached read gives");
  assert.deepEqual(show(edited).slice(4, 8), ["url:r2.png", "path:docs/f2.png", "url:more.png", "url:r3.png"]);
  assert.equal(show(edited).at(-1), "url:late.png");
  nav(WEB);
  assert.equal(lexed.n, 9);
});

test("one stop per picture per message; a linked picture, code and a non-image path are no stops; messages oldest first", () => {
  const evs: Ev[] = [
    { kind: "user", uuid: "u1", md: "here's the board: [![card](https://img.example.test/card.png)](https://board.example.test/)" },
    { kind: "assistant", uuid: "a1", md: "![w](https://img.example.test/w.png) again ![w2](https://img.example.test/w.png)\n\n`![x](https://img.example.test/code.png)`\n\nnotes in docs/spec.pdf",
      pathLinks: { "docs/spec.pdf": "docs/spec.pdf" } },
    { kind: "assistant", uuid: "a2", md: "![w](https://img.example.test/w.png)" },
  ];
  const nav = lift(new Map([[WEB, { events: evs }]]))(WEB);
  assert.deepEqual(show(nav), ["url:w.png", "url:w.png"], "the same picture in two messages is two stops, twice in one message one");
});

test("a path picture is its file's stop, a data: picture its URL; a source the sanitizer would not load is no stop", () => {
  // a src with no scheme names a file on the session's disk since 2026-11-12 (md() loads it through the kernel), so the
  // arrows know it as that file, never as the dashboard's own address it once resolved to and 404'd on
  const ev: Ev = { kind: "assistant", md: "![r](/media/chart.png) ![d](data:image/png;base64,AAAA) ![j](javascript:alert(1)) ![f](file:///tmp/x.png)" };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(nav.map((e) => e.url || "path:" + e.path), ["path:/media/chart.png", "data:image/png;base64,AAAA"]);
  assert.equal(nav[0].sid, WEB);
});

test("a picture written as a path is its file's stop where it is written, with the message's pin, never a second figure stop", () => {
  // the kernel verifies the picture's path as it does any path in the text (pathLinks, pathPins), but the page draws the
  // picture where it is written and no figure for that link: one stop, at the picture, pinned as md() loads it
  const ev: Ev = { kind: "assistant", uuid: "a1", md: [
    "The latency split:",
    "",
    "![split](/tmp/notes-api/split.png)",
    "",
    "and the raw counts in docs/figure.png, under home ![home](~/notes-api/hit.png) and relative ![rel](figs/p95.png).",
    "",
    "![web](https://img.example.test/wide.png) ![nox](/tmp/notes-api/plot)",
  ].join("\n"), pathLinks: { "/tmp/notes-api/split.png": "/tmp/notes-api/split.png", "docs/figure.png": "docs/figure.png", "~/notes-api/hit.png": "~/notes-api/hit.png" },
    pathPins: { "/tmp/notes-api/split.png": "v1", "docs/figure.png": "v2" } };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(show(nav), ["path:/tmp/notes-api/split.png@v1", "path:~/notes-api/hit.png", "path:figs/p95.png", "path:docs/figure.png@v2",
    "url:wide.png", "url:https://dash.example.test/tmp/notes-api/plot"],
    "the pictures where they are written, the paragraph's figure after it; a path with no picture extension is no file (md() leaves its src " +
    "as written, the review of #454), so it stands as the page resolves it, and a load that fails drops it from the arrows");
  assert.ok(nav.slice(0, 3).every((e) => e.sid === WEB), "each resolves in the session's folder");
  const remote = lift(new Map([["gpu1:" + WEB, { events: [ev] }]]))("gpu1:" + WEB);
  assert.equal(remote[0].sid, "gpu1:" + WEB, "a remote session's picture is its machine's file");
  assert.equal(remote.at(-1)!.url, "https://dash.example.test/tmp/notes-api/plot", "…and a path to no picture is nobody's file, here or there");
});

test("a user's attached pictures follow the text, and the attached path's mention in the text is no second stop", () => {
  const ev: Ev = { kind: "user", md: "look at /tmp/shot.png and ![ref](https://img.example.test/ref.png)",
    images: [{ src: "path:/tmp/shot.png", path: "/tmp/shot.png" }], pathLinks: { "/tmp/shot.png": "/tmp/shot.png" } };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(show(nav), ["url:ref.png", "path:/tmp/shot.png"]);
});

test("the scope is the transcript the picture sits in: a subagent viewer's own events, its files in the parent's folder", () => {
  const parent: Ev[] = [{ kind: "assistant", md: "![p](https://img.example.test/parent.png)" }];
  const sub: Ev[] = [{ kind: "assistant", md: "![s](https://img.example.test/sub.png)\n\nplotted in docs/figure.png", pathLinks: { "docs/figure.png": "docs/figure.png" } }];
  const nav = lift(new Map([[WEB, { events: parent }], [SUB, { events: sub }]]));
  const fromSub = inside({ ".thread[data-session]": { dataset: { session: SUB } } });
  const got = nav(null, fromSub);
  assert.deepEqual(show(got), ["url:sub.png", "path:docs/figure.png"]);
  assert.equal(got[1].sid, WEB, "the viewer's files belong to its parent session");
  assert.deepEqual(show(nav(WEB, fromSub)), ["url:sub.png", "path:docs/figure.png"], "a path preview in the viewer (its sid the parent's) steps the viewer's pictures too");
  assert.deepEqual(show(nav(WEB)), ["url:parent.png"], "without an element: the sid's chat, as before");
});

test("…and an open comment thread's own messages, its files in its session's folder", () => {
  const thread = { sid: WEB, th: { events: [{ kind: "assistant", md: "![burst](https://img.example.test/burst.png)" }] as Ev[] } };
  const nav = lift(new Map([[WEB, { events: [{ kind: "assistant", md: "![p](https://img.example.test/parent.png)" }] }]]), thread);
  const fromPop = inside({ "#cmt-pop": {}, ".thread[data-session]": null });
  assert.deepEqual(show(nav(null, fromPop)), ["url:burst.png"]);
  assert.deepEqual(nav(null), [], "a URL picture with no element and no sid has no chat");
});
