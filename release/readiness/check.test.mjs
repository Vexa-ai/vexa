import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  FOUNDER_LEGS,
  IDENTITY_REQUIRED,
  LEG_REQUIRED,
  MANIFEST_REQUIRED,
  RECEIPT_REQUIRED,
  checkReadiness,
  formatReport,
  legsForPhase,
  parseArgs,
  parseManifestYaml,
  stableRelease,
  validateManifest,
  validateReceipt,
} from "./check.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO = join(HERE, "..", "..");
const RELEASE = "v0.12.23";

// ── fixtures ──────────────────────────────────────────────────────────────────────────────────

function validManifest(release = RELEASE) {
  return {
    schema_version: 1,
    release,
    candidate_map: `releases/${release}/candidate-images.json`,
    receipts_dir: `releases/${release}/readiness`,
    legs: [
      {
        id: "train-value",
        leg: "1",
        title: "TRAIN VALUE as of PRs",
        kind: "machine",
        phase: "formation",
        oracle: { command: `RELEASE_VERSION=${release} node scripts/release-value-gate.mjs` },
        receipt: `releases/${release}/readiness/train-value.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the batch range the value gate walked",
          stale_reason: "a re-cut changes which PRs the promote hands users",
        },
      },
      {
        id: "blast-radius",
        leg: "2",
        title: "BLAST RADIUS as of PRs",
        kind: "agent",
        phase: "formation",
        oracle: { protocol: "release/readiness/protocols/blast-radius.md" },
        receipt: `releases/${release}/readiness/blast-radius.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the diff the surface map was built from",
          input_pattern: "^v\\d+\\.\\d+\\.\\d+\\.\\.[0-9a-f]{40}",
          stale_reason: "the surface table describes a diff the re-cut candidate no longer ships",
        },
      },
      {
        id: "full-functionality",
        leg: "3a",
        title: "FULL FUNCTIONALITY — builds and runs",
        kind: "machine",
        phase: "staging",
        oracle: { command: "gh workflow run release-validate.yml" },
        receipt: `releases/${release}/readiness/full-functionality.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the release-validate run URL",
          stale_reason: "the legs pulled published bytes a re-cut replaces",
        },
      },
      {
        id: "api-docs-sweep",
        leg: "3b",
        title: "FULL FUNCTIONALITY — API coverage as of docs",
        kind: "agent",
        phase: "staging",
        oracle: { protocol: "release/readiness/protocols/api-docs-sweep.md" },
        receipt: `releases/${release}/readiness/api-docs-sweep.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the documented surface and the deployment probed",
          stale_reason: "the sweep probed image bytes a re-cut replaces",
        },
      },
      {
        id: "security-review",
        leg: "4",
        title: "SECURITY review",
        kind: "agent",
        phase: "both",
        oracle: { protocol: "release/readiness/protocols/security-review.md" },
        receipt: `releases/${release}/readiness/security-review.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the reviewed diff plus shipped-image dependency state",
          stale_reason: "a re-cut can add a surface or a dependency nobody reviewed",
        },
      },
      {
        id: "compliance-review",
        leg: "5",
        title: "COMPLIANCE review",
        kind: "agent",
        phase: "both",
        oracle: { protocol: "release/readiness/protocols/compliance-review.md" },
        receipt: `releases/${release}/readiness/compliance-review.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the reviewed diff plus the issue/PR roster",
          stale_reason: "custody and contract state are read off the exact commit set",
        },
      },
      {
        id: "promotion-ceremony",
        leg: "6",
        title: "PROMOTION CEREMONY",
        kind: "machine",
        phase: "staging",
        oracle: { command: "make check-promotion-legs" },
        receipt: `releases/${release}/readiness/promotion-ceremony.receipt.json`,
        identity: {
          binds_candidate_map: true,
          input: "the rehearsal packet whose four legs rendered green",
          stale_reason: "the rehearsal rendered the exact candidate digests",
        },
      },
    ],
  };
}

function validReceipt(leg, sha) {
  return {
    schema_version: 1,
    leg: leg.id,
    candidate_map_sha: sha,
    input_identity: "v0.12.22..c93a24374c4337b226f74000dd5ca4d9fbcfe307",
    result: "green",
    findings_ref: "https://github.com/Vexa-ai/vexa/pull/1234",
    generated_at: "2026-08-18T15:57:00Z",
    generated_by: "agent (readiness session)",
  };
}

// A deliberately small emitter: every manifest fixture is authored as an object and rendered
// through the same parser the runner uses, so a mutation test exercises the real read path.
function emitYaml(value, indent = 0) {
  const pad = " ".repeat(indent);
  const scalar = (v) => {
    if (typeof v === "boolean" || typeof v === "number") return String(v);
    if (v === null) return "null";
    const s = String(v);
    const needsQuote =
      s === "" ||
      /^-?\d+$/.test(s) ||
      ["true", "false", "null", "~"].includes(s) ||
      /[:#'"]/.test(s) ||
      /^[[{&*|>%@`-]/.test(s);
    return needsQuote ? `'${s.replace(/'/g, "''")}'` : s;
  };
  const lines = [];
  for (const [key, item] of Object.entries(value)) {
    if (item === undefined) continue;
    if (Array.isArray(item)) {
      lines.push(`${pad}${key}:`);
      for (const entry of item) {
        const body = emitYaml(entry, indent + 4).split("\n");
        lines.push(`${pad}  - ${body[0].trim()}`);
        lines.push(...body.slice(1));
      }
      continue;
    }
    if (item !== null && typeof item === "object") {
      lines.push(`${pad}${key}:`);
      lines.push(emitYaml(item, indent + 2));
      continue;
    }
    lines.push(`${pad}${key}: ${scalar(item)}`);
  }
  return lines.join("\n");
}

function scaffold(manifest, receipts = {}, { mapBody = '{"release":"v0.12.23"}' } = {}) {
  const root = mkdtempSync(join(tmpdir(), "readiness-"));
  const releaseDir = join(root, "releases", manifest.release);
  mkdirSync(join(releaseDir, "readiness"), { recursive: true });
  writeFileSync(join(releaseDir, "candidate-images.json"), mapBody);
  writeFileSync(join(releaseDir, "readiness.yaml"), `${emitYaml(manifest)}\n`);
  for (const [id, receipt] of Object.entries(receipts)) {
    writeFileSync(
      join(releaseDir, "readiness", `${id}.receipt.json`),
      typeof receipt === "string" ? receipt : JSON.stringify(receipt, null, 2),
    );
  }
  return { root, sha: createHash("sha256").update(mapBody).digest("hex") };
}

function allGreen(manifest, sha) {
  return Object.fromEntries(manifest.legs.map((leg) => [leg.id, validReceipt(leg, sha)]));
}

// ── the strict YAML subset ────────────────────────────────────────────────────────────────────

test("parses mappings, nested mappings, sequences of mappings and quoted scalars", () => {
  const doc = parseManifestYaml(
    [
      "# a comment",
      "schema_version: 1",
      "release: v0.12.23",
      "flag: true",
      "empty: null",
      "quoted: 'a: colon # and a hash'",
      'escaped: "say \\"hi\\""',
      "legs:",
      "  - id: one",
      "    leg: '1'",
      "    identity:",
      "      binds_candidate_map: true",
      "  - id: two",
      "    leg: '3a'",
      "",
    ].join("\n"),
  );
  assert.equal(doc.schema_version, 1);
  assert.equal(doc.flag, true);
  assert.equal(doc.empty, null);
  assert.equal(doc.quoted, "a: colon # and a hash");
  assert.equal(doc.escaped, 'say "hi"');
  assert.equal(doc.legs.length, 2);
  assert.equal(doc.legs[0].identity.binds_candidate_map, true);
  assert.equal(doc.legs[1].leg, "3a");
});

test("refuses the YAML it does not model rather than guessing at it", () => {
  assert.throws(() => parseManifestYaml("a:\n\tb: 1\n"), /tabs are not allowed/);
  assert.throws(() => parseManifestYaml("legs: [a, b]\n"), /unsupported YAML construct/);
  assert.throws(() => parseManifestYaml("a: &anchor 1\n"), /unsupported YAML construct/);
  assert.throws(() => parseManifestYaml("a: 1\na: 2\n"), /duplicate key/);
  assert.throws(() => parseManifestYaml("a: 1\n   b: 2\n"), /not a multiple of two/);
  assert.throws(() => parseManifestYaml("a:\n"), /has no value/);
  assert.throws(() => parseManifestYaml("just a bare line\n"), /mapping at the top level/);
  assert.throws(() => parseManifestYaml("a: 1\nbare\n"), /expected "key: value"/);
  assert.throws(() => parseManifestYaml("a: 'unterminated\n"), /unterminated string/);
  assert.throws(() => parseManifestYaml(""), /manifest is empty/);
});

// ── manifest validation ───────────────────────────────────────────────────────────────────────

test("accepts a well-formed manifest and round-trips it through the parser", () => {
  const doc = parseManifestYaml(emitYaml(validManifest()));
  assert.equal(validateManifest(doc, RELEASE).legs.length, 7);
});

test("refuses a manifest that does not cover all six legs of the standard", () => {
  const doc = validManifest();
  doc.legs = doc.legs.filter((leg) => leg.leg !== "4");
  assert.throws(() => validateManifest(doc), /six-leg standard is not covered.*4/s);

  const partial = validManifest();
  partial.legs = partial.legs.filter((leg) => !["3a", "3b"].includes(leg.leg));
  assert.throws(() => validateManifest(partial), /no leg declares 3/);
});

test("refuses a manifest whose binding targets are not this release's frozen artefacts", () => {
  const wrongMap = validManifest();
  wrongMap.candidate_map = "releases/v0.12.22/candidate-images.json";
  assert.throws(() => validateManifest(wrongMap), /candidate_map must be/);

  const wrongDir = validManifest();
  wrongDir.receipts_dir = "releases/v0.12.23/receipts";
  assert.throws(() => validateManifest(wrongDir), /receipts_dir must be/);

  // The fourth place the release number appears is inside a machine leg's oracle. A manifest copied
  // forward with this one left behind still runs — against the PREVIOUS release's batch — and
  // reports green, which is the one failure here that makes no noise at all.
  const staleOracle = validManifest();
  const valueLeg = staleOracle.legs.find((leg) => leg.oracle?.command?.includes("RELEASE_VERSION="));
  valueLeg.oracle.command = valueLeg.oracle.command.replace(/RELEASE_VERSION=\S+/, "RELEASE_VERSION=v0.12.22");
  assert.throws(() => validateManifest(staleOracle), /would run against another release/);

  const wrongVersion = validManifest();
  assert.throws(() => validateManifest(wrongVersion, "v0.12.24"), /does not match requested/);

  const badRelease = validManifest();
  badRelease.release = "v0.12.23-rc.18";
  assert.throws(() => validateManifest(badRelease), /invalid release/);

  const badSchema = validManifest();
  badSchema.schema_version = 2;
  assert.throws(() => validateManifest(badSchema), /schema_version must be 1/);
});

test("a leg's kind and its oracle cannot disagree", () => {
  const machineWithProtocol = validManifest();
  machineWithProtocol.legs[0].oracle = { protocol: "release/readiness/protocols/x.md" };
  assert.throws(() => validateManifest(machineWithProtocol), /machine leg's oracle must be a command/);

  const agentWithCommand = validManifest();
  agentWithCommand.legs[1].oracle = { command: "echo" };
  assert.throws(() => validateManifest(agentWithCommand), /agent leg's oracle must be a protocol/);

  const both = validManifest();
  both.legs[1].oracle = { command: "echo", protocol: "release/readiness/protocols/x.md" };
  assert.throws(() => validateManifest(both), /exactly one of command \| protocol/);

  const neither = validManifest();
  neither.legs[1].oracle = { note: "somebody looked at it" };
  assert.throws(() => validateManifest(neither), /exactly one of command \| protocol/);

  const strayProtocol = validManifest();
  strayProtocol.legs[1].oracle = { protocol: "docs/whatever.md" };
  assert.throws(() => validateManifest(strayProtocol), /release\/readiness\/protocols/);
});

test("every leg must declare a binding, a stated input and a stale reason", () => {
  const unbound = validManifest();
  unbound.legs[3].identity.binds_candidate_map = false;
  assert.throws(() => validateManifest(unbound), /binds_candidate_map must be true/);

  for (const field of IDENTITY_REQUIRED) {
    const doc = validManifest();
    delete doc.legs[3].identity[field];
    assert.throws(() => validateManifest(doc), new RegExp(field));
  }

  const badPattern = validManifest();
  badPattern.legs[1].identity.input_pattern = "([unclosed";
  assert.throws(() => validateManifest(badPattern), /not a valid regular expression/);
});

test("a leg must carry every required field, a unique id, and its own receipt path", () => {
  for (const field of LEG_REQUIRED) {
    const doc = validManifest();
    delete doc.legs[2][field];
    assert.throws(() => validateManifest(doc), new RegExp(field));
  }
  for (const field of MANIFEST_REQUIRED) {
    const doc = validManifest();
    delete doc[field];
    assert.throws(() => validateManifest(doc), new RegExp(field));
  }

  const duplicate = validManifest();
  duplicate.legs[1].id = "train-value";
  assert.throws(() => validateManifest(duplicate), /duplicate leg id/);

  const misfiled = validManifest();
  misfiled.legs[1].receipt = "releases/v0.12.23/readiness/blast.receipt.json";
  assert.throws(() => validateManifest(misfiled), /receipt must be/);

  const badOrdinal = validManifest();
  badOrdinal.legs[1].leg = "7";
  assert.throws(() => validateManifest(badOrdinal), /six ordinals/);

  const badKind = validManifest();
  badKind.legs[1].kind = "human";
  assert.throws(() => validateManifest(badKind), /kind must be/);

  const badPhase = validManifest();
  badPhase.legs[1].phase = "promote";
  assert.throws(() => validateManifest(badPhase), /phase must be/);
});

// ── receipt validation ────────────────────────────────────────────────────────────────────────

test("accepts a well-formed receipt and refuses a malformed identity", () => {
  const leg = validManifest().legs[1];
  const sha = "a".repeat(64);
  assert.equal(validateReceipt(validReceipt(leg, sha), leg).result, "green");

  for (const field of RECEIPT_REQUIRED) {
    const receipt = validReceipt(leg, sha);
    delete receipt[field];
    assert.throws(() => validateReceipt(receipt, leg), new RegExp(field));
  }

  const shortSha = validReceipt(leg, "abc");
  assert.throws(() => validateReceipt(shortSha, leg), /lowercase 64-hex/);

  const upperSha = validReceipt(leg, "A".repeat(64));
  assert.throws(() => validateReceipt(upperSha, leg), /lowercase 64-hex/);

  const wrongLeg = validReceipt(leg, sha);
  wrongLeg.leg = "security-review";
  assert.throws(() => validateReceipt(wrongLeg, leg), /does not match the manifest leg/);

  const badResult = validReceipt(leg, sha);
  badResult.result = "mostly";
  assert.throws(() => validateReceipt(badResult, leg), /result must be one of/);

  const badTime = validReceipt(leg, sha);
  badTime.generated_at = "2026-08-18";
  assert.throws(() => validateReceipt(badTime, leg), /ISO-8601/);
});

test("a receipt's input identity must have the shape its leg declares", () => {
  const leg = validManifest().legs[1];
  const sha = "a".repeat(64);

  const abbreviated = validReceipt(leg, sha);
  abbreviated.input_identity = "v0.12.22..c93a2437";
  assert.throws(() => validateReceipt(abbreviated, leg), /does not match the shape this leg declares/);

  const vague = validReceipt(leg, sha);
  vague.input_identity = "the diff";
  assert.throws(() => validateReceipt(vague, leg), /does not match the shape/);
});

// ── phase selection ───────────────────────────────────────────────────────────────────────────

test("formation requires its own legs; staging requires all six", () => {
  const doc = validManifest();
  assert.deepEqual(
    legsForPhase(doc, "formation").map((leg) => leg.id),
    ["train-value", "blast-radius", "security-review", "compliance-review"],
  );
  assert.equal(legsForPhase(doc, "staging").length, doc.legs.length);
  assert.throws(() => legsForPhase(doc, "promote"), /unknown phase/);
});

test("a candidate suffix resolves to the stable release the receipts live under", () => {
  assert.equal(stableRelease("v0.12.23-rc.18"), "v0.12.23");
  assert.equal(stableRelease("v0.12.23"), "v0.12.23");
  assert.equal(stableRelease(" v1.2.3-260723.stage2 "), "v1.2.3");
  assert.throws(() => stableRelease("0.12.23"), /cannot derive a stable release/);
});

test("argument parsing refuses a flag without a value", () => {
  assert.deepEqual(parseArgs(["--phase", "staging"]), { phase: "staging" });
  assert.equal(parseArgs(["--phase"]), null);
  assert.equal(parseArgs(["--phase", "--release"]), null);
  assert.equal(parseArgs(["staging"]), null);
});

// ── the check ─────────────────────────────────────────────────────────────────────────────────

test("green when every required leg has a receipt bound to the current candidate map", () => {
  const manifest = validManifest();
  const { root, sha } = scaffold(manifest, {});
  const receipts = allGreen(manifest, sha);
  const staged = scaffold(manifest, receipts, { mapBody: '{"release":"v0.12.23"}' });
  rmSync(root, { recursive: true, force: true });

  const report = checkReadiness({ root: staged.root, release: RELEASE, phase: "staging" });
  assert.equal(report.ok, true);
  assert.equal(report.rows.length, 7);
  assert.ok(report.rows.every((row) => row.status === "green"));
  assert.match(formatReport(report), /all 7 legs required at `staging` are green/);
  rmSync(staged.root, { recursive: true, force: true });
});

test("a re-cut candidate strands every receipt, naming the legs and why they no longer carry", () => {
  const manifest = validManifest();
  const first = scaffold(manifest, {});
  const receipts = allGreen(manifest, first.sha);
  // The candidate is re-cut: the map's bytes change, so its sha does — and the receipts taken
  // against the old bytes now describe a candidate that is not shipping.
  const { root } = scaffold(manifest, receipts, { mapBody: '{"release":"v0.12.23","recut":true}' });
  rmSync(first.root, { recursive: true, force: true });

  const report = checkReadiness({ root, release: RELEASE, phase: "staging" });
  assert.equal(report.ok, false);
  assert.equal(report.rows.filter((row) => row.status === "stale").length, 7);

  const rendered = formatReport(report);
  assert.match(rendered, /The candidate was re-cut after 7 legs ran/);
  for (const leg of manifest.legs) {
    assert.ok(rendered.includes(`\`${leg.id}\``), `${leg.id} is named`);
    assert.ok(rendered.includes(leg.identity.stale_reason), `${leg.id} says why`);
  }
  assert.match(rendered, /readiness is not proven/);
  rmSync(root, { recursive: true, force: true });
});

test("a missing, unparseable, invalid or red receipt each fail closed with their own reason", () => {
  const manifest = validManifest();
  const probe = scaffold(manifest, {});
  const sha = probe.sha;
  rmSync(probe.root, { recursive: true, force: true });

  const receipts = allGreen(manifest, sha);
  delete receipts["api-docs-sweep"];
  receipts["security-review"] = "{ not json";
  receipts["compliance-review"] = { ...validReceipt(manifest.legs[5], sha), result: "red" };
  receipts["promotion-ceremony"] = (() => {
    const bad = validReceipt(manifest.legs[6], sha);
    delete bad.findings_ref;
    return bad;
  })();

  const { root } = scaffold(manifest, receipts);
  const report = checkReadiness({ root, release: RELEASE, phase: "staging" });
  const status = Object.fromEntries(report.rows.map((row) => [row.leg.id, row.status]));

  assert.equal(report.ok, false);
  assert.equal(status["api-docs-sweep"], "missing");
  assert.equal(status["security-review"], "unparseable");
  assert.equal(status["compliance-review"], "red");
  assert.equal(status["promotion-ceremony"], "invalid");
  assert.equal(status["train-value"], "green");

  const rendered = formatReport(report);
  assert.match(rendered, /4 legs are not covered/);
  assert.match(rendered, /findings_ref/);
  rmSync(root, { recursive: true, force: true });
});

test("a receipt that declares itself stale is never read as green", () => {
  const manifest = validManifest();
  const probe = scaffold(manifest, {});
  const receipts = allGreen(manifest, probe.sha);
  receipts["blast-radius"] = { ...receipts["blast-radius"], result: "stale" };
  rmSync(probe.root, { recursive: true, force: true });

  const { root } = scaffold(manifest, receipts);
  const report = checkReadiness({ root, release: RELEASE, phase: "formation" });
  assert.equal(report.ok, false);
  assert.equal(report.rows.find((row) => row.leg.id === "blast-radius").status, "stale");
  rmSync(root, { recursive: true, force: true });
});

test("formation passes on its own legs while staging still demands the rest", () => {
  const manifest = validManifest();
  const probe = scaffold(manifest, {});
  const receipts = Object.fromEntries(
    manifest.legs
      .filter((leg) => leg.phase === "formation" || leg.phase === "both")
      .map((leg) => [leg.id, validReceipt(leg, probe.sha)]),
  );
  rmSync(probe.root, { recursive: true, force: true });

  const { root } = scaffold(manifest, receipts);
  assert.equal(checkReadiness({ root, release: RELEASE, phase: "formation" }).ok, true);
  assert.equal(checkReadiness({ root, release: RELEASE, phase: "staging" }).ok, false);
  rmSync(root, { recursive: true, force: true });
});

test("a release with no manifest and a manifest with no candidate map both fail closed", () => {
  const root = mkdtempSync(join(tmpdir(), "readiness-"));
  assert.throws(
    () => checkReadiness({ root, release: RELEASE, phase: "staging" }),
    /no readiness manifest at releases\/v0\.12\.23\/readiness\.yaml/,
  );

  const releaseDir = join(root, "releases", RELEASE);
  mkdirSync(releaseDir, { recursive: true });
  writeFileSync(join(releaseDir, "readiness.yaml"), `${emitYaml(validManifest())}\n`);
  assert.throws(
    () => checkReadiness({ root, release: RELEASE, phase: "staging" }),
    /no candidate map at releases\/v0\.12\.23\/candidate-images\.json/,
  );
  rmSync(root, { recursive: true, force: true });
});

// ── the shipped artefacts ─────────────────────────────────────────────────────────────────────

test("the shipped v0.12.23 manifest is valid and every declared protocol exists", () => {
  const doc = validateManifest(
    parseManifestYaml(readFileSync(join(REPO, "releases", RELEASE, "readiness.yaml"), "utf8")),
    RELEASE,
  );
  for (const leg of doc.legs) {
    if (leg.oracle.protocol) {
      assert.ok(existsSync(join(REPO, leg.oracle.protocol)), `${leg.oracle.protocol} exists`);
    }
  }
  assert.deepEqual(
    doc.legs.filter((leg) => leg.kind === "agent").map((leg) => leg.id).sort(),
    ["api-docs-sweep", "blast-radius", "compliance-review", "security-review"],
  );
  assert.deepEqual([...FOUNDER_LEGS].sort(), ["1", "2", "3", "4", "5", "6"]);
});

test("each protocol's worked input-identity example satisfies the shape its leg declares", () => {
  const doc = parseManifestYaml(readFileSync(join(REPO, "releases", RELEASE, "readiness.yaml"), "utf8"));
  const checked = [];
  for (const leg of doc.legs) {
    if (!leg.oracle.protocol || !leg.identity.input_pattern) continue;
    const text = readFileSync(join(REPO, leg.oracle.protocol), "utf8");
    const section = text.slice(text.indexOf("## Input identity"), text.indexOf("## Method"));
    assert.ok(section.length > 0, `${leg.id}: protocol has an Input identity section`);
    // The first unlabelled fenced block in that section is the worked example.
    const example = /```\n([\s\S]*?)```/.exec(section);
    assert.ok(example, `${leg.id}: protocol shows a worked input_identity`);
    const value = example[1].trim();
    assert.match(value, new RegExp(leg.identity.input_pattern));
    // And it must survive the real receipt validator, not just the regex.
    assert.doesNotThrow(() =>
      validateReceipt(
        { ...validReceipt(leg, "b".repeat(64)), input_identity: value },
        leg,
      ),
    );
    checked.push(leg.id);
  }
  assert.deepEqual(checked.sort(), ["api-docs-sweep", "blast-radius", "compliance-review", "security-review"]);
});

test("the published schema and the runner agree on what is required", () => {
  const schema = JSON.parse(readFileSync(join(REPO, "release/readiness/readiness.schema.json"), "utf8"));
  assert.equal(schema.schema_version, 1);
  assert.deepEqual(schema.$defs.manifest.required.sort(), [...MANIFEST_REQUIRED].sort());
  assert.deepEqual(schema.$defs.leg.required.sort(), [...LEG_REQUIRED].sort());
  assert.deepEqual(schema.$defs.identity.required.sort(), [...IDENTITY_REQUIRED].sort());
  assert.deepEqual(schema.$defs.receipt.required.sort(), [...RECEIPT_REQUIRED].sort());
});

test("the readiness job is wired in as advisory and gates nothing this train", () => {
  const workflow = readFileSync(join(REPO, ".github/workflows/release-validate.yml"), "utf8");
  const job = workflow.slice(workflow.indexOf("\n  readiness:"), workflow.indexOf("\n  value-gate:"));
  assert.ok(job.length > 0, "the readiness job exists");
  assert.match(job, /continue-on-error: true/);
  assert.match(job, /check\.mjs --phase staging/);
  assert.match(job, /ref: main/);

  // Advisory means advisory: no existing job may depend on it, or it would gate this train.
  const promote = workflow.slice(workflow.indexOf("\n  promote:"));
  assert.doesNotMatch(promote.slice(0, promote.indexOf("steps:")), /readiness/);

  // And the tests of the instrument itself have a lane in CI.
  const gates = readFileSync(join(REPO, ".github/workflows/gates.yml"), "utf8");
  assert.match(gates, /node --test .*release\/readiness\/\*\.test\.mjs/);
});
