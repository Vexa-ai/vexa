/**
 * The bot's UI language: pinned to English, then checked on the page.
 *
 * Every platform localises its lobby, waiting room and rejection copy to the VIEWER's language, and
 * the bot is the viewer. The join layer reads that copy in English (the waiting-room and rejection
 * text indicators of every platform), so the bot's browser is pinned to English on every launch:
 * Chromium `--lang` / `--accept-lang` (browser-args.ts), the Playwright context `locale`
 * (@vexa/remote-browser), and `?hl=en` on the Google Meet URL.
 *
 * The pin can still lose. A signed-in bot account carries its own language preference, which
 * Google, Microsoft and Zoom apply over the browser's; Meet's `?hl=` overrides it for Meet, Teams
 * and Zoom have no such URL lever. So after the meeting page loads the join layer READS the
 * language the page rendered in (`<html lang>`, or an English landmark in the visible text when the
 * attribute is missing). A page that is not English is logged loudly as `ui_locale_mismatch`, and a
 * join that then ends without admission is thrown as `UiLocaleMismatchError`: the English
 * waiting-room and rejection detectors cannot read that page, so its "timeout" or "rejection" must
 * never be reported as the host's decision.
 */
import type { Page } from "playwright";
import { log } from "../_host";
import { AdmissionError } from "./admission";

/** The one UI locale every bot browser runs in. */
export const PINNED_UI_LOCALE = "en-US";

/** The reason code a non-English meeting page is reported under (logs and join evidence). */
export const UI_LOCALE_MISMATCH = "ui_locale_mismatch";

/** What the page said about its own language. */
export interface UiLocaleReading {
  /** The detected language tag (lower-cased), or `"unknown"`. */
  lang: string;
  /** Where it came from: the document's `lang` attribute, English copy on the page, or nothing. */
  source: "html-lang" | "landmark" | "none";
  /** true = English; false = positively not English; null = could not tell. */
  english: boolean | null;
}

/**
 * English copy that the platforms' pre-join, lobby and in-call pages show. Used only when the page
 * carries no `lang` attribute; one hit is enough to read the page as English. Matched as whole
 * words, case-insensitively. Phrases only: single words such as "camera" or "meeting" are also
 * Dutch, Italian or German UI copy.
 */
export const ENGLISH_UI_LANDMARKS: readonly string[] = [
  "join now", "ask to join", "what's your name", "your name", "type your name", "enter your name",
  "asking to be let in", "waiting for the host", "let you in", "leave call", "leave meeting",
  "join meeting", "join audio", "continue on this browser", "turn off microphone", "turn off camera",
  "you can't join", "you can’t join", "return to home screen",
];

/** A language tag reduced to its comparable form: trimmed, lower-cased, `_` → `-`. */
export function normalizeLangTag(raw: string | null | undefined): string {
  return (raw ?? "").trim().toLowerCase().replace(/_/g, "-");
}

/** Is this tag English (`en`, `en-US`, `en-GB`, …)? */
export function isEnglishTag(tag: string): boolean {
  return /^en(-|$)/.test(normalizeLangTag(tag));
}

/**
 * Classify the page's language from its `lang` attribute and its visible text. Pure, so it is
 * tested without a browser. The attribute wins when present; otherwise an English landmark reads
 * the page as English; otherwise the language is unknown (logged, never treated as a mismatch).
 */
export function classifyUiLanguage(htmlLang: string | null | undefined, visibleText: string): UiLocaleReading {
  const tag = normalizeLangTag(htmlLang);
  if (tag) return { lang: tag, source: "html-lang", english: isEnglishTag(tag) };
  const text = (visibleText || "").normalize("NFKC").toLowerCase().replace(/\s+/g, " ");
  const hit = ENGLISH_UI_LANDMARKS.some((l) => {
    const i = text.indexOf(l);
    if (i < 0) return false;
    const before = i === 0 ? " " : text[i - 1];
    const after = i + l.length >= text.length ? " " : text[i + l.length];
    return !/[a-z]/.test(before) && !/[a-z]/.test(after);
  });
  return hit ? { lang: "en", source: "landmark", english: true } : { lang: "unknown", source: "none", english: null };
}

// The latest reading per page. A WeakMap, so a closed page takes its reading with it.
const readings = new WeakMap<object, UiLocaleReading>();

/** The mismatch recorded for this page, if any reading of it was positively not English. */
export function uiLocaleMismatchOf(page: Page): UiLocaleReading | null {
  const r = readings.get(page);
  return r && r.english === false ? r : null;
}

/**
 * Read the language the meeting page rendered in and record it for this page. Logs every reading;
 * a non-English page is logged as a loud `ui_locale_mismatch` line. A mismatch, once seen, stays
 * recorded for the page even if a later reading is unknown. Never throws.
 */
export async function checkUiLocale(page: Page, platform: string, when: string): Promise<UiLocaleReading> {
  let reading: UiLocaleReading = { lang: "unknown", source: "none", english: null };
  let nav = "?";
  try {
    const ctx = await page.evaluate(() => ({
      lang: document.documentElement.getAttribute("lang") || "",
      text: (document.body && document.body.innerText) ? document.body.innerText.slice(0, 20000) : "",
      nav: navigator.language || "",
    }));
    nav = ctx.nav || "?";
    reading = classifyUiLanguage(ctx.lang, ctx.text);
  } catch { /* page not readable: the reading stays unknown */ }
  const prior = readings.get(page);
  if (!(prior && prior.english === false && reading.english !== false)) readings.set(page, reading);
  if (reading.english === false) {
    log(
      `🚨 ${UI_LOCALE_MISMATCH}: the ${platform} page (${when}) rendered in '${reading.lang}' ` +
        `(via ${reading.source}), not the pinned ${PINNED_UI_LOCALE}; navigator.language=${nav}. ` +
        `The English waiting-room and rejection detectors cannot read this page.`,
    );
  } else if (reading.english === null) {
    log(`UI locale (${platform}, ${when}): unknown — no lang attribute and no English landmark; navigator.language=${nav}`);
  } else {
    log(`UI locale (${platform}, ${when}): ${reading.lang} via ${reading.source}; navigator.language=${nav}`);
  }
  return reading;
}

/**
 * A join that ended without admission on a page that was not English. Typed as an AdmissionError
 * with outcome `join_failure`: the verdict the English detectors produced on that page (a lobby
 * timeout, a "rejection", a missing button) is not trusted, and the message names the detected
 * language and what the detectors had concluded.
 */
export class UiLocaleMismatchError extends AdmissionError {
  readonly reasonCode = UI_LOCALE_MISMATCH;
  readonly detectedLang: string;
  constructor(platform: string, reading: UiLocaleReading, original: string) {
    super(
      "join_failure",
      `${UI_LOCALE_MISMATCH}: the ${platform} page rendered in '${reading.lang}' (via ${reading.source}), ` +
        `not the pinned ${PINNED_UI_LOCALE}, so the join layer could not read the lobby or a rejection; ` +
        `its reading was: ${original}`,
    );
    this.name = "UiLocaleMismatchError";
    this.detectedLang = reading.lang;
  }
}
