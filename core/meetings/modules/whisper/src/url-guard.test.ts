/**
 * The TypeScript outbound URL guard against the shared vector table — the same rows the Python guard
 * (`ssrf.py`) is held to, from a byte-identical copy (scripts/parity.json, fact outbound-url-vectors);
 * then the pieces only a client has: the connect-time lookup pins the checked address, and a
 * customer-owned endpoint is never dialled when it names, or resolves to, an internal address.
 * Run: npm test (chained)  or  npx tsx src/url-guard.test.ts
 */
import { readFileSync } from 'node:fs';
import { createServer } from 'node:http';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SsrfError, guardedLookup, isBlockedHostname, isBlockedIp, validateUrl } from './url-guard.js';
import { TranscriptionClient, TranscriptionError } from './index.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  if (!cond) { console.log(`  ❌ ${name}  — ${detail}`); failed++; }
};

const V = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), 'outbound-url-vectors.json'), 'utf8'));

async function run() {
  for (const r of V.addresses) check(`address ${r.addr}`, isBlockedIp(r.addr) === r.blocked, `want blocked=${r.blocked}`);
  for (const r of V.hostnames) check(`hostname ${r.host}`, isBlockedHostname(r.host) === r.blocked, `want blocked=${r.blocked}`);
  for (const r of V.urls) {
    let refused = false;
    try {
      await validateUrl(r.url, { resolver: async () => {
        if (r.resolved === null) throw new Error(`${r.url} is a literal address and needs no lookup`);
        return [...r.resolved];
      } });
    } catch (e) { refused = e instanceof SsrfError; if (!refused) throw e; }
    check(`url ${r.url}`, refused === r.refused, `want refused=${r.refused}`);
  }
  console.log(`  ✅ ${V.addresses.length + V.hostnames.length + V.urls.length} shared vectors checked`);

  // the lookup hook pins the connection to the checked address, and refuses an internal answer
  const pinned = await new Promise<string>((res, rej) => guardedLookup(async () => ['93.184.216.34', '2606:4700::1111'])(
    'hooks.example.com', {}, (err, addr) => (err ? rej(err) : res(addr as string))));
  check('lookup pins the first checked address', pinned === '93.184.216.34', pinned);
  const refusedLookup = await new Promise<string>((res) => guardedLookup(async () => ['93.184.216.34', '::ffff:169.254.169.254'])(
    'rebind.example.com', { all: true }, (err) => res(err ? (err as any).code : 'none')));
  check('lookup refuses when any answer is internal', refusedLookup === 'EVEXAREFUSED', refusedLookup);

  // a customer-owned endpoint: never dialled when it names, or resolves to, an internal address
  let hits = 0;
  const server = createServer((_req, res) => { hits++; res.writeHead(200, { 'content-type': 'application/json' }); res.end('{"text":"x"}'); });
  await new Promise<void>((res) => server.listen(0, '127.0.0.1', () => res()));
  const port = (server.address() as any).port;
  const pcm = new Float32Array(1600).fill(0.05);
  for (const [name, serviceUrl, resolver] of [
    ['a literal internal address', `http://127.0.0.1:${port}`, async () => ['127.0.0.1']],
    ['a name that resolves to one (connect-time)', `http://stt.example.com:${port}`, async () => ['127.0.0.1']],
  ] as const) {
    const client = new TranscriptionClient({ serviceUrl, publicOnly: true, resolveHost: resolver, maxRetries: 0 });
    let fault: TranscriptionError | null = null;
    try { await client.transcribe(pcm); } catch (e) { fault = e instanceof TranscriptionError ? e : null; }
    check(`customer endpoint refused: ${name}`, !!fault && fault.retryable === false && /internal or private/.test(fault.message), String(fault?.message));
  }
  check('the internal server was never reached', hits === 0, `hits=${hits}`);
  // the operator's own endpoint (not customer-owned) keeps working on a private address
  const own = new TranscriptionClient({ serviceUrl: `http://127.0.0.1:${port}`, maxRetries: 0 });
  const out = await own.transcribe(pcm).catch((e) => e);
  check("the deployment's own endpoint is reached", hits === 1 && out?.text === 'x', `hits=${hits} out=${JSON.stringify(out)}`);
  server.close();

  if (failed) { console.log(`\n❌ ${failed} url-guard check(s) failed`); process.exit(1); }
  console.log('✅ url-guard: shared vectors, pinned lookup, customer endpoints refused');
}
run().catch((e) => { console.error(e); process.exit(1); });
