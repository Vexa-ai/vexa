#!/usr/bin/env node
/**
 * gate:schema for api.v1 — the PUBLIC surface, frozen IDENTICAL to vexa main's api-gateway.
 *
 * `api.schema.json` is the OpenAPI 3.1 document emitted by main's api-gateway (title
 * "Vexa API Gateway", version 1.5.0) — captured verbatim, sealed by `contracts.seal.json`
 * so the v0.12 services (meeting-api, dashboard, bot) build against the REAL production
 * surface, not invented shapes. This validator pins main's identity + the core paths every
 * consumer depends on, and checks each golden `<Shape>.<case>.json` against the frozen
 * `#/components/schemas/<Shape>`. Re-capture (a deliberate main bump) → re-seal on lane:contract.
 */
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readdirSync, readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const oas = JSON.parse(readFileSync(join(HERE, "api.schema.json"), "utf8"));
let failed = 0;
const check = (name, cond) => { console.log(`  ${cond ? "✓" : "✗"} ${name}`); if (!cond) failed++; };

// ── 1) IDENTITY — this must be main's api-gateway, unchanged ──────────────────
check(`openapi 3.1.x (got ${oas.openapi})`, /^3\.1\./.test(oas.openapi || ""));
check(`info.title == "Vexa API Gateway" (got "${oas.info?.title}")`, oas.info?.title === "Vexa API Gateway");
check(`info.version == "1.5.0" (got "${oas.info?.version}")`, oas.info?.version === "1.5.0");

// ── 2) CORE PATHS — the surface the eval, dashboard, bot + meeting-api depend on ──
const CORE = [
  ["/bots", "get"], ["/bots", "post"], ["/bots/status", "get"],
  ["/bots/{platform}/{native_meeting_id}", "delete"],
  ["/bots/{platform}/{native_meeting_id}/config", "put"],
  ["/bots/{platform}/{native_meeting_id}/speak", "post"],
  ["/transcripts/{platform}/{native_meeting_id}", "get"],
  ["/recordings", "get"], ["/recordings/{recording_id}", "get"],
  ["/meetings", "get"],
];
for (const [p, m] of CORE) check(`${m.toUpperCase()} ${p}`, !!oas.paths?.[p]?.[m]);

// ── 3) GOLDENS — example messages conform to the frozen component schemas ─────
const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
const BASE = "https://vexa.ai/contracts/api.v1";
ajv.addSchema(oas, BASE);   // internal "#/components/schemas/X" refs resolve against BASE
const dir = join(HERE, "golden");
for (const f of readdirSync(dir).filter((n) => n.endsWith(".json"))) {
  const shape = f.split(".")[0];
  const validate = ajv.getSchema(`${BASE}#/components/schemas/${shape}`);
  if (!validate) { check(`golden ${f} → schema ${shape} exists`, false); continue; }
  const data = JSON.parse(readFileSync(join(dir, f), "utf8"));
  check(`golden ${f} ≡ ${shape}${validate(data) ? "" : " — " + ajv.errorsText(validate.errors)}`, validate(data));
}

console.log(failed ? `\napi.v1: ${failed} check(s) FAILED` : `\napi.v1: all checks pass (frozen ≡ vexa main api-gateway 1.5.0)`);
process.exit(failed ? 1 : 0);
