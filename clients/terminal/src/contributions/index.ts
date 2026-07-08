/**
 * term-contributions — the registries the structured shell renders from (layout v2).
 *
 * Two rendered zones plus commands:
 *  • LISTS    (left)  — `registerList` → the segmented top-bar items + their list component.
 *  • TABS     (center)— `registerTab(kind, comp)` → a tab-type the dockview center can open by kind
 *                        (params are serializable so the layout persists).
 *  • COMMANDS         — `registerCommand` → /-skills + palette entries (unchanged concept).
 *
 * A list opens center tabs (via LayoutService.openTab). The persistent chat rail reads the active tab.
 * Adding a surface = a few registrations, never a shell edit.
 */
import type { ComponentType } from "react";
import type { CommandContribution } from "../platform";

export type SurfaceId = string;

export interface ListContribution {
  id: string;
  label: string;
  icon: string;
  order: number;
  component: ComponentType;
}

export interface TabProps {
  /** the dockview panel id (stable per tab) */
  id: string;
  params: Record<string, unknown>;
  /** true while this tab is the active center panel */
  active: boolean;
}
export type TabComponent = ComponentType<TabProps>;

const _lists = new Map<string, ListContribution>();
const _tabs = new Map<string, TabComponent>();
const _commands: CommandContribution[] = [];

// Most surfaces register at import time (the barrel), but a surface may register LATE after an
// async check (e.g. the hidden admin panel appears only once /api/admin/me confirms the caller).
// The shell subscribes (useSyncExternalStore) so a late registration re-renders the switcher.
let _version = 0;
const _subs = new Set<() => void>();
const _notify = () => { _version++; for (const fn of _subs) fn(); };

export const registry = {
  registerList(l: ListContribution) { _lists.set(l.id, l); _notify(); },
  lists(): ListContribution[] { return [..._lists.values()].sort((a, b) => a.order - b.order); },
  list(id: string): ListContribution | undefined { return _lists.get(id); },

  subscribe(fn: () => void): () => void { _subs.add(fn); return () => { _subs.delete(fn); }; },
  version(): number { return _version; },

  registerTab(kind: string, c: TabComponent) { _tabs.set(kind, c); },
  tabComponent(kind: string): TabComponent | undefined { return _tabs.get(kind); },

  registerCommand(c: CommandContribution) { _commands.push(c); },
  commands(): CommandContribution[] { return [..._commands]; },
};

export const registerList = (l: ListContribution): void => registry.registerList(l);
export const registerTab = (kind: string, c: TabComponent): void => registry.registerTab(kind, c);
export const registerCommand = (c: CommandContribution): void => registry.registerCommand(c);
