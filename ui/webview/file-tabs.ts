// FILE TABS (the user 2026-09-30, who liked the file viewer and asked for a way to keep several files open as tabs of
// their own, beside the sessions, to flip between them and the sessions): the viewer's pop-out makes the file a tab in
// the chat strip. This module is the model, pure and DOM-free (tab-groups.ts's pattern), so the rules run in node tests;
// render.ts paints them and file-view.ts shows the file.
//
// A file tab is not a session and never enters the session order. It sits BESIDE a session: `anchor` names the session
// tab it follows (or, with `lead`, the one it precedes, for a tab dropped at the head of a group), so it appears in that
// session's tag group, after every copy of that session the strip shows, and moves with the session when the session is
// dragged. Among the tabs that share an anchor and a side, the store's list order is the strip's order. A tab whose
// anchor has left the strip falls back to the session it was opened from, and one with neither trails the strip (never
// dropped: a closed session's file is still a file).
//
// ONE arrangement on every device, like the tab order (view-order.ts) and the folds (tab-groups.ts): the kernel keeps the
// list beside them as opaque data (kernel.py view-files.json, the viewOrder frame's `files` half, the `viewFiles` cap),
// this browser caches the kernel's copy under romp:filetabs:shared, and a change is published once the page has heard the
// kernel's copy on the current connection (a change made before that waits as a pending delta and is merged over the
// kernel's when it arrives: the folds' protocol, and for the same reason). A separate store rather than new entries in
// the tab order, so an older page, which knows nothing of file tabs, never prunes or reorders them, and an older kernel,
// which sends no `files` half, leaves them this browser's own.
import { hostOf } from "./host-prefix";

export const FILETABS_SHARED_KEY = "romp:filetabs:shared";
/** The file tabs this browser held before its first change made while no page had heard the kernel's copy: the BASE of
 *  that change, merged over the kernel's copy on the first hearing (mergeFileTabs). Absent when nothing is pending. */
export const FILETABS_PENDING_KEY = "romp:filetabs:pending";
export const FILETABS_EVENT = "romp-filetabs";
/** A backstop, not a working limit: a strip with a hundred files open is already past use, and the kernel's store has a
 *  byte cap of its own. An open past it is refused out loud by the caller (the toast), never a silent drop. */
export const FILETABS_CAP = 100;
/** Every file tab id wears this prefix and no colon: host-prefix.ts reads a colon as a session's host, and a file tab
 *  is nobody's session. */
export const FILE_TAB_PREFIX = "ftab-";

export interface FileTab {
  /** fileTabId(host, path, sid): the same file on the same machine is one tab, whichever session opened it (a relative
   *  path is the same file only from the same session) */
  id: string;
  /** the path as the session knows it, on its own machine */
  path: string;
  /** the session it was opened from (host-prefixed for a remote one): the file is fetched through that session's kernel,
   *  and the tab's tint and tooltip name it; null when the opener named none (the local kernel's file) */
  sid: string | null;
  /** the session tab this one sits beside */
  anchor: string;
  /** true: before the anchor (dropped at the head of a group); absent: after it */
  lead?: boolean;
}
export interface FileTabsState { v: 1; tabs: FileTab[] }

export const emptyFileTabs = (): FileTabsState => ({ v: 1, tabs: [] });

export function isFileTabId(id: string | null | undefined): boolean {
  return typeof id === "string" && id.startsWith(FILE_TAB_PREFIX);
}

/** The machine a file lives on, as the strip names hosts: the opener's host prefix, "" for the local kernel. */
export function fileHost(sid: string | null | undefined): string {
  return sid ? hostOf(sid) : "";
}

// FNV-1a, twice with different offsets, as base 36: stable across devices and pages (every device mints the same id for
// the same file, which is what makes the dedupe hold after a sync), and colon-free.
function fnv(s: string, h: number): number {
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
  return h >>> 0;
}
/** A relative path names a file only together with the session it was written in (the kernel resolves it against that
 *  session's folder), so its identity carries the session; an absolute or home-anchored one is the machine's. */
export function fileTabId(host: string, path: string, sid?: string | null): string {
  const relative = !path.startsWith("/") && !path.startsWith("~");
  const key = host + "\u0000" + (relative && sid ? sid + "\u0000" : "") + path;
  return FILE_TAB_PREFIX + fnv(key, 2166136261).toString(36) + fnv(key, 0x811c9dc5 ^ 0x5bd1e995).toString(36);
}

/** A stored blob (an object, or its JSON). Anything malformed reads as no file tabs, and a malformed entry is dropped
 *  alone: a corrupt entry may cost that tab, never the strip. Duplicate ids keep the first. */
export function parseFileTabs(raw: unknown): FileTabsState {
  let o: unknown = raw;
  if (typeof raw === "string") { try { o = JSON.parse(raw); } catch { return emptyFileTabs(); } }
  if (!o || typeof o !== "object" || Array.isArray(o)) return emptyFileTabs();
  const list = (o as { tabs?: unknown }).tabs;
  if (!Array.isArray(list)) return emptyFileTabs();
  const out: FileTab[] = [];
  const seen = new Set<string>();
  for (const x of list) {
    if (!x || typeof x !== "object") continue;
    const t = x as Record<string, unknown>;
    if (typeof t.id !== "string" || !isFileTabId(t.id) || seen.has(t.id)) continue;
    if (typeof t.path !== "string" || !t.path || typeof t.anchor !== "string") continue;
    const sid = typeof t.sid === "string" && t.sid ? t.sid : null;
    const tab: FileTab = { id: t.id, path: t.path, sid, anchor: t.anchor };
    if (t.lead === true) tab.lead = true;
    seen.add(t.id);
    out.push(tab);
    if (out.length >= FILETABS_CAP) break;
  }
  return { v: 1, tabs: out };
}

/** Open `path` (from session `sid`) as a tab beside `anchor`. The same file on the same machine already open is that tab:
 *  the state comes back unchanged with its id and `added` false. A new one goes after every tab already beside the
 *  anchor (the end of that run), so a second file opened from a session lands after the first. `full`: the cap refused
 *  it, and the caller says so. */
export function addFileTab(st: FileTabsState, path: string, sid: string | null, anchor: string): { st: FileTabsState; id: string; added: boolean; full: boolean } {
  const id = fileTabId(fileHost(sid), path, sid);
  if (st.tabs.some((t) => t.id === id)) return { st, id, added: false, full: false };
  if (st.tabs.length >= FILETABS_CAP) return { st, id, added: false, full: true };
  const tab: FileTab = { id, path, sid: sid || null, anchor };
  return { st: { v: 1, tabs: [...st.tabs, tab] }, id, added: true, full: false };
}

export function removeFileTab(st: FileTabsState, id: string): FileTabsState {
  return st.tabs.some((t) => t.id === id) ? { v: 1, tabs: st.tabs.filter((t) => t.id !== id) } : st;
}

/** A drag's landing: the tab now sits beside `anchor` (before it when `lead`), just ahead of `before` when that is a tab
 *  on the same side of the same anchor, else at the end of that run. Returns the state unchanged for an unknown id. */
export function moveFileTab(st: FileTabsState, id: string, anchor: string, lead: boolean, before: string | null): FileTabsState {
  const cur = st.tabs.find((t) => t.id === id);
  if (!cur) return st;
  const moved: FileTab = { id: cur.id, path: cur.path, sid: cur.sid, anchor };
  if (lead) moved.lead = true;
  const rest = st.tabs.filter((t) => t.id !== id);
  const same = (t: FileTab) => t.anchor === anchor && !!t.lead === lead;
  let at = before ? rest.findIndex((t) => t.id === before && same(t)) : -1;
  if (at < 0) {
    let last = -1;
    rest.forEach((t, i) => { if (same(t)) last = i; });
    at = last >= 0 ? last + 1 : rest.length;
  }
  return { v: 1, tabs: [...rest.slice(0, at), moved, ...rest.slice(at)] };
}

/** The session a tab sits beside NOW: its anchor while the strip lists it, else the session it was opened from while
 *  the strip lists that, else null (it trails the strip). `listed` is the strip's membership (render.ts stripLists). */
export function anchorOf(t: FileTab, listed: (sid: string) => boolean): { anchor: string; lead: boolean } | null {
  if (listed(t.anchor)) return { anchor: t.anchor, lead: !!t.lead };
  if (t.sid && t.sid !== t.anchor && listed(t.sid)) return { anchor: t.sid, lead: false };
  return null;
}

/** The tabs beside each session, by side, in store order, and the ones that trail the strip (no listed anchor). */
export function besides(tabs: readonly FileTab[], listed: (sid: string) => boolean): { lead: Map<string, FileTab[]>; after: Map<string, FileTab[]>; trail: FileTab[] } {
  const lead = new Map<string, FileTab[]>(), after = new Map<string, FileTab[]>(), trail: FileTab[] = [];
  for (const t of tabs) {
    const a = anchorOf(t, listed);
    if (!a) { trail.push(t); continue; }
    const m = a.lead ? lead : after;
    const run = m.get(a.anchor);
    if (run) run.push(t); else m.set(a.anchor, [t]);
  }
  return { lead, after, trail };
}

/** Weave the file tabs into a strip plan's items: a session item gets its leading tabs before it and its trailing ones
 *  after it, at EVERY copy (a session under two tags shows its files in both groups, as it shows itself in both). A
 *  session the plan does not show (folded away, hidden by a view, in another column) shows none of its files either.
 *  `idOf` reads a plan item's session id (null for a header). The trailing tabs are returned apart: where they go is
 *  the caller's (render.ts puts them after the last group, in the first column only). */
export function weaveFileTabs<T>(items: readonly T[], idOf: (it: T) => string | null, tabs: readonly FileTab[],
                                 listed: (sid: string) => boolean): { items: Array<T | { file: string }>; trail: FileTab[] } {
  const by = besides(tabs, listed);
  const out: Array<T | { file: string }> = [];
  for (const it of items) {
    const id = idOf(it);
    if (id !== null) for (const t of by.lead.get(id) || []) out.push({ file: t.id });
    out.push(it);
    if (id !== null) for (const t of by.after.get(id) || []) out.push({ file: t.id });
  }
  return { items: out, trail: by.trail };
}

/** The keyboard's walk (←/→, the session pair, Ctrl+Alt+←/→): the sessions it walks, in its order, with each one's file
 *  tabs beside it, and the trailing tabs last when `withTrail`. Pure over the lists render.ts reads, so the walk is the
 *  strip's by construction. */
export function navWithFiles(sessionIds: readonly string[], tabs: readonly FileTab[], listed: (sid: string) => boolean, withTrail: boolean): string[] {
  const woven = weaveFileTabs(sessionIds, (id) => id, tabs, listed);
  const out = woven.items.map((it) => (typeof it === "string" ? it : it.file));
  if (withTrail) for (const t of woven.trail) out.push(t.id);
  return out;
}

/** The name a tab shows: the basename, and, when another open tab shows the same basename, as much of the parent path as
 *  tells them apart (`hint`: "a", "src/a"), the way editors label two open index.ts. A tab on another machine is told
 *  apart by its host prefix already, so only same-host tabs are compared. */
export function fileTabLabels(tabs: readonly FileTab[]): Map<string, { base: string; hint: string }> {
  const parts = (p: string) => p.split("/").filter((x) => x !== "");
  const baseOf = (p: string) => { const q = parts(p); return q.length ? q[q.length - 1] : p; };
  const out = new Map<string, { base: string; hint: string }>();
  const groups = new Map<string, FileTab[]>();
  for (const t of tabs) {
    const k = fileHost(t.sid) + "\u0000" + baseOf(t.path);
    const g = groups.get(k);
    if (g) g.push(t); else groups.set(k, [t]);
  }
  for (const g of groups.values()) {
    for (const t of g) {
      const base = baseOf(t.path);
      if (g.length < 2) { out.set(t.id, { base, hint: "" }); continue; }
      const dirs = parts(t.path).slice(0, -1);
      const others = g.filter((x) => x !== t).map((x) => parts(x.path).slice(0, -1));
      let hint = dirs.join("/");
      for (let k = 1; k <= dirs.length; k++) {
        const tail = dirs.slice(-k).join("/");
        if (others.every((o) => o.slice(-k).join("/") !== tail)) { hint = tail; break; }
      }
      out.set(t.id, { base, hint: hint || (t.path.startsWith("~") ? "~" : "/") });
    }
  }
  return out;
}

/** Where a dragged file tab lands, from the strip's DOM around its dropped node: `prev` the nearest session tab before it
 *  in its group, `next` the nearest after it, `nextFile` the file tab right after it (if one is). Beside the session
 *  before it when there is one (after it), else before the session after it (the head of a group); `before` keeps its
 *  place among that session's other files. Null when no session is in reach (nothing to sit beside). */
export function fileDropPlace(prev: string | null, next: string | null, nextFile: string | null): { anchor: string; lead: boolean; before: string | null } | null {
  if (prev) return { anchor: prev, lead: false, before: nextFile };
  if (next) return { anchor: next, lead: true, before: nextFile };
  return null;
}

// ── the store, and its sync with the kernel ─────────────────────────────────────────────────────────────────────────

function getKey(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }   // blocked storage reads as absent
}

/** The file tabs as every surface reads them: the kernel's copy as this browser caches it. Re-read per use, never cached:
 *  another pane of this page writes the same key. */
export function readFileTabs(): FileTabsState {
  return parseFileTabs(getKey(FILETABS_SHARED_KEY));
}

/** How a change reaches the kernel: federation.ts installs `window.__rompPublishViewFiles` once the kernel's own copy has
 *  reached the page on the current connection (the viewOrder frame's `files` half), and withdraws it when that
 *  connection drops; a page with neither (a kernel from before file tabs were shared) keeps its tabs to this browser. */
export type FileTabsPublisher = (st: FileTabsState) => void;
let directPublisher: FileTabsPublisher | null = null;
export function setFileTabsPublisher(fn: FileTabsPublisher | null): void { directPublisher = fn; }
export function fileTabsPublisher(): FileTabsPublisher | null {
  try {
    const slot = (globalThis as any).window?.__rompPublishViewFiles;
    if (typeof slot === "function") return slot as FileTabsPublisher;
  } catch { /* no window */ }
  return directPublisher;
}

/** Cache `st` as the kernel's copy; true when that CHANGED what this browser held (absent counts as changed). */
function cache(st: FileTabsState): boolean {
  const text = JSON.stringify(st);
  if (getKey(FILETABS_SHARED_KEY) === text) return false;
  try { localStorage.setItem(FILETABS_SHARED_KEY, text); } catch { /* quota / private mode: the page still shows it */ }
  return true;
}

function announce(): void {
  try { window.dispatchEvent(new CustomEvent(FILETABS_EVENT)); } catch { /* no window (node test) */ }
}

/** Persist, publish and tell every pane (the folds' write, tab-groups.ts writeTabGroups): the cache, then the kernel when
 *  the page may speak for the list, else the pending base; `storage` reaches the other panes, the CustomEvent this one,
 *  the kernel's push the other devices. An unchanged write does nothing at all. */
export function writeFileTabs(st: FileTabsState): void {
  const prev = readFileTabs();
  if (!cache(st)) return;
  const publish = fileTabsPublisher();
  if (publish) { try { publish(st); } catch { /* a dead socket: the cache still holds it */ } }
  else if (getKey(FILETABS_PENDING_KEY) === null) {
    try { localStorage.setItem(FILETABS_PENDING_KEY, JSON.stringify(prev)); } catch { /* the change stays this browser's */ }
  }
  announce();
}

/** The three-way merge a pending change lands by: the kernel's list (`served`) with this browser's changes since `base`
 *  applied over it — a tab opened here is added, a tab closed here is removed, a tab moved here takes its new place —
 *  and everything else as the kernel has it, so another device's changes in the meantime stand. A tab changed here is
 *  put back after the nearest tab before it in `local` that the result holds, else at the front. */
export function mergeFileTabs(base: FileTabsState, local: FileTabsState, served: FileTabsState): FileTabsState {
  const b = new Map(base.tabs.map((t) => [t.id, JSON.stringify(t)] as const));
  const l = new Map(local.tabs.map((t) => [t.id, JSON.stringify(t)] as const));
  const changed = new Set<string>();
  for (const t of local.tabs) if (b.get(t.id) !== l.get(t.id)) changed.add(t.id);   // added here, or moved here
  const removed = new Set(base.tabs.filter((t) => !l.has(t.id)).map((t) => t.id));
  const out = served.tabs.filter((t) => !removed.has(t.id) && !changed.has(t.id));
  local.tabs.forEach((t, i) => {
    if (!changed.has(t.id)) return;
    let at = 0;
    for (let j = i - 1; j >= 0; j--) { const k = out.findIndex((x) => x.id === local.tabs[j].id); if (k >= 0) { at = k + 1; break; } }
    out.splice(at, 0, t);
  });
  return parseFileTabs({ v: 1, tabs: out });
}

/** A page HEARS the kernel's file tabs: the viewOrder frame's `files` half, on the connect push and on every change.
 *  `served` is an object when the kernel keeps a list and null when it keeps NONE (not an emptied list). In order: a
 *  change this browser made before any of its pages had heard lands over the kernel's list (mergeFileTabs) and is
 *  published; else a kernel with none gets this browser's tabs (so nobody's open files are lost in the move); else the
 *  kernel's list is adopted. Then `install` hands the page its publisher. */
export function hearSharedFileTabs(served: unknown, publish: FileTabsPublisher, install: (fn: FileTabsPublisher) => void): void {
  const local = readFileTabs();
  const pending = getKey(FILETABS_PENDING_KEY);
  if (pending !== null) { try { localStorage.removeItem(FILETABS_PENDING_KEY); } catch { /* read once */ } }
  const isObj = !!served && typeof served === "object" && !Array.isArray(served);
  const push = (st: FileTabsState) => { const changed = cache(st); try { publish(st); } catch { /* the next frame asks again */ } if (changed) announce(); };
  if (pending !== null && isObj) push(mergeFileTabs(parseFileTabs(pending), local, parseFileTabs(served)));
  else if (!isObj && served === null && local.tabs.length) push(local);
  else if (isObj && cache(parseFileTabs(served))) announce();
  install(publish);
}
