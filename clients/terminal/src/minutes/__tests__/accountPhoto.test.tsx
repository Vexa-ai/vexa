import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/** The person's own face in the account badge (UI 15g): their photo route with its version, re-asked
 *  when the photo changes, and "Change photo…" in the account menu. */
import { AccountBadge } from "../AccountBadge";
import { PHOTO_CHANGED_EVENT } from "../../surfaces/personPhotoApi";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function serve() {
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const u = String(url);
    if (u.startsWith("/api/auth/me")) return new Response(JSON.stringify({ authenticated: true, user: { email: "jane@acme.example", name: "Jane Smith" } }), { status: 200 });
    if (u.startsWith("/api/user/photo")) return new Response(JSON.stringify({ subject: "7", photo: { source: "upload", version: "a1" }, sources: ["upload"] }), { status: 200 });
    return new Response("{}", { status: 404 });
  }));
}

describe("account badge — own photo", () => {
  it("shows the photo route with its version and follows a change", async () => {
    serve();
    const { container } = render(<AccountBadge />);
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("/api/people/7/photo?v=a1"));
    act(() => { window.dispatchEvent(new CustomEvent(PHOTO_CHANGED_EVENT, { detail: { subject: "7", photo: { source: "upload", version: "b2" }, sources: [] } })); });
    await waitFor(() => expect(container.querySelector("img")?.getAttribute("src")).toBe("/api/people/7/photo?v=b2"));
  });
  it("offers Change photo… in the menu", async () => {
    serve();
    const { container } = render(<AccountBadge />);
    await waitFor(() => expect(container.querySelector("img")).not.toBeNull());
    fireEvent.click(screen.getByRole("button", { name: /Jane Smith/ }));
    expect(screen.getByRole("menuitem", { name: "Change photo…" })).toBeTruthy();
  });
});
