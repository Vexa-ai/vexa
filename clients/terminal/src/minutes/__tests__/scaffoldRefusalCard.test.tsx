/** F48 — a refused link says WHICH account it was refused for, and offers the way out.
 *
 *  Before this the card read "This link isn't open to you." and stopped. The reader is looking at a
 *  signed-in app that will not tell them who it thinks they are, so the one thing they need in order
 *  to act — which of their addresses is in this browser — was the one thing missing, and the fix
 *  (sign out, sign back in as the other person) was behind a menu at the foot of a rail they may
 *  have collapsed.
 */
import { describe, expect, it, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ScaffoldRefusalCard } from "../ScaffoldRefusalCard";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("the refusal card names the signed-in address", () => {
  it("states who the server judged the link against", () => {
    render(<ScaffoldRefusalCard refusal={{ reason: "not-found", status: 404, detail: "" }}
      signedInAs="dmitry@vexa.ai" onDismiss={() => {}} />);
    expect(screen.getByRole("alert").textContent).toContain("You are signed in as dmitry@vexa.ai");
  });

  it("a 403 — where 'not yours' IS the verdict — says so flatly", () => {
    render(<ScaffoldRefusalCard refusal={{ reason: "forbidden", status: 403, detail: "" }}
      signedInAs="dmitry@vexa.ai" onDismiss={() => {}} />);
    expect(screen.getByRole("alert").textContent)
      .toContain("You are signed in as dmitry@vexa.ai; this link was sent to another address.");
  });

  it("degrades to the old copy when the identity probe could not answer — never 'signed in as null'", () => {
    render(<ScaffoldRefusalCard refusal={{ reason: "forbidden", status: 403, detail: "" }}
      signedInAs={null} onDismiss={() => {}} />);
    const text = screen.getByRole("alert").textContent ?? "";
    expect(text).toContain("You are signed in as a different person.");
    expect(text).not.toMatch(/null|undefined/);
  });
});

describe("the refusal card offers a way out", () => {
  it("switching account signs out, so the reader lands on the sign-in screen", () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    render(<ScaffoldRefusalCard refusal={{ reason: "not-found", status: 404, detail: "" }}
      signedInAs="dmitry@vexa.ai" onDismiss={() => {}} />);
    fireEvent.click(screen.getByText("Switch account"));
    expect(fetchMock).toHaveBeenCalledWith("/api/auth/logout", { method: "POST" });
  });

  it("offers it on the identity refusals and NOT on the ones an account switch cannot fix", () => {
    const { container, rerender } = render(
      <ScaffoldRefusalCard refusal={{ reason: "not-found", status: 404, detail: "" }} onDismiss={() => {}} />);
    expect(container.querySelector('[data-refusal="switch"]')).toBeTruthy();
    rerender(<ScaffoldRefusalCard refusal={{ reason: "unavailable", status: 502, detail: "" }} onDismiss={() => {}} />);
    expect(container.querySelector('[data-refusal="switch"]')).toBeNull();
    // dismissing is always available — the card must never be a wall
    expect(container.querySelector('[data-refusal="dismiss"]')).toBeTruthy();
  });

  it("dismiss stays the reader's own move", () => {
    const onDismiss = vi.fn();
    const { container } = render(
      <ScaffoldRefusalCard refusal={{ reason: "malformed", status: 200, detail: "" }} onDismiss={onDismiss} />);
    fireEvent.click(container.querySelector('[data-refusal="dismiss"]') as HTMLElement);
    expect(onDismiss).toHaveBeenCalled();
  });
});
