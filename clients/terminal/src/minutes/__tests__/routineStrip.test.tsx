/** THE ROUTINE STRIP IS ONE LINE (founder, 2026-10-14, live call: "total mess here"; the strip sat on
 *  top of the chat and its "Show flow" read `schedule 59 14 14 10 * → agent.run → background policy`).
 *  Collapsed: one line — "Routine · when · next <date> · how it went" — and a small expand. Expanded:
 *  When → Does → Results go to, permissions as questions with a way to allow, a refusal as a sentence
 *  with its fix, Pause/Retire. The engine view (the graph, raw facts) stays behind "Details". */
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, cleanup, fireEvent, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { RoutineBar } from "../RoutineBar";
import { ASK_CHAT_EVENT } from "../../platform/events";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const ENGINE = /agent\.run|background policy|\bbell\b|human_session_required|request_meeting_bot|\d+ \d+ \d+ \d+ \*|\d{3,} ?h\b/;
const NOW = Math.floor(Date.now() / 1000);
const local = new Date(); local.setHours(15, 59, 0, 0);
const oneShot = {
  name: "bot-at-1600", trigger: { kind: "at", cron: `${local.getUTCMinutes()} ${local.getUTCHours()} ${local.getUTCDate()} ${local.getUTCMonth() + 1} *` },
  lifecycle: "once", steps: ["agent_run"], status: "active", enabled: true, thread: "chat-9",
  plan_summary: "Send a bot to the 16:00 partner call and take notes.",
  policy: { key: "routine_background", allowed: ["read", "write_page", "notify"], refused: ["request_meeting_bot", "stop_bot"] },
  last_run: { status: "done", started_at: NOW - 300, duration_s: 2 },
  last_refusal: { reason: "human_session_required", text: "Couldn't send the bot: request_meeting_bot needs a person present (human_session_required)." },
  runs_today: 1, runs_per_day: 500, grants: [], may: "",
};
const serve = (card: object) => vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ routines: [card] }))));
const mount = () => render(<RoutineBar routine={{ flow: "bot-at-1600", scope: "user:1", version: 1 }} session="chat-9" />);

describe("collapsed: one line", () => {
  it("says routine · when · how it went, with a date and no engine words, and nothing else", async () => {
    serve(oneShot);
    const { container } = mount();
    await waitFor(() => expect(container.querySelector("[data-routine-line]")?.textContent).toMatch(/^Routine · once, /));
    const line = container.querySelector("[data-routine-line]")!.textContent!;
    expect(line).toMatch(/needs permission$/);
    expect(line).not.toMatch(ENGINE);
    expect(container.textContent).not.toMatch(/in \d+ h/);
    expect(container.querySelector("[data-routine-flow]")).toBeNull();
    expect(container.querySelector("[data-flow-graph]")).toBeNull();
    const buttons = Array.from(container.querySelectorAll("button"));
    expect(buttons).toHaveLength(1);                                     // the expand, nothing else
    expect(buttons[0].getAttribute("aria-expanded")).toBe("false");
  });
});

describe("expanded: the flow in human terms", () => {
  it("When → Does → Results go to, permissions as questions, the refusal with its fix", async () => {
    serve(oneShot);
    const { container } = mount();
    await waitFor(() => expect(container.querySelector("[data-routine-line]")?.textContent).toMatch(/needs permission/));
    fireEvent.click(container.querySelector("[data-routine-expand]")!);
    const flow = container.querySelector("[data-routine-flow]")!;
    expect(flow).not.toBeNull();
    const text = flow.textContent!;
    expect(text).toMatch(/When:\s*once, /);
    expect(text).toContain("Does: Send a bot to the 16:00 partner call and take notes.");
    expect(text).toContain("Results go to: this chat");
    expect(flow.querySelector('[data-routine-perm="bot.request"]')!.textContent).toMatch(/^May send bots: no/);
    expect(flow.querySelector("[data-routine-notice]")!.textContent).toMatch(/^It couldn't send the bot: nobody was here to approve it\./);
    expect(text).not.toMatch(ENGINE);
    expect(Array.from(flow.querySelectorAll("button")).map((b) => b.textContent)).toEqual(
      expect.arrayContaining(["Pause", "Retire", "Details"]));
    expect(container.querySelector("[data-flow-graph]")).toBeNull();      // the engine view waits
  });

  it("Allow asks in the routine's chat — the agent records the permission, nothing is granted here", async () => {
    serve(oneShot);
    const asks: CustomEvent[] = [];
    const on = (e: Event) => asks.push(e as CustomEvent);
    window.addEventListener(ASK_CHAT_EVENT, on);
    const { container } = mount();
    await waitFor(() => expect(container.querySelector("[data-routine-line]")?.textContent).toMatch(/needs permission/));
    fireEvent.click(container.querySelector("[data-routine-expand]")!);
    fireEvent.click(container.querySelector('[data-routine-perm="bot.request"] button')!);
    window.removeEventListener(ASK_CHAT_EVENT, on);
    expect(asks).toHaveLength(1);
    expect(asks[0].detail).toMatchObject({ session: "chat-9", prompt: expect.stringContaining("bot-at-1600") });
    expect(asks[0].detail.prompt).toMatch(/send bots to meetings/);
    expect(asks[0].detail.hidden).toBeFalsy();
  });

  it("Details shows the operator view: the graph and the raw facts", async () => {
    serve({ ...oneShot, may: "This routine may: retire itself." });
    const { container } = mount();
    await waitFor(() => expect(container.querySelector("[data-routine-line]")?.textContent).toMatch(/needs permission/));
    fireEvent.click(container.querySelector("[data-routine-expand]")!);
    fireEvent.click(container.querySelector("[data-routine-details]")!);
    expect(container.querySelector("[data-flow-graph]")).not.toBeNull();
    expect(container.querySelector("[data-routine-raw]")!.textContent).toContain("1/500 runs today");
    expect(container.querySelector("[data-routine-may]")!.textContent).toBe("This routine may: retire itself.");
  });
});

describe("it never covers the chat", () => {
  const CSS = readFileSync(join(__dirname, "..", "minutes.css"), "utf8");
  const rule = (sel: string) => {
    const m = CSS.match(new RegExp(`${sel.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`));
    expect(m, `${sel} has a rule`).not.toBeNull();
    return m![1];
  };
  it("the line is one line: it truncates, it does not wrap", () => {
    const l = rule(".vx-routine-line-text");
    expect(l).toMatch(/white-space:\s*nowrap/);
    expect(l).toMatch(/text-overflow:\s*ellipsis/);
    expect(l).toMatch(/overflow:\s*hidden/);
  });
  it("the expanded flow is bounded and scrolls inside itself", () => {
    const f = rule(".vx-routine-flow");
    expect(f).toMatch(/max-height:/);
    expect(f).toMatch(/overflow-y:\s*auto/);
  });
});
