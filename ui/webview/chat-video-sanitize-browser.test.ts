// A <video> in a chat message through the REAL pipeline in headless Chromium (2026-10-21, the user wanting a chat video
// treated like a picture): marked with the chat grammar, sanitizeMd (DOMPurify under the one profile, md-sanitize.ts),
// then the chat's post-pass (preview.ts mdImgPostPass, which turns every video the lightbox may open into the still),
// serialized and put back into a page exactly as md() and innerHTML do. What a source pin cannot see, and this leg reads
// off the live elements:
//   • nothing an author writes on a <video> or a <source> runs: every on* handler is gone, a poster or a src with a
//     javascript: URL is dropped, a blob: src too (DOMPurify's URI rule), so a still never names one;
//   • the still: no native controls, no autoplay, no loop, muted as a PROPERTY once parsed back (the attribute is what
//     survives md()'s serialization), playsinline, preload="metadata", the first-frame fragment on its address, and the
//     address the lightbox opens on its wrapper without that fragment; framed as a block only alone in its paragraph;
//   • every other video keeps its own controls and never autoplays: a data: clip (the sanitizer lets a <video> carry one,
//     the lightbox never opens one), a linked one, one in a fold's <summary>;
//   • a <source>-only video is named by the first <source> that survives the sanitizer.
// Skips with a stated reason when no playwright browser is installed, and fails with it where CI requires the leg
// (browser-leg.ts). Synthetic values only: invented addresses under example.test.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as path from "node:path";
import { createRequire } from "node:module";
import { launchLeg } from "./browser-leg";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const UI = path.resolve(EXT, "..", "ui", "webview");

const ENTRY = `
import { marked } from "marked";
import { chatMdExtensions } from "./chat-md";
import { sanitizeMd } from "./md-sanitize";
import { mdImgPostPass } from "./preview";
marked.setOptions({ gfm: true, breaks: false });
marked.use(...chatMdExtensions);
// md() as render.ts composes it, minus the PR-reference walk: sanitize, the chat's post-pass, serialize
function md(src: string): string { const clean = sanitizeMd(marked.parse(src) as string); mdImgPostPass(clean); return clean.innerHTML; }
(window as any).__md = md;
`;

function bundle(): string {
  const esbuild = requireCjs("esbuild");
  const r = esbuild.buildSync({
    stdin: { contents: ENTRY, resolveDir: UI, loader: "ts", sourcefile: "chat-video-entry.ts" },
    bundle: true, write: false, format: "iife", platform: "browser", target: "es2020",
    nodePaths: [path.join(EXT, "node_modules")], external: ["*.png", "*.svg", "*.woff", "*.ttf", "../media/*.woff2"], logLevel: "silent",
  });
  return r.outputFiles[0].text;
}

const PAGE_HTML = `<!DOCTYPE html><html><head><meta charset=utf-8></head><body><div id=out class=md></div>
<script src=/dist/video.js></script></body></html>`;

let pw: any = null;
try { pw = requireCjs("playwright"); } catch { pw = null; }

type Vid = { attrs: Record<string, string>; muted: boolean; autoplay: boolean; controls: boolean; still: { cls: string; video: string | null } | null;
  sources: Record<string, string>[] };

test("a chat video, sanitized and turned into the still: nothing an author writes runs, and only a playable address becomes a still", { timeout: 60000 }, async (t) => {
  const browser = await launchLeg(t, pw, "chromium");
  if (!browser) return;
  try {
    const js = bundle();
    const page = await browser.newPage();
    const errors: string[] = [];
    page.on("pageerror", (e: Error) => { errors.push(e.message); });
    let fetched: string[] = [];
    await page.route("**/*", (route: any) => {
      const u = new URL(route.request().url());
      if (u.host === "romp.test" && u.pathname === "/page") return route.fulfill({ status: 200, contentType: "text/html; charset=utf-8", body: PAGE_HTML });
      if (u.host === "romp.test" && u.pathname === "/dist/video.js") return route.fulfill({ status: 200, contentType: "application/javascript", body: js });
      fetched.push(u.href);
      return route.fulfill({ status: 404, body: "" });                  // no clip is needed: the attributes are what is read
    });
    await page.goto("http://romp.test/page");
    await page.waitForFunction(() => typeof (window as any).__md === "function", null, { timeout: 10000 });
    fetched = [];
    const M = "https://media.example.test/";
    const SRC = [
      `<video controls autoplay loop width="100%" height="900" src="${M}demo.mp4" onplay="window.__ran=1" onloadeddata="window.__ran=2" poster="javascript:window.__ran=3"></video>`,
      "",
      `The short take <video src="${M}short.webm" onerror="window.__ran=4"></video> runs mid-sentence.`,
      "",
      `<video controls src="javascript:window.__ran=5"></video>`,
      "",
      `<video controls autoplay src="data:video/mp4;base64,AAAA"></video>`,
      "",
      `<video src="blob:https://media.example.test/1234"></video>`,
      "",
      `<video controls><source src="javascript:window.__ran=6"><source src="${M}alt.webm" type="video/webm" onerror="window.__ran=7"></video>`,
      "",
      `<a href="${M}page"><video src="${M}linked.mp4"></video></a>`,
      "",
      `<details><summary><video src="${M}folded.mp4"></video> Raw take</summary>x</details>`,
      "",
      `<video src="${M}posted.mp4" poster="${M}poster.png"></video>`,
      "",
      // an author dressing a span as the still, with an address of their own: the sanitizer drops the data- attribute, so the
      // post-pass still wraps the video in a still of its own, which carries the video's real address
      `<span class="md-video-still" data-video="javascript:window.__ran=8"><video src="${M}dressed.mp4"></video></span>`,
    ].join("\n");
    const got: Vid[] = await page.evaluate((src: string) => {
      const out = document.getElementById("out")!;
      out.innerHTML = (window as any).__md(src);
      return Array.from(out.querySelectorAll("video")).map((v) => {
        const attrs: Record<string, string> = {};
        for (const a of Array.from(v.attributes)) attrs[a.name] = a.value;
        const w = v.parentElement && v.parentElement.classList.contains("md-video-still") ? v.parentElement : null;
        return { attrs, muted: v.muted, autoplay: v.autoplay, controls: v.controls,
          still: w ? { cls: w.className, video: w.getAttribute("data-video") } : null,
          sources: Array.from(v.querySelectorAll("source")).map((s) => { const o: Record<string, string> = {}; for (const a of Array.from(s.attributes)) o[a.name] = a.value; return o; }) };
      });
    }, SRC);
    await page.waitForTimeout(300);
    const ran = await page.evaluate(() => (window as any).__ran ?? null);
    const show = "\n" + JSON.stringify(got, null, 1);
    assert.equal(got.length, 10, "every video rendered" + show);
    for (const v of got) {
      for (const k of Object.keys(v.attrs)) assert.doesNotMatch(k, /^on/i, "no handler survives: " + k + show);
      for (const s of v.sources) for (const k of Object.keys(s)) assert.doesNotMatch(k, /^on/i, "no handler on a <source>: " + k + show);
      for (const val of [...Object.values(v.attrs), ...v.sources.flatMap((s) => Object.values(s))]) assert.doesNotMatch(val, /javascript:|blob:/i, val + show);
      assert.equal(v.autoplay, false, "no chat video plays by itself" + show);
      assert.equal(v.muted, true, "muted, as a property once parsed back" + show);
    }
    assert.equal(ran, null, "nothing an author wrote ran");
    const [demo, short, script, data, blob, sourced, linked, folded, posted, dressed] = got;
    assert.equal(dressed.still && dressed.still.video, M + "dressed.mp4", "an author's dressed span neither names the address nor stops the still" + show);
    const authored = await page.evaluate(() => Array.from(document.querySelectorAll("#out [data-video]")).map((e) => e.getAttribute("data-video")));
    assert.ok(authored.every((u: string | null) => /^https:\/\/media\.example\.test\//.test(u || "")), "every data-video on the page is the post-pass's own: " + authored.join(" "));
    // the still
    assert.deepEqual(demo.still, { cls: "md-video-still md-solo", video: M + "demo.mp4" }, "alone in its paragraph: a framed block; the address the lightbox opens, no fragment" + show);
    assert.equal(demo.controls, false, "no native controls on a still: they would fight the tap that opens the lightbox");
    assert.equal(demo.attrs.src, M + "demo.mp4#t=0.001", "the first-frame fragment on the still's own address (iOS paints no frame without it)");
    assert.equal(demo.attrs.preload, "metadata");
    assert.ok("playsinline" in demo.attrs && "muted" in demo.attrs, "playsinline and muted ride the serialization");
    assert.ok(!("loop" in demo.attrs) && !("poster" in demo.attrs), "no loop; the javascript: poster is gone");
    assert.equal(demo.attrs.width, "100%", "the author's width stays on the element (the still's CSS auto outranks it: the served lab measures that)");
    assert.equal(short.still && short.still.cls, "md-video-still", "mid-sentence: a still that stays on its line, no box" + show);
    assert.equal(posted.still && posted.still.video, M + "posted.mp4");
    assert.equal(posted.attrs.poster, M + "poster.png", "a web poster stays");
    assert.deepEqual(sourced.still, { cls: "md-video-still md-solo", video: M + "alt.webm" }, "named by its first surviving <source>" + show);
    assert.equal(sourced.sources[1].src, M + "alt.webm#t=0.001", "…whose address carries the fragment too");
    // the plain players: their own controls, never a still
    for (const [name, v] of [["javascript: src", script], ["data: clip", data], ["blob: src", blob], ["linked", linked], ["folded", folded]] as [string, Vid][]) {
      assert.equal(v.still, null, name + " is no still" + show);
      assert.equal(v.controls, true, name + " keeps its own controls, so it still plays in the message" + show);
    }
    assert.ok(!("src" in script.attrs), "DOMPurify dropped the javascript: src" + show);
    assert.ok(!("src" in blob.attrs), "…and the blob: one");
    assert.equal(data.attrs.src, "data:video/mp4;base64,AAAA", "a data: clip survives the sanitizer, and plays where it is, never in the lightbox");
    assert.ok(fetched.every((u) => !/^javascript:|^blob:/i.test(u)), "nothing fetched a script or a blob: " + fetched.join(" "));
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
});
