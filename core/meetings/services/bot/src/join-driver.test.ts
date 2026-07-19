/**
 * join-driver seam (G1) — a TYPED `AdmissionError` outcome must map to the right `JoinOutcome`, so a
 * host DENIAL is recorded as a PERMANENT `rejected` (→ `awaiting_admission_rejected`), not collapsed
 * into a TRANSIENT, RETRIED `join_failure`. Regression for the wasted-respawn-on-denial bug: before the
 * fix the admission wait's throw fell through to the orchestrator's blanket `join_failure` catch, and
 * `lifecycle/retry.py` re-spawned a bot that was actually denied.
 *
 * Run: tsx src/join-driver.test.ts
 */
import { AdmissionError, AuthSessionError } from '@vexa/join';
import { admissionOutcomeToJoinOutcome, joinPlatform } from './join-driver.js';
import type { JoinOutcome } from './ports.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean) => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`); failed++; }
};

// JoinOutcomes the orchestrator (OUTCOME_FAIL) + retry.py treat as PERMANENT (no retry):
// 'rejected' → awaiting_admission_rejected. Transient (retried): 'timeout' → awaiting_admission_timeout,
// 'error'/'blocked' → join_failure.
const PERMANENT_OUTCOMES = new Set<JoinOutcome>(['rejected', 'auth_missing']);

console.log('\n=== join-driver: AdmissionError outcome → JoinOutcome (G1) ===');

check('denial → rejected (permanent, not retried)', admissionOutcomeToJoinOutcome('denial') === 'rejected');
check('lobby_timeout → timeout (transient retry)', admissionOutcomeToJoinOutcome('lobby_timeout') === 'timeout');
check('join_failure → error', admissionOutcomeToJoinOutcome('join_failure') === 'error');
check('telemost is routed to its own join adapter', joinPlatform('telemost') === 'telemost');
check('unknown platform still falls back to google_meet', joinPlatform('unknown') === 'google_meet');

// The bug, end to end at the boundary: a real AdmissionError('denial') must NOT surface transient.
const denial = new AdmissionError('denial', 'Bot admission was rejected by meeting admin');
const mapped = admissionOutcomeToJoinOutcome(denial.outcome);
check('AdmissionError("denial").outcome maps to rejected', mapped === 'rejected');
check('a denial is PERMANENT (not a retried join_failure)', PERMANENT_OUTCOMES.has(mapped));
check('a denial does NOT map to the transient/retried classes', mapped !== 'error' && mapped !== 'timeout');

// A signed-out profile in authenticated mode: AuthSessionError IS an AdmissionError (the driver's
// single `instanceof` catch maps it — no re-raise → no blanket transient join_failure), and its
// typed outcome maps to the PERMANENT `auth_missing` (→ auth_session_missing, never re-spawned).
check('auth_session_missing → auth_missing', admissionOutcomeToJoinOutcome('auth_session_missing') === 'auth_missing');
const authErr = new AuthSessionError('Browser profile signed out — cannot authenticate with Google.');
check('AuthSessionError instanceof AdmissionError (driver catches, does not re-raise)', authErr instanceof AdmissionError);
const authMapped = admissionOutcomeToJoinOutcome(authErr.outcome);
check('AuthSessionError.outcome maps to auth_missing', authMapped === 'auth_missing');
check('a missing auth session is PERMANENT (not a retried join_failure)', PERMANENT_OUTCOMES.has(authMapped));
check('auth failure does NOT map to the transient/retried classes', authMapped !== 'error' && authMapped !== 'timeout');

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
