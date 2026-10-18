/** FILES INTO A CHAT — dropped anywhere on the panel, pasted, or picked (founder 2026-10-10: "a nice
 *  UI for dropping a file or a screenshot into the chat, and dropping anywhere on the whole chat
 *  panel should accept it"). The reference is the Claude Code composer: attachments sit above the
 *  text as rounded thumbnails or chips, each with its own ×, each uploading on its own.
 *
 *  Every file that is offered gets a visible outcome (P18): it uploads, or it says why it did not —
 *  too large, a type the composer does not take, or an upload that failed and can be retried. Nothing
 *  is dropped on the floor.
 *
 *  The upload path is the one the attach button always used: `POST /api/workspace/upload`
 *  (agent-api `routers/workspaces.py`), one file per request so each has its own progress and its
 *  own failure. The 25 MB ceiling mirrors that route's `MAX_UPLOAD_BYTES`; checking it here only
 *  saves a doomed upload — the server still enforces it. */
import { useEffect, useRef, useState, type DragEvent, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { Icon } from "../ui-kit";

/** agent-api `api_shared.MAX_UPLOAD_BYTES`. */
export const MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024;

/** What the file picker offers — and therefore what a drop or a paste may attach. */
export const ATTACHMENT_ACCEPT = [
  "image/*", ".pdf", ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl", ".yaml", ".yml", ".log",
  ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
].join(",");

export type UploadedWorkspaceFile = { name: string; path: string };
export type AttachmentStatus = "uploading" | "done" | "error" | "refused";
export type ComposerAttachment = {
  id: string;
  file: File;
  isImage: boolean;
  previewUrl?: string;
  status: AttachmentStatus;
  /** 0..1 while uploading, when the browser reports it. */
  progress: number;
  uploaded?: UploadedWorkspaceFile;
  /** Why it failed or was refused, in words for the person. */
  error?: string;
};

/** Does `file` match an `accept` list (`image/*`, `.pdf`, `application/json`, …)? */
export function acceptsFile(file: File, accept: string = ATTACHMENT_ACCEPT): boolean {
  const name = (file.name || "").toLowerCase();
  const type = (file.type || "").toLowerCase();
  return accept.split(",").map((s) => s.trim().toLowerCase()).filter(Boolean).some((rule) => {
    if (rule.startsWith(".")) return name.endsWith(rule);
    if (rule.endsWith("/*")) return type.startsWith(rule.slice(0, -1));
    return type === rule;
  });
}

/** The refusal for a file the composer will not upload, or null when it may. */
export function refusalFor(file: File): string | null {
  if (file.size > MAX_ATTACHMENT_BYTES) return `Too large (${formatBytes(file.size)}) — the limit is 25 MB`;
  if (!acceptsFile(file)) return "This file type isn't supported";
  return null;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(n < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

/** Truncate in the MIDDLE so the extension stays readable: `quarterly-rep…ort.pdf`. */
export function middleTruncate(name: string, max = 26): string {
  if (name.length <= max) return name;
  const dot = name.lastIndexOf(".");
  const ext = dot > 0 && name.length - dot <= 8 ? name.slice(dot) : "";
  const stem = ext ? name.slice(0, dot) : name;
  const keep = Math.max(4, max - ext.length - 1);
  const head = Math.ceil(keep * 0.6);
  const tail = keep - head;
  return `${stem.slice(0, head)}…${tail > 0 ? stem.slice(-tail) : ""}${ext}`;
}

/** Files carried by a drag — and only files: a dragged link or text selection is not an attachment. */
export function dragHasFiles(e: { dataTransfer: DataTransfer | null }): boolean {
  const types = e.dataTransfer?.types;
  if (!types) return false;
  return Array.from(types as ArrayLike<string>).includes("Files");
}

/** The files a paste carries. A clipboard holding text as well (a spreadsheet range copies as text
 *  AND a picture of itself) is a paste of the text, so it attaches nothing. */
export function pastedFiles(data: DataTransfer | null): File[] {
  if (!data) return [];
  if ((data.getData?.("text/plain") ?? "").length > 0) return [];
  return Array.from(data.items ?? [])
    .filter((item) => item.kind === "file")
    .map((item) => item.getAsFile())
    .filter((f): f is File => !!f);
}

/** Upload ONE file on the attach button's route, reporting progress. XHR rather than `fetch`
 *  because `fetch` cannot report how much of a request body has gone. */
export function uploadAttachment(file: File, onProgress: (p: number) => void, signal: AbortSignal): Promise<UploadedWorkspaceFile> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("files", file, file.name || "upload");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/workspace/upload");
    xhr.upload.onprogress = (e: ProgressEvent) => { if (e.lengthComputable && e.total > 0) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let body: { files?: UploadedWorkspaceFile[]; detail?: unknown } = {};
      try { body = JSON.parse(xhr.responseText || "{}"); } catch { /* keep the status-derived message */ }
      if (xhr.status >= 200 && xhr.status < 300 && body.files?.[0]) { resolve(body.files[0]); return; }
      const detail = typeof body.detail === "string" ? body.detail : "";
      reject(new Error(detail || (xhr.status === 413 ? "Too large for the server" : `Upload failed (${xhr.status})`)));
    };
    xhr.onerror = () => reject(new Error("Upload failed — network error"));
    xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
    signal.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.send(form);
  });
}

// ── the drop target ─────────────────────────────────────────────────────────────────────────────

/** A drop zone that counts enter/leave so crossing into a child does not flicker the overlay, and
 *  that only reacts to drags carrying files. */
export function FileDropZone({ onFiles, children, label = "Drop files to attach" }: {
  onFiles: (files: File[]) => void; children: ReactNode; label?: string;
}) {
  const [over, setOver] = useState(false);
  const depth = useRef(0);
  // A drag that ends outside the window (or is cancelled with Escape) never sends us a dragleave
  // for every enter; reset on the window's own end-of-drag signals.
  useEffect(() => {
    if (!over) return;
    const reset = () => { depth.current = 0; setOver(false); };
    window.addEventListener("dragend", reset);
    window.addEventListener("drop", reset);
    return () => { window.removeEventListener("dragend", reset); window.removeEventListener("drop", reset); };
  }, [over]);
  const onDragEnter = (e: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    depth.current += 1;
    setOver(true);
  };
  const onDragOver = (e: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  };
  const onDragLeave = (e: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(e)) return;
    depth.current = Math.max(0, depth.current - 1);
    if (depth.current === 0) setOver(false);
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    e.stopPropagation();
    depth.current = 0;
    setOver(false);
    const files = Array.from(e.dataTransfer?.files ?? []);
    if (files.length) onFiles(files);
  };
  return (
    <div data-chat-drop-zone onDragEnter={onDragEnter} onDragOver={onDragOver} onDragLeave={onDragLeave} onDrop={onDrop}
      style={{ position: "relative", height: "100%", minHeight: 0, display: "flex", flexDirection: "column" }}>
      {children}
      {over && (
        <div data-drop-overlay role="status" aria-live="polite"
          style={{ position: "absolute", inset: 8, zIndex: 40, pointerEvents: "none", borderRadius: 16,
            border: "2px dashed var(--accent)", background: "color-mix(in srgb, var(--rail) 86%, var(--accent) 14%)",
            display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 10, color: "var(--t1)" }}>
          <span style={{ width: 44, height: 44, borderRadius: 12, background: "var(--accentbg)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <Icon name="upload" size={22} />
          </span>
          <span style={{ fontSize: 14, fontWeight: 600 }}>{label}</span>
          <span style={{ fontSize: 12, color: "var(--t3)" }}>Images, PDFs, docs and spreadsheets · up to 25 MB each</span>
        </div>
      )}
    </div>
  );
}

// ── the tray ────────────────────────────────────────────────────────────────────────────────────

function statusWords(a: ComposerAttachment): string {
  if (a.status === "uploading") return `uploading${a.progress > 0 ? ` ${Math.round(a.progress * 100)}%` : ""}`;
  if (a.status === "error") return `upload failed: ${a.error ?? "error"}`;
  if (a.status === "refused") return `not attached: ${a.error ?? "refused"}`;
  return "attached";
}

function ProgressRing({ progress }: { progress: number }) {
  const r = 9, c = 2 * Math.PI * r;
  const known = progress > 0;
  return (
    <svg width={22} height={22} viewBox="0 0 22 22" aria-hidden="true" className={known ? undefined : "vx-op-spin"} style={{ display: "block" }}>
      <circle cx={11} cy={11} r={r} fill="none" stroke="var(--line2)" strokeWidth={2.5} />
      <circle cx={11} cy={11} r={r} fill="none" stroke="var(--accent)" strokeWidth={2.5} strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={known ? c * (1 - progress) : c * 0.7} transform="rotate(-90 11 11)" />
    </svg>
  );
}

const removeButtonStyle = {
  width: 18, height: 18, borderRadius: 999, border: "none", background: "var(--panel)", color: "var(--t2)",
  cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", padding: 0,
  boxShadow: "0 1px 3px rgba(0,0,0,.3)", flex: "none",
} as const;

export function AttachmentTray({ attachments, onRemove, onRetry, onPreview, onExit }: {
  attachments: ComposerAttachment[];
  onRemove: (id: string) => void;
  onRetry: (id: string) => void;
  onPreview: (a: ComposerAttachment) => void;
  /** Focus leaves the tray (the last item was removed from the keyboard). */
  onExit?: () => void;
}) {
  const itemRefs = useRef(new Map<string, HTMLElement>());
  if (attachments.length === 0) return null;
  const removeFromKeyboard = (e: ReactKeyboardEvent, id: string) => {
    if (e.key !== "Backspace" && e.key !== "Delete") return;
    if (e.target !== e.currentTarget) return;   // a key on the × or retry inside is that button's
    e.preventDefault();
    const i = attachments.findIndex((a) => a.id === id);
    const next = attachments[i + 1] ?? attachments[i - 1];
    onRemove(id);
    if (next) window.setTimeout(() => itemRefs.current.get(next.id)?.focus(), 0);
    else onExit?.();
  };
  return (
    <div role="list" aria-label="Attachments" data-attachment-tray
      style={{ display: "flex", alignItems: "flex-end", flexWrap: "wrap", gap: 8, minWidth: 0 }}>
      {attachments.map((a) => {
        const name = a.file.name || (a.isImage ? "pasted image" : "upload");
        const failed = a.status === "error" || a.status === "refused";
        const label = `${name}, ${formatBytes(a.file.size)}, ${statusWords(a)}`;
        const setRef = (el: HTMLElement | null) => { if (el) itemRefs.current.set(a.id, el); else itemRefs.current.delete(a.id); };
        const remove = (
          <button type="button" aria-label={`Remove ${name}`} title="Remove" onClick={() => onRemove(a.id)} style={removeButtonStyle}>
            <Icon name="x" size={10} />
          </button>
        );
        const retry = a.status === "error" ? (
          <button type="button" aria-label={`Retry uploading ${name}`} title="Retry" onClick={() => onRetry(a.id)}
            style={{ ...removeButtonStyle, color: "var(--danger)" }}>
            <Icon name="refresh" size={10} />
          </button>
        ) : null;
        if (a.isImage && a.previewUrl && a.status !== "refused") {
          return (
            <div key={a.id} role="listitem" ref={setRef} tabIndex={0} aria-label={label} title={failed ? `${name} — ${a.error}` : name}
              data-attachment-thumb data-status={a.status}
              onKeyDown={(e) => {
                if ((e.key === "Enter" || e.key === " ") && e.target === e.currentTarget) { e.preventDefault(); onPreview(a); return; }
                removeFromKeyboard(e, a.id);
              }}
              style={{ position: "relative", width: 72, height: 72, flex: "none", borderRadius: 10, overflow: "hidden",
                border: `1px solid ${failed ? "var(--danger)" : "var(--line2)"}`, background: "var(--bg)", outlineOffset: 2 }}>
              <img src={a.previewUrl} alt={name} onClick={() => onPreview(a)}
                style={{ width: "100%", height: "100%", objectFit: "cover", display: "block", cursor: "zoom-in",
                  opacity: a.status === "uploading" ? 0.55 : 1, filter: failed ? "grayscale(0.6)" : undefined }} />
              {a.status === "uploading" && (
                <span style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", pointerEvents: "none" }}>
                  <ProgressRing progress={a.progress} />
                </span>
              )}
              {failed && (
                <span style={{ position: "absolute", left: 0, right: 0, bottom: 0, background: "var(--danger)", color: "var(--on-accent)", fontSize: 10,
                  lineHeight: "15px", textAlign: "center", pointerEvents: "none" }}>Failed</span>
              )}
              <span style={{ position: "absolute", top: 4, right: 4, display: "flex", gap: 3 }}>{retry}{remove}</span>
            </div>
          );
        }
        return (
          <div key={a.id} role="listitem" ref={setRef} tabIndex={0} aria-label={label} title={failed ? `${name} — ${a.error}` : name}
            data-attachment-chip data-status={a.status}
            onKeyDown={(e) => removeFromKeyboard(e, a.id)}
            style={{ display: "flex", alignItems: "center", gap: 8, maxWidth: 260, minWidth: 0, height: 48, boxSizing: "border-box",
              border: `1px solid ${failed ? "var(--danger)" : "var(--line2)"}`, borderRadius: 10,
              background: failed ? "var(--dangerbg)" : "var(--panel2)", color: "var(--t2)", padding: "6px 8px 6px 6px", outlineOffset: 2 }}>
            <span style={{ width: 34, height: 34, borderRadius: 7, display: "flex", alignItems: "center", justifyContent: "center", flex: "none",
              background: "var(--bg)", color: failed ? "var(--danger)" : "var(--t3)" }}>
              {a.status === "uploading" ? <ProgressRing progress={a.progress} /> : <Icon name={failed ? "alert" : "file"} size={16} />}
            </span>
            <span style={{ display: "flex", flexDirection: "column", minWidth: 0, gap: 2 }}>
              <span style={{ fontSize: 12.5, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden" }}>{middleTruncate(name)}</span>
              <span style={{ fontSize: 11, color: failed ? "var(--danger)" : "var(--t3)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                {failed ? a.error : a.status === "uploading" ? `${formatBytes(a.file.size)} · uploading${a.progress > 0 ? ` ${Math.round(a.progress * 100)}%` : "…"}` : formatBytes(a.file.size)}
              </span>
            </span>
            <span style={{ display: "flex", gap: 3, marginLeft: 2 }}>{retry}{remove}</span>
          </div>
        );
      })}
    </div>
  );
}

/** A larger look at an attached image. Escape, the backdrop or the close button dismisses it. */
export function ImagePreview({ attachment, onClose }: { attachment: ComposerAttachment; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    window.addEventListener("keydown", onKey);
    return () => { window.removeEventListener("keydown", onKey); prev?.focus?.(); };
  }, [onClose]);
  const name = attachment.file.name || "pasted image";
  return (
    <div role="dialog" aria-modal="true" aria-label={`Preview of ${name}`} onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,.72)", display: "flex", alignItems: "center", justifyContent: "center", padding: 24 }}>
      <figure onClick={(e) => e.stopPropagation()} style={{ margin: 0, position: "relative", maxWidth: "min(92vw, 1200px)", maxHeight: "88vh", display: "flex", flexDirection: "column", gap: 8 }}>
        <img src={attachment.previewUrl} alt={name} style={{ maxWidth: "100%", maxHeight: "80vh", objectFit: "contain", borderRadius: 10, background: "var(--bg)", display: "block" }} />
        <figcaption style={{ alignSelf: "center", color: "var(--t1)", background: "var(--panel)", borderRadius: 999, padding: "3px 10px", fontSize: 12 }}>{name} · {formatBytes(attachment.file.size)}</figcaption>
        <button ref={closeRef} type="button" aria-label="Close preview" onClick={onClose}
          style={{ ...removeButtonStyle, position: "absolute", top: -10, right: -10, width: 28, height: 28 }}>
          <Icon name="x" size={14} />
        </button>
      </figure>
    </div>
  );
}
