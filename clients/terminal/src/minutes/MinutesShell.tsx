"use client";
/** MINUTES — the shell (#1311). Design source: biz drafts/minutes-mock-chat.
 *
 *  A WORKSPACE is a folder — the shared thing, scaffolded by a conversation.
 *  A PROJECT is private — your bundle of workspaces to chat over. Chats live in projects.
 *  One CSS grid: three columns (rail · conversation · pages), a shared 46px header band. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ASK_CHAT_EVENT } from "../canvas/actions";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import {
  listSharedMemberships, readActiveSet, setSharedActive, deactivateWorkspace,
  readWorkspaceFile, type Membership,
} from "../surfaces/workspaceApi";
import { ContextBar } from "./ContextBar";
import { PagesPanel } from "./PagesPanel";
import { ProjectComposer } from "./ProjectComposer";
import { loadProjects, saveProjects, type Project } from "./projects";
import { Rail, isHeld } from "./Rail";
import { T } from "./tokens";
import type { Page, Sel, View } from "./types";

export function MinutesShell() {
  const meetings = useLiveMeetings();
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [projects, setProjects] = useState<Project[]>(() => loadProjects());
  const [view, setView] = useState<View>("meetings");
  const [sel, setSel] = useState<Sel>({ kind: "personal", id: "personal", label: "Personal" });
  const lastSel = useRef<{ meetings: Sel | null; projects: Sel | null }>({ meetings: null, projects: { kind: "personal", id: "personal", label: "Personal" } });
  const [pages, setPages] = useState<Page[]>([]);
  const [docPath, setDocPath] = useState("README.md");
  const [docSlug, setDocSlug] = useState<string | undefined>(undefined);
  const [docBody, setDocBody] = useState<string | null>(null);
  const [docNonce, setDocNonce] = useState(0);
  const [composer, setComposer] = useState(false);

  useEffect(() => { void listSharedMemberships().then(setMemberships).catch(() => undefined); }, []);

  const mountSet = useCallback(async (wanted: string[]) => {
    // Mount every shared workspace in the project's set; park the rest. personal/_global/_system
    // ride along by construction. Best-effort — the chat runs regardless.
    try {
      const share = wanted.filter((w) => w !== "personal" && w !== "_global");
      for (const w of share) await setSharedActive(w, true).catch(() => undefined);
      const act = await readActiveSet().catch(() => null);
      for (const m of act?.active ?? []) if (m.role === "shared" && !share.includes(m.slug)) { try { await deactivateWorkspace(m.slug); } catch { /* parked */ } }
    } catch { /* best-effort */ }
  }, []);

  const select = useCallback(async (s: Sel) => {
    setSel(s); setDocBody(null); setDocNonce((n) => n + 1);
    lastSel.current[s.kind === "meeting" ? "meetings" : "projects"] = s;
    const proj = s.kind === "project" ? projects.find((pr) => pr.id === s.id) : undefined;
    await mountSet(proj ? proj.set : ["personal"]);
    if (s.kind === "org") { setPages([{ path: "README.md", slug: "_global", label: "The organisation" }]); setDocPath("README.md"); setDocSlug("_global"); }
    else if (s.kind === "meeting") {
      const m = meetings.find((x) => x.id === s.id);
      const native = (m as { native_id?: string } | undefined)?.native_id;
      const held = m ? isHeld(m) : false;
      const p: Page[] = held && native
        ? [{ path: `kg/entities/meeting/${native}.md`, label: "Minutes" }, { path: `kg/entities/meeting/${native}.transcript.md`, label: "Transcript" }, { path: "README.md", label: "Personal page" }]
        : [{ path: "README.md", label: "Personal page" }];
      setPages(p); setDocPath(p[0].path); setDocSlug(undefined);
    } else if (proj) {
      const ps: Page[] = proj.set.filter((w) => w !== "_global").map((w) => w === "personal"
        ? { path: "README.md", label: "personal" }
        : { path: "README.md", slug: w, label: w });
      ps.push({ path: "README.md", slug: "_global", label: "_global" });
      setPages(ps); setDocPath(ps[0].path); setDocSlug(ps[0].slug);
    } else { setPages([{ path: "README.md", label: "Personal page" }]); setDocPath("README.md"); setDocSlug(undefined); }
  }, [meetings, projects, mountSet]);

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
    if (sel.kind === "org") return "org-setup";
    return `meet-${sel.id}`;
  }, [sel]);

  const activeProj = sel.kind === "project" ? projects.find((pr) => pr.id === sel.id) : undefined;
  const flavor = sel.kind === "meeting" ? `meeting · ${isHeld(meetings.find((m) => m.id === sel.id) ?? ({} as never)) ? "held" : "upcoming"}`
    : sel.kind === "org" ? "project · admin" : sel.kind === "project" ? "project" : "project · yours";
  const mounts = sel.kind === "org" ? "[_global rw · _system]"
    : sel.kind === "meeting" ? "[_global · personal · _system] + meeting"
    : activeProj ? `[${activeProj.set.join(" · ")} · _system]` : "[_global · personal · _system]";

  const addChat = (projectId: string, label: string, kick?: string) => {
    const id = `pchat-${Date.now().toString(36)}`;
    const next = projects.map((pr) => pr.id === projectId ? { ...pr, chats: [...pr.chats, { id, label }] } : pr);
    setProjects(next); saveProjects(next);
    const proj = next.find((pr) => pr.id === projectId);
    void select({ kind: "project", id: projectId, label: proj?.name ?? "Project", session: id, chatLabel: label });
    if (kick) setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, prompt: kick } })), 1200);
  };

  // "+ workspace" is NOT a form: it starts a chat prompted to scaffold one — the conversation
  // is the wizard (founder ruling). The agent asks name → what it holds → who belongs, then
  // creates and seeds the workspace.
  const newWorkspace = () => addChat("personal", "new workspace",
    "[workspace-scaffold] The user wants a NEW WORKSPACE — the shared folder. Run the scaffold conversation: " +
    "ask what it should be called and what will live there (one question); infer a slug; ask who belongs " +
    "(emails, or their organisation); then create the shared workspace with that name, write its seed README " +
    "(purpose, what to pay attention to, who's in), and confirm with the #group tag they can use in invites. " +
    "One question at a time; write as you learn.");

  return (
    <div style={{ display: "grid", gridTemplateColumns: `${T.railW}px 1fr ${T.pagesW}px`, gridTemplateRows: `${T.headerH}px 1fr`, height: "100%", minHeight: 0, background: "var(--bg)" }}>
      <Rail view={view} onView={switchView} meetings={meetings} memberships={memberships} projects={projects} sel={sel}
        onSelect={(s) => void select(s)} onNewChat={(pid) => addChat(pid, "new chat")} onNewProject={() => setComposer(true)} onNewWorkspace={newWorkspace} />
      <ContextBar sel={sel} flavor={flavor} mounts={mounts} />
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0 }}>
        <Chat params={{ session }} />
      </main>
      <PagesPanel pages={pages} docPath={docPath} onOpen={(pg) => { setDocPath(pg.path); setDocSlug(pg.slug); }} body={docBody} />
      {composer && <ProjectComposer memberships={memberships}
        onCancel={() => setComposer(false)}
        onCreate={(name, set) => {
          const id = `proj-${Date.now().toString(36)}`;
          const next = [...projects.filter((pr) => pr.builtin !== "org"), { id, name, set, chats: [] as { id: string; label: string }[] }, ...projects.filter((pr) => pr.builtin === "org")];
          setProjects(next); saveProjects(next); setComposer(false);
          addChat(id, "first chat");
        }} />}
    </div>
  );
}
