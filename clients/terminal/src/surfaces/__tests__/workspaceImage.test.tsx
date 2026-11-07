/** "Change image…" (UI decision 15f): upload, use the company logo, reset — and the owner accepts or
 *  declines an image an agent proposed, in one click. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkspaceImageDialog, WorkspaceImageProposal, readImageState, setWorkspaceImage } from "../workspaceImage";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function mockFetch(state: Record<string, unknown>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (u, init) => {
    calls.push({ url: String(u), init: init as RequestInit });
    const body = String(u).endsWith("/identity/state") ? state : { set: true };
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  return calls;
}

describe("workspace image data", () => {
  it("reads the state and sets by PUT on the workspace's identity route", async () => {
    const calls = mockFetch({ source: null, proposal: null, owner: true, shared: false, domain: null });
    expect((await readImageState("garden")).source).toBeNull();
    await setWorkspaceImage("garden", { reset: true });
    expect(calls[1].url).toBe("/api/workspaces/garden/identity/icon");
    expect(calls[1].init?.method).toBe("PUT");
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({ reset: true });
  });
});

describe("WorkspaceImageDialog", () => {
  it("offers the company logo only when the workspace names one, and reset only when there is an image", async () => {
    mockFetch({ source: "workspace", proposal: null, owner: true, shared: false, domain: "studio.example.com" });
    render(<WorkspaceImageDialog slug="studio" name="Studio" open onClose={() => undefined} />);
    expect(await screen.findByRole("button", { name: /use studio\.example\.com's logo/i })).toBeTruthy();
    expect(screen.getByRole("button", { name: /reset/i })).toBeTruthy();
    expect(screen.getByLabelText(/upload an image/i)).toBeTruthy();
  });
  it("uses the logo with one click", async () => {
    const calls = mockFetch({ source: null, proposal: null, owner: true, shared: false, domain: "studio.example.com" });
    const done = vi.fn();
    render(<WorkspaceImageDialog slug="studio" name="Studio" open onClose={done} />);
    fireEvent.click(await screen.findByRole("button", { name: /use studio\.example\.com's logo/i }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(JSON.parse(String(put.init?.body))).toEqual({ use: "company_logo" });
  });
});

describe("WorkspaceImageProposal", () => {
  it("shows the owner an agent's proposal with accept and decline", async () => {
    const calls = mockFetch({ source: null, owner: true, shared: true, domain: null,
      proposal: { by: "u_bob", at: 1, source: "designed", kind: "svg" } });
    render(<WorkspaceImageProposal slug="studio" />);
    fireEvent.click(await screen.findByRole("button", { name: /accept/i }));
    await waitFor(() => expect(calls.some((c) => c.url.endsWith("/identity/proposal"))).toBe(true));
    const post = calls.find((c) => c.url.endsWith("/identity/proposal"))!;
    expect(JSON.parse(String(post.init?.body))).toEqual({ accept: true });
  });
  it("shows nothing to a member who is not the owner", async () => {
    mockFetch({ source: null, owner: false, shared: true, domain: null, proposal: { by: "u_bob", at: 1, source: "", kind: "svg" } });
    const { container } = render(<WorkspaceImageProposal slug="studio" />);
    await new Promise((r) => setTimeout(r, 20));
    expect(container.textContent).toBe("");
  });
});

describe("WorkspaceImageDialog · standard icons (founder 2026-10-18)", () => {
  it("offers a searchable grid of standard icons, the current one marked, and a pick is stored by PUT", async () => {
    const calls = mockFetch({ source: "glyph", glyph: "sprout", glyph_by: "auto", proposal: null, owner: true, shared: false, domain: null });
    const done = vi.fn();
    render(<WorkspaceImageDialog slug="garden" name="Garden club" open onClose={done} />);
    const now = await screen.findByRole("button", { name: "sprout" });
    await waitFor(() => expect(now.getAttribute("aria-pressed")).toBe("true"));
    fireEvent.change(screen.getByLabelText("Search icons"), { target: { value: "rocket" } });
    fireEvent.click(await screen.findByRole("button", { name: "rocket" }));
    await waitFor(() => expect(done).toHaveBeenCalled());
    const put = calls.find((c) => c.init?.method === "PUT");
    expect(put?.url).toBe("/api/workspaces/garden/identity/icon");
    expect(JSON.parse(String(put?.init?.body))).toEqual({ glyph: "rocket" });
  });
});
