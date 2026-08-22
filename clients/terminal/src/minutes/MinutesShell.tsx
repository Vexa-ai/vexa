"use client";
/** MINUTES — the one-object shell (#1311). Design source: biz drafts/minutes-mock-chat.
 *
 *  WORKSPACES hold knowledge · a ROOM is a set of workspaces · CHATS live in rooms and inherit
 *  the set. One CSS grid holds the whole screen: three columns (rail · conversation · pages),
 *  two rows — the FIRST row is one shared 46px header band across all columns, so the top line
 *  is flush everywhere. State lives here; Rail / ContextBar / PagesPanel render it. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import { RoomOnboarding } from "../surfaces/roomOnboarding";
import {
  listSharedMemberships, readActiveSet, setSharedActive, deactivateWorkspace,
  readWorkspaceFile, type Membership,
} from "../surfaces/workspaceApi";
import { Rail, isHeld } from "./Rail";
import { ContextBar } from "./ContextBar";
import { PagesPanel } from "./PagesPanel";
import { T } from "./tokens";
import type { Page, Sel, View } from "./types";

export function MinutesShell() {
  const meetings = useLiveMeetings();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [view, setView] = useState<View>("meetings");
  const [sel, setSel] = useState<Sel>({ kind: "personal", id: "personal", label: "Personal" });
  const lastSel = useRef<{ meetings: Sel | null; rooms: Sel | null }>({ meetings: null, rooms: { kind: "personal", id: "personal", label: "Personal" } });
  const [pages, setPages] = useState<Page[]>([]);
  const [docPath, setDocPath] = useState("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  const [docBody, setDocBody] = useState<string | null>(null);
  const [docNonce, setDocNonce] = useState(0);
  const [wiz, setWiz] = useState(false);
  const [extra, setExtra] = useState<Record<string, { id: string; label: string }[]>>(() => {
    try { return JSON.parse(localStorage.getItem("vexa.minutes.chats") || "{}"); } catch { return {}; }
  });

  useEffect(() => { void listSharedMemberships().then(setMemberships).catch(() => undefined); }, []);

  const select = useCallback(async (s: Sel) => {
    setSel(s); setDocBody(null); setDocNonce((n) => n + 1);
    lastSel.current[s.kind === "meeting" ? "meetings" : "rooms"] = s;
    try {
      if (s.kind === "shared") {
        await setSharedActive(s.id, true);
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared" && m.slug !== s.id) { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      } else if (s.kind === "personal" || s.kind === "meeting") {
        const act = await readActiveSet().catch(() => null);
        for (const m of act?.active ?? []) if (m.role === "shared") { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
      }
    } catch { /* mounts are best-effort; the chat still runs */ }
    if (s.kind === "shared") { setPages([{ path: "README.md", slug: s.id, label: "This room's page" }]); setDocPath("README.md"); setDocSlug(s.id); }
    else if (s.kind === "org") { setPages([{ path: "README.md", slug: "_global", label: "The organisation" }]); setDocPath("README.md"); setDocSlug("_global"); }
    else if (s.kind === "meeting") {
      const m = meetings.find((x) => x.id === s.id);
      const native = (m as { native_id?: string } | undefined)?.native_id;
      const held = m ? isHeld(m) : false;
      // Pre-meeting there are no minutes and no transcript — no empty-promise chips.
      const p: Page[] = held && native
        ? [{ path: `kg/entities/meeting/${native}.md`, label: "Minutes" }, { path: `kg/entities/meeting/${native}.transcript.md`, label: "Transcript" }, { path: "README.md", label: "Personal page" }]
        : [{ path: "README.md", label: "Personal page" }];
      setPages(p); setDocPath(p[0].path); setDocSlug(undefined);
    } else { setPages([{ path: "README.md", label: "This room's page" }]); setDocPath("README.md"); setDocSlug(undefined); }
  }, [meetings]);

  const switchView = useCallback((v: View) => {
    if (v === view) return;
    setView(v);
    const want = lastSel.current[v];
    if (want) { void select(want); return; }
    if (v === "meetings") {
      const first = [...meetings].sort((a, b) => String((b as { start_time?: string }).start_time ?? "").localeCompare(String((a as { start_time?: string }).start_time ?? "")))[0];
      if (first) void select({ kind: "meeting", id: first.id, label: first.title.split(" — ")[0] });
    } else void select({ kind: "personal", id: "personal", label: "Personal" });
  }, [view, meetings, select]);

  useEffect(() => {
    let dead = false;
    setDocBody(null);
    readWorkspaceFile(docPath, docSlug ? { slug: docSlug } : undefined)
      .then((c) => { if (!dead) setDocBody(c); })
      .catch(() => { if (!dead) setDocBody(null); });
    return () => { dead = true; };
  }, [docPath, docSlug, sel.id, docNonce]);

  const session = useMemo(() => {
    if (sel.session) return sel.session;
    if (sel.kind === "personal") return "main";
    if (sel.kind === "shared") return `room-${sel.id.replace(/[^a-zA-Z0-9-]/g, "").slice(0, 24)}`;
    if (sel.kind === "org") return "org-setup";
    return `meet-${sel.id}`;
  }, [sel]);

  const flavor = sel.kind === "meeting" ? `meeting · ${isHeld(meetings.find((m) => m.id === sel.id) ?? ({} as never)) ? "held" : "upcoming"}`
    : sel.kind === "shared" ? "room · shared set" : sel.kind === "org" ? "room · admin" : "room · yours";
  const mounts = sel.kind === "shared" ? `[_global · ${sel.label} · _system]`
    : sel.kind === "org" ? "[_global rw · _system]"
    : sel.kind === "meeting" ? "[_global · personal · _system] + meeting" : "[_global · personal · _system]";

  const newChat = () => {
    const id = `chat-c${Date.now().toString(36)}`;
    const next = { ...extra, personal: [...(extra.personal ?? []), { id, label: "new chat" }] };
    setExtra(next); try { localStorage.setItem("vexa.minutes.chats", JSON.stringify(next)); } catch { /* ignore */ }
    void select({ kind: "personal", id: "personal", label: "Personal", session: id, chatLabel: "new chat" });
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: `${T.railW}px 1fr ${T.pagesW}px`, gridTemplateRows: `${T.headerH}px 1fr`, height: "100%", minHeight: 0, background: "var(--bg)" }}>
      <Rail view={view} onView={switchView} meetings={meetings} memberships={memberships} sel={sel}
        onSelect={(s) => void select(s)} extraChats={extra.personal ?? []} onNewChat={newChat} onNewWorkspace={() => setWiz(true)} />
      <ContextBar sel={sel} flavor={flavor} mounts={mounts} />
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0 }}>
        <Chat params={{ session }} />
      </main>
      <PagesPanel pages={pages} docPath={docPath} onOpen={(pg) => { setDocPath(pg.path); setDocSlug(pg.slug); }} body={docBody} />
      {wiz && <RoomOnboarding onClose={() => setWiz(false)} onCreated={() => { setWiz(false); void listSharedMemberships().then(setMemberships).catch(() => undefined); }} />}
    </div>
  );
}
