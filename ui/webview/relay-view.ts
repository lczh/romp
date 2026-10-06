// The comment-thread RELAY as the chat shows it expanded (the user 2026-10-06). The kernel hands a thread's discussion
// back to its session as a TAGGED record (kernel _merge_body): its lead line, the passage in <passage>, then
// <discussion> holding one <message from="me"> or <message from="the other assistant"> per message, with a dim line
// where something was left out (the start could not be read; the middle was cut for length), then the closing ask.
// "Me:" / "Them:" prefixes were hard to pick out among the messages' own headings and lists, so the expanded bubble
// renders the record as a small transcript: the passage as a quote, each message as its own block labelled You or
// Thread with its markdown rendered inside, the notes as dim lines.
//
// Pure (no DOM), so the node tests execute it: parseRelay reads the record, relayHtml builds the expanded view through
// the caller's md(), and relayPlain is the fallback text for a body that does not parse, so a malformed record never
// shows raw tags. A relay from before the tags (Me:/Them:) does not parse and renders exactly as it always did.

/** The marker the kernel puts on every relay; the chat routes on it, never on the prose. */
export const RELAY_MARK_RE = /<!--\s*romp-tag:\s*relay\s*-->/;

export type RelayPart =
  | { kind: "msg"; who: "you" | "thread"; text: string }
  | { kind: "note"; text: string };

export interface Relay {
  lead: string;            // "I took a side discussion with another assistant about this passage of your earlier work:"
  passage: string | null;  // null when the relay carried none
  parts: RelayPart[];      // the discussion, in order: messages and notes
  close: string;           // the ask after the discussion
}

// who each <message from="…"> names, in the reader's terms: the person is You, the assistant they talked to is the Thread
const FROM: Record<string, "you" | "thread"> = { "me": "you", "the other assistant": "thread" };
export const RELAY_LABEL: Record<"you" | "thread", string> = { you: "You", thread: "Thread" };

// the whole record: a lead with no tags in it, an optional passage, the discussion, whatever follows it
const RECORD_RE = /^([^<]*?)\n*(?:<passage>\n([\s\S]*?)\n<\/passage>\n+)?<discussion>\n([\s\S]*?)\n<\/discussion>(?:\n([\s\S]*))?$/;
const MSG_RE = /<message from="([^"]*)">\n([\s\S]*?)\n<\/message>/g;
// any piece of the structure outside the places it may stand: the record is malformed
const STRAY_TAG_RE = /<\/?\s*(?:message|discussion|passage)\b/i;
// a closing tag of the structure, the form the kernel escapes inside content
const CLOSE_TAG_RE = /<\/\s*(?:message|discussion|passage)\s*>/i;
// the structural tags as whole lines, for the fallback: never shown raw
const TAG_LINE_RE = /^[ \t]*<\/?(?:passage|discussion)>[ \t]*$|^[ \t]*<message from="[^"]*">[ \t]*$|^[ \t]*<\/message>[ \t]*$/gm;

export function isRelay(md: string | null | undefined): boolean {
  return !!md && RELAY_MARK_RE.test(md);
}

/** The relay record in `text` (its <!-- --> markers already stripped), or null when it is not one: a relay from before
 *  the tags, or a record whose structure does not hold. */
export function parseRelay(text: string): Relay | null {
  const m = RECORD_RE.exec(text.replace(/\r\n/g, "\n").trim());
  if (!m) return null;
  const [, lead, passage, disc, close] = m;
  const parts: RelayPart[] = [];
  const notes = (chunk: string): boolean => {
    for (const ln of chunk.split("\n").map((l) => l.trim()).filter(Boolean)) {
      if (STRAY_TAG_RE.test(ln)) return false;
      parts.push({ kind: "note", text: ln });
    }
    return true;
  };
  let at = 0;
  for (const mm of disc.matchAll(MSG_RE)) {
    const who = FROM[mm[1]];
    if (!who || !notes(disc.slice(at, mm.index))) return null;
    parts.push({ kind: "msg", who, text: mm[2] });
    at = (mm.index || 0) + mm[0].length;
  }
  if (!notes(disc.slice(at)) || !parts.some((p) => p.kind === "msg")) return null;
  // the content's closing tags arrive escaped (kernel _merge_escape), so a raw one inside means the record did not hold
  if ([passage || "", ...parts.map((p) => (p.kind === "msg" ? p.text : ""))].some((t) => CLOSE_TAG_RE.test(t))) return null;
  return { lead: lead.trim(), passage: passage === undefined ? null : passage, parts, close: (close || "").trim() };
}

function esc(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// A closing tag of the structure inside the content arrives escaped (&lt;/message&gt;). Prose shows the entity as the
// tag, but code shows its source, so the code's twice-escaped form becomes the entity here: the person reads the
// message as it was written, in both places.
function shown(html: string): string {
  return html.replace(/&amp;lt;\/(\s*(?:message|discussion|passage)\s*)&amp;gt;/gi, "&lt;/$1&gt;");
}

/** The expanded view of a parsed relay as HTML, every piece of content through `md` (the chat's sanitizing renderer);
 *  the labels and the classes are the module's own. */
export function relayHtml(r: Relay, md: (s: string) => string): string {
  const out: string[] = [];
  if (r.lead) out.push(`<div class="relay-lead">${md(r.lead)}</div>`);
  if (r.passage !== null) out.push(`<blockquote class="relay-passage">${shown(md(r.passage))}</blockquote>`);
  out.push(`<div class="relay-disc">`);
  for (const p of r.parts) {
    if (p.kind === "note") out.push(`<div class="relay-note">${esc(p.text)}</div>`);
    else out.push(`<div class="relay-msg relay-${p.who}"><div class="relay-who">${RELAY_LABEL[p.who]}</div>`
                  + `<div class="relay-body">${shown(md(p.text))}</div></div>`);
  }
  out.push(`</div>`);
  if (r.close) out.push(`<div class="relay-close">${md(r.close)}</div>`);
  return out.join("");
}

/** The text a relay that does not parse renders as plain markdown: its structural tag lines lifted out, so a malformed
 *  record never shows raw tags. A relay from before the tags carries none, and passes through unchanged. */
export function relayPlain(text: string): string {
  return text.replace(TAG_LINE_RE, "");
}
