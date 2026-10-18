// WHERE THE KEYBOARD IS, across the whole window (the dashboard's columns, 2026-10-18). On the dashboard a chat page is one
// pane among several same-origin frames of the shell: chat columns side by side, the feed, the files pane. The page's own
// document.activeElement says only where ITS focus is; the keys may be going to another pane. The shell's document is the
// authority: its activeElement is the frame holding the focus, and that frame's document names the element inside it, on
// down to the last frame.
//
// VS Code's webview has a top of another origin, which this page cannot read: there the answer is the page's own
// document.activeElement, what every caller read before this existed. A frame on the way down that belongs to another
// origin is returned as itself: the keys are in there, and this page cannot see where.

import { walkPageFocus } from "./page-focus";   // the one walk down the page's frames, shared with the page-focus watch

/** The element the window's keyboard focus is on, read from the top document when this page can reach it. */
export function windowFocusedElement(win: Window): Element | null {
  const w = walkPageFocus(win);
  if (w.stop === "unreadable-top") return win.document.activeElement;   // a cross-origin top (VS Code's webview): this page's own focus, as before
  return w.element;   // a frame of another origin on the way down is the frame element itself: the keys are in there, out of this page's sight
}
