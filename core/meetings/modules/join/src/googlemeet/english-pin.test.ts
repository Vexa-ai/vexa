/**
 * The English pin, end to end on the Meet lobby: the bot's browser is pinned to en-US, so Meet
 * serves its English waiting-room and denial copy, and the English detectors read both.
 *
 * Pages are fixture DOMs in jsdom, with Meet's English copy under `<html lang="en">`. The page
 * stand-in resolves the Playwright selector forms the detectors use (`text=…` substring,
 * `text="…"` exact, `css:has-text("…")`, plain CSS) against that DOM, so the detectors run on the
 * markup rather than on a list of selector strings.
 *
 * The negative control is the reason the pin exists: the same waiting room in German is not
 * recognised by the English detectors, and the locale check reports it as `ui_locale_mismatch`.
 *
 * Run: npx tsx src/googlemeet/english-pin.test.ts
 */
import { JSDOM } from "jsdom";
import { getJoinBrowserArgs } from "../browser-args";
import { withPinnedMeetLocale } from "./join";
import { checkForGoogleRejection, checkForWaitingRoomIndicators } from "./admission";
import { PINNED_UI_LOCALE, checkUiLocale, uiLocaleMismatchOf } from "../shared/ui-locale";

let passed = 0, failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  \x1b[32mPASS\x1b[0m  ${msg}`); }
  else { failed++; console.log(`  \x1b[31mFAIL\x1b[0m  ${msg}`); }
}

const norm = (s: string | null | undefined) => (s || "").replace(/\s+/g, " ").trim();
const unquote = (s: string) => s.replace(/^"(.*)"$/, "$1").replace(/\\'/g, "'");

/** A Playwright-Page stand-in over a jsdom document (every element counts as visible). */
function fixturePage(html: string): any {
  const dom = new JSDOM(html);
  const doc = dom.window.document;
  const all = () => Array.from(doc.body.querySelectorAll("*")) as Element[];
  const resolve = (sel: string): Element[] => {
    if (sel.startsWith("text=")) {
      const body = sel.slice(5);
      if (body.startsWith('"')) {
        const want = unquote(body);
        return all().filter((el) => norm(el.textContent) === want);
      }
      const want = body.toLowerCase();
      return all().filter((el) => norm(el.textContent).toLowerCase().includes(want));
    }
    const has = sel.match(/^(.*):has-text\((".*")\)$/);
    if (has) {
      const want = unquote(has[2]).toLowerCase();
      return (Array.from(doc.querySelectorAll(has[1] || "*")) as Element[])
        .filter((el) => norm(el.textContent).toLowerCase().includes(want));
    }
    return Array.from(doc.querySelectorAll(sel)) as Element[];
  };
  const loc = (sel: string) => {
    const els = () => resolve(sel);
    const at = (i: number) => ({
      isVisible: async () => els().length > i,
      boundingBox: async () => null,
      getAttribute: async () => null,
    });
    return { first: () => at(0), nth: (i: number) => at(i), count: async () => els().length, evaluateAll: async () => [] };
  };
  return {
    locator: loc,
    frames: () => [],
    url: () => "https://meet.google.com/abc-defg-hij?hl=en",
    evaluate: async () => ({
      lang: doc.documentElement.getAttribute("lang") || "",
      text: norm(doc.body.textContent),
      nav: PINNED_UI_LOCALE,
    }),
  };
}

// Meet's English lobby copy (typographic apostrophe as served live).
const WAITING_EN = `<!doctype html><html lang="en"><body><div role="main">
  <div jsname="rhHFf"><span>Asking to be let in...</span></div>
  <div>You’ll join the call when someone lets you in</div>
  <button jsname="dqt8Pb"><span>Return to home screen</span></button></div></body></html>`;
const DENIED_EN = `<!doctype html><html lang="en"><body><div role="main">
  <div jsname="aO7olb">Someone in the call denied your request to join</div>
  <button jsname="IbE0S"><span>Return to home screen</span></button></div></body></html>`;
const WAITING_DE = `<!doctype html><html lang="de"><body><div role="main">
  <div jsname="rhHFf"><span>Teilnahme wird angefragt...</span></div>
  <div>Sie nehmen am Anruf teil, sobald Sie jemand hereinlässt</div>
  <button jsname="dqt8Pb"><span>Zurück zum Startbildschirm</span></button></div></body></html>`;

async function main(): Promise<void> {
  console.log("\n=== the pin: Chrome flags and Meet's hl ===");
  const args = getJoinBrowserArgs();
  assert(args.includes("--lang=en-US"), "launch args carry --lang=en-US");
  assert(args.includes("--accept-lang=en-US,en"), "launch args carry --accept-lang=en-US,en");
  assert(/[?&]hl=en(&|$)/.test(withPinnedMeetLocale("https://meet.google.com/abc-defg-hij", PINNED_UI_LOCALE)),
    "the Meet URL the bot opens carries hl=en");

  console.log("\n=== English waiting room under the pin ===");
  {
    const page = fixturePage(WAITING_EN);
    const r = await checkUiLocale(page, "google_meet", "test");
    assert(r.english === true && r.lang === "en", `the page reads as English (${r.lang} via ${r.source})`);
    assert(uiLocaleMismatchOf(page) === null, "no ui_locale_mismatch recorded");
    assert(await checkForWaitingRoomIndicators(page), "the waiting room is detected");
    assert(!(await checkForGoogleRejection(page)), "the waiting room is not read as a rejection");
  }

  console.log("\n=== English host denial under the pin ===");
  {
    const page = fixturePage(DENIED_EN);
    assert((await checkUiLocale(page, "google_meet", "test")).english === true, "the page reads as English");
    assert(await checkForGoogleRejection(page), "the host's denial is detected");
  }

  console.log("\n=== negative control: the same waiting room in German ===");
  {
    const page = fixturePage(WAITING_DE);
    assert(!(await checkForWaitingRoomIndicators(page)), "the English detectors do not see the German waiting room");
    await checkUiLocale(page, "google_meet", "test");
    const m = uiLocaleMismatchOf(page);
    assert(m !== null && m.lang === "de", `the locale check reports ui_locale_mismatch (lang=${m?.lang})`);
  }

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
