/** A reply the meeting's signals woke, read back from the chat's history: the agent's message with a
 *  small "From the meeting" marker; the composed wake prompt is never drawn as the person's bubble,
 *  and no trigger or flow name is shown. */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: () => Promise.resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "1-0" }),
}));

import { Chat } from "../chat";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../platform/layout";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

afterEach(() => cleanup());

describe("a signal-woken reply in the meeting's chat", () => {
  it("is the agent's message, marked from the meeting, with no wake prompt bubble", async () => {
    globalThis.fetch = vi.fn(async (u: string) => ({ ok: true, status: 200, json: async () => (
      String(u).includes("/history") ? { turns: [
        { role: "user", text: "what is on the agenda?", user_text: "what is on the agenda?" },
        { role: "agent", text: "Install blockers and dates.", ops: [] },
        { role: "user", text: "[vexa-flow:meeting-signal:wake] Signals since your last look (score 1.3): …", user_text: "Meeting signal" },
        { role: "agent", text: "Noted: proxy change ships Friday.", ops: [] },
      ] } : { turns: [], sessions: [] }) })) as unknown as typeof fetch;
    const r = render(<ServicesProvider container={container()}><Chat params={{ session: "signal-wake-1" }} /></ServicesProvider>);
    await screen.findByText("Noted: proxy change ships Friday.");
    const marked = r.container.querySelectorAll("[data-from-meeting]");
    expect(marked).toHaveLength(1);
    expect(marked[0].textContent).toContain("From the call");
    expect(marked[0].textContent).toContain("Noted: proxy change ships Friday.");
    expect(r.container.textContent).not.toContain("Signals since your last look");
    expect(r.container.textContent).not.toContain("Meeting signal");
    expect(r.container.textContent).not.toContain("vexa-flow");
    expect(r.container.querySelectorAll('[data-turn="user"]')).toHaveLength(1);
  });

  // LIVE app.dev 2026-10-18: the server's history (before the fix) missed a wake whose mark sat after
  // the thread preamble, so the quiet reply came back as text with no trigger — and "From the call"
  // drew a literal "[vexa-quiet]". The marker never renders, whatever the server sent.
  it("never draws the quiet marker: a silent wake leaves no message, and a reply that spoke loses it", async () => {
    globalThis.fetch = vi.fn(async (u: string) => ({ ok: true, status: 200, json: async () => (
      String(u).includes("/history") ? { turns: [
        { role: "user", text: "This chat's thread id is `s-1`.\n## How you speak\n…\n[vexa-flow:meeting-signal:signal] Signals since your last look (score 0.00): …", user_text: "From the meeting" },
        { role: "agent", text: "[vexa-quiet]", ops: [] },
        { role: "user", text: "anything else?", user_text: "anything else?" },
        { role: "agent", text: "The proxy ships Friday. [VEXA-QUIET]", ops: [] },
      ] } : { turns: [], sessions: [] }) })) as unknown as typeof fetch;
    const r = render(<ServicesProvider container={container()}><Chat params={{ session: "signal-wake-quiet" }} /></ServicesProvider>);
    await screen.findByText("The proxy ships Friday.");
    expect(r.container.textContent?.toLowerCase()).not.toContain("vexa-quiet");
    expect(r.container.querySelectorAll("[data-from-meeting]")).toHaveLength(0);
    expect(r.container.querySelectorAll('[data-turn="agent"]')).toHaveLength(1);
  });
});
