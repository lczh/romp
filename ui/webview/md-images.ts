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
// (imgAttrs). Which `<` in the message's raw HTML opens a tag is read the way the browser's tokenizer reads it
// (htmlImages), the same code in the browser and under node; only the attributes' decoding uses the browser's parser
// when there is one.
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

/** An address the lightbox opens a picture on: a web address or a data: picture, the schemes the arrows' sequence keeps
 *  (render.ts chatImagesFor) and the only ones a click opens (preview.ts mdImgZoomable). Anything else, a javascript: or
 *  a data:text/html URL that a srcset candidate could name, opens nothing. `url` is resolved: an <img>'s currentSrc or
 *  src, or a markdown URL resolved against the page. */
export function opensInLightbox(url: string): boolean {
  return /^(https?:|data:image\/)/i.test(url);
}

// An HTML <img>'s src and alt as the page's <img> has them. In the browser that is the browser's own HTML parser, run on
// the tag alone in a detached <template>: its content is inert (nothing fetches, nothing runs), and the parse is the one
// the sanitizer's DOMPurify ran on the message, so every character reference decodes as it did there, named or numeric.
// Under node (the unit tests) there is no parser: the attributes are the ones htmlImages read off the tag, and decodeRefs
// decodes them. A null src: the tag has none (a srcset alone), so there is no URL to name.
let tpl: HTMLTemplateElement | null = null;
function imgAttrs(tag: string, attrs: Map<string, string>): { src: string | null; alt: string } {
  if (typeof document !== "undefined" && typeof document.createElement === "function") {
    tpl = tpl || document.createElement("template");
    if (tpl.content) {
      tpl.innerHTML = tag;
      const img = tpl.content.firstElementChild;
      if (img) return { src: img.getAttribute("src"), alt: img.getAttribute("alt") || "" };
    }
  }
  const s = attrs.get("src"), a = attrs.get("alt");
  return { src: s === undefined ? null : decodeRefs(s), alt: a === undefined ? "" : decodeRefs(a) };
}

// The message's raw HTML read the way the browser's tokenizer reads it (the HTML standard's tokenization), because the
// page is that parse: which `<` opens a tag, and which text is a comment or an element's raw text that no tag inside can
// open (2026-10-20 review: a scan for the text `<summary>` found one inside a comment and inside an attribute value, and
// every picture after it in the message lost its arrows; an <img> commented out was a stop the page never shows). So:
//   a comment runs from `<!--` to the first `-->` or `--!>` (`<!-->` and `<!--->` are whole, empty ones); `<!`, `<?` and
//   `</` before anything but a letter open a bogus comment that the next `>` ends; a tag's attribute values, quoted or
//   not, are the tag's own text; and the contents of script, style, textarea, title, xmp, iframe, noembed and noframes
//   are text up to the element's end tag (plaintext's, to the end), as are noscript's, which the sanitizer drops with
//   everything in it. The same code runs in the browser and under node.
// On the tags that remain: an <a> opens a link (its pictures are the link's), </a> closes it; a <summary> opens a fold's
// head (its pictures are the summary's: a click on one opens the fold), </summary> or the fold's own </details> closes
// it; every other <img> with a src is a picture. The state carries across the message's HTML tokens: `link` across the
// inline tokens of one paragraph (`<a href>`, the picture and `</a>` arrive as three; the walk drops it at a block),
// `fold` across blocks too, since a blank line inside a summary leaves it open in the browser's parse, and `skip` (an
// unclosed comment, "--", or the raw-text element being read) for as long as the browser's parse stays in it, which
// also hides the markdown pictures rendered inside it.
interface HtmlState { link: number; fold: number; skip: string; }
const RAW_TEXT = new Set(["script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"]);
const isSpace = (c: string) => c === " " || c === "\n" || c === "\t" || c === "\r" || c === "\f";
const isLetter = (c: string) => /^[a-z]$/i.test(c);
// A tag from just past its `<` or `</`: its name and attributes (the first of two with one name wins, as in the browser),
// and the offset past its `>`; null when the text ends inside the tag (the browser drops such a tag).
function readTag(raw: string, p: number): { name: string; attrs: Map<string, string>; end: number } | null {
  const n = raw.length, start = p;
  while (p < n && !isSpace(raw[p]) && raw[p] !== "/" && raw[p] !== ">") p++;
  const name = raw.slice(start, p).toLowerCase(), attrs = new Map<string, string>();
  for (;;) {
    while (p < n && (isSpace(raw[p]) || raw[p] === "/")) p++;          // a stray `/` between attributes is skipped too
    if (p >= n) return null;
    if (raw[p] === ">") return { name, attrs, end: p + 1 };
    const a = p++;                                                       // a name's first character may be `=`
    while (p < n && !isSpace(raw[p]) && raw[p] !== "/" && raw[p] !== ">" && raw[p] !== "=") p++;
    const key = raw.slice(a, p).toLowerCase();
    while (p < n && isSpace(raw[p])) p++;
    let val = "";
    if (raw[p] === "=") {
      p++;
      while (p < n && isSpace(raw[p])) p++;
      const q = raw[p];
      if (q === "\"" || q === "'") {
        const e = raw.indexOf(q, p + 1);
        if (e < 0) return null;
        val = raw.slice(p + 1, e);
        p = e + 1;
      } else {
        const v = p;
        while (p < n && !isSpace(raw[p]) && raw[p] !== ">") p++;
        val = raw.slice(v, p);
      }
    }
    if (!attrs.has(key)) attrs.set(key, val);
  }
}
function htmlImages(raw: string, at: number, st: HtmlState, out: MdImage[]): void {
  const n = raw.length;
  let i = 0;
  while (i < n) {
    if (st.skip) {                                     // inside a comment or a raw-text element: find where it ends
      if (st.skip === "plaintext") return;
      const end = st.skip === "--" ? /--!?>/g : new RegExp("</" + st.skip + "(?=[\\t\\n\\f\\r />])", "gi");
      end.lastIndex = i;
      const m = end.exec(raw);
      if (!m) return;                                  // the rest of this token is its text
      i = st.skip === "--" ? m.index + m[0].length : m.index;   // an element's end tag is a tag: read below
      st.skip = "";
      continue;
    }
    const lt = raw.indexOf("<", i);
    if (lt < 0) return;
    const c = raw[lt + 1] || "";
    if (raw.startsWith("<!--", lt)) {
      if (raw[lt + 4] === ">") i = lt + 5;             // <!-->
      else if (raw.startsWith("->", lt + 4)) i = lt + 6;   // <!--->
      else { st.skip = "--"; i = lt + 4; }
      continue;
    }
    if (c === "/" && raw[lt + 2] === ">") { i = lt + 3; continue; }   // `</>` is dropped
    if (c === "!" || c === "?" || (c === "/" && lt + 2 < n && !isLetter(raw[lt + 2]))) {   // a bogus comment
      const gt = raw.indexOf(">", lt + 2);
      if (gt < 0) return;
      i = gt + 1;
      continue;
    }
    const close = c === "/";
    if (!isLetter(close ? raw[lt + 2] || "" : c)) { i = lt + 1; continue; }   // a `<` that opens nothing is text
    const tag = readTag(raw, lt + (close ? 2 : 1));
    if (!tag) return;
    i = tag.end;
    if (close) {
      if (tag.name === "a") st.link = Math.max(0, st.link - 1);
      else if (tag.name === "summary") st.fold = Math.max(0, st.fold - 1);
      else if (tag.name === "details") st.fold = 0;
      continue;
    }
    if (tag.name === "a") { st.link++; continue; }
    if (tag.name === "summary") { st.fold++; continue; }
    if (RAW_TEXT.has(tag.name)) { st.skip = tag.name; continue; }
    if (tag.name !== "img" || st.link > 0 || st.fold > 0) continue;
    const { src, alt } = imgAttrs(raw.slice(lt, tag.end), tag.attrs);
    if (src === null || !src.trim()) continue;          // no src, or an empty one (the page's own address): nothing to step to
    out.push({ url: src, alt, at: at + lt });
  }
}

/** Every picture `src` embeds outside a link, a summary and a comment, in document order, and where its path figures
 *  land. Cheap for the common message: one that names no picture (`![` or `<img`) is never lexed. */
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
  const st: HtmlState = { link: 0, fold: 0, skip: "" };
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
        if (!linked && !st.fold && !st.skip && url) out.push({ url, alt: im.text || "", at });   // an empty href names the page itself: no picture
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "html") {
        const at = find(tok.raw);
        if (linked) st.link++;                         // an <img> inside a markdown link's text is the link's too
        htmlImages(tok.raw, at, st, out);
        if (linked) st.link = Math.max(0, st.link - 1);
        from = at + tok.raw.length;
        continue;
      }
      if (tok.type === "link") { find(tok.raw); walk(tok.tokens, true, item); continue; }
      if (tok.type === "list") {
        for (const it of tok.items || []) {
          st.link = 0;                                 // a link opened in raw HTML never spans blocks (nor items)
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
        st.link = 0;
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
