// A SHORT HIGHLIGHT names its words and quotes the sentence they sit in (the user 2026-10-14, who approved the wording).
// A quote of one word ("placement") says nothing about WHERE it was: the reply or comment it rides with reached the session
// as "> placement" alone. So a highlight shorter than a sentence goes out as the words, named in the lead-in, and the
// sentence around them, read from the rendered message the selection was made in:
//
//   Replying to "placement" in this part of the conversation:
//   > …we should revisit the placement of the legend before shipping the deck…
//
// A highlight of a whole sentence or more goes out as it always did. Pure: render.ts reads the message's rendered text and
// the selection's offsets in it from the DOM and hands them here; the kernel never reconstructs any of this.

/** The longest stretch of a sentence quoted around the words; a longer sentence is cut to a window around them. */
export const QUOTE_CONTEXT_CAP = 280;

// a sentence's end: its stop, then any closing quote or bracket, then whitespace (a stop inside "3.5" or "config.py" is not)
const STOP = /[.!?…]+["'”’)\]»]*(?=\s)/g;
// a stop that ends one of these is not the end of a sentence
const ABBREV = /(?:^|[\s(])(?:e\.g|i\.e|vs|cf)\.$/i;

/** The context line for the highlight at [start, end) of `prose` (a message's rendered text, its lines and blocks
 *  separated by newlines): the sentence the words sit in, a "…" where the line goes on before or after it (the sentence's
 *  own closing period gives way to it), cut to a window of about QUOTE_CONTEXT_CAP characters around the words when the
 *  sentence is longer, and the words in **bold** when they occur more than once in what is quoted, so the right one is
 *  plain. Null when the words are a whole sentence or more, run over a line break, or are not `words` (the quote the
 *  selection gave, when the caller passes it): the quote then goes out as it always did. */
export function quoteContext(prose: string, start: number, end: number, words?: string): string | null {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > prose.length || start >= end) return null;
  while (start < end && /\s/.test(prose[start])) start++;
  while (end > start && /\s/.test(prose[end - 1])) end--;
  if (start >= end) return null;
  const said = prose.slice(start, end);
  if (words !== undefined && said !== words.trim()) return null;
  if (said.includes("\n")) return null;
  // the line the words sit on: a paragraph, a list item, a heading, a table cell, a line of code
  const lineStart = prose.lastIndexOf("\n", start - 1) + 1;
  const nl = prose.indexOf("\n", end);
  const line = prose.slice(lineStart, nl < 0 ? prose.length : nl);
  const s = start - lineStart, e = end - lineStart;
  // the sentence: after the last stop that ends before the words, through the first that ends at or after them
  let a = 0, b = line.length;
  for (const m of line.matchAll(STOP)) {
    const stop = m.index! + m[0].length;
    if (ABBREV.test(line.slice(0, m.index! + 1))) continue;
    if (stop <= s) { a = stop; continue; }
    if (stop < e) return null;                         // the words run on into the next sentence
    b = stop;
    break;
  }
  while (a < s && /\s/.test(line[a])) a++;
  while (b > e && /\s/.test(line[b - 1])) b--;
  // the whole sentence, give or take its opening and closing punctuation: no context to add
  const core0 = a + (/^["'“‘(\[«]*/.exec(line.slice(a, b))![0].length);
  const core1 = b - (/[.!?…:;,"'”’)\]»]*$/.exec(line.slice(a, b))![0].length);
  if (s <= core0 && e >= core1) return null;
  // a long sentence: a window around the words, cut at word boundaries
  if (b - a > QUOTE_CONTEXT_CAP) {
    const room = Math.max(0, QUOTE_CONTEXT_CAP - (e - s));
    let wa = Math.max(a, s - Math.floor(room / 2));
    const wb = Math.min(b, e + room - (s - wa));
    wa = Math.max(a, s - (room - (wb - e)));
    let na = wa, nb = wb;
    if (na > a) { while (na < s && !/\s/.test(line[na - 1])) na++; while (na < s && /\s/.test(line[na])) na++; }
    if (nb < b) { while (nb > e && !/\s/.test(line[nb])) nb--; while (nb > e && /\s/.test(line[nb - 1])) nb--; }
    a = na; b = nb;
  }
  const before = line.slice(0, a).trim() !== "";
  const after = line.slice(b).trim() !== "";
  let text = line.slice(a, b);
  if (after && b > e && /[^.]\.$/.test(text)) text = text.slice(0, -1);   // "deck." + "…" reads "deck…"
  // the words twice or more in what is quoted (any case): the highlighted one in bold
  const lower = text.toLowerCase(), w = said.toLowerCase();
  let n = 0;
  for (let i = lower.indexOf(w); i >= 0 && n < 2; i = lower.indexOf(w, i + 1)) n++;
  if (n > 1) text = text.slice(0, s - a) + "**" + said + "**" + text.slice(e - a);
  return (before ? "…" : "") + text + (after ? "…" : "");
}
