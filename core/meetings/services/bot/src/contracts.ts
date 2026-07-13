/**
 * TS mirrors of the meetings published schemas the bot PRODUCES / CONSUMES, plus the
 * lifecycle.v1 state machine the orchestrator obeys.
 *
 * These are language-neutral schemas at a service boundary (lifecycle.v1 → meeting-api,
 * transcript.v1 → collector, acts.v1 ← control plane). The wire format is JSON Schema
 * (`meetings/contracts/*.v1/`, validated by `gate:schema`); this file is the compile-time
 * shadow for the TS producer — kept structurally in lock-step with those `.schema.json`
 * files. The `canTransition` machine is the README's legal-transition table made
 * executable (lifecycle.v1/README.md) — the impl enforces what the contract documents.
 */

// ── lifecycle.v1 ──────────────────────────────────────────────────────────────────────

export type BotStatus =
  | 'joining'
  | 'awaiting_admission'
  | 'active'
  | 'needs_help'
  | 'completed'
  | 'failed';

export type CompletionReason =
  | 'stopped'
  | 'left_alone'
  | 'startup_alone'
  | 'evicted'
  | 'awaiting_admission_timeout'
  | 'awaiting_admission_rejected'
  | 'join_failure'
  | 'validation_error'
  | 'max_bot_time_exceeded';

export type FailureStage = 'requested' | 'joining' | 'awaiting_admission' | 'active';

/** One lifecycle.v1 status report. `connection_id` + `status` always; the rest are
 *  state-dependent terminal forensics. Mirrors `#/$defs/LifecycleEvent`. */
export interface LifecycleEvent {
  connection_id: string;
  container_id?: string;
  status: BotStatus;
  reason?: string;
  exit_code?: number;
  completion_reason?: CompletionReason;
  failure_stage?: FailureStage;
  bot_logs?: string[];
  bot_resources?: { peak_memory_bytes?: number; cpu_usage_usec?: number; [k: string]: unknown };
  speaker_events?: unknown[];
}

/** The legal transitions — the machine the bot MUST obey (lifecycle.v1/README.md). */
export const LIFECYCLE_TRANSITIONS: Record<BotStatus, readonly BotStatus[]> = {
  joining: ['awaiting_admission', 'active', 'failed'],
  awaiting_admission: ['active', 'needs_help', 'failed'],
  needs_help: ['active', 'failed'],
  active: ['completed', 'failed'],
  completed: [],
  failed: [],
};

export const TERMINAL_STATUSES: readonly BotStatus[] = ['completed', 'failed'];

export function isTerminal(s: BotStatus): boolean {
  return TERMINAL_STATUSES.includes(s);
}

/** True iff `to` is a legal next state from `from`. */
export function canTransition(from: BotStatus, to: BotStatus): boolean {
  return LIFECYCLE_TRANSITIONS[from].includes(to);
}

// ── acts.v1 ───────────────────────────────────────────────────────────────────────────

/** A control-plane → bot command. Discriminated by `action` (acts.v1 `#/$defs/Act`). */
export type Act =
  | { action: 'leave' }
  | { action: 'reconfigure'; language?: string | null; task?: string | null; allowedLanguages?: string[] }
  | { action: 'speak'; text: string; voice?: string }
  | { action: 'speak_audio'; url?: string; audioBase64?: string }
  | { action: 'speak_stop' }
  | { action: 'chat_send'; text: string }
  | { action: 'chat_read' }
  | { action: 'screen_show'; imageUrl?: string; text?: string }
  | { action: 'screen_stop' }
  | { action: 'avatar_set'; url: string }
  | { action: 'avatar_reset' };

export type ActAction = Act['action'];

export const ACT_ACTIONS: readonly ActAction[] = [
  'leave', 'reconfigure',
  'speak', 'speak_audio', 'speak_stop',
  'chat_send', 'chat_read',
  'screen_show', 'screen_stop', 'avatar_set', 'avatar_reset',
];

/** The redis pub/sub channel carrying a meeting's command bus (acts.v1). */
export const actsChannel = (meetingId: string | number): string => `bot_commands:meeting:${meetingId}`;

/** Narrow a raw decoded message to an Act (null for anything off-contract; unknown actions ignored). */
export function parseAct(msg: unknown): Act | null {
  if (!msg || typeof msg !== 'object') return null;
  const action = (msg as { action?: unknown }).action;
  if (typeof action !== 'string' || !(ACT_ACTIONS as readonly string[]).includes(action)) return null;
  return msg as Act;
}

// ── transcript.v1 ─────────────────────────────────────────────────────────────────────

export type Source = 'glow-bound' | 'provisional-cluster-id' | 'caption' | 'merged' | 'chat';

/** One speaker-attributed utterance. Mirrors transcript.v1 `#/$defs/TranscriptSegment`. */
export interface TranscriptSegment {
  segment_id: string;
  speaker: string;
  speaker_key?: string;
  text: string;
  start: number;
  end: number;
  language?: string | null;
  completed: boolean;
  absolute_start_time?: string;
  absolute_end_time?: string;
  source?: Source;
  confidence?: number;
  words?: { word: string; start: number; end: number; probability?: number }[];
}
