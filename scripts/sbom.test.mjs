import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { guardTree, sandboxTree } from "./test-tree.mjs";

guardTree();

// sbom.mjs runs `pnpm licenses list`, and pnpm writes a scratch file (`_tmp_<pid>_<hex>`) into the
// directory it runs in. Run in the checkout, that is a write every parallel test file can see — so
// the SBOM is emitted from this file's private copy of the tree.
const ROOT = sandboxTree();
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

// S73: the terminal's images install from clients/terminal/package-lock.json with `npm ci`, which the
// pnpm index does not describe. A package only that lock carries must still reach the SBOM.
test("a package only the terminal's npm lock carries is inventoried", () => {
  const lockPath = join(ROOT, "clients", "terminal", "package-lock.json");
  const original = readFileSync(lockPath, "utf8");
  const lock = JSON.parse(original);
  lock.packages["node_modules/vexa-sbom-npm-lock-probe"] = { version: "9.9.9", license: "MIT" };
  writeFileSync(lockPath, JSON.stringify(lock, null, 2));
  try {
    const pkg = packageNamed(emitSbom(), "vexa-sbom-npm-lock-probe");
    assert(pkg, "a package in the terminal's npm lock is absent from the SPDX");
    assert.equal(pkg.versionInfo, "9.9.9");
    assert.equal(pkg.licenseDeclared, "MIT");
  } finally {
    writeFileSync(lockPath, original);
  }
});
