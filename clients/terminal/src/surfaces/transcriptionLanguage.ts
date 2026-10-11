/** Transcription language — the ONE client model of "which language(s) does the bot transcribe in".
 *
 *  A setting is `{language, allowed_languages}` over Whisper codes (`^[a-z]{2,3}$`), in three modes:
 *    - auto-detect: neither field set;
 *    - one language: `language` set, list empty — every window is pinned to it;
 *    - a few languages: a list of two or more — detection is restricted to the list, and `language`
 *      (one of the list) is the fallback when detection lands outside it, else the list's first entry.
 *
 *  Three places speak it, all through this module so the mapping exists once:
 *    - Settings: the person's default (`GET/PUT /api/user/transcription`, "" and [] clear it);
 *    - the paste-a-link senders: a per-meeting override on `POST /api/bots` (`languageSpawnFields`) —
 *      "Default" sends nothing, so the server resolves person → deployment → auto;
 *    - the live meeting header: `PUT /api/bots/{platform}/{native}/config` (`setMeetingLanguage`),
 *      whose body replaces the running setting.
 *  The picker that edits a `LanguageChoice` lives in `TranscriptionLanguagePicker.tsx`. */
import { ApiError } from "./apiClient";

export const LANGUAGE_CODE = /^[a-z]{2,3}$/;

/** The curated list the pickers offer — the languages people actually hold meetings in here. */
export const LANGUAGE_CODES = [
  "de", "en", "fr", "es", "it", "nl", "pt", "pl", "cs", "sv", "da",
  "no", "fi", "ru", "uk", "tr", "ar", "he", "hi", "ja", "ko", "zh",
] as const;

let names: Intl.DisplayNames | null | undefined;
/** "de" → "German". Falls back to the code itself when the runtime has no display names for it. */
export function languageLabel(code: string): string {
  if (names === undefined) {
    try { names = new Intl.DisplayNames(["en"], { type: "language" }); } catch { names = null; }
  }
  try { return names?.of(code) || code; } catch { return code; }
}

/** The wire shape on settings, meeting rows and the live-switch body. */
export interface LanguageSetting { language: string | null; allowed_languages: string[] }
export type LanguageSource = "meeting" | "user" | "deployment" | "auto";
/** What a meeting row reports (`data.transcription_language`): the setting the bot is running with. */
export interface MeetingLanguage extends LanguageSetting { source?: LanguageSource }

/** Read a meeting row's `data.transcription_language`; anything malformed reads as not reported. */
export function readMeetingLanguage(raw: unknown): MeetingLanguage | undefined {
  if (!raw || typeof raw !== "object") return undefined;
  const r = raw as { language?: unknown; allowed_languages?: unknown; source?: unknown };
  const language = typeof r.language === "string" && LANGUAGE_CODE.test(r.language) ? r.language : null;
  const allowed_languages = Array.isArray(r.allowed_languages) ? r.allowed_languages.filter((c): c is string => typeof c === "string" && LANGUAGE_CODE.test(c)) : [];
  const source = r.source === "meeting" || r.source === "user" || r.source === "deployment" || r.source === "auto" ? r.source : undefined;
  return { language, allowed_languages, ...(source ? { source } : {}) };
}

/** `default` = defer to the level below (the person's default on a spawn, the deployment's in
 *  Settings); `auto` = force auto-detect over any default. */
export type LanguageMode = "default" | "auto" | "one" | "few";

/** What a picker edits. Flat on purpose: switching mode keeps the other modes' picks, so the user
 *  can flip between "One language" and "A few" without losing what they chose. */
export interface LanguageChoice {
  mode: LanguageMode;
  language: string;      // the "one language" pick ("" = none yet)
  languages: string[];   // the "a few languages" list
  fallback: string;      // the "a few" fallback ("" = the list's first entry)
}

export const DEFAULT_CHOICE: LanguageChoice = { mode: "default", language: "", languages: [], fallback: "" };

/** Read a wire setting into a choice. An empty setting reads as `emptyMode` — "default" in Settings
 *  (empty means the deployment decides), "auto" on a running meeting (empty means auto-detect). */
export function settingToChoice(s: LanguageSetting | null | undefined, emptyMode: "default" | "auto"): LanguageChoice {
  const list = (s?.allowed_languages ?? []).filter((c) => LANGUAGE_CODE.test(c));
  const lang = s?.language && LANGUAGE_CODE.test(s.language) ? s.language : "";
  if (list.length >= 2) return { mode: "few", language: lang || list[0], languages: list, fallback: lang && list.includes(lang) ? lang : "" };
  const one = lang || list[0] || "";
  if (one) return { mode: "one", language: one, languages: [one], fallback: "" };
  return { ...DEFAULT_CHOICE, mode: emptyMode };
}

/** The setting a choice stands for. `default` and `auto` both carry no language — which of the two it
 *  means is the caller's (Settings clears, a live switch forces auto). */
export function choiceToSetting(c: LanguageChoice): LanguageSetting {
  if (c.mode === "one" && c.language) return { language: c.language, allowed_languages: [] };
  if (c.mode === "few" && c.languages.length === 1) return { language: c.languages[0], allowed_languages: [] };
  if (c.mode === "few" && c.languages.length >= 2) {
    return { language: c.fallback && c.languages.includes(c.fallback) ? c.fallback : null, allowed_languages: [...c.languages] };
  }
  return { language: null, allowed_languages: [] };
}

/** Why a choice cannot be sent yet, in words that name the fix — or null when it can. */
export function choiceProblem(c: LanguageChoice): string | null {
  if (c.mode === "one" && !c.language) return "Choose a language.";
  if (c.mode === "few" && c.languages.length < 2) return "Add at least two languages, or choose one language instead.";
  return null;
}

/** "Auto-detect" · "German" · "German or English (falls back to German)". */
export function summarizeLanguage(s: LanguageSetting | null | undefined): string {
  const list = s?.allowed_languages ?? [];
  if (list.length >= 2) {
    const labels = list.map(languageLabel);
    const joined = labels.length === 2 ? `${labels[0]} or ${labels[1]}` : `${labels.slice(0, -1).join(", ")} or ${labels[labels.length - 1]}`;
    const fallback = s?.language && list.includes(s.language) ? s.language : list[0];
    return `${joined} (falls back to ${languageLabel(fallback)})`;
  }
  const one = s?.language || list[0];
  return one ? languageLabel(one) : "Auto-detect";
}

/** The `POST /api/bots` fields for a per-meeting choice. "Default" adds nothing, so the server
 *  resolves the person's default, then the deployment's, then auto. */
export function languageSpawnFields(c: LanguageChoice): { language?: string; allowed_languages?: string[] } {
  if (c.mode === "auto") return { language: "auto" };
  if (c.mode === "default") return {};
  const s = choiceToSetting(c);
  if (s.allowed_languages.length) return s.language ? { allowed_languages: s.allowed_languages, language: s.language } : { allowed_languages: s.allowed_languages };
  return s.language ? { language: s.language } : {};
}

/** The `PUT /api/user/transcription` fields that store a choice as the person's default; "" and []
 *  clear back to the deployment default. */
export function languageDefaultFields(c: LanguageChoice): { language: string; allowed_languages: string[] } {
  const s = choiceToSetting(c);
  return { language: s.language ?? "", allowed_languages: s.allowed_languages };
}

/** The meeting-api platform slug for a meeting row's display platform. */
export function platformSlug(display: string): string {
  return display === "Google Meet" ? "google_meet" : display.toLowerCase().replace(/\s+/g, "_");
}

/** A refused live switch, carrying the words to show beside the control. */
export class LanguageSwitchError extends Error {
  constructor(public readonly status: number, message: string, public readonly detail: string) {
    super(message);
    this.name = "LanguageSwitchError";
  }
}

/** What each refusal means to the person holding the control, and what to do about it. */
export function languageSwitchMessage(status: number, detail: string): string {
  switch (status) {
    case 403: return "Only the meeting's owner or an editor of its workspace can change the language. Ask one of them to change it.";
    case 404: return "There is no bot in this meeting any more, so there is nothing to switch. Send a bot to transcribe again.";
    case 409: return "The bot isn't listening yet. Try again once it has joined the meeting.";
    case 422: return detail ? `That language setting was refused: ${detail}` : "That language setting was refused. Pick a language from the list.";
    case 503: return "Your access to this meeting couldn't be confirmed right now. Try again in a moment.";
    case 0: return "Couldn't reach the Vexa server. Check your connection and try again.";
    default: return detail || `The language could not be changed (${status}). Try again.`;
  }
}

/** Switch the language a running bot transcribes in. The body replaces the setting; an auto-detect
 *  setting is sent as `{language: null, allowed_languages: null}`. Resolves to what the bot now runs. */
export async function setMeetingLanguage(platform: string, nativeId: string, s: LanguageSetting): Promise<MeetingLanguage> {
  const body = { language: s.language, allowed_languages: s.allowed_languages.length ? s.allowed_languages : null };
  let res: Response;
  try {
    res = await fetch(`/api/bots/${encodeURIComponent(platform)}/${encodeURIComponent(nativeId)}/config`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
  } catch (e) {
    throw new LanguageSwitchError(0, languageSwitchMessage(0, ""), String(e));
  }
  if (!res.ok) {
    let detail = "";
    try {
      const j = (await res.json()) as { detail?: unknown };
      if (typeof j.detail === "string") detail = j.detail;
    } catch { /* body not json */ }
    throw new LanguageSwitchError(res.status, languageSwitchMessage(res.status, detail), detail);
  }
  const j = (await res.json().catch(() => ({}))) as { transcription_language?: MeetingLanguage };
  return j.transcription_language ?? { ...s, source: "meeting" };
}

/** A settings save refused with a 422 shows the server's own words; anything else is the caller's. */
export function isValidationRefusal(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 422 && !!e.detail;
}
