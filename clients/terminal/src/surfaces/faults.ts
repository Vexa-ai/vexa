/** faults — a dependency's TYPED failure, as the chat shows it (P18, ADR-0010).
 *
 *  The founder, 0.13.2 demo stack: he sent "hi", the runtime refused to start his agent, and the chat
 *  said "Internal Server Error" while the message sat "queued behind the current turn" — *"this fails
 *  our 'fail loud' principle"*. Nothing on screen said WHO failed, or that anything had.
 *
 *  The server now types every failure on the chat path at its adapter:
 *    · `source: "runtime"` — agent-api could not get an agent started (`shared/runtime_fault.py`);
 *    · `source: "model-provider"` — the model's provider refused the turn (`llm/faults.py`), or
 *      agent-api refused the chat's model pick before asking it (`control_plane/model_providers`);
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
import { TRANSIENT_STATUS } from "./transientRetry";

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
  /** session-store only: the conversation the refusal is about */
  session?: string;
  /** what the person can do in one press, when the server names it (`stop` — stop the turn still
   *  running in this conversation) */
  action?: string;
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
    ...(str(f.session) ? { session: str(f.session) } : {}),
    ...(str(f.action) ? { action: str(f.action) } : {}),
  };
}

/** A PREVIOUS REPLY IS STILL RUNNING in this conversation (agent-api's 409, `session-store ·
 *  session_conflict`). Not a failure of anything: the turn in front has not ended, and the one
 *  control that helps is stopping it — so it renders as one calm line with that control, and the
 *  store's own vocabulary never reaches the screen (app.dev 2026-10-15: "Session conflict. Retry —
 *  session-store · session conflict", under a Retry that could not work while the old turn ran). */
export function isSessionConflict(f: Pick<Fault, "source" | "kind">): boolean {
  return f.source === "session-store" && f.kind === "session_conflict";
}

/** THE SESSION STORE'S WORDS. `session-store` is not in unit.v1's `Fault` vocabulary (faultWire.ts
 *  is generated from it, and SOURCE_LABEL/KIND_LABEL must equal it exactly — faultContract.test), so
 *  its labels live here, beside it, until the schema names the source. */
const STORE_SOURCE_LABEL: Readonly<Record<string, string>> = { "session-store": "Conversation" };
const STORE_KIND_LABEL: Readonly<Record<string, string>> = {
  session_conflict: "previous reply still running",
  session_unavailable: "store not answering",
};
const SESSION_CONFLICT_SUMMARY = "A previous reply is still running.";

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
  // the chat's model pick, refused by agent-api before any request (`control_plane/model_providers`)
  unknown_model: "model no longer offered",
  not_permitted: "model not open to you",
  not_configured: "model not set up for you",
  credential_missing: "provider credential not set",
  endpoint_refused: "your endpoint is not allowed",
  effort_unsupported: "effort level not offered",
  unauthorized: "credential refused",
  internal: "failed",
  access_expired: "tool access expired",
  tools_unconfined: "could not confine the model's tools",
  credential_conflict: "another model credential is mounted",
  no_tool_calling: "cannot call tools",
};

/** A label table read with a source or kind a newer server may send and this release cannot know. */
const label = (table: Readonly<Record<string, string>>, key: string): string | undefined =>
  Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;

/** WHO failed, in words. An unknown source is named as itself rather than hidden. The provider's
 *  host is not part of it: the headline reads `Model provider · out of credits (402)`, and the
 *  remedy line already says where to act ("Add credits at openrouter.ai…"). */
export function faultSource(f: Fault): string {
  return label(SOURCE_LABEL, f.source) ?? label(STORE_SOURCE_LABEL, f.source) ?? f.source;
}

/** A kind in words: the contract's label, the session store's, or the kind itself made readable. */
function kindWords(f: Fault): string {
  const own = f.source === "session-store" ? label(STORE_KIND_LABEL, f.kind) : undefined;
  return own ?? label(KIND_LABEL, f.kind) ?? f.kind.replace(/_/g, " ");
}

/** WHAT KIND of failure, in words, with the status beside it when there was one. */
export function faultKind(f: Fault): string {
  const base = kindWords(f);
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


// ── how loud a fault is (the founder, 2026-10-13: "UPDATE UI SO THAT WE DO NOT HAVE RED DANGEROUS
//    THINGS HERE") ───────────────────────────────────────────────────────────────────────────────
// A one-second gateway 503 drew a red box naming an internal service and its status code, and he
// re-sent the same message three times. Most faults are a moment's unavailability, or a setting
// someone has to change; neither is dangerous. Red is kept for what is.

/** Kinds that clear by themselves: something was down, busy, or still letting go of the last agent. */
const TRANSIENT_KINDS: ReadonlySet<string> = new Set(
  ["unavailable", "unreachable", "rate_limited", "spawn_refused", "quota_exceeded"]);

/** Is this the kind of failure that clears by itself — worth retrying, not worth alarming anyone? */
export function isTransientFault(f: Fault): boolean {
  if (!TRANSIENT_KINDS.has(f.kind)) return false;
  return f.status === null || f.status === undefined || TRANSIENT_STATUS.has(f.status);
}

/** Kinds that are a security refusal: the worker would not run the turn because it could not keep
 *  the model's tools confined, or found a credential it must not run beside. The only ones in red. */
const DANGER_KINDS: ReadonlySet<string> = new Set(["tools_unconfined", "credential_conflict"]);

export type FaultTone = "neutral" | "danger";
export function faultTone(f: Fault): FaultTone {
  return DANGER_KINDS.has(f.kind) ? "danger" : "neutral";
}

/** The one sentence a fault shows by default — the person's words, never a status code or the name
 *  of an internal service; those are under "details" (`faultHeadline`). */
export function faultSummary(f: Fault): string {
  if (isTransientFault(f)) {
    return f.source === "model-provider"
      ? "The model didn't answer just now."
      : "Couldn't reach Vexa just now.";
  }
  if (isSessionConflict(f) && !f.detail) return SESSION_CONFLICT_SUMMARY;
  if (f.detail) {
    const t = f.detail.charAt(0).toUpperCase() + f.detail.slice(1);
    return /[.!?…]$/.test(t) ? t : `${t}.`;
  }
  const k = kindWords(f);
  return `${k.charAt(0).toUpperCase()}${k.slice(1)}.`;
}
