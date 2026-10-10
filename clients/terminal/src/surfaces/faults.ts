/** faults — a dependency's TYPED failure, as the chat shows it (P18, ADR-0010).
 *
 *  The founder, 0.13.2 demo stack: he sent "hi", the runtime refused to start his agent, and the chat
 *  said "Internal Server Error" while the message sat "queued behind the current turn" — *"this fails
 *  our 'fail loud' principle"*. Nothing on screen said WHO failed, or that anything had.
 *
 *  The server now types every failure on the chat path at its adapter:
 *    · `source: "runtime"` — agent-api could not get an agent started (`shared/runtime_fault.py`);
 *    · `source: "model-provider"` — the model's provider refused the turn (`llm/faults.py`);
 *    · `source: "agent-api"` / `"gateway"` — the terminal's own chat proxy could not get a typed
 *      answer at all (`app/api/chat/route.ts`, its floor under every 5xx).
 *  This file is the ONE place the chat turns that record into words: who failed, what kind of
 *  failure, the safe detail the server wrote, and the remedy. Every renderer reads it from here, so a
 *  bubble, a queued row and a test can never spell the same fault two ways.
 */

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

const SOURCE_LABEL: Record<string, string> = {
  runtime: "Agent runtime",
  "model-provider": "Model provider",
  "agent-api": "Agent service",
  gateway: "Vexa gateway",
};

const KIND_LABEL: Record<string, string> = {
  // runtime (`shared/runtime_fault.KINDS`)
  spawn_refused: "could not start your agent",
  quota_exceeded: "agent limit reached",
  unreachable: "unreachable",
  unavailable: "unavailable",
  bad_response: "answered unreadably",
  not_found: "agent not found",
  // model provider (`llm/faults.KINDS`)
  unpaid: "out of credit",
  rate_limited: "rate limited",
  refused: "refused the request",
  // both
  unauthorized: "credential refused",
  // the terminal's own proxy
  internal: "failed",
};

/** WHO failed, in words. An unknown source is named as itself rather than hidden. */
export function faultSource(f: Fault): string {
  const base = SOURCE_LABEL[f.source] ?? f.source;
  return f.source === "model-provider" && f.provider && f.provider !== "unknown" ? `${base} (${f.provider})` : base;
}

/** WHAT KIND of failure, in words, with the status beside it when there was one. */
export function faultKind(f: Fault): string {
  const base = KIND_LABEL[f.kind] ?? f.kind.replace(/_/g, " ");
  return typeof f.status === "number" && f.status > 0 ? `${base} (${f.status})` : base;
}

/** The headline a fault renders under: `Model provider (openrouter.ai) · out of credit (402)`. */
export function faultHeadline(f: Fault): string {
  return `${faultSource(f)} · ${faultKind(f)}`;
}

/** The whole fault on ONE line — a queued row, a tooltip, a log. */
export function faultLine(f: Fault): string {
  return [faultHeadline(f), f.detail].filter(Boolean).join(" — ");
}

