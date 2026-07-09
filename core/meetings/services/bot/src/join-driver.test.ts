/**
 * join-driver seam (G1) — a TYPED `AdmissionError` outcome must map to the right `JoinOutcome`, so a
 * host DENIAL is recorded as a PERMANENT `rejected` (→ `awaiting_admission_rejected`), not collapsed
 * into a TRANSIENT, RETRIED `join_failure`. Regression for the wasted-respawn-on-denial bug: before the
 * fix the admission wait's throw fell through to the orchestrator's blanket `join_failure` catch, and
 * `lifecycle/retry.py` re-spawned a bot that was actually denied.
 *
 * Run: tsx src/join-driver.test.ts
 */
import { AdmissionError } from '@vexa/join';
import { admissionOutcomeToJoinOutcome } from './join-driver.js';
import type { JoinOutcome } from './ports.js';

let passed = 0, failed = 0;
const check = (name: string, cond: boolean) => {
  if (cond) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}`); failed++; }
};

// JoinOutcomes the orchestrator (OUTCOME_FAIL) + retry.py treat as PERMANENT (no retry):
// 'rejected' → awaiting_admission_rejected. Transient (retried): 'timeout' → awaiting_admission_timeout,
// 'error'/'blocked' → join_failure.
const PERMANENT_OUTCOMES = new Set<JoinOutcome>(['rejected']);

console.log('\n=== join-driver: AdmissionError outcome → JoinOutcome (G1) ===');

check('denial → rejected (permanent, not retried)', admissionOutcomeToJoinOutcome('denial') === 'rejected');
check('lobby_timeout → timeout (transient retry)', admissionOutcomeToJoinOutcome('lobby_timeout') === 'timeout');
check('join_failure → error', admissionOutcomeToJoinOutcome('join_failure') === 'error');

// The bug, end to end at the boundary: a real AdmissionError('denial') must NOT surface transient.
const denial = new AdmissionError('denial', 'Bot admission was rejected by meeting admin');
const mapped = admissionOutcomeToJoinOutcome(denial.outcome);
check('AdmissionError("denial").outcome maps to rejected', mapped === 'rejected');
check('a denial is PERMANENT (not a retried join_failure)', PERMANENT_OUTCOMES.has(mapped));
check('a denial does NOT map to the transient/retried classes', mapped !== 'error' && mapped !== 'timeout');

console.log(`\n${passed} passed, ${failed} failed`);
if (failed > 0) process.exit(1);
