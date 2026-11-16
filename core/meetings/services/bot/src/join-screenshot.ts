/**
 * The join-failure screenshot — the one picture of WHY a bot did not get in, moved off the pod.
 *
 * The join layer writes checkpoint screenshots to `/app/storage/screenshots/` inside the container.
 * Nothing uploaded them, so they died with the pod: on 1 Nov 2026 the founder asked to see the
 * screen a prod bot was refused on, and there was no way to show it. This uploads ONE picture per
 * failed join attempt to meeting-api (`POST /internal/join-evidence/screenshot`), which stores it
 * beside the meeting's recordings and answers with the object key the terminal evidence then names.
 *
 * Shaped by the same rule as the signal-tape upload (signal-upload.ts): **it must not be able to
 * change how the attempt ended.**
 *   • Best effort: every failure (no URL, no picture, oversize, HTTP error, timeout) is logged and
 *     yields `undefined`. It never throws and never rejects.
 *   • ONE attempt, short bound: the terminal report waits for it at most `timeoutMs` (default 5 s).
 *   • Size-guarded: a picture over the server's cap is skipped, never cropped or recompressed.
 */
import { readdir, readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import type { Invocation } from './config.js';

/** Where @vexa/join writes its checkpoint screenshots (join.ts, Dockerfile). */
export const SCREENSHOT_DIR = '/app/storage/screenshots';
/** meeting-api refuses a larger picture (JOIN_SCREENSHOT_MAX_BYTES); skip it here instead of sending it. */
export const MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024;
/** The whole step — capture + upload — waits at most this long before the terminal report goes out. */
export const DEFAULT_SCREENSHOT_TIMEOUT_MS = 5_000;

const RECORDING_UPLOAD_PATH = '/internal/recordings/upload';
const SCREENSHOT_UPLOAD_PATH = '/internal/join-evidence/screenshot';
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

/** Deliver ONE picture; resolves with the stored object key. Injected in tests. */
export type ScreenshotUploader = (png: Buffer) => Promise<string>;

export interface JoinScreenshotOptions {
  inv: Invocation;
  /** A fresh picture of the page as it is now (the join driver's `screenshot`), if there is a page. */
  capture?: () => Promise<Buffer | undefined>;
  /** Where to fall back to the newest checkpoint file. */
  dir?: string;
  upload?: ScreenshotUploader;
  timeoutMs?: number;
  log?: (line: string) => void;
}

/**
 * The upload URL, derived from the invocation's recording upload URL exactly as the signal tape
 * reaches its route: same host, same internal edge, same credential. `undefined` when the
 * invocation carries no upload URL (the local hot loop), or one that does not end in the recording
 * route (never guess a path on someone else's server).
 */
export function screenshotUploadUrl(inv: Invocation): string | undefined {
  const raw = inv.recordingUploadUrl;
  if (!raw) return undefined;
  try {
    const u = new URL(raw);
    if (!u.pathname.endsWith(RECORDING_UPLOAD_PATH)) return undefined;
    u.pathname = u.pathname.slice(0, -RECORDING_UPLOAD_PATH.length) + SCREENSHOT_UPLOAD_PATH;
    u.search = '';
    return u.toString();
  } catch {
    return undefined;
  }
}

/** The newest `.png` in `dir`, or `undefined`. The join layer writes one per checkpoint, so the
 *  newest is the last screen the bot saw before it gave up. */
export async function newestCheckpoint(dir: string): Promise<Buffer | undefined> {
  try {
    const names = (await readdir(dir)).filter((n) => n.toLowerCase().endsWith('.png'));
    let best: { path: string; mtime: number } | undefined;
    for (const name of names) {
      const path = join(dir, name);
      const s = await stat(path).catch(() => undefined);
      if (s?.isFile() && (!best || s.mtimeMs > best.mtime)) best = { path, mtime: s.mtimeMs };
    }
    return best ? await readFile(best.path) : undefined;
  } catch {
    return undefined;
  }
}

/** The default transport: one multipart POST with the session's MeetingToken. */
export function httpScreenshotUploader(inv: Invocation, url: string, timeoutMs: number): ScreenshotUploader {
  return async (png) => {
    const form = new FormData();
    form.set('session_uid', inv.connectionId ?? '');
    form.set('file', new Blob([png], { type: 'image/png' }), 'join-failure.png');
    const res = await fetch(url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${inv.token ?? ''}` },
      body: form,
      signal: AbortSignal.timeout(timeoutMs),
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`upload rejected: HTTP ${res.status} ${text.slice(0, 200)}`);
    const key = (JSON.parse(text) as { key?: unknown }).key;
    if (typeof key !== 'string' || !key) throw new Error('upload answered without a key');
    return key;
  };
}

/**
 * Capture (or reuse the newest checkpoint) and upload ONE join-failure picture. Resolves with the
 * stored key, or `undefined`. NEVER throws, NEVER rejects, and settles within `timeoutMs`.
 */
export async function uploadJoinFailureScreenshot(opts: JoinScreenshotOptions): Promise<string | undefined> {
  const log = opts.log ?? ((line: string) => console.log(line));
  const timeoutMs = opts.timeoutMs ?? DEFAULT_SCREENSHOT_TIMEOUT_MS;
  let timer: NodeJS.Timeout | undefined;
  const deadline = new Promise<undefined>((resolve) => {
    timer = setTimeout(() => {
      log(`[bot] join screenshot: gave up after ${timeoutMs} ms`);
      resolve(undefined);
    }, timeoutMs);
  });
  try {
    return await Promise.race([attempt(opts, log, timeoutMs), deadline]);
  } finally {
    if (timer) clearTimeout(timer);
  }
}

async function attempt(opts: JoinScreenshotOptions, log: (line: string) => void,
                       timeoutMs: number): Promise<string | undefined> {
  try {
    const url = screenshotUploadUrl(opts.inv);
    if (!url && !opts.upload) {
      log('[bot] join screenshot: skipped (no recordingUploadUrl in the invocation)');
      return undefined;
    }
    let png = opts.capture ? await opts.capture().catch(() => undefined) : undefined;
    if (!isPng(png)) png = await newestCheckpoint(opts.dir ?? SCREENSHOT_DIR);
    if (!isPng(png)) {
      log('[bot] join screenshot: skipped (no picture)');
      return undefined;
    }
    if (png.length > MAX_SCREENSHOT_BYTES) {
      log(`[bot] join screenshot: skipped (${png.length} bytes > ${MAX_SCREENSHOT_BYTES})`);
      return undefined;
    }
    const upload = opts.upload ?? httpScreenshotUploader(opts.inv, url!, timeoutMs);
    const key = await upload(png);
    log(`[bot] join screenshot: stored ${key} (${png.length} bytes)`);
    return key;
  } catch (e) {
    log(`[bot] join screenshot: upload failed: ${String(e)}`);
    return undefined;
  }
}

function isPng(b: Buffer | undefined): b is Buffer {
  return !!b && b.length > PNG_MAGIC.length && b.subarray(0, PNG_MAGIC.length).equals(PNG_MAGIC);
}
