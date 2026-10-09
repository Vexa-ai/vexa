/**
 * Telemost admission — the DOM oracle and the typed terminal outcomes.
 *
 * Telemost exposes no runtime API, so admission is read from the page: in the
 * call = leave control visible with no pre-join form and no waiting-room text.
 * A waiting-room decline must throw a TYPED AdmissionError('denial') (the
 * driver records a permanent rejection, not a retried join_failure), and a
 * give-up must throw AdmissionError('lobby_timeout').
 *
 * Drives the SHIPPED isAdmitted / isInLobby / waitForTelemostMeetingAdmission
 * over a fabricated Page (no browser): `evaluate` runs the probe in-node
 * against a stub `document`, `locator(sel).isVisible()` answers from a set.
 *
 * Run: npx tsx src/telemost/admission.test.ts
 */

import { isAdmitted, isInLobby, waitForTelemostMeetingAdmission } from "./admission";
import { AdmissionError } from "../shared/admission";

let passed = 0;
let failed = 0;
function check(name: string, ok: boolean, detail?: string) {
  if (ok) { console.log(`  \x1b[32mPASS\x1b[0m  ${name}`); passed++; }
  else { console.log(`  \x1b[31mFAIL\x1b[0m  ${name}${detail ? ` — ${detail}` : ""}`); failed++; }
}

type Screen = { text: string; visible: string[]; prejoin?: boolean };

function makePage(screen: Screen): any {
  (globalThis as any).document = {
    body: { innerText: screen.text },
    querySelector: (_s: string) => (screen.prejoin ? { offsetParent: {} } : null),
  };
  const page: any = {
    mainFrame: () => page,
    frames: () => [page],
    url: () => "https://telemost.yandex.ru/j/12345678901234",
    async evaluate(fn: any, arg?: any) { return fn(arg); },
    async waitForTimeout(_ms: number) {},
    locator(sel: string) {
      return { first: () => ({ isVisible: async () => screen.visible.includes(sel) }) };
    },
  };
  return page;
}

const LEAVE = 'button[aria-label*="Покинуть" i]';

async function main() {
  console.log("\n=== isAdmitted / isInLobby — DOM oracle ===");

  check("leave control visible, no pre-join, no lobby → admitted",
    await isAdmitted(makePage({ text: "Иван Петров", visible: [LEAVE] })));

  check("pre-join form on screen → not admitted (even with a leave-like control)",
    !(await isAdmitted(makePage({ text: "Как вас зовут?", visible: [LEAVE], prejoin: true }))));

  check("waiting-room text → not admitted",
    !(await isAdmitted(makePage({ text: "Вы в зале ожидания", visible: [LEAVE] }))));

  check("«комнате ожидания» (the UI's own term, any case form) → in lobby",
    await isInLobby(makePage({ text: "Вы в комнате ожидания. Организатор впустит вас", visible: [] })));

  check("waiting-room text (EN UI) → in lobby",
    await isInLobby(makePage({ text: "You are in the waiting room", visible: [] })));

  check("nothing recognisable → not admitted",
    !(await isAdmitted(makePage({ text: "", visible: [] }))));

  console.log("\n=== waitForTelemostMeetingAdmission — typed outcomes ===");

  let caught: any = null;
  try {
    await waitForTelemostMeetingAdmission(
      makePage({ text: "Организатор отклонил ваш запрос", visible: [] }), 60_000, {} as any);
  } catch (e) { caught = e; }
  check("decline throws AdmissionError('denial')",
    caught instanceof AdmissionError && caught.outcome === "denial",
    caught instanceof AdmissionError ? caught.outcome : `got ${caught && caught.name}`);

  caught = null;
  try {
    await waitForTelemostMeetingAdmission(
      makePage({ text: "Зал ожидания", visible: [] }), 0, {} as any);
  } catch (e) { caught = e; }
  check("timeout throws AdmissionError('lobby_timeout')",
    caught instanceof AdmissionError && caught.outcome === "lobby_timeout",
    caught instanceof AdmissionError ? caught.outcome : `got ${caught && caught.name}`);

  caught = null;
  const kicked: any = makePage({ text: "", visible: [] });
  kicked.__vexaTelemostTerminalCode = "ROOM_HAS_BEEN_CLOSED";
  try { await waitForTelemostMeetingAdmission(kicked, 60_000, {} as any); }
  catch (e) { caught = e; }
  check("latched media-engine code (ROOM_HAS_BEEN_CLOSED) → AdmissionError('denial')",
    caught instanceof AdmissionError && caught.outcome === "denial" && /ROOM_HAS_BEEN_CLOSED/.test(caught.message),
    caught ? caught.message : "no throw");

  caught = null;
  try {
    await waitForTelemostMeetingAdmission(
      makePage({ text: "Такого звонка нет. Возможно, в ссылке опечатка", visible: [] }), 60_000, {} as any);
  } catch (e) { caught = e; }
  check("«Такого звонка нет» → AdmissionError('denial'), never a retried join_failure",
    caught instanceof AdmissionError && caught.outcome === "denial",
    caught instanceof AdmissionError ? caught.outcome : `got ${caught && caught.name}`);

  const admitted = await waitForTelemostMeetingAdmission(
    makePage({ text: "Иван Петров", visible: [LEAVE] }), 60_000, {} as any);
  check("already in the call → resolves true", admitted === true);

  delete (globalThis as any).document;
  console.log(`\n=== summary: ${passed} passed, ${failed} failed ===`);
  process.exit(failed > 0 ? 1 : 0);
}

main().catch((e) => { console.error(e); process.exit(1); });
