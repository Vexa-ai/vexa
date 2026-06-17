// Canonical join launch args live in the join brick (single source of truth — the
// service consumes them, never re-declares them; see modules/join/src/browser-args.ts).
import { JOIN_BROWSER_ARGS } from "@vexa/join";

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
// The meeting-launch environment is the join brick's contract (JOIN_BROWSER_ARGS) —
// consumed here, never duplicated, so the brick's debug harness and this image
// launch byte-for-byte the same browser (no drift). Bot-only concerns (voice-agent
// audio, CDP debug) are layered on below.
const baseBrowserArgs = [...JOIN_BROWSER_ARGS];

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
// Persistent-context / interactive (VNC) browser args — getAuthenticatedBrowserArgs,
// getBrowserSessionArgs, CDP_DEBUG_ARGS — now live in @vexa/remote-browser (the
// browser-as-container brick, single source of truth). The MEETING args below stay
// here: they are a bot concern, built on the join brick's JOIN_BROWSER_ARGS.
export function getBrowserArgs(voiceAgentEnabled: boolean = false): string[] {
  const args = [...baseBrowserArgs];
  // Opt-in CDP exposure for the hot-debug loop. Inert unless BOT_DEBUG_CDP=true.
  if (process.env.BOT_DEBUG_CDP === 'true') {
    args.push(
      '--remote-debugging-port=9222',
      '--remote-debugging-address=0.0.0.0',
      '--remote-allow-origins=*'
    );
  }
  return args;
}

// Default browser args
export const browserArgs = getBrowserArgs(false);
