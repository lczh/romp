// A queued message RESCINDED to the composer (T373, the user 2026-09-12): the queued bubble's edit does not edit in
// place; it pulls the message back into the message box, where it can be changed and sent again, and takes it out of
// the queue. What comes back is what went out: the typed words, the quote citations as chips again (the outgoing body
// wrote them as quote sections ahead of the text, quoteReplyBody), and the attachments as chips again (the outgoing
// text carried their paths as a trailing line, quoted when they contain spaces). Pure: the DOM half lives in
// render.ts and executes this.

export interface RescindCite { quote: string; src?: string; kind?: "file"; path?: string; lines?: [number, number] | null; ctx?: string }
export interface RescindedState { text: string; cites: RescindCite[]; files: string[] }

const LEAD_CONVERSATION = "Replying to this part of the conversation:";
// a passage shorter than a sentence (staged-messages.ts quoteLeadIn, 2026-10-14): its words in the lead, its sentence quoted
const LEAD_WORDS = /^Replying to "(.+)" in this part of the conversation:$/;
const LEAD_CODE = /^Replying to this highlighted code \((.+)\):$/;
// a passage of a file the viewer showed (staged-messages.ts quoteLeadIn, 2026-09-30): its path, and its lines when it had them
const LEAD_FILE = /^Replying to this part of (.+?)(?: \(lines? (\d+)(?:\u2013(\d+))?\))?:$/;
const MAX_SECTIONS = 64;   // a body carries at most a strip's worth of citations; the parse is bounded by it

/** The inverse of quoteReplyBody: leading quote sections back into citations, the rest is the text. A body that
 *  starts with no quote section is text alone. Accepted and documented (the fold's low): a message the user typed
 *  whose FIRST line is the send's own lead sentence followed by quoted lines reads as a citation here and comes back
 *  as a chip; re-sent, it composes to the same body but for one blank line. The lead is a sentence of romp's own
 *  making that a person has no reason to type, so the inverse trusts it rather than marking the send. */
export function splitQuoteReplyBody(body: string): { cites: RescindCite[]; text: string } {
  const cites: RescindCite[] = [];
  let rest = body;
  for (let n = 0; n < MAX_SECTIONS; n++) {
    const lines = rest.split("\n");
    const lead = lines[0] || "";
    const code = LEAD_CODE.exec(lead);
    const words = LEAD_WORDS.exec(lead);
    const file = lead === LEAD_CONVERSATION || words ? null : LEAD_FILE.exec(lead);
    if (lead !== LEAD_CONVERSATION && !code && !file && !words) break;
    let i = 1;
    const q: string[] = [];
    while (i < lines.length && (lines[i].startsWith("> ") || lines[i] === ">")) { q.push(lines[i] === ">" ? "" : lines[i].slice(2)); i++; }
    if (!q.length) break;
    const cite: RescindCite = words ? { quote: words[1], ctx: q.join("\n") } : { quote: q.join("\n") };
    if (code) cite.src = code[1];
    else if (file) {
      const a = file[2] ? Number(file[2]) : 0, b = file[3] ? Number(file[3]) : a;
      cite.kind = "file"; cite.path = file[1]; cite.lines = a > 0 ? [a, b] : null;
      cite.src = file[1] + (a > 0 ? ":" + a + (b > a ? "-" + b : "") : "");   // the chip's label, as the viewer made it
    }
    cites.push(cite);
    while (i < lines.length && lines[i] === "") i++;   // the blank line between sections, or before the text
    rest = lines.slice(i).join("\n");
  }
  return { cites, text: rest };
}

/** The trailing paths line off the text when the send wrote one, by the RECORD alone: `known` is the attachment list
 *  the page's own pending entry kept or the kernel shipped on the queued copy (every attachment, images and documents
 *  alike). No record, no guess (the fold's low: a line of the user's own URLs or paths read as attachments): the line
 *  stays text. */
export function splitTrailingPaths(text: string, known?: readonly string[] | null): { text: string; files: string[] } {
  if (!known || !known.length) return { text, files: [] };
  const lines = text.split("\n");
  const last = lines[lines.length - 1] || "";
  const want = known.map((p) => (/\s/.test(p) ? '"' + p + '"' : p)).join(" ");
  if (last === want) return { text: lines.slice(0, -1).join("\n"), files: [...known] };
  return { text, files: [] };
}

/** The composer state a rescinded message comes back as: text, quote citations, attachment paths. */
export function rescindedComposerState(md: string, known?: readonly string[] | null): RescindedState {
  const { text: body, files } = splitTrailingPaths(md, known);
  const { cites, text } = splitQuoteReplyBody(body);
  return { text, cites, files };
}
