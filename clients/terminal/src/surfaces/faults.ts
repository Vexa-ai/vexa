/** faults — a dependency's TYPED failure, as the chat shows it (P18, ADR-0010).
 *
 *  The founder, 0.13.2 demo stack: he sent "hi", the runtime refused to start his agent, and the chat
 *  said "Internal Server Error" while the message sat "queued behind the current turn" — *"this fails
 *  our 'fail loud' principle"*. Nothing on screen said WHO failed, or that anything had.
 *
 *  The server now types every failure on the chat path at its adapter:
 *    · `source: "runtime"` — agent-api could not get an agent started (`shared/runtime_fault.py`);
 *    · `source: "model-provider"` — the model's provider refused the turn (`llm/faults.py`);
 *    · `source: "vexa-tools"` — the turn ran past its tool access and its Vexa tool calls were
 *      refused (`worker/tool_access.py`);
 *    · `source: "agent-worker"` — the worker refused to start the turn: it could not hand the turn's
 *      workspace to the user the model's tools run as (`worker/tool_access.py`), or the environment
 *      holds a model credential it could not remove (`llm/claude_code.py`);
 *    · `source: "agent-api"` / `"gateway"` — the terminal's own chat proxy could not get a typed
 *      answer at all (`app/api/chat/route.ts`, its floor under every 5xx).
 *  This file is the ONE place the chat turns that record into words: who failed, what kind of
 *  failure, the safe detail the server wrote, and the remedy. Every renderer reads it from here, so a
 *  bubble, a queued row and a test can never spell the same fault two ways.
 *
 *  THE VOCABULARY IS THE CONTRACT'S (unit.v1 `Fault`, S65). Every source and kind comes from
 *  `./faultWire.ts`, generated from core/agent/contracts/unit.v1/unit.schema.json with the worker's
 *  and agent-api's copies; the label tables below are typed by it, so a kind the contract adds and
 *  this file does not name fails `tsc`, and one it names that the contract does not, too.
 */
import type { FaultKind, FaultSource } from "./faultWire";

export type Fault = {
  /** WHO failed — the dependency, never "something" */
  source: string;
  /** HOW it failed, in the server's closed vocabulary (e.g. `spawn_refused`, `unpaid`) */
  kind: string;
  /** one safe sentence the server wrote — never the dependency's raw text */
  detail?: string;
  /** what the person (or their operator) can do about it */
  remedy?: string;
  /** the dependency's HTTP status, when it answered with one */
  status?: number | null;
  /** model-provider only: the host the turn went to, and the model it asked for */
  provider?: string;
  model?: string;
  /** runtime only: which call failed (`spawn`, `status`, `list`) */
  op?: string;
};

/** Is this a typed fault? A server one release behind sends none, and a half-record (no source or
 *  no kind) is treated as no record — the caller falls back to the plain message it always had. */
export function readFault(x: unknown): Fault | null {
  if (!x || typeof x !== "object") return null;
  const f = x as Record<string, unknown>;
  if (typeof f.source !== "string" || !f.source || typeof f.kind !== "string" || !f.kind) return null;
  const str = (v: unknown) => (typeof v === "string" && v.trim() ? v.trim() : undefined);
  return {
    source: f.source,
    kind: f.kind,
    detail: str(f.detail),
    remedy: str(f.remedy),
    status: typeof f.status === "number" ? f.status : null,
    provider: str(f.provider),
    model: str(f.model),
    op: str(f.op),
  };
}

/** WHO failed, per contract source. Exported for the contract test, never for a second renderer. */
export const SOURCE_LABEL: Readonly<Record<FaultSource, string>> = {
  runtime: "Agent runtime",
  "model-provider": "Model provider",
  "vexa-tools": "Vexa tools",
  "agent-worker": "Your agent",
  "agent-api": "Agent service",
  gateway: "Vexa gateway",
};

/** HOW it failed, per contract kind — one label for a kind several sources share. */
export const KIND_LABEL: Readonly<Record<FaultKind, string>> = {
  spawn_refused: "could not start your agent",
  quota_exceeded: "agent limit reached",
  unreachable: "unreachable",
  unavailable: "unavailable",
  bad_response: "answered unreadably",
  not_found: "agent not found",
  unpaid: "out of credits",
  rate_limited: "rate limited",
  refused: "refused the request",
  unauthorized: "credential refused",
  internal: "failed",
  access_expired: "tool access expired",
  tools_unconfined: "could not confine the model's tools",
  credential_conflict: "another model credential is mounted",
};

/** A label table read with a source or kind a newer server may send and this release cannot know. */
const label = (table: Readonly<Record<string, string>>, key: string): string | undefined =>
  Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;

/** WHO failed, in words. An unknown source is named as itself rather than hidden. The provider's
 *  host is not part of it: the headline reads `Model provider · out of credits (402)`, and the
 *  remedy line already says where to act ("Add credits at openrouter.ai…"). */
export function faultSource(f: Fault): string {
  return label(SOURCE_LABEL, f.source) ?? f.source;
}

/** WHAT KIND of failure, in words, with the status beside it when there was one. */
export function faultKind(f: Fault): string {
  const base = label(KIND_LABEL, f.kind) ?? f.kind.replace(/_/g, " ");
  return typeof f.status === "number" && f.status > 0 ? `${base} (${f.status})` : base;
}

/** The headline a fault renders under: `Model provider · out of credits (402)`. */
export function faultHeadline(f: Fault): string {
  return `${faultSource(f)} · ${faultKind(f)}`;
}

/** The whole fault on ONE line — a queued row, a tooltip, a log. */
export function faultLine(f: Fault): string {
  return [faultHeadline(f), f.detail].filter(Boolean).join(" — ");
}

