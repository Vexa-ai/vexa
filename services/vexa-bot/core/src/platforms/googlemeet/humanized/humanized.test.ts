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

import * as fs from "fs";
import * as path from "path";
import { MocapEngine, type Rect } from "./mocapEngine";
import { X11Input } from "./x11Input";
import { HumanizedInteractor } from "./humanizedInteraction";
import { MOCAP_LIBRARY } from "./mocap-data";
import { googleJoinButtonSelectors, googleNameInputSelectors } from "../selectors";

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
  await x.typeText("VexaBot", 55);
  const argvs = x.log.map((a) => a.join(" "));
  assert(argvs.some((a) => a === "xdotool mousemove_relative --sync -- 12 -3"), "relative move uses XTEST mousemove_relative --sync");
  assert(argvs.some((a) => a === "xdotool mousedown 1"), "button down via xdotool mousedown");
  assert(argvs.some((a) => a === "xdotool mouseup 1"), "button up via xdotool mouseup");
  assert(argvs.some((a) => a === "xdotool type --clearmodifiers --delay 55 -- VexaBot"), "text entry uses XTEST xdotool type (not the hang-prone clipboard path)");

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
  // The click must land AFTER the move replay (endpoint verified before press).
  const x11 = (interactor as any).x11 as X11Input;
  const argv5 = x11.log.map((a) => a.join(" "));
  const lastMove = argv5.map((a, i) => (a.startsWith("xdotool mousemove") ? i : -1)).filter((i) => i >= 0).pop() ?? -1;
  const down = argv5.findIndex((a) => a === "xdotool mousedown 1");
  assert(down > lastMove && lastMove >= 0, "button-down is emitted only after the pointer has been moved onto the target");

  // ── 6. Endpoint verification refuses an off-target click ──────
  // Force a wrong offset so the real (simulated) pointer can never reach the
  // live rect; the interactor must THROW (no-fallbacks: fail loud, never click
  // blindly) and emit a miss screenshot.
  console.log("\nTest 6: endpoint verification refuses an off-target click");
  {
    let shotReason = "";
    const offPage = makeFakePage({ left: 800, top: 420, width: 120, height: 44, dpr: 1, screenX: 0, screenY: 0, forceOccluded: true });
    const guard = new HumanizedInteractor(MOCAP_LIBRARY, {
      dryRun: true,
      onMissScreenshot: async (_p, reason) => { shotReason = reason; },
    });
    let missThrew = false;
    try {
      await guard.navigateAndClick(offPage as any, {} as any);
    } catch (e) {
      missThrew = true;
    }
    assert(missThrew, "navigateAndClick THROWS when the pointer cannot be verified inside the target");
    assert(/verification FAILED/.test(shotReason), "a miss-screenshot reason is surfaced before abandoning the click");
    const gx11 = (guard as any).x11 as X11Input;
    assert(!gx11.log.some((a) => a.join(" ") === "xdotool mousedown 1"), "no button-down is emitted on a verification failure");
  }

  // ── 7. Locale-agnostic selectors match a Hungarian mock DOM ───
  console.log("\nTest 7: locale-agnostic join/name selectors (hu UI)");
  {
    const selectorsSrc = fs.readFileSync(
      path.join(__dirname, "..", "selectors.ts"),
      "utf-8"
    );
    // (a) join button: a locale-agnostic structural selector precedes the
    // English text selector, so a Hungarian "Kérvényezés a csatlakozásra"
    // button (no English text) still matches by jsname/structure.
    const joinBlock = selectorsSrc.slice(
      selectorsSrc.indexOf("googleJoinButtonSelectors"),
      selectorsSrc.indexOf("googleCameraButtonSelectors")
    );
    const joinJsnameIdx = joinBlock.indexOf("button[jsname]");
    // Match the English *selector literals* (not the prose comment, which also
    // mentions the English text).
    const joinEnglishIdx = joinBlock.indexOf("has-text(\"Ask to join\")");
    assert(joinJsnameIdx >= 0, "join selectors include a locale-agnostic jsname/structure selector");
    assert(joinJsnameIdx < joinEnglishIdx, "locale-agnostic join selector precedes the English-text fallback");

    // (b) name field: structural input selector precedes the English aria-label.
    const nameBlock = selectorsSrc.slice(
      selectorsSrc.indexOf("googleNameInputSelectors"),
      selectorsSrc.indexOf("googleMeetingContainerSelectors")
    );
    const nameStructIdx = Math.min(
      ...["input[jsname]", 'input[type="text"]:not('].map((s) => {
        const i = nameBlock.indexOf(s);
        return i < 0 ? Number.MAX_SAFE_INTEGER : i;
      })
    );
    const nameEnglishIdx = nameBlock.indexOf('aria-label="Your name"');
    assert(nameStructIdx !== Number.MAX_SAFE_INTEGER, "name selectors include a locale-agnostic structural selector");
    assert(nameStructIdx < nameEnglishIdx, "locale-agnostic name selector precedes the English aria-label fallback");

    // (c) DOM match: build a Hungarian lobby (no English text anywhere) and
    // confirm the FIRST (locale-agnostic) join + name selectors match it via a
    // minimal CSS matcher. This is the localized-DOM check the English-literal
    // selectors failed (prod ids 13951 13952 14018 14153).
    const huJoinBtn: El = {
      tag: "button",
      attrs: { jsname: "Qx7uuf" },
      text: "Kérvényezés a csatlakozásra", // "Ask to join" (hu) — no English
      children: [{ tag: "span", attrs: {}, text: "Kérvényezés a csatlakozásra", children: [] }],
    };
    const huNameInput: El = {
      tag: "input",
      attrs: { jsname: "YPqjbf", type: "text", "aria-label": "A neved" }, // "Your name" (hu)
      text: "",
      children: [],
    };
    const firstJoinSel = googleJoinButtonSelectors[0];
    const firstNameSel = googleNameInputSelectors[0];
    assert(matchesSelector(huJoinBtn, firstJoinSel), `hu join button matches locale-agnostic selector '${firstJoinSel}'`);
    assert(matchesSelector(huNameInput, firstNameSel), `hu name input matches locale-agnostic selector '${firstNameSel}'`);
    // Negative control: the English-text selector must NOT match the hu button.
    assert(!matchesSelector(huJoinBtn, 'button:has-text("Ask to join")'), "English-text selector does NOT match the hu join button (the regression)");
  }

  // ── 8. join.ts fails LOUD (screenshot) when no selector matches ─
  console.log("\nTest 8: join.ts fails loud when controls are missing");
  {
    const joinSrc = fs.readFileSync(path.join(__dirname, "..", "join.ts"), "utf-8");
    assert(/waitForAnySelector/.test(joinSrc), "join.ts uses an ordered multi-selector resolver");
    const fn = joinSrc.slice(joinSrc.indexOf("export async function waitForAnySelector"));
    assert(/screenshot/.test(fn) && /throw new Error/.test(fn), "selector resolver screenshots + throws on total miss (no silent skip)");
  }

  console.log(`\n${passed} passed, ${failed} failed`);
  if (failed > 0) process.exit(1);
})();

// ── Minimal CSS-selector matcher for the structural localized-DOM check ──────
// Supports exactly the predicate forms our locale-agnostic selectors use:
//   tag, [attr], [attr="v"], [attr*="v"], :not([...]), :has(tag),
//   and the Playwright :has-text("...") pseudo (English-only fallbacks). Not a
//   general CSS engine — just enough to faithfully prove match/non-match on the
//   selectors this pack ships. Combinators (descendant " ") match the leaf.
interface El { tag: string; attrs: Record<string, string>; text: string; children: El[] }

function matchesSelector(el: El, selector: string): boolean {
  // Descendant combinator: only the right-most compound must match `el`
  // (our DOM mocks are the leaf control itself).
  const compound = selector.trim().split(/\s+(?![^\[]*\])/).pop() as string;
  // tag prefix
  let rest = compound;
  const tagMatch = /^[a-zA-Z]+/.exec(rest);
  if (tagMatch) {
    if (el.tag !== tagMatch[0]) return false;
    rest = rest.slice(tagMatch[0].length);
  }
  // walk the remaining predicates
  const predicate = /\[([^\]]+)\]|:not\(([^)]+)\)|:has\(([^)]+)\)|:has-text\("([^"]+)"\)/g;
  let m: RegExpExecArray | null;
  while ((m = predicate.exec(rest)) !== null) {
    if (m[1] !== undefined) { if (!attrMatch(el, m[1])) return false; }
    else if (m[2] !== undefined) { if (matchesSelector(el, m[2])) return false; } // :not()
    else if (m[3] !== undefined) { // :has(child)
      if (!el.children.some((c) => matchesSelector(c, m![3]))) return false;
    } else if (m[4] !== undefined) { // :has-text — English fallback path
      if (!el.text.includes(m[4])) return false;
    }
  }
  return true;
}

function attrMatch(el: El, body: string): boolean {
  const star = body.match(/^([\w-]+)\*="([^"]*)"$/);
  if (star) return (el.attrs[star[1]] ?? "").includes(star[2]);
  const eq = body.match(/^([\w-]+)="([^"]*)"$/);
  if (eq) return el.attrs[eq[1]] === eq[2];
  const present = body.match(/^([\w-]+)$/);
  if (present) return present[1] in el.attrs;
  return false;
}

// Minimal Playwright Page stand-in: only the calls navigateAndClick makes.
function makeFakePage(m: { left: number; top: number; width: number; height: number; dpr: number; screenX: number; screenY: number; forceOccluded?: boolean }) {
  return {
    async waitForTimeout(_ms: number) { /* no-op in tests */ },
    async screenshot(_opts: any) { /* no-op in tests */ },
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
        // Calibration sample consistent with offset 0, dpr m.dpr.
        return { clientX: 400, clientY: 300 };
      }
      if (src.includes("getBoundingClientRect")) {
        return { left: m.left, top: m.top, width: m.width, height: m.height, screenX: m.screenX, screenY: m.screenY, dpr: m.dpr };
      }
      // elementFromPoint: the target resolves unless we force it occluded (the
      // off-target / covered-button case the verification must catch).
      if (src.includes("elementFromPoint")) return !m.forceOccluded;
      return undefined;
    },
  };
}
