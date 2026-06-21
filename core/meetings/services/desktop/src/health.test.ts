/**
 * L3 — desktop /health + the server-side no-frames liveness watchdog (P18).
 *
 * Proves two observable things the desktop previously kept silent:
 *  • GET /health reports the STT dependency's state (200 configured / 503 degraded) — a
 *    desktop with no STT silently never transcribes; this makes it probe-able.
 *  • A connected session receiving NO audio surfaces a 'no-signal' health fault on /ws —
 *    the receiver's own "active but silent", observable instead of invisible.
 * Run: npx tsx src/health.test.ts
 */
import { WebSocket } from 'ws';
import { startDesktop } from './desktop.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = ''): void => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function main(): Promise<void> {
  // ── /health: no STT configured → 503 (degraded) ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true });   // NO txUrl
    const res = await fetch(`http://localhost:${desk.gatewayPort}/health`);
    const body = await res.json() as any;
    check('no-STT: /health → 503 (degraded)', res.status === 503, String(res.status));
    check('no-STT: ok=false', body.ok === false, JSON.stringify(body));
    check('no-STT: checks.stt = unconfigured', body.checks?.stt === 'unconfigured', JSON.stringify(body.checks));
    await desk.close();
  }

  // ── /health: STT configured → 200 + the dependency surfaced ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test' });
    const res = await fetch(`http://localhost:${desk.gatewayPort}/health`);
    const body = await res.json() as any;
    check('STT: /health → 200', res.status === 200, String(res.status));
    check('STT: ok=true', body.ok === true);
    check('STT: checks.stt = configured', body.checks?.stt === 'configured', JSON.stringify(body.checks));
    check('STT: stt_url surfaced', body.checks?.stt_url === 'http://stt.test', body.checks?.stt_url);
    check('STT: live_sessions present', typeof body.live_sessions === 'number');
    await desk.close();
  }

  // ── watchdog: a connected session with NO audio frames → 'no-signal' fault on /ws ──
  {
    const desk = await startDesktop({ ingestPort: 0, gatewayPort: 0, quiet: true, txUrl: 'http://stt.test', noSignalMs: 200 });
    const platform = 'google_meet', native = 'no-signal-test';   // gmeet → the light pipeline (no pyannote)
    const key = `${platform}/${native}`;
    const live = new WebSocket(`ws://localhost:${desk.gatewayPort}/ws`);
    const health: any[] = [];
    await new Promise<void>((r) => live.on('open', () => r()));
    live.on('message', (d) => { try { const m = JSON.parse(d.toString()); if (m.type === 'health') health.push(m); } catch { /* */ } });
    live.send(JSON.stringify({ action: 'subscribe', meetings: [{ platform, native_meeting_id: native }] }));
    await sleep(50);
    // an ingest session that connects but never sends an audio frame
    const ingest = new WebSocket(`ws://localhost:${desk.ingestPort}/ingest?platform=${platform}&native_meeting_id=${native}`);
    await new Promise<void>((r) => ingest.on('open', () => r()));
    for (let i = 0; i < 30 && !health.some((h) => h.kind === 'no-signal'); i++) await sleep(100);
    const ns = health.find((h) => h.kind === 'no-signal');
    check('watchdog: a no-signal health frame is surfaced on /ws', !!ns, JSON.stringify(health));
    check('watchdog: attributed to capture + the meeting', ns?.source === 'capture' && ns?.meeting === key, JSON.stringify(ns));
    ingest.close(); live.close();
    await desk.close();
  }

  if (failed) { console.error(`\n❌ health (L3): ${failed} check(s) FAILED.`); process.exit(1); }
  console.log('\n✅ health (L3): /health reports the STT dependency; a silent session surfaces a no-signal fault (P18 server-side liveness).');
}
main().catch((e) => { console.error(e); process.exit(1); });
