/** 7g asks are real pending items (0.13.4 S5): the card opens its pending item when it first renders,
 *  a press answers it with the nonce + content hash, and only agent-api's returned message is sent as
 *  the person's chat message. If opening or answering fails, the card shows a quiet error and sends
 *  NOTHING — never the old unbound `[answer <id>] …` text. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ASK_CHAT_EVENT } from "../../../platform/events";
import { AskSessionProvider, InlineCard, parseInlineCard } from "../../../ui-kit/InlineCard";
import { flush, routeFetch } from "./fixtures";

const sent: { prompt: string; display?: string }[] = [];
const on = (e: Event) => sent.push((e as CustomEvent).detail);
window.addEventListener(ASK_CHAT_EVENT, on);
afterEach(() => { cleanup(); sent.length = 0; vi.unstubAllGlobals(); });

const SPEC = parseInlineCard({ id: "save-notes", question: "Save the notes to Growth?", primary: { label: "Save", reply: "yes" }, secondary: "Not now" })!;
const OPEN = { pending_id: "pend-7", nonce: "n-7", content_hash: "h-7", status: "open", spec: {} };

describe("InlineCard bound to a pending item", () => {
  it("opens on first render, answers with nonce + hash, then sends agent-api's message", async () => {
    const f = routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ body: { status: "answered", message: "Save (answer to save-notes · pend-7)" } }) });
    render(<AskSessionProvider value="s-9"><InlineCard spec={SPEC} /></AskSessionProvider>);
    await waitFor(() => expect(f.to("/api/asks/open").map((c) => c.body)).toEqual([{ session: "s-9", ask_id: "save-notes" }]));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent).toEqual([{ prompt: "Save (answer to save-notes · pend-7)", display: "Save" }]));
    expect(f.to("/api/asks/pend-7/answer")[0].body).toEqual({ nonce: "n-7", content_hash: "h-7", reply: { label: "Save", reply: "yes" } });
    expect(document.querySelector("[data-inline-card=save-notes]")!.hasAttribute("data-answered")).toBe(true);
  });

  it("an item already answered folds at once", async () => {
    routeFetch({ "/api/asks/open": () => ({ body: { ...OPEN, status: "answered", answer: { label: "Not now" } } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await waitFor(() => expect(document.querySelector("[data-answered]")!.textContent).toBe("Save the notes to Growth? → Not now"));
  });

  it("opens lazily on the press when the first open failed, and refuses to send unbound text when it fails again", async () => {
    const f = routeFetch({ "/api/asks/open": () => ({ status: 503, body: { detail: "down" } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Couldn't send your answer/));
    expect(f.to("/api/asks/open")).toHaveLength(2);
    expect(sent).toEqual([]);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();   // still answerable
  });

  it("a failed answer (409 replay) shows a quiet error and sends nothing", async () => {
    routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ status: 409, body: { detail: "replayed" } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/changed/));
    expect(sent).toEqual([]);
    expect(sent.some((s) => s.prompt.startsWith("[answer "))).toBe(false);
  });

  it("without a chat to bind to, nothing is sent", async () => {
    const f = routeFetch({});
    render(<InlineCard spec={SPEC} />);
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert")).toBeTruthy());
    expect(sent).toEqual([]);
    expect(f.calls).toHaveLength(0);
  });
});
