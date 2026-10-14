// A SHORT HIGHLIGHT names its words and quotes the sentence they sit in (the user 2026-10-14, who approved the wording).
// A quote of one word ("placement") says nothing about WHERE it was: the reply or comment it rides with reached the session
// as "> placement" alone. So a highlight of a few words, shorter than its sentence, goes out as the words, named in the
// lead-in, and the sentence around them, read from the rendered message the selection was made in:
//
//   Replying to "placement" in this part of the conversation:
//   > …we should revisit the placement of the legend before shipping the deck…
//
// Code is not prose (review of #229, 2026-10-14): a few words of a line of code are named over that LINE, never a
// sentence, and never marked in bold, which is an operator in most languages. A longer highlight, a whole sentence or
// line, goes out as it always did; one over several lines of a code block keeps its line breaks (render.ts reads them).
// Pure: render.ts reads the message's text and the selection's offsets from the DOM (controls such as a code block's Copy
// button left out) and hands them here; the kernel never reconstructs any of this.

/** The longest stretch quoted around the words; a longer sentence or line is cut to a window around them. */
export const QUOTE_CONTEXT_CAP = 280;
/** "A few words": a longer highlight goes out as it always did, so the lead-in never repeats a long selection. */
export const QUOTE_WORDS_MAX = 100;

// a sentence's end: its stop (a run of . ! ? or an ellipsis), any closing quote or bracket, then whitespace
const STOP = /(?:[.!?]|…)+["'”’)\]»]*\s+/g;
// a period after one of these is part of the word, not a sentence's end ("Dr. Shaw", "St. Louis", "et al. found")
const TITLES = new Set(["mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "cf", "fig", "approx", "al"]);
const ACRONYM = /(?:^|[^\p{L}.])(?:(?:\p{L}\.)+\p{L}?|\p{Lu})$/u;   // what precedes the period: "U.S", "e.g", "i.e", an initial "J"
const LOWER = /\p{Ll}/u;

const norm = (s: string): string => s.replace(/\s+/g, " ").trim();

/** Whether the stop STOP matched at `m` ends a sentence in `line`: what follows starts like a sentence (not a lowercase
 *  word: "etc. and", "e.g. seconds", a "…" that trails into the rest of the sentence), and a lone period is not part of an
 *  abbreviation, an initial or an acronym. */
function endsSentence(line: string, m: RegExpMatchArray): boolean {
  const next = line[m.index! + m[0].length];
  if (next === undefined || LOWER.test(next)) return false;
  const stop = /^(?:[.!?]|…)+/.exec(m[0])![0];
  if (stop !== ".") return true;
  const before = line.slice(0, m.index!);
  const word = (/[\p{L}]+$/u.exec(before) || [""])[0].toLowerCase();
  if (TITLES.has(word)) return false;
  return !ACRONYM.test(before);
}

/** The "…" joins: a cut before the text unless it already opens on an ellipsis; a cut after it, the sentence's own closing
 *  period giving way to it ("deck." + "…" reads "deck…"), set off by a space after a ? or ! ("now? …"), and none after an
 *  ellipsis already there. `cutPeriod`: the period is not part of the highlighted words, so it may give way. */
function joinCuts(text: string, before: boolean, after: boolean, cutPeriod: boolean): string {
  let t = text;
  if (before && !/^(?:…|\.\.\.)/.test(t)) t = "…" + t;
  if (after) {
    if (/(?:…|\.\.\.)["'”’)\]»]*$/.test(t)) return t;
    if (cutPeriod && /[^.]\.$/.test(t)) return t.slice(0, -1) + "…";
    t += /[.!?]["'”’)\]»]*$/.test(t) ? " …" : "…";
  }
  return t;
}

/** The window of [a, b) around the words [s, e) when it is longer than the cap, cut at whitespace where the text has any. */
function capWindow(line: string, a: number, b: number, s: number, e: number): [number, number] {
  if (b - a <= QUOTE_CONTEXT_CAP) return [a, b];
  const room = Math.max(0, QUOTE_CONTEXT_CAP - (e - s));
  let wa = Math.max(a, s - Math.floor(room / 2));
  const wb = Math.min(b, e + room - (s - wa));
  wa = Math.max(a, s - (room - (wb - e)));
  let na = wa, nb = wb;
  if (na > a) {
    let k = na;
    while (k < s && !/\s/.test(line[k - 1])) k++;
    if (k < s) { na = k; while (na < s && /\s/.test(line[na])) na++; }
  }
  if (nb < b) {
    let k = nb;
    while (k > e && !/\s/.test(line[k])) k--;
    if (k > e) { nb = k; while (nb > e && /\s/.test(line[nb - 1])) nb--; }
  }
  return [na, nb];
}

/** [start, end) trimmed of edge whitespace in `text`, and the words there, when they are a few words that are the
 *  highlight's (`words`, the quote the selection gave, compared whitespace-insensitively) on one line; else null. */
function fewWords(text: string, start: number, end: number, words?: string): [number, number, string] | null {
  if (!Number.isInteger(start) || !Number.isInteger(end) || start < 0 || end > text.length || start >= end) return null;
  while (start < end && /\s/.test(text[start])) start++;
  while (end > start && /\s/.test(text[end - 1])) end--;
  if (start >= end) return null;
  const said = text.slice(start, end);
  if (words !== undefined && norm(said) !== norm(words)) return null;
  if (said.includes("\n") || said.length > QUOTE_WORDS_MAX) return null;
  return [start, end, said];
}

/** The context line for a few words of PROSE at [start, end) of `text` (a block of a message's rendered text: a paragraph,
 *  a list item, a heading or a table cell, its hard line breaks as newlines and nothing else): the sentence the words sit
 *  in, with "…" where the line goes on before or after it, cut to a window of about QUOTE_CONTEXT_CAP characters around
 *  the words when the sentence is longer, and the words in **bold** when they occur more than once in what is quoted.
 *  Null when the words are more than a few (QUOTE_WORDS_MAX), a whole sentence or more, run over a line break, or are not
 *  `words`: the quote then goes out as it always did. */
export function quoteContext(text: string, start: number, end: number, words?: string): string | null {
  const w = fewWords(text, start, end, words);
  if (!w) return null;
  const [s0, e0, said] = w;
  const lineStart = text.lastIndexOf("\n", s0 - 1) + 1;
  const nl = text.indexOf("\n", e0);
  const line = text.slice(lineStart, nl < 0 ? text.length : nl);
  const s = s0 - lineStart, e = e0 - lineStart;
  // the sentence: after the last sentence end before the words, through the first at or after them
  let a = 0, b = line.length;
  for (const m of line.matchAll(STOP)) {
    if (!endsSentence(line, m)) continue;
    const stopEnd = m.index! + m[0].trimEnd().length, next = m.index! + m[0].length;
    if (next <= s) { a = next; continue; }
    if (stopEnd < e) return null;                      // the words run on into the next sentence
    b = stopEnd;
    break;
  }
  while (a < s && /\s/.test(line[a])) a++;
  while (b > e && /\s/.test(line[b - 1])) b--;
  // the whole sentence, give or take its opening and closing punctuation: no context to add
  const core0 = a + (/^["'“‘(\[«]*/.exec(line.slice(a, b))![0].length);
  const core1 = b - (/[.!?…:;,"'”’)\]»]*$/.exec(line.slice(a, b))![0].length);
  if (s <= core0 && e >= core1) return null;
  [a, b] = capWindow(line, a, b, s, e);
  let t = line.slice(a, b);
  // the words twice or more in what is quoted (any case): the highlighted one in bold
  const lower = t.toLowerCase(), lw = said.toLowerCase();
  let n = 0;
  for (let i = lower.indexOf(lw); i >= 0 && n < 2; i = lower.indexOf(lw, i + 1)) n++;
  if (n > 1) t = t.slice(0, s - a) + "**" + said + "**" + t.slice(e - a);
  return joinCuts(t, line.slice(0, a).trim() !== "", line.slice(b).trim() !== "", b > e);
}

/** The context for a few words of CODE at [start, end) of `line` (one line of a code block, or an inline code span, as
 *  the code's own text): the line itself, edge whitespace trimmed, cut to a window around the words when it is longer than
 *  the cap. Never a sentence and never bold. Null when the words are more than a few, the whole line, or not `words`. */
export function codeLineContext(line: string, start: number, end: number, words?: string): string | null {
  if (line.includes("\n")) return null;
  const w = fewWords(line, start, end, words);
  if (!w) return null;
  const [s, e] = w;
  let a = 0, b = line.length;
  while (a < s && /\s/.test(line[a])) a++;
  while (b > e && /\s/.test(line[b - 1])) b--;
  if (s <= a && e >= b) return null;                   // the whole line: the words say it all
  const [wa, wb] = capWindow(line, a, b, s, e);
  return (wa > a ? "…" : "") + line.slice(wa, wb) + (wb < b ? "…" : "");
}
