import { describe, expect, it } from "vitest";
import type { CreateBotRequest } from "@/types/vexa";
import {
  ZOOM_PENDING_REQUEST_TTL_SECONDS,
  decryptPendingZoomBotRequest,
  encryptPendingZoomBotRequest,
  normalizePendingZoomBotRequest,
} from "@/lib/zoom-pending-request";
import { pendingZoomBotRequestFrom } from "@/lib/zoom-oauth-client";

const SECRET = "state-secret-for-tests";
const NOW = 1_800_000_000;
const REQUEST: CreateBotRequest = {
  platform: "zoom",
  native_meeting_id: "89237402037",
  passcode: "123456",
  meeting_url: "https://us05web.zoom.us/j/89237402037?pwd=abc",
  bot_name: "Vexa",
  language: "en",
  transcribe_enabled: true,
};

describe("pending Zoom bot request carriage", () => {
  it("round-trips the request for the same user", () => {
    const sealed = encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW);
    expect(decryptPendingZoomBotRequest(sealed, "42", SECRET, NOW + 60)).toEqual(REQUEST);
  });

  it("does not contain the passcode or meeting URL in readable form", () => {
    const sealed = encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW);
    const decoded = Buffer.from(sealed, "base64url").toString("latin1");
    expect(sealed).not.toContain("123456");
    expect(decoded).not.toContain("123456");
    expect(decoded).not.toContain("pwd=abc");
  });

  it("uses a fresh IV for each encryption", () => {
    expect(encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW)).not.toBe(
      encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW)
    );
  });

  it("refuses another user's request", () => {
    const sealed = encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW);
    expect(decryptPendingZoomBotRequest(sealed, "43", SECRET, NOW)).toBeNull();
  });

  it("refuses an expired request", () => {
    const sealed = encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW);
    expect(
      decryptPendingZoomBotRequest(sealed, "42", SECRET, NOW + ZOOM_PENDING_REQUEST_TTL_SECONDS + 1)
    ).toBeNull();
  });

  it("refuses a value sealed with another secret, a tampered value, or garbage", () => {
    const sealed = encryptPendingZoomBotRequest(REQUEST, "42", SECRET, NOW);
    expect(decryptPendingZoomBotRequest(sealed, "42", "other-secret", NOW)).toBeNull();

    const raw = Buffer.from(sealed, "base64url");
    raw[raw.length - 1] ^= 0x01;
    expect(decryptPendingZoomBotRequest(raw.toString("base64url"), "42", SECRET, NOW)).toBeNull();

    expect(decryptPendingZoomBotRequest("not-a-sealed-value", "42", SECRET, NOW)).toBeNull();
    expect(decryptPendingZoomBotRequest(undefined, "42", SECRET, NOW)).toBeNull();
    expect(decryptPendingZoomBotRequest("", "42", SECRET, NOW)).toBeNull();
  });

  it("keeps only known fields with their expected types", () => {
    expect(
      normalizePendingZoomBotRequest({
        ...REQUEST,
        transcribe_enabled: "yes",
        extra: "dropped",
      })
    ).toEqual({ ...REQUEST, transcribe_enabled: undefined } as unknown as CreateBotRequest);
    expect(normalizePendingZoomBotRequest({ platform: "zoom" })).toBeNull();
    expect(normalizePendingZoomBotRequest(null)).toBeNull();
    expect(normalizePendingZoomBotRequest("zoom")).toBeNull();
  });

  it("reads the pending request from the OAuth complete response", () => {
    expect(pendingZoomBotRequestFrom({ success: true, pendingRequest: REQUEST })).toEqual(REQUEST);
    expect(pendingZoomBotRequestFrom({ success: true })).toBeNull();
    expect(pendingZoomBotRequestFrom({ pendingRequest: { platform: "zoom" } })).toBeNull();
    expect(pendingZoomBotRequestFrom(null)).toBeNull();
  });
});
