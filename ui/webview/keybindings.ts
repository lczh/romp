// Keyboard bindings for the shell's commands (the user 2026-08-09, who wanted the shortcuts
// configurable "the way VS Code does it": record a chord, see conflicts, reset). This module is the
// PURE half — chord normalization, display, the localStorage store, conflict resolution, and the
// dispatch decision — so every rule here runs under real tests; the DOM (the dialog, the key wiring)
// lives in shortcuts-modal.ts / palette-main.ts. The VS Code surface does NOT use any of this: there
// the same actions are contributed commands, rebindable in VS Code's own Keyboard Shortcuts editor.
//
// A chord is a normalized string: modifiers in the fixed order Ctrl, Alt, Shift, Meta, then the key —
// "Meta+Shift+O", "Ctrl+P", "Alt+ArrowLeft". Single-character keys are uppercased; named keys keep
// their KeyboardEvent.key spelling. Defaults are declared with the "Mod" placeholder ("Mod+O"), which
// resolves to Meta on a Mac and Ctrl elsewhere — one declaration, both platforms.

import { DEFAULT_CHORDS } from "./commands";

export type Bindings = Record<string, string>;   // command id → chord ("" = deliberately unbound)

const KEY = "romp:keys";
export const KEYS_EVENT = "romp:keys";           // window event raised on every save (same-document)

// ── chord model ───────────────────────────────────────────────────────────────────────────────────

const MOD_ORDER = ["Ctrl", "Alt", "Shift", "Meta"];

// The chord a keydown spells, or null when it's a bare modifier (still being held, nothing to bind).
// Option on a Mac composes a character (⌥M types µ, ⌥E is a dead key), so a ⌘⌥ keydown's `key` names the glyph and not
// the key pressed; Command suppresses the typing, so there the physical key (`code` KeyM → M) is the chord's, or ⌘⌥M would
// never match its own binding (2026-09-30, for the comment key's first default; it serves any ⌘⌥ binding a reader
// records). Only with Meta held: without it, Ctrl+Alt is AltGr on Windows, and the glyph it spells (µ on a German layout)
// is the reader's text, never a chord.
// A "/" is the character, whichever key and Shift state the layout types it with: a German or Nordic keyboard types it as
// Shift+7, a French one as Shift+":", so a keydown spelling "/" with Shift held is that layout's plain "/" and Ctrl+/ is
// what the hint promises there too (the comment key, 2026-09-30). The physical key a US keyboard has "/" on (`code` Slash)
// is not used: on a German layout it types "-", and Ctrl+- is the browser's zoom out. Only "/", the comment key's
// character: every other symbol keeps its Shift. Only with Ctrl or Alt held: a "/" typed with Shift alone stays Shift+/,
// as the dialog recorded it before #39, because without that Shift it would be a bare "/", the key a US keyboard types
// with no Shift at all, and a Shift+/ saved before #39 would run on it (review, 2026-09-30). And only without Meta: a
// keydown spelling "/" with Meta and Shift held keeps its Shift, so ⌘⇧/ never reads as ⌘/ (the review of #39) and ⌃⌘⇧/
// never as ⌃⌘/; with neither Ctrl nor Alt held, the clause before keeps that Shift too. The Mac case rests on reading
// the source of w3c-keyname, the key reader CodeMirror ships, and the engines', not on a run on a Mac: they say that on
// macOS a key held with ⌘ and Shift, and neither Ctrl nor Alt, reports its `key` as if Shift were up, which would spell
// a US Mac's ⌘⇧/, the Help menu's key, as "/" with Shift. Whether a page receives that key before the Help menu takes it
// is unverified. The rule keeps Shift under any Meta, not only where those sources say Shift is lost:
// with Ctrl or Alt held too, or off a Mac (where Meta is the Windows or Super key), a "/" spelled with Shift comes from
// a key that types "/" with Shift held, such as the numpad's divide key or a German layout's 7, and a binding recorded
// from it keeps that Shift. resolveChord applies the same rule to a saved binding, so a chord the dialog recorded
// before #39 still matches the keys that recorded it.
function shiftCounts(key: string, ctrlOrAlt: boolean, meta: boolean): boolean {
  return key !== "/" || meta || !ctrlOrAlt;
}
export function chordOf(e: { key: string; code?: string; ctrlKey: boolean; altKey: boolean; shiftKey: boolean; metaKey: boolean }): string | null {
  let k = e.key;
  if (!k || k === "Control" || k === "Alt" || k === "Shift" || k === "Meta") return null;
  if (e.metaKey && e.altKey && (k === "Dead" || (k.length === 1 && k.charCodeAt(0) > 0x7f))) {
    const phys = /^(?:Key([A-Z])|Digit([0-9]))$/.exec(e.code || "");
    if (phys) k = phys[1] || phys[2];
  }
  const parts: string[] = [];
  if (e.ctrlKey) parts.push("Ctrl");
  if (e.altKey) parts.push("Alt");
  if (e.shiftKey && shiftCounts(k, e.ctrlKey || e.altKey, e.metaKey)) parts.push("Shift");
  if (e.metaKey) parts.push("Meta");
  parts.push(k.length === 1 ? k.toUpperCase() : k);
  return parts.join("+");
}

// A default's "Mod" resolves per platform, and the result is NORMALIZED to the canonical modifier
// order — every comparison flows through here, so a default declared "Mod+Shift+O" (Meta+Shift) and
// the keydown's own spelling (Shift+Meta) land on the same string. The executed test caught the
// mismatch: without the re-sort, no Shift-carrying default ever matched a real keypress.
// chordOf's Shift rule for "/" applies here too, so a SAVED chord reads the way the keys that recorded it spell now: the
// dialog stored a German Ctrl+Shift+7 as Ctrl+Shift+/ before #39, and it reads as Ctrl+/, so the binding still fires and
// still outranks the comment key's default there (the review of #39). A saved Shift+Meta+/ keeps its Shift, as chordOf
// does, and so does a saved Shift+/ with no Ctrl or Alt. The rule has two costs. A binding saved before #39 on a "/" chord
// with Ctrl or Alt and Shift and without Meta (Ctrl+Shift+/, Alt+Shift+/, Ctrl+Alt+Shift+/) now shares its chord with one
// saved on the same chord without Shift (Ctrl+/, Alt+/, Ctrl+Alt+/; a US numpad's divide key could record both), whether
// that one was saved before #39 or after it, while #39's rule still kept the two apart; chordMap gives the shared chord to
// the command registered later (the dialog marks the other as yielding), though in the chat a handler of the chat's own
// that reads the chord (chatAnswers) takes it for one of its commands saved there, whatever chordMap picks (yieldsTo
// lists where the two differ). And a binding the dialog recorded under #39's rule from a "/" keydown with Meta and Shift
// was stored without that Shift, since #39 dropped it under Meta too, so it now answers to the same chord without Shift
// (a ⌘⇧/ stored as Meta+/ answers to ⌘/) and not to the keys that recorded it: any keydown that spells "/" with Meta and
// Shift held. By the sources chordOf's rule cites (read, not run on a Mac), those include a US Mac's ⌘⇧/ where the page
// receives it, ⌘⇧ or ⌃⌘⇧ with the numpad's divide key, and ⌃⌘⇧ with a German Mac's 7; off a Mac, the Windows or Super
// key with Shift and a German 7, where the system leaves that key to the page.
export function resolveChord(chord: string, mac: boolean): string {
  const parts = chord.replace(/(^|\+)Mod(\+)/, (m, a, b) => a + (mac ? "Meta" : "Ctrl") + b).split("+");
  const key = parts.pop() || "";
  const mods = MOD_ORDER.filter((m) => parts.includes(m) && (m !== "Shift" || shiftCounts(key, parts.includes("Ctrl") || parts.includes("Alt"), parts.includes("Meta"))));
  return [...mods, key].join("+");
}

// Chords a binding may NOT be: bare typing/navigation keys the panes own (Escape closes modals,
// Enter/Tab/Space/Backspace type). With modifiers they're fine (Ctrl+Enter is a real chord).
export function bindable(chord: string): boolean {
  if (chord.includes("+")) {
    const key = chord.split("+").pop() || "";
    return key !== "Escape";   // Escape stays the universal close, whatever the modifiers
  }
  if (/^(Escape|Enter|Tab| |Backspace|Delete)$/.test(chord)) return false;   // the panes own these bare
  return chord.length > 1;     // bare named keys (F1, Home) are fine; a bare letter would fire while typing
}

// The chat/pane keys that are BEHAVIOR, not bindable commands — listed so the one dialog answers "what can my
// keyboard do" completely, clearly marked built-in (Enter-to-send is deliberately not here: a typing key nobody
// looks up, the user 2026-08-09). Pure data, here rather than in the dialog, because the recorder REFUSES these
// too (2026-09-10): a hot key recorded as Alt+ArrowLeft would never fire — the shell's pane-focus script takes
// the key first — so the row says who owns it instead of binding a dead chord.
export const BUILT_IN: Array<[string, string]> = [
  ["Shift+Enter", "New line in the composer"],
  ["Escape", "Leave the composer / close a panel"],
  ["Ctrl+C", "Interrupt the session (composer)"],
  ["Alt+Arrows", "Move focus between panes"],
];

// A command's FIXED key: a built-in the pane owns that does what the command does, shown on the command's own
// row beside its rebindable chord rather than in the built-in section (the maintainer, 2026-09-23: the arrows
// are the natural keys and belong in the keymap). Command id → [chord spec, what the recorder says when it
// refuses the chord]. The bare arrows step sessions from the tab bar and anywhere in the chat outside a text
// field; they cannot be bound (they are the feed's card cursor, the file browser's back, the viewer's step in
// their own panes), so the recorder refuses them exactly as it refuses the rows above.
export const FIXED_KEYS: Record<string, [string, string]> = {
  "chat.nextTab": ["ArrowRight", "Go to the next session, from the tab bar"],
  "chat.prevTab": ["ArrowLeft", "Go to the previous session, from the tab bar"],
};

// The built-in behaviour a chord already belongs to (its description), or null when it is free to bind.
// "Alt+Arrows" stands for the four arrows; " / " separates alternatives; Ctrl here is the literal key.
// A command's fixed key (FIXED_KEYS) is a built-in too.
export function builtInOwner(chord: string, mac: boolean): string | null {
  const want = resolveChord(chord, mac);
  for (const [spec, what] of [...BUILT_IN, ...Object.values(FIXED_KEYS)]) {
    for (const alt of spec.split(" / ")) {
      const forms = alt.endsWith("+Arrows")
        ? ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].map((k) => alt.slice(0, -"Arrows".length) + k)
        : [alt];
      if (forms.some((f) => resolveChord(f, mac) === want)) return what;
    }
  }
  return null;
}

// Display: ⌘⇧O on a Mac (symbols, no separators), Ctrl+Shift+O elsewhere.
export function displayChord(chord: string, mac: boolean): string {
  const c = resolveChord(chord, mac);
  const KEYCAP: Record<string, string> = { ArrowLeft: "←", ArrowRight: "→", ArrowUp: "↑", ArrowDown: "↓" };
  const parts = c.split("+").map((p) => KEYCAP[p] || p);
  if (!mac) return parts.join("+");
  const SYM: Record<string, string> = { Ctrl: "⌃", Alt: "⌥", Shift: "⇧", Meta: "⌘" };
  return parts.map((p) => SYM[p] || p).join("");
}

// ── the store: user overrides over per-command defaults ──────────────────────────────────────────

export function loadOverrides(): Bindings {
  try {
    const d = JSON.parse(localStorage.getItem(KEY) || "{}");
    return d && typeof d === "object" ? (d as Bindings) : {};
  } catch (e) { return {}; }
}

// chord = the new binding; "" = unbind; null = forget the override (back to the default)
export function saveOverride(id: string, chord: string | null): void {
  const all = loadOverrides();
  if (chord === null) delete all[id]; else all[id] = chord;
  try { localStorage.setItem(KEY, JSON.stringify(all)); } catch (e) { /* storage full/blocked */ }
  try { window.dispatchEvent(new Event(KEYS_EVENT)); } catch (e) { /* non-DOM (tests) */ }
}

// The chord a command answers to right now: the override when one exists, else its default, resolved.
export function effectiveChord(id: string, defaultChord: string | undefined, overrides: Bindings, mac: boolean): string {
  const o = overrides[id];
  if (o !== undefined) return o === "" ? "" : resolveChord(o, mac);
  return defaultChord ? resolveChord(defaultChord, mac) : "";
}

// chord → command id for the dispatcher, built from the full command list. A chord the reader SAVED for a
// command outranks any command's DEFAULT on the same chord (review, 2026-09-23): a default that lands on a chord
// someone had already bound — the session pair shipping on Ctrl+Alt+arrows, where a saved "Focus the next chat
// column" could sit — must not take the key from them; the dialog marks such a default as yielding (yieldsTo).
// Two SAVED bindings on one chord can only enter via a hand-edited store (the dialog refuses them), or through
// resolveChord's "/" rule: a "/" chord with Ctrl or Alt and Shift saved before #39 (Ctrl+Shift+/, Alt+Shift+/) beside the
// same chord without Shift (Ctrl+/, Alt+/), saved before #39 or after it, now reads as that chord. The LAST registered
// command wins there in the dispatcher, and the dialog marks the other as yielding to it (yieldsTo), so the loser is
// visible, not silently dead.
export function chordMap(cmds: { id: string; chord?: string }[], overrides: Bindings, mac: boolean): Map<string, string> {
  const m = new Map<string, string>();
  const saved = new Set<string>();
  for (const c of cmds) {                                   // the reader's own bindings first…
    if (overrides[c.id] === undefined) continue;
    const ch = effectiveChord(c.id, c.chord, overrides, mac);
    if (ch) { m.set(ch, c.id); saved.add(ch); }
  }
  for (const c of cmds) {                                   // …then every default whose chord no saved binding holds
    if (overrides[c.id] !== undefined) continue;
    const ch = effectiveChord(c.id, c.chord, overrides, mac);
    if (ch && !saved.has(ch)) m.set(ch, c.id);
  }
  return m;
}

// The command the shell's dispatcher runs on `id`'s chord instead of `id` (the one `id` yields to), or null: `id` is
// unbound, or the chord is its own. chordMap decides, so the dialog names the command the dispatcher runs on the key, from
// the shell and from the panes the dispatcher listens in: a default on a chord the reader saved for another command
// yields to it, and of two SAVED bindings on one chord the one registered earlier yields to the later one (review,
// 2026-09-30: this used to name the first saved binding it found, which the key did not run when a later one shared the
// chord, and it marked no saved row).
// In the chat, the handlers for the nav trail and the jump moves (CHAT_KEY_COMMANDS below) answer the chords they read
// (chatAnswers) before the dispatcher hears them, whatever it would pick, each running the first of its commands on the
// chord, and only the comment key's handler steps aside, for one of those saved on its chord (render.ts
// commentKeyTakenBy). So, through those two handlers, in four kinds of store the chat runs something other than the
// command the dialog names, or something besides it; "the disclosed residual" test in comment-key.test.ts pins each by its
// number: (1) a key saved for another command on a chat command's default still runs the chat command there, though the
// dialog marks that default as yielding; (2) a chat command's SAVED binding the dialog marks as yielding, the earlier
// of two saved on one chord (two "/" chords that resolveChord now reads as one, or any chord in a hand-edited store),
// still runs there when its handler reads the chord, from the chat viewer's code editor too; (3) a nav command and a
// jump move saved on one such chord, which both their handlers read, both run there on one press; and (4) the comment
// key's own binding saved on a chat command's default runs both commands there on one press. The dialog reaches (4):
// the recorder unbinds that default when it gives the key to the comment command, and the default's Reset puts it back
// without checking for a conflict. (1) and (4) predate #39 ((4) since #35 added the comment key, whose default #39
// moved to Mod+/). (2) and (3) come with resolveChord's "/" rule. In a hand-edited store, where they could arise before,
// the nav and jump handlers run what they ran before; what is new there is the mark on the earlier saved row, which this
// function used to leave off (above), and, when the comment key's own binding is one of those saved on the chord, that
// the comment key steps aside instead of running beside them. Making the nav and jump handlers ask the dispatcher is a
// change of its own.
export function yieldsTo(id: string, cmds: { id: string; chord?: string }[], overrides: Bindings, mac: boolean): string | null {
  const c = cmds.find((x) => x.id === id);
  const ch = effectiveChord(id, c && c.chord, overrides, mac);
  if (!ch) return null;
  const pick = chordMap(cmds, overrides, mac).get(ch);
  return pick && pick !== id ? pick : null;
}

// The command the shell's dispatcher runs on `chord` now, when that is not `forId` (the conflict the dialog names), or
// null. chordMap's pick, so never a default that already yields there.
export function conflictOf(chord: string, forId: string, cmds: { id: string; chord?: string }[], overrides: Bindings, mac: boolean): string | null {
  const pick = chordMap(cmds, overrides, mac).get(resolveChord(chord, mac));
  return pick && pick !== forId ? pick : null;
}

// The commands whose saved keys the chat answers itself, in its own window-capture handlers (render.ts), each group
// with the modifiers its handler needs before it reads a chord at all (the session picker's handler there takes Ctrl or
// Meta with O, Shift or not, reads no saved binding, and stands down inside the shell, so it is not one of them):
// Navigate back and forward (the nav trail's handler returns unless Ctrl or Meta is held) and the six jump moves
// (theirs, unless Ctrl, Alt or Meta is). Those handlers run on the chat document with or without the shell, read the
// saved bindings themselves, and take their key before the shell's dispatcher hears it, so in the chat a key one of
// them answers runs that command whatever the dispatcher would pick.
const CHAT_KEY_HANDLERS: ReadonlyArray<readonly [readonly string[], readonly string[]]> = [
  [["chat.navBack", "chat.navForward"], ["Ctrl", "Meta"]],
  [["chat.prevMine", "chat.nextMine", "chat.prevComment", "chat.nextComment", "chat.prevUnread", "chat.nextUnread"], ["Ctrl", "Alt", "Meta"]],
];
export const CHAT_KEY_COMMANDS: readonly string[] = CHAT_KEY_HANDLERS.flatMap(([ids]) => ids);
// Whether the chat's own handler for command `id` answers `chord` (a resolved chord): `id` is one of CHAT_KEY_COMMANDS and
// the chord holds a modifier its handler reads.
export function chatAnswers(id: string, chord: string): boolean {
  const mods = chord.split("+").slice(0, -1);
  return CHAT_KEY_HANDLERS.some(([ids, need]) => ids.includes(id) && need.some((m) => mods.includes(m)));
}

// The commands the recorder's Enter unbinds when it gives `chord` to `forId`, so that afterwards the fresh binding is what
// the key runs from the shell, from the panes the dispatcher listens in and from the chat (except in the file viewer's
// code editor when it is the comment command's own Mod+/, which the editor keeps: editorKeepsKey): the one the dispatcher
// runs there now (conflictOf), every other SAVED binding that reads as the chord, and the DEFAULT of a chat command whose
// own handler answers the chord (chatAnswers). Other defaults on the chord keep their default, marked as yielding:
// inside the shell nothing but the dispatcher runs those (the chat's comment-key handler steps aside there for the
// command the dispatcher picks), and the dispatcher runs the fresh saved binding over them; on a page with no shell,
// where a shell command's binding runs nothing, the comment key's handler still runs its own default. Unbinding only the
// command the dispatcher runs would leave the fresh binding dead when a Ctrl+Shift+/ saved before #39 and a Ctrl+/ saved
// apart from it, which both read as Ctrl+/ under resolveChord's "/" rule, share the chord and the other is registered
// later (review, 2026-09-30); and when a key saved for another command on a chat command's default (Open the log on
// Navigate back's Ctrl+M) is recorded for a third, or again for the command that holds it, it would leave the chat's
// handler taking the key from the chat (review, 2026-10-01). The recorder shows its conflict row whenever this list is
// not empty.
export function reassignLosers(chord: string, forId: string, cmds: { id: string; chord?: string }[], overrides: Bindings, mac: boolean): string[] {
  const want = resolveChord(chord, mac);
  const pick = conflictOf(chord, forId, cmds, overrides, mac);
  const out = pick ? [pick] : [];
  for (const c of cmds) {
    if (c.id === forId || c.id === pick || effectiveChord(c.id, c.chord, overrides, mac) !== want) continue;
    if (overrides[c.id] !== undefined || chatAnswers(c.id, want)) out.push(c.id);
  }
  return out;
}

// The first of `ids` whose SAVED binding resolves to `chord`, or null. The chat's comment-key handler asks it about the
// chat's own commands whose handler answers that chord (render.ts chatKeyCommands, by chatAnswers): a saved binding
// outranks a default (chordMap), and one of those saved on the comment key's chord is taken by its own handler, with or
// without the shell.
export function savedHolder(chord: string, ids: string[], overrides: Bindings, mac: boolean): string | null {
  for (const id of ids) {
    const saved = overrides[id];
    if (typeof saved === "string" && saved && resolveChord(saved, mac) === chord) return id;
  }
  return null;
}

// ── hover discoverability (the user 2026-08-10) ───────────────────────────────────────────────────

// The binding a command answers to RIGHT NOW, in display form ("⌘⇧O" on a Mac, "Ctrl+Shift+O"
// elsewhere); "" when unbound. Reads the live overrides store, so a rebind changes what the next
// hover says — a tooltip advertises the user's configuration, never a stale default.
// It prints a command's chord whatever else is saved on that chord: the chat answers some keys itself (the jump moves, the
// nav trail) whichever command the shell's dispatcher would pick, so the tooltips beside those buttons keep naming the
// key the chat runs. Only the comment key's surfaces ask more, the menu line (render.ts commentKeyHint) and the palette's
// chip (palette-main.ts kbdChip), because that key's handler steps aside for a saved binding (review, 2026-09-30).
export function keyHint(id: string): string {
  const mac = typeof navigator !== "undefined" && /Mac|iP(hone|ad|od)/.test(navigator.platform || "");
  const ch = effectiveChord(id, DEFAULT_CHORDS[id], loadOverrides(), mac);
  return ch ? displayChord(ch, mac) : "";
}

// A control's tooltip with its command's current binding appended — "Open a session (⌘⇧O)" — so every
// shortcut is discoverable by hovering the button that does the same thing (the user 2026-08-10).
// The bare title when the command is unbound.
export function titleWithKey(base: string, id: string): string {
  const k = keyHint(id);
  return k ? (base ? base + " (" + k + ")" : k) : base;
}

// ── the dispatch decision (pure: the wiring calls this per keydown) ───────────────────────────────

// Modifier-less (or Shift-only) chords must never fire while typing — same rule the chat's bare-arrow
// handlers follow. Chords with a real modifier fire regardless of focus. A keydown inside an input method's
// composition (isComposing, or the legacy keyCode 229) is the composition's, never a chord — the type-to-focus
// handler's rule, here too (review, 2026-09-23).
export function dispatchable(e: { ctrlKey: boolean; altKey: boolean; metaKey: boolean; repeat?: boolean; isComposing?: boolean; keyCode?: number }, typing: boolean): boolean {
  if (e.repeat) return false;
  if (e.isComposing || e.keyCode === 229) return false;
  if (typing && !e.ctrlKey && !e.altKey && !e.metaKey) return false;
  return true;
}

// Whether a keydown aimed at `target` is the code editor's rather than command `id`'s. The file viewer's edit mode is
// CodeMirror (editor-chunk.ts), whose default keymap toggles a comment on Mod-/, and the comment command's default is the
// same chord, chosen because code editors use it. Inside the editor that key stays the editor's: the chat's handler
// (render.ts) and the shell's dispatcher (palette-main.ts) both ask this and leave the key alone, so the editor's toggle
// comment runs (in a language with no comments, such as JSON or plain text, it does nothing, and in the editor's own
// search field it does nothing either) and no notice or comment box opens (the review of #39). Both halves are needed: in
// the chat document one runs after the other. Only the comment command, and only on that chord. The older defaults that
// sit on editor keys (Mod+Shift+\ everywhere, and off a Mac Ctrl+M and Ctrl+Alt+↑/↓) took them from the editor before
// the comment key moved, the chat's own handlers take Ctrl+M and Ctrl+Alt+↑/↓ before the dispatcher hears them, and the
// editor's keymap lives in its own lazily loaded chunk, not in the bundles that dispatch keys; a key a reader saves for
// another command is that command's, in the editor too.
export function editorKeepsKey(id: string, chord: string, target: unknown, mac: boolean): boolean {
  if (id !== "chat.commentSelection" || chord !== resolveChord("Mod+/", mac)) return false;
  const el = target as { closest?: (sel: string) => unknown } | null;
  return !!el && typeof el.closest === "function" && !!el.closest(".cm-editor");
}
