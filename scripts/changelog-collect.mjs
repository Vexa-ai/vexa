// scripts/changelog-collect.mjs — assemble per-PR changelog fragments into the changelog at
// release time (the towncrier pattern). Repo tooling only: pure Node stdlib, no product runtime,
// no import from core/ or clients/ — it runs during the release version-bump, never in a service.
//
// WHY THIS EXISTS. Every user-visible PR used to append its line to the single tail of
// docs/docs/changelog.mdx. N parallel PRs then collide on that one last line even though the
// additions don't interact — the v0.12.9 batch (#678…#687) paid 5 manual conflict-resolution
// cycles to exactly this. The fix: each PR drops its own file at docs/changelog.d/<pr>-<slug>.md
// (N PRs → N distinct files → zero collisions; git never sees a shared edit). The release
// version-bump runs this collector once to fold the pending fragments into the changelog under the
// current version section, then removes the consumed fragments.
//
// The docs-reflects stamp (docs/docs/changelog.mdx:6-7) is NOT touched here — the release
// version-bump owns advancing it (gate:docs-version pins it to Chart.yaml appVersion). This script
// only inserts the accumulated fragment bullets into an existing version section.
//
// Usage:
//   node scripts/changelog-collect.mjs                 assemble pending fragments, delete them
//   node scripts/changelog-collect.mjs --check         preview only; mutate nothing
//                                                       (exit 3 if fragments are pending — a CI signal)
//   node scripts/changelog-collect.mjs --version X.Y.Z override the target version
//   node scripts/changelog-collect.mjs --section "## …" override the exact section heading
//
// Default target version = Chart.yaml appVersion; default section = "## <MAJOR>.<MINOR>.x maintenance
// fixes" (the changelog's existing home for per-PR point-release bullets).

import { readFileSync, writeFileSync, readdirSync, rmSync, existsSync } from "node:fs";
import { join, dirname, basename } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const FRAG_DIR = join(ROOT, "docs", "changelog.d");
const CHANGELOG = join(ROOT, "docs", "docs", "changelog.mdx");
const CHART = join(ROOT, "deploy", "helm", "charts", "vexa", "Chart.yaml");

const argv = process.argv.slice(2);
const flag = (name) => { const i = argv.indexOf(name); return i >= 0 ? argv[i + 1] : undefined; };
const check = argv.includes("--check");

function die(msg) { console.error(`changelog-collect: ${msg}`); process.exit(2); }

// The version whose section receives the fragments.
let version = flag("--version");
if (!version) {
  if (!existsSync(CHART)) die("Chart.yaml not found — pass --version X.Y.Z");
  version = (readFileSync(CHART, "utf8").match(/^appVersion:\s*"?([^"\s]+)"?/m) || [])[1];
  if (!version) die("could not read appVersion from Chart.yaml — pass --version X.Y.Z");
}
const [major, minor] = version.split(".");
const section = flag("--section") || `## ${major}.${minor}.x maintenance fixes`;

// Pending fragments: every *.md under docs/changelog.d/ except the README convention doc.
// Sorted by numeric PR prefix (then name) so assembly order is deterministic and PR-ordered.
const fragments = existsSync(FRAG_DIR)
  ? readdirSync(FRAG_DIR)
      .filter((f) => f.endsWith(".md") && f !== "README.md")
      .sort((a, b) => (parseInt(a) || 0) - (parseInt(b) || 0) || a.localeCompare(b))
  : [];

if (fragments.length === 0) {
  console.log("changelog-collect: no pending fragments in docs/changelog.d/ — nothing to assemble.");
  process.exit(0);
}

const blocks = fragments.map((f) => ({
  file: f,
  body: readFileSync(join(FRAG_DIR, f), "utf8").replace(/\s+$/, ""),
}));

if (check) {
  console.log(`changelog-collect --check: ${blocks.length} pending fragment(s) for section "${section}":`);
  for (const b of blocks) console.log(`\n  ── ${b.file} ──\n${b.body.split("\n").map((l) => "  " + l).join("\n")}`);
  console.log(`\n(preview only — nothing written; run without --check at release to assemble.)`);
  process.exit(3); // nonzero: "fragments are pending", a usable CI/release-checklist signal
}

// Insert the fragment bodies at the END of the named section (append after its last line, just
// before the next "## " level-2 heading or EOF). Everything else — including the docs-reflects
// stamp — is preserved byte-for-byte.
const lines = readFileSync(CHANGELOG, "utf8").split("\n");
const start = lines.findIndex((l) => l.trim() === section);
if (start < 0) {
  die(`section heading not found in changelog.mdx: "${section}".\n` +
      `   Create it (a deliberate act at a minor bump), or pass --section "## …".`);
}
let end = lines.length;
for (let i = start + 1; i < lines.length; i++) {
  if (/^## /.test(lines[i])) { end = i; break; }
}
// Trim trailing blank lines inside the section, then append each fragment as its own block.
let insertAt = end;
while (insertAt > start + 1 && lines[insertAt - 1].trim() === "") insertAt--;
const additions = [];
for (const b of blocks) { additions.push("", b.body); }
lines.splice(insertAt, 0, ...additions);

writeFileSync(CHANGELOG, lines.join("\n"));
for (const b of blocks) rmSync(join(FRAG_DIR, b.file));

console.log(`changelog-collect: assembled ${blocks.length} fragment(s) into "${section}" of docs/docs/changelog.mdx:`);
for (const b of blocks) console.log(`  ✓ ${basename(b.file)} (removed)`);
console.log(`  docs-reflects stamp untouched — advance it in the version-bump so gate:docs-version stays green.`);
