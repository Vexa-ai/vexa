/** PIN AND HIDE — the person's own marks on their rail, held by the SERVER.
 *
 *  agent-api keeps them per person (`GET`/`POST /api/chat/marks`), so a pin made on one device is a
 *  pin on every other and survives a reload. A row is named by its rail key, `c:<chat>` or
 *  `m:<meeting>`. A mark on a meeting shared with you is yours alone: the owner's rail never moves. */

export type RailMarks = { pinned: string[]; hidden: string[] };
export type RailMark = { pinned?: boolean; hidden?: boolean };

export const NO_MARKS: RailMarks = { pinned: [], hidden: [] };

function read(data: unknown): RailMarks {
  const d = (data ?? {}) as { pinned?: unknown; hidden?: unknown };
  const keys = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && !!x) : []);
  return { pinned: keys(d.pinned), hidden: keys(d.hidden) };
}

/** The person's marks. A server that cannot answer leaves the rail unmarked rather than broken. */
export async function fetchMarks(fetchImpl: typeof fetch = fetch): Promise<RailMarks> {
  const r = await fetchImpl("/api/chat/marks", { cache: "no-store" });
  if (!r.ok) throw new Error(`Couldn't load pins (${r.status})`);
  return read(await r.json());
}

/** Set or clear one row's marks; resolves to every mark the person now has. Throws when the server
 *  refuses, so the caller keeps the old state and says so. */
export async function markRow(row: string, mark: RailMark, fetchImpl: typeof fetch = fetch): Promise<RailMarks> {
  const r = await fetchImpl("/api/chat/marks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ row, ...mark }),
  });
  if (!r.ok) throw new Error(`Couldn't save (${r.status})`);
  return read(await r.json());
}

/** The rows with their marks on them. */
export function applyMarks<T extends { key: string }>(rows: T[], marks: RailMarks): (T & { pinned: boolean; hidden: boolean })[] {
  const pinned = new Set(marks.pinned), hidden = new Set(marks.hidden);
  return rows.map((r) => ({ ...r, pinned: pinned.has(r.key), hidden: hidden.has(r.key) }));
}
