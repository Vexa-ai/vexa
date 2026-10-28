import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { SURFACES, checkVersions, setVersion, sourceVersion } from "./version.mjs";
import { guardTree } from "../scripts/test-tree.mjs";

guardTree();

test("every version surface of this tree reads package.json's version", () => {
  assert.deepEqual(checkVersions(), []);
});

function copyTree() {
  const real = join(dirname(new URL(import.meta.url).pathname), "..");
  const dir = mkdtempSync(join(tmpdir(), "vexa-version-"));
  for (const f of ["package.json", ...new Set(SURFACES.map((s) => s.file))]) {
    mkdirSync(dirname(join(dir, f)), { recursive: true });
    writeFileSync(join(dir, f), readFileSync(join(real, f)));
  }
  return dir;
}

test("a surface left behind is named, with what it says and what package.json says", () => {
  const dir = copyTree();
  const p = join(dir, "deploy/helm/charts/vexa/Chart.yaml");
  writeFileSync(p, readFileSync(p, "utf8").replace(/^appVersion:.*$/m, 'appVersion: "9.9.9"'));
  const bad = checkVersions(dir);
  assert.equal(bad.length, 1);
  assert.equal(bad[0].file, "deploy/helm/charts/vexa/Chart.yaml");
  assert.equal(bad[0].found, "9.9.9");
  assert.equal(bad[0].want, sourceVersion(dir));
});

test("set writes one version to package.json and every surface, and nothing else changes", () => {
  const dir = copyTree();
  const before = readFileSync(join(dir, "docs/docs/changelog.mdx"), "utf8");
  setVersion("1.2.3", dir);
  assert.equal(sourceVersion(dir), "1.2.3");
  assert.deepEqual(checkVersions(dir), []);
  const after = readFileSync(join(dir, "docs/docs/changelog.mdx"), "utf8");
  assert.equal(after.split("\n").length, before.split("\n").length);
});

test("set refuses something that is not a version", () => {
  assert.throws(() => setVersion("v1.2", copyTree()), /not a version/);
});
