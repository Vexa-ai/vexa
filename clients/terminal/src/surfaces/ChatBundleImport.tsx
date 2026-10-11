"use client";
/** A MEETING BUNDLE DROPPED INTO A CHAT IS A CALL TO IMPORT, not a file to attach (founder,
 *  2026-10-11: *"they would just drop in chat and it would understand we import a call"*).
 *
 *  Every file the chat is handed — dropped anywhere on it, pasted, or picked with the attach
 *  button — passes through `route` first. A file whose own bytes say it is a meeting bundle
 *  (`sniffMeetingBundle`, the contract's rules, never its name) goes to the SAME import route the
 *  meetings list's "Import meeting" uses (`confirmImport` → `POST /api/meetings/import`, streamed by
 *  the gateway under BUNDLE_TIMEOUT), and its workspace and page follow (`restoreParts`). Every other
 *  file comes back to the caller and is attached as before, so a mixed drop does both.
 *
 *  The outcome is a card in the conversation:
 *    · importing — the file, the phase, and how much has gone up;
 *    · imported — the call's title, and a link that opens its transcript (it is in Calls too);
 *    · already imported — said, and the link to the call it already is;
 *    · refused — the importer's own code and sentence, in words the person can act on.
 *  The server decides all of it: who may import (the caller, into their own account), whether the
 *  file is whole, whether it is new. Nothing here grants or checks access. */
import { useCallback, useRef, useState, type CSSProperties } from "react";
import { OPEN_MEETING_EVENT } from "../platform";
import { Icon } from "../ui-kit";
import { presentError } from "./apiClient";
import { refreshMeetings } from "./liveMeetings";
import { BundleError, confirmImport, previewImport, restoreParts, type RestoreResult } from "./meetingBundle";
import { sniffMeetingBundle } from "./meetingBundleSniff";

export type BundleCard =
  | { id: string; key: string; file: string; state: "importing"; phase: string; fraction: number | null }
  | { id: string; key: string; file: string; state: "imported"; meetingId: number; title: string; segments: number; recordings: number;
      restored: RestoreResult | null; restoreError: string | null; restoring: boolean }
  | { id: string; key: string; file: string; state: "duplicate"; meetingId: number | null; title: string }
  | { id: string; key: string; file: string; state: "refused"; code: string; reason: string; detail: string };

/** What each refusal means to the person who dropped the file (the code stays visible beside it). */
const REASON: Record<string, string> = {
  hash_mismatch: "the file does not match its signature — it was changed or damaged after export",
  unsupported_version: "it was made in a bundle version this deployment does not read",
  too_large: "it is too big to import",
  not_a_bundle: "it is not a meeting bundle",
  manifest_mismatch: "its contents do not match its manifest",
  unsafe_path: "it holds a file path a bundle may not carry",
  unsafe_entry: "it holds an entry a bundle may not carry",
  unsafe_media: "a recording in it is not the format it claims",
  invalid_part: "a part of it is not valid",
  http_401: "you are not signed in",
  http_403: "you are not allowed to import meetings here",
  http_404: "this deployment does not import meetings",
};

function refusal(e: unknown): { code: string; reason: string; detail: string } {
  if (e instanceof BundleError) {
    return { code: e.code, reason: REASON[e.code] ?? (e.status >= 500 ? "the server could not import it" : "the server refused it"), detail: e.message };
  }
  return { code: "network", reason: "the upload did not reach the server", detail: (({ headline, detail }) => detail && detail !== headline ? `${headline} ${detail}` : headline)(presentError(e)) };
}

/** One file is one import at a time: the same file dropped again while it is still going up is
 *  not sent twice. Name + size + mtime is what a browser can say about "the same file". */
function fileKey(f: File): string {
  return `${f.name}\u0000${f.size}\u0000${f.lastModified}`;
}

export function openCall(meetingId: number): void {
  window.dispatchEvent(new CustomEvent(OPEN_MEETING_EVENT, { detail: { ref: String(meetingId) } }));
}

export function useBundleImports() {
  const [cards, setCards] = useState<BundleCard[]>([]);
  const inFlight = useRef(new Set<string>());
  const seq = useRef(0);

  const put = useCallback((id: string, next: (c: BundleCard) => BundleCard) => {
    setCards((all) => all.map((c) => (c.id === id ? next(c) : c)));
  }, []);

  const importOne = useCallback(async (file: File) => {
    const key = fileKey(file);
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    const id = `bundle-${seq.current++}`;
    const base = { id, key, file: file.name || "meeting bundle" };
    setCards((all) => [...all, { ...base, state: "importing", phase: "Importing", fraction: null }]);
    const progress = (phase: string) => (fraction: number) => put(id, (c) => (c.state === "importing" ? { ...c, phase, fraction } : c));
    try {
      const result = await confirmImport(file, fetch, progress("Importing"));
      refreshMeetings();
      const needsRestore = result.handoff.workspace_files > 0 || result.handoff.notes_page;
      put(id, () => ({
        ...base, state: "imported", meetingId: result.meeting_id, title: result.meeting.title || "Untitled call",
        segments: result.segments, recordings: result.media.length, restored: null, restoreError: null, restoring: needsRestore,
      }));
      if (needsRestore) {
        // The meeting has landed; the agent domain lands what it owns from the same file. A refusal
        // there is said on the card — the call stays imported either way.
        try {
          const restored = await restoreParts(file, result.meeting_id, fetch);
          put(id, (c) => (c.state === "imported" ? { ...c, restored, restoring: false } : c));
        } catch (e) {
          const r = refusal(e);
          put(id, (c) => (c.state === "imported" ? { ...c, restoreError: `${r.detail} (${r.code})`, restoring: false } : c));
        }
      }
    } catch (e) {
      if (e instanceof BundleError && e.code === "duplicate_import") {
        // The refusal names the meeting only in prose; the importer's own dry run names it as data.
        let meetingId: number | null = null;
        let title = "";
        try {
          const p = await previewImport(file, fetch);
          meetingId = p.duplicate_of;
          title = p.meeting.title || "";
        } catch { /* the card still says it is already imported */ }
        put(id, () => ({ ...base, state: "duplicate", meetingId, title: title || "this call" }));
      } else {
        put(id, () => ({ ...base, state: "refused", ...refusal(e) }));
      }
    } finally {
      inFlight.current.delete(key);
    }
  }, [put]);

  /** Start importing every bundle among `files`; answer the files that are NOT bundles, in order,
   *  for the caller to attach as it always has. */
  const route = useCallback(async (files: File[]): Promise<File[]> => {
    const claims = await Promise.all(files.map((f) => sniffMeetingBundle(f).catch(() => "not_bundle" as const)));
    const rest: File[] = [];
    files.forEach((f, i) => { if (claims[i] === "bundle") void importOne(f); else rest.push(f); });
    return rest;
  }, [importOne]);

  const dismiss = useCallback((id: string) => setCards((all) => all.filter((c) => c.id !== id || c.state === "importing")), []);

  return { cards, route, dismiss };
}

const card: CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4, margin: "0 0 14px", padding: "10px 12px",
  border: "1px solid var(--line2)", borderRadius: 10, background: "var(--panel)", fontSize: 13, color: "var(--t2)", lineHeight: 1.45,
};
const link: CSSProperties = { background: "none", border: "none", padding: 0, color: "var(--blue)", cursor: "pointer", font: "inherit", textAlign: "left" };
const quiet: CSSProperties = { fontSize: 12, color: "var(--t3)" };

function Close({ onClick }: { onClick: () => void }) {
  return <button type="button" aria-label="Dismiss" title="Dismiss" onClick={onClick}
    style={{ marginLeft: "auto", background: "none", border: "none", color: "var(--t3)", cursor: "pointer", padding: 0, flex: "none" }}>
    <Icon name="x" size={12} />
  </button>;
}

/** The cards, at the foot of the conversation. Everything a bundle carries is a text node. */
export function BundleImportCards({ cards, onDismiss }: { cards: BundleCard[]; onDismiss: (id: string) => void }) {
  if (cards.length === 0) return null;
  return <>
    {cards.map((c) => {
      if (c.state === "importing") {
        return <div key={c.id} data-bundle-card="importing" role="status" style={card}>
          <div>Importing a call from <b style={{ color: "var(--t1)", fontWeight: 600 }}>{c.file}</b>…</div>
          <div style={quiet}>{c.phase}{c.fraction == null ? "" : ` ${Math.round(c.fraction * 100)}%`}</div>
          <progress value={c.fraction ?? undefined} max={1} style={{ width: "100%" }} />
        </div>;
      }
      if (c.state === "imported") {
        return <div key={c.id} data-bundle-card="imported" role="status" style={card}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span>Imported the call</span>
            <Close onClick={() => onDismiss(c.id)} />
          </div>
          <button type="button" style={{ ...link, fontWeight: 600 }} onClick={() => openCall(c.meetingId)} title="Open its transcript">
            {c.title}
          </button>
          <div style={quiet}>
            {c.segments} transcript segment{c.segments === 1 ? "" : "s"}
            {c.recordings ? ` · ${c.recordings} recording${c.recordings === 1 ? "" : "s"}` : ""} · in your Calls, yours only
          </div>
          {c.restoring && <div style={quiet}>Restoring its workspace and page…</div>}
          {c.restored?.workspace && <div style={quiet}>Workspace restored: {c.restored.workspace.files} files in &ldquo;{c.restored.workspace.slug}&rdquo;.</div>}
          {c.restored?.notes_page && <div style={quiet}>{c.restored.notes_page.written ? "Its page is on your desk." : "Your desk already had its page; it was kept."}</div>}
          {c.restoreError && <div role="alert" style={{ fontSize: 12, color: "var(--danger)" }}>The call was imported, but its workspace and page were not: {c.restoreError}</div>}
        </div>;
      }
      if (c.state === "duplicate") {
        return <div key={c.id} data-bundle-card="duplicate" role="status" style={card}>
          <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
            <span>You already imported this call</span>
            <Close onClick={() => onDismiss(c.id)} />
          </div>
          {c.meetingId != null
            ? <button type="button" style={{ ...link, fontWeight: 600 }} onClick={() => openCall(c.meetingId!)} title="Open its transcript">{c.title}</button>
            : <div style={{ color: "var(--t1)" }}>{c.title}</div>}
          <div style={quiet}>Nothing was imported again. Delete that call to import the file anew.</div>
        </div>;
      }
      return <div key={c.id} data-bundle-card="refused" role="alert" style={{ ...card, borderColor: "var(--danger)" }}>
        <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
          <span style={{ color: "var(--danger)" }}>Not imported: {c.reason}</span>
          <Close onClick={() => onDismiss(c.id)} />
        </div>
        <div style={quiet}>{c.file} — {c.detail} <code style={{ fontFamily: "var(--mono)" }}>{c.code}</code></div>
      </div>;
    })}
  </>;
}
