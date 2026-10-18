/** The person's own photo (UI decision 15g, Vexa-ai/vexa#1783).
 *
 *  Identity (admin-api) keeps it and is its one writer; these calls ride the authenticated catch-all
 *  proxy to the gateway (`/api/user/photo` → identity's `/user/photo`) with the person's own key. A
 *  worker acting for them is refused there: only the person changes their own face.
 *
 *  Anyone's photo is shown from `/api/people/{subject}/photo` (agent-api), which answers only for the
 *  person and people who share a workspace or chat with them. */

export const PHOTO_CHANGED_EVENT = "vexa:photo-changed";
/** Identity's limit; a bigger picture is made smaller here first. */
export const MAX_BYTES = 200 * 1024;
const TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const SIDE = 512;

export type PhotoSource = "upload" | "provider" | "gravatar";
export type OwnPhoto = {
  subject: string;
  photo: { source: PhotoSource; version?: string; provider?: string; updated_at?: number } | null;
  sources: PhotoSource[];
};
export type PhotoResult = { ok: true; photo: OwnPhoto } | { ok: false; error: string };

export function photoUrl(subject: string, version?: string): string {
  return `/api/people/${encodeURIComponent(subject)}/photo${version ? `?v=${encodeURIComponent(version)}` : ""}`;
}

/** Which photo shows for the signed-in person, and their account id. Null when it cannot be read. */
export async function readOwnPhoto(): Promise<OwnPhoto | null> {
  try {
    const res = await fetch("/api/user/photo", { cache: "no-store" });
    return res.ok ? ((await res.json()) as OwnPhoto) : null;
  } catch {
    return null;
  }
}

async function errorOf(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const d = body?.detail;
    if (d && typeof d === "object" && typeof d.refused === "string") return d.refused;
    if (typeof d === "string") return d;
  } catch { /* not JSON */ }
  return res.status === 403 ? "Only you can change your photo." : "The photo could not be saved just now.";
}

function announce(photo: OwnPhoto) {
  if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent(PHOTO_CHANGED_EVENT, { detail: photo }));
}

async function send(method: "PUT" | "DELETE", body?: unknown): Promise<PhotoResult> {
  try {
    const res = await fetch("/api/user/photo", {
      method, cache: "no-store",
      ...(body ? { body: JSON.stringify(body), headers: { "Content-Type": "application/json" } } : {}),
    });
    if (!res.ok) return { ok: false, error: await errorOf(res) };
    const photo = (await res.json()) as OwnPhoto;
    announce(photo);
    return { ok: true, photo };
  } catch {
    return { ok: false, error: "The photo could not be saved just now." };
  }
}

async function base64Of(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** Draw the picture at most SIDE pixels on its longer side as a JPEG — or null where the browser
 *  cannot (no canvas). */
async function shrinkInBrowser(file: Blob): Promise<Blob | null> {
  if (typeof createImageBitmap !== "function" || typeof document === "undefined") return null;
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, SIDE / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(bmp.width * scale));
    canvas.height = Math.max(1, Math.round(bmp.height * scale));
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    for (const q of [0.88, 0.75, 0.6]) {
      const out = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", q));
      if (out && out.size <= MAX_BYTES) return out;
    }
    return null;
  } catch {
    return null;
  }
}

/** The picture as identity takes it: PNG, JPEG or WebP, at most MAX_BYTES (shrunk when larger). */
export async function preparePhoto(file: Blob, opts: { shrink?: (f: Blob) => Promise<Blob | null> } = {}): Promise<Blob> {
  if (!TYPES.has(file.type)) throw new Error("A photo is a PNG, JPEG or WebP image.");
  if (file.size <= MAX_BYTES) return file;
  const smaller = await (opts.shrink ?? shrinkInBrowser)(file);
  if (!smaller || smaller.size > MAX_BYTES) throw new Error("That picture is too large — a photo is at most 200 KB.");
  return smaller;
}

export async function uploadOwnPhoto(file: Blob): Promise<PhotoResult> {
  let ready: Blob;
  try {
    ready = await preparePhoto(file);
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
  return send("PUT", { image: await base64Of(ready) });
}

/** Remove the person's upload: their sign-in provider's photo or Gravatar shows again, else initials. */
export function removeOwnPhoto(): Promise<PhotoResult> {
  return send("DELETE");
}

/** Open the file picker for a photo. Resolves null when the person cancels. */
export function pickPhotoFile(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp";
    input.onchange = () => resolve(input.files?.[0] ?? null);
    input.addEventListener("cancel", () => resolve(null));
    input.click();
  });
}

/** "Change photo" on the person's own avatar (`<Identity onChange>`): pick, upload, done. Null when
 *  they cancelled. */
export async function changeOwnPhoto(): Promise<PhotoResult | null> {
  const file = await pickPhotoFile();
  return file ? uploadOwnPhoto(file) : null;
}
