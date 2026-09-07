/**
 * Typed join-failure evidence, produced AT THE SOURCE (#1059, #1058).
 *
 * The bot is the only party that ever sees the platform. It holds the DOM verdict, the message the
 * join layer threw, and the clock that tells a twenty-second refusal apart from a thirteen-minute
 * wait nobody answered. By the time the control plane reads the row, all of it is gone — which is how
 * 83 consecutive production join failures came to carry `reason: None`, and how a Teams refusal and
 * a Meet lobby expiry ended up sharing one label.
 *
 * So the classification happens HERE, on the evidence, and rides the terminal lifecycle.v1 event as
 * `join_evidence`. The control plane mirrors this taxonomy (`meeting_api/lifecycle/join_evidence.py`)
 * and re-derives a verdict when a producer sends none — the two implementations must stay in step,
 * and the shared vocabulary below is the contract between them.
 *
 * TWO AXES, deliberately independent:
 *   - `JoinFailureReason`      — WHAT happened.
 *   - `JoinFailureAttribution` — WHO it belongs to. This is what makes a reliability metric
 *     possible: a user who stopped their own bot and a host who never clicked admit are not
 *     software failures, and a rate that counts them is not a reliability rate.
 *
 * FAIL-OPEN, absolutely. This module describes a run that has ALREADY ended. Every export is total —
 * it returns a verdict or `undefined` and never throws — and the orchestrator additionally guards
 * every call site. Evidence that cannot be produced must degrade to a less-informative terminal
 * event, NEVER to a changed join outcome.
 *
 * Rides lifecycle.v1 additively (the contract ingests liberally, `additionalProperties: true`) — the
 * same no-seal-bump path `infra_fault` and `stt_fault` already take. The sealed `CompletionReason`
 * enum is untouched: it drives retry policy, and this is a diagnostic axis, not a retry input.
 *
 * Run its tests: tsx src/join-evidence.test.ts
 */
import type { FailureStage } from './contracts.js';
import type { JoinOutcome, JoinSignals } from './ports.js';

/**
 * WHAT happened — see the Python mirror for the per-value ownership rationale.
 *
 * ONE WORD PER FACT: where this axis names a fact the sealed lifecycle.v1 / api.v1 enums already
 * name, it reuses THEIR word, so the two vocabularies layer instead of needing a translation table.
 * Where the fact differs, so does the word — `stopped_while_joining` is deliberately not api.v1's
 * `stopped_before_admission`, which is a different field with a near-but-not-identical meaning.
 */
export type JoinFailureReason =
  /** The platform or its host actively DENIED us (moderator deny, automated-join block, captcha). */
  | 'awaiting_admission_rejected'
  /** We reached the waiting room and nobody ever answered; the lobby budget ran out. */
  | 'awaiting_admission_timeout'
  /** Authenticated mode found a signed-out browser profile — the session is dead. */
  | 'auth_session_missing'
  /** The page loaded but no admission signal ever appeared (selector rot / platform UI change). */
  | 'never_reached_lobby'
  /** We never got as far as the meeting page (navigation error, off-origin redirect, closed target). */
  | 'navigation_failure'
  /** The USER stopped the run while the bot was still knocking. Not a failure. */
  | 'stopped_while_joining'
  /** The signals do not support a verdict. `detail` carries what was observed. */
  | 'unknown';

/** WHO it belongs to — the axis the reliability gate reads (`system_failure_rate`). */
export type JoinFailureAttribution =
  /** We failed to do our job: the join flow, navigation, the session, a crash. */
  | 'system_fault'
  /** The user ended it themselves. Excluded from the gate metric's denominator entirely. */
  | 'user_action'
  /** The meeting's host decided — denied us, or never answered. A funnel outcome, not a defect. */
  | 'host_action'
  /** The platform itself refused or changed under us, positively identified. Exogenous. */
  | 'exogenous_platform'
  /** No owner is supportable from the evidence. Never quietly folded into any of the above. */
  | 'unknown';

/** The `join_evidence` block as it rides the terminal lifecycle.v1 event. */
export interface JoinEvidence {
  reason: JoinFailureReason;
  attribution: JoinFailureAttribution;
  /** Always `'bot'` from here — the control plane uses it to weight first-hand over inferred. */
  source: 'bot';
  stage?: FailureStage;
  /** The platform's own signal, trimmed + capped: the string that TRIGGERED this classification. */
  detail?: string;
  timings?: { time_to_lobby_ms?: number; time_in_lobby_ms?: number; total_ms?: number };
  lobby_budget_ms?: number;
}

/** Cap the free-text platform signal so a stack trace can never bloat `meeting.data`. Mirrors the
 *  Python `DETAIL_MAX_CHARS`; over-long details are TRUNCATED, never dropped. */
export const DETAIL_MAX_CHARS = 500;

/** How much of the issued lobby budget must be burned for a lobby exit to read as EXPIRY rather
 *  than a refusal. Mirrors the Python `LOBBY_EXPIRY_FRACTION` — see there for why it is not 1.0. */
export const LOBBY_EXPIRY_FRACTION = 0.9;

/** Transport/browser-level markers that positively identify a NAVIGATION failure. Mechanical
 *  strings, not interpretations of meeting state — matching one is reading evidence, not guessing. */
const NAVIGATION_MARKERS = [
  'net::err',
  'err_name_not_resolved',
  'err_connection',
  'target closed',
  'target page, context or browser has been closed',
  'page closed',
  'navigating to',
  'navigation failed',
  'teams_auth_redirect',
  'teams_off_meeting_origin',
  'cannot infer platform',
  'unsupported platform',
  // Our own control plane being unreachable at boot: the bot deliberately never navigated to the
  // meeting. It never got as far as the page, which is what this reason means.
  'control_plane_unreachable',
];

/** Markers that identify a PLATFORM-POLICY refusal (not a human's decision) → exogenous. */
const PLATFORM_POLICY_MARKERS = [
  'zoom_requires_rtms',
  'blocks automated browser joins',
  'recaptcha',
  'captcha',
  'bot detection',
  'bot-detection',
  'unusual traffic',
];

/** Markers that identify OUR admission flow as the thing that broke — the join layer could not read
 *  the platform's state at all. A wait that ended this way is not a host declining to admit. */
const ADMISSION_FLOW_FAULT_MARKERS = [
  'no meeting indicators found',
  'could not determine',
  'selector',
  'waiting for selector',
  'no admission signal',
];

/** Default owner per typed reason; moved only by positive evidence in `detail`. */
const ATTRIBUTION_BY_REASON: Record<JoinFailureReason, JoinFailureAttribution> = {
  awaiting_admission_rejected: 'host_action',
  awaiting_admission_timeout: 'host_action',
  auth_session_missing: 'system_fault',
  never_reached_lobby: 'system_fault',
  navigation_failure: 'system_fault',
  stopped_while_joining: 'user_action',
  unknown: 'unknown',
};

/** Trim + cap the producer's message. Blank/absent → `undefined`. */
export function cleanDetail(detail: unknown): string | undefined {
  if (typeof detail !== 'string') return undefined;
  const text = detail.trim();
  if (!text) return undefined;
  return text.length <= DETAIL_MAX_CHARS ? text : `${text.slice(0, DETAIL_MAX_CHARS - 1)}…`;
}

/** A non-negative integer millisecond count, or `undefined`. Never NaN, never negative. */
function millis(value: unknown): number | undefined {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return undefined;
  return Math.round(value);
}

function matches(text: string | undefined, markers: readonly string[]): boolean {
  if (!text) return false;
  const lowered = text.toLowerCase();
  return markers.some((m) => lowered.includes(m));
}

/**
 * Classify a non-admitted join from the driver's verdict + the signals it gathered.
 *
 * Order is evidence strength, strongest first:
 *  1. The driver's own typed outcome, where it is already precise (`auth_missing`, `rejected`).
 *  2. A transport marker in the platform's message — checked before the stage buckets, because a
 *     browser that never loaded the page can die at any stage.
 *  3. Reached the lobby: the only exits without admission are a denial (case 1) or the budget
 *     expiring. Burned ≥ `LOBBY_EXPIRY_FRACTION` of the issued budget ⇒ expiry; out well inside it
 *     with no denial ⇒ the platform ended our wait — the #1058 split, read off the timing signature.
 *  4. Never reached the lobby but was on the page ⇒ no admission signal ever appeared.
 *  5. Otherwise `unknown`, with the detail preserved.
 */
export function classifyJoinFailure(
  outcome: Exclude<JoinOutcome, 'admitted'> | 'stopped',
  signals: JoinSignals = {},
): JoinFailureReason {
  try {
    if (outcome === 'stopped') return 'stopped_while_joining';
    if (outcome === 'auth_missing') return 'auth_session_missing';

    const detail = cleanDetail(signals.detail);
    if (matches(detail, NAVIGATION_MARKERS)) return 'navigation_failure';

    // A typed denial (or a bot-detection block, which is the platform refusing us just as flatly)
    // is a verdict, not an inference — it outranks any timing.
    if (outcome === 'rejected' || outcome === 'blocked') return 'awaiting_admission_rejected';

    if (signals.reachedLobby) {
      const inLobby = millis(signals.timeInLobbyMs);
      const budget = millis(signals.lobbyBudgetMs);
      if (inLobby !== undefined && budget) {
        return inLobby >= budget * LOBBY_EXPIRY_FRACTION ? 'awaiting_admission_timeout' : 'awaiting_admission_rejected';
      }
      return 'awaiting_admission_timeout';
    }

    if (outcome === 'timeout') return 'awaiting_admission_timeout';
    if (signals.reachedLobby === false) return 'never_reached_lobby';
    return 'unknown';
  } catch {
    return 'unknown';
  }
}

/**
 * WHO the failure belongs to. Starts from the typed reason's default owner and moves it only on
 * positive evidence: a platform-policy refusal is exogenous rather than the host's doing, and an
 * admission wait that ended because our join layer went blind is ours rather than the host's.
 */
export function attributeJoinFailure(
  reason: JoinFailureReason,
  detail?: string,
): JoinFailureAttribution {
  try {
    const base = ATTRIBUTION_BY_REASON[reason] ?? 'unknown';
    const text = cleanDetail(detail);
    if (!text) return base;
    if (reason === 'awaiting_admission_rejected' && matches(text, PLATFORM_POLICY_MARKERS)) {
      return 'exogenous_platform';
    }
    if (reason === 'awaiting_admission_timeout' && matches(text, ADMISSION_FLOW_FAULT_MARKERS)) {
      return 'system_fault';
    }
    return base;
  } catch {
    return 'unknown';
  }
}

/**
 * Build the `join_evidence` block for a terminal event. Only known keys are emitted — an absent
 * timing is ABSENT, never a zero: "zero ms in the lobby" and "nobody measured" are different facts.
 *
 * Total by contract: any fault yields `undefined`, and the caller emits its terminal regardless.
 */
export function buildJoinEvidence(
  outcome: Exclude<JoinOutcome, 'admitted'> | 'stopped',
  stage: FailureStage | undefined,
  signals: JoinSignals = {},
): JoinEvidence | undefined {
  try {
    const detail = cleanDetail(signals.detail);
    const reason = classifyJoinFailure(outcome, signals);
    const evidence: JoinEvidence = {
      reason,
      attribution: attributeJoinFailure(reason, detail),
      source: 'bot',
    };
    if (stage) evidence.stage = stage;
    if (detail) evidence.detail = detail;
    const timings: NonNullable<JoinEvidence['timings']> = {};
    const toLobby = millis(signals.timeToLobbyMs);
    const inLobby = millis(signals.timeInLobbyMs);
    const total = millis(signals.totalMs);
    if (toLobby !== undefined) timings.time_to_lobby_ms = toLobby;
    if (inLobby !== undefined) timings.time_in_lobby_ms = inLobby;
    if (total !== undefined) timings.total_ms = total;
    if (Object.keys(timings).length > 0) evidence.timings = timings;
    const budget = millis(signals.lobbyBudgetMs);
    if (budget !== undefined) evidence.lobby_budget_ms = budget;
    return evidence;
  } catch {
    return undefined;
  }
}
