// Per-session VIEW FLAGS (the bell, the feed mute, the mail mute) are flipped OPTIMISTICALLY by the tab menu's rows and
// the bell command (render.ts setSessionFlag): the local copy takes the value at the click and the kernel is told. The
// kernel's frames carry the flags back — every chatTail on both chat wires (2026-09-11) and every full session frame —
// and a frame BUILT before the kernel applied the click can land after it with the old value, reverting the copy for one
// push cycle without new information (the cards-move-on-new-information rule; the review of 2026-09-14: the bell row read
// "Notify me" again after the toast said enabled, and a second press in that window flipped from the stale value). So a
// click records its expectation here, and a frame that still disagrees is held off while it stands: a frame that agrees
// is the echo and clears it; three disagreeing frames yield, because the kernel is the store of record and setSessionFlag
// has no per-write ack beyond the settingRefused frame, which drops the expectation outright. tab-meta.ts's pending guard
// for the label and the colour is the precedent; this is the same shape for booleans. Pure and DOM-free so node executes it.
//
// The bell (2026-10-11): a click is the session's OWN setting from then on (bell-follow.ts sessionBell), and the frames
// carry that setting beside the effective value (`notifyRaw`: true, false, or null for none). So a frame that carries it
// confirms a bell click on IT: the effective `notify` can agree before the click lands (a session with no setting of its
// own under a master already at the clicked value). While the click stands the copy's setting is the clicked value too,
// so the menu paints it. A frame without the setting (a linked kernel on an older romp) confirms on `notify`, as before.

export type SessionFlag = "notify" | "hideFromFeed" | "postalServiceOff";
export const SESSION_FLAGS: readonly SessionFlag[] = ["notify", "hideFromFeed", "postalServiceOff"];
export interface PendingFlag { value: boolean; age: number }
/** sid → flag → the value the click expects and how many disagreeing frames it has held off */
export type PendingFlags = Map<string, Map<SessionFlag, PendingFlag>>;
export const PENDING_FLAG_MAX_AGE = 3;

/** A click's expectation: the frames that follow cannot show the other value while it stands. */
export function notePendingFlag(pending: PendingFlags, id: string, flag: SessionFlag, value: boolean): void {
  let m = pending.get(id);
  if (!m) { m = new Map(); pending.set(id, m); }
  m.set(flag, { value, age: 0 });
}

/** The kernel refused the click (settingRefused), or the session left: nothing to hold any more. */
export function dropPendingFlag(pending: PendingFlags, id: string, flag?: SessionFlag): void {
  const m = pending.get(id);
  if (!m) return;
  if (flag) m.delete(flag); else m.clear();
  if (!m.size) pending.delete(id);
}

/** The session's own bell setting a frame carries: true, false, null for none, undefined when the frame has none. */
export function frameRaw(frame: Record<string, unknown>): boolean | null | undefined {
  if (!("notifyRaw" in frame)) return undefined;
  const r = frame.notifyRaw;
  return r === true || r === false || r === null ? r : undefined;
}

/** Apply the flags a frame carries to the session's copy, under the guard. A flag the frame does not carry is left
 *  alone; so is the bell's own setting (`notifyRaw`), which lands beside `notify` when the frame carries it. Returns the
 *  flags whose value on the copy changed. */
export function applyFrameFlags(s: Partial<Record<SessionFlag, boolean>> & { notifyRaw?: boolean | null }, frame: Record<string, unknown>, pending: PendingFlags, id: string): SessionFlag[] {
  const changed: SessionFlag[] = [];
  const m = pending.get(id);
  const raw = frameRaw(frame);
  for (const f of SESSION_FLAGS) {
    const v = frame[f];
    if (typeof v !== "boolean") continue;
    let next = v;
    const p = m?.get(f);
    if (p) {
      const echo = f === "notify" && raw !== undefined ? raw : v;      // a bell click is the session's own setting: confirmed on it
      if (p.value === echo || p.age >= PENDING_FLAG_MAX_AGE) m!.delete(f);   // the echo; or the kernel's view standing after three silent frames
      else { p.age++; next = p.value; }                                  // a frame built before the click: held off
    }
    if (s[f] !== next) { s[f] = next; changed.push(f); }
  }
  if (raw !== undefined) {
    const held = m?.get("notify");
    s.notifyRaw = held ? held.value : raw;                               // the clicked setting while the click stands
  }
  if (m && !m.size) pending.delete(id);
  return changed;
}
