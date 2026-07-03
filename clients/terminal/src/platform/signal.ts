/**
 * Signals — the engine's fine-grained event primitive (Lumino's `ISignal`). A `Signal<T>` is a typed
 * pub/sub: `connect(slot)` returns an `IDisposable`; `emit(value)` fires every connected slot in
 * connection order. Used for engine events (layout changed, ready, theme changed) where an
 * `ObservableStore` (whole-state snapshot) is the wrong granularity.
 *
 * Slots are iterated over a copy, so a slot may disconnect (or connect) during dispatch safely.
 */
import { type IDisposable, toDisposable } from "./disposable";

export type Slot<T> = (value: T) => void;

export interface ISignal<T> {
  connect(slot: Slot<T>): IDisposable;
}

export class Signal<T = void> implements ISignal<T>, IDisposable {
  private readonly _slots = new Set<Slot<T>>();

  connect(slot: Slot<T>): IDisposable {
    this._slots.add(slot);
    return toDisposable(() => {
      this._slots.delete(slot);
    });
  }

  emit(value: T): void {
    for (const slot of [...this._slots]) slot(value);
  }

  dispose(): void {
    this._slots.clear();
  }
}
