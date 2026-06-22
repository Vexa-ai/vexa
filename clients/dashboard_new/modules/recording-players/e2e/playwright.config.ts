/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-recording-players.
 *
 * chromium only. The fixture is a static page (players-render.html) that mounts the REAL AudioPlayer +
 * VideoPlayer (bundled from brick source) with golden props — there is NO backend to boot. We serve it
 * over http (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file://.
 *
 * `globalSetup` rebuilds fixtures/players-bundle.js from the brick source first, so the page always
 * mounts the current components.
 */
import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
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
    cwd: HERE,
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
