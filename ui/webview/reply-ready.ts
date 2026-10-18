// Replies ready (the user 2026-09-08): a reply lands on a comment you scrolled away from, and you forget
// to come back — the mark's ring and the rail tick say so only where you happen to be looking. Two chips
// beside the go-to-bottom chip counted the unread replies above and below the viewport until 2026-09-24, when
// they folded into the jump cluster (jump-nav.ts; render.ts updateJumpCluster), whose badge counts every one
// of them since 2026-10-15. This module is the PURE half (node-testable, no DOM): the one predicate for "a
// landed reply waits here", the placement of a windowed-out turn, and the phrase.
import type { CommentThread } from "./comments";

export type Dir = "above" | "below";

/** THE predicate: the kernel's `unread` bit ("a FINISHED reply you have not seen", T237) on an OPEN thread —
 *  exactly what the mark's `.unread` class and the rail tick's `.unread` read (styleCommentMark /
 *  updateCommentRail keep the verbatim form; this is the same expression, named). A resolved or merged
 *  thread with a stale bit is not a reply waiting for anyone. */
export function isReplyReady(th: Pick<CommentThread, "unread" | "status">): boolean {
  return !!th.unread && th.status === "open";
}

/** A mark whose turn is WINDOWED OUT (the chat virtualizes: a rendered window between two spacers) has
 *  no box, but its direction is exact from event order: the window always extends past the viewport, so
 *  a unit before the window's first rendered unit is above, one at/after its end is below. Its distance
 *  lives in a tier ABOVE every pixel distance (WINDOWED_OUT + units from the window edge), so a rendered
 *  mark is always nearer than a windowed-out one and windowed-out marks still order among themselves.
 *  `unit < 0` = not in the resident events at all (older than the streamed-in head): above, farthest. A
 *  unit INSIDE the window with no turn of its own (a member of a folded tool run) reads "in" — it is on
 *  screen in folded form, and the rail tick still reaches it. */
export const WINDOWED_OUT = 1e9;
export function placeWindowed(unit: number, winStart: number, winEnd: number): { dir: Dir | "in"; dist: number } {
  if (unit < 0) return { dir: "above", dist: WINDOWED_OUT * 2 };
  if (unit < winStart) return { dir: "above", dist: WINDOWED_OUT + (winStart - unit) };
  if (unit >= winEnd) return { dir: "below", dist: WINDOWED_OUT + (unit - winEnd + 1) };
  return { dir: "in", dist: 0 };
}

// ── copy: the user's own words ────────────────────────────────────────────────────────────────────
// "reply"/"replies", not "comment": the unread thing is the ANSWER to their comment (the coordinator's
// refinement, 2026-09-08). The jump cluster's badge tip reads this phrase (jump-nav.ts countTip).
export function replyWord(n: number): string {
  return n === 1 ? "1 reply unread" : n + " replies unread";
}
