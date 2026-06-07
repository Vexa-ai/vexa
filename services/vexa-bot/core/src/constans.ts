// User Agent — MUST stay consistent with the bundled Chromium's real version AND
// platform, or Google Meet's anti-abuse flags the UA↔Client-Hints mismatch and serves
// a reCAPTCHA + "You can't join this video call" (then redirects to
// workspace.google.com/products/meet/).
//
// The bot runs Chromium on Linux (headful under Xvfb). navigator.userAgentData
// (Client Hints) reports the *real* platform (Linux x86_64) and major version, which
// CANNOT be spoofed by a plain userAgent string. A stale/cross-platform override (the
// old "Windows ... Chrome/129" string, kept while the bundled Chromium advanced to 141)
// produced exactly that mismatch and blocked every Google Meet join.
//
// 2026-06-07: aligned to the bundled Chromium (playwright chromium-1194 = Chrome 141)
// on Linux x86_64 so UA and Client-Hints agree. If the Playwright/Chromium bundle is
// bumped, update the major version here to match (or remove the override entirely so the
// native, self-consistent UA flows through).
export const userAgent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36";

// Base browser launch arguments (shared across all modes).
//
// Pack F (2026-06-06): Removed --ignore-certificate-errors, --ignore-ssl-errors,
// --ignore-certificate-errors-spki-list, --disable-web-security, and
// --allow-running-insecure-content. These flags are detectable by Google's
// bot-detection layer and directly cause the "You can't join this meeting"
// interstitial on datacenter egress IPs (k8s / Linode LKE). Replaced with
// --disable-blink-features=AutomationControlled (mirrors getAuthenticatedBrowserArgs).
// Google Meet uses valid TLS certs; the certificate-error flags were never needed
// for meet.google.com and init-scripts are injected via CDP (unaffected by CSP).
const baseBrowserArgs = [
  "--incognito",
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-features=IsolateOrigins,site-per-process",
  "--disable-infobars",
  "--disable-gpu",
  // Collapse Chromium's gpu-process work into the renderer — no separate
  // gpu-process at all.
  //
  // 2026-04-27 measurement (cycle 260426 Zoom Web): a Zoom Web bot
  // demanded 4.4 cores; 3.6 of those (= 357% CPU) lived in
  // --type=gpu-process running SwiftShader software-WebGL + canvas
  // compositing for Zoom's UI. Software-decoded video frames also flow
  // through that process. With --in-process-gpu, the work collapses
  // into the renderer (which already runs the page's JS) and per-bot
  // demand drops to ~115% — back inside the 1500m budget that matches
  // the gmeet/teams p95 (780m).
  //
  // Earlier iterations on this cycle tried --disable-webgl /
  // --disable-3d-apis / --disable-accelerated-2d-canvas etc.; all
  // confirmed inert (gpu-process kept running because it hosts the
  // software video decoder, not just the compositor).
  // --in-process-gpu is the only flag that actually killed it.
  "--in-process-gpu",
  "--use-fake-ui-for-media-stream",
  "--use-file-for-fake-video-capture=/dev/null",
  "--disable-blink-features=AutomationControlled",
  "--disable-features=VizDisplayCompositor",
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
// CDP debug args — shared by EVERY browser launch (meeting + browser-session) so an
// agent can attach over the gateway /b/{token}/cdp proxy to clear captcha/blocking
// states. Chrome opens 9222 alongside Playwright's own --remote-debugging-pipe (the
// pipe is NOT removed — removing it severs Playwright's connection and the bot dies on
// launch). Chrome still binds 9222 on 127.0.0.1 only; the entrypoint socat relay
// re-exposes it on 0.0.0.0:9223 for the gateway to reach across the docker network.
export const CDP_DEBUG_ARGS = [
  '--remote-debugging-port=9222',
  '--remote-debugging-address=0.0.0.0',
  '--remote-allow-origins=*',
];

export function getBrowserArgs(voiceAgentEnabled: boolean = false): string[] {
  return [...baseBrowserArgs, ...CDP_DEBUG_ARGS];
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
    ...CDP_DEBUG_ARGS,
    '--password-store=basic',
  ];
}
