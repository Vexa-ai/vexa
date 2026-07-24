import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const LITE = join(ROOT, "deploy", "lite", "Dockerfile.lite");

function emitSbom(liteDockerfile) {
  const scratch = mkdtempSync(join(tmpdir(), "vexa-sbom-test-"));
  const output = join(scratch, "sbom.spdx.json");
  const env = { ...process.env, SBOM_CREATED: "2026-07-24T00:00:00.000Z" };
  if (liteDockerfile) {
    const fixture = join(scratch, "Dockerfile.lite");
    writeFileSync(fixture, liteDockerfile);
    env.SBOM_LITE_DOCKERFILE = fixture;
  }
  try {
    execFileSync(
      "node",
      ["scripts/sbom.mjs", "--version", "test", "--output", output],
      {
        cwd: ROOT,
        env,
        stdio: "pipe",
      },
    );
    return JSON.parse(readFileSync(output, "utf8"));
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

function packageNamed(doc, name) {
  return doc.packages.find((entry) => entry.name === name);
}

test("Lite final-stage apt packages are represented in the emitted SPDX", () => {
  const doc = emitSbom();
  for (const name of ["ffmpeg", "x11vnc", "pulseaudio", "postgresql-client"]) {
    const pkg = packageNamed(doc, name);
    assert(pkg, `${name} is installed in the Lite final image but absent from the SPDX`);
    assert.equal(pkg.licenseDeclared, "NOASSERTION");
    assert(doc.relationships.some(
      (edge) => edge.relatedSpdxElement === pkg.SPDXID && edge.relationshipType === "CONTAINS",
    ));
  }
});

test("a newly declared Lite final-stage apt package is automatically inventoried", () => {
  const marker = "supervisor postgresql-client";
  const original = readFileSync(LITE, "utf8");
  const edited = original.replace(marker, `${marker} review-apt-probe`);
  assert.notEqual(edited, original, "fixture setup did not alter Dockerfile.lite");
  const doc = emitSbom(edited);
  assert(packageNamed(doc, "review-apt-probe"));
});

test("Firefox and its isolated LGPL library are nested in the emitted SPDX", () => {
  const doc = emitSbom();
  const firefox = packageNamed(doc, "firefox");
  assert(firefox, "FIREFOX_SBOM_RED: Firefox is absent from the emitted SPDX");
  assert.equal(firefox.versionInfo, "151.0");
  assert.equal(firefox.licenseDeclared, "MPL-2.0");

  const lgpl = packageNamed(doc, "liblgpllibs.so");
  assert(lgpl, "FIREFOX_SBOM_RED: liblgpllibs.so is absent from the emitted SPDX");
  assert.equal(lgpl.licenseDeclared, "LGPL-2.1-or-later");

  assert(doc.relationships.some(
    (edge) =>
      edge.spdxElementId === "SPDXRef-Package-vexa" &&
      edge.relatedSpdxElement === firefox.SPDXID &&
      edge.relationshipType === "CONTAINS",
  ), "root package does not CONTAIN Firefox");
  assert(doc.relationships.some(
    (edge) =>
      edge.spdxElementId === firefox.SPDXID &&
      edge.relatedSpdxElement === lgpl.SPDXID &&
      edge.relationshipType === "CONTAINS",
  ), "Firefox does not CONTAIN liblgpllibs.so");
});
