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
import { hostOf } from "./host-prefix";

let hubAll: boolean | null = null;      // this kernel's master, from its notifySwitches frame (null until the first)

/** The local kernel's switches frame: {type: "notifySwitches", all, turns}. */
export function setHubSwitches(m: { all?: unknown }): boolean {
  const next = m && m.all === true;
  const moved = hubAll !== next;
  hubAll = next;
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
