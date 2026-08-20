#!/usr/bin/env node

// Production-readiness runner — the six-leg coverage as machinery.
//
// A release is ready when six legs are covered: train value, blast radius, full functionality
// (build/compose legs + API coverage as of docs), security, compliance, promotion ceremony.
// Each leg declares — in `releases/<version>/readiness.yaml` — its kind, its oracle, its receipt
// path, its identity binding, and the point it fires at. This runner proves the declaration:
// every receipt a phase requires exists, parses, is GREEN, and is bound to the CURRENT candidate
// map. A candidate re-cut changes the map's sha, which strands every receipt taken against the
// old bytes: those legs are reported STALE by name, with the reason the manifest itself declares.
//
// Fail-closed by construction: an unreadable manifest, an unknown leg kind, a receipt for a leg
// that is not declared, or a receipt whose identity does not bind are all failures — never a pass
// with a warning. The alternative (a leg that quietly does not run) is the exact defect the
// harness exists to remove.
//
// No runtime dependencies: release-time instruments never enter a Vexa image, and this one runs
// on a bare `node` in any checkout, so the manifest is read by the strict YAML subset parser
// below rather than by a library.

import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve as resolvePath } from "node:path";
import { pathToFileURL } from "node:url";

export const PHASES = ["formation", "staging"];
export const LEG_PHASES = ["formation", "staging", "both"];
export const KINDS = ["machine", "agent"];
export const RESULTS = ["green", "red", "stale"];

// The six legs of the standard. A manifest that does not cover all six is not a readiness
// manifest — dropping a leg must be impossible to do silently, which is the whole point.
export const FOUNDER_LEGS = ["1", "2", "3", "4", "5", "6"];

export const MANIFEST_REQUIRED = [
  "schema_version",
  "release",
  "candidate_map",
  "receipts_dir",
  "legs",
];
export const LEG_REQUIRED = [
  "id",
  "leg",
  "title",
  "kind",
  "phase",
  "oracle",
  "receipt",
  "identity",
];
export const IDENTITY_REQUIRED = ["binds_candidate_map", "input", "stale_reason"];
export const RECEIPT_REQUIRED = [
  "schema_version",
  "leg",
  "candidate_map_sha",
  "input_identity",
  "result",
  "findings_ref",
  "generated_at",
  "generated_by",
];

const VERSION = /^v\d+\.\d+\.\d+$/;
const SHA256 = /^[0-9a-f]{64}$/;
const LEG_ID = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const LEG_ORDINAL = /^([1-6])([ab])?$/;
const ISO_8601 = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$/;
const PROTOCOL_PATH = /^release\/readiness\/protocols\/[a-z0-9-]+\.md$/;

const fail = (message) => {
  throw new Error(message);
};

// ── the strict YAML subset ────────────────────────────────────────────────────────────────────
// Mappings, sequences of mappings, and scalars, nested by two-space indentation. Everything else
// — anchors, aliases, flow collections, block scalars, tabs — throws with a line number. A parser
// that guesses at a construct it does not model is a parser that reads a manifest as something
// other than what its author wrote, so this one refuses instead.

function stripComment(line) {
  let quote = null;
  for (let i = 0; i < line.length; i += 1) {
    const ch = line[i];
    if (quote) {
      if (ch === "\\" && quote === '"') i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === "#" && (i === 0 || /\s/.test(line[i - 1]))) return line.slice(0, i);
  }
  return line;
}

function splitKey(content) {
  let quote = null;
  for (let i = 0; i < content.length; i += 1) {
    const ch = content[i];
    if (quote) {
      if (ch === "\\" && quote === '"') i += 1;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '"' || ch === "'") {
      quote = ch;
      continue;
    }
    if (ch === ":" && (i + 1 === content.length || content[i + 1] === " ")) {
      return { key: content.slice(0, i).trim(), rest: content.slice(i + 1).trim() };
    }
  }
  return null;
}

function parseScalar(raw, line) {
  const text = raw.trim();
  if (text === "") fail(`line ${line}: empty value; write an explicit null if that is meant`);
  if (text[0] === '"') {
    if (text.length < 2 || text[text.length - 1] !== '"') fail(`line ${line}: unterminated string`);
    return text.slice(1, -1).replace(/\\(["\\])/g, "$1");
  }
  if (text[0] === "'") {
    if (text.length < 2 || text[text.length - 1] !== "'") fail(`line ${line}: unterminated string`);
    return text.slice(1, -1).replace(/''/g, "'");
  }
  if ("[{&*|>%@`".includes(text[0])) {
    fail(`line ${line}: unsupported YAML construct "${text[0]}" — this manifest uses a strict subset`);
  }
  if (text === "true") return true;
  if (text === "false") return false;
  if (text === "null" || text === "~") return null;
  if (/^-?\d+$/.test(text)) return Number(text);
  return text;
}

function tokenize(text) {
  const tokens = [];
  const lines = text.split("\n");
  for (let n = 0; n < lines.length; n += 1) {
    const raw = lines[n];
    if (raw.includes("\t")) fail(`line ${n + 1}: tabs are not allowed; indent with spaces`);
    const stripped = stripComment(raw);
    if (stripped.trim() === "") continue;
    if (stripped.trim() === "---") continue;
    const indent = stripped.length - stripped.trimStart().length;
    if (indent % 2 !== 0) fail(`line ${n + 1}: indent ${indent} is not a multiple of two`);
    let content = stripped.trim();
    let dash = false;
    if (content === "-") fail(`line ${n + 1}: empty sequence entry`);
    if (content.startsWith("- ")) {
      dash = true;
      content = content.slice(2).trim();
    }
    tokens.push({ line: n + 1, indent, dash, content });
  }
  return tokens;
}

function parseNodes(tokens, start, indent) {
  let pos = start;
  const first = tokens[pos];
  if (!first) fail("unexpected end of manifest");
  if (first.indent !== indent) {
    fail(`line ${first.line}: indent ${first.indent} where ${indent} was expected`);
  }

  if (first.dash) {
    const items = [];
    while (pos < tokens.length && tokens[pos].indent === indent && tokens[pos].dash) {
      const head = tokens[pos];
      const body = [{ line: head.line, indent: indent + 2, dash: false, content: head.content }];
      pos += 1;
      while (pos < tokens.length && tokens[pos].indent >= indent + 2) {
        body.push(tokens[pos]);
        pos += 1;
      }
      items.push(parseNodes(body, 0, indent + 2)[0]);
    }
    return [items, pos];
  }

  if (!splitKey(first.content)) {
    if (tokens.length === pos + 1) return [parseScalar(first.content, first.line), pos + 1];
    fail(`line ${first.line}: expected "key: value"`);
  }

  const map = {};
  while (pos < tokens.length && tokens[pos].indent === indent) {
    const token = tokens[pos];
    if (token.dash) fail(`line ${token.line}: sequence entry inside a mapping`);
    const pair = splitKey(token.content);
    if (!pair) fail(`line ${token.line}: expected "key: value"`);
    if (Object.prototype.hasOwnProperty.call(map, pair.key)) {
      fail(`line ${token.line}: duplicate key "${pair.key}"`);
    }
    pos += 1;
    if (pair.rest !== "") {
      map[pair.key] = parseScalar(pair.rest, token.line);
      continue;
    }
    const child = tokens[pos];
    if (!child) fail(`line ${token.line}: "${pair.key}" has no value`);
    if (child.indent === indent && child.dash) {
      const [value, next] = parseNodes(tokens, pos, indent);
      map[pair.key] = value;
      pos = next;
      continue;
    }
    if (child.indent <= indent) fail(`line ${token.line}: "${pair.key}" has no value`);
    const [value, next] = parseNodes(tokens, pos, child.indent);
    map[pair.key] = value;
    pos = next;
  }
  return [map, pos];
}

export function parseManifestYaml(text) {
  const tokens = tokenize(text);
  if (tokens.length === 0) fail("manifest is empty");
  const [doc, pos] = parseNodes(tokens, 0, 0);
  if (pos !== tokens.length) fail(`line ${tokens[pos].line}: trailing content after the document`);
  if (!isPlainObject(doc)) fail("manifest must be a mapping at the top level");
  return doc;
}

// ── manifest + receipt validation ─────────────────────────────────────────────────────────────

const isPlainObject = (value) =>
  value !== null && typeof value === "object" && !Array.isArray(value);

function requireFields(row, fields, label) {
  for (const field of fields) {
    if (row[field] === undefined) fail(`${label}: missing required field "${field}"`);
  }
}

function requireText(row, field, label) {
  if (typeof row[field] !== "string" || row[field].trim() === "") {
    fail(`${label}: "${field}" must be a non-empty string`);
  }
}

export function validateManifest(doc, expectedRelease) {
  if (!isPlainObject(doc)) fail("manifest must be a mapping");
  requireFields(doc, MANIFEST_REQUIRED, "manifest");
  if (doc.schema_version !== 1) fail("manifest: schema_version must be 1");
  if (!VERSION.test(doc.release)) fail(`manifest: invalid release "${doc.release}"`);
  if (expectedRelease && doc.release !== expectedRelease) {
    fail(`manifest release ${doc.release} does not match requested ${expectedRelease}`);
  }
  if (doc.candidate_map !== `releases/${doc.release}/candidate-images.json`) {
    fail(
      `manifest: candidate_map must be releases/${doc.release}/candidate-images.json ` +
      `(got ${doc.candidate_map}) — the identity binding is to THIS release's frozen map`,
    );
  }
  if (doc.receipts_dir !== `releases/${doc.release}/readiness`) {
    fail(`manifest: receipts_dir must be releases/${doc.release}/readiness`);
  }
  if (!Array.isArray(doc.legs) || doc.legs.length === 0) fail("manifest: legs must be a non-empty list");

  const ids = new Set();
  for (const leg of doc.legs) {
    if (!isPlainObject(leg)) fail("manifest: every leg must be a mapping");
    const label = `leg ${leg.id ?? "<unnamed>"}`;
    requireFields(leg, LEG_REQUIRED, label);
    requireText(leg, "id", label);
    if (!LEG_ID.test(leg.id)) fail(`${label}: id must be kebab-case`);
    if (ids.has(leg.id)) fail(`manifest: duplicate leg id "${leg.id}"`);
    ids.add(leg.id);
    requireText(leg, "title", label);
    if (typeof leg.leg !== "string" || !LEG_ORDINAL.test(leg.leg)) {
      fail(`${label}: leg must be one of the six ordinals, optionally suffixed a/b (got ${leg.leg})`);
    }
    if (!KINDS.includes(leg.kind)) fail(`${label}: kind must be one of ${KINDS.join("|")}`);
    if (!LEG_PHASES.includes(leg.phase)) fail(`${label}: phase must be one of ${LEG_PHASES.join("|")}`);

    if (!isPlainObject(leg.oracle)) fail(`${label}: oracle must be a mapping`);
    const hasCommand = leg.oracle.command !== undefined;
    const hasProtocol = leg.oracle.protocol !== undefined;
    if (hasCommand === hasProtocol) {
      fail(`${label}: oracle must declare exactly one of command | protocol`);
    }
    if (leg.kind === "machine" && !hasCommand) {
      fail(`${label}: a machine leg's oracle must be a command`);
    }
    if (leg.kind === "agent" && !hasProtocol) {
      fail(`${label}: an agent leg's oracle must be a protocol document`);
    }
    if (hasCommand) {
      requireText(leg.oracle, "command", label);
      // A version pinned INSIDE the oracle is the fourth place this release's number appears, after
      // release / candidate_map / receipts_dir. The other three are checked above; without this one
      // a manifest copied forward keeps running the previous release's oracle and reports green for
      // a batch it never walked — the failure is silent, because the command still succeeds.
      for (const pinned of leg.oracle.command.matchAll(/RELEASE_VERSION=(\S+)/g)) {
        if (pinned[1] !== doc.release) {
          fail(
            `${label}: oracle pins RELEASE_VERSION=${pinned[1]} but this manifest is ${doc.release} ` +
            `— the oracle would run against another release`,
          );
        }
      }
    }
    if (hasProtocol) {
      requireText(leg.oracle, "protocol", label);
      if (!PROTOCOL_PATH.test(leg.oracle.protocol)) {
        fail(`${label}: protocol must live at release/readiness/protocols/<name>.md`);
      }
    }

    if (leg.receipt !== `${doc.receipts_dir}/${leg.id}.receipt.json`) {
      fail(`${label}: receipt must be ${doc.receipts_dir}/${leg.id}.receipt.json (got ${leg.receipt})`);
    }

    if (!isPlainObject(leg.identity)) fail(`${label}: identity must be a mapping`);
    requireFields(leg.identity, IDENTITY_REQUIRED, `${label} identity`);
    if (leg.identity.binds_candidate_map !== true) {
      fail(`${label}: identity.binds_candidate_map must be true — every leg binds the candidate map`);
    }
    requireText(leg.identity, "input", `${label} identity`);
    requireText(leg.identity, "stale_reason", `${label} identity`);
    if (leg.identity.input_pattern !== undefined) {
      requireText(leg.identity, "input_pattern", `${label} identity`);
      try {
        new RegExp(leg.identity.input_pattern);
      } catch (error) {
        fail(`${label}: identity.input_pattern is not a valid regular expression (${error.message})`);
      }
    }
  }

  const covered = new Set(doc.legs.map((leg) => LEG_ORDINAL.exec(leg.leg)[1]));
  const uncovered = FOUNDER_LEGS.filter((ordinal) => !covered.has(ordinal));
  if (uncovered.length > 0) {
    fail(`manifest: the six-leg standard is not covered — no leg declares ${uncovered.join(", ")}`);
  }
  return doc;
}

export function validateReceipt(receipt, leg) {
  const label = `receipt ${leg.id}`;
  if (!isPlainObject(receipt)) fail(`${label}: must be a JSON object`);
  requireFields(receipt, RECEIPT_REQUIRED, label);
  if (receipt.schema_version !== 1) fail(`${label}: schema_version must be 1`);
  if (receipt.leg !== leg.id) fail(`${label}: leg "${receipt.leg}" does not match the manifest leg "${leg.id}"`);
  if (typeof receipt.candidate_map_sha !== "string" || !SHA256.test(receipt.candidate_map_sha)) {
    fail(`${label}: candidate_map_sha must be a lowercase 64-hex sha256`);
  }
  requireText(receipt, "input_identity", label);
  if (leg.identity.input_pattern) {
    if (!new RegExp(leg.identity.input_pattern).test(receipt.input_identity)) {
      fail(
        `${label}: input_identity "${receipt.input_identity}" does not match the shape this leg ` +
        `declares (${leg.identity.input_pattern})`,
      );
    }
  }
  if (!RESULTS.includes(receipt.result)) fail(`${label}: result must be one of ${RESULTS.join("|")}`);
  requireText(receipt, "findings_ref", label);
  requireText(receipt, "generated_by", label);
  if (typeof receipt.generated_at !== "string" || !ISO_8601.test(receipt.generated_at)) {
    fail(`${label}: generated_at must be an ISO-8601 timestamp`);
  }
  return receipt;
}

// ── phase selection ───────────────────────────────────────────────────────────────────────────

// Formation requires the legs that fire when the train forms. Staging requires ALL six — a
// formation leg is not spent by having once been green: if the candidate was re-cut after it ran,
// its receipt is bound to bytes that are no longer shipping, and that is precisely what staging
// has to catch.
export function legsForPhase(doc, phase) {
  if (!PHASES.includes(phase)) fail(`unknown phase "${phase}" — expected ${PHASES.join(" | ")}`);
  if (phase === "staging") return doc.legs;
  return doc.legs.filter((leg) => leg.phase === "formation" || leg.phase === "both");
}

export function stableRelease(version) {
  const match = /^(v\d+\.\d+\.\d+)(?:-.*)?$/.exec(String(version || "").trim());
  if (!match) fail(`cannot derive a stable release from "${version}"`);
  return match[1];
}

export function sha256File(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

// ── the check ─────────────────────────────────────────────────────────────────────────────────

export function checkReadiness({ root = process.cwd(), manifestPath, release, phase }) {
  if (!manifestPath && !release) fail("checkReadiness needs a release or an explicit manifest path");
  const relativeManifest = manifestPath || `releases/${release}/readiness.yaml`;
  const absoluteManifest = resolvePath(root, relativeManifest);
  let manifestText;
  try {
    manifestText = readFileSync(absoluteManifest, "utf8");
  } catch {
    fail(`no readiness manifest at ${relativeManifest} — a release without one has no declared coverage`);
  }
  const doc = validateManifest(parseManifestYaml(manifestText), release);

  const candidateMapPath = resolvePath(root, doc.candidate_map);
  let currentSha;
  try {
    currentSha = sha256File(candidateMapPath);
  } catch {
    fail(`no candidate map at ${doc.candidate_map} — nothing to bind the readiness receipts to`);
  }

  const required = legsForPhase(doc, phase);
  const rows = required.map((leg) => {
    const row = { leg, status: null, detail: "", receipt: null };
    let text;
    try {
      text = readFileSync(resolvePath(root, leg.receipt), "utf8");
    } catch {
      row.status = "missing";
      row.detail = `no receipt at ${leg.receipt}`;
      return row;
    }
    let receipt;
    try {
      receipt = JSON.parse(text);
    } catch (error) {
      row.status = "unparseable";
      row.detail = `receipt is not valid JSON (${error.message})`;
      return row;
    }
    try {
      validateReceipt(receipt, leg);
    } catch (error) {
      row.status = "invalid";
      row.detail = error.message;
      return row;
    }
    row.receipt = receipt;
    if (receipt.candidate_map_sha !== currentSha) {
      row.status = "stale";
      row.detail =
        `bound to candidate map ${receipt.candidate_map_sha.slice(0, 12)}, ` +
        `current is ${currentSha.slice(0, 12)}`;
      return row;
    }
    if (receipt.result !== "green") {
      row.status = receipt.result === "stale" ? "stale" : "red";
      row.detail = `receipt declares result=${receipt.result} · ${receipt.findings_ref}`;
      return row;
    }
    row.status = "green";
    row.detail = receipt.findings_ref;
    return row;
  });

  return {
    release: doc.release,
    phase,
    manifest: relativeManifest,
    candidate_map: doc.candidate_map,
    candidate_map_sha: currentSha,
    rows,
    ok: rows.every((row) => row.status === "green"),
  };
}

const MARK = {
  green: "✅",
  stale: "🕒",
  red: "❌",
  missing: "⛔",
  invalid: "❌",
  unparseable: "❌",
};

export function formatReport(report) {
  const lines = [];
  lines.push(`## Production readiness — ${report.release} · phase \`${report.phase}\``);
  lines.push("");
  lines.push(`Candidate map \`${report.candidate_map}\` → \`${report.candidate_map_sha}\``);
  lines.push("");
  lines.push("| Leg | # | Kind | Fires | Status | Evidence |");
  lines.push("|---|---|---|---|---|---|");
  for (const { leg, status, detail } of report.rows) {
    lines.push(
      `| ${leg.title} | ${leg.leg} | ${leg.kind} | ${leg.phase} | ${MARK[status]} ${status} | ${detail} |`,
    );
  }

  const stale = report.rows.filter((row) => row.status === "stale");
  if (stale.length > 0) {
    lines.push("");
    lines.push(
      `**The candidate was re-cut after ${stale.length} leg${stale.length === 1 ? "" : "s"} ran.** ` +
      "Their receipts are bound to bytes that are no longer shipping, so their coverage does not " +
      "describe this candidate. Each must be re-run and its receipt re-bound:",
    );
    lines.push("");
    for (const { leg, receipt, detail } of stale) {
      const bound = receipt ? receipt.candidate_map_sha.slice(0, 12) : "unknown";
      lines.push(`- \`${leg.id}\` — bound to \`${bound}\`, current \`${report.candidate_map_sha.slice(0, 12)}\`.`);
      lines.push(`  Why it does not carry: ${leg.identity.stale_reason}`);
      lines.push(`  Re-run: ${leg.oracle.command ? `\`${leg.oracle.command}\`` : leg.oracle.protocol}`);
      if (!receipt) lines.push(`  (${detail})`);
    }
  }

  const broken = report.rows.filter((row) => ["missing", "invalid", "unparseable", "red"].includes(row.status));
  if (broken.length > 0) {
    lines.push("");
    lines.push(`**${broken.length} leg${broken.length === 1 ? " is" : "s are"} not covered:**`);
    lines.push("");
    for (const { leg, status, detail } of broken) {
      lines.push(`- \`${leg.id}\` (${status}) — ${detail}`);
      lines.push(`  Oracle: ${leg.oracle.command ? `\`${leg.oracle.command}\`` : leg.oracle.protocol}`);
    }
  }

  lines.push("");
  lines.push(
    report.ok
      ? `✅ all ${report.rows.length} legs required at \`${report.phase}\` are green and bound to the current candidate.`
      : `❌ readiness is not proven at \`${report.phase}\` — see the rows above.`,
  );
  return lines.join("\n");
}

function usage() {
  console.error(
    "usage: check.mjs --phase <formation|staging> [--release vX.Y.Z] [--manifest <path>] [--root <path>]\n" +
    "       RELEASE_VERSION is read when --release is omitted; a candidate suffix is stripped.",
  );
  process.exit(2);
}

export function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (!flag.startsWith("--")) return null;
    const value = argv[i + 1];
    if (value === undefined || value.startsWith("--")) return null;
    args[flag.slice(2)] = value;
    i += 1;
  }
  return args;
}

function main(argv) {
  const args = parseArgs(argv);
  if (!args || !args.phase) usage();
  const version = args.release || process.env.RELEASE_VERSION;
  if (!version && !args.manifest) usage();
  const report = checkReadiness({
    root: args.root || process.cwd(),
    manifestPath: args.manifest,
    release: version ? stableRelease(version) : undefined,
    phase: args.phase,
  });
  console.log(formatReport(report));
  process.exit(report.ok ? 0 : 1);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    main(process.argv.slice(2));
  } catch (error) {
    console.error(`readiness: ${error.message}`);
    process.exit(1);
  }
}
