// The chat pane's hold on the dashboard's self-reload (T272 follow-up, the manager's review 2026-09-08): a reload takes
// an upload in flight's bytes and a held send's release with it, so the pane reports itself busy while either is
// pending — but ONLY while the ack can still arrive. A ship to a host whose relay is down, or to a host no longer
// attached, has no ack coming until that host returns; holding every restart and build reload for it would stop the
// dashboard from ever reloading, silently. So the hold counts ships whose host is the local kernel or an attached,
// reachable host, and a held reload wears a face on the notification center or a standalone pane's bar, drawn by the
// kernel's two held maps (the shim's and the shell's; tests/test_dashboard_auto_reload.py runs both). Pure and DOM-free so node --test executes it.

export type FedView = { hosts?: () => string[]; down?: () => string[] } | null | undefined;

/** The host prefix of a sid ("" for the local kernel). */
const hostOfSid = (sid: string): string => { const i = sid.indexOf(":"); return i > 0 ? sid.slice(0, i) : ""; };

/** Whether an answer from `host` ("" for the local kernel) can still arrive: local, or a host the federation manager
 *  lists as attached and not down. No manager loaded (a single-kernel page): every host can. */
export function hostHoldsReload(host: string, fed: FedView): boolean {
  if (!host) return true;
  if (!fed) return true;
  try {
    if (typeof fed.hosts === "function" && fed.hosts().indexOf(host) < 0) return false;   // detached or removed: gone
    if (typeof fed.down === "function" && fed.down().indexOf(host) >= 0) return false;   // its relay is down
  } catch { return true; }
  return true;
}

/** Whether a pending ship for `sid` still has an ack coming (hostHoldsReload, for the sid's host). */
export function shipHoldsReload(sid: string, fed: FedView): boolean {
  return hostHoldsReload(hostOfSid(sid), fed);
}

/** The pane's busy reason for the reload core, from what is pending: "upload" while a ship whose ack can still
 *  arrive awaits it, "held-send" while a send is held on such a ship, or while a create in flight holds sends made on its
 *  tab (`createHost`, the create's host, null with none: render.ts createHoldHost) and its session can still arrive;
 *  "" otherwise. The core's notice for "held-send" says the dashboard reloads once the held message has been sent, which
 *  the create's sends are, on its session's arrival (2026-10-23). */
export function reloadHoldReason(shipSids: string[], gateSid: string | null, fed: FedView, createHost: string | null = null): string {
  const live = shipSids.filter((sid) => shipHoldsReload(sid, fed));
  if (live.length) return "upload";
  if (gateSid && shipHoldsReload(gateSid, fed)) return "held-send";
  if (createHost !== null && hostHoldsReload(createHost, fed)) return "held-send";
  return "";
}
