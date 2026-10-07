/** Server-only adapter. Validated login identity, never user-info/body identity.
 * Separate terminal signing key is not available to agent-api or MCP workers. */
import { createHash, createHmac, randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { cookies } from 'next/headers';
import { currentUser } from '../tokens/currentUser';
import { AUTH_COOKIE } from '../auth/adminApi';

export async function brokerCall(method: string, path: string, payload?: unknown) {
  const me = await currentUser();
  if (!me.ok) throw new Error('Sign in to manage connections');
  const token = (await cookies()).get(AUTH_COOKIE)?.value;
  const base = process.env.VEXA_CONNECTIONS_BROKER_URL;
  const keyPath = process.env.VEXA_CONNECTIONS_HUMAN_KEY_FILE;
  if (!token || !base || !keyPath) throw new Error('Connections are not configured');
  const key = (await readFile(keyPath, 'utf8')).trim();
  if (key.length < 32) throw new Error('Connections are not configured');
  const body = payload === undefined ? '' : JSON.stringify(payload);
  const claims = {
    role: 'human', actor: String(me.userId),
    session: createHmac('sha256', key).update(token).digest('hex'),
    at: Math.floor(Date.now()/1000), nonce: randomUUID(), method, path,
    body: createHash('sha256').update(body).digest('hex'),
  };
  const encoded = Buffer.from(JSON.stringify(claims)).toString('base64url');
  const signature = createHmac('sha256',key).update(encoded).digest('hex');
  const r = await fetch(base.replace(/\/$/,'')+path, {
    method, body: body || undefined, redirect: 'error', cache: 'no-store',
    signal: AbortSignal.timeout(20000),
    headers: {'Content-Type':'application/json', 'X-Vexa-Assertion':encoded+'.'+signature},
  });
  if (!r.ok) throw new Error('Connection service refused the request');
  return r.json();
}
