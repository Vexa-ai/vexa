// User Agent for consistency - Updated to modern Chrome version for Google Meet compatibility
export const userAgent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36";

// Base browser launch arguments (shared across all modes)
const baseBrowserArgs = [
  "--incognito",
  "--no-sandbox",
  "--disable-setuid-sandbox",
  "--disable-features=IsolateOrigins,site-per-process",
  "--disable-infobars",
  "--disable-gpu",
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
export function getBrowserArgs(voiceAgentEnabled: boolean = false): string[] {
  let args = [...baseBrowserArgs];

  if (voiceAgentEnabled) {
    // Audio: Omit --use-file-for-fake-audio-capture so Chromium reads from
    // PulseAudio default source (virtual_mic → tts_sink.monitor).
    // This allows TTS audio played to tts_sink to enter the meeting as mic input.
    //
    // Video: Keep --use-file-for-fake-video-capture=/dev/null (from base args).
    // Our getUserMedia patch in the init script intercepts video requests and
    // returns a canvas stream. The replaceTrack in enableCamera() swaps the
    // WebRTC sender track for our canvas track.
    //
    // NOTE: Do NOT use --use-fake-device-for-media-stream here — it creates
    // Chromium-internal fake devices that bypass PulseAudio entirely,
    // preventing TTS audio from reaching the meeting.
  } else {
    // Silence mic input when voice agent is not active
    args.push("--use-file-for-fake-audio-capture=/dev/null");
  }

  return args;
}

// Default browser args for backward compatibility (voice agent disabled)
export const browserArgs = getBrowserArgs(false);
