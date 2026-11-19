/**
 * Session restore over HTTP — a bot that joins as a saved bot account gets its profile from meeting-api.
 *
 * A bot account is a signed-in browser profile a person provisioned through a browser session
 * (browser-session.ts). Its files are not in the deployment's userdata store, so a meeting bot that
 * joins as one restores by `GET` from the URL meeting-api names in its invocation
 * (`sessionRestoreUrl`, invocation.v1), with the MeetingToken it already holds. The answer is a
 * session-profile.v1 `WritebackBody`; the bot checks it against the contract's shape (read by path,
 * like config.ts reads invocation.v1) and then writes it with @vexa/remote-browser
 * `writeSessionProfile`, which refuses the whole answer when one path is not a SESSION_PROFILE path
 * (traversal, an extension, a cache…) or a limit is crossed.
 *
 * Every failure is the typed `SessionSyncError('session-restore')` the S3 restore throws: the
 * composition root drives it to a clean terminal failed, and the bot never joins signed out. The
 * profile bytes are a credential — they are never logged.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Ajv2020 } from 'ajv/dist/2020.js';
import type { ValidateFunction } from 'ajv';
import { SessionSyncError, writeSessionProfile } from '@vexa/remote-browser';

export interface RestoreOptions {
  fetchImpl?: typeof fetch;
  /** One attempt, bounded (default 30 s). */
  timeoutMs?: number;
}

// src/ → ../../../contracts/session-profile.v1 (meetings/services/bot/src → meetings/contracts/…)
const SCHEMA_PATH = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..', 'contracts', 'session-profile.v1', 'session-profile.schema.json');
let _isBody: ValidateFunction | undefined;
function isWritebackBody(): ValidateFunction {
  if (_isBody) return _isBody;
  const schema = JSON.parse(readFileSync(SCHEMA_PATH, 'utf8'));
  const ajv = new Ajv2020({ strict: false, allErrors: true });
  ajv.addSchema(schema);
  _isBody = ajv.compile({ $ref: `${schema.$id}#/$defs/WritebackBody` });
  return _isBody;
}

const fail = (detail: string, cause?: unknown): SessionSyncError => new SessionSyncError('session-restore', detail, cause);

/**
 * GET the session profile at `url` (Bearer `token`) and write it into `dataDir`. Resolves with the
 * number of files written; rejects with SessionSyncError('session-restore') on a network error, a
 * non-2xx answer, a body that is not JSON or not a WritebackBody, or a body the profile rule refuses.
 */
export async function restoreSessionFromUrl(url: string, token: string | undefined, dataDir: string, opts: RestoreOptions = {}): Promise<number> {
  if (!token) throw fail('the restore needs a bearer token and the invocation carries none');
  let res: Response;
  try {
    res = await (opts.fetchImpl ?? fetch)(url, {
      method: 'GET',
      headers: { Authorization: `Bearer ${token}`, Accept: 'application/json' },
      signal: AbortSignal.timeout(opts.timeoutMs ?? 30_000),
    });
  } catch (e) {
    throw fail(`meeting-api could not be reached for the session profile (${(e as Error)?.name ?? 'error'})`, e);
  }
  const text = await res.text().catch(() => '');
  // A refusal's detail is meeting-api's own words, never the profile: it is safe to name.
  if (!res.ok) throw fail(`meeting-api refused the session profile: HTTP ${res.status} ${text.slice(0, 200)}`.trim());
  let body: unknown;
  try { body = JSON.parse(text); } catch (e) { throw fail('the session profile answer is not JSON', e); }
  const validate = isWritebackBody();
  if (!validate(body)) {
    // Name where the answer is off-contract, never its values.
    const where = (validate.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message}`).slice(0, 3).join('; ');
    throw fail(`the session profile answer is not a session-profile.v1 WritebackBody (${where})`);
  }
  const files = (body as { files: Array<{ path: string; data: string }> }).files
    .map((f) => ({ path: f.path, data: Buffer.from(f.data, 'base64') }));
  return writeSessionProfile(dataDir, files);   // throws SessionSyncError('session-restore') on a bad set
}

/** The invocation's restore: `sessionRestoreUrl` with its MeetingToken. */
export async function restoreSession(inv: { sessionRestoreUrl?: string; token?: string }, dataDir: string, opts: RestoreOptions = {}): Promise<number> {
  if (!inv.sessionRestoreUrl) throw fail('the invocation names no session restore URL');
  return restoreSessionFromUrl(inv.sessionRestoreUrl, inv.token, dataDir, opts);
}
