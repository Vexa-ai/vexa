/**
 * In-meeting act routing (act-dispatch.ts) — L2, fake controllers.
 *
 * Pins which controller each acts.v1 action reaches: chat_send → the ChatSender with the act's
 * text (and NOT the SpeakController); speak / speak_stop → the SpeakController (and NOT the
 * ChatSender); an empty chat_send and the actions the bot does not perform here reach neither.
 * Run: npx tsx src/act-dispatch.test.ts
 */
import { createActDispatch } from './act-dispatch.js';
import type { Act } from './contracts.js';

let failed = 0;
const check = (name: string, cond: boolean, detail?: string) => {
  console.log(`  ${cond ? '✅' : '❌'} ${name}${cond || !detail ? '' : ` — ${detail}`}`);
  if (!cond) failed++;
};

const calls: string[] = [];
const dispatch = createActDispatch({
  speak: {
    async speak(text, voice, id) { calls.push(`speak:${text}:${voice ?? ''}${id ? `:${id}` : ''}`); },
    async stop() { calls.push('speak_stop'); },
  },
  chat: { async send(text) { calls.push(`chat:${text}`); return { sent: true, via: 'test' }; } },
});
const run = async (act: Act) => { calls.length = 0; await dispatch(act); return [...calls]; };

{
  const got = await run({ action: 'chat_send', text: 'hello from the agent' });
  check('chat_send routes to the ChatSender with the text', JSON.stringify(got) === JSON.stringify(['chat:hello from the agent']), JSON.stringify(got));
}
{
  const got = await run({ action: 'speak', text: 'say this', voice: 'alloy' });
  check('speak still routes to the SpeakController', JSON.stringify(got) === JSON.stringify(['speak:say this:alloy']), JSON.stringify(got));
}
{
  // #1866: the act's id (meeting-api's request_id) reaches the SpeakController, so its log lines carry it.
  const got = await run({ action: 'speak', text: 'say this', id: '3f2a9c0d1b2e4f5a' });
  check('speak passes the act id to the SpeakController', JSON.stringify(got) === JSON.stringify(['speak:say this::3f2a9c0d1b2e4f5a']), JSON.stringify(got));
}
{
  const got = await run({ action: 'speak_stop' });
  check('speak_stop still routes to the SpeakController', JSON.stringify(got) === JSON.stringify(['speak_stop']), JSON.stringify(got));
}
{
  const got = await run({ action: 'chat_send', text: '   ' });
  check('an empty chat_send reaches no controller', got.length === 0, JSON.stringify(got));
}
for (const act of [{ action: 'leave' }, { action: 'chat_read' }, { action: 'screen_stop' }] as Act[]) {
  const got = await run(act);
  check(`${act.action} is not performed by the in-meeting handler`, got.length === 0, JSON.stringify(got));
}

// ── the chat-send outcome goes back to meeting-api, once, only when the act carried an id ──
{
  const reports: unknown[] = [];
  let outcome: { sent: true; via: string } | { sent: false; reason: 'not_landed' } = { sent: true, via: 'test' };
  const sent: string[] = [];
  const withReport = createActDispatch({
    speak: { async speak() {}, async stop() {} },
    chat: { async send(text) { sent.push(text); return outcome; } },
    report: async (r) => { reports.push(r); },
  });
  const id = '0f1e2d3c4b5a69788796a5b4c3d2e1f0';

  await withReport({ action: 'chat_send', text: 'posted line', id });
  check('a chat_send with an id reports exactly one posted result',
    JSON.stringify(reports) === JSON.stringify([{ id, posted: true }]), JSON.stringify(reports));

  reports.length = 0; outcome = { sent: false, reason: 'not_landed' };
  await withReport({ action: 'chat_send', text: 'lost line', id });
  check('a failed send reports posted:false with the sender\'s reason',
    JSON.stringify(reports) === JSON.stringify([{ id, posted: false, reason: 'not_landed' }]), JSON.stringify(reports));

  reports.length = 0; outcome = { sent: true, via: 'test' };
  await withReport({ action: 'chat_send', text: 'no id here' });
  check('a chat_send without an id reports nothing (but is still sent)',
    reports.length === 0 && sent[sent.length - 1] === 'no id here', JSON.stringify({ reports, sent }));

  reports.length = 0; sent.length = 0;
  await withReport({ action: 'chat_send', text: '   ', id });
  check('an empty chat_send with an id is reported as empty_text and never sent',
    JSON.stringify(reports) === JSON.stringify([{ id, posted: false, reason: 'empty_text' }]) && sent.length === 0,
    JSON.stringify({ reports, sent }));

  reports.length = 0;
  await withReport({ action: 'speak', text: 'not chat' });
  check('a non-chat act reports nothing', reports.length === 0, JSON.stringify(reports));

  const failing = createActDispatch({
    speak: { async speak() {}, async stop() {} },
    chat: { async send() { return { sent: true, via: 'test' }; } },
    report: async () => { throw new Error('redis down'); },
  });
  let threw = false;
  try { await failing({ action: 'chat_send', text: 'still fine', id }); } catch { threw = true; }
  check('a report that fails never fails the act', !threw);
}

console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ act-dispatch: all checks passed');
process.exit(failed ? 1 : 0);
