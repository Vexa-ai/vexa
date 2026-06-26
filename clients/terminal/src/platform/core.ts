/**
 * Core platform primitives shared by browser-facing React hooks and service leaf modules.
 */

// ── DI ───────────────────────────────────────────────────────────────────────
export interface ServiceId<T> { readonly _t?: T; readonly id: string }
export function createServiceId<T>(id: string): ServiceId<T> { return { id }; }

export interface ServiceContainer {
  get<T>(id: ServiceId<T>): T;
  tryGet<T>(id: ServiceId<T>): T | undefined;
  /** Dispose every instantiated service that exposes a `dispose()`. */
  dispose(): void;
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
      if (!f) throw new Error(`[term-platform] no service registered for "${id.id}"`);
      const inst = f(container);
      instances.set(id.id, inst);
      return inst as T;
    },
    tryGet<T>(id: ServiceId<T>): T | undefined {
      try { return container.get(id); } catch { return undefined; }
    },
    dispose() {
      for (const inst of instances.values()) {
        const d = inst as { dispose?: () => void };
        if (d && typeof d.dispose === "function") {
          try { d.dispose(); } catch { /* a failed dispose must not block the rest */ }
        }
      }
      instances.clear();
    },
  };
  return container;
}

export interface ObservableStore<S> { getState(): S; subscribe(cb: (s: S) => void): () => void; }

/** Make a minimal observable store from an initial value. */
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
      const s = store.getState();
      return when.split("||").some((orPart) =>
        orPart.split("&&").every((part) => {
          const t = part.trim();
          const eq = t.match(/^(\S+)\s*(==|!=)\s*(.+)$/);
          if (eq) {
            const [, k, op, raw] = eq;
            const want = raw.trim().replace(/^["']|["']$/g, "");
            const cur = String(s[k.trim()] ?? "");
            return op === "==" ? cur === want : cur !== want;
          }
          const neg = t.startsWith("!");
          const key = neg ? t.slice(1).trim() : t;
          const v = !!s[key];
          return neg ? !v : v;
        }),
      );
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
