// File-preview helpers for the chat (render.ts path thumbnails and full-size renders) — the user
// 2026-07-08: when an agent produces a plot/PDF/screenshot, show the thing, not just
// its path. The bytes come from the kernel's `/file?path=…&sid=…` endpoint (extension-allowlisted,
// existence-checked, behind the same auth as every route), so a preview is only ever what the kernel
// can actually read RIGHT NOW — a deleted/hallucinated path 404s and the <img> onerror hides the thumb
// (event-based; no stale placeholders). Web dashboard only: the VS Code webview sandbox can't reach the
// kernel origin from an <img>, so callers gate on canPreview() and keep the plain click-to-open link.

import { hostOf, bareId } from "./host-prefix";
import { mediaSrc } from "./media";
import { ICON_DOWNLOAD, ICON_COPY, ICON_CHECK, ICON_CROSS, ICON_POPOUT } from "./icons";   // the shared stroke-family glyphs (T367; the popout for a URL picture's original)
import * as pz from "./pinch";
import { opensInLightbox, VIDEO_EXT } from "./md-images";   // the schemes a picture or a video opens on: the click's and the arrows' one rule

// Extensions the kernel's _PREVIEW_MIME serves — keep the two lists in step (tests pin both). Video's are VIDEO_EXT
// (md-images.ts), the kernel's _VIDEO_MIME.
const IMG_EXT = new Set(["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"]);

export type PreviewKind = "img" | "pdf" | "video";

export function previewKind(path: string): PreviewKind | null {
  const ext = path.slice(path.lastIndexOf(".") + 1).toLowerCase();
  if (IMG_EXT.has(ext)) return "img";
  if (ext === "pdf") return "pdf";
  if (VIDEO_EXT.has(ext)) return "video";
  return null;
}

// Previews load over the page's own origin, so they only work where the page IS the kernel
// (the web dashboard). In the VS Code webview (vscode-webview: origin) a relative /file URL
// resolves nowhere — callers keep the existing openFile behavior there.
export function canPreview(): boolean {
  return location.protocol === "http:" || location.protocol === "https:";
}

// Which click means "in a browser tab of its own"? The browser-wide idiom, so there is nothing new to
// learn and no setting to find: a Cmd/Ctrl-click (the `click` event carries the modifier) or a
// middle-button press (which arrives as `auxclick`, button 1). A plain click acts inside the dashboard
// (the user 2026-09-07, who wanted one opening rule for every file: a plain click keeps a PDF inside the
// dashboard exactly as an image opens; the modifier is the one signal that the browser's own tab, beside
// the dashboard, is what is wanted). The PDF and folder gestures read this signal, and so do the links
// inside a file the viewer shows (file-view.ts).
export function wantsOwnTab(ev?: { metaKey?: boolean; ctrlKey?: boolean; button?: number } | null): boolean {
  return !!ev && (!!ev.metaKey || !!ev.ctrlKey || ev.button === 1);
}

// A file in the browser's OWN tab, on the gesture above: ONE window.open aimed at the kernel's /file URL, the
// same-origin, cookie-authed route the viewer fetches from (a remote session's file relays exactly as the
// viewer's fetch does), so the browser renders what the kernel serves: a PDF in its viewer, an image, a text
// file as text. False when nothing opened, and the caller's in-app view takes over: the popup was blocked, or
// this is the VS Code webview, whose sandbox has no tabs and no kernel origin (canPreview). The tab's opener is
// severed on the handle: a link inside the opened file may navigate the tab to a foreign site, which must not
// hold the dashboard. Not the `noopener` feature, which returns null even on success. The one opener behind
// every own-tab gesture: the viewer's links hand it any file (file-view.ts), openPdfTab below hands it a PDF.
export function openFileTab(path: string, sid?: string | null): boolean {
  if (!canPreview()) return false;
  const w = window.open(fileUrl(path, sid), "_blank");
  if (!w) return false;                                   // blocked: the caller's in-app view takes over
  try { w.opener = null; } catch { /* unreachable while the tab is our own initial page */ }
  return true;
}

// LOADING CUE (the user 2026-07-31): a remote image's bytes arrive over the ssh tunnel, so for a
// beat the message showed only the path text and the picture "popped in" with nothing saying it was
// on the way. Per the loading-state rule the first thing up is the romp swirl: a mini spinning glyph
// holds the image's spot until its `load` event lands (event-based; an error still removes the whole
// box, spinner included — no backstop needed because the cue dies with its box either way). Memoized
// per URL for this page life: chat re-renders rebuild these elements constantly, and re-flashing a
// spinner over bytes the browser just painted would itself be flicker — only a URL's FIRST load spins.
const loadedOnce = new Set<string>();

// A manual retry's swirl stays up at least this long before a failure may swap the chip back in —
// an instant connection reset otherwise flashes it for one frame and the tap looks ignored (the
// user 2026-08-16). Presentation smoothing only: the failed state is already decided, this paces
// nothing but the paint.
const MIN_RETRY_SPIN_MS = 400;

// Blob types for the resumable retry's assembled bytes (an <img> renders a typed blob everywhere;
// untyped leans on sniffing). Keyed by extension, mirroring IMG_EXT.
const IMG_MIME: Record<string, string> = {
  png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
  webp: "image/webp", bmp: "image/bmp", svg: "image/svg+xml",
};

// Fully-fetched previews for this page life: original URL → object URL. The chat re-renders its
// messages constantly, and a resumable fetch bypasses the HTTP cache (no-store) — without this memo
// every re-render would re-pull the whole image over the very link that struggled to deliver it
// once. Bounded; the evicted entry's blob is released.
const resolvedUrls = new Map<string, string>();
function rememberResolved(url: string, objUrl: string): void {
  resolvedUrls.set(url, objUrl);
  if (resolvedUrls.size > 24) {
    const oldest = resolvedUrls.entries().next().value as [string, string];
    resolvedUrls.delete(oldest[0]);
    URL.revokeObjectURL(oldest[1]);
  }
}

function fmtBytes(got: number, total: number): string {
  const h = (n: number) => (n >= 1e6 ? (n / 1e6).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1e3)) + " KB");
  return total ? h(got) + " of " + h(total) : h(got);
}

// The fixed-footprint wait box shared by the retrying swirl AND the failure chip, so retry churn
// never shifts the layout under the reader (the user 2026-08-16: the chat scroll thrashed by about
// a line as the states swapped heights).
function mkWait(box: HTMLElement): HTMLElement {
  box.textContent = "";
  const wait = document.createElement("span");
  wait.className = "path-full-wait";
  box.appendChild(wait);
  return wait;
}

function withLoadCue(box: HTMLElement, img: HTMLImageElement, url: string): void {
  if (loadedOnce.has(url)) return;
  const spin = document.createElement("img");
  spin.className = "path-load-spin";
  spin.src = mediaSrc("romp-swirl-glyph.svg");
  spin.alt = "loading preview…";
  spin.title = "loading preview…";
  box.appendChild(spin);
  img.classList.add("path-img-loading");
  img.addEventListener("load", () => {
    loadedOnce.add(url);
    spin.remove();
    img.classList.remove("path-img-loading");
  });
}

// The kernel serves the bytes; sid lets it resolve a relative path against THAT session's cwd
// (same resolution as click-to-open — kernel _resolve_open_path). A FEDERATED session's file lives
// on the REMOTE machine's disk, so a host-prefixed sid (`gpu1:‹uuid›` — see federation.ts) routes
// through this kernel's /remote/<host>/file relay, the HTTP twin of the /remote/<host>/ws splice,
// with the bare sid the remote kernel actually knows (the user 2026-07-31: mentioned plots on a
// remote session's chat never rendered — /file read the LOCAL disk and 404'd). Still a same-origin
// URL, so it works wherever the dashboard is viewed from (the phone over `tailscale serve` included).
export function fileUrl(path: string, sid?: string | null): string {
  const host = sid ? hostOf(sid) : "";
  const base = host ? "/remote/" + encodeURIComponent(host) + "/file" : "/file";
  const bare = sid ? bareId(sid) : "";
  return base + "?path=" + encodeURIComponent(path) + (bare ? "&sid=" + encodeURIComponent(bare) : "");
}

// A PDF's OWN browser tab — on a Cmd/Ctrl- or middle-click (wantsOwnTab). A plain click keeps the PDF
// inside the dashboard like an image: the lightbox from the chat card, the in-pane viewer from a path or a
// file-browser row (the user 2026-09-07). The modified click opens the paper full size in the browser's own
// viewer, the way one opens from OpenReview or HotCRP (the user 2026-09-06), and it stays open beside the
// dashboard while the work goes on. Those sites do nothing more than link the PDF's URL with a new-tab
// target and serve it inline as application/pdf; the browser renders it from its own cache and nothing lands
// on disk unless the browser is set to download PDFs — no download-then-delete dance is needed. /file
// already serves exactly that (same-origin, cookie-authed, federation-aware through fileUrl), so the
// tab is ONE window.open inside the click gesture — synchronous, so a popup blocker reads it as
// user-initiated; a fetch-then-open would lose the gesture and be blocked everywhere. The opener
// link is severed by hand (w.opener = null) rather than with the noopener feature: with noopener
// window.open returns null even on success, which would hide the one signal needed — a null handle
// means the browser BLOCKED the tab, and the caller falls back to the in-app view (lightbox /
// viewer) so the PDF is never unreachable. Severed it must be (review find 2026-09-06, reproduced in
// a real browser): the tab starts on our origin, but a link inside the PDF navigates that SAME tab
// to a foreign site, and a document holding a live opener may replace the dashboard with a
// look-alike (reverse tabnabbing) — every other new tab this UI opens is disowned the same way.
// The flag is set while the tab is still its same-origin initial page and rides every later
// navigation. Web dashboard only: the VS Code webview cannot window.open, and previews are gated off
// there anyway (canPreview). The KIND check lives here, by extension — the only fact available inside
// the gesture — so a caller passes any path and a non-PDF is simply "not mine" (false): the viewer's own
// media branch keeps keying on the kernel's Content-Type verdict, never on an extension re-test
// (file-view.test.ts pins that). The tab itself is openFileTab's (above): one window.open, one severed
// opener, one blocked-popup verdict, shared with the viewer's links, which open any file that way.
export function openPdfTab(path: string, sid?: string | null): boolean {
  if (previewKind(path) !== "pdf") return false;
  return openFileTab(path, sid);
}

// The PDF card's click, with its gesture: a plain click opens the in-app lightbox, like an image; a
// Cmd/Ctrl- or middle-click opens the PDF in its own browser tab (openPdfTab), and a BLOCKED popup falls
// back to the lightbox so the PDF is never unreachable.
export function openPdf(path: string, sid?: string | null, ev?: MouseEvent | null): void {
  if (wantsOwnTab(ev) && openPdfTab(path, sid)) return;
  openLightbox({ path, sid });
}

// Full-view lightbox: dark backdrop, the image at natural-but-capped size or the PDF in the browser's
// native viewer (a plain click's PDF view, and the fallback when a modified click's tab was popup-blocked — openPdf),
// filename caption. One singleton element; backdrop click / Esc / ✕ closes. Styles live
// in BOTH styles.css and feed.css (each page loads only its own sheet — the .romp-acted precedent).
// Pinch-zoom on the lightbox image (T162, the user 2026-08-28 on Android). Pointer events only —
// no gesture library: two pointers pinch around the gesture midpoint (the content point under it
// held fixed — pinch.ts owns the math, executable-tested), one pointer PANS while zoomed,
// double-tap toggles home/2.5× around the tap. touch-action:none on the stage (CSS) keeps the
// browser's own page-zoom out of it. The CLOSE gesture is untouched by construction: dismissal
// stays tap-on-BACKDROP (target === wrap) and the ✕ — every pinch/pan pointer is CAPTURED by the
// stage, so a drag that ends anywhere can never read as a backdrop tap.
function wirePinchZoom(stage: HTMLElement, img: HTMLElement): { retarget: (next: HTMLElement) => void } {
  let view = pz.identity();
  const ptrs = new Map<number, { x: number; y: number }>();
  let start: { view: pz.PinchView; d: number } | null = null;   // two-pointer gesture snapshot
  let rest: { left: number; top: number; w: number; h: number; vw: number; vh: number } | null = null;
  let lastTap = 0, lastTapAt = { x: 0, y: 0 };
  const apply = () => {
    img.style.transform = view.s === 1 && !view.tx && !view.ty
      ? "" : `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`;
  };
  // the image's REST frame (its untransformed layout box, q-space origin) — measured at gesture
  // start by removing the current transform's offset from the live rect; origin 0 0 keeps the
  // top-left corner exactly translate() away from layout, so the inversion is exact
  const measure = () => {
    const r = img.getBoundingClientRect();
    const box = stage.getBoundingClientRect();
    rest = { left: r.left - view.tx, top: r.top - view.ty,
             w: r.width / view.s, h: r.height / view.s,
             vw: box.width, vh: box.height };
  };
  const q = (e: PointerEvent) => ({ x: e.clientX - rest!.left, y: e.clientY - rest!.top });
  stage.addEventListener("pointerdown", (e) => {
    if ((e.target as HTMLElement).closest(".romp-lightbox-bar")) return;   // the bar's buttons own their taps
    if (img.tagName === "VIDEO") return;   // a video's own controls own its taps (a capture here would take the scrub bar's drag); no zoom
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    try { stage.setPointerCapture(e.pointerId); } catch { /* a pointer gone between event and capture — the move/up handlers still see bubbled events */ }
    measure();
    if (ptrs.size === 2) {
      const [a, b] = [...ptrs.values()];
      start = { view, d: pz.dist(a, b) };
    } else if (ptrs.size === 1) {
      // double-tap: a second down within the platform window and radius toggles the zoom. The
      // 350ms is the GESTURE'S definition (like a double-click), not state logic.
      const now = e.timeStamp;
      const p = q(e);
      if (now - lastTap < 350 && Math.hypot(p.x - lastTapAt.x, p.y - lastTapAt.y) < 30) {
        view = pz.clampPan(pz.doubleTapToggle(view, p.x, p.y), rest!.w, rest!.h, rest!.vw, rest!.vh);
        apply();
        lastTap = 0;
      } else { lastTap = now; lastTapAt = p; }
    }
    if (view.s > 1 || ptrs.size === 2) e.preventDefault();
  });
  stage.addEventListener("pointermove", (e) => {
    const prev = ptrs.get(e.pointerId);
    if (!prev || !rest) return;
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 2 && start) {
      const [a, b] = [...ptrs.values()];
      const m = pz.midpoint(a, b);
      view = pz.clampPan(
        pz.zoomAt(start.view, m.x - rest.left, m.y - rest.top, pz.dist(a, b) / start.d),
        rest.w, rest.h, rest.vw, rest.vh);
      apply();
    } else if (ptrs.size === 1 && view.s > 1) {
      view = pz.clampPan(pz.pan(view, e.clientX - prev.x, e.clientY - prev.y), rest.w, rest.h, rest.vw, rest.vh);
      apply();
    }
  });
  const lift = (e: PointerEvent) => {
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) start = null;
    if (ptrs.size === 0) { view = pz.settle(view); apply(); }
  };
  stage.addEventListener("pointerup", lift);
  stage.addEventListener("pointercancel", lift);
  // stepping the lightbox swaps the img element: re-identity the view for the fresh element —
  // the stage's listeners are wired ONCE (steps must never stack handlers), only the target moves
  return { retarget: (next: HTMLElement) => { img = next; view = pz.identity(); ptrs.clear(); start = null; apply(); } };
}

// ── lightbox arrow navigation (the user 2026-08-29: arrow keys step to the previous/next picture
// in the chat, like a messaging app) ────────────────────────────────────────────────────────────
// The image sequence comes from a PROVIDER the chat registers (render.ts walks the session's
// EVENTS oldest→newest — the DOM misses virtualization-windowed images), each entry the same
// source the click passes — for a file, the (path, sid, pin) triple, so a step shows THAT message's
// pinned bytes and can never resurrect the history-rewrite the pin store prevents. Surfaces that
// register no provider (the feed) keep inert arrows. The provider is handed the clicked element
// too (`from`): the chat reads off it which transcript the picture sits in (a subagent viewer's, a
// comment thread's), so the arrows step through the pictures the reader is looking at.
//
// A SOURCE is what the lightbox shows (2026-10-20, the user on a phone: a picture a message embeds as
// markdown never opened the lightbox, only the ones romp draws from a file path did): a FILE the kernel
// serves, named by its path (the sid resolves a relative one against that session's folder, the pin names
// the bytes the message saw), or a picture embedded by URL, shown from that URL as the message has it.
// One lightbox for both, and one sequence: the chat's provider returns both kinds in the order the chat
// shows them.
// A VIDEO is a source too (2026-10-21, the user wanting a chat video treated like a picture): a file whose path names one
// (previewKind "video"), or an address flagged `video` (an address alone cannot say: a <video> may play a URL with no
// extension). The lightbox plays it with its controls, and the arrows step through pictures and videos alike.
export type LightboxSource = { path: string; sid?: string | null; pin?: string } | { url: string; alt?: string; video?: boolean };
export type LightboxNavEntry = LightboxSource;
let lightboxNav: ((sid: string | null | undefined, from?: Element | null) => LightboxNavEntry[]) | null = null;
export function setLightboxNav(fn: (sid: string | null | undefined, from?: Element | null) => LightboxNavEntry[]): void {
  lightboxNav = fn;
}

// The bytes a source names: a file's through the kernel (pinned when the message pinned them), a URL as it is.
function lightboxSrc(e: LightboxSource): string {
  return "url" in e ? e.url : fileUrl(e.path, e.sid) + (e.pin ? "&pin=" + encodeURIComponent(e.pin) : "");
}
// Whether a source is a video: an address flagged so, a file by its extension.
function isVideoSource(e: LightboxSource): boolean {
  return "url" in e ? !!e.video : previewKind(e.path) === "video";
}
// Two sources are the same picture: a file by its path and pin (two messages embedding different VERSIONS of one
// path are different entries), a URL by itself.
function sameSource(a: LightboxSource, b: LightboxSource): boolean {
  if ("url" in a || "url" in b) return "url" in a && "url" in b && a.url === b.url;
  return a.path === b.path && (a.pin || "") === (b.pin || "");
}
// The title a URL picture wears in the bar, in the path's two halves: the place dimmed (host and folders), the file
// name whole, the whole URL one hover away. A data: URL has no place and no name: its alt text names it, else
// "image" (its bytes, perhaps megabytes of them, never reach the title).
function urlTitle(e: { url: string; alt?: string; video?: boolean }): { dir: string; base: string; full: string } {
  if (/^data:/i.test(e.url)) { const n = (e.alt || "").trim() || "image"; return { dir: "", base: n, full: n }; }
  try {
    const u = new URL(e.url);
    let p = u.pathname;
    try { p = decodeURIComponent(p); } catch { /* a malformed escape: the spelling as written */ }
    const at = u.host + p;
    const cut = at.lastIndexOf("/");
    return cut < 0 || cut === at.length - 1 ? { dir: "", base: at, full: e.url } : { dir: at.slice(0, cut + 1), base: at.slice(cut + 1), full: e.url };
  } catch { return { dir: "", base: e.url, full: e.url }; }
}
// The file name a data: picture saves under: "image" and its type's extension (image/png → image.png, svg+xml → svg).
function dataSaveName(url: string): string {
  const m = /^data:image\/([a-z0-9.+-]+)/i.exec(url);
  return "image." + (m ? m[1].toLowerCase().replace(/\+xml$/, "").replace(/^jpeg$/, "jpg") : "png");
}

export function openLightbox(src: LightboxSource, from?: Element | null): void {
  for (const v of Array.from(document.querySelectorAll("#romp-lightbox video"))) stopVideo(v as HTMLVideoElement);   // a reopen silences what played…
  document.getElementById("romp-lightbox")?.remove();                                                                 // …and replaces it
  const kind = "url" in src ? (src.video ? "video" : "img") : previewKind(src.path);
  if (!kind) return;
  const wrap = document.createElement("div");
  wrap.id = "romp-lightbox";
  const inner = document.createElement("div");
  inner.className = "romp-lightbox-inner" + (kind === "pdf" ? " pdf" : "");
  let nav: LightboxNavEntry[] = [];
  let at = -1;
  let step: ((delta: number) => void) | null = null;     // arrows step the chat's image sequence (img kind only)
  let cue: HTMLElement | null = null;                    // the compact position mark ("3/17") in the bar
  let curImg: (() => HTMLImageElement | HTMLVideoElement) | null = null;   // the picture or video on screen NOW (step rebinds it) — the copy source
  if (kind === "pdf" && !("url" in src)) {
    // a plain click's view (openPdf), and the fallback when a modified click's tab was popup-blocked
    const frame = document.createElement("iframe");
    frame.className = "romp-lightbox-frame";
    frame.src = fileUrl(src.path, src.sid);
    frame.title = src.path;
    inner.appendChild(frame);
  } else {
    // A video plays here (2026-10-21): its controls, inline on iOS (playsinline, or the phone takes it full screen), the
    // whole file buffered as it plays (preload auto: it was opened to be watched). It shares the picture's class, so it
    // sits in the same capped box (.romp-lightbox-img), at the clip's own size from the first frame when the chat measured
    // it already (sizeLikeClip). The tap that opened it starts it (openLightbox's end); a step onto one leaves it paused.
    const mkImg = (e: LightboxNavEntry): HTMLImageElement | HTMLVideoElement => {
      if (isVideoSource(e)) {
        const v = document.createElement("video");
        v.className = "romp-lightbox-img romp-lightbox-video";
        v.controls = true;
        v.playsInline = true;
        v.preload = "auto";
        sizeLikeClip(v, videoDims.get(lightboxSrc(e)));
        v.src = lightboxSrc(e);
        v.setAttribute("aria-label", "url" in e ? e.alt || "video" : e.path);
        return v;
      }
      const im = document.createElement("img");
      im.className = "romp-lightbox-img";
      im.src = lightboxSrc(e);
      im.alt = "url" in e ? e.alt || "" : e.path;          // a file's alt is its path (the artifacts pane's Files control reads it back)
      return im;
    };
    let img = mkImg(src);
    curImg = () => img;
    inner.appendChild(img);
    const pzc = wirePinchZoom(inner, img);
    // the chat's image sequence, oldest→newest (empty on surfaces with no provider): the current
    // position matches the source (sameSource), a file falling back to its path alone for pre-pin
    // history. A URL that failed to load, or a glyph-sized one (mdImgTiny), is no stop: no click opens it
    nav = (lightboxNav ? lightboxNav("url" in src ? null : src.sid, from) : [])
      .filter((e) => "url" in e ? !mdImgFailed.has(e.url) && !mdImgTiny.has(e.url) : previewKind(e.path) === "img" || previewKind(e.path) === "video");
    at = nav.findIndex((e) => sameSource(e, src));
    if (at < 0 && !("url" in src)) at = nav.findIndex((e) => !("url" in e) && e.path === src.path);
    // a picture that shows a srcset's candidate opens on what it shows (currentSrc), while the sequence, read off the
    // text, names its src: it is that entry (a step back to it shows the src)
    const own = from && (from as HTMLImageElement).tagName === "IMG" ? (from as HTMLImageElement).src : "";
    if (at < 0 && "url" in src && own) at = nav.findIndex((e) => "url" in e && e.url === own);
    step = (delta: number) => {
      if (at < 0 || nav.length < 2) return;
      const n = at + delta;
      if (n < 0 || n >= nav.length) return;              // the ends END (messaging-app feel) — no wrap
      at = n;
      const e = nav[at];
      const next = mkImg(e);
      if (img.tagName === "VIDEO") stopVideo(img as HTMLVideoElement);   // stepping away from a video stops it (2026-10-21)
      img.replaceWith(next);
      img = next;
      pzc.retarget(next);                                // a step lands on the fit view, zoom reset
      if (cp) cp.style.display = isVideoSource(e) ? "none" : "";   // a video has no picture to copy
      setName(e);                                        // the two-element title follows the step
      aimSave(e);                                        // …and so does the save control, its kind included
      if (cue) cue.textContent = (at + 1) + "/" + nav.length;
    };
  }
  // ── the bar, ABOVE the picture (T385, the user 2026-09-12: the controls sit at the top, the way a file opens) ──
  // It IS the file viewer's bar (file-view.ts, T367): .fileview-bar for the row, .fileview-name as a dimmed directory
  // and the basename (only the directory may truncate), .fileview-acts holding the file group (download, copy) and the
  // close cross alone at the end, glyph buttons as .fileview-btn.fileview-icon. One set of rules for both surfaces
  // (styles.css .fileview-*), so a control added to one wears the same dress in the other. The lightbox's own classes
  // stay as HOOKS: the bar's placement over the backdrop (styles.css) and the stage's tap rule (wirePinchZoom's
  // closest(".romp-lightbox-bar")). Prepended to the column below, so it precedes the picture and the PDF frame.
  const bar = document.createElement("div");
  bar.className = "fileview-bar romp-lightbox-bar";
  const name = document.createElement("div");
  name.className = "fileview-name romp-lightbox-name";
  const dir = document.createElement("span"); dir.className = "fileview-dir";
  const base = document.createElement("span"); base.className = "fileview-base";
  name.append(dir, base);
  const setName = (e: LightboxSource) => {
    let t: { dir: string; base: string; full: string };
    if ("url" in e) t = urlTitle(e);
    else { const p = e.path, cut = p.lastIndexOf("/"); t = { dir: cut >= 0 ? p.slice(0, cut + 1) : "", base: p.slice(cut + 1), full: p }; }
    dir.textContent = t.dir;
    base.textContent = t.base;
    name.title = t.full;                                  // the full path or URL, one hover away
  };
  setName(src);
  if (nav.length > 1 && at >= 0) {
    cue = document.createElement("span");
    cue.className = "romp-lightbox-cue";
    cue.textContent = (at + 1) + "/" + nav.length;
    cue.title = "picture " + (at + 1) + " of " + nav.length + " in this chat — ←/→ to step";
  }
  const acts = document.createElement("div");
  acts.className = "fileview-acts";
  const group = document.createElement("span");
  group.className = "fileview-group fileview-group-file";
  // The save control is ONE anchor, aimed per picture (aimSave; a step re-aims it, the kind included). A FILE downloads
  // through the anchor's download attribute (the user 2026-08-19): the browser saves the same bytes the lightbox is
  // showing — the pinned URL when a pin rode in, so a re-generated file can't swap the image between viewing and saving
  // — under the path's basename. A URL picture opens its ORIGINAL in a new tab instead (2026-10-20): the bytes live on
  // someone else's server, where a download attribute is ignored anyway, and the page that serves them may say more
  // than the picture; the tab is disowned (noopener noreferrer, the rule for every tab this UI opens) and the chat's
  // link opener (render.ts) opens it the same way. A data: picture has no original a browser will open as a page, so it
  // downloads, under "image" and its type's extension. The drawing follows the meaning: the tray to save, the
  // popout (the file viewer's "Open as a tab") to leave.
  const dl = document.createElement("a");
  dl.className = "fileview-btn fileview-icon romp-lightbox-dl";
  const aimSave = (e: LightboxSource) => {
    const leaves = "url" in e && !/^data:/i.test(e.url);
    dl.href = lightboxSrc(e);
    if (leaves) {
      dl.removeAttribute("download");
      dl.target = "_blank";
      dl.rel = "noopener noreferrer";
    } else {
      dl.download = "url" in e ? dataSaveName(e.url) : e.path.slice(e.path.lastIndexOf("/") + 1) || "image";
      dl.removeAttribute("target");
      dl.removeAttribute("rel");
    }
    // the tray icon every download control should wear (the composer buttons' stroke family) as an
    // inline SVG: the old text glyph (U+2B73, arrow-to-bar) has no coverage in the mac system fonts and
    // rendered as a tofu box instead of an icon (the user 2026-08-19). A literal — no sanitize.
    dl.innerHTML = leaves ? ICON_POPOUT : ICON_DOWNLOAD;   // icons.ts: one drawing per meaning, shared with the file viewer's bar (T367)
    const word = leaves ? "Open original" : "Download";
    dl.title = word;
    dl.setAttribute("aria-label", word);
  };
  aimSave(src);
  dl.onclick = (ev) => ev.stopPropagation();               // saving must not also dismiss
  // copy beside download (the user 2026-08-31): the image ITSELF onto the clipboard. It reads the
  // CURRENT img's src at CLICK time — mkImg bakes the pin param into src and an arrow step rebinds
  // `img`, so a pinned historical version copies as exactly what is on screen, and stepping needs no
  // retarget bookkeeping. Rendered only where the clipboard API exists (an insecure-context http
  // origin drops navigator.clipboard entirely — canPreview's honest-absence precedent: no dead
  // control). Clipboards take image/png; any other source re-encodes through a canvas. The
  // ClipboardItem takes the PROMISE form so write() runs synchronously inside the click gesture
  // (Safari refuses a write that awaits first — the tailnet phone case). Success and failure both
  // speak in place, the file viewer's glyph swap (T367): the press dims the button in the same tick
  // (click-safe: every press acknowledges, ui/CLAUDE.md; a large jpeg's decode is not instant), then a
  // check and "Copied", or a cross whose words name the reason, and the button restores itself either
  // way on ONE timer that every swap clears (two presses inside the pulse never let the first wipe the
  // second's check early; the viewer's copyTimer rule). A URL picture copies the same way when its host lets
  // this page read the bytes; one that does not (no CORS) fails in place, its reason in the cross's words.
  const COPY_SVG = ICON_COPY;   // icons.ts: the one two-sheets drawing, shared with the file viewer's bar (T367)
  let cp: HTMLButtonElement | null = null;
  if (curImg && typeof ClipboardItem !== "undefined" && navigator.clipboard && navigator.clipboard.write) {
    const btn = document.createElement("button");
    cp = btn;
    btn.type = "button";
    btn.className = "fileview-btn fileview-icon romp-lightbox-copy";
    let copyTimer: number | null = null;                 // window.setTimeout's handle (a number in the page)
    const say = (icon: string, word: string, cls: string, ms: number | null) => {
      btn.innerHTML = icon; btn.title = word; btn.setAttribute("aria-label", word);
      btn.classList.remove("ok", "err", "fileview-busy"); if (cls) btn.classList.add(cls);
      if (copyTimer) { window.clearTimeout(copyTimer); copyTimer = null; }
      if (ms !== null) copyTimer = window.setTimeout(() => say(COPY_SVG, "Copy image", "", null), ms);
    };
    say(COPY_SVG, "Copy image", "", null);
    if (isVideoSource(src)) btn.style.display = "none";   // a video has no picture to copy; a step onto a picture shows it again
    btn.onclick = (ev) => {
      ev.stopPropagation();                                // copying must not also dismiss
      say(COPY_SVG, "Copy image", "", null);               // a press inside the last pulse: its check and its restore timer go, so the restore never wipes this press's dim (review, round two)
      btn.classList.add("fileview-busy");                  // the same-tick acknowledgement
      const src = curImg!().src;
      const png = (async () => {
        const blob = await (await fetch(src)).blob();
        if (blob.type === "image/png") return blob;
        const bmp = await createImageBitmap(blob);         // jpeg/webp → decode → png, the one type
        const cv = document.createElement("canvas");       // every clipboard accepts
        cv.width = bmp.width; cv.height = bmp.height;
        cv.getContext("2d")!.drawImage(bmp, 0, 0);
        return await new Promise<Blob>((res, rej) =>
          cv.toBlob((b) => (b ? res(b) : rej(new Error("png encode failed"))), "image/png"));
      })();
      navigator.clipboard.write([new ClipboardItem({ "image/png": png })]).then(
        () => say(ICON_CHECK, "Copied", "ok", 1400),        // the ack pulse, then back to a button
        (e) => say(ICON_CROSS, "Copy failed: " + ((e && (e as Error).message) || String(e)), "err", 3000));   // loud: the reason, never a silent no-op
    };
  }
  const close = document.createElement("button");
  close.type = "button";
  close.className = "fileview-btn fileview-close romp-lightbox-close";
  close.textContent = "✕";
  close.title = "Close (Esc)";
  close.setAttribute("aria-label", "Close the picture");
  group.append(dl, ...(cp ? [cp] : []));
  acts.append(group, close);
  if (cue) bar.append(name, cue, acts); else bar.append(name, acts);
  inner.prepend(bar);                                      // FIRST in the column: above the picture and the PDF frame
  wrap.appendChild(inner);
  // closing stops a video (2026-10-21): its sound and its download end with the lightbox, not some stable state later
  const dismiss = () => {
    const cur = curImg ? curImg() : null;
    if (cur && cur.tagName === "VIDEO") stopVideo(cur as HTMLVideoElement);
    wrap.remove(); document.removeEventListener("keydown", onKey, true);
  };
  const onKey = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") { ev.stopPropagation(); dismiss(); return; }
    if (ev.ctrlKey || ev.altKey || ev.metaKey) return;   // a modified arrow is a chord (the shell's session pair), not a step: two capture
    //                                                      listeners on one document both fire whatever the other stops (review, 2026-09-23)
    if ((ev.key === "ArrowLeft" || ev.key === "ArrowRight") && step) {
      ev.stopPropagation(); ev.preventDefault();         // the chat must not scroll under the lightbox
      step(ev.key === "ArrowLeft" ? -1 : 1);             // ← older, → newer — the transcript's own order
    }
  };
  close.onclick = (ev) => { ev.stopPropagation(); dismiss(); };
  wrap.onclick = (ev) => { if (ev.target === wrap) dismiss(); };   // backdrop closes; content clicks don't
  document.addEventListener("keydown", onKey, true);
  document.body.appendChild(wrap);
  // AUTOPLAY ON OPEN (2026-10-21): a video opened by a tap or a click starts playing, sound on. That click is the user's
  // gesture, which every browser's autoplay policy accepts (openLightbox runs inside the click handler, synchronously),
  // so no muted-autoplay workaround is needed. A browser that declines anyway (a stricter policy, a low-power mode)
  // leaves it paused with its controls showing, which is what a refusal should look like; the rejection is the browser's
  // answer, not an error to report. A step onto a video by the arrows never starts it: browsing is not watching.
  const first = curImg ? curImg() : null;
  if (first && first.tagName === "VIDEO") { const pl = (first as HTMLVideoElement).play(); if (pl) pl.catch(() => { /* declined: paused, controls up */ }); }
  // the column's FLOOR (review, round two): a picture narrower than the download-and-copy group (about 68px) let the group run
  // out of the column to the left, since the bar contributes no intrinsic width (contain: inline-size: the picture sets the
  // column, never the path text). The floor is the GROUP's own width, read off its controls (their widths are intrinsic:
  // flex 0 0 auto buttons in a constrained box still measure themselves), set once per open as the variable the sheet reads
  // (.romp-lightbox-inner min-width). The close may wrap beneath the group under a tiny picture; the picture centres under them.
  const ctl = Array.from(group.children) as HTMLElement[];
  inner.style.setProperty("--lb-acts-w", Math.ceil(ctl.reduce((a, c) => a + c.getBoundingClientRect().width, 0) + 4 * Math.max(0, ctl.length - 1) + 8) + "px");
}

// FULL-SIZE inline render for a mentioned image in the CHAT (the user 2026-07-20, who wanted not even a
// thumbnail but a rendered image, like the user messages). Self-verifying —
// a path the kernel can't serve removes itself — and an image click still opens the lightbox. Images
// render at the user-image scale (.path-full-img mirrors .user-img's 320px cap, one size per
// information type). A PDF is a labeled CARD, not an auto-loading inline viewer (click → the
// lightbox; Cmd/Ctrl- or middle-click → its own browser tab, openPdf):
// the first cut embedded an <iframe> per mentioned PDF, and a browser set to "Download PDFs" (or one
// that declines to render inline) saved a FRESH COPY on every chat re-render — the user's Downloads
// folder silently filled with datasheet copies (2026-07-20). A fetch must be user-initiated, once.
// Web only — callers gate on canPreview and fall back per surface.
// `verified`: the KERNEL already stat'd this path (spacePaths / a pathLinks verdict), so a load
// error is TRANSIENT — the kernel restarting mid-fetch, a tunnel blip — not a dead path. Removing
// the box then erases the preview silently until some later re-render (the user 2026-08-15, who sat
// through exactly that: verified path pills, no images, no cue). A verified path's failure therefore
// stays VISIBLE — a "preview unavailable — tap to retry" chip in the figure's spot — per the
// fail-loudly rule. Only an UNVERIFIED path (old kernel, no pathLinks key) keeps self-removal:
// there the error really does mean "no such file".
export function previewFull(path: string, sid?: string | null, verified = false, pin?: string): HTMLElement | null {
  const kind = previewKind(path);
  if (!kind || !canPreview()) return null;
  const box = document.createElement("span");
  box.className = "path-full" + (kind === "pdf" ? " pdf" : "");
  box.title = path;
  if (kind === "video") {
    // A mentioned video file (2026-10-21): the chat's still (makeVideoStill), its bytes from /file like a picture's, which
    // answers a video's Range requests (Safari and iOS play nothing without them). Never pinned: the kernel's mention pins
    // are pictures only (a clip would fill the pin store), so a re-recorded clip shows its newest take. Its failure follows
    // the picture's rule: a path the kernel verified fails VISIBLY, with a tap to retry and the reconnect-class heal; an
    // unverified one hides and stays healable (a later success shows it again).
    const url = fileUrl(path, sid);
    const v = document.createElement("video");
    v.setAttribute("src", url);
    const still = makeVideoStill(v, url, true);
    still.onclick = (ev) => { ev.stopPropagation(); openLightbox({ path, sid }, v); };
    box.appendChild(still);
    v.addEventListener("error", () => {
      const retry = () => { box.style.display = ""; box.textContent = ""; box.appendChild(still); v.load(); };
      settledPreviews.set(box, retry);                     // the link coming back is new information: one more try
      if (!verified) { box.style.display = "none"; return; }
      const wait = mkWait(box);
      const chip = document.createElement("span");
      chip.className = "path-full-retry";
      chip.textContent = "⚠ video unavailable — tap to retry";
      chip.title = path;
      chip.onclick = (ev) => { ev.stopPropagation(); retry(); };
      wait.appendChild(chip);
    });
    return box;
  }
  if (kind === "pdf") {
    box.classList.add("path-full-pdfcard");
    const tag = document.createElement("span");
    tag.className = "path-thumb-tag";
    tag.textContent = "PDF";
    const nm = document.createElement("span");
    nm.className = "path-thumb-name";
    nm.textContent = path.slice(path.lastIndexOf("/") + 1);
    box.append(tag, nm);
    box.style.cursor = "pointer";
    box.title = "Open " + path + " (Cmd/Ctrl-click or middle-click: a browser tab of its own)";
    box.onclick = (ev) => { ev.stopPropagation(); openPdf(path, sid, ev); };   // plain: the lightbox; modified: its own tab
    box.onmousedown = (ev) => { if (ev.button === 1) ev.preventDefault(); };   // the middle press: no autoscroll (it would swallow the auxclick)
    box.onauxclick = (ev) => { if (ev.button !== 1) return; ev.stopPropagation(); openPdf(path, sid, ev); };
    // a chip can't self-verify like an <img> — HEAD-probe (headers only, no body — never a download)
    // so a missing PDF never shows a dead card. A kernel-VERIFIED card skips the probe: the kernel
    // said the file exists, and a transient probe failure must not erase the card.
    // an UNVERIFIED card's failed probe HIDES the card and keeps it registered for the heal
    // events — never removed from the DOM: one transient failure (a kernel-restart window, a tunnel blip)
    // used to erase the figure until a send's re-render minted a fresh box (the user 2026-08-24).
    // The hidden sentinel keeps the spot healable with zero visual noise when the mention really
    // is dead; a probe that later succeeds unhides the card in place.
    if (!verified) {
      // a 502 is the relay's link verdict (HEAD carries no body to read): that one waits for the reconnect-class
      // heal like an image's link failure (T291); any other miss keeps the per-message heal
      const probe = () => fetch(fileUrl(path, sid), { method: "HEAD" })
        .then((r) => { if (r.ok) { box.style.display = ""; } else { box.style.display = "none"; (r.status === 502 ? settledPreviews : failedPreviews).set(box, probe); } })
        .catch(() => { box.style.display = "none"; failedPreviews.set(box, probe); });
      probe();
    }
  } else {
    // `pin` freezes this MESSAGE's embed to its mention-time bytes (kernel _pin_mention): the sid
    // rides too (the pin store lives on the owning kernel; the relay forwards the query untouched),
    // and a pin whose blob was evicted falls back server-side to the live file.
    const url = fileUrl(path, sid) + (pin ? "&pin=" + encodeURIComponent(pin) : "");
    // A verified preview whose fetch died usually died because the KERNEL was away (a restart mid-
    // deploy — the 2026-08-15 report hit exactly the converge-restart window), and delta-send never
    // rebuilds an old turn's DOM, so the chip would otherwise sit until a human tapped it. Bounded so
    // a genuinely-dead file settles on the tap chip instead of re-fetching on every push forever —
    // but an attempt that MADE PROGRESS refills the budget (see the resumable retry below): forward
    // motion is the event proving the link works sometimes, and only truly dead attempts spend it.
    let autoRetries = 3;
    let chipHealedErr: string | null = null;    // the error a settled chip already spent its one heal on
    let fails = 0;                                   // total failed attempts — the chip's copy escalates
    // RESUMABLE RETRY STATE (the user 2026-08-16, on flaky wifi: every retry restarted the transfer
    // from byte 0, so a large figure never finished arriving — and the swirl gave no idea how far it
    // got). The happy path below stays a plain <img> (the browser cache makes the chat's constant
    // re-renders free); once a load has FAILED, retries switch to a managed fetch that keeps every
    // byte received so far and asks the kernel for the REST (Range: bytes=N-, honored by /file and
    // across the federation relay). A dropping link then finishes the picture ACROSS attempts, with
    // the swirl narrating real progress ("1.2 of 3.4 MB" — content-length makes it knowable). No
    // artificial deadline anywhere: only a real network error ends an attempt.
    let parts: Uint8Array[] = [];
    let got = 0;
    let total = 0;
    let fetching = false;                            // one managed attempt at a time (a tap mid-fetch no-ops)
    let lastErr = "";                                // the newest attempt's server-side reason, shown verbatim
    // The wait box is REUSED across attempts (T291, the user 2026-09-09: two figures on a remote session
    // whose relay was failing re-attempted on every kernel push, and each attempt swapped the box between
    // the one-line "fetching…" and the five-line failure note — the whole transcript above the reader
    // moved by four lines, ten times a second, for the length of the streaming turn). One wait element,
    // one swirl, one note, kept from attempt to attempt; only the note's TEXT changes, and only when the
    // words do. An attempt therefore never changes the box's layout; the picture landing does.
    const waitBox = (): { wait: HTMLElement; note: HTMLElement } => {
      let wait = box.firstElementChild as HTMLElement | null;
      if (!wait || !wait.classList.contains("path-full-wait")) wait = mkWait(box);
      const chip = wait.querySelector(".path-full-retry") as HTMLElement | null;
      if (chip) {
        // the one new-evidence heal (autoRetries 1) keeps the chip's shape: the chip carries the words while it
        // runs. A tap or a reconnect heal re-armed the budget (3): the loading persona returns, swirl and note,
        // inside the same wait element (the 2026-08-16 rule: the chip is the give-up state only)
        if (autoRetries <= 1) return { wait, note: chip };
        chip.remove();
      }
      let spin = wait.querySelector(".path-load-spin") as HTMLImageElement | null;
      if (!spin) {
        spin = document.createElement("img");
        spin.className = "path-load-spin";
        spin.src = "/media/romp-swirl-glyph.svg";
        spin.alt = "loading preview…";
        wait.appendChild(spin);
      }
      let note = wait.querySelector(".path-load-note") as HTMLElement | null;
      if (!note) {
        note = document.createElement("span");
        note.className = "path-load-note";
        wait.appendChild(note);
      }
      return { wait, note };
    };
    const setNote = (note: HTMLElement, text: string) => { if (note.textContent !== text) note.textContent = text; };
    const showChip = () => {
      if (!box.isConnected) return;                  // the turn re-rendered; a fresh box owns this spot now
      // ONE continuous narrative while the machinery is still going (the user 2026-08-16, third
      // report: the box flipped between "trying" and "unavailable" on every auto-retry cycle even
      // though it eventually loaded — the state bounced, so the UI read as impatient). While bounded
      // auto-retries remain, the wait box KEEPS its loading persona — swirl + a note carrying the
      // failure and the plan ("dropped at 1.2 MB of 3.4 MB — retrying · tap to retry now"), the
      // whole box tappable — and the ⚠ chip appears only when the budget is genuinely spent. A
      // repeat failure must still READ as a response to a tap: the note re-pulses on swap-in.
      // INFRASTRUCTURE-DOWN failures are FREE (the user 2026-08-17: figures gave up seconds after a
      // kernel restart — the tunnel re-dial window produces instant "no attached host" 404s and
      // "tunnel not answering" 502s, and three of those spent the whole budget right before the link
      // came back). A failure that names the LINK, not the image, doesn't decrement: the preview
      // re-attempts when the link itself comes back (the reconnect-class heal below, T291), and only
      // real verdicts — a true not-found from the owning kernel, a transfer that died with zero
      // progress — spend attempts.
      const transient = /tunnel to .* is not answering|no attached host|re-dialing/i.test(lastErr);
      if (autoRetries > 0 || transient) {
        // A LINK failure heals on the reconnect-class event alone (T291): romp:wsup when this page's
        // kernel socket comes back, hostUp when the federated tunnel does (the tunnel poll dispatches
        // it on the down→up transition), romp:hostRelayUp when the host's relay socket reopens (a
        // remote kernel's restart fires that one and neither of the others), never on the next kernel push. A streaming session pushes
        // many times a second, and a dead link answered every one of those instantly: the per-push
        // retry was the flicker. The budget stays untouched, as before; the tap still retries at once.
        if (transient) settledPreviews.set(box, () => build(true));
        else { autoRetries--; failedPreviews.set(box, () => build(true)); }
        const { wait, note } = waitBox();
        wait.title = path + " — tap to retry now";
        wait.style.cursor = "pointer";
        wait.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
        setNote(note, (got > 0 ? "connection dropped at " + fmtBytes(got, total)
                               : lastErr || "connection dropped")
                      + (transient ? " — retries when the link is back · tap to retry now" : " — retrying · tap to retry now"));
        if (fails > 1) {
          note.classList.add("path-retry-flash");
          note.addEventListener("animationend", () => note.classList.remove("path-retry-flash"), { once: true });
        }
        return;
      }
      const wait = mkWait(box);
      const chip = document.createElement("span");
      chip.className = "path-full-retry";
      // the budget is spent: three attempts gained nothing (progress refills it), so say so plainly
      chip.textContent =
        (got > 0 ? "⚠ connection dropped at " + fmtBytes(got, total)
                 : lastErr ? "⚠ " + lastErr
                 : (fails > 1 ? "⚠ still unavailable" : "⚠ preview unavailable"))
        + " — tap to retry";
      chip.title = path;
      chip.onclick = (ev) => { ev.stopPropagation(); autoRetries = 3; ackTap(ev); build(true); };   // a tap re-arms persistence
      wait.appendChild(chip);
      // A settled chip still rides the push-heal (the user 2026-08-18: "they never render on their
      // own — only when I send a message"): only the retrying branch registered for the heal, so a
      // spent budget dropped the box from the map forever — pushes and tunnel recovery ignored it,
      // and a send only "worked" because the tail re-render minted a FRESH box. One heal attempt
      // per registration, and the box re-registers ONLY when the error CHANGED (new information —
      // the same verdict re-answered is no reason to fetch again): a truly-dead figure costs one
      // extra fetch per new-evidence transition, never one per push.
      if (lastErr !== chipHealedErr) {
        failedPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 1; build(true); });
      }
      // …and a RECONNECT-class event (romp:wsup / hostUp) heals a settled chip REGARDLESS of the
      // error text (the user 2026-08-24): a byte-identical 404 while the file was still being
      // written — or a constant connection-refused — parked the chip inert forever, though the
      // link coming back is new information even when the words didn't change. The budget refills
      // exactly like a send's fresh box; reconnects are rare, so this can't hammer.
      settledPreviews.set(box, () => { chipHealedErr = lastErr; autoRetries = 3; build(true); });
      if (fails > 1) {
        chip.classList.add("path-retry-flash");
        chip.addEventListener("animationend", () => chip.classList.remove("path-retry-flash"), { once: true });
      }
    };
    const failAfterBeat = (started: number) => {
      fails++;
      // A retry that dies instantly (a dead tunnel resets the connection in milliseconds) would
      // flash the swirl for one frame and put back an identical chip — an ignored-looking tap.
      // Hold the swirl to a perceivable beat before swapping. Presentation smoothing only: the
      // attempt has already failed, and the auto-heal registration rides the same swap.
      const left = MIN_RETRY_SPIN_MS - (Date.now() - started);
      if (left > 0) setTimeout(showChip, left); else showChip();
    };
    const mkImg = (src: string) => {
      const img = document.createElement("img");
      img.className = "path-full-img";
      img.src = src;
      img.alt = path;
      img.loading = "lazy";       // off-screen figures don't fetch until scrolled near (eager-all, 2026-08-30)
      img.decoding = "async";     // and never decode on the main thread mid-scroll
      img.onclick = (ev) => { ev.stopPropagation(); openLightbox({ path, sid, pin }, img); };
      return img;
    };
    // every tap READS as a tap even when the click lands mid-attempt and build() no-ops on its
    // `fetching` guard (the buttons-always-acknowledge rule: an unacknowledged tap gets re-tapped)
    const ackTap = (ev: Event) => {
      const t = ev.currentTarget as HTMLElement | null;
      if (!t) return;
      t.classList.add("path-retry-flash");
      t.addEventListener("animationend", () => t.classList.remove("path-retry-flash"), { once: true });
    };
    const resumeFetch = async (note: HTMLElement) => {
      const gotBefore = got;
      const r = await fetch(url, { cache: "no-store",
                                   headers: got > 0 ? { Range: "bytes=" + got + "-" } : {} });
      if (r.status === 206) {
        // the kernel continues our partial — the entity size rides Content-Range's "/<size>" tail
        total = parseInt((r.headers.get("Content-Range") || "").split("/")[1] || "0", 10) || total;
      } else if (r.ok) {
        parts = []; got = 0;                         // full body (no range asked, or the server restarted us)
        total = parseInt(r.headers.get("Content-Length") || "0", 10) || 0;
      } else {
        // the error BODY is the diagnostic (the kernel's 502 says "tunnel to <host> is not
        // answering") — a bare status code hid that the IMAGE was fine and the LINK was down
        let why = "";
        try { why = ((await r.text()) || "").split("\n")[0].slice(0, 120); } catch { /* body unavailable */ }
        // a refused status VOIDS the resume state (the user 2026-08-18, whose re-generated figures
        // never loaded): the file changed under our offset — an agent re-plotting the same name
        // shrinks it — and the kernel's 416 expects the client to RESTART cleanly. Keeping `got`
        // made every later attempt, tap and heal alike, replay the same stale Range and fail
        // deterministically fast, while a send's fresh box (got=0) rendered instantly.
        parts = []; got = 0;
        throw new Error(why || "http " + r.status);
      }
      const reader = r.body!.getReader();
      try {
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          parts.push(value);
          got += value.byteLength;
          note.textContent = "fetching… " + fmtBytes(got, total);
        }
      } finally {
        if (got > gotBefore) autoRetries = 3;        // progress refills the budget — the link works sometimes
      }
      if (total && got < total) throw new Error("cut at " + got);   // stream ended early → resume next attempt
      const blob = new Blob(parts as BlobPart[], { type: IMG_MIME[path.slice(path.lastIndexOf(".") + 1).toLowerCase()] || "" });
      return URL.createObjectURL(blob);
    };
    const build = (bust: boolean) => {
      const done = resolvedUrls.get(url);
      if (done) {                                    // already fully fetched this page-life → instant
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(done));
        return;
      }
      if (!bust) {                                   // first attempt: the plain <img> happy path
        box.textContent = "";
        const img = mkImg(url);
        img.onerror = () => {
          // unverified → the SAME retry machinery as verified, just invisible while failed (see
          // the probe note above): the box hides instead of wearing the chip, and a later success
          // unhides it — never self-removed, which erased the spot until a send re-rendered
          if (!verified) box.style.display = "none";
          failAfterBeat(0);                          // no beat on the first attempt — the cue was already up
        };
        withLoadCue(box, img, url);   // mini swirl holds the spot until the load event (memo on the un-busted url)
        box.appendChild(img);
        return;
      }
      if (fetching) return;
      fetching = true;
      const started = Date.now();
      // the wait box stays as it is while the attempt runs (T291): a note already up keeps its words, so a
      // retry from a failure note does not shrink the box to one line and grow it back on the next failure
      const { note } = waitBox();
      if (!note.textContent) setNote(note, got > 0 ? "resuming… " + fmtBytes(got, total) : "fetching…");
      resumeFetch(note).then((objUrl) => {
        fetching = false;
        lastErr = "";
        rememberResolved(url, objUrl);
        loadedOnce.add(url);                         // re-renders skip the cue — the bytes are in hand
        if (!box.isConnected) return;
        box.style.display = "";                      // a hidden unverified sentinel that healed comes back
        box.textContent = "";
        box.appendChild(mkImg(objUrl));
      }).catch((e: unknown) => {
        fetching = false;
        lastErr = String((e as Error)?.message || "");
        if (lastErr.startsWith("cut at ")) lastErr = "";   // a mid-stream cut narrates via got/fmtBytes
        if (!verified) box.style.display = "none";         // hidden while failed, healable — never removed
        failAfterBeat(started);
      });
    };
    build(false);
  }
  return box;
}

// Failed VERIFIED previews awaiting recovery. A kernel push arriving IS the kernel-is-back event —
// no pushes arrive while it's down, so retrying on push can't spam — and render.ts calls this on
// every incoming kernel message, healing the chips without a tap (event-based; the tap chip stays
// as the manual path and the backstop once a box's bounded auto-retries are spent).
const failedPreviews = new Map<HTMLElement, () => void>();
export function retryFailedPreviews(): void {
  retryMdImgProbes();                                // parked markdown images with attempts left (T291c): the budget rides the URL
  if (!failedPreviews.size) return;
  for (const [box, rebuild] of Array.from(failedPreviews.entries())) {
    failedPreviews.delete(box);                      // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();                  // a re-rendered turn made a fresh box — let the old go
  }
}

// Settled chips (auto-retry budget spent) awaiting a RECONNECT-class heal — romp:wsup (this page's
// kernel socket came back) or hostUp (a federated tunnel recovered). Drained only by these events,
// never by the per-message heal above, so a dead figure costs one fetch per reconnect, not per push.
const settledPreviews = new Map<HTMLElement, () => void>();
export function refreshSettledPreviews(): void {
  healMdImgs();                                      // parked markdown images ride the same reconnect-class heal (T291c)
  if (!settledPreviews.size) return;
  for (const [box, rebuild] of Array.from(settledPreviews.entries())) {
    settledPreviews.delete(box);                     // one attempt per registration; re-registers on error
    if (box.isConnected) rebuild();
  }
}

// Markdown-inline <img> (a figure written as markdown in a message body): marked leaves `![fig](path)` as
// <img src="path"> (the chat rewrites no image source), the browser resolves the path against the page's
// origin, and the kernel answers 404, so the img fails and the browser shows its alt text. The 2026-08-24
// heal re-set that img's src on every kernel message; each reset hid the alt text while the reload was in
// flight and the 404 showed it again, so a streaming turn flipped every such caption on and off at the
// push rate (T291c, the user 2026-09-09: three captions, two to four lines each, ten times a second). A
// failed markdown image is PARKED instead: its src leaves (into data-md-src), the element wears
// .md-img-failed, and the browser shows the alt text as a stable caption: no src, no fetch, nothing to
// flip. No per-message path touches the img on the page: a URL the kernel serves gets a bounded probe OFF the
// DOM (servedByKernel and retryMdImgProbes, below), every other parked URL waits for a reconnect. A URL that
// failed is remembered for the page life, and the reconnect-class heal (refreshSettledPreviews: romp:wsup,
// hostUp, romp:hostRelayUp) probes every remembered URL once OFF the DOM with a detached Image, whether or not
// an img with it is on the page (a turn evicted from the rendered window, a closed tab): a failure changes
// nothing on screen, a success lands the picture on every parked img with that URL. The sanitizer post-pass
// (mdImgPostPass, registered by render.ts) parks a re-rendered img with a remembered URL BEFORE the browser
// fetches it, so a re-render of the same markdown reuses the state instead of fetching, failing and flipping
// once more. DOMPurify strips inline handlers
// (correctly), so the failures are caught by ONE document-level capture listener (error events do not
// bubble but do capture); previews' own <img>s are skipped: their machinery (budgets, resume, chips) owns those.
const mdImgFailed = new Set<string>();             // URLs that failed this page life: a re-render parks them before any fetch
const mdImgProbe = new Map<string, number>();      // served URLs with per-message attempts left: the budget rides the URL, not the img
const mdImgProbing = new Map<string, number>();    // served URLs with a per-message probe in flight, by that probe's token: one at a time per URL
let mdImgProbeSeq = 0;                             // the tokens: a failure counts only for the probe whose token is still the URL's
let mdImgHealOn = false;
function parkMdImg(img: HTMLImageElement, url: string): void {
  mdImgFailed.add(url);
  img.dataset.mdSrc = url;
  if (img.hasAttribute("src")) img.removeAttribute("src");   // no src: the browser shows the alt text and fetches nothing
  img.classList.add("md-img-failed");
  img.classList.remove("md-img-zoom");               // a caption, not a picture: no picture box, no zoom cursor
}
/** The chat's post-pass (render.ts runs it on md() and userMd() output after the sanitize, NEVER through the shared
 *  sanitizer: the file viewer rewrites its images' paths to /file URLs after its own sanitize, and a park inside the
 *  sanitizer removed the src that rewrite keys on, the review's find): an img whose URL is already known to fail is
 *  parked at RENDER, before any fetch. Every image it sees is a picture a message embedded, and wears .md-img: the one
 *  mark the zoom hook below reads, so a picture the file viewer or a hover card draws is never its business. One whose
 *  URL already loaded, and that will show picture-sized, takes .md-img-zoom here too, so a re-render draws it in the
 *  picture box at once (mdImgDims and shownHeightOf, below); one alone on its line takes .md-solo, the box's block
 *  form (mdSolo). Its videos become the chat's still (mdVideoPostPass). */
export function mdImgPostPass(root: ParentNode): void {
  for (const img of Array.from(root.querySelectorAll("img")) as HTMLImageElement[]) {
    img.classList.add("md-img");                     // a picture the message's markdown embeds: the zoom hook's mark (installMdImgZoom)
    if (mdSolo(img)) img.classList.add("md-solo");   // alone on its line: the box as a block, when it zooms (2026-10-21)
    const u = img.src || "";
    const d = u ? mdImgDims.get(u) : undefined;
    if (u && mdImgFailed.has(u)) parkMdImg(img, u);
    else if (d && shownHeightOf(img, d) >= ZOOM_MIN_H && !img.closest("a, summary") && opensInLightbox(u)) img.classList.add("md-img-zoom");
  }
  mdVideoPostPass(root);                             // and its videos become the still that opens the lightbox (2026-10-21)
}
/** A URL the kernel actually answers (/file, a remote relay's /file, /media): a file an agent is still writing or a
 *  relay behind a blip may come back with no reconnect event, so those get a BOUNDED probe on the per-message path
 *  (three attempts, off the DOM, so the caption never moves for them). The budget is keyed on the URL, not on the img
 *  that failed: a streaming turn is re-rendered on every push, so that img is off the page by the next push and the
 *  post-pass has parked a fresh one, which inherits the attempts left. An origin-resolved path no route serves is
 *  park-only, since nothing but a reconnect could change its answer. */
function servedByKernel(u: string): boolean {
  try {
    const p = new URL(u, location.href).pathname;
    return p === "/file" || /^\/remote\/[^/]+\/file$/.test(p) || p.startsWith("/media/");
  } catch { return false; }
}
/** One detached probe of a parked URL. On load the picture lands on every parked img with that URL that is on the
 *  page NOW, re-queried, not a snapshot: a re-render during the probe parks a fresh img, which a snapshot missed
 *  (the review's find). On failure nothing on screen changes. */
function probeMdImgUrl(u: string, onFail?: () => void): void {
  const probe = new Image();
  probe.onload = () => {
    mdImgFailed.delete(u);                           // first: a rebuild from here on fetches normally
    mdImgProbe.delete(u); mdImgProbing.delete(u);    // a healed URL has no pending budget
    for (const img of Array.from(document.querySelectorAll("img.md-img-failed[data-md-src]")) as HTMLImageElement[]) {
      if (img.dataset.mdSrc !== u || !img.isConnected) continue;
      img.classList.remove("md-img-failed");
      delete img.dataset.mdSrc;
      img.src = u;                                   // the bytes are in the browser's cache: the picture lands
    }
  };
  probe.onerror = () => { if (onFail) onFail(); };  // still down: the parked captions stay exactly as they are
  probe.src = u;
}
/** The per-message path for the served URLs with attempts left: one detached probe per URL per push, the next armed
 *  only once the previous has failed (a push while it is pending fires nothing), whatever img carries the URL now and
 *  whether or not one is on the page (the probe is off the DOM; a load re-queries the page). Run from
 *  retryFailedPreviews, so it rides the kernel-message event like the figure previews' own retries. Each probe holds a
 *  token, and its failure counts only while that token is still the URL's: a load of the URL by any probe retires it,
 *  and the URL may since have failed again with a fresh budget and a newer probe in flight, so a stale failure spends
 *  nothing and clears nothing (a bare in-flight check is not enough: the newer probe has re-marked the URL). */
function retryMdImgProbes(): void {
  for (const [u, left] of Array.from(mdImgProbe.entries())) {
    if (mdImgProbing.has(u)) continue;
    const token = ++mdImgProbeSeq;
    mdImgProbing.set(u, token);
    probeMdImgUrl(u, () => {
      if (mdImgProbing.get(u) !== token) return;   // retired by a load meanwhile: the fresh budget and any newer probe stand
      mdImgProbing.delete(u);
      if (left > 1) mdImgProbe.set(u, left - 1);    // still down: one attempt spent
      else mdImgProbe.delete(u);                     // spent: the reconnect heal alone from here
    });
  }
}
/** The reconnect-class heal for parked markdown images: one detached probe per remembered URL. The remembered set is
 *  the source, since a URL whose turn left the rendered window or whose tab was closed has no img on the page, and a
 *  re-render would park it again for the page life; the page's parked imgs are read too, for one parked under a URL
 *  the memory no longer holds. */
export function healMdImgs(): void {
  const urls = new Set<string>(mdImgFailed);
  for (const img of Array.from(document.querySelectorAll("img.md-img-failed[data-md-src]")) as HTMLImageElement[]) {
    const u = img.dataset.mdSrc || "";
    if (u) urls.add(u);
  }
  for (const u of urls) probeMdImgUrl(u);
}
export function installMdImgHeal(): void {
  if (mdImgHealOn) return;                           // ensure-once (the click-safety installation rule)
  mdImgHealOn = true;
  document.addEventListener("error", (e) => {
    const img = e.target as HTMLImageElement | null;
    if (!img || img.tagName !== "IMG") return;
    const src = img.src || "";
    if (!src || src.startsWith("data:")) return;     // a broken data: URI has no server to heal
    if (img.onerror || img.closest(".path-full")) return;   // the preview machinery retries its own
    parkMdImg(img, src);
    if (servedByKernel(src)) {                       // bounded and off the DOM: the caption never moves for it
      if (!mdImgProbe.has(src)) mdImgProbe.set(src, 3);   // the budget rides the URL: a re-rendered turn inherits what is left
    }
  }, true);
}

// ── a markdown picture opens the lightbox (2026-10-20, the user on a phone: a picture a message embedded ran off the
// screen and never zoomed, where the pictures romp draws from a file path did) ─────────────────────────────────────
// The pictures are the ones mdImgPostPass marks .md-img on md() and userMd() output, the bodies of every chat surface
// (the transcript, a subagent viewer, a comment thread), and nothing else. ONE pair of document-level listeners,
// installed once: the transcript rebuilds these images on every push, so nothing hangs on an image (the click-safety
// rule, ui/CLAUDE.md).
//   load (capture: a load does not bubble) decides from the height the picture SHOWS at whether it zooms, and marks it
//     .md-img-zoom, the class the zoom cursor rides, and with .md-solo the path preview's picture box (styles.css). A
//     picture under ZOOM_MIN_H px tall is a glyph in a line of text (a status badge is 20px, an emoji or an icon 16 to
//     24): zooming it shows nothing more, so it neither zooms, nor wears the box, nor stops the lightbox's arrows
//     (mdImgTiny, which the sequence filter reads). A URL that loaded is remembered with its natural size (mdImgDims), so
//     the post-pass stamps .md-img-zoom on the next render of it before it is on the page: the transcript rebuilds a streaming turn's
//     pictures on every push, and a box that waited for each fresh element's load would step the turn's height a
//     few px (inline to block) on every push. The load still rules on each element, so the stamp is only a head start.
//   click (capture, so a fold or a bubble around the picture never also toggles under it) opens the lightbox on the
//     picture, by its URL, handing over the element so the chat's sequence knows which transcript it sits in.
// A picture inside a link is the LINK's: its click follows the link (render.ts's opener, as it always did) and it wears
// no zoom cursor, so a linked badge or a thumbnail that points at its page keeps meaning what its author wrote. A
// picture inside a <details>'s <summary> is the summary's by the same rule: its click opens or closes the details
// (2026-10-20 review: the click's preventDefault swallowed the toggle). A picture opens on what it SHOWS, currentSrc:
// a srcset's candidate when it has one, else its src. One with neither shows nothing a lightbox could, and never zooms
// (2026-10-20 review: a srcset with no src opened an empty lightbox whose "Open original" was the dashboard itself). And
// it opens only on the schemes the arrows' sequence keeps (opensInLightbox): the sanitizer checks a srcset's first
// candidate alone, so a later one could name a javascript: or data:text/html URL, and the browser may pick it; such a
// picture never loads, but the click refuses it by rule rather than by that accident. Web dashboard only, as every
// preview (canPreview): the VS Code webview loads only data: pictures and has no kernel origin.
// The threshold is the height the picture SHOWS at, not its file's (2026-10-21, the review of the shared box: an
// `<img height="20">` of a large picture is a glyph in its line, and judging it by its natural height turned it into a
// 20px framed blob on a line of its own). img.height is that: the rendered content height of a picture on the page, the
// natural height of one that is not rendered (a closed fold, a hidden tab), where no better answer exists. Before a
// picture is on the page (the post-pass, on a re-render), its height is read off what sizes it: its height attribute,
// else its width attribute through the remembered natural ratio, else the natural height (shownHeightOf); its load then
// rules on the element itself, so that reading is only a head start.
const ZOOM_MIN_H = 32;
const mdImgTiny = new Set<string>();                // URLs that loaded glyph-sized this page life: no stop in the sequence
const mdImgDims = new Map<string, { w: number; h: number }>();   // natural sizes of the URLs that loaded this page life: a re-render wears the box at once
let mdImgZoomOn = false;
const attrPx = (v: string | null): number | null => {
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:px)?\s*$/i.exec(v || "");   // a length in pixels; a percentage says nothing here
  return m ? parseFloat(m[1]) : null;
};
export function shownHeightOf(img: Element, d: { w: number; h: number }): number {
  const h = attrPx(img.getAttribute("height")), w = attrPx(img.getAttribute("width"));
  if (h !== null) return h;
  if (w !== null && d.w > 0) return w * d.h / d.w;
  return d.h;
}
export function mdImgZoomable(img: HTMLImageElement): boolean {
  return img.classList.contains("md-img") && !img.closest("a, summary") && opensInLightbox(img.currentSrc || img.src) && img.complete && img.naturalHeight > 0 && img.height >= ZOOM_MIN_H;
}
export function installMdImgZoom(): void {
  if (mdImgZoomOn || !canPreview()) return;        // ensure-once (the click-safety installation rule)
  mdImgZoomOn = true;
  document.addEventListener("load", (e) => {
    const img = e.target as HTMLImageElement | null;
    if (!img || img.tagName !== "IMG" || !img.classList.contains("md-img")) return;
    if (img.src && img.naturalHeight > 0) {
      if (img.height < ZOOM_MIN_H) mdImgTiny.add(img.src); else mdImgTiny.delete(img.src);   // by src: the sequence's name for it
      mdImgDims.set(img.src, { w: img.naturalWidth, h: img.naturalHeight });               // …and the post-pass's
    }
    img.classList.toggle("md-img-zoom", mdImgZoomable(img));
  }, true);
  // a video still's clip size, remembered by its address the moment its metadata lands (videoDims), so the next render of
  // it is laid out at that size from the start (loadedmetadata does not bubble: the capture phase sees it)
  document.addEventListener("loadedmetadata", (e) => {
    const v = e.target as HTMLVideoElement | null;
    if (!v || v.tagName !== "VIDEO" || !v.classList.contains("md-video")) return;
    const url = (v.parentElement as HTMLElement | null)?.dataset.video || "";
    if (url && v.videoWidth > 0 && v.videoHeight > 0) videoDims.set(url, { w: v.videoWidth, h: v.videoHeight });
  }, true);
  document.addEventListener("click", (e) => {
    const t = e.target as Element | null;
    // a message's video still (mdVideoPostPass): the tap opens the lightbox on its clip, which plays there. A path's or a
    // link's still (inside .path-full) carries its own click, with the file's source (previewFull, previewVideoUrl).
    const still = t && t.closest ? t.closest(".md-video-still") as HTMLElement | null : null;
    if (still && !still.closest(".path-full") && still.dataset.video && opensInLightbox(still.dataset.video, "video")) {
      const v = still.querySelector("video");
      e.preventDefault(); e.stopPropagation();
      openLightbox({ url: still.dataset.video, video: true }, v);
      return;
    }
    const img = t as HTMLImageElement | null;
    if (!img || img.tagName !== "IMG" || !mdImgZoomable(img)) return;
    e.preventDefault(); e.stopPropagation();
    openLightbox({ url: img.currentSrc || img.src, alt: img.alt }, img);
  }, true);
}

// ── a chat video: a STILL that opens the lightbox (2026-10-21, the user wanting a video in the chat treated like a picture:
// the same compact box, a tap to see it large) ─────────────────────────────────────────────────────────────────────────
// A <video> a message writes (`<video controls width="100%" src="…mp4">`, the shape an agent posts), a mentioned path to a
// video file (previewFull) and a link to one (previewVideoUrl) all show ONE still: the clip's first frame in the picture
// box (the shared rules in styles.css: the bubble's width and 320px tall at most, its ratio kept; framed and a block when
// it stands alone on its line), a play badge over it and the zoom cursor. No native controls: they would fight the
// tap that opens the lightbox, where it plays with its controls (openLightbox). Muted, and never playing by itself.
//   width/height attributes never stretch it: the still's own rule sets both auto (styles.css .md-video), which a
//     presentational width="100%" loses to, so a 1920x1080 clip shows at 569x320 in a wide bubble as a picture would.
//   preload="metadata": the browser fetches the header and the first frame, never the whole file.
//   playsinline: iOS otherwise takes a video full screen the moment it plays.
//   "#t=0.001" on its address (STILL_FRAGMENT): iOS Safari paints no frame for a never-played video without a poster;
//     a media fragment that starts it a millisecond in makes Safari fetch and show that frame, and desktop browsers paint
//     the same frame. An address that carries a fragment already keeps the author's. The lightbox plays the address
//     without it, from the start.
// `url` is the address the lightbox opens (no fragment), carried as the still's data-video: the key of the remembered
// size (videoDims) and, for a message's still, what the document-level click opens (installMdImgZoom).
const STILL_FRAGMENT = "#t=0.001";
// The size a clip showed at, by its address, for this page life. The transcript rebuilds a streaming turn's DOM on every
// push, and a fresh <video> lays out at the browser's default 300x150 until its metadata lands, so each rebuild would step
// the turn's height twice. A still whose clip the page has measured is laid out at that size from the start: size
// containment with the clip's dimensions as its intrinsic size and its ratio (sizeLikeClip), so the picture box's caps
// scale it exactly as they scale the loaded clip. The lightbox's video uses it too.
const videoDims = new Map<string, { w: number; h: number }>();
function sizeLikeClip(v: HTMLVideoElement, d: { w: number; h: number } | undefined): void {
  if (!d || !(d.w > 0) || !(d.h > 0)) return;
  v.style.setProperty("contain", "size");
  v.style.setProperty("contain-intrinsic-size", d.w + "px " + d.h + "px");
  v.style.setProperty("aspect-ratio", d.w + " / " + d.h);
}
/** Turn `v` (in the page or in the sanitizer's detached document) into the chat's still of the clip at `url`, wrapped in
 *  the element that carries the badge and takes the tap; returns the wrapper, which takes `v`'s place when `v` has one.
 *  `solo`: it stands alone on its line (mdSolo), so it wears the box as a block. */
export function makeVideoStill(v: HTMLVideoElement, url: string, solo: boolean): HTMLElement {
  const doc = v.ownerDocument;
  for (const a of ["controls", "autoplay", "loop"]) v.removeAttribute(a);   // the lightbox plays it; the chat never does
  v.setAttribute("muted", "");                       // the attribute rides a serialization (md() hands back HTML) …
  v.muted = true;                                    // … and the property holds where the element lives on
  v.setAttribute("playsinline", "");
  v.setAttribute("preload", "metadata");
  v.classList.add("md-video");
  for (const el of [v, ...Array.from(v.querySelectorAll("source"))]) {
    const a = el.getAttribute("src");
    if (a && !a.includes("#")) el.setAttribute("src", a + STILL_FRAGMENT);
  }
  sizeLikeClip(v, videoDims.get(url));
  const still = doc.createElement("span");
  still.className = "md-video-still" + (solo ? " md-solo" : "");
  still.dataset.video = url;
  if (v.parentNode) v.replaceWith(still);
  still.appendChild(v);
  return still;
}

// A picture or a video STANDS ALONE (2026-10-21, the review of the shared picture box: a picture in the middle of a
// sentence became a framed block that split the sentence into three lines) when nothing else shows on its LINE. So
// `![plot](url)` with `*Figure 1: …*` on the next line, one paragraph and the common agent shape, stands alone and takes
// the box with the caption under it (the review of #343: the solo rule read the caption as text beside it, and the
// picture went inline with the caption at its bottom edge); so does each of two pictures on lines of their own, and a
// picture under a lead-in line. Text on the picture's own line, before it or after it, keeps it inline: "Build ![ok](u)
// is green" and "![ok](u) means passing" are sentences it is a word of, and a block would split them. Only a picture that
// stands alone wears the box as a block (styles.css .md-solo); one inline with text stays in its line, capped and, when
// it zooms, with the zoom cursor. Read off the sanitized DOM at render (the post-pass), so it never waits on a load and
// never flips.
// Where a line ends (the review of #349, 2026-10-21, which found a README's side-by-side pictures stacked and a bold
// picture splitting its sentence):
//   - at a <br> (the user's markdown, breaks:true; an agent's hard break), and, in a container that holds blocks (the
//     message's root, a <div>, a list item, a cell), at a block;
//   - at a newline in a text node only where the author's line breaks there (newlineEndsLine): a soft break in a
//     paragraph, list item or heading marked wrote (assistant markdown, breaks:false, where the page shows it as a space
//     but the source put the next thing on a line of its own), and any newline in a <pre>. In the author's own HTML a
//     newline between tags is a space, so `<p>\n<img width="45%">\n<img width="45%">\n</p>` keeps its pictures side by side;
//   - never at an inline wrapper's edge: a bold, italic or linked picture is part of the line its wrapper sits on, so the
//     walk climbs out of the wrappers (`The **![x](u)** result` stays one sentence).
// When it stands alone and a <br> ends its line, that <br> (and no other) takes .md-solo-end, the one styles.css hides
// under a box, which ends the line itself.
const BLOCK_TAGS = /^(ADDRESS|ARTICLE|ASIDE|BLOCKQUOTE|CENTER|DD|DETAILS|DIALOG|DIR|DIV|DL|DT|FIELDSET|FIGCAPTION|FIGURE|FOOTER|FORM|H[1-6]|HEADER|HGROUP|HR|LI|MAIN|MENU|NAV|OL|P|PRE|SECTION|SUMMARY|TABLE|UL)$/;
const INLINE_WRAPS = /^(A|ABBR|B|BDI|BDO|CITE|CODE|DEL|DFN|EM|FONT|I|INS|KBD|MARK|PICTURE|Q|S|SAMP|SMALL|SPAN|STRIKE|STRONG|SUB|SUP|TIME|U|VAR)$/;
const SHOWS_NOTHING = /^(SOURCE|TRACK|WBR)$/;       // a <picture>'s sources, a video's tracks, a break opportunity
// Whether a newline in a text node of `box` ends a line. Marked writes the content of a paragraph, a list item and a
// heading flush against its tags, so a newline there is the markdown's own soft break. The author's HTML gives itself away
// by its padding: content opening on a line of its own after the tag (`<p>\n`, `<p align="center">\n  `), or closing on
// one after inline content (`…<img>\n</p>`; marked does end a list item on a newline, but after its nested list, a
// block). There, and in any other container (the root, a <div>, a cell), a newline is a space. A raw `<p><img>\n<img></p>`
// with no padding at all reads as markdown's own lines and stacks: the DOM cannot tell the two apart.
function newlineEndsLine(box: Element): boolean {
  const tag = box.tagName.toUpperCase();
  if (tag === "PRE") return true;                    // the page itself breaks there
  if (!/^(P|LI|H[1-6])$/.test(tag)) return false;
  const kids = box.childNodes;
  const pad = (n: ChildNode | undefined): boolean => !!n && n.nodeType === 3 && (n.textContent || "").includes("\n") && !(n.textContent || "").trim();
  const prev = kids[kids.length - 2];
  const afterBlock = !!prev && prev.nodeType === 1 && BLOCK_TAGS.test((prev as Element).tagName.toUpperCase());
  return !pad(kids[0]) && !(pad(kids[kids.length - 1]) && !afterBlock);
}
export function mdSolo(el: Element): boolean {
  let box = el.parentElement;
  if (!box) return false;
  while (INLINE_WRAPS.test(box.tagName.toUpperCase()) && box.parentElement) box = box.parentElement;   // the line's container
  const newlines = newlineEndsLine(box);
  const blocksEnd = box.tagName.toUpperCase() !== "P";
  // walk from el toward one end of its line, out through the wrappers it sits in: the <br> when one ends the line, true
  // at any other end with nothing shown on the way, false at anything shown
  const end = (step: 1 | -1): Element | boolean => {
    for (let at = el; ; at = at.parentElement as Element) {
      const p = at.parentElement as Element;
      const kids = Array.from(p.childNodes);
      for (let i = kids.indexOf(at as ChildNode) + step; i >= 0 && i < kids.length; i += step) {
        const n = kids[i];
        if (n.nodeType === 3) {
          const t = n.textContent || "";
          const nl = !newlines ? -1 : step > 0 ? t.indexOf("\n") : t.lastIndexOf("\n");
          if ((nl < 0 ? t : step > 0 ? t.slice(0, nl) : t.slice(nl + 1)).trim()) return false;   // words on its line
          if (nl >= 0) return true;                    // the soft break ends the line
          continue;
        }
        if (n.nodeType !== 1) continue;                // a comment shows nothing
        const tag = (n as Element).tagName.toUpperCase();
        if (SHOWS_NOTHING.test(tag)) continue;
        if (tag === "BR") return n as Element;         // a break ends it
        return blocksEnd && BLOCK_TAGS.test(tag);      // so does a block; anything else is on it
      }
      if (p === box) return true;                      // the container's edge
    }
  };
  const after = end(1);
  if (after === false || end(-1) === false) return false;
  if (after !== true) after.classList.add("md-solo-end");
  return true;
}

/** The address a message's <video> plays, resolved against the page: its src, else its first <source>'s (the one a
 *  browser tries first; md-images.ts names the same one in the arrows' sequence). Null when it names none. */
function mdVideoUrl(v: HTMLVideoElement): string | null {
  const own = (v.getAttribute("src") || "").trim();
  const a = own || ((v.querySelector("source[src]") as HTMLSourceElement | null)?.getAttribute("src") || "").trim();
  if (!a) return null;
  try { return new URL(a, document.baseURI).href; } catch { return null; }
}

/** The chat's post-pass over a message's videos (mdImgPostPass runs it): none ever starts by itself or with sound, and
 *  every one the lightbox may open (opensInLightbox's video rule: a web address on another site, or this kernel's file
 *  route) becomes the still. Any other keeps its own controls, so it still plays in the message: a data: clip (the
 *  sanitizer lets a <video> carry one, the lightbox never opens one), one inside a link or a fold's <summary> (whose click
 *  is theirs, as a picture's is), and every video in the VS Code webview, where no preview reaches the kernel. */
function mdVideoPostPass(root: ParentNode): void {
  for (const v of Array.from(root.querySelectorAll("video")) as HTMLVideoElement[]) {
    v.removeAttribute("autoplay");
    v.setAttribute("muted", "");
    v.setAttribute("playsinline", "");
    // already a still (a pass that ran twice): its wrapper carries the address, a data- attribute no author can write (the
    // sanitizer drops every data-*), so a span an author dressed in the class is no reason to skip
    if ((v.parentElement as HTMLElement | null)?.dataset?.video) continue;
    const url = mdVideoUrl(v);
    if (!url || v.closest("a, summary") || !canPreview() || !opensInLightbox(url, "video")) { v.setAttribute("controls", ""); continue; }
    makeVideoStill(v, url, mdSolo(v));
  }
}

/** A LINK to a video file in a message (isVideoUrl: a web address whose path ends in .mp4, .webm or .mov) renders the still
 *  after its block, as a mentioned path's figure does (render.ts linkifyFileUris hands it the link; md-images.ts names it
 *  in the arrows' sequence at the same place). Null where the lightbox would not open it, or with no kernel to reach. */
export function previewVideoUrl(url: string): HTMLElement | null {
  if (!canPreview() || !opensInLightbox(url, "video")) return null;
  const box = document.createElement("span");
  box.className = "path-full";
  box.title = url;
  const v = document.createElement("video");
  v.setAttribute("src", url);
  const still = makeVideoStill(v, url, true);
  still.onclick = (ev) => { ev.stopPropagation(); openLightbox({ url, video: true }, v); };
  box.appendChild(still);
  return box;
}

/** Stop a video for good: paused, its source dropped and the load reset, so neither its sound nor its download outlives
 *  the view that showed it (the lightbox's close and step, a reopen). */
export function stopVideo(v: HTMLVideoElement): void {
  try { v.pause(); } catch { /* a detached element */ }
  v.removeAttribute("src");
  try { v.load(); } catch { /* nothing to reset */ }
}
