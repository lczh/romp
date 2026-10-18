// Where the page's keyboard focus is, read from the TOP document, and a watch that hears the page lose it wherever in the page
// the focus is held. Two gestures need it: a docking-kit divider drag (panedock-main.ts) and the chat's press-hold on its tab
// strip (render.ts releaseTabStrip). Each ends when the window loses the focus (a page put behind another mid-press hears
// neither a pointerup nor a pointercancel), and each must go on when the focus only moves to another frame of the same page.
//
// The two look alike from inside a frame. Its window's blur fires for both, and its own document.hasFocus() reads false for
// both. The top document tells them apart: Chromium moves the focused frame before it fires the blur of focus moving between
// frames, and drops the page's focus before it fires a window switch's (focus_controller.cc: SetFocusedFrame, SetFocused), so
// the top's hasFocus() reads true for the one and false for the other, read at the blur of ANY frame of the page (the review
// of PR 302, in the full Chromium with focus emulation off). A window switch blurs only the window holding the focus, so the
// watch listens there, and moves with the focus at every blur that leaves the page focused.

/** One window on the way down from the top: the top itself (frame null), or a frame's window and its frame element. */
export interface FocusStep { win: Window; frame: HTMLIFrameElement | null }

/** The page's keyboard focus, walked down from the top document (walkPageFocus). */
export interface FocusWalk {
  /** The windows read, the top first, each frame's below the one holding its element; empty when the top cannot be read. */
  chain: FocusStep[];
  /** The innermost element holding the focus: the last document's active element, or the frame element itself when the walk
   *  went no further into it (a frame of another origin, a frame with no window, a frame whose document has no active element).
   *  null with no active element at the top, or the top unreadable. */
  element: Element | null;
  /** Why the walk stopped. "unreadable-top": this document cannot read the top (VS Code's webview sits under a cross-origin
   *  top). "cross-origin": the focus is inside a frame of another origin (a sandboxed URL-source pane), `element` that frame,
   *  whose window takes no listener of ours and whose document is not ours to read. "leaf": the end of the chain was reached. */
  stop: "unreadable-top" | "cross-origin" | "leaf";
}

/** The one walk of the page's focus: from the top document's active element down through every frame whose document this
 *  document can read. Each caller puts its own policy on the edges: holder (below) asks the page to hold the focus and calls
 *  either stop short of a leaf "cannot be told"; window-focus.ts's windowFocusedElement falls back to its own document for an
 *  unreadable top and answers a frame of another origin with the frame element. A frame's document is read as contentDocument,
 *  which the DOM answers null for a frame of another origin (where its window's document throws) and for a frame with no window,
 *  the two told apart by contentWindow. */
export function walkPageFocus(self: Window): FocusWalk {
  let win: Window, at: Element | null;
  try { win = self.top || self; at = win.document.activeElement; }
  catch { return { chain: [], element: null, stop: "unreadable-top" }; }
  const chain: FocusStep[] = [{ win, frame: null }];
  while (at && at.tagName === "IFRAME") {
    const f = at as HTMLIFrameElement;
    let inner: Window | null, doc: Document | null;
    try { inner = f.contentWindow; doc = f.contentDocument; } catch { return { chain, element: f, stop: "cross-origin" }; }
    if (!doc) return { chain, element: f, stop: inner ? "cross-origin" : "leaf" };
    chain.push({ win: inner as Window, frame: f });
    if (!doc.activeElement) return { chain, element: f, stop: "leaf" };
    at = doc.activeElement;
  }
  return { chain, element: at, stop: "leaf" };
}

/** The window holding the page's keyboard focus, and the frame element it sits in (null for the top). null when the page does
 *  not hold the focus, and when that cannot be told: a top this document cannot read, or the focus inside a frame of another
 *  origin, whose window takes no listener of ours. */
function holder(self: Window): FocusStep | null {
  const w = walkPageFocus(self);
  if (w.stop !== "leaf") return null;
  try { if (!w.chain[0].win.document.hasFocus()) return null; } catch { return null; }
  return w.chain[w.chain.length - 1];
}

function follow(self: Window, lost: () => void, atBlur: boolean): () => void {
  let win: Window | null = null, frame: HTMLIFrameElement | null = null, reload: HTMLIFrameElement | null = null, done = false;
  const unhook = (): void => {
    if (win) { try { win.removeEventListener("blur", check); win.removeEventListener("pagehide", away); } catch { /* gone */ } win = null; }
    if (reload) { reload.removeEventListener("load", check); reload = null; }
    frame = null;
  };
  const off = (): void => {
    if (done) return;
    done = true; unhook();
    self.removeEventListener("pagehide", off);
  };
  const hook = (h: FocusStep): boolean => {
    try {
      h.win.addEventListener("blur", check);
      if (h.win !== self) h.win.addEventListener("pagehide", away);   // this document's own end is off (below)
    } catch { return false; }
    win = h.win; frame = h.frame;
    return true;
  };
  // a blur of the window holding the focus (or the watch's start at a blur): the page lost the focus, or it cannot be told
  // (lost runs, once, and the watch is off), or the focus moved to another frame of the page (the watch moves with it).
  // THE LIMIT (the review of PR 307, reproduced in the full Chromium): "cannot be told" takes in the focus moving INTO a frame of
  // another origin, so that ends the gesture as a lost focus. PR 302's rule that a pane focusing itself ends nothing holds for
  // same-origin panes only: a sandboxed URL-source pane (sandboxed without allow-same-origin, kernel.py's pane frames) focusing
  // itself by script ends a divider drag by its cancel path (the layout restored, nothing written) and releases the strip's
  // press-hold. The move itself reads here (the top still has the focus, its active element that frame); what nothing can read is
  // the rest of the gesture. With the keys in that frame a window switch blurs that frame's window alone, which takes no listener
  // of ours (a SecurityError), and nothing reaches the top or a same-origin frame: no blur, no focusout, no visibilitychange, only
  // the top's hasFocus() turning false in silence. No signal then tells a switch from the user still at work in the pane, so going
  // on would leave a gesture no switch can end; only a timer polling hasFocus() could tell, and this module keys on events only.
  function check(): void {
    if (done) return;
    unhook();
    const h = holder(self);
    if (h && hook(h)) return;
    off(); lost();
  }
  // the document holding the focus is going. A reload or a navigation keeps the frame, and the focus in it (Chromium, read in a lab:
  // the top's hasFocus true and its active element that frame at the old document's pagehide and at the new one's load), so the
  // new document is read at the frame's load, and the old window's listeners go now. A frame REMOVED never loads again, and leaves
  // the page with no focused frame, so a window switch then blurs no window at all (the same lab): the load awaited until the watch
  // is off is all there is to wait for
  function away(): void {
    if (done) return;
    const f = frame;
    unhook();
    if (f) { f.addEventListener("load", check); reload = f; }
    else check();
  }
  self.addEventListener("pagehide", off);   // this document going (a closed column): nothing of it may stay on another window
  if (atBlur) check();
  else { const h = holder(self); if (!(h && hook(h))) hook({ win: self, frame: null }); }   // unreadable at the start: this window's own blur
  return off;
}

/** `self`'s window has just blurred. If the page lost the keyboard focus, or that cannot be told (VS Code's webview: the top is
 *  cross-origin, and a blur there ends the gesture as it always did), `lost` runs now. If the focus only moved to another frame
 *  of the page, `lost` runs later, once, when the page loses it. Returns the watch's off switch (a no-op once `lost` ran). */
export function pageBlurred(self: Window, lost: () => void): () => void {
  return follow(self, lost, true);
}

/** From now on, `lost` runs once when the page loses the keyboard focus, wherever in the page the focus is held. With the focus
 *  where it cannot be followed at the start (a cross-origin frame), `self`'s own blur is what is heard. Returns the off switch. */
export function watchPageFocus(self: Window, lost: () => void): () => void {
  return follow(self, lost, false);
}
