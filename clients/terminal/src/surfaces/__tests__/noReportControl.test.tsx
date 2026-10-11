/** NO "REPORT THIS" CONTROL IN THE TERMINAL (founder 2026-10-11).
 *
 *  Founder, on screenshots of the one-line "What did not work?" field and its warning-triangle
 *  button: *"remove - they report friction via chat"*. A person tells the chat agent what did not
 *  work, and the agent files it with `report_friction`. These guard that neither placement comes
 *  back: an agent turn (hover or right-click) and the page header.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, waitFor, act, fireEvent } from "@testing-library/react";

const stream = vi.hoisted(() => ({
  calls: [] as { cb: Record<string, ((...a: never[]) => void) | undefined>; finish: () => void }[],
}));

vi.mock("../chatStream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../chatStream")>()),
  streamChatTurn: (_req: unknown, cb: unknown) =>
    new Promise((resolve) => {
      stream.calls.push({
        cb: cb as never,
        finish: () => resolve({ sawVisibleOutput: true, terminal: true, aborted: false, cursor: "42-0" }),
      });
    }),
}));

import { Chat } from "../chat";
import { PagesPanel } from "../../minutes/PagesPanel";
import { ServicesProvider, createContainer, reg, CommandServiceId, type CommandService, ASK_CHAT_EVENT } from "../../platform";
import { LayoutServiceId, createLayoutService } from "../../workbench/layout";

const container = () => createContainer([
  reg(LayoutServiceId, () => createLayoutService("files")),
  reg(CommandServiceId, () => ({ querySkills: () => [], execute: () => {} }) as unknown as CommandService),
]);

/** Anything the removed control drew: its button, its field, its menu item. */
function expectNoReportControl(root: ParentNode) {
  expect(root.querySelector("[data-report], [data-report-turn], [data-doc-act=\"report\"]")).toBeNull();
  expect(screen.queryByLabelText(/report this/i)).toBeNull();
  expect(screen.queryByPlaceholderText(/what did not work/i)).toBeNull();
  expect(screen.queryByText(/report this/i)).toBeNull();
}

beforeEach(() => {
  stream.calls.length = 0;
  try { localStorage.clear(); } catch { /* jsdom always has one */ }
  globalThis.fetch = vi.fn(async (url: unknown) => {
    const u = String(url);
    if (u.startsWith("/api/chat/submit")) {
      return { ok: true, status: 200, json: async () => ({ ok: true, pending: [], cursor: "9-0" }) };
    }
    if (u.startsWith("/api/chat/pending")) {
      return { ok: true, status: 200, json: async () => ({ pending: [], cursor: "9-0" }) };
    }
    return { ok: true, status: 200, json: async () => ({ turns: [], sessions: [] }) };
  }) as unknown as typeof fetch;
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("the terminal has no direct friction-report control", () => {
  it("an agent turn renders no report control, on hover or on right-click", async () => {
    const { container: root } = render(
      <ServicesProvider container={container()}>
        <Chat params={{ session: "no-report-turn" }} />
      </ServicesProvider>,
    );
    await act(async () => {
      window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { prompt: "open the PRD" } }));
    });
    await waitFor(() => expect(stream.calls.length).toBe(1));
    await act(async () => {
      stream.calls[0].cb.onTruncated?.("context-trimmed: 1 message(s) dropped" as never,
        "Here is the agent's answer" as never, undefined as never);
      stream.calls[0].finish();
    });
    const turn = await screen.findByText(/Here is the agent's answer/);

    fireEvent.mouseEnter(turn);
    fireEvent.contextMenu(turn);
    expectNoReportControl(root);
  });

  it("the page header renders no report control", () => {
    const { container: root } = render(
      <PagesPanel pages={[{ path: "drafts/prd.md", label: "prd" }]} docPath="drafts/prd.md"
        onOpen={() => {}} body={"# The PRD\n\nOne paragraph."} />,
    );
    // the header is there — its other utilities still render — and the report button is not
    expect(root.querySelector('[data-doc-act="copy"]')).toBeTruthy();
    expectNoReportControl(root);
  });
});
