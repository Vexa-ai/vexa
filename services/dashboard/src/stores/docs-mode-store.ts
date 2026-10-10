import { create } from "zustand";
import { persist } from "zustand/middleware";

interface DocsModeState {
  enabled: boolean;
  toggle: () => void;
  setEnabled: (enabled: boolean) => void;
}

export const useDocsModeStore = create<DocsModeState>()(
  persist(
    (set) => ({
      enabled: false,
      toggle: () => set((state) => ({ enabled: !state.enabled })),
      setEnabled: (enabled: boolean) => set({ enabled }),
    }),
    {
      name: "vexa-docs-mode",
    }
  )
);

