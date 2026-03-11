import { create } from "zustand";
import { persist } from "zustand/middleware";

interface AdminAuthState {
  isAdminAuthenticated: boolean;
  isVerifying: boolean;
  error: string | null;

  // Actions
  verifyAdminToken: (token: string) => Promise<boolean>;
  logout: () => void;
  clearError: () => void;
}

export const useAdminAuthStore = create<AdminAuthState>()(
  persist(
    (set) => ({
      isAdminAuthenticated: false,
      isVerifying: false,
      error: null,

      verifyAdminToken: async (token: string): Promise<boolean> => {
        set({ isVerifying: true, error: null });

        try {
          const response = await fetch("/api/auth/admin-verify", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ token }),
          });

          const data = await response.json();

          if (!response.ok) {
            set({
              isVerifying: false,
              error: data.error || "Invalid admin token",
              isAdminAuthenticated: false
            });
            return false;
          }

          set({
            isAdminAuthenticated: true,
            isVerifying: false,
            error: null
          });
          return true;
        } catch (error) {
          set({
            isVerifying: false,
            error: (error as Error).message,
            isAdminAuthenticated: false
          });
          return false;
        }
      },

      logout: () => {
        // Clear server-side admin cookie
        fetch("/api/auth/admin-logout", { method: "POST" });
        set({
          isAdminAuthenticated: false,
          error: null
        });
      },

      clearError: () => set({ error: null }),
    }),
    {
      name: "vexa-admin-auth",
      partialize: (state) => ({
        isAdminAuthenticated: state.isAdminAuthenticated,
      }),
    }
  )
);
