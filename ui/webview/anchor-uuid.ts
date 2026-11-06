// The record a quote or a comment anchors to, from the node a selection starts in (2026-11-06). A transcript row anchors on
// its turn's uuid (`.turn[data-uuid]`). A run's caption (looks C and C2 of the progress-note drafts) is a note drawn inside a
// tool run's head, whose turn wears the first TOOL's uuid; the caption carries the note's own record as data-note-uuid, and a
// selection that starts in it anchors there, never on the tool. Pure over the DOM's closest(), so it runs in node tests.

interface ClosestLike { closest(sel: string): { getAttribute(name: string): string | null } | null }
interface NodeLike { parentElement?: unknown }

/** The uuid a selection starting at `node` anchors to: a caption's note, else its turn's record, else null. */
export function anchorUuidAt(node: unknown): string | null {
  const el = (node && typeof (node as ClosestLike).closest === "function" ? node : (node as NodeLike | null)?.parentElement) as ClosestLike | null | undefined;
  if (!el || typeof el.closest !== "function") return null;
  const note = el.closest("[data-note-uuid]");
  if (note) return note.getAttribute("data-note-uuid");
  return el.closest(".turn")?.getAttribute("data-uuid") || null;
}
