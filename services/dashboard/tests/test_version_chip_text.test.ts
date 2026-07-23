/**
 * Unit tests for the version chip's product release label builder.
 *
 * A hosted deployment presents one product version. Without runtime platform
 * configuration, OSS self-hosted deployments use the build-time identity.
 */
import { describe, it, expect } from "vitest";
import { versionChipText, withVPrefix } from "@/lib/version-chip-label";

const UI = "0.10.6.3";
const DATE = "2025-01-01";

describe("versionChipText — platform configured", () => {
  it("minimal shows only the platform release", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      platformVersion: "v0.12.16-rc.4",
    });
    expect(label).toBe("v0.12.16-rc.4");
    expect(label).not.toContain(UI);
  });

  it("adds a v-prefix when the platform version lacks one", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      platformVersion: "0.12.16",
    });
    expect(label).toBe("v0.12.16");
  });

  it("full and compact variants keep one product identity", () => {
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16", variant: "full" }).label
    ).toBe("Running v0.12.16");
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16", variant: "compact" }).label
    ).toBe("v0.12.16");
  });

  it("title points to the platform release without exposing the UI build", () => {
    const { title } = versionChipText({ uiVersion: UI, releaseDate: DATE, platformVersion: "v0.12.16" });
    expect(title).toBe("Vexa v0.12.16 · click for release notes");
    expect(title).not.toContain(UI);
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
