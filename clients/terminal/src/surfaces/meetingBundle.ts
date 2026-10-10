/** A meeting as a portable file (meeting-bundle.v1): the two calls the Export and Import actions make.
 *
 *  Export downloads `/api/meetings/{id}/export` with progress (the server sends Content-Length) and
 *  hands the browser a file. Import posts a zip to `/api/meetings/import` twice: first with
 *  `dry_run=true` to show what WOULD be created, then for real once the person confirms.
 *
 *  Every refusal is shown as the server said it — its contract code and its sentence — never a
 *  generic "something went wrong": a bad hash, a duplicate and a too-big file need different fixes. */

export interface BundlePreview {
  bundle_id: string;
  exported_at: string;
  source: { deployment_id: string; meeting_id: number };
  meeting: {
    platform: string; native_meeting_id: string | null; title: string | null; status: string;
    start_time: string | null; end_time: string | null; participants: string[];
  };
  segments: number;
  speakers: string[];
  media: { path: string; type: string; format: string; bytes: number; duration_seconds: number | null }[];
  annotations: { metadata_keys: string[] };
  skipped: { part: string; reason: string; files?: number }[];
  duplicate_of: number | null;
}

export interface ImportResult extends BundlePreview { imported: true; meeting_id: number }

/** A refusal the server named. `code` is the contract's Refusal code when there is one. */
export class BundleError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export const MAX_BUNDLE_BYTES = 512 * 1024 * 1024;

async function failure(r: Response): Promise<BundleError> {
  let body: unknown = null;
  try { body = await r.json(); } catch { /* not JSON */ }
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (detail && typeof detail === "object" && "code" in detail) {
    const d = detail as { code: string; detail?: string };
    return new BundleError(r.status, d.code, d.detail || d.code);
  }
  const text = typeof detail === "string" ? detail : (body as { error?: string } | null)?.error;
  return new BundleError(r.status, `http_${r.status}`, text || `Request failed (${r.status})`);
}

/** The file name the server chose (Content-Disposition), or a safe fallback. */
export function bundleFilename(disposition: string | null, meetingId: string): string {
  const m = /filename="?([^";]+)"?/i.exec(disposition || "");
  const name = (m?.[1] || "").replace(/[\\/]/g, "").trim();
  return name || `meeting-${meetingId}.meeting-bundle.zip`;
}

/** Download a meeting's bundle, reporting progress as a fraction (null while the size is unknown). */
export async function downloadBundle(
  meetingId: string,
  onProgress: (fraction: number | null) => void,
  fetcher: typeof fetch = fetch,
): Promise<{ blob: Blob; filename: string }> {
  const r = await fetcher(`/api/meetings/${encodeURIComponent(meetingId)}/export`, { cache: "no-store" });
  if (!r.ok) throw await failure(r);
  const total = Number(r.headers.get("content-length")) || 0;
  const filename = bundleFilename(r.headers.get("content-disposition"), meetingId);
  if (!r.body) {
    const blob = await r.blob();
    onProgress(1);
    return { blob, filename };
  }
  const reader = r.body.getReader();
  const chunks: Uint8Array[] = [];
  let received = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    chunks.push(value);
    received += value.length;
    onProgress(total ? Math.min(1, received / total) : null);
  }
  onProgress(1);
  return { blob: new Blob(chunks as BlobPart[], { type: "application/zip" }), filename };
}

/** Hand a blob to the browser as a download. */
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

async function post(file: Blob, dryRun: boolean, fetcher: typeof fetch): Promise<Response> {
  return fetcher(`/api/meetings/import${dryRun ? "?dry_run=true" : ""}`, {
    method: "POST",
    headers: { "Content-Type": "application/zip" },
    body: file,
  });
}

/** What importing `file` would create — nothing is written. Refused locally when it is plainly too big. */
export async function previewImport(file: Blob, fetcher: typeof fetch = fetch): Promise<BundlePreview> {
  if (file.size > MAX_BUNDLE_BYTES) {
    throw new BundleError(413, "too_large", `The file is ${file.size} bytes; the limit is ${MAX_BUNDLE_BYTES}.`);
  }
  const r = await post(file, true, fetcher);
  if (!r.ok) throw await failure(r);
  return await r.json() as BundlePreview;
}

/** Import `file` for real. */
export async function confirmImport(file: Blob, fetcher: typeof fetch = fetch): Promise<ImportResult> {
  const r = await post(file, false, fetcher);
  if (!r.ok) throw await failure(r);
  return await r.json() as ImportResult;
}
