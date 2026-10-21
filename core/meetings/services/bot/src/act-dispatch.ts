/**
 * The bot's in-meeting act handler — routes the acts.v1 actions the bot performs IN the meeting
 * (beside `leave`, which the orchestrator owns) to the controller that performs them:
 *   • speak / speak_stop → the SpeakController (gated inside it on voiceAgentEnabled)
 *   • chat_send          → the ChatSender (ungated — a text act, not a voice one)
 * Every other action is ignored here. Pure: the controllers are injected, so the routing is
 * provable without a browser.
 */
import type { Act } from './contracts.js';

export interface ActControllers {
  speak: { speak(text: string, voice?: string): Promise<void>; stop(): Promise<void> };
  chat: { send(text: string): Promise<boolean> };
}

export function createActDispatch(c: ActControllers): (act: Act) => Promise<void> {
  return async (act) => {
    if (act.action === 'speak') await c.speak.speak(act.text, act.voice);
    else if (act.action === 'speak_stop') await c.speak.stop();
    else if (act.action === 'chat_send') {
      if (typeof act.text !== 'string' || !act.text.trim()) {
        console.error('[bot] chat_send ignored: empty text');
        return;
      }
      await c.chat.send(act.text);
    }
  };
}
