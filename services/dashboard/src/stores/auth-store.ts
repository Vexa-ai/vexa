import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { VexaUser } from "@/types/vexa";

interface LoginResult {
  success: boolean;
  error?: string;
  mode?: "direct" | "magic-link";
  user?: VexaUser;
  token?: string;
  isNewUser?: boolean;
}

interface AuthState {
  user: VexaUser | null;
  token: string | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  didLogout: boolean; // true after explicit logout — prevents SSO redirect loop

  // Actions
  sendMagicLink: (email: string) => Promise<LoginResult>;
  setAuth: (user: VexaUser, token: string) => void;
  logout: () => void;
  setUser: (user: VexaUser | null) => void;
  setToken: (token: string | null) => void;
  checkAuth: () => Promise<void>;

  // Legacy login (kept for backwards compatibility)
  login: (email: string) => Promise<{ success: boolean; error?: string }>;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      token: null,
      isLoading: true, // Start true so auth-provider waits for checkAuth() before redirecting
      isAuthenticated: false,
      didLogout: false,

      sendMagicLink: async (email: string): Promise<LoginResult> => {
        set({ isLoading: true });
        try {
          const response = await fetch("/api/auth/send-magic-link", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
          });

          const data = await response.json();

          if (!response.ok) {
            set({ isLoading: false });
            return { success: false, error: data.error || "Failed to send magic link" };
          }

          // Check if this is a direct login response
          if (data.mode === "direct" && data.user && data.token) {
            // Direct login - set auth immediately
            set({
              user: data.user,
              token: data.token,
              isAuthenticated: true,
              isLoading: false,
              didLogout: false,
            });

            return {
              success: true,
              mode: "direct",
              user: data.user,
              token: data.token,
              isNewUser: data.isNewUser,
            };
          }

          // Magic link mode - user needs to check email
          set({ isLoading: false });
          return {
            success: true,
            mode: "magic-link",
          };
        } catch (error) {
          set({ isLoading: false });
          return { success: false, error: (error as Error).message };
        }
      },

      setAuth: (user: VexaUser, token: string) => {
        set({
          user,
          token,
          isAuthenticated: true,
          isLoading: false,
          didLogout: false,
        });
      },

      // Legacy login that directly authenticates (for backwards compatibility or dev mode)
      login: async (email: string) => {
        set({ isLoading: true });
        try {
          const response = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email }),
          });

          const data = await response.json();

          if (!response.ok) {
            set({ isLoading: false });
            return { success: false, error: data.error || "Login failed" };
          }

          set({
            user: data.user,
            token: data.token,
            isAuthenticated: true,
            isLoading: false,
            didLogout: false,
          });

          return { success: true };
        } catch (error) {
          set({ isLoading: false });
          return { success: false, error: (error as Error).message };
        }
      },

      logout: () => {
        // Clear server-side cookie
        fetch("/api/auth/logout", { method: "POST" });
        // Clear state
        set({
          user: null,
          token: null,
          isAuthenticated: false,
          didLogout: true,
        });
        // In hosted mode: redirect to webapp signout immediately
        // Don't wait for React re-render — avoids flash of "Invalid API token"
        const externalAuthUrl = process.env.NEXT_PUBLIC_EXTERNAL_AUTH_URL;
        if (externalAuthUrl) {
          const webappUrl = process.env.NEXT_PUBLIC_WEBAPP_URL || externalAuthUrl.replace(/\/account$/, '');
          window.location.href = `${webappUrl}/api/auth/signout?callbackUrl=${encodeURIComponent(webappUrl + '/signin')}`;
        }
      },

      setUser: (user) => set({ user, isAuthenticated: !!user }),
      setToken: (token) => set({ token }),

      checkAuth: async () => {
        const { token, user } = get();

        // Use localStorage as a quick pre-render hint so UI doesn't flash,
        // but ALWAYS verify with the server below.
        if (user && token) {
          set({ isAuthenticated: true, isLoading: false, didLogout: false });
        }

        // Always verify with server — localStorage may be stale (e.g. different
        // user logged in on the webapp since last dashboard visit).
        try {
          const response = await fetch("/api/auth/me");
          if (response.ok) {
            const meData = await response.json();

            // SSO path: /api/auth/me returns user+token from shared cookies
            if (meData.user && meData.token) {
              set({
                user: meData.user,
                token: meData.token,
                isAuthenticated: true,
                isLoading: false,
                didLogout: false,
              });
              return;
            }

            // OAuth callback path (Dashboard's own auth flow)
            if (!get().user || !get().token) {
              try {
                const oauthResponse = await fetch("/api/auth/oauth-callback");
                if (oauthResponse.ok) {
                  const oauthData = await oauthResponse.json();
                  if (oauthData.user && oauthData.token) {
                    set({
                      user: oauthData.user,
                      token: oauthData.token,
                      isAuthenticated: true,
                      isLoading: false,
                      didLogout: false,
                    });
                    return;
                  }
                }
              } catch {
                // OAuth callback failed, but cookie is still valid
              }
            }
            // Cookie is valid, but we don't have user info
            set({ isAuthenticated: true, isLoading: false, didLogout: false });
          } else {
            // Server returned 401 or error — clear stale localStorage
            set({ user: null, token: null, isAuthenticated: false, isLoading: false });
          }
        } catch {
          // Network error — if we have local data, keep it as fallback
          const current = get();
          if (current.user && current.token) {
            set({ isAuthenticated: true, isLoading: false });
          } else {
            set({ user: null, token: null, isAuthenticated: false, isLoading: false });
          }
        }
      },
    }),
    {
      name: "vexa-auth",
      partialize: (state) => ({
        user: state.user,
        token: state.token,
        isAuthenticated: state.isAuthenticated,
        didLogout: state.didLogout,
      }),
    }
  )
);
