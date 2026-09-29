// WHO POSTED A WINDOW MESSAGE. A pane's window `message` listener hears every window that can post to it, not only
// romp's own. windowSender names the sender of one message, so a listener can act on a message only from the senders
// that legitimately post it:
//   dispatch  no source and no origin: an event this document's own script dispatched (the pane shim's and
//             federation.js's kernel frames, each a new MessageEvent("message", { data }), whose origin is the empty
//             string), or a handler called directly with a plain object. A post from another window names that
//             window's origin, and it keeps it when it arrives with no source, so a sourceless message that carries
//             an origin is judged by the origin rules below, never counted as this document's own.
//   self      this window posted it: a same-document post, such as the file viewer mounted in the chat's document.
//   embedder  this window's parent posted it: the romp shell (same origin, since the kernel serves its pages with
//             frame-ancestors 'self').
//   peer      another window on this document's own origin, such as a second chat column, or the VS Code webview host.
//             VS Code's script in the webview's frame replaces window.parent with the frame itself (older releases
//             delete it), so window.parent never refers to the host that forwards the extension's messages; the host
//             posts from its own window on the webview's origin. A same-origin window can already reach this
//             document's script directly, so its posts carry no authority it lacks.
//   foreign   anything else, such as a sandboxed iframe (its origin is "null", which never matches, even when this
//             document's own origin is "null").
// A listener that acts on a message from a foreign sender acts for a page romp does not control; see the chat's frame
// handler in render.ts, which ignores every message from a foreign sender.
export type WindowSender = "self" | "embedder" | "dispatch" | "peer" | "foreign";

export function windowSender(e: { source?: unknown; origin?: unknown } | null | undefined,
                             w: { parent?: unknown; location?: { origin?: string } } = window): WindowSender {
  if (!e) return "foreign";
  const src = e.source;
  if (src === null || src === undefined) {
    if (e.origin === undefined || e.origin === null || e.origin === "") return "dispatch";
  } else {
    if (src === w) return "self";
    if (w.parent && w.parent !== w && src === w.parent) return "embedder";
  }
  const own = w.location && w.location.origin;
  if (typeof own === "string" && own !== "null" && e.origin === own) return "peer";
  return "foreign";
}

// A REFUSED MESSAGE LEAVES A TRACE (2026-09-29). Every listener that drops a foreign sender's message calls noteForeign
// at the drop, as `if (windowSender(e) === "foreign") { noteForeign("<site>", e, <transport>); return; }`. Nothing
// legitimate is refused today, but a host that changed shape (a sender windowSender stopped hearing) would otherwise
// leave a pane or a VS Code panel quiet with nothing to say why. noteForeign files ONE client-diag row per site and
// sender origin a page life (surface "window-sender", what "foreign", data { site, origin }): the site's name and the
// origin the browser stamped on the post, never the message's data. The row goes through the pane shim's send
// (window.__rompLocalSend, on a kernel page) or else the transport the caller passes (the VS Code api, or a poster that
// reaches it), and it never throws. At most NOTE_MAX rows a page life: a page framing many origins cannot flood the
// journal, and the first rows already name what is refused. While a kernel page's socket is down, the pane shim queues
// these rows under a cap of their own (kernel.py _shim, FOREIGN_QUEUE_MAX, equal to NOTE_MAX), so a foreign page posting
// during an outage never takes a queued slot from the page's own rows (pane-shim-stale.test.ts runs it). The timeline
// boot (kernel.py _TIMELINE_BOOT) inlines the same beside heardSender. Kept import-free, like windowSender.
export const NOTE_MAX = 20;
export type NoteTransport = ((m: Record<string, unknown>) => unknown) | { postMessage(m: unknown): unknown } | null | undefined;
const noted = new WeakMap<object, Set<string>>();   // per receiving window: the site and origin pairs it has filed

export function noteForeign(site: string, e: { origin?: unknown } | null | undefined, post?: NoteTransport,
                            w?: object): void {   // w: the receiving window, the page life a row is filed once in
  try {
    const win: any = w || (typeof window !== "undefined" ? window : null);
    if (!win) return;
    let seen = noted.get(win);
    if (!seen) { seen = new Set(); noted.set(win, seen); }
    const o = e ? e.origin : undefined;
    const origin = typeof o === "string" ? o : String(o);
    const key = site + " " + origin;
    if (seen.has(key) || seen.size >= NOTE_MAX) return;
    seen.add(key);
    const row = { type: "clientDiag", surface: "window-sender", what: "foreign", data: { site, origin } };
    const send = win.__rompLocalSend;
    if (typeof send === "function") send(row);
    else if (typeof post === "function") post(row);
    else if (post && typeof post.postMessage === "function") post.postMessage(row);
  } catch { /* a trace is never worth the listener */ }
}
