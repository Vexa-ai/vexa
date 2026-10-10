/** A meeting as a portable file (meeting-bundle.v1): the calls the Export and Import actions make.
 *
 *  A bundle has two owners. meeting-api writes it and lands the meeting; the agent domain owns the
 *  meeting's workspace and its page. The domains do not call each other, so THIS client carries the
 *  agent half across:
 *
 *  Export: `/api/meeting/bundle-parts` (the workspace tree + the page, or 204) → handed to
 *  `POST /api/meetings/{id}/export`, which places them in the bundle; with none, `GET` the export.
 *  Import: `/api/meetings/import?dry_run=true` (the preview) → `/api/meetings/import` → when the
 *  bundle carries a workspace or a page, `/api/meeting/bundle-restore` with the same file.
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
  annotations: { metadata_keys: string[]; notes: boolean };
  /** What the agent domain restores after the meeting lands (`restoreParts`). */
  handoff: { workspace_files: number; notes_page: boolean };
  duplicate_of: number | null;
}

export interface ImportResult extends BundlePreview { imported: true; meeting_id: number }

export interface RestoreResult {
  meeting_id: number;
  workspace: { slug: string; files: number } | null;
  notes_page: { path: string; written: boolean } | null;
}

/** Whether the export carried the meeting's workspace and page: `included`, `none` (the meeting has
 *  neither), or `unavailable` (the agent domain refused or is not deployed — said, never hidden). */
export type PartsOutcome = { state: "included"; workspaceFiles: number; notesPage: boolean; skippedFiles: number }
  | { state: "none" } | { state: "unavailable"; reason: string };

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

/** The agent half of an export: the parts archive, or why there is none. */
async function fetchParts(meetingId: string, fetcher: typeof fetch): Promise<{ body: Blob | null; outcome: PartsOutcome }> {
  try {
    const r = await fetcher(`/api/meeting/bundle-parts?meeting_id=${encodeURIComponent(meetingId)}`, { cache: "no-store" });
    if (r.status === 204) return { body: null, outcome: { state: "none" } };
    if (!r.ok) return { body: null, outcome: { state: "unavailable", reason: (await failure(r)).message } };
    return {
      body: await r.blob(),
      outcome: {
        state: "included",
        workspaceFiles: Number(r.headers.get("x-vexa-workspace-files")) || 0,
        notesPage: r.headers.get("x-vexa-notes-page") === "1",
        skippedFiles: Number(r.headers.get("x-vexa-skipped-files")) || 0,
      },
    };
  } catch (e) {
    return { body: null, outcome: { state: "unavailable", reason: e instanceof Error ? e.message : "unreachable" } };
  }
}

/** Download a meeting's bundle, reporting progress as a fraction (null while the size is unknown). */
export async function downloadBundle(
  meetingId: string,
  onProgress: (fraction: number | null) => void,
  fetcher: typeof fetch = fetch,
): Promise<{ blob: Blob; filename: string; parts: PartsOutcome }> {
  const { body, outcome } = await fetchParts(meetingId, fetcher);
  const url = `/api/meetings/${encodeURIComponent(meetingId)}/export`;
  const r = body
    ? await fetcher(url, { method: "POST", headers: { "Content-Type": "application/zip" }, body, cache: "no-store" })
    : await fetcher(url, { cache: "no-store" });
  if (!r.ok) throw await failure(r);
  const total = Number(r.headers.get("content-length")) || 0;
  const filename = bundleFilename(r.headers.get("content-disposition"), meetingId);
  if (!r.body) {
    const blob = await r.blob();
    onProgress(1);
    return { blob, filename, parts: outcome };
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
  return { blob: new Blob(chunks as BlobPart[], { type: "application/zip" }), filename, parts: outcome };
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

/** Upload progress, as a fraction of the file sent. */
export type UploadProgress = (fraction: number) => void;

// The fetch this module was loaded with: only through the browser's own fetch is an upload swapped
// for XMLHttpRequest — the one browser API that reports upload progress. An injected fetcher (a test,
// a caller with its own transport) is always used as given.
const NATIVE_FETCH: typeof fetch | undefined = typeof fetch === "function" ? fetch : undefined;

function xhrSend(url: string, body: Blob, onProgress: UploadProgress): Promise<Response> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/zip");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable && e.total) onProgress(Math.min(1, e.loaded / e.total)); };
    xhr.onload = () => {
      onProgress(1);
      resolve(new Response(xhr.responseText, {
        status: xhr.status,
        headers: { "Content-Type": xhr.getResponseHeader("Content-Type") || "application/json" },
      }));
    };
    xhr.onerror = () => reject(new Error("the upload failed before the server answered"));
    xhr.onabort = () => reject(new Error("the upload was cancelled"));
    xhr.send(body);
  });
}

async function sendZip(url: string, body: Blob, fetcher: typeof fetch, onProgress?: UploadProgress): Promise<Response> {
  if (onProgress && fetcher === NATIVE_FETCH && typeof XMLHttpRequest !== "undefined") {
    return xhrSend(url, body, onProgress);
  }
  const r = await fetcher(url, { method: "POST", headers: { "Content-Type": "application/zip" }, body });
  onProgress?.(1);
  return r;
}

/** What importing `file` would create — nothing is written. Refused locally when it is plainly too big. */
export async function previewImport(file: Blob, fetcher: typeof fetch = fetch, onProgress?: UploadProgress): Promise<BundlePreview> {
  if (file.size > MAX_BUNDLE_BYTES) {
    throw new BundleError(413, "too_large", `The file is ${file.size} bytes; the limit is ${MAX_BUNDLE_BYTES}.`);
  }
  const r = await sendZip("/api/meetings/import?dry_run=true", file, fetcher, onProgress);
  if (!r.ok) throw await failure(r);
  return await r.json() as BundlePreview;
}

/** Import `file` for real. */
export async function confirmImport(file: Blob, fetcher: typeof fetch = fetch, onProgress?: UploadProgress): Promise<ImportResult> {
  const r = await sendZip("/api/meetings/import", file, fetcher, onProgress);
  if (!r.ok) throw await failure(r);
  return await r.json() as ImportResult;
}

/** Land the bundle's workspace and page for the meeting `confirmImport` just created from it. */
export async function restoreParts(file: Blob, meetingId: number, fetcher: typeof fetch = fetch, onProgress?: UploadProgress): Promise<RestoreResult> {
  const r = await sendZip(`/api/meeting/bundle-restore?meeting_id=${meetingId}`, file, fetcher, onProgress);
  if (!r.ok) throw await failure(r);
  return await r.json() as RestoreResult;
}
