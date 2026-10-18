// WHERE THE KEYBOARD IS, across the whole window (the dashboard's columns, 2026-10-18). On the dashboard a chat page is one
// pane among several same-origin frames of the shell: chat columns side by side, the feed, the files pane. The page's own
// document.activeElement says only where ITS focus is; the keys may be going to another pane. The shell's document is the
// authority: its activeElement is the frame holding the focus, and that frame's document names the element inside it, on
// down to the last frame.
//
// VS Code's webview has a top of another origin, which this page cannot read: there the answer is the page's own
// document.activeElement, what every caller read before this existed. A frame on the way down that belongs to another
// origin is returned as itself: the keys are in there, and this page cannot see where.

/** The element the window's keyboard focus is on, read from the top document when this page can reach it. */
export function windowFocusedElement(win: Window): Element | null {
  let at: Element | null;
  try { at = (win.top || win).document.activeElement; }
  catch { return win.document.activeElement; }   // a cross-origin top (VS Code's webview): this page's own focus, as before
  while (at && at.tagName === "IFRAME") {   // a frame holds the focus: the element is inside it, one document further down
    let inner: Document | null = null;
    try { inner = (at as HTMLIFrameElement).contentDocument; } catch { inner = null; }
    if (!inner || !inner.activeElement) break;   // another origin's frame: the keys are in there, out of this page's sight
    at = inner.activeElement;
  }
  return at;
}
