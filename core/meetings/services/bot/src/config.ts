/**
 * invocation.v1 boot config (P14) — the bot's "constructor".
 *
 * The container is started with ONE JSON env var, `VEXA_BOT_CONFIG`, holding an
 * `invocation.v1` object. We validate it at boot against the PUBLISHED schema
 * (`meetings/contracts/invocation.v1/invocation.schema.json`, loaded by PATH — the
 * goldens are the spec, P8) with ajv — the same validator the contract's own
 * `validate.mjs` uses, so the bot can NEVER drift from the contract. A parse/validation
 * failure is fatal: the caller maps it to a lifecycle.v1 `failed` / `validation_error`
 * (fail-fast, P14). Secrets ride in this contract (token / internalSecret / S3 keys) —
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

export type Platform = 'google_meet' | 'zoom' | 'teams';
export type TranscriptionTier = 'realtime' | 'deferred';

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
  nativeMeetingId?: string;
  // ── identity / control plane ──
  token?: string;
  connectionId?: string;
  meeting_id?: number;
  container_name?: string;
  redisUrl: string;
  meetingApiCallbackUrl?: string;
  internalSecret?: string;
  // ── transcription ──
  language?: string | null;
  task?: string | null;
  allowedLanguages?: string[];
  transcribeEnabled?: boolean;
  transcriptionTier?: TranscriptionTier;
  transcriptionServiceUrl?: string;
  transcriptionServiceToken?: string;
  // ── recording ──
  recordingEnabled?: boolean;
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
  return data as Invocation;
}

/** Boot helper — read VEXA_BOT_CONFIG from the environment and validate it (P7: config by env). */
export function loadInvocation(env: NodeJS.ProcessEnv = process.env): Invocation {
  return parseInvocation(env.VEXA_BOT_CONFIG);
}
