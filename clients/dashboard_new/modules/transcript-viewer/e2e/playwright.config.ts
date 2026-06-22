/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-transcript-viewer.
 *
 * chromium only. The fixture is a static page (render.html) that mounts the bundled TranscriptViewer
 * component over golden props — there is NO backend to boot. We serve it with a tiny stdlib-only static
 * server (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file://
 * (origin "null"), and the page loads the bundle + goldens as real modules.
 *
 * `globalSetup` rebuilds fixtures/render-bundle.js from the brick source first, so the page always
 * exercises the current component.
 */
import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

// Anchor every relative path to THIS file's directory (the e2e/ dir), so the config behaves
// identically whether Playwright is invoked from e2e/ or from the package root (npm test sets
// cwd to the package root). Without this, `node static-server.mjs` / globalSetup / testDir resolve
// against the wrong cwd and the webServer silently never starts → page.goto("/") "invalid URL".
const HERE = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT ?? 4318);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: HERE,
  testMatch: /.*\.spec\.ts$/,
  // rebuild fixtures/render-bundle.js from the component source before any test runs
  globalSetup: join(HERE, "build-bundle.mjs"),
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
