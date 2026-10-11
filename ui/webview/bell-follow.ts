// Bells follow the dashboard (2026-10-10; plans/federated-push.md, "Bells follow the dashboard").
//
// A session or card on a LINKED kernel (its id wears the "host:" prefix federation gives it) notifies the devices
// subscribed at the kernel THIS page is served by: that kernel pulls the linked kernel's events and decides by its own
// switches. So its bell is painted by the same rule, from the linked kernel's raw override (`notifyRaw`: true, false,
// or null for none) and this kernel's master (the `notifySwitches` frame, local only): an override stands, and no
// override follows this master. A click on such a bell sends the value as given (`asGiven`), which the linked kernel
// stores exactly as sent rather than judging it against its own master.
//
// An older linked kernel sends no `notifyRaw`, serves no pull, and keeps its own rules (its legacy relay gates on its
// switches): its bell is painted as IT reports it, the click goes out as before, and the bell says so in its hover
// (`legacy`), never a guess at what the new rule would make of it. A local id is untouched: `notify` as the kernel sent it.
// Side-effect free, like host-prefix.ts: imported by the feed, the chat and the timeline boot.
//
// A SESSION's bell (2026-10-11, the user's call: the session bell is a choice the user makes, and it beats the defaults) is
// three-way: on, off, or no choice of its own. Its turn ends (the turns the user started) notify when it is explicitly on,
// or with no choice when this kernel's master AND turn-finished switches are both on; an explicit off silences them. Its
// blocks and completions follow the card rule above (a card's own choice, else the session's, else the master). So the
// session bell reads "on" when the session notifies for everything a session can (`fullOn`), a click stores the opposite
// as the session's own setting, and the menu's sub-line says whether this is that setting or the defaults
// (`sessionBell`). The switches are this kernel's for a local session and a linked one alike: the hub decides.
import { hostOf } from "./host-prefix";

let hubAll: boolean | null = null;      // this kernel's master, from its notifySwitches frame (null until the first)
let hubTurns: boolean | null = null;    // …and its turn-finished switch, from the same frame

/** The local kernel's switches frame: {type: "notifySwitches", all, turns}. True when either switch moved. */
export function setHubSwitches(m: { all?: unknown; turns?: unknown }): boolean {
  const all = !!m && m.all === true;
  const turns = !!m && m.turns === true;
  const moved = hubAll !== all || hubTurns !== turns;
  hubAll = all;
  hubTurns = turns;
  return moved;
}

/** This kernel's master as last heard (null before the first frame). */
export function hubMaster(): boolean | null {
  return hubAll;
}

export interface BellFields {
  notify?: boolean | null;
  notifyRaw?: boolean | null;
}

/** How a bell paints: `on` as the devices subscribed at this kernel hear it, `legacy` when the item's kernel is a build
 *  that sends no raw override (painted as that kernel reports it, and said so). */
export function bellState(id: string, it: BellFields | null | undefined): { on: boolean; legacy: boolean } {
  const f = it || {};
  if (!hostOf(id)) return { on: !!f.notify, legacy: false };
  if (!("notifyRaw" in f) || f.notifyRaw === undefined) return { on: !!f.notify, legacy: true };
  return { on: f.notifyRaw === null ? hubAll === true : f.notifyRaw === true, legacy: false };
}

/** The fields a bell click adds to its message: `asGiven` for a linked kernel that sends raw overrides, nothing else. */
export function bellClickFields(id: string, it: BellFields | null | undefined): { asGiven?: true } {
  return hostOf(id) && !bellState(id, it).legacy ? { asGiven: true } : {};
}

/** The hover line a legacy bell adds: whose switches it follows, in the user's terms. */
export function legacyBellNote(id: string): string {
  return "follows " + hostOf(id) + "'s own notification switch: that machine runs an older romp";
}

/** A session's bell as its menu row shows it. `fullOn`: the session notifies for everything a session can (the turns the
 *  user started ending, plus its blocks and completions), which is what the row's label and icon say and what a click
 *  flips: the click stores `!fullOn` as the session's own setting. `explicit`: the session has a setting of its own
 *  (`notifyRaw` true or false) rather than following the defaults. `sub`: the row's sub-line, whose setting it is and
 *  what it means. A kernel that sends no setting (a linked kernel on an older romp: `legacy`) keeps the bell it reports
 *  and the wording it always had. */
export interface SessionBell { fullOn: boolean; explicit: boolean; legacy: boolean; sub: string }

const BELL_ALL = "when a turn you started ends, or it blocks on you or completes";
const BELL_CARDS = "when it blocks on you or completes";
const BELL_NONE = "no notifications";

export function sessionBell(id: string, it: BellFields | null | undefined): SessionBell {
  const f = it || {};
  const raw = f.notifyRaw;
  if (raw === undefined || !("notifyRaw" in f)) {
    const on = !!f.notify;
    return { fullOn: on, explicit: false, legacy: bellState(id, f).legacy,
             sub: on ? "no more system notifications for this session" : "system notification when its work blocks on you or completes" };
  }
  if (raw === true) return { fullOn: true, explicit: true, legacy: false, sub: "your setting: " + BELL_ALL };
  if (raw === false) return { fullOn: false, explicit: true, legacy: false, sub: "your setting: " + BELL_NONE };
  const all = hubAll === true, turns = hubTurns === true;
  return { fullOn: all && turns, explicit: false, legacy: false,
           sub: "default: " + (all && turns ? BELL_ALL : all ? BELL_CARDS : BELL_NONE) };
}
