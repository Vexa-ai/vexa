/**
 * The speak queue — turns acts.v1 `speak` / `speak_stop` into audible speech in the call (#1866 C3).
 *
 * Pure: the meeting-UI mic and the TTS player are injected ports, so every rule here is provable
 * without a browser or PulseAudio (speak-controller.test.ts). capture-bridge.ts supplies the real
 * ports — the page's mic control and tts-playback.ts — and nothing else.
 *
 * The rules:
 *   • One utterance plays at a time, first in first out. At most MAX_PENDING wait behind it; one more
 *     is dropped and logged, never played late. Acts arrive fire-and-forget (adapters/acts-redis), so
 *     without this two `speak`s would talk over each other.
 *   • `stop()` clears the queue, then stops the playback — stopping always works.
 *   • The meeting-UI mic is READ before it is clicked, never toggled blind. If it cannot be confirmed
 *     on, nothing plays (`mic_unverified`): the audio would not be heard anyway. After every utterance
 *     the bot confirms it is off again, and says when it cannot (`mic_off_unverified`; the PulseAudio
 *     source is muted at rest regardless).
 *   • Zoom: the join leaves the UI mic on for a voice bot (Zoom grants audio only then), so the bot
 *     leaves it as it is and relies on the PulseAudio mute; the line says so.
 *   • Every line names the utterance's id (the `request_id` meeting-api answered with); none carries
 *     the spoken text, which is meeting content.
 */

export type MicState = 'on' | 'off' | 'unknown';

/** The meeting-UI microphone control. */
export interface MicPort {
  read(): Promise<MicState>;
  /** Click the control once. The caller reads the state again to learn what the click did. */
  toggle(): Promise<void>;
}

export type TtsFailure = 'tts_not_configured' | `tts_http_${number}` | 'tts_unreachable' | 'stopped' | 'playback_failed';
export type TtsOutcome = { ok: true; ms: number } | { ok: false; reason: TtsFailure };

/** Synthesizes and plays one utterance into the bot's mic (tts-playback.ts). */
export interface TtsPort {
  play(text: string, voice?: string): Promise<TtsOutcome>;
  /** Kill any playback in flight and mute the PulseAudio chain. */
  stop(): void;
}

export interface SpeakController {
  speak(text: string, voice?: string, id?: string): Promise<void>;
  stop(): Promise<void>;
}

/** Utterances that may wait behind the one playing. */
export const MAX_PENDING = 3;

export interface SpeakQueueOptions {
  /** `inv.voiceAgentEnabled`: a bot spawned without it never speaks (the second lock behind meeting-api's 409). */
  enabled: boolean;
  platform: string;
  mic: MicPort;
  tts: TtsPort;
  log?: (line: string) => void;
  /** How long a clicked mic control gets to show its new state before it is read again. */
  settleMs?: number;
}

interface Utterance {
  text: string;
  voice?: string;
  id: string;
  done: () => void;
}

/** What a mic control's `aria-label` (or `aria-pressed`) says about the mic. The label names the
 *  ACTION the button performs: "Turn off microphone" / "Mute" is shown while the mic is on. */
export function micStateFromControl(label: string | null | undefined, pressed: string | null | undefined): MicState {
  const l = (label ?? '').toLowerCase();
  if (/\bunmute\b|turn on (the )?mic(rophone)?\b|turn on audio|start (my )?audio/.test(l)) return 'off';
  if (/\bmute\b|turn off (the )?mic(rophone)?\b|turn off audio/.test(l)) return 'on';
  void pressed; // `aria-pressed` means "muted" on some builds and "on" on others: never guessed from alone.
  return 'unknown';
}

let seq = 0;
const nextId = () => `local-${Date.now().toString(36)}-${(++seq).toString(36)}`;

export function createSpeakQueue(o: SpeakQueueOptions): SpeakController {
  const log = o.log ?? ((l: string) => console.log(l));
  const settle = () => new Promise<void>((r) => setTimeout(r, o.settleMs ?? 400));
  const leaveUiMic = o.platform === 'zoom';
  const pending: Utterance[] = [];
  let current: Utterance | null = null;
  // Bumped by stop(): an utterance started before the stop ends as `stopped`, whatever its playback said.
  let generation = 0;

  const ensureMic = async (want: 'on' | 'off'): Promise<boolean> => {
    let state = await o.mic.read().catch((): MicState => 'unknown');
    if (state === want) return true;
    if (state === 'unknown') return false;
    await o.mic.toggle().catch(() => { /* the read below decides */ });
    await settle();
    state = await o.mic.read().catch((): MicState => 'unknown');
    return state === want;
  };

  const micOff = async (id: string): Promise<void> => {
    if (leaveUiMic) return;
    log((await ensureMic('off')) ? `[bot] speak id=${id} mic=off` : `[bot] speak id=${id} mic_off_unverified`);
  };

  const run = async (u: Utterance): Promise<void> => {
    const gen = generation;
    log(`[bot] speak id=${u.id} started chars=${u.text.length}`);
    if (leaveUiMic) {
      log(`[bot] speak id=${u.id} mic=zoom_ui_left_on (Zoom grants a voice bot audio only with its mic on; the PulseAudio source is muted at rest)`);
    } else if (!(await ensureMic('on'))) {
      log(`[bot] speak id=${u.id} failed reason=mic_unverified`);
      await micOff(u.id);
      return;
    }
    if (gen !== generation) {
      log(`[bot] speak id=${u.id} failed reason=stopped`);
      await micOff(u.id);
      return;
    }
    const outcome = await o.tts.play(u.text, u.voice).catch((): TtsOutcome => ({ ok: false, reason: 'playback_failed' }));
    if (gen !== generation && outcome.ok) log(`[bot] speak id=${u.id} failed reason=stopped`);
    else if (outcome.ok) log(`[bot] speak id=${u.id} done ms=${outcome.ms}`);
    else log(`[bot] speak id=${u.id} failed reason=${outcome.reason}`);
    await micOff(u.id);
  };

  const drain = async (): Promise<void> => {
    while (pending.length) {
      const u = pending.shift()!;
      current = u;
      try { await run(u); } finally { current = null; u.done(); }
    }
  };

  return {
    speak(text: string, voice?: string, id?: string): Promise<void> {
      const uid = id && id.trim() ? id : nextId();
      if (!o.enabled) {
        log('[bot] speak ignored: voiceAgentEnabled is false');
        log(`[bot] speak id=${uid} failed reason=disabled`);
        return Promise.resolve();
      }
      const busy = current !== null || pending.length > 0;
      if (busy && pending.length >= MAX_PENDING) {
        log(`[bot] speak dropped id=${uid} reason=queue_full`);
        return Promise.resolve();
      }
      return new Promise<void>((done) => {
        pending.push({ text, voice: voice && voice !== 'auto' ? voice : undefined, id: uid, done });
        if (!busy) void drain();
      });
    },
    async stop(): Promise<void> {
      if (!o.enabled) return;
      generation++;
      const cleared = pending.splice(0);
      for (const u of cleared) { log(`[bot] speak id=${u.id} failed reason=stopped`); u.done(); }
      log(`[bot] speak_stop cleared=${cleared.length}`);
      o.tts.stop();
      // Nothing playing: still leave the mic off. Something playing: its own run does that.
      if (current === null) await micOff('none');
    },
  };
}
