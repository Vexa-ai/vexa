"use client";
/** MINUTES — the shell (#1311). Design source: biz drafts/minutes-mock-chat.
 *
 *  A WORKSPACE is a folder — the shared thing, scaffolded by a conversation.
 *  A PROJECT is private — your bundle of workspaces to chat over. Chats live in projects.
 *  One CSS grid: three columns (rail · conversation · pages), a shared 46px header band. */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ASK_CHAT_EVENT, ONBOARDING_SEED_EVENT, OPEN_ENTITY_EVENT, OPEN_MEETING_EVENT } from "../canvas/actions";
import { Chat } from "../surfaces/chat";
import { useLiveMeetings } from "../surfaces/liveMeetings";
import {
  listSharedMemberships, readActiveSet, setSharedActive, deactivateWorkspace,
  readWorkspaceFile, createSharedWorkspace, unshareWorkspace, deleteWorkspace, resetWorkspace, type Membership,
} from "../surfaces/workspaceApi";
import { ContextBar } from "./ContextBar";
import { PagesPanel } from "./PagesPanel";
import { ProjectComposer } from "./ProjectComposer";
import { DeleteCeremony } from "./DeleteCeremony";
import { loadProjects, saveProjects, type Project } from "./projects";
import { resolveDocRef } from "../ui-kit/docLinks";
import { Rail, isHeld } from "./Rail";
import { T, surface } from "./tokens";
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
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(() => {
    try { return JSON.parse(localStorage.getItem("vexa.minutes.collapsed") || "{}"); } catch { return {}; }
  });
  const toggleCollapse = (id: string) => setCollapsed((c) => {
    const n = { ...c, [id]: !c[id] };
    try { localStorage.setItem("vexa.minutes.collapsed", JSON.stringify(n)); } catch { /* ignore */ }
    return n;
  });
  // Deleting a chat drops it from the rail (its agent session stays on the server — the row is
  // the user's index, not the record). Deleting a project is refused while it still holds chats.
  const deleteChat = (projectId: string, chatId: string) => {
    setProjects((prev) => { const next = prev.map((pr) => pr.id === projectId ? { ...pr, chats: pr.chats.filter((c) => c.id !== chatId) } : pr); saveProjects(next); return next; });
    if (sel.session === chatId) void select({ kind: "personal", id: "personal", label: "Personal" });
  };
  const deleteProject = (projectId: string) => {
    setProjects((prev) => {
      const target = prev.find((pr) => pr.id === projectId);
      if (!target || target.builtin || target.chats.length) return prev;   // refuse: not empty
      const next = prev.filter((pr) => pr.id !== projectId); saveProjects(next); return next;
    });
    if (sel.kind === "project" && sel.id === projectId) void select({ kind: "personal", id: "personal", label: "Personal" });
  };
  // The deletion CEREMONY (typed-name confirm). Two verbs, per the ownership model:
  //   Delete — an OWNED shared workspace: un-share → private slug → remove. Gone for every member.
  //   Reset — a STRUCTURAL folder (personal baseline · _global): wipe to seed; the slot survives.
  // `_system` gets neither: sessions/continuity are never a folder you reset.
  const [ceremony, setCeremony] = useState<{ name: string; verb: "Delete" | "Reset"; detail: string; run: () => Promise<void> } | null>(null);
  const deleteOwnedWorkspace = async (workspaceId: string) => {
    try {
      const { slug } = await unshareWorkspace(workspaceId);
      await deleteWorkspace(slug);
    } catch (e) { window.alert(`Could not delete ${workspaceId}: ${e instanceof Error ? e.message : e}`); return; }
    setProjects((prev) => {
      const next = prev
        .map((pr) => ({ ...pr, set: pr.set.filter((w) => w !== workspaceId) }))
        .filter((pr) => pr.builtin || pr.set.some((w) => w !== "_global") || pr.id === "org");
      saveProjects(next); return next;
    });
    await listSharedMemberships().then(setMemberships).catch(() => undefined);
    if (sel.kind === "project" && !projects.find((pr) => pr.id === sel.id)) void select({ kind: "personal", id: "personal", label: "Personal" });
  };
  const askDeleteWorkspace = (workspaceId: string) => setCeremony({
    name: workspaceId, verb: "Delete",
    detail: "Removes this workspace's data for every member. Irreversible — there is no archive behind this.",
    run: () => deleteOwnedWorkspace(workspaceId),
  });
  const askResetWorkspace = (target: "personal" | "_global") => setCeremony({
    name: target, verb: "Reset",
    detail: target === "_global"
      ? "Wipes the organisation tier back to the empty seed (git history survives). Every member's assistant sees the reset on its next turn; the setup conversation starts from the first question. Admins only."
      : "Wipes your personal workspace back to the empty seed (git history survives). Your entities, notes and dashboard are removed; onboarding starts over.",
    run: async () => {
      try { await resetWorkspace(target); } catch (e) { window.alert(`Could not reset ${target}: ${e instanceof Error ? e.message : e}`); return; }
      probeScaffolded();
      void select({ kind: "personal", id: "personal", label: "Personal" });
    },
  });

  // The pages panel is DRAGGABLE — a document panel whose width is the reader's call.
  const [pagesW, setPagesW] = useState<number>(() => {
    const n = Number(localStorage.getItem("vexa.minutes.pagesW"));
    return Number.isFinite(n) && n >= T.pagesMin && n <= T.pagesMax ? n : T.pagesDefault;
  });
  const dragging = useRef(false);
  useEffect(() => {
    const move = (e: MouseEvent) => {
      if (!dragging.current) return;
      e.preventDefault();
      const room = window.innerWidth - T.railW - 420;   // never squeeze the conversation below ~420
      const w = Math.min(Math.min(T.pagesMax, room), Math.max(T.pagesMin, window.innerWidth - e.clientX));
      setPagesW(w);
    };
    const up = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.cursor = ""; document.body.style.userSelect = "";
      setPagesW((w) => { try { localStorage.setItem("vexa.minutes.pagesW", String(w)); } catch { /* ignore */ } return w; });
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, []);
  const startDrag = () => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; };
  const nudge = (d: number) => setPagesW((w) => {
    const n = Math.min(T.pagesMax, Math.max(T.pagesMin, w + d));
    try { localStorage.setItem("vexa.minutes.pagesW", String(n)); } catch { /* ignore */ }
    return n;
  });

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

  const select = useCallback(async (s: Sel, projOverride?: Project) => {
    setSel(s); setDocBody(null); setDocNonce((n) => n + 1);
    lastSel.current[s.kind === "meeting" ? "meetings" : "projects"] = s;
    const proj = projOverride ?? (s.kind === "project" ? projects.find((pr) => pr.id === s.id) : undefined);
    await mountSet(proj ? proj.set : ["personal"]);
    if (s.kind === "org") {
      setPages([{ path: "README.md", slug: "_global", label: "The organisation" }]); setDocPath("README.md"); setDocSlug("_global");
      // No warm-up turn: the setup opener is CACHED (empty-state greeting in chat.tsx) and the
      // flow grounding rides on the admin's first reply — the first LLM turn already carries an answer.
    }
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

  // Entity badges and wiki links in chat/pages open the document HERE — minutes mode has no tabs,
  // so the pages panel is the one place a document can land. The chip is added to this room's page
  // list (so you can flip back) and opened.
  useEffect(() => {
    const onEntity = async (e: Event) => {
      const d = (e as CustomEvent<{ path?: string; wikilink?: string; slug?: string; docPath?: string }>).detail || {};
      const r = await resolveDocRef(d, { path: d.docPath, slug: d.slug }).catch(() => null);
      if (!r) return;
      const label = (r.path.split("/").pop() ?? r.path).replace(/\.md$/, "");
      setPages((prev) => prev.some((pg) => pg.path === r.path && pg.slug === r.slug) ? prev : [...prev, { path: r.path, slug: r.slug, label }]);
      setDocPath(r.path); setDocSlug(r.slug); setDocNonce((n) => n + 1);
    };
    const onMeeting = (e: Event) => {
      const ref = (e as CustomEvent<{ ref?: string }>).detail?.ref;
      if (!ref) return;
      const native = ref.includes("/") ? ref.slice(ref.indexOf("/") + 1) : ref;
      const m = meetings.find((x) => (x as { native_id?: string }).native_id === native || String(x.id) === ref);
      if (m) void select({ kind: "meeting", id: m.id, label: m.title.split(" — ")[0] });
    };
    window.addEventListener(OPEN_ENTITY_EVENT, onEntity);
    window.addEventListener(OPEN_MEETING_EVENT, onMeeting);
    return () => { window.removeEventListener(OPEN_ENTITY_EVENT, onEntity); window.removeEventListener(OPEN_MEETING_EVENT, onMeeting); };
  }, [meetings, select]);

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

  const addChat = (projectId: string, label: string, kick?: string, projOverride?: Project) => {
    const id = `pchat-${Date.now().toString(36)}`;
    // FUNCTIONAL update — addChat may run in the same tick as project creation, and a stale
    // closure here silently deleted the just-created project (the "project not created" bug).
    setProjects((prev) => { const next = prev.map((pr) => pr.id === projectId ? { ...pr, chats: [...pr.chats, { id, label }] } : pr); saveProjects(next); return next; });
    const known = projOverride ?? projects.find((pr) => pr.id === projectId);
    void select({ kind: "project", id: projectId, label: known?.name ?? "Project", session: id, chatLabel: label }, known);
    if (kick) setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, prompt: kick } })), 1200);
  };

  // "+ workspace" is NOT a form and NOT a plain chat: it opens a WORKSPACE SETUP chat whose
  // whole job is scaffolding one. The workspace is created up front (so the agent has a real rw
  // mount to seed), then the conversation names it, writes CLAUDE.md + PURPOSE + README, and
  // settles membership. Founder ruling: the conversation is the wizard.
  // `.scaffolded` (written by the setup flows as their FINAL act) is the platform's signal that a
  // structural tier is ready. Absent → the rail offers a Setup button instead of the workspace row.
  const [scaffolded, setScaffolded] = useState<{ global: boolean | null; personal: boolean | null }>({ global: null, personal: null });
  const probeScaffolded = useCallback(() => {
    void readWorkspaceFile(".scaffolded", { slug: "_global" }).then((c) => setScaffolded((x) => ({ ...x, global: c !== null }))).catch(() => undefined);
    void readWorkspaceFile(".scaffolded").then((c) => setScaffolded((x) => ({ ...x, personal: c !== null }))).catch(() => undefined);
  }, []);
  useEffect(() => { probeScaffolded(); const t = setInterval(probeScaffolded, 20000); return () => clearInterval(t); }, [probeScaffolded]);

  // Setup entry points — the buttons the rail shows while a tier awaits setup.
  // Setup means a FRESH conversation (founder ruling 2026-08-22) — never reopening a stale thread.
  // Org-setup sessions are a FAMILY (`org-setup-<ts>`): each setup click mints a new one; the cached
  // void opener + grounding key on the prefix.
  const setupGlobal = () => {
    const chatId = `org-setup-${Date.now().toString(36)}`;
    const orgProj: Project = { id: "org", name: "Organisation", set: ["_global"], chats: [{ id: chatId, label: "setup" }] };
    let proj: Project = orgProj;
    setProjects((prev) => {
      const existing = prev.find((pr) => pr.id === "org");
      proj = existing ? { ...existing, chats: [...existing.chats, { id: chatId, label: "setup" }] } : orgProj;
      const next = existing ? prev.map((pr) => (pr.id === "org" ? proj : pr)) : [...prev, orgProj];
      saveProjects(next); return next;
    });
    void select({ kind: "project", id: "org", label: "Organisation", session: chatId, chatLabel: "setup" }, proj);
  };
  const setupPersonal = () => {
    addChat("personal", "onboarding");
    setTimeout(() => window.dispatchEvent(new CustomEvent(ONBOARDING_SEED_EVENT)), 500);
  };

  const newWorkspace = async () => {
    const stamp = new Date().toISOString().slice(5, 16).replace(/[-:T]/g, "");
    let created: { workspace_id: string } | null = null;
    try { created = await createSharedWorkspace(`workspace-${stamp}`); } catch { /* surfaced below */ }
    await listSharedMemberships().then(setMemberships).catch(() => undefined);
    const id = `wsetup-${Date.now().toString(36)}`;
    const projId = created ? `proj-ws-${created.workspace_id}` : "personal";
    if (created) {
      const proj: Project = { id: projId, name: "workspace setup", set: [created.workspace_id, "_global"], chats: [{ id, label: "scaffold" }] };
      setProjects((prev) => { const next = [...prev.filter((pr) => pr.builtin !== "org"), proj, ...prev.filter((pr) => pr.builtin === "org")]; saveProjects(next); return next; });
      void select({ kind: "project", id: projId, label: proj.name, session: id, chatLabel: "scaffold" }, proj);
    } else {
      addChat("personal", "workspace setup");
    }
    setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, { detail: { hidden: true, session: id, prompt:
      "[workspace-scaffold] A NEW SHARED WORKSPACE has just been created for this conversation and is mounted " +
      `read-write here${created ? ` as \`${created.workspace_id}\`` : ""} — you are seeding it. Ask ONE question at a time: ` +
      "(1) what it should be called and what will live in it; (2) who belongs — emails, or their organisation. " +
      "As you learn, WRITE into that workspace and commit: `CLAUDE.md` (what this folder is, its conventions — the map " +
      "any agent mounting it reads first), `PURPOSE` (one line: what this workspace is for, so writes route here " +
      "correctly), and `README.md` (its face page: purpose, what to pay attention to, who's in). " +
      "Rename the workspace to the name they give. Finish by telling them the `#group:` tag to put in calendar " +
      "invites so its meetings land here." } })), 1400);
  };

  // `?ask=<preset>` — the emailed link. App.tsx stashed the name; resolve it to an ADMIN-AUTHORED
  // body in `_global/asks/<name>.md` and open a fresh chat already holding it. The preset also says
  // which workspaces the chat is over, so context and opening prompt arrive together — which is the
  // whole point of the link. Editing the file changes every future click; nothing is rebuilt.
  const presetFired = useRef(false);
  useEffect(() => {
    if (presetFired.current) return;
    let raw: string | null = null;
    try { raw = localStorage.getItem("vexa.pendingPreset"); } catch { /* ignore */ }
    if (!raw) return;
    presetFired.current = true;
    try { localStorage.removeItem("vexa.pendingPreset"); } catch { /* ignore */ }
    let intent: { ask?: string; ws?: string; meeting?: string };
    try { intent = JSON.parse(raw) as typeof intent; } catch { return; }
    const name = (intent.ask || "").trim();
    // a NAME, and only a name — no slashes, no dots, nothing that walks out of asks/
    if (!/^[a-z0-9][a-z0-9_-]{0,63}$/i.test(name)) return;
    void (async () => {
      const body = await readWorkspaceFile(`asks/${name}.md`, { slug: "_global" }).catch(() => null);
      // an unknown preset opens nothing. Never fall back to text from the URL.
      if (!body || !body.trim()) return;
      // optional frontmatter: `mounts:` (comma-separated) and `label:`
      let text = body, mounts: string[] = [], label = name.replace(/[-_]/g, " ");
      const fm = /^---\n([\s\S]*?)\n---\n?/.exec(body);
      if (fm) {
        text = body.slice(fm[0].length);
        const m = /^mounts:\s*(.+)$/m.exec(fm[1]);
        if (m) mounts = m[1].split(",").map((x) => x.trim()).filter(Boolean);
        const l = /^label:\s*(.+)$/m.exec(fm[1]);
        if (l) label = l[1].trim();
      }
      if (intent.ws) mounts = [intent.ws, ...mounts.filter((x) => x !== intent.ws)];
      if (!mounts.length) mounts = ["_global", "personal"];
      const prompt = text
        .replace(/\{\{\s*meeting\s*\}\}/g, intent.meeting || "the meeting in view")
        .replace(/\{\{\s*ws\s*\}\}/g, mounts[0] || "")
        .replace(/\{\{\s*today\s*\}\}/g, new Date().toISOString().slice(0, 10))
        .trim();
      if (!prompt) return;
      const projId = `ask-${name}`;
      const chatId = `askchat-${Date.now().toString(36)}`;
      let proj: Project = { id: projId, name: label, set: mounts, chats: [{ id: chatId, label }] };
      setProjects((prev) => {
        const existing = prev.find((pr) => pr.id === projId);
        proj = existing ? { ...existing, set: mounts, chats: [...existing.chats, { id: chatId, label }] } : proj;
        const next = existing ? prev.map((pr) => (pr.id === projId ? proj : pr)) : [...prev, proj];
        saveProjects(next); return next;
      });
      void select({ kind: "project", id: projId, label, session: chatId, chatLabel: label }, proj);
      // NOT dispatching OPEN_MEETING_EVENT: its handler calls select({kind:"meeting"}), which would
      // replace the project selection made above and take the preset's mounts with it. The ref
      // reaches the agent through the {{meeting}} substitution, and it can open the meeting itself.
      // same settle delay the other seeded conversations use — the chat must be mounted to hear it
      setTimeout(() => window.dispatchEvent(new CustomEvent(ASK_CHAT_EVENT, {
        detail: { hidden: true, session: chatId, prompt },
      })), 1400);
    })();
  }, [select]);

  return (
    <div style={{ position: "relative", display: "grid", gridTemplateColumns: `${T.railW}px minmax(0, 1fr) ${pagesW}px`, gridTemplateRows: `${T.headerH}px 1fr`, height: "100%", minHeight: 0, background: surface.rail }}>
      <Rail view={view} onView={switchView} meetings={meetings} memberships={memberships} projects={projects} sel={sel}
        onSelect={(s) => void select(s)} onNewChat={(pid) => addChat(pid, "new chat")} onNewProject={() => setComposer(true)} onNewWorkspace={() => void newWorkspace()}
        collapsed={collapsed} onToggleCollapse={toggleCollapse} onDeleteChat={deleteChat} onDeleteProject={deleteProject} onDeleteWorkspace={askDeleteWorkspace} onResetWorkspace={askResetWorkspace}
        scaffolded={scaffolded} onSetupGlobal={setupGlobal} onSetupPersonal={setupPersonal} />
      <ContextBar sel={sel} flavor={flavor} mounts={mounts} />
      <main style={{ gridRow: 2, gridColumn: 2, minWidth: 0, minHeight: 0, background: surface.center }}>
        <Chat params={{ session }} />
      </main>
      {/* the pages panel's resize handle — a real separator: 11px hit area, a hairline that
          lights up on hover/focus, and arrow keys for anyone not dragging */}
      <div role="separator" aria-orientation="vertical" aria-label="Resize pages panel" tabIndex={0}
        onMouseDown={startDrag}
        onKeyDown={(e) => { if (e.key === "ArrowLeft") { e.preventDefault(); nudge(24); } if (e.key === "ArrowRight") { e.preventDefault(); nudge(-24); } }}
        onMouseEnter={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onMouseLeave={(e) => { if (!dragging.current) (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        onFocus={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "var(--accent)"; }}
        onBlur={(e) => { (e.currentTarget.firstElementChild as HTMLElement).style.background = "transparent"; }}
        style={{ position: "absolute", top: 0, bottom: 0, right: pagesW - 5, width: 11, cursor: "col-resize", zIndex: 5, display: "flex", justifyContent: "center", outline: "none" }}>
        <span style={{ width: 1, alignSelf: "stretch", background: "transparent", transition: "background .12s" }} />
      </div>
      <PagesPanel pages={pages} docPath={docPath} docSlug={docSlug} onOpen={(pg) => { setDocPath(pg.path); setDocSlug(pg.slug); }} body={docBody} onSaved={() => setDocNonce((n) => n + 1)} />
      {ceremony && <DeleteCeremony name={ceremony.name} verb={ceremony.verb} detail={ceremony.detail}
        onCancel={() => setCeremony(null)}
        onConfirm={() => { const c = ceremony; setCeremony(null); void c.run(); }} />}
      {composer && <ProjectComposer memberships={memberships}
        onCancel={() => setComposer(false)}
        onCreate={(name, set) => {
          const id = `proj-${Date.now().toString(36)}`;
          setProjects((prev) => { const next = [...prev.filter((pr) => pr.builtin !== "org"), { id, name, set, chats: [] as { id: string; label: string }[] }, ...prev.filter((pr) => pr.builtin === "org")]; saveProjects(next); return next; });
          setComposer(false);
          addChat(id, "first chat", undefined, { id, name, set, chats: [] });
        }} />}
    </div>
  );
}
