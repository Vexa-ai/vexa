/**
 * L3 — P20 / ADR-0012 complete-mediation SEAM on the desktop's read paths.
 *
 * Proves every open read path now CONSULTS `canAccess(subject, resource, 'read')`
 * before serving — the deliverable is the seam, not a real policy. Two hosts:
 *  • a DENYING host (canAccess = () => false): GET /transcripts → 403, GET
 *    /recordings → 403, GET /recordings/.../player → 403, GET /bots → empty list,
 *    and a /ws subscribe to a (seeded) meeting yields NO broadcast for it — the
 *    denied subject gets no live data/health.
 *  • the DEFAULT host (ownerOnly = allow-all, the single-user localhost): the same
 *    paths behave normally (200 / list populated / the broadcast arrives).
 *
 * The broadcast we deny is the STT-free `no-signal` health frame (the watchdog
 * fires with no STT configured), gated by the SAME liveClients subscription set —
 * so it cleanly proves the /ws subscribe path drops denied keys with no STT.
 * Run: npx tsx src/access.test.ts
 */
import { WebSocket } from 'ws';
import { startDesktop } from './desktop.js';
import { ownerOnly } from './access.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Seed a meeting in the host's store by opening (and closing) an ingest WS — the
// connection calls resolve(platform, native), so the meeting exists for /bots etc.
async function seed(ingestPort: number, platform: string, native: string): Promise<void> {
  const ws = new WebSocket(`ws://localhost:${ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}`);
  await new Promise<void>((r) => ws.on('open', () => r()));
  await sleep(50);
  ws.close();
  await sleep(50);
}

// Subscribe a live /ws client to one meeting and collect any health frames for it
// over `waitMs`. The host's no-signal watchdog (no STT) is the broadcast source.
// Returns the count of health frames the client received for `key`.
async function collectHealth(gatewayPort: number, platform: string, native: string, apiKey: string | null, waitMs: number): Promise<number> {
  const key = `${platform}/${native}`;
  const q = apiKey ? `?api_key=${encodeURIComponent(apiKey)}` : '';
  const live = new WebSocket(`ws://localhost:${gatewayPort}/ws${q}`);
  let got = 0;
  await new Promise<void>((r) => live.on('open', () => r()));
  live.on('message', (d) => { try { const m = JSON.parse(d.toString()); if (m.type === 'health' && m.meeting === key) got++; } catch { /* */ } });
  live.send(JSON.stringify({ action: 'subscribe', meetings: [{ platform, native_meeting_id: native }] }));
  await sleep(waitMs);
  live.close();
  return got;
}

async function main(): Promise<void> {
  const platform = 'google_meet', native = 'access-test';

  // ── DENY: canAccess = () => false on every read path ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', noSignalMs: 150, canAccess: () => false });
    try {
      await seed(desk.ingestPort, platform, native);

      const tr = await fetch(`http://localhost:${desk.gatewayPort}/transcripts/${platform}/${native}`);
      check('deny: GET /transcripts/{p}/{n} → 403', tr.status === 403, String(tr.status));
      check('deny: /transcripts body = {error:forbidden}', (await tr.json() as any).error === 'forbidden');

      // Recordings has no master on disk, but the 403 must precede the 404 — the
      // deny is checked BEFORE the resource is looked up.
      const rec = await fetch(`http://localhost:${desk.gatewayPort}/recordings/${platform}/${native}`);
      check('deny: GET /recordings/{p}/{n} → 403 (before 404)', rec.status === 403, String(rec.status));
      check('deny: /recordings body = {error:forbidden}', (await rec.json() as any).error === 'forbidden');

      const ply = await fetch(`http://localhost:${desk.gatewayPort}/recordings/${platform}/${native}/player`);
      check('deny: GET /recordings/{p}/{n}/player → 403', ply.status === 403, String(ply.status));

      const bots = await (await fetch(`http://localhost:${desk.gatewayPort}/bots`)).json() as any;
      check('deny: GET /bots → empty list', Array.isArray(bots.meetings) && bots.meetings.length === 0, JSON.stringify(bots.meetings));

      // A live ingest session with no audio (no-signal watchdog) would broadcast a
      // health frame — but a DENIED /ws subscriber must receive NONE for that key.
      const ingest = new WebSocket(`ws://localhost:${desk.ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}`);
      await new Promise<void>((r) => ingest.on('open', () => r()));
      const got = await collectHealth(desk.gatewayPort, platform, native, 'denied-key', 700);
      ingest.close();
      check('deny: /ws subscribe yields NO broadcast for the denied meeting', got === 0, `${got} health frame(s) leaked`);
    } finally {
      await desk.close();
    }
  }

  // ── ALLOW: the default ownerOnly adapter (single local user owns everything) ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', noSignalMs: 150, canAccess: ownerOnly });
    try {
      await seed(desk.ingestPort, platform, native);

      const tr = await fetch(`http://localhost:${desk.gatewayPort}/transcripts/${platform}/${native}`);
      check('allow: GET /transcripts/{p}/{n} → 200', tr.status === 200, String(tr.status));
      const trBody = await tr.json() as any;
      check('allow: /transcripts is the seeded meeting (not forbidden)', trBody.error !== 'forbidden' && trBody.platform === platform);

      const ply = await fetch(`http://localhost:${desk.gatewayPort}/recordings/${platform}/${native}/player`);
      check('allow: GET /recordings/{p}/{n}/player → 200 (HTML)', ply.status === 200 && (ply.headers.get('content-type') || '').includes('text/html'), `${ply.status} ${ply.headers.get('content-type')}`);

      // No recording exists → the bytes route falls through to its normal 404 (NOT
      // a 403): allow lets the request reach the resource lookup.
      const rec = await fetch(`http://localhost:${desk.gatewayPort}/recordings/${platform}/${native}`);
      check('allow: GET /recordings/{p}/{n} → 404 no-recording (not 403)', rec.status === 404, String(rec.status));

      const bots = await (await fetch(`http://localhost:${desk.gatewayPort}/bots`)).json() as any;
      check('allow: GET /bots → list populated (the seeded meeting visible)', Array.isArray(bots.meetings) && bots.meetings.some((b: any) => b.platform === platform && b.native_meeting_id === native), JSON.stringify(bots.meetings));

      // The ALLOWED subscriber DOES receive the no-signal health broadcast.
      const ingest = new WebSocket(`ws://localhost:${desk.ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}`);
      await new Promise<void>((r) => ingest.on('open', () => r()));
      const got = await collectHealth(desk.gatewayPort, platform, native, null, 900);
      ingest.close();
      check('allow: /ws subscribe receives the broadcast for the allowed meeting', got > 0, `${got} health frame(s)`);
    } finally {
      await desk.close();
    }
  }

  if (failed) { console.error(`\n❌ access (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ access (L3): every read path (/transcripts · /recordings · …/player · /bots · /ws subscribe) consults canAccess — deny ⇒ 403/empty/no-broadcast, default ownerOnly ⇒ normal (P20/ADR-0012 seam).');
}
main().catch((e) => { console.error(e); process.exit(1); });
