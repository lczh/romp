// Several Claude logins (T346, the user 2026-09-11): every Billing surface lists every login the host knows plus the
// API key, names WHICH login a session bills, and posts the pick as "login" | "key" | "login:<id>". render.ts has no
// jsdom harness, so these are source pins (the auth-selector.test.ts idiom); the kernel's halves are pinned in
// tests/test_login_records.py. The picker's rebuild is EXECUTED here on a small DOM stand-in.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { createRequire } from "node:module";

const requireCjs = createRequire(__filename);

const ROOT = path.resolve(process.cwd(), "..");
const RENDER = fs.readFileSync(path.join(ROOT, "ui", "webview", "render.ts"), "utf8");
const STYLES = fs.readFileSync(path.join(ROOT, "ui", "webview", "styles.css"), "utf8");
const GEAR = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.js"), "utf8");
const GEARCSS = fs.readFileSync(path.join(ROOT, "ui", "webview", "gear.css"), "utf8");
const FEED = fs.readFileSync(path.join(ROOT, "ui", "webview", "feed.ts"), "utf8");

test("the status and the availability reply carry WHICH login, typed", () => {
  assert.match(RENDER, /interface AuthLogin \{ id\?: string; value\?: string; label\?: string; machine\?: boolean; available\?: boolean; why\?: string; expiresSoon\?: boolean \}/);
  assert.match(RENDER, /interface AuthAvail \{ login\?: boolean; key\?: boolean; loginWhy\?: string; keyWhy\?: string; acct\?: string; default\?: string; defaultExplicit\?: boolean; logins\?: AuthLogin\[\] \}/);
  assert.match(RENDER, /authAcct\?: string; authLogin\?: string; authLabel\?: string; authLoginLive\?: string \| null; ctx\?: string;/);
});

test("a stored login the CLI did not use is said on the hover and the sub-line, from the init's evidence", () => {
  // authLoginLive "" = the CLI signed in with the machine's own login instead of the stored one's helper (the
  // kernel's _note_auth_source reads the init's source word); absent before an init, the record id when it answered
  assert.match(RENDER, /: \(s\.status\.auth === "login" && s\.status\.authLogin && s\.status\.authLoginLive === ""\)\s*\n\s*\? `⚠ Login \(\$\{loginName\(s\.status\)\}\) picked, but the CLI signed in with another credential: this session bills that`/);
  assert.match(RENDER, /: \(st\.auth === "login" && st\.authLogin && st\.authLoginLive === ""\)\s*\n\s*\? "⚠ CLI used another credential"/);
});

test("the tab menu's Billing submenu lists every login plus the key, the current one by WHICH login", () => {
  // the kernel's list when it sends one; the two-entry list for an older kernel, its literals intact
  assert.match(RENDER, /const all = billingChoices\(st, avail\);/, "the ONE list both menus draw from (T387)…");
  assert.match(RENDER, /const choices = all\.filter\(\(c\) => !c\.why\);/, "…filtered to the billings this machine can apply (the user 2026-09-14)");
  assert.match(RENDER, /function billingChoices\(st: Status, avail: AuthAvail\)[^\n]*\n(\s*\/\/[^\n]*\n)*\s*if \(avail\.logins && avail\.logins\.length\) return authLoginChoices\(avail\);/, "…grows with the stored logins (T346): every login this host knows plus the key; an older kernel keeps the two-entry list");
  assert.match(RENDER, /for \(const c of choices\) \{\s*\n\s*const cur = authChoiceCurrent\(st, c\.value\);/);
  // every login the kernel knows, `Login (<label>)`, greyed with its reason; then the key
  assert.match(RENDER, /label: l\.label \? `Login \(\$\{l\.label\}\)` : "Login",\s*\n\s*value: l\.value \|\| \(l\.id \? `login:\$\{l\.id\}` : "login"\),/);
  assert.match(RENDER, /out\.push\(\{ label: "API key", value: "key", why: avail\.key \? "" : \(avail\.keyWhy \|\| "no apiKeyHelper configured"\) \}\);/);
  // the current mark: the key, or a login by st.authLogin ("" = the machine's own)
  assert.match(RENDER, /return st\.auth === "login" && \("login" \+ \(st\.authLogin \? `:\$\{st\.authLogin\}` : ""\)\) === value;/);
  // the sub-line and the hover name the login by the kernel's label, the account name as the fallback: ONE wording,
  // authWords, which both read (2026-10-07, when both gained the billing's source)
  assert.match(RENDER, /function authWords\(st: Status\): string \{\s*\n\s*return st\.auth === "key" \? "API key" : \(loginName\(st\) \? `Login \(\$\{loginName\(st\)\}\)` : "Login"\);/);
  assert.match(RENDER, /: \(authFollows\(st\) \? `Machine default · \$\{authWords\(st\)\}` : authWords\(st\)\);/);
  assert.match(RENDER, /: authWords\(s\.status\) \+ \(authFollows\(s\.status\) \? " · machine default" : ""\)\]\);/);
});

test("the picker's Billing row keeps the two-option path and adds the many-login rebuild", () => {
  // the pinned two-option gate is untouched; a stored login can show the row when the machine has neither side
  assert.match(RENDER, /const show = !pickMode && !!\(a && \(a\.login \|\| a\.key\)\) && pickerBackendChoice\(\) === "sdk";/);
  assert.match(RENDER, /const stored = \(a && a\.logins \? a\.logins : \[\]\)\.filter\(\(l\) => !l\.machine\);/);
  assert.match(RENDER, /if \(stored\.length\) \{ syncPickerAuthMany\(wrap, a!\); return; \}/);
  // up to three choices: buttons (shrinking, an unavailable one disabled with its reason); beyond: one dropdown,
  // rebuilt only when the choice set changes; the pick kept by value; one usable choice written out
  assert.match(RENDER, /if \(choices\.length <= 3\) \{/);
  assert.match(RENDER, /b\.dataset\.auth = c\.value; b\.dataset\.many = "1";/);
  assert.match(RENDER, /if \(c\.why\) b\.disabled = true;/);
  assert.match(RENDER, /const sig = choices\.map\(\(c\) => c\.value \+ "\|" \+ c\.label \+ "\|" \+ c\.why\)\.join\("\\n"\);/);
  assert.match(RENDER, /if \(!dd \|\| dd\.dataset\.sig !== sig\) \{/);
  assert.match(RENDER, /if \(usable\.length <= 1\) \{/);
  // the create payload reads the dropdown when there is one
  assert.match(RENDER, /const dd = wrap\.querySelector\("select\.picker-auth-select"\) as HTMLSelectElement \| null;\s*\/\/ several logins \(T346\)\s*\n\s*if \(dd\) return dd\.value \|\| "";/);
  // styles: the buttons shrink with an ellipsis; the dropdown wears the menu tokens (dark literals as fallbacks only)
  assert.match(STYLES, /\.picker-auth \.picker-be-opt \{ flex: 0 1 auto; min-width: 0; max-width: 42%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; \}/);
  assert.match(STYLES, /\.picker-auth-select \{[^}]*background: var\(--menu-bg, #252526\); color: var\(--menu-fg, #cccccc\);/);
});

test("the picker rebuild runs: three choices are buttons, four are a dropdown, one usable choice is written out", () => {
  // lift authLoginChoices + syncPickerAuthMany + pickerAuthDropSelect + pickerAuthChoice, run them on a tiny DOM
  const lift = (name: string) => {
    const i = RENDER.indexOf(`function ${name}(`);
    assert.ok(i > 0, name);
    // the body's brace is the first one that opens a line (a return type's `{ label: ... }` sits inline)
    let depth = 0, j = RENDER.indexOf("{\n", i);
    for (; j < RENDER.length; j++) { if (RENDER[j] === "{") depth++; else if (RENDER[j] === "}") { depth--; if (!depth) break; } }
    return RENDER.slice(i, j + 1);
  };
  // a minimal element: classList, dataset, style, children, querySelector(All) over class + attribute selectors used here
  class El {
    tag: string; cls: Set<string>; dataset: Record<string, string> = {}; style: Record<string, string> = {}; children: El[] = [];
    textContent = ""; title = ""; type = ""; disabled = false; value = ""; parent: El | null = null;
    constructor(tag: string, cls = "") { this.tag = tag; this.cls = new Set(cls.split(" ").filter(Boolean)); }
    get classList() { const c = this.cls; return { toggle: (n: string, on: boolean) => { if (on) c.add(n); else c.delete(n); }, add: (n: string) => c.add(n), remove: (n: string) => c.delete(n), contains: (n: string) => c.has(n) }; }
    appendChild(e: El) { e.parent = this; this.children.push(e); return e; }
    insertBefore(e: El, ref: El | null) { e.parent = this; const i = ref ? this.children.indexOf(ref) : -1; if (i < 0) this.children.push(e); else this.children.splice(i, 0, e); return e; }
    remove() { if (this.parent) this.parent.children = this.parent.children.filter((x) => x !== this); }
    addEventListener() { /* no-op */ }
    all(): El[] { return this.children.flatMap((c) => [c, ...c.all()]); }
    matches(sel: string): boolean {
      // "select.picker-auth-select", ".picker-be-opt", ".picker-be-opt.sel", ".picker-be-opt[data-many]", ".picker-be-opt:not([data-many])", ".picker-auth-fixed", ".picker-be-opt[data-auth=\"login\"]"
      const m = /^([a-z]*)((?:\.[\w-]+)*)(\[data-([\w-]+)(?:="([^"]*)")?\])?(:not\(\[data-([\w-]+)\]\))?$/.exec(sel);
      if (!m) throw new Error("selector " + sel);
      if (m[1] && this.tag !== m[1]) return false;
      for (const c of (m[2] || "").split(".").filter(Boolean)) if (!this.cls.has(c)) return false;
      if (m[3]) { const v = this.dataset[m[4]]; if (v === undefined) return false; if (m[5] !== undefined && v !== m[5]) return false; }
      if (m[6] && this.dataset[m[7]] !== undefined) return false;
      return true;
    }
    querySelector(sel: string) { return this.all().find((e) => e.matches(sel)) || null; }
    querySelectorAll(sel: string) { return this.all().filter((e) => e.matches(sel)); }
  }
  const wrap = new El("div", "picker-backend picker-auth");
  wrap.style.display = "";
  const fixed = new El("span", "picker-auth-fixed"); fixed.style.display = "none";
  const b1 = new El("button", "picker-be-opt"); b1.dataset.auth = "login";
  const b2 = new El("button", "picker-be-opt"); b2.dataset.auth = "key";
  wrap.appendChild(b1); wrap.appendChild(b2); wrap.appendChild(fixed);
  const doc = { querySelector: (sel: string) => (sel === "#picker .picker-auth" ? wrap : null),
                createElement: (tag: string) => new El(tag) };
  const el = (tag: string, cls: string) => new El(tag, cls);
  const ts = [lift("authLoginChoices"), lift("syncPickerAuthMany"), lift("pickerAuthDropSelect"), lift("pickerAuthChoice")].join("\n");
  const src = requireCjs("esbuild").transformSync(ts, { loader: "ts", target: "es2020" }).code;   // the types go, the logic runs as shipped
  const fn = new Function("document", "el", src + "\nreturn { authLoginChoices, syncPickerAuthMany, pickerAuthChoice };");
  const api = fn(doc, el);
  const logins = [{ id: "", value: "login", label: "user@example.com · Acme · enterprise", machine: true, available: true },
                  { id: "0123456789ab", value: "login:0123456789ab", label: "Work · Acme · enterprise", available: true }];
  // three choices: buttons, the host's default selected, the static two hidden
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins, default: "login:0123456789ab" });
  const many = wrap.querySelectorAll(".picker-be-opt[data-many]");
  assert.deepEqual(many.map((b) => b.textContent), ["Login (user@example.com · Acme · enterprise)", "Login (Work · Acme · enterprise)", "API key"]);
  assert.equal(b1.style.display, "none"); assert.equal(fixed.style.display, "none");
  assert.equal(api.pickerAuthChoice(), "login:0123456789ab", "the host's remembered stored login seeds the pick");
  // the pick survives a re-sync by value
  many[2].classList.add("sel"); many[1].classList.remove("sel");
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins, default: "login:0123456789ab" });
  assert.equal(api.pickerAuthChoice(), "key");
  // an unavailable login is disabled with its reason and never seeded
  const refused = [...logins, { id: "abcdefabcdef", value: "login:abcdefabcdef", label: "Old", available: false, why: "the Old login was refused: expired" }];
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins: refused, default: "login:abcdefabcdef" });
  const dd = wrap.querySelector("select.picker-auth-select");
  assert.ok(dd, "four choices: a dropdown");
  assert.equal(dd!.children.length, 4);
  assert.equal(dd!.children[2].disabled, true);
  assert.match(dd!.children[2].textContent, /unavailable: the Old login was refused/);
  assert.equal(api.pickerAuthChoice(), "key", "the held pick stands; a refused default is never seeded");
  assert.equal(wrap.querySelectorAll(".picker-be-opt[data-many]").length, 0, "buttons gave way to the dropdown");
  const sig = dd!.dataset.sig;
  api.syncPickerAuthMany(wrap, { login: true, key: true, logins: refused, default: "login:abcdefabcdef" });
  assert.equal(wrap.querySelector("select.picker-auth-select"), dd, "an unchanged choice set keeps the open dropdown");
  assert.equal(dd!.dataset.sig, sig);
  // one usable choice: written out, the reasons in the hover
  api.syncPickerAuthMany(wrap, { login: false, loginWhy: "no Claude login signed in on this machine", key: false, keyWhy: "no apiKeyHelper configured",
    logins: [{ id: "", value: "login", label: "", machine: true, available: false }, logins[1]] });
  assert.equal(fixed.style.display, "");
  assert.equal(fixed.textContent, "Login (Work · Acme · enterprise)");
  assert.match(fixed.title, /Login unavailable: no Claude login signed in on this machine/);
  assert.equal(wrap.querySelector("select.picker-auth-select"), null);
});

// gear.js's stored-logins read and the link POST, lifted and run over a stand-in fetch (the lift idiom of the picker test above):
// the two var statements they share, lgDate, lgWhen, lgLogins, lgSignIn, lgPost, lgLater and lgFetchLogins, with document, fetch,
// ku, lgOwn, console, selfName and post standing in. Each GET and POST /logins and each fill's /tunnels read is a promise the
// test settles; drawn() reads the rows off the stand-in #rs-logins, and draws() counts its drawings
type Defer = { promise: Promise<unknown>; resolve: (v: unknown) => void; reject: (e: unknown) => void };
function defer(): Defer {
  const d = {} as Defer;
  d.promise = new Promise((resolve, reject) => { d.resolve = resolve; d.reject = reject; });
  return d;
}
const settle = async () => { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)); };
type Stub = { tagName: string; className: string; textContent: string; title: string; type: string; hidden: boolean; disabled: boolean;
  value: string; children: Stub[]; options: Stub[]; appendChild: (c: Stub) => Stub; addEventListener: () => void; remove: () => void };
function stub(tag: string): Stub {
  const n = { tagName: tag.toUpperCase(), className: "", textContent: "", title: "", type: "", hidden: false, disabled: false, value: "",
    children: [] as Stub[], addEventListener: () => {}, remove: () => {} } as Stub;
  n.options = n.children;
  n.appendChild = (c) => { n.children.push(c); return c; };
  return n;
}
function liftGear(head: string): string {
  const i = GEAR.indexOf(head);
  assert.ok(i >= 0 && GEAR.indexOf(head, i + 1) < 0, head + " occurs once in gear.js");
  if (head.startsWith("var ")) return GEAR.slice(i, GEAR.indexOf(";", i) + 1);
  let depth = 0, j = GEAR.indexOf("{", i);
  for (; j < GEAR.length; j++) { if (GEAR[j] === "{") depth++; else if (GEAR[j] === "}") { depth--; if (!depth) break; } }
  return GEAR.slice(i, j + 1);
}
const answer = (d: unknown) => ({ json: () => Promise.resolve(d) });
const notJson = () => ({ json: () => Promise.reject(new SyntaxError("not JSON")) });   // a 500 with a plain-text body
const named = (name: string) => ({ logins: [{ id: "0123456789ab", label: name, addedAt: 0 }] });
const LINK = { link: { login: "0123456789ab", sameAs: "TESTHOST" } }, LINK2 = { link: { login: "ba9876543210", sameAs: "TESTHOST" } };
function gearLogins() {
  const list = stub("div");
  let draws = 0;
  Object.defineProperty(list, "textContent", { get: () => "", set: () => { draws++; list.children.length = 0; } });
  const gets: Defer[] = [], posts: Defer[] = [], said: unknown[] = [], errors: unknown[] = [];
  const doc = { getElementById: (id: string) => (id === "rs-logins" ? list : null), createElement: (tag: string) => stub(tag) };
  const fetchStub = (_url: string, opts?: { method?: string }) => {
    const q = defer();
    (opts && opts.method === "POST" ? posts : gets).push(q);
    return q.promise;
  };
  const src = ["var lgL = ", "var lgBusy = ", "function lgDate(", "function lgWhen(", "function lgLogins(", "function lgSignIn(",
    "function lgPost(", "function lgLater(", "function lgFetchLogins("].map(liftGear).join("\n");
  const api = new Function("document", "fetch", "ku", "lgOwn", "console", "selfName", "post",
    src + "\nreturn { fill: lgFetchLogins, post: lgPost };")(doc, fetchStub, (p: string) => p,
    (d: any) => said.push(d && d.logins && d.logins[0] && d.logins[0].label), { error: (...a: unknown[]) => errors.push(a) }, "TESTHOST",
    () => {}) as { fill: (after?: Promise<unknown>) => unknown; post: (body: unknown, st: Stub, ctl: Stub, label: string) => void };
  // the rows drawn: each one's name and, where it has one, its picker's machines
  const drawn = () => list.children.filter((r) => r.className === "rs-login-row").map((r) => {
    const sel = r.children.find((c) => c.className === "rs-login-link");
    return { name: r.children[0].textContent, picker: sel ? sel.options.map((o) => o.value) : null };
  });
  const names = () => drawn().map((r) => r.name);
  // a fill run to its end, its /logins answered with `name` and its /tunnels read settled with no kernel: true if it drew
  const fillDraws = async (name: string) => {
    const t = defer(), n = gets.length, d0 = draws;
    api.fill(t.promise);
    gets[n].resolve(answer(named(name)));
    t.resolve([]);
    await settle();
    return draws > d0;
  };
  return { api, gets, posts, said, errors, drawn, names, fillDraws, draws: () => draws };
}

test("the gear lists the stored logins with a Remove and the feed names a refused login", async () => {
  assert.match(GEAR, /<div id=rs-logins class=rs-logins style='margin-top:8px'><\/div>/);
  // lgFetchLogins, run (gearLogins above)
  {
    // overlapping fills: each answer is said as it arrives; the rows wait for a /tunnels read; the newest fill draws, listing its
    // own read's kernels, and the older read settling after it changes neither the rows nor the list
    const g = gearLogins(), t1 = defer(), t2 = defer();
    g.api.fill(t1.promise);
    g.api.fill(t2.promise);
    g.gets[0].resolve(answer(named("first")));
    g.gets[1].resolve(answer(named("second")));
    await settle();
    assert.deepEqual(g.said, ["first", "second"], "each answer is said as it arrives (lgOwn), no /tunnels read settled");
    assert.equal(g.draws(), 0, "no rows are drawn before a /tunnels read settles");
    t2.resolve([{ host: "web-box" }]);
    await settle();
    assert.deepEqual(g.drawn(), [{ name: "second", picker: ["", "TESTHOST", "web-box"] }],
      "the newer fill draws once its /tunnels read settles, listing that read's kernels");
    t1.resolve([{ host: "api-box" }]);
    await settle();
    assert.equal(g.draws(), 1, "the older fill's read, settling after it, draws nothing");
    g.api.fill();
    g.gets[2].resolve(answer(named("third")));
    await settle();
    assert.deepEqual(g.drawn(), [{ name: "third", picker: ["", "TESTHOST", "web-box"] }],
      "a caller that draws at once lists the newer fill's kernels, not the list that settled last");
  }
  {
    // a fill whose own answer arrived first draws the newer answer, not its own
    const g = gearLogins(), t = defer();
    g.api.fill(t.promise);
    g.gets[0].resolve(answer(named("the fill's own")));
    await settle();
    g.api.fill();
    g.gets[1].resolve(answer(named("newer")));
    await settle();
    t.resolve([]);
    await settle();
    assert.equal(g.draws(), 2, "the caller that draws at once drew, then the fill once its /tunnels read settled");
    assert.deepEqual(g.names(), ["newer"], "…the newer answer, not its own");
  }
  {
    // `after` rejecting (fill()'s own catch threw) is logged, and the rows are drawn
    const g = gearLogins(), t = defer();
    g.api.fill(t.promise);
    g.gets[0].resolve(answer(named("one")));
    t.reject(new Error("the catch threw"));
    await settle();
    assert.equal(g.errors.length, 1, "the rejection is logged once");
    assert.deepEqual(g.names(), ["one"], "…and the rows are drawn");
  }
  {
    // two fills wait on a newer one whose read fails, the older settling last: nothing is drawn until the failed fill's /tunnels
    // read settles, then once; a later fill whose read fails, none waiting on it, draws nothing more
    const g = gearLogins(), t = [defer(), defer(), defer(), defer()];
    for (let k = 0; k < 3; k++) g.api.fill(t[k].promise);
    g.gets[0].resolve(answer(named("one")));
    g.gets[1].resolve(answer(named("two")));
    g.gets[2].resolve(notJson());
    t[1].resolve([]);
    await settle();
    t[0].resolve([]);
    await settle();
    assert.equal(g.draws(), 0, "fills 1 and 2 wait on fill 3, whose /tunnels read has not settled");
    t[2].resolve([]);
    await settle();
    assert.equal(g.draws(), 1, "once it has, the rows are drawn once");
    assert.deepEqual(g.names(), ["two"], "…from the newest answer");
    g.api.fill(t[3].promise);
    g.gets[3].resolve(notJson());
    t[3].resolve([]);
    await settle();
    assert.equal(g.draws(), 1, "a later fill whose read fails draws nothing, none waiting on it");
  }
  {
    // a caller that draws at once, then the only fill, whose /logins read fails: nothing more is drawn
    const g = gearLogins(), t = defer();
    g.api.fill();
    g.gets[0].resolve(answer(named("one")));
    await settle();
    assert.equal(g.draws(), 1, "the caller that draws at once drew");
    g.api.fill(t.promise);
    g.gets[1].resolve(notJson());
    t.resolve([]);
    await settle();
    assert.equal(g.draws(), 1, "the only fill, its read failed, draws nothing");
  }
  {
    // while a link is on its way, a fill's drawing is skipped, and so is a waiting fill's when the fill it waits on gives up; a
    // caller that draws at once still draws
    const g = gearLogins(), t = [defer(), defer()];
    g.api.post(LINK, stub("span"), stub("select"), "");
    assert.equal(await g.fillDraws("one"), false, "a fill's drawing is skipped while a link is on its way");
    g.api.fill(t[0].promise);
    g.api.fill(t[1].promise);
    g.gets[1].resolve(answer(named("two")));
    g.gets[2].resolve(notJson());
    t[0].resolve([]);
    await settle();
    t[1].resolve([]);
    await settle();
    assert.equal(g.draws(), 0, "so is a waiting fill's, when the fill it waits on gives up");
    g.api.fill();
    g.gets[3].resolve(answer(named("three")));
    await settle();
    assert.deepEqual(g.names(), ["three"], "a caller that draws at once still draws");
  }
  {
    // a fill whose /tunnels read settles while a link is on its way still waits on a newer fill, and draws when that fill gives up
    // after the link's refusal
    const g = gearLogins(), t1 = defer(), t2 = defer();
    g.api.fill(t1.promise);
    g.api.fill(t2.promise);
    g.gets[0].resolve(answer(named("one")));
    g.gets[1].resolve(notJson());
    g.api.post(LINK, stub("span"), stub("select"), "");
    t1.resolve([]);
    await settle();
    g.posts[0].resolve(answer({ ok: false, error: "refused" }));
    await settle();
    t2.resolve([]);
    await settle();
    assert.deepEqual(g.names(), ["one"], "the waiting fill draws once the newer fill gives up");
  }
  {
    // the newer fill's /logins read fails and its `after` rejects (fill()'s own catch threw): the fill waiting on it draws
    const g = gearLogins(), t1 = defer(), t2 = defer();
    g.api.fill(t1.promise);
    g.api.fill(t2.promise);
    g.gets[0].resolve(answer(named("one")));
    g.gets[1].resolve(notJson());
    t1.resolve([]);
    await settle();
    assert.equal(g.draws(), 0, "the older fill waits on the newer one");
    t2.reject(new Error("the catch threw"));
    await settle();
    assert.deepEqual(g.names(), ["one"], "the waiting fill draws once the newer fill gives up on a rejected `after`");
  }
  assert.match(GEAR, /function lgLater\(n\) \{[^\n]*\n\s*for \(var k = n \+ 1; k <= lgFills; k\+\+\) if \(!lgFailed\[k\]\) return true;\n\s*return false;/,
    "…a later fill counts until its /logins read has failed and its /tunnels read has settled");
  assert.match(GEAR, /\}\)\.catch\(function \(e\) \{[^\n]*return null; \}\)[^\n]*\n\s*\.then\(function \(rows\) \{\n\s*var up = \(rows \|\| \[\]\)\.filter\(function \(t\) \{ return t && t\.status === 'up'; \}\);\n\s*if \(n <= clickFill\) return up;[^\n]*\n\s*if \(n < drawnFill\) \{ if \(reread && !p\.hidden\) fill\(\); return up; \}[^\n]*\n(\s*[^\n]*drawKernelRows[^\n]*\n){2}\s*return up;/,
    "fill()'s /tunnels read settles with the connected kernels, or none when it failed, for the pickers to list (drawn or not: a fill a click or a later fill outdated draws nothing)");
  assert.match(GEAR, /var kernelsRead = fetch\(ku\('\/tunnels' \+ q\), \{ cache: 'no-store' \}\)/, "fill() keeps its /tunnels read to hand on");
  assert.match(GEAR, /lgRender\(v\);[^\n]*\n\s*lgFetchLogins\(kernelsRead\);/, "…and hands it to the stored logins' read, beside /version's");
  assert.match(GEAR, /post\(\{ type: 'loginRemove', id: r\.id \}\);\s*\n\s*row\.remove\(\);/);
  assert.match(GEAR, /'No other Claude logins stored on this machine\. Add one with romp login add <label>\.'/);
  assert.match(GEARCSS, /\.rs-logins \.rs-login-row \{ display: flex; align-items: center; gap: 8px;/);
  assert.match(GEAR, /'<span style="flex:1 1 auto;min-width:0"><b>[^<]*<\/b>' \+\n\s*'<span class=rs-sub id=rs-login-acct>/,
    "the Account row's span shrinks to the row, so the rows' names ellipsize (tests/test_login_link_picker_served.py measures each row's Remove against the card)");
  assert.match(FEED, /it\.blocked\.login \? `⚠ Can't analyze · \$\{it\.blocked\.login\}`\s*\n\s*: it\.blocked\.mode === "key" \? "⚠ Can't analyze · API key" : "⚠ Can't analyze · login";/);
  assert.match(FEED, /mode\?: string; login\?: string; since\?: number;/);
});

test("the gear's stored-login row links a login to a machine's sign-in and unlinks it, said as the user's statement (2026-10-01)", async () => {
  // linked: the note names the machine and that the link is the user's, and an Unlink takes it back
  assert.match(GEAR, /if \(ln\) note = \(note \? note \+ ' \\u00b7 ' : ''\) \+ 'same account as ' \+ lgSignIn\(ln\.host\) \+ ', linked by you';/);
  assert.match(GEAR, /lgPost\(\{ unlink: r\.id \}, st, ul, 'Unlink'\);/);
  // not linked, and the record names no account of its own: a Same account as… picker over this machine and every connected kernel
  assert.match(GEAR, /\} else if \(!r\.ownAccount\) \{/);
  assert.match(GEAR, /\[selfName \|\| 'local'\]\.concat\(lgKernels\.map\(function \(k\) \{ return k\.host; \}\)\)/,
    "the picker lists the kernels of the newest settings fill whose /tunnels read has settled (tests/test_login_link_picker_served.py reads it after overlapping opens)");
  assert.match(GEAR, /lgPost\(\{ link: \{ login: r\.id, sameAs: sel\.value \} \}, st, sel, ''\);/);
  // the kernel's answer decides: ok re-reads the list, a refusal is said in the row with the control restored
  assert.match(GEAR, /fetch\(ku\('\/logins'\), \{ method: 'POST', headers: \{ 'Content-Type': 'application\/json' \}, body: JSON\.stringify\(body\) \}\)/);
  // lgPost and its count of links and unlinks on their way, run (gearLogins above)
  {
    const g = gearLogins(), st = stub("span"), sel = stub("select");
    sel.disabled = true;
    sel.value = "TESTHOST";
    g.api.post(LINK, st, sel, "");
    assert.equal(await g.fillDraws("during"), false, "a fill's drawing is skipped from the send");
    g.posts[0].resolve(answer({ ok: false, error: "no sign-in there" }));
    await settle();
    assert.deepEqual([st.className, st.textContent, sel.disabled, sel.value], ["rs-note rs-login-bad", "no sign-in there", false, ""],
      "a refusal is said in the row, the picker enabled at its placeholder");
    assert.equal(g.draws(), 0, "…and nothing is drawn over the refusal");
    assert.equal(await g.fillDraws("after"), true, "…and the next fill draws");
  }
  {
    const g = gearLogins(), st = stub("span"), ul = stub("button");
    ul.disabled = true;
    g.api.post({ unlink: "0123456789ab" }, st, ul, "Unlink");
    g.posts[0].resolve(notJson());
    await settle();
    assert.deepEqual([st.textContent, ul.disabled, ul.textContent], ["the kernel did not answer; try again", false, "Unlink"],
      "no answer is said in the row, the button restored");
    assert.equal(await g.fillDraws("after"), true, "…and the next fill draws");
  }
  {
    // an ok: a fill settling while its re-read is on its way is skipped; the re-read draws once, and then the next fill draws
    const g = gearLogins();
    g.api.post(LINK, stub("span"), stub("select"), "");
    g.posts[0].resolve(answer({ ok: true }));
    await settle();
    assert.equal(g.gets.length, 1, "the ok re-reads the list");
    assert.equal(await g.fillDraws("during"), false, "a fill settling while the re-read is on its way is skipped");
    g.gets[0].resolve(answer(named("re-read")));
    await settle();
    assert.equal(g.draws(), 1, "the re-read draws, once");
    assert.deepEqual(g.names(), ["re-read"]);
    assert.equal(await g.fillDraws("after"), true, "…and the next fill draws");
  }
  {
    // oks whose re-reads fail after a fill's drawing was skipped: the rows are drawn once no link or unlink is on its way, not while
    // another link is
    const g = gearLogins();
    g.api.post(LINK, stub("span"), stub("select"), "");
    g.api.post(LINK2, stub("span"), stub("select"), "");
    assert.equal(await g.fillDraws("skipped"), false, "a fill's drawing is skipped while the two links are on their way");
    g.posts[0].resolve(answer({ ok: true }));
    await settle();
    g.gets[1].resolve(notJson());
    await settle();
    assert.equal(g.draws(), 0, "the first link's re-read fails, the second link still on its way: nothing is drawn");
    g.posts[1].resolve(answer({ ok: true }));
    await settle();
    g.gets[2].resolve(notJson());
    await settle();
    assert.equal(g.draws(), 1, "the second link's re-read fails too: the rows are drawn once");
    assert.deepEqual(g.names(), ["skipped"], "…from the newest answer");
  }
  {
    // the same when the skipped drawing was a waiting fill's
    const g = gearLogins(), t1 = defer(), t2 = defer();
    g.api.fill(t1.promise);
    g.api.fill(t2.promise);
    g.gets[0].resolve(answer(named("waiting")));
    g.gets[1].resolve(notJson());
    t1.resolve([]);
    await settle();
    g.api.post(LINK, stub("span"), stub("select"), "");
    t2.resolve([]);
    await settle();
    assert.equal(g.draws(), 0, "the waiting fill's drawing is skipped while the link is on its way");
    g.posts[0].resolve(answer({ ok: true }));
    await settle();
    g.gets[2].resolve(notJson());
    await settle();
    assert.deepEqual(g.names(), ["waiting"], "the link's re-read fails: the rows are drawn from the newest answer");
  }
  {
    // a waiting fill draws when the fill it waits on gives up; a link's ok whose re-read fails then draws nothing more
    const g = gearLogins(), t1 = defer(), t2 = defer();
    g.api.fill(t1.promise);
    g.api.fill(t2.promise);
    g.gets[0].resolve(answer(named("waiting")));
    g.gets[1].resolve(notJson());
    t1.resolve([]);
    await settle();
    t2.resolve([]);
    await settle();
    assert.equal(g.draws(), 1, "the waiting fill draws once the fill it waits on gives up");
    g.api.post(LINK, stub("span"), stub("select"), "");
    g.posts[0].resolve(answer({ ok: true }));
    await settle();
    g.gets[2].resolve(notJson());
    await settle();
    assert.equal(g.draws(), 1, "…then the link's re-read fails: nothing more is drawn");
  }
  {
    // a refusal after the skip: the other link's ok with a failed re-read draws nothing, so the refusal stays shown
    const g = gearLogins(), st = stub("span");
    g.api.post(LINK, st, stub("select"), "");
    g.api.post(LINK2, stub("span"), stub("select"), "");
    assert.equal(await g.fillDraws("skipped"), false, "a fill's drawing is skipped while the two links are on their way");
    g.posts[0].resolve(answer({ ok: false, error: "no sign-in there" }));
    await settle();
    g.posts[1].resolve(answer({ ok: true }));
    await settle();
    g.gets[1].resolve(notJson());
    await settle();
    assert.equal(st.textContent, "no sign-in there", "the first link's refusal is shown");
    assert.equal(g.draws(), 0, "the second link's re-read fails: nothing is drawn over it");
  }
  {
    // no answer after the skip: nothing is drawn over the note
    const g = gearLogins(), st = stub("span");
    g.api.post(LINK, st, stub("select"), "");
    assert.equal(await g.fillDraws("skipped"), false, "a fill's drawing is skipped while the link is on its way");
    g.posts[0].resolve(notJson());
    await settle();
    assert.equal(st.textContent, "the kernel did not answer; try again", "no answer is said in the row");
    assert.equal(g.draws(), 0, "…and nothing is drawn over the no-answer note");
  }
  {
    // no answer after the skip, then the other link's ok with a failed re-read: nothing is drawn over the note
    const g = gearLogins(), st = stub("span");
    g.api.post(LINK, st, stub("select"), "");
    g.api.post(LINK2, stub("span"), stub("select"), "");
    assert.equal(await g.fillDraws("skipped"), false, "a fill's drawing is skipped while the two links are on their way");
    g.posts[0].resolve(notJson());
    await settle();
    g.posts[1].resolve(answer({ ok: true }));
    await settle();
    g.gets[1].resolve(notJson());
    await settle();
    assert.equal(st.textContent, "the kernel did not answer; try again", "the first link's no answer is said in its row");
    assert.equal(g.draws(), 0, "the second link's re-read fails: nothing is drawn over the no-answer note");
  }
  assert.match(GEAR, /go\.hidden = true; sel\.disabled = true; st\.className = 'rs-note'; st\.textContent = 'Linking\\u2026';/,
    "acknowledged before the round-trip, Link gone (tests/test_login_link_picker_served.py presses Enter again while the link is in flight)");
  // the pick only chooses and Link sends it (review of #78: in the Linux builds of Chromium, Firefox and WebKit, a focused, closed
  // select fires change on an arrow key). Where the send is hung and the pick kept, not what a key does:
  // tests/test_login_link_picker_served.py presses ArrowDown, ArrowUp back to the placeholder (Link goes), ArrowDown again and
  // then Link from the keyboard; it reads that a pick taken back with the placeholder is not put back by a redraw, and that a
  // reopen after a refused link shows no pick and no Link (Link dropped the pick)
  assert.match(GEAR, /sel\.addEventListener\('change', function \(\) \{\n\s*go\.hidden = !sel\.value;\n\s*if \(sel\.value\) lgPicks\[r\.id\] = sel\.value; else delete lgPicks\[r\.id\];\n\s*\}\);\n\s*go\.addEventListener\('click', function \(\) \{\n\s*if \(!sel\.value\) return;\n\s*delete lgPicks\[r\.id\];\n\s*go\.hidden = true;/,
    "change only shows or hides Link and keeps the pick by login; the link POST is sent from Link's click, which drops the kept pick");
  // where the redraw takes a kept pick back, not that it does: tests/test_login_link_picker_served.py toggles a synchronized
  // switch, picks a machine, and reads the pick and Link after each of two re-run drawings, then presses Link; it reads the pick
  // after a reopen of settings, and reads it dropped when its machine has left the kernel list and when its login was linked
  // elsewhere and unlinked before the rows were drawn again (the reset below)
  assert.match(GEAR, /var kept = picks\[r\.id\];\n\s*if \(kept && Array\.prototype\.some\.call\(sel\.options, function \(o\) \{ return o\.value === kept; \}\)\) \{\n\s*sel\.value = kept; go\.hidden = false; lgPicks\[r\.id\] = kept;/,
    "a redrawn row takes back the pick its predecessor held while that machine is still listed, with Link showing");
  assert.match(GEAR, /var picks = lgPicks;[^\n]*\n\s*lgPicks = \{\};\n\s*lgL\.textContent = '';/, "…from the picks the replaced rows held, so a login no longer drawn keeps none");
  assert.match(GEARCSS, /\.rs-logins \.rs-login-link \{ flex: 0 0 auto;/);
});
