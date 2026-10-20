// The pictures a message's markdown embeds, read off the message's TEXT, in the order the text names them (2026-10-20,
// the user reading on a phone: a markdown picture ran off the screen and never opened the lightbox, where a picture
// romp drew from a file path did). The chat's lightbox steps through every picture in the chat with the arrow keys, and
// that sequence is built from the session's EVENTS, not the DOM, because the transcript renders a window of its turns
// (render.ts chatImagesFor). So the markdown pictures are found the way the renderer finds them: marked's own lexer,
// passed in by the caller (the chat's singleton carries the chat grammar), never a regex over the text, so a picture
// written inside code is not a picture, and neither is one inside a link: a linked picture follows its link when
// clicked, so it is not a stop in the lightbox's sequence either.
//
// `at` is the picture's offset in the text, so the caller can interleave the message's path previews by where they
// render. `url` is what the page's <img> carries before the browser resolves it: marked writes a markdown href through
// its cleanUrl (encodeURI with a percent sign kept as written), and an HTML <img src> keeps its attribute as written,
// its character references decoded. Pure: no DOM, executable under node.
import type { Token } from "marked";

export interface MdImage { url: string; alt: string; at: number; }

/** marked's renderer runs a link's or a picture's href through cleanUrl: encodeURI, then `%25` back to `%`, so an
 *  author's own escapes stay as written; an href encodeURI refuses (a lone surrogate) renders as text, no picture. */
function cleanUrl(href: string): string | null {
  try { return encodeURI(href).replace(/%25/g, "%"); } catch { return null; }
}

const ENTITY: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'", "#39": "'" };
function decodeAttr(v: string): string {
  return v.replace(/&(amp|lt|gt|quot|apos|#39);/g, (_m, k: string) => ENTITY[k]);
}

// One pass over an HTML fragment's tags: an <a> opens a link (its pictures are the link's), </a> closes it, and every
// <img> outside one yields its src. `depth` carries across the inline HTML tokens of one paragraph, where `<a href>`,
// the picture and `</a>` arrive as three tokens.
const TAG = /<(\/?)(a|img)\b([^>]*)>/gi;
const SRC = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i;
const ALT = /\balt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i;
function htmlImages(raw: string, at: number, depth: { n: number }, out: MdImage[]): void {
  TAG.lastIndex = 0;
  for (let m = TAG.exec(raw); m; m = TAG.exec(raw)) {
    const tag = m[2].toLowerCase();
    if (tag === "a") { depth.n = Math.max(0, depth.n + (m[1] ? -1 : 1)); continue; }
    if (m[1] || depth.n > 0) continue;
    const s = SRC.exec(m[3]);
    if (!s) continue;
    const a = ALT.exec(m[3]);
    out.push({ url: decodeAttr(s[1] ?? s[2] ?? s[3] ?? ""), alt: a ? decodeAttr(a[1] ?? a[2] ?? a[3] ?? "") : "", at: at + m.index });
  }
}

/** Every picture `src` embeds outside a link, in document order. Cheap for the common message: one that names no
 *  picture (`![` or `<img`) is never lexed. */
export function mdImages(src: string, lex: (s: string) => Token[]): MdImage[] {
  if (!src || !/!\[|<img/i.test(src)) return [];
  let tokens: Token[];
  try { tokens = lex(src); } catch { return []; }
  const out: MdImage[] = [];
  const depth = { n: 0 };
  let from = 0;                                        // where the token being walked starts, at the latest
  const find = (raw: string): number => {
    const i = raw ? src.indexOf(raw, from) : -1;
    if (i >= 0) from = i;
    return i < 0 ? from : i;
  };
  const walk = (list: readonly Token[] | undefined, linked: boolean): void => {
    for (const t of list || []) {
      const tok = t as Token & { tokens?: Token[]; items?: Token[]; header?: { tokens: Token[] }[]; rows?: { tokens: Token[] }[][] };
      if (tok.type === "code" || tok.type === "codespan") { from = find(tok.raw) + tok.raw.length; continue; }   // code is never a picture: past it
      if (tok.type === "image") {
        const at = find(tok.raw);
        const im = tok as Token & { href: string; text: string };
        const url = cleanUrl(im.href);
        if (!linked && url !== null) out.push({ url, alt: im.text || "", at });
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "html") {
        const at = find(tok.raw);
        if (linked) depth.n++;                         // an <img> inside a markdown link's text is the link's too
        htmlImages(tok.raw, at, depth, out);
        if (linked) depth.n = Math.max(0, depth.n - 1);
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "link") { find(tok.raw); walk(tok.tokens, true); continue; }
      // a link opened in raw HTML never spans blocks: each block starts outside one (a list item's block text carries
      // inline tokens of its own; an inline text token, between `<a href>` and its picture, must not reset it)
      if (tok.type === "paragraph" || tok.type === "heading" || tok.type === "list_item" || (tok.type === "text" && tok.tokens)) depth.n = 0;
      find(tok.raw);
      walk(tok.tokens, linked);
      for (const it of tok.items || []) walk([it], linked);
      for (const cell of tok.header || []) walk(cell.tokens, linked);
      for (const row of tok.rows || []) for (const cell of row) walk(cell.tokens, linked);
    }
  };
  walk(tokens, false);
  return out;
}
