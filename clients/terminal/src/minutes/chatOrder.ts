/** THE RAIL'S ORDER: pinned rows first, then every other row by recency, newest on top.
 *
 *  `rows` arrive in recency order (`railRows`). That order is kept for every unpinned row, so a chat
 *  started now is the first unpinned row, whatever was saved before. `order` is the saved
 *  drag order (`GET /api/chat/order`) and arranges the PINNED rows only: a pinned row the person
 *  has not placed yet sits at the top of the pinned group, newest first. */
export function orderedRows<T extends { key: string }>(rows: T[], order: string[], pinned: ReadonlySet<string> = new Set()): T[] {
  const ranks = new Map(order.map((id, i) => [id, i]));
  const recency = new Map(rows.map((r, i) => [r.key, i]));
  const pins = rows.filter((r) => pinned.has(r.key)).sort((a, b) =>
    (ranks.get(a.key) ?? -1) - (ranks.get(b.key) ?? -1) || (recency.get(a.key) ?? 0) - (recency.get(b.key) ?? 0));
  return [...pins, ...rows.filter((r) => !pinned.has(r.key))];
}

/** Drop `from` on `to` in a list of keys. Callers pass only pinned keys: an unpinned row's place is
 *  decided by its recency, so there is nothing to drag it to. */
export function moveChat(order: string[], from: string, to: string): string[] {
  if (from === to || !order.includes(from) || !order.includes(to)) return order;
  const next = order.filter(x=>x!==from);
  next.splice(order.indexOf(to),0,from);
  return next;
}
