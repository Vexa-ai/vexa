/** Server-only adapter to the credential broker (credential-broker.v1), role `human`.
 *
 * Identity is the validated sign-in (currentUser checks the cookie against identity on every call),
 * never a user-info cookie or a body field. The human key is mounted into this server only — never
 * into agent-api, an MCP process or a worker — and is signed with the one TypeScript signer
 * (./assertion). A failure is a BrokerFault with a kind (unauthenticated · config · transport ·
 * http_<status> · parse), logged once here as a structured line without any value. */
import { createHmac } from 'node:crypto';
import { cookies } from 'next/headers';
import { currentUser } from '../tokens/currentUser';
import { AUTH_COOKIE } from '../auth/adminApi';
import { ASSERTION_HEADER, KeyUnavailable, loadKey, signAssertion } from './assertion';

export class BrokerFault extends Error {
  constructor(public readonly kind: string, public readonly status?: number, public readonly detail?: string) {
    super(`credential broker fault: ${kind}`);
  }
}

const routeOf = (path: string) => path.split('?')[0].replace(/\/[a-f0-9]{32}(?=\/|$)/g, '/{cid}');

export function brokerFault(kind: string, method: string, path: string, status?: number, detail?: string): BrokerFault {
  console.warn(JSON.stringify({ event: 'broker_fault', source: 'credential-broker', kind, role: 'human', method, route: routeOf(path), status }));
  return new BrokerFault(kind, status, detail);
}

/** The origin browser POSTs must come from: the declared Connections origin, else the terminal's
 *  own NEXTAUTH_URL. Deployment configuration only — never a forwarded Host header. */
export function publicOrigin(): string | null {
  const configured = process.env.VEXA_CONNECTIONS_PUBLIC_ORIGIN || process.env.NEXTAUTH_URL;
  if (!configured) return null;
  try { return new URL(configured).origin; } catch { return null; }
}

// A refusal a person can act on carries the broker's fixed sentence (the contract guarantees it
// never echoes input); anything else is "unavailable".
const ACTIONABLE = new Set([400, 404, 409, 422]);

export async function brokerCall(method: 'GET' | 'POST', path: string, payload?: unknown) {
  const me = await currentUser();
  if (!me.ok) throw brokerFault('unauthenticated', method, path);
  const token = (await cookies()).get(AUTH_COOKIE)?.value;
  const base = process.env.VEXA_CONNECTIONS_BROKER_URL;
  if (!token) throw brokerFault('unauthenticated', method, path);
  if (!base) throw brokerFault('config', method, path);
  let key: Buffer;
  try { key = await loadKey(process.env.VEXA_CONNECTIONS_HUMAN_KEY_FILE); }
  catch (e) { throw brokerFault(e instanceof KeyUnavailable ? 'config' : 'transport', method, path); }
  const body = payload === undefined ? '' : JSON.stringify(payload);
  const header = signAssertion(key, {
    role: 'human', actor: String(me.userId),
    // Binds an OAuth callback to the browser session that started it, without revealing the cookie.
    session: createHmac('sha256', key).update(token).digest('hex'),
    method, path, body,
  });
  let r: Response;
  try {
    r = await fetch(base.replace(/\/$/, '') + path, {
      method, body: body || undefined, redirect: 'error', cache: 'no-store',
      signal: AbortSignal.timeout(20000),
      headers: { 'Content-Type': 'application/json', [ASSERTION_HEADER]: header },
    });
  } catch { throw brokerFault('transport', method, path); }
  if (!r.ok) {
    let detail: string | undefined;
    if (ACTIONABLE.has(r.status)) {
      try { const d = (await r.json())?.detail; if (typeof d === 'string') detail = d; } catch { /* no detail */ }
    }
    throw brokerFault(`http_${r.status}`, method, path, r.status, detail);
  }
  try { return await r.json(); } catch { throw brokerFault('parse', method, path, r.status); }
}
