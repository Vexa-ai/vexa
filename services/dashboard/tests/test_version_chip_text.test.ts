/**
 * The chip's headline is the RELEASE this deployment is. The dashboard's own UI build is a
 * component identity and belongs in the tooltip: a 0.10-lineage UI build fronting a 0.12
 * platform is normal, and "0.10.6.3" beside the release in a headline reports a true fact about
 * the wrong layer.
 *
 * And when nothing can name the release, the chip says so rather than borrowing the UI build —
 * which is how this badge advertised 0.12.18 against a 0.12.22-rc.3 cluster.
 */
import { describe, it, expect } from "vitest";
import { versionChipText, withVPrefix } from "@/lib/version-chip-label";

const UI = "0.10.6.3";
const DATE = "2026-05-28";

describe("versionChipText — the release is the headline", () => {
  it("shows the release alone, with the UI build nowhere near it", () => {
    const { label } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      backendVersion: "0.12.22",
      backendStatus: "ok",
      versionSource: "gateway",
    });
    expect(label).toBe("v0.12.22");
    expect(label).not.toContain(UI);
  });

  it("keeps the UI build in the tooltip, labelled as a build", () => {
    const { title } = versionChipText({
      uiVersion: UI,
      releaseDate: DATE,
      backendVersion: "0.12.22",
      backendStatus: "ok",
      versionSource: "gateway",
    });
    expect(title).toContain("Vexa v0.12.22");
    expect(title).toContain("dashboard UI build v0.10.6.3");
  });

  it("names the provenance so a deploy pin is not passed off as a live reading", () => {
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22",
        backendStatus: "ok", versionSource: "release-pin",
      }).title
    ).toContain("deployed as");
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22",
        backendStatus: "ok", versionSource: "gateway",
      }).title
    ).toContain("reported live by this deployment");
  });

  it("full and compact keep the headline to one version", () => {
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22",
        backendStatus: "ok", versionSource: "gateway", variant: "full",
      }).label
    ).toBe("Running v0.12.22");
    expect(
      versionChipText({
        uiVersion: UI, releaseDate: DATE, backendVersion: "0.12.22",
        backendStatus: "ok", versionSource: "gateway", variant: "compact",
      }).label
    ).toBe("v0.12.22 · 2026-05-28");
  });
});

describe("versionChipText — nothing can name the release", () => {
  it("says unknown and refuses to let the UI build stand in", () => {
    const { label, title } = versionChipText({ uiVersion: UI, releaseDate: DATE });
    expect(label).toBe("version unknown");
    expect(title).toContain("describes the UI only, not the release");
    expect(title).toContain("dashboard UI build v0.10.6.3");
  });

  it("an 'ok' status with no version is still unknown", () => {
    expect(
      versionChipText({ uiVersion: UI, releaseDate: DATE, backendVersion: null, backendStatus: "ok" }).label
    ).toBe("version unknown");
  });

  it("says it is checking rather than showing a version it does not have yet", () => {
    const { label } = versionChipText({ uiVersion: UI, releaseDate: DATE, backendStatus: "loading" });
    expect(label).toBe("checking…");
    expect(label).not.toContain("unknown");
  });

  it("offers no release-notes link when there is no version to link to", () => {
    expect(versionChipText({ uiVersion: UI, releaseDate: DATE }).title).not.toContain(
      "click for release notes"
    );
  });
});

describe("withVPrefix", () => {
  it("is idempotent and adds when missing", () => {
    expect(withVPrefix("v1.2.3")).toBe("v1.2.3");
    expect(withVPrefix("1.2.3")).toBe("v1.2.3");
  });
});
