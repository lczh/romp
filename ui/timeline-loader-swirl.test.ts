// The timeline's loader wordmark paints its "o" from the page, not from a request (the user 2026-10-01, who saw
// R, m and p paint at once and the swirl land a beat later). The view reads __ROMP_SWIRL_O__, a data URI of
// media/romp-swirl-o.svg: the kernel's /timeline page declares it before injecting the view, and esbuild bakes
// it in when it bundles the view for VS Code. Run through the real constructor (which raises the loader) on a
// small headless DOM shim, the view required raw off disk the way the kernel page takes it. Synthetic only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { createRequire } from "node:module";
import * as path from "node:path";

declare const __ROMP_SWIRL_O__: string;   // the test bundle carries the shipped bundles' define (esbuild.js)

function makeNode(tag: string): any {
  const n: any = {
    tag, _attrs: {}, children: [] as any[], style: {}, dataset: {}, textContent: "", parentNode: null,
    classList: { _s: new Set<string>(), add(...a: string[]) { a.forEach((c) => this._s.add(c)); },
      remove(...a: string[]) { a.forEach((c) => this._s.delete(c)); },
      toggle(c: string, f?: boolean) { f ? this._s.add(c) : this._s.delete(c); }, contains(c: string) { return this._s.has(c); } },
    setAttribute(k: string, v: any) { this._attrs[k] = v; }, getAttribute(k: string) { return this._attrs[k]; },
    setAttributeNS(_n: any, k: string, v: any) { this._attrs[k] = v; }, removeAttribute(k: string) { delete this._attrs[k]; },
    appendChild(c: any) { c.parentNode = n; this.children.push(c); return c; },
    insertBefore(c: any, ref: any) { c.parentNode = n; const i = this.children.indexOf(ref); i < 0 ? this.children.push(c) : this.children.splice(i, 0, c); return c; },
    removeChild(c: any) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
    get firstChild() { return this.children[0] || null; },
    addEventListener() {}, removeEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    getBoundingClientRect() { return { width: 1400, height: 420, left: 0, top: 0, right: 1400, bottom: 420 }; },
    closest() { return null; }, focus() {},
    createEl(t: string, o: any) { const e = makeNode(t); if (o && o.cls) e.classList.add(o.cls); if (o && o.text) e.textContent = o.text; this.appendChild(e); return e; },
    createDiv(o: any) { return this.createEl("div", o); }, createSpan(o: any) { return this.createEl("span", o); },
  };
  return n;
}
const g: any = global;
g.document = {
  createElement(t: string) { return t === "canvas" ? { getContext() { return { font: "", measureText(s: string) { return { width: (s ? s.length : 0) * 6 }; } }; } } : makeNode(t); },
  createElementNS(_n: any, t: string) { return makeNode(t); },
  body: makeNode("body"), documentElement: makeNode("html"), head: makeNode("head"),
  getElementById() { return null; },
  addEventListener() {}, removeEventListener() {},
};
g.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
g.getComputedStyle = () => ({ backgroundColor: "rgb(30,30,30)" });
g.requestAnimationFrame = () => 0;
g.addEventListener = () => {}; g.removeEventListener = () => {};
g.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
g.window = g;
g.innerWidth = 1400; g.innerHeight = 800;

const { TimelinePanel } = createRequire(__filename)(path.resolve(process.cwd(), "..", "ui", "romp-timeline-view.js"));

function loaderWord(panel: any): any[] {
  const out: any[] = [];
  const walk = (n: any) => { if (n.className === "rl-word") out.push(...n.children); (n.children || []).forEach(walk); };
  walk(panel._loaderEl);
  return out;
}

test("with the inline swirl declared, the loader's o is that data URI, never a /media fetch", () => {
  g.__ROMP_SWIRL_O__ = __ROMP_SWIRL_O__;   // what the kernel page's script declares
  try {
    const word = loaderWord(new TimelinePanel(makeNode("div")));
    assert.deepEqual(word.map((n) => n.textContent || n.className), ["R", "rl-o", "m", "p"], "R, the swirl as the o, m, p");
    const o = word[1];
    assert.equal(o.tag, "img");
    assert.ok(__ROMP_SWIRL_O__.startsWith("data:image/svg+xml,%3Csvg"), "the define is the swirl's SVG");
    assert.equal(o.src, __ROMP_SWIRL_O__);
    assert.equal(o.alt, "o");
  } finally {
    delete g.__ROMP_SWIRL_O__;
  }
});

test("a host that declares nothing keeps the /media URL it always had", () => {
  const o = loaderWord(new TimelinePanel(makeNode("div")))[1];
  assert.equal(o.src, "/media/romp-swirl-o.svg");
});
