/**
 * Canonical browser launch args for joining a meeting — the SINGLE source of truth
 * for the browser environment the join layer requires.
 *
 * Who consumes this (so it never drifts):
 *  - the `vexa-bot` service builds its real meeting launches on top of these
 *    (services/vexa-bot/core/src/constans.ts → baseBrowserArgs), then layers on
 *    bot-only concerns (voice-agent audio, CDP debug exposure);
 *  - the standalone debug harness (scripts/debug-join.ts) launches with these
 *    verbatim, so the hot-debug container reproduces production exactly.
 *
 * The isolation law (modules never import services) makes this the only place the
 * set can live without drift: the service imports FROM here, never the reverse.
 *
 * Pack F (2026-06-06): deliberately NO --ignore-certificate-errors / --ignore-ssl-errors
 * / --disable-web-security / --allow-running-insecure-content — those are detectable by
 * Google's bot-detection layer and directly cause the "You can't join this meeting"
 * interstitial on datacenter egress IPs. Meet uses valid TLS; init-scripts inject via
 * CDP (unaffected by CSP). --disable-blink-features=AutomationControlled replaces them.
 */
export const JOIN_BROWSER_ARGS: readonly string[] = [
  "--incognito",
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-features=IsolateOrigins,site-per-process",
  "--disable-infobars",
  "--disable-gpu",
  // Collapse Chromium's gpu-process work into the renderer — no separate
  // gpu-process at all. 2026-04-27 measurement (Zoom Web): the gpu-process ran
  // SwiftShader software-WebGL + the software video decoder at ~357% CPU;
  // --in-process-gpu folds that into the renderer and drops per-bot demand from
  // ~4.4 cores to ~115%. --disable-webgl/--disable-3d-apis were all inert (the
  // gpu-process hosts the decoder, not just the compositor); this is the only
  // flag that actually killed it. Belongs to the launch ENV the bot runs join in,
  // so it lives here to keep the debug harness byte-for-byte with production.
  "--in-process-gpu",
  "--use-fake-ui-for-media-stream",
  "--use-file-for-fake-video-capture=/dev/null",
  "--disable-blink-features=AutomationControlled",
  "--disable-features=VizDisplayCompositor",
  "--disable-site-isolation-trials",
  // Allow AudioContext to start in "running" state without a user gesture.
  // Without this, ctx.resume() in the per-speaker audio pipeline never
  // actually starts the AudioContext — the promise stays pending — so the
  // AudioWorklet never processes audio and whisper=0 forever.
  "--autoplay-policy=no-user-gesture-required",
];

/** The canonical join launch args, as a fresh mutable array per call. */
export function getJoinBrowserArgs(): string[] {
  return [...JOIN_BROWSER_ARGS];
}
