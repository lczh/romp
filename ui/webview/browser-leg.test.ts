// browser-leg.ts: what a browser leg of npm test does when it cannot launch its engine, held for every file that loads
// playwright, and the workflow order that makes CI's run of them real. Until 2026-10-14 CI's unit job ran its Test step
// before it installed Chromium, so every one of these legs skipped there, each under a reason that blamed CI for having
// no browser; the switch turns such a skip into a failure where the runner says the leg must run. Synthetic values only.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import * as cp from "node:child_process";
import { createRequire } from "node:module";
import { REQUIRE_ENV, ENGINES_ENV, legRequired, skipLeg, launchLeg, measuredSkip } from "./browser-leg";

const EXT = process.cwd();                                        // npm test runs in vscode-extension
const REPO = path.resolve(EXT, "..");
const requireCjs = createRequire(path.join(EXT, "package.json"));

const ON = { [REQUIRE_ENV]: "1", [ENGINES_ENV]: "chromium" };
const fakeT = () => { const skips: string[] = []; return { skips, skip: (m?: string) => { skips.push(String(m)); } }; };

test("a leg is required only with the switch on, for an engine the runner declares, or for any engine when it declares none", () => {
  assert.equal(legRequired("chromium", {}), false, "off by default");
  assert.equal(legRequired("chromium", { [REQUIRE_ENV]: "0", [ENGINES_ENV]: "chromium" }), false, "only 1 turns it on");
  assert.equal(legRequired("chromium", ON), true);
  assert.equal(legRequired("firefox", ON), false, "an engine the runner does not install stays optional");
  assert.equal(legRequired("Firefox", { [REQUIRE_ENV]: "1", [ENGINES_ENV]: " chromium , firefox " }), true, "a list, case and spaces aside");
  assert.equal(legRequired("webkit", { [REQUIRE_ENV]: "1" }), true, "no list declared: every engine must run");
});

test("off, a leg that cannot run skips with its reason; on, it fails with it; on for an undeclared engine, it skips as optional", () => {
  const off = fakeT();
  skipLeg(off, "chromium", "no browser on this synthetic box", {});
  assert.deepEqual(off.skips, ["no browser on this synthetic box"]);

  const on = fakeT();
  assert.throws(() => skipLeg(on, "chromium", "no browser on this synthetic box", ON),
    (e: Error) => e.message === `${REQUIRE_ENV}=1 and this chromium leg cannot run: no browser on this synthetic box`);
  assert.deepEqual(on.skips, [], "a required leg never reports a skip");

  const other = fakeT();
  skipLeg(other, "firefox", "no firefox on this synthetic box", ON);
  assert.deepEqual(other.skips, [`optional: this runner declares no firefox (${ENGINES_ENV}=chromium): no firefox on this synthetic box`]);
});

test("launchLeg hands back the launched browser, or skips or fails with the reason: no package, or the launch's own first line", async () => {
  const fake = { chromium: { launch: async () => "a browser" }, firefox: { launch: async () => { throw new Error("Executable doesn't exist\nmore detail"); } } };
  assert.equal(await launchLeg(fakeT(), fake, "chromium", ON), "a browser");

  const noPkg = fakeT();
  assert.equal(await launchLeg(noPkg, null, "chromium", {}), null);
  assert.match(noPkg.skips[0], /^playwright is not installed under vscode-extension/);
  await assert.rejects(launchLeg(fakeT(), null, "chromium", ON), new RegExp(`^AssertionError.*${REQUIRE_ENV}=1 and this chromium leg cannot run: playwright is not installed`));

  const noFox = fakeT();
  assert.equal(await launchLeg(noFox, fake, "firefox", ON), null);
  assert.equal(noFox.skips[0], `optional: this runner declares no firefox (${ENGINES_ENV}=chromium): no playwright firefox on this machine (npx playwright install firefox under vscode-extension): Executable doesn't exist`);
  await assert.rejects(launchLeg(fakeT(), fake, "firefox", { [REQUIRE_ENV]: "1", [ENGINES_ENV]: "chromium,firefox" }), /this firefox leg cannot run: no playwright firefox on this machine .*: Executable doesn't exist$/);
});

test("a measured file's skip option: false when the measurement ran, the reason when not, and where required one more test that fails with it", () => {
  const registered: Array<{ name: string; fn: () => void }> = [];
  const register = (name: string, fn: () => void) => { registered.push({ name, fn }); };
  assert.equal(measuredSkip("chromium", false, ON, register), false, "measured: nothing skips, nothing added");
  assert.equal(measuredSkip("chromium", "no driver result here", {}, register), "no driver result here");
  assert.equal(registered.length, 0, "off: the measured tests skip and nothing fails");
  assert.match(String(measuredSkip("firefox", "no driver result here", ON, register)), /^optional: this runner declares no firefox/);
  assert.equal(registered.length, 0, "an undeclared engine adds nothing");
  assert.equal(measuredSkip("chromium", "no driver result here", ON, register), "no driver result here", "the measured tests still skip");
  assert.equal(registered.length, 1, "and one test is added");
  assert.equal(registered[0].name, `the chromium measurement ran (${REQUIRE_ENV}=1)`);
  assert.throws(registered[0].fn, (e: Error) => e.message === `${REQUIRE_ENV}=1 and this chromium leg cannot run: no driver result here`);
});

test("through node --test with real playwright and no browser on disk: the switch turns the leg's skip into a failure naming the reason", { timeout: 120_000 }, () => {
  // a one-leg test file bundled from this module, run in a child under an empty browsers directory, as the CI runner
  // would be if its install step went missing
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "browser-leg-"));
  try {
    const src = `import { test } from "node:test";
import { createRequire } from "node:module";
import { launchLeg } from ${JSON.stringify(path.join(REPO, "ui", "webview", "browser-leg"))};
let pw = null; try { pw = createRequire(${JSON.stringify(path.join(EXT, "package.json"))})("playwright"); } catch { pw = null; }
test("a synthetic chromium leg", async (t) => { const b = await launchLeg(t, pw, "chromium"); if (b) await b.close(); });
`;
    const file = path.join(dir, "leg.test.js");
    requireCjs("esbuild").buildSync({ stdin: { contents: src, resolveDir: dir, loader: "ts" }, bundle: true, format: "cjs", platform: "node",
      target: "node18", outfile: file, logLevel: "silent" });
    const empty = path.join(dir, "no-browsers"); fs.mkdirSync(empty);
    const base: Record<string, string | undefined> = { ...process.env, PLAYWRIGHT_BROWSERS_PATH: empty };
    delete base[REQUIRE_ENV]; delete base[ENGINES_ENV];
    delete base.NODE_TEST_CONTEXT;   // set for this file by the parent runner; inherited, it makes the child report to it instead of printing
    const run = (env: Record<string, string | undefined>) => cp.spawnSync(process.execPath, ["--test", "--test-reporter=tap", file],
      { encoding: "utf8", timeout: 100_000, cwd: dir, env });
    const off = run(base);
    assert.equal(off.status, 0, "off: the leg skips and the run passes\n" + off.stdout.slice(-1500));
    assert.match(off.stdout, /# SKIP .*(no playwright chromium on this machine|playwright is not installed)/);
    const on = run({ ...base, ...ON });
    assert.notEqual(on.status, 0, "on: the run fails\n" + on.stdout.slice(-1500));
    assert.match(on.stdout, new RegExp(`${REQUIRE_ENV}=1 and this chromium leg cannot run: (no playwright chromium on this machine|playwright is not installed)`));
    assert.doesNotMatch(on.stdout, /# SKIP/);
  } finally { fs.rmSync(dir, { recursive: true, force: true }); }
});

test("every npm-test file that loads playwright goes through browser-leg.ts and claims nothing about CI's browser", () => {
  const dirs = ["ui", "ui/webview", "vscode-extension/src"];
  const files = dirs.flatMap((d) => fs.readdirSync(path.join(REPO, d)).filter((f) => f.endsWith(".test.ts")).map((f) => d + "/" + f));
  const loaders = files.filter((f) => f !== "ui/webview/browser-leg.test.ts"   // this file: its child loads playwright, and it spells the claims out
    && /\(\s*["']playwright["']\s*\)/.test(fs.readFileSync(path.join(REPO, f), "utf8")));
  assert.ok(loaders.length >= 17, "the census still finds the browser legs: " + loaders.length);
  const off: string[] = [];
  for (const f of loaders) {
    const s = fs.readFileSync(path.join(REPO, f), "utf8");
    if (!/from "\.\/(webview\/)?browser-leg"/.test(s) || !/\b(launchLeg|skipLeg|measuredSkip)\(/.test(s)) off.push(f + ": does not route its skip through browser-leg.ts");
    if (/\bt\.skip\(/.test(s)) off.push(f + ": calls t.skip directly, which no CI switch can see");
    if (/CI installs (none|no browser)|before its Chromium install|downloads a browser only after/.test(s)) off.push(f + ": claims CI runs no browser for it");
  }
  assert.deepEqual(off, []);
});

test("CI's unit job installs the pinned Chromium before its Test step and requires the Chromium legs there", () => {
  const wf = fs.readFileSync(path.join(REPO, ".github", "workflows", "ci.yml"), "utf8");
  const start = wf.indexOf("\n  vscode-extension-unit:\n");
  assert.ok(start >= 0, "the unit job is there");
  const end = wf.indexOf("\n  vscode-extension-served:\n", start);
  const job = wf.slice(start, end > start ? end : undefined);
  const at = (name: string) => { const i = job.indexOf("      - name: " + name + "\n"); assert.ok(i >= 0, "the job has a step named " + name); return i; };
  const install = at("Install the pinned Playwright Chromium"), cache = at("Cache Playwright's browsers");
  const testStep = at("Test"), next = job.indexOf("\n      - ", testStep + 1);
  assert.ok(cache < install && install < testStep, "the browser is cached and installed before npm test runs");
  const step = job.slice(testStep, next);
  assert.match(step, /\n\s+run: npm test$/m);
  assert.match(step, new RegExp(`\\n\\s+${REQUIRE_ENV}: "1"\\n`), "the Test step sets the switch");
  assert.match(step, new RegExp(`\\n\\s+${ENGINES_ENV}: chromium\\n`), "and declares the one engine the job installs");
});
