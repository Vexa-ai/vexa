"use client";
/** THE FOLDER SHEET, and the host that opens it (founder 2026-10-13: "as we drop calls we can drop
 *  folder"). A folder dropped anywhere on the app — the rail, the chat, the empty state — or picked
 *  with "New workspace from folder…" opens one confirmation: the name (the folder's, editable), how
 *  many files and how large, what is left out and why, and where it lands. A consequential act is
 *  confirmed, so this is one of the few things that is a screen rather than a tool call; everything
 *  it decides is the server's answer (`POST /api/workspace/folder/plan`).
 *
 *  A folder that is a git repository with a remote is offered as a LINK first — the existing
 *  "import a repository" route, its history and its remote kept — and as a plain copy second.
 *  Dropped on a workspace (a workspace chip, or a chat that writes to one), it offers "Add into
 *  <workspace>/<folder>" before a new workspace.
 *
 *  The outcome is said once in the chat (`FOLDER_IMPORTED_EVENT` → `FolderImportedCards`), with a
 *  link to what was made. */
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { WorkspaceName } from "../ui-kit/WsLink";
import { Modal } from "../ui-kit/Modal";
import { workspacePath } from "../platform/workspaceRoute";
import { useFeature } from "../platform/features";
import { importWorkspace } from "../data/workspaceApi";
import { presentError } from "../data/apiClient";
import {
  FOLDER_IMPORT_EVENT, FOLDER_IMPORTED_EVENT, droppedDirectories, finishFolder, formatSize, planFolder,
  skippedGroups, uploadFolder, walkDirectory,
  type DroppedFolder, type FolderImportRequest, type FolderPlan, type FolderResult, type FolderTarget,
} from "./folderDrop";
import "./meetingBundle.css";
import "./folderImport.css";

export type IntoWorkspace = { slug: string; label: ReactNode };
export type FolderImported = { name: string; slug: string; path: string; files: number; skipped: number; linked: boolean };

const quiet = "t-xs c-3";
const btn = "bun-btn";

function intoFolder(name: string): string {
  return (name || "").replace(/[\\/\x00-\x1f]/g, "").trim() || "imported";
}

export function FolderImportSheet(p: {
  folder: DroppedFolder;
  /** A workspace the folder was dropped on (or the chat writes to): offered first as "Add into". */
  into?: IntoWorkspace | null;
  onClose: () => void;
  onImported?: (r: FolderImported) => void;
}) {
  const [name, setName] = useState(p.folder.name);
  const [dest, setDest] = useState<"new" | "into">(p.into ? "into" : "new");
  const [plan, setPlan] = useState<FolderPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"plan" | "sending" | "linking" | "done">("plan");
  const [progress, setProgress] = useState<[number, number]>([0, 0]);
  const [result, setResult] = useState<FolderImported | null>(null);
  const [lateSkips, setLateSkips] = useState<{ path: string; reason: string }[]>([]);
  const seq = useRef(0);

  const target = useCallback((n: string): FolderTarget | null =>
    dest === "into" && p.into ? { slug: p.into.slug, into: intoFolder(n) } : null, [dest, p.into]);

  // THE PLAN IS ASKED FOR AGAIN when the destination changes (the caps and the target's own rules
  // are the server's); a name edit is sent with the import itself.
  useEffect(() => {
    const mine = ++seq.current;
    setPlan(null); setError(null);
    planFolder(p.folder, name, target(name))
      .then((r) => { if (seq.current === mine) setPlan(r); })
      .catch((e) => { if (seq.current === mine) setError(presentError(e).headline || String(e)); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.folder, dest]);

  const announce = (r: FolderImported) => {
    setResult(r); setPhase("done");
    p.onImported?.(r);
    window.dispatchEvent(new CustomEvent(FOLDER_IMPORTED_EVENT, { detail: r }));
  };

  const doImport = async () => {
    if (!plan || plan.blocked) return;
    setPhase("sending"); setError(null);
    try {
      // the plan is re-made with the name as it stands now, so what is created is what was shown
      const final = name === plan.name && (target(name)?.into ?? null) === (plan.target?.into ?? null)
        ? plan : await planFolder(p.folder, name, target(name));
      if (final.blocked) { setPlan(final); setPhase("plan"); return; }
      const late = await uploadFolder(final, p.folder, (sent, total) => setProgress([sent, total]));
      setLateSkips(late);
      const r: FolderResult = await finishFolder(final.plan_id);
      announce({ name: r.name, slug: r.slug, path: r.path, files: r.files, skipped: r.skipped_total, linked: false });
    } catch (e) {
      setError(presentError(e).headline || String(e)); setPhase("plan");
    }
  };

  const doLink = async () => {
    if (!p.folder.gitRemote) return;
    setPhase("linking"); setError(null);
    try {
      const r = await importWorkspace(p.folder.gitRemote, "main", undefined, () => {});
      announce({ name: p.folder.name, slug: r.workspace, path: "", files: 0, skipped: 0, linked: true });
    } catch (e) {
      setError(presentError(e).headline || String(e)); setPhase("plan");
    }
  };

  const title = p.into && dest === "into" ? `Add folder to ${typeof p.into.label === "string" ? p.into.label : "a workspace"}` : "New workspace from folder";
  const groups = plan ? skippedGroups(plan) : [];
  const envSkipped = plan ? Object.keys(plan.counts).some((r) => r.startsWith(".env") || r.includes("credential") || r.includes("private key")) : false;
  const busy = phase === "sending" || phase === "linking";

  return (
    <Modal title={phase === "done" ? "Folder imported" : title} onClose={p.onClose} width={480}>
      <div data-folder-sheet className="fi-sheet">
        {phase === "done" && result ? (
          <div role="status" data-folder-done>
            <div>{result.linked ? "Linked" : "Created"}{" "}
              <a href={workspacePath(result.slug, result.path)} className="bun-link fw-600">{result.path ? `${result.name}/${result.path}` : result.name}</a>
              {result.linked ? " to its repository." : ` — ${result.files} file${result.files === 1 ? "" : "s"}${result.skipped ? `, ${result.skipped} left out` : ""}.`}
            </div>
            {lateSkips.length > 0 && <div className={quiet}>Left out on arrival: {lateSkips.slice(0, 5).map((s) => `${s.path} (${s.reason})`).join(", ")}</div>}
            <div className="fi-gap"><button type="button" className={btn} onClick={p.onClose}>Done</button></div>
          </div>
        ) : (<>
          {p.folder.gitRemote && (
            <div className="bun-card" data-folder-remote>
              <div>This folder is a git repository.</div>
              <button type="button" className={btn} data-variant="primary" disabled={busy} onClick={doLink}>
                Link to {p.folder.gitRemote}
              </button>
              <div className={quiet}>Keeps its history and its remote; pull and push work. Or copy the files instead, below.</div>
            </div>
          )}

          {p.into && (
            <div role="radiogroup" aria-label="Where it goes" className="fi-col">
              <label><input type="radio" name="folder-dest" checked={dest === "into"} disabled={busy} onChange={() => setDest("into")} />
                {" "}Add into {p.into.label}/{intoFolder(name)}</label>
              <label><input type="radio" name="folder-dest" checked={dest === "new"} disabled={busy} onChange={() => setDest("new")} />
                {" "}A new workspace</label>
            </div>
          )}

          <label className="fi-col">
            <span className={quiet}>{dest === "into" ? "Folder name" : "Name"}</span>
            <input aria-label="Workspace name" value={name} disabled={busy} maxLength={80}
              onChange={(e) => setName(e.target.value)} className="fi-input" />
          </label>

          {dest === "new" && <div className={quiet}>Private — only you. Share it afterwards from the workspace&rsquo;s page.</div>}

          {!plan && !error && <div className={quiet} role="status">Reading the folder…</div>}
          {plan && (
            <div data-folder-plan>
              <div className="c-1">{plan.files} file{plan.files === 1 ? "" : "s"} · {formatSize(plan.total_bytes)}</div>
              <div className={quiet}>Limits here: {formatSize(plan.limits.max_bytes)} and {plan.limits.max_files} files per folder, {formatSize(plan.limits.file_max_bytes)} per file.</div>
              {groups.length > 0 && (
                <details className="fi-gap-s" open={envSkipped}>
                  <summary className={quiet}>Left out: {groups.reduce((n, g) => n + g[1], 0)}</summary>
                  <ul className={`${quiet} fi-list`}>
                    {groups.map(([reason, n, ex]) => (
                      <li key={reason}>{n} · {reason}{ex.length ? ` — ${ex.join(", ")}${n > ex.length ? ", …" : ""}` : ""}</li>
                    ))}
                  </ul>
                </details>
              )}
              {envSkipped && <div role="note" className="bun-small fi-gap-s">Secrets are never imported: .env files and keys stay on your computer.</div>}
              {plan.blocked && <div role="alert" className="bun-small bun-danger fi-gap-s">{plan.blocked}</div>}
            </div>
          )}
          {phase === "sending" && <div role="status" className={quiet}>Uploading {progress[0]} of {progress[1]} files…
            <progress value={progress[1] ? progress[0] / progress[1] : undefined} max={1} className="fi-progress" /></div>}
          {phase === "linking" && <div role="status" className={quiet}>Linking the repository. Large repositories can take several minutes.</div>}
          {error && <div role="alert" className="bun-small bun-danger">{error}</div>}

          <div className="fi-actions">
            <button type="button" className={btn} onClick={p.onClose} disabled={busy}>Cancel</button>
            <button type="button" className={btn} data-variant={p.folder.gitRemote ? undefined : "primary"}
              disabled={!plan || !!plan.blocked || busy || !name.trim()} onClick={doImport}>Import</button>
          </div>
        </>)}
      </div>
    </Modal>
  );
}

/** Mounted once by the shell. Opens the sheet for every `FOLDER_IMPORT_EVENT`, and for a folder
 *  dropped anywhere on the window that no nearer zone took (the rail, the empty state). A folder
 *  dropped on an element carrying `data-ws` (a workspace chip) is offered into that workspace. */
type FolderImportHostProps = {
  /** The workspace the current chat writes to, when it is one (not Personal). */
  chatTarget?: IntoWorkspace | null;
  /** A label for a workspace slug, for "Add into <label>". */
  labelFor?: (slug: string) => ReactNode;
  onImported?: (r: FolderImported) => void;
};

/** Not mounted while `folder_drop` is switched off (`platform/features`): no window drop handler,
 *  no sheet. Files dropped on the chat attach as before; that is the chat's own drop zone. */
export function FolderImportHost(p: FolderImportHostProps) {
  return useFeature("folder_drop") ? <FolderImportHostBody {...p} /> : null;
}

function FolderImportHostBody(p: FolderImportHostProps) {
  const [open, setOpen] = useState<{ folder: DroppedFolder; into: IntoWorkspace | null } | null>(null);
  const [reading, setReading] = useState(false);
  const chatTarget = useRef(p.chatTarget); chatTarget.current = p.chatTarget;
  const labelFor = useRef(p.labelFor); labelFor.current = p.labelFor;

  const intoFor = (slug: string | null | undefined): IntoWorkspace | null => {
    if (slug === undefined) return null;
    if (slug === null) return chatTarget.current ?? null;
    return { slug, label: labelFor.current?.(slug) ?? <WorkspaceName slug={slug} /> };
  };

  useEffect(() => {
    const onRequest = async (e: Event) => {
      const req = (e as CustomEvent<FolderImportRequest>).detail;
      if (!req) return;
      const into = intoFor(req.into);
      if (req.kind === "folder") { setOpen({ folder: req.folder, into }); return; }
      if (req.entries.length === 0) return;
      setReading(true);
      try { setOpen({ folder: await walkDirectory(req.entries[0]), into }); } finally { setReading(false); }
    };
    // THE WINDOW IS A DROP TARGET FOR FOLDERS. Without `preventDefault` on dragover a drop anywhere
    // outside a zone is the browser's to handle — it would open the file in place of the app.
    const over = (e: DragEvent) => {
      if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
      e.preventDefault();
    };
    const drop = (e: DragEvent) => {
      const dirs = droppedDirectories(e.dataTransfer);
      if (!Array.from(e.dataTransfer?.types ?? []).includes("Files")) return;
      e.preventDefault();
      if (dirs.length === 0) return;
      const chip = (e.target instanceof Element ? e.target.closest("[data-ws]") : null)?.getAttribute("data-ws");
      void onRequest(new CustomEvent(FOLDER_IMPORT_EVENT, {
        detail: { kind: "entries", entries: dirs, into: chip && chip !== "personal" ? chip : undefined },
      }));
    };
    window.addEventListener(FOLDER_IMPORT_EVENT, onRequest);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener(FOLDER_IMPORT_EVENT, onRequest);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (reading && !open) return <div role="status" className="at-drop-overlay"><span className="t-md fw-600">Reading the folder…</span></div>;
  if (!open) return null;
  return <FolderImportSheet folder={open.folder} into={open.into} onClose={() => setOpen(null)}
    onImported={(r) => p.onImported?.(r)} />;
}

/** The one line the chat says when a folder landed: what was made, and the link to it. */
export function useFolderImportedCards() {
  const [cards, setCards] = useState<(FolderImported & { id: number })[]>([]);
  useEffect(() => {
    let n = 0;
    const on = (e: Event) => {
      const d = (e as CustomEvent<FolderImported>).detail;
      if (d) setCards((all) => [...all, { ...d, id: n++ }]);
    };
    window.addEventListener(FOLDER_IMPORTED_EVENT, on);
    return () => window.removeEventListener(FOLDER_IMPORTED_EVENT, on);
  }, []);
  return { cards, dismiss: (id: number) => setCards((all) => all.filter((c) => c.id !== id)) };
}

export function FolderImportedCards({ cards }: { cards: (FolderImported & { id: number })[] }) {
  if (cards.length === 0) return null;
  return <>{cards.map((c) => (
    <div key={c.id} role="status" data-folder-card className="bun-card">
      <div>{c.linked ? "Linked the repository as " : "Imported your folder as "}
        <a href={workspacePath(c.slug, c.path)} className="bun-link fw-600">{c.path ? `${c.name}/${c.path}` : c.name}</a>
        {c.linked ? "." : ` — ${c.files} file${c.files === 1 ? "" : "s"}${c.skipped ? `, ${c.skipped} left out` : ""}.`}
      </div>
    </div>
  ))}</>;
}
