import { chromium } from "playwright";

const dashboardUrl = process.env.DASHBOARD_URL || "http://localhost:3001";
const email = process.env.DASHBOARD_TEST_EMAIL || "test@vexa.ai";
const configuredMeetingId = process.env.DASHBOARD_MEETING_ID || "";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
});

try {
  const context = await browser.newContext({
    baseURL: dashboardUrl,
    viewport: { width: 1280, height: 800 },
  });
  const page = await context.newPage();
  const errors = [];
  const failedResponses = [];

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

  await page.goto("/login", { waitUntil: "domcontentloaded", timeout: 30000 });

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

  if (!login.ok || !login.body?.success) {
    throw new Error(`direct login failed: HTTP ${login.status} ${JSON.stringify(login.body).slice(0, 300)}`);
  }

  await page.goto("/meetings", { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1000);

  const bodyText = await page.locator("body").innerText({ timeout: 10000 });
  const authFailureText =
    bodyText.includes("Authentication failed") ||
    bodyText.includes("Your session may have expired") ||
    bodyText.includes("Please log in again");
  const loginRedirect = new URL(page.url()).pathname.includes("/login");
  const hasMeetingsShell = bodyText.includes("Meetings") || bodyText.includes("meeting transcriptions");

  if (authFailureText || loginRedirect || !hasMeetingsShell) {
    throw new Error(
      `meetings page not authenticated: redirected=${loginRedirect} hasShell=${hasMeetingsShell} text=${bodyText
        .replace(/\s+/g, " ")
        .slice(0, 300)}`
    );
  }

  const listResult = await page.evaluate(async () => {
    const response = await fetch("/api/vexa/meetings?limit=1&offset=0", { credentials: "include" });
    const body = await response.json().catch(() => ({}));
    return { ok: response.ok, status: response.status, body };
  });
  if (!listResult.ok) {
    throw new Error(`meetings API failed before detail probe: HTTP ${listResult.status} ${JSON.stringify(listResult.body).slice(0, 300)}`);
  }
  const meetingId = configuredMeetingId || listResult.body?.meetings?.[0]?.id;
  if (!meetingId) {
    throw new Error("meetings API returned no meeting id for detail probe");
  }

  await page.goto(`/meetings/${meetingId}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.waitForTimeout(1000);

  const detailText = await page.locator("body").innerText({ timeout: 10000 });
  const detailFailed =
    detailText.includes("Invalid API key") ||
    detailText.includes("Something went wrong") ||
    detailText.includes("Authentication failed") ||
    detailText.includes("Your session may have expired") ||
    new URL(page.url()).pathname.includes("/login");
  const hasDetailShell =
    detailText.includes("Meeting Info") ||
    detailText.includes("Transcript") ||
    detailText.includes("Recording") ||
    detailText.includes("Status History");

  if (detailFailed || !hasDetailShell) {
    throw new Error(
      `meeting detail not authenticated: id=${meetingId} hasShell=${hasDetailShell} text=${detailText
        .replace(/\s+/g, " ")
        .slice(0, 400)}`
    );
  }

  const relevantFailures = failedResponses.filter((line) => !line.includes("/api/auth/me") || line.startsWith("401 "));
  if (relevantFailures.length > 0) {
    throw new Error(`unexpected authenticated-route HTTP failures: ${relevantFailures.slice(0, 5).join(" | ")}`);
  }
  if (errors.length > 0) {
    throw new Error(`browser errors: ${errors.slice(0, 5).join(" | ")}`);
  }

  console.log(`PASS ${dashboardUrl}/meetings and /meetings/${meetingId} authenticated browser routes loaded`);
} finally {
  await browser.close();
}
