// "Apply now" (2026-10-01) — the one row the Billing flyout and the effort and fast pickers offer while a pick of theirs
// waits for a quiet session. A billing, effort or fast pick relaunches the session's CLI, and since that day the relaunch
// waits until nothing would be cut; a session whose monitors never let it go quiet kept its pick waiting for hours. This
// row is the explicit way through, at the cost it names: the kernel relaunches at once (Restart session's road), ending
// the work the pick waited on, and the session is told which background tasks ended.
//
// What this module owns, so the surfaces that offer the row cannot drift:
//   * WHEN it shows — only while that control's own pick waits AND something holds it (the kernel's relaunchWaits);
//   * the WORDS — the label, the sub-line naming what it ends (the kernel's relaunchCuts, the same reading as
//     relaunchWaits less what a cut relaunch leaves alone: a queued message survives it), and the confirm, in Restart
//     session's shape (restart-row.ts: the title asks, the detail names the cost, then what the session keeps; no danger
//     mark, since nothing is destroyed);
//   * the GESTURE — a click with something to end confirms first (Cancel, Escape and the backdrop post nothing), one
//     with nothing to end posts at once; the post is the caller's own { type: "applyNow", id }.
import type { ConfirmButton } from "./ctx-menu";
import { RESTART_STANDING } from "./clear-confirm";

export type WaitingPick = "billing" | "effort" | "fast";
/** the slice of a session's status the row reads (render.ts's Status satisfies it structurally) */
export interface ApplyNowStatus {
  authPending?: boolean; effortPending?: boolean; fastPending?: boolean;
  relaunchWaits?: string;   // what the relaunch waits for ("2 background tasks"; "" when nothing holds it)
  relaunchCuts?: string;    // what pushing it through now would end ("" when nothing would)
}

export const APPLY_NOW_LABEL = "Apply now";

/** Does the control for `pick` offer the row? Its own pick waits, and something holds the relaunch: a pick nothing holds
 *  is applying already, and a waiting pick of another control is that control's row to offer. */
export function applyNowOffered(st: ApplyNowStatus | null | undefined, pick: WaitingPick): boolean {
  if (!st) return false;
  const waiting = pick === "billing" ? st.authPending : pick === "effort" ? st.effortPending : st.fastPending;
  return !!waiting && !!st.relaunchWaits;
}

/** The row's sub-line: what the click ends, in the kernel's words. */
export function applyNowSub(st: ApplyNowStatus): string {
  return st.relaunchCuts ? "ends " + st.relaunchCuts : "relaunches it now";
}

/** The confirm's title, Restart session's shape. */
export function applyNowTitle(name: string): string { return `Apply the change to “${name}” now?`; }

/** The confirm's detail: what the click ends, then what the session keeps (Restart session's standing sentence). */
export function applyNowDetail(cuts: string): string { return `Applying it now ends ${cuts}. ` + RESTART_STANDING; }

/** The confirm's buttons: no danger mark — nothing is destroyed, the work it names is cut. */
export const APPLY_NOW_BUTTONS: ConfirmButton[] = [{ label: APPLY_NOW_LABEL, value: "apply" }, { label: "Cancel", value: "" }];

export interface ApplyNowCtx {
  name: string;                 // the session as the user calls it, for the confirm's title
  cuts: string;                 // what the click ends (relaunchCuts as the click reads it), "" for nothing
  confirm: (title: string, detail: string, buttons: ConfirmButton[], cb: (v: string | null) => void) => void;
  post: () => void;             // the page's own postMessage of { type: "applyNow", id: sid }
}

/** The click: something to end confirms first, nothing to end posts at once. The caller has closed its menu already, so
 *  the dialog is this click's acknowledgement and never sits under an open card. */
export function requestApplyNow(ctx: ApplyNowCtx): void {
  if (!ctx.cuts) { ctx.post(); return; }
  ctx.confirm(applyNowTitle(ctx.name), applyNowDetail(ctx.cuts), APPLY_NOW_BUTTONS, (v) => { if (v === "apply") ctx.post(); });
}
