/** A FOLDER FROM THE COMPUTER BECOMES A WORKSPACE (founder 2026-10-13: "as we drop calls we can drop
 *  folder"). This file is the browser's half: it walks what was dropped or picked, asks the server
 *  what of it is imported, sends the bytes in batches and finishes the import.
 *
 *  THE RULES ARE THE SERVER'S. What is skipped (`.git`, `node_modules`, `.env*`, keys, the folder's
 *  own `.gitignore`) and how large an import may be are decided by agent-api
 *  (`control_plane/folder_import.py`, `POST /api/workspace/folder/plan`); the sheet shows its answer.
 *  The one thing done here is NOT WALKING a dependency or build folder — reading fifty thousand
 *  `node_modules` entries only to send them to be refused is the slow part of a drop — and those
 *  folders are named to the server (`pruned`) so the sheet still says they were left out.
 *
 *  Batches, because the gateway buffers each request: a few megabytes and a couple of hundred files
 *  per request, every one re-checked on the server. */

/** Folder names never walked (the server's `SKIP_DIRS`, for speed only — the server still decides). */
export const PRUNE_DIRS = new Set([
  ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache",
  ".ruff_cache", ".tox", ".nox", ".next", ".nuxt", ".turbo", ".parcel-cache", ".cache", ".gradle",
  ".terraform", ".vexa",
]);

export const BATCH_BYTES = 8 * 1024 * 1024;
export const BATCH_FILES = 200;

export type FolderFile = { path: string; file: File };
export type DroppedFolder = {
  /** The folder's own name — the workspace's name unless the person changes it. */
  name: string;
  files: FolderFile[];
  /** Folders not walked because their name is in `PRUNE_DIRS`, relative to the folder. */
  pruned: string[];
  /** The folder's git remote, when it is a repository with one (`.git/config`). */
  gitRemote: string | null;
};

export type FolderTarget = { slug: string; into: string };
export type FolderPlan = {
  plan_id: string; name: string; target: FolderTarget | null;
  accepted: { path: string; size: number }[];
  skipped: { path: string; reason: string }[];
  counts: Record<string, number>;
  total_bytes: number; files: number; blocked: string | null;
  limits: { max_bytes: number; max_files: number; file_max_bytes: number };
};
export type FolderResult = {
  slug: string; workspace: string; name: string; path: string; files: number; bytes: number;
  skipped: { path: string; reason: string }[]; skipped_total: number; commit: string | null; new: boolean;
};

// ── reading what was dropped ─────────────────────────────────────────────────────────────────────

/** The minimal FileSystemEntry surface used here (the browser's `webkitGetAsEntry`). */
export interface EntryLike {
  isFile: boolean; isDirectory: boolean; name: string;
  file?: (ok: (f: File) => void, err?: (e: unknown) => void) => void;
  createReader?: () => { readEntries: (ok: (es: EntryLike[]) => void, err?: (e: unknown) => void) => void };
}

/** The directories among a drop's items. MUST be called during the drop event: the browser empties
 *  `dataTransfer.items` once the handler returns. */
export function droppedDirectories(dt: DataTransfer | null): EntryLike[] {
  const items = dt?.items;
  if (!items) return [];
  const out: EntryLike[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i];
    if (item.kind !== "file") continue;
    const entry = (item as DataTransferItem & { webkitGetAsEntry?: () => EntryLike | null }).webkitGetAsEntry?.();
    if (entry?.isDirectory) out.push(entry);
  }
  return out;
}

/** True when a drop carries at least one folder. */
export function dropHasFolder(dt: DataTransfer | null): boolean {
  return droppedDirectories(dt).length > 0;
}

/** The files of a drop that are NOT folders. A folder is also listed in `files` (with a size, on some
 *  systems), so the answer is read item by item: an item whose entry is a directory is left out. */
export function droppedPlainFiles(dt: DataTransfer | null): File[] {
  if (!dt) return [];
  const items = dt.items;
  if (!items || !items.length) return Array.from(dt.files ?? []);
  const out: File[] = [];
  for (let i = 0; i < items.length; i++) {
    const item = items[i] as DataTransferItem & { webkitGetAsEntry?: () => EntryLike | null };
    if (item.kind !== "file" || item.webkitGetAsEntry?.()?.isDirectory) continue;
    const f = item.getAsFile?.();
    if (f) out.push(f);
  }
  return out;
}

function readAll(dir: EntryLike): Promise<EntryLike[]> {
  const reader = dir.createReader?.();
  if (!reader) return Promise.resolve([]);
  const all: EntryLike[] = [];
  return new Promise((resolve, reject) => {
    const next = () => reader.readEntries((batch) => {
      if (batch.length === 0) { resolve(all); return; }
      all.push(...batch);
      next();
    }, reject);
    next();
  });
}

function entryFile(e: EntryLike): Promise<File> {
  return new Promise((resolve, reject) => e.file ? e.file(resolve, reject) : reject(new Error("not a file")));
}

/** `[remote "origin"] url = …` from a `.git/config`, else the first remote's url, else null. */
export function parseGitRemote(config: string): string | null {
  let current: string | null = null;
  const remotes: Record<string, string> = {};
  for (const raw of (config || "").split(/\r?\n/)) {
    const line = raw.trim();
    const section = /^\[remote\s+"([^"]+)"\]$/.exec(line);
    if (section) { current = section[1]; continue; }
    if (line.startsWith("[")) { current = null; continue; }
    const url = /^url\s*=\s*(.+)$/.exec(line);
    if (current && url && !remotes[current]) remotes[current] = url[1].trim();
  }
  return remotes.origin ?? Object.values(remotes)[0] ?? null;
}

/** Walk a dropped directory: every file with its path relative to the folder; dependency and build
 *  folders are not entered (named in `pruned`); `.git` is entered only for its `config`. */
export async function walkDirectory(root: EntryLike): Promise<DroppedFolder> {
  const files: FolderFile[] = [];
  const pruned: string[] = [];
  let gitRemote: string | null = null;
  const visit = async (dir: EntryLike, prefix: string) => {
    for (const e of await readAll(dir)) {
      const rel = prefix ? `${prefix}/${e.name}` : e.name;
      if (e.isDirectory) {
        if (PRUNE_DIRS.has(e.name)) {
          pruned.push(rel);
          if (e.name === ".git" && !prefix) {
            const config = (await readAll(e)).find((x) => x.isFile && x.name === "config");
            if (config) { try { gitRemote = parseGitRemote(await (await entryFile(config)).text()); } catch { /* no remote */ } }
          }
          continue;
        }
        await visit(e, rel);
      } else if (e.isFile) {
        try { files.push({ path: rel, file: await entryFile(e) }); } catch { /* unreadable: not offered */ }
      }
    }
  };
  await visit(root, "");
  return { name: root.name, files, pruned, gitRemote };
}

/** A folder chosen with `<input type="file" webkitdirectory>` — the phone's way, and anywhere a drag
 *  is not possible. Each file carries `webkitRelativePath` (`<folder>/<path>`). */
export async function folderFromPicked(list: FileList | File[]): Promise<DroppedFolder | null> {
  const all = Array.from(list as ArrayLike<File>);
  if (all.length === 0) return null;
  const rel = (f: File) => ((f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name);
  const name = rel(all[0]).split("/")[0] || "Folder";
  const files: FolderFile[] = [];
  const pruned = new Set<string>();
  let gitRemote: string | null = null;
  for (const f of all) {
    const parts = rel(f).split("/").slice(1);
    if (parts.length === 0) continue;
    const at = parts.slice(0, -1).findIndex((p) => PRUNE_DIRS.has(p));
    if (at >= 0) {
      pruned.add(parts.slice(0, at + 1).join("/"));
      if (parts[0] === ".git" && parts.length === 2 && parts[1] === "config") {
        try { gitRemote = parseGitRemote(await f.text()); } catch { /* no remote */ }
      }
      continue;
    }
    files.push({ path: parts.join("/"), file: f });
  }
  return { name, files, pruned: [...pruned], gitRemote };
}

// ── the server ───────────────────────────────────────────────────────────────────────────────────

export class FolderImportError extends Error {
  constructor(readonly status: number, message: string) { super(message); }
}

async function json<T>(res: Response): Promise<T> {
  let body: unknown = null;
  try { body = await res.json(); } catch { /* status-derived message below */ }
  if (!res.ok) {
    const detail = (body as { detail?: unknown } | null)?.detail;
    throw new FolderImportError(res.status, typeof detail === "string" ? detail : `Import failed (${res.status})`);
  }
  return body as T;
}

/** Ask the server what of this folder is imported. Sends paths and sizes and the text of every
 *  `.gitignore`; no file's contents. */
export async function planFolder(folder: DroppedFolder, name: string, target: FolderTarget | null,
  fetchImpl: typeof fetch = fetch): Promise<FolderPlan> {
  const gitignores: Record<string, string> = {};
  for (const f of folder.files) {
    if (f.path === ".gitignore" || f.path.endsWith("/.gitignore")) {
      try { gitignores[f.path] = (await f.file.text()).slice(0, 256 * 1024); } catch { /* unreadable: ignored */ }
    }
  }
  return json<FolderPlan>(await fetchImpl("/api/workspace/folder/plan", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, files: folder.files.map((f) => ({ path: f.path, size: f.file.size })),
      gitignores, pruned: folder.pruned, ...(target ? { slug: target.slug, into: target.into } : {}) }),
  }));
}

/** Split files into request-sized batches (at most `maxBytes` and `maxFiles` each; a file larger
 *  than `maxBytes` goes alone). */
export function batches<T extends { file: { size: number } }>(files: T[], maxBytes = BATCH_BYTES, maxFiles = BATCH_FILES): T[][] {
  const out: T[][] = [];
  let cur: T[] = [], bytes = 0;
  for (const f of files) {
    if (cur.length && (bytes + f.file.size > maxBytes || cur.length >= maxFiles)) { out.push(cur); cur = []; bytes = 0; }
    cur.push(f); bytes += f.file.size;
  }
  if (cur.length) out.push(cur);
  return out;
}

/** Send the plan's accepted files, batch by batch. Returns what the server refused on arrival (a
 *  key inside an innocent name, a file larger than it said). */
export async function uploadFolder(plan: FolderPlan, folder: DroppedFolder,
  onProgress: (sent: number, total: number) => void, fetchImpl: typeof fetch = fetch,
  signal?: AbortSignal): Promise<{ path: string; reason: string }[]> {
  const wanted = new Set(plan.accepted.map((a) => a.path));
  const files = folder.files.filter((f) => wanted.has(f.path));
  const refused: { path: string; reason: string }[] = [];
  let sent = 0;
  onProgress(0, files.length);
  for (const batch of batches(files)) {
    const form = new FormData();
    for (const f of batch) { form.append("paths", f.path); form.append("files", f.file, f.path.split("/").pop() || "file"); }
    const out = await json<{ skipped: { path: string; reason: string }[] }>(await fetchImpl(
      `/api/workspace/folder/${encodeURIComponent(plan.plan_id)}/files`, { method: "POST", body: form, signal }));
    refused.push(...(out.skipped ?? []));
    sent += batch.length;
    onProgress(sent, files.length);
  }
  return refused;
}

export async function finishFolder(planId: string, fetchImpl: typeof fetch = fetch): Promise<FolderResult> {
  return json<FolderResult>(await fetchImpl(`/api/workspace/folder/${encodeURIComponent(planId)}/finish`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
  }));
}

// ── words ────────────────────────────────────────────────────────────────────────────────────────

export function formatSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** The skipped list grouped by reason, largest group first: `[reason, count, examples]`. */
export function skippedGroups(plan: Pick<FolderPlan, "skipped" | "counts">): [string, number, string[]][] {
  const examples: Record<string, string[]> = {};
  for (const s of plan.skipped) (examples[s.reason] ??= []).length < 5 && examples[s.reason].push(s.path);
  return Object.entries(plan.counts).sort((a, b) => b[1] - a[1]).map(([r, n]) => [r, n, examples[r] ?? []]);
}

// ── the event that opens the sheet ───────────────────────────────────────────────────────────────

/** Fired by any surface a folder lands on; `FolderImportHost` (MinutesShell) opens the sheet. */
export const FOLDER_IMPORT_EVENT = "vexa:folder-import";
/** Fired when an import completed, so the chat can say so in one line. */
export const FOLDER_IMPORTED_EVENT = "vexa:folder-imported";

export type FolderImportRequest =
  | { kind: "entries"; entries: EntryLike[]; into?: string | null }
  | { kind: "folder"; folder: DroppedFolder; into?: string | null };

export function requestFolderImport(req: FolderImportRequest): void {
  window.dispatchEvent(new CustomEvent(FOLDER_IMPORT_EVENT, { detail: req }));
}
