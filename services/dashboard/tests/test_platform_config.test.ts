/**
 * Pins the platform table against the platforms the API actually serves.
 *
 * A platform the API serves but the UI has no entry for used to blank the page:
 * the meeting detail page reads the config and dereferences `.name`, so a missing
 * key threw and tripped the error boundary. These tests pin the roster and prove
 * the lookup is total.
 */
import { describe, it, expect } from "vitest";
import { existsSync } from "fs";
import path from "path";
import { PLATFORM_CONFIG, getPlatformConfig } from "@/types/vexa";
import type { Meeting } from "@/types/vexa";
import { exportToTxt } from "@/lib/export";

/**
 * The platforms the core API accepts, mirrored from
 * core/meetings/services/meeting-api/src/meeting_api/lifecycle/stop_router.py
 * (`_SUPPORTED_PLATFORMS`). Adding a platform there means adding it here and to
 * PLATFORM_CONFIG — the union is `satisfies Record<Platform, PlatformConfig>`,
 * so the build fails before this test does.
 */
const API_PLATFORMS = ["google_meet", "teams", "zoom", "jitsi", "browser_session"];

const LUCIDE_ICON_KEYS = ["video", "users", "monitor"];

describe("PLATFORM_CONFIG roster", () => {
  it("covers exactly the platforms the API serves — no gaps, no strays", () => {
    expect(Object.keys(PLATFORM_CONFIG).sort()).toEqual([...API_PLATFORMS].sort());
  });

  for (const platform of API_PLATFORMS) {
    describe(platform, () => {
      const config = getPlatformConfig(platform);

      it("renders a display name", () => {
        // The detail page does exactly this; undefined here blanks the page.
        expect(config.name).toBeTruthy();
      });

      it("carries the classes the badges apply", () => {
        expect(config.textColor).toBeTruthy();
        expect(config.bgColor).toBeTruthy();
        expect(config.color).toBeTruthy();
      });

      it("resolves to an icon that exists", () => {
        if (config.iconSrc) {
          const asset = path.join(__dirname, "..", "public", config.iconSrc);
          expect(existsSync(asset), `missing icon asset ${config.iconSrc}`).toBe(true);
        } else {
          expect(LUCIDE_ICON_KEYS).toContain(config.icon);
        }
      });
    });
  }
});

describe("getPlatformConfig is total", () => {
  it("falls back for a platform this build predates", () => {
    const config = getPlatformConfig("some_platform_shipped_after_this_build");
    expect(config).toBeDefined();
    expect(config.name).toBeTruthy();
    expect(LUCIDE_ICON_KEYS).toContain(config.icon);
  });

  it("never returns undefined for junk input", () => {
    for (const junk of ["", "GOOGLE_MEET", "null", "../etc/passwd"]) {
      expect(getPlatformConfig(junk).name).toBeTruthy();
    }
  });
});

describe("transcript export names the meeting's own platform", () => {
  const meetingOn = (platform: string): Meeting => ({
    id: "1",
    platform: platform as Meeting["platform"],
    platform_specific_id: "room",
    status: "completed",
    start_time: null,
    end_time: null,
    bot_container_id: null,
    data: {},
    created_at: "2026-07-22T09:00:00",
  });

  for (const platform of API_PLATFORMS) {
    it(`labels ${platform} as ${getPlatformConfig(platform).name}`, () => {
      const txt = exportToTxt(meetingOn(platform), []);
      expect(txt).toContain(`Platform: ${getPlatformConfig(platform).name}`);
    });
  }
});

describe("jitsi specifically", () => {
  it("is named for the platform, not mislabelled as another", () => {
    expect(getPlatformConfig("jitsi").name).toBe("Jitsi Meet");
  });

  it("does not borrow another platform's logo", () => {
    // The old icon ternary fell through to the Zoom asset for anything unrecognised.
    expect(getPlatformConfig("jitsi").iconSrc).toBeNull();
  });

  it("accepts a plain room name as its native id", () => {
    expect(getPlatformConfig("jitsi").pattern.test("vexa-witness-cov2")).toBe(true);
  });
});
