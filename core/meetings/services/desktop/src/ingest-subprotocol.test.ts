/**
 * /ingest speaks ws.v1 § Connect's subprotocol form (card ca078278e83d).
 *
 * The extension no longer puts its API key in the ingest URL; it opens
 *   new WebSocket(url, ['vexa.v1', 'vexa.key.<key>'])
 * and a browser FAILS the handshake unless the server answers one of the offered protocols.
 * Proves:
 *  • offered ['vexa.v1', 'vexa.key.<key>'] → the handshake succeeds and answers 'vexa.v1'
 *    (never the key entry: the key must not be echoed into a response header);
 *  • the session starts (the 'ready' frame arrives) exactly as before;
 *  • the answer is 'vexa.v1' whatever the offer order (the ws default answers the first entry);
 *  • a client offering no subprotocol (CLI, tests, old extension) still connects;
 *  • the key never appears in the desktop's log output.
 * Run: npx tsx src/ingest-subprotocol.test.ts
 */
import { WebSocket } from 'ws';
import { startDesktop } from './desktop.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const KEY = 'vxa_ingest_test_SECRET_9f3c';

function open(url: string, protocols?: string[]): Promise<{ ws: WebSocket; ready: boolean; protocol: string }> {
  return new Promise((resolve, reject) => {
    const ws = protocols ? new WebSocket(url, protocols) : new WebSocket(url);
    const t = setTimeout(() => reject(new Error('no ready frame within 3s')), 3000);
    ws.on('error', (e) => { clearTimeout(t); reject(e); });
    ws.on('message', (d) => {
      try {
        if (JSON.parse(d.toString()).type === 'ready') { clearTimeout(t); resolve({ ws, ready: true, protocol: ws.protocol }); }
      } catch { /* binary or non-JSON */ }
    });
  });
}

async function main(): Promise<void> {
  const lines: string[] = [];
  const origLog = console.log, origErr = console.error, origWarn = console.warn;
  const capture = (...a: unknown[]) => lines.push(a.map(String).join(' '));
  const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, txUrl: 'http://stt.test', noSignalMs: 60_000 });
  try {
    const base = `ws://localhost:${desk.ingestPort}/ingest?platform=google_meet&native_meeting_id=sub-proto-test&language=auto`;

    console.log = capture; console.error = capture; console.warn = capture;
    let withKey: Awaited<ReturnType<typeof open>> | null = null;
    let err = '';
    try { withKey = await open(base, ['vexa.v1', `vexa.key.${KEY}`]); } catch (e: any) { err = e?.message || String(e); }
    console.log = origLog; console.error = origErr; console.warn = origWarn;

    check('subprotocol form: handshake accepted, ready frame arrives', !!withKey?.ready, err);
    check("subprotocol form: server answers 'vexa.v1'", withKey?.protocol === 'vexa.v1', String(withKey?.protocol));
    check('subprotocol form: the key entry is never echoed', !String(withKey?.protocol).includes(KEY), String(withKey?.protocol));
    withKey?.ws.close();

    // Order must not matter: the ws library's default answers the FIRST offered protocol, which
    // would echo a leading key entry into the response header. /ingest picks 'vexa.v1' itself.
    console.log = capture; console.error = capture; console.warn = capture;
    let keyFirst: Awaited<ReturnType<typeof open>> | null = null;
    try { keyFirst = await open(base.replace('sub-proto-test', 'key-first-test'), [`vexa.key.${KEY}`, 'vexa.v1']); } catch (e: any) { err = e?.message || String(e); }
    console.log = origLog; console.error = origErr; console.warn = origWarn;
    check("key entry offered first: server still answers 'vexa.v1', never the key", keyFirst?.protocol === 'vexa.v1', String(keyFirst?.protocol).includes(KEY) ? '<the key entry>' : String(keyFirst?.protocol));
    keyFirst?.ws.close();

    console.log = capture; console.error = capture; console.warn = capture;
    let plain: Awaited<ReturnType<typeof open>> | null = null;
    try { plain = await open(base.replace('sub-proto-test', 'no-proto-test')); } catch (e: any) { err = e?.message || String(e); }
    console.log = origLog; console.error = origErr; console.warn = origWarn;
    check('no subprotocol: still connects (ready frame)', !!plain?.ready, err);
    check('no subprotocol: none answered', plain?.protocol === '', String(plain?.protocol));
    plain?.ws.close();

    await new Promise((r) => setTimeout(r, 100));
    check('the key never appears in desktop log output', !lines.some((l) => l.includes(KEY)),
      lines.filter((l) => l.includes(KEY)).join(' | '));
  } finally {
    console.log = origLog; console.error = origErr; console.warn = origWarn;
    await desk.close();
  }

  if (failed) { console.error(`\n❌ ingest subprotocol: ${failed} check(s) FAILED.`); process.exit(1); }
  console.log("\n✅ ingest subprotocol: /ingest accepts ['vexa.v1', 'vexa.key.<key>'], answers 'vexa.v1', never echoes or logs the key.");
}
main().catch((e) => { console.error(e); process.exit(1); });
