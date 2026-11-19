/** BACKGROUND BLUR IN A CALL (founder 2026-10-15): one control — on your own tile, in the call bar's
 *  "…" and before joining — cycling off / light / strong. It is the person's setting, kept in this
 *  browser and applied whenever their camera starts.
 *
 *  The work is `@livekit/track-processors` (MediaPipe selfie segmentation in WebAssembly) on the
 *  local camera track. Its runtime and model are served by this app at /mediapipe/ — never fetched
 *  from a CDN (scripts/copy-mediapipe.mjs).
 *
 *  It never freezes the video: a browser that cannot run it, a model that does not load, or a device
 *  that cannot keep up (frames slower than FRAME_BUDGET_MS for SLOW_FOR_MS, or no frame at all) turns
 *  it off, with one short notice, and the camera goes on unprocessed. */
import { useSyncExternalStore } from "react";

export type BlurLevel = "off" | "light" | "strong";
export const BLUR_LEVELS: BlurLevel[] = ["off", "light", "strong"];
export const BLUR_RADIUS: Record<Exclude<BlurLevel, "off">, number> = { light: 6, strong: 16 };
export const BLUR_WORD: Record<BlurLevel, string> = { off: "Off", light: "Light", strong: "Strong" };
export const BLUR_ASSETS = { tasksVisionFileSet: "/mediapipe/wasm", modelAssetPath: "/mediapipe/selfie_segmenter.tflite" };
export const FRAME_BUDGET_MS = 60;
export const SLOW_FOR_MS = 3000;
const KEY = "vexa.call.blur";

export const nextBlur = (l: BlurLevel): BlurLevel => BLUR_LEVELS[(BLUR_LEVELS.indexOf(l) + 1) % BLUR_LEVELS.length];

export function readBlur(): BlurLevel {
  try { const v = localStorage.getItem(KEY); return v === "light" || v === "strong" ? v : "off"; } catch { return "off"; }
}

let state = { level: readBlurSafe(), notice: "" };
function readBlurSafe(): BlurLevel { return typeof window === "undefined" ? "off" : readBlur(); }
const subs = new Set<() => void>();
const emit = (next: Partial<typeof state>) => { state = { ...state, ...next }; subs.forEach((f) => f()); };
export function useBlur(): { level: BlurLevel; notice: string } {
  return useSyncExternalStore((f) => { subs.add(f); return () => subs.delete(f); }, () => state, () => state);
}
export function blurState() { return state; }

/** The person's choice: kept, and applied by whoever holds the camera (`applyBlur`). */
export function setBlur(level: BlurLevel): void {
  try { localStorage.setItem(KEY, level); } catch { /* private window: this call only */ }
  emit({ level, notice: "" });
}
export function cycleBlur(): BlurLevel { const n = nextBlur(state.level); setBlur(n); return n; }
export function clearBlurNotice(): void { emit({ notice: "" }); }
/** Test seam: a notice as the processor would raise it. */
export function showBlurNoticeForTest(notice: string): void { emit({ notice }); }

/** The slice of a LiveKit LocalVideoTrack this needs. */
export interface ProcessableTrack {
  setProcessor(p: unknown): Promise<void>;
  stopProcessor(): Promise<void>;
  getProcessor?(): unknown;
}
export interface FrameStats { processingTimeMs: number }
export interface BlurDeps {
  supported(): boolean;
  /** a blur processor of this radius; it reports every processed frame */
  make(radius: number, onFrame: (s: FrameStats) => void): { switchTo?(o: unknown): Promise<void> };
  now?(): number;
  setInterval?(fn: () => void, ms: number): unknown;
  clearInterval?(t: unknown): void;
}

/** The live dependencies: @livekit/track-processors, loaded only when blur is asked for. */
export async function liveBlurDeps(): Promise<BlurDeps> {
  const tp = await import("@livekit/track-processors");
  return {
    supported: () => tp.supportsBackgroundProcessors(),
    make: (radius, onFrame) => tp.BackgroundProcessor({ mode: "background-blur", blurRadius: radius, assetPaths: BLUR_ASSETS,
      onFrameProcessed: onFrame }),
  };
}

let watch: { stop: () => void } | null = null;
let applied: { track: ProcessableTrack; level: BlurLevel } | null = null;

async function off(track: ProcessableTrack, notice = ""): Promise<void> {
  watch?.stop(); watch = null;
  applied = null;
  try { await track.stopProcessor(); } catch { /* already unprocessed */ }
  if (notice) emit({ notice });
}

/** Put the camera track in the chosen state. Safe to call on every change; never throws. */
export async function applyBlur(track: ProcessableTrack | null | undefined, level: BlurLevel, deps?: BlurDeps): Promise<void> {
  if (!track) return;
  if (level === "off") { if (applied?.track === track || track.getProcessor?.()) await off(track); return; }
  if (applied && applied.track === track && applied.level === level) return;
  const d = deps ?? await liveBlurDeps().catch(() => null);
  if (!d || !d.supported()) {
    emit({ level: "off", notice: "Background blur isn't supported in this browser." });
    return;
  }
  const now = d.now ?? (() => Date.now());
  let lastFrame = now();
  let slowSince = 0;
  const onFrame = (s: FrameStats) => {
    const t = now();
    lastFrame = t;
    if (s.processingTimeMs > FRAME_BUDGET_MS) { if (!slowSince) slowSince = t; }
    else slowSince = 0;
  };
  try {
    watch?.stop(); watch = null;
    await track.setProcessor(d.make(BLUR_RADIUS[level], onFrame));
    applied = { track, level };
  } catch {
    await off(track, "Background blur couldn't start here — your camera is on without it.");
    emit({ level: "off" });
    return;
  }
  // THE WATCHDOG: sustained slow frames, or none at all, turn blur off — the video never freezes.
  const setI = d.setInterval ?? ((fn: () => void, ms: number) => setInterval(fn, ms));
  const clearI = d.clearInterval ?? ((t: unknown) => clearInterval(t as ReturnType<typeof setInterval>));
  lastFrame = now();
  const timer = setI(() => {
    const t = now();
    if ((slowSince && t - slowSince >= SLOW_FOR_MS) || t - lastFrame >= SLOW_FOR_MS) {
      void off(track, "Background blur turned off — this device couldn't keep up.").then(() => emit({ level: "off" }));
    }
  }, 500);
  watch = { stop: () => clearI(timer) };
}
