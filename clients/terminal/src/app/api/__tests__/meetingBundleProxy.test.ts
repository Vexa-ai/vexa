import { afterEach, describe, expect, it, vi } from "vitest";

/** The two meeting-bundle routes carry BYTES, which the catch-all cannot: it decodes every body as
 *  text and labels it JSON, so a zip through it loses every non-UTF-8 byte and every manifest hash
 *  fails. These prove the bytes and the headers that matter pass through untouched both ways. */

vi.mock("next/headers", () => ({
  cookies: async () => ({
    get: (name: string) => (name === "vexa-token" ? { name, value: "alice-tok" } : undefined),
  }),
}));

import { GET as exportRoute, POST as exportWithPartsRoute } from "../meetings/[id]/export/route";
import { GET as partsRoute } from "../meeting/bundle-parts/route";
import { POST as restoreRoute } from "../meeting/bundle-restore/route";
import { POST as importRoute } from "../meetings/import/route";

const ZIP = new Uint8Array([0x50, 0x4b, 0x03, 0x04, 0xff, 0xfe, 0x00, 0x80, 0xc3, 0x28]);

function req(search = "", body: BodyInit | null = null): import("next/server").NextRequest {
  return {
    nextUrl: { search, searchParams: new URLSearchParams(search) },
    body,
    headers: new Headers({ "content-length": "10" }),
  } as unknown as import("next/server").NextRequest;
}

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("export proxy", () => {
  it("streams the zip through byte-for-byte with its file name and length", async () => {
    const fetchSpy = vi.fn(async () => new Response(ZIP, { status: 200, headers: {
      "Content-Type": "application/zip", "Content-Length": "10",
      "Content-Disposition": 'attachment; filename="sync.meeting-bundle.zip"',
    } }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await exportRoute(req(), { params: Promise.resolve({ id: "42" }) });
    expect(res.status).toBe(200);
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(ZIP);
    expect(res.headers.get("content-disposition")).toBe('attachment; filename="sync.meeting-bundle.zip"');
    expect(res.headers.get("content-type")).toBe("application/zip");
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/meetings\/42\/export$/);
    expect((init.headers as Record<string, string>)["X-API-Key"]).toBeTruthy();
  });

  it("passes a transcript-only request through", async () => {
    const fetchSpy = vi.fn(async () => new Response(ZIP, { status: 200 }));
    vi.stubGlobal("fetch", fetchSpy);
    await exportRoute(req("?media=false"), { params: Promise.resolve({ id: "7" }) });
    expect((fetchSpy.mock.calls[0] as unknown as [string])[0]).toMatch(/\/meetings\/7\/export\?media=false$/);
  });

  it("refuses a non-numeric id without reaching the gateway", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const res = await exportRoute(req(), { params: Promise.resolve({ id: "../admin" }) });
    expect(res.status).toBe(422);
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("passes a refusal through with its status (403: not the owner)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Only the meeting's owner can export it" }), { status: 403, headers: { "Content-Type": "application/json" } })));
    const res = await exportRoute(req(), { params: Promise.resolve({ id: "42" }) });
    expect(res.status).toBe(403);
    expect((await res.json()).detail).toMatch(/owner/);
  });
});

describe("import proxy", () => {
  it("streams the body to the gateway as a zip and returns its JSON", async () => {
    let sent: Uint8Array | null = null;
    const fetchSpy = vi.fn(async (_url: string, init: RequestInit) => {
      sent = new Uint8Array(await new Response(init.body as BodyInit).arrayBuffer());
      return new Response(JSON.stringify({ imported: true, meeting_id: 9 }), { status: 201, headers: { "Content-Type": "application/json" } });
    });
    vi.stubGlobal("fetch", fetchSpy);
    const res = await importRoute(req("", new Response(ZIP).body as unknown as BodyInit));
    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ imported: true, meeting_id: 9 });
    expect(sent).toEqual(ZIP);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/meetings\/import$/);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/zip");
  });

  it("forwards dry_run and passes a refusal's code through", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ detail: { code: "hash_mismatch", detail: "x" } }), { status: 422, headers: { "Content-Type": "application/json" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await importRoute(req("?dry_run=true", new Response(ZIP).body as unknown as BodyInit));
    expect((fetchSpy.mock.calls[0] as unknown as [string])[0]).toMatch(/\/meetings\/import\?dry_run=true$/);
    expect(res.status).toBe(422);
    expect((await res.json()).detail.code).toBe("hash_mismatch");
  });
});

describe("the agent half: parts and restore", () => {
  it("export with parts streams the parts body to the gateway as a POST", async () => {
    let sent: Uint8Array | null = null;
    const fetchSpy = vi.fn(async (_url: string, init: RequestInit) => {
      sent = new Uint8Array(await new Response(init.body as BodyInit).arrayBuffer());
      return new Response(ZIP, { status: 200, headers: { "Content-Type": "application/zip" } });
    });
    vi.stubGlobal("fetch", fetchSpy);
    const res = await exportWithPartsRoute(req("", new Response(ZIP).body as unknown as BodyInit), { params: Promise.resolve({ id: "42" }) });
    expect(res.status).toBe(200);
    expect(sent).toEqual(ZIP);
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toMatch(/\/meetings\/42\/export$/);
    expect(init.method).toBe("POST");
  });

  it("parts are read from the agent branch and passed through as bytes with their counts", async () => {
    const fetchSpy = vi.fn(async () => new Response(ZIP, { status: 200, headers: { "Content-Type": "application/zip", "X-Vexa-Skipped-Files": "1" } }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await partsRoute(req("?meeting_id=42"));
    expect(new Uint8Array(await res.arrayBuffer())).toEqual(ZIP);
    expect(res.headers.get("x-vexa-skipped-files")).toBe("1");
    expect((fetchSpy.mock.calls[0] as unknown as [string])[0]).toMatch(/\/agent\/meeting\/bundle-parts\?meeting_id=42$/);
  });

  it("a meeting with no parts answers 204, and a non-numeric id never reaches the gateway", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(null, { status: 204 })));
    expect((await partsRoute(req("?meeting_id=42"))).status).toBe(204);
    const spy = vi.fn();
    vi.stubGlobal("fetch", spy);
    expect((await partsRoute(req("?meeting_id=1;drop"))).status).toBe(422);
    expect((await restoreRoute(req("?meeting_id=../x", null))).status).toBe(422);
    expect(spy).not.toHaveBeenCalled();
  });

  it("restore streams the bundle to the agent branch and passes the refusal through", async () => {
    const fetchSpy = vi.fn(async () => new Response(JSON.stringify({ detail: { code: "duplicate_import", detail: "x" } }), { status: 409 }));
    vi.stubGlobal("fetch", fetchSpy);
    const res = await restoreRoute(req("?meeting_id=12", new Response(ZIP).body as unknown as BodyInit));
    expect(res.status).toBe(409);
    expect((fetchSpy.mock.calls[0] as unknown as [string])[0]).toMatch(/\/agent\/meeting\/bundle-restore\?meeting_id=12$/);
  });
});
