#!/usr/bin/env node
// No extension source puts the API key in a URL (card ca078278e83d). The key rides the
// ws.v1 subprotocol list (src/ingest-socket.ts) or the X-API-Key header (REST); the
// query-string form `api_key=` is what leaked into logs and what 0.13.4 refuses.
// A grep over src/ (tests excluded): any `api_key` occurrence fails.
import { readFileSync, readdirSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");
const offenders = [];
(function walk(d) {
  for (const e of readdirSync(d, { withFileTypes: true })) {
    const p = join(d, e.name);
    if (e.isDirectory()) { walk(p); continue; }
    if (!/\.(ts|tsx|js|html)$/.test(e.name) || /\.test\.ts$/.test(e.name)) continue;
    readFileSync(p, "utf8").split("\n").forEach((line, i) => {
      if (/api_key/.test(line)) offenders.push(`${p.slice(SRC.length + 1)}:${i + 1}: ${line.trim()}`);
    });
  }
})(SRC);
if (offenders.length) {
  console.log("  ❌ api_key found in extension source (a key must never ride a URL):");
  for (const o of offenders) console.log("     " + o);
  process.exit(1);
}
console.log("  ✅ no extension source mentions api_key (the URL key form)");
