/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-vnc-view.
 *
 * chromium only. The fixtures are static pages that mount the bundled REAL <VncView> — there is NO
 * backend. We serve them with a tiny stdlib-only static server (static-server.mjs) rather than file://
 * because Chromium blocks ESM `import` from file:// (origin "null"), and the pages load the bundle +
 * goldens as real modules. Serving over http mirrors the real stack.
 *
 * `globalSetup` rebuilds vnc-bundle.js from the brick source first, so every run exercises the current
 * component.
 */
import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname } from "node:path";

const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 4318);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  // rebuild vnc-bundle.js from the brick source before any test runs
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
  // tiny static server for this e2e dir — no app backend involved
  webServer: {
    command: "node static-server.mjs",
    url: BASE_URL,
    cwd: HERE,
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
