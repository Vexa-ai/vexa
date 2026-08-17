import { VexaAPIError } from "@/lib/api";
import { getUserFriendlyError } from "@/lib/error-messages";
import {
  serviceDenialFromError,
  type ServiceDenialFacts,
  type ServiceDenialPresentation,
} from "@/lib/service-denial";
import { shouldTriggerZoomOAuth } from "@/lib/zoom-oauth-client";

/**
 * The rendering states a failed Join can land in.
 *
 * Kept as a pure function so the join surfaces (modal, pending-meeting hook)
 * agree on which state a given error produces, and so the mapping is testable
 * without a DOM.
 *
 * - `denial`     — the service authority refused. Rendered as an in-modal panel
 *                  with its own words and, where one exists, one fixing action.
 *                  Never a toast: a paywall the customer must act on should not
 *                  disappear on a timer.
 * - `zoom-oauth` — the Zoom connection is missing; the caller runs the OAuth
 *                  hand-off instead of showing anything.
 * - `toast`      — everything else, including genuine authz failures.
 */
export type JoinErrorState =
  | { kind: "denial"; presentation: ServiceDenialPresentation }
  | { kind: "zoom-oauth" }
  | { kind: "toast"; title: string; description: string };

export interface ResolveJoinErrorOptions {
  /** Platform of the request that failed, for the Zoom OAuth branch. */
  platform?: string;
  /** True when the Zoom OAuth hand-off can actually run (user email known). */
  canStartZoomOAuth?: boolean;
  /** Extra numbers to put in the denial copy, when the caller knows them. */
  facts?: ServiceDenialFacts;
}

export function resolveJoinError(
  error: unknown,
  options: ResolveJoinErrorOptions = {},
): JoinErrorState {
  const { platform, canStartZoomOAuth = false, facts } = options;

  // A subscription gate (402) keeps its existing, already-specific handling.
  // Everything the service authority refuses arrives as 403/503 with a code.
  const denial = serviceDenialFromError(error, facts);
  if (denial) return { kind: "denial", presentation: denial };

  if (
    canStartZoomOAuth &&
    platform === "zoom" &&
    shouldTriggerZoomOAuth(error, platform)
  ) {
    return { kind: "zoom-oauth" };
  }

  const { title, description } = getUserFriendlyError(
    error instanceof Error ? error : new Error(String(error)),
  );
  return { kind: "toast", title, description };
}

/**
 * Absolute URL for a denial action. Account and billing live on the webapp
 * origin (vexa.ai), not on the dashboard, so the relative href the mapping
 * carries is resolved against the webapp base.
 */
export function denialActionUrl(webappUrl: string, href: string): string {
  return `${webappUrl.replace(/\/+$/, "")}${href}`;
}

/** True when the error is a genuine authorization failure, not a service denial. */
export function isAccessError(error: unknown): boolean {
  if (!(error instanceof VexaAPIError)) return false;
  if (error.status === 401) return true;
  if (error.status !== 403) return false;
  return serviceDenialFromError(error) === null;
}
