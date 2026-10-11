#!/usr/bin/env node
/**
 * layout-sweep — the terminal's L1–L3 layout proof (terminal design guidelines §8), run in a real
 * browser at each width in both themes.
 *
 *   node scripts/layout-sweep.mjs <url> [--widths 360,390,412,640,…,1920] [--json out.json] [--mobile]
 *
 * `--mobile` adds Playwright's phone devices (Pixel 7, iPhone 14, both orientations) with touch and a
 * mobile user agent — the 2026-10-10 phone defect (the whole shell scrolled 145px sideways) only
 * showed there.
 *
 * <url> is a page that renders the Minutes shell: the fixture shell at `/design#shell` (no sign-in,
 * fixtures only), or a signed-in page whose browser context the caller provides. It needs
 * Playwright (Apache-2.0), which is NOT a dependency of the terminal: run it where Playwright is
 * installed (the bbb sweep runner), e.g. `NODE_PATH=<dir>/node_modules node scripts/layout-sweep.mjs …`.
 *
 * What it asserts, per width × theme (the same function, `MEASURE`, can be pasted into a browser
 * console on any page to read the numbers by hand):
 *   L1  no element that scrolls sideways — `overflow-x: auto|scroll` with scrollWidth > clientWidth —
 *       except code blocks, tables and the tab strip (which has its own overflow menu); and the
 *       document itself never wider than the viewport.
 *   L2  every visible control is at least 24×24 (reported; WCAG 2.5.8 allows a spacing exception).
 *   L3  the conversation is at least the mode's floor (560 / 480 / 440, full width below 960), and
 *       the composer toolbar is ONE row.
 *   tabs  a tab strip that hides a tab shows its overflow control.
 *   L5  no text stacked one letter per line (a collapsed column) — part of L1's verdict.
 * Exit 1 when L1, L3 or tabs fail anywhere.
 */
import { writeFileSync } from "node:fs";

/** Runs IN THE PAGE. Self-contained: no closure over this module. */
export const MEASURE = () => {
  const vw = window.innerWidth;
  const mode = vw >= 1440 ? "wide" : vw >= 1200 ? "desktop" : vw >= 960 ? "compact" : vw >= 720 ? "narrow" : "single";
  const floor = { wide: 560, desktop: 480, compact: 440, narrow: 0, single: 0 }[mode];
  const visible = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el); return r.width > 0 && r.height > 0 && cs.visibility !== "hidden" && !el.closest("[inert]"); };
  const describe = (el) => {
    const id = el.getAttribute("data-pane") || el.getAttribute("data-composer-toolbar") !== null && "composer-toolbar" || "";
    return `${el.tagName.toLowerCase()}${id ? `[${id}]` : ""}${el.className && typeof el.className === "string" ? "." + el.className.split(" ").slice(0, 2).join(".") : ""}`;
  };
  // L1
  const sideways = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!visible(el)) continue;
    const ox = getComputedStyle(el).overflowX;
    if (ox !== "auto" && ox !== "scroll") continue;
    if (el.scrollWidth <= el.clientWidth + 1) continue;
    if (el.closest("pre, code, table, .vx-strip-scroll, [data-allow-hscroll]")) continue;
    sideways.push(`${describe(el)} ${el.scrollWidth}>${el.clientWidth}`);
  }
  const docWide = document.documentElement.scrollWidth > vw + 1;
  // L5 — TEXT STACKED ONE LETTER PER LINE (founder, 2026-10-10: a metadata label collapsed to a
  // sliver). A visible element holding ≥ 4 characters of its own text, narrower than two
  // characters and taller than four lines, is a collapsed column, whatever caused it.
  const stacked = [];
  for (const el of document.querySelectorAll("[data-pane] *")) {
    if (!visible(el) || el.closest("svg")) continue;
    const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("");
    if (own.length < 4) continue;
    const cs = getComputedStyle(el); const r = el.getBoundingClientRect();
    const fs = parseFloat(cs.fontSize) || 12; const lh = parseFloat(cs.lineHeight) || fs * 1.4;
    if (r.width < fs * 2 && r.height > lh * 4) stacked.push(`${describe(el)} ${Math.round(r.width)}×${Math.round(r.height)} "${own.slice(0, 20)}"`);
  }
  // L2
  const small = [];
  for (const el of document.querySelectorAll("button, [role=button], a[href], [role=menuitem], [role=tab], [role=separator]")) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (el.getAttribute("role") === "separator") { if (r.height < 24) small.push(describe(el)); continue; }
    if (r.width < 24 || r.height < 24) small.push(`${describe(el)} ${Math.round(r.width)}×${Math.round(r.height)} "${(el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 24)}"`);
  }
  // L3
  const conv = document.querySelector("[data-pane=conversation]");
  const chat = conv ? Math.round(conv.getBoundingClientRect().width) : null;
  const toolbar = document.querySelector("[data-composer-toolbar]");
  let toolbarRows = null;
  if (toolbar && visible(toolbar)) {
    const tops = new Set();
    for (const c of toolbar.querySelectorAll(":scope > *, :scope .vx-fold-wide > *, :scope .vx-fold-narrow > *")) {
      if (!visible(c)) continue;
      const r = c.getBoundingClientRect();
      tops.add(Math.round((r.top + r.bottom) / 2 / 8));   // centre line, in 8px buckets
    }
    toolbarRows = tops.size;
  }
  const textarea = document.querySelector("[data-pane=conversation] textarea");
  // tabs
  const strips = [...document.querySelectorAll(".vx-strip")].filter(visible).map((s) => {
    const sc = s.querySelector(".vx-strip-scroll");
    const hides = sc && sc.scrollWidth > sc.clientWidth + 1;
    return { hides, control: !!s.querySelector("[data-strip-more]") };
  });
  // The shell must never be scrolled: in single and narrow modes the conversation starts at x=0.
  const shell = document.querySelector("[data-shell-mode]");
  const shellScrolled = shell ? shell.scrollLeft !== 0 : false;
  const convLeft = conv ? Math.round(conv.getBoundingClientRect().left) : null;
  const convOffscreen = conv ? (conv.getBoundingClientRect().left < -1 || conv.getBoundingClientRect().right > vw + 1) : false;
  const cols = (() => { const sh = document.querySelector("[data-shell-mode]"); return sh ? getComputedStyle(sh).gridTemplateColumns : null; })();
  return {
    vw, mode, floor, cols,
    shellMode: document.querySelector("[data-shell-mode]")?.getAttribute("data-shell-mode") ?? null,
    chat, textarea: textarea ? Math.round(textarea.getBoundingClientRect().width) : null,
    convLeft, shellScrolled,
    L1: { ok: sideways.length === 0 && !docWide && !shellScrolled && !convOffscreen && stacked.length === 0, sideways, docWide, shellScrolled, convOffscreen, stacked },
    L2: { small: small.length, examples: small.slice(0, 8) },
    L3: { ok: (chat === null || chat >= floor) && (toolbarRows === null || toolbarRows <= 1), toolbarRows },
    tabs: { ok: strips.every((s) => !s.hides || s.control), strips },
  };
};

async function main() {
  const args = process.argv.slice(2);
  const url = args.find((a) => !a.startsWith("--"));
  if (!url) { console.error("usage: layout-sweep.mjs <url> [--widths a,b,c] [--json out.json]"); process.exit(2); }
  const arg = (k) => { const i = args.indexOf(k); return i >= 0 ? args[i + 1] : undefined; };
  const widths = (arg("--widths") ?? "360,390,412,640,720,820,960,1024,1200,1440,1920").split(",").map(Number);
  let chromium;
  try { ({ chromium } = await import("playwright")); }
  catch { console.error("Playwright is not installed here. Run this where it is (set NODE_PATH), see the header."); process.exit(2); }
  const browser = await chromium.launch();
  const results = [];
  let failed = false;
  // A FRESH LOAD PER WIDTH. Resizing one page is the realistic path, but a headless browser on a
  // loaded host may render no frames at all (requestAnimationFrame never fires — seen on the bbb
  // runner), and then neither resize events nor ResizeObserver are delivered: the page keeps the
  // layout of its first width. Loading at each width measures what that width renders; resizing a
  // live page is covered by the unit sweep and by checking app.dev in a real browser.
  for (const theme of ["dark", "light"]) {
    const ctx = await browser.newContext({ viewport: { width: widths[0], height: 900 }, colorScheme: theme });
    await ctx.addInitScript((t) => { try { localStorage.setItem("vexa.terminal.theme", t); } catch { /* */ } }, theme);
    for (const w of widths) {
      const page = await ctx.newPage();
      await page.setViewportSize({ width: w, height: 900 });
      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(400);
      const r = await page.evaluate(MEASURE);
      r.theme = theme;
      results.push(r);
      const bad = !r.L1.ok || !r.L3.ok || !r.tabs.ok;
      failed ||= bad;
      console.log(`${bad ? "✗" : "✓"} ${theme.padEnd(5)} ${String(w).padStart(4)}  mode=${r.shellMode ?? r.mode}  cols=${r.cols}  chat=${r.chat}  textarea=${r.textarea}  toolbarRows=${r.L3.toolbarRows}  L1=${r.L1.ok ? "ok" : JSON.stringify(r.L1)}  L2 small=${r.L2.small}  tabs=${r.tabs.ok ? "ok" : "hidden without control"}`);
      await page.close();
    }
    await ctx.close();
  }
  if (args.includes("--mobile")) {
    const { devices } = await import("playwright");
    for (const name of ["Pixel 7", "Pixel 7 landscape", "iPhone 14", "iPhone 14 landscape"]) {
      const d = devices[name];
      if (!d) continue;
      const ctx = await browser.newContext({ ...d });
      const page = await ctx.newPage();
      await page.goto(url, { waitUntil: "networkidle" });
      await page.waitForTimeout(500);
      const r = await page.evaluate(MEASURE);
      r.device = name;
      results.push(r);
      const bad = !r.L1.ok || !r.L3.ok || !r.tabs.ok;
      failed ||= bad;
      console.log(`${bad ? "✗" : "✓"} ${name.padEnd(20)} ${r.vw}px mode=${r.shellMode ?? r.mode} chat=${r.chat} convLeft=${r.convLeft} shellScrolled=${r.shellScrolled} L1=${r.L1.ok ? "ok" : JSON.stringify(r.L1)}`);
      await ctx.close();
    }
  }
  await browser.close();
  const out = arg("--json");
  if (out) writeFileSync(out, JSON.stringify(results, null, 2));
  process.exit(failed ? 1 : 0);
}

if (import.meta.url === `file://${process.argv[1]}`) main();
