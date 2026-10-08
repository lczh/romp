// The usage windows' display half. Since 2026-10-08 the bar's cell and its hover show CALENDAR windows in the user's words,
// "today" and "this month", the VIEWER's own: every turn at API prices from the hour ledger, by billing route, built in the
// browser's zone (_spend_win). The two
// windows nest (a day inside its month), so the superset rule T235 wrote for the old rolling windows ("1 month" must
// never read below "1 week") holds by construction, and "this month" is the window an enterprise seat's monthly
// allowance resets on. The kernel still ships the rolling and calendar `spend` windows for an older shell (T235, its
// math executed in tests/test_spend_windows.py), with the bill-cycle budget on the calendar figure.
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";

const KERNEL = fs.readFileSync(path.resolve(process.cwd(), "..", "kernel", "kernel.py"), "utf8");
const STRIP = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "strip.ts"), "utf8");
const usageJS = KERNEL.split('_LANDING_USAGE_JS = """')[1].split('"""')[0];

test("the cell and the hover share ONE pair of calendar windows, in the user's words", () => {
  assert.ok(usageJS.includes("var COST_WINS=[['today','today'],['monthToDate','this month']];"));
  assert.ok(!usageJS.includes("SPEND_WINS"), "the rolling-window rows of the key-only era are gone from the rail");
  assert.match(STRIP, /\[\["today", "today", "today"\], \["monthToDate", "this month", "month"\]\] as const/, "the strip twin wears the same two");
});

test("the kernel builds them in the VIEWER's zone from the hour ledger, an hour counting from its start", () => {
  // 2026-10-08 review: they were each kernel's own calendar day and month, so a viewer elsewhere lost "today" at that
  // kernel's midnight; the behaviour executes in tests/test_spend_windows.py (zones, coverage, a peer's hours)
  assert.match(KERNEL, /def _spend_win\(hp, tz=None, now=None\):/);
  assert.match(KERNEL, /zi, name = _viewer_zone\(tz\)\n    t0, m0 = _zone_starts\(now, zi\)/);
  assert.match(KERNEL, /if not \(isinstance\(row, list\) and len\(row\) >= 6\) or row\[0\] \* 3600 < start:/, "an hour counts when it starts in the window");
  assert.match(KERNEL, /_SPEND_HOURS_SPAN = 33 \* 24/, "a month of hours and a zone's offset");
});

test("an older shell's windows still ride the payload: rolling '1 month' and calendar 'this month', the budget on the bill figure", () => {
  assert.match(KERNEL, /"month": rolling_month,\s*\n\s*"monthToDate": _sum\(v for k, v in days\.items\(\) if isinstance\(k, str\) and k\.startswith\(month\)\)/);
  assert.match(KERNEL, /win\["monthToDate" if k == "month" else k\]\["budget"\] = v/, "the bill-cycle budget rides the bill figure, never the rolling window");
  assert.match(KERNEL, /win\["month"\]\["since"\] = oldest/, "a ledger younger than the window still says how far back it goes");
});
