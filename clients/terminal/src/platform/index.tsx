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

// ── DI ───────────────────────────────────────────────────────────────────────
export interface ServiceId<T> { readonly _t?: T; readonly id: string }
export function createServiceId<T>(id: string): ServiceId<T> { return { id }; }

export interface ServiceContainer {
  get<T>(id: ServiceId<T>): T;
  tryGet<T>(id: ServiceId<T>): T | undefined;
}
export interface ServiceRegistration<T> { id: ServiceId<T>; factory: (c: ServiceContainer) => T; }
export function reg<T>(id: ServiceId<T>, factory: (c: ServiceContainer) => T): ServiceRegistration<T> {
  return { id, factory };
}

export function createContainer(regs: ServiceRegistration<unknown>[]): ServiceContainer {
  const factories = new Map<string, (c: ServiceContainer) => unknown>();
  const instances = new Map<string, unknown>();
  for (const r of regs) factories.set(r.id.id, r.factory);
  const container: ServiceContainer = {
    get<T>(id: ServiceId<T>): T {
      if (instances.has(id.id)) return instances.get(id.id) as T;
      const f = factories.get(id.id);
      if (!f) throw new Error(`[term-platform] no service registered for "${id.id}"`); // fail loud (P18)
      const inst = f(container);
      instances.set(id.id, inst);
      return inst as T;
    },
    tryGet<T>(id: ServiceId<T>): T | undefined {
      try { return container.get(id); } catch { return undefined; }
    },
  };
  return container;
}

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

export interface ObservableStore<S> { getState(): S; subscribe(cb: (s: S) => void): () => void; }
export function useStore<S>(store: ObservableStore<S>): S {
  return useSyncExternalStore(
    (cb) => store.subscribe(cb),
    () => store.getState(),
    () => store.getState(),
  );
}

/** Make a minimal observable store (the dash-meeting-state shape) from an initial value. */
export function createStore<S>(initial: S): ObservableStore<S> & { set(next: S | ((s: S) => S)): void } {
  let state = initial;
  const subs = new Set<(s: S) => void>();
  return {
    getState: () => state,
    subscribe(cb) { subs.add(cb); return () => subs.delete(cb); },
    set(next) {
      state = typeof next === "function" ? (next as (s: S) => S)(state) : next;
      subs.forEach((cb) => cb(state));
    },
  };
}

// ── Context keys (when-clause gating) ──────────────────────────────────────────
export interface ContextKeyService {
  set(key: string, value: boolean | string): void;
  evaluate(when?: string): boolean;
  store: ObservableStore<Record<string, boolean | string>>;
}
export const ContextKeyServiceId = createServiceId<ContextKeyService>("contextKey");
export function createContextKeyService(): ContextKeyService {
  const store = createStore<Record<string, boolean | string>>({});
  return {
    store,
    set(key, value) { store.set((s) => ({ ...s, [key]: value })); },
    evaluate(when) {
      if (!when) return true;
      // minimal evaluator: `a && b && !c` over truthy context keys (VSCode-style, kept small)
      return when.split("&&").every((part) => {
        const t = part.trim();
        const neg = t.startsWith("!");
        const key = neg ? t.slice(1).trim() : t;
        const v = !!store.getState()[key];
        return neg ? !v : v;
      });
    },
  };
}

// ── Commands (the /-skill palette source) ──────────────────────────────────────
export interface CommandContribution {
  id: string; title: string; skill?: `/${string}`; when?: string;
  run(ctx: { container: ServiceContainer; args?: string }): void | Promise<void>;
}
export interface CommandService {
  register(cmd: CommandContribution): void;
  all(): CommandContribution[];
  skills(): CommandContribution[];
  querySkills(input: string): CommandContribution[];
  execute(id: string, args?: string): Promise<void>;
}
export const CommandServiceId = createServiceId<CommandService>("command");
export function createCommandService(container: ServiceContainer): CommandService {
  const cmds = new Map<string, CommandContribution>();
  const ctxKeys = () => container.tryGet(ContextKeyServiceId);
  const visible = (c: CommandContribution) => ctxKeys()?.evaluate(c.when) ?? true;
  return {
    register(cmd) { cmds.set(cmd.id, cmd); },
    all() { return [...cmds.values()].filter(visible); },
    skills() { return this.all().filter((c) => c.skill); },
    querySkills(input) {
      const q = input.replace(/^\//, "").toLowerCase();
      return this.skills().filter((c) => c.skill!.slice(1).toLowerCase().startsWith(q));
    },
    async execute(id, args) { const c = cmds.get(id); if (c && visible(c)) await c.run({ container, args }); },
  };
}
