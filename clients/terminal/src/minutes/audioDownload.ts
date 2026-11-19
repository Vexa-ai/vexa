"use client";
/** Saving a meeting's audio. The bytes come from the SAME owner-scoped route the player streams
 *  (`/api/recordings/<id>/media/<mid>/raw`), asked for as a file (`download=1`): no second auth path,
 *  no credential in a URL. Fetched rather than linked so a refusal or a dropped read surfaces as an
 *  explicit error on the page instead of a silent failed entry in the browser's download list. */

const EXT_BY_TYPE: Record<string, string> = {
  "audio/webm": "webm", "video/webm": "webm", "audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
  "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
};

function slug(value: string): string {
  return value.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 64);
}

/** The real container's extension: the response's Content-Type first, then the media file's own
 *  `format`; never guessed. */
export function audioExtension(contentType: string | null | undefined, format?: string): string {
  const type = (contentType || "").split(";")[0].trim().toLowerCase();
  if (EXT_BY_TYPE[type]) return EXT_BY_TYPE[type];
  const f = (format || "").toLowerCase();
  return /^[a-z0-9]{1,8}$/.test(f) ? f : "bin";
}

/** `<platform>-<native_id>-<date>.<ext>`, e.g. `zoom-84512345678-2026-10-09.webm`. */
export function audioFilename(meeting: { platform?: string; native_id?: string; id: string; start_time?: string }, ext: string): string {
  const platform = slug(meeting.platform || "") || "meeting";
  const native = slug(meeting.native_id || "") || slug(meeting.id) || "recording";
  const start = meeting.start_time && Number.isFinite(Date.parse(meeting.start_time)) ? new Date(meeting.start_time).toISOString().slice(0, 10) : "";
  return [platform, native, start].filter(Boolean).join("-") + "." + ext;
}

export function audioDownloadUrl(recording: number, media: number): string {
  return `/api/recordings/${recording}/media/${media}/raw?type=audio&download=1`;
}

/** Fetch the recording and hand it to the browser as a file. Throws a reader-facing message on any
 *  refusal or read failure. */
export async function downloadAudio(url: string, filename: (contentType: string | null) => string): Promise<void> {
  let response: Response;
  try { response = await fetch(url, { cache: "no-store" }); }
  catch { throw new Error("Could not download audio: the connection failed. Retry."); }
  if (!response.ok) {
    throw new Error(response.status === 404
      ? "Could not download audio: the recording was not found."
      : `Could not download audio (HTTP ${response.status}). Retry.`);
  }
  let blob: Blob;
  try { blob = await response.blob(); }
  catch { throw new Error("Could not download audio: the transfer was interrupted. Retry."); }
  if (!blob.size) throw new Error("Could not download audio: the recording is empty.");
  const href = URL.createObjectURL(blob);
  try {
    const a = document.createElement("a");
    a.href = href; a.download = filename(response.headers.get("Content-Type")); a.rel = "noopener"; a.style.display = "none";
    document.body.appendChild(a); a.click(); a.remove();
  } finally {
    // Revoked later, not now: some browsers read the blob after click() returns.
    setTimeout(() => URL.revokeObjectURL(href), 60_000);
  }
}
