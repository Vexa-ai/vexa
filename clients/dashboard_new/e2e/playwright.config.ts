/**
 * playwright.config.ts — the L4 harness config.
 *
 * chromium only. The fixture is a static page (ws-render.html) that runs the bundled dash-ws brick over
 * a FakeWsTransport — there is NO backend to boot. We serve it with a tiny stdlib-only static server
 * (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file:// (origin
 * "null"), and the page loads the bundle + goldens as real modules. Serving over http also mirrors how
 * the page will be served on the real stack, so the harness graduates with no page change.
 *
 * `globalSetup` rebuilds fixtures/ws-bundle.js from the brick source first, so the page always exercises
 * the current dash-ws.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 4317);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  // live*.spec.ts are the L4-against-the-real-stack harnesses (live.config.ts) — they need a running
  // app + backend (and live-realbot spawns a real bot), so the offline golden harness never picks them up.
  testIgnore: /live.*\.spec\.ts$/,
  // rebuild fixtures/ws-bundle.js from the dash-ws source before any test runs
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
  // tiny static server for the fixtures dir — no app backend involved
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
