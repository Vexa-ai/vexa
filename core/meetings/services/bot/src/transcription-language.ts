/**
 * Transcription language — the bot's live language setting and the STT call that honours it.
 *
 * The setting is `{language, allowedLanguages}` (transcription-language.v1). It names one of three
 * modes:
 *   • auto        — no language, empty list: the STT call carries no language.
 *   • forced      — language set, empty list: every STT call is pinned to `language`.
 *   • restricted  — non-empty list. One entry pins every call to it. Several entries: the call goes
 *                   out unpinned, and a detection outside the list is re-run on the same audio pinned
 *                   to the fallback (`language` when set — always a list member — else the first
 *                   entry). Every fallback is counted and logged.
 *
 * The setting is LIVE: invocation.v1 seeds it, and an acts.v1 `reconfigure` replaces each field it
 * carries. The next STT call reads the new setting; a call already in flight finishes with the one
 * it started with. A malformed reconfigure is refused loudly and changes nothing.
 *
 * Pure: no browser, no redis, no network. The STT round-trip is injected.
 */
import type { TranscriptionResult } from '@vexa/transcribe-whisper';
import type { Act } from './contracts.js';

/** A language code as transcription-language.v1 states it. */
export const LANGUAGE_CODE = /^[a-z]{2,3}$/;

export interface LanguageSetting {
  readonly language: string | null;
  readonly allowedLanguages: readonly string[];
}

export type LanguageMode = 'auto' | 'forced' | 'restricted';

export function modeOf(s: LanguageSetting): LanguageMode {
  if (s.allowedLanguages.length > 0) return 'restricted';
  return s.language ? 'forced' : 'auto';
}

/** Why a setting is off-contract, or null when it is valid. */
export function settingProblem(s: { language?: unknown; allowedLanguages?: unknown }): string | null {
  const { language, allowedLanguages } = s;
  if (language !== undefined && language !== null) {
    if (typeof language !== 'string' || !LANGUAGE_CODE.test(language)) {
      return `language ${JSON.stringify(language)} is not a language code (${LANGUAGE_CODE.source})`;
    }
  }
  if (allowedLanguages !== undefined) {
    if (!Array.isArray(allowedLanguages)) return `allowedLanguages ${JSON.stringify(allowedLanguages)} is not an array`;
    for (const code of allowedLanguages) {
      if (typeof code !== 'string' || !LANGUAGE_CODE.test(code)) {
        return `allowedLanguages entry ${JSON.stringify(code)} is not a language code (${LANGUAGE_CODE.source})`;
      }
    }
    if (typeof language === 'string' && allowedLanguages.length > 0 && !allowedLanguages.includes(language)) {
      return `language "${language}" is not in allowedLanguages [${allowedLanguages.join(',')}]`;
    }
  }
  return null;
}

/** One human-readable line for a setting: `auto`, `forced de`, `restricted [de,en] fallback de`. */
export function summarize(s: LanguageSetting): string {
  switch (modeOf(s)) {
    case 'auto': return 'auto';
    case 'forced': return `forced ${s.language}`;
    case 'restricted': return `restricted [${s.allowedLanguages.join(',')}] fallback ${fallbackLanguage(s)}`;
  }
}

/** The language every STT call is pinned to up front: forced, or a one-entry list. */
export function pinnedLanguage(s: LanguageSetting): string | undefined {
  if (s.allowedLanguages.length === 1) return s.allowedLanguages[0];
  if (s.allowedLanguages.length === 0 && s.language) return s.language;
  return undefined;
}

/** The language an out-of-list detection is re-run with (restricted mode only). */
export function fallbackLanguage(s: LanguageSetting): string | undefined {
  if (s.allowedLanguages.length === 0) return undefined;
  return s.language ?? s.allowedLanguages[0];
}

export class LanguageSettingError extends Error {
  constructor(message: string) { super(message); this.name = 'LanguageSettingError'; }
}

export type ReconfigureOutcome =
  | { outcome: 'applied'; previous: LanguageSetting; current: LanguageSetting }
  | { outcome: 'refused'; reason: string }
  /** The act carried neither language field — the setting is untouched. */
  | { outcome: 'untouched' };

export interface LanguageControl {
  get(): LanguageSetting;
  /** Apply a `reconfigure` act: present keys replace, absent keys keep, malformed acts are refused. */
  apply(act: Extract<Act, { action: 'reconfigure' }>): ReconfigureOutcome;
}

/** The live setting, seeded from invocation.v1. An off-contract seed throws LanguageSettingError. */
export function createLanguageControl(initial: { language?: string | null; allowedLanguages?: readonly string[] }): LanguageControl {
  const seedProblem = settingProblem(initial);
  if (seedProblem) throw new LanguageSettingError(`transcription language: ${seedProblem}`);
  let current: LanguageSetting = freeze(initial.language ?? null, initial.allowedLanguages ?? []);
  return {
    get: () => current,
    apply(act) {
      const hasLanguage = Object.prototype.hasOwnProperty.call(act, 'language');
      const hasList = Object.prototype.hasOwnProperty.call(act, 'allowedLanguages');
      if (!hasLanguage && !hasList) return { outcome: 'untouched' };
      const language: unknown = hasLanguage ? act.language : current.language;
      const allowedLanguages: unknown = hasList ? act.allowedLanguages : current.allowedLanguages;
      if (hasLanguage && language !== null && typeof language !== 'string') {
        return { outcome: 'refused', reason: `language ${JSON.stringify(language)} is neither a code nor null` };
      }
      const problem = settingProblem({ language, allowedLanguages });
      if (problem) return { outcome: 'refused', reason: problem };
      const previous = current;
      current = freeze(language as string | null, allowedLanguages as readonly string[]);
      return { outcome: 'applied', previous, current };
    },
  };
}

function freeze(language: string | null, allowedLanguages: readonly string[]): LanguageSetting {
  return Object.freeze({ language, allowedLanguages: Object.freeze([...allowedLanguages]) });
}

/** What a recorder learns about each setting the bot runs with (the capture-signal observation). */
export interface LanguageObservation {
  language: string | null;
  allowedLanguages: string[];
  cause: 'invocation' | 'reconfigure';
}

/**
 * The acts.v1 handler for `reconfigure`: applies the act to the control and reports every outcome —
 * an applied change as one log line plus an observation, a refusal as a console.error naming why.
 * Every other act is not this handler's concern.
 */
export function languageActHandler(
  control: LanguageControl,
  deps: {
    log?: (line: string) => void;
    error?: (line: string) => void;
    observe?: (obs: LanguageObservation) => void;
  } = {},
): (act: Act) => void {
  const log = deps.log ?? ((line) => console.log(line));
  const error = deps.error ?? ((line) => console.error(line));
  return (act) => {
    if (act.action !== 'reconfigure') return;
    if (act.task !== undefined && act.task !== null && act.task !== 'transcribe') {
      error(`[bot] transcription language: reconfigure task "${String(act.task)}" is not supported by this bot (transcribe only); task ignored`);
    }
    const result = control.apply(act);
    if (result.outcome === 'refused') {
      error(`[bot] transcription language: reconfigure REFUSED — ${result.reason}; setting stays ${summarize(control.get())}`);
      return;
    }
    if (result.outcome === 'untouched') return;
    log(`[bot] transcription language: ${summarize(result.previous)} → ${summarize(result.current)} (reconfigure)`);
    deps.observe?.(observationOf(result.current, 'reconfigure'));
  };
}

export function observationOf(s: LanguageSetting, cause: LanguageObservation['cause']): LanguageObservation {
  return { language: s.language, allowedLanguages: [...s.allowedLanguages], cause };
}

/** OpenAI's own endpoint reports the detected language by English name ("german"); faster-whisper
 *  and most compatible servers report the code. Both read as the code, so a restricted bot on an
 *  OpenAI endpoint does not re-run every window. */
const LANGUAGE_NAMES: Readonly<Record<string, string>> = {
  english: 'en', german: 'de', french: 'fr', spanish: 'es', italian: 'it', dutch: 'nl',
  portuguese: 'pt', polish: 'pl', czech: 'cs', swedish: 'sv', danish: 'da', norwegian: 'no',
  finnish: 'fi', russian: 'ru', ukrainian: 'uk', turkish: 'tr', arabic: 'ar', hebrew: 'he',
  hindi: 'hi', japanese: 'ja', korean: 'ko', chinese: 'zh', greek: 'el', hungarian: 'hu',
  romanian: 'ro', slovak: 'sk', slovenian: 'sl', croatian: 'hr', serbian: 'sr', bulgarian: 'bg',
};

/** The detected language as a code, whichever form the backend reported it in. */
export function detectedCode(language: unknown): string {
  const raw = typeof language === 'string' ? language.trim().toLowerCase() : '';
  return LANGUAGE_NAMES[raw] ?? raw;
}

/** The STT round-trip as the transcription client exposes it. */
export type SttCall = (pcm: Float32Array, language?: string, prompt?: string) => Promise<TranscriptionResult>;

export interface LanguageAwareTranscribe {
  (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult>;
  /** How many calls were re-run pinned because the detection fell outside the list. */
  readonly fallbacks: number;
}

/**
 * The lane-facing transcribe `(pcm, prompt)` over an STT call, honouring the control's setting as it
 * stands when each call starts.
 */
export function languageAwareTranscribe(
  stt: SttCall,
  control: LanguageControl,
  log: (line: string) => void = (line) => console.log(line),
): LanguageAwareTranscribe {
  let fallbacks = 0;
  const transcribe = async (pcm: Float32Array, prompt?: string): Promise<TranscriptionResult> => {
    const setting = control.get();
    const pinned = pinnedLanguage(setting);
    if (pinned || modeOf(setting) === 'auto') return stt(pcm, pinned, prompt);
    const result = await stt(pcm, undefined, prompt);
    const detected = detectedCode(result.language);
    if (setting.allowedLanguages.includes(detected)) return result;
    const fallback = fallbackLanguage(setting)!;
    fallbacks++;
    log(`[bot] transcription language: detected ${detected ? `"${detected}"` : 'nothing'} outside [${setting.allowedLanguages.join(',')}] → re-run pinned to ${fallback} (fallback #${fallbacks})`);
    return stt(pcm, fallback, prompt);
  };
  Object.defineProperty(transcribe, 'fallbacks', { get: () => fallbacks, enumerable: true });
  return transcribe as LanguageAwareTranscribe;
}
