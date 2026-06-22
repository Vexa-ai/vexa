/**
 * live.spec.ts — L4 against the REAL stack.
 *
 * A real browser loads the WIRED dashboard_new meeting-detail page; we then PUBLISH golden ws.v1
 * frames (`meeting.status` + `transcript`) to the REAL redis. The real gateway forwards them verbatim
 * over `/ws` to the app's `dash-ws` client → `dash-meeting-state` → the view bricks. We assert the
 * real DOM updates. This is the exact path the human walks — so green here ⇒ green for the human, the
 * thing the backend-only instruments (node-ws/curl) could never prove.
 *
 * The subscribe race is eliminated precisely: we wait until `PUBSUB CHANNELS` shows the gateway has
 * registered THIS meeting's status channel (i.e. the browser's WS opened + subscribed) before
 * publishing — no sleeps-and-hope.
 */
import { test, expect } from "@playwright/test";
import { execSync } from "node:child_process";

// How to reach the stack's redis, INCLUDING `-i` (docker exec needs it to read stdin). Local L4:
// `docker exec -i dashl4-redis-1`. bbb L4 (over the tunnel): `ssh bbb docker exec -i vexa-dash-redis-1`.
// The JSON payload is passed via STDIN (`redis-cli -x`), never as a shell arg — that's the only way it
// survives the two shell layers `ssh … docker exec …` re-parses it through (a bare quoted arg word-splits
// on the remote side). The channel + PUBSUB pattern carry no spaces and are kept glob-free for the same reason.
const REDIS_EXEC = process.env.REDIS_EXEC || `docker exec -i ${process.env.DASHL4_REDIS || "dashl4-redis-1"}`;
const DBID = process.env.MEETING_DBID || "1";
const PLATFORM = process.env.PLATFORM || "google_meet";
const NATIVE_ID = process.env.NATIVE_ID || "l4-render-01";
const TRANSCRIPT_TEXT = "Hello from the L4 render";

function redisCli(args: string): string {
  return execSync(`${REDIS_EXEC} redis-cli ${args}`, { encoding: "utf8" }).trim();
}
function publish(channel: string, payload: object): void {
  // -x reads the message (the JSON) from stdin → no shell-quoting of the payload across ssh+docker.
  execSync(`${REDIS_EXEC} redis-cli -x PUBLISH ${channel}`, { input: JSON.stringify(payload), encoding: "utf8" });
}

const STATUS_FRAME = {
  type: "meeting.status",
  meeting: { id: Number(DBID), platform: PLATFORM, native_id: NATIVE_ID },
  payload: { status: "active" },
  user_id: 1,
  ts: "2026-06-22T19:30:00Z",
};
const TRANSCRIPT_FRAME = {
  type: "transcript",
  speaker: "Alice",
  confirmed: [
    {
      text: TRANSCRIPT_TEXT,
      speaker: "Alice",
      start_time: 1.0,
      end_time: 3.0,
      segment_id: "l4-seg-1",
      absolute_start_time: "2026-06-22T19:30:01Z",
      language: "en",
      completed: true,
    },
  ],
  pending: [],
};

test("real browser renders live meeting.status + transcript through the wired app + real gateway", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (e) => pageErrors.push(String(e)));

  await page.goto(`/meetings/${DBID}`);

  // the meeting-detail mounted (the status pill is always rendered once the meeting loads)
  await expect(page.locator(".status-pill")).toBeVisible({ timeout: 20_000 });

  // wait until the gateway has SUBSCRIBED this meeting's status channel — i.e. the browser's WS opened,
  // subscribed, and the gateway registered the fan-in. Only then will a PUBLISH be forwarded.
  await expect
    .poll(() => redisCli("PUBSUB CHANNELS"), { timeout: 20_000, intervals: [400] })
    .toContain(`meeting:${DBID}:status`);

  // publish the goldens a few times (idempotent: status is set; transcript merges by segment_id)
  for (let i = 0; i < 3; i++) {
    publish(`bm:meeting:${DBID}:status`, STATUS_FRAME);
    publish(`tc:meeting:${DBID}:mutable`, TRANSCRIPT_FRAME);
    await page.waitForTimeout(400);
  }

  // ① live status rendered (the pill carries the forwarded meeting.status value)
  await expect(page.locator('.status-pill[data-status="active"]')).toBeVisible({ timeout: 10_000 });

  // ② live transcript rendered (the forwarded `transcript` bundle's segment text is in the DOM)
  await expect(page.getByText(TRANSCRIPT_TEXT)).toBeVisible({ timeout: 10_000 });

  // ③ the WS event log captured the forwarded frames (the unified dispatch fired)
  await expect(page.getByTestId("ws-event-log")).toContainText("status: active", { timeout: 5_000 });

  // ④ the recording player section is mounted (byte playback is validated in the human walk, with a real recording)
  await expect(page.getByText("Recording")).toBeVisible();

  expect(pageErrors, `uncaught page errors: ${pageErrors.join("; ")}`).toHaveLength(0);
});
