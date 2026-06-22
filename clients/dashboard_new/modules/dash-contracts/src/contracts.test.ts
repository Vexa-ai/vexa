/**
 * L1 — pins this brick to the SEALED contracts. Exit 1 on any failure.
 *
 *   1. Every ws.v1 golden (`core/gateway/contracts/ws.v1/golden/*.json`) conforms to its shape
 *      (filename prefix `<Shape>.<case>.json` → `#/$defs/<Shape>`), like ws.v1/validate.mjs.
 *   2. A couple api.v1 goldens conform to `#/components/schemas/<Shape>`.
 *   3. The exported TS types actually PARSE a MeetingStatus + a Transcript golden (the consumed
 *      frames the dashboard reads), proving src/index.ts mirrors the wire shapes.
 */
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

import { validateWsFrame, validateApiShape, apiIdentity } from "./validate.ts";
import type { MeetingStatusFrame, TranscriptFrame, MeetingStatus } from "./index.ts";

const HERE = dirname(fileURLToPath(import.meta.url));

function findContractsDir(): string {
  for (const root of ["core/gateway/contracts", "gateway/contracts"]) {
    let dir = HERE;
    while (true) {
      const p = join(dir, root);
      if (existsSync(p)) return p;
      const parent = dirname(dir);
      if (parent === dir) break;
      dir = parent;
    }
  }
  throw new Error("could not locate core/gateway/contracts walking up from " + HERE);
}

const CONTRACTS = findContractsDir();
let failed = 0;
const ok = (label: string, cond: boolean, detail = "") => {
  console.log(`  ${cond ? "✓" : "✗"} ${label}${cond ? "" : detail ? " — " + detail : ""}`);
  if (!cond) failed++;
};

// ── 1) every ws.v1 golden conforms ──────────────────────────────────────────────────────────────
console.log("ws.v1 goldens:");
const wsGoldenDir = join(CONTRACTS, "ws.v1", "golden");
const wsGoldens = readdirSync(wsGoldenDir).filter((n) => n.endsWith(".json"));
let wsCount = 0;
for (const f of wsGoldens) {
  const shape = f.split(".")[0];
  const data = JSON.parse(readFileSync(join(wsGoldenDir, f), "utf8"));
  const { valid, errors } = validateWsFrame(shape, data);
  ok(`${f} ≡ ${shape}`, valid, errors);
  wsCount++;
}
ok(`loaded ws.v1 goldens (${wsCount} > 0)`, wsCount > 0);

// ── 2) a couple api.v1 goldens conform ──────────────────────────────────────────────────────────
console.log("api.v1 goldens:");
const apiGoldenDir = join(CONTRACTS, "api.v1", "golden");
const apiPicks = ["MeetingResponse.example.json", "TranscriptionResponse.example.json"];
for (const f of apiPicks) {
  const shape = f.split(".")[0];
  const path = join(apiGoldenDir, f);
  if (!existsSync(path)) {
    ok(`api.v1 golden ${f} exists`, false);
    continue;
  }
  const data = JSON.parse(readFileSync(path, "utf8"));
  const { valid, errors } = validateApiShape(shape, data);
  ok(`${f} ≡ ${shape}`, valid, errors);
}
ok(
  `api.v1 identity (Vexa API Gateway 1.5.0, got "${apiIdentity.title}" ${apiIdentity.version})`,
  apiIdentity.title === "Vexa API Gateway" && apiIdentity.version === "1.5.0",
);

// ── 3) the TS types parse the consumed frames ───────────────────────────────────────────────────
console.log("TS types parse goldens:");

const statusJson = JSON.parse(
  readFileSync(join(wsGoldenDir, "MeetingStatus.active.json"), "utf8"),
) as MeetingStatusFrame;
ok("MeetingStatusFrame.type == meeting.status", statusJson.type === "meeting.status");
const status: MeetingStatus | string = statusJson.payload.status;
ok(`MeetingStatusFrame.payload.status read ("${status}")`, status === "active");

const transcriptJson = JSON.parse(
  readFileSync(join(wsGoldenDir, "Transcript.bundle.json"), "utf8"),
) as TranscriptFrame;
ok("TranscriptFrame.type == transcript", transcriptJson.type === "transcript");
const firstSeg = (transcriptJson.confirmed ?? [])[0];
ok(
  `TranscriptFrame.confirmed[0].text read ("${firstSeg?.text}")`,
  typeof firstSeg?.text === "string" && firstSeg.text.length > 0,
);

// ── verdict ─────────────────────────────────────────────────────────────────────────────────────
console.log(
  failed
    ? `\ndash-contracts: ${failed} check(s) FAILED`
    : `\ndash-contracts: all checks pass (≡ sealed ws.v1 + api.v1)`,
);
process.exit(failed ? 1 : 0);
