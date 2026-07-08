"use client";
/** Meetings (mocked backend) — the differentiator flow.
 *  • "meetings" LIST (left): meetings; the live one auto-opens; click any to (re)open its meeting view.
 *  • "meeting" TAB (center): fixed meeting chrome around the Meeting Canvas body.
 *    The generated canvas view consumes this meeting's live MeetingState. */
import { useEffect, useRef, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { registerList, registerTab, registerCommand, type TabProps } from "../contributions";
import { Icon } from "../ui-kit";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { MEETING_CANVAS_CONTENT_INSET, MeetingCanvasView } from "../canvas/MeetingCanvasView";
import { type MeetingMock } from "./meetingModel";
import { useLiveMeetings, liveMeetingsNow, refreshMeetings } from "./liveMeetings";
import { usePreviewPinTab } from "./previewPinTab";
import { parseMeetingInput } from "./meetingId";
import { mintTranscriptShare, mintInvite, listSharedMemberships, type Membership } from "./workspaceApi";
import { createPlannedMeeting, deletePlannedMeeting, getCalendarConfig, setCalendarConfig, getCalendarSyncStatus, syncCalendarNow, type CalendarConfig, type CalendarSyncStamp } from "./plannedApi";
import { prepTabDescriptor } from "./meetingPrep";

// ── "Share session" — mint a link to this meeting's LIVE FEED (independent transcript share) and,
//    optionally, BUNDLE a shared-workspace invite into the SAME link (?tshare=…&invite=…). The two are
//    decoupled capabilities; this is the one-click way to hand someone both at once. ─────────────────
function platformSlug(display: string): string {
  return display === "Google Meet" ? "google_meet" : display.toLowerCase().replace(/\s+/g, "_");
}
function ShareSessionButton({ platform, native }: { platform: string; native: string }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("open");
  const [emails, setEmails] = useState("");
  const [ttlDays, setTtlDays] = useState(7);
  const [wsId, setWsId] = useState("");            // "" = transcript only; else also bundle this workspace invite
  const [shares, setShares] = useState<Membership[]>([]);
  const [link, setLink] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    void listSharedMemberships().then((ms) => setShares(ms.filter((m) => m.role === "owner" || m.role === "contributor"))).catch(() => {});
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const create = async () => {
    setBusy(true); setErr(null);
    try {
      const allowed = mode === "restricted" ? emails.split(/[,\s]+/).map((s) => s.trim()).filter(Boolean) : undefined;
      const t = await mintTranscriptShare({ platform, native_meeting_id: native, mode, allowed_emails: allowed, expires_in_sec: ttlDays * 86400 });
      const params = new URLSearchParams();
      params.set("tshare", t.token);
      if (wsId) {  // bundle a workspace membership invite into the same link
        const inv = await mintInvite({ workspace_id: wsId, role: "contributor", mode, expires_in_sec: ttlDays * 86400, max_uses: mode === "open" ? 50 : 1, allowed_emails: allowed });
        params.set("invite", inv.token);
      }
      setLink(`${window.location.origin}/?${params.toString()}`);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  const fieldStyle = { fontSize: 12, padding: "4px 6px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" } as const;
  return (
    <div ref={ref} style={{ position: "relative", flex: "none" }}>
      <button onClick={() => { setOpen((v) => !v); setLink(null); }} title="Share this meeting's live feed (and optionally a workspace)"
        style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "transparent", border: "1px solid var(--line2)", color: "var(--t2)", borderRadius: 6, padding: "2px 8px", fontSize: 12, cursor: "pointer" }}>
        <Icon name="upload" size={12} /> Share session
      </button>
      {open && (
        <div style={{ position: "absolute", top: "100%", right: 0, marginTop: 6, width: 280, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 10, boxShadow: "0 8px 28px rgba(0,0,0,.32)", padding: 12, zIndex: 50, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em" }}>Share session</div>
          <div style={{ display: "flex", gap: 6 }}>
            <select value={mode} disabled={busy} onChange={(e) => { setMode(e.target.value); setLink(null); }} style={{ ...fieldStyle, flex: 1 }}>
              <option value="open">anyone with link</option>
              <option value="restricted">restricted (emails)</option>
            </select>
            <select value={ttlDays} disabled={busy} onChange={(e) => { setTtlDays(Number(e.target.value)); setLink(null); }} style={fieldStyle}>
              <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
            </select>
          </div>
          {mode === "restricted" && (
            <input value={emails} placeholder="allowed emails (comma-separated)" disabled={busy}
              onChange={(e) => { setEmails(e.target.value); setLink(null); }} style={fieldStyle} />
          )}
          <select value={wsId} disabled={busy} onChange={(e) => { setWsId(e.target.value); setLink(null); }} style={fieldStyle} title="Optionally bundle a shared workspace into the link">
            <option value="">live feed only (no workspace)</option>
            {shares.map((s) => <option key={s.workspace_id} value={s.workspace_id}>+ workspace: {s.workspace_id}</option>)}
          </select>
          {err && <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)" }}>⚠ {err}</div>}
          {link ? (
            <div style={{ display: "flex", gap: 6 }}>
              <input readOnly value={link} onFocus={(e) => e.currentTarget.select()} style={{ ...fieldStyle, flex: 1, fontSize: 11 }} />
              <button onClick={() => void copyText(link)} style={{ fontSize: 12, padding: "4px 10px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 6, cursor: "pointer" }}>Copy</button>
            </div>
          ) : (
            <button disabled={busy} onClick={() => void create()} style={{ fontSize: 12, padding: "5px 10px", background: "var(--accent)", color: "var(--bg)", border: "none", borderRadius: 6, cursor: "pointer", opacity: busy ? 0.5 : 1 }}>{busy ? "Creating…" : "Create link"}</button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Connected docs — the meeting's knowledge-graph entity + the [[entities]] it links ─────────────
//  The meeting doc lives at a deterministic path: kg/entities/meeting/<native>.md. When present we show
//  its title + the [[wikilinks]] parsed from the body as chips that open that entity's doc. A wikilink
//  [[Title]] is resolved to a real doc by matching its slug against the workspace tree (so we open the
//  entity under its true type folder, whatever that is). 404 → a quiet "no notes yet" state.
// No client subject: workspace docs are read through the gateway, which injects X-User-Id → agent-api scopes (P20).
const docSlug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const baseName = (p: string) => p.split("/").pop() ?? p;
const docTabFor = (path: string, title: string): TabDescriptor =>
  ({ id: `doc:${path}`, title, kind: "doc", params: { path } });

type ConnectedDoc = { workspace: string; path: string; title?: string; kind?: string };

function ConnectedDocChip({ doc }: { doc: ConnectedDoc }) {
  const label = doc.title || baseName(doc.path).replace(/\.md$/, "");
  const nav = usePreviewPinTab<HTMLButtonElement>(docTabFor(doc.path, label));
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} title={`Open ${doc.path}`}
      style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer", maxWidth: 280 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--blue)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      {doc.kind && <span style={{ fontSize: 9.5, color: "var(--t3)", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: ".04em" }}>{doc.kind}</span>}
    </button>
  );
}

function MeetingDocChip({ native, title, hasLinks }: { native: string; title: string; hasLinks: boolean }) {
  const nav = usePreviewPinTab<HTMLButtonElement>(docTabFor(`kg/entities/meeting/${native}.md`, title));
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} title="Open this meeting's notes"
      style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px 5px 6px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer", maxWidth: 360, marginBottom: hasLinks ? 8 : 0 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ width: 18, height: 18, flex: "none", borderRadius: 5, background: "var(--accentbg)", color: "var(--accent)", display: "flex", alignItems: "center", justifyContent: "center" }}><Icon name="panel" size={11} /></span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
    </button>
  );
}

function WikiLinkChip({ title, path }: { title: string; path: string }) {
  const nav = usePreviewPinTab<HTMLButtonElement>(docTabFor(path, title));
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} title={`Open ${title}`}
      style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer", maxWidth: 280 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span style={{ width: 5, height: 5, borderRadius: "50%", background: "var(--blue)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
    </button>
  );
}

// ── Connected (data.docs) — the meeting-api now ships data.docs = the workspace docs this meeting
//  produced. When present we render them as chips grouped by kind, each opening that doc.path in a doc
//  tab. When EMPTY we fall back to the deterministic meeting-doc path below.
function ConnectedDocsPanel({ docs }: { docs: ConnectedDoc[] }) {
  // group by kind, preserving first-seen order
  const groups: { kind: string; docs: ConnectedDoc[] }[] = [];
  const byKind = new Map<string, ConnectedDoc[]>();
  for (const d of docs) {
    const k = d.kind || "doc";
    if (!byKind.has(k)) { byKind.set(k, []); groups.push({ kind: k, docs: byKind.get(k)! }); }
    byKind.get(k)!.push(d);
  }
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 2px 10px" }}>
        <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600 }}>Connected</span>
        <span style={{ fontSize: 10.5, color: "var(--t3)", fontFamily: "var(--mono)" }}>{docs.length}</span>
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      {groups.map((g) => (
        <div key={g.kind} style={{ marginBottom: 10 }}>
          {groups.length > 1 && <div style={{ fontSize: 10, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", margin: "0 2px 6px" }}>{g.kind}</div>}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>{g.docs.map((d, i) => <ConnectedDocChip key={`${d.path}-${i}`} doc={d} />)}</div>
        </div>
      ))}
    </div>
  );
}

function ConnectedPanel({ native, docs }: { native: string; docs?: ConnectedDoc[] }) {
  // data.docs first — when the meeting carries connected docs, render them and skip the path fallback
  const hasDocs = !!docs?.length;
  const [state, setState] = useState<{ status: "loading" | "absent" | "present"; title: string; links: string[] }>({ status: "loading", title: "", links: [] });
  // slug → real entity doc path, built from the workspace tree (so a [[Title]] resolves to its true type)
  const [slugMap, setSlugMap] = useState<Record<string, string>>({});

  useEffect(() => {
    let alive = true;
    const path = `kg/entities/meeting/${native}.md`;
    void (async () => {
      try {
        const r = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);
        if (!alive) return;
        if (!r.ok) { setState({ status: "absent", title: "", links: [] }); return; }
        const content: string = (await r.json()).content ?? "";
        const fmTitle = content.match(/^---\n([\s\S]*?)\n---/)?.[1]?.split("\n").find((l) => l.startsWith("title:"))?.slice(6).trim();
        const h1 = content.match(/^#\s+(.+)$/m)?.[1]?.trim();
        const title = (fmTitle || h1 || native).replace(/^["']|["']$/g, "");
        const links = [...new Set([...content.matchAll(/\[\[([^\]]+)\]\]/g)].map((m) => m[1].trim()).filter(Boolean))];
        setState({ status: "present", title, links });
      } catch { if (alive) setState({ status: "absent", title: "", links: [] }); }
    })();
    return () => { alive = false; };
  }, [native]);

  // load the tree once so wikilink slugs resolve to their real entity doc paths
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const files: string[] = (await (await fetch(`/api/workspace/tree`)).json()).files ?? [];
        if (!alive) return;
        const map: Record<string, string> = {};
        for (const f of files) if (f.startsWith("kg/entities/") && f.endsWith(".md")) map[baseName(f).replace(/\.md$/, "")] = f;
        setSlugMap(map);
      } catch { /* offline — keep wikilinks on the meeting doc */ }
    })();
    return () => { alive = false; };
  }, []);

  if (hasDocs) return <ConnectedDocsPanel docs={docs!} />;
  if (state.status === "loading") return null;
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 2px 10px" }}>
        <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600 }}>Connected</span>
        {state.status === "present" && state.links.length > 0 && <span style={{ fontSize: 10.5, color: "var(--t3)", fontFamily: "var(--mono)" }}>{state.links.length}</span>}
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      {state.status === "absent" && (
        <div style={{ fontSize: 12.5, color: "var(--t3)", padding: "2px 2px", lineHeight: 1.5 }}>No notes yet — they&apos;re written when the meeting ends (or a prep routine runs).</div>
      )}
      {state.status === "present" && (
        <>
          <MeetingDocChip native={native} title={state.title} hasLinks={state.links.length > 0} />
          {state.links.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {state.links.map((l) => {
                const slug = docSlug(l);
                const path = slugMap[slug] ?? `kg/entities/meeting/${native}.md`;
                return <WikiLinkChip key={l} title={l} path={path} />;
              })}
            </div>
          )}
          {state.links.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)", padding: "2px 2px" }}>Notes recorded — no linked entities yet.</div>}
        </>
      )}
    </div>
  );
}

// ── Per-meeting status badge + action dropdown ─────────────────────────────────────
//  The badge shows the REAL meeting-api status; the dropdown is an ACTION→TRANSITION map (not free
//  status editing) — each item calls ONE endpoint that performs the one legal write (design doc §B).
type BadgeKind = "intent" | "live" | "awaiting" | "needshelp" | "stopping" | "terminal";
const STATUS_BADGE: Record<string, { label: string; color: string; bg: string; kind: BadgeKind }> = {
  idle: { label: "Idle", color: "var(--t3)", bg: "var(--panel2)", kind: "intent" },
  scheduled: { label: "Scheduled", color: "var(--blue)", bg: "var(--bluebg)", kind: "intent" },
  requested: { label: "Requested", color: "var(--accent)", bg: "var(--accentbg)", kind: "live" },
  joining: { label: "Joining", color: "var(--accent)", bg: "var(--accentbg)", kind: "live" },
  awaiting_admission: { label: "Awaiting", color: "var(--violet)", bg: "var(--violetbg)", kind: "awaiting" },
  needs_help: { label: "Needs help", color: "var(--warn)", bg: "var(--warnbg)", kind: "needshelp" },
  active: { label: "Live", color: "var(--green)", bg: "var(--greenbg)", kind: "live" },
  stopping: { label: "Stopping", color: "var(--t3)", bg: "var(--panel2)", kind: "stopping" },
  completed: { label: "Completed", color: "var(--green)", bg: "var(--greenbg)", kind: "terminal" },
  failed: { label: "Failed", color: "var(--danger)", bg: "var(--dangerbg)", kind: "terminal" },
  stopped: { label: "Stopped", color: "var(--t3)", bg: "var(--panel2)", kind: "terminal" },
};
const badgeFor = (raw?: string) => STATUS_BADGE[raw ?? ""] ?? { label: raw ?? "—", color: "var(--t3)", bg: "var(--panel2)", kind: "terminal" as BadgeKind };

type MeetingActionFailure = { actionId: string; actionLabel: string; native: string; message: string };
type MeetingActionFailureHandler = (failure: MeetingActionFailure) => void;
type RowAction = { id: string; label: string; tone: "accent" | "live" | "muted"; run: (onFailure?: MeetingActionFailureHandler) => Promise<void> | void };

function failureMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error.trim()) return error.trim();
  return "Request failed";
}

async function readFailure(r: Response): Promise<string> {
  const detail = (await r.text().catch(() => "")).trim().replace(/\s+/g, " ");
  const status = `${r.status}${r.statusText ? ` ${r.statusText}` : ""}`;
  if (!detail) return status;
  return `${status}: ${detail.slice(0, 180)}`;
}

async function runMeetingAction(action: Omit<MeetingActionFailure, "message">, request: Promise<Response>, onFailure?: MeetingActionFailureHandler): Promise<void> {
  try {
    const r = await request;
    if (!r.ok) throw new Error(await readFailure(r));
  } catch (error) {
    const message = failureMessage(error);
    console.warn("meeting action failed", { ...action, message });
    onFailure?.({ ...action, message });
  } finally {
    refreshMeetings();
  }
}

/** The action→transition map for a row, keyed on its REAL status. Each action hits exactly one endpoint.
 *  Exported (additive — no runtime behavior change) so the behavioral test can assert each status offers
 *  the correct actions and each fires the correct endpoint+body. */
export function actionsFor(m: MeetingMock): RowAction[] {
  const native = m.native_id ?? m.id;
  // The model stores platform DISPLAY-cased ("Google Meet", else the raw API slug like "teams"/"zoom").
  // Stop targets DELETE /bots/{platform}/{native}, so normalise back to the slug — hardcoding google_meet
  // 404s ("No active meeting for this bot") for a live Teams/Zoom bot.
  const platformSlug = m.platform === "Google Meet" ? "google_meet" : m.platform.toLowerCase().replace(/\s+/g, "_");
  const intent = (state: "idle" | "scheduled", at?: string, onFailure?: MeetingActionFailureHandler) =>
    runMeetingAction({ actionId: state === "idle" ? "cancel" : "schedule", actionLabel: state === "idle" ? "Cancel" : "Schedule", native }, fetch(`/api/meetings/${platformSlug}/${encodeURIComponent(native)}/intent`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent: state, ...(at ? { at } : {}) }),
    }), onFailure);
  const send = (onFailure?: MeetingActionFailureHandler) =>
    runMeetingAction({ actionId: "send", actionLabel: "Send now", native }, fetch("/api/bots", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        platform: platformSlug, native_meeting_id: native,
        // the row's real link when it has one (zoom/teams NEED it); gmeet can be constructed
        ...(m.meeting_url ? { meeting_url: m.meeting_url }
          : platformSlug === "google_meet" ? { meeting_url: `https://meet.google.com/${native}` } : {}),
        bot_name: "Vexa",
      }),
    }), onFailure);
  // Delete a PLANNED row — ROW-id addressed (a link-less plan has no platform/native path).
  const del = (onFailure?: MeetingActionFailureHandler) =>
    runMeetingAction({ actionId: "delete", actionLabel: "Delete", native }, fetch(`/api/meetings/${encodeURIComponent(m.id)}`, { method: "DELETE" }), onFailure);
  // Cancel (clear the time) on a LINK-LESS planned row — PATCH by row id (no native path exists).
  const cancelById = (onFailure?: MeetingActionFailureHandler) =>
    runMeetingAction({ actionId: "cancel", actionLabel: "Cancel", native }, fetch(`/api/meetings/${encodeURIComponent(m.id)}`, {
      method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scheduled_at: null }),
    }), onFailure);
  // Stop = the gateway-backed user-stop route DELETE /bots/{platform}/{native} (meeting-api lifecycle/stop_router).
  const stop = (onFailure?: MeetingActionFailureHandler) =>
    runMeetingAction({ actionId: "stop", actionLabel: "Stop", native }, fetch(`/api/bots/${platformSlug}/${encodeURIComponent(native)}`, { method: "DELETE" }), onFailure);
  const schedule = (onFailure?: MeetingActionFailureHandler) => {
    // minimal time picker: prompt for a local datetime, send as ISO. (A richer picker can replace this.)
    const def = new Date(Date.now() + 3600_000).toISOString().slice(0, 16);
    const input = typeof window !== "undefined" ? window.prompt("Schedule for (YYYY-MM-DD HH:MM, local):", def) : null;
    if (!input) return;
    const at = new Date(input).toISOString();
    return intent("scheduled", at, onFailure);
  };

  const raw = m.live_status ?? (m.status === "live" ? "active" : "completed");
  const hasLink = !!m.native_id;
  switch (raw) {
    case "idle":
      return [
        ...(hasLink ? [
          { id: "schedule", label: "Schedule", tone: "accent", run: schedule } as RowAction,
          { id: "send", label: "Send now", tone: "accent", run: send } as RowAction,
        ] : []),
        { id: "delete", label: "Delete", tone: "muted", run: del },
      ];
    case "scheduled":
      return [
        ...(hasLink ? [{ id: "send", label: "Send now", tone: "accent", run: send } as RowAction] : []),
        { id: "cancel", label: "Cancel", tone: "muted", run: (onFailure?: MeetingActionFailureHandler) => hasLink ? intent("idle", undefined, onFailure) : cancelById(onFailure) },
        { id: "delete", label: "Delete", tone: "muted", run: del },
      ];
    case "requested": case "joining": case "awaiting_admission": case "needs_help": case "active": case "stopping":
      return [{ id: "stop", label: "Stop", tone: "live", run: stop }];
    case "completed": case "failed": case "stopped": default:
      return [{ id: "resend", label: "Re-send", tone: "accent", run: send }];
  }
}

function StatusBadge({ raw }: { raw?: string }) {
  const b = badgeFor(raw);
  const dot = b.kind === "live" || b.kind === "needshelp";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "1px 7px", borderRadius: 5, background: b.bg, color: b.color, fontSize: 10, fontWeight: 600, letterSpacing: ".02em", whiteSpace: "nowrap", flex: "none" }}>
      {dot && <span style={{ width: 5, height: 5, borderRadius: "50%", background: b.color }} />}{b.label}
    </span>
  );
}

/** Status badge (only when meaningful) + a small ▾ menu of action→transition items for one meeting row.
 *  The ▾ is revealed on row hover (or while its menu is open) to keep the list quiet at rest. */
function RowActions({ m, showBadge, reveal, onActionStart, onActionFailure }: { m: MeetingMock; showBadge: boolean; reveal: boolean; onActionStart?: () => void; onActionFailure?: MeetingActionFailureHandler }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const acts = actionsFor(m);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  return (
    <div ref={ref} style={{ position: "relative", flex: "none", display: "inline-flex", alignItems: "center", gap: 5 }} onClick={(e) => e.stopPropagation()} onDoubleClick={(e) => e.stopPropagation()}>
      {showBadge && <StatusBadge raw={m.live_status} />}
      {acts.length > 0 && (reveal || open) && (
        <button title="Actions" onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
          style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--t2)", borderRadius: 6, padding: "1px 5px", fontSize: 11, lineHeight: 1.4, cursor: "pointer" }}>▾</button>
      )}
      {open && (
        <div style={{ position: "absolute", top: "100%", right: 0, marginTop: 4, minWidth: 132, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 8, boxShadow: "0 6px 20px rgba(0,0,0,.28)", padding: 4, zIndex: 40 }}>
          {acts.map((a) => (
            <button key={a.id} onClick={(e) => { e.stopPropagation(); setOpen(false); onActionStart?.(); void a.run(onActionFailure); }}
              style={{ display: "block", width: "100%", textAlign: "left", background: "transparent", border: "none", color: a.tone === "live" ? "var(--danger)" : a.tone === "muted" ? "var(--t2)" : "var(--accent)", borderRadius: 6, padding: "6px 9px", fontSize: 12, fontWeight: 550, cursor: "pointer" }}
              onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const INTENT_STATUSES = new Set(["idle", "scheduled"]);

export function meetingTab(m: MeetingMock): TabDescriptor {
  // A PLANNED (intent-status) row opens its PREP tab — title/time/link editing, workspace bind,
  // share. Once the bot claims the row (requested→…), the same row opens the live meeting view.
  if (INTENT_STATUSES.has(m.live_status ?? "")) return prepTabDescriptor({ id: m.id, title: m.title_custom || m.title });
  return { id: `meeting:${m.id}`, title: m.title.split(" — ")[0], kind: "meeting", params: { meetingId: m.id } };
}

// Statuses worth a badge — `active` (in-room) is shown by the green dot alone, not a badge; the rest
// (stopped/completed/failed) live under the "Recorded" header already.
const BADGE_STATUSES = new Set(["idle", "scheduled", "requested", "joining", "awaiting_admission", "needs_help", "stopping"]);

function MeetingRow({ m }: { m: MeetingMock }) {
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const [hover, setHover] = useState(false);
  const [actionFailure, setActionFailure] = useState<MeetingActionFailure | null>(null);
  const native = m.native_id ?? m.id;
  const live = m.status === "live";
  const inRoom = m.live_status === "active";   // actually live = green dot + a quiet "live", no badge
  // A planned meeting's user-given title wins; else just the meeting code — the platform is implicit.
  const label = m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "");
  const showBadge = BADGE_STATUSES.has(m.live_status ?? "");
  const isIntent = INTENT_STATUSES.has(m.live_status ?? "");
  useEffect(() => {
    if (!actionFailure) return;
    const t = window.setTimeout(() => setActionFailure(null), 6000);
    return () => window.clearTimeout(t);
  }, [actionFailure]);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); }} style={{ padding: "7px 9px", borderRadius: 7, cursor: "pointer", marginBottom: 1 }}
      onMouseEnter={(e) => { setHover(true); e.currentTarget.style.background = "var(--panel2)"; }} onMouseLeave={(e) => { setHover(false); e.currentTarget.style.background = "transparent"; }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {inRoom && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)", flex: "none" }} />}
        <span style={{ fontSize: 13, color: live ? "var(--t1)" : "var(--t2)", fontWeight: live ? 600 : 400, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        {m.shared && <span title="Shared with you (you don't own this meeting)" style={{ flex: "none", fontSize: 9.5, color: "var(--t3)", border: "1px solid var(--line)", borderRadius: 5, padding: "0 5px" }}>shared</span>}
        {(m.native_id || isIntent) && !m.shared && <RowActions m={m} showBadge={showBadge} reveal={hover} onActionStart={() => setActionFailure(null)} onActionFailure={setActionFailure} />}
      </div>
      <div style={{ fontSize: 11, color: inRoom ? "var(--green)" : "var(--t3)", marginTop: 1, paddingLeft: inRoom ? 13 : 0 }}>{inRoom ? "live" : m.when}</div>
      {isIntent && m.auto_join_error && (
        <div role="alert" style={{ fontSize: 11, color: "var(--danger)", marginTop: 3, lineHeight: 1.35 }}>
          ⚠ Auto-join failed: {m.auto_join_error}
        </div>
      )}
      {actionFailure && (
        <div role="status" aria-live="polite" style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, lineHeight: 1.35 }}>
          {actionFailure.actionLabel} failed: {actionFailure.message}
        </div>
      )}
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[
          { id: "copy-reference", label: "Copy reference", detail: `@meeting:${native}`, onSelect: () => copyText(`@meeting:${native}`) },
        ]} />
      )}
    </div>
  );
}

// ── "+ Plan a meeting" — ONE CLICK: create the planned row (idle, untitled) and open its PREP
//    TAB, where the real form lives (title, modern date/time picker, link, workspace, share). No
//    inline sidebar form — the center tab is the editing surface. ─────────────────────────────────
function PlanMeetingButton() {
  const layout = useService(LayoutServiceId);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const plan = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      const row = await createPlannedMeeting({});          // idle, untitled — the prep tab edits it
      refreshMeetings();
      layout.openTab(prepTabDescriptor({ id: String(row.id), title: "New meeting" }));
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <>
      <button onClick={() => void plan()} disabled={busy}
        style={{ display: "block", width: "100%", textAlign: "left", background: "transparent", border: "1px dashed var(--line2)", color: "var(--t2)", borderRadius: 7, padding: "6px 9px", fontSize: 12, cursor: "pointer", marginBottom: 2, opacity: busy ? 0.6 : 1 }}
        onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
        {busy ? "Planning…" : "+ Plan a meeting"}
      </button>
      {err && <div role="alert" style={{ fontSize: 11, color: "var(--danger)", marginTop: 4, lineHeight: 1.4 }}>⚠ {err}</div>}
    </>
  );
}

/** The last sync attempt, humanized: "Imported 3 · updated 1 (2 min ago)" or the actual error. */
function CalendarSyncStatusLine({ stamp }: { stamp: CalendarSyncStamp | null }) {
  if (!stamp?.last_sync) return null;
  const ago = (() => {
    const s = Math.max(0, (Date.now() - new Date(stamp.last_sync).getTime()) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return `${Math.round(s / 3600)} h ago`;
  })();
  if (stamp.last_error) {
    return <div role="alert" style={{ fontSize: 11.5, color: "var(--danger)", lineHeight: 1.5 }}>⚠ Last sync failed ({ago}): {stamp.last_error}</div>;
  }
  const c = stamp.counts ?? {};
  const bits = [c.created ? `imported ${c.created}` : "", c.updated ? `updated ${c.updated}` : "", c.cancelled ? `removed ${c.cancelled}` : ""].filter(Boolean);
  return (
    <div style={{ fontSize: 11.5, color: "var(--green)", lineHeight: 1.5 }}>
      ✓ Synced {ago}{bits.length ? ` — ${bits.join(", ")}` : " — no meetings with joinable links found"}
    </div>
  );
}

// ── Calendar sync — the secret ICS URL + the GLOBAL auto-join default for imported meetings.
//    The URL is a secret: reads come back MASKED (host + tail). Synced meetings land under Upcoming.
//    Two skins over ONE popover: `icon` (the quiet header icon, always there) and `row` (a
//    discoverable "Connect your calendar" row that hides itself once a feed is connected). ──
function CalendarSyncButton({ variant = "icon" }: { variant?: "icon" | "row" }) {
  const [open, setOpen] = useState(false);
  const [cfg, setCfg] = useState<CalendarConfig | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [stamp, setStamp] = useState<CalendarSyncStamp | null>(null);
  const [syncing, setSyncing] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const syncNow = async () => {
    setSyncing(true); setErr(null);
    try { setStamp(await syncCalendarNow()); refreshMeetings(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setSyncing(false); }
  };
  // the row skin needs the connected-state up front (it hides once connected)
  useEffect(() => {
    if (variant !== "row") return;
    void getCalendarConfig().then(setCfg).catch(() => setCfg(null));
  }, [variant]);
  useEffect(() => {
    if (!open) return;
    void getCalendarConfig().then(setCfg).catch(() => setCfg(null));
    void getCalendarSyncStatus().then(setStamp).catch(() => {});
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);
  const save = async (body: { ics_url?: string | null; auto_join?: boolean }) => {
    setBusy(true); setErr(null);
    try {
      setCfg(await setCalendarConfig(body));
      setUrl(""); refreshMeetings();
      if (body.ics_url) await syncNow();              // paste → an ANSWER, not a silent wait
      if (body.ics_url === null) setStamp(null);
    }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  if (variant === "row" && cfg?.ics_url_set) return null;   // connected → manage via the header icon
  return (
    <div ref={ref} style={{ position: "relative", flex: variant === "row" ? "initial" : "none" }}>
      {variant === "row" ? (
        <button onClick={() => setOpen((v) => !v)}
          style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", textAlign: "left", background: "transparent", border: "1px dashed var(--line2)", color: "var(--t2)", borderRadius: 7, padding: "6px 9px", fontSize: 12, cursor: "pointer", marginTop: 6 }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <Icon name="cal" size={12} /> Connect your calendar
        </button>
      ) : (
        <button onClick={() => setOpen((v) => !v)} title="Calendar sync — import upcoming meetings from your calendar"
          style={{ display: "inline-flex", alignItems: "center", background: "transparent", border: "none", color: cfg?.ics_url_set ? "var(--accent)" : "var(--t3)", cursor: "pointer", padding: 2 }}>
          <Icon name="cal" size={13} />
        </button>
      )}
      {open && (
        <div style={{ position: "absolute", top: "100%", right: 0, marginTop: 6, width: 280, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 10, boxShadow: "0 8px 28px rgba(0,0,0,.32)", padding: 12, zIndex: 50, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em" }}>Calendar sync</div>
          {cfg?.ics_url_set ? (
            <>
              <div style={{ fontSize: 12, color: "var(--t2)", lineHeight: 1.5 }}>
                Connected: <span style={{ fontFamily: "var(--mono)", fontSize: 11 }}>{cfg.ics_url_masked}</span>
              </div>
              <label style={{ display: "inline-flex", alignItems: "center", gap: 7, fontSize: 12, color: "var(--t2)", cursor: "pointer", userSelect: "none" }}>
                <input type="checkbox" checked={cfg.auto_join} disabled={busy}
                  onChange={(e) => void save({ auto_join: e.target.checked })} />
                Auto-join imported meetings
              </label>
              <CalendarSyncStatusLine stamp={stamp} />
              <div style={{ display: "flex", gap: 6 }}>
                <button disabled={busy || syncing} onClick={() => void syncNow()}
                  style={{ flex: 1, fontSize: 12, padding: "4px 10px", background: "var(--panel2)", border: "1px solid var(--line)", color: "var(--t1)", borderRadius: 6, cursor: "pointer" }}>
                  {syncing ? "Syncing…" : "Sync now"}
                </button>
                <button disabled={busy || syncing} onClick={() => void save({ ics_url: null })}
                  style={{ fontSize: 12, padding: "4px 10px", background: "transparent", border: "1px solid var(--line2)", color: "var(--danger)", borderRadius: 6, cursor: "pointer" }}>
                  Disconnect
                </button>
              </div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 11.5, color: "var(--t3)", lineHeight: 1.5 }}>
                Paste your calendar&apos;s <b>secret ICS address</b> (Google Calendar → Settings → &quot;Secret address in iCal format&quot;).
                Upcoming meetings with a Meet/Zoom/Teams link appear under Upcoming and auto-join at start.
              </div>
              <input value={url} placeholder="https://calendar.google.com/…/basic.ics" disabled={busy}
                onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) void save({ ics_url: url.trim() }); }}
                style={{ fontSize: 11.5, padding: "5px 7px", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)", outline: "none" }} />
              <button disabled={busy || !url.trim()} onClick={() => void save({ ics_url: url.trim() })}
                style={{ fontSize: 12, padding: "5px 10px", background: url.trim() ? "var(--accent)" : "var(--panel2)", color: url.trim() ? "var(--bg)" : "var(--t3)", border: "none", borderRadius: 6, cursor: url.trim() ? "pointer" : "default" }}>
                {busy || syncing ? "Connecting…" : "Connect"}
              </button>
              <div style={{ fontSize: 10.5, color: "var(--t3)", lineHeight: 1.45 }}>
                Tip: the <i>public</i> address only carries events you made public — use the <b>secret</b> one for your full calendar.
              </div>
            </>
          )}
          {err && <div role="alert" style={{ fontSize: 11, color: "var(--danger)" }}>⚠ {err}</div>}
        </div>
      )}
    </div>
  );
}

// ── Meetings LIST (left) ─────────────────────────────────────────────────────────
function MeetingsList() {
  const layout = useService(LayoutServiceId);
  const all = useLiveMeetings();                                   // real meetings (live + past) from agent-api
  // Three buckets: upcoming intent (idle/scheduled), live (bot in/heading to the room), and recorded/past.
  const INTENT = new Set(["idle", "scheduled"]);
  const upcomingOnes = all.filter((m) => INTENT.has(m.live_status ?? ""));
  const liveOnes = all.filter((m) => m.status === "live" && !INTENT.has(m.live_status ?? ""));
  const pastOnes = all.filter((m) => m.status !== "live" && !INTENT.has(m.live_status ?? ""));
  const autoOpened = useRef(false);
  useEffect(() => {                                                // a live meeting opens itself, once
    const firstLive = all.find((m) => m.status === "live");
    if (!autoOpened.current && firstLive) {
      autoOpened.current = true;
      layout.openTab(meetingTab(firstLive));
    }
  }, [all, layout]);
  // 'add bot from URL': send OUR bot into a meeting; the watcher attaches the copilot once it transcribes
  const [url, setUrl] = useState("");
  const [sent, setSent] = useState<null | "sending" | "ok" | "err">(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const addBot = async () => {
    const u = url.trim();
    if (!u || sent === "sending") return;
    // Parse + validate the pasted link/id against the platform formats (mirrors join-form).
    const parsed = parseMeetingInput(u);
    if (!parsed) { setSent("err"); setErrMsg("That doesn't look like a Meet / Zoom / Teams link."); setTimeout(() => setSent(null), 5000); return; }
    setSent("sending"); setErrMsg(null);
    try {
      // POST /bots through the authed gateway proxy (X-API-Key injected server-side from the cookie token).
      const r = await fetch("/api/bots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: parsed.platform, native_meeting_id: parsed.native_meeting_id, meeting_url: u, bot_name: "Vexa" }),
      });
      if (r.ok) {
        setSent("ok"); setUrl("");
        // The list has no background poll, so force a re-fetch now and again as the bot
        // transitions requested → joining → active (else the meeting only shows on reload).
        refreshMeetings(); setTimeout(refreshMeetings, 2000); setTimeout(refreshMeetings, 6000);
      } else {
        // Surface the REAL reason, not a generic "bad link" (the cap/dup/auth cases are common).
        const detail = (await r.text().catch(() => "")).replace(/\s+/g, " ").slice(0, 160);
        setSent("err");
        setErrMsg(
          r.status === 429 ? "You're at your meeting limit — stop one first."
            : r.status === 409 ? "That meeting already has a bot."
              : r.status === 401 ? "Not signed in — sign in and retry."
                : `Couldn't send (${r.status})${detail ? `: ${detail}` : ""}`,
        );
      }
    } catch { setSent("err"); setErrMsg("Couldn't reach the server."); }
    setTimeout(() => setSent(null), 5000);
  };
  return (
    <div style={{ padding: "8px" }}>
      <div style={{ display: "flex", alignItems: "center", padding: "6px 4px 6px" }}>
        <span style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", flex: 1 }}>meetings</span>
        <CalendarSyncButton />
      </div>
      <div style={{ padding: "0 4px 10px" }}>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addBot(); }}
            placeholder="Paste a Google Meet link…" style={{ flex: 1, minWidth: 0, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 7, padding: "6px 8px", color: "var(--t1)", fontSize: 12, outline: "none" }} />
          <button onClick={() => void addBot()} disabled={!url.trim() || sent === "sending"} title="Send the Vexa bot to this meeting"
            style={{ flex: "none", background: url.trim() ? "var(--accent)" : "var(--panel2)", color: url.trim() ? "var(--on-accent)" : "var(--t3)", border: "none", borderRadius: 7, padding: "0 10px", fontSize: 12, fontWeight: 600, cursor: url.trim() ? "pointer" : "default" }}>
            {sent === "sending" ? "…" : "Add bot"}
          </button>
        </div>
        {sent === "ok" && <div style={{ fontSize: 11, color: "var(--green)", marginTop: 5, lineHeight: 1.4 }}>Bot sent — admit it in the meeting; it appears here once it starts transcribing.</div>}
        {sent === "err" && <div style={{ fontSize: 11, color: "var(--danger)", marginTop: 5, lineHeight: 1.4 }}>{errMsg ?? "Couldn't send."}</div>}
        <div style={{ marginTop: 8 }}>
          <PlanMeetingButton />
          <CalendarSyncButton variant="row" />
        </div>
      </div>
      {all.length === 0 && <div style={{ padding: "8px 9px", fontSize: 12, color: "var(--t3)", lineHeight: 1.5 }}>No meetings yet — paste a Meet link above and I&apos;ll send the bot.</div>}
      {upcomingOnes.length > 0 && <div style={{ fontSize: 10, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", padding: "12px 9px 4px" }}>Upcoming</div>}
      {upcomingOnes.map((m) => <MeetingRow key={m.id} m={m} />)}
      {liveOnes.map((m) => <MeetingRow key={m.id} m={m} />)}
      {pastOnes.length > 0 && <div style={{ fontSize: 10, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", padding: "12px 9px 4px" }}>Recorded</div>}
      {pastOnes.map((m) => <MeetingRow key={m.id} m={m} />)}
    </div>
  );
}

// ── Meeting COPILOT tab (center) — meeting shell + canvas ──────────────────────────
type ModelInfo = { chat_model?: string; streaming_model?: string; agent_model?: string; meeting_model?: string };

function useModelInfo(): ModelInfo | null {
  const [models, setModels] = useState<ModelInfo | null>(null);
  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const r = await fetch(`/api/models`, { cache: "no-store" });
        if (!alive || !r.ok) return;
        setModels(await r.json() as ModelInfo);
      } catch {
        /* model labels are informational */
      }
    })();
    return () => { alive = false; };
  }, []);
  return models;
}

function ModelChips() {
  const models = useModelInfo();
  const streaming = models?.streaming_model || models?.meeting_model || "streaming";
  const chat = models?.chat_model || models?.agent_model || "chat";
  const chip = (label: string, value: string) => (
    <span title={`${label} model: ${value}`} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 8px", border: "1px solid var(--line)", borderRadius: 7, color: "var(--t2)", background: "var(--panel)", fontSize: 11.5, whiteSpace: "nowrap", minWidth: 0 }}>
      <span style={{ color: "var(--t3)", fontFamily: "var(--mono)", flex: "none" }}>{label}</span>
      <span style={{ color: "var(--t1)", maxWidth: 220, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0 }}>{value}</span>
    </span>
  );
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center", justifyContent: "flex-end", minWidth: 0 }}>
      {chip("stream", streaming)}
      {chip("chat", chat)}
    </div>
  );
}

function MeetingTab({ params }: TabProps) {
  const liveList = useLiveMeetings();
  const requestedMeetingId = params.meetingId as string;
  // ONE resolver, shared with the canvas body (useMeeting.resolveMeeting): the real meetings list is the
  // only source of truth — a mock never shadows a real id, and a real id never falls back to a mock. While
  // the async list is still loading the row is simply not-yet-resolved; we render the canvas bound to the
  // id with a neutral header (never a wrong/mock meeting), so the header can't disagree with the body.
  const m = liveList.find((x) => x.id === requestedMeetingId || x.native_id === requestedMeetingId);
  const live = m?.status === "live";

  return (
    <div style={{ width: "100%", height: "100%", minHeight: 0, display: "flex", flexDirection: "column", padding: "16px 0 24px", boxSizing: "border-box" }}>
      <header style={{ flex: "none", marginBottom: 16, padding: `0 ${MEETING_CANVAS_CONTENT_INSET}px`, boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 13, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
            {live
              ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--green)", fontWeight: 600, letterSpacing: ".04em", fontSize: 11, textTransform: "uppercase", flex: "none" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--green)", boxShadow: "0 0 0 3px var(--greenbg)" }} />Live</span>
              : <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase", flex: "none" }}>{m ? "Ended" : "Connecting…"}</span>}
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--t3)", flex: "none" }} />
            <span style={{ color: "var(--t1)", fontWeight: 550, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m?.platform ?? "Meeting"}</span>
            {m && <span style={{ color: "var(--t3)", flex: "none" }}>{m.participants.length} in the room</span>}
          </div>
          <div style={{ flex: 1 }} />
          {m?.native_id && <ShareSessionButton platform={platformSlug(m.platform)} native={m.native_id} />}
          <ModelChips />
        </div>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        <MeetingCanvasView key={requestedMeetingId} meetingId={requestedMeetingId} />
      </div>
    </div>
  );
}

registerList({ id: "meetings", label: "Meetings", icon: "cal", order: 20, component: MeetingsList });
registerTab("meeting", MeetingTab);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeetingsNow()[0]; if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
