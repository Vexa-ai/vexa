/**
 * The bot's in-meeting act handler — routes the acts.v1 actions the bot performs IN the meeting
 * (beside `leave`, which the orchestrator owns) to the controller that performs them:
 *   • speak / speak_stop → the SpeakController (gated inside it on voiceAgentEnabled)
 *   • chat_send          → the ChatSender (ungated — a text act, not a voice one). When the act
 *                          carries an `id`, its outcome goes back to meeting-api through the injected
 *                          `report` (a transcript.v1 `ChatSendResult` on the signed stream): `posted`,
 *                          or not with the sender's `ChatSendFailure` reason. Without an id nothing is
 *                          reported.
 * Every other action is ignored here. Pure: the controllers are injected, so the routing is
 * provable without a browser.
 */
import type { Act } from './contracts.js';
import type { ChatSendResult } from './capture-bridge.js';
import type { ChatSendReport } from './ports.js';

export interface ActControllers {
  speak: { speak(text: string, voice?: string): Promise<void>; stop(): Promise<void> };
  chat: { send(text: string): Promise<ChatSendResult> };
  /** Where a chat send that carried an `id` reports its outcome. */
  report?: (r: ChatSendReport) => Promise<void>;
}

export function createActDispatch(c: ActControllers): (act: Act) => Promise<void> {
  const report = async (r: ChatSendReport): Promise<void> => {
    if (!c.report) return;
    // Best-effort: a lost report reads as `unconfirmed` on meeting-api's side, never as a failure here.
    await c.report(r).catch((e) => console.error(`[bot] chat_send_result not reported: ${String(e)}`));
  };
  return async (act) => {
    if (act.action === 'speak') await c.speak.speak(act.text, act.voice);
    else if (act.action === 'speak_stop') await c.speak.stop();
    else if (act.action === 'chat_send') {
      const id = typeof act.id === 'string' && act.id ? act.id : undefined;
      if (typeof act.text !== 'string' || !act.text.trim()) {
        console.error('[bot] chat_send ignored: empty text');
        if (id) await report({ id, posted: false, reason: 'empty_text' });
        return;
      }
      const r = await c.chat.send(act.text);
      if (id) await report(r.sent ? { id, posted: true } : { id, posted: false, reason: r.reason });
    }
  };
}
