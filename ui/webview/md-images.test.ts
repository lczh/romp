// The pictures a message's markdown embeds, off its text (md-images.ts, 2026-10-20): the lightbox's sequence reads them
// from the session's events, so they are found the way the renderer finds them, with marked's own lexer. Executed
// against the real marked, armed with the chat's grammar, as render.ts arms its singleton.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { Marked } from "marked";
import { mdImages, mdLayout, decodeRefs, opensInLightbox, isVideoUrl, mdImgFile, mdImgFileSrc, mdImgPathPass } from "./md-images";
import { chatMdExtensions } from "./chat-md";

const chat = new Marked({ gfm: true, breaks: false }, ...chatMdExtensions);
const lex = (s: string) => chat.lexer(s);
const urls = (src: string) => mdImages(src, lex).map((m) => m.url);

test("a markdown picture, with its alt text and its offset in the text", () => {
  const src = "Throughput over the run:\n\n![throughput](https://img.example.test/wide.png \"the run\")\n";
  const [m, ...rest] = mdImages(src, lex);
  assert.equal(rest.length, 0);
  assert.equal(m.url, "https://img.example.test/wide.png");
  assert.equal(m.alt, "throughput");
  assert.equal(m.at, src.indexOf("![throughput]"), "where the picture is written, so the caller can order it against path figures");
});

test("document order across blocks: paragraphs, list items, table cells, quotes", () => {
  const src = [
    "![a](https://img.example.test/a.png)",
    "",
    "- one ![b](https://img.example.test/b.png)",
    "- two",
    "",
    "| k | v |",
    "|---|---|",
    "| x | ![c](https://img.example.test/c.png) |",
    "",
    "> ![d](https://img.example.test/d.png)",
  ].join("\n");
  const got = mdImages(src, lex);
  assert.deepEqual(got.map((m) => m.alt), ["a", "b", "c", "d"]);
  for (let i = 1; i < got.length; i++) assert.ok(got[i].at > got[i - 1].at, "offsets increase in document order: " + JSON.stringify(got));
  assert.equal(got[2].at, src.indexOf("![c]"));
});

test("a picture inside a link is the link's: no stop (markdown and HTML links alike)", () => {
  assert.deepEqual(urls("[![badge](https://img.example.test/badge.svg)](https://ci.example.test/run)"), []);
  assert.deepEqual(urls('<a href="https://ci.example.test/run"><img src="https://img.example.test/badge.svg"></a>'), []);
  assert.deepEqual(urls('see <a href="https://ci.example.test/run"> <img src="https://img.example.test/in.png"> </a> and <img src="https://img.example.test/out.png">'),
    ["https://img.example.test/out.png"], "an inline <a>, a space, its picture, </a>: three tokens, still the link's; the one after it is not");
  assert.deepEqual(urls("[see ![x](https://img.example.test/x.png) here](https://ci.example.test/)\n\n![y](https://img.example.test/y.png)"),
    ["https://img.example.test/y.png"]);
});

test("code is never a picture: a span or a fence that shows the syntax", () => {
  const src = "`![no](https://img.example.test/span.png)`![yes](https://img.example.test/span.png)\n\n```md\n![no](https://img.example.test/fence.png)\n```\n";
  const got = mdImages(src, lex);
  assert.deepEqual(got.map((m) => m.alt), ["yes"], "only the real picture");
  assert.equal(got[0].at, src.indexOf("![yes]"), "its offset past the span that spells the same URL");
});

test("an HTML <img> in the message: its src as written, character references decoded, its alt", () => {
  const got = mdImages('<p><img alt="curve &amp; fit" src="https://img.example.test/c.png?a=1&amp;b=2"></p>\n\n<img src=\'https://img.example.test/q.png\'>', lex);
  assert.deepEqual(got.map((m) => [m.url, m.alt]), [["https://img.example.test/c.png?a=1&b=2", "curve & fit"], ["https://img.example.test/q.png", ""]]);
});

test("numeric character references in an HTML <img> decode as the browser decodes them: decimal and hex, src and alt", () => {
  // the 2026-10-20 review's case: `&#38;` left as written named a URL no picture on the page has (its `#` a fragment)
  const got = mdImages('<img alt="&#60;fit&#x3E; &#x2014; run 2" src="https://img.example.test/c.png?a=1&#38;b=2&#x26;c=3&#X26;d=4&#38e=5">', lex);
  assert.deepEqual(got.map((m) => [m.url, m.alt]), [["https://img.example.test/c.png?a=1&b=2&c=3&d=4&e=5", "<fit> — run 2"]]);
});

test("decodeRefs: the HTML standard's numeric rule, the cases a browser maps (checked against Chromium's own parse)", () => {
  const cases: [string, string][] = [
    ["&#38;", "&"], ["&#x26;", "&"], ["&#X26;", "&"], ["&#0038;", "&"], ["&#38", "&"], ["&#65&#66;", "AB"],
    ["&#39;", "'"], ["&apos;", "'"], ["&amp;&lt;&gt;&quot;", "&<>\""],
    ["&#128;", "€"], ["&#x9F;", "Ÿ"], ["&#x81;", "\x81"],          // the C1 range is Windows-1252's, its holes as they are
    ["&#0;", "�"], ["&#xD800;", "�"], ["&#x110000;", "�"], ["&#99999999999999999999;", "�"],
    ["&#x1F600;", "\u{1F600}"],
    ["&#;", "&#;"], ["&#x;", "&#x;"], ["&#xg;", "&#xg;"], ["& #38;", "& #38;"],   // no digits: no reference
  ];
  for (const [inp, want] of cases) assert.equal(decodeRefs(inp), want, JSON.stringify(inp));
});

test("a picture inside a <details>'s <summary> is the summary's: no stop, as a linked one, across a blank line too", () => {
  assert.deepEqual(urls('<details><summary><img src="https://img.example.test/head.png"> Raw counts</summary>\n\n![body](https://img.example.test/body.png)\n\n</details>'),
    ["https://img.example.test/body.png"], "the summary's picture opens the fold; the body's zooms");
  assert.deepEqual(urls("<details><summary>\n\n![x](https://img.example.test/x.png)\n\n</summary>\n\n![y](https://img.example.test/y.png)\n\n</details>"),
    ["https://img.example.test/y.png"], "a summary left open by a blank line still holds the markdown picture after it");
  assert.deepEqual(urls("<details><summary>Plot\n\n![x](https://img.example.test/x.png)\n\n</details>\n\n![z](https://img.example.test/z.png)"),
    ["https://img.example.test/z.png"], "a summary never closed ends with its details, as the browser's parse ends it");
});

// The raw HTML is read as the browser's tokenizer reads it (2026-10-20 review of the summary rule: a scan for the TEXT
// `<summary>` opened a fold inside a comment or an attribute value, and every later picture in the message lost its
// arrows while the page still showed it and let it zoom). Every message in these five tests was checked against Chromium:
// the page's pictures, rendered and sanitized as the chat does, outside any link or summary, are these stops.
test("the text `<summary>` inside a comment opens no fold: the pictures after it are still stops", () => {
  assert.deepEqual(urls("Note <!-- <summary> --> here\n\n![a](https://img.example.test/a.png)"), ["https://img.example.test/a.png"],
    "an inline comment, then a markdown picture");
  assert.deepEqual(urls('<!-- <details><summary> -->\n\n<img src="https://img.example.test/b.png">\n\n![c](https://img.example.test/c.png)'),
    ["https://img.example.test/b.png", "https://img.example.test/c.png"], "a block comment, then an HTML picture and a markdown one");
  assert.deepEqual(urls("<!-- a -- b --!> <summary> --> ![d](https://img.example.test/d.png)"), [],
    "`--!>` ends a comment as `-->` does, so the summary after it is a real one");
});

test("the text `<summary>` inside an attribute value opens no fold", () => {
  assert.deepEqual(urls('Note <span title="<summary>">x</span> here ![a](https://img.example.test/a.png)'), ["https://img.example.test/a.png"]);
  assert.deepEqual(urls("<div data-x='<summary>' class=y>\n<img src=\"https://img.example.test/b.png\">\n</div>\n\n![c](https://img.example.test/c.png)"),
    ["https://img.example.test/b.png", "https://img.example.test/c.png"], "a block's tag, single-quoted");
  assert.deepEqual(urls('<span title="a > <summary> b">x</span> ![d](https://img.example.test/d.png)'), ["https://img.example.test/d.png"],
    "a `>` inside a quoted value does not end the tag");
});

test("an <img> the page never shows is no stop: commented out, inside an attribute value, inside raw text", () => {
  assert.deepEqual(urls('see <!-- <img src="https://img.example.test/c.png"> --> ok'), [], "an inline comment");
  assert.deepEqual(urls('<!--\n<img src="https://img.example.test/c.png">\n-->\n\n![a](https://img.example.test/a.png)'),
    ["https://img.example.test/a.png"], "a block comment; the picture after it still counts");
  assert.deepEqual(urls("<span title='<img src=\"https://img.example.test/t.png\">'>x</span>"), [], "an attribute value");
  assert.deepEqual(urls('<textarea><img src="https://img.example.test/t.png"></textarea> <img src="https://img.example.test/u.png">'),
    ["https://img.example.test/u.png"], "a textarea's contents are text up to its end tag");
  assert.deepEqual(urls('<noscript><img src="https://img.example.test/n.png"></noscript> ok'), [], "noscript's go with it in the sanitizer");
  assert.deepEqual(urls("<div>\n<!-- draft\n\n![h](https://img.example.test/h.png)\n\n</div>"), [],
    "a comment left open runs on through the markdown after it, as the browser's parse does");
  assert.deepEqual(urls('<!--> <img src="https://img.example.test/e.png"> <!---> <img src="https://img.example.test/f.png">'),
    ["https://img.example.test/e.png", "https://img.example.test/f.png"], "`<!-->` and `<!--->` are whole, empty comments");
  assert.deepEqual(urls('<!x <img src="https://img.example.test/g.png"> <img src="https://img.example.test/k.png">'),
    ["https://img.example.test/k.png"], "a bogus comment ends at the first `>`, the first picture's own");
});

test("a real <summary> still holds its picture, beside a comment that only mentions one", () => {
  assert.deepEqual(urls('<!-- <summary> -->\n\n<details><summary><img src="https://img.example.test/head.png"> Plot</summary>\n\n![body](https://img.example.test/body.png)\n\n</details>'),
    ["https://img.example.test/body.png"], "the summary's picture opens its fold; the body's zooms");
  assert.deepEqual(urls('<details><summary title="</summary>"><img src="https://img.example.test/head.png"></summary></details>\n\n![z](https://img.example.test/z.png)'),
    ["https://img.example.test/z.png"], "an attribute that spells `</summary>` closes nothing");
});

test("an HTML <img>'s src is its own attribute, not text that spells one inside another", () => {
  assert.deepEqual(mdImages('<img title="src=https://img.example.test/no.png" src="https://img.example.test/yes.png" alt=\'a "b"\'>', lex)
    .map((m) => [m.url, m.alt]), [["https://img.example.test/yes.png", 'a "b"']]);
  assert.deepEqual(urls('<img src="https://img.example.test/first.png" SRC="https://img.example.test/second.png">'),
    ["https://img.example.test/first.png"], "the first of two src attributes wins, in any case");
});

test("a picture with no source to name is no stop: a srcset alone, an empty src, an empty href", () => {
  assert.deepEqual(urls('<img srcset="https://img.example.test/a.png 1x" alt="a"> <img src="" alt="b"> <img src="  "> ![e]()'), []);
  assert.deepEqual(urls('<img src="https://img.example.test/ok.png" srcset="https://img.example.test/ok2.png 2x">'),
    ["https://img.example.test/ok.png"], "a src beside a srcset names the picture by its src");
});

test("figureAt: a path's figure lands after the block that names it, a paragraph that wraps whole", () => {
  const src = "The split is in docs/figure.png and\n![x](https://img.example.test/x.png) shows the curve.\n\n![y](https://img.example.test/y.png)";
  const { images, figureAt } = mdLayout(src, lex);
  const fig = figureAt(src.indexOf("docs/figure.png"));
  assert.equal(fig, src.indexOf("curve.") + "curve.".length, "the end of the paragraph, past its second line");
  assert.ok(images[0].at < fig && fig < images[1].at, "so x (on the wrapped line) shows before the figure, y after it");
  assert.equal(figureAt(-1), src.length, "a mention not in the text: the end");
});

test("figureAt: a tight list item's figure goes in the item, after anything nested in it; a loose item's after its text", () => {
  const tight = "- see docs/a.png and\n  ![b](https://img.example.test/b.png) below\n  - inner ![c](https://img.example.test/c.png)\n- next ![d](https://img.example.test/d.png)";
  const t = mdLayout(tight, lex);
  const at = t.figureAt(tight.indexOf("docs/a.png"));
  assert.deepEqual(t.images.map((m) => m.at < at), [true, true, false], "b and the nested c show before the figure, the next item's d after");
  const loose = "- see docs/a.png\n\n  ![b](https://img.example.test/b.png)\n\n- next";
  const l = mdLayout(loose, lex);
  assert.ok(l.figureAt(loose.indexOf("docs/a.png")) < l.images[0].at, "a loose item's text is a paragraph: its figure follows it, before b");
});

test("figureAt: a quote's paragraph is found under its markers; a table row, code and an unlexed message keep the line's end", () => {
  const quote = "> see docs/a.png and\n> ![b](https://img.example.test/b.png) here";
  const q = mdLayout(quote, lex);
  assert.equal(q.figureAt(quote.indexOf("docs/a.png")), quote.length, "after the quote's paragraph, both its lines");
  const table = "| k | v |\n|---|---|\n| docs/c.png | x |\n| e | ![e](https://img.example.test/e.png) |";
  const tb = mdLayout(table, lex);
  const row = table.indexOf("| docs/c.png");
  assert.equal(tb.figureAt(table.indexOf("docs/c.png")), table.indexOf("\n", row), "a table row: the row's end");
  const plain = "one docs/a.png\ntwo docs/b.png";
  assert.equal(mdLayout(plain, lex).figureAt(0 + plain.indexOf("docs/a.png")), plain.indexOf("\n"), "no picture, never lexed: the line's end");
});

test("a markdown href goes through marked's own cleanUrl: encodeURI, an author's escapes kept", () => {
  assert.deepEqual(urls("![s](<https://img.example.test/run 1/a b.png>)"), ["https://img.example.test/run%201/a%20b.png"]);
  assert.deepEqual(urls("![s](https://img.example.test/run%201.png)"), ["https://img.example.test/run%201.png"], "an escape stays one escape");
});

test("relative and data: sources come back as written (the caller resolves them against the page)", () => {
  assert.deepEqual(urls("![p](plots/a.png) ![d](data:image/png;base64,AAAA)"), ["plots/a.png", "data:image/png;base64,AAAA"]);
});

test("a message that names no picture is never lexed", () => {
  let lexed = 0;
  const counting = (s: string) => { lexed++; return lex(s); };
  assert.deepEqual(mdImages("plain prose with [a link](https://example.test) and `code`", counting), []);
  assert.deepEqual(mdImages("", counting), []);
  assert.equal(lexed, 0);
});

test("opensInLightbox: a web address or a data: picture; never a script, a page or anything else", () => {
  for (const u of ["https://img.example.test/a.png", "http://img.example.test/a.png", "HTTPS://img.example.test/a.png",
    "data:image/png;base64,AAAA", "data:image/svg+xml,%3Csvg%3E"]) assert.equal(opensInLightbox(u), true, u);
  for (const u of ["javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,<p>x</p>", "data:,x", "blob:https://img.example.test/1",
    "file:///tmp/x.png", "vbscript:x", ""]) assert.equal(opensInLightbox(u), false, u);
});

// ── videos (2026-10-21, the user wanting a chat video treated like a picture) ─────────────────────────────────────────
test("opensInLightbox for a video: a web address on another site, or this kernel's file route; never data:, blob: or a script", () => {
  const here = "https://dash.example.test";
  for (const u of ["https://media.example.test/demo.mp4", "http://media.example.test/clip.webm?x=1", "https://media.example.test/stream",
    here + "/file?path=out%2Fdemo.mp4&sid=11111111-2222-3333-4444-555555555555", here + "/remote/TESTHOST/file?path=demo.mov"])
    assert.equal(opensInLightbox(u, "video", here), true, u);
  for (const u of ["data:video/mp4;base64,AAAA", "blob:https://dash.example.test/1", "javascript:alert(1)", "JavaScript:alert(1)",
    "file:///tmp/demo.mp4", "", "not a url", here + "/chat", here + "/api/sessions", here + "/remote/TESTHOST/ws", here + "/files"])
    assert.equal(opensInLightbox(u, "video", here), false, u);
  // the picture rule is untouched: the kind defaults to a picture, and a data: picture still opens
  assert.equal(opensInLightbox("data:image/png;base64,AAAA"), true);
  assert.equal(opensInLightbox(here + "/chat"), true, "a picture's rule is the scheme alone, as before");
  assert.equal(opensInLightbox("data:video/mp4;base64,AAAA"), false, "…and a data: video is no picture either");
});

test("isVideoUrl: a web address whose path ends in .mp4, .webm or .mov", () => {
  for (const u of ["https://media.example.test/demo.mp4", "https://media.example.test/a/b/Clip.WEBM", "http://media.example.test/x.mov?t=3#frag"])
    assert.equal(isVideoUrl(u), true, u);
  for (const u of ["https://media.example.test/demo.mp4/page", "https://media.example.test/?f=demo.mp4", "https://media.example.test/demo.mp3",
    "demo.mp4", "/file?path=demo.mp4", "javascript:x.mp4", "data:video/mp4;base64,AAAA", "https://media.example.test/.mp4dir/x"])
    assert.equal(isVideoUrl(u), false, u);
});

test("a <video> is a stop where it is written, named by its src or its first <source>; a linked or folded one is not", () => {
  const src = [
    "The run:",
    "",
    "<video controls width=\"100%\" src=\"https://media.example.test/run.mp4\"></video>",
    "",
    "![after](https://img.example.test/after.png)",
    "",
    "<video controls>",
    "  <source src=\"https://media.example.test/alt.webm\" type=\"video/webm\">",
    "  <source src=\"https://media.example.test/alt.mp4\" type=\"video/mp4\">",
    "  <img src=\"https://img.example.test/fallback.png\">",
    "</video>",
    "",
    "<a href=\"https://ci.example.test/run\"><video src=\"https://media.example.test/linked.mp4\"></video></a>",
    "",
    "<details><summary><video src=\"https://media.example.test/folded.mp4\"></video></summary>x</details>",
  ].join("\n");
  const got = mdImages(src, lex);
  assert.deepEqual(got.map((m) => [m.url, !!m.video]), [
    ["https://media.example.test/run.mp4", true], ["https://img.example.test/after.png", false], ["https://media.example.test/alt.webm", true]],
    "the first source names a sourced video; its fallback picture is never shown, so never a stop; a linked or folded video is its link's or fold's");
  assert.equal(got[0].at, src.indexOf("<video controls width"), "where the video is written");
  assert.ok(got[2].at === src.indexOf("<video controls>\n"), "a sourced video stands where its <video> opens");
});

test("a link to a video file renders its still after its block: videoLinks, at a path figure's place", () => {
  const src = "The demo is at https://media.example.test/demo.mp4 and\n![x](https://img.example.test/x.png) beside it.\n\n"
    + "[the long take](https://media.example.test/long.webm)\n\n`https://media.example.test/code.mp4` stays code.\n\n"
    + "<a href=\"https://media.example.test/raw.mov\">raw</a>\n\n[again](https://media.example.test/demo.mp4)\n\n"
    + "<video src=\"https://media.example.test/shown.mp4\"></video> and [its link](https://media.example.test/shown.mp4)\n\n"
    + "[a page](https://media.example.test/page)";
  const { images, videoLinks } = mdLayout(src, lex);
  assert.deepEqual(videoLinks.map((m) => m.url), ["https://media.example.test/demo.mp4", "https://media.example.test/long.webm",
    "https://media.example.test/raw.mov"], "autolinked, markdown and HTML links; the first to a URL alone; none in code; none a <video> shows; no page link");
  assert.ok(videoLinks.every((m) => m.video), "each a video");
  const x = images.find((m) => m.url.endsWith("x.png"))!;
  assert.ok(x.at < videoLinks[0].at, "the wrapped paragraph's picture shows before the link's still, which lands after the paragraph");
  assert.equal(videoLinks[0].at, src.indexOf("beside it.") + "beside it.".length);
});

test("a message that names a video file is read; one that names none still is not lexed", () => {
  let lexed = 0;
  const counting = (s: string) => { lexed++; return lex(s); };
  assert.equal(mdLayout("see https://media.example.test/demo.mp4", counting).videoLinks.length, 1);
  assert.equal(lexed, 1);
  assert.deepEqual(mdLayout("see https://media.example.test/page", counting).videoLinks, []);
  assert.equal(lexed, 1, "no picture, no video, no video file: never lexed");
});

// ── a picture whose src is a FILE (2026-11-12): `![plot](/tmp/…png)` resolved against the dashboard's origin and 404'd, where
// the same path on a line of its own showed. md() and userMd() now load it through the kernel's /file for the session the
// message belongs to (mdImgPathPass), the rule the file viewer's images follow. Synthetic paths, placeholder sids.
const WEB = "aaaaaaaa-1111-2222-3333-444444444444";

test("a src that is a path names a file on the session's disk: absolute, under home, relative to the session's folder", () => {
  assert.deepEqual(mdImgFile("/tmp/notes-api/latency.png"), { path: "/tmp/notes-api/latency.png" });
  assert.deepEqual(mdImgFile("~/notes-api/figs/latency.png"), { path: "~/notes-api/figs/latency.png" }, "the kernel expands ~");
  assert.deepEqual(mdImgFile("docs/figure.png"), { path: "docs/figure.png" }, "the kernel resolves it against the session's cwd");
  assert.deepEqual(mdImgFile("./docs/../docs/figure.png#top"), { path: "docs/figure.png" }, "normalised, the fragment dropped");
  assert.deepEqual(mdImgFile("/tmp/notes-api/p95%20split.png"), { path: "/tmp/notes-api/p95 split.png" }, "marked's percent-encoding decoded");
  assert.deepEqual(mdImgFile("constructor"), { path: "constructor" }, "a name that is an Object key is still only a name");
});

test("a src the browser fetches itself, or the kernel's file route already, is no file", () => {
  for (const u of ["https://img.example.test/a.png", "HTTP://img.example.test/a.png", "data:image/png;base64,AAAA", "blob:https://x.test/1",
    "//img.example.test/a.png", "\\\\img.example.test/a.png", "#top", "?v=2", "", "/file?path=%2Ftmp%2Fa.png&sid=s1",
    "/remote/gpu1/file?path=a.png&sid=s1"]) assert.equal(mdImgFile(u), null, JSON.stringify(u));
});

test("a path the kernel verified names its file and carries the message's mention-time pin", () => {
  const paths = { links: { "docs/figure.png": "docs/figure.png", "./figs/a.png": "figs/a.png", "/tmp/p95 split.png": "/tmp/p95 split.png" },
                  pins: { "docs/figure.png": "v1", "/tmp/p95 split.png": "v2" } };
  assert.deepEqual(mdImgFile("docs/figure.png", paths), { path: "docs/figure.png", pin: "v1" });
  assert.deepEqual(mdImgFile("./figs/a.png", paths), { path: "figs/a.png" }, "the token as written, mapped to the kernel's file; no pin, none carried");
  assert.deepEqual(mdImgFile("/tmp/p95%20split.png", paths), { path: "/tmp/p95 split.png", pin: "v2" }, "found by its decoded token");
  assert.deepEqual(mdImgFile("docs/other.png", paths), { path: "docs/other.png" }, "an unverified path still loads, the kernel resolving it");
  assert.deepEqual(mdImgFile("toString", { links: {}, pins: {} }), { path: "toString" }, "an inherited key is never a link");
});

test("the address: this kernel's /file for a local session, a remote session's /remote/<host>/file with its bare sid, pinned when pinned", () => {
  assert.equal(mdImgFileSrc({ path: "/tmp/notes-api/latency.png" }, WEB), "/file?path=%2Ftmp%2Fnotes-api%2Flatency.png&sid=" + WEB);
  assert.equal(mdImgFileSrc({ path: "docs/figure.png", pin: "v1" }, "gpu1:" + WEB),
    "/remote/gpu1/file?path=docs%2Ffigure.png&sid=" + WEB + "&pin=v1", "the owning machine's kernel, through this one's relay");
  assert.equal(mdImgFileSrc({ path: "~/figs/a.png" }, null), "/file?path=~%2Ffigs%2Fa.png", "no session: the path alone");
});

// a sanitized body, as far as the pass reads it: its <img>s with their attributes and dataset
class FakeImg {
  attrs: Record<string, string>; dataset: Record<string, string> = {};
  constructor(src: string) { this.attrs = { src }; }
  getAttribute(k: string): string | null { return k in this.attrs ? this.attrs[k] : null; }
  setAttribute(k: string, v: string): void { this.attrs[k] = v; }
}
const bodyOf = (srcs: string[]) => { const imgs = srcs.map((u) => new FakeImg(u)); return { imgs, root: { querySelectorAll: () => imgs } as unknown as ParentNode }; };

test("md()'s pass: every picture written as a path loads through the kernel for the owning session, a URL picture is left alone", () => {
  const reply = "The screens:\n\n![a](/tmp/notes-api/a.png)\n\n![b](~/notes-api/b.png)\n\n![c](docs/figure.png)\n\n"
    + "<img src=\"/tmp/notes-api/raw.png\" width=\"45%\">\n\n![web](https://img.example.test/wide.png)\n\n![d](data:image/png;base64,AAAA)";
  const srcs = mdImages(reply, lex).map((m) => m.url);   // each <img>'s src as marked writes it (the attribute the pass reads)
  const { imgs, root } = bodyOf(srcs);
  mdImgPathPass(root, WEB, { links: { "docs/figure.png": "docs/figure.png" }, pins: { "docs/figure.png": "v1" } });
  assert.deepEqual(imgs.map((i) => i.attrs.src), [
    "/file?path=%2Ftmp%2Fnotes-api%2Fa.png&sid=" + WEB,
    "/file?path=~%2Fnotes-api%2Fb.png&sid=" + WEB,
    "/file?path=docs%2Ffigure.png&sid=" + WEB + "&pin=v1",
    "/file?path=%2Ftmp%2Fnotes-api%2Fraw.png&sid=" + WEB,
    "https://img.example.test/wide.png",
    "data:image/png;base64,AAAA"]);
  assert.deepEqual(imgs.map((i) => [i.dataset.mdPath, i.dataset.mdSid, i.dataset.mdPin]), [
    ["/tmp/notes-api/a.png", WEB, undefined], ["~/notes-api/b.png", WEB, undefined], ["docs/figure.png", WEB, "v1"],
    ["/tmp/notes-api/raw.png", WEB, undefined], [undefined, undefined, undefined], [undefined, undefined, undefined]],
    "the file rides on the picture, so its click opens the lightbox on the file");
});

test("md()'s pass on a remote session's message: the remote kernel's file, through this kernel's relay", () => {
  const { imgs, root } = bodyOf(["/tmp/notes-api/a.png", "docs/figure.png"]);
  mdImgPathPass(root, "gpu1:" + WEB, { links: { "docs/figure.png": "docs/figure.png" }, pins: { "docs/figure.png": "v1" } });
  assert.deepEqual(imgs.map((i) => i.attrs.src), ["/remote/gpu1/file?path=%2Ftmp%2Fnotes-api%2Fa.png&sid=" + WEB,
    "/remote/gpu1/file?path=docs%2Ffigure.png&sid=" + WEB + "&pin=v1"]);
  assert.equal(imgs[0].dataset.mdSid, "gpu1:" + WEB, "the full sid, host prefix kept, for the lightbox's own fetch");
  mdImgPathPass(root, "gpu1:" + WEB);
  assert.equal(imgs[1].attrs.src, "/remote/gpu1/file?path=docs%2Ffigure.png&sid=" + WEB + "&pin=v1", "a second pass wraps nothing twice");
});
