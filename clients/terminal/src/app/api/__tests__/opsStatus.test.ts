import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterAll, describe, expect, it } from "vitest";
import { readOpsStatus } from "../ops-status/status";

/** The ops-notice contract: the operator's file is the whole protocol, and every failure mode
 *  (absent, unreadable, malformed, active!=true) must read as "no notice" — a broken status file
 *  must never block the UI or fabricate a maintenance window. */
describe("ops-status — operator file → notice", () => {
  const dir = mkdtempSync(join(tmpdir(), "ops-status-"));
  afterAll(() => rmSync(dir, { recursive: true, force: true }));

  const file = (name: string, content: string) => {
    const p = join(dir, name);
    writeFileSync(p, content);
    return p;
  };

  it("missing file → inactive", () => {
    expect(readOpsStatus(join(dir, "nope.json"))).toEqual({ active: false });
  });

  it("malformed JSON → inactive", () => {
    expect(readOpsStatus(file("bad.json", "{not json"))).toEqual({ active: false });
  });

  it("active must be literally true — truthy strings don't count", () => {
    expect(readOpsStatus(file("truthy.json", '{"active":"yes"}'))).toEqual({ active: false });
    expect(readOpsStatus(file("off.json", '{"active":false,"message":"x"}'))).toEqual({ active: false });
  });

  it("active window passes message + since through, trimmed", () => {
    const p = file("on.json", '{"active":true,"message":"  Updating agent service — chats may pause ~1 min  ","since":"2026-07-08T16:20:00Z"}');
    expect(readOpsStatus(p)).toEqual({
      active: true,
      message: "Updating agent service — chats may pause ~1 min",
      since: "2026-07-08T16:20:00Z",
    });
  });

  it("blank message → active with default wording (undefined message)", () => {
    expect(readOpsStatus(file("blank.json", '{"active":true,"message":"   "}'))).toEqual({ active: true, message: undefined, since: undefined });
  });
});
