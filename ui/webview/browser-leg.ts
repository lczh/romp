// The browser legs of the extension's node tests (npm test): one rule for what a leg does when it cannot launch its engine.
// Off, the default and every contributor's machine, the leg skips and says why. Under ROMP_BROWSER_TESTS_REQUIRE=1, which
// CI's unit job sets on its Test step after installing the pinned Chromium, a leg for an engine the runner declares in
// ROMP_BROWSER_TESTS_ENGINES (comma-separated; every engine when unset) FAILS with the same reason instead, so a runner that
// lost its browser turns the check red rather than green with the coverage gone. A leg for an engine the runner does not
// declare (Firefox: CI installs Chromium alone) stays a skip, its reason opening "optional:". Until 2026-10-14 the Test
// step ran before the install and every one of these legs skipped in CI under a reason that said CI installs no browser.
// The served pytest labs take the same stance through ROMP_SERVED_TESTS_REQUIRE (tests/conftest.py), the pane bench through
// ROMP_UI_BENCH_REQUIRE. browser-leg.test.ts pins the rule, holds every test file that loads playwright to it, and pins
// the workflow's order.
import * as assert from "node:assert/strict";
import { test } from "node:test";

export const REQUIRE_ENV = "ROMP_BROWSER_TESTS_REQUIRE";
export const ENGINES_ENV = "ROMP_BROWSER_TESTS_ENGINES";

type Env = Record<string, string | undefined>;
type Skippable = { skip(message?: string): void };

const required = (env: Env) => env[REQUIRE_ENV] === "1";
const declared = (env: Env) => (env[ENGINES_ENV] || "").split(",").map((s) => s.trim().toLowerCase()).filter(Boolean);
const firstLine = (e: unknown) => String((e as Error)?.message ?? e).split("\n")[0];

/** Whether a leg on `engine` must run here: the switch is on and the runner declares the engine (or declares none). */
export function legRequired(engine: string, env: Env = process.env): boolean {
  if (!required(env)) return false;
  const engines = declared(env);
  return engines.length === 0 || engines.includes(engine.toLowerCase());
}

/** The reason a leg on `engine` that cannot run gives, or throws: where the leg is required it fails with the reason; where
 *  the switch is on but the runner declares other engines it is an "optional:" skip; elsewhere the plain reason. */
function verdict(engine: string, reason: string, env: Env): string {
  if (legRequired(engine, env)) assert.fail(`${REQUIRE_ENV}=1 and this ${engine} leg cannot run: ${reason}`);
  return required(env) ? `optional: this runner declares no ${engine} (${ENGINES_ENV}=${env[ENGINES_ENV]}): ${reason}` : reason;
}

/** A leg that cannot run on `engine`: `t` skips with the reason, or the leg fails with it where it is required. */
export function skipLeg(t: Skippable, engine: string, reason: string, env: Env = process.env): void {
  t.skip(verdict(engine, reason, env));
}

/** `engine` launched for this leg, or null with the leg skipped (or failed, where required) and saying why. `pw` is the
 *  playwright module as the extension installed it, or null where it is not installed. */
export async function launchLeg(t: Skippable, pw: any, engine: string, env: Env = process.env): Promise<any | null> {
  if (!pw) { skipLeg(t, engine, "playwright is not installed under vscode-extension (npm ci there installs it)", env); return null; }
  try { return await pw[engine].launch(); }
  catch (e) {
    skipLeg(t, engine, `no playwright ${engine} on this machine (npx playwright install ${engine} under vscode-extension): ${firstLine(e)}`, env);
    return null;
  }
}

/** The `skip` option for the tests of a file whose browser measurement ran once at load (a child driver): false when it ran,
 *  else the reason. Where the leg is required, the file also gets one test that fails with the reason, so a runner without
 *  the browser reads one red line naming it while the measured tests skip. */
export function measuredSkip(engine: string, reason: string | false, env: Env = process.env,
                             register: (name: string, fn: () => void) => unknown = test): string | false {
  if (!reason) return false;
  try { return verdict(engine, reason, env); }
  catch (e) {
    register(`the ${engine} measurement ran (${REQUIRE_ENV}=1)`, () => { throw e; });
    return reason;
  }
}
