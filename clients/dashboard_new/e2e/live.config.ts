/**
 * live.config.ts — the L4 against the REAL stack (not the offline brick harness).
 *
 * This config drives a real browser against a RUNNING `dashboard_new` (`next start`, default :3002)
 * that is wired to a real backend (gateway + redis + meeting-api). It boots NO server of its own — the
 * app + stack are stood up by `run-live-l4.sh` first. The spec publishes golden ws.v1 frames to the
 * real redis and asserts the real browser renders them, proving the wired path end-to-end (the gate
 * that earns the human walk). Kept separate from `playwright.config.ts` (the offline golden harness).
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: /live.*\.spec\.ts$/,
  timeout: 60_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.DASH_URL || "http://localhost:3002",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
