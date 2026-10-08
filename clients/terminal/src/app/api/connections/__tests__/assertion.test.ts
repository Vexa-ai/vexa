// @vitest-environment node
/** The TypeScript signer against credential-broker.v1: it reproduces every golden signing vector
 *  the Python signer and the contract validator reproduce, verifies them, and refuses forged,
 *  expired, replayed and mis-bound assertions with a kind. */
import { readFileSync, readdirSync, writeFileSync, mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { describe, expect, test } from 'vitest';
import { AssertionRefused, KeyUnavailable, loadKey, signAssertion, verifyAssertion, type Claims } from '../assertion';

const GOLDEN = join(__dirname, '../../../../../../../core/agent/contracts/credential-broker.v1/golden');
const vectors = readdirSync(GOLDEN).filter((f) => f.startsWith('SignedAssertionVector.')).sort();
type Vector = { key: string; claims: Claims; request_body: string; encoded: string; signature: string; header: string };
const load = (f: string): Vector => JSON.parse(readFileSync(join(GOLDEN, f), 'utf8'));

describe('golden vectors (shared with the Python signer)', () => {
  test('there are vectors for every role', () => {
    expect(new Set(vectors.map((f) => load(f).claims.role))).toEqual(new Set(['agent', 'human', 'git']));
  });
  test.each(vectors)('%s reproduces and verifies', (f) => {
    const v = load(f), c = v.claims;
    const key = Buffer.from(v.key, 'utf8');
    expect(signAssertion(key, { role: c.role, actor: c.actor, session: c.session, method: c.method, path: c.path,
      body: v.request_body, at: c.at, nonce: c.nonce })).toBe(v.header);
    expect(verifyAssertion(v.header, () => key, { method: c.method, path: c.path, body: v.request_body }, { now: c.at + 1 })).toEqual(c);
  });
  test('non-ASCII claims encode exactly as Python json.dumps does', () => {
    // Produced by core/agent/contracts/credential-broker.v1/assertion.py with the same inputs.
    const python = 'eyJyb2xlIjoiaHVtYW4iLCJhY3RvciI6IlpvXHUwMGViIFx1ZDgzZFx1ZGU4MCIsInNlc3Npb24iOiJzIiwiYXQiOjE3NjAwMDAwMDAsIm5vbmNlIjoibjEiLCJtZXRob2QiOiJHRVQiLCJwYXRoIjoiL2FwaS9jb25uZWN0aW9ucyIsImJvZHkiOiJlM2IwYzQ0Mjk4ZmMxYzE0OWFmYmY0Yzg5OTZmYjkyNDI3YWU0MWU0NjQ5YjkzNGNhNDk1OTkxYjc4NTJiODU1In0.867b9878767e225505e9fca07dda3c135328e861d6be0b67661933d61cf98a64';
    expect(signAssertion(Buffer.alloc(32, 'k'), { role: 'human', actor: 'Zoë 🚀', session: 's', method: 'GET',
      path: '/api/connections', at: 1760000000, nonce: 'n1' })).toBe(python);
  });
});

describe('refusals carry their kind', () => {
  const v = load(vectors[0]);
  const c = v.claims;
  const key = Buffer.from(v.key, 'utf8');
  const verify = (over: { header?: string; key?: Buffer; now?: number; method?: string; path?: string; body?: string }) =>
    verifyAssertion(over.header ?? v.header, () => over.key ?? key,
      { method: over.method ?? c.method, path: over.path ?? c.path, body: over.body ?? v.request_body }, { now: over.now ?? c.at });
  const kind = (fn: () => unknown) => { try { fn(); } catch (e) { return (e as AssertionRefused).kind; } return 'accepted'; };

  test('forged with another key', () => expect(kind(() => verify({ key: Buffer.alloc(40, 'x') }))).toBe('signature'));
  test('claims tampered after signing', () => {
    const forged = Buffer.from(JSON.stringify({ ...c, actor: 'victim' })).toString('base64url') + '.' + v.signature;
    expect(kind(() => verify({ header: forged }))).toBe('signature');
  });
  test('expired, and from the future', () => {
    expect(kind(() => verify({ now: c.at + 31 }))).toBe('expired');
    expect(kind(() => verify({ now: c.at - 6 }))).toBe('expired');
  });
  test('bound to its method, path and body', () => {
    expect(kind(() => verify({ method: 'POST' }))).toBe('binding');
    expect(kind(() => verify({ path: c.path + '?x=1' }))).toBe('binding');
    expect(kind(() => verify({ body: '{}' }))).toBe('binding');
  });
  test('malformed headers', () => {
    for (const h of ['', 'x', 'a.b.c', '!!!.00']) expect(kind(() => verify({ header: h }))).toBe('malformed');
  });
  test('replayed', () => {
    const seen = new Set<string>();
    const remember = (n: string) => (seen.has(n) ? false : (seen.add(n), true));
    verifyAssertion(v.header, () => key, { method: c.method, path: c.path, body: v.request_body }, { now: c.at, remember });
    expect(kind(() => verifyAssertion(v.header, () => key, { method: c.method, path: c.path, body: v.request_body }, { now: c.at, remember }))).toBe('replay');
  });
});

describe('keys', () => {
  test('a key file is stripped exactly like Python bytes.strip and must be 32 bytes', async () => {
    const dir = mkdtempSync(join(tmpdir(), 'keys-'));
    writeFileSync(join(dir, 'ok'), '  ' + 'a'.repeat(40) + '\n\t');
    expect((await loadKey(join(dir, 'ok'))).toString()).toBe('a'.repeat(40));
    writeFileSync(join(dir, 'short'), 'short\n');
    await expect(loadKey(join(dir, 'short'))).rejects.toBeInstanceOf(KeyUnavailable);
    await expect(loadKey(join(dir, 'absent'))).rejects.toBeInstanceOf(KeyUnavailable);
    await expect(loadKey(undefined)).rejects.toBeInstanceOf(KeyUnavailable);
  });
  test('a short key never signs', () => {
    expect(() => signAssertion(Buffer.from('short'), { role: 'human', actor: 'a', session: 's', method: 'GET', path: '/api/x' })).toThrow(KeyUnavailable);
  });
});
