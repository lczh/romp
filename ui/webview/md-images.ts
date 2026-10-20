// The pictures a message's markdown embeds, read off the message's TEXT, in the order the text names them (2026-10-20,
// the user reading on a phone: a markdown picture ran off the screen and never opened the lightbox, where a picture
// romp drew from a file path did). The chat's lightbox steps through every picture in the chat with the arrow keys, and
// that sequence is built from the session's EVENTS, not the DOM, because the transcript renders a window of its turns
// (render.ts chatImagesFor). So the markdown pictures are found the way the renderer finds them: marked's own lexer,
// passed in by the caller (the chat's singleton carries the chat grammar), never a regex over the text, so a picture
// written inside code is not a picture, and neither is one inside a link or a fold's <summary>: a linked picture follows
// its link when clicked and a summary's picture opens its fold, so neither is a stop in the lightbox's sequence either.
//
// `at` is the picture's offset in the text, so the caller can interleave the message's path previews by where they
// render, and the same walk records the blocks a path's figure renders after (mdLayout's figureAt). `url` is what the
// page's <img> carries before the browser resolves it: marked writes a markdown href through its cleanUrl (encodeURI
// with a percent sign kept as written), and an HTML <img src> keeps its attribute as the browser's parser reads it
// (imgAttrs). No DOM is needed: under node the attributes are read by pattern and decoded by the standard's rule.
import type { Token } from "marked";

export interface MdImage { url: string; alt: string; at: number; }

/** One message's reading for the lightbox: its pictures, and where a path it mentions shows its figure. */
export interface MdLayout {
  images: MdImage[];
  /** Where the figure of a path first mentioned at offset `mention` renders, as an offset the caller orders against the
   *  pictures' `at`. render.ts linkifyFileUris puts a figure after the paragraph or heading that names the path (BLOCK_SEL),
   *  or INSIDE a list item, at its end, after anything nested in it (a loose item's text is a paragraph of its own). So
   *  the figure lands at the end of that block, and a paragraph that wraps onto a second line holds the pictures written
   *  on it ahead of the figure, as the page does. A mention outside those blocks (a table row, code, raw HTML, or any
   *  mention in a message that names no picture and so was never lexed) keeps the end of its line; one not in the text
   *  at all (-1) lands at the end. */
  figureAt: (mention: number) => number;
}

/** marked's renderer runs a link's or a picture's href through cleanUrl: encodeURI, then `%25` back to `%`, so an
 *  author's own escapes stay as written; an href encodeURI refuses (a lone surrogate) renders as text, no picture. */
function cleanUrl(href: string): string | null {
  try { return encodeURI(href).replace(/%25/g, "%"); } catch { return null; }
}

// The HTML standard's numeric character reference, as a browser decodes one in an attribute (2026-10-20 review: a
// `src="…?a=1&#38;b=2"` came back undecoded, a stop no click could find and a phantom of its own): decimal or hex, the
// semicolon optional; zero, a surrogate or past U+10FFFF is U+FFFD; the C1 range names Windows-1252's characters (the
// standard's table: its five holes stay as they are). Named references beyond XML's five are the browser's alone.
const C1: Record<number, number> = {
  0x80: 0x20AC, 0x82: 0x201A, 0x83: 0x0192, 0x84: 0x201E, 0x85: 0x2026, 0x86: 0x2020, 0x87: 0x2021, 0x88: 0x02C6,
  0x89: 0x2030, 0x8A: 0x0160, 0x8B: 0x2039, 0x8C: 0x0152, 0x8E: 0x017D, 0x91: 0x2018, 0x92: 0x2019, 0x93: 0x201C,
  0x94: 0x201D, 0x95: 0x2022, 0x96: 0x2013, 0x97: 0x2014, 0x98: 0x02DC, 0x99: 0x2122, 0x9A: 0x0161, 0x9B: 0x203A,
  0x9C: 0x0153, 0x9E: 0x017E, 0x9F: 0x0178,
};
const NAMED: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: "\"", apos: "'" };
export function decodeRefs(v: string): string {
  return v.replace(/&(?:#[xX]([0-9a-fA-F]+);?|#([0-9]+);?|(amp|lt|gt|quot|apos);)/g, (_m, hex?: string, dec?: string, name?: string) => {
    if (name) return NAMED[name];
    const n = hex !== undefined ? parseInt(hex, 16) : parseInt(dec as string, 10);
    if (!(n > 0) || n > 0x10FFFF || (n >= 0xD800 && n <= 0xDFFF)) return "�";
    return String.fromCodePoint(C1[n] ?? n);
  });
}

// An HTML <img>'s src and alt as the page's <img> has them. In the browser that is the browser's own HTML parser, run on
// the tag alone in a detached <template>: its content is inert (nothing fetches, nothing runs), and the parse is the one
// the sanitizer's DOMPurify ran on the message, so every character reference decodes as it did there, named or numeric,
// and the first of two `src`s wins, as there. Under node (the unit tests) there is no parser: the attributes are read by
// pattern and decodeRefs decodes them. A null src: the tag has none (a srcset alone), so there is no URL to name.
const SRC = /\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i;
const ALT = /\balt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))/i;
let tpl: HTMLTemplateElement | null = null;
function imgAttrs(tag: string, attrs: string): { src: string | null; alt: string } {
  if (typeof document !== "undefined" && typeof document.createElement === "function") {
    tpl = tpl || document.createElement("template");
    if (tpl.content) {
      tpl.innerHTML = tag;
      const img = tpl.content.firstElementChild;
      if (img) return { src: img.getAttribute("src"), alt: img.getAttribute("alt") || "" };
    }
  }
  const s = SRC.exec(attrs), a = ALT.exec(attrs);
  return { src: s ? decodeRefs(s[1] ?? s[2] ?? s[3] ?? "") : null, alt: a ? decodeRefs(a[1] ?? a[2] ?? a[3] ?? "") : "" };
}

// One pass over an HTML fragment's tags: an <a> opens a link (its pictures are the link's), </a> closes it; a <summary>
// opens a fold's head (its pictures are the summary's: a click on one opens the fold), </summary> or the fold's own
// </details> closes it; every other <img> with a src yields it. `link` carries across the inline HTML tokens of one
// paragraph, where `<a href>`, the picture and `</a>` arrive as three tokens; `fold` across blocks too, since a blank
// line inside a summary leaves it open in the browser's parse.
const TAG = /<(\/?)(a|img|summary|details)\b([^>]*)>/gi;
function htmlImages(raw: string, at: number, link: { n: number }, fold: { n: number }, out: MdImage[]): void {
  TAG.lastIndex = 0;
  for (let m = TAG.exec(raw); m; m = TAG.exec(raw)) {
    const tag = m[2].toLowerCase(), close = !!m[1];
    if (tag === "a") { link.n = Math.max(0, link.n + (close ? -1 : 1)); continue; }
    if (tag === "summary") { fold.n = Math.max(0, fold.n + (close ? -1 : 1)); continue; }
    if (tag === "details") { if (close) fold.n = 0; continue; }
    if (close || link.n > 0 || fold.n > 0) continue;
    const { src, alt } = imgAttrs(m[0], m[3]);
    if (src === null || !src.trim()) continue;          // no src, or an empty one (the page's own address): nothing to step to
    out.push({ url: src, alt, at: at + m.index });
  }
}

/** Every picture `src` embeds outside a link and a summary, in document order, and where its path figures land. Cheap
 *  for the common message: one that names no picture (`![` or `<img`) is never lexed. */
export function mdLayout(src: string, lex: (s: string) => Token[]): MdLayout {
  const blocks: { start: number; end: number; land: number }[] = [];   // the leaf blocks a figure lands after, in order
  const figureAt = (mention: number): number => {
    if (mention < 0) return src.length;
    for (const b of blocks) if (mention >= b.start && mention < b.end) return b.land;
    const n = src.indexOf("\n", mention);
    return n < 0 ? src.length : n;
  };
  if (!src || !/!\[|<img/i.test(src)) return { images: [], figureAt };
  let tokens: Token[];
  try { tokens = lex(src); } catch { return { images: [], figureAt }; }
  const out: MdImage[] = [];
  const link = { n: 0 }, fold = { n: 0 };
  let from = 0;                                        // where the token being walked starts, at the latest
  const find = (raw: string): number => {
    const i = raw ? src.indexOf(raw, from) : -1;
    if (i >= 0) from = i;
    return i < 0 ? from : i;
  };
  // A block's span in the text. marked strips a container's markers off its children's raw (a quote's `>`, a list
  // item's indent) and expands leading tabs, so the raw is found line by line, each line's content after the last one's.
  const locate = (raw: string): { start: number; end: number } | null => {
    let pos = from, start = -1, end = -1;
    for (const line of raw.split("\n")) {
      const l = line.trim();
      if (!l) continue;
      const i = src.indexOf(l, pos);
      if (i < 0) return null;
      if (start < 0) start = i;
      pos = end = i + l.length;
    }
    return start < 0 ? null : { start, end };
  };
  type Tok = Token & { tokens?: Token[]; items?: Token[]; loose?: boolean; header?: { tokens: Token[] }[]; rows?: { tokens: Token[] }[][] };
  // `item`: the list item a block text sits in, when its list is tight (no paragraph around the text: the figure goes in
  // the item, at its end)
  const walk = (list: readonly Token[] | undefined, linked: boolean, item: { end: number } | null): void => {
    for (const t of list || []) {
      const tok = t as Tok;
      if (tok.type === "code" || tok.type === "codespan") { from = find(tok.raw) + tok.raw.length; continue; }   // code is never a picture: past it
      if (tok.type === "image") {
        const at = find(tok.raw);
        const im = tok as Token & { href: string; text: string };
        const url = cleanUrl(im.href);
        if (!linked && !fold.n && url) out.push({ url, alt: im.text || "", at });   // an empty href names the page itself: no picture
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "html") {
        const at = find(tok.raw);
        if (linked) link.n++;                          // an <img> inside a markdown link's text is the link's too
        htmlImages(tok.raw, at, link, fold, out);
        if (linked) link.n = Math.max(0, link.n - 1);
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "link") { find(tok.raw); walk(tok.tokens, true, item); continue; }
      if (tok.type === "list") {
        for (const it of tok.items || []) {
          link.n = 0;                                  // a link opened in raw HTML never spans blocks (nor items)
          const sp = locate(it.raw);
          if (sp) from = sp.start;
          walk((it as Tok).tokens, linked, tok.loose ? null : { end: sp ? sp.end : -1 });
          if (sp) from = Math.max(from, sp.end);
        }
        continue;
      }
      // the blocks a figure lands after: a paragraph, a heading, a list item's text (an inline text token, between
      // `<a href>` and its picture, carries no tokens of its own and is none of these)
      if (tok.type === "paragraph" || tok.type === "heading" || (tok.type === "text" && tok.tokens)) {
        link.n = 0;
        const sp = locate(tok.raw);
        if (sp) {
          blocks.push({ start: sp.start, end: sp.end, land: tok.type === "text" && item && item.end >= 0 ? item.end : sp.end });
          from = sp.start;
        }
        walk(tok.tokens, linked, null);
        if (sp) from = Math.max(from, sp.end);
        continue;
      }
      find(tok.raw);
      walk(tok.tokens, linked, null);
      for (const cell of tok.header || []) walk(cell.tokens, linked, null);
      for (const row of tok.rows || []) for (const cell of row) walk(cell.tokens, linked, null);
    }
  };
  walk(tokens, false, null);
  return { images: out, figureAt };
}

/** The pictures alone (mdLayout's). */
export function mdImages(src: string, lex: (s: string) => Token[]): MdImage[] {
  return mdLayout(src, lex).images;
}
