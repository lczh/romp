// THE TAG MENU (the user 2026-10-01, who wanted to right-click a tag and get a menu to rename it, delete it and so
// on). One menu for every place a tag is drawn and can take a right-click: the tab strip's group headers (where the
// user most likely reaches for it), the tag chips of the tab menu's Tags flyout, the strip's filter chips, and the
// phone picker's group headings (kernel.py _CHAT_MOBILE_JS forwards a long press to the strip's hidden header). It is
// built on the shared context menu (ctx-menu.ts: one card, one dismissal, one keyboard) in the Sessions pane menu's
// dress: a label and its sub-line, no icons, the destructive row in the error colour.
//
// The rows (tagMenuSections, pure, so the model executes in node tests):
//   - the tag's own chip at the top, so the menu names the tag it acts on (the flyout lists several);
//   - Rename…, then the identity palette's swatches (the tab menu's colour row), the current colour ringed;
//   - Fold group / Open group on a group header (the strip's fold, as the header's own click does), then Show overview
//     (the section at a glance in the pane, which a header's click no longer opens since 2026-10-02) or, while that
//     tag's overview shows, Close overview; Show only this tag (the chat filter set to it) unless the filter already
//     is exactly that; Configure tags…;
//   - Delete tag…, which asks first and says how many sessions lose the tag (the sessions stay).
// Nothing here is a new concept: every row is an edit the Tags dialog makes, or a fold, a filter or a route the
// strip already has.
//
// A tag is its NAME (the name-keyed union, session-views.ts): a gesture on it reaches every store holding a tag of
// that name, as the Tags dialog's _editTagUnion does. planTagUnionEdit says what one gesture posts: a targeted op by
// the local tag's id (the kernel's tagEdit door), the editTag wire for each tag homed on an attached kernel (its home
// applies it), and the optimistic copy with the name carried through the order and the lenses (views-writes.ts
// carryTagNames; the kernel does the same in its write, `tagNameCarry`).
import type { SessionViews, TagUnion } from "./session-views";
import { viewTags } from "./session-views";
import { hostOf } from "./host-prefix";
import type { TagEditOp, LensFields } from "./views-writes";
import { applyTagEdit, carryTagNames } from "./views-writes";
import { menuCard, addMenuItem, addMenuSep, showMenuCard, closeContextMenu, CtxMenuOpts } from "./ctx-menu";
import { tagChip } from "./tag-menu";

/** The longest name the store keeps (the kernel's _VIEWS_MAX_NAME): the inputs stop there. */
export const TAG_NAME_MAX = 40;

export type TagMenuAct = "rename" | "fold" | "open" | "overview" | "closeOverview" | "only" | "configure" | "delete";
export interface TagMenuRow { act: TagMenuAct; label: string; sub?: string; danger?: boolean }
/** What the menu is opened on. `folded`: the group header's RENDERED fold, null off a header (a chip has no fold).
 *  `only`: whether the surface's filter is already exactly this tag, null where the surface has no filter to set.
 *  `configure`: the surface has the route to the Tags dialog. `overview`: whether the pane is showing this tag's
 *  overview (the section at a glance), null or absent where none can show (off a header, on the phone). */
export interface TagMenuState { name: string; members: number; folded: boolean | null; only: boolean | null; configure: boolean; overview?: boolean | null }

const sessions = (n: number) => `${n} session${n === 1 ? "" : "s"}`;

/** The menu's rows, in sections (a divider between two): what a tag is called and wears; how it shows; deleting it.
 *  The colour swatches ride the first section, after Rename… (openTagContextMenu draws them). */
export function tagMenuSections(s: TagMenuState): TagMenuRow[][] {
  const look: TagMenuRow[] = [{ act: "rename", label: "Rename…", sub: "its sessions keep it under the new name" }];
  const view: TagMenuRow[] = [];
  if (s.folded === true) view.push({ act: "open", label: "Open group", sub: "show its tabs on the strip" });
  else if (s.folded === false) view.push({ act: "fold", label: "Fold group", sub: "the header stands in for its tabs" });
  // the overview's one door from the strip (a header's click only folds, the user 2026-10-02), named as its heading is
  // ("Overview of <tag>"), and its way back while it shows
  if (s.overview === false) view.push({ act: "overview", label: "Show overview", sub: "what each of its sessions is doing" });
  else if (s.overview === true) view.push({ act: "closeOverview", label: "Close overview", sub: "back to the session you were reading" });
  if (s.only === false) view.push({ act: "only", label: "Show only this tag", sub: `filter the tabs to its ${sessions(s.members)}` });
  if (s.configure) view.push({ act: "configure", label: "Configure tags…" });
  const del: TagMenuRow[] = [{ act: "delete", label: "Delete tag…", danger: true,
    sub: s.members ? `${sessions(s.members)} ${s.members === 1 ? "loses" : "lose"} it; the sessions stay` : "no session holds it" }];
  return [look, view, del].filter((x) => x.length > 0);
}

/** The delete's question, in the confirm box's words: what goes (the tag), what stays (the sessions). */
export function deleteTagWords(name: string, members: number): { title: string; detail: string; confirm: string } {
  return {
    title: `Delete the tag “${name}”?`,
    detail: members
      ? `${sessions(members)} ${members === 1 ? "loses" : "lose"} this tag. The sessions stay, with any other tags they have.`
      : "No session holds it.",
    confirm: "Delete tag",
  };
}

/** What a typed new name means: the name to post, nothing to do (empty, or the name it already has), or why it
 *  cannot be (another tag already has it: two tags of one name would make one group, and the kernel refuses a twin
 *  of its own tags). Clamped and stripped the way the store keeps names. */
export function tagRenameVerdict(unions: readonly TagUnion[], from: string, raw: string): { name: string } | { cancel: true } | { error: string } {
  const name = raw.slice(0, TAG_NAME_MAX).trim();
  if (!name || name === from) return { cancel: true };
  if (unions.some((u) => u.name === name)) return { error: `a tag named “${name}” already exists` };
  return { name };
}

export type TagUnionEdit = { rename: string } | { color: string } | { delete: true };
/** One editTag message's `edit`: the change for a tag homed on an attached kernel, addressed by host and name. */
export interface RemoteTagEdit { host: string; name: string; rename?: string; color?: string; delete?: boolean }
/** What one gesture on a tag posts. `nv`: the optimistic copy with the whole union edited (the remote halves
 *  mirrored for show; their homes' next push is the truth). `ops`: the targeted op for the local tag, if there is
 *  one. `remote`: one editTag per tag of the name homed elsewhere. `lens`: the order and the filters as the copy
 *  carries them, when the gesture changed them — what a page on a kernel without `tagNameCarry` writes itself. */
export interface TagUnionPlan { nv: SessionViews; ops: TagEditOp[]; remote: RemoteTagEdit[]; lens: LensFields | null }

export function planTagUnionEdit(views: SessionViews | null | undefined, g: TagUnion, edit: TagUnionEdit): TagUnionPlan {
  let nv = JSON.parse(JSON.stringify(views || { active: "all", tags: [] })) as SessionViews;
  const names = JSON.stringify([nv.tagOrder || null, nv.actives || null]);
  const remote: RemoteTagEdit[] = [];
  // the remote halves first, so the local op's carry (applyTagEdit) finds no other tag still bearing the old name
  for (const rt of g.remotes) {
    const host = rt.host || hostOf(rt.id);
    if ("rename" in edit) remote.push({ host, name: g.name, rename: edit.rename });
    else if ("color" in edit) remote.push({ host, name: g.name, color: edit.color });
    else remote.push({ host, name: g.name, delete: true });
    const list = nv.remoteTags || [];
    const mine = list.find((x) => x.id === rt.id);
    if (!mine) continue;
    if ("rename" in edit) mine.name = edit.rename;
    else if ("color" in edit) mine.color = edit.color;
    else nv.remoteTags = list.filter((x) => x.id !== rt.id);
  }
  const ops: TagEditOp[] = [];
  if (g.localId && !g.pending) {
    const tid = g.localId;
    ops.push("rename" in edit ? { op: "rename", tid, newName: edit.rename }
      : "color" in edit ? { op: "recolor", tid, color: edit.color } : { op: "delete", tid });
    nv = applyTagEdit(nv, ops[0]);
  } else if (!("color" in edit)) {
    // a union only remote tags make: no local op carries the names, so the copy carries them here
    const still = viewTags(nv).some((t) => (t.name || "tag") === g.name) || (nv.remoteTags || []).some((t) => (t.name || "tag") === g.name);
    carryTagNames(nv, g.name, "rename" in edit ? edit.rename : null, still);
  }
  const changed = JSON.stringify([nv.tagOrder || null, nv.actives || null]) !== names;
  const lens: LensFields | null = changed ? { tagOrder: (nv.tagOrder || []).slice(), ...(nv.actives ? { actives: JSON.parse(JSON.stringify(nv.actives)) } : {}) } : null;
  return { nv, ops, remote, lens };
}

/** What a page hands the menu: the action for each row, and the recolor the swatches post. */
export interface TagMenuHandlers { pick: (act: TagMenuAct) => void; recolor: (color: string) => void }

/** Open the tag menu at (`x`, `y`): the tag's chip, the rows, the palette's swatches after Rename… (omitted until the
 *  palette has loaded, as on the tab menu). Every pick resolves the tag by NAME when it runs: the menu outlives the
 *  pushes that rebuild the header it was opened from (the click-safety rule). */
export function openTagContextMenu(x: number, y: number, s: TagMenuState, color: string, palette: readonly string[],
                                   h: TagMenuHandlers, opts: CtxMenuOpts = {}): HTMLElement {
  const menu = menuCard({ ...opts, className: "ctx-tagmenu" + (opts.className ? " " + opts.className : "") });
  menu.setAttribute("aria-label", `Tag ${s.name}`);
  const head = document.createElement("div");
  head.className = "ctx-tagmenu-head";
  head.setAttribute("aria-hidden", "true");   // the card's own label names the tag; the chip is the eye's
  head.appendChild(tagChip(s.name, color || null));
  menu.appendChild(head);
  tagMenuSections(s).forEach((rows, i) => {
    if (i > 0) addMenuSep(menu);
    for (const r of rows) {
      addMenuItem(menu, { label: r.label, sub: r.sub, danger: r.danger, className: "ctx-tag-" + r.act, pick: () => h.pick(r.act) });
      if (r.act === "rename" && palette.length) menu.appendChild(swatches(palette, color, h.recolor));
    }
  });
  return showMenuCard(menu, x, y, opts);
}

/** The identity palette as the tab menu's swatch row (`.ctx-colors`, balanced rows of at most six), the current one
 *  ringed; a pick closes the menu and recolors. */
function swatches(palette: readonly string[], current: string, recolor: (c: string) => void): HTMLElement {
  const row = document.createElement("div");
  row.className = "ctx-colors";
  const rows = Math.ceil(palette.length / 6);
  row.style.gridTemplateColumns = "repeat(" + Math.ceil(palette.length / rows) + ", 18px)";
  const cur = (current || "").toLowerCase();
  for (const bg of palette) {
    const sw = document.createElement("button");
    sw.type = "button";
    sw.className = "ctx-swatch" + (bg.toLowerCase() === cur ? " sel" : "");
    sw.style.background = bg;
    sw.title = bg;
    sw.setAttribute("aria-label", "Set the tag's colour to " + bg);
    sw.addEventListener("click", (ev) => { ev.stopPropagation(); closeContextMenu(); recolor(bg); });
    row.appendChild(sw);
  }
  return row;
}

/** The rename where no header can take the input (the phone, whose strip is hidden; a chip in a flyout or a filter):
 *  the confirm box's chrome around one name field, the move and fork prompts' shape. Enter or Rename commits through
 *  `verdict` (an error keeps the box open with the reason under the field), Escape, Cancel and the backdrop close it.
 *  Returns the overlay. */
export function openTagRenamePrompt(name: string, verdict: (raw: string) => { name: string } | { cancel: true } | { error: string },
                                    commit: (to: string) => void): HTMLElement {
  document.getElementById("tag-rename-prompt")?.remove();
  const overlay = document.createElement("div"); overlay.className = "picker-overlay confirm-overlay"; overlay.id = "tag-rename-prompt";
  const box = document.createElement("div"); box.className = "picker-box confirm-box";
  const title = document.createElement("div"); title.className = "confirm-title"; title.textContent = `Rename the tag “${name}”`;
  const detail = document.createElement("div"); detail.className = "confirm-detail"; detail.textContent = "Its sessions keep it under the new name.";
  const input = document.createElement("input");
  input.type = "text"; input.className = "fork-name tag-rename-name"; input.value = name; input.maxLength = TAG_NAME_MAX;
  input.setAttribute("aria-label", "New name for the tag");
  input.setAttribute("autocapitalize", "off"); input.setAttribute("autocomplete", "off"); input.setAttribute("spellcheck", "false");
  const hint = document.createElement("div"); hint.className = "move-dir-hint tag-rename-hint";
  const actions = document.createElement("div"); actions.className = "confirm-actions";
  const cancel = document.createElement("button"); cancel.type = "button"; cancel.className = "picker-action confirm-btn"; cancel.textContent = "Cancel";
  const go = document.createElement("button"); go.type = "button"; go.className = "picker-action confirm-btn"; go.textContent = "Rename";
  const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); } };
  const close = () => { overlay.remove(); document.removeEventListener("keydown", onKey, true); };
  const submit = () => {
    const v = verdict(input.value);
    if ("error" in v) { hint.textContent = v.error; hint.className = "move-dir-hint tag-rename-hint bad"; input.classList.add("bad"); input.focus(); return; }
    close();
    if ("name" in v) commit(v.name);
  };
  input.addEventListener("keydown", (e) => { e.stopPropagation(); if (e.key === "Enter") { e.preventDefault(); submit(); } else if (e.key === "Escape") { e.preventDefault(); close(); } });
  input.addEventListener("input", () => { input.classList.remove("bad"); hint.textContent = ""; hint.className = "move-dir-hint tag-rename-hint"; });
  cancel.addEventListener("click", close);
  go.addEventListener("click", submit);
  overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
  actions.append(cancel, go);
  box.append(title, detail, input, hint, actions);
  overlay.appendChild(box);
  document.body.appendChild(overlay);
  document.addEventListener("keydown", onKey, true);
  input.focus(); input.select();
  return overlay;
}
