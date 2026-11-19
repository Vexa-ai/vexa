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
    const f = routeFetch({ "/api/asks/open": () => ({ status: 503, body: { detail: "Answers are temporarily unavailable" } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("Answers are temporarily unavailable."));
    expect(f.to("/api/asks/open")).toHaveLength(2);
    expect(sent).toEqual([]);
    expect(screen.getByRole("button", { name: "Save" })).toBeTruthy();   // still answerable
  });

  // hotfix 2026-10-18: a browser whose credential the server could not move onto a session is told to
  // sign in again — with a button that does it — never the generic "Couldn't send".
  it("a session refusal on the press shows 'Sign in again to answer' with a sign-in button, never 'Couldn't send'", async () => {
    const refused = { status: 403, body: { detail: { refused: "session_required", sign_in_again: true } } };
    const f = routeFetch({ "/api/asks/open": () => refused, "/api/auth/logout": () => ({ body: { ok: true } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Sign in again to answer/));
    expect(screen.getByRole("alert").textContent).not.toMatch(/Couldn't send/);
    expect(sent).toEqual([]);
    // the normal sign-out → sign-in: logout, then the page reloads onto the sign-in screen
    fireEvent.click(screen.getByRole("button", { name: "Sign in again" }));
    await waitFor(() => expect(f.to("/api/auth/logout")).toHaveLength(1));
  });

  it("a session refusal on the answer also asks to sign in again", async () => {
    routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ status: 403, body: { detail: { refused: "session_required" } } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/Sign in again to answer/));
    expect(sent).toEqual([]);
  });

  it("a failed answer (409 replay) shows a quiet error and sends nothing", async () => {
    routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ status: 409, body: { detail: "card_changed" } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/changed/));
    expect(sent).toEqual([]);
    expect(sent.some((s) => s.prompt.startsWith("[answer "))).toBe(false);
  });

  // founder 2026-10-18: the person can always answer in their own words — on any card, bound like a press
  it("a typed answer on a card without `free` goes as text alone, bound to the item", async () => {
    const f = routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ body: { status: "answered", message: "[answer save-notes #pend-7] build from this one" } }) });
    expect(SPEC.free).toBeUndefined();
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "build from this one" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(sent).toEqual([{ prompt: "[answer save-notes #pend-7] build from this one", display: "build from this one" }]));
    expect(f.to("/api/asks/pend-7/answer")[0].body).toEqual({ nonce: "n-7", content_hash: "h-7", text: "build from this one" });
  });

  it("a refused typed answer shows the server's reason and keeps the text in the field", async () => {
    const typed = "build from this one, add https://www.youtube.com/@peterdiamandis as a source";
    routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => ({ status: 422, body: { detail: "That answer is too long (4100 characters). Keep it under 4000." } }) });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: typed } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("That answer is too long (4100 characters). Keep it under 4000."));
    expect(screen.getByRole("alert").textContent).not.toMatch(/Couldn't send/);
    expect((screen.getByLabelText("Your answer") as HTMLInputElement).value).toBe(typed);
    expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
    expect(sent).toEqual([]);
  });

  it("a validation refusal (FastAPI's list) and a network failure are both put in plain words", async () => {
    let answer: () => { status: number; body: unknown } = () => ({ status: 422, body: { detail: [{ loc: ["body", "text"], msg: "String should have at most 16000 characters" }] } });
    routeFetch({ "/api/asks/open": () => ({ body: OPEN }), "/api/asks/pend-7/answer": () => answer() });
    render(<InlineCard spec={SPEC} session="s-9" />);
    await flush();
    fireEvent.change(screen.getByLabelText("Your answer"), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toBe("String should have at most 16000 characters."));
    answer = () => ({ status: 502, body: {} });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(screen.getByRole("alert").textContent).toMatch(/error 502/));
    expect((screen.getByLabelText("Your answer") as HTMLInputElement).value).toBe("hello");
    expect(sent).toEqual([]);
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
