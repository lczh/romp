// QUOTE A FILE, STAGE IT, SEND IT (the user 2026-09-30, who wanted the chat's select-and-stage flow in the file viewer):
// the viewer's message bar. A passage selected in the file becomes a quote chip there, cited to the file and, in a view
// with line rows, to its lines; Cmd/Ctrl+Enter stages it onto the source session's staged stack (the chat composer's
// own), Enter sends it through the same release. The lead-ins (the words romp puts ahead of the quote block, approved by
// the user as drafted):
//   code or raw view, with lines:  Replying to this part of <path> (lines 12–18):
//   rendered document, no lines:   Replying to this part of <path>:
// The pure halves (the lead-in, the body, the rescind's inverse) run on the real modules; the bar itself is lifted out of
// render.ts with the staging code around it (the staged-list-cap.test.ts idiom) and driven over a fake DOM, with the real
// StagedStack and composition under it. Synthetic sessions and paths only (the notes-api demo world).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { StagedStack, quoteReplyBody, quoteLeadIn, stagedRunBody, stagedPosts, isSlashCommand } from "./staged-messages";
import { splitQuoteReplyBody } from "./queued-rescind";
import { mintQid } from "./send-pending";
import { isClearCmd } from "./clear-confirm";

const requireCjs = createRequire(__filename);
const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const VIEW = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "file-view.ts"), "utf8");
const CODE = "def notes():\n    return [\"a\", \"b\"]";

// ── the words ──
test("the lead-ins: a file with lines, a file without, and the editor's and the conversation's unchanged", () => {
  assert.equal(quoteLeadIn({ kind: "file", path: "src/app.py", lines: [12, 18] }), "Replying to this part of src/app.py (lines 12–18):");
  assert.equal(quoteLeadIn({ kind: "file", path: "docs/guide.md", lines: null }), "Replying to this part of docs/guide.md:");
  assert.equal(quoteLeadIn({ kind: "file", path: "src/app.py", lines: [4, 4] }), "Replying to this part of src/app.py (line 4):", "one line says so");
  assert.equal(quoteLeadIn({ src: "src/a.ts:12-12" }), "Replying to this highlighted code (src/a.ts:12-12):", "the editor's flavour keeps its words");
  assert.equal(quoteLeadIn({ src: "src/app.py:1-2", kind: "other" } as never), "Replying to this highlighted code (src/app.py:1-2):", "only kind file is a viewer quote");
  assert.equal(quoteLeadIn({}), "Replying to this part of the conversation:");
});

test("the bodies the session receives, byte for byte: the code view's with its lines, the rendered document's without", () => {
  assert.equal(quoteReplyBody([{ quote: CODE, kind: "file", path: "src/app.py", lines: [1, 2] }], "Why a list here?"),
    "Replying to this part of src/app.py (lines 1–2):\n> def notes():\n>     return [\"a\", \"b\"]\n\nWhy a list here?");
  assert.equal(quoteReplyBody([{ quote: "One rule per line.", kind: "file", path: "docs/guide.md", lines: null }], "Is that a hard rule?"),
    "Replying to this part of docs/guide.md:\n> One rule per line.\n\nIs that a hard rule?");
});

test("a file's quote and a chat quote release together, each with its own lead-in, in stage order", () => {
  const body = stagedRunBody([{ text: "this one", cites: [{ quote: "One rule per line.", kind: "file", path: "docs/guide.md", lines: null }] },
                              { text: "and this", cites: [{ quote: "It keeps the web side tidy." }] }], { text: "thoughts?" });
  assert.equal(body, "Replying to this part of docs/guide.md:\n> One rule per line.\n\nthis one\n\n"
    + "Replying to this part of the conversation:\n> It keeps the web side tidy.\n\nand this\n\nthoughts?");
});

test("the words speak as the person, never romp's machinery (the injected-voice rule)", () => {
  // the ban list tests/test_injected_voice.py holds the kernel's bodies to; these bodies are the page's own
  const words = ["romp", "card", "board", "goal", "cleared", "dismissal", "status check", "nudge", "viewer", "tab", "staged"];
  for (const lead of [quoteLeadIn({ kind: "file", path: "src/app.py", lines: [12, 18] }), quoteLeadIn({ kind: "file", path: "docs/guide.md" })]) {
    const prose = lead.replace(/ (src\/app\.py|docs\/guide\.md)/, " FILE");
    for (const w of words) assert.ok(!new RegExp("\\b" + w + "\\b", "i").test(prose), "\"" + w + "\" in: " + lead);
  }
});

test("a queued file quote pulled back to the box comes back as the same chip: the path, the lines, the label", () => {
  const body = quoteReplyBody([{ quote: CODE, kind: "file", path: "src/app.py", lines: [1, 2] },
                               { quote: "One rule per line.", kind: "file", path: "docs/guide.md", lines: null },
                               { quote: "x = 1", kind: "file", path: "src/app.py", lines: [7, 7] }], "why?");
  const back = splitQuoteReplyBody(body);
  assert.equal(back.text, "why?");
  assert.deepEqual(back.cites, [
    { quote: CODE, kind: "file", path: "src/app.py", lines: [1, 2], src: "src/app.py:1-2" },
    { quote: "One rule per line.", kind: "file", path: "docs/guide.md", lines: null, src: "docs/guide.md" },
    { quote: "x = 1", kind: "file", path: "src/app.py", lines: [7, 7], src: "src/app.py:7" }]);
  assert.deepEqual(splitQuoteReplyBody("Replying to this part of the conversation:\n> hi\n\nok").cites, [{ quote: "hi" }], "the conversation's lead is never read as a file");
});

// ── the viewer's side: the bar is lent, the chip is cited, the lines come from the rows ──
test("the viewer lends its foot to the chat's bar, seeds its chip with the rows' lines, and stands it down while editing", () => {
  assert.match(VIEW, /const compose = composeBarOf && sid \? composeBarOf\(composeHost, \{ sid, path \}\) : null;\n\s*if \(compose\) box\.appendChild\(composeHost\);/);
  assert.match(VIEW, /compose\.seed\(picked, selectionLines\(sel\), quoteAdd\);/, "the selection becomes the bar's chip, with its lines");
  assert.match(VIEW, /const rows = Array\.from\(body\.querySelectorAll\("code\.hljs \.fv-cl"\)\);/, "the lines are the code view's rows (a rendered document has none)");
  assert.match(VIEW, /compose\?\.setEditing\(editing\);/);
  assert.match(RENDER, /setFileViewComposer\(\(host, ctx\) => mountViewerBar\(host, ctx\.sid, ctx\.path\)\);/, "the chat document lends its composer");
});

// ── the bar, executed ──
class FakeEl {
  tag: string; className: string; children: FakeEl[] = []; parent: FakeEl | null = null;
  attrs: Record<string, string> = {}; listeners: Record<string, Function[]> = {};
  textContent = ""; title = ""; value = ""; placeholder = ""; rows = 0; type = ""; hidden = false; scrollHeight = 20;
  style: Record<string, string> = {};
  classList: { add(...c: string[]): void; remove(...c: string[]): void; contains(c: string): boolean };
  constructor(tag: string, cls = "") {
    this.tag = tag; this.className = cls;
    const self = this;
    this.classList = {
      add(...c: string[]) { for (const x of c) if (!self.className.split(" ").includes(x)) self.className = (self.className + " " + x).trim(); },
      remove(...c: string[]) { self.className = self.className.split(" ").filter((y) => !c.includes(y)).join(" "); },
      contains(x: string) { return self.className.split(" ").includes(x); },
    };
  }
  get isConnected(): boolean { let n: FakeEl | null = this; while (n) { if (n.tag === "root") return true; n = n.parent; } return false; }
  appendChild(c: FakeEl): FakeEl { c.parent = this; this.children.push(c); return c; }
  append(...cs: FakeEl[]): void { for (const c of cs) this.appendChild(c); }
  replaceChildren(...cs: FakeEl[]): void { this.children = []; this.append(...cs); }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
  addEventListener(t: string, f: Function): void { (this.listeners[t] ??= []).push(f); }
  fire(t: string, ev: Record<string, unknown> = {}): void { for (const f of this.listeners[t] ?? []) f({ preventDefault() {}, stopPropagation() {}, ...ev }); }
  focus(): void { /* nothing to focus */ }
  find(cls: string): FakeEl | null { for (const c of this.children) { if (c.classList.contains(cls)) return c; const d = c.find(cls); if (d) return d; } return null; }
}
type Posted = { type: string; [k: string]: unknown };
type Hooks = {
  FakeEl: typeof FakeEl; StagedStack: typeof StagedStack; quoteReplyBody: typeof quoteReplyBody; stagedPosts: typeof stagedPosts; mintQid: typeof mintQid;
  isClearCmd: typeof isClearCmd; isSlashCommand: typeof isSlashCommand; posted: Posted[]; optimistic: { sid: string; text: string }[];
  toasts: string[]; strips: (string | null)[]; down: Set<string>; activeId: string | null; mkQuote: string;
};
type Bar = { seed(quote: string, lines: [number, number] | null, add: boolean): void; setEditing(on: boolean): void };
type Api = { mountViewerBar: (host: FakeEl, sid: string, path: string) => Bar; stagedMsgs: StagedStack; flushStaged: (sid: string, typed?: { text: string; cites?: unknown[] }) => number };

function lift(): (hooks: Hooks) => Api {
  const a = RENDER.indexOf("function routeUserMessage("), b = RENDER.indexOf("\nfunction beginComposerEdit(");
  assert.ok(a > 0 && b > a && RENDER.slice(a, b).includes("function mountViewerBar("), "the bar sits in the staging block, before beginComposerEdit; re-anchor");
  const mq = RENDER.slice(RENDER.indexOf("const QUOTE_CAP = "), RENDER.indexOf("\n}\n", RENDER.indexOf("function mkQuoteCitation(")) + 3);
  const js = requireCjs("esbuild").transformSync(mq + RENDER.slice(a, b), { loader: "ts" }).code;
  const prelude = `
    const H = HOOKS;
    const el = (tag, cls) => new H.FakeEl(tag, cls);
    const document = { createTextNode: (t) => { const n = new H.FakeEl("#text"); n.textContent = t; return n; }, getElementById: () => null, activeElement: null };
    const stagedMsgs = new H.StagedStack();
    const stagedOpen = new Set(), stagedCollapsed = new Set(), stagedScroll = new Map();
    const quoteReplyBody = H.quoteReplyBody, stagedPosts = H.stagedPosts, mintQid = H.mintQid, isClearCmd = H.isClearCmd, isSlashCommand = H.isSlashCommand;
    const liveSession = () => ({ status: { backend: "sdk" } });
    const vscodeApi = { postMessage: (m) => { const { qid, ...frame } = m; H.posted.push(frame); } };
    const registerOptimistic = (sid, text) => { H.optimistic.push({ sid, text }); };
    const persistDrafts = () => {};
    const hostIsDown = (id) => H.down.has(id); const isProvisionalId = () => false;
    const warnToast = (m) => { H.toasts.push(m); }; const ephemeralWarnToast = (m) => { H.toasts.push(m); };
    const syncComposerPh = () => {}; const liveAsks = new Map();
    const sessions = new Map([["web-sid", { name: "web", color: { bg: "#9cd2ff", fg: "#0c1a2e" } }]]); const tabMeta = new Map();
    const hostStub = () => null;
    const sessionLabelNodes = (name) => { const n = new H.FakeEl("span", "session-name"); n.textContent = name; return [n]; };
    let activeId = H.activeId;
  `;
  // the chat's strip: recorded, not painted (staged-list-cap.test.ts paints it); the bar's own line reads the stack
  const epilogue = `
    const realStrip = renderStagedStrip;
    return { mountViewerBar, stagedMsgs, flushStaged };
  `;
  return new Function("HOOKS", prelude + js + epilogue) as (hooks: Hooks) => Api;
}
const SID = "web-sid";
function world(active: string | null = SID) {
  const H: Hooks = { FakeEl, StagedStack, quoteReplyBody, stagedPosts, mintQid, isClearCmd, isSlashCommand, posted: [], optimistic: [], toasts: [], strips: [],
                     down: new Set(), activeId: active, mkQuote: "" };
  const api = lift()(H);
  const root = new FakeEl("root");
  const host = new FakeEl("div", "fileview-compose");
  root.appendChild(host);
  const bar = api.mountViewerBar(host, SID, "src/app.py");
  const ta = host.find("fv-compose-input")!, chips = host.find("fv-compose-chips")!, note = host.find("fv-compose-note")!, send = host.find("fv-compose-send")!;
  const type = (t: string) => { ta.value = t; ta.fire("input"); };
  const key = (mods: Record<string, boolean> = {}) => ta.fire("keydown", { key: "Enter", shiftKey: false, isComposing: false, metaKey: false, ctrlKey: false, ...mods });
  return { H, api, host, bar, ta, chips, note, send, type, key };
}
const sends = (H: Hooks) => H.posted.filter((m) => m.type === "sendMessage");

test("the bar names its session and holds one chip per selection gesture; a Cmd/Ctrl gesture adds one; identical text never lists twice", () => {
  const w = world();
  assert.equal(w.host.find("fv-compose-to")!.children.map((c) => c.textContent).join(""), "to web");
  assert.equal(w.chips.hidden, true, "no chip yet");
  w.bar.seed(CODE, [1, 2], false);
  assert.equal(w.chips.children.length, 1);
  assert.equal(w.chips.children[0].find("composer-chip-label")!.textContent.startsWith("src/app.py:1-2 — def notes():"), true, "labelled with the file and its lines");
  w.bar.seed("print(notes())", [6, 6], false);
  assert.equal(w.chips.children.length, 1, "a plain gesture replaces the chip");
  w.bar.seed(CODE, [1, 2], true);
  w.bar.seed(CODE, [1, 2], true);
  assert.equal(w.chips.children.length, 2, "a Cmd/Ctrl gesture adds; the same passage again collapses to one");
  w.bar.setEditing(true);
  assert.equal(w.host.hidden, true, "editing: the bar stands down");
});

test("Cmd/Ctrl+Enter stages the chip and the words onto the SOURCE session's stack, the chat composer's own", () => {
  const w = world("other-sid");   // the chat is on another session: the file's session still gets it
  w.bar.seed(CODE, [1, 2], false);
  w.type("Why a list here?");
  w.key({ metaKey: true });
  const staged = w.api.stagedMsgs.list(SID);
  assert.equal(staged.length, 1, "on the file's session's stack");
  assert.equal(staged[0].text, "Why a list here?");
  assert.deepEqual((staged[0].cites as any[]).map((c) => [c.kind, c.path, c.lines, c.quote]), [["file", "src/app.py", [1, 2], CODE]]);
  assert.equal(w.api.stagedMsgs.count("other-sid"), 0, "never the chat's active session");
  assert.equal(w.ta.value, "", "the box clears"); assert.equal(w.chips.hidden, true, "the chip moved onto the staged item");
  assert.ok(w.note.textContent.startsWith("1 staged"), "the bar says what is held: " + w.note.textContent);
  assert.deepEqual(sends(w.H), [], "nothing sent");
  w.key({ ctrlKey: true });
  assert.equal(w.api.stagedMsgs.count(SID), 1, "an empty box with no chip stages nothing");
});

test("Enter sends through the chat's release: the staged run (a chat quote among it) and then the typed message, as one body to the source session", () => {
  const w = world();
  w.api.stagedMsgs.push(SID, { text: "and this", cites: [{ quote: "It keeps the web side tidy.", title: "t" }] });   // staged from the chat
  w.bar.seed("One rule per line.", null, false);
  w.type("this one");
  w.key({ metaKey: true });
  w.bar.seed(CODE, [1, 2], false);
  w.type("Why a list here?");
  w.key();
  const out = sends(w.H);
  assert.equal(out.length, 1, "one message");
  assert.equal(out[0].id, SID);
  assert.equal(out[0].text,
    "Replying to this part of the conversation:\n> It keeps the web side tidy.\n\nand this\n\n"
    + "Replying to this part of src/app.py:\n> One rule per line.\n\nthis one\n\n"
    + "Replying to this part of src/app.py (lines 1–2):\n> def notes():\n>     return [\"a\", \"b\"]\n\nWhy a list here?");
  assert.deepEqual(w.H.optimistic.map((o) => o.sid), [SID], "the session's own bubble, at once");
  assert.equal(w.api.stagedMsgs.count(SID), 0, "the stack released");
  assert.equal(w.note.textContent, "Sent to web.", "acknowledged in place");
  assert.equal(w.ta.value, "");
});

test("an empty Enter with a stack releases it; a slash command and an unreachable session are refused, the words kept", () => {
  const w = world();
  w.api.stagedMsgs.push(SID, { text: "held", cites: [] });
  w.key();
  assert.deepEqual(sends(w.H).map((m) => m.text), ["held"]);
  w.type("/clear");
  w.key();
  assert.equal(sends(w.H).length, 1, "no slash command from the bar");
  assert.equal(w.ta.value, "/clear", "…and the box keeps it");
  assert.ok(w.H.toasts.some((t) => /slash command/.test(t)));
  w.H.down.add(SID);
  w.type("are you there?");
  w.key();
  assert.equal(sends(w.H).length, 1, "a session on a host that is down is not sent to");
  assert.equal(w.ta.value, "are you there?");
});

test("the draft and the chips outlive the bar: a file tab's view is rebuilt on each visit", () => {
  const w = world();
  w.bar.seed(CODE, [1, 2], false);
  w.type("half a thought");
  const host2 = new FakeEl("div"); const root = new FakeEl("root"); root.appendChild(host2);
  w.api.mountViewerBar(host2, SID, "src/app.py");
  assert.equal(host2.find("fv-compose-input")!.value, "half a thought");
  assert.equal(host2.find("fv-compose-chips")!.children.length, 1);
});
