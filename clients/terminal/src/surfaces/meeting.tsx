"use client";
/** Meetings (mocked backend) — the differentiator flow, rendered through the shared agent-window.
 *  • "meetings" LIST (left): meetings; the live one auto-opens; click any to (re)open its copilot.
 *  • "meeting" TAB (center): ONE stacked agent window — a centered list of the meeting's entities
 *    ("in the room" + "detected"); click one to open its entity card in the right sidebar (the agent
 *    creates it first if missing); Research runs in the chat below. The composer + suggested actions
 *    sit under the conversation. No horizontal split, no insight feed.
 *  • "transcript" CONTEXT (right): the real-time transcript (until you open an entity card). */
import { useEffect, useRef, useState } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type TabDescriptor } from "../workbench/layout";
import { AgentWindow, Conversation, opIcon, type Turn, type Op } from "../workbench/agent-window";
import { registerList, registerTab, registerContext, registerCommand, type TabProps, type ContextProps } from "../contributions";
import { Icon } from "../ui-kit";
import { EntityList, onResearchRequest } from "./entities";
import { meetingById, liveMeeting, meetingEntities, type MeetingMock, type Entity } from "./mock";
import { useMeetingLive, type LiveCard } from "./meetingLive";
import { useLiveMeetings, liveMeetingsNow, fetchTranscript, refreshMeetings } from "./liveMeetings";
import { usePreviewPinTab } from "./previewPinTab";
import type { TranscriptLine } from "./mock";

function formatTranscriptTime(start?: number | null): string {
  if (start == null || !Number.isFinite(start)) return "";
  const date = new Date(start * 1000);
  if (!Number.isFinite(date.getTime())) return "";
  return date.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

// ── live entities (streamed from the real dispatch) — compact, clickable to research ──────────────
const KIND: Record<string, { icon: string; color: string; bg: string }> = {
  person: { icon: "user", color: "var(--blue)", bg: "var(--bluebg)" },
  company: { icon: "building", color: "var(--accent)", bg: "var(--accentbg)" },
  topic: { icon: "tag", color: "var(--violet)", bg: "var(--violetbg)" },
  action: { icon: "zap", color: "var(--green)", bg: "var(--greenbg)" },
};

/** classify a tool name into one of the op icons so the operation line reads at a glance */
function toolOp(tool: string): Op {
  const t = tool.toLowerCase();
  const icon = /read|cat|open/.test(t) ? opIcon.read : /search|grep|find/.test(t) ? opIcon.search
    : /edit|write|append/.test(t) ? opIcon.edit : /git|commit/.test(t) ? opIcon.git
    : /web|fetch|http/.test(t) ? opIcon.web : opIcon.tool;
  return { icon, label: tool, status: "done" };
}

// ── Connected docs — the meeting's knowledge-graph entity + the [[entities]] it links ─────────────
//  The meeting doc lives at a deterministic path: kg/entities/meeting/<native>.md. When present we show
//  its title + the [[wikilinks]] parsed from the body as chips that open that entity's doc. A wikilink
//  [[Title]] is resolved to a real doc by matching its slug against the workspace tree (so we open the
//  entity under its true type folder, whatever that is). 404 → a quiet "no notes yet" state.
const SUBJECT_DOCS = "u_live";
const docSlug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const baseName = (p: string) => p.split("/").pop() ?? p;
const docTabFor = (path: string, title: string): TabDescriptor =>
  ({ id: `doc:${path}`, title, kind: "doc", params: { path }, context: { kind: "doc-context", params: { path } } });

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
        const r = await fetch(`/api/workspace/file?subject=${SUBJECT_DOCS}&path=${encodeURIComponent(path)}`);
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
        const files: string[] = (await (await fetch(`/api/workspace/tree?subject=${SUBJECT_DOCS}`)).json()).files ?? [];
        if (!alive) return;
        const map: Record<string, string> = {};
        for (const f of files) if (f.startsWith("kg/entities/") && f.endsWith(".md")) map[baseName(f).replace(/\.md$/, "")] = f;
        setSlugMap(map);
      } catch { /* offline — fall back to topic/<slug> */ }
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
                const path = slugMap[slug] ?? `kg/entities/topic/${slug}.md`;
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

function LiveCards({ cards, connected, onResearch }: { cards: LiveCard[]; connected: boolean; onResearch: (c: LiveCard) => void }) {
  // the feed re-surfaces the same entity across beats — dedupe by title (first kind/body wins)
  const seen = new Set<string>();
  const uniq = cards.filter((c) => c.title && !seen.has(c.title.toLowerCase()) && seen.add(c.title.toLowerCase()));
  return (
    <div style={{ marginTop: 22 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, margin: "0 2px 10px" }}>
        <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".07em", fontWeight: 600 }}>Entities</span>
        <span style={{ fontSize: 10.5, color: "var(--t3)", fontFamily: "var(--mono)" }}>{uniq.length}</span>
        <span style={{ flex: 1, height: 1, background: "var(--line)" }} />
        <span style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: 10.5, color: connected ? "var(--green)" : "var(--t3)" }}>
          <span style={{ width: 6, height: 6, borderRadius: "50%", background: connected ? "var(--green)" : "var(--t3)" }} />{connected ? "listening" : "…"}
        </span>
      </div>
      {uniq.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)", padding: "2px 2px" }}>Listening — people, companies, and topics will appear here to click and research.</div>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
        {uniq.map((c) => {
          const k = KIND[c.kind] ?? KIND.topic;
          return (
            <button key={c.title} className="vx-fade-up" title={c.body || `Research ${c.title}`} onClick={() => onResearch(c)}
              style={{ display: "inline-flex", alignItems: "center", gap: 7, padding: "5px 10px 5px 6px", borderRadius: 8, background: "var(--panel)", border: "1px solid var(--line)", color: "var(--t1)", fontSize: 12.5, cursor: "pointer", maxWidth: 280 }}
              onMouseEnter={(e) => { e.currentTarget.style.background = "var(--panel2)"; e.currentTarget.style.borderColor = "var(--line2)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = "var(--panel)"; e.currentTarget.style.borderColor = "var(--line)"; }}>
              <span style={{ width: 18, height: 18, flex: "none", borderRadius: 5, background: k.bg, color: k.color, display: "flex", alignItems: "center", justifyContent: "center" }}><Icon name={k.icon} size={11} /></span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.title}</span>
            </button>
          );
        })}
      </div>
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
  needs_help: { label: "Needs help", color: "var(--live)", bg: "var(--livebg)", kind: "needshelp" },
  active: { label: "Live", color: "var(--live)", bg: "var(--livebg)", kind: "live" },
  stopping: { label: "Stopping", color: "var(--t3)", bg: "var(--panel2)", kind: "stopping" },
  completed: { label: "Completed", color: "var(--green)", bg: "var(--greenbg)", kind: "terminal" },
  failed: { label: "Failed", color: "var(--live)", bg: "var(--livebg)", kind: "terminal" },
  stopped: { label: "Stopped", color: "var(--t3)", bg: "var(--panel2)", kind: "terminal" },
};
const badgeFor = (raw?: string) => STATUS_BADGE[raw ?? ""] ?? { label: raw ?? "—", color: "var(--t3)", bg: "var(--panel2)", kind: "terminal" as BadgeKind };

type RowAction = { id: string; label: string; tone: "accent" | "live" | "muted"; run: () => void };

/** The action→transition map for a row, keyed on its REAL status. Each action hits exactly one endpoint.
 *  Exported (additive — no runtime behavior change) so the behavioral test can assert each status offers
 *  the correct actions and each fires the correct endpoint+body. */
export function actionsFor(m: MeetingMock): RowAction[] {
  const native = m.native_id ?? m.id;
  const intent = (state: "idle" | "scheduled", at?: string) =>
    void fetch(`/api/meetings/google_meet/${encodeURIComponent(native)}/intent`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ intent: state, ...(at ? { at } : {}) }),
    }).finally(refreshMeetings);
  const send = () =>
    void fetch("/api/meeting/bot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: `https://meet.google.com/${native}` }) }).finally(refreshMeetings);
  const stop = () =>
    void fetch("/api/meeting/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ native_id: native, platform: "google_meet" }) }).finally(refreshMeetings);
  const schedule = () => {
    // minimal time picker: prompt for a local datetime, send as ISO. (A richer picker can replace this.)
    const def = new Date(Date.now() + 3600_000).toISOString().slice(0, 16);
    const input = typeof window !== "undefined" ? window.prompt("Schedule for (YYYY-MM-DD HH:MM, local):", def) : null;
    if (!input) return;
    const at = new Date(input).toISOString();
    intent("scheduled", at);
  };

  const raw = m.live_status ?? (m.status === "live" ? "active" : "completed");
  switch (raw) {
    case "idle":
      return [{ id: "schedule", label: "Schedule", tone: "accent", run: schedule }, { id: "send", label: "Send now", tone: "accent", run: send }];
    case "scheduled":
      return [{ id: "send", label: "Send now", tone: "accent", run: send }, { id: "cancel", label: "Cancel", tone: "muted", run: () => intent("idle") }];
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

/** Status badge + a small ▾ menu of action→transition items for one meeting row. */
function RowActions({ m }: { m: MeetingMock }) {
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
      <StatusBadge raw={m.live_status} />
      {acts.length > 0 && (
        <button title="Actions" onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
          style={{ background: "transparent", border: "1px solid var(--line2)", color: "var(--t2)", borderRadius: 6, padding: "1px 5px", fontSize: 11, lineHeight: 1.4, cursor: "pointer" }}>▾</button>
      )}
      {open && (
        <div style={{ position: "absolute", top: "100%", right: 0, marginTop: 4, minWidth: 132, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 8, boxShadow: "0 6px 20px rgba(0,0,0,.28)", padding: 4, zIndex: 40 }}>
          {acts.map((a) => (
            <button key={a.id} onClick={(e) => { e.stopPropagation(); setOpen(false); a.run(); }}
              style={{ display: "block", width: "100%", textAlign: "left", background: "transparent", border: "none", color: a.tone === "live" ? "var(--live)" : a.tone === "muted" ? "var(--t2)" : "var(--accent)", borderRadius: 6, padding: "6px 9px", fontSize: 12, fontWeight: 550, cursor: "pointer" }}
              onMouseEnter={(ev) => (ev.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(ev) => (ev.currentTarget.style.background = "transparent")}>
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export function meetingTab(m: MeetingMock): TabDescriptor {
  return { id: `meeting:${m.id}`, title: m.title.split(" — ")[0], kind: "meeting", params: { meetingId: m.id }, context: { kind: "transcript", params: { meetingId: m.id } } };
}

function MeetingRow({ m }: { m: MeetingMock }) {
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} style={{ padding: "8px 9px", borderRadius: 7, cursor: "pointer", marginBottom: 2 }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {m.status === "live" && <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", flex: "none" }} />}
        <span style={{ fontSize: 13, color: "var(--t1)", fontWeight: m.status === "live" ? 600 : 400, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.title}</span>
        {m.native_id && <RowActions m={m} />}
      </div>
      <div style={{ fontSize: 11.5, color: m.status === "live" ? "var(--live)" : "var(--t3)", marginTop: 2, paddingLeft: m.status === "live" ? 14 : 0 }}>{m.when} · {m.platform}</div>
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
  const addBot = async () => {
    const u = url.trim();
    if (!u || sent === "sending") return;
    setSent("sending");
    try {
      const r = await fetch("/api/meeting/bot", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: u }) });
      setSent(r.ok ? "ok" : "err");
      if (r.ok) setUrl("");
    } catch { setSent("err"); }
    setTimeout(() => setSent(null), 4000);
  };
  return (
    <div style={{ padding: "8px" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", padding: "6px 4px 6px" }}>meetings</div>
      <div style={{ padding: "0 4px 10px" }}>
        <div style={{ display: "flex", gap: 6 }}>
          <input value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") void addBot(); }}
            placeholder="Paste a Google Meet link…" style={{ flex: 1, minWidth: 0, background: "var(--panel)", border: "1px solid var(--line2)", borderRadius: 7, padding: "6px 8px", color: "var(--t1)", fontSize: 12, outline: "none" }} />
          <button onClick={() => void addBot()} disabled={!url.trim() || sent === "sending"} title="Send the Vexa bot to this meeting"
            style={{ flex: "none", background: url.trim() ? "var(--accent)" : "var(--panel2)", color: url.trim() ? "var(--on-accent, #241008)" : "var(--t3)", border: "none", borderRadius: 7, padding: "0 10px", fontSize: 12, fontWeight: 600, cursor: url.trim() ? "pointer" : "default" }}>
            {sent === "sending" ? "…" : "Add bot"}
          </button>
        </div>
        {sent === "ok" && <div style={{ fontSize: 11, color: "var(--green)", marginTop: 5, lineHeight: 1.4 }}>Bot sent — admit it in the meeting; it appears here once it starts transcribing.</div>}
        {sent === "err" && <div style={{ fontSize: 11, color: "var(--live)", marginTop: 5, lineHeight: 1.4 }}>Couldn&apos;t send — make sure it&apos;s a Google Meet link.</div>}
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

// ── Meeting COPILOT tab (center) — entity list + research chat ─────────────────────
function MeetingTab({ params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const liveList = useLiveMeetings();
  const m = meetingById(params.meetingId as string) ?? liveList.find((x) => x.id === params.meetingId);
  const live = m?.status === "live";
  const [feed, setFeed] = useState<Turn[]>([]);
  const [value, setValue] = useState("");
  const [composerFocus, setComposerFocus] = useState(false);
  const idRef = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [feed]);

  // a REAL agent turn over /api/chat (the same engine as the chat surface): tool-calls stream as ops,
  // then the reply + commit land. One session per meeting so research accumulates context.
  const [busy, setBusy] = useState(false);
  const patchAgent = (fn: (t: Extract<Turn, { role: "agent" }>) => Extract<Turn, { role: "agent" }>) =>
    setFeed((ts) => ts.map((t, i) => (i === ts.length - 1 && t.role === "agent" ? fn(t) : t)));

  // the meeting copilot's chat is separate from the meeting transcript (it's a chat agent) — so inject
  // the live transcript as context on EVERY call, grounding research/questions in what was actually said.
  const meetingContext = () => {
    const t = liveData.transcript.filter((s) => s.completed !== false).slice(-120).map((s) => `[${s.speaker}] ${s.text}`).join("\n");
    return t ? `You are the copilot for a live meeting ("${m?.title ?? ""}"). The meeting transcript so far:\n${t}\n\n---\n` : "";
  };
  const send = async (label: string, prompt: string) => {
    if (busy) return;
    const n = idRef.current++;
    setFeed((f) => [...f, { id: `u-${n}`, role: "user", text: label }, { id: `a-${n}`, role: "agent", text: "", ops: [] }]);
    setBusy(true);
    try {
      const r = await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ prompt: meetingContext() + prompt, subject: "u_live", session: `meeting-${m?.id ?? "live"}` }) });
      const reader = r.body?.getReader(); const dec = new TextDecoder(); let buf = "";
      while (reader) {
        const { value: chunk, done } = await reader.read(); if (done) break;
        buf += dec.decode(chunk, { stream: true });
        const ls = buf.split("\n"); buf = ls.pop() ?? "";
        for (const line of ls) {
          if (!line.startsWith("data: ")) continue;
          let ev: { type: string; text?: string; tool?: string; sha?: string };
          try { ev = JSON.parse(line.slice(6)); } catch { continue; }
          if (ev.type === "message-delta") patchAgent((t) => ({ ...t, text: (t.text ?? "") + (ev.text ?? "") }));
          else if (ev.type === "tool-call") patchAgent((t) => ({ ...t, ops: [...t.ops, toolOp(ev.tool ?? "tool")] }));
          else if (ev.type === "commit") patchAgent((t) => ({ ...t, commit: ev.sha }));
          else if (ev.type === "rejected") patchAgent((t) => ({ ...t, rejected: "workspace.v1 violation — reverted" }));
        }
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
      }
    } finally { setBusy(false); }
  };

  const research = (title: string, kind?: string) =>
    send(`Research ${title}`, `In the live meeting "${m?.title ?? "this meeting"}", the ${kind ?? "entity"} "${title}" came up. Research it (web + the workspace knowledge graph), append a concise note to its entity file, and commit. Keep it tight.`);
  const ask = (text: string) => send(text, text);

  // open an entity card in the right sidebar (the card creates it first if missing)
  const openEntity = (e: Entity) => layout.setContext({ kind: "entity", params: { title: e.title, from: m?.id } });

  // research-from-entity-card requests land in this meeting's chat
  useEffect(() => onResearchRequest((title) => research(title)), [m?.id]);

  // a live-backed meeting subscribes to the REAL dispatch Stream (transcript + copilot cards)
  const liveData = useMeetingLive(m?.id ?? "", (m?.session_uid as string) ?? "");

  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;
  const isLive = !!m.session_uid;
  const { present, detected } = meetingEntities(m);

  const composer = (
    <div style={{ border: `1px solid ${composerFocus ? "var(--accent)" : "var(--line2)"}`, borderRadius: 11, background: "var(--panel)", padding: "9px 9px 9px 13px", display: "flex", alignItems: "center", gap: 10, transition: "border-color .12s ease" }}>
      <input value={value} onChange={(e) => setValue(e.target.value)} onFocus={() => setComposerFocus(true)} onBlur={() => setComposerFocus(false)}
        onKeyDown={(e) => { if (e.key === "Enter" && value.trim()) { ask(value.trim()); setValue(""); } }}
        placeholder="Ask the copilot, or research an entity…" style={{ flex: 1, background: "none", border: "none", outline: "none", color: "var(--t1)", fontSize: 13.5 }} />
      <button aria-label="Send" onClick={() => { if (value.trim()) { ask(value.trim()); setValue(""); } }} disabled={!value.trim()}
        style={{ background: value.trim() ? "var(--accent)" : "var(--panel2)", color: value.trim() ? "#241008" : "var(--t3)", border: "none", width: 30, height: 30, borderRadius: 7, display: "flex", alignItems: "center", justifyContent: "center", cursor: value.trim() ? "pointer" : "default", transition: "background .12s ease, color .12s ease", flex: "none" }}><Icon name="send" size={15} /></button>
    </div>
  );
  const actions = (
    <div className="vx-hscroll" style={{ display: "flex", alignItems: "center", gap: 6, padding: "1px 0" }}>
      <span style={{ fontSize: 10.5, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".06em", fontWeight: 600, flex: "none", paddingRight: 2 }}>Suggested</span>
      {m.actions.map((a) => (
        <button key={a.id} onClick={() => ask(a.label)} title={a.detail}
          style={{ display: "inline-flex", alignItems: "center", gap: 6, border: "1px solid var(--line2)", borderRadius: 8, background: "var(--panel)", color: "var(--t2)", padding: "5px 10px", fontSize: 12, cursor: "pointer", whiteSpace: "nowrap", flex: "none" }}
          onMouseEnter={(ev) => { ev.currentTarget.style.color = "var(--t1)"; ev.currentTarget.style.background = "var(--panel2)"; }}
          onMouseLeave={(ev) => { ev.currentTarget.style.color = "var(--t2)"; ev.currentTarget.style.background = "var(--panel)"; }}>
          <Icon name="spark" size={12} style={{ color: "var(--accent)" }} />{a.label}
        </button>
      ))}
    </div>
  );

  return (
    <AgentWindow scrollRef={scrollRef} composer={composer} actions={actions}>
      <div style={{ maxWidth: 560, margin: "0 auto" }}>
        <header style={{ marginBottom: 24 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13 }}>
            {live
              ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--live)", fontWeight: 600, letterSpacing: ".04em", fontSize: 11, textTransform: "uppercase" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", boxShadow: "0 0 0 3px var(--livebg)" }} />Live</span>
              : <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase" }}>Ended</span>}
            <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--t3)" }} />
            <span style={{ color: "var(--t1)", fontWeight: 550 }}>{m.platform}</span>
            <span style={{ color: "var(--t3)" }}>{m.participants.length} in the room</span>
          </div>
          <p style={{ fontSize: 12.5, color: "var(--t3)", lineHeight: 1.5, margin: "6px 0 0", maxWidth: 460 }}>People and topics surfaced from this meeting. Open one for its card, or research it — I'll work in the chat below.</p>
        </header>
        {!isLive && <EntityList present={present} detected={detected} onOpen={openEntity} onResearch={(e) => research(e.title, e.type)} />}
        {isLive && <LiveCards cards={liveData.cards} connected={liveData.connected} onResearch={(c) => research(c.title, c.kind)} />}
        {m.native_id && <ConnectedPanel native={m.native_id} docs={m.docs} />}
        {feed.length > 0 && (
          <div className="vx-fade-up" style={{ borderTop: "1px solid var(--line)", marginTop: 10, paddingTop: 20 }}>
            <Conversation turns={feed} busy={busy} />
          </div>
        )}
      </div>
    </AgentWindow>
  );
}

// ── Transcript CONTEXT (right) ────────────────────────────────────────────────────
function TranscriptContext({ params }: ContextProps) {
  const liveList = useLiveMeetings();
  const m = meetingById(params.meetingId as string) ?? liveList.find((x) => x.id === params.meetingId);
  const isLive = !!m?.session_uid;
  const liveData = useMeetingLive(m?.id ?? "", (m?.session_uid as string) ?? "");
  const scrollRef = useRef<HTMLDivElement>(null);
  // a PAST (recorded) meeting fetches its transcript over REST (gateway → meeting-api) when opened
  const [recorded, setRecorded] = useState<TranscriptLine[]>([]);
  useEffect(() => {
    if (!m || isLive) return;
    let live = true;
    void fetchTranscript(m.platform, m.native_id ?? m.id).then((segs) => { if (live) setRecorded(segs); });
    return () => { live = false; };
  }, [m?.id, m?.platform, m?.native_id, isLive]);
  useEffect(() => { scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }); }, [liveData.transcript.length, recorded.length]);
  if (!m) return <div style={{ padding: 16, color: "var(--t3)" }}>No transcript.</div>;
  const lines = isLive
    ? liveData.transcript.map((s) => ({ t: formatTranscriptTime(s.t), speaker: s.speaker, text: s.text, pending: s.completed === false, id: s.id }))
    : recorded.map((l) => ({ t: l.t, speaker: l.speaker, text: l.text, pending: false, id: undefined as string | undefined }));
  const streaming = isLive && liveData.connected && !liveData.ended;
  const liveDot = isLive ? (liveData.connected && !liveData.ended) : m.status === "live";
  return (
    <div ref={scrollRef} style={{ padding: "14px 16px", height: "100%", overflowY: "auto" }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em", marginBottom: 10, display: "flex", alignItems: "center", gap: 7 }}>
        {liveDot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--live)" }} />}transcript
      </div>
      {lines.map((l, i) => (
        <div key={l.id ?? i} style={{ marginBottom: 11, opacity: l.pending ? 0.5 : 1, transition: "opacity .18s ease" }}>
          <div style={{ display: "flex", gap: 8, fontSize: 11, color: "var(--t3)", marginBottom: 2 }}><span style={{ fontFamily: "var(--mono)" }}>{l.t}</span><span style={{ color: "var(--t2)", fontWeight: 500 }}>{l.speaker}</span>{l.pending && <span style={{ color: "var(--live)", fontFamily: "var(--mono)", fontSize: 9.5, letterSpacing: ".04em" }}>● live</span>}</div>
          <div style={{ fontSize: 13, color: "var(--t1)", lineHeight: 1.5, fontStyle: l.pending ? "italic" : "normal" }}>{l.text}</div>
        </div>
      ))}
      {isLive && lines.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)" }}>Waiting for the transcript…</div>}
      {!isLive && lines.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)" }}>No transcript recorded for this meeting.</div>}
      {streaming && <div style={{ fontSize: 12, color: "var(--t3)" }}>…</div>}
    </div>
  );
}

registerList({ id: "meetings", label: "Meetings", icon: "cal", order: 20, component: MeetingsList });
registerTab("meeting", MeetingTab);
registerContext("transcript", TranscriptContext);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeetingsNow()[0] ?? liveMeeting(); if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
