/**
 * ajv validators that LOAD the on-disk SEALED schemas and validate live data against them.
 *
 * The schemas are the spec — we never copy them in. We walk up parent dirs to find
 * `core/gateway/contracts/` (the same find-the-contracts move `test_lifecycle_durable.py` /
 * `collector_contracts.py` use), load `ws.v1/ws.schema.json` + `api.v1/api.schema.json`, and compile
 * a per-shape validator from each schema's `$defs` (ws) / `components.schemas` (api) — exactly as the
 * lane gates' `validate.mjs` do.
 *
 *   validateWsFrame(name, frame)   → conforms `frame` to ws.v1 `#/$defs/<name>`
 *   validateApiShape(name, obj)    → conforms `obj`   to api.v1 `#/components/schemas/<name>`
 */
import Ajv2020, { type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));

/** Walk up from HERE looking for `<root>/<rel>`; throw with the tried roots if not found. */
function findContract(rel: string): string {
  // The contracts moved under `core/` in the 0.12 reorg; accept the legacy top-level layout too.
  const candidates = [join("core", "gateway", "contracts", rel), join("gateway", "contracts", rel)];
  let dir = HERE;
  const tried: string[] = [];
  while (true) {
    for (const c of candidates) {
      const p = join(dir, c);
      tried.push(p);
      if (existsSync(p)) return p;
    }
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`contract not found: ${rel}\n  tried:\n    ${tried.join("\n    ")}`);
}

function readJson(path: string): any {
  return JSON.parse(readFileSync(path, "utf8"));
}

// ── ws.v1 ─────────────────────────────────────────────────────────────────────────────────────
const wsSchema = readJson(findContract(join("ws.v1", "ws.schema.json")));
const wsAjv = new Ajv2020({ strict: false, allErrors: true });
addFormats(wsAjv);
wsAjv.addSchema(wsSchema);
const wsCache = new Map<string, ValidateFunction>();

/** All ws.v1 `$defs` shape names (e.g. "MeetingStatus", "Transcript", "Subscribed"). */
export const WS_SHAPES: string[] = Object.keys(wsSchema.$defs ?? {});

/** Validate `frame` against ws.v1 `#/$defs/<shape>`. Throws if `shape` is unknown. */
export function validateWsFrame(shape: string, frame: unknown): { valid: boolean; errors: string } {
  if (!WS_SHAPES.includes(shape)) throw new Error(`unknown ws.v1 shape: ${shape}`);
  let v = wsCache.get(shape);
  if (!v) {
    v = wsAjv.compile({ $ref: `${wsSchema.$id}#/$defs/${shape}` });
    wsCache.set(shape, v);
  }
  const valid = v(frame) as boolean;
  return { valid, errors: valid ? "" : wsAjv.errorsText(v.errors) };
}

// ── api.v1 ────────────────────────────────────────────────────────────────────────────────────
const API_BASE = "https://vexa.ai/contracts/api.v1";
const apiSchema = readJson(findContract(join("api.v1", "api.schema.json")));
const apiAjv = new Ajv2020({ strict: false, allErrors: true });
addFormats(apiAjv);
apiAjv.addSchema(apiSchema, API_BASE); // internal "#/components/schemas/X" refs resolve against BASE

/** All api.v1 `components.schemas` shape names. */
export const API_SHAPES: string[] = Object.keys(apiSchema.components?.schemas ?? {});

/** Validate `obj` against api.v1 `#/components/schemas/<shape>`. Throws if `shape` is unknown. */
export function validateApiShape(shape: string, obj: unknown): { valid: boolean; errors: string } {
  const v = apiAjv.getSchema(`${API_BASE}#/components/schemas/${shape}`) as ValidateFunction | undefined;
  if (!v) throw new Error(`unknown api.v1 shape: ${shape}`);
  const valid = v(obj) as boolean;
  return { valid, errors: valid ? "" : apiAjv.errorsText(v.errors) };
}

/** The api.v1 OpenAPI identity, for sanity assertions (title + version). */
export const apiIdentity = {
  openapi: apiSchema.openapi as string,
  title: apiSchema.info?.title as string,
  version: apiSchema.info?.version as string,
};
