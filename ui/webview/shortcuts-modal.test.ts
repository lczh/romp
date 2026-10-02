// The keyboard-shortcuts dialog (ui/webview/shortcuts-modal.ts): the configurable list the settings
// gear now links to instead of carrying a static table (the user 2026-08-09) — VS Code's grammar:
// click Change, press the chord, conflicts named with an explicit reassign, Backspace unbinds, Reset
// returns the default. DOM-heavy → source pins; the decision rules it renders are the REAL exported
// functions under test in keybindings.test.ts and in comment-key.test.ts (reassignLosers), not a replica. The conflict
// row and its Enter are executed on the real initShortcutsModal over a small DOM stand-in.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { registerCommand } from "./commands";
import { initShortcutsModal } from "./shortcuts-modal";

const ROOT = path.resolve(process.cwd(), "..");
const MODAL = fs.readFileSync(path.join(ROOT, "ui", "webview", "shortcuts-modal.ts"), "utf8");
const MAIN = fs.readFileSync(path.join(ROOT, "ui", "webview", "palette-main.ts"), "utf8");
const GEARJS = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
const KERNEL = fs.readFileSync(path.join(ROOT, "kernel", "kernel.py"), "utf8");
const KEYB = fs.readFileSync(path.join(ROOT, "ui", "webview", "keybindings.ts"), "utf8");

test("wears the shared modal vocabulary at z300, over a 0.55 dim, dashboard untouched behind", () => {
  assert.match(MODAL, /#rkeys-back\{position:fixed;inset:0;z-index:300;/);
  assert.match(MODAL, /background:rgba\(0,0,0,0\.55\)/);
  assert.match(MODAL, /#rkeys\{width:min\(640px,94%\)/);
  assert.match(MODAL, /background:#252526;/);
});

test("recording is a captured card-level listener: chords never type into the filter or leak out", () => {
  assert.match(MODAL, /panel\.addEventListener\("keydown", onRecordKey, true\);/);
  assert.match(MODAL, /if \(!recId\) return;/);
  assert.match(MODAL, /e\.preventDefault\(\);\s*\n\s*e\.stopPropagation\(\);/);
  // a bare modifier is a chord still being built; an unbindable key keeps the recorder listening — and SAYS why on the
  // row (2026-09-10: a bare ` or 1 used to be swallowed silently, so the dialog looked dead)
  assert.match(MODAL, /if \(!ch\) return;\s*.*\n(?:\s*\/\/[^\n]*\n)*\s*if \(!bindable\(ch\)\) \{\n\s*refused = /);
});

test("a conflict is named and resolved explicitly — reassign unbinds the loser visibly", () => {
  assert.match(MODAL, /is used by /);
  assert.match(MODAL, /Enter reassigns it here, Esc keeps it there/);
  // when the row shows, which commands it names, and which commands Enter unbinds are executed on the real dialog below;
  // what the row says around the names ("is used by" before them, what Enter and Esc do after) is pinned here alone, as
  // the source's text
});

// ── the conflict row and its Enter, executed on the real initShortcutsModal over a small DOM stand-in ────────────────
// An element as far as the dialog uses one: its fields, its children (textContent reads them all, and setting it clears
// them, as the DOM's does), its listeners, and a walk over itself and everything under it.
class El {
  id = ""; hidden = false; className = ""; placeholder = ""; spellcheck = false; tabIndex = 0; type = ""; title = ""; value = "";
  dataset: Record<string, string> = {};
  children: El[] = [];
  listeners: Record<string, Array<(e: unknown) => void>> = {};
  private own = "";
  constructor(public tag: string) {}
  get textContent(): string { return this.own + this.children.map((c) => c.textContent).join(""); }
  set textContent(t: string) { this.own = t; this.children = []; }
  get childElementCount(): number { return this.children.length; }
  appendChild(c: El): El { this.children.push(c); return c; }
  addEventListener(t: string, f: (e: unknown) => void): void { (this.listeners[t] ||= []).push(f); }
  fire(t: string, e: unknown): void { for (const f of this.listeners[t] || []) f(e); }
  focus(): void {}
  querySelector(): null { return null; }
  all(): El[] { return [this, ...this.children.flatMap((c) => c.all())]; }
}
type Press = [string, string, { ctrl?: boolean; alt?: boolean; shift?: boolean }];
// Open the dialog over `saved` (the store, a stand-in localStorage restored after), click Change on `forId`'s row, press
// the chord, and read the row's conflict line; with `resetId`, click Reset on that command's row while the conflict row
// is pending and read the line again (afterReset); then press Enter and read the store and the rows the dialog repainted.
function recordOver(saved: Record<string, string>, forId: string, press: Press, resetId?: string) {
  const g: any = globalThis;
  const had = "localStorage" in g, prev = g.localStorage;
  const store: Record<string, string> = { "romp:keys": JSON.stringify(saved) };
  g.localStorage = { getItem: (k: string) => (k in store ? store[k] : null), setItem: (k: string, v: string) => { store[k] = v; } };
  try {
    const body = new El("body");
    const doc = { head: new El("head"), body, defaultView: { addEventListener: () => {} }, createElement: (tag: string) => new El(tag) };
    const dialog = initShortcutsModal(false, doc as unknown as Document);
    dialog.open();
    const row = (id: string): El => { const r = body.all().find((e) => e.dataset.cmd === id); assert.ok(r, "a row for " + id); return r; };
    const change = row(forId).children.find((e) => e.tag === "button" && e.textContent === "Change");
    assert.ok(change, "the row's Change button");
    change.fire("click", { stopPropagation: () => {} });
    const key = ([k, code, m]: Press) => dialog.feed({ key: k, code, ctrlKey: !!m.ctrl, altKey: !!m.alt, shiftKey: !!m.shift, metaKey: false,
      preventDefault: () => {}, stopPropagation: () => {} } as unknown as KeyboardEvent);
    assert.equal(key(press), true, "the recorder takes the key");
    const line = (): string => { const c = row(forId).children.find((e) => e.className === "rkeys-conflict"); return c ? c.textContent : "(no conflict line)"; };
    const said = line();
    let afterReset = "(no Reset clicked)";
    if (resetId) {
      const reset = row(resetId).children.find((e) => e.tag === "button" && e.textContent === "Reset");
      assert.ok(reset, "the Reset button on " + resetId + "'s row while the conflict row is pending");
      reset.fire("click", { stopPropagation: () => {} });
      afterReset = line();
    }
    key(["Enter", "Enter", {}]);
    // marks: the row's yield marks, each the note (rkeys-none) right after the row's chip, read as the title it quotes; the
    // mark's other words are the dialog's copy, which tab-cycle.test.ts pins as source text
    const marks = (id: string): string[] => row(id).children.filter((e, i, k) => e.className === "rkeys-none" && i > 0 && k[i - 1].className === "rkeys-chip")
      .map((e) => { const t = e.textContent, q = t.indexOf("“"); return q >= 0 ? t.slice(q, t.lastIndexOf("”") + 1) : "(no quoted title in: " + t + ")"; });
    return { said, afterReset, stored: JSON.parse(store["romp:keys"]) as Record<string, string>, marks };
  } finally {
    if (had) g.localStorage = prev; else delete g.localStorage;
  }
}

// The commands these tests register, in this order (the dispatcher gives a chord two saved bindings share to the later)
function registerCommands(): void {
  for (const [id, title] of [["chat.navBack", "Navigate back in the chat"], ["chat.commentSelection", "Comment on selection"],
    ["log.open", "Open the log"], ["net.open", "Remote kernels"], ["usage.open", "Token usage"],
    ["chat.prevMine", "Go to your previous message"]]) registerCommand({ id, title, run: () => {} });
}
// rowNames: the commands the row `said` names for `chord`, its text from the first “ to the last ” before the dash: each
// title in quotes, the last joined by "and", the others by commas. The chord must stand as a word in the text before the
// first title, or the result is a note that names the chord and quotes what the row said, which no expected value
// equals. The row's other words ("is used by", and what Enter and Esc do after the dash) are the dialog's copy, pinned by
// the source check above.
function rowNames(said: string, chord: string): string {
  const head = said.split(" \u2014 ")[0], q = head.indexOf("“");
  return q >= 0 && head.slice(0, q).split(" ").includes(chord) ? head.slice(q, head.lastIndexOf("”") + 1) : "(no names for " + chord + " in: " + said + ")";
}

test("the conflict row names every command Enter takes the key from, and Enter unbinds every one of them: the real dialog", () => {
  registerCommands();
  // nothing saved: Ctrl+M recorded for Remote kernels names Navigate back, whose default it is, and Enter unbinds it alone
  const alone = recordOver({}, "net.open", ["m", "KeyM", { ctrl: true }]);
  assert.equal(rowNames(alone.said, "Ctrl+M"), "“Navigate back in the chat”");
  assert.deepEqual(alone.stored, { "chat.navBack": "", "net.open": "Ctrl+M" });
  // Open the log saved on Navigate back's Ctrl+M: the row names the log, which the dispatcher runs there, and Navigate back,
  // whose own handler would keep taking the key in the chat, and Enter unbinds both
  const both = recordOver({ "log.open": "Ctrl+M" }, "net.open", ["m", "KeyM", { ctrl: true }]);
  assert.equal(rowNames(both.said, "Ctrl+M"), "“Open the log” and “Navigate back in the chat”");
  assert.deepEqual(both.stored, { "log.open": "", "chat.navBack": "", "net.open": "Ctrl+M" });
  // two saved keys that read as one chord now (a Ctrl+Shift+/ saved before #39 beside a Ctrl+/): the row names the one the
  // key runs, then the other, Enter unbinds both, and the comment key's default stays, marked as yielding to the new key
  const shared = recordOver({ "log.open": "Ctrl+/", "usage.open": "Ctrl+Shift+/" }, "net.open", ["/", "Slash", { ctrl: true }]);
  assert.equal(rowNames(shared.said, "Ctrl+/"), "“Token usage” and “Open the log”");
  assert.deepEqual(shared.stored, { "log.open": "", "usage.open": "", "net.open": "Ctrl+/" });
  assert.deepEqual(shared.marks("chat.commentSelection"), ["“Remote kernels”"]);
  // three saved keys on that chord: the one the key runs first, then the others in the dialog's order, joined by a comma
  // and an "and"; Enter unbinds all three
  const three = recordOver({ "log.open": "Ctrl+/", "usage.open": "Ctrl+Shift+/", "chat.navBack": "Ctrl+Shift+/" }, "net.open", ["/", "Slash", { ctrl: true }]);
  assert.equal(rowNames(three.said, "Ctrl+/"), "“Token usage”, “Navigate back in the chat” and “Open the log”");
  assert.deepEqual(three.stored, { "log.open": "", "usage.open": "", "chat.navBack": "", "net.open": "Ctrl+/" });
});

test("a key recorded again for the command that holds it names the chat command whose default is on it, and Enter frees it: the real dialog", () => {
  registerCommands();
  // Open the log saved on Navigate back's Ctrl+M (or Go to your previous message's Ctrl+Alt+↑) and recorded again for the
  // log: the dispatcher already runs the log there, but the chat command's own handler takes the key in the chat, so the
  // row names that command, and Enter unbinds its default and keeps the log's key
  for (const [chord, own, title, press] of [["Ctrl+M", "chat.navBack", "Navigate back in the chat", ["m", "KeyM", { ctrl: true }]],
    ["Ctrl+Alt+ArrowUp", "chat.prevMine", "Go to your previous message", ["ArrowUp", "ArrowUp", { ctrl: true, alt: true }]]] as Array<[string, string, string, Press]>) {
    const again = recordOver({ "log.open": chord }, "log.open", press);
    assert.equal(rowNames(again.said, chord.replace("ArrowUp", "↑")), "“" + title + "”");
    assert.deepEqual(again.stored, { "log.open": chord, [own]: "" }, "Enter left " + own + "'s default on " + chord);
  }
  // with nothing else on the chord the key is recorded at once, no row: Ctrl+M for Navigate back, its own default
  const own = recordOver({}, "chat.navBack", ["m", "KeyM", { ctrl: true }]);
  assert.equal(own.said, "(no conflict line)");
  assert.deepEqual(own.stored, { "chat.navBack": "Ctrl+M" });
});

test("a Reset on the named command's row while the conflict row is pending leaves nothing on the chord: the row keeps the name it showed, and Enter unbinds nothing: the real dialog", () => {
  registerCommands();
  // Open the log saved on Ctrl+K, no command's default, and Ctrl+K recorded for Remote kernels: the row names the log. The
  // log's row keeps its Reset button while the row is pending, and a click on it empties the store, so Enter takes the key
  // from no command; the row still names the log, the name it first showed (not "undefined"), and Enter stores Remote
  // kernels' key and unbinds nothing
  const reset = recordOver({ "log.open": "Ctrl+K" }, "net.open", ["k", "KeyK", { ctrl: true }], "log.open");
  assert.equal(rowNames(reset.said, "Ctrl+K"), "“Open the log”");
  assert.equal(rowNames(reset.afterReset, "Ctrl+K"), "“Open the log”", "the row after the log's Reset");
  assert.deepEqual(reset.stored, { "net.open": "Ctrl+K" });
});

test("Backspace unbinds, Reset returns the default, the chips read the EFFECTIVE binding", () => {
  assert.match(MODAL, /if \(e\.key === "Backspace" \|\| e\.key === "Delete"\) \{ commit\(recId, ""\); return; \}/);
  // a bound command's listening row offers the same unbind as a button (the user 2026-09-11: the tab menu's one
  // "Update hot key…" row covers changing and removing)
  assert.match(MODAL, /if \(effectiveChord\(c\.id, c\.chord, overrides, mac\)\) \{\s*const rm = doc\.createElement\("button"\);[\s\S]{0,200}?rm\.textContent = "Remove";\s*rm\.addEventListener\("click", \(e\) => \{ e\.stopPropagation\(\); commit\(c\.id, ""\); \}\);/);
  assert.match(MODAL, /saveOverride\(c\.id, null\); render\(\);/);
  assert.match(MODAL, /const eff = effectiveChord\(c\.id, c\.chord, overrides, mac\);/);
  assert.match(MODAL, /textContent = "not bound";/);
});

test("the solo recording row's Remove shows WITHOUT a hover (review 2026-09-14): the keyboard flow never hovers, and the tab menu's sub-line promises the button", () => {
  // the full dialog's per-row buttons (Change, Reset) still wait for the pointer: dozens of rows, one hovered
  assert.match(MODAL, /"\.rkeys-act\{flex:0 0 auto;visibility:hidden;/);
  assert.match(MODAL, /"\.rkeys-row:hover \.rkeys-act\{visibility:visible\}"/);
  // …but the listening row's own button is visible at once: openFor's row is reached from the tab menu with no
  // pointer on it, and a hidden Remove made the sub-line's promise ("or remove it") a lie
  assert.match(MODAL, /"\.rkeys-row\.recording \.rkeys-act\{visibility:visible\}"/,
    "the recording row's buttons show (three class selectors: it wins the base rule's visibility:hidden on specificity, whatever the order)");
  // the Remove IS an .rkeys-act on that row
  assert.match(MODAL, /row\.className = "rkeys-row" \+ \(c\.id === recId \? " recording" : ""\);[\s\S]{0,1400}?if \(c\.id === recId\) \{[\s\S]{0,1400}?rm\.className = "rkeys-act";\s*rm\.textContent = "Remove";/);
});

test("Escape is one level at a time and owned by the shell chain: recording → cancel, open → close", () => {
  // the recorder deliberately does NOT handle Escape; the shell's Escape chain calls close(),
  // whose first level cancels the recording and reports consumed
  assert.match(MODAL, /if \(e\.key === "Escape"\) return;\s*\/\/ the shell Escape chain routes it/);
  assert.match(MODAL, /if \(recId\) \{ cancelRecord\(\); return true; \}/);
  assert.match(MODAL, /if \(!back \|\| back\.hidden\) return false;/);
  // …and the kernel's chain asks this dialog FIRST (topmost, z300)
  assert.match(KERNEL, /if\(window\.__rompKeysClose&&window\.__rompKeysClose\(\)\)\{closed=true;\}/);
});

test("the built-in section keeps the non-command keys — without an Enter-to-send row — and the recorder refuses them", () => {
  // the list lives in the pure module now (2026-09-10): the dialog renders it AND refuses a chord it owns
  assert.match(KEYB, /export const BUILT_IN: Array<\[string, string\]> = \[/);
  assert.match(KEYB, /\["Shift\+Enter", "New line in the composer"\]/);
  assert.match(KEYB, /\["Ctrl\+C", "Interrupt the session \(composer\)"\]/);
  assert.match(KEYB, /\["Alt\+Arrows", "Move focus between panes"\]/);
  assert.doesNotMatch(KEYB, /Send message/);
  assert.doesNotMatch(MODAL, /const BUILT_IN/);
  assert.match(MODAL, /for \(const \[chord, what\] of BUILT_IN\) \{/, "the dialog renders the shared list");
  assert.match(MODAL, /const own = builtInOwner\(ch, mac\);\n\s*if \(own\) \{\n\s*refused = displayChord\(ch, mac\) \+ " is built in \(" \+ own\.charAt\(0\)\.toLowerCase\(\) \+ own\.slice\(1\) \+ "\) — pick another";\n\s*render\(\);\n\s*return;/,
    "a built-in chord is said on the row, never bound dead");
});

test("reachable from the palette, the gear's customize link, and __rompKeysOpen", () => {
  assert.match(MAIN, /registerCommand\(\{ id: "keys\.open", title: "Keyboard shortcuts", run: \(\) => keys\.open\(\) \}\);/);
  assert.match(MAIN, /w\.__rompKeysOpen = \(\) => keys\.open\(\);/);
  assert.match(MAIN, /if \(e\.data && e\.data\.romp === "openKeys"\) keys\.open\(\);/);
  // the gear's link: web shell only (VS Code points at its own Keyboard Shortcuts editor), and it
  // closes the settings modal first so the two never stack
  assert.match(GEARJS, /id=rs-keys-web hidden/);
  assert.match(GEARJS, /id=rs-keys-vsc hidden/);
  assert.match(GEARJS, /closeSettings\(\); try \{ window\.parent\.postMessage\(\{ romp: 'openKeys' \}, '\*'\); \}/);
  assert.match(GEARJS, /search "rompChat" in Keyboard Shortcuts/);
  // the static list is gone — the dialog is the one home for shortcuts
  assert.doesNotMatch(GEARJS, /<kbd>Enter<\/kbd><\/span><span class=rs-key-desc>Send message/);
  assert.doesNotMatch(GEARJS, /Jump to the session tabs/);
});

test("while the dialog is up the dispatcher stands down — browsing the list can't fire commands", () => {
  assert.match(MAIN, /if \(keys\.isOpen\(\)\) \{ keys\.feed\(e\); return; \}\s*.*never dispatch under it/);
});
