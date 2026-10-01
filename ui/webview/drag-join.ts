// DRAGGING A TAB INTO (and OUT OF) A TAG GROUP (the user 2026-09-23, who wanted a tab dragged onto a
// group to put that session IN the group rather than only reorder it, and the ungrouped row to take it
// back out; the user 2026-09-30, who dragged a tab into another group expecting it to take that tag and
// lose all its others, and found it had only gained the one). The strip's sections ARE tags
// (tab-groups.ts), so the gesture is a tag edit, and every half writes through the kernel's targeted
// `tagEdit` op by the tag's stored id, or the `editTag` wire for a name homed on another kernel, so one
// writer owns tags in every direction. This module is the DECISION: given the strip's unions, where a
// drop landed, where the dragged copy came from and whether the copy modifier is held, what the drop
// does to that session's tags, and the ops that carry it. Pure and DOM-free (the tab-order.ts /
// dragslot.ts pattern), so the rule executes in node (drag-join.test.ts); render.ts's #tabs
// dragover/drop handlers paint and post it, and the tab menu's "Move to <tag>" rows post the same move
// (moveToTag) through the same writer.
//
// THE RULE, in the user's terms: the place a tab is dropped states WHERE THEY WANT IT TO APPEAR NOW, and
// only there.
//   - a group IS a tag, so a drop into group X MOVES the session there: its tags become exactly [X].
//     That holds for a copy of a multi-tagged session too — its copy dragged from group A to group B
//     leaves it tagged B alone, so every other copy leaves the strip.
//   - holding Option (Alt elsewhere) while dropping is the copy-drag of every desktop's file manager: it
//     ADDS X and keeps the rest, so a session with several tags shows under each one. The cue carries a
//     small "+" while it is held.
//   - the ungrouped row is NOT a group. It is the set of sessions carrying no tags at all, so "appear
//     here" is satisfied by CLEARING EVERY TAG the session has (with or without the modifier: there is no
//     tag to add).
//   - a drop inside the group the dragged copy was already sitting in is a reorder, and writes nothing.
// The cost is reversibility: dragging a moved tab back restores the tag it was dragged into, not the ones
// the move took off. There is no undo stack and no timed undo affordance; the pre-release cue, which names
// every tag about to come off (leaveWords), is the safety.
//
// THE WRITE IS ONE OP on a kernel that has the `tagSet` cap: `setTags` sets the session's local tags to
// the list the drop leaves (`keep`), so a second device never reads a half-moved session. An older
// kernel gets the fewest older ops that reach the same tags (dropLocalOps): the one-to-one move is its
// atomic `move`, and anything wider rides addMember / removeMember in turn, all on ONE optimistic blob
// so this page never shows the half-moved state. A tag homed on another kernel is that kernel's write
// (the `editTag` wire, dropRemoteEdits) and cannot join the local op: no write spans two kernels.
//
// WHERE A DROP LANDED is read from the PROVISIONAL TAB's own place in the strip — the nearest section
// header before it, or the untagged boundary it has passed (render.ts dropZoneOf) — never from the
// element under the pointer. The live reorder has already slid the dragged tab into a row by the time
// the drop lands, and that row is what the user is looking at, so the cue and the drop read one input
// and cannot disagree. It also makes the rule fall out for free in the cases that have no tab to
// hit-test: a folded group (its members are hidden, and the slot after its bare header is inside it)
// and a folded header PACKED onto the folded header before it (PR 1792: no row break between them, so
// the slot between the two heads is the end of the first group's row and lands in THAT group, the one
// the provisional tab is sitting in).
//
// THE UNGROUPED ROW IS THE ONE PLACE A TAB LEAVES ITS GROUPS. The slot ahead of the strip's FIRST header
// resolves to no section either, but it is not the ungrouped row — it is the head of the strip — so it
// clears nothing: `zone.trail` is the untagged boundary actually crossed, not merely "no header found",
// and a drop aimed a little left of the first group's chip can never strip a tag.
//
// A HEADER dragged onto another header still reorders the groups: that path is chosen by WHAT IS BEING
// DRAGGED (render.ts's dragover/drop branch on `draggedGroup`), never by where the pointer is, so a tab
// dropped on a header and a header dropped on a header are two different gestures on the same pixel.
import type { TagUnion } from "./session-views";
import type { TagEditOp } from "./views-writes";

/** Where the provisional tab sits when the drop lands: the section header's tag name, or the untagged
 *  trail (`trail`, past the boundary the strip draws for the sessions in no tag). Neither, with `trail`
 *  false, is the head of the strip or a flat (unsectioned) strip — a plain reorder. The same shape names
 *  where the dragged COPY came from (its `data-copy`: the group whose row it was drawn in). */
export interface DropZone { section: string | null; trail: boolean }

/** One tag the drop writes, and the routes that carry the write: `tid` is the LOCAL store's tag,
 *  addressed by its stored id (never its name — a name is what a refused rename would have changed,
 *  views-writes.ts TagEditOp), and `hosts` the kernels the `editTag` wire must carry it to. An add
 *  lands on the local tag when one holds the name and otherwise on the name's single remote home; a
 *  removal goes to EVERY store holding the (name, member) pair, which is the Tags flyout's rule. */
export interface TagRoute { tag: string; tid: string | null; hosts: string[] }

/** What a drop does to the dragged session's tags.
 *  - `move`: into a group — `add` is that group's tag (null when the session already carries it), `remove`
 *    every OTHER tag it carries, and `keep` the local tag ids it holds afterwards (the setTags list)
 *  - `join`: into a group with the copy modifier held — `add` alone, nothing removed
 *  - `leave`: onto the ungrouped row — `remove` is every tag it carries, `keep` empty
 *  - `reorder`: nothing (the reorder the strip has always done is the whole gesture), and `why` says which
 *    reorder-only case it is, for a diagnostic reader — never user-facing copy */
export interface TabDrop {
  kind: "move" | "join" | "leave" | "reorder";
  add: TagRoute | null;
  remove: TagRoute[];
  keep: string[];
  why: "" | "trail" | "head" | "untagged" | "unknown" | "already" | "same" | "pending";
}

/** How the drop was made: `from`, the zone the dragged copy was drawn in before the drag (a drop back
 *  into it is a reorder); `add`, the copy modifier (Option, or Alt) held at the release. */
export interface DropHow { from: DropZone; add: boolean }

const REORDER = (why: TabDrop["why"]): TabDrop => ({ kind: "reorder", add: null, remove: [], keep: [], why });
const NOWHERE: DropHow = { from: { section: null, trail: false }, add: false };

/** The routes a REMOVAL of `sid` from this union takes: the local tag that actually HOLDS the member
 *  (not simply the union's first local tag — two local tags may share a name, and the op must land on
 *  the one whose membership is going), and every remote home holding the pair. Null when nothing holds
 *  it: the strip drew a copy the blob no longer explains, and there is nothing to write. */
function removalRoute(g: TagUnion, sid: string): TagRoute | null {
  const local = g.locals.find((t) => (t.members || []).includes(sid) && !/^pending-/.test(t.id));
  const hosts = g.remotes.filter((rt) => (rt.members || []).includes(sid)).map((rt) => rt.host || "");
  if (!local && !hosts.length) return null;
  return { tag: g.name, tid: local ? local.id : null, hosts };
}

/** The route an ADD of a session to this union takes: its local tag by id, else its single remote home.
 *  Null for a union whose local tag is a create still in flight with no remote home: its id is the
 *  placeholder the ack replaces and the kernel would refuse an op addressed by it (the Tags flyout stands
 *  down on the same union for the same reason). */
function addRoute(g: TagUnion): TagRoute | null {
  if (g.localId && !g.pending) return { tag: g.name, tid: g.localId, hosts: [] };
  if (g.remotes.length) return { tag: g.name, tid: null, hosts: [g.remotes[0].host || ""] };
  return null;
}

/** THE RULE (the account above). `zone` is where the provisional tab sits, `sid` the session dragged,
 *  `how` where its copy came from and whether the copy modifier is held.
 *  - the group the copy came from → `reorder` (`same`): a drag inside one group is exactly what it has
 *    always been, and render.ts lights no cue for it, so nothing flashes
 *  - another group's row → `move`: the session's tags become exactly that one, landing at the drop index
 *    among the group's members (the reorder still applies, render.ts runs it unchanged). A session
 *    whose only tag already is that one → `reorder` (`already`)
 *  - another group's row with the modifier held → `join`: it gains that tag and keeps the rest; a session
 *    already carrying it → `reorder` (`already`)
 *  - the ungrouped row, dragging a TAGGED session → `leave`: every tag it carries comes off, and the tab
 *    lands in that row at the drop index, under no group
 *  - the ungrouped row, dragging an untagged tab → `reorder` (`untagged`): trail to trail is a plain
 *    reorder, as it has always been
 *  - the head of the strip, or a flat strip → `reorder` (`head`): not the ungrouped row, nothing cleared
 *  - a name no union holds → `reorder` (`unknown`): the strip drew a section the blob no longer explains
 *  - a group whose tag is a create still in flight, with no remote home, that the session is not in →
 *    `reorder` (`pending`): nothing addressable to add it to */
export function resolveTabDrop(unions: readonly TagUnion[], zone: DropZone, sid: string, how: DropHow = NOWHERE): TabDrop {
  if (zone.section !== null) {
    const g = unions.find((u) => u.name === zone.section);
    if (!g) return REORDER("unknown");
    if (how.from.section === zone.section) return REORDER("same");
    const has = (g.members || []).includes(sid);
    const add = has ? null : addRoute(g);
    if (!has && !add) return REORDER("pending");
    if (how.add) return add ? { kind: "join", add, remove: [], keep: [], why: "" } : REORDER("already");
    const remove = unions.filter((u) => u !== g && (u.members || []).includes(sid))
      .map((u) => removalRoute(u, sid)).filter((r): r is TagRoute => r !== null);
    if (!add && !remove.length) return REORDER("already");
    // the local tags it holds afterwards: the target's own that already hold it, or the one it is added to
    const keep = has ? g.locals.filter((t) => (t.members || []).includes(sid) && !/^pending-/.test(t.id)).map((t) => t.id)
                     : (add && add.tid ? [add.tid] : []);
    return { kind: "move", add, remove, keep, why: "" };
  }
  if (!zone.trail) return REORDER("head");
  const holders = unions.filter((u) => (u.members || []).includes(sid));
  if (!holders.length) return REORDER("untagged");
  const remove = holders.map((g) => removalRoute(g, sid)).filter((r): r is TagRoute => r !== null);
  return remove.length ? { kind: "leave", add: null, remove, keep: [], why: "" } : REORDER("unknown");
}

/** THE TAB MENU'S "Move to <tag>" (the user 2026-10-01, who wanted the menu's move to do what the drag's does):
 *  the plain drop into that group, without the geometry, so the menu and the drag are one rule and one write
 *  (render.ts posts it through postTabDropTag, the drop's own writer). The session's tags become exactly
 *  [tag], whichever copy was right-clicked. The row's "+" is the menu's Option-drag, an add, and does not come
 *  through here. */
export function moveToTag(unions: readonly TagUnion[], tag: string, sid: string): TabDrop {
  return resolveTabDrop(unions, { section: tag, trail: false }, sid);
}

/** Does this drop write a tag — the one bit the dragover's cue needs (the group about to take the
 *  session wears the accent ring, a release that takes tags off names them; nothing shows for a
 *  reorder). Stated here so the cue and the drop can only ever read the same rule. */
export function writesTag(d: TabDrop): boolean {
  return d.kind !== "reorder";
}

/** The LOCAL store's ops that carry a drop, in the order they are posted. With the kernel's `tagSet`
 *  cap (`canSet`), a move or a clear is ONE `setTags` op listing the local tags the session keeps, which
 *  the kernel applies under its lock, all of it or none. Without it (an older kernel), the fewest older
 *  ops that reach the same tags: a one-to-one move is the atomic `move`, the rest addMember then
 *  removeMember in turn (the add first, so a device reading between two ops sees the session in one
 *  group too many, never in none). A drop whose tags are all homed on other kernels has no local op. */
export function dropLocalOps(d: TabDrop, sid: string, canSet: boolean): TagEditOp[] {
  if (d.kind === "reorder") return [];
  const on = d.add && d.add.tid ? d.add.tid : null;
  const off = d.remove.map((r) => r.tid).filter((t): t is string => !!t);
  if (d.kind === "join") return on ? [{ op: "addMember", tid: on, sids: [sid] }] : [];
  if (!on && !off.length) return [];
  if (canSet) return [{ op: "setTags", tids: d.keep.slice(), sids: [sid] }];
  const ops: TagEditOp[] = [];
  if (on && off.length) ops.push({ op: "move", tid_from: off[0], tid_to: on, sids: [sid] });
  else if (on) ops.push({ op: "addMember", tid: on, sids: [sid] });
  for (const tid of on ? off.slice(1) : off) ops.push({ op: "removeMember", tid, sids: [sid] });
  return ops;
}

/** The `editTag` wire's edits for the drop's tags homed on OTHER kernels: the add on the name's home,
 *  a removal on every host holding the pair. */
export interface RemoteTagEdit { host: string; name: string; add?: string[]; remove?: string[] }
export function dropRemoteEdits(d: TabDrop, sid: string): RemoteTagEdit[] {
  if (d.kind === "reorder") return [];
  const out: RemoteTagEdit[] = [];
  if (d.add) for (const host of d.add.hosts) out.push({ host, name: d.add.tag, add: [sid] });
  for (const r of d.remove) for (const host of r.hosts) out.push({ host, name: r.tag, remove: [sid] });
  return out;
}

/** The words the cue carries BEFORE the release. Taking tags off is more than the tab's move says, so
 *  the cue must name what is going while the hand can still move: `leaving` is the tags coming off (a
 *  move's other tags, a clear's every one), capped at `limit` with `more` counting the rest (the
 *  many-tags rule: dozens of tags on every surface, so a cue is a bounded run and never a list of them
 *  all). Empty for an add (the ring on the target header and the "+" are that cue) and for a move into a
 *  group from no tag at all. This label is the whole safety: there is no undo stack (see the account
 *  above). */
export interface LeaveWords { leaving: string[]; more: number }
export function leaveWords(d: TabDrop, limit = 3): LeaveWords {
  if (d.kind !== "leave" && d.kind !== "move") return { leaving: [], more: 0 };
  const names = d.remove.map((t) => t.tag);
  return { leaving: names.slice(0, limit), more: Math.max(0, names.length - limit) };
}
