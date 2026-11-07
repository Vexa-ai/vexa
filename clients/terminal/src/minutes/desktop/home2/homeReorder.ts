/** REORDERING THE HOME'S CARDS, TRELLO-LIGHT (founder 2026-10-16: "it feels heavy"). The new order
 *  settles locally the moment a card is dropped (or moved with the keyboard); it is saved once, in
 *  the background, after the burst of moves ends; a failed save puts the last saved order back. No
 *  network during the drag and no re-fetch of the home after the drop. */

/** `order` with `ref` moved before `to` (a card it was dropped on) or by one step (-1 / +1). */
export function moved(order: string[], ref: string, to: string | -1 | 1): string[] {
  const i = order.indexOf(ref);
  if (i < 0) return order;
  if (to === -1 || to === 1) {
    const j = i + to;
    if (j < 0 || j >= order.length) return order;
    const next = [...order];
    [next[i], next[j]] = [next[j], next[i]];
    return next;
  }
  if (to === ref) return order;
  const rest = order.filter((x) => x !== ref);
  const at = rest.indexOf(to);
  if (at < 0) return order;
  return [...rest.slice(0, at), ref, ...rest.slice(at)];
}

export function createReorder(o: {
  save: (next: string[], prev: string[]) => Promise<boolean>;
  onChange: (order: string[]) => void;
  onFail?: () => void;
  debounceMs?: number;
}) {
  let current: string[] = [];
  let saved: string[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  const emit = () => o.onChange([...current]);
  const flush = async () => {
    timer = null;
    const next = [...current], prev = [...saved];
    if (next.join("\n") === prev.join("\n")) return;
    let ok = false;
    try { ok = await o.save(next, prev); } catch { ok = false; }
    if (ok) { saved = next; return; }
    current = prev; emit(); o.onFail?.();
  };
  return {
    /** the server's order (a fresh read): taken as both current and saved, unless a save is pending */
    set(order: string[]) { if (timer) return; current = [...order]; saved = [...order]; emit(); },
    move(ref: string, to: string | -1 | 1) {
      const next = moved(current, ref, to);
      if (next === current) return;
      current = next; emit();
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { void flush(); }, o.debounceMs ?? 400);
    },
    order: () => [...current],
  };
}
