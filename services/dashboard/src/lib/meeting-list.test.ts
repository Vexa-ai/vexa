// #1222 — the list orders by MEETING EVENT time, non-terminal rows pinned first, matching the
// server (meeting-api list_meetings). The witnessed failure: a calendar row imported Aug 16,
// live Aug 18, sat at position 19 under 18 rows created since the import.
import { describe, expect, it } from "vitest";

import { compareMeetingsListOrder } from "./meeting-list";
import type { Meeting } from "@/types/vexa";

function meeting(partial: Partial<Meeting> & { id: string }): Meeting {
  return {
    platform: "google_meet",
    platform_specific_id: `native-${partial.id}`,
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: {},
    created_at: "2026-08-01T00:00:00Z",
    ...partial,
  };
}

describe("compareMeetingsListOrder", () => {
  it("puts the live calendar meeting (imported days ago) at position 0", () => {
    const live = meeting({
      id: "26298", status: "active",
      created_at: "2026-08-16T22:40:00Z",
      data: { scheduled_at: "2026-08-18T09:00:00Z" },
    });
    const sinceImport = Array.from({ length: 18 }, (_, i) => meeting({
      id: `${26300 + i}`, status: "completed",
      created_at: `2026-08-17T${String(i).padStart(2, "0")}:00:00Z`,
      start_time: `2026-08-17T${String(i).padStart(2, "0")}:00:30Z`,
    }));
    const sorted = [...sinceImport, live].sort(compareMeetingsListOrder);
    expect(sorted[0].id).toBe("26298");
  });

  it("pins every non-terminal status above a fresher terminal row", () => {
    const freshTerminal = meeting({
      id: "9", status: "completed", created_at: "2026-08-18T12:00:00Z",
      start_time: "2026-08-18T12:00:30Z",
    });
    const pinned = (["scheduled", "requested", "joining", "awaiting_admission", "active",
      "stopping"] as const).map((status, i) => meeting({
      id: `${i + 1}`, status, created_at: "2026-08-10T00:00:00Z",
      data: { scheduled_at: `2026-08-1${i}T09:00:00Z` },
    }));
    const sorted = [freshTerminal, ...pinned].sort(compareMeetingsListOrder);
    expect(sorted[sorted.length - 1].id).toBe("9");
  });

  it("orders within a group by scheduled_at ?? start_time ?? created_at desc, id tiebreak", () => {
    const byStart = meeting({ id: "3", created_at: "2026-08-10T00:00:00Z",
      start_time: "2026-08-17T10:00:00Z" });
    const byCreated = meeting({ id: "4", created_at: "2026-08-16T00:00:00Z" });
    const byScheduled = meeting({ id: "5", created_at: "2026-08-01T00:00:00Z",
      data: { scheduled_at: "2026-08-18T09:00:00Z" } });
    const tieOld = meeting({ id: "6", created_at: "2026-08-15T00:00:00Z" });
    const tieNew = meeting({ id: "7", created_at: "2026-08-15T00:00:00Z" });
    const sorted = [tieOld, byStart, byCreated, tieNew, byScheduled]
      .sort(compareMeetingsListOrder);
    expect(sorted.map((m) => m.id)).toEqual(["5", "3", "4", "7", "6"]);
  });

  it("degrades a malformed scheduled_at to start_time/created_at instead of NaN-scrambling", () => {
    const bad = meeting({ id: "1", created_at: "2026-08-18T08:00:00Z",
      data: { scheduled_at: "not-a-timestamp" } });
    const good = meeting({ id: "2", created_at: "2026-08-17T00:00:00Z",
      data: { scheduled_at: "2026-08-18T09:00:00Z" } });
    const sorted = [bad, good].sort(compareMeetingsListOrder);
    expect(sorted.map((m) => m.id)).toEqual(["2", "1"]);
  });
});
