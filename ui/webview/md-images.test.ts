// The pictures a message's markdown embeds, off its text (md-images.ts, 2026-10-20): the lightbox's sequence reads them
// from the session's events, so they are found the way the renderer finds them, with marked's own lexer. Executed
// against the real marked, armed with the chat's grammar, as render.ts arms its singleton.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { Marked } from "marked";
import { mdImages } from "./md-images";
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
