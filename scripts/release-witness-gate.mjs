// release-witness-gate — guarantee line 7, enforced. "A human witnessed the assembled value.
// No signature, no release." The receipt is the auditable EVIDENCE artifact; the hard human gate
// is the `release-promote` Environment's required reviewer (a CI file cannot forge an approval).
// This gate refuses to let promote proceed unless a well-formed, version-matched witness receipt
// for exactly this release is committed at releases/<version>/witness.json.
//
// The receipt is GENERATED FROM THE BATCH (scripts/release-witness-script.mjs) so EVERY PR's value
// is one accounted-for entry — no value can be silently skipped. The human then resolves every
// entry: a user-visible value is WALKED live (witnessed:true + observation); a backend/ci value is
// witnessed BY PROXY (its named test/gate evidence). This gate enforces that coverage: promote is
// blocked until every value in the batch is resolved and the pass is signed.
//
// Inputs (env): RELEASE_VERSION (vX.Y.Z). Exit 0 = valid; 1 = missing/unwitnessed; 2 = usage.

import { existsSync, readFileSync } from "node:fs";

const VERSION = process.env.RELEASE_VERSION;
if (!VERSION) { console.error("release-witness-gate: RELEASE_VERSION is required"); process.exit(2); }

const path = `releases/${VERSION}/witness.json`;
const fail = (lines) => {
  console.error(`::error ::release-witness-gate — ${VERSION} is NOT fully witnessed. Promote blocked (guarantee line 7).`);
  for (const l of lines) console.error("   " + l);
  process.exit(1);
};

if (!existsSync(path)) {
  fail([
    `no witness receipt at ${path}. Generate it from the batch, then witness + sign:`,
    `   RELEASE_VERSION=${VERSION} GITHUB_REPOSITORY=<owner/repo> node scripts/release-witness-script.mjs > ${path}`,
    "It lists EVERY batch PR. Walk each user-visible value live (set witnessed:true + observation);",
    "each backend/ci value is by-proxy (its named evidence). Fill witnessed_by/at/deployment,",
    "set signed_off:true, commit. The promote Environment approval is the second half of the gate.",
  ]);
}

let r;
try { r = JSON.parse(readFileSync(path, "utf8")); }
catch (e) { fail([`${path} is not valid JSON — ${e.message}`]); }

const errs = [];
const nonEmpty = (v) => typeof v === "string" && v.trim().length > 0;
const placeholder = (v) => /^NAME THE PROOF|^LIVE —/i.test((v || "").trim());

if (r.version !== VERSION) errs.push(`version "${r.version}" ≠ release ${VERSION}`);
if (r.candidate !== VERSION) errs.push(`candidate "${r.candidate}" ≠ ${VERSION} — must witness the PUBLISHED :${VERSION} images`);
if (!nonEmpty(r.witnessed_by)) errs.push("witnessed_by is empty — name the human who ran the pass");
if (!nonEmpty(r.witnessed_at)) errs.push("witnessed_at is empty — ISO date of the pass");
if (!nonEmpty(r.deployment)) errs.push("deployment is empty — which install shape was witnessed (compose|lite|helm)");

if (!Array.isArray(r.values) || r.values.length === 0) {
  errs.push("values is empty — the receipt must account for every batch PR (regenerate with release-witness-script.mjs)");
} else {
  let live = 0, proxy = 0;
  for (const v of r.values) {
    const id = `#${v.pr || "?"} (${(v.title || "").slice(0, 50)})`;
    if (v.witnessed === "by-proxy") {
      proxy++;
      if (!nonEmpty(v.evidence) || placeholder(v.evidence)) errs.push(`${id}: by-proxy but evidence not named — name the test/leg/gate that proves it`);
    } else {
      live++;
      if (v.witnessed !== true) errs.push(`${id}: user-visible value NOT witnessed — walk it live and set witnessed:true (or convert to by-proxy with named evidence)`);
      if (!nonEmpty(v.observation)) errs.push(`${id}: no observation recorded — state what you actually saw`);
      if (!nonEmpty(v.pass) || placeholder(v.pass)) errs.push(`${id}: pass criterion not filled — what counted as a pass`);
    }
  }
  if (!errs.length) console.error(`  coverage: ${r.values.length} value(s) — ${live} walked live, ${proxy} by-proxy.`);
}

if (r.signed_off !== true) errs.push("signed_off is not true — the human has not signed the pass");

if (errs.length) fail([`${path} does not fully account for the batch:`, ...errs]);

console.log(`✓ release-witness-gate — ${VERSION} witnessed by ${r.witnessed_by} on ${r.witnessed_at} (${r.deployment}); all ${r.values.length} batch value(s) resolved.`);
console.log("  (the receipt is the evidence; the Environment approval on this job is the human gate.)");
