/** Founder 2026-10-16: typing "confirm a8nv7c" to approve saving private details into a shared
 *  workspace is wrong UX. The held write renders as an approval card; a click is the person's own
 *  message from their own browser session (the chat route refuses an agent's — see
 *  core/agent/tests/test_private_writes_held.py::test_the_agent_cannot_confirm_its_own_write). */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../platform/events";
import { ApprovalCard, privateApproval } from "../approvalCard";

afterEach(cleanup);

const held = { refused: "private_sources_to_shared", workspace: "ait-lisbon", path: "plan.md",
  readers: ["ana@x.io", "bob@x.io"], sources: ["calendar", "gmail"], confirm_code: "a8nv7c" };

describe("privateApproval", () => {
  it("reads a held write from a tool result, as an object, a JSON string or under detail", () => {
    expect(privateApproval(held)?.code).toBe("a8nv7c");
    expect(privateApproval(JSON.stringify(held))?.code).toBe("a8nv7c");
    expect(privateApproval({ detail: held })?.workspace).toBe("ait-lisbon");
    expect(privateApproval({ ok: true })).toBeNull();
  });
});

describe("ApprovalCard", () => {
  it("shows what, where, who reads it and the private sources; Save sends the person's confirmation", () => {
    const seen: { prompt: string; display?: string }[] = [];
    window.addEventListener(ASK_CHAT_EVENT, (e) => seen.push((e as CustomEvent).detail));
    render(<ApprovalCard a={privateApproval(held)!} workspaceName="AI Tinkerers Lisbon" />);
    expect(screen.getByText(/plan\.md/)).toBeTruthy();
    expect(screen.getByText(/ana@x\.io/)).toBeTruthy();
    expect(screen.getByText(/calendar, gmail/)).toBeTruthy();
    expect(document.body.textContent).not.toMatch(/confirm a8nv7c/);
    fireEvent.click(screen.getByRole("button", { name: "Save to AI Tinkerers Lisbon" }));
    expect(seen.at(-1)).toMatchObject({ prompt: "confirm a8nv7c", display: "Save to AI Tinkerers Lisbon" });
    // answered once: the card stops offering; a fresh card can be kept private
    expect(screen.queryByRole("button", { name: "Keep private" })).toBeNull();
    cleanup();
    render(<ApprovalCard a={privateApproval(held)!} workspaceName="AI Tinkerers Lisbon" />);
    fireEvent.click(screen.getByRole("button", { name: "Keep private" }));
    expect(seen.at(-1)?.display).toBe("Keep private");
  });
});
