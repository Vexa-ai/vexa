/**
 * The meeting-page navigation retry (shared/navigation.ts), driven with a fake page.
 * Fixture: meeting 282 on claude-rc, 3 Nov 2026 — `net::ERR_NAME_NOT_RESOLVED` on meet.google.com.
 *
 * Run: npx tsx src/shared/navigation.test.ts
 */
import { gotoMeetingPage, networkErrorCode, NavigationNetworkError, NAVIGATION_NETWORK_ERROR } from "./navigation";
import { AdmissionError } from "./admission";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail = "") {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? `\n        ${detail}` : ""}`); failed++; }
}

const URL_282 = "https://meet.google.com/dwz-pacf-jmf?hl=en&pwd=secret-token";
const DNS_282 = `page.goto: net::ERR_NAME_NOT_RESOLVED at ${URL_282}\nCall log:\n  - navigating to "${URL_282}", waiting until "domcontentloaded"`;

/** A page whose goto throws each scripted error in turn, then succeeds. */
function fakePage(errors: unknown[]) {
  let calls = 0;
  return {
    get calls() { return calls; },
    goto: async () => { calls++; const e = errors.shift(); if (e !== undefined) throw e; return null; },
  };
}
const noSleep = async () => {};

async function main() {
  console.log("\nnetworkErrorCode");
  check("DNS failure of meeting 282 is a network error", networkErrorCode(new Error(DNS_282)) === "net::ERR_NAME_NOT_RESOLVED");
  check("connection refused is a network error", networkErrorCode(new Error("net::ERR_CONNECTION_REFUSED at x")) === "net::ERR_CONNECTION_REFUSED");
  check("a Playwright timeout is not (negative control)", networkErrorCode(new Error("page.goto: Timeout 30000ms exceeded.")) === null);
  check("an aborted navigation is not (negative control)", networkErrorCode(new Error("net::ERR_ABORTED at x")) === null);

  console.log("\ngotoMeetingPage");
  {
    const p = fakePage([new Error(DNS_282), new Error(DNS_282)]);
    await gotoMeetingPage(p, URL_282, { waitUntil: "domcontentloaded" }, [1, 1, 1], noSleep);
    check("two DNS failures, then the page: reached on navigation 3", p.calls === 3);
  }
  {
    const p = fakePage([new Error(DNS_282), new Error(DNS_282), new Error(DNS_282), new Error(DNS_282)]);
    let caught: unknown;
    try { await gotoMeetingPage(p, URL_282, {}, [1, 1, 1], noSleep); } catch (e) { caught = e; }
    check("every try fails: 4 navigations, then NavigationNetworkError", p.calls === 4 && caught instanceof NavigationNetworkError);
    const e = caught as NavigationNetworkError;
    check("it is a join_failure AdmissionError", caught instanceof AdmissionError && e.outcome === "join_failure");
    check("its reason names the marker and the code",
      e.message.startsWith(`${NAVIGATION_NETWORK_ERROR}: net::ERR_NAME_NOT_RESOLVED on 4 of 4`), e.message);
    check("its reason carries no URL or token", !/meet\.google\.com|secret-token/.test(e.message), e.message);
  }
  {
    const p = fakePage([new Error(`page.goto: Timeout 60000ms exceeded.\n  - navigating to "${URL_282}"`)]);
    let caught: unknown;
    try { await gotoMeetingPage(p, URL_282, {}, [1, 1, 1], noSleep); } catch (e) { caught = e; }
    check("a timeout is not retried (negative control)", p.calls === 1 && !(caught instanceof NavigationNetworkError));
    check("and is rethrown URL-redacted", caught instanceof Error && !/secret-token/.test((caught as Error).message), String(caught));
  }
  {
    const waits: number[] = [];
    const p = fakePage([new Error(DNS_282)]);
    await gotoMeetingPage(p, URL_282, {}, [2000, 5000, 10000], async (ms) => { waits.push(ms); });
    check("waits the first delay before the retry", waits.length === 1 && waits[0] === 2000, JSON.stringify(waits));
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed) process.exit(1);
}
main().catch((e) => { console.error(e); process.exit(1); });
