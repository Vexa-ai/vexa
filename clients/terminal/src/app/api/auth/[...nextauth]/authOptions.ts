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
import { AUTH_COOKIE, SETUP_GATE_REFUSAL, USER_INFO_COOKIE, findOrCreateUserToken, mintFirstVisitScaffold, signinAllowed } from "../adminApi";

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

/** The one-shot hand-off from `signIn` to `redirect` (F42). Short-lived and httpOnly: it carries a
 *  URL for the next hop of THIS sign-in and nothing else, and it is consumed the moment it is read
 *  so a second navigation cannot land on a spent arrival. */
const ARRIVAL_COOKIE = "vexa-arrival";

async function readArrival(): Promise<string | null> {
  try {
    const store = await cookies();
    const url = store.get(ARRIVAL_COOKIE)?.value;
    if (!url) return null;
    store.delete(ARRIVAL_COOKIE);
    return url;
  } catch {
    // Reading (or clearing) cookies is not always permitted in every context this callback runs in.
    // A missing arrival is not a failure — it lands where it always did.
    return null;
  }
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

      // The company-layer setup gate — the THIRD door, and it needs the guard for exactly the same
      // reason the other two do: findOrCreateUserToken() on the next line CREATES the user as a side
      // effect, so a refusal placed after it would leave a ghost account for somebody who was never
      // admitted. OAuth is not a weaker door than the magic link, it is a door with a different
      // proof, and the gate is about WHO may enter, not HOW they proved it.
      //
      // NextAuth turns a `false` here into a redirect to `pages.error` ("/"), which is the sign-in
      // card — and that card reads /api/auth/instance and renders the same refusal sentence. So the
      // user does see why, even though this callback has no channel of its own to say it in.
      const gate = await signinAllowed(user.email.toLowerCase());
      if (!gate.allowed) {
        // eslint-disable-next-line no-console
        console.warn(`[terminal-auth] ${provider} sign-in refused for ${user.email}: ${SETUP_GATE_REFUSAL} (reason: ${gate.reason})`);
        return false;
      }

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

      // THE ARRIVAL (F42) — the third door mints one too. This callback answers a boolean and has no
      // say in where NextAuth then sends the browser, so the minted path is handed to `redirect`
      // below through a one-shot cookie rather than through a second sign-in mechanism.
      //
      // A FAILED MINT IS NOT A FAILED SIGN-IN: it is logged, no cookie is written, and `redirect`
      // lands them exactly where it always did. Returning `false` here would refuse an authenticated
      // person over a missing conversation, which is not the trade — see mintFirstVisitScaffold.
      try {
        const minted = await mintFirstVisitScaffold(result.user.email, result.user.id);
        if (minted.ok && minted.data?.url) {
          cookieStore.set(ARRIVAL_COOKIE, minted.data.url, { ...opts, httpOnly: true, maxAge: 120 });
        } else {
          // eslint-disable-next-line no-console
          console.error(`[terminal-auth] first-visit scaffold mint failed for ${result.user.email}: ${minted.ok ? "no url" : minted.error}`);
        }
      } catch (e) {
        // eslint-disable-next-line no-console
        console.error(`[terminal-auth] first-visit scaffold mint threw for ${result.user.email}: ${(e as Error).message}`);
      }
      return true;
    },
    // Land back on the workbench, HONORING a same-origin callbackUrl so an invite link's ?invite=<token>
    // survives the OAuth round-trip (InviteRedeemer then redeems it post-auth). Off-origin URLs → baseUrl.
    //
    // …and when the round-trip named NO destination of its own, land on the arrival `signIn` just
    // minted (F42). A callbackUrl that says something — an invite, a meeting, a `?s=` somebody
    // already minted for this person — always wins: minting a second arrival over the one they were
    // sent would open a conversation on top of the one they clicked.
    async redirect({ url, baseUrl }) {
      const named = url !== baseUrl && url !== `${baseUrl}/` && url !== "/";
      if (!named) {
        const arrival = await readArrival();
        if (arrival) return arrival;
      }
      if (url.startsWith("/")) return `${baseUrl}${url}`;
      if (url.startsWith(baseUrl)) return url;
      return baseUrl;
    },
  },
};
