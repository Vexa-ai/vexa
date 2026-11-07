// @vitest-environment node
/** The terminal's broker adapter: signs as the human role for the validated user with the one
 *  TypeScript signer, binds the browser session without revealing the cookie, and turns every
 *  failure into a typed, logged fault that carries no value. */
import { mkdtempSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';

vi.mock('next/headers', () => ({ cookies: async () => ({ get: (n: string) => (n === 'vexa-token' ? { value: 'COOKIE-VALUE' } : undefined) }) }));
vi.mock('../../tokens/currentUser', () => ({ currentUser: vi.fn(async () => ({ ok: true, userId: 42, email: 'p@example.test' })) }));
import { currentUser } from '../../tokens/currentUser';
import { brokerCall, BrokerFault, publicOrigin } from '../broker';
import { verifyAssertion } from '../assertion';

const dir = mkdtempSync(join(tmpdir(), 'human-key-'));
const KEY = 'fixture-human-key-' + 'h'.repeat(40);
writeFileSync(join(dir, 'key'), KEY + '\n');
let sent: Request[] = [];
let respond: () => Response | Promise<Response> = () => Response.json({ connections: [] });
let warnings: string[] = [];

beforeEach(() => {
  sent = []; warnings = [];
  process.env.VEXA_CONNECTIONS_BROKER_URL = 'http://credential-broker:8100/';
  process.env.VEXA_CONNECTIONS_HUMAN_KEY_FILE = join(dir, 'key');
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => { sent.push(new Request(url, init)); return respond(); }));
  vi.spyOn(console, 'warn').mockImplementation((line: string) => { warnings.push(line); });
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); respond = () => Response.json({ connections: [] }); });

const faults = () => warnings.map((w) => JSON.parse(w)).filter((w) => w.event === 'broker_fault');

test('signs as the human role for the validated user, for exactly this request', async () => {
  await brokerCall('POST', '/api/setup', { provider: 'custom_secret', label: 'Bot' });
  const r = sent[0];
  expect(r.url).toBe('http://credential-broker:8100/api/setup');
  const body = await r.text();
  const claims = verifyAssertion(r.headers.get('x-vexa-assertion')!, () => Buffer.from(KEY), { method: 'POST', path: '/api/setup', body });
  expect(claims.role).toBe('human');
  expect(claims.actor).toBe('42');
  expect(claims.session).toMatch(/^[a-f0-9]{64}$/);
  expect(claims.session).not.toContain('COOKIE-VALUE');
  expect(() => verifyAssertion(r.headers.get('x-vexa-assertion')!, () => Buffer.alloc(40, 'a'), { method: 'POST', path: '/api/setup', body })).toThrow();
});

test('an unauthenticated request is refused before anything is signed', async () => {
  vi.mocked(currentUser).mockResolvedValueOnce({ ok: false, status: 401, error: 'Not authenticated' });
  await expect(brokerCall('GET', '/api/connections')).rejects.toMatchObject({ kind: 'unauthenticated' });
  expect(sent).toHaveLength(0);
});

test.each([
  ['unset broker URL', () => { delete process.env.VEXA_CONNECTIONS_BROKER_URL; }, 'config'],
  ['missing key file', () => { process.env.VEXA_CONNECTIONS_HUMAN_KEY_FILE = join(dir, 'absent'); }, 'config'],
  ['broker unreachable', () => { respond = () => { throw new TypeError('fetch failed'); }; }, 'transport'],
  ['broker 500', () => { respond = () => new Response('PRIVATE-BODY', { status: 500 }); }, 'http_500'],
  ['broker answers non-JSON', () => { respond = () => new Response('not json', { status: 200 }); }, 'parse'],
])('%s is a typed, logged fault without values', async (_label, arrange, kind) => {
  arrange();
  await expect(brokerCall('POST', '/api/connections/' + 'c'.repeat(32) + '/custom-secret', { value: 'PRIVATE-SECRET' }))
    .rejects.toMatchObject({ kind });
  const logged = faults();
  expect(logged.at(-1)).toMatchObject({ source: 'credential-broker', kind, role: 'human', route: '/api/connections/{cid}/custom-secret' });
  expect(warnings.join('\n')).not.toMatch(/PRIVATE|COOKIE-VALUE|fixture-human-key/);
});

test('an actionable refusal carries the broker sentence', async () => {
  respond = () => Response.json({ detail: 'Confirm the destination host before saving' }, { status: 409 });
  const e = await brokerCall('POST', '/api/connections/' + 'c'.repeat(32) + '/custom-secret', {}).catch((x) => x);
  expect(e).toBeInstanceOf(BrokerFault);
  expect([e.status, e.detail]).toEqual([409, 'Confirm the destination host before saving']);
});

test('public origin: declared, else NEXTAUTH_URL, never a request header', () => {
  process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN = 'https://app.example.test/';
  expect(publicOrigin()).toBe('https://app.example.test');
  delete process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN;
  process.env.NEXTAUTH_URL = 'https://minutes.example.test/some/path';
  expect(publicOrigin()).toBe('https://minutes.example.test');
  delete process.env.NEXTAUTH_URL;
  expect(publicOrigin()).toBeNull();
});
