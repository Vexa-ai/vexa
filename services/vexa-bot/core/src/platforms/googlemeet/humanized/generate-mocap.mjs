// Clean-room synthetic mocap generator for Vexa (Apache-2.0).
//
// Produces a library of human-like pointer trajectories used by the humanized
// Google Meet join path. This is NOT derived from any third-party recording or
// source: every sample is generated here from a minimum-jerk velocity model
// plus bowed-path and jitter perturbations, with a seeded PRNG so the committed
// data set is reproducible.
//
// Output schema (our own, see ./types.ts -> MocapLibrary):
//   { meta, sequences: [ { movements: [{dx,dy,dt}], total_dx, total_dy,
//                          click_down_dt, click_up_dt } ] }
// dt is seconds between relative moves; dx/dy are integer pixel deltas whose
// running sum equals total_dx/total_dy exactly.
//
// Regenerate with:  node generate-mocap.mjs > mocap-data.ts

// --- seeded PRNG (mulberry32) so committed data is deterministic ---
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const rng = mulberry32(0x5645_5841); // "VEXA"
const rand = (lo, hi) => lo + (hi - lo) * rng();
const randSign = () => (rng() < 0.5 ? -1 : 1);

// Minimum-jerk normalized position profile s(tau), tau in [0,1].
const minJerk = (t) => t * t * t * (10 - 15 * t + 6 * t * t);

/**
 * Generate one human-like trajectory covering displacement (DX, DY).
 * Straight-ish min-jerk path with a slight perpendicular bow, per-step jitter,
 * slow-in/slow-out dt, a small overshoot near the end and a corrective settle.
 */
function generateTrajectory(DX, DY) {
  const dist = Math.hypot(DX, DY) || 1;
  // Fitts-like duration; longer moves take longer, with mild variance.
  const T = Math.min(1.15, Math.max(0.24, 0.16 + dist / 1500)) * rand(0.85, 1.18);
  const avgDt = rand(0.010, 0.014);
  const n = Math.max(14, Math.round(T / avgDt));

  // Unit vector + perpendicular for the bow.
  const ux = DX / dist;
  const uy = DY / dist;
  const px = -uy;
  const py = ux;
  const bowAmp = dist * rand(0.015, 0.05) * randSign();

  // Overshoot: aim slightly past the target, then settle back.
  const overshoot = rand(0.0, 0.06);
  const aimX = DX * (1 + overshoot);
  const aimY = DY * (1 + overshoot);

  const movements = [];
  let prevX = 0;
  let prevY = 0;
  let accDx = 0;
  let accDy = 0;

  // Travel + overshoot phase.
  for (let i = 1; i <= n; i++) {
    const tau = i / n;
    const s = minJerk(tau);
    const bow = Math.sin(Math.PI * tau) * bowAmp;
    const jitterX = rand(-0.7, 0.7);
    const jitterY = rand(-0.7, 0.7);
    const cx = aimX * s + px * bow + jitterX;
    const cy = aimY * s + py * bow + jitterY;
    const dx = Math.round(cx - prevX);
    const dy = Math.round(cy - prevY);
    prevX += dx;
    prevY += dy;
    accDx += dx;
    accDy += dy;
    // slow-in/slow-out: dt larger at the extremes of the move
    const edge = 1 - Math.sin(Math.PI * tau); // 0 mid, ~1 at ends
    const dt = avgDt * (0.7 + 0.9 * edge) * rand(0.8, 1.25);
    if (dx !== 0 || dy !== 0) movements.push({ dx, dy, dt: round4(dt) });
  }

  // Corrective settle back onto the exact target (a few small moves).
  const settle = Math.round(rand(2, 4));
  for (let j = 1; j <= settle; j++) {
    const fx = j / settle;
    const targetX = DX * fx + DX * 0; // converge to DX
    const targetY = DY * fx;
    // we want the FINAL position == (DX,DY); interpolate remaining error
    const remX = DX - accDx;
    const remY = DY - accDy;
    const dx = Math.round(remX / (settle - j + 1));
    const dy = Math.round(remY / (settle - j + 1));
    accDx += dx;
    accDy += dy;
    if (dx !== 0 || dy !== 0) {
      movements.push({ dx, dy, dt: round4(avgDt * rand(1.4, 2.4)) });
    }
  }
  // Guarantee exact landing.
  const fixDx = DX - accDx;
  const fixDy = DY - accDy;
  if (fixDx !== 0 || fixDy !== 0) {
    movements.push({ dx: fixDx, dy: fixDy, dt: round4(avgDt * rand(1.2, 2.0)) });
    accDx += fixDx;
    accDy += fixDy;
  }

  return {
    movements,
    total_dx: accDx,
    total_dy: accDy,
    click_down_dt: round4(rand(0.05, 0.11)),
    click_up_dt: round4(rand(0.06, 0.13)),
  };
}

const round4 = (x) => Math.round(x * 10000) / 10000;

// Coverage grid: a spread of radii x angles so the engine can find a base
// sequence landing in most target rects; runtime rotation/stretch fills gaps.
const RADII = [130, 280, 450, 650, 880, 1130, 1400];
const ANGLE_STEP_DEG = 15;
const VARIANTS_PER_POINT = 1;

const sequences = [];
for (const r of RADII) {
  for (let a = 0; a < 360; a += ANGLE_STEP_DEG) {
    const rad = (a * Math.PI) / 180;
    const DX = Math.round(Math.cos(rad) * r);
    const DY = Math.round(Math.sin(rad) * r);
    for (let v = 0; v < VARIANTS_PER_POINT; v++) {
      sequences.push(generateTrajectory(DX, DY));
    }
  }
}
// A few short fine-positioning samples (small rects, near targets).
for (let k = 0; k < 24; k++) {
  const r = rand(20, 130);
  const rad = rand(0, 2 * Math.PI);
  sequences.push(
    generateTrajectory(Math.round(Math.cos(rad) * r), Math.round(Math.sin(rad) * r))
  );
}

const library = {
  meta: {
    generator: "vexa clean-room procedural mocap (generate-mocap.mjs)",
    license: "Apache-2.0",
    provenance:
      "Synthetic. Generated from a min-jerk motion model with seeded PRNG. " +
      "Not derived from any third-party recording or source.",
    seed: "0x56455841",
    radii: RADII,
    angle_step_deg: ANGLE_STEP_DEG,
    count: sequences.length,
  },
  sequences,
};

const header =
  "// GENERATED FILE — do not edit by hand. Regenerate with:\n" +
  "//   node generate-mocap.mjs > mocap-data.ts\n" +
  "// Synthetic, clean-room mocap library (Apache-2.0). No third-party data.\n" +
  'import type { MocapLibrary } from "./types";\n\n' +
  "export const MOCAP_LIBRARY: MocapLibrary = ";
process.stdout.write(header + JSON.stringify(library, null, 0) + ";\n");
