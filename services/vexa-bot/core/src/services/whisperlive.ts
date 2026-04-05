import { BotConfig } from '../types';

/**
 * Stub WhisperLiveService — the original module was removed.
 * This provides a no-op implementation so Zoom Web recording compiles.
 * Zoom Web transcription via WhisperLive is non-functional until a real
 * implementation is provided; other transcription paths (per-speaker audio)
 * are unaffected.
 */
export class WhisperLiveService {
  private sessionUid: string = '';

  constructor(_opts?: { whisperLiveUrl?: string }) {}

  async initializeWithStubbornReconnection(_label: string): Promise<string> {
    console.log('[WhisperLive] Stub — initializeWithStubbornReconnection called (no-op)');
    return '';
  }

  async connectToWhisperLive(
    _cfg: BotConfig,
    _onMessage: (data: any) => void,
    _onError: (err: Event) => void,
    _onClose: (evt: CloseEvent) => void,
  ): Promise<void> {
    console.log('[WhisperLive] Stub — connectToWhisperLive called (no-op)');
  }

  sendSpeakerEvent(
    _type: string,
    _speakerName: string,
    _speakerId: string,
    _relativeMs: number,
    _cfg: BotConfig,
  ): boolean {
    return false;
  }

  sendAudioData(_data: Float32Array): void {}

  setServerReady(_ready: boolean): void {}

  closeSocketForReconfigure(): void {}

  getSessionUid(): string {
    return this.sessionUid;
  }
}
