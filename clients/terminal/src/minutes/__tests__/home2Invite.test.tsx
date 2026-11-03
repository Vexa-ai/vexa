/** JTBD-1 blocker 2: inviting someone who already has an account adds them to the workspace in-app
 *  (the membership path; they get a notice and see it on their home). Only addresses with no account
 *  get an email invite, drafted by the agent IN the chat as a real turn, not a composer prefill. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
vi.mock("../../ui-kit/primitives/Toast", () => ({ toast: () => undefined, Toaster: () => null }));
import { ASK_CHAT_EVENT } from "../../platform/events";
import { InviteDialog, inviteMembers } from "../desktop/home2/NewWorkspace";

type Call = { url: string; body: Record<string, unknown> };
function stubFetch(accounts: string[]): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) as Record<string, unknown> : {};
    calls.push({ url, body });
    if (url === "/api/workspace/members/add") {
      const ok = accounts.includes(String(body.person));
      return new Response(JSON.stringify(ok ? { added: true, email: body.person } : { detail: "no account" }), { status: ok ? 200 : 404 });
    }
    if (url === "/api/workspace/invite-link") return new Response(JSON.stringify({ url: "https://x/join/abc" }), { status: 201 });
    return new Response("{}", { status: 404 });
  }));
  return calls;
}
function asks(): CustomEvent[] {
  const seen: CustomEvent[] = [];
  window.addEventListener(ASK_CHAT_EVENT, (e) => seen.push(e as CustomEvent));
  return seen;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("invite (home2)", () => {
  it("adds people who have an account directly, and asks the agent to draft only for the rest", async () => {
    const calls = stubFetch(["b@acme.test"]);
    const seen = asks();
    const r = await inviteMembers("launch-1", "Launch plan", ["b@acme.test", "new@out.test"]);
    expect(r.added).toEqual(["b@acme.test"]);
    expect(r.outside).toEqual(["new@out.test"]);
    const adds = calls.filter((c) => c.url === "/api/workspace/members/add");
    expect(adds.map((c) => [c.body.workspace, c.body.person, c.body.role])).toEqual([
      ["launch-1", "b@acme.test", "write"], ["launch-1", "new@out.test", "write"]]);
    expect(seen).toHaveLength(1);
    const d = seen[0].detail as { prompt: string; mode?: string; display?: string };
    expect(d.mode).toBeUndefined();                       // a real turn: the draft appears in the chat
    expect(d.prompt).toContain("new@out.test");
    expect(d.prompt).not.toContain("b@acme.test");
    expect(d.display).toBeTruthy();
    expect(d.display).not.toContain("launch-1");          // no slug in what the person reads
  });
  it("with everyone already on Vexa, nothing is drafted", async () => {
    stubFetch(["b@acme.test"]);
    const seen = asks();
    render(<InviteDialog open slug="launch-1" name="Launch plan" onClose={() => {}} />);
    fireEvent.change(screen.getByLabelText("People to invite"), { target: { value: "b@acme.test" } });
    fireEvent.click(screen.getByRole("button", { name: "Invite" }));
    await waitFor(() => expect((fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(1));
    expect(seen).toHaveLength(0);
  });
});
