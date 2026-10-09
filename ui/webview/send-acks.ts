// A chat send is the page's until a kernel ANSWERS it (2026-10-09). The decisions live here, pure, so send-acks.test.ts
// executes them; render.ts owns the per-session map, the socket events and the storage.
//
// Every composer send carries the id the press minted (`qid`, send-pending.ts mintQid). A kernel that speaks the
// protocol answers it on the socket that carried it with {type: "sendAck", id, qid, ok, dup} once it has decided the
// send's fate (kernel.py _send_ack): handed over or parked (ok), or refused (not ok, beside the refusal's own frame).
// Before this the page had no such answer, and two messages a user typed into a linked machine's session were lost
// without a trace: the kernel had not read them yet when their socket closed, and the reload that followed took the
// only copy of their words with it. So, until a send is answered:
//   - the page KEEPS it, across a reload too (keptSends / restoreKept, in the page's persisted state beside the drafts);
//   - when the socket to its kernel goes down, its bubble says "not confirmed" (the existing `lost` label);
//   - when a socket to that kernel opens and the kernel says on it that it recognises a resend (the keepalive's `acks`,
//     the event romp:hostAcks), the page posts the very message again (resendable). The kernel takes a send it has
//     already decided on as a duplicate and answers it again without delivering it twice;
//   - a kernel that never says so (one from before the protocol) is never sent anything twice: the send keeps its
//     "not confirmed" label, and its words stay on the bubble, where the ✎ puts them back in the box. So does a send
//     older than the kernel's memory of what it decided (RESEND_MAX_AGE_MS);
//   - the store is shared by every tab of the origin: each tab writes its own sends and keeps the others' (mergeKept),
//     and a send it withdraws or sees answered leaves the store with that write.
// An answer ends all of that: ok proves the kernel holds the send (`received`, as a copy of it seen in a frame does),
// not-ok is a refusal whose own frame has said why.

import { pendingBody, type PendingSend } from "./send-pending";
import { isProvisionalId } from "./provisional";

// How long the page keeps an unanswered send, how many it keeps, and how old a send may be and still be resent. The kernel
// remembers the sends it decided on for seven days (kernel.py _SEND_ACCEPTS_TTL_S): a resend older than that could be
// delivered a second time, so the page never resends past RESEND_MAX_AGE_MS, a day inside it. A kept send older than that
// still comes back on a reload, labelled "not confirmed" with its words on the bubble, until KEPT_MAX_AGE_MS drops it.
export const RESEND_MAX_AGE_MS = 6 * 86400_000;
export const KEPT_MAX_AGE_MS = 14 * 86400_000;
export const KEPT_MAX = 200;

/** One unanswered send as the page keeps it across a reload: enough to draw its bubble again and to resend it. */
export type KeptSend = {
  sid: string;
  text: string;
  qid: string;
  ts: number;
  wire: Record<string, unknown>;
  imgPaths?: string[];
  paths?: string[];
  clear?: boolean;
};

const QID_RE = /^echo:[0-9a-f]{32}$/;
const strs = (v: unknown): string[] | undefined =>
  Array.isArray(v) && v.every((x) => typeof x === "string") && v.length ? (v as string[]).slice() : undefined;

/** The wire a send without one was posted with: a composer's plain or quoted send posts exactly this (render.ts
 *  routeUserMessage: the body it shows, the id the press minted, the attachments its trailing line named). */
export function defaultWire(sid: string, p: PendingSend): Record<string, unknown> {
  return { type: "sendMessage", id: sid, text: p.text, qid: p.qid, ...(p.paths && p.paths.length ? { paths: p.paths.slice() } : {}) };
}

/** Whether the page still owes this send a delivery it can prove: no kernel has answered it, it has an id and a wire to
 *  post again, and no frame has shown the kernel holding it. */
export function unanswered(p: PendingSend): boolean {
  return !p.acked && !p.received && !!p.qid && !!p.wire;
}

/** Every unanswered send young enough to keep, in the shape the page keeps across a reload (render.ts persists it with the
 *  drafts). A send to a session still being created (a provisional id) is never kept: it was never posted, and the
 *  session's real send carries its own entry. */
export function keptSends(entries: Iterable<[string, PendingSend[]]>, now: number = Date.now()): KeptSend[] {
  const out: KeptSend[] = [];
  for (const [sid, list] of entries) {
    if (isProvisionalId(sid)) continue;
    for (const p of list) {
      if (!unanswered(p) || now - p.ts > KEPT_MAX_AGE_MS) continue;
      out.push({ sid, text: p.text, qid: p.qid as string, ts: p.ts, wire: { ...(p.wire as Record<string, unknown>) },
                 ...(p.imgPaths && p.imgPaths.length ? { imgPaths: p.imgPaths.slice() } : {}),
                 ...(p.paths && p.paths.length ? { paths: p.paths.slice() } : {}),
                 ...(p.clear ? { clear: true } : {}) });
    }
  }
  return out;
}

/** The kept sends a fresh page reads back, every row checked: a row that is not one (a corrupt or foreign value) is
 *  skipped, never trusted, and a wire whose id is not the row's own is not posted again. */
export function restoreKept(raw: unknown, now: number = Date.now()): KeptSend[] {
  if (!Array.isArray(raw)) return [];
  const out: KeptSend[] = [];
  const seen = new Set<string>();
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const w = o.wire as Record<string, unknown> | undefined;
    if (typeof o.sid !== "string" || !o.sid || isProvisionalId(o.sid) || typeof o.text !== "string" || typeof o.qid !== "string" || !QID_RE.test(o.qid)
        || typeof o.ts !== "number" || now - o.ts > KEPT_MAX_AGE_MS || !w || typeof w !== "object" || w.qid !== o.qid
        || (w.type !== "sendMessage" && w.type !== "askFollowUp") || seen.has(o.qid)) continue;
    seen.add(o.qid);
    out.push({ sid: o.sid, text: o.text, qid: o.qid, ts: o.ts, wire: { ...w },
               ...(strs(o.imgPaths) ? { imgPaths: strs(o.imgPaths) } : {}),
               ...(strs(o.paths) ? { paths: strs(o.paths) } : {}),
               ...(o.clear === true ? { clear: true } : {}) });
  }
  return out;
}

/** What the page writes back to the store every tab of this origin shares (render.ts persistKeptSends): its own unanswered
 *  sends, plus every stored send it has never held, so another tab's unanswered sends survive this tab's write. A send this
 *  page has held and no longer holds (answered, landed, withdrawn with the ✎ or the ✕) is gone from the store. A send to a
 *  host no longer attached is dropped (`hostGone`); the age and count caps hold, the newest kept. */
export function mergeKept(mine: KeptSend[], stored: KeptSend[], held: ReadonlySet<string>, now: number,
                          hostGone: (sid: string) => boolean = () => false): KeptSend[] {
  const byQid = new Map<string, KeptSend>();
  for (const k of stored) if (!held.has(k.qid)) byQid.set(k.qid, k);
  for (const k of mine) byQid.set(k.qid, k);
  const out = [...byQid.values()].filter((k) => now - k.ts <= KEPT_MAX_AGE_MS && !hostGone(k.sid));
  out.sort((a, b) => a.ts - b.ts);
  return out.slice(-KEPT_MAX);
}

/** A kept send as a pending entry again. It says "not confirmed" (`lost`) until a kernel answers it: the page that sent
 *  it is gone, and nothing yet says whether its kernel ever read it. `late`: the fresh page may hold no frame to anchor
 *  it to (send-pending.ts stampBase). */
export function pendingFromKept(k: KeptSend): PendingSend {
  return { text: k.text, body: pendingBody(k.text, k.imgPaths), ts: k.ts, qid: k.qid, wire: { ...k.wire }, lost: "reload", late: true,
           ...(k.imgPaths ? { imgPaths: k.imgPaths.slice() } : {}), ...(k.paths ? { paths: k.paths.slice() } : {}),
           ...(k.clear ? { clear: true } : {}) };
}

/** The sends to post again on a socket to `host` whose kernel said it recognises a resend: every unanswered send to one
 *  of that host's sessions not already posted on this socket (`gen` counts the host's sockets, so a send is resent once
 *  per socket). `hostOf` maps a session id to its host ("" for the local kernel). */
export function resendable(entries: Iterable<[string, PendingSend[]]>, host: string, hostOf: (sid: string) => string,
                           resentOn: WeakMap<PendingSend, number>, gen: number, now: number = Date.now()): { sid: string; p: PendingSend }[] {
  const out: { sid: string; p: PendingSend }[] = [];
  for (const [sid, list] of entries) {
    if (hostOf(sid) !== host || isProvisionalId(sid)) continue;
    // older than the kernel's memory of what it decided: a resend could deliver it twice, so it keeps its label instead
    for (const p of list) if (unanswered(p) && resentOn.get(p) !== gen && now - p.ts <= RESEND_MAX_AGE_MS) out.push({ sid, p });
  }
  return out;
}

/** What a resend posts: the send's own wire, marked as a resend for the kernel's log and the page's journal. */
export function resendWire(p: PendingSend): Record<string, unknown> {
  return { ...(p.wire as Record<string, unknown>), resend: true };
}

/** Apply a kernel's answer to the session's pending sends. ok: the kernel holds the send, so it is `received` and its
 *  "not confirmed" label goes; not ok: refused there (its own frame said why), so it is never sent again and keeps its
 *  label. Returns the entry answered, or null when none wears the id (a push already retired it). */
export function applySendAck(list: PendingSend[], qid: string, ok: boolean): PendingSend | null {
  const p = list.find((x) => x.qid === qid);
  if (!p) return null;
  p.acked = true;
  if (ok) { p.received = true; p.lost = undefined; }
  else p.lost = p.lost || "refused";
  return p;
}

/** Mark the unanswered sends to `host`'s sessions "not confirmed": that socket went down, and none of them has been
 *  answered, so any may never have arrived. Returns the session ids whose entries changed. */
export function markHostLost(entries: Iterable<[string, PendingSend[]]>, host: string, hostOf: (sid: string) => string, why: string): string[] {
  const changed: string[] = [];
  for (const [sid, list] of entries) {
    if (hostOf(sid) !== host) continue;
    let any = false;
    for (const p of list) if (!p.acked && !p.received && !p.lost) { p.lost = why; any = true; }
    if (any) changed.push(sid);
  }
  return changed;
}
