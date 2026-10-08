/** The terminal reads a deleted meeting through api.v1's `ArtifactDeletion` (S12).
 *
 *  meeting-api stamps `data.artifact_deletion` when the owner deletes a meeting's transcript and
 *  recordings, and the meeting view must then show it as deleted. The field was `additionalProperties`
 *  until it got a schema; these cases read the sealed contract's own goldens and enum, so renaming or
 *  reshaping the stamp in the contract fails this test instead of quietly showing a deleted meeting as
 *  one with a transcript.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { artifactsDeleted, type MeetingRowDTO } from "../liveMeetings";

const API_V1 = join(__dirname, "../../../../../core/gateway/contracts/api.v1");
const schema = JSON.parse(readFileSync(join(API_V1, "api.schema.json"), "utf8"));
const meetingGoldens = readdirSync(join(API_V1, "golden"))
  .filter((f) => f.startsWith("MeetingResponse.") && f.endsWith(".json"))
  .map((f) => ({ name: f, row: JSON.parse(readFileSync(join(API_V1, "golden", f), "utf8")) as MeetingRowDTO }));

describe("api.v1 MeetingResponse.data.artifact_deletion", () => {
  it("is the field the contract declares on a meeting's data", () => {
    const data = schema.components.schemas.MeetingResponse.properties.data.anyOf.find(
      (a: { type?: string }) => a.type === "object");
    expect(data.properties.artifact_deletion.$ref).toBe("#/components/schemas/ArtifactDeletion");
  });

  it("every golden reads as deleted exactly when it carries the stamp", () => {
    expect(meetingGoldens.some((g) => g.row.data?.artifact_deletion)).toBe(true);
    for (const g of meetingGoldens) {
      expect(artifactsDeleted(g.row), g.name).toBe(!!g.row.data?.artifact_deletion);
    }
  });

  it("every state the contract allows reads as deleted", () => {
    const states: string[] = schema.components.schemas.ArtifactDeletion.properties.state.enum;
    expect(states).toEqual(expect.arrayContaining(["pending", "completed"]));
    for (const state of states) {
      expect(artifactsDeleted({ data: { artifact_deletion: { state } } } as MeetingRowDTO), state).toBe(true);
    }
    expect(artifactsDeleted({ data: { artifact_deletion: null } } as MeetingRowDTO)).toBe(false);
    expect(artifactsDeleted({ data: null })).toBe(false);
  });
});
