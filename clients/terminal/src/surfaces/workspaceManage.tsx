"use client";
/** Workspace MANAGE panel — the center TAB (kind "workspace") opened from a WORKSPACES row. ONE hub that
 *  houses everything about a single workspace:
 *   • Header      — inline rename + on/off (mount) toggle + the purpose one-liner.
 *   • GitHub      — push · pull · open + ahead/behind, for ANY workspace with a home remote (attached
 *                   clone's `origin`, or a published vexa-born workspace); Publish for an unpublished seed.
 *   • Purpose     — edit the per-workspace purpose (stored in the workspace, travels when shared, feeds
 *                   the agent's mount preamble).
 *   • Participants— the shared-workspace members list: role (creator/member), remove (owner), LEAVE
 *                   (self), invite-link + add-by-email (both small sub-dialogs).
 *
 *  Opened via `openManageTab(...)` from workspace.tsx. Data-access is the workspaceApi SoC module. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useService } from "../platform";
import { LayoutServiceId, type LayoutService, type TabDescriptor } from "../workbench/layout";
import { registerTab, type TabProps } from "../contributions";
import { meetingsOnly } from "../app/mode";
import { Icon, Checkbox } from "../ui-kit";
import { copyText } from "../ui-kit/ContextMenu";
import {
  readAttachedWorkspaces, readActiveSet, listSharedMemberships, renameWorkspace,
  activateWorkspace, deactivateWorkspace, setSharedActive, shareEnableWorkspace, unshareWorkspace,
  publishWorkspace, archiveWorkspace, deleteWorkspace,
  gitRemoteStatus, pushWorkspace, pullWorkspace, getGitToken,
  readWorkspacePurpose, writeWorkspacePurpose,
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
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
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
  const isBorn = !shared && !meta?.repo;                       // vexa-born (no external origin) — publishable
  const myRole = memberships.find((m) => m.workspace_id === slug)?.role;

  // The shared workspace_id the participants section operates on: a shared row IS the id; an own workspace
  // gets one once it is shared (share-enable returns it). Null until then → the "Share this workspace" CTA.
  const [shareWsId, setShareWsId] = useState<string | null>(shared ? slug : null);
  useEffect(() => { setShareWsId(shared ? slug : null); }, [shared, slug]);

  return (
    <div style={{ height: "100%", overflowY: "auto", background: "var(--bg)" }}>
      <div style={{ maxWidth: 680, margin: "0 auto", padding: "22px 24px" }}>
        <Header
          slug={slug} shared={shared} isSeed={isSeed} displayName={displayName} mounted={mounted}
          busy={busy} onRun={run} reload={loadCore} layout={layout} tabId={id}
        />
        {err && <div role="alert" style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--live)", background: "var(--panel)", border: "1px solid var(--live)", borderRadius: 8, padding: "8px 11px" }}>⚠ {err}</div>}
        {note && <div role="status" style={{ margin: "0 0 12px", fontSize: 12.5, color: "var(--green)" }}>✓ {note}</div>}

        <PurposeSection slug={slug} />

        <GitHubSection
          slug={slug} status={status} published_url={isSeed ? (attached.published_url ?? null) : null}
          canPublish={isBorn && isSeed} defaultRepoName={defaultRepoName(displayName)}
          busy={busy} onRun={run} reload={loadCore}
        />

        <ParticipantsSection
          ownSlug={shared ? null : slug} shared={shared} shareWsId={shareWsId} myRole={shared ? myRole : undefined}
          setShareWsId={setShareWsId} busy={busy} onRun={run} reload={loadCore}
          layout={layout} tabId={id}
        />

        {!shared && <DangerZone slug={slug} isSeed={isSeed} displayName={displayName} archived={!!meta?.archived} busy={busy} onRun={run} reload={loadCore} layout={layout} tabId={id} />}
      </div>
    </div>
  );
}

const defaultRepoName = (name: string) =>
  (name || "vexa-workspace").toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "") || "vexa-workspace";

// ── header: rename + on/off + purpose one-liner ─────────────────────────────────────────────────
function Header({ slug, shared, isSeed, displayName, mounted, busy, onRun, reload, layout, tabId }: {
  slug: string; shared: boolean; isSeed: boolean; displayName: string; mounted: boolean; busy: boolean;
  onRun: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; reload: () => void; layout: LayoutService; tabId: string;
}) {
  const [renaming, setRenaming] = useState(false);
  const cancelled = useRef(false);
  const toggle = () => onRun(async () => {
    if (shared) await setSharedActive(slug, !mounted);
    else if (mounted) await deactivateWorkspace(slug); else await activateWorkspace({ slug });
    reload();
  });
  const doRename = (name: string) => onRun(async () => { await renameWorkspace(slug, name.trim()); setRenaming(false); layout.retargetTab(tabId, manageTabDescriptor(slug, { name: name.trim() || slug })); reload(); });
  return (
    <div style={{ marginBottom: 16 }}>
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
        {!shared && !renaming && (
          <span onClick={() => setRenaming(true)} title="Rename (display label)" style={{ cursor: "pointer", color: "var(--t3)", padding: 4 }}><Icon name="edit" size={15} /></span>
        )}
      </div>
      <div style={{ fontSize: 12.5, color: "var(--t3)", marginTop: 5, marginLeft: 26 }}>
        {shared ? "Shared workspace — you're a member." : isSeed ? "Your Personal workspace." : "Your workspace."}
        {" "}{mounted ? "Mounted into the agent this turn." : "Not mounted — switch on to include it in the agent's context."}
      </div>
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
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  return (
    <Section icon="info" title="Purpose"
      right={!editing && <span onClick={() => setEditing(true)} style={{ cursor: "pointer", color: "var(--t3)" }}><Icon name="edit" size={13} /></span>}>
      {err && <div role="alert" style={{ fontSize: 12, color: "var(--live)", marginBottom: 6 }}>⚠ {err}</div>}
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
function GitHubSection({ slug, status, published_url, canPublish, defaultRepoName, busy, onRun, reload }: {
  slug: string; status: GitRemoteStatus | null; published_url: string | null; canPublish: boolean;
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
  const doPublish = (f: { name: string; priv: boolean; token: string }) => onRun(async () => { await publishWorkspace(f.name.trim(), f.priv, f.token.trim() || undefined); setPub(null); reload(); }, "Published to GitHub.");
  // With a saved token, push/pull run straight away (no prompt); otherwise open the one-off token row.
  const onPush = () => { if (hasSaved) void doPush(); else { setPushTok({ open: true, token: "" }); setPullTok({ open: false, token: "" }); } };
  const onPull = () => { if (hasSaved) void doPull(); else { setPullTok({ open: true, token: "" }); setPushTok({ open: false, token: "" }); } };

  return (
    <Section icon="github" title="GitHub"
      right={status?.branch && <span style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "var(--t2)" }}>{status.branch}</span>}>
      {hasHome ? (<>
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
      </>) : canPublish ? (<>
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
      </>) : (
        <div style={{ fontSize: 12.5, color: "var(--t3)" }}>No GitHub home yet. Attach a repo (from the Workspaces list) or publish to sync this workspace.</div>
      )}
    </Section>
  );
}

function AheadBehind({ ahead, behind, tracked }: { ahead: number; behind: number; tracked: boolean }) {
  if (!tracked) return <span style={{ color: "var(--t3)" }}>not yet fetched</span>;
  if (!ahead && !behind) return <span style={{ color: "var(--green)" }}>up to date</span>;
  return (
    <span style={{ display: "inline-flex", gap: 8, fontFamily: "var(--mono)" }}>
      {ahead > 0 && <span style={{ color: "var(--accent)" }} title={`${ahead} local commit(s) to push`}>↑{ahead}</span>}
      {behind > 0 && <span style={{ color: "var(--live)" }} title={`${behind} remote commit(s) to pull`}>↓{behind}</span>}
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
          <button disabled={busy} style={{ ...btn(), marginLeft: "auto", color: "var(--live)" }}
            onClick={() => onRun(async () => { await leaveWorkspace(shareWsId); layout.closeTab(tabId); }, "Left the workspace.")}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><Icon name="logout" size={13} />Leave</span></button>
        )}
        {isOwner && shared && (
          <button disabled={busy} style={{ ...btn(), color: "var(--live)" }}
            onClick={() => { if (window.confirm("Stop sharing? All members lose access and it becomes your private workspace.")) onRun(async () => { await unshareWorkspace(shareWsId); layout.closeTab(tabId); }, "Unshared."); }}>Unshare</button>
        )}
      </div>

      {invite && <InviteDialog s={invite} setS={setInvite} onMint={doMint} busy={busy} />}
    </Section>
  );
}

function InviteDialog({ s, setS, onMint, busy }: {
  s: { mode: "link" | "email"; role: string; ttlDays: number; emails: string; link: string | null };
  setS: (s: any) => void; onMint: (s: any) => void; busy: boolean;
}) {
  return (
    <div style={{ marginTop: 12, padding: "12px", background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 8, display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ fontSize: 11, color: "var(--t3)", textTransform: "uppercase", letterSpacing: ".04em" }}>{s.mode === "email" ? "Add by email" : "Invite link"}</div>
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

// ── danger zone (own workspaces): archive / delete ────────────────────────────────────────────────
function DangerZone({ slug, isSeed, displayName, archived, busy, onRun, reload, layout, tabId }: {
  slug: string; isSeed: boolean; displayName: string; archived: boolean; busy: boolean;
  onRun: (fn: () => Promise<unknown>, ok?: string) => Promise<void>; reload: () => void; layout: LayoutService; tabId: string;
}) {
  if (isSeed) return null;  // the seed slot (Personal) can't be archived/deleted (backend refuses)
  return (
    <Section icon="alert" title="Danger zone">
      <div style={{ display: "flex", gap: 8 }}>
        <button disabled={busy} onClick={() => onRun(async () => { await archiveWorkspace(slug, !archived); reload(); }, archived ? "Un-archived." : "Archived.")} style={btn()}>{archived ? "Un-archive" : "Archive"}</button>
        <button disabled={busy} style={{ ...btn(), color: "var(--live)", borderColor: "var(--live)" }}
          onClick={() => { if (window.confirm(`Delete "${displayName}"? This permanently removes the workspace and all its data.`)) onRun(async () => { await deleteWorkspace(slug); layout.closeTab(tabId); }); }}>Delete</button>
      </div>
    </Section>
  );
}

// Agent surface — absent in meetings-only mode.
if (!meetingsOnly()) {
  registerTab("workspace", WorkspaceManagePanel);
}
