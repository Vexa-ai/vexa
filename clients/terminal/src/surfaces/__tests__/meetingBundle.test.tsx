import { describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach } from "vitest";
import { BundleError, bundleFilename, confirmImport, downloadBundle, previewImport, restoreParts, MAX_BUNDLE_BYTES } from "../meetingBundle";
import { ImportMeetingButton, exportable } from "../MeetingBundleActions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const PREVIEW = {
  bundle_id: "b", exported_at: "2026-10-10T12:00:00Z", source: { deployment_id: "d", meeting_id: 4 },
  meeting: { platform: "jitsi", native_meeting_id: "r", title: "<img src=x onerror=alert(1)>", status: "completed",
             start_time: "2026-10-01T14:00:00Z", end_time: null, participants: ["Ada"] },
  segments: 3, speakers: ["Ada", "Grace"], media: [], annotations: { metadata_keys: ["ticket"], notes: true },
  handoff: { workspace_files: 2, notes_page: true },
  duplicate_of: null,
};

describe("meeting bundle client", () => {
  it("reads the server's file name, and never a path", () => {
    expect(bundleFilename('attachment; filename="sync.meeting-bundle.zip"', "1")).toBe("sync.meeting-bundle.zip");
    expect(bundleFilename('attachment; filename="../../evil.zip"', "1")).toBe("....evil.zip");
    expect(bundleFilename(null, "7")).toBe("meeting-7.meeting-bundle.zip");
  });

  it("reports download progress against Content-Length", async () => {
    const body = new Uint8Array(100);
    const fetcher = vi.fn(async (url: string) => url.includes("bundle-parts")
      ? new Response(null, { status: 204 })
      : new Response(body, { headers: { "content-length": "100", "content-disposition": 'attachment; filename="m.zip"' } }));
    const seen: (number | null)[] = [];
    const { blob, filename, parts } = await downloadBundle("5", (f) => seen.push(f), fetcher as unknown as typeof fetch);
    expect(blob.size).toBe(100);
    expect(filename).toBe("m.zip");
    expect(seen.at(-1)).toBe(1);
    expect(parts).toEqual({ state: "none" });
  });

  it("hands the agent domain's parts to the export when the meeting has them", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const parts = new Uint8Array([80, 75, 3, 4]);
    const fetcher = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return url.includes("bundle-parts")
        ? new Response(parts, { headers: { "x-vexa-workspace-files": "2", "x-vexa-notes-page": "1", "x-vexa-skipped-files": "1" } })
        : new Response(new Uint8Array(10));
    });
    const out = await downloadBundle("5", () => {}, fetcher as unknown as typeof fetch);
    expect(calls.map(([u, i]) => `${i?.method ?? "GET"} ${u}`)).toEqual([
      "GET /api/meeting/bundle-parts?meeting_id=5", "POST /api/meetings/5/export"]);
    expect(out.parts).toEqual({ state: "included", workspaceFiles: 2, notesPage: true, skippedFiles: 1 });
  });

  it("exports without the parts when the agent domain refuses, and says so", async () => {
    const fetcher = vi.fn(async (url: string) => url.includes("bundle-parts")
      ? new Response(JSON.stringify({ detail: "agent endpoints are disabled in meetings mode" }), { status: 404 })
      : new Response(new Uint8Array(10)));
    const out = await downloadBundle("5", () => {}, fetcher as unknown as typeof fetch);
    expect(out.parts.state).toBe("unavailable");
  });

  it("restores the bundle's workspace and page against the new meeting", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ meeting_id: 12, workspace: { slug: "imp-1", files: 2 }, notes_page: null }), { status: 201 }));
    const out = await restoreParts(new Blob(["x"]), 12, fetcher as unknown as typeof fetch);
    expect(out.workspace?.slug).toBe("imp-1");
    expect((fetcher.mock.calls[0] as unknown as [string])[0]).toBe("/api/meeting/bundle-restore?meeting_id=12");
  });

  it("surfaces a refusal's contract code, not a generic failure", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ detail: { code: "duplicate_import", detail: "already imported as meeting 3" } }), { status: 409 }));
    await expect(confirmImport(new Blob(["x"]), fetcher as unknown as typeof fetch)).rejects.toMatchObject({ code: "duplicate_import", status: 409 });
  });

  it("refuses an oversize file before uploading it", async () => {
    const fetcher = vi.fn();
    const huge = { size: MAX_BUNDLE_BYTES + 1 } as Blob;
    await expect(previewImport(huge, fetcher as unknown as typeof fetch)).rejects.toBeInstanceOf(BundleError);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("offers export only on an ended meeting the caller owns", () => {
    expect(exportable({ shared: false, status: "past", live_status: "completed" })).toBe(true);
    expect(exportable({ shared: true, status: "past", live_status: "completed" })).toBe(false);
    expect(exportable({ shared: false, status: "live", live_status: "active" })).toBe(false);
    expect(exportable({ shared: false, status: "past", live_status: "scheduled" })).toBe(false);
  });
});

describe("Import meeting dialog", () => {
  it("previews, shows bundle text as text, names what is skipped, and imports only on confirm", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      calls.push(url);
      if (url.includes("bundle-restore")) {
        return new Response(JSON.stringify({ meeting_id: 12, workspace: { slug: "release-sync-imported", files: 2 },
                                             notes_page: { path: "kg/entities/meeting/x.md", written: true } }), { status: 201 });
      }
      return url.includes("dry_run")
        ? new Response(JSON.stringify(PREVIEW), { status: 200 })
        : new Response(JSON.stringify({ ...PREVIEW, imported: true, meeting_id: 12 }), { status: 201 });
    }));
    render(<ImportMeetingButton />);
    fireEvent.click(screen.getByRole("button", { name: "Import meeting" }));
    const input = screen.getByLabelText("Meeting bundle file");
    fireEvent.change(input, { target: { files: [new File([new Uint8Array([1])], "m.meeting-bundle.zip")] } });
    await waitFor(() => expect(screen.getByText(/3 transcript segments/)).toBeTruthy());
    expect(calls).toEqual(["/api/meetings/import?dry_run=true"]);
    // The title is shown literally — markup in a bundle never becomes an element.
    expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText(/Workspace: 2 files, as a new workspace of yours/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(screen.getByText(/Imported as meeting 12/)).toBeTruthy());
    // (the meetings list refresh that follows is the only other call)
    expect(calls.filter(u => u.includes("/import") || u.includes("bundle-restore"))).toEqual([
      "/api/meetings/import?dry_run=true", "/api/meetings/import", "/api/meeting/bundle-restore?meeting_id=12"]);
    await waitFor(() => expect(screen.getByText(/Workspace restored: 2 files/)).toBeTruthy());
  });

  it("will not import a file it has already imported", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...PREVIEW, duplicate_of: 3 }), { status: 200 })));
    render(<ImportMeetingButton />);
    fireEvent.click(screen.getByRole("button", { name: "Import meeting" }));
    fireEvent.change(screen.getByLabelText("Meeting bundle file"), { target: { files: [new File(["x"], "m.zip")] } });
    await waitFor(() => expect(screen.getByText(/already imported this file as meeting 3/)).toBeTruthy());
    expect((screen.getByRole("button", { name: "Import" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("upload progress", () => {
  it("reports the phase's progress through to the end with an injected transport", async () => {
    const seen: number[] = [];
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ...PREVIEW }), { status: 200 }));
    await previewImport(new Blob(["x"]), fetcher as unknown as typeof fetch, (f) => seen.push(f));
    expect(seen.at(-1)).toBe(1);
  });
});
