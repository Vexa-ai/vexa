/**
 * The bot's camera — what the meeting sees.  // L4: validated in a live meeting.
 *
 * The canvas itself is installed page-side at document-start (@vexa/virtual-camera, in the browser
 * bundle). This drives it: turn the meeting's camera on once the bot is admitted, keep frames
 * flowing, draw what the control plane asked for (`avatar_set` / `screen_show`), and push a fresh
 * canvas track onto the senders — when a platform renegotiates, the track a sender holds can end, and
 * an ended track is a black tile.
 *
 * Images are fetched HERE, not by the page: a meeting page's CSP blocks third-party images, and the
 * URL is re-checked at fetch time (media-fetch). Gated on inv.voiceAgentEnabled, like speak.
 */
import type { Page } from '@vexa/remote-browser';
import type { Invocation } from './config.js';
import { ImageFetchRefused, fetchImageAsDataUri } from './media-fetch.js';

/** The meeting's own camera toggle, in the state that means "off". */
const CAMERA_ON_SELECTORS = [
  'button[aria-label*="Turn on camera"]',
  '[role="button"][aria-label*="Turn on camera"]',
  'button[aria-label*="Turn camera on"]',
];
const CAMERA_OFF_SELECTORS = ['[aria-label*="Turn off camera"]'];
/** How many times to push the track when no frames are leaving yet, and how long to wait between. */
const APPLY_ATTEMPTS = 3;
const APPLY_WAIT_MS = 2000;

export interface CameraController {
  /** Post-admission: camera on, frames flowing, the bot's avatar on screen. */
  start(): Promise<void>;
  /** `screen_show`: draw this image until it is cleared. */
  show(url: string): Promise<void>;
  /** One already-decoded frame (a `data:` URI), drawn silently — for a live source like the
   *  external agent's page, which pushes a frame a second and must not narrate each one. */
  showFrame(dataUri: string): Promise<boolean>;
  /** `screen_stop`: back to the avatar (or black when there is none). */
  clear(): Promise<void>;
  /** `avatar_set`: the picture the bot shows when nothing else is on screen. */
  setAvatar(url: string): Promise<void>;
  /** `avatar_reset`: back to the avatar this bot was started with. */
  resetAvatar(): Promise<void>;
}

export function createCameraController(page: Page, inv: Invocation): CameraController {
  const enabled = !!inv.voiceAgentEnabled;
  let defaultAvatar: string | null = null;   // resolved once, from the invocation
  let avatar: string | null = null;          // what `screen_stop` / `avatar_reset` returns to
  let started = false;
  let liveSourceAttached = false;            // a frame pump only needs the track pushed once

  const log = (m: string): void => console.log(`[bot] camera: ${m}`);

  const draw = async (image: string | null): Promise<void> => {
    const drawn = await page.evaluate(async (src: string | null) => {
      const utils = (globalThis as any).VexaBrowserUtils;
      if (!utils?.installVirtualCamera) return false;
      return src ? await utils.drawVirtualCameraImage(src) : utils.clearVirtualCamera();
    }, image).catch((e: unknown) => { log(`could not draw: ${String(e)}`); return false; });
    if (!drawn) log('nothing was drawn — the camera is showing what it showed before');
  };

  const applyTrack = async (): Promise<number> => {
    const result = await page.evaluate(async () => {
      const utils = (globalThis as any).VexaBrowserUtils;
      return utils?.applyVirtualCameraTrack ? await utils.applyVirtualCameraTrack() : { replaced: 0, senders: 0, errors: ['no camera'] };
    }).catch((e: unknown) => ({ replaced: 0, senders: 0, errors: [String(e)] }));
    if (result.errors?.length) log(`track push: ${result.replaced}/${result.senders} sender(s), ${result.errors.join('; ')}`);
    return result.replaced ?? 0;
  };

  const framesSent = (): Promise<number> => page.evaluate(async () => {
    const utils = (globalThis as any).VexaBrowserUtils;
    return utils?.virtualCameraFramesSent ? await utils.virtualCameraFramesSent() : 0;
  }).catch(() => 0);

  /** Turn the meeting's camera on. The join flow deliberately leaves it off. */
  const cameraOn = async (): Promise<boolean> => page.evaluate(({ on, off }: { on: string[]; off: string[] }) => {
    const doc = (globalThis as any).document;
    const find = (sels: string[]) => sels.map((s) => doc?.querySelector(s)).find(Boolean);
    if (find(off)) return true;                 // already on
    const button = find(on) as { click?: () => void } | undefined;
    if (!button?.click) return false;
    button.click();
    return true;
  }, { on: CAMERA_ON_SELECTORS, off: CAMERA_OFF_SELECTORS }).catch(() => false);

  const resolveImage = async (url: string): Promise<string | null> => {
    try {
      return await fetchImageAsDataUri(url);
    } catch (e) {
      // The control plane accepted this URL; refusing it here is a fetch-time fact worth naming.
      log(e instanceof ImageFetchRefused ? `image refused: ${e.message}` : `image fetch failed: ${String(e)}`);
      return null;
    }
  };

  return {
    async start(): Promise<void> {
      if (!enabled || started) return;
      started = true;
      if (inv.defaultAvatarUrl) {
        defaultAvatar = await resolveImage(inv.defaultAvatarUrl);
        avatar = defaultAvatar;
      }
      const toggled = await cameraOn();
      log(toggled ? 'camera turned on' : 'no camera toggle found — publishing the canvas anyway');
      await page.evaluate(() => (globalThis as any).VexaBrowserUtils?.startVirtualCameraPump?.()).catch(() => {});
      await draw(avatar);
      for (let attempt = 1; attempt <= APPLY_ATTEMPTS; attempt++) {
        await applyTrack();
        const frames = await framesSent();
        if (frames > 0) { log(`video is flowing (${frames} frames sent)`); return; }
        if (attempt < APPLY_ATTEMPTS) await new Promise((r) => setTimeout(r, APPLY_WAIT_MS));
      }
      // Not fatal: the bot stays in the meeting and keeps transcribing; only its tile is dark.
      log('no video frames are leaving yet — the meeting may not have accepted the camera');
    },

    async show(url: string): Promise<void> {
      if (!enabled) { console.error('[bot] screen_show ignored: voiceAgentEnabled is false'); return; }
      const image = await resolveImage(url);
      if (!image) return;
      await draw(image);
      await applyTrack();
      log(`showing an image (${await framesSent()} frames sent so far)`);
    },

    async showFrame(dataUri: string): Promise<boolean> {
      if (!enabled) return false;
      const drawn = await page.evaluate(async (src: string) => {
        const utils = (globalThis as any).VexaBrowserUtils;
        return utils?.drawVirtualCameraImage ? await utils.drawVirtualCameraImage(src) : false;
      }, dataUri).catch(() => false);
      // The first frame of a live source is the one that needs a sender; after that the pump
      // keeps the same track alive and re-pushing per frame would be pointless work.
      if (drawn && !liveSourceAttached) { liveSourceAttached = true; await applyTrack(); }
      return drawn;
    },

    async clear(): Promise<void> {
      if (!enabled) { console.error('[bot] screen_stop ignored: voiceAgentEnabled is false'); return; }
      await draw(avatar);
      log(avatar ? 'back to the avatar' : 'screen cleared');
    },

    async setAvatar(url: string): Promise<void> {
      if (!enabled) { console.error('[bot] avatar_set ignored: voiceAgentEnabled is false'); return; }
      const image = await resolveImage(url);
      if (!image) return;
      avatar = image;
      await draw(avatar);
      await applyTrack();
      log(`avatar set (${await framesSent()} frames sent so far)`);
    },

    async resetAvatar(): Promise<void> {
      if (!enabled) { console.error('[bot] avatar_reset ignored: voiceAgentEnabled is false'); return; }
      avatar = defaultAvatar;
      await draw(avatar);
      log(avatar ? 'avatar reset to the one this bot started with' : 'avatar cleared');
    },
  };
}
