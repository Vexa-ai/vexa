/** CP8 (docs/CONTROL-PLANE.md §4) — the cookbook op "agent on a meeting" composes the two PUBLISHED
 *  domain contracts (meetings POST /bots + agent POST /api/meeting/process) into one state-delivering
 *  call. Deterministic: a faked fetch in → exact calls + combined state out. Partial failure of the agent
 *  step is SURFACED in the state (the bot is already in), not swallowed; a hard failure of the meetings
 *  step throws (P18). */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { agentOnMeeting } from "../meetingCookbook";
import { ApiError } from "../apiClient";

let fetchMock: ReturnType<typeof vi.fn>;
const calls = () => fetchMock.mock.calls.map((c) => ({ url: String(c[0]), init: (c[1] ?? {}) as RequestInit }));
const bodyOf = (init: RequestInit) => JSON.parse(String(init.body));

const ok = (json: unknown) => ({ ok: true, status: 200, json: async () => json }) as unknown as Response;
const err = (status: number, detail: string) =>
  ({ ok: false, status, json: async () => ({ detail }) }) as unknown as Response;

beforeEach(() => {
  fetchMock = vi.fn();
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("agentOnMeeting — cookbook composition over two domain contracts", () => {
  it("sends the bot then enables the copilot, returning the combined state", async () => {
    fetchMock.mockResolvedValueOnce(ok({ status: "requested" })); // POST /api/bots (meetings)
    fetchMock.mockResolvedValueOnce(ok({ resumed_from: "0-0" })); // POST /api/meeting/process (agent)

    const state = await agentOnMeeting({ platform: "google_meet", native_id: "abc-defg-hij" });

    const [bot, proc] = calls();
    expect(bot.url).toBe("/api/bots");
    expect(bot.init.method).toBe("POST");
    expect(bodyOf(bot.init)).toMatchObject({
      platform: "google_meet", native_meeting_id: "abc-defg-hij",
      meeting_url: "https://meet.google.com/abc-defg-hij", bot_name: "Vexa",
    });
    expect(proc.url).toBe("/api/meeting/process");
    expect(bodyOf(proc.init)).toEqual({ native_id: "abc-defg-hij", platform: "google_meet", on: true });

    expect(state).toEqual({
      platform: "google_meet", native_id: "abc-defg-hij",
      bot: { sent: true, status: "requested" },
      copilot: { enabled: true, resumed_from: "0-0" },
    });
  });

  it("SURFACES a copilot-enable failure (bot is already in; not swallowed, not thrown)", async () => {
    fetchMock.mockResolvedValueOnce(ok({ status: "requested" }));   // bot OK
    fetchMock.mockResolvedValueOnce(err(502, "agent down"));         // process fails

    const state = await agentOnMeeting({ platform: "google_meet", native_id: "abc-defg-hij" });
    expect(state.bot).toEqual({ sent: true, status: "requested" });
    expect(state.copilot).toEqual({ enabled: false, error: "agent down" });
  });

  it("THROWS on a hard meetings-step failure (no bot ⇒ nothing to do); copilot is never called", async () => {
    fetchMock.mockResolvedValueOnce(err(422, "bad meeting"));        // bot send fails
    await expect(agentOnMeeting({ platform: "google_meet", native_id: "x" })).rejects.toBeInstanceOf(ApiError);
    expect(calls()).toHaveLength(1);                                 // process was never called
  });

  it("is deterministic: same input ⇒ identical call sequence", async () => {
    const run = async () => {
      fetchMock.mockResolvedValueOnce(ok({ status: "requested" }));
      fetchMock.mockResolvedValueOnce(ok({ resumed_from: "0-0" }));
      const s = await agentOnMeeting({ platform: "google_meet", native_id: "abc-defg-hij" });
      const seq = calls().map((c) => ({ url: c.url, body: bodyOf(c.init) }));
      fetchMock.mockClear();
      return { s, seq };
    };
    const a = await run();
    const b = await run();
    expect(JSON.stringify(a)).toBe(JSON.stringify(b));
  });
});
