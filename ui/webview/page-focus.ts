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

/** The window holding the page's keyboard focus, and the frame element it sits in (null for the top). */
interface Holder { win: Window; frame: HTMLIFrameElement | null }

/** Walked down from the top document's active element through same-origin frames. null when the page does not hold the focus,
 *  and when that cannot be told: a top this document cannot read (VS Code's webview sits under a cross-origin top), or the
 *  focus inside a cross-origin frame (a sandboxed URL-source pane), whose window takes no listener of ours. */
function holder(self: Window): Holder | null {
  let win: Window;
  let frame: HTMLIFrameElement | null = null;
  try { win = self.top || self; if (!win.document.hasFocus()) return null; } catch { return null; }
  for (;;) {
    let a: Element | null;
    try { a = win.document.activeElement; } catch { return null; }
    if (!a || a.tagName !== "IFRAME") return { win, frame };
    const f = a as HTMLIFrameElement;
    let c: Window | null;
    try { c = f.contentWindow; if (c) void c.document; } catch { return null; }   // a cross-origin frame: its document is not ours to read
    if (!c) return { win, frame };
    win = c; frame = f;
  }
}

/** The window holding the keyboard focus of the page `self` is part of (see holder for when it is null). */
export function pageFocusWindow(self: Window): Window | null {
  const h = holder(self);
  return h ? h.win : null;
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
  const hook = (h: Holder): boolean => {
    try {
      h.win.addEventListener("blur", check);
      if (h.win !== self) h.win.addEventListener("pagehide", away);   // this document's own end is off (below)
    } catch { return false; }
    win = h.win; frame = h.frame;
    return true;
  };
  // a blur of the window holding the focus (or the watch's start at a blur): the page lost the focus, or it cannot be told
  // (lost runs, once, and the watch is off), or the focus moved to another frame of the page (the watch moves with it)
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
