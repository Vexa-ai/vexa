/** Settings → Profile: the person's own photo (UI 15g). "Change photo" picks a PNG/JPEG/WebP; "Remove
 *  photo" takes their upload away (the sign-in provider's photo or Gravatar shows again, else
 *  initials). Where the photo came from is said in a few words. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProfilePhoto } from "../ProfilePhoto";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function server(state: { photo: unknown }) {
  const calls: { url: string; method: string }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    calls.push({ url, method });
    if (url === "/api/auth/me") return new Response(JSON.stringify({ authenticated: true, user: { id: 12, name: "Ana Lopez", email: "ana@corp.example" } }));
    if (url === "/api/user/photo" && method === "DELETE") state.photo = { source: "provider", provider: "google", version: "p1" };
    if (url === "/api/user/photo" && method === "PUT") state.photo = { source: "upload", version: "u2" };
    return new Response(JSON.stringify({ subject: "12", photo: state.photo, sources: [] }));
  }));
  return calls;
}

describe("Settings → Profile photo", () => {
  it("an uploaded photo shows, with Change and Remove", async () => {
    server({ photo: { source: "upload", version: "u1" } });
    render(<ProfilePhoto />);
    expect(await screen.findByText("Your photo")).toBeTruthy();
    const img = document.querySelector("img") as HTMLImageElement;
    await waitFor(() => expect(img?.getAttribute("src")).toBe("/api/people/12/photo?v=u1"));
    expect(screen.getByRole("button", { name: /Change photo/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove photo" })).toBeTruthy();
  });

  it("removing the upload falls back to the provider's photo and says so", async () => {
    const calls = server({ photo: { source: "upload", version: "u1" } });
    render(<ProfilePhoto />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.url === "/api/user/photo")).toBe(true));
    expect(await screen.findByText(/From your Google account/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove photo" })).toBeNull();
  });

  it("no photo: initials, and only Change photo", async () => {
    server({ photo: null });
    render(<ProfilePhoto />);
    expect(await screen.findByText(/Initials until you add one/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Remove photo" })).toBeNull();
    expect(document.querySelector("img")).toBeNull();
  });
});
