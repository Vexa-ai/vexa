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
 *
 * #856 (2026-07-23): the browser UI locale is now PINNED. We never used to tell
 * the browser what language to be, so Google Meet localised from Accept-Language
 * or IP geolocation and served non-English lobbies on EU/other egress — the root
 * cause of the join-button-not-found class (#846). `--lang` / `--accept-lang`
 * pin Chrome's own UI + Accept-Language header; the Playwright context `locale`
 * (remote-browser/browser.ts) pins navigator.language. The pinned value is a
 * deployment knob — BOT_UI_LOCALE, default en-US — so a deployment that genuinely
 * wants another UI language can set it. This is what makes the English lobby
 * selectors correct BY CONSTRUCTION rather than lucky.
 */

/** The pinned browser UI locale (#856). Deployment knob; default en-US. */
export function resolveBotUiLocale(): string {
  const v = (process.env.BOT_UI_LOCALE || "").trim();
  return v.length > 0 ? v : "en-US";
}

/** `--lang` / `--accept-lang` flags for the pinned UI locale (#856). Kept out of
 *  the static array below because they resolve an env knob at call time. */
export function getLocaleBrowserArgs(): string[] {
  const locale = resolveBotUiLocale();
  const primaryLang = locale.split("-")[0];
  const acceptLang = primaryLang && primaryLang !== locale ? `${locale},${primaryLang}` : locale;
  return [`--lang=${locale}`, `--accept-lang=${acceptLang}`];
}

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
  // Start AudioContexts in 'running', not 'suspended' — the capture taps remote participant audio
  // via createMediaStreamSource; without this the worklet never fires and no PCM flows. (L4.)
  "--autoplay-policy=no-user-gesture-required",
  "--use-file-for-fake-video-capture=/dev/null",
  "--disable-blink-features=AutomationControlled",
  "--disable-features=VizDisplayCompositor",
  "--disable-site-isolation-trials",
];

/** The canonical join launch args, as a fresh mutable array per call. Includes
 *  the pinned-locale flags (#856) so every launch path — production bot and the
 *  debug harness — is byte-identical and speaks the same UI language. */
export function getJoinBrowserArgs(): string[] {
  return [...JOIN_BROWSER_ARGS, ...getLocaleBrowserArgs()];
}
