/** The person's own photo, from Settings → Profile or "Change photo" on their own avatar (UI 15g).
 *  Identity keeps it; the browser asks only through the authenticated proxy. */
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAX_BYTES, PHOTO_CHANGED_EVENT, photoUrl, preparePhoto, readOwnPhoto, removeOwnPhoto, uploadOwnPhoto,
} from "../personPhotoApi";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 1, 2, 3]);

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function ok(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

describe("personPhotoApi", () => {
  it("names a person's photo by their account id, through the proxy", () => {
    expect(photoUrl("12")).toBe("/api/people/12/photo");
    expect(photoUrl("12", "abc")).toBe("/api/people/12/photo?v=abc");
  });

  it("reads which photo shows for the person", async () => {
    const f = vi.fn(async () => ok({ subject: "12", photo: { source: "provider", provider: "google", version: "abc" }, sources: ["provider"] }));
    vi.stubGlobal("fetch", f);
    const got = await readOwnPhoto();
    expect(got?.subject).toBe("12");
    expect(got?.photo?.source).toBe("provider");
    expect((f.mock.calls[0] as unknown as [string])[0]).toBe("/api/user/photo");
  });

  it("uploads the image as base64 with PUT and tells every avatar it changed", async () => {
    const f = vi.fn(async () => ok({ subject: "12", photo: { source: "upload", version: "def" }, sources: ["upload"] }));
    vi.stubGlobal("fetch", f);
    const heard = vi.fn();
    window.addEventListener(PHOTO_CHANGED_EVENT, heard);
    const got = await uploadOwnPhoto(new Blob([PNG], { type: "image/png" }));
    window.removeEventListener(PHOTO_CHANGED_EVENT, heard);
    expect(got.ok).toBe(true);
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/user/photo");
    expect(init.method).toBe("PUT");
    const sent = JSON.parse(String(init.body));
    expect(Array.from(Buffer.from(sent.image, "base64"))).toEqual(Array.from(PNG));
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("says why identity refused an image", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: { refused: "a photo is a PNG, JPEG or WebP image" } }), { status: 422 })));
    const got = await uploadOwnPhoto(new Blob([PNG], { type: "image/png" }));
    expect(got).toEqual({ ok: false, error: "a photo is a PNG, JPEG or WebP image" });
  });

  it("removes the upload with DELETE", async () => {
    const f = vi.fn(async () => ok({ subject: "12", photo: null, sources: [] }));
    vi.stubGlobal("fetch", f);
    expect((await removeOwnPhoto()).ok).toBe(true);
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].method).toBe("DELETE");
  });

  it("takes PNG, JPEG and WebP only — never an SVG or a GIF", async () => {
    await expect(preparePhoto(new Blob(["<svg/>"], { type: "image/svg+xml" }))).rejects.toThrow(/PNG, JPEG or WebP/);
    await expect(preparePhoto(new Blob(["GIF89a"], { type: "image/gif" }))).rejects.toThrow(/PNG, JPEG or WebP/);
    const small = new Blob([PNG], { type: "image/png" });
    expect(await preparePhoto(small)).toBe(small);
  });

  it("a photo over the limit is made smaller first, or refused when the browser cannot", async () => {
    const big = new Blob([new Uint8Array(MAX_BYTES + 1)], { type: "image/jpeg" });
    await expect(preparePhoto(big, { shrink: async () => null })).rejects.toThrow(/200 KB/);
    const smaller = new Blob([PNG], { type: "image/jpeg" });
    expect(await preparePhoto(big, { shrink: async () => smaller })).toBe(smaller);
  });
});
