// A comment the kernel could not turn into a thread: a create parked for transcript lag that it dropped
// (_hand_back_dropped_create in kernel.py). Its err frame names the create (op commentCreate and its createId) and
// carries the words in its copy slot. Both panes that render an err frame, the chat page and the feed, draw drops here.
//
// ONE dialog lists every drop that arrives while it is up, each with its own Copy (review of PR 218, 2026-10-11): the
// kernel offers a pane that starts listening every untold drop at once, and with one dialog per frame the second replaced
// the first. A drop this page drew already (its acknowledgement lost with a socket, so the kernel offered it again) is
// never drawn twice.
//
// Each drop is acknowledged to its kernel (commentDropSeen) only once SOMEONE SAW IT (review of PR 223, 2026-10-11): the
// kernel counts a drop as told on that acknowledgement and offers it to no pane after, so a page that acknowledged a drop
// once its entry was in the document, visible or not, took it off every page the user was looking at (a background tab or
// a hidden pane saying ready first after a restart). The page's sight (paint-gate.ts paneSight) decides:
// - "hidden" (the tab is in the background, or the pane is off screen): the drop is not drawn; it is handed back
//   (commentDropHidden, hidden), so the kernel offers it to a pane someone can see, and asked for again (hidden false) once
//   this page can be seen, which re-offers it here unless another pane holds it;
// - "shown" (on screen, but the page does not hold the focus): drawn, and acknowledged once the page is focused or the user
//   dismisses the dialog;
// - "seen" (on screen and focused): drawn and acknowledged.
// The page calls settle() on the events that change its sight (focus, visibilitychange, a resize, its visibility
// observer's report), and dismissed() on the user's own dismissal of the dialog, the only way the dialog leaves the screen:
// a newer dialog of another kind waits behind it (render.ts showConfirm's `pinned`, feed.ts showErrDialog).
// A page from before the acknowledgement never sends one, so the kernel offers it the same drop at each reconnect until
// that page reloads onto a current bundle; it is drawn once per reload there.
import type { Sight } from "./paint-gate";

export interface CmtDrop { sid: string; createId: string; title: string; text: string; copy: string }

/** The drop an err frame names, or null for any other frame (a remote kernel from before createId rode the frame sends
 *  none, and its frame stays an ordinary err dialog). */
export function cmtDropOf(m: any): CmtDrop | null {
  if (!m || m.type !== "err" || m.op !== "commentCreate") return null;
  if (typeof m.createId !== "string" || !m.createId || typeof m.sid !== "string" || !m.sid) return null;
  if (typeof m.text !== "string" || !m.text) return null;
  return { sid: m.sid, createId: m.createId, text: m.text, copy: typeof m.copy === "string" ? m.copy : "",
           title: typeof m.title === "string" && m.title ? m.title : "That comment did not start a thread" };
}

/** The dialog's title once it lists more than one drop (the first drop's own title until then). */
export const CMT_DROPS_TITLE = "Those comments did not start threads";

/** What the dialog needs from its page. */
export interface CmtDropView {
  /** Is the dialog this page opened last still on screen? Only the user's dismissal closes it (dismissed()); the next drop
   *  then opens a fresh one. */
  isOpen(): boolean;
  /** Open a fresh dialog titled `title`, listing nothing yet. */
  open(title: string): void;
  /** Add drop `d`'s entry (cmtDropEntry) to the open dialog. */
  add(d: CmtDrop): void;
  /** Retitle the open dialog. */
  retitle(title: string): void;
  /** Can anyone see this page now (paint-gate.ts paneSight)? */
  sight(): Sight;
  /** Tell drop `d`'s kernel someone saw its dialog here (commentDropSeen; the sid routes it to a remote host's kernel). */
  ack(d: CmtDrop): void;
  /** Tell drop `d`'s kernel whether anyone can see this page (commentDropHidden): `hidden` hands the drop back, so it goes
   *  to a pane someone can see; not hidden asks for it again. */
  hidden(d: CmtDrop, hidden: boolean): void;
  /** File drop `d`'s "not sent" entry in the Log. */
  log(d: CmtDrop): void;
}

const keyOf = (d: CmtDrop) => d.sid + " " + d.createId;

export class CmtDrops {
  private readonly acked = new Set<string>();            // "<sid> <createId>" of every drop someone saw on this page
  private readonly listed = new Map<string, CmtDrop>();  // drawn in the open dialog, nobody seen it yet
  private readonly handed = new Map<string, CmtDrop>();  // handed back while nobody could see this page
  private readonly logged = new Set<string>();           // filed in the Log (once per page)
  private entries = 0;                                   // entries in the open dialog
  constructor(private readonly view: CmtDropView) {}

  /** Take an err frame: a drop is drawn (unless this page drew it already, or nobody can see the page, which hands it
   *  back) and acknowledged once someone sees it, and the answer is true; any other frame is left to the page's own err
   *  dialog (false). */
  take(m: any): boolean {
    const d = cmtDropOf(m);
    if (!d) return false;
    const key = keyOf(d);
    if (this.acked.has(key)) { this.view.ack(d); return true; }   // offered again: its acknowledgement was lost with a socket
    if (!this.view.isOpen()) this.listed.clear();
    if (!this.listed.has(key)) {
      if (this.view.sight() === "hidden") {      // nobody can see it here: it goes to a pane someone can see
        this.handed.set(key, d);
        this.view.hidden(d, true);
        return true;
      }
      this.handed.delete(key);
      if (!this.view.isOpen()) { this.view.open(d.title); this.entries = 0; }
      this.view.add(d);
      if (++this.entries === 2) this.view.retitle(CMT_DROPS_TITLE);
      this.listed.set(key, d);                   // after the entry is on the page: an add that threw lists nothing
      if (!this.logged.has(key)) { this.logged.add(key); this.view.log(d); }
    }
    this.settle();
    return true;
  }

  /** The page's sight may have changed: the drops in the open dialog are acknowledged once someone sees them, and the
   *  drops handed back are asked for again once someone can. */
  settle(): void {
    const sight = this.view.sight();
    if (sight === "seen" && this.view.isOpen()) for (const d of [...this.listed.values()]) this.ackOne(d);
    if (sight !== "hidden") for (const d of [...this.handed.values()]) { this.handed.delete(keyOf(d)); this.view.hidden(d, false); }
  }

  /** The user dismissed the dialog: every drop it listed was seen. */
  dismissed(): void {
    for (const d of [...this.listed.values()]) this.ackOne(d);
  }

  private ackOne(d: CmtDrop): void {
    const key = keyOf(d);
    this.listed.delete(key);
    this.acked.add(key);
    this.view.ack(d);
  }
}

/** One drop's entry: the kernel's account of it and, when it carries words, a line showing them (clipped to the line,
 *  the whole text on hover) with its own Copy, which says "Copied" once clicked. `make` is the page's element builder,
 *  `cls` its dialog's classes for prose and for a button, `copy` its clipboard write. */
export function cmtDropEntry(d: CmtDrop, make: (tag: string, cls?: string) => HTMLElement,
                             cls: { detail: string; button: string }, copy: (text: string) => void): HTMLElement {
  const entry = make("div", "cmt-drop");
  const said = make("div", cls.detail);
  said.textContent = d.text;
  entry.appendChild(said);
  if (d.copy) {
    const line = make("div", "cmt-drop-words");
    const words = make("span", "cmt-drop-quote");
    words.textContent = d.copy;
    words.title = d.copy;
    const btn = make("button", cls.button);
    btn.textContent = "Copy my text";
    btn.addEventListener("click", () => { copy(d.copy); btn.textContent = "Copied"; });
    line.appendChild(words);
    line.appendChild(btn);
    entry.appendChild(line);
  }
  return entry;
}
