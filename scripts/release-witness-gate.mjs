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
import { pathToFileURL } from "node:url";

const IS_MAIN = import.meta.url === pathToFileURL(process.argv[1] || "").href;

const nonEmpty = (v) => typeof v === "string" && v.trim().length > 0;
const placeholder = (v) => /^NAME THE PROOF|^LIVE —/i.test((v || "").trim());

// Any `sha256:` in the deployment fields must carry the FULL 64-hex digest. The deployment field's
// whole job is pinning WHICH BYTES the human witnessed — a truncated prefix (v0.12.10 shipped
// `sha256:a3128453…`, #713) is only evidence after a registry lookup, which is not what a
// guarantee-line-7 artifact is for. A shorter hex run, or a full run trailed by `…`/`...`, fails.
function checkDigests(field, s, errs) {
  const re = /sha256:([0-9a-fA-F]*)/g;
  let m;
  while ((m = re.exec(s))) {
    const rest = s.slice(m.index + m[0].length);
    if (m[1].length !== 64 || /^(…|\.\.\.)/.test(rest))
      errs.push(`${field}: truncated image digest "${m[0].slice(0, 20)}…" — record the FULL 64-hex sha256 (the receipt pins the witnessed bytes; evidence must not need a registry lookup)`);
  }
}
function checkDigestFields(field, v, errs) {
  if (typeof v === "string") checkDigests(field, v, errs);
  else if (v && typeof v === "object") for (const [k, x] of Object.entries(v)) checkDigestFields(`${field}.${k}`, x, errs);
}

// Pure receipt validation (no fs/env) — the RED/GREEN self-test surface (release-witness-gate.test.mjs).
// Returns { errs, coverage }; coverage is non-null only when the values scan ran clean (the CLI's
// coverage line prints exactly then, as before).
export function validateReceipt(r, version) {
  const errs = [];

  if (r.version !== version) errs.push(`version "${r.version}" ≠ release ${version}`);
  if (r.candidate !== version) errs.push(`candidate "${r.candidate}" ≠ ${version} — must witness the PUBLISHED :${version} images`);
  if (!nonEmpty(r.witnessed_by)) errs.push("witnessed_by is empty — name the human who ran the pass");
  if (!nonEmpty(r.witnessed_at)) errs.push("witnessed_at is empty — ISO date of the pass");
  if (!nonEmpty(r.deployment)) errs.push("deployment is empty — which install shape was witnessed (compose|lite|helm)");
  checkDigestFields("deployment", r.deployment, errs);
  checkDigestFields("witness_deployment", r.witness_deployment, errs);

  // D-L4, enforced at the receipt: the human walks a DELIVERED deployment — provisioned,
  // pre-validated, and instrumented by the agent — never a setup recipe. A receipt with no
  // delivered-deployment record means the human was handed homework, not a URL.
  const wd = r.witness_deployment;
  if (!wd || typeof wd !== "object") {
    errs.push("witness_deployment is missing — the witness walks a DELIVERED deployment: record {url, provisioned_by, prevalidated[]} (D-L4: the human receives a URL, never a setup recipe)");
  } else {
    if (!nonEmpty(wd.url)) errs.push("witness_deployment.url is empty — the UI URL the human actually walked");
    if (!nonEmpty(wd.provisioned_by)) errs.push("witness_deployment.provisioned_by is empty — who/what stood the deployment up");
    if (!Array.isArray(wd.prevalidated) || wd.prevalidated.length === 0 || !wd.prevalidated.every(nonEmpty))
      errs.push("witness_deployment.prevalidated is empty — name the autonomous checks that passed before handover (health, UI, auth, STT)");
  }

  let coverage = null;
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
    if (!errs.length) coverage = { total: r.values.length, live, proxy };
  }

  if (r.signed_off !== true) errs.push("signed_off is not true — the human has not signed the pass");

  return { errs, coverage };
}

function main() {
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

  const { errs, coverage } = validateReceipt(r, VERSION);
  if (coverage) console.error(`  coverage: ${coverage.total} value(s) — ${coverage.live} walked live, ${coverage.proxy} by-proxy.`);

  if (errs.length) fail([`${path} does not fully account for the batch:`, ...errs]);

  console.log(`✓ release-witness-gate — ${VERSION} witnessed by ${r.witnessed_by} on ${r.witnessed_at} (${r.deployment}); all ${r.values.length} batch value(s) resolved.`);
  console.log("  (the receipt is the evidence; the Environment approval on this job is the human gate.)");
}

if (IS_MAIN) main();
