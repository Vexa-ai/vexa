import { NextResponse } from "next/server";

// Force dynamic rendering to avoid Next.js fetch caching issues
export const dynamic = "force-dynamic";

interface HealthStatus {
  status: "ok" | "degraded" | "error";
  authMode: "direct" | "magic-link" | "google" | "oauth";
  checks: {
    smtp: { configured: boolean; optional: boolean; error?: string };
    googleOAuth: { configured: boolean; optional: boolean; error?: string };
    microsoftOAuth: { configured: boolean; optional: boolean; error?: string };
    adminApi: { configured: boolean; reachable: boolean; error?: string };
    vexaApi: { configured: boolean; reachable: boolean; error?: string };
  };
  missingConfig: string[];
}

/**
 * Health check endpoint - validates server configuration
 */
export async function GET() {
  const status: HealthStatus = {
    status: "ok",
    authMode: "direct", // Will be updated based on configured auth methods
    checks: {
      smtp: { configured: false, optional: true },
      googleOAuth: { configured: false, optional: true },
      microsoftOAuth: { configured: false, optional: true },
      adminApi: { configured: false, reachable: false },
      vexaApi: { configured: false, reachable: false },
    },
    missingConfig: [],
  };

  // Check Google OAuth configuration (optional - enables Google auth)
  const enableGoogleAuth = process.env.ENABLE_GOOGLE_AUTH;
  const googleClientId = process.env.GOOGLE_CLIENT_ID;
  const googleClientSecret = process.env.GOOGLE_CLIENT_SECRET;
  const nextAuthUrl = process.env.NEXTAUTH_URL;

  // Check if explicitly disabled
  if (enableGoogleAuth === "false" || enableGoogleAuth === "0") {
    status.checks.googleOAuth.error = "Google OAuth is disabled";
  } else if (googleClientId && googleClientSecret && nextAuthUrl) {
    status.checks.googleOAuth.configured = true;
    status.authMode = "google";
  } else {
    // Google OAuth is optional
    if (enableGoogleAuth === "true" || enableGoogleAuth === "1") {
      status.checks.googleOAuth.error = "Google OAuth is enabled but configuration is incomplete";
    } else {
      status.checks.googleOAuth.error = "Google OAuth not configured";
    }
  }

  // Check Microsoft OAuth configuration (optional - enables Microsoft auth)
  const enableMicrosoftAuth = process.env.ENABLE_MICROSOFT_AUTH;
  const microsoftClientId = process.env.MICROSOFT_CLIENT_ID;
  const microsoftClientSecret = process.env.MICROSOFT_CLIENT_SECRET;

  if (enableMicrosoftAuth === "false" || enableMicrosoftAuth === "0") {
    status.checks.microsoftOAuth.error = "Microsoft OAuth is disabled";
  } else if (microsoftClientId && microsoftClientSecret && nextAuthUrl) {
    status.checks.microsoftOAuth.configured = true;
    // Use "oauth" when both providers are configured, "google" when only Google
    if (status.checks.googleOAuth.configured) {
      status.authMode = "oauth";
    } else {
      status.authMode = "oauth";
    }
  } else {
    if (enableMicrosoftAuth === "true" || enableMicrosoftAuth === "1") {
      status.checks.microsoftOAuth.error = "Microsoft OAuth is enabled but configuration is incomplete";
    } else {
      status.checks.microsoftOAuth.error = "Microsoft OAuth not configured";
    }
  }

  // Check SMTP configuration (optional - enables magic link auth)
  const smtpHost = process.env.SMTP_HOST;
  const smtpUser = process.env.SMTP_USER;
  const smtpPass = process.env.SMTP_PASS;

  if (smtpHost && smtpUser && smtpPass) {
    status.checks.smtp.configured = true;
    // Only set to magic-link if no OAuth provider is configured (OAuth takes precedence)
    if (!status.checks.googleOAuth.configured && !status.checks.microsoftOAuth.configured) {
      status.authMode = "magic-link";
    }
  } else {
    // SMTP is optional - direct login mode will be used if no other auth is configured
    status.checks.smtp.error = "SMTP not configured";
  }

  // Check Admin API configuration
  const adminApiKey = process.env.VEXA_ADMIN_API_KEY;
  const adminApiUrl = process.env.VEXA_ADMIN_API_URL || process.env.VEXA_API_URL;

  if (adminApiKey && adminApiKey !== "your_admin_api_key_here") {
    status.checks.adminApi.configured = true;

    // Test Admin API reachability - check if the /admin/users endpoint exists
    if (adminApiUrl) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);

        const response = await fetch(`${adminApiUrl}/admin/users?limit=1`, {
          method: "GET",
          headers: { "X-Admin-API-Key": adminApiKey },
          signal: controller.signal,
        });
        clearTimeout(timeoutId);

        // Check response status
        if (response.status === 200) {
          status.checks.adminApi.reachable = true;
        } else if (response.status === 401) {
          status.checks.adminApi.reachable = true; // Server is up but key is wrong
          status.checks.adminApi.error = "Invalid admin API key";
        } else if (response.status === 403) {
          status.checks.adminApi.reachable = true;
          status.checks.adminApi.error = "Access forbidden";
        } else if (response.status === 404) {
          // Admin endpoints not found - likely only Bot Manager is deployed
          status.checks.adminApi.reachable = false;
          status.checks.adminApi.error = "Admin API endpoints not found. Ensure Vexa admin service is running.";
        } else if (response.status >= 500) {
          status.checks.adminApi.reachable = false;
          status.checks.adminApi.error = `Server error: ${response.status}`;
        } else {
          status.checks.adminApi.reachable = true;
        }
      } catch (error) {
        const err = error as Error;
        if (err.name === "AbortError") {
          status.checks.adminApi.error = "Connection timeout";
        } else {
          status.checks.adminApi.error = `Cannot reach API: ${err.message || "unknown error"}`;
        }
      }
    }
  } else {
    status.checks.adminApi.error = "Admin API key not configured";
    status.missingConfig.push("VEXA_ADMIN_API_KEY");
  }

  // Check Vexa API configuration
  const vexaApiUrl = process.env.VEXA_API_URL;

  if (vexaApiUrl) {
    status.checks.vexaApi.configured = true;

    // Test Vexa API reachability - check root endpoint
    try {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 5000);

      const response = await fetch(`${vexaApiUrl}/`, {
        method: "GET",
        signal: controller.signal,
      });
      clearTimeout(timeoutId);

      // Any response < 500 means server is reachable
      status.checks.vexaApi.reachable = response.status < 500;
      if (response.status >= 500) {
        status.checks.vexaApi.error = `Server error: ${response.status}`;
      }
    } catch (error) {
      const err = error as Error;
      if (err.name === "AbortError") {
        status.checks.vexaApi.error = "Connection timeout";
      } else {
        status.checks.vexaApi.error = `Cannot reach API: ${err.message || "unknown error"}`;
      }
    }
  } else {
    status.checks.vexaApi.error = "Vexa API URL not configured";
    status.missingConfig.push("VEXA_API_URL");
  }

  // Determine overall status
  // Only Admin API is required. SMTP is optional (enables magic-link, otherwise direct login).
  const hasAdminApi = status.checks.adminApi.configured && status.checks.adminApi.reachable;
  const hasVexaApi = status.checks.vexaApi.configured;

  if (!hasAdminApi) {
    // Admin API is required for authentication
    status.status = "error";
  } else if (!hasVexaApi || !status.checks.vexaApi.reachable) {
    // Vexa API is needed for full functionality but not login
    status.status = "degraded";
  }

  return NextResponse.json(status);
}
