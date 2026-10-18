/** credential-broker.v1 assertion — the ONE TypeScript signer and verifier.
 *
 * Twin of core/agent/contracts/credential-broker.v1/assertion.py. Both reproduce the contract's
 * golden vectors (golden/SignedAssertionVector.*.json) byte for byte; that is what holds the two
 * languages to one wire. Server-only: it reads key files and must never reach a browser bundle.
 *
 *   X-Vexa-Assertion: <base64url(claims JSON), unpadded>.<hex HMAC-SHA256(role key, encoded part)>
 *
 * The claims bind role, actor, session, time, a single-use nonce, the method, the exact path with
 * its query, and the SHA-256 of the exact body bytes. Accepted for 30 s after `at` (5 s ahead), once.
 */
import { createHash, createHmac, randomUUID, timingSafeEqual } from 'node:crypto';
import { readFile } from 'node:fs/promises';

export const ASSERTION_HEADER = 'X-Vexa-Assertion';
export const ROLES = ['agent', 'human', 'git'] as const;
export type Role = (typeof ROLES)[number];
export const MAX_AGE_S = 30;
export const MAX_SKEW_S = 5;
export const NONCE_TTL_S = MAX_AGE_S + MAX_SKEW_S + 25;
export const MIN_KEY_BYTES = 32;
const FIELDS = ['role', 'actor', 'session', 'at', 'nonce', 'method', 'path', 'body'] as const;

export type Claims = {
  role: Role; actor: string; session: string; at: number; nonce: string;
  method: 'GET' | 'POST'; path: string; body: string;
};

/** Refused, with the reason as a kind (malformed · role · signature · expired · binding · replay).
 *  The message never carries a claim value, a key or a body. */
export class AssertionRefused extends Error {
  constructor(public readonly kind: string) { super('Product identity refused'); }
}
export class KeyUnavailable extends Error {}

const ASCII_WHITESPACE = new Set([0x20, 0x09, 0x0a, 0x0d, 0x0b, 0x0c]);
/** Python's bytes.strip(), exactly: a key read in either language is the same bytes. */
function strip(raw: Buffer): Buffer {
  let start = 0, end = raw.length;
  while (start < end && ASCII_WHITESPACE.has(raw[start])) start++;
  while (end > start && ASCII_WHITESPACE.has(raw[end - 1])) end--;
  return raw.subarray(start, end);
}

export async function loadKey(path: string | undefined): Promise<Buffer> {
  if (!path) throw new KeyUnavailable('no key file configured');
  let raw: Buffer;
  try { raw = await readFile(path); } catch { throw new KeyUnavailable('key file unreadable'); }
  const key = strip(raw);
  if (key.length < MIN_KEY_BYTES) throw new KeyUnavailable(`key shorter than ${MIN_KEY_BYTES} bytes`);
  return key;
}

export function bodyDigest(body: string | Uint8Array): string {
  return createHash('sha256').update(typeof body === 'string' ? Buffer.from(body, 'utf8') : body).digest('hex');
}

/** JSON exactly as Python's json.dumps(separators=(',', ':')) writes it: non-ASCII as \uXXXX. */
function pythonJson(value: unknown): string {
  return JSON.stringify(value).replace(/[\u0080-￿]/g, (c) => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
}

function encode(claims: Claims): string {
  const ordered = Object.fromEntries(FIELDS.map((k) => [k, claims[k]]));
  return Buffer.from(pythonJson(ordered), 'utf8').toString('base64url');
}

function signature(key: Buffer, encoded: string): string {
  return createHmac('sha256', key).update(encoded).digest('hex');
}

export function signAssertion(key: Buffer, input: {
  role: Role; actor: string; session: string; method: 'GET' | 'POST'; path: string;
  body?: string | Uint8Array; at?: number; nonce?: string;
}): string {
  if (!ROLES.includes(input.role)) throw new Error('unknown role');
  if (input.method !== 'GET' && input.method !== 'POST') throw new Error('unsupported method');
  if (key.length < MIN_KEY_BYTES) throw new KeyUnavailable(`key shorter than ${MIN_KEY_BYTES} bytes`);
  const claims: Claims = {
    role: input.role, actor: String(input.actor), session: String(input.session),
    at: input.at ?? Math.floor(Date.now() / 1000), nonce: input.nonce ?? randomUUID().replace(/-/g, ''),
    method: input.method, path: input.path, body: bodyDigest(input.body ?? ''),
  };
  const encoded = encode(claims);
  return `${encoded}.${signature(key, encoded)}`;
}

export function verifyAssertion(
  header: string,
  keyFor: (role: Role) => Buffer,
  request: { method: string; path: string; body: string | Uint8Array },
  options: { now?: number; remember?: (nonce: string, expiresAt: number) => boolean } = {},
): Claims {
  const parts = header.split('.');
  if (parts.length !== 2) throw new AssertionRefused('malformed');
  const [encoded, sig] = parts;
  let claims: Record<string, unknown>;
  try { claims = JSON.parse(Buffer.from(encoded, 'base64url').toString('utf8')); } catch { throw new AssertionRefused('malformed'); }
  if (!claims || typeof claims !== 'object' || Array.isArray(claims)
      || Object.keys(claims).sort().join() !== [...FIELDS].sort().join()) throw new AssertionRefused('malformed');
  const role = claims.role as Role;
  if (!ROLES.includes(role)) throw new AssertionRefused('role');
  let key: Buffer;
  try { key = keyFor(role); } catch { throw new AssertionRefused('role'); }
  const expected = Buffer.from(signature(key, encoded), 'utf8');
  const given = Buffer.from(sig, 'utf8');
  if (expected.length !== given.length || !timingSafeEqual(expected, given)) throw new AssertionRefused('signature');
  const text = (k: string) => typeof claims[k] === 'string' && (claims[k] as string).length >= 1 && (claims[k] as string).length <= 160;
  if (!['GET', 'POST'].includes(claims.method as string) || typeof claims.path !== 'string' || !claims.path.startsWith('/api/')
      || claims.path.length > 16384 || typeof claims.body !== 'string' || !Number.isInteger(claims.at)
      || !text('actor') || !text('session') || !text('nonce')) throw new AssertionRefused('malformed');
  const now = options.now ?? Date.now() / 1000;
  const at = claims.at as number;
  if (!(now - MAX_AGE_S <= at && at <= now + MAX_SKEW_S)) throw new AssertionRefused('expired');
  if (claims.method !== request.method || claims.path !== request.path || claims.body !== bodyDigest(request.body)) {
    throw new AssertionRefused('binding');
  }
  if (options.remember && !options.remember(claims.nonce as string, now + NONCE_TTL_S)) throw new AssertionRefused('replay');
  return claims as unknown as Claims;
}
