/**
 * term-contributions — the registries the structured shell renders from (layout v2).
 *
 * Three decoupled zones, three registries:
 *  • LISTS    (left)  — `registerList` → the segmented top-bar items + their list component.
 *  • TABS     (center)— `registerTab(kind, comp)` → a tab-type the dockview center can open by kind
 *                        (params are serializable so the layout persists).
 *  • CONTEXTS (right) — `registerContext(kind, comp)` → the single contextual panel, by kind.
 *  • COMMANDS         — `registerCommand` → /-skills + palette entries (unchanged concept).
 *
 * A list opens center tabs (via LayoutService.openTab) and the active tab carries the right context;
 * the shell wires them. Adding a surface = a few registrations, never a shell edit.
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
  /** true while this tab is the active center panel (drives the right context) */
  active: boolean;
}
export type TabComponent = ComponentType<TabProps>;

export interface ContextProps {
  params: Record<string, unknown>;
}
export type ContextComponent = ComponentType<ContextProps>;

const _lists = new Map<string, ListContribution>();
const _tabs = new Map<string, TabComponent>();
const _contexts = new Map<string, ContextComponent>();
const _commands: CommandContribution[] = [];

export const registry = {
  registerList(l: ListContribution) { _lists.set(l.id, l); },
  lists(): ListContribution[] { return [..._lists.values()].sort((a, b) => a.order - b.order); },
  list(id: string): ListContribution | undefined { return _lists.get(id); },

  registerTab(kind: string, c: TabComponent) { _tabs.set(kind, c); },
  tabComponent(kind: string): TabComponent | undefined { return _tabs.get(kind); },

  registerContext(kind: string, c: ContextComponent) { _contexts.set(kind, c); },
  contextComponent(kind: string): ContextComponent | undefined { return _contexts.get(kind); },

  registerCommand(c: CommandContribution) { _commands.push(c); },
  commands(): CommandContribution[] { return [..._commands]; },
};

export const registerList = (l: ListContribution): void => registry.registerList(l);
export const registerTab = (kind: string, c: TabComponent): void => registry.registerTab(kind, c);
export const registerContext = (kind: string, c: ContextComponent): void => registry.registerContext(kind, c);
export const registerCommand = (c: CommandContribution): void => registry.registerCommand(c);
