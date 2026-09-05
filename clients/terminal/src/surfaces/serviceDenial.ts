/** serviceDenial — how the terminal shows a refusal that SOMEONE ELSE authored.
 *
 *  The meeting API refuses a bot with `403 {"detail":{"code":"service_not_allowed","reason":…,
 *  "decision_id":…,"message"?,"action_url"?}}` and reports its own outage as
 *  `503 {"detail":{"code":"service_authority_unavailable",…}}`
 *  (core/meetings/services/meeting-api/src/meeting_api/bot_spawn/router.py). `code` and `reason`
 *  are for a program to branch on; `message` and `action_url` are for whoever has to DO something
 *  about it, and the deciding service authors both (Vexa-ai/vexa#1532).
 *
 *  So this module knows no vocabulary. It used to carry a seven-value reason union, a table of
 *  customer copy and button labels, and a hosted account origin — the THIRD hand-maintained twin of
 *  a mapping that lives in the deciding service, and the reason a refusal it had never heard of
 *  reached a customer as a raw code. None of that is here now: whatever the decider said is what
 *  the user sees, and a reason this build has never heard of renders exactly as well as one it has.
 *  Billing is not the open product's business (Vexa-ai/vexa#1548).
 *
 *  Same rendering as the MCP surface's `render_tool_error`
 *  (core/meetings/services/mcp/src/vexa_mcp/tool_errors.py):
 *    · line 1  — `<reason>: <message>` when the decider authored a message; the bare `reason` when
 *                it did not; `HTTP <status> <code>` when it said neither.
 *    · line 2  — `HTTP <status> <code>`, the machine-readable half, always.
 *    · the `action_url` verbatim, as the place to resolve it. No label, no path, no origin: the
 *      decider names the whole URL or there is nowhere to send anyone.
 *
 *  The ONLY branch is structural — a body carrying one of the two codes below is a decision about
 *  service, and anything else on a 403 is a genuine permission fault that keeps its own words.
 *
 *  Deliberately free of React and of network access.
 */
import { ApiError, presentError } from "./apiClient";

export const SERVICE_NOT_ALLOWED_CODE = "service_not_allowed";
export const SERVICE_AUTHORITY_UNAVAILABLE_CODE = "service_authority_unavailable";

/** The two codes that mean "the deciding service ruled on this", as opposed to an authz fault. */
const DENIAL_CODES: readonly string[] = [
  SERVICE_NOT_ALLOWED_CODE,
  SERVICE_AUTHORITY_UNAVAILABLE_CODE,
];

export interface ServiceDenialPresentation {
  /** Whatever the decider said, passed through. Never interpreted, never matched against a list. */
  reason: string;
  /** The structural code the body was recognised by. */
  code: string;
  status: number;
  /** Line 1 — the words a human acts on, authored upstream. */
  headline: string;
  /** Line 2 — `HTTP <status> <code>`. */
  detail: string;
  /** Where the decider says this is resolved, verbatim, or null when it named nowhere. */
  actionUrl: string | null;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

/** Envelopes are pathological long before this; the bound just stops a cyclic structure spinning. */
const MAX_UNWRAP = 10;

/** Peel nested `{"detail": …}` envelopes down to the innermost value. Peels only when `detail` is
 *  the SOLE key, so a body that carries siblings is never truncated. */
function unwrapDetail(body: unknown): unknown {
  let current = body;
  for (let depth = 0; depth < MAX_UNWRAP; depth += 1) {
    if (typeof current !== "object" || current === null || Array.isArray(current)) return current;
    const keys = Object.keys(current as Record<string, unknown>);
    if (keys.length !== 1 || keys[0] !== "detail") return current;
    current = (current as Record<string, unknown>).detail;
  }
  return current;
}

/** Reads a decision out of an API failure body, at the status it arrived on.
 *
 *  Two shapes are accepted because both are on the wire: FastAPI nests the payload under `detail`
 *  (`{"detail":{"code":…}}`, which is what `POST /bots` emits), and the platform routes emit the
 *  object bare. Returns null when the body is not one of the two decision codes, so callers keep
 *  their own handling for genuine auth or transport faults. */
export function serviceDenialFromResponseBody(
  body: unknown,
  status: number,
): ServiceDenialPresentation | null {
  const inner = unwrapDetail(body);
  if (typeof inner !== "object" || inner === null || Array.isArray(inner)) return null;
  const record = inner as Record<string, unknown>;
  const code = text(record.code);
  if (!DENIAL_CODES.includes(code)) return null;

  const reason = text(record.reason);
  const message = text(record.message);
  const machine = `HTTP ${status} ${code}`;
  const headline = message
    ? (reason ? `${reason}: ${message}` : message)
    : (reason || machine);

  return {
    reason,
    code,
    status,
    headline,
    detail: machine,
    actionUrl: text(record.action_url) || null,
  };
}

/** Reads a decision off a thrown error. Only 4xx/5xx bodies carrying one of the decision codes
 *  qualify — a 401, or a 403 that is a genuine permission fault, returns null and keeps its
 *  access-error rendering.
 *
 *  The terminal's `ApiError` flattens the backend `detail` to a string for the operator channel, so
 *  the STRUCTURED body is carried alongside it (`ApiError.body`) and read here. A body that only
 *  survived as a JSON string is still parsed, so a call site that predates the structured field
 *  cannot silently render a refusal as "access denied". */
export function serviceDenialFromError(error: unknown): ServiceDenialPresentation | null {
  if (!(error instanceof ApiError)) return null;
  if (error.status === 401) return null;
  const fromBody = serviceDenialFromResponseBody(error.body, error.status);
  if (fromBody) return fromBody;
  return serviceDenialFromResponseBody(parseMaybeJson(error.detail), error.status);
}

function parseMaybeJson(detail: string): unknown {
  const t = detail.trim();
  if (!t.startsWith("{")) return null;
  try {
    return JSON.parse(t);
  } catch {
    return null;
  }
}

/** The rendering states a failed Join can land in.
 *
 *  Kept as a pure function so every join surface (the sidebar "Add bot", the drop-a-bot card, the
 *  row "Send now", the prep tab) agrees on which state a given error produces, and so the mapping
 *  is testable without a DOM.
 *
 *  - `denial`  — the deciding service ruled. Rendered as an in-flow panel carrying that service's
 *                own words and, where it named one, its `action_url`. Never a transient line: a
 *                refusal the customer must act on should not read like a typo in the meeting link.
 *  - `message` — everything else, including genuine authz failures; the existing presenter seam
 *                (`presentError`) owns the words. */
export type JoinErrorState =
  | { kind: "denial"; presentation: ServiceDenialPresentation }
  | { kind: "message"; headline: string };

export function resolveJoinError(error: unknown): JoinErrorState {
  const denial = serviceDenialFromError(error);
  if (denial) return { kind: "denial", presentation: denial };
  return { kind: "message", headline: presentError(error).headline };
}

/** True when the error is a genuine authorization failure, not a service decision. */
export function isAccessError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false;
  if (error.status === 401) return true;
  if (error.status !== 403) return false;
  return serviceDenialFromError(error) === null;
}
