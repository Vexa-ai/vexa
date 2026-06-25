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
import { meetingById, liveMeeting, type MeetingMock } from "./mock";
import { useLiveMeetings, liveMeetingsNow, refreshMeetings } from "./liveMeetings";
import { usePreviewPinTab } from "./previewPinTab";

// ── Connected docs — the meeting's knowledge-graph entity + the [[entities]] it links ─────────────
//  The meeting doc lives at a deterministic path: kg/entities/meeting/<native>.md. When present we show
//  its title + the [[wikilinks]] parsed from the body as chips that open that entity's doc. A wikilink
//  [[Title]] is resolved to a real doc by matching its slug against the workspace tree (so we open the
//  entity under its true type folder, whatever that is). 404 → a quiet "no notes yet" state.
const SUBJECT_DOCS = "u_live";
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
  return { id: `meeting:${m.id}`, title: m.title.split(" — ")[0], kind: "meeting", params: { meetingId: m.id } };
}

function MeetingRow({ m }: { m: MeetingMock }) {
  const nav = usePreviewPinTab<HTMLDivElement>(meetingTab(m));
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const native = m.native_id ?? m.id;
  return (
    <div onClick={nav.onClick} onDoubleClick={nav.onDoubleClick} onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); setMenu({ x: e.clientX, y: e.clientY }); }} style={{ padding: "8px 9px", borderRadius: 7, cursor: "pointer", marginBottom: 2 }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "var(--panel2)")} onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
        {m.status === "live" && <span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", flex: "none" }} />}
        <span style={{ fontSize: 13, color: "var(--t1)", fontWeight: m.status === "live" ? 600 : 400, flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{m.title}</span>
        {m.native_id && <RowActions m={m} />}
      </div>
      <div style={{ fontSize: 11.5, color: m.status === "live" ? "var(--live)" : "var(--t3)", marginTop: 2, paddingLeft: m.status === "live" ? 14 : 0 }}>{m.when} · {m.platform}</div>
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[
          { id: "copy-reference", label: "Copy reference", detail: `@meeting:${native}`, onSelect: () => copyText(`@meeting:${native}`) },
        ]} />
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

// ── Meeting COPILOT tab (center) — meeting shell + canvas ──────────────────────────
function MeetingTab({ params }: TabProps) {
  const liveList = useLiveMeetings();
  const requestedMeetingId = params.meetingId as string;
  // real meetings FIRST (the list is the source of truth); mock is only a fallback for ids not in it.
  const m = liveList.find((x) => x.id === requestedMeetingId || x.native_id === requestedMeetingId) ?? meetingById(requestedMeetingId);
  const live = m?.status === "live";

  if (!m) return <div style={{ padding: 24, color: "var(--t3)" }}>Meeting not found.</div>;

  return (
    <div style={{ width: "100%", height: "100%", minHeight: 0, display: "flex", flexDirection: "column", padding: "16px 0 24px", boxSizing: "border-box" }}>
      <header style={{ flex: "none", marginBottom: 16, padding: `0 ${MEETING_CANVAS_CONTENT_INSET}px`, boxSizing: "border-box" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 9, fontSize: 13 }}>
          {live
            ? <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--live)", fontWeight: 600, letterSpacing: ".04em", fontSize: 11, textTransform: "uppercase" }}><span style={{ width: 7, height: 7, borderRadius: "50%", background: "var(--live)", boxShadow: "0 0 0 3px var(--livebg)" }} />Live</span>
            : <span style={{ fontSize: 11, color: "var(--t3)", fontWeight: 600, letterSpacing: ".04em", textTransform: "uppercase" }}>Ended</span>}
          <span style={{ width: 3, height: 3, borderRadius: "50%", background: "var(--t3)" }} />
          <span style={{ color: "var(--t1)", fontWeight: 550 }}>{m.platform}</span>
          <span style={{ color: "var(--t3)" }}>{m.participants.length} in the room</span>
          <span style={{ marginLeft: "auto", fontFamily: "var(--mono)", fontSize: 10, color: "var(--t3)" }}>dbg req={String(requestedMeetingId)} → m.id={String(m.id)} native={String(m.native_id)} “{m.title}”</span>
        </div>
        <p style={{ fontSize: 12.5, color: "var(--t3)", lineHeight: 1.5, margin: "6px 0 0", maxWidth: 460 }}>People and topics surfaced from this meeting. Open one for its card, or ask the right-rail chat for research grounded in this meeting.</p>
      </header>
      <div style={{ flex: 1, minHeight: 0 }}>
        <MeetingCanvasView key={m.id} meetingId={m.id} />
      </div>
    </div>
  );
}

registerList({ id: "meetings", label: "Meetings", icon: "cal", order: 20, component: MeetingsList });
registerTab("meeting", MeetingTab);
registerCommand({ id: "meeting.openLive", title: "Open live meeting", run: ({ container }) => { const m = liveMeetingsNow()[0] ?? liveMeeting(); if (m) container.get(LayoutServiceId).openTab(meetingTab(m)); } });
