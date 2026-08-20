import type { Act } from './contracts.js';

export interface VoiceActController {
  speak(text: string, voice?: string): Promise<void>;
  speakAudio(audioBase64: string): Promise<void>;
  stop(): Promise<void>;
}

/** Route the voice subset of acts.v1. Other acts remain owned by their existing handlers. */
export function createVoiceActHandler(controller: VoiceActController): (act: Act) => Promise<void> {
  return async (act) => {
    if (act.action === 'speak') await controller.speak(act.text, act.voice);
    else if (act.action === 'speak_audio' && act.audioBase64) {
      await controller.speakAudio(act.audioBase64);
    } else if (act.action === 'speak_stop') await controller.stop();
  };
}
