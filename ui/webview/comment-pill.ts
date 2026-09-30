// The touch "Comment" pill (the user 2026-09-30: a comment could not be started on a phone at all). A long-press selection
// in iOS Safari never fires `contextmenu`, so the transcript's right-click menu never opens there, and a page cannot add a
// row to the system's own selection callout. On a coarse pointer the chat shows a small pill just BELOW a selection that
// qualifies for a comment (iOS draws its callout above), and a tap runs the one comment door the menu, the key and the
// palette use (render.ts commentOnSelection). This module is the pure half: where the pill goes, and when it shows. The
// DOM wiring lives in render.ts.
//
// Every change keys on an event, never a timer (CLAUDE.md):
//  - a selection change with no finger down places the pill at once (a programmatic or keyboard selection), or hides it
//    when the selection stopped qualifying;
//  - while a finger is down (a long-press, a selection handle being dragged) a change only hides it, and the gesture's end,
//    the last finger's touchend or touchcancel, places it on the settled selection: handles being dragged never make it
//    flicker or jump;
//  - a scroll under a finger hides it the same way until the finger lifts; a scroll the page makes itself (the browser's
//    clamp when the quote chip this same selection seeds shrinks the transcript, a scroll's momentum, a programmatic
//    scroll) carries it along with its selection, and it goes once the selection leaves the pane. Hiding on every scroll
//    lost the pill on the very first selection: the chip's clamp is a scroll event;
//  - no pill while a comment box (or the chat's own menu) is up, and opening a box hides it;
//  - a press that starts on the pill holds it, so the selection a tap may collapse cannot take the pill away before its
//    click lands; the tap hands over the aim the pill was placed for.

export type Rect = { top: number; bottom: number; left: number; right: number };

export const PILL_GAP = 14;     // below the selection: room for iOS's end handle, whose knob hangs under the last line
export const PILL_MARGIN = 8;   // from the viewport's edges and the message box

/** Where a `w` by `h` pill goes for a selection whose box is `sel`, in a viewport `vw` wide whose usable bottom is `floor`
 *  (the message box's top, or the viewport's bottom): centred under the selection, flipped above it only when there is no
 *  room below, and clamped inside the viewport and clear of the message box. */
export function placeCommentPill(sel: Rect, w: number, h: number, vw: number, floor: number): { left: number; top: number; above: boolean } {
  let top = sel.bottom + PILL_GAP, above = false;
  if (top + h > floor - PILL_MARGIN) { top = sel.top - PILL_GAP - h; above = true; }
  top = Math.max(PILL_MARGIN, Math.min(top, floor - PILL_MARGIN - h));
  const left = Math.max(PILL_MARGIN, Math.min((sel.left + sel.right) / 2 - w / 2, vw - PILL_MARGIN - w));
  return { left, top, above };
}

/** What the controller needs from the page. */
export interface PillHost<A> {
  coarse(): boolean;       // a touch screen: the pill exists only there
  blocked(): boolean;      // a comment box (or the chat's menu) is up
  aim(): A | null;         // the comment aim of the live selection, or null when it does not qualify or is off the pane
  show(aim: A): void;      // place the pill on the live selection and show it
  hide(): void;
}

export class CommentPill<A> {
  private shown: A | null = null;   // the aim the pill is showing for; null while hidden
  private down = false;             // a finger is on the screen (a touch that did not start on the pill)
  private pressing = false;         // a press started on the pill and its click has not landed

  constructor(private host: PillHost<A>) {}

  /** The aim on screen, or null. */
  current(): A | null { return this.shown; }

  private sync(): void {
    const aim = this.host.coarse() && !this.host.blocked() ? this.host.aim() : null;
    if (aim === null) { this.hide(); return; }
    this.shown = aim;
    this.host.show(aim);
  }

  hide(): void {
    const was = this.shown;
    this.shown = null;
    if (was !== null) this.host.hide();
  }

  selectionChanged(): void {
    if (this.pressing) return;          // the tap on the pill may collapse the selection; its click still lands
    if (this.down) this.hide();         // a gesture in progress: out of the way until it settles
    else this.sync();
  }

  touchStart(onPill: boolean): void {
    this.pressing = onPill;
    if (!onPill) this.down = true;
  }

  /** A touchend or touchcancel, with `remaining` fingers still down. */
  touchEnd(remaining: number): void {
    if (remaining > 0 || !this.down) return;
    this.down = false;
    this.sync();                        // the settled selection
  }

  scrolled(): void {
    if (this.down) this.hide();         // the reader's finger is moving the page: out of the way until it lifts
    else if (this.shown !== null) this.sync();   // the page moved itself: the pill stays with its selection
  }

  /** The viewport changed shape (a phone turned): a pill on screen moves with its selection. */
  resized(): void {
    if (this.shown !== null && !this.down) this.sync();
  }

  /** The pill was tapped: the aim it was placed for, and it goes. */
  tapped(): A | null {
    const aim = this.shown;
    this.pressing = false;
    this.hide();
    return aim;
  }
}
