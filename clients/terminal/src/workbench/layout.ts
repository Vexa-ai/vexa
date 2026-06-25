/** term-workbench/layout (v2) — the structured-shell LayoutService.
 *  Left = which LIST is active. Center = dockview TABS. Right = persistent chat, grounded by activeTab. */
import { createServiceId, createStore, type ObservableStore } from "../platform";
import type { DockviewApi } from "dockview-react";

const LS_DOCK = "vexa.terminal.dock.v3";
const LS_LIST = "vexa.terminal.activeList.v1";

export interface RightContext { kind: string; params?: Record<string, unknown>; }
export interface ActiveTab { kind: string; params: Record<string, unknown>; }

export interface TabDescriptor {
  id: string;
  title: string;
  kind: string;
  params?: Record<string, unknown>;
  /** optional contextual metadata carried with persisted descriptors */
  context?: RightContext | null;
}

export interface LayoutState {
  activeList: string;
  leftCollapsed: boolean;
  rightCollapsed: boolean;
  context: RightContext | null;
  activeTab: ActiveTab | null;
}

export interface LayoutService {
  store: ObservableStore<LayoutState>;
  attach(api: DockviewApi): void;
  detach(api: DockviewApi): void;
  openTab(d: TabDescriptor): void;
  /** open the tab in the single shared PREVIEW slot (reused on the next single-click) */
  openPreview(d: TabDescriptor): void;
  closeTab(id: string): void;
  /** optional contextual metadata for legacy descriptors */
  setContext(ctx: RightContext | null): void;
  setActiveTab(tab: ActiveTab | null): void;
  setActiveList(id: string): void;
  toggleLeft(): void;
  toggleRight(): void;
  showRight(): void;
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
    activeTab: null,
  });
  let api: DockviewApi | null = null;
  // the single shared preview slot. We keep one dockview panel (fixed id) and swap its
  // params/title in place so a single-click reuses ONE tab. `previewLogicalId` records
  // which descriptor currently lives in the slot (so pinning that same thing promotes it).
  const PREVIEW_PANEL = "__preview__";
  let previewLogicalId: string | null = null;

  const persist = () => { if (api) writeLS(LS_DOCK, JSON.stringify(api.toJSON())); };

  const panelParams = (d: TabDescriptor, preview: boolean) =>
    ({ kind: d.kind, p: d.params ?? {}, ctx: d.context ?? null, preview });

  /** drop the preview slot bookkeeping (the panel itself is handled by the caller). */
  const forgetPreview = () => { previewLogicalId = null; };

  /** addPanel that survives a corrupted/stale grid. A layout restored via
   *  fromJSON can land with no resolvable active group (dockview then throws
   *  "invalid location" on a bare addPanel). Reset the grid and retry once so a
   *  click/navigation always opens its tab instead of crashing the surface. */
  const addPanelSafe = (opts: Parameters<DockviewApi["addPanel"]>[0]) => {
    if (!api) return;
    try { api.addPanel(opts); return; }
    catch { /* grid in a bad state — try resetting it below */ }
    try { api.clear(); forgetPreview(); api.addPanel(opts); }
    catch { api = null; }  // api is disposed (unmount/HMR) — drop the dead ref; the next onReady re-attaches.
  };

  return {
    store,
    attach(dvApi) {
      api = dvApi;
      try { const s = readLS(LS_DOCK); if (s) api.fromJSON(JSON.parse(s)); } catch { /* stale layout — start empty */ }
      // TabHost publishes the active panel's params. Clear when the grid has no active panel.
      api.onDidActivePanelChange((p) => { if (!p) store.set((st) => ({ ...st, context: null, activeTab: null })); });
      // if the preview panel goes away (closed/reset), forget the slot.
      api.onDidRemovePanel((p) => { if (p.id === PREVIEW_PANEL) forgetPreview(); });
      api.onDidLayoutChange(persist);
    },
    // DockviewReact disposes its api on unmount (navigation/HMR). Drop our cached
    // ref so openTab/openPreview don't operate on a disposed grid; the remount's
    // onReady re-attaches a fresh one. Guard the identity in case a new api
    // already attached before the old one's cleanup runs.
    detach(dvApi) { if (api === dvApi) { api = null; forgetPreview(); } },
    setContext(ctx) { store.set((st) => ({ ...st, context: ctx })); },
    setActiveTab(tab) { store.set((st) => ({ ...st, activeTab: tab })); },
    openTab(d) {
      if (!api) return;
      // pinning the thing currently in preview → promote it: drop the preview slot so the
      // single shared tab is free again, and open the content as a persistent panel.
      if (previewLogicalId === d.id) { api.getPanel(PREVIEW_PANEL)?.api.close(); forgetPreview(); }
      const existing = api.getPanel(d.id);
      if (existing) { existing.api.setActive(); return; }
      addPanelSafe({
        id: d.id,
        component: "tab",
        title: d.title,
        params: panelParams(d, false),
      });
    },
    openPreview(d) {
      if (!api) return;
      // already pinned as a real tab? just activate it — don't spawn a preview duplicate.
      const pinned = api.getPanel(d.id);
      if (pinned) { pinned.api.setActive(); return; }
      const slot = api.getPanel(PREVIEW_PANEL);
      if (slot) {
        // REPLACE in place: same dockview panel, swap kind/params/title. TabHost re-renders
        // on the params change and its effects re-fetch (no remount-thrash).
        slot.api.updateParameters(panelParams(d, true));
        slot.api.setTitle(d.title);
        slot.api.setActive();
      } else {
        addPanelSafe({
          id: PREVIEW_PANEL,
          component: "tab",
          title: d.title,
          params: panelParams(d, true),
        });
      }
      previewLogicalId = d.id;
    },
    closeTab(id) { api?.getPanel(id)?.api.close(); },
    setActiveList(id) { store.set((s) => ({ ...s, activeList: id })); writeLS(LS_LIST, id); },
    toggleLeft() { store.set((s) => ({ ...s, leftCollapsed: !s.leftCollapsed })); },
    toggleRight() { store.set((s) => ({ ...s, rightCollapsed: !s.rightCollapsed })); },
    showRight() { store.set((s) => ({ ...s, rightCollapsed: false })); },
    resetLayout() {
      try { localStorage.removeItem(LS_DOCK); } catch { /* noop */ }
      forgetPreview();
      api?.clear();
      store.set((s) => ({ ...s, context: null, activeTab: null }));
    },
  };
}
