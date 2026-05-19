import { chromium } from "playwright";

const dashboardUrl = process.env.DASHBOARD_URL || "http://localhost:3100";
const meetingId = process.env.DASHBOARD_MEETING_ID || "170";
const authCookieName = process.env.DASHBOARD_AUTH_COOKIE_NAME || "vexa-token-lite";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
});

try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  await context.addCookies([
    {
      name: authCookieName,
      value: "vxa_bot_invalid_stale_token",
      domain: "localhost",
      path: "/",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
      expires: Math.floor(Date.now() / 1000) + 3600,
    },
  ]);

  const page = await context.newPage();
  await page.goto(`${dashboardUrl}/login`, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.evaluate(() => {
    localStorage.setItem(
      "vexa-auth",
      JSON.stringify({
        state: {
          user: { id: 1, email: "test@vexa.ai", name: "test" },
          token: "vxa_bot_invalid_stale_token",
          isAuthenticated: true,
          didLogout: false,
        },
        version: 0,
      })
    );
  });

  const failures = [];
  page.on("response", async (response) => {
    if (response.status() === 401 || response.status() === 403 || response.status() >= 500) {
      let body = "";
      try {
        body = (await response.text()).replace(/\s+/g, " ").slice(0, 200);
      } catch {
        body = "";
      }
      failures.push(`${response.status()} ${response.url()} ${body}`);
    }
  });

  await page.goto(`${dashboardUrl}/meetings/${meetingId}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1500);

  const bodyText = await page.locator("body").innerText({ timeout: 10000 });
  if (bodyText.includes("Invalid API key") || bodyText.includes("Something went wrong")) {
    throw new Error(`stale auth leaked raw detail error: ${bodyText.replace(/\s+/g, " ").slice(0, 500)} failures=${failures.join(" | ")}`);
  }

  const path = new URL(page.url()).pathname;
  const redirectedToLogin = path.includes("/login");
  const authFailureShown = bodyText.includes("Authentication failed") || bodyText.includes("Please log in again");
  if (!redirectedToLogin && !authFailureShown) {
    throw new Error(`stale auth did not redirect or show auth failure; url=${page.url()} text=${bodyText.replace(/\s+/g, " ").slice(0, 500)}`);
  }

  console.log(`PASS stale ${authCookieName} did not leak Invalid API key on ${dashboardUrl}/meetings/${meetingId}`);
} finally {
  await browser.close();
}
