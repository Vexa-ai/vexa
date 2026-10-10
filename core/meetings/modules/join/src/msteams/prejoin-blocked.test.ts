/**
 * Regression guard for the Teams pre-join display-name refusal (Vexa-ai/vexa#1780).
 *
 * Teams keeps "Join now" DISABLED while the anonymous display name breaks its rule ("letters,
 * numbers, spaces, and these symbols: - ' . _ @"). With a name such as `Notetaker (recording)`
 * the pre-#1780 join clicked a disabled button, let the 30s click timeout pass, discarded the
 * error, and handed over to the admission wait — which counts a visible "Join now" as a lobby
 * indicator and reported `awaiting_admission` for the whole lobby budget. No join was ever
 * attempted and no host was ever asked to admit the bot.
 *
 * Fix: after the name is filled, join.ts asks the live button whether it is enabled. Still
 * disabled after a short budget (with the AV-confirm modal cleared) → `TeamsPreJoinBlockedError`,
 * reasonCode `teams_prejoin_blocked`, carrying Teams' own validation text. The join never clicks
 * and never reaches the admission wait.
 *
 * Fabricated-DOM test in the style of auth-redirect.test.ts — no browser, no live meeting. A
 * virtual clock makes the waits free and lets the test bound how long a refusal takes to report.
 * The live DOM facts it models were captured against teams.live.com on 2026-10-11: the button is
 * `button[data-tid="prejoin-join-button"]` "Join now", `disabled` while the name is invalid.
 *
 * Run: npx tsx src/msteams/prejoin-blocked.test.ts
 */

import { joinMicrosoftTeams } from './join';
import {
  TeamsPreJoinBlockedError,
  TEAMS_PREJOIN_BLOCKED,
  teamsDisplayNameDisallowedChars,
} from './prejoin-blocked';

let vnow = 1_000_000;
(Date as any).now = () => vnow;

let passed = 0, failed = 0;
function check(name: string, ok: boolean, detail = '') {
  if (ok) { realConsoleLog(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { realConsoleLog(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ''}`); failed++; }
}

let captured: string[] = [];
const realConsoleLog = console.log;
function startCapture() {
  captured = [];
  console.log = (...a: any[]) => {
    const s = a.map(String).join(' ');
    try { captured.push(JSON.parse(s).msg ?? s); } catch { captured.push(s); }
  };
}
function stopCapture(): string[] { console.log = realConsoleLog; return captured; }

const TEAMS_RULE_TEXT =
  "Your name can only include letters, numbers, spaces, and these symbols: - ' . _ @";

/**
 * A Teams light-meetings pre-join: name input + an enabled/disabled "Join now". `enabledAfter` is
 * how many isEnabled() reads report disabled before it enables (Infinity = never).
 */
function prejoinPage(enabledAfter: number) {
  let enabledReads = 0;
  const clicks: string[] = [];
  const filled: string[] = [];
  const isJoinNow = (sel: string) => sel.includes('Join now');
  const node = (sel: string): any => ({
    first: () => node(sel),
    isVisible: async () => isJoinNow(sel) || sel.includes('placeholder*="name"'),
    isEnabled: async () => {
      if (!isJoinNow(sel)) return true;
      enabledReads += 1;
      return enabledReads > enabledAfter;
    },
    waitFor: async () => {
      if (sel.includes('placeholder*="name"')) return;
      throw new Error(`timeout waiting for ${sel}`);
    },
    click: async () => {
      if (isJoinNow(sel)) clicks.push(sel);
      else throw new Error(`no element for ${sel}`);
    },
    fill: async (v: string) => { filled.push(v); },
    getAttribute: async () => null,
    count: async () => 1,
  });
  const page: any = {
    url: () => 'https://teams.live.com/light-meetings/launch',
    goto: async () => {},
    waitForTimeout: async (ms: number) => { vnow += ms; },
    locator: node,
    evaluate: async (fn: any) =>
      typeof fn === 'string' && fn.includes('can only include') ? TEAMS_RULE_TEXT : undefined,
    keyboard: { press: async () => {} },
    isClosed: () => false,
  };
  return { page, clicks, filled };
}

const URL = 'https://teams.live.com/meet/9000000000001?p=FakeTestPass';
const CFG = { platform: 'teams' } as any;

async function run(name: string, enabledAfter: number) {
  const { page, clicks, filled } = prejoinPage(enabledAfter);
  const t0 = vnow;
  let error: any = null;
  startCapture();
  try { await joinMicrosoftTeams(page, URL, name, CFG); } catch (e) { error = e; }
  const logs = stopCapture();
  return { error, clicks, filled, logs, elapsedMs: vnow - t0 };
}

(async () => {
  realConsoleLog('\n=== #1780: a name Teams refuses is a typed terminal, not a lobby ===');
  {
    const r = await run('Notetaker (recording)', Infinity);
    check('the join rejects with TeamsPreJoinBlockedError', r.error instanceof TeamsPreJoinBlockedError,
      String(r.error));
    check('reasonCode is teams_prejoin_blocked', r.error?.reasonCode === TEAMS_PREJOIN_BLOCKED);
    check('the message leads with the discriminator (it rides into last_error)',
      String(r.error?.message).startsWith(`${TEAMS_PREJOIN_BLOCKED}:`), String(r.error?.message));
    check('the message carries Teams\' own validation text',
      String(r.error?.message).includes(TEAMS_RULE_TEXT), String(r.error?.message));
    check('the message names the refused characters',
      String(r.error?.message).includes('characters Teams does not allow: ( )'), String(r.error?.message));
    check('the name was filled before the verdict', r.filled.includes('Notetaker (recording)'));
    check('the disabled button was never clicked', r.clicks.length === 0, JSON.stringify(r.clicks));
    check('the admission wait never ran (no waiting-room verdict was logged)',
      !r.logs.some((l) => /waiting room/i.test(l)), JSON.stringify(r.logs.filter((l) => /waiting/i.test(l))));
    check('the refusal is reported in under 60s, not after a lobby budget',
      r.elapsedMs < 60_000, `${r.elapsedMs}ms`);
    check('a warning about the name was logged at the name step',
      r.logs.some((l) => l.includes('characters Teams does not allow')));
  }

  realConsoleLog('\n=== a valid name still joins ===');
  {
    const r = await run('Notetaker - recording', 0);
    check('no error', r.error === null, String(r.error));
    check('"Join now" was clicked', r.clicks.length >= 1);
    check('no name warning', !r.logs.some((l) => l.includes('characters Teams does not allow')));
  }

  realConsoleLog('\n=== a button that enables a moment late is not a refusal ===');
  {
    const r = await run('Vexa Bot', 3);
    check('no error', r.error === null, String(r.error));
    check('"Join now" was clicked once it enabled', r.clicks.length >= 1);
  }

  realConsoleLog('\n=== the advisory character check ===');
  {
    const eq = (a: string[], b: string[]) => JSON.stringify(a) === JSON.stringify(b);
    check('parentheses are flagged', eq(teamsDisplayNameDisallowedChars('Notetaker (recording)'), ['(', ')']));
    check('hyphen, apostrophe, dot, underscore, at and spaces pass',
      eq(teamsDisplayNameDisallowedChars("O'Brien-Bot_2 @team."), []));
    check('non-ASCII letters pass', eq(teamsDisplayNameDisallowedChars('Protokoll Jürgen Ölçer'), []));
    check('colon and slash are flagged, de-duplicated',
      eq(teamsDisplayNameDisallowedChars('Bot: A/B/C'), [':', '/']));
  }

  realConsoleLog(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();
