/**
 * L-1699 regression guard: a Google Meet knock that Google refuses must end as
 * a denied join BEFORE any waiting-room evidence, while a genuine waiting room
 * (text indicator + Return button) still waits.
 *
 * Fixture replay of staging meeting 86: after "Ask to join", the page faulted
 * (DisconnectedError / EndCause) and only "Return to home screen" remained. The
 * pre-fix detector counted that button as waiting-room evidence, sent
 * awaiting_admission, and waited 600s. These fixtures pin the terminal denial,
 * the no-awaiting_admission invariant, the sanitized diagnostic line, the
 * genuine-lobby control, and the post-admission DisconnectedError negative.
 *
 * Run: npx tsx src/googlemeet/knock-refusal.test.ts
 */

import { setHooks } from "../_host";
import { AdmissionError } from "../shared/admission";
import {
  checkForGoogleWaitingRoomTextIndicators,
  waitForGoogleMeetingAdmission,
} from "./admission";
import { markGoogleKnock } from "./knock";

let passed = 0, failed = 0;
function check(name: string, actual: unknown, expected: unknown) {
  const ok = actual === expected;
  console.log(`  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}`);
  if (!ok) console.log(`        expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  if (ok) passed++; else failed++;
}

const RETURN_BUTTON = 'button:has-text("Return to home screen")';
const WAITING_TEXT = 'text=Asking to be let in';
const SELF_NAME = '[data-self-name]';

type Emitter = (arg: any) => void;

function mockPage(
  visible: string[],
  opts: { participantLabels?: string[]; headline?: string; onWait?: () => void } = {},
): any {
  const state = { visible: new Set(visible) };
  const handlers = new Map<string, Emitter[]>();
  const page: any = {
    state,
    handlers,
    emit(event: string, arg: any) {
      for (const handler of handlers.get(event) ?? []) handler(arg);
    },
    locator: (selector: string) => ({
      first: () => ({
        isVisible: async () => state.visible.has(selector),
        getAttribute: async () => null,
      }),
      count: async () => (state.visible.has(selector) ? 1 : 0),
      evaluateAll: async () =>
        selector === '[data-participant-id]' ? (opts.participantLabels ?? []) : [],
    }),
    mouse: { move: async () => {} },
    frames: () => [],
    screenshot: async () => {},
    waitForTimeout: async (_ms: number) => {
      opts.onWait?.();
    },
    evaluate: async () => opts.headline ?? '',
    url: () => 'https://meet.google.com/abc-defg-hij',
    on: (event: string, handler: Emitter) => {
      handlers.set(event, [...(handlers.get(event) ?? []), handler]);
    },
  };
  return page;
}

function emitDisconnectedError(page: any) {
  page.emit('console', {
    text: () => 'DisconnectedError, EndCause = 72, StartupCode = 200',
  });
}

function emitClientError(page: any) {
  page.emit('response', {
    status: () => 403,
    url: () => 'https://meet.google.com/abc-defg-hij?hs=123&authuser=0',
  });
}

async function captureLogs<T>(fn: () => Promise<T>): Promise<{ result: T; lines: string[] }> {
  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: any[]) => {
    lines.push(args.map((arg) => String(arg)).join(' '));
  };
  try {
    return { result: await fn(), lines };
  } finally {
    console.log = original;
  }
}

async function main() {
  const states: string[] = [];
  setHooks({ onState: (state) => { states.push(state); } });

  console.log('\n=== L-1699: refused knock ends denied before waiting-room evidence ===');

  // 0. Evidence split: the Return button alone is not waiting-room proof, but
  //    waiting-room TEXT keeps the genuine lobby classification.
  check(
    'Return button alone is not waiting-room text evidence',
    await checkForGoogleWaitingRoomTextIndicators(mockPage([RETURN_BUTTON])),
    false,
  );
  check(
    'waiting text + Return button is waiting-room evidence',
    await checkForGoogleWaitingRoomTextIndicators(mockPage([WAITING_TEXT, RETURN_BUTTON])),
    true,
  );

  // 1. Meeting-86 replay: click -> page fault -> only Return button visible.
  {
    states.length = 0;
    const page = mockPage([RETURN_BUTTON], {
      headline: `You can't join meeting abc-defg-hij ${'x'.repeat(220)}`,
    });
    markGoogleKnock(page);
    emitDisconnectedError(page);
    emitClientError(page);

    const started = Date.now();
    const { result: error, lines } = await captureLogs(async () => {
      try {
        await waitForGoogleMeetingAdmission(page, 60_000, {});
        return null;
      } catch (err) {
        return err;
      }
    });
    const elapsed = Date.now() - started;

    check('86 replay throws AdmissionError', error instanceof AdmissionError, true);
    check('86 replay outcome is denial', (error as AdmissionError)?.outcome, 'denial');
    check('86 replay ends inside the 60s bound', elapsed < 60_000, true);
    check('86 replay never sends awaiting_admission', states.includes('awaiting_admission'), false);

    const diagnostics = lines.filter((line) => line.includes('Google Meet refused the join knock'));
    check('86 replay emits exactly one refusal diagnostic', diagnostics.length, 1);
    const diagnostic = diagnostics[0] ?? '';
    check('diagnostic marks disconnected_error', diagnostic.includes('"reason":"disconnected_error"'), true);
    check('diagnostic redacts the meeting code in headline', diagnostic.includes('<code>'), true);
    check('diagnostic keeps headline within 200 chars', /"headline":"[^"]{201,}"/.test(diagnostic), false);
    check('diagnostic carries sanitized 4xx path', diagnostic.includes('"client_error_paths":["/<code>"]'), true);
    check('diagnostic never leaks code or query', diagnostic.includes('abc-defg-hij') || diagnostic.includes('hs=123'), false);
  }

  // 2. Can't-join page copy is refusal evidence even without a console fault.
  {
    states.length = 0;
    const page = mockPage(["text=can't join this call"]);
    markGoogleKnock(page);

    const { result: error } = await captureLogs(async () => {
      try {
        await waitForGoogleMeetingAdmission(page, 60_000, {});
        return null;
      } catch (err) {
        return err;
      }
    });

    check('denied-text fixture throws AdmissionError', error instanceof AdmissionError, true);
    check('denied-text fixture outcome is denial', (error as AdmissionError)?.outcome, 'denial');
    check('denied-text fixture never sends awaiting_admission', states.includes('awaiting_admission'), false);
  }

  // 3. Genuine waiting room: "Asking to be let in" + Return button still waits
  //    and reports awaiting_admission once, then admits when controls appear.
  {
    states.length = 0;
    let waits = 0;
    const page = mockPage([WAITING_TEXT, RETURN_BUTTON], {
      onWait: () => {
        waits += 1;
        if (waits === 1) {
          page.state.visible.delete(WAITING_TEXT);
          page.state.visible.add(SELF_NAME);
        }
      },
    });
    markGoogleKnock(page);

    const admitted = await waitForGoogleMeetingAdmission(page, 30_000, {});
    check('genuine waiting room admits after host let-in', admitted, true);
    check('genuine waiting room sent awaiting_admission once', states.filter((s) => s === 'awaiting_admission').length, 1);
  }

  // 4. Ordering guard: once waiting-room TEXT has been seen, a later page fault
  //    is a dropped lobby connection, not proof the original knock was refused.
  {
    states.length = 0;
    let waits = 0;
    const page = mockPage([WAITING_TEXT, RETURN_BUTTON], {
      onWait: () => {
        waits += 1;
        if (waits === 1) {
          page.state.visible.delete(WAITING_TEXT);
          page.state.visible.add(SELF_NAME);
        }
      },
    });
    markGoogleKnock(page);
    emitDisconnectedError(page);

    const admitted = await waitForGoogleMeetingAdmission(page, 30_000, {});
    check('post-waiting-text DisconnectedError still admits', admitted, true);
    check('post-waiting-text DisconnectedError is not a refusal', states.includes('awaiting_admission'), true);
  }

  // 5. Negative control: DisconnectedError after admission must not be
  //    reclassified as a refused knock.
  {
    states.length = 0;
    let waits = 0;
    const page = mockPage([], {
      onWait: () => {
        waits += 1;
        if (waits === 1) {
          page.state.visible.add(SELF_NAME);
          emitDisconnectedError(page);
        }
      },
    });
    markGoogleKnock(page);

    const admitted = await waitForGoogleMeetingAdmission(page, 30_000, {});
    check('post-admission DisconnectedError still admits', admitted, true);
    check('post-admission DisconnectedError is not a refusal', states.includes('awaiting_admission'), false);
  }

  setHooks({});

  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
