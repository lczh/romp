// A comment thread's READ, held from the moment the reader opens it until the kernel's frame shows it (2026-10-15).
// Opening a thread reads it: the page clears its unread mark at once and tells the kernel (commentSeen), whose read
// watermark then turns the frame's `unread` bit off. A comments frame the kernel built before it recorded that read was
// already on its way, and it still said unread: with the popover closed inside one round trip, that frame put the mark
// back, so the jump cluster's badge read 3, 2, 3, 2 with one thread read once (found in the review of #245; a linked
// machine's relay makes the window longer). The read now LATCHES until the deciding event, never on a clock: the kernel
// counts every read it records (`seenSeq` on the thread's row, carried by every frame) and answers each read with the
// count it recorded it at (commentSeenAck), and a frame whose count is below that one predates the read, so its unread
// bit cannot undo it. A frame at or past it is the kernel's word, a reply that landed after the read included.
// Two other events end the hold: the kernel refusing the read (commentSeenFailed: the page shows the frame's own bit
// again and says why), and the link the read went out on dropping before the kernel answered, when the read may never
// have arrived: the first frame after the link is back is the truth either way (a fresh frame, built after the read
// if the read got there), so it decides. A kernel that answers no read (no "seenAck" in its frame's caps) gets no latch.
// This module is the PURE half (node-tested); render.ts keeps the map and the events (readThread, the comments frame).

/** One held read. `rid` names the newest read the page sent for the thread, the id the kernel's answer echoes; `seq` is
 *  the count the kernel recorded it at (its answer), null until it answers; `raw` is the newest frame's own unread bit
 *  for the thread, shown again when a refusal ends the hold; `lost` is 1 once the link the unanswered read went out on
 *  dropped, and 2 once a link to that kernel is back. */
export type ReadLatch = { sid: string; tid: string; rid: number; seq: number | null; raw: boolean; lost: 0 | 1 | 2 };

/** The latch map's key: a session (host-prefixed for a linked machine's) and one of its threads. */
export function readKey(sid: string, tid: string): string {
  return sid + "\u001f" + tid;
}

/** A new hold for a read the page is sending now. `raw`: the frame's own bit it holds as read (a held read's, when the
 *  thread is read again before the kernel answered the first). */
export function holdRead(sid: string, tid: string, rid: number, raw: boolean): ReadLatch {
  return { sid, tid, rid, seq: null, raw, lost: 0 };
}

/** What a frame's row says for a held thread: its own unread bit once the frame shows the kernel recorded the read (its
 *  `seenSeq` at or past the count the kernel answered with), or once a link came back after the read went unanswered
 *  (lost 2: the frame was built on the new link, so it says whether the read arrived); read otherwise. `done`: the hold
 *  is over. A frame from a kernel that sends no count reads 0, below every answer: such a kernel never gets a latch. */
export function heldUnread(l: ReadLatch, row: { unread?: boolean; seenSeq?: number }): { unread: boolean; done: boolean } {
  if (l.seq !== null) return (Number(row.seenSeq) || 0) >= l.seq ? { unread: !!row.unread, done: true } : { unread: false, done: false };
  if (l.lost === 2) return { unread: !!row.unread, done: true };
  return { unread: false, done: false };
}

/** The kernel answered read `rid` with the count it recorded it at. False when the answer is for a read the page has
 *  since superseded (a newer read of the same thread is out; its own answer decides). */
export function answerRead(l: ReadLatch, rid: unknown, seq: unknown): boolean {
  if (rid !== l.rid || typeof seq !== "number") return false;
  l.seq = seq;
  l.lost = 0;                                  // answered: the read arrived, whatever the link did since
  return true;
}

/** The link to `host` ("" for this page's own kernel) went down (`up` false) or came back (`up` true): every read on that
 *  host the kernel has not answered may be lost, and the first frame after the link is back decides it. */
export function linkEdge(latches: Iterable<ReadLatch>, host: string, up: boolean, hostOf: (sid: string) => string): void {
  for (const l of latches) {
    if (l.seq !== null || hostOf(l.sid) !== host) continue;
    if (!up) l.lost = 1;
    else if (l.lost === 1) l.lost = 2;
  }
}
