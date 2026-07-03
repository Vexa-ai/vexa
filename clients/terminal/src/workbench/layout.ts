/** term-workbench/layout (v2) — the structured-shell LayoutService.
 *  Left = which LIST is active. Center = dockview TABS. Right = persistent chat, grounded by activeTab. */
import { createServiceId, createStore, type ObservableStore } from "../platform";
import type { DockviewApi } from "dockview-react";

const LS_DOCK = "vexa.terminal.dock.v3";
const LS_LIST = "vexa.terminal.activeList.v1";
const LS_SESSION = "vexa.terminal.activeSession.v1";

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
  /** which chat session the persistent right rail is showing */
  activeSession: string;
}

export interface LayoutService {
  store: ObservableStore<LayoutState>;
  attach(api: DockviewApi): void;
  detach(api: DockviewApi): void;
  openTab(d: TabDescriptor): void;
  /** open the tab in the single shared PREVIEW slot (reused on the next single-click) */
  openPreview(d: TabDescriptor): void;
  closeTab(id: string): void;
  /** open the tab in a SPLIT next to the active panel (link-from-doc semantics: never
   *  replace the doc the user clicked in). Reuses the other group when one exists. */
  openTabBeside(d: TabDescriptor): void;
  /** swap WHAT an existing panel shows in place (Obsidian-style in-pane navigation):
   *  update its params + title without opening a new tab. `panelId` is the dockview
   *  panel id (may be the preview slot). */
  retargetTab(panelId: string, d: TabDescriptor): void;
  /** Chrome-style tab pin: a pinned tab cannot be closed until unpinned. Stored in the
   *  panel params, so it persists reloads with the dock layout. Pinning the preview slot
   *  promotes it to a real panel first. */
  pinTab(panelId: string): void;
  unpinTab(panelId: string): void;
  /** restore the UI state (dock layout + active list) from before the last navigation.
   *  Returns false when the history is empty. */
  goBack(): boolean;
  /** optional contextual metadata for legacy descriptors */
  setContext(ctx: RightContext | null): void;
  setActiveTab(tab: ActiveTab | null): void;
  /** switch which chat session the right rail shows (Sessions list / New chat) */
  setActiveSession(id: string): void;
  setActiveList(id: string): void;
  toggleLeft(): void;
  toggleRight(): void;
  showRight(): void;
  showLeft(): void;
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
    activeSession: readLS(LS_SESSION) || "main",
  });
  let api: DockviewApi | null = null;
  // the single shared preview slot. We keep one dockview panel (fixed id) and swap its
  // params/title in place so a single-click reuses ONE tab. `previewLogicalId` records
  // which descriptor currently lives in the slot (so pinning that same thing promotes it).
  const PREVIEW_PANEL = "__preview__";
  let previewLogicalId: string | null = null;

  const persist = () => { if (api) writeLS(LS_DOCK, JSON.stringify(api.toJSON())); };

  // ── navigation history (Escape / Alt+Left = go back) ─────────────────────────
  // Every user navigation snapshots the FULL dock layout + active list BEFORE mutating,
  // so goBack restores exactly what was on screen — including a preview tab whose content
  // was swapped in place, or a tab that was closed. Bounded; in-memory only (a reload
  // starts with an empty history, matching editor go-back behavior).
  const HIST_MAX = 50;
  interface NavSnapshot { dock: string; activeList: string; previewLogicalId: string | null }
  const hist: NavSnapshot[] = [];
  let restoring = false;
  const histPush = () => {
    if (!api || restoring) return;
    try {
      const snap: NavSnapshot = { dock: JSON.stringify(api.toJSON()), activeList: store.getState().activeList, previewLogicalId };
      const top = hist[hist.length - 1];
      if (top && top.dock === snap.dock && top.activeList === snap.activeList) return;
      hist.push(snap);
      if (hist.length > HIST_MAX) hist.shift();
    } catch { /* grid mid-teardown — skip this snapshot */ }
  };

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
    setActiveSession(id) { store.set((st) => ({ ...st, activeSession: id })); writeLS(LS_SESSION, id); },
    goBack() {
      const snap = hist.pop();
      if (!snap || !api) return false;
      restoring = true;
      try {
        api.fromJSON(JSON.parse(snap.dock));
        previewLogicalId = snap.previewLogicalId;
        if (store.getState().activeList !== snap.activeList) {
          store.set((s) => ({ ...s, activeList: snap.activeList }));
          writeLS(LS_LIST, snap.activeList);
        }
        persist();
      } catch { forgetPreview(); return false; }  // stale snapshot — drop it, leave the grid as-is
      finally { restoring = false; }
      return true;
    },
    openTab(d) {
      if (!api) return;
      histPush();
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
    openTabBeside(d) {
      if (!api) return;
      histPush();
      // already open anywhere (including as the preview's current content)? just activate.
      if (previewLogicalId === d.id) { api.getPanel(PREVIEW_PANEL)?.api.setActive(); return; }
      const existing = api.getPanel(d.id);
      if (existing) { existing.api.setActive(); return; }
      const active = api.activePanel ?? undefined;
      const otherGroup = api.groups.find((g) => g.id !== active?.group?.id);
      addPanelSafe({
        id: d.id,
        component: "tab",
        title: d.title,
        params: panelParams(d, false),
        // a second group exists → open WITHIN it (reuse the split); otherwise split right
        // of the panel the user clicked in. No active panel → plain add.
        position: otherGroup
          ? { referenceGroup: otherGroup.id, direction: "within" }
          : active
            ? { referencePanel: active.id, direction: "right" }
            : undefined,
      });
    },
    retargetTab(panelId, d) {
      const panel = api?.getPanel(panelId);
      if (!panel) return;
      histPush();  // Escape / Alt+Left also undoes in-pane navigation
      const pinned = Boolean((panel.params as { pinned?: boolean } | undefined)?.pinned);
      panel.api.updateParameters({ ...panelParams(d, panelId === PREVIEW_PANEL), pinned });
      panel.api.setTitle(d.title);
      if (panelId === PREVIEW_PANEL) previewLogicalId = d.id;
    },
    pinTab(panelId) {
      const panel = api?.getPanel(panelId);
      if (!panel) return;
      const cur = (panel.params ?? {}) as { kind: string; p: Record<string, unknown>; ctx: RightContext | null };
      if (panelId === PREVIEW_PANEL) {
        // the preview slot must stay reusable — promote its content to a real panel, pinned
        const d: TabDescriptor = { id: previewLogicalId ?? `${cur.kind}:pinned`, title: panel.title ?? "", kind: cur.kind, params: cur.p, context: cur.ctx };
        panel.api.close(); forgetPreview();
        addPanelSafe({ id: d.id, component: "tab", title: d.title, params: { ...panelParams(d, false), pinned: true } });
        return;
      }
      panel.api.updateParameters({ ...cur, preview: false, pinned: true });
    },
    unpinTab(panelId) {
      const panel = api?.getPanel(panelId);
      if (panel) panel.api.updateParameters({ ...(panel.params ?? {}), pinned: false });
    },
    openPreview(d) {
      if (!api) return;
      histPush();
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
    closeTab(id) {
      const panel = api?.getPanel(id);
      if (!panel || (panel.params as { pinned?: boolean } | undefined)?.pinned) return;  // pinned tabs stay until unpinned
      histPush();
      panel.api.close();
    },
    setActiveList(id) {
      if (store.getState().activeList !== id) histPush();
      store.set((s) => ({ ...s, activeList: id })); writeLS(LS_LIST, id);
    },
    toggleLeft() { store.set((s) => ({ ...s, leftCollapsed: !s.leftCollapsed })); },
    toggleRight() { store.set((s) => ({ ...s, rightCollapsed: !s.rightCollapsed })); },
    showRight() { store.set((s) => ({ ...s, rightCollapsed: false })); },
    showLeft() { store.set((s) => ({ ...s, leftCollapsed: false })); },
    resetLayout() {
      histPush();
      try { localStorage.removeItem(LS_DOCK); } catch { /* noop */ }
      forgetPreview();
      api?.clear();
      store.set((s) => ({ ...s, context: null, activeTab: null }));
    },
  };
}
