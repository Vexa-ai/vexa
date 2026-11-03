/** Synthetic cards and a routed fetch for the card-core tests. No real people or data. */
import { vi } from "vitest";
import type { CoreCard } from "../../../platform/desktop";

export const NOW = Date.parse("2026-10-17T12:00:00Z");

export function card(over: Partial<CoreCard> = {}): CoreCard {
  return {
    id: "c-1", ref: "card:c-1", type: "commitment", revision: 3, title: "Send the agenda", about: null,
    state: "offered", states: ["offered", "acknowledged", "done"], side_states: [], next: null, waiting: null,
    summary: "Send the agenda to the room by Thursday.", due: null, progress: null, workspaces: ["growth"], parent: null,
    children: { done: 0, total: 0, items: [] }, members: [], face: [], movement: null, stalled: false,
    can: { edit: false, transition: false, review: false, members: false }, href: "/c/c-1",
    kind: "commitment", next_actor: "p-me", requester: "p-ana", origin: "session:s-42", open: true,
    handoff: { phase: "offered", since: new Date(NOW - 8 * 3600_000).toISOString() },
    gates: [{ name: "Offered", state: "done", by: "p-ana", at: new Date(NOW - 8 * 3600_000).toISOString() }, { name: "Accepted", state: "current", by: null, at: null }, { name: "Done", state: "ahead", by: null, at: null }],
    viewer: { role: "next_actor", seen: false, actions: ["acknowledge", "decline"] },
    decisions: [], last_decision: null, chain: { blocks: [], blocked_by: [] },
    ...over,
  } as CoreCard;
}

export type Call = { url: string; body: Record<string, unknown> };
type Route = (body: Record<string, unknown>, n: number) => { status?: number; body: unknown };

/** a fetch that answers by URL suffix and records every call */
export function routeFetch(routes: Record<string, Route>): { calls: Call[]; to: (suffix: string) => Call[] } {
  const calls: Call[] = [];
  const count: Record<string, number> = {};
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    let body: Record<string, unknown> = {};
    try { body = init?.body ? JSON.parse(String(init.body)) : {}; } catch { body = {}; }
    calls.push({ url, body });
    const key = Object.keys(routes).find((k) => url.endsWith(k));
    if (!key) return new Response("{}", { status: 404 });
    count[key] = (count[key] ?? 0) + 1;
    const r = routes[key](body, count[key]);
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200, headers: { "Content-Type": "application/json" } });
  }));
  return { calls, to: (suffix) => calls.filter((c) => c.url.endsWith(suffix)) };
}

export const PEOPLE = { "/api/people/mentionable": () => ({ body: { people: [{ subject: "p-ana", name: "Ana" }, { subject: "p-val", name: "Valeriia" }] } }) };

/** let pending promises and React updates settle */
export async function flush(times = 8): Promise<void> {
  const { act } = await import("@testing-library/react");
  for (let i = 0; i < times; i++) await act(async () => { await Promise.resolve(); });
}
