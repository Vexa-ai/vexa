/** Missing (new) chats follow the saved rows; their own recency order is preserved. */
export function orderedRows<T extends { key: string }>(rows: T[], order: string[]): T[] {
  const ranks = new Map(order.map((id,i)=>[id,i]));
  return [...rows].sort((a,b)=>(ranks.get(a.key) ?? Infinity)-(ranks.get(b.key) ?? Infinity));
}
export function moveChat(order: string[], from: string, to: string): string[] {
  if (from === to || !order.includes(from) || !order.includes(to)) return order;
  const next = order.filter(x=>x!==from);
  next.splice(order.indexOf(to),0,from);
  return next;
}
