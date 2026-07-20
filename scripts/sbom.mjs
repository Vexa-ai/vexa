#!/usr/bin/env node
/**
 * sbom.mjs — emit the per-release SPDX 2.3 SBOM (ADR-0004: "Emit an SBOM (SPDX)
 * per release so the consumer's OSPO can audit").
 *
 * `gate:licenses` (scripts/gates.mjs) PROVES the npm tree is licence-clean but
 * emits no artifact, and it never sees non-dependency bytes baked into the images
 * (model weights). This script is the emit side + the packaging complement: it
 * inventories three sources into one SPDX document —
 *
 *   1. npm deps   — `pnpm licenses list --json` (the same index gate:licenses uses):
 *                   name · version · declared licence, one SPDX package per version.
 *   2. pip deps   — the committed uv.lock files (name · version). uv.lock carries
 *                   no licence field, so pip packages ship licenceDeclared=NOASSERTION
 *                   (Python licence resolution via pip-licenses is owed per ADR-0009);
 *                   the INVENTORY is still complete and CI-runnable with no install.
 *   3. baked model — onnx-community/pyannote-segmentation-3.0 (MIT), baked into
 *                   vexaai/vexa-bot + vexaai/vexa-lite at /opt/hf-cache. Fully
 *                   specified (licence + copyright + download location). Mirrors
 *                   THIRD_PARTY_LICENSES.md; the piece gate:licenses cannot see.
 *
 * The npm and pip sources are best-effort: a missing pnpm index or absent uv.locks
 * degrade to a WARNING on stderr, never a hard failure — the document always emits
 * with at least the repo + the baked model (green-on-empty, like the gates).
 *
 * Usage:
 *   node scripts/sbom.mjs --version v0.12.3 [--output sbom.spdx.json]
 *   VERSION=v0.12.3 node scripts/sbom.mjs            # writes to stdout
 * Run from the repo root (like scripts/gates.mjs).
 */
import { readFileSync, existsSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execSync } from "node:child_process";

const ROOT = process.cwd();
const SKIP = new Set(["node_modules", ".venv", ".git", "dist", ".turbo", "__pycache__", ".pytest_cache"]);

// ── args / env ────────────────────────────────────────────────────────────────
const argv = process.argv.slice(2);
const argOf = (flag) => { const i = argv.indexOf(flag); return i >= 0 ? argv[i + 1] : undefined; };
const VERSION = argOf("--version") || process.env.VERSION || "0.0.0-dev";
const OUTPUT = argOf("--output");
// Deterministic-friendly: honour an injected timestamp (e.g. the commit date) so a
// re-run over the same tree can reproduce the document; else stamp now.
const CREATED = process.env.SBOM_CREATED || new Date().toISOString();

const warn = (m) => console.error(`[sbom] WARN: ${m}`);

// SPDXID charset is [a-zA-Z0-9.-]; map everything else to '-' and keep IDs unique.
const seenIds = new Set();
function spdxId(prefix, ...parts) {
  let base = `SPDXRef-${prefix}-` + parts.join("-").replace(/[^a-zA-Z0-9.-]/g, "-").replace(/-+/g, "-");
  let id = base, n = 1;
  while (seenIds.has(id)) id = `${base}-${n++}`;
  seenIds.add(id);
  return id;
}

// Only pass through a licence string that is a plausible SPDX expression; prose like
// "SEE LICENSE IN LICENSE" or "UNKNOWN" becomes NOASSERTION (SPDX-valid, honest).
function spdxLicense(lic) {
  if (!lic || typeof lic !== "string") return "NOASSERTION";
  const s = lic.trim();
  if (/^[\w.\-+()]+( (?:OR|AND|WITH) [\w.\-+()]+)*$/.test(s)) return s;
  return "NOASSERTION";
}

// ── the repo's own licence (root package) ───────────────────────────────────────
function repoLicense() {
  try {
    const txt = readFileSync(join(ROOT, "LICENSE"), "utf8");
    if (/Apache License/i.test(txt)) return "Apache-2.0";
    if (/\bMIT License\b/i.test(txt)) return "MIT";
    if (/GNU GENERAL PUBLIC/i.test(txt)) return "GPL-3.0-or-later";
  } catch { /* no LICENSE — fall through */ }
  return "NOASSERTION";
}

// ── source 1: npm deps via pnpm's built-in licence index ────────────────────────
function npmPackages() {
  let raw = "";
  try {
    raw = execSync("pnpm licenses list --json", { cwd: ROOT, stdio: ["ignore", "pipe", "ignore"] }).toString();
  } catch (e) { raw = (e.stdout || "").toString(); }
  if (!raw.trim()) { warn("`pnpm licenses list --json` produced no output — run `pnpm install` first; emitting SBOM without the npm tree"); return []; }
  let data;
  try { data = JSON.parse(raw); } catch { warn("`pnpm licenses list --json` returned non-JSON — skipping npm tree"); return []; }
  const out = [];
  for (const [lic, pkgs] of Object.entries(data)) {
    for (const p of pkgs) {
      const versions = Array.isArray(p.versions) && p.versions.length ? p.versions : (p.version ? [p.version] : ["NOASSERTION"]);
      for (const v of versions) {
        out.push({
          eco: "npm", name: p.name, version: v,
          licenseDeclared: spdxLicense(p.license || lic),
          purl: `pkg:npm/${p.name}@${v}`,
          homepage: p.homepage || null,
        });
      }
    }
  }
  return out;
}

// ── source 2: pip deps — inventory from the committed uv.lock files ──────────────
function findUvLocks(dir = ROOT, acc = []) {
  for (const name of readdirSync(dir)) {
    if (SKIP.has(name) || name.startsWith(".")) continue;
    const p = join(dir, name);
    let s; try { s = statSync(p); } catch { continue; }
    if (s.isDirectory()) findUvLocks(p, acc);
    else if (name === "uv.lock") acc.push(p);
  }
  return acc;
}
// Minimal TOML scan: uv.lock is an array of `[[package]]` tables; capture name+version
// per block. No TOML dep to vet (itself a P17 win, like gate:licenses' pnpm reuse).
function parseUvLock(file) {
  const pkgs = [];
  let cur = null;
  for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
    if (line.trim() === "[[package]]") { if (cur && cur.name) pkgs.push(cur); cur = {}; continue; }
    if (line.startsWith("[")) { if (cur && cur.name) pkgs.push(cur); cur = null; continue; } // left the package table
    if (!cur) continue;
    let m;
    if ((m = line.match(/^name = "(.+)"$/))) cur.name = m[1];
    else if ((m = line.match(/^version = "(.+)"$/))) cur.version = m[1];
  }
  if (cur && cur.name) pkgs.push(cur);
  return pkgs;
}
function pipPackages() {
  const locks = findUvLocks();
  if (!locks.length) { warn("no uv.lock files found — emitting SBOM without the pip tree"); return []; }
  const byKey = new Map(); // dedup name@version across every service lockfile
  for (const f of locks) {
    for (const p of parseUvLock(f)) {
      const version = p.version || "NOASSERTION";
      const key = `${p.name}@${version}`;
      if (!byKey.has(key)) byKey.set(key, {
        eco: "pypi", name: p.name, version,
        licenseDeclared: "NOASSERTION", // uv.lock carries no licence field (ADR-0009: pip-licenses owed)
        purl: `pkg:pypi/${p.name.toLowerCase()}@${version}`,
        homepage: null,
      });
    }
  }
  return [...byKey.values()];
}

// ── source 3: the baked model weights (the piece gate:licenses cannot see) ───────
const BAKED_MODEL = {
  eco: "huggingface", name: "onnx-community/pyannote-segmentation-3.0", version: "main",
  licenseDeclared: "MIT", licenseConcluded: "MIT",
  copyrightText: "Copyright (c) 2020 CNRS",
  downloadLocation: "https://huggingface.co/onnx-community/pyannote-segmentation-3.0",
  purl: "pkg:huggingface/onnx-community/pyannote-segmentation-3.0@main",
  supplier: "Organization: onnx-community (ONNX conversion of pyannote/segmentation-3.0, pyannote.audio / CNRS)",
  comment: "Baked into vexaai/vexa-bot + vexaai/vexa-lite at /opt/hf-cache; loaded offline by the mixed (Zoom/Teams) diarization lane. Notice: /opt/hf-cache/LICENSE.pyannote-segmentation-3.0 + repo THIRD_PARTY_LICENSES.md.",
};

// ── assemble the SPDX 2.3 document ──────────────────────────────────────────────
function pkgObject(p, id) {
  const externalRefs = p.purl ? [{ referenceCategory: "PACKAGE-MANAGER", referenceType: "purl", referenceLocator: p.purl }] : [];
  const o = {
    SPDXID: id,
    name: p.name,
    versionInfo: p.version,
    downloadLocation: p.downloadLocation || "NOASSERTION",
    filesAnalyzed: false,
    licenseConcluded: p.licenseConcluded || "NOASSERTION",
    licenseDeclared: p.licenseDeclared || "NOASSERTION",
    copyrightText: p.copyrightText || "NOASSERTION",
  };
  if (p.supplier) o.supplier = p.supplier;
  if (externalRefs.length) o.externalRefs = externalRefs;
  if (p.comment) o.comment = p.comment;
  return o;
}

const npm = npmPackages();
const pip = pipPackages();
const deps = [...npm, ...pip].sort((a, b) => (a.eco + a.name + a.version).localeCompare(b.eco + b.name + b.version));

const ROOT_ID = "SPDXRef-Package-vexa";
const packages = [{
  SPDXID: ROOT_ID,
  name: "vexa",
  versionInfo: VERSION.replace(/^v/, ""),
  downloadLocation: "https://github.com/Vexa-ai/vexa",
  filesAnalyzed: false,
  licenseConcluded: "NOASSERTION",
  licenseDeclared: repoLicense(),
  copyrightText: "NOASSERTION",
  supplier: "Organization: Vexa",
}];
const relationships = [{ spdxElementId: "SPDXRef-DOCUMENT", relatedSpdxElement: ROOT_ID, relationshipType: "DESCRIBES" }];

// The baked model is CONTAINED in the distributed image (not a source dependency).
const modelId = spdxId("Package", "hf", "pyannote-segmentation-3.0");
packages.push(pkgObject(BAKED_MODEL, modelId));
relationships.push({ spdxElementId: ROOT_ID, relatedSpdxElement: modelId, relationshipType: "CONTAINS" });

for (const d of deps) {
  const id = spdxId("Package", d.eco, d.name, d.version);
  packages.push(pkgObject(d, id));
  relationships.push({ spdxElementId: ROOT_ID, relatedSpdxElement: id, relationshipType: "DEPENDS_ON" });
}

const doc = {
  spdxVersion: "SPDX-2.3",
  dataLicense: "CC0-1.0",
  SPDXID: "SPDXRef-DOCUMENT",
  name: `vexa-${VERSION}`,
  documentNamespace: `https://github.com/Vexa-ai/vexa/sbom/${VERSION}-${CREATED.replace(/[:.]/g, "-")}`,
  creationInfo: {
    created: CREATED,
    creators: ["Tool: vexa-sbom (scripts/sbom.mjs)", "Organization: Vexa"],
    comment: `Coverage: npm deps=${npm.length} (declared licences via pnpm), pip deps=${pip.length} (inventory only, licence=NOASSERTION — pip-licenses owed per ADR-0009), baked model weights=1 (fully specified). Non-dependency baked artifacts (model weights) sit outside gate:licenses; see THIRD_PARTY_LICENSES.md.`,
  },
  packages,
  relationships,
};

const json = JSON.stringify(doc, null, 2) + "\n";
if (OUTPUT) {
  writeFileSync(OUTPUT, json);
  console.error(`[sbom] wrote ${OUTPUT} — ${packages.length} packages (root + model + ${deps.length} deps: ${npm.length} npm, ${pip.length} pip)`);
} else {
  process.stdout.write(json);
}
