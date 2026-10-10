#!/usr/bin/env node
/**
 * gate:schema for outbound-url.v1 — validate the golden case table, and every copy of it that a
 * test suite reads (the sites of the `outbound-url-vectors` fact in scripts/parity.json, ADR-0042),
 * against outbound-url.schema.json. Beyond the schema: no case appears twice in a section (a URL
 * case is the URL with its resolver answer).
 * Run: node validate.mjs [--check] [--file PATH]...
 */
import Ajv2020 from "ajv/dist/2020.js";
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..", "..", "..");
const schema = JSON.parse(readFileSync(join(HERE, "outbound-url.schema.json"), "utf8"));
const ajv = new Ajv2020({ strict: false, allErrors: true });
const validate = ajv.compile(schema);

const extra = [];
for (let i = 2; i < process.argv.length; i++) if (process.argv[i] === "--file") extra.push(resolve(process.argv[++i]));

const goldenDir = join(HERE, "golden");
const goldens = readdirSync(goldenDir).filter((n) => n.endsWith(".json")).map((n) => join(goldenDir, n));
const parity = JSON.parse(readFileSync(join(ROOT, "scripts", "parity.json"), "utf8"));
const fact = parity.facts.find((f) => f.id === "outbound-url-vectors");
if (!fact) { console.error("  ✗ scripts/parity.json has no outbound-url-vectors fact"); process.exit(1); }
const copies = fact.sites.map((s) => join(ROOT, s.path));
const files = [...new Set([...goldens, ...copies, ...extra])];

const KEY = { addresses: (c) => c.addr, hostnames: (c) => c.host, urls: (c) => JSON.stringify([c.url, c.resolved]) };
let failed = 0;
for (const f of files) {
  const label = relative(ROOT, f);
  if (!existsSync(f)) { console.error(`  ✗ ${label}: missing`); failed++; continue; }
  let data;
  try { data = JSON.parse(readFileSync(f, "utf8")); }
  catch (e) { console.error(`  ✗ ${label}: ${e.message}`); failed++; continue; }
  if (!validate(data)) { console.error(`  ✗ ${label}: ${ajv.errorsText(validate.errors)}`); failed++; continue; }
  const errs = [];
  for (const [section, key] of Object.entries(KEY)) {
    const seen = new Set();
    for (const c of data[section]) {
      const k = key(c);
      if (seen.has(k)) errs.push(`${section}: ${k} appears twice`);
      seen.add(k);
    }
  }
  if (errs.length) { for (const e of errs) console.error(`  ✗ ${label}: ${e}`); failed++; continue; }
  console.log(`  ✓ ${label}`);
}
if (failed) { console.error(`outbound-url.v1: ${failed} file(s) do not conform`); process.exit(1); }
