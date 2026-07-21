/**
 * Unit tests for the version chip's two-identity label builder (hosted #72).
 *
 * The chip must be HONEST: when a platform release is configured at runtime it
 * shows the platform version PROMINENT with the UI build as provenance; when it
 * is absent (OSS self-host where UI == release) it falls back to the UI build
 * alone, unchanged from prior behavior.
 */
import { describe, it, expect } from "vitest";
import { versionChipText, withVPrefix } from "@/lib/version-chip-label";

const UI = "0.10.6.3";
const DATE = "2025-01-01";

describe("versionChipText — platform configured", () => {
  it("minimal shows platform prominent, UI as provenance", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      platformVersion: "v0.12.16-rc.4",
    });
    expect(label).toBe("v0.12.16-rc.4 · UI 0.10.6.3");
    expect(label).toContain("v0.12.16");
    expect(label).toContain("0.10.6.3");
  });

  it("adds a v-prefix when the platform version lacks one", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      platformVersion: "0.12.16",
    });
    expect(label).toBe("v0.12.16 · UI 0.10.6.3");
  });

  it("full and compact variants keep both identities", () => {
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16", variant: "full" }).label
    ).toBe("Running v0.12.16 · UI 0.10.6.3 · updated 2025-01-01");
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16", variant: "compact" }).label
    ).toBe("v0.12.16 · UI 0.10.6.3 · 2025-01-01");
  });

  it("title names both platform and UI build", () => {
    const { title } = versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16" });
    expect(title).toContain("platform v0.12.16");
    expect(title).toContain("UI build 0.10.6.3");
  });
});

describe("versionChipText — platform absent (unchanged OSS behavior)", () => {
  it("minimal shows UI version alone", () => {
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE }).label).toBe("0.10.6.3");
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: null }).label).toBe("0.10.6.3");
  });

  it("full and compact match prior format", () => {
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE, variant: "full" }).label).toBe(
      "Running 0.10.6.3 · updated 2025-01-01"
    );
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE, variant: "compact" }).label).toBe(
      "0.10.6.3 · 2025-01-01"
    );
  });
});

describe("withVPrefix", () => {
  it("is idempotent and adds when missing", () => {
    expect(withVPrefix("v1.2.3")).toBe("v1.2.3");
    expect(withVPrefix("1.2.3")).toBe("v1.2.3");
  });
});
