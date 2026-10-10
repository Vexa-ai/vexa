"use client";
/** Meetings (mocked backend) — the differentiator flow.
 *  • "meetings" LIST (left): meetings; the live one auto-opens; click any to (re)open its meeting view.
 *  • "meeting" TAB (center): fixed meeting chrome around the Meeting Canvas body.
 *    The generated canvas view consumes this meeting's live MeetingState. */
import { minutesOnly } from "../app/mode";
import { useCallback, useEffect, useRef, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { registerList, registerTab, registerCommand, type TabProps } from "../contributions";
import { Badge, Icon, SectionLabel, Spinner, Tag } from "../ui-kit";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { MEETING_CANVAS_CONTENT_INSET, MeetingCanvasView } from "../canvas/MeetingCanvasView";
import { type MeetingMock } from "./meetingModel";
import { ApiError, presentError, readApiFailure } from "./apiClient";
import { resolveJoinError, serviceDenialFromError, type ServiceDenialPresentation } from "./serviceDenial";
import { ServiceDenialPanel } from "./ServiceDenialPanel";
import { useLiveMeetings, useLiveMeetingsConnection, useLiveMeetingsLoaded, liveMeetingsNow, refreshMeetings } from "./liveMeetings";
import { usePreviewPinTab } from "./previewPinTab";
import { defaultBotName } from "./defaultBotName";
import { parseMeetingInput } from "./meetingId";
import { getJitsiHosts } from "./jitsiHosts";
import { mintTranscriptShare, mintInvite, listSharedMemberships, type Membership } from "./workspaceApi";
import { deletePlannedMeeting, MAX_CALENDARS, listCalendars, createCalendar, updateCalendar, syncCalendar, getCalendarSyncStatus, type CalendarConnection, type CalendarSyncStamp } from "./plannedApi";
import { prepTabDescriptor, prepDraftTabDescriptor } from "./meetingPrep";

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
    } catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };
  const fieldStyle = { fontSize: 12, padding: "4px 6px", background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--t1)" } as const;
  return (
    <div ref={ref} style={{ position: "relative", flex: "none" }}>
      <button onClick={() => { setOpen((v) => !v); setLink(null); }} title="Share this meeting's live feed (and optionally a workspace)"
        className="bg-none bd-strong c-2 r-md pt-0_5 pr-2 pb-0_5 pl-2 t-xs" style={{ display: "inline-flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
        <Icon name="upload" size={12} /> Share session
      </button>
      {open && (
        <div className="mt-1_5 bg-2 bd-strong r-lg p-3" style={{ position: "absolute", top: "100%", right: 0, width: 280, boxShadow: "0 8px 28px rgba(0,0,0,.32)", zIndex: 50, display: "flex", flexDirection: "column", gap: 8 }}>
          <SectionLabel as="div">Share session</SectionLabel>
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
          {err && <div role="alert" className="t-xs c-danger">⚠ {err}</div>}
          {link ? (
            <div style={{ display: "flex", gap: 6 }}>
              <input readOnly value={link} onFocus={(e) => e.currentTarget.select()} className="t-xs" style={{ ...fieldStyle, flex: 1 }} />
              <button onClick={() => void copyText(link)} className="t-xs pt-1 pr-2 pb-1 pl-2 bg-accent bd-none r-md" style={{ color: "var(--bg)", cursor: "pointer" }}>Copy</button>
            </div>
          ) : (
            <button disabled={busy} onClick={() => void create()} className="t-xs pt-1 pr-2 pb-1 pl-2 bg-accent bd-none r-md" style={{ color: "var(--bg)", cursor: "pointer", opacity: busy ? 0.5 : 1 }}>{busy ? "Creating…" : "Create link"}</button>
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
      className="pt-1 pr-2 pb-1 pl-2 r-md bg-2 bd c-1 t-xs" style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer", maxWidth: 280 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span className="r-full" style={{ width: 5, height: 5, background: "var(--blue)", flex: "none" }} />
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
      {doc.kind && <Tag>{doc.kind[0].toUpperCase() + doc.kind.slice(1)}</Tag>}
    </button>
  );
}

function MeetingDocChip({ native, title, hasLinks }: { native: string; title: string; hasLinks: boolean }) {
  const nav = usePreviewPinTab<HTMLButtonElement>(docTabFor(`kg/entities/meeting/${native}.md`, title));
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} title="Open this meeting's notes"
      className="pt-1 pr-2 pb-1 pl-1_5 r-md bg-2 bd c-1 t-xs" style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer", maxWidth: 360, marginBottom: hasLinks ? 8 : 0 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span className="r-md bg-accent-tint c-accent" style={{ width: 18, height: 18, flex: "none", display: "flex", alignItems: "center", justifyContent: "center" }}><Icon name="panel" size={11} /></span>
      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{title}</span>
    </button>
  );
}

function WikiLinkChip({ title, path }: { title: string; path: string }) {
  const nav = usePreviewPinTab<HTMLButtonElement>(docTabFor(path, title));
  return (
    <button onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} title={`Open ${title}`}
      className="pt-1 pr-2 pb-1 pl-2 r-md bg-2 bd c-1 t-xs" style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer", maxWidth: 280 }}
      onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
      onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
      <span className="r-full" style={{ width: 5, height: 5, background: "var(--blue)", flex: "none" }} />
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
    <div className="mt-5">
      <div className="mt-0 mr-0_5 mb-2 ml-0_5" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <SectionLabel>Connected</SectionLabel>
        <span className="t-xs c-3 f-mono">{docs.length}</span>
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      {groups.map((g) => (
        <div key={g.kind} className="mb-2">
          {groups.length > 1 && <SectionLabel as="div">{g.kind[0].toUpperCase() + g.kind.slice(1)}</SectionLabel>}
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
    <div className="mt-5">
      <div className="mt-0 mr-0_5 mb-2 ml-0_5" style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <SectionLabel>Connected</SectionLabel>
        {state.status === "present" && state.links.length > 0 && <span className="t-xs c-3 f-mono">{state.links.length}</span>}
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
      </div>
      {state.status === "absent" && (
        <div className="t-xs c-3 pt-0_5 pr-0_5 pb-0_5 pl-0_5 lh-snug">No notes yet — they&apos;re written when the meeting ends (or a prep routine runs).</div>
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
          {state.links.length === 0 && <div className="t-xs c-3 pt-0_5 pr-0_5 pb-0_5 pl-0_5">Notes recorded — no linked entities yet.</div>}
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

type MeetingActionFailure = {
  actionId: string; actionLabel: string; native: string; message: string;
  /** Set when the deciding service REFUSED (403 service_not_allowed / 503 authority-unavailable).
   *  The row renders the panel instead of the one-line message, and does not auto-clear it. */
  denial?: ServiceDenialPresentation | null;
};
type MeetingActionFailureHandler = (failure: MeetingActionFailure) => void;
type RowAction = { id: string; label: string; tone: "accent" | "live" | "muted"; run: (onFailure?: MeetingActionFailureHandler) => Promise<void> | void };

/** A non-ok action response as a STRUCTURED failure (status + backend detail + the intact body),
 *  never a raw string. Shared with `getJson`'s error path so both edges produce the same object. */
const readFailure = (r: Response): Promise<ApiError> => readApiFailure(r);

/** User-truth message for a failed bot/row action (issue #674): a `404` means the backend no
 *  longer has this meeting — the list re-snapshot (runMeetingAction's `finally`) reconciles the
 *  control, so say that; a `409` is the duplicate/already case. Everything else goes through the
 *  presenter seam. Exported (additive) so the action test pins the vocabulary. */
export function presentMeetingActionFailure(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 404) return "This meeting is no longer active — refreshing the list.";
    if (error.status === 409) return "That meeting already has a bot.";
  }
  // A refusal from the deciding service says WHY in its own words — "Your key doesn't have access
  // to this." is a lie about a bot that was refused for some other reason entirely.
  const denial = serviceDenialFromError(error);
  if (denial) return denial.headline;
  return presentError(error).headline;
}

async function runMeetingAction(action: Omit<MeetingActionFailure, "message">, request: Promise<Response>, onFailure?: MeetingActionFailureHandler): Promise<void> {
  try {
    const r = await request;
    if (!r.ok) throw await readFailure(r);
  } catch (error) {
    // Operator channel keeps the full plumbing (P18); the UI channel gets the presented truth.
    console.warn("meeting action failed", { ...action, message: String(error instanceof Error ? error.message : error) });
    onFailure?.({ ...action, message: presentMeetingActionFailure(error), denial: serviceDenialFromError(error) });
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
        bot_name: defaultBotName(),
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
    <span className="pt-0 pr-1_5 pb-0 pl-1_5 r-md t-xs fw-600" style={{ display: "inline-flex", alignItems: "center", gap: 5, background: b.bg, color: b.color, letterSpacing: ".02em", whiteSpace: "nowrap", flex: "none" }}>
      {dot && <span className="r-full" style={{ width: 5, height: 5, background: b.color }} />}{b.label}
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
          className="bg-none bd-strong c-2 r-md pt-0 pr-1 pb-0 pl-1 t-xs lh-snug" style={{ cursor: "pointer" }}>▾</button>
      )}
      {open && (
        <div className="mt-1 bg-2 bd-strong r-md p-1" style={{ position: "absolute", top: "100%", right: 0, minWidth: 132, boxShadow: "0 6px 20px rgba(0,0,0,.28)", zIndex: 40 }}>
          {acts.map((a) => (
            <button key={a.id} onClick={(e) => { e.stopPropagation(); setOpen(false); onActionStart?.(); void a.run(onActionFailure); }}
              className="bg-none bd-none r-md pt-1_5 pr-2 pb-1_5 pl-2 t-xs fw-500" style={{ display: "block", width: "100%", textAlign: "left", color: a.tone === "live" ? "var(--danger)" : a.tone === "muted" ? "var(--t2)" : "var(--accent)", cursor: "pointer" }}
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
    // A service denial is a thing the user must ACT on (add funds, finish setup, raise the cap) —
    // it must not evaporate on a 6s timer the way a transient "already has a bot" should.
    if (actionFailure.denial) return;
    const t = window.setTimeout(() => setActionFailure(null), 6000);
    return () => window.clearTimeout(t);
  }, [actionFailure]);
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); }} className="pt-1_5 pr-2 pb-1_5 pl-2 r-md mb-0" style={{ cursor: "pointer" }}
      onMouseEnter={(e) => { setHover(true); e.currentTarget.style.background = "var(--panel2)"; }} onMouseLeave={(e) => { setHover(false); e.currentTarget.style.background = "transparent"; }}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {inRoom && <span className="r-full bg-success" style={{ width: 6, height: 6, flex: "none" }} />}
        <span className="t-sm" style={{ color: live ? "var(--t1)" : "var(--t2)", fontWeight: live ? 600 : 400, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{label}</span>
        {m.shared && <span title="Shared with you (you don't own this meeting)" className="t-xs c-3 bd r-md pt-0 pr-1 pb-0 pl-1" style={{ flex: "none" }}>shared</span>}
        {(m.native_id || isIntent) && !m.shared && <RowActions m={m} showBadge={showBadge} reveal={hover} onActionStart={() => setActionFailure(null)} onActionFailure={setActionFailure} />}
      </div>
      <div className="t-xs mt-0" style={{ color: inRoom ? "var(--green)" : "var(--t3)", paddingLeft: inRoom ? 13 : 0 }}>{inRoom ? "live" : m.when}</div>
      {isIntent && m.auto_join_error && (
        <div role="alert" className="t-xs c-danger mt-0_5 lh-tight">
          ⚠ Auto-join failed: {m.auto_join_error}
        </div>
      )}
      {actionFailure?.denial ? (
        <div onClick={(e) => e.stopPropagation()}>
          <ServiceDenialPanel presentation={actionFailure.denial} />
        </div>
      ) : actionFailure && (
        <div role="status" aria-live="polite" className="t-xs c-danger mt-1 lh-tight">
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

// ── "+ Plan a meeting" — opens a DRAFT prep tab. No backend row is created here; the prep tab
//    creates the row lazily on the first real input (title/link/date, brief chat, …), so abandoning
//    the tab never leaves an empty "Untitled meeting" behind. ───────────────────────────────────────
function PlanMeetingButton() {
  const layout = useService(LayoutServiceId);
  return (
    <button onClick={() => layout.openTab(prepDraftTabDescriptor())}
      className="bg-none c-2 r-md pt-1_5 pr-2 pb-1_5 pl-2 t-xs mb-0_5" style={{ display: "block", width: "100%", textAlign: "left", border: "1px dashed var(--line2)", cursor: "pointer" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      + Plan a meeting
    </button>
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
    return <div role="alert" className="t-xs c-danger lh-snug">⚠ Last sync failed ({ago}): {stamp.last_error}</div>;
  }
  const c = stamp.counts ?? {};
  const bits = [c.created ? `imported ${c.created}` : "", c.updated ? `updated ${c.updated}` : "", c.cancelled ? `removed ${c.cancelled}` : ""].filter(Boolean);
  return (
    <div className="t-xs c-success lh-snug">
      ✓ Synced {ago}{bits.length ? ` — ${bits.join(", ")}` : " — no meetings with joinable links found"}
    </div>
  );
}

// ── Calendar sync — the QUICK view over the user's calendar CONNECTIONS (#1150 plural API).
//    Per-calendar auto-join + Sync now live here because they are the two things you reach for
//    mid-day; adding, renaming, replacing a feed and disconnecting live in Settings → Calendar
//    (one manager, not two — the design-spec's "same list twice" anti-pattern). The feed address
//    is write-only: it is typed once here for the FIRST calendar and never rendered back.
//    Two skins over ONE popover: `icon` (the quiet header icon, always there) and `row` (a
//    discoverable "Connect your calendar" row that hides itself once a calendar exists). ──
function CalendarSyncButton({ variant = "icon" }: { variant?: "icon" | "row" }) {
  const layout = useService(LayoutServiceId);
  const [open, setOpen] = useState(false);
  const [cals, setCals] = useState<CalendarConnection[] | null>(null);
  const [stamps, setStamps] = useState<Record<string, CalendarSyncStamp>>({});
  const [url, setUrl] = useState("");
  const [name, setName] = useState("My calendar");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const load = useCallback(async (withStamps: boolean) => {
    try {
      const list = await listCalendars();
      setCals(list);
      if (!withStamps) return;
      const pairs = await Promise.all(list.map(async (c) => {
        try { return [c.id, await getCalendarSyncStatus(c.id)] as const; }
        catch { return [c.id, {} as CalendarSyncStamp] as const; }
      }));
      setStamps(Object.fromEntries(pairs));
    } catch { setCals(null); }
  }, []);

  const syncOne = async (id: string) => {
    setSyncing(id); setErr(null);
    try { const st = await syncCalendar(id); setStamps((s) => ({ ...s, [id]: st })); refreshMeetings(); }
    catch (e) { setErr(presentError(e).headline); }
    finally { setSyncing(null); }
  };

  // the row skin needs the connected-state up front (it hides once a calendar exists)
  useEffect(() => {
    if (variant !== "row") return;
    void load(false);
  }, [variant, load]);
  useEffect(() => {
    if (!open) return;
    void load(true);
    const close = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open, load]);

  const setAutoJoin = async (cal: CalendarConnection, autoJoin: boolean) => {
    setBusy(true); setErr(null);
    try {
      await updateCalendar(cal.id, { auto_join: autoJoin });
      await load(false);
      await syncOne(cal.id);   // PATCH does not reconcile already-imported meetings; the sync does
    }
    catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const connectFirst = async () => {
    const u = url.trim();
    if (!u) return;
    setBusy(true); setErr(null);
    try {
      const created = await createCalendar({ name: name.trim() || "My calendar", ics_url: u, auto_join: true });
      setUrl(""); refreshMeetings();
      await load(false);
      await syncOne(created.id);            // paste → an ANSWER, not a silent wait
    }
    catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const connected = (cals?.length ?? 0) > 0;
  const openSettings = () => { setOpen(false); layout.openTab({ id: "settings", title: "Settings", kind: "settings", params: {} }); };

  if (variant === "row" && connected) return null;   // has one → manage via the header icon / Settings
  return (
    <div ref={ref} style={{ position: "relative", flex: variant === "row" ? "initial" : "none" }}>
      {variant === "row" ? (
        <button onClick={() => setOpen((v) => !v)}
          className="bg-none c-2 r-md pt-1_5 pr-2 pb-1_5 pl-2 t-xs mt-1_5" style={{ display: "flex", alignItems: "center", gap: 6, width: "100%", textAlign: "left", border: "1px dashed var(--line2)", cursor: "pointer" }}
          onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
          <Icon name="cal" size={12} /> Connect your calendar
        </button>
      ) : (
        <button onClick={() => setOpen((v) => !v)} title="Calendars — import upcoming meetings from your calendars"
          className="bg-none bd-none p-0_5" style={{ display: "inline-flex", alignItems: "center", color: connected ? "var(--accent)" : "var(--t3)", cursor: "pointer" }}>
          <Icon name="cal" size={13} />
        </button>
      )}
      {open && (
        <div className="mt-1_5 bg-2 bd-strong r-lg p-3" style={{ position: "absolute", top: "100%", right: 0, width: 300, boxShadow: "0 8px 28px rgba(0,0,0,.32)", zIndex: 50, display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="vx-label">
            {connected ? `calendars · ${cals?.length ?? 0} of ${MAX_CALENDARS}` : "calendar sync"}
          </div>
          {connected ? (
            <>
              {cals?.map((c) => (
                <div key={c.id} className="pb-1_5" style={{ display: "flex", flexDirection: "column", gap: 5, borderBottom: "1px dashed var(--line)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <span className="t-xs fw-600" style={{ flex: 1, color: c.enabled ? "var(--t1)" : "var(--t3)" }}>{c.name}</span>
                    {!c.enabled && <span className="t-xs c-3">paused</span>}
                    <button disabled={busy || syncing !== null} onClick={() => void syncOne(c.id)}
                      className="t-xs pt-0_5 pr-2 pb-0_5 pl-2 bg-3 bd c-1 r-md" style={{ cursor: "pointer" }}>
                      {syncing === c.id ? "Syncing…" : "Sync"}
                    </button>
                  </div>
                  <label className="t-xs c-2" style={{ display: "inline-flex", alignItems: "center", gap: 7, cursor: "pointer", userSelect: "none" }}>
                    <input type="checkbox" checked={c.auto_join} disabled={busy || syncing !== null}
                      onChange={(e) => void setAutoJoin(c, e.target.checked)} />
                    Auto-join meetings from this calendar
                  </label>
                  <CalendarSyncStatusLine stamp={stamps[c.id] ?? null} />
                </div>
              ))}
              <button onClick={openSettings}
                className="t-xs pt-1 pr-2 pb-1 pl-2 bg-none bd-strong c-2 r-md" style={{ cursor: "pointer" }}>
                Add, rename or disconnect in Settings → Calendar
              </button>
            </>
          ) : (
            <>
              <div className="t-xs c-3 lh-snug">
                Paste your calendar&apos;s <b>secret ICS address</b> (Google Calendar → Settings → &quot;Secret address in iCal format&quot;).
                Upcoming meetings with a Meet/Zoom/Teams link appear under Upcoming and auto-join at start.
              </div>
              <input value={name} onChange={(e) => setName(e.target.value)} disabled={busy} maxLength={100}
                aria-label="Calendar name" placeholder="Calendar name"
                className="t-xs pt-1 pr-1_5 pb-1 pl-1_5 bg-3 bd r-md c-1" style={{ outline: "none" }} />
              <input value={url} placeholder="https://calendar.google.com/…/basic.ics" disabled={busy}
                type="password" autoComplete="off" aria-label="Secret ICS address"
                onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && url.trim()) void connectFirst(); }}
                className="t-xs pt-1 pr-1_5 pb-1 pl-1_5 bg-3 bd r-md c-1" style={{ outline: "none" }} />
              <button disabled={busy || !url.trim()} onClick={() => void connectFirst()}
                className="t-xs pt-1 pr-2 pb-1 pl-2 bd-none r-md" style={{ background: url.trim() ? "var(--accent)" : "var(--panel2)", color: url.trim() ? "var(--bg)" : "var(--t3)", cursor: url.trim() ? "pointer" : "default" }}>
                {busy || syncing !== null ? "Connecting…" : "Connect"}
              </button>
              <div className="t-xs c-3 lh-snug">
                Tip: the <i>public</i> address only carries events you made public — use the <b>secret</b> one for your full calendar.
              </div>
            </>
          )}
          {err && <div role="alert" className="t-xs c-danger">⚠ {err}</div>}
        </div>
      )}
    </div>
  );
}

// ── Meetings LIST (left) ─────────────────────────────────────────────────────────
function MeetingsList() {
  const layout = useService(LayoutServiceId);
  // The meeting LIST lives on the Today page (the center) — the sidebar never renders it too
  // (design-spec §v4 anti-pattern: same list twice). The rail keeps only its ACTIONS + a link to Today.
  const all = useLiveMeetings();                                   // real meetings (live + past) from agent-api
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
  const [denial, setDenial] = useState<ServiceDenialPresentation | null>(null);
  const addBot = async () => {
    const u = url.trim();
    if (!u || sent === "sending") return;
    // Parse + validate the pasted link/id against the platform formats (mirrors join-form).
    const parsed = parseMeetingInput(u, await getJitsiHosts());
    if (!parsed) { setSent("err"); setErrMsg("That doesn't look like a Meet / Zoom / Teams / Jitsi link."); setDenial(null); setTimeout(() => setSent(null), 5000); return; }
    setSent("sending"); setErrMsg(null); setDenial(null);
    let refused = false;
    try {
      // POST /bots through the authed gateway proxy (X-API-Key injected server-side from the cookie token).
      const r = await fetch("/api/bots", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ platform: parsed.platform, native_meeting_id: parsed.native_meeting_id, meeting_url: u, bot_name: defaultBotName() }),
      });
      if (r.ok) {
        setSent("ok"); setUrl("");
        // The list has no background poll, so force a re-fetch now and again as the bot
        // transitions requested → joining → active (else the meeting only shows on reload).
        refreshMeetings(); setTimeout(refreshMeetings, 2000); setTimeout(refreshMeetings, 6000);
      } else {
        // Surface the REAL reason, not a generic "bad link" (the cap/dup/auth cases are common).
        setSent("err");
        if (r.status === 429) { setErrMsg("You're at your meeting limit — stop one first."); }
        else if (r.status === 409) { setErrMsg("That meeting already has a bot."); }
        else if (r.status === 401) { setErrMsg("Not signed in — sign in and retry."); }
        else {
          // 403 service_not_allowed / 503 service_authority_unavailable are the deciding service
          // refusing, not an access fault: they get their own words and their own fix.
          const state = resolveJoinError(await readFailure(r));
          if (state.kind === "denial") { setDenial(state.presentation); setErrMsg(null); refused = true; }
          else setErrMsg(state.headline);
        }
      }
    } catch { setSent("err"); setErrMsg("Couldn't reach the server."); }
    // The transient flash clears itself; a denial panel is sticky until the next attempt — a
    // paywall the user has to act on must not vanish on a 5s timer.
    if (!refused) setTimeout(() => setSent(null), 5000);
  };
  return (
    <div className="p-2">
      <div className="pt-1_5 pr-1 pb-1_5 pl-1" style={{ display: "flex", alignItems: "center" }}>
        <span className="vx-label vx-grow">Meetings</span>
        {!minutesOnly() && <CalendarSyncButton />}
      </div>
      {/* MINUTES: meetings arrive by INVITATION — no paste-a-link, no bot button, no calendar
          sync, no plan-a-meeting. The rail states the mechanism instead of offering workarounds. */}
      {minutesOnly() ? (
        <div className="pt-0 pr-2 pb-2 pl-2 t-xs c-3 lh-snug">
          Invite the assistant&rsquo;s address to any calendar event — meetings appear here after
          they happen.
        </div>
      ) : (
      <div className="pt-0 pr-1 pb-2 pl-1">
        <div style={{ display: "flex", gap: 6 }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addBot(); }}
            placeholder="Paste a meeting link (Meet / Zoom / Teams / Jitsi)…" className="bg-2 bd-strong r-md pt-1_5 pr-2 pb-1_5 pl-2 c-1 t-xs" style={{ flex: 1, minWidth: 0, outline: "none" }} />
          <button onClick={() => void addBot()} disabled={!url.trim() || sent === "sending"} title="Send the Vexa bot to this meeting"
            className="bd-none r-md pt-0 pr-2 pb-0 pl-2 t-xs fw-600" style={{ flex: "none", background: url.trim() ? "var(--accent)" : "var(--panel2)", color: url.trim() ? "var(--on-accent)" : "var(--t3)", cursor: url.trim() ? "pointer" : "default" }}>
            {sent === "sending" ? "…" : "Add bot"}
          </button>
        </div>
        {sent === "ok" && <div className="t-xs c-success mt-1 lh-snug">Bot sent — admit it in the meeting; it appears here once it starts transcribing.</div>}
        {denial
          ? <ServiceDenialPanel presentation={denial} onRetry={() => void addBot()} />
          : sent === "err" && <div className="t-xs c-danger mt-1 lh-snug">{errMsg ?? "Couldn't send."}</div>}
        <div className="mt-2">
          <PlanMeetingButton />
          <CalendarSyncButton variant="row" />
        </div>
      </div>
      )}
      {/* The day itself renders in the center (Today) — the sidebar only links there. */}
      <button onClick={() => layout.openTab({ id: "today", title: "Today", kind: "today", params: {} })}
        className="bg-none bd-none pt-2 pr-2 pb-2 pl-2 t-xs c-3 lh-snug" style={{ display: "block", width: "100%", textAlign: "left", cursor: "pointer" }}
        onMouseEnter={(e) => (e.currentTarget.style.color = "var(--t2)")} onMouseLeave={(e) => (e.currentTarget.style.color = "var(--t3)")}>
        {all.length === 0 ? (minutesOnly() ? "No meetings yet — they arrive by invitation" : "No meetings yet — paste a Meet link above, or open Today →") : "Your meetings are in Today →"}
      </button>
    </div>
  );
}

// ── Meeting tab (center) — meeting shell + the live transcript canvas ─────────────────────────
// PRD decision 34: no model chips here. The product runs no model calls of its own beside the
// agent, so which model is configured is not a fact about this meeting and has no place on it.

/** Bot lifecycle controls on the meeting page header (owner ask 2026-07-09): Stop while the bot
 *  is in the call, Re-send once it stopped/completed/failed. Reuses the row-action map verbatim
 *  (same endpoints, same status vocabulary) — only bot actions surface here; row management
 *  (schedule/cancel/delete) stays in the sidebar menu. */
export function BotControls({ m, connected = true }: { m: MeetingMock; connected?: boolean }) {
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const acts = actionsFor(m).filter((a) => a.id === "stop" || a.id === "resend" || a.id === "send");
  if (acts.length === 0) return null;
  // The control's enabled-ness is a pure function of (row status, WS-connected): while the live
  // `meeting.status` stream is down the row may be a stale snapshot, so a state-bearing control
  // degrades to indeterminate/disabled — never an actionable "Stop bot" for a meeting the backend
  // may no longer have (issue #674).
  const disabled = busy || !connected;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flex: "none" }}>
      {acts.map((a) => {
        const danger = a.tone === "live";
        return (
          <button key={a.id} disabled={disabled}
            title={!connected ? "Live connection lost — reconnecting…" : undefined}
            onClick={() => {
              if (disabled) return;
              setErr(null); setBusy(true);
              void Promise.resolve(a.run((f) => setErr(f.message))).finally(() => setBusy(false));
            }}
            className="bg-none r-md pt-1 pr-3 pb-1 pl-3 t-xs fw-600" style={{ display: "inline-flex", alignItems: "center", gap: 6, border: `1px solid ${danger ? "var(--danger)" : "var(--line2)"}`, color: danger ? "var(--danger)" : "var(--accent)", cursor: disabled ? "default" : "pointer", opacity: disabled ? 0.6 : 1 }}>
            {a.id === "stop" ? "Stop bot" : "Send bot again"}
          </button>
        );
      })}
      {err && <span role="alert" className="t-xs c-danger" style={{ maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={err}>⚠ {err}</span>}
    </span>
  );
}

/** Header state as a pure function of (row, WS-connected) — the badge and the bot controls both
 *  derive from it, so a stale-live snapshot can never present as Live while the authoritative
 *  `meeting.status` stream is down (issue #674). Exported for the behavioral test. */
export function meetingHeaderState(m: MeetingMock | undefined, connected: boolean): "live" | "reconnecting" | "recap" | "connecting" {
  if (!m) return "connecting";
  if (m.status !== "live") return "recap";
  return connected ? "live" : "reconnecting";
}

/** Whether a requested meeting id has RESOLVED, is still resolving, or does not exist for this user.
 *  `listLoaded` is the meetings list having answered at least once — without it an id that simply hasn't
 *  been fetched yet is indistinguishable from one that doesn't exist, and an addressable URL
 *  (`/meetings/<id>`) would show a permanent "Connecting…" for a deleted or foreign meeting.
 *  Exported for the routing test. */
export type MeetingResolution = "resolving" | "resolved" | "not-found";
export function meetingResolution(m: MeetingMock | undefined, listLoaded: boolean): MeetingResolution {
  if (m) return "resolved";
  return listLoaded ? "not-found" : "resolving";
}

/** How long a bound-but-not-yet-listed id gets to ARRIVE before the canvas calls it dead. One
 *  snapshot round-trip, not a poll: the canvas asks once and then waits out the answer. */
export const MEETING_LOOKUP_GRACE_MS = 8_000;

/** What the canvas SHOWS for a requested id — `meetingResolution` plus the one state it lacked.
 *
 *  The founder's chat sent a bot into `google_meet/edh-vofu-jxm`, the gateway served
 *  `GET /meetings/132` 200 and listed it, and the Transcript tab said "Meeting not found — Nothing
 *  here matches 132". `meetingResolution` was correct about its own inputs and wrong about the
 *  world: it reads `listLoaded` as "the list is COMPLETE", when all the flag can honestly mean is
 *  "the list ANSWERED once" — and a row created mid-session is absent from that answer.
 *
 *  So an absent id gets one more question before it gets a verdict: `searching` while a fresh
 *  snapshot is in flight, `not-found` only once that has come back without it. The P0 is untouched —
 *  an unanswered list still resolves forever, so a network blip still cannot make a live meeting
 *  look deleted. This adds a bounded wait; it removes no gate. */
export type MeetingLookup = MeetingResolution | "searching";
export function meetingLookupState(m: MeetingMock | undefined, listLoaded: boolean, graceExpired: boolean): MeetingLookup {
  const base = meetingResolution(m, listLoaded);
  return base === "not-found" && !graceExpired ? "searching" : base;
}

/** Ask once, then wait a BOUNDED moment — returns whether the grace for `meetingId` is spent.
 *
 *  Two effects rather than one, and that is the whole subtlety: a single effect holding both the
 *  request and the timer clears its own timeout every time its inputs move, which is how a "bounded"
 *  grace silently becomes an unbounded one. The request is guarded by a ref (exactly one per id — a
 *  re-render must never become a request) and the deadline is an absolute timestamp, so re-running
 *  the timer effect re-arms the SAME instant instead of restarting the clock.
 *
 *  The wait ends on that clock and not on the answer, because a snapshot that legitimately lacks the
 *  row changes nothing and therefore notifies nobody. */
function useMeetingLookupGrace(missing: boolean, meetingId: string): boolean {
  const [expiredFor, setExpiredFor] = useState<string | null>(null);
  const askedFor = useRef<string | null>(null);
  const deadline = useRef<{ id: string; at: number } | null>(null);

  useEffect(() => {
    if (!missing || askedFor.current === meetingId) return;
    askedFor.current = meetingId;
    refreshMeetings();
  }, [missing, meetingId]);

  useEffect(() => {
    if (!missing || expiredFor === meetingId) return;
    if (deadline.current?.id !== meetingId) deadline.current = { id: meetingId, at: Date.now() + MEETING_LOOKUP_GRACE_MS };
    const t = window.setTimeout(() => setExpiredFor(meetingId), Math.max(0, deadline.current.at - Date.now()));
    return () => window.clearTimeout(t);
  }, [missing, meetingId, expiredFor]);

  return expiredFor === meetingId;
}

/** The clean terminal state for an id that isn't ours (deleted, mistyped, someone else's un-shared
 *  meeting). A dead reference is a normal outcome of a shareable URL, so it reads as an answer — not an
 *  error, not a spinner that never ends. Pure (no services) so it renders in a test as-is. */
export function MeetingNotFound({ meetingId, onOpenToday }: { meetingId: string; onOpenToday?: () => void }) {
  return (
    <div role="status" className="p-6" style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ maxWidth: 380, textAlign: "center" }}>
        <div className="t-md c-1 fw-500 mb-1_5">Meeting not found</div>
        <div className="t-xs c-3 lh-normal">
          {meetingId
            ? <>Nothing here matches <span className="f-mono c-2">{meetingId}</span>. It may have been deleted, or it belongs to someone who hasn&apos;t shared it with you.</>
            : <>This link doesn&apos;t carry a meeting. Open one from your day.</>}
        </div>
        {onOpenToday && (
          <button onClick={onOpenToday}
            className="mt-4 t-xs pt-1 pr-3 pb-1 pl-3 bg-none bd-strong c-2 r-md" style={{ cursor: "pointer" }}>
            Open today
          </button>
        )}
      </div>
    </div>
  );
}

/** The neutral face of `searching`. NOT a verdict and NOT an error — it says the app is still
 *  asking, which at that instant is the only true thing it can say. Pure, like its twin, so the
 *  test renders it as-is. */
export function MeetingLookingUp({ meetingId }: { meetingId: string }) {
  return (
    <div role="status" className="p-6" style={{ height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
      <div style={{ maxWidth: 380, textAlign: "center" }}>
        <div className="t-md c-1 fw-500 mb-1_5">Looking for this meeting…</div>
        <div className="t-xs c-3 lh-normal">
          {meetingId
            ? <>Fetching the latest list for <span className="f-mono c-2">{meetingId}</span>. A meeting that was just created can take a moment to appear here.</>
            : <>Fetching the latest meetings list.</>}
        </div>
      </div>
    </div>
  );
}

function MeetingTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const liveList = useLiveMeetings();
  const listLoaded = useLiveMeetingsLoaded();
  const connected = useLiveMeetingsConnection();
  const requestedMeetingId = params.meetingId as string;
  // ONE resolver, shared with the canvas body (useMeeting.resolveMeeting): the real meetings list is the
  // only source of truth — a mock never shadows a real id, and a real id never falls back to a mock. While
  // the async list is still loading the row is simply not-yet-resolved; we render the canvas bound to the
  // id with a neutral header (never a wrong/mock meeting), so the header can't disagree with the body.
  const m = liveList.find((x) => x.id === requestedMeetingId || x.native_id === requestedMeetingId);
  const header = meetingHeaderState(m, connected);
  // An id the list does not carry is a QUESTION before it is an answer: re-ask the list once, and
  // only call the meeting missing if the fresh snapshot still lacks it (see meetingLookupState).
  const graceExpired = useMeetingLookupGrace(!m && listLoaded, requestedMeetingId);
  const lookup = meetingLookupState(m, listLoaded, graceExpired);

  // Still asking — say so, neutrally. A row the chat created moments ago lands here.
  if (lookup === "searching") return <MeetingLookingUp meetingId={requestedMeetingId} />;

  // An unknown id — a stale/foreign/mistyped meeting URL — is a clean dead end, never a crash and never
  // an endless "Connecting…". Only once the list has actually answered AND been re-asked (P0: an
  // offline list keeps resolving, so a network blip can't make a live meeting look deleted).
  if (lookup === "not-found") {
    return (
      <MeetingNotFound
        meetingId={requestedMeetingId}
        onOpenToday={() => layout.openTab({ id: "today", title: "Today", kind: "today", params: {} })}
      />
    );
  }

  return (
    <div className="pt-4 pr-0 pb-6 pl-0" style={{ width: "100%", height: "100%", minHeight: 0, display: "flex", flexDirection: "column", boxSizing: "border-box" }}>
      <header className="mb-4" style={{ flex: "none", padding: `0 ${MEETING_CANVAS_CONTENT_INSET}px`, boxSizing: "border-box" }}>
        {/* WRAPS. This header was written for the full-width centre pane and its controls are all
            `flex: none`; rendered in a narrower column (minutes mode puts this same canvas in the
            resizable pages panel) a single non-wrapping row does not truncate, it OVERLAPS — the
            bot controls paint across the meeting's own name. Wrapping costs nothing at the width it
            was designed for and stays legible at every width below it. */}
        <div className="t-sm" style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 12, rowGap: 8, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: 9, rowGap: 6, minWidth: 0 }}>
            {/* THE PHASE IS A BADGE EARNED FROM OBSERVED STATE (guidelines §5.4, P21): "Live" only
                on the live signal, with its dot and word; the interim states are warning-toned with
                a spinner; the recap is the meeting colour. Sentence case, never uppercase. */}
            {header === "live"
              ? <Badge tone="success" dot>Live</Badge>
              : header === "reconnecting"
                ? <span title="Live connection lost — the last known state may be stale"><Badge tone="warning"><Spinner size={14} />Reconnecting…</Badge></span>
                : header === "recap"
                  ? <Badge tone="meeting">Recap</Badge>
                  : <Badge tone="warning"><Spinner size={14} />Connecting…</Badge>}
            <span className="r-full" style={{ width: 3, height: 3, background: "var(--t3)", flex: "none" }} />
            <span className="c-1 fw-500" style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m ? (m.title_custom ?? (m.native_id ?? m.title).replace(/^Google Meet · /, "")) : "Meeting"}</span>
            {m && <span className="c-3 t-xs" style={{ flex: "none" }}>{m.platform}</span>}
            {m && <span className="c-3" style={{ flex: "none" }}>{m.participants.length} in the room</span>}
          </div>
          {/* the spacer keeps the controls right-aligned while they fit on the title's line, and
              collapses to nothing once they have wrapped to their own */}
          <div style={{ flex: "1 0 0", minWidth: 0 }} />
          {m && <BotControls m={m} connected={connected} />}
          {m?.native_id && <ShareSessionButton platform={platformSlug(m.platform)} native={m.native_id} />}
        </div>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        <MeetingCanvasView key={requestedMeetingId} meetingId={requestedMeetingId} />
      </div>
    </div>
  );
}

registerList({ id: "meetings", label: "Meetings", icon: "cal", order: 20, component: MeetingsList,
  // clicking Meetings opens the user's DAY in the center (design-spec meeting-lifecycle-v2, W2)
  centerTab: { id: "today", title: "Today", kind: "today", params: {} } });
registerTab("meeting", MeetingTab);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeetingsNow()[0]; if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
