// Gating controls for release-validate's `resolve` job (#1237).
//
// `release-images` concluded FAILURE on every successful rc freeze because the nested
// `validate / resolve` leg pinned the sha256 of a candidate map that cannot exist until that
// very build publishes the images. These tests hold the repaired gating in place from both
// sides:
//
//   fail-closed   an unreviewed version still fails when a promote is requested, when the
//                 version is stable, or when a committed map already names it
//   fail-open     ONLY in the freeze window — a prerelease, promote:false, no reviewed arm,
//                 no committed map naming it — and the deferral is VISIBLE
//
// The decision block is bash embedded in YAML, so these tests EXECUTE it rather than
// pattern-matching it: the step body is lifted out of the workflow and run under bash with the
// inputs GitHub Actions would supply. A regex suite would pass on a gate that no longer gates.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const VALIDATE_WORKFLOW = new URL(
  "../.github/workflows/release-validate.yml",
  import.meta.url,
);
const IMAGES_WORKFLOW = new URL(
  "../.github/workflows/release-images.yml",
  import.meta.url,
);

const validateYaml = readFileSync(VALIDATE_WORKFLOW, "utf8");
const imagesYaml = readFileSync(IMAGES_WORKFLOW, "utf8");

const IDENTITY_STEP = "Resolve the packet-bound candidate-map identity";

/** Lift a step's `run: |` body out of a workflow file, dedented to column 0. */
function extractRunBlock(yaml, stepName) {
  const lines = yaml.split("\n");
  const start = lines.findIndex((line) => line.trim() === `- name: ${stepName}`);
  assert.ok(start >= 0, `step not found: ${stepName}`);
  let cursor = start;
  while (cursor < lines.length && !/^\s*run: \|\s*$/.test(lines[cursor])) cursor += 1;
  assert.ok(cursor < lines.length, `step has no literal run block: ${stepName}`);
  const bodyIndent = lines[cursor].search(/\S/) + 2;
  const body = [];
  for (let index = cursor + 1; index < lines.length; index += 1) {
    const line = lines[index];
    if (line.trim() === "") {
      body.push("");
      continue;
    }
    if (line.search(/\S/) < bodyIndent) break;
    body.push(line.slice(bodyIndent));
  }
  return body.join("\n");
}

const identityScript = extractRunBlock(validateYaml, IDENTITY_STEP);

/**
 * Run the identity gate exactly as the runner would: a temp workdir standing in for the
 * checkout, `$GITHUB_ENV` / `$GITHUB_OUTPUT` as real files, the step's env as GitHub renders it.
 */
function runIdentityGate(t, { version, promote = false, prerelease, map = null }) {
  const dir = mkdtempSync(join(tmpdir(), "release-validate-gate-"));
  t.after(() => rmSync(dir, { recursive: true, force: true }));

  if (map) {
    const base = version.replace(/^v/, "").split("-")[0];
    mkdirSync(join(dir, "releases", `v${base}`), { recursive: true });
    writeFileSync(
      join(dir, "releases", `v${base}`, "candidate-images.json"),
      JSON.stringify(map, null, 2),
    );
  }

  const githubEnv = join(dir, "github.env");
  const githubOutput = join(dir, "github.output");
  writeFileSync(githubEnv, "");
  writeFileSync(githubOutput, "");

  const result = spawnSync("bash", ["-c", identityScript], {
    cwd: dir,
    encoding: "utf8",
    env: {
      ...process.env,
      VERSION: version,
      PRERELEASE: String(prerelease),
      PROMOTE: String(promote),
      GITHUB_ENV: githubEnv,
      GITHUB_OUTPUT: githubOutput,
    },
  });

  const parse = (file) =>
    Object.fromEntries(
      readFileSync(file, "utf8")
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          const at = line.indexOf("=");
          return [line.slice(0, at), line.slice(at + 1)];
        }),
    );

  return {
    status: result.status,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    outputs: parse(githubOutput),
    exported: parse(githubEnv),
  };
}

const mapFor = (candidateTag, stableTag) => ({
  schema_version: 1,
  release: stableTag,
  stable_tag: stableTag,
  candidate_tag: candidateTag,
});

// ── the freeze window: the only shape allowed to defer ─────────────────────────────────────────

test("a freeze build of an unreviewed rc no longer fails — identity is deferred, not asserted", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.23-rc.22",
    promote: false,
    prerelease: true,
  });
  assert.equal(run.status, 0, run.stdout + run.stderr);
  assert.equal(run.outputs.enforce, "false");
  assert.match(run.stdout, /identity verification deferred/i);
});

test("a stale map naming an EARLIER candidate does not make the new candidate's identity knowable", (t) => {
  // The exact rc.22-after-rc.21 shape: releases/v0.12.23/candidate-images.json exists on the
  // branch, but it describes rc.20. Treating "a map file exists" as "identity is knowable"
  // would reproduce #1237 one commit later.
  const run = runIdentityGate(t, {
    version: "v0.12.23-rc.22",
    promote: false,
    prerelease: true,
    map: mapFor("v0.12.23-rc.21", "v0.12.23"),
  });
  assert.equal(run.status, 0, run.stdout + run.stderr);
  assert.equal(run.outputs.enforce, "false");
});

// ── everything else still fails closed ─────────────────────────────────────────────────────────

test("a promote request on an unreviewed version still fails closed", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.23-rc.22",
    promote: true,
    prerelease: true,
  });
  assert.notEqual(run.status, 0);
  assert.match(run.stdout, /has no reviewed, packet-bound candidate-map identity/);
  assert.match(run.stdout, /a promote was requested/);
});

test("a stable version with no reviewed identity still fails closed", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.24",
    promote: false,
    prerelease: false,
  });
  assert.notEqual(run.status, 0);
  assert.match(run.stdout, /has no reviewed, packet-bound candidate-map identity/);
  assert.match(run.stdout, /stable version/);
});

test("a committed map that NAMES this candidate makes identity knowable — unreviewed fails closed", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.23-rc.22",
    promote: false,
    prerelease: true,
    map: mapFor("v0.12.23-rc.22", "v0.12.23"),
  });
  assert.notEqual(run.status, 0);
  assert.match(run.stdout, /has no reviewed, packet-bound candidate-map identity/);
  assert.match(run.stdout, /already names v0\.12\.23-rc\.22/);
});

test("a committed map whose STABLE tag is this version fails closed when unreviewed", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.24",
    promote: false,
    prerelease: false,
    map: mapFor("v0.12.24-rc.1", "v0.12.24"),
  });
  assert.notEqual(run.status, 0);
  assert.match(run.stdout, /has no reviewed, packet-bound candidate-map identity/);
});

// ── the reviewed path is untouched ─────────────────────────────────────────────────────────────

test("a reviewed version enforces and hands the pinned packet identity to the validator", (t) => {
  const run = runIdentityGate(t, {
    version: "v0.12.23-rc.21",
    promote: false,
    prerelease: true,
  });
  assert.equal(run.status, 0, run.stdout + run.stderr);
  assert.equal(run.outputs.enforce, "true");
  assert.deepEqual(run.exported, {
    CANDIDATE_MAP: "releases/v0.12.23/candidate-images.json",
    EXPECTED_MAP_STABLE_TAG: "v0.12.23",
    EXPECTED_MAP_SHA256:
      "c5a310465b9005573c9fef79534e34a7822447d6c7004f24271ed59df61fe2a6",
    EXPECTED_TOP_DESCRIPTORS: "10",
    EXPECTED_PLATFORM_IDENTITIES: "19",
  });
});

test("the pinned resolve arms match the committed candidate maps they claim", () => {
  const arms = [...validateYaml.matchAll(
    /CANDIDATE_MAP=(\S+)\n\s+EXPECTED_MAP_STABLE_TAG=(\S+)\n\s+EXPECTED_MAP_SHA256=([0-9a-f]{64})/g,
  )];
  assert.ok(arms.length >= 2, "expected the reviewed-identity table to hold pinned arms");
  for (const [, mapPath, stableTag] of arms) {
    const map = JSON.parse(
      readFileSync(new URL(`../${mapPath}`, import.meta.url), "utf8"),
    );
    assert.equal(map.stable_tag, stableTag, mapPath);
  }
});

// ── the deferral must be visible, and the floor must be unconditional ──────────────────────────

test("the deferral is announced as a notice AND written to the run summary", () => {
  const deferral = extractRunBlock(validateYaml, "Identity verification deferred — say so, loudly");
  assert.match(deferral, /::notice title=candidate-map identity DEFERRED::/);
  assert.match(deferral, /GITHUB_STEP_SUMMARY/);
  assert.match(deferral, /standalone/);
});

test("the guarantee block renders the deferral instead of claiming a proof it does not have", () => {
  assert.match(validateYaml, /R_MAP_IDENTITY: \$\{\{ needs\.resolve\.outputs\.identity_verified \}\}/);
  assert.match(validateYaml, /map_identity\(\) \{[\s\S]*DEFERRED[\s\S]*\}/);
  assert.match(
    validateYaml,
    /Every artifact users pull is the artifact we proved \| \$\(m "\$R_RESOLVE"\) manifests resolve\$\(map_identity\)/,
  );
});

test("the version-agnostic manifest floor runs on EVERY version, reviewed or not", () => {
  // #949 replaced this floor with the version-pinned map check; #1237 restored it. It is what
  // makes a deferred identity check safe, so it must never acquire an `if:`.
  const step = validateYaml.match(
    /- name: Every published manifest resolves and carries required platforms\n([\s\S]*?)\n {6}- (?:name|uses):/,
  );
  assert.ok(step, "the always-on manifest floor is missing from resolve");
  assert.doesNotMatch(step[1].split("run: |")[0], /^\s+if:/m);
  assert.match(step[1], /missing required platform manifest\(s\)/);
  assert.match(step[1], /for img in \$ALL_IMAGES/);
});

test("the identity check is the only thing the freeze path may skip", () => {
  const resolveJob = validateYaml.match(/\n {2}resolve:\n([\s\S]*?)\n {2}# ─/)[1];
  const conditional = [...resolveJob.matchAll(/^ {6}- name: (.+)\n(?: {8}.+\n)*? {8}if: (.+)$/gm)]
    .map(([, name]) => name);
  assert.deepEqual(conditional, [
    "Every frozen descriptor resolves through authenticated registry reads",
    "Identity verification deferred — say so, loudly",
  ]);
});

// ── promote stays fully fail-closed, on its own independent table ──────────────────────────────

test("promote keeps its own reviewed stable-promotion map identity, fail-closed on unknown versions", () => {
  const promoteJob = validateYaml.slice(validateYaml.indexOf("\n  promote:"));
  assert.match(promoteJob, /has no reviewed stable-promotion map identity[\s\S]*?exit 1/);
  assert.match(promoteJob, /--expected-map-sha256 "\$EXPECTED_MAP_SHA256"/);
  assert.match(promoteJob, /--candidate-map "releases\/\$VERSION\/candidate-images\.json"/);
  // Prereleases and non-promote runs can never reach it, whatever resolve decided.
  assert.match(
    validateYaml,
    /if: needs\.resolve\.outputs\.prerelease == 'false' && inputs\.promote/,
  );
  assert.match(promoteJob, /environment: release-promote/);
  for (const gate of ["witness-gate", "value-gate"]) {
    assert.ok(promoteJob.includes(`- ${gate}`), `promote lost its ${gate} dependency`);
  }
});

test("stable-tag aliasing still proves the packet map before any tag moves", () => {
  // release-images' alias-candidate job is the other place a candidate becomes a stable tag.
  // It must keep refusing on any drift from the committed map.
  assert.match(imagesYaml, /node release\/candidate-image-map\.mjs check "\$MAP" "\$VERSION"/);
  assert.match(imagesYaml, /refusing to overwrite \$stable at \$stable_digest \(packet pins \$digest\)/);
  assert.match(imagesYaml, /readback \$readback != witnessed \$digest/);
  assert.match(imagesYaml, /if: needs\.preflight\.outputs\.reuse_candidate == 'true'/);
});

test("the freeze build still hands off to release-validate, and still never promotes", () => {
  const validateCall = imagesYaml.slice(imagesYaml.indexOf("\n  validate:\n"));
  assert.match(validateCall, /uses: \.\/\.github\/workflows\/release-validate\.yml/);
  assert.match(validateCall, /promote: false/);
  assert.doesNotMatch(validateCall, /promote: true/);
});
