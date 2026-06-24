/** term-workbench/layout — the LayoutService: which surface is active + part visibility (observable). */
import { createServiceId, createStore, type ObservableStore } from "../platform";
import type { SurfaceId } from "../contributions";

export interface LayoutState { activeSurface: SurfaceId; railOpen: boolean; sidebarOpen: boolean; }
export interface LayoutService {
  store: ObservableStore<LayoutState>;
  setActiveSurface(id: SurfaceId): void;
  toggleRail(): void;
  toggleSidebar(): void;
}
export const LayoutServiceId = createServiceId<LayoutService>("layout");

export function createLayoutService(initialSurface: SurfaceId): LayoutService {
  const store = createStore<LayoutState>({ activeSurface: initialSurface, railOpen: true, sidebarOpen: true });
  return {
    store,
    setActiveSurface(id) { store.set((s) => ({ ...s, activeSurface: id })); },
    toggleRail() { store.set((s) => ({ ...s, railOpen: !s.railOpen })); },
    toggleSidebar() { store.set((s) => ({ ...s, sidebarOpen: !s.sidebarOpen })); },
  };
}
