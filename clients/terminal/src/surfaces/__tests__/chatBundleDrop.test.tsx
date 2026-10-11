/** A MEETING BUNDLE DROPPED INTO A CHAT IMPORTS A CALL (founder, 2026-10-11: *"they would just drop
 *  in chat and it would understand we import a call"*).
 *
 *  Mounted whole, like `chatFaults`: the defect would live in the wiring — a dropped file has to
 *  reach the import route and NOT the attachment upload, and the outcome has to come back as a card.
 *  The files are the contract's own goldens (`deploy/contracts/meeting-bundle.v1/golden`), so the
 *  detection is held to the format, not to a fixture this test made up.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: () => new Promise(() => {}),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, OPEN_MEETING_EVENT, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";
import { sniffMeetingBundle } from "../meetingBundleSniff";

const GOLDEN = join(__dirname, "../../../../../deploy/contracts/meeting-bundle.v1/golden");
const golden = (rel: string, name = rel.split("/").pop()!) =>
  new File([readFileSync(join(GOLDEN, rel))], name, { type: "application/zip", lastModified: 1 });

/** meeting-api's import route, faked: `answer` decides what the next real import says. */
const server = vi.hoisted(() => ({
  answer: null as null | { status: number; body: unknown },
  dryRun: null as null | Record<string, unknown>,
  hold: null as null | Promise<void>,
}));

const IMPORTED = (id: number, title: string) => ({
  imported: true, meeting_id: id, bundle_id: "b", exported_at: "2026-10-10T12:00:00Z",
  source: { deployment_id: "d", meeting_id: 4 },
  meeting: { platform: "jitsi", native_meeting_id: null, title, status: "completed", start_time: null, end_time: null, participants: [] },
  segments: 3, speakers: ["Ada"], media: [], annotations: { metadata_keys: [], notes: false },
  handoff: { workspace_files: 0, notes_page: false }, duplicate_of: null,
});

const calls = () => (globalThis.fetch as unknown as { mock: { calls: [unknown, RequestInit?][] } }).mock.calls
  .map(([u, i]) => `${i?.method ?? "GET"} ${String(u)}`);
const imports = () => calls().filter((c) => c === "POST /api/meetings/import");

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

let seq = 0;
function mountChat() {
  seq += 1;
  render(<ServicesProvider container={container()}><Chat params={{ session: `bundle-drop-${seq}` }} /></ServicesProvider>);
}

async function drop(...files: File[]) {
  const box = screen.getByRole("textbox");
  await act(async () => { fireEvent.drop(box, { dataTransfer: { files, types: ["Files"] } }); });
}

beforeEach(() => {
  server.answer = null; server.dryRun = null; server.hold = null;
  try { localStorage.clear(); } catch { /* jsdom */ }
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u === "/api/meetings/import") {
      if (server.hold) await server.hold;
      const a = server.answer ?? { status: 201, body: IMPORTED(42, "Quarterly sync") };
      return new Response(JSON.stringify(a.body), { status: a.status, headers: { "content-type": "application/json" } });
    }
    if (u === "/api/meetings/import?dry_run=true") {
      return new Response(JSON.stringify(server.dryRun ?? {}), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (u.startsWith("/api/workspace/upload")) {
      return new Response(JSON.stringify({ files: [{ name: "notes.txt", path: "notes.txt" }] }), { status: 200 });
    }
    return new Response(JSON.stringify({ turns: [], sessions: [], pending: [], meetings: [] }), { status: 200 });
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("which dropped file is a meeting bundle — the contract's rules, never the name", () => {
  it("every golden bundle, and every refused golden that carries a manifest, claims to be one", async () => {
    for (const f of ["bundles/transcript-only.zip", "bundles/with-audio.zip", "bundles/with-workspace-and-notes.zip",
                     "refused/hash-mismatch.zip", "refused/unknown-version.zip", "refused/zip-slip.zip", "refused/fake-media.zip"]) {
      expect(await sniffMeetingBundle(golden(f)), f).toBe("bundle");
    }
  });

  it("a zip without a manifest, a parts archive, or not a zip at all, is a file — whatever it is called", async () => {
    for (const f of ["refused/missing-manifest.zip", "refused/not-a-zip.zip", "parts/workspace-and-notes.zip"]) {
      expect(await sniffMeetingBundle(golden(f, "call.meeting-bundle.zip")), f).toBe("not_bundle");
    }
    expect(await sniffMeetingBundle(new File(["hello"], "x.meeting-bundle.zip"))).toBe("not_bundle");
  });

  it("a bundle renamed to anything is still a bundle", async () => {
    expect(await sniffMeetingBundle(golden("bundles/transcript-only.zip", "holiday-photos.bin"))).toBe("bundle");
  });
});

describe("a bundle dropped into a chat", () => {
  it("goes to the import route, not the attachment upload, and the card links the new call", async () => {
    mountChat();
    await drop(golden("bundles/transcript-only.zip"));
    const card = await screen.findByText("Quarterly sync");
    expect(imports()).toHaveLength(1);
    expect(calls().some((c) => c.includes("/api/workspace/upload"))).toBe(false);
    expect(screen.queryByLabelText(/^Remove /)).toBeNull();   // not attached
    expect(document.querySelector('[data-bundle-card="imported"]')?.textContent).toContain("Imported the call");

    const opened = vi.fn();
    window.addEventListener(OPEN_MEETING_EVENT, opened);
    fireEvent.click(card);
    window.removeEventListener(OPEN_MEETING_EVENT, opened);
    expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({ ref: "42" });
  });

  it("a file that is not a bundle is attached as before and never imported", async () => {
    mountChat();
    await drop(new File(["plain text"], "notes.txt", { type: "text/plain" }), golden("refused/missing-manifest.zip"));
    await screen.findByLabelText("Remove notes.txt");
    await screen.findByLabelText("Remove missing-manifest.zip");
    expect(imports()).toHaveLength(0);
  });

  it("a mixed drop imports the bundle and attaches the rest", async () => {
    mountChat();
    await drop(golden("bundles/transcript-only.zip"), new File(["plain text"], "notes.txt", { type: "text/plain" }));
    await screen.findByText("Quarterly sync");
    await screen.findByLabelText("Remove notes.txt");
    expect(imports()).toHaveLength(1);
  });

  it("a bundle the importer refuses says why, with its code — never attached", async () => {
    server.answer = { status: 422, body: { detail: { code: "hash_mismatch", detail: "transcript.json: length or SHA-256 differs from the manifest" } } };
    mountChat();
    await drop(golden("refused/hash-mismatch.zip"));
    const card = await screen.findByRole("alert");
    expect(card.getAttribute("data-bundle-card")).toBe("refused");
    expect(card.textContent).toContain("Not imported: the file does not match its signature");
    expect(card.textContent).toContain("hash_mismatch");
    expect(card.textContent).toContain("SHA-256 differs");
    expect(screen.queryByLabelText(/^Remove /)).toBeNull();
  });

  it("a bundle already imported says so and links the call it already is", async () => {
    server.answer = { status: 409, body: { detail: { code: "duplicate_import", detail: "this bundle was already imported as meeting 7; delete that meeting first to import it again" } } };
    server.dryRun = { ...IMPORTED(0, "Quarterly sync"), duplicate_of: 7, dry_run: true };
    mountChat();
    await drop(golden("bundles/transcript-only.zip"));
    await screen.findByText("You already imported this call");
    const opened = vi.fn();
    window.addEventListener(OPEN_MEETING_EVENT, opened);
    fireEvent.click(screen.getByText("Quarterly sync"));
    window.removeEventListener(OPEN_MEETING_EVENT, opened);
    expect((opened.mock.calls[0][0] as CustomEvent).detail).toEqual({ ref: "7" });
    expect(imports()).toHaveLength(1);
  });

  it("the same file dropped again while it is still going up is sent once", async () => {
    let release!: () => void;
    server.hold = new Promise<void>((r) => { release = r; });
    mountChat();
    const f = golden("bundles/transcript-only.zip");
    await drop(f);
    await screen.findByText(/Importing a call from/);
    await drop(f);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(imports()).toHaveLength(1);
    expect(document.querySelectorAll('[data-bundle-card]').length).toBe(1);
    await act(async () => { release(); });
    await screen.findByText("Quarterly sync");
    await waitFor(() => expect(imports()).toHaveLength(1));
  });
});
