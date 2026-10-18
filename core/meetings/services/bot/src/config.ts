/**
 * invocation.v1 boot config (P14) — the bot's "constructor".
 *
 * The container is started with ONE JSON env var, `VEXA_BOT_CONFIG`, holding an
 * `invocation.v1` object. We validate it at boot against the PUBLISHED schema
 * (`meetings/contracts/invocation.v1/invocation.schema.json`, loaded by PATH — the
 * goldens are the spec, P8) with ajv — the same validator the contract's own
 * `validate.mjs` uses, so the bot can NEVER drift from the contract. A parse/validation
 * failure is fatal: the caller maps it to a lifecycle.v1 `failed` / `validation_error`
 * (fail-fast, P14). Secrets ride in this contract (token / S3 keys) —
 * never logged (P14/P15).
 *
 * `Invocation` is the typed view the rest of the bot depends on. It is a hand-written
 * mirror of the schema's `#/$defs/Invocation` (no zod — zero new runtime deps; ajv is the
 * single source of truth at runtime, this interface is the compile-time shadow).
 */
import { Ajv2020 } from 'ajv/dist/2020.js';
import type { Ajv, ValidateFunction } from 'ajv';
import addFormatsDefault from 'ajv-formats';
import { readFileSync } from 'node:fs';

// verbatimModuleSyntax (tsconfig.base): the CJS default export of ajv-formats isn't
// synthesized as callable, so bind its call signature explicitly. Runtime is unchanged.
const addFormats = addFormatsDefault as unknown as (ajv: Ajv) => Ajv;
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import type { SpeakerStreamManagerConfig } from '@vexa/gmeet-pipeline';
import { settingProblem } from './transcription-language.js';

export type Platform = 'google_meet' | 'zoom' | 'teams' | 'jitsi' | 'voice_note' | 'vexa_call';
export type TranscriptionTier = 'realtime' | 'deferred';

/** True for platforms that ride the MIXED capture lane (one combined WebRTC audio
 *  stream + pyannote separation); google_meet rides the per-channel gmeet lane.
 *  The ONE predicate the browser hook, the capture bridge, and the pipeline pick
 *  must all agree on — never restate it inline. */
export function isMixedLanePlatform(p: Platform | string): boolean {
  return p === 'zoom' || p === 'teams' || p === 'jitsi';
}

/** True for mixed-lane platforms whose per-participant WebRTC streams are confirmed stable
 *  (one track per person, no remapping) — captured PER-TRACK and transcribed through the
 *  per-channel (gmeet) engine instead of the pyannote mix. Zoom is verified live (10 stable
 *  streams, 0 teardowns). Teams/Jitsi stay on the mix until witnessed the same way — a Teams
 *  active-speaker SLOT model would defeat per-track, so it is NOT assumed. Both the capture
 *  bridge (which capture) and the pipeline pick (which engine) read THIS one predicate. */
export function isPerTrackLanePlatform(p: Platform | string): boolean {
  return p === 'zoom';
}

/** invocation.v1 `VoiceNote`: the note's stream and its limits (present iff platform is voice_note). */
export interface VoiceNote {
  audioStream: string;
  maxDurationSec: number;
  idleTimeoutSec?: number;
  speakerName?: string;
}

/** invocation.v1 `Call`: the LiveKit room the listener joins hidden (present iff platform is vexa_call). */
export interface Call {
  /** The LiveKit server address inside the deployment (ws:// or wss://). */
  url: string;
  room: string;
  /** The listener's own token: hidden, subscribe-only, this room only. */
  token: string;
  maxDurationSec: number;
  /** How long an emptied room is given before the call ends. */
  emptyGraceSec?: number;
  /** How long the listener waits for the first participant. */
  firstJoinTimeoutSec?: number;
  /** Transcribe the audio of a screen a participant shares (policy; absent ⇒ on). */
  screenShareAudio?: boolean;
}

export interface AutomaticLeave {
  waitingRoomTimeout?: number;
  noOneJoinedTimeout?: number;
  everyoneLeftTimeout?: number;
}

/** The compile-time mirror of invocation.v1 `#/$defs/Invocation` (ajv is the runtime truth). */
export interface Invocation {
  // ── what to join (required: platform, meetingUrl, botName, redisUrl) ──
  platform: Platform;
  meetingUrl: string | null;
  botName: string;
  passcode?: string;
  nativeMeetingId?: string;
  voiceNote?: VoiceNote;
  call?: Call;
  // ── identity / control plane ──
  token?: string;
  connectionId?: string;
  meeting_id?: number;
  container_name?: string;
  redisUrl: string;
  meetingApiCallbackUrl?: string;
  // ── transcription ──
  language?: string | null;
  task?: string | null;
  allowedLanguages?: string[];
  transcribeEnabled?: boolean;
  transcriptionTier?: TranscriptionTier;
  transcriptionServiceUrl?: string;
  transcriptionServiceToken?: string;
  transcriptionModel?: string | null;
  transcriptionServiceOwner?: 'vexa' | 'customer';
  // ── recording ──
  recordingEnabled?: boolean;
  captureSignalEnabled?: boolean;
  captureModes?: string[];
  recordingUploadUrl?: string;
  // ── lifecycle timeouts ──
  automaticLeave?: AutomaticLeave;
  reconnectionIntervalMs?: number;
  // ── voice agent (gates acts.v1 voice commands; DEFERRED in this increment) ──
  voiceAgentEnabled?: boolean;
  defaultAvatarUrl?: string;
  videoReceiveEnabled?: boolean;
  cameraEnabled?: boolean;
  // ── authenticated meeting bot (persistent browser context from S3) ──
  authenticated?: boolean;
  userdataS3Path?: string;
  s3Endpoint?: string;
  s3Bucket?: string;
  s3AccessKey?: string;
  s3SecretKey?: string;
  /** where the authenticated bot PUTs its session-profile.v1 write-back (sent only in authenticated mode) */
  sessionWritebackUrl?: string;
  /** where an authenticated bot GETs a saved bot account's session-profile.v1 profile before launch (sent only in authenticated mode) */
  sessionRestoreUrl?: string;
}

/** Thrown when VEXA_BOT_CONFIG is missing / not JSON / off-contract. The composition root
 *  maps this to lifecycle.v1 failed(validation_error, failure_stage=requested). */
export class InvocationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'InvocationError';
  }
}

const HERE = dirname(fileURLToPath(import.meta.url));
// src/ → ../../../contracts/invocation.v1/  (meetings/services/bot/src → meetings/contracts/…)
const SCHEMA_PATH = join(HERE, '..', '..', '..', 'contracts', 'invocation.v1', 'invocation.schema.json');

interface Validator { ajv: Ajv; validate: ValidateFunction }
let _validator: Validator | undefined;
function validator(): Validator {
  if (_validator) return _validator;
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'));
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  addFormats(ajv);
  ajv.addSchema(schema);
  const validate = ajv.compile({ $ref: `${schema.$id}#/$defs/Invocation` });
  _validator = { ajv, validate };
  return _validator;
}

/** Parse + validate a raw JSON string against invocation.v1, or throw InvocationError. */
export function parseInvocation(raw: string | undefined): Invocation {
  if (!raw || !raw.trim()) throw new InvocationError('invocation.v1: VEXA_BOT_CONFIG env is missing or empty');
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch (e) {
    throw new InvocationError(`invocation.v1: VEXA_BOT_CONFIG is not valid JSON — ${(e as Error).message}`);
  }
  const { ajv, validate } = validator();
  if (!validate(data)) {
    throw new InvocationError(`invocation.v1: VEXA_BOT_CONFIG failed validation — ${ajv.errorsText(validate.errors)}`);
  }
  // The language pair is a transcription-language.v1 setting: codes, and a language that belongs
  // to a non-empty list. The schema types the fields; the pair's meaning is checked here.
  const languageProblem = settingProblem(data as Invocation);
  if (languageProblem) {
    throw new InvocationError(`invocation.v1: VEXA_BOT_CONFIG transcription language — ${languageProblem}`);
  }
  return data as Invocation;
}

/** Boot helper — read VEXA_BOT_CONFIG from the environment and validate it (P7: config by env). */
export function loadInvocation(env: NodeJS.ProcessEnv = process.env): Invocation {
  return parseInvocation(env.VEXA_BOT_CONFIG);
}

const SPEAKER_STREAM_ENV: Array<[Exclude<keyof SpeakerStreamManagerConfig, 'callbackStampedFrames' | 'maxHeldAudioSec'>, string]> = [
  ['minAudioDuration', 'BOT_SPEAKER_MIN_AUDIO_SEC'],
  ['submitInterval', 'BOT_SPEAKER_SUBMIT_INTERVAL_SEC'],
  ['confirmThreshold', 'BOT_SPEAKER_CONFIRM_THRESHOLD'],
  ['maxBufferDuration', 'BOT_SPEAKER_MAX_BUFFER_SEC'],
  ['idleTimeoutSec', 'BOT_SPEAKER_IDLE_TIMEOUT_SEC'],
  ['minFinalAudioSec', 'BOT_SPEAKER_MIN_FINAL_AUDIO_SEC'],
];

/** Per-request STT timeout (ms) from `BOT_STT_TIMEOUT_SEC`, or undefined for the client default
 *  (90 s). A value under 20 s is refused loudly: a remote realtime STT answers a few seconds of
 *  audio in 5–30 s, and a shorter timeout aborts requests that would have answered. */
export function sttTimeoutMsFromEnv(
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string) => void = (message) => console.warn(`[bot] ${message}`),
): number | undefined {
  const raw = env.BOT_STT_TIMEOUT_SEC;
  if (raw === undefined || raw.trim() === '') return undefined;
  const value = Number(raw);
  if (!Number.isFinite(value) || value < 20) {
    warn(`BOT_STT_TIMEOUT_SEC=${JSON.stringify(raw)} is invalid (must be a number of seconds >= 20); using the built-in 90 s`);
    return undefined;
  }
  return Math.round(value * 1000);
}

/** Show fast, then reconfirm: every window is shown at once as a draft, and each speaker's drafts are
 *  replaced by ONE segment transcribed again with context when the speaker pauses or this many
 *  seconds accumulate. BOT_TRANSCRIPT_RECONFIRM_SEC (forwarded by the runtime); unset → 10; 0 → off. */
export const DEFAULT_RECONFIRM_SEC = 10;
export function reconfirmSecFromEnv(env: NodeJS.ProcessEnv = process.env): number {
  const raw = (env.BOT_TRANSCRIPT_RECONFIRM_SEC ?? '').trim();
  if (raw === '') return DEFAULT_RECONFIRM_SEC;
  const v = Number(raw);
  return Number.isFinite(v) && v >= 0 ? Math.min(v, 30) : DEFAULT_RECONFIRM_SEC;
}

/** The lane tuning. ONE pipeline by default — show fast, then reconfirm: a pause of 500–700 ms ends
 *  an utterance and sends it once, one answer confirms it as a DRAFT shown at once, windows stay
 *  under 10 s, and per-participant tracks pipeline a fresh draft about every second; the drafts are
 *  then replaced by one reconfirmed segment (pipeline.ts, BOT_TRANSCRIPT_RECONFIRM_SEC). Measured on
 *  the guarantee harness against 0.12 (3 speakers, real STT): visible p95 0.1 s vs 0.7 s, confirmed
 *  p95 3.5 s vs 3.2 s, coverage 0.946 vs 0.935. Without reconfirmation the same tuning publishes its
 *  first versions as final (coverage 0.914, short fragments), so it is used only with it — or when
 *  `BOT_TRANSCRIPTION_LATENCY=low` asks for it explicitly. `BOT_TRANSCRIPTION_LATENCY=standard` keeps
 *  the built-in confirm-on-agreement lane. A voice note's microphone streams continuously and
 *  pipelined drafts starve there, so it takes the tuning without them. Explicit BOT_SPEAKER_* knobs
 *  still win. */
export function withLatencyProfile(
  platform: string,
  config: SpeakerStreamManagerConfig | undefined,
  env: NodeJS.ProcessEnv = process.env,
): SpeakerStreamManagerConfig | undefined {
  const mode = (env.BOT_TRANSCRIPTION_LATENCY ?? '').trim().toLowerCase();
  const fast = mode === 'low' || (mode !== 'standard' && reconfirmSecFromEnv(env) > 0);
  if (!fast) return config;
  const base: SpeakerStreamManagerConfig = platform === 'voice_note'
    ? { pauseFinalMs: 700, idleTimeoutSec: 1, submitInterval: 1, maxBufferDuration: 10, confirmThreshold: 1 }
    : { pauseFinalMs: 500, idleTimeoutSec: 1, submitInterval: 1, maxBufferDuration: 10, confirmThreshold: 1,
        pipelineDrafts: true, minAudioDuration: 0.75 };
  return { ...base, ...(config ?? {}) };
}

/** Read optional Meet speaker-stream tuning knobs from the bot environment. */
export function speakerStreamConfigFromEnv(
  env: NodeJS.ProcessEnv = process.env,
  warn: (message: string) => void = (message) => console.warn(`[bot] ${message}`),
): SpeakerStreamManagerConfig | undefined {
  const config: SpeakerStreamManagerConfig = {};
  let configured = false;
  for (const [property, key] of SPEAKER_STREAM_ENV) {
    const raw = env[key];
    if (raw === undefined || raw.trim() === '') continue;
    const value = Number(raw);
    const valid = Number.isFinite(value) && value > 0 &&
      (property !== 'confirmThreshold' || Number.isInteger(value));
    if (!valid) {
      warn(`${key}=${JSON.stringify(raw)} is invalid; using the built-in speaker-stream default`);
      continue;
    }
    (config as Record<string, number>)[property] = value;   // every env-tunable knob is numeric
    configured = true;
  }
  return configured ? config : undefined;
}
