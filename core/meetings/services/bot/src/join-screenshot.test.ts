/**
 * The join-failure screenshot (join-screenshot.ts) and the attempt evidence (node + screenshot_key)
 * the orchestrator puts on a failed join's terminal event.
 *
 * Founder, 1 Nov 2026: he asked to see the screen a prod bot was refused on and nobody could show
 * it — the checkpoints never left the pod. A refused join must leave exactly one retrievable picture,
 * and a failed upload must never change how the attempt ended.
 * Run: npx tsx src/join-screenshot.test.ts
 */
import http from 'node:http';
import { mkdtempSync, writeFileSync, utimesSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createOrchestrator } from './orchestrator.js';
import type { JoinDriver, JoinOutcome, LifecycleSink } from './ports.js';
import type { LifecycleEvent } from './contracts.js';
import type { Invocation } from './config.js';
import { noopAloneness, noopPipeline, noopActs } from './test-doubles.js';
import {
  screenshotUploadUrl, uploadJoinFailureScreenshot, MAX_SCREENSHOT_BYTES,
} from './join-screenshot.js';

let failed = 0;
const check = (name: string, cond: boolean, detail = '') => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond ? '' : '  — ' + detail}`);
  if (!cond) failed++;
};

const PNG = Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), Buffer.from('fake-png-body')]);
const quiet = () => {};

const inv = (over: Partial<Invocation> = {}): Invocation => ({
  platform: 'google_meet', meetingUrl: 'https://meet.google.com/abc-defg-hij', botName: 'B',
  redisUrl: 'redis://r:6379', connectionId: 'sess-uid', container_name: 'mtg-abc123-bot',
  nativeMeetingId: 'abc-defg-hij', token: 'meeting-token', meeting_id: 7,
  ...over,
});
const sink = (): LifecycleSink & { readonly events: LifecycleEvent[] } => {
  const events: LifecycleEvent[] = [];
  return { events, async emit(e: LifecycleEvent) { events.push(e); } };
};
const mockJoin = (outcome: JoinOutcome | 'throw'): JoinDriver => ({
  async join(report) {
    await report('awaiting_admission');
    if (outcome === 'throw') throw new Error('Target page, context or browser has been closed');
    if (outcome === 'admitted') await report('active');
    return outcome === 'rejected'
      ? { outcome, reason: 'google_meet_join_refused: Google Meet refused the join on 3 of 3 knocks (CreateMeetingDevice answered HTTP 403)' }
      : outcome;
  },
  onRemoval() { return () => {}; },
  async leave() {},
  async withdraw() {},
});
const last = (e: LifecycleEvent[]) => e[e.length - 1];

async function main(): Promise<void> {
  console.log('\n=== orchestrator: attempt evidence on a failed join ===');
  {
    const lc = sink();
    let calls = 0;
    const res = await createOrchestrator(inv(), {
      lifecycle: lc, join: mockJoin('rejected'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness(),
      nodeName: 'lke590708-865130-a',
      failureScreenshot: async () => { calls++; return 'join-evidence/7/sess-uid.png'; },
    }).run();
    const ev = last(lc.events);
    check('refused join → failed, awaiting_admission_rejected (outcome unchanged)',
      res.status === 'failed' && ev.completion_reason === 'awaiting_admission_rejected', JSON.stringify(ev));
    check('… exactly one screenshot upload', calls === 1, `calls=${calls}`);
    check('… evidence names the node', ev.join_evidence?.node === 'lke590708-865130-a', JSON.stringify(ev.join_evidence));
    check('… evidence names the stored screenshot', ev.join_evidence?.screenshot_key === 'join-evidence/7/sess-uid.png', JSON.stringify(ev.join_evidence));
  }
  {
    const lc = sink();
    const res = await createOrchestrator(inv(), {
      lifecycle: lc, join: mockJoin('rejected'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness(),
      nodeName: 'node-b',
      failureScreenshot: async () => { throw new Error('upload exploded'); },
    }).run();
    const ev = last(lc.events);
    check('upload failure → the refusal is still reported, as before',
      res.status === 'failed' && ev.status === 'failed' && ev.completion_reason === 'awaiting_admission_rejected', JSON.stringify(ev));
    check('… with the node and no screenshot_key', ev.join_evidence?.node === 'node-b' && ev.join_evidence?.screenshot_key === undefined, JSON.stringify(ev.join_evidence));
  }
  {
    const lc = sink();
    let calls = 0;
    const res = await createOrchestrator(inv(), {
      lifecycle: lc, join: mockJoin('throw'), pipeline: noopPipeline(), acts: noopActs(), aloneness: noopAloneness(),
      failureScreenshot: async () => { calls++; return 'join-evidence/7/sess-uid.png'; },
    }).run();
    check('join throws → one screenshot, key on the join_failure evidence',
      res.completionReason === 'join_failure' && calls === 1 && last(lc.events).join_evidence?.screenshot_key === 'join-evidence/7/sess-uid.png',
      JSON.stringify(last(lc.events)));
    check('… no node when the runtime gave none', last(lc.events).join_evidence?.node === undefined);
  }
  {
    let calls = 0;
    const o = createOrchestrator(inv(), {
      lifecycle: sink(), join: mockJoin('admitted'), pipeline: noopPipeline(), acts: noopActs((f) => { setTimeout(() => f({ action: 'leave' }), 5); }),
      aloneness: noopAloneness(),
      failureScreenshot: async () => { calls++; return 'x'; },
    });
    const res = await o.run();
    check('admitted run → no screenshot upload', res.status === 'completed' && calls === 0, `status=${res.status} calls=${calls}`);
  }

  console.log('\n=== upload URL ===');
  check('derived from recordingUploadUrl, same host',
    screenshotUploadUrl(inv({ recordingUploadUrl: 'http://meeting-api:8080/internal/recordings/upload' })) === 'http://meeting-api:8080/internal/join-evidence/screenshot');
  check('none without recordingUploadUrl', screenshotUploadUrl(inv()) === undefined);
  check('none for an unexpected path (never guess)', screenshotUploadUrl(inv({ recordingUploadUrl: 'http://x/other' })) === undefined);

  console.log('\n=== uploader, against a local server ===');
  const requests: Array<{ path: string; auth: string; body: Buffer }> = [];
  let mode: 'ok' | 'hang' | 'error' = 'ok';
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      requests.push({ path: req.url ?? '', auth: String(req.headers.authorization ?? ''), body: Buffer.concat(chunks) });
      if (mode === 'hang') return;
      if (mode === 'error') { res.writeHead(500); res.end('boom'); return; }
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ status: 'stored', key: 'join-evidence/7/sess-uid.png' }));
    });
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  const port = (server.address() as { port: number }).port;
  const live = inv({ recordingUploadUrl: `http://127.0.0.1:${port}/internal/recordings/upload` });
  try {
    const key = await uploadJoinFailureScreenshot({ inv: live, capture: async () => PNG, log: quiet });
    const r = requests[0];
    check('one POST to /internal/join-evidence/screenshot', requests.length === 1 && r?.path === '/internal/join-evidence/screenshot', JSON.stringify(requests.map((q) => q.path)));
    check('… with the session MeetingToken', r?.auth === 'Bearer meeting-token', r?.auth);
    check('… carrying session_uid and the PNG', !!r && r.body.includes(Buffer.from('sess-uid')) && r.body.includes(PNG));
    check('… resolves with the stored key', key === 'join-evidence/7/sess-uid.png', String(key));

    requests.length = 0;
    const big = Buffer.concat([PNG, Buffer.alloc(MAX_SCREENSHOT_BYTES)]);
    check('oversize picture → skipped, nothing sent',
      (await uploadJoinFailureScreenshot({ inv: live, capture: async () => big, log: quiet })) === undefined && requests.length === 0);

    const dir = mkdtempSync(join(tmpdir(), 'shots-'));
    writeFileSync(join(dir, 'bot-checkpoint-0-after-navigation.png'), Buffer.concat([PNG, Buffer.from('old')]));
    writeFileSync(join(dir, 'bot-checkpoint-2-refused.png'), Buffer.concat([PNG, Buffer.from('newest')]));
    utimesSync(join(dir, 'bot-checkpoint-0-after-navigation.png'), new Date(1_000_000), new Date(1_000_000));
    requests.length = 0;
    await uploadJoinFailureScreenshot({ inv: live, capture: async () => undefined, dir, log: quiet });
    check('no live page → the newest checkpoint file is sent',
      requests.length === 1 && requests[0].body.includes(Buffer.from('newest')) && !requests[0].body.includes(Buffer.from('old')));

    mode = 'error';
    check('HTTP error → undefined, never a throw',
      (await uploadJoinFailureScreenshot({ inv: live, capture: async () => PNG, log: quiet })) === undefined);

    mode = 'hang';
    const t0 = Date.now();
    const hung = await uploadJoinFailureScreenshot({ inv: live, capture: async () => PNG, timeoutMs: 300, log: quiet });
    check('a hung server is cut at the bound', hung === undefined && Date.now() - t0 < 1_500, `${Date.now() - t0} ms`);

    check('no upload URL → undefined, no capture needed',
      (await uploadJoinFailureScreenshot({ inv: inv(), capture: async () => PNG, log: quiet })) === undefined);
  } finally {
    server.closeAllConnections?.();
    server.close();
  }

  console.log(failed === 0 ? '\n✅ join-screenshot: all green' : `\n❌ join-screenshot: ${failed} failed`);
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((e) => { console.error('❌ FAIL —', e); process.exit(1); });
