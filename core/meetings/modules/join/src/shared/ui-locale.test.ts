/**
 * The UI-locale check: a meeting page that is not English is reported as `ui_locale_mismatch`, and
 * a join that then fails is typed as `UiLocaleMismatchError` instead of the English detectors'
 * reading of a page they cannot read.
 *
 * Run: npx tsx src/shared/ui-locale.test.ts
 */
import {
  PINNED_UI_LOCALE, UI_LOCALE_MISMATCH, UiLocaleMismatchError,
  classifyUiLanguage, checkUiLocale, uiLocaleMismatchOf,
} from "./ui-locale";
import { AdmissionError } from "./admission";
import { joinMeeting } from "../index";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

/** A page stand-in whose locale probe answers with this lang attribute and visible text. */
function localePage(lang: string, text: string, extra: Record<string, unknown> = {}): any {
  return {
    on: () => {},
    url: () => "https://meet.jit.si/locale-test",
    goto: async () => {},
    waitForTimeout: async () => {},
    screenshot: async () => {},
    evaluate: async (fn: unknown) =>
      String(fn).includes("innerText") ? { lang, text, nav: PINNED_UI_LOCALE } : false,
    ...extra,
  };
}

async function main(): Promise<void> {
  console.log("\n=== classifyUiLanguage ===");
  assert(PINNED_UI_LOCALE === "en-US", "the pin is en-US");
  for (const tag of ["en", "en-US", "EN-gb", "en_AU"]) {
    assert(classifyUiLanguage(tag, "").english === true, `lang="${tag}" is English`);
  }
  for (const tag of ["de", "nl-NL", "ru", "ja", "ar", "he", "zh-TW", "pt-BR"]) {
    const r = classifyUiLanguage(tag, "Join now");
    assert(r.english === false && r.source === "html-lang", `lang="${tag}" is not English, whatever the text says`);
  }
  assert(classifyUiLanguage("", "Ready to join? Ask to join").english === true, "no lang + English landmark → English");
  assert(classifyUiLanguage(null, "What’s your name?").english === true, "typographic apostrophe landmark → English");
  const unknown = classifyUiLanguage("", "Bereit zur Teilnahme? Teilnahme anfragen");
  assert(unknown.english === null && unknown.lang === "unknown", "no lang + no English landmark → unknown, not a mismatch");
  assert(classifyUiLanguage("", "Camera en microfoon").english === null, "a lone word shared with English (camera) is not a landmark");

  console.log("\n=== checkUiLocale records a mismatch per page, on every platform ===");
  for (const platform of ["google_meet", "teams", "zoom", "jitsi"]) {
    const de = localePage("de", "Jetzt teilnehmen");
    const r = await checkUiLocale(de, platform, "pre-join");
    assert(r.english === false && uiLocaleMismatchOf(de)?.lang === "de", `${platform}: a German page is a ui_locale_mismatch`);
    const en = localePage("en-US", "Join now");
    await checkUiLocale(en, platform, "pre-join");
    assert(uiLocaleMismatchOf(en) === null, `${platform}: an English page is not`);
  }
  {
    const p = localePage("de", "");
    await checkUiLocale(p, "teams", "pre-join");
    p.evaluate = async () => { throw new Error("detached"); };
    await checkUiLocale(p, "teams", "join verdict");
    assert(uiLocaleMismatchOf(p)?.lang === "de", "a later unreadable page does not erase a recorded mismatch");
  }

  console.log("\n=== UiLocaleMismatchError ===");
  {
    const e = new UiLocaleMismatchError("zoom", { lang: "ja", source: "html-lang", english: false }, "lobby timeout");
    assert(e instanceof AdmissionError && e.outcome === "join_failure", "is an AdmissionError with outcome join_failure");
    assert(e.message.startsWith(`${UI_LOCALE_MISMATCH}:`) && e.message.includes("'ja'") && e.message.includes("lobby timeout"),
      "message leads with the reason code and names the language and the untrusted reading");
  }

  console.log("\n=== joinMeeting: a failed join on a non-English page is typed ui_locale_mismatch ===");
  // The Jitsi flow below never finds its controls and fails; the page's language decides how that
  // failure is reported. English: the original error, untouched. German: UiLocaleMismatchError
  // carrying the original error's text.
  const nameFieldMiss = () => ({
    locator: () => ({
      first: () => ({
        isVisible: async () => false, count: async () => 0, click: async () => {},
        waitFor: async () => { throw new Error("Timeout 30000ms exceeded waiting for the name field"); },
      }),
      count: async () => 0, isVisible: async () => false,
    }),
    $: async () => null, $$: async () => [],
    waitForSelector: async () => { throw new Error("Timeout 30000ms exceeded waiting for the name field"); },
  });
  const failedJoin = async (lang: string): Promise<any> => {
    try {
      await joinMeeting(localePage(lang, "", nameFieldMiss()), {
        meetingUrl: "https://meet.jit.si/locale-test", platform: "jitsi", waitingRoomTimeoutMs: 2000,
      });
    } catch (e) { return e; }
    return null;
  };
  const en = await failedJoin("en");
  assert(en !== null && !(en instanceof UiLocaleMismatchError), `lang=en: the original failure stands (${en?.name}: ${String(en?.message).slice(0, 80)})`);
  const de = await failedJoin("de");
  assert(de instanceof UiLocaleMismatchError, `lang=de: throws UiLocaleMismatchError (got ${de?.name})`);
  assert(!!en && String(de?.message).includes(String(en.message)), "lang=de: the original failure's text is kept in the message");

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
