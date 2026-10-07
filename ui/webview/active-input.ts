// THE ACTIVE INPUT (the user 2026-10-07, who wanted the message box's behaviours in the comment threads and the file
// viewer): typing that starts outside any box, a paste, and a dictated utterance all go to ONE box, the box of the
// topmost surface open on the page. Before this the message box under the chat took them whatever was open, so words
// dictated over a file viewer, or over a comment thread, landed in the wrong place.
//
// The order is the surfaces' stacking order, top first:
//   1. the file viewer MODAL (it sits over everything else on the pane, a comment popover included): its message bar,
//      or nothing at all when it has none (a file with no session to speak to, or the bar standing down while the file is
//      edited): a full-pane surface owns its keys;
//   2. an open comment POPOVER whose reply box takes typing (a thread, open or relayed or resolved, or the new-comment
//      box). A popover with no such box (a promoted thread's view, a sent comment waiting for its thread) takes nothing
//      and the rule reads on, as it did before there was a rule;
//   3. the FILE TAB being shown: its message bar, or nothing when it has none (the pane shows no message box then);
//   4. the MESSAGE BOX under the chat.
// A box the user is already typing in is never part of this: the caller checks that first and leaves the keys alone.
// Pure, so the rule is executed by its test (active-input.test.ts) for every combination of open surfaces; render.ts
// reads the page into this shape (activeInputBox) and applies its own gates around it.

/** What is open on the page, as the rule needs it. A viewer surface is absent (null), up with a message bar that takes
 *  typing ("bar"), or up without one ("none"). */
export interface OpenSurfaces {
  viewerModal: null | "bar" | "none";
  /** a comment popover is open and its reply box takes typing (not read-only) */
  comment: boolean;
  fileTab: null | "bar" | "none";
  /** the chat's message box is on the page */
  composer: boolean;
}

export type ActiveInput = "viewerModal" | "comment" | "fileTab" | "composer";

/** The box typing, pasting and dictation go to, or null while the topmost surface takes none. */
export function activeInput(s: OpenSurfaces): ActiveInput | null {
  if (s.viewerModal) return s.viewerModal === "bar" ? "viewerModal" : null;
  if (s.comment) return "comment";
  if (s.fileTab) return s.fileTab === "bar" ? "fileTab" : null;
  return s.composer ? "composer" : null;
}
