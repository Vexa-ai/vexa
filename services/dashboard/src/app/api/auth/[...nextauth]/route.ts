import NextAuth, { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import AzureADProvider from "next-auth/providers/azure-ad";
import { cookies } from "next/headers";
import { findUserByEmail, createUser, createUserToken } from "@/lib/vexa-admin-api";
import { getRegistrationConfig, validateEmailForRegistration } from "@/lib/registration";
import { getVexaCookieOptions } from "@/lib/cookie-utils";

// Check if Google OAuth is enabled
const isGoogleAuthEnabled = () => {
  const enableGoogleAuth = process.env.ENABLE_GOOGLE_AUTH;
  if (enableGoogleAuth === "false" || enableGoogleAuth === "0") return false;
  const hasConfig = !!(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET && process.env.NEXTAUTH_URL);
  if (enableGoogleAuth === "true" || enableGoogleAuth === "1") return hasConfig;
  return hasConfig;
};

// Check if Microsoft OAuth is enabled
const isMicrosoftAuthEnabled = () => {
  const enableMicrosoftAuth = process.env.ENABLE_MICROSOFT_AUTH;
  if (enableMicrosoftAuth === "false" || enableMicrosoftAuth === "0") return false;
  const hasConfig = !!(process.env.MICROSOFT_CLIENT_ID && process.env.MICROSOFT_CLIENT_SECRET && process.env.NEXTAUTH_URL);
  if (enableMicrosoftAuth === "true" || enableMicrosoftAuth === "1") return hasConfig;
  return hasConfig;
};

export const authOptions: NextAuthOptions = {
  providers: [
    ...(isGoogleAuthEnabled()
      ? [
          GoogleProvider({
            clientId: process.env.GOOGLE_CLIENT_ID!,
            clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
          }),
        ]
      : []),
    ...(isMicrosoftAuthEnabled()
      ? [
          AzureADProvider({
            id: "microsoft",
            name: "Microsoft",
            clientId: process.env.MICROSOFT_CLIENT_ID!,
            clientSecret: process.env.MICROSOFT_CLIENT_SECRET!,
            tenantId: process.env.MICROSOFT_TENANT_ID || "common",
          }),
        ]
      : []),
  ],
  pages: {
    signIn: "/login",
    error: "/login",
  },
  callbacks: {
    async signIn({ user, account, profile }) {
      // This callback is called after successful OAuth but before session creation
      if ((account?.provider === "google" || account?.provider === "microsoft") && user.email) {
        try {
          // Step 1: Find or create user in Vexa Admin API
          let vexaUser;
          const findResult = await findUserByEmail(user.email);
          let isNewUser = false;

          if (findResult.success && findResult.data) {
            vexaUser = findResult.data;
          } else if (findResult.error?.code === "NOT_FOUND") {
            // Check registration restrictions
            const config = getRegistrationConfig();
            const validationError = validateEmailForRegistration(user.email, false, config);

            if (validationError) {
              console.error(`[NextAuth] Registration blocked for ${user.email}: ${validationError}`);
              return false; // Prevent sign-in
            }

            // Create new user
            const createResult = await createUser({
              email: user.email,
              name: user.name || user.email.split("@")[0],
            });

            if (!createResult.success || !createResult.data) {
              console.error(`[NextAuth] Failed to create user for ${user.email}:`, createResult.error);
              return false;
            }

            vexaUser = createResult.data;
            isNewUser = true;
          } else {
            console.error(`[NextAuth] Error finding user for ${user.email}:`, findResult.error);
            return false;
          }

          // Step 2: Create API token for the user
          const tokenResult = await createUserToken(vexaUser.id);

          if (!tokenResult.success || !tokenResult.data) {
            console.error(`[NextAuth] Failed to create token for ${user.email}:`, tokenResult.error);
            return false;
          }

          const apiToken = tokenResult.data.token;

          // Step 3: Set cookie (same as existing auth flow)
          const cookieStore = await cookies();
          cookieStore.set("vexa-token", apiToken, getVexaCookieOptions());

          // Store Vexa user info in the user object for the JWT callback
          (user as any).vexaUser = vexaUser;
          (user as any).vexaToken = apiToken;
          (user as any).isNewUser = isNewUser;

          return true;
        } catch (error) {
          console.error(`[NextAuth] Unexpected error during sign-in for ${user.email}:`, error);
          return false;
        }
      }

      return false; // Deny sign-in for other providers
    },
    async jwt({ token, user }) {
      // Persist the Vexa user data to the token
      if (user && (user as any).vexaUser) {
        token.vexaUser = (user as any).vexaUser;
        token.vexaToken = (user as any).vexaToken;
        token.isNewUser = (user as any).isNewUser;
      }
      return token;
    },
    async session({ session, token }) {
      // Add Vexa user data to the session
      if (token.vexaUser) {
        (session as any).vexaUser = token.vexaUser;
        (session as any).vexaToken = token.vexaToken;
        (session as any).isNewUser = token.isNewUser;
      }
      return session;
    },
    async redirect({ url, baseUrl }) {
      // Redirect to dashboard after successful sign-in
      if (url.startsWith(baseUrl)) {
        return url;
      }
      return `${baseUrl}/`;
    },
  },
  session: {
    strategy: "jwt",
  },
  secret: process.env.NEXTAUTH_SECRET || process.env.VEXA_ADMIN_API_KEY,
};

const handler = NextAuth(authOptions);

export { handler as GET, handler as POST };

