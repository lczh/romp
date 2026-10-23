import { test } from "node:test";
import * as assert from "node:assert/strict";
import { shipHoldsReload, reloadHoldReason } from "./reload-hold";

// T272 follow-up: the upload hold on the dashboard's reload counts only ships whose ack can still arrive, and a held
// reload wears a line.

const fed = { hosts: () => ["web", "api"], down: () => ["api"] };

test("a ship holds the reload only while its host is the local kernel or an attached, reachable host", () => {
  assert.equal(shipHoldsReload("aaaaaaaa-1111-2222-3333-444444444444", fed), true, "local: the shim's own socket carries the ack");
  assert.equal(shipHoldsReload("web:aaaaaaaa-1111-2222-3333-444444444444", fed), true, "an attached, reachable host");
  assert.equal(shipHoldsReload("api:aaaaaaaa-1111-2222-3333-444444444444", fed), false, "its relay is down: no ack until it returns");
  assert.equal(shipHoldsReload("gone:aaaaaaaa-1111-2222-3333-444444444444", fed), false, "a detached or removed host: no ack ever");
  assert.equal(shipHoldsReload("api:aaaaaaaa-1111-2222-3333-444444444444", null), true, "no manager loaded (a single-kernel page): every ship holds");
  assert.equal(shipHoldsReload("api:x", { hosts: () => { throw new Error("boom"); } }), true, "a manager that throws is read as reachable");
});

test("the pane's reason: upload for a live ship, held-send for a gate on a live ship, nothing for unreachable ones", () => {
  assert.equal(reloadHoldReason(["s1"], null, fed), "upload");
  assert.equal(reloadHoldReason(["api:s2"], null, fed), "", "a ship to a down host never holds");
  assert.equal(reloadHoldReason(["api:s2", "web:s3"], null, fed), "upload", "…but a live one beside it does");
  assert.equal(reloadHoldReason([], "s1", fed), "held-send");
  assert.equal(reloadHoldReason([], "gone:s1", fed), "", "a send held on a gone host's upload does not hold");
  assert.equal(reloadHoldReason([], null, fed), "");
});

test("a create in flight holding sends made on its tab holds the reload as a held send, while its session can still arrive (2026-10-23)", () => {
  assert.equal(reloadHoldReason([], null, fed, ""), "held-send", "a local create: its session's arrival sends them");
  assert.equal(reloadHoldReason([], null, fed, "web"), "held-send", "a create on an attached, reachable host");
  assert.equal(reloadHoldReason([], null, fed, "api"), "", "its host's relay is down: no arrival until it returns");
  assert.equal(reloadHoldReason([], null, fed, "gone"), "", "a detached or removed host: no arrival ever");
  assert.equal(reloadHoldReason([], null, fed, null), "", "no create holding sends");
  assert.equal(reloadHoldReason(["s1"], null, fed, ""), "upload", "a live ship's reason comes first");
});
