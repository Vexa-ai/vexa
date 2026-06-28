/** NextAuth config for the terminal's OAuth broker (Google + Microsoft). Kept in its own module because
 *  an App Router `route.ts` may only export HTTP handlers — re-exporting `authOptions` from there fails
 *  Next's route-type check. NextAuth owns ONLY the OAuth dance; the terminal's auth contract is the
 *  httpOnly `vexa-token` + `vexa-user-info` cookies (read by server.mjs's WS proxy, api/proxyAuth.ts,
 *  and api/auth/me). So `signIn` ends by setting those exact cookies, via the SAME find-or-create+mint
 *  path the direct email login uses (findOrCreateUserToken in ../adminApi.ts). Mirrors the production
 *  webapp route, trimmed and reusing our admin client.
 *
 *  Providers self-gate on env presence, so a deploy with no OAuth creds simply exposes no providers
 *  (the email debug login still works). Credentials come from vexa-secrets (see .env.local).
 */
import { type AuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import AzureADProvider from "next-auth/providers/azure-ad";
import { cookies } from "next/headers";
import { AUTH_COOKIE, USER_INFO_COOKIE, findOrCreateUserToken } from "../adminApi";

const isGoogleEnabled = () => !!(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET);
const isMicrosoftEnabled = () =>
  !!(process.env.MICROSOFT_CLIENT_ID && process.env.MICROSOFT_CLIENT_SECRET);

/** Secure cookies behind HTTPS, mirroring the login route's isSecureRequest(). */
function isSecureRequest(): boolean {
  return (
    (process.env.NEXTAUTH_URL || "").startsWith("https://") ||
    (process.env.TERMINAL_URL || "").startsWith("https://") ||
    process.env.NODE_ENV === "production"
  );
}

export const authOptions: AuthOptions = {
  providers: [
    // prompt=select_account forces the provider's account chooser EVERY time, so after logout a user can
    // pick a different account instead of being silently re-authenticated into the last one (the provider
    // keeps its own session — without this it auto-returns the previous identity and logout looks broken).
    ...(isGoogleEnabled()
      ? [
          GoogleProvider({
            clientId: process.env.GOOGLE_CLIENT_ID!,
            clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
            authorization: { params: { prompt: "select_account" } },
          }),
        ]
      : []),
    ...(isMicrosoftEnabled()
      ? [
          AzureADProvider({
            id: "microsoft",
            name: "Microsoft",
            clientId: process.env.MICROSOFT_CLIENT_ID!,
            clientSecret: process.env.MICROSOFT_CLIENT_SECRET!,
            tenantId: process.env.MICROSOFT_TENANT_ID || "common",
            authorization: { params: { prompt: "select_account" } },
          }),
        ]
      : []),
  ],
  session: { strategy: "jwt" },
  secret: process.env.NEXTAUTH_SECRET,
  // Behind an HTTPS reverse proxy, NextAuth infers secure cookies from the URL; match that.
  useSecureCookies: isSecureRequest(),
  pages: { signIn: "/", error: "/" },
  callbacks: {
    /** The load-bearing step: turn a verified OAuth identity into the terminal's `vexa-token` +
     *  `vexa-user-info` cookies, reusing the admin-api find-or-create+mint flow. Deny on any failure. */
    async signIn({ user, account }) {
      const provider = account?.provider;
      if ((provider !== "google" && provider !== "microsoft") || !user.email) return false;

      const result = await findOrCreateUserToken(user.email.toLowerCase());
      if (!result.ok) {
        // eslint-disable-next-line no-console
        console.error(`[terminal-auth] ${provider} sign-in failed for ${user.email}: ${result.error}`);
        return false;
      }

      const opts = {
        httpOnly: true,
        secure: isSecureRequest(),
        sameSite: "lax" as const,
        maxAge: 60 * 60 * 24 * 30,
        path: "/",
      };
      const cookieStore = await cookies();
      cookieStore.set(AUTH_COOKIE, result.token, opts);
      const displayName = user.name || result.user.name || result.user.email.split("@")[0];
      cookieStore.set(USER_INFO_COOKIE, JSON.stringify({ email: result.user.email, name: displayName }), opts);
      return true;
    },
    // Land back on the workbench; AuthGate's /api/auth/me poll then sees the cookie and renders.
    async redirect({ baseUrl }) {
      return baseUrl;
    },
  },
};
