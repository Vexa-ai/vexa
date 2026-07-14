// release-witness-gate — guarantee line 7, enforced. "A human witnessed the assembled value.
// No signature, no release." The receipt is the auditable EVIDENCE artifact; the hard human gate
// is the `release-promote` Environment's required reviewer (a CI file cannot forge an approval).
// This gate refuses to let promote proceed unless a well-formed, version-matched witness receipt
// for exactly this release is committed at releases/<version>/witness.json.
//
// The receipt records the single combined-value witness pass (ship bar): one live session on the
// release candidate — real meeting, real words, every user-visible batch change walked once —
// signed by the human who did it. It is generated as a template from the batch (see
// scripts/release-witness-template.mjs) and filled + committed after the witness pass.
//
// Inputs (env): RELEASE_VERSION (vX.Y.Z). Exit 0 = receipt valid; exit 1 = missing/malformed.

import { existsSync, readFileSync } from "node:fs";

const VERSION = process.env.RELEASE_VERSION;
if (!VERSION) { console.error("release-witness-gate: RELEASE_VERSION is required"); process.exit(2); }

const path = `releases/${VERSION}/witness.json`;
const fail = (lines) => {
  console.error(`::error ::release-witness-gate — ${VERSION} is NOT witnessed. Promote blocked (guarantee line 7).`);
  for (const l of lines) console.error("   " + l);
  process.exit(1);
};

if (!existsSync(path)) {
  fail([
    `no witness receipt at ${path}.`,
    "The release candidate must be witnessed first: on a fresh self-host of the PUBLISHED",
    `:${VERSION} images, admit a bot to a real meeting, speak, confirm the live transcript, and`,
    "walk every user-visible batch value once. Then generate + fill the receipt:",
    `   node scripts/release-witness-template.mjs ${VERSION} > ${path}`,
    "   (fill witnessed_by, evidence.*, values_walked; set signed_off:true) and commit it.",
    "The promote run's Environment approval is the second half of the gate — both are required.",
  ]);
}

let r;
try { r = JSON.parse(readFileSync(path, "utf8")); }
catch (e) { fail([`${path} is not valid JSON — ${e.message}`]); }

const errs = [];
const nonEmpty = (v) => typeof v === "string" && v.trim().length > 0;

if (r.version !== VERSION) errs.push(`version "${r.version}" ≠ release ${VERSION} (a stale receipt from another release does not count)`);
if (r.candidate !== VERSION) errs.push(`candidate "${r.candidate}" ≠ ${VERSION} — the receipt must witness the PUBLISHED :${VERSION} images`);
if (!nonEmpty(r.witnessed_by)) errs.push("witnessed_by is empty — name the human who ran the pass");
if (!nonEmpty(r.witnessed_at)) errs.push("witnessed_at is empty — ISO date of the pass");
if (!nonEmpty(r.deployment)) errs.push("deployment is empty — which install shape was witnessed (compose|lite|helm)");
const ev = r.evidence || {};
if (!nonEmpty(ev.meeting_url)) errs.push("evidence.meeting_url is empty — the real meeting the bot joined");
if (!nonEmpty(ev.transcript)) errs.push("evidence.transcript is empty — proof the transcript rendered (segment ids / link / screenshot)");
if (ev.live_stream !== "confirmed") errs.push('evidence.live_stream must be "confirmed" — the live SSE transcript was seen');
if (!Array.isArray(ev.values_walked) || ev.values_walked.length === 0)
  errs.push("evidence.values_walked is empty — list every user-visible batch value experienced once");
if (r.signed_off !== true) errs.push("signed_off is not true — the human has not signed the pass");

if (errs.length) fail([`${path} is malformed:`, ...errs]);

console.log(`✓ release-witness-gate — ${VERSION} witnessed by ${r.witnessed_by} on ${r.witnessed_at} (${r.deployment}); ${r.evidence.values_walked.length} value(s) walked.`);
console.log(`  meeting: ${r.evidence.meeting_url} · transcript: ${r.evidence.transcript} · live-stream: ${r.evidence.live_stream}`);
console.log("  (the receipt is the evidence; the Environment approval on this job is the human gate.)");
