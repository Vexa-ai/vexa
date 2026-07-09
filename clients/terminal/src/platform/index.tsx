"use client";
/**
 * term-platform — the workbench's lightweight DI + command + context-key services.
 *
 * VSCode's service-identifier + container model without decorators/reflect-metadata: a `ServiceId<T>`
 * is a typed key; `createContainer` resolves factories lazily; React reaches the container through one
 * context with `useService`. Observable stores (the dash-meeting-state shape) are subscribed via
 * `useStore` over React 19's `useSyncExternalStore`.
 *
 * (Foundation lives under src/ as modules; graduates to `@vexa/term-platform` package later — see
 * docs/ARCHITECTURE.md + DECISIONS.md.)
 */
import { createContext, useContext, useSyncExternalStore, type ReactNode } from "react";
import type { ObservableStore, ServiceContainer, ServiceId } from "./core";

export {
  CommandServiceId,
  ContextKeyServiceId,
  createCommandService,
  createContainer,
  createContextKeyService,
  createServiceId,
  createStore,
  reg,
} from "./core";
export type {
  CommandContribution,
  CommandService,
  ContextKeyService,
  ObservableStore,
  ServiceContainer,
  ServiceId,
  ServiceRegistration,
} from "./core";

// ── React bridge ──────────────────────────────────────────────────────────────
const ContainerCtx = createContext<ServiceContainer | null>(null);
export function ServicesProvider({ container, children }: { container: ServiceContainer; children: ReactNode }) {
  return <ContainerCtx.Provider value={container}>{children}</ContainerCtx.Provider>;
}
export function useContainer(): ServiceContainer {
  const c = useContext(ContainerCtx);
  if (!c) throw new Error("[term-platform] useService outside <ServicesProvider>");
  return c;
}
export function useService<T>(id: ServiceId<T>): T { return useContainer().get(id); }
export function useStore<S>(store: ObservableStore<S>): S {
  return useSyncExternalStore(
    (cb) => store.subscribe(cb),
    () => store.getState(),
    () => store.getState(),
  );
}

// ── kernel barrel — disposables, signals, keybindings, lifecycle ────────────────
// (one-way: these import the hoisted `createServiceId` from here; safe to re-export.)
export * from "./disposable";
export * from "./signal";
export * from "./keybindings";
export * from "./lifecycle";
