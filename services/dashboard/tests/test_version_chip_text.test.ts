/**
 * Unit tests for the version chip's label builder.
 *
 * The rule under test is honesty, not formatting: the backend version comes
 * live from the deployment, the UI build version is labelled as the UI's own,
 * and a backend that does not answer produces "version unknown" rather than
 * any remembered number. The defect these tests exist to prevent shipped once
 * already — a chip reading "v0.12.18" against a v0.12.22-rc.3 cluster, because
 * 0.12.18 was a constant the image carried.
 */
import { describe, it, expect } from "vitest";
import { versionChipText, withVPrefix } from "@/lib/version-chip-label";

const UI = "0.10.6.3";
const DATE = "2025-01-01";

describe("versionChipText — backend answered, differs from the UI build", () => {
  it("minimal shows the live backend version AND labels the UI build", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      backendVersion: "0.12.22-rc.3",
      backendStatus: "ok",
    });
    expect(label).toBe("v0.12.22-rc.3 · UI v0.10.6.3");
  });

  it("adds a v-prefix when the backend version lacks one", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      backendVersion: "0.12.22",
      backendStatus: "ok",
    });
    expect(label).toContain("v0.12.22");
  });

  it("full and compact spell out 'UI build' so neither number can pass for the other", () => {
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22", backendStatus: "ok", variant: "full",
      }).label
    ).toBe("Running v0.12.22 · UI build v0.10.6.3 · updated 2025-01-01");
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22", backendStatus: "ok", variant: "compact",
      }).label
    ).toBe("v0.12.22 · UI build v0.10.6.3 · 2025-01-01");
  });

  it("the title names the backend version as live and the UI build as a build", () => {
    const { title } = versionChipText({
      uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22", backendStatus: "ok",
    });
    expect(title).toContain("live from this deployment");
    expect(title).toContain("UI build");
  });
});

describe("versionChipText — backend and UI agree", () => {
  it("shows one version rather than saying it twice", () => {
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, backendVersion: UI, backendStatus: "ok" }).label
    ).toBe("v0.10.6.3");
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: `v${UI}`, backendStatus: "ok", variant: "full",
      }).label
    ).toBe("Running v0.10.6.3 · updated 2025-01-01");
  });
});

describe("versionChipText — backend silent", () => {
  it("says the version is unknown and never borrows the UI build for it", () => {
    const { label, title } = versionChipText({
      uiVersion: UI, releaseDate: DATE, backendStatus: "unknown",
    });
    expect(label).toBe("version unknown · UI v0.10.6.3");
    expect(title).toContain("did not report its version");
    expect(title).toContain("does NOT tell you the backend release");
  });

  it("an 'ok' status with no version is still unknown, not the UI build", () => {
    const { label } = versionChipText({
      uiVersion: UI, releaseDate: DATE, backendVersion: null, backendStatus: "ok",
    });
    expect(label).toBe("version unknown · UI v0.10.6.3");
  });

  it("full and compact keep the admission visible", () => {
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, backendStatus: "unknown", variant: "full" }).label
    ).toBe("Running version unknown · UI build v0.10.6.3 · updated 2025-01-01");
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, backendStatus: "unknown", variant: "compact" }).label
    ).toBe("version unknown · UI build v0.10.6.3 · 2025-01-01");
  });

  it("defaults to unknown when no status is supplied — silence is never optimism", () => {
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE }).label).toBe(
      "version unknown · UI v0.10.6.3"
    );
  });
});

describe("versionChipText — still asking", () => {
  it("says it is checking rather than showing a version it does not have yet", () => {
    const { label } = versionChipText({
      uiVersion: UI, releaseDate: DATE, backendStatus: "loading",
    });
    expect(label).toBe("checking… · UI v0.10.6.3");
    expect(label).not.toContain("unknown");
  });
});

describe("withVPrefix", () => {
  it("is idempotent and adds when missing", () => {
    expect(withVPrefix("v1.2.3")).toBe("v1.2.3");
    expect(withVPrefix("1.2.3")).toBe("v1.2.3");
  });
});
