/** GENERATED from core/agent/contracts/unit.v1/unit.schema.json by gen-faults.mjs — DO NOT EDIT.
 *  Regenerate with: node core/agent/contracts/unit.v1/gen-faults.mjs
 *
 *  The typed-fault vocabulary (unit.v1 `Fault`): every `source` a fault may name — WHO failed — and, per
 *  source, every `kind` — HOW. Shared with the worker and agent-api through
 *  core/agent/llm/fault_wire.py and core/agent/shared/fault_wire.py, generated from the same schema. */

export const FAULT_SOURCES = ["runtime", "model-provider", "vexa-tools", "agent-worker", "agent-api", "gateway"] as const;
export type FaultSource = (typeof FAULT_SOURCES)[number];

export const FAULT_KINDS = {
  runtime: ["spawn_refused", "quota_exceeded", "unauthorized", "refused", "not_found", "unreachable", "unavailable", "bad_response"],
  "model-provider": ["unpaid", "unauthorized", "rate_limited", "unavailable", "refused", "unknown_model", "not_permitted", "not_configured", "credential_missing", "endpoint_refused", "effort_unsupported"],
  "vexa-tools": ["access_expired"],
  "agent-worker": ["tools_unconfined", "credential_conflict"],
  "agent-api": ["internal", "unavailable"],
  gateway: ["unreachable"],
} as const;
export type FaultKind = (typeof FAULT_KINDS)[FaultSource][number];

export const isFaultSource = (x: unknown): x is FaultSource =>
  typeof x === "string" && (FAULT_SOURCES as readonly string[]).includes(x);

/** Is `kind` in `source`'s vocabulary? A source this release does not know has none. */
export const isFaultKind = (source: string, kind: unknown): kind is FaultKind =>
  isFaultSource(source) && typeof kind === "string" && (FAULT_KINDS[source] as readonly string[]).includes(kind);
