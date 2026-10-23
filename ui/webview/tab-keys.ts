// Per-tab hot keys (the user 2026-09-10): a key combination that switches to a session, set from the tab's
// right-click menu ("Hot key…", then press the combination), shown minified on the tab after its name and
// context gauge, and a pair of commands that cycle the focus between chat columns when the chat is split.
//
// A tab's hot key is a COMMAND like any other: `session.hotkey.<sid>`, registered in the shell's command
// registry (palette-main.ts) with no default chord, so the recorded chord lives in the same overrides store
// as every other binding (keybindings.ts, `romp:keys`), the one dispatcher fires it from every pane, the
// conflict check names collisions, and the shortcuts dialog lists it as "Switch to <name>", rebindable and
// resettable. What this store adds is only the SET of sessions that have such a command, with their display
// names, so the shell can register them at boot before any pane has loaded (`romp:tabkeys`, per browser
// like the bindings themselves). Pure and DOM-free so node executes the rules; storage is passed in.
import { effectiveChord, type Bindings } from "./keybindings";
import { hostOf } from "./host-prefix";

export const TABKEYS_KEY = "romp:tabkeys";
export const HOTKEY_PREFIX = "session.hotkey.";

export type TabKeys = Record<string, string>;   // sid → display name (the title the dialog shows)

export function hotkeyCommandId(sid: string): string { return HOTKEY_PREFIX + sid; }
export function sidOfHotkeyCommand(id: string): string | null {
  return id.startsWith(HOTKEY_PREFIX) ? id.slice(HOTKEY_PREFIX.length) : null;
}

type StorageLike = { getItem(k: string): string | null; setItem(k: string, v: string): void };

export function loadTabKeys(storage: StorageLike): TabKeys {
  try {
    const d = JSON.parse(storage.getItem(TABKEYS_KEY) || "{}");
    if (!d || typeof d !== "object") return {};
    const out: TabKeys = {};
    for (const k of Object.keys(d)) if (typeof d[k] === "string") out[k] = d[k];
    return out;
  } catch (e) { return {}; }
}

/** A session joins the set (or its name is refreshed); returns the set as stored. */
export function rememberTabKey(storage: StorageLike, sid: string, name: string): TabKeys {
  const all = loadTabKeys(storage);
  all[sid] = (name || "").trim() || sid.slice(0, 8);
  try { storage.setItem(TABKEYS_KEY, JSON.stringify(all)); } catch (e) { /* storage full/blocked */ }
  return all;
}

export function forgetTabKey(storage: StorageLike, sid: string): TabKeys {
  const all = loadTabKeys(storage);
  delete all[sid];
  try { storage.setItem(TABKEYS_KEY, JSON.stringify(all)); } catch (e) { /* storage full/blocked */ }
  return all;
}

// ── keeping the set honest (review, 2026-09-10: it only ever grew) ─────────────────────────────────
// A session is in the set exactly while it has a chord bound AND its tab exists; its title follows the tab's
// name. The shell prunes the first (it hears every bindings write), the chat pane the other two (it knows the
// strip); both read the rules from here so node executes them.

/** Sessions in the set with no chord bound: a cancelled first recording, a Backspace, a menu's Remove. */
export function unboundTabKeys(set: TabKeys, overrides: Bindings, mac: boolean): string[] {
  return Object.keys(set).filter((sid) => !tabChord(sid, overrides, mac));
}

/** A set-like reader: the hosts-seen and reachable sets only ever answer `has`. */
export interface HasHost { has(host: string): boolean }

/** Sessions in the set whose tab the strip no longer carries — none while the strip is empty (a kernel not
 *  yet heard from is not a strip with nothing on it), and none whose HOST cannot speak for it (review,
 *  2026-10-23). A session is judged only when its host's own strip has landed on this page (`hostsSeen`,
 *  tab-order.ts stripHost: "" is the local kernel) and, for a linked machine's session (`host:uuid`), that
 *  host is reachable now (`reachable`, tab-groups.ts reachableFrom: attached, up, its tab list in this pane).
 *  The local kernel's strip lands first on a fresh page and says nothing about a linked machine's sessions;
 *  a host down at load never reports; a detached host's sessions leave the strip with it; a linked
 *  machine's strip that lands first says nothing about the local ones. None of these is a session's end,
 *  and judged, each erased the hot key for good: on every reload, for a linked machine's tab. The pin prune
 *  (prunePinned) and the column's emptiness check (columnEmptiness) keep the same host rule. */
export function goneTabKeys(set: TabKeys, order: readonly string[], hostsSeen: HasHost, reachable: HasHost): string[] {
  if (!order.length) return [];
  const judged = (sid: string) => { const h = hostOf(sid); return hostsSeen.has(h) && (h === "" || reachable.has(h)); };
  return Object.keys(set).filter((sid) => judged(sid) && !order.includes(sid));
}

/** Sessions in the set whose stored title trails the tab's current name: [sid, name] pairs to re-remember. */
export function renamedTabKeys(set: TabKeys, nameOf: (sid: string) => string | undefined): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  for (const sid of Object.keys(set)) {
    const n = (nameOf(sid) || "").trim();
    if (n && n !== set[sid]) out.push([sid, n]);
  }
  return out;
}

/** The chord a tab answers to right now (resolved for the platform), or "" when none is bound. */
export function tabChord(sid: string, overrides: Bindings, mac: boolean): string {
  return effectiveChord(hotkeyCommandId(sid), undefined, overrides, mac);
}

