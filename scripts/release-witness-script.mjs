// release-witness-script — generate the witness script for a release FROM ITS BATCH, every time.
//
// The constitution's ship bar says "the release generates a witness script from the batch". This
// enforces exactly that: it enumerates every PR merged since the previous release tag, classifies
// each by what it touches (user-visible + platform / backend / ci-governance), auto-names the
// machine evidence (the test files + gates the PR added), and writes a witness.json where EVERY
// batch PR is one accounted-for entry. No value can be silently skipped — the entry exists whether
// or not a human remembers it.
//
//   • user-visible  → a live step the human must walk (stub step + pass, filled by the witness);
//                     `witnessed:false` until signed.
//   • backend / ci  → witnessed BY PROXY; `evidence` is the named test/gate the PR shipped.
//
// The human then walks the user-visible steps, records each observation, sets witnessed:true, fills
// witnessed_by/at/deployment, and sets signed_off:true. release-witness-gate.mjs enforces that every
// user-visible entry is witnessed and every backend entry names evidence (full-coverage gate).
//
// Usage: RELEASE_VERSION=vX.Y.Z GITHUB_REPOSITORY=owner/repo node scripts/release-witness-script.mjs
//        [> releases/vX.Y.Z/witness.json]   (writes to stdout)

import { execSync } from "node:child_process";

const REPO = process.env.GITHUB_REPOSITORY;
const VERSION = process.env.RELEASE_VERSION;
if (!REPO || !VERSION) { console.error("release-witness-script: RELEASE_VERSION + GITHUB_REPOSITORY required"); process.exit(2); }

function ghRaw(p) { let e; for (let i = 0; i < 3; i++) { try { return execSync(`gh api "${p}"`, { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] }); } catch (x) { e = x; } } throw e; }
const ghj = (p) => JSON.parse(ghRaw(p));

function parseVer(t) { const m = String(t).match(/^v?(\d+)\.(\d+)\.(\d+)(?:-(.+))?$/); return m ? { core: [+m[1], +m[2], +m[3]], pre: m[4] || null, raw: t } : null; }
function cmpVer(a, b) { for (let i = 0; i < 3; i++) if (a.core[i] !== b.core[i]) return a.core[i] - b.core[i]; if (a.pre === b.pre) return 0; if (!a.pre) return 1; if (!b.pre) return -1; return a.pre < b.pre ? -1 : 1; }
function prevTag() {
  const cur = parseVer(VERSION), tags = [];
  for (let pg = 1; pg <= 10; pg++) { const b = ghj(`repos/${REPO}/tags?per_page=100&page=${pg}`); for (const t of b) { const p = parseVer(t.name); if (p && !p.pre) tags.push(p); } if (b.length < 100) break; }
  const lower = tags.filter((t) => cmpVer(t, cur) < 0).sort(cmpVer); return lower.length ? lower[lower.length - 1].raw : null;
}
function batchPRs(prev) {
  const nums = new Set();
  for (let pg = 1; pg <= 30; pg++) { const c = ghj(`repos/${REPO}/compare/${prev}...${VERSION}?per_page=100&page=${pg}`); const cs = c.commits || []; for (const x of cs) { const m = (x.commit?.message || "").split("\n")[0].match(/\(#(\d+)\)\s*$/); if (m) nums.add(+m[1]); } if (cs.length < 100) break; }
  return [...nums].sort((a, b) => a - b);
}

// Classify a PR from the files it changed. Returns {visibility, platform, evidence[]}.
const PLATFORM = [
  [/modules\/join\/src\/msteams\//, "ms-teams"], [/modules\/join\/src\/jitsi\//, "jitsi"],
  [/modules\/join\/src\/googlemeet\//, "google-meet"], [/modules\/join\/src\/zoom\//, "zoom"],
];
function classify(files) {
  const paths = files.map((f) => f.filename);
  const evidence = paths.filter((p) => /(^|\/)(test_[^/]+\.py|[^/]+\.test\.ts)$|(^|\/)tests?\//.test(p));
  // gate/seal signals count as named evidence too (a gate IS the standing proof).
  for (const p of paths) {
    if (/scripts\/gates\.mjs$/.test(p)) evidence.push("scripts/gates.mjs (gate)");
    if (/\.seal\.json$/.test(p)) evidence.push(`${p} (seal gate)`);
    if (/deploy\/db-budget\.json$/.test(p)) evidence.push("gate:db-budget");
    if (/\.github\/workflows\/[^/]+\.yml$/.test(p)) evidence.push(`${p} (CI leg)`);
  }
  const isTest = (p) => /(test_|\.test\.|\/tests?\/)/.test(p);
  const nonTest = paths.filter((p) => !isTest(p));

  let platform = null;
  for (const [re, name] of PLATFORM) if (paths.some((p) => re.test(p))) { platform = name; break; }

  // user-visible signals
  const botBehavior = nonTest.some((p) => /modules\/join\/src\/|services\/bot\/src\//.test(p));
  const recordings = nonTest.some((p) => /recordings\//.test(p));
  const terminalUi = nonTest.some((p) => /^clients\/terminal\//.test(p));
  const docs = nonTest.some((p) => /^docs\//.test(p));
  // ci-governance: touches ONLY tooling/gates/workflows/seals (no runtime source)
  const onlyCi = nonTest.length > 0 && nonTest.every((p) => /^scripts\/|^\.github\/|\.seal\.json$|^package\.json$|^deploy\/db-budget\.json$/.test(p));
  // config-contract / boot preflight is operator-visible (fail-closed on missing config)
  const bootConfig = nonTest.some((p) => /config_preflight\.py$|config\.v1\.json$/.test(p));

  let visibility;
  if (botBehavior || recordings || terminalUi) visibility = "user-visible";
  else if (bootConfig) visibility = "user-visible";        // operator observes the fail-closed boot
  else if (onlyCi) visibility = "ci-governance";
  else if (docs && nonTest.every((p) => /^docs\//.test(p))) visibility = "docs";
  else visibility = "backend";

  return { visibility, platform, evidence: [...new Set(evidence)] };
}

const prev = prevTag();
if (!prev) { console.error(`::error ::no prior release tag < ${VERSION}; cannot bound the batch`); process.exit(1); }
const prs = batchPRs(prev);
if (!prs.length) { console.error(`::error ::empty batch ${prev}...${VERSION}`); process.exit(1); }

const values = [];
for (const num of prs) {
  const pr = ghj(`repos/${REPO}/pulls/${num}`);
  const title = (pr.title || "").trim();
  if (/^release: .*version bump/i.test(title)) continue;   // release mechanics, not a value
  let files = []; for (let pg = 1; pg <= 10; pg++) { const f = ghj(`repos/${REPO}/pulls/${num}/files?per_page=100&page=${pg}`); files.push(...f); if (f.length < 100) break; }
  const c = classify(files);
  const entry = { pr: String(num), title, visibility: c.visibility };
  if (c.platform) entry.platform = c.platform;
  if (c.visibility === "user-visible") {
    entry.witness_step = `LIVE — witness the delivered value: ${title}. (Fill the exact action + what you observe.)`;
    entry.pass = "";               // the witness fills the pass criterion actually observed
    entry.witnessed = false;
    entry.observation = "";
  } else {
    entry.witnessed = "by-proxy";
    entry.evidence = c.evidence.length ? c.evidence.join(", ") : "NAME THE PROOF (test / validate leg / gate) — none auto-detected";
  }
  values.push(entry);
}

const receipt = {
  version: VERSION,
  candidate: VERSION,
  generated_from: `${prev}...${VERSION}`,
  witnessed_by: "",
  witnessed_at: "",
  deployment: "",
  values,
  signed_off: false,
};
console.error(`release-witness-script — ${values.length} value(s) from ${prev}...${VERSION}: ` +
  `${values.filter((v) => v.witnessed === false).length} user-visible (walk live), ` +
  `${values.filter((v) => v.witnessed === "by-proxy").length} by-proxy.`);
console.log(JSON.stringify(receipt, null, 2));
