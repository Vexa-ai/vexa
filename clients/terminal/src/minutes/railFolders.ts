/** CHAT FOLDERS — the person's own sections of the rail, held by the SERVER (#1783).
 *
 *  agent-api keeps them per person in the session store (`GET /api/chat/folders`, `POST
 *  /api/chat/folders`, `…/update`, `…/delete`, `POST /api/chat/move`), so a folder made on one
 *  device is there on every other, and the agent's own tools (`chat_folder_create`, `chat_move`)
 *  change the same rail. On a chat shared with you, where you file it is yours alone.
 *
 *  The sections, in drawing order: Pinned (manual order) · Shared with me (a chat somebody else owns
 *  and added the person to lands here until they file it elsewhere) · the person's folders (manual order; an
 *  automatic Routines folder holds routine chats until the person renames or dissolves it) ·
 *  Ungrouped (newest first) · Hidden (collapsed by default, at the bottom). Hidden is a folder, not a
 *  filter: hiding a chat is moving it there, un-hiding is moving it out.
 *
 *  A server without the session store answers `503 folders_unavailable`; the rail then draws the
 *  same sections from the person's pin and hide marks (`railMarks.ts`), with no named folders. */
import type { RailMarks } from "./railMarks";

export const PINNED = "pinned", UNGROUPED = "ungrouped", HIDDEN = "hidden", ROUTINES = "routines", SHARED = "shared";

export type RailFolder = { id: string; name: string; builtin: boolean; collapsed: boolean; position?: number };
export type RailFolders = {
  folders: RailFolder[];
  /** where each filed row is; a row with no filing is Ungrouped (a routine's, Routines) */
  filings: Record<string, string>;
  /** each folder's manual order (the rail uses Pinned's) */
  order: Record<string, string[]>;
  /** false when the server cannot keep folders: the sections come from the marks instead */
  served: boolean;
};

const BUILTINS: RailFolder[] = [
  { id: PINNED, name: "Pinned", builtin: true, collapsed: false },
  { id: UNGROUPED, name: "Ungrouped", builtin: true, collapsed: false },
  { id: HIDDEN, name: "Hidden", builtin: true, collapsed: true },
];

export const NO_FOLDERS: RailFolders = { folders: BUILTINS, filings: {}, order: {}, served: false };

function read(data: unknown, served = true): RailFolders {
  const d = (data ?? {}) as Record<string, unknown>;
  const folders = Array.isArray(d.folders)
    ? (d.folders as Record<string, unknown>[])
        .filter((f) => f && typeof f.id === "string" && f.id)
        .map((f) => ({ id: String(f.id), name: String(f.name ?? f.id), builtin: !!f.builtin, collapsed: !!f.collapsed,
          position: typeof f.position === "number" ? f.position : undefined }))
    : BUILTINS;
  const filings: Record<string, string> = {};
  for (const [k, v] of Object.entries((d.filings as Record<string, unknown>) ?? {})) if (typeof v === "string") filings[k] = v;
  const order: Record<string, string[]> = {};
  for (const [k, v] of Object.entries((d.order as Record<string, unknown>) ?? {}))
    if (Array.isArray(v)) order[k] = v.filter((x): x is string => typeof x === "string");
  return { folders: folders.length ? folders : BUILTINS, filings, order, served };
}

/** The same sections drawn from pin and hide marks, for a server that keeps no folders. */
export function foldersFromMarks(marks: RailMarks): RailFolders {
  const filings: Record<string, string> = {};
  for (const r of marks.pinned) filings[r] = PINNED;
  for (const r of marks.hidden) filings[r] = HIDDEN;
  return { folders: BUILTINS, filings, order: { [PINNED]: [...marks.pinned] }, served: false };
}

export class FoldersUnavailable extends Error {}

async function call(path: string, body: unknown | undefined, fetchImpl: typeof fetch): Promise<unknown> {
  const r = await fetchImpl(path, body === undefined ? { cache: "no-store" } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (r.status === 503) throw new FoldersUnavailable("folders_unavailable");
  if (!r.ok) {
    let msg = `Couldn't save (${r.status})`;
    try { const j = await r.json() as { detail?: { message?: string } }; if (j?.detail?.message) msg = j.detail.message; } catch { /* plain status */ }
    throw new Error(msg);
  }
  return r.json();
}

export async function fetchFolders(fetchImpl: typeof fetch = fetch): Promise<RailFolders> {
  return read(await call("/api/chat/folders", undefined, fetchImpl));
}

/** File rows in one place; resolves to the person's folders as they now are. */
export async function moveRows(rows: string[], to: string, before?: string, fetchImpl: typeof fetch = fetch): Promise<RailFolders> {
  return read(await call("/api/chat/move", { rows, to, ...(before ? { before } : {}) }, fetchImpl));
}

export async function createFolder(name: string, fetchImpl: typeof fetch = fetch): Promise<RailFolder> {
  const f = await call("/api/chat/folders", { name }, fetchImpl) as Record<string, unknown>;
  return { id: String(f.id), name: String(f.name), builtin: false, collapsed: !!f.collapsed };
}

export async function updateFolder(folder: string, change: { name?: string; collapsed?: boolean; position?: number },
  fetchImpl: typeof fetch = fetch): Promise<void> {
  await call("/api/chat/folders/update", { folder, ...change }, fetchImpl);
}

export async function deleteFolder(folder: string, fetchImpl: typeof fetch = fetch): Promise<void> {
  await call("/api/chat/folders/delete", { folder }, fetchImpl);
}

/** The section a row is drawn in: where it is filed; else Shared with me for a chat somebody else
 *  owns; else Routines for a routine's chat while that folder exists; else Ungrouped. A filing to a
 *  folder that is gone counts as Ungrouped. */
export function folderOf(row: { key: string; routine?: unknown; sharedBy?: string }, f: RailFolders): string {
  const have = new Set(f.folders.map((x) => x.id));
  const at = f.filings[row.key];
  if (at && have.has(at)) return at;
  if (!at && row.sharedBy !== undefined && have.has(SHARED)) return SHARED;
  if (!at && row.routine && have.has(ROUTINES)) return ROUTINES;
  return UNGROUPED;
}

/** Rows with their section on them, and the pin/hide flags the row's menu and icon read. */
export function applyFolders<T extends { key: string; routine?: unknown; sharedBy?: string }>(rows: T[], f: RailFolders):
  (T & { folder: string; pinned: boolean; hidden: boolean })[] {
  return rows.map((r) => {
    const folder = folderOf(r, f);
    return { ...r, folder, pinned: folder === PINNED, hidden: folder === HIDDEN };
  });
}

/** Rows grouped by section, in drawing order. Pinned keeps its manual order; every other section
 *  keeps the order the rows arrive in (newest first). */
export function sections<T extends { key: string; folder?: string }>(rows: T[], f: RailFolders):
  { folder: RailFolder; rows: T[] }[] {
  const by = new Map<string, T[]>(f.folders.map((x) => [x.id, []]));
  for (const r of rows) (by.get(r.folder ?? UNGROUPED) ?? by.get(UNGROUPED))?.push(r);
  const rank = new Map((f.order[PINNED] ?? []).map((k, i) => [k, i]));
  const pinned = by.get(PINNED);
  if (pinned) pinned.sort((a, b) => (rank.get(a.key) ?? 1e9) - (rank.get(b.key) ?? 1e9));
  return f.folders.map((folder) => ({ folder, rows: by.get(folder.id) ?? [] }));
}
