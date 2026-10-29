// A picture named by path shows the bytes of the message that named it (2026-10-29). The path-image chips (a user
// message's pictures on every surface; in VS Code also the reply's figures) fetch their bytes from the host with
// imgRequest and cache the data URL. The cache was keyed by (session, path) alone, so while a page was open the first
// bytes fetched for a path served every later message naming it: an agent regenerated a plot under the same name, and
// the newer message showed the old picture. And no ask named the message's mention-time pin (pathPins), so a fresh page
// read the live file into every older message. The chips now key on (session, path, pin) and ask for the pin; the
// kernel answers with that snapshot (tests/test_mention_pins.py ImgRequestPins).
// buildPathImg, onImgData, healPathImgs and userImage are lifted out of render.ts verbatim, transpiled and run over a
// small fake DOM. Synthetic world: session web of the notes-api demo, one plot the agent regenerates.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";
import { bookkeepingKey } from "./federation";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const requireCjs = createRequire(path.join(EXT, "package.json"));
const RENDER = fs.readFileSync(path.resolve(EXT, "..", "ui", "webview", "render.ts"), "utf8");

function lift(start: string, end: string): string {
  const a = RENDER.indexOf(start), b = RENDER.indexOf(end, a);
  assert.ok(a > 0 && b > a, "anchor not found in render.ts: " + start);
  return requireCjs("esbuild").transformSync(RENDER.slice(a, b), { loader: "ts", target: "es2020" }).code;
}

const WEB = "7d0c2b1a-5e4f-4a3b-9c8d-0e1f2a3b4c5d";
const P = "~/notes-api/out/latency.png";
const V1 = "data:image/png;base64,VjEtYnl0ZXM=";   // the plot as the 10:00 message showed it
const V2 = "data:image/png;base64,VjItYnl0ZXM=";   // the regenerated plot, as the 10:30 message showed it
const PIN1 = "a".repeat(64) + ".png";             // the kernel's mention-time pins (sha256 + extension)
const PIN2 = "b".repeat(64) + ".png";

type Fake = { tagName: string; className: string; dataset: Record<string, string>; children: Fake[]; src?: string; textContent: string };
type World = {
  posts: any[];
  userImage: (im: { src: string; path?: string }, pathInText?: boolean, pin?: string) => Fake;
  buildPathImg: (p: string, sid: string | null, pin?: string | null) => Fake;
  onImgData: (p: string, url: string | null, sid: string | null, pin?: string | null) => void;
  heal: () => void;
};

function world(): World {
  const H = { posts: [] as any[], chips: [] as Fake[] };
  const js = lift("const imgKey = ", "// RECONNECT-class heal for the path-image chips")
           + lift("function healPathImgs(): void {", "// the page shim fires romp:wsup")
           + lift("function userImage(", "function imgCaption(");
  const prelude = `
    let renderingOwnerSid = null; const activeId = null;
    class FakeEl {
      constructor(tag) { this.tagName = tag.toUpperCase(); this.className = ""; this.dataset = {}; this.children = []; this._t = ""; }
      set textContent(v) { this.children = []; this._t = v; }
      get textContent() { return this._t; }
      appendChild(c) { this.children.push(c); return c; }
    }
    const document = {
      createElement: (t) => new FakeEl(t),
      querySelectorAll: (sel) => { if (sel !== ".js-pathimg") throw new Error("unexpected selector " + sel); return H.chips.slice(); },
    };
    const el = (tag, cls) => { const e = new FakeEl(tag); e.className = cls || ""; if ((cls || "").split(" ").includes("js-pathimg")) H.chips.push(e); return e; };
    const vscodeApi = { postMessage: (m) => { H.posts.push(m); } };
    const imgCaption = (p) => { const e = new FakeEl("span"); e.className = "img-caption"; return e; };
  `;
  const api = new Function("H", prelude + js + `
    renderingOwnerSid = ${JSON.stringify(WEB)};
    return { userImage, buildPathImg, onImgData, heal: healPathImgs };`)(H);
  return { posts: H.posts, ...api };
}

/** What a chip shows: the picture's data URL, "pending" while its ask is out, or "chip" after a failure. */
function shown(wrap: Fake): string {
  const first = wrap.children[0];
  if (!first) return "nothing";
  if (first.tagName === "IMG") return first.src === V1 ? "V1" : first.src === V2 ? "V2" : String(first.src);
  return first.className.includes("img-pending") ? "pending" : "chip";
}
const pic = (fig: Fake) => fig.children[0];   // userImage's figure → its path-image chip
const img = { src: "path:" + P, path: P };

test("re-rendering the same message reuses its picture and asks nothing new", () => {
  const w = world();
  const m1 = w.userImage(img, true, PIN1);
  assert.equal(w.posts.length, 1);
  assert.deepEqual(w.posts[0], { type: "imgRequest", path: P, id: WEB, pin: PIN1 }, "the ask names the message's pin");
  w.onImgData(P, V1, WEB, PIN1);
  assert.equal(shown(pic(m1)), "V1");
  assert.equal(shown(pic(w.userImage(img, true, PIN1))), "V1");
  assert.equal(w.posts.length, 1, "a rebuild of the same message is served from the cache");
});

test("a later message naming the regenerated file asks again and shows its own picture; the older keeps v1", () => {
  const w = world();
  const m1 = w.userImage(img, true, PIN1);                       // 10:00 'see ~/notes-api/out/latency.png'
  w.onImgData(P, V1, WEB, PIN1);
  const m2 = w.userImage(img, true, PIN2);                       // the plot regenerated; 10:30 names it again
  assert.equal(w.posts.length, 2, "the new pin is a new picture: one more ask");
  assert.deepEqual(w.posts[1], { type: "imgRequest", path: P, id: WEB, pin: PIN2 });
  w.onImgData(P, V2, WEB, PIN2);
  assert.equal(shown(pic(m2)), "V2", "the 10:30 message shows the plot it was sent with");
  assert.equal(shown(pic(m1)), "V1", "the 10:00 message keeps its picture");
  assert.equal(shown(pic(w.userImage(img, true, PIN1))), "V1", "and so does its rebuild");
});

test("on a fresh page the older message's ask names its pin, so it never reads the live file", () => {
  const w = world();
  w.userImage(img, true, PIN1);
  w.userImage(img, true, PIN2);
  assert.deepEqual(w.posts.map((m) => m.pin), [PIN1, PIN2], "one ask per message, each naming its own snapshot");
});

test("an optimistic echo's thumbnail carries into the landed bubble with no pending flash", () => {
  const w = world();
  const echo = w.userImage(img, true);                           // the echo: no pin yet, the live file
  assert.deepEqual(w.posts[0], { type: "imgRequest", path: P, id: WEB, pin: "" });
  w.onImgData(P, V2, WEB, "");
  assert.equal(shown(pic(echo)), "V2");
  const landed = w.userImage(img, true, PIN2);                   // the landed message carries its pin
  assert.equal(shown(pic(landed)), "V2", "the echo's picture stands in while the pinned ask is out");
  assert.equal(w.posts.length, 2, "…and the message still asks for its own bytes");
  w.onImgData(P, V2, WEB, PIN2);
  assert.equal(shown(pic(landed)), "V2");
});

test("a failed pinned ask shows the plain chip, not the stand-in, and a reconnect re-asks with the pin", () => {
  const w = world();
  w.userImage(img, true);
  w.onImgData(P, V2, WEB, "");                                   // a live picture of the path is cached
  const m1 = w.userImage(img, true, PIN1);
  w.onImgData(P, null, WEB, PIN1);
  assert.equal(shown(pic(m1)), "chip", "a failure says so; the live picture is not the message's");
  w.heal();
  assert.deepEqual(w.posts[w.posts.length - 1], { type: "imgRequest", path: P, id: WEB, pin: PIN1 });
  w.onImgData(P, V1, WEB, PIN1);
  assert.equal(shown(pic(m1)), "V1");
});

test("an older kernel's reply (no pin echo) still fills the pinned chip, never a dead one", () => {
  const w = world();
  const m1 = w.userImage(img, true, PIN1);
  w.onImgData(P, V2, WEB, null);                                 // it read the live file and echoed no pin
  assert.equal(shown(pic(m1)), "V2");
  assert.equal(shown(pic(w.userImage(img, true, PIN1))), "V2", "the ask is answered, not left in flight");
  assert.equal(w.posts.length, 1);
});

test("the figures and pictures pass the message's pin, and the reply's pin reaches onImgData", () => {
  // a user message's picture: pathPins is keyed by the open target, which pathLinks maps the path to
  assert.match(RENDER, /const pins = ev\.pathPins \|\| \{\}, links = ev\.pathLinks \|\| \{\};\s*\n\s*for \(const im of ev\.images\) bubble\.appendChild\(userImage\(im, !!\(im\.path && mdText\.includes\(im\.path\)\), im\.path \? pins\[links\[im\.path\] \|\| im\.path\] : undefined\)\);/);
  // a reply's figure in VS Code, the same pin the web figure's previewFull gets
  assert.match(RENDER, /buildPathImg\(p, renderingOwnerSid \?\? activeId, \(pathPins \|\| \{\}\)\[p\]\)/);
  assert.match(RENDER, /onImgData\(m\.path, typeof m\.url === "string" \? m\.url : null, typeof m\.sid === "string" \? m\.sid : null, typeof m\.pin === "string" \? m\.pin : null\);/);
});

test("a held ask for each pin stands on its own while a remote host's socket is closed", () => {
  const a = bookkeepingKey({ type: "imgRequest", id: WEB, path: P, pin: PIN1 });
  const b = bookkeepingKey({ type: "imgRequest", id: WEB, path: P, pin: PIN2 });
  assert.ok(a && b && a !== b, "two messages' versions of one file are two asks");
  assert.equal(bookkeepingKey({ type: "imgRequest", id: WEB, path: P }), bookkeepingKey({ type: "imgRequest", id: WEB, path: P, pin: "" }));
});
