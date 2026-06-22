/**
 * playwright.config.ts — the L4 harness config for @vexa/dash-chat.
 *
 * chromium only. The fixture is a static page (chat-render.html) that mounts the bundled dash-chat
 * <ChatPanel> over golden messages — there is NO backend to boot. We serve it with a tiny stdlib-only
 * static server (static-server.mjs) rather than file:// because Chromium blocks ESM `import` from file://
 * (origin "null"), and the page loads the bundle as a real module.
 *
 * `globalSetup` rebuilds chat-bundle.js from the brick source first, so the page always mounts the
 * current component.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.PORT ?? 4319);
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  // rebuild chat-bundle.js from the dash-chat source before any test runs
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
  // tiny static server for the e2e dir — no app backend involved
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
