import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const pageSource = readFileSync(
  fileURLToPath(new URL("../src/app/meetings/page.tsx", import.meta.url)),
  "utf8",
);

describe("meetings page initial load", () => {
  it("uses one effect for the initial and filter-driven history request", () => {
    const effects = [...pageSource.matchAll(/useEffect\(\(\) => \{([\s\S]*?)\n\s*\}, \[([^\]]*)\]\);/g)];
    const meetingLoadEffects = effects.filter(([, body]) =>
      body.includes("fetchMeetings(") || body.includes("applyFilters("),
    );

    expect(meetingLoadEffects).toHaveLength(1);
    expect(meetingLoadEffects[0][1]).toContain(
      "applyFilters(searchQuery, statusFilter, platformFilter)",
    );
  });
});
