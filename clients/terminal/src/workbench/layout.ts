/** term-workbench/layout (v2) — the structured-shell LayoutService.
 *  Left = which LIST is active (top bar). Center = dockview TABS (openTab by kind; params serializable so
 *  the arrangement persists). Right = the CONTEXT carried by the active tab. Plus left/right collapse. */
import { createServiceId, createStore, type ObservableStore } from "../platform";
import type { DockviewApi } from "dockview-react";

const LS_DOCK = "vexa.terminal.dock.v2";
const LS_LIST = "vexa.terminal.activeList.v1";

export interface RightContext { kind: string; params?: Record<string, unknown>; }

export interface TabDescriptor {
  id: string;
  title: string;
  kind: string;
  params?: Record<string, unknown>;
  /** what the right pane shows while this tab is active */
  context?: RightContext | null;
}

export interface LayoutState {
  activeList: string;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  context: RightContext | null;
}

export interface LayoutService {
  store: ObservableStore<LayoutState>;
  attach(api: DockviewApi): void;
  openTab(d: TabDescriptor): void;
  closeTab(id: string): void;
  /** the active tab pushes its right-pane context here */
  setContext(ctx: RightContext | null): void;
  setActiveList(id: string): void;
  toggleLeft(): void;
  toggleRight(): void;
  resetLayout(): void;
}

export const LayoutServiceId = createServiceId<LayoutService>("layout");

const readLS = (k: string): string | null => { try { return localStorage.getItem(k); } catch { return null; } };
const writeLS = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* noop */ } };

export function createLayoutService(defaultList: string): LayoutService {
  const store = createStore<LayoutState>({
    activeList: readLS(LS_LIST) || defaultList,
    leftCollapsed: false,
    rightCollapsed: false,
    context: null,
  });
  let api: DockviewApi | null = null;

  const persist = () => { if (api) writeLS(LS_DOCK, JSON.stringify(api.toJSON())); };

  return {
    store,
    attach(dvApi) {
      api = dvApi;
      try { const s = readLS(LS_DOCK); if (s) api.fromJSON(JSON.parse(s)); } catch { /* stale layout — start empty */ }
      // the active tab pushes its context via setContext; here we only clear it when nothing is active.
      api.onDidActivePanelChange((p) => { if (!p) store.set((st) => ({ ...st, context: null })); });
      api.onDidLayoutChange(persist);
    },
    setContext(ctx) { store.set((st) => ({ ...st, context: ctx })); },
    openTab(d) {
      if (!api) return;
      const existing = api.getPanel(d.id);
      if (existing) { existing.api.setActive(); return; }
      api.addPanel({
        id: d.id,
        component: "tab",
        title: d.title,
        params: { kind: d.kind, p: d.params ?? {}, ctx: d.context ?? null },
      });
    },
    closeTab(id) { api?.getPanel(id)?.api.close(); },
    setActiveList(id) { store.set((s) => ({ ...s, activeList: id })); writeLS(LS_LIST, id); },
    toggleLeft() { store.set((s) => ({ ...s, leftCollapsed: !s.leftCollapsed })); },
    toggleRight() { store.set((s) => ({ ...s, rightCollapsed: !s.rightCollapsed })); },
    resetLayout() {
      try { localStorage.removeItem(LS_DOCK); } catch { /* noop */ }
      api?.clear();
      store.set((s) => ({ ...s, context: null }));
    },
  };
}
