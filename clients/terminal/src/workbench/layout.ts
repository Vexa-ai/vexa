/** term-workbench/layout — the LayoutService over dockview. Owns the DockviewApi: opens/focuses a
 *  surface's panels (main + sidebar + aux + bottom), tracks the active surface, and persists/restores
 *  the whole arrangement to localStorage. The shell just hosts <DockviewReact> and hands us the api. */
import { createServiceId, createStore, type ObservableStore } from "../platform";
import { registry, type Slot, type SurfaceId } from "../contributions";
import type { DockviewApi } from "dockview-react";

const LS_KEY = "vexa.terminal.layout.v1";

export interface LayoutState {
  activeSurface: SurfaceId | null;
}

export interface LayoutService {
  store: ObservableStore<LayoutState>;
  /** Wire the dockview api once the shell is ready; restores the saved layout or opens `fallback`. */
  attach(api: DockviewApi, fallback: SurfaceId): void;
  /** Open (or focus, if already open) a surface's panels. */
  openSurface(id: SurfaceId): void;
  /** Toggle the active surface's primary sidebar panel. */
  toggleSidebar(): void;
  /** Clear the saved layout + re-open the active surface fresh. */
  resetLayout(): void;
}

export const LayoutServiceId = createServiceId<LayoutService>("layout");

const surfaceOf = (panelId: string): SurfaceId => panelId.split(":")[0];

export function createLayoutService(): LayoutService {
  const store = createStore<LayoutState>({ activeSurface: null });
  let api: DockviewApi | null = null;

  const persist = () => {
    if (!api) return;
    try { localStorage.setItem(LS_KEY, JSON.stringify(api.toJSON())); } catch { /* storage may be unavailable */ }
  };

  const addSlot = (id: SurfaceId, slot: Slot, position?: object) => {
    if (!api) return;
    if (slot !== "main" && registry.views(slot, id).length === 0) return;
    const pid = `${id}:${slot}`;
    if (api.getPanel(pid)) return;
    const label = registry.get(id)?.activity?.label ?? id;
    api.addPanel({
      id: pid,
      component: "viewport",
      title: slot === "main" ? label : `${label} ${slot === "primarySidebar" ? "·" : slot === "auxiliaryBar" ? "›" : "▾"}`,
      params: { surfaceId: id, slot },
      ...(position ? { position } : {}),
    } as Parameters<DockviewApi["addPanel"]>[0]);
  };

  const open = (id: SurfaceId) => {
    if (!api) return;
    const mainId = `${id}:main`;
    const existing = api.getPanel(mainId);
    if (existing) { existing.api.setActive(); return; }
    addSlot(id, "main");
    addSlot(id, "primarySidebar", { direction: "left", referencePanel: mainId });
    addSlot(id, "auxiliaryBar", { direction: "right", referencePanel: mainId });
    addSlot(id, "panel", { direction: "below", referencePanel: mainId });
    api.getPanel(mainId)?.api.setActive();
  };

  return {
    store,
    attach(dvApi, fallback) {
      api = dvApi;
      let restored = false;
      try {
        const saved = localStorage.getItem(LS_KEY);
        if (saved) { api.fromJSON(JSON.parse(saved)); restored = api.panels.length > 0; }
      } catch { restored = false; }
      if (!restored) open(fallback);

      const sync = () => {
        const a = api?.activePanel;
        store.set({ activeSurface: a ? surfaceOf(a.id) : null });
      };
      api.onDidActivePanelChange(sync);
      api.onDidLayoutChange(persist);
      sync();
    },
    openSurface: open,
    toggleSidebar() {
      const a = store.getState().activeSurface;
      if (!a || !api) return;
      const p = api.getPanel(`${a}:primarySidebar`);
      if (p) p.api.close();
      else addSlot(a, "primarySidebar", { direction: "left", referencePanel: `${a}:main` });
    },
    resetLayout() {
      try { localStorage.removeItem(LS_KEY); } catch { /* noop */ }
      if (!api) return;
      const a = store.getState().activeSurface ?? "chat";
      api.clear();
      open(a);
    },
  };
}
