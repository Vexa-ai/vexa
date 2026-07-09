/**
 * Disposables — the Lumino/VSCode resource-lifecycle primitive. Anything that holds a subscription,
 * a timer, a DOM listener, or a child resource returns an `IDisposable`; a `DisposableStore` collects
 * them so a surface/service can tear everything down in one `dispose()`. This is what makes the engine
 * leak-free (every `connect`, every keybinding, every panel registration is disposable).
 */
export interface IDisposable {
  dispose(): void;
}

export function toDisposable(fn: () => void): IDisposable {
  let done = false;
  return {
    dispose() {
      if (done) return;
      done = true;
      fn();
    },
  };
}

export function isDisposable(x: unknown): x is IDisposable {
  return !!x && typeof (x as IDisposable).dispose === "function";
}

export function combinedDisposable(...items: IDisposable[]): IDisposable {
  return toDisposable(() => {
    for (const i of items) i.dispose();
  });
}

/** Collects disposables; `dispose()` (idempotent) tears them all down. `add` after disposal disposes
 *  immediately (mirrors VSCode), so late registrations never leak. */
export class DisposableStore implements IDisposable {
  private readonly _items = new Set<IDisposable>();
  private _disposed = false;

  get isDisposed(): boolean {
    return this._disposed;
  }

  add<T extends IDisposable>(d: T): T {
    if (this._disposed) {
      d.dispose();
      return d;
    }
    this._items.add(d);
    return d;
  }

  delete(d: IDisposable): void {
    if (this._items.delete(d)) d.dispose();
  }

  clear(): void {
    for (const d of [...this._items]) d.dispose();
    this._items.clear();
  }

  dispose(): void {
    if (this._disposed) return;
    this._disposed = true;
    this.clear();
  }
}
