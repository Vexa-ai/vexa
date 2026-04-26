// User Agent for consistency - Updated to modern Chrome version for Google Meet compatibility
export const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";

// Base browser launch arguments (shared across all modes).
//
// 2026-04-26 (cycle 260426 Zoom Web): added --disable-webgl and friends.
// The bot was already passing --disable-gpu but Chrome's GPU process was
// still measured at 180%+ CPU on Zoom Web meetings — Chromium falls back
// to SwiftShader (software WebGL) for any WebGL context the page asks
// for, and Zoom Web's UI uses WebGL for tile compositing / animations.
// Disabling WebGL outright removes that fallback and the GPU process
// drops to near-zero. No transcription path uses WebGL, so this is
// pure overhead removal for our headless meeting bot.
const baseBrowserArgs = [
  "--incognito",
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-features=IsolateOrigins,site-per-process",
  "--disable-infobars",
  "--disable-gpu",
  // GPU/WebGL/compositor — purely overhead for a headless transcription bot.
  "--disable-gpu-compositing",
  "--disable-webgl",
  "--disable-webgl2",
  "--disable-3d-apis",
  "--disable-accelerated-2d-canvas",
  "--disable-accelerated-video-decode",
  "--disable-accelerated-mjpeg-decode",
  "--disable-canvas-aa",
  "--disable-2d-canvas-clip-aa",
  "--disable-gl-drawing-for-tests",
  "--use-fake-ui-for-media-stream",
  "--use-file-for-fake-video-capture=/dev/null",
  "--allow-running-insecure-content",
  "--disable-web-security",
  "--disable-features=VizDisplayCompositor",
  "--ignore-certificate-errors",
  "--ignore-ssl-errors",
  "--ignore-certificate-errors-spki-list",
  "--disable-site-isolation-trials"
];

/**
 * Get browser launch arguments based on voice agent state.
 *
 * When voiceAgentEnabled is false (default):
 *   --use-file-for-fake-audio-capture=/dev/null  → silence as mic input
 *
 * When voiceAgentEnabled is true:
 *   Omit the fake-audio-capture flag so Chromium reads from PulseAudio default
 *   source (virtual_mic remap of tts_sink.monitor), allowing TTS audio into meeting.
 */
/**
 * Get browser launch arguments.
 *
 * All bots use PulseAudio (no /dev/null). Silence is achieved by:
 * - PulseAudio: tts_sink and virtual_mic muted at startup (entrypoint.sh)
 * - Teams UI: mic muted after join (join.ts)
 * - TTS: unmutes pactl + UI mic before speaking, re-mutes after
 */
export function getBrowserArgs(voiceAgentEnabled: boolean = false): string[] {
  return [...baseBrowserArgs];
}

/**
 * Browser args for authenticated bot mode (persistent context with stored cookies).
 * Uses minimal, clean flags — aggressive flags like --disable-web-security and
 * --ignore-certificate-errors trigger Google's bot detection and cause "You can't
 * join this video call" blocks. Modeled after getBrowserSessionArgs().
 */
export function getAuthenticatedBrowserArgs(): string[] {
  return [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-blink-features=AutomationControlled',
    '--disable-infobars',
    '--disable-gpu',
    '--use-fake-ui-for-media-stream',
    '--use-file-for-fake-video-capture=/dev/null',
    '--disable-features=VizDisplayCompositor',
    '--password-store=basic',
  ];
}

// Default browser args
export const browserArgs = getBrowserArgs(false);

/**
 * Browser args for interactive browser session mode (VNC + CDP).
 * No incognito, no fake media — human interacts via VNC, agent via CDP.
 */
export function getBrowserSessionArgs(): string[] {
  return [
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-blink-features=AutomationControlled',
    '--use-fake-ui-for-media-stream',
    '--start-maximized',
    '--window-size=1920,1080',
    '--window-position=0,0',
    '--remote-debugging-port=9222',
    '--remote-debugging-address=0.0.0.0',
    '--remote-allow-origins=*',
    '--password-store=basic',
  ];
}
