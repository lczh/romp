// Remembered size + maximize for the comment popover (the user 2026-09-10, who kept enlarging the box
// by hand on every open): the size the user last dragged it to is applied on every open, in both the
// thread and the create dialog, and a maximize control (the head's button, or a double-click on the
// title bar) snaps it to the cap and back. Pure logic split out of render.ts so it can be unit-tested
// without a DOM (the tabbar-resize.ts pattern); the wiring lives in renderCommentPopover.

// localStorage key: the chosen size is per-viewer ARRANGEMENT, like the tab strip's dragged cap
// (romp:tabbarH) and the tab order (romp:vieworder) — a property of how you are looking at your
// sessions on THIS screen, not a setting the kernel should carry to every dashboard. Stored as
// FRACTIONS of the window so the same entry means the same thing on a phone and a desktop.
export const CMT_POP_SIZE_KEY = "romp:cmtPopSize";

export type CmtPopFrac = { w: number; h: number };   // each in (0, 1]

// The floor: the .cmt-pop CSS mins (min-width: 300px; min-height: 120px), restated for the math the
// same way wireEdgeResize's MIN_W/MIN_H restate them.
export const CMT_POP_MIN_W = 300;
export const CMT_POP_MIN_H = 120;
// The cap: the .cmt-pop CSS caps (max-width: 94vw; max-height: 90vh), as fractions.
export const CMT_POP_CAP_W = 0.94;
export const CMT_POP_CAP_H = 0.90;
// The open geometry keeps 8px of window on every side (renderCommentPopover's left/top clamp), so an
// applied size also stays 8px short of each edge — a stored fraction can never park the box off-screen.
export const CMT_POP_EDGE = 8;
// The thread dialog's default when nothing is stored (renderCommentPopover's 70%/60% open geometry).
export const CMT_POP_THREAD_DEFAULT: CmtPopFrac = { w: 0.7, h: 0.6 };
// "Maximized" is COMPUTED from the live size, never a stored bit that could drift: within this many px
// of the cap on both axes.
export const CMT_POP_MAX_SLACK = 4;

/** The largest box the window allows: the CSS caps, and never closer than CMT_POP_EDGE to an edge. */
export function cmtPopCapPx(innerW: number, innerH: number): { w: number; h: number } {
  return {
    w: Math.min(Math.round(innerW * CMT_POP_CAP_W), Math.round(innerW - 2 * CMT_POP_EDGE)),
    h: Math.min(Math.round(innerH * CMT_POP_CAP_H), Math.round(innerH - 2 * CMT_POP_EDGE)),
  };
}

/** A stored fraction as pixels for the LIVE window: capped by cmtPopCapPx, then floored by the CSS mins
 *  (the floor wins on a window too small for both, exactly as CSS resolves min-width over max-width). */
export function clampCmtPopPx(frac: CmtPopFrac, innerW: number, innerH: number): { w: number; h: number } {
  const cap = cmtPopCapPx(innerW, innerH);
  return {
    w: Math.max(CMT_POP_MIN_W, Math.min(cap.w, Math.round(frac.w * innerW))),
    h: Math.max(CMT_POP_MIN_H, Math.min(cap.h, Math.round(frac.h * innerH))),
  };
}

/** A live pixel size as the fraction to store: of the window, clamped to (0, 1]. */
export function toCmtPopFrac(wPx: number, hPx: number, innerW: number, innerH: number): CmtPopFrac {
  const f = (px: number, win: number) => (win > 0 ? Math.min(1, Math.max(0.01, px / win)) : 1);
  return { w: f(wPx, innerW), h: f(hPx, innerH) };
}

/** A stored entry: a fraction pair, or null for never stored / reset / garbage — never throws. */
export function parseCmtPopSize(raw: string | null): CmtPopFrac | null {
  if (!raw) return null;
  let v: unknown;
  try { v = JSON.parse(raw); } catch { return null; }
  if (!v || typeof v !== "object") return null;
  const { w, h } = v as { w?: unknown; h?: unknown };
  const ok = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n) && n > 0 && n <= 1;
  return ok(w) && ok(h) ? { w, h } : null;
}

/** Whether a live size IS the cap (both axes within CMT_POP_MAX_SLACK px) — the maximize toggle's state. */
export function isCmtPopMax(wPx: number, hPx: number, innerW: number, innerH: number): boolean {
  const cap = cmtPopCapPx(innerW, innerH);
  return Math.abs(wPx - cap.w) <= CMT_POP_MAX_SLACK && Math.abs(hPx - cap.h) <= CMT_POP_MAX_SLACK;
}

/** The position that centers a w×h box in the window, never nearer than CMT_POP_EDGE to the top-left. */
export function centerCmtPop(w: number, h: number, innerW: number, innerH: number): { left: number; top: number } {
  return {
    left: Math.max(CMT_POP_EDGE, Math.round((innerW - w) / 2)),
    top: Math.max(CMT_POP_EDGE, Math.round((innerH - h) / 2)),
  };
}

/** A box on screen, in viewport pixels. */
export type CmtPopRect = { left: number; top: number; right: number; bottom: number };

/** Whether the box covers any of the passage. */
export function cmtPopCovers(box: CmtPopRect, at: CmtPopRect): boolean {
  return box.left < at.right && box.right > at.left && box.top < at.bottom && box.bottom > at.top;
}

/** Where a thread's box goes when a landing opens it on its passage (the rail tick, the turn's rail line, the box's own
 *  quote): null when the box, where it is, covers none of the passage, so it stays put; else the top (and the height)
 *  that keep the passage clear, below it when the box fits there, else above it, else on the roomier side at that side's
 *  height and never under the CSS floor. Only the vertical moves: the box keeps its left edge and its width, so the
 *  user's own spot and size come back the next time it opens without a landing. `gap` keeps a line of air between the
 *  passage and the box. */
export function cmtPopBeside(box: CmtPopRect, at: CmtPopRect, innerH: number, gap = 8): { top: number; height: number } | null {
  if (!cmtPopCovers(box, at)) return null;
  const h = box.bottom - box.top;
  const below = innerH - CMT_POP_EDGE - (at.bottom + gap);   // the room between the passage's foot and the window's
  const above = at.top - gap - CMT_POP_EDGE;                 // …and between the window's top and the passage's head
  const fit = (top: number, height: number) => ({ top: Math.round(Math.max(CMT_POP_EDGE, Math.min(top, innerH - CMT_POP_EDGE - height))), height: Math.round(height) });
  if (below >= h) return fit(at.bottom + gap, h);
  if (above >= h) return fit(at.top - gap - h, h);
  if (below >= above) { const hh = Math.max(CMT_POP_MIN_H, below); return fit(at.bottom + gap, hh); }
  const hh = Math.max(CMT_POP_MIN_H, above);
  return fit(at.top - gap - hh, hh);
}

/** Where the box goes when its passage moved INTO it after the landing placed it beside it (the landing re-landed the
 *  passage while a control joined the reply under it): null when they do not overlap, so the box stays exactly where it
 *  is; else the nearest spot clear of the passage, the box's foot just above it or its head just below it, whichever is
 *  the shorter move and fits the window at the box's size. Only when neither fits does it fall back to the landing's own
 *  rule (cmtPopBeside: the roomier side, at that side's height). Never a move further than the overlap needs. */
export function cmtPopNudge(box: CmtPopRect, at: CmtPopRect, innerH: number, gap = 8): { top: number; height: number } | null {
  if (!cmtPopCovers(box, at)) return null;
  const h = box.bottom - box.top;
  const up = at.top - gap - h, down = at.bottom + gap;
  const fits = (top: number) => top >= CMT_POP_EDGE && top + h <= innerH - CMT_POP_EDGE;
  const order = Math.abs(up - box.top) <= Math.abs(down - box.top) ? [up, down] : [down, up];
  for (const top of order) if (fits(top)) return { top: Math.round(top), height: Math.round(h) };
  return cmtPopBeside(box, at, innerH, gap);
}
