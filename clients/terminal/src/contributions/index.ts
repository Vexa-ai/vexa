/**
 * term-contributions — the Surface contribution API + registry.
 *
 * A surface is a self-contained module that REGISTERS its activity item, views (per slot), composer,
 * and commands at load time. The shell renders entirely off this registry — adding a surface is a
 * `registerSurface(...)` call, never a shell edit. (docs/ARCHITECTURE.md)
 */
import type { ComponentType } from "react";
import type { CommandContribution, ServiceContainer } from "../platform";

export type SurfaceId = string;
export type Slot = "main" | "primarySidebar" | "auxiliaryBar" | "panel";

export interface ViewContribution {
  id: string;
  slot: Slot;
  component: ComponentType<{ surfaceId: SurfaceId }>;
  when?: string;
}
export interface ActivityItem { id: SurfaceId; label: string; icon: string; order: number; live?: boolean; }
export interface ComposerSpec { enabled: boolean; placeholder?: string; quickChips?: string[]; }

export interface SurfaceContribution {
  id: SurfaceId;
  activity?: ActivityItem;
  views?: ViewContribution[];
  composer?: ComposerSpec;
  commands?: CommandContribution[];
  contextKeys?: string[];
  /** the shell composer calls this on a non-slash submit while this surface is active. */
  onSubmit?: (text: string, container: ServiceContainer) => void;
}

export interface ContributionRegistry {
  registerSurface(s: SurfaceContribution): void;
  surfaces(): SurfaceContribution[];
  activityItems(): ActivityItem[];
  views(slot: Slot, surfaceId: SurfaceId): ViewContribution[];
  commands(): CommandContribution[];
  get(id: SurfaceId): SurfaceContribution | undefined;
}

const _surfaces = new Map<SurfaceId, SurfaceContribution>();

export const registry: ContributionRegistry = {
  registerSurface(s) { _surfaces.set(s.id, s); },
  surfaces() { return [..._surfaces.values()]; },
  activityItems() {
    return this.surfaces().map((s) => s.activity).filter(Boolean as unknown as (a: ActivityItem | undefined) => a is ActivityItem)
      .sort((a, b) => a.order - b.order);
  },
  views(slot, surfaceId) {
    const s = _surfaces.get(surfaceId);
    return (s?.views ?? []).filter((v) => v.slot === slot);
  },
  commands() { return this.surfaces().flatMap((s) => s.commands ?? []); },
  get(id) { return _surfaces.get(id); },
};

export function registerSurface(s: SurfaceContribution): void { registry.registerSurface(s); }
