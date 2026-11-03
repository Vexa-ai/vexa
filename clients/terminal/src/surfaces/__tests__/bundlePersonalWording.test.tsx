/** A restored meeting page lands in the person's PERSONAL workspace, and the import surfaces say so
 *  in those words (founder, 2026-10-11: "rename desk to personal to remove ambiguity"). The chat's
 *  bundle card and the Import meeting dialog never say "desk". */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { BundleImportCards, type BundleCard } from "../ChatBundleImport";
import { ImportMeetingButton } from "../MeetingBundleActions";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const imported = (written: boolean): BundleCard => ({
  id: "b1", key: "k", file: "m.zip", state: "imported", meetingId: 7, title: "Quarterly sync", segments: 3, recordings: 0,
  restored: { meeting_id: 7, workspace: null, notes_page: { path: "kg/entities/meeting/x.md", written } } as never,
  restoreError: null, restoring: false,
});

describe("the chat's bundle card", () => {
  it("says the page is in your personal workspace", () => {
    const { container } = render(<BundleImportCards cards={[imported(true)]} onDismiss={() => {}} />);
    expect(screen.getByText("Its page is in your personal workspace.")).toBeTruthy();
    expect(container.textContent).not.toMatch(/desk/i);
  });
  it("says an existing page in your personal workspace was kept", () => {
    const { container } = render(<BundleImportCards cards={[imported(false)]} onDismiss={() => {}} />);
    expect(screen.getByText("Your personal workspace already had its page; it was kept.")).toBeTruthy();
    expect(container.textContent).not.toMatch(/desk/i);
  });
});

const PREVIEW = {
  bundle_id: "b", exported_at: "2026-10-10T12:00:00Z", source: { deployment_id: "d", meeting_id: 4 },
  meeting: { platform: "jitsi", native_meeting_id: null, title: "Release sync", status: "completed", start_time: null, end_time: null, participants: [] },
  segments: 3, speakers: ["Ada"], media: [], annotations: { metadata_keys: [], notes: false },
  handoff: { workspace_files: 0, notes_page: true }, duplicate_of: null,
};

describe("the Import meeting dialog", () => {
  it("previews and reports the page in your personal workspace, never on a desk", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      if (url.includes("bundle-restore")) {
        return new Response(JSON.stringify({ meeting_id: 12, workspace: null, notes_page: { path: "kg/entities/meeting/x.md", written: true } }), { status: 201 });
      }
      return url.includes("dry_run")
        ? new Response(JSON.stringify(PREVIEW), { status: 200 })
        : new Response(JSON.stringify({ ...PREVIEW, imported: true, meeting_id: 12 }), { status: 201 });
    }));
    render(<ImportMeetingButton />);
    fireEvent.click(screen.getByRole("button", { name: "Import meeting" }));
    fireEvent.change(screen.getByLabelText("Meeting bundle file"), { target: { files: [new File([new Uint8Array([1])], "m.zip")] } });
    await waitFor(() => expect(screen.getByText("The meeting’s page, into your personal workspace")).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/desk/i);
    fireEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(screen.getByText("The meeting’s page is in your personal workspace.")).toBeTruthy());
    expect(document.body.textContent).not.toMatch(/desk/i);
  });
});
