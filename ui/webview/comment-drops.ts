// A comment the kernel could not turn into a thread: a create parked for transcript lag that it dropped
// (_hand_back_dropped_create in kernel.py). Its err frame names the create (op commentCreate and its createId) and
// carries the words in its copy slot. Both panes that render an err frame, the chat page and the feed, draw drops here.
//
// ONE dialog lists every drop that arrives while it is up, each with its own Copy, and each is acknowledged to its kernel
// (commentDropSeen) only once its entry is on screen (review of PR 218, 2026-10-11). The kernel counts a drop as told only
// on that acknowledgement, and it offers a pane that starts listening every untold drop at once: with one dialog per
// frame, the second replaced the first, so one drop was drawn while both were acknowledged. A drop this page drew already
// (its acknowledgement lost with a socket, so the kernel offered it again) is acknowledged again and never drawn twice.
// A page from before the acknowledgement never sends one, so the kernel offers it the same drop at each reconnect until
// that page reloads onto a current bundle; it is drawn once per reload there.

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
  /** Is the dialog this page opened last still on screen? A newer dialog replaces it, as any dialog on the page does,
   *  and Dismiss closes it; either way the next drop opens a fresh one. */
  isOpen(): boolean;
  /** Open a fresh dialog titled `title`, listing nothing yet. */
  open(title: string): void;
  /** Add drop `d`'s entry (cmtDropEntry) to the open dialog. */
  add(d: CmtDrop): void;
  /** Retitle the open dialog. */
  retitle(title: string): void;
  /** Tell drop `d`'s kernel this page drew it (commentDropSeen; the sid routes it to a remote host's kernel). */
  ack(d: CmtDrop): void;
  /** File drop `d`'s "not sent" entry in the Log. */
  log(d: CmtDrop): void;
}

export class CmtDrops {
  private readonly drawn = new Set<string>();   // "<sid> <createId>" of every drop this page drew
  private listed = 0;                            // entries in the open dialog
  constructor(private readonly view: CmtDropView) {}

  /** Take an err frame: a drop is drawn (unless this page drew it already) and then acknowledged, and the answer is true;
   *  any other frame is left to the page's own err dialog (false). */
  take(m: any): boolean {
    const d = cmtDropOf(m);
    if (!d) return false;
    const key = d.sid + " " + d.createId;
    if (!this.drawn.has(key)) {
      if (!this.view.isOpen()) { this.view.open(d.title); this.listed = 0; }
      this.view.add(d);
      if (++this.listed === 2) this.view.retitle(CMT_DROPS_TITLE);
      this.drawn.add(key);
      this.view.log(d);
    }
    this.view.ack(d);                            // after the entry is on screen: an add that threw acknowledges nothing
    return true;
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
