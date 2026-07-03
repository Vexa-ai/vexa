/** PaletteService — the ⌘K command-palette open/close state (observable). The palette UI subscribes;
 *  the `palette.toggle` command + the ⌘K keybinding flip it. */
import { createServiceId, createStore, type ObservableStore } from "../platform";

export interface PaletteService {
  store: ObservableStore<{ open: boolean }>;
  open(): void;
  close(): void;
  toggle(): void;
}

export const PaletteServiceId = createServiceId<PaletteService>("palette");

export function createPaletteService(): PaletteService {
  const store = createStore({ open: false });
  return {
    store,
    open() { store.set({ open: true }); },
    close() { store.set({ open: false }); },
    toggle() { store.set((s) => ({ open: !s.open })); },
  };
}
