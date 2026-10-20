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
import { mdImages } from "./md-images";
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

function lift(sessions: Map<string, { events: Ev[] }>, thread: { sid: string; th: { events: Ev[] } } | null = null) {
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
  const H = { sessions, thread, marked, mdImages };
  return (new Function("H", "marked", "mdImages", prelude + code + "\nreturn chatImagesFor;") as
    (h: unknown, m: unknown, f: unknown) => (sid: string | null, from?: Fake | null) => Entry[])(H, marked, mdImages);
}
// a clicked element, as far as the scope reads it: which ancestor it sits under
function inside(sel: Record<string, unknown>): Fake {
  return { closest: (s: string) => (s in sel ? sel[s] : null) };
}
const show = (es: Entry[]) => es.map((e) => e.url ? "url:" + e.url.replace("https://img.example.test/", "") : "path:" + e.path + (e.pin ? "@" + e.pin : ""));

test("within a message: a markdown picture where it is written, a path's figure at the end of the line that names it", () => {
  const ev: Ev = { kind: "assistant", uuid: "a1",
    md: "The run:\n\n![throughput](https://img.example.test/wide.png)\n\nThe split is in docs/figure.png and ![inline](https://img.example.test/inline.png) beside it.\n\n![after](https://img.example.test/after.png)",
    pathLinks: { "docs/figure.png": "docs/figure.png" }, pathPins: { "docs/figure.png": "v1" } };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(show(nav), ["url:wide.png", "url:inline.png", "path:docs/figure.png@v1", "url:after.png"],
    "the inline picture on the mentioning line renders inside its paragraph, the figure after it");
  assert.equal(nav[2].sid, WEB, "the file resolves in the session's folder");
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

test("relative and data: pictures resolve against the page; a source the sanitizer would not load is no stop", () => {
  const ev: Ev = { kind: "assistant", md: "![r](/media/chart.png) ![d](data:image/png;base64,AAAA) ![j](javascript:alert(1)) ![f](file:///tmp/x.png)" };
  const nav = lift(new Map([[WEB, { events: [ev] }]]))(WEB);
  assert.deepEqual(nav.map((e) => e.url), ["https://dash.example.test/media/chart.png", "data:image/png;base64,AAAA"]);
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
