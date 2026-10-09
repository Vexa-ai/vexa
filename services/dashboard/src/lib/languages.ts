/**
 * All languages supported by Whisper/faster-whisper (matches backend ACCEPTED_LANGUAGE_CODES).
 * Used for searchable language picker with recent choices on top.
 */
export const WHISPER_LANGUAGE_NAMES: Record<string, string> = {
  af: "Afrikaans",
  am: "Amharic",
  ar: "Arabic",
  as: "Assamese",
  az: "Azerbaijani",
  ba: "Bashkir",
  be: "Belarusian",
  bg: "Bulgarian",
  bn: "Bengali",
  bo: "Tibetan",
  br: "Breton",
  bs: "Bosnian",
  ca: "Catalan",
  cs: "Czech",
  cy: "Welsh",
  da: "Danish",
  de: "German",
  el: "Greek",
  en: "English",
  es: "Spanish",
  et: "Estonian",
  eu: "Basque",
  fa: "Persian",
  fi: "Finnish",
  fo: "Faroese",
  fr: "French",
  gl: "Galician",
  gu: "Gujarati",
  ha: "Hausa",
  haw: "Hawaiian",
  he: "Hebrew",
  hi: "Hindi",
  hr: "Croatian",
  ht: "Haitian Creole",
  hu: "Hungarian",
  hy: "Armenian",
  id: "Indonesian",
  is: "Icelandic",
  it: "Italian",
  ja: "Japanese",
  jw: "Javanese",
  ka: "Georgian",
  kk: "Kazakh",
  km: "Khmer",
  kn: "Kannada",
  ko: "Korean",
  la: "Latin",
  lb: "Luxembourgish",
  ln: "Lingala",
  lo: "Lao",
  lt: "Lithuanian",
  lv: "Latvian",
  mg: "Malagasy",
  mi: "Maori",
  mk: "Macedonian",
  ml: "Malayalam",
  mn: "Mongolian",
  mr: "Marathi",
  ms: "Malay",
  mt: "Maltese",
  my: "Myanmar",
  ne: "Nepali",
  nl: "Dutch",
  nn: "Norwegian Nynorsk",
  no: "Norwegian",
  oc: "Occitan",
  pa: "Punjabi",
  pl: "Polish",
  ps: "Pashto",
  pt: "Portuguese",
  ro: "Romanian",
  ru: "Russian",
  sa: "Sanskrit",
  sd: "Sindhi",
  si: "Sinhala",
  sk: "Slovak",
  sl: "Slovenian",
  sn: "Shona",
  so: "Somali",
  sq: "Albanian",
  sr: "Serbian",
  su: "Sundanese",
  sv: "Swedish",
  sw: "Swahili",
  ta: "Tamil",
  te: "Telugu",
  tg: "Tajik",
  th: "Thai",
  tk: "Turkmen",
  tl: "Tagalog",
  tr: "Turkish",
  tt: "Tatar",
  uk: "Ukrainian",
  ur: "Urdu",
  uz: "Uzbek",
  vi: "Vietnamese",
  yi: "Yiddish",
  yo: "Yoruba",
  zh: "Chinese",
  yue: "Cantonese",
};

/**
 * Rough popularity order for display (most used first). Not comprehensive;
 * codes not listed appear after, sorted by name.
 */
const POPULARITY_ORDER: string[] = [
  "en", "es", "zh", "hi", "ar", "pt", "bn", "ru", "ja", "pa", "de", "jw", "ko", "fr",
  "te", "mr", "tr", "vi", "ta", "ur", "id", "pl", "nl", "it", "uk", "th", "gu", "fa",
  "sw", "ro", "ml", "kn", "my", "yo", "ha", "am", "ne", "si", "sv", "cs", "el", "hu",
  "fi", "da", "he", "sk", "bg", "no", "hr", "sr", "ca", "lt", "sl", "et", "lv", "tl",
  "af", "sq", "hy", "az", "eu", "gl", "mk", "ka", "lo", "km", "ps", "sd", "uz", "kk",
  "mn", "tg", "tk", "so", "sn", "mg", "oc", "br", "cy", "yi", "la", "bo", "sa", "fo",
  "lb", "ln", "tt", "ba", "su", "haw", "mi", "yue",
];

/** All Whisper language codes (no "auto"). Sorted by popularity then by name. */
export const WHISPER_LANGUAGE_CODES = (() => {
  const byName = (a: string, b: string) =>
    WHISPER_LANGUAGE_NAMES[a].localeCompare(WHISPER_LANGUAGE_NAMES[b]);
  const rank = (code: string) => {
    const i = POPULARITY_ORDER.indexOf(code);
    return i === -1 ? 1e4 : i;
  };
  return Object.keys(WHISPER_LANGUAGE_NAMES).sort((a, b) => {
    const r = rank(a) - rank(b);
    return r !== 0 ? r : byName(a, b);
  });
})();

export const RECENT_LANGUAGES_KEY = "vexa-recent-transcription-languages";
const RECENT_MAX = 10;

export function getRecentLanguageCodes(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = localStorage.getItem(RECENT_LANGUAGES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((c): c is string => typeof c === "string" && WHISPER_LANGUAGE_NAMES[c] != null).slice(0, RECENT_MAX);
  } catch {
    return [];
  }
}

export function saveRecentLanguage(code: string): void {
  if (code === "auto" || !WHISPER_LANGUAGE_NAMES[code]) return;
  const recent = getRecentLanguageCodes().filter((c) => c !== code);
  recent.unshift(code);
  try {
    localStorage.setItem(RECENT_LANGUAGES_KEY, JSON.stringify(recent.slice(0, RECENT_MAX)));
  } catch {
    // ignore
  }
}

export function getLanguageDisplayName(code: string): string {
  if (code === "auto") return "Auto-detect";
  return WHISPER_LANGUAGE_NAMES[code] ?? code.toUpperCase();
}
