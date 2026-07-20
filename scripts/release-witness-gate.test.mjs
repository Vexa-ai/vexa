// Unit tests for the receipt validation in release-witness-gate.mjs (issue #713).
// Run: node --test scripts/release-witness-gate.test.mjs   (offline — fixtures + a temp-dir CLI spawn)
//
// The regression: v0.12.10 shipped a SIGNED receipt whose deployment field recorded the witnessed
// image as `sha256:a3128453…` — 8 hex chars and a literal ellipsis. The field's whole job is pinning
// which bytes the human witnessed; a prefix is only evidence after a registry lookup. The fix makes
// the gate demand the full 64-hex digest after every `sha256:` in deployment/witness_deployment.

import test from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { validateReceipt } from "./release-witness-gate.mjs";

const V = "v9.9.9";
const FULL_A = "a".repeat(64);
const FULL_B = "b".repeat(64);
// A minimal receipt that passes every rule — each test mutates one field off it.
const receipt = (o) => ({
  version: V, candidate: V,
  witnessed_by: "Test Witness", witnessed_at: "2026-07-17",
  deployment: `lite (image index sha256:${FULL_A} (linux/amd64 image sha256:${FULL_B}))`,
  values: [{ pr: "1", title: "backend value", visibility: "backend", witnessed: "by-proxy", evidence: "scripts/x.test.mjs" }],
  // D-L4 (#709): the witness walks a DELIVERED deployment — the record is required, so the
  // all-green fixture carries one; the D-L4 tests below mutate it away.
  witness_deployment: { url: "http://test:3001", provisioned_by: "agent (test VM)", prevalidated: ["gateway /health 200"] },
  signed_off: true,
  ...o,
});
const digestErrs = (r) => validateReceipt(r, V).errs.filter((e) => /truncated image digest/.test(e));

// ── the truncated-digest rule — RED shapes ──────────────────────────────────────────────────────

test("RED: 8-hex prefix + ellipsis in deployment (the shipped v0.12.10 shape) flags, naming the field", () => {
  const errs = digestErrs(receipt({ deployment: "lite (throwaway Linode VM, LOCAL_STT=1, image digest sha256:a3128453…)" }));
  assert.equal(errs.length, 1);
  assert.match(errs[0], /^deployment: /);
  assert.match(errs[0], /FULL 64-hex/);
});

test("RED: three-dot ellipsis fails the same way as the … character", () => {
  assert.equal(digestErrs(receipt({ deployment: "lite (image sha256:a3128453...)" })).length, 1);
});

test("RED: short hex run with no ellipsis still fails (63 chars is not a digest)", () => {
  assert.equal(digestErrs(receipt({ deployment: `lite (image sha256:${"a".repeat(63)})` })).length, 1);
});

test("RED: full 64-hex followed by ellipsis fails (the run must END at 64)", () => {
  assert.equal(digestErrs(receipt({ deployment: `lite (image sha256:${FULL_A}…)` })).length, 1);
});

test("RED: one truncated digest among full ones is enough — every occurrence is checked", () => {
  const errs = digestErrs(receipt({ deployment: `lite (index sha256:${FULL_A} (image sha256:43c9e320…))` }));
  assert.equal(errs.length, 1);
});

test("RED: truncation inside witness_deployment string fields flags with the nested field name", () => {
  const errs = digestErrs(receipt({ witness_deployment: { url: "http://x", provisioned_by: "agent (vexa-lite sha256:43c9e320…)" } }));
  assert.equal(errs.length, 1);
  assert.match(errs[0], /^witness_deployment\.provisioned_by: /);
});

// ── GREEN shapes ────────────────────────────────────────────────────────────────────────────────

test("GREEN: full 64-hex digests (index + platform image) pass clean", () => {
  const { errs, coverage } = validateReceipt(receipt({}), V);
  assert.deepEqual(errs, []);
  assert.deepEqual(coverage, { total: 1, live: 0, proxy: 1 });
});

test("GREEN: a receipt with no sha256: anywhere (the v0.12.4/v0.12.9 shape) passes untouched", () => {
  const { errs } = validateReceipt(receipt({ deployment: "lite (throwaway Linode VM, LOCAL_STT=1)" }), V);
  assert.deepEqual(errs, []);
});

// ── D-L4 (#709): the witness walks a DELIVERED deployment — RED without the record ──────────────

const dl4Errs = (r) => validateReceipt(r, V).errs.filter((e) => /witness_deployment/.test(e) && !/truncated/.test(e));

test("RED (D-L4): no witness_deployment at all — the human was handed homework, not a URL", () => {
  const errs = dl4Errs(receipt({ witness_deployment: undefined }));
  assert.equal(errs.length, 1);
  assert.match(errs[0], /witness_deployment is missing/);
  assert.match(errs[0], /never a setup recipe/);
});

test("RED (D-L4): empty url / provisioned_by / prevalidated each flag by name", () => {
  const errs = dl4Errs(receipt({ witness_deployment: { url: "", provisioned_by: "", prevalidated: [] } }));
  assert.equal(errs.length, 3);
  assert.match(errs[0], /witness_deployment\.url is empty/);
  assert.match(errs[1], /witness_deployment\.provisioned_by is empty/);
  assert.match(errs[2], /witness_deployment\.prevalidated is empty/);
});

// ── CLI: exit codes against the COMMITTED v0.12.10 receipt (temp-dir spawn, no network) ─────────

const SCRIPTS = dirname(fileURLToPath(import.meta.url));
const gate = (dir, version) => {
  try { execFileSync("node", [join(SCRIPTS, "release-witness-gate.mjs")], { cwd: dir, env: { ...process.env, RELEASE_VERSION: version }, stdio: "pipe" }); return { code: 0, err: "" }; }
  catch (e) { return { code: e.status, err: String(e.stderr) }; }
};
const stage = (mutate) => {
  const dir = mkdtempSync(join(tmpdir(), "witness-gate-"));
  mkdirSync(join(dir, "releases/v0.12.10"), { recursive: true });
  const r = JSON.parse(readFileSync(join(SCRIPTS, "../releases/v0.12.10/witness.json"), "utf8"));
  if (mutate) mutate(r);
  writeFileSync(join(dir, "releases/v0.12.10/witness.json"), JSON.stringify(r, null, 2));
  return dir;
};

test("CLI GREEN: the committed v0.12.10 receipt (backfilled digests) exits 0", () => {
  assert.equal(gate(stage(null), "v0.12.10").code, 0);
});

test("CLI RED: reverting the backfill to the truncated prefix exits 1 naming deployment", () => {
  const { code, err } = gate(stage((r) => { r.deployment = "lite (throwaway Linode VM, LOCAL_STT=1, image digest sha256:a3128453…)"; }), "v0.12.10");
  assert.equal(code, 1);
  assert.match(err, /deployment: truncated image digest "sha256:a3128453…"/);
});
