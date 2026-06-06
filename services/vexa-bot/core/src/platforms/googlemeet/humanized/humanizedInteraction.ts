// Clean-room humanized interaction orchestrator (Apache-2.0).
//
// Ties the mocap engine to real XTEST input against a Playwright page:
//   1. Resolve a target element's rect in absolute device pixels.
//   2. Pick a recorded trajectory that lands the pointer inside that rect,
//      verifying with document.elementFromPoint (retry / stretch fallback).
//   3. Replay the trajectory's relative moves with their recorded timing, then
//      press/release with recorded click timing — all via the X server.
//   4. For text entry, click the field then paste (clipboard) or human-type.
//
// Independent implementation of the publicly described approach; no third-party
// code or recorded data.

import type { Page, ElementHandle } from "playwright";
import { MocapEngine, type Rect } from "./mocapEngine";
import { X11Input, type PointerLocation } from "./x11Input";
import type { MocapLibrary } from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface HumanizedOptions {
  display?: string;
  dryRun?: boolean;
  log?: (msg: string) => void;
  /**
   * Optional sink for a diagnostic screenshot when a click is abandoned as a
   * confirmed miss (no-fallbacks.md: fail LOUD with a logged reason + image).
   */
  onMissScreenshot?: (page: Page, reason: string) => Promise<void>;
}

interface PageMetrics {
  left: number;
  top: number;
  width: number;
  height: number;
  screenX: number;
  screenY: number;
  dpr: number;
}

export class HumanizedInteractor {
  private engine: MocapEngine;
  private x11: X11Input;
  private log: (msg: string) => void;
  private offsetX = 0; // device-px from page-client origin to X screen origin
  private offsetY = 0;
  private dpr = 1;
  private calibrated = false;
  private mocapMisses = 0;
  private onMissScreenshot?: (page: Page, reason: string) => Promise<void>;
  /** Slack (device px) allowed between the real pointer and the live button rect. */
  private static readonly ENDPOINT_SLACK_PX = 2;

  constructor(library: MocapLibrary, opts: HumanizedOptions = {}) {
    this.engine = new MocapEngine(library);
    this.x11 = new X11Input({ display: opts.display, dryRun: opts.dryRun });
    this.log = opts.log ?? (() => {});
    this.onMissScreenshot = opts.onMissScreenshot;
  }

  async available(): Promise<boolean> {
    return this.x11.isAvailable();
  }

  /**
   * Derive the linear mapping between page client coords (CSS px) and X screen
   * coords (device px) by moving the real pointer to two known screen points
   * and reading the resulting mousemove events. Falls back to the
   * window.screenX/Y + devicePixelRatio formula if events aren't observed.
   */
  async calibrate(page: Page, force = false): Promise<void> {
    if (this.calibrated && !force) return;
    // DPR / screenX can change between page load and lobby render (zoom,
    // window move, OS scaling). Always re-read on a (re)calibrate so the
    // offset reflects the geometry at click time, not at page-load time.
    this.dpr = await page.evaluate(() => window.devicePixelRatio || 1);

    await page.evaluate(() => {
      (window as any).__vexaLastMouse = null;
      window.addEventListener(
        "mousemove",
        (e) => {
          (window as any).__vexaLastMouse = { clientX: e.clientX, clientY: e.clientY };
        },
        { capture: true }
      );
    });

    const geo = await page.evaluate(() => ({
      sx: window.screenX,
      sy: window.screenY,
      iw: window.innerWidth,
      ih: window.innerHeight,
    }));

    // Two probe points well inside the viewport (device px).
    const probes = [
      { x: Math.round((geo.sx + geo.iw * 0.35) * this.dpr), y: Math.round((geo.sy + geo.ih * 0.4) * this.dpr) },
      { x: Math.round((geo.sx + geo.iw * 0.6) * this.dpr), y: Math.round((geo.sy + geo.ih * 0.6) * this.dpr) },
    ];

    const samples: { sx: number; sy: number; cx: number; cy: number }[] = [];
    for (const p of probes) {
      await this.x11.moveAbs(p.x, p.y);
      await sleep(120);
      const ev = await page.evaluate(() => (window as any).__vexaLastMouse);
      if (ev) samples.push({ sx: p.x, sy: p.y, cx: ev.clientX, cy: ev.clientY });
    }

    if (samples.length >= 1) {
      // offset = screen_px - client_css * dpr  (consistent across samples)
      const s = samples[0];
      this.offsetX = s.sx - s.cx * this.dpr;
      this.offsetY = s.sy - s.cy * this.dpr;
      this.log(`humanized: calibrated offset=(${this.offsetX.toFixed(0)},${this.offsetY.toFixed(0)}) dpr=${this.dpr}`);
    } else {
      // Fallback to the documented screenX/Y formula.
      this.offsetX = geo.sx * this.dpr;
      this.offsetY = geo.sy * this.dpr;
      this.log(`humanized: calibration fell back to screenX/Y formula`);
    }
    this.calibrated = true;
  }

  private rectDevicePx(m: PageMetrics): Rect {
    const inset = 0.18; // aim for the central 64% of the element
    const ix = m.width * inset;
    const iy = m.height * inset;
    return {
      left: Math.round(this.offsetX + (m.left + ix) * this.dpr),
      top: Math.round(this.offsetY + (m.top + iy) * this.dpr),
      right: Math.round(this.offsetX + (m.left + m.width - ix) * this.dpr),
      bottom: Math.round(this.offsetY + (m.top + m.height - iy) * this.dpr),
    };
  }

  private async metricsOf(page: Page, handle: ElementHandle<Element>): Promise<PageMetrics> {
    return page.evaluate((el) => {
      const r = (el as Element).getBoundingClientRect();
      return {
        left: r.left,
        top: r.top,
        width: r.width,
        height: r.height,
        screenX: window.screenX,
        screenY: window.screenY,
        dpr: window.devicePixelRatio || 1,
      };
    }, handle);
  }

  /** Map an absolute X-screen point (device px) into page client coords (CSS px). */
  private screenToPage(sx: number, sy: number): { x: number; y: number } {
    return { x: (sx - this.offsetX) / this.dpr, y: (sy - this.offsetY) / this.dpr };
  }

  /** Map a page client point (CSS px) to absolute X-screen coords (device px). */
  private pageToScreen(px: number, py: number): { x: number; y: number } {
    return { x: this.offsetX + px * this.dpr, y: this.offsetY + py * this.dpr };
  }

  /**
   * Endpoint verification (the fix for the silent off-target click).
   *
   * Reads the REAL hardware pointer from the X server and re-reads the target's
   * LIVE bounding rect, then asserts the pointer is inside that rect AND that
   * elementFromPoint at the mapped page coords resolves to the target. Crucially
   * this uses the actual pointer position (not the predicted endpoint) so a wrong
   * screen↔page offset is detected instead of papered over by a self-consistent
   * page-space round-trip.
   */
  private async pointerHitsTarget(
    page: Page,
    handle: ElementHandle<Element>
  ): Promise<{ ok: boolean; m: PageMetrics; pointer: PointerLocation; pageX: number; pageY: number }> {
    const m = await this.metricsOf(page, handle);
    const pointer = await this.x11.getPointer();
    const { x: pageX, y: pageY } = this.screenToPage(pointer.x, pointer.y);
    const S = HumanizedInteractor.ENDPOINT_SLACK_PX;
    const insideRect =
      pageX >= m.left - S &&
      pageX <= m.left + m.width + S &&
      pageY >= m.top - S &&
      pageY <= m.top + m.height + S;
    if (!insideRect) {
      return { ok: false, m, pointer, pageX, pageY };
    }
    const onTarget = await page.evaluate(
      ([px, py, el]) => {
        const hit = document.elementFromPoint(px as number, py as number);
        return !!hit && (hit === el || (el as Element).contains(hit as Node) || (hit as Element).contains(el as Node));
      },
      [pageX, pageY, handle] as const
    );
    return { ok: onTarget, m, pointer, pageX, pageY };
  }

  /**
   * Replay a human trajectory toward the target's live rect. Returns the rect
   * (device px) the trajectory aimed at so the caller can re-verify.
   */
  private async replayTowards(page: Page, handle: ElementHandle<Element>): Promise<void> {
    // Let layout settle before reading the rect: a rect read before the lobby
    // finishes painting was a primary miss source (button shifts after read).
    await page.waitForTimeout(120);
    const m = await this.metricsOf(page, handle);
    if (m.width <= 0 || m.height <= 0) throw new Error("humanized: element has zero size");
    const rect = this.rectDevicePx(m);

    const cur = await this.x11.getPointer();
    let seq = this.engine.findSequenceLandingInRect(cur.x, cur.y, rect);
    if (!seq) {
      this.mocapMisses++;
      this.log(`humanized: no direct sequence (miss #${this.mocapMisses}); trying stretch+rotate`);
      seq = this.engine.findSequenceWithStretchAndRotation(cur.x, cur.y, rect);
    }
    if (!seq) throw new Error("humanized: no mocap sequence lands on target element");

    this.log(`humanized: replay ${seq.movements.length} moves dx=${seq.total_dx} dy=${seq.total_dy}`);
    for (const mv of seq.movements) {
      if (mv.dt > 0) await sleep(mv.dt * 1000);
      if (mv.dx !== 0 || mv.dy !== 0) await this.x11.moveRel(mv.dx, mv.dy);
    }
  }

  /** Move the pointer to the element along a human trajectory and click it. */
  async navigateAndClick(page: Page, handle: ElementHandle<Element>): Promise<void> {
    await this.calibrate(page);
    await this.replayTowards(page, handle);

    // ── Endpoint verification (PRIMARY fix) ──────────────────────────────
    // Re-read the button rect and the REAL pointer immediately before the
    // click; assert the pointer is inside the live button. On a miss, treat the
    // pointer↔mouseevent geometry as stale: recalibrate, nudge the pointer to
    // the live rect center via an absolute move, and re-verify. A click is only
    // emitted once the pointer is confirmed inside the target — a miss is caught,
    // not silently lost.
    const MAX_CORRECTIONS = 4;
    let hit = await this.pointerHitsTarget(page, handle);
    for (let i = 0; !hit.ok && i < MAX_CORRECTIONS; i++) {
      this.log(
        `humanized: endpoint miss #${i + 1} — pointer page=(${hit.pageX.toFixed(0)},${hit.pageY.toFixed(0)}) ` +
        `rect=[${hit.m.left.toFixed(0)},${hit.m.top.toFixed(0)} ${hit.m.width.toFixed(0)}x${hit.m.height.toFixed(0)}]; recalibrating`
      );
      await this.calibrate(page, /* force */ true);
      // Aim at the live rect center, mapped through the fresh offset.
      const cx = hit.m.left + hit.m.width / 2;
      const cy = hit.m.top + hit.m.height / 2;
      const target = this.pageToScreen(cx, cy);
      await this.x11.moveAbs(Math.round(target.x), Math.round(target.y));
      await sleep(60);
      hit = await this.pointerHitsTarget(page, handle);
    }

    if (!hit.ok) {
      const reason =
        `humanized: click target verification FAILED after ${MAX_CORRECTIONS} corrections — ` +
        `real pointer page=(${hit.pageX.toFixed(0)},${hit.pageY.toFixed(0)}) is not inside the join control ` +
        `rect=[${hit.m.left.toFixed(0)},${hit.m.top.toFixed(0)} ${hit.m.width.toFixed(0)}x${hit.m.height.toFixed(0)}] ` +
        `(offset=(${this.offsetX.toFixed(0)},${this.offsetY.toFixed(0)}) dpr=${this.dpr}). Refusing to click off-target.`;
      this.log(reason);
      if (this.onMissScreenshot) {
        try { await this.onMissScreenshot(page, reason); } catch { /* screenshot best-effort */ }
      }
      throw new Error(reason);
    }

    this.log(`humanized: endpoint verified inside target page=(${hit.pageX.toFixed(0)},${hit.pageY.toFixed(0)}); clicking`);
    const downDt = 0.06 + Math.random() * 0.05;
    const upDt = 0.05 + Math.random() * 0.05;
    await sleep(downDt * 1000);
    await this.x11.buttonDown(1);
    await sleep(upDt * 1000);
    await this.x11.buttonUp(1);
  }

  /**
   * Click a text field then enter text via real XTEST keystrokes (xdotool type),
   * with a per-character delay so input looks human. We deliberately avoid the
   * clipboard path: `xclip` holds the X selection and does not exit, which hangs
   * the child process.
   */
  async fillField(page: Page, handle: ElementHandle<Element>, text: string): Promise<void> {
    await this.navigateAndClick(page, handle);
    await sleep(120 + Math.floor(Math.random() * 180));
    await this.x11.typeText(text, 55 + Math.floor(Math.random() * 50));
  }
}
