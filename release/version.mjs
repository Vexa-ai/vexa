#!/usr/bin/env node
// The version this tree builds has ONE source: the root package.json "version".
// Every other surface that names the current version is derived from it:
//   node release/version.mjs              check every surface equals package.json (exit 1 if not)
//   node release/version.mjs set 0.13.4   write that version to package.json and every surface
// gate:docs-version runs the check, so a surface left behind at a release cut reds CI.
//
// Not listed here on purpose: the "minimum version" statements in contracts and docs ("a bot older
// than v0.13.2 …") are facts about when something changed, and LEGACY_DUAL_PUSH_ENDS_AFTER in
// image-names.mjs names the one release that renames the images. Neither follows the line's version.
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT_DEFAULT = join(dirname(fileURLToPath(import.meta.url)), "..");

// Each surface: the file, a pattern whose group 1 is the version, and how to write a version back.
export const SURFACES = [
  { file: "deploy/compose/.env.example", re: /^IMAGE_TAG=v(\S+)$/m, write: (v) => `IMAGE_TAG=v${v}` },
  { file: "deploy/helm/charts/vexa/Chart.yaml", re: /^appVersion:\s*"([^"]+)"$/m, write: (v) => `appVersion: "${v}"` },
  { file: "docs/docs/changelog.mdx", re: /docs-reflects:\s*([0-9A-Za-z.\-]+)/, write: (v) => `docs-reflects: ${v}` },
  { file: "docs/docs/changelog.mdx", re: /These docs reflect Vexa `v([^`]+)`/, write: (v) => `These docs reflect Vexa \`v${v}\`` },
  { file: "docs/docs/configuration.mdx", re: /\| `IMAGE_TAG` \| the release, e\.g\. `v([^`]+)`/, write: (v) => `| \`IMAGE_TAG\` | the release, e.g. \`v${v}\`` },
];

const SEMVER = /^\d+\.\d+\.\d+(-[0-9A-Za-z.\-]+)?$/;

export function sourceVersion(root = ROOT_DEFAULT) {
  return JSON.parse(readFileSync(join(root, "package.json"), "utf8")).version;
}

// Returns [{file, found, want}] for every surface that disagrees with package.json (empty = all agree).
export function checkVersions(root = ROOT_DEFAULT) {
  const want = sourceVersion(root);
  const bad = [];
  for (const s of SURFACES) {
    let text;
    try { text = readFileSync(join(root, s.file), "utf8"); } catch { bad.push({ file: s.file, found: "(file missing)", want }); continue; }
    const found = s.re.exec(text)?.[1];
    if (found !== want) bad.push({ file: s.file, found: found ?? "(pattern not found)", want });
  }
  return bad;
}

export function setVersion(version, root = ROOT_DEFAULT) {
  if (!SEMVER.test(version)) throw new Error(`not a version: ${version}`);
  const pkgPath = join(root, "package.json");
  const pkg = readFileSync(pkgPath, "utf8");
  writeFileSync(pkgPath, pkg.replace(/("version":\s*")[^"]+(")/, `$1${version}$2`));
  for (const s of SURFACES) {
    const p = join(root, s.file);
    const text = readFileSync(p, "utf8");
    if (!s.re.test(text)) throw new Error(`${s.file}: version pattern not found`);
    writeFileSync(p, text.replace(s.re, s.write(version)));
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [cmd, v] = process.argv.slice(2);
  if (cmd === "set") { setVersion(v); console.log(`version set to ${v} on package.json and ${SURFACES.length} surfaces`); }
  const bad = checkVersions();
  for (const b of bad) console.error(`  ✗ ${b.file}: ${b.found} (package.json says ${b.want})`);
  if (bad.length) process.exit(1);
  console.log(`  ✓ every version surface reads ${sourceVersion()} (package.json)`);
}
