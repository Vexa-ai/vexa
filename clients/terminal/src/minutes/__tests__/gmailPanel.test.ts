/** Founder 2026-10-16: a Gmail connection with drafts on is "Connected · Drafts on" plus Disconnect — no
 *  loud "Enable drafts / reconnect"; a quiet Reconnect only when the grant is missing; no "Minutes". */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { gmailView } from "../connectionLabels";

describe("the Gmail connection", () => {
  it("with drafts on says so and offers no reconnect", () => {
    expect(gmailView({ status: "ready", permissions: ["drafts"] })).toEqual({ line: "Connected · Drafts on", action: null });
  });
  it("without the draft grant offers a quiet Reconnect", () => {
    expect(gmailView({ status: "ready", permissions: [] })).toEqual({ line: "Connected · Drafts off", action: "Reconnect" });
  });
  it("never names the old product", () => {
    const src = readFileSync(join(__dirname, "../ConnectionsPanel.tsx"), "utf8");
    expect(src).not.toMatch(/Minutes does not send|Minutes exposes|Your Minutes workspace|Enable drafts \/ reconnect/);
  });
});
