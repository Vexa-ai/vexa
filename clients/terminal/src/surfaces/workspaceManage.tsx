"use client";
/** Workspace PAGE — the center TAB (kind "workspace") opened from a WORKSPACES row. The workspace's
 *  README is the page BODY (the dashboard you land on); management is a compact header:
 *   • Header   — mount toggle + name (inline rename) · SHARE (the primary action — enables sharing if
 *                needed and opens the invite dialog) · ⋯ menu (Rename / Manage / Archive / Delete) ·
 *                one quiet meta row (members · GitHub sync · role).
 *   • README   — rendered below the header, live-refreshed; the hero of the page.
 *   • Manage   — the deeper sections (Purpose · GitHub push/pull/publish · Participants) fold below the
 *                README behind one quiet toggle, opened by the meta row / ⋯ / Share.
 *
 *  Opened via `manageTabDescriptor(...)` from workspace.tsx. Data-access is the workspaceApi SoC module. */
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type LayoutService, type TabDescriptor } from "../workbench/layout";
import { registerTab, type TabProps } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { Icon, Checkbox } from "../ui-kit";
import { Modal } from "../ui-kit/Modal";
import { ContextMenu, copyText } from "../ui-kit/ContextMenu";
import { MdxDoc } from "../ui-kit/MdxDoc";
import { presentError } from "./apiClient";
import { DocMetaContext } from "../ui-kit/docLinks";
import {
  readAttachedWorkspaces, readActiveSet, listSharedMemberships, renameWorkspace,
  activateWorkspace, deactivateWorkspace, setSharedActive, shareEnableWorkspace, unshareWorkspace,
  publishWorkspace, archiveWorkspace, deleteWorkspace,
  gitRemoteStatus, pushWorkspace, pullWorkspace, getGitToken,
  readWorkspacePurpose, writeWorkspacePurpose, readWorkspaceFile,
  listWorkspaceMembers, removeWorkspaceMember, leaveWorkspace, mintInvite,
  type AttachedWorkspaces, type ActiveMount, type Membership, type GitRemoteStatus, type WorkspaceMember, type SavedGitToken,
} from "./workspaceApi";

/** The tab descriptor a WORKSPACES row opens. `shared` ⇒ `slug` is a shared workspace_id (member view);
 *  otherwise `slug` is one of the caller's own slots. `name` seeds the tab title + header label. */
export const manageTabDescriptor = (slug: string, opts?: { shared?: boolean; name?: string }): TabDescriptor => ({
  id: `workspace:${opts?.shared ? "shared:" : ""}${slug}`,
  title: opts?.name || (slug === "seed" ? "Personal" : slug),
  kind: "workspace",
  params: { slug, shared: !!opts?.shared, name: opts?.name ?? null },
});

// ── shared section primitives (mirror workspace.tsx's token styling) ──────────────────────────────
const card: React.CSSProperties = { border: "1px solid var(--line)", borderRadius: 10, background: "var(--panel)", padding: "13px 15px", marginBottom: 14 };
const sectionTitle: React.CSSProperties = { fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em", marginBottom: 10, display: "flex", alignItems: "center", gap: 6 };
const btn = (variant: "primary" | "ghost" = "ghost"): React.CSSProperties => ({
  fontSize: 12.5, padding: "5px 12px", borderRadius: 7, cursor: "pointer",
  background: variant === "primary" ? "var(--accent)" : "transparent",
  color: variant === "primary" ? "var(--bg)" : "var(--t2)",
  border: variant === "primary" ? "none" : "1px solid var(--line)",
});
const field: React.CSSProperties = { fontSize: 12.5, padding: "6px 9px", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 7, color: "var(--t1)", outline: "none" };
const short = (subject: string) => subject.replace(/@.*$/, "").replace(/^u_/, "");

function Section({ icon, title, right, children }: { icon: string; title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <div style={card}>
      <div style={sectionTitle}><Icon name={icon} size={13} /><span>{title}</span>{right && <span style={{ marginLeft: "auto", textTransform: "none", letterSpacing: 0 }}>{right}</span>}</div>
      {children}
    </div>
  );
}

// ── the panel ─────────────────────────────────────────────────────────────────────────────────────
function WorkspaceManagePanel({ id, params }: TabProps) {
  const layout = useService(LayoutServiceId);
  const slug = params.slug as string;
  const shared = Boolean(params.shared);
  const initialName = (params.name as string | null) ?? null;

  const [attached, setAttached] = useState<AttachedWorkspaces>({ active: null, slots: {} });
  const [activeSet, setActiveSet] = useState<ActiveMount[]>([]);
  const [memberships, setMemberships] = useState<Membership[]>([]);
  const [status, setStatus] = useState<GitRemoteStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);   // transient success line

  const run = async (fn: () => Promise<unknown>, ok?: string) => {
    setBusy(true); setErr(null); setNote(null);
    try { await fn(); if (ok) setNote(ok); }
    catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };

  const loadCore = () => {
    void readAttachedWorkspaces().then(setAttached).catch(() => {});
    void readActiveSet().then((s) => setActiveSet(s.active)).catch(() => {});
    void listSharedMemberships().then(setMemberships).catch(() => {});
    void gitRemoteStatus({ slug }).then(setStatus).catch(() => setStatus(null));
  };
  useEffect(() => { loadCore(); }, [slug]);  // eslint-disable-line react-hooks/exhaustive-deps

  // ── derived facts about this workspace ──
  const isSeed = !shared && slug === (attached.active ?? "seed");
  const meta = attached.slots[slug];
  const displayName = shared ? (initialName || slug) : (meta?.name || (isSeed ? "Personal" : (meta?.repo ?? slug)) || initialName || slug);
  const mounted = shared
    ? activeSet.some((m) => m.role === "shared" && m.slug === slug)
    : activeSet.some((m) => m.slug === slug);
  const myRole = memberships.find((m) => m.workspace_id === slug)?.role;

  // The shared workspace_id the participants section operates on: a shared row IS the id; an own workspace
  // gets one once it is shared (share-enable returns it). Null until then → Share enables it first.
  const [shareWsId, setShareWsId] = useState<string | null>(shared ? slug : null);
  useEffect(() => { setShareWsId(shared ? slug : null); }, [shared, slug]);

  // README is the page; the deeper sections fold behind ONE quiet toggle (meta row / ⋯ open it).
  const [manage, setManage] = useState(false);
  const [members, setMembers] = useState<WorkspaceMember[]>([]);  // header meta only — Participants keeps its own list
  useEffect(() => {
    if (!shareWsId) { setMembers([]); return; }
    void listWorkspaceMembers(shareWsId).then(setMembers).catch(() => setMembers([]));
  }, [shareWsId, manage]);
  // The header's Share opens a MODAL (portaled — never lost below a long README): enable sharing on an
  // own private workspace first, then mint an invite link / email invite right there.
  const [invite, setInvite] = useState<{ mode: "link" | "email"; role: string; ttlDays: number; emails: string; link: string | null } | null>(null);
  const [inviteWsId, setInviteWsId] = useState<string | null>(null);
  const doShare = () => run(async () => {
    let wsId = shareWsId;
    if (!wsId && !shared) { ({ workspace_id: wsId } = await shareEnableWorkspace(slug)); setShareWsId(wsId); loadCore(); }
    if (!wsId) return;
    setInviteWsId(wsId);
    setInvite({ mode: "link", role: "contributor", ttlDays: 7, emails: "", link: null });
  });
  const doMintHeader = (s: NonNullable<typeof invite>) => run(async () => {
    if (!inviteWsId) return;
    const emails = s.mode === "email" ? s.emails.split(/[,\s]+/).map((e) => e.trim()).filter(Boolean) : undefined;
    const minted = await mintInvite({ workspace_id: inviteWsId, role: s.role, mode: s.mode === "email" ? "restricted" : "open",
      expires_in_sec: s.ttlDays * 86400, max_uses: s.mode === "email" ? 1 : 50, allowed_emails: emails });
    setInvite({ ...s, link: `${window.location.origin}/?invite=${encodeURIComponent(minted.token)}` });
  });

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 720, margin: "0 auto", padding: "22px 24px" }}>
        <Header
          slug={slug} shared={shared} isSeed={isSeed} displayName={displayName} mounted={mounted}
          archived={!!meta?.archived} busy={busy} onRun={run} reload={loadCore} layout={layout} tabId={id}
          onShare={doShare} onManage={() => setManage(true)}
          meta={{ members: shareWsId ? members : null, status, myRole: shared ? myRole : undefined }}
        />
        {err && <div role="alert" style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--danger)", background: "var(--panel)", border: "1px solid var(--danger)", borderRadius: 8, padding: "8px 11px" }}>⚠ {err}</div>}
        {note && <div role="status" style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--green)" }}>✓ {note}</div>}

        <ReadmeBody slug={slug} />

        {/* the deeper management sections, folded — README stays the hero */}
        <div onClick={() => setManage((v) => !v)}
          style={{ display: "flex", alignItems: "center", gap: 6, margin: "18px 0 12px", padding: "6px 0", cursor: "pointer", borderTop: "1px solid var(--line)", fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".05em" }}>
          <Icon name="chevR" size={12} style={{ transform: manage ? "rotate(90deg)" : "none", transition: "transform .12s" }} />
          <Icon name="gear" size={12} />Manage workspace
        </div>
        {manage && (<>
          <PurposeSection slug={slug} />

          <GitHubSection
            slug={slug} status={status} published_url={isSeed ? (attached.published_url ?? null) : null}
            defaultRepoName={defaultRepoName(displayName)}
            busy={busy} onRun={run} reload={loadCore}
          />

          <ParticipantsSection
            ownSlug={shared ? null : slug} shared={shared} shareWsId={shareWsId} myRole={shared ? myRole : undefined}
            setShareWsId={setShareWsId} busy={busy} onRun={run} reload={loadCore}
            layout={layout} tabId={id}
          />
        </>)}

        {invite && (
          <Modal title={`Share “${displayName}”`} onClose={() => setInvite(null)}>
            <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
              {(["link", "email"] as const).map((m) => (
                <button key={m} disabled={busy} onClick={() => setInvite({ ...invite, mode: m, link: null })}
                  style={{ ...btn(invite.mode === m ? "primary" : "ghost"), fontSize: 12 }}>
                  {m === "link" ? "Invite link" : "Add by email"}
                </button>
              ))}
            </div>
            <InviteDialog s={invite} setS={setInvite} onMint={doMintHeader} busy={busy} plain />
          </Modal>
        )}
      </div>
    </div>
  );
}

// ── README — the page body: the workspace's dashboard doc, live-refreshed ─────────────────────────
function ReadmeBody({ slug }: { slug: string }) {
  const [text, setText] = useState<string | null | undefined>(undefined);  // undefined = loading, null = no README
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setText(undefined); setError(null);
    const load = () => void readWorkspaceFile("README.md", { slug })
      .then((c) => { if (live) { setText(c); setError(null); } })
      .catch((e: unknown) => { if (live) setError(presentError(e).headline); });
    load();
    const iv = setInterval(() => { if (!document.hidden) load(); }, 8000);
    window.addEventListener("focus", load);
    return () => { live = false; clearInterval(iv); window.removeEventListener("focus", load); };
  }, [slug]);
  if (error) return <div role="alert" style={{ fontSize: 12.5, color: "var(--danger)" }}>⚠ Couldn’t read the README — {error}</div>;
  if (text === undefined) return <div style={{ fontSize: 12.5, color: "var(--t3)" }}>loading…</div>;
  if (text === null) return <div style={{ fontSize: 12.5, color: "var(--t3)" }}>No README yet — ask the agent in Chat to start this workspace’s dashboard.</div>;
  const body = text.replace(/^---\n[\s\S]*?\n---\n/, "");  // READMEs rarely carry frontmatter; strip if present
  return (
    <DocMetaContext.Provider value={{ path: "README.md", slug }}>
      <div style={{ fontSize: 14, color: "var(--t1)", lineHeight: 1.6 }}><MdxDoc>{body}</MdxDoc></div>
    </DocMetaContext.Provider>
  );
}

const defaultRepoName = (name: string) =>
  (name || "vexa-workspace").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "vexa-workspace";

// ── header: mount toggle + name (inline rename) · Share · ⋯ menu · one quiet meta row ──────────────
function Header({ slug, shared, isSeed, displayName, mounted, archived, busy, onRun, reload, layout, tabId, onShare, onManage, meta }: {
  slug: string; shared: boolean; isSeed: boolean; displayName: string; mounted: boolean; archived: boolean; busy: boolean;
  onRun: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; reload: () => void; layout: LayoutService; tabId: string;
  onShare: () => void; onManage: () => void;
  meta: { members: WorkspaceMember[] | null; status: GitRemoteStatus | null; myRole?: string };
}) {
  const [renaming, setRenaming] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null);
  const cancelled = useRef(false);
  const toggle = () => onRun(async () => {
    if (shared) await setSharedActive(slug, !mounted);
    else if (mounted) await deactivateWorkspace(slug); else await activateWorkspace({ slug });
    reload();
  });
  const doRename = (name: string) => onRun(async () => { await renameWorkspace(slug, name.trim()); setRenaming(false); layout.retargetTab(tabId, manageTabDescriptor(slug, { name: name.trim() || slug })); reload(); });
  const openMenu = (e: MouseEvent<HTMLSpanElement>) => { e.preventDefault(); setMenu({ x: e.clientX, y: e.clientY }); };
  const st = meta.status;
  const sync = !st?.has_home ? null : !st.tracked ? "not fetched" : st.ahead || st.behind ? `${st.ahead ? `↑${st.ahead}` : ""}${st.ahead && st.behind ? " " : ""}${st.behind ? `↓${st.behind}` : ""}` : "up to date";
  const metaItem: React.CSSProperties = { display: "inline-flex", alignItems: "center", gap: 4, cursor: "pointer" };
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Checkbox checked={mounted} disabled={busy} onChange={toggle}
          title={mounted ? "Mounted into the agent — uncheck to switch off" : "Switched off — check to mount"}
          label={`${displayName} — ${mounted ? "on" : "off"}`} />
        {renaming && !shared ? (
          <input autoFocus defaultValue={displayName} disabled={busy}
            onKeyDown={(e) => { if (e.key === "Enter") { cancelled.current = false; e.currentTarget.blur(); } else if (e.key === "Escape") { cancelled.current = true; e.currentTarget.blur(); } }}
            onBlur={(e) => { if (cancelled.current) { cancelled.current = false; setRenaming(false); } else doRename(e.currentTarget.value); }}
            style={{ ...field, flex: 1, fontSize: 18, fontWeight: 600, padding: "4px 8px" }} />
        ) : (
          <h1 style={{ margin: 0, fontSize: 19, fontWeight: 650, color: "var(--t1)", flex: 1, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{displayName}</h1>
        )}
        <button disabled={busy} onClick={onShare} style={btn("primary")} title="Share this workspace — mint an invite link or add by email">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="link" size={13} />Share</span>
        </button>
        <span onClick={openMenu} title="Rename · Manage · Archive · Delete"
          style={{ cursor: "pointer", color: "var(--t3)", padding: "2px 7px", fontSize: 16, lineHeight: 1, border: "1px solid var(--line)", borderRadius: 7 }}>⋯</span>
      </div>
      {/* ONE quiet meta row: members · GitHub sync · role/kind — each opens the manage fold. */}
      <div style={{ fontSize: 12, color: "var(--t3)", marginTop: 6, marginLeft: 26, display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
        {meta.members !== null && (
          <span style={metaItem} onClick={onManage} title="Participants">
            <Icon name="user" size={12} />{meta.members.length} member{meta.members.length === 1 ? "" : "s"}
          </span>
        )}
        {sync && (
          <span style={metaItem} onClick={onManage} title="GitHub sync">
            <Icon name="github" size={12} />{sync}
          </span>
        )}
        <span style={{ ...metaItem, cursor: "default" }}>
          {shared ? `shared${meta.myRole ? ` · ${meta.myRole}` : ""}` : isSeed ? "Personal" : meta.members !== null ? "shared by you" : "private"}
          {archived ? " · archived" : ""}
          {!mounted && " · not mounted"}
        </span>
      </div>
      {menu && (
        <ContextMenu x={menu.x} y={menu.y} onClose={() => setMenu(null)} items={[
          ...(!shared ? [{ id: "rename", label: "Rename", detail: "display label", onSelect: () => setRenaming(true) }] : []),
          { id: "manage", label: "Manage workspace", detail: "purpose · GitHub · participants", onSelect: onManage },
          ...(!shared && !isSeed ? [
            { id: "archive", label: archived ? "Un-archive" : "Archive", detail: "collapse · keep data", onSelect: () => void onRun(async () => { await archiveWorkspace(slug, !archived); reload(); }, archived ? "Un-archived." : "Archived.") },
            { id: "delete", label: "Delete", detail: "removes all data", onSelect: () => { if (window.confirm(`Delete "${displayName}"? This permanently removes the workspace and all its data.`)) void onRun(async () => { await deleteWorkspace(slug); layout.closeTab(tabId); }); } },
          ] : []),
        ]} />
      )}
    </div>
  );
}

// ── purpose (per-workspace, travels when shared, feeds the mount preamble) ─────────────────────────
function PurposeSection({ slug }: { slug: string }) {
  const [purpose, setPurpose] = useState<string>("");
  const [draft, setDraft] = useState<string>("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void readWorkspacePurpose({ slug }).then((p) => { setPurpose(p); setDraft(p); }).catch(() => {}); }, [slug]);
  const save = async () => {
    setBusy(true); setErr(null);
    try { const p = await writeWorkspacePurpose(draft, { slug }); setPurpose(p); setDraft(p); setEditing(false); }
    catch (e) { setErr(presentError(e).headline); }
    finally { setBusy(false); }
  };
  return (
    <Section icon="info" title="Purpose"
      right={!editing && <span onClick={() => setEditing(true)} style={{ cursor: "pointer", color: "var(--t3)" }}><Icon name="edit" size={13} /></span>}>
      {err && <div role="alert" style={{ fontSize: 12, color: "var(--danger)", marginBottom: 6 }}>⚠ {err}</div>}
      {editing ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <textarea autoFocus value={draft} disabled={busy} placeholder="What is this workspace for? (one line — the agent reads it to know where things belong)"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void save(); } if (e.key === "Escape") { setDraft(purpose); setEditing(false); } }}
            rows={2} style={{ ...field, resize: "vertical", lineHeight: 1.5 }} />
          <div style={{ display: "flex", gap: 8 }}>
            <button disabled={busy} onClick={() => void save()} style={btn("primary")}>{busy ? "Saving…" : "Save"}</button>
            <button disabled={busy} onClick={() => { setDraft(purpose); setEditing(false); }} style={btn()}>Cancel</button>
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 13.5, color: purpose ? "var(--t1)" : "var(--t3)", lineHeight: 1.5, cursor: "pointer" }} onClick={() => setEditing(true)}>
          {purpose || "No purpose set — click to describe what this workspace is for."}
        </div>
      )}
    </Section>
  );
}

// ── GitHub sync ──────────────────────────────────────────────────────────────────────────────────
function GitHubSection({ slug, status, published_url, defaultRepoName, busy, onRun, reload }: {
  slug: string; status: GitRemoteStatus | null; published_url: string | null;
  defaultRepoName: string; busy: boolean; onRun: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; reload: () => void;
}) {
  const [pushTok, setPushTok] = useState<{ open: boolean; token: string }>({ open: false, token: "" });
  const [pullTok, setPullTok] = useState<{ open: boolean; token: string }>({ open: false, token: "" });
  const [pub, setPub] = useState<{ name: string; priv: boolean; token: string } | null>(null);
  const [savedTok, setSavedTok] = useState<SavedGitToken | null>(null);  // the reusable server-side token
  useEffect(() => { void getGitToken().then(setSavedTok).catch(() => setSavedTok(null)); }, []);
  const hasSaved = !!savedTok?.set;
  const hasHome = !!status?.has_home;
  const url = status?.url || published_url;
  // token: prompt value → else the saved token (backend fills it in when omitted).
  const doPush = () => onRun(async () => { await pushWorkspace({ slug, token: pushTok.token.trim() || undefined }); setPushTok({ open: false, token: "" }); reload(); }, "Pushed to GitHub.");
  const doPull = () => onRun(async () => { const r = await pullWorkspace({ slug, token: pullTok.token.trim() || undefined }); setPullTok({ open: false, token: "" }); reload(); return r; }, "Pulled — fast-forwarded from GitHub.");
  const doPublish = (f: { name: string; priv: boolean; token: string }) => onRun(async () => { await publishWorkspace(f.name.trim(), f.priv, f.token.trim() || undefined, undefined, slug); setPub(null); reload(); }, "Published to GitHub.");
  // With a saved token, push/pull run straight away (no prompt); otherwise open the one-off token row.
  const onPush = () => { if (hasSaved) void doPush(); else { setPushTok({ open: true, token: "" }); setPullTok({ open: false, token: "" }); } };
  const onPull = () => { if (hasSaved) void doPull(); else { setPullTok({ open: true, token: "" }); setPushTok({ open: false, token: "" }); } };

  return (
    <Section icon="github" title="GitHub"
      right={status?.branch && <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--t2)" }}>{status.branch}</span>}>
      {status === null ? (
        <div style={{ fontSize: 12.5, color: "var(--t3)" }}>Checking the GitHub state…</div>
      ) : hasHome ? (<>
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12.5, color: "var(--t2)", marginBottom: 10, flexWrap: "wrap" }}>
          {url && <a href={url} target="_blank" rel="noreferrer" style={{ color: "var(--accent)", display: "inline-flex", alignItems: "center", gap: 4 }}><Icon name="openIn" size={13} />Open on GitHub</a>}
          <AheadBehind ahead={status!.ahead} behind={status!.behind} tracked={status!.tracked} />
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button disabled={busy} onClick={onPush} style={btn("primary")} title="Push this branch to its GitHub home (fast-forward only)"><span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="upload" size={13} />Push{status!.ahead ? ` (↑${status!.ahead})` : ""}</span></button>
          <button disabled={busy} onClick={onPull} style={btn()} title="Fetch + fast-forward from GitHub"><span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="refresh" size={13} />Pull{status!.behind ? ` (↓${status!.behind})` : ""}</span></button>
          {hasSaved && <span title="Using your saved GitHub token" style={{ fontSize: 11, color: "var(--t3)", display: "inline-flex", alignItems: "center", gap: 3 }}><Icon name="key" size={11} />saved token</span>}
        </div>
        {pushTok.open && (
          <TokenRow label="GitHub token (repo scope — used once, never stored)" value={pushTok.token} busy={busy}
            onChange={(t) => setPushTok({ open: true, token: t })} onSubmit={doPush} onCancel={() => setPushTok({ open: false, token: "" })}
            submitLabel={busy ? "Pushing…" : "Push"} required />
        )}
        {pullTok.open && (
          <TokenRow label="GitHub token (optional — public repos need none)" value={pullTok.token} busy={busy}
            onChange={(t) => setPullTok({ open: true, token: t })} onSubmit={doPull} onCancel={() => setPullTok({ open: false, token: "" })}
            submitLabel={busy ? "Pulling…" : "Pull"} />
        )}
      </>) : (<>
        {/* No home yet ⇒ this is a vexa-born workspace (attached clones always have origin) — offer
            Publish on ANY of them, own or shared (the backend permission-checks the slug). */}
        <div style={{ fontSize: 12.5, color: "var(--t2)", marginBottom: 10 }}>Not published yet — create a GitHub repo and push this workspace's full history.</div>
        {pub === null ? (
          <button disabled={busy} onClick={() => setPub({ name: defaultRepoName, priv: true, token: "" })} style={btn("primary")}>Publish to GitHub…</button>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <input autoFocus value={pub.name} placeholder="repo name" disabled={busy} onChange={(e) => setPub({ ...pub, name: e.target.value })} style={field} />
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: "var(--t2)", cursor: "pointer" }}>
              <input type="checkbox" checked={pub.priv} disabled={busy} onChange={(e) => setPub({ ...pub, priv: e.target.checked })} /> private repo
            </label>
            <input type="password" value={pub.token} placeholder={hasSaved ? "GitHub token (optional — using your saved token)" : "GitHub token (repo scope — used once, never stored)"} disabled={busy} onChange={(e) => setPub({ ...pub, token: e.target.value })} style={field} />
            <div style={{ display: "flex", gap: 8 }}>
              <button disabled={busy || !pub.name.trim() || (!pub.token.trim() && !hasSaved)} onClick={() => doPublish(pub)} style={btn("primary")}>{busy ? "Publishing…" : "Publish"}</button>
              <button disabled={busy} onClick={() => setPub(null)} style={btn()}>Cancel</button>
            </div>
          </div>
        )}
      </>)}
    </Section>
  );
}

function AheadBehind({ ahead, behind, tracked }: { ahead: number; behind: number; tracked: boolean }) {
  if (!tracked) return <span style={{ color: "var(--t3)" }}>not yet fetched</span>;
  if (!ahead && !behind) return <span style={{ color: "var(--green)" }}>up to date</span>;
  return (
    <span style={{ display: "inline-flex", gap: 8, fontFamily: "var(--mono)" }}>
      {ahead > 0 && <span style={{ color: "var(--accent)" }} title={`${ahead} local commit(s) to push`}>↑{ahead}</span>}
      {behind > 0 && <span style={{ color: "var(--warn)" }} title={`${behind} remote commit(s) to pull`}>↓{behind}</span>}
    </span>
  );
}

function TokenRow({ label, value, busy, onChange, onSubmit, onCancel, submitLabel, required }: {
  label: string; value: string; busy: boolean; onChange: (v: string) => void; onSubmit: () => void; onCancel: () => void; submitLabel: string; required?: boolean;
}) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 10 }}>
      <input autoFocus type="password" value={value} placeholder={label} disabled={busy}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && (!required || value.trim())) onSubmit(); if (e.key === "Escape") onCancel(); }}
        style={field} />
      <div style={{ display: "flex", gap: 8 }}>
        <button disabled={busy || (required && !value.trim())} onClick={onSubmit} style={btn("primary")}>{submitLabel}</button>
        <button disabled={busy} onClick={onCancel} style={btn()}>Cancel</button>
      </div>
    </div>
  );
}

// ── participants (shared membership) ───────────────────────────────────────────────────────────────
function ParticipantsSection({ ownSlug, shared, shareWsId, myRole, setShareWsId, busy, onRun, reload, layout, tabId }: {
  ownSlug: string | null; shared: boolean; shareWsId: string | null; myRole?: string;
  setShareWsId: (id: string) => void; busy: boolean; onRun: (fn: () => Promise<unknown>, ok?: string) => Promise<void>;
  reload: () => void; layout: LayoutService; tabId: string;
}) {
  const [members, setMembers] = useState<WorkspaceMember[]>([]);
  const [invite, setInvite] = useState<{ mode: "link" | "email"; role: string; ttlDays: number; emails: string; link: string | null } | null>(null);
  const loadMembers = () => { if (shareWsId) void listWorkspaceMembers(shareWsId).then(setMembers).catch(() => setMembers([])); };
  useEffect(() => { loadMembers(); }, [shareWsId]);  // eslint-disable-line react-hooks/exhaustive-deps

  // An OWN workspace that isn't shared yet → the CTA that turns on sharing.
  if (!shareWsId) {
    return (
      <Section icon="user" title="Participants">
        <div style={{ fontSize: 12.5, color: "var(--t3)", marginBottom: 10 }}>Private to you. Share it to add members and collaborate.</div>
        <button disabled={busy || !ownSlug} style={btn("primary")}
          onClick={() => ownSlug && onRun(async () => { const { workspace_id } = await shareEnableWorkspace(ownSlug); setShareWsId(workspace_id); reload(); }, "Sharing enabled.")}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="link" size={13} />Share this workspace</span>
        </button>
      </Section>
    );
  }

  const isOwner = myRole === "owner" || !shared;  // an own workspace you just shared → you're the owner
  const doMint = async (s: NonNullable<typeof invite>) => onRun(async () => {
    const emails = s.mode === "email" ? s.emails.split(/[,\s]+/).map((e) => e.trim()).filter(Boolean) : undefined;
    const minted = await mintInvite({ workspace_id: shareWsId, role: s.role, mode: s.mode === "email" ? "restricted" : "open",
      expires_in_sec: s.ttlDays * 86400, max_uses: s.mode === "email" ? 1 : 50, allowed_emails: emails });
    const link = `${window.location.origin}/?invite=${encodeURIComponent(minted.token)}`;
    setInvite({ ...s, link });
    loadMembers();
  });

  return (
    <Section icon="user" title="Participants" right={<span style={{ fontSize: 11.5, color: "var(--t3)" }}>{members.length} member{members.length === 1 ? "" : "s"}</span>}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: 10 }}>
        {members.map((m) => {
          const creator = m.role === "owner";
          return (
            <div key={m.subject} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 4px", borderBottom: "1px solid var(--line)" }}>
              <Icon name="user" size={14} style={{ color: "var(--t3)" }} />
              <span title={m.subject} style={{ flex: 1, fontSize: 13, color: "var(--t1)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{m.email || short(m.subject)}</span>
              <span style={{ fontSize: 11, color: creator ? "var(--accent)" : "var(--t3)", textTransform: "uppercase", letterSpacing: ".03em" }}>{creator ? "creator" : "member"}</span>
              {isOwner && !creator && (
                <span onClick={() => onRun(async () => { await removeWorkspaceMember(shareWsId, m.subject); loadMembers(); }, "Member removed.")}
                  title="Remove member" style={{ cursor: "pointer", color: "var(--t3)", padding: "0 3px" }}><Icon name="x" size={13} /></span>
              )}
            </div>
          );
        })}
        {members.length === 0 && <div style={{ fontSize: 12.5, color: "var(--t3)", padding: "4px 0" }}>No members yet — invite someone below.</div>}
      </div>

      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button disabled={busy} onClick={() => setInvite({ mode: "link", role: "contributor", ttlDays: 7, emails: "", link: null })} style={btn("primary")}><span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="link" size={13} />Invite link</span></button>
        <button disabled={busy} onClick={() => setInvite({ mode: "email", role: "contributor", ttlDays: 7, emails: "", link: null })} style={btn()}><span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="mail" size={13} />Add by email</span></button>
        {shared && (
          <button disabled={busy} style={{ ...btn(), marginLeft: "auto", color: "var(--danger)" }}
            onClick={() => onRun(async () => { await leaveWorkspace(shareWsId); layout.closeTab(tabId); }, "Left the workspace.")}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="logout" size={13} />Leave</span></button>
        )}
        {isOwner && shared && (
          <button disabled={busy} style={{ ...btn(), color: "var(--danger)" }}
            onClick={() => { if (window.confirm("Stop sharing? All members lose access and it becomes your private workspace.")) onRun(async () => { await unshareWorkspace(shareWsId); layout.closeTab(tabId); }, "Unshared."); }}>Unshare</button>
        )}
      </div>

      {invite && <InviteDialog s={invite} setS={setInvite} onMint={doMint} busy={busy} />}
    </Section>
  );
}

function InviteDialog({ s, setS, onMint, busy, plain }: {
  s: { mode: "link" | "email"; role: string; ttlDays: number; emails: string; link: string | null };
  setS: (s: any) => void; onMint: (s: any) => void; busy: boolean; plain?: boolean;
}) {
  // `plain` = hosted in the header's Share MODAL, which brings its own chrome/title — skip the box.
  return (
    <div style={plain
      ? { display: "flex", flexDirection: "column", gap: 8 }
      : { marginTop: 12, padding: "12px", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      {!plain && <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em" }}>{s.mode === "email" ? "Add by email" : "Invite link"}</div>}
      <div style={{ display: "flex", gap: 8 }}>
        <select value={s.role} disabled={busy} onChange={(e) => setS({ ...s, role: e.target.value, link: null })} style={{ ...field, flex: 1 }}>
          <option value="contributor">member (read + write)</option>
          <option value="viewer">viewer (read)</option>
        </select>
        <select value={s.ttlDays} disabled={busy} onChange={(e) => setS({ ...s, ttlDays: Number(e.target.value), link: null })} style={field}>
          <option value={1}>1 day</option><option value={7}>7 days</option><option value={30}>30 days</option>
        </select>
      </div>
      {s.mode === "email" && (
        <input value={s.emails} placeholder="emails (comma-separated) — only these may redeem" disabled={busy}
          onChange={(e) => setS({ ...s, emails: e.target.value, link: null })} style={field} />
      )}
      {s.link ? (
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <input readOnly value={s.link} onFocus={(e) => e.currentTarget.select()} style={{ ...field, flex: 1, fontSize: 11.5, color: "var(--t2)" }} />
          <button onClick={() => void copyText(s.link!)} style={btn("primary")}>Copy</button>
        </div>
      ) : (
        <button disabled={busy || (s.mode === "email" && !s.emails.trim())} onClick={() => onMint(s)} style={btn("primary")}>
          {busy ? "Creating…" : s.mode === "email" ? "Create email invite" : "Create link"}
        </button>
      )}
    </div>
  );
}

// (Archive/Delete retired into the header's ⋯ menu — the old Danger-zone section is gone.)

// Agent surface — absent in meetings-only mode.
if (!meetingsOnly()) {
  registerTab("workspace", WorkspaceManagePanel);
}
