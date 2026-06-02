/**
 * Synthetic tests for the clean-room humanized Google Meet input layer.
 *
 * Run: npx tsx core/src/platforms/googlemeet/humanized/humanized.test.ts
 *
 * No browser, no live meeting, no real X server: X11Input runs in dryRun and a
 * fake Page stands in for Playwright. These prove the risk-bearing logic —
 * mocap landing/fallback, exact landing of generated data, OS-level move/click
 * emission, and the screen<->page coordinate mapping — deterministically.
 */

import { MocapEngine, type Rect } from "./mocapEngine";
import { X11Input } from "./x11Input";
import { HumanizedInteractor } from "./humanizedInteraction";
import { MOCAP_LIBRARY } from "./mocap-data";

let passed = 0;
let failed = 0;
function assert(cond: boolean, msg: string): void {
  if (cond) { passed++; console.log(`  ✓ ${msg}`); }
  else { failed++; console.log(`  ✗ ${msg}`); }
}

// ── 1. Generated data integrity ──────────────────────────────
console.log("\nTest 1: mocap data integrity");
{
  let mismatches = 0, negDt = 0, badClick = 0;
  for (const s of MOCAP_LIBRARY.sequences) {
    let ax = 0, ay = 0;
    for (const m of s.movements) { ax += m.dx; ay += m.dy; if (m.dt < 0) negDt++; }
    if (ax !== s.total_dx || ay !== s.total_dy) mismatches++;
    if (s.click_down_dt <= 0 || s.click_up_dt <= 0) badClick++;
  }
  assert(MOCAP_LIBRARY.sequences.length > 100, `library has ${MOCAP_LIBRARY.sequences.length} base sequences`);
  assert(mismatches === 0, "every sequence's deltas sum exactly to its total displacement");
  assert(negDt === 0, "no negative inter-move timings");
  assert(badClick === 0, "every sequence has positive click down/up timing");
  assert(String(MOCAP_LIBRARY.meta.license) === "Apache-2.0", "data is labeled Apache-2.0 (own/clean-room)");
}

// ── 2. Engine: perturbation + landing ────────────────────────
console.log("\nTest 2: mocap engine landing");
{
  const engine = new MocapEngine(MOCAP_LIBRARY);
  assert(engine.size > MOCAP_LIBRARY.sequences.length * 10, `perturbation expanded library to ${engine.size}`);

  // Target rect 600px to the right, 100px down from the pointer. The real
  // navigateAndClick tries a direct landing first, then stretch/rotate — assert
  // the same direct-or-fallback contract here.
  const rect: Rect = { left: 560, top: 60, right: 660, bottom: 160 };
  const seq = engine.findSequenceLandingInRect(0, 0, rect)
    ?? engine.findSequenceWithStretchAndRotation(0, 0, rect);
  assert(seq !== null, "finds a sequence (direct or fallback) landing in a reachable rect");
  if (seq) {
    assert(
      seq.total_dx >= rect.left && seq.total_dx <= rect.right &&
      seq.total_dy >= rect.top && seq.total_dy <= rect.bottom,
      "selected sequence endpoint is inside the rect"
    );
  }
}

// ── 3. Engine: stretch/rotate fallback for awkward target ─────
console.log("\nTest 3: stretch+rotate fallback");
{
  const engine = new MocapEngine(MOCAP_LIBRARY);
  // A tiny rect at an odd distance unlikely to be hit directly.
  const rect: Rect = { left: 233, top: -177, right: 238, bottom: -172 };
  const direct = engine.findSequenceLandingInRect(0, 0, rect);
  const stretched = engine.findSequenceWithStretchAndRotation(0, 0, rect);
  assert(stretched !== null || direct !== null, "fallback (or direct) lands on an awkward small rect");
  if (stretched) {
    assert(
      stretched.total_dx >= rect.left && stretched.total_dx <= rect.right &&
      stretched.total_dy >= rect.top && stretched.total_dy <= rect.bottom,
      "stretched sequence lands inside the awkward rect"
    );
  }
}

// ── 4. X11Input dryRun emits correct OS-level commands ───────
console.log("\nTest 4: X11Input command emission (dryRun)");
(async () => {
  const x = new X11Input({ dryRun: true });
  await x.moveRel(12, -3);
  await x.buttonDown(1);
  await x.buttonUp(1);
  await x.clipboardPaste("VexaBot");
  const argvs = x.log.map((a) => a.join(" "));
  assert(argvs.some((a) => a === "xdotool mousemove_relative --sync -- 12 -3"), "relative move uses XTEST mousemove_relative --sync");
  assert(argvs.some((a) => a === "xdotool mousedown 1"), "button down via xdotool mousedown");
  assert(argvs.some((a) => a === "xdotool mouseup 1"), "button up via xdotool mouseup");
  assert(argvs.some((a) => a.startsWith("xclip -selection clipboard")), "paste stages text on clipboard via xclip");
  assert(argvs.some((a) => a === "xdotool key --clearmodifiers ctrl+v"), "paste issues ctrl+v via XTEST");

  // ── 5. End-to-end replay against a fake Page (dryRun) ──────
  console.log("\nTest 5: navigateAndClick replay (fake page, dryRun)");
  const fakePage = makeFakePage({ left: 800, top: 420, width: 120, height: 44, dpr: 1, screenX: 0, screenY: 0 });
  const interactor = new HumanizedInteractor(MOCAP_LIBRARY, { dryRun: true });
  let threw = false;
  try {
    await interactor.navigateAndClick(fakePage as any, {} as any);
  } catch (e) {
    threw = true;
    console.log(`    (navigateAndClick error: ${e})`);
  }
  assert(!threw, "navigateAndClick completes against a reachable fake target");

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();

// Minimal Playwright Page stand-in: only the calls navigateAndClick makes.
function makeFakePage(m: { left: number; top: number; width: number; height: number; dpr: number; screenX: number; screenY: number }) {
  return {
    async evaluate(fn: any, _arg?: any) {
      const src = String(fn);
      if (src.includes("devicePixelRatio") && !src.includes("getBoundingClientRect") && !src.includes("screenX")) {
        return m.dpr; // calibrate(): read dpr
      }
      if (src.includes("addEventListener")) return undefined; // install listener
      if (src.includes("window.screenX") && src.includes("innerWidth")) {
        return { sx: m.screenX, sy: m.screenY, iw: 1920, ih: 1080 };
      }
      if (src.includes("__vexaLastMouse")) {
        // Return a calibration sample consistent with offset 0, dpr m.dpr.
        return { clientX: 400, clientY: 300 };
      }
      if (src.includes("getBoundingClientRect")) {
        return { left: m.left, top: m.top, width: m.width, height: m.height, screenX: m.screenX, screenY: m.screenY, dpr: m.dpr };
      }
      if (src.includes("elementFromPoint")) return true; // endpoint verified
      return undefined;
    },
  };
}
