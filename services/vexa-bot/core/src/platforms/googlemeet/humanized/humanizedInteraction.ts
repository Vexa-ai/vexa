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
import { X11Input } from "./x11Input";
import type { MocapLibrary } from "./types";

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export interface HumanizedOptions {
  display?: string;
  dryRun?: boolean;
  log?: (msg: string) => void;
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

  constructor(library: MocapLibrary, opts: HumanizedOptions = {}) {
    this.engine = new MocapEngine(library);
    this.x11 = new X11Input({ display: opts.display, dryRun: opts.dryRun });
    this.log = opts.log ?? (() => {});
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
  async calibrate(page: Page): Promise<void> {
    if (this.calibrated) return;
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

  /** Move the pointer to the element along a human trajectory and click it. */
  async navigateAndClick(page: Page, handle: ElementHandle<Element>): Promise<void> {
    await this.calibrate(page);
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

    // Verify endpoint actually resolves to the element; if not, attempt a few
    // other sequences before giving up (mirrors the documented retry).
    for (let attempt = 0; attempt < 8; attempt++) {
      const endScreenX = cur.x + seq.total_dx;
      const endScreenY = cur.y + seq.total_dy;
      const pageX = (endScreenX - this.offsetX) / this.dpr;
      const pageY = (endScreenY - this.offsetY) / this.dpr;
      const onTarget = await page.evaluate(
        ([px, py, el]) => {
          const hit = document.elementFromPoint(px as number, py as number);
          return !!hit && (hit === el || (el as Element).contains(hit as Node));
        },
        [pageX, pageY, handle] as const
      );
      if (onTarget) break;
      const next = this.engine.findSequenceLandingInRect(cur.x, cur.y, rect);
      if (!next) break;
      seq = next;
    }

    this.log(`humanized: replay ${seq.movements.length} moves dx=${seq.total_dx} dy=${seq.total_dy}`);
    for (const mv of seq.movements) {
      if (mv.dt > 0) await sleep(mv.dt * 1000);
      if (mv.dx !== 0 || mv.dy !== 0) await this.x11.moveRel(mv.dx, mv.dy);
    }
    if (seq.click_down_dt > 0) await sleep(seq.click_down_dt * 1000);
    await this.x11.buttonDown(1);
    if (seq.click_up_dt > 0) await sleep(seq.click_up_dt * 1000);
    await this.x11.buttonUp(1);
  }

  /** Click a text field then enter text via clipboard paste (most human-safe). */
  async fillField(page: Page, handle: ElementHandle<Element>, text: string): Promise<void> {
    await this.navigateAndClick(page, handle);
    await sleep(120 + Math.floor(Math.random() * 180));
    await this.x11.clipboardPaste(text);
  }
}
