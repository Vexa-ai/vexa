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
    async speak(text, voice) { calls.push(`speak:${text}:${voice ?? ''}`); },
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

console.log(failed ? `\n❌ ${failed} check(s) failed` : '\n✅ act-dispatch: all checks passed');
process.exit(failed ? 1 : 0);
