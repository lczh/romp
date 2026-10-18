// WHERE THE KEYBOARD IS across the window (window-focus.ts, 2026-10-18): the question card's focus guard reads it so a
// question arriving in one dashboard column does not take the keys from another column's box. Executed against stub
// windows: a page alone (its own top), the dashboard (the shell's focus followed down into the focused frame), a frame of
// another origin on the way down, and VS Code's webview (a top of another origin, whose document cannot be read).
import { test } from "node:test";
import * as assert from "node:assert/strict";
import { windowFocusedElement } from "./window-focus";

type Stub = { tagName: string; contentDocument?: { activeElement: Stub | null } | null; name?: string };
const el = (tagName: string, name: string): Stub => ({ tagName, name });
const frame = (inner: Stub | null, name = "frame"): Stub => ({ tagName: "IFRAME", name, contentDocument: { activeElement: inner } });
const page = (active: Stub | null, top?: unknown) => {
  const w: any = { document: { activeElement: active } };
  w.top = top === undefined ? w : top;
  return w as Window;
};
const shell = (active: Stub | null) => ({ document: { activeElement: active } });

test("a page alone is its own top: its own focus", () => {
  const box = el("TEXTAREA", "box");
  assert.equal(windowFocusedElement(page(box)), box as unknown as Element);
  const body = el("BODY", "body");
  assert.equal(windowFocusedElement(page(body)), body as unknown as Element);
});

test("on the dashboard the shell's focus decides, followed down into the frame that holds it", () => {
  const otherBox = el("TEXTAREA", "column 2's box");
  const mineBody = el("BODY", "column 1's body");
  // column 1's own focus is on its body; the keys are in column 2's box
  assert.equal(windowFocusedElement(page(mineBody, shell(frame(otherBox, "f-chat-2")))), otherBox as unknown as Element);
  // a frame inside a frame: followed to the end
  const deep = el("INPUT", "an input two frames down");
  assert.equal(windowFocusedElement(page(mineBody, shell(frame(frame(deep, "inner"), "outer")))), deep as unknown as Element);
  // the shell holds the focus itself: its body, not this page's
  const shellBody = el("BODY", "the shell's body");
  assert.equal(windowFocusedElement(page(mineBody, shell(shellBody))), shellBody as unknown as Element);
});

test("a frame of another origin on the way down is returned as itself", () => {
  const foreign: Stub = { tagName: "IFRAME", name: "foreign", contentDocument: null };
  assert.equal(windowFocusedElement(page(el("BODY", "b"), shell(foreign))), foreign as unknown as Element);
  const throwing: any = { tagName: "IFRAME", name: "throws", get contentDocument() { throw new Error("SecurityError"); } };
  assert.equal(windowFocusedElement(page(el("BODY", "b"), shell(throwing))), throwing as unknown as Element);
});

test("VS Code's webview: a top of another origin leaves this page's own focus, as before", () => {
  const card = el("DIV", "the page's own card");
  const crossOrigin = { get document(): never { throw new Error("SecurityError: Blocked a frame from accessing a cross-origin frame"); } };
  assert.equal(windowFocusedElement(page(card, crossOrigin)), card as unknown as Element);
  assert.equal(windowFocusedElement(page(null, crossOrigin)), null);
});
