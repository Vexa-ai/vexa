import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const LITE = join(ROOT, "deploy", "lite", "Dockerfile.lite");

function emitSbom(liteDockerfile, pnpmResponse) {
  const scratch = mkdtempSync(join(tmpdir(), "vexa-sbom-test-"));
  const output = join(scratch, "sbom.spdx.json");
  const env = { ...process.env, SBOM_CREATED: "2026-07-24T00:00:00.000Z" };
  if (pnpmResponse) {
    writeFileSync(join(scratch, "pnpm"), `#!${process.execPath}\n` +
      `process.stdout.write(${JSON.stringify(JSON.stringify(pnpmResponse.output))});\n` +
      `process.exit(${pnpmResponse.status});\n`, { mode: 0o755 });
    env.PATH = `${scratch}:${env.PATH}`;
  }
  if (liteDockerfile) {
    const fixture = join(scratch, "Dockerfile.lite");
    writeFileSync(fixture, liteDockerfile);
    env.SBOM_LITE_DOCKERFILE = fixture;
  }
  try {
    const result = spawnSync(
      "node",
      ["scripts/sbom.mjs", "--version", "test", "--output", output],
      {
        cwd: ROOT,
        env,
        stdio: "pipe",
        encoding: "utf8",
      },
    );
    assert.equal(result.status, 0, result.stderr);
    return { doc: JSON.parse(readFileSync(output, "utf8")), stderr: result.stderr };
  } finally {
    rmSync(scratch, { recursive: true, force: true });
  }
}

function packageNamed(doc, name) {
  return doc.packages.find((entry) => entry.name === name);
}

test("Lite final-stage apt packages are represented in the emitted SPDX", () => {
  const { doc } = emitSbom();
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
  const { doc } = emitSbom(edited);
  assert(packageNamed(doc, "review-apt-probe"));
});

test("a failed pnpm licence query warns and preserves the other SBOM sources", () => {
  const { doc, stderr } = emitSbom(undefined, {
    status: 1,
    output: { error: { code: "ENOENT", message: "synthetic missing store entry" } },
  });
  assert.match(stderr, /WARN:.*pnpm licenses list --json.*failed/);
  assert.match(stderr, /without the npm tree/);
  assert(packageNamed(doc, "ffmpeg"));
  assert(!doc.packages.some((pkg) => pkg.externalRefs?.some((ref) => ref.referenceLocator.startsWith("pkg:npm/"))));
});

test("a successful pnpm licence query retains every declared package version", () => {
  const { doc, stderr } = emitSbom(undefined, {
    status: 0,
    output: { MIT: [{ name: "synthetic-dependency", versions: ["1.0.0", "2.0.0"] }] },
  });
  assert.doesNotMatch(stderr, /without the npm tree/);
  const packages = doc.packages.filter((pkg) => pkg.name === "synthetic-dependency");
  assert.deepEqual(packages.map((pkg) => pkg.versionInfo), ["1.0.0", "2.0.0"]);
  assert(packages.every((pkg) => pkg.licenseDeclared === "MIT"));
});
