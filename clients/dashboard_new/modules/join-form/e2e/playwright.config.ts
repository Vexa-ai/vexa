/**
 * playwright.config.ts — the L4 harness config for the JoinForm VIEW brick.
 *
 * chromium only. The fixture is a static page (join-form.html) that mounts the REAL JoinForm component
 * (bundled from the brick source) in a real DOM — there is NO backend to boot. We serve it with a tiny
 * stdlib-only static server (static-server.mjs) rather than file:// because Chromium blocks ESM
 * `import` from file:// (origin "null"). `globalSetup` rebuilds fixtures/form-bundle.js from the brick
 * source first, so the page always exercises the current component.
 *
 * testDir is "." (this e2e dir) so it only picks up *.spec.ts here.
 */
import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

// this config's own directory (the e2e/ dir) — used as the webServer cwd so `node static-server.mjs`
// resolves regardless of where `playwright test` is invoked from. (ESM has no __dirname.)
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
