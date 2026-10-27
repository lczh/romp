// The MCP panel and the composer's "/" menu read per-session data from a kernel (/mcp, /commands). A session on a
// linked machine carries a "host:uuid" id, and the page's own kernel owns no session by it: asked there, the panel
// said no running backend owned the session and the menu listed this machine's commands (a Codex session on the
// other machine was offered Claude's). Both reads now go through this kernel's /remote/<host>/<route> relay with
// the bare id, the way preview.ts fileUrl routes /file; tests/test_remote_session_reads_relay.py pins the relay.
// Synthetic ids only (TESTHOST, a placeholder uuid).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import * as fs from "node:fs";
import * as path from "node:path";
import { sessionReadUrl } from "./media";

const RENDER = fs.readFileSync(path.resolve(process.cwd(), "..", "ui", "webview", "render.ts"), "utf8");
const BARE = "11111111-2222-3333-4444-555555555555";

function withWindow<T>(w: any, fn: () => T): T {
  const real = (globalThis as any).window;
  try {
    if (w === undefined) delete (globalThis as any).window; else (globalThis as any).window = w;
    return fn();
  } finally {
    if (real === undefined) delete (globalThis as any).window; else (globalThis as any).window = real;
  }
}

test("a linked machine's session is read through that host's relay with its bare id", () => {
  withWindow(undefined, () => {
    assert.equal(sessionReadUrl("/mcp", "TESTHOST:" + BARE), "/remote/TESTHOST/mcp?sid=" + BARE);
    assert.equal(sessionReadUrl("/commands", "TESTHOST:" + BARE), "/remote/TESTHOST/commands?sid=" + BARE);
  });
});

test("a local session, and no session at all, keep the local route unchanged", () => {
  withWindow(undefined, () => {
    assert.equal(sessionReadUrl("/mcp", BARE), "/mcp?sid=" + BARE);
    assert.equal(sessionReadUrl("/commands", BARE), "/commands?sid=" + BARE);
    assert.equal(sessionReadUrl("/commands", ""), "/commands?sid=", "the kernel-directory fallback the menu asks for with no tab");
  });
});

test("under a VS Code host the relay URL carries the injected base and the serve token, as every kernel read does", () => {
  withWindow({ __rompKernelBase: "http://127.0.0.1:29855", __rompKernelToken: "t0k/en" }, () => {
    assert.equal(sessionReadUrl("/mcp", "TESTHOST:" + BARE),
      "http://127.0.0.1:29855/remote/TESTHOST/mcp?sid=" + BARE + "&token=t0k%2Fen");
  });
});

test("the MCP panel and the menu fetch through sessionReadUrl, never the local route for every tab", () => {
  assert.ok(RENDER.includes('fetch(sessionReadUrl("/mcp", sid), { cache: "no-store" })'));
  assert.ok(RENDER.includes('fetch(sessionReadUrl("/commands", sid), { cache: "no-store" })'));
  assert.ok(!RENDER.includes('kernelUrl("/mcp?sid="'), "the panel's read for a linked machine's session asked this kernel");
  assert.ok(!RENDER.includes('kernelUrl("/commands?sid="'), "the menu's read for a linked machine's session asked this kernel");
});

test("a relay's refusal for a linked machine's session is shown as it came, not as a stale local kernel", () => {
  const at = RENDER.indexOf("function loadMcpPanel(");
  const fn = RENDER.slice(at, RENDER.indexOf("\nfunction ", at + 1));
  assert.ok(at > 0, "loadMcpPanel moved: re-anchor");
  assert.ok(fn.includes('if (hostOf(sid)) return r.text().then((t) => { throw new Error(t || "HTTP " + r.status); });'));
  assert.ok(fn.indexOf("if (hostOf(sid))") < fn.indexOf("this romp kernel predates the MCP panel"),
    "the remote branch runs before the local kernel's stale-build message");
});
