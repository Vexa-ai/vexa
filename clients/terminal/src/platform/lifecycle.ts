/**
 * LifecycleService — the application boot/teardown spine (Lumino's `app.restored`, VSCode's
 * lifecycle phases). Boot runs ordered phases (register services → activate contributions → restore
 * layout) and resolves a `whenReady()` promise / fires an `onReady` signal when done. Teardown disposes
 * everything. The shell waits on `whenReady()` before painting its restored layout.
 */
import { Signal } from "./signal";
import { DisposableStore, type IDisposable } from "./disposable";
import { createServiceId } from "./core";

export type Phase = "starting" | "ready" | "disposed";

export interface LifecycleService extends IDisposable {
  readonly phase: Phase;
  readonly onReady: Signal<void>;
  /** Run ordered async startup steps, then mark ready. Idempotent. */
  start(steps?: Array<() => void | Promise<void>>): Promise<void>;
  whenReady(): Promise<void>;
  /** Register a disposable to be torn down on app dispose. */
  register<T extends IDisposable>(d: T): T;
}

export const LifecycleServiceId = createServiceId<LifecycleService>("lifecycle");

export function createLifecycleService(): LifecycleService {
  const store = new DisposableStore();
  const onReady = store.add(new Signal<void>());
  let phase: Phase = "starting";
  let started: Promise<void> | null = null;
  let resolveReady!: () => void;
  const ready = new Promise<void>((r) => (resolveReady = r));

  return {
    get phase() {
      return phase;
    },
    onReady,
    register: (d) => store.add(d),
    whenReady: () => ready,
    start(steps = []) {
      if (started) return started;
      started = (async () => {
        for (const step of steps) await step();
        phase = "ready";
        resolveReady();
        onReady.emit();
      })();
      return started;
    },
    dispose() {
      if (phase === "disposed") return;
      phase = "disposed";
      store.dispose();
    },
  };
}
