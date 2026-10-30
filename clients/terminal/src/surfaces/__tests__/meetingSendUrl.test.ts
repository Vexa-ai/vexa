/** The address a bot-send carries, and what happens when a row has none (#1681).
 *
 *  `POST /bots` is refused 422 when the body has no `meeting_url` AND meeting-api cannot construct
 *  one — which for `zoom` and `jitsi` it never can (`bot_spawn/service.py` `_URL_TEMPLATES` /
 *  `construct_meeting_url`; the refusal is `bot_spawn/router.py`). The dashboard used to offer
 *  "Send now" on any row with a native id and fall back to a constructed URL for `google_meet`
 *  only, so a Zoom row with no stored link produced a request whose ONLY possible answer was that
 *  422 — with wording aimed at an API caller ("use google_meet/teams") that a dashboard user cannot
 *  act on. That was the shape reported from a self-hosted 0.12.27 install.
 *
 *  Two levels are asserted here: the pure resolver, and the row action that consumes it.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { platformSlug, requiresExplicitMeetingUrl, resolveSendAddress, meetingUrlField } from "../meetingSendUrl";
import { actionsFor } from "../meeting";
import type { MeetingMock } from "../meetingModel";

const MEET = "abc-defg-hij";
const ZOOM = "81234567890";
const ZOOM_URL = "https://us06web.zoom.us/j/81234567890?pwd=ABCdef123";

describe("platformSlug — the display name back to the API slug", () => {
  it("maps the one display-cased platform and passes the rest through", () => {
    expect(platformSlug("Google Meet")).toBe("google_meet");
    expect(platformSlug("zoom")).toBe("zoom");
    expect(platformSlug("teams")).toBe("teams");
    expect(platformSlug("jitsi")).toBe("jitsi");
  });
});

describe("requiresExplicitMeetingUrl — mirrors construct_meeting_url returning None", () => {
  it("zoom and jitsi need the caller to carry the URL", () => {
    expect(requiresExplicitMeetingUrl("zoom")).toBe(true);
    expect(requiresExplicitMeetingUrl("jitsi")).toBe(true);
  });
  it("meet and teams do not — the server has a template for both", () => {
    expect(requiresExplicitMeetingUrl("google_meet")).toBe(false);
    expect(requiresExplicitMeetingUrl("teams")).toBe(false);
  });
});

describe("resolveSendAddress", () => {
  it("the row's STORED link always wins, credentials and query string intact", () => {
    expect(resolveSendAddress("zoom", ZOOM, ZOOM_URL)).toEqual({ ok: true, meeting_url: ZOOM_URL });
    // never re-derived over, not even for a platform we could construct for
    expect(resolveSendAddress("google_meet", MEET, "https://meet.google.com/xyz?authuser=2"))
      .toEqual({ ok: true, meeting_url: "https://meet.google.com/xyz?authuser=2" });
  });

  it("google_meet with no stored link is still addressable — the code IS the address", () => {
    expect(resolveSendAddress("google_meet", MEET, undefined))
      .toEqual({ ok: true, meeting_url: `https://meet.google.com/${MEET}` });
  });

  it("teams with no stored link sends NO url — meeting-api builds it from the id shape and the deployment host", () => {
    expect(resolveSendAddress("teams", "397421056486982", undefined)).toEqual({ ok: true });
    expect(meetingUrlField(resolveSendAddress("teams", "397421056486982", undefined))).toEqual({});
  });

  it("zoom with no stored link is NOT addressable, and says why in words a user can act on", () => {
    const addr = resolveSendAddress("zoom", ZOOM, undefined);
    expect(addr.ok).toBe(false);
    if (addr.ok) throw new Error("unreachable");
    expect(addr.reason).toMatch(/Zoom/);
    expect(addr.reason).toMatch(/invite link/i);
    // NOT the API's operator-facing wording
    expect(addr.reason).not.toMatch(/google_meet|unsupported platform/);
  });

  it("jitsi rides the same contract — a room name is deployment-scoped, so the id is not an address", () => {
    expect(resolveSendAddress("jitsi", "vexa-standup", undefined).ok).toBe(false);
    expect(resolveSendAddress("jitsi", "vexa-standup", "https://meet.jit.si/vexa-standup"))
      .toEqual({ ok: true, meeting_url: "https://meet.jit.si/vexa-standup" });
  });

  it("a whitespace-only stored link counts as no link, not as an address", () => {
    expect(resolveSendAddress("zoom", ZOOM, "   ").ok).toBe(false);
  });

  it("a link-less row is unaddressable on every platform", () => {
    expect(resolveSendAddress("google_meet", undefined, undefined).ok).toBe(false);
  });
});

// ── The row action that consumes it ──────────────────────────────────────────────────────────────

function row(platform: string, native: string, meeting_url?: string): MeetingMock {
  return {
    id: "77",
    native_id: native,
    title: `${platform} · ${native}`,
    when: "now",
    status: "past",
    live_status: "idle",
    platform,
    meeting_url,
    docs: [],
    participants: [],
    mentioned: [],
    actions: [],
    transcript: [],
    insights: [],
  } as MeetingMock;
}

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({}) }) as Response);
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});
afterEach(() => vi.restoreAllMocks());

describe("actionsFor — Send now on a Zoom row", () => {
  it("carries the row's stored link (this is the case that already worked, and must keep working)", async () => {
    await actionsFor(row("zoom", ZOOM, ZOOM_URL)).find((a) => a.id === "send")!.run();
    // calls[0] is the POST; the action's `finally` then re-fetches the list.
    const call = fetchMock.mock.calls[0]!;
    expect(String(call[0])).toBe("/api/bots");
    expect(JSON.parse(String(call[1].body))).toEqual({
      platform: "zoom", native_meeting_id: ZOOM, meeting_url: ZOOM_URL,
    });
  });

  it("with NO stored link fires NO request — it refuses locally, naming the fix", async () => {
    const onFailure = vi.fn();
    await actionsFor(row("zoom", ZOOM)).find((a) => a.id === "send")!.run(onFailure);

    // The whole point: the 422 never happens, because the request never leaves.
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onFailure).toHaveBeenCalledTimes(1);
    const failure = onFailure.mock.calls[0][0];
    expect(failure).toMatchObject({ actionId: "send", actionLabel: "Send now", native: ZOOM });
    expect(failure.message).toMatch(/Zoom meeting has no stored link/);
  });

  it("Re-send on a completed Zoom row is the same action, and the same guard", async () => {
    const onFailure = vi.fn();
    const completed = { ...row("zoom", ZOOM), live_status: "completed" } as MeetingMock;
    await actionsFor(completed).find((a) => a.id === "resend")!.run(onFailure);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(onFailure).toHaveBeenCalledTimes(1);
  });

  it("a Meet row is untouched by the guard — the constructed URL still ships", async () => {
    await actionsFor(row("Google Meet", MEET)).find((a) => a.id === "send")!.run();
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1].body))).toEqual({
      platform: "google_meet", native_meeting_id: MEET, meeting_url: `https://meet.google.com/${MEET}`,
    });
  });

  it("a Teams row with no stored link still sends — the server constructs it", async () => {
    await actionsFor(row("teams", "397421056486982")).find((a) => a.id === "send")!.run();
    expect(JSON.parse(String(fetchMock.mock.calls[0]![1].body))).toEqual({
      platform: "teams", native_meeting_id: "397421056486982",
    });
  });
});
