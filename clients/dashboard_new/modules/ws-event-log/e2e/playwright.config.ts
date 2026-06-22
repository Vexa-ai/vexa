/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-ws-event-log.
 *
 * chromium only. The fixture is a static page (mount.html) that mounts the bundled WsEventLog component
 * over golden events — there is NO backend to boot. Served by a tiny stdlib static server
 * (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file://.
 *
 * `globalSetup` rebuilds fixtures/mount-bundle.js from the brick source first, so the page always mounts
 * the current component.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 4319);
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
    cwd: import.meta.dirname,
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
