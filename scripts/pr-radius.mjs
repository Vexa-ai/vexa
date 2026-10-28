// pr-radius — the blast radius of a pull request, from its diff paths alone. The merge card
// (scripts/merge-card-gate.mjs) reads it to decide WHO may sign the architecture and security
// passes: on a SMALL radius the independent reviewer agent's passes count (vexa-reviewer.yml); on a
// LARGE radius only a maintainer's do. The founder's rule, 28 Oct 2026: CI, docs, tests and one
// service with no database and no contract are small; the database, contracts, the bot protocol,
// billing and auth are the maintainer's.
//
//   LARGE — any path of a LARGE KIND (below), or runtime code in two or more UNITS, or a path this
//           file cannot place. A path nobody classified goes to the maintainer, never to the agent.
//   SMALL — everything else: CI, docs and tests in any number, plus runtime code in at most one unit.
//
// A UNIT is one deployable or shippable piece: a service (`core/<domain>/services/<name>/`), the
// rest of a domain (`core/<domain>/`), a client, an integration, an SDK, a tool. Docs, tests and
// CI are NEUTRAL: they never count as a unit, so a test-only change across five services is small.
//
// Deterministic and pure over the path list (a rename contributes both its old and new path), so
// it is unit-tested against fixtures (scripts/pr-radius.test.mjs). Changing this file changes a
// merge gate, so this file is itself a LARGE path (kind `gate`).
//
// CLI: `node scripts/pr-radius.mjs <path>…` or one path per line on stdin; prints the verdict JSON.

import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";

// The large kinds, in the order the card names them. Each test is over the full repo-relative path.
// Tests and docs are NOT exempt here: a migration test or a contract's README is still that kind.
export const LARGE_KINDS = [
  ["database", (p) =>
    /(^|\/)(migrations?|alembic)(\/|\.ini$)/i.test(p) ||
    /(^|\/)MIGRATION-[^/]*$/i.test(p) ||
    /(^|\/)schema\//.test(p) ||                       // admin-api's schema/ (models, sync, migrations)
    /(^|\/)[^/]*(models?|schema|db|database)\.py$/i.test(p) ||
    /\.sql$/i.test(p) ||
    /(^|\/)schema\.seal\.json$/.test(p) ||
    /(^|\/)(db|database)\//i.test(p) && !/^docs\//.test(p)],
  ["contracts", (p) =>
    /(^|\/)contracts?\//i.test(p) ||
    /\.schema\.json$/i.test(p) ||
    /(^|\/)goldens?(\/|\.|$)/i.test(p) ||
    /(^|\/)contracts\.seal\.json$/.test(p) ||
    /(^|\/)(openapi|asyncapi)[^/]*\.(json|ya?ml)$/i.test(p)],
  ["bot protocol", (p) =>
    /(^|[\/_.-])protocol([\/_.-]|$)/i.test(p) ||
    /capture[._-]?v\d/i.test(p) ||
    /(^|\/)bot_spawn\//.test(p)],
  ["seam", (p) => /^core\/seam\//.test(p)],             // the cross-domain seams (status ws, webhook signing)
  ["billing", (p) => /(billing|stripe|metering|invoice|pricing|entitlement)/i.test(p)],
  ["auth", (p) =>
    /^core\/identity\//.test(p) ||
    /^core\/agent\/services\/credential-broker\//.test(p) ||
    /(^|[\/_.-])(auth|authn|authz|oidc|oauth2?|saml|sso|jwt|credentials?|api[_-]?keys?|sessions?|permissions?|rbac|secrets?)([\/_.-]|$)/i.test(p)],
  ["gateway", (p) => /^core\/gateway\/services\/gateway\//.test(p) && !isTest(p) && !isDoc(p)],
  ["gate", (p) =>
    /^scripts\/(merge-card|pr-radius|vexa-reviewer|gates|check-|test-tree)[^/]*$/.test(p) ||
    /^scripts\/[^/]*\.allow\.json$/.test(p) ||
    /(^|\/)[^/]*\.seal\.json$/.test(p) ||
    /^\.github\/workflows\/(merge-card[^/]*|vexa-reviewer|gates|contribution-rights|docs-current|pr-value|dco[^/]*|codeql|cve-scanning|dependency-review|image-cve-scan)\.ya?ml$/.test(p) ||
    /^\.github\/(CODEOWNERS|dependabot\.ya?ml|actions\/)/.test(p) ||
    /^(CODEOWNERS|SECURITY\.md|security-insights\.yml|license-exceptions\.json|image-licenses\.json)$/.test(p) ||
    /^(osv-scanner\.toml|\.trivyignore[^/]*|trivy[^/]*\.ya?ml|\.gitleaks[^/]*|\.gitguardian[^/]*|\.semgrep[^/]*)$/.test(p) ||
    /^(security|CLA)\//.test(p)],
  ["deploy", (p) => /^deploy\//.test(p) || /(^|\/)(helm|charts?)\//i.test(p) || /(^|\/)Chart\.ya?ml$/.test(p)],
  ["release", (p) => /^(release|releases)\//.test(p) || /^\.github\/workflows\/(release-[^/]*|latest-drift)\.ya?ml$/.test(p)],
  ["shared", (p) =>
    /^(libs|packages)\//.test(p) ||
    /^(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|turbo\.json|tsconfig\.base\.json|Makefile|\.npmrc|\.nvmrc)$/.test(p)],
];

// Tests: a test directory, or a file named as a test, anywhere — plus the repo's test runner.
export function isTest(p) {
  return /(^|\/)(tests?|__tests__|e2e|fixtures?|testdata)\//.test(p) ||
    /(^|\/)test_[^/]*\.py$/.test(p) || /_test\.(py|go)$/.test(p) ||
    /\.(test|spec)\.[cm]?[jt]sx?$/.test(p) ||
    /^tests3\//.test(p);
}

// Docs: the docs site, ADRs, and prose files anywhere.
export function isDoc(p) {
  return /^docs\//.test(p) || /\.(md|mdx|txt|rst)$/i.test(p) || /(^|\/)(LICENSE|NOTICE|AUTHORS)[^/]*$/.test(p) ||
    /^(assets|behavior|calm|carve)\//.test(p) || /\.(png|jpe?g|gif|svg|webp)$/i.test(p);
}

// CI: workflow definitions and repo-level dotfiles that configure tooling, not product.
export function isCi(p) {
  return /^\.github\//.test(p) || /^\.githooks\//.test(p) || /^\.claude-plugin\//.test(p) ||
    /^\.(gitignore|gitattributes|editorconfig|dockerignore|prettierrc[^/]*|eslintrc[^/]*)$/.test(p) ||
    /^scripts\//.test(p);
}

// The unit a runtime path belongs to, or null when the path is in no known unit (→ LARGE).
export function unitOf(p) {
  let m;
  if ((m = p.match(/^core\/([^/]+)\/services\/([^/]+)\//))) return `core/${m[1]}/services/${m[2]}`;
  if ((m = p.match(/^core\/([^/]+)\//))) return `core/${m[1]}`;
  if ((m = p.match(/^(clients|services|integrations|sdks|tools)\/([^/]+)\//))) return `${m[1]}/${m[2]}`;
  return null;
}

// The verdict over a PR's paths. Returns { radius, kinds: {kind: [paths]}, units: [..], why }.
export function classify(paths) {
  const list = [...new Set((paths || []).filter(Boolean).map(String))].sort();
  const kinds = {};
  const units = new Set();
  const unplaced = [];
  for (const p of list) {
    const hit = LARGE_KINDS.filter(([, test]) => test(p)).map(([k]) => k);
    for (const k of hit) (kinds[k] ||= []).push(p);
    if (hit.length || isTest(p) || isDoc(p) || isCi(p)) continue;
    const u = unitOf(p);
    if (u) units.add(u);
    else unplaced.push(p);
  }
  if (unplaced.length) kinds.unclassified = unplaced;
  const unitList = [...units].sort();
  const reasons = Object.entries(kinds).map(([k, ps]) => `${k} (${ps.length} file${ps.length === 1 ? "" : "s"}: ${ps.slice(0, 3).join(", ")}${ps.length > 3 ? ", …" : ""})`);
  if (unitList.length > 1) reasons.push(`runtime code in ${unitList.length} units (${unitList.join(", ")})`);
  if (!list.length) return { radius: "large", kinds: { empty: [] }, units: [], why: "no changed paths could be read" };
  if (reasons.length) return { radius: "large", kinds, units: unitList, why: reasons.join("; ") };
  return {
    radius: "small", kinds, units: unitList,
    why: unitList.length ? `one unit (${unitList[0]}), plus CI, docs or tests` : "CI, docs or tests only",
  };
}

// The paths of a PR's files listing (`repos/:r/pulls/:n/files` entries): a rename brings its old path.
export function pathsOfFiles(files) {
  return (files || []).flatMap((f) => [f.filename, f.previous_filename].filter(Boolean));
}

if (import.meta.url === pathToFileURL(process.argv[1] || "").href) {
  const args = process.argv.slice(2);
  const paths = args.length ? args : readFileSync(0, "utf8").split("\n").map((s) => s.trim()).filter(Boolean);
  console.log(JSON.stringify(classify(paths), null, 2));
}
