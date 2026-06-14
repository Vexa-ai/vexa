#!/usr/bin/env node
// gate:dashboard-contract — pins the desktop gateway to the dashboard's gateway
// surface so it can't silently drift.
//
// ⚠ INTERIM. The durable fix is the MVP4 "n" deliverable (MANIFEST.md): the
// api-gateway / meeting-api Pydantic schemas (already annotated with
// response_model=) extracted to a published, versioned OpenAPI artifact under
// contracts/api/v1/, with the dashboard types GENERATED from it and the desktop
// gateway RESPONSE-VALIDATED against it. When that lands, delete this file — a
// hand-maintained endpoint list is itself a thing that can drift from the client.
//
// WHY THIS EXISTS: every other deploy runs the real api-gateway (+ meeting-api),
// which IS the contract the dashboard was built against. Desktop replaces both
// with one hand-written Node gateway (modules/pipeline/scripts/desktop.ts). With
// nothing binding the two, the dashboard discovers missing/!-shaped endpoints one
// click at a time. This manifest is the SSOT for what desktop must serve and how;
// the probe asserts the running gateway matches it.
//
// The endpoint list below is EXACTLY what services/dashboard/src/lib/api.ts calls
// (paths after the /api/vexa proxy prefix). When the dashboard adds a call, add it
// here with a disposition — the gate then forces a deliberate desktop decision.
//
// Usage: node dashboard-contract.mjs            (probes $GATEWAY, default :8056)
//        GATEWAY=http://localhost:8056 node dashboard-contract.mjs
// Exit 0 = conformant; exit 1 = drift.

const GATEWAY = process.env.GATEWAY || 'http://localhost:8056';

// Disposition codes:
//   served      — desktop returns real/empty data the dashboard renders (expect 200)
//   no-op       — control action meaningless for single-user desktop; succeeds (200)
//   unsupported — no local backend for this feature; honest 501 (dashboard degrades)
//   graceful404 — 404 is the dashboard's OWN designed path (e.g. recording not ready)
const SURFACE = [
  // path template, method, disposition, note
  ['/bots',                              'GET',    'served',      'meetings list (dashboard reads .meetings)'],
  ['/bots/status',                       'GET',    'served',      'running bots = active local meetings'],
  ['/bots/{platform}/{fakeNative}',      'DELETE', 'no-op',       'stop bot ⇒ end local session (fake id ⇒ no side effect here)'],
  ['/bots/{platform}/{native}/config',   'PUT',    'no-op',       'no live bot to reconfigure'],
  ['/bots/{platform}/{native}/chat',     'GET',    'served',      'chat (empty: not persisted to lite-db)'],
  ['/meetings/{id}',                     'GET',    'served',      'meeting detail (getMeeting)'],
  ['/meetings/{platform}/{native}',      'PATCH',  'no-op',       'data edits not persisted; echoes meeting'],
  ['/meetings/{id}/transcribe',          'POST',   'no-op',       'desktop transcribes live; reports counts'],
  ['/transcripts/{platform}/{native}',   'GET',    'served',      'meeting envelope + segments + recordings'],
  ['/transcripts/{platform}/{native}/share', 'POST', 'unsupported','no share service in desktop'],
  ['/recordings/{id}/master',            'GET',    'graceful404', 'dashboard treats 404 as "recording not ready"'],
];

const EXPECT = { served: [200], 'no-op': [200], unsupported: [501], graceful404: [404] };

function fill(tpl, ctx) {
  return tpl
    .replace('{platform}', ctx.platform).replace('{native}', ctx.native)
    .replace('{fakeNative}', '__contract_probe_no_such_meeting__')
    .replace('{id}', String(ctx.id));
}

async function main() {
  // Discover a real meeting to probe against.
  let ctx = { platform: 'google_meet', native: '__none__', id: 0 };
  try {
    const r = await fetch(`${GATEWAY}/bots`);
    const j = await r.json();
    const m = (j.meetings || [])[0];
    if (m) ctx = { platform: m.platform, native: m.native_meeting_id, id: m.id };
  } catch (e) {
    console.error(`❌ gateway unreachable at ${GATEWAY} — start the desktop stack first (${e.message})`);
    process.exit(1);
  }
  if (ctx.native === '__none__') console.log('⚠ no meetings in the store — path-shape probe only (serve a meeting for full coverage)\n');

  const rows = [];
  let failed = 0;
  for (const [tpl, method, disp, note] of SURFACE) {
    const path = fill(tpl, ctx);
    let code = 0;
    try { code = (await fetch(`${GATEWAY}${path}`, { method })).status; } catch { code = 0; }
    const ok = EXPECT[disp].includes(code);
    if (!ok) failed++;
    rows.push({ ok, method, tpl, disp, code, note });
  }

  const W = Math.max(...SURFACE.map(([t]) => t.length));
  for (const r of rows) {
    console.log(`  ${r.ok ? '✅' : '❌'} ${r.method.padEnd(6)} ${r.tpl.padEnd(W)}  ${r.disp.padEnd(11)} → ${r.code}${r.ok ? '' : `  EXPECTED ${EXPECT[r.disp].join('|')}`}`);
  }
  if (failed) {
    console.error(`\n❌ DASHBOARD CONTRACT DRIFT — ${failed} endpoint(s) off-contract. Update desktop.ts or this manifest.`);
    process.exit(1);
  }
  console.log(`\n✅ DASHBOARD CONTRACT OK — desktop gateway serves all ${SURFACE.length} dashboard endpoints as declared.`);
}
main();
