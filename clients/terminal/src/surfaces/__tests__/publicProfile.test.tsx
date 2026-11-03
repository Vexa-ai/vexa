/** Public profiles in the app (R4, `public_profiles`; approved mock flows 13–14). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { setFeatureOverride } from "../../platform/features";
import { DirectorySearch, LinkedProfile, PublicProfileSettings } from "../profile/PublicProfile";

type Call = { url: string; method: string; body: unknown };
function server(routes: Record<string, (body: Record<string, unknown>) => unknown>) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url, method, body });
    const key = url.startsWith("/api/crm") ? `crm ${body.operation}` : `${method} ${url.split("?")[0]}`;
    const h = routes[key];
    return h ? new Response(JSON.stringify(h(body)), { status: 200 }) : new Response("{}", { status: 404 });
  }));
  return calls;
}
const own = (published: boolean, linked_by = 3) => ({
  handle: "h-jonas-0123456789", published, linked_by, company_verification: null,
  fields: { name: { value: "Jonas Weber", public: true }, photo: { value: "", public: false }, role: { value: "Head of Workplace IT", public: true },
    company: { value: "NWB", public: true }, links: { value: [], public: false }, bio: { value: "Runs the Exchange team.", public: false } },
});
const pub = { handle: "h-jonas-0123456789", fields: { name: "Jonas Weber", role: "Head of Workplace IT", company: "NWB" }, company_verified: null };

beforeEach(() => setFeatureOverride("all"));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); setFeatureOverride("all"); });

describe("Settings · Public profile (13a)", () => {
  // ONE WRITER (PRD 0.13.4 D35): the editor reads and writes the claimed profile (/api/person/profile);
  // identity's retired profile answers only the company verification, and is never written.
  const claimed = (published: boolean) => ({ claimed: true, record_id: "r-jonas", published, handle: published ? "h-jonas-0123456789" : null,
    public_fields: ["display_name", "headline", "company"],
    fields: { display_name: "Jonas Weber", headline: "Head of Workplace IT", company: "NWB", bio: "Runs the Exchange team." } });

  it("shows each field with its own public switch, from the claimed profile", async () => {
    server({ "POST /api/person/profile": (b) => (b.action === "read" ? claimed(true) : {}), "GET /api/user/profile": () => own(true) });
    render(<PublicProfileSettings />);
    const bio = (await screen.findByText("Short bio")).closest("[data-field]")!.querySelector("input[type=checkbox]") as HTMLInputElement;
    expect(bio.checked).toBe(false);
    const role = document.querySelector('[data-field="role"] input[type=checkbox]') as HTMLInputElement;
    expect(role.checked).toBe(true);
  });

  it("publishes the edited fields to the claimed profile, and withdraws; identity's profile is never written", async () => {
    let published = false;
    const calls = server({
      "POST /api/person/profile": (b) => {
        if (b.action === "publish") published = !!b.public;
        return b.action === "read" ? claimed(published) : { status: "saved" };
      },
      "GET /api/user/profile": () => own(false, 0),
    });
    render(<PublicProfileSettings />);
    const bio = (await screen.findByText("Short bio")).closest("[data-field]")!.querySelector("input[type=checkbox]")!;
    fireEvent.click(bio);
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Publish changes" })); });
    const posts = calls.filter((c) => c.url === "/api/person/profile").map((c) => c.body as Record<string, unknown>);
    const edit = posts.find((b) => b.action === "edit")!;
    expect((edit.fields as Record<string, unknown>).bio).toBe("Runs the Exchange team.");
    const pub = posts.find((b) => b.action === "publish")!;
    expect(pub.public).toBe(true);
    expect(pub.public_fields).toContain("bio");
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "Withdraw profile" })); });
    expect(posts.length).toBeGreaterThan(0);
    expect(calls.some((c) => c.url === "/api/person/profile" && (c.body as Record<string, unknown>).public === false)).toBe(true);
    expect(calls.some((c) => c.method !== "GET" && c.url.startsWith("/api/user/profile"))).toBe(false);
    expect(screen.getByRole("status").textContent).toMatch(/Withdrawn/);
  });

  it("is not drawn and reads nothing with the feature off", async () => {
    const calls = server({ "GET /api/user/profile": () => own(true) });
    setFeatureOverride({ public_profiles: false });
    const { container } = render(<PublicProfileSettings />);
    await act(async () => {});
    expect(container.textContent).toBe("");
    expect(calls).toEqual([]);
  });
});

describe("a contact linked to a public profile (13b / 13c)", () => {
  it("shows the public fields live next to the org's own notes", async () => {
    server({ "crm profile/read": () => ({ record_id: "r1", linked: true, profile: pub, withdrawn: false }) });
    render(<LinkedProfile recordId="r1" ownNotes="Pilot champion; prefers short written updates" />);
    expect(await screen.findByText(/From Jonas's public profile/)).toBeTruthy();
    expect(screen.getByText("Head of Workplace IT")).toBeTruthy();
    expect(screen.getByText("Pilot champion; prefers short written updates")).toBeTruthy();
    expect(screen.getByText(/only your organisation/)).toBeTruthy();
  });

  it("after a withdrawal shows the org's own data only", async () => {
    server({ "crm profile/read": () => ({ record_id: "r1", linked: true, profile: null, withdrawn: true }) });
    render(<LinkedProfile recordId="r1" ownNotes="Security review owner is Lena" />);
    expect(await screen.findByText(/withdrew their public profile/)).toBeTruthy();
    expect(screen.queryByText(/public profile <|live/)).toBeNull();
    expect(screen.getByText("Security review owner is Lena")).toBeTruthy();
  });

  it("links one from the directory", async () => {
    let linked = false;
    const calls = server({
      "crm profile/read": () => ({ record_id: "r1", linked, profile: linked ? pub : null, withdrawn: false }),
      "crm profile/link": () => { linked = true; return { record_id: "r1", linked: true, handle: pub.handle }; },
      "GET /api/user/directory/profiles": () => ({ profiles: [pub] }),
    });
    render(<LinkedProfile recordId="r1" ownNotes="" />);
    fireEvent.click(await screen.findByRole("button", { name: "Link a public profile" }));
    fireEvent.change(screen.getByRole("textbox", { name: "Search the directory" }), { target: { value: "jonas" } });
    fireEvent.click(await screen.findByRole("button", { name: "Link" }, { timeout: 2000 }));
    expect(await screen.findByText(/From Jonas's public profile/)).toBeTruthy();
    expect(calls.find((c) => (c.body as { operation?: string }).operation === "profile/link")?.body)
      .toEqual({ operation: "profile/link", record_id: "r1", handle: pub.handle, linked: true });
  });
});

describe("the directory (14b)", () => {
  it("asks nothing under 3 letters", async () => {
    const calls = server({ "GET /api/user/directory/profiles": () => ({ profiles: [pub] }) });
    render(<DirectorySearch />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search the directory" }), { target: { value: "jo" } });
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(calls).toEqual([]);
  });
});

describe("the directory: open workspaces and people (14b)", () => {
  const ws = { slug: "harbor-ai", name: "Harbor AI Meetup", description: "Monthly AI builders' meetups in Porto", members: 4, pages: 26, joining: "request" };
  it("lists open workspaces above people, each opening its page", async () => {
    server({ "GET /api/open": () => ({ workspaces: [ws] }), "GET /api/user/directory/profiles": () => ({ profiles: [pub] }) });
    render(<DirectorySearch />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search the directory" }), { target: { value: "harbor" } });
    expect(await screen.findByText("Open workspaces", {}, { timeout: 2000 })).toBeTruthy();
    expect(screen.getByText(/Monthly AI builders' meetups in Porto · 26 pages · 4 members/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open" }).getAttribute("href")).toBe("/w/harbor-ai");
    const heads = [...document.querySelectorAll(".vx-dir-h")].map((h) => h.textContent);
    expect(heads).toEqual(["Open workspaces", "People"]);
  });
  it("asks for no workspaces with open_workspaces off, nor from a contact's Link search", async () => {
    const calls = server({ "GET /api/open": () => ({ workspaces: [ws] }), "GET /api/user/directory/profiles": () => ({ profiles: [pub] }) });
    setFeatureOverride({ open_workspaces: false });
    render(<DirectorySearch />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search the directory" }), { target: { value: "harbor" } });
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(calls.some((c) => c.url.startsWith("/api/open"))).toBe(false);
    cleanup(); calls.length = 0; setFeatureOverride("all");
    render(<DirectorySearch onPick={() => undefined} pickLabel="Link" />);
    fireEvent.change(screen.getByRole("textbox", { name: "Search the directory" }), { target: { value: "harbor" } });
    await act(async () => { await new Promise((r) => setTimeout(r, 400)); });
    expect(calls.some((c) => c.url.startsWith("/api/open"))).toBe(false);
  });
});
