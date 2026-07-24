import { describe, expect, it } from "vitest";
import fixture from "./fixtures/meeting-13627.json";
import { PLATFORM_CONFIG } from "@/types/vexa";

describe("sanitized meeting 13627 platform boundary", () => {
  it("resolves a display config before the detail page renders", () => {
    expect(fixture.segments).toHaveLength(42);
    expect(new Set(fixture.segments.map((segment) => segment.speaker))).toEqual(
      new Set(["Anna", "Boris"])
    );

    const platformConfig = (
      PLATFORM_CONFIG as Record<string, (typeof PLATFORM_CONFIG)[keyof typeof PLATFORM_CONFIG]>
    )[fixture.platform];

    // This is the meeting-detail page's point of introduction: it immediately
    // dereferences .name after indexing the shared platform table.
    expect(platformConfig.name).toBe("Jitsi Meet");
  });
});
