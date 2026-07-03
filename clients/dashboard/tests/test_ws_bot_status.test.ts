import { describe, it, expect } from "vitest";
import { readFileSync } from "fs";
import path from "path";
import { resolveStatusFromMessage } from "@/lib/ws-status";
import type { WebSocketIncomingMessage } from "@/types/vexa";

// The sealed ws.v1 contract + its golden, resolved relative to this test file
// (clients/dashboard/tests → repo core/gateway/contracts/ws.v1).
const CONTRACTS_DIR = path.resolve(
  __dirname,
  "../../../core/gateway/contracts/ws.v1"
);
const golden = JSON.parse(
  readFileSync(path.join(CONTRACTS_DIR, "golden/BotStatus.recording.json"), "utf8")
);
const schema = JSON.parse(
  readFileSync(path.join(CONTRACTS_DIR, "ws.schema.json"), "utf8")
);

describe("resolveStatusFromMessage — ws.v1 bot_status status frame (the dropped-frame fix)", () => {
  it("the BotStatus golden conforms to the contract (top-level type + status)", () => {
    // Guards our understanding of the wire shape: the gateway forwards the raw
    // redis payload verbatim, so status lives at the top level — not in payload.
    const def = schema.$defs.BotStatus;
    expect(def.required).toContain("type");
    expect(def.required).toContain("status");
    expect(def.properties.type.const).toBe("bot_status");

    expect(golden.type).toBe("bot_status");
    expect(typeof golden.status).toBe("string");
    expect(golden).toHaveProperty("meeting_id");
  });

  it("extracts the top-level status from a bot_status frame (golden)", () => {
    // Previously fell through the switch and was silently dropped.
    const status = resolveStatusFromMessage(golden as WebSocketIncomingMessage);
    expect(status).toBe(golden.status);
  });

  it("extracts status from a bot_status frame for a dashboard MeetingStatus value", () => {
    const frame: WebSocketIncomingMessage = {
      type: "bot_status",
      status: "active",
      meeting_id: 7,
    };
    expect(resolveStatusFromMessage(frame)).toBe("active");
  });

  it("still reads the legacy meeting.status frame from payload (back-compat)", () => {
    const frame: WebSocketIncomingMessage = {
      type: "meeting.status",
      meeting: { platform: "google_meet", native_id: "abc-defg-hij" },
      payload: { status: "completed" },
      ts: "2026-06-22T00:00:00Z",
    };
    expect(resolveStatusFromMessage(frame)).toBe("completed");
  });

  it("returns null for non-status frames (no spurious setBotStatus)", () => {
    const pong: WebSocketIncomingMessage = { type: "pong" };
    expect(resolveStatusFromMessage(pong)).toBeNull();
  });
});
