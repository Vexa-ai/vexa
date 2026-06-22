/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-meetings-list.
 *
 * chromium only. The fixture is a static page (list-render.html) that mounts the bundled MeetingsList
 * brick over golden props — there is NO backend to boot. We serve it with a tiny stdlib-only static
 * server (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file://
 * (origin "null"), and the page loads the bundle + goldens as real modules.
 *
 * `globalSetup` rebuilds fixtures/list-bundle.js from the brick source first, so the page always
 * exercises the current MeetingsList.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 4318);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  globalSetup: "./build-bundle.mjs",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "node static-server.mjs",
    url: BASE_URL,
    env: { PORT: String(PORT) },
    reuseExistingServer: !process.env.CI,
    timeout: 30_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
