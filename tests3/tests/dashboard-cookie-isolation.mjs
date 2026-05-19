import { chromium } from "playwright";

const urls = (process.env.DASHBOARD_URLS || "http://localhost:3100,http://localhost:3001")
  .split(",")
  .map((url) => url.trim())
  .filter(Boolean);
const email = process.env.DASHBOARD_TEST_EMAIL || "test@vexa.ai";

if (urls.length < 2) {
  throw new Error("DASHBOARD_URLS must contain at least two dashboard origins");
}

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
});

const normalizeText = (text) => text.replace(/\s+/g, " ").slice(0, 300);

try {
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });
  const errors = [];
  const failedResponses = [];

  async function newTrackedPage() {
    const page = await context.newPage();
    page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") {
        errors.push(`console: ${msg.text()}`);
      }
    });
    page.on("response", (response) => {
      const status = response.status();
      if (status === 401 || status >= 500) {
        failedResponses.push(`${status} ${response.url()}`);
      }
    });
    return page;
  }

  for (const dashboardUrl of urls) {
    const page = await newTrackedPage();
    await page.goto(`${dashboardUrl}/login`, { waitUntil: "domcontentloaded", timeout: 30000 });

    const login = await page.evaluate(async (loginEmail) => {
      const response = await fetch("/api/auth/send-magic-link", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ email: loginEmail }),
      });
      const body = await response.json().catch(() => ({}));
      return { ok: response.ok, status: response.status, body };
    }, email);

    await page.close();

    if (!login.ok || !login.body?.success) {
      throw new Error(`direct login failed for ${dashboardUrl}: HTTP ${login.status} ${JSON.stringify(login.body).slice(0, 300)}`);
    }
  }

  const cookies = await context.cookies(urls);
  const tokenCookies = cookies
    .filter((cookie) => cookie.name.startsWith("vexa-token"))
    .map((cookie) => cookie.name)
    .sort();
  const uniqueTokenCookies = [...new Set(tokenCookies)];

  if (uniqueTokenCookies.includes("vexa-token")) {
    throw new Error(`default shared vexa-token cookie is still present: ${uniqueTokenCookies.join(", ")}`);
  }
  if (uniqueTokenCookies.length < urls.length) {
    throw new Error(`expected at least ${urls.length} isolated token cookies, got: ${uniqueTokenCookies.join(", ") || "(none)"}`);
  }

  for (const dashboardUrl of urls) {
    const page = await newTrackedPage();
    await page.goto(`${dashboardUrl}/meetings`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForTimeout(1000);

    const bodyText = await page.locator("body").innerText({ timeout: 10000 });
    const authFailureText =
      bodyText.includes("Authentication failed") ||
      bodyText.includes("Your session may have expired") ||
      bodyText.includes("Please log in again");
    const loginRedirect = new URL(page.url()).pathname.includes("/login");
    const hasMeetingsShell = bodyText.includes("Meetings") || bodyText.includes("meeting transcriptions");

    await page.close();

    if (authFailureText || loginRedirect || !hasMeetingsShell) {
      throw new Error(
        `${dashboardUrl}/meetings not authenticated after cross-login: redirected=${loginRedirect} hasShell=${hasMeetingsShell} text=${normalizeText(bodyText)}`
      );
    }
  }

  const relevantFailures = failedResponses.filter((line) => line.startsWith("401 ") || !line.includes("/api/auth/me"));
  if (relevantFailures.length > 0) {
    throw new Error(`unexpected authenticated-route HTTP failures: ${relevantFailures.slice(0, 8).join(" | ")}`);
  }
  if (errors.length > 0) {
    throw new Error(`browser errors: ${errors.slice(0, 8).join(" | ")}`);
  }

  console.log(`PASS isolated auth cookies ${uniqueTokenCookies.join(", ")} kept ${urls.join(" and ")} authenticated`);
} finally {
  await browser.close();
}
