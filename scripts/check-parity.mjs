#!/usr/bin/env node
/**
 * The FACT-PARITY checker — P23, one writer per fact, applied to facts that are not data carriers
 * but LITERALS: a set of statuses, a mark, a sentence, a regex, a default URL.
 *
 * THE ARGUMENT IS ONE CONTROL CASE. `config_preflight.py` is vendored byte-identically into six
 * packages and has never drifted, because `gate:config-contract` fails the build on byte-inequality.
 * It is the only cross-image constant in this repository with a parity gate, and it is the only
 * multiply-written fact that has held. Every other one on the 2026-09-03 SSOT survey has either
 * drifted or is one edit from drifting — including four that carry a comment asserting the copies
 * are "kept identical on purpose".
 *
 * WHAT THIS IS NOT. It is not a refactor. Where one canonical definition can be imported by the
 * others without anybody deciding anything, this PR imports it and the fact is ENFORCED. Where
 * agreeing requires choosing which of two live answers is right — which slug the server writes,
 * whether a `joining` meeting is live — the gate does NOT pick. It records both answers, names the
 * decision, and refuses to let either side move without saying so. A gate that guesses a product
 * decision is worse than no gate: it makes the wrong answer permanent and calls it enforcement.
 *
 * THE MANIFEST (`scripts/parity.json`) is the deliverable. Each fact declares:
 *   kind          file-bytes | literal | set | regex-source
 *   sites[]       {path, pattern} — `pattern` is a JS regex with exactly ONE capture group; it must
 *                 match exactly once in the file. Zero matches or two is a STALE MANIFEST and fails:
 *                 the manifest cannot quietly stop describing the tree.
 *   enforced      true  → every site must agree. This is the teeth.
 *                 false → the fact has ALREADY DRIFTED and agreeing needs a decision. The distinct
 *                         values are pinned in `distinct` and `decision` names what must be settled.
 *                         The gate then fails if what it finds differs from what is pinned — so a
 *                         drifted fact cannot move without a human writing down that it moved — and
 *                         fails if the sites have come into agreement, which means the decision was
 *                         taken and `enforced` should now be true. The ledger can only shrink.
 *
 * `regex-source` is a seam, stated as one: for a Python↔TypeScript pair there is no shared module to
 * import (ADR-0036 item 10), so what is compared is the regex SOURCE STRING. Two engines can still
 * read one source differently; string equality is the strongest check available without a mechanism
 * that does not exist, and it is strictly better than the nothing that is there today.
 */
import { readFileSync, writeFileSync, existsSync, readdirSync, statSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_ROOT = join(HERE, "..");
export const MANIFEST_PATH = "scripts/parity.json";

export function loadManifest(root = DEFAULT_ROOT) {
  const p = join(root, MANIFEST_PATH);
  if (!existsSync(p)) return { facts: [] };
  return JSON.parse(readFileSync(p, "utf8"));
}

/**
 * The PROSE normaliser. A sentence that must read the same to a stranger is carried differently in
 * every file that holds it: a Python implicit-concat splits it at whichever word hit the margin, a
 * markdown blockquote prefixes every line with "> " and hard-wraps, a TS template literal keeps it
 * on one line. Those are carrier artefacts, not different sentences, and a byte comparator would
 * report five spellings where a reader sees one. So prose is compared after the carrier is removed:
 * quote markers, concatenation seams and every whitespace run collapse to single spaces.
 * What is NOT normalised: apostrophes. U+2019 vs U+0027 is a real difference in text a stranger
 * reads, and the rig's copy differs from every other in exactly that way.
 */
export const asProse = (raw) => raw
  .replace(/\\n/g, " ")                  // an escaped newline inside a source string literal
  .replace(/^[ \t]*>[ \t]?/gm, " ")      // markdown blockquote marker
  .replace(/["`]/g, "")                   // string delimiters — but NEVER the apostrophe
  .replace(/"\s*\n\s*"/g, "")           // Python/TS implicit string concatenation seam
  .replace(/\s+/g, " ")
  .trim();

/** The set-comparison normaliser: a delimited list of quoted members → a sorted, de-duped array. */
export const asSet = (raw) => [...new Set(
  raw.split(/[,|\s]+/).map((t) => t.replace(/^[\s'"`\[({]+|[\s'"`\])}]+$/g, "")).filter(Boolean),
)].sort();

/** Read one site's value. Returns {value} or {error}. */
export function readSite(fact, site, root) {
  const abs = join(root, site.path);
  if (!existsSync(abs)) return { error: `${site.path} does not exist (the manifest names a file the tree does not have)` };
  const text = readFileSync(abs, "utf8");
  if (fact.kind === "file-bytes") return { value: text, line: 1 };

  let re;
  try { re = new RegExp(site.pattern, site.flags || "m"); }
  catch (e) { return { error: `${site.path}: the manifest's own pattern does not compile — ${e.message}` }; }
  const all = [...text.matchAll(new RegExp(re.source, (re.flags.includes("g") ? re.flags : re.flags + "g")))];
  if (all.length === 0)
    return { error: `${site.path}: the manifest's pattern matches nothing — either the fact moved or the site is gone. Re-anchor it or delete the site; a pattern that matches nothing silently stops checking.` };
  if (all.length > 1 && !site.all)
    return { error: `${site.path}: the manifest's pattern matches ${all.length} times — anchor it to exactly one, or set "all": true if EVERY occurrence in this file must carry the same value.` };
  if (site.all) {
    // "all": the fact is written several times in ONE file (five contracts carry the placeholder
    // list once per key). Every occurrence must agree with every other before the file contributes
    // a value at all, so an intra-file disagreement is named where it happens rather than being
    // averaged into whichever match came first.
    const vals = all.map((x) => x[1]);
    const norm = (v) => fact.kind === "set" ? asSet(v).join(" · ") : fact.kind === "prose" ? asProse(v) : v;
    const distinctHere = [...new Set(vals.map(norm))];
    if (distinctHere.length > 1)
      return { error: `${site.path}: this file writes the fact ${all.length} times and its own copies disagree — ${distinctHere.map((v) => JSON.stringify(v.slice(0, 80))).join(" vs ")}` };
  }
  const m = all[0];
  if (m[1] === undefined) return { error: `${site.path}: the manifest's pattern has no capture group` };
  const line = text.slice(0, m.index).split("\n").length;
  const raw = m[1];
  const value = fact.kind === "set" ? asSet(raw).join(" · ")
    : fact.kind === "prose" ? asProse(raw)
    : raw;
  return { value, line, raw };
}

/** Check one fact. Returns {id, errs[], values: Map(value -> [site…]), distinct[]} */
export function checkFact(fact, root = DEFAULT_ROOT) {
  const errs = [];
  const byValue = new Map();
  for (const site of fact.sites || []) {
    const r = readSite(fact, site, root);
    if (r.error) { errs.push(`${fact.id}: ${r.error}`); continue; }
    if (!byValue.has(r.value)) byValue.set(r.value, []);
    byValue.get(r.value).push(`${site.path}:${r.line}`);
  }
  const distinct = [...byValue.keys()].sort();

  if (fact.enforced) {
    if (distinct.length > 1) {
      const groups = distinct.map((v, i) => `        (${i + 1}) ${short(v)}\n            ${byValue.get(v).join("\n            ")}`).join("\n");
      errs.push(`${fact.id} — "${fact.fact}" is written ${(fact.sites || []).length} times and they DISAGREE (${distinct.length} answers):\n${groups}\n        canonical: ${fact.canonical || "(none declared)"} — ${fact.how_to_agree || "make every site read the canonical definition"}`);
    }
  } else {
    const pinned = (fact.distinct || []).slice().sort();
    if (distinct.length === 1 && (fact.sites || []).length > 1)
      errs.push(`${fact.id} — "${fact.fact}" is now IN PARITY across all ${(fact.sites || []).length} sites. The decision it was waiting on ("${fact.decision}") has been taken: set "enforced": true in ${MANIFEST_PATH} so it can never drift again. The ledger only shrinks.`);
    else if (JSON.stringify(distinct) !== JSON.stringify(pinned)) {
      errs.push(`${fact.id} — "${fact.fact}" has CHANGED since it was recorded, and it is a fact nobody has decided yet.\n        recorded: ${pinned.map(short).join("  |  ") || "(nothing)"}\n        found:    ${distinct.map(short).join("  |  ")}\n        Either make the sites agree and set "enforced": true, or update "distinct" in ${MANIFEST_PATH} so the ledger still tells the truth. Decision pending: ${fact.decision}`);
    }
  }
  return { id: fact.id, errs, byValue, distinct };
}

const short = (v) => {
  const one = String(v).replace(/\s+/g, " ").trim();
  return one.length > 160 ? one.slice(0, 157) + "…" : one;
};

// A declared site list is only a real inventory if nothing ELSE writes the fact. `forbid_elsewhere`
// names the literal and the trees to sweep: any non-test source file outside the declared sites that
// contains it fails the gate. Without this, single-sourcing a mark today just means someone retypes
// it next month and the manifest keeps reporting green over two writers.
const SWEEP_SKIP = new Set(["node_modules", "dist", ".turbo", "__pycache__", ".venv", ".next",
                            "coverage", "test-results", "playwright-report", "tests", "__tests__"]);
const SWEEP_EXT = /\.(py|ts|tsx|js|jsx|mjs|cjs|sh|sql|json|yml|yaml|md)$/;
const sweepTestFile = (n) => /^test_.*\.py$/.test(n) || /_test\.py$/.test(n) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(n);

function sweep(dir, root, needle, hits) {
  let names; try { names = readdirSync(dir); } catch { return hits; }
  for (const name of names) {
    if (name.startsWith(".") || SWEEP_SKIP.has(name)) continue;
    const p = join(dir, name);
    let st; try { st = statSync(p); } catch { continue; }
    if (st.isDirectory()) { sweep(p, root, needle, hits); continue; }
    if (!SWEEP_EXT.test(name) || sweepTestFile(name)) continue;
    let text; try { text = readFileSync(p, "utf8"); } catch { continue; }
    const i = text.indexOf(needle);
    if (i < 0) continue;
    hits.push({ path: p.slice(root.length + 1).replace(/\\/g, "/"), line: text.slice(0, i).split("\n").length });
  }
  return hits;
}

/** Every non-test source file outside `fact.sites` that still writes the literal. */
export function strayWriters(fact, root = DEFAULT_ROOT) {
  if (!fact.forbid_elsewhere) return [];
  const declared = new Set((fact.sites || []).map((s) => s.path));
  const hits = [];
  for (const r of fact.scan || ["core", "clients", "deploy", "services", "libs", "sdks"])
    if (existsSync(join(root, r))) sweep(join(root, r), root, fact.forbid_elsewhere, hits);
  return hits.filter((h) => !declared.has(h.path));
}

export function checkParity(root = DEFAULT_ROOT) {
  const manifest = loadManifest(root);
  const errs = [];
  const results = [];
  const seen = new Set();
  for (const fact of manifest.facts || []) {
    if (seen.has(fact.id)) { errs.push(`duplicate fact id "${fact.id}"`); continue; }
    seen.add(fact.id);
    if (!fact.enforced && !fact.decision)
      errs.push(`${fact.id}: an unenforced fact must name the decision that would settle it ("decision")`);
    const r = checkFact(fact, root);
    for (const h of strayWriters(fact, root))
      r.errs.push(`${fact.id}: ${h.path}:${h.line} writes "${fact.forbid_elsewhere}" and is not a declared site — read it from ${fact.canonical || "the canonical definition"} instead, or add the site to ${MANIFEST_PATH} and say why a third writer is right.`);
    results.push(r);
    errs.push(...r.errs);
  }
  const enforced = (manifest.facts || []).filter((f) => f.enforced);
  const ledger = (manifest.facts || []).filter((f) => !f.enforced);
  const siteCount = (manifest.facts || []).reduce((n, f) => n + (f.sites || []).length, 0);
  return { manifest, results, errs, enforced, ledger, siteCount };
}

/**
 * Write today's answers into each LEDGER fact's `distinct`. This is how a drifted fact is BASELINED,
 * never how one is fixed: it records what the tree says so that the next change to any copy has to
 * be written down. Run it deliberately — the diff it produces IS the review — and never to make a
 * red gate quiet, which is the one use that would turn the ledger back into wallpaper.
 */
export function recordLedger(root = DEFAULT_ROOT) {
  const { manifest, results } = checkParity(root);
  let changed = 0;
  for (const fact of manifest.facts || []) {
    if (fact.enforced) continue;
    const r = results.find((x) => x.id === fact.id);
    if (!r) continue;
    const next = r.distinct.slice().sort();
    if (JSON.stringify(next) !== JSON.stringify((fact.distinct || []).slice().sort())) { fact.distinct = next; changed++; }
  }
  writeFileSync(join(root, MANIFEST_PATH), JSON.stringify(manifest, null, 2) + "\n");
  return changed;
}

// CLI: `node scripts/check-parity.mjs [--ledger|--record]`
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--record")) {
    const n = recordLedger();
    console.log(`recorded today's answers for ${n} ledger fact(s) in ${MANIFEST_PATH} — read the diff, it is the review`);
    process.exit(0);
  }
  const res = checkParity();
  if (process.argv.includes("--ledger")) {
    for (const f of res.ledger) {
      const r = res.results.find((x) => x.id === f.id);
      console.log(`\n■ ${f.id} — ${f.fact}   (${(f.sites || []).length} sites, ${r.distinct.length} answers)`);
      console.log(`  decision: ${f.decision}`);
      for (const v of r.distinct) {
        console.log(`   • ${short(v)}`);
        for (const s of r.byValue.get(v)) console.log(`       ${s}`);
      }
    }
  } else {
    for (const e of res.errs) console.log("✗ " + e);
    console.log(`${res.enforced.length} enforced fact(s) · ${res.ledger.length} on the drift ledger · ${res.siteCount} sites · ${res.errs.length} error(s)`);
  }
  process.exit(res.errs.length ? 1 : 0);
}
