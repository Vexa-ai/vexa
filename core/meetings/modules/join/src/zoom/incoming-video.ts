import { Page } from "playwright";
import { log } from "../_host";
import {
  zoomMoreMeetingControlSelector,
  zoomStopIncomingVideoSelector,
  zoomStartIncomingVideoSelector,
} from "./selectors";

/**
 * Stop the bot receiving participants' video in the Zoom web client.
 *
 * The bot only needs the call's audio and the active-speaker tile. Every camera that is on costs
 * the bot a software video decode (no GPU in the container), and that CPU is taken from the audio
 * capture when the pod sits at its limit. Zoom web offers "Stop Incoming Video" in the footer's
 * More menu: the server stops sending video, the tiles fall back to name avatars, and the
 * active-speaker containers and their name footers stay in the DOM, so speaker attribution reads
 * them unchanged.
 */

export interface IncomingVideoSelectors {
  more: string;
  stop: string;
  start: string;
}

export const ZOOM_INCOMING_VIDEO_SELECTORS: IncomingVideoSelectors = {
  more: zoomMoreMeetingControlSelector,
  stop: zoomStopIncomingVideoSelector,
  start: zoomStartIncomingVideoSelector,
};

/**
 * What one in-page step saw and did:
 *  - `stopped`     the "Stop Incoming Video" item was present and was clicked;
 *  - `off`         the item reads "Start Incoming Video": incoming video is already off;
 *  - `opened`      neither item was present, so the More menu was clicked open;
 *  - `unavailable` there is no More button (the meeting UI is not up yet, or this client lacks it).
 */
export type IncomingVideoStep = "stopped" | "off" | "opened" | "unavailable";

/** One step, run inside the page (serialized by page.evaluate). Plain DOM only. */
export function incomingVideoStep(sel: IncomingVideoSelectors): IncomingVideoStep {
  const stop = document.querySelector(sel.stop) as HTMLElement | null;
  if (stop) {
    stop.click();
    return "stopped";
  }
  if (document.querySelector(sel.start)) return "off";
  const more = document.querySelector(sel.more) as HTMLElement | null;
  if (!more) return "unavailable";
  more.click();
  return "opened";
}

export type IncomingVideoOutcome = "off" | "unavailable";

export interface StopIncomingVideoOptions {
  /** Tries before giving up. Default 24 (two minutes at the default interval). */
  attempts?: number;
  /** Wait between tries while the menu or the item is not there yet. Default 5000 ms. */
  intervalMs?: number;
  /** Wait for the More menu to render after it is clicked open. Default 800 ms. */
  menuSettleMs?: number;
}

/**
 * Turn incoming video off and confirm it. Resolves `off` once the menu item reads "Start Incoming
 * Video", or `unavailable` when the control never appears within the attempts. Never throws: the
 * call goes on either way, only at a higher CPU cost.
 */
export async function stopZoomIncomingVideo(
  page: Page,
  opts: StopIncomingVideoOptions = {},
): Promise<IncomingVideoOutcome> {
  const attempts = opts.attempts ?? 24;
  const intervalMs = opts.intervalMs ?? 5000;
  const menuSettleMs = opts.menuSettleMs ?? 800;
  const step = (): Promise<IncomingVideoStep> =>
    page.evaluate(incomingVideoStep, ZOOM_INCOMING_VIDEO_SELECTORS).catch(() => "unavailable" as const);
  const closeMenu = () => page.keyboard.press("Escape").catch(() => { /* menu already closed */ });

  let clicked = false;
  for (let i = 0; i < attempts; i++) {
    if (page.isClosed()) return "unavailable";
    let s = await step();
    if (s === "opened") {
      await page.waitForTimeout(menuSettleMs).catch(() => {});
      s = await step();
    }
    if (s === "off") {
      await closeMenu();
      log(clicked
        ? "[Zoom Web] Incoming video is off (Stop Incoming Video confirmed)"
        : "[Zoom Web] Incoming video was already off");
      return "off";
    }
    if (s === "stopped") {
      clicked = true;
      log("[Zoom Web] Clicked Stop Incoming Video — confirming");
      await page.waitForTimeout(menuSettleMs).catch(() => {});
      continue;
    }
    if (s === "opened") await closeMenu();
    await page.waitForTimeout(intervalMs).catch(() => {});
  }
  log("[Zoom Web] Stop Incoming Video control not found — incoming video stays on");
  return "unavailable";
}
