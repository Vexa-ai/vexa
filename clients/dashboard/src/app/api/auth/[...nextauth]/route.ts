import NextAuth, { NextAuthOptions } from "next-auth";
import GoogleProvider from "next-auth/providers/google";
import AzureADProvider from "next-auth/providers/azure-ad";
import { cookies } from "next/headers";
import { findUserByEmail, createUser, createUserToken } from "@/lib/vexa-admin-api";

function isSecureRequest(): boolean {
  return process.env.NEXTAUTH_URL?.startsWith("https://") ||
         process.env.DASHBOARD_URL?.startsWith("https://") ||
         false;
}
import { getRegistrationConfig, validateEmailForRegistration } from "@/lib/registration";

// Check if Google OAuth is enabled
const isGoogleAuthEnabled = () => {
  // Check if explicitly disabled via flag
  const enableGoogleAuth = process.env.ENABLE_GOOGLE_AUTH;
  if (enableGoogleAuth === "false" || enableGoogleAuth === "0") {
    return false;
  }

  // If flag is set to true, or flag is not set (default), check if config is present
  const hasConfig = !!(
    process.env.GOOGLE_CLIENT_ID &&
    process.env.GOOGLE_CLIENT_SECRET &&
    process.env.NEXTAUTH_URL
  );

  // If flag is explicitly "true", require config to be present
  if (enableGoogleAuth === "true" || enableGoogleAuth === "1") {
    return hasConfig;
  }

  // Default: enable if config is present (backward compatible)
  return hasConfig;
};

const isAzureAdAuthEnabled = () => {
  const enableAzureAdAuth = process.env.ENABLE_AZURE_AD_AUTH;
  if (enableAzureAdAuth === "false" || enableAzureAdAuth === "0") {
    return false;
  }

  const hasConfig = !!(
    process.env.AZURE_AD_CLIENT_ID &&
    process.env.AZURE_AD_CLIENT_SECRET &&
    process.env.AZURE_AD_TENANT_ID &&
    process.env.NEXTAUTH_URL
  );

  if (enableAzureAdAuth === "true" || enableAzureAdAuth === "1") {
    return hasConfig;
  }

  return hasConfig;
};

const getAppBasePath = (): string => {
  const rawUrl = process.env.NEXTAUTH_URL;
  if (!rawUrl) return "";
  try {
    const path = new URL(rawUrl).pathname.replace(/\/$/, "");
    return path.endsWith("/api/auth") ? path.slice(0, -"/api/auth".length) : path;
  } catch {
    return "";
  }
};

const buildAppPath = (suffix: string): string => {
  const basePath = getAppBasePath();
  if (!basePath || basePath === "/") {
    return suffix;
  }
  return `${basePath}${suffix}`;
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
    ...(isAzureAdAuthEnabled()
      ? [
          AzureADProvider({
            clientId: process.env.AZURE_AD_CLIENT_ID!,
            clientSecret: process.env.AZURE_AD_CLIENT_SECRET!,
            tenantId: process.env.AZURE_AD_TENANT_ID!,
          }),
        ]
      : []),
  ],
  pages: {
    signIn: buildAppPath("/login"),
    error: buildAppPath("/login"),
  },
  callbacks: {
    async signIn({ user, account, profile }) {
      // This callback is called after successful OAuth but before session creation
      if (
        (account?.provider === "google" || account?.provider === "azure-ad") &&
        user.email
      ) {
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
          cookieStore.set("vexa-token", apiToken, {
            httpOnly: true,
            secure: isSecureRequest(),
            sameSite: "lax",
            maxAge: 60 * 60 * 24 * 30, // 30 days
            path: "/",
          });

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

