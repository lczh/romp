// A key and a palette command for commenting on selected chat text (chat.commentSelection, the user 2026-09-30, who
// wanted a keyboard shortcut for adding a comment). Mod+Alt+M, Google Docs' insert-comment key, runs exactly what the
// transcript's right-click menu's Comment runs: the same gate (render.ts commentAim) and the same openCommentComposer.
// Here: the default chord and its freedom from every other default and built-in on both platforms, the keydown a
// browser really spells for it (a Mac's ⌘⌥M names µ), the command's behaviour EXECUTED from render.ts's own source (a
// selection opens the opener with its sid/uuid/text under the selection's box; none toasts and opens nothing; a palette
// run uses what the palette captured as it opened), and the palette's capture executed on the real initPalette over a
// small DOM stand-in. The served half is tests/test_comment_key_served.py. Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { DEFAULT_CHORDS, registerCommand, runCommand } from "./commands";
import { builtInOwner, chordMap, chordOf, conflictOf, dispatchable, displayChord, resolveChord } from "./keybindings";
import { initPalette } from "./palette";

const requireCjs = createRequire(__filename);
const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const MAIN = fs.readFileSync(path.join(ROOT, "ui", "webview", "palette-main.ts"), "utf8");
const ID = "chat.commentSelection";

// ── the key ───────────────────────────────────────────────────────────────────────────────────────
test("the default is Mod+Alt+M in the one table: Ctrl+Alt+M, and ⌘⌥M on a Mac", () => {
  assert.equal(DEFAULT_CHORDS[ID], "Mod+Alt+M");
  assert.equal(resolveChord(DEFAULT_CHORDS[ID], false), "Ctrl+Alt+M");
  assert.equal(resolveChord(DEFAULT_CHORDS[ID], true), "Alt+Meta+M");
  assert.equal(displayChord(DEFAULT_CHORDS[ID], false), "Ctrl+Alt+M");
  assert.equal(displayChord(DEFAULT_CHORDS[ID], true), "⌥⌘M", "Apple's order, ⌥ before ⌘");
});

test("it collides with no other command's default and no built-in, on either platform; chat.navBack's Ctrl+M stays its own", () => {
  const cmds = Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord }));
  for (const mac of [false, true]) {
    assert.equal(builtInOwner(DEFAULT_CHORDS[ID], mac), null, "no built-in owns it" + (mac ? " on a Mac" : ""));
    assert.equal(conflictOf(DEFAULT_CHORDS[ID], ID, cmds, {}, mac), null, "no other default holds it" + (mac ? " on a Mac" : ""));
    assert.notEqual(resolveChord(DEFAULT_CHORDS[ID], mac), resolveChord(DEFAULT_CHORDS["chat.navBack"], mac), "the Alt makes it another chord than Ctrl+M");
    const m = chordMap(cmds, {}, mac);
    assert.equal(m.get(resolveChord(DEFAULT_CHORDS[ID], mac)), ID);
    assert.equal(m.get(resolveChord("Ctrl+M", mac)), "chat.navBack", "chat.navBack still answers to its own key");
    // …and the table as a whole: no two defaults resolve to one chord (a clash would leave one of them dead)
    const resolved = cmds.map((c) => resolveChord(c.chord, mac));
    assert.equal(new Set(resolved).size, resolved.length, "every default distinct" + (mac ? " on a Mac" : "") + ": " + resolved.join(", "));
  }
});

const kd = (key: string, code: string, m: Partial<{ ctrl: boolean; alt: boolean; shift: boolean; meta: boolean }>) =>
  ({ key, code, ctrlKey: !!m.ctrl, altKey: !!m.alt, shiftKey: !!m.shift, metaKey: !!m.meta });

test("the keydown a browser spells matches it: Ctrl+Alt+M on Linux and Windows, ⌘⌥M on a Mac where Option composes µ", () => {
  assert.equal(chordOf(kd("m", "KeyM", { ctrl: true, alt: true })), resolveChord(DEFAULT_CHORDS[ID], false));
  // a Mac: Option+M types µ, and the browser reports that glyph as the key even with ⌘ held; the physical key is the chord's
  assert.equal(chordOf(kd("µ", "KeyM", { alt: true, meta: true })), resolveChord(DEFAULT_CHORDS[ID], true), "⌘⌥M matches its own binding");
  assert.equal(chordOf(kd("Dead", "KeyE", { alt: true, meta: true })), "Alt+Meta+E", "a dead key under ⌘⌥ is its physical key too");
  assert.equal(chordOf(kd("m", "KeyM", { alt: true, meta: true })), "Alt+Meta+M", "a browser that reports the plain letter agrees");
  // without ⌘ the glyph stays the key: Ctrl+Alt is AltGr on Windows, and what it types there is the reader's text
  assert.notEqual(chordOf(kd("µ", "KeyM", { ctrl: true, alt: true })), "Ctrl+Alt+M", "AltGr+M on a German layout types µ; it never comments");
  assert.notEqual(chordOf(kd("µ", "KeyM", { alt: true })), "Alt+M", "Option+M alone types µ on a Mac");
  assert.equal(chordOf(kd("M", "KeyM", { ctrl: true })), "Ctrl+M", "chat.navBack's keydown is untouched");
  assert.equal(dispatchable({ ctrlKey: true, altKey: true, metaKey: false }, true), true, "a real modifier dispatches from a text field");
});

// ── the command, executed from render.ts's own source ─────────────────────────────────────────────
const lift = (name: string): string => {
  const i = RENDER.indexOf(`function ${name}(`);
  assert.ok(i > 0, name);
  let depth = 0, j = RENDER.indexOf("{\n", i);
  for (; j < RENDER.length; j++) { if (RENDER[j] === "{") depth++; else if (RENDER[j] === "}") { depth--; if (!depth) break; } }
  return RENDER.slice(i, j + 1);
};
type Aim = { sid: string; uuid: string; text: string; x: number; y: number };
type World = {
  activeId: string | null;
  sel: { text: string; uuid: string | null } | null;            // what transcriptSelection returns
  live?: boolean;                                                // liveSession(activeId) answers
  rect?: { left: number; bottom: number };                       // the selection's box
  elsewhere?: boolean;                                           // the selection sits in another tab's (hidden) view
};
function chat(w: World) {
  const opened: Array<[string, string, string, number, number]> = [];
  const toasts: string[] = [];
  const ts = [lift("commentAim"), lift("commentOnSelection"), lift("heldCommentAim")].join("\n");
  const src = requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020" }).code;   // the types go, the logic runs as shipped
  const env = {
    activeId: w.activeId,
    transcriptSelection: () => w.sel,
    isProvisionalId: (id: string) => id.startsWith("pending:"),
    liveSession: (id: string) => (w.live === false ? undefined : { id }),
    window: { getSelection: () => ({ rangeCount: 1, getRangeAt: () => ({ startContainer: "the passage", getBoundingClientRect: () => w.rect || { left: 0, bottom: 0 } }) }) },
    views: new Map([[w.activeId, { el: { contains: (n: unknown) => n === "the passage" && !w.elsewhere } }]]),
    openCommentComposer: (sid: string, uuid: string, exact: string, x: number, y: number) => { opened.push([sid, uuid, exact, x, y]); },
    ephemeralWarnToast: (msg: string) => { toasts.push(msg); },
  };
  const api = new Function("env", "const { transcriptSelection, isProvisionalId, liveSession, window, views, openCommentComposer, ephemeralWarnToast } = env;\n"
    + "let activeId = env.activeId;\n" + src + "\nreturn { commentAim, commentOnSelection, heldCommentAim };")(env) as {
    commentAim: () => Aim | "none" | "session"; commentOnSelection: (held?: unknown) => void; heldCommentAim: (v: unknown) => Aim | "none" | "session" };
  return { ...api, opened, toasts };
}
const SID = "11111111-2222-3333-4444-555555555555", UUID = "22222222-3333-4444-5555-666666666666";
const PASSAGE = "The notes-api export streams rows through a bounded buffer.";
const NONE = "Select some text in the chat to comment on it.";

test("with a passage selected in a turn, the command opens the menu's opener with its sid, uuid and text, under the selection", () => {
  const c = chat({ activeId: SID, sel: { text: PASSAGE, uuid: UUID }, rect: { left: 140, bottom: 300 } });
  c.commentOnSelection();
  assert.deepEqual(c.opened, [[SID, UUID, PASSAGE, 140, 306]], "the box's corner at the selection's left, just under it");
  assert.deepEqual(c.toasts, []);
});

test("with nothing usable selected it toasts and opens nothing: no selection, a selection outside a turn, a session not ready", () => {
  for (const [why, w, msg] of [
    ["nothing selected", { activeId: SID, sel: null }, NONE],
    ["outside a transcript turn", { activeId: SID, sel: { text: PASSAGE, uuid: null } }, NONE],
    ["left in another tab's hidden view", { activeId: SID, sel: { text: PASSAGE, uuid: UUID }, elsewhere: true }, NONE],
    ["a tab still being created", { activeId: "pending:web", sel: { text: PASSAGE, uuid: UUID } }, "This session isn't ready for comments yet."],
    ["a skeleton this page has not loaded", { activeId: SID, sel: { text: PASSAGE, uuid: UUID }, live: false }, "This session isn't ready for comments yet."],
  ] as Array<[string, World, string]>) {
    const c = chat(w);
    c.commentOnSelection();
    assert.deepEqual(c.opened, [], why + ": nothing opens");
    assert.deepEqual(c.toasts, [msg], why);
  }
});

test("a palette run uses the aim captured as the palette opened, whatever the selection is by the time it runs", () => {
  const captured = chat({ activeId: SID, sel: { text: PASSAGE, uuid: UUID }, rect: { left: 90, bottom: 210 } }).commentAim();
  assert.deepEqual(captured, { sid: SID, uuid: UUID, text: PASSAGE, x: 90, y: 216 });
  // the palette's input took the focus: the live selection is gone
  const c = chat({ activeId: SID, sel: null });
  c.commentOnSelection(c.heldCommentAim(JSON.parse(JSON.stringify(captured))));   // it crosses to the pane as a posted message
  assert.deepEqual(c.opened, [[SID, UUID, PASSAGE, 90, 216]]);
  assert.deepEqual(c.toasts, []);
  // …and a palette opened over nothing says so, even if something is selected by the time it runs
  const n = chat({ activeId: SID, sel: { text: PASSAGE, uuid: UUID } });
  n.commentOnSelection(n.heldCommentAim("none"));
  assert.deepEqual([n.opened, n.toasts], [[], [NONE]]);
  // a held aim for a tab that is no longer the one shown, or malformed, is nothing selected
  const other = chat({ activeId: "33333333-4444-5555-6666-777777777777", sel: null });
  other.commentOnSelection(other.heldCommentAim(captured));
  assert.deepEqual([other.opened, other.toasts], [[], [NONE]]);
  assert.equal(c.heldCommentAim({ sid: SID, uuid: UUID }), "none", "a partial aim is refused");
  assert.equal(c.heldCommentAim("session"), "session");
});

test("the wiring: the menu's Comment names the key, the chat's capture handler answers it, the shell's message runs it", () => {
  assert.match(RENDER, /mk\("Comment", \(\) => openCommentComposer\(aim\.sid, aim\.uuid, aim\.text, e\.clientX, e\.clientY\), keyHint\("chat\.commentSelection"\) \|\| undefined\);/,
    "the menu's pick keeps its pointer; its sub-line is the key while one is bound");
  const h = RENDER.slice(RENDER.indexOf("// The key (desktop): chat.commentSelection"), RENDER.indexOf("function copyToClipboard("));
  assert.match(h, /if \(!\(e\.ctrlKey \|\| e\.altKey \|\| e\.metaKey\) \|\| e\.repeat \|\| e\.isComposing\) return;/);
  assert.match(h, /if \(ch !== effectiveChord\("chat\.commentSelection", DEFAULT_CHORDS\["chat\.commentSelection"\], loadOverrides\(\), mac\)\) return;\n\s*e\.preventDefault\(\); e\.stopPropagation\(\);\n\s*commentOnSelection\(\);\n\}, true\);/,
    "the overrides read per press, so a rebind moves it; capture phase, so the message box's own keys never see it");
  assert.match(RENDER, /if \(m\.romp === "commentSelection"\) \{ commentOnSelection\("held" in m \? heldCommentAim\(m\.held\) : undefined\); return; \}/);
  assert.match(RENDER, /\(window as any\)\.__rompCommentAim = commentAim;/, "the shell's palette reads the aim through this");
});

test("the shell registers it for the palette and the dispatcher: verb first, capturing the chat's aim, posting to the chat", () => {
  const at = MAIN.indexOf('id: "chat.commentSelection", title: "Comment on selection",');
  assert.ok(at > 0, "registered under the user's words");
  const reg = MAIN.slice(at, MAIN.indexOf("});", at));
  assert.match(reg, /const aim = \(chatPane\(\)\?\.contentWindow as any\)\?\.__rompCommentAim; return typeof aim === "function" \? aim\(\) : undefined;/);
  assert.match(reg, /run: \(held\) => chatPost\(held === undefined \? \{ romp: "commentSelection" \} : \{ romp: "commentSelection", held \}\),/);
  assert.doesNotMatch(reg, /when:/, "listed always: with nothing selected the toast says what to do");
});

// ── the palette's capture, executed on the real initPalette ───────────────────────────────────────
class FakeEl {
  id = ""; hidden = false; className = ""; spellcheck = false; textContent = ""; placeholder = ""; value = "";
  children: FakeEl[] = [];
  listeners: Record<string, Array<(e: any) => void>> = {};
  classList = { toggle: () => {}, add: () => {}, remove: () => {}, contains: () => false };
  style: Record<string, string> = {};
  constructor(public tag: string, private onFocus?: () => void) {}
  appendChild(c: FakeEl): FakeEl { this.children.push(c); return c; }
  addEventListener(t: string, f: (e: any) => void): void { (this.listeners[t] ||= []).push(f); }
  fire(t: string, e: any = {}): void { for (const f of this.listeners[t] || []) f(e); }
  focus(): void { if (this.onFocus) this.onFocus(); }
  scrollIntoView(): void {}
}

test("the palette reads a command's capture BEFORE its input takes the focus, hands it to a run from the palette, and a run by key gets none", () => {
  let state = "at open";
  const got: unknown[] = [];
  registerCommand({ id: "t.capture", title: "Capture probe", capture: () => state, run: (held) => { got.push(held); } });
  const made: FakeEl[] = [];
  const doc = {
    head: new FakeEl("head"), body: new FakeEl("body"),
    createElement: (tag: string) => { const e = new FakeEl(tag, tag === "input" ? () => { state = "after the focus moved"; } : undefined); made.push(e); return e; },
    createDocumentFragment: () => new FakeEl("#fragment"),
    createTextNode: (t: string) => { const e = new FakeEl("#text"); e.textContent = t; return e; },
  };
  const pal = initPalette({}, doc as unknown as Document);
  pal.open();
  assert.equal(state, "after the focus moved", "the stand-in's focus move did disturb the state");
  const input = made.find((e) => e.tag === "input")!;
  const back = made.find((e) => e.id === "rpal-back")!;
  input.value = "Capture probe";
  input.fire("input");
  back.fire("keydown", { key: "Enter", preventDefault: () => {}, stopPropagation: () => {} });
  assert.deepEqual(got, ["at open"], "the palette's run carries what the command read as the palette opened");
  assert.equal(pal.isOpen(), false);
  runCommand("t.capture");
  assert.deepEqual(got, ["at open", undefined], "a run by key carries nothing: the command reads the live state itself");
});
