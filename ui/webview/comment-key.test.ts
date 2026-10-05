// A key and a palette command for commenting on selected chat text (chat.commentSelection, the user 2026-09-30, who
// wanted a keyboard shortcut for adding a comment, then wanted it two keys rather than three). Mod+/ (⌘/ on a Mac, Ctrl+/
// elsewhere; the code editors' toggle-comment key) runs exactly what the transcript's right-click menu's Comment runs: the
// same gate (render.ts commentAim) and the same openCommentComposer. Here: the default chord and its freedom from every
// other default and built-in on both platforms, the keydown a browser really spells for it on a US layout and on one that
// types "/" with Shift (German: Shift+7), and a Mac's ⌘⇧/ as w3c-keyname's source reads it (not run on a Mac), which keeps
// its Shift, as does a "/" typed with Shift alone and a saved "/" chord with Meta, Shift and Ctrl or Alt;
// the chat's key handler EXECUTED from render.ts's own source (it takes the chord with its default prevented, so the
// message box never gets the "/" that would open its slash menu); what it and the shell's dispatcher (EXECUTED from
// palette-main.ts's own source) leave to others: the code editor's own toggle comment, and a chord another command's saved
// binding runs on instead, a Ctrl+Shift+/ or Shift+Meta+/ saved before #39 included (the review of #39): one of the chat's
// own commands (the nav trail, the jump moves) on any page where its handler reads the chord, or inside the shell the
// command the shell's dispatcher picks (palette-main.ts chordPick, executed); the chat document's key handlers and the
// shell's dispatcher composed, so no key runs two commands in the stores tried here, apart from the residual
// keybindings' yieldsTo discloses (the chat's nav and jump handlers ignore the dispatcher's pick), which "the disclosed
// residual" test pins as it stands; the Mac's ⌘⌥ physical-key read kept for a chord the reader binds; the menu's key
// hint (the menu line's own expression, executed) and the palette's chips (the palette's own kbdFor, executed) per
// platform and under saved bindings, the tooltips' hints left as they were; the dialog's yield marks following the
// dispatcher's own pick, and the recorder's reassign freeing the chord from the chat's own handlers too; the command's
// behaviour EXECUTED from render.ts's own source (a selection opens the opener with its sid/uuid/text under the
// selection's box; none toasts and opens nothing; a palette run uses what the palette captured as it opened), and the
// palette's capture executed on the real initPalette over a small DOM stand-in. The served half is
// tests/test_comment_key_served.py. Synthetic text only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { DEFAULT_CHORDS, registerCommand, runCommand } from "./commands";
import { bindable, builtInOwner, chordMap, chordOf, conflictOf, dispatchable, displayChord, effectiveChord, keyHint, resolveChord, titleWithKey, yieldsTo } from "./keybindings";
import * as keybindings from "./keybindings";
import { MOVES, MOVE_TITLE } from "./jump-nav";
import { initPalette } from "./palette";

const requireCjs = createRequire(__filename);
const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const MAIN = fs.readFileSync(path.join(ROOT, "ui", "webview", "palette-main.ts"), "utf8");
const ID = "chat.commentSelection";

// ── the key ───────────────────────────────────────────────────────────────────────────────────────
test("the default is Mod+/ in the one table: Ctrl+/, and ⌘/ on a Mac", () => {
  assert.equal(DEFAULT_CHORDS[ID], "Mod+/");
  assert.equal(resolveChord(DEFAULT_CHORDS[ID], false), "Ctrl+/");
  assert.equal(resolveChord(DEFAULT_CHORDS[ID], true), "Meta+/");
  assert.equal(displayChord(DEFAULT_CHORDS[ID], false), "Ctrl+/");
  assert.equal(displayChord(DEFAULT_CHORDS[ID], true), "⌘/");
  assert.equal(bindable(resolveChord(DEFAULT_CHORDS[ID], false)), true);
});

test("it collides with no other command's default and no built-in, on either platform; chat.split's Mod+\\ stays its own", () => {
  const cmds = Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord }));
  for (const mac of [false, true]) {
    assert.equal(builtInOwner(DEFAULT_CHORDS[ID], mac), null, "no built-in owns it" + (mac ? " on a Mac" : ""));
    assert.equal(conflictOf(DEFAULT_CHORDS[ID], ID, cmds, {}, mac), null, "no other default holds it" + (mac ? " on a Mac" : ""));
    const m = chordMap(cmds, {}, mac);
    assert.equal(m.get(resolveChord(DEFAULT_CHORDS[ID], mac)), ID);
    assert.equal(m.get(resolveChord("Mod+\\", mac)), "chat.split", "the neighbouring editor key still splits");
    assert.equal(m.get(resolveChord("Ctrl+M", mac)), "chat.navBack", "chat.navBack still answers to its own key");
    // …and the table as a whole: no two defaults resolve to one chord (a clash would leave one of them dead)
    const resolved = cmds.map((c) => resolveChord(c.chord, mac));
    assert.equal(new Set(resolved).size, resolved.length, "every default distinct" + (mac ? " on a Mac" : "") + ": " + resolved.join(", "));
  }
});

const kd = (key: string, code: string, m: Partial<{ ctrl: boolean; alt: boolean; shift: boolean; meta: boolean }>) =>
  ({ key, code, ctrlKey: !!m.ctrl, altKey: !!m.alt, shiftKey: !!m.shift, metaKey: !!m.meta });

test("the keydown a browser spells matches it: Ctrl+/ and ⌘/ on a US layout, and off a Mac wherever the layout types \"/\" with Shift", () => {
  const pc = resolveChord(DEFAULT_CHORDS[ID], false), mac = resolveChord(DEFAULT_CHORDS[ID], true);
  // a US (UK, Japanese…) keyboard: "/" has a key of its own, no Shift
  assert.equal(chordOf(kd("/", "Slash", { ctrl: true })), pc);
  assert.equal(chordOf(kd("/", "Slash", { meta: true })), mac);
  // a German, Nordic or Spanish keyboard types "/" as Shift+7, a French one as Shift+":": the character is the chord, so the
  // printed Ctrl+/ is the key to press there too
  assert.equal(chordOf(kd("/", "Digit7", { ctrl: true, shift: true })), pc, "Ctrl+Shift+7 on a German layout");
  assert.equal(chordOf(kd("/", "Period", { ctrl: true, shift: true })), pc, "Ctrl+Shift+: on a French layout");
  // with ⌘ held a Mac's browser reports the key as if Shift were up (w3c-keyname's source says so; not run on a Mac), so a
  // German Mac's ⌘⇧7 arrives as "7", not "/": it never was ⌘/ (the printed ⌘/ needs the numpad or a rebind there)
  assert.equal(chordOf(kd("7", "Digit7", { meta: true, shift: true })), "Shift+Meta+7", "⌘⇧7 on a German Mac");
  // the physical key a US keyboard has "/" on is not the chord: on a German layout it types "-", and Ctrl+- zooms out
  assert.equal(chordOf(kd("-", "Slash", { ctrl: true })), "Ctrl+-");
  // Shift+/ on a US keyboard spells "?": another chord (the editors' block-comment key), never this one
  assert.equal(chordOf(kd("?", "Slash", { ctrl: true, shift: true })), "Ctrl+Shift+?");
  // only "/": Shift stays a modifier on every other key
  assert.equal(chordOf(kd("O", "KeyO", { meta: true, shift: true })), resolveChord(DEFAULT_CHORDS["session.new"], true), "session.new's ⌘⇧O is untouched");
  assert.equal(chordOf(kd("M", "KeyM", { ctrl: true })), "Ctrl+M", "chat.navBack's keydown is untouched");
  // "/" typed with Shift alone keeps its Shift (the "/" rule is for a chord with Ctrl or Alt, and without the Shift it would
  // be the bare "/" a US keyboard types); it is typing, so it never dispatches from a text field
  assert.equal(chordOf(kd("/", "Digit7", { shift: true })), "Shift+/");
  assert.equal(chordOf(kd("/", "Slash", {})), "/", "a US keyboard's bare \"/\"");
  assert.equal(bindable("/"), false, "a bare \"/\" would fire while typing");
  assert.equal(dispatchable({ ctrlKey: true, altKey: false, metaKey: false }, true), true, "a real modifier dispatches from a text field");
  assert.equal(dispatchable({ ctrlKey: false, altKey: false, metaKey: false }, true), false, "Shift+7 in a text field types its \"/\"");
});

test("the Mac's ⌘⌥ physical-key read stays, for a chord the reader binds: ⌘⌥M saved in the dialog still matches the µ keydown", () => {
  const cmds = Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord }));
  const ov = { [ID]: "Mod+Alt+M" };
  const bound = effectiveChord(ID, DEFAULT_CHORDS[ID], ov, true);
  assert.equal(bound, "Alt+Meta+M");
  // Option+M types µ, and the browser reports that glyph as the key even with ⌘ held; the physical key is the chord's
  assert.equal(chordOf(kd("µ", "KeyM", { alt: true, meta: true })), bound, "⌘⌥M matches its own binding");
  assert.equal(chordMap(cmds, ov, true).get(bound), ID, "the dispatcher sends it to the command");
  assert.equal(chordOf(kd("Dead", "KeyE", { alt: true, meta: true })), "Alt+Meta+E", "a dead key under ⌘⌥ is its physical key too");
  assert.equal(chordOf(kd("m", "KeyM", { alt: true, meta: true })), "Alt+Meta+M", "a browser that reports the plain letter agrees");
  // without ⌘ the glyph stays the key: Ctrl+Alt is AltGr on Windows, and what it types there is the reader's text
  assert.notEqual(chordOf(kd("µ", "KeyM", { ctrl: true, alt: true })), "Ctrl+Alt+M", "AltGr+M on a German layout types µ; it never comments");
  assert.notEqual(chordOf(kd("µ", "KeyM", { alt: true })), "Alt+M", "Option+M alone types µ on a Mac");
});

// ── the key handlers, executed from their own source ─────────────────────────────────────────────
// (legalComments "none": esbuild keeps //!, /*!, @license and @preserve comments by default, and this harness reads the
// transform's output as code with no comments in it)
const esb = (ts: string): string => requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020", legalComments: "none" }).code;
const isMac = (platform: string): boolean => /Mac|iP(hone|ad|od)/.test(platform);
// a named function lifted whole from a file's source, by counting its braces from the first "{" after its name that ends
// a line (the body's opening brace, which this code base writes last on the header's line; a comment after that brace
// would move the count's start and break the lift)
const liftFrom = (text: string, name: string): string => {
  const i = text.indexOf(`function ${name}(`);
  assert.ok(i > 0, name);
  let depth = 0, j = text.indexOf("{\n", i);
  for (; j < text.length; j++) { if (text[j] === "{") depth++; else if (text[j] === "}") { depth--; if (!depth) break; } }
  return text.slice(i, j + 1);
};
// render.ts's helpers for the comment key's handler and its menu line, lifted as shipped: whichever of them the file
// defines (a handler that calls one the file lacks fails at the press, loudly), and jumpCommand, the jump moves' ids
const JUMP_COMMAND = (() => { const m = /\nconst jumpCommand = [^\n]*\n/.exec(RENDER); assert.ok(m, "render.ts's jumpCommand"); return m[0]; })();
const chatHelpers = (): string => JUMP_COMMAND + ["chatKeyCommands", "commentKeyTakenBy", "commentKeyHint"]
  .filter((n) => RENDER.includes(`function ${n}(`)).map((n) => liftFrom(RENDER, n)).join("\n");
// The shell's command list in these tests: every command with a default, in the table's order (the comment command last),
// then three the shell registers with none, in the shell's own order. That is not the shell's registration order as a
// whole (palette-main.ts registers the split and tab pairs, for one, after the panels), but it is for the commands these
// tests save together: the nav trail, then the jump moves, then the comment command, then Open the log, Remote kernels and
// Token usage. So of two of those saved on one chord, the later one here is the later one there.
const SHELL_CMDS: Array<{ id: string; chord?: string }> = [
  ...Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord })), { id: "log.open" }, { id: "net.open" }, { id: "usage.open" },
];
// What the shell answers the chat through __rompChordPick: palette-main.ts's chordPick, lifted and run over a command list
function shellPick(platform: string, overrides: () => Record<string, string>, cmds: Array<{ id: string; chord?: string }>) {
  const env: Record<string, unknown> = { chordMap, commandList: () => cmds, loadOverrides: overrides, mac: isMac(platform) };
  return new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(liftFrom(MAIN, "chordPick")) + "\nreturn chordPick;")(env) as (ch: string) => string | null;
}
// The shell's window as the chat sees it through window.parent: what palette-main.ts hands it under __rompChordPick, the
// name render.ts's commentKeyTakenBy reads. Every line of palette-main.ts's code that names it (read from esbuild's
// transform of the file, which drops its comments, so a comment naming it, whole-line, trailing or in a block, is not
// such a line) runs on its own against a stand-in for the shell's window (palette-main.ts's `w`, and `window`), with
// chordPick lifted from the same file, so this keys on the name the two files share and not on one spelling of the
// assignment. A palette-main.ts that hands nothing under that name leaves the stand-in empty (and lifts nothing, so one
// from before chordPick reads the same way), as a shell whose script has not run exposes nothing, and a line naming it
// that cannot run on its own (a hand-off spelled over several lines, say) fails here, saying so, rather than leaving the
// chat without its parent.
let mainCode: string | null = null;
function shellWindow(platform: string, overrides: () => Record<string, string>, cmds: Array<{ id: string; chord?: string }>): Record<string, unknown> {
  const w: Record<string, unknown> = {};
  const lines = (mainCode ??= esb(MAIN)).split("\n").filter((l) => l.includes("__rompChordPick"));
  if (!lines.length) return w;
  const env = { w, window: w, chordPick: shellPick(platform, overrides, cmds) };
  for (const line of lines) {
    try {
      new Function("env", "const { w, window, chordPick } = env;\n" + esb(line))(env);
    } catch (e) {
      assert.fail("palette-main.ts names __rompChordPick on a line that does not run on its own, so this harness cannot read what the shell hands the chat: " + line.trim() + " (" + e + ")");
    }
  }
  return w;
}
// The world the chat's code runs in: the real keybindings module, `overrides` as the store (its loadOverrides returns a
// fresh copy on every call, as the real one parses localStorage on every call, so a test that edits the store between
// presses sees whether the code reads it per press), the platform (the page's navigator here, and the global one while
// a key is pressed: withPlatform), and whether a shell is around the page (then the parent window is shellWindow's,
// whose __rompChordPick answers with the shell's own pick over SHELL_CMDS and `extra`, unless `booted` is false: a
// shell whose script has not run yet).
function chatEnv(platform: string, overrides: Record<string, string>, shell: boolean, extra: Array<{ id: string; chord?: string }>, listeners: Array<{ type: string; capture: unknown; fn: (e: any) => void }>, booted = true) {
  const ov = () => ({ ...overrides });
  const parent = shell && booted ? shellWindow(platform, ov, [...SHELL_CMDS, ...extra]) : {};
  return {
    ...keybindings, DEFAULT_CHORDS, MOVES,
    window: { parent, addEventListener: (type: string, fn: (e: any) => void, capture: unknown) => { listeners.push({ type, capture, fn }); } },
    navigator: { platform }, loadOverrides: ov, inRompShell: () => shell,
  } as Record<string, unknown>;
}
// render.ts's top-level window keydown listeners, each sliced from its opening line through the end of its call (the
// handler's body by counting its braces, then the call's own closing parenthesis, with the capture flag when it has one),
// with where it sits (the order the listeners register in, at the top level of the module). The slices must not overlap:
// a brace inside a string or a regex that threw the count off would run one listener's slice into the next.
const TOP_KEYDOWN: Array<{ src: string; at: number }> = (() => {
  const head = '\nwindow.addEventListener("keydown", (e) => {';
  const out: Array<{ src: string; at: number }> = [];
  for (let i = RENDER.indexOf(head); i >= 0; i = RENDER.indexOf(head, i + 1)) {
    const from = i + 1;
    let depth = 0, j = from + head.length - 2;
    for (; j < RENDER.length; j++) { if (RENDER[j] === "{") depth++; else if (RENDER[j] === "}") { depth--; if (!depth) break; } }
    out.push({ src: RENDER.slice(from, RENDER.indexOf(");", j) + 2), at: from });
  }
  out.forEach((l, k) => assert.ok(k === 0 || out[k - 1].at + out[k - 1].src.length <= l.at, "render.ts's keydown listener at " + l.at + " starts inside the one before it"));
  return out;
})();
// the one of them whose body holds `code`: a handler is found by what it reads, not by the comment above it
function listenerSrc(code: string): { src: string; at: number } {
  const hits = TOP_KEYDOWN.filter((l) => l.src.includes(code));
  assert.equal(hits.length, 1, "exactly one top-level keydown listener of render.ts holds " + code + ", not " + hits.length);
  return hits[0];
}
const COMMENT_KEY = 'effectiveChord("chat.commentSelection"';
const NAV_KEYS = 'effectiveChord("chat.navBack"';
const JUMP_KEYS = "jumpCommand(";

// The chat's own comment-key handler, lifted from render.ts and run over a stand-in window: what it takes, and that it
// takes it in the capture phase with its default prevented, before the message box (whose slash menu opens on the box's
// input) or any other handler sees the keystroke.
type Mods = Partial<{ ctrl: boolean; alt: boolean; shift: boolean; meta: boolean; repeat: boolean }>;
function keyHandler(platform: string, overrides: Record<string, string> = {}, shell = true, extra: Array<{ id: string; chord?: string }> = [], booted = true) {
  const added: Array<{ type: string; capture: unknown; fn: (e: any) => void }> = [];
  let runs = 0;
  const env = { ...chatEnv(platform, overrides, shell, extra, added, booted), commentOnSelection: () => { runs++; } };
  new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(chatHelpers() + "\n" + listenerSrc(COMMENT_KEY).src))(env);
  assert.equal(added.length, 1);
  assert.equal(added[0].type, "keydown");
  assert.equal(added[0].capture, true, "the capture phase: it answers before the message box's own keydown and input");
  // `target` is what the keydown is aimed at (null: the transcript or the message box, anything outside the code editor)
  const press = (key: string, code: string, m: Mods, target: unknown = null) => {
    const e = { ...kd(key, code, m), repeat: !!m.repeat, isComposing: false, target, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    const before = runs;
    withPlatform(platform, () => added[0].fn(e));   // the page's own navigator too, so code that read it would see this platform
    return { ran: runs > before, prevented: e.prevented, stopped: e.stopped };
  };
  return press;
}
const TAKEN = { ran: true, prevented: true, stopped: true };
const LEFT = { ran: false, prevented: false, stopped: false };
// a keydown's target, given as itself and its ancestors in the form the code's selectors name them (a tag, a class, an id,
// an attribute selector): only closest() is read, and it answers a selector list as the DOM's does, when any part of it
// names the target or an ancestor
const at = (chain: string[]) => ({ closest: (sel: string) => (sel.split(",").some((part) => chain.includes(part.trim())) ? { chain } : null) });
// a line in the file viewer's code editor: CodeMirror's contenteditable content, .cm-content, inside .cm-editor
const IN_EDITOR = at([".cm-line", ".cm-content", "[contenteditable=true]", ".cm-scroller", ".cm-editor", "#romp-fileview"]);
// the editor's search field: its panel sits inside .cm-editor but outside .cm-content
const IN_SEARCH = at(["input", ".cm-textfield", ".cm-search", ".cm-panel", ".cm-panels", ".cm-editor", "#romp-fileview"]);
// the file viewer outside its editor: a button on its bar, and its read mode's highlighted text (highlight.js, no
// CodeMirror), in the viewer the chat and the Files pane open and in a file tab's
const IN_VIEWER_BAR = at(["button", ".fileview-bar", "#romp-fileview"]);
const IN_VIEWER_TEXT = at(["code", "pre", ".fileview-body", "#romp-fileview"]);
const IN_FILE_TAB_TEXT = at(["code", "pre", ".fileview-body", "#filetab-host"]);
const IN_BOX = at(["textarea", "#composer-input"]);
const IN_TRANSCRIPT = at([".turn", "#content"]);

// The shell's dispatcher (palette-main.ts onKey, with its isTyping), lifted from its own source and run over the real
// keybindings module and a command list: which command a keydown runs, if any, and whether it was taken.
function shellOnKey(platform: string, overrides: Record<string, string>, cmds: Array<{ id: string; chord?: string }>, ran: string[]) {
  const src = esb(liftFrom(MAIN, "isTyping") + "\n" + liftFrom(MAIN, "onKey"));
  const env: Record<string, unknown> = {
    ...keybindings, mac: isMac(platform),
    keys: { isOpen: () => false, feed: () => {} }, palette: { close: () => {} },
    commandList: () => cmds, loadOverrides: () => ({ ...overrides }), runCommand: (id: string) => { ran.push(id); return true; },
  };
  return new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\nlet byChord = null;\n" + src + "\nreturn onKey;")(env) as (e: unknown) => void;
}
function shellKeys(platform: string, overrides: Record<string, string> = {}, extra: Array<{ id: string; chord?: string }> = []) {
  const cmds = [...Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord })), ...extra];
  const ran: string[] = [];
  const onKey = shellOnKey(platform, overrides, cmds, ran);
  return (key: string, code: string, m: Mods, target: unknown = IN_TRANSCRIPT) => {
    const e = { ...kd(key, code, m), repeat: false, isComposing: false, keyCode: 0, target, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    const before = ran.length;
    withPlatform(platform, () => onKey(e));
    return { ran: ran.slice(before), prevented: e.prevented, stopped: e.stopped };
  };
}

// The chat document whole: render.ts's three window-capture key handlers that read the store (the comment key's, the nav
// trail's, the jump moves'), lifted and registered in their source order, and, inside the shell, the shell's dispatcher
// on the chat document. A keydown reaches every window-capture listener (stopPropagation stops none on the same target),
// then the dispatcher's document-capture listener unless one of them stopped it. `ran` lists every command run, by any
// of them: one entry is one action.
function chatDocument(platform: string, overrides: Record<string, string> = {}, shell = true, extra: Array<{ id: string; chord?: string }> = []) {
  const listeners: Array<{ type: string; capture: unknown; fn: (e: any) => void }> = [];
  const ran: string[] = [];
  const parts = [listenerSrc(COMMENT_KEY), listenerSrc(NAV_KEYS), listenerSrc(JUMP_KEYS)];
  assert.ok(parts[0].at < parts[1].at && parts[1].at < parts[2].at, "the comment key's handler registers first, then the nav trail's, then the jump moves'");
  const env = {
    ...chatEnv(platform, overrides, shell, extra, listeners),
    commentOnSelection: () => { ran.push(ID); },
    navHist: { go: (d: number) => { ran.push(d < 0 ? "chat.navBack" : "chat.navForward"); } },
    jumpCluster: { hidden: true }, jcButtons: {}, flash: () => {}, runJump: (m: string) => { ran.push("chat." + m); },
  };
  new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(chatHelpers() + "\n" + parts.map((p) => p.src).join("\n")))(env);
  assert.equal(listeners.length, 3);
  assert.ok(listeners.every((l) => l.type === "keydown" && l.capture === true));
  const onKey = shell ? shellOnKey(platform, overrides, [...SHELL_CMDS, ...extra], ran) : null;
  return (key: string, code: string, m: Mods, target: unknown = IN_TRANSCRIPT) => {
    const e = { ...kd(key, code, m), repeat: !!m.repeat, isComposing: false, keyCode: 0, target, prevented: false, stopped: false,
      preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; } };
    const before = ran.length;
    withPlatform(platform, () => {
      for (const l of listeners) l.fn(e);
      if (onKey && !e.stopped) onKey(e);
    });
    return { ran: ran.slice(before), prevented: e.prevented };
  };
}

// The right-click menu's Comment line: the hint expression of render.ts's own mk("Comment", …) line, executed in the
// chat's world. The line calls commentKeyHint, lifted with the chat's helpers, which reads the platform and the store
// from that world; the globals are set to the same world for a line that calls keyHint instead (a keybindings function
// that reads them), as the line did at #39
const MENU_HINT = (() => {
  const m = /mk\("Comment", \(\) => openCommentComposer\(aim\.sid, aim\.uuid, aim\.text, e\.clientX, e\.clientY\), (.+?) \|\| undefined\);/.exec(RENDER);
  assert.ok(m, "the menu's Comment line");
  return m[1];
})();
function menuHint(platform: string, overrides: Record<string, string> = {}, shell = true, extra: Array<{ id: string; chord?: string }> = []): string {
  const env = chatEnv(platform, overrides, shell, extra, []);
  let out = "";
  withPlatform(platform, () => withOverrides(overrides, () => {
    out = new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(chatHelpers() + "\nconst __hint = " + MENU_HINT + ";") + "\nreturn __hint;")(env);
  }));
  return out;
}
// The palette's chip for a command: palette-main.ts's own kbdFor, executed over the shell's command list
function paletteChip(platform: string, overrides: Record<string, string>, id: string, extra: Array<{ id: string; chord?: string }> = []): string | undefined {
  const m = /kbdFor: (.*?),\n/.exec(MAIN);
  assert.ok(m, "the palette's kbdFor");
  const cmds = [...SHELL_CMDS, ...extra];
  const helper = MAIN.includes("function kbdChip(") ? liftFrom(MAIN, "kbdChip") : "";
  const env: Record<string, unknown> = { ...keybindings, loadOverrides: () => ({ ...overrides }), commandList: () => cmds, mac: isMac(platform) };
  const kbdFor = new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(helper + "\nconst __kbd = " + m[1] + ";") + "\nreturn __kbd;")(env);
  return kbdFor(cmds.find((c) => c.id === id));
}

test("the chat's key handler takes Mod+/ with its default prevented, so the message box gets no \"/\" and no slash menu", () => {
  const pc = keyHandler("Linux x86_64");
  assert.deepEqual(pc("/", "Slash", { ctrl: true }), TAKEN, "Ctrl+/: comment; the keystroke's \"/\" never reaches the box");
  assert.deepEqual(pc("/", "Digit7", { ctrl: true, shift: true }), TAKEN, "Ctrl+Shift+7, a German layout's Ctrl+/");
  assert.deepEqual(pc("/", "Slash", {}), LEFT, "a plain \"/\" is typing: the box gets it and its slash menu opens");
  assert.deepEqual(pc("/", "Digit7", { shift: true }), LEFT, "a German layout's plain \"/\" likewise");
  assert.deepEqual(pc("-", "Slash", { ctrl: true }), LEFT, "Ctrl+- on a German layout stays the browser's zoom out");
  assert.deepEqual(pc("?", "Slash", { ctrl: true, shift: true }), LEFT, "Ctrl+Shift+/ is another chord");
  assert.deepEqual(pc("/", "Slash", { meta: true }), LEFT, "Meta is not Mod off a Mac");
  assert.deepEqual(pc("/", "Slash", { ctrl: true, repeat: true }), LEFT, "a held key comments once");
  const mac = keyHandler("MacIntel");
  assert.deepEqual(mac("/", "Slash", { meta: true }), TAKEN, "⌘/ on a Mac");
  assert.deepEqual(mac("7", "Digit7", { meta: true, shift: true }), LEFT, "⌘⇧7 on a German Mac arrives as \"7\": not ⌘/");
  assert.deepEqual(mac("/", "Slash", { ctrl: true }), LEFT, "Control+/ is not ⌘/");
  // a rebind moves it, read per press: ⌘⌥M bound in the dialog answers, ⌘/ goes back to the page
  const bound = keyHandler("MacIntel", { [ID]: "Mod+Alt+M" });
  assert.deepEqual(bound("µ", "KeyM", { alt: true, meta: true }), TAKEN, "a Mac's ⌘⌥M, spelled µ");
  assert.deepEqual(bound("/", "Slash", { meta: true }), LEFT);
});

test("the chat's key handler reads the saved bindings on every press: a rebind moves the key at once, a reset brings it back", () => {
  const store: Record<string, string> = {};
  const h = keyHandler("Linux x86_64", store);
  assert.deepEqual(h("/", "Slash", { ctrl: true }), TAKEN, "the default");
  store[ID] = "Mod+Alt+M";
  assert.deepEqual(h("/", "Slash", { ctrl: true }), LEFT, "rebound in the dialog: Ctrl+/ goes back to the page on the next press");
  assert.deepEqual(h("m", "KeyM", { ctrl: true, alt: true }), TAKEN, "and the new chord answers");
  delete store[ID];
  assert.deepEqual(h("/", "Slash", { ctrl: true }), TAKEN, "reset: the default answers again");
});

test("a Mac's ⌘⇧/ (the Help menu's key, reported as \"/\" with ⌘ and Shift) keeps its Shift: the comment key runs neither in the chat nor in the shell", () => {
  const mac = keyHandler("MacIntel");
  assert.deepEqual(mac("/", "Slash", { meta: true, shift: true }), LEFT, "a US Mac's ⌘⇧/ is not ⌘/");
  assert.deepEqual(mac("/", "NumpadDivide", { meta: true, shift: true }), LEFT, "nor is ⌘⇧ with the numpad's divide key");
  const shell = shellKeys("MacIntel");
  assert.deepEqual(shell("/", "Slash", { meta: true, shift: true }), { ran: [], prevented: false, stopped: false }, "the shell's dispatcher runs nothing on it");
  assert.equal(chordOf(kd("/", "Slash", { meta: true, shift: true })), "Shift+Meta+/");
  assert.deepEqual(mac("/", "Slash", { meta: true }), TAKEN, "⌘/ itself still comments");
});

test("a binding saved on Shift+Meta+/ (how the dialog records a Mac's ⌘⇧/ or ⌘⇧ with the numpad's divide key) runs its own command on those keys", () => {
  const ov = { "log.open": "Shift+Meta+/" };
  const shell = shellKeys("MacIntel", ov, [{ id: "log.open" }]);
  assert.deepEqual(shell("/", "Slash", { meta: true, shift: true }), { ran: ["log.open"], prevented: true, stopped: true }, "⌘⇧/ opens the log");
  assert.deepEqual(shell("/", "NumpadDivide", { meta: true, shift: true }).ran, ["log.open"], "so does ⌘⇧ with the numpad's divide key");
  const chat = keyHandler("MacIntel", ov);
  assert.deepEqual(chat("/", "Slash", { meta: true, shift: true }), LEFT, "the chat's handler leaves it to the shell's dispatcher on the chat document");
  assert.deepEqual(chat("/", "Slash", { meta: true }), TAKEN, "⌘/ is still the comment key");
  assert.equal(displayChord(ov["log.open"], true), "⇧⌘/", "the dialog's row keeps the Shift");
});

test("a binding saved on a \"/\" chord with Ctrl or Alt, Shift and Meta (Ctrl+Shift+Meta+/, Alt+Shift+Meta+/) keeps its Shift: on a Mac it runs on those keys and not on the same keys without Shift", () => {
  // keydowns that reach the page as "/" with Shift under ⌘ and Ctrl or Alt: the numpad's divide key, and a German Mac's 7
  // with Ctrl held (w3c-keyname's source reads Shift as lost under ⌘ only with neither Ctrl nor Alt; not run on a Mac)
  for (const [saved, m, codes] of [["Ctrl+Shift+Meta+/", { ctrl: true }, ["NumpadDivide", "Digit7"]], ["Alt+Shift+Meta+/", { alt: true }, ["NumpadDivide"]]] as Array<[string, Mods, string[]]>) {
    const shell = shellKeys("MacIntel", { "log.open": saved }, [{ id: "log.open" }]);
    for (const code of codes) {
      assert.deepEqual(shell("/", code, { ...m, meta: true, shift: true }), { ran: ["log.open"], prevented: true, stopped: true }, saved + " opens the log on its own keys (" + code + ")");
    }
    assert.deepEqual(shell("/", "NumpadDivide", { ...m, meta: true }).ran, [], saved + ": the same keys without Shift run nothing");
    assert.equal(resolveChord(saved, true), saved, saved + " reads as itself, so the dialog's row shows its Shift");
  }
});

test("a binding saved on Ctrl+Shift+/ before #39 (the dialog's spelling of a German Ctrl+Shift+7 then) runs its own command, over the comment key's default", () => {
  const ov = { "log.open": "Ctrl+Shift+/" };
  const extra = [{ id: "log.open" }];
  const shell = shellKeys("Linux x86_64", ov, extra);
  assert.deepEqual(shell("/", "Digit7", { ctrl: true, shift: true }), { ran: ["log.open"], prevented: true, stopped: true }, "a German Ctrl+Shift+7 opens the log");
  assert.deepEqual(shell("/", "NumpadDivide", { ctrl: true, shift: true }).ran, ["log.open"], "so does Ctrl+Shift with the numpad's divide key");
  const chat = keyHandler("Linux x86_64", ov);
  assert.deepEqual(chat("/", "Digit7", { ctrl: true, shift: true }), LEFT, "the chat's handler leaves it to the shell's dispatcher on the chat document");
  assert.deepEqual(chat("/", "Slash", { ctrl: true }), LEFT, "Ctrl+/ is the same chord now, so it is the saved command's too");
  // the dialog: the saved row reads Ctrl+/, the comment row yields to it, and recording Ctrl+/ again names the holder
  const cmds = [...Object.entries(DEFAULT_CHORDS).map(([id, chord]) => ({ id, chord })), ...extra];
  assert.equal(displayChord(ov["log.open"], false), "Ctrl+/");
  assert.equal(yieldsTo(ID, cmds, ov, false), "log.open");
  assert.equal(conflictOf("Ctrl+/", ID, cmds, ov, false), "log.open");
  // a per-tab hot key saved there likewise
  const hot = "session.hotkey.aaaaaaaa-1111-2222-3333-abcabcabcabc";
  assert.deepEqual(shellKeys("Linux x86_64", { [hot]: "Ctrl+Shift+/" }, [{ id: hot }])("/", "Digit7", { ctrl: true, shift: true }).ran, [hot]);
});

test("the \"/\" rule reads the chord's own Meta, not the platform: on a Mac a Ctrl+Shift+/ or Alt+Shift+/ saved before #39 runs on its keys, and off a Mac a Ctrl+Shift+Meta+/ (Meta the Windows or Super key) keeps its Shift", () => {
  const extra = [{ id: "log.open" }];
  // on a Mac with Ctrl or Alt held and no ⌘, a "/" typed with Shift arrives as "/" with Shift (w3c-keyname's source reads
  // Shift as lost under ⌘ only with neither Ctrl nor Alt; not run on a Mac), so a saved binding's Shift goes as off a Mac
  assert.equal(resolveChord("Ctrl+Shift+/", true), "Ctrl+/");
  assert.equal(resolveChord("Alt+Shift+/", true), "Alt+/");
  const macCtrl = shellKeys("MacIntel", { "log.open": "Ctrl+Shift+/" }, extra);
  for (const code of ["NumpadDivide", "Digit7"]) {
    assert.deepEqual(macCtrl("/", code, { ctrl: true, shift: true }).ran, ["log.open"], "a Mac's ⌃⇧ with " + code + " opens the log");
  }
  assert.deepEqual(shellKeys("MacIntel", { "log.open": "Alt+Shift+/" }, extra)("/", "NumpadDivide", { alt: true, shift: true }).ran, ["log.open"],
    "a Mac's ⌥⇧ with the numpad's divide key opens the log");
  // off a Mac a saved chord with Meta keeps its Shift, as on a Mac, and answers to the keys that recorded it
  assert.equal(resolveChord("Ctrl+Shift+Meta+/", false), "Ctrl+Shift+Meta+/");
  assert.equal(resolveChord("Alt+Shift+Meta+/", false), "Alt+Shift+Meta+/");
  const pcMeta = shellKeys("Linux x86_64", { "log.open": "Ctrl+Shift+Meta+/" }, extra);
  assert.deepEqual(pcMeta("/", "NumpadDivide", { ctrl: true, meta: true, shift: true }).ran, ["log.open"], "Ctrl+Win+Shift with the numpad's divide key opens the log");
  assert.deepEqual(pcMeta("/", "NumpadDivide", { ctrl: true, meta: true }).ran, [], "the same keys without Shift run nothing");
});

test("a binding saved on Shift+/ before #39 (the dialog's spelling of a German Shift+7, or Shift with the numpad's divide key) runs on those keys and never on a bare \"/\"", () => {
  const ov = { "log.open": "Shift+/" };
  assert.equal(resolveChord("Shift+/", false), "Shift+/", "Shift alone stays: the \"/\" rule is for a chord with Ctrl or Alt");
  assert.equal(resolveChord("Alt+Shift+/", false), "Alt+/", "with Alt the rule holds");
  assert.equal(chordOf(kd("/", "Digit7", { alt: true, shift: true })), "Alt+/", "and the keydown agrees");
  const shell = shellKeys("Linux x86_64", ov, [{ id: "log.open" }]);
  assert.deepEqual(shell("/", "Slash", {}), { ran: [], prevented: false, stopped: false },
    "a US keyboard's bare \"/\" from the transcript runs nothing, so it goes on to type into the message box");
  assert.deepEqual(shell("/", "NumpadDivide", {}).ran, [], "nor does the numpad's divide key alone");
  assert.deepEqual(shell("/", "Digit7", { shift: true }), { ran: ["log.open"], prevented: true, stopped: true },
    "a German Shift+7 from the transcript opens the log, as it did before #39");
  assert.deepEqual(shell("/", "NumpadDivide", { shift: true }).ran, ["log.open"], "so does Shift with the numpad's divide key");
  assert.deepEqual(shell("/", "Digit7", { shift: true }, IN_BOX).ran, [], "in the message box Shift+7 types its \"/\", as any typing does");
  assert.equal(bindable(chordOf(kd("/", "Digit7", { shift: true }))!), true, "the recorder takes a German Shift+7 again, as Shift+/");
});

test("the cost, disclosed: a binding saved before #39 on a \"/\" chord with Ctrl or Alt and Shift now shares its chord with one saved apart from it without Shift, the command registered later takes it, and the dialog and the recorder name that one", () => {
  const ov = { "t.first": "Ctrl+/", "t.second": "Ctrl+Shift+/" };
  const comment = { id: ID, chord: DEFAULT_CHORDS[ID] }, other = { id: "t.other" };
  for (const [order, pick, dead] of [[["t.first", "t.second"], "t.second", "t.first"], [["t.second", "t.first"], "t.first", "t.second"]] as Array<[string[], string, string]>) {
    const cmds = [comment, ...order.map((id) => ({ id })), other];
    const why = " (registered " + order.join(", then ") + ")";
    assert.equal(chordMap(cmds, ov, false).get("Ctrl+/"), pick, "the dispatcher gives Ctrl+/ to the later one" + why);
    // the dialog's marks (yieldsTo) follow the dispatcher's pick
    assert.equal(yieldsTo(ID, cmds, ov, false), pick, "the comment row yields to the command the key runs" + why);
    assert.equal(yieldsTo(dead, cmds, ov, false), pick, "the other saved row is marked as yielding to it" + why);
    assert.equal(yieldsTo(pick, cmds, ov, false), null, "the pick's own row carries no mark" + why);
    // the recorder: Ctrl+/ recorded for another command names the command the key runs, and Enter frees the chord
    assert.equal(conflictOf("Ctrl+/", other.id, cmds, ov, false), pick, "the recorder names the command the key runs" + why);
    const losers = keybindings.reassignLosers("Ctrl+/", other.id, cmds, ov, false);
    assert.deepEqual([...losers].sort(), [pick, dead].sort(), "Enter unbinds both saved bindings, and the comment's default stays, yielding" + why);
    const after: Record<string, string> = { ...ov, [other.id]: "Ctrl+/" };
    for (const id of losers) after[id] = "";
    assert.equal(chordMap(cmds, after, false).get("Ctrl+/"), other.id, "so the fresh binding is the one the key runs" + why);
    assert.equal(yieldsTo(ID, cmds, after, false), other.id, "and the comment row yields to it" + why);
  }
  // with nothing saved, recording Ctrl+/ for another command names the comment command, and Enter unbinds it alone
  assert.equal(conflictOf("Ctrl+/", other.id, [comment, other], {}, false), ID);
  assert.deepEqual(keybindings.reassignLosers("Ctrl+/", other.id, [comment, other], {}, false), [ID]);
  // the command being recorded is never its own loser
  assert.deepEqual(keybindings.reassignLosers("Ctrl+/", "t.first", [comment, { id: "t.first" }, { id: "t.second" }], ov, false), ["t.second"]);
  // the same with Alt, and with Ctrl and Alt together; Shift alone stays two chords
  for (const [first, second, shared] of [["Alt+/", "Alt+Shift+/", "Alt+/"], ["Ctrl+Alt+/", "Ctrl+Alt+Shift+/", "Ctrl+Alt+/"]]) {
    const cmds = [{ id: "t.first" }, { id: "t.second" }];
    const alt = { "t.first": first, "t.second": second };
    assert.equal(chordMap(cmds, alt, false).get(shared), "t.second", first + " and " + second + " share " + shared);
    assert.equal(yieldsTo("t.first", cmds, alt, false), "t.second");
  }
  assert.equal(chordMap([{ id: "t.first" }], { "t.first": "Shift+/" }, false).get("Shift+/"), "t.first");
  // the second cost: a binding recorded under #39's rule from a "/" keydown with Meta and Shift was stored without the
  // Shift, so it answers to the chord without Shift and no longer to the keys that recorded it (on a Mac, and off one with
  // Meta, the Windows or Super key)
  for (const [stored, meant, without] of [["Meta+/", { meta: true }, "Meta+/"], ["Ctrl+Meta+/", { ctrl: true, meta: true }, "Ctrl+Meta+/"]] as Array<[string, Mods, string]>) {
    assert.notEqual(chordOf(kd("/", "Slash", { ...meant, shift: true })), resolveChord(stored, true), stored + ", recorded from the same keys with Shift, no longer matches them");
    assert.equal(chordOf(kd("/", "Slash", meant)), resolveChord(stored, true), stored + " answers to " + without);
  }
});

test("on any chord, of two saved bindings the dialog marks the earlier as yielding to the later, which the dispatcher runs: a hand-edited store's too", () => {
  // the dialog never saves two bindings on one chord without "/", but a hand-edited store can, and keybindings' chordMap
  // says the loser is marked there too, not silently dead
  for (const order of [["t.first", "t.second"], ["t.second", "t.first"]]) {
    const cmds = order.map((id) => ({ id }));
    const hand = { "t.first": "Ctrl+K", "t.second": "Ctrl+K" };
    const [early, late] = order;
    assert.equal(chordMap(cmds, hand, false).get("Ctrl+K"), late, "the dispatcher runs the later registered (" + order.join(", then ") + ")");
    assert.equal(yieldsTo(early, cmds, hand, false), late, "the earlier one's row yields to it (" + order.join(", then ") + ")");
    assert.equal(yieldsTo(late, cmds, hand, false), null, "the later one's row carries no mark (" + order.join(", then ") + ")");
  }
});

test("the recorder frees the chord from a chat command's default too: a key saved for another command on Navigate back's Ctrl+M (or a jump move's Ctrl+Alt+↑), recorded for a third, then runs the third from the chat as from the shell", () => {
  const onChatDefaults = [["Ctrl+M", "chat.navBack", "m", "KeyM", { ctrl: true }],
    ["Ctrl+Alt+ArrowUp", "chat.prevMine", "ArrowUp", "ArrowUp", { ctrl: true, alt: true }]] as Array<[string, string, string, string, Mods]>;
  for (const [chord, own, key, code, m] of onChatDefaults) {
    const ov = { "log.open": chord };
    assert.equal(yieldsTo(own, SHELL_CMDS, ov, false), "log.open", "the chat command's default yields to the saved key in the dispatcher");
    assert.equal(conflictOf(chord, "net.open", SHELL_CMDS, ov, false), "log.open", "the recorder names the command the dispatcher runs there");
    const losers = keybindings.reassignLosers(chord, "net.open", SHELL_CMDS, ov, false);
    assert.deepEqual(losers, ["log.open", own], "Enter unbinds the saved key and the chat command's default, whose handler would keep taking it from the chat");
    const after: Record<string, string> = { ...ov, "net.open": chord };
    for (const id of losers) after[id] = "";
    assert.deepEqual(chatDocument("Linux x86_64", after, true)(key, code, m).ran, ["net.open"], chord + " from the transcript opens Remote kernels, and the chat's own command does not run");
    assert.deepEqual(shellKeys("Linux x86_64", after, [{ id: "log.open" }, { id: "net.open" }])(key, code, m).ran, ["net.open"], "and so it does from the shell");
  }
  // a default no chat handler answers keeps its default, marked as yielding: only the dispatcher runs it, and it runs the
  // fresh binding over it
  const jump = { "log.open": "Mod+O" };
  assert.deepEqual(keybindings.reassignLosers("Mod+O", "net.open", SHELL_CMDS, jump, false), ["log.open"]);
  assert.equal(yieldsTo("session.jump", SHELL_CMDS, { ...jump, "log.open": "", "net.open": "Mod+O" }, false), "net.open");
  // with nothing saved the chat command's default is the conflict, and Enter unbinds it alone, as before
  assert.deepEqual(keybindings.reassignLosers("Ctrl+M", "net.open", SHELL_CMDS, {}, false), ["chat.navBack"]);
  // the command being recorded keeps its own default out of the list
  assert.deepEqual(keybindings.reassignLosers("Ctrl+M", "chat.navBack", SHELL_CMDS, { "log.open": "Ctrl+M" }, false), ["log.open"]);
  // the key recorded again for the command that holds it: the dispatcher already runs that command there, so conflictOf
  // names nothing, but the chat command's default is in the list, and once Enter unbinds it the key opens the log from
  // the chat too (the dialog shows its row whenever the list is not empty: shortcuts-modal.test.ts runs it)
  for (const [chord, own, key, code, m] of onChatDefaults) {
    const ov = { "log.open": chord };
    assert.equal(conflictOf(chord, "log.open", SHELL_CMDS, ov, false), null, "the dispatcher already runs the log on " + chord);
    const losers = keybindings.reassignLosers(chord, "log.open", SHELL_CMDS, ov, false);
    assert.deepEqual(losers, [own], "Enter unbinds the chat command's default, whose handler takes " + chord + " from the chat");
    const after: Record<string, string> = { ...ov };
    for (const id of losers) after[id] = "";
    assert.deepEqual(chatDocument("Linux x86_64", ov, true)(key, code, m).ran, [own], "before, " + chord + " from the transcript runs " + own);
    assert.deepEqual(chatDocument("Linux x86_64", after, true)(key, code, m).ran, ["log.open"], "after, " + chord + " from the transcript opens the log");
  }
});

test("on a page with no shell (a standalone /chat, the VS Code view) the comment key keeps its chord from a command only the shell runs: nothing there runs it", () => {
  const ov = { "log.open": "Ctrl+Shift+/" };
  const alone = keyHandler("Linux x86_64", ov, false);
  assert.deepEqual(alone("/", "Slash", { ctrl: true }), TAKEN, "Ctrl+/ comments");
  assert.deepEqual(alone("/", "Digit7", { ctrl: true, shift: true }), TAKEN, "so does a German Ctrl+/");
  assert.deepEqual(keyHandler("Linux x86_64", { "retired.command": "Ctrl+/" }, false)("/", "Slash", { ctrl: true }), TAKEN,
    "and with a key saved under an id no command has");
  assert.deepEqual(keyHandler("MacIntel", { "log.open": "Meta+/" }, false)("/", "Slash", { meta: true }), TAKEN, "⌘/ on a Mac");
  assert.deepEqual(alone("/", "Slash", { ctrl: true }, IN_EDITOR), LEFT, "the code editor keeps its own toggle comment there too");
  assert.deepEqual(keyHandler("Linux x86_64", ov, true)("/", "Slash", { ctrl: true }), LEFT,
    "inside the shell the same store leaves the key to the shell's dispatcher, which opens the log");
  assert.deepEqual(keyHandler("Linux x86_64", { "retired.command": "Ctrl+/" }, true)("/", "Slash", { ctrl: true }), TAKEN,
    "inside the shell a key saved under an id no command has runs nothing, so the comment key keeps it there too");
  // in a shell whose script has not run yet (no __rompChordPick, and no dispatcher either), the comment key keeps it
  assert.deepEqual(keyHandler("Linux x86_64", ov, true, [], false)("/", "Slash", { ctrl: true }), TAKEN);
});

test("on the chat document the comment key steps aside for one of the chat's own commands saved on its chord, with or without the shell: that key runs one command", () => {
  for (const shell of [false, true]) {
    const where = shell ? " (in the shell)" : " (the chat page on its own)";
    const back = chatDocument("Linux x86_64", { "chat.navBack": "Ctrl+Shift+/" }, shell);
    assert.deepEqual(back("/", "Slash", { ctrl: true }).ran, ["chat.navBack"], "Ctrl+/ with Navigate back saved on Ctrl+Shift+/ goes back and opens no comment" + where);
    assert.deepEqual(back("/", "Digit7", { ctrl: true, shift: true }).ran, ["chat.navBack"], "so does a German Ctrl+Shift+7" + where);
    assert.deepEqual(chatDocument("Linux x86_64", { "chat.navForward": "Ctrl+/" }, shell)("/", "Slash", { ctrl: true }).ran, ["chat.navForward"],
      "Navigate forward saved on Ctrl+/ itself" + where);
    assert.deepEqual(chatDocument("Linux x86_64", { "chat.prevMine": "Ctrl+Shift+/" }, shell)("/", "Slash", { ctrl: true }).ran, ["chat.prevMine"],
      "a jump move saved there jumps" + where);
    assert.deepEqual(chatDocument("MacIntel", { "chat.navBack": "Meta+/" }, shell)("/", "Slash", { meta: true }).ran, ["chat.navBack"], "⌘/ on a Mac" + where);
    const none = chatDocument("Linux x86_64", {}, shell);
    assert.deepEqual(none("/", "Slash", { ctrl: true }).ran, [ID], "nothing saved: Ctrl+/ comments, once" + where);
    assert.deepEqual(none("m", "KeyM", { ctrl: true }).ran, ["chat.navBack"], "Ctrl+M goes back, once" + where);
    assert.deepEqual(none("ArrowUp", "ArrowUp", { ctrl: true, alt: true }).ran, ["chat.prevMine"], "Ctrl+Alt+↑ jumps, once" + where);
  }
  // a command only the shell runs: in the shell its saved key runs it, and on the chat page alone the comment key keeps it
  assert.deepEqual(chatDocument("Linux x86_64", { "log.open": "Ctrl+Shift+/" }, true)("/", "Slash", { ctrl: true }).ran, ["log.open"]);
  assert.deepEqual(chatDocument("Linux x86_64", { "log.open": "Ctrl+Shift+/" }, false)("/", "Slash", { ctrl: true }).ran, [ID]);
  // a key saved under an id no command has: the comment runs, once, in the shell too
  assert.deepEqual(chatDocument("Linux x86_64", { "retired.command": "Ctrl+/" }, true)("/", "Slash", { ctrl: true }).ran, [ID]);
  // a jump default under another command's saved binding: the chat's jump handler still takes it (the tooltip keeps it)
  assert.deepEqual(chatDocument("Linux x86_64", { "log.open": "Ctrl+Alt+ArrowUp" }, false)("ArrowUp", "ArrowUp", { ctrl: true, alt: true }).ran, ["chat.prevMine"]);
});

test("one of the chat's own commands saved on the comment key's chord takes it only where its handler reads the chord: a Navigate back on an Alt chord leaves the comment key its key, and the menu names it", () => {
  const L = "Linux x86_64";
  for (const shell of [false, true]) {
    const where = shell ? " (in the shell)" : " (the chat page on its own)";
    // the comment key rebound to Alt+/ and Navigate back saved on Alt+Shift+/ before #39: both read as Alt+/ now, and the
    // nav trail's handler reads no chord without Ctrl or Meta
    const alt = { [ID]: "Alt+/", "chat.navBack": "Alt+Shift+/" };
    assert.deepEqual(chatDocument(L, alt, shell)("/", "Slash", { alt: true }).ran, [ID], "Alt+/ comments, once" + where);
    assert.equal(menuHint(L, alt, shell), "Alt+/", "and the menu names it" + where);
    // a jump move saved there takes it: the jump moves' handler reads Alt
    const jump = { [ID]: "Alt+/", "chat.prevMine": "Alt+Shift+/" };
    assert.deepEqual(chatDocument(L, jump, shell)("/", "Slash", { alt: true }).ran, ["chat.prevMine"], "a jump move saved on Alt+Shift+/ jumps" + where);
    assert.equal(menuHint(L, jump, shell), "", "and the menu names no key" + where);
    // with Ctrl in the chord the nav trail's handler reads it, and takes it
    const ctrl = { [ID]: "Ctrl+Alt+/", "chat.navBack": "Ctrl+Alt+Shift+/" };
    assert.deepEqual(chatDocument(L, ctrl, shell)("/", "Slash", { ctrl: true, alt: true }).ran, ["chat.navBack"], "Navigate back on Ctrl+Alt+Shift+/ goes back" + where);
    assert.equal(menuHint(L, ctrl, shell), "", "and the menu names no key" + where);
  }
});

test("one of the chat's own commands saved on ANOTHER chord leaves the comment key its key: the stand-down asks which chord the saved binding is on, not only whether there is one", () => {
  const L = "Linux x86_64";
  for (const ov of [{ "chat.navBack": "Ctrl+Shift+M" }, { "chat.prevMine": "Ctrl+Alt+K" }, { "chat.navBack": "Ctrl+Shift+M", "chat.prevMine": "Ctrl+Alt+K" }] as Array<Record<string, string>>) {
    for (const shell of [false, true]) {
      const where = " (" + JSON.stringify(ov) + (shell ? ", in the shell)" : ", the chat page on its own)");
      assert.deepEqual(keyHandler(L, ov, shell)("/", "Slash", { ctrl: true }), TAKEN, "the chat's handler takes Ctrl+/" + where);
      assert.deepEqual(chatDocument(L, ov, shell)("/", "Slash", { ctrl: true }).ran, [ID], "Ctrl+/ comments, once" + where);
      assert.equal(menuHint(L, ov, shell), "Ctrl+/", "and the menu names it" + where);
    }
  }
  // on a Mac likewise, and each saved key still runs its own command
  assert.deepEqual(chatDocument("MacIntel", { "chat.navBack": "Shift+Meta+M" }, false)("/", "Slash", { meta: true }).ran, [ID], "⌘/ comments on a Mac");
  assert.deepEqual(chatDocument(L, { "chat.navBack": "Ctrl+Shift+M" }, false)("M", "KeyM", { ctrl: true, shift: true }).ran, ["chat.navBack"], "Ctrl+Shift+M goes back");
  assert.deepEqual(chatDocument(L, { "chat.prevMine": "Ctrl+Alt+K" }, false)("k", "KeyK", { ctrl: true, alt: true }).ran, ["chat.prevMine"], "Ctrl+Alt+K jumps");
});

// The residual keybindings' yieldsTo discloses, pinned as it stands, so a change that closes or widens it in these four
// stores turns this red, as does the nav trail's handler coming to read an Alt chord (the Alt-chord negatives); a chat
// handler that comes to read any other chord it ignores now fails "chatAnswers is the chat's handlers' own rule". The
// chat's nav and jump handlers answer the chords they read whatever the shell's dispatcher picks, and only the comment
// key's handler steps aside (for one of those saved on its chord), so in these four stores the chat runs what the dialog
// marks as yielding, or two commands on one press. Making those handlers ask the dispatcher is a change of its own.
test("the disclosed residual: in these stores the chat's nav and jump handlers run what the dialog marks as yielding, or run beside another command", () => {
  const L = "Linux x86_64";
  const shellWith = (ov: Record<string, string>) => shellKeys(L, ov, [{ id: "log.open" }]);
  // (1) a key saved for another command on a chat command's default: the chat runs the chat command
  const onDefault = { "log.open": "Ctrl+M" };
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, onDefault, false), "log.open", "(1) the dialog marks Navigate back's default as yielding");
  for (const shell of [false, true]) {
    assert.deepEqual(chatDocument(L, onDefault, shell)("m", "KeyM", { ctrl: true }).ran, ["chat.navBack"], "(1) Ctrl+M goes back from the chat" + (shell ? " in the shell" : ""));
  }
  assert.deepEqual(shellWith(onDefault)("m", "KeyM", { ctrl: true }).ran, ["log.open"], "(1) and opens the log from the shell");
  // (2) a chat command's SAVED binding the dialog marks as yielding, the earlier of two saved on one collapsed "/" chord:
  // the chat runs it, from the chat viewer's code editor too, where the line stays as it was
  for (const [ov, winner] of [[{ [ID]: "Ctrl+/", "chat.navBack": "Ctrl+Shift+/" }, ID], [{ "log.open": "Ctrl+/", "chat.navBack": "Ctrl+Shift+/" }, "log.open"]] as Array<[Record<string, string>, string]>) {
    const why = " (" + JSON.stringify(ov) + ")";
    assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, ov, false), winner, "(2) the dialog marks Navigate back as yielding" + why);
    for (const shell of [false, true]) {
      assert.deepEqual(chatDocument(L, ov, shell)("/", "Slash", { ctrl: true }).ran, ["chat.navBack"], "(2) Ctrl+/ goes back from the chat" + why);
      assert.deepEqual(chatDocument(L, ov, shell)("/", "Slash", { ctrl: true }, IN_EDITOR).ran, ["chat.navBack"], "(2) and from the chat viewer's code editor" + why);
    }
    assert.deepEqual(shellWith(ov)("/", "Slash", { ctrl: true }).ran, [winner], "(2) from the shell the key runs the command the dialog names" + why);
  }
  // (3) a nav command and a jump move saved on one collapsed "/" chord both their handlers read: both run from the chat on
  // one press
  for (const ov of [{ "chat.navBack": "Ctrl+/", "chat.prevMine": "Ctrl+Shift+/" }, { "chat.navBack": "Ctrl+/", "chat.prevMine": "Ctrl+Shift+/", [ID]: "Mod+Alt+M" }] as Array<Record<string, string>>) {
    const why = " (" + JSON.stringify(ov) + ")";
    assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, ov, false), "chat.prevMine", "(3) the dialog marks Navigate back as yielding to the jump" + why);
    for (const shell of [false, true]) {
      assert.deepEqual(chatDocument(L, ov, shell)("/", "Slash", { ctrl: true }).ran, ["chat.navBack", "chat.prevMine"], "(3) Ctrl+/ goes back and jumps from the chat" + why);
    }
    assert.deepEqual(shellWith(ov)("/", "Slash", { ctrl: true }).ran, ["chat.prevMine"], "(3) from the shell it jumps alone" + why);
  }
  // (2) and (3) in a hand-edited store, on a chord without "/" (the dialog never saves two bindings there): the dialog
  // marks the earlier saved row as yielding to the later, which the shell runs, and the chat runs its own command, or
  // both its handlers' commands, as it did before rows like these carried the mark
  const handBack = { "chat.navBack": "Ctrl+K", "log.open": "Ctrl+K" };
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, handBack, false), "log.open", "(2) hand-edited: the dialog marks Navigate back as yielding to the log");
  for (const shell of [false, true]) {
    assert.deepEqual(chatDocument(L, handBack, shell)("k", "KeyK", { ctrl: true }).ran, ["chat.navBack"], "(2) hand-edited: Ctrl+K goes back from the chat" + (shell ? " in the shell" : ""));
  }
  assert.deepEqual(shellWith(handBack)("k", "KeyK", { ctrl: true }).ran, ["log.open"], "(2) hand-edited: from the shell the key opens the log");
  const handPair = { "chat.navBack": "Ctrl+K", "chat.prevMine": "Ctrl+K" };
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, handPair, false), "chat.prevMine", "(3) hand-edited: the dialog marks Navigate back as yielding to the jump");
  for (const shell of [false, true]) {
    assert.deepEqual(chatDocument(L, handPair, shell)("k", "KeyK", { ctrl: true }).ran, ["chat.navBack", "chat.prevMine"], "(3) hand-edited: Ctrl+K goes back and jumps from the chat" + (shell ? " in the shell" : ""));
  }
  assert.deepEqual(shellWith(handPair)("k", "KeyK", { ctrl: true }).ran, ["chat.prevMine"], "(3) hand-edited: from the shell it jumps alone");
  // and with the comment key's own binding one of those saved there: the dialog marks the chat's commands as yielding to
  // the comment key, registered later, which the shell runs; in the chat the comment key steps aside, where it used to
  // run beside them, so the chat runs its own commands alone, and the menu names no key
  for (const ov of [{ [ID]: "Ctrl+K", "chat.navBack": "Ctrl+K" }, { [ID]: "Ctrl+K", "chat.prevMine": "Ctrl+K" },
    { [ID]: "Ctrl+K", "chat.navBack": "Ctrl+K", "chat.prevMine": "Ctrl+K" }] as Array<Record<string, string>>) {
    const mine = keybindings.CHAT_KEY_COMMANDS.filter((id) => id in ov), why = " (" + JSON.stringify(ov) + ")";
    for (const id of mine) assert.equal(yieldsTo(id, SHELL_CMDS, ov, false), ID, "hand-edited: the dialog marks " + id + " as yielding to the comment key" + why);
    for (const shell of [false, true]) {
      assert.deepEqual(chatDocument(L, ov, shell)("k", "KeyK", { ctrl: true }).ran, mine, "hand-edited: Ctrl+K runs the chat's own commands alone" + (shell ? " in the shell" : "") + why);
      assert.equal(menuHint(L, ov, shell), "", "hand-edited: and the menu names no key" + (shell ? " in the shell" : "") + why);
    }
    assert.deepEqual(shellWith(ov)("k", "KeyK", { ctrl: true }).ran, [ID], "hand-edited: from the shell the key comments" + why);
  }
  // (2) and (3) only where the chat's handler reads the chord: the nav trail's reads none without Ctrl or Meta, so on an
  // Alt chord Navigate back runs nowhere. Beside Open the log there, the key opens the log in the shell, as the dialog
  // says, and runs nothing on the chat page alone; beside a jump move, the chat jumps alone on both pages
  const altBack = { "chat.navBack": "Alt+Shift+/", "log.open": "Alt+/" };
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, altBack, false), "log.open", "(2) on an Alt chord the dialog marks Navigate back as yielding to the log");
  assert.deepEqual(chatDocument(L, altBack, false)("/", "Slash", { alt: true }).ran, [], "(2) on an Alt chord the chat page on its own runs nothing");
  assert.deepEqual(chatDocument(L, altBack, true)("/", "Slash", { alt: true }).ran, ["log.open"], "(2) on an Alt chord in the shell the key opens the log, as the dialog says");
  const altPair = { "chat.navBack": "Alt+/", "chat.prevMine": "Alt+Shift+/" };
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, altPair, false), "chat.prevMine", "(3) on an Alt chord the dialog marks Navigate back as yielding to the jump");
  for (const shell of [false, true]) {
    assert.deepEqual(chatDocument(L, altPair, shell)("/", "Slash", { alt: true }).ran, ["chat.prevMine"], "(3) on an Alt chord the chat jumps alone" + (shell ? " in the shell" : ""));
  }
  // (4) the comment key's own binding saved on a chat command's default, which the dialog reaches: record Ctrl+M for
  // Comment on selection (Enter unbinds Navigate back), then Reset Navigate back, which checks no conflict
  const recorded: Record<string, string> = { [ID]: "Ctrl+M" };
  for (const id of keybindings.reassignLosers("Ctrl+M", ID, SHELL_CMDS, {}, false)) recorded[id] = "";
  assert.deepEqual(recorded, { [ID]: "Ctrl+M", "chat.navBack": "" }, "(4) the recorder unbinds Navigate back");
  const own = { [ID]: "Ctrl+M" };   // Reset on Navigate back forgets its override
  assert.equal(yieldsTo("chat.navBack", SHELL_CMDS, own, false), ID, "(4) the dialog marks Navigate back's default as yielding to the comment key");
  for (const shell of [false, true]) {
    assert.deepEqual(chatDocument(L, own, shell)("m", "KeyM", { ctrl: true }).ran, [ID, "chat.navBack"], "(4) Ctrl+M comments and goes back from the chat" + (shell ? " in the shell" : ""));
    assert.equal(menuHint(L, own, shell), "Ctrl+M", "(4) and the menu names Ctrl+M");
  }
  assert.deepEqual(shellWith(own)("m", "KeyM", { ctrl: true }).ran, [ID], "(4) from the shell it comments alone");
});

test("the chat's own key commands are the ones its handlers answer: every store read by a render.ts key handler is the comment key's, the nav trail's or a jump move's", () => {
  // keybindings' list is the nav pair and the jump moves under the ids render.ts's jumpCommand gives MOVES
  const jumps = new Function("env", "const { MOVES } = env;\n" + esb(JUMP_COMMAND) + "\nreturn MOVES.map(jumpCommand);")({ MOVES }) as string[];
  assert.deepEqual([...keybindings.CHAT_KEY_COMMANDS], ["chat.navBack", "chat.navForward", ...jumps]);
  // render.ts's chatKeyCommands, executed: the ones whose handler reads the chord
  const env = { ...keybindings, MOVES } as Record<string, unknown>;
  const own = new Function("env", "const { " + Object.keys(env).join(", ") + " } = env;\n" + esb(JUMP_COMMAND + liftFrom(RENDER, "chatKeyCommands")) + "\nreturn chatKeyCommands;")(env) as (ch: string) => string[];
  assert.deepEqual(own("Ctrl+/"), [...keybindings.CHAT_KEY_COMMANDS], "with Ctrl, every one");
  assert.deepEqual(own("Meta+/"), [...keybindings.CHAT_KEY_COMMANDS], "with Meta, every one");
  assert.deepEqual(own("Alt+/"), jumps, "with Alt alone, the jump moves: the nav trail's handler reads no chord without Ctrl or Meta");
  assert.deepEqual(own("Shift+F2"), [], "with Shift alone, none");
  // a new chat-side handler reads the store through effectiveChord; its command must join keybindings' list, or the
  // comment key would not step aside for it and one key would run two commands (a jump read is one entry, whatever its
  // loop variable is called). The census counts every read in the file, a key handler's or not, so a read that only
  // shows a key trips it too: that one joins the list here, not keybindings' (membership there makes the comment key
  // step aside for the command and lets the recorder's Enter unbind its default)
  const reads = new Set(Array.from(RENDER.matchAll(/effectiveChord\(([^,]+),/g), (m) => m[1].trim().replace(/^jumpCommand\(\w+\)$/, "jumpCommand(<move>)")));
  assert.deepEqual([...reads].sort(), ['"chat.commentSelection"', '"chat.navBack"', '"chat.navForward"', "jumpCommand(<move>)"].sort(),
    "render.ts reads a key binding for a command this census does not list: if a key handler of the chat's reads it, add the command to keybindings' CHAT_KEY_COMMANDS; if the read only shows the key, add it to the list here");
});

test("chatAnswers is the chat's handlers' own rule: one of the chat's commands saved on a chord runs from the chat exactly when chatAnswers says its handler reads that chord", () => {
  // every set of the four modifiers, the empty one included (a bare F2 is bindable): chatAnswers reads only the modifiers
  const sets: Mods[] = Array.from({ length: 16 }, (_, b) => ({ ctrl: !!(b & 1), alt: !!(b & 2), shift: !!(b & 4), meta: !!(b & 8) }));
  for (const id of keybindings.CHAT_KEY_COMMANDS) {
    for (const m of sets) {
      const chord = chordOf(kd("F2", "F2", m))!;
      const ran = chatDocument("Linux x86_64", { [id]: chord }, false)("F2", "F2", m).ran;
      assert.equal(ran.includes(id), keybindings.chatAnswers(id, chord), id + " saved on " + chord + ": the chat ran " + JSON.stringify(ran));
    }
  }
  assert.equal(keybindings.chatAnswers("log.open", "Ctrl+M"), false, "a command only the shell runs is never the chat's");
});

test("in the file viewer's code editor Mod+/ stays the editor's toggle comment: the chat's handler and the shell's dispatcher both leave it", () => {
  const pc = keyHandler("Linux x86_64");
  assert.deepEqual(pc("/", "Slash", { ctrl: true }, IN_EDITOR), LEFT, "Ctrl+/ in the editor: the editor comments the line");
  assert.deepEqual(pc("/", "Digit7", { ctrl: true, shift: true }, IN_EDITOR), LEFT, "a German layout's Ctrl+/ likewise");
  const shell = shellKeys("Linux x86_64");
  assert.deepEqual(shell("/", "Slash", { ctrl: true }, IN_EDITOR), { ran: [], prevented: false, stopped: false },
    "the shell's dispatcher leaves it too: it hears the key on the chat document once the chat's handler lets it by, and alone in the Files pane");
  assert.deepEqual(pc("/", "Slash", { ctrl: true }, IN_SEARCH), LEFT, "in the editor's search field too, where the editor does nothing with it");
  assert.deepEqual(shell("/", "Slash", { ctrl: true }, IN_SEARCH).ran, [], "the shell's dispatcher leaves it there as well");
  assert.deepEqual(keyHandler("MacIntel")("/", "Slash", { meta: true }, IN_EDITOR), LEFT, "⌘/ in the editor on a Mac");
  assert.deepEqual(shellKeys("MacIntel")("/", "Slash", { meta: true }, IN_EDITOR).ran, [], "the shell's dispatcher leaves ⌘/ there too");
  // outside the editor the key comments, as before
  assert.deepEqual(pc("/", "Slash", { ctrl: true }, IN_TRANSCRIPT), TAKEN, "outside the editor Ctrl+/ comments");
  assert.deepEqual(shell("/", "Slash", { ctrl: true }, IN_TRANSCRIPT).ran, [ID], "and the shell's dispatcher runs the comment command there");
  // the boundary is the editor itself, not the viewer around it: on the viewer's bar and in its read mode, which has no
  // editor and no toggle comment, the key is the comment key's, as anywhere else
  for (const [target, where] of [[IN_VIEWER_BAR, "a button on the file viewer's bar"], [IN_VIEWER_TEXT, "the file viewer's read mode"],
    [IN_FILE_TAB_TEXT, "a file tab's read mode"]] as Array<[unknown, string]>) {
    assert.deepEqual(pc("/", "Slash", { ctrl: true }, target), TAKEN, "the chat's handler takes Ctrl+/ from " + where);
    assert.deepEqual(shell("/", "Slash", { ctrl: true }, target).ran, [ID], "the shell's dispatcher runs the comment command from " + where);
  }
  // only the comment command, and only on the editor's own chord: other commands' keys reach them from the editor as they
  // did, a comment key rebound off Mod+/ still comments from it, and another command a reader saved on Ctrl+/ keeps it
  assert.deepEqual(shell("\\", "Backslash", { ctrl: true }, IN_EDITOR).ran, ["chat.split"], "Ctrl+\\ still splits from the editor");
  assert.deepEqual(shell("m", "KeyM", { ctrl: true }, IN_EDITOR).ran, ["chat.navBack"], "Ctrl+M still goes back from the editor, as before the comment key moved");
  const rebound = { [ID]: "Mod+Alt+M" };
  assert.deepEqual(keyHandler("Linux x86_64", rebound)("m", "KeyM", { ctrl: true, alt: true }, IN_EDITOR), TAKEN,
    "the comment key rebound to Ctrl+Alt+M comments from the editor, which binds no such key");
  assert.deepEqual(shellKeys("Linux x86_64", rebound)("m", "KeyM", { ctrl: true, alt: true }, IN_EDITOR).ran, [ID], "the shell's dispatcher agrees");
  // so does the comment key rebound to any other "/" chord with Ctrl, Alt or Meta (CodeMirror's keymap binds no "/" chord
  // but Mod-/), from the chat's handler and from the shell's dispatcher, on either platform; a chord that reads as Mod+/
  // (off a Mac, Ctrl+Shift+/) is the editor's own key, left to it
  for (const platform of ["Linux x86_64", "MacIntel"]) {
    const own = resolveChord("Mod+/", isMac(platform)), tried = new Set<string>();
    for (let b = 0; b < 16; b++) {
      const m: Mods = { ctrl: !!(b & 1), alt: !!(b & 2), shift: !!(b & 4), meta: !!(b & 8) };
      if (!m.ctrl && !m.alt && !m.meta) continue;   // a "/" with Shift alone, or bare, is typing in the editor
      const chord = chordOf(kd("/", "Slash", m))!, ov = { [ID]: chord }, why = " (" + chord + " on " + platform + ")";
      if (chord === own) {
        assert.deepEqual(keyHandler(platform, ov)("/", "Slash", m, IN_EDITOR), LEFT, "the comment key saved on the editor's own chord leaves it to the editor" + why);
        assert.deepEqual(shellKeys(platform, ov)("/", "Slash", m, IN_EDITOR).ran, [], "and so does the shell's dispatcher" + why);
        continue;
      }
      tried.add(chord);
      assert.deepEqual(keyHandler(platform, ov)("/", "Slash", m, IN_EDITOR), TAKEN, "the comment key rebound to " + chord + " comments from the editor" + why);
      assert.deepEqual(shellKeys(platform, ov)("/", "Slash", m, IN_EDITOR).ran, [ID], "and the shell's dispatcher runs it from the Files pane's editor" + why);
    }
    // not empty: at least the six chords without Shift other than Mod+/, which the "/" rule leaves alone, whatever it does
    // with Shift
    assert.ok(tried.size >= 6, "the other \"/\" chords tried on " + platform + ": " + [...tried].join(", "));
  }
  // and every other command a reader saves on the editor's own chord runs from the editor, from the Files pane (the
  // shell's dispatcher alone) and from the chat's viewer (the chat document, whose own handlers answer first): the editor
  // keeps the key from the comment command only
  const HOT = "session.hotkey.aaaaaaaa-1111-2222-3333-abcabcabcabc";
  for (const platform of ["Linux x86_64", "MacIntel"]) {
    const own = resolveChord("Mod+/", isMac(platform)), m: Mods = isMac(platform) ? { meta: true } : { ctrl: true };
    for (const { id } of [...SHELL_CMDS, { id: HOT }]) {
      const ov = { [id]: own }, want = id === ID ? [] : [id], why = " (" + id + " saved on " + own + " on " + platform + ")";
      assert.deepEqual(shellKeys(platform, ov, [...SHELL_CMDS.filter((c) => !c.chord), { id: HOT }])("/", "Slash", m, IN_EDITOR).ran, want,
        "from the Files pane's editor" + why);
      assert.deepEqual(chatDocument(platform, ov, true, [{ id: HOT }])("/", "Slash", m, IN_EDITOR).ran, want, "from the chat viewer's editor" + why);
    }
  }
});

// the global navigator set to `platform` while `fn` runs, restored after: the harnesses press every key inside it, so a
// keybindings function that read the platform itself (rather than a chord's own modifiers) would see the world's platform,
// not node's own
function withPlatform<T>(platform: string, fn: () => T): T {
  const g: any = globalThis;
  const prev = Object.getOwnPropertyDescriptor(g, "navigator");
  Object.defineProperty(g, "navigator", { value: { platform }, configurable: true, writable: true });
  try { return fn(); } finally { if (prev) Object.defineProperty(g, "navigator", prev); else delete g.navigator; }
}

test("the key hint every surface prints reads Ctrl+/ off a Mac and ⌘/ on one", () => {
  withPlatform("Win32", () => assert.equal(keyHint(ID), "Ctrl+/"));
  withPlatform("Linux x86_64", () => assert.equal(keyHint(ID), "Ctrl+/"));
  withPlatform("MacIntel", () => assert.equal(keyHint(ID), "⌘/"));
});

// node has no localStorage: a stand-in holding the saved bindings, for keyHint's loadOverrides, restored after
function withOverrides(overrides: Record<string, string>, fn: () => void): void {
  const g: any = globalThis;
  const had = "localStorage" in g, prev = g.localStorage;
  g.localStorage = { getItem: (k: string) => (k === "romp:keys" ? JSON.stringify(overrides) : null), setItem: () => {} };
  try { fn(); } finally { if (had) g.localStorage = prev; else delete g.localStorage; }
}

test("the menu's Comment names the key, for a chord with Ctrl, Alt or Meta, only where pressing it comments: none while another command's saved binding runs on it here", () => {
  const L = "Linux x86_64", M = "MacIntel", HOT = "session.hotkey.aaaaaaaa-1111-2222-3333-abcabcabcabc";
  assert.equal(menuHint(L, {}), "Ctrl+/");
  assert.equal(menuHint(M, {}), "⌘/");
  assert.equal(menuHint(L, {}, false), "Ctrl+/", "on the chat page alone too");
  assert.equal(menuHint(L, { "log.open": "Ctrl+Shift+/" }), "", "a Ctrl+Shift+/ saved before #39 reads as Ctrl+/, which opens the log in the shell");
  assert.equal(menuHint(L, { "log.open": "Ctrl+Shift+/" }, false), "Ctrl+/", "on the chat page alone nothing runs the log, and the key comments");
  assert.equal(menuHint(L, { [HOT]: "Ctrl+/" }, true, [{ id: HOT }]), "", "a per-tab hot key saved there");
  assert.equal(menuHint(L, { "retired.command": "Ctrl+/" }), "Ctrl+/", "a key saved under an id no command has runs nothing: the key comments, and the menu says so");
  assert.equal(menuHint(L, { "chat.navBack": "Ctrl+Shift+/" }, false), "", "one of the chat's own commands saved there takes the key on the chat page alone");
  assert.equal(menuHint(L, { "chat.navBack": "Ctrl+Shift+/" }), "", "and in the shell");
  assert.equal(menuHint(L, { "log.open": "Ctrl+Alt+L" }), "Ctrl+/", "a saved binding on another chord changes nothing");
  assert.equal(menuHint(L, { [ID]: "Mod+Alt+M", "log.open": "Ctrl+/" }), "Ctrl+Alt+M", "the comment key rebound prints its own chord");
  // the comment key's own saved binding and another on the same chord: the shell's dispatcher runs the one registered later
  // (Open the log, after the comment command), and on the chat page alone nothing runs the log
  assert.equal(menuHint(L, { [ID]: "Ctrl+Shift+/", "log.open": "Ctrl+/" }), "", "in the shell Ctrl+/ opens the log");
  assert.equal(menuHint(L, { [ID]: "Ctrl+Shift+/", "log.open": "Ctrl+/" }, false), "Ctrl+/", "on the chat page alone it comments");
  assert.equal(menuHint(M, { "log.open": "Meta+/" }), "", "⌘/ saved for the log on a Mac");
  assert.equal(menuHint(M, { "log.open": "Shift+Meta+/" }), "⌘/", "⌘⇧/ is another chord on a Mac");
  // and in every one of these worlds the key does what the menu says: it comments exactly when the menu names it
  const worlds: Array<[string, Record<string, string>, boolean, Array<{ id: string }>]> = [
    [L, {}, true, []], [L, { "log.open": "Ctrl+Shift+/" }, true, []], [L, { "log.open": "Ctrl+Shift+/" }, false, []],
    [L, { [HOT]: "Ctrl+/" }, true, [{ id: HOT }]], [L, { "retired.command": "Ctrl+/" }, true, []], [L, { "chat.navBack": "Ctrl+Shift+/" }, false, []],
    [L, { "chat.navBack": "Ctrl+Shift+/" }, true, []], [L, { [ID]: "Ctrl+Shift+/", "log.open": "Ctrl+/" }, true, []],
    [L, { [ID]: "Ctrl+Shift+/", "log.open": "Ctrl+/" }, false, []], [M, { "log.open": "Meta+/" }, true, []], [M, { "log.open": "Shift+Meta+/" }, true, []],
  ];
  for (const [plat, ov, shell, extra] of worlds) {
    const ran = chatDocument(plat, ov, shell, extra)("/", "Slash", isMac(plat) ? { meta: true } : { ctrl: true }).ran;
    assert.equal(ran.includes(ID), menuHint(plat, ov, shell, extra) !== "", JSON.stringify([plat, ov, shell]) + ": the key ran " + JSON.stringify(ran));
    assert.ok(ran.length <= 1, "one key, at most one command: " + JSON.stringify(ran));
  }
});

// Disclosed as it stands, not a rule of the comment key: the dialog also records a chord with no Ctrl, Alt or Meta (F2,
// Shift+F2, Shift+/), the chat's handler reads no such chord (plain typing is never a command), and only the shell's
// dispatcher runs one, from outside a text field. The menu names it all the same, so on the chat page alone it names a key
// that runs nothing. Blanking the menu there would change what it shows; this test pins it as it stands.
test("a comment key rebound to a chord with no Ctrl, Alt or Meta runs only from the shell's dispatcher, and the menu names it on every page", () => {
  const L = "Linux x86_64";
  for (const [chord, key, code, m] of [["Shift+F2", "F2", "F2", { shift: true }], ["F2", "F2", "F2", {}], ["Shift+/", "/", "Digit7", { shift: true }]] as Array<[string, string, string, Mods]>) {
    const ov = { [ID]: chord };
    assert.equal(bindable(chord), true, chord + ": the dialog records it");
    assert.deepEqual(keyHandler(L, ov, false)(key, code, m), LEFT, chord + ": the chat's handler leaves it");
    assert.equal(menuHint(L, ov, false), chord, chord + ": the menu names it on the chat page alone");
    assert.deepEqual(chatDocument(L, ov, false)(key, code, m).ran, [], chord + ": and there the key runs nothing");
    assert.equal(menuHint(L, ov, true), chord, chord + ": the menu names it in the shell");
    assert.deepEqual(chatDocument(L, ov, true)(key, code, m, IN_TRANSCRIPT).ran, [ID], chord + ": in the shell the dispatcher comments from the transcript");
    assert.deepEqual(chatDocument(L, ov, true)(key, code, m, IN_BOX).ran, [], chord + ": and runs nothing from the message box, where the key is typing");
  }
});

test("the tooltips name each command's own chord whatever else is saved on it: the chat's jump and nav keys still answer there", () => {
  withPlatform("Linux x86_64", () => {
    withOverrides({ "log.open": "Ctrl+Alt+ArrowUp" }, () => {
      assert.equal(keyHint("chat.prevMine"), "Ctrl+Alt+↑", "a jump move's key, which the chat's jump handler still takes (the chat document test above)");
      assert.equal(titleWithKey(MOVE_TITLE.prevMine, "chat.prevMine"), MOVE_TITLE.prevMine + " (Ctrl+Alt+↑)", "the jump button's tooltip");
    });
    withOverrides({ "log.open": "Ctrl+M" }, () => assert.equal(keyHint("chat.navBack"), "Ctrl+M"));
    withOverrides({ "log.open": "Ctrl+Shift+O" }, () => assert.equal(keyHint("session.new"), displayChord(DEFAULT_CHORDS["session.new"], false)));
    withOverrides({ "log.open": "Ctrl+Shift+/" }, () => assert.equal(keyHint("log.open"), "Ctrl+/", "a saved binding prints as it reads"));
  });
});

test("the palette's chips: Comment's goes blank while its chord runs another command; the other commands' chips keep their chord", () => {
  const L = "Linux x86_64";
  assert.equal(paletteChip(L, {}, ID), "Ctrl+/");
  assert.equal(paletteChip("MacIntel", {}, ID), "⌘/");
  assert.equal(paletteChip(L, { "log.open": "Ctrl+Shift+/" }, ID), undefined, "Comment's chip names no key that opens the log");
  assert.equal(paletteChip(L, { "log.open": "Ctrl+Shift+/" }, "log.open"), "Ctrl+/", "the log's chip names it");
  assert.equal(paletteChip(L, { "retired.command": "Ctrl+/" }, ID), "Ctrl+/", "a key saved under an id no command has takes nothing");
  assert.equal(paletteChip(L, { [ID]: "Mod+Alt+M" }, ID), "Ctrl+Alt+M", "a rebound command shows its binding, never a stale default");
  assert.equal(paletteChip(L, { "log.open": "Ctrl+Alt+ArrowUp" }, "chat.prevMine"), "Ctrl+Alt+↑",
    "a jump move's chip keeps its chord: the chat's jump handler still answers it, whatever the dispatcher picks");
});

// ── the command, executed from render.ts's own source ─────────────────────────────────────────────
const lift = (name: string): string => liftFrom(RENDER, name);
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
  // the menu line's pick keeps its pointer (MENU_HINT reads the line whole), and its sub-line, the line's own hint
  // expression, is executed by the menu test above and end to end in the served comment-key lab (cases 5, 6, 11, 13, 14, 17)
  assert.ok(MENU_HINT.length > 0);
  const h = listenerSrc(COMMENT_KEY).src;
  assert.match(h, /if \(!\(e\.ctrlKey \|\| e\.altKey \|\| e\.metaKey\) \|\| e\.repeat \|\| e\.isComposing\) return;/);
  // what the handler takes, that it listens in the capture phase (keyHandler asserts the flag it registers with) and that
  // it reads the store per press are executed on its own lifted source above (keyHandler and the tests that use it)
  assert.match(RENDER, /if \(m\.romp === "commentSelection"\) \{ commentOnSelection\("held" in m \? heldCommentAim\(m\.held\) : undefined\); return; \}/);
  assert.match(RENDER, /\(window as any\)\.__rompCommentAim = commentAim;/, "the shell's palette reads the aim through this");
  // the message box's slash menu follows the box's TEXT (a leading "/" that reached it), never the keystroke, so the handler's
  // prevented default is all it takes to keep Mod+/ from opening it; type-to-focus lets every Ctrl/⌘ chord by
  assert.match(RENDER, /const updateSlash = \(\) => \{\n\s*const q = slashQuery\(\);/);
  assert.match(RENDER, /if \(e\.defaultPrevented \|\| e\.altKey \|\| e\.ctrlKey \|\| e\.metaKey\) return;   \/\/ chords are not typing/);
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
