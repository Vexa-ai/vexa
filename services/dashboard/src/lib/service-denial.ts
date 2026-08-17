import { VexaAPIError } from "@/lib/api";

/**
 * The customer-facing rendering of a refused Join.
 *
 * The meeting API refuses a bot with
 * `403 {"detail":{"code":"service_not_allowed","reason":<reason>,"decision_id":…}}`.
 * Before this change every one of those reasons reached the customer as a bare
 * "Access denied", so a paywall was indistinguishable from an outage and from a
 * permissions fault.
 *
 * This is the dashboard-side twin of `lib/service-denial-view.ts` in
 * Vexa-ai/vexa-platform (PR #292). The two surfaces must say the same sentence
 * about the same account, so the copy below is kept byte-aligned with that
 * module; change one, change both.
 *
 * Deliberately free of React and of network access.
 */

export const SERVICE_NOT_ALLOWED_CODE = "service_not_allowed";
export const SERVICE_AUTHORITY_UNAVAILABLE_CODE = "service_authority_unavailable";

/**
 * The vocabulary the service authority emits. Mirrors `ServiceAuthorityReason`
 * in the platform repo. A reason outside this union is rendered verbatim rather
 * than flattened — see {@link unknownServiceDenial}.
 */
export type ServiceAuthorityReason =
  | "allowed"
  | "billing_setup_required"
  | "insufficient_balance"
  | "payment_past_due"
  | "spend_cap_reached"
  | "concurrency_limit_reached"
  | "billing_unavailable";

/**
 * What the customer can DO about it, which is the only distinction the UI needs
 * to style:
 *
 * - `paywall`   — they are out of money; one payment fixes it.
 * - `setup`     — billing is not finished; one setup flow fixes it.
 * - `limit`     — a ceiling they chose or bought; the number is named.
 * - `retryable` — our side; the account is fine and waiting helps.
 * - `unknown`   — a reason this build has never heard of. Shown verbatim.
 */
export type ServiceDenialKind =
  | "paywall"
  | "setup"
  | "limit"
  | "retryable"
  | "unknown";

export interface ServiceDenialAction {
  label: string;
  /** Path on the webapp (billing/account) origin, not on the dashboard. */
  href: string;
}

export interface ServiceDenialPresentation {
  reason: string;
  kind: ServiceDenialKind;
  title: string;
  body: string;
  action: ServiceDenialAction | null;
  /** True when simply trying again later is a sane instruction. */
  retryable: boolean;
}

export interface ServiceDenialFacts {
  /** Live prepaid balance in cents, when the caller knows it. */
  balanceCents?: number | null;
  /** Concurrent-bot ceiling, for the concurrency reason. */
  concurrencyCeiling?: number | null;
  /** Human plan label, e.g. "Pay-as-you-go". */
  planLabel?: string | null;
}

const ADD_FUNDS: ServiceDenialAction = {
  label: "Add funds",
  href: "/account?tab=bots",
};

const FINISH_SETUP: ServiceDenialAction = {
  label: "Finish billing setup",
  href: "/account?tab=bots",
};

const UPDATE_PAYMENT: ServiceDenialAction = {
  label: "Update payment method",
  href: "/account?tab=balance",
};

const REVIEW_SPEND_CAP: ServiceDenialAction = {
  label: "Review spend cap",
  href: "/account?tab=balance",
};

function usd(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`;
}

function balanceSentence(facts: ServiceDenialFacts): string {
  const cents = facts.balanceCents;
  if (typeof cents !== "number" || !Number.isSafeInteger(cents) || cents < 0) {
    return "Your prepaid balance is empty.";
  }
  const plan = facts.planLabel ? ` on ${facts.planLabel}` : "";
  return `Your prepaid balance${plan} is ${usd(cents)}.`;
}

function concurrencySentence(facts: ServiceDenialFacts): string {
  const ceiling = facts.concurrencyCeiling;
  if (
    typeof ceiling !== "number" ||
    !Number.isSafeInteger(ceiling) ||
    ceiling < 0
  ) {
    return "Every bot your plan allows is already in a meeting.";
  }
  return (
    `Your plan runs ${ceiling} bot${ceiling === 1 ? "" : "s"} at once, ` +
    "and they are all in meetings."
  );
}

/**
 * `reason` is whatever the API said. A reason this build does not know is
 * rendered VERBATIM rather than flattened into "Access denied" — an unmapped
 * code in front of the customer is a bug we want reported, not hidden.
 */
export function serviceDenialPresentation(
  reason: string,
  facts: ServiceDenialFacts = {},
): ServiceDenialPresentation {
  switch (reason as ServiceAuthorityReason) {
    case "allowed":
      // Not a denial. Callers that reach here have mismatched allow/reason.
      return {
        reason,
        kind: "unknown",
        title: "Service not allowed",
        body: `service not allowed: ${reason}`,
        action: null,
        retryable: true,
      };
    case "insufficient_balance":
      return {
        reason,
        kind: "paywall",
        title: "Out of credit",
        body:
          `${balanceSentence(facts)} Add funds and the bot joins ` +
          "immediately — nothing else about your account needs changing.",
        action: ADD_FUNDS,
        retryable: false,
      };
    case "billing_setup_required":
      return {
        reason,
        kind: "setup",
        title: "Billing setup not finished",
        body:
          "Your billing account is still being set up, so bots cannot " +
          "join yet. Finishing setup takes a minute.",
        action: FINISH_SETUP,
        retryable: false,
      };
    case "payment_past_due":
      return {
        reason,
        kind: "paywall",
        title: "Payment past due",
        body:
          "Your last payment did not go through, so bots are paused. " +
          "Updating the payment method resumes them.",
        action: UPDATE_PAYMENT,
        retryable: false,
      };
    case "spend_cap_reached":
      return {
        reason,
        kind: "limit",
        title: "Monthly spend cap reached",
        body:
          "You set a hard spend cap and this month has reached it. " +
          "Raising or clearing the cap resumes bots.",
        action: REVIEW_SPEND_CAP,
        retryable: false,
      };
    case "concurrency_limit_reached":
      return {
        reason,
        kind: "limit",
        title: "All bots are busy",
        body: `${concurrencySentence(facts)} One will free up when a meeting ends.`,
        action: null,
        retryable: true,
      };
    case "billing_unavailable":
      return {
        reason,
        kind: "retryable",
        title: "Billing system temporarily unavailable",
        body:
          "Your account is fine — we could not reach the billing ledger " +
          "just now. Try again shortly.",
        action: null,
        retryable: true,
      };
    default:
      return unknownServiceDenial(reason);
  }
}

function unknownServiceDenial(reason: string): ServiceDenialPresentation {
  const label = reason.trim() || "unspecified";
  // Fail loud in the logs, PRODUCTION INCLUDED — never in the customer's face.
  // An unmapped reason is a prod-only event by definition: it means the service
  // authority (Vexa-ai/vexa-platform lib/billing-spend-policy.ts) shipped a
  // reason ahead of this module's copy. A dev-only console.error is silent
  // exactly where the drift actually happens, so the net has no signal.
  console.error(
    `[service-denial] unmapped service-authority reason: ${label}. ` +
      "Add it to serviceDenialPresentation before it reaches a customer.",
  );
  return {
    reason,
    kind: "unknown",
    title: "Service not allowed",
    body:
      `service not allowed: ${label}. If this keeps happening, send us ` +
      "this code and we will tell you exactly what it means.",
    action: null,
    retryable: true,
  };
}

/**
 * Compile-time exhaustiveness: adding a reason without a branch above turns
 * this into a type error, so the vocabulary cannot drift ahead of the copy.
 */
const MAPPED_REASONS: Record<ServiceAuthorityReason, true> = {
  allowed: true,
  billing_setup_required: true,
  insufficient_balance: true,
  payment_past_due: true,
  spend_cap_reached: true,
  concurrency_limit_reached: true,
  billing_unavailable: true,
};

export const SERVICE_DENIAL_REASONS = Object.keys(
  MAPPED_REASONS,
) as ServiceAuthorityReason[];

/**
 * Reads a denial out of an API error body.
 *
 * Two shapes are accepted because both are on the wire: FastAPI nests the
 * payload under `detail` (`{"detail":{"code":…,"reason":…}}`, which is what
 * `POST /bots` emits), and the platform routes emit the object bare. Returns
 * null when the body is not a denial, so callers keep their own handling for
 * genuine auth or transport faults.
 */
export function serviceDenialFromResponseBody(
  body: unknown,
  facts: ServiceDenialFacts = {},
): ServiceDenialPresentation | null {
  const payload = unwrapDetail(body);
  if (typeof payload !== "object" || payload === null) return null;
  const record = payload as Record<string, unknown>;
  const code = typeof record.code === "string" ? record.code : null;
  const reason = typeof record.reason === "string" ? record.reason : null;
  if (code === SERVICE_NOT_ALLOWED_CODE) {
    return serviceDenialPresentation(reason ?? "", facts);
  }
  if (code === SERVICE_AUTHORITY_UNAVAILABLE_CODE) {
    // 503 from the same gate: our side, account untouched, retrying helps.
    return {
      reason: reason ?? SERVICE_AUTHORITY_UNAVAILABLE_CODE,
      kind: "retryable",
      title: "Billing system temporarily unavailable",
      body:
        "Your account is fine — we could not reach the billing ledger " +
        "just now. Try again shortly.",
      action: null,
      retryable: true,
    };
  }
  return null;
}

function unwrapDetail(body: unknown): unknown {
  if (typeof body !== "object" || body === null) return body;
  const record = body as Record<string, unknown>;
  if ("code" in record) return record;
  if ("detail" in record) return record.detail;
  return record;
}

/**
 * Reads a denial off a thrown error. Only 4xx/5xx bodies carrying the denial
 * code qualify — a 401, or a 403 that is a genuine permission fault, returns
 * null and keeps its access-error rendering.
 */
export function serviceDenialFromError(
  error: unknown,
  facts: ServiceDenialFacts = {},
): ServiceDenialPresentation | null {
  if (!(error instanceof VexaAPIError)) return null;
  if (error.status === 401) return null;
  return serviceDenialFromResponseBody(error.details, facts);
}
