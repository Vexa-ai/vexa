import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { SURFACES, VERSION_FILES, applyVersion, checkVersions, compareVersions, setVersion, sourceVersion } from "./version.mjs";
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

test("set writes the chart's own version too, so a bump PR is exactly what set writes", () => {
  const dir = copyTree();
  setVersion("1.2.3", dir);
  const chart = readFileSync(join(dir, "deploy/helm/charts/vexa/Chart.yaml"), "utf8");
  assert.match(chart, /^version: 1\.2\.3$/m);
  assert.match(chart, /^appVersion: "1\.2\.3"$/m);
});

test("applyVersion is set without the disk: same texts, input left alone, all-or-nothing", () => {
  const dir = copyTree();
  const files = Object.fromEntries(VERSION_FILES.map((f) => [f, readFileSync(join(dir, f), "utf8")]));
  const before = { ...files };
  const out = applyVersion(files, "2.0.0");
  assert.deepEqual(files, before);
  setVersion("2.0.0", dir);
  for (const f of VERSION_FILES) assert.equal(out[f], readFileSync(join(dir, f), "utf8"), f);
  assert.throws(() => applyVersion({ ...files, "docs/docs/configuration.mdx": "nothing here" }, "2.0.0"), /configuration\.mdx: version pattern not found/);
});

test("compareVersions follows SemVer precedence", () => {
  assert.ok(compareVersions("0.13.4", "0.13.3") > 0);
  assert.ok(compareVersions("0.13.3", "0.13.4") < 0);
  assert.ok(compareVersions("0.14.0", "0.13.9") > 0);
  assert.ok(compareVersions("1.0.0", "0.99.99") > 0);
  assert.equal(compareVersions("0.13.4", "0.13.4"), 0);
  assert.ok(compareVersions("0.13.4", "0.13.4-alpha.2") > 0);
  assert.ok(compareVersions("0.13.4-alpha.10", "0.13.4-alpha.2") > 0);
  assert.ok(compareVersions("0.13.4-beta", "0.13.4-alpha.2") > 0);
  assert.ok(compareVersions("0.13.4-alpha", "0.13.4-alpha.1") < 0);
  assert.throws(() => compareVersions("v0.13.4", "0.13.3"), /not a version/);
});
